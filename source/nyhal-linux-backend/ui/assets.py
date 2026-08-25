#!/usr/bin/env python3
"""NUI Asset Manager — manages project assets (NUI-SCHEMA §8.2).

Provides a single-source-of-truth asset pipeline for .nstudio projects:

- Import assets from disk into the project directory
- Compute SHA-256 hashes for integrity and deduplication
- Detect missing or broken ``$asset:id`` references
- Generate asset metadata (dimensions, format, size)
- Track provenance (source path, import time)

The asset manager sits between the editor (Nyforge) and the runtime
(Nyrqis). It reads the ``resources.assets`` section of an
``NstudioDocument`` and manages the physical files on disk.

References:
- NUI-SCHEMA §8.2: resources and asset declarations
- NFS-006 §8: asset pipeline
- ADR-0025 §9: runtime consumption (floor implementation)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Asset kinds from the NUI schema (ASSET_KINDS in nstudio.py)
ASSET_KINDS = (
    "image", "svg", "icon", "font", "audio", "video",
    "material", "animation",
)

# File extensions we can detect (kind → extensions)
_EXT_MAP: Dict[str, Tuple[str, ...]] = {
    "image": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"),
    "svg": (".svg",),
    "icon": (".ico", ".icns"),
    "font": (".ttf", ".otf", ".woff", ".woff2"),
    "audio": (".mp3", ".wav", ".ogg", ".flac", ".aac"),
    "video": (".mp4", ".webm", ".mov", ".avi"),
    "material": (".mat", ".json"),
    "animation": (".json", ".lottie"),
}


def detect_kind(path: str) -> Optional[str]:
    """Detect the asset kind from a file extension."""
    ext = os.path.splitext(path)[1].lower()
    for kind, extensions in _EXT_MAP.items():
        if ext in extensions:
            return kind
    return None


def sha256_file(path: str) -> str:
    """Compute the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Compute the SHA-256 hex digest of a bytes object."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Asset metadata
# ---------------------------------------------------------------------------

@dataclass
class AssetMetadata:
    """Computed metadata for a single asset file."""
    id: str
    kind: str
    path: str
    sha256: Optional[str] = None
    size_bytes: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    # Format-specific metadata
    mime_type: Optional[str] = None
    # Provenance
    source_path: Optional[str] = None
    imported_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "size_bytes": self.size_bytes,
        }
        if self.sha256:
            d["sha256"] = self.sha256
        if self.width is not None:
            d["width"] = self.width
        if self.height is not None:
            d["height"] = self.height
        if self.mime_type:
            d["mime_type"] = self.mime_type
        if self.source_path:
            d["source_path"] = self.source_path
        if self.imported_at:
            d["imported_at"] = self.imported_at
        return d


# ---------------------------------------------------------------------------
# Asset Manager
# ---------------------------------------------------------------------------

class AssetManager:
    """Manages assets for a .nstudio project.

    Parameters
    ----------
    project_dir : str
        The root directory of the .nstudio project (where the
        ``.nstudio`` file lives).  Asset files are stored under
        ``project_dir/assets/``.
    """

    def __init__(self, project_dir: str) -> None:
        self.project_dir = os.path.abspath(project_dir)
        self.assets_dir = os.path.join(self.project_dir, "assets")
        os.makedirs(self.assets_dir, exist_ok=True)
        # cache: asset_id → AssetMetadata
        self._cache: Dict[str, AssetMetadata] = {}

    # ---- import -----------------------------------------------------------

    def import_file(
        self,
        source_path: str,
        asset_id: Optional[str] = None,
        kind: Optional[str] = None,
        dedup: bool = True,
    ) -> AssetMetadata:
        """Import a file from *source_path* into the project's asset
        directory.

        Parameters
        ----------
        source_path : str
            Absolute or relative path to the source file.
        asset_id : str, optional
            Explicit asset id.  Defaults to the stem of the filename.
        kind : str, optional
            Asset kind.  Detected from extension if omitted.
        dedup : bool
            If True, skip import when a file with the same SHA-256
            already exists (re-use the existing copy).

        Returns
        -------
        AssetMetadata
            Metadata for the imported (or re-used) asset.
        """
        source = os.path.abspath(source_path)
        if not os.path.isfile(source):
            raise FileNotFoundError(f"source file not found: {source}")

        if asset_id is None:
            asset_id = os.path.splitext(os.path.basename(source))[0]
        if kind is None:
            kind = detect_kind(source) or "material"

        file_hash = sha256_file(source)
        file_size = os.path.getsize(source)

        # Deduplication: if an asset with the same hash exists, reuse it
        if dedup:
            existing = self._find_by_hash(file_hash)
            if existing is not None:
                logger.info(
                    f"dedup: '{asset_id}' matches existing '{existing.id}' "
                    f"(hash {file_hash[:12]}…)")
                meta = AssetMetadata(
                    id=asset_id,
                    kind=existing.kind,
                    path=existing.path,
                    sha256=existing.sha256,
                    size_bytes=existing.size_bytes,
                    width=existing.width,
                    height=existing.height,
                    source_path=existing.source_path,
                    imported_at=existing.imported_at,
                )
                self._cache[asset_id] = meta
                return meta

        # Compute destination path
        ext = os.path.splitext(source)[1]
        dest_name = f"{asset_id}{ext}"
        dest_path = os.path.join(self.assets_dir, dest_name)

        # Copy the file
        shutil.copy2(source, dest_path)

        # Compute image dimensions if possible
        width, height = self._probe_image_dimensions(dest_path, kind)

        meta = AssetMetadata(
            id=asset_id,
            kind=kind,
            path=os.path.relpath(dest_path, self.project_dir),
            sha256=file_hash,
            size_bytes=file_size,
            width=width,
            height=height,
            source_path=source,
            imported_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._cache[asset_id] = meta
        logger.info(f"imported asset '{asset_id}' ({kind}, {file_size} B)")
        return meta

    def import_bytes(
        self,
        data: bytes,
        filename: str,
        asset_id: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> AssetMetadata:
        """Import raw bytes as an asset (useful for programmatically
        generated content)."""
        if asset_id is None:
            asset_id = os.path.splitext(filename)[0]
        if kind is None:
            kind = detect_kind(filename) or "material"

        file_hash = sha256_bytes(data)
        ext = os.path.splitext(filename)[1]
        dest_name = f"{asset_id}{ext}"
        dest_path = os.path.join(self.assets_dir, dest_name)

        with open(dest_path, "wb") as f:
            f.write(data)

        width, height = self._probe_image_dimensions(dest_path, kind)

        meta = AssetMetadata(
            id=asset_id,
            kind=kind,
            path=os.path.relpath(dest_path, self.project_dir),
            sha256=file_hash,
            size_bytes=len(data),
            width=width,
            height=height,
            imported_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._cache[asset_id] = meta
        return meta

    # ---- query ------------------------------------------------------------

    def get(self, asset_id: str) -> Optional[AssetMetadata]:
        """Look up an asset by id."""
        if asset_id in self._cache:
            return self._cache[asset_id]
        # Try scanning the assets directory
        self._scan()
        return self._cache.get(asset_id)

    def list_assets(self) -> List[AssetMetadata]:
        """List all assets in the project."""
        self._scan()
        return list(self._cache.values())

    def by_kind(self, kind: str) -> List[AssetMetadata]:
        """List assets of a specific kind."""
        return [a for a in self.list_assets() if a.kind == kind]

    def duplicates(self) -> List[List[AssetMetadata]]:
        """Find groups of assets with the same SHA-256 hash."""
        self._scan()
        by_hash: Dict[str, List[AssetMetadata]] = {}
        for meta in self._cache.values():
            if meta.sha256:
                by_hash.setdefault(meta.sha256, []).append(meta)
        return [group for group in by_hash.values() if len(group) > 1]

    # ---- validation -------------------------------------------------------

    def validate_references(
        self, declared_assets: List[Dict[str, Any]]
    ) -> List[str]:
        """Check that every declared asset reference has a physical file
        on disk and report issues.

        Parameters
        ----------
        declared_assets : list
            The ``resources.assets`` list from an ``NstudioDocument``.

        Returns
        -------
        list[str]
            Issue descriptions (empty if all references are valid).
        """
        issues: List[str] = []
        declared_ids: set = set()

        for entry in declared_assets:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("id", "")
            path = entry.get("path", "")
            declared_ids.add(aid)

            # Check file exists
            full_path = os.path.join(self.project_dir, path)
            if not os.path.isfile(full_path):
                issues.append(
                    f"asset '{aid}': file not found at '{path}'")
                continue

            # Check hash integrity if declared
            declared_hash = entry.get("sha256")
            if declared_hash:
                actual_hash = sha256_file(full_path)
                if actual_hash != declared_hash:
                    issues.append(
                        f"asset '{aid}': SHA-256 mismatch "
                        f"(declared {declared_hash[:12]}… ≠ "
                        f"actual {actual_hash[:12]}…)")

        # Check for orphan files (on disk but not declared)
        if os.path.isdir(self.assets_dir):
            for fname in os.listdir(self.assets_dir):
                stem = os.path.splitext(fname)[0]
                if stem not in declared_ids:
                    issues.append(
                        f"orphan asset: '{fname}' exists on disk but "
                        f"is not declared in resources")

        return issues

    def find_missing(self, declared_assets: List[Dict[str, Any]]) -> List[str]:
        """Return asset ids whose files are missing from disk."""
        missing: List[str] = []
        for entry in declared_assets:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("id", "")
            path = entry.get("path", "")
            full_path = os.path.join(self.project_dir, path)
            if not os.path.isfile(full_path):
                missing.append(aid)
        return missing

    # ---- serialization ----------------------------------------------------

    def to_resources_dict(self) -> Dict[str, Any]:
        """Serialize the managed assets into a ``resources`` dict
        suitable for embedding in a .nstudio document."""
        self._scan()
        return {
            "assets": [meta.to_dict() for meta in self._cache.values()]
        }

    # ---- internal helpers -------------------------------------------------

    def _scan(self) -> None:
        """Populate the cache by scanning the assets directory."""
        if not os.path.isdir(self.assets_dir):
            return
        for fname in os.listdir(self.assets_dir):
            full_path = os.path.join(self.assets_dir, fname)
            if not os.path.isfile(full_path):
                continue
            aid = os.path.splitext(fname)[0]
            if aid in self._cache:
                continue
            kind = detect_kind(fname) or "material"
            width, height = self._probe_image_dimensions(full_path, kind)
            meta = AssetMetadata(
                id=aid,
                kind=kind,
                path=os.path.relpath(full_path, self.project_dir),
                sha256=sha256_file(full_path),
                size_bytes=os.path.getsize(full_path),
                width=width,
                height=height,
            )
            self._cache[aid] = meta

    def _find_by_hash(self, file_hash: str) -> Optional[AssetMetadata]:
        """Find an existing asset with the given SHA-256."""
        self._scan()
        for meta in self._cache.values():
            if meta.sha256 == file_hash:
                return meta
        return None

    def _probe_image_dimensions(
        self, path: str, kind: str
    ) -> Tuple[Optional[int], Optional[int]]:
        """Try to read image dimensions without PIL (header parsing)."""
        if kind not in ("image", "icon"):
            return None, None
        try:
            with open(path, "rb") as f:
                header = f.read(32)
            # PNG: width/height at bytes 16–23
            if header[:8] == b"\x89PNG\r\n\x1a\n":
                import struct
                w, h = struct.unpack(">II", header[16:24])
                return int(w), int(h)
            # JPEG: SOF0 marker
            if header[:2] == b"\xff\xd8":
                import struct
                f = open(path, "rb")
                f.read(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2:
                        break
                    if marker[0] != 0xFF:
                        break
                    if marker[1] in (0xC0, 0xC1, 0xC2):
                        f.read(3)  # length + precision
                        h, w = struct.unpack(">HH", f.read(4))
                        f.close()
                        return int(w), int(h)
                    length = struct.unpack(">H", f.read(2))[0]
                    f.read(length - 2)
                f.close()
        except Exception:
            pass
        return None, None
