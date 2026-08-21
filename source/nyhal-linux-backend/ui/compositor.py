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

import os
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise ImportError("PIL/Pillow is required: pip install Pillow")


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


class Compositor:
    """Renders a NUI document to a PIL Image.

    Parameters
    ----------
    theme_name : str
        The theme to use ("Eclipse" or "Solar").
    scale : float
        Rendering scale factor (1.0 = native, 2.0 = retina).
    """

    # Class-level font cache: keyed by (family_path, size) to avoid
    # reloading TrueType files on every render_screen call.
    _font_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}

    def __init__(
        self,
        theme_name: str = "Eclipse",
        scale: float = 1.0,
    ) -> None:
        self.theme_name = theme_name
        self.theme = THEMES.get(theme_name, THEMES["Eclipse"])
        self.scale = scale

    @classmethod
    def _get_font(cls, path: str, size: int) -> ImageFont.FreeTypeFont:
        """Return a cached font, loading from disk on first use."""
        key = (path, size)
        if key not in cls._font_cache:
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
        elif comp_type in ("List", "AppGrid"):
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
        """Render a Window component with chrome."""
        title_h = 32
        # Shadow (subtle drop shadow)
        for i in range(4):
            alpha_color = tuple(
                int(c * 0.7) for c in self.theme["border"])
            draw.rectangle(
                [x+i+2, y+i+2, x+w+i+2, y+h+i+2],
                outline=alpha_color)
        # Window body
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["background"],
                       outline=self.theme["border"])
        # Title bar
        draw.rectangle([x, y, x+w, y+title_h],
                       fill=self.theme["surface_overlay"])
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
        for i, (glyph, color) in enumerate(controls):
            bx = x + w - 12 - (i + 1) * (btn_size + btn_gap)
            draw.rounded_rectangle(
                [bx, btn_y, bx+btn_size, btn_y+btn_size],
                radius=4, fill=self.theme["surface_elevated"])
            draw.text((bx+5, btn_y+2), glyph, fill=color, font=fs)
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
        """Render a Taskbar with app buttons, clock, and system tray."""
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface_overlay"])
        draw.line([x, y, x+w, y], fill=self.theme["border"], width=1)
        # Start button area
        draw.rounded_rectangle(
            [x+4, y+4, x+48, y+h-4], radius=6,
            fill=self.theme["accent"])
        draw.text((x+16, y+8), "N", fill=(255,255,255), font=ft)
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
        """Render a StartMenu."""
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface_elevated"])
        draw.rectangle([x, y, x+w, y+h], outline=self.theme["border"], width=1)
        # Header
        draw.text((x+16, y+16), "Start Menu", fill=self.theme["text_primary"], font=ft)

    def _render_button(self, img, draw, x, y, w, h, props, font, fs):
        """Render a Button."""
        text = props.get("text", "Button")
        draw.rounded_rectangle([x, y, x+w, y+h], radius=4,
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
        """Render an Input."""
        draw.rounded_rectangle([x, y, x+w, y+h], radius=4,
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
        """Render a Slider."""
        value = props.get("value", 50)
        min_val = props.get("min", 0)
        max_val = props.get("max", 100)
        track_y = y + h // 2
        draw.rounded_rectangle([x, track_y-2, x+w, track_y+2], radius=2,
                               fill=self.theme["slider_track"])
        fill_w = int(w * (value - min_val) / (max_val - min_val)) if max_val > min_val else 0
        draw.rounded_rectangle([x, track_y-2, x+fill_w, track_y+2], radius=2,
                               fill=self.theme["slider_fill"])

    def _render_progress(self, img, draw, x, y, w, h, props, font, fs):
        """Render a ProgressBar."""
        value = props.get("value", 60)
        min_val = props.get("min", 0)
        max_val = props.get("max", 100)
        draw.rounded_rectangle([x, y, x+w, y+h], radius=4,
                               fill=self.theme["progress_bg"])
        fill_w = int(w * (value - min_val) / (max_val - min_val)) if max_val > min_val else 0
        if fill_w > 0:
            draw.rounded_rectangle([x, y, x+fill_w, y+h], radius=4,
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
        """Render a QuickSettings panel."""
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface_elevated"])
        draw.rectangle([x, y, x+w, y+h], outline=self.theme["border"], width=1)
        draw.text((x+16, y+12), "Quick Settings", fill=self.theme["text_primary"], font=font)

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
        """Render a Launcher."""
        draw.rectangle([x, y, x+w, y+h], fill=self.theme["surface_elevated"])
        draw.rectangle([x, y, x+w, y+h], outline=self.theme["border"], width=1)
        draw.text((x+16, y+12), "Launcher", fill=self.theme["text_primary"], font=font)

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
        """Render a MenuItem."""
        label = props.get("label", props.get("text", "Item"))
        draw.text((x+8, y+6), label, fill=self.theme["text_primary"], font=fs)

    def _render_list(self, img, draw, x, y, w, h, props, comp, font, fs, doc):
        """Render a List."""
        items = props.get("items", [])
        for i, item in enumerate(items[:10]):
            iy = y + i * 24
            draw.text((x+8, iy+4), str(item), fill=self.theme["text_primary"], font=fs)

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
