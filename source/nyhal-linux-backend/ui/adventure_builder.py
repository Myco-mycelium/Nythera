from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class BuilderMode(Enum):
    ROOMS = "rooms"
    ITEMS = "items"
    NPCS = "npcs"
    EXITS = "exits"
    EVENTS = "events"
    PREVIEW = "preview"


class RoomType(Enum):
    OUTDOOR = "outdoor"
    INDOOR = "indoor"
    DUNGEON = "dungeon"
    SPECIAL = "special"


class ItemType(Enum):
    WEAPON = "weapon"
    KEY = "key"
    CONSUMABLE = "consumable"
    QUEST = "quest"
    TREASURE = "treasure"
    TOOL = "tool"


class NPCRole(Enum):
    FRIENDLY = "friendly"
    MERCHANT = "merchant"
    GUARD = "guard"
    ENEMY = "enemy"
    QUEST_GIVER = "quest-giver"
    NEUTRAL = "neutral"


@dataclass
class RoomDef:
    id: str
    name: str
    description: str
    room_type: RoomType = RoomType.INDOOR
    exits: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    npcs: list = field(default_factory=list)
    is_start: bool = False
    is_locked: bool = False
    light_level: int = 100

    @property
    def exit_count(self) -> int:
        return len(self.exits)


@dataclass
class ItemDef:
    id: str
    name: str
    description: str
    item_type: ItemType
    damage: int = 0
    heal_amount: int = 0
    value: int = 0
    is_takeable: bool = True
    is_usable: bool = False
    key_id: str = ""


@dataclass
class NPCDef:
    id: str
    name: str
    description: str
    role: NPCRole
    health: int = 100
    damage: int = 0
    dialogue: list = field(default_factory=list)
    loot: list = field(default_factory=list)
    shop_items: list = field(default_factory=list)


@dataclass
class EventDef:
    id: str
    trigger: str
    action: str
    target: str
    condition: str = ""


@dataclass
class GameProject:
    name: str
    author: str
    description: str
    rooms: list = field(default_factory=list)
    items: list = field(default_factory=list)
    npcs: list = field(default_factory=list)
    events: list = field(default_factory=list)
    created_at: float = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    @property
    def total_entities(self) -> int:
        return len(self.rooms) + len(self.items) + len(self.npcs)

    @property
    def room_count(self) -> int:
        return len(self.rooms)


class AdventureBuilder:
    def __init__(self):
        self._project: Optional[GameProject] = None
        self._mode: BuilderMode = BuilderMode.ROOMS
        self._selected_room: int = 0
        self._selected_item: int = 0
        self._selected_npc: int = 0
        self._selected_event: int = 0
        self._view: str = "editor"
        self._create_sample_project()

    def _create_sample_project(self):
        rooms = [
            RoomDef("entrance", "Entrance Hall", "A grand hall with marble floors.", RoomType.INDOOR, {"north": "library", "east": "armory"}, ["torch"], ["guard"], is_start=True),
            RoomDef("library", "Ancient Library", "Shelves of ancient tomes.", RoomType.INDOOR, {"south": "entrance", "east": "lab"}, ["scroll"], ["librarian"]),
            RoomDef("armory", "Armory", "Racks of ancient weapons.", RoomType.INDOOR, {"west": "entrance"}, ["sword", "shield"], ["merchant"]),
            RoomDef("lab", "Alchemist Lab", "Bubbling beakers.", RoomType.INDOOR, {"west": "library"}, ["potion"], ["alchemist"]),
            RoomDef("tower", "Dragon Tower", "A vast chamber with a dragon.", RoomType.DUNGEON, {"south": "library"}, ["hoard"], ["dragon"]),
        ]

        items = [
            ItemDef("torch", "Torch", "A burning torch.", ItemType.TOOL),
            ItemDef("sword", "Sword of Light", "A legendary blade.", ItemType.WEAPON, damage=25, value=500),
            ItemDef("shield", "Iron Shield", "A sturdy shield.", ItemType.TOOL, value=100),
            ItemDef("scroll", "Ancient Scroll", "Contains an incantation.", ItemType.QUEST),
            ItemDef("potion", "Health Potion", "Restores health.", ItemType.CONSUMABLE, heal_amount=30, value=25),
            ItemDef("hoard", "Dragon Hoard", "Massive pile of gold.", ItemType.TREASURE, value=1000, is_takeable=False),
        ]

        npcs = [
            NPCDef("guard", "Guard", "A stalwart defender.", NPCRole.GUARD, health=150, damage=10, dialogue=["Halt! Who goes there?"]),
            NPCDef("librarian", "Librarian Ghost", "A translucent figure.", NPCRole.FRIENDLY, dialogue=["Welcome, seeker."]),
            NPCDef("merchant", "Merchant", "A trader of goods.", NPCRole.MERCHANT, shop_items=["sword", "shield"]),
            NPCDef("alchemist", "Alchemist", "A wild-eyed figure.", NPCRole.NEUTRAL, dialogue=["Don't touch my bombs!"]),
            NPCDef("dragon", "Ancient Dragon", "A colossal beast.", NPCRole.ENEMY, health=500, damage=40, dialogue=["WHO DARES?!"]),
        ]

        events = [
            EventDef("dragon_defeat", "npc_defeated", "dragon", "unlock_hoard"),
            EventDef("scroll_find", "item_taken", "scroll", "enable_tower_exit"),
        ]

        self._project = GameProject("Dragon's Keep", "Nyrqis Dev", "A classic dungeon crawl.", rooms, items, npcs, events)

    @property
    def project(self) -> Optional[GameProject]:
        return self._project

    def select_room(self, idx: int):
        if self._project and 0 <= idx < len(self._project.rooms):
            self._selected_room = idx

    def select_item(self, idx: int):
        if self._project and 0 <= idx < len(self._project.items):
            self._selected_item = idx

    def select_npc(self, idx: int):
        if self._project and 0 <= idx < len(self._project.npcs):
            self._selected_npc = idx

    def add_room(self, room: RoomDef):
        if self._project:
            self._project.rooms.append(room)

    def add_item(self, item: ItemDef):
        if self._project:
            self._project.items.append(item)

    def add_npc(self, npc: NPCDef):
        if self._project:
            self._project.npcs.append(npc)

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS ADVENTURE BUILDER                                ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        if self._project:
            p = self._project
            lines.append(f"  Project: {p.name}  Author: {p.author}")
            lines.append(f"  Rooms: {len(p.rooms)}  Items: {len(p.items)}  NPCs: {len(p.npcs)}  Events: {len(p.events)}  Total: {p.total_entities}")
            lines.append("")
            lines.append("  ── Rooms ──")
            for i, r in enumerate(p.rooms):
                sel = "▶" if i == self._selected_room else " "
                start = "🏠" if r.is_start else "  "
                lines.append(f"  {sel}{start} {r.name:<20s} [{r.room_type.value}]  {r.exit_count} exits  {len(r.items)} items  {len(r.npcs)} NPCs")
            lines.append("")
            lines.append("  ── Items ──")
            for i, item in enumerate(p.items):
                sel = "▶" if i == self._selected_item else " "
                type_icons = {"weapon": "⚔️", "key": "🔑", "consumable": "🧪", "quest": "📜", "treasure": "💰", "tool": "🔧"}
                icon = type_icons.get(item.item_type.value, "📦")
                lines.append(f"  {sel}{icon} {item.name:<20s} [{item.item_type.value}]  dmg:{item.damage}  heal:{item.heal_amount}  val:{item.value}")
            lines.append("")
            lines.append("  ── NPCs ──")
            for i, npc in enumerate(p.npcs):
                sel = "▶" if i == self._selected_npc else " "
                role_icons = {"friendly": "😊", "merchant": "🏪", "guard": "🛡️", "enemy": "👹", "quest-giver": "❗", "neutral": "😐"}
                icon = role_icons.get(npc.role.value, "👤")
                lines.append(f"  {sel}{icon} {npc.name:<20s} [{npc.role.value}]  HP:{npc.health}  dmg:{npc.damage}  {len(npc.dialogue)} lines")
            lines.append("")
            lines.append("  ── Events ──")
            for e in p.events:
                lines.append(f"  ⚡ {e.trigger} → {e.action} on {e.target}")
        lines.append("")
        lines.append("  [R]ooms  [I]tems  [N]PCs  [E]vents  [P]review  [A]dd  [D]elete  [S]ave  [L]oad")
        return lines

    def render_room_detail(self) -> list:
        if not self._project or self._selected_room >= len(self._project.rooms):
            return ["  No room selected"]
        r = self._project.rooms[self._selected_room]
        lines = []
        lines.append(f"  ── {r.name} ({r.id}) ──")
        lines.append(f"  Type: {r.room_type.value}  Start: {'Yes' if r.is_start else 'No'}  Locked: {'Yes' if r.is_locked else 'No'}")
        lines.append(f"  Light: {r.light_level}%")
        lines.append(f"  Description: {r.description}")
        lines.append(f"  Exits: {r.exits}")
        lines.append(f"  Items: {r.items}")
        lines.append(f"  NPCs: {r.npcs}")
        return lines

    def render_npc_detail(self) -> list:
        if not self._project or self._selected_npc >= len(self._project.npcs):
            return ["  No NPC selected"]
        npc = self._project.npcs[self._selected_npc]
        lines = []
        lines.append(f"  ── {npc.name} ({npc.id}) ──")
        lines.append(f"  Role: {npc.role.value}  HP: {npc.health}  Damage: {npc.damage}")
        lines.append(f"  Description: {npc.description}")
        if npc.dialogue:
            lines.append("  Dialogue:")
            for d in npc.dialogue:
                lines.append(f"    \"{d}\"")
        if npc.loot:
            lines.append(f"  Loot: {npc.loot}")
        if npc.shop_items:
            lines.append(f"  Shop: {npc.shop_items}")
        return lines
