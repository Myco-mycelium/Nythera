"""shm_buffer — Shared memory buffer management for Wayland surfaces.

Provides real SHM buffer sharing between Wayland clients and the compositor:

1. Create anonymous shared memory regions via memfd_create
2. Map shared memory for CPU access
3. Create Wayland SHM pools from shared memory
4. Create Wayland buffers from SHM pools
5. Read/write pixel data for surface compositing

References:
    - Wayland SHM protocol: https://wayland.freedesktop.org/docs/html/
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import mmap
import os
import struct
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# libc constants
MADV_SEQUENTIAL = 2
MADV_DONTNEED = 4


# SHM format constants (from wayland-client-protocol.h)
WL_SHM_FORMAT_ARGB8888 = 0
WL_SHM_FORMAT_XRGB8888 = 1
WL_SHM_FORMAT_RGBA8888 = 0x34324152  # DRM_FORMAT_RGBA8888
WL_SHM_FORMAT_RGB565 = 0x35314752  # DRM_FORMAT_RGB565


@dataclass
class ShmRegion:
    """A shared memory region."""
    fd: int
    size: int
    mmap_obj: Optional[mmap.mmap] = None
    pointer: Optional[int] = None  # ctypes pointer
    active: bool = True


@dataclass
class ShmPool:
    """A Wayland SHM pool."""
    id: int
    region: ShmRegion
    fd: int
    size: int
    buffers: List[int] = field(default_factory=list)
    active: bool = True


@dataclass
class ShmBuffer:
    """A Wayland SHM buffer."""
    id: int
    pool_id: int
    offset: int
    width: int
    height: int
    stride: int
    format: int
    data: Optional[bytes] = None
    active: bool = True


class ShmManager:
    """Shared memory buffer manager for Wayland surfaces.
    
    Usage:
        shm = ShmManager()
        region = shm.create_region(1920 * 1080 * 4)
        pool = shm.create_pool(region)
        buffer = shm.create_buffer(pool, 0, 1920, 1080, 1920 * 4, WL_SHM_FORMAT_ARGB8888)
        
        # Write pixel data
        shm.write_buffer(buffer, pixel_data)
        
        # Read pixel data
        data = shm.read_buffer(buffer)
        
        # Cleanup
        shm.destroy_buffer(buffer)
        shm.destroy_pool(pool)
        shm.destroy_region(region)
    """
    
    def __init__(self):
        self._regions: Dict[int, ShmRegion] = {}
        self._pools: Dict[int, ShmPool] = {}
        self._buffers: Dict[int, ShmBuffer] = {}
        self._next_region_id = 0
        self._next_pool_id = 0
        self._next_buffer_id = 0
        
        # Load libc for memfd_create
        self._libc = None
        self._memfd_create = None
        self._load_libc()
    
    def _load_libc(self):
        """Load libc for memfd_create."""
        try:
            libc_path = ctypes.util.find_library("c")
            if libc_path:
                self._libc = ctypes.CDLL(libc_path)
                # memfd_create(const char *name, unsigned int flags)
                self._memfd_create = self._libc.memfd_create
                self._memfd_create.restype = ctypes.c_int
                self._memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
                logger.debug("Loaded libc for memfd_create")
        except Exception as exc:
            logger.warning("Failed to load libc: %s", exc)
    
    def create_region(self, size: int) -> Optional[ShmRegion]:
        """Create a shared memory region.
        
        Parameters
        ----------
        size : int
            Size in bytes.
            
        Returns
        -------
        ShmRegion or None
            The created region, or None on failure.
        """
        if size <= 0:
            logger.error("Invalid region size: %d", size)
            return None
        
        # Try memfd_create first
        fd = -1
        if self._memfd_create:
            try:
                fd = self._memfd_create(b"nyrqis-shm", 0)
                if fd >= 0:
                    os.ftruncate(fd, size)  # memfd starts at size 0
            except Exception:
                fd = -1
        
        # Fall back to tmpfile
        if fd < 0:
            try:
                tmp = tempfile.NamedTemporaryFile(delete=False)
                tmp.write(b"\x00" * size)
                tmp.flush()
                fd = os.open(tmp.name, os.O_RDWR | os.O_CREAT)
                # Ensure file is large enough for mmap
                os.ftruncate(fd, size)
                os.close(tmp.fileno())
                os.unlink(tmp.name)
            except OSError as exc:
                logger.error("Failed to create shared memory: %s", exc)
                return None
        
        # Map the region
        try:
            mm = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        except OSError as exc:
            logger.error("Failed to map shared memory: %s", exc)
            os.close(fd)
            return None
        
        region_id = self._next_region_id
        self._next_region_id += 1
        
        region = ShmRegion(
            fd=fd,
            size=size,
            mmap_obj=mm,
            pointer=ctypes.addressof(ctypes.c_char.from_buffer(mm)),
            active=True,
        )
        
        self._regions[region_id] = region
        logger.debug("Created SHM region: id=%d, size=%d", region_id, size)
        return region
    
    def create_pool(self, region: ShmRegion) -> Optional[ShmPool]:
        """Create a Wayland SHM pool from a region.
        
        Parameters
        ----------
        region : ShmRegion
            The shared memory region.
            
        Returns
        -------
        ShmPool or None
            The created pool, or None on failure.
        """
        if not region or not region.active:
            logger.error("Invalid region")
            return None
        
        pool_id = self._next_pool_id
        self._next_pool_id += 1
        
        pool = ShmPool(
            id=pool_id,
            region=region,
            fd=region.fd,
            size=region.size,
            active=True,
        )
        
        self._pools[pool_id] = pool
        logger.debug("Created SHM pool: id=%d, size=%d", pool_id, region.size)
        return pool
    
    def create_buffer(
        self,
        pool: ShmPool,
        offset: int,
        width: int,
        height: int,
        stride: int,
        format: int = WL_SHM_FORMAT_ARGB8888,
    ) -> Optional[ShmBuffer]:
        """Create a Wayland SHM buffer.
        
        Parameters
        ----------
        pool : ShmPool
            The SHM pool.
        offset : int
            Offset within the pool.
        width : int
            Buffer width in pixels.
        height : int
            Buffer height in pixels.
        stride : int
            Row stride in bytes.
        format : int
            Pixel format (default: ARGB8888).
            
        Returns
        -------
        ShmBuffer or None
            The created buffer, or None on failure.
        """
        if not pool or not pool.active:
            logger.error("Invalid pool")
            return None
        
        # Check bounds
        end = offset + stride * height
        if end > pool.size:
            logger.error("Buffer exceeds pool size: %d + %d > %d", offset, stride * height, pool.size)
            return None
        
        buffer_id = self._next_buffer_id
        self._next_buffer_id += 1
        
        buffer = ShmBuffer(
            id=buffer_id,
            pool_id=pool.id,
            offset=offset,
            width=width,
            height=height,
            stride=stride,
            format=format,
            active=True,
        )
        
        self._buffers[buffer_id] = buffer
        pool.buffers.append(buffer_id)
        
        logger.debug("Created SHM buffer: id=%d, %dx%d, stride=%d, format=0x%x",
                    buffer_id, width, height, stride, format)
        return buffer
    
    def write_buffer(self, buffer: ShmBuffer, data: bytes) -> bool:
        """Write pixel data to a buffer.
        
        Parameters
        ----------
        buffer : ShmBuffer
            The buffer to write to.
        data : bytes
            Pixel data to write.
            
        Returns
        -------
        bool
            True on success, False on failure.
        """
        if not buffer or not buffer.active:
            return False
        
        pool = self._pools.get(buffer.pool_id)
        if not pool or not pool.active:
            return False
        
        region = pool.region
        if not region or not region.mmap_obj:
            return False
        
        # Write data to the mapped memory
        try:
            region.mmap_obj.seek(buffer.offset)
            region.mmap_obj.write(data[:buffer.stride * buffer.height])
            return True
        except OSError as exc:
            logger.error("Failed to write buffer: %s", exc)
            return False
    
    def read_buffer(self, buffer: ShmBuffer) -> Optional[bytes]:
        """Read pixel data from a buffer.
        
        Parameters
        ----------
        buffer : ShmBuffer
            The buffer to read from.
            
        Returns
        -------
        bytes or None
            The pixel data, or None on failure.
        """
        if not buffer or not buffer.active:
            return None
        
        pool = self._pools.get(buffer.pool_id)
        if not pool or not pool.active:
            return None
        
        region = pool.region
        if not region or not region.mmap_obj:
            return None
        
        # Read data from the mapped memory
        try:
            region.mmap_obj.seek(buffer.offset)
            data = region.mmap_obj.read(buffer.stride * buffer.height)
            return data
        except OSError as exc:
            logger.error("Failed to read buffer: %s", exc)
            return None
    
    def get_buffer_pixels(self, buffer: ShmBuffer) -> Optional[List[Tuple[int, int, int, int]]]:
        """Get pixel data as a list of (R, G, B, A) tuples.
        
        Parameters
        ----------
        buffer : ShmBuffer
            The buffer to read from.
            
        Returns
        -------
        list of (R, G, B, A) or None
            The pixel data, or None on failure.
        """
        data = self.read_buffer(buffer)
        if data is None:
            return None
        
        pixels = []
        for y in range(buffer.height):
            row_offset = y * buffer.stride
            for x in range(buffer.width):
                pixel_offset = row_offset + x * 4
                if pixel_offset + 4 <= len(data):
                    # ARGB8888 format
                    a = data[pixel_offset + 3]
                    r = data[pixel_offset + 2]
                    g = data[pixel_offset + 1]
                    b = data[pixel_offset + 0]
                    pixels.append((r, g, b, a))
        
        return pixels
    
    def fill_buffer(
        self,
        buffer: ShmBuffer,
        r: int,
        g: int,
        b: int,
        a: int = 255,
    ) -> bool:
        """Fill a buffer with a solid color.
        
        Parameters
        ----------
        buffer : ShmBuffer
            The buffer to fill.
        r, g, b, a : int
            Color components (0-255).
            
        Returns
        -------
        bool
            True on success, False on failure.
        """
        # Create pixel data (ARGB8888)
        pixel = struct.pack("BBBB", b, g, r, a)
        row = pixel * buffer.width
        data = row * buffer.height
        
        return self.write_buffer(buffer, data)
    
    def destroy_buffer(self, buffer: ShmBuffer):
        """Destroy a buffer."""
        if not buffer:
            return
        
        buffer.active = False
        
        pool = self._pools.get(buffer.pool_id)
        if pool and buffer.id in pool.buffers:
            pool.buffers.remove(buffer.id)
        
        if buffer.id in self._buffers:
            del self._buffers[buffer.id]
        
        logger.debug("Destroyed SHM buffer: id=%d", buffer.id)
    
    def destroy_pool(self, pool: ShmPool):
        """Destroy a pool."""
        if not pool:
            return
        
        pool.active = False
        
        if pool.id in self._pools:
            del self._pools[pool.id]
        
        logger.debug("Destroyed SHM pool: id=%d", pool.id)
    
    def destroy_region(self, region: ShmRegion):
        """Destroy a region."""
        if not region:
            return
        
        region.active = False
        
        if region.mmap_obj:
            try:
                region.mmap_obj.close()
            except OSError:
                pass
        
        if region.fd >= 0:
            try:
                os.close(region.fd)
            except OSError:
                pass
        
        logger.debug("Destroyed SHM region: fd=%d", region.fd)
    
    def cleanup(self):
        """Clean up all resources."""
        for buffer in list(self._buffers.values()):
            self.destroy_buffer(buffer)
        for pool in list(self._pools.values()):
            self.destroy_pool(pool)
        for region in list(self._regions.values()):
            self.destroy_region(region)
    
    def get_stats(self) -> dict:
        """Get statistics about shared memory usage."""
        return {
            "regions": len(self._regions),
            "pools": len(self._pools),
            "buffers": len(self._buffers),
            "total_size": sum(r.size for r in self._regions.values() if r.active),
        }
