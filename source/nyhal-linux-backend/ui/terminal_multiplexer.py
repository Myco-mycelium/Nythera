"""
Nyrqis Terminal Multiplexer — split panes, tabs, and sessions.

Features:
- Tabbed sessions (new, close, rename, switch)
- Split panes (horizontal and vertical)
- Pane navigation (focus, resize, close)
- Session history per pane
- Copy/paste between panes
- Synchronized input mode
- Layout save/restore
- Keyboard shortcuts for all operations
"""

import os
import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class SplitDirection(Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class PaneState(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SPLIT = "split"


@dataclass
class Pane:
    """A single terminal pane."""
    pane_id: str = ""
    title: str = "Terminal"
    history: List[str] = field(default_factory=list)
    cursor_line: int = 0
    scrollback: int = 5000
    width: int = 80
    height: int = 24
    created: float = field(default_factory=time.time)
    pid: int = 0  # simulated process ID

    def __post_init__(self):
        if not self.pane_id:
            self.pane_id = hashlib.md5(f"{self.created}{self.title}".encode()).hexdigest()[:6]
        if not self.pid:
            self.pid = 1000 + hash(self.pane_id) % 9000

    @property
    def display_title(self) -> str:
        return self.title[:20]

    @property
    def line_count(self) -> int:
        return len(self.history)

    def write(self, text: str) -> None:
        """Write output to pane history."""
        for line in text.split("\n"):
            self.history.append(line)
            if len(self.history) > self.scrollback:
                self.history = self.history[-self.scrollback:]
            self.cursor_line = len(self.history) - 1

    def clear(self) -> None:
        self.history.clear()
        self.cursor_line = 0

    def get_visible_lines(self, count: int = 24) -> List[str]:
        start = max(0, self.cursor_line - count + 1)
        end = min(len(self.history), start + count)
        lines = self.history[start:end]
        while len(lines) < count:
            lines.append("")
        return lines


@dataclass
class SplitNode:
    """Binary tree node for split layout."""
    pane: Optional[Pane] = None
    split: Optional[SplitDirection] = None
    split_ratio: float = 0.5
    children: List['SplitNode'] = field(default_factory=list)
    parent: Optional['SplitNode'] = None

    @property
    def is_leaf(self) -> bool:
        return self.pane is not None

    @property
    def depth(self) -> int:
        if self.is_leaf:
            return 0
        return 1 + max(c.depth for c in self.children) if self.children else 0

    def find_pane(self, pane_id: str) -> Optional['SplitNode']:
        if self.pane and self.pane.pane_id == pane_id:
            return self
        for child in self.children:
            result = child.find_pane(pane_id)
            if result:
                return result
        return None

    def all_panes(self) -> List[Pane]:
        if self.is_leaf:
            return [self.pane]
        panes = []
        for child in self.children:
            panes.extend(child.all_panes())
        return panes

    def to_dict(self) -> Dict:
        if self.is_leaf:
            return {"pane_id": self.pane.pane_id, "title": self.pane.title}
        return {
            "split": self.split.value if self.split else "none",
            "ratio": self.split_ratio,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class Session:
    """A tab session containing a split layout."""
    name: str = "Terminal"
    root: SplitNode = field(default=None)
    focused_pane_id: str = ""
    created: float = field(default_factory=time.time)
    session_id: str = ""

    def __post_init__(self):
        if self.root is None:
            pane = Pane(title="Terminal")
            self.root = SplitNode(pane=pane)
            self.focused_pane_id = pane.pane_id
        if not self.session_id:
            self.session_id = hashlib.md5(f"{self.created}{self.name}".encode()).hexdigest()[:6]

    @property
    def pane_count(self) -> int:
        return len(self.root.all_panes())

    @property
    def all_panes(self) -> List[Pane]:
        return self.root.all_panes()

    @property
    def focused_pane(self) -> Optional[Pane]:
        for pane in self.all_panes:
            if pane.pane_id == self.focused_pane_id:
                return pane
        return self.all_panes[0] if self.all_panes else None


# ─── Terminal Multiplexer ────────────────────────────────────────────────


class TerminalMultiplexer:
    """
    Terminal multiplexer for Nyrqis OS.

    Manages sessions (tabs) and panes (splits) with keyboard navigation.
    """

    def __init__(self, width: int = 80, height: int = 24):
        self._width = width
        self._height = height
        self._sessions: List[Session] = []
        self._session_index: int = 0

        # Init with default session
        pane = Pane(title="Terminal 1")
        root = SplitNode(pane=pane)
        session = Session(name="Terminal", root=root, focused_pane_id=pane.pane_id)
        self._sessions.append(session)

        # Sync mode
        self._sync_mode: bool = False
        self._sync_panes: List[str] = []

        # Callbacks
        self._on_split: List[Callable] = []

    # ── Session Management ────────────────────────────────────────────

    def new_session(self, name: str = "") -> Session:
        """Create a new tab session."""
        pane = Pane(title="Terminal")
        root = SplitNode(pane=pane)
        session = Session(
            name=name or f"Terminal {len(self._sessions) + 1}",
            root=root,
            focused_pane_id=pane.pane_id,
        )
        self._sessions.append(session)
        self._session_index = len(self._sessions) - 1
        return session

    def close_session(self, index: int = -1) -> bool:
        """Close a tab session."""
        if len(self._sessions) <= 1:
            return False
        idx = index if index >= 0 else self._session_index
        if idx < 0 or idx >= len(self._sessions):
            return False
        self._sessions.pop(idx)
        self._session_index = min(self._session_index, len(self._sessions) - 1)
        return True

    def switch_session(self, index: int) -> bool:
        if 0 <= index < len(self._sessions):
            self._session_index = index
            return True
        return False

    def next_session(self) -> None:
        if self._sessions:
            self._session_index = (self._session_index + 1) % len(self._sessions)

    def prev_session(self) -> None:
        if self._sessions:
            self._session_index = (self._session_index - 1) % len(self._sessions)

    def rename_session(self, name: str) -> None:
        session = self.current_session
        if session:
            session.name = name

    @property
    def current_session(self) -> Optional[Session]:
        if 0 <= self._session_index < len(self._sessions):
            return self._sessions[self._session_index]
        return None

    @property
    def session_index(self) -> int:
        return self._session_index

    @property
    def sessions(self) -> List[Session]:
        return list(self._sessions)

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    # ── Pane Operations ───────────────────────────────────────────────

    def split_horizontal(self) -> Optional[Pane]:
        """Split current pane horizontally."""
        return self._split_pane(SplitDirection.HORIZONTAL)

    def split_vertical(self) -> Optional[Pane]:
        """Split current pane vertically."""
        return self._split_pane(SplitDirection.VERTICAL)

    def _split_pane(self, direction: SplitDirection) -> Optional[Pane]:
        """Internal split operation."""
        session = self.current_session
        if not session:
            return None

        new_pane = Pane(title="Terminal")
        focused_id = session.focused_pane_id

        # Find the focused node
        target_node = session.root.find_pane(focused_id)
        if not target_node or target_node.is_leaf:
            # Replace leaf with split
            parent = target_node.parent if target_node else None
            if target_node:
                new_node = SplitNode(
                    split=direction,
                    children=[
                        SplitNode(pane=target_node.pane),
                        SplitNode(pane=new_pane),
                    ],
                    parent=parent,
                )
                new_node.children[0].parent = new_node
                new_node.children[1].parent = new_node
                if parent:
                    idx = parent.children.index(target_node) if target_node in parent.children else 0
                    parent.children[idx] = new_node
                else:
                    session.root = new_node
                    new_node.parent = None
            else:
                new_root = SplitNode(
                    split=direction,
                    children=[
                        SplitNode(pane=Pane(title="Terminal")),
                        SplitNode(pane=new_pane),
                    ],
                )
                new_root.children[0].parent = new_root
                new_root.children[1].parent = new_root
                session.root = new_root
        else:
            # Add to existing split
            target_node.split = direction
            child1 = SplitNode(
                pane=target_node.children[0].pane if target_node.children else Pane(),
                parent=target_node,
            )
            child2 = SplitNode(pane=new_pane, parent=target_node)
            target_node.children = [child1, child2]

        session.focused_pane_id = new_pane.pane_id
        self._notify("split")
        return new_pane

    def close_pane(self, pane_id: str = None) -> bool:
        """Close a pane."""
        session = self.current_session
        if not session:
            return False

        target_id = pane_id or session.focused_pane_id
        panes = session.all_panes

        if len(panes) <= 1:
            return False

        # Find and remove the pane node
        node = session.root.find_pane(target_id)
        if not node or node.is_leaf is False:
            return False

        # Remove from parent
        parent = node.parent
        if parent:
            # Find sibling (the other child)
            sibling = None
            for c in parent.children:
                if c.pane and c.pane.pane_id != target_id:
                    sibling = c
                    break
            if sibling:
                # Replace parent with sibling in grandparent
                grandparent = parent.parent
                if grandparent:
                    idx = grandparent.children.index(parent)
                    grandparent.children[idx] = sibling
                    sibling.parent = grandparent
                else:
                    # Parent is root, promote sibling
                    session.root = sibling
                    sibling.parent = None
            else:
                parent.children.remove(node)
        else:
            # Root pane, create new empty one
            new_pane = Pane()
            session.root = SplitNode(pane=new_pane)
            session.root.pane = new_pane

        # Focus another pane
        remaining = session.all_panes
        if remaining and target_id == session.focused_pane_id:
            session.focused_pane_id = remaining[0].pane_id

        return True

    def focus_pane(self, pane_id: str) -> bool:
        """Focus a specific pane."""
        session = self.current_session
        if not session:
            return False
        for pane in session.all_panes:
            if pane.pane_id == pane_id:
                session.focused_pane_id = pane_id
                return True
        return False

    def focus_next_pane(self) -> None:
        """Focus the next pane in order."""
        session = self.current_session
        if not session:
            return
        panes = session.all_panes
        if not panes:
            return
        ids = [p.pane_id for p in panes]
        try:
            idx = ids.index(session.focused_pane_id)
            session.focused_pane_id = ids[(idx + 1) % len(ids)]
        except ValueError:
            session.focused_pane_id = panes[0].pane_id

    def focus_prev_pane(self) -> None:
        session = self.current_session
        if not session:
            return
        panes = session.all_panes
        if not panes:
            return
        ids = [p.pane_id for p in panes]
        try:
            idx = ids.index(session.focused_pane_id)
            session.focused_pane_id = ids[(idx - 1) % len(ids)]
        except ValueError:
            session.focused_pane_id = panes[0].pane_id

    def focus_left(self) -> None:
        """Focus the pane to the left."""
        self._focus_by_direction("left")

    def focus_right(self) -> None:
        self._focus_by_direction("right")

    def focus_up(self) -> None:
        self._focus_by_direction("up")

    def focus_down(self) -> None:
        self._focus_by_direction("down")

    def _focus_by_direction(self, direction: str) -> None:
        """Focus pane by spatial direction (simplified)."""
        session = self.current_session
        if not session:
            return
        panes = session.all_panes
        if len(panes) <= 1:
            return
        # Just cycle to next pane (spatial layout would need geometry tracking)
        self.focus_next_pane()

    def resize_pane(self, pane_id: str, delta: int) -> bool:
        """Resize a pane by delta (positive = larger)."""
        session = self.current_session
        if not session:
            return False
        node = session.root.find_pane(pane_id)
        if node and node.parent and node.parent.split:
            # Adjust split ratio
            idx = node.parent.children.index(node)
            if idx == 0:
                node.parent.split_ratio = max(0.2, min(0.8, node.parent.split_ratio + delta * 0.05))
            else:
                node.parent.split_ratio = max(0.2, min(0.8, node.parent.split_ratio - delta * 0.05))
            return True
        return False

    def rename_pane(self, pane_id: str, title: str) -> bool:
        session = self.current_session
        if not session:
            return False
        for pane in session.all_panes:
            if pane.pane_id == pane_id:
                pane.title = title
                return True
        return False

    @property
    def focused_pane(self) -> Optional[Pane]:
        session = self.current_session
        if session:
            return session.focused_pane
        return None

    # ── Sync Mode ─────────────────────────────────────────────────────

    def toggle_sync(self) -> bool:
        self._sync_mode = not self._sync_mode
        if self._sync_mode:
            session = self.current_session
            if session:
                self._sync_panes = [p.pane_id for p in session.all_panes]
        else:
            self._sync_panes.clear()
        return self._sync_mode

    @property
    def sync_mode(self) -> bool:
        return self._sync_mode

    def broadcast_input(self, text: str) -> int:
        """Send input to all synced panes."""
        if not self._sync_mode:
            return 0
        session = self.current_session
        if not session:
            return 0
        count = 0
        for pane in session.all_panes:
            if pane.pane_id in self._sync_panes:
                pane.write(text)
                count += 1
        return count

    # ── Copy/Paste ────────────────────────────────────────────────────

    def copy_pane_history(self, pane_id: str, lines: int = 100) -> str:
        """Copy recent history from a pane."""
        session = self.current_session
        if not session:
            return ""
        for pane in session.all_panes:
            if pane.pane_id == pane_id:
                return "\n".join(pane.history[-lines:])
        return ""

    def paste_to_pane(self, pane_id: str, text: str) -> bool:
        session = self.current_session
        if not session:
            return False
        for pane in session.all_panes:
            if pane.pane_id == pane_id:
                pane.write(text)
                return True
        return False

    # ── Layout Save/Restore ───────────────────────────────────────────

    def save_layout(self) -> str:
        """Save current layout as JSON string."""
        import json
        session = self.current_session
        if not session:
            return "{}"
        layout = {
            "name": session.name,
            "panes": [
                {
                    "id": p.pane_id,
                    "title": p.title,
                }
                for p in session.all_panes
            ],
            "focused": session.focused_pane_id,
            "tree": session.root.to_dict(),
        }
        return json.dumps(layout, indent=2)

    def get_layout_dict(self) -> Dict:
        session = self.current_session
        if not session:
            return {}
        return {
            "name": session.name,
            "pane_count": session.pane_count,
            "focused": session.focused_pane_id,
            "tree": session.root.to_dict(),
        }

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        """Handle keyboard input. Returns action name."""
        if key == "Ctrl+b":
            return "prefix"
        elif key == "Ctrl+b" or key == "prefix_mode":
            return self._handle_prefix_key()

        # Tab management
        elif key == "Ctrl+t":
            self.new_session()
            return "new_session"
        elif key == "Ctrl+w":
            self.close_session()
            return "close_session"
        elif key == "Alt+1":
            self.switch_session(0)
            return "switch_session"
        elif key == "Alt+2":
            self.switch_session(1)
            return "switch_session"
        elif key == "Alt+3":
            self.switch_session(2)
            return "switch_session"
        elif key == "Alt+Left":
            self.prev_session()
            return "prev_session"
        elif key == "Alt+Right":
            self.next_session()
            return "next_session"

        # Pane management
        elif key == "Ctrl+Alt+s":
            self.split_horizontal()
            return "split_horizontal"
        elif key == "Ctrl+Alt+v":
            self.split_vertical()
            return "split_vertical"
        elif key == "Ctrl+Alt+w":
            self.close_pane()
            return "close_pane"
        elif key == "Ctrl+Alt+ArrowRight":
            self.focus_right()
            return "focus_right"
        elif key == "Ctrl+Alt+ArrowLeft":
            self.focus_left()
            return "focus_left"
        elif key == "Ctrl+Alt+ArrowUp":
            self.focus_up()
            return "focus_up"
        elif key == "Ctrl+Alt+ArrowDown":
            self.focus_down()
            return "focus_down"
        elif key == "Ctrl+Alt+n":
            self.focus_next_pane()
            return "focus_next"
        elif key == "Ctrl+Alt+p":
            self.focus_prev_pane()
            return "focus_prev"
        elif key == "Ctrl+Alt+=":
            if self.focused_pane:
                self.resize_pane(self.focused_pane.pane_id, 1)
            return "resize_up"
        elif key == "Ctrl+Alt+-":
            if self.focused_pane:
                self.resize_pane(self.focused_pane.pane_id, -1)
            return "resize_down"
        elif key == "Ctrl+Alt+z":
            self.toggle_sync()
            return "toggle_sync"
        elif key == "Ctrl+Alt+l":
            self.toggle_sync()
            return "layout"

        return None

    def _handle_prefix_key(self) -> Optional[str]:
        """Handle key after Ctrl+B prefix."""
        return "prefix_active"

    # ── Output ────────────────────────────────────────────────────────

    def write_to_pane(self, pane_id: str, text: str) -> None:
        """Write output to a pane."""
        session = self.current_session
        if not session:
            return
        for pane in session.all_panes:
            if pane.pane_id == pane_id:
                pane.write(text)
                return

    def write_to_focused(self, text: str) -> None:
        pane = self.focused_pane
        if pane:
            pane.write(text)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_tab_bar(self, width: int = 80) -> str:
        """Render session tab bar."""
        parts = []
        for i, session in enumerate(self._sessions):
            marker = "▸" if i == self._session_index else " "
            name = session.name[:15]
            panes = session.pane_count
            tab = f"{marker} {name} [{panes}]"
            parts.append(tab)

        return " ".join(parts)[:width]

    def render_status_bar(self, width: int = 80) -> str:
        """Render status bar."""
        session = self.current_session
        if not session:
            return ""

        pane = session.focused_pane
        parts = [
            f" {session.name}",
            f"│ Pane: {pane.display_title}" if pane else "",
            f"│ {session.pane_count} panes",
        ]

        if self._sync_mode:
            parts.append("│ 🔗 Sync ON")

        status = "".join(parts)
        return status[:width].ljust(width)

    def render_pane(self, pane: Pane, width: int = 40, height: int = 20) -> List[str]:
        """Render a single pane."""
        lines = []
        # Title bar
        is_focused = (self.focused_pane and pane.pane_id == self.focused_pane.pane_id)
        title = pane.display_title
        if is_focused:
            lines.append(f" ─ {title} ─" + "─" * max(0, width - len(title) - 6))
        else:
            lines.append(f" ── {title} " + "─" * max(0, width - len(title) - 6))

        # Content
        content = pane.get_visible_lines(height - 2)
        for line in content:
            lines.append(f" {line[:width - 2]}".ljust(width))

        return lines

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        """Render the complete multiplexer UI."""
        lines = []

        # Tab bar
        lines.append(self.render_tab_bar(width))

        # Main area
        session = self.current_session
        if not session:
            lines.append("No sessions")
            return lines

        panes = session.all_panes
        if not panes:
            lines.append("No panes")
            return lines

        # Render focused pane full-width (simplified)
        pane = session.focused_pane
        if pane:
            pane_height = height - 2  # tab bar + status bar
            pane_lines = self.render_pane(pane, width, pane_height)
            lines.extend(pane_lines)

        # Status bar
        lines.append(self.render_status_bar(width))

        return lines

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_split(self, cb: Callable) -> None:
        self._on_split.append(cb)

    def _notify(self, event: str) -> None:
        if event == "split":
            for cb in self._on_split:
                try:
                    cb()
                except Exception:
                    pass
