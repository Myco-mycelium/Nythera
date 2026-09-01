"""Python FFI bindings for the Nyrqis GBM buffer allocation crate.

Provides GPU buffer allocation via libgbm for hardware-accelerated
rendering through Wayland.  GBM (Generic Buffer Manager) provides a
vendor-neutral interface for allocating buffers that can be used with
DRM/KMS (direct scanout) and EGL (OpenGL/Vulkan rendering).

References:
    - ADR-0026 Phase 3: GPU acceleration
    - GBM API: https://docs.kernel.org/gpu/gbm.html
"""

import ctypes
import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------

_GBM_STUB = True  # will be False if the real crate is loaded
_lib = None

# Find the GBM crate .so
_CANDIDATE_PATHS = [
    os.environ.get("NYRQIS_GBM_LIB", ""),
    os.path.join(os.path.dirname(__file__), "..", "rust", "gbm", "target", "release", "libnyrqis_gbm.so"),
    os.path.join(os.path.dirname(__file__), "..", "rust", "gbm", "target", "debug", "libnyrqis_gbm.so"),
]

def _load_lib():
    global _lib, _GBM_STUB
    if _lib is not None:
        return _lib
    for p in _CANDIDATE_PATHS:
        if p and os.path.isfile(p):
            try:
                _lib = ctypes.CDLL(p)
                _GBM_STUB = False
                logger.info("Loaded GBM crate from %s", p)
                return _lib
            except OSError as e:
                logger.warning("Failed to load %s: %s", p, e)
    logger.info("GBM crate not available — using stub mode")
    return None


def is_available() -> bool:
    """Check if the real GBM crate is loaded."""
    _load_lib()
    return not _GBM_STUB


# ---------------------------------------------------------------------------
# FFI wrappers
# ---------------------------------------------------------------------------

def gbm_version() -> int:
    """Return the ABI version of the GBM crate."""
    lib = _load_lib()
    if lib is None:
        return 0
    return lib.nyrqis_gbm_version()


def open_device(render_node: str = None) -> int:
    """Open a GBM device from a DRM render node.

    Parameters
    ----------
    render_node : str, optional
        Path to the DRM render node (e.g. ``/dev/dri/renderD128``).
        If None, uses the default.

    Returns
    -------
    int
        Device ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    if render_node is not None:
        path_bytes = render_node.encode("utf-8")
        return lib.nyrqis_gbm_open_device(path_bytes, len(path_bytes))
    return lib.nyrqis_gbm_open_device(None, 0)


def create_surface(device_id: int, width: int, height: int, format: int = 0x34325241) -> int:
    """Create a GBM surface for rendering.

    Parameters
    ----------
    device_id : int
        The device to create the surface on.
    width, height : int
        Surface dimensions in pixels.
    format : int
        GBM pixel format (default: GBM_FORMAT_ARGB8888).

    Returns
    -------
    int
        Surface ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_gbm_create_surface(device_id, width, height, format)


def lock_buffer(surface_id: int) -> int:
    """Lock a GBM surface buffer for CPU access.

    Returns a buffer ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_gbm_lock_buffer(surface_id)


def get_buffer_info(buffer_id: int):
    """Get buffer dimensions and stride.

    Returns a tuple (width, height, stride) or None on error.
    """
    lib = _load_lib()
    if lib is None:
        return None
    w = ctypes.c_int32()
    h = ctypes.c_int32()
    s = ctypes.c_int32()
    result = lib.nyrqis_gbm_get_buffer_info(buffer_id, ctypes.byref(w), ctypes.byref(h), ctypes.byref(s))
    if result < 0:
        return None
    return (w.value, h.value, s.value)


def release_buffer(buffer_id: int) -> bool:
    """Release a buffer.

    Returns True on success.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_gbm_release_buffer(buffer_id) == 0


def destroy_surface(surface_id: int) -> bool:
    """Destroy a surface.

    Returns True on success.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_gbm_destroy_surface(surface_id) == 0


def close_device(device_id: int) -> bool:
    """Close a GBM device.

    Returns True on success.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_gbm_close_device(device_id) == 0


def last_error() -> str:
    """Return the last error message from the GBM crate."""
    lib = _load_lib()
    if lib is None:
        return ""
    buf = ctypes.create_string_buffer(256)
    lib.nyrqis_gbm_last_error(buf, 256)
    return buf.value.decode("utf-8", errors="replace")
