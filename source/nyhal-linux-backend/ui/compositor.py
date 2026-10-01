#!/usr/bin/env python3
"""Compositor — renders NUI documents to images using PIL.

This is the visual output layer of the Nyrqis shell. It takes a loaded
NstudioDocument (or NyrqisShell) and renders the component tree to a
PIL Image, applying themes, layout, and basic component rendering.

This is a floor-level compositor — a reference implementation for
testing and verification. The real Nyrqis compositor would be a
high-performance Rust/C renderer, but this proves the pipeline
works: design → load → render → image.

References:
- NUI-SCHEMA §3: layout system
- NUI-SCHEMA §6: themes and design tokens
- NFS-001 §4: component vocabulary
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

# PIL imported lazily to avoid 5-15s import penalty in containers.
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
    from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont


# ---- Theme definitions (Eclipse / Solar) --------------------------------

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
        "on_accent": (255, 255, 255),
    },
    # Material 3 (Android) — baseline dark scheme, tonal surfaces.
    # The Android look: elevated tonal surfaces (not borders), a large
    # touch-oriented accent, pill geometry (tokens do the rounding).
    # Accent #D0BCFF (M3 baseline dark primary) on surface #141218:
    # contrast ~8:1 (WCAG AAA) — legibility before decoration.
    "Material": {
        "background": (20, 18, 24),          # M3 dark surface
        "surface": (28, 27, 31),             # surface-container-low #1c1b1f
        "surface_elevated": (36, 35, 42),    # surface-container
        "surface_overlay": (39, 37, 45),     # surface-container-high
        "border": (73, 69, 79),              # outline-variant
        "text_primary": (230, 225, 233),     # on-surface #e6e1e9
        "text_secondary": (202, 196, 208),   # on-surface-variant
        "accent": (208, 188, 255),           # primary (M3 baseline)
        "accent_hover": (211, 199, 250),     # primary ~92
        "button_bg": (79, 55, 139),          # primary-container
        "button_text": (210, 193, 255),      # on-primary-container
        "input_bg": (36, 35, 42),
        "input_border": (73, 69, 79),
        "toggle_on": (208, 188, 255),
        "toggle_off": (73, 69, 79),
        "slider_track": (73, 69, 79),
        "slider_fill": (208, 188, 255),
        "progress_bg": (49, 48, 51),
        "progress_fill": (208, 188, 255),
        "on_accent": (56, 30, 114),           # on-primary (M3 dark purple)
    },
    # Cupertino (Apple/iOS-style) — dark system grays, system blue.
    # The Apple look: flat translucent-feeling surfaces, hairline
    # separators, SF-style restrained accent (#0A84FF system blue).
    # on #1C1C1E: contrast ~4.9:1 — HIG-legible at text sizes.
    "Cupertino": {
        "background": (18, 18, 20),          # systemBackground dark
        "surface": (28, 28, 30),             # secondarySystemBackground
        "surface_elevated": (44, 44, 46),    # tertiarySystemBackground
        "surface_overlay": (24, 24, 26),     # material (thinned)
        "border": (58, 58, 60),              # separator (hairline)
        "text_primary": (255, 255, 255),     # label
        "text_secondary": (235, 235, 245),   # ~secondaryLabel (60%)
        "accent": (10, 132, 255),            # systemBlue dark
        "accent_hover": (64, 156, 255),
        "button_bg": (44, 44, 46),           # filled: gray (iOS buttons)
        "button_text": (255, 255, 255),
        "input_bg": (44, 44, 46),
        "input_border": (58, 58, 60),
        "toggle_on": (48, 209, 88),         # systemGreen (iOS switches)
        "toggle_off": (58, 58, 60),
        "slider_track": (58, 58, 60),
        "slider_fill": (255, 255, 255),     # iOS slider fill is white
        "progress_bg": (44, 44, 46),
        "progress_fill": (10, 132, 255),
        "on_accent": (255, 255, 255),
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
        "on_accent": (255, 255, 255),
    },
}


# ---- Design-language tokens (docs/reference/design-language.md) ---------

# The token vocabulary every renderer understands. A document's
# ``designTokens`` section merges over these defaults (depth-1 merge per
# group); a renderer that lacks a capability degrades to the nearest
# supported effect — tokens record intent, renderers stay honest.
DESIGN_TOKENS = {
    "space": {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24},
    "radius": {"sm": 8, "md": 12, "lg": 16, "full": 999,
                # Controls with built-in chrome (progress fills): the
                # historical hard-coded 4 px. Not in the §3 vocabulary
                # list, but documents may retune it like any radius key.
                "control": 4},
    "motion": {},          # timing — the PIL renderer is static
    "surface": {
        # "bar"/"raised" opacity 0–1: the PIL renderer alpha-blends the
        # chrome over the already-painted wallpaper (real translucency).
        # Defaults are 1.0 (opaque) so documents WITHOUT a designTokens
        # section render exactly as before; translucency is opt-in via
        # the document's tokens (the shipped reference shell opts in).
        "bar": {"opacity": 1.0},
        "raised": {"opacity": 1.0},
    },
    "target": {"min": 44, "gap": 8},
    # Chrome style tokens (opt-in via a document's designTokens):
    # ``window.style`` selects the window chrome dialect (``material``
    # = Android flat tonal, ``cupertino`` = Apple hairline),
    # ``start.pill`` shapes the taskbar's start control as a pill.
    # Defaults render the historical chrome exactly as before.
    "window": {},
    "start": {},
    "tiles": {},
    "apps": {},
}


def _merge_tokens(base: Dict[str, Any], override: Any) -> Dict[str, Any]:
    """Depth-1 merge of a designTokens group (``None`` clears to defaults)."""
    merged = dict(base)
    if isinstance(override, dict):
        for key, value in override.items():
            merged[key] = dict(value) if isinstance(value, dict) else value
    return merged


def _alpha_over(base_rgba, top_rgb, alpha):
    """Composite ``top_rgb`` at ``alpha`` over ``base_rgba`` (alpha-over)."""
    a = max(0.0, min(float(alpha), 1.0))
    return tuple(
        int(round(t * (1.0 - a) + c * a))
        for t, c in zip(base_rgba[:3], top_rgb)
    )


class Compositor:
    """Renders a NUI document to a PIL Image.

    Parameters
    ----------
    theme_name : str
        The theme to use ("Eclipse" or "Solar").
    scale : float
        Rendering scale factor (1.0 = native, 2.0 = retina).

    Design-language tokens (docs/reference/design-language.md) come from
    the module-level ``DESIGN_TOKENS``; a document's ``designTokens``
    section (tolerated by the loader) merges over them per render.
    Documents without tokens render exactly as before.
    """

    # Class-level font cache: keyed by (family_path, size) to avoid
    # reloading TrueType files on every render_screen call.
    _font_cache: Dict[Tuple[str, int], Any] = {}

    def __init__(
        self,
        theme_name: str = "Eclipse",
        scale: float = 1.0,
    ) -> None:
        self.theme_name = theme_name
        self.theme = THEMES.get(theme_name, THEMES["Eclipse"])
        # Active token set: module defaults until render_screen merges a
        # document's designTokens (per-render, keeps the compositor
        # stateless between documents).
        self.tokens = DESIGN_TOKENS
        self.scale = scale

    @classmethod
    def _get_font(cls, path: str, size: int):
        """Return a cached font, loading from disk on first use."""
        key = (path, size)
        if key not in cls._font_cache:
            _, _, ImageFont = _pil()
            try:
                cls._font_cache[key] = ImageFont.truetype(path, size)
            except (OSError, IOError):
                cls._font_cache[key] = ImageFont.load_default()
        return cls._font_cache[key]

    def render_screen(
        self,
        document: Any,
        screen_id: Optional[str] = None,
    ) -> Image.Image:
        """Render a screen from a NstudioDocument to a PIL Image.

        Returns
        -------
        PIL.Image.Image
            The rendered screen as an RGB image.
        """
        # Find the screen
        screen = None
        for s in document.screens:
            if screen_id is None or s.id == screen_id:
                screen = s
                break
        if screen is None:
            raise ValueError(f"Screen '{screen_id}' not found")

        # Merge the document's designTokens over the module defaults
        # (depth-1 per group; absent/None groups keep the defaults).
        doc_tokens = getattr(document, "design_tokens", None)
        self.tokens = DESIGN_TOKENS
        if isinstance(doc_tokens, dict) and doc_tokens:
            for group in ("space", "radius", "motion", "surface",
                          "target", "window", "start", "tiles", "apps"):
                if group in doc_tokens:
                    self.tokens = dict(self.tokens)
                    self.tokens[group] = _merge_tokens(
                        DESIGN_TOKENS.get(group, {}), doc_tokens[group])

        Image, ImageDraw, ImageFont = _pil()
        # Create the image
        w = int(screen.size.get("width", 1440) * self.scale)
        h = int(screen.size.get("height", 900) * self.scale)
        img = Image.new("RGB", (w, h), self.theme["background"])
        draw = ImageDraw.Draw(img)

        # Load fonts (cached at class level to avoid reloading TrueType
        # files on every render_screen call — saves ~5ms per call).
        _sans = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        _sans_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font = self._get_font(_sans, int(14 * self.scale))
        font_small = self._get_font(_sans, int(11 * self.scale))
        font_title = self._get_font(_sans_bold, int(16 * self.scale))

        # Render the component tree
        self._render_component(img, draw, screen.root, font, font_small, font_title,
                               document=document)

        return img

    def _render_component(
        self,
        img: Image.Image,
        draw: ImageDraw.ImageDraw,
        comp: Any,
        font: ImageFont.FreeTypeFont,
        font_small: ImageFont.FreeTypeFont,
        font_title: ImageFont.FreeTypeFont,
        document: Any = None,
    ) -> None:
        """Render a single component and its children."""
        layout = getattr(comp, "layout", {})
        x = int(layout.get("x", 0) * self.scale)
        y = int(layout.get("y", 0) * self.scale)
        w = int(layout.get("width", 100) * self.scale)
        h = int(layout.get("height", 30) * self.scale)

        props = getattr(comp, "properties", {})
        comp_type = getattr(comp, "type", "Unknown")
        comp_id = getattr(comp, "id", "")

        # Render based on component type
        if comp_type == "Window":
            self._render_window(img, draw, x, y, w, h, props, comp, font, font_small, font_title, document)
        elif comp_type in ("DesktopSurface",):
            self._render_desktop_surface(img, draw, x, y, w, h, props, comp, font, font_small, font_title, document)
        elif comp_type in ("Taskbar",):
            self._render_taskbar(img, draw, x, y, w, h, props, comp, font, font_small, font_title, document)
        elif comp_type in ("StartMenu",):
            self._render_start_menu(img, draw, x, y, w, h, props, comp, font, font_small, font_title, document)
        elif comp_type in ("Button", "Link"):
            self._render_button(img, draw, x, y, w, h, props, font, font_small)
        elif comp_type in ("Text", "Label", "Heading", "Paragraph"):
            self._render_text(img, draw, x, y, w, h, props, comp, font, font_small, font_title)
        elif comp_type in ("Input", "PasswordField", "Search"):
            self._render_input(img, draw, x, y, w, h, props, font, font_small)
        elif comp_type in ("Toggle", "Checkbox", "Radio"):
            self._render_toggle(img, draw, x, y, w, h, props, font, font_small)
        elif comp_type in ("Slider",):
            self._render_slider(img, draw, x, y, w, h, props, font, font_small)
        elif comp_type in ("ProgressBar",):
            self._render_progress(img, draw, x, y, w, h, props, font, font_small)
        elif comp_type in ("Image",):
            self._render_image_placeholder(img, draw, x, y, w, h, font_small)
        elif comp_type in ("Icon",):
            self._render_icon(img, draw, x, y, w, h, props, font_small)
        elif comp_type in ("Container", "Stack", "Grid", "Panel", "Card",
                           "WindowFrame", "Dock", "SplitView", "ScrollView",
                           "Tabs", "FlexLayout"):
            self._render_container(img, draw, x, y, w, h, props, comp, font, font_small, font_title, document)
        elif comp_type in ("DesktopIcon",):
            self._render_desktop_icon(img, draw, x, y, w, h, props, font_small)
        elif comp_type in ("Clock",):
            self._render_clock(img, draw, x, y, w, h, props, font_small)
        elif comp_type in ("SystemTray",):
            self._render_system_tray(img, draw, x, y, w, h, props, font_small)
        elif comp_type in ("NotificationCenter",):
            self._render_notification_center(img, draw, x, y, w, h, props, font, font_small)
        elif comp_type in ("QuickSettings",):
            self._render_quick_settings(img, draw, x, y, w, h, props, comp, font, font_small, document)
        elif comp_type in ("WorkspaceSwitcher",):
            self._render_workspace_switcher(img, draw, x, y, w, h, props, font_small)
        elif comp_type in ("CommandPalette",):
            self._render_command_palette(img, draw, x, y, w, h, props, comp, font, font_small, document)
        elif comp_type in ("Launcher",):
            self._render_launcher(img, draw, x, y, w, h, props, comp, font, font_small, document)
        elif comp_type in ("LockScreen",):
            self._render_lock_screen(img, draw, x, y, w, h, props, font, font_small, font_title)
        elif comp_type in ("ContextMenu",):
            self._render_context_menu(img, draw, x, y, w, h, props, comp, font, font_small, document)
        elif comp_type in ("MenuItem",):
            self._render_menu_item(img, draw, x, y, w, h, props, font_small)
        elif comp_type == "AppGrid":
            self._render_app_grid(img, draw, x, y, w, h, props, comp, font, font_small)
        elif comp_type in ("List",):
            self._render_list(img, draw, x, y, w, h, props, comp, font, font_small, document)
        elif comp_type in ("TreeView",):
            self._render_tree_view(img, draw, x, y, w, h, props, font_small)
        elif comp_type in ("TitleBar",):
            self._render_title_bar(img, draw, x, y, w, h, props, font_small)
        elif comp_type in ("WindowControls",):
            self._render_window_controls(img, draw, x, y, w, h, font_small)
        else:
            # Generic placeholder
            self._render_placeholder(img, draw, x, y, w, h, comp_type, font_small)

        # Render children (for container types)
        children = getattr(comp, "children", [])
        if children and comp_type not in ("Text", "Label", "Heading", "Paragraph",
                                           "Button", "Link", "Input", "PasswordField",
                                           "Search", "Toggle", "Checkbox", "Radio",
                                           "Slider", "ProgressBar", "Image", "Icon",
                                           "DesktopIcon", "Clock", "MenuItem"):
            for child in children:
                self._render_component(img, draw, child, font, font_small, font_title, document)

    # ---- Component renderers -----------------------------------------------

    def _render_window(self, img, draw, x, y, w, h, props, comp, font, fs, ft, doc):
        """Render a Window component with chrome.

        Chrome style is token-driven (``window.style``): ``material``
        draws the Android look — borderless flat surface, no grip dots,
        pill window controls; ``cupertino`` draws the Apple look —
        hairline title separator, hidden grips; the historical default
        (no token) renders exactly as before."""
        title_h = 32
        wstyle = (self.tokens.get("window", {}) or {}).get("style", "")
        material = wstyle == "material"
        cupertino = wstyle == "cupertino"
        if not material:
            # Shadow (subtle drop shadow) — Android windows float shadow-
            # less (their elevation reads through flat tonal surfaces).
            for i in range(4):
                alpha_color = tuple(
                    int(c * 0.7) for c in self.theme["border"])
                draw.rectangle(
                    [x+i+2, y+i+2, x+w+i+2, y+h+i+2],
                    outline=alpha_color)
        if material:
            draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface"])
            # Title bar: slightly more elevated tonal band, no border line.
            draw.rectangle([x, y, x+w, y+title_h],
                           fill=self.theme["surface_elevated"])
        else:
            draw.rectangle([x, y, x+w, y+h], fill=self.theme["background"],
                           outline=self.theme["border"])
            # Title bar
            draw.rectangle([x, y, x+w, y+title_h],
                           fill=self.theme["surface_overlay"])
            if cupertino:
                # HIG separator: one hairline under the title, no box.
                draw.line([x, y+title_h, x+w, y+title_h],
                          fill=self.theme["border"], width=1)
        title = props.get("title", "Window")
        draw.text((x+12, y+8), title,
                  fill=self.theme["text_primary"], font=fs)
        # Window control buttons (close, minimize, maximize)
        btn_y = y + 6
        btn_size = 20
        btn_gap = 6
        controls = [
            ("×", self.theme["text_secondary"]),
            ("−", self.theme["text_secondary"]),
            ("□", self.theme["text_secondary"]),
        ]
        ctrl_radius = 10 if (material or cupertino) else 4
        for i, (glyph, color) in enumerate(controls):
            bx = x + w - 12 - (i + 1) * (btn_size + btn_gap)
            draw.rounded_rectangle(
                [bx, btn_y, bx+btn_size, btn_y+btn_size],
                radius=ctrl_radius, fill=self.theme["surface_elevated"])
            draw.text((bx+5, btn_y+2), glyph, fill=color, font=fs)
        if not (material or cupertino):
            # Resize grip indicators (subtle dots on edges)
            grip = self.theme["border"]
            # Right edge center
            draw.rectangle([x+w-3, y+h//2-8, x+w-1, y+h//2+8], fill=grip)
            # Bottom edge center
            draw.rectangle([x+w//2-8, y+h-3, x+w//2+8, y+h-1], fill=grip)
            # Bottom-right corner
            for i in range(3):
                draw.rectangle(
                    [x+w-4-i*3, y+h-4, x+w-2-i*3, y+h-2], fill=grip)

    def _render_desktop_surface(self, img, draw, x, y, w, h, props, comp, font, fs, ft, doc):
        """Render a DesktopSurface."""
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface"])

    def _render_taskbar(self, img, draw, x, y, w, h, props, comp, font, fs, ft, doc):
        """Render a Taskbar with app buttons, clock, and system tray.

        Design language: ``surface.bar`` opacity blends the chrome over
        the wallpaper (real alpha-over, opt-in via designTokens); the
        start control is sized to the ``target.min`` hit-height token."""
        bar_rgb = self.theme["surface_overlay"]
        opacity = (self.tokens.get("surface", {}).get("bar", {}) or {}).get(
            "opacity", 1.0)
        if opacity < 1.0 and y + h <= img.height and x + w <= img.width:
            Image, ImageDraw, _ = _pil()
            region = img.crop((x, y, x + w, y + h)).convert("RGBA")
            blended = Image.new("RGB", (w, h))
            px = region.load()
            bl = blended.load()
            for j in range(h):
                for i in range(w):
                    bl[i, j] = _alpha_over(px[i, j], bar_rgb, opacity)
            img.paste(blended, (x, y))
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([x, y, x+w, y+h], fill=bar_rgb)
        draw.line([x, y, x+w, y], fill=self.theme["border"], width=1)
        # Start button area — hit height respects target.min where the
        # bar can afford it (small bars degrade honestly).
        target_min = int(self.tokens.get("target", {}).get("min", 44))
        pad = max(4, min((h - target_min) // 2, 12)) if h >= target_min else 4
        # Start control: pill-shaped under the token ``start.pill`` (the
        # Material/Apple shells set it), historical rounded-square else.
        start_style = (self.tokens.get("start", {}) or {}).get("pill", False)
        start_radius = (y + h - pad - (y + pad)) // 2 if start_style else 6
        draw.rounded_rectangle(
            [x+4, y+pad, x+48, y+h-pad], radius=start_radius,
            fill=self.theme["accent"])
        draw.text((x+16, y+8), "N",
                  fill=self.theme.get("on_accent", (255, 255, 255)), font=ft)
        # Running app indicators (dots)
        app_x = x + 60
        if doc:
            windows = props.get("_session_windows", [])
            for i, wtitle in enumerate(windows[:8]):
                # Small pill for each running app
                draw.rounded_rectangle(
                    [app_x + i*28, y+6, app_x + i*28 + 24, y+h-6],
                    radius=4, fill=self.theme["button_bg"])
                draw.text((app_x + i*28 + 6, y+8),
                          wtitle[:2].upper(),
                          fill=self.theme["text_primary"], font=fs)
        # System tray (right side)
        tray_x = x + w - 120
        draw.text((tray_x, y+8), "🔊  🔋  📶",
                  fill=self.theme["text_secondary"], font=fs)
        # Clock
        import datetime
        now = datetime.datetime.now().strftime("%H:%M")
        draw.text((x + w - 48, y+8), now,
                  fill=self.theme["text_primary"], font=fs)

    def _render_start_menu(self, img, draw, x, y, w, h, props, comp, font, fs, ft, doc):
        """Render a StartMenu.

        Design language: ``surface.raised`` opacity (opt-in blend, same
        mechanism as the taskbar) and ``radius.lg`` corners."""
        menu_rgb = self.theme["surface_elevated"]
        opacity = (self.tokens.get("surface", {}).get("raised", {}) or {}).get(
            "opacity", 1.0)
        radius = int(self.tokens.get("radius", {}).get("lg", 16))
        if opacity < 1.0 and y + h <= img.height and x + w <= img.width:
            Image, ImageDraw, _ = _pil()
            region = img.crop((x, y, x + w, y + h)).convert("RGBA")
            blended = Image.new("RGB", (w, h))
            px = region.load()
            bl = blended.load()
            for j in range(h):
                for i in range(w):
                    bl[i, j] = _alpha_over(px[i, j], menu_rgb, opacity)
            # Rounded mask so the blend doesn't paint sharp corners.
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, w - 1, h - 1], radius=radius, fill=255)
            img.paste(blended, (x, y), mask)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle([x, y, x+w, y+h], radius=radius,
                                   outline=self.theme["border"], width=1)
        else:
            draw.rounded_rectangle([x, y, x+w, y+h], radius=radius,
                                   fill=menu_rgb,
                                   outline=self.theme["border"], width=1)
        # Header
        draw.text((x+16, y+16), "Start Menu", fill=self.theme["text_primary"], font=ft)

    def _render_button(self, img, draw, x, y, w, h, props, font, fs):
        """Render a Button (``radius.sm`` from the design tokens, or the
        per-instance ``cornerRadius`` override; 0 = token default).
        Registry 1.1: ``cornerRadius`` is an optional Button property
        (nui-api-v1.json versionHistory 1.1)."""
        text = props.get("text", "Button")
        radius = int(self.tokens.get("radius", {}).get("sm", 8))
        override = props.get("cornerRadius")
        if override:
            try:
                req = int(override)
            except (TypeError, ValueError):
                req = 0
            if req > 0:  # only positive overrides; 0/negative/junk = default
                radius = min(req, 64, min(w, h) // 2)
        draw.rounded_rectangle([x, y, x+w, y+h], radius=radius,
                               fill=self.theme["button_bg"])
        bbox = draw.textbbox((0, 0), text, font=fs)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = x + (w - tw) // 2
        ty = y + (h - th) // 2
        draw.text((tx, ty), text, fill=self.theme["button_text"], font=fs)

    def _render_text(self, img, draw, x, y, w, h, props, comp, font, fs, ft):
        """Render a Text component."""
        text = props.get("text", comp.id)
        f = ft if getattr(comp, "type", "") == "Heading" else font
        draw.text((x, y), text, fill=self.theme["text_primary"], font=f)

    def _render_input(self, img, draw, x, y, w, h, props, font, fs):
        """Render an Input (``radius.sm`` from the design tokens)."""
        radius = int(self.tokens.get("radius", {}).get("sm", 8))
        draw.rounded_rectangle([x, y, x+w, y+h], radius=radius,
                               fill=self.theme["input_bg"],
                               outline=self.theme["input_border"])
        placeholder = props.get("placeholder", "")
        if placeholder:
            draw.text((x+8, y+4), placeholder, fill=self.theme["text_secondary"], font=fs)

    def _render_toggle(self, img, draw, x, y, w, h, props, font, fs):
        """Render a Toggle/Checkbox."""
        value = props.get("value", False)
        label = props.get("label", props.get("text", ""))
        color = self.theme["toggle_on"] if value else self.theme["toggle_off"]
        draw.rounded_rectangle([x, y, x+40, y+20], radius=10, fill=color)
        cx = x + 30 if value else x + 10
        draw.ellipse([cx-7, y+3, cx+7, y+17], fill=(255, 255, 255))
        if label:
            draw.text((x+48, y+2), label, fill=self.theme["text_primary"], font=fs)

    def _render_slider(self, img, draw, x, y, w, h, props, font, fs):
        """Render a Slider (``radius.sm`` track — clamped to the 5 px
        track height it renders identically to the historical r=2)."""
        value = props.get("value", 50)
        min_val = props.get("min", 0)
        max_val = props.get("max", 100)
        track_y = y + h // 2
        radius = int(self.tokens.get("radius", {}).get("sm", 8))
        draw.rounded_rectangle([x, track_y-2, x+w, track_y+2], radius=radius,
                               fill=self.theme["slider_track"])
        fill_w = int(w * (value - min_val) / (max_val - min_val)) if max_val > min_val else 0
        draw.rounded_rectangle([x, track_y-2, x+fill_w, track_y+2], radius=radius,
                               fill=self.theme["slider_fill"])

    def _render_progress(self, img, draw, x, y, w, h, props, font, fs):
        """Render a ProgressBar (``radius.control``, default 4 px = the
        historical hard-coded value)."""
        value = props.get("value", 60)
        min_val = props.get("min", 0)
        max_val = props.get("max", 100)
        radius = int(self.tokens.get("radius", {}).get("control", 4))
        draw.rounded_rectangle([x, y, x+w, y+h], radius=radius,
                               fill=self.theme["progress_bg"])
        fill_w = int(w * (value - min_val) / (max_val - min_val)) if max_val > min_val else 0
        if fill_w > 0:
            draw.rounded_rectangle([x, y, x+fill_w, y+h], radius=radius,
                                   fill=self.theme["progress_fill"])

    def _render_image_placeholder(self, img, draw, x, y, w, h, fs):
        """Render an Image placeholder."""
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["border"])
        draw.text((x+w//2-20, y+h//2-6), "[Image]", fill=self.theme["text_secondary"], font=fs)

    def _render_icon(self, img, draw, x, y, w, h, props, fs):
        """Render an Icon."""
        glyph = props.get("glyph", "?")
        draw.text((x+4, y+4), glyph, fill=self.theme["text_primary"], font=fs)

    def _render_container(self, img, draw, x, y, w, h, props, comp, font, fs, ft, doc):
        """Render a generic container (no visual, just layout)."""
        pass  # Containers are transparent — children render on top

    def _render_desktop_icon(self, img, draw, x, y, w, h, props, fs):
        """Render a DesktopIcon."""
        glyph = props.get("glyph", "?")
        label = props.get("label", "")
        # Icon square
        icon_size = min(w, h - 20)
        ix = x + (w - icon_size) // 2
        draw.rounded_rectangle([ix, y, ix+icon_size, y+icon_size], radius=8,
                               fill=self.theme["surface_elevated"])
        draw.text((ix + icon_size//2 - 6, y + icon_size//2 - 8), glyph,
                  fill=self.theme["text_primary"], font=fs)
        # Label
        if label:
            bbox = draw.textbbox((0, 0), label, font=fs)
            tw = bbox[2] - bbox[0]
            draw.text((x + (w - tw)//2, y + icon_size + 4), label,
                      fill=self.theme["text_primary"], font=fs)

    def _render_clock(self, img, draw, x, y, w, h, props, fs):
        """Render a Clock."""
        time_str = props.get("time", "12:00")
        draw.text((x+4, y+4), time_str, fill=self.theme["text_primary"], font=fs)

    def _render_system_tray(self, img, draw, x, y, w, h, props, fs):
        """Render a SystemTray."""
        icons = props.get("icons", [])
        for i, icon in enumerate(icons[:3]):
            draw.text((x + i*24, y+4), icon[:1], fill=self.theme["text_secondary"], font=fs)

    def _render_notification_center(self, img, draw, x, y, w, h, props, font, fs):
        """Render a NotificationCenter."""
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface_elevated"])
        draw.rectangle([x, y, x+w, y+h], outline=self.theme["border"], width=1)
        draw.text((x+16, y+12), "Notifications", fill=self.theme["text_primary"], font=font)

    def _render_quick_settings(self, img, draw, x, y, w, h, props, comp, font, fs, doc):
        """Render a QuickSettings panel.

        Android-surface style under the token ``tiles.grid``: the
        panel's ``toggles`` list becomes Material's tile grid — rounded
        accent tiles for ON states, tonal surface tiles for OFF, laid
        out two-per-row on the ``target.min`` height (HIG touch target).
        The historical panel renders exactly as before without it."""
        tiles_grid = (self.tokens.get("tiles", {}) or {}).get("grid", False)
        surface = self.theme["surface_elevated"]
        radius = int(self.tokens.get("radius", {}).get("md", 12))
        if tiles_grid:
            draw.rounded_rectangle([x, y, x+w, y+h], radius=radius,
                                   fill=surface)
        else:
            draw.rectangle([x, y, x+w, y+h], fill=surface)
            draw.rectangle([x, y, x+w, y+h], outline=self.theme["border"], width=1)
        draw.text((x+16, y+12), "Quick Settings",
                  fill=self.theme["text_primary"], font=font)
        if not tiles_grid:
            return
        toggles = props.get("toggles", [])
        if isinstance(toggles, dict):
            toggles = [{"label": k, "value": v}
                       for k, v in toggles.items()]
        # Bare-string toggles (the shell document's data): index 0 ON —
        # Android's stock Wi-Fi-on state — the rest OFF.
        toggles = [
            {"label": t, "value": i == 0} if isinstance(t, str) else t
            for i, t in enumerate(toggles)]
        target_min = int(self.tokens.get("target", {}).get("min", 44))
        gap = int(self.tokens.get("target", {}).get("gap", 8))
        tile_w = (w - 32 - gap) // 2
        row_y = y + 40
        for i, t in enumerate(toggles[:8]):
            label = str(t.get("label", t) if isinstance(t, dict) else t)
            on = bool(t.get("value", False)) if isinstance(t, dict) else False
            col, row = i % 2, i // 2
            tx = x + 16 + col * (tile_w + gap)
            ty = row_y + row * (target_min + gap)
            if ty + target_min > y + h:
                break
            fill = self.theme["accent"] if on else \
                self.theme["surface_overlay"]
            fg = self.theme.get("on_accent", (255, 255, 255)) if on \
                else self.theme["text_primary"]
            draw.rounded_rectangle(
                [tx, ty, tx + tile_w, ty + target_min],
                radius=max(8, target_min // 2), fill=fill)
            draw.text((tx + 12, ty + target_min // 2 - 7), label,
                      fill=fg, font=fs)

    def _render_workspace_switcher(self, img, draw, x, y, w, h, props, fs):
        """Render a WorkspaceSwitcher."""
        current = props.get("currentWorkspace", 1)
        total = props.get("workspaces", 3)
        for i in range(1, total + 1):
            cx = x + (i-1) * 20 + 4
            color = self.theme["accent"] if i == current else self.theme["toggle_off"]
            draw.ellipse([cx, y+4, cx+12, y+16], fill=color)

    def _render_command_palette(self, img, draw, x, y, w, h, props, comp, font, fs, doc):
        """Render a CommandPalette."""
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface_elevated"])
        draw.rectangle([x, y, x+w, y+h], outline=self.theme["border"], width=1)
        draw.text((x+16, y+12), "Command Palette", fill=self.theme["text_primary"], font=font)

    def _render_launcher(self, img, draw, x, y, w, h, props, comp, font, fs, doc):
        """Render a Launcher.

        Android-surface style under the token ``apps.grid``: the
        launcher's ``AppGrid`` data renders as a home-screen icon grid
        — squircle-ish rounded tiles (``radius.lg``), one accent glyph
        chip + label per app, flowing left-to-right on the ``target
        .min`` cell. Historical panel without the token, as before."""
        apps_grid = (self.tokens.get("apps", {}) or {}).get("grid", False)
        surface = self.theme["surface_elevated"]
        radius = int(self.tokens.get("radius", {}).get("lg", 16))
        if apps_grid:
            draw.rounded_rectangle([x, y, x+w, y+h], radius=radius,
                                   fill=surface)
        else:
            draw.rectangle([x, y, x+w, y+h], fill=surface)
            draw.rectangle([x, y, x+w, y+h], outline=self.theme["border"], width=1)
        draw.text((x+16, y+12), "Launcher",
                  fill=self.theme["text_primary"], font=font)
    def _render_lock_screen(self, img, draw, x, y, w, h, props, font, fs, ft):
        """Render a LockScreen."""
        draw.rectangle([x, y, x+w, y+h], fill=(20, 20, 40))
        # Clock
        time_str = props.get("clockTime", "12:00")
        bbox = draw.textbbox((0, 0), time_str, font=ft)
        tw = bbox[2] - bbox[0]
        draw.text((x + (w - tw)//2, y + h//2 - 40), time_str,
                  fill=(255, 255, 255), font=ft)

    def _render_context_menu(self, img, draw, x, y, w, h, props, comp, font, fs, doc):
        """Render a ContextMenu."""
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface_elevated"])
        draw.rectangle([x, y, x+w, y+h], outline=self.theme["border"], width=1)

    def _render_menu_item(self, img, draw, x, y, w, h, props, fs):
        """Render a MenuItem (selection highlight from ``space`` tokens)."""
        label = props.get("label", props.get("text", "Item"))
        selected = props.get("selected", False)
        if selected:
            # Full-bleed accent row, radius.sm corner: the label KEEPS its
            # position (x+8) so selection never shifts text; contrast text
            # comes from the surface the menu sits on.
            radius = int(self.tokens.get("radius", {}).get("sm", 8))
            draw.rounded_rectangle(
                [x, y, x + w - 1, y + h - 1], radius=radius,
                fill=self.theme["accent"])
            draw.text((x + 8, y + 6), label,
                      fill=self.theme["surface_elevated"], font=fs)
        else:
            draw.text((x + 8, y + 6), label,
                      fill=self.theme["text_primary"], font=fs)

    def _render_app_grid(self, img, draw, x, y, w, h, props, comp, font, fs):
        """Render an AppGrid.

        Android-surface style under the token ``apps.grid``: a
        home-screen icon grid — accent glyph chips + labels flowing on
        ``target.min`` cells. Without the token the historical
        list-row rendering applies (one implementation, two skins)."""
        apps = props.get("apps", [])
        if isinstance(apps, dict):
            apps = list(apps.keys())
        if not (self.tokens.get("apps", {}).get("grid", False)) or not apps:
            # Historical skin: reuse the List renderer verbatim.
            self._render_list(img, draw, x, y, w, h, props, comp, font, fs, None)
            return
        cols = max(1, int(props.get("columns", 4) or 4))
        target_min = int(self.tokens.get("target", {}).get("min", 44))
        gap = int(self.tokens.get("target", {}).get("gap", 8))
        cell_w = (w - (cols - 1) * gap) // cols
        cell_h = target_min + 20
        for i, app in enumerate(apps):
            label = str(app)
            col, row = i % cols, i // cols
            cx = x + col * (cell_w + gap)
            cy = y + row * (cell_h + gap)
            if cy + cell_h > y + h or cx + cell_w > x + w:
                break
            chip = min(40, cell_h - 20)
            draw.rounded_rectangle(
                [cx + (cell_w - chip) // 2, cy,
                 cx + (cell_w - chip) // 2 + chip, cy + chip],
                radius=max(10, chip // 3), fill=self.theme["accent"])
            draw.text(
                (cx + (cell_w - chip) // 2 + chip // 2 - 5,
                 cy + chip // 2 - 7),
                label[:1].upper(),
                fill=self.theme.get("on_accent", (255, 255, 255)), font=fs)
            bbox = draw.textbbox((0, 0), label, font=fs)
            tw = bbox[2] - bbox[0]
            draw.text((cx + max(0, (cell_w - tw) // 2), cy + chip + 2),
                      label, fill=self.theme["text_primary"], font=fs)

    def _render_list(self, img, draw, x, y, w, h, props, comp, font, fs, doc):
        """Render a List (row pitch + selection from ``space`` tokens).

        Row pitch defaults to ``space.xl`` (24 px) — the historical
        constant — so token-less documents render pixel-identically.
        Documents may retune it via ``designTokens.space.xl``; the
        ``selectedIndex`` highlight is accent-on-interactive with a
        ``radius.sm`` corner per the design-language §7 checklist.
        """
        items = props.get("items", [])
        pitch = int(self.tokens.get("space", {}).get("xl", 24))
        radius = int(self.tokens.get("radius", {}).get("sm", 8))
        selected = props.get("selectedIndex", -1)
        for i, item in enumerate(items[:10]):
            iy = y + i * pitch
            if i == selected:
                # Full-bleed accent row (radius.sm), 1 px separation from
                # the next row; the item text KEEPS its exact position.
                draw.rounded_rectangle(
                    [x, iy, x + w - 1, iy + pitch - 1],
                    radius=radius, fill=self.theme["accent"])
                draw.text((x + 8, iy + 4), str(item),
                          fill=self.theme["surface_elevated"], font=fs)
            else:
                draw.text((x + 8, iy + 4), str(item),
                          fill=self.theme["text_primary"], font=fs)

    def _render_tree_view(self, img, draw, x, y, w, h, props, fs):
        """Render a TreeView."""
        draw.text((x+8, y+4), "(tree)", fill=self.theme["text_secondary"], font=fs)

    def _render_title_bar(self, img, draw, x, y, w, h, props, fs):
        """Render a TitleBar."""
        title = props.get("title", "Window")
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface_overlay"])
        draw.text((x+8, y+8), title, fill=self.theme["text_primary"], font=fs)

    def _render_window_controls(self, img, draw, x, y, w, h, fs):
        """Render WindowControls (minimize/maximize/close)."""
        controls = ["—", "□", "×"]
        for i, c in enumerate(controls):
            cx = x + i * 24
            draw.text((cx+4, y+4), c, fill=self.theme["text_secondary"], font=fs)

    def _render_placeholder(self, img, draw, x, y, w, h, comp_type, fs):
        """Render a generic placeholder for unknown component types."""
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface_elevated"],
                       outline=self.theme["border"])
        draw.text((x+4, y+4), comp_type, fill=self.theme["text_secondary"], font=fs)
