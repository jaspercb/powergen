"""
DONE
    * Synthesize DAGs that represent abilities from a set of components
    * Graph generation is pretty well-optimized
    * We canonically hash power graphs to ensure uniqueness
TODO:
    * Investigate whether graph generation would be sped up by maintaining backtrack pointers (space efficiency?)
    * More payoffs
        * Displaces (pull, push)
        * %life damage
        * More status effects
    * Generate consistent sets of abilities
        * Elemental palettes
    * Restrictions on output graphs
        * Per node restrictions like
            * Unique in entire graph
            * Unique in any path
        (right now we force single-uniqueness)
    * Cross-ability interaction
        * E.g. hitting chills enemies, hitting chilled enemies freezes them
        * Probably easier to build into palettes and damage types
        * Still need to theoretically support, though.
    * Possibly use what I'm currently calling "augments" - after generating simple
      core graphs, add slightly complicating behavior that DOES NOT CHANGE the graph
        * This would also be a good way to add stuff like delays and damage modifiers
        * Also a good way to add cross-ability interaction
            * e.g. Condition x EntityId -> stronger condition output
        * Damage modifiers
            * Lifesteal

    * When generating ability node types, some orderings can be more "general" than others
        * E.g. Input -> Float,  () -> Float, Float -> Intermediate, Intermediate * Float -> Final
            * If we add () -> Float after Float -> Intermediate there are ungenerateable combinations
            * To fix, we add a restriction to "can we add this node"
                * If a previous node has consumed an X, we can't add a node that produces an X
                  unless it could plausibly depend somehow on that previous node
"""


import os
import logging
import sys
import itertools
import xxhash
from collections import namedtuple, defaultdict
from Queue import Queue
import networkx as nx
from networkx.drawing.nx_pydot import write_dot
from multiset import FrozenMultiset


# Config vars
MAX_ENDTYPES = 2
OUTPUT_IMAGES = False

# UTILITIES

def memoize(f):
    c = {}

    def g(x):
        if x not in c:
            c[x] = f(x)
        return c[x]
    return g


def powerset(iterable):
    "powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
    s = list(iterable)
    return itertools.chain.from_iterable(
        itertools.combinations(
            s, r) for r in range(
            len(s) + 1))


class TypedValue(object):
    def __init__(self, typ, description):
        self.type = typ
        self.description = description
        self.source = None  # will be set in Node constructor
        # self.destination = None  # will be set, uh, eventually?

    def __repr__(self):
        return 'TypedValue(type={0}, value={1})'.format(
            self.type, self.description)


InputKey = namedtuple("InputKey", "null")
Position = namedtuple("Position", "x y")
SimplePath = namedtuple("SimplePath", "points")
Direction = namedtuple("Direction", "dx dy")
EntityId = namedtuple("EntityId", "null")
EnemyEntityId = namedtuple("EnemyEntityId", "null")
Damage = namedtuple("Damage", "quant")
GameEffect = namedtuple("GameEffect", "null")
Area = type("Area", (), {})
Bool = type("Bool", (), {})

LOGGER = logging.getLogger("foo")
CHANNEL = logging.StreamHandler(sys.stdout)
FORMATTER = logging.Formatter(
    '%(levelname)s - %(module)s.py:%(lineno)d - %(message)s')
CHANNEL.setFormatter(FORMATTER)
LOGGER.addHandler(CHANNEL)


class Node(object):
    INTYPES = None  # [type]
    OUTTYPES = None  # [type]
    FORMATSTRINGS = None  # [String]

    def __init__(self, *args):
        assert(all(isinstance(arg, TypedValue)) for arg in args)
        if len(args) != len(self.INTYPES):
            LOGGER.warning("Node constructor %s expected %d args of type %s, got %d: %s", self.__class__.__name__, len(
                self.INTYPES), [i.__name__ for i in self.INTYPES], len(args), str([arg.type.__name__ for arg in args]))
        for typedvalue, typ in zip(args, self.INTYPES):
            assert(typedvalue.type == typ)
        self.args = args
        self.out = tuple(TypedValue(t, "uninitialized") for t in self.OUTTYPES)
        for out in self.out:
            out.source = self

    def bake(self):
        argdescriptions = [arg.description for arg in self.args]
        for out, formatstring in zip(self.out, self.FORMATSTRINGS):
            out.description = formatstring.format(*argdescriptions)

    def values(self):
        return [var.description for var in self.out]


def CreateNodeType(
        nodename,
        intypes,
        outtypes,
        formatstrings,
        optionalintypes=[]):
    for i, opttypesubset in enumerate(powerset(optionalintypes)):
        actualnodename = nodename + str(i)
        typ = type(actualnodename,
                   (Node,
                    ),
                   {"INTYPES": tuple(intypes) + opttypesubset,
                    "OUTTYPES": tuple(outtypes),
                    "FORMATSTRINGS": formatstrings})
        yield typ


UNIVERSALS = itertools.chain(
    CreateNodeType(
        "ConstantFloat",
        intypes=[],
        outtypes=[float],
        formatstrings=["$CONSTANT"]),
    CreateNodeType(
        "OwningEntity",
        intypes=[],
        outtypes=[EntityId],
        formatstrings=["the user's character"]),
    CreateNodeType(
        "InKey",
        intypes=[],
        outtypes=[InputKey],
        formatstrings=[""]),
)

# INPUTS


ALL_NODETYPES = list(itertools.chain(
    CreateNodeType("InputClickPosition", intypes=[InputKey], outtypes=[
                   Position], formatstrings=["where the user clicked"]),
    CreateNodeType(
        "InputClickDirection",
        intypes=[InputKey],
        outtypes=[Direction],
        formatstrings=["the direction of the user's click"]),
    CreateNodeType(
        "InputPerpendicularLine",
        intypes=[InputKey],
        outtypes=[
            SimplePath],
        formatstrings=["a line perpendicular to the player"]),
    CreateNodeType("InputClickDragReleaseDirection", intypes=[InputKey], outtypes=[Position, Direction], formatstrings=[
        "where the user clicked",
        "where the mouse moved before releasing"]),
    CreateNodeType("InputClickCharge", intypes=[InputKey], outtypes=[Position, float], formatstrings=[
        "where the user clicked and held",
        "proportional to how long the user held the mouse for"]),
    CreateNodeType("InputPlaceMines", intypes=[InputKey], outtypes=[Position, float], formatstrings=[
        "where the mines were placed",
        "proportional to how long the mines charged before detonation"]),
    CreateNodeType("InputUnitTargetEnemy", intypes=[InputKey], outtypes=[EnemyEntityId], formatstrings=[
        "the clicked enemy",
    ]),
    CreateNodeType(
        "InputUnitTargetEnemy",
        intypes=[InputKey],
        outtypes=[Bool],
        formatstrings=["a toggle is held"]),
    # Converters
    CreateNodeType("PositionToArea", intypes=[Position], optionalintypes=[float], outtypes=[
                   Area], formatstrings=["a circle centered on {0} with radius {1}"]),
    CreateNodeType("TimeBoolToRandomDirection", intypes=[Bool], outtypes=[
                   Direction], formatstrings=["random directions when {0}"]),
    CreateNodeType(
        "PositionFromEntity",
        intypes=[EntityId],
        outtypes=[Position],
        formatstrings=["the position of {0}"]),
    CreateNodeType(
        "EntitiesInArea",
        intypes=[Area],
        outtypes=[EnemyEntityId],
        formatstrings=["enemy entities in {0}"]),
    CreateNodeType(
        "DirectionToProjectile",
        intypes=[Direction],
        outtypes=[EnemyEntityId],
        formatstrings=["enemies hit by projectiles emitted towards {0}"]),
    CreateNodeType("CloudFollowingPath", intypes=[SimplePath], outtypes=[
                   Area], formatstrings=["a cloud that moves along {0}"]),
    CreateNodeType("PathToArea", intypes=[SimplePath], outtypes=[
                   Area], formatstrings=["a static cloud covering {0}"]),
    CreateNodeType(
        "PositionDirectionFloatToArea",
        intypes=[
            Position,
            Direction,
            float],
        outtypes=[Area],
        formatstrings=["a rectangle starting at {0}, moving towards {1}, of length {2}"]),
    # GameEffects
    CreateNodeType("AddDamageOnEntity", intypes=[EnemyEntityId], optionalintypes=[float], outtypes=[
                   Damage], formatstrings=["Deal damage scaling with {1} to {0}"]),
    CreateNodeType("ConditionOnEntity", intypes=[EnemyEntityId], optionalintypes=[float], outtypes=[
                   GameEffect], formatstrings=["Inflict a condition on {0} with intensity {1}"]),
    CreateNodeType("TeleportPlayer", intypes=[EntityId, Position], outtypes=[
                   GameEffect], formatstrings=["Teleports {0} to {1}"]),
    CreateNodeType(
        "Wall",
        intypes=[SimplePath],
        outtypes=[GameEffect],
        formatstrings=["A wall following {0}"]),
    CreateNodeType(
        "TerminateDamage",
        intypes=[Damage],
        outtypes=[GameEffect],
        formatstrings=["{0}"]),
))


"""
class DelayArea(Node):
    INTYPES = [Area]
    OUTTYPES = [Area]
    FORMATSTRINGS = ["delayed {0}"]

class Transform(Node):
    INTYPES = [Bool]
    OUTTYPES = [Area, InputKey]
    FORMATSTRINGS = ["transform into a {0}", "idk"]
"""

"""
class DamageLifesteal(Node):
    INTYPES = [Damage]
    OUTTYPES = [Damage]
    FORMATSTRINGS = ["{0} with lifesteal"]
"""

# GAME EFFECTS

# bad code
# bad bad bad code
# will error if there's any Node subclass that isn't in `nodetypes` or
# `universals`
for objname in dir():
    obj = eval(objname)
    try:
        if objname != "Node" and issubclass(
                obj, Node) and obj not in ALL_NODETYPES and obj not in UNIVERSALS:
            raise ValueError(
                "expected to see {objname} in ALL_NODETYPES".format(
                    objname=objname))
    except TypeError:
        pass


def generate_valid_topsorted_nodetype_dags(
        start_types=UNIVERSALS,
        end_type=GameEffect,
        predicate=lambda types: len(types) < 3):
    """Generator function that performs a bidirectional BFS, searching forward from InputType and backward from GameEffect.
    A given vertex in the search has two components
            * A set of "unused types" - corresponding to missing sinks if searching forward, sources if backward
            * The order in which we added edges - a prefix if searching forward, a suffix if searching backward

    TODO: optimization: since many nodes have the same type signature, we can generate templated outputs,
          then replace templates with particular nodetypes with a matching type signature
    """

    previously_output = set()
    forwardq = Queue()
    prefixcache = defaultdict(list)  # [Type] -> [[NodeType]]
    for subset in powerset(start_types):
        # flatten available types
        typeset = FrozenMultiset([typ for n in subset for typ in n.OUTTYPES])
        forwardq.put((typeset, tuple(subset)))
        prefixcache[typeset].append(subset)
    backwardq = Queue()

    suffixcache = defaultdict(list)
    for i in range(1, MAX_ENDTYPES + 1):
        typeset = FrozenMultiset([end_type] * i)
        backwardq.put((typeset, ()))
        suffixcache[typeset].append(())
    # bias the search to prefer certain nodes
    # random.shuffle(nodetypes)

    def process_forwardq():
        available_types, nodetypes_prefix = forwardq.get(block=False)
        if available_types in suffixcache:
            for nodetypes_suffix in suffixcache[available_types]:
                entire_nodetypes = tuple(nodetypes_prefix + nodetypes_suffix)
                if entire_nodetypes not in previously_output:
                    previously_output.add(entire_nodetypes)
                    yield entire_nodetypes

        def can_add_nodetype(nodetype):
            required_types = FrozenMultiset(nodetype.INTYPES)
            return required_types.issubset(available_types)

        for nodetype in ALL_NODETYPES:
            if can_add_nodetype(nodetype):
                new_args = (available_types - FrozenMultiset(nodetype.INTYPES)
                            ) + FrozenMultiset(nodetype.OUTTYPES)
                new_nodetypes_prefix = nodetypes_prefix + (nodetype,)
                if predicate(new_args):
                    forwardq.put((new_args, new_nodetypes_prefix))
                    prefixcache[new_args].append(new_nodetypes_prefix)

    def process_backwardq():
        target_types, nodetypes_suffix = backwardq.get(block=False)
        if target_types in prefixcache:
            for nodetypes_prefix in prefixcache[target_types]:
                entire_nodetypes = tuple(nodetypes_prefix + nodetypes_suffix)
                if entire_nodetypes not in previously_output:
                    previously_output.add(entire_nodetypes)
                    yield entire_nodetypes

        def can_add_nodetype(nodetype):
            output_types = FrozenMultiset(nodetype.OUTTYPES)
            return output_types.issubset(target_types)

        for nodetype in ALL_NODETYPES:
            if can_add_nodetype(nodetype):
                new_args = (target_types - FrozenMultiset(nodetype.OUTTYPES)
                            ) + FrozenMultiset(nodetype.INTYPES)
                new_nodetypes_suffix = (nodetype,) + nodetypes_suffix
                if predicate(new_args):
                    backwardq.put((new_args, new_nodetypes_suffix))
                    suffixcache[new_args].append(new_nodetypes_suffix)
    while forwardq.qsize() or backwardq.qsize():
        if forwardq.qsize():
            for out in process_forwardq():
                yield out
        if backwardq.qsize():
            for out in process_backwardq():
                yield out


class PowerGraph(object):
    def __init__(self, nodes):
        self.nodes = nodes

    def __hash__(self):
        """
        Returns a deterministic 64-bit hex value. Different PowerGraph objects
        representing the same structure give the same hash
        """

        def canonicalNodeOrder(nodelist):
            return sorted(nodelist, key=lambda node: node.__class__.__name__)
        @memoize
        def hash_arg(var):
            i = var.source.out.index(var)
            return xxhash.xxh64(str(i) +
                                str(hash_node(var.source))).intdigest()

        @memoize
        def hash_node(node):
            xxh = xxhash.xxh64(node.__class__.__name__)
            for arg in node.args:
                xxh.update(str(hash_arg(arg)))
            return xxh.intdigest()

        xxh = xxhash.xxh64()
        for node in canonicalNodeOrder(self.nodes):
            xxh.update(str(hash_node(node)))
        return xxh.intdigest()

    """
    Generate all PowerGraph objects from a list of nodetypes using different argument ordering choices

    Since a given topsorted list of nodetypes does not uniquely specify a
    power if at any point there are two available variables of a given type,
    this function just generates all of them, Every single combination.
    For example, if TypeA is a node from () => (float, float) and TypeB is a node from (float, float) => ()
    this function will yield both possible result graphs
    """
    @classmethod
    def from_list_of_node_types(cls, nodetypes):
        def flatmap(f, l):
            return [j for i in l for j in f(i)]

        # nodes, unused vars
        state = [(frozenset(), frozenset())]

        for nodetype in nodetypes:
            def add_nodetype(state, captured_nodetype=nodetype):
                (nodes, unused_vars) = state
                consumed_argsets = [((), unused_vars)]
                for intype in captured_nodetype.INTYPES:
                    def select_one_arg(state1, captured_intype=intype):
                        (prev_used_vars, inner_unused_vars) = state1
                        for var in inner_unused_vars:
                            if var.type == captured_intype:
                                yield (prev_used_vars + (var,), inner_unused_vars - frozenset([var]))

                    consumed_argsets = flatmap(
                        select_one_arg, consumed_argsets)
                for (used_vars, inner_unused_vars) in consumed_argsets:
                    node = captured_nodetype(*used_vars)
                    yield (nodes | frozenset([node]), (inner_unused_vars | frozenset(node.out)))

            state = flatmap(add_nodetype, state)

        return (cls(nodes) for (nodes, _) in state)

    def description(self):
        descriptions = []
        for node in self.nodes:
            for arg in node.out:
                if arg.type == GameEffect:
                    descriptions.append(arg.description)
        return ". ".join(descriptions)

    def render_to_file(self, filename):
        count = 0
        digraph = nx.MultiDiGraph()
        label_from_node = {}
        for node in self.nodes:
            name = node.__class__.__name__ + str(count)
            count += 1
            label_from_node[node] = name
            digraph.add_node(name)

        for destination_node in self.nodes:
            for var in destination_node.args:
                if destination_node is not var.source:
                    digraph.add_edge(label_from_node[var.source],
                                     label_from_node[destination_node],
                                     xlabel=var.type.__name__)

        LOGGER.info("Writing to %s", filename)
        write_dot(digraph, 'multi.dot')

        os.system(
            """C:/"Program Files (x86)"/Graphviz2.38/bin/dot.exe -Nshape=box -T png multi.dot > {0}""".format(filename))
        os.remove("multi.dot")


def main():
    LOGGER.setLevel(logging.INFO)

    generator = generate_valid_topsorted_nodetype_dags()
    seen_graph_hashes = set()
    n_successful_generated = 0
    n_attempted_generated = 0
    for nodetypeslist in generator:
        for pg in PowerGraph.from_list_of_node_types(nodetypeslist):
            h = hash(pg)
            if h not in seen_graph_hashes:
                seen_graph_hashes.add(h)
                if OUTPUT_IMAGES:
                    pg.render_to_file(
                        "out/power{0}.png".format(n_successful_generated))
                n_successful_generated += 1
            n_attempted_generated += 1
    print """Generated {attempted} power graphs, {successful} unique
Uniqueness rate: {ratio:.3%}""".format(attempted=n_attempted_generated, successful=n_successful_generated, ratio=n_successful_generated / float(n_attempted_generated))

    def must_contain_nodetype(nodetype):
        return lambda graph: any(isinstance(node, nodetype)
                                 for node in graph.nodes)


if __name__ == "__main__":
    main()
