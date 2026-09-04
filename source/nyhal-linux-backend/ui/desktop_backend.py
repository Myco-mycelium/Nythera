"""
Nyrqis OS — Desktop Backend Integration

Wires the backend abstraction layer into the shell, compositor, and
desktop session so the entire pipeline can switch between Linux and
Nyrqis kernel backends at runtime.

Usage:
    from ui.desktop_backend import create_desktop

    desktop = create_desktop("shell.nstudio")  # auto-detect backend
    desktop.render_to_png("/tmp/desktop.png")

    # Or force a specific backend:
    desktop = create_desktop("shell.nstudio", BackendType.NYRQIS)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ui.backend_abstraction import (
    BackendType, BackendSet, get_backend, switch_backend,
)


@dataclass
class WindowState:
    """Represents a managed window in the desktop session."""
    id: str
    title: str = ""
    x: int = 0
    y: int = 0
    width: int = 800
    height: int = 600
    visible: bool = True
    focused: bool = False
    minimized: bool = False
    maximized: bool = False
    surface_id: Any = None


@dataclass
class DesktopState:
    """Full state of the desktop session."""
    windows: List[WindowState] = field(default_factory=list)
    active_window: Optional[str] = None
    taskbar_visible: bool = True
    notification_shade_open: bool = False
    quick_settings_open: bool = False
    wallpaper: str = ""


class DesktopBackend:
    """Full desktop session using the backend abstraction layer.

    Provides window management, rendering, and input handling through
    the abstract backend interfaces, so the shell works identically
    on Linux, Nyrqis kernel, or headless.
    """

    def __init__(
        self,
        backend: BackendSet,
        design_path: Optional[str] = None,
        width: int = 1920,
        height: int = 1080,
    ):
        self._backend = backend
        self._design_path = design_path
        self._width = width
        self._height = height
        self._state = DesktopState()
        self._shell = None
        self._compositor_img = None
        self._render_count = 0

        # Load shell if design path provided
        if design_path and os.path.exists(design_path):
            try:
                from ui.shell import NyrqisShell
                self._shell = NyrqisShell.from_file(design_path)
            except Exception:
                self._shell = None

        # Initialize GPU
        self._backend.gpu.initialize()

        # Create the internal compositor
        self._init_compositor()

    def _init_compositor(self):
        """Initialize the PIL compositor for rendering."""
        try:
            from ui.compositor import Compositor
            self._pil_compositor = Compositor()
        except Exception:
            self._pil_compositor = None

    @property
    def state(self) -> DesktopState:
        return self._state

    @property
    def backend_type(self) -> BackendType:
        return self._backend.backend_type

    # -- Window Management --

    def create_window(self, title: str = "", width: int = 800, height: int = 600) -> WindowState:
        """Create a new window."""
        win_id = f"win-{len(self._state.windows)}"
        x = (self._width - width) // 2
        y = (self._height - height) // 2

        surface_id = self._backend.compositor.create_surface(width, height)

        win = WindowState(
            id=win_id, title=title, x=x, y=y,
            width=width, height=height, surface_id=surface_id,
        )
        self._state.windows.append(win)
        return win

    def close_window(self, win_id: str) -> bool:
        """Close a window."""
        for i, w in enumerate(self._state.windows):
            if w.id == win_id:
                if w.surface_id is not None:
                    self._backend.compositor.destroy_surface(w.surface_id)
                del self._state.windows[i]
                return True
        return False

    def focus_window(self, win_id: str) -> bool:
        """Focus a window."""
        for w in self._state.windows:
            w.focused = (w.id == win_id)
        self._state.active_window = win_id
        return True

    def minimize_window(self, win_id: str) -> bool:
        for w in self._state.windows:
            if w.id == win_id:
                w.minimized = True
                w.visible = False
                return True
        return False

    def maximize_window(self, win_id: str) -> bool:
        for w in self._state.windows:
            if w.id == win_id:
                w.maximized = not w.maximized
                if w.maximized:
                    w.x, w.y = 0, 0
                    w.width, w.height = self._width, self._height
                return True
        return False

    # -- Rendering --

    def render_to_image(self) -> Any:
        """Render the desktop to a PIL Image via the backend abstraction."""
        # Get framebuffer from backend
        fb = self._backend.display.get_framebuffer()

        if fb is not None and self._pil_compositor:
            try:
                from PIL import Image, ImageDraw, ImageFont
                img = fb.copy() if hasattr(fb, 'copy') else Image.new("RGB", (self._width, self._height), (15, 15, 30))
                draw = ImageDraw.Draw(img)

                # Draw wallpaper gradient
                for y in range(self._height):
                    r = int(15 + (y / self._height) * 10)
                    g = int(15 + (y / self._height) * 5)
                    b = int(30 + (y / self._height) * 15)
                    draw.line([(0, y), (self._width, y)], fill=(r, g, b))

                # Draw windows
                for win in self._state.windows:
                    if not win.visible:
                        continue
                    # Window shadow
                    draw.rectangle(
                        [win.x + 4, win.y + 4, win.x + win.width + 4, win.y + win.height + 4],
                        fill=(0, 0, 0, 80),
                    )
                    # Window background
                    draw.rectangle(
                        [win.x, win.y, win.x + win.width, win.y + win.height],
                        fill=(30, 30, 50) if not win.focused else (35, 35, 60),
                        outline=(80, 80, 120) if win.focused else (60, 60, 80),
                    )
                    # Title bar
                    draw.rectangle(
                        [win.x, win.y, win.x + win.width, win.y + 32],
                        fill=(40, 40, 65) if not win.focused else (50, 50, 80),
                    )
                    # Title text
                    try:
                        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
                    except (OSError, IOError):
                        font = ImageFont.load_default()
                    draw.text((win.x + 10, win.y + 8), win.title or win.id, fill=(200, 200, 220), font=font)

                    # Close button
                    draw.rectangle(
                        [win.x + win.width - 28, win.y + 6, win.x + win.width - 8, win.y + 26],
                        fill=(200, 60, 60),
                    )

                    # Shell content if available
                    if self._shell:
                        try:
                            result = self._shell.run()
                            if result.get("text_preview"):
                                lines = result["text_preview"].split("\n")[:15]
                                for i, line in enumerate(lines):
                                    draw.text(
                                        (win.x + 10, win.y + 40 + i * 18),
                                        line[:80], fill=(160, 160, 180), font=font,
                                    )
                        except Exception:
                            pass

                # Draw taskbar
                if self._state.taskbar_visible:
                    tb_y = self._height - 40
                    draw.rectangle([0, tb_y, self._width, self._height], fill=(20, 20, 35))
                    draw.line([(0, tb_y), (self._width, tb_y)], fill=(60, 60, 100))

                    # Taskbar items
                    try:
                        tb_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
                    except (OSError, IOError):
                        tb_font = ImageFont.load_default()
                    x_off = 10
                    for win in self._state.windows:
                        draw.rectangle([x_off, tb_y + 5, x_off + 120, tb_y + 35],
                                       fill=(50, 50, 80) if win.focused else (35, 35, 55))
                        draw.text((x_off + 5, tb_y + 10), win.title or win.id[:12],
                                  fill=(180, 180, 200), font=tb_font)
                        x_off += 130

                    # Clock
                    clock = time.strftime("%H:%M")
                    draw.text((self._width - 80, tb_y + 10), clock,
                              fill=(180, 180, 200), font=tb_font)

                self._compositor_img = img
                self._render_count += 1
                return img

            except Exception:
                pass

        # Fallback: use PIL directly
        try:
            from PIL import Image
            return Image.new("RGB", (self._width, self._height), (15, 15, 30))
        except ImportError:
            return None

    def render_to_png(self, path: str) -> bool:
        """Render the desktop and save to PNG."""
        img = self.render_to_image()
        if img is not None and hasattr(img, 'save'):
            img.save(path)
            return True
        return False

    def render_shell(self) -> Optional[Dict]:
        """Run the shell and return its state."""
        if self._shell:
            try:
                return self._shell.run()
            except Exception:
                pass
        return None

    # -- Input Handling --

    def handle_key(self, key: str) -> str:
        """Handle a keyboard event."""
        if key == "super":
            self._state.taskbar_visible = not self._state.taskbar_visible
            return "toggle_taskbar"
        elif key == "alt+tab":
            # Cycle window focus
            visible = [w for w in self._state.windows if w.visible]
            if visible:
                current_idx = next((i for i, w in enumerate(visible) if w.focused), 0)
                next_idx = (current_idx + 1) % len(visible)
                for w in visible:
                    w.focused = False
                visible[next_idx].focused = True
                self._state.active_window = visible[next_idx].id
            return "cycle_focus"
        elif key == "alt+f4":
            if self._state.active_window:
                self.close_window(self._state.active_window)
            return "close_window"

        return "noop"

    def poll_input(self) -> List[Dict]:
        """Poll for input events from the backend."""
        return self._backend.input_backend.poll_events()

    # -- Info --

    def summary(self) -> Dict:
        """Get a summary of the desktop state."""
        return {
            "backend": self._backend.backend_type.value,
            "windows": len(self._state.windows),
            "active_window": self._state.active_window,
            "render_count": self._render_count,
            "size": f"{self._width}x{self._height}",
        }


def create_desktop(
    design_path: Optional[str] = None,
    backend_type: Optional[BackendType] = None,
    width: int = 1920,
    height: int = 1080,
) -> DesktopBackend:
    """Factory function to create a DesktopBackend.

    Parameters
    ----------
    design_path : str, optional
        Path to a .nstudio shell design file.
    backend_type : BackendType, optional
        Force a specific backend. If None, auto-detects.
    width, height : int
        Desktop resolution.

    Returns
    -------
    DesktopBackend
        A fully initialized desktop session.
    """
    backend = get_backend(backend_type)
    return DesktopBackend(
        backend=backend,
        design_path=design_path,
        width=width,
        height=height,
    )
