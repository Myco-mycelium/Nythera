#!/usr/bin/env python3
"""
rust_syscalls — FFI loader + wiring for the Nyrqis syscalls module
(ADR-0020 migration priority #2; see rust/syscalls/README.md).

Shared by ``backend/launcher.py`` (``sethostname`` / ``prctl``) and
``backend/container.py`` (``unshare`` / ``mount`` — the direct-syscall
launcher, ``docs/implementation_plan.md`` §4.1, landed 2026-08-13:
``unshare(1)`` is retained only as an opt-in legacy path).

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

MIN_RUST_ABI_VERSION = 0x0001_0200  # nyrqis-syscalls 1.2.0 (clone + launch child)

# CLONE_NEW* flags (the direct-syscall launcher's namespace mask). These
# are the stable Linux UAPI bit values; the Rust crate consumes them as
# the ``unshare``/``clone`` flags argument. CLONE_NEWPID only affects
# the caller's children, which is why the fork path forks after
# unsharing it; the clone path carries all the namespace flags in the
# clone call itself. SIGCHLD (17) must be OR'd in so the child is a
# normal zombie (a zero signal byte auto-releases it — waitpid would
# ECHILD).
CLONE_NEWNS = 0x0002_0000
CLONE_NEWUTS = 0x0400_0000
CLONE_NEWIPC = 0x0800_0000
CLONE_NEWUSER = 0x1000_0000
CLONE_NEWPID = 0x2000_0000
CLONE_NEWNET = 0x4000_0000  # network namespace (container isolation)
CLONE_SIGCHLD = 17  # SIGCHLD — the low byte's child-termination signal

# MS_* flags for the container's procfs mount (hardened like
# unshare(1)'s --mount-proc: nosuid, nodev, noexec).
MS_NOSUID = 0x2
MS_NODEV = 0x4
MS_NOEXEC = 0x8
MS_PROC_MOUNT = MS_NOSUID | MS_NODEV | MS_NOEXEC

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
        lib.nyrqis_syscalls_mount.restype = ctypes.c_int
        lib.nyrqis_syscalls_mount.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_uint64, ctypes.c_void_p,
        ]
        lib.nyrqis_syscalls_mount_proc.restype = ctypes.c_int
        lib.nyrqis_syscalls_mount_proc.argtypes = []
        lib.nyrqis_syscalls_clone.restype = ctypes.c_int
        lib.nyrqis_syscalls_clone.argtypes = [
            ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p,
        ]
        lib.nyrqis_syscalls_launch_child.restype = ctypes.c_int
        lib.nyrqis_syscalls_launch_child.argtypes = [ctypes.c_void_p]
        _RUST_LIB = lib
        logger.info("syscalls: using Rust backend (%s)", path)
        return lib
    return None


def available() -> bool:
    """True when the Rust syscalls backend is loaded (or loadable). The
    direct-syscall launcher branches on this: the Rust-native clone
    child when present, the Python fork child otherwise."""
    return _load_rust_backend() is not None


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


def _rust_mount(
    lib: ctypes.CDLL, source: bytes, target: bytes, fstype: bytes,
    flags: int, data: Optional[bytes] = None,
) -> int:
    """mount(2) via the Rust module: 0 or -errno."""
    src = ctypes.create_string_buffer(source, len(source) + 1)
    tgt = ctypes.create_string_buffer(target, len(target) + 1)
    fst = ctypes.create_string_buffer(fstype, len(fstype) + 1)
    data_buf = None
    data_ptr = None
    if data is not None:
        data_buf = ctypes.create_string_buffer(data, len(data) + 1)
        data_ptr = ctypes.cast(data_buf, ctypes.c_void_p)
    rc = lib.nyrqis_syscalls_mount(
        ctypes.cast(src, ctypes.c_void_p),
        ctypes.cast(tgt, ctypes.c_void_p),
        ctypes.cast(fst, ctypes.c_void_p),
        flags,
        data_ptr,
    )
    return int(rc)


def _rust_mount_proc(lib: ctypes.CDLL) -> int:
    """mount("proc", "/proc", ...) via the Rust module: 0 or -errno.
    No-arg on purpose: called in the container child between fork and
    exec, where no Python allocation may happen."""
    return int(lib.nyrqis_syscalls_mount_proc())


class LaunchArgs(ctypes.Structure):
    """The argument struct for ``nyrqis_syscalls_launch_child`` — the
    Rust-native PID-1 entry of the direct-syscall launcher (ABI 1.2.0).
    Built by the manager BEFORE ``clone``: inside the new user
    namespace ``getuid()`` reports the overflow uid 65534, so the real
    uid/gid to map MUST cross in this struct (the kernel refuses to map
    an id that is not the caller's own). The argv is a NULL-terminated
    array of NUL-terminated strings in Python memory — inherited
    copy-on-write by the clone child, which reads it between clone and
    exec (no allocation)."""

    _fields_ = [
        ("write_fd", ctypes.c_int),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("argc", ctypes.c_size_t),
        ("argv", ctypes.POINTER(ctypes.c_void_p)),
    ]

    @classmethod
    def build(cls, write_fd: int, uid: int, gid: int,
              argv: List[str]) -> "LaunchArgs":
        """Build the struct with a live argv array (kept alive by the
        returned instance). The argv strings live in
        ``create_string_buffer`` copies (stable, owned); the array holds
        their RAW ADDRESSES as ``c_void_p`` — a ``c_char_p`` array would
        re-copy each string on assignment into a temporary-owned buffer
        that dies with the temporary, leaving the clone child's execv
        reading freed memory (EFAULT, exit 126). argc+1 slots: execv
        scans the array for a NULL terminator, and without one the
        kernel reads past the end."""
        bufs = [ctypes.create_string_buffer(a.encode("utf-8")) for a in argv]
        argv_arr = (ctypes.c_void_p * (len(bufs) + 1))()
        for i, buf in enumerate(bufs):
            argv_arr[i] = ctypes.addressof(buf)
        argv_arr[len(bufs)] = None  # the execv NULL terminator
        inst = cls()
        inst.write_fd = write_fd
        inst.uid = uid
        inst.gid = gid
        inst.argc = len(argv)
        inst.argv = argv_arr
        # Keep the buffers alive for the struct's lifetime (the child
        # reads them copy-on-write after clone).
        inst._argv_bufs = bufs  # type: ignore[attr-defined]
        return inst


def _rust_clone(lib: ctypes.CDLL, flags: int, args: LaunchArgs) -> int:
    """clone(2) via the Rust module: the child pid (positive), or
    -errno. The child runs ``nyrqis_syscalls_launch_child`` (the Rust
    entry address is resolved from the loaded library — never a
    Python callback) and never returns to Python."""
    entry_fn = lib.nyrqis_syscalls_launch_child
    # A malformed library must fail cleanly, never segfault: ctypes.cast
    # on a non-ctypes object (e.g. a Mock in a loader test) reads
    # garbage and crashes the process.
    if not isinstance(entry_fn, ctypes._CFuncPtr):
        raise RuntimeError(
            "syscalls: nyrqis_syscalls_launch_child is not a callable"
        )
    entry = ctypes.cast(entry_fn, ctypes.c_void_p)
    rc = lib.nyrqis_syscalls_clone(flags, entry, ctypes.byref(args))
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


def _ctypes_mount_proc() -> int:
    """mount("proc", "/proc", "proc", MS_PROC_MOUNT, NULL) via ctypes:
    0 or -errno."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [
        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_ulong, ctypes.c_void_p,
    ]
    libc.mount.restype = ctypes.c_int
    if libc.mount(b"proc", b"/proc", b"proc", MS_PROC_MOUNT, None) == 0:
        return 0
    return -ctypes.get_errno()

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


def mount(
    source: bytes, target: bytes, fstype: bytes,
    flags: int = MS_PROC_MOUNT, data: Optional[bytes] = None,
) -> int:
    """mount(2): 0 on success or -errno. Rust FFI when the module is
    loaded; ctypes otherwise. See ``sethostname`` for the force rules.
    ``data`` is passed through untouched (NULL when omitted)."""
    lib = _load_rust_backend()
    if lib is not None:
        try:
            return _rust_mount(lib, source, target, fstype, flags, data)
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "syscalls: Rust mount failed (%s: %s); using ctypes",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    return _ctypes_mount_generic(source, target, fstype, flags, data)


def mount_proc() -> int:
    """Mount a fresh procfs at /proc (the container init's view of its
    PID namespace — unshare(1)'s --mount-proc equivalent): 0 or -errno.
    Rust FFI when the module is loaded; ctypes otherwise. The generic
    ``mount`` also routes here for the exact proc mount; this entry
    exists so the container child (post-fork, pre-exec) calls the
    no-argument path that performs zero Python allocation."""
    lib = _load_rust_backend()
    if lib is not None:
        try:
            return _rust_mount_proc(lib)
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "syscalls: Rust mount_proc failed (%s: %s); using ctypes",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    return _ctypes_mount_proc()


def clone(flags: int, args: LaunchArgs) -> int:
    """clone(2) with a Rust child entry point (ABI 1.2.0): returns the
    child's pid. The child runs ``nyrqis_syscalls_launch_child`` — the
    container PID-1 entry (root maps, proc mount, exec the launcher) —
    and never returns to Python, so no Python runs between fork and
    exec on this path.

    Rust-only by design: a raw ``clone(2)`` child cannot run a Python
    callback (no GIL, no interpreter — the FFI contract), so there is
    no ctypes fallback. Callers must check ``available()`` first (the
    direct-syscall launcher uses the Python fork child on crate-less
    hosts). ``flags`` must include ``CLONE_SIGCHLD`` (a zero signal
    byte auto-releases the child). Raises ``OSError`` on -errno and
    ``RuntimeError`` when the crate is absent."""
    lib = _load_rust_backend()
    if lib is None:
        raise RuntimeError(
            "syscalls: clone(2) needs the Rust backend (the Python "
            "fork child is the crate-less path)"
        )
    try:
        rc = _rust_clone(lib, flags, args)
    except Exception as exc:  # noqa: BLE001 - force-aware, like the others
        if _force_enabled():
            raise
        logger.warning(
            "syscalls: Rust clone failed (%s: %s)",
            type(exc).__name__, exc,
        )
        raise RuntimeError("syscalls: clone(2) unavailable") from exc
    if rc > 0:
        return rc
    _raise_rust_error(rc, "clone")  # -errno -> OSError(errno)
    raise AssertionError("unreachable")


def _ctypes_mount_generic(
    source: bytes, target: bytes, fstype: bytes, flags: int,
    data: Optional[bytes],
) -> int:
    """Generic mount(2) via ctypes: 0 or -errno."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [
        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_ulong, ctypes.c_void_p,
    ]
    libc.mount.restype = ctypes.c_int
    # A raw bytes object must never be passed to ctypes.cast directly
    # (TypeError); keep a real buffer alive so the pointer stays valid
    # across the call (same class of bug as the prctl fallback fix).
    data_buf = None
    data_ptr = None
    if data is not None:
        data_buf = ctypes.create_string_buffer(data, len(data) + 1)
        data_ptr = ctypes.cast(data_buf, ctypes.c_void_p)
    if libc.mount(source, target, fstype, flags, data_ptr) == 0:
        return 0
    return -ctypes.get_errno()


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
    "CLONE_NEWNS",
    "CLONE_NEWUTS",
    "CLONE_NEWIPC",
    "CLONE_NEWUSER",
    "CLONE_NEWPID",
    "CLONE_NEWNET",
    "CLONE_SIGCHLD",
    "MS_PROC_MOUNT",
    "LaunchArgs",
    "available",
    "clone",
    "set_hostname",
    "sethostname",
    "prctl",
    "unshare",
    "mount",
    "mount_proc",
]
