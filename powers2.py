"""
DONE
    * Synthesize DAGs that represent abilities from a set of components
    * Optimize graph generation to be less "generate a bunch, ignore the bad ones"
    * Damage modifiers
        * Lifesteal
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
    * Cross-ability interaction
        * E.g. hitting chills enemies, hitting chilled enemies freezes them
        * Probably easier to build into palettes and damage types
        * Still need to theoretically support, though.
    * Possibly use what I'm going to call "augments" - after generating simple
      core graphs, add slightly complicating behavior that DOES NOT CHANGE the graph
        * This would also be a good way to add stuff like delays and damage modifiers
        * Also a good way to add cross-ability interaction
            * e.g. Condition x EntityId -> stronger condition output
"""


import os
import random
import logging
import sys
import itertools
from collections import namedtuple, defaultdict
from Queue import Queue
import networkx as nx
from networkx.drawing.nx_pydot import write_dot
from multiset import FrozenMultiset


class TypedValue(object):
    def __init__(self, typ, description):
        self.type = typ
        self.description = description
        self.source = None  # will be set in Node constructor
        self.destination = None  # will be set, uh, eventually #TODO

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


# UNIVERSALS (things you always have access to)

class ConstantFloat(Node):
    INTYPES = []
    OUTTYPES = [float]
    FORMATSTRINGS = ["$CONSTANT"]


class OwningEntity(Node):
    INTYPES = []
    OUTTYPES = [EntityId]
    FORMATSTRINGS = ["the user's character"]


class InKey(Node):
    INTYPES = []
    OUTTYPES = [InputKey]
    FORMATSTRINGS = [""]


UNIVERSALS = [
    ConstantFloat,
    ConstantFloat,
    ConstantFloat,
    OwningEntity,
    InKey
]

# INPUTS


class InputClickPosition(Node):
    INTYPES = [InputKey]
    OUTTYPES = [Position]
    FORMATSTRINGS = ["where the user clicked"]


class InputClickDirection(Node):
    INTYPES = [InputKey]
    OUTTYPES = [Direction]
    FORMATSTRINGS = ["the direction of the user's click"]


class InputPerpendicularLine(Node):
    INTYPES = [InputKey]
    OUTTYPES = [SimplePath]
    FORMATSTRINGS = ["a line perpendicular to the player"]


class InputClickDragReleaseDirection(Node):
    INTYPES = [InputKey]
    OUTTYPES = [Position, Direction]
    FORMATSTRINGS = [
        "where the user clicked",
        "where the mouse moved before releasing"
    ]


class InputClickCharge(Node):
    INTYPES = [InputKey]
    OUTTYPES = [Position, float]
    FORMATSTRINGS = [
        "where the user clicked and held",
        "proportional to how long the user held the mouse for"
    ]


class InputPlaceMines(Node):
    INTYPES = [InputKey]
    OUTTYPES = [Position, float]
    FORMATSTRINGS = [
        "where the mines were placed",
        "proportional to how long the mines charged before detonation"
    ]


class InputUnitTargetEnemy(Node):
    INTYPES = [InputKey]
    OUTTYPES = [EnemyEntityId]
    FORMATSTRINGS = [
        "the clicked enemy",
    ]


class InputToggle(Node):
    INTYPES = [InputKey]
    OUTTYPES = [Bool]
    FORMATSTRINGS = ["a toggle is held"]


INPUT_NODETYPES = [
    InputClickPosition,
    InputClickDirection,
    InputPerpendicularLine,
    InputClickDragReleaseDirection,
    InputClickCharge,
    InputPlaceMines,
    InputToggle,
    InputUnitTargetEnemy
]

# CONVERTER_NODETYPES


class PositionToArea(Node):
    INTYPES = [Position, float]
    OUTTYPES = [Area]
    FORMATSTRINGS = ["a circle centered on {0} with radius {1}"]


class TimeBoolToRandomDirection(Node):
    INTYPES = [Bool]
    OUTTYPES = [Direction]
    FORMATSTRINGS = ["random directions when {0}"]


class PositionFromEntity(Node):
    INTYPES = [EntityId]
    OUTTYPES = [Position]
    FORMATSTRINGS = ["the Position of {0}"]


class EntitiesInArea(Node):
    INTYPES = [Area]
    OUTTYPES = [EnemyEntityId]
    FORMATSTRINGS = ["entities in {0}"]


class DirectionToProjectile(Node):
    INTYPES = [Direction]
    OUTTYPES = [EnemyEntityId]
    FORMATSTRINGS = ["enemies hit by projectiles emitted towards {0}"]


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


class CloudFollowingPath(Node):
    INTYPES = [SimplePath]
    OUTTYPES = [Area]
    FORMATSTRINGS = ["a cloud that moves along {0}"]


class PathToArea(Node):
    INTYPES = [SimplePath]
    OUTTYPES = [Area]
    FORMATSTRINGS = ["a static cloud covering {0}"]


class PositionDirectionFloatToArea(Node):
    INTYPES = [Position, Direction, float]
    OUTTYPES = [Area]
    FORMATSTRINGS = [
        "a rectangle starting at {0}, moving towards {1}, of length {2}"]


"""
class DamageLifesteal(Node):
    INTYPES = [Damage]
    OUTTYPES = [Damage]
    FORMATSTRINGS = ["{0} with lifesteal"]
"""

CONVERTER_NODETYPES = [
    PositionToArea,
    TimeBoolToRandomDirection,
    PositionFromEntity,
    EntitiesInArea,
    DirectionToProjectile,
    # DelayArea,
    # Transform,
    CloudFollowingPath,
    PathToArea,
    PositionDirectionFloatToArea,
    # DamageLifesteal,
]


# GAME EFFECTS

class AddDamageOnEntity(Node):
    INTYPES = [EnemyEntityId, float]
    OUTTYPES = [Damage]
    FORMATSTRINGS = ["Deal damage scaling with {1} to {0}"]


class ConditionOnEntity(Node):
    INTYPES = [EnemyEntityId, float]
    OUTTYPES = [GameEffect]
    FORMATSTRINGS = ["Inflict a condition on {0} with intensity {1}"]


class TeleportPlayer(Node):
    INTYPES = [EntityId, Position]
    OUTTYPES = [GameEffect]
    FORMATSTRINGS = ["Teleport {0} to {1}"]


class Wall(Node):
    INTYPES = [SimplePath]
    OUTTYPES = [GameEffect]
    FORMATSTRINGS = ["A wall following {0}"]


class TerminateDamage(Node):
    INTYPES = [Damage]
    OUTTYPES = [GameEffect]
    FORMATSTRINGS = ["{0}"]


GAME_EFFECTS = [
    AddDamageOnEntity,
    ConditionOnEntity,
    TeleportPlayer,
    Wall,
    TerminateDamage
]

NODETYPES = INPUT_NODETYPES + CONVERTER_NODETYPES + GAME_EFFECTS

# bad code
# bad bad bad code
# will error if there's any Node subclass that isn't in `nodetypes` or
# `universals`
for objname in dir():
    obj = eval(objname)
    try:
        if objname != "Node" and issubclass(
                obj, Node) and obj not in NODETYPES and obj not in UNIVERSALS:
            raise ValueError(
                "expected to see {objname} in NODETYPES".format(
                    objname=objname))
    except TypeError:
        pass


def generate_valid_topsorted_nodetype_dags(
        start_types=UNIVERSALS,
        end_type=GameEffect,
        predicate=lambda types: len(types) < 4):
    """Generator function that performs a bidirectional BFS, searching forward from InputType and backward from GameEffect.
    A given vertex in the search has two components
            * A set of "unused types" - corresponding to missing sinks if searching forward, sources if backward
            * The order in which we added edges - a prefix if searching forward, a suffix if searching backward

    TODO: optimization: since many nodes have the same type signature, we can generate templated outputs,
          then replace templates with particular nodetypes with a matching type signature
    """

    def powerset(iterable):
        "powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
        s = list(iterable)
        return itertools.chain.from_iterable(
            itertools.combinations(
                s, r) for r in range(
                len(s) + 1))

    previously_output = set()
    forwardq = Queue()
    prefixcache = defaultdict(list)  # [Type] -> [[NodeType]]
    for subset in powerset(start_types):
        a = [typ for n in subset for typ in n.OUTTYPES]
        forwardq.put((FrozenMultiset(a), tuple(subset)))
        prefixcache[FrozenMultiset(a)].append(subset)
    backwardq = Queue()
    backwardq.put((FrozenMultiset([end_type]), ()))
    suffixcache = defaultdict(list)
    suffixcache[FrozenMultiset([end_type])].append(())
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

        for nodetype in NODETYPES:
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

        for nodetype in NODETYPES:
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

    @classmethod
    def from_list_of_node_types(cls, nodetypes):
        print nodetypes
        nodes = set()  # [Node]
        unused_vars = set()  # [TypedVar]
        used_vars = set()

        def add_nodetype(nodetype):
            args = []
            for intype in nodetype.INTYPES:
                arg = random.choice(
                    [v for v in unused_vars if v.type == intype])
                unused_vars.remove(arg)
                used_vars.add(arg)
                args.append(arg)
            node = nodetype(*args)
            nodes.add(node)
            for outvar in node.out:
                unused_vars.add(outvar)

        for nodetype in nodetypes:
            add_nodetype(nodetype)
        return cls(nodes)

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
    i = 0
    for nodetypeslist in generator:
        if InKey in nodetypeslist:
            PowerGraph.from_list_of_node_types(nodetypeslist).render_to_file(
                "out/power{0}.png".format(i))
            i += 1

    def must_contain_nodetype(nodetype):
        return lambda graph: any(isinstance(node, nodetype)
                                 for node in graph.nodes)


if __name__ == "__main__":
    main()
