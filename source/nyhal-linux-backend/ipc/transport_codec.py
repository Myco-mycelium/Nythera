#!/usr/bin/env python3
"""
transport_codec — FFI loader + routing for the Rust IPC transport hot
path (ADR-0020 migration priority #6; see rust/transport/README.md).

The send/receive half of the cross-process IPC transport
(``ipc/transport.py``): one ``sendto`` per outbound frame, one
``poll``+``recvmsg`` per inbound frame, and the ``SCM_CREDENTIALS``
ancillary parse that yields the sender's real ``(pid, uid, gid)``. The
wire bytes are opaque here — the codec (migration #4, ``ipc/ipc_codec``)
owns framing and parsing. Binding (0700 perms, ``SO_PASSCRED``, unlink)
stays on the Python floor; this module is the per-message syscall path.

Contract, mirroring the seccomp/syscalls/nyfs/ipc/container loaders:

- Search order for the Rust cdylib: ``$NYRQIS_RUST_LIB``, the crate's
  ``target/release/``, then a bare name (honors ``LD_LIBRARY_PATH``).
- ABI-version check against ``MIN_RUST_ABI_VERSION``.
- On load failure, the routing helpers raise ``BackendUnavailable`` and
  the caller (``ipc/transport.py``) falls back to the Python floor —
  unless ``NYRQIS_RUST_FORCE=1``, which turns routing failures into
  errors (the conformance gate's guarantee that every call drives the
  Rust module).
- The Rust module returns 0 on success or a negative value: ``-errno``
  for real failures, ``-4096`` (ERR_INTERNAL) for module failures.
  ``-4096`` is outside the errno range (1..=4095) so the
  ``-errno → OSError`` mapping can never misreport it; the loader maps
  ``-4096`` → ``RuntimeError``.

Timeout semantics: ``recv(fd, timeout_ms)`` returns ``None`` when the
timeout elapsed with no data (negative ``timeout_ms`` = block until
data); the crate ``poll``s first and ``recvmsg``s with ``MSG_DONTWAIT``,
so it never blocks past the timeout and is safe on both blocking and
non-blocking fds. Output buffers (frame bytes, sender path) are
``libc::malloc``'d by the crate and freed here through
``nyrqis_transport_free`` — never leaked, and the sender path is always
a real C string (``None`` on the Python side only when it is absent).
"""

import ctypes
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

MIN_RUST_ABI_VERSION = 0x0001_0000  # nyrqis-transport 1.0.0

# Module internal error codes (negative i32), outside the errno range.
RUST_ERR_INTERNAL = -4096  # module failure (→ RuntimeError)

_RUST_LIB: Optional[ctypes.CDLL] = None
_RUST_LIB_CHECKED = False


class BackendUnavailable(RuntimeError):
    """The Rust transport backend could not be loaded and force mode is
    off — the caller falls back to the Python floor (never a failure on
    its own)."""


def _rust_lib_candidates() -> list:
    """Search order: ``$NYRQIS_RUST_LIB``, the crate's
    ``target/release/``, then a bare name (honors ``LD_LIBRARY_PATH``)."""
    override = os.environ.get("NYRQIS_RUST_LIB")
    if override:
        return [override]
    here = os.path.dirname(os.path.abspath(__file__))
    crate_target = os.path.join(
        here, "..", "rust", "transport", "target", "release",
        "libnyrqis_transport.so",
    )
    return [crate_target, "libnyrqis_transport.so"]


def force_enabled() -> bool:
    return os.environ.get("NYRQIS_RUST_FORCE") in ("1", "true", "yes")


def _rust_force_error() -> str:
    return (
        "NYRQIS_RUST_FORCE=1 but the Rust IPC transport backend is not "
        "available (searched: " + ", ".join(_rust_lib_candidates()) + ")"
    )


def _raise_rust_error(code: int, context: str) -> None:
    """Map a negative return from the Rust module: ``-errno`` →
    ``OSError``, ``-4096`` (internal) → ``RuntimeError``."""
    if code == RUST_ERR_INTERNAL:
        raise RuntimeError(f"{context}: Rust IPC transport: error {code}")
    err = -code
    if 1 <= err <= 4095:
        raise OSError(err, os.strerror(err), context)
    raise RuntimeError(f"{context}: Rust IPC transport: error {code}")


def _load_rust_backend() -> Optional[ctypes.CDLL]:
    """Locate and load the Rust IPC transport cdylib, or return None.

    The result is cached. A library whose ABI version is below
    ``MIN_RUST_ABI_VERSION`` is skipped. Never raises: a miss simply
    means "use the Python path".
    """
    global _RUST_LIB, _RUST_LIB_CHECKED
    if _RUST_LIB_CHECKED:
        return _RUST_LIB
    _RUST_LIB_CHECKED = True
    for path in _rust_lib_candidates():
        try:
            lib = ctypes.CDLL(path)
        except OSError:
            continue
        try:
            lib.nyrqis_transport_version.restype = ctypes.c_uint32
            version = lib.nyrqis_transport_version()
        except AttributeError:
            logger.warning(
                "ipc transport: %s has no nyrqis_transport_version "
                "symbol; skipping", path,
            )
            continue
        if version < MIN_RUST_ABI_VERSION:
            logger.warning(
                "ipc transport: %s ABI %#x is below required %#x; skipping",
                path, version, MIN_RUST_ABI_VERSION,
            )
            continue
        lib.nyrqis_transport_send.restype = ctypes.c_int
        lib.nyrqis_transport_send.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_char_p,
        ]
        lib.nyrqis_transport_recv.restype = ctypes.c_int
        lib.nyrqis_transport_recv.argtypes = [
            ctypes.c_int, ctypes.c_int64,
            # out_wire is *mut *mut u8 — POINTER(POINTER(c_ubyte)), NOT
            # POINTER(c_void_p): ctypes enforces nested pointer types
            # exactly (the CI conformance gate caught this mismatch).
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_char_p),
        ]
        lib.nyrqis_transport_free.restype = None
        lib.nyrqis_transport_free.argtypes = [ctypes.c_void_p]
        _RUST_LIB = lib
        logger.info("ipc transport: using Rust backend (%s)", path)
        return lib
    return None


def available() -> bool:
    """True when the Rust transport backend is loaded (cached). Used by
    the conformance skip-gate and the endpoint's routing decision."""
    return _load_rust_backend() is not None


def send(fd: int, wire: bytes, peer_path: str) -> None:
    """Route one outbound frame through the Rust transport.

    Raises ``BackendUnavailable`` (fall back to the floor) when the
    backend is absent and not forced; ``RuntimeError`` in force mode;
    ``OSError``/``RuntimeError`` on a Rust failure.
    """
    lib = _load_rust_backend()
    if lib is None:
        if force_enabled():
            raise RuntimeError(_rust_force_error())
        raise BackendUnavailable()
    buf = ctypes.create_string_buffer(wire, len(wire))
    rc = lib.nyrqis_transport_send(
        fd, ctypes.cast(buf, ctypes.c_void_p), len(wire),
        peer_path.encode("utf-8"),
    )
    if rc != 0:
        _raise_rust_error(rc, "send")


def recv(
    fd: int, timeout_ms: int
) -> Optional[Tuple[bytes, int, int, int, str]]:
    """Route one inbound frame through the Rust transport.

    Returns ``(wire, pid, uid, gid, sender_path)`` on data, ``None`` on
    timeout. Raises ``BackendUnavailable`` (fall back to the floor) when
    the backend is absent and not forced; ``RuntimeError`` in force
    mode; ``OSError``/``RuntimeError`` on a Rust failure. Freed output
    buffers are always reclaimed.
    """
    lib = _load_rust_backend()
    if lib is None:
        if force_enabled():
            raise RuntimeError(_rust_force_error())
        raise BackendUnavailable()
    out_wire = ctypes.POINTER(ctypes.c_ubyte)()
    out_len = ctypes.c_size_t()
    out_pid = ctypes.c_int()
    out_uid = ctypes.c_int()
    out_gid = ctypes.c_int()
    out_path = ctypes.c_char_p()
    rc = lib.nyrqis_transport_recv(
        fd, timeout_ms,
        ctypes.byref(out_wire), ctypes.byref(out_len),
        ctypes.byref(out_pid), ctypes.byref(out_uid),
        ctypes.byref(out_gid), ctypes.byref(out_path),
    )
    if rc != 0:
        _raise_rust_error(rc, "receive")
    if out_len.value == 0:
        return None  # timeout — no data
    try:
        wire = ctypes.string_at(out_wire, out_len.value)
        sender_path = out_path.value.decode("utf-8") if out_path.value else ""
        return wire, out_pid.value, out_uid.value, out_gid.value, sender_path
    finally:
        lib.nyrqis_transport_free(ctypes.cast(out_wire, ctypes.c_void_p))
        if out_path.value is not None:
            lib.nyrqis_transport_free(ctypes.cast(out_path, ctypes.c_void_p))


__all__ = [
    "MIN_RUST_ABI_VERSION",
    "RUST_ERR_INTERNAL",
    "BackendUnavailable",
    "force_enabled",
    "available",
    "send",
    "recv",
]
