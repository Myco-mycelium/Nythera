#!/usr/bin/env python3
"""
ipc_codec — FFI loader + wiring for the Nyrqis IPC message wire codec
(ADR-0020 migration priority #4; see rust/ipc/README.md).

The binary framing for ``IPCMessage`` (NPS-003 §3, NPS-017 §4.3): the
message serialization a real cross-process transport will sit on. The
Python IPC semantics are stable and benchmarked (BENCHMARK_RESULTS.md
§1), so this module extracts the transport's serialization boundary
into the memory-safe Rust parser — the parsing trust boundary of future
cross-container IPC.

Wire format (canonical — the pure-Python floor here produces
BYTE-IDENTICAL output, verified by the differential conformance gate):

    "NYRQ" | wire_version(1) | message_type(1) | timestamp(f64 LE)
    | u32-len id | u32-len sender | u32-len receiver | u32-len reply_to
    | u32-len payload | u32-len caps_flat ([u32 len + bytes]*)
    | u32-len metadata (opaque — the caller's JSON blob)

``metadata`` is opaque on the wire: the caller serializes it (JSON),
so no dict-ordering contract crosses the boundary.

Contract, mirroring the seccomp/syscalls/nyfs loaders:

- Search order for the Rust cdylib: ``$NYRQIS_RUST_LIB``, the crate's
  ``target/release/``, then a bare name (honors ``LD_LIBRARY_PATH``).
- ABI-version check against ``MIN_RUST_ABI_VERSION``.
- On ANY load or routing failure: log once and fall back to the
  pure-Python path (unless ``NYRQIS_RUST_FORCE=1``, which turns routing
  failures into errors — the conformance gate's guarantee that every
  call drives the Rust module).
- The Rust module returns 0 on success or a negative value: ``-errno``
  for real failures, ``-4096`` (ERR_INTERNAL) for module failures, and
  ``-4097`` (ERR_INVALID_WIRE) for a malformed/oversized message.
  ``-4096``/``-4097`` are outside the errno range (1..=4095) so the
  ``-errno → OSError`` mapping can never misreport them; the loader maps
  ``-4097`` → ``ValueError("invalid IPC message wire format")`` — the
  exact exception the pure-Python parser raises.

Bounds (defense-in-depth): the string fields (id/sender/receiver/
reply_to) are capped at 1 MiB and the whole message at 16 MiB
(payload/caps/metadata share the 16 MiB bound), enforced by the Rust
parser and pinned by its unit tests. The pure-Python floor is the
byte-identical correctness floor and trusts its caller (the reference
transport keeps messages small); the Rust module is the bounded parser.

Output-buffer ownership (the seccomp/nyfs contract): the encoder's
output and the decoder's ``IpcMessageView`` are ``libc::malloc``'d by
the crate and freed with ``nyrqis_ipc_free``.
"""

import ctypes
import logging
import os
import struct
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_RUST_ABI_VERSION = 0x0001_0000  # nyrqis-ipc 1.0.0

# Module internal error codes (negative i32), outside the errno range.
RUST_ERR_INTERNAL = -4096      # module failure (→ RuntimeError)
RUST_ERR_INVALID_WIRE = -4097  # malformed/oversized message (→ ValueError)

_WIRE_VERSION = 1
_MAGIC = b"NYRQ"

_INVALID_WIRE_MSG = "invalid IPC message wire format"

_RUST_LIB: Optional[ctypes.CDLL] = None
_RUST_LIB_CHECKED = False


def _rust_lib_candidates() -> list:
    """Search order for the Rust cdylib: ``$NYRQIS_RUST_LIB``, the
    crate's ``target/release/``, then a bare name (honors
    ``LD_LIBRARY_PATH``)."""
    override = os.environ.get("NYRQIS_RUST_LIB")
    if override:
        return [override]
    here = os.path.dirname(os.path.abspath(__file__))
    crate_target = os.path.join(
        here, "..", "rust", "ipc", "target", "release",
        "libnyrqis_ipc.so",
    )
    return [crate_target, "libnyrqis_ipc.so"]


def _force_enabled() -> bool:
    return os.environ.get("NYRQIS_RUST_FORCE") in ("1", "true", "yes")


def _rust_force_error() -> str:
    return (
        "NYRQIS_RUST_FORCE=1 but the Rust IPC wire codec backend is not "
        "available (searched: " + ", ".join(_rust_lib_candidates()) + ")"
    )


def _raise_rust_error(code: int, context: str) -> None:
    """Map a negative return from the Rust module to the exception the
    equivalent Python path would raise: ``-errno`` → ``OSError``,
    ``-4096`` (internal) → ``RuntimeError``, ``-4097`` (invalid wire) →
    ``ValueError`` (the floor parser's exception)."""
    if code == RUST_ERR_INVALID_WIRE:
        raise ValueError(_INVALID_WIRE_MSG)
    if code == RUST_ERR_INTERNAL:
        raise RuntimeError(f"{context}: Rust IPC wire codec: error {code}")
    err = -code
    if 1 <= err <= 4095:
        raise OSError(err, os.strerror(err), context)
    raise RuntimeError(f"{context}: Rust IPC wire codec: error {code}")


def _load_rust_backend() -> Optional[ctypes.CDLL]:
    """Locate and load the Rust IPC wire codec cdylib, or return None.

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
            lib.nyrqis_ipc_version.restype = ctypes.c_uint32
            version = lib.nyrqis_ipc_version()
        except AttributeError:
            logger.warning(
                "ipc codec: %s has no nyrqis_ipc_version symbol; skipping",
                path,
            )
            continue
        if version < MIN_RUST_ABI_VERSION:
            logger.warning(
                "ipc codec: %s ABI %#x is below required %#x; skipping",
                path, version, MIN_RUST_ABI_VERSION,
            )
            continue
        lib.nyrqis_ipc_encode.restype = ctypes.c_int
        lib.nyrqis_ipc_encode.argtypes = [
            ctypes.c_ubyte, ctypes.c_double,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.nyrqis_ipc_decode.restype = ctypes.c_int
        lib.nyrqis_ipc_decode.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.nyrqis_ipc_free.restype = None
        lib.nyrqis_ipc_free.argtypes = [ctypes.c_void_p]
        _RUST_LIB = lib
        logger.info("ipc codec: using Rust backend (%s)", path)
        return lib
    return None


class _IpcMessageView(ctypes.Structure):
    """Mirror of the crate's ``#[repr(C)] IpcMessageView`` — the decoded
    fields with pointers into one malloc'd block (freed via
    ``nyrqis_ipc_free``)."""
    _fields_ = [
        ("message_type", ctypes.c_ubyte),
        ("timestamp", ctypes.c_double),
        ("message_id", ctypes.c_void_p), ("message_id_len", ctypes.c_uint32),
        ("sender_id", ctypes.c_void_p), ("sender_id_len", ctypes.c_uint32),
        ("receiver_id", ctypes.c_void_p), ("receiver_id_len", ctypes.c_uint32),
        ("reply_to", ctypes.c_void_p), ("reply_to_len", ctypes.c_uint32),
        ("payload", ctypes.c_void_p), ("payload_len", ctypes.c_uint32),
        ("caps_flat", ctypes.c_void_p), ("caps_flat_len", ctypes.c_uint32),
        ("metadata", ctypes.c_void_p), ("metadata_len", ctypes.c_uint32),
    ]


# ---------------------------------------------------------------------------
# Rust FFI entry points (called only when the module is loaded)
# ---------------------------------------------------------------------------

def _rust_encode(
    lib: ctypes.CDLL,
    message_type: int, timestamp: float,
    message_id: bytes, sender_id: bytes, receiver_id: bytes, reply_to: bytes,
    payload: bytes, caps_flat: bytes, metadata: bytes,
) -> bytes:
    out_ptr = ctypes.c_void_p()
    out_len = ctypes.c_size_t()
    rc = lib.nyrqis_ipc_encode(
        int(message_type), timestamp,
        _buf(message_id), len(message_id),
        _buf(sender_id), len(sender_id),
        _buf(receiver_id), len(receiver_id),
        _buf(reply_to), len(reply_to),
        _buf(payload), len(payload),
        _buf(caps_flat), len(caps_flat),
        _buf(metadata), len(metadata),
        ctypes.byref(out_ptr), ctypes.byref(out_len),
    )
    if rc != 0:
        _raise_rust_error(int(rc), "ipc_encode")
    try:
        return ctypes.string_at(out_ptr, out_len.value)
    finally:
        lib.nyrqis_ipc_free(out_ptr)


def _rust_decode(lib: ctypes.CDLL, buf: bytes) -> Dict:
    view_ptr = ctypes.c_void_p()
    rc = lib.nyrqis_ipc_decode(
        _buf(buf), len(buf), ctypes.byref(view_ptr)
    )
    if rc != 0:
        _raise_rust_error(int(rc), "ipc_decode")
    view = None
    try:
        view = ctypes.cast(view_ptr, ctypes.POINTER(_IpcMessageView)).contents
        return {
            "message_type": int(view.message_type),
            "timestamp": float(view.timestamp),
            "message_id": ctypes.string_at(view.message_id, view.message_id_len),
            "sender_id": ctypes.string_at(view.sender_id, view.sender_id_len),
            "receiver_id": ctypes.string_at(view.receiver_id, view.receiver_id_len),
            "reply_to": ctypes.string_at(view.reply_to, view.reply_to_len),
            "payload": ctypes.string_at(view.payload, view.payload_len),
            "caps_flat": ctypes.string_at(view.caps_flat, view.caps_flat_len),
            "metadata": ctypes.string_at(view.metadata, view.metadata_len),
        }
    finally:
        if view is not None:
            lib.nyrqis_ipc_free(view_ptr)


def _buf(data: bytes):
    """A non-null ctypes buffer for ``data`` (1-byte minimum so even
    empty fields pass a valid pointer)."""
    return ctypes.cast(ctypes.create_string_buffer(data, len(data) + 1),
                       ctypes.c_void_p)


# ---------------------------------------------------------------------------
# Pure-Python floor (the correctness floor — byte-identical output)
# ---------------------------------------------------------------------------

def _py_encode(
    message_type: int, timestamp: float,
    message_id: bytes, sender_id: bytes, receiver_id: bytes, reply_to: bytes,
    payload: bytes, caps_flat: bytes, metadata: bytes,
) -> bytes:
    out = struct.pack("<4sBBd", _MAGIC, _WIRE_VERSION, message_type, timestamp)
    for field in (message_id, sender_id, receiver_id, reply_to):
        out += struct.pack("<I", len(field)) + field
    for field in (payload, caps_flat, metadata):
        out += struct.pack("<I", len(field)) + field
    return out


def _py_decode(buf: bytes) -> Dict:
    """Parse a wire buffer, raising ``ValueError(_INVALID_WIRE_MSG)`` on
    any malformation — mirroring the Rust parser's contract exactly
    (bad magic/version/type, truncated fields, trailing bytes)."""
    if len(buf) < 14:
        raise ValueError(_INVALID_WIRE_MSG)
    magic, version, message_type, timestamp = struct.unpack_from(
        "<4sBBd", buf, 0
    )
    if magic != _MAGIC or version != _WIRE_VERSION or message_type > 4:
        raise ValueError(_INVALID_WIRE_MSG)
    pos = 14

    def take() -> bytes:
        nonlocal pos
        if pos + 4 > len(buf):
            raise ValueError(_INVALID_WIRE_MSG)
        (length,) = struct.unpack_from("<I", buf, pos)
        pos += 4
        if pos + length > len(buf):
            raise ValueError(_INVALID_WIRE_MSG)
        field = buf[pos:pos + length]
        pos += length
        return field

    fields = {
        "message_type": message_type,
        "timestamp": timestamp,
        "message_id": take(),
        "sender_id": take(),
        "receiver_id": take(),
        "reply_to": take(),
        "payload": take(),
        "caps_flat": take(),
        "metadata": take(),
    }
    if pos != len(buf):
        raise ValueError(_INVALID_WIRE_MSG)
    return fields


# ---------------------------------------------------------------------------
# Public surface (FFI first, Python fallback, force-aware)
# ---------------------------------------------------------------------------

def encode(
    message_type: int, timestamp: float,
    message_id: str, sender_id: str, receiver_id: str,
    reply_to: Optional[str], payload: bytes,
    capabilities: List[str], metadata_blob: bytes,
) -> bytes:
    """Serialize a message to the canonical wire format.

    ``message_type`` is the IPCMessageType index (0 send, 1 receive,
    2 call, 3 reply, 4 notify). ``metadata_blob`` is opaque on the wire
    (the caller's JSON bytes). Rust FFI when the module is loaded; the
    pure-Python ``struct`` encoder otherwise — byte-identical output.
    """
    reply_to_bytes = reply_to.encode("utf-8") if reply_to else b""
    caps_flat = build_caps_flat(capabilities)
    lib = _load_rust_backend()
    if lib is not None:
        try:
            return _rust_encode(
                lib, message_type, timestamp,
                message_id.encode("utf-8"), sender_id.encode("utf-8"),
                receiver_id.encode("utf-8"), reply_to_bytes,
                payload, caps_flat, metadata_blob,
            )
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "ipc codec: Rust encode failed (%s: %s); using struct floor",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    return _py_encode(
        message_type, timestamp,
        message_id.encode("utf-8"), sender_id.encode("utf-8"),
        receiver_id.encode("utf-8"), reply_to_bytes,
        payload, caps_flat, metadata_blob,
    )


def decode(buf: bytes) -> Dict:
    """Parse a wire buffer into a field dict.

    Raises ``ValueError("invalid IPC message wire format")`` on a
    malformed message — the same exception the pure-Python parser
    raises. Rust FFI when the module is loaded; the ``struct`` parser
    otherwise.
    """
    lib = _load_rust_backend()
    if lib is not None:
        try:
            return _rust_decode(lib, buf)
        except ValueError:
            raise  # invalid wire — identical semantics to the floor
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "ipc codec: Rust decode failed (%s: %s); using struct floor",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    return _py_decode(buf)


def build_caps_flat(capabilities: List[str]) -> bytes:
    """Frame the capability list as ``[u32 cap_len + cap bytes]*`` (the
    wire's opaque flat buffer)."""
    out = bytearray()
    for cap in capabilities:
        cap_bytes = cap.encode("utf-8")
        out += struct.pack("<I", len(cap_bytes))
        out += cap_bytes
    return bytes(out)


def split_caps_flat(caps_flat: bytes) -> List[str]:
    """Split a flat capabilities buffer back into the capability list."""
    caps: List[str] = []
    pos = 0
    while pos < len(caps_flat):
        if pos + 4 > len(caps_flat):
            raise ValueError(_INVALID_WIRE_MSG)
        (length,) = struct.unpack_from("<I", caps_flat, pos)
        pos += 4
        if pos + length > len(caps_flat):
            raise ValueError(_INVALID_WIRE_MSG)
        caps.append(caps_flat[pos:pos + length].decode("utf-8"))
        pos += length
    return caps


__all__ = [
    "MIN_RUST_ABI_VERSION",
    "encode",
    "decode",
    "build_caps_flat",
    "split_caps_flat",
]
