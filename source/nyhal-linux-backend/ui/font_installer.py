"""
Nyrqis OS - Font Installer
Preview, comparison, and batch font management.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class FontCategory(Enum):
    SANS_SERIF = "sans-serif"
    SERIF = "serif"
    MONOSPACE = "monospace"
    DISPLAY = "display"
    HANDWRITING = "handwriting"
    DECORATIVE = "decorative"


class FontWeight(Enum):
    THIN = "thin"
    LIGHT = "light"
    REGULAR = "regular"
    MEDIUM = "medium"
    BOLD = "bold"
    BLACK = "black"


class FontStatus(Enum):
    AVAILABLE = "available"
    INSTALLED = "installed"
    PENDING = "pending"
    UPDATABLE = "updatable"


@dataclass
class FontFile:
    name: str
    family: str
    style: str = "Regular"
    weight: FontWeight = FontWeight.REGULAR
    category: FontCategory = FontCategory.SANS_SERIF
    size_bytes: int = 0
    version: str = "1.0"
    designer: str = ""
    license: str = "OFL"
    status: FontStatus = FontStatus.AVAILABLE
    rating: float = 0.0
    downloads: int = 0
    languages: List[str] = field(default_factory=list)
    opentype_features: List[str] = field(default_factory=list)
    variable_axes: List[str] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        icons = {
            FontStatus.AVAILABLE: "📥",
            FontStatus.INSTALLED: "✅",
            FontStatus.PENDING: "⏳",
            FontStatus.UPDATABLE: "🔄",
        }
        return icons.get(self.status, "?")

    @property
    def category_icon(self) -> str:
        icons = {
            FontCategory.SANS_SERIF: "Aa",
            FontCategory.SERIF: "Aa",
            FontCategory.MONOSPACE: "Aa",
            FontCategory.DISPLAY: "Aa",
            FontCategory.HANDWRITING: "Aa",
            FontCategory.DECORATIVE: "Aa",
        }
        return icons.get(self.category, "Aa")

    @property
    def size_display(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MB"


@dataclass
class FontCollection:
    name: str
    description: str = ""
    fonts: List[str] = field(default_factory=list)
    created_at: float = 0.0
    is_system: bool = False

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()


@dataclass
class FontPreview:
    text: str = "The quick brown fox jumps over the lazy dog"
    size_pt: int = 24
    line_height: float = 1.4
    letter_spacing: float = 0.0
    word_spacing: float = 0.0
    color: str = "#ffffff"
    background: str = "#1a1a2e"
    preview_modes: List[str] = field(default_factory=lambda: ["sample", "alphabet", "paragraph"])


class FontInstaller:
    def __init__(self):
        self.fonts: List[FontFile] = []
        self.collections: List[FontCollection] = []
        self.preview: FontPreview = FontPreview()
        self.selected_fonts: List[str] = []
        self.current_font: Optional[FontFile] = None
        self.comparison_fonts: List[str] = []
        self.search_query: str = ""
        self.active_category: Optional[FontCategory] = None
        self._create_sample_fonts()

    def _create_sample_fonts(self):
        self.fonts = [
            FontFile(name="Inter-Regular", family="Inter", style="Regular",
                     weight=FontWeight.REGULAR, category=FontCategory.SANS_SERIF,
                     size_bytes=245000, version="3.19", designer="Rasmus Andersson",
                     license="OFL", status=FontStatus.INSTALLED, rating=4.8, downloads=125000,
                     languages=["Latin", "Cyrillic", "Greek"],
                     opentype_features=["liga", "kern", "calt"],
                     variable_axes=["wght", "ital", "slnt"]),
            FontFile(name="Inter-Bold", family="Inter", style="Bold",
                     weight=FontWeight.BOLD, category=FontCategory.SANS_SERIF,
                     size_bytes=248000, version="3.19", designer="Rasmus Andersson",
                     license="OFL", status=FontStatus.INSTALLED, rating=4.8, downloads=125000,
                     languages=["Latin", "Cyrillic", "Greek"]),
            FontFile(name="JetBrainsMono-Regular", family="JetBrains Mono", style="Regular",
                     weight=FontWeight.REGULAR, category=FontCategory.MONOSPACE,
                     size_bytes=180000, version="2.304", designer="JetBrains",
                     license="OFL", status=FontStatus.INSTALLED, rating=4.9, downloads=98000,
                     languages=["Latin", "Cyrillic"],
                     opentype_features=["liga", "calt", "ss01", "ss02"],
                     variable_axes=["wght"]),
            FontFile(name="NotoSans-Regular", family="Noto Sans", style="Regular",
                     weight=FontWeight.REGULAR, category=FontCategory.SANS_SERIF,
                     size_bytes=320000, version="2.007", designer="Google",
                     license="OFL", status=FontStatus.INSTALLED, rating=4.7, downloads=200000,
                     languages=["Latin", "Greek", "Cyrillic", "Arabic", "Hebrew", "CJK"]),
            FontFile(name="FiraCode-Regular", family="Fira Code", style="Regular",
                     weight=FontWeight.REGULAR, category=FontCategory.MONOSPACE,
                     size_bytes=195000, version="6.2", designer="Nikita Prokopov",
                     license="OFL", status=FontStatus.INSTALLED, rating=4.6, downloads=87000,
                     languages=["Latin"],
                     opentype_features=["liga", "calt", "ss01", "ss02", "ss03", "ss04"]),
            FontFile(name="SourceCodePro-Regular", family="Source Code Pro", style="Regular",
                     weight=FontWeight.REGULAR, category=FontCategory.MONOSPACE,
                     size_bytes=175000, version="2.042", designer="Paul D. Hunt",
                     license="OFL", status=FontStatus.AVAILABLE, rating=4.5, downloads=150000,
                     languages=["Latin"]),
            FontFile(name="Roboto-Regular", family="Roboto", style="Regular",
                     weight=FontWeight.REGULAR, category=FontCategory.SANS_SERIF,
                     size_bytes=165000, version="2.138", designer="Christian Robertson",
                     license="Apache 2.0", status=FontStatus.AVAILABLE, rating=4.6, downloads=180000,
                     languages=["Latin", "Cyrillic", "Greek", "Vietnamese"]),
            FontFile(name="Roboto-Bold", family="Roboto", style="Bold",
                     weight=FontWeight.BOLD, category=FontCategory.SANS_SERIF,
                     size_bytes=168000, version="2.138", designer="Christian Robertson",
                     license="Apache 2.0", status=FontStatus.AVAILABLE, rating=4.6, downloads=180000),
            FontFile(name="PlayfairDisplay-Regular", family="Playfair Display", style="Regular",
                     weight=FontWeight.REGULAR, category=FontCategory.SERIF,
                     size_bytes=210000, version="9.000", designer="Claus Eggers Sørensen",
                     license="OFL", status=FontStatus.AVAILABLE, rating=4.4, downloads=95000,
                     languages=["Latin"],
                     variable_axes=["wght", "ital"]),
            FontFile(name="SpaceGrotesk-Regular", family="Space Grotesk", style="Regular",
                     weight=FontWeight.REGULAR, category=FontCategory.SANS_SERIF,
                     size_bytes=155000, version="3.3", designer="Florian Karsten",
                     license="OFL", status=FontStatus.AVAILABLE, rating=4.5, downloads=42000,
                     languages=["Latin"],
                     variable_axes=["wght"]),
            FontFile(name="Caveat-Regular", family="Caveat", style="Regular",
                     weight=FontWeight.REGULAR, category=FontCategory.HANDWRITING,
                     size_bytes=120000, version="2.0", designer="Pablo Impallari",
                     license="OFL", status=FontStatus.AVAILABLE, rating=4.3, downloads=67000,
                     languages=["Latin"],
                     variable_axes=["wght"]),
            FontFile(name="Monoton-Regular", family="Monoton", style="Regular",
                     weight=FontWeight.REGULAR, category=FontCategory.DECORATIVE,
                     size_bytes=45000, version="1.0", designer="Vernon Adams",
                     license="OFL", status=FontStatus.AVAILABLE, rating=4.1, downloads=32000,
                     languages=["Latin"]),
        ]

        self.collections = [
            FontCollection(name="System Default", description="Nyrqis OS default fonts",
                           fonts=["Inter-Regular", "JetBrainsMono-Regular", "NotoSans-Regular"],
                           is_system=True),
            FontCollection(name="Developer Set", description="Monospace fonts for coding",
                           fonts=["JetBrainsMono-Regular", "FiraCode-Regular", "SourceCodePro-Regular"]),
            FontCollection(name="Web Safe", description="Cross-platform web fonts",
                           fonts=["Roboto-Regular", "NotoSans-Regular", "Inter-Regular"]),
            FontCollection(name="Typography Pack", description="Display and decorative fonts",
                           fonts=["PlayfairDisplay-Regular", "SpaceGrotesk-Regular", "Monoton-Regular"]),
        ]

    def search_fonts(self, query: str) -> List[FontFile]:
        self.search_query = query
        q = query.lower()
        return [f for f in self.fonts if q in f.family.lower() or q in f.name.lower()
                or q in f.designer.lower()]

    def filter_by_category(self, category: Optional[FontCategory]) -> List[FontFile]:
        self.active_category = category
        if category is None:
            return self.fonts
        return [f for f in self.fonts if f.category == category]

    def get_font_families(self) -> Dict[str, List[FontFile]]:
        families: Dict[str, List[FontFile]] = {}
        for font in self.fonts:
            if font.family not in families:
                families[font.family] = []
            families[font.family].append(font)
        return families

    def select_font(self, name: str) -> Optional[FontFile]:
        font = next((f for f in self.fonts if f.name == name), None)
        if font:
            self.current_font = font
        return font

    def install_font(self, name: str) -> bool:
        font = next((f for f in self.fonts if f.name == name), None)
        if font and font.status == FontStatus.AVAILABLE:
            font.status = FontStatus.INSTALLED
            return True
        return False

    def uninstall_font(self, name: str) -> bool:
        font = next((f for f in self.fonts if f.name == name), None)
        if font and font.status == FontStatus.INSTALLED:
            font.status = FontStatus.AVAILABLE
            return True
        return False

    def batch_install(self, names: List[str]) -> int:
        count = 0
        for name in names:
            if self.install_font(name):
                count += 1
        return count

    def batch_uninstall(self, names: List[str]) -> int:
        count = 0
        for name in names:
            if self.uninstall_font(name):
                count += 1
        return count

    def add_to_comparison(self, name: str) -> bool:
        if name not in self.comparison_fonts and len(self.comparison_fonts) < 4:
            self.comparison_fonts.append(name)
            return True
        return False

    def remove_from_comparison(self, name: str) -> bool:
        if name in self.comparison_fonts:
            self.comparison_fonts.remove(name)
            return True
        return False

    def update_preview(self, **kwargs) -> FontPreview:
        for k, v in kwargs.items():
            if hasattr(self.preview, k):
                setattr(self.preview, k, v)
        return self.preview

    def get_installed_fonts(self) -> List[FontFile]:
        return [f for f in self.fonts if f.status == FontStatus.INSTALLED]

    def get_available_fonts(self) -> List[FontFile]:
        return [f for f in self.fonts if f.status == FontStatus.AVAILABLE]

    def get_font_stats(self) -> Dict:
        installed = sum(1 for f in self.fonts if f.status == FontStatus.INSTALLED)
        return {
            "total": len(self.fonts),
            "installed": installed,
            "available": len(self.fonts) - installed,
            "families": len(set(f.family for f in self.fonts)),
            "categories": len(set(f.category for f in self.fonts)),
        }

    def get_collection_fonts(self, collection_name: str) -> List[FontFile]:
        collection = next((c for c in self.collections if c.name == collection_name), None)
        if not collection:
            return []
        return [f for f in self.fonts if f.name in collection.fonts]
