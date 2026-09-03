"""Terminal Multiplexer — panes, sessions, split layouts for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import os


class SplitDirection(Enum):
    HORIZONTAL = "Horizontal"
    VERTICAL = "Vertical"
    TAB = "Tab"


class PaneState(Enum):
    ACTIVE = "Active"
    IDLE = "Idle"
    RUNNING = "Running"
    ZOMBIE = "Zombie"
    MINIMIZED = "Minimized"


class LayoutPreset(Enum):
    SINGLE = "Single"
    TWO_HORIZONTAL = "Two Horizontal"
    TWO_VERTICAL = "Two Vertical"
    THREE_COLUMNS = "Three Columns"
    THREE_ROWS = "Three Rows"
    FOUR_GRID = "Four Grid"
    MAIN_PLUS_SIDEBAR = "Main + Sidebar"
    TALL_PLUS_WIDE = "Tall + Wide"


@dataclass
class TerminalCommand:
    command: str
    exit_code: int = 0
    timestamp: float = 0.0
    duration_ms: float = 0.0
    output_lines: int = 0


@dataclass
class TerminalHistory:
    commands: List[TerminalCommand] = field(default_factory=list)
    max_size: int = 1000

    def add(self, cmd: str, exit_code: int = 0, duration_ms: float = 0, output_lines: int = 0):
        self.commands.append(TerminalCommand(cmd, exit_code, time.time(), duration_ms, output_lines))
        if len(self.commands) > self.max_size:
            self.commands = self.commands[-self.max_size:]

    @property
    def last_command(self) -> Optional[TerminalCommand]:
        return self.commands[-1] if self.commands else None


@dataclass
class Pane:
    id: int
    title: str = "Terminal"
    working_dir: str = ""
    state: PaneState = PaneState.ACTIVE
    split_from: int = -1
    split_direction: SplitDirection = SplitDirection.HORIZONTAL
    size_ratio: float = 0.5  # 0.0 to 1.0 (ratio of parent space)
    history: TerminalHistory = field(default_factory=TerminalHistory)
    scroll_offset: int = 0
    zoom_level: float = 1.0
    pid: int = 0
    shell: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    font_size: int = 14
    wrap: bool = True
    cursor_blink: bool = True
    _output_buffer: List[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.state == PaneState.ACTIVE

    @property
    def state_icon(self) -> str:
        icons = {
            PaneState.ACTIVE: "🟢",
            PaneState.IDLE: "⚪",
            PaneState.RUNNING: "🔄",
            PaneState.ZOMBIE: "💀",
            PaneState.MINIMIZED: "➖",
        }
        return icons.get(self.state, "?")

    @property
    def dir_display(self) -> str:
        if not self.working_dir:
            return "~"
        parts = self.working_dir.split("/")
        return "~/" + "/".join(parts[-2:]) if len(parts) > 2 else self.working_dir

    @property
    def size_str(self) -> str:
        pct = int(self.size_ratio * 100)
        return f"{pct}%"


@dataclass
class Session:
    id: int
    name: str
    created_at: float = 0.0
    panes: List[Pane] = field(default_factory=list)
    active_pane: int = 0
    layout: LayoutPreset = LayoutPreset.SINGLE
    saved_layout: str = ""
    is_detached: bool = False

    @property
    def pane_count(self) -> int:
        return len(self.panes)

    @property
    def active_pane_obj(self) -> Optional[Pane]:
        if 0 <= self.active_pane < len(self.panes):
            return self.panes[self.active_pane]
        return None

    @property
    def created_str(self) -> str:
        t = time.time() - self.created_at
        if t < 60:
            return f"{t:.0f}s ago"
        elif t < 3600:
            return f"{t / 60:.0f}m ago"
        else:
            return f"{t / 3600:.0f}h ago"


class TermMultiplexer:
    def __init__(self):
        self._sessions: List[Session] = []
        self._active_session: int = 0
        self._pane_counter: int = 0
        self._session_counter: int = 0
        self._view_mode: str = "sessions"
        self._show_status_bar: bool = True
        self._show_clock: bool = True
        self._history: List[str] = []
        self._copy_mode: bool = False
        self._search_pattern: str = ""
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Session 1: Development
        s1 = Session(0, "Development", now - 3600)
        s1.layout = LayoutPreset.TWO_HORIZONTAL
        s1.panes = [
            Pane(0, "Main", "/home/nyrqis/src", PaneState.ACTIVE, size_ratio=0.7,
                 shell="/bin/bash"),
            Pane(1, "Build", "/home/nyrqis/src", PaneState.RUNNING, size_ratio=0.3,
                 shell="/bin/bash"),
        ]
        s1.panes[0].history.add("git pull origin main", 0, 1200, 5)
        s1.panes[0].history.add("cargo build --release", 0, 45000, 120)
        s1.panes[1].history.add("make run", 0, 2000, 10)
        self._sessions.append(s1)

        # Session 2: Server
        s2 = Session(1, "Server Monitor", now - 1800)
        s2.layout = LayoutPreset.THREE_COLUMNS
        s2.panes = [
            Pane(2, "SSH - prod", "/var/log", PaneState.ACTIVE, size_ratio=0.4,
                 shell="/bin/zsh"),
            Pane(3, "htop", "/tmp", PaneState.RUNNING, size_ratio=0.3,
                 shell="/bin/bash"),
            Pane(4, "logs", "/var/log/nginx", PaneState.IDLE, size_ratio=0.3,
                 shell="/bin/bash"),
        ]
        s2.panes[0].history.add("ssh production@10.0.1.50", 0, 500, 1)
        s2.panes[0].history.add("docker ps", 0, 200, 8)
        s2.panes[1].history.add("htop", 0, 100, 0)
        s2.panes[2].history.add("tail -f access.log", 0, 50, 0)
        self._sessions.append(s2)

        # Session 3: Research
        s3 = Session(2, "Research", now - 600)
        s3.layout = LayoutPreset.FOUR_GRID
        s3.panes = [
            Pane(5, "Python", "/home/nyrqis/research", PaneState.ACTIVE, size_ratio=0.25,
                 shell="/bin/bash"),
            Pane(6, "Jupyter", "/home/nyrqis/notebooks", PaneState.IDLE, size_ratio=0.25,
                 shell="/bin/bash"),
            Pane(7, "Docs", "/home/nyrqis/docs", PaneState.IDLE, size_ratio=0.25,
                 shell="/bin/bash"),
            Pane(8, "Git", "/home/nyrqis/research", PaneState.ACTIVE, size_ratio=0.25,
                 shell="/bin/bash"),
        ]
        s3.panes[0].history.add("python3 train.py --epochs=100", 0, 120000, 500)
        self._sessions.append(s3)

        self._pane_counter = 9
        self._session_counter = 3

    @property
    def active_session(self) -> Optional[Session]:
        if 0 <= self._active_session < len(self._sessions):
            return self._sessions[self._active_session]
        return None

    @property
    def total_panes(self) -> int:
        return sum(s.pane_count for s in self._sessions)

    @property
    def total_sessions(self) -> int:
        return len(self._sessions)

    @property
    def active_panes(self) -> int:
        return sum(1 for s in self._sessions for p in s.panes if p.is_active)

    def select_session(self, idx: int):
        if 0 <= idx < len(self._sessions):
            self._active_session = idx

    def new_session(self, name: str = "New Session") -> int:
        self._session_counter += 1
        s = Session(self._session_counter, name, time.time())
        self._pane_counter += 1
        s.panes.append(Pane(self._pane_counter, "Terminal", os.path.expanduser("~")))
        self._sessions.append(s)
        self._history.append(f"Created session: {name}")
        return self._session_counter

    def kill_session(self, idx: int = -1):
        i = idx if idx >= 0 else self._active_session
        if 0 <= i < len(self._sessions) and len(self._sessions) > 1:
            name = self._sessions[i].name
            self._sessions.pop(i)
            self._active_session = min(self._active_session, len(self._sessions) - 1)
            self._history.append(f"Killed session: {name}")

    def split_pane(self, direction: SplitDirection = SplitDirection.HORIZONTAL):
        session = self.active_session
        if not session:
            return
        self._pane_counter += 1
        new_pane = Pane(self._pane_counter, f"Pane {self._pane_counter}",
                        session.active_pane_obj.working_dir if session.active_pane_obj else "",
                        split_direction=direction, size_ratio=0.5)
        # Resize existing pane
        if session.active_pane_obj:
            session.active_pane_obj.size_ratio = 0.5
        session.panes.append(new_pane)
        session.active_pane = len(session.panes) - 1
        self._history.append(f"Split pane ({direction.value})")

    def close_pane(self, pane_idx: int = -1):
        session = self.active_session
        if not session or len(session.panes) <= 1:
            return
        i = pane_idx if pane_idx >= 0 else session.active_pane
        if 0 <= i < len(session.panes):
            name = session.panes[i].title
            session.panes.pop(i)
            session.active_pane = min(session.active_pane, len(session.panes) - 1)
            self._history.append(f"Closed pane: {name}")

    def focus_pane(self, idx: int):
        session = self.active_session
        if session and 0 <= idx < len(session.panes):
            session.active_pane = idx

    def next_pane(self):
        session = self.active_session
        if session and session.pane_count > 1:
            session.active_pane = (session.active_pane + 1) % session.pane_count

    def next_session(self):
        if self._sessions:
            self._active_session = (self._active_session + 1) % len(self._sessions)

    def apply_layout(self, layout: LayoutPreset):
        session = self.active_session
        if not session:
            return
        session.layout = layout

    def send_command(self, command: str):
        session = self.active_session
        if not session:
            return
        pane = session.active_pane_obj
        if pane:
            pane.history.add(command, 0, 0, 0)
            pane.state = PaneState.RUNNING
            # Simulate output
            pane._output_buffer.append(f"$ {command}")
            self._history.append(f"Sent: {command}")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "n":
            self.new_session()
        elif key == "x":
            self.kill_session()
        elif key == "s":
            self.split_pane(SplitDirection.HORIZONTAL)
        elif key == "v":
            self.split_pane(SplitDirection.VERTICAL)
        elif key == "w":
            self.close_pane()
        elif key == "tab":
            self.next_pane()
        elif key == "c":
            self.next_session()
        elif key == "b":
            self._show_status_bar = not self._show_status_bar

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS TERMINAL MULTIPLEXER                              ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Info bar
        lines.append(f"  Sessions: {self.total_sessions}  Panes: {self.total_panes}  Active: {self.active_panes}  Clock: {'ON' if self._show_clock else 'OFF'}")
        lines.append("")

        # Sessions list
        lines.append("  ── Sessions ──")
        for i, s in enumerate(self._sessions):
            sel = "▶" if i == self._active_session else " "
            lines.append(f"  {sel} {s.name}  ({s.pane_count} panes)  {s.created_str}  {s.layout.value}")
        lines.append("")

        # Active session detail
        session = self.active_session
        if session:
            lines.append(f"  ── {session.name} — {session.layout.value} ──")
            for i, pane in enumerate(session.panes):
                sel = "▶" if i == session.active_pane else " "
                lock = "🔒" if pane.state == PaneState.MINIMIZED else ""
                lines.append(f"  {sel} {pane.state_icon} {pane.title}  {pane.dir_display}  [{pane.size_str}] {lock}")

                # Show last commands
                if pane.history.commands:
                    for cmd in pane.history.commands[-3:]:
                        status = "✅" if cmd.exit_code == 0 else "❌"
                        lines.append(f"      {status} {cmd.command[:50]}")
                lines.append("")

            # Status bar
            if self._show_status_bar:
                pane = session.active_pane_obj
                if pane:
                    lines.append(f"  ── Status Bar ──")
                    lines.append(f"  🟢 {pane.title}  {pane.dir_display}  {pane.shell}  {pane.history.commands.__len__} commands  Font: {pane.font_size}px")
                lines.append("")

        lines.append("  [N]ew Session [X]Kill [S]plit Horiz [V]Split Vert [W]Close Pane")
        lines.append("  [Tab]Next Pane [C]Next Session [B]Status Bar")
        return lines
