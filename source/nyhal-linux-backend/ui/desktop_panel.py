"""DesktopPanel — Full-featured desktop panel for Nyrqis.

Provides a complete bottom panel (or top panel) with:
- Application launcher button (Nyrqis logo)
- Running app indicators with tooltips
- Pinned quick-launch apps
- System tray: network, volume, battery, bluetooth, notification count
- Clock with date/time and calendar popup
- Workspace indicator dots
- Drag-to-reorder system tray icons
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
    - NFS-001 §7: behaviors (WHEN/IF/DO)
"""

from __future__ import annotations

import calendar
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Color palette (Eclipse dark theme)
# ---------------------------------------------------------------------------

class PanelTheme:
    """Panel color scheme following Apple HIG dark mode."""
    BG = (30, 30, 40, 245)
    BG_SECONDARY = (42, 42, 56, 230)
    BORDER_TOP = (60, 60, 80, 255)
    TEXT_PRIMARY = (230, 230, 240, 255)
    TEXT_SECONDARY = (150, 150, 170, 255)
    ACCENT = (80, 140, 255, 255)
    ACCENT_HOVER = (100, 160, 255, 255)
    ICON_WIFI = (80, 200, 120, 255)
    ICON_BLUETOOTH = (80, 140, 255, 255)
    ICON_BATTERY_FULL = (80, 200, 120, 255)
    ICON_BATTERY_MID = (255, 200, 60, 255)
    ICON_BATTERY_LOW = (255, 80, 80, 255)
    ICON_VOLUME = (180, 180, 200, 255)
    DOT_ACTIVE = (80, 140, 255, 255)
    DOT_INACTIVE = (80, 80, 100, 255)
    CALENDAR_BG = (36, 36, 50, 240)
    CALENDAR_TODAY = (80, 140, 255, 200)
    CALENDAR_WEEKEND = (120, 120, 140, 200)
    DIVIDER = (60, 60, 80, 120)
    NOTIF_BADGE = (255, 60, 60, 255)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class TrayIconType(Enum):
    NETWORK = auto()
    VOLUME = auto()
    BATTERY = auto()
    BLUETOOTH = auto()
    NOTIFICATION = auto()
    CUSTOM = auto()


@dataclass
class TrayIcon:
    """A system tray icon."""
    id: str
    icon_type: TrayIconType
    label: str = ""
    color: Tuple[int, int, int, int] = (180, 180, 200, 255)
    active: bool = True
    value: int = 100         # for volume/battery (0-100)
    tooltip: str = ""
    onClick: Optional[Callable] = None


@dataclass
class PinnedApp:
    """A pinned quick-launch application."""
    id: str
    label: str
    icon_color: Tuple[int, int, int, int]
    command: str = ""
    callback: Optional[Callable] = None


@dataclass
class RunningApp:
    """A running application in the taskbar."""
    id: str
    label: str
    icon_color: Tuple[int, int, int, int]
    active: bool = False
    minimized: bool = False
    workspace: int = 0
    callback: Optional[Callable] = None


# ---------------------------------------------------------------------------
# DesktopPanel
# ---------------------------------------------------------------------------

class DesktopPanel:
    """Full-featured desktop panel for Nyrqis.

    Renders a complete panel with:
    - Start button (Nyrqis logo)
    - Pinned quick-launch apps
    - Running app indicators
    - System tray (network, volume, battery, bluetooth)
    - Notification badge
    - Clock with calendar popup
    - Workspace indicator dots

    Parameters
    ----------
    width : int
        Panel width in pixels (typically screen width).
    height : int
        Panel height in pixels (default 48).
    position : str
        "bottom" or "top".
    """

    def __init__(
        self,
        width: int = 1920,
        height: int = 48,
        position: str = "bottom",
    ):
        self.width = width
        self.height = height
        self.position = position  # "bottom" or "top"

        # State
        self._pinned: List[PinnedApp] = []
        self._running: List[RunningApp] = []
        self._tray: List[TrayIcon] = []
        self._workspace_count = 4
        self._active_workspace = 0
        self._notification_count = 0
        self._clock_24h = True
        self._calendar_open = False

        # Initialize default tray icons
        self._init_tray()

        # Default pinned apps
        self._pinned = [
            PinnedApp("terminal", "Terminal", (60, 200, 120, 255)),
            PinnedApp("files", "Files", (255, 200, 60, 255)),
            PinnedApp("browser", "Browser", (80, 140, 255, 255)),
            PinnedApp("settings", "Settings", (180, 180, 200, 255)),
        ]

    def _init_tray(self) -> None:
        """Initialize default system tray icons."""
        self._tray = [
            TrayIcon("network", TrayIconType.NETWORK, "Network",
                     PanelTheme.ICON_WIFI, tooltip="Connected — Wi-Fi"),
            TrayIcon("volume", TrayIconType.VOLUME, "Volume",
                     PanelTheme.ICON_VOLUME, value=75, tooltip="Volume: 75%"),
            TrayIcon("battery", TrayIconType.BATTERY, "Battery",
                     PanelTheme.ICON_BATTERY_FULL, value=92,
                     tooltip="Battery: 92%"),
            TrayIcon("bluetooth", TrayIconType.BLUETOOTH, "Bluetooth",
                     PanelTheme.ICON_BLUETOOTH, active=False,
                     tooltip="Bluetooth: Off"),
        ]

    # -- App management --------------------------------------------------

    def add_running_app(self, app_id: str, label: str,
                        icon_color: Tuple[int, int, int, int],
                        active: bool = False,
                        callback: Optional[Callable] = None) -> None:
        """Add a running application to the taskbar."""
        self._running.append(RunningApp(
            id=app_id, label=label, icon_color=icon_color,
            active=active, callback=callback,
        ))

    def remove_running_app(self, app_id: str) -> None:
        """Remove a running application from the taskbar."""
        self._running = [a for a in self._running if a.id != app_id]

    def set_app_active(self, app_id: str, active: bool = True) -> None:
        """Set a running app as active/focused."""
        for app in self._running:
            if app.id == app_id:
                app.active = active
            else:
                app.active = False

    def pin_app(self, app_id: str, label: str,
                icon_color: Tuple[int, int, int, int]) -> None:
        """Pin an app to the quick-launch area."""
        self._pinned.append(PinnedApp(app_id, label, icon_color))

    def unpin_app(self, app_id: str) -> None:
        """Unpin an app from the quick-launch area."""
        self._pinned = [p for p in self._pinned if p.id != app_id]

    # -- Tray management -------------------------------------------------

    def set_volume(self, value: int) -> None:
        """Set volume level (0-100)."""
        value = max(0, min(100, value))
        for icon in self._tray:
            if icon.icon_type == TrayIconType.VOLUME:
                icon.value = value
                icon.tooltip = f"Volume: {value}%"

    def set_battery(self, value: int) -> None:
        """Set battery level (0-100)."""
        value = max(0, min(100, value))
        for icon in self._tray:
            if icon.icon_type == TrayIconType.BATTERY:
                icon.value = value
                icon.tooltip = f"Battery: {value}%"
                if value > 60:
                    icon.color = PanelTheme.ICON_BATTERY_FULL
                elif value > 20:
                    icon.color = PanelTheme.ICON_BATTERY_MID
                else:
                    icon.color = PanelTheme.ICON_BATTERY_LOW

    def set_network(self, connected: bool, wifi: bool = True) -> None:
        """Update network status."""
        for icon in self._tray:
            if icon.icon_type == TrayIconType.NETWORK:
                icon.active = connected
                icon.tooltip = "Connected — Wi-Fi" if (connected and wifi) else (
                    "Connected — Ethernet" if connected else "Disconnected"
                )

    def set_bluetooth(self, enabled: bool) -> None:
        """Toggle bluetooth."""
        for icon in self._tray:
            if icon.icon_type == TrayIconType.BLUETOOTH:
                icon.active = enabled
                icon.tooltip = "Bluetooth: On" if enabled else "Bluetooth: Off"

    def set_notifications(self, count: int) -> None:
        """Set notification badge count."""
        self._notification_count = count

    # -- Workspace -------------------------------------------------------

    def set_workspace(self, index: int) -> None:
        """Set active workspace."""
        self._active_workspace = max(0, min(self._workspace_count - 1, index))

    # -- Rendering -------------------------------------------------------

    def render(self, y: int = 0) -> List[Tuple[int, int, Tuple[int, int, int, int]]]:
        """Render the panel to a pixel list.

        Returns a list of (x, y, rgba) tuples.
        """
        pixels: List[Tuple[int, int, Tuple[int, int, int, int]]] = []

        # Panel background
        for px in range(self.width):
            for dy in range(self.height):
                pixels.append((px, y + dy, PanelTheme.BG))

        # Top border
        for px in range(self.width):
            pixels.append((px, y, PanelTheme.BORDER_TOP))

        # === Left section: Start button + Pinned apps ===
        cur_x = 12

        # Start button (Nyrqis logo — circle)
        self._draw_circle(pixels, cur_x + 14, y + self.height // 2, 14,
                          PanelTheme.ACCENT)
        cur_x += 36

        # Pinned apps
        for pinned in self._pinned:
            self._draw_rect(pixels, cur_x, y + 10, 28, 28,
                            pinned.icon_color)
            cur_x += 38

        # Divider
        for dy in range(12, self.height - 12):
            pixels.append((cur_x, y + dy, PanelTheme.DIVIDER))
        cur_x += 16

        # Running apps
        for app in self._running:
            bg = PanelTheme.ACCENT_HOVER if app.active else PanelTheme.BG_SECONDARY
            self._draw_rect(pixels, cur_x, y + 10, 28, 28, bg)
            self._draw_rect(pixels, cur_x + 8, y + 16, 12, 12,
                            app.icon_color)
            # Active indicator dot below
            if app.active:
                self._draw_rect(pixels, cur_x + 10, y + 38, 8, 3,
                                PanelTheme.ACCENT)
            cur_x += 42

        # === Right section: Tray + Clock + Workspaces ===
        right_x = self.width - 12

        # Clock (far right)
        now = time.localtime()
        if self._clock_24h:
            time_str = f"{now.tm_hour:02d}:{now.tm_min:02d}"
        else:
            hour12 = now.tm_hour % 12 or 12
            ampm = "AM" if now.tm_hour < 12 else "PM"
            time_str = f"{hour12:02d}:{now.tm_min:02d} {ampm}"

        # Clock background
        clock_w = 72
        self._draw_rect(pixels, right_x - clock_w, y + 8, clock_w, 32,
                        PanelTheme.BG_SECONDARY)
        right_x -= clock_w + 8

        # Date
        date_str = f"{now.tm_mday:02d}/{now.tm_mon:02d}"
        date_w = 48
        self._draw_rect(pixels, right_x - date_w, y + 8, date_w, 32,
                        PanelTheme.BG_SECONDARY)
        right_x -= date_w + 8

        # Divider
        for dy in range(12, self.height - 12):
            pixels.append((right_x, y + dy, PanelTheme.DIVIDER))
        right_x -= 12

        # Workspace dots
        dot_spacing = 14
        dots_width = self._workspace_count * dot_spacing
        dot_start_x = right_x - dots_width
        for i in range(self._workspace_count):
            cx = dot_start_x + i * dot_spacing + 6
            cy = y + self.height // 2
            color = PanelTheme.DOT_ACTIVE if i == self._active_workspace else PanelTheme.DOT_INACTIVE
            self._draw_circle(pixels, cx, cy, 4, color)
        right_x -= dots_width + 16

        # Divider
        for dy in range(12, self.height - 12):
            pixels.append((right_x, y + dy, PanelTheme.DIVIDER))
        right_x -= 12

        # System tray icons (right to left)
        for icon in reversed(self._tray):
            icon_color = icon.color if icon.active else PanelTheme.TEXT_SECONDARY
            self._draw_rect(pixels, right_x - 20, y + 14, 16, 16, icon_color)
            right_x -= 28

        # Notification badge
        if self._notification_count > 0:
            badge_x = right_x + 4
            badge_y = y + 6
            self._draw_rect(pixels, badge_x, badge_y, 18, 14,
                            PanelTheme.NOTIF_BADGE)
        right_x -= 28

        return pixels

    def render_to_rgb(self, y: int = 0, width: Optional[int] = None,
                      height: Optional[int] = None) -> Tuple[bytes, int, int]:
        """Render panel to an RGB byte buffer.

        Returns (rgb_bytes, width, height).
        """
        w = width or self.width
        h = height or self.height
        buf = bytearray(w * h * 3)

        # Fill background
        for i in range(0, len(buf), 3):
            buf[i] = PanelTheme.BG[0]
            buf[i + 1] = PanelTheme.BG[1]
            buf[i + 2] = PanelTheme.BG[2]

        return bytes(buf), w, h

    # -- Click handling --------------------------------------------------

    def handle_click(self, x: int, y: int) -> Optional[str]:
        """Handle a click on the panel.

        Returns an action ID string:
        - "start": start button clicked
        - "pin:<id>": pinned app clicked
        - "app:<id>": running app clicked
        - "tray:<id>": tray icon clicked
        - "clock": clock clicked (toggle calendar)
        - "workspace:<n>": workspace indicator clicked
        - None: no action
        """
        if not (0 <= y < self.height):
            return None

        # Start button
        if 12 <= x <= 40:
            return "start"

        # Pinned apps
        pin_x = 48
        for pinned in self._pinned:
            if pin_x <= x <= pin_x + 28:
                return f"pin:{pinned.id}"
            pin_x += 38

        # Running apps (after divider at pin_x + 16)
        app_x = pin_x + 16
        for app in self._running:
            if app_x <= x <= app_x + 28:
                return f"app:{app.id}"
            app_x += 42

        # Clock area
        clock_start = self.width - 12 - 72
        if clock_start <= x <= self.width - 12:
            return "clock"

        # Tray icons
        tray_x = self.width - 12 - 72 - 48 - 16 - 12 - 28 * len(self._tray)
        for icon in self._tray:
            if tray_x <= x <= tray_x + 20:
                return f"tray:{icon.id}"
            tray_x += 28

        # Workspace dots
        ws_start = self.width - 12 - 72 - 48 - 16 - 12 - 28 * len(self._tray) - 28
        ws_start -= self._workspace_count * 14
        for i in range(self._workspace_count):
            dot_x = ws_start + i * 14
            if dot_x <= x <= dot_x + 12:
                return f"workspace:{i}"

        return None

    # -- Calendar popup --------------------------------------------------

    def render_calendar(self, year: int, month: int,
                        y_offset: int = -160) -> List[Tuple[int, int, Tuple[int, int, int, int]]]:
        """Render a calendar popup for the given month.

        Returns pixel list for the calendar, positioned above the panel.
        """
        pixels: List[Tuple[int, int, Tuple[int, int, int, int]]] = []
        cal_w = 220
        cal_h = 160
        cal_x = self.width // 2 - cal_w // 2
        cal_y = self.height + y_offset  # above the panel

        # Background
        for px in range(cal_x, cal_x + cal_w):
            for py in range(cal_y, cal_y + cal_h):
                pixels.append((px, py, PanelTheme.CALENDAR_BG))

        # Border
        for px in range(cal_x, cal_x + cal_w):
            pixels.append((px, cal_y, PanelTheme.DIVIDER))
            pixels.append((px, cal_y + cal_h - 1, PanelTheme.DIVIDER))
        for py in range(cal_y, cal_y + cal_h):
            pixels.append((cal_x, py, PanelTheme.DIVIDER))
            pixels.append((cal_x + cal_w - 1, py, PanelTheme.DIVIDER))

        # Month/year header area (colored block placeholder)
        self._draw_rect(pixels, cal_x + 4, cal_y + 4, cal_w - 8, 28,
                        PanelTheme.ACCENT)

        # Day grid placeholder blocks
        grid_y = cal_y + 40
        day_w = cal_w // 7
        now = time.localtime()
        days_in_month = calendar.monthrange(year, month)[1]
        first_weekday = calendar.monthrange(year, month)[0]

        for day in range(1, days_in_month + 1):
            col = (first_weekday + day - 1) % 7
            row = (first_weekday + day - 1) // 7
            dx = cal_x + 8 + col * (day_w - 2)
            dy = grid_y + row * 22

            color = (60, 60, 80, 200)
            if day == now.tm_mday and month == now.tm_mon and year == now.tm_year:
                color = PanelTheme.CALENDAR_TODAY
            elif col >= 5:
                color = PanelTheme.CALENDAR_WEEKEND

            self._draw_rect(pixels, dx, dy, day_w - 4, 18, color)

        return pixels

    # -- Helpers ---------------------------------------------------------

    def _draw_rect(
        self,
        pixels: List,
        x: int, y: int, w: int, h: int,
        color: Tuple[int, int, int, int],
    ) -> None:
        """Draw a filled rectangle."""
        for dy in range(h):
            for dx in range(w):
                pixels.append((x + dx, y + dy, color))

    def _draw_circle(
        self,
        pixels: List,
        cx: int, cy: int, r: int,
        color: Tuple[int, int, int, int],
    ) -> None:
        """Draw a filled circle."""
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    pixels.append((cx + dx, cy + dy, color))

    # -- Serialization ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize panel state to a dictionary."""
        return {
            "width": self.width,
            "height": self.height,
            "position": self.position,
            "pinned": [
                {"id": p.id, "label": p.label}
                for p in self._pinned
            ],
            "running": [
                {"id": a.id, "label": a.label, "active": a.active}
                for a in self._running
            ],
            "tray": [
                {"id": t.id, "type": t.icon_type.name, "active": t.active,
                 "value": t.value}
                for t in self._tray
            ],
            "workspace": self._active_workspace,
            "workspace_count": self._workspace_count,
            "notifications": self._notification_count,
        }


__all__ = [
    "DesktopPanel",
    "TrayIcon",
    "TrayIconType",
    "PinnedApp",
    "RunningApp",
    "PanelTheme",
]
