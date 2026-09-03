"""ShellRenderer — bridges NyrqisShell and Compositor via Backend abstraction.

This module sits between the shell (design/state) and the compositor (rendering),
using Backend.get() so the entire rendering pipeline can switch between
LinuxBackend (PIL) and NyrqisBackend (Rust) without any code changes in
the shell, compositor, or UI apps.

When the user sets NYRQIS_BACKEND=nyrqis or when the Rust compositor
library is available, this renderer automatically uses the NyrqisBackend.
Otherwise it falls back to the LinuxBackend with PIL rendering.

References:
- ADR-0026: Wayland display-server integration
- ADR-0028: Backend abstraction layer
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .backend import Backend, BackendCapabilities, BackendType, SurfaceBuffer

logger = logging.getLogger(__name__)


class ShellRenderer:
    """Renders the Nyrqis shell using the active Backend.

    This class owns the rendering pipeline:
    1. Backend.get().begin_frame()
    2. Draw shell UI via Backend operations
    3. Backend.get().end_frame()

    It also provides a compatibility layer for the existing PIL-based
    Compositor, so the shell can render via either path.
    """

    def __init__(self, width: int = 1920, height: int = 1080, theme: str = "Eclipse"):
        self._width = width
        self._height = height
        self._theme_name = theme
        self._backend: Optional[Backend] = None
        self._initialized = False
        self._frame_count: int = 0
        self._total_frame_time_ms: float = 0.0
        self._last_frame_time_ms: float = 0.0
        self._fps: float = 0.0
        self._fps_update_time: float = 0.0
        self._fps_frame_count: int = 0
        self._render_stats: Dict[str, Any] = {}

        # Theme colors
        self._themes = {
            "Eclipse": {
                "bg": (30, 30, 30),
                "surface": (40, 40, 40),
                "surface_elevated": (50, 50, 50),
                "border": (80, 80, 80),
                "text": (230, 230, 230),
                "text_secondary": (150, 150, 150),
                "accent": (100, 149, 237),
                "button_bg": (60, 60, 60),
            },
            "Solar": {
                "bg": (253, 246, 227),
                "surface": (238, 232, 213),
                "surface_elevated": (250, 244, 230),
                "border": (200, 190, 170),
                "text": (50, 50, 50),
                "text_secondary": (120, 110, 100),
                "accent": (38, 139, 210),
                "button_bg": (230, 222, 205),
            },
        }

    @property
    def theme(self) -> Dict[str, Tuple[int, int, int]]:
        return self._themes.get(self._theme_name, self._themes["Eclipse"])

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            self._backend = Backend.get()
        return self._backend

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frame_time_ms(self) -> float:
        return self._last_frame_time_ms

    @property
    def backend_info(self) -> str:
        info = self.backend.info()
        return f"{info.get('backend', '?')} / {info.get('renderer', '?')}"

    def initialize(self) -> bool:
        """Initialize the backend and renderer."""
        try:
            ok = self.backend.initialize(self._width, self._height)
            if ok:
                self._initialized = True
                logger.info(f"ShellRenderer initialized: {self.backend_info}")
                return True
            logger.warning(f"Backend initialization failed, trying fallback")
            # Try fallback to Linux backend
            Backend.switch_to(BackendType.LINUX)
            self._backend = None
            ok = self.backend.initialize(self._width, self._height)
            self._initialized = ok
            return ok
        except Exception as e:
            logger.error(f"ShellRenderer init failed: {e}")
            return False

    def shutdown(self):
        """Shutdown the backend."""
        if self._initialized:
            self.backend.shutdown()
            self._initialized = False
            logger.info("ShellRenderer shutdown")

    def switch_backend(self, backend_type: BackendType) -> bool:
        """Switch to a different backend at runtime."""
        was_initialized = self._initialized
        if was_initialized:
            self.shutdown()

        Backend.switch_to(backend_type)
        self._backend = None  # force re-fetch

        if was_initialized:
            return self.initialize()
        return True

    # --- Frame lifecycle ---

    def begin_frame(self) -> bool:
        """Begin rendering a frame."""
        if not self._initialized:
            return False
        return self.backend.begin_frame()

    def end_frame(self):
        """End rendering and present."""
        if not self._initialized:
            return
        self.backend.end_frame()
        self._frame_count += 1
        self._last_frame_time_ms = self.backend.get_frame_time_ms()
        self._total_frame_time_ms += self._last_frame_time_ms

        # Update FPS every second
        now = time.time()
        self._fps_frame_count += 1
        if now - self._fps_update_time >= 1.0:
            self._fps = self._fps_frame_count / (now - self._fps_update_time)
            self._fps_frame_count = 0
            self._fps_update_time = now

    # --- Drawing shortcuts ---

    def clear(self, r: int = 0, g: int = 0, b: int = 0, a: int = 255):
        self.backend.clear(r, g, b, a)

    def clear_theme(self):
        """Clear to theme background."""
        bg = self.theme["bg"]
        self.backend.clear(bg[0], bg[1], bg[2])

    def draw_rect(self, x: int, y: int, w: int, h: int,
                   r: int, g: int, b: int, a: int = 255):
        self.backend.draw_rect(x, y, w, h, r, g, b, a)

    def draw_rect_outline(self, x: int, y: int, w: int, h: int,
                           r: int, g: int, b: int, a: int = 255, thickness: int = 1):
        self.backend.draw_rect_outline(x, y, w, h, r, g, b, a, thickness)

    def draw_text(self, x: int, y: int, text: str,
                   r: int = 255, g: int = 255, b: int = 255, size: int = 14,
                   font: str = ""):
        self.backend.draw_text(x, y, text, r, g, b, size, font)

    def draw_line(self, x1: int, y1: int, x2: int, y2: int,
                   r: int, g: int, b: int, a: int = 255, thickness: int = 1):
        self.backend.draw_line(x1, y1, x2, y2, r, g, b, a, thickness)

    def draw_circle(self, cx: int, cy: int, radius: int,
                     r: int, g: int, b: int, a: int = 255):
        self.backend.draw_circle(cx, cy, radius, r, g, b, a)

    def draw_rounded_rect(self, x: int, y: int, w: int, h: int,
                           r: int, g: int, b: int, a: int = 255, radius: int = 4):
        """Draw a rounded rectangle (approximated with overlapping shapes)."""
        # Fill
        self.backend.draw_rect(x + radius, y, w - 2 * radius, h, r, g, b, a)
        self.backend.draw_rect(x, y + radius, w, h - 2 * radius, r, g, b, a)
        # Corners
        self.backend.draw_circle(x + radius, y + radius, radius, r, g, b, a)
        self.backend.draw_circle(x + w - radius, y + radius, radius, r, g, b, a)
        self.backend.draw_circle(x + radius, y + h - radius, radius, r, g, b, a)
        self.backend.draw_circle(x + w - radius, y + h - radius, radius, r, g, b, a)

    # --- Theme-aware drawing ---

    def draw_themed_rect(self, x: int, y: int, w: int, h: int, color_key: str):
        """Draw a rectangle using a theme color."""
        c = self.theme.get(color_key, (100, 100, 100))
        self.backend.draw_rect(x, y, w, h, c[0], c[1], c[2])

    def draw_themed_text(self, x: int, y: int, text: str, color_key: str = "text",
                          size: int = 14):
        """Draw text using a theme color."""
        c = self.theme.get(color_key, (200, 200, 200))
        self.backend.draw_text(x, y, text, c[0], c[1], c[2], size)

    # --- Shell UI rendering ---

    def render_taskbar(self, y: int = 0, apps: List[str] = None):
        """Render the shell taskbar."""
        h = 40
        # Background
        self.draw_themed_rect(0, y, self._width, h, "surface")
        # Border
        self.draw_line(0, y, self._width, y, 80, 80, 80)
        # Start button
        self.draw_rounded_rect(8, y + 4, 40, h - 8, 100, 149, 237, radius=6)
        self.draw_text(20, y + 10, "N", 255, 255, 255, 16)
        # App indicators
        if apps:
            for i, app in enumerate(apps[:8]):
                ax = 60 + i * 32
                self.draw_rounded_rect(ax, y + 6, 28, h - 12, 60, 60, 60, radius=4)
                self.draw_text(ax + 6, y + 10, app[:2].upper(), 230, 230, 230, 11)
        # Clock
        import datetime
        now = datetime.datetime.now().strftime("%H:%M")
        self.draw_text(self._width - 60, y + 10, now, 230, 230, 230, 12)

    def render_window(self, x: int, y: int, w: int, h: int, title: str = "Window"):
        """Render a window frame."""
        # Shadow
        for i in range(3):
            c = max(0, 80 - i * 20)
            self.draw_rect_outline(x + i, y + i, w, h, c, c, c, thickness=1)
        # Body
        self.draw_rect(x, y, w, h, 30, 30, 30)
        self.draw_rect_outline(x, y, w, h, 80, 80, 80)
        # Title bar
        self.draw_rect(x, y, w, 32, 35, 35, 35)
        self.draw_text(x + 12, y + 8, title, 230, 230, 230, 14)
        # Close/min/max buttons
        for i, (glyph, cx) in enumerate([("×", -30), ("−", -56), ("□", -82)]):
            bx = x + w + cx
            self.draw_rounded_rect(bx, y + 6, 20, 20, 50, 50, 50, radius=4)
            self.draw_text(bx + 5, y + 8, glyph, 150, 150, 150, 12)

    # --- Info ---

    def get_surface(self) -> Optional[SurfaceBuffer]:
        """Get the current framebuffer."""
        if self._initialized:
            return self.backend.get_surface()
        return None

    def screenshot(self) -> Optional[SurfaceBuffer]:
        """Capture the current screen."""
        if self._initialized:
            return self.backend.screenshot()
        return None

    def save_screenshot(self, path: str) -> bool:
        """Save screenshot to file."""
        buf = self.screenshot()
        if buf and not buf.is_empty:
            try:
                from PIL import Image
                img = Image.frombytes("RGBA", (buf.width, buf.height), buf.data)
                img.save(path)
                return True
            except Exception as e:
                logger.error(f"Failed to save screenshot: {e}")
        return False

    def stats(self) -> Dict[str, Any]:
        """Return renderer statistics."""
        return {
            "backend": self.backend_info,
            "initialized": self._initialized,
            "width": self._width,
            "height": self._height,
            "theme": self._theme_name,
            "fps": round(self._fps, 1),
            "frame_time_ms": round(self._last_frame_time_ms, 2),
            "total_frames": self._frame_count,
            "total_time_ms": round(self._total_frame_time_ms, 1),
            "avg_frame_ms": round(self._total_frame_time_ms / max(1, self._frame_count), 2),
            "capabilities": {
                k: v for k, v in self.backend.capabilities().__dict__.items()
            },
        }

    def render_stats_overlay(self, x: int = 10, y: int = 10):
        """Render a stats overlay on screen."""
        s = self.stats()
        self.draw_rect(x, y, 250, 100, 0, 0, 0, 180)
        self.draw_text(x + 8, y + 8, f"Backend: {s['backend']}", 200, 200, 200, 12)
        self.draw_text(x + 8, y + 24, f"FPS: {s['fps']:.0f}  Frame: {s['frame_time_ms']:.1f}ms", 200, 200, 200, 12)
        self.draw_text(x + 8, y + 40, f"Frames: {s['total_frames']}  Avg: {s['avg_frame_ms']:.1f}ms", 200, 200, 200, 12)
        caps = s["capabilities"]
        hw = "HW Accel" if caps.get("hardware_acceleration") else "SW"
        vk = "Vulkan" if caps.get("vulkan") else ""
        self.draw_text(x + 8, y + 56, f"Renderer: {hw} {vk}", 200, 200, 200, 12)
        self.draw_text(x + 8, y + 72, f"Theme: {s['theme']}  {s['width']}x{s['height']}", 200, 200, 200, 12)
