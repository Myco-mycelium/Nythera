"""Python FFI bindings for the Nyrqis EGL integration crate.

Provides OpenGL ES rendering via EGL for hardware-accelerated
Wayland display output.  EGL provides the interface between
OpenGL ES and the native windowing system (Wayland/GBM).

References:
    - ADR-0026 Phase 3: GPU acceleration
    - ADR-0010: Vulkan as native graphics API
    - EGL API: https://www.khronos.org/egl/
"""

import ctypes
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------

_EGL_STUB = True  # will be False if the real crate is loaded
_lib = None

# Find the EGL crate .so
_CANDIDATE_PATHS = [
    os.environ.get("NYRQIS_EGL_LIB", ""),
    os.path.join(os.path.dirname(__file__), "..", "rust", "egl", "target", "release", "libnyrqis_egl.so"),
    os.path.join(os.path.dirname(__file__), "..", "rust", "egl", "target", "debug", "libnyrqis_egl.so"),
]

def _load_lib():
    global _lib, _EGL_STUB
    if _lib is not None:
        return _lib
    for p in _CANDIDATE_PATHS:
        if p and os.path.isfile(p):
            try:
                _lib = ctypes.CDLL(p)
                _EGL_STUB = False
                logger.info("Loaded EGL crate from %s", p)
                return _lib
            except OSError as e:
                logger.warning("Failed to load %s: %s", p, e)
    logger.info("EGL crate not available — using stub mode")
    return None


def is_available() -> bool:
    """Check if the real EGL crate is loaded."""
    _load_lib()
    return not _EGL_STUB


# ---------------------------------------------------------------------------
# EGL constants
# ---------------------------------------------------------------------------

EGL_SUCCESS = 0x3000
EGL_FALSE = 0
EGL_TRUE = 1

# ---------------------------------------------------------------------------
# FFI wrappers
# ---------------------------------------------------------------------------

def egl_version() -> int:
    """Return the ABI version of the EGL crate."""
    lib = _load_lib()
    if lib is None:
        return 0
    return lib.nyrqis_egl_version()


def get_display(display_id: int = 0) -> int:
    """Get an EGL display.

    Returns a display ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_egl_get_display(display_id)


def initialize(display_id: int) -> bool:
    """Initialize EGL for a display.

    Returns True on success.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_egl_initialize(display_id) == EGL_TRUE


def choose_config(display_id: int) -> int:
    """Choose an EGL configuration.

    Returns a config ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_egl_choose_config(display_id)


def create_window_surface(display_id: int, width: int, height: int) -> int:
    """Create an EGL window surface.

    Returns a surface ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_egl_create_window_surface(display_id, width, height)


def create_context(display_id: int) -> int:
    """Create an EGL context.

    Returns a context ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_egl_create_context(display_id)


def destroy_surface(surface_id: int) -> bool:
    """Destroy an EGL surface.

    Returns True on success.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_egl_destroy_surface(surface_id) == 0


def destroy_context(context_id: int) -> bool:
    """Destroy an EGL context.

    Returns True on success.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_egl_destroy_context(context_id) == 0


def terminate(display_id: int) -> bool:
    """Terminate an EGL display.

    Returns True on success.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_egl_terminate(display_id) == EGL_TRUE


def last_error() -> str:
    """Return the last error message from the EGL crate."""
    lib = _load_lib()
    if lib is None:
        return ""
    buf = ctypes.create_string_buffer(256)
    lib.nyrqis_egl_last_error(buf, 256)
    return buf.value.decode("utf-8", errors="replace")
