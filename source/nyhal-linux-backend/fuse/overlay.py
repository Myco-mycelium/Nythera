#!/usr/bin/env python3
"""Overlay filesystem for container-specific views.

Provides per-container writable layers on top of a shared read-only
base NyFS filesystem.  Each container gets an ``OverlayFilesystem``
instance that presents a merged view:

- **Lower layer** (read-only): the shared base ``NyFSFilesystem``.
- **Upper layer** (read-write): per-container delta that records new
  files, modifications, and deletions.
- **Merged view**: reads fall through from upper to lower; writes go
  to the upper layer only.

This is the user-space equivalent of Linux overlayfs (ADR-0016
follow-on), letting each container see a complete filesystem without
copying the base image.

Architecture::

    ┌─────────────────────────────┐
    │     OverlayFilesystem       │  ← container sees this
    │  ┌───────────┬───────────┐  │
    │  │  upper    │  lower    │  │
    │  │ (writable)│ (read-only│  │
    │  └───────────┴───────────┘  │
    └─────────────────────────────┘

References:
    - NPS-004: NyFS Filesystem Core
    - implementation_plan.md: Phase 2, overlay filesystem
"""

import copy
import errno
import logging
import os
import stat
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class OverlayEntry:
    """One entry in the upper (overlay) layer.

    Tracks the state of a file or directory relative to the lower
    layer.  An entry with ``deleted=True`` masks the lower-layer
    entry; an entry with ``data`` set shadows the lower-layer data.
    """

    __slots__ = (
        "inode", "kind", "mode", "size", "data",
        "children", "deleted", "created_at",
    )

    def __init__(self, kind: str = "file", mode: int = 0o644,
                 data: bytes = b"", children: Optional[Dict[str, int]] = None,
                 deleted: bool = False):
        self.kind = kind           # "file" or "dir"
        self.mode = mode
        self.size = len(data) if kind == "file" else 0
        self.data = data
        self.children = children or {}  # name → overlay inode (for dirs)
        self.deleted = deleted
        self.created_at = time.time()
        # inode assigned by the overlay filesystem
        self.inode: int = 0


class OverlayFilesystem:
    """Per-container overlay on top of a shared base NyFS.

    The overlay maintains an upper layer that shadows the lower
    ``NyFSFilesystem``.  Reads fall through to lower when the upper
    has no entry (or the entry is not deleted).  Writes and mutations
    go to the upper layer only.

    Parameters
    ----------
    lower : NyFSFilesystem
        The shared, read-only base filesystem.
    container_id : str
        Identifier for the container this overlay serves.
    """

    def __init__(self, lower: Any, container_id: str = "default"):
        self.lower = lower
        self.container_id = container_id

        # Upper layer: path → OverlayEntry
        self._upper: Dict[str, OverlayEntry] = {}
        # Inode counter (monotonic, never reused)
        self._next_inode: int = 2  # 1 is reserved for root
        # Thread safety
        self._lock = threading.Lock()

        # Root directory always exists in the upper layer
        root = OverlayEntry(kind="dir", mode=0o755)
        root.inode = self._alloc_inode()
        self._upper["/"] = root

    # -- inode allocation ------------------------------------------------

    def _alloc_inode(self) -> int:
        """Allocate the next inode number."""
        ino = self._next_inode
        self._next_inode += 1
        return ino

    # -- path helpers ----------------------------------------------------

    @staticmethod
    def _norm(path: str) -> str:
        """Normalize a path: ensure leading /, collapse . and .."""
        if not path.startswith("/"):
            path = "/" + path
        parts = [p for p in path.split("/") if p]
        resolved: List[str] = []
        for p in parts:
            if p == ".":
                continue
            if p == "..":
                if resolved:
                    resolved.pop()
                continue
            resolved.append(p)
        return "/" + "/".join(resolved)

    def _parent(self, path: str) -> str:
        """Return the parent directory path."""
        if path == "/":
            return "/"
        parts = path.rstrip("/").split("/")
        if len(parts) <= 1:
            return "/"
        return "/".join(parts[:-1]) or "/"

    def _basename(self, path: str) -> str:
        """Return the last component of a path."""
        parts = path.rstrip("/").split("/")
        return parts[-1] if parts else ""

    # -- upper layer lookup ----------------------------------------------

    def _upper_get(self, path: str) -> Optional[OverlayEntry]:
        """Look up a path in the upper layer."""
        return self._upper.get(path)

    def _upper_exists(self, path: str) -> bool:
        """Check if a path exists in the upper layer (not deleted)."""
        entry = self._upper.get(path)
        return entry is not None and not entry.deleted

    # -- merged resolution -----------------------------------------------

    def _lower_exists(self, path: str) -> bool:
        """Check if a path exists in the lower filesystem."""
        try:
            self.lower.getattr(path)
            return True
        except (OSError, Exception):
            return False

    def _lower_is_dir(self, path: str) -> bool:
        """Check if a path is a directory in the lower filesystem."""
        try:
            attr = self.lower.getattr(path)
            return bool(attr.get("st_mode", 0) & stat.S_IFDIR)
        except (OSError, Exception):
            return False

    def _lower_readdir(self, path: str) -> List[str]:
        """List directory contents from the lower filesystem."""
        try:
            entries = self.lower.readdir(path)
            if isinstance(entries, list):
                return [e for e in entries if e not in (".", "..")]
            return []
        except (OSError, Exception):
            return []

    def exists(self, path: str) -> bool:
        """Check if a path exists in the merged view."""
        path = self._norm(path)
        entry = self._upper.get(path)
        if entry is not None:
            return not entry.deleted
        # Fall through to lower
        return self._lower_exists(path)

    def is_dir(self, path: str) -> bool:
        """Check if a path is a directory in the merged view."""
        path = self._norm(path)
        entry = self._upper.get(path)
        if entry is not None:
            return not entry.deleted and entry.kind == "dir"
        return self._lower_is_dir(path)

    def getattr(self, path: str) -> Dict[str, Any]:
        """Get file/directory attributes from the merged view.

        Returns a dict with ``st_mode``, ``st_size``, ``st_nlink``,
        ``st_uid``, ``st_gid``, ``st_mtime``, ``ino``, etc.
        """
        path = self._norm(path)
        entry = self._upper.get(path)
        if entry is not None:
            if entry.deleted:
                raise OSError(errno.ENOENT, f"No such file or directory: {path}")
            if entry.kind == "dir":
                mode = entry.mode | stat.S_IFDIR
            else:
                # Ensure file type bit is set
                mode = entry.mode if (entry.mode & 0o170000) else (
                    entry.mode | stat.S_IFREG)
            return {
                "st_mode": mode,
                "st_nlink": 2 if entry.kind == "dir" else 1,
                "st_size": entry.size if entry.kind == "file" else 0,
                "st_uid": 0,
                "st_gid": 0,
                "st_mtime": entry.created_at,
                "ino": entry.inode,
            }
        # Fall through to lower
        try:
            attr = self.lower.getattr(path)
            # Ensure file type bits are set (NyFS may omit them for files)
            mode = attr.get("st_mode", 0)
            if not (mode & 0o170000):
                # No type bits — determine from path
                if self._lower_is_dir(path):
                    mode |= stat.S_IFDIR
                else:
                    mode |= stat.S_IFREG
                attr["st_mode"] = mode
            return attr
        except (OSError, Exception):
            raise OSError(errno.ENOENT, f"No such file or directory: {path}")

    def readdir(self, path: str) -> List[str]:
        """List directory contents from the merged view."""
        path = self._norm(path)
        if path == "":
            path = "/"

        # Collect names from lower layer
        lower_names = set(self._lower_readdir(path))

        # Collect names from upper layer
        upper_entry = self._upper.get(path)
        upper_names: set = set()
        deleted_lower: set = set()

        if upper_entry is not None and not upper_entry.deleted:
            if upper_entry.kind == "dir":
                upper_names = set(upper_entry.children.keys())
            # Check for deletions
            for name, child_ino in (upper_entry.children.items()
                                     if upper_entry.kind == "dir" else []):
                child_path = f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"
                child = self._upper.get(child_path)
                if child and child.deleted:
                    deleted_lower.add(name)

        # Merge: upper names override lower, deleted names are removed
        result = set()
        for name in lower_names:
            if name not in deleted_lower:
                result.add(name)
        result |= upper_names

        return sorted(result)

    def read(self, path: str, size: int = -1, offset: int = 0) -> bytes:
        """Read file contents from the merged view."""
        path = self._norm(path)
        entry = self._upper.get(path)
        if entry is not None:
            if entry.deleted:
                raise OSError(errno.ENOENT, f"No such file or directory: {path}")
            if entry.kind == "dir":
                raise OSError(errno.EISDIR, f"Is a directory: {path}")
            data = entry.data
            if offset > 0:
                data = data[offset:]
            if size >= 0:
                data = data[:size]
            return data
        # Fall through to lower
        return self.lower.read(path, size=size, offset=offset)

    def write(self, path: str, data: bytes, offset: int = 0) -> int:
        """Write file contents to the upper layer."""
        path = self._norm(path)
        parent = self._parent(path)
        name = self._basename(path)

        with self._lock:
            # Ensure parent directory exists in upper
            parent_entry = self._upper.get(parent)
            if parent_entry is None or parent_entry.deleted:
                # Create parent dir in upper if it doesn't exist
                if not self._lower_is_dir(parent):
                    raise OSError(errno.ENOENT,
                                  f"No such file or directory: {parent}")
                parent_entry = OverlayEntry(kind="dir", mode=0o755)
                parent_entry.inode = self._alloc_inode()
                self._upper[parent] = parent_entry

            entry = self._upper.get(path)
            if entry is None:
                # New file in upper layer
                new_data = bytearray(offset) + data
                entry = OverlayEntry(kind="file", mode=0o644,
                                     data=bytes(new_data))
                entry.inode = self._alloc_inode()
                self._upper[path] = entry
            elif entry.deleted:
                # File was deleted in upper, recreate
                new_data = bytearray(offset) + data
                entry = OverlayEntry(kind="file", mode=0o644,
                                     data=bytes(new_data))
                entry.inode = self._alloc_inode()
                self._upper[path] = entry
            elif entry.kind == "dir":
                raise OSError(errno.EISDIR, f"Is a directory: {path}")
            else:
                # Modify existing upper entry
                old = bytearray(entry.data)
                if offset > len(old):
                    old.extend(b"\x00" * (offset - len(old)))
                old[offset:offset + len(data)] = data
                entry.data = bytes(old)
                entry.size = len(entry.data)

            # Register in parent
            if name and name not in parent_entry.children:
                parent_entry.children[name] = entry.inode

            return len(data)

    def truncate(self, path: str, size: int) -> None:
        """Truncate a file to the given size."""
        path = self._norm(path)
        with self._lock:
            entry = self._upper.get(path)
            if entry is None or entry.deleted:
                # Read from lower first, then create upper copy
                try:
                    lower_data = self.lower.read(path)
                except OSError:
                    raise OSError(errno.ENOENT,
                                  f"No such file or directory: {path}")
                new_data = lower_data[:size]
                entry = OverlayEntry(kind="file", mode=0o644,
                                     data=new_data)
                entry.inode = self._alloc_inode()
                self._upper[path] = entry
            elif entry.kind == "dir":
                raise OSError(errno.EISDIR, f"Is a directory: {path}")
            else:
                if size < entry.size:
                    entry.data = entry.data[:size]
                elif size > entry.size:
                    entry.data = entry.data + b"\x00" * (size - entry.size)
                entry.size = size

    def mkdir(self, path: str, mode: int = 0o755) -> None:
        """Create a directory in the upper layer."""
        path = self._norm(path)
        parent = self._parent(path)
        name = self._basename(path)

        with self._lock:
            if self.exists(path):
                raise OSError(errno.EEXIST, f"File exists: {path}")

            parent_entry = self._upper.get(parent)
            if parent_entry is None or parent_entry.deleted:
                if not self._lower_is_dir(parent):
                    raise OSError(errno.ENOENT,
                                  f"No such file or directory: {parent}")
                parent_entry = OverlayEntry(kind="dir", mode=0o755)
                parent_entry.inode = self._alloc_inode()
                self._upper[parent] = parent_entry

            entry = OverlayEntry(kind="dir", mode=mode)
            entry.inode = self._alloc_inode()
            self._upper[path] = entry
            if name:
                parent_entry.children[name] = entry.inode

    def unlink(self, path: str) -> None:
        """Delete a file (mark as deleted in upper layer)."""
        path = self._norm(path)
        parent = self._parent(path)
        name = self._basename(path)

        with self._lock:
            entry = self._upper.get(path)
            if entry is not None and not entry.deleted:
                if entry.kind == "dir":
                    raise OSError(errno.EISDIR, f"Is a directory: {path}")
                entry.deleted = True
                # Remove from parent
                parent_entry = self._upper.get(parent)
                if parent_entry and name in parent_entry.children:
                    del parent_entry.children[name]
                return

            # If it exists in lower, mark as deleted in upper
            if self._lower_exists(path):
                deleted = OverlayEntry(kind="file", deleted=True)
                deleted.inode = 0
                self._upper[path] = deleted
                # Mark in parent
                parent_entry = self._upper.get(parent)
                if parent_entry is None:
                    parent_entry = OverlayEntry(kind="dir", mode=0o755)
                    parent_entry.inode = self._alloc_inode()
                    self._upper[parent] = parent_entry
                if name:
                    parent_entry.children[name] = 0  # 0 = deleted marker
                return

            raise OSError(errno.ENOENT, f"No such file or directory: {path}")

    def rmdir(self, path: str) -> None:
        """Remove a directory (mark as deleted in upper layer)."""
        path = self._norm(path)
        if path == "/":
            raise OSError(errno.EBUSY, "Cannot remove root directory")

        with self._lock:
            # Check if directory is empty in merged view
            contents = self.readdir(path)
            if contents:
                raise OSError(errno.ENOTEMPTY, f"Directory not empty: {path}")

            entry = self._upper.get(path)
            if entry is not None and not entry.deleted:
                if entry.kind != "dir":
                    raise OSError(errno.ENOTDIR, f"Not a directory: {path}")
                entry.deleted = True
                return

            if self._lower_is_dir(path):
                deleted = OverlayEntry(kind="dir", deleted=True)
                deleted.inode = 0
                self._upper[path] = deleted
                return

            raise OSError(errno.ENOENT, f"No such file or directory: {path}")

    def rename(self, src: str, dst: str) -> None:
        """Rename/move a file or directory within the merged view."""
        src = self._norm(src)
        dst = self._norm(dst)

        with self._lock:
            # Source must exist somewhere
            if not self.exists(src) and not self._lower_exists(src):
                raise OSError(errno.ENOENT, f"No such file or directory: {src}")

            src_parent = self._parent(src)
            src_name = self._basename(src)
            dst_parent = self._parent(dst)
            dst_name = self._basename(dst)

            # Read source data if it's in the lower layer
            entry = self._upper.get(src)
            if entry is None or entry.deleted:
                # Read from lower
                if self._lower_is_dir(src):
                    entry = OverlayEntry(kind="dir", mode=0o755)
                else:
                    data = self.lower.read(src)
                    entry = OverlayEntry(kind="file", mode=0o644, data=data)
                entry.inode = self._alloc_inode()
            else:
                # Detach from old parent
                old_parent = self._upper.get(src_parent)
                if old_parent and src_name in old_parent.children:
                    del old_parent.children[src_name]

            # Mark source as deleted
            deleted = OverlayEntry(deleted=True)
            deleted.inode = 0
            self._upper[src] = deleted

            # Place at destination
            entry.inode = self._alloc_inode() if entry.inode == 0 else entry.inode
            self._upper[dst] = entry

            # Register in new parent
            dst_parent_entry = self._upper.get(dst_parent)
            if dst_parent_entry is None or dst_parent_entry.deleted:
                dst_parent_entry = OverlayEntry(kind="dir", mode=0o755)
                dst_parent_entry.inode = self._alloc_inode()
                self._upper[dst_parent] = dst_parent_entry
            if dst_name:
                dst_parent_entry.children[dst_name] = entry.inode

    # -- snapshot / restore ----------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Capture the current state of the upper layer.

        Returns a serializable dict that can be passed to
        ``restore_snapshot()`` to replay the overlay state.
        """
        with self._lock:
            snap: Dict[str, Any] = {}
            for path, entry in self._upper.items():
                if entry.kind == "dir":
                    snap[path] = {
                        "kind": "dir",
                        "mode": entry.mode,
                        "deleted": entry.deleted,
                        "children": dict(entry.children),
                    }
                else:
                    snap[path] = {
                        "kind": "file",
                        "mode": entry.mode,
                        "deleted": entry.deleted,
                        "data": entry.data.hex(),
                        "size": entry.size,
                    }
            return {"container_id": self.container_id, "entries": snap}

    def restore_snapshot_data(self, snap: Dict[str, Any]) -> None:
        """Restore the upper layer from a snapshot dict."""
        with self._lock:
            self._upper.clear()
            for path, info in snap.get("entries", {}).items():
                kind = info.get("kind", "file")
                deleted = info.get("deleted", False)
                mode = info.get("mode", 0o644)
                if kind == "dir":
                    entry = OverlayEntry(kind="dir", mode=mode,
                                         deleted=deleted)
                    entry.children = info.get("children", {})
                else:
                    data = bytes.fromhex(info.get("data", ""))
                    entry = OverlayEntry(kind="file", mode=mode, data=data,
                                         deleted=deleted)
                entry.inode = self._alloc_inode()
                self._upper[path] = entry

    # -- diff ------------------------------------------------------------

    def diff(self) -> Dict[str, Dict[str, Any]]:
        """Report changes made in the upper layer vs the lower.

        Returns a dict mapping paths to their change type:
        ``{"type": "created"|"modified"|"deleted"}``.
        """
        changes: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            for path, entry in self._upper.items():
                if entry.deleted:
                    if self._lower_exists(path):
                        changes[path] = {"type": "deleted"}
                elif path == "/":
                    continue  # root is always present
                else:
                    if self._lower_exists(path):
                        changes[path] = {"type": "modified"}
                    else:
                        changes[path] = {"type": "created"}
        return changes

    # -- stats -----------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Return overlay statistics."""
        with self._lock:
            created = sum(1 for p, e in self._upper.items()
                          if not e.deleted and e.kind == "file"
                          and not self._lower_exists(p))
            modified = sum(1 for p, e in self._upper.items()
                           if not e.deleted and e.kind == "file"
                           and self._lower_exists(p))
            deleted = sum(1 for e in self._upper.values() if e.deleted)
            dirs = sum(1 for e in self._upper.values()
                       if not e.deleted and e.kind == "dir")
            return {
                "container_id": self.container_id,
                "upper_entries": len(self._upper),
                "created": created,
                "modified": modified,
                "deleted": deleted,
                "dirs": dirs,
            }

    # -- NyFS compatibility layer ----------------------------------------
    # The vault storage service expects certain NyFS methods on the
    # filesystem object.  These delegate to the lower NyFS so the
    # overlay can be used transparently in the storage service.

    @property
    def dirty(self) -> bool:
        """Whether the overlay has unsaved changes."""
        return bool(self._upper)

    @property
    def base_path(self):
        """Delegate to the lower filesystem's base_path."""
        return self.lower.base_path

    @property
    def block_size(self) -> int:
        """Delegate to the lower filesystem's block_size."""
        return self.lower.block_size

    @property
    def dek(self):
        """Delegate to the lower filesystem's dek."""
        return self.lower.dek

    def save(self, **kwargs) -> None:
        """Save the lower filesystem (the overlay's upper layer is
        in-memory only — persistence is a lower-layer concern."""
        self.lower.save(**kwargs)

    def walk(self) -> Dict[str, Any]:
        """Walk the merged filesystem tree (delegates to lower with
        upper modifications applied)."""
        result = self.lower.walk()
        # Apply upper-layer changes
        for path, entry in self._upper.items():
            if entry.deleted:
                result.pop(path, None)
            elif entry.kind == "file":
                result[path] = entry  # simplified — real impl would
                # need a full inode-like object
        return result

    def create_snapshot(self, snapshot_id=None):
        """Delegate snapshot creation to the lower filesystem."""
        return self.lower.create_snapshot(snapshot_id)

    def restore_snapshot(self, snapshot_id):
        """Restore from an overlay snapshot (dict) or delegate to lower."""
        if isinstance(snapshot_id, dict):
            # Overlay snapshot (from self.snapshot())
            self.restore_snapshot_data(snapshot_id)
            return
        return self.lower.restore_snapshot(snapshot_id)

    def list_snapshots(self):
        """Delegate snapshot listing to the lower filesystem."""
        return self.lower.list_snapshots()

    def delete_snapshot(self, snapshot_id):
        """Delegate snapshot deletion to the lower filesystem."""
        return self.lower.delete_snapshot(snapshot_id)
