#!/usr/bin/env python3
"""
nyfs_codec — FFI loader + wiring for the Nyrqis NyFS block codec
(ADR-0020 migration priority #3; see rust/nyfs/README.md).

The two hot-path primitives of NyFS storage (NPS-004 §4, ADR-0007):

- ``checksum(data)`` — SHA-256 of the UNCOMPRESSED block data (the
  per-block integrity checksum; verified on every read).
- ``compress(data, level)`` / ``decompress_verify(compressed,
  expected_checksum)`` — Zstandard at the block level, with the read
  path verifying the checksum before the payload is returned (the
  ``ValueError`` on mismatch mirrors the pure-Python floor).

Benchmark evidence (tests/BENCHMARK_RESULTS.md §5) shows the read-path
verification dominates NyFS read cost and per-block compress dominates
write cost, so these primitives move into the memory-safe Rust module.
This loader routes calls there when the cdylib is present and falls
back to the pure-Python implementation (``hashlib`` + the ``zstandard``
module) otherwise — the Python path remains the correctness floor, so
the tests keep passing unchanged either way.

Contract, mirroring ``backend/seccomp.py``'s loader:

- Search order for the Rust cdylib: ``$NYRQIS_RUST_LIB``, the crate's
  ``target/release/``, then a bare name (honors ``LD_LIBRARY_PATH``).
- ABI-version check against ``MIN_RUST_ABI_VERSION``.
- On ANY load or routing failure: log once and fall back to the
  pure-Python path (unless ``NYRQIS_RUST_FORCE=1``, which turns routing
  failures into errors — the conformance gate that proves every call
  drives the Rust module).
- The Rust module returns 0 on success or a negative value: ``-errno``
  for real failures, ``-4096`` (ERR_INTERNAL) for its own failures, and
  ``-4097`` (ERR_CHECKSUM) for data-integrity failures. ``-4096`` and
  ``-4097`` are deliberately OUTSIDE the errno range (1..=4095) so the
  ``-errno → OSError`` mapping can never misreport them as kernel
  errors. The loader maps ``-4096`` → ``RuntimeError`` and ``-4097`` →
  ``ValueError("Block checksum verification failed")`` — the exact
  exception the pure-Python path raises on a mismatch.

Output-buffer ownership (the seccomp contract): ``zstd_compress`` and
``zstd_decompress_verify`` return ``libc::malloc``'d buffers that the
caller releases with ``nyrqis_nyfs_free``.
"""

import ctypes
import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

MIN_RUST_ABI_VERSION = 0x0001_0000  # nyrqis-nyfs 1.0.0

# Module internal error codes (negative i32), outside the errno range.
RUST_ERR_INTERNAL = -4096   # module failure (→ RuntimeError)
RUST_ERR_CHECKSUM = -4097   # data integrity failure (→ ValueError)

# The digest buffer the FFI writes is 32 bytes (SHA-256).
_DIGEST_BYTES = 32

# Match the pure-Python floor's mismatch exception (fuse/nyfs.py).
_CHECKSUM_MISMATCH_MSG = "Block checksum verification failed"

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
        here, "..", "rust", "nyfs", "target", "release",
        "libnyrqis_nyfs.so",
    )
    return [crate_target, "libnyrqis_nyfs.so"]


def _force_enabled() -> bool:
    return os.environ.get("NYRQIS_RUST_FORCE") in ("1", "true", "yes")


def _rust_force_error() -> str:
    return (
        "NYRQIS_RUST_FORCE=1 but the Rust NyFS codec backend is not "
        "available (searched: " + ", ".join(_rust_lib_candidates()) + ")"
    )


def _raise_rust_error(code: int, context: str) -> None:
    """Map a negative return from the Rust module to the exception the
    equivalent Python path would raise: ``-errno`` → ``OSError``,
    ``-4096`` (internal) → ``RuntimeError``, ``-4097`` (checksum
    mismatch) → ``ValueError`` (the floor's integrity exception)."""
    if code == RUST_ERR_CHECKSUM:
        raise ValueError(_CHECKSUM_MISMATCH_MSG)
    if code == RUST_ERR_INTERNAL:
        raise RuntimeError(f"{context}: Rust NyFS codec backend: error {code}")
    err = -code
    if 1 <= err <= 4095:
        raise OSError(err, os.strerror(err), context)
    raise RuntimeError(f"{context}: Rust NyFS codec backend: error {code}")


def _load_rust_backend() -> Optional[ctypes.CDLL]:
    """Locate and load the Rust NyFS codec cdylib, or return None.

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
            lib.nyrqis_nyfs_version.restype = ctypes.c_uint32
            version = lib.nyrqis_nyfs_version()
        except AttributeError:
            logger.warning(
                "nyfs codec: %s has no nyrqis_nyfs_version symbol; skipping",
                path,
            )
            continue
        if version < MIN_RUST_ABI_VERSION:
            logger.warning(
                "nyfs codec: %s ABI %#x is below required %#x; skipping",
                path, version, MIN_RUST_ABI_VERSION,
            )
            continue
        lib.nyrqis_nyfs_sha256.restype = ctypes.c_int
        lib.nyrqis_nyfs_sha256.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
        ]
        lib.nyrqis_nyfs_zstd_compress.restype = ctypes.c_int
        lib.nyrqis_nyfs_zstd_compress.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.nyrqis_nyfs_zstd_decompress_verify.restype = ctypes.c_int
        lib.nyrqis_nyfs_zstd_decompress_verify.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.nyrqis_nyfs_free.restype = None
        lib.nyrqis_nyfs_free.argtypes = [ctypes.c_void_p]
        _RUST_LIB = lib
        logger.info("nyfs codec: using Rust backend (%s)", path)
        return lib
    return None


# ---------------------------------------------------------------------------
# Rust FFI entry points (called only when the module is loaded)
# ---------------------------------------------------------------------------

def _rust_sha256(lib: ctypes.CDLL, data: bytes) -> bytes:
    """SHA-256 via the Rust module: raw 32-byte digest."""
    buf = ctypes.create_string_buffer(data, len(data) + 1)
    digest_buf = ctypes.create_string_buffer(_DIGEST_BYTES)
    rc = lib.nyrqis_nyfs_sha256(
        ctypes.cast(buf, ctypes.c_void_p), len(data),
        ctypes.cast(digest_buf, ctypes.c_void_p),
    )
    if rc != 0:
        _raise_rust_error(int(rc), "sha256")
    return digest_buf.raw[: _DIGEST_BYTES]


def _rust_zstd_compress(
    lib: ctypes.CDLL, data: bytes, level: int
) -> bytes:
    """Zstandard compress via the Rust module (malloc'd output, freed
    here)."""
    buf = ctypes.create_string_buffer(data, len(data) + 1)
    out_ptr = ctypes.c_void_p()
    out_len = ctypes.c_size_t()
    rc = lib.nyrqis_nyfs_zstd_compress(
        ctypes.cast(buf, ctypes.c_void_p), len(data), int(level),
        ctypes.byref(out_ptr), ctypes.byref(out_len),
    )
    if rc != 0:
        _raise_rust_error(int(rc), "zstd_compress")
    try:
        return ctypes.string_at(out_ptr, out_len.value)
    finally:
        lib.nyrqis_nyfs_free(out_ptr)


def _rust_zstd_decompress_verify(
    lib: ctypes.CDLL, compressed: bytes, expected_digest: bytes
) -> bytes:
    """Decompress + verify via the Rust module (malloc'd output, freed
    here). Raises ValueError on checksum mismatch."""
    buf = ctypes.create_string_buffer(compressed, len(compressed) + 1)
    digest_buf = ctypes.create_string_buffer(expected_digest, _DIGEST_BYTES)
    out_ptr = ctypes.c_void_p()
    out_len = ctypes.c_size_t()
    rc = lib.nyrqis_nyfs_zstd_decompress_verify(
        ctypes.cast(buf, ctypes.c_void_p), len(compressed),
        ctypes.cast(digest_buf, ctypes.c_void_p),
        ctypes.byref(out_ptr), ctypes.byref(out_len),
    )
    if rc != 0:
        _raise_rust_error(int(rc), "zstd_decompress_verify")
    try:
        return ctypes.string_at(out_ptr, out_len.value)
    finally:
        lib.nyrqis_nyfs_free(out_ptr)


# ---------------------------------------------------------------------------
# Pure-Python fallbacks (the correctness floor — identical semantics)
# ---------------------------------------------------------------------------

def _py_sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _py_zstd_compress(data: bytes, level: int) -> Optional[bytes]:
    """Zstandard via the ``zstandard`` module, or None when the module
    is absent (the floor then stores the data uncompressed, exactly as
    ``NyFSBlock.compress`` does today)."""
    try:
        import zstandard as zstd

        cctx = zstd.ZstdCompressor(level=level)
        return cctx.compress(data)
    except ImportError:
        return None


def _py_zstd_decompress(compressed: bytes) -> Optional[bytes]:
    """Zstandard decompress via the ``zstandard`` module, or None when
    the module is absent (data stored uncompressed by the floor) or the
    payload is not a zstd frame (a block stored RAW by a host without
    the module — cross-host migration — or a corrupt frame). None routes
    the caller to the raw-verify branch, so both cases surface as a
    checksum verdict instead of an unhandled ``ZstdError``."""
    try:
        import zstandard as zstd

        dctx = zstd.ZstdDecompressor()
        return dctx.decompress(compressed)
    except Exception:  # noqa: BLE001 - ZstdError and kin (lazy import: no
        # module-level zstandard dependency on the floor path)
        return None


# ---------------------------------------------------------------------------
# Public surface (FFI first, Python fallback, force-aware)
# ---------------------------------------------------------------------------

def checksum(data: bytes) -> str:
    """SHA-256 hex digest of ``data`` (the per-block checksum, NPS-004
    §4). Rust FFI when the module is loaded; hashlib otherwise."""
    lib = _load_rust_backend()
    if lib is not None:
        try:
            return _rust_sha256(lib, data).hex()
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "nyfs codec: Rust sha256 failed (%s: %s); using hashlib",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    return hashlib.sha256(data).hexdigest()


def compress(data: bytes, level: int = 3) -> bytes:
    """Zstandard-compress ``data`` (ADR-0007, level 1-22, default 3).

    Rust FFI when the module is loaded. Otherwise the ``zstandard``
    module; when that is absent the data is returned unchanged — the
    uncompressed-storage fallback ``NyFSBlock.compress`` uses today.
    """
    lib = _load_rust_backend()
    if lib is not None:
        try:
            return _rust_zstd_compress(lib, data, level)
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "nyfs codec: Rust zstd_compress failed (%s: %s); "
                "using zstandard",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    compressed = _py_zstd_compress(data, level)
    if compressed is None:
        logger.warning("zstandard not available; storing uncompressed")
        return data
    return compressed


def decompress_verify(compressed: bytes, expected_checksum: str) -> bytes:
    """Decompress ``compressed`` and verify its SHA-256 against
    ``expected_checksum`` (the hex digest of the uncompressed data).

    Raises ``ValueError("Block checksum verification failed")`` on a
    mismatch — the same exception the pure-Python floor raises. Rust FFI
    when the module is loaded; ``zstandard`` + hashlib otherwise (data
    stored uncompressed by the floor passes through verified).
    """
    lib = _load_rust_backend()
    if lib is not None:
        try:
            expected = bytes.fromhex(expected_checksum)
        except ValueError:
            raise ValueError(_CHECKSUM_MISMATCH_MSG)
        try:
            return _rust_zstd_decompress_verify(lib, compressed, expected)
        except ValueError:
            raise  # checksum mismatch — identical semantics to the floor
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if _force_enabled():
                raise
            logger.warning(
                "nyfs codec: Rust zstd_decompress_verify failed "
                "(%s: %s); using zstandard",
                type(exc).__name__, exc,
            )
    elif _force_enabled():
        raise RuntimeError(_rust_force_error())
    data = _py_zstd_decompress(compressed)
    if data is None:
        # Stored uncompressed by the floor: the payload IS the data.
        data = compressed
    computed = hashlib.sha256(data).hexdigest()
    if computed != expected_checksum:
        logger.error(
            "Checksum mismatch: expected %s, got %s",
            expected_checksum, computed,
        )
        raise ValueError(_CHECKSUM_MISMATCH_MSG)
    return data


__all__ = [
    "MIN_RUST_ABI_VERSION",
    "checksum",
    "compress",
    "decompress_verify",
]
