import random
from collections import namedtuple, Counter

import logging
import sys

import networkx as nx
from networkx.drawing.nx_pydot import write_dot
import os

# TypedValue = recordclass("TypedValue", "type value") # for now, a value is just a string descriptor
class TypedValue:
	def __init__(self, typ, value):
		self.type = typ
		self.value = value
	def __repr__(self):
		return 'TypedValue(type={0}, value={1})'.format(self.type, self.value)

Position = namedtuple("Position", "x y")
Direction = namedtuple("Direction", "dx dy")
EntityId = namedtuple("EntityId", "null")
EnemyEntityId = namedtuple("EnemyEntityId", "null")
GameEffect = namedtuple("GameEffect", "null")
PositionTimeFunc = type("PositionTimeFunc", (), {})
Area = type("Area", (), {})
Time_BoolFunc = type("Time_BoolFunc", (), {})

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
		if len(args) != len(self.INTYPES):
			logger.warning("Node constructor %s expected %d args of type %s, got %d: %s", self.__class__.__name__, len(self.INTYPES), [i.__name__ for i in self.INTYPES], len(args), str([arg.type.__name__ for arg in args]))
		for tv, t in zip(args, self.INTYPES):
			assert(tv.type == t)
		self.args = args
		self.out = tuple(TypedValue(t, "uninitialized") for t in self.OUTTYPES)

	def bake(self):
		argdescriptions = [arg.value for arg in self.args]
		for out, formatstring in zip(self.out, self.FORMATSTRINGS):
			out.value = formatstring.format(*argdescriptions)

	def values(self):
		return [var.value for var in self.out]


# INPUTS

class InputClick(Node):
	INTYPES = []
	OUTTYPES = [Position]
	FORMATSTRINGS = ["where the user clicked"]

class InputClickDragCircle(Node):
	INTYPES = []
	OUTTYPES = [Area]
	FORMATSTRINGS = ["a click-and-drag circle"]

class InputClickDragRelease(Node):
	INTYPES = []
	OUTTYPES = [PositionTimeFunc]
	FORMATSTRINGS = ["the path traced by the user between press and release"]

class InputClickDragReleaseDirection(Node):
	INTYPES = []
	OUTTYPES = [Position, Direction]
	FORMATSTRINGS = [
		"where the user clicked",
		"where the mouse moved before releasing"
	]

class InputClickCharge(Node):
	INTYPES = []
	OUTTYPES = [Position, float]
	FORMATSTRINGS = [
		"where the user clicked and held",
		"proportional to how long the user held the mouse for"
	]

class InputPlaceMines(Node):
	INTYPES = []
	OUTTYPES = [Position, float]
	FORMATSTRINGS = [
		"where the mines were placed",
		"proportional to how long the mines charged before detonation"
	]

class InputToggle(Node):
	INTYPES = []
	OUTTYPES = [Time_BoolFunc]
	FORMATSTRINGS = ["a toggle is held"]

input_nodetypes = [
	InputClick,
	InputClickDragCircle,
	InputClickDragRelease,
	InputClickDragReleaseDirection,
	InputClickCharge,
	InputPlaceMines,
	InputToggle
]

# CONVERTERS

class TimeBoolToRandomDirection(Node):
	INTYPES = [Time_BoolFunc]
	OUTTYPES = [Direction]
	FORMATSTRINGS = ["random directions when {0}"]

class OwningEntity(Node):
	INTYPES = []
	OUTTYPES = [EntityId]
	FORMATSTRINGS = ["the user's character"]

class PositionFromEntity(Node):
	INTYPES = [EntityId]
	OUTTYPES = [Position]
	FORMATSTRINGS = ["the position of {0}"]

class EvaluatePositionTimeFunc(Node):
	INTYPES = [PositionTimeFunc]
	OUTTYPES = [Position]
	FORMATSTRINGS = ["tracing the path of {0}"]

class ConstantFloat(Node):
	INTYPES = []
	OUTTYPES = [float]
	FORMATSTRINGS = ["$CONSTANT"]

class EntitiesInArea(Node):
	INTYPES = [Area]
	OUTTYPES = [EnemyEntityId]
	FORMATSTRINGS = ["entities in {0}"]

class DirectionToProjectile(Node):
	INTYPES = [Direction]
	OUTTYPES = [EnemyEntityId]
	FORMATSTRINGS = ["enemies hit by projectiles emitted towards {0}"]


converters = [
	TimeBoolToRandomDirection,
	OwningEntity,
	PositionFromEntity,
	EvaluatePositionTimeFunc,
	ConstantFloat,
	EntitiesInArea,
	DirectionToProjectile
]


# GAME EFFECTS

class ExplosionAtPoint(Node):
	INTYPES = [Position, float]
	OUTTYPES = [GameEffect]
	FORMATSTRINGS = ["an explosion happens, centered on {0} with radius {1}"]

class CloudFollowingPath(Node):
	INTYPES = [PositionTimeFunc]
	OUTTYPES = [Area]
	FORMATSTRINGS = ["a cloud following the path of {0}"]

class DamageToEntity(Node):
	INTYPES = [EnemyEntityId, float]
	OUTTYPES = [GameEffect]
	FORMATSTRINGS = ["Deal damage to {0} that scales with {1}"]

class ConditionOnEntity(Node):
	INTYPES = [EnemyEntityId, float]
	OUTTYPES = [GameEffect]
	FORMATSTRINGS = ["Inflict a condition on {0} with intensity {1}"]

game_effects = [
	ExplosionAtPoint,
	CloudFollowingPath,
	DamageToEntity,
	ConditionOnEntity
]

nodetypes = input_nodetypes + converters + game_effects

# Node sanity tests
inp = InputClick()
exp = ExplosionAtPoint(inp.out[0], TypedValue(float, "something"))

# matcher
def attemptCreatePowerGraph():
	nodes = set() # [Node]
	unused_vars = set() # [TypedVar]
	used_vars = set()
	var_to_source_node = {}

	def canAddNodeType(nodetype):
		required_types = Counter(nodetype.INTYPES)
		available_types = Counter(i.type for i in unused_vars)
		return not required_types - available_types	

	def shouldAddNodeType(nodetype):
		# Don't add a node if it only gives us variable types we already have
		# This feels hacky but whatever.

		# If we don't have any nodes, sure, we better add something
		if not nodes:
			return True

		# If we already have a node of this type, maybe don't add another one
		if nodetype in [node.__class__ for node in nodes]:
			return False

		if 0 == len(nodetype.INTYPES):
			# Don't add inputs that give us types we already have
			new_types = Counter(nodetype.OUTTYPES)
			available_types = Counter(i.type for i in unused_vars)
			return not (available_types - new_types) and not (new_types - available_types)
		return True

	def addNodeType(nodetype):
		logger.debug("adding %s", str(nodetype))
		args = []
		for t in nodetype.INTYPES:
			arg = random.choice([v for v in unused_vars if v.type == t])
			unused_vars.remove(arg)
			used_vars.add(arg)
			args.append(arg)
		node = nodetype(*args)
		nodes.add(node)
		for outvar in node.out:
			var_to_source_node[outvar] = node
			unused_vars.add(outvar)

	#step 1: create a graph that, eventually, terminates at a node
	while not nodes or not any(i.type == GameEffect for i in unused_vars):
		logger.debug("Current state: %s", str(nodes))
		options = [nt for nt in nodetypes if canAddNodeType(nt) and shouldAddNodeType(nt)]
		if not options:
			logger.error("We couldn't add anything, that's really weird")
			return None
		addNodeType(random.choice(options))

	# strip out unused nodes
	queue = [var_to_source_node[var] for var in unused_vars if var.type == GameEffect]
	used_nodes = []
	while queue:
		node, queue = queue[0], queue[1:]
		if node not in used_nodes:
			used_nodes.append(node)
			for var in node.args:
				queue.append(var_to_source_node[var])

	# now that we have all used nodes, let's check that every output from every source is used
	used_vars = set()
	for node in nodes:
		for var in node.args:
			used_vars.add(var)
	source_nodes = [node for node in used_nodes if not node.args]

	for source in source_nodes:
		for out in source.out:
			if out not in used_vars:
				logger.debug("Nope, didn't use all outputs in %s", str(nodes))
				return None


	for _ in range(len(used_nodes)):
		for n in used_nodes:
			n.bake()

	#print [node.__class__.__name__ for node in used_nodes[::-1]]
	#print "Unused", unused_vars
	#print "Used", used_vars
	return PowerGraph(used_nodes[::-1], var_to_source_node)

class PowerGraph:
	def __init__(self, nodes, var_to_source_node):
		self.nodes =  nodes
		self.var_to_source_node = var_to_source_node

	def description(self):
		descriptions = []
		for node in self.nodes:
			for arg in node.out:
				if arg.type == GameEffect:
					descriptions.append(arg.value)
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

		print self.var_to_source_node
		for destination_node in self.nodes:
			for var in node.args:
				source_node = self.var_to_source_node[var]
				if destination_node is not source_node:
					G.add_edge(labelFromNode[source_node], labelFromNode[destination_node], xlabel=var.type.__name__)

		write_dot(G,'multi.dot')

		os.system("""C:/"Program Files (x86)"/Graphviz2.38/bin/dot.exe -T png multi.dot > {0}""".format(filename))
		os.remove("multi.dot")

def createUniquePowers(n):
	sigs = set()
	tries = 100 * n
	while len(sigs) < n and tries:
		tries -= 1
		powerGraph = attemptCreatePowerGraph()
		if powerGraph:
			types = tuple(sorted(node.__class__ for node in powerGraph.nodes))
			if types not in sigs:
				sigs.add(types)
				yield powerGraph

logger.setLevel(logging.DEBUG)

for i, power in enumerate(createUniquePowers(4)):
	power.renderToFile("out/power{0}.png".format(i))
