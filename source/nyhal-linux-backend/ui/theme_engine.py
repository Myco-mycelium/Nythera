#!/usr/bin/env python3
"""theme_engine — Nyrqis CSS-like theming engine.

A full theming system with CSS-like style application:

- Theme definition with named color tokens
- Style inheritance and overrides
- Component-level style selectors
- Dark/light mode with auto-switch
- Built-in themes (Eclipse dark, Solar light, Dracula, Nord, Monokai)
- Custom user themes from dict/JSON
- Style properties: colors, fonts, spacing, borders, shadows
- Contrast checking for accessibility (WCAG AA)
- Dynamic theme switching with change callbacks
- Theme export/import

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Theme data types
# ---------------------------------------------------------------------------

class ThemeMode(Enum):
    LIGHT = "light"
    DARK = "dark"


class StyleProperty(Enum):
    """Supported style property names."""
    BG_PRIMARY = "bg_primary"
    BG_SECONDARY = "bg_secondary"
    BG_TERTIARY = "bg_tertiary"
    BG_SURFACE = "bg_surface"
    BG_OVERLAY = "bg_overlay"
    BG_HOVER = "bg_hover"
    BG_ACTIVE = "bg_active"
    BG_DISABLED = "bg_disabled"

    FG_PRIMARY = "fg_primary"
    FG_SECONDARY = "fg_secondary"
    FG_TERTIARY = "fg_tertiary"
    FG_INVERSE = "fg_inverse"
    FG_DISABLED = "fg_disabled"
    FG_LINK = "fg_link"

    ACCENT = "accent"
    ACCENT_HOVER = "accent_hover"
    ACCENT_ACTIVE = "accent_active"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"

    BORDER = "border"
    BORDER_FOCUS = "border_focus"
    BORDER_ERROR = "border_error"
    BORDER_RADIUS = "border_radius"

    SHADOW_COLOR = "shadow_color"
    SHADOW_BLUR = "shadow_blur"
    SHADOW_OFFSET_Y = "shadow_offset_y"

    FONT_SIZE_XS = "font_size_xs"
    FONT_SIZE_SM = "font_size_sm"
    FONT_SIZE_MD = "font_size_md"
    FONT_SIZE_LG = "font_size_lg"
    FONT_SIZE_XL = "font_size_xl"
    FONT_FAMILY = "font_family"
    FONT_MONO = "font_mono"

    SPACING_XS = "spacing_xs"
    SPACING_SM = "spacing_sm"
    SPACING_MD = "spacing_md"
    SPACING_LG = "spacing_lg"
    SPACING_XL = "spacing_xl"

    OPACITY_DISABLED = "opacity_disabled"
    OPACITY_OVERLAY = "opacity_overlay"
    TRANSITION_MS = "transition_ms"


# Color properties (for WCAG checking)
_COLOR_PROPERTIES = {
    StyleProperty.BG_PRIMARY, StyleProperty.BG_SECONDARY, StyleProperty.BG_TERTIARY,
    StyleProperty.BG_SURFACE, StyleProperty.BG_OVERLAY, StyleProperty.FG_PRIMARY,
    StyleProperty.FG_SECONDARY, StyleProperty.FG_TERTIARY, StyleProperty.FG_INVERSE,
    StyleProperty.ACCENT, StyleProperty.SUCCESS, StyleProperty.WARNING,
    StyleProperty.ERROR, StyleProperty.BORDER,
}


@dataclass
class ComponentStyle:
    """Style override for a specific component type or ID."""
    selector: str                  # e.g. "Button", "#my-button", ".primary"
    properties: Dict[str, Any] = field(default_factory=dict)
    parent_selector: Optional[str] = None  # for nesting


@dataclass
class ThemeDefinition:
    """Complete theme definition."""
    name: str
    mode: ThemeMode = ThemeMode.DARK
    colors: Dict[str, str] = field(default_factory=dict)   # token → hex color
    metrics: Dict[str, Any] = field(default_factory=dict)   # spacing, radius, etc.
    components: List[ComponentStyle] = field(default_factory=list)
    parent_theme: Optional[str] = None  # inherit from another theme
    description: str = ""
    author: str = ""
    version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Color utilities
# ---------------------------------------------------------------------------

def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB to hex color."""
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_rgba(hex_color: str, alpha: float = 1.0) -> Tuple[int, int, int, int]:
    """Convert hex color to RGBA tuple."""
    r, g, b = hex_to_rgb(hex_color)
    return (r, g, b, int(alpha * 255))


def relative_luminance(r: int, g: int, b: int) -> float:
    """Calculate relative luminance (WCAG 2.0)."""
    def linearize(c: int) -> float:
        c_norm = c / 255.0
        return c_norm / 12.92 if c_norm <= 0.03928 else ((c_norm + 0.055) / 1.055) ** 2.4
    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def contrast_ratio(hex1: str, hex2: str) -> float:
    """Calculate WCAG contrast ratio between two colors."""
    r1, g1, b1 = hex_to_rgb(hex1)
    r2, g2, b2 = hex_to_rgb(hex2)
    l1 = relative_luminance(r1, g1, b1)
    l2 = relative_luminance(r2, g2, b2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def blend_color(hex1: str, hex2: str, t: float) -> str:
    """Blend two hex colors by factor t (0=hex1, 1=hex2)."""
    r1, g1, b1 = hex_to_rgb(hex1)
    r2, g2, b2 = hex_to_rgb(hex2)
    t = max(0, min(1, t))
    return rgb_to_hex(
        int(r1 + (r2 - r1) * t),
        int(g1 + (g2 - g1) * t),
        int(b1 + (b2 - b1) * t),
    )


def lighten(hex_color: str, amount: float = 0.1) -> str:
    """Lighten a color toward white."""
    return blend_color(hex_color, "#ffffff", amount)


def darken(hex_color: str, amount: float = 0.1) -> str:
    """Darken a color toward black."""
    return blend_color(hex_color, "#000000", amount)


# ---------------------------------------------------------------------------
# Built-in themes
# ---------------------------------------------------------------------------

BUILTIN_THEMES: Dict[str, ThemeDefinition] = {}


def _register_builtin_themes():
    """Register all built-in themes."""
    # Eclipse (Dark) — default
    BUILTIN_THEMES["Eclipse"] = ThemeDefinition(
        name="Eclipse",
        mode=ThemeMode.DARK,
        description="Default dark theme with cool blue accents",
        colors={
            "bg_primary": "#1a1a2e",
            "bg_secondary": "#16213e",
            "bg_tertiary": "#0f3460",
            "bg_surface": "#232342",
            "bg_overlay": "#1a1a2eee",
            "bg_hover": "#2a2a4a",
            "bg_active": "#3a3a6a",
            "bg_disabled": "#2a2a3a",
            "fg_primary": "#e8e8f0",
            "fg_secondary": "#a0a0b8",
            "fg_tertiary": "#707088",
            "fg_inverse": "#1a1a2e",
            "fg_disabled": "#505060",
            "fg_link": "#64b5f6",
            "accent": "#5090ff",
            "accent_hover": "#6aa0ff",
            "accent_active": "#4080ee",
            "success": "#4caf50",
            "warning": "#ff9800",
            "error": "#f44336",
            "info": "#2196f3",
            "border": "#3a3a5a",
            "border_focus": "#5090ff",
            "border_error": "#f44336",
            "shadow_color": "#00000080",
        },
        metrics={
            "border_radius": 8,
            "shadow_blur": 12,
            "shadow_offset_y": 4,
            "font_size_xs": 11,
            "font_size_sm": 13,
            "font_size_md": 15,
            "font_size_lg": 18,
            "font_size_xl": 24,
            "font_family": "DejaVu Sans",
            "font_mono": "DejaVu Sans Mono",
            "spacing_xs": 4,
            "spacing_sm": 8,
            "spacing_md": 16,
            "spacing_lg": 24,
            "spacing_xl": 32,
            "opacity_disabled": 0.4,
            "opacity_overlay": 0.88,
            "transition_ms": 200,
        },
    )

    # Solar (Light)
    BUILTIN_THEMES["Solar"] = ThemeDefinition(
        name="Solar",
        mode=ThemeMode.LIGHT,
        description="Warm light theme with golden accents",
        colors={
            "bg_primary": "#fdf6e3",
            "bg_secondary": "#eee8d5",
            "bg_tertiary": "#ddd6c1",
            "bg_surface": "#ffffff",
            "bg_overlay": "#fdf6e3ee",
            "bg_hover": "#eee8d5",
            "bg_active": "#ddd6c1",
            "bg_disabled": "#e8e0d0",
            "fg_primary": "#073642",
            "fg_secondary": "#586e75",
            "fg_tertiary": "#93a1a1",
            "fg_inverse": "#fdf6e3",
            "fg_disabled": "#b0b0b0",
            "fg_link": "#268bd2",
            "accent": "#268bd2",
            "accent_hover": "#1a7bc4",
            "accent_active": "#2080d0",
            "success": "#859900",
            "warning": "#b58900",
            "error": "#dc322f",
            "info": "#2aa198",
            "border": "#c0b9a8",
            "border_focus": "#268bd2",
            "border_error": "#dc322f",
            "shadow_color": "#00000020",
        },
        metrics={
            "border_radius": 8,
            "shadow_blur": 8,
            "shadow_offset_y": 2,
            "font_size_xs": 11,
            "font_size_sm": 13,
            "font_size_md": 15,
            "font_size_lg": 18,
            "font_size_xl": 24,
            "font_family": "DejaVu Sans",
            "font_mono": "DejaVu Sans Mono",
            "spacing_xs": 4,
            "spacing_sm": 8,
            "spacing_md": 16,
            "spacing_lg": 24,
            "spacing_xl": 32,
            "opacity_disabled": 0.5,
            "opacity_overlay": 0.95,
            "transition_ms": 200,
        },
    )

    # Dracula
    BUILTIN_THEMES["Dracula"] = ThemeDefinition(
        name="Dracula",
        mode=ThemeMode.DARK,
        description="Popular dark theme with purple/pink accents",
        colors={
            "bg_primary": "#282a36",
            "bg_secondary": "#21222c",
            "bg_tertiary": "#191a21",
            "bg_surface": "#343746",
            "bg_overlay": "#282a36ee",
            "bg_hover": "#3a3d50",
            "bg_active": "#44475a",
            "bg_disabled": "#3a3d50",
            "fg_primary": "#f8f8f2",
            "fg_secondary": "#bfbfbf",
            "fg_tertiary": "#6272a4",
            "fg_inverse": "#282a36",
            "fg_disabled": "#6272a4",
            "fg_link": "#8be9fd",
            "accent": "#bd93f9",
            "accent_hover": "#caa8ff",
            "accent_active": "#a87eeb",
            "success": "#50fa7b",
            "warning": "#f1fa8c",
            "error": "#ff5555",
            "info": "#8be9fd",
            "border": "#44475a",
            "border_focus": "#bd93f9",
            "border_error": "#ff5555",
            "shadow_color": "#00000080",
        },
        metrics={
            "border_radius": 6,
            "shadow_blur": 10,
            "shadow_offset_y": 3,
            "font_size_xs": 11,
            "font_size_sm": 13,
            "font_size_md": 15,
            "font_size_lg": 18,
            "font_size_xl": 24,
            "font_family": "DejaVu Sans",
            "font_mono": "DejaVu Sans Mono",
            "spacing_xs": 4,
            "spacing_sm": 8,
            "spacing_md": 16,
            "spacing_lg": 24,
            "spacing_xl": 32,
            "opacity_disabled": 0.4,
            "opacity_overlay": 0.92,
            "transition_ms": 180,
        },
    )

    # Nord
    BUILTIN_THEMES["Nord"] = ThemeDefinition(
        name="Nord",
        mode=ThemeMode.DARK,
        description="Arctic blue theme with subtle tones",
        colors={
            "bg_primary": "#2e3440",
            "bg_secondary": "#3b4252",
            "bg_tertiary": "#434c5e",
            "bg_surface": "#3b4252",
            "bg_overlay": "#2e3440ee",
            "bg_hover": "#434c5e",
            "bg_active": "#4c566a",
            "bg_disabled": "#3b4252",
            "fg_primary": "#eceff4",
            "fg_secondary": "#d8dee9",
            "fg_tertiary": "#a0a8c0",
            "fg_inverse": "#2e3440",
            "fg_disabled": "#616e88",
            "fg_link": "#88c0d0",
            "accent": "#88c0d0",
            "accent_hover": "#9dd4e0",
            "accent_active": "#78b0c0",
            "success": "#a3be8c",
            "warning": "#ebcb8b",
            "error": "#bf616a",
            "info": "#81a1c1",
            "border": "#4c566a",
            "border_focus": "#88c0d0",
            "border_error": "#bf616a",
            "shadow_color": "#00000080",
        },
        metrics={
            "border_radius": 6,
            "shadow_blur": 10,
            "shadow_offset_y": 3,
            "font_size_xs": 11,
            "font_size_sm": 13,
            "font_size_md": 15,
            "font_size_lg": 18,
            "font_size_xl": 24,
            "font_family": "DejaVu Sans",
            "font_mono": "DejaVu Sans Mono",
            "spacing_xs": 4,
            "spacing_sm": 8,
            "spacing_md": 16,
            "spacing_lg": 24,
            "spacing_xl": 32,
            "opacity_disabled": 0.4,
            "opacity_overlay": 0.92,
            "transition_ms": 200,
        },
    )


_register_builtin_themes()


# ---------------------------------------------------------------------------
# Style resolver
# ---------------------------------------------------------------------------

@dataclass
class ResolvedStyle:
    """Fully resolved style for a component."""
    bg: str = "#1a1a2e"
    fg: str = "#e8e8f0"
    accent: str = "#5090ff"
    border: str = "#3a3a5a"
    border_radius: int = 8
    font_size: int = 15
    font_family: str = "DejaVu Sans"
    padding: int = 16
    margin: int = 0
    opacity: float = 1.0
    shadow: bool = True
    properties: Dict[str, Any] = field(default_factory=dict)


class StyleResolver:
    """Resolves style properties from theme + component overrides."""

    def __init__(self, theme: ThemeDefinition) -> None:
        self._theme = theme
        self._resolved_cache: Dict[str, Dict[str, Any]] = {}

    def set_theme(self, theme: ThemeDefinition) -> None:
        self._theme = theme
        self._resolved_cache.clear()

    def resolve_color(self, token: str) -> str:
        """Resolve a color token to a hex value."""
        # Direct hex
        if token.startswith("#"):
            return token
        # Token lookup
        color = self._theme.colors.get(token)
        if color:
            return color
        # Try parent theme
        if self._theme.parent_theme:
            parent = BUILTIN_THEMES.get(self._theme.parent_theme)
            if parent:
                color = parent.colors.get(token)
                if color:
                    return color
        return "#808080"  # fallback gray

    def resolve_metric(self, key: str, default: Any = 0) -> Any:
        """Resolve a metric (spacing, font size, etc.)."""
        val = self._theme.metrics.get(key)
        if val is not None:
            return val
        if self._theme.parent_theme:
            parent = BUILTIN_THEMES.get(self._theme.parent_theme)
            if parent:
                val = parent.metrics.get(key)
                if val is not None:
                    return val
        return default

    def resolve_component(
        self,
        component_type: str,
        component_id: Optional[str] = None,
        extra_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resolve all style properties for a component."""
        cache_key = f"{component_type}:{component_id or ''}:{id(extra_overrides)}"
        if cache_key in self._resolved_cache and not extra_overrides:
            return self._resolved_cache[cache_key]

        # Start with defaults based on component type
        style = self._base_style_for_type(component_type)

        # Apply component-level overrides from theme
        for cs in self._theme.components:
            if cs.selector == component_type:
                style.update(cs.properties)
            elif component_id and cs.selector == f"#{component_id}":
                style.update(cs.properties)
            elif cs.selector.startswith(".") and cs.selector[1:] in component_type.lower():
                style.update(cs.properties)

        # Apply parent theme overrides
        if self._theme.parent_theme:
            parent = BUILTIN_THEMES.get(self._theme.parent_theme)
            if parent:
                for cs in parent.components:
                    if cs.selector == component_type and cs.selector not in [
                        s.selector for s in self._theme.components
                    ]:
                        style.update(cs.properties)

        # Apply extra overrides
        if extra_overrides:
            style.update(extra_overrides)

        # Resolve color tokens
        for key in list(style.keys()):
            if isinstance(style[key], str) and not style[key].startswith("#"):
                resolved = self._theme.colors.get(style[key])
                if resolved:
                    style[key] = resolved

        self._resolved_cache[cache_key] = style
        return style

    def _base_style_for_type(self, component_type: str) -> Dict[str, Any]:
        """Get base style for a component type."""
        base = {
            "bg": self.resolve_color("bg_surface"),
            "fg": self.resolve_color("fg_primary"),
            "accent": self.resolve_color("accent"),
            "border": self.resolve_color("border"),
            "border_radius": self.resolve_metric("border_radius", 8),
            "font_size": self.resolve_metric("font_size_md", 15),
            "padding": self.resolve_metric("spacing_md", 16),
        }

        # Type-specific overrides
        if component_type in ("Button", "button"):
            base["bg"] = self.resolve_color("accent")
            base["fg"] = self.resolve_color("fg_inverse")
        elif component_type in ("Input", "Textbox", "textbox"):
            base["bg"] = self.resolve_color("bg_primary")
            base["border"] = self.resolve_color("border")
        elif component_type in ("Window", "Dialog", "dialog"):
            base["bg"] = self.resolve_color("bg_surface")
            base["border"] = self.resolve_color("border")
        elif component_type in ("Taskbar", "taskbar"):
            base["bg"] = self.resolve_color("bg_secondary")
        elif component_type in ("Menu", "MenuItem", "menu", "menuitem"):
            base["bg"] = self.resolve_color("bg_surface")
        elif component_type in ("Card", "card"):
            base["bg"] = self.resolve_color("bg_surface")
        elif component_type in ("Text", "Label", "text", "label"):
            base["bg"] = "transparent"
        elif component_type in ("Error", "error"):
            base["fg"] = self.resolve_color("error")
        elif component_type in ("Success", "success"):
            base["fg"] = self.resolve_color("success")
        elif component_type in ("Warning", "warning"):
            base["fg"] = self.resolve_color("warning")

        return base

    def check_contrast(self, fg_token: str, bg_token: str) -> Dict[str, Any]:
        """Check WCAG contrast ratio between two tokens."""
        fg = self.resolve_color(fg_token)
        bg = self.resolve_color(bg_token)
        ratio = contrast_ratio(fg, bg)
        return {
            "foreground": fg,
            "background": bg,
            "ratio": round(ratio, 2),
            "wcag_aa_normal": ratio >= 4.5,
            "wcag_aa_large": ratio >= 3.0,
            "wcag_aaa_normal": ratio >= 7.0,
        }


# ---------------------------------------------------------------------------
# Theme manager
# ---------------------------------------------------------------------------

class ThemeEngine:
    """Manages themes and style resolution for the desktop.

    Parameters
    ----------
    default_theme : str
        Name of the default theme.
    """

    def __init__(self, default_theme: str = "Eclipse") -> None:
        self._themes: Dict[str, ThemeDefinition] = dict(BUILTIN_THEMES)
        self._current_name: str = default_theme
        self._resolver: StyleResolver = StyleResolver(self._themes[default_theme])
        self._callbacks: List[Callable] = []
        self._history: List[str] = [default_theme]
        self._custom_themes: set = set()

    # -- Theme CRUD ----------------------------------------------------

    def register_theme(self, theme: ThemeDefinition, overwrite: bool = False) -> bool:
        """Register a custom theme."""
        if theme.name in self._themes and not overwrite:
            return False
        self._themes[theme.name] = theme
        self._custom_themes.add(theme.name)
        return True

    def unregister_theme(self, name: str) -> bool:
        """Remove a custom theme."""
        if name not in self._custom_themes:
            return False
        self._themes.pop(name, None)
        self._custom_themes.discard(name)
        return True

    def get_theme(self, name: str) -> Optional[ThemeDefinition]:
        """Get a theme by name."""
        return self._themes.get(name)

    @property
    def available_themes(self) -> List[str]:
        """List all available theme names."""
        return list(self._themes.keys())

    @property
    def builtin_themes(self) -> List[str]:
        """List built-in theme names."""
        return [n for n in self._themes if n not in self._custom_themes]

    @property
    def custom_themes(self) -> List[str]:
        return list(self._custom_themes)

    # -- Theme switching -----------------------------------------------

    def set_theme(self, name: str) -> bool:
        """Switch to a different theme."""
        if name not in self._themes:
            return False
        old = self._current_name
        self._current_name = name
        self._resolver.set_theme(self._themes[name])
        self._history.append(name)
        self._dispatch("theme_changed", {"from": old, "to": name})
        return True

    def next_theme(self) -> str:
        """Cycle to the next theme. Returns the new name."""
        names = self.available_themes
        idx = names.index(self._current_name) if self._current_name in names else 0
        next_idx = (idx + 1) % len(names)
        self.set_theme(names[next_idx])
        return self._current_name

    def previous_theme(self) -> str:
        """Cycle to the previous theme."""
        names = self.available_themes
        idx = names.index(self._current_name) if self._current_name in names else 0
        prev_idx = (idx - 1) % len(names)
        self.set_theme(names[prev_idx])
        return self._current_name

    @property
    def current_theme_name(self) -> str:
        return self._current_name

    @property
    def current_theme(self) -> ThemeDefinition:
        return self._themes[self._current_name]

    @property
    def mode(self) -> ThemeMode:
        return self._themes[self._current_name].mode

    # -- Style resolution ----------------------------------------------

    @property
    def resolver(self) -> StyleResolver:
        return self._resolver

    def color(self, token: str) -> str:
        """Resolve a color token."""
        return self._resolver.resolve_color(token)

    def metric(self, key: str, default: Any = 0) -> Any:
        """Resolve a metric."""
        return self._resolver.resolve_metric(key, default)

    def style(
        self,
        component_type: str,
        component_id: Optional[str] = None,
        **overrides,
    ) -> Dict[str, Any]:
        """Resolve full style for a component."""
        return self._resolver.resolve_component(component_type, component_id, overrides)

    def rgb(self, token: str) -> Tuple[int, int, int]:
        """Get RGB tuple for a color token."""
        return hex_to_rgb(self.color(token))

    def rgba(self, token: str, alpha: float = 1.0) -> Tuple[int, int, int, int]:
        """Get RGBA tuple for a color token."""
        return hex_to_rgba(self.color(token), alpha)

    # -- Accessibility -------------------------------------------------

    def check_all_contrast(self) -> List[Dict[str, Any]]:
        """Check contrast ratios for all key color pairs."""
        pairs = [
            ("fg_primary", "bg_primary"),
            ("fg_secondary", "bg_primary"),
            ("fg_primary", "bg_surface"),
            ("fg_inverse", "accent"),
            ("fg_primary", "bg_secondary"),
            ("accent", "bg_primary"),
        ]
        results = []
        for fg, bg in pairs:
            result = self._resolver.check_contrast(fg, bg)
            result["foreground_token"] = fg
            result["background_token"] = bg
            results.append(result)
        return results

    def wcag_compliant(self) -> bool:
        """Check if the current theme passes WCAG AA for all key pairs."""
        for check in self.check_all_contrast():
            if not check["wcag_aa_normal"]:
                return False
        return True

    # -- Export/Import -------------------------------------------------

    def export_theme(self, name: str) -> Optional[Dict[str, Any]]:
        """Export a theme as a serializable dict."""
        theme = self._themes.get(name)
        if not theme:
            return None
        return {
            "name": theme.name,
            "mode": theme.mode.value,
            "description": theme.description,
            "author": theme.author,
            "version": theme.version,
            "colors": dict(theme.colors),
            "metrics": dict(theme.metrics),
            "components": [
                {"selector": cs.selector, "properties": dict(cs.properties)}
                for cs in theme.components
            ],
        }

    def import_theme(self, data: Dict[str, Any]) -> Optional[ThemeDefinition]:
        """Import a theme from a dict."""
        try:
            theme = ThemeDefinition(
                name=data["name"],
                mode=ThemeMode(data.get("mode", "dark")),
                colors=data.get("colors", {}),
                metrics=data.get("metrics", {}),
                components=[
                    ComponentStyle(
                        selector=c["selector"],
                        properties=c.get("properties", {}),
                    )
                    for c in data.get("components", [])
                ],
                description=data.get("description", ""),
                author=data.get("author", ""),
                version=data.get("version", "1.0.0"),
            )
            self.register_theme(theme)
            return theme
        except (KeyError, ValueError):
            return None

    def export_json(self, name: str) -> Optional[str]:
        """Export theme as JSON string."""
        data = self.export_theme(name)
        if data:
            return json.dumps(data, indent=2)
        return None

    def import_json(self, json_str: str) -> Optional[ThemeDefinition]:
        """Import theme from JSON string."""
        try:
            data = json.loads(json_str)
            return self.import_theme(data)
        except (json.JSONDecodeError, KeyError):
            return None

    # -- History -------------------------------------------------------

    @property
    def history(self) -> List[str]:
        return list(self._history)

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, data: Optional[Dict] = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (
            f"ThemeEngine(theme='{self._current_name}', "
            f"mode={self.mode.value}, "
            f"themes={len(self._themes)})"
        )


__all__ = [
    "ThemeEngine", "ThemeDefinition", "ThemeMode", "StyleResolver",
    "ResolvedStyle", "ComponentStyle", "BUILTIN_THEMES",
    "hex_to_rgb", "rgb_to_hex", "contrast_ratio",
    "lighten", "darken", "blend_color",
]
