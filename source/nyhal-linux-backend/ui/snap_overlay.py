#!/usr/bin/env python3
"""Snap preview overlay for the Nyrqis desktop.

Shows semi-transparent ghost rectangles when a window is dragged near
screen edges, previewing where the window will snap to.

Features:
- Ghost rectangle previews for all snap zones
- Semi-transparent overlay with rounded corners
- Zone label text
- Smooth fade-in animation
- Works with WindowManager snap detection
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Snap zones (mirrors window_manager)
# ---------------------------------------------------------------------------

class SnapZone(Enum):
    NONE = "none"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    CENTER = "center"
    MAXIMIZE = "maximize"


# ---------------------------------------------------------------------------
# Ghost rectangle
# ---------------------------------------------------------------------------

@dataclass
class GhostRect:
    """A preview ghost rectangle for a snap zone."""
    zone: SnapZone
    x: int
    y: int
    width: int
    height: int
    alpha: float = 0.0  # 0.0 to 1.0
    label: str = ""
    color: Tuple[int, int, int] = (80, 140, 255)
    
    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)
    
    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


# ---------------------------------------------------------------------------
# Snap overlay
# ---------------------------------------------------------------------------

class SnapOverlay:
    """Renders snap preview ghost rectangles.
    
    Parameters
    ----------
    screen_width : int
        Screen width.
    screen_height : int
        Screen height.
    taskbar_height : int
        Taskbar height.
    """
    
    # Visual constants
    GHOST_ALPHA = 0.35  # Ghost rectangle fill opacity
    GHOST_BORDER_ALPHA = 0.6  # Border opacity
    CORNER_RADIUS = 12
    LABEL_FONT_SIZE = 10
    ANIMATION_SPEED = 0.15  # Alpha change per frame
    
    # Colors
    GHOST_COLOR = (80, 140, 255)
    GHOST_BORDER = (120, 170, 255)
    MAXIMIZE_COLOR = (60, 200, 140)
    LABEL_COLOR = (220, 220, 230)
    
    def __init__(self, screen_width: int = 1920, screen_height: int = 1080,
                 taskbar_height: int = 48):
        self._sw = screen_width
        self._sh = screen_height
        self._th = taskbar_height
        
        self._visible: bool = False
        self._active_zone: Optional[SnapZone] = None
        self._ghost: Optional[GhostRect] = None
        self._alpha: float = 0.0  # Current animation alpha
        
        # Pre-computed zone rects
        self._zone_rects: Dict[SnapZone, Tuple[int, int, int, int]] = {}
        self._compute_zones()
    
    def _compute_zones(self) -> None:
        """Pre-compute all snap zone rectangles."""
        usable_h = self._sh - self._th
        half_w = self._sw // 2
        half_h = usable_h // 2
        qtr_w = self._sw // 4
        qtr_h = usable_h // 4
        margin = 12  # Margin from edges
        
        self._zone_rects = {
            SnapZone.LEFT: (margin, margin, half_w - margin * 2, usable_h - margin * 2),
            SnapZone.RIGHT: (half_w + margin, margin, half_w - margin * 2, usable_h - margin * 2),
            SnapZone.TOP: (margin, margin, self._sw - margin * 2, half_h - margin * 2),
            SnapZone.BOTTOM: (margin, half_h + margin, self._sw - margin * 2, half_h - margin * 2),
            SnapZone.TOP_LEFT: (margin, margin, half_w - margin * 2, half_h - margin * 2),
            SnapZone.TOP_RIGHT: (half_w + margin, margin, half_w - margin * 2, half_h - margin * 2),
            SnapZone.BOTTOM_LEFT: (margin, half_h + margin, half_w - margin * 2, half_h - margin * 2),
            SnapZone.BOTTOM_RIGHT: (half_w + margin, half_h + margin, half_w - margin * 2, half_h - margin * 2),
            SnapZone.CENTER: (qtr_w, qtr_h, half_w, half_h),
            SnapZone.MAXIMIZE: (margin, margin, self._sw - margin * 2, usable_h - margin * 2),
        }
    
    # -- State management --------------------------------------------------
    
    def show(self, zone: SnapZone) -> None:
        """Show the overlay for a snap zone."""
        self._visible = True
        self._active_zone = zone
        self._alpha = 0.0
        
        if zone in self._zone_rects:
            x, y, w, h = self._zone_rects[zone]
            color = self.MAXIMIZE_COLOR if zone == SnapZone.MAXIMIZE else self.GHOST_COLOR
            label = zone.value.replace("_", " ").title()
            self._ghost = GhostRect(zone, x, y, w, h, 0.0, label, color)
        else:
            self._ghost = None
    
    def hide(self) -> None:
        """Hide the overlay."""
        self._visible = False
        self._active_zone = None
        self._ghost = None
    
    def update(self) -> bool:
        """Update animation. Returns True if still animating."""
        if not self._visible:
            if self._alpha > 0:
                self._alpha = max(0, self._alpha - self.ANIMATION_SPEED * 2)
                return True
            return False
        
        # Fade in
        if self._alpha < 1.0:
            self._alpha = min(1.0, self._alpha + self.ANIMATION_SPEED)
            if self._ghost:
                self._ghost.alpha = self._alpha
            return True
        
        return False
    
    @property
    def is_visible(self) -> bool:
        return self._visible
    
    @property
    def active_zone(self) -> Optional[SnapZone]:
        return self._active_zone
    
    @property
    def ghost(self) -> Optional[GhostRect]:
        return self._ghost
    
    @property
    def alpha(self) -> float:
        return self._alpha
    
    # -- Rendering ---------------------------------------------------------
    
    def render(self) -> Tuple[List[Tuple[int, int, int]], int, int]:
        """Render the snap overlay to a pixel buffer.
        
        Returns (pixels, width, height) or empty if not visible.
        """
        if not self._visible or not self._ghost or self._alpha <= 0:
            return [], 0, 0
        
        w = self._sw
        h = self._sh
        pixels = [(0, 0, 0)] * (w * h)
        
        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            if 0 <= px < w and 0 <= py < h:
                pixels[py * w + px] = color
        
        def blend_pixel(px: int, py: int, color: Tuple[int, int, int], alpha: float) -> None:
            if 0 <= px < w and 0 <= py < h:
                old = pixels[py * w + px]
                r = int(old[0] * (1 - alpha) + color[0] * alpha)
                g = int(old[1] * (1 - alpha) + color[1] * alpha)
                b = int(old[2] * (1 - alpha) + color[2] * alpha)
                pixels[py * w + px] = (r, g, b)
        
        def fill_rect(rx: int, ry: int, rw: int, rh: int, color: Tuple[int, int, int], alpha: float) -> None:
            for dy in range(rh):
                for dx in range(rw):
                    blend_pixel(rx + dx, ry + dy, color, alpha * self._alpha)
        
        def draw_char(cx: int, cy: int, ch: str, color: Tuple[int, int, int]) -> None:
            FONT = _get_overlay_font()
            glyph = FONT.get(ch, FONT[' '])
            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        blend_pixel(cx + col, cy + row, color, self._alpha)
        
        def draw_text(tx: int, ty: int, text: str, color: Tuple[int, int, int]) -> int:
            cx = tx
            for ch in text[:20]:
                draw_char(cx, ty, ch, color)
                cx += 6
            return cx
        
        ghost = self._ghost
        
        # Draw ghost rectangle with rounded corners approximation
        x, y, gw, gh = ghost.x, ghost.y, ghost.width, ghost.height
        fill_alpha = self.GHOST_ALPHA * ghost.alpha
        
        # Fill
        fill_rect(x, y, gw, gh, ghost.color, fill_alpha)
        
        # Border (thick)
        border_color = self.GHOST_BORDER
        border_w = 3
        # Top
        fill_rect(x, y, gw, border_w, border_color, self.GHOST_BORDER_ALPHA * self._alpha)
        # Bottom
        fill_rect(x, y + gh - border_w, gw, border_w, border_color, self.GHOST_BORDER_ALPHA * self._alpha)
        # Left
        fill_rect(x, y, border_w, gh, border_color, self.GHOST_BORDER_ALPHA * self._alpha)
        # Right
        fill_rect(x + gw - border_w, y, border_w, gh, border_color, self.GHOST_BORDER_ALPHA * self._alpha)
        
        # Corner radius approximation (small squares at corners)
        cr = self.CORNER_RADIUS
        bg = (28, 28, 35)  # Match desktop background
        # Top-left
        for dy in range(cr):
            for dx in range(cr):
                if dx * dx + dy * dy > cr * cr:
                    blend_pixel(x + dx, y + dy, bg, self._alpha * 0.8)
        # Top-right
        for dy in range(cr):
            for dx in range(cr):
                if (gw - 1 - dx) ** 2 + dy * dy > cr * cr:
                    blend_pixel(x + gw - 1 - dx, y + dy, bg, self._alpha * 0.8)
        # Bottom-left
        for dy in range(cr):
            for dx in range(cr):
                if dx * dx + (gh - 1 - dy) ** 2 > cr * cr:
                    blend_pixel(x + dx, y + gh - 1 - dy, bg, self._alpha * 0.8)
        # Bottom-right
        for dy in range(cr):
            for dx in range(cr):
                if (gw - 1 - dx) ** 2 + (gh - 1 - dy) ** 2 > cr * cr:
                    blend_pixel(x + gw - 1 - dx, y + gh - 1 - dy, bg, self._alpha * 0.8)
        
        # Zone label
        if ghost.label:
            label = ghost.label.upper()
            lw = len(label) * 6
            lx = x + (gw - lw) // 2
            ly = y + (gh - 14) // 2
            
            # Label background
            label_bg = (20, 20, 28)
            fill_rect(lx - 8, ly - 4, lw + 16, 20, label_bg, 0.7 * self._alpha)
            
            # Label text
            draw_text(lx, ly, label, self.LABEL_COLOR)
        
        # Size indicator
        size_text = f"{gw}x{gh}"
        sw = len(size_text) * 6
        sx = x + gw - sw - 16
        sy = y + gh - 20
        draw_text(sx, sy, size_text, self.GHOST_BORDER)
        
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
    
    def get_zone_at(self, x: int, y: int) -> Optional[SnapZone]:
        """Get the snap zone at a screen position."""
        for zone, rect in self._zone_rects.items():
            rx, ry, rw, rh = rect
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return zone
        return None
    
    def __repr__(self) -> str:
        return f"SnapOverlay(visible={self._visible}, zone={self._active_zone})"


# ---------------------------------------------------------------------------
# Shared font
# ---------------------------------------------------------------------------

def _get_overlay_font() -> Dict[str, List[int]]:
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
        'x': [0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11],
    }
