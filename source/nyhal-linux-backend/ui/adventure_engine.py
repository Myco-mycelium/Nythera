from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class Direction(Enum):
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"
    UP = "up"
    DOWN = "down"


class ItemType(Enum):
    WEAPON = "weapon"
    KEY = "key"
    CONSUMABLE = "consumable"
    QUEST = "quest"
    TREASURE = "treasure"
    TOOL = "tool"
    ARMOR = "armor"
    MISC = "misc"


class NPCDisposition(Enum):
    FRIENDLY = "friendly"
    NEUTRAL = "neutral"
    HOSTILE = "hostile"
    QUEST_GIVER = "quest-giver"
    MERCHANT = "merchant"


class GameEvent(Enum):
    ENTER = "enter"
    EXAMINE = "examine"
    TAKE = "take"
    USE = "use"
    TALK = "talk"
    ATTACK = "attack"
    PICK_LOCK = "pick-lock"
    SOLVE = "solve"


@dataclass
class Item:
    name: str
    item_type: ItemType
    description: str
    use_text: str = ""
    damage: int = 0
    value: int = 0
    is_takeable: bool = True
    is_usable: bool = False
    key_id: str = ""
    effects: list = field(default_factory=list)


@dataclass
class NPC:
    name: str
    description: str
    disposition: NPCDisposition
    health: int = 100
    damage: int = 0
    dialogue: list = field(default_factory=list)
    quest_text: str = ""
    loot: list = field(default_factory=list)
    is_alive: bool = True

    @property
    def health_bar(self) -> str:
        filled = int(self.health / 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class Room:
    name: str
    description: str
    exits: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    npcs: list = field(default_factory=list)
    is_locked: bool = False
    key_required: str = ""
    visited: bool = False
    light_level: int = 100

    @property
    def exit_list(self) -> str:
        return ", ".join(self.exits.keys()) if self.exits else "none"


@dataclass
class PlayerState:
    health: int = 100
    max_health: int = 100
    inventory: list = field(default_factory=list)
    gold: int = 50
    experience: int = 0
    level: int = 1
    current_room: str = "entrance"
    quests_active: list = field(default_factory=list)
    quests_completed: list = field(default_factory=list)

    @property
    def health_bar(self) -> str:
        filled = int(self.health / self.max_health * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def attack_power(self) -> int:
        base = 5
        for item in self.inventory:
            if isinstance(item, Item) and item.item_type == ItemType.WEAPON:
                base += item.damage
        return base


@dataclass
class GameLog:
    timestamp: float
    event: GameEvent
    message: str
    room: str = ""


class AdventureEngine:
    def __init__(self):
        self._rooms: dict[str, Room] = {}
        self._player: PlayerState = PlayerState()
        self._log: list[GameLog] = []
        self._is_running: bool = False
        self._turn_count: int = 0
        self._time_played: float = 0
        self._start_time: float = 0
        self._view: str = "game"
        self._create_world()

    def _create_world(self):
        # Entrance Hall
        entrance = Room("Entrance Hall", "A grand hall with marble floors and towering pillars. Dust motes dance in shafts of light from stained glass windows above.", {"north": "library", "east": "armory", "west": "kitchen"}, [
            Item("Torch", ItemType.TOOL, "A burning torch that illuminates the darkness.", "The torch flickers, casting dancing shadows.", is_takeable=True),
        ], [], light_level=80)

        # Library
        library = Room("Ancient Library", "Shelves stretching to the ceiling hold thousands of ancient tomes. A faint magical glow emanates from a book on the central pedestal.", {"south": "entrance", "east": "lab", "north": "tower"}, [
            Item("Ancient Scroll", ItemType.QUEST, "A scroll containing the incantation to open the vault.", "The scroll glows with arcane energy."),
            Item("Health Potion", ItemType.CONSUMABLE, "A red potion that restores health.", "You feel refreshed!", effects=["heal:30"], value=25),
        ], [
            NPC("Librarian Ghost", "A translucent figure drifting between the shelves. Its eyes glow with spectral blue light.", NPCDisposition.FRIENDLY, dialogue=[
                "Welcome, seeker of knowledge. The vault holds secrets older than the mountains.",
                "Beware the dragon in the tower. Only the Sword of Light can defeat it.",
                "Take this scroll - it will guide you to the hidden passage.",
            ], quest_text="Find the Sword of Light in the Armory and defeat the dragon."),
        ])

        # Armory
        armory = Room("Armory", "Racks of ancient weapons line the walls. Most are rusted, but one sword gleams with an inner light.", {"west": "entrance"}, [
            Item("Sword of Light", ItemType.WEAPON, "A legendary blade that glows with holy radiance.", "The sword hums with power.", damage=25, is_takeable=True, value=500),
            Item("Shield", ItemType.ARMOR, "A sturdy iron shield.", "The shield bears the crest of an ancient order.", is_takeable=True),
            Item("Iron Key", ItemType.KEY, "A heavy iron key with an ornate handle.", key_id="vault", value=10),
        ], [
            NPC("Armorer", "A stout dwarf with soot-stained hands and a leather apron.", NPCDisposition.MERCHANT, dialogue=[
                "Ah, a adventurer! I have the finest weapons in the realm.",
                "The Sword of Light was forged by the ancient elves. It is your destiny to wield it.",
            ]),
        ])

        # Kitchen
        kitchen = Room("Kitchen", "A cozy kitchen with a crackling fireplace. The smell of fresh bread fills the air.", {"east": "entrance"}, [
            Item("Bread", ItemType.CONSUMABLE, "A loaf of fresh, warm bread.", "The bread is delicious! You feel slightly healthier.", effects=["heal:10"], value=5),
            Item("Cheese", ItemType.CONSUMABLE, "A wedge of aged cheddar cheese.", "Mmm, tasty cheese!", effects["heal:5"] if False else ["heal:5"], value=3),
        ], [])

        # Lab
        lab = Room("Alchemist Lab", "Bubbling beakers and strange apparatus cover every surface. The air shimmers with magical energy.", {"west": "library"}, [
            Item("Mana Potion", ItemType.CONSUMABLE, "A blue potion that restores magical energy.", "Energy courses through your veins!", effects=["heal:20"], value=40),
            Item("Bomb", ItemType.WEAPON, "A volatile explosive device.", "BOOM! The bomb explodes in a shower of sparks.", damage=50, is_usable=True, value=75),
        ], [
            NPC("Mad Alchemist", "A wild-eyed figure in a stained robe, muttering to himself.", NPCDisposition.NEUTRAL, dialogue=[
                "My experiments! You must not disturb my experiments!",
                "Fine, fine. Take a potion. But don't touch the bombs!",
                "The dragon? Oh yes, terrible beast. The Sword of Light is the only way.",
            ]),
        ])

        # Tower
        tower = Room("Dragon Tower", "A spiraling staircase leads up to a vast chamber. A massive dragon coils around a hoard of gold, its eyes gleaming with ancient intelligence.", {"south": "library"}, [
            Item("Dragon Hoard", ItemType.TREASURE, "A massive pile of gold coins and precious gems.", "The gold sparkles in the dragon's firelight.", value=1000, is_takeable=False),
        ], [
            NPC("Ancient Dragon", "A colossal red dragon with scales like molten steel. Its breath warps the air around it.", NPCDisposition.HOSTILE, health=500, damage=40, dialogue=[
                "WHO DARES ENTER MY DOMAIN?",
                "You seek my treasure? Then prove your worth!",
                "IMPOSSIBLE! The Sword of Light... how did you find it?!",
            ], loot=[Item("Dragon Scale", ItemType.TREASURE, "A scale from the ancient dragon.", value=200)]),
        ])

        self._rooms = {
            "entrance": entrance,
            "library": library,
            "armory": armory,
            "kitchen": kitchen,
            "lab": lab,
            "tower": tower,
        }

    @property
    def player(self) -> PlayerState:
        return self._player

    @property
    def current_room(self) -> Optional[Room]:
        return self._rooms.get(self._player.current_room)

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def game_time(self) -> str:
        if self._start_time == 0:
            return "0:00"
        elapsed = time.time() - self._start_time
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def start_game(self):
        self._is_running = True
        self._start_time = time.time()
        self._log.append(GameLog(time.time(), GameEvent.ENTER, "Welcome to the Dragon's Keep!", "entrance"))

    def process_command(self, command: str) -> str:
        self._turn_count += 1
        parts = command.lower().strip().split()
        if not parts:
            return "Please enter a command."
        verb = parts[0]
        noun = " ".join(parts[1:]) if len(parts) > 1 else ""
        room = self.current_room
        if not room:
            return "Error: No current room."
        if verb in ("go", "move", "walk") and noun:
            return self._move(noun)
        elif verb in ("look", "examine", "l"):
            return self._examine(noun)
        elif verb in ("take", "get", "grab") and noun:
            return self._take(noun)
        elif verb in ("use", "activate") and noun:
            return self._use(noun)
        elif verb in ("talk", "speak", "ask") and noun:
            return self._talk(noun)
        elif verb in ("attack", "fight", "hit") and noun:
            return self._attack(noun)
        elif verb == "inventory" or verb == "i":
            return self._show_inventory()
        elif verb == "status" or verb == "stats":
            return self._show_status()
        elif verb in ("help", "h", "?"):
            return self._show_help()
        elif verb == "quit":
            self._is_running = False
            return "Thanks for playing!"
        else:
            return f"I don't understand '{command}'. Type 'help' for commands."

    def _move(self, direction: str) -> str:
        room = self.current_room
        if not room:
            return "You can't move."
        if direction in room.exits:
            next_room_id = room.exits[direction]
            next_room = self._rooms.get(next_room_id)
            if not next_room:
                return "That way is blocked."
            if next_room.is_locked:
                for item in self._player.inventory:
                    if isinstance(item, Item) and item.item_type == ItemType.KEY and item.key_id == next_room.key_required:
                        next_room.is_locked = False
                        self._player.inventory.remove(item)
                        return f"You unlock the {next_room.name} with the {item.name}!"
                return f"The {next_room.name} is locked. You need a key."
            self._player.current_room = next_room_id
            next_room.visited = True
            self._log.append(GameLog(time.time(), GameEvent.ENTER, f"You enter the {next_room.name}.", next_room_id))
            return f"\n{next_room.name}\n{next_room.description}\n\nExits: {next_room.exit_list}"
        return f"You can't go {direction} from here. Available exits: {room.exit_list}"

    def _examine(self, noun: str) -> str:
        room = self.current_room
        if not room:
            return "Nothing to examine."
        if not noun:
            return room.description
        for item in room.items:
            if noun.lower() in item.name.lower():
                return f"{item.name}: {item.description}"
        for npc in room.npcs:
            if noun.lower() in npc.name.lower():
                return f"{npc.name}: {npc.description}"
        return f"You don't see '{noun}' here."

    def _take(self, noun: str) -> str:
        room = self.current_room
        if not room:
            return "Nothing to take."
        for i, item in enumerate(room.items):
            if noun.lower() in item.name.lower():
                if item.is_takeable:
                    room.items.pop(i)
                    self._player.inventory.append(item)
                    self._log.append(GameLog(time.time(), GameEvent.TAKE, f"You take the {item.name}."))
                    return f"You take the {item.name}. {item.use_text}"
                return f"You can't take the {item.name}."
        return f"You don't see '{noun}' to take."

    def _use(self, noun: str) -> str:
        for item in self._player.inventory:
            if noun.lower() in item.name.lower():
                if item.item_type == ItemType.CONSUMABLE:
                    for effect in item.effects:
                        if effect.startswith("heal:"):
                            amount = int(effect.split(":")[1])
                            self._player.health = min(self._player.max_health, self._player.health + amount)
                    self._player.inventory.remove(item)
                    return f"You use the {item.name}. {item.use_text}"
                elif item.item_type == ItemType.WEAPON:
                    return f"You wield the {item.name}. (Damage: {item.damage})"
                return f"You can't use the {item.name} right now."
        return f"You don't have '{noun}' in your inventory."

    def _talk(self, noun: str) -> str:
        room = self.current_room
        if not room:
            return "No one here to talk to."
        for npc in room.npcs:
            if noun.lower() in npc.name.lower():
                if npc.dialogue:
                    import random
                    response = random.choice(npc.dialogue)
                    return f"{npc.name}: \"{response}\""
                return f"{npc.name} doesn't respond."
        return f"You don't see '{noun}' here to talk to."

    def _attack(self, noun: str) -> str:
        room = self.current_room
        if not room:
            return "No one to attack."
        for npc in room.npcs:
            if noun.lower() in npc.name.lower():
                if not npc.is_alive:
                    return f"{npc.name} is already defeated."
                damage = self._player.attack_power
                npc.health -= damage
                result = f"You attack {npc.name} for {damage} damage! ({npc.health_bar} {max(0, npc.health)} HP)"
                if npc.health <= 0:
                    npc.is_alive = False
                    self._player.experience += 50
                    result += f"\n{npc.name} is defeated! You gain 50 XP."
                    for loot in npc.loot:
                        self._player.inventory.append(loot)
                        result += f"\nYou find: {loot.name}"
                else:
                    npc_damage = npc.damage
                    self._player.health -= npc_damage
                    result += f"\n{npc.name} attacks you for {npc_damage} damage! ({self.player.health_bar} {self.player.health} HP)"
                    if self._player.health <= 0:
                        result += "\n\nYOU DIED! Game Over."
                        self._is_running = False
                return result
        return f"You don't see '{noun}' to attack."

    def _show_inventory(self) -> str:
        if not self._player.inventory:
            return "Your inventory is empty."
        lines = ["Inventory:"]
        for item in self._player.inventory:
            lines.append(f"  - {item.name} ({item.item_type.value})")
        return "\n".join(lines)

    def _show_status(self) -> str:
        p = self._player
        return f"Health: [{p.health_bar}] {p.health}/{p.max_health}  Gold: {p.gold}  Level: {p.level}  XP: {p.experience}\nAttack: {p.attack_power}  Room: {self.current_room.name if self.current_room else 'Unknown'}"

    def _show_help(self) -> str:
        return "Commands:\n  go <direction> - Move (north/south/east/west/up/down)\n  look [target] - Examine room or item\n  take <item> - Pick up an item\n  use <item> - Use an item\n  talk <npc> - Talk to an NPC\n  attack <target> - Attack an NPC\n  inventory - Show inventory\n  status - Show player stats\n  quit - Exit game"

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                   NYRQIS ADVENTURE ENGINE                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Turn: {self._turn_count}  Time: {self.game_time}  Gold: {self._player.gold}  Level: {self._player.level}")
        lines.append(f"  HP: [{self._player.health_bar}] {self._player.health}/{self._player.max_health}  XP: {self._player.experience}")
        lines.append("")
        room = self.current_room
        if room:
            lines.append(f"  ── {room.name} ──")
            lines.append(f"  {room.description[:70]}")
            if room.items:
                lines.append(f"  Items: {', '.join(i.name for i in room.items)}")
            if room.npcs:
                lines.append(f"  NPCs: {', '.join(n.name for n in room.npcs)}")
            lines.append(f"  Exits: {room.exit_list}")
        lines.append("")
        lines.append("  ── Log ──")
        for entry in self._log[-5:]:
            lines.append(f"  {entry.message}")
        lines.append("")
        lines.append("  Type a command (go/take/use/talk/attack/inventory/status/help/quit)")
        return lines
