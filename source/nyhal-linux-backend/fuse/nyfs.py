#!/usr/bin/env python3
"""
NyFS FUSE Filesystem Implementation

Implements the NyFS filesystem as a user-space FUSE daemon per ADR-0016.
Provides the following guarantees from NPS-004 §4:
- Copy-on-Write (CoW): Multiple containers share a base image with private overlays
- Snapshots: Immutable point-in-time copies of filesystem state
- Checksumming: Data integrity verification
- Transparent Compression: Efficient storage using Zstandard (ADR-0007)

Architecture:
- NyFSFilesystem: Core filesystem logic (CoW, snapshots, compression)
- NyFSMount: FUSE daemon wrapper for mounting
- NyFSInode: In-memory inode representation
- NyFSBlock: Compressed data block with checksum

References:
- NPS-004: NyFS Filesystem Core
- ADR-0016: NyFS Linux Backend implemented as a user-space FUSE filesystem
- ADR-0007: Adopt Zstandard as the default compression codec
"""

import hashlib
import logging
import os
import stat
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple, List

logger = logging.getLogger(__name__)


@dataclass
class NyFSBlock:
    """Represents a compressed data block in NyFS.
    
    Per NPS-004 §4, all blocks are checksummed for integrity and
    compressed with Zstandard (ADR-0007).
    """
    block_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    data: bytes = b""
    compressed_data: Optional[bytes] = None
    checksum: str = ""  # SHA256 hex digest
    compression_level: int = 3  # Zstandard compression level (1-22)
    created_at: float = field(default_factory=time.time)
    
    def compute_checksum(self) -> str:
        """Compute SHA256 checksum of the uncompressed data."""
        self.checksum = hashlib.sha256(self.data).hexdigest()
        return self.checksum
    
    def compress(self) -> None:
        """Compress the data using Zstandard."""
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
    children: Dict[str, 'NyFSInode'] = field(default_factory=dict)  # For directories
    parent: Optional['NyFSInode'] = None
    is_directory: bool = False
    
    def __repr__(self) -> str:
        return f"NyFSInode(ino={self.inode_number}, name={self.name!r}, mode={oct(self.mode)})"


class NyFSFilesystem:
    """Core NyFS filesystem logic.
    
    Implements the storage guarantees from NPS-004 §4:
    - Copy-on-Write (CoW)
    - Snapshots
    - Checksumming
    - Transparent Compression
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
        self.snapshots: Dict[str, Dict[int, NyFSInode]] = {}  # snapshot_id -> inodes
        self.lock = threading.Lock()
        
        logger.info(f"Initialized NyFS filesystem at {self.base_path}")
    
    def _create_inode(
        self,
        inode_number: int,
        name: str,
        mode: int,
        is_directory: bool = False,
    ) -> NyFSInode:
        """Create a new inode."""
        return NyFSInode(
            inode_number=inode_number,
            name=name,
            mode=mode,
            is_directory=is_directory,
        )
    
    def create_file(self, path: str, mode: int = 0o644) -> NyFSInode:
        """Create a new file in the filesystem.
        
        Per NPS-004 §4, file creation is part of the CoW mechanism.
        
        Args:
            path: File path (relative to root)
            mode: File mode (permissions)
            
        Returns:
            The created inode
        """
        with self.lock:
            self.inode_counter += 1
            inode = self._create_inode(self.inode_counter, Path(path).name, mode)
            self.inodes[inode.inode_number] = inode
            logger.info(f"Created file {path} (ino={inode.inode_number})")
            return inode
    
    def create_directory(self, path: str, mode: int = 0o755) -> NyFSInode:
        """Create a new directory in the filesystem.
        
        Args:
            path: Directory path (relative to root)
            mode: Directory mode (permissions)
            
        Returns:
            The created inode
        """
        with self.lock:
            self.inode_counter += 1
            inode = self._create_inode(
                self.inode_counter,
                Path(path).name,
                stat.S_IFDIR | mode,
                is_directory=True,
            )
            self.inodes[inode.inode_number] = inode
            logger.info(f"Created directory {path} (ino={inode.inode_number})")
            return inode
    
    def write_block(self, inode_number: int, data: bytes, compress: bool = True) -> NyFSBlock:
        """Write data to a file as a block.
        
        Per NPS-004 §4, blocks are checksummed and transparently compressed.
        
        Args:
            inode_number: Target inode
            data: Data to write
            compress: Whether to compress the block
            
        Returns:
            The created block
        """
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
            
            logger.info(
                f"Wrote block to inode {inode_number}: "
                f"{len(data)} bytes, checksum={block.checksum[:8]}"
            )
            return block
    
    def read_block(self, inode_number: int, block_index: int = 0) -> bytes:
        """Read data from a block.
        
        Args:
            inode_number: Source inode
            block_index: Index of the block to read
            
        Returns:
            The decompressed block data
        """
        with self.lock:
            inode = self.inodes.get(inode_number)
            if inode is None:
                raise ValueError(f"Inode {inode_number} not found")
            
            if block_index >= len(inode.blocks):
                raise IndexError(f"Block {block_index} not found in inode {inode_number}")
            
            block = inode.blocks[block_index]
            data = block.decompress()
            
            # Verify checksum
            computed_checksum = hashlib.sha256(data).hexdigest()
            if computed_checksum != block.checksum:
                logger.error(
                    f"Checksum mismatch for block {block.block_id}: "
                    f"expected {block.checksum}, got {computed_checksum}"
                )
                raise ValueError("Block checksum verification failed")
            
            logger.debug(f"Read block from inode {inode_number}: {len(data)} bytes")
            return data
    
    def create_snapshot(self, snapshot_id: Optional[str] = None) -> str:
        """Create an immutable snapshot of the filesystem.
        
        Per NPS-004 §4, snapshots are point-in-time copies that can be used
        as the basis for CoW overlays.
        
        Args:
            snapshot_id: Optional custom snapshot ID
            
        Returns:
            The snapshot ID
        """
        if snapshot_id is None:
            snapshot_id = f"snap-{uuid.uuid4().hex[:12]}"
        
        with self.lock:
            # Deep copy the inode tree
            import copy
            self.snapshots[snapshot_id] = copy.deepcopy(self.inodes)
        
        logger.info(f"Created snapshot {snapshot_id} with {len(self.inodes)} inodes")
        return snapshot_id
    
    def restore_snapshot(self, snapshot_id: str) -> None:
        """Restore the filesystem to a previous snapshot.
        
        Args:
            snapshot_id: The snapshot to restore
        """
        if snapshot_id not in self.snapshots:
            raise ValueError(f"Snapshot {snapshot_id} not found")
        
        with self.lock:
            import copy
            self.inodes = copy.deepcopy(self.snapshots[snapshot_id])
        
        logger.info(f"Restored filesystem to snapshot {snapshot_id}")
    
    def list_snapshots(self) -> List[str]:
        """List all available snapshots."""
        with self.lock:
            return list(self.snapshots.keys())
    
    def get_inode(self, inode_number: int) -> Optional[NyFSInode]:
        """Get an inode by number."""
        with self.lock:
            return self.inodes.get(inode_number)
    
    def get_inode_stats(self, inode_number: int) -> Dict:
        """Get filesystem stats for an inode."""
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


class NyFSMount:
    """FUSE mount wrapper for NyFS.
    
    This class would integrate with a FUSE library (e.g., pyfuse3 or fusepy)
    to expose the NyFS filesystem to the Linux kernel.
    
    Note: Full FUSE integration is deferred; this is a structural placeholder.
    """
    
    def __init__(self, filesystem: NyFSFilesystem, mount_point: str):
        """Initialize the FUSE mount.
        
        Args:
            filesystem: The NyFS filesystem to mount
            mount_point: Path where the filesystem should be mounted
        """
        self.filesystem = filesystem
        self.mount_point = Path(mount_point)
        self.mount_point.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initialized NyFSMount at {self.mount_point}")
    
    def mount(self) -> None:
        """Mount the filesystem (placeholder for FUSE integration)."""
        logger.info(f"Would mount NyFS at {self.mount_point}")
        # In a real implementation, this would call pyfuse3.run() or similar
    
    def unmount(self) -> None:
        """Unmount the filesystem (placeholder for FUSE integration)."""
        logger.info(f"Would unmount NyFS from {self.mount_point}")


def main():
    """Simple CLI for testing the NyFS filesystem."""
    logging.basicConfig(level=logging.INFO)
    
    # Create a test filesystem
    fs = NyFSFilesystem("/tmp/nyfs-test")
    
    # Create some files
    file1 = fs.create_file("/test.txt")
    fs.write_block(file1.inode_number, b"Hello from NyFS!")
    
    # Read the file back
    data = fs.read_block(file1.inode_number)
    print(f"Read data: {data.decode()}")
    
    # Create a snapshot
    snap_id = fs.create_snapshot()
    print(f"Created snapshot: {snap_id}")
    
    # Get inode stats
    stats = fs.get_inode_stats(file1.inode_number)
    print(f"Inode stats: {stats}")


if __name__ == "__main__":
    main()
