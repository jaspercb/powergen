"""
Idea: Randomly generate superpowers flexibly and powerfully. Combine building blocks to do neat stuff.

Example powers that I want to be generatable:
	* You can press a button
		* to turn into a dragon, and while you're a dragon you can...
			* click to shoot fireballs
	* You can hold a button
		* to charge a laser
			* which does fire damage

	OOP APPROACH:
		"Power" object takes mouse movements + key presses, maintains internal state, delegates relevant type signatures to sub-process
		Pro: Easy to write
		Con: Hard to create variations on abilities like (mines) x (explosions) x (more powerful when detonated immediately/after more time/whatever)

	NEAT-O FUNCTIONAL APPROACH
		Pro: Highly expressive, not too much extra work to render to text as a proof-of-concept
		Con: Hard to write

		Rough outline:
			DataUnit : (Type, Description)
			Vector : List[DataUnit]
			Converter : Vector -> Vector
				Permute : Converter
				Drop : Converter
			Applicator: (ApplicatorState, Input) -> (ApplicatorState, Vector)

			Consumer: (Vector => EffectOnWorld)
			ApplicatorConsumer: Input => EffectOnWorld : Applicator o Converter* o Consumer

		More implementation questions:
			* How complex should types be? Higher-order, like (Time => Position)?
				* Time => Position is useful for, for example, preplotting out the path of projectiles
				* Having an AOE centered on a projectile as it flies (this is closer to a .getCurrentPosition())

Currently implementing neat-o functional approach.

TODO:
* Clarify where in the abstraction projectiles live (in Consumers, surely)
* Adapters
    * Type coercions (e.g. getting position, HP fraction, from an EntityId)
    * Projectiles emitting an EntityId if they hit
"""

from collections import namedtuple
import random

DataUnit = namedtuple("DataUnit", "type description")

Position = namedtuple("Position", "x y")
Direction = namedtuple("Direction", "dx dy")
EnemyEntityId = namedtuple("EnemyEntityId", "val")
FriendlyEntityId = namedtuple("FriendlyEntityId", "val")

Applicator = namedtuple("Applicator", "name emissions")
Applicator.signature = lambda c: [d.type for d in c.emissions]

"""
An applicator is, roughly speaking, an interface for a power.
It defines how the user interacts with a power - by clicking, by pointing and pressing a button, by toggling.
I haven't decided whether it controls some of the "feel" of a power - eg. "spray and pray" projectiles vs single-target bullet.
It does not control the type of damage done, any status effects, that sort of thing. That's in a Consumer
"""
applicators = [
	Applicator("TouchEnemyEntity",
		[DataUnit(EnemyEntityId, "touched entity")]
	),
	Applicator("Spray",
		[DataUnit(EnemyEntityId, "enemies hit by your spray of projectiles")]
	),
	Applicator("Click",
		[DataUnit(FriendlyEntityId, "you"), DataUnit(Position, "wherever you click")]
	),
	Applicator("ClickAndDrag",
		[DataUnit(Position, "mouse down position"), DataUnit(Position, "mouse up position"), DataUnit(float, "time mouse held down for")]
	),
	Applicator("Bullet",
		[DataUnit(Position, "where your bullets impact"), DataUnit(float, "projectile flight duration")]
	),
	Applicator("BulletHumanOnly",
		[DataUnit(EnemyEntityId, "who your bullets hit"), DataUnit(float, "projectile flight duration")]
	),
	Applicator("SelfToggle",
		[DataUnit(FriendlyEntityId, "you")]
	),
	Applicator("Mines",
		[DataUnit(Position, "where your mines detonate"), DataUnit(float, "time since mine was placed")]
	),
]

Consumer = namedtuple("Consumer", "name consumptions formatString")
Consumer.signature = lambda c: c.consumptions
Consumer.prettyprint = lambda c, args: c.formatString.format(*[a.description for a in args])

#	Consumer("Explosion", [Position, float], "Explosion at ({0}) with intensity proportional to ({1})")

class Element:
	def __init__(self, name, consumers=None, passives=None):
		# Effects: [Consumers]
		# Passives: [String]
		self.name = name
		self.consumers = consumers or []
		self.passives = passives or []

		assert(all(isinstance(consumer, Consumer) for consumer in self.consumers))
		assert(all(isinstance(passive, str) for passive in self.passives))

elements = [
	Element("Spark",
		consumers=[
			Consumer("SparkDirection", [EnemyEntityId], "Superheated motes that inflict burning on ({0})")
		],
		passives=["Immunity to fire and heat"]
	),
	Element("Static",
		consumers=[
			Consumer("StaticTarget", [EnemyEntityId], "Bright arcs of electricity shock and damage ({0})"),
			Consumer("StaticArea", [Position], "Bright arcs of electricity shock and damage the area around ({0})")
		],
		passives=["Immunity to shocks"]
	),
	Element("Chill",
		consumers=[
			Consumer("ChillTarget", [EnemyEntityId], "Frigid motes condense around ({0}), chilling them"),
			Consumer("ChillArmorSelf", [FriendlyEntityId], "Frigid motes condense around ({0}), forming a layer of icy armor")],
		passives=["Immunity to chill and cold"]
	),
	Element("Blade",
		consumers=[
			Consumer("BladeTarget", [Position], "Blades fly through the air towards ({0}), cleaving through terrain and causing bleeding")
		]
	),
	Element("Swap",
		consumers=[
			Consumer("SwapWithTarget", [EnemyEntityId, EnemyEntityId], "({0}) swaps positions with ({1})"),
			Consumer("SwapWithTarget", [FriendlyEntityId, EnemyEntityId], "({0}) swaps positions with ({1})"),
			Consumer("SwapWithTarget", [FriendlyEntityId, FriendlyEntityId], "({0}) swaps positions with ({1})"),
			Consumer("SwapWithTarget", [Position, Position, float], "Swaps objects near ({0}) with objects near ({1}) within radius that scales with ({2})"),
		]
	)
]

def applicator_matches_consumer(applicator, consumer):
	return all(any(e.type is consumptiontype for e in applicator.emissions) for consumptiontype in consumer.consumptions)

def isConvertible(inTypes, outTypes):
	return set(outTypes).issubset(set(inTypes))

def createConverter(inTypes, outTypes):
	"""Takes two lists of types.
	Returns a function (list => list) that converts an list with the first element into a list of the second type.
	Subsets only
	Examples:
		(a, a, a) and (a, a) returns (1, 2, 3) => (1, 2) or (2, 1) or (1, 3) or (3, 1) or (2, 3) or (3, 2)
		(a, b, c, d) and (a, b, c), returns a function that takes a four-element list and returns a three-element list with the last element dropped
		(a, b) and (a, b, c) ValueErrors"""
	if not isConvertible(inTypes, outTypes):
		raise ValueError("Could not create converter: no function from {0} to {1}".format(inTypes, outTypes))

	# O(n^2), but that's OK
	input_indices = []
	for typ in outTypes:
		possible_indices = [i for i, t in enumerate(inTypes) if t == typ and i not in input_indices]
		if not possible_indices: raise ValueError
		input_indices.append(random.choice(possible_indices))

	def f(l):
		assert len(l) == len(inTypes)
		return [l[i] for i in input_indices]
	return f



def GeneratePower():
	while True:
		try:
			applicator = random.choice(applicators)
			element = random.choice(elements)
			consumer = random.choice(element.consumers)
			return applicator.name, consumer.prettyprint(createConverter(applicator.signature(), consumer.signature())(applicator.emissions))
		except ValueError:
			pass

def generateAllPowers():
	powers = []
	for applicator in applicators:
		for element in elements:
			for consumer in element.consumers:
				try:
					powers.append((applicator.name, consumer.prettyprint(createConverter(applicator.signature(), consumer.signature())(applicator.emissions))))
				except ValueError:
					pass
	return powers

# for _ in range(10):
# 	print GeneratePower()

allPowers = generateAllPowers()

for power in allPowers:
	print power
print
print "Generated", len(allPowers), "powers"
# print createConverter([int, str, str, str], [str, str, str, int])([1, "hi", "bla", "oh my god"])
