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
		* E.g. one ability chills enemies, hitting chilled enemies with another ability freezes them
		* Probably easier to build into palettes and damage types
		* Still need to theoretically support, though.
	* Possibly use what I'm going to call "augments" - after generating simple core graphs, add slightly
	  complicating behavior that DOES NOT CHANGE the graph
		* This would also be a good way to add stuff like delays and damage modifiers
		* Also a good way to add cross-ability interaction - e.g. Condition x EntityId -> stronger condition output
"""


import random
from collections import namedtuple, Counter, defaultdict

import logging
import sys
import itertools

import networkx as nx
from networkx.drawing.nx_pydot import write_dot
import os
from Queue import Queue

"""

"""
class TypedValue:
	def __init__(self, typ, description):
		self.type = typ
		self.description = description
		self.source = None # will be set in Node constructor
		self.destination = None # will be set, uh, eventually
	def __repr__(self):
		return 'TypedValue(type={0}, value={1})'.format(self.type, self.description)

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

logger = logging.getLogger("foo")
ch = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(levelname)s - %(module)s.py:%(lineno)d - %(message)s')
#formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

class Node:
	INTYPES  = None # [type]
	OUTTYPES = None # [type]
	FORMATSTRINGS = None # [String]

	def __init__(self, *args):
		assert(all(isinstance(arg, TypedValue)) for arg in args)
		if len(args) != len(self.INTYPES):
			logger.warning("Node constructor %s expected %d args of type %s, got %d: %s", self.__class__.__name__, len(self.INTYPES), [i.__name__ for i in self.INTYPES], len(args), str([arg.type.__name__ for arg in args]))
		for tv, t in zip(args, self.INTYPES):
			assert(tv.type == t)
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

universals = [
	ConstantFloat,
	OwningEntity,
	InKey
]

# INPUTS

class InputClick(Node):
	INTYPES = [InputKey]
	OUTTYPES = [Position]
	FORMATSTRINGS = ["where the user clicked"]

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

input_nodetypes = [
	InputClick,
	InputPerpendicularLine,
	InputClickDragReleaseDirection,
	InputClickCharge,
	InputPlaceMines,
	InputToggle,
	InputUnitTargetEnemy
]

# converter_nodetypes

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

"""
class DamageLifesteal(Node):
	INTYPES = [Damage]
	OUTTYPES = [Damage]
	FORMATSTRINGS = ["{0} with lifesteal"]
"""

converter_nodetypes = [
	PositionToArea,
	TimeBoolToRandomDirection,
	PositionFromEntity,
	EntitiesInArea,
	DirectionToProjectile,
	#DelayArea,
	#Transform,
	CloudFollowingPath,
	PathToArea,
	#DamageLifesteal,
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

game_effects = [
	AddDamageOnEntity,
	ConditionOnEntity,
	TeleportPlayer,
	Wall,
	TerminateDamage
]

nodetypes = input_nodetypes + converter_nodetypes + game_effects

# bad code
# bad bad bad code
# will error if there's any Node subclass that isn't in `nodetypes` or `universals`
for name in dir():
	obj = eval(name)
	try:
		if name != "Node" and issubclass(obj, Node) and obj not in nodetypes and obj not in universals:
			raise ValueError("expected to see {name} in nodetypes".format(name=name))
	except TypeError:
		pass

def findValidNodeTypes(start_types=universals, end_type=GameEffect, predicate=lambda types: len(types)<4):
	"""Generator function that performs a bidirectional BFS, searching forward from InputType and backward from GameEffect.
	A given vertex in the search has two components
		* A set of "unused types" - corresponding to missing sinks if searching forward, sources if backward
		* The order in which we added edges - a prefix if searching forward, a suffix if searching backward
	"""

	def powerset(iterable):
		"powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
		s = list(iterable)
		return itertools.chain.from_iterable(itertools.combinations(s, r) for r in range(len(s)+1))

	forwardq  = Queue()
	prefixcache = defaultdict(list) # [Type] -> [[NodeType]]
	for subset in powerset(start_types):
		a = [typ for n in subset for typ in n.OUTTYPES]
		forwardq.put((frozenset(a), tuple(subset)))
		prefixcache[tuple(a)].append(subset)
	backwardq = Queue()
	backwardq.put((frozenset([end_type]), ()))
	suffixcache  = defaultdict(list)
	suffixcache[frozenset([end_type])].append(())
	print suffixcache
	# bias the search to prefer certain nodes
	#random.shuffle(nodetypes)
	def process_forwardq():
		available_types, nodetypes_prefix = forwardq.get(block=False)
		if available_types in suffixcache:
			for nodetypes_suffix in suffixcache[available_types]:
				print nodetypes_prefix, nodetypes_suffix
				yield nodetypes_prefix + nodetypes_suffix

		def canAddNodeType(nodetype):
			required_types = Counter(nodetype.INTYPES)
			return not required_types - Counter(available_types)

		for nodetype in nodetypes:
			if canAddNodeType(nodetype):
				new_args = (available_types - frozenset(nodetype.INTYPES)) | frozenset(nodetype.OUTTYPES)
				new_nodetypes_prefix = nodetypes_prefix + (nodetype,)
				"""
				print "forward"
				print new_args
				print new_nodetypes_prefix
				print
				"""
				if predicate(new_args):
					forwardq.put((new_args, new_nodetypes_prefix))
					prefixcache[new_args].append(new_nodetypes_prefix)

	def process_backwardq():
		# returns _ if 
		target_types, nodetypes_suffix = backwardq.get(block=False)
		if target_types in prefixcache:
			for nodetypes_prefix in prefixcache[target_types]:
				yield nodetypes_prefix + nodetypes_suffix
		def canAddNodeType(nodetype):
			required_types = Counter(target_types)
			available_types = Counter(nodetype.OUTTYPES)
			return not required_types - available_types	

		for nodetype in nodetypes:
			if canAddNodeType(nodetype):
				new_args = (target_types - frozenset(nodetype.OUTTYPES)) | frozenset(nodetype.INTYPES)
				new_nodetypes_suffix = (nodetype,) + nodetypes_suffix
				"""
				print "back"
				print new_args
				print new_nodetypes_suffix
				print
				"""
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

class PowerGraph:
	def __init__(self, nodes):
		self.nodes =  nodes

	@staticmethod
	def FromListOfNodeTypes(nodetypes):
		nodes = set() # [Node]
		unused_vars = set() # [TypedVar]
		used_vars = set()

		def addNodeType(nodetype):
			args = []
			for t in nodetype.INTYPES:
				arg = random.choice([v for v in unused_vars if v.type == t])
				unused_vars.remove(arg)
				used_vars.add(arg)
				args.append(arg)
			node = nodetype(*args)
			nodes.add(node)
			for outvar in node.out:
				unused_vars.add(outvar)

		for nodetype in nodetypes:
			addNodeType(nodetype)
		return PowerGraph(nodes)

	def description(self):
		descriptions = []
		for node in self.nodes:
			for arg in node.out:
				if arg.type == GameEffect:
					descriptions.append(arg.description)
		return ". ".join(descriptions)

	def render(self):
		pass

	def renderToFile(self, filename):
		count = 0
		G=nx.MultiDiGraph()
		labelFromNode = {}
		for node in self.nodes:
			name = node.__class__.__name__ + str(count)
			count += 1
			labelFromNode[node] = name
			G.add_node(name)

		for destination_node in self.nodes:
			for var in destination_node.args:
				if destination_node is not var.source:
					G.add_edge(labelFromNode[var.source], labelFromNode[destination_node], xlabel=var.type.__name__)

		logger.info("Writing to %s", filename)
		write_dot(G,'multi.dot')

		os.system("""C:/"Program Files (x86)"/Graphviz2.38/bin/dot.exe -Nshape=box -T png multi.dot > {0}""".format(filename))
		os.remove("multi.dot")

def createUniquePowers(n, predicate=lambda pg: True):
	sigs = set()
	tries = 100 * n
	while len(sigs) < n and tries:
		tries -= 1
		powerGraph = attemptCreatePowerGraph()
		if powerGraph and predicate(powerGraph):
			types = tuple(sorted(node.__class__ for node in powerGraph.nodes))
			if types not in sigs:
				sigs.add(types)
				yield powerGraph
			else:
				logger.debug("Generated a non-unique power, retrying...")

if __name__ == "__main__":
	logger.setLevel(logging.INFO)


	generator = findValidNodeTypes()
	i = 0
	for nodetypeslist in generator:
		PowerGraph.FromListOfNodeTypes(nodetypeslist).renderToFile("out/power{0}.png".format(i))
		i += 1
		print nodetypeslist

	def mustContainNode(nodetype):
		return lambda graph: any(isinstance(node, nodetype) for node in graph.nodes)

	#for i, power in enumerate(createUniquePowers(10)):
	#	power.renderToFile("out/power{0}.png".format(i))
