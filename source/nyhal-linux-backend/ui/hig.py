#!/usr/bin/env python3
"""Apple Human Interface Guidelines (HIG) design system for Nyrqis.

Defines typography, colors, spacing, and component standards based on
Apple's HIG (https://developer.apple.com/design/human-interface-guidelines).

All Nyrqis UI components should reference these constants for
consistent, HIG-compliant rendering.

References:
    - Apple HIG Typography: https://developer.apple.com/design/human-interface-guidelines/typography
    - Apple HIG Colors: https://developer.apple.com/design/human-interface-guidelines/color
    - Apple HIG Layout: https://developer.apple.com/design/human-interface-guidelines/layout
    - Apple HIG Icons: https://developer.apple.com/design/human-interface-guidelines/sf-symbols
"""

from dataclasses import dataclass, field
from typing import Dict, Tuple


# ---------------------------------------------------------------------------
# Typography — SF Pro font system (iOS 17+ defaults)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HIGFontStyle:
    """One of Apple's preferred font styles."""
    name: str
    size: float          # point size at default Dynamic Type
    weight: str          # "regular", "semibold", "bold", etc.
    leading: float = 0   # line height (0 = auto)

    @property
    def px(self) -> int:
        """Size in pixels (for bitmap rendering)."""
        return int(self.size)


# iOS 17 preferred font sizes (SF Pro)
EXTRA_LARGE_TITLE = HIGFontStyle("ExtraLargeTitle", 36.0, "bold")
EXTRA_LARGE_TITLE_2 = HIGFontStyle("ExtraLargeTitle2", 28.0, "bold")
LARGE_TITLE = HIGFontStyle("LargeTitle", 34.0, "regular")
TITLE_1 = HIGFontStyle("Title1", 28.0, "regular")
TITLE_2 = HIGFontStyle("Title2", 22.0, "regular")
TITLE_3 = HIGFontStyle("Title3", 20.0, "regular")
HEADLINE = HIGFontStyle("Headline", 17.0, "semibold")
SUBHEADLINE = HIGFontStyle("Subheadline", 15.0, "regular")
BODY = HIGFontStyle("Body", 17.0, "regular")
CALLOUT = HIGFontStyle("Callout", 16.0, "regular")
FOOTNOTE = HIGFontStyle("Footnote", 13.0, "regular")
CAPTION_1 = HIGFontStyle("Caption1", 12.0, "regular")
CAPTION_2 = HIGFontStyle("Caption2", 11.0, "regular")

# Map name → style for lookup
FONT_STYLES: Dict[str, HIGFontStyle] = {
    s.name: s for s in [
        EXTRA_LARGE_TITLE, EXTRA_LARGE_TITLE_2, LARGE_TITLE,
        TITLE_1, TITLE_2, TITLE_3, HEADLINE, SUBHEADLINE,
        BODY, CALLOUT, FOOTNOTE, CAPTION_1, CAPTION_2,
    ]
}


# ---------------------------------------------------------------------------
# Colors — Apple system colors (light + dark mode)
# ---------------------------------------------------------------------------

# RGB tuples (0–255)
@dataclass(frozen=True)
class HIGColor:
    """An Apple system color with light and dark variants."""
    name: str
    light: Tuple[int, int, int]
    dark: Tuple[int, int, int]
    alpha: float = 1.0

    def rgb(self, dark_mode: bool = True) -> Tuple[int, int, int]:
        return self.dark if dark_mode else self.light

    def rgba(self, dark_mode: bool = True) -> Tuple[int, int, int, int]:
        r, g, b = self.rgb(dark_mode)
        return (r, g, b, int(self.alpha * 255))


# -- Semantic colors (Apple system colors) ---------------------------------

SYSTEM_BLUE = HIGColor("SystemBlue", (0, 122, 255), (10, 132, 255))
SYSTEM_GREEN = HIGColor("SystemGreen", (52, 199, 89), (48, 209, 88))
SYSTEM_INDIGO = HIGColor("SystemIndigo", (88, 86, 214), (94, 92, 230))
SYSTEM_ORANGE = HIGColor("SystemOrange", (255, 149, 0), (255, 159, 10))
SYSTEM_PINK = HIGColor("SystemPink", (255, 45, 85), (255, 55, 95))
SYSTEM_PURPLE = HIGColor("SystemPurple", (175, 82, 222), (191, 90, 242))
SYSTEM_RED = HIGColor("SystemRed", (255, 59, 48), (255, 69, 58))
SYSTEM_TEAL = HIGColor("SystemTeal", (90, 200, 250), (100, 210, 255))
SYSTEM_YELLOW = HIGColor("SystemYellow", (255, 204, 0), (255, 214, 10))

# -- Label colors ----------------------------------------------------------

LABEL = HIGColor("Label", (0, 0, 0), (255, 255, 255))
SECONDARY_LABEL = HIGColor("SecondaryLabel", (60, 60, 67), (235, 235, 245), alpha=0.6)
TERTIARY_LABEL = HIGColor("TertiaryLabel", (60, 60, 67), (235, 235, 245), alpha=0.3)
QUATERNARY_LABEL = HIGColor("QuaternaryLabel", (60, 60, 67), (235, 235, 245), alpha=0.18)

# -- Background colors -----------------------------------------------------

SYSTEM_BACKGROUND = HIGColor("SystemBackground", (255, 255, 255), (0, 0, 0))
SECONDARY_SYSTEM_BACKGROUND = HIGColor("SecondarySystemBackground", (242, 242, 247), (28, 28, 30))
TERTIARY_SYSTEM_BACKGROUND = HIGColor("TertiarySystemBackground", (255, 255, 255), (44, 44, 46))

# -- Grouped background colors ---------------------------------------------

SYSTEM_GROUPED_BACKGROUND = HIGColor("SystemGroupedBackground", (242, 242, 247), (28, 28, 30))
SECONDARY_SYSTEM_GROUPED_BACKGROUND = HIGColor("SecondarySystemGroupedBackground", (255, 255, 255), (44, 44, 46))
TERTIARY_SYSTEM_GROUPED_BACKGROUND = HIGColor("TertiarySystemGroupedBackground", (242, 242, 247), (58, 58, 60))

# -- Separator colors ------------------------------------------------------

SEPARATOR = HIGColor("Separator", (60, 60, 67), (84, 84, 88), alpha=0.29)
OPAQUE_SEPARATOR = HIGColor("OpaqueSeparator", (198, 198, 200), (56, 56, 58))

# -- Fill colors -----------------------------------------------------------

SYSTEM_FILL = HIGColor("SystemFill", (120, 120, 128), (120, 120, 128), alpha=0.2)
SECONDARY_SYSTEM_FILL = HIGColor("SecondarySystemFill", (209, 209, 214), (209, 209, 214), alpha=0.16)
TERTIARY_SYSTEM_FILL = HIGColor("TertiarySystemFill", (209, 209, 214), (209, 209, 214), alpha=0.12)
QUATERNARY_SYSTEM_FILL = HIGColor("QuaternarySystemFill", (209, 209, 214), (209, 209, 214), alpha=0.08)

# -- Gray system colors ----------------------------------------------------

SYSTEM_GRAY = HIGColor("SystemGray", (142, 142, 147), (142, 142, 147))
SYSTEM_GRAY_2 = HIGColor("SystemGray2", (174, 174, 178), (99, 99, 102))
SYSTEM_GRAY_3 = HIGColor("SystemGray3", (199, 199, 204), (72, 72, 74))
SYSTEM_GRAY_4 = HIGColor("SystemGray4", (209, 209, 214), (58, 58, 60))
SYSTEM_GRAY_5 = HIGColor("SystemGray5", (229, 229, 234), (44, 44, 46))
SYSTEM_GRAY_6 = HIGColor("SystemGray6", (242, 242, 247), (28, 28, 30))

# Map name → color for lookup
SYSTEM_COLORS: Dict[str, HIGColor] = {
    c.name: c for c in [
        SYSTEM_BLUE, SYSTEM_GREEN, SYSTEM_INDIGO, SYSTEM_ORANGE,
        SYSTEM_PINK, SYSTEM_PURPLE, SYSTEM_RED, SYSTEM_TEAL,
        SYSTEM_YELLOW, LABEL, SECONDARY_LABEL, TERTIARY_LABEL,
        QUATERNARY_LABEL, SYSTEM_BACKGROUND, SECONDARY_SYSTEM_BACKGROUND,
        TERTIARY_SYSTEM_BACKGROUND, SYSTEM_GROUPED_BACKGROUND,
        SECONDARY_SYSTEM_GROUPED_BACKGROUND, TERTIARY_SYSTEM_GROUPED_BACKGROUND,
        SEPARATOR, OPAQUE_SEPARATOR, SYSTEM_FILL, SECONDARY_SYSTEM_FILL,
        TERTIARY_SYSTEM_FILL, QUATERNARY_SYSTEM_FILL, SYSTEM_GRAY,
        SYSTEM_GRAY_2, SYSTEM_GRAY_3, SYSTEM_GRAY_4, SYSTEM_GRAY_5,
        SYSTEM_GRAY_6,
    ]
}


# ---------------------------------------------------------------------------
# Spacing — Apple's 8pt grid and standard spacing values
# ---------------------------------------------------------------------------

# Standard spacing points (Apple uses a 4pt/8pt grid)
SPACING_XS = 4     # Extra small
SPACING_SM = 8     # Small
SPACING_MD = 12    # Medium (iOS standard inset)
SPACING_LG = 16    # Large (iOS standard margin)
SPACING_XL = 20    # Extra large
SPACING_2XL = 24   # Section spacing
SPACING_3XL = 32   # Major section spacing
SPACING_4XL = 40   # Screen margins (iPad)

# Corner radii (Apple's standard radii)
CORNER_RADIUS_SM = 8      # Small controls (buttons, chips)
CORNER_RADIUS_MD = 12     # Medium cards
CORNER_RADIUS_LG = 16     # Large cards, sheets
CORNER_RADIUS_XL = 20     # Extra large (search bars)
CORNER_RADIUS_FULL = 9999 # Pill shape (capsule buttons)

# Standard component sizes
MINIMUM_TAP_TARGET = 44   # Apple's minimum touch target (pt)
SEARCH_BAR_HEIGHT = 36    # Standard search bar
NAVIGATION_BAR_HEIGHT = 44  # Standard nav bar
TAB_BAR_HEIGHT = 49       # Standard tab bar
TOOLBAR_HEIGHT = 44       # Standard toolbar


# ---------------------------------------------------------------------------
# Component Standards — HIG-compliant component definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HIGButtonStyle:
    """Apple HIG button specifications."""
    height: int = 50
    min_width: int = 134
    corner_radius: int = CORNER_RADIUS_SM
    font: HIGFontStyle = BODY
    padding_horizontal: int = SPACING_LG
    padding_vertical: int = SPACING_SM


@dataclass(frozen=True)
class HIGCardStyle:
    """Apple HIG card specifications."""
    corner_radius: int = CORNER_RADIUS_LG
    padding: int = SPACING_LG
    shadow_radius: float = 8.0
    shadow_opacity: float = 0.15
    shadow_offset_y: float = 2.0


@dataclass(frozen=True)
class HIGNavigationBarStyle:
    """Apple HIG navigation bar specifications."""
    height: int = NAVIGATION_BAR_HEIGHT
    title_font: HIGFontStyle = HEADLINE
    large_title_font: HIGFontStyle = LARGE_TITLE
    large_title_height: int = 52


@dataclass(frozen=True)
class HIGTabBarStyle:
    """Apple HIG tab bar specifications."""
    height: int = TAB_BAR_HEIGHT
    icon_size: int = 25
    font: HIGFontStyle = CAPTION_2
    item_spacing: int = SPACING_SM


@dataclass(frozen=True)
class HIGListStyle:
    """Apple HIG list/tableView specifications."""
    row_height: int = 44
    cell_spacing: float = 0.5  # separator height
    content_margin: int = SPACING_LG
    section_header_height: int = 24


@dataclass(frozen=True)
class HIGTextFieldStyle:
    """Apple HIG text field specifications."""
    height: int = 36
    corner_radius: int = CORNER_RADIUS_SM
    font: HIGFontStyle = BODY
    padding_horizontal: int = SPACING_MD


@dataclass(frozen=True)
class HIGAlertStyle:
    """Apple HIG alert/dialog specifications."""
    corner_radius: int = CORNER_RADIUS_MD
    max_width: int = 270
    padding: int = SPACING_LG
    button_height: int = 44
    button_separator: float = 0.5


# Default HIG component styles
BUTTON = HIGButtonStyle()
CARD = HIGCardStyle()
NAVIGATION_BAR = HIGNavigationBarStyle()
TAB_BAR = HIGTabBarStyle()
LIST = HIGListStyle()
TEXT_FIELD = HIGTextFieldStyle()
ALERT = HIGAlertStyle()


# ---------------------------------------------------------------------------
# Iconography — SF Symbols reference (26 categories, 5000+ symbols)
# ---------------------------------------------------------------------------

# SF Symbols names organized by category
SF_SYMBOLS = {
    "navigation": [
        "chevron.left", "chevron.right", "chevron.up", "chevron.down",
        "arrow.left", "arrow.right", "arrow.up", "arrow.down",
        "xmark", "checkmark", "plus", "minus",
    ],
    "actions": [
        "play.fill", "pause.fill", "stop.fill", "forward.fill", "backward.fill",
        "shuffle", "repeat", "volume.2.fill", "volume.xmark",
    ],
    "editing": [
        "pencil", "trash", "doc.text", "doc.text.fill",
        "scissors", "copy", "square.and.arrow.up", "square.and.arrow.down",
    ],
    "communication": [
        "envelope", "envelope.fill", "phone", "phone.fill",
        "message", "message.fill", "video", "video.fill",
    ],
    "weather": [
        "sun.max.fill", "moon.fill", "cloud.fill", "cloud.rain.fill",
        "cloud.snow.fill", "wind", "thermometer.medium", "drop.fill",
    ],
    "commerce": [
        "cart", "cart.fill", "bag", "bag.fill",
        "creditcard", "creditcard.fill", "gift", "gift.fill",
    ],
    "devices": [
        "laptopcomputer", "desktopcomputer", "iphone", "ipad",
        "applewatch", "airpods", "keyboard", "mouse",
    ],
    "media": [
        "photo", "photo.fill", "camera.fill", "film",
        "music.note", "music.note.list", "mic.fill", "speaker.wave.2.fill",
    ],
    "system": [
        "gearshape", "gearshape.fill", "wrench", "wrench.fill",
        "lock.fill", "lock.open.fill", "key.fill", "shield.fill",
    ],
    "files": [
        "folder", "folder.fill", "doc", "doc.fill",
        "doc.richtext", "archivebox", "externaldrive", "internaldrive",
    ],
    "status": [
        "wifi", "wifi.slash", "antenna.radiowaves.left.and.right",
        "bolt.fill", "battery.100", "battery.0", "circle.inset.filled",
    ],
    "maps": [
        "mappin", "mappin.circle.fill", "location.fill",
        "location.north.line.fill", "arrow.triangle.2.circlepath",
    ],
}


# ---------------------------------------------------------------------------
# HIG Layout Guide
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HIGLayout:
    """Apple HIG layout specifications."""
    # Margins
    margin_large: int = 20        # Standard horizontal margin (iPhone)
    margin_ipad: int = 40         # Standard horizontal margin (iPad)
    margin_compact: int = 16      # Compact width margin
    
    # Grid
    grid_columns: int = 4         # iPhone grid columns
    grid_columns_ipad: int = 8    # iPad grid columns
    grid_gutter: int = 16         # Column gutter
    
    # Safe areas
    safe_area_top: int = 44       # Status bar + notch
    safe_area_bottom: int = 34    # Home indicator
    
    # Alerts
    alert_max_width: int = 270    # Standard alert width
    alert_padding: int = 20       # Alert internal padding
    
    # Keyboard
    keyboard_height: int = 336    # Standard keyboard height
    
    # Content sizes (Dynamic Type)
    max_content_width: int = 620  # Maximum readable line width


LAYOUT = HIGLayout()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_font(style_name: str, scale: float = 1.0) -> HIGFontStyle:
    """Get a font style by name, optionally scaled for Dynamic Type."""
    base = FONT_STYLES.get(style_name, BODY)
    scaled_size = base.size * scale
    return HIGFontStyle(
        name=base.name,
        size=scaled_size,
        weight=base.weight,
        leading=base.leading,
    )


def get_color(name: str, dark_mode: bool = True) -> Tuple[int, int, int]:
    """Get a system color by name."""
    color = SYSTEM_COLORS.get(name, LABEL)
    return color.rgb(dark_mode)


def get_color_rgba(name: str, dark_mode: bool = True) -> Tuple[int, int, int, int]:
    """Get a system color with alpha by name."""
    color = SYSTEM_COLORS.get(name, LABEL)
    return color.rgba(dark_mode)
