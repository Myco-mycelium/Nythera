"""
Nyrqis OS - Theme Editor
Color scheme builder, preview, and GTK/Qt export.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class ThemeVariant(Enum):
    DARK = "dark"
    LIGHT = "light"
    AUTO = "auto"


class ThemeFormat(Enum):
    GTK = "gtk"
    QT = "qt"
    CSS = "css"
    JSON = "json"
    YAML = "yaml"
    INI = "ini"


class AccentColor(Enum):
    BLUE = "blue"
    GREEN = "green"
    PURPLE = "purple"
    RED = "red"
    ORANGE = "orange"
    PINK = "pink"
    TEAL = "teal"
    YELLOW = "yellow"


@dataclass
class ColorScheme:
    name: str
    variant: ThemeVariant = ThemeVariant.DARK
    accent: AccentColor = AccentColor.BLUE
    is_default: bool = False
    primary: str = "#4fc3f7"
    secondary: str = "#81c784"
    background: str = "#1a1a2e"
    surface: str = "#16213e"
    surface_bright: str = "#1e2d4a"
    on_background: str = "#ffffff"
    on_surface: str = "#e0e0e0"
    error: str = "#e57373"
    warning: str = "#ffb74d"
    success: str = "#6bcb77"
    info: str = "#4fc3f7"
    text_primary: str = "#ffffff"
    text_secondary: str = "#b0bec5"
    text_disabled: str = "#546e7a"
    border: str = "#2a2a4a"
    divider: str = "#1e1e3e"
    shadow: str = "rgba(0,0,0,0.3)"
    opacity: float = 1.0
    corner_radius: int = 8
    font_family: str = "Inter, sans-serif"
    font_size: int = 14

    @property
    def variant_icon(self) -> str:
        return "🌙" if self.variant == ThemeVariant.DARK else "☀️"

    @property
    def css(self) -> str:
        return f"""/* {self.name} Theme */
:root {{
  --primary: {self.primary};
  --secondary: {self.secondary};
  --background: {self.background};
  --surface: {self.surface};
  --text-primary: {self.text_primary};
  --text-secondary: {self.text_secondary};
  --error: {self.error};
  --warning: {self.warning};
  --success: {self.success};
  --border: {self.border};
  --corner-radius: {self.corner_radius}px;
  --font-family: {self.font_family};
  --font-size: {self.font_size}px;
}}
body {{
  background: var(--background);
  color: var(--text-primary);
  font-family: var(--font-family);
  font-size: var(--font-size);
}}
.window {{
  background: var(--surface);
  border-radius: var(--corner-radius);
  border: 1px solid var(--border);
}}
.button {{
  background: var(--primary);
  color: var(--text-primary);
  border-radius: var(--corner_radius);
  padding: 8px 16px;
}}
.button:hover {{
  opacity: 0.9;
}}
.text-field {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--corner_radius);
  color: var(--text-primary);
  padding: 8px 12px;
}}"""


@dataclass
class ThemePreset:
    name: str
    description: str = ""
    colors: Dict[str, str] = field(default_factory=dict)
    is_default: bool = False


@dataclass
class WidgetStyle:
    name: str
    background: str = ""
    foreground: str = ""
    border_color: str = ""
    border_width: int = 1
    border_radius: int = 8
    padding: int = 8
    font_size: int = 14
    font_weight: str = "normal"

    @property
    def css_properties(self) -> str:
        props = []
        if self.background:
            props.append(f"background: {self.background}")
        if self.foreground:
            props.append(f"color: {self.foreground}")
        if self.border_color:
            props.append(f"border: {self.border_width}px solid {self.border_color}")
        props.append(f"border-radius: {self.border_radius}px")
        props.append(f"padding: {self.padding}px")
        return "; ".join(props)


class ThemeEditor:
    def __init__(self):
        self.schemes: List[ColorScheme] = []
        self.current_scheme: Optional[ColorScheme] = None
        self.presets: List[ThemePreset] = []
        self.widget_styles: List[WidgetStyle] = []
        self.export_format: ThemeFormat = ThemeFormat.CSS
        self.preview_widgets: List[str] = []
        self._create_sample_data()

    def _create_sample_data(self):
        self.schemes = [
            ColorScheme(name="Nyrqis Default", variant=ThemeVariant.DARK,
                         accent=AccentColor.BLUE, is_default=True,
                         primary="#e94560", secondary="#0f3460",
                         background="#0f0f23", surface="#1a1a2e",
                         surface_bright="#16213e", on_background="#ffffff",
                         on_surface="#e0e0e0", text_primary="#ffffff",
                         text_secondary="#8892b0", border="#233554",
                         corner_radius=12, font_family="Inter, sans-serif"),
            ColorScheme(name="Nyrqis Light", variant=ThemeVariant.LIGHT,
                         accent=AccentColor.BLUE,
                         primary="#1565c0", secondary="#2e7d32",
                         background="#fafafa", surface="#ffffff",
                         surface_bright="#f5f5f5", on_background="#000000",
                         on_surface="#212121", text_primary="#212121",
                         text_secondary="#757575", border="#e0e0e0",
                         corner_radius=12, font_family="Inter, sans-serif"),
            ColorScheme(name="Monokai Pro", variant=ThemeVariant.DARK,
                         accent=AccentColor.PURPLE,
                         primary="#a9dc76", secondary="#fc9867",
                         background="#2d2a2e", surface="#403e41",
                         surface_bright="#5b595c", on_background="#fcfcfa",
                         on_surface="#fcfcfa", text_primary="#fcfcfa",
                         text_secondary="#939293", border="#5b595c",
                         corner_radius=8, font_family="JetBrains Mono, monospace"),
            ColorScheme(name="Dracula", variant=ThemeVariant.DARK,
                         accent=AccentColor.PURPLE,
                         primary="#bd93f9", secondary="#50fa7b",
                         background="#282a36", surface="#44475a",
                         surface_bright="#6272a4", on_background="#f8f8f2",
                         on_surface="#f8f8f2", error="#ff5555",
                         warning="#f1fa8c", success="#50fa7b",
                         text_primary="#f8f8f2", text_secondary="#6272a4",
                         border="#6272a4", corner_radius=8,
                         font_family="Fira Code, monospace"),
            ColorScheme(name="Nord", variant=ThemeVariant.DARK,
                         accent=AccentColor.TEAL,
                         primary="#88c0d0", secondary="#a3be8c",
                         background="#2e3440", surface="#3b4252",
                         surface_bright="#434c5e", on_background="#eceff4",
                         on_surface="#eceff4", text_primary="#eceff4",
                         text_secondary="#d8dee9", border="#4c566a",
                         corner_radius=6, font_family="Noto Sans, sans-serif"),
        ]
        self.current_scheme = self.schemes[0]

        self.presets = [
            ThemePreset(name="Nyrqis Brand", description="Official Nyrqis OS colors",
                        colors={"primary": "#e94560", "secondary": "#0f3460",
                                "background": "#0f0f23", "surface": "#1a1a2e"}),
            ThemePreset(name="Material Dark", description="Google Material dark theme",
                        colors={"primary": "#bb86fc", "secondary": "#03dac6",
                                "background": "#121212", "surface": "#1e1e1e"}),
            ThemePreset(name="Solarized Dark", description="Solarized color palette",
                        colors={"primary": "#268bd2", "secondary": "#2aa198",
                                "background": "#002b36", "surface": "#073642"}),
            ThemePreset(name="One Dark", description="Atom One Dark theme",
                        colors={"primary": "#61afef", "secondary": "#98c379",
                                "background": "#282c34", "surface": "#3e4451"}),
            ThemePreset(name="Gruvbox Dark", description="Gruvbox color scheme",
                        colors={"primary": "#d79921", "secondary": "#b8bb26",
                                "background": "#282828", "surface": "#3c3836"}),
        ]

        self.widget_styles = [
            WidgetStyle(name="Button", background="#e94560", foreground="#ffffff",
                         border_width=0, border_radius=8, padding=12),
            WidgetStyle(name="Text Field", background="#16213e", foreground="#ffffff",
                         border_color="#233554", border_width=1, border_radius=8),
            WidgetStyle(name="Card", background="#1a1a2e", foreground="#ffffff",
                         border_color="#233554", border_width=1, border_radius=12),
            WidgetStyle(name="Header", background="#0f3460", foreground="#ffffff",
                         border_width=0, border_radius=0, padding=16, font_size=18,
                         font_weight="bold"),
            WidgetStyle(name="Badge", background="#e94560", foreground="#ffffff",
                         border_width=0, border_radius=12, padding=4),
            WidgetStyle(name="Divider", background="#233554", border_width=0,
                         padding=0),
        ]

        self.preview_widgets = ["Window", "Button", "Text Field", "Card", "Badge", "Header"]

    def set_accent(self, accent: AccentColor) -> bool:
        accent_colors = {
            AccentColor.BLUE: "#4fc3f7", AccentColor.GREEN: "#6bcb77",
            AccentColor.PURPLE: "#ce93d8", AccentColor.RED: "#e57373",
            AccentColor.ORANGE: "#ffb74d", AccentColor.PINK: "#f06292",
            AccentColor.TEAL: "#4dd0e1", AccentColor.YELLOW: "#ffd54f",
        }
        if self.current_scheme:
            self.current_scheme.accent = accent
            self.current_scheme.primary = accent_colors.get(accent, "#4fc3f7")
            return True
        return False

    def set_variant(self, variant: ThemeVariant) -> bool:
        if self.current_scheme:
            self.current_scheme.variant = variant
            if variant == ThemeVariant.LIGHT:
                self.current_scheme.background = "#fafafa"
                self.current_scheme.surface = "#ffffff"
                self.current_scheme.text_primary = "#212121"
                self.current_scheme.text_secondary = "#757575"
            elif variant == ThemeVariant.DARK:
                self.current_scheme.background = "#0f0f23"
                self.current_scheme.surface = "#1a1a2e"
                self.current_scheme.text_primary = "#ffffff"
                self.current_scheme.text_secondary = "#8892b0"
            return True
        return False

    def apply_preset(self, name: str) -> bool:
        preset = next((p for p in self.presets if p.name == name), None)
        if preset and self.current_scheme:
            for key, value in preset.colors.items():
                if hasattr(self.current_scheme, key):
                    setattr(self.current_scheme, key, value)
            return True
        return False

    def create_scheme(self, name: str, **kwargs) -> ColorScheme:
        scheme = ColorScheme(name=name, **kwargs)
        self.schemes.append(scheme)
        return scheme

    def duplicate_scheme(self, name: str) -> Optional[ColorScheme]:
        source = next((s for s in self.schemes if s.name == name), None)
        if source:
            import copy
            new_scheme = copy.deepcopy(source)
            new_scheme.name = f"{name} (Copy)"
            self.schemes.append(new_scheme)
            return new_scheme
        return None

    def export_css(self, scheme: Optional[ColorScheme] = None) -> str:
        s = scheme or self.current_scheme
        if not s:
            return ""
        return s.css

    def export_gtk(self, scheme: Optional[ColorScheme] = None) -> str:
        s = scheme or self.current_scheme
        if not s:
            return ""
        return f"""[Settings]
gtk-theme-name={s.name}
gtk-application-prefer-dark-theme={'true' if s.variant == ThemeVariant.DARK else 'false'}
gtk-color-scheme=bg_color:{s.background};fg_color:{s.text_primary};selected_bg_color:{s.primary};selected_fg_color:{s.text_primary};error_color:{s.error};warning_color:{s.warning};success_color:{s.success}
"""

    def export_qt(self, scheme: Optional[ColorScheme] = None) -> str:
        s = scheme or self.current_scheme
        if not s:
            return ""
        return f"""[ColorScheme]
name={s.name}
background={s.background}
foreground={s.text_primary}
window={s.surface}
button={s.primary}
buttonText={s.text_primary}
highlight={s.primary}
highlightedText={s.text_primary}
disabledText={s.text_disabled}
"""

    def get_preview_html(self) -> str:
        s = self.current_scheme
        if not s:
            return ""
        return f"""<html><body style="background:{s.background};color:{s.text_primary};font-family:{s.font_family}">
<div style="background:{s.surface};padding:20px;border-radius:{s.corner_radius}px;border:1px solid {s.border}">
  <h2 style="color:{s.text_primary}">Preview</h2>
  <button style="background:{s.primary};color:#fff;padding:10px 20px;border:none;border-radius:{s.corner_radius}px">Button</button>
  <input style="background:{s.surface_bright};color:{s.text_primary};border:1px solid {s.border};border-radius:{s.corner_radius}px;padding:8px;margin-left:10px" value="Text Field">
  <div style="margin-top:10px;padding:12px;background:{s.surface_bright};border-radius:{s.corner_radius}px">
    <p style="color:{s.text_secondary}">Secondary text</p>
  </div>
</div></body></html>"""

    def get_stats(self) -> Dict:
        return {
            "schemes": len(self.schemes),
            "presets": len(self.presets),
            "widget_styles": len(self.widget_styles),
            "current_scheme": self.current_scheme.name if self.current_scheme else "None",
        }
