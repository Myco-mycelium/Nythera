#!/usr/bin/env python3
"""
container_codec — FFI loader + wiring for the Nyrqis container
launch-plan primitives (ADR-0020 migration priority #5; see
rust/container/README.md).

The pure, well-bounded computations the container manager makes when
launching a container (NPS-017 §4.1 / NPS-010 §7): the launcher argv
(no shell interpolation, FIND-BACKEND-004), the cgroup v1/v2 resource
plan (release_agent hardening, FIND-BACKEND-003), the --map-root-user
uid/gid maps, and the NPS-010 §4 lifecycle state machine. These are
platform-critical execution paths (container launch) that under the
ADR-0020 platform-boundary rule must not depend on the Python
interpreter in their shipped form; this module is the FFI loader, and
the pure-Python floor here is the byte-identical correctness floor.

Wire formats (canonical — the floor produces BYTE-IDENTICAL output,
verified by the differential conformance gate; see the crate README):

    launcher_argv: "NYRQ" | wire_version(1) | u32 argv_count
                   | count × (u32 len + bytes)
    cgroup_plan:   "NYRQ" | wire_version(1) | u32 v1_count
                   | v1_count × (path, pairs)
                     path  = u32 len + bytes
                     pairs = u32 pair_count + pair_count × (u32 klen + key, u32 vlen + val)
                   | u32 v2_count | v2_count × (u32 klen + key, u32 vlen + val)
    root_maps:     "NYRQ" | wire_version(1) | 3 × (u32 len + bytes)
                   [setgroups, uid_map, gid_map]

Contract, mirroring the seccomp/syscalls/nyfs/ipc loaders:

- Search order for the Rust cdylib: ``$NYRQIS_RUST_LIB``, the crate's
  ``target/release/``, then a bare name (honors ``LD_LIBRARY_PATH``).
- ABI-version check against ``MIN_RUST_ABI_VERSION``.
- On ANY load or routing failure: log once and fall back to the
  pure-Python path (unless ``NYRQIS_RUST_FORCE=1``, which turns routing
  failures into errors — the conformance gate's guarantee that every
  call drives the Rust module).
- The Rust module returns 0 on success or a negative value: ``-errno``
  for real failures, ``-4096`` (ERR_INTERNAL) for module failures,
  ``-4097`` (ERR_INVALID_WIRE) for a malformed command flat buffer, and
  ``-4098`` (ERR_INVALID_TRANSITION) for a disallowed NPS-010 §4 state
  pair (``transition_valid`` only). -4096/-4097/-4098 are outside the
  errno range (1..=4095); the loader maps -4097 → ``ValueError`` (the
  floor's exception) and -4098 → ``False`` (an invalid transition is a
  result, not an error).
"""

import ctypes
import logging
import os
import struct
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_RUST_ABI_VERSION = 0x0001_0000  # nyrqis-container 1.0.0

# Module internal error codes (negative i32), outside the errno range.
RUST_ERR_INTERNAL = -4096          # module failure (→ RuntimeError)
RUST_ERR_INVALID_WIRE = -4097      # malformed command flat (→ ValueError)
RUST_ERR_INVALID_TRANSITION = -4098  # NPS-010 §4 pair not allowed (→ False)

_WIRE_VERSION = 1
_MAGIC = b"NYRQ"

_INVALID_WIRE_MSG = "invalid container command flat buffer"

# ContainerState → index (the crate's wire vocabulary): 0 CREATED,
# 1 RUNNING, 2 SUSPENDED, 3 TERMINATED.
STATE_INDEX = {"created": 0, "running": 1, "suspended": 2, "terminated": 3}

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
        here, "..", "rust", "container", "target", "release",
        "libnyrqis_container.so",
    )
    return [crate_target, "libnyrqis_container.so"]


def _force_enabled() -> bool:
    return os.environ.get("NYRQIS_RUST_FORCE") in ("1", "true", "yes")


def _rust_force_error() -> str:
    return (
        "NYRQIS_RUST_FORCE=1 but the Rust container launch-plan backend "
        "is not available (searched: " + ", ".join(_rust_lib_candidates()) + ")"
    )


def _raise_rust_error(code: int, context: str) -> None:
    """Map a negative return from the Rust module to the exception the
    equivalent Python path would raise: ``-errno`` → ``OSError``,
    ``-4096`` (internal) → ``RuntimeError``, ``-4097`` (invalid wire) →
    ``ValueError`` (the floor's exception). -4098 (invalid transition)
    is handled by ``transition_valid`` itself, not here."""
    if code == RUST_ERR_INVALID_WIRE:
        raise ValueError(_INVALID_WIRE_MSG)
    if code == RUST_ERR_INTERNAL:
        raise RuntimeError(f"{context}: Rust container launch-plan: error {code}")
    err = -code
    if 1 <= err <= 4095:
        raise OSError(err, os.strerror(err), context)
    raise RuntimeError(f"{context}: Rust container launch-plan: error {code}")


def _load_rust_backend() -> Optional[ctypes.CDLL]:
    """Locate and load the Rust container launch-plan cdylib, or return
    None. The result is cached. A library whose ABI version is below
    ``MIN_RUST_ABI_VERSION`` is skipped. Never raises: a miss simply
    means "use the Python path"."""
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
            lib.nyrqis_container_version.restype = ctypes.c_uint32
            version = lib.nyrqis_container_version()
        except AttributeError:
            logger.warning(
                "container codec: %s has no nyrqis_container_version symbol; skipping",
                path,
            )
            continue
        if version < MIN_RUST_ABI_VERSION:
            logger.warning(
                "container codec: %s ABI %#x is below required %#x; skipping",
                path, version, MIN_RUST_ABI_VERSION,
            )
            continue
        lib.nyrqis_container_launcher_argv.restype = ctypes.c_int
        lib.nyrqis_container_launcher_argv.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_ubyte,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.nyrqis_container_cgroup_plan.restype = ctypes.c_int
        lib.nyrqis_container_cgroup_plan.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_uint64, ctypes.c_uint64,
            ctypes.c_int64, ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.nyrqis_container_root_maps.restype = ctypes.c_int
        lib.nyrqis_container_root_maps.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.nyrqis_container_transition_valid.restype = ctypes.c_int
        lib.nyrqis_container_transition_valid.argtypes = [
            ctypes.c_ubyte, ctypes.c_ubyte,
        ]
        lib.nyrqis_container_free.restype = None
        lib.nyrqis_container_free.argtypes = [ctypes.c_void_p]
        _RUST_LIB = lib
        logger.info("container codec: using Rust backend (%s)", path)
        return lib
    return None


def _buf(data: bytes):
    """A non-null ctypes buffer for ``data`` (1-byte minimum so even
    empty fields pass a valid pointer)."""
    return ctypes.cast(ctypes.create_string_buffer(data, len(data) + 1),
                       ctypes.c_void_p)


# ---------------------------------------------------------------------------
# Rust FFI entry points (called only when the module is loaded)
# ---------------------------------------------------------------------------

def _rust_launcher_argv(
    lib: ctypes.CDLL,
    python_path: bytes, launcher_path: bytes, hostname: bytes,
    policy_path: bytes, default_deny: int, command_flat: bytes,
) -> bytes:
    out_ptr = ctypes.c_void_p()
    out_len = ctypes.c_size_t()
    rc = lib.nyrqis_container_launcher_argv(
        _buf(python_path), len(python_path),
        _buf(launcher_path), len(launcher_path),
        _buf(hostname), len(hostname),
        _buf(policy_path), len(policy_path),
        int(default_deny),
        _buf(command_flat), len(command_flat),
        ctypes.byref(out_ptr), ctypes.byref(out_len),
    )
    if rc != 0:
        _raise_rust_error(int(rc), "launcher_argv")
    try:
        return ctypes.string_at(out_ptr, out_len.value)
    finally:
        lib.nyrqis_container_free(out_ptr)


def _rust_cgroup_plan(
    lib: ctypes.CDLL,
    container_id: bytes, memory_mb: int, pid_limit: int,
    cpu_quota_us: Optional[int], cpu_period_us: int,
) -> bytes:
    out_ptr = ctypes.c_void_p()
    out_len = ctypes.c_size_t()
    rc = lib.nyrqis_container_cgroup_plan(
        _buf(container_id), len(container_id),
        int(memory_mb), int(pid_limit),
        int(cpu_quota_us if cpu_quota_us is not None else -1),
        int(cpu_period_us),
        ctypes.byref(out_ptr), ctypes.byref(out_len),
    )
    if rc != 0:
        _raise_rust_error(int(rc), "cgroup_plan")
    try:
        return ctypes.string_at(out_ptr, out_len.value)
    finally:
        lib.nyrqis_container_free(out_ptr)


def _rust_root_maps(lib: ctypes.CDLL, uid: int, gid: int) -> bytes:
    out_ptr = ctypes.c_void_p()
    out_len = ctypes.c_size_t()
    rc = lib.nyrqis_container_root_maps(
        int(uid), int(gid),
        ctypes.byref(out_ptr), ctypes.byref(out_len),
    )
    if rc != 0:
        _raise_rust_error(int(rc), "root_maps")
    try:
        return ctypes.string_at(out_ptr, out_len.value)
    finally:
        lib.nyrqis_container_free(out_ptr)


# ---------------------------------------------------------------------------
# Pure-Python floor (the correctness floor — byte-identical output)
# ---------------------------------------------------------------------------

def _py_launcher_argv(
    python_path: bytes, launcher_path: bytes, hostname: bytes,
    policy_path: bytes, default_deny: int, command_flat: bytes,
) -> bytes:
    """The exact argv the manager hands to ``os.execv`` — mirroring
    ``ContainerManager._launcher_args`` (FIND-BACKEND-004: hostname and
    command are argv entries, never shell-interpolated)."""
    entries: List[bytes] = [
        python_path, launcher_path, b"--hostname", hostname,
    ]
    if policy_path:
        entries += [b"--policy-file", policy_path]
        if default_deny:
            entries += [b"--default-deny"]
    entries += [b"--"]
    entries += split_command_flat(command_flat)
    out = _MAGIC + bytes([_WIRE_VERSION]) + struct.pack("<I", len(entries))
    for e in entries:
        out += struct.pack("<I", len(e)) + e
    return out


def _py_cgroup_plan(
    container_id: bytes, memory_mb: int, pid_limit: int,
    cpu_quota_us: Optional[int], cpu_period_us: int,
) -> bytes:
    """The v1 hierarchy plan (FIND-BACKEND-003: memory cgroup carries
    ``notify_on_release=0``) and v2 unified settings (NPS-010 §7)."""
    mem_str = str(memory_mb * 1024 * 1024).encode()
    v1 = [
        (
            b"/sys/fs/cgroup/memory/" + container_id,
            [
                (b"memory.limit_in_bytes", mem_str),
                (b"notify_on_release", b"0"),
            ],
        ),
        (
            b"/sys/fs/cgroup/pids/" + container_id,
            [(b"pids.max", str(pid_limit).encode())],
        ),
    ]
    v2 = [
        (b"memory.max", mem_str),
        (b"pids.max", str(pid_limit).encode()),
    ]
    if cpu_quota_us is not None and cpu_quota_us >= 0:
        v2.append((b"cpu.max", f"{cpu_quota_us} {cpu_period_us}".encode()))

    out = _MAGIC + bytes([_WIRE_VERSION]) + struct.pack("<I", len(v1))
    for path, pairs in v1:
        out += struct.pack("<I", len(path)) + path
        out += struct.pack("<I", len(pairs))
        for key, val in pairs:
            out += struct.pack("<I", len(key)) + key
            out += struct.pack("<I", len(val)) + val
    out += struct.pack("<I", len(v2))
    for key, val in v2:
        out += struct.pack("<I", len(key)) + key
        out += struct.pack("<I", len(val)) + val
    return out


def _py_root_maps(uid: int, gid: int) -> bytes:
    """The ``--map-root-user`` map contents (``setgroups=deny`` first,
    then the caller mapped to root)."""
    contents = [
        b"deny\n",
        f"0 {uid} 1\n".encode(),
        f"0 {gid} 1\n".encode(),
    ]
    out = _MAGIC + bytes([_WIRE_VERSION])
    for c in contents:
        out += struct.pack("<I", len(c)) + c
    return out


def _py_transition_valid(from_state: str, to_state: str) -> bool:
    """The NPS-010 §4 lifecycle state machine (the container.py
    reference)."""
    try:
        f = STATE_INDEX[from_state]
        t = STATE_INDEX[to_state]
    except KeyError:
        raise ValueError(f"unknown container state: {from_state} → {to_state}")
    return (f, t) in ((0, 1), (1, 2), (1, 3), (2, 1), (2, 3))


# ---------------------------------------------------------------------------
# Wire decoders (shared by both paths)
# ---------------------------------------------------------------------------

def _take_u32(buf: bytes, pos: List[int]) -> int:
    if pos[0] + 4 > len(buf):
        raise ValueError(_INVALID_WIRE_MSG)
    (v,) = struct.unpack_from("<I", buf, pos[0])
    pos[0] += 4
    return v


def _take_str(buf: bytes, pos: List[int]) -> bytes:
    length = _take_u32(buf, pos)
    if pos[0] + length > len(buf):
        raise ValueError(_INVALID_WIRE_MSG)
    field = buf[pos[0]:pos[0] + length]
    pos[0] += length
    return field


def _decode_launcher_argv(wire: bytes) -> List[str]:
    """Decode a launcher-argv wire into the argv list."""
    if len(wire) < 9 or wire[:4] != _MAGIC or wire[4] != _WIRE_VERSION:
        raise ValueError(_INVALID_WIRE_MSG)
    pos = [5]
    count = _take_u32(wire, pos)
    argv = []
    for _ in range(count):
        argv.append(_take_str(wire, pos).decode("utf-8"))
    if pos[0] != len(wire):
        raise ValueError(_INVALID_WIRE_MSG)
    return argv


def _decode_cgroup_plan(wire: bytes) -> Dict:
    """Decode a cgroup-plan wire into ``{"v1": [(path, {key: val})],
    "v2": [(key, val)]}``."""
    if len(wire) < 5 or wire[:4] != _MAGIC or wire[4] != _WIRE_VERSION:
        raise ValueError(_INVALID_WIRE_MSG)
    pos = [5]
    v1 = []
    v1_count = _take_u32(wire, pos)
    for _ in range(v1_count):
        path = _take_str(wire, pos).decode("utf-8")
        pair_count = _take_u32(wire, pos)
        pairs = {}
        for _ in range(pair_count):
            key = _take_str(wire, pos).decode("utf-8")
            val = _take_str(wire, pos).decode("utf-8")
            pairs[key] = val
        v1.append((path, pairs))
    v2 = []
    v2_count = _take_u32(wire, pos)
    for _ in range(v2_count):
        key = _take_str(wire, pos).decode("utf-8")
        val = _take_str(wire, pos).decode("utf-8")
        v2.append((key, val))
    if pos[0] != len(wire):
        raise ValueError(_INVALID_WIRE_MSG)
    return {"v1": v1, "v2": v2}


def _decode_root_maps(wire: bytes) -> Tuple[bytes, bytes, bytes]:
    """Decode a root-maps wire into (setgroups, uid_map, gid_map)."""
    if len(wire) < 5 or wire[:4] != _MAGIC or wire[4] != _WIRE_VERSION:
        raise ValueError(_INVALID_WIRE_MSG)
    pos = [5]
    contents = tuple(_take_str(wire, pos) for _ in range(3))
    if pos[0] != len(wire):
        raise ValueError(_INVALID_WIRE_MSG)
    return contents  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Public surface (FFI first, Python fallback, force-aware)
# ---------------------------------------------------------------------------

def launcher_argv(
    python_path: str, launcher_path: str, hostname: str,
    policy_path: str, default_deny: bool, command: List[str],
) -> List[str]:
    """The container's launcher argv (FIND-BACKEND-004): the exact argv
    handed to ``os.execv`` inside the new namespaces. Rust FFI when the
    module is loaded; the pure-Python floor otherwise — byte-identical
    output."""
    command_flat = build_command_flat(command)
    lib = _load_rust_backend()
    if lib is not None:
        try:
            wire = _rust_launcher_argv(
                lib,
                python_path.encode("utf-8"),
                launcher_path.encode("utf-8"),
                hostname.encode("utf-8"),
                policy_path.encode("utf-8"),
                int(default_deny),
                command_flat,
            )
            return _decode_launcher_argv(wire)
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "container codec: Rust launcher_argv failed (%s: %s); using floor",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    wire = _py_launcher_argv(
        python_path.encode("utf-8"),
        launcher_path.encode("utf-8"),
        hostname.encode("utf-8"),
        policy_path.encode("utf-8"),
        int(default_deny),
        command_flat,
    )
    return _decode_launcher_argv(wire)


def cgroup_plan(
    container_id: str, memory_mb: int, pid_limit: int,
    cpu_quota_us: Optional[int] = None, cpu_period_us: int = 100000,
) -> Dict:
    """The container's cgroup resource plan: ``{"v1": [(path, {file:
    content})], "v2": [(file, content)]}``. Rust FFI when the module is
    loaded; the pure-Python floor otherwise — byte-identical output.
    ``cpu_quota_us=None`` (or negative) means no CPU quota (no
    ``cpu.max``)."""
    container_id_bytes = container_id.encode("utf-8")
    lib = _load_rust_backend()
    if lib is not None:
        try:
            wire = _rust_cgroup_plan(
                lib, container_id_bytes, memory_mb, pid_limit,
                cpu_quota_us, cpu_period_us,
            )
            return _decode_cgroup_plan(wire)
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "container codec: Rust cgroup_plan failed (%s: %s); using floor",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    wire = _py_cgroup_plan(
        container_id_bytes, memory_mb, pid_limit,
        cpu_quota_us, cpu_period_us,
    )
    return _decode_cgroup_plan(wire)


def root_maps(uid: int, gid: int) -> Tuple[bytes, bytes, bytes]:
    """The ``--map-root-user`` map contents (setgroups, uid_map,
    gid_map) as bytes, ready to write to ``/proc/self/*``. Rust FFI when
    the module is loaded; the pure-Python floor otherwise — byte-
    identical output."""
    lib = _load_rust_backend()
    if lib is not None:
        try:
            wire = _rust_root_maps(lib, uid, gid)
            return _decode_root_maps(wire)
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "container codec: Rust root_maps failed (%s: %s); using floor",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    return _decode_root_maps(_py_root_maps(uid, gid))


def transition_valid(from_state: str, to_state: str) -> bool:
    """The NPS-010 §4 lifecycle state machine: is ``from_state →
    ``to_state`` a legal transition? (State names are the
    ``ContainerState`` enum values.) Rust FFI when the module is loaded
    (0 = valid, -4098 = invalid pair, -22 = out-of-range state); the
    pure-Python floor otherwise."""
    try:
        f = STATE_INDEX[from_state]
        t = STATE_INDEX[to_state]
    except KeyError:
        raise ValueError(f"unknown container state: {from_state} → {to_state}")
    lib = _load_rust_backend()
    if lib is not None:
        try:
            rc = int(lib.nyrqis_container_transition_valid(f, t))
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "container codec: Rust transition_valid failed (%s: %s); using floor",
                type(exc).__name__, exc,
            )
            rc = None
        if rc is not None:
            if rc == 0:
                return True
            if rc == RUST_ERR_INVALID_TRANSITION:
                return False
            if rc == -22:  # ERR_INVALID_ARGS — out-of-range state (a caller bug)
                raise ValueError(
                    f"invalid container state index: {f} → {t}"
                )
            _raise_rust_error(int(rc), "transition_valid")
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    return _py_transition_valid(from_state, to_state)


def build_command_flat(command: List[str]) -> bytes:
    """Frame the command list as the wire's flat buffer
    (``[u32 len + bytes]*``)."""
    out = bytearray()
    for entry in command:
        entry_bytes = entry.encode("utf-8")
        out += struct.pack("<I", len(entry_bytes))
        out += entry_bytes
    return bytes(out)


def split_command_flat(command_flat: bytes) -> List[bytes]:
    """Split a flat command buffer back into the entry list."""
    entries: List[bytes] = []
    pos = [0]
    while pos[0] < len(command_flat):
        length = _take_u32(command_flat, pos)
        if pos[0] + length > len(command_flat):
            raise ValueError(_INVALID_WIRE_MSG)
        entries.append(command_flat[pos[0]:pos[0] + length])
        pos[0] += length
    return entries


__all__ = [
    "MIN_RUST_ABI_VERSION",
    "STATE_INDEX",
    "launcher_argv",
    "cgroup_plan",
    "root_maps",
    "transition_valid",
    "build_command_flat",
    "split_command_flat",
]
