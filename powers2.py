"""
DONE
    * Synthesize DAGs that represent abilities from a set of components
    * Graph generation is pretty well-optimized
    * We canonically hash power graphs to ensure uniqueness
TODO:
    * More sources
        * All enemies
    * More combinators
        * Path boomerang (augment?)
        * Direction to path
        * Seeking projectile (augment?)
    * More payoffs
        * Displaces (pull, push)
        * %life damage (augment?)
        * More status effects
        * Visibility
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
        * Still need to theoretically support, though
    * Complexity metric?
    * Possibly use what I'm currently calling "augments" - after generating simple
      core graphs, add slightly complicating behavior that DOES NOT CHANGE the graph
        * This would also be a good way to add stuff like delays and damage modifiers
        * Also a good way to add cross-ability interaction
            * e.g. Condition x EntityId -> stronger condition output
        * Damage modifiers
            * Lifesteal
            * Increased-damage-if-previously-hit
            * True damage
"""


import os
import logging
import sys
import itertools
import xxhash
import random
from collections import namedtuple, defaultdict
from Queue import Queue
import networkx as nx
from networkx.drawing.nx_pydot import write_dot
from multiset import FrozenMultiset


# Config vars
OUTPUT_IMAGES = True
MAX_GAME_EFFECTS_PER_POWER = 3
MAX_INTERMEDIATE_UNBOUND_VARS = 4
N_POWERS_TO_GENERATE = 20

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

PossiblyRepeatedInputKey = namedtuple("PossiblyRepeatedInputKey", "null")
InputKey = namedtuple("InputKey", "null")
Position = namedtuple("Position", "x y")
SimplePath = namedtuple("SimplePath", "points")
Direction = namedtuple("Direction", "dx dy")
EntityId = namedtuple("EntityId", "null")
EnemyEntityId = namedtuple("EnemyEntityId", "null")
Projectile = namedtuple("Projectile", "null")
Damage = namedtuple("Damage", "quant")
Condition = namedtuple("Condition", "null")
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


def create_node_type(
        nodename,
        intypes,
        outtypes,
        formatstrings,
        optionalintypes=[]):
    for i, opttypesubset in enumerate(powerset(optionalintypes)):
        actualnodename = nodename + (str(i) if optionalintypes else "")
        typ = type(actualnodename,
                   (Node,
                    ),
                   {"INTYPES": tuple(intypes) + opttypesubset,
                    "OUTTYPES": tuple(outtypes),
                    "FORMATSTRINGS": formatstrings,
                    })
        globals()[actualnodename] = typ
        yield typ

# TODO: remove all no-sources, replace with something that ruins the DFS less
UNIVERSALS = itertools.chain(
    create_node_type(
        "OwningEntity",
        intypes=[],
        outtypes=[EntityId],
        formatstrings=["the user's character"]),
)

InKey = list(create_node_type(
        "InKey",
        intypes=[],
        outtypes=[PossiblyRepeatedInputKey],
        formatstrings=[""]))[0]

# INPUTS


ALL_NODETYPES = list(itertools.chain(
    create_node_type(
        "RepeatInputKey",
        intypes=[PossiblyRepeatedInputKey],
        outtypes=[InputKey, InputKey],
        formatstrings=[""]),
    create_node_type(
        "SingleInputKey",
        intypes=[PossiblyRepeatedInputKey],
        outtypes=[InputKey],
        formatstrings=[""]),
    create_node_type(
        "InputClickPosition",
        intypes=[InputKey],
        outtypes=[Position],
        formatstrings=[""]),
    create_node_type(
        "InputClickDirection",
        intypes=[InputKey],
        outtypes=[Direction],
        formatstrings=["the direction of the user's click"]),
    create_node_type(
        "InputPerpendicularLine",
        intypes=[InputKey],
        outtypes=[
            SimplePath],
        formatstrings=["a line perpendicular to the player"]),
    create_node_type(
        "InputClickDragReleaseDirection",
        intypes=[InputKey],
        outtypes=[Position, Direction],
        formatstrings=[
            "where the user clicked",
            "where the mouse moved before releasing"]),
    create_node_type(
        "InputUnitTargetEnemy",
        intypes=[InputKey],
        outtypes=[EnemyEntityId],
        formatstrings=["the clicked enemy",
    ]),
    create_node_type(
        "InputToggle",
        intypes=[PossiblyRepeatedInputKey], # don't want a one-two punch that uses a toggle
        outtypes=[Bool],
        formatstrings=["a toggle is held"]),
    # Converters
# Currently disabled because it forms an infinite loop
    #create_node_type(
    #    "HomingProjectileEntity",
    #    intypes=[EnemyEntityId],
    #    outtypes=[Projectile],
    #    formatstrings=["a projectile that homes towards {0}"]),
    create_node_type(
        "DumbProjectile",
        intypes=[SimplePath],
        outtypes=[Projectile],
        formatstrings=["a projectile that travels along {0}"]),
    create_node_type(
        "ProjectilePassthrough",
        intypes=[Projectile],
        outtypes=[EnemyEntityId],
        formatstrings=["all enemies hit by {0}"]),
    create_node_type(
        "ProjectileCollideFirst",
        intypes=[Projectile],
        outtypes=[EnemyEntityId],
        formatstrings=["the first enemy hit by {0}"]),
    create_node_type(
        "ProjectileCollideFirstTwo",
        intypes=[Projectile],
        outtypes=[EnemyEntityId],
        formatstrings=["the first two enemies hit by {0}"]),
    create_node_type(
        "CircleAroundPoint",
        intypes=[Position],
        optionalintypes=[float],
        outtypes=[Area],
        formatstrings=["a circle centered on {0} with radius {1}"]),
    create_node_type(
        "EmitRandomDirections",
        intypes=[Bool],
        outtypes=[Direction],
        formatstrings=["random directions when {0}"]),
    create_node_type(
        "PositionFromEntity",
        intypes=[EntityId],
        outtypes=[Position],
        formatstrings=["the position of {0}"]),
    create_node_type(
        "EntitiesInArea",
        intypes=[Area],
        outtypes=[EnemyEntityId],
        formatstrings=["enemy entities in {0}"]),
    create_node_type(
        "DirectionToSimplePath",
        intypes=[Position, Direction],
        outtypes=[SimplePath],
        formatstrings=["towards {0}"]),
    create_node_type(
        "PathToProjectile",
        intypes=[SimplePath],
        outtypes=[EnemyEntityId],
        formatstrings=["enemies hit by projectiles emitted {0}"]),
    create_node_type(
        "CloudFollowingPath",
        intypes=[SimplePath],
        outtypes=[Area],
        formatstrings=["a cloud that moves along {0}"]),
    create_node_type(
        "PulseAlongPath",
        intypes=[SimplePath],
        outtypes=[Position],
        formatstrings=["points along {0}"]),
    create_node_type(
        "PathToArea",
        intypes=[SimplePath],
        outtypes=[Area],
        formatstrings=["a static cloud covering {0}"]),
    create_node_type(
        "Accumulator",
        intypes=[Position, InKey],
        outtypes=[Position],
        formatstrings=["Stores a sequence of positions for simultaneous firing {0}"]),
    # GameEffects
    create_node_type(
        "AddDamageOnEntity",
        intypes=[EnemyEntityId],
        optionalintypes=[float],
        outtypes=[Damage],
        formatstrings=["Deal damage scaling with {1} to {0}"]),
    create_node_type(
        "ConditionOnEntity",
        intypes=[EnemyEntityId],
        optionalintypes=[float],
        outtypes=[Condition],
        formatstrings=["Inflict a condition on {0} with intensity {1}"]),
    create_node_type(
        "TeleportPlayer",
        intypes=[EntityId, Position],
        outtypes=[GameEffect],
        formatstrings=["Teleports {0} to {1}"]),
    create_node_type(
        "Wall",
        intypes=[SimplePath],
        outtypes=[GameEffect],
        formatstrings=["A wall following {0}"]),
    create_node_type(
        "TerminateDamage",
        intypes=[Damage],
        outtypes=[GameEffect],
        formatstrings=["{0}"]),
    create_node_type(
        "TerminateCondition",
        intypes=[Condition],
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


class PowerGraph(object):
    def __init__(self, nodes):
        self.nodes = nodes

    def __hash__(self):
        """
        Returns a deterministic 64-bit hex value. Different PowerGraph objects
        representing the same structure give the same hash
        """

        def canonical_node_order(nodelist):
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
        for node in canonical_node_order(self.nodes):
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
            return [random.choice([j for i in l for j in f(i)])]

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

    @classmethod
    def all_from_list_of_node_types(cls, nodetypes):
        def flatmap(func, seq):
            return [j for i in seq for j in func(i)]

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
            """dot -Nshape=box -T png multi.dot > {0}""".format(filename))
        os.remove("multi.dot")


class PowerGraphGenerator(object):
    def __init__(self):
        pass

    def generate_valid_topsorted_node_dag(
            self,
            start_type=PossiblyRepeatedInputKey,
            end_type=GameEffect,
            predicate=lambda types: len(types) <= MAX_INTERMEDIATE_UNBOUND_VARS):
        goalstates = set()
        for n in range(MAX_GAME_EFFECTS_PER_POWER):
            goalstates.add(FrozenMultiset([end_type] * n))

        @memoize
        def dfs(available_types):
            """Returns a list of """
            def can_add_nodetype(nodetype):
                required_types = FrozenMultiset(nodetype.INTYPES)
                return required_types.issubset(available_types)

            possible_nodetypes = filter(can_add_nodetype, ALL_NODETYPES)
            random.shuffle(possible_nodetypes)
            for nodetype in possible_nodetypes:
                new_available_types = (available_types - FrozenMultiset(nodetype.INTYPES)
                                       ) + FrozenMultiset(nodetype.OUTTYPES)
                if new_available_types in goalstates:
                    return [nodetype]
                elif predicate(new_available_types):
                    suffix = dfs(new_available_types)
                    if suffix:
                        return [nodetype] + suffix

        return dfs(FrozenMultiset([start_type]))

    def generate_unique(self, n_unique=20, predicate=lambda pg: True):
        seen_graph_hashes = set()
        n_output = 0
        while n_output < n_unique:
            nodetypeslist = [InKey] + self.generate_valid_topsorted_node_dag()
            for powergraph in PowerGraph.from_list_of_node_types(nodetypeslist):
                if predicate(powergraph):
                    graphhash = hash(powergraph)
                    if graphhash not in seen_graph_hashes:
                        seen_graph_hashes.add(graphhash)
                        yield powergraph
                        n_output += 1


def render_all_nodetypes(filename):
    digraph = nx.MultiDiGraph()
    counter = defaultdict(int)

    def typenodename(typ):
        counter[typ] += 1
        return typ.__name__ + str(counter[typ])

    for nodetype in ALL_NODETYPES:
        name = nodetype.__name__
        digraph.add_node(name)

        for intype in nodetype.INTYPES:
            typename = typenodename(intype)
            digraph.add_node(typename, label=intype.__name__)
            digraph.add_edge(typename, name)
        for outtype in nodetype.OUTTYPES:
            typename = typenodename(outtype)
            digraph.add_node(typename, label=outtype.__name__)
            digraph.add_edge(name, typename)

    LOGGER.info("Writing to %s", filename)
    write_dot(digraph, 'multi.dot')

    os.system(
        """dot -Nshape=box -T png multi.dot > {0}""".format(filename))
    os.remove("multi.dot")


def main():
    def pg_contains_node(nodetype):
        def f(pg):
            return any(node.__class__ == nodetype for node in pg.nodes)
        return f

    LOGGER.setLevel(logging.INFO)
    generator = PowerGraphGenerator()
    n_successful_generated = 0
    render_all_nodetypes("out/all_nodetypes.png")
    for powergraph in generator.generate_unique(N_POWERS_TO_GENERATE):
        if OUTPUT_IMAGES:
            powergraph.render_to_file(
                "out/power{0}.png".format(n_successful_generated))
            n_successful_generated += 1


if __name__ == "__main__":
    main()
