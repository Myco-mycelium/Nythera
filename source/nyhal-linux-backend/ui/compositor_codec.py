"""compositor_codec — Python FFI loader for the Rust Wayland compositor crate.

Loads the ``nyrqis_compositor`` cdylib via ctypes and exposes the
compositor lifecycle, output management, surface management, and input
dispatch as Python-friendly wrappers.

The crate is optional: when the cdylib is absent the module exposes
stub functions that return honest error codes. This matches the
pattern used by ``wayland_codec.py`` and ``gbm_codec.py``.

References:
    - rust/compositor/ (crate: nyrqis-compositor, ABI 0.2.0)
    - ADR-0026: Wayland display-server integration
    - ADR-0020: Implementation languages and the platform boundary
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ABI_VERSION: int = 0x0000_0300  # 0.3.0 — wl_shm pool/buffer objects in the wire loop

# Input event types (mirrors the Rust enum)
INPUT_KEY_PRESS: int = 1
INPUT_KEY_RELEASE: int = 2
INPUT_POINTER_MOTION: int = 3
INPUT_POINTER_BUTTON: int = 4


# ---------------------------------------------------------------------------
# Crate loading
# ---------------------------------------------------------------------------
def _find_cdylib() -> Optional[str]:
    """Locate the ``nyrqis_compositor`` cdylib.

    Search order:
    1. ``$NYRQIS_RUST_LIB`` if set
    2. ``target/release/`` relative to the backend directory
    3. ``LD_LIBRARY_PATH`` / system paths
    """
    # 1. Explicit override
    env_path = os.environ.get("NYRQIS_RUST_LIB")
    if env_path and os.path.isfile(env_path):
        return env_path

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(backend_dir, "rust", "compositor", "target", "release",
                     "libnyrqis_compositor.so"),
        os.path.join(backend_dir, "rust", "compositor", "target", "release",
                     "libnyrqis_compositor.dylib"),
        os.path.join(backend_dir, "rust", "compositor", "target", "release",
                     "nyrqis_compositor.dll"),
    ]

    for path in candidates:
        if os.path.isfile(path):
            return path

    # 3. Let the system search LD_LIBRARY_PATH
    return None


_CDLL: Optional[ctypes.CDLL] = None
_available: Optional[bool] = None


def _load() -> Optional[ctypes.CDLL]:
    """Load and return the cdylib (once), or None if unavailable."""
    global _CDLL, _available
    if _available is not None:
        return _CDLL

    path = _find_cdylib()
    if path is None:
        _available = False
        logger.info("compositor_codec: cdylib not found; stub mode active")
        return None

    try:
        cdll = ctypes.CDLL(path)
        # ABI gate
        cdll.nyrqis_compositor_version.restype = ctypes.c_uint32
        ver = cdll.nyrqis_compositor_version()
        if ver != ABI_VERSION:
            logger.warning(
                "compositor_codec: ABI mismatch — expected 0x%08x, got 0x%08x; "
                "stubs active", ABI_VERSION, ver)
            _available = False
            return None
        _CDLL = cdll
        _available = True
        logger.info("compositor_codec: loaded %s (ABI 0x%08x)", path, ver)
        return cdll
    except OSError as exc:
        logger.warning("compositor_codec: failed to load %s: %s; stub mode active",
                       path, exc)
        _available = False
        return None


def available() -> bool:
    """Return True when the Rust compositor crate is loaded."""
    if _available is None:
        _load()
    return _available is True


# ---------------------------------------------------------------------------
# FFI wrappers
# ---------------------------------------------------------------------------

def version() -> int:
    """Return the crate ABI version, or 0 if the crate is absent."""
    cdll = _load()
    if cdll is None:
        return 0
    return cdll.nyrqis_compositor_version()


def start() -> int:
    """Start the compositor event loop. Returns 0 on success, -1 on error."""
    cdll = _load()
    if cdll is None:
        return -1
    cdll.nyrqis_compositor_start.restype = ctypes.c_int
    return cdll.nyrqis_compositor_start()


def stop() -> int:
    """Stop the compositor event loop. Returns 0 on success, -1 on error."""
    cdll = _load()
    if cdll is None:
        return -1
    cdll.nyrqis_compositor_stop.restype = ctypes.c_int
    return cdll.nyrqis_compositor_stop()


def is_running() -> bool:
    """Return True if the compositor is currently running."""
    cdll = _load()
    if cdll is None:
        return False
    cdll.nyrqis_compositor_is_running.restype = ctypes.c_int
    return cdll.nyrqis_compositor_is_running() != 0


def add_output(width: int, height: int, name: str = "") -> int:
    """Add a display output. Returns the output ID (>=0) or -1 on error."""
    cdll = _load()
    if cdll is None:
        return -1
    name_bytes = name.encode("utf-8") if name else None
    name_ptr = ctypes.c_char_p(name_bytes) if name_bytes else ctypes.c_char_p(None)
    cdll.nyrqis_compositor_add_output.restype = ctypes.c_int
    return cdll.nyrqis_compositor_add_output(
        ctypes.c_uint32(width),
        ctypes.c_uint32(height),
        name_ptr,
        ctypes.c_int(len(name_bytes) if name_bytes else 0),
    )


def create_surface(client_id: int, width: int, height: int) -> int:
    """Create a surface for a client. Returns the surface ID or -1."""
    cdll = _load()
    if cdll is None:
        return -1
    cdll.nyrqis_compositor_create_surface.restype = ctypes.c_int
    return cdll.nyrqis_compositor_create_surface(
        ctypes.c_uint32(client_id),
        ctypes.c_int(width),
        ctypes.c_int(height),
    )


def destroy_surface(surface_id: int) -> int:
    """Destroy a surface. Returns 0 on success, -1 on error."""
    cdll = _load()
    if cdll is None:
        return -1
    cdll.nyrqis_compositor_destroy_surface.restype = ctypes.c_int
    return cdll.nyrqis_compositor_destroy_surface(ctypes.c_int(surface_id))


def surface_count() -> int:
    """Return the number of active surfaces."""
    cdll = _load()
    if cdll is None:
        return 0
    cdll.nyrqis_compositor_surface_count.restype = ctypes.c_int
    return cdll.nyrqis_compositor_surface_count()


def output_count() -> int:
    """Return the number of active outputs."""
    cdll = _load()
    if cdll is None:
        return 0
    cdll.nyrqis_compositor_output_count.restype = ctypes.c_int
    return cdll.nyrqis_compositor_output_count()


def process_input(event_type: int, surface_id: int,
                  key_code: int = 0, button: int = 0,
                  x: float = 0.0, y: float = 0.0) -> int:
    """Process an input event. Returns 0 on success, -1 on error."""
    cdll = _load()
    if cdll is None:
        return -1
    cdll.nyrqis_compositor_process_input.restype = ctypes.c_int
    return cdll.nyrqis_compositor_process_input(
        ctypes.c_int(event_type),
        ctypes.c_uint32(surface_id),
        ctypes.c_uint32(key_code),
        ctypes.c_uint32(button),
        ctypes.c_double(x),
        ctypes.c_double(y),
    )


def send_frame_callback(surface_id: int, timestamp: int = 0) -> int:
    """Send a frame callback to a surface. Returns 0 on success, -1."""
    cdll = _load()
    if cdll is None:
        return -1
    cdll.nyrqis_compositor_send_frame_callback.restype = ctypes.c_int
    return cdll.nyrqis_compositor_send_frame_callback(
        ctypes.c_int(surface_id),
        ctypes.c_uint64(timestamp),
    )


def commit_surface(surface_id: int) -> int:
    """Commit a surface (process pending buffer). Returns 0 on success, -1."""
    cdll = _load()
    if cdll is None:
        return -1
    cdll.nyrqis_compositor_commit_surface.restype = ctypes.c_int
    return cdll.nyrqis_compositor_commit_surface(ctypes.c_int(surface_id))


def last_error() -> str:
    """Return the last error message from the crate."""
    cdll = _load()
    if cdll is None:
        return "compositor crate not available"
    buf = ctypes.create_string_buffer(256)
    cdll.nyrqis_compositor_last_error.restype = ctypes.c_int
    cdll.nyrqis_compositor_last_error(buf, ctypes.c_int(256))
    return buf.value.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Introspection (input queue depth, commit counts, frame timing)
# ---------------------------------------------------------------------------

def input_queue_depth(surface_id: int) -> int:
    """Return the number of queued input events for a surface, or -1."""
    cdll = _load()
    if cdll is None:
        return -1
    cdll.nyrqis_compositor_input_queue_depth.restype = ctypes.c_int
    return cdll.nyrqis_compositor_input_queue_depth(ctypes.c_int(surface_id))


def total_input_dispatched() -> int:
    """Return the total input events dispatched across all surfaces."""
    cdll = _load()
    if cdll is None:
        return 0
    cdll.nyrqis_compositor_total_input_dispatched.restype = ctypes.c_uint64
    return cdll.nyrqis_compositor_total_input_dispatched()


def commit_count(surface_id: int) -> int:
    """Return a surface's wl_surface.commit count, or -1."""
    cdll = _load()
    if cdll is None:
        return -1
    cdll.nyrqis_compositor_commit_count.restype = ctypes.c_int
    return cdll.nyrqis_compositor_commit_count(ctypes.c_int(surface_id))


def last_frame_time(surface_id: int) -> int:
    """Return the last frame-callback timestamp for a surface (0 = none)."""
    cdll = _load()
    if cdll is None:
        return 0
    cdll.nyrqis_compositor_last_frame_time.restype = ctypes.c_uint64
    return cdll.nyrqis_compositor_last_frame_time(ctypes.c_int(surface_id))


# ---------------------------------------------------------------------------
# Wire-format event loop (ABI 0.2.0)
# ---------------------------------------------------------------------------

def handle_client_data(client_id: int, data: bytes) -> int:
    """Feed client bytes (wire-format Wayland requests) into the event
    loop. Returns the number of bytes consumed, or -1 on a protocol
    error (requests before the malformed one have been applied)."""
    cdll = _load()
    if cdll is None:
        return -1
    fn = cdll.nyrqis_compositor_handle_client_data
    fn.restype = ctypes.c_int
    fn.argtypes = [
        ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
    ]
    buf = bytes(data)
    return fn(
        ctypes.c_uint32(client_id),
        buf if buf else None,  # c_char_p accepts bytes directly
        ctypes.c_size_t(len(buf)),
    )


def next_event(client_id: int, cap: int = 4096) -> bytes:
    """Drain the next queued server→client event (wire format).

    Returns b"" when no events are queued. Raises RuntimeError when the
    queued event does not fit the buffer (it stays queued for a larger
    drain).
    """
    cdll = _load()
    if cdll is None:
        return b""
    fn = cdll.nyrqis_compositor_next_event
    fn.restype = ctypes.c_int
    fn.argtypes = [
        ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
    ]
    buf = ctypes.create_string_buffer(cap)
    n = fn(ctypes.c_uint32(client_id), buf, ctypes.c_size_t(cap))
    if n < 0:
        raise RuntimeError(last_error() or "event drain failed")
    return buf.raw[:n]


def object_count() -> int:
    """Return the number of active objects in the event loop's object
    table (wl_display included)."""
    cdll = _load()
    if cdll is None:
        return 0
    cdll.nyrqis_compositor_object_count.restype = ctypes.c_int
    return cdll.nyrqis_compositor_object_count()


def event_loop_last_error() -> str:
    """Return the event loop's last protocol-error message ("" = none)."""
    cdll = _load()
    if cdll is None:
        return "compositor crate not available"
    buf = ctypes.create_string_buffer(256)
    fn = cdll.nyrqis_compositor_event_loop_last_error
    fn.restype = ctypes.c_int
    fn.argtypes = [ctypes.c_char_p, ctypes.c_int]
    n = fn(buf, ctypes.c_int(256))
    return buf.value.decode("utf-8", errors="replace") if n >= 0 else ""
