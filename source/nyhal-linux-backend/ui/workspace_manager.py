"""
Nyrqis Workspace Manager — virtual desktop management application.

Features:
- Virtual desktop creation and management
- Window tiling presets (monocle, columns, rows, grid, floating)
- Per-workspace wallpaper and name
- Workspace switching with animations
- Window placement rules
- Workspace thumbnails
- Multi-monitor workspace support
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class TilingMode(Enum):
    FLOATING = "floating"
    MONOCLE = "monocle"
    COLUMNS = "columns"
    ROWS = "rows"
    GRID = "grid"
    BSP = "bsp"
    CENTERED = "centered"
    MAIN_STACK = "main-stack"


class WindowState(Enum):
    NORMAL = "normal"
    MAXIMIZED = "maximized"
    MINIMIZED = "minimized"
    FULLSCREEN = "fullscreen"
    TILED = "tiled"
    STICKY = "sticky"


class MonitorRole(Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"


TILING_ICONS = {
    TilingMode.FLOATING: "🪟",
    TilingMode.MONOCLE: "⬜",
    TilingMode.COLUMNS: "▮▮",
    TilingMode.ROWS: "═",
    TilingMode.GRID: "⊞",
    TilingMode.BSP: "⊡",
    TilingMode.CENTERED: "⊞",
    TilingMode.MAIN_STACK: "▮═",
}

STATE_ICONS = {
    WindowState.NORMAL: "",
    WindowState.MAXIMIZED: "⬜",
    WindowState.MINIMIZED: "➖",
    WindowState.FULLSCREEN: "🖥️",
    WindowState.TILED: "🔲",
    WindowState.STICKY: "📌",
}


@dataclass
class WorkspaceWindow:
    """A window in a workspace."""
    title: str
    app: str = ""
    instance: str = ""
    state: WindowState = WindowState.NORMAL
    x: int = 0
    y: int = 0
    width: int = 800
    height: int = 600
    floating: bool = False
    pinned: bool = False
    monitor: int = 0
    focused: bool = False
    window_id: str = ""

    def __post_init__(self):
        if not self.window_id:
            self.window_id = hashlib.md5(f"{self.title}{time.time()}".encode()).hexdigest()[:8]

    @property
    def display(self) -> str:
        state = STATE_ICONS.get(self.state, "")
        focused = "◆" if self.focused else "◇"
        return f"{focused} {state}{self.title}"

    @property
    def app_display(self) -> str:
        return f"{self.app}" if self.app else self.instance

    @property
    def size_str(self) -> str:
        return f"{self.width}×{self.height}"

    @property
    def position_str(self) -> str:
        return f"{self.x},{self.y}"


@dataclass
class Workspace:
    """A virtual desktop/workspace."""
    name: str
    number: int
    tiling_mode: TilingMode = TilingMode.FLOATING
    wallpaper: str = ""
    windows: List[WorkspaceWindow] = field(default_factory=list)
    # Layout
    main_ratio: float = 0.55  # ratio for main area in BSP/main-stack
    gaps: int = 6  # pixel gaps between windows
    # Rules
    default_app: str = ""  # auto-launch app
    # Metadata
    workspace_id: str = ""
    created: float = field(default_factory=time.time)
    last_active: float = 0.0
    monitor: int = 0

    def __post_init__(self):
        if not self.workspace_id:
            self.workspace_id = hashlib.md5(f"ws{self.number}".encode()).hexdigest()[:8]

    @property
    def display(self) -> str:
        tiling_icon = TILING_ICONS.get(self.tiling_mode, "🪟")
        return f" {tiling_icon} {self.name} ({len(self.windows)} windows)"

    @property
    def window_count(self) -> int:
        return len(self.windows)

    @property
    def focused_window(self) -> Optional[WorkspaceWindow]:
        for w in self.windows:
            if w.focused:
                return w
        return self.windows[0] if self.windows else None

    @property
    def focused_title(self) -> str:
        fw = self.focused_window
        return fw.title if fw else "—"


@dataclass
class TilingPreset:
    """A saved tiling layout preset."""
    name: str
    tiling_mode: TilingMode
    main_ratio: float = 0.55
    gaps: int = 6
    description: str = ""
    icon: str = ""

    @property
    def display(self) -> str:
        icon = self.icon or TILING_ICONS.get(self.tiling_mode, "🪟")
        return f"{icon} {self.name} ({self.tiling_mode.value})"


@dataclass
class Monitor:
    """A display monitor."""
    name: str
    role: MonitorRole = MonitorRole.PRIMARY
    width: int = 2560
    height: int = 1440
    refresh_rate: int = 144
    workspaces: List[int] = field(default_factory=list)  # workspace numbers
    active_workspace: int = 1
    x_offset: int = 0
    y_offset: int = 0

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}@{self.refresh_rate}Hz"

    @property
    def display(self) -> str:
        role = f" [{self.role.value}]" if self.role != MonitorRole.PRIMARY else ""
        return f"🖥️ {self.name}{role} — {self.resolution}"


# ─── Workspace Manager ───────────────────────────────────────────────────


class WorkspaceManager:
    """
    Virtual desktop and workspace manager for Nyrqis OS.
    """

    def __init__(self):
        self._workspaces: List[Workspace] = []
        self._presets: List[TilingPreset] = []
        self._monitors: List[Monitor] = []
        self._selected_ws: int = 0
        self._selected_window: int = 0
        self._view_mode: str = "workspaces"  # workspaces, windows, presets, monitors, rules
        self._active_workspace: int = 0

        self._init_presets()
        self._init_sample_data()

    def _init_presets(self) -> None:
        self._presets = [
            TilingPreset("Floating", TilingMode.FLOATING, 0.55, 6, "Free-form window placement"),
            TilingPreset("Monocle", TilingMode.MONOCLE, 1.0, 0, "One window full screen"),
            TilingPreset("Columns", TilingMode.COLUMNS, 0.5, 6, "Vertical columns"),
            TilingPreset("Rows", TilingMode.ROWS, 0.5, 6, "Horizontal rows"),
            TilingPreset("Grid", TilingMode.GRID, 0.5, 6, "Automatic grid layout"),
            TilingPreset("BSP", TilingMode.BSP, 0.55, 6, "Binary space partitioning"),
            TilingPreset("Centered", TilingMode.CENTERED, 0.6, 8, "Centered main window"),
            TilingPreset("Main+Stack", TilingMode.MAIN_STACK, 0.65, 6, "Main area + stack"),
        ]

    def _init_sample_data(self) -> None:
        now = time.time()

        # Workspaces
        self._workspaces = [
            Workspace("Main", 1, TilingMode.MAIN_STACK, wallpaper="gradient-blue",
                      gaps=6, monitor=0, last_active=now,
                      windows=[
                          WorkspaceWindow("Terminal — bash", "Nyrqis Terminal", "bash",
                                          WindowState.TILED, 0, 0, 1600, 1440, monitor=0, focused=True),
                          WorkspaceWindow("Firefox — Nyrqis Docs", "Firefox", "",
                                          WindowState.TILED, 1600, 0, 960, 1440, monitor=0),
                      ]),
            Workspace("Development", 2, TilingMode.BSP, wallpaper="gradient-purple",
                      gaps=6, monitor=0, last_active=now - 600,
                      windows=[
                          WorkspaceWindow("VS Code — nyhal-linux-backend", "Code", "",
                                          WindowState.TILED, 0, 0, 2560, 1440, monitor=0, focused=True),
                          WorkspaceWindow("Terminal — cargo test", "Nyrqis Terminal", "bash",
                                          WindowState.TILED, 0, 0, 1280, 720, monitor=0),
                      ]),
            Workspace("Communication", 3, TilingMode.FLOATING, wallpaper="gradient-green",
                      gaps=8, monitor=0, last_active=now - 3600,
                      windows=[
                          WorkspaceWindow("Discord — Nyrqis", "Discord", "",
                                          WindowState.NORMAL, 200, 100, 1200, 800, floating=True, monitor=0, focused=True),
                          WorkspaceWindow("Telegram Desktop", "Telegram", "",
                                          WindowState.NORMAL, 800, 200, 1000, 700, floating=True, monitor=0),
                      ]),
            Workspace("Media", 4, TilingMode.MONOCLE, wallpaper="gradient-orange",
                      gaps=0, monitor=0, last_active=now - 7200,
                      windows=[
                          WorkspaceWindow("Spotify", "Spotify", "",
                                          WindowState.FULLSCREEN, 0, 0, 2560, 1440, monitor=0, focused=True),
                      ]),
            Workspace("System", 5, TilingMode.FLOATING, wallpaper="gradient-red",
                      gaps=8, monitor=0, last_active=now - 86400,
                      windows=[
                          WorkspaceWindow("System Monitor", "Nyrqis Monitor", "",
                                          WindowState.NORMAL, 100, 100, 1400, 900, floating=True, monitor=0, focused=True),
                          WorkspaceWindow("Disk Manager", "Nyrqis Disk", "",
                                          WindowState.NORMAL, 400, 200, 1200, 700, floating=True, monitor=0),
                      ]),
            Workspace("Scratch", 6, TilingMode.FLOATING, wallpaper="",
                      gaps=10, monitor=0, last_active=now - 172800, windows=[]),
        ]
        self._active_workspace = 0

        # Monitors
        self._monitors = [
            Monitor("LG 27GP850", MonitorRole.PRIMARY, 2560, 1440, 144,
                    [1, 2, 3, 4, 5, 6], 1, 0, 0),
            Monitor("Dell U2419H", MonitorRole.SECONDARY, 1920, 1080, 60,
                    [7, 8], 7, 2560, 0),
        ]

    # ── Workspace Operations ──────────────────────────────────────────

    def create_workspace(self, name: str = "", tiling: TilingMode = TilingMode.FLOATING) -> Workspace:
        num = max(ws.number for ws in self._workspaces) + 1 if self._workspaces else 1
        ws = Workspace(
            name=name or f"Workspace {num}",
            number=num,
            tiling_mode=tiling,
        )
        self._workspaces.append(ws)
        return ws

    def delete_workspace(self, index: int) -> bool:
        if 0 <= index < len(self._workspaces):
            ws = self._workspaces[index]
            if len(self._workspaces) > 1 and not ws.windows:
                self._workspaces.pop(index)
                self._selected_ws = min(self._selected_ws, len(self._workspaces) - 1)
                return True
        return False

    def rename_workspace(self, index: int, name: str) -> bool:
        if 0 <= index < len(self._workspaces):
            self._workspaces[index].name = name
            return True
        return False

    def set_tiling(self, ws_index: int, mode: TilingMode) -> bool:
        if 0 <= ws_index < len(self._workspaces):
            self._workspaces[ws_index].tiling_mode = mode
            return True
        return False

    def switch_workspace(self, index: int) -> bool:
        if 0 <= index < len(self._workspaces):
            self._workspaces[self._active_workspace].last_active = time.time()
            self._active_workspace = index
            self._workspaces[index].last_active = time.time()
            return True
        return False

    def apply_preset(self, preset_idx: int) -> bool:
        if 0 <= preset_idx < len(self._presets):
            preset = self._presets[preset_idx]
            if 0 <= self._active_workspace < len(self._workspaces):
                ws = self._workspaces[self._active_workspace]
                ws.tiling_mode = preset.tiling_mode
                ws.main_ratio = preset.main_ratio
                ws.gaps = preset.gaps
                return True
        return False

    # ── Window Operations ─────────────────────────────────────────────

    def add_window(self, ws_index: int, title: str, app: str = "") -> Optional[WorkspaceWindow]:
        if 0 <= ws_index < len(self._workspaces):
            win = WorkspaceWindow(title=title, app=app)
            self._workspaces[ws_index].windows.append(win)
            return win
        return None

    def remove_window(self, ws_index: int, win_index: int) -> bool:
        if 0 <= ws_index < len(self._workspaces):
            ws = self._workspaces[ws_index]
            if 0 <= win_index < len(ws.windows):
                ws.windows.pop(win_index)
                return True
        return False

    def focus_window(self, ws_index: int, win_index: int) -> bool:
        if 0 <= ws_index < len(self._workspaces):
            ws = self._workspaces[ws_index]
            for i, w in enumerate(ws.windows):
                w.focused = (i == win_index)
            return True
        return False

    def move_window_to_workspace(self, from_ws: int, win_idx: int, to_ws: int) -> bool:
        if (0 <= from_ws < len(self._workspaces) and
                0 <= to_ws < len(self._workspaces)):
            ws_from = self._workspaces[from_ws]
            ws_to = self._workspaces[to_ws]
            if 0 <= win_idx < len(ws_from.windows):
                win = ws_from.windows.pop(win_idx)
                win.workspace = to_ws
                ws_to.windows.append(win)
                return True
        return False

    # ── Navigation ────────────────────────────────────────────────────

    def select_ws_up(self) -> None:
        self._selected_ws = max(0, self._selected_ws - 1)

    def select_ws_down(self) -> None:
        self._selected_ws = min(len(self._workspaces) - 1, self._selected_ws + 1)

    def select_win_up(self) -> None:
        self._selected_window = max(0, self._selected_window - 1)

    def select_win_down(self) -> None:
        ws = self.get_selected_workspace()
        if ws:
            self._selected_window = min(len(ws.windows) - 1, self._selected_window + 1)

    def get_selected_workspace(self) -> Optional[Workspace]:
        if 0 <= self._selected_ws < len(self._workspaces):
            return self._workspaces[self._selected_ws]
        return None

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_window = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def workspaces(self) -> List[Workspace]:
        return list(self._workspaces)

    @property
    def presets(self) -> List[TilingPreset]:
        return list(self._presets)

    @property
    def monitors(self) -> List[Monitor]:
        return list(self._monitors)

    @property
    def selected_ws(self) -> int:
        return self._selected_ws

    @property
    def active_workspace(self) -> int:
        return self._active_workspace

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def total_windows(self) -> int:
        return sum(ws.window_count for ws in self._workspaces)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_workspaces(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 🖥️  Workspace Manager")
        lines.append("─" * width)
        lines.append(f" Active: {self._workspaces[self._active_workspace].name if self._workspaces else '—'} | {self.total_windows} windows total")
        lines.append("─" * width)

        for i, ws in enumerate(self._workspaces):
            marker = "▸" if i == self._selected_ws else " "
            active = " 🟢" if i == self._active_workspace else ""
            lines.append(f"{marker}{ws.display}{active}")

            # Window thumbnails (simplified)
            if ws.windows:
                for win in ws.windows[:4]:
                    focused = " ◆" if win.focused else ""
                    lines.append(f"    {win.display}{focused}")
                    lines.append(f"      {win.app_display} · {win.size_str}")
            else:
                lines.append("    (empty)")

            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Switch  W:Windows  P:Presets")
        lines.append(" M:Monitors  N:New workspace  Del:Delete  Esc:Back")
        return lines

    def render_windows(self, width: int = 70) -> List[str]:
        ws = self.get_selected_workspace()
        if not ws:
            return ["No workspace selected"]

        lines = []
        tiling_icon = TILING_ICONS.get(ws.tiling_mode, "🪟")
        lines.append(f" 🪟  {ws.name} — {tiling_icon} {ws.tiling_mode.value}")
        lines.append("─" * width)
        lines.append(f" Gaps: {ws.gaps}px | Main ratio: {ws.main_ratio:.0%}")
        lines.append("─" * width)

        if not ws.windows:
            lines.append("  No windows in this workspace.")
        else:
            for i, win in enumerate(ws.windows):
                marker = "▸" if i == self._selected_window else " "
                lines.append(f"{marker} {win.display}")
                lines.append(f"   App: {win.app_display} | Size: {win.size_str} | Pos: {win.position_str}")
                lines.append(f"   {'Floating' if win.floating else 'Tiled'} | Monitor: {win.monitor}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Del:Close  F:Float  M:Move to WS")
        lines.append(" T:Change tiling  Esc:Back")
        return lines

    def render_presets(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📐 Tiling Presets")
        lines.append("─" * width)

        for i, preset in enumerate(self._presets):
            marker = "▸" if i == self._selected_window else " "
            lines.append(f"{marker} {preset.display}")
            lines.append(f"   Ratio: {preset.main_ratio:.0%} | Gaps: {preset.gaps}px")
            if preset.description:
                lines.append(f"   {preset.description}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Apply to active workspace  Esc:Back")
        return lines

    def render_monitors(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 🖥️  Monitors")
        lines.append("─" * width)

        for monitor in self._monitors:
            lines.append(f" {monitor.display}")
            ws_names = []
            for ws in self._workspaces:
                if ws.number in monitor.workspaces:
                    ws_names.append(f"WS{ws.number}:{ws.name}")
            lines.append(f"   Workspaces: {', '.join(ws_names)}")
            lines.append(f"   Active: WS{monitor.active_workspace}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "windows": self.render_windows,
            "presets": self.render_presets,
            "monitors": self.render_monitors,
        }
        renderer = renderers.get(self._view_mode, self.render_workspaces)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "windows":
            return self._handle_windows_key(key)
        elif self._view_mode == "presets":
            return self._handle_presets_key(key)
        elif self._view_mode == "monitors":
            return self._handle_monitors_key(key)
        return self._handle_workspaces_key(key)

    def _handle_workspaces_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_ws_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_ws_down()
            return "select_down"
        elif key == "Enter":
            return "switch" if self.switch_workspace(self._selected_ws) else "switch_failed"
        elif key == "w":
            self.set_view("windows")
            return "windows"
        elif key == "p":
            self.set_view("presets")
            return "presets"
        elif key == "m":
            self.set_view("monitors")
            return "monitors"
        elif key == "n":
            self.create_workspace()
            return "new_workspace"
        elif key == "Delete":
            return "delete" if self.delete_workspace(self._selected_ws) else "delete_failed"
        return None

    def _handle_windows_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("workspaces")
            return "back"
        elif key == "ArrowUp":
            self.select_win_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_win_down()
            return "select_down"
        elif key == "Delete":
            return "close_window" if self.remove_window(self._selected_ws, self._selected_window) else "close_failed"
        elif key == "f":
            ws = self.get_selected_workspace()
            if ws and 0 <= self._selected_window < len(ws.windows):
                win = ws.windows[self._selected_window]
                win.floating = not win.floating
                return "toggle_float"
        return None

    def _handle_presets_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("workspaces")
            return "back"
        elif key == "ArrowUp":
            self.select_win_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_win_down()
            return "select_down"
        elif key == "Enter":
            return "apply_preset" if self.apply_preset(self._selected_window) else "apply_failed"
        return None

    def _handle_monitors_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("workspaces")
            return "back"
        return None
