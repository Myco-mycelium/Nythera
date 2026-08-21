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
