"""
Nyrqis OS - Dock / Taskbar Customizer
App pins, widgets, behavior settings, and layout configuration.

Features:
- Dock position (bottom, left, right, top)
- Dock appearance (size, opacity, icon size, spacing)
- Pinned apps with custom order
- Running app indicators
- Dock widgets (clock, battery, volume, network)
- Taskbar modes (dock, panel, both)
- Auto-hide behavior with sensitivity
- Click actions (minimize, preview, focus)
- Notification badges and indicators
- Multiple monitor dock placement
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class DockPosition(Enum):
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"


class DockTheme(Enum):
    LIGHT = "light"
    DARK = "dark"
    TRANSPARENT = "transparent"
    BLUR = "blur"
    ACRYLIC = "acrylic"


class ClickAction(Enum):
    MINIMIZE_TOGGLE = "minimize_toggle"
    SHOW_PREVIEW = "show_preview"
    FOCUS_ONLY = "focus_only"
    NEW_INSTANCE = "new_instance"


class IndicatorStyle(Enum):
    DOT = "dot"
    LINE = "line"
    SQUARE = "square"
    RING = "ring"
    GLOW = "glow"


class AutoHideMode(Enum):
    NEVER = "never"
    ALWAYS = "always"
    MAXIMIZED = "when_maximized"


class WidgetType(Enum):
    CLOCK = "clock"
    BATTERY = "battery"
    VOLUME = "volume"
    NETWORK = "network"
    CALENDAR = "calendar"
    WEATHER = "weather"
    CPU = "cpu"
    NOTIFICATIONS = "notifications"
    SEARCH = "search"
    SHOW_APPS = "show_apps"
    TRASH = "trash"
    SPACER = "spacer"


POSITION_ICONS = {
    DockPosition.BOTTOM: "⬇️", DockPosition.LEFT: "⬅️",
    DockPosition.RIGHT: "➡️", DockPosition.TOP: "⬆️",
}

WIDGET_ICONS = {
    WidgetType.CLOCK: "🕐", WidgetType.BATTERY: "🔋",
    WidgetType.VOLUME: "🔊", WidgetType.NETWORK: "🌐",
    WidgetType.CALENDAR: "📅", WidgetType.WEATHER: "🌤️",
    WidgetType.CPU: "📊", WidgetType.NOTIFICATIONS: "🔔",
    WidgetType.SEARCH: "🔍", WidgetType.SHOW_APPS: "📱",
    WidgetType.TRASH: "🗑️", WidgetType.SPACER: "⬜",
}


@dataclass
class DockApp:
    name: str = ""
    app_path: str = ""
    icon: str = ""
    pinned: bool = True
    running: bool = False
    notification_count: int = 0
    favorite: bool = False
    category: str = ""
    last_launched: float = 0.0
    launch_count: int = 0
    custom_label: str = ""

    @property
    def display_name(self) -> str:
        return self.custom_label if self.custom_label else self.name

    @property
    def running_indicator(self) -> str:
        return "●" if self.running else ""

    @property
    def badge(self) -> str:
        if self.notification_count > 0:
            return f"({self.notification_count})" if self.notification_count < 100 else "(99+)"
        return ""

    @property
    def icon_display(self) -> str:
        return self.icon if self.icon else "📦"

    @property
    def last_launched_str(self) -> str:
        if self.last_launched == 0:
            return "Never"
        delta = time.time() - self.last_launched
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h ago"
        return f"{delta / 86400:.0f}d ago"

    @property
    def detail(self) -> str:
        parts = []
        if self.pinned:
            parts.append("📌")
        if self.running:
            parts.append("🟢")
        if self.notification_count > 0:
            parts.append(f"🔔 {self.notification_count}")
        return " ".join(parts) if parts else self.category


@dataclass
class DockWidget:
    widget_type: WidgetType = WidgetType.CLOCK
    enabled: bool = True
    size: int = 32
    show_label: bool = False
    custom_format: str = ""

    @property
    def icon(self) -> str:
        return WIDGET_ICONS.get(self.widget_type, "❓")

    @property
    def display(self) -> str:
        label = f" ({self.widget_type.value})" if self.show_label else ""
        return f"{self.icon}{label}"


@dataclass
class DockConfig:
    # Position and size
    position: DockPosition = DockPosition.BOTTOM
    dock_size: int = 48  # pixels
    icon_size: int = 36  # pixels
    spacing: int = 6  # pixels between icons
    padding: int = 8  # edge padding
    corner_radius: int = 12

    # Appearance
    theme: DockTheme = DockTheme.BLUR
    opacity: float = 0.85
    bg_color: str = "rgba(30, 30, 30, 0.85)"
    border_color: str = "rgba(255, 255, 255, 0.1)"
    border_width: int = 1
    shadow: bool = True
    animation: bool = True

    # Behavior
    auto_hide: AutoHideMode = AutoHideMode.NEVER
    auto_hide_delay_ms: int = 300
    click_action: ClickAction = ClickAction.MINIMIZE_TOGGLE
    indicator_style: IndicatorStyle = IndicatorStyle.DOT
    show_notifications: bool = True
    show_running: bool = True
    scroll_action: str = "cycle_windows"  # cycle_windows, switch_workspace
    middle_click: str = "new_instance"
    show_tooltips: bool = True
    zoom_on_hover: bool = True
    zoom_scale: float = 1.5

    # Taskbar mode
    taskbar_enabled: bool = True
    taskbar_show_titles: bool = True
    taskbar_max_width: int = 200

    # Multi-monitor
    show_on_all_monitors: bool = False
    primary_monitor_only: bool = True

    # Advanced
    intellihide: bool = True  # auto-hide when windows overlap
    backdrop_effect: str = "blur"  # blur, dim, none
    animation_speed_ms: int = 200

    @property
    def position_label(self) -> str:
        return f"{POSITION_ICONS.get(self.position, '❓')} {self.position.value.title()}"

    @property
    def size_str(self) -> str:
        return f"{self.dock_size}px (icons: {self.icon_size}px)"

    @property
    def opacity_str(self) -> str:
        return f"{self.opacity * 100:.0f}%"

    @property
    def opacity_bar(self) -> str:
        filled = int(self.opacity * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def theme_display(self) -> str:
        return self.theme.value.title()


@dataclass
class MonitorDock:
    monitor_name: str = ""
    position: DockPosition = DockPosition.BOTTOM
    enabled: bool = True
    dock_size: int = 48

    @property
    def display(self) -> str:
        status = "🟢" if self.enabled else "⚫"
        return f"{status} {self.monitor_name}: {self.position.value}"


class DockCustomizer:
    def __init__(self):
        self.config: DockConfig = DockConfig()
        self.apps: List[DockApp] = []
        self.widgets: List[DockWidget] = []
        self.monitors: List[MonitorDock] = []
        self._selected_app: int = 0
        self._selected_widget: int = 0
        self._view_mode: str = "apps"
        self._create_sample_data()

    def _create_sample_data(self):
        self.apps = [
            DockApp("Firefox", "/usr/bin/firefox", "🦊", True, True, 3, False,
                    "browser", time.time() - 300, 1240),
            DockApp("VS Code", "/usr/bin/code", "💻", True, True, 0, True,
                    "development", time.time() - 60, 2100),
            DockApp("Terminal", "/usr/bin/nyrqis-terminal", "🖥️", True, True, 0, True,
                    "system", time.time() - 120, 3500),
            DockApp("Files", "/usr/bin/nyrqis-files", "📁", True, False, 0, False,
                    "system", time.time() - 3600, 890),
            DockApp("Spotify", "/usr/bin/spotify", "🎵", True, True, 1, False,
                    "media", time.time() - 1800, 450),
            DockApp("Discord", "/usr/bin/discord", "💬", True, True, 12, False,
                    "messaging", time.time() - 600, 780),
            DockApp("Settings", "/usr/bin/nyrqis-settings", "⚙️", True, False, 0, False,
                    "system", time.time() - 86400, 45),
            DockApp("GIMP", "/usr/bin/gimp", "🎨", True, False, 0, False,
                    "graphics", time.time() - 86400 * 3, 12),
            DockApp("Blender", "/usr/bin/blender", "🧊", False, False, 0, False,
                    "graphics", time.time() - 86400 * 7, 5),
            DockApp("OBS Studio", "/usr/bin/obs", "📹", True, False, 0, False,
                    "media", time.time() - 86400 * 2, 18),
            DockApp("Neovim", "/usr/bin/nvim", "📝", False, True, 0, True,
                    "development", time.time() - 60, 890),
            DockApp("Docker", "/usr/bin/docker", "🐳", False, False, 0, False,
                    "development", time.time() - 86400, 32),
            DockApp("System Monitor", "/usr/bin/nyrqis-sysmon", "📊", False, True, 0, False,
                    "system", time.time() - 3600, 56),
            DockApp("Calculator", "/usr/bin/nyrqis-calc", "🧮", False, False, 0, False,
                    "utilities", time.time() - 86400 * 15, 8),
            DockApp("Screenshot", "/usr/bin/nyrqis-screenshot", "📸", False, False, 0, False,
                    "utilities", time.time() - 86400 * 5, 23),
        ]

        self.widgets = [
            DockWidget(WidgetType.SHOW_APPS, True, 36),
            DockWidget(WidgetType.SPACER, True, 12),
            DockWidget(WidgetType.CLOCK, True, 32, True, "%H:%M"),
            DockWidget(WidgetType.CALENDAR, True, 32, False),
            DockWidget(WidgetType.SPACER, True, 12),
            DockWidget(WidgetType.CPU, True, 32),
            DockWidget(WidgetType.NETWORK, True, 32),
            DockWidget(WidgetType.VOLUME, True, 32),
            DockWidget(WidgetType.BATTERY, True, 32),
            DockWidget(WidgetType.NOTIFICATIONS, True, 32),
            DockWidget(WidgetType.TRASH, True, 32),
        ]

        self.monitors = [
            MonitorDock("ASUS PA278QV (Primary)", DockPosition.BOTTOM, True, 48),
            MonitorDock("LG C2 42\" (TV)", DockPosition.LEFT, True, 40),
            MonitorDock("Dell U2723QE (Secondary)", DockPosition.BOTTOM, False, 48),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_app(self) -> Optional[DockApp]:
        if 0 <= self._selected_app < len(self.apps):
            return self.apps[self._selected_app]
        return None

    def select_app(self, idx: int):
        if 0 <= idx < len(self.apps):
            self._selected_app = idx

    def select_widget(self, idx: int):
        if 0 <= idx < len(self.widgets):
            self._selected_widget = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        if self._view_mode == "apps":
            self._selected_app = min(self._selected_app + 1, len(self.apps) - 1)
        elif self._view_mode == "widgets":
            self._selected_widget = min(self._selected_widget + 1, len(self.widgets) - 1)

    def select_up(self):
        if self._view_mode == "apps":
            self._selected_app = max(self._selected_app - 1, 0)
        elif self._view_mode == "widgets":
            self._selected_widget = max(self._selected_widget - 1, 0)

    # ─── App Actions ───────────────────────────────────────────────────

    def pin_app(self, idx: int) -> bool:
        if 0 <= idx < len(self.apps):
            self.apps[idx].pinned = True
            return True
        return False

    def unpin_app(self, idx: int) -> bool:
        if 0 <= idx < len(self.apps):
            self.apps[idx].pinned = False
            return True
        return False

    def toggle_favorite(self, idx: int) -> bool:
        if 0 <= idx < len(self.apps):
            self.apps[idx].favorite = not self.apps[idx].favorite
            return True
        return False

    def move_app(self, from_idx: int, to_idx: int) -> bool:
        if 0 <= from_idx < len(self.apps) and 0 <= to_idx < len(self.apps):
            app = self.apps.pop(from_idx)
            self.apps.insert(to_idx, app)
            return True
        return False

    def remove_app(self, idx: int) -> bool:
        if 0 <= idx < len(self.apps):
            self.apps.pop(idx)
            if self._selected_app >= len(self.apps):
                self._selected_app = max(0, len(self.apps) - 1)
            return True
        return False

    def add_app(self, name: str, path: str, icon: str = "📦") -> DockApp:
        app = DockApp(name, path, icon, pinned=True)
        self.apps.append(app)
        return app

    # ─── Config Actions ────────────────────────────────────────────────

    def set_position(self, position: DockPosition):
        self.config.position = position

    def set_dock_size(self, size: int):
        self.config.dock_size = max(32, min(80, size))
        self.config.icon_size = max(24, min(64, size - 12))

    def set_opacity(self, opacity: float):
        self.config.opacity = max(0.1, min(1.0, opacity))

    def set_theme(self, theme: DockTheme):
        self.config.theme = theme

    def toggle_auto_hide(self):
        modes = list(AutoHideMode)
        idx = modes.index(self.config.auto_hide)
        self.config.auto_hide = modes[(idx + 1) % len(modes)]

    def toggle_zoom(self):
        self.config.zoom_on_hover = not self.config.zoom_on_hover

    def toggle_intellihide(self):
        self.config.intellihide = not self.config.intellihide

    # ─── Widget Actions ────────────────────────────────────────────────

    def toggle_widget(self, idx: int) -> bool:
        if 0 <= idx < len(self.widgets):
            self.widgets[idx].enabled = not self.widgets[idx].enabled
            return True
        return False

    def add_widget(self, widget_type: WidgetType) -> DockWidget:
        w = DockWidget(widget_type)
        self.widgets.append(w)
        return w

    def remove_widget(self, idx: int) -> bool:
        if 0 <= idx < len(self.widgets):
            self.widgets.pop(idx)
            return True
        return False

    # ─── Queries ───────────────────────────────────────────────────────

    def get_pinned_apps(self) -> List[DockApp]:
        return [a for a in self.apps if a.pinned]

    def get_running_apps(self) -> List[DockApp]:
        return [a for a in self.apps if a.running]

    def get_favorites(self) -> List[DockApp]:
        return [a for a in self.apps if a.favorite]

    def get_active_widgets(self) -> List[DockWidget]:
        return [w for w in self.widgets if w.enabled]

    def search_apps(self, query: str) -> List[DockApp]:
        q = query.lower()
        return [a for a in self.apps if q in a.name.lower() or q in a.category.lower()]

    def get_stats(self) -> Dict:
        return {
            "total_apps": len(self.apps),
            "pinned": len(self.get_pinned_apps()),
            "running": len(self.get_running_apps()),
            "favorites": len(self.get_favorites()),
            "widgets": len(self.widgets),
            "active_widgets": len(self.get_active_widgets()),
            "monitors": len(self.monitors),
            "position": self.config.position.value,
            "theme": self.config.theme.value,
        }
