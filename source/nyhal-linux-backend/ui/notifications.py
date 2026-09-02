#!/usr/bin/env python3
"""notifications — Nyrqis toast notification system.

A notification service that manages desktop toast notifications with:

- Multiple severity levels (info, warning, error, success)
- Auto-dismiss with configurable timeout
- Notification stacking and ordering
- Theme-aware rendering (Eclipse/Solar)
- Action callbacks on notification click
- History tracking

This is the Nyrqis counterpart of a desktop notification daemon.
In a real OS it would communicate with the compositor over IPC;
on the floor it renders to PIL images for verification.

References:
    - NFS-001 §7: behaviors (WHEN/IF/DO)
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class NotificationSeverity(Enum):
    INFO = auto()
    SUCCESS = auto()
    WARNING = auto()
    ERROR = auto()


@dataclass
class Notification:
    """A single desktop notification.

    Parameters
    ----------
    id : str
        Unique identifier for this notification.
    title : str
        Bold header text.
    message : str
        Body text.
    severity : NotificationSeverity
        Visual severity level (affects color and icon).
    timeout_ms : int
        Auto-dismiss timeout in milliseconds.  0 = no auto-dismiss.
    actions : dict
        Available actions: ``{"dismiss": callable, "action": callable}``.
    timestamp : float
        Creation time (epoch seconds).
    dismissed : bool
        Whether the notification has been dismissed.
    """
    id: str
    title: str = ""
    message: str = ""
    severity: NotificationSeverity = NotificationSeverity.INFO
    timeout_ms: int = 5000
    actions: Dict[str, Optional[Callable]] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    dismissed: bool = False

    # Rendering layout (populated by the renderer)
    _x: int = 0
    _y: int = 0
    _width: int = 360
    _height: int = 80


# Severity → color key mapping
SEVERITY_COLORS = {
    NotificationSeverity.INFO: "accent",
    NotificationSeverity.SUCCESS: "toggle_on",     # reuse green-ish
    NotificationSeverity.WARNING: "button_bg",     # warm
    NotificationSeverity.ERROR: "border",          # red-ish
}

SEVERITY_ICONS = {
    NotificationSeverity.INFO: "ℹ",
    NotificationSeverity.SUCCESS: "✓",
    NotificationSeverity.WARNING: "⚠",
    NotificationSeverity.ERROR: "✕",
}


class NotificationService:
    """Desktop notification service.

    Manages a list of active notifications and provides methods to
    create, dismiss, and render them.

    Parameters
    ----------
    max_visible : int
        Maximum number of visible (non-stacked) notifications.
    default_timeout_ms : int
        Default auto-dismiss timeout for new notifications.
    """

    def __init__(
        self,
        max_visible: int = 5,
        default_timeout_ms: int = 5000,
    ) -> None:
        self._notifications: List[Notification] = []
        self._max_visible = max_visible
        self._default_timeout_ms = default_timeout_ms
        self._callbacks: List[Callable] = []
        self._next_id = 1

    # -- Notification API ----------------------------------------------

    def notify(
        self,
        title: str,
        message: str = "",
        severity: NotificationSeverity = NotificationSeverity.INFO,
        timeout_ms: Optional[int] = None,
        action: Optional[Callable] = None,
    ) -> Notification:
        """Create and display a new notification.

        Returns the Notification object.
        """
        nid = f"notif-{self._next_id}"
        self._next_id += 1

        notif = Notification(
            id=nid,
            title=title,
            message=message,
            severity=severity,
            timeout_ms=timeout_ms if timeout_ms is not None else self._default_timeout_ms,
            actions={"action": action} if action else {},
        )
        self._notifications.append(notif)
        self._log(f"[{severity.name}] {title}: {message}")
        self._dispatch(notif, "created")
        return notif

    def info(self, title: str, message: str = "", **kw) -> Notification:
        """Shorthand for an info notification."""
        return self.notify(title, message, NotificationSeverity.INFO, **kw)

    def success(self, title: str, message: str = "", **kw) -> Notification:
        """Shorthand for a success notification."""
        return self.notify(title, message, NotificationSeverity.SUCCESS, **kw)

    def warning(self, title: str, message: str = "", **kw) -> Notification:
        """Shorthand for a warning notification."""
        return self.notify(title, message, NotificationSeverity.WARNING, **kw)

    def error(self, title: str, message: str = "", **kw) -> Notification:
        """Shorthand for an error notification."""
        return self.notify(title, message, NotificationSeverity.ERROR, **kw)

    def dismiss(self, notification_id: str) -> bool:
        """Dismiss a notification by ID."""
        for n in self._notifications:
            if n.id == notification_id and not n.dismissed:
                n.dismissed = True
                self._log(f"Dismissed: {n.title}")
                self._dispatch(n, "dismissed")
                return True
        return False

    def dismiss_all(self) -> int:
        """Dismiss all notifications.  Returns count dismissed."""
        count = 0
        for n in self._notifications:
            if not n.dismissed:
                n.dismissed = True
                self._dispatch(n, "dismissed")
                count += 1
        self._log(f"Dismissed {count} notification(s)")
        return count

    def clear(self) -> int:
        """Remove all notifications (dismissed and active)."""
        count = len(self._notifications)
        self._notifications.clear()
        self._log(f"Cleared {count} notification(s)")
        return count

    def tick(self, elapsed_ms: float = 100) -> List[Notification]:
        """Advance time by ``elapsed_ms`` milliseconds.

        Auto-dismisses expired notifications and returns the list
        of notifications that were dismissed this tick.
        """
        now = time.time()
        dismissed = []
        for n in self._notifications:
            if n.dismissed:
                continue
            if n.timeout_ms > 0:
                age_ms = (now - n.timestamp) * 1000
                if age_ms >= n.timeout_ms:
                    n.dismissed = True
                    self._dispatch(n, "dismissed")
                    dismissed.append(n)
        return dismissed

    # -- Queries ------------------------------------------------------

    @property
    def active(self) -> List[Notification]:
        """Currently visible (non-dismissed) notifications."""
        return [n for n in self._notifications if not n.dismissed]

    @property
    def history(self) -> List[Notification]:
        """All notifications including dismissed ones."""
        return list(self._notifications)

    @property
    def count(self) -> int:
        """Number of active notifications."""
        return len(self.active)

    def by_severity(
        self, severity: NotificationSeverity
    ) -> List[Notification]:
        """Active notifications filtered by severity."""
        return [n for n in self.active if n.severity == severity]

    # -- Callbacks ----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        """Register a callback for notification events.

        Callback signature: ``(notification, event_type) -> None``
        where event_type is one of: 'created', 'dismissed'.
        """
        self._callbacks.append(callback)

    def _dispatch(self, notif: Notification, event_type: str) -> None:
        for cb in self._callbacks:
            try:
                cb(notif, event_type)
            except Exception as e:
                self._log(f"Callback error: {e}")

    # -- Layout -------------------------------------------------------

    def layout(self, screen_width: int = 1920, screen_height: int = 1080) -> None:
        """Compute the layout positions for active notifications.

        Notifications stack from the top-right corner of the screen.
        """
        margin = 16
        notif_w = 360
        notif_h = 80
        gap = 8

        x = screen_width - notif_w - margin
        y = margin

        for n in self.active:
            n._x = x
            n._y = y
            n._width = notif_w
            n._height = notif_h
            y += notif_h + gap

    # -- Render to PIL ------------------------------------------------

    def render(
        self,
        theme: Optional[Dict[str, Any]] = None,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Any:
        """Render active notifications to a PIL Image.

        Returns a transparent RGBA image that can be composited on top
        of the desktop.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        self.layout(screen_width, screen_height)
        img = Image.new("RGBA", (screen_width, screen_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Load font
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        except (OSError, IOError):
            font = ImageFont.load_default()
            font_bold = font

        # Default theme colors
        colors = {
            "background": (40, 40, 40),
            "surface_elevated": (50, 50, 50),
            "border": (80, 80, 80),
            "text_primary": (230, 230, 230),
            "text_secondary": (150, 150, 150),
            "accent": (100, 149, 237),
        }
        if theme:
            colors.update(theme)

        for n in self.active:
            self._render_notification(
                draw, n, colors, font, font_bold)

        return img

    def _render_notification(
        self, draw, notif: Notification, colors: Dict, font, font_bold
    ) -> None:
        """Render a single notification toast."""
        x, y = notif._x, notif._y
        w, h = notif._width, notif._height

        # Background with rounded corners
        bg_color = colors.get("surface_elevated", (50, 50, 50))
        border_color = colors.get("border", (80, 80, 80))

        # Severity accent strip
        accent_colors = {
            NotificationSeverity.INFO: colors.get("accent", (100, 149, 237)),
            NotificationSeverity.SUCCESS: (100, 200, 100),
            NotificationSeverity.WARNING: (220, 180, 60),
            NotificationSeverity.ERROR: (220, 80, 80),
        }
        strip_color = accent_colors.get(notif.severity, border_color)

        # Shadow
        for i in range(3):
            alpha = max(0, 60 - i * 20)
            draw.rectangle(
                [x + i + 1, y + i + 1, x + w + i + 1, y + h + i + 1],
                fill=(0, 0, 0, alpha))

        # Body
        draw.rectangle([x, y, x + w, y + h], fill=bg_color,
                       outline=border_color)

        # Severity strip (left edge)
        draw.rectangle([x, y, x + 4, y + h], fill=strip_color)

        # Icon
        icon = SEVERITY_ICONS.get(notif.severity, "•")
        draw.text((x + 12, y + 8), icon, fill=strip_color, font=font_bold)

        # Title
        draw.text((x + 30, y + 8), notif.title,
                  fill=colors.get("text_primary", (230, 230, 230)),
                  font=font_bold)

        # Message
        if notif.message:
            # Truncate if too long
            msg = notif.message[:50]
            if len(notif.message) > 50:
                msg += "…"
            draw.text((x + 12, y + 32), msg,
                      fill=colors.get("text_secondary", (150, 150, 150)),
                      font=font)

        # Dismiss hint (top-right)
        draw.text((x + w - 20, y + 4), "×",
                  fill=colors.get("text_secondary", (150, 150, 150)),
                  font=font)

    # -- Hit testing --------------------------------------------------

    def hit_test(self, x: int, y: int) -> Optional[Notification]:
        """Find the notification at screen coordinates."""
        for n in reversed(self.active):
            if (n._x <= x < n._x + n._width
                    and n._y <= y < n._y + n._height):
                return n
        return None

    def handle_click(self, x: int, y: int) -> bool:
        """Handle a click on the notification area.

        Returns True if a notification was clicked.
        """
        notif = self.hit_test(x, y)
        if notif is None:
            return False

        # Check if click is on the dismiss button (top-right)
        if x >= notif._x + notif._width - 24:
            self.dismiss(notif.id)
            return True

        # Otherwise, invoke the action callback
        action = notif.actions.get("action")
        if action:
            action(notif)
            self._log(f"Action invoked: {notif.title}")

        self._dispatch(notif, "clicked")
        return True

    # -- Internal -----------------------------------------------------

    def _log(self, msg: str) -> None:
        logger.info("[Notifications] %s", msg)


__all__ = [
    "NotificationService",
    "Notification",
    "NotificationSeverity",
]


# ===========================================================================
# Android-style pull-down panels (Notification Shade & Quick Settings)
# ===========================================================================


@dataclass
class QuickToggle:
    """A quick settings toggle tile."""
    label: str
    icon_letter: str  # Single character for icon
    enabled: bool = False
    color_on: Tuple[int, int, int] = (80, 140, 255)
    color_off: Tuple[int, int, int] = (80, 80, 100)
    expanded: bool = False
    slider_value: int = -1  # -1 = no slider, 0-100 = has slider


class PanelState(Enum):
    """Panel animation state."""
    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"


# ---------------------------------------------------------------------------
# Notification shade (top-left pull-down)
# ---------------------------------------------------------------------------

class NotificationShade:
    """Android-style notification shade — pull down from top-left.
    
    Features:
    - Stacked notifications with app icons
    - Swipe-to-dismiss (simulated)
    - Auto-dismiss after timeout
    - Clear all button
    - Priority-based ordering
    """
    
    # Layout constants (Apple HIG: 8pt grid, 16pt margins)
    PANEL_WIDTH = 420
    HANDLE_HEIGHT = 24
    NOTIFICATION_HEIGHT = 80
    NOTIFICATION_PADDING = 12
    MAX_VISIBLE = 8
    CORNER_RADIUS = 16
    
    # Colors (Apple HIG system colors)
    BG_COLOR = (28, 28, 35)
    SURFACE_COLOR = (40, 40, 52)
    TEXT_PRIMARY = (240, 240, 245)
    TEXT_SECONDARY = (140, 140, 160)
    ACCENT = (80, 140, 255)
    DIVIDER = (55, 55, 70)
    DISMISS_BG = (60, 60, 75)
    
    def __init__(self, screen_width: int = 1920, screen_height: int = 1080):
        self._sw = screen_width
        self._sh = screen_height
        self._notifications: List[Notification] = []
        self._state: PanelState = PanelState.CLOSED
        self._drag_y: int = 0
        self._pull_progress: float = 0.0  # 0.0 = closed, 1.0 = fully open
        self._scroll_offset: int = 0
        self._auto_dismiss_ms: int = 5000
        self._clear_all_callbacks: List[Callable] = []
    
    # -- Notification management -------------------------------------------
    
    def add_notification(self, notif: Notification) -> None:
        """Add a notification to the shade."""
        self._notifications.insert(0, notif)
    
    def remove_notification(self, notif_id: str) -> bool:
        """Remove a notification by ID."""
        for i, n in enumerate(self._notifications):
            if n.id == notif_id:
                self._notifications.pop(i)
                return True
        return False
    
    def dismiss_notification(self, notif_id: str) -> bool:
        """Dismiss (swipe away) a notification."""
        for n in self._notifications:
            if n.id == notif_id:
                n.dismissed = True
                self._notifications = [x for x in self._notifications if not x.dismissed]
                return True
        return False
    
    def clear_all(self) -> int:
        """Clear all notifications. Returns count cleared."""
        count = len(self._notifications)
        self._notifications.clear()
        for cb in self._clear_all_callbacks:
            cb()
        return count
    
    def on_clear_all(self, callback: Callable) -> None:
        """Register a callback for clear all."""
        self._clear_all_callbacks.append(callback)
    
    @property
    def notification_count(self) -> int:
        return len(self._notifications)
    
    @property
    def notifications(self) -> List[Notification]:
        return list(self._notifications)
    
    # -- Panel state -------------------------------------------------------
    
    def open(self) -> None:
        self._state = PanelState.OPEN
        self._pull_progress = 1.0
    
    def close(self) -> None:
        self._state = PanelState.CLOSED
        self._pull_progress = 0.0
    
    def toggle(self) -> None:
        if self._state == PanelState.OPEN:
            self.close()
        else:
            self.open()
    
    @property
    def is_open(self) -> bool:
        return self._state == PanelState.OPEN
    
    @property
    def pull_progress(self) -> float:
        return self._pull_progress
    
    # -- Input handling ----------------------------------------------------
    
    def handle_drag_start(self, y: int) -> None:
        if y < self.HANDLE_HEIGHT * 3:
            self._drag_y = y
            self._state = PanelState.OPENING
    
    def handle_drag_move(self, y: int) -> None:
        if self._state == PanelState.OPENING:
            delta = y - self._drag_y
            self._pull_progress = max(0.0, min(1.0, delta / 300))
    
    def handle_drag_end(self, y: int) -> None:
        if self._state == PanelState.OPENING:
            if self._pull_progress > 0.3:
                self.open()
            else:
                self.close()
    
    def handle_tap(self, x: int, y: int) -> str:
        """Handle a tap within the panel. Returns action or ""."""
        if not self.is_open:
            return ""
        
        panel_x = 0
        panel_y = 0
        panel_h = self._panel_height
        
        if not (panel_x <= x <= panel_x + self.PANEL_WIDTH):
            self.close()
            return "close"
        
        if not (panel_y <= y <= panel_y + panel_h):
            self.close()
            return "close"
        
        # Check "Clear all" button
        clear_y = panel_y + panel_h - 50
        if y >= clear_y and self._notifications:
            self.clear_all()
            return "clear_all"
        
        # Check notification taps
        content_y = panel_y + self.HANDLE_HEIGHT + 40
        for i, notif in enumerate(self._notifications):
            ny = content_y + i * (self.NOTIFICATION_HEIGHT + 8)
            if ny <= y <= ny + self.NOTIFICATION_HEIGHT:
                return f"notification:{notif.id}"
        
        return ""
    
    @property
    def _panel_height(self) -> int:
        """Current panel height based on pull progress."""
        header = self.HANDLE_HEIGHT + 40
        notif_h = min(len(self._notifications), self.MAX_VISIBLE) * (self.NOTIFICATION_HEIGHT + 8)
        clear_h = 50 if self._notifications else 0
        total = header + notif_h + clear_h + 20
        return int(total * self._pull_progress)
    
    # -- Rendering ---------------------------------------------------------
    
    def render(self) -> Tuple[List[Tuple[int, int, int]], int, int]:
        """Render the notification shade to a pixel buffer."""
        w = self.PANEL_WIDTH
        h = self._panel_height
        if h <= 0:
            return [], 0, 0
        
        pixels = [self.BG_COLOR] * (w * h)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < w and 0 <= py < h:
                pixels[py * w + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int]) -> None:
            FONT = _get_small_font()
            glyph = FONT.get(ch, FONT[' '])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        set_pixel(cx + col, cy + row, color)
        
        def draw_text(tx: int, ty: int, text: str, color: Tuple[int, int, int]) -> int:
            cx = tx
            for ch in text[:50]:  # Limit length
                draw_char(cx, ty, ch, color)
                cx += 6
            return cx
        
        # Draw handle area
        handle_w = 40
        fill_rect((w - handle_w) // 2, 4, handle_w, 4, self.DIVIDER)
        
        # Header
        draw_text(16, self.HANDLE_HEIGHT + 8, "Notifications", self.TEXT_PRIMARY)
        count_text = f"{len(self._notifications)}"
        draw_text(w - 40, self.HANDLE_HEIGHT + 8, count_text, self.ACCENT)
        
        # Divider
        fill_rect(0, self.HANDLE_HEIGHT + 32, w, 1, self.DIVIDER)
        
        # Notifications
        content_y = self.HANDLE_HEIGHT + 40
        for i, notif in enumerate(self._notifications[:self.MAX_VISIBLE]):
            ny = content_y + i * (self.NOTIFICATION_HEIGHT + 8)
            if ny + self.NOTIFICATION_HEIGHT > h:
                break
            
            # Card background
            fill_rect(8, ny, w - 16, self.NOTIFICATION_HEIGHT, self.SURFACE_COLOR)
            
            # App icon (colored circle)
            icon_cx = 28
            icon_cy = ny + 20
            icon_color = getattr(notif, 'icon_color', (80, 140, 255))
            fill_rect(icon_cx - 8, icon_cy - 8, 16, 16, icon_color)
            
            # Title
            draw_text(48, ny + 12, notif.title[:30], self.TEXT_PRIMARY)
            
            # Body (if expanded)
            body = getattr(notif, "body", getattr(notif, "message", ""))
            if getattr(notif, "expanded", False) and body:
                draw_text(48, ny + 28, body[:40], self.TEXT_SECONDARY)
            
            # App name and time
            draw_text(48, ny + self.NOTIFICATION_HEIGHT - 16, getattr(notif, "app_name", "System"), self.TEXT_SECONDARY)
            draw_text(w - 80, ny + self.NOTIFICATION_HEIGHT - 16, getattr(notif, "time_ago", ""), self.TEXT_SECONDARY)
        
        # Clear all button
        if self._notifications:
            clear_y = h - 50
            fill_rect(8, clear_y, w - 16, 36, self.DISMISS_BG)
            clear_text = "Clear all"
            tw = len(clear_text) * 6
            draw_text((w - tw) // 2, clear_y + 10, clear_text, self.TEXT_PRIMARY)
        
        return pixels, w, h


# ---------------------------------------------------------------------------
# Quick settings (top-right pull-down)
# ---------------------------------------------------------------------------

class QuickSettings:
    """Android-style quick settings — pull down from top-right.
    
    Features:
    - Toggle grid (WiFi, BT, etc.)
    - Brightness slider
    - Volume slider
    - Device info
    """
    
    PANEL_WIDTH = 420
    HANDLE_HEIGHT = 24
    TILE_SIZE = 72
    TILE_GAP = 8
    SLIDER_HEIGHT = 40
    
    BG_COLOR = (28, 28, 35)
    SURFACE_COLOR = (40, 40, 52)
    TEXT_PRIMARY = (240, 240, 245)
    TEXT_SECONDARY = (140, 140, 160)
    ACCENT = (80, 140, 255)
    TOGGLE_ON = (80, 140, 255)
    TOGGLE_OFF = (60, 60, 75)
    DIVIDER = (55, 55, 70)
    
    def __init__(self, screen_width: int = 1920, screen_height: int = 1080):
        self._sw = screen_width
        self._sh = screen_height
        self._state: PanelState = PanelState.CLOSED
        self._pull_progress: float = 0.0
        self._drag_y: int = 0
        
        # Toggles
        self._toggles: List[QuickToggle] = [
            QuickToggle("Wi-Fi", "W", True, (80, 140, 255)),
            QuickToggle("Bluetooth", "B", False, (60, 180, 255)),
            QuickToggle("Airplane", "A", False, (255, 140, 60)),
            QuickToggle("Flashlight", "F", False, (255, 200, 60)),
            QuickToggle("Auto-rotate", "R", True, (80, 200, 140)),
            QuickToggle("Do Not Disturb", "D", False, (200, 80, 120)),
        ]
        
        # Sliders
        self._brightness: int = 80
        self._volume: int = 65
        
        # Callbacks
        self._toggle_callbacks: List[Callable] = []
    
    # -- Toggle management -------------------------------------------------
    
    def toggle_setting(self, index: int) -> bool:
        if 0 <= index < len(self._toggles):
            self._toggles[index].enabled = not self._toggles[index].enabled
            for cb in self._toggle_callbacks:
                cb(index, self._toggles[index].enabled)
            return self._toggles[index].enabled
        return False
    
    def on_toggle(self, callback: Callable) -> None:
        self._toggle_callbacks.append(callback)
    
    @property
    def toggles(self) -> List[QuickToggle]:
        return list(self._toggles)
    
    @property
    def brightness(self) -> int:
        return self._brightness
    
    @brightness.setter
    def brightness(self, value: int) -> None:
        self._brightness = max(0, min(100, value))
    
    @property
    def volume(self) -> int:
        return self._volume
    
    @volume.setter
    def volume(self, value: int) -> None:
        self._volume = max(0, min(100, value))
    
    # -- Panel state -------------------------------------------------------
    
    def open(self) -> None:
        self._state = PanelState.OPEN
        self._pull_progress = 1.0
    
    def close(self) -> None:
        self._state = PanelState.CLOSED
        self._pull_progress = 0.0
    
    def toggle_panel(self) -> None:
        if self._state == PanelState.OPEN:
            self.close()
        else:
            self.open()
    
    @property
    def is_open(self) -> bool:
        return self._state == PanelState.OPEN
    
    @property
    def pull_progress(self) -> float:
        return self._pull_progress
    
    # -- Input handling ----------------------------------------------------
    
    def handle_drag_start(self, y: int) -> None:
        if y < self.HANDLE_HEIGHT * 3:
            self._drag_y = y
            self._state = PanelState.OPENING
    
    def handle_drag_move(self, y: int) -> None:
        if self._state == PanelState.OPENING:
            delta = y - self._drag_y
            self._pull_progress = max(0.0, min(1.0, delta / 300))
    
    def handle_drag_end(self, y: int) -> None:
        if self._state == PanelState.OPENING:
            if self._pull_progress > 0.3:
                self.open()
            else:
                self.close()
    
    def handle_tap(self, x: int, y: int) -> str:
        """Handle tap within the panel."""
        if not self.is_open:
            return ""
        
        panel_x = self._sw - self.PANEL_WIDTH
        panel_y = 0
        
        if not (panel_x <= x <= panel_x + self.PANEL_WIDTH):
            self.close()
            return "close"
        
        # Check toggle taps
        grid_y = panel_y + self.HANDLE_HEIGHT + 40
        cols = 3
        for i, toggle in enumerate(self._toggles):
            row = i // cols
            col = i % cols
            tx = panel_x + 16 + col * (self.TILE_SIZE + self.TILE_GAP)
            ty = grid_y + row * (self.TILE_SIZE + self.TILE_GAP)
            
            if tx <= x <= tx + self.TILE_SIZE and ty <= y <= ty + self.TILE_SIZE:
                self.toggle_setting(i)
                return f"toggle:{toggle.label}"
        
        return ""
    
    @property
    def _panel_height(self) -> int:
        header = self.HANDLE_HEIGHT + 40
        rows = (len(self._toggles) + 2) // 3
        grid_h = rows * (self.TILE_SIZE + self.TILE_GAP)
        sliders_h = self.SLIDER_HEIGHT * 2 + 40
        total = header + grid_h + sliders_h + 40
        return int(total * self._pull_progress)
    
    # -- Rendering ---------------------------------------------------------
    
    def render(self) -> Tuple[List[Tuple[int, int, int]], int, int]:
        """Render quick settings to a pixel buffer."""
        w = self.PANEL_WIDTH
        h = self._panel_height
        if h <= 0:
            return [], 0, 0
        
        pixels = [self.BG_COLOR] * (w * h)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < w and 0 <= py < h:
                pixels[py * w + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int]) -> None:
            FONT = _get_small_font()
            glyph = FONT.get(ch, FONT[' '])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        set_pixel(cx + col, cy + row, color)
        
        def draw_text(tx: int, ty: int, text: str, color: Tuple[int, int, int]) -> int:
            cx = tx
            for ch in text[:40]:
                draw_char(cx, ty, ch, color)
                cx += 6
            return cx
        
        def draw_slider(sx: int, sy: int, sw: int, value: int, color: Tuple[int, int, int]) -> None:
            fill_rect(sx, sy + 8, sw, 8, self.DIVIDER)
            fill_w = int(sw * value / 100)
            fill_rect(sx, sy + 8, fill_w, 8, color)
            handle_x = sx + fill_w - 4
            fill_rect(handle_x, sy + 4, 8, 16, color)
        
        # Handle
        handle_w = 40
        fill_rect((w - handle_w) // 2, 4, handle_w, 4, self.DIVIDER)
        
        # Header
        draw_text(16, self.HANDLE_HEIGHT + 8, "Quick Settings", self.TEXT_PRIMARY)
        
        # Toggle grid
        grid_y = self.HANDLE_HEIGHT + 40
        cols = 3
        for i, toggle in enumerate(self._toggles):
            row = i // cols
            col = i % cols
            tx = 16 + col * (self.TILE_SIZE + self.TILE_GAP)
            ty = grid_y + row * (self.TILE_SIZE + self.TILE_GAP)
            
            # Tile background
            tile_color = toggle.color_on if toggle.enabled else self.TOGGLE_OFF
            fill_rect(tx, ty, self.TILE_SIZE, self.TILE_SIZE, tile_color)
            
            # Icon letter (centered)
            icon_x = tx + self.TILE_SIZE // 2 - 3
            icon_y = ty + 12
            draw_char(icon_x, icon_y, toggle.icon_letter[0], self.TEXT_PRIMARY)
            
            # Label
            label = toggle.label[:8]
            lw = len(label) * 6
            draw_text(tx + (self.TILE_SIZE - lw) // 2, ty + 32, label, self.TEXT_PRIMARY)
        
        # Sliders section
        rows = (len(self._toggles) + 2) // 3
        slider_y = grid_y + rows * (self.TILE_SIZE + self.TILE_GAP) + 16
        
        # Brightness
        draw_text(16, slider_y, "Brightness", self.TEXT_SECONDARY)
        draw_slider(16, slider_y + 16, w - 32, self._brightness, (255, 200, 60))
        
        # Volume
        vol_y = slider_y + 44
        draw_text(16, vol_y, "Volume", self.TEXT_SECONDARY)
        draw_slider(16, vol_y + 16, w - 32, self._volume, self.ACCENT)
        
        return pixels, w, h


# ---------------------------------------------------------------------------
# Shared font
# ---------------------------------------------------------------------------

def _get_small_font() -> Dict[str, List[int]]:
    """Shared 5x7 bitmap font."""
    return {
        ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
        '0': [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
        '1': [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
        '2': [0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F],
        '3': [0x0E, 0x11, 0x01, 0x06, 0x01, 0x11, 0x0E],
        '4': [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
        '5': [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
        '6': [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
        '7': [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
        '8': [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
        '9': [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
        'A': [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
        'B': [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
        'C': [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
        'D': [0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E],
        'E': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
        'F': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
        'G': [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
        'H': [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
        'I': [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
        'K': [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
        'L': [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
        'M': [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
        'N': [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11],
        'O': [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        'P': [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
        'Q': [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
        'R': [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
        'S': [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E],
        'T': [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
        'U': [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        'V': [0x11, 0x11, 0x11, 0x11, 0x0A, 0x0A, 0x04],
        'W': [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
        'X': [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
        'Y': [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
        'Z': [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
        'a': [0x00, 0x00, 0x0E, 0x01, 0x0F, 0x11, 0x0F],
        'b': [0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x1E],
        'c': [0x00, 0x00, 0x0E, 0x10, 0x10, 0x11, 0x0E],
        'd': [0x01, 0x01, 0x0D, 0x13, 0x11, 0x11, 0x0F],
        'e': [0x00, 0x00, 0x0E, 0x11, 0x1F, 0x10, 0x0E],
        'f': [0x06, 0x09, 0x08, 0x1C, 0x08, 0x08, 0x08],
        'g': [0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x0E],
        'h': [0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x11],
        'i': [0x04, 0x00, 0x0C, 0x04, 0x04, 0x04, 0x0E],
        'k': [0x10, 0x10, 0x12, 0x14, 0x18, 0x14, 0x12],
        'l': [0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
        'm': [0x00, 0x00, 0x1A, 0x15, 0x15, 0x11, 0x11],
        'n': [0x00, 0x00, 0x16, 0x19, 0x11, 0x11, 0x11],
        'o': [0x00, 0x00, 0x0E, 0x11, 0x11, 0x11, 0x0E],
        'p': [0x00, 0x00, 0x1E, 0x11, 0x1E, 0x10, 0x10],
        'r': [0x00, 0x00, 0x16, 0x19, 0x10, 0x10, 0x10],
        's': [0x00, 0x00, 0x0E, 0x10, 0x0E, 0x01, 0x1E],
        't': [0x10, 0x10, 0x1C, 0x10, 0x10, 0x10, 0x0E],
        'u': [0x00, 0x00, 0x11, 0x11, 0x11, 0x13, 0x0D],
        'v': [0x00, 0x00, 0x11, 0x11, 0x11, 0x0A, 0x04],
        'w': [0x00, 0x00, 0x11, 0x11, 0x15, 0x15, 0x0A],
        'x': [0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11],
        'y': [0x00, 0x00, 0x11, 0x11, 0x0F, 0x01, 0x0E],
        'z': [0x00, 0x00, 0x1F, 0x02, 0x04, 0x08, 0x1F],
        '-': [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
        ':': [0x00, 0x00, 0x04, 0x00, 0x00, 0x04, 0x00],
        '%': [0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03],
        '/': [0x02, 0x02, 0x04, 0x08, 0x08, 0x10, 0x10],
    }


# ---------------------------------------------------------------------------