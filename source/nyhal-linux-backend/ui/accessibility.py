"""
Nyrqis OS - Accessibility Settings
Screen reader, magnifier, high contrast, and keyboard navigation.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class ColorScheme(Enum):
    HIGH_CONTRAST = "high_contrast"
    DARK = "dark"
    LIGHT = "light"
    INVERTED = "inverted"
    YELLOW_BLACK = "yellow_black"
    CUSTOM = "custom"


class CursorSize(Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    EXTRA_LARGE = "extra_large"


class MagnifierType(Enum):
    FULLSCREEN = "fullscreen"
    WINDOW = "window"
    LENS = "lens"
    DOCKED = "docked"


class ScreenReaderVoice(Enum):
    MALE = "male"
    FEMALE = "female"
    CHILD = "child"


@dataclass
class ScreenReaderConfig:
    enabled: bool = False
    voice: ScreenReaderVoice = ScreenReaderVoice.FEMALE
    rate: float = 1.0
    pitch: float = 1.0
    volume: float = 0.8
    announce_keys: bool = True
    announce_notifications: bool = True
    announce_location: bool = True
    echo_char: bool = True
    braille_display: bool = False
    highlight_focused: bool = True

    @property
    def rate_display(self) -> str:
        return f"{self.rate:.1f}x"

    @property
    def rate_bar(self) -> str:
        filled = int(self.rate * 10)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class MagnifierConfig:
    enabled: bool = False
    magnifier_type: MagnifierType = MagnifierType.FULLSCREEN
    zoom_level: float = 2.0
    follows_mouse: bool = True
    follows_keyboard: bool = False
    follows_text_cursor: bool = True
    show_crosshair: bool = False
    invert_colors: bool = False
    smooth_scroll: bool = True

    @property
    def zoom_display(self) -> str:
        return f"{self.zoom_level:.1f}x"

    @property
    def zoom_bar(self) -> str:
        filled = int((self.zoom_level - 1) * 6)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class HighContrastConfig:
    enabled: bool = False
    color_scheme: ColorScheme = ColorScheme.HIGH_CONTRAST
    foreground: str = "#ffffff"
    background: str = "#000000"
    link_color: str = "#00ffff"
    visited_color: str = "#ff00ff"
    border_color: str = "#ffffff"
    border_width: int = 2
    text_scale: float = 1.0
    force_gtk_theme: bool = True
    override_app_colors: bool = True

    @property
    def text_scale_bar(self) -> str:
        filled = int((self.text_scale - 0.5) * 20)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class KeyboardNavConfig:
    enabled: bool = False
    sticky_keys: bool = False
    slow_keys: bool = False
    slow_keys_delay_ms: int = 200
    bounce_keys: bool = False
    bounce_keys_delay_ms: int = 300
    toggle_keys: bool = False
    keyboard_shortcut_access: bool = True
    key_repeat_delay_ms: int = 500
    key_repeat_rate_ms: int = 33
    cursor_blinks: bool = True
    cursor_blink_time_ms: int = 500
    cursor_blink_timeout_s: int = 10

    @property
    def slow_delay_bar(self) -> str:
        filled = int(self.slow_keys_delay_ms / 50)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class CursorConfig:
    size: CursorSize = CursorSize.MEDIUM
    color: str = "#ffffff"
    blink: bool = True
    blink_time_ms: int = 500
    trails: bool = False
    trail_length: int = 10
    acceleration: float = 1.0
    double_click_time_ms: int = 400

    @property
    def size_pixels(self) -> int:
        sizes = {CursorSize.SMALL: 16, CursorSize.MEDIUM: 24,
                 CursorSize.LARGE: 36, CursorSize.EXTRA_LARGE: 48}
        return sizes.get(self.size, 24)


@dataclass
class AccessibilityShortcut:
    name: str
    keys: str
    action: str
    enabled: bool = True
    category: str = "General"


class AccessibilitySettings:
    def __init__(self):
        self.screen_reader = ScreenReaderConfig()
        self.magnifier = MagnifierConfig()
        self.high_contrast = HighContrastConfig()
        self.keyboard_nav = KeyboardNavConfig()
        self.cursor = CursorConfig()
        self.shortcuts: List[AccessibilityShortcut] = []
        self.auto_start_screen_reader: bool = False
        self.visual_bell: bool = False
        self.flash_areas: bool = False
        self._create_sample_data()

    def _create_sample_data(self):
        self.shortcuts = [
            AccessibilityShortcut(name="Toggle Screen Reader", keys="Super+Escape",
                                  action="Toggle screen reader on/off",
                                  category="Screen Reader"),
            AccessibilityShortcut(name="Toggle Magnifier", keys="Super+Plus",
                                  action="Toggle magnifier on/off",
                                  category="Magnifier"),
            AccessibilityShortcut(name="Zoom In", keys="Super+=",
                                  action="Increase magnification",
                                  category="Magnifier"),
            AccessibilityShortcut(name="Zoom Out", keys="Super+-",
                                  action="Decrease magnification",
                                  category="Magnifier"),
            AccessibilityShortcut(name="Toggle High Contrast", keys="Alt+Shift+H",
                                  action="Toggle high contrast mode",
                                  category="Visual"),
            AccessibilityShortcut(name="Invert Colors", keys="Alt+Shift+I",
                                  action="Invert screen colors",
                                  category="Visual"),
            AccessibilityShortcut(name="Sticky Keys", keys="Shift x5",
                                  action="Toggle sticky keys",
                                  category="Keyboard"),
            AccessibilityShortcut(name="Toggle Keys", keys="NumLock x5",
                                  action="Toggle toggle keys",
                                  category="Keyboard"),
            AccessibilityShortcut(name="Slow Keys", keys="Left Shift x8",
                                  action="Toggle slow keys",
                                  category="Keyboard"),
            AccessibilityShortcut(name="Open Accessibility", keys="Super+U",
                                  action="Open accessibility settings panel",
                                  category="General"),
        ]

    def toggle_screen_reader(self) -> bool:
        self.screen_reader.enabled = not self.screen_reader.enabled
        return self.screen_reader.enabled

    def toggle_magnifier(self) -> bool:
        self.magnifier.enabled = not self.magnifier.enabled
        return self.magnifier.enabled

    def set_magnifier_zoom(self, level: float) -> bool:
        self.magnifier.zoom_level = max(1.0, min(20.0, level))
        return True

    def zoom_in(self) -> float:
        self.magnifier.zoom_level = min(20.0, self.magnifier.zoom_level + 0.5)
        return self.magnifier.zoom_level

    def zoom_out(self) -> float:
        self.magnifier.zoom_level = max(1.0, self.magnifier.zoom_level - 0.5)
        return self.magnifier.zoom_level

    def toggle_high_contrast(self) -> bool:
        self.high_contrast.enabled = not self.high_contrast.enabled
        return self.high_contrast.enabled

    def set_color_scheme(self, scheme: ColorScheme) -> bool:
        self.high_contrast.color_scheme = scheme
        if scheme == ColorScheme.HIGH_CONTRAST:
            self.high_contrast.foreground = "#ffffff"
            self.high_contrast.background = "#000000"
        elif scheme == ColorScheme.INVERTED:
            self.high_contrast.foreground = "#000000"
            self.high_contrast.background = "#ffffff"
        elif scheme == ColorScheme.YELLOW_BLACK:
            self.high_contrast.foreground = "#000000"
            self.high_contrast.background = "#ffff00"
        return True

    def set_text_scale(self, scale: float) -> bool:
        self.high_contrast.text_scale = max(0.5, min(3.0, scale))
        return True

    def toggle_keyboard_nav(self) -> bool:
        self.keyboard_nav.enabled = not self.keyboard_nav.enabled
        return self.keyboard_nav.enabled

    def toggle_sticky_keys(self) -> bool:
        self.keyboard_nav.sticky_keys = not self.keyboard_nav.sticky_keys
        return self.keyboard_nav.sticky_keys

    def toggle_slow_keys(self) -> bool:
        self.keyboard_nav.slow_keys = not self.keyboard_nav.slow_keys
        return self.keyboard_nav.slow_keys

    def toggle_bounce_keys(self) -> bool:
        self.keyboard_nav.bounce_keys = not self.keyboard_nav.bounce_keys
        return self.keyboard_nav.bounce_keys

    def set_cursor_size(self, size: CursorSize) -> bool:
        self.cursor.size = size
        return True

    def toggle_cursor_trails(self) -> bool:
        self.cursor.trails = not self.cursor.trails
        return self.cursor.trails

    def get_active_features(self) -> List[str]:
        features = []
        if self.screen_reader.enabled:
            features.append("Screen Reader")
        if self.magnifier.enabled:
            features.append(f"Magnifier ({self.magnifier.zoom_display})")
        if self.high_contrast.enabled:
            features.append(f"High Contrast ({self.high_contrast.color_scheme.value})")
        if self.keyboard_nav.enabled:
            features.append("Keyboard Navigation")
        if self.keyboard_nav.sticky_keys:
            features.append("Sticky Keys")
        if self.keyboard_nav.slow_keys:
            features.append("Slow Keys")
        if self.keyboard_nav.bounce_keys:
            features.append("Bounce Keys")
        if self.visual_bell:
            features.append("Visual Bell")
        return features

    def get_stats(self) -> Dict:
        return {
            "screen_reader": self.screen_reader.enabled,
            "magnifier": self.magnifier.enabled,
            "high_contrast": self.high_contrast.enabled,
            "keyboard_nav": self.keyboard_nav.enabled,
            "shortcuts": len(self.shortcuts),
            "active_features": len(self.get_active_features()),
        }
