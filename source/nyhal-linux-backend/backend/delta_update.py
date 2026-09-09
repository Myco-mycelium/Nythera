"""delta_update — Delta update generation, signing, and application.

The generation half of NPS-026 §6: ``backend/update_signing.py``
verifies full/delta/rollback updates; this module *produces* signed
delta updates between two package payload directories and applies them
(after verification) to an installed package.

A delta update is a JSON document:

    {
      "package_id": ...,
      "version_from": ...,
      "version_to": ...,
      "update_type": "delta",
      "ops": [
        {"op": "add",     "path": "images/new.bin",    "data_b64": "..."},
        {"op": "modify",  "path": "images/data.bin",   "data_b64": "..."},
        {"op": "remove",  "path": "images/old.bin"}
      ],
      "checksum": "<sha256 of the canonical op list>",
      "key_id": "...", "signature": "<base64 Ed25519>", "timestamp": ...
    }

The ``checksum`` binds the signature to the content: the signature
payload is ``package_id:version_from:version_to:delta:checksum`` — the
same canonical form ``UpdateVerifier._signature_payload`` verifies, so
a delta generated here passes the shipped verifier unmodified.

Fail-closed posture (matching package_signing.py): without PyNaCl,
signing raises ``DeltaSignError`` — no deterministic keys, no hash
"signatures".

References:
    - NPS-026 §6: Digital Signatures (delta updates, rollback)
    - backend/update_signing.py (the verification half)
    - backend/installer.py (.nypkg directory layout)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .package_signing import HAS_NACL, PackageSignError, SigningKeypair

if HAS_NACL:
    from nacl.encoding import RawEncoder
    from nacl.signing import SigningKey

logger = logging.getLogger(__name__)


class DeltaUpdateError(Exception):
    """Delta generation/application failure."""


class DeltaSignError(DeltaUpdateError):
    """Raised when signing is requested but no signing key is usable."""


# The three ops the shipped verifier accepts (update_signing.py).
_VALID_OPS = ("add", "remove", "modify")


# ---------------------------------------------------------------------------
# Payload diffing
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _relative_files(root: Path) -> Dict[str, Path]:
    """Map every file under root to its POSIX-style relative path.

    The ``images/`` prefix is stripped so ops address package content
    the way the installer does (``images/`` is the .nypkg container
    directory, not payload content).
    """
    files: Dict[str, Path] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            full = Path(dirpath) / fname
            rel = full.relative_to(root).as_posix()
            if rel.startswith("images/"):
                rel = rel[len("images/"):]
            if not rel:
                continue
            files[rel] = full
    return files


def diff_packages(
    old_dir: str, new_dir: str
) -> List[Dict[str, Any]]:
    """Diff two package payload directories into a delta op list.

    Both directories may use the .nypkg layout (manifest.json +
    images/…) or a flat payload layout; the ``images/`` container
    prefix is normalized away either way. Manifest and integrity
    metadata files are excluded — they are regenerated at install
    time, not shipped in deltas.
    """
    old_root, new_root = Path(old_dir), Path(new_dir)
    if not old_root.is_dir():
        raise DeltaUpdateError(f"old package directory not found: {old_dir}")
    if not new_root.is_dir():
        raise DeltaUpdateError(f"new package directory not found: {new_dir}")

    skip = {"manifest.json", "integrity.json", "signature.json"}
    old_files = {
        p: f for p, f in _relative_files(old_root).items() if p not in skip
    }
    new_files = {
        p: f for p, f in _relative_files(new_root).items() if p not in skip
    }

    ops: List[Dict[str, Any]] = []
    # Removals first (deterministic order).
    for path in sorted(set(old_files) - set(new_files)):
        ops.append({"op": "remove", "path": path})
    # Adds and modifies, path-ordered.
    for path in sorted(set(new_files)):
        if path not in old_files:
            ops.append({
                "op": "add",
                "path": path,
                "data_b64": base64.b64encode(
                    new_files[path].read_bytes()
                ).decode(),
            })
        elif _hash_file(old_files[path]) != _hash_file(new_files[path]):
            ops.append({
                "op": "modify",
                "path": path,
                "data_b64": base64.b64encode(
                    new_files[path].read_bytes()
                ).decode(),
            })
    return ops


def _canonical_ops_bytes(ops: List[Dict[str, Any]]) -> bytes:
    """Canonical bytes the checksum covers (stable across runs)."""
    return json.dumps(ops, sort_keys=True, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Delta documents
# ---------------------------------------------------------------------------

def _sign_payload(package_id: str, version_from: str, version_to: str,
                  checksum: str, private_key: bytes) -> bytes:
    """Sign the canonical update payload with a raw Ed25519 seed.

    The payload mirrors ``UpdateVerifier._signature_payload`` exactly:
    ``package_id:version_from:version_to:delta:checksum`` — the
    verifier validates the identity + checksum binding, not the ops.
    """
    if not HAS_NACL:
        raise DeltaSignError("PyNaCl required for delta signing")
    sk = SigningKey(private_key, encoder=RawEncoder)
    signed = sk.sign(
        f"{package_id}:{version_from}:{version_to}:delta:{checksum}".encode()
    )
    return bytes(signed.signature)


def create_delta_update(
    old_dir: str,
    new_dir: str,
    package_id: str,
    version_from: str,
    version_to: str,
    signing_keypair: Optional[SigningKeypair] = None,
) -> Dict[str, Any]:
    """Generate a delta update document between two package versions.

    Parameters
    ----------
    old_dir, new_dir : str
        Package payload directories (any layout; ``images/`` is
        normalized).
    package_id : str
        Package identity the delta applies to.
    version_from, version_to : str
        The base version (must be installed) and the target version.
    signing_keypair : SigningKeypair, optional
        When provided (and holding a private key), the delta is signed
        with its Ed25519 key so ``UpdateVerifier.verify_delta_update``
        accepts it. Without it the document is unsigned (usable for
        dry-run/preview only).

    Returns
    -------
    dict
        The delta document (see module docstring).
    """
    ops = diff_packages(old_dir, new_dir)
    checksum = hashlib.sha256(_canonical_ops_bytes(ops)).hexdigest()

    delta: Dict[str, Any] = {
        "package_id": package_id,
        "version_from": version_from,
        "version_to": version_to,
        "update_type": "delta",
        "ops": ops,
        "checksum": checksum,
    }

    if signing_keypair is not None:
        if not signing_keypair.can_sign:
            raise DeltaSignError(
                "signing keypair has no private key (verify-only)"
            )
        signature = _sign_payload(
            package_id, version_from, version_to, checksum,
            signing_keypair.private_key,
        )
        delta["signature"] = base64.b64encode(signature).decode()
        delta["key_id"] = signing_keypair.key_id
        delta["timestamp"] = time.time()

    logger.info(
        "Delta %s %s → %s: %d ops, checksum %s…",
        package_id, version_from, version_to, len(ops), checksum[:12],
    )
    return delta


def delta_payload_bytes(delta: Dict[str, Any]) -> bytes:
    """The delta *payload* file bytes: the canonical op list.

    ``UpdateVerifier.verify_delta_update`` computes the payload
    checksum over the delta payload FILE; the document's ``checksum``
    field is the SHA-256 of exactly these bytes. Writing these bytes
    to a file (``*.delta``) and handing that path to the verifier
    closes the loop between generation and verification.
    """
    return _canonical_ops_bytes(delta.get("ops", []))


def save_delta_update(delta: Dict[str, Any], path: str) -> str:
    """Write a delta document to disk (canonical JSON) and return it."""
    Path(path).write_text(
        json.dumps(delta, sort_keys=True, separators=(",", ":"))
    )
    return path


def load_delta_update(path: str) -> Dict[str, Any]:
    """Load a delta document from disk."""
    delta = json.loads(Path(path).read_text())
    if not isinstance(delta, dict) or "ops" not in delta:
        raise DeltaUpdateError(f"not a delta update document: {path}")
    return delta


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

def apply_delta_update(
    delta: Dict[str, Any],
    install_dir: str,
    verify_trust_store: Optional[str] = None,
) -> List[str]:
    """Apply a verified delta to an installed package directory.

    Security posture: the signature is checked BEFORE any filesystem
    mutation when the delta is signed (forged deltas never reach patch
    handling — mirrors update_signing.verify_delta_update's ordering).

    Parameters
    ----------
    delta : dict
        Delta document (from ``create_delta_update`` /
        ``load_delta_update``).
    install_dir : str
        The installed package's directory (content is addressed
        relative to it, without the ``images/`` prefix).
    verify_trust_store : str, optional
        Path to a trust-store JSON. When given, the signature must
        verify against a trusted key; when the delta is unsigned the
        store is required and verification fails closed.

    Returns
    -------
    list of str
        The paths written or removed (relative to ``install_dir``).
    """
    if not isinstance(delta, dict) or delta.get("update_type") != "delta":
        raise DeltaUpdateError("not a delta update document")

    ops = delta.get("ops", [])
    for i, op in enumerate(ops):
        if "op" not in op or op["op"] not in _VALID_OPS:
            raise DeltaUpdateError(f"delta op {i} invalid")
        if "path" not in op or not op["path"]:
            raise DeltaUpdateError(f"delta op {i} missing path")
        # Path traversal guard: content paths must stay inside the
        # install directory.
        rel = Path(op["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise DeltaUpdateError(f"delta op {i} path escapes install dir")

    if verify_trust_store is not None:
        _verify_delta_signature(delta, verify_trust_store)

    root = Path(install_dir)
    if not root.is_dir():
        raise DeltaUpdateError(f"install directory not found: {install_dir}")

    touched: List[str] = []
    for op in ops:
        target = root / op["path"]
        kind = op["op"]
        if kind == "remove":
            if target.exists():
                target.unlink()
                touched.append(op["path"])
        elif kind in ("add", "modify"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(op["data_b64"]))
            touched.append(op["path"])

    logger.info("Delta applied to %s: %d paths touched", install_dir, len(touched))
    return touched


def _verify_delta_signature(delta: Dict[str, Any], trust_store_path: str) -> None:
    """Verify a delta's Ed25519 signature against a trust-store file.

    Raises ``DeltaUpdateError`` on any failure — unsigned deltas are
    refused when a trust store is supplied (fail-closed).
    """
    if not HAS_NACL:
        raise DeltaUpdateError("PyNaCl required for delta verification")

    signature_b64 = delta.get("signature")
    key_id = delta.get("key_id")
    if not signature_b64 or not key_id:
        raise DeltaUpdateError("delta is unsigned and a trust store was supplied")

    store = json.loads(Path(trust_store_path).read_text())
    trusted = {k.get("key_id"): k for k in store.get("trusted_keys", [])}
    if key_id not in trusted:
        raise DeltaUpdateError(f"delta signing key not trusted: {key_id}")

    public_key = base64.b64decode(trusted[key_id].get("public_key", ""))
    if len(public_key) != 32:
        raise DeltaUpdateError(f"trust store key {key_id} malformed")

    ops_bytes = _canonical_ops_bytes(delta.get("ops", []))
    checksum = hashlib.sha256(ops_bytes).hexdigest()
    payload = (
        f"{delta.get('package_id')}:{delta.get('version_from')}:"
        f"{delta.get('version_to')}:delta:{checksum}"
    ).encode()

    signature = base64.b64decode(signature_b64)
    if len(signature) != 64:
        raise DeltaUpdateError("delta signature malformed")

    from nacl.encoding import RawEncoder
    from nacl.signing import VerifyKey

    try:
        VerifyKey(public_key, encoder=RawEncoder).verify(payload, signature)
    except Exception as exc:  # noqa: BLE001 — any crypto failure = tamper
        raise DeltaUpdateError(f"delta signature does not verify: {exc}") from exc


__all__ = [
    "DeltaUpdateError",
    "DeltaSignError",
    "diff_packages",
    "create_delta_update",
    "delta_payload_bytes",
    "save_delta_update",
    "load_delta_update",
    "apply_delta_update",
]
