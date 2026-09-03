"""
Nyrqis Mind Map — visual thinking and idea organization tool.

Features:
- Hierarchical node creation and editing
- Connection lines between nodes
- Auto-layout (tree, radial, force-directed)
- Node colors and icons
- Notes/attachments on nodes
- Collapse/expand branches
- Zoom and pan
- Export as text outline, JSON, or Markdown
- Keyboard navigation between nodes
- Undo/redo
- Search across nodes
"""

import time
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple, Set
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class LayoutType(Enum):
    TREE = "tree"
    RADIAL = "radial"
    FORCE = "force"


class NodeType(Enum):
    ROOT = "root"
    BRANCH = "branch"
    LEAF = "leaf"
    NOTE = "note"


NODE_ICONS = {
    NodeType.ROOT: "🍄",
    NodeType.BRANCH: "📁",
    NodeType.LEAF: "💡",
    NodeType.NOTE: "📝",
}

NODE_COLORS = [
    "#4A90D9", "#2ECC71", "#E74C3C", "#F39C12",
    "#9B59B6", "#1ABC9C", "#E67E22", "#3498DB",
]


@dataclass
class MindNode:
    """A node in the mind map."""
    text: str
    node_type: NodeType = NodeType.BRANCH
    notes: str = ""
    color: str = "#4A90D9"
    icon: str = ""
    collapsed: bool = False
    x: int = 0
    y: int = 0
    node_id: str = ""
    parent_id: str = ""
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.node_id:
            self.node_id = hashlib.md5(f"{self.text}{self.created}".encode()).hexdigest()[:8]
        if not self.icon:
            self.icon = NODE_ICONS.get(self.node_type, "💡")

    @property
    def display(self) -> str:
        return f"{self.icon} {self.text}"

    @property
    def has_children(self) -> bool:
        return False  # Will be set externally

    @property
    def depth(self) -> int:
        return 0  # Will be calculated


@dataclass
class MindConnection:
    """A connection between two nodes."""
    from_id: str
    to_id: str
    label: str = ""
    color: str = "#888888"
    connection_id: str = ""

    def __post_init__(self):
        if not self.connection_id:
            self.connection_id = hashlib.md5(f"{self.from_id}{self.to_id}".encode()).hexdigest()[:8]


# ─── Mind Map Editor ─────────────────────────────────────────────────────


class MindMap:
    """
    Mind map editor for Nyrqis OS.

    Creates and manages hierarchical mind maps with visual layout.
    """

    def __init__(self):
        self._nodes: List[MindNode] = []
        self._connections: List[MindConnection] = []
        self._selected_index: int = 0
        self._edit_mode: bool = False
        self._edit_text: str = ""
        self._layout: LayoutType = LayoutType.TREE
        self._zoom: float = 1.0
        self._pan_x: int = 0
        self._pan_y: int = 0
        self._search_query: str = ""
        self._history: List[str] = []  # Undo stack
        self._redo_stack: List[str] = []

        # Callbacks
        self._on_change: List[Callable] = []

        # Init sample mind map
        self._init_sample_map()

    def _init_sample_map(self) -> None:
        """Create a sample mind map."""
        # Root
        root = MindNode("Nyrqis OS", NodeType.ROOT, color="#4A90D9", node_id="root")
        self._nodes.append(root)

        # Level 1 branches
        branches = [
            ("Architecture", "branch", "#2ECC71", "root", "arch"),
            ("Applications", "branch", "#E74C3C", "root", "apps"),
            ("Development", "branch", "#F39C12", "root", "dev"),
            ("Community", "branch", "#9B59B6", "root", "community"),
        ]

        for text, ntype, color, parent, nid in branches:
            node = MindNode(text, NodeType.BRANCH, color=color, parent_id=parent, node_id=nid)
            self._nodes.append(node)
            self._connections.append(MindConnection(parent, nid))

        # Level 2 - Architecture
        arch_children = [
            ("Wayland Compositor", "leaf", "arch", "wayland"),
            ("Vulkan Renderer", "leaf", "arch", "vulkan"),
            ("DRM/KMS Backend", "leaf", "arch", "drm"),
            ("Plugin System", "leaf", "arch", "plugins"),
        ]
        for text, ntype, parent, nid in arch_children:
            node = MindNode(text, NodeType.LEAF, parent_id=parent, node_id=nid)
            self._nodes.append(node)
            self._connections.append(MindConnection(parent, nid))

        # Level 2 - Applications
        app_children = [
            ("Terminal", "leaf", "apps", "terminal"),
            ("File Manager", "leaf", "apps", "files"),
            ("Web Browser", "leaf", "apps", "browser"),
            ("Settings", "leaf", "apps", "settings"),
        ]
        for text, ntype, parent, nid in app_children:
            node = MindNode(text, NodeType.LEAF, parent_id=parent, node_id=nid)
            self._nodes.append(node)
            self._connections.append(MindConnection(parent, nid))

        # Level 2 - Development
        dev_children = [
            ("Python UI Layer", "leaf", "dev", "python"),
            ("Rust Core", "leaf", "dev", "rust"),
            ("Test Suite", "leaf", "dev", "tests"),
        ]
        for text, ntype, parent, nid in dev_children:
            node = MindNode(text, NodeType.LEAF, parent_id=parent, node_id=nid)
            self._nodes.append(node)
            self._connections.append(MindConnection(parent, nid))

        # Level 2 - Community
        community_children = [
            ("GitHub Repository", "leaf", "community", "github"),
            ("Documentation", "leaf", "community", "docs"),
            ("Contributing Guide", "leaf", "community", "contrib"),
        ]
        for text, ntype, parent, nid in community_children:
            node = MindNode(text, NodeType.LEAF, parent_id=parent, node_id=nid)
            self._nodes.append(node)
            self._connections.append(MindConnection(parent, nid))

        # Level 3 examples
        more_children = [
            ("Window Manager", "leaf", "wayland", "wm"),
            ("Input Handling", "leaf", "wayland", "input"),
            ("GPU Acceleration", "leaf", "vulkan", "gpu"),
        ]
        for text, ntype, parent, nid in more_children:
            node = MindNode(text, NodeType.LEAF, parent_id=parent, node_id=nid)
            self._nodes.append(node)
            self._connections.append(MindConnection(parent, nid))

    # ── Node Operations ───────────────────────────────────────────────

    def get_node(self, node_id: str) -> Optional[MindNode]:
        for n in self._nodes:
            if n.node_id == node_id:
                return n
        return None

    def get_children(self, node_id: str) -> List[MindNode]:
        """Get direct children of a node."""
        child_ids = [c.to_id for c in self._connections if c.from_id == node_id]
        return [self.get_node(cid) for cid in child_ids if self.get_node(cid)]

    def get_root(self) -> Optional[MindNode]:
        return self.get_node("root")

    def add_node(self, text: str, parent_id: str = "", node_type: NodeType = NodeType.LEAF) -> MindNode:
        """Add a new node."""
        if not parent_id and self._selected_index < len(self._nodes):
            parent_id = self._nodes[self._selected_index].node_id

        node = MindNode(text, node_type, parent_id=parent_id)
        self._nodes.append(node)

        if parent_id:
            self._connections.append(MindConnection(parent_id, node.node_id))

        self._save_undo()
        self._notify("change")
        return node

    def delete_node(self, node_id: str) -> bool:
        """Delete a node and its children."""
        if node_id == "root":
            return False

        # Get all descendants
        to_delete = self._get_descendants(node_id)
        to_delete.add(node_id)

        self._nodes = [n for n in self._nodes if n.node_id not in to_delete]
        self._connections = [c for c in self._connections
                             if c.from_id not in to_delete and c.to_id not in to_delete]

        self._save_undo()
        self._notify("change")
        return True

    def _get_descendants(self, node_id: str) -> Set[str]:
        """Get all descendant node IDs."""
        result = set()
        children = self.get_children(node_id)
        for child in children:
            result.add(child.node_id)
            result.update(self._get_descendants(child.node_id))
        return result

    def update_node_text(self, node_id: str, text: str) -> bool:
        node = self.get_node(node_id)
        if node:
            node.text = text
            node.modified = time.time()
            self._notify("change")
            return True
        return False

    def update_node_notes(self, node_id: str, notes: str) -> bool:
        node = self.get_node(node_id)
        if node:
            node.notes = notes
            node.modified = time.time()
            return True
        return False

    def update_node_color(self, node_id: str, color: str) -> bool:
        node = self.get_node(node_id)
        if node:
            node.color = color
            return True
        return False

    def toggle_collapse(self, node_id: str) -> bool:
        node = self.get_node(node_id)
        if node:
            node.collapsed = not node.collapsed
            return node.collapsed
        return False

    @property
    def selected_node(self) -> Optional[MindNode]:
        if 0 <= self._selected_index < len(self._nodes):
            return self._nodes[self._selected_index]
        return None

    @property
    def visible_nodes(self) -> List[MindNode]:
        """Get nodes visible (not collapsed behind a parent)."""
        collapsed_parents = set()
        for node in self._nodes:
            if node.collapsed:
                collapsed_parents.update(self._get_descendants(node.node_id))

        return [n for n in self._nodes if n.node_id not in collapsed_parents]

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    # ── Selection ─────────────────────────────────────────────────────

    def select(self, index: int) -> None:
        visible = self.visible_nodes
        self._selected_index = max(0, min(len(visible) - 1, index))

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        visible = self.visible_nodes
        self._selected_index = min(len(visible) - 1, self._selected_index + 1)

    def select_parent(self) -> None:
        """Move selection to parent node."""
        node = self.selected_node
        if node and node.parent_id:
            visible = self.visible_nodes
            for i, n in enumerate(visible):
                if n.node_id == node.parent_id:
                    self._selected_index = i
                    break

    def select_child(self) -> None:
        """Move selection to first child."""
        node = self.selected_node
        if node:
            children = self.get_children(node.node_id)
            if children:
                visible = self.visible_nodes
                for i, n in enumerate(visible):
                    if n.node_id == children[0].node_id:
                        self._selected_index = i
                        break

    # ── Search ────────────────────────────────────────────────────────

    def search(self, query: str) -> List[MindNode]:
        self._search_query = query
        if not query:
            return []
        q = query.lower()
        return [n for n in self._nodes if q in n.text.lower() or q in n.notes.lower()]

    # ── Undo/Redo ─────────────────────────────────────────────────────

    def _save_undo(self) -> None:
        state = json.dumps([{"id": n.node_id, "text": n.text, "parent": n.parent_id} for n in self._nodes])
        self._history.append(state)
        if len(self._history) > 50:
            self._history.pop(0)
        self._redo_stack.clear()

    def undo(self) -> bool:
        if self._history:
            self._redo_stack.append(self._history.pop())
            return True
        return False

    def redo(self) -> bool:
        if self._redo_stack:
            self._history.append(self._redo_stack.pop())
            return True
        return False

    # ── Export ────────────────────────────────────────────────────────

    def export_outline(self) -> str:
        """Export as text outline."""
        lines = []
        self._export_node("root", lines, 0)
        return "\n".join(lines)

    def _export_node(self, node_id: str, lines: List[str], depth: int) -> None:
        node = self.get_node(node_id)
        if node:
            indent = "  " * depth
            lines.append(f"{indent}{'├── ' if depth > 0 else ''}{node.text}")
            if node.notes:
                lines.append(f"{indent}    📝 {node.notes[:50]}")
            children = self.get_children(node_id)
            for child in children:
                self._export_node(child.node_id, lines, depth + 1)

    def export_markdown(self) -> str:
        """Export as Markdown."""
        lines = ["# Nyrqis Mind Map\n"]
        self._export_md_node("root", lines, 0)
        return "\n".join(lines)

    def _export_md_node(self, node_id: str, lines: List[str], depth: int) -> None:
        node = self.get_node(node_id)
        if node:
            prefix = "#" * min(depth + 1, 6)
            lines.append(f"{prefix} {node.text}")
            if node.notes:
                lines.append(f"\n{node.notes}\n")
            children = self.get_children(node_id)
            for child in children:
                self._export_md_node(child.node_id, lines, depth + 1)

    # ── Rendering ─────────────────────────────────────────────────────

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        lines = []
        lines.append(f" 🧠 Mind Map — {self.node_count} nodes, {self.connection_count} connections")
        lines.append("─" * width)

        # Tree view
        self._render_tree("root", lines, 0, width)

        lines.append("─" * width)

        # Selected node info
        node = self.selected_node
        if node:
            lines.append(f" Selected: {node.display}")
            if node.notes:
                lines.append(f" Notes: {node.notes[:width - 8]}")
            lines.append(f" Color: {node.color}")
            lines.append(f" Children: {len(self.get_children(node.node_id))}")

        if self._search_query:
            results = self.search(self._search_query)
            lines.append(f" 🔍 \"{self._search_query}\" ({len(results)} matches)")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Add child  D:Delete  Space:Collapse")
        lines.append(" N:Notes  C:Color  Tab:Parent  Shift+Tab:Child")
        return lines

    def _render_tree(self, node_id: str, lines: List[str], depth: int, width: int) -> None:
        node = self.get_node(node_id)
        if not node:
            return

        indent = "  " * depth
        connector = "├── " if depth > 0 else ""
        collapse = " ▸" if node.collapsed else ""

        # Highlight selected
        visible = self.visible_nodes
        is_selected = (self._selected_index < len(visible) and
                       visible[self._selected_index].node_id == node_id)
        marker = "▸ " if is_selected else "  "

        line = f"{marker}{indent}{connector}{node.display}{collapse}"
        lines.append(line[:width])

        # Children (if not collapsed)
        if not node.collapsed:
            children = self.get_children(node_id)
            for child in children:
                self._render_tree(child.node_id, lines, depth + 1, width)

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_change(self, cb: Callable) -> None:
        self._on_change.append(cb)

    def _notify(self, event: str) -> None:
        for cb in self._on_change:
            try:
                cb()
            except Exception:
                pass
