#!/usr/bin/env python3
"""lock_screen — Nyrqis screen lock screen.

A lock screen that demonstrates the full Nyrqis stack:

- Large clock display with date
- Wallpaper background
- Unlock gesture (click/drag to unlock)
- Notification badges on the lock screen
- Automatic screen timeout

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class LockScreenState:
    """Current state of the lock screen."""
    locked: bool = False
    clock_format: str = "%H:%M"       # 24h
    date_format: str = "%A, %B %d"    # e.g. "Monday, January 15"
    wallpaper_color: Tuple[int, int, int] = (20, 20, 40)
    show_notifications: bool = True
    show_date: bool = True
    unlock_progress: float = 0.0       # 0.0 = locked, 1.0 = unlocked
    swipe_start_x: int = 0
    swipe_start_y: int = 0
    swipe_active: bool = False


class LockScreen:
    """Nyrqis screen lock screen.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session to lock/unlock.
    timeout_seconds : int
        Seconds of inactivity before auto-lock (0 = disabled).
    """

    def __init__(
        self,
        session=None,
        timeout_seconds: int = 300,
    ) -> None:
        self._session = session
        self._timeout = timeout_seconds
        self._state = LockScreenState()
        self._last_activity = time.time()
        self._callbacks: List[Callable] = []
        self._visible = False

    # -- Lock/Unlock --------------------------------------------------

    def lock(self) -> None:
        """Lock the screen."""
        self._state.locked = True
        self._state.unlock_progress = 0.0
        self._visible = True
        self._dispatch("locked")
        if self._session and hasattr(self._session, '_notifications'):
            self._session._notifications.info("Screen locked")

    def unlock(self) -> bool:
        """Attempt to unlock the screen.

        Returns True if the screen was successfully unlocked.
        """
        if not self._state.locked:
            return True
        self._state.locked = False
        self._state.unlock_progress = 0.0
        self._visible = False
        self._last_activity = time.time()
        self._dispatch("unlocked")
        if self._session and hasattr(self._session, '_notifications'):
            self._session._notifications.info("Screen unlocked")
        return True

    def toggle(self) -> bool:
        """Toggle lock state.  Returns new locked state."""
        if self._state.locked:
            self.unlock()
        else:
            self.lock()
        return self._state.locked

    @property
    def locked(self) -> bool:
        return self._state.locked

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Input --------------------------------------------------------

    def handle_swipe_start(self, x: int, y: int) -> None:
        """Start a swipe gesture (for unlock)."""
        if not self._state.locked:
            return
        self._state.swipe_start_x = x
        self._state.swipe_start_y = y
        self._state.swipe_active = True
        self._state.unlock_progress = 0.0

    def handle_swipe_move(self, x: int, y: int) -> None:
        """Update swipe gesture progress."""
        if not self._state.swipe_active:
            return
        # Calculate vertical swipe distance (upward = unlock)
        dy = self._state.swipe_start_y - y
        self._state.unlock_progress = max(0.0, min(1.0, dy / 200.0))

    def handle_swipe_end(self, x: int, y: int) -> bool:
        """End swipe gesture.  Returns True if unlocked."""
        if not self._state.swipe_active:
            return False
        self._state.swipe_active = False
        if self._state.unlock_progress >= 0.8:
            self.unlock()
            return True
        self._state.unlock_progress = 0.0
        return False

    def handle_click(self, x: int, y: int) -> bool:
        """Handle a click on the lock screen.

        For simple unlock: click anywhere and drag up.
        """
        if not self._state.locked:
            return False
        self.handle_swipe_start(x, y)
        return True

    def activity(self) -> None:
        """Record user activity (resets auto-lock timer)."""
        self._last_activity = time.time()

    # -- Auto-lock check ----------------------------------------------

    def check_timeout(self) -> bool:
        """Check if auto-lock should trigger.

        Returns True if the screen was auto-locked.
        """
        if self._timeout <= 0 or self._state.locked:
            return False
        elapsed = time.time() - self._last_activity
        if elapsed >= self._timeout:
            self.lock()
            return True
        return False

    # -- Time ---------------------------------------------------------

    @property
    def current_time(self) -> str:
        """Current time formatted for display."""
        return time.strftime(self._state.clock_format)

    @property
    def current_date(self) -> str:
        """Current date formatted for display."""
        return time.strftime(self._state.date_format)

    @property
    def state(self) -> LockScreenState:
        return self._state

    # -- Render to PIL ------------------------------------------------

    def render(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Any:
        """Render the lock screen as a PIL Image."""
        if not self._state.locked:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        img = Image.new("RGB", (screen_width, screen_height),
                        self._state.wallpaper_color)
        draw = ImageDraw.Draw(img)

        # Load fonts
        try:
            font_clock = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
            font_date = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            font_hint = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        except (OSError, IOError):
            font_clock = font_date = font_hint = ImageFont.load_default()

        # Clock (centered, upper third)
        time_str = self.current_time
        bbox = draw.textbbox((0, 0), time_str, font=font_clock)
        tw = bbox[2] - bbox[0]
        tx = (screen_width - tw) // 2
        ty = screen_height // 3 - 40
        draw.text((tx, ty), time_str, fill=(255, 255, 255), font=font_clock)

        # Date
        if self._state.show_date:
            date_str = self.current_date
            bbox = draw.textbbox((0, 0), date_str, font=font_date)
            dw = bbox[2] - bbox[0]
            dx = (screen_width - dw) // 2
            draw.text((dx, ty + 80), date_str,
                      fill=(180, 180, 180), font=font_date)

        # Unlock hint
        hint = "Swipe up to unlock"
        if self._state.unlock_progress > 0:
            hint = f"Unlocking... {int(self._state.unlock_progress * 100)}%"
        bbox = draw.textbbox((0, 0), hint, font=font_hint)
        hw = bbox[2] - bbox[0]
        hx = (screen_width - hw) // 2
        hy = screen_height - 120
        draw.text((hx, hy), hint, fill=(150, 150, 150), font=font_hint)

        # Unlock progress bar
        if self._state.unlock_progress > 0:
            bar_w = 300
            bar_h = 4
            bar_x = (screen_width - bar_w) // 2
            bar_y = hy + 30
            draw.rectangle(
                [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
                fill=(60, 60, 60))
            fill_w = int(bar_w * self._state.unlock_progress)
            draw.rectangle(
                [bar_x, bar_y, bar_x + fill_w, bar_y + bar_h],
                fill=(100, 149, 237))

        # Nyrqis logo
        logo = "Nyrqis"
        bbox = draw.textbbox((0, 0), logo, font=font_hint)
        lw = bbox[2] - bbox[0]
        draw.text(((screen_width - lw) // 2, 40), logo,
                  fill=(100, 100, 100), font=font_hint)

        return img

    # -- Callbacks ----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type)
            except Exception:
                pass


__all__ = ["LockScreen", "LockScreenState"]
