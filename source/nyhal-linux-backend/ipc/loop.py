#!/usr/bin/env python3
"""
loop — FFI driver for the Rust IPC serving loop (ADR-0021,
``rust/ipcd/``; the first NyRuntime-shaped artifact).

Per ADR-0021, the loop owns the whole dispatch cycle for the daemon's
service socket — ``poll`` → ``recvmsg`` (``SCM_CREDENTIALS``) →
wire-codec parse → sender authorization → service dispatch →
``sendto`` reply — inside the Rust process loop, crossing the boundary
once per *batch* (a bounded drain of datagrams) instead of once per
message. The per-message ctypes boundary tax of the Python floor is
paid once per batch, not twice per round trip.

First-increment scope (honest, ADR-0021's gate-on-data rule):

- The loop serves the built-in ``ping`` op of the status service with
  byte-identical reply semantics to the Python floor
  (``ipc/service.py``); anything else is dropped at the trust boundary
  (the non-ping dispatch handoff is the next increment).
- Sender-authorization policy crosses the boundary as plain data at
  loop creation: a pid→container table, the trusted-uid set, and the
  operator id — the same inputs the floor's ``_authorized`` uses. The
  loop does the execution; Python supplies only the data.
- The floor stays shipped. The loop lands behind the differential
  conformance gate (reply semantics equivalent to the floor) and the
  §N benchmark A/B; ADR-0021's close gate (beat the floor AND < 100 µs
  wire median) decides when the floor is demoted.

Contract, mirroring the transport/codec loaders:

- Search order for the Rust cdylib: ``$NYRQIS_RUST_LIB``, the crate's
  ``target/release/``, then a bare name (honors ``LD_LIBRARY_PATH``).
- ABI-version check against ``MIN_RUST_ABI_VERSION`` (1.0.0).
- On load failure, ``IpcdLoop`` raises ``BackendUnavailable`` and the
  caller falls back to the Python floor — unless ``NYRQIS_RUST_FORCE=1``,
  which turns routing failures into errors (the conformance gate's
  guarantee that every call drives the Rust module).
- The Rust module returns 0 on success or a negative value: ``-errno``
  for real failures, ``-4096`` (ERR_INTERNAL) for module failures.
  ``nyrqis_ipcd_loop_step`` returns the number of datagrams drained
  (≥ 0); 0 is a clean timeout, never an error.

Ownership: the loop does NOT close the socket fd — the caller (the
Python floor's ``UnixDatagramEndpoint``) owns binding, ``SO_PASSCRED``,
and unlink-on-close, and hands the bound fd in.
"""

import ctypes
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_RUST_ABI_VERSION = 0x0001_0000  # nyrqis-ipcd 1.0.0

# Module internal error codes (negative i32), outside the errno range.
RUST_ERR_INTERNAL = -4096  # module failure (→ RuntimeError)


class _PidEntry(ctypes.Structure):
    """Mirror of the crate's ``#[repr(C)] PidEntry`` (one pid→container
    mapping in the loop's authorization policy)."""
    _fields_ = [
        ("pid", ctypes.c_int),
        ("container", ctypes.c_char_p),
    ]


_RUST_LIB: Optional[ctypes.CDLL] = None
_RUST_LIB_CHECKED = False


class BackendUnavailable(RuntimeError):
    """The Rust serving-loop backend could not be loaded and force mode
    is off — the caller falls back to the Python floor (never a failure
    on its own)."""


def _rust_lib_candidates() -> list:
    """Search order: ``$NYRQIS_RUST_LIB``, the crate's
    ``target/release/``, then a bare name (honors ``LD_LIBRARY_PATH``)."""
    override = os.environ.get("NYRQIS_RUST_LIB")
    if override:
        return [override]
    here = os.path.dirname(os.path.abspath(__file__))
    crate_target = os.path.join(
        here, "..", "rust", "ipcd", "target", "release",
        "libnyrqis_ipcd.so",
    )
    return [crate_target, "libnyrqis_ipcd.so"]


def force_enabled() -> bool:
    return os.environ.get("NYRQIS_RUST_FORCE") in ("1", "true", "yes")


def _rust_force_error() -> str:
    return (
        "NYRQIS_RUST_FORCE=1 but the Rust IPC serving loop backend is "
        "not available (searched: " + ", ".join(_rust_lib_candidates()) + ")"
    )


def _raise_rust_error(code: int, context: str) -> None:
    """Map a negative return from the Rust module: ``-errno`` →
    ``OSError``, ``-4096`` (internal) → ``RuntimeError``."""
    if code == RUST_ERR_INTERNAL:
        raise RuntimeError(f"{context}: Rust IPC serving loop: error {code}")
    err = -code
    if 1 <= err <= 4095:
        raise OSError(err, os.strerror(err), context)
    raise RuntimeError(f"{context}: Rust IPC serving loop: error {code}")


def _load_rust_backend() -> Optional[ctypes.CDLL]:
    """Locate and load the Rust serving-loop cdylib, or return None.

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
            lib.nyrqis_ipcd_version.restype = ctypes.c_uint32
            version = lib.nyrqis_ipcd_version()
        except AttributeError:
            logger.warning(
                "ipc loop: %s has no nyrqis_ipcd_version symbol; skipping",
                path,
            )
            continue
        if version < MIN_RUST_ABI_VERSION:
            logger.warning(
                "ipc loop: %s ABI %#x is below required %#x; skipping",
                path, version, MIN_RUST_ABI_VERSION,
            )
            continue
        lib.nyrqis_ipcd_loop_new.restype = ctypes.c_void_p
        lib.nyrqis_ipcd_loop_new.argtypes = [
            ctypes.c_int,
            ctypes.c_uint32,
            ctypes.POINTER(_PidEntry), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_int), ctypes.c_size_t,
            ctypes.c_char_p,
        ]
        lib.nyrqis_ipcd_loop_step.restype = ctypes.c_int
        lib.nyrqis_ipcd_loop_step.argtypes = [
            ctypes.c_void_p, ctypes.c_int64,
        ]
        lib.nyrqis_ipcd_loop_free.restype = None
        lib.nyrqis_ipcd_loop_free.argtypes = [ctypes.c_void_p]
        _RUST_LIB = lib
        logger.info("ipc loop: using Rust serving loop (%s)", path)
        return lib
    return None


def available() -> bool:
    """True when the Rust serving loop is loaded (cached)."""
    return _load_rust_backend() is not None


class IpcdLoop:
    """A Rust serving loop over a bound, SO_PASSCRED-enabled socket fd.

    The caller (the Python floor) owns the fd lifecycle: bind with 0700
    perms and ``SO_PASSCRED`` (``UnixDatagramEndpoint.bind``), then hand
    the fd in; the loop does NOT close it. Drive the loop by calling
    :meth:`step` repeatedly (a serve thread); each step polls and drains
    up to ``batch_max`` datagrams in one boundary crossing.

    ``pids`` maps pid → container id; ``trusted_uids`` and
    ``operator_id`` mirror the floor's ``_authorized`` operator
    fallback. The policy is a snapshot — the per-batch refresh is the
    next increment (ADR-0021 decision point 2).
    """

    def __init__(
        self,
        fd: int,
        batch_max: int = 64,
        pids: Optional[Dict[int, str]] = None,
        trusted_uids: Optional[List[int]] = None,
        operator_id: str = "host-operator",
    ) -> None:
        lib = _load_rust_backend()
        if lib is None:
            if force_enabled():
                raise RuntimeError(_rust_force_error())
            raise BackendUnavailable()
        self._lib = lib
        entries = [
            _PidEntry(pid, cid.encode("utf-8"))
            for pid, cid in (pids or {}).items()
        ]
        entries_arr = (_PidEntry * len(entries))(*entries) if entries else None
        uids = [int(u) for u in (trusted_uids or [])]
        uids_arr = (ctypes.c_int * len(uids))(*uids) if uids else None
        handle = lib.nyrqis_ipcd_loop_new(
            fd,
            batch_max,
            entries_arr,
            len(entries),
            uids_arr,
            len(uids),
            operator_id.encode("utf-8"),
        )
        if not handle:
            raise RuntimeError("ipc loop: nyrqis_ipcd_loop_new failed")
        self._handle = ctypes.c_void_p(handle)
        self._closed = False

    def step(self, timeout_ms: int = 50) -> int:
        """Poll up to ``timeout_ms`` and drain one batch. Returns the
        number of datagrams processed (0 = clean timeout)."""
        if self._closed:
            raise RuntimeError("ipc loop: step on a closed loop")
        rc = self._lib.nyrqis_ipcd_loop_step(self._handle, timeout_ms)
        if rc < 0:
            _raise_rust_error(rc, "loop step")
        return rc

    def close(self) -> None:
        if not self._closed:
            self._lib.nyrqis_ipcd_loop_free(self._handle)
            self._closed = True


__all__ = [
    "MIN_RUST_ABI_VERSION",
    "RUST_ERR_INTERNAL",
    "BackendUnavailable",
    "force_enabled",
    "available",
    "IpcdLoop",
]
