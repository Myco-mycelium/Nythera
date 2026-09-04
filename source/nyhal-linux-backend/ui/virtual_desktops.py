"""
Nyrqis OS - Virtual Desktop Manager
Workspace switching, window tiling, and hot corners.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class TileMode(Enum):
    FLOATING = "floating"
    TILED = "tiled"
    MONOCLE = "monocle"
    GRID = "grid"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    CENTERED = "centered"


class TileDirection(Enum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"
    FULL = "full"
    CENTER = "center"


class WindowState(Enum):
    NORMAL = "normal"
    MAXIMIZED = "maximized"
    MINIMIZED = "minimized"
    FULLSCREEN = "fullscreen"
    TILED = "tiled"
    FLOATING = "floating"


class HotCornerAction(Enum):
    SHOW_DESKTOP = "show_desktop"
    SHOW_LAUNCHER = "show_launcher"
    SHOW_OVERVIEW = "show_overview"
    SHOW_NOTIFICATIONS = "show_notifications"
    SHOW_QUICK_SETTINGS = "show_quick_settings"
    SWITCH_WINDOW = "switch_window"
    TILE_WINDOW = "tile_window"
    NOTHING = "nothing"


@dataclass
class Window:
    pid: int = 0
    title: str = ""
    app_name: str = ""
    icon: str = ""
    state: WindowState = WindowState.NORMAL
    x: int = 0
    y: int = 0
    width: int = 800
    height: int = 600
    workspace: int = 0
    is_focused: bool = False
    is_floating: bool = False
    is_sticky: bool = False
    opacity: float = 1.0
    desktop: int = 0

    @property
    def state_icon(self) -> str:
        icons = {
            WindowState.NORMAL: "🪟", WindowState.MAXIMIZED: "⬜",
            WindowState.MINIMIZED: "➖", WindowState.FULLSCREEN: "🔳",
            WindowState.TILED: "🔲", WindowState.FLOATING: "📎",
        }
        return icons.get(self.state, "?")

    @property
    def position(self) -> str:
        return f"({self.x},{self.y}) {self.width}×{self.height}"


@dataclass
class Workspace:
    id: int = 0
    name: str = ""
    icon: str = ""
    windows: List[Window] = field(default_factory=list)
    tile_mode: TileMode = TileMode.FLOATING
    wallpaper: str = ""
    is_visible: bool = False
    is_active: bool = False
    gap: int = 10

    @property
    def window_count(self) -> int:
        return len(self.windows)

    @property
    def has_focused(self) -> bool:
        return any(w.is_focused for w in self.windows)


@dataclass
class HotCorner:
    position: str = ""  # top-left, top-right, bottom-left, bottom-right
    action: HotCornerAction = HotCornerAction.NOTHING
    enabled: bool = True
    delay_ms: int = 300

    @property
    def action_icon(self) -> str:
        icons = {
            HotCornerAction.SHOW_DESKTOP: "🖥️",
            HotCornerAction.SHOW_LAUNCHER: "🚀",
            HotCornerAction.SHOW_OVERVIEW: "👁️",
            HotCornerAction.SHOW_NOTIFICATIONS: "🔔",
            HotCornerAction.SHOW_QUICK_SETTINGS: "⚙️",
            HotCornerAction.SWITCH_WINDOW: "🔄",
            HotCornerAction.TILE_WINDOW: "🔲",
            HotCornerAction.NOTHING: "⬜",
        }
        return icons.get(self.action, "?")


@dataclass
class TileLayout:
    name: str
    mode: TileMode = TileMode.TILED
    master_ratio: float = 0.55
    gap: int = 10
    borders: bool = True
    border_width: int = 2
    border_focused: str = "#e94560"
    border_unfocused: str = "#16213e"

    @property
    def description(self) -> str:
        descs = {
            TileMode.FLOATING: "No tiling, windows float freely",
            TileMode.TILED: "Master-stack tiling layout",
            TileMode.MONOCLE: "One window at a time, fullscreen",
            TileMode.GRID: "Automatic grid layout",
            TileMode.HORIZONTAL: "Horizontal split layout",
            TileMode.VERTICAL: "Vertical split layout",
            TileMode.CENTERED: "Centered master layout",
        }
        return descs.get(self.mode, "")


class VirtualDesktopManager:
    def __init__(self):
        self.workspaces: List[Workspace] = []
        self.hot_corners: List[HotCorner] = []
        self.layouts: List[TileLayout] = []
        self.current_workspace: Optional[Workspace] = None
        self.current_layout: Optional[TileLayout] = None
        self.window_count: int = 0
        self.showing_overview: bool = False
        self.showing_desktop: bool = False
        self._create_sample_data()

    def _create_sample_data(self):
        windows = [
            Window(pid=2, title="Nyrqis Compositor", app_name="nyrqis-compositor",
                   icon="🍄", state=WindowState.TILED, x=0, y=0,
                   width=1920, height=1080, workspace=0, desktop=0),
            Window(pid=3, title="Nyrqis Shell", app_name="nyrqis-shell",
                   icon="🐚", state=WindowState.TILED, x=960, y=0,
                   width=960, height=1080, workspace=0, desktop=0),
            Window(pid=200, title="Firefox - Nyrqis Wiki", app_name="firefox",
                   icon="🦊", state=WindowState.TILED, x=0, y=0,
                   width=1440, height=1080, workspace=1, desktop=1),
            Window(pid=300, title="Code - Nyrqis Backend", app_name="code",
                   icon="📝", state=WindowState.TILED, x=1440, y=0,
                   width=480, height=1080, workspace=1, desktop=1),
            Window(pid=301, title="Terminal", app_name="terminal",
                   icon="⬛", state=WindowState.TILED, x=0, y=540,
                   width=1920, height=540, workspace=2, desktop=2,
                   is_focused=True),
            Window(pid=400, title="PulseAudio Volume Control", app_name="pavucontrol",
                   icon="🔊", state=WindowState.FLOATING, x=500, y=300,
                   width=600, height=400, workspace=2, desktop=2),
            Window(pid=500, title="System Monitor", app_name="gnome-system-monitor",
                   icon="📊", state=WindowState.TILED, x=0, y=0,
                   width=1920, height=1080, workspace=3, desktop=3),
            Window(pid=600, title="Settings", app_name="gnome-settings",
                   icon="⚙️", state=WindowState.FLOATING, x=200, y=100,
                   width=800, height=600, workspace=3, desktop=3),
        ]
        self.window_count = len(windows)

        self.workspaces = [
            Workspace(id=0, name="System", icon="🍄", windows=[w for w in windows if w.desktop == 0],
                      tile_mode=TileMode.TILED, is_active=True, is_visible=True,
                      wallpaper="nyrqis-default-dark"),
            Workspace(id=1, name="Web & Code", icon="💻", windows=[w for w in windows if w.desktop == 1],
                      tile_mode=TileMode.TILED, gap=8,
                      wallpaper="nyrqis-gradient-blue"),
            Workspace(id=2, name="Terminal", icon="⬛", windows=[w for w in windows if w.desktop == 2],
                      tile_mode=TileMode.TILED, gap=12,
                      wallpaper="nyrqis-matrix"),
            Workspace(id=3, name="Monitoring", icon="📊", windows=[w for w in windows if w.desktop == 3],
                      tile_mode=TileMode.FLOATING,
                      wallpaper="nyrqis-gradient-purple"),
        ]
        self.current_workspace = self.workspaces[0]

        self.hot_corners = [
            HotCorner(position="top-left", action=HotCornerAction.SHOW_OVERVIEW,
                      enabled=True, delay_ms=300),
            HotCorner(position="top-right", action=HotCornerAction.SHOW_NOTIFICATIONS,
                      enabled=True, delay_ms=250),
            HotCorner(position="bottom-left", action=HotCornerAction.SHOW_LAUNCHER,
                      enabled=True, delay_ms=400),
            HotCorner(position="bottom-right", action=HotCornerAction.SHOW_QUICK_SETTINGS,
                      enabled=True, delay_ms=350),
        ]

        self.layouts = [
            TileLayout(name="Tiled", mode=TileMode.TILED, master_ratio=0.55,
                        gap=10, border_focused="#e94560", border_unfocused="#16213e"),
            TileLayout(name="Monocle", mode=TileMode.MONOCLE,
                        gap=0, borders=False),
            TileLayout(name="Grid", mode=TileMode.GRID, gap=12,
                        border_focused="#4fc3f7"),
            TileLayout(name="Centered", mode=TileMode.CENTERED, master_ratio=0.5,
                        gap=10, border_focused="#6bcb77"),
            TileLayout(name="Horizontal", mode=TileMode.HORIZONTAL, gap=8),
            TileLayout(name="Vertical", mode=TileMode.VERTICAL, gap=8),
            TileLayout(name="Floating", mode=TileMode.FLOATING, borders=False),
        ]
        self.current_layout = self.layouts[0]

    def switch_workspace(self, workspace_id: int) -> bool:
        ws = next((w for w in self.workspaces if w.id == workspace_id), None)
        if ws:
            for w in self.workspaces:
                w.is_active = False
                w.is_visible = False
            ws.is_active = True
            ws.is_visible = True
            self.current_workspace = ws
            return True
        return False

    def move_window_to_workspace(self, window_pid: int, workspace_id: int) -> bool:
        ws = next((w for w in self.workspaces if w.id == workspace_id), None)
        if not ws:
            return False
        for w in self.workspaces:
            win = next((win for win in w.windows if win.pid == window_pid), None)
            if win:
                w.windows.remove(win)
                win.workspace = workspace_id
                win.desktop = workspace_id
                ws.windows.append(win)
                return True
        return False

    def focus_window(self, pid: int) -> bool:
        for ws in self.workspaces:
            for win in ws.windows:
                win.is_focused = (win.pid == pid)
        return True

    def tile_window(self, pid: int, direction: TileDirection = TileDirection.FULL) -> bool:
        for ws in self.workspaces:
            win = next((w for w in ws.windows if w.pid == pid), None)
            if win:
                win.state = WindowState.TILED
                win.is_floating = False
                return True
        return False

    def float_window(self, pid: int) -> bool:
        for ws in self.workspaces:
            win = next((w for w in ws.windows if w.pid == pid), None)
            if win:
                win.state = WindowState.FLOATING
                win.is_floating = True
                return True
        return False

    def minimize_window(self, pid: int) -> bool:
        for ws in self.workspaces:
            win = next((w for w in ws.windows if w.pid == pid), None)
            if win:
                win.state = WindowState.MINIMIZED
                return True
        return False

    def maximize_window(self, pid: int) -> bool:
        for ws in self.workspaces:
            win = next((w for w in ws.windows if w.pid == pid), None)
            if win:
                win.state = WindowState.MAXIMIZED
                return True
        return False

    def close_window(self, pid: int) -> bool:
        for ws in self.workspaces:
            for i, win in enumerate(ws.windows):
                if win.pid == pid:
                    del ws.windows[i]
                    self.window_count -= 1
                    return True
        return False

    def cycle_layout(self) -> TileLayout:
        if not self.current_layout:
            return self.layouts[0]
        idx = self.layouts.index(self.current_layout)
        self.current_layout = self.layouts[(idx + 1) % len(self.layouts)]
        return self.current_layout

    def set_layout(self, name: str) -> bool:
        layout = next((l for l in self.layouts if l.name == name), None)
        if layout:
            self.current_layout = layout
            return True
        return False

    def get_windows_on_workspace(self, workspace_id: int) -> List[Window]:
        ws = next((w for w in self.workspaces if w.id == workspace_id), None)
        return ws.windows if ws else []

    def get_all_windows(self) -> List[Window]:
        windows = []
        for ws in self.workspaces:
            windows.extend(ws.windows)
        return windows

    def get_hot_corner(self, position: str) -> Optional[HotCorner]:
        return next((h for h in self.hot_corners if h.position == position), None)

    def trigger_hot_corner(self, position: str) -> HotCornerAction:
        corner = self.get_hot_corner(position)
        if corner and corner.enabled:
            return corner.action
        return HotCornerAction.NOTHING

    def get_stats(self) -> Dict:
        return {
            "workspaces": len(self.workspaces),
            "windows": self.window_count,
            "hot_corners": len(self.hot_corners),
            "layouts": len(self.layouts),
            "active_workspace": self.current_workspace.id if self.current_workspace else -1,
            "active_layout": self.current_layout.name if self.current_layout else "None",
        }
