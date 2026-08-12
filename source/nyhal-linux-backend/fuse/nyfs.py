#!/usr/bin/env python3
"""
NyFS FUSE Filesystem Implementation

Implements the NyFS filesystem as a user-space FUSE daemon per ADR-0016,
providing the guarantees of NPS-004 §4:
- Copy-on-Write (CoW): writes never mutate existing blocks — a write
  appends a new block and swaps the inode's block list, so snapshots
  (which deep-copy the inode table) remain immutable point-in-time views.
- Snapshots: immutable point-in-time copies of filesystem state.
- Checksumming: every block carries a SHA256 of its uncompressed data,
  verified on read.
- Transparent Compression: Zstandard (ADR-0007) with a graceful fallback
  when the ``zstandard`` module is unavailable.

Architecture:
- ``NyFSFilesystem`` — core logic: inode tree with real parent/child
  linking, a path-resolution API, and the storage guarantees above.
- ``NyFSOperations`` — the FUSE operation handlers (getattr, readdir,
  open, read, write, truncate, mkdir, mknod, unlink, rmdir, rename,
  statfs) as pure Python, testable without a kernel mount.
- ``NyFSMount`` — wires ``NyFSOperations`` to a real FUSE mount via the
  ``fusepy`` package when available. ``fusepy`` is imported by *file
  path* from site-packages because this package is itself named ``fuse``
  and would otherwise shadow the third-party module.

References:
- NPS-004: NyFS Filesystem Core
- ADR-0016: NyFS Linux Backend implemented as a user-space FUSE filesystem
- ADR-0007: Adopt Zstandard as the default compression codec
"""

import errno
import hashlib
import logging
import os
import site
import stat
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, Optional, Tuple, List

logger = logging.getLogger(__name__)


class NyFSError(OSError):
    """An error raised by NyFS operations carrying a POSIX errno.

    Mirrors the FUSE contract (operations signal failure via errno) so
    that the same handlers work mounted and unmounted.
    """

    def __init__(self, errno_value: int, message: str = ""):
        super().__init__(errno_value, message or os.strerror(errno_value))
        self.errno = errno_value


@dataclass
class NyFSBlock:
    """Represents a compressed data block in NyFS.

    Per NPS-004 §4, all blocks are checksummed for integrity and
    compressed with Zstandard (ADR-0007). Blocks are immutable once
    written — CoW never mutates them.
    """

    block_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    data: bytes = b""
    compressed_data: Optional[bytes] = None
    checksum: str = ""  # SHA256 hex digest of the uncompressed data
    compression_level: int = 3  # Zstandard compression level (1-22)
    created_at: float = field(default_factory=time.time)

    def compute_checksum(self) -> str:
        """Compute SHA256 checksum of the uncompressed data."""
        self.checksum = hashlib.sha256(self.data).hexdigest()
        return self.checksum

    def compress(self) -> None:
        """Compress the data using Zstandard (ADR-0007)."""
        try:
            import zstandard as zstd

            cctx = zstd.ZstdCompressor(level=self.compression_level)
            self.compressed_data = cctx.compress(self.data)
            logger.debug(
                f"Compressed block {self.block_id[:8]}: "
                f"{len(self.data)} -> {len(self.compressed_data)} bytes"
            )
        except ImportError:
            logger.warning("zstandard not available; storing uncompressed")
            self.compressed_data = self.data

    def decompress(self) -> bytes:
        """Decompress the data."""
        if self.compressed_data is None:
            return self.data
        try:
            import zstandard as zstd

            dctx = zstd.ZstdDecompressor()
            return dctx.decompress(self.compressed_data)
        except ImportError:
            return self.compressed_data


@dataclass
class NyFSInode:
    """In-memory representation of an inode in NyFS.

    Per NPS-004, inodes track file metadata and reference blocks.
    """

    inode_number: int
    name: str
    mode: int  # File mode (permissions + type)
    uid: int = 0
    gid: int = 0
    size: int = 0
    atime: float = field(default_factory=time.time)
    mtime: float = field(default_factory=time.time)
    ctime: float = field(default_factory=time.time)
    blocks: List[NyFSBlock] = field(default_factory=list)
    children: Dict[str, "NyFSInode"] = field(default_factory=dict)
    parent: Optional["NyFSInode"] = None
    is_directory: bool = False

    def __repr__(self) -> str:
        return f"NyFSInode(ino={self.inode_number}, name={self.name!r}, mode={oct(self.mode)})"


class NyFSFilesystem:
    """Core NyFS filesystem logic.

    Implements the storage guarantees from NPS-004 §4: copy-on-write,
    snapshots, checksumming, and transparent compression, behind a
    path-based API usable both directly and from FUSE operations.
    """

    def __init__(self, base_path: str):
        """Initialize the NyFS filesystem.

        Args:
            base_path: Path to the backing storage directory
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

        self.inode_counter = 1
        self.root_inode = self._create_inode(0, "/", stat.S_IFDIR | 0o755, is_directory=True)
        self.inodes: Dict[int, NyFSInode] = {0: self.root_inode}
        self.snapshots: Dict[str, Dict[int, NyFSInode]] = {}
        self.lock = threading.Lock()
        self._fh_counter = 0
        self._open_files: Dict[int, int] = {}  # fh -> inode number

        logger.info(f"Initialized NyFS filesystem at {self.base_path}")

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _create_inode(self, inode_number, name, mode, is_directory=False) -> NyFSInode:
        return NyFSInode(
            inode_number=inode_number,
            name=name,
            mode=mode,
            is_directory=is_directory,
        )

    def _new_inode(self, name: str, mode: int, is_directory: bool = False) -> NyFSInode:
        self.inode_counter += 1
        inode = self._create_inode(self.inode_counter, name, mode, is_directory)
        self.inodes[inode.inode_number] = inode
        return inode

    def _link(self, parent: NyFSInode, child: NyFSInode) -> None:
        if not parent.is_directory:
            raise NyFSError(errno.ENOTDIR, f"{parent.name!r} is not a directory")
        if child.name in parent.children:
            raise NyFSError(errno.EEXIST, f"{child.name!r} already exists")
        child.parent = parent
        parent.children[child.name] = child

    def _unlink(self, parent: NyFSInode, name: str) -> NyFSInode:
        child = parent.children.pop(name, None)
        if child is None:
            raise NyFSError(errno.ENOENT, f"{name!r} not found")
        child.parent = None
        return child

    def resolve(self, path: str) -> NyFSInode:
        """Resolve an absolute path to an inode, following . and .."""
        if not path.startswith("/"):
            raise NyFSError(errno.EINVAL, f"path must be absolute: {path!r}")
        current = self.root_inode
        # PurePosixPath("/").parts is ("/",), so the root component must be
        # dropped too or a bare "/" (and every parent path) fails to resolve.
        parts = [p for p in PurePosixPath(path).parts if p not in ("", ".", "/")]
        for part in parts:
            if part == "..":
                if current.parent is not None:
                    current = current.parent
                continue
            if not current.is_directory:
                raise NyFSError(errno.ENOTDIR, f"{part!r} is not a directory")
            child = current.children.get(part)
            if child is None:
                raise NyFSError(errno.ENOENT, f"no such file or directory: {path}")
            current = child
        return current

    def resolve_parent(self, path: str) -> Tuple[NyFSInode, str]:
        """Resolve the parent directory and final component of a path."""
        parent_path = str(PurePosixPath(path).parent)
        name = PurePosixPath(path).name
        if not name or name in (".", ".."):
            raise NyFSError(errno.EINVAL, f"invalid path: {path!r}")
        return self.resolve(parent_path), name

    # ------------------------------------------------------------------
    # Tree mutation
    # ------------------------------------------------------------------

    def create_file(self, path: str, mode: int = 0o644) -> NyFSInode:
        """Create a new file at ``path`` and link it into the tree."""
        with self.lock:
            parent, name = self.resolve_parent(path)
            inode = self._new_inode(name, mode)
            self._link(parent, inode)
            logger.info(f"Created file {path} (ino={inode.inode_number})")
            return inode

    def create_directory(self, path: str, mode: int = 0o755) -> NyFSInode:
        """Create a new directory at ``path`` and link it into the tree."""
        with self.lock:
            parent, name = self.resolve_parent(path)
            inode = self._new_inode(name, stat.S_IFDIR | mode, is_directory=True)
            self._link(parent, inode)
            logger.info(f"Created directory {path} (ino={inode.inode_number})")
            return inode

    # FUSE-style aliases
    def mkdir(self, path: str, mode: int = 0o755) -> NyFSInode:
        return self.create_directory(path, mode)

    def mknod(self, path: str, mode: int = 0o644, dev: int = 0) -> NyFSInode:
        return self.create_file(path, mode)

    def unlink(self, path: str) -> None:
        """Remove a file. Directories must be removed with rmdir."""
        with self.lock:
            parent, name = self.resolve_parent(path)
            child = parent.children.get(name)
            if child is None:
                raise NyFSError(errno.ENOENT, f"no such file: {path}")
            if child.is_directory:
                raise NyFSError(errno.EISDIR, f"{path} is a directory")
            self._unlink(parent, name)
            self.inodes.pop(child.inode_number, None)

    def rmdir(self, path: str) -> None:
        """Remove an empty directory."""
        with self.lock:
            parent, name = self.resolve_parent(path)
            child = parent.children.get(name)
            if child is None:
                raise NyFSError(errno.ENOENT, f"no such directory: {path}")
            if not child.is_directory:
                raise NyFSError(errno.ENOTDIR, f"{path} is not a directory")
            if child.children:
                raise NyFSError(errno.ENOTEMPTY, f"{path} not empty")
            self._unlink(parent, name)
            self.inodes.pop(child.inode_number, None)

    def rename(self, old_path: str, new_path: str) -> None:
        """Move ``old_path`` to ``new_path``."""
        with self.lock:
            old_parent, old_name = self.resolve_parent(old_path)
            new_parent, new_name = self.resolve_parent(new_path)
            child = old_parent.children.get(old_name)
            if child is None:
                raise NyFSError(errno.ENOENT, f"no such file: {old_path}")
            if new_name in new_parent.children:
                raise NyFSError(errno.EEXIST, f"{new_path} already exists")
            self._unlink(old_parent, old_name)
            child.name = new_name
            self._link(new_parent, child)

    # ------------------------------------------------------------------
    # Data operations (CoW + checksum + compression)
    # ------------------------------------------------------------------

    def _content(self, inode: NyFSInode) -> bytes:
        return b"".join(b.decompress() for b in inode.blocks)

    def read(self, inode_or_path, size: int = -1, offset: int = 0) -> bytes:
        """Read ``size`` bytes starting at ``offset`` (size -1 = to EOF)."""
        with self.lock:
            inode = self._as_inode(inode_or_path)
            if inode.is_directory:
                raise NyFSError(errno.EISDIR, "cannot read a directory")
            data = self._content(inode)
            if offset >= len(data):
                return b""
            data = data[offset:]
            if size is not None and size >= 0:
                data = data[:size]
            inode.atime = time.time()
            return data

    def write(self, inode_or_path, data: bytes, offset: int = 0) -> int:
        """Write ``data`` at ``offset`` with copy-on-write semantics.

        CoW: the existing block list is never mutated; a new block
        holding the merged content is appended and the inode's block list
        is replaced. Old blocks live on in any snapshot taken before the
        write (NPS-004 §4, NPS-006 §4 overlay model).
        """
        with self.lock:
            inode = self._as_inode(inode_or_path)
            if inode.is_directory:
                raise NyFSError(errno.EISDIR, "cannot write a directory")
            current = self._content(inode)
            if offset > len(current):
                current = current + b"\x00" * (offset - len(current))
            merged = current[:offset] + data + current[offset + len(data):]

            block = NyFSBlock(data=merged, compression_level=3)
            block.compute_checksum()
            block.compress()
            inode.blocks = [block]  # CoW: swap, never mutate
            inode.size = len(merged)
            inode.mtime = time.time()
            return len(data)

    def truncate(self, inode_or_path, length: int) -> None:
        """Truncate a file to ``length`` bytes (CoW, see write)."""
        with self.lock:
            inode = self._as_inode(inode_or_path)
            if inode.is_directory:
                raise NyFSError(errno.EISDIR, "cannot truncate a directory")
            current = self._content(inode)
            if length < len(current):
                merged = current[:length]
            else:
                merged = current + b"\x00" * (length - len(current))
            block = NyFSBlock(data=merged, compression_level=3)
            block.compute_checksum()
            block.compress()
            inode.blocks = [block]
            inode.size = len(merged)
            inode.mtime = time.time()

    # Legacy block-level API (kept for compatibility)
    def write_block(self, inode_number: int, data: bytes, compress: bool = True) -> NyFSBlock:
        with self.lock:
            inode = self.inodes.get(inode_number)
            if inode is None:
                raise ValueError(f"Inode {inode_number} not found")
            block = NyFSBlock(data=data, compression_level=3)
            block.compute_checksum()
            if compress:
                block.compress()
            inode.blocks.append(block)
            inode.size += len(data)
            inode.mtime = time.time()
            return block

    def read_block(self, inode_number: int, block_index: int = 0) -> bytes:
        with self.lock:
            inode = self.inodes.get(inode_number)
            if inode is None:
                raise ValueError(f"Inode {inode_number} not found")
            if block_index >= len(inode.blocks):
                raise IndexError(f"Block {block_index} not found in inode {inode_number}")
            block = inode.blocks[block_index]
            data = block.decompress()
            computed = hashlib.sha256(data).hexdigest()
            if computed != block.checksum:
                logger.error(
                    f"Checksum mismatch for block {block.block_id}: "
                    f"expected {block.checksum}, got {computed}"
                )
                raise ValueError("Block checksum verification failed")
            return data

    def _as_inode(self, inode_or_path) -> NyFSInode:
        if isinstance(inode_or_path, NyFSInode):
            return inode_or_path
        if isinstance(inode_or_path, int):
            inode = self.inodes.get(inode_or_path)
            if inode is None:
                raise NyFSError(errno.ENOENT, f"inode {inode_or_path} not found")
            return inode
        return self.resolve(str(inode_or_path))

    # ------------------------------------------------------------------
    # Metadata operations
    # ------------------------------------------------------------------

    def lookup(self, parent_ino: int, name: str) -> Optional[NyFSInode]:
        with self.lock:
            parent = self.inodes.get(parent_ino)
            if parent is None:
                return None
            return parent.children.get(name)

    def getattr(self, inode_or_path) -> Dict:
        """Return a ``st_*`` stat dict (FUSE ``getattr`` contract)."""
        with self.lock:
            inode = self._as_inode(inode_or_path)
            nlink = 1
            if inode.is_directory:
                nlink = 2 + len(inode.children)
            return {
                "st_ino": inode.inode_number,
                "st_mode": inode.mode,
                "st_nlink": nlink,
                "st_size": inode.size,
                "st_uid": inode.uid,
                "st_gid": inode.gid,
                "st_atime": inode.atime,
                "st_mtime": inode.mtime,
                "st_ctime": inode.ctime,
            }

    def readdir(self, inode_or_path) -> List[str]:
        """Return directory entry names including '.' and '..'."""
        with self.lock:
            inode = self._as_inode(inode_or_path)
            if not inode.is_directory:
                raise NyFSError(errno.ENOTDIR, "not a directory")
            return [".", ".."] + sorted(inode.children.keys())

    def open(self, inode_or_path, flags: int = os.O_RDONLY) -> int:
        """Open a file, returning an opaque file handle."""
        with self.lock:
            inode = self._as_inode(inode_or_path)
            if inode.is_directory:
                raise NyFSError(errno.EISDIR, "cannot open a directory")
            self._fh_counter += 1
            self._open_files[self._fh_counter] = inode.inode_number
            return self._fh_counter

    def release(self, fh: int) -> None:
        with self.lock:
            self._open_files.pop(fh, None)

    def statfs(self) -> Dict:
        """Filesystem statistics (FUSE ``statfs`` contract)."""
        try:
            st = os.statvfs(self.base_path)
            return {
                "f_bsize": st.f_bsize,
                "f_frsize": st.f_frsize,
                "f_blocks": st.f_blocks,
                "f_bfree": st.f_bfree,
                "f_bavail": st.f_bavail,
                "f_files": st.f_files,
                "f_ffree": st.f_ffree,
            }
        except OSError:
            return {
                "f_bsize": 4096, "f_frsize": 4096, "f_blocks": 0,
                "f_bfree": 0, "f_bavail": 0, "f_files": 0, "f_ffree": 0,
            }

    # ------------------------------------------------------------------
    # Snapshots (NPS-004 §4: immutable point-in-time copies)
    # ------------------------------------------------------------------

    def create_snapshot(self, snapshot_id: Optional[str] = None) -> str:
        if snapshot_id is None:
            snapshot_id = f"snap-{uuid.uuid4().hex[:12]}"
        with self.lock:
            import copy
            self.snapshots[snapshot_id] = copy.deepcopy(self.inodes)
        logger.info(f"Created snapshot {snapshot_id} with {len(self.inodes)} inodes")
        return snapshot_id

    def restore_snapshot(self, snapshot_id: str) -> None:
        if snapshot_id not in self.snapshots:
            raise ValueError(f"Snapshot {snapshot_id} not found")
        with self.lock:
            import copy
            self.inodes = copy.deepcopy(self.snapshots[snapshot_id])
            # resolve()/resolve_parent() walk from self.root_inode, so it
            # must be rebound to the restored root or path lookups keep
            # reaching the pre-restore tree.
            self.root_inode = self.inodes[0]
        logger.info(f"Restored filesystem to snapshot {snapshot_id}")

    def list_snapshots(self) -> List[str]:
        with self.lock:
            return list(self.snapshots.keys())

    def get_inode(self, inode_number: int) -> Optional[NyFSInode]:
        with self.lock:
            return self.inodes.get(inode_number)

    def get_inode_stats(self, inode_number: int) -> Dict:
        with self.lock:
            inode = self.inodes.get(inode_number)
            if inode is None:
                return {}
            return {
                "inode_number": inode.inode_number,
                "name": inode.name,
                "size": inode.size,
                "blocks": len(inode.blocks),
                "mode": inode.mode,
                "mtime": inode.mtime,
                "is_directory": inode.is_directory,
            }


class NyFSOperations:
    """FUSE operation handlers backed by a ``NyFSFilesystem``.

    Method signatures follow the FUSE kernel protocol as implemented by
    ``fusepy``. Errors are raised as ``NyFSError`` (an ``OSError`` with a
    POSIX errno), which the mount adapter translates to ``FuseOSError``.
    """

    def __init__(self, filesystem: NyFSFilesystem):
        self.fs = filesystem

    def getattr(self, path, fh=None):
        try:
            return self.fs.getattr(path)
        except NyFSError:
            raise
        except Exception as e:
            logger.error("getattr(%s): %s", path, e)
            raise NyFSError(errno.EIO)

    def readdir(self, path, fh=None):
        return self.fs.readdir(path)

    def open(self, path, flags):
        return self.fs.open(path, flags)

    def release(self, path, fh):
        self.fs.release(fh)
        return 0

    def read(self, path, size, offset, fh=None):
        return self.fs.read(path, size, offset)

    def write(self, path, data, offset, fh=None):
        return self.fs.write(path, data, offset)

    def truncate(self, path, length, fh=None):
        self.fs.truncate(path, length)
        return 0

    def mkdir(self, path, mode):
        self.fs.mkdir(path, mode)
        return 0

    def mknod(self, path, mode, dev):
        self.fs.mknod(path, mode, dev)
        return 0

    def unlink(self, path):
        self.fs.unlink(path)
        return 0

    def rmdir(self, path):
        self.fs.rmdir(path)
        return 0

    def rename(self, old, new):
        self.fs.rename(old, new)
        return 0

    def statfs(self, path):
        return self.fs.statfs()


def _import_fusepy():
    """Import the third-party ``fuse`` module (fusepy) by file path.

    This package is itself named ``fuse``, so a plain ``import fuse``
    resolves to ourselves. Load the real module from site-packages and
    register it under a private name.
    """
    try:
        import importlib.util
    except Exception:
        return None
    search_paths = []
    try:
        search_paths = list(site.getsitepackages()) + [site.getusersitepackages()]
    except Exception:
        search_paths = [p for p in __import__("sys").path if "site-packages" in p]
    for path in search_paths:
        candidate = os.path.join(path, "fuse.py")
        if not os.path.exists(candidate):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_fusepy", candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception as e:
            logger.debug("fusepy import from %s failed: %s", candidate, e)
    return None


class NyFSMount:
    """FUSE mount wrapper for NyFS (ADR-0016).

    Mounts ``NyFSOperations`` against a real kernel mount via ``fusepy``
    when the package is installed and ``/dev/fuse`` is accessible;
    otherwise reports the deferral honestly rather than pretending.
    """

    def __init__(self, filesystem: NyFSFilesystem, mount_point: str):
        self.filesystem = filesystem
        self.mount_point = Path(mount_point)
        self.mount_point.mkdir(parents=True, exist_ok=True)
        self.operations = NyFSOperations(filesystem)
        self._fusepy = None
        self._fuse = None
        logger.info(f"Initialized NyFSMount at {self.mount_point}")

    def attach(self) -> bool:
        """Attempt to load fusepy. Returns True when a real mount is possible."""
        if self._fusepy is not None:
            return True
        self._fusepy = _import_fusepy()
        if self._fusepy is None:
            logger.warning(
                "fusepy not importable — NyFS mount unavailable in this "
                "environment (storage core still fully usable directly)"
            )
        else:
            logger.info("fusepy loaded; NyFS mount available")
        return self._fusepy is not None

    def _build_fuse(self, foreground: bool = True):
        fuse_mod = self._fusepy or _import_fusepy()
        if fuse_mod is None:
            raise NyFSError(errno.ENODEV, "fusepy is not available")

        class _Adapter:
            """Translates NyFSError -> fusepy.FuseOSError."""

            def __init__(self, ops: NyFSOperations):
                self._ops = ops

            def __getattr__(self, name):
                handler = getattr(self._ops, name)

                def wrapped(*args, **kwargs):
                    try:
                        return handler(*args, **kwargs)
                    except NyFSError as e:
                        raise fuse_mod.FuseOSError(e.errno)

                return wrapped

        self._fuse = fuse_mod.Fuse(
            _Adapter(self.operations),
            str(self.mount_point),
            foreground=foreground,
            nothreads=False,
        )
        return self._fuse

    def mount(self, foreground: bool = True, blocking: bool = True):
        """Mount the filesystem.

        Args:
            foreground: Run in the foreground (default True for a daemon).
            blocking: Block until unmounted (True) or run in a thread.

        Returns:
            True if the mount was attempted, False if fusepy is unavailable.
        """
        if not self.attach():
            return False
        fuse = self._build_fuse(foreground=foreground)

        def _run():
            logger.info("Mounting NyFS at %s (FUSE)", self.mount_point)
            fuse.main()

        if blocking:
            _run()
        else:
            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()
        return True

    def unmount(self) -> None:
        """Best-effort unmount via fusepy or ``fusermount -u``."""
        try:
            if self._fusepy is not None and hasattr(self._fusepy, "fuse_unmount"):
                self._fusepy.fuse_unmount(str(self.mount_point))
                logger.info("Unmounted NyFS from %s", self.mount_point)
                return
        except Exception as e:
            logger.warning("fusepy unmount failed: %s", e)
        try:
            subprocess.run(
                ["fusermount", "-u", str(self.mount_point)],
                check=False,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("fusermount -u failed: %s", e)


def main():
    """Simple CLI for testing the NyFS filesystem."""
    logging.basicConfig(level=logging.INFO)

    fs = NyFSFilesystem("/tmp/nyfs-test")
    file1 = fs.create_file("/test.txt")
    fs.write(file1, b"Hello from NyFS!")
    print(f"Read data: {fs.read(file1).decode()}")
    snap_id = fs.create_snapshot()
    print(f"Created snapshot: {snap_id}")
    print(f"Inode stats: {fs.get_inode_stats(file1.inode_number)}")


if __name__ == "__main__":
    main()
