"""Linux Backend — PIL/Python-based backend for Nyrqis shell.

This is the fallback backend that works on any system with Python + Pillow.
It renders to PIL Images which can be saved to files or displayed.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .backend import (
    Backend, BackendCapabilities, BackendType, DisplayOutput,
    InputEvent, PixelFormat, SurfaceBuffer,
)

# Lazy PIL import
_PIL = None


def _get_pil():
    global _PIL
    if _PIL is None:
        from PIL import Image, ImageDraw, ImageFont
        _PIL = (Image, ImageDraw, ImageFont)
    return _PIL


class LinuxBackend(Backend):
    """Linux/PIL-based backend.

    Uses Pillow for software rendering. Good for:
    - Testing and development
    - Systems without GPU
    - Headless/CI environments
    """

    def __init__(self):
        self._initialized = False
        self._width: int = 1920
        self._height: int = 1080
        self._image = None
        self._draw = None
        self._frame_time: float = 0.0
        self._frame_start: float = 0.0
        self._vsync: bool = True
        self._outputs: List[DisplayOutput] = []
        self._cursor_x: float = 0.0
        self._cursor_y: float = 0.0
        self._window_counter: int = 0
        self._windows: Dict[int, Dict] = {}
        self._clipboard_text: str = ""
        self._cursor_type: str = "default"
        self._frame_count: int = 0

    def initialize(self, width: int = 1920, height: int = 1080) -> bool:
        try:
            Image, ImageDraw, ImageFont = _get_pil()
            self._width = width
            self._height = height
            self._image = Image.new("RGBA", (width, height), (30, 30, 30, 255))
            self._draw = ImageDraw.Draw(self._image)
            self._outputs = [
                DisplayOutput(0, "eDP-1", width, height, 60.0, 1.0, 0, 0, True),
            ]
            self._initialized = True
            return True
        except ImportError:
            return False

    def shutdown(self):
        self._image = None
        self._draw = None
        self._initialized = False

    def is_available(self) -> bool:
        try:
            import PIL
            return True
        except ImportError:
            return False

    def begin_frame(self) -> bool:
        if not self._initialized:
            return False
        self._frame_start = time.time()
        # Clear to background
        self._image = _PIL[0].new("RGBA", (self._width, self._height), (30, 30, 30, 255))
        self._draw = _PIL[1].Draw(self._image)
        return True

    def end_frame(self):
        self._frame_time = (time.time() - self._frame_start) * 1000
        self._frame_count += 1

    def clear(self, r: int = 0, g: int = 0, b: int = 0, a: int = 255):
        if self._image:
            self._image = _PIL[0].new("RGBA", (self._width, self._height), (r, g, b, a))
            self._draw = _PIL[1].Draw(self._image)

    def draw_rect(self, x: int, y: int, w: int, h: int,
                   r: int, g: int, b: int, a: int = 255):
        if self._draw:
            self._draw.rectangle([x, y, x + w, y + h], fill=(r, g, b, a))

    def draw_rect_outline(self, x: int, y: int, w: int, h: int,
                           r: int, g: int, b: int, a: int = 255, thickness: int = 1):
        if self._draw:
            for i in range(thickness):
                self._draw.rectangle(
                    [x + i, y + i, x + w - i, y + h - i],
                    outline=(r, g, b, a),
                )

    def draw_text(self, x: int, y: int, text: str,
                   r: int = 255, g: int = 255, b: int = 255, size: int = 14,
                   font: str = ""):
        if self._draw:
            try:
                ImageFont = _PIL[2]
                f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
            except Exception:
                f = _PIL[2].load_default()
            self._draw.text((x, y), text, fill=(r, g, b, 255), font=f)

    def draw_line(self, x1: int, y1: int, x2: int, y2: int,
                   r: int, g: int, b: int, a: int = 255, thickness: int = 1):
        if self._draw:
            self._draw.line([x1, y1, x2, y2], fill=(r, g, b, a), width=thickness)

    def draw_circle(self, cx: int, cy: int, radius: int,
                     r: int, g: int, b: int, a: int = 255):
        if self._draw:
            self._draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                fill=(r, g, b, a),
            )

    def get_surface(self) -> SurfaceBuffer:
        if self._image is None:
            return SurfaceBuffer()
        data = self._image.tobytes()
        return SurfaceBuffer(
            width=self._width,
            height=self._height,
            data=data,
            pixel_format=PixelFormat.RGBA,
            stride=self._width * 4,
            timestamp=time.time(),
        )

    def present(self, buffer: SurfaceBuffer):
        # In PIL backend, present is a no-op (rendering happens in-place)
        pass

    def save_frame(self, path: str) -> bool:
        """Save the current frame to a file."""
        if self._image:
            self._image.save(path)
            return True
        return False

    def poll_input(self) -> List[InputEvent]:
        return []

    def get_outputs(self) -> List[DisplayOutput]:
        return self._outputs

    def set_mode(self, output_id: int, width: int, height: int, refresh: float = 60.0):
        for out in self._outputs:
            if out.output_id == output_id:
                out.width = width
                out.height = height
                out.refresh_rate = refresh
                break

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            hardware_acceleration=False,
            vulkan=False,
            egl=False,
            gbm=False,
            drm=False,
            wayland=False,
            opengl=False,
            max_texture_size=4096,
            multi_monitor=True,
            hdr=False,
            vsync=True,
            opacity=True,
            shadows=False,
            blur=False,
            animations=True,
        )

    def info(self) -> Dict[str, Any]:
        return {
            "backend": "linux",
            "renderer": "PIL/Software",
            "version": "1.0.0",
            "width": self._width,
            "height": self._height,
            "frame_time_ms": self._frame_time,
            "frame_count": self._frame_count,
        }

    def clipboard_get(self) -> str:
        # Try xclip/xsel
        try:
            import subprocess
            result = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                                    capture_output=True, text=True, timeout=1)
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass
        return self._clipboard_text

    def clipboard_set(self, text: str):
        self._clipboard_text = text
        try:
            import subprocess
            proc = subprocess.Popen(["xclip", "-selection", "clipboard"],
                                    stdin=subprocess.PIPE)
            proc.communicate(text.encode())
        except Exception:
            pass

    def set_cursor(self, cursor_type: str = "default"):
        self._cursor_type = cursor_type

    def get_cursor_position(self) -> Tuple[float, float]:
        return (self._cursor_x, self._cursor_y)

    def create_window(self, title: str, width: int, height: int) -> Any:
        self._window_counter += 1
        handle = self._window_counter
        self._windows[handle] = {
            "title": title,
            "width": width,
            "height": height,
            "visible": True,
        }
        return handle

    def destroy_window(self, handle: Any):
        self._windows.pop(handle, None)

    def set_window_title(self, handle: Any, title: str):
        if handle in self._windows:
            self._windows[handle]["title"] = title

    def set_window_size(self, handle: Any, width: int, height: int):
        if handle in self._windows:
            self._windows[handle]["width"] = width
            self._windows[handle]["height"] = height

    def set_vsync(self, enabled: bool):
        self._vsync = enabled

    def get_frame_time_ms(self) -> float:
        return self._frame_time

    def screenshot(self) -> Optional[SurfaceBuffer]:
        return self.get_surface()
