"""
Nyrqis OS — Desktop Preview Renderer

Renders the live desktop state to PNG images and animated GIFs.
Captures the full pipeline: backend → compositor → shell → desktop.

Usage:
    from ui.desktop_preview import DesktopPreview

    preview = DesktopPreview(1920, 1080)
    preview.start()
    preview.capture("desktop.png")
    preview.start_recording("desktop.gif", fps=10)
    # ... let the desktop run ...
    preview.stop_recording()
    preview.stop()

    # Or render a specific state
    preview.render_default_state()
    preview.render_notification_shade()
    preview.render_quick_settings()
    preview.render_app_launcher()
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
WALLPAPER_TOP = (25, 25, 45)
WALLPAPER_BOT = (15, 15, 35)
TASKBAR_BG = (18, 18, 32)
WINDOW_BG = (28, 28, 48)
WINDOW_TITLE = (35, 35, 55)
WHITE = (220, 220, 230)
GRAY = (100, 100, 120)
GREEN = (60, 200, 100)
ACCENT = (80, 180, 255)


def _get_font(size: int):
    """Get a monospace font."""
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Desktop Preview
# ---------------------------------------------------------------------------

class DesktopPreview:
    """Renders live desktop state to images."""

    def __init__(self, width: int = 1280, height: int = 720):
        self._width = width
        self._height = height
        self._framebuffer: Optional[Image.Image] = None
        self._draw: Optional[ImageDraw.Draw] = None
        self._recording = False
        self._record_frames: List[Image.Image] = []
        self._record_fps: int = 10
        self._font = _get_font(14)
        self._bold_font = _get_font(16)
        self._sans_font = _get_font(18)
        self._started = False

    def start(self):
        """Start the preview renderer."""
        if Image is None:
            raise RuntimeError("Pillow required")
        self._framebuffer = Image.new("RGB", (self._width, self._height), WALLPAPER_TOP)
        self._draw = ImageDraw.Draw(self._framebuffer)
        self._started = True

    def stop(self):
        """Stop the preview renderer."""
        self._started = False
        self._framebuffer = None
        self._draw = None

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def framebuffer(self) -> Optional[Image.Image]:
        return self._framebuffer

    # --- Capture ---

    def capture(self, path: str) -> bool:
        """Save the current framebuffer to a PNG file."""
        if not self._started or self._framebuffer is None:
            return False
        self._framebuffer.save(path)
        return True

    # --- Recording ---

    def start_recording(self, path: str, fps: int = 10):
        """Start recording frames to an animated GIF."""
        self._recording = True
        self._record_frames = []
        self._record_fps = fps
        self._record_path = path

    def stop_recording(self) -> bool:
        """Stop recording and save the GIF."""
        if not self._recording:
            return False
        self._recording = False
        if self._record_frames:
            duration_ms = int(1000 / self._record_fps)
            self._record_frames[0].save(
                self._record_path,
                save_all=True,
                append_images=self._record_frames[1:],
                duration=duration_ms,
                loop=0,
                optimize=True,
            )
            self._record_frames = []
            return True
        return False

    def _maybe_record_frame(self):
        """Record a frame if recording."""
        if self._recording and self._framebuffer:
            self._record_frames.append(self._framebuffer.copy())

    # --- Drawing helpers ---

    def _draw_gradient_bg(self):
        """Draw a gradient wallpaper background."""
        for y in range(self._height):
            t = y / self._height
            r = int(WALLPAPER_TOP[0] * (1 - t) + WALLPAPER_BOT[0] * t)
            g = int(WALLPAPER_TOP[1] * (1 - t) + WALLPAPER_BOT[1] * t)
            b = int(WALLPAPER_TOP[2] * (1 - t) + WALLPAPER_BOT[2] * t)
            self._draw.line([(0, y), (self._width, y)], fill=(r, g, b))

    def _draw_wallpaper_pattern(self):
        """Draw a subtle wallpaper pattern."""
        for y in range(0, self._height, 3):
            wave = int(8 * (y / self._height * 3.14159))
            c = (20 + wave, 20 + wave, 38 + wave)
            self._draw.line([(0, y), (self._width, y)], fill=c)

    def _draw_window(self, x: int, y: int, w: int, h: int, title: str,
                     content_lines: List[str], focused: bool = False):
        """Draw a window with title bar and content."""
        d = self._draw
        outline = ACCENT if focused else (60, 60, 80)
        # Shadow
        d.rounded_rectangle([x + 3, y + 3, x + w + 3, y + h + 3],
                            radius=8, fill=(0, 0, 0, 30))
        # Body
        d.rounded_rectangle([x, y, x + w, y + h],
                            radius=8, fill=WINDOW_BG, outline=outline, width=2)
        # Title bar
        d.rounded_rectangle([x, y, x + w, y + 30],
                            radius=8, fill=WINDOW_TITLE)
        d.text((x + 10, y + 7), title, fill=GRAY, font=self._bold_font)
        # Close/min/max dots
        for j, c in enumerate([(220, 70, 70), (220, 180, 60), (60, 200, 100)]):
            d.ellipse([x + w - 70 + j * 20, y + 9,
                       x + w - 58 + j * 20, y + 21], fill=c)
        # Content
        for i, line in enumerate(content_lines):
            ly = y + 38 + i * 20
            if ly < y + h - 5:
                d.text((x + 10, ly), line, fill=WHITE, font=self._font)

    def _draw_taskbar(self):
        """Draw the taskbar at the bottom."""
        d = self._draw
        ty = self._height - 44
        d.rectangle([0, ty, self._width, self._height], fill=TASKBAR_BG)
        d.line([(0, ty), (self._width, ty)], fill=(40, 40, 55))
        # Clock
        now = time.strftime("%H:%M")
        date = time.strftime("%b %d")
        d.text((self._width - 120, ty + 8), now, fill=WHITE, font=self._bold_font)
        d.text((self._width - 120, ty + 28), date, fill=GRAY, font=self._font)
        # App icons
        icons = ["🖥", "📁", "⚙️", "🌐", "📝"]
        for i, icon in enumerate(icons):
            d.text((20 + i * 50, ty + 10), icon, font=self._font)

    # --- Desktop states ---

    def render_default_state(self) -> Image.Image:
        """Render the default desktop with 3 windows."""
        self._draw_gradient_bg()
        self._draw_wallpaper_pattern()
        self._draw_window(60, 40, 480, 360, "Terminal", [
            "$ neofetch",
            "  OS: Nyrqis 0.1.0",
            "  Kernel: 6.x-nyrqis",
            "  Shell: nyrqis-shell",
            "  Compositor: nyrqis-compositor",
        ], focused=True)
        self._draw_window(180, 90, 580, 400, "Files", [
            "📁 Documents/",
            "📁 Downloads/",
            "📁 Music/",
            "📄 readme.txt",
        ])
        self._draw_window(320, 60, 700, 380, "Settings", [
            "⚙️  Display",
            "     Theme: Dark",
            "     Resolution: 1920x1080",
            "🔊  Sound",
            "     Volume: 75%",
        ])
        self._draw_taskbar()
        self._maybe_record_frame()
        return self._framebuffer

    def render_notification_shade(self) -> Image.Image:
        """Render notification shade pulled from top-left."""
        self._draw_gradient_bg()
        self._draw_wallpaper_pattern()
        # Notification panel (from top)
        d = self._draw
        panel_h = self._height // 2
        d.rectangle([0, 0, self._width, panel_h], fill=(22, 22, 38))
        d.rectangle([0, panel_h, self._width, panel_h + 2], fill=ACCENT)
        # Notification header
        d.text((20, 15), "Notifications", fill=WHITE, font=self._sans_font)
        d.text((self._width - 120, 18), "Clear all", fill=ACCENT, font=self._font)
        # Notifications
        notifications = [
            ("🟡", "System Update", "Nyrqis 0.2.0 available", "2m ago"),
            ("🟢", "Spotify", "Now playing: Ambient Focus", "5m ago"),
            ("🔵", "Discord", "3 new messages", "12m ago"),
            ("🔴", "Battery", "Low battery: 15%", "15m ago"),
        ]
        ny = 55
        for icon, title, body, time_ago in notifications:
            d.rounded_rectangle([15, ny, self._width - 15, ny + 65],
                                radius=8, fill=(30, 30, 48))
            d.text((25, ny + 8), icon, font=self._sans_font)
            d.text((55, ny + 8), title, fill=WHITE, font=self._bold_font)
            d.text((55, ny + 30), body, fill=GRAY, font=self._font)
            d.text((self._width - 80, ny + 8), time_ago, fill=GRAY, font=self._font)
            ny += 75
        self._draw_taskbar()
        self._maybe_record_frame()
        return self._framebuffer

    def render_quick_settings(self) -> Image.Image:
        """Render quick settings panel from top-right."""
        self._draw_gradient_bg()
        self._draw_wallpaper_pattern()
        d = self._draw
        panel_w = 400
        panel_h = self._height // 2
        px = self._width - panel_w
        d.rectangle([px, 0, self._width, panel_h], fill=(22, 22, 38))
        d.rectangle([px, panel_h, self._width, panel_h + 2], fill=ACCENT)
        # Header
        d.text((px + 15, 15), "Quick Settings", fill=WHITE, font=self._sans_font)
        # Toggle tiles
        toggles = [
            ("📶", "Wi-Fi", True),
            ("🔵", "Bluetooth", True),
            ("🌙", "Night Light", False),
            ("✈️", "Airplane", False),
        ]
        for i, (icon, name, active) in enumerate(toggles):
            tx = px + 15 + (i % 2) * 195
            ty = 55 + (i // 2) * 80
            color = ACCENT if active else (50, 50, 65)
            d.rounded_rectangle([tx, ty, tx + 180, ty + 65],
                                radius=10, fill=color)
            d.text((tx + 10, ty + 8), icon, font=self._sans_font)
            d.text((tx + 40, ty + 10), name, fill=WHITE, font=self._font)
        # Sliders
        sliders = [("☀️", "Brightness", 75), ("🔊", "Volume", 60)]
        for i, (icon, name, val) in enumerate(sliders):
            sy = 230 + i * 50
            d.text((px + 15, sy), icon, font=self._sans_font)
            d.text((px + 45, sy), name, fill=WHITE, font=self._font)
            # Slider track
            d.rounded_rectangle([px + 140, sy + 8, px + 380, sy + 20],
                                radius=6, fill=(50, 50, 65))
            # Slider fill
            fw = int(240 * val / 100)
            d.rounded_rectangle([px + 140, sy + 8, px + 140 + fw, sy + 20],
                                radius=6, fill=ACCENT)
        self._draw_taskbar()
        self._maybe_record_frame()
        return self._framebuffer

    def render_app_launcher(self) -> Image.Image:
        """Render the app launcher grid."""
        self._draw_gradient_bg()
        self._draw_wallpaper_pattern()
        d = self._draw
        # Semi-transparent overlay
        overlay = Image.new("RGBA", (self._width, self._height), (0, 0, 0, 120))
        self._framebuffer = Image.alpha_composite(
            self._framebuffer.convert("RGBA"), overlay
        ).convert("RGB")
        self._draw = ImageDraw.Draw(self._framebuffer)
        d = self._draw
        # Search bar
        d.rounded_rectangle([self._width // 2 - 200, 30,
                              self._width // 2 + 200, 70],
                             radius=20, fill=(40, 40, 55))
        d.text((self._width // 2 - 180, 40), "🔍  Search apps...", fill=GRAY, font=self._font)
        # App grid
        apps = [
            ("🖥", "Terminal"), ("📁", "Files"), ("⚙️", "Settings"),
            ("🌐", "Browser"), ("📝", "Editor"), ("📅", "Calendar"),
            ("🎵", "Music"), ("🖼", "Photos"), ("📧", "Mail"),
            ("💬", "Chat"), ("📋", "Notes"), ("🔧", "Utilities"),
            ("📊", "Monitor"), ("🎨", "Theme"), ("🔒", "Security"),
            ("🎮", "Games"), ("📹", "Camera"), ("📦", "Packages"),
        ]
        cols = 6
        cell_w = self._width // cols
        cell_h = 100
        for i, (icon, name) in enumerate(apps):
            col = i % cols
            row = i // cols
            ax = col * cell_w + (cell_w - 70) // 2
            ay = 100 + row * cell_h
            d.rounded_rectangle([ax, ay, ax + 70, ay + 70],
                                radius=12, fill=(35, 35, 50))
            d.text((ax + 20, ay + 10), icon, font=self._sans_font)
            d.text((ax + 10, ay + 45), name, fill=GRAY, font=_get_font(10))
        self._draw_taskbar()
        self._maybe_record_frame()
        return self._framebuffer

    def render_boot_splash(self) -> Image.Image:
        """Render the Nyrqis boot splash screen."""
        d = self._draw
        # Black background
        d.rectangle([0, 0, self._width, self._height], fill=(8, 8, 14))
        # Logo (simplified mushroom)
        cx, cy = self._width // 2, self._height // 2 - 60
        d.ellipse([cx - 60, cy - 60, cx + 60, cy + 20], fill=ACCENT)
        d.rounded_rectangle([cx - 15, cy + 10, cx + 15, cy + 60],
                            radius=10, fill=ACCENT)
        d.ellipse([cx - 15, cy - 20, cx - 5, cy - 8], fill=(8, 8, 14))
        d.ellipse([cx + 5, cy - 20, cx + 15, cy - 8], fill=(8, 8, 14))
        # Title
        d.text((cx - 60, cy + 80), "NYRQIS", fill=WHITE, font=_get_font(42))
        d.text((cx - 80, cy + 130), "Operating System", fill=GRAY, font=self._font)
        # Progress bar
        d.rounded_rectangle([cx - 150, cy + 170, cx + 150, cy + 180],
                            radius=5, fill=(40, 40, 55))
        d.rounded_rectangle([cx - 150, cy + 170, cx - 150 + 200, cy + 180],
                            radius=5, fill=GREEN)
        self._maybe_record_frame()
        return self._framebuffer

    def render_all_states(self, output_dir: str = "/tmp/nyrqis_preview") -> List[str]:
        """Render all desktop states and save to files."""
        os.makedirs(output_dir, exist_ok=True)
        self.start()
        paths = []
        states = [
            ("boot_splash", self.render_boot_splash),
            ("default", self.render_default_state),
            ("notifications", self.render_notification_shade),
            ("quick_settings", self.render_quick_settings),
            ("app_launcher", self.render_app_launcher),
        ]
        for name, render_func in states:
            render_func()
            path = os.path.join(output_dir, f"{name}.png")
            self.capture(path)
            paths.append(path)
        self.stop()
        return paths

    def render_animated_gif(self, path: str, seconds: float = 5.0, fps: int = 10) -> str:
        """Render an animated GIF cycling through all states."""
        self.start()
        self.start_recording(path, fps)
        total_frames = int(seconds * fps)
        states = [
            self.render_boot_splash,
            self.render_default_state,
            self.render_notification_shade,
            self.render_quick_settings,
            self.render_app_launcher,
        ]
        frames_per_state = total_frames // len(states)
        for render_func in states:
            for _ in range(frames_per_state):
                render_func()
                time.sleep(1.0 / fps)
        self.stop_recording()
        self.stop()
        return path
