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
A datagram sender is authenticated by the kernel's
``SCM_CREDENTIALS`` ancillary data, not by anything on the wire. The
receiver sets ``SO_PASSCRED`` so the kernel attaches the real sender's
``(pid, uid, gid)`` to every inbound datagram; the backend maps the
pid to a container, and a wire ``sender_id`` that does not match the
authenticated container is rejected as a forgery. Unprivileged senders
cannot forge credentials — the kernel refuses (EPERM) a ``ucred`` that
does not match the caller (verified on Linux 6.x; ``SO_PEERCRED`` does
NOT work on datagram sockets — it returns ``(0, -1, -1)`` — so
``SCM_CREDENTIALS`` is the mechanism here).

Capability enforcement
----------------------
Inbound datagrams are checked before delivery: the authenticated
sender must hold ``CAP_IPC_SEND`` (control plane, NPS-017 §4.2) and
the destination endpoint enqueues through its token bucket (ADR-0009).
The receive-side ``CAP_IPC_RECEIVE`` check stays on the consumer's
``IPCManager.receive()``, exactly as in the in-process path.

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
- ADR-0009: per-container token-bucket rate limiting
"""

import logging
import os
import socket
import struct
import time
from typing import Callable, Dict, List, Optional, Tuple

from .core import IPCManager, IPCMessage, IPCMessageType

logger = logging.getLogger(__name__)

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


def _make_credentials() -> bytes:
    """The caller's real ``ucred`` for SCM_CREDENTIALS. The kernel
    verifies it matches the caller (an unprivileged sender cannot forge
    another identity — the send is refused with EPERM)."""
    return _UCRED.pack(os.getpid(), os.getuid(), os.getgid())


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

    The server side of an endpoint path sets ``SO_PASSCRED`` so every
    inbound datagram carries the kernel-attached sender credentials;
    the caller side attaches its own ``SCM_CREDENTIALS`` on send.
    """

    def __init__(self, path: str):
        if not path or len(path) > _MAX_SOCKET_PATH:
            raise IPCTransportError(
                f"socket path must be 1..{_MAX_SOCKET_PATH} bytes "
                f"(got {len(path)})"
            )
        self.path = path
        self._sock: Optional[socket.socket] = None

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
        """Send ``payload`` to ``peer_path`` (default: ``self.path``),
        attaching the sender's real SCM_CREDENTIALS."""
        if self._sock is None:
            raise IPCTransportError(f"endpoint {self.path} is not bound")
        target = peer_path or self.path
        try:
            self._sock.sendmsg(
                [payload],
                [(_SOL_SOCKET, _SCM_CREDENTIALS, _make_credentials())],
                0,
                target,
            )
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
            self._sock.settimeout(timeout)
            data, ancdata, _flags, addr = self._sock.recvmsg(
                64 * 1024, _CMSG_SPACE
            )
        except socket.timeout:
            return None
        except OSError as e:
            raise IPCTransportError(f"receive on {self.path} failed: {e}") from e
        creds = _peer_credentials(ancdata)
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
    resolves the authenticated sender pid to a container, rejects
    forged ``sender_id`` values and senders lacking ``CAP_IPC_SEND``,
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
    ):
        self.manager = manager
        self.endpoint_id = endpoint_id
        self.endpoint = UnixDatagramEndpoint(path)
        # pid -> container_id for authenticated senders (the backend
        # maintains this; a resolver callable is accepted too).
        self.pid_registry = pid_registry
        self.capability_manager = capability_manager
        self.on_call = on_call

    def bind(self) -> "IPCDatagramServer":
        self.endpoint.bind()
        return self

    def close(self) -> None:
        self.endpoint.close()

    def _resolve_sender(self, pid: int) -> Optional[str]:
        if self.pid_registry is None:
            return None
        if callable(self.pid_registry):
            return self.pid_registry(pid)
        return self.pid_registry.get(pid)

    def _authorized(self, msg: IPCMessage, pid: int) -> Optional[str]:
        """Return the authenticated sender container id, or None when
        the datagram must be dropped (unknown sender, a wire
        ``sender_id`` that does not match the authenticated container —
        including an empty one, since the transport is the attribution
        boundary — or a sender without CAP_IPC_SEND)."""
        sender = self._resolve_sender(pid)
        if sender is None:
            logger.warning(
                "ipc: dropping datagram from unknown pid %d (not in the "
                "container registry)", pid,
            )
            return None
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

    def reply(self, sender_path: str, call_id: str, payload: bytes) -> bool:
        """Send a REPLY to ``sender_path`` for call ``call_id``."""
        reply = IPCMessage(
            message_type=IPCMessageType.REPLY,
            payload=payload,
            reply_to=call_id,
        )
        self.endpoint.send(reply.to_wire(), peer_path=sender_path)
        logger.info("ipc: replied to call %s", call_id[:8])
        return True

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
        sender = self._authorized(msg, pid)
        if sender is None:
            return None
        if msg.message_type == IPCMessageType.CALL:
            if self.on_call is not None:
                self.on_call(msg, sender, sender_path)
            return msg
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
        """Blocking dispatch loop until ``stop_event`` is set. A
        transient transport error (e.g. an EINTR-wrapped OSError) is
        logged and the loop continues — one bad datagram must not kill
        the serving thread."""
        while not stop_event.is_set():
            try:
                self.serve_once(timeout=poll_s)
            except IPCTransportError as e:
                logger.warning("ipc: transport error, continuing: %s", e)


class IPCClient:
    """A container's transport endpoint (the caller side).

    Binds its own socket path (so the server can reply to CALLs at the
    path carried in ``metadata['reply_path']``) and speaks to peer
    endpoint paths with the wire codec.
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
    ) -> Optional[IPCMessage]:
        """Synchronous CALL/REPLY (NPS-003 §3.3): send the CALL, then
        wait for the correlated REPLY. A REPLY is accepted only when
        its ``reply_to`` matches this call — the correlation is the
        client-side trust anchor."""
        msg = IPCMessage(
            message_type=IPCMessageType.CALL,
            sender_id=self.container_id,
            payload=payload,
            capabilities=capabilities or [],
        )
        self._send_message(peer_path, msg)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            reply = self.receive(timeout=max(0.05, deadline - time.time()))
            if reply is not None and reply.message_type == IPCMessageType.REPLY:
                if reply.reply_to == msg.message_id:
                    return reply
                logger.warning(
                    "ipc: dropping REPLY for unknown call %s",
                    reply.reply_to[:8] if reply.reply_to else None,
                )
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
