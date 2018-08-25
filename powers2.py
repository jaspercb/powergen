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
formatter = logging.Formatter('%(levelname)s - %(message)s')
#formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

class Node:
	INTYPES  = [] # [type]
	OUTTYPES = [] # [type]

	def __init__(self, *args):
		for tv, t in zip(args, self.INTYPES):
			assert(tv.type == t)
		self.args = args
		self.out = tuple(TypedValue(t, "uninitialized") for t in self.OUTTYPES)

	def bake(self):
		pass

	def values(self):
		return [var.value for var in self.out]


# INPUTS

class InputClick(Node):
	INTYPES = []
	OUTTYPES = [Position]
	def bake(self):
		self.out[0].value = "where the user clicked"

class InputClickDragCircle(Node):
	INTYPES = []
	OUTTYPES = [Area]
	def bake(self):
		self.out[0].value = "a click-and-drag circle"

class InputClickDragRelease(Node):
	INTYPES = []
	OUTTYPES = [PositionTimeFunc]
	def bake(self):
		self.out[0].value = "the path traced by the user between press and release"

class InputClickDragReleaseDirection(Node):
	INTYPES = []
	OUTTYPES = [Position, Direction]
	def bake(self):
		self.out[0].value = "where the user clicked"
		self.out[1].value = "where the mouse moved before releasing"

class InputClickCharge(Node):
	INTYPES = []
	OUTTYPES = [Position, float]
	def bake(self):
		self.out[0].value = "where the user clicked and held"
		self.out[1].value = "proportional to how long the user held the mouse for"

class InputPlaceMines(Node):
	INTYPES = []
	OUTTYPES = [Position, float]
	def bake(self):
		self.out[0].value = "where the mines were placed"
		self.out[1].value = "proportional to how long the mines charged before detonation"

class InputToggle(Node):
	INTYPES = []
	OUTTYPES = [Time_BoolFunc]
	def bake(self):
		self.out[0].value = "a toggle is held"

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
	def bake(self):
		self.out[0].value = "random directions when {0}".format(self.args[0].value)

class OwningEntity(Node):
	INTYPES = []
	OUTTYPES = [EntityId]
	def bake(self):
		self.out[0].value = "the user's character"

class PositionFromEntity(Node):
	INTYPES = [EntityId]
	OUTTYPES = [Position]
	def bake(self):
		self.out[0].value = "the position of {0}".format(self.args[0].value)

class EvaluatePositionTimeFunc(Node):
	INTYPES = [PositionTimeFunc]
	OUTTYPES = [Position]
	def bake(self):
		self.out[0].value = "tracing the path of {0}".format(self.args[0].value)

class ConstantFloat(Node):
	INTYPES = []
	OUTTYPES = [float]
	def bake(self):
		self.out[0].value = "$CONSTANT"

class EntitiesInArea(Node):
	INTYPES = [Area]
	OUTTYPES = [EnemyEntityId]
	def bake(self):
		self.out[0].value = "entites in {0}".format(self.args[0].value)

class DirectionToProjectile(Node):
	INTYPES = [Direction]
	OUTTYPES = [EnemyEntityId]
	def bake(self):
		self.out[0].value = "enemies hit by projectiles emitted towards {0}".format(self.args[0].value)


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
	def bake(self):
		self.out[0].value = "an explosion happens, centered on {0} with radius {1}".format(self.args[0].value, self.args[1].value)

class CloudFollowingPath(Node):
	INTYPES = [PositionTimeFunc]
	OUTTYPES = [Area]
	def bake(self):
		self.out[0].value = "a cloud following the path of {0}".format(self.args[0].value)

class DamageToEntity(Node):
	INTYPES = [EnemyEntityId, float]
	OUTTYPES = [GameEffect]
	def bake(self):
		self.out[0].value = "Deal damage to {0} that scales with {1}".format(self.args[0].value, self.args[1].value)

class ConditionOnEntity(Node):
	INTYPES = [EnemyEntityId, float]
	OUTTYPES = [GameEffect]
	def bake(self):
		self.out[0].value = "Inflict a condition on {0} with intensity {1}".format(self.args[0].value, self.args[1].value)


game_effects = [
	ExplosionAtPoint,
	CloudFollowingPath,
	DamageToEntity,
	ConditionOnEntity
]

nodetypes = input_nodetypes + converters + game_effects

# Node sanity tests
inp = InputClick()
exp = ExplosionAtPoint(inp.out[0])

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
			return False
			# Don't add inputs that give us types we already have
			new_types = Counter(nodetype.OUTTYPES)
			available_types = Counter(i.type for i in unused_vars)
			return not (available_types - new_types) and not (new_types - available_types)
		return True

	def addNodeType(node):
		logger.debug("adding %s", str(node))
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

	random.shuffle(nodetypes)
	#step 1: create a graph that, eventually, terminates at a node
	while not nodes or not any(i.type == GameEffect for i in unused_vars):
		found = False
		logger.debug("Current state: %s", str(nodes))
		for nodetype in nodetypes:
			a = canAddNodeType(nodetype)
			b = shouldAddNodeType(nodetype)
			logger.debug("considering adding %s     can I? %d    should I? %d", str(nodetype), a, b)
			if a and b:
				addNodeType(nodetype)
				found = True
				break
		if not found:
			return None

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
			print destination_node
			for var in node.args:
				print var
				source_node = self.var_to_source_node[var]
				G.add_edge(labelFromNode[source_node], labelFromNode[destination_node], xlabel=var.type.__name__)
		#G.add_edge(1, 2, xlabel="bla")
		#G.add_edge(1, 2)
		write_dot(G,'multi.dot')

		os.system("""C:/"Program Files (x86)"/Graphviz2.38/bin/dot.exe -T png multi.dot > multi.png""")

def createUniquePowers(n):
	sigs = set()
	tries = 10 * n
	while len(sigs) < n and tries:
		tries -= 1
		powerGraph = attemptCreatePowerGraph()
		if powerGraph:
			types = tuple(sorted(node.__class__ for node in powerGraph.nodes))
			if types not in sigs:
				sigs.add(types)
				yield powerGraph
				#print [typ.__name__ for typ in types]


logger.setLevel(logging.INFO)

for power in createUniquePowers(100):
	power.render()
#for i in range(10):
#	createPower()
