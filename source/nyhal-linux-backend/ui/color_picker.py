"""ColorPicker — Color selection UI for Nyrqis.

Provides a complete color picker with:
- Visual color wheel / spectrum
- Hex, RGB, HSV input
- Built-in palettes (Material, Solarized, Nord, Dracula, Catppuccin)
- Recent colors
- Favorite/saved colors
- Color history
- Copy color code
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Built-in palettes
# ---------------------------------------------------------------------------

PALETTES = {
    "Material": [
        (244, 67, 54), (233, 30, 99), (156, 39, 176), (103, 58, 183),
        (63, 81, 181), (33, 150, 243), (3, 169, 244), (0, 188, 212),
        (0, 150, 136), (76, 175, 80), (139, 195, 74), (205, 220, 57),
        (255, 235, 59), (255, 193, 7), (255, 152, 0), (255, 87, 34),
        (121, 85, 72), (158, 158, 158), (96, 125, 139), (38, 50, 56),
    ],
    "Solarized": [
        (0, 43, 54), (7, 54, 66), (88, 110, 117), (131, 148, 150),
        (147, 161, 161), (181, 137, 0), (203, 75, 22), (220, 50, 47),
        (211, 54, 130), (108, 113, 196), (38, 139, 210), (42, 161, 152),
        (255, 255, 255), (238, 232, 213), (253, 246, 227), (0, 43, 54),
    ],
    "Nord": [
        (46, 52, 64), (59, 66, 82), (67, 76, 94), (76, 86, 106),
        (129, 161, 193), (143, 188, 187), (163, 190, 140), (180, 142, 173),
        (235, 160, 172), (208, 135, 112), (222, 165, 132), (237, 212, 0),
        (152, 151, 26), (133, 153, 0), (38, 139, 210), (211, 54, 130),
    ],
    "Dracula": [
        (40, 42, 54), (68, 71, 90), (98, 114, 164), (139, 233, 253),
        (80, 250, 123), (255, 184, 108), (255, 121, 198), (189, 147, 249),
        (255, 85, 85), (241, 250, 140), (189, 147, 249), (139, 233, 253),
    ],
    "Catppuccin": [
        (30, 30, 46), (49, 50, 68), (69, 71, 90), (88, 91, 112),
        (108, 112, 134), (147, 153, 178), (180, 190, 254), (137, 220, 235),
        (137, 180, 250), (180, 190, 254), (245, 224, 220), (205, 214, 244),
        (243, 139, 168), (250, 179, 135), (249, 226, 175), (166, 227, 161),
        (148, 226, 213), (116, 199, 236), (180, 190, 254), (203, 166, 247),
    ],
    "Nord": [
        (46, 52, 64), (59, 66, 82), (67, 76, 94), (76, 86, 106),
        (129, 161, 193), (163, 190, 140), (180, 142, 173), (191, 97, 106),
        (208, 135, 112), (222, 165, 132), (235, 203, 139), (136, 192, 208),
        (143, 188, 187), (164, 186, 195), (186, 197, 212), (216, 222, 233),
    ],
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Color:
    """A color with multiple representations."""
    r: int = 0
    g: int = 0
    b: int = 255
    a: int = 255

    @property
    def hex(self) -> str:
        if self.a < 255:
            return f"#{self.r:02x}{self.g:02x}{self.b:02x}{self.a:02x}"
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    @property
    def rgb_str(self) -> str:
        return f"rgb({self.r}, {self.g}, {self.b})"

    @property
    def rgba_str(self) -> str:
        return f"rgba({self.r}, {self.g}, {self.b}, {self.a / 255:.2f})"

    @property
    def hsv(self) -> Tuple[float, float, float]:
        return colorsys.rgb_to_hsv(self.r / 255, self.g / 255, self.b / 255)

    @property
    def hsl(self) -> Tuple[float, float, float]:
        return colorsys.rgb_to_hls(self.r / 255, self.g / 255, self.b / 255)

    @property
    def tuple(self) -> Tuple[int, int, int, int]:
        return (self.r, self.g, self.b, self.a)

    @property
    def tuple_rgb(self) -> Tuple[int, int, int]:
        return (self.r, self.g, self.b)

    @property
    def luminance(self) -> float:
        """Relative luminance (0=black, 1=white)."""
        return 0.299 * self.r + 0.587 * self.g + 0.114 * self.b

    @property
    def is_light(self) -> bool:
        return self.luminance > 128

    @property
    def contrast_text(self) -> Tuple[int, int, int]:
        """Black or white for best contrast."""
        return (0, 0, 0) if self.is_light else (255, 255, 255)

    @staticmethod
    def from_hex(hex_str: str) -> "Color":
        hex_str = hex_str.lstrip("#")
        if not all(c in '0123456789abcdefABCDEF' for c in hex_str):
            raise ValueError(f"Invalid hex color: {hex_str}")
        if len(hex_str) == 6:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
            return Color(r, g, b)
        elif len(hex_str) == 8:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
            a = int(hex_str[6:8], 16)
            return Color(r, g, b, a)
        raise ValueError(f"Invalid hex color length: {hex_str}")

    @staticmethod
    def from_hsv(h: float, s: float, v: float) -> "Color":
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return Color(int(r * 255), int(g * 255), int(b * 255))


# ---------------------------------------------------------------------------
# ColorPicker
# ---------------------------------------------------------------------------

class ColorPicker:
    """Color selection UI for Nyrqis.

    Provides visual color selection, palette browsing, and
    precise input via hex/RGB/HSV.

    Parameters
    ----------
    width, height : int
        Rendering dimensions.
    """

    def __init__(self, width: int = 400, height: int = 500):
        self.width = width
        self.height = height

        # Current color
        self._color = Color(80, 140, 255)

        # Palettes
        self._palettes = dict(PALETTES)
        self._selected_palette = "Material"

        # History
        self._recent: List[Color] = []
        self._favorites: List[Color] = []
        self._max_recent = 20

        # UI state
        self._tab = "spectrum"  # spectrum, palette, values
        self._input_mode = "hex"  # hex, rgb, hsv
        self._visible = False

    @property
    def color(self) -> Color:
        return self._color

    @property
    def selected_palette(self) -> str:
        return self._selected_palette

    @property
    def recent(self) -> List[Color]:
        return list(self._recent)

    @property
    def favorites(self) -> List[Color]:
        return list(self._favorites)

    # -- Color setting ---------------------------------------------------

    def set_color(self, r: int, g: int, b: int, a: int = 255) -> None:
        self._color = Color(max(0, min(255, r)),
                           max(0, min(255, g)),
                           max(0, min(255, b)),
                           max(0, min(255, a)))

    def set_from_hex(self, hex_str: str) -> bool:
        try:
            self._color = Color.from_hex(hex_str)
            self._add_recent()
            return True
        except (ValueError, IndexError):
            return False

    def set_from_hsv(self, h: float, s: float, v: float) -> None:
        self._color = Color.from_hsv(h, s, v)
        self._add_recent()

    # -- Palette ---------------------------------------------------------

    def set_palette(self, name: str) -> bool:
        if name in self._palettes:
            self._selected_palette = name
            return True
        return False

    @property
    def palette_colors(self) -> List[Tuple[int, int, int]]:
        return self._palettes.get(self._selected_palette, [])

    def add_palette(self, name: str, colors: List[Tuple[int, int, int]]) -> None:
        self._palettes[name] = colors

    def select_palette_color(self, index: int) -> bool:
        colors = self.palette_colors
        if 0 <= index < len(colors):
            r, g, b = colors[index]
            self.set_color(r, g, b)
            return True
        return False

    # -- Favorites -------------------------------------------------------

    def add_favorite(self, color: Optional[Color] = None) -> None:
        c = color or self._color
        # Don't duplicate
        for fav in self._favorites:
            if fav.tuple_rgb == c.tuple_rgb:
                return
        self._favorites.append(Color(c.r, c.g, c.b))

    def remove_favorite(self, index: int) -> bool:
        if 0 <= index < len(self._favorites):
            self._favorites.pop(index)
            return True
        return False

    # -- Recent ----------------------------------------------------------

    def _add_recent(self) -> None:
        # Don't duplicate the most recent
        if self._recent and self._recent[0].tuple_rgb == self._color.tuple_rgb:
            return
        self._recent.insert(0, Color(self._color.r, self._color.g, self._color.b))
        if len(self._recent) > self._max_recent:
            self._recent.pop()

    # -- Copy ------------------------------------------------------------

    def get_copy_text(self, mode: str = "hex") -> str:
        """Get color code for copying."""
        if mode == "hex":
            return self._color.hex
        elif mode == "rgb":
            return self._color.rgb_str
        elif mode == "rgba":
            return self._color.rgba_str
        elif mode == "hsv":
            h, s, v = self._color.hsv
            return f"hsv({h * 360:.0f}°, {s * 100:.0f}%, {v * 100:.0f}%)"
        return self._color.hex

    # -- Operations ------------------------------------------------------

    def invert(self) -> None:
        self.set_color(255 - self._color.r, 255 - self._color.g, 255 - self._color.b)

    def lighten(self, amount: int = 20) -> None:
        self.set_color(
            min(255, self._color.r + amount),
            min(255, self._color.g + amount),
            min(255, self._color.b + amount),
        )

    def darken(self, amount: int = 20) -> None:
        self.set_color(
            max(0, self._color.r - amount),
            max(0, self._color.g - amount),
            max(0, self._color.b - amount),
        )

    def complementary(self) -> Color:
        h, s, v = self._color.hsv
        return Color.from_hsv((h + 0.5) % 1.0, s, v)

    def analogous(self) -> List[Color]:
        h, s, v = self._color.hsv
        return [
            Color.from_hsv((h - 1 / 12) % 1.0, s, v),
            self._color,
            Color.from_hsv((h + 1 / 12) % 1.0, s, v),
        ]

    def triadic(self) -> List[Color]:
        h, s, v = self._color.hsv
        return [
            self._color,
            Color.from_hsv((h + 1 / 3) % 1.0, s, v),
            Color.from_hsv((h + 2 / 3) % 1.0, s, v),
        ]

    # -- Visibility ------------------------------------------------------

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def toggle(self) -> bool:
        self._visible = not self._visible
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Rendering -------------------------------------------------------

    def render(self) -> Tuple[bytes, int, int]:
        """Render the color picker UI."""
        w, h = self.width, self.height
        buf = bytearray(w * h * 3)
        bg = (30, 30, 40)
        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        # Color preview (top)
        preview_h = 80
        c = self._color.tuple_rgb
        self._fill_rect(buf, w, 0, 0, w, preview_h, c)

        # Hex display
        self._fill_rect(buf, w, 12, preview_h + 8, 100, 20, (42, 42, 56))

        # Palette grid
        y = preview_h + 40
        colors = self.palette_colors
        cols = 5
        cell_w = (w - 24) // cols
        for i, color in enumerate(colors):
            col = i % cols
            row = i // cols
            cx = 12 + col * cell_w
            cy = y + row * (cell_w + 4)
            self._fill_rect(buf, w, cx, cy, cell_w - 4, cell_w - 4, color)

        return bytes(buf), w, h

    def _fill_rect(self, buf: bytearray, buf_width: int,
                   x: int, y: int, w: int, h: int,
                   color: Tuple[int, int, int]) -> None:
        buf_height = len(buf) // (buf_width * 3)
        for dy in range(h):
            for dx in range(w):
                px, py = x + dx, y + dy
                if 0 <= px < buf_width and 0 <= py < buf_height:
                    idx = (py * buf_width + px) * 3
                    if idx + 2 < len(buf):
                        buf[idx] = color[0]
                        buf[idx + 1] = color[1]
                        buf[idx + 2] = color[2]

    # -- Serialization ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "color": self._color.hex,
            "rgb": list(self._color.tuple_rgb),
            "palette": self._selected_palette,
            "recent": len(self._recent),
            "favorites": len(self._favorites),
        }


__all__ = ["ColorPicker", "Color", "PALETTES"]
