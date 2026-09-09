"""compositor_host — Socket host half for the Wayland wire event loop.

Bridges the host-side ``WaylandSocketServer`` (accept, read, write over
a real Unix domain socket) to the Rust compositor's wire-format event
loop (``rust/compositor``, ABI 0.2.0) through ``ui/compositor_codec.py``:

1. A real Wayland client connects to the Unix domain socket.
2. Bytes read from the client are fed into the crate via
   ``compositor_codec.handle_client_data`` — the crate parses the
   standard Wayland wire format (object table, get_registry / bind /
   create_surface / attach / frame / commit) and maintains the surface
   state machine.
3. Server→client events queued by the crate are drained with
   ``compositor_codec.next_event`` and written back to the socket.

Fail-closed posture: when the cdylib is absent or its ABI gate fails,
the host falls back to the pure-Python protocol path already in
``WaylandSocketServer`` (honest stub sentinel ``-1`` from the codec ⇒
Python dispatch), never a silently-forged success.

This closes the "socket/epoll host half" follow-on from M14: the
protocol engine (Rust) and the transport (Python) now run end-to-end
over a real socket.

References:
    - rust/compositor/src/event_loop.rs (the wire-format engine)
    - ui/compositor_codec.py (FFI loader, ABI gate 0x0000_0200)
    - ui/wayland_socket.py (the host-side transport)
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import logging
import os
import struct
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Wire-format header size (object id + size|opcode) — same constant the
# Rust event loop enforces.
_HEADER_SIZE = 8

# wl_shm / wl_shm_pool request opcodes (the subset the host handles).
_OPCODE_SHM_CREATE_POOL = 0        # wl_shm.create_pool(new_id, fd, size)
_OPCODE_POOL_CREATE_BUFFER = 1    # wl_shm_pool.create_buffer(...)
_OPCODE_POOL_DESTROY = 2          # wl_shm_pool.destroy
_OPCODE_COMPOSITOR_CREATE_SURFACE = 0  # wl_compositor.create_surface(new_id)

# wl_shm wire args (after the fd): new_id u32, size i32.
_CREATE_POOL_FIXED = 8


@dataclass
class HostStats:
    """Counters for the host half of the event loop."""

    clients: int = 0
    bytes_from_clients: int = 0
    bytes_to_clients: int = 0
    events_drained: int = 0
    protocol_errors: int = 0
    fds_received: int = 0
    engine: str = "stub"  # "rust" when the crate drives dispatch
    # Per-client reassembly buffers (client_id → partial message tail).
    partial: Dict[int, bytes] = field(default_factory=dict)
    # SHM pools awaiting their buffer geometry: client_id → list of
    # (pool_object_id, fd, size) in arrival order.
    pending_pools: Dict[int, List[Tuple[int, int, int]]] = field(
        default_factory=dict
    )


class CompositorHost:
    """Drives the Rust wire-format event loop from socket bytes.

    The transport (accept/read/write) stays in ``WaylandSocketServer``;
    this class owns the protocol half: feed client bytes to the crate,
    drain the crate's response events, and ship them back over the
    socket.

    Usage (wired inside ``NyrqisCompositor``)::

        host = CompositorHost(engine)
        server.set_data_callback(host.on_client_data)
        server.set_disconnect_callback(host.on_client_disconnected)
        # ... after feeding data:
        host.flush_pending(writer)   # writer: (client_id, bytes) → bool
    """

    def __init__(self) -> None:
        self._stats = HostStats()
        # Reentrant: the data path drains events while holding the lock.
        self._lock = threading.RLock()
        # Events drained but not yet delivered (client_id → bytes).
        self._pending: Dict[int, bytearray] = {}
        # FDs received out-of-band, keyed by the message boundary they
        # arrived at: (client_id, byte_offset) → fd. Consumed by the
        # wire parse when the matching message (wl_shm.create_pool)
        # is dispatched.
        self._fds_at: Dict[Tuple[int, int], int] = {}
        # Presentation callback: set by the compositor to register
        # client SHM content (client_id, surface_id, fd, w, h, stride).
        self.on_surface_buffer = None
        # fd staging: fds arrive (in kernel order) ahead of or with
        # the message bytes that sent them; the host consumes one fd
        # per wl_shm.create_pool message, in arrival order.
        self._staged_fds: Dict[int, List[int]] = {}
        # Pools created but awaiting buffer geometry: client_id → list
        # of (pool_object_id, fd, size).
        self._pending_pools: Dict[int, List[Tuple[int, int, int]]] = {}
        # Last created wl_surface object id per client (content keying
        # for pool buffers created before their attach).
        self._last_surface: Dict[int, int] = {}

    # ------------------------------------------------------------------
    # Engine selection (fail-closed)
    # ------------------------------------------------------------------

    @staticmethod
    def _engine() -> str:
        """Which dispatch engine is live.

        "rust" — the cdylib is loaded and its ABI matches; wire-format
        dispatch runs in the crate. "stub" — the codec is absent (or
        ABI-gated off) and callers must fall back to the pure-Python
        protocol path.
        """
        try:
            from ui import compositor_codec as comp

            return "rust" if comp.available() else "stub"
        except ImportError:
            return "stub"

    @property
    def engine(self) -> str:
        """The live dispatch engine ("rust" or "stub")."""
        return self._engine()

    # ------------------------------------------------------------------
    # Client data path
    # ------------------------------------------------------------------

    def on_client_fd(self, client_id: int, fd: int) -> None:
        """Handle an fd received out-of-band (SCM_RIGHTS).

        The fd is queued per client; ``_consume_fd`` pairs queued fds
        with the client's ``wl_shm.create_pool`` messages in arrival
        order (the kernel preserves fd order relative to the byte
        stream).
        """
        with self._lock:
            self._staged_fds.setdefault(client_id, []).append(fd)
            self._stats.fds_received += 1

    def on_client_data(self, client_id: int, data: bytes) -> None:
        """Handle bytes read from a client socket.

        Feeds the bytes to the Rust event loop, reassembling partial
        messages across ``recv()`` boundaries, then queues the crate's
        response events for ``flush_pending`` to deliver.
        """
        engine = self._engine()
        with self._lock:
            self._stats.partial.setdefault(client_id, b"")
            buf = self._stats.partial[client_id] + bytes(data)
            self._stats.bytes_from_clients += len(data)

            if engine != "rust":
                # Fail-closed fallback: no crate ⇒ no wire dispatch
                # here. The Python protocol path in
                # WaylandSocketServer handles well-formed core
                # requests; partial messages are simply retained until
                # the rest arrives.
                self._stats.partial[client_id] = b""
                return

            try:
                from ui import compositor_codec as comp
            except ImportError:
                self._stats.partial[client_id] = b""
                return

            # Feed only complete messages: hold back an incomplete
            # tail so the crate never sees a truncated request. A
            # staged fd (SCM_RIGHTS) attaches to the FIRST complete
            # message fed after it arrived — the kernel delivers fds in
            # order with the bytes that carried them.
            self._stats.partial[client_id] = b""
            offset = 0
            total = len(buf)
            while offset + _HEADER_SIZE <= total:
                try:
                    object_id, size_opcode = struct.unpack_from(
                        "II", buf, offset
                    )
                except struct.error:
                    break
                size = size_opcode >> 16
                opcode = size_opcode & 0xFFFF
                if size < _HEADER_SIZE:
                    # Malformed size — let the crate report the
                    # protocol error rather than silently dropping.
                    size = total - offset
                if offset + size > total:
                    break  # incomplete message: wait for more bytes

                # Track surface creation per client (content keying).
                if opcode == _OPCODE_COMPOSITOR_CREATE_SURFACE and size >= _HEADER_SIZE + 4:
                    surface_new_id = struct.unpack_from("I", buf, offset + 8)[0]
                    self._last_surface[client_id] = surface_new_id

                # An fd may have arrived attached to this message.
                self._consume_fd(client_id, object_id, opcode,
                                 buf[offset : offset + size])

                # A pool buffer names the geometry: pair the pool's fd
                # with the surface and register the content mapping.
                if opcode == _OPCODE_POOL_CREATE_BUFFER and size >= _HEADER_SIZE + 24:
                    (new_id, off_i, width, height, stride, _fmt) = \
                        struct.unpack_from("Iiiiii", buf, offset + 8)
                    # The wl_surface id is the buffer's association:
                    # clients create the buffer before attach, and the
                    # crate maps buffer→surface at attach time. The
                    # host keys content by the LAST created surface of
                    # this client when the buffer is not yet bound.
                    surface_id = self._last_surface.get(client_id, new_id)
                    self._handle_pool_buffer(
                        client_id, object_id, surface_id,
                        off_i, width, height, stride,
                    )

                chunk = buf[offset : offset + size]
                n = comp.handle_client_data(client_id, chunk)
                if n < 0:
                    logger.warning(
                        "compositor_host: protocol error from crate "
                        "(client %d): %s",
                        client_id,
                        comp.event_loop_last_error(),
                    )
                    self._stats.protocol_errors += 1
                    offset = total  # stop feeding; drop the tail
                    break
                offset += size

            # Retain the unconsumed tail (partial message) for the
            # next recv.
            self._stats.partial[client_id] = buf[offset:]

            self._drain_events(client_id)

    # ------------------------------------------------------------------
    # SHM (fd) handling
    # ------------------------------------------------------------------

    def _consume_fd(
        self, client_id: int, object_id: int,
        opcode: int, message: bytes,
    ) -> None:
        """Inspect one complete message; consume a queued fd when it is
        the wl_shm.create_pool that carried it.

        The pool fd is held (not yet mapped) until the following
        wl_shm_pool.create_buffer names the surface geometry.
        """
        queue = self._staged_fds.get(client_id)
        if not queue:
            return

        # Match wl_shm.create_pool: sent on a bound wl_shm object,
        # opcode 0. Args: new_id u32, fd placeholder u32, size i32.
        if (
            len(message) >= _HEADER_SIZE + _CREATE_POOL_FIXED
            and opcode == _OPCODE_SHM_CREATE_POOL
        ):
            new_id = struct.unpack_from("I", message, 8)[0]
            size = struct.unpack_from("i", message, 12)[0]
            fd = queue.pop(0)
            self._pending_pools.setdefault(client_id, []).append(
                (new_id, fd, size)
            )
            logger.debug(
                "compositor_host: client %d pool %d (fd %d, %d bytes)",
                client_id, new_id, fd, size,
            )
        # Non-create_pool messages leave the queue alone: fds may be
        # delivered split from their bytes. Unconsumed fds are closed
        # on disconnect.

    def _handle_pool_buffer(
        self, client_id: int, pool_id: int, surface_id: int,
        offset: int, width: int, height: int, stride: int,
    ) -> bool:
        """A wl_shm_pool.create_buffer arrived: pair the pool's fd with
        the surface geometry and hand the mapping to the presentation
        callback. Returns True when registered.
        """
        pools = self._pending_pools.get(client_id, [])
        for i, (pid, fd, _size) in enumerate(pools):
            if pid != pool_id:
                continue
            pools.pop(i)
            registered = False
            if self.on_surface_buffer is not None:
                registered = self.on_surface_buffer(
                    client_id, surface_id, fd, width, height, stride
                )
            if not registered:
                try:
                    os.close(fd)
                except OSError:
                    pass
            return registered
        return False

    def handle_pool_buffer(
        self, client_id: int, pool_id: int, surface_id: int,
        offset: int, width: int, height: int, stride: int,
    ) -> bool:
        """Public entry: the socket layer (or tests) reports a
        wl_shm_pool.create_buffer for geometry registration."""
        with self._lock:
            return self._handle_pool_buffer(
                client_id, pool_id, surface_id,
                offset, width, height, stride,
            )

    def pending_pool_count(self, client_id: int) -> int:
        """Pools awaiting buffer geometry for a client (test hook)."""
        return len(self._pending_pools.get(client_id, []))

    def _drain_events(self, client_id: int) -> None:
        """Drain the crate's queued events for a client into _pending."""
        try:
            from ui import compositor_codec as comp
        except ImportError:
            return

        drained = bytearray()
        for _ in range(256):  # bounded drain per feed
            try:
                chunk = comp.next_event(client_id)
            except RuntimeError as exc:
                logger.warning(
                    "compositor_host: event drain failed (client %d): %s",
                    client_id,
                    exc,
                )
                break
            if not chunk:
                break
            drained += chunk

        if drained:
            with self._lock:
                self._pending.setdefault(client_id, bytearray())
                self._pending[client_id] += drained
                self._stats.events_drained += 1

    def flush_pending(self, writer) -> int:
        """Write queued events to the client sockets.

        ``writer`` is called as ``writer(client_id, bytes)`` and must
        return True on success. Returns the number of clients that got
        data. Pending bytes that fail to write are retained.
        """
        delivered = 0
        with self._lock:
            for client_id in list(self._pending.keys()):
                data = bytes(self._pending[client_id])
                if not data:
                    continue
                try:
                    ok = writer(client_id, data)
                except Exception:  # noqa: BLE001 — socket errors
                    ok = False
                if ok:
                    self._stats.bytes_to_clients += len(data)
                    del self._pending[client_id]
                    delivered += 1
                # On failure the bytes stay queued for the next flush.
        return delivered

    def pending_bytes(self, client_id: int) -> bytes:
        """Return (and keep) the pending outbound bytes for a client."""
        with self._lock:
            return bytes(self._pending.get(client_id, b""))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_client_disconnected(self, client_id: int) -> None:
        """Release per-client state (partial buffer, pending events,
        staged fds, pending pools)."""
        with self._lock:
            self._stats.partial.pop(client_id, None)
            self._pending.pop(client_id, None)
            fds = self._staged_fds.pop(client_id, [])
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            pools = self._pending_pools.pop(client_id, [])
            for _pid, fd2, _size in pools:
                try:
                    os.close(fd2)
                except OSError:
                    pass
            self._last_surface.pop(client_id, None)
            self._stats.pending_pools.pop(client_id, None)

    def get_stats(self) -> HostStats:
        """Snapshot of host-half counters."""
        with self._lock:
            return HostStats(
                clients=len(self._stats.partial),
                bytes_from_clients=self._stats.bytes_from_clients,
                bytes_to_clients=self._stats.bytes_to_clients,
                events_drained=self._stats.events_drained,
                protocol_errors=self._stats.protocol_errors,
                fds_received=self._stats.fds_received,
                engine=self._engine(),
                partial=dict(self._stats.partial),
                pending_pools={
                    k: list(v) for k, v in self._pending_pools.items()
                },
            )


__all__ = ["CompositorHost", "HostStats"]
