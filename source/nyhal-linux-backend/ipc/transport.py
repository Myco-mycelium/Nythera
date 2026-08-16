#!/usr/bin/env python3
"""
IPC Transport — Unix-domain datagram channel (NPS-017 §4.3, plan §4.3)

Moves :class:`~ipc.core.IPCMessage` objects between processes over
``AF_UNIX SOCK_DGRAM`` sockets, serialized with the canonical wire
format (``ipc_codec``, ADR-0020 migration #4 — the framing whose
parsing is the transport's trust boundary).

Addressing
----------
An IPC endpoint's transport address IS its socket path. A server bound
at a path dispatches inbound datagrams to the ``IPCManager`` endpoint
of the same id; a caller's ``IPCClient`` binds its own path and speaks
to peer paths.

Sender identity (the honest trust anchor)
----------------------------------------
A datagram sender is authenticated by the kernel, not by anything on
the wire: the receiver sets ``SO_PASSCRED`` and the kernel attaches
the real sender's ``(pid, uid, gid)`` to EVERY inbound datagram (the
sender attaches nothing, so it cannot influence or forge its identity
at all). The backend maps the pid to a container, and a wire
``sender_id`` that does not match the authenticated container is
rejected as a forgery. The attached pid is the sender's **global**
pid and the uid/gid its **real** values (verified 2026-08-14: a
sender inside an unprivileged new pid+user namespace presented its
namespace-local pid as 1 to itself while the receiver saw the host
pid and host uid/gid). ``SO_PEERCRED`` does NOT work on datagram
sockets (it returns ``(0, -1, -1)``, verified on this host), so
receiver-side ``SO_PASSCRED`` + kernel-attached ``SCM_CREDENTIALS``
is the mechanism here.

Capability enforcement
----------------------
Inbound datagrams are checked before delivery: the authenticated
sender must hold ``CAP_IPC_SEND`` (control plane, NPS-017 §4.2) and
the destination endpoint enqueues through its token bucket (ADR-0009).
The receive-side ``CAP_IPC_RECEIVE`` check stays on the consumer's
``IPCManager.receive()``, exactly as in the in-process path.

Operator identity (the control plane)
-------------------------------------
A second, non-container identity exists for the daemon's control
plane: the daemon's OWN user (``trusted_uids``, default the daemon's
uid) may act as ``host-operator``. The kernel-attached uid is as
unforgeable as the pid — only a process running as the daemon's user
can claim it — and such a process already has full control of the
daemon (it could kill or restart it), so the capability model for
containers deliberately does not apply to it; the control service
validates its operations instead. Resolution is container-FIRST: a
registered container pid always takes the container path (a container
spawned by the daemon runs as the same user, so its datagrams must
NOT be misattributed to the operator), and the operator path only
applies to pids unknown to the registry.

CALL/REPLY
----------
The server answers a ``CALL`` at the **kernel-observed sender address**
(``sender_path`` from ``recvmsg`` — authoritative, never a claimed
path); the client correlates ``REPLY``s by ``reply_to``. The caller
still records ``metadata['reply_path']`` on every outbound message for
diagnostics, but the server never trusts it.

References:
- NPS-017 §4.3: IPC Semantics
- NPS-003 §3–4: IPC Primitives and Endpoint Model
- ADR-0020 priority #4: IPC wire codec (``ipc/ipc_codec.py``)
- ADR-0020 priority #6: Rust transport hot path (``ipc/transport_codec.py``,
  ``rust/transport``) — the per-message sendto/recvmsg syscall path
  when the crate is built; the Python floor here is the byte-identical
  fallback and the CI conformance gate's differential.
- ADR-0009: per-container token-bucket rate limiting
"""

import ctypes
import hashlib
import json
import logging
import os
import socket
import struct
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import transport_codec  # ADR-0020 priority #6 FFI loader (transport hot path)
from . import loop as ipc_loop  # ADR-0021 client half (Rust client call)
from .core import IPCManager, IPCMessage, IPCMessageType

# The default operator identity: the sender a daemon recognizes as its
# operator on the trusted-uid path (see ``IPCDatagramServer._authorized``).
# Defined here — the server is the auth boundary — and imported by the
# control service, so both sides always agree by construction.
DEFAULT_OPERATOR_ID = "host-operator"

# ---------------------------------------------------------------------------
# Wire-level streaming framing (ADR-0024 follow-on)
# ---------------------------------------------------------------------------
# A STREAM_CHUNK message's payload is a binary envelope, byte-identical
# between the Python floor and the Rust serving loop (rust/ipcd):
#
#   u8   version = 1
#   u32  stream_id_len + stream_id       (48-bit CSPRNG -> 6 bytes)
#   u32  call_id_len + call_id           (the logical CALL id / reply_to)
#   u32  chunk_index
#   u32  chunk_count
#   u32  payload_len + payload
#   32B  sha256(payload)
#
# The receiver reassembles by (kernel-attached sender, stream_id) with
# the ADR-0024 bounds: at most ``STREAM_MAX_CHUNKS`` chunks (16 MiB at
# ``STREAM_CHUNK_BYTES``), a 30 s reassembly TTL, per-chunk checksums
# verified before dispatch, and the stream bound to its first chunk's
# sender. The codec's ``reply_to`` field rides the correlation for
# REPLY chunks (the existing correlation machinery untouched); the
# envelope's ``call_id`` is authoritative for CALL chunks (it becomes
# the reassembled CALL's ``message_id``).
STREAM_CHUNK_BYTES = 32 * 1024
STREAM_MAX_CHUNKS = 512
STREAM_TTL_S = 30.0
STREAM_MAX_STREAMS = 64
_STREAM_ENVELOPE_VERSION = 1
_STREAM_ID_BYTES = 6

# The largest payload a single datagram can carry. The wire recv buffer
# is 64 KiB on both halves (a larger datagram is truncated and dropped
# at the parse), and the codec wire carries ~42 B of fixed header +
# per-field length prefixes on top of the payload, so a payload that
# fits a datagram must stay comfortably under 64 KiB. A CALL/REPLY
# payload larger than this is what ADR-0024 chunks — the FRAMING
# boundary is the single-datagram budget, not the chunk size (a
# service-level stream piece of ≤32 KiB of data is ~44 KiB of JSON and
# still rides ONE datagram; chunking it would break old peers).
_DATAGRAM_PAYLOAD_BUDGET = 60 * 1024


def _encode_stream_chunk(
    stream_id: bytes, call_id: str, index: int, count: int, payload: bytes,
) -> bytes:
    """Encode one chunk envelope (ADR-0024 wire-level framing)."""
    cid = call_id.encode("utf-8")
    return (
        struct.pack("<B", _STREAM_ENVELOPE_VERSION)
        + struct.pack("<I", len(stream_id)) + stream_id
        + struct.pack("<I", len(cid)) + cid
        + struct.pack("<II", index, count)
        + struct.pack("<I", len(payload)) + payload
        + hashlib.sha256(payload).digest()
    )


def _decode_stream_chunk(
    data: bytes,
) -> Optional[Tuple[bytes, str, int, int, bytes]]:
    """Decode + verify one chunk envelope. Returns
    ``(stream_id, call_id, index, count, payload)`` or None when the
    envelope is malformed or fails a bound/checksum check (the
    receiver drops those fail-closed, exactly like the codec)."""
    try:
        pos = 0
        (version,) = struct.unpack_from("<B", data, pos)
        pos += 1
        if version != _STREAM_ENVELOPE_VERSION:
            return None
        (sid_len,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if sid_len != _STREAM_ID_BYTES or pos + sid_len > len(data):
            return None
        stream_id = data[pos:pos + sid_len]
        pos += sid_len
        (cid_len,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if pos + cid_len > len(data) or cid_len > 128:
            return None
        call_id = data[pos:pos + cid_len].decode("utf-8", "replace")
        pos += cid_len
        index, count = struct.unpack_from("<II", data, pos)
        pos += 8
        if index >= count or count > STREAM_MAX_CHUNKS:
            return None
        (plen,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if plen > STREAM_CHUNK_BYTES or pos + plen + 32 != len(data):
            return None
        payload = data[pos:pos + plen]
        pos += plen
        if hashlib.sha256(payload).digest() != data[pos:pos + 32]:
            return None
        return stream_id, call_id, index, count, payload
    except struct.error:
        return None


def build_reply_wires(call_id: str, payload: bytes) -> List[bytes]:
    """The wires that carry one reply payload: a single REPLY wire when
    it fits the datagram budget, or N STREAM_CHUNK wires when it does
    not (ADR-0024 wire-level framing). The floor's ``reply()`` sends
    these; the loop dispatcher (``ipc/dispatch.py``) enqueues them —
    byte-identical by construction, so a reply is identical whichever
    backend served it."""
    if len(payload) <= _DATAGRAM_PAYLOAD_BUDGET:
        return [
            IPCMessage(
                message_type=IPCMessageType.REPLY,
                payload=payload,
                reply_to=call_id,
            ).to_wire()
        ]
    stream_id = os.urandom(_STREAM_ID_BYTES)
    chunks = [
        payload[i:i + STREAM_CHUNK_BYTES]
        for i in range(0, len(payload), STREAM_CHUNK_BYTES)
    ]
    wires = []
    for i, chunk in enumerate(chunks):
        env = _encode_stream_chunk(stream_id, call_id, i, len(chunks), chunk)
        wires.append(
            IPCMessage(
                message_type=IPCMessageType.STREAM_CHUNK,
                payload=env,
                reply_to=call_id,
            ).to_wire()
        )
    return wires

logger = logging.getLogger(__name__)

# Sentinel for "the Rust backend is unavailable, use the Python floor"
# (distinct from recv's timeout, which is ``None``).
_RUST_FLOOR = object()

# AF_UNIX pathname sockets: sun_path is 108 bytes including the NUL.
_MAX_SOCKET_PATH = 100

# The sender credentials a receiver must be able to name-check. On
# datagram sockets the kernel supplies these via SCM_CREDENTIALS (with
# SO_PASSCRED); SO_PEERCRED is stream-only (returns (0,-1,-1) here).
_UCRED = struct.Struct("3i")
_SOL_SOCKET = socket.SOL_SOCKET
_SCM_CREDENTIALS = getattr(socket, "SCM_CREDENTIALS", 2)
_SO_PASSCRED = getattr(socket, "SO_PASSCRED", 16)
_CMSG_SPACE = socket.CMSG_SPACE(_UCRED.size)


class IPCTransportError(Exception):
    """Transport-level error (bad path, unbound socket, etc.)."""


def _peer_credentials(ancdata: list) -> Optional[Tuple[int, int, int]]:
    """Extract the kernel-attached (pid, uid, gid) from recvmsg
    ancillary data, or None when absent (a bare sendto still gets
    credentials because SO_PASSCRED forces the kernel to attach them)."""
    for level, ctype, data in ancdata:
        if level == _SOL_SOCKET and ctype == _SCM_CREDENTIALS:
            return _UCRED.unpack(data)
    return None


class UnixDatagramEndpoint:
    """One bound Unix-domain datagram socket (the primitive).

    The receiver sets ``SO_PASSCRED`` so every inbound datagram
    carries the kernel-attached sender credentials; senders attach
    NOTHING (a bare ``sendto``), so the kernel's attachment is the
    sender's real identity, uninfluenceable by the sender.
    """

    def __init__(self, path: str):
        if not path or len(path) > _MAX_SOCKET_PATH:
            raise IPCTransportError(
                f"socket path must be 1..{_MAX_SOCKET_PATH} bytes "
                f"(got {len(path)})"
            )
        self.path = path
        self._sock: Optional[socket.socket] = None
        # Reusable receive buffers for the Rust transport hot path
        # (FFI surface v2 — caller-supplied, no per-call allocation).
        # Allocated lazily on first Rust recv so floor-only hosts (no
        # crate) don't pay 128 KiB per endpoint.
        self._recv_wire: Optional[ctypes.Array] = None
        self._recv_path: Optional[ctypes.Array] = None

    def bind(self) -> "UnixDatagramEndpoint":
        """Bind the socket to ``self.path`` (idempotent) with the
        receiver-side credential option and 0700 permissions."""
        if self._sock is not None:
            return self
        try:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self._sock.setsockopt(_SOL_SOCKET, _SO_PASSCRED, 1)
            self._sock.bind(self.path)
            os.chmod(self.path, 0o700)
        except OSError:
            self.close()
            raise
        return self

    def close(self) -> None:
        """Close the socket and unlink the path (if still ours)."""
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def send(self, payload: bytes, peer_path: Optional[str] = None) -> None:
        """Send ``payload`` to ``peer_path`` (default: ``self.path``) as
        a bare datagram — no ancillary credentials. The receiver's
        ``SO_PASSCRED`` makes the kernel attach the sender's real
        identity to the datagram, so the sender cannot influence or
        forge it. Works from inside containers too: the kernel reports
        the sender's global pid and real uid/gid (verified with a
        sender in an unprivileged new pid+user namespace)."""
        if self._sock is None:
            raise IPCTransportError(f"endpoint {self.path} is not bound")
        target = peer_path or self.path
        try:
            try:
                transport_codec.send(self._sock.fileno(), payload, target)
                return  # routed through the Rust transport
            except transport_codec.BackendUnavailable:
                pass  # no crate built — use the Python floor
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if transport_codec.force_enabled():
                raise IPCTransportError(
                    f"send to {target} failed: {exc}"
                ) from exc
            logger.warning(
                "ipc transport: Rust send failed (%s: %s); using Python floor",
                type(exc).__name__, exc,
            )
        try:
            self._sock.sendto(payload, target)
        except OSError as e:
            raise IPCTransportError(f"send to {target} failed: {e}") from e

    def receive(
        self, timeout: Optional[float] = None
    ) -> Optional[Tuple[bytes, int, int, int, str]]:
        """Receive one datagram: ``(payload, pid, uid, gid, sender_path)``
        or None on timeout. The credentials are the kernel's — the
        sender's real identity, unforgeable by an unprivileged peer."""
        if self._sock is None:
            raise IPCTransportError(f"endpoint {self.path} is not bound")
        try:
            try:
                timeout_ms = -1 if timeout is None else max(0, int(timeout * 1000))
                # Only floor-only hosts (no crate) skip the buffer
                # allocation; once the Rust backend is present the
                # endpoint owns one pair for its lifetime.
                if self._recv_wire is None:
                    if not transport_codec.available():
                        raise transport_codec.BackendUnavailable()
                    self._recv_wire = ctypes.create_string_buffer(
                        transport_codec.RECV_WIRE_SIZE)
                    self._recv_path = ctypes.create_string_buffer(
                        transport_codec.RECV_PATH_SIZE)
                got = transport_codec.recv(
                    self._sock.fileno(), timeout_ms,
                    self._recv_wire, self._recv_path,
                )
            except transport_codec.BackendUnavailable:
                got = _RUST_FLOOR  # no crate built — use the Python floor
            if got is not _RUST_FLOOR:
                # Routed through the Rust transport: None = timeout.
                return got
        except Exception as exc:  # noqa: BLE001 - fall back by contract
            if transport_codec.force_enabled():
                raise IPCTransportError(
                    f"receive on {self.path} failed: {exc}"
                ) from exc
            logger.warning(
                "ipc transport: Rust recv failed (%s: %s); using Python floor",
                type(exc).__name__, exc,
            )
        try:
            self._sock.settimeout(timeout)
            data, ancdata, _flags, addr = self._sock.recvmsg(
                64 * 1024, _CMSG_SPACE
            )
        except socket.timeout:
            return None
        except OSError as e:
            raise IPCTransportError(f"receive on {self.path} failed: {e}") from e
        creds = _peer_credentials(ancdata)
        # SO_PASSCRED forces the kernel to attach credentials even for
        # a bare sendto; the (0,-1,-1) fallback is defensive only.
        pid, uid, gid = creds if creds is not None else (0, -1, -1)
        sender_path = addr if isinstance(addr, str) else ""
        return data, pid, uid, gid, sender_path

    def __enter__(self) -> "UnixDatagramEndpoint":
        return self.bind()

    def __exit__(self, *exc) -> None:
        self.close()


class IPCDatagramServer:
    """Serves one endpoint path: authenticate, enforce, deliver.

    Each ``serve_once`` receives one datagram, parses it through the
    wire codec (malformed input is dropped at the trust boundary),
    authenticates the sender (a registered container pid — checking
    the wire ``sender_id`` against it and ``CAP_IPC_SEND`` — or, for
    pids unknown to the registry, a trusted-uid ``host-operator``),
    then either invokes ``on_call`` (CALL) or enqueues to the endpoint
    (everything else, rate-limited per ADR-0009).
    """

    def __init__(
        self,
        manager: IPCManager,
        endpoint_id: str,
        path: str,
        pid_registry: Optional[Dict[int, str]] = None,
        capability_manager=None,
        on_call: Optional[Callable] = None,
        trusted_uids: Optional[set] = None,
        operator_id: str = DEFAULT_OPERATOR_ID,
    ):
        self.manager = manager
        self.endpoint_id = endpoint_id
        self.endpoint = UnixDatagramEndpoint(path)
        # pid -> container_id for authenticated senders (the backend
        # maintains this; a resolver callable is accepted too).
        self.pid_registry = pid_registry
        self.capability_manager = capability_manager
        self.on_call = on_call
        # Uids whose processes may act as the daemon's control plane
        # (``operator_id``): the daemon's own user by default. Container
        # resolution stays pid-FIRST, so a daemon-spawned container
        # (which runs as the same user) is never misattributed.
        self.trusted_uids = trusted_uids
        self.operator_id = operator_id
        # Wire-level stream reassembly (ADR-0024 follow-on): keyed by
        # ``(authenticated sender, stream_id)`` — the kernel-attached
        # sender is the ADR's "bind to the sender of its first chunk"
        # rule, never a claimed identity. Each slot holds the declared
        # chunk count, the logical call_id (from the envelope), the
        # buffered chunks by index, and a TTL for the sweep.
        self._streams: Dict[Tuple[str, bytes], Dict] = {}
        # Close coordination (see close/serve): close() waits for the
        # serve loop to exit before closing the endpoint, so the fd is
        # never closed while a poll is in flight on it (the fd-reuse
        # hazard close() documents below).
        self._close_lock = threading.Lock()
        self._closed = False
        self._serve_thread: Optional[threading.Thread] = None

    def bind(self) -> "IPCDatagramServer":
        self.endpoint.bind()
        return self

    def close(self) -> None:
        """Request shutdown and release the socket. Idempotent; safe
        from any thread.

        When a serve loop is running, close() waits for it to exit
        (bounded — the loop notices the closed flag within one poll
        window) BEFORE closing the endpoint, so the fd is never closed
        while a ``poll`` is in flight on it. Closing an fd out from
        under another thread's poll is a classic fd-reuse hazard: the
        kernel may hand the freed number to the next bound socket, and
        the stale poll can then steal ONE datagram from it. That bit
        the wire-level streaming path directly (ADR-0024): a server
        torn down with ``stop.set(); close()`` left its serve thread
        mid-poll; the next bind reused the fd; the zombie received one
        STREAM_CHUNK into its own dead ``_streams``; the live server's
        reassembly never completed (one chunk short) and the caller
        timed out. Because the thread is joined, the socket path is
        unlinked before close() returns — a caller can bind a new
        endpoint at the same path immediately."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            thread = self._serve_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.endpoint.close()

    def _resolve_sender(self, pid: int) -> Optional[str]:
        if self.pid_registry is None:
            return None
        if callable(self.pid_registry):
            return self.pid_registry(pid)
        return self.pid_registry.get(pid)

    def _authorized(self, msg: IPCMessage, pid: int, uid: int) -> Optional[str]:
        """Return the authenticated sender identity, or None when the
        datagram must be dropped. Two paths, container-FIRST:

        - **Container** — the pid resolves via the registry; the wire
          ``sender_id`` must match the resolved container (an empty or
          forged one is dropped — the transport is the attribution
          boundary) and the container must hold ``CAP_IPC_SEND``.
        - **Operator** — the pid is unknown (the operator's short-lived
          CLI is never registered); the kernel-attached uid must be in
          ``trusted_uids`` AND the wire must claim ``operator_id``. A
          daemon-spawned container runs as the same user, so this path
          only ever applies to pids the registry does not know.
        """
        sender = self._resolve_sender(pid)
        if sender is not None:
            if msg.sender_id != sender:
                logger.warning(
                    "ipc: dropping %s sender_id %r — SCM_CREDENTIALS "
                    "authenticates pid %d as container %r",
                    "forged" if msg.sender_id else "missing",
                    msg.sender_id, pid, sender,
                )
                return None
            if self.capability_manager is not None:
                from backend.capability import Capability
                if not self.capability_manager.validate_operation(
                    sender, Capability.CAP_IPC_SEND
                ):
                    logger.warning(
                        "ipc: container %s lacks CAP_IPC_SEND; dropping datagram",
                        sender,
                    )
                    return None
            return sender
        if self.trusted_uids is not None and uid in self.trusted_uids:
            if msg.sender_id == self.operator_id:
                return self.operator_id
            logger.warning(
                "ipc: dropping %s sender_id %r from trusted uid %d — "
                "the operator must claim %r",
                "forged" if msg.sender_id else "missing",
                msg.sender_id, uid, self.operator_id,
            )
            return None
        logger.warning(
            "ipc: dropping datagram from unknown pid %d uid %d (pid not "
            "in the container registry and uid not trusted)", pid, uid,
        )
        return None

    def reply(self, sender_path: str, call_id: str, payload: bytes) -> bool:
        """Send the REPLY for call ``call_id`` to ``sender_path``: a
        single REPLY when the payload fits the datagram budget, or a
        sequence of STREAM_CHUNK messages when it does not (ADR-0024
        wire-level framing — a large reply is chunked by the wire
        layer the loop owns, never by the service)."""
        for wire in build_reply_wires(call_id, payload):
            self.endpoint.send(wire, peer_path=sender_path)
        logger.info("ipc: replied to call %s (%d bytes)", call_id[:8], len(payload))
        return True

    def _accept_stream_chunk(
        self, sender: str, msg: IPCMessage, sender_path: str,
    ) -> Optional[IPCMessage]:
        """Buffer one STREAM_CHUNK datagram from ``sender`` (ADR-0024
        wire-level framing). Returns the reassembled CALL when the
        stream is complete (the caller dispatches it like any CALL),
        or None while chunks are still buffering (or on rejection).

        Bounds mirror the service-level stream (ADR-0024): at most
        ``STREAM_MAX_CHUNKS`` chunks (16 MiB at 32 KiB), a TTL sweep
        (an incomplete stream is dropped — the caller's paging path
        takes over), a per-sender bind to the FIRST chunk, and a
        duplicate/mismatched chunk rejects the whole stream
        fail-closed. The logical call_id rides the envelope (and the
        codec ``reply_to`` for REPLY chunks) so the reassembled CALL
        correlates normally."""
        env = _decode_stream_chunk(msg.payload)
        if env is None:
            logger.warning("ipc: dropping malformed STREAM_CHUNK on %s",
                           self.endpoint_id)
            return None
        stream_id, call_id, index, count, payload = env
        now = time.monotonic()
        # TTL sweep on arrival: incomplete streams older than the
        # window are dropped (bounded memory, independent of the
        # declared size).
        expired = [
            key for key, slot in self._streams.items()
            if now - slot["last_seen"] > STREAM_TTL_S
        ]
        for key in expired:
            logger.info(
                "ipc: dropping expired stream %s (%d/%d chunks)",
                key[1][:4].hex(), len(self._streams[key]["chunks"]),
                self._streams[key]["count"],
            )
            del self._streams[key]
        key = (sender, stream_id)
        slot = self._streams.get(key)
        if slot is None:
            if len(self._streams) >= STREAM_MAX_STREAMS:
                logger.warning(
                    "ipc: stream table full (%d); dropping chunk",
                    STREAM_MAX_STREAMS,
                )
                return None
            slot = {
                "count": count,
                "call_id": call_id,
                "chunks": {},
                "last_seen": now,
                "sender_path": sender_path,
            }
            self._streams[key] = slot
        else:
            # Bind to the first chunk's sender: a chunk from a
            # different sender fails closed even with a matching id
            # (the dict key already enforces this — the check is the
            # fail-closed floor for a corrupted slot).
            if slot["count"] != count or slot["call_id"] != call_id:
                logger.warning(
                    "ipc: stream %s changed mid-stream; rejecting",
                    stream_id[:4].hex(),
                )
                del self._streams[key]
                return None
        slot["last_seen"] = now
        if index in slot["chunks"]:
            logger.warning(
                "ipc: duplicate chunk %d of stream %s; rejecting",
                index, stream_id[:4].hex(),
            )
            del self._streams[key]
            return None
        slot["chunks"][index] = payload
        if len(slot["chunks"]) < slot["count"]:
            return None  # still buffering
        # Complete: reassemble in index order and synthesize the CALL
        # the client meant (the envelope's call_id is the message_id,
        # so the service's reply correlates through the normal path).
        del self._streams[key]
        body = b"".join(
            slot["chunks"][i] for i in range(slot["count"])
        )
        return IPCMessage(
            message_type=IPCMessageType.CALL,
            message_id=call_id,
            sender_id=sender,
            payload=body,
        )

    def serve_once(self, timeout: Optional[float] = None) -> Optional[IPCMessage]:
        """Receive and dispatch one datagram; returns the dispatched
        message (or None on timeout/drop)."""
        got = self.endpoint.receive(timeout)
        if got is None:
            return None
        payload, pid, uid, gid, sender_path = got
        try:
            msg = IPCMessage.from_wire(payload)
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(
                "ipc: dropping malformed wire datagram on %s (%s)",
                self.endpoint_id, e,
            )
            return None
        sender = self._authorized(msg, pid, uid)
        if sender is None:
            return None
        if msg.message_type == IPCMessageType.CALL:
            if self.on_call is not None:
                try:
                    self.on_call(msg, sender, sender_path)
                except Exception as e:  # noqa: BLE001 - one bad handler must not kill the serve loop
                    logger.error(
                        "ipc: on_call handler on %s raised %s: %s",
                        self.endpoint_id, type(e).__name__, e,
                    )
            return msg
        if msg.message_type == IPCMessageType.STREAM_CHUNK:
            # Wire-level streaming (ADR-0024 follow-on): reassemble the
            # chunks into the CALL the client meant, then dispatch it
            # through the SAME on_call path (the service sees a normal
            # CALL with the full payload — it never knows the chunks
            # existed). Intermediate chunks return None (no dispatch).
            call_msg = self._accept_stream_chunk(sender, msg, sender_path)
            if call_msg is None:
                return None
            if self.on_call is not None:
                try:
                    self.on_call(call_msg, sender, sender_path)
                except Exception as e:  # noqa: BLE001 - one bad handler must not kill the serve loop
                    logger.error(
                        "ipc: on_call handler on %s raised %s: %s",
                        self.endpoint_id, type(e).__name__, e,
                    )
            return call_msg
        endpoint = self.manager.get_endpoint(self.endpoint_id)
        if endpoint is None:
            logger.warning("ipc: endpoint %s not found; dropping", self.endpoint_id)
            return None
        if not endpoint.send_message(msg):
            logger.warning(
                "ipc: endpoint %s rate-limited the inbound datagram",
                self.endpoint_id,
            )
            return None
        return msg

    def serve(self, stop_event, poll_s: float = 0.2) -> None:
        """Blocking dispatch loop until ``stop_event`` is set or the
        server is closed. A transient transport error (e.g. an
        EINTR-wrapped OSError) is logged and the loop continues — one
        bad datagram must not kill the serving thread.

        close() joins this loop before closing the endpoint (see
        :meth:`close` — the fd-reuse hazard that motivated it), so the
        loop must exit promptly once ``stop_event`` is set or close()
        is called: it checks both on every iteration, and a poll is
        never left running past ``poll_s``."""
        with self._close_lock:
            if self._closed:
                return
            self._serve_thread = threading.current_thread()
        try:
            while not stop_event.is_set() and not self._closed:
                try:
                    self.serve_once(timeout=poll_s)
                except IPCTransportError as e:
                    if self._closed:
                        break
                    logger.warning("ipc: transport error, continuing: %s", e)
        finally:
            with self._close_lock:
                self._serve_thread = None
            self.endpoint.close()


class IPCClient:
    """A container's transport endpoint (the caller side).

    Binds its own socket path (so the server can reply to CALLs at the
    kernel-observed sender address) and speaks to peer endpoint paths
    with the wire codec. Sends carry no credentials: the receiving
    server's ``SO_PASSCRED`` supplies the kernel-attached identity, so
    the client cannot claim a different container even if it wanted to.
    """

    def __init__(self, container_id: str, path: str):
        self.container_id = container_id
        self.endpoint = UnixDatagramEndpoint(path)

    def bind(self) -> "IPCClient":
        self.endpoint.bind()
        return self

    def close(self) -> None:
        self.endpoint.close()

    def _send_message(self, peer_path: str, msg: IPCMessage) -> None:
        # metadata['reply_path'] is recorded for diagnostics only — the
        # server answers CALLs at the kernel-observed sender address.
        msg.metadata.setdefault("reply_path", self.endpoint.path)
        self.endpoint.send(msg.to_wire(), peer_path=peer_path)

    def send(
        self, peer_path: str, payload: bytes,
        capabilities: Optional[List[str]] = None,
    ) -> IPCMessage:
        """Asynchronous SEND to ``peer_path`` (NPS-003 §3.1)."""
        msg = IPCMessage(
            message_type=IPCMessageType.SEND,
            sender_id=self.container_id,
            payload=payload,
            capabilities=capabilities or [],
        )
        self._send_message(peer_path, msg)
        return msg

    def notify(self, peer_path: str, event_type: str) -> IPCMessage:
        """Lightweight NOTIFY to ``peer_path`` (NPS-003 §3.4)."""
        msg = IPCMessage(
            message_type=IPCMessageType.NOTIFY,
            sender_id=self.container_id,
            payload=event_type.encode(),
        )
        self._send_message(peer_path, msg)
        return msg

    def call(
        self, peer_path: str, payload: bytes,
        timeout_s: float = 10.0,
        capabilities: Optional[List[str]] = None,
        wire_stream: bool = False,
    ) -> Optional[IPCMessage]:
        """Synchronous CALL/REPLY (NPS-003 §3.3): send the CALL, then
        wait for the correlated REPLY. A REPLY is accepted only when
        its ``reply_to`` matches this call — the correlation is the
        client-side trust anchor.

        When the Rust serving-loop crate is present, the whole round
        trip runs through its client half (ADR-0021): ``sendto`` +
        ``poll`` + ``recvmsg`` + the correlation inside the Rust
        process, one FFI call per CALL instead of the floor's per-call
        encode + send + receive loop + decode. The floor loop is the
        fallback (byte-identical semantics, so a caller cannot tell
        which half served the call).

        ``wire_stream=True`` selects the ADR-0024 wire-level streaming
        path: a payload larger than the single-datagram budget is sent
        as a sequence of STREAM_CHUNK messages (the receiver
        reassembles it into the CALL before dispatch), and a large
        reply is reassembled from its STREAM_CHUNK pieces. This runs
        on the FLOOR path by design — the Rust client half is
        single-round-trip and cannot pipeline chunks (the ADR's
        wire-level client streaming is the documented follow-on).
        Calls at or below the budget are byte-identical either way."""
        msg = IPCMessage(
            message_type=IPCMessageType.CALL,
            sender_id=self.container_id,
            payload=payload,
            capabilities=capabilities or [],
        )
        if wire_stream:
            # Wire-level streaming (ADR-0024 follow-on): the FLOOR path
            # with STREAM_CHUNK awareness in BOTH directions — a
            # request that exceeds the single-datagram budget is
            # chunked on send (``_call_wire_stream``); a small request
            # that gets a large reply is reassembled by
            # ``_await_reply``. The Rust client half is single-round-
            # trip and cannot see the chunks, so wire_stream always
            # bypasses it (the ADR's client-half streaming is the
            # documented follow-on).
            if len(payload) > _DATAGRAM_PAYLOAD_BUDGET:
                return self._call_wire_stream(peer_path, msg, timeout_s)
            self._send_message(peer_path, msg)
            return self._await_reply(msg, timeout_s)
        wire = msg.to_wire()
        # The Rust client half (ADR-0021), when the crate is present:
        # one FFI call for the whole round trip. A timeout returns None
        # (the floor's semantics); the floor loop below runs ONLY when
        # the crate is absent (a timeout must not re-send the CALL —
        # that would duplicate it).
        if self.endpoint._sock is not None:
            try:
                reply_wire = ipc_loop.client_call(
                    self.endpoint._sock.fileno(),
                    peer_path,
                    wire,
                    int(timeout_s * 1000),
                )
            except ipc_loop.BackendUnavailable:
                pass  # no crate — use the Python floor loop below
            else:
                if reply_wire is None:
                    return None
                try:
                    reply = IPCMessage.from_wire(reply_wire)
                except (ValueError, KeyError, TypeError) as e:
                    # The Rust half correlated by reply_to; a wire it
                    # accepted but the full codec rejects is a peer
                    # anomaly — drop, like the floor drops malformed.
                    logger.warning(
                        "ipc: Rust client half returned a malformed "
                        "reply (%s); dropping", e)
                    return None
                if (reply.message_type == IPCMessageType.REPLY
                        and reply.reply_to == msg.message_id):
                    return reply
                logger.warning(
                    "ipc: Rust client half returned an uncorrelated "
                    "reply; dropping")
                return None
        # The Python floor loop (the fallback when the crate is
        # absent): send, then wait for the correlated REPLY.
        self._send_message(peer_path, msg)
        return self._await_reply(msg, timeout_s)

    def _await_reply(
        self, msg: IPCMessage, timeout_s: float,
    ) -> Optional[IPCMessage]:
        """The floor's correlated-reply loop: wait for the REPLY to
        ``msg`` (single REPLY, or a reassembled STREAM_CHUNK reply
        stream keyed by the same ``reply_to`` — ADR-0024). Returns the
        reply (a synthesized REPLY with the reassembled payload for
        chunked replies) or None on timeout."""
        deadline = time.time() + timeout_s
        # stream_id -> reassembly slot for chunked replies.
        reply_streams: Dict[bytes, Dict] = {}
        while time.time() < deadline:
            reply = self.receive(timeout=max(0.05, deadline - time.time()))
            if reply is None:
                continue
            if reply.message_type == IPCMessageType.REPLY:
                if reply.reply_to == msg.message_id:
                    return reply
                logger.warning(
                    "ipc: dropping REPLY for unknown call %s",
                    reply.reply_to[:8] if reply.reply_to else None,
                )
                continue
            if reply.message_type == IPCMessageType.STREAM_CHUNK:
                # A chunk of a chunked REPLY to this call (ADR-0024).
                if reply.reply_to != msg.message_id:
                    continue  # a different call's stream — drop
                env = _decode_stream_chunk(reply.payload)
                if env is None:
                    continue  # malformed chunk — drop
                stream_id, _call_id, index, count, payload = env
                slot = reply_streams.get(stream_id)
                if slot is None:
                    if len(reply_streams) >= STREAM_MAX_STREAMS:
                        continue  # too many concurrent reply streams
                    slot = {"count": count, "chunks": {}}
                    reply_streams[stream_id] = slot
                if slot["count"] != count or index in slot["chunks"]:
                    continue  # inconsistent/duplicate — drop
                slot["chunks"][index] = payload
                if len(slot["chunks"]) == slot["count"]:
                    body = b"".join(
                        slot["chunks"][i] for i in range(slot["count"])
                    )
                    return IPCMessage(
                        message_type=IPCMessageType.REPLY,
                        payload=body,
                        reply_to=msg.message_id,
                    )
                continue
        return None

    def _call_wire_stream(
        self, peer_path: str, msg: IPCMessage, timeout_s: float,
    ) -> Optional[IPCMessage]:
        """Send ``msg`` as a wire-level STREAM_CHUNK sequence
        (ADR-0024 follow-on): split the payload into ≤32 KiB chunks,
        pipeline every chunk back-to-back (the receiver reassembles
        and dispatches ONE CALL), then await the single correlated
        reply (which may itself be a chunked stream)."""
        stream_id = os.urandom(_STREAM_ID_BYTES)
        chunks = [
            msg.payload[i:i + STREAM_CHUNK_BYTES]
            for i in range(0, len(msg.payload), STREAM_CHUNK_BYTES)
        ]
        if len(chunks) > STREAM_MAX_CHUNKS:
            # Fail fast instead of pipelining a stream the receiver is
            # bound to drop: the reassembly window is 512 chunks
            # (STREAM_MAX_CHUNKS) and a longer stream is rejected
            # chunk-by-chunk as malformed — an honest immediate refusal
            # beats a call that silently times out after the sender has
            # spent seconds pipelining chunks nobody will accept.
            logger.warning(
                "ipc: refusing to wire-stream %d chunks (> %d) for "
                "call %s — payload exceeds the reassembly window",
                len(chunks), STREAM_MAX_CHUNKS, msg.message_id[:8],
            )
            return None
        for i, chunk in enumerate(chunks):
            env = _encode_stream_chunk(
                stream_id, msg.message_id, i, len(chunks), chunk)
            chunk_msg = IPCMessage(
                message_type=IPCMessageType.STREAM_CHUNK,
                sender_id=self.container_id,
                payload=env,
                reply_to=msg.message_id,
            )
            self._send_message(peer_path, chunk_msg)
        return self._await_reply(msg, timeout_s)

    def call_stream_write(
        self, peer_path: str, chunk_payloads: List[bytes],
        timeout_s: float = 10.0, capabilities: Optional[List[str]] = None,
    ) -> Optional[IPCMessage]:
        """Pipelined streaming WRITE (ADR-0024 first increment): send
        every chunk CALL back-to-back WITHOUT waiting, then await the
        single reply correlated to the LAST chunk.

        The storage service answers intermediate chunks with no reply
        and replies once on the final chunk (the correlated write
        result), so the round trip collapses from one-per-chunk to one
        per logical write. This is the FLOOR path by design — the Rust
        client half is single-round-trip and would wait (and time out)
        for replies the service never sends to intermediate chunks.
        The wire-level streaming of the Rust client half is the
        documented follow-on.

        Returns the reply message (or None on timeout). The caller
        built the chunk payloads (each an ordinary ``volume_write``
        CALL with the stream envelope) and knows how to interpret the
        final reply."""
        msgs = [
            IPCMessage(
                message_type=IPCMessageType.CALL,
                sender_id=self.container_id,
                payload=payload,
                capabilities=capabilities or [],
            )
            for payload in chunk_payloads
        ]
        if not msgs:
            return None
        for m in msgs:
            self._send_message(peer_path, m)
        last_id = msgs[-1].message_id
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            reply = self.receive(timeout=max(0.05, deadline - time.time()))
            if reply is None:
                continue
            if (reply.message_type == IPCMessageType.REPLY
                    and reply.reply_to == last_id):
                return reply
            # Replies to intermediate chunks never arrive (the service
            # replies only on the final chunk); anything else is
            # unrelated — drop and keep waiting.
        return None

    def call_stream_reply(
        self, peer_path: str, payload: bytes,
        timeout_s: float = 10.0, capabilities: Optional[List[str]] = None,
    ) -> Optional[List[bytes]]:
        """Streamed READ (ADR-0024 first increment): send ONE CALL
        (``stream=True`` in the service payload), then collect the
        correlated REPLYs until ``stream_count`` pieces have arrived.

        Each REPLY's JSON carries ``stream_index``/``stream_count`` and
        ≤32 KiB of data; the pieces are returned ORDERED by index (the
        caller decodes each). A single non-stream reply (an old peer
        or a plain error) is returned as a one-element list so the
        caller can surface it. FLOOR path by design (see
        ``call_stream_write``); None on timeout or a service error
        reply."""
        msg = IPCMessage(
            message_type=IPCMessageType.CALL,
            sender_id=self.container_id,
            payload=payload,
            capabilities=capabilities or [],
        )
        self._send_message(peer_path, msg)
        deadline = time.time() + timeout_s
        pieces: Dict[int, bytes] = {}
        count: Optional[int] = None
        while time.time() < deadline:
            reply = self.receive(timeout=max(0.05, deadline - time.time()))
            if reply is None:
                continue
            if (reply.message_type != IPCMessageType.REPLY
                    or reply.reply_to != msg.message_id):
                continue  # unrelated datagram — drop
            try:
                body = json.loads(reply.payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue  # malformed reply — drop, keep waiting
            if not body.get("ok"):
                # A mid-stream failure fails the read; the caller
                # surfaces the error from the piece it gets.
                return [reply.payload]
            idx = body.get("stream_index")
            n = body.get("stream_count")
            if not isinstance(idx, int) or not isinstance(n, int):
                return [reply.payload]  # plain single reply
            pieces[idx] = reply.payload
            count = n
            if len(pieces) == count:
                return [pieces[i] for i in range(count)]
        return None

    def receive(self, timeout: Optional[float] = None) -> Optional[IPCMessage]:
        """Return the next inbound message (None on timeout)."""
        got = self.endpoint.receive(timeout)
        if got is None:
            return None
        payload, _pid, _uid, _gid, _addr = got
        try:
            return IPCMessage.from_wire(payload)
        except (ValueError, KeyError, TypeError) as e:
            logger.warning("ipc: client dropped malformed datagram (%s)", e)
            return None


__all__ = [
    "IPCTransportError",
    "UnixDatagramEndpoint",
    "IPCDatagramServer",
    "IPCClient",
]
