"""Wayland display-server FFI loader — ADR-0026.

Thin ctypes wrapper around the ``nyrqis_wayland`` Rust cdylib.  This
module owns the FFI surface for the Wayland client; the pure-Python
``DesktopSession`` is the reference floor.

Search order for the Rust cdylib
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. ``$NYRQIS_RUST_LIB``  (explicit override, used in CI conformance
   gates: ``NYRQIS_RUST_FORCE=1 NYRQIS_RUST_LIB=/path/to/libnyrqis_wayland.so``)
2. ``<crate>/target/release/libnyrqis_wayland.so``  (production build)
3. ``<crate>/target/debug/libnyrqis_wayland.so``    (development build)
4. Fall back to the pure-Python reference floor (``WAYLAND_STUB = True``).

When the stub path is active every FFI call returns -1 and sets
``last_error()`` to ``"stub"`` — the same behaviour as a compositor
refusing the connection, so callers see a clean failure mode.

ABI 1.0.0 — ``NYRQIS_WAYLAND_ABI = 0x0001_0000``.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Optional

# ABI version expected by this loader — must match
# nyrqis_wayland_version() in rust/wayland/src/lib.rs.
NYRQIS_WAYLAND_ABI: int = 0x0001_0100  # 1.1.0 — Phase 1b + xdg-shell + input

# ---------------------------------------------------------------------------
# Library search and load
# ---------------------------------------------------------------------------

_CRATE_DIR = Path(__file__).resolve().parent.parent / "rust" / "wayland"
_LIB_NAMES = [
    "libnyrqis_wayland.so",    # Linux
    "libnyrqis_wayland.dylib", # macOS
    "nyrqis_wayland.dll",      # Windows
]

_cached_lib: Optional[ctypes.CDLL] = None


def _find_library() -> Optional[Path]:
    """Return the first matching cdylib path, or None."""
    # 1. Explicit override
    override = os.environ.get("NYRQIS_RUST_LIB")
    if override:
        p = Path(override)
        if p.exists():
            return p

    # 2–3. Crate build directories
    for profile in ("release", "debug"):
        for name in _LIB_NAMES:
            p = _CRATE_DIR / "target" / profile / name
            if p.exists():
                return p

    return None


def _load():
    """Load the cdylib, or fall back to the stub."""
    global _cached_lib
    if _cached_lib is not None:
        return _cached_lib

    path = _find_library()
    if path is None:
        return None

    try:
        _cached_lib = ctypes.CDLL(str(path))
        # Quick ABI check
        fn = _cached_lib.nyrqis_wayland_version
        fn.restype = ctypes.c_uint32
        ver = fn()
        if ver != NYRQIS_WAYLAND_ABI:
            raise RuntimeError(
                f"wayland crate ABI mismatch: expected "
                f"0x{NYRQIS_WAYLAND_ABI:08X}, got 0x{ver:08X}"
            )
        return _cached_lib
    except Exception:
        _cached_lib = None
        return None


def reset():
    """Forget the cached handle (tests that shuffle NYRQIS_RUST_LIB)."""
    global _cached_lib
    _cached_lib = None


# ---------------------------------------------------------------------------
# FFI wrappers
# ---------------------------------------------------------------------------

WAYLAND_STUB: bool = _load() is None


def _lib():
    lib = _load()
    if lib is None:
        raise RuntimeError(
            "nyrqis_wayland cdylib not found — set NYRQIS_RUST_LIB or "
            "build with `cargo build --release` in rust/wayland/"
        )
    return lib


def connect(display_name: Optional[str] = None) -> int:
    """Connect to a Wayland display server.  Returns conn_id or -1."""
    if WAYLAND_STUB:
        return -1
    lib = _lib()
    if display_name is not None:
        encoded = display_name.encode("utf-8")
        ptr = ctypes.create_string_buffer(encoded)
        return lib.nyrqis_wayland_connect(ptr, len(encoded))
    return lib.nyrqis_wayland_connect(None, 0)


def create_surface(
    conn_id: int,
    use_xdg: bool = True,
    title: Optional[str] = None,
) -> int:
    """Create a wl_surface with optional xdg-shell decoration.

    If *use_xdg* is True (default), creates xdg_surface + xdg_toplevel
    for proper window management.

    Returns surface_id or -1.
    """
    if WAYLAND_STUB:
        return -1
    lib = _lib()
    if title is not None:
        encoded = title.encode("utf-8")
        ptr = ctypes.create_string_buffer(encoded)
        return lib.nyrqis_wayland_create_surface(
            conn_id, 1 if use_xdg else 0, ptr, len(encoded)
        )
    return lib.nyrqis_wayland_create_surface(conn_id, 1 if use_xdg else 0, None, 0)


def submit_buffer(
    surface_id: int,
    pixel_data: bytes,
    width: int,
    height: int,
    stride: int,
) -> int:
    """Submit a pixel buffer to a surface via wl_shm.

    Creates a memfd, copies pixel data, creates wl_shm_pool + wl_buffer,
    attaches to the surface, and commits.  Returns buffer_id or -1.
    """
    if WAYLAND_STUB:
        return -1
    buf = ctypes.create_string_buffer(pixel_data)
    return _lib().nyrqis_wayland_submit_buffer(
        surface_id, buf, len(pixel_data), width, height, stride
    )


def dispatch_events(conn_id: int, timeout_ms: int = 100) -> int:
    """Poll for pending Wayland events."""
    if WAYLAND_STUB:
        return -1
    return _lib().nyrqis_wayland_dispatch_events(conn_id, timeout_ms)


def disconnect(conn_id: int) -> int:
    """Disconnect from a Wayland display and free resources."""
    if WAYLAND_STUB:
        return -1
    return _lib().nyrqis_wayland_disconnect(conn_id)


def destroy_surface(surface_id: int) -> int:
    """Destroy a surface and free its resources."""
    if WAYLAND_STUB:
        return -1
    return _lib().nyrqis_wayland_destroy_surface(surface_id)


def set_title(surface_id: int, title: str) -> int:
    """Set the title of an xdg_toplevel surface."""
    if WAYLAND_STUB:
        return -1
    encoded = title.encode("utf-8")
    ptr = ctypes.create_string_buffer(encoded)
    return _lib().nyrqis_wayland_set_title(surface_id, ptr, len(encoded))


def get_fd(conn_id: int) -> int:
    """Get the display connection file descriptor for external polling."""
    if WAYLAND_STUB:
        return -1
    return _lib().nyrqis_wayland_get_fd(conn_id)


def last_error() -> str:
    """Return the last error message from the Rust crate."""
    if WAYLAND_STUB:
        return "wayland crate not loaded (stub mode)"
    buf = ctypes.create_string_buffer(512)
    n = _lib().nyrqis_wayland_last_error(buf, 512)
    if n < 0:
        return ""
    return buf.raw[:n].decode("utf-8", errors="replace")
