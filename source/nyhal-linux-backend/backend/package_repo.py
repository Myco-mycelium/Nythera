"""package_repo — Package repository management for Nyrqis.

The repository half of NPS-026: a package repository is a directory
holding published ``.nypkg`` payloads, delta payloads, and a single
signed index document that a client can verify *before* downloading
anything (the index entry binds package id, version, checksum, and
publisher key).

Layout::

    <repo>/
      index.json                 # signed index (this module)
      packages/<id>/<version>/   # published package payloads
      deltas/<id>/<from>_<to>.delta

Index document::

    {
      "version": 1,
      "updated": <unix ts>,
      "packages": [ {entry}... ],   # full package versions
      "deltas":   [ {entry}... ],   # delta updates
    }
    signature block:
      {"key_id": ..., "signature": <b64 Ed25519 over canonical index>,
       "timestamp": ...}

Trust model (fail-closed, matching package_signing/delta_update):
verification requires a trusted key AND a valid signature over the
canonical bytes; a repo signed by an untrusted key is an error, never
a warning. Tamper checks: the entry checksum binds content; a
signature that does not verify refuses the whole index (there is no
partial trust).

References:
    - NPS-026: Package Format (.nypkg) — signed manifests
    - NPS-027: Package Trust Model
    - backend/package_signing.py (Ed25519 keys, trust store)
    - backend/delta_update.py (delta generation/verification)
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .package_signing import HAS_NACL, SigningKeypair

if HAS_NACL:
    from nacl.encoding import RawEncoder
    from nacl.signing import SigningKey, VerifyKey

logger = logging.getLogger(__name__)

INDEX_VERSION = 1


class RepoError(Exception):
    """Repository operation failure."""


class RepoSignError(RepoError):
    """Signing requested but no usable signing key."""


def _canonical(obj: Any) -> bytes:
    """Canonical JSON bytes (stable across runs and hosts)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _dir_sha256(root: Path) -> str:
    """Content checksum of a directory tree (sorted rel paths + bytes)."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix().encode()
        h.update(rel)
        h.update(p.read_bytes())
    return h.hexdigest()


def _sign_bytes(payload: bytes, keypair: SigningKeypair) -> Dict[str, Any]:
    """Ed25519-sign canonical bytes; returns a signature block."""
    if not HAS_NACL:
        raise RepoSignError("PyNaCl required for repository signing")
    if not keypair.can_sign:
        raise RepoSignError("signing keypair has no private key")
    sk = SigningKey(keypair.private_key, encoder=RawEncoder)
    signed = sk.sign(payload)
    return {
        "key_id": keypair.key_id,
        "signature": signed.signature.hex(),
        "timestamp": time.time(),
    }


class PackageRepository:
    """A signed package repository on disk.

    Usage::

        repo = PackageRepository("/srv/nypkg")
        kp = SigningKeypair.generate()
        repo.publish_package(pkg_dir, "myapp", "1.0.0", kp)
        repo.publish_delta(delta_doc, delta_payload_path, kp)
        # client side, with a trust store:
        entries = repo.load_index(trust_store_path="trust.json")
        entry = repo.find("myapp", "1.0.0")
    """

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self._index_path = self.root / "index.json"

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _ensure_layout(self) -> None:
        (self.root / "packages").mkdir(parents=True, exist_ok=True)
        (self.root / "deltas").mkdir(parents=True, exist_ok=True)

    def _package_dir(self, package_id: str, version: str) -> Path:
        return self.root / "packages" / package_id / version

    def _delta_path(self, package_id: str, v_from: str, v_to: str) -> Path:
        return self.root / "deltas" / package_id / f"{v_from}_{v_to}.delta"

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish_package(
        self,
        package_dir: str,
        package_id: str,
        version: str,
        signing_keypair: SigningKeypair,
    ) -> Dict[str, Any]:
        """Publish a package version: copy the payload into the repo,
        checksum it, and record an index entry (re-signs the index).

        Returns the new index entry.
        """
        src = Path(package_dir)
        if not src.is_dir():
            raise RepoError(f"package directory not found: {package_dir}")

        self._ensure_layout()
        dest = self._package_dir(package_id, version)
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)

        entry = {
            "type": "package",
            "package_id": package_id,
            "version": version,
            "checksum": _dir_sha256(dest),
            "published": time.time(),
        }
        self._add_entry(entry, signing_keypair)
        logger.info("repo: published %s %s", package_id, version)
        return entry

    def publish_delta(
        self,
        delta: Dict[str, Any],
        delta_payload_path: str,
        signing_keypair: SigningKeypair,
    ) -> Dict[str, Any]:
        """Publish a delta update document + payload file and record
        its index entry (re-signs the index)."""
        payload = Path(delta_payload_path)
        if not payload.is_file():
            raise RepoError(f"delta payload not found: {delta_payload_path}")
        package_id = delta.get("package_id")
        v_from = delta.get("version_from")
        v_to = delta.get("version_to")
        if not (package_id and v_from and v_to):
            raise RepoError("delta document lacks package identity")

        self._ensure_layout()
        dest = self._delta_path(package_id, v_from, v_to)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # A publisher may stage the payload inside the repo (the CLI
        # writes the delta doc next to it) — copy only when it is a
        # different file.
        if not (dest.exists() and payload.samefile(dest)):
            shutil.copy2(payload, dest)

        entry = {
            "type": "delta",
            "package_id": package_id,
            "version_from": v_from,
            "version_to": v_to,
            "checksum": _file_sha256(dest),
            "published": time.time(),
        }
        self._add_entry(entry, signing_keypair)
        logger.info(
            "repo: published delta %s %s→%s", package_id, v_from, v_to
        )
        return entry

    def remove_package(
        self, package_id: str, version: str,
        signing_keypair: SigningKeypair,
    ) -> bool:
        """Remove a package version (payload + index entry)."""
        dest = self._package_dir(package_id, version)
        existed = False
        if dest.exists():
            shutil.rmtree(dest)
            existed = True
        self._drop_entry(
            lambda e: e.get("type") == "package"
            and e.get("package_id") == package_id
            and e.get("version") == version,
            signing_keypair,
        )
        return existed

    def _add_entry(
        self, entry: Dict[str, Any], kp: SigningKeypair
    ) -> None:
        index = self._read_index_unsigned()
        index["packages"] = [
            e for e in index.get("packages", [])
            if not (
                e.get("type") == entry["type"]
                and e.get("package_id") == entry["package_id"]
                and e.get("version") == entry.get("version")
                and e.get("version_from") == entry.get("version_from")
                and e.get("version_to") == entry.get("version_to")
            )
        ]
        index.setdefault("packages", []).append(entry)
        index["updated"] = time.time()
        self._write_index(index, kp)

    def _drop_entry(self, predicate, kp: SigningKeypair) -> None:
        index = self._read_index_unsigned()
        index["packages"] = [
            e for e in index.get("packages", []) if not predicate(e)
        ]
        index["updated"] = time.time()
        self._write_index(index, kp)

    # ------------------------------------------------------------------
    # Index persistence (signed)
    # ------------------------------------------------------------------

    def _write_index(
        self, index: Dict[str, Any], kp: SigningKeypair
    ) -> None:
        doc = dict(index)
        doc["version"] = INDEX_VERSION
        doc.pop("signature", None)
        doc["signature"] = _sign_bytes(_canonical(doc), kp)
        self._index_path.write_text(
            json.dumps(doc, indent=2, sort_keys=True)
        )

    def _read_index_unsigned(self) -> Dict[str, Any]:
        if not self._index_path.exists():
            return {"packages": [], "deltas": []}
        doc = json.loads(self._index_path.read_text())
        # Deltas are kept in a separate list from packages on disk but
        # live in the same signed doc.
        doc.setdefault("packages", [])
        doc.setdefault("deltas", [])
        return doc

    # ------------------------------------------------------------------
    # Client side: verification + query
    # ------------------------------------------------------------------

    def load_index(self, trust_store_path: str) -> Dict[str, Any]:
        """Load and FULLY verify the index against a trust store.

        The signature must verify under a key the trust store trusts —
        an unsigned index, an untrusted key, or any signature mismatch
        raises (fail-closed; no partial trust of entries).

        Returns the index document with packages/deltas entries.
        """
        if not self._index_path.exists():
            raise RepoError(f"repository index missing: {self._index_path}")
        doc = json.loads(self._index_path.read_text())

        sig = doc.get("signature")
        if not sig:
            raise RepoError("repository index is unsigned")
        key_id = sig.get("key_id")
        signature_hex = sig.get("signature")
        if not key_id or not signature_hex:
            raise RepoError("repository index signature block malformed")

        trust_path = Path(trust_store_path)
        if not trust_path.is_file():
            raise RepoError(f"trust store not found: {trust_store_path}")
        store = json.loads(trust_path.read_text())
        trusted = {
            k.get("key_id"): k for k in store.get("trusted_keys", [])
        }
        if key_id not in trusted:
            raise RepoError(f"repository signing key not trusted: {key_id}")

        if not HAS_NACL:
            raise RepoError("PyNaCl required for index verification")

        public_key_b64 = trusted[key_id].get("public_key", "")
        import base64

        public_key = base64.b64decode(public_key_b64)
        if len(public_key) != 32:
            raise RepoError(f"trust store key {key_id} malformed")

        signed_doc = {k: v for k, v in doc.items() if k != "signature"}
        try:
            signature = bytes.fromhex(signature_hex)
            if len(signature) != 64:
                raise RepoError("repository index signature malformed")
            vk = VerifyKey(public_key, encoder=RawEncoder)
            vk.verify(_canonical(signed_doc), signature)
        except RepoError:
            raise
        except Exception as exc:  # noqa: BLE001 — any crypto fail = tamper
            raise RepoError(
                f"repository index signature does not verify: {exc}"
            ) from exc

        logger.info("repo: index verified (key %s, %d entries)",
                    key_id, len(doc.get("packages", [])))
        return doc

    def find(
        self, package_id: str, version: str, trust_store_path: str
    ) -> Optional[Dict[str, Any]]:
        """Find a verified package entry (or None when absent)."""
        index = self.load_index(trust_store_path)
        for entry in index.get("packages", []):
            if (
                entry.get("type") == "package"
                and entry.get("package_id") == package_id
                and entry.get("version") == version
            ):
                return entry
        return None

    def find_delta(
        self, package_id: str, version_from: str, version_to: str,
        trust_store_path: str,
    ) -> Optional[Dict[str, Any]]:
        """Find a verified delta entry between two versions (or None)."""
        index = self.load_index(trust_store_path)
        for entry in index.get("packages", []):
            if (
                entry.get("type") == "delta"
                and entry.get("package_id") == package_id
                and entry.get("version_from") == version_from
                and entry.get("version_to") == version_to
            ):
                return entry
        return None

    def verify_entry_content(
        self, entry: Dict[str, Any]
    ) -> bool:
        """Re-check a fetched entry's payload against its checksum.

        For a package entry the on-disk package directory must hash to
        the recorded checksum; for a delta the payload file must.
        """
        if entry.get("type") == "package":
            dest = self._package_dir(
                entry["package_id"], entry["version"]
            )
            if not dest.is_dir():
                return False
            return _dir_sha256(dest) == entry.get("checksum")
        if entry.get("type") == "delta":
            dest = self._delta_path(
                entry["package_id"],
                entry["version_from"],
                entry["version_to"],
            )
            if not dest.is_file():
                return False
            return _file_sha256(dest) == entry.get("checksum")
        return False

    def payload_path(self, entry: Dict[str, Any]) -> Path:
        """The on-disk payload location for a verified entry."""
        if entry.get("type") == "package":
            return self._package_dir(entry["package_id"], entry["version"])
        if entry.get("type") == "delta":
            return self._delta_path(
                entry["package_id"],
                entry["version_from"],
                entry["version_to"],
            )
        raise RepoError("unknown entry type")


__all__ = [
    "RepoError",
    "RepoSignError",
    "PackageRepository",
    "INDEX_VERSION",
]
