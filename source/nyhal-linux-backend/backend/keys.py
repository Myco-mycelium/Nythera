#!/usr/bin/env python3
"""
NyVault key manager — floor implementation + crate loader (ADR-0023).

Envelope encryption: every volume gets a random 256-bit data-encryption
key (DEK); the DEK is AEAD-wrapped with a daemon-held key-encryption
key (KEK); the KEK is derived from an operator unlock secret via
Argon2id and is NEVER stored in plaintext (only the ``make_kek_blob``
envelope — salt, KDF parameters, and a check value — is on disk).

The reference floor is pure PyNaCl (libsodium): ``argon2id.kdf`` +
``crypto_aead_xchacha20poly1305_ietf_*``, byte-identical to the
`rust/keys/` crate (RustCrypto argon2 + chacha20poly1305, same
construction, same parameters — pinned by the differential conformance
tests). When the crate is present, key custody moves behind the FFI
boundary: ``unlock`` returns an OPAQUE HANDLE and the KEK exists only
inside the Rust process's handle table (ADR-0020's platform-boundary
rule applied to the most sensitive path — the interpreter cannot hold
or leak what it never receives). The floor holds the KEK in Python
memory; that limitation is exactly why the crate exists.

Blob formats (deterministic, big-endian, versioned — the crate parses
the same bytes):

- KEK envelope (the vault key file, 110 bytes)::

    magic    b"NYRQIS-KEYS"  (12)
    version  u8 = 1          (1)
    kdf      u8 = 0          (1)  # argon2id
    opslimit u32 BE          (4)  # argon2 t_cost (iterations)
    memlimit u32 BE          (4)  # argon2 m_cost, KiB
    p        u8 = 1          (1)  # argon2 parallelism (libsodium's
                                  # argon2id KDF is fixed at p=1)
    salt     16 bytes        (16)
    check    nonce(24)+ct    (72) # AEAD(kek, ad=b"nyrqis-kek-check",
                                  #      plaintext=b"\\x00"*32)

- Wrapped DEK (72 bytes): nonce(24) + AEAD ciphertext+tag(48), with the
  caller-supplied associated data (the volume context).

References: ADR-0023 (vault key manager), ADR-0020 (platform boundary),
NPS-011 (capability model).
"""

import base64
import ctypes
import ctypes.util
import os
import struct
from dataclasses import dataclass
from typing import Optional, Tuple

# -- format constants -------------------------------------------------

KEYS_MAGIC = b"NYRQIS-KEYS"
KEYS_VERSION = 1
KDF_ARGO2ID = 0

SALT_LEN = 16
KEK_LEN = 32
DEK_LEN = 32
NONCE_LEN = 24
TAG_LEN = 16
CHECK_AD = b"nyrqis-kek-check"
CHECK_PLAINTEXT = b"\x00" * 32

# Argon2id defaults. memlimit is stored in KiB (the argon2 m_cost unit);
# libsodium's argon2id KDF is fixed at parallelism = 1.
ARGON2ID_OPSLIMIT = 2        # t_cost (iterations)
ARGON2ID_MEMLIMIT_KIB = 64 * 1024  # 64 MiB (PyNaCl MEMLIMIT_INTERACTIVE)
ARGON2ID_PARALLELISM = 1

_KEK_BLOB_SIZE = (len(KEYS_MAGIC) + 1 + 1 + 4 + 4 + 1
                  + SALT_LEN + NONCE_LEN + DEK_LEN + TAG_LEN)
_DEK_BLOB_SIZE = NONCE_LEN + DEK_LEN + TAG_LEN


class KeysError(Exception):
    """A key-manager failure (wrong unlock secret, tampered blob, ...)."""


# -- pure floor (PyNaCl / libsodium) ----------------------------------

def _require_nacl(what: str):
    """A clear failure when the PyNaCl floor is needed but missing
    (the crate-less reference path needs libsodium's bindings)."""
    try:
        import nacl  # noqa: F401
    except ImportError as e:
        raise KeysError(
            "%s needs the PyNaCl floor (nacl) or the Rust keys crate; "
            "pip install pynacl" % what) from e


def derive_kek(password: bytes, salt: bytes,
               opslimit: int = ARGON2ID_OPSLIMIT,
               memlimit_kib: int = ARGON2ID_MEMLIMIT_KIB) -> bytes:
    """Argon2id(password, salt) -> the 32-byte KEK. Parallelism is 1
    (libsodium's argon2id KDF is fixed at p=1)."""
    if len(salt) != SALT_LEN:
        raise ValueError("salt must be %d bytes" % SALT_LEN)
    _require_nacl("derive_kek")
    from nacl.pwhash import argon2id
    return argon2id.kdf(KEK_LEN, password, salt, opslimit,
                        memlimit_kib * 1024)


def _aead_encrypt(key: bytes, nonce: bytes, ad: bytes, plaintext: bytes
                  ) -> bytes:
    _require_nacl("the AEAD")
    from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt
    return crypto_aead_xchacha20poly1305_ietf_encrypt(
        plaintext, ad, nonce, key)


def _aead_decrypt(key: bytes, nonce: bytes, ad: bytes, ciphertext: bytes
                  ) -> bytes:
    _require_nacl("the AEAD")
    from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt
    return crypto_aead_xchacha20poly1305_ietf_decrypt(
        ciphertext, ad, nonce, key)


def make_kek_blob(password: bytes,
                  opslimit: int = ARGON2ID_OPSLIMIT,
                  memlimit_kib: int = ARGON2ID_MEMLIMIT_KIB,
                  salt: Optional[bytes] = None) -> bytes:
    """The 110-byte KEK envelope (the only thing persisted). The KEK
    itself is re-derivable from the blob + the unlock secret, so
    ``unlock_kek`` is the only way back in."""
    salt = salt if salt is not None else os.urandom(SALT_LEN)
    kek = derive_kek(password, salt, opslimit, memlimit_kib)
    nonce = os.urandom(NONCE_LEN)
    check = _aead_encrypt(kek, nonce, CHECK_AD, CHECK_PLAINTEXT)
    return (KEYS_MAGIC + struct.pack(">BB", KEYS_VERSION, KDF_ARGO2ID)
            + struct.pack(">I", opslimit)
            + struct.pack(">I", memlimit_kib)
            + struct.pack(">B", ARGON2ID_PARALLELISM)
            + salt + nonce + check)


def _parse_kek_blob(blob: bytes) -> Tuple[int, int, int, int, bytes,
                                          bytes, bytes]:
    """(version, opslimit, memlimit_kib, p, salt, check_nonce,
    check_ct) — raises KeysError on a malformed envelope."""
    if len(blob) != _KEK_BLOB_SIZE:
        raise KeysError("malformed KEK envelope: bad length")
    off = 0
    magic = blob[off:off + len(KEYS_MAGIC)]
    off += len(KEYS_MAGIC)
    if magic != KEYS_MAGIC:
        raise KeysError("malformed KEK envelope: bad magic")
    version, kdf = blob[off], blob[off + 1]
    off += 2
    if version != KEYS_VERSION or kdf != KDF_ARGO2ID:
        raise KeysError("unsupported KEK envelope (version=%d kdf=%d)"
                        % (version, kdf))
    opslimit, memlimit_kib = struct.unpack(">II", blob[off:off + 8])
    off += 8
    p = blob[off]
    off += 1
    if p != ARGON2ID_PARALLELISM:
        raise KeysError("unsupported argon2 parallelism: %d" % p)
    salt = blob[off:off + SALT_LEN]
    off += SALT_LEN
    check_nonce = blob[off:off + NONCE_LEN]
    off += NONCE_LEN
    check_ct = blob[off:off + DEK_LEN + TAG_LEN]
    return (version, opslimit, memlimit_kib, p, salt,
            check_nonce, check_ct)


def unlock_kek(blob: bytes, password: bytes) -> bytes:
    """Derive + verify the KEK from the envelope and the unlock secret.
    Raises KeysError when the secret is wrong or the envelope tampered
    (the check value fails its AEAD verification)."""
    (_v, opslimit, memlimit_kib, _p, salt,
     check_nonce, check_ct) = _parse_kek_blob(blob)
    kek = derive_kek(password, salt, opslimit, memlimit_kib)
    try:
        got = _aead_decrypt(kek, check_nonce, CHECK_AD, check_ct)
    except Exception as e:  # noqa: BLE001 - any AEAD failure = wrong secret
        raise KeysError("wrong unlock secret or tampered envelope") from e
    if got != CHECK_PLAINTEXT:
        raise KeysError("wrong unlock secret or tampered envelope")
    return kek


def new_dek() -> bytes:
    """A fresh 32-byte data-encryption key for a volume."""
    return os.urandom(DEK_LEN)


def wrap_dek(kek: bytes, ad: bytes, dek: bytes,
             nonce: Optional[bytes] = None) -> bytes:
    """The 72-byte wrapped DEK (nonce + ciphertext+tag). The nonce may
    be supplied for deterministic tests; it is random in production."""
    if len(kek) != KEK_LEN or len(dek) != DEK_LEN:
        raise ValueError("KEK and DEK must both be %d bytes" % KEK_LEN)
    nonce = nonce if nonce is not None else os.urandom(NONCE_LEN)
    if len(nonce) != NONCE_LEN:
        raise ValueError("nonce must be %d bytes" % NONCE_LEN)
    ct = _aead_encrypt(kek, nonce, ad, dek)
    return nonce + ct


def unwrap_dek(kek: bytes, ad: bytes, blob: bytes) -> bytes:
    """The DEK back out of its envelope. Raises KeysError on tampering
    or a wrong KEK (the AEAD verification fails)."""
    if len(blob) != _DEK_BLOB_SIZE:
        raise KeysError("malformed wrapped DEK: bad length")
    nonce, ct = blob[:NONCE_LEN], blob[NONCE_LEN:]
    try:
        dek = _aead_decrypt(kek, nonce, ad, ct)
    except Exception as e:  # noqa: BLE001 - AEAD failure = tamper/wrong key
        raise KeysError("wrapped DEK failed verification") from e
    if len(dek) != DEK_LEN:
        raise KeysError("wrapped DEK decrypted to the wrong length")
    return dek


def kek_blob_size() -> int:
    return _KEK_BLOB_SIZE


def dek_blob_size() -> int:
    return _DEK_BLOB_SIZE


# -- crate loader / custody boundary (ADR-0023) -----------------------

@dataclass
class _FloorHandle:
    """The floor's key handle: the KEK bytes in Python memory. The
    crate path (below) holds the same bytes inside the Rust process
    and returns an integer handle instead — the platform-boundary
    difference the differential gate exists to preserve."""
    kek: bytes


@dataclass
class _CrateHandle:
    """An opaque KEK handle into the Rust crate's handle table. The
    plaintext KEK never crosses the FFI boundary."""
    handle: int


KeyHandle = object  # _FloorHandle | _CrateHandle (opaque to callers)

# ABI version, same encoding as the other migration crates:
# semver-major*10000 + minor*100 + patch (1.0.0 = 0x0001_0000).
_KEYS_ABI = 0x0001_0000


def _crate_candidates() -> list:
    override = os.environ.get("NYRQIS_KEYS_LIB")
    if override:
        return [override]
    here = os.path.dirname(os.path.abspath(__file__))
    crate = os.path.join(here, "..", "rust", "keys",
                         "target", "release", "libnyrqis_keys.so")
    return [os.path.normpath(crate)]


def _force_enabled() -> bool:
    return os.environ.get("NYRQIS_RUST_FORCE") == "1"


def _force_error() -> str:
    return ("NYRQIS_RUST_FORCE=1 but the Rust keys crate is not built "
            "(rust/keys/target/release/libnyrqis_keys.so)")


class _Crate:
    """ctypes binding to rust/keys (ABI 1.0.0)."""

    def __init__(self, path: str) -> None:
        self.lib = ctypes.CDLL(path)
        self.lib.nyrqis_keys_version.restype = ctypes.c_uint32
        version = self.lib.nyrqis_keys_version()
        if version != _KEYS_ABI:
            raise RuntimeError("keys crate ABI mismatch: %d != %d"
                               % (version, _KEYS_ABI))
        L = ctypes.c_size_t
        P = ctypes.c_void_p
        U32 = ctypes.c_uint32
        U64 = ctypes.c_uint64
        self.lib.nyrqis_keys_derive_kek.restype = ctypes.c_int
        self.lib.nyrqis_keys_derive_kek.argtypes = [
            P, L, P, L, U32, U32, ctypes.c_uint8, P, L]
        self.lib.nyrqis_keys_make_blob.restype = ctypes.c_int
        self.lib.nyrqis_keys_make_blob.argtypes = [
            P, L, U32, U32, P, P, L]
        self.lib.nyrqis_keys_unlock.restype = ctypes.c_int
        self.lib.nyrqis_keys_unlock.argtypes = [P, L, P, L, ctypes.POINTER(U64)]
        self.lib.nyrqis_keys_wrap.restype = ctypes.c_int
        self.lib.nyrqis_keys_wrap.argtypes = [
            U64, P, L, P, L, P, P, L]
        self.lib.nyrqis_keys_unwrap.restype = ctypes.c_int
        self.lib.nyrqis_keys_unwrap.argtypes = [
            U64, P, L, P, L, P, L]
        self.lib.nyrqis_keys_shred.restype = ctypes.c_int
        self.lib.nyrqis_keys_shred.argtypes = [U64]

    # -- pure functions (differential surface) ----------------------

    @staticmethod
    def _ptr(data: bytes):
        # c_char_p holds a reference to the bytes for the call duration
        # (a plain cast of a bytes object is not a pointer).
        return ctypes.c_char_p(data)

    def derive_kek(self, password: bytes, salt: bytes,
                   opslimit: int, memlimit_kib: int) -> bytes:
        out = ctypes.create_string_buffer(KEK_LEN)
        rc = self.lib.nyrqis_keys_derive_kek(
            self._ptr(password), len(password),
            self._ptr(salt), len(salt), opslimit, memlimit_kib, 1,
            out, KEK_LEN)
        if rc != 0:
            raise KeysError("keys crate derive_kek failed (%d)" % rc)
        return out.raw

    def make_blob(self, password: bytes, opslimit: int,
                  memlimit_kib: int, salt: bytes) -> bytes:
        out = ctypes.create_string_buffer(_KEK_BLOB_SIZE)
        rc = self.lib.nyrqis_keys_make_blob(
            self._ptr(password), len(password), opslimit, memlimit_kib,
            self._ptr(salt), out, _KEK_BLOB_SIZE)
        if rc != 0:
            raise KeysError("keys crate make_blob failed (%d)" % rc)
        return out.raw

    # -- custody surface (handles) ----------------------------------

    def unlock(self, blob: bytes, password: bytes) -> int:
        handle = ctypes.c_uint64()
        rc = self.lib.nyrqis_keys_unlock(
            self._ptr(blob), len(blob),
            self._ptr(password), len(password), ctypes.byref(handle))
        if rc != 0:
            raise KeysError("unlock failed (%d)" % rc)
        return int(handle.value)

    def wrap(self, handle: int, ad: bytes, plaintext: bytes,
             nonce: bytes) -> bytes:
        out = ctypes.create_string_buffer(_DEK_BLOB_SIZE)
        rc = self.lib.nyrqis_keys_wrap(
            handle, self._ptr(ad), len(ad),
            self._ptr(plaintext), len(plaintext),
            self._ptr(nonce), out, _DEK_BLOB_SIZE)
        if rc != 0:
            raise KeysError("wrap failed (%d)" % rc)
        return out.raw

    def unwrap(self, handle: int, ad: bytes, blob: bytes) -> bytes:
        out = ctypes.create_string_buffer(DEK_LEN)
        rc = self.lib.nyrqis_keys_unwrap(
            handle, self._ptr(ad), len(ad),
            self._ptr(blob), len(blob), out, DEK_LEN)
        if rc != 0:
            raise KeysError("unwrap failed (%d)" % rc)
        return out.raw

    def shred(self, handle: int) -> None:
        rc = self.lib.nyrqis_keys_shred(handle)
        if rc != 0:
            raise KeysError("shred failed (%d)" % rc)


def available() -> bool:
    """The Rust keys crate is built on this host (never raises; a miss
    means \"use the PyNaCl floor\")."""
    try:
        return _crate() is not None
    except Exception:  # noqa: BLE001 - availability is a probe
        return False


def _reset_cache() -> None:
    """Drop the cached crate (tests point the loader at a temp fake).
    The attribute must be REMOVED, not set to None: its presence is
    the "already scanned" flag, so a None value would permanently
    pin the loader to "no crate" (or to whatever was cached)."""
    if hasattr(_crate, "_cache"):
        delattr(_crate, "_cache")  # type: ignore[attr-defined]


def _crate() -> Optional[_Crate]:
    if not hasattr(_crate, "_cache"):
        _crate._cache = None  # type: ignore[attr-defined]
        for path in _crate_candidates():
            if os.path.isfile(path):
                try:
                    _crate._cache = _Crate(path)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001 - a broken crate = floor
                    _crate._cache = None  # type: ignore[attr-defined]
                break
    return _crate._cache  # type: ignore[attr-defined,return-value]


def _require_crate() -> _Crate:
    crate = _crate()
    if crate is None:
        if _force_enabled():
            raise RuntimeError(_force_error())
        raise KeysError("keys crate not available")
    return crate


# -- the manager API (floor OR crate — same semantics) ----------------

def derive_kek_any(password: bytes, salt: bytes,
                   opslimit: int = ARGON2ID_OPSLIMIT,
                   memlimit_kib: int = ARGON2ID_MEMLIMIT_KIB) -> bytes:
    """The 32-byte KEK for a passphrase+salt (pure KDF; used by the
    differential gate and the blob checker)."""
    crate = _crate()
    if crate is not None:
        return crate.derive_kek(password, salt, opslimit, memlimit_kib)
    if _force_enabled():
        raise RuntimeError(_force_error())
    return derive_kek(password, salt, opslimit, memlimit_kib)


def make_blob_any(password: bytes,
                  opslimit: int = ARGON2ID_OPSLIMIT,
                  memlimit_kib: int = ARGON2ID_MEMLIMIT_KIB,
                  salt: Optional[bytes] = None) -> bytes:
    """The KEK envelope (110 bytes) — the only thing persisted."""
    crate = _crate()
    if crate is not None:
        salt = salt if salt is not None else os.urandom(SALT_LEN)
        return crate.make_blob(password, opslimit, memlimit_kib, salt)
    if _force_enabled():
        raise RuntimeError(_force_error())
    return make_kek_blob(password, opslimit, memlimit_kib, salt)


def unlock(blob: bytes, password: bytes) -> KeyHandle:
    """Derive + verify the KEK and return an opaque handle. Crate path:
    the KEK stays inside the Rust process (a u64 handle). Floor path:
    a handle wrapping the KEK bytes (the documented floor limitation)."""
    crate = _crate()
    if crate is not None:
        try:
            return _CrateHandle(crate.unlock(blob, password))
        except KeysError:
            if _force_enabled():
                raise
            # Fall through to the floor (a real daemon needs to keep
            # working even if a crate edge case misfires).
    if _force_enabled():
        # The conformance gate: every keys op must run through the
        # crate or fail the build.
        raise RuntimeError(_force_error())
    return _FloorHandle(unlock_kek(blob, password))


def wrap(key: KeyHandle, ad: bytes, plaintext: bytes,
         nonce: Optional[bytes] = None) -> bytes:
    """Wrap ``plaintext`` (a DEK) with the handle's KEK. Returns the
    72-byte envelope (nonce + ciphertext+tag)."""
    if isinstance(key, _CrateHandle):
        nonce = nonce if nonce is not None else os.urandom(NONCE_LEN)
        return _require_crate().wrap(key.handle, ad, plaintext, nonce)
    return wrap_dek(key.kek, ad, plaintext, nonce)


def unwrap(key: KeyHandle, ad: bytes, blob: bytes) -> bytes:
    """The DEK back out of its envelope; raises KeysError on tampering
    or a wrong KEK."""
    if isinstance(key, _CrateHandle):
        return _require_crate().unwrap(key.handle, ad, blob)
    return unwrap_dek(key.kek, ad, blob)


def shred(key: KeyHandle) -> None:
    """Release the key (crate: drop it from the handle table; floor:
    drop the reference)."""
    if isinstance(key, _CrateHandle):
        _require_crate().shred(key.handle)
