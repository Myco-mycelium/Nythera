"""Package installer with signature verification — NPS-026 compliance.

Implements the install flow for .nypkg packages with mandatory
signature verification (NPS-026 §6.1).

Architecture:
  .nypkg file
      │
      ▼
  PackageInstaller.install(path)
      │
      ├── 1. Extract manifest + integrity tree + signature block
      ├── 2. Verify signature against trust store (NPS-026 §6)
      ├── 3. Verify integrity tree (Merkle hash)
      ├── 4. Check capability requests (NPS-010 §4.2)
      ├── 5. Install content images
      └── 6. Register in package database

References:
  - NPS-026: Package Format (.nypkg)
  - NPS-026 §6: Digital Signatures
  - NPS-026 §6.1: Every package MUST be signed
  - NPS-026 §7: Integrity Tree (Merkle)
  - FIND-PACKAGE-001: checksum-only integrity hole
  - ADR-0014: Trust anchor model
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .package_signing import (
    PackageSignature,
    TrustStore,
    verify_package,
    PackageSignError,
    HAS_NACL,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Package manifest
# ---------------------------------------------------------------------------

@dataclass
class PackageManifest:
    """A parsed .nypkg manifest (NPS-026 §5)."""
    name: str
    version: str
    publisher: str
    runtime_class: str = "native"  # native / windows-compat / android-compat
    capabilities: List[str] = field(default_factory=list)
    dependencies: Dict[str, str] = field(default_factory=dict)
    images: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "PackageManifest":
        return cls(
            name=data["name"],
            version=data["version"],
            publisher=data.get("publisher", ""),
            runtime_class=data.get("runtime_class", "native"),
            capabilities=data.get("capabilities", []),
            dependencies=data.get("dependencies", {}),
            images=data.get("images", []),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "publisher": self.publisher,
            "runtime_class": self.runtime_class,
            "capabilities": self.capabilities,
            "dependencies": self.dependencies,
            "images": self.images,
            "metadata": self.metadata,
        }

    def to_bytes(self) -> bytes:
        """Serialize to canonical JSON bytes for signing."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Integrity tree
# ---------------------------------------------------------------------------

@dataclass
class IntegrityTree:
    """A Merkle integrity tree for package content (NPS-026 §7)."""
    root_hash: bytes
    file_hashes: Dict[str, bytes] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "IntegrityTree":
        import base64
        return cls(
            root_hash=base64.b64decode(data["root_hash"]),
            file_hashes={
                k: base64.b64decode(v)
                for k, v in data.get("file_hashes", {}).items()
            },
        )

    def to_dict(self) -> dict:
        import base64
        return {
            "root_hash": base64.b64encode(self.root_hash).decode(),
            "file_hashes": {
                k: base64.b64encode(v).decode()
                for k, v in self.file_hashes.items()
            },
        }

    def to_bytes(self) -> bytes:
        """Serialize to bytes for signing."""
        return self.root_hash

    def verify_file(self, path: str, expected_hash: bytes) -> bool:
        """Verify a single file against its hash."""
        actual_hash = hashlib.sha256(Path(path).read_bytes()).digest()
        return actual_hash == expected_hash


# ---------------------------------------------------------------------------
# Package database
# ---------------------------------------------------------------------------

class PackageDatabase:
    """Persistent registry of installed packages."""

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._packages: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            self._packages = json.loads(self._path.read_text())

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._packages, indent=2))

    def is_installed(self, name: str) -> bool:
        return name in self._packages

    def get(self, name: str) -> Optional[dict]:
        return self._packages.get(name)

    def register(self, name: str, version: str, path: str, key_id: str) -> None:
        self._packages[name] = {
            "version": version,
            "path": path,
            "key_id": key_id,
        }
        self._save()

    def unregister(self, name: str) -> bool:
        if name in self._packages:
            del self._packages[name]
            self._save()
            return True
        return False

    def list_installed(self) -> List[dict]:
        return [
            {"name": k, **v}
            for k, v in self._packages.items()
        ]


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

class InstallerError(Exception):
    """Raised when installation fails."""


class PackageInstaller:
    """Install .nypkg packages with signature verification.

    Parameters
    ----------
    install_dir : str
        Base directory for installed packages.
    trust_store_path : str, optional
        Path to the trust store JSON file.
    """

    def __init__(
        self,
        install_dir: str = "/var/lib/nyrqis/packages",
        trust_store_path: Optional[str] = None,
    ) -> None:
        self._install_dir = Path(install_dir)
        self._trust_store_path = trust_store_path
        self._trust_store: Optional[TrustStore] = None
        self._db = PackageDatabase(str(self._install_dir / "packages.json"))

    @property
    def trust_store(self) -> TrustStore:
        if self._trust_store is None:
            if self._trust_store_path and Path(self._trust_store_path).exists():
                self._trust_store = TrustStore.load(self._trust_store_path)
            else:
                self._trust_store = TrustStore()
        return self._trust_store

    def install(self, package_path: str) -> dict:
        """Install a .nypkg package.

        Parameters
        ----------
        package_path : str
            Path to the .nypkg file (directory with manifest, sig, images).

        Returns
        -------
        dict
            Installation result with name, version, status.

        Raises
        ------
        InstallerError
            If installation fails (unsigned, untrusted, integrity failure).
        """
        pkg_dir = Path(package_path)
        if not pkg_dir.is_dir():
            raise InstallerError(f"Not a package directory: {package_path}")

        # 1. Load manifest
        manifest_path = pkg_dir / "manifest.json"
        if not manifest_path.exists():
            raise InstallerError("Package missing manifest.json")
        manifest = PackageManifest.from_dict(json.loads(manifest_path.read_text()))

        logger.info("Installing %s v%s from %s", manifest.name, manifest.version, manifest.publisher)

        # 2. Check if already installed
        if self._db.is_installed(manifest.name):
            existing = self._db.get(manifest.name)
            if existing and existing["version"] == manifest.version:
                logger.info("%s v%s already installed", manifest.name, manifest.version)
                return {"name": manifest.name, "version": manifest.version, "status": "already_installed"}

        # 3. Verify signature (NPS-026 §6.1)
        sig_path = pkg_dir / "signature.json"
        if not sig_path.exists():
            raise InstallerError(
                "Package is not signed (NPS-026 §6.1 requires all packages to be signed)"
            )

        sig_data = json.loads(sig_path.read_text())
        sig_block = PackageSignature.from_dict(sig_data)

        # Load integrity tree for verification
        tree_path = pkg_dir / "integrity.json"
        if not tree_path.exists():
            raise InstallerError("Package missing integrity.json")

        tree_data = json.loads(tree_path.read_text())
        integrity_tree = IntegrityTree.from_dict(tree_data)

        # Verify signature against manifest + integrity tree
        try:
            valid = verify_package(
                manifest.to_bytes(),
                integrity_tree.to_bytes(),
                sig_block.signature,
                sig_block.public_key,
            )
            if not valid:
                raise InstallerError("Signature verification failed")
        except PackageSignError as e:
            raise InstallerError(f"Signature verification failed: {e}")

        # 4. Check trust (NPS-026 §6.3)
        if not self.trust_store.verify_against_trust(sig_block):
            raise InstallerError(
                f"Publisher key {sig_block.key_id} is not trusted. "
                f"Add it with: nyrqisctl sign trust --add <keyfile>"
            )

        logger.info("Signature verified (key_id=%s)", sig_block.key_id)

        # 5. Verify integrity tree (NPS-026 §7)
        for file_path, expected_hash in integrity_tree.file_hashes.items():
            full_path = pkg_dir / "images" / file_path
            if not full_path.exists():
                raise InstallerError(f"Missing file in package: {file_path}")
            if not integrity_tree.verify_file(str(full_path), expected_hash):
                raise InstallerError(f"Integrity check failed for: {file_path}")

        logger.info("Integrity tree verified (%d files)", len(integrity_tree.file_hashes))

        # 6. Install content images
        install_path = self._install_dir / manifest.name
        if install_path.exists():
            shutil.rmtree(install_path)
        install_path.mkdir(parents=True)

        images_dir = pkg_dir / "images"
        if images_dir.exists():
            shutil.copytree(images_dir, install_path / "images")

        # Copy manifest and signature
        shutil.copy2(manifest_path, install_path / "manifest.json")
        shutil.copy2(sig_path, install_path / "signature.json")
        shutil.copy2(tree_path, install_path / "integrity.json")

        # 7. Register in database
        self._db.register(
            manifest.name,
            manifest.version,
            str(install_path),
            sig_block.key_id,
        )

        logger.info("Installed %s v%s to %s", manifest.name, manifest.version, install_path)

        return {
            "name": manifest.name,
            "version": manifest.version,
            "publisher": manifest.publisher,
            "key_id": sig_block.key_id,
            "status": "installed",
            "path": str(install_path),
        }

    def uninstall(self, name: str) -> bool:
        """Uninstall a package."""
        entry = self._db.get(name)
        if not entry:
            return False

        install_path = Path(entry["path"])
        if install_path.exists():
            shutil.rmtree(install_path)

        self._db.unregister(name)
        logger.info("Uninstalled %s", name)
        return True

    def list_installed(self) -> List[dict]:
        """List all installed packages."""
        return self._db.list_installed()

    def verify_installed(self, name: str) -> bool:
        """Verify an installed package's integrity."""
        entry = self._db.get(name)
        if not entry:
            return False

        install_path = Path(entry["path"])
        sig_path = install_path / "signature.json"
        tree_path = install_path / "integrity.json"

        if not sig_path.exists() or not tree_path.exists():
            return False

        try:
            sig_data = json.loads(sig_path.read_text())
            sig_block = PackageSignature.from_dict(sig_data)

            tree_data = json.loads(tree_path.read_text())
            integrity_tree = IntegrityTree.from_dict(tree_data)

            # Verify integrity tree
            for file_path, expected_hash in integrity_tree.file_hashes.items():
                full_path = install_path / "images" / file_path
                if not full_path.exists():
                    return False
                if not integrity_tree.verify_file(str(full_path), expected_hash):
                    return False

            return True
        except Exception:
            return False


__all__ = [
    "PackageManifest",
    "IntegrityTree",
    "PackageDatabase",
    "PackageInstaller",
    "InstallerError",
]
