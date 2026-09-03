"""Mind Map Editor — nodes, connections, export for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time


class NodeShape(Enum):
    ROUNDED_RECT = "Rounded Rectangle"
    RECTANGLE = "Rectangle"
    CIRCLE = "Circle"
    DIAMOND = "Diamond"
    HEXAGON = "Hexagon"
    CLOUD = "Cloud"
    ELLIPSE = "Ellipse"


class ExportFormat(Enum):
    MARKDOWN = "Markdown"
    TEXT = "Plain Text"
    JSON = "JSON"
    HTML = "HTML"
    SVG = "SVG"
    OPML = "OPML"
    FREEFORM = "FreeMind"


class NodeStyle(Enum):
    DEFAULT = "Default"
    IDEA = "Idea"
    TASK = "Task"
    NOTE = "Note"
    QUESTION = "Question"
    ANSWER = "Answer"
    PROBLEM = "Problem"
    SOLUTION = "Solution"


@dataclass
class MindNode:
    id: int
    text: str = ""
    x: float = 0.0
    y: float = 0.0
    width: float = 120.0
    height: float = 40.0
    color: str = "#4a9eff"
    shape: NodeShape = NodeShape.ROUNDED_RECT
    style: NodeStyle = NodeStyle.DEFAULT
    collapsed: bool = False
    notes: str = ""
    url: str = ""
    tags: List[str] = field(default_factory=list)
    priority: int = 0  # 0=none, 1=low, 2=medium, 3=high
    completed: bool = False
    children_ids: List[int] = field(default_factory=list)
    parent_id: int = -1

    @property
    def priority_icon(self) -> str:
        icons = {1: "🟢", 2: "🟡", 3: "🔴"}
        return icons.get(self.priority, "")

    @property
    def style_icon(self) -> str:
        icons = {
            NodeStyle.IDEA: "💡", NodeStyle.TASK: "✅", NodeStyle.NOTE: "📝",
            NodeStyle.QUESTION: "❓", NodeStyle.ANSWER: "✅", NodeStyle.PROBLEM: "⚠️",
            NodeStyle.SOLUTION: "🔧",
        }
        return icons.get(self.style, "●")

    @property
    def shape_char(self) -> str:
        chars = {
            NodeShape.ROUNDED_RECT: "▢", NodeShape.RECTANGLE: "□",
            NodeShape.CIRCLE: "○", NodeShape.DIAMOND: "◇",
            NodeShape.HEXAGON: "⬡", NodeShape.CLOUD: "☁",
        }
        return chars.get(self.shape, "●")

    @property
    def child_count(self) -> int:
        return len(self.children_ids)

    @property
    def depth_str(self) -> str:
        return "root" if self.parent_id == -1 else f"child of {self.parent_id}"


@dataclass
class Connection:
    source_id: int
    target_id: int
    label: str = ""
    color: str = "#888888"
    dashed: bool = False
    thickness: float = 1.0
    style: str = "solid"  # solid, dashed, dotted


@dataclass
class MindMapLayout:
    direction: str = "right"  # right, down, radial, tree
    spacing_h: float = 200.0
    spacing_v: float = 80.0
    auto_arrange: bool = True


@dataclass
class MindMap:
    name: str = "Untitled"
    created: float = 0.0
    modified: float = 0.0
    root_id: int = 0
    nodes: List[MindNode] = field(default_factory=list)
    connections: List[Connection] = field(default_factory=list)
    layout: MindMapLayout = field(default_factory=MindMapLayout)
    description: str = ""
    tags: List[str] = field(default_factory=list)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def connection_count(self) -> int:
        return len(self.connections)

    @property
    def depth(self) -> int:
        return self._calc_depth(0)

    def _calc_depth(self, node_id: int, visited: set = None) -> int:
        if visited is None:
            visited = set()
        if node_id in visited:
            return 0
        visited.add(node_id)
        node = next((n for n in self.nodes if n.id == node_id), None)
        if not node or not node.children_ids:
            return 0
        return 1 + max(self._calc_depth(cid, visited) for cid in node.children_ids)


class MindMapEditor:
    def __init__(self):
        self._maps: List[MindMap] = []
        self._selected_map: int = 0
        self._selected_node: int = 0
        self._zoom: float = 1.0
        self._pan_x: float = 0.0
        self._pan_y: float = 0.0
        self._clipboard_node: Optional[MindNode] = None
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Map 1: Project Architecture
        m1 = MindMap("Nyrqis Architecture", now - 86400 * 30, now - 3600,
                      description="System architecture overview", tags=["architecture", "design"])
        m1.nodes = [
            MindNode(0, "Nyrqis OS", 400, 300, 160, 50, "#1a73e8", NodeShape.ROUNDED_RECT, NodeStyle.DEFAULT,
                     children_ids=[1, 2, 3, 4]),
            MindNode(1, "Compositor", 700, 150, 130, 40, "#ea4335", NodeShape.ROUNDED_RECT, NodeStyle.IDEA,
                     notes="Rust-based Wayland compositor", parent_id=0, children_ids=[5, 6, 7]),
            MindNode(2, "Shell", 700, 250, 130, 40, "#34a853", NodeShape.ROUNDED_RECT, NodeStyle.TASK,
                     parent_id=0, children_ids=[8, 9]),
            MindNode(3, "HAL", 700, 350, 130, 40, "#fbbc04", NodeShape.ROUNDED_RECT, NodeStyle.NOTE,
                     parent_id=0, children_ids=[10, 11]),
            MindNode(4, "Apps", 700, 450, 130, 40, "#9334e6", NodeShape.ROUNDED_RECT, NodeStyle.DEFAULT,
                     parent_id=0, children_ids=[12, 13, 14]),
            MindNode(5, "Wayland Protocols", 1000, 100, 150, 35, "#ea4335", NodeShape.ROUNDED_RECT, NodeStyle.DEFAULT,
                     notes="wl_compositor, xdg_wm_base, wl_seat", parent_id=1),
            MindNode(6, "DRM/KMS", 1000, 160, 130, 35, "#ea4335", NodeShape.ROUNDED_RECT, NodeStyle.DEFAULT,
                     parent_id=1),
            MindNode(7, "Input Handling", 1000, 220, 130, 35, "#ea4335", NodeShape.ROUNDED_RECT, NodeStyle.DEFAULT,
                     parent_id=1),
            MindNode(8, "NUI Schema", 1000, 250, 130, 35, "#34a853", NodeShape.ROUNDED_RECT, NodeStyle.DEFAULT,
                     parent_id=2),
            MindNode(9, "State Management", 1000, 310, 150, 35, "#34a853", NodeShape.ROUNDED_RECT, NodeStyle.DEFAULT,
                     parent_id=2),
            MindNode(10, "GPU Access", 1000, 350, 130, 35, "#fbbc04", NodeShape.ROUNDED_RECT, NodeStyle.DEFAULT,
                     parent_id=3),
            MindNode(11, "Input Devices", 1000, 410, 140, 35, "#fbbc04", NodeShape.ROUNDED_RECT, NodeStyle.DEFAULT,
                     parent_id=3),
            MindNode(12, "File Manager", 1000, 450, 130, 35, "#9334e6", NodeShape.ROUNDED_RECT, NodeStyle.TASK,
                     completed=True, parent_id=4),
            MindNode(13, "Terminal", 1000, 510, 130, 35, "#9334e6", NodeShape.ROUNDED_RECT, NodeStyle.TASK,
                     completed=True, parent_id=4),
            MindNode(14, "Settings", 1000, 570, 130, 35, "#9334e6", NodeShape.ROUNDED_RECT, NodeStyle.TASK,
                     parent_id=4),
        ]
        m1.connections = [
            Connection(0, 1), Connection(0, 2), Connection(0, 3), Connection(0, 4),
            Connection(1, 5), Connection(1, 6), Connection(1, 7),
            Connection(2, 8), Connection(2, 9),
            Connection(3, 10), Connection(3, 11),
            Connection(4, 12), Connection(4, 13), Connection(4, 14),
        ]
        self._maps.append(m1)

        # Map 2: Feature Planning
        m2 = MindMap("Feature Roadmap", now - 86400 * 15, now - 86400,
                      description="Q3/Q4 feature planning", tags=["roadmap", "planning"])
        m2.nodes = [
            MindNode(0, "v2.0 Features", 400, 300, 160, 50, "#1a73e8", children_ids=[1, 2, 3]),
            MindNode(1, "Q3 2026", 700, 200, 130, 40, "#ea4335", parent_id=0, children_ids=[4, 5]),
            MindNode(2, "Q4 2026", 700, 300, 130, 40, "#34a853", parent_id=0, children_ids=[6, 7]),
            MindNode(3, "2027", 700, 400, 130, 40, "#fbbc04", parent_id=0, children_ids=[8]),
            MindNode(4, "Vulkan Renderer", 1000, 170, 140, 35, "#ea4335", priority=3, parent_id=1),
            MindNode(5, "Bluetooth Stack", 1000, 230, 140, 35, "#ea4335", priority=2, parent_id=1),
            MindNode(6, "App Store", 1000, 270, 130, 35, "#34a853", priority=2, parent_id=2),
            MindNode(7, "Mobile Support", 1000, 330, 140, 35, "#34a853", priority=3, parent_id=2),
            MindNode(8, "AI Assistant", 1000, 400, 130, 35, "#fbbc04", priority=1, parent_id=3),
        ]
        m2.connections = [
            Connection(0, 1), Connection(0, 2), Connection(0, 3),
            Connection(1, 4), Connection(1, 5), Connection(2, 6), Connection(2, 7), Connection(3, 8),
        ]
        self._maps.append(m2)

    @property
    def selected_map(self) -> Optional[MindMap]:
        if 0 <= self._selected_map < len(self._maps):
            return self._maps[self._selected_map]
        return None

    @property
    def selected_node(self) -> Optional[MindNode]:
        m = self.selected_map
        if m and 0 <= self._selected_node < len(m.nodes):
            return m.nodes[self._selected_node]
        return None

    @property
    def total_nodes(self) -> int:
        return sum(m.node_count for m in self._maps)

    def select_map(self, idx: int):
        if 0 <= idx < len(self._maps):
            self._selected_map = idx
            self._selected_node = 0

    def select_node(self, idx: int):
        self._selected_node = idx

    def add_child(self):
        m = self.selected_map
        node = self.selected_node
        if m and node:
            new_id = max(n.id for n in m.nodes) + 1
            child = MindNode(new_id, "New Idea", node.x + 200, node.y + len(node.children_ids) * 50,
                             parent_id=node.id)
            m.nodes.append(child)
            node.children_ids.append(new_id)
            m.connections.append(Connection(node.id, new_id))
            self._history.append(f"Added child to {node.text}")

    def delete_node(self):
        m = self.selected_map
        node = self.selected_node
        if m and node and node.id != 0:
            m.nodes = [n for n in m.nodes if n.id != node.id]
            m.connections = [c for c in m.connections if c.source_id != node.id and c.target_id != node.id]
            self._selected_node = max(0, self._selected_node - 1)
            self._history.append(f"Deleted {node.text}")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "a":
            self.add_child()
        elif key == "d":
            self.delete_node()
        elif key == "c":
            if self.selected_node:
                self._clipboard_node = self.selected_node

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS MIND MAP EDITOR                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  Maps: {len(self._maps)}  Total Nodes: {self.total_nodes}  Zoom: {self._zoom:.0%}")
        lines.append("")

        # Maps
        lines.append("  ── Mind Maps ──")
        for i, m in enumerate(self._maps):
            sel = "▶" if i == self._selected_map else " "
            lines.append(f"  {sel} 🧠 {m.name}  {m.node_count} nodes  {m.connection_count} connections  Depth: {m.depth}")
        lines.append("")

        # Nodes in tree view
        m = self.selected_map
        if m:
            lines.append(f"  ── {m.name} ──")
            root = next((n for n in m.nodes if n.id == m.root_id), None)
            if root:
                self._render_node_tree(lines, m, root, 0)
            lines.append("")

        # Selected node detail
        node = self.selected_node
        if node:
            lines.append(f"  ── Node: {node.text} ──")
            lines.append(f"  Shape: {node.shape.value}  Style: {node.style.value}  Color: {node.color}")
            lines.append(f"  Position: ({node.x:.0f}, {node.y:.0f})  Children: {node.child_count}  Parent: {node.depth_str}")
            if node.notes:
                lines.append(f"  Notes: {node.notes[:60]}")
            if node.tags:
                lines.append(f"  Tags: {' '.join(f'[{t}]' for t in node.tags)}")
            if node.priority:
                lines.append(f"  Priority: {node.priority_icon} {'Low' if node.priority == 1 else 'Medium' if node.priority == 2 else 'High'}")
            if node.completed:
                lines.append(f"  Status: ✅ Completed")
            lines.append("")

        # Layout
        if m:
            lines.append(f"  ── Layout ──")
            lines.append(f"  Direction: {m.layout.direction}  Spacing: {m.layout.spacing_h:.0f}x{m.layout.spacing_v:.0f}  Auto: {'ON' if m.layout.auto_arrange else 'OFF'}")
            lines.append("")

        lines.append("  [A]Add Child [D]Delete [C]Copy [E]Export [↑↓]Node [←→]Map")
        return lines

    def _render_node_tree(self, lines: List[str], m: MindMap, node: MindNode, depth: int):
        indent = "  " * (depth + 2)
        check = "✅" if node.completed else "  "
        priority = node.priority_icon
        lines.append(f"  {indent}{check} {node.style_icon} {node.text} {priority}")
        if not node.collapsed:
            for child_id in node.children_ids:
                child = next((n for n in m.nodes if n.id == child_id), None)
                if child:
                    self._render_node_tree(lines, m, child, depth + 1)
