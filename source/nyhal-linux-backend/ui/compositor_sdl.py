#!/usr/bin/env python3
"""SDL2-based real-time compositor for NUI documents.

This is the high-performance rendering path for the Nyrqis shell.
It uses SDL2 (via pysdl2) for GPU-accelerated rendering and can
display the shell in a real window or render to an off-screen surface.

When DISPLAY is available: renders to a live window.
When headless (CI/dummy driver): renders to an off-screen surface
and can export to PNG.

Architecture (NUI-SCHEMA §3, §6):
- Takes a loaded NstudioDocument (or NyrqisShell)
- Applies themes (Eclipse/Solar) with full color palettes
- Renders the component tree using SDL2 surfaces/textures
- Supports 30+ NUI component types
- Can run in headless mode for CI testing

References:
- NUI-SCHEMA §3: layout system
- NUI-SCHEMA §6: themes and design tokens
- NFS-001 §4: component vocabulary
"""

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import sdl2
    import sdl2.ext
    HAS_SDL2 = True
except ImportError:
    HAS_SDL2 = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ---- Theme definitions (mirrored from compositor.py) ---------------------

THEMES = {
    "Eclipse": {
        "background": (30, 30, 30),
        "surface": (40, 40, 40),
        "surface_elevated": (50, 50, 50),
        "surface_overlay": (35, 35, 35),
        "border": (80, 80, 80),
        "text_primary": (230, 230, 230),
        "text_secondary": (150, 150, 150),
        "accent": (100, 149, 237),
        "accent_hover": (120, 169, 255),
        "button_bg": (60, 60, 60),
        "button_text": (230, 230, 230),
        "input_bg": (45, 45, 45),
        "input_border": (80, 80, 80),
        "toggle_on": (100, 149, 237),
        "toggle_off": (80, 80, 80),
        "slider_track": (60, 60, 60),
        "slider_fill": (100, 149, 237),
        "progress_bg": (60, 60, 60),
        "progress_fill": (100, 149, 237),
    },
    "Solar": {
        "background": (253, 246, 227),
        "surface": (238, 232, 213),
        "surface_elevated": (250, 244, 230),
        "surface_overlay": (245, 238, 220),
        "border": (200, 190, 170),
        "text_primary": (50, 50, 50),
        "text_secondary": (120, 110, 100),
        "accent": (38, 139, 210),
        "accent_hover": (58, 159, 230),
        "button_bg": (230, 222, 205),
        "button_text": (50, 50, 50),
        "input_bg": (245, 238, 220),
        "input_border": (200, 190, 170),
        "toggle_on": (38, 139, 210),
        "toggle_off": (180, 170, 150),
        "slider_track": (200, 190, 170),
        "slider_fill": (38, 139, 210),
        "progress_bg": (200, 190, 170),
        "progress_fill": (38, 139, 210),
    },
}


def _sdl_color(rgb: Tuple[int, int, int]) -> int:
    """Convert an (R, G, B) tuple to an SDL2 32-bit ARGB pixel value."""
    return (0xFF << 24) | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]


def _fill_rect(surface, x: int, y: int, w: int, h: int, color: Tuple[int, int, int]) -> None:
    """Fill a rectangle on an SDL2 surface."""
    rect = sdl2.SDL_Rect(x, y, w, h)
    sdl2.SDL_FillRect(surface, rect, _sdl_color(color))


def _draw_rect(surface, x: int, y: int, w: int, h: int, color: Tuple[int, int, int]) -> None:
    """Draw a 1px border rectangle on an SDL2 surface."""
    # Top
    _fill_rect(surface, x, y, w, 1, color)
    # Bottom
    _fill_rect(surface, x, y + h - 1, w, 1, color)
    # Left
    _fill_rect(surface, x, y, 1, h, color)
    # Right
    _fill_rect(surface, x + w - 1, y, 1, h, color)


def _draw_text(surface, x: int, y: int, text: str,
               color: Tuple[int, int, int], font=None) -> None:
    """Draw text on an SDL2 surface using SDL_ttf or pixel fallback."""
    try:
        import sdl2.sdlfont as font_mod
        # Use SDL_ttf if available
        rendered = font_mod.render(text, color)
        if rendered:
            sdl2.SDL_BlitSurface(rendered, None, surface,
                                 sdl2.SDL_Rect(x, y, 0, 0))
            return
    except Exception:
        pass
    # Fallback: render as pixel blocks (simple bitmap font)
    _draw_text_bitmap(surface, x, y, text, color)


# Simple 5x7 bitmap font for ASCII 32-126
_SIMPLE_FONT = {}


def _init_bitmap_font():
    """Initialize a minimal bitmap font for ASCII printable characters."""
    if _SIMPLE_FONT:
        return
    # Each character is 5 columns x 7 rows, stored as 5 bytes (MSB-first)
    # This is a very minimal set — just enough for digits and common letters
    glyphs = {
        ' ': [0x00, 0x00, 0x00, 0x00, 0x00],
        '!': [0x00, 0x00, 0x5F, 0x00, 0x00],
        '"': [0x00, 0x07, 0x00, 0x07, 0x00],
        '#': [0x14, 0x7F, 0x14, 0x7F, 0x14],
        '$': [0x24, 0x2A, 0x7F, 0x2A, 0x12],
        '%': [0x23, 0x13, 0x08, 0x64, 0x62],
        '&': [0x36, 0x49, 0x55, 0x22, 0x50],
        "'": [0x00, 0x05, 0x03, 0x00, 0x00],
        '(': [0x00, 0x1C, 0x22, 0x41, 0x00],
        ')': [0x00, 0x41, 0x22, 0x1C, 0x00],
        '*': [0x14, 0x08, 0x3E, 0x08, 0x14],
        '+': [0x08, 0x08, 0x3E, 0x08, 0x08],
        ',': [0x00, 0x50, 0x30, 0x00, 0x00],
        '-': [0x08, 0x08, 0x08, 0x08, 0x08],
        '.': [0x00, 0x60, 0x60, 0x00, 0x00],
        '/': [0x20, 0x10, 0x08, 0x04, 0x02],
        '0': [0x3E, 0x51, 0x49, 0x45, 0x3E],
        '1': [0x00, 0x42, 0x7F, 0x40, 0x00],
        '2': [0x42, 0x61, 0x51, 0x49, 0x46],
        '3': [0x21, 0x41, 0x45, 0x4B, 0x31],
        '4': [0x18, 0x14, 0x12, 0x7F, 0x10],
        '5': [0x27, 0x45, 0x45, 0x45, 0x39],
        '6': [0x3C, 0x4A, 0x49, 0x49, 0x30],
        '7': [0x01, 0x71, 0x09, 0x05, 0x03],
        '8': [0x36, 0x49, 0x49, 0x49, 0x36],
        '9': [0x06, 0x49, 0x49, 0x29, 0x1E],
        ':': [0x00, 0x36, 0x36, 0x00, 0x00],
        ';': [0x00, 0x56, 0x36, 0x00, 0x00],
        '<': [0x08, 0x14, 0x22, 0x41, 0x00],
        '=': [0x14, 0x14, 0x14, 0x14, 0x14],
        '>': [0x00, 0x41, 0x22, 0x14, 0x08],
        '?': [0x02, 0x01, 0x51, 0x09, 0x06],
        '@': [0x32, 0x49, 0x79, 0x41, 0x3E],
        'A': [0x7E, 0x11, 0x11, 0x11, 0x7E],
        'B': [0x7F, 0x49, 0x49, 0x49, 0x36],
        'C': [0x3E, 0x41, 0x41, 0x41, 0x22],
        'D': [0x7F, 0x41, 0x41, 0x22, 0x1C],
        'E': [0x7F, 0x49, 0x49, 0x49, 0x41],
        'F': [0x7F, 0x09, 0x09, 0x09, 0x01],
        'G': [0x3E, 0x41, 0x49, 0x49, 0x7A],
        'H': [0x7F, 0x08, 0x08, 0x08, 0x7F],
        'I': [0x00, 0x41, 0x7F, 0x41, 0x00],
        'J': [0x20, 0x40, 0x41, 0x3F, 0x01],
        'K': [0x7F, 0x08, 0x14, 0x22, 0x41],
        'L': [0x7F, 0x40, 0x40, 0x40, 0x40],
        'M': [0x7F, 0x02, 0x0C, 0x02, 0x7F],
        'N': [0x7F, 0x04, 0x08, 0x10, 0x7F],
        'O': [0x3E, 0x41, 0x41, 0x41, 0x3E],
        'P': [0x7F, 0x09, 0x09, 0x09, 0x06],
        'Q': [0x3E, 0x41, 0x51, 0x21, 0x5E],
        'R': [0x7F, 0x09, 0x19, 0x29, 0x46],
        'S': [0x46, 0x49, 0x49, 0x49, 0x31],
        'T': [0x01, 0x01, 0x7F, 0x01, 0x01],
        'U': [0x3F, 0x40, 0x40, 0x40, 0x3F],
        'V': [0x1F, 0x20, 0x40, 0x20, 0x1F],
        'W': [0x3F, 0x40, 0x38, 0x40, 0x3F],
        'X': [0x63, 0x14, 0x08, 0x14, 0x63],
        'Y': [0x07, 0x08, 0x70, 0x08, 0x07],
        'Z': [0x61, 0x51, 0x49, 0x45, 0x43],
        'a': [0x20, 0x54, 0x54, 0x54, 0x78],
        'b': [0x7F, 0x48, 0x44, 0x44, 0x38],
        'c': [0x38, 0x44, 0x44, 0x44, 0x20],
        'd': [0x38, 0x44, 0x44, 0x48, 0x7F],
        'e': [0x38, 0x54, 0x54, 0x54, 0x18],
        'f': [0x08, 0x7E, 0x09, 0x01, 0x02],
        'g': [0x0C, 0x52, 0x52, 0x52, 0x3E],
        'h': [0x7F, 0x08, 0x04, 0x04, 0x78],
        'i': [0x00, 0x44, 0x7D, 0x40, 0x00],
        'j': [0x20, 0x40, 0x44, 0x3D, 0x00],
        'k': [0x7F, 0x10, 0x28, 0x44, 0x00],
        'l': [0x00, 0x41, 0x7F, 0x40, 0x00],
        'm': [0x7C, 0x04, 0x18, 0x04, 0x78],
        'n': [0x7C, 0x08, 0x04, 0x04, 0x78],
        'o': [0x38, 0x44, 0x44, 0x44, 0x38],
        'p': [0x7C, 0x14, 0x14, 0x14, 0x08],
        'q': [0x08, 0x14, 0x14, 0x18, 0x7C],
        'r': [0x7C, 0x08, 0x04, 0x04, 0x08],
        's': [0x48, 0x54, 0x54, 0x54, 0x20],
        't': [0x04, 0x3F, 0x44, 0x40, 0x20],
        'u': [0x3C, 0x40, 0x40, 0x20, 0x7C],
        'v': [0x1C, 0x20, 0x40, 0x20, 0x1C],
        'w': [0x3C, 0x40, 0x30, 0x40, 0x3C],
        'x': [0x44, 0x28, 0x10, 0x28, 0x44],
        'y': [0x0C, 0x50, 0x50, 0x50, 0x3C],
        'z': [0x44, 0x64, 0x54, 0x4C, 0x44],
        '[': [0x00, 0x7F, 0x41, 0x41, 0x00],
        '\\': [0x02, 0x04, 0x08, 0x10, 0x20],
        ']': [0x00, 0x41, 0x41, 0x7F, 0x00],
        '^': [0x04, 0x02, 0x01, 0x02, 0x04],
        '_': [0x40, 0x40, 0x40, 0x40, 0x40],
        '`': [0x00, 0x01, 0x02, 0x04, 0x00],
        '{': [0x00, 0x14, 0x7F, 0x41, 0x00],
        '|': [0x00, 0x00, 0x7F, 0x00, 0x00],
        '}': [0x00, 0x41, 0x7F, 0x14, 0x00],
        '~': [0x10, 0x08, 0x08, 0x10, 0x08],
    }
    for ch, data in glyphs.items():
        _SIMPLE_FONT[ch] = data


def _draw_text_bitmap(surface, x: int, y: int, text: str,
                      color: Tuple[int, int, int]) -> None:
    """Draw text using a simple 5x7 bitmap font."""
    _init_bitmap_font()
    pixel_color = _sdl_color(color)
    cx = x
    for ch in text:
        glyph = _SIMPLE_FONT.get(ch, _SIMPLE_FONT.get('?', [0x3E]*5))
        for col_idx, col_bits in enumerate(glyph):
            for row in range(7):
                if col_bits & (1 << (6 - row)):
                    px = cx + col_idx
                    py = y + row
                    rect = sdl2.SDL_Rect(px, py, 1, 1)
                    sdl2.SDL_FillRect(surface, rect, pixel_color)
        cx += 6  # 5 pixels wide + 1 pixel gap


class SDLCompositor:
    """SDL2-based compositor that renders NUI documents.

    Parameters
    ----------
    theme_name : str
        The theme to use ("Eclipse" or "Solar").
    scale : float
        Rendering scale factor (1.0 = native, 2.0 = retina).
    headless : bool
        If True, render to an off-screen surface (for CI/testing).
        If False, attempt to create a window.
    """

    def __init__(
        self,
        theme_name: str = "Eclipse",
        scale: float = 1.0,
        headless: bool = True,
    ) -> None:
        if not HAS_SDL2:
            raise ImportError("pysdl2 is required: pip install pysdl2 pysdl2-dll")
        self.theme_name = theme_name
        self.theme = THEMES.get(theme_name, THEMES["Eclipse"])
        self.scale = scale
        self.headless = headless
        self._window = None
        self._renderer = None
        self._surface = None

    def _init_sdl(self, width: int, height: int) -> None:
        """Initialize SDL2 with either a window or off-screen surface."""
        if self.headless:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO)
        if not self.headless:
            self._window = sdl2.SDL_CreateWindow(
                b"Nyrqis Shell",
                sdl2.SDL_WINDOWPOS_CENTERED, sdl2.SDL_WINDOWPOS_CENTERED,
                width, height,
                sdl2.SDL_WINDOW_SHOWN,
            )
            self._renderer = sdl2.SDL_CreateRenderer(
                self._window, -1,
                sdl2.SDL_RENDERER_ACCELERATED | sdl2.SDL_RENDERER_PRESENTVSYNC,
            )
        else:
            self._surface = sdl2.SDL_CreateRGBSurfaceWithFormat(
                0, width, height, 32, sdl2.SDL_PIXELFORMAT_ARGB8888,
            )

    def _cleanup(self) -> None:
        """Clean up SDL2 resources."""
        if self._renderer:
            sdl2.SDL_DestroyRenderer(self._renderer)
            self._renderer = None
        if self._window:
            sdl2.SDL_DestroyWindow(self._window)
            self._window = None
        if self._surface:
            sdl2.SDL_FreeSurface(self._surface)
            self._surface = None
        sdl2.SDL_Quit()

    def render_screen(
        self,
        document: Any,
        screen_id: Optional[str] = None,
    ) -> Optional[Any]:
        """Render a screen from a NstudioDocument.

        Returns
        -------
        PIL.Image.Image or None
            When headless, returns a PIL Image (for PNG export).
            When windowed, returns None (displayed on screen).
        """
        from ui.nstudio import NstudioScreen

        # Find the screen
        screen = None
        for s in document.screens:
            if screen_id is None or s.id == screen_id:
                screen = s
                break
        if screen is None:
            raise ValueError(f"Screen '{screen_id}' not found")

        w = int(screen.size.get("width", 1440) * self.scale)
        h = int(screen.size.get("height", 900) * self.scale)

        try:
            self._init_sdl(w, h)

            if self.headless:
                self._render_to_surface(screen.root, document)
                return self._surface_to_pil(self._surface, w, h)
            else:
                self._render_to_window(screen.root, document)
                return None
        finally:
            self._cleanup()

    def _render_to_surface(self, root: Any, document: Any) -> None:
        """Render the component tree to an off-screen SDL2 surface."""
        surface = self._surface
        bg = _sdl_color(self.theme["background"])
        sdl2.SDL_FillRect(surface, None, bg)

        self._render_component(surface, root, document)

    def _render_to_window(self, root: Any, document: Any) -> None:
        """Render the component tree to a live window."""
        renderer = self._renderer
        bg = self.theme["background"]
        sdl2.SDL_SetRenderDrawColor(renderer, bg[0], bg[1], bg[2], 0xFF)
        sdl2.SDL_RenderClear(renderer)

        self._render_component(renderer, root, document)

        sdl2.SDL_RenderPresent(renderer)
        # Keep window open briefly for display
        import time
        time.sleep(0.5)

    def _render_component(self, target, comp: Any, document: Any) -> None:
        """Render a single component and its children."""
        layout = getattr(comp, "layout", {})
        x = int(layout.get("x", 0) * self.scale)
        y = int(layout.get("y", 0) * self.scale)
        w = int(layout.get("width", 100) * self.scale)
        h = int(layout.get("height", 30) * self.scale)

        props = getattr(comp, "properties", {})
        comp_type = getattr(comp, "type", "Unknown")

        # Use the headless flag to determine rendering path — surfaces
        # for headless, renderer for windowed.  Avoids fragile attribute
        # detection on pysdl2 wrapper objects.
        if self.headless:
            self._render_on_surface(target, x, y, w, h, props, comp_type, comp, document)
        else:
            self._render_on_renderer(target, x, y, w, h, props, comp_type, comp, document)

        # Render children
        children = getattr(comp, "children", [])
        leaf_types = {
            "Text", "Label", "Heading", "Paragraph",
            "Button", "Link", "Input", "PasswordField", "Search",
            "Toggle", "Checkbox", "Radio", "Slider", "ProgressBar",
            "Image", "Icon", "DesktopIcon", "Clock", "MenuItem",
        }
        if children and comp_type not in leaf_types:
            for child in children:
                self._render_component(target, child, document)

    def _render_on_surface(self, surface, x, y, w, h, props, comp_type, comp, document):
        """Render a component onto an SDL2 surface."""
        theme = self.theme
        if comp_type == "Window":
            _fill_rect(surface, x, y, w, h, theme["background"])
        elif comp_type == "DesktopSurface":
            _fill_rect(surface, x, y, w, h, theme["surface"])
        elif comp_type == "Taskbar":
            _fill_rect(surface, x, y, w, h, theme["surface_overlay"])
            _draw_rect(surface, x, y, w, 1, theme["border"])
        elif comp_type == "StartMenu":
            _fill_rect(surface, x, y, w, h, theme["surface_elevated"])
            _draw_rect(surface, x, y, w, h, theme["border"])
            _draw_text_bitmap(surface, x + 16, y + 16, "Start Menu",
                              theme["text_primary"])
        elif comp_type in ("Button", "Link"):
            text = props.get("text", "Button")
            _fill_rect(surface, x, y, w, h, theme["button_bg"])
            _draw_text_bitmap(surface, x + 8, y + 8, text,
                              theme["button_text"])
        elif comp_type in ("Text", "Label", "Heading", "Paragraph"):
            text = props.get("text", comp.id)
            _draw_text_bitmap(surface, x, y, text, theme["text_primary"])
        elif comp_type in ("Input", "PasswordField", "Search"):
            _fill_rect(surface, x, y, w, h, theme["input_bg"])
            _draw_rect(surface, x, y, w, h, theme["input_border"])
            placeholder = props.get("placeholder", "")
            if placeholder:
                _draw_text_bitmap(surface, x + 8, y + 8, placeholder,
                                  theme["text_secondary"])
        elif comp_type in ("Toggle", "Checkbox", "Radio"):
            value = props.get("value", False)
            color = theme["toggle_on"] if value else theme["toggle_off"]
            _fill_rect(surface, x, y, 40, 20, color)
            label = props.get("label", props.get("text", ""))
            if label:
                _draw_text_bitmap(surface, x + 48, y + 2, label,
                                  theme["text_primary"])
        elif comp_type == "Slider":
            _fill_rect(surface, x, y + h // 2 - 2, w, 4, theme["slider_track"])
            value = props.get("value", 50)
            min_val = props.get("min", 0)
            max_val = props.get("max", 100)
            fill_w = int(w * (value - min_val) / (max_val - min_val)) if max_val > min_val else 0
            _fill_rect(surface, x, y + h // 2 - 2, fill_w, 4, theme["slider_fill"])
        elif comp_type == "ProgressBar":
            _fill_rect(surface, x, y, w, h, theme["progress_bg"])
            value = props.get("value", 60)
            min_val = props.get("min", 0)
            max_val = props.get("max", 100)
            fill_w = int(w * (value - min_val) / (max_val - min_val)) if max_val > min_val else 0
            if fill_w > 0:
                _fill_rect(surface, x, y, fill_w, h, theme["progress_fill"])
        elif comp_type == "DesktopIcon":
            glyph = props.get("glyph", "?")
            label = props.get("label", "")
            icon_size = min(w, h - 20)
            ix = x + (w - icon_size) // 2
            _fill_rect(surface, ix, y, icon_size, icon_size,
                       theme["surface_elevated"])
            _draw_text_bitmap(surface, ix + icon_size // 2 - 3,
                              y + icon_size // 2 - 3, glyph,
                              theme["text_primary"])
            if label:
                _draw_text_bitmap(surface, x + 4, y + icon_size + 4, label,
                                  theme["text_primary"])
        elif comp_type == "Clock":
            time_str = props.get("time", "12:00")
            _draw_text_bitmap(surface, x + 4, y + 4, time_str,
                              theme["text_primary"])
        elif comp_type == "TitleBar":
            title = props.get("title", "Window")
            _fill_rect(surface, x, y, w, h, theme["surface_overlay"])
            _draw_text_bitmap(surface, x + 8, y + 8, title,
                              theme["text_primary"])
        elif comp_type == "WindowControls":
            _draw_text_bitmap(surface, x + 4, y + 4, "--",
                              theme["text_secondary"])
        elif comp_type == "LockScreen":
            _fill_rect(surface, x, y, w, h, (20, 20, 40))
            time_str = props.get("clockTime", "12:00")
            _draw_text_bitmap(surface, x + w // 2 - 15, y + h // 2 - 4,
                              time_str, (255, 255, 255))
        elif comp_type == "ContextMenu":
            _fill_rect(surface, x, y, w, h, theme["surface_elevated"])
            _draw_rect(surface, x, y, w, h, theme["border"])
        elif comp_type == "MenuItem":
            label = props.get("label", props.get("text", "Item"))
            _draw_text_bitmap(surface, x + 8, y + 6, label,
                              theme["text_primary"])
        elif comp_type == "NotificationCenter":
            _fill_rect(surface, x, y, w, h, theme["surface_elevated"])
            _draw_rect(surface, x, y, w, h, theme["border"])
            _draw_text_bitmap(surface, x + 16, y + 12, "Notifications",
                              theme["text_primary"])
        elif comp_type == "QuickSettings":
            _fill_rect(surface, x, y, w, h, theme["surface_elevated"])
            _draw_rect(surface, x, y, w, h, theme["border"])
            _draw_text_bitmap(surface, x + 16, y + 12, "Quick Settings",
                              theme["text_primary"])
        elif comp_type == "Launcher":
            _fill_rect(surface, x, y, w, h, theme["surface_elevated"])
            _draw_rect(surface, x, y, w, h, theme["border"])
            _draw_text_bitmap(surface, x + 16, y + 12, "Launcher",
                              theme["text_primary"])
        elif comp_type == "CommandPalette":
            _fill_rect(surface, x, y, w, h, theme["surface_elevated"])
            _draw_rect(surface, x, y, w, h, theme["border"])
            _draw_text_bitmap(surface, x + 16, y + 12, "Command Palette",
                              theme["text_primary"])
        elif comp_type == "WorkspaceSwitcher":
            current = props.get("currentWorkspace", 1)
            total = props.get("workspaces", 3)
            for i in range(1, total + 1):
                cx = x + (i - 1) * 20 + 4
                color = theme["accent"] if i == current else theme["toggle_off"]
                _fill_rect(surface, cx, y + 4, 12, 12, color)
        elif comp_type == "SystemTray":
            icons = props.get("icons", [])
            for i, icon in enumerate(icons[:3]):
                _draw_text_bitmap(surface, x + i * 24, y + 4, icon[:1],
                                  theme["text_secondary"])
        elif comp_type in ("Container", "Stack", "Grid", "Panel", "Card",
                           "WindowFrame", "Dock", "SplitView", "ScrollView",
                           "Tabs", "FlexLayout", "AppGrid", "List"):
            pass  # Containers are transparent
        else:
            _fill_rect(surface, x, y, w, h, theme["surface_elevated"])
            _draw_rect(surface, x, y, w, h, theme["border"])

    def _render_on_renderer(self, renderer, x, y, w, h, props, comp_type, comp, document):
        """Render a component using SDL2 renderer (windowed mode)."""
        theme = self.theme
        if comp_type == "Window":
            sdl2.SDL_SetRenderDrawColor(renderer, *theme["background"], 0xFF)
            sdl2.SDL_RenderFillRect(renderer, sdl2.SDL_Rect(x, y, w, h))
        elif comp_type == "Taskbar":
            sdl2.SDL_SetRenderDrawColor(renderer, *theme["surface_overlay"], 0xFF)
            sdl2.SDL_RenderFillRect(renderer, sdl2.SDL_Rect(x, y, w, h))
        elif comp_type == "LockScreen":
            sdl2.SDL_SetRenderDrawColor(renderer, 20, 20, 40, 0xFF)
            sdl2.SDL_RenderFillRect(renderer, sdl2.SDL_Rect(x, y, w, h))
        else:
            # For windowed mode, use fill rect as placeholder
            sdl2.SDL_SetRenderDrawColor(renderer, *theme["surface"], 0xFF)
            sdl2.SDL_RenderFillRect(renderer, sdl2.SDL_Rect(x, y, w, h))

    def _surface_to_pil(self, surface, w: int, h: int):
        """Convert an SDL2 surface to a PIL Image."""
        if not HAS_PIL:
            raise ImportError("Pillow is required for surface_to_pil")
        import ctypes
        sdl2.SDL_LockSurface(surface)
        try:
            ptr = surface.contents.pixels  # raw pointer (int)
            pitch = surface.contents.pitch
            buf = ctypes.string_at(ptr, pitch * h)
            # ARGB8888 in memory order (little-endian) = B, G, R, A
            img = Image.frombytes("RGB", (w, h), buf, "raw", "BGRX", pitch)
        finally:
            sdl2.SDL_UnlockSurface(surface)
        return img

    def render_to_file(
        self,
        document: Any,
        path: str,
        screen_id: Optional[str] = None,
        theme_name: Optional[str] = None,
    ) -> str:
        """Render a screen and save to a PNG file.

        Returns the output path.
        """
        old_theme = self.theme_name
        if theme_name:
            self.theme_name = theme_name
            self.theme = THEMES.get(theme_name, THEMES["Eclipse"])

        try:
            img = self.render_screen(document, screen_id=screen_id)
            if img is not None:
                img.save(path)
                return path
        finally:
            self.theme_name = old_theme
            self.theme = THEMES.get(old_theme, THEMES["Eclipse"])
        return path


__all__ = ["SDLCompositor", "THEMES", "HAS_SDL2"]
