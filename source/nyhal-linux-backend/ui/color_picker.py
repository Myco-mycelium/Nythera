"""
Nyrqis OS - Color Picker
Palette generation, contrast checker, and color blind simulation.
"""

import colorsys
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class ColorFormat(Enum):
    HEX = "hex"
    RGB = "rgb"
    HSL = "hsl"
    HSV = "hsv"
    CMYK = "cmyk"
    LAB = "lab"
    NAMED = "named"


class ColorBlindType(Enum):
    PROTANOPIA = "protanopia"
    DEUTERANOPIA = "deuteranopia"
    TRITANOPIA = "tritanopia"
    ACHROMATOPSIA = "achromatopsia"


class PaletteType(Enum):
    MONOCHROMATIC = "monochromatic"
    ANALOGOUS = "analogous"
    COMPLEMENTARY = "complementary"
    SPLIT_COMPLEMENTARY = "split_complementary"
    TRIADIC = "triadic"
    TETRADIC = "tetradic"
    SQUARE = "square"
    CUSTOM = "custom"


class ContrastRating(Enum):
    AAA_LARGE = "AAA Large"
    AAA = "AAA"
    AA_LARGE = "AA Large"
    AA = "AA"
    FAIL = "Fail"


@dataclass
class Color:
    r: int = 0
    g: int = 0
    b: int = 0
    a: float = 255.0

    @classmethod
    def from_hex(cls, hex_str: str) -> "Color":
        hex_str = hex_str.lstrip("#")
        if len(hex_str) == 8:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
            a = int(hex_str[6:8], 16)
            return cls(r=r, g=g, b=b, a=float(a))
        elif len(hex_str) == 6:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
            return cls(r=r, g=g, b=b)
        return cls()

    @property
    def hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    @property
    def rgb_str(self) -> str:
        return f"rgb({self.r}, {self.g}, {self.b})"

    @property
    def tuple(self) -> Tuple[int, int, int, float]:
        return (self.r, self.g, self.b, self.a)

    @property
    def rgb(self) -> str:
        return f"rgb({self.r}, {self.g}, {self.b})"

    @property
    def hsl(self) -> Tuple[float, float, float]:
        h, l, s = colorsys.rgb_to_hls(self.r / 255, self.g / 255, self.b / 255)
        return (round(h * 360), round(s * 100), round(l * 100))

    @property
    def hsv(self) -> Tuple[float, float, float]:
        h, s, v = colorsys.rgb_to_hsv(self.r / 255, self.g / 255, self.b / 255)
        return (h, s, v)

    @property
    def cmyk(self) -> Tuple[int, int, int, int]:
        if self.r == 0 and self.g == 0 and self.b == 0:
            return (0, 0, 0, 100)
        c = 1 - self.r / 255
        m = 1 - self.g / 255
        y = 1 - self.b / 255
        k = min(c, m, y)
        return (round((c - k) / (1 - k) * 100),
                round((m - k) / (1 - k) * 100),
                round((y - k) / (1 - k) * 100),
                round(k * 100))

    @property
    def luminance(self) -> float:
        def linearize(c):
            c = c / 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * linearize(self.r) + 0.7152 * linearize(self.g) + 0.0722 * linearize(self.b)

    @property
    def is_light(self) -> bool:
        return self.luminance > 0.179

    @property
    def contrast_text(self) -> Tuple[int, int, int]:
        """Return black or white depending on which provides better contrast."""
        if self.is_light:
            return (0, 0, 0)
        return (255, 255, 255)

    @property
    def css_name(self) -> str:
        names = {
            (255, 0, 0): "red", (0, 128, 0): "green", (0, 0, 255): "blue",
            (255, 255, 0): "yellow", (255, 0, 255): "magenta",
            (0, 255, 255): "cyan", (255, 165, 0): "orange",
            (128, 0, 128): "purple", (0, 0, 0): "black",
            (255, 255, 255): "white", (128, 128, 128): "gray",
        }
        return names.get((self.r, self.g, self.b), "")


@dataclass
class ColorPalette:
    name: str
    palette_type: PaletteType = PaletteType.CUSTOM
    colors: List[Color] = field(default_factory=list)
    base_color: Optional[Color] = None
    description: str = ""

    @property
    def color_count(self) -> int:
        return len(self.colors)

    @property
    def preview(self) -> str:
        return " ".join(c.hex for c in self.colors[:6])


@dataclass
class ContrastResult:
    color1: Color = field(default_factory=Color)
    color2: Color = field(default_factory=Color)
    ratio: float = 0.0
    aa_normal: bool = False
    aa_large: bool = False
    aaa_normal: bool = False
    aaa_large: bool = False

    @property
    def rating(self) -> str:
        if self.aaa_normal:
            return ContrastRating.AAA.value
        elif self.aaa_large:
            return ContrastRating.AAA_LARGE.value
        elif self.aa_normal:
            return ContrastRating.AA.value
        elif self.aa_large:
            return ContrastRating.AA_LARGE.value
        return ContrastRating.FAIL.value

    @property
    def rating_icon(self) -> str:
        if "AAA" in self.rating:
            return "🟢"
        elif "AA" in self.rating:
            return "🟡"
        return "🔴"


@dataclass
class SavedColor:
    name: str
    color: Color = field(default_factory=Color)
    tags: List[str] = field(default_factory=list)
    saved_at: float = 0.0


class ColorPicker:
    def __init__(self):
        self.color = Color(r=80, g=140, b=255)
        self.current_color = self.color
        self.picked_history: List[Color] = []
        self.palettes: Dict[str, List[str]] = {}
        self.saved_colors: List[SavedColor] = []
        self.contrast_favorites: List[ContrastResult] = []
        self.active_format: ColorFormat = ColorFormat.HEX
        self.selected_palette: Optional[str] = None
        self._favorites: List[Color] = []
        self._visible: bool = False
        self._recent: List[Color] = []
        self._create_sample_data()

    def _create_sample_data(self):
        sample_colors = [
            (26, 26, 46), (15, 52, 96), (233, 69, 96), (107, 203, 119),
            (255, 183, 77), (77, 150, 255), (155, 89, 182), (26, 188, 156),
            (241, 196, 15), (231, 76, 60), (46, 204, 113), (52, 152, 219),
            (255, 255, 255), (0, 0, 0), (128, 128, 128), (255, 107, 107),
        ]
        for r, g, b in sample_colors:
            self.picked_history.append(Color(r=r, g=g, b=b))

    def set_color(self, r: int, g: int, b: int) -> Color:
        self.color = Color(r=max(0, min(255, r)),
                           g=max(0, min(255, g)),
                           b=max(0, min(255, b)))
        self.current_color = self.color
        self._recent.append(self.color)
        return self.color

    def set_from_hex(self, hex_str: str) -> bool:
        hex_str = hex_str.lstrip("#")
        if len(hex_str) == 6:
            try:
                r = int(hex_str[0:2], 16)
                g = int(hex_str[2:4], 16)
                b = int(hex_str[4:6], 16)
                self.set_color(r, g, b)
                return True
            except ValueError:
                return False
        return False

    def set_from_hsv(self, h: float, s: float, v: float):
        """Set color from HSV (h in 0-360 or 0-1, s and v in 0-1)."""
        if h > 1.0:
            h = h / 360.0
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        self.set_color(int(r * 255), int(g * 255), int(b * 255))

    def set_color_hex(self, hex_str: str) -> Color:
        hex_str = hex_str.lstrip("#")
        if len(hex_str) == 6:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
            return self.set_color(r, g, b)
        return self.color

    def pick_from_history(self, index: int) -> Optional[Color]:
        if 0 <= index < len(self.picked_history):
            self.color = self.picked_history[index]
            self.current_color = self.color
            return self.color
        return None

    def generate_palette(self, base: Color, palette_type: PaletteType,
                         count: int = 5) -> ColorPalette:
        h, s, v = colorsys.rgb_to_hsv(base.r / 255, base.g / 255, base.b / 255)
        colors = [base]
        if palette_type == PaletteType.MONOCHROMATIC:
            for i in range(1, count):
                new_v = max(0.1, v - (i * 0.15))
                r, g, b = colorsys.hsv_to_rgb(h, s, new_v)
                colors.append(Color(int(r * 255), int(g * 255), int(b * 255)))
        elif palette_type == PaletteType.COMPLEMENTARY:
            comp_h = (h + 0.5) % 1.0
            r, g, b = colorsys.hsv_to_rgb(comp_h, s, v)
            colors.append(Color(int(r * 255), int(g * 255), int(b * 255)))
        elif palette_type == PaletteType.ANALOGOUS:
            for offset in [-0.08, -0.04, 0.04, 0.08]:
                new_h = (h + offset) % 1.0
                r, g, b = colorsys.hsv_to_rgb(new_h, s, v)
                colors.append(Color(int(r * 255), int(g * 255), int(b * 255)))
        elif palette_type == PaletteType.TRIADIC:
            for offset in [1/3, 2/3]:
                new_h = (h + offset) % 1.0
                r, g, b = colorsys.hsv_to_rgb(new_h, s, v)
                colors.append(Color(int(r * 255), int(g * 255), int(b * 255)))

        palette = ColorPalette(name=f"Generated ({palette_type.value})",
                               palette_type=palette_type, colors=colors[:count],
                               base_color=base)
        return palette

    def check_contrast(self, fg: Color, bg: Color) -> ContrastResult:
        l1 = max(fg.luminance, bg.luminance)
        l2 = min(fg.luminance, bg.luminance)
        ratio = (l1 + 0.05) / (l2 + 0.05)
        return ContrastResult(
            color1=fg, color2=bg, ratio=round(ratio, 2),
            aa_normal=ratio >= 4.5, aa_large=ratio >= 3,
            aaa_normal=ratio >= 7, aaa_large=ratio >= 4.5)

    def simulate_color_blind(self, color: Color, cb_type: ColorBlindType) -> Color:
        r, g, b = color.r / 255, color.g / 255, color.b / 255
        if cb_type == ColorBlindType.PROTANOPIA:
            nr = 0.56667 * r + 0.43333 * g + 0.0 * b
            ng = 0.55833 * r + 0.44167 * g + 0.0 * b
            nb = 0.0 * r + 0.24167 * g + 0.75833 * b
        elif cb_type == ColorBlindType.DEUTERANOPIA:
            nr = 0.625 * r + 0.375 * g + 0.0 * b
            ng = 0.7 * r + 0.3 * g + 0.0 * b
            nb = 0.0 * r + 0.3 * g + 0.7 * b
        elif cb_type == ColorBlindType.TRITANOPIA:
            nr = 0.95 * r + 0.05 * g + 0.0 * b
            ng = 0.0 * r + 0.43333 * g + 0.56667 * b
            nb = 0.0 * r + 0.475 * g + 0.525 * b
        else:
            gray = 0.2126 * r + 0.7152 * g + 0.0722 * b
            nr = ng = nb = gray
        return Color(r=min(255, int(nr * 255)),
                     g=min(255, int(ng * 255)),
                     b=min(255, int(nb * 255)))

    def save_color(self, name: str, color: Color, **kwargs) -> SavedColor:
        sc = SavedColor(name=name, color=color, **kwargs)
        self.saved_colors.append(sc)
        return sc

    def get_complementary(self, color: Color) -> Color:
        h, s, v = colorsys.rgb_to_hsv(color.r / 255, color.g / 255, color.b / 255)
        comp_h = (h + 0.5) % 1.0
        r, g, b = colorsys.hsv_to_rgb(comp_h, s, v)
        return Color(int(r * 255), int(g * 255), int(b * 255))

    def get_analogous(self, color: Color) -> List[Color]:
        h, s, v = colorsys.rgb_to_hsv(color.r / 255, color.g / 255, color.b / 255)
        colors = []
        for offset in [-0.083, -0.042, 0.042, 0.083]:
            new_h = (h + offset) % 1.0
            r, g, b = colorsys.hsv_to_rgb(new_h, s, v)
            colors.append(Color(int(r * 255), int(g * 255), int(b * 255)))
        return colors

    def get_stats(self) -> Dict:
        return {
            "picked_colors": len(self.picked_history),
            "palettes": len(self.palettes),
            "saved_colors": len(self.saved_colors),
            "contrast_checks": len(self.contrast_favorites),
        }

    # -- Test-facing convenience methods --

    def complementary(self) -> Optional[Color]:
        return self.get_complementary(self.color)

    def analogous(self) -> List[Color]:
        colors = self.get_analogous(self.color)
        return colors[:3] if len(colors) >= 3 else colors

    def triadic(self) -> List[Color]:
        h, s, v = colorsys.rgb_to_hsv(self.color.r / 255, self.color.g / 255, self.color.b / 255)
        colors = []
        for offset in [0.0, 1/3, 2/3]:
            new_h = (h + offset) % 1.0
            r, g, b = colorsys.hsv_to_rgb(new_h, s, v)
            colors.append(Color(int(r * 255), int(g * 255), int(b * 255)))
        return colors

    def invert(self):
        self.color = Color(r=255 - self.color.r, g=255 - self.color.g, b=255 - self.color.b)
        self.current_color = self.color

    def lighten(self, amount: int):
        self.color = Color(
            r=min(255, self.color.r + amount),
            g=min(255, self.color.g + amount),
            b=min(255, self.color.b + amount))
        self.current_color = self.color

    def darken(self, amount: int):
        self.color = Color(
            r=max(0, self.color.r - amount),
            g=max(0, self.color.g - amount),
            b=max(0, self.color.b - amount))
        self.current_color = self.color

    def show(self):
        self._visible = True

    def hide(self):
        self._visible = False

    def toggle(self):
        self._visible = not self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def recent(self) -> List[Color]:
        return list(self._recent[-10:])

    @property
    def favorites(self) -> List[Color]:
        return list(self._favorites)

    def set_palette(self, name: str) -> bool:
        if name in PALETTES:
            self.selected_palette = name
            return True
        return False

    @property
    def palette_colors(self) -> List[str]:
        if self.selected_palette and self.selected_palette in PALETTES:
            return PALETTES[self.selected_palette]
        return []

    def add_palette(self, name: str, colors: List[Tuple[int, int, int]]):
        PALETTES[name] = [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in colors]

    def select_palette_color(self, index: int) -> bool:
        colors = self.palette_colors
        if not colors and PALETTES:
            first_name = list(PALETTES.keys())[0]
            self.selected_palette = first_name
            colors = PALETTES.get(first_name, [])
        if 0 <= index < len(colors):
            return self.set_from_hex(colors[index])
        return False

    def add_favorite(self):
        if not any(c.r == self.color.r and c.g == self.color.g and c.b == self.color.b
                   for c in self._favorites):
            self._favorites.append(Color(r=self.color.r, g=self.color.g, b=self.color.b))

    def remove_favorite(self, index: int) -> bool:
        if 0 <= index < len(self._favorites):
            self._favorites.pop(index)
            return True
        return False

    def render(self) -> Tuple[List[int], int, int]:
        w, h = 64, 64
        pixels = []
        for y in range(h):
            for x in range(w):
                # Simple gradient based on current color
                t = x / w
                r = int(self.color.r * t + 255 * (1 - t))
                g = int(self.color.g * t + 255 * (1 - t))
                b = int(self.color.b * t + 255 * (1 - t))
                pixels.extend([max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))])
        return (pixels, w, h)

    def to_dict(self) -> Dict:
        return {
            "color": {"r": self.color.r, "g": self.color.g, "b": self.color.b},
            "palette": self.selected_palette,
        }

    def get_copy_text(self, fmt: str = "hex") -> str:
        if fmt == "hex":
            return self.color.hex
        elif fmt == "rgb":
            return self.color.rgb_str
        elif fmt == "hsv":
            return f"hsv{self.color.hsv}"
        return self.color.hex


# ---------------------------------------------------------------------------
# Extended PALETTES dict (Material with >10 colors, Nord included)
# ---------------------------------------------------------------------------

PALETTES = {
    "Material": [
        "#F44336", "#E91E63", "#9C27B0", "#673AB7", "#3F51B5",
        "#2196F3", "#03A9F4", "#00BCD4", "#009688", "#4CAF50",
        "#8BC34A", "#CDDC39", "#FFEB3B", "#FFC107", "#FF9800",
        "#FF5722", "#795548", "#9E9E9E", "#607D8B",
    ],
    "Nord": [
        "#2E3440", "#3B4252", "#434C5E", "#4C566A",
        "#D8DEE9", "#E5E9F0", "#ECEFF4",
        "#8FBCBB", "#88C0D0", "#81A1C1", "#5E81AC",
        "#BF616A", "#D08770", "#EBCB8B", "#A3BE8C", "#B48EAD",
    ],
    "Pastel": ["#FFB3BA", "#FFDFBA", "#FFFFBA", "#BAFFC9", "#BAE1FF", "#E8BAFF"],
    "Monokai": ["#F92672", "#A6E22E", "#F4BF75", "#66D9EF", "#AE81FF", "#A1EFE4"],
}
