"""Nyrqis Backend — Rust compositor backend via ctypes FFI.

This backend wraps the Nyrqis Rust compositor (nyrqis_compositor.cdylib)
and provides the same Backend interface as the Linux backend.

When the Rust compositor is not available, this backend gracefully
falls back to indicating unavailability so the shell can use LinuxBackend.

The FFI surface follows ABI version 0.1.0.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from .backend import (
    Backend, BackendCapabilities, BackendType, DisplayOutput,
    InputEvent, PixelFormat, SurfaceBuffer,
)

# ---------------------------------------------------------------------------
# Shared library search paths
# ---------------------------------------------------------------------------
_SEARCH_PATHS = [
    os.path.join(os.path.dirname(__file__), "..", "rust", "compositor", "target", "release"),
    os.path.join(os.path.dirname(__file__), "..", "rust", "compositor", "target", "debug"),
    os.path.join(os.path.dirname(__file__), "..", "rust", "target", "release"),
    os.path.join(os.path.dirname(__file__), "..", "rust", "target", "debug"),
    "/usr/local/lib",
    "/usr/lib",
]

_LIB_NAME = "libnyrqis_compositor.so"
_loaded_lib = None
_lib_load_attempted = False


def _find_library() -> Optional[str]:
    """Search for the Nyrqis compositor shared library."""
    # Check LD_LIBRARY_PATH
    for path in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
        full = os.path.join(path, _LIB_NAME)
        if os.path.isfile(full):
            return full

    # Check standard paths
    for base in _SEARCH_PATHS:
        full = os.path.join(base, _LIB_NAME)
        if os.path.isfile(full):
            return full

    # Try pkg-config or system paths
    try:
        import subprocess
        result = subprocess.run(
            ["pkg-config", "--libs", "nyrqis-compositor"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            # Parse -L and -l flags
            pass
    except Exception:
        pass

    return None


def _load_library():
    """Load the Nyrqis compositor shared library."""
    global _loaded_lib, _lib_load_attempted

    if _lib_load_attempted:
        return _loaded_lib

    _lib_load_attempted = True

    lib_path = _find_library()
    if lib_path is None:
        return None

    try:
        _loaded_lib = ctypes.CDLL(lib_path)
        # Verify ABI version
        if hasattr(_loaded_lib, "nyrqis_abi_version"):
            _loaded_lib.nyrqis_abi_version.restype = ctypes.c_uint32
            version = _loaded_lib.nyrqis_abi_version()
            # ABI 0x0000_0100 = 0.1.0
            if version < 0x0000_0100:
                _loaded_lib = None
                return None
        return _loaded_lib
    except OSError:
        _loaded_lib = None
        return None


# ---------------------------------------------------------------------------
# FFI types matching Rust C ABI
# ---------------------------------------------------------------------------

class NyrqisCompositor:
    """Python wrapper for the Nyrqis Rust compositor FFI."""

    def __init__(self):
        self._lib = _load_library()
        self._handle = None
        self._initialized = False

    @property
    def available(self) -> bool:
        return self._lib is not None

    def initialize(self, width: int = 1920, height: int = 1080) -> bool:
        if not self.available:
            return False
        try:
            # nyrqis_compositor_init(width, height) -> handle
            if hasattr(self._lib, "nyrqis_compositor_init"):
                self._lib.nyrqis_compositor_init.restype = ctypes.c_void_p
                self._lib.nyrqis_compositor_init.argtypes = [ctypes.c_int, ctypes.c_int]
                self._handle = self._lib.nyrqis_compositor_init(width, height)
                self._initialized = self._handle is not None
                return self._initialized
            return False
        except Exception:
            return False

    def shutdown(self):
        if self._lib and self._handle and hasattr(self._lib, "nyrqis_compositor_destroy"):
            try:
                self._lib.nyrqis_compositor_destroy(self._handle)
            except Exception:
                pass
        self._handle = None
        self._initialized = False

    def get_renderer_info(self) -> str:
        if self._lib and hasattr(self._lib, "nyrqis_renderer_info"):
            try:
                self._lib.nyrqis_renderer_info.restype = ctypes.c_char_p
                result = self._lib.nyrqis_renderer_info()
                return result.decode() if result else "Unknown"
            except Exception:
                pass
        return "Unavailable"

    def get_output_count(self) -> int:
        if self._lib and self._handle and hasattr(self._lib, "nyrqis_output_count"):
            try:
                self._lib.nyrqis_output_count.restype = ctypes.c_int
                self._lib.nyrqis_output_count.argtypes = [ctypes.c_void_p]
                return self._lib.nyrqis_output_count(self._handle)
            except Exception:
                pass
        return 0

    def create_surface(self, client_id: int, width: int, height: int) -> int:
        if self._lib and self._handle and hasattr(self._lib, "nyrqis_surface_create"):
            try:
                self._lib.nyrqis_surface_create.restype = ctypes.c_int
                self._lib.nyrqis_surface_create.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int
                ]
                return self._lib.nyrqis_surface_create(self._handle, client_id, width, height)
            except Exception:
                pass
        return -1

    def destroy_surface(self, surface_id: int):
        if self._lib and self._handle and hasattr(self._lib, "nyrqis_surface_destroy"):
            try:
                self._lib.nyrqis_surface_destroy.argtypes = [ctypes.c_void_p, ctypes.c_int]
                self._lib.nyrqis_surface_destroy(self._handle, surface_id)
            except Exception:
                pass

    def begin_frame(self) -> bool:
        if self._lib and self._handle and hasattr(self._lib, "nyrqis_frame_begin"):
            try:
                self._lib.nyrqis_frame_begin.restype = ctypes.c_int
                self._lib.nyrqis_frame_begin.argtypes = [ctypes.c_void_p]
                return self._lib.nyrqis_frame_begin(self._handle) == 0
            except Exception:
                pass
        return False

    def end_frame(self):
        if self._lib and self._handle and hasattr(self._lib, "nyrqis_frame_end"):
            try:
                self._lib.nyrqis_frame_end.argtypes = [ctypes.c_void_p]
                self._lib.nyrqis_frame_end(self._handle)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------

class NyrqisBackend(Backend):
    """Nyrqis Rust compositor backend.

    Wraps the nyrqis_compositor shared library via ctypes.
    Provides hardware-accelerated rendering when available.
    """

    def __init__(self):
        self._compositor = NyrqisCompositor()
        self._initialized = False
        self._width: int = 1920
        self._height: int = 1080
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
        if not self._compositor.available:
            return False
        self._width = width
        self._height = height
        ok = self._compositor.initialize(width, height)
        if ok:
            self._initialized = True
            self._outputs = [
                DisplayOutput(0, "eDP-1", width, height, 60.0, 1.0, 0, 0, True),
            ]
        return ok

    def shutdown(self):
        self._compositor.shutdown()
        self._initialized = False

    def is_available(self) -> bool:
        return _load_library() is not None

    def begin_frame(self) -> bool:
        if not self._initialized:
            return False
        self._frame_start = time.time()
        return self._compositor.begin_frame()

    def end_frame(self):
        self._compositor.end_frame()
        self._frame_time = (time.time() - self._frame_start) * 1000
        self._frame_count += 1

    def clear(self, r: int = 0, g: int = 0, b: int = 0, a: int = 255):
        # Rust compositor handles clearing internally
        pass

    def draw_rect(self, x: int, y: int, w: int, h: int,
                   r: int, g: int, b: int, a: int = 255):
        # Drawing commands go through surface commit in the Rust backend
        pass

    def draw_rect_outline(self, x: int, y: int, w: int, h: int,
                           r: int, g: int, b: int, a: int = 255, thickness: int = 1):
        pass

    def draw_text(self, x: int, y: int, text: str,
                   r: int = 255, g: int = 255, b: int = 255, size: int = 14,
                   font: str = ""):
        pass

    def draw_line(self, x1: int, y1: int, x2: int, y2: int,
                   r: int, g: int, b: int, a: int = 255, thickness: int = 1):
        pass

    def draw_circle(self, cx: int, cy: int, radius: int,
                     r: int, g: int, b: int, a: int = 255):
        pass

    def get_surface(self) -> SurfaceBuffer:
        return SurfaceBuffer(
            width=self._width,
            height=self._height,
            data=b"",
            pixel_format=PixelFormat.RGBA,
            stride=self._width * 4,
            timestamp=time.time(),
        )

    def present(self, buffer: SurfaceBuffer):
        pass

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
            hardware_acceleration=True,
            vulkan=True,
            egl=True,
            gbm=True,
            drm=True,
            wayland=True,
            opengl=True,
            max_texture_size=16384,
            multi_monitor=True,
            hdr=True,
            vsync=True,
            opacity=True,
            shadows=True,
            blur=True,
            animations=True,
        )

    def info(self) -> Dict[str, Any]:
        return {
            "backend": "nyrqis",
            "renderer": self._compositor.get_renderer_info(),
            "version": "0.1.0",
            "abi_version": "0.1.0",
            "width": self._width,
            "height": self._height,
            "frame_time_ms": self._frame_time,
            "frame_count": self._frame_count,
            "output_count": self._compositor.get_output_count(),
        }

    def clipboard_get(self) -> str:
        return self._clipboard_text

    def clipboard_set(self, text: str):
        self._clipboard_text = text

    def set_cursor(self, cursor_type: str = "default"):
        self._cursor_type = cursor_type

    def get_cursor_position(self) -> Tuple[float, float]:
        return (self._cursor_x, self._cursor_y)

    def create_window(self, title: str, width: int, height: int) -> Any:
        self._window_counter += 1
        handle = self._window_counter
        # Create a Wayland surface in the compositor
        surface_id = self._compositor.create_surface(0, width, height)
        self._windows[handle] = {
            "title": title,
            "width": width,
            "height": height,
            "visible": True,
            "surface_id": surface_id,
        }
        return handle

    def destroy_window(self, handle: Any):
        win = self._windows.pop(handle, None)
        if win and "surface_id" in win:
            self._compositor.destroy_surface(win["surface_id"])

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
