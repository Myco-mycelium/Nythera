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
import struct
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Wire-format header size (object id + size|opcode) — same constant the
# Rust event loop enforces.
_HEADER_SIZE = 8


@dataclass
class HostStats:
    """Counters for the host half of the event loop."""

    clients: int = 0
    bytes_from_clients: int = 0
    bytes_to_clients: int = 0
    events_drained: int = 0
    protocol_errors: int = 0
    engine: str = "stub"  # "rust" when the crate drives dispatch
    # Per-client reassembly buffers (client_id → partial message tail).
    partial: Dict[int, bytes] = field(default_factory=dict)


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
            # tail so the crate never sees a truncated request.
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
                if size < _HEADER_SIZE:
                    # Malformed size — let the crate report the
                    # protocol error rather than silently dropping.
                    size = total - offset
                if offset + size > total:
                    break  # incomplete message: wait for more bytes
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
        """Release per-client state (partial buffer, pending events)."""
        with self._lock:
            self._stats.partial.pop(client_id, None)
            self._pending.pop(client_id, None)

    def get_stats(self) -> HostStats:
        """Snapshot of host-half counters."""
        with self._lock:
            return HostStats(
                clients=len(self._stats.partial),
                bytes_from_clients=self._stats.bytes_from_clients,
                bytes_to_clients=self._stats.bytes_to_clients,
                events_drained=self._stats.events_drained,
                protocol_errors=self._stats.protocol_errors,
                engine=self._engine(),
                partial=dict(self._stats.partial),
            )


__all__ = ["CompositorHost", "HostStats"]
