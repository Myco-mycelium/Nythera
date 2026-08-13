#!/usr/bin/env python3
"""
rust_syscalls — FFI loader + wiring for the Nyrqis syscalls module
(ADR-0020 migration priority #2; see rust/syscalls/README.md).

Shared by ``backend/launcher.py`` (``sethostname`` / ``prctl`` — wired
today) and, in the direct-syscall launcher transition
(``docs/implementation_plan.md`` §4.1), ``backend/container.py``
(``unshare`` — the container launch path still uses ``unshare(1)`` until
that transition lands).

Contract, mirroring ``backend/seccomp.py``'s loader:

- Search order for the Rust cdylib: ``$NYRQIS_RUST_LIB``, the crate's
  ``target/release/``, then a bare name (honors ``LD_LIBRARY_PATH``).
- ABI-version check against ``MIN_RUST_ABI_VERSION``.
- On ANY load or call failure: log once and fall back to the pure-ctypes
  path — the Python implementation remains the correctness floor, so the
  tests keep passing unchanged either way.
- ``NYRQIS_RUST_FORCE=1`` turns routing failures into errors (the
  conformance gate that proves every call drives the Rust module).

The Rust module returns 0 on success or a negative errno (the Linux
syscall convention); ``-4096`` is the module's internal error code,
deliberately OUTSIDE the errno range (1..=4095) so ``-errno → OSError``
can never misreport it as a real kernel error (an in-range code like -4
would surface as EINTR).
"""

import ctypes
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

MIN_RUST_ABI_VERSION = 0x0001_0000  # nyrqis-syscalls 1.0.0

# Module internal error codes (negative i32). -4096 is outside the errno
# range by design (see module docstring).
RUST_ERR_INTERNAL = -4096

# prctl(2) constants
PR_SET_HOSTNAME = 10  # used when sethostname(2) is blocked (e.g. no CAP_SYS_ADMIN in the namespace)

_RUST_LIB: Optional[ctypes.CDLL] = None
_RUST_LIB_CHECKED = False


def _rust_lib_candidates() -> List[str]:
    """Search order for the Rust cdylib: ``$NYRQIS_RUST_LIB``, the
    crate's ``target/release/``, then a bare name (honors
    ``LD_LIBRARY_PATH``)."""
    override = os.environ.get("NYRQIS_RUST_LIB")
    if override:
        return [override]
    here = os.path.dirname(os.path.abspath(__file__))
    crate_target = os.path.join(
        here, "..", "rust", "syscalls", "target", "release",
        "libnyrqis_syscalls.so",
    )
    return [crate_target, "libnyrqis_syscalls.so"]


def _force_enabled() -> bool:
    return os.environ.get("NYRQIS_RUST_FORCE") in ("1", "true", "yes")


def _rust_force_error() -> str:
    return (
        "NYRQIS_RUST_FORCE=1 but the Rust syscalls backend is not available "
        "(searched: " + ", ".join(_rust_lib_candidates()) + ")"
    )


def _raise_rust_error(code: int, context: str) -> None:
    """Map a negative return from the Rust module to the Python
    exception the equivalent syscall path would raise: ``-errno`` →
    ``OSError(errno, strerror)``; the module's internal code (-4096,
    outside the errno range) → ``RuntimeError``."""
    err = -code
    if 1 <= err <= 4095:
        raise OSError(err, os.strerror(err), context)
    raise RuntimeError(f"{context}: Rust syscalls backend: error {code}")


def _load_rust_backend() -> Optional[ctypes.CDLL]:
    """Locate and load the Rust syscalls cdylib, or return None.

    The result is cached. A library whose ABI version is below
    ``MIN_RUST_ABI_VERSION`` is skipped. Never raises: a miss simply
    means "use the ctypes path".
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
            lib.nyrqis_syscalls_version.restype = ctypes.c_uint32
            version = lib.nyrqis_syscalls_version()
        except AttributeError:
            logger.warning(
                "syscalls: %s has no nyrqis_syscalls_version symbol; skipping",
                path,
            )
            continue
        if version < MIN_RUST_ABI_VERSION:
            logger.warning(
                "syscalls: %s ABI %#x is below required %#x; skipping",
                path, version, MIN_RUST_ABI_VERSION,
            )
            continue
        lib.nyrqis_syscalls_sethostname.restype = ctypes.c_int
        lib.nyrqis_syscalls_sethostname.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t,
        ]
        lib.nyrqis_syscalls_prctl.restype = ctypes.c_int
        lib.nyrqis_syscalls_prctl.argtypes = [
            ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint64,
            ctypes.c_uint64, ctypes.c_uint64,
        ]
        lib.nyrqis_syscalls_unshare.restype = ctypes.c_int
        lib.nyrqis_syscalls_unshare.argtypes = [ctypes.c_uint64]
        _RUST_LIB = lib
        logger.info("syscalls: using Rust backend (%s)", path)
        return lib
    return None


# ---------------------------------------------------------------------------
# Rust FFI entry points (called only when the module is loaded)
# ---------------------------------------------------------------------------

def _rust_sethostname(lib: ctypes.CDLL, name: bytes) -> int:
    """sethostname(2) via the Rust module: 0 or -errno."""
    buf = ctypes.create_string_buffer(name, len(name) + 1)
    rc = lib.nyrqis_syscalls_sethostname(
        ctypes.cast(buf, ctypes.c_void_p), len(name)
    )
    return int(rc)


def _rust_prctl(
    lib: ctypes.CDLL, option: int, a2: int, a3: int, a4: int, a5: int
) -> int:
    """prctl(2) via the Rust module: 0 or -errno."""
    rc = lib.nyrqis_syscalls_prctl(option, a2, a3, a4, a5)
    return int(rc)


def _rust_unshare(lib: ctypes.CDLL, flags: int) -> int:
    """unshare(2) via the Rust module: 0 or -errno."""
    rc = lib.nyrqis_syscalls_unshare(flags)
    return int(rc)


# ---------------------------------------------------------------------------
# ctypes fallbacks (the pre-ADR-0020 path — the correctness floor)
# ---------------------------------------------------------------------------

def _ctypes_sethostname(name: bytes) -> int:
    """sethostname(2) via ctypes: 0 or -errno (mirror of the old
    launcher path — no shell, FIND-BACKEND-004)."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.sethostname.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    libc.sethostname.restype = ctypes.c_int
    if libc.sethostname(name, len(name)) == 0:
        return 0
    return -ctypes.get_errno()


def _ctypes_prctl(
    option: int, a2: int, a3: int, a4: int, a5: int
) -> int:
    """prctl(2) via ctypes: 0 or -errno."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_ulong, ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(option, a2, a3, a4, a5) == 0:
        return 0
    return -ctypes.get_errno()


def _ctypes_unshare(flags: int) -> None:
    """unshare(2) via ctypes; raises OSError on failure."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.unshare.argtypes = [ctypes.c_int]
    libc.unshare.restype = ctypes.c_int
    if libc.unshare(int(flags)) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


# ---------------------------------------------------------------------------
# Public surface (FFI first, ctypes fallback, force-aware)
# ---------------------------------------------------------------------------

def sethostname(name: bytes) -> int:
    """sethostname(2): 0 on success or -errno.

    Rust FFI when the module is loaded; ctypes otherwise. A negative
    return is the kernel's answer (passed through — the ctypes path
    would answer identically); only routing failures raise, and only
    under ``NYRQIS_RUST_FORCE=1``.
    """
    lib = _load_rust_backend()
    if lib is not None:
        try:
            return _rust_sethostname(lib, name)
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "syscalls: Rust sethostname failed (%s: %s); using ctypes",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    return _ctypes_sethostname(name)


def prctl(option: int, a2: int = 0, a3: int = 0, a4: int = 0, a5: int = 0) -> int:
    """prctl(2): 0 on success or -errno. Rust FFI when the module is
    loaded; ctypes otherwise. See ``sethostname`` for the force rules."""
    lib = _load_rust_backend()
    if lib is not None:
        try:
            return _rust_prctl(lib, option, a2, a3, a4, a5)
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "syscalls: Rust prctl failed (%s: %s); using ctypes",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    return _ctypes_prctl(option, a2, a3, a4, a5)


def unshare(flags: int) -> None:
    """unshare(2): raises OSError on failure.

    Rust FFI when the module is loaded; ctypes otherwise. The container
    launch path still uses ``unshare(1)`` until the direct-syscall
    transition (``docs/implementation_plan.md`` §4.1) lands; this is the
    primitive that transition will call.
    """
    lib = _load_rust_backend()
    if lib is not None:
        try:
            rc = _rust_unshare(lib, flags)
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "syscalls: Rust unshare failed (%s: %s); using ctypes",
                type(exc).__name__, exc,
            )
            rc = None
        if rc == 0:
            return
        if rc is not None:
            _raise_rust_error(rc, "unshare")  # -errno -> OSError(errno)
        # rc None = routing failure, not forced: fall through to ctypes.
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    _ctypes_unshare(flags)


def set_hostname(hostname: str) -> bool:
    """Set the UTS hostname without any shell involvement.

    ``sethostname(2)`` via the Rust module (or ctypes fallback), then
    ``prctl(PR_SET_HOSTNAME)`` as the in-namespace fallback when
    sethostname(2) is blocked (e.g. a user namespace without
    CAP_SYS_ADMIN). Failures are logged, not fatal — matching the
    launcher's contract (FIND-BACKEND-004: the hostname is an argv
    entry, never shell-interpolated).
    """
    if not hostname:
        return True
    encoded = hostname.encode("utf-8", "replace")
    rc = sethostname(encoded)
    if rc == 0:
        logger.info("hostname set to %r", hostname)
        return True
    logger.warning(
        "sethostname(%r) failed: errno=%d (%s)",
        hostname, -rc, os.strerror(-rc),
    )
    # prctl(PR_SET_HOSTNAME, name) writes the same UTS namespace
    # hostname; arg2 carries the name pointer. The buffer must be a
    # real ctypes object (ctypes.cast on a raw bytes object raises
    # TypeError), so build one and take its address.
    buf = ctypes.create_string_buffer(encoded)
    addr = ctypes.addressof(buf)
    prc = prctl(PR_SET_HOSTNAME, addr, 0, 0, 0)
    if prc == 0:
        logger.info("hostname set to %r (prctl fallback)", hostname)
        return True
    logger.warning(
        "prctl(PR_SET_HOSTNAME) fallback failed: errno=%d (%s)",
        -prc, os.strerror(-prc),
    )
    return False


__all__ = [
    "MIN_RUST_ABI_VERSION",
    "PR_SET_HOSTNAME",
    "set_hostname",
    "sethostname",
    "prctl",
    "unshare",
]
