"""
Nyrqis OS — Live Wayland Session

Provides a live desktop session that uses the Rust compositor for
real rendering when available, falling back to PIL-based software
rendering for headless/CI environments.

This is the bridge between the shell/apps and the actual display hardware.

Usage:
    session = LiveSession()
    session.start()
    session.render_frame()  # renders one frame
    session.handle_input(event)
    session.stop()
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
    ImageFont = None


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

class SessionState:
    """Current state of the live session."""
    BOOTING = "booting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"


class WindowInfo:
    """Information about a managed window."""

    def __init__(self, handle: int, title: str = "", x: int = 0, y: int = 0,
                 width: int = 800, height: int = 600):
        self.handle = handle
        self.title = title
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.visible = True
        self.focused = False
        self.minimized = False
        self.maximized = False
        self.surface_id = -1


# ---------------------------------------------------------------------------
# Live Session
# ---------------------------------------------------------------------------

class LiveSession:
    """
    Live Wayland session with real compositor rendering.

    Uses Rust compositor when available, PIL fallback otherwise.
    """

    def __init__(self, width: int = 1920, height: int = 1080,
                 backend_type: str = "auto"):
        self._width = width
        self._height = height
        self._state = SessionState.STOPPED
        self._backend_type = backend_type
        self._rust_backend = None
        self._framebuffer = None
        self._draw = None
        self._frame_count = 0
        self._fps = 0.0
        self._last_frame_time = 0.0
        self._windows: Dict[int, WindowInfo] = {}
        self._window_counter = 0
        self._focused_window: Optional[int] = None
        self._cursor_x = 0
        self._cursor_y = 0
        self._wallpaper_color = (20, 20, 38)
        self._taskbar_height = 48
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
    def window_count(self) -> int:
        return len(self._windows)

    @property
    def using_rust(self) -> bool:
        """True if using the Rust compositor."""
        return self._rust_backend is not None and \
               self._rust_backend.compositor.available

    def start(self) -> bool:
        """Start the live session."""
        if self._state != SessionState.STOPPED:
            return False

        self._state = SessionState.BOOTING

        # Try to load Rust backend
        try:
            from ui.rust_ffi import get_rust_backend
            self._rust_backend = get_rust_backend()
        except ImportError:
            self._rust_backend = None

        # Initialize rendering
        if Image is not None:
            self._framebuffer = Image.new("RGB", (self._width, self._height),
                                          self._wallpaper_color)
            self._draw = ImageDraw.Draw(self._framebuffer)
            self._load_fonts()

        # Start Rust compositor if available
        if self.using_rust:
            self._rust_backend.compositor.start()
            self._rust_backend.compositor.add_output(
                self._width, self._height, "default"
            )

        # Create initial windows
        self._create_default_windows()

        self._state = SessionState.RUNNING
        self._last_frame_time = time.time()
        return True

    def stop(self):
        """Stop the live session."""
        if self._state == SessionState.STOPPED:
            return

        self._state = SessionState.STOPPING

        # Destroy all windows
        for handle in list(self._windows.keys()):
            self.destroy_window(handle)

        # Stop Rust compositor
        if self.using_rust and self._rust_backend.compositor.started:
            self._rust_backend.compositor.stop()

        self._framebuffer = None
        self._draw = None
        self._state = SessionState.STOPPED

    def render_frame(self) -> Optional[Image.Image]:
        """Render one frame and return the framebuffer."""
        if self._state != SessionState.RUNNING:
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

        draw = self._draw

        # Clear to wallpaper
        draw.rectangle([0, 0, self._width, self._height],
                       fill=self._wallpaper_color)

        # Draw wallpaper pattern
        for y in range(0, self._height, 4):
            wave = int(8 * (y / self._height * 3.14159))
            c = (20 + wave, 20 + wave, 38 + wave)
            draw.line([(0, y), (self._width, y)], fill=c)

        # Draw windows
        for handle, win in self._windows.items():
            if not win.visible or win.minimized:
                continue
            self._draw_window(draw, win)

        # Draw taskbar
        self._draw_taskbar(draw)

        # Draw cursor
        self._draw_cursor(draw)

        # Commit to Rust compositor if available
        if self.using_rust and self._rust_backend.compositor.started:
            self._rust_backend.compositor.commit_surface(0)

        return self._framebuffer

    def handle_input(self, event: Dict) -> str:
        """Handle an input event. Returns action taken."""
        event_type = event.get("type", "")

        if event_type == "key":
            return self._handle_key(event)
        elif event_type == "mouse_move":
            self._cursor_x = event.get("x", 0)
            self._cursor_y = event.get("y", 0)
            return "cursor_moved"
        elif event_type == "mouse_click":
            return self._handle_click(event)
        elif event_type == "mouse_scroll":
            return "scroll"

        return "unknown"

    # --- Window management ---

    def create_window(self, title: str, x: int = 0, y: int = 0,
                      width: int = 800, height: int = 600) -> int:
        """Create a new window. Returns handle."""
        self._window_counter += 1
        handle = self._window_counter

        win = WindowInfo(handle, title, x, y, width, height)

        # Create Rust surface if available
        if self.using_rust and self._rust_backend.compositor.started:
            win.surface_id = self._rust_backend.compositor.create_surface(
                0, width, height
            )

        self._windows[handle] = win
        return handle

    def destroy_window(self, handle: int) -> bool:
        """Destroy a window."""
        win = self._windows.pop(handle, None)
        if win is None:
            return False

        # Destroy Rust surface
        if self.using_rust and win.surface_id >= 0:
            self._rust_backend.compositor.destroy_surface(win.surface_id)

        if self._focused_window == handle:
            self._focused_window = None
        return True

    def focus_window(self, handle: int) -> bool:
        """Focus a window."""
        if handle not in self._windows:
            return False
        # Unfocus all
        for win in self._windows.values():
            win.focused = False
        self._windows[handle].focused = True
        self._focused_window = handle
        return True

    def minimize_window(self, handle: int) -> bool:
        """Minimize a window."""
        if handle in self._windows:
            self._windows[handle].minimized = True
            return True
        return False

    def maximize_window(self, handle: int) -> bool:
        """Maximize/restore a window."""
        if handle in self._windows:
            win = self._windows[handle]
            if win.maximized:
                win.maximized = False
                win.width = 800
                win.height = 600
            else:
                win.maximized = True
                win.width = self._width
                win.height = self._height - self._taskbar_height
                win.x = 0
                win.y = 0
            return True
        return False

    def get_window(self, handle: int) -> Optional[WindowInfo]:
        """Get window info."""
        return self._windows.get(handle)

    def get_windows(self) -> List[WindowInfo]:
        """Get all windows."""
        return list(self._windows.values())

    # --- Private methods ---

    def _load_fonts(self):
        """Load fonts for rendering."""
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ]
        bold_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        ]
        for p in paths:
            if os.path.exists(p):
                self._font = ImageFont.truetype(p, 14)
                break
        for p in bold_paths:
            if os.path.exists(p):
                self._bold_font = ImageFont.truetype(p, 14)
                break
        if self._font is None:
            self._font = ImageFont.load_default()
            self._bold_font = self._font

    def _create_default_windows(self):
        """Create the default desktop windows."""
        # Terminal
        self.create_window("Terminal", 80, 60, 520, 400)
        # File Manager
        self.create_window("Files", 200, 120, 640, 420)
        # Settings
        self.create_window("Settings", 350, 80, 780, 400)
        # Focus terminal
        if self._windows:
            first_handle = list(self._windows.keys())[0]
            self.focus_window(first_handle)

    def _draw_window(self, draw: ImageDraw.Draw, win: WindowInfo):
        """Draw a window."""
        # Shadow
        draw.rounded_rectangle(
            [win.x + 3, win.y + 3, win.x + win.width + 3, win.y + win.height + 3],
            radius=8, fill=(0, 0, 0, 30)
        )

        # Window body
        outline = (80, 180, 255) if win.focused else (60, 60, 80)
        draw.rounded_rectangle(
            [win.x, win.y, win.x + win.width, win.y + win.height],
            radius=8, fill=(28, 28, 48), outline=outline, width=2
        )

        # Title bar
        draw.rounded_rectangle(
            [win.x, win.y, win.x + win.width, win.y + 32],
            radius=8, fill=(35, 35, 55)
        )

        # Title
        font = self._bold_font or self._font
        draw.text((win.x + 12, win.y + 8), win.title, fill=(160, 160, 180), font=font)

        # Close/minimize/maximize dots
        for j, color in enumerate([(220, 70, 70), (220, 180, 60), (60, 200, 100)]):
            draw.ellipse(
                [win.x + win.width - 80 + j * 22, win.y + 10,
                 win.x + win.width - 68 + j * 22, win.y + 22],
                fill=color
            )

        # Window content area
        content_y = win.y + 40
        content_h = win.height - 40

        # Terminal content
        if "Terminal" in win.title:
            content_lines = [
                ("$ ", (60, 200, 100)),
                ("nyrqis@desktop:~$ ", (60, 200, 100)),
                ("  neofetch", (160, 160, 180)),
                ("  OS: Nyrqis 0.1.0", (160, 160, 180)),
                ("  Kernel: 6.x-nyrqis", (160, 160, 180)),
                ("  Shell: nyrqis-shell", (160, 160, 180)),
                ("  Compositor: nyrqis-compositor", (160, 160, 180)),
            ]
            for i, (text, color) in enumerate(content_lines):
                if content_y + i * 20 < win.y + win.height - 10:
                    draw.text((win.x + 10, content_y + i * 20), text,
                              fill=color, font=self._font)

        # Files content
        elif "Files" in win.title:
            items = [
                "📁 Documents/",
                "📁 Downloads/",
                "📁 Music/",
                "📁 Pictures/",
                "📁 Videos/",
                "📄 readme.txt",
                "📄 notes.md",
            ]
            for i, item in enumerate(items):
                if content_y + i * 22 < win.y + win.height - 10:
                    draw.text((win.x + 15, content_y + i * 22), item,
                              fill=(200, 200, 210), font=self._font)

        # Settings content
        elif "Settings" in win.title:
            settings = [
                "⚙️  Display",
                "     Theme: Dark",
                "     Resolution: 1920x1080",
                "     Refresh: 60Hz",
                "🔊  Sound",
                "     Volume: 75%",
                "🌐  Network",
                "     Wi-Fi: Connected",
                "🔒  Privacy",
                "     Screen lock: On",
            ]
            for i, item in enumerate(settings):
                if content_y + i * 20 < win.y + win.height - 10:
                    color = (200, 200, 210) if not item.startswith("     ") else (120, 120, 140)
                    draw.text((win.x + 15, content_y + i * 20), item,
                              fill=color, font=self._font)

    def _draw_taskbar(self, draw: ImageDraw.Draw):
        """Draw the taskbar."""
        ty = self._height - self._taskbar_height
        draw.rectangle([0, ty, self._width, self._height], fill=(18, 18, 32))

        # Taskbar border
        draw.line([(0, ty), (self._width, ty)], fill=(40, 40, 55), width=1)

        # App icons
        icons = ["🖥", "📁", "⚙️", "🌐", "📝"]
        for i, icon in enumerate(icons):
            draw.text((20 + i * 50, ty + 12), icon, font=self._font)

        # Window indicators
        for handle, win in self._windows.items():
            if not win.minimized:
                idx = list(self._windows.keys()).index(handle)
                ix = 270 + idx * 20
                color = (80, 180, 255) if win.focused else (60, 60, 80)
                draw.ellipse([ix, ty + 18, ix + 8, ty + 26], fill=color)

        # Clock
        now = time.strftime("%H:%M")
        date = time.strftime("%b %d")
        font = self._bold_font or self._font
        draw.text((self._width - 120, ty + 10), now, fill=(220, 220, 230), font=font)
        draw.text((self._width - 120, ty + 30), date, fill=(100, 100, 120), font=self._font)

        # FPS
        draw.text((self._width - 200, ty + 30), f"{self._fps:.0f} fps",
                   fill=(80, 120, 80), font=self._font)

    def _draw_cursor(self, draw: ImageDraw.Draw):
        """Draw the mouse cursor."""
        x, y = int(self._cursor_x), int(self._cursor_y)
        # Simple arrow cursor
        draw.polygon([(x, y), (x, y + 16), (x + 5, y + 12), (x + 10, y + 18),
                       (x + 13, y + 16), (x + 8, y + 10), (x + 14, y + 10)],
                      fill=(220, 220, 230))

    def _handle_key(self, event: Dict) -> str:
        """Handle a key event."""
        key = event.get("key", "")
        ctrl = event.get("ctrl", False)
        alt = event.get("alt", False)
        meta = event.get("meta", False)

        # Window management shortcuts
        if meta or ctrl:
            if key == "tab":
                return self._cycle_focus()
            elif key == "q":
                if self._focused_window:
                    self.destroy_window(self._focused_window)
                    return "window_closed"
            elif key == "n":
                h = self.create_window("New Window", 150, 100, 600, 400)
                self.focus_window(h)
                return "window_created"
        elif key == "f11":
            if self._focused_window:
                self.maximize_window(self._focused_window)
                return "toggle_maximize"

        return "key_ignored"

    def _handle_click(self, event: Dict) -> str:
        """Handle a mouse click."""
        x, y = event.get("x", 0), event.get("y", 0)

        # Check taskbar click
        if y >= self._height - self._taskbar_height:
            return self._handle_taskbar_click(x)

        # Check window click
        for handle, win in reversed(list(self._windows.items())):
            if (win.x <= x <= win.x + win.width and
                win.y <= y <= win.y + win.height):
                self.focus_window(handle)
                return f"window_focused:{handle}"

        return "click_ignored"

    def _cycle_focus(self) -> str:
        """Cycle focus between windows."""
        visible = [h for h, w in self._windows.items()
                   if w.visible and not w.minimized]
        if not visible:
            return "no_windows"

        if self._focused_window in visible:
            idx = visible.index(self._focused_window)
            next_idx = (idx + 1) % len(visible)
        else:
            next_idx = 0

        self.focus_window(visible[next_idx])
        return "focus_cycled"

    def _handle_taskbar_click(self, x: int) -> str:
        """Handle a click in the taskbar."""
        handles = list(self._windows.keys())
        if not handles:
            return "no_windows"

        icon_idx = x // 50
        if 0 <= icon_idx < len(handles):
            handle = handles[icon_idx]
            win = self._windows[handle]
            if win.minimized:
                win.minimized = False
                self.focus_window(handle)
                return "window_restored"
            elif self._focused_window == handle:
                win.minimized = True
                return "window_minimized"
            else:
                self.focus_window(handle)
                return "window_focused"

        return "taskbar_ignored"

    def summary(self) -> Dict:
        """Get session summary."""
        return {
            "state": self._state,
            "width": self._width,
            "height": self._height,
            "using_rust": self.using_rust,
            "frame_count": self._frame_count,
            "fps": self._fps,
            "window_count": self.window_count,
            "focused_window": self._focused_window,
            "cursor": (self._cursor_x, self._cursor_y),
        }


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

def create_session(width: int = 1920, height: int = 1080,
                   backend: str = "auto") -> LiveSession:
    """Create and start a live session."""
    session = LiveSession(width, height, backend)
    session.start()
    return session
