#!/usr/bin/env python3
"""Apple-quality compositor for Nyrqis — Gaussian shadows, rounded corners,
vibrancy/blur effects, gradients, and smooth animations.

This is the premium rendering path: it takes a live DesktopSession and
produces a composited image that looks like a macOS Sequoia / iOS 18
desktop.  The floor-level PIL compositor (compositor.py) remains the
fast/dependency-free fallback; this module adds the visual polish when
Pillow is available.

Design principles (Apple HIG):
  - Depth through shadows, not outlines
  - Rounded corners everywhere (radius 10–16 px)
  - Translucent/vibrant surfaces (frosted glass)
  - Subtle gradients for surfaces and accents
  - 1 px hairline borders only where semantically needed
  - Typography: San Francisco-like weights, generous line height
  - Animations: ease-in-out with spring-like overshoot

References:
  - Apple Human Interface Guidelines 2024
  - macOS Sequoia visual language
  - iOS 18 Dynamic Island / Control Center
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

# Lazy PIL
_PIL_AVAILABLE: Optional[bool] = None


def _ensure_pil():
    global _PIL_AVAILABLE
    if _PIL_AVAILABLE is not None:
        if _PIL_AVAILABLE is False:
            raise ImportError("PIL/Pillow is required: pip install Pillow")
        return
    try:
        from PIL import Image as _Img  # noqa: F401
        _PIL_AVAILABLE = True
    except ImportError:
        _PIL_AVAILABLE = False
        raise ImportError("PIL/Pillow is required: pip install Pillow")


def _pil():
    _ensure_pil()
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    return Image, ImageDraw, ImageFont, ImageFilter


# ---------------------------------------------------------------------------
# Apple Design Tokens
# ---------------------------------------------------------------------------

APPLE_RADIUS = 12           # Standard corner radius
APPLE_RADIUS_SM = 8         # Small radius (buttons, inputs)
APPLE_RADIUS_LG = 16        # Large radius (modals, panels)
APPLE_RADIUS_XL = 20        # Extra-large (windows, sheets)

APPLE_SHADOW_OFFSET = (0, 4)
APPLE_SHADOW_BLUR = 16
APPLE_SHADOW_COLOR = (0, 0, 0, 60)

APPLE_HAIRLINE = 1          # 1 px border width

# Vibrancy alpha (frosted glass)
VIBRANCY_ALPHA = 210        # 82% opacity for panels
VIBRANCY_BLUR_RADIUS = 20   # Gaussian blur radius for background

# Apple system colors (dark mode)
APPLE_COLORS_DARK = {
    "background":        (28,  28,  30),
    "surface":           (44,  44,  46),
    "surface_elevated":  (58,  58,  60),
    "surface_overlay":   (36,  36,  38),
    "surface_vibrant":   (28,  28,  30, VIBRANCY_ALPHA),
    "border":            (56,  56,  58),
    "hairline":          (88,  88,  90),
    "text_primary":      (255, 255, 255),
    "text_secondary":    (142, 142, 147),
    "text_tertiary":     (99,  99,  102),
    "accent":            (10,  132, 255),
    "accent_hover":      (30,  152, 255),
    "accent_pressed":    (0,  112, 210),
    "green":             (48,  209, 88),
    "red":               (255, 69,  58),
    "orange":            (255, 159, 10),
    "yellow":            (255, 214, 10),
    "purple":            (191, 90,  242),
    "pink":              (255, 55,  95),
    "teal":              (100, 210, 255),
    "button_bg":         (58,  58,  60),
    "button_text":       (255, 255, 255),
    "input_bg":          (28,  28,  30),
    "input_border":      (56,  56,  58),
    "toggle_on":         (48,  209, 88),
    "toggle_off":        (99,  99,  102),
    "slider_track":      (58,  58,  60),
    "slider_fill":       (10,  132, 255),
    "progress_bg":       (58,  58,  60),
    "progress_fill":     (10,  132, 255),
    "window_title_bg":   (36,  36,  38),
    "titlebar_text":     (255, 255, 255),
    "close_btn":         (255, 69,  58),
    "minimize_btn":      (255, 159, 10),
    "maximize_btn":      (48,  209, 88),
    "menu_bg":           (36,  36,  38, 230),
    "menu_hover":        (58,  58,  60, 200),
    "menu_separator":    (56,  56,  58),
    "notification_bg":   (44,  44,  46, 240),
    "widget_bg":         (44,  44,  46, 200),
    "start_menu_bg":     (36,  36,  38, 230),
    "taskbar_bg":        (36,  36,  38, 220),
}

# Apple system colors (light mode)
APPLE_COLORS_LIGHT = {
    "background":        (242, 242, 247),
    "surface":           (255, 255, 255),
    "surface_elevated":  (255, 255, 255),
    "surface_overlay":   (242, 242, 247),
    "surface_vibrant":   (255, 255, 255, VIBRANCY_ALPHA),
    "border":            (210, 210, 215),
    "hairline":          (200, 200, 205),
    "text_primary":      (0,   0,   0),
    "text_secondary":    (120, 120, 128),
    "text_tertiary":     (174, 174, 178),
    "accent":            (0,   122, 255),
    "accent_hover":      (0,   102, 235),
    "accent_pressed":    (0,   82,  215),
    "green":             (52,  199, 89),
    "red":               (255, 59,  48),
    "orange":            (255, 149, 0),
    "yellow":            (255, 204, 0),
    "purple":            (175, 82,  222),
    "pink":              (255, 45,  85),
    "teal":              (90,  200, 250),
    "button_bg":         (240, 240, 245),
    "button_text":       (0,   0,   0),
    "input_bg":          (255, 255, 255),
    "input_border":      (210, 210, 215),
    "toggle_on":         (52,  199, 89),
    "toggle_off":        (174, 174, 178),
    "slider_track":      (210, 210, 215),
    "slider_fill":       (0,   122, 255),
    "progress_bg":       (210, 210, 215),
    "progress_fill":     (0,   122, 255),
    "window_title_bg":   (232, 232, 237),
    "titlebar_text":     (0,   0,   0),
    "close_btn":         (255, 59,  48),
    "minimize_btn":      (255, 149, 0),
    "maximize_btn":      (52,  199, 89),
    "menu_bg":           (255, 255, 255, 240),
    "menu_hover":        (240, 240, 245, 200),
    "menu_separator":    (210, 210, 215),
    "notification_bg":   (255, 255, 255, 245),
    "widget_bg":         (255, 255, 255, 210),
    "start_menu_bg":     (245, 245, 250, 235),
    "taskbar_bg":        (245, 245, 250, 225),
}


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_color(c1: Tuple, c2: Tuple, t: float) -> Tuple:
    return tuple(int(_lerp(a, b, t)) for a, b in zip(c1, c2))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _ease_in_out_cubic(t: float) -> float:
    """Apple-style ease-in-out (cubic)."""
    if t < 0.5:
        return 4 * t * t * t
    return 1 - pow(-2 * t + 2, 3) / 2


def _spring_interpolate(t: float, damping: float = 0.7,
                        frequency: float = 1.5) -> float:
    """Spring-like interpolation with overshoot."""
    return 1 - math.exp(-damping * t * 10) * math.cos(frequency * t * 10)


# ---------------------------------------------------------------------------
# Shadow / Blur helpers
# ---------------------------------------------------------------------------

def _create_shadow(Image, ImageFilter, width: int, height: int,
                   color: Tuple = (0, 0, 0), alpha: int = 60,
                   offset: Tuple = APPLE_SHADOW_OFFSET,
                   blur: int = APPLE_SHADOW_BLUR,
                   radius: int = APPLE_RADIUS_XL) -> Any:
    """Create a soft drop shadow for a rounded rectangle.

    Returns an RGBA image that can be composited behind the element.
    """
    # Create a black rounded rectangle with alpha
    shadow = Image.new("RGBA", (width + blur * 2, height + blur * 2), (0, 0, 0, 0))
    from PIL import ImageDraw as _Draw
    draw = _Draw.Draw(shadow)
    draw.rounded_rectangle(
        [blur, blur, blur + width, blur + height],
        radius=radius,
        fill=(*color, alpha),
    )
    # Blur for soft shadow
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=blur))
    # Offset
    offset_shadow = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
    offset_shadow.paste(shadow, (offset[0], offset[1]), shadow)
    return offset_shadow


def _create_vibrancy_overlay(Image, bg_color: Tuple, alpha: int = VIBRANCY_ALPHA,
                              tint: Tuple = (255, 255, 255)) -> Any:
    """Create a translucent vibrancy overlay (frosted glass effect).

    Blends a semi-transparent tint over the background color to
    simulate the macOS vibrancy effect.
    """
    r = int(_lerp(bg_color[0], tint[0], 0.1))
    g = int(_lerp(bg_color[1], tint[1], 0.1))
    b = int(_lerp(bg_color[2], tint[2], 0.1))
    return (*_clamp_color((r, g, b)), alpha)


def _clamp_color(c: Tuple) -> Tuple:
    return tuple(max(0, min(255, v)) for v in c)


def _create_gradient(Image, width: int, height: int,
                     color_top: Tuple, color_bottom: Tuple,
                     direction: str = "vertical") -> Any:
    """Create a gradient image (vertical or horizontal)."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for y in range(height):
        t = y / max(height - 1, 1)
        c = _lerp_color(color_top, color_bottom, t)
        from PIL import ImageDraw as _Draw
        draw = _Draw.Draw(img)
        if direction == "vertical":
            draw.line([(0, y), (width, y)], fill=(*c, 255))
        else:
            for x in range(width):
                tx = x / max(width - 1, 1)
                cx = _lerp_color(color_top, color_bottom, tx)
                draw.point((x, y), fill=(*cx, 255))
    return img


# ---------------------------------------------------------------------------
# AppleCompositor
# ---------------------------------------------------------------------------

class AppleCompositor:
    """Premium compositor with Apple-quality visual effects.

    Parameters
    ----------
    dark_mode : bool
        Use dark mode colors (True) or light mode (False).
    scale : float
        Rendering scale factor (1.0 = native, 2.0 = retina).
    """

    _font_cache: Dict[Tuple[str, int], Any] = {}

    def __init__(self, dark_mode: bool = True, scale: float = 1.0) -> None:
        self.dark_mode = dark_mode
        self.colors = APPLE_COLORS_DARK if dark_mode else APPLE_COLORS_LIGHT
        self.scale = scale

    @classmethod
    def _get_font(cls, path: str, size: int) -> Any:
        key = (path, size)
        if key not in cls._font_cache:
            _, _, ImageFont, _ = _pil()
            try:
                cls._font_cache[key] = ImageFont.truetype(path, size)
            except (OSError, IOError):
                cls._font_cache[key] = ImageFont.load_default()
        return cls._font_cache[key]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render_session(self, session: Any) -> Any:
        """Render a live DesktopSession to a composited PIL Image.

        Applies Apple-quality effects: drop shadows, rounded corners,
        vibrancy panels, gradients, and proper typography.
        """
        Image, ImageDraw, ImageFont, ImageFilter = _pil()

        doc = session.document
        if not doc.screens:
            raise RuntimeError("no screens in document")

        screen = doc.screens[0]
        sw = int(screen.size.get("width", 1440) * self.scale)
        sh = int(screen.size.get("height", 900) * self.scale)

        # Base layer: background gradient
        bg_top = self._c("background")
        bg_bottom = _lerp_color(bg_top, (0, 0, 0), 0.05)
        img = Image.new("RGB", (sw, sh), bg_top)

        # Create a subtle gradient overlay
        gradient = _create_gradient(Image, sw, sh, bg_top, bg_bottom)
        img.paste(Image.alpha_composite(
            img.convert("RGBA"), gradient).convert("RGB"))

        # Shadow pass: create shadow layer for all windows
        shadow_layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        for win in session.windows:
            if not win.visible or win.minimized:
                continue
            shadow = _create_shadow(
                Image, ImageFilter,
                int(win.width * self.scale),
                int(win.height * self.scale),
                color=(0, 0, 0),
                alpha=45,
                offset=(0, int(6 * self.scale)),
                blur=int(24 * self.scale),
                radius=int(APPLE_RADIUS_XL * self.scale),
            )
            sx = int(win.x * self.scale) - int(APPLE_SHADOW_BLUR * self.scale) + int(APPLE_SHADOW_OFFSET[0] * self.scale)
            sy = int(win.y * self.scale) - int(APPLE_SHADOW_BLUR * self.scale) + int(APPLE_SHADOW_OFFSET[1] * self.scale)
            # Clamp to image bounds
            if 0 <= sx < sw and 0 <= sy < sh:
                shadow_layer.paste(shadow, (sx, sy), shadow)

        # Composite shadows
        img = Image.alpha_composite(img.convert("RGBA"), shadow_layer).convert("RGB")

        # Render each visible window
        draw = ImageDraw.Draw(img)

        # Load fonts
        _sans = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        _sans_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font = self._get_font(_sans, int(13 * self.scale))
        font_small = self._get_font(_sans, int(11 * self.scale))
        font_title = self._get_font(_sans_bold, int(14 * self.scale))
        font_large = self._get_font(_sans_bold, int(24 * self.scale))
        font_huge = self._get_font(_sans_bold, int(48 * self.scale))

        # Render document component tree (non-window elements: taskbar, etc.)
        self._render_component_tree(img, draw, screen.root, font, font_small,
                                     font_title, font_large, doc)

        # Render windows with Apple chrome
        for win in session.windows:
            if not win.visible or win.minimized:
                continue
            self._render_apple_window(
                img, draw, win, font, font_small, font_title, doc)

        return img

    def render_document(self, doc: Any, screen_id: str = None) -> Any:
        """Render a NstudioDocument directly (no session needed)."""
        Image, ImageDraw, ImageFont, ImageFilter = _pil()

        if not doc.screens:
            raise RuntimeError("no screens in document")

        screen = None
        for s in doc.screens:
            if screen_id is None or s.id == screen_id:
                screen = s
                break
        if screen is None:
            raise ValueError(f"Screen '{screen_id}' not found")

        sw = int(screen.size.get("width", 1440) * self.scale)
        sh = int(screen.size.get("height", 900) * self.scale)

        # Background
        bg = self._c("background")
        img = Image.new("RGB", (sw, sh), bg)
        draw = ImageDraw.Draw(img)

        # Fonts
        _sans = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        _sans_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font = self._get_font(_sans, int(13 * self.scale))
        font_small = self._get_font(_sans, int(11 * self.scale))
        font_title = self._get_font(_sans_bold, int(14 * self.scale))
        font_large = self._get_font(_sans_bold, int(24 * self.scale))
        font_huge = self._get_font(_sans_bold, int(48 * self.scale))

        self._render_component_tree(img, draw, screen.root, font, font_small,
                                     font_title, font_large, doc)
        return img

    # ------------------------------------------------------------------
    # Window rendering (Apple chrome)
    # ------------------------------------------------------------------

    def _render_apple_window(self, img, draw, win, font, fs, ft, doc):
        """Render a single window with Apple-quality chrome."""
        Image, ImageDraw, ImageFont, ImageFilter = _pil()
        s = self.scale
        x = int(win.x * s)
        y = int(win.y * s)
        w = int(win.width * s)
        h = int(win.height * s)
        r = int(APPLE_RADIUS_XL * s)
        titlebar_h = int(38 * s)

        # Window body (rounded rectangle with vibrancy)
        body = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        body_draw = ImageDraw.Draw(body)
        bg_color = self._c("surface")
        body_draw.rounded_rectangle(
            [0, 0, w - 1, h - 1], radius=r,
            fill=(*bg_color, 255))

        # Vibrancy tint on the body
        vibrancy = _create_vibrancy_overlay(
            Image, bg_color, alpha=240,
            tint=(255, 255, 255) if self.dark_mode else (0, 0, 0))
        body_draw.rounded_rectangle(
            [0, 0, w - 1, h - 1], radius=r, fill=vibrancy)

        # Paste window onto main image
        img.paste(body.convert("RGB"), (x, y), body.split()[3])

        # Title bar (slightly different shade)
        titlebar = Image.new("RGBA", (w, titlebar_h), (0, 0, 0, 0))
        tb_draw = ImageDraw.Draw(titlebar)
        tb_color = self._c("window_title_bg")
        tb_draw.rounded_rectangle(
            [0, 0, w - 1, titlebar_h + int(6 * s)],
            radius=r, fill=(*tb_color, 255))

        # Paste titlebar (only the top part, masked by rounded corners)
        img.paste(
            titlebar.convert("RGB"),
            (x, y),
            titlebar.split()[3])

        # Hairline border at bottom of titlebar
        draw = ImageDraw.Draw(img)
        hairline_color = self._c("hairline")
        draw.line(
            [(x + int(4 * s), y + titlebar_h),
             (x + w - int(4 * s), y + titlebar_h)],
            fill=hairline_color, width=1)

        # Window title (centered, like macOS)
        title = win.title or win.component_id
        bbox = draw.textbbox((0, 0), title, font=fs)
        tw = bbox[2] - bbox[0]
        tx = x + (w - tw) // 2
        ty = y + int(10 * s)
        draw.text((tx, ty), title, fill=self._c("titlebar_text"), font=fs)

        # Traffic light buttons (close, minimize, maximize)
        btn_y = y + int(10 * s)
        btn_r = int(7 * s)
        btn_gap = int(8 * s)
        btn_colors = [
            self._c("close_btn"),
            self._c("minimize_btn"),
            self._c("maximize_btn"),
        ]
        btn_glyphs = ["×", "−", "□"]

        for i, (color, glyph) in enumerate(zip(btn_colors, btn_glyphs)):
            bx = x + int(14 * s) + i * (btn_r * 2 + btn_gap)
            by = btn_y + btn_r

            # Button circle
            draw.ellipse(
                [bx - btn_r, by - btn_r, bx + btn_r, by + btn_r],
                fill=color)

            # Glyph (white, small)
            g_bbox = draw.textbbox((0, 0), glyph, font=fs)
            gw = g_bbox[2] - g_bbox[0]
            gh = g_bbox[3] - g_bbox[1]
            draw.text(
                (bx - gw // 2, by - gh // 2 - int(1 * s)),
                glyph, fill=(255, 255, 255), font=fs)

        # Resize grip (bottom-right corner, subtle)
        grip_color = self._c("hairline")
        grip_x = x + w - int(12 * s)
        grip_y = y + h - int(12 * s)
        for i in range(3):
            draw.ellipse(
                [grip_x + i * int(4 * s), grip_y,
                 grip_x + i * int(4 * s) + int(2 * s), grip_y + int(2 * s)],
                fill=grip_color)

    # ------------------------------------------------------------------
    # Component tree rendering
    # ------------------------------------------------------------------

    def _render_component_tree(self, img, draw, root, font, fs, ft, fl, doc):
        """Render the full component tree from a screen root."""
        self._render_node(img, draw, root, font, fs, ft, fl, doc, 0, 0)

    def _render_node(self, img, draw, comp, font, fs, ft, fl, doc, off_x, off_y):
        """Recursively render a component and its children."""
        layout = getattr(comp, "layout", {})
        props = getattr(comp, "properties", {})
        comp_type = getattr(comp, "type", "Unknown")

        # Absolute position (relative to parent offset)
        x = off_x + int(layout.get("x", 0) * self.scale)
        y = off_y + int(layout.get("y", 0) * self.scale)
        w = int(layout.get("width", 100) * self.scale)
        h = int(layout.get("height", 30) * self.scale)

        # Render based on type
        if comp_type == "Taskbar":
            self._render_taskbar(img, draw, x, y, w, h, props, comp, font, fs, ft, doc)
        elif comp_type == "StartMenu":
            self._render_start_menu(img, draw, x, y, w, h, props, comp, font, fs, ft, doc)
        elif comp_type == "Button":
            self._render_button(img, draw, x, y, w, h, props, fs)
        elif comp_type in ("Text", "Label", "Heading"):
            f = ft if comp_type == "Heading" else font
            draw.text((x, y), props.get("text", comp.id),
                      fill=self._c("text_primary"), font=f)
        elif comp_type in ("Input", "Search"):
            self._render_input(img, draw, x, y, w, h, props, fs)
        elif comp_type in ("Toggle", "Checkbox"):
            self._render_toggle(img, draw, x, y, w, h, props, fs)
        elif comp_type in ("Slider",):
            self._render_slider(img, draw, x, y, w, h, props, fs)
        elif comp_type in ("ProgressBar",):
            self._render_progress(img, draw, x, y, w, h, props, fs)
        elif comp_type in ("Icon",):
            draw.text((x + 4, y + 4), props.get("glyph", "?"),
                      fill=self._c("text_primary"), font=fs)
        elif comp_type in ("Container", "Stack", "Grid", "Panel", "Card",
                           "Dock", "ScrollView", "FlexLayout"):
            pass  # Transparent containers
        elif comp_type in ("DesktopSurface",):
            pass  # Background surface
        elif comp_type in ("Window",):
            # Standalone windows not managed by session — render with chrome
            self._render_standalone_window(img, draw, x, y, w, h, props, comp,
                                            font, fs, ft)
        else:
            # Subtle placeholder
            draw.rounded_rectangle(
                [x, y, x + w, y + h], radius=int(APPLE_RADIUS_SM * self.scale),
                fill=self._c("surface_elevated"))
            draw.text((x + int(4 * self.scale), y + int(4 * self.scale)),
                      comp_type, fill=self._c("text_tertiary"), font=fs)

        # Render children (only for container-like types)
        children = getattr(comp, "children", [])
        leaf_types = {"Text", "Label", "Heading", "Button", "Input",
                      "Toggle", "Checkbox", "Slider", "ProgressBar",
                      "Icon", "Image", "DesktopIcon", "Clock", "MenuItem"}
        if children and comp_type not in leaf_types:
            for child in children:
                self._render_node(img, draw, child, font, fs, ft, fl, doc, x, y)

    # ------------------------------------------------------------------
    # Apple-style component renderers
    # ------------------------------------------------------------------

    def _render_standalone_window(self, img, draw, x, y, w, h, props, comp,
                                   font, fs, ft):
        """Render a standalone Window component (not managed by session)."""
        Image, ImageDraw, _, ImageFilter = _pil()
        r = int(APPLE_RADIUS_XL * self.scale)
        titlebar_h = int(38 * self.scale)

        # Shadow
        shadow = _create_shadow(
            Image, ImageFilter,
            w, h, (0, 0, 0), 45, (0, int(6 * self.scale)),
            int(24 * self.scale), r)

        # Body
        body = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        body_draw = ImageDraw.Draw(body)
        body_draw.rounded_rectangle([0, 0, w-1, h-1], radius=r,
                                     fill=(*self._c("surface"), 255))

        # Titlebar
        body_draw.rounded_rectangle(
            [0, 0, w-1, titlebar_h + int(6 * self.scale)],
            radius=r, fill=(*self._c("window_title_bg"), 255))

        # Paste
        img.paste(body.convert("RGB"), (x, y), body.split()[3])

        # Title
        title = props.get("title", comp.id)
        bbox = draw.textbbox((0, 0), title, font=fs)
        tw = bbox[2] - bbox[0]
        draw.text((x + (w - tw)//2, y + int(10 * self.scale)),
                  title, fill=self._c("titlebar_text"), font=fs)

        # Traffic lights
        btn_y = y + int(10 * self.scale)
        btn_r = int(7 * self.scale)
        colors = [self._c("close_btn"), self._c("minimize_btn"),
                  self._c("maximize_btn")]
        for i, color in enumerate(colors):
            bx = x + int(14 * self.scale) + i * (btn_r * 2 + int(8 * self.scale))
            draw.ellipse([bx - btn_r, btn_y, bx + btn_r, btn_y + btn_r * 2],
                          fill=color)

    def _render_taskbar(self, img, draw, x, y, w, h, props, comp, font, fs, ft, doc):
        """Render a taskbar with Apple-quality styling."""
        Image, ImageDraw, _, _ = _pil()
        s = self.scale

        # Vibrancy background
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        o_draw = ImageDraw.Draw(overlay)
        tb_color = self._c("taskbar_bg")
        o_draw.rounded_rectangle([0, 0, w-1, h-1], radius=int(16 * s),
                                  fill=tb_color)
        img.paste(Image.alpha_composite(
            img.crop((x, y, x + w, y + h)).convert("RGBA"),
            overlay).convert("RGB"), (x, y))

        # Hairline top border
        draw.line([(x, y), (x + w, y)], fill=self._c("hairline"), width=1)

        # Start button (circle, like macOS dock)
        btn_r = int(14 * s)
        btn_cx = x + int(28 * s)
        btn_cy = y + h // 2
        draw.ellipse(
            [btn_cx - btn_r, btn_cy - btn_r,
             btn_cx + btn_r, btn_cy + btn_r],
            fill=self._c("accent"))
        draw.text((btn_cx - int(5 * s), btn_cy - int(7 * s)),
                  "N", fill=(255, 255, 255), font=fs)

        # App indicators (dots under active apps, like macOS)
        app_x = x + int(64 * s)
        app_r = int(3 * s)
        windows = props.get("_session_windows", [])
        for i in range(min(len(windows), 8)):
            dot_x = app_x + i * int(20 * s)
            draw.ellipse([dot_x, y + h - int(8 * s),
                          dot_x + app_r * 2, y + h - int(8 * s) + app_r * 2],
                         fill=self._c("accent"))

        # Clock (right-aligned, like macOS)
        import datetime
        now = datetime.datetime.now().strftime("%a %H:%M")
        bbox = draw.textbbox((0, 0), now, font=fs)
        tw = bbox[2] - bbox[0]
        draw.text((x + w - tw - int(16 * s), y + int(8 * s)),
                  now, fill=self._c("text_primary"), font=fs)

        # System icons (right side)
        icons = ["🔊", "📶", "🔋"]
        icon_x = x + w - tw - int(100 * s)
        for i, icon in enumerate(icons):
            draw.text((icon_x + i * int(20 * s), y + int(8 * s)),
                      icon, fill=self._c("text_secondary"), font=fs)

    def _render_start_menu(self, img, draw, x, y, w, h, props, comp, font, fs, ft, doc):
        """Render a Start Menu with Apple-quality styling."""
        Image, ImageDraw, _, _ = _pil()
        s = self.scale

        # Vibrancy panel
        panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        p_draw = ImageDraw.Draw(panel)
        r = int(APPLE_RADIUS_LG * s)
        bg = self._c("start_menu_bg")
        p_draw.rounded_rectangle([0, 0, w-1, h-1], radius=r, fill=bg)

        # Hairline border
        p_draw.rounded_rectangle([0, 0, w-1, h-1], radius=r,
                                  outline=self._c("hairline"))

        img.paste(Image.alpha_composite(
            img.crop((x, y, x + w, y + h)).convert("RGBA"),
            panel).convert("RGB"), (x, y))

        # Title
        draw.text((x + int(20 * s), y + int(20 * s)),
                  "Start Menu", fill=self._c("text_primary"), font=ft)

        # Search bar
        search_y = y + int(60 * s)
        search_r = int(APPLE_RADIUS_SM * s)
        draw.rounded_rectangle(
            [x + int(20 * s), search_y,
             x + w - int(20 * s), search_y + int(32 * s)],
            radius=search_r,
            fill=self._c("input_bg"),
            outline=self._c("input_border"))
        draw.text((x + int(32 * s), search_y + int(8 * s)),
                  "Search apps...", fill=self._c("text_tertiary"), font=fs)

    def _render_button(self, img, draw, x, y, w, h, props, fs):
        """Render a button with Apple-quality styling."""
        s = self.scale
        text = props.get("text", "Button")
        r = int(APPLE_RADIUS_SM * s)

        # Button body (rounded rect)
        draw.rounded_rectangle(
            [x, y, x + w, y + h], radius=r,
            fill=self._c("accent"))

        # Text (centered, white)
        bbox = draw.textbbox((0, 0), text, font=fs)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(
            (x + (w - tw) // 2, y + (h - th) // 2),
            text, fill=(255, 255, 255), font=fs)

    def _render_input(self, img, draw, x, y, w, h, props, fs):
        """Render an input field with Apple-quality styling."""
        s = self.scale
        r = int(APPLE_RADIUS_SM * s)

        # Field
        draw.rounded_rectangle(
            [x, y, x + w, y + h], radius=r,
            fill=self._c("input_bg"),
            outline=self._c("input_border"))

        # Placeholder
        placeholder = props.get("placeholder", "")
        if placeholder:
            draw.text((x + int(10 * s), y + int(6 * s)),
                      placeholder, fill=self._c("text_tertiary"), font=fs)

    def _render_toggle(self, img, draw, x, y, w, h, props, fs):
        """Render a toggle with Apple-quality styling."""
        s = self.scale
        value = props.get("value", False)
        label = props.get("label", props.get("text", ""))

        # Track
        track_w = int(51 * s)
        track_h = int(31 * s)
        track_r = int(track_h // 2)
        color = self._c("toggle_on") if value else self._c("toggle_off")
        draw.rounded_rectangle(
            [x, y, x + track_w, y + track_h],
            radius=track_r, fill=color)

        # Thumb
        thumb_r = int(12 * s)
        if value:
            cx = x + track_w - thumb_r - int(3 * s)
        else:
            cx = x + thumb_r + int(3 * s)
        cy = y + track_h // 2
        draw.ellipse(
            [cx - thumb_r, cy - thumb_r, cx + thumb_r, cy + thumb_r],
            fill=(255, 255, 255))

        # Label
        if label:
            draw.text((x + track_w + int(10 * s), y + int(6 * s)),
                      label, fill=self._c("text_primary"), font=fs)

    def _render_slider(self, img, draw, x, y, w, h, props, fs):
        """Render a slider with Apple-quality styling."""
        s = self.scale
        value = props.get("value", 50)
        min_val = props.get("min", 0)
        max_val = props.get("max", 100)

        track_y = y + h // 2
        track_h = int(4 * s)
        r = track_h // 2

        # Track background
        draw.rounded_rectangle(
            [x, track_y - r, x + w, track_y + r], radius=r,
            fill=self._c("slider_track"))

        # Fill
        fill_w = int(w * (value - min_val) / (max_val - min_val)) if max_val > min_val else 0
        if fill_w > 0:
            draw.rounded_rectangle(
                [x, track_y - r, x + fill_w, track_y + r], radius=r,
                fill=self._c("slider_fill"))

        # Thumb
        thumb_r = int(10 * s)
        thumb_x = x + fill_w
        draw.ellipse(
            [thumb_x - thumb_r, track_y - thumb_r,
             thumb_x + thumb_r, track_y + thumb_r],
            fill=(255, 255, 255))

    def _render_progress(self, img, draw, x, y, w, h, props, fs):
        """Render a progress bar with Apple-quality styling."""
        s = self.scale
        value = props.get("value", 60)
        min_val = props.get("min", 0)
        max_val = props.get("max", 100)
        r = int(h // 2)

        # Track
        draw.rounded_rectangle([x, y, x + w, y + h], radius=r,
                                fill=self._c("progress_bg"))

        # Fill
        fill_w = int(w * (value - min_val) / (max_val - min_val)) if max_val > min_val else 0
        if fill_w > 0:
            draw.rounded_rectangle(
                [x, y, x + fill_w, y + h], radius=r,
                fill=self._c("progress_fill"))

    # ------------------------------------------------------------------
    # Color helper
    # ------------------------------------------------------------------

    def _c(self, name: str) -> Tuple:
        """Get a color from the current palette."""
        return self.colors.get(name, (128, 128, 128))


__all__ = ["AppleCompositor", "APPLE_COLORS_DARK", "APPLE_COLORS_LIGHT"]
