"""Font Manager — preview, comparison, installation management for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time


class FontType(Enum):
    TRUETYPE = "TrueType"
    OPENTYPE = "OpenType"
    WOFF = "WOFF"
    WOFF2 = "WOFF2"
    VARIABLE = "Variable"
    BITMAP = "Bitmap"
    VECTOR = "Vector"


class FontCategory(Enum):
    SANS_SERIF = "Sans-Serif"
    SERIF = "Serif"
    MONOSPACE = "Monospace"
    DISPLAY = "Display"
    HANDWRITING = "Handwriting"
    DECORATIVE = "Decorative"
    UNKNOWN = "Unknown"


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


@dataclass
class FontVariant:
    name: str
    weight: FontWeight = FontWeight.REGULAR
    italic: bool = False
    file_path: str = ""
    file_size: int = 0

    @property
    def weight_name(self) -> str:
        names = {
            100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular",
            500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black",
        }
        return names.get(self.weight.value, "Regular")

    @property
    def style_str(self) -> str:
        italic = " Italic" if self.italic else ""
        return f"{self.weight_name}{italic}"

    @property
    def size_str(self) -> str:
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024**2:
            return f"{self.file_size / 1024:.1f} KB"
        return f"{self.file_size / 1024**2:.1f} MB"


@dataclass
class Font:
    family: str = ""
    font_type: FontType = FontType.TRUETYPE
    category: FontCategory = FontCategory.SANS_SERIF
    license: FontLicense = FontLicense.OFL
    designer: str = ""
    variants: List[FontVariant] = field(default_factory=list)
    installed: bool = True
    variable: bool = False
    variable_axes: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    languages: List[str] = field(default_factory=list)
    opentype_features: List[str] = field(default_factory=list)
    version: str = ""
    copyright: str = ""
    homepage: str = ""

    @property
    def variant_count(self) -> int:
        return len(self.variants)

    @property
    def is_installed(self) -> str:
        return "✅" if self.installed else "⬜"

    @property
    def type_icon(self) -> str:
        icons = {
            FontType.TRUETYPE: "🔤", FontType.OPENTYPE: "🔤",
            FontType.VARIABLE: "✨", FontType.BITMAP: "🔠",
        }
        return icons.get(self.font_type, "🔤")

    @property
    def category_icon(self) -> str:
        icons = {
            FontCategory.SANS_SERIF: "Aa", FontCategory.SERIF: "Aa",
            FontCategory.MONOSPACE: "Aa", FontCategory.DISPLAY: "𝙰𝚊",
            FontCategory.HANDWRITING: "𝒜𝒶", FontCategory.DECORATIVE: "ᎯᏗ",
        }
        return icons.get(self.category, "?")


@dataclass
class FontComparison:
    fonts: List[str] = field(default_factory=list)
    text: str = "The quick brown fox jumps over the lazy dog"
    sizes: List[int] = field(default_factory=lambda: [12, 14, 16, 18, 24, 32, 48, 72])

    @property
    def font_count(self) -> int:
        return len(self.fonts)


class FontManager:
    def __init__(self):
        self._fonts: List[Font] = []
        self._selected_font: int = 0
        self._comparison = FontComparison()
        self._preview_text: str = "The quick brown fox jumps over the lazy dog"
        self._create_samples()
        self._setup_comparison()

    def _create_samples(self):
        self._fonts = [
            Font("Inter", FontType.VARIABLE, FontCategory.SANS_SERIF, FontLicense.OFL,
                 "Rasmus Andersson", installed=True, variable=True,
                 variable_axes={"wght": (100, 900), "ital": (0, 1)},
                 languages=["Latin", "Cyrillic", "Greek"], version="4.0"),
            Font("Fira Code", FontType.TRUETYPE, FontCategory.MONOSPACE, FontLicense.OFL,
                 "Nikita Prokopov", installed=True, opentype_features=["liga", "calt"],
                 languages=["Latin", "Cyrillic"], version="6.2"),
            Font("JetBrains Mono", FontType.TRUETYPE, FontCategory.MONOSPACE, FontLicense.OFL,
                 "JetBrains", installed=True, opentype_features=["liga", "calt", "ss01"],
                 languages=["Latin", "Cyrillic"], version="2.304"),
            Font("Noto Sans", FontType.VARIABLE, FontCategory.SANS_SERIF, FontLicense.APACHE,
                 "Google", installed=True, variable=True,
                 languages=["Latin", "Greek", "Cyrillic", "Arabic", "CJK", "Hindi"],
                 version="2.004"),
            Font("Merriweather", FontType.TRUETYPE, FontCategory.SERIF, FontLicense.OFL,
                 "Sorkin Type", installed=True, version="1.0"),
            Font("Space Grotesk", FontType.VARIABLE, FontCategory.SANS_SERIF, FontLicense.OFL,
                 "Florian Karsten", installed=True, variable=True, version="3.0"),
            Font("DM Sans", FontType.VARIABLE, FontCategory.SANS_SERIF, FontLicense.OFL,
                 "Colophon Foundry", installed=True, variable=True, version="1.0"),
            Font("Source Code Pro", FontType.TRUETYPE, FontCategory.MONOSPACE, FontLicense.APACHE,
                 "Adobe", installed=True, version="2.042"),
            Font("Playfair Display", FontType.TRUETYPE, FontCategory.SERIF, FontLicense.OFL,
                 "Claus Eggers Sørensen", installed=False, version="8.000"),
            Font("IBM Plex Mono", FontType.TRUETYPE, FontCategory.MONOSPACE, FontLicense.APACHE,
                 "Mike Abbink", installed=False, version="1.000"),
            Font("Manrope", FontType.VARIABLE, FontCategory.SANS_SERIF, FontLicense.OFL,
                 "Mikhail Sharanda", installed=True, variable=True, version="8.0"),
            Font("Cascadia Code", FontType.TRUETYPE, FontCategory.MONOSPACE, FontLicense.OFL,
                 "Microsoft", installed=True, opentype_features=["liga", "calt"],
                 version="2111.01"),
        ]
        self._fonts[0].variants = [
            FontVariant("Inter Thin", FontWeight.THIN, False, file_size=100000),
            FontVariant("Inter Light", FontWeight.LIGHT, False, file_size=102000),
            FontVariant("Inter Regular", FontWeight.REGULAR, False, file_size=105000),
            FontVariant("Inter Medium", FontWeight.MEDIUM, False, file_size=108000),
            FontVariant("Inter Bold", FontWeight.BOLD, False, file_size=110000),
            FontVariant("Inter Black", FontWeight.BLACK, False, file_size=112000),
        ]
        self._fonts[1].variants = [
            FontVariant("Fira Code Regular", FontWeight.REGULAR, False, file_size=280000),
            FontVariant("Fira Code Bold", FontWeight.BOLD, False, file_size=285000),
        ]
        self._fonts[3].variants = [
            FontVariant("Noto Sans Regular", FontWeight.REGULAR, False, file_size=350000),
            FontVariant("Noto Sans Bold", FontWeight.BOLD, False, file_size=360000),
        ]

    def _setup_comparison(self):
        self._comparison.fonts = [self._fonts[0].family, self._fonts[1].family, self._fonts[2].family]

    @property
    def selected_font(self) -> Optional[Font]:
        if 0 <= self._selected_font < len(self._fonts):
            return self._fonts[self._selected_font]
        return None

    @property
    def total_fonts(self) -> int:
        return len(self._fonts)

    @property
    def installed_fonts(self) -> int:
        return sum(1 for f in self._fonts if f.installed)

    def select_font(self, idx: int):
        if 0 <= idx < len(self._fonts):
            self._selected_font = idx

    def toggle_install(self):
        font = self.selected_font
        if font:
            font.installed = not font.installed

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS FONT MANAGER                                       ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  Fonts: {self.total_fonts}  Installed: {self.installed_fonts}  Comparison: {self._comparison.font_count} fonts")
        lines.append(f"  Preview: {self._preview_text[:50]}")
        lines.append("")

        # Font list
        lines.append("  ── Fonts ──")
        for i, font in enumerate(self._fonts):
            sel = "▶" if i == self._selected_font else " "
            var = "✨" if font.variable else "  "
            lines.append(f"  {sel} {font.is_installed} {font.type_icon} {font.family:<25s} {font.category.value:<12s} {var} {font.variant_count} variants  {font.license.value}")
        lines.append("")

        # Selected font detail
        font = self.selected_font
        if font:
            lines.append(f"  ── {font.family} ──")
            lines.append(f"  Type: {font.font_type.value}  Category: {font.category.value}  License: {font.license.value}")
            lines.append(f"  Designer: {font.designer}  Version: {font.version}  Variable: {'Yes' if font.variable else 'No'}")
            if font.languages:
                lines.append(f"  Languages: {', '.join(font.languages[:6])}")
            if font.opentype_features:
                lines.append(f"  Features: {', '.join(font.opentype_features)}")
            lines.append("")

            # Variants
            if font.variants:
                lines.append("  ── Variants ──")
                for v in font.variants:
                    lines.append(f"  {v.style_str:<20s}  {v.size_str}")
                lines.append("")

            # Variable axes
            if font.variable and font.variable_axes:
                lines.append("  ── Variable Axes ──")
                for axis, (min_val, max_val) in font.variable_axes.items():
                    filled = int((max_val - min_val) / 10)
                    lines.append(f"  {axis}: {min_val}-{max_val}  [{'█' * min(filled, 20)}]")
                lines.append("")

            # Preview
            lines.append(f"  ── Preview ──")
            for size in [14, 18, 24, 32]:
                lines.append(f"  [{size}pt] {self._preview_text[:55]}")
            lines.append("")

        # Comparison
        lines.append("  ── Comparison ──")
        for f_name in self._comparison.fonts:
            lines.append(f"  {f_name}: {self._preview_text[:45]}")
        lines.append("")

        lines.append("  [↑↓]Select [I]Install/Uninstall [C]Compare [P]Preview Text [F]Filter")
        return lines


class FontFamily(Enum):
    SANS_SERIF = "sans-serif"
    SERIF = "serif"
    MONOSPACE = "monospace"
    DISPLAY = "display"
    HANDWRITING = "handwriting"


class FontStyle:
    pass  # backward compat stub
