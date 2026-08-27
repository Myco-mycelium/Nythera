#!/usr/bin/env python3
"""
NyFS FUSE Filesystem Implementation

Implements the NyFS filesystem as a user-space FUSE daemon per ADR-0016,
providing the guarantees of NPS-004 §4:
- Copy-on-Write (CoW): writes never mutate existing blocks. File content
  is stored as a list of fixed-size (``BLOCK_SIZE``, 64 KiB by default)
  blocks; a write rewrites only the blocks it touches — the merge-and-
  recompress-the-whole-file path was replaced after first-pass
  benchmarking showed it dominated NyFS overhead (40.5 vs 884 MB/s
  write; ``tests/BENCHMARK_RESULTS.md`` §3). Old blocks live on in any
  snapshot taken before the write, so snapshots (which deep-copy the
  inode table) remain immutable point-in-time views.
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

import base64
import ctypes
import errno
import json
import logging
import os
import site
import stat
import struct
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, Optional, Tuple, List

from . import nyfs_codec  # ADR-0020 priority #3 FFI loader (Rust codec)

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

    Through the path API every block is exactly ``block_size`` bytes,
    even for a short file (the final block is zero-padded; reads clamp
    to the logical size so padding never leaks). The legacy
    ``write_block`` may append arbitrary-size blocks; ``write``/``read``
    re-block such inodes on first use.
    """

    block_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    data: bytes = b""
    compressed_data: Optional[bytes] = None
    checksum: str = ""  # SHA256 hex digest of the (possibly encrypted) data
    compression_level: int = 3  # Zstandard compression level (1-22)
    created_at: float = field(default_factory=time.time)

    def compute_checksum(self) -> str:
        """Compute SHA256 checksum of the uncompressed data."""
        self.checksum = nyfs_codec.checksum(self.data)
        return self.checksum

    def compress(self) -> None:
        """Compress the data using Zstandard (ADR-0007), routed through
        the NyFS codec module (Rust FFI when available; the ``zstandard``
        module otherwise; data stored uncompressed when neither exists)."""
        self.compressed_data = nyfs_codec.compress(
            self.data, self.compression_level
        )
        logger.debug(
            f"Compressed block {self.block_id[:8]}: "
            f"{len(self.data)} -> {len(self.compressed_data)} bytes"
        )

    def decompress(self) -> bytes:
        """Decompress the data, verifying its checksum (NPS-004 §4.3)."""
        if self.compressed_data is None:
            return self.data
        return nyfs_codec.decompress_verify(
            self.compressed_data, self.checksum
        )


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

    File content is stored as fixed-size blocks (``block_size``, default
    64 KiB). A write rebuilds only the blocks it overlaps, so its
    compress cost is bounded by the bytes written, not the file size.

    Durability (NPS-004 §7): ``save()`` persists the filesystem to
    ``<base_path>/state/metadata.json`` (inode tree + block references)
    and ``<base_path>/state/blocks/`` (one immutable file per block). The
    commit sequence is: write new block files, fsync them, then
    atomically swap the metadata file (write-temp + fsync + rename), so
    a crash at any point leaves either the old or the new consistent
    state — never a mixed one. ``save()`` is explicit — a mounted daemon
    calls it at transaction boundaries (the FUSE ``fsync`` handler is
    the natural hook) — and ``load()`` reconstructs a filesystem from a
    previously saved state.
    """

    BLOCK_SIZE = 65536  # default CoW block size, in bytes
    STATE_DIR = "state"  # subdirectory holding metadata + block files
    METADATA_FILE = "metadata.json"
    JOURNAL_FILE = "journal.bin"  # append-only commit journal

    def __init__(self, base_path: str, block_size: int = BLOCK_SIZE,
                 journal_compact_bytes: int = 64 * 1024 * 1024,
                 dek: Optional[bytes] = None,
                 ad: Optional[bytes] = None):
        """Initialize the NyFS filesystem.

        Args:
            base_path: Path to the backing storage directory
            block_size: Fixed block size in bytes for CoW extents
                (default 64 KiB). A write rewrites only the blocks it
                touches, so this bounds the per-write compress cost.
            journal_compact_bytes: Journal size (bytes) that triggers
                compaction on the next journal-mode save — referenced
                blocks are materialized into ``state/blocks/`` and the
                journal truncated (default 64 MiB; small values in tests
                exercise compaction cheaply).
            dek: The volume's 32-byte data-encryption key (ADR-0023).
                When set, every block is AEAD-encrypted at rest
                (checksum-then-encrypt: the SHA256 covers the
                ciphertext) with a fresh per-block random nonce; the
                ciphertext is what save() persists. ``None`` keeps the
                filesystem plaintext.
            ad: The AEAD associated data (the volume context) — required
                when ``dek`` is set.
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

        if block_size <= 0:
            raise ValueError(f"block_size must be positive, got {block_size}")
        self.block_size = block_size
        self.journal_compact_bytes = journal_compact_bytes
        if dek is not None and len(dek) != 32:
            raise ValueError("dek must be 32 bytes")
        if dek is not None and not ad:
            raise ValueError("ad is required when a dek is set")
        self.dek = dek
        self._ad = ad.encode("utf-8") if isinstance(ad, str) else ad

        self.inode_counter = 1
        self.root_inode = self._create_inode(0, "/", stat.S_IFDIR | 0o755, is_directory=True)
        self.inodes: Dict[int, NyFSInode] = {0: self.root_inode}
        self.snapshots: Dict[str, Dict[int, NyFSInode]] = {}
        self.lock = threading.Lock()
        self._fh_counter = 0
        self._open_files: Dict[int, int] = {}  # fh -> inode number
        # Journal bookkeeping: block_ids whose payloads are already
        # durable in the journal (immutable blocks are never re-appended),
        # and a lazily-built scan cache.
        self._journal_ids: set = set()
        self._journal_index = None
        # Dirty tracking (DAEMON_LIFECYCLE.md §2): True when in-memory
        # state has diverged from the last committed state. Set by every
        # mutating operation, cleared by save() — the shutdown contract
        # uses it to decide whether a final commit is needed.
        self._dirty = False
        # Content-hash dedup cache: checksum hex → NyFSBlock. When two
        # blocks have identical uncompressed content, they share the
        # same compressed payload and checksum — the second write
        # reuses the existing block instead of recompressing.  The
        # cache is rebuilt from on-disk blocks at load() time.
        self._block_dedup: Dict[str, NyFSBlock] = {}

        logger.info(f"Initialized NyFS filesystem at {self.base_path}")

    def _mark_dirty(self) -> None:
        """Record that in-memory state differs from the last save."""
        self._dirty = True

    @property
    def dirty(self) -> bool:
        """True when a save() is needed to commit current state (the
        daemon shutdown contract's final-commit gate)."""
        return self._dirty

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
            self._mark_dirty()
            logger.info(f"Created file {path} (ino={inode.inode_number})")
            return inode

    def create_directory(self, path: str, mode: int = 0o755) -> NyFSInode:
        """Create a new directory at ``path`` and link it into the tree."""
        with self.lock:
            parent, name = self.resolve_parent(path)
            inode = self._new_inode(name, stat.S_IFDIR | mode, is_directory=True)
            self._link(parent, inode)
            self._mark_dirty()
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
            self._mark_dirty()

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
            self._mark_dirty()

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
            self._mark_dirty()

    # ------------------------------------------------------------------
    # Data operations (CoW + checksum + compression)
    # ------------------------------------------------------------------

    def _decompress_verified(self, block: NyFSBlock) -> bytes:
        """Decompress a block, verify its checksum (NPS-004 §4.3), and
        (for an at-rest-encrypted filesystem) AEAD-decrypt it.

        ``NyFSBlock.decompress`` routes through the codec module, whose
        ``decompress_verify`` performs the verification in one step (Rust
        FFI when available) and raises ``ValueError`` on a mismatch.
        """
        try:
            data = block.decompress()
        except ValueError as e:
            logger.error(
                f"Checksum mismatch for block {block.block_id}: {e}"
            )
            raise
        if self.dek is not None:
            # The block DATA is the AEAD envelope (nonce + ciphertext
            # + tag, produced by block_encrypt_any); the nonce rides
            # inside it, so an overwrite at the same index never
            # reuses it and no separate nonce needs persisting.
            from backend.keys import block_decrypt_any
            try:
                data = block_decrypt_any(self.dek, self._ad, data)
            except Exception as e:  # noqa: BLE001 - AEAD failure = tamper
                raise NyFSError(
                    errno.EIO,
                    f"block {block.block_id} failed AEAD verification: {e}")
        return data

    def _content(self, inode: NyFSInode) -> bytes:
        data = b"".join(self._decompress_verified(b) for b in inode.blocks)
        # Blocks are fixed-size and the final block is padded to
        # ``block_size``; the file's logical size is ``inode.size``, so
        # clamp to it rather than exposing trailing padding.
        return data[:inode.size]

    def _make_block(self, data: bytes) -> NyFSBlock:
        """Create a checksummed, compressed CoW block (level 3, ADR-0007).

        For an at-rest-encrypted filesystem the block DATA is the AEAD
        envelope (nonce + ciphertext+tag) — the checksum covers the
        ciphertext (ADR-0023: a tampered block fails both the checksum
        and the AEAD verification).

        Content-hash dedup: if a block with identical uncompressed
        content already exists in the dedup cache, the existing block
        is returned — the second write reuses the compressed payload
        instead of recompressing.  This saves both CPU (compression) and
        disk space (identical blocks share one block file on save)."""
        # Content-hash dedup: check cache before compressing
        # (compute checksum on plaintext for dedup key)
        if self.dek is None:
            # Plaintext path: dedup on the raw content hash.
            # Return a new NyFSBlock that shares the compressed payload
            # (same checksum, same compressed_data) but has its own
            # block_id — each inode owns its own block identity while
            # save() deduplicates identical block files on disk.
            checksum = nyfs_codec.checksum(data)
            cached = self._block_dedup.get(checksum)
            if cached is not None:
                logger.debug(
                    f"Dedup hit for block {checksum[:12]}... "
                    f"({len(data)} bytes reused)")
                dup = NyFSBlock(
                    block_id=str(uuid.uuid4()),
                    data=data,
                    checksum=cached.checksum,
                    compression_level=cached.compression_level,
                )
                dup.compressed_data = cached.compressed_data
                return dup

        block = NyFSBlock(data=data, compression_level=3)
        if self.dek is not None:
            # Encrypted path: block_encrypt_any returns nonce +
            # ciphertext+tag — the filesystem generates a FRESH random
            # nonce per write, so an overwrite at the same index never
            # reuses one.  Dedup is skipped for encrypted blocks because
            # the per-block nonce makes ciphertext unique even for
            # identical plaintext.
            from backend.keys import NONCE_LEN, block_encrypt_any
            block.data = block_encrypt_any(
                self.dek, os.urandom(NONCE_LEN), self._ad, data)
        block.compute_checksum()
        block.compress()
        # Cache for future dedup (plaintext path only)
        if self.dek is None:
            self._block_dedup[block.checksum] = block
        return block

    def _normalize_blocks(self, inode: NyFSInode) -> None:
        """Re-block an inode to uniform ``block_size`` blocks in place.

        The path API (read/write/truncate) assumes every block is exactly
        ``block_size`` bytes so that block ``i`` covers ``[i*bs, (i+1)*bs)``.
        The legacy ``write_block`` appends arbitrary-size blocks, so an
        inode touched by it can violate the invariant; re-blocking from
        the logical content restores it (a no-op for uniform-size files).
        """
        try:
            uniform = all(
                len(self._decompress_verified(b)) == self.block_size
                for b in inode.blocks
            )
        except Exception as e:
            raise ValueError(
                f"Cannot read block data (corrupt or unreadable): {e}"
            ) from e
        if uniform:
            return
        content = self._content(inode)
        inode.blocks = [
            self._make_block(content[i:i + self.block_size])
            for i in range(0, len(content), self.block_size)
        ]

    def _coalesce_blocks(self, blocks: List[NyFSBlock], size: int) -> List[NyFSBlock]:
        """Merge the surviving tail of ``blocks`` into ``size`` bytes of
        CoW blocks, preserving untouched leading blocks by reference.

        Only the last (possibly partial) block is re-written; every block
        fully below ``size`` is carried over untouched — that is the
        per-block CoW guarantee that replaces whole-file recompression.

        Block starts are tracked by cumulative data length (a final
        block may be partial after a previous truncate), never assumed
        to be ``i * block_size``.
        """
        keep, tail, tail_start = [], [], None
        start = 0
        for block in blocks:
            block_len = len(self._decompress_verified(block))
            if start + block_len <= size:
                keep.append(block)
            else:
                tail.append(block)
                if tail_start is None:
                    tail_start = start
            start += block_len
        if not tail:
            return keep
        tail_data = b"".join(self._decompress_verified(b) for b in tail)
        merged = tail_data[:size - tail_start]
        if merged:
            keep.append(self._make_block(merged))
        return keep

    def read(self, inode_or_path, size: int = -1, offset: int = 0) -> bytes:
        """Read ``size`` bytes starting at ``offset`` (size -1 = to EOF).

        Reads are block-aware: only the blocks overlapping the requested
        range are decompressed, not the whole file.
        """
        with self.lock:
            inode = self._as_inode(inode_or_path)
            if inode.is_directory:
                raise NyFSError(errno.EISDIR, "cannot read a directory")
            self._normalize_blocks(inode)
            if offset >= inode.size:
                inode.atime = time.time()
                return b""
            if size is None or size < 0:
                size = inode.size - offset
            size = min(size, inode.size - offset)
            if size <= 0:
                inode.atime = time.time()
                return b""

            first = offset // self.block_size
            last = (offset + size - 1) // self.block_size
            pieces = []
            for i in range(first, last + 1):
                if i >= len(inode.blocks):
                    break
                pieces.append(self._decompress_verified(inode.blocks[i]))
            data = b"".join(pieces)
            rel = offset - first * self.block_size
            data = data[rel:rel + size]
            inode.atime = time.time()
            return data

    def write(self, inode_or_path, data: bytes, offset: int = 0) -> int:
        """Write ``data`` at ``offset`` with per-block copy-on-write.

        CoW (NPS-004 §4.1): existing blocks are never mutated. Only the
        blocks overlapping ``[offset, offset + len(data))`` are replaced
        with new blocks; untouched blocks are carried over by reference,
        so a write's compress cost is bounded by the bytes written rather
        than the file size. Blocks are always ``block_size`` bytes, so a
        block that ends beyond EOF is rebuilt at full size and the file's
        logical size (``inode.size``) is what read/getattr expose — a
        short final write never leaks trailing zero padding. A gap past
        EOF is zero-filled. Old blocks live on in any snapshot taken
        before the write.
        """
        with self.lock:
            inode = self._as_inode(inode_or_path)
            if inode.is_directory:
                raise NyFSError(errno.EISDIR, "cannot write a directory")
            self._normalize_blocks(inode)
            bs = self.block_size
            end = offset + len(data)
            n = max(len(inode.blocks), (end + bs - 1) // bs)
            final_size = max(inode.size, end)

            new_blocks: List[NyFSBlock] = []
            for i in range(n):
                b_start = i * bs
                b_end = b_start + bs
                if b_end <= offset or b_start >= end:
                    # Not touched by this write: carry the block over by
                    # reference, or zero-fill a gap block that lies
                    # between the existing content and a past-EOF write.
                    if i < len(inode.blocks):
                        new_blocks.append(inode.blocks[i])
                    elif final_size > b_start:
                        # Gap block between existing content and a
                        # past-EOF write (or beyond EOF entirely).
                        new_blocks.append(self._make_block(b"\x00" * bs))
                    continue

                old = (self._decompress_verified(inode.blocks[i])
                       if i < len(inode.blocks) else b"")
                merged = bytearray(bs)
                merged[:len(old)] = old[:bs]
                w_start = max(offset, b_start)
                w_end = min(end, b_end)
                merged[w_start - b_start:w_end - b_start] = \
                    data[w_start - offset:w_end - offset]
                new_blocks.append(self._make_block(bytes(merged)))

            inode.blocks = new_blocks  # CoW: swap, never mutate
            inode.size = final_size
            inode.mtime = time.time()
            self._mark_dirty()
            return len(data)

    def truncate(self, inode_or_path, length: int) -> None:
        """Truncate a file to ``length`` bytes (per-block CoW, see write).

        Shortening rewrites only the tail block straddling ``length``;
        leading blocks are carried over untouched. Extending zero-fills
        the gap with new blocks.
        """
        with self.lock:
            inode = self._as_inode(inode_or_path)
            if inode.is_directory:
                raise NyFSError(errno.EISDIR, "cannot truncate a directory")
            if length == inode.size:
                return
            bs = self.block_size
            if length < inode.size:
                inode.blocks = self._coalesce_blocks(inode.blocks, length)
            else:
                # Extension: preserve existing content, zero-fill the gap,
                # and re-block at full ``block_size`` (same convention as
                # write) so reads see real zeroes and no padding leaks.
                content = self._content(inode) + b"\x00" * (length - inode.size)
                inode.blocks = [
                    self._make_block(content[i:i + bs])
                    for i in range(0, length, bs)
                ]
            inode.size = length
            inode.mtime = time.time()
            self._mark_dirty()

    # Legacy block-level API (kept for compatibility)
    def write_block(self, inode_number: int, data: bytes, compress: bool = True) -> NyFSBlock:
        # Legacy compatibility API: appends a block of ``data``'s size
        # (arbitrary, not ``block_size``). Mixing this with the path API
        # is supported via ``_normalize_blocks`` re-blocking, but for new
        # code prefer ``write`` which maintains uniform blocks.
        with self.lock:
            inode = self.inodes.get(inode_number)
            if inode is None:
                raise ValueError(f"Inode {inode_number} not found")
            block = NyFSBlock(data=data, compression_level=3)
            if self.dek is not None:
                from backend.keys import NONCE_LEN, block_encrypt_any
                block.data = block_encrypt_any(
                    self.dek, os.urandom(NONCE_LEN), self._ad, data)
            block.compute_checksum()
            if compress:
                block.compress()
            inode.blocks.append(block)
            inode.size += len(data)
            inode.mtime = time.time()
            self._mark_dirty()
            return block

    def read_block(self, inode_number: int, block_index: int = 0) -> bytes:
        with self.lock:
            inode = self.inodes.get(inode_number)
            if inode is None:
                raise ValueError(f"Inode {inode_number} not found")
            if block_index >= len(inode.blocks):
                raise IndexError(f"Block {block_index} not found in inode {inode_number}")
            return self._decompress_verified(inode.blocks[block_index])

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
            self._mark_dirty()
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
            self._mark_dirty()
        logger.info(f"Restored filesystem to snapshot {snapshot_id}")

    def list_snapshots(self) -> List[str]:
        with self.lock:
            return list(self.snapshots.keys())

    def delete_snapshot(self, snapshot_id: str) -> None:
        """Drop a snapshot (the immutable point-in-time copy). The
        snapshot table entry is removed and the tree is marked dirty,
        so the next save() persists the deletion; CoW blocks referenced
        only by the deleted snapshot are reclaimed by compaction.
        Raises ``ValueError`` when the snapshot does not exist."""
        if snapshot_id not in self.snapshots:
            raise ValueError(f"Snapshot {snapshot_id} not found")
        with self.lock:
            del self.snapshots[snapshot_id]
            self._mark_dirty()
        logger.info(f"Deleted snapshot {snapshot_id}")

    # ------------------------------------------------------------------
    # Snapshot diffing
    # ------------------------------------------------------------------

    def _build_path_map(self, root: NyFSInode) -> Dict[str, NyFSInode]:
        """Map absolute path -> inode for a tree, walking from its root."""
        out: Dict[str, NyFSInode] = {}

        def walk(inode: NyFSInode, path: str) -> None:
            out[path] = inode
            for child in inode.children.values():
                child_path = f"/{child.name}" if path == "/" else f"{path}/{child.name}"
                walk(child, child_path)

        walk(root, "/")
        return out

    def walk(self) -> Dict[str, NyFSInode]:
        """Map absolute path -> inode for the LIVE tree (a public alias
        of the snapshot-diff walker, rooted at ``self.root_inode``).
        Directories are included; callers skip them via
        ``inode.is_directory``. Used by the storage service's quota
        ledger, which re-derives per-container usage from the tree at
        commit time (ADR-0022: the tree is the source of truth, so
        deletes/restores/truncates can never leave the ledger drifting
        from what the tree actually holds)."""
        with self.lock:
            return self._build_path_map(self.root_inode)

    def _snapshot_path_map(self, snap_id: str) -> Dict[str, NyFSInode]:
        snap = self.snapshots.get(snap_id)
        if snap is None:
            raise ValueError(f"Snapshot {snap_id} not found")
        root = snap.get(0)
        if root is None:
            return {}
        return self._build_path_map(root)

    def _live_path_map(self) -> Dict[str, NyFSInode]:
        return self._build_path_map(self.root_inode)

    @staticmethod
    def _file_signature(inode: NyFSInode):
        """Cheap content signature: (size, per-block checksums).

        Blocks are immutable and uniform ``block_size`` through the path
        API, so two files with identical logical content have identical
        checksum lists without decompressing anything. Returns None for
        directories (their changes surface as child entries).
        """
        if inode.is_directory:
            return None
        return (inode.size, tuple(b.checksum for b in inode.blocks))

    def _contents_equal(self, inode_a: NyFSInode, inode_b: NyFSInode) -> bool:
        """True when two files hold identical logical bytes.

        Only invoked in the ambiguous case — equal sizes with differing
        block layouts — so the decompression cost is paid there alone.
        """
        if inode_a.size != inode_b.size:
            return False
        return self._content(inode_a) == self._content(inode_b)

    def _diff_trees(self, tree_a: Dict[str, NyFSInode],
                    tree_b: Dict[str, NyFSInode]) -> List[Dict]:
        """Changes FROM tree_a TO tree_b, one entry per differing path."""
        changes = []
        for path in sorted(set(tree_a) | set(tree_b)):
            in_a = tree_a.get(path)
            in_b = tree_b.get(path)
            if in_a is None:
                changes.append({
                    "path": path,
                    "kind": "directory" if in_b.is_directory else "file",
                    "change": "added",
                    "size_before": None,
                    "size_after": None if in_b.is_directory else in_b.size,
                })
            elif in_b is None:
                changes.append({
                    "path": path,
                    "kind": "directory" if in_a.is_directory else "file",
                    "change": "removed",
                    "size_before": None if in_a.is_directory else in_a.size,
                    "size_after": None,
                })
            else:
                sig_a = self._file_signature(in_a)
                sig_b = self._file_signature(in_b)
                modified = sig_a != sig_b
                if modified and sig_a is not None and sig_b is not None \
                        and sig_a[0] == sig_b[0]:
                    # Equal sizes but different block layouts (partial vs
                    # padded final block after truncate, or legacy
                    # write_block boundaries): verify the actual bytes
                    # before declaring a change.
                    modified = not self._contents_equal(in_a, in_b)
                if modified:
                    changes.append({
                        "path": path,
                        "kind": "directory" if in_a.is_directory else "file",
                        "change": "modified",
                        "size_before": None if in_a.is_directory else in_a.size,
                        "size_after": None if in_b.is_directory else in_b.size,
                    })
        return changes

    def diff_snapshots(self, snap_id_a: str, snap_id_b: str) -> List[Dict]:
        """List the changes from snapshot A to snapshot B.

        Returns one entry per path whose presence or content differs:
        ``{"path", "kind", "change" (added|removed|modified),
        "size_before", "size_after"}``. Directories appear only for
        added/removed; their children's changes are reported as their
        own entries. Content comparison uses per-block checksums (no
        decompression), so identical content is never reported as
        modified even across different writes.
        """
        with self.lock:
            return self._diff_trees(self._snapshot_path_map(snap_id_a),
                                    self._snapshot_path_map(snap_id_b))

    def diff_live(self, snap_id: str) -> List[Dict]:
        """List the changes from a snapshot to the current live state.

        Same shape as ``diff_snapshots``; useful for "what changed since
        I last saved" against a snapshot taken at that point.
        """
        with self.lock:
            return self._diff_trees(self._snapshot_path_map(snap_id),
                                    self._live_path_map())

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

    # ------------------------------------------------------------------
    # Durability (NPS-004 §7): explicit save/load with atomic metadata
    # ------------------------------------------------------------------

    def _state_dir(self) -> Path:
        return self.base_path / self.STATE_DIR

    def _blocks_dir(self) -> Path:
        return self._state_dir() / "blocks"

    def _serialize_inode(self, inode: NyFSInode) -> Dict:
        """Serialize one inode (and, recursively, its children)."""
        return {
            "inode_number": inode.inode_number,
            "name": inode.name,
            "mode": inode.mode,
            "uid": inode.uid,
            "gid": inode.gid,
            "size": inode.size,
            "atime": inode.atime,
            "mtime": inode.mtime,
            "ctime": inode.ctime,
            "is_directory": inode.is_directory,
            "blocks": [
                {
                    "id": b.block_id,
                    "checksum": b.checksum,
                    "compression_level": b.compression_level,
                }
                for b in inode.blocks
            ],
            "children": [
                self._serialize_inode(c)
                for c in inode.children.values()
            ],
        }

    def save(self, batched_fsync: bool = False, use_journal: bool = True,
             compact_threshold: Optional[int] = None) -> None:
        """Persist the current filesystem state atomically.

        Blocks are immutable (CoW), so files already on disk are skipped
        and only new blocks are written (temp + rename, so a
        partially-written block file is never visible), flushed, and
        fsynced before the metadata file is atomically swapped (write
        temp + fsync + rename). Both containing directories are fsynced
        so the commit point itself is durable. If a crash interrupts the
        sequence, the old metadata — which references only old,
        already-present blocks — is still the one on disk, so the
        mountable state is always consistent (NPS-004 §7.1). Orphaned
        block files from superseded versions are left in place (CoW
        history); ``gc_blocks`` can reclaim them.

        ``batched_fsync`` groups the per-block durability work instead
        of interleaving it: every new block temp is written first, then
        all are fsynced, then all are renamed into place. The same
        crash-consistency guarantee holds (nothing is visible until its
        temp is fsynced, and the metadata swap is still the commit
        point), but the disk sees one write phase followed by one
        fsync phase. On single disks the fsync syscall count is
        unchanged, so the gain — if any — comes from kernel write
        coalescing; measured in ``tests/BENCHMARK_RESULTS.md`` §8.

        ``use_journal`` (the default) replaces the per-block fsyncs with
        an append-only journal (``state/journal.bin``): every new block
        payload is appended to the journal and the whole transaction is
        fsynced ONCE, then the metadata swap becomes the commit point.
        The crash-consistency guarantee is unchanged — the journal is
        fsynced before the metadata swap, so new metadata never
        references un-durable entries, and a torn journal tail (crash
        mid-append) is ignored by ``load()``. The journal is compacted
        (materialize referenced blocks into ``state/blocks/``, truncate)
        once it exceeds ``journal_compact_bytes``. Journal commit is the
        decisive commit-cost lever (BENCHMARK_RESULTS.md §9: ~60–70×
        faster than fsync-per-block at ~0.3% on-disk overhead); it
        became the default on 2026-08-12 per implementer decision, with
        Architecture Group review still the formal governance step.
        ``use_journal=False`` restores the per-block interleaved path
        (``batched_fsync=True`` groups the fsyncs; both are kept for
        compatibility and benchmarking).
        """
        with self.lock:
            blocks_dir = self._blocks_dir()

            # 1. Persist every block referenced by the live state (and
            #    snapshots, which share block IDs with the tree).
            live_blocks = {
                b.block_id for b in self._all_blocks(self.inodes)
            }
            for snap in self.snapshots.values():
                live_blocks |= {
                    b.block_id for b in self._all_blocks(snap)
                }
            if use_journal:
                self._journal_append_new(live_blocks, blocks_dir)
            else:
                blocks_dir.mkdir(parents=True, exist_ok=True)
                pending: List[Tuple[Path, Path]] = []
                for block_id in live_blocks:
                    block = self._find_block(block_id)
                    if block is None:
                        continue
                    tmp = blocks_dir / f".{block_id}.tmp"
                    target = blocks_dir / f"{block_id}.bin"
                    if target.exists():
                        # Blocks are immutable (CoW), so a file already
                        # on disk for this ID was written by a previous
                        # save of the exact same content — re-saving is
                        # a no-op.
                        continue
                    with open(tmp, "wb") as fh:
                        fh.write(block.compressed_data or block.data or b"")
                        fh.flush()
                        if batched_fsync:
                            # Defer the fsync + rename to the grouped
                            # phase.
                            pending.append((tmp, target))
                        else:
                            os.fsync(fh.fileno())
                            os.replace(tmp, target)
                if batched_fsync and pending:
                    # Grouped phase: flush every temp to disk, then
                    # publish all renames. Until this completes, the old
                    # metadata references only old, present blocks —
                    # consistent.
                    for tmp, _target in pending:
                        with open(tmp, "rb") as fh:
                            os.fsync(fh.fileno())
                    for tmp, target in pending:
                        os.replace(tmp, target)

            # Fsync the block directory so the new block files are
            # durable before the metadata swap becomes the commit point
            # (journal mode writes no block files, so the directory may
            # not exist).
            if blocks_dir.exists():
                self._fsync_dir(blocks_dir)

            # 2. Serialize the metadata (tree + snapshots).
            metadata = {
                "format": "nyfs-state",
                "version": 1,
                "block_size": self.block_size,
                "inode_counter": self.inode_counter,
                "tree": self._serialize_inode(self.root_inode),
                "snapshots": {
                    snap_id: self._serialize_snapshot(snap)
                    for snap_id, snap in self.snapshots.items()
                },
            }

            # 3. Atomically swap the metadata file.
            state_dir = self._state_dir()
            state_dir.mkdir(parents=True, exist_ok=True)
            tmp_meta = state_dir / f".{self.METADATA_FILE}.tmp"
            with open(tmp_meta, "w") as fh:
                json.dump(metadata, fh, indent=1, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_meta, state_dir / self.METADATA_FILE)
            # Fsync the state directory so the metadata rename itself is
            # durable — the commit point.
            self._fsync_dir(state_dir)
            # Journal-mode compaction: materialize referenced blocks and
            # truncate once the journal exceeds the threshold. Runs only
            # after the commit point, so a failure here leaves the
            # (still valid) journal intact.
            if use_journal:
                journal = state_dir / self.JOURNAL_FILE
                threshold = (compact_threshold
                             if compact_threshold is not None
                             else self.journal_compact_bytes)
                if journal.exists() and journal.stat().st_size > threshold:
                    self._materialize_journal()
            # Committed: in-memory state now matches the on-disk state.
            self._dirty = False
            logger.info(
                f"Saved NyFS state: {len(live_blocks)} blocks, "
                f"{len(self.inodes)} inodes"
            )

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        """Fsync a directory so its entry changes are durable.

        Some filesystems (e.g. certain network mounts) do not support
        directory fsync; such failures are logged and ignored rather than
        failing the save.
        """
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as e:
            logger.warning("directory fsync of %s failed: %s", path, e)

    # ------------------------------------------------------------------
    # Journal commit (NPS-004 §7, append-only log with one fsync per
    # transaction; measured in BENCHMARK_RESULTS.md §9)
    # ------------------------------------------------------------------

    def _journal_path(self) -> Path:
        return self._state_dir() / self.JOURNAL_FILE

    @staticmethod
    def _valid_block_id(block_id: str) -> bool:
        """True for a canonical 36-char UUID hex id (dashes at 8/13/18/23)."""
        if len(block_id) != 36:
            return False
        for i, c in enumerate(block_id):
            if i in (8, 13, 18, 23):
                if c != "-":
                    return False
            elif c not in "0123456789abcdefABCDEF":
                return False
        return True

    def _journal_append_new(self, live_blocks, blocks_dir) -> None:
        """Append new block payloads to the append-only journal with a
        single fsync for the whole transaction.

        Blocks already durable in the journal (``_journal_ids``) or as
        ``.bin`` files are skipped — block payloads are immutable, so
        their journal entry stays valid forever. A crash mid-append can
        leave a torn tail; ``_scan_journal`` stops at the first
        malformed record, and since appends are sequential that is
        exactly the torn tail.
        """
        state_dir = self._state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        if not self._journal_ids:
            # Seed from the on-disk journal so a freshly constructed
            # (non-loaded) instance never re-appends blocks that another
            # instance already journaled. Scanned once; after any save
            # the in-memory set is populated.
            self._journal_ids |= set(self._scan_journal().keys())
        new_records = []
        for block_id in sorted(live_blocks):
            if block_id in self._journal_ids:
                continue
            if (blocks_dir / f"{block_id}.bin").exists():
                self._journal_ids.add(block_id)
                continue
            block = self._find_block(block_id)
            if block is None:
                continue
            payload = block.compressed_data or block.data or b""
            new_records.append((block_id, payload))
        if not new_records:
            return
        with open(state_dir / self.JOURNAL_FILE, "ab") as fh:
            for block_id, payload in new_records:
                fh.write(struct.pack("<I", len(payload)))
                fh.write(block_id.encode("ascii"))
                fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())  # ONE fsync per transaction
        self._journal_ids.update(bid for bid, _ in new_records)
        self._journal_index = None  # invalidate the scan cache

    def _scan_journal(self):
        """Index journal records (block_id -> payload offset/length).

        Robust to a torn tail: scanning stops at the first malformed
        record. Appends are sequential, so everything before the first
        bad record is valid and anything after it is torn garbage.
        """
        if self._journal_index is not None:
            return self._journal_index
        index = {}
        journal = self._journal_path()
        if journal.exists():
            with open(journal, "rb") as fh:
                while True:
                    header = fh.read(4)
                    if len(header) < 4:
                        break
                    (plen,) = struct.unpack("<I", header)
                    if plen > 512 * 1024 * 1024:
                        break  # sanity bound: torn header
                    bid = fh.read(36)
                    if len(bid) < 36 or not self._valid_block_id(
                            bid.decode("ascii", "replace")):
                        break
                    pos = fh.tell()
                    payload = fh.read(plen)
                    if len(payload) < plen:
                        break  # torn payload
                    index[bid.decode("ascii")] = (pos, plen)
        self._journal_index = index
        return index

    def _journal_read(self, block_id: str) -> Optional[bytes]:
        """Read a block payload from the journal, or None if absent."""
        entry = self._scan_journal().get(block_id)
        if entry is None:
            return None
        offset, plen = entry
        with open(self._journal_path(), "rb") as fh:
            fh.seek(offset)
            return fh.read(plen)

    def _materialize_journal(self) -> int:
        """Move referenced blocks out of the journal into ``.bin`` files
        and truncate the journal (compaction).

        Unreferenced records are garbage (blocks orphaned by CoW) and
        are dropped with the truncate. Returns the record count moved.
        """
        index = self._scan_journal()
        if not index:
            return 0
        blocks_dir = self._blocks_dir()
        blocks_dir.mkdir(parents=True, exist_ok=True)
        referenced = {b.block_id for b in self._all_blocks(self.inodes)}
        for snap in self.snapshots.values():
            referenced |= {b.block_id for b in self._all_blocks(snap)}
        pending = []
        moved = 0
        for block_id in referenced:
            if block_id not in index:
                continue
            target = blocks_dir / f"{block_id}.bin"
            if target.exists():
                continue
            payload = self._journal_read(block_id)
            if payload is None:
                continue
            tmp = blocks_dir / f".{block_id}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(payload)
                fh.flush()
            pending.append((tmp, target))
            moved += 1
        for tmp, _t in pending:
            with open(tmp, "rb") as fh:
                os.fsync(fh.fileno())
        for tmp, target in pending:
            os.replace(tmp, target)
        self._fsync_dir(blocks_dir)
        # Everything referenced is now in .bin; the rest is garbage.
        self._journal_path().write_bytes(b"")
        self._journal_ids = set()
        self._journal_index = None
        return moved

    def journal_bytes(self) -> int:
        """Current size of the append-only journal (0 when absent)."""
        journal = self._journal_path()
        if not journal.exists():
            return 0
        try:
            return journal.stat().st_size
        except OSError:
            return 0

    def compact_journal(self) -> int:
        """Force compaction: materialize referenced journal blocks into
        ``state/blocks/`` and truncate the journal.

        Safe to call at any point (also outside ``save()``, e.g. from a
        daemon's idle loop or on unmount): the materialize-then-truncate
        order means a crash mid-compaction leaves the journal intact and
        the state loadable. Returns the number of block records moved
        out of the journal.
        """
        with self.lock:
            return self._materialize_journal()

    def maybe_compact(self, threshold: Optional[int] = None) -> int:
        """Compact the journal only when it exceeds ``threshold``
        (default ``journal_compact_bytes``). Returns the number of block
        records moved, or 0 when the journal is below the threshold or
        empty. This is the hook a long-running daemon calls from a
        periodic/idle timer so that compaction — which can stall a
        transaction for the materialize pass — happens outside the
        ``fsync`` commit path.
        """
        threshold = (self.journal_compact_bytes if threshold is None
                     else threshold)
        if self.journal_bytes() <= threshold:
            return 0
        return self.compact_journal()

    def _all_blocks(self, inodes: Dict[int, NyFSInode]):
        for inode in inodes.values():
            yield from inode.blocks

    def _find_block(self, block_id: str) -> Optional[NyFSBlock]:
        for inode in self.inodes.values():
            for block in inode.blocks:
                if block.block_id == block_id:
                    return block
        for snap in self.snapshots.values():
            for inode in snap.values():
                for block in inode.blocks:
                    if block.block_id == block_id:
                        return block
        return None

    def _serialize_snapshot(self, inodes: Dict[int, NyFSInode]) -> Dict:
        """Serialize a snapshot (a deep-copied inode table) from its root."""
        root = inodes.get(0)
        if root is None:
            return {"tree": None}
        return {"tree": self._serialize_inode(root)}

    @classmethod
    def load(cls, base_path: str) -> "NyFSFilesystem":
        """Load a filesystem previously persisted with ``save()``.

        Raises ``NyFSError`` if no valid metadata exists (never silently
        fabricates an empty filesystem). Block data is read from
        ``state/blocks/`` by ID; checksums are verified lazily on read.
        """
        fs = cls(base_path)
        state_dir = fs._state_dir()
        meta_path = state_dir / cls.METADATA_FILE
        if not meta_path.exists():
            raise NyFSError(
                errno.ENOENT,
                f"no saved NyFS state at {meta_path} (nothing to load)",
            )
        try:
            with open(meta_path) as fh:
                metadata = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            raise NyFSError(errno.EIO, f"corrupt NyFS metadata: {e}") from e

        if metadata.get("format") != "nyfs-state":
            raise NyFSError(errno.EIO, "unrecognized NyFS state format")

        def _load_block(meta: Dict) -> NyFSBlock:
            block_id = meta["id"]
            path = fs._blocks_dir() / f"{block_id}.bin"
            try:
                payload = path.read_bytes()
            except OSError:
                # Fall back to the append-only journal (journal-mode
                # commits never wrote a .bin for this block).
                payload = fs._journal_read(block_id)
                if payload is None:
                    raise NyFSError(
                        errno.EIO, f"missing block file {path.name}")
            block = NyFSBlock(block_id=block_id,
                              checksum=meta.get("checksum", ""),
                              compression_level=meta.get("compression_level", 3))
            block.compressed_data = payload
            return block

        def _deserialize(node: Dict, parent: Optional[NyFSInode]) -> NyFSInode:
            inode = NyFSInode(
                inode_number=node["inode_number"],
                name=node["name"],
                mode=node["mode"],
                uid=node.get("uid", 0),
                gid=node.get("gid", 0),
                size=node.get("size", 0),
                atime=node.get("atime", 0.0),
                mtime=node.get("mtime", 0.0),
                ctime=node.get("ctime", 0.0),
                is_directory=node.get("is_directory", False),
            )
            inode.blocks = [_load_block(b) for b in node.get("blocks", [])]
            inode.parent = parent
            for child_node in node.get("children", []):
                child = _deserialize(child_node, inode)
                inode.children[child.name] = child
            return inode

        fs.block_size = metadata["block_size"]
        fs.inode_counter = metadata["inode_counter"]
        fs.root_inode = _deserialize(metadata["tree"], None)

        # Rebuild the inode table by walking the tree.
        fs.inodes = {}
        stack = [fs.root_inode]
        while stack:
            inode = stack.pop()
            fs.inodes[inode.inode_number] = inode
            stack.extend(inode.children.values())

        # Rebuild snapshots.
        fs.snapshots = {}
        for snap_id, snap_data in metadata.get("snapshots", {}).items():
            if snap_data.get("tree") is None:
                continue
            snap_root = _deserialize(snap_data["tree"], None)
            snap_inodes = {}
            stack = [snap_root]
            while stack:
                inode = stack.pop()
                snap_inodes[inode.inode_number] = inode
                stack.extend(inode.children.values())
            fs.snapshots[snap_id] = snap_inodes

        # Journal entries are durable block payloads; remember which
        # block_ids they cover so a later journal-mode save never
        # re-appends immutable blocks.
        fs._journal_ids = set(fs._scan_journal().keys())

        # Rebuild the content-hash dedup cache from all blocks in the
        # filesystem.  Only plaintext blocks (dek is None) are cached —
        # encrypted blocks use per-block nonces, so identical plaintext
        # yields different ciphertext.
        if fs.dek is None:
            seen: Dict[str, NyFSBlock] = {}
            for inode in fs.inodes.values():
                for blk in inode.blocks:
                    if blk.checksum and blk.checksum not in seen:
                        seen[blk.checksum] = blk
            fs._block_dedup = seen
            logger.debug(
                f"Dedup cache: {len(seen)} unique blocks")

        logger.info(
            f"Loaded NyFS state from {meta_path}: "
            f"{len(fs.inodes)} inodes, {len(fs.snapshots)} snapshots"
        )
        return fs

    def gc_blocks(self) -> int:
        """Delete block files no longer referenced by any inode or
        snapshot. Returns the number of files removed. Orphaned blocks
        are the only files a crash can leave behind, so this is safe to
        run after a successful ``save()``.
        """
        blocks_dir = self._blocks_dir()
        if not blocks_dir.exists():
            return 0
        referenced = {
            b.block_id for b in self._all_blocks(self.inodes)
        }
        for snap in self.snapshots.values():
            referenced |= {b.block_id for b in self._all_blocks(snap)}
        removed = 0
        for path in blocks_dir.glob("*.bin"):
            if path.stem not in referenced:
                path.unlink()
                removed += 1
        # Stale temp files from an interrupted save are never referenced
        # and never become visible; clean them up too.
        for path in blocks_dir.glob(".*.tmp"):
            path.unlink()
            removed += 1
        return removed

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

    def fsync(self, path, datasync, fh=None):
        """FUSE fsync hook: persist the filesystem at a transaction
        boundary (NPS-004 §7). ``save()`` is the durability contract, and
        this is the natural place a mounted daemon commits.
        """
        self.fs.save()
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


class _FuseConnInfo(ctypes.Structure):
    """``struct fuse_conn_info`` from libfuse 2.9's ``fuse_common.h``.

    Used only to negotiate FUSE capabilities during the INIT handshake
    (fusepy passes the connection pointer to ``init`` but never touches
    it). The layout is version-sensitive — verified against the
    ``libfuse.so.2`` this host links; fusepy resolves ``libfuse.so.2``.
    """

    _fields_ = [
        ("proto_major", ctypes.c_uint),
        ("proto_minor", ctypes.c_uint),
        ("async_read", ctypes.c_uint),
        ("max_write", ctypes.c_uint),
        ("max_readahead", ctypes.c_uint),
        ("capable", ctypes.c_uint),
        ("want", ctypes.c_uint),
        ("max_pages", ctypes.c_uint),
        ("max_background", ctypes.c_uint),
        ("congestion_threshold", ctypes.c_uint),
        ("time_gran", ctypes.c_uint),
        ("reserved", ctypes.c_uint * 22),
    ]


# FUSE_CAP_* capability flags, from the kernel UAPI ``linux/fuse.h``.
_FUSE_CAP_BIG_WRITES = 1 << 5        # writes larger than one page
_FUSE_CAP_WRITEBACK_CACHE = 1 << 16  # page-cache writeback batching
_FUSE_CAP_MAX_PAGES = 1 << 22        # honor conn->max_pages


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
        self._compact_stop: Optional[threading.Event] = None
        self._compact_thread: Optional[threading.Thread] = None
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

    def _build_fuse(self, foreground: bool = True, writeback_cache: bool = True,
                    **fuse_kwargs):
        """Construct the fusepy FUSE object for this mount.

        NOTE: fusepy runs the FUSE event loop inside ``FUSE.__init__``
        (there is no ``main()``), so this call blocks until the mount is
        unmounted. ``mount()`` therefore runs it in a daemon thread in
        non-blocking mode.

        ``fuse_kwargs`` are forwarded to fusepy as FUSE mount options
        (e.g. ``max_write=131072``); they are appended to the ``-o``
        string.

        ``writeback_cache`` negotiates FUSE_CAP_BIG_WRITES +
        FUSE_CAP_WRITEBACK_CACHE + FUSE_CAP_MAX_PAGES in the INIT
        handshake so the kernel batches writes into multi-page requests
        instead of 4 KiB ones (see BENCHMARK_RESULTS.md §6). Requests
        measure 128 KiB on this host — the cap is kernel/libfuse
        determined (raising max_write breaks the handshake, so the
        observed cap stands).
        """
        fuse_mod = self._fusepy or _import_fusepy()
        if fuse_mod is None:
            raise NyFSError(errno.ENODEV, "fusepy is not available")

        class _Adapter:
            """Callable operations object for fusepy.

            fusepy both probes handler presence with ``getattr(operations,
            name)`` (so ``__getattr__`` exposes the NyFSOperations methods)
            and dispatches requests by calling ``operations(name, path,
            *args)`` (so ``__call__`` routes them, translating
            ``NyFSError`` -> ``FuseOSError``).
            """

            # NB: do NOT set use_ns here. fusepy's ``use_ns`` flag
            # changes how getattr timestamps are interpreted (divmod by
            # 1e9 — nanoseconds), which breaks the float-seconds
            # convention NyFSFilesystem.getattr returns. Leaving it
            # unset keeps timestamps correct; the one-time DeprecationWarning
            # at mount is harmless.

            def __init__(self, ops: NyFSOperations):
                self._ops = ops

            def __getattr__(self, name):
                return getattr(self._ops, name)

            def __call__(self, op, path, *args):
                try:
                    return getattr(self._ops, op)(path, *args)
                except NyFSError as e:
                    raise fuse_mod.FuseOSError(e.errno)

            def init(self, path):
                # Presence marker so fusepy registers the ``init`` C
                # callback; the actual fuse_conn_info negotiation happens
                # in the FUSE subclass's ``init`` override below.
                return 0

        fuse_cls = getattr(fuse_mod, "FUSE", None) or getattr(
            fuse_mod, "Fuse", None)
        if fuse_cls is None:
            raise NyFSError(errno.ENODEV, "fusepy has no FUSE class")

        class _NyFUSE(fuse_cls):
            """FUSE subclass that negotiates write-batching capabilities
            in the INIT handshake.

            fusepy drops the connection pointer in its stock ``init``,
            which is why default mounts get page-sized (4 KiB) write
            requests: without FUSE_CAP_BIG_WRITES +
            FUSE_CAP_WRITEBACK_CACHE the kernel submits one page per
            write. Overriding ``init`` lets us request multi-page
            writes. Disabled via ``writeback_cache=False``.
            """

            def init(self, conn):
                if not writeback_cache:
                    return 0
                try:
                    info = ctypes.cast(
                        conn, ctypes.POINTER(_FuseConnInfo)).contents
                    # Layout sanity gate: the FUSE protocol major is 7.
                    # Anything else means the ctypes layout does not
                    # match this host's libfuse.so — skip negotiation
                    # rather than silently write at the wrong offsets.
                    if info.proto_major != 7:
                        logger.warning(
                            "FUSE INIT negotiation skipped: unexpected "
                            "proto_major=%s (ctypes layout mismatch?) ",
                            info.proto_major)
                        return 0
                    desired = (_FUSE_CAP_BIG_WRITES
                               | _FUSE_CAP_WRITEBACK_CACHE
                               | _FUSE_CAP_MAX_PAGES)
                    # Only request capabilities the kernel advertises.
                    info.want |= desired & info.capable
                    # Raise the per-request page cap. (Raising max_write
                    # as well breaks the INIT handshake on this libfuse
                    # with EINVAL; the observed request cap is
                    # kernel/libfuse-determined at 128 KiB regardless.)
                    info.max_pages = 256
                    logger.info(
                        "FUSE INIT negotiated: proto %s.%s, "
                        "capable=0x%x, want=0x%x, max_pages=%s",
                        info.proto_major, info.proto_minor,
                        info.capable, info.want, info.max_pages)
                except Exception as e:
                    # Negotiation failure must not prevent mounting;
                    # fall back to kernel defaults (4 KiB writes).
                    logger.warning(
                        "FUSE INIT negotiation failed (%s); "
                        "falling back to kernel write defaults", e)
                return 0

        self._fuse = _NyFUSE(
            _Adapter(self.operations),
            str(self.mount_point),
            foreground=foreground,
            nothreads=False,
            fsname="nyfs",
            **fuse_kwargs,
        )
        return self._fuse

    def _start_compaction_watcher(self, interval: float,
                                  threshold: Optional[int]) -> None:
        """Start a daemon thread that periodically compacts the journal
        outside the fsync commit path (see ``maybe_compact``).

        The watcher uses a lower threshold than save()-time compaction
        (half of ``journal_compact_bytes`` by default) so the journal is
        preemptively trimmed during idle intervals and a transaction is
        rarely the one that crosses the threshold and stalls on the
        materialize pass. All work runs under the filesystem lock, so it
        serializes safely with concurrent saves; failures are logged,
        never fatal.
        """
        if self._compact_thread is not None and self._compact_thread.is_alive():
            logger.debug("compaction watcher already running; not re-starting")
            return
        self._compact_stop = threading.Event()

        def _loop():
            while not self._compact_stop.wait(interval):
                try:
                    self.filesystem.maybe_compact(threshold=threshold)
                except Exception as e:
                    logger.warning("background journal compaction failed: %s", e)

        self._compact_thread = threading.Thread(target=_loop, daemon=True)
        self._compact_thread.start()

    def _stop_compaction_watcher(self) -> None:
        """Signal the background compaction watcher to stop and wait for
        it. Safe to call when no watcher is running (e.g. from unmount
        or a failed-mount path)."""
        if self._compact_stop is None:
            return
        self._compact_stop.set()
        if self._compact_thread is not None:
            self._compact_thread.join(timeout=5.0)
            if self._compact_thread.is_alive():
                # A compaction pass can outlive the join (large journals
                # take seconds, BENCHMARK_RESULTS §14). The thread is a
                # daemon and exits at its next loop iteration once the
                # current pass completes; it never blocks process exit.
                logger.info(
                    "compaction watcher still finishing a pass after "
                    "unmount; it will exit at the next interval")

    def mount(self, foreground: bool = True, blocking: bool = True,
              writeback_cache: bool = True, auto_compact: bool = True,
              compact_interval: float = 60.0,
              compact_interval_bytes: Optional[int] = None,
              handle_signals: bool = True,
              **fuse_kwargs):
        """Mount the filesystem.

        Args:
            foreground: Run in the foreground (default True for a daemon).
                False lets fusepy daemonize (fork into the background).
            blocking: Block until unmounted (True) or run in a thread.
            writeback_cache: Negotiate big-write/writeback-cache/max-pages
                capabilities so the kernel batches writes instead of
                sending 4 KiB requests (default True).
            auto_compact: Run a background journal-compaction watcher
                while mounted (default True, per DAEMON_LIFECYCLE.md).
                The watcher calls ``filesystem.maybe_compact()`` every
                ``compact_interval`` seconds so journal compaction
                happens during idle periods rather than stalling a
                transaction. Without it, a long-running daemon's
                journal grows until the next save() crosses
                ``journal_compact_bytes`` and pays the materialize cost
                inline. Architecture Group tuning review of the
                interval/threshold defaults is still pending
                (ADR-0019 open question 1).
            compact_interval: Seconds between background compaction
                checks (default 60).
            compact_interval_bytes: Journal size (bytes) that triggers
                background compaction; defaults to half of
                ``journal_compact_bytes`` so trimming runs well before
                the save()-time threshold (see
                ``_start_compaction_watcher``).
            handle_signals: In blocking mode, install SIGINT/SIGTERM
                handlers that run the orderly shutdown contract
                (DAEMON_LIFECYCLE.md §2): stop the watcher, commit
                uncommitted state (dirty-flag gate), unmount, then
                exit 0 (default True).
            fuse_kwargs: Extra FUSE mount options forwarded to fusepy
                (e.g. ``max_write=131072``).

        Returns:
            True if the mount was attempted, False if fusepy is unavailable.
        """
        if not self.attach():
            return False
        self._mount_error = None
        if auto_compact:
            # The watcher is a separate daemon thread, so it can run
            # alongside the FUSE loop in both modes (fusepy's
            # ``FUSE.__init__`` blocks in the event loop, so there is no
            # "after the mount is confirmed" moment to hook inside
            # ``_run`` — the start must happen before it). A failed
            # mount stops it again in ``_run``'s failure path.
            threshold = (compact_interval_bytes
                         if compact_interval_bytes is not None
                         else max(1, self.filesystem.journal_compact_bytes // 2))
            self._start_compaction_watcher(compact_interval, threshold)

        def _run():
            logger.info("Mounting NyFS at %s (FUSE)", self.mount_point)
            try:
                self._build_fuse(foreground=foreground,
                                 writeback_cache=writeback_cache,
                                 **fuse_kwargs)
            except Exception as e:
                # Surface background mount failures: ``wait_ready()``
                # re-raises this instead of letting the caller believe a
                # dead mount succeeded.
                logger.error("FUSE mount failed: %s", e)
                self._mount_error = e
                # A failed mount must not leave an orphaned watcher
                # thread holding the filesystem (it stops at its next
                # loop iteration at the latest).
                self._stop_compaction_watcher()

        if blocking:
            if handle_signals:
                self._install_signal_handlers()
            _run()
            if self._mount_error is not None:
                raise self._mount_error
        else:
            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()
        return True

    def _install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers running the orderly shutdown
        contract (DAEMON_LIFECYCLE.md §2). Only valid from the main
        thread; silently skipped otherwise (tests mount non-blocking
        from threads and are unaffected).

        Pragmatism note: the handler runs Python-level work (watcher
        stop, final save, unmount). CPython executes it in the main
        thread between bytecodes, which is reliable in practice for
        this daemon; strict POSIX async-signal-safety (a self-pipe + a
        main-loop check) is recorded as future work in
        DAEMON_LIFECYCLE.md.
        """
        import signal as _signal

        def _on_signal(signum, frame):
            logger.info("received signal %s — orderly shutdown", signum)
            self.shutdown()
            # The FUSE loop may not observe the unmount; a daemon exits
            # here. The final save above is fsynced, so this is a clean
            # commit point.
            os._exit(0)

        try:
            _signal.signal(_signal.SIGINT, _on_signal)
            _signal.signal(_signal.SIGTERM, _on_signal)
        except (ValueError, OSError) as e:
            # ValueError: not the main thread; OSError: unsupported.
            logger.debug("signal handlers not installed: %s", e)

    def shutdown(self) -> None:
        """Orderly shutdown (DAEMON_LIFECYCLE.md §2): stop the
        compaction watcher, commit uncommitted state (gated on the
        filesystem's dirty flag), then unmount. Best-effort — every
        step is guarded and logged, never raised — so it is safe to
        call from a signal handler.
        """
        try:
            self._stop_compaction_watcher()
        except Exception as e:
            logger.warning("shutdown: watcher stop failed: %s", e)
        try:
            if self.filesystem.dirty:
                logger.info("shutdown: committing uncommitted state")
                self.filesystem.save()
        except Exception as e:
            logger.warning("shutdown: final save failed: %s", e)
        try:
            self.unmount()
        except Exception as e:
            logger.warning("shutdown: unmount failed: %s", e)

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """Wait until the background mount is live (or has failed).

        Returns True once the kernel mount is visible. Raises the
        FUSE-constructor error if the background mount failed. Use after
        ``mount(blocking=False)`` before issuing I/O to avoid racing the
        mount thread.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._mount_error is not None:
                raise self._mount_error
            if os.path.ismount(self.mount_point):
                return True
            time.sleep(0.05)
        return False

    def unmount(self) -> None:
        """Best-effort unmount via fusepy or ``fusermount -u``."""
        # Stop the background compaction watcher first so it cannot race
        # the teardown (Event.wait returns immediately once set).
        self._stop_compaction_watcher()
        if self._mount_error is not None:
            # The background mount never came up; nothing to unmount.
            return
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
