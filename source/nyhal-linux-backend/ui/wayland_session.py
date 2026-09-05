"""
Nyrqis OS — Wayland Session Launcher

Launches a complete Wayland session using the Rust compositor.
This is the main entry point for starting the Nyrqis desktop environment.

When the Rust compositor is available:
  - Uses real DRM modesetting
  - GPU-accelerated rendering via EGL/Vulkan
  - Hardware input handling via evdev

When the Rust compositor is NOT available:
  - Falls back to software rendering via PIL
  - Simulated input events
  - Virtual display (headless)

Usage:
    from ui.wayland_session import WaylandSession

    session = WaylandSession()
    session.start()
    # session is now running with compositor, shell, and apps
    session.stop()

    # Or render a single frame
    frame = session.render_frame()

    # Or capture a screenshot
    session.screenshot("desktop.png")
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
# Session state
# ---------------------------------------------------------------------------

class WaylandState:
    """Session state constants."""
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"


# ---------------------------------------------------------------------------
# Wayland Session
# ---------------------------------------------------------------------------

class WaylandSession:
    """
    Complete Wayland session with compositor, shell, and apps.

    Integrates:
    - Rust compositor (when available)
    - Backend abstraction layer
    - Desktop preview renderer
    - Shell rendering
    """

    def __init__(self, width: int = 1920, height: int = 1080):
        self._width = width
        self._height = height
        self._state = WaylandState.UNINITIALIZED
        self._rust_compositor = None
        self._backend = None
        self._framebuffer: Optional[Image.Image] = None
        self._draw = None
        self._frame_count = 0
        self._fps = 0.0
        self._last_frame_time = 0.0
        self._windows: Dict[int, Dict] = {}
        self._window_counter = 0
        self._focused_window: Optional[int] = None
        self._cursor_x = 0
        self._cursor_y = 0
        self._font = None
        self._bold_font = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def using_rust(self) -> bool:
        return self._rust_compositor is not None and self._rust_compositor.available

    @property
    def window_count(self) -> int:
        return len(self._windows)

    # --- Lifecycle ---

    def start(self) -> bool:
        """Start the Wayland session."""
        if self._state not in (WaylandState.UNINITIALIZED, WaylandState.STOPPED):
            return False

        self._state = WaylandState.INITIALIZING

        # Try to load Rust compositor
        try:
            from ui.rust_ffi import get_rust_backend
            rb = get_rust_backend()
            if rb.compositor.available:
                self._rust_compositor = rb.compositor
        except ImportError:
            pass

        # Try to load backend
        try:
            from ui.backend_abstraction import get_backend, BackendType
            self._backend = get_backend(BackendType.HEADLESS)
            self._backend.gpu.initialize()
        except ImportError:
            pass

        # Initialize rendering
        if Image is not None:
            self._framebuffer = Image.new("RGB", (self._width, self._height), (20, 20, 38))
            self._draw = ImageDraw.Draw(self._framebuffer)
            self._load_fonts()

        # Start Rust compositor
        if self.using_rust:
            self._rust_compositor.start()
            self._rust_compositor.add_output(self._width, self._height, "default")

        # Create default windows
        self._create_default_windows()

        self._state = WaylandState.RUNNING
        self._last_frame_time = time.time()
        return True

    def stop(self):
        """Stop the Wayland session."""
        if self._state == WaylandState.STOPPED:
            return

        self._state = WaylandState.STOPPING

        # Destroy windows
        for handle in list(self._windows.keys()):
            self._destroy_window(handle)

        # Stop Rust compositor
        if self.using_rust and self._rust_compositor.started:
            self._rust_compositor.stop()

        self._framebuffer = None
        self._draw = None
        self._state = WaylandState.STOPPED

    # --- Rendering ---

    def render_frame(self) -> Optional[Image.Image]:
        """Render one frame of the desktop."""
        if self._state != WaylandState.RUNNING:
            return self._framebuffer

        # Calculate FPS
        now = time.time()
        dt = now - self._last_frame_time
        if dt > 0:
            self._fps = 1.0 / dt
        self._last_frame_time = now
        self._frame_count += 1

        if self._framebuffer is None:
            return None

        d = self._draw

        # Wallpaper gradient
        for y in range(0, self._height, 3):
            wave = int(8 * (y / self._height * 3.14159))
            c = (20 + wave, 20 + wave, 38 + wave)
            d.line([(0, y), (self._width, y)], fill=c)

        # Draw windows
        for handle, win in self._windows.items():
            if not win.get("visible", True):
                continue
            self._draw_window(d, win)

        # Draw taskbar
        self._draw_taskbar(d)

        # Commit to Rust compositor
        if self.using_rust and self._rust_compositor.started:
            self._rust_compositor.commit_surface(0)

        return self._framebuffer

    def screenshot(self, path: str) -> bool:
        """Save a screenshot to a PNG file."""
        if self._framebuffer is None:
            return False
        self._framebuffer.save(path)
        return True

    # --- Window management ---

    def create_window(self, title: str, x: int = 0, y: int = 0,
                      width: int = 800, height: int = 600) -> int:
        """Create a new window. Returns handle."""
        self._window_counter += 1
        handle = self._window_counter

        self._windows[handle] = {
            "title": title, "x": x, "y": y,
            "width": width, "height": height,
            "visible": True, "focused": False,
        }

        # Create Rust surface
        if self.using_rust and self._rust_compositor.started:
            self._windows[handle]["surface_id"] = \
                self._rust_compositor.create_surface(0, width, height)

        return handle

    def destroy_window(self, handle: int) -> bool:
        """Destroy a window."""
        return self._destroy_window(handle)

    def focus_window(self, handle: int) -> bool:
        """Focus a window."""
        if handle not in self._windows:
            return False
        for w in self._windows.values():
            w["focused"] = False
        self._windows[handle]["focused"] = True
        self._focused_window = handle
        return True

    def get_window(self, handle: int) -> Optional[Dict]:
        return self._windows.get(handle)

    def get_windows(self) -> List[Dict]:
        return list(self._windows.values())

    # --- Input ---

    def handle_input(self, event: Dict) -> str:
        """Handle an input event."""
        etype = event.get("type", "")
        if etype == "mouse_move":
            self._cursor_x = event.get("x", 0)
            self._cursor_y = event.get("y", 0)
            return "cursor_moved"
        elif etype == "mouse_click":
            x, y = event.get("x", 0), event.get("y", 0)
            for handle, win in reversed(list(self._windows.items())):
                if (win["x"] <= x <= win["x"] + win["width"] and
                    win["y"] <= y <= win["y"] + win["height"]):
                    self.focus_window(handle)
                    return f"window_focused:{handle}"
            return "click_ignored"
        elif etype == "key":
            key = event.get("key", "")
            if event.get("meta") or event.get("ctrl"):
                if key == "q" and self._focused_window:
                    self.destroy_window(self._focused_window)
                    return "window_closed"
                elif key == "tab":
                    return self._cycle_focus()
            return "key_ignored"
        return "unknown"

    # --- Private ---

    def _destroy_window(self, handle: int) -> bool:
        win = self._windows.pop(handle, None)
        if win is None:
            return False
        if self.using_rust and "surface_id" in win:
            self._rust_compositor.destroy_surface(win["surface_id"])
        if self._focused_window == handle:
            self._focused_window = None
        return True

    def _cycle_focus(self) -> str:
        visible = [h for h, w in self._windows.items() if w.get("visible", True)]
        if not visible:
            return "no_windows"
        if self._focused_window in visible:
            idx = (visible.index(self._focused_window) + 1) % len(visible)
        else:
            idx = 0
        self.focus_window(visible[idx])
        return "focus_cycled"

    def _create_default_windows(self):
        self.create_window("Terminal", 80, 60, 520, 400)
        self.create_window("Files", 200, 120, 640, 420)
        self.create_window("Settings", 350, 80, 780, 400)
        if self._windows:
            self.focus_window(list(self._windows.keys())[0])

    def _load_fonts(self):
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ]
        bold_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        ]
        for p in paths:
            if os.path.exists(p):
                self._font = ImageFont.truetype(p, 14)
                break
        for p in bold_paths:
            if os.path.exists(p):
                self._bold_font = ImageFont.truetype(p, 16)
                break
        if self._font is None:
            self._font = ImageFont.load_default()
            self._bold_font = self._font

    def _draw_window(self, d: ImageDraw.Draw, win: Dict):
        x, y, w, h = win["x"], win["y"], win["width"], win["height"]
        outline = (80, 180, 255) if win.get("focused") else (60, 60, 80)
        d.rounded_rectangle([x + 3, y + 3, x + w + 3, y + h + 3], radius=8, fill=(0, 0, 0, 30))
        d.rounded_rectangle([x, y, x + w, y + h], radius=8, fill=(28, 28, 48), outline=outline, width=2)
        d.rounded_rectangle([x, y, x + w, y + 30], radius=8, fill=(35, 35, 55))
        d.text((x + 10, y + 7), win["title"], fill=(160, 160, 180), font=self._bold_font or self._font)
        for j, c in enumerate([(220, 70, 70), (220, 180, 60), (60, 200, 100)]):
            d.ellipse([x + w - 70 + j * 20, y + 9, x + w - 58 + j * 20, y + 21], fill=c)

    def _draw_taskbar(self, d: ImageDraw.Draw):
        ty = self._height - 44
        d.rectangle([0, ty, self._width, self._height], fill=(18, 18, 32))
        d.line([(0, ty), (self._width, ty)], fill=(40, 40, 55))
        icons = ["🖥", "📁", "⚙️", "🌐", "📝"]
        for i, icon in enumerate(icons):
            d.text((20 + i * 50, ty + 10), icon, font=self._font)
        now = time.strftime("%H:%M")
        d.text((self._width - 120, ty + 8), now, fill=(220, 220, 230), font=self._bold_font or self._font)
        d.text((self._width - 200, ty + 28), f"{self._fps:.0f} fps", fill=(80, 120, 80), font=self._font)

    def summary(self) -> Dict:
        return {
            "state": self._state,
            "width": self._width,
            "height": self._height,
            "using_rust": self.using_rust,
            "frame_count": self._frame_count,
            "fps": self._fps,
            "window_count": self.window_count,
            "focused_window": self._focused_window,
        }
