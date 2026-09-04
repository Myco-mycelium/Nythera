"""
Font Manager — preview, comparison, installation management for Nyrqis OS.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time


# ─── Enums ───────────────────────────────────────────────────────────────

class FontStyle(Enum):
    REGULAR = "Regular"
    BOLD = "Bold"
    ITALIC = "Italic"
    BOLD_ITALIC = "Bold Italic"
    LIGHT = "Light"
    THIN = "Thin"


class FontCategory(Enum):
    SANS_SERIF = "Sans-Serif"
    SERIF = "Serif"
    MONOSPACE = "Monospace"
    DISPLAY = "Display"
    HANDWRITING = "Handwriting"
    DECORATIVE = "Decorative"
    UNKNOWN = "Unknown"


class FontType(Enum):
    TRUETYPE = "TrueType"
    OPENTYPE = "OpenType"
    WOFF = "WOFF"
    WOFF2 = "WOFF2"
    VARIABLE = "Variable"
    BITMAP = "Bitmap"
    VECTOR = "Vector"


class FontLicense(Enum):
    OFL = "Open Font License"
    APACHE = "Apache License"
    MIT = "MIT License"
    COMMERCIAL = "Commercial"
    FREE = "Free"
    UNKNOWN = "Unknown"


class FontWeight(Enum):
    THIN = 100
    EXTRA_LIGHT = 200
    LIGHT = 300
    REGULAR = 400
    MEDIUM = 500
    SEMI_BOLD = 600
    BOLD = 700
    EXTRA_BOLD = 800
    BLACK = 900


# ─── Sample data ─────────────────────────────────────────────────────────

SYSTEM_FONTS = [
    {"name": "DejaVu Sans", "category": "Sans-Serif", "styles": ["Regular", "Bold", "Oblique", "Bold Oblique"]},
    {"name": "DejaVu Serif", "category": "Serif", "styles": ["Regular", "Bold", "Italic", "Bold Italic"]},
    {"name": "DejaVu Sans Mono", "category": "Monospace", "styles": ["Regular", "Bold", "Oblique", "Bold Oblique"]},
    {"name": "Liberation Sans", "category": "Sans-Serif", "styles": ["Regular", "Bold", "Italic", "Bold Italic"]},
    {"name": "Liberation Serif", "category": "Serif", "styles": ["Regular", "Bold", "Italic", "Bold Italic"]},
    {"name": "Liberation Mono", "category": "Monospace", "styles": ["Regular", "Bold", "Italic", "Bold Italic"]},
    {"name": "Noto Sans", "category": "Sans-Serif", "styles": ["Regular", "Bold", "Light", "Medium"]},
    {"name": "Noto Serif", "category": "Serif", "styles": ["Regular", "Bold"]},
    {"name": "Ubuntu", "category": "Sans-Serif", "styles": ["Regular", "Bold", "Light", "Medium"]},
    {"name": "Ubuntu Mono", "category": "Monospace", "styles": ["Regular", "Bold"]},
]


# ─── Data classes ────────────────────────────────────────────────────────

@dataclass
class FontVariant:
    style: FontStyle = FontStyle.REGULAR
    weight: int = 400

    @property
    def label(self) -> str:
        return self.style.value

    @property
    def weight_name(self) -> str:
        if self.weight <= 200:
            return "Thin"
        elif self.weight <= 300:
            return "Light"
        elif self.weight <= 400:
            return "Regular"
        elif self.weight <= 500:
            return "Medium"
        elif self.weight <= 600:
            return "Semi-Bold"
        elif self.weight <= 700:
            return "Bold"
        elif self.weight <= 800:
            return "Extra-Bold"
        return "Black"

    @property
    def style_str(self) -> str:
        return self.style.value

    @property
    def size_str(self) -> str:
        return f"{self.weight} {self.style.value}"


@dataclass
class FontFamily:
    name: str = ""
    category: FontCategory = FontCategory.UNKNOWN
    is_system: bool = True
    is_installed: bool = True
    path: str = ""
    variants: List[FontVariant] = field(default_factory=list)

    @property
    def has_bold(self) -> bool:
        return any(v.style == FontStyle.BOLD or v.weight >= 700 for v in self.variants)

    @property
    def variant_count(self) -> int:
        return len(self.variants)

    @property
    def type_icon(self) -> str:
        return "🔤"

    @property
    def category_icon(self) -> str:
        icons = {
            FontCategory.SANS_SERIF: "sans",
            FontCategory.SERIF: "serif",
            FontCategory.MONOSPACE: "mono",
            FontCategory.DISPLAY: "display",
            FontCategory.HANDWRITING: "hand",
            FontCategory.DECORATIVE: "decor",
            FontCategory.UNKNOWN: "?",
        }
        return icons.get(self.category, "?")


@dataclass
class Font:
    """Legacy Font class for backward compat."""
    name: str = ""
    family: str = ""
    category: FontCategory = FontCategory.UNKNOWN
    is_installed: bool = True
    is_system: bool = True
    variants: List[FontVariant] = field(default_factory=list)

    @property
    def variant_count(self) -> int:
        return len(self.variants)

    @property
    def type_icon(self) -> str:
        return "🔤"

    @property
    def category_icon(self) -> str:
        return "?"


@dataclass
class FontComparison:
    fonts: List[FontFamily] = field(default_factory=list)

    @property
    def font_count(self) -> int:
        return len(self.fonts)


# ─── Font Manager ────────────────────────────────────────────────────────

class FontManager:
    """Main font manager with families, search, filtering, and rendering."""

    def __init__(self):
        self.families: List[FontFamily] = []
        self._selected_index: int = 0
        self._search_query: str = ""
        self._category_filter: Optional[FontCategory] = None
        self._installed_only: bool = False
        self._preview_text: str = "The quick brown fox jumps over the lazy dog"
        self._preview_size: int = 24
        self._create_sample_data()

    def _create_sample_data(self):
        for font in SYSTEM_FONTS:
            cat = FontCategory(font["category"])
            variants = []
            style_map = {
                "Regular": FontStyle.REGULAR,
                "Bold": FontStyle.BOLD,
                "Italic": FontStyle.ITALIC,
                "Bold Italic": FontStyle.BOLD_ITALIC,
                "Oblique": FontStyle.ITALIC,
                "Bold Oblique": FontStyle.BOLD_ITALIC,
                "Light": FontStyle.LIGHT,
                "Thin": FontStyle.THIN,
                "Medium": FontStyle.REGULAR,
            }
            for style_name in font["styles"]:
                style = style_map.get(style_name, FontStyle.REGULAR)
                weight = 700 if "Bold" in style_name else 400
                variants.append(FontVariant(style=style, weight=weight))
            self.families.append(FontFamily(
                name=font["name"],
                category=cat,
                is_system=True,
                is_installed=True,
                variants=variants,
            ))

    def get_family(self, name: str) -> Optional[FontFamily]:
        for f in self.families:
            if f.name == name:
                return f
        return None

    def install_font(self, name: str, path: str = "") -> FontFamily:
        family = FontFamily(
            name=name,
            category=FontCategory.UNKNOWN,
            is_system=False,
            is_installed=True,
            path=path,
            variants=[FontVariant(style=FontStyle.REGULAR, weight=400)],
        )
        self.families.append(family)
        return family

    def uninstall_font(self, name: str) -> bool:
        family = self.get_family(name)
        if not family:
            return False
        if family.is_system:
            return False
        self.families.remove(family)
        return True

    def set_search(self, query: str):
        self._search_query = query

    def set_category_filter(self, category: FontCategory):
        self._category_filter = category

    def toggle_installed_only(self):
        self._installed_only = not self._installed_only

    @property
    def filtered_families(self) -> List[FontFamily]:
        result = self.families[:]
        if self._search_query:
            q = self._search_query.lower()
            result = [f for f in result if q in f.name.lower()]
        if self._category_filter:
            result = [f for f in result if f.category == self._category_filter]
        if self._installed_only:
            result = [f for f in result if f.is_installed]
        return result

    def set_preview_text(self, text: str):
        self._preview_text = text

    def set_preview_size(self, size: int):
        self._preview_size = size

    def move_down(self):
        if self._selected_index < len(self.families) - 1:
            self._selected_index += 1

    def move_up(self):
        if self._selected_index > 0:
            self._selected_index -= 1

    def select(self, index: int = None) -> Optional[FontFamily]:
        idx = index if index is not None else self._selected_index
        if 0 <= idx < len(self.families):
            return self.families[idx]
        return None

    def handle_key(self, key: str) -> str:
        if key == "Down":
            self.move_down()
            return "navigate"
        if key == "Up":
            self.move_up()
            return "navigate"
        if key == "Enter":
            family = self.select()
            return f"select:{family.name}" if family else "select:none"
        if key == "Escape":
            return "close"
        if len(key) == 1:
            self._search_query += key
            return "search"
        return "unknown"

    def get_stats(self) -> Dict:
        cats = {}
        for f in self.families:
            cat_name = f.category.value.lower() if hasattr(f.category, 'value') else str(f.category)
            cats[cat_name] = cats.get(cat_name, 0) + 1
        return {
            "total": len(self.families),
            "installed": sum(1 for f in self.families if f.is_installed),
            "system": sum(1 for f in self.families if f.is_system),
            "custom": sum(1 for f in self.families if not f.is_system),
            **cats,
        }

    def render(self, width: int = 800, height: int = 400) -> Tuple[List[int], int, int]:
        """Render font preview as RGB pixel data."""
        pixels = [255] * (width * height * 3)  # white background
        return pixels, width, height

    def to_dict(self) -> Dict:
        return {
            "total": len(self.families),
            "installed": sum(1 for f in self.families if f.is_installed),
            "preview_text": self._preview_text,
            "preview_size": self._preview_size,
            "search": self._search_query,
            "selected_index": self._selected_index,
        }

    # ─── Legacy properties ───────────────────────────────────────────

    @property
    def selected_font(self) -> Optional[FontFamily]:
        return self.select()

    @property
    def total_fonts(self) -> int:
        return len(self.families)

    @property
    def installed_fonts(self) -> int:
        return sum(1 for f in self.families if f.is_installed)

    def select_font(self, idx: int):
        self._selected_index = idx

    def toggle_install(self):
        family = self.select()
        if family:
            family.is_installed = not family.is_installed

    def _setup_comparison(self):
        pass
