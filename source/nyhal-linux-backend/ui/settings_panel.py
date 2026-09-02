#!/usr/bin/env python3
"""Settings panel component for the Nyrqis desktop.

Provides a visual settings panel with:
- Volume slider with icon and percentage
- Brightness slider with icon and percentage
- Theme selector (Eclipse, Solar, custom)
- WiFi/Bluetooth toggles
- About section with system info
- Pixel rendering for display in desktop windows
"""

from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Theme definitions
# ---------------------------------------------------------------------------

@dataclass
class Theme:
    """A color theme for the desktop."""
    name: str
    bg: Tuple[int, int, int] = (30, 30, 42)
    surface: Tuple[int, int, int] = (40, 40, 55)
    accent: Tuple[int, int, int] = (80, 140, 255)
    text: Tuple[int, int, int] = (220, 220, 230)
    text_dim: Tuple[int, int, int] = (140, 140, 160)
    border: Tuple[int, int, int] = (60, 60, 80)
    success: Tuple[int, int, int] = (60, 200, 120)
    warning: Tuple[int, int, int] = (255, 180, 60)
    error: Tuple[int, int, int] = (220, 60, 60)


# Built-in themes
THEME_ECLIPSE = Theme(
    name="Eclipse",
    bg=(24, 24, 32),
    surface=(35, 35, 48),
    accent=(80, 140, 255),
    text=(200, 200, 220),
    text_dim=(120, 120, 140),
    border=(60, 60, 80),
)

THEME_SOLAR = Theme(
    name="Solar",
    bg=(0, 43, 54),
    surface=(7, 54, 66),
    accent=(38, 139, 210),
    text=(131, 148, 150),
    text_dim=(101, 123, 131),
    border=(88, 110, 117),
)

THEME_DRACULA = Theme(
    name="Dracula",
    bg=(40, 42, 54),
    surface=(68, 71, 90),
    accent=(189, 147, 249),
    text=(248, 248, 242),
    text_dim=(168, 173, 198),
    border=(98, 114, 164),
)

BUILTIN_THEMES = [THEME_ECLIPSE, THEME_SOLAR, THEME_DRACULA]


# ---------------------------------------------------------------------------
# Toggle state
# ---------------------------------------------------------------------------

@dataclass
class Toggle:
    """A boolean toggle setting."""
    label: str
    enabled: bool = False
    icon: str = ""


# ---------------------------------------------------------------------------
# Settings panel
# ---------------------------------------------------------------------------

class SettingsPanel:
    """Visual settings panel component.
    
    Parameters
    ----------
    width : int
        Panel width in pixels.
    height : int
        Panel height in pixels.
    """
    
    # Layout constants
    PADDING = 20
    SECTION_HEIGHT = 180
    SLIDER_HEIGHT = 36
    TOGGLE_HEIGHT = 36
    THEME_ITEM_HEIGHT = 40
    
    def __init__(self, width: int = 400, height: int = 700):
        self._width = width
        self._height = height
        
        # Settings state
        self._volume: int = 75
        self._brightness: int = 100
        self._selected_theme: int = 0  # Index into BUILTIN_THEMES
        self._toggles: List[Toggle] = [
            Toggle("WiFi", True, "📶"),
            Toggle("Bluetooth", False, "🔵"),
            Toggle("Do Not Disturb", False, "🌙"),
            Toggle("Night Light", False, "🌅"),
        ]
        
        # Interaction state
        self._hover_section: int = -1
        self._dragging: str = ""  # "volume" or "brightness"
        self._scroll_offset: int = 0
        
        # System info
        self._hostname = platform.node() or "nyrqis-desktop"
        self._kernel = platform.release() or "6.8.0-nyrqis"
        self._version = "Nyrqis OS v0.25.0"
    
    # -- Properties --------------------------------------------------------
    
    @property
    def volume(self) -> int:
        return self._volume
    
    @volume.setter
    def volume(self, value: int) -> None:
        self._volume = max(0, min(100, value))
    
    @property
    def brightness(self) -> int:
        return self._brightness
    
    @brightness.setter
    def brightness(self, value: int) -> None:
        self._brightness = max(0, min(100, value))
    
    @property
    def selected_theme(self) -> Theme:
        return BUILTIN_THEMES[self._selected_theme]
    
    @property
    def toggles(self) -> List[Toggle]:
        return list(self._toggles)
    
    # -- Settings ----------------------------------------------------------
    
    def set_volume(self, value: int) -> None:
        self.volume = value
    
    def set_brightness(self, value: int) -> None:
        self.brightness = value
    
    def select_theme(self, index: int) -> None:
        if 0 <= index < len(BUILTIN_THEMES):
            self._selected_theme = index
    
    def cycle_theme(self) -> Theme:
        self._selected_theme = (self._selected_theme + 1) % len(BUILTIN_THEMES)
        return self.selected_theme
    
    def toggle_setting(self, index: int) -> bool:
        if 0 <= index < len(self._toggles):
            self._toggles[index].enabled = not self._toggles[index].enabled
            return self._toggles[index].enabled
        return False
    
    # -- Keyboard input ----------------------------------------------------
    
    def handle_key(self, key: str, modifiers: Optional[Dict[str, bool]] = None) -> str:
        """Handle a keyboard event.
        
        Returns action name or "" if unhandled.
        """
        mods = modifiers or {}
        
        if key == "Up":
            self._volume = min(100, self._volume + 5)
            return "volume"
        elif key == "Down":
            self._volume = max(0, self._volume - 5)
            return "volume"
        elif key == "Right":
            self._brightness = min(100, self._brightness + 5)
            return "brightness"
        elif key == "Left":
            self._brightness = max(0, self._brightness - 5)
            return "brightness"
        elif key == "t" or key == "T":
            self.cycle_theme()
            return "theme"
        elif key in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
            idx = int(key) - 1
            if idx < len(self._toggles):
                self.toggle_setting(idx)
                return "toggle"
        elif key == "w":
            if self._toggles:
                self._toggles[0].enabled = not self._toggles[0].enabled
                return "toggle"
        elif key == "b":
            if len(self._toggles) > 1:
                self._toggles[1].enabled = not self._toggles[1].enabled
                return "toggle"
        
        return ""
    
    # -- Rendering ---------------------------------------------------------
    
    def render(self) -> Tuple[List[Tuple[int, int, int]], int, int]:
        """Render the settings panel to a pixel buffer.
        
        Returns (pixels, width, height) where pixels is a flat list of
        (r, g, b) tuples in row-major order.
        """
        w = self._width
        h = self._height
        theme = self.selected_theme
        
        pixels = [theme.bg] * (w * h)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < w and 0 <= py < h:
                pixels[py * w + px] = color
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int]) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int]) -> None:
            FONT = {
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
                '%': [0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03],
                ':': [0x00, 0x00, 0x04, 0x00, 0x00, 0x04, 0x00],
                '-': [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
                '/': [0x02, 0x02, 0x04, 0x08, 0x08, 0x10, 0x10],
                ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            }
            glyph = FONT.get(ch, FONT[' '])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        set_pixel(cx + col, cy + row, color)
        
        def draw_text(tx: int, ty: int, text: str, color: Tuple[int, int, int]) -> int:
            cx = tx
            for ch in text:
                draw_char(cx, ty, ch, color)
                cx += 6
            return cx
        
        def draw_slider(sx: int, sy: int, sw: int, value: int, color: Tuple[int, int, int]) -> None:
            """Draw a slider bar."""
            # Track background
            fill_rect(sx, sy + 8, sw, 8, theme.border)
            # Track fill
            fill_w = int(sw * value / 100)
            fill_rect(sx, sy + 8, fill_w, 8, color)
            # Handle
            handle_x = sx + fill_w - 4
            fill_rect(handle_x, sy + 4, 8, 16, color)
            # Handle border
            fill_rect(handle_x, sy + 4, 8, 1, theme.text)
            fill_rect(handle_x, sy + 19, 8, 1, theme.text)
        
        def draw_toggle(tx: int, ty: int, toggle: Toggle) -> None:
            """Draw a toggle switch."""
            # Track
            track_w = 40
            track_h = 20
            track_color = theme.accent if toggle.enabled else theme.border
            fill_rect(tx, ty + 4, track_w, track_h, track_color)
            
            # Handle
            handle_x = tx + (track_w - 16) if toggle.enabled else tx
            fill_rect(handle_x + 2, ty + 6, 16, 16, theme.text)
        
        # === Header ===
        fill_rect(0, 0, w, 60, theme.surface)
        fill_rect(0, 59, w, 1, theme.border)
        draw_text(self.PADDING, 20, "Settings", theme.text)
        
        cy = 70
        
        # === Volume Section ===
        fill_rect(0, cy, w, self.SECTION_HEIGHT, theme.surface)
        fill_rect(0, cy + self.SECTION_HEIGHT - 1, w, 1, theme.border)
        
        draw_text(self.PADDING, cy + 12, "Volume", theme.text)
        vol_text = f"{self._volume}%"
        draw_text(w - self.PADDING - 40, cy + 12, vol_text, theme.accent)
        
        # Volume icon (speaker shape)
        icon_x = self.PADDING
        icon_y = cy + 36
        fill_rect(icon_x, icon_y + 2, 4, 8, theme.text_dim)
        fill_rect(icon_x + 4, icon_y, 4, 12, theme.text_dim)
        
        # Volume slider
        draw_slider(self.PADDING + 20, cy + 32, w - self.PADDING * 2 - 60, 
                   self._volume, theme.accent)
        
        # Volume levels (mute, 25, 50, 75, 100)
        labels = ["0", "25", "50", "75", "100"]
        for i, label in enumerate(labels):
            lx = self.PADDING + 20 + (w - self.PADDING * 2 - 60) * i // 4
            draw_text(lx, cy + 60, label, theme.text_dim)
        
        cy += self.SECTION_HEIGHT
        
        # === Brightness Section ===
        fill_rect(0, cy, w, self.SECTION_HEIGHT, theme.bg)
        fill_rect(0, cy + self.SECTION_HEIGHT - 1, w, 1, theme.border)
        
        draw_text(self.PADDING, cy + 12, "Brightness", theme.text)
        bright_text = f"{self._brightness}%"
        draw_text(w - self.PADDING - 40, cy + 12, bright_text, theme.warning)
        
        # Brightness icon (sun shape)
        icon_x = self.PADDING + 2
        icon_y = cy + 34
        fill_rect(icon_x + 4, icon_y + 2, 4, 8, theme.warning)
        fill_rect(icon_x + 2, icon_y + 4, 8, 4, theme.warning)
        
        # Brightness slider
        draw_slider(self.PADDING + 20, cy + 32, w - self.PADDING * 2 - 60,
                   self._brightness, theme.warning)
        
        # Brightness labels
        for i, label in enumerate(["0", "25", "50", "75", "100"]):
            lx = self.PADDING + 20 + (w - self.PADDING * 2 - 60) * i // 4
            draw_text(lx, cy + 60, label, theme.text_dim)
        
        cy += self.SECTION_HEIGHT
        
        # === Theme Section ===
        fill_rect(0, cy, w, 160, theme.surface)
        fill_rect(0, cy + 159, w, 1, theme.border)
        
        draw_text(self.PADDING, cy + 12, "Theme", theme.text)
        draw_text(w - self.PADDING - 80, cy + 12, "[T] cycle", theme.text_dim)
        
        for i, t in enumerate(BUILTIN_THEMES):
            item_y = cy + 40 + i * self.THEME_ITEM_HEIGHT
            
            # Selection indicator
            if i == self._selected_theme:
                fill_rect(self.PADDING, item_y, w - self.PADDING * 2, 32, theme.accent)
            
            # Theme preview colors
            preview_x = self.PADDING + 4
            fill_rect(preview_x, item_y + 8, 16, 16, t.bg)
            fill_rect(preview_x + 18, item_y + 8, 16, 16, t.surface)
            fill_rect(preview_x + 36, item_y + 8, 16, 16, t.accent)
            
            # Theme name
            name_color = theme.text if i == self._selected_theme else theme.text_dim
            draw_text(preview_x + 60, item_y + 10, t.name, name_color)
        
        cy += 160
        
        # === Toggles Section ===
        fill_rect(0, cy, w, len(self._toggles) * self.TOGGLE_HEIGHT + 50, theme.bg)
        fill_rect(0, cy + len(self._toggles) * self.TOGGLE_HEIGHT + 49, w, 1, theme.border)
        
        draw_text(self.PADDING, cy + 12, "Quick Settings", theme.text)
        cy += 40
        
        for i, toggle in enumerate(self._toggles):
            ty = cy + i * self.TOGGLE_HEIGHT
            
            draw_text(self.PADDING, ty + 10, toggle.label, theme.text)
            draw_toggle(w - self.PADDING - 50, ty + 2, toggle)
            
            # Status text
            status = "On" if toggle.enabled else "Off"
            status_color = theme.success if toggle.enabled else theme.text_dim
            draw_text(w - self.PADDING - 60, ty + 10, status, status_color)
        
        cy += len(self._toggles) * self.TOGGLE_HEIGHT + 20
        
        # === About Section ===
        fill_rect(0, cy, w, 120, theme.surface)
        fill_rect(0, cy + 119, w, 1, theme.border)
        
        draw_text(self.PADDING, cy + 12, "About", theme.text)
        draw_text(self.PADDING, cy + 40, self._version, theme.accent)
        draw_text(self.PADDING, cy + 58, f"Host: {self._hostname}", theme.text_dim)
        draw_text(self.PADDING, cy + 74, f"Kernel: {self._kernel}", theme.text_dim)
        draw_text(self.PADDING, cy + 90, f"Theme: {theme.name}", theme.text_dim)
        
        return pixels, w, h
    
    def render_to_rgb(self) -> Tuple[bytes, int, int]:
        """Render to raw RGB bytes."""
        pixels, width, height = self.render()
        buf = bytearray(width * height * 3)
        i = 0
        for r, g, b in pixels:
            buf[i] = r
            buf[i+1] = g
            buf[i+2] = b
            i += 3
        return bytes(buf), width, height
    
    def __repr__(self) -> str:
        return (
            f"SettingsPanel(volume={self._volume}, "
            f"brightness={self._brightness}, "
            f"theme='{self.selected_theme.name}')"
        )
