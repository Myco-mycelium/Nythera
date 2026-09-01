"""Python FFI bindings for the Nyrqis DRM atomic modesetting crate.

Provides direct scanout of GPU buffers to display via DRM/KMS.
This is the hardware-accelerated path for Wayland display output;
the software path uses ``wl_shm`` buffers.

References:
    - ADR-0026 Phase 3: GPU acceleration
    - ADR-0010: Vulkan as native graphics API
    - DRM API: https://docs.kernel.org/gpu/drm.html
"""

import ctypes
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------

_DRM_STUB = True  # will be False if the real crate is loaded
_lib = None

# Find the DRM crate .so
_CANDIDATE_PATHS = [
    os.environ.get("NYRQIS_DRM_LIB", ""),
    os.path.join(os.path.dirname(__file__), "..", "rust", "drm", "target", "release", "libnyrqis_drm.so"),
    os.path.join(os.path.dirname(__file__), "..", "rust", "drm", "target", "debug", "libnyrqis_drm.so"),
]

def _load_lib():
    global _lib, _DRM_STUB
    if _lib is not None:
        return _lib
    for p in _CANDIDATE_PATHS:
        if p and os.path.isfile(p):
            try:
                _lib = ctypes.CDLL(p)
                _DRM_STUB = False
                logger.info("Loaded DRM crate from %s", p)
                return _lib
            except OSError as e:
                logger.warning("Failed to load %s: %s", p, e)
    logger.info("DRM crate not available — using stub mode")
    return None


def is_available() -> bool:
    """Check if the real DRM crate is loaded."""
    _load_lib()
    return not _DRM_STUB


# ---------------------------------------------------------------------------
# ctypes structs
# ---------------------------------------------------------------------------

class DrmConnectorInfo(ctypes.Structure):
    """DRM connector information."""
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("refresh", ctypes.c_uint32),  # mHz
        ("status", ctypes.c_uint32),
    ]


# ---------------------------------------------------------------------------
# FFI wrappers
# ---------------------------------------------------------------------------

def drm_version() -> int:
    """Return the ABI version of the DRM crate."""
    lib = _load_lib()
    if lib is None:
        return 0
    return lib.nyrqis_drm_version()


def open_device(device_path: Optional[str] = None) -> int:
    """Open a DRM device.

    Parameters
    ----------
    device_path : str, optional
        Path to the DRM device (e.g. ``/dev/dri/card0``).
        If None, uses the default.

    Returns
    -------
    int
        Device ID (0-based) on success, -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    if device_path is not None:
        path_bytes = device_path.encode("utf-8")
        return lib.nyrqis_drm_open_device(path_bytes, len(path_bytes))
    return lib.nyrqis_drm_open_device(None, 0)


def enumerate_connectors(device_id: int) -> int:
    """Enumerate connectors on a device.

    Returns the number of connectors found, or -1 on error.
    """
    lib = _load_lib()
    if lib is None:
        return -1
    return lib.nyrqis_drm_enumerate_connectors(device_id)


def get_connector_info(connector_id: int) -> Optional[DrmConnectorInfo]:
    """Get connector info.

    Returns a DrmConnectorInfo struct, or None on error.
    """
    lib = _load_lib()
    if lib is None:
        return None
    info = DrmConnectorInfo()
    result = lib.nyrqis_drm_get_connector_info(
        connector_id,
        ctypes.byref(info.width),
        ctypes.byref(info.height),
        ctypes.byref(info.refresh),
        ctypes.byref(info.status),
    )
    if result < 0:
        return None
    return info


def atomic_commit(device_id: int, connector_id: int, crtc_id: int, fb_id: int) -> bool:
    """Perform an atomic modesetting commit.

    Returns True on success, False on error.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_drm_atomic_commit(device_id, connector_id, crtc_id, fb_id) == 0


def close_device(device_id: int) -> bool:
    """Close a DRM device.

    Returns True on success, False on error.
    """
    lib = _load_lib()
    if lib is None:
        return False
    return lib.nyrqis_drm_close_device(device_id) == 0


def last_error() -> str:
    """Return the last error message from the DRM crate."""
    lib = _load_lib()
    if lib is None:
        return ""
    buf = ctypes.create_string_buffer(256)
    lib.nyrqis_drm_last_error(buf, 256)
    return buf.value.decode("utf-8", errors="replace")
