#!/usr/bin/env python3
"""Task switcher (Alt+Tab) for the Nyrqis desktop.

Features:
- Window thumbnails with labels
- Keyboard navigation (Tab, arrows, Enter, Escape)
- Live preview highlighting
- Workspace-aware filtering
- Smooth show/hide transitions
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Switcher entry
# ---------------------------------------------------------------------------

@dataclass
class SwitcherEntry:
    """An entry in the task switcher."""
    window_id: str
    title: str
    app_name: str = ""
    workspace: int = 0
    icon_color: Tuple[int, int, int] = (80, 140, 255)
    thumbnail_color: Tuple[int, int, int] = (35, 35, 48)
    focused: bool = False


# ---------------------------------------------------------------------------
# Task switcher
# ---------------------------------------------------------------------------

class TaskSwitcher:
    """Alt+Tab task switcher with thumbnails.
    
    Parameters
    ----------
    screen_width : int
        Screen width.
    screen_height : int
        Screen height.
    """
    
    # Layout
    THUMB_WIDTH = 240
    THUMB_HEIGHT = 150
    THUMB_GAP = 16
    THUMB_PADDING = 12
    LABEL_HEIGHT = 24
    PANEL_HEIGHT = 220
    PANEL_RADIUS = 16
    
    # Colors
    BG_COLOR = (28, 28, 35, 230)
    THUMB_BG = (40, 40, 55)
    THUMB_ACTIVE_BG = (60, 60, 80)
    THUMB_BORDER = (70, 70, 90)
    THUMB_ACTIVE_BORDER = (80, 140, 255)
    TEXT_PRIMARY = (240, 240, 245)
    TEXT_SECONDARY = (140, 140, 160)
    ACCENT = (80, 140, 255)
    
    def __init__(self, screen_width: int = 1920, screen_height: int = 1080):
        self._sw = screen_width
        self._sh = screen_height
        
        self._entries: List[SwitcherEntry] = []
        self._selected_index: int = 0
        self._visible: bool = False
        self._active_workspace: int = 0
        
        # Animation
        self._alpha: float = 0.0
        self._show_time: float = 0.0
        self._animating: bool = False
    
    # -- State management --------------------------------------------------
    
    def show(self, entries: List[SwitcherEntry], start_index: int = 0) -> None:
        """Show the task switcher."""
        self._entries = entries
        self._selected_index = start_index % len(entries) if entries else 0
        self._visible = True
        self._alpha = 0.0
        self._show_time = time.time()
        self._animating = True
    
    def hide(self) -> None:
        """Hide the task switcher."""
        self._visible = False
        self._animating = True
    
    def set_entries(self, entries: List[SwitcherEntry]) -> None:
        """Update the entry list."""
        self._entries = entries
        if self._selected_index >= len(entries):
            self._selected_index = max(0, len(entries) - 1)
    
    def set_workspace(self, workspace: int) -> None:
        """Filter entries to a specific workspace."""
        self._active_workspace = workspace
    
    @property
    def is_visible(self) -> bool:
        return self._visible
    
    @property
    def selected_index(self) -> int:
        return self._selected_index
    
    @property
    def selected_entry(self) -> Optional[SwitcherEntry]:
        if 0 <= self._selected_index < len(self._entries):
            return self._entries[self._selected_index]
        return None
    
    @property
    def entries(self) -> List[SwitcherEntry]:
        return list(self._entries)
    
    @property
    def alpha(self) -> float:
        return self._alpha
    
    # -- Navigation --------------------------------------------------------
    
    def next(self) -> Optional[SwitcherEntry]:
        """Move to the next entry."""
        if self._entries:
            self._selected_index = (self._selected_index + 1) % len(self._entries)
            return self._entries[self._selected_index]
        return None
    
    def prev(self) -> Optional[SwitcherEntry]:
        """Move to the previous entry."""
        if self._entries:
            self._selected_index = (self._selected_index - 1) % len(self._entries)
            return self._entries[self._selected_index]
        return None
    
    def select(self) -> Optional[SwitcherEntry]:
        """Confirm selection and return the chosen entry."""
        entry = self.selected_entry
        self.hide()
        return entry
    
    # -- Keyboard handling -------------------------------------------------
    
    def handle_key(self, key: str, modifiers: Optional[Dict[str, bool]] = None) -> str:
        """Handle keyboard input during task switching."""
        mods = modifiers or {}
        
        if not self._visible:
            return ""
        
        if key == "Tab" and not mods.get("shift"):
            self.next()
            return "next"
        elif key == "Tab" and mods.get("shift"):
            self.prev()
            return "prev"
        elif key == "Right":
            self.next()
            return "next"
        elif key == "Left":
            self.prev()
            return "prev"
        elif key in ("Enter", "Return", "space"):
            entry = self.select()
            return f"select:{entry.window_id}" if entry else "select:"
        elif key == "Escape":
            self.hide()
            return "cancel"
        
        return ""
    
    # -- Animation ---------------------------------------------------------
    
    def update(self) -> bool:
        """Update animation state. Returns True if still animating."""
        if not self._animating:
            return False
        
        elapsed = time.time() - self._show_time
        
        if self._visible:
            # Fade in
            self._alpha = min(1.0, elapsed * 5)  # 200ms fade in
        else:
            # Fade out
            self._alpha = max(0.0, self._alpha - 0.1)
            if self._alpha <= 0:
                self._animating = False
                return False
        
        return self._animating
    
    # -- Rendering ---------------------------------------------------------
    
    def render(self) -> Tuple[List[Tuple[int, int, int]], int, int]:
        """Render the task switcher to a pixel buffer."""
        if not self._visible or not self._entries or self._alpha <= 0:
            return [], 0, 0
        
        # Calculate panel dimensions
        n = len(self._entries)
        panel_w = min(
            self._sw - 40,
            n * (self.THUMB_WIDTH + self.THUMB_GAP) + self.THUMB_PADDING * 2
        )
        panel_h = self.PANEL_HEIGHT
        panel_x = (self._sw - panel_w) // 2
        panel_y = (self._sh - panel_h) // 2
        
        w = panel_w
        h = panel_h
        pixels = [(0, 0, 0)] * (w * h)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < w and 0 <= py < h:
                pixels[py * w + px] = color
        
        def blend_pixel(px: int, py: int, color: Tuple[int, int, int], alpha: float) -> None:
            if 0 <= px < w and 0 <= py < h:
                old = pixels[py * w + px]
                a = alpha * self._alpha
                r = int(old[0] * (1 - a) + color[0] * a)
                g = int(old[1] * (1 - a) + color[1] * a)
                b = int(old[2] * (1 - a) + color[2] * a)
                pixels[py * w + px] = (r, g, b)
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int], alpha: float = 1.0) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    blend_pixel(rx + dx, ry + dy, color, alpha * self._alpha)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int]) -> None:
            FONT = _get_switcher_font()
            glyph = FONT.get(ch, FONT[' '])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        blend_pixel(cx + col, cy + row, color, self._alpha)
        
        def draw_text(tx: int, ty: int, text: str, color: Tuple[int, int, int]) -> int:
            cx = tx
            for ch in text[:30]:
                draw_char(cx, ty, ch, color)
                cx += 6
            return cx
        
        # Panel background
        fill_rect(0, 0, w, h, (28, 28, 35), 0.95)
        
        # Panel border
        fill_rect(0, 0, w, 2, self.THUMB_ACTIVE_BORDER, 0.5)
        fill_rect(0, h - 2, w, 2, self.THUMB_ACTIVE_BORDER, 0.5)
        fill_rect(0, 0, 2, h, self.THUMB_ACTIVE_BORDER, 0.5)
        fill_rect(w - 2, 0, 2, h, self.THUMB_ACTIVE_BORDER, 0.5)
        
        # Header
        draw_text(self.THUMB_PADDING, 8, f"Switch ({n})", self.TEXT_SECONDARY)
        
        # Thumbnails
        thumb_y = 32
        start_x = max(self.THUMB_PADDING, (w - n * (self.THUMB_WIDTH + self.THUMB_GAP)) // 2)
        
        for i, entry in enumerate(self._entries):
            tx = start_x + i * (self.THUMB_WIDTH + self.THUMB_GAP)
            ty = thumb_y
            
            if tx + self.THUMB_WIDTH > w - self.THUMB_PADDING:
                break
            
            # Selection highlight
            if i == self._selected_index:
                # Glow effect
                for glow in range(4):
                    glow_color = (
                        min(255, self.ACCENT[0] + glow * 20),
                        min(255, self.ACCENT[1] + glow * 10),
                        min(255, self.ACCENT[2] + glow * 5),
                    )
                    fill_rect(tx - glow - 1, ty - glow - 1,
                             self.THUMB_WIDTH + glow * 2 + 2,
                             self.THUMB_HEIGHT + glow * 2 + 2,
                             glow_color, 0.15)
            
            # Thumbnail background
            bg = self.THUMB_ACTIVE_BG if i == self._selected_index else self.THUMB_BG
            fill_rect(tx, ty, self.THUMB_WIDTH, self.THUMB_HEIGHT, bg)
            
            # Thumbnail border
            border = self.THUMB_ACTIVE_BORDER if i == self._selected_index else self.THUMB_BORDER
            fill_rect(tx, ty, self.THUMB_WIDTH, 2, border, 0.8)
            fill_rect(tx, ty + self.THUMB_HEIGHT - 2, self.THUMB_WIDTH, 2, border, 0.8)
            fill_rect(tx, ty, 2, self.THUMB_HEIGHT, border, 0.8)
            fill_rect(tx + self.THUMB_WIDTH - 2, ty, 2, self.THUMB_HEIGHT, border, 0.8)
            
            # App icon (colored square in top-left)
            fill_rect(tx + 8, ty + 8, 24, 24, entry.icon_color)
            draw_text(tx + 12, ty + 12, entry.app_name[0:1] if entry.app_name else "?", self.TEXT_PRIMARY)
            
            # Window title
            title = entry.title[:28]
            draw_text(tx + 40, ty + 14, title, self.TEXT_PRIMARY)
            
            # Simulated window content (decorative lines)
            for line_i in range(3):
                line_y = ty + 44 + line_i * 16
                line_w = self.THUMB_WIDTH - 16 - (line_i * 20)
                fill_rect(tx + 8, line_y, max(20, line_w), 8, (50, 50, 65))
            
            # Label below thumbnail
            label_y = ty + self.THUMB_HEIGHT + 4
            label = entry.title[:20]
            lw = len(label) * 6
            lx = tx + (self.THUMB_WIDTH - lw) // 2
            label_color = self.TEXT_PRIMARY if i == self._selected_index else self.TEXT_SECONDARY
            draw_text(lx, label_y, label, label_color)
        
        return pixels, w, h
    
    def render_to_rgb(self) -> Tuple[bytes, int, int]:
        """Render to raw RGB bytes."""
        pixels, width, height = self.render()
        if not pixels:
            return b"", 0, 0
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
            f"TaskSwitcher(entries={len(self._entries)}, "
            f"selected={self._selected_index}, "
            f"visible={self._visible})"
        )


# ---------------------------------------------------------------------------
# Shared font
# ---------------------------------------------------------------------------

def _get_switcher_font() -> Dict[str, List[int]]:
    """5x7 bitmap font."""
    return {
        ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
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
        '(': [0x02, 0x04, 0x04, 0x04, 0x04, 0x04, 0x02],
        ')': [0x08, 0x04, 0x04, 0x04, 0x04, 0x04, 0x08],
        '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
        ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
    }
