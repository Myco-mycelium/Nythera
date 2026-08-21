#!/usr/bin/env python3
"""window_switcher — Alt+Tab window switcher for Nyrqis.

A visual overlay that shows all open windows and allows cycling
between them with Alt+Tab (forward) and Alt+Shift+Tab (backward).

Architecture:
    The window switcher is a transparent overlay rendered on top of
    the desktop.  It shows thumbnail previews of each window with
    the window title.  Releasing Alt switches to the selected window.

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class SwitcherEntry:
    """A window entry in the Alt+Tab switcher."""
    window_id: str
    title: str
    icon: str = "□"
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    focused: bool = False


class WindowSwitcher:
    """Alt+Tab window switcher overlay.

    Parameters
    ----------
    session : DesktopSession
        The desktop session to switch windows in.
    """

    # Layout constants
    THUMB_WIDTH = 200
    THUMB_HEIGHT = 140
    THUMB_GAP = 16
    THUMB_PADDING = 12
    OVERLAY_HEIGHT = 200

    def __init__(self, session) -> None:
        self._session = session
        self._active = False
        self._entries: List[SwitcherEntry] = []
        self._selected_index: int = 0
        self._direction: int = 1  # +1 = forward, -1 = backward
        self._start_time: float = 0.0
        self._callbacks: List[Callable] = []

    # -- API ----------------------------------------------------------

    def start(self, backward: bool = False) -> bool:
        """Start the window switcher.

        Call this on Alt key down.  Returns True if the switcher
        was started (there are windows to switch between).
        """
        windows = [w for w in self._session.windows
                   if w.visible and not w.minimized]
        if len(windows) < 2:
            return False

        self._active = True
        self._start_time = time.monotonic()
        self._direction = -1 if backward else 1

        # Build entries
        self._entries = []
        for w in windows:
            self._entries.append(SwitcherEntry(
                window_id=w.id,
                title=w.title or w.id,
                icon="□",
                x=w.x, y=w.y,
                width=w.width, height=w.height,
                focused=w.focused,
            ))

        # Start selection after the focused window
        focused_idx = 0
        for i, e in enumerate(self._entries):
            if e.focused:
                focused_idx = i
                break
        self._selected_index = (focused_idx + self._direction) % len(self._entries)

        self._dispatch("started")
        return True

    def stop(self) -> Optional[str]:
        """Stop the switcher and return the selected window ID.

        Call this on Alt key up.  Returns the window ID to focus,
        or None if no switch was made.
        """
        if not self._active:
            return None

        self._active = False
        selected = None
        if self._entries:
            entry = self._entries[self._selected_index]
            selected = entry.window_id

        self._entries = []
        self._dispatch("stopped")
        return selected

    def cycle(self, backward: bool = False) -> None:
        """Cycle to the next/previous window in the switcher.

        Call this on Tab key press while Alt is held.
        """
        if not self._active or not self._entries:
            return

        direction = -1 if backward else 1
        self._selected_index = (
            (self._selected_index + direction) % len(self._entries))
        self._dispatch("cycled")

    @property
    def active(self) -> bool:
        return self._active

    @property
    def selected(self) -> Optional[SwitcherEntry]:
        if not self._entries:
            return None
        return self._entries[self._selected_index]

    @property
    def entries(self) -> List[SwitcherEntry]:
        return list(self._entries)

    # -- Layout -------------------------------------------------------

    def layout(self, screen_width: int = 1920, screen_height: int = 1080) -> None:
        """Compute positions for the switcher overlay entries."""
        if not self._active:
            return

        total_w = (len(self._entries) * (self.THUMB_WIDTH + self.THUMB_GAP)
                   - self.THUMB_GAP + 2 * self.THUMB_PADDING)
        start_x = (screen_width - total_w) // 2
        start_y = (screen_height - self.OVERLAY_HEIGHT) // 2

        for i, entry in enumerate(self._entries):
            entry.x = start_x + i * (self.THUMB_WIDTH + self.THUMB_GAP)
            entry.y = start_y

    # -- Render to PIL ------------------------------------------------

    def render(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Any:
        """Render the switcher overlay as a transparent PIL Image."""
        if not self._active:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        self.layout(screen_width, screen_height)
        img = Image.new("RGBA", (screen_width, screen_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
            font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        except (OSError, IOError):
            font = font_bold = ImageFont.load_default()

        # Semi-transparent background overlay
        draw.rectangle(
            [0, self._entries[0].y - 20 if self._entries else 0,
             screen_width,
             (self._entries[0].y + self.OVERLAY_HEIGHT + 20
              if self._entries else screen_height)],
            fill=(0, 0, 0, 140))

        for i, entry in enumerate(self._entries):
            is_selected = (i == self._selected_index)
            self._render_entry(
                draw, entry, is_selected, font, font_bold)

        return img

    def _render_entry(
        self, draw, entry: SwitcherEntry, is_selected: bool,
        font, font_bold,
    ) -> None:
        """Render a single window thumbnail."""
        x, y = entry.x, entry.y
        w, h = self.THUMB_WIDTH, self.THUMB_HEIGHT

        # Background
        if is_selected:
            bg = (80, 120, 180, 220)
            border = (100, 160, 240)
        else:
            bg = (50, 50, 50, 200)
            border = (80, 80, 80)

        draw.rectangle([x, y, x + w, y + h], fill=bg, outline=border)

        # Window preview (miniature rectangle)
        preview_margin = 8
        px = x + preview_margin
        py = y + preview_margin
        pw = w - 2 * preview_margin
        ph = h - 2 * preview_margin - 24  # Leave room for title
        draw.rectangle(
            [px, py, px + pw, py + ph],
            fill=(30, 30, 30, 180), outline=(60, 60, 60))

        # Mini window chrome
        draw.rectangle(
            [px, py, px + pw, py + 14],
            fill=(40, 40, 40, 200))
        draw.text((px + 4, py + 1), entry.title[:15],
                  fill=(200, 200, 200), font=font)

        # Title below
        title = entry.title[:20]
        bbox = draw.textbbox((0, 0), title, font=font_bold)
        tw = bbox[2] - bbox[0]
        tx = x + (w - tw) // 2
        draw.text((tx, y + h - 20), title,
                  fill=(230, 230, 230), font=font_bold)

    # -- Callbacks ----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        """Register a callback for switcher events."""
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, self._selected_index)
            except Exception:
                pass


__all__ = ["WindowSwitcher", "SwitcherEntry"]
