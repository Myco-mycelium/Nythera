#!/usr/bin/env python3
"""power_menu — Nyrqis power menu.

A power menu that demonstrates the full Nyrqis stack:

- Shutdown, restart, sleep, logout, lock options
- Confirmation dialog
- Visual overlay rendering
- Keyboard shortcuts (power button)

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class PowerOption:
    """A single power menu option."""
    id: str
    label: str
    icon: str
    description: str = ""
    dangerous: bool = False     # Requires confirmation
    action: Optional[str] = None


POWER_OPTIONS = [
    PowerOption(
        id="lock", label="Lock", icon="🔒",
        description="Lock the screen",
        action="lock"),
    PowerOption(
        id="logout", label="Log Out", icon="↩",
        description="End your session",
        action="logout"),
    PowerOption(
        id="sleep", label="Sleep", icon="💤",
        description="Suspend to RAM",
        action="sleep"),
    PowerOption(
        id="restart", label="Restart", icon="↻",
        description="Restart the system",
        dangerous=True, action="restart"),
    PowerOption(
        id="shutdown", label="Shut Down", icon="⏻",
        description="Turn off the system",
        dangerous=True, action="shutdown"),
]


class PowerMenu:
    """Nyrqis power menu.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session.
    """

    def __init__(self, session=None) -> None:
        self._session = session
        self._visible = False
        self._selected_index: int = 0
        self._confirming: bool = False
        self._confirm_option: Optional[PowerOption] = None
        self._callbacks: List[Callable] = []

    # -- API ----------------------------------------------------------

    def show(self) -> None:
        """Show the power menu."""
        self._visible = True
        self._selected_index = 0
        self._confirming = False
        self._confirm_option = None
        self._dispatch("shown")

    def hide(self) -> None:
        """Hide the power menu."""
        self._visible = False
        self._confirming = False
        self._confirm_option = None
        self._dispatch("hidden")

    def toggle(self) -> bool:
        if self._visible:
            self.hide()
        else:
            self.show()
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def options(self) -> List[PowerOption]:
        return list(POWER_OPTIONS)

    @property
    def selected(self) -> Optional[PowerOption]:
        if 0 <= self._selected_index < len(POWER_OPTIONS):
            return POWER_OPTIONS[self._selected_index]
        return None

    @property
    def confirming(self) -> bool:
        return self._confirming

    @property
    def confirm_option(self) -> Optional[PowerOption]:
        return self._confirm_option

    # -- Navigation ---------------------------------------------------

    def navigate_up(self) -> None:
        self._selected_index = (
            (self._selected_index - 1) % len(POWER_OPTIONS))

    def navigate_down(self) -> None:
        self._selected_index = (
            (self._selected_index + 1) % len(POWER_OPTIONS))

    # -- Execution ----------------------------------------------------

    def execute(self) -> Optional[PowerOption]:
        """Execute the selected power option.

        Dangerous options require a second call to confirm.
        """
        option = self.selected
        if option is None:
            return None

        if option.dangerous and not self._confirming:
            self._confirming = True
            self._confirm_option = option
            self._dispatch("confirming")
            return None

        self._confirming = False
        self._confirm_option = None
        self._perform_action(option)
        self.hide()
        return option

    def cancel(self) -> None:
        """Cancel a confirmation."""
        self._confirming = False
        self._confirm_option = None
        self._dispatch("cancelled")

    def _perform_action(self, option: PowerOption) -> None:
        """Perform the actual power action."""
        self._log(f"Executing: {option.label}")

        if self._session is None:
            return

        action = option.action
        if action == "lock":
            if hasattr(self._session, '_lock_screen'):
                self._session._lock_screen.lock()
            self._session._notifications.info("Locked", "Screen locked")

        elif action == "logout":
            # Minimize all windows
            for w in self._session.windows:
                if w.visible and not w.minimized:
                    self._session.minimize_window(w.id)
            self._session._notifications.info("Logged out", "Session ended")

        elif action == "sleep":
            self._session._notifications.info("Sleeping", "Suspending to RAM...")

        elif action == "restart":
            self._session._notifications.info("Restarting", "System restart...")

        elif action == "shutdown":
            self._session._notifications.info("Shutting down", "System power off...")

        for cb in self._callbacks:
            try:
                cb("executed", option)
            except Exception:
                pass

    # -- Render to PIL ------------------------------------------------

    def render(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Any:
        """Render the power menu overlay."""
        if not self._visible:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        img = Image.new("RGBA", (screen_width, screen_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        except (OSError, IOError):
            font = font_large = font_small = ImageFont.load_default()

        # Overlay
        draw.rectangle([0, 0, screen_width, screen_height],
                       fill=(0, 0, 0, 150))

        # Menu box
        box_w = 320
        box_h = 360
        box_x = (screen_width - box_w) // 2
        box_y = (screen_height - box_h) // 2

        draw.rounded_rectangle(
            [box_x, box_y, box_x + box_w, box_y + box_h],
            radius=16, fill=(40, 40, 40, 240), outline=(80, 80, 80))

        # Title
        title = "Power" if not self._confirming else "Confirm?"
        bbox = draw.textbbox((0, 0), title, font=font_large)
        tw = bbox[2] - bbox[0]
        draw.text(((box_x + (box_w - tw) // 2, box_y + 16)),
                  title, fill=(230, 230, 230), font=font_large)

        if self._confirming and self._confirm_option:
            # Confirmation dialog
            msg = f"{self._confirm_option.label}?"
            bbox = draw.textbbox((0, 0), msg, font=font)
            mw = bbox[2] - bbox[0]
            draw.text(((box_x + (box_w - mw) // 2, box_y + 50)),
                      msg, fill=(200, 200, 200), font=font)

            # Yes/No buttons
            btn_y = box_y + box_h - 80
            # Yes (dangerous)
            draw.rounded_rectangle(
                [box_x + 30, btn_y, box_x + box_w // 2 - 15, btn_y + 40],
                radius=8, fill=(180, 60, 60))
            draw.text((box_x + 55, btn_y + 10), "Yes",
                      fill=(255, 255, 255), font=font)
            # No
            draw.rounded_rectangle(
                [box_x + box_w // 2 + 15, btn_y,
                 box_x + box_w - 30, btn_y + 40],
                radius=8, fill=(60, 60, 60))
            draw.text((box_x + box_w // 2 + 45, btn_y + 10), "No",
                      fill=(200, 200, 200), font=font)
        else:
            # Power options
            for i, option in enumerate(POWER_OPTIONS):
                oy = box_y + 50 + i * 56
                is_selected = (i == self._selected_index)

                if is_selected:
                    draw.rounded_rectangle(
                        [box_x + 12, oy, box_x + box_w - 12, oy + 48],
                        radius=10, fill=(60, 80, 120))

                # Icon
                draw.text((box_x + 24, oy + 12), option.icon,
                          fill=(200, 200, 200), font=font_large)

                # Label
                draw.text((box_x + 56, oy + 8), option.label,
                          fill=(230, 230, 230), font=font)

                # Description
                draw.text((box_x + 56, oy + 28), option.description,
                          fill=(130, 130, 130), font=font_small)

        return img

    # -- Callbacks ----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, None)
            except Exception:
                pass

    def _log(self, msg: str) -> None:
        import logging
        logging.getLogger(__name__).info("[PowerMenu] %s", msg)


__all__ = ["PowerMenu", "PowerOption"]
