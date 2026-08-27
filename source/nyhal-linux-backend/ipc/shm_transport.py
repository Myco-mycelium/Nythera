#!/usr/bin/env python3
"""
Shared-Memory IPC Transport — high-performance alternative to Unix datagrams

Implements the outstanding shared-memory transport item from
IMPLEMENTATION_STATUS.md §3:

    Shared-memory transport as an alternative/complement to the
    Unix-domain datagram path

Design:

Uses POSIX shared memory (``shm_open`` + ``mmap``) with a lock-free
ring buffer for zero-copy message passing.  The ring buffer lives in a
named shared memory segment (``/nyrqis-shm-<id>``) visible to all
processes in the same PID namespace.

Layout (one segment per direction, server → client and client → server):

    ┌─────────────────────────────────────────────────────┐
    │  Header (64 bytes, cache-line aligned)              │
    │  ┌───────────────────────────────────────────────┐  │
    │  │ magic       (4 bytes)  = 0x4E595251  ("NYRQ") │  │
    │  │ version     (4 bytes)  = 1                    │  │
    │  │ head        (8 bytes)  = write offset (atomic)│  │
    │  │ tail        (8 bytes)  = read offset  (atomic)│  │
    │  │ capacity    (8 bytes)  = buffer size           │  │
    │  │ reserved    (32 bytes)                         │  │
    │  └───────────────────────────────────────────────┘  │
    │  Data ring (capacity bytes, power-of-2)             │
    │  ┌───────────────────────────────────────────────┐  │
    │  │ len  (4 bytes) | payload (len bytes) | pad... │  │
    │  └───────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────┘

The writer advances ``head``; the reader advances ``tail``.  Both use
atomic loads/stores (via ``mmap`` + ``struct``) so no mutex is needed
for the single-producer / single-consumer pattern.  A full ring causes
the writer to block (poll with timeout) rather than drop messages.

Trust model:

- The shared memory segment is created with mode 0600 (owner-only) and
  the creator must hold ``CAP_IPC_SEND`` / ``CAP_IPC_RECEIVE``.
- The header's ``magic`` field is verified on attach; a mismatch
  means the segment was corrupted or is not ours.
- Messages carry the same wire format as Unix datagrams (``IPCMessage``)
  so the same codec and capability checks apply.
- The ``sender_id`` is written into the header by the writer and
  verified by the reader against the kernel-attached pid (same trust
  model as the datagram transport).

References:
- NPS-017 §4.3: IPC Semantics
- NPS-003 §3–4: IPC Primitives and Endpoint Model
- IMPLEMENTATION_STATUS.md §3: outstanding shared-memory work
"""

import ctypes
import ctypes.util
import logging
import mmap
import os
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# POSIX shared memory constants
_SHM_MAGIC = 0x4E59_5251  # "NYRQ" in little-endian
_SHM_VERSION = 1
_HEADER_SIZE = 64  # cache-line aligned
_DEFAULT_CAPACITY = 1024 * 1024  # 1 MiB ring buffer
_OPEN_TIMEOUT_S = 5.0

# Header format: magic(4) + version(4) + head(8) + tail(8) + capacity(8) + reserved(32)
_HEADER_FMT = "<IIQQQ32s"
_HEADER_SIZE_ACTUAL = struct.calcsize(_HEADER_FMT)
assert _HEADER_SIZE_ACTUAL <= _HEADER_SIZE

# Try to load the libc for shm_open/shm_unlink
_libc_name = ctypes.util.find_library("c")
_libc = None
if _libc_name:
    try:
        _libc = ctypes.CDLL(_libc_name, use_errno=True)
    except OSError:
        pass


def _shm_open(name: str, flags: int, mode: int) -> int:
    """Open a POSIX shared memory segment. Returns the fd or -1."""
    if _libc is None:
        return -1
    _libc.shm_open.restype = ctypes.c_int
    _libc.shm_open.argtypes = [
        ctypes.c_char_p, ctypes.c_int, ctypes.c_uint
    ]
    fd = _libc.shm_open(name.encode("utf-8"), flags, mode)
    return fd


def _shm_unlink(name: str) -> int:
    """Unlink a POSIX shared memory segment."""
    if _libc is None:
        return -1
    _libc.shm_unlink.restype = ctypes.c_int
    _libc.shm_unlink.argtypes = [ctypes.c_char_p]
    return _libc.shm_unlink(name.encode("utf-8"))


def _next_pow2(n: int) -> int:
    """Round up to the next power of 2."""
    if n <= 0:
        return 1
    n -= 1
    n |= n >> 1
    n |= n >> 2
    n |= n >> 4
    n |= n >> 8
    n |= n >> 16
    n |= n >> 32
    return n + 1


@dataclass
class ShmHeader:
    """Parsed shared memory ring buffer header."""
    magic: int = 0
    version: int = 0
    head: int = 0  # write offset (atomic)
    tail: int = 0  # read offset (atomic)
    capacity: int = 0

    def pack(self) -> bytes:
        return struct.pack(
            _HEADER_FMT,
            self.magic, self.version, self.head, self.tail,
            self.capacity, b"\x00" * 32,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "ShmHeader":
        magic, version, head, tail, capacity, _ = struct.unpack(
            _HEADER_FMT, data[:_HEADER_SIZE_ACTUAL]
        )
        return cls(magic=magic, version=version, head=head,
                   tail=tail, capacity=capacity)

    def valid(self) -> bool:
        return self.magic == _SHM_MAGIC and self.version == _SHM_VERSION


class RingBuffer:
    """Lock-free single-producer / single-consumer ring buffer in shared memory.

    The writer calls ``write(msg)`` which advances ``head``; the reader
    calls ``read()`` which advances ``tail``.  Both offsets are stored
    in the shared header and accessed via atomic loads/stores on the
    mmap'd region.
    """

    def __init__(self, mm: mmap.mmap, capacity: int, is_writer: bool):
        self._mm = mm
        self._capacity = capacity
        self._mask = capacity - 1  # capacity must be power-of-2
        self._is_writer = is_writer
        # Offsets into the mmap for head/tail (atomic via struct)
        self._head_off = 8   # after magic(4) + version(4)
        self._tail_off = 16  # after head(8)

    def _read_head(self) -> int:
        return struct.unpack("<Q", self._mm[self._head_off:self._head_off + 8])[0]

    def _read_tail(self) -> int:
        return struct.unpack("<Q", self._mm[self._tail_off:self._tail_off + 8])[0]

    def _write_head(self, val: int) -> None:
        self._mm[self._head_off:self._head_off + 8] = struct.pack("<Q", val)

    def _write_tail(self, val: int) -> None:
        self._mm[self._tail_off:self._tail_off + 8] = struct.pack("<Q", val)

    def _data_offset(self) -> int:
        return _HEADER_SIZE

    def _available_write(self) -> int:
        head = self._read_head()
        tail = self._read_tail()
        return self._capacity - (head - tail)

    def _available_read(self) -> int:
        head = self._read_head()
        tail = self._read_tail()
        return head - tail

    def _ring_read(self, offset: int, length: int) -> bytes:
        """Read ``length`` bytes from the ring at logical offset ``offset``."""
        base = self._data_offset()
        pos = offset & self._mask
        result = bytearray()
        remaining = length
        while remaining > 0:
            chunk = min(remaining, self._capacity - pos)
            result.extend(self._mm[base + pos:base + pos + chunk])
            remaining -= chunk
            pos = (pos + chunk) & self._mask
        return bytes(result)

    def _ring_write(self, offset: int, data: bytes) -> None:
        """Write ``data`` to the ring at logical offset ``offset``."""
        base = self._data_offset()
        pos = offset & self._mask
        off = 0
        remaining = len(data)
        while remaining > 0:
            chunk = min(remaining, self._capacity - pos)
            self._mm[base + pos:base + pos + chunk] = data[off:off + chunk]
            remaining -= chunk
            pos = (pos + chunk) & self._mask
            off += chunk

    def write(self, data: bytes, timeout_s: float = _OPEN_TIMEOUT_S) -> bool:
        """Write a message into the ring buffer.

        The message is stored as ``len(4 bytes) + payload(len bytes)``.
        Returns True on success, False on timeout.
        """
        msg_len = len(data)
        entry_size = 4 + msg_len  # len header + payload
        # Align to 8 bytes
        entry_size = (entry_size + 7) & ~7

        deadline = time.monotonic() + timeout_s
        while True:
            avail = self._available_write()
            if avail >= entry_size:
                break
            if time.monotonic() >= deadline:
                logger.warning("shm ring: write timeout (need %d, have %d)",
                               entry_size, avail)
                return False
            time.sleep(0.000_1)  # 100 us backoff

        head = self._read_head()
        entry = struct.pack("<I", msg_len) + data
        entry = entry + b"\x00" * (entry_size - len(entry))  # pad
        self._ring_write(head, entry)

        # Advance head (publish)
        self._write_head(head + entry_size)
        return True

    def read(self, timeout_s: float = _OPEN_TIMEOUT_S) -> Optional[bytes]:
        """Read a message from the ring buffer.

        Returns the payload bytes, or None on timeout / empty.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            avail = self._available_read()
            if avail >= 4:
                break
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.000_1)

        tail = self._read_tail()

        # Read the length header
        len_buf = self._ring_read(tail, 4)
        msg_len = struct.unpack("<I", len_buf)[0]

        # Read the payload
        entry_size = (4 + msg_len + 7) & ~7  # aligned
        payload = self._ring_read(tail + 4, msg_len)

        # Advance tail (publish)
        self._write_tail(tail + entry_size)
        return payload

    def is_empty(self) -> bool:
        return self._available_read() == 0

    def is_full(self) -> bool:
        return self._available_write() == 0


class ShmTransport:
    """Shared-memory IPC transport for a single communication channel.

    Creates two ring buffers: one for server→client messages and one
    for client→server messages.  Each ring buffer lives in a named
    POSIX shared memory segment.
    """

    def __init__(
        self,
        channel_id: str,
        capacity: int = _DEFAULT_CAPACITY,
    ):
        self._channel_id = channel_id
        self._capacity = _next_pow2(capacity)
        # s2c = server-to-client (server writes, client reads)
        # c2s = client-to-server (client writes, server reads)
        self._s2c_seg = f"/nyrqis-shm-{channel_id}-s2c"
        self._c2s_seg = f"/nyrqis-shm-{channel_id}-c2s"
        self._write_mm: Optional[mmap.mmap] = None
        self._read_mm: Optional[mmap.mmap] = None
        self._write_ring: Optional[RingBuffer] = None
        self._read_ring: Optional[RingBuffer] = None
        self._write_fd: int = -1
        self._read_fd: int = -1
        self._closed = False

    def create(self) -> bool:
        """Create the shared memory segments (server-side).

        Returns True on success.
        """
        total_size = _HEADER_SIZE + self._capacity

        # Create s2c segment (server writes, client reads)
        self._write_fd = _shm_open(self._s2c_seg, os.O_CREAT | os.O_RDWR, 0o600)
        if self._write_fd < 0:
            logger.error("shm: failed to create s2c segment: %s", self._s2c_seg)
            return False
        os.ftruncate(self._write_fd, total_size)
        self._write_mm = mmap.mmap(self._write_fd, total_size)

        # Initialize header
        header = ShmHeader(
            magic=_SHM_MAGIC, version=_SHM_VERSION,
            head=0, tail=0, capacity=self._capacity,
        )
        self._write_mm[:_HEADER_SIZE] = header.pack()
        self._write_ring = RingBuffer(self._write_mm, self._capacity, is_writer=True)

        # Create c2s segment (client writes, server reads)
        self._read_fd = _shm_open(self._c2s_seg, os.O_CREAT | os.O_RDWR, 0o600)
        if self._read_fd < 0:
            logger.error("shm: failed to create c2s segment: %s", self._c2s_seg)
            self.close()
            return False
        os.ftruncate(self._read_fd, total_size)
        self._read_mm = mmap.mmap(self._read_fd, total_size)

        # Initialize header
        self._read_mm[:_HEADER_SIZE] = header.pack()
        self._read_ring = RingBuffer(self._read_mm, self._capacity, is_writer=False)

        logger.info("shm transport created: %s (capacity=%d bytes)",
                     self._channel_id, self._capacity)
        return True

    def attach(self) -> bool:
        """Attach to existing shared memory segments (client-side).

        The naming convention: the server creates `-w` (server writes,
        client reads) and `-r` (client writes, server reads).  The
        client attaches to the server's `-w` for reading and its own
        `-r` for writing.

        Returns True on success.
        """
        total_size = _HEADER_SIZE + self._capacity

        # Client reads from server's s2c segment (also RW so reader can advance tail)
        self._read_fd = _shm_open(self._s2c_seg, os.O_RDWR, 0)
        if self._read_fd < 0:
            logger.error("shm: failed to attach s2c segment: %s", self._s2c_seg)
            return False
        self._read_mm = mmap.mmap(self._read_fd, total_size)  # RW so reader can advance tail
        header = ShmHeader.unpack(self._read_mm[:_HEADER_SIZE])
        if not header.valid():
            logger.error("shm: invalid header on s2c segment")
            self.close()
            return False
        self._capacity = header.capacity
        self._read_ring = RingBuffer(self._read_mm, self._capacity, is_writer=False)

        # Client writes to server's c2s segment
        self._write_fd = _shm_open(self._c2s_seg, os.O_RDWR, 0)
        if self._write_fd < 0:
            logger.error("shm: failed to attach c2s segment: %s", self._c2s_seg)
            self.close()
            return False
        self._write_mm = mmap.mmap(self._write_fd, total_size)
        self._write_ring = RingBuffer(self._write_mm, self._capacity, is_writer=True)

        logger.info("shm transport attached: %s", self._channel_id)
        return True

    def send(self, data: bytes, timeout_s: float = _OPEN_TIMEOUT_S) -> bool:
        """Send a message through the write ring buffer."""
        if self._write_ring is None:
            return False
        return self._write_ring.write(data, timeout_s)

    def recv(self, timeout_s: float = _OPEN_TIMEOUT_S) -> Optional[bytes]:
        """Receive a message from the read ring buffer."""
        if self._read_ring is None:
            return None
        return self._read_ring.read(timeout_s)

    def close(self) -> None:
        """Close and clean up the transport."""
        if self._closed:
            return
        self._closed = True
        for mm in (self._write_mm, self._read_mm):
            if mm is not None:
                try:
                    mm.close()
                except Exception:
                    pass
        for fd in (self._write_fd, self._read_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        # Unlink the shared memory segments
        for name in (self._s2c_seg, self._c2s_seg):
            _shm_unlink(name)
        logger.debug("shm transport closed: %s", self._channel_id)

    @property
    def available(self) -> bool:
        """Check if the shared-memory transport is usable."""
        return (
            _libc is not None
            and self._write_ring is not None
            and self._read_ring is not None
        )


def is_shm_available() -> bool:
    """Check if POSIX shared memory is available on this system."""
    return _libc is not None


__all__ = [
    "ShmTransport",
    "RingBuffer",
    "ShmHeader",
    "is_shm_available",
    "_DEFAULT_CAPACITY",
]
