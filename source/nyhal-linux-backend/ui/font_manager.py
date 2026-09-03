"""FontManager — Font management UI for Nyrqis.

Provides font management with:
- Font family browsing (system + user fonts)
- Font preview with customizable text
- Font style variants (Regular, Bold, Italic, etc.)
- Font size preview
- Install/uninstall user fonts
- Font search and filter
- Recently used fonts
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FontCategory(Enum):
    SERIF = auto()
    SANS_SERIF = auto()
    MONOSPACE = auto()
    DISPLAY = auto()
    HANDWRITING = auto()
    SYSTEM = auto()


class FontStyle(Enum):
    REGULAR = auto()
    BOLD = auto()
    ITALIC = auto()
    BOLD_ITALIC = auto()
    LIGHT = auto()
    MEDIUM = auto()
    SEMIBOLD = auto()
    EXTRA_BOLD = auto()
    THIN = auto()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FontVariant:
    """A specific font style variant."""
    style: FontStyle
    path: str = ""
    weight: int = 400   # CSS weight (100-900)
    italic: bool = False
    available: bool = True

    @property
    def label(self) -> str:
        return self.style.name.replace("_", " ").title()


@dataclass
class FontFamily:
    """A font family with its variants."""
    name: str
    category: FontCategory = FontCategory.SANS_SERIF
    variants: List[FontVariant] = field(default_factory=list)
    is_system: bool = True
    is_installed: bool = True
    path: str = ""
    designer: str = ""
    license: str = ""

    @property
    def variant_count(self) -> int:
        return len(self.variants)

    @property
    def has_bold(self) -> bool:
        return any(v.style == FontStyle.BOLD for v in self.variants)

    @property
    def has_italic(self) -> bool:
        return any(v.style == FontStyle.ITALIC for v in self.variants)

    @property
    def display_category(self) -> str:
        return self.category.name.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Built-in system fonts (simulated)
# ---------------------------------------------------------------------------

SYSTEM_FONTS = [
    FontFamily(
        name="DejaVu Sans", category=FontCategory.SANS_SERIF, is_system=True,
        designer="Bitstream", license="Bitstream Vera",
        variants=[
            FontVariant(FontStyle.LIGHT, weight=300),
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.MEDIUM, weight=500),
            FontVariant(FontStyle.BOLD, weight=700),
            FontVariant(FontStyle.EXTRA_BOLD, weight=800),
        ]),
    FontFamily(
        name="DejaVu Sans Mono", category=FontCategory.MONOSPACE, is_system=True,
        designer="Bitstream", license="Bitstream Vera",
        variants=[
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.BOLD, weight=700),
        ]),
    FontFamily(
        name="DejaVu Serif", category=FontCategory.SERIF, is_system=True,
        designer="Bitstream", license="Bitstream Vera",
        variants=[
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.BOLD, weight=700),
        ]),
    FontFamily(
        name="Liberation Sans", category=FontCategory.SANS_SERIF, is_system=True,
        designer="Red Hat", license="SIL Open Font",
        variants=[
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.BOLD, weight=700),
            FontVariant(FontStyle.ITALIC, italic=True),
            FontVariant(FontStyle.BOLD_ITALIC, weight=700, italic=True),
        ]),
    FontFamily(
        name="Liberation Mono", category=FontCategory.MONOSPACE, is_system=True,
        designer="Red Hat", license="SIL Open Font",
        variants=[
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.BOLD, weight=700),
        ]),
    FontFamily(
        name="Noto Sans", category=FontCategory.SANS_SERIF, is_system=True,
        designer="Google", license="SIL Open Font",
        variants=[
            FontVariant(FontStyle.THIN, weight=100),
            FontVariant(FontStyle.LIGHT, weight=300),
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.MEDIUM, weight=500),
            FontVariant(FontStyle.SEMIBOLD, weight=600),
            FontVariant(FontStyle.BOLD, weight=700),
            FontVariant(FontStyle.EXTRA_BOLD, weight=900),
        ]),
    FontFamily(
        name="Noto Serif", category=FontCategory.SERIF, is_system=True,
        designer="Google", license="SIL Open Font",
        variants=[
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.BOLD, weight=700),
        ]),
    FontFamily(
        name="Noto Sans Mono", category=FontCategory.MONOSPACE, is_system=True,
        designer="Google", license="SIL Open Font",
        variants=[
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.BOLD, weight=700),
        ]),
    FontFamily(
        name="Ubuntu", category=FontCategory.SANS_SERIF, is_system=True,
        designer="Canonical", license="SIL Open Font",
        variants=[
            FontVariant(FontStyle.LIGHT, weight=300),
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.MEDIUM, weight=500),
            FontVariant(FontStyle.BOLD, weight=700),
        ]),
    FontFamily(
        name="Fira Code", category=FontCategory.MONOSPACE, is_system=True,
        designer="Nikita Prokopov", license="SIL Open Font",
        variants=[
            FontVariant(FontStyle.LIGHT, weight=300),
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.MEDIUM, weight=500),
            FontVariant(FontStyle.BOLD, weight=700),
        ]),
    FontFamily(
        name="JetBrains Mono", category=FontCategory.MONOSPACE, is_system=True,
        designer="JetBrains", license="SIL Open Font",
        variants=[
            FontVariant(FontStyle.THIN, weight=100),
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.BOLD, weight=700),
        ]),
    FontFamily(
        name="Inter", category=FontCategory.SANS_SERIF, is_system=True,
        designer="Rasmus Andersson", license="SIL Open Font",
        variants=[
            FontVariant(FontStyle.THIN, weight=100),
            FontVariant(FontStyle.LIGHT, weight=300),
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.MEDIUM, weight=500),
            FontVariant(FontStyle.SEMIBOLD, weight=600),
            FontVariant(FontStyle.BOLD, weight=700),
            FontVariant(FontStyle.EXTRA_BOLD, weight=900),
        ]),
    FontFamily(
        name="Source Code Pro", category=FontCategory.MONOSPACE, is_system=True,
        designer="Adobe", license="SIL Open Font",
        variants=[
            FontVariant(FontStyle.THIN, weight=100),
            FontVariant(FontStyle.LIGHT, weight=300),
            FontVariant(FontStyle.REGULAR, weight=400),
            FontVariant(FontStyle.MEDIUM, weight=500),
            FontVariant(FontStyle.SEMIBOLD, weight=600),
            FontVariant(FontStyle.BOLD, weight=700),
        ]),
]


# ---------------------------------------------------------------------------
# FontManager
# ---------------------------------------------------------------------------

class FontManager:
    """Font management UI for Nyrqis.

    Parameters
    ----------
    width, height : int
        Rendering dimensions.
    """

    PREVIEW_TEXT = "The quick brown fox jumps over the lazy dog"
    PREVIEW_SIZES = [10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 60, 72]

    def __init__(self, width: int = 480, height: int = 600):
        self.width = width
        self.height = height

        # Fonts
        self._families: List[FontFamily] = list(SYSTEM_FONTS)
        self._user_fonts: List[FontFamily] = []

        # UI state
        self._search_query: str = ""
        self._filter_category: Optional[FontCategory] = None
        self._selected_index: int = 0
        self._preview_text: str = self.PREVIEW_TEXT
        self._preview_size: int = 24
        self._selected_variant: int = 0
        self._show_only_installed: bool = False
        self._recent_fonts: List[str] = []

    @property
    def families(self) -> List[FontFamily]:
        return list(self._families) + list(self._user_fonts)

    @property
    def filtered_families(self) -> List[FontFamily]:
        result = self.families

        if self._show_only_installed:
            result = [f for f in result if f.is_installed]

        if self._filter_category is not None:
            result = [f for f in result if f.category == self._filter_category]

        if self._search_query:
            q = self._search_query.lower()
            result = [f for f in result if q in f.name.lower()
                      or q in f.designer.lower()
                      or q in f.display_category.lower()]

        return sorted(result, key=lambda f: f.name)

    # -- Font management -------------------------------------------------

    def get_family(self, name: str) -> Optional[FontFamily]:
        for f in self.families:
            if f.name == name:
                return f
        return None

    def install_font(self, name: str, path: str,
                     category: FontCategory = FontCategory.SANS_SERIF) -> FontFamily:
        """Install a user font."""
        family = FontFamily(
            name=name, category=category, is_system=False,
            is_installed=True, path=path,
            variants=[FontVariant(FontStyle.REGULAR, weight=400, path=path)],
        )
        self._user_fonts.append(family)
        return family

    def uninstall_font(self, name: str) -> bool:
        """Uninstall a user font (system fonts cannot be removed)."""
        before = len(self._user_fonts)
        self._user_fonts = [f for f in self._user_fonts
                           if f.name != name or f.is_system]
        return len(self._user_fonts) < before

    def set_preview_text(self, text: str) -> None:
        self._preview_text = text or self.PREVIEW_TEXT

    def set_preview_size(self, size: int) -> None:
        self._preview_size = max(8, min(200, size))

    def select_variant(self, index: int) -> None:
        self._selected_variant = index

    # -- Search / Filter ------------------------------------------------

    def set_search(self, query: str) -> None:
        self._search_query = query
        self._selected_index = 0

    def set_category_filter(self, category: Optional[FontCategory]) -> None:
        self._filter_category = category
        self._selected_index = 0

    def toggle_installed_only(self) -> bool:
        self._show_only_installed = not self._show_only_installed
        return self._show_only_installed

    # -- Navigation ------------------------------------------------------

    def move_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def move_down(self) -> None:
        families = self.filtered_families
        self._selected_index = min(len(families) - 1, self._selected_index + 1)

    def select(self) -> Optional[FontFamily]:
        families = self.filtered_families
        if 0 <= self._selected_index < len(families):
            return families[self._selected_index]
        return None

    def handle_key(self, key: str) -> str:
        if key == "Up":
            self.move_up()
            return "navigate"
        elif key == "Down":
            self.move_down()
            return "navigate"
        elif key in ("Enter", "Return"):
            family = self.select()
            if family:
                self._recent_fonts = [family.name] + [
                    n for n in self._recent_fonts if n != family.name][:9]
                return f"select:{family.name}"
            return ""
        elif key == "Escape":
            return "close"
        elif key == "BackSpace":
            self._search_query = self._search_query[:-1]
            self._selected_index = 0
            return "search"
        elif len(key) == 1 and key.isprintable():
            self._search_query += key
            self._selected_index = 0
            return "search"
        return ""

    # -- Stats -----------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        all_fonts = self.families
        return {
            "total": len(all_fonts),
            "system": sum(1 for f in all_fonts if f.is_system),
            "user": sum(1 for f in all_fonts if not f.is_system),
            "serif": sum(1 for f in all_fonts if f.category == FontCategory.SERIF),
            "sans_serif": sum(1 for f in all_fonts if f.category == FontCategory.SANS_SERIF),
            "monospace": sum(1 for f in all_fonts if f.category == FontCategory.MONOSPACE),
            "preview_size": self._preview_size,
        }

    # -- Rendering -------------------------------------------------------

    def render(self) -> Tuple[bytes, int, int]:
        """Render the font manager UI."""
        w, h = self.width, self.height
        buf = bytearray(w * h * 3)
        bg = (30, 30, 40)
        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        # Header
        self._fill_rect(buf, w, 0, 0, w, 48, (42, 42, 56))

        # Search bar
        self._fill_rect(buf, w, 12, 56, w - 24, 32, (42, 42, 56))

        # Font list
        y = 100
        families = self.filtered_families[:12]
        for i, family in enumerate(families):
            is_selected = (i == self._selected_index)
            row_bg = (50, 50, 70) if is_selected else (35, 35, 48)
            self._fill_rect(buf, w, 4, y, w - 8, 44, row_bg)

            # Category dot
            cat_colors = {
                FontCategory.SERIF: (200, 120, 80),
                FontCategory.SANS_SERIF: (80, 140, 255),
                FontCategory.MONOSPACE: (80, 200, 120),
                FontCategory.DISPLAY: (255, 200, 60),
            }
            dot_color = cat_colors.get(family.category, (180, 180, 200))
            self._fill_rect(buf, w, 12, y + 8, 12, 12, dot_color)

            # Font name
            name_color = (230, 230, 240) if is_selected else (200, 200, 210)
            self._fill_rect(buf, w, 32, y + 8, 160, 14, name_color)

            # Variant count
            self._fill_rect(buf, w, 32, y + 26, 60, 10, (120, 120, 140))

            y += 48

        # Preview area at bottom
        preview_y = max(y + 20, h - 100)
        self._fill_rect(buf, w, 12, preview_y, w - 24, 80, (42, 42, 56))
        # Preview text placeholder
        bar_w = min(300, (w - 48) * self._preview_size // 36)
        self._fill_rect(buf, w, 20, preview_y + 20, bar_w, 16, (200, 200, 210))
        self._fill_rect(buf, w, 20, preview_y + 44, bar_w // 2, 12, (120, 120, 140))

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
            "total": len(self.families),
            "preview_text": self._preview_text,
            "preview_size": self._preview_size,
            "recent": self._recent_fonts[:5],
        }


__all__ = [
    "FontManager", "FontFamily", "FontVariant",
    "FontCategory", "FontStyle", "SYSTEM_FONTS",
]
