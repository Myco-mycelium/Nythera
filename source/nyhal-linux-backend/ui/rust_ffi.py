"""
Nyrqis OS — Rust FFI Wrapper

Loads and wraps all Nyrqis Rust shared libraries via ctypes:
- libnyrqis_compositor.so — Wayland compositor
- libnyrqis_drm.so — DRM display modesetting
- libnyrqis_gbm.so — GBM buffer management
- libnyrqis_egl.so — EGL rendering context
- libnyrqis_vulkan.so — Vulkan rendering

All crates follow ABI version 0.1.0.

Usage:
    from ui.rust_ffi import RustCompositor, RustDRM, RustGBM, RustEGL, RustVulkan

    comp = RustCompositor()
    if comp.available:
        comp.start()
        sid = comp.create_surface(0, 1920, 1080)
        comp.commit_surface(sid)
        comp.stop()
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Shared library search paths
# ---------------------------------------------------------------------------
_RUST_BASE = os.path.join(_HERE, "..", "rust")
_SEARCH_DIRS = [
    os.path.join(_RUST_BASE, "compositor", "target", "release"),
    os.path.join(_RUST_BASE, "compositor", "target", "debug"),
    os.path.join(_RUST_BASE, "target", "release"),
    os.path.join(_RUST_BASE, "target", "debug"),
    "/usr/local/lib",
    "/usr/lib",
]


def _find_lib(name: str) -> Optional[str]:
    """Search for a shared library."""
    # Check LD_LIBRARY_PATH
    for path in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
        full = os.path.join(path, name)
        if os.path.isfile(full):
            return full

    # Check standard paths
    for base in _SEARCH_DIRS:
        full = os.path.join(base, name)
        if os.path.isfile(full):
            return full

    return None


def _load_lib(name: str) -> Optional[ctypes.CDLL]:
    """Load a shared library by name."""
    lib_path = _find_lib(name)
    if lib_path is None:
        return None
    try:
        return ctypes.CDLL(lib_path)
    except OSError:
        return None


def _check_abi(lib: ctypes.CDLL, min_version: int = 0x0000_0100) -> bool:
    """Check ABI version of a loaded library."""
    if not hasattr(lib, "nyrqis_compositor_version") and \
       not hasattr(lib, "nyrqis_drm_version") and \
       not hasattr(lib, "nyrqis_gbm_version") and \
       not hasattr(lib, "nyrqis_egl_version") and \
       not hasattr(lib, "nyrqis_vulkan_version"):
        return True  # No version check available, assume OK

    for attr in ["nyrqis_compositor_version", "nyrqis_drm_version",
                 "nyrqis_gbm_version", "nyrqis_egl_version",
                 "nyrqis_vulkan_version"]:
        if hasattr(lib, attr):
            try:
                func = getattr(lib, attr)
                func.restype = ctypes.c_uint32
                version = func()
                return version >= min_version
            except Exception:
                continue
    return True


# ---------------------------------------------------------------------------
# Compositor FFI
# ---------------------------------------------------------------------------

class RustCompositor:
    """Python wrapper for the Nyrqis Rust compositor FFI."""

    def __init__(self):
        self._lib = _load_lib("libnyrqis_compositor.so")
        self._handle = None
        self._initialized = False
        self._started = False

    @property
    def available(self) -> bool:
        return self._lib is not None

    @property
    def started(self) -> bool:
        return self._started

    def version(self) -> int:
        if not self.available:
            return 0
        self._lib.nyrqis_compositor_version.restype = ctypes.c_uint32
        return self._lib.nyrqis_compositor_version()

    def start(self) -> bool:
        if not self.available:
            return False
        # If already running, stop first for clean state
        if self._started or self.is_running():
            self.stop()
        self._lib.nyrqis_compositor_start.restype = ctypes.c_int
        result = self._lib.nyrqis_compositor_start()
        self._started = result == 0
        return self._started

    def stop(self) -> bool:
        if not self.available or not self._started:
            return True
        self._lib.nyrqis_compositor_stop.restype = ctypes.c_int
        result = self._lib.nyrqis_compositor_stop()
        self._started = result != 0
        return result == 0

    def is_running(self) -> bool:
        if not self.available:
            return False
        self._lib.nyrqis_compositor_is_running.restype = ctypes.c_int
        return self._lib.nyrqis_compositor_is_running() == 1

    def add_output(self, width: int, height: int, name: str = "") -> int:
        if not self.available:
            return -1
        self._lib.nyrqis_compositor_add_output.restype = ctypes.c_int
        name_bytes = name.encode("utf-8") if name else None
        name_ptr = ctypes.c_char_p(name_bytes) if name_bytes else None
        name_len = len(name_bytes) if name_bytes else 0
        return self._lib.nyrqis_compositor_add_output(
            width, height, name_ptr, name_len
        )

    def create_surface(self, client_id: int, width: int, height: int) -> int:
        if not self.available:
            return -1
        self._lib.nyrqis_compositor_create_surface.restype = ctypes.c_int
        self._lib.nyrqis_compositor_create_surface.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.c_int
        ]
        return self._lib.nyrqis_compositor_create_surface(
            client_id, width, height
        )

    def destroy_surface(self, surface_id: int) -> bool:
        if not self.available:
            return False
        self._lib.nyrqis_compositor_destroy_surface.restype = ctypes.c_int
        self._lib.nyrqis_compositor_destroy_surface.argtypes = [ctypes.c_int]
        return self._lib.nyrqis_compositor_destroy_surface(surface_id) == 0

    def commit_surface(self, surface_id: int) -> bool:
        if not self.available:
            return False
        self._lib.nyrqis_compositor_commit_surface.restype = ctypes.c_int
        self._lib.nyrqis_compositor_commit_surface.argtypes = [ctypes.c_int]
        return self._lib.nyrqis_compositor_commit_surface(surface_id) == 0

    def surface_count(self) -> int:
        if not self.available:
            return 0
        self._lib.nyrqis_compositor_surface_count.restype = ctypes.c_int
        return self._lib.nyrqis_compositor_surface_count()

    def output_count(self) -> int:
        if not self.available:
            return 0
        self._lib.nyrqis_compositor_output_count.restype = ctypes.c_int
        return self._lib.nyrqis_compositor_output_count()

    def last_error(self) -> str:
        if not self.available:
            return ""
        buf = ctypes.create_string_buffer(256)
        self._lib.nyrqis_compositor_last_error.restype = ctypes.c_int
        self._lib.nyrqis_compositor_last_error.argtypes = [
            ctypes.c_char_p, ctypes.c_int
        ]
        n = self._lib.nyrqis_compositor_last_error(buf, 256)
        return buf.value[:n].decode("utf-8", errors="replace") if n > 0 else ""


# ---------------------------------------------------------------------------
# DRM FFI
# ---------------------------------------------------------------------------

class RustDRM:
    """Python wrapper for the Nyrqis Rust DRM FFI."""

    def __init__(self):
        self._lib = _load_lib("libnyrqis_drm.so")
        self._device_id = -1

    @property
    def available(self) -> bool:
        return self._lib is not None

    def version(self) -> int:
        if not self.available:
            return 0
        self._lib.nyrqis_drm_version.restype = ctypes.c_uint32
        return self._lib.nyrqis_drm_version()

    def open_device(self, path: str = "/dev/dri/card0") -> int:
        if not self.available:
            return -1
        self._lib.nyrqis_drm_open_device.restype = ctypes.c_int
        self._lib.nyrqis_drm_open_device.argtypes = [ctypes.c_char_p]
        self._device_id = self._lib.nyrqis_drm_open_device(path.encode())
        return self._device_id

    def close_device(self) -> bool:
        if not self.available or self._device_id < 0:
            return True
        self._lib.nyrqis_drm_close_device.restype = ctypes.c_int
        result = self._lib.nyrqis_drm_close_device(self._device_id)
        self._device_id = -1
        return result == 0

    def enumerate_connectors(self) -> int:
        if not self.available or self._device_id < 0:
            return 0
        self._lib.nyrqis_drm_enumerate_connectors.restype = ctypes.c_int
        return self._lib.nyrqis_drm_enumerate_connectors(self._device_id)

    def atomic_commit(self) -> bool:
        if not self.available or self._device_id < 0:
            return False
        self._lib.nyrqis_drm_atomic_commit.restype = ctypes.c_int
        self._lib.nyrqis_drm_atomic_commit.argtypes = [ctypes.c_int]
        return self._lib.nyrqis_drm_atomic_commit(self._device_id) == 0

    def last_error(self) -> str:
        if not self.available:
            return ""
        buf = ctypes.create_string_buffer(256)
        self._lib.nyrqis_drm_last_error.restype = ctypes.c_int
        n = self._lib.nyrqis_drm_last_error(buf, 256)
        return buf.value[:n].decode("utf-8", errors="replace") if n > 0 else ""


# ---------------------------------------------------------------------------
# GBM FFI
# ---------------------------------------------------------------------------

class RustGBM:
    """Python wrapper for the Nyrqis Rust GBM FFI."""

    def __init__(self):
        self._lib = _load_lib("libnyrqis_gbm.so")
        self._device_id = -1
        self._surfaces: Dict[int, int] = {}  # surface_id -> native_id

    @property
    def available(self) -> bool:
        return self._lib is not None

    def version(self) -> int:
        if not self.available:
            return 0
        self._lib.nyrqis_gbm_version.restype = ctypes.c_uint32
        return self._lib.nyrqis_gbm_version()

    def open_device(self, path: str = "/dev/dri/card0") -> int:
        if not self.available:
            return -1
        self._lib.nyrqis_gbm_open_device.restype = ctypes.c_int
        self._lib.nyrqis_gbm_open_device.argtypes = [ctypes.c_char_p]
        self._device_id = self._lib.nyrqis_gbm_open_device(path.encode())
        return self._device_id

    def create_surface(self, width: int, height: int) -> int:
        if not self.available or self._device_id < 0:
            return -1
        self._lib.nyrqis_gbm_create_surface.restype = ctypes.c_int
        self._lib.nyrqis_gbm_create_surface.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int
        ]
        sid = self._lib.nyrqis_gbm_create_surface(self._device_id, width, height)
        if sid >= 0:
            self._surfaces[sid] = sid
        return sid

    def lock_buffer(self, surface_id: int) -> int:
        if not self.available:
            return -1
        self._lib.nyrqis_gbm_lock_buffer.restype = ctypes.c_int
        self._lib.nyrqis_gbm_lock_buffer.argtypes = [ctypes.c_int]
        return self._lib.nyrqis_gbm_lock_buffer(surface_id)

    def release_buffer(self, buffer_id: int) -> bool:
        if not self.available:
            return False
        self._lib.nyrqis_gbm_release_buffer.restype = ctypes.c_int
        self._lib.nyrqis_gbm_release_buffer.argtypes = [ctypes.c_int]
        return self._lib.nyrqis_gbm_release_buffer(buffer_id) == 0

    def destroy_surface(self, surface_id: int) -> bool:
        if not self.available:
            return False
        self._lib.nyrqis_gbm_destroy_surface.restype = ctypes.c_int
        self._lib.nyrqis_gbm_destroy_surface.argtypes = [ctypes.c_int]
        result = self._lib.nyrqis_gbm_destroy_surface(surface_id) == 0
        self._surfaces.pop(surface_id, None)
        return result

    def close_device(self) -> bool:
        if not self.available or self._device_id < 0:
            return True
        self._lib.nyrqis_gbm_close_device.restype = ctypes.c_int
        result = self._lib.nyrqis_gbm_close_device(self._device_id)
        self._device_id = -1
        return result == 0

    def last_error(self) -> str:
        if not self.available:
            return ""
        buf = ctypes.create_string_buffer(256)
        self._lib.nyrqis_gbm_last_error.restype = ctypes.c_int
        n = self._lib.nyrqis_gbm_last_error(buf, 256)
        return buf.value[:n].decode("utf-8", errors="replace") if n > 0 else ""


# ---------------------------------------------------------------------------
# EGL FFI
# ---------------------------------------------------------------------------

class RustEGL:
    """Python wrapper for the Nyrqis Rust EGL FFI."""

    def __init__(self):
        self._lib = _load_lib("libnyrqis_egl.so")
        self._display_id = -1
        self._context_id = -1
        self._surface_id = -1

    @property
    def available(self) -> bool:
        return self._lib is not None

    def version(self) -> int:
        if not self.available:
            return 0
        self._lib.nyrqis_egl_version.restype = ctypes.c_uint32
        return self._lib.nyrqis_egl_version()

    def initialize(self, native_display: int = 0) -> bool:
        if not self.available:
            return False
        self._lib.nyrqis_egl_get_display.restype = ctypes.c_int
        self._lib.nyrqis_egl_get_display.argtypes = [ctypes.c_uint64]
        self._display_id = self._lib.nyrqis_egl_get_display(native_display)
        if self._display_id < 0:
            return False
        self._lib.nyrqis_egl_initialize.restype = ctypes.c_uint32
        self._lib.nyrqis_egl_initialize.argtypes = [ctypes.c_int]
        return self._lib.nyrqis_egl_initialize(self._display_id) == 0

    def create_window_surface(self, width: int, height: int) -> int:
        if not self.available or self._display_id < 0:
            return -1
        self._lib.nyrqis_egl_create_window_surface.restype = ctypes.c_int
        self._lib.nyrqis_egl_create_window_surface.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int
        ]
        self._surface_id = self._lib.nyrqis_egl_create_window_surface(
            self._display_id, width, height
        )
        return self._surface_id

    def create_context(self) -> int:
        if not self.available or self._display_id < 0:
            return -1
        self._lib.nyrqis_egl_create_context.restype = ctypes.c_int
        self._lib.nyrqis_egl_create_context.argtypes = [ctypes.c_int]
        self._context_id = self._lib.nyrqis_egl_create_context(self._display_id)
        return self._context_id

    def make_current(self) -> bool:
        if not self.available or self._display_id < 0:
            return False
        self._lib.nyrqis_egl_make_current.restype = ctypes.c_int
        self._lib.nyrqis_egl_make_current.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int
        ]
        return self._lib.nyrqis_egl_make_current(
            self._display_id, self._surface_id, self._context_id
        ) == 0

    def swap_buffers(self) -> bool:
        if not self.available or self._display_id < 0:
            return False
        self._lib.nyrqis_egl_swap_buffers.restype = ctypes.c_int
        self._lib.nyrqis_egl_swap_buffers.argtypes = [ctypes.c_int]
        return self._lib.nyrqis_egl_swap_buffers(self._display_id) == 0

    def destroy_surface(self) -> bool:
        if not self.available or self._surface_id < 0:
            return True
        self._lib.nyrqis_egl_destroy_surface.restype = ctypes.c_int
        self._lib.nyrqis_egl_destroy_surface.argtypes = [ctypes.c_int]
        result = self._lib.nyrqis_egl_destroy_surface(self._surface_id) == 0
        self._surface_id = -1
        return result

    def last_error(self) -> str:
        if not self.available:
            return ""
        buf = ctypes.create_string_buffer(256)
        self._lib.nyrqis_egl_last_error.restype = ctypes.c_int
        n = self._lib.nyrqis_egl_last_error(buf, 256)
        return buf.value[:n].decode("utf-8", errors="replace") if n > 0 else ""


# ---------------------------------------------------------------------------
# Vulkan FFI
# ---------------------------------------------------------------------------

class RustVulkan:
    """Python wrapper for the Nyrqis Rust Vulkan FFI."""

    def __init__(self):
        self._lib = _load_lib("libnyrqis_vulkan.so")
        self._instance_id = -1
        self._device_id = -1
        self._swapchain_id = -1

    @property
    def available(self) -> bool:
        return self._lib is not None

    def version(self) -> int:
        if not self.available:
            return 0
        self._lib.nyrqis_vulkan_version.restype = ctypes.c_uint32
        return self._lib.nyrqis_vulkan_version()

    def create_instance(self) -> int:
        if not self.available:
            return -1
        self._lib.nyrqis_vulkan_create_instance.restype = ctypes.c_int
        self._instance_id = self._lib.nyrqis_vulkan_create_instance()
        return self._instance_id

    def create_device(self) -> int:
        if not self.available or self._instance_id < 0:
            return -1
        self._lib.nyrqis_vulkan_create_device.restype = ctypes.c_int
        self._lib.nyrqis_vulkan_create_device.argtypes = [ctypes.c_int]
        self._device_id = self._lib.nyrqis_vulkan_create_device(self._instance_id)
        return self._device_id

    def create_swapchain(self, width: int, height: int) -> int:
        if not self.available or self._device_id < 0:
            return -1
        self._lib.nyrqis_vulkan_create_swapchain.restype = ctypes.c_int
        self._lib.nyrqis_vulkan_create_swapchain.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int
        ]
        self._swapchain_id = self._lib.nyrqis_vulkan_create_swapchain(
            self._device_id, width, height
        )
        return self._swapchain_id

    def acquire_next_image(self) -> int:
        if not self.available or self._swapchain_id < 0:
            return -1
        self._lib.nyrqis_vulkan_acquire_next_image.restype = ctypes.c_int
        self._lib.nyrqis_vulkan_acquire_next_image.argtypes = [ctypes.c_int]
        return self._lib.nyrqis_vulkan_acquire_next_image(self._swapchain_id)

    def destroy_swapchain(self) -> bool:
        if not self.available or self._swapchain_id < 0:
            return True
        self._lib.nyrqis_vulkan_destroy_swapchain.restype = ctypes.c_int
        self._lib.nyrqis_vulkan_destroy_swapchain.argtypes = [ctypes.c_int]
        result = self._lib.nyrqis_vulkan_destroy_swapchain(self._swapchain_id) == 0
        self._swapchain_id = -1
        return result

    def destroy_device(self) -> bool:
        if not self.available or self._device_id < 0:
            return True
        self._lib.nyrqis_vulkan_destroy_device.restype = ctypes.c_int
        self._lib.nyrqis_vulkan_destroy_device.argtypes = [ctypes.c_int]
        result = self._lib.nyrqis_vulkan_destroy_device(self._device_id) == 0
        self._device_id = -1
        return result

    def destroy_instance(self) -> bool:
        if not self.available or self._instance_id < 0:
            return True
        self._lib.nyrqis_vulkan_destroy_instance.restype = ctypes.c_int
        self._lib.nyrqis_vulkan_destroy_instance.argtypes = [ctypes.c_int]
        result = self._lib.nyrqis_vulkan_destroy_instance(self._instance_id) == 0
        self._instance_id = -1
        return result

    def last_error(self) -> str:
        if not self.available:
            return ""
        buf = ctypes.create_string_buffer(256)
        self._lib.nyrqis_vulkan_last_error.restype = ctypes.c_int
        n = self._lib.nyrqis_vulkan_last_error(buf, 256)
        return buf.value[:n].decode("utf-8", errors="replace") if n > 0 else ""


# ---------------------------------------------------------------------------
# Unified Rust backend
# ---------------------------------------------------------------------------

class RustBackend:
    """Unified Nyrqis Rust backend — loads all crates and provides access."""

    def __init__(self):
        self.compositor = RustCompositor()
        self.drm = RustDRM()
        self.gbm = RustGBM()
        self.egl = RustEGL()
        self.vulkan = RustVulkan()

    @property
    def available(self) -> bool:
        """True if any Rust crate is available."""
        return any([
            self.compositor.available,
            self.drm.available,
            self.gbm.available,
            self.egl.available,
            self.vulkan.available,
        ])

    def info(self) -> Dict[str, Any]:
        """Get status of all Rust crates."""
        return {
            "compositor": {
                "available": self.compositor.available,
                "version": self.compositor.version() if self.compositor.available else 0,
                "started": self.compositor.started,
            },
            "drm": {
                "available": self.drm.available,
                "version": self.drm.version() if self.drm.available else 0,
            },
            "gbm": {
                "available": self.gbm.available,
                "version": self.gbm.version() if self.gbm.available else 0,
            },
            "egl": {
                "available": self.egl.available,
                "version": self.egl.version() if self.egl.available else 0,
            },
            "vulkan": {
                "available": self.vulkan.available,
                "version": self.vulkan.version() if self.vulkan.available else 0,
            },
        }

    def initialize_all(self) -> bool:
        """Initialize the full Rust rendering pipeline."""
        # Start compositor
        if self.compositor.available:
            if not self.compositor.start():
                return False
            self.compositor.add_output(1920, 1080, "default")

        # Open DRM device
        if self.drm.available:
            self.drm.open_device("/dev/dri/card0")

        # Open GBM device and create surface
        if self.gbm.available:
            self.gbm.open_device("/dev/dri/card0")
            self.gbm.create_surface(1920, 1080)

        # Initialize EGL
        if self.egl.available:
            self.egl.initialize()
            self.egl.create_window_surface(1920, 1080)
            self.egl.create_context()
            self.egl.make_current()

        # Initialize Vulkan
        if self.vulkan.available:
            self.vulkan.create_instance()
            self.vulkan.create_device()
            self.vulkan.create_swapchain(1920, 1080)

        return True

    def shutdown_all(self):
        """Shutdown all Rust backends."""
        self.vulkan.destroy_swapchain()
        self.vulkan.destroy_device()
        self.vulkan.destroy_instance()
        self.egl.destroy_surface()
        self.gbm.close_device()
        self.drm.close_device()
        if self.compositor.started:
            self.compositor.stop()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_rust_backend: Optional[RustBackend] = None


def get_rust_backend() -> RustBackend:
    """Get or create the Rust backend singleton."""
    global _rust_backend
    if _rust_backend is None:
        _rust_backend = RustBackend()
    return _rust_backend
