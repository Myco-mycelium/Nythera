#!/usr/bin/env python3
"""Selectable shared-memory IPC channel (IPC-001 §7).

The shared-memory transport (`ipc/shm_transport.py`) is a raw SPSC byte
pipe. This module is the **selection + contract layer** that IPC-001's
accepted Option A calls for:

- a channel carries the SAME wire bytes as the datagram path
  (``IPCMessage.to_wire()`` / ``from_wire``) — codec-identical, so every
  capability/permission check that inspects a message works unchanged;
- selection is **explicit and never silent**: a caller that asks for SHM
  but cannot have it (unavailable host, or a peer not authenticated over
  the datagram path) gets the datagram transport back with
  ``TransportSelection.fell_back`` set and a reason;
- the channel name is an **unguessable token** (``new_channel_token()``),
  so a same-namespace process cannot attach by guessing the segment name
  — the identity binding SHM needs, since (unlike the datagram path)
  there are no per-message kernel credentials.

Scope note (honest): this is the selectable channel + its safety
contract. Wiring the daemon to *establish* a channel per authenticated
client (server ``create`` + advertise the token over the authenticated
datagram path; client ``attach``) is the follow-on increment; the
transport's own wait strategy was fixed under IPC-001 §7 (§38 closes the
<100 µs gate).

References:
- IPC-001: IPC Wire-Latency Gate (design note, Accepted)
- NPS-003 §3–4: IPC primitives and endpoint model
- NPS-017 §4.3: IPC semantics
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from ipc.core import IPCMessage
from ipc.shm_transport import ShmTransport, is_shm_available

__all__ = [
    "ShmChannelError",
    "TransportSelection",
    "select_transport",
    "new_channel_token",
    "ShmChannel",
    "offer_channel",
    "accept_channel",
    "ShmChannelRegistry",
    "with_shm_channel_offers",
    "SHM_OPEN_OP",
    "FALLBACK_SENTINEL",
    "MIN_CHANNEL_TOKEN_LEN",
]

# Channel ids must be unguessable tokens, not short names: the segment name
# is the only access control below the kernel's 0600.
MIN_CHANNEL_TOKEN_LEN = 16

# The wire op a client CALLs to request a channel, and the reply a server
# sends when it will NOT offer one (so the client falls back explicitly).
SHM_OPEN_OP = b"shm-open"
FALLBACK_SENTINEL = b"datagram"


class ShmChannelError(RuntimeError):
    """The shared-memory channel could not be established."""


class TransportSelection:
    """The result of a transport selection — never silent.

    ``fell_back`` is True when SHM was requested but the datagram transport
    must be used instead; ``reason`` always explains the choice so a caller
    can surface it (the fallback is observable, never hidden).
    """

    __slots__ = ("kind", "fell_back", "reason")

    def __init__(self, kind: str, fell_back: bool, reason: str):
        assert kind in ("datagram", "shm")
        assert reason, "a selection reason is mandatory (never silent)"
        self.kind = kind
        self.fell_back = fell_back
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (f"TransportSelection(kind={self.kind!r}, "
                f"fell_back={self.fell_back!r}, reason={self.reason!r})")


def new_channel_token() -> str:
    """Return a fresh, unguessable channel token."""
    return uuid.uuid4().hex


def select_transport(
    preferred: str,
    *,
    shm_available: Optional[bool] = None,
    peer_authenticated: bool = False,
) -> TransportSelection:
    """Choose a transport for one endpoint pair. Explicit, fail-closed.

    Rules (IPC-001 §4):
    - ``preferred="datagram"`` -> datagram, no fallback.
    - ``preferred="shm"`` is granted ONLY when POSIX shm is available AND
      the peer was authenticated over the datagram path (identity is bound
      at channel-establishment, since SHM carries no per-message
      credentials). Otherwise it falls back to datagram with a reason.
    """
    if preferred not in ("datagram", "shm"):
        raise ValueError(f"unknown transport preference: {preferred!r}")
    if preferred == "datagram":
        return TransportSelection("datagram", False, "explicit datagram request")
    available = is_shm_available() if shm_available is None else shm_available
    if not available:
        return TransportSelection(
            "datagram", True, "shared memory unavailable on this host")
    if not peer_authenticated:
        return TransportSelection(
            "datagram", True,
            "peer not authenticated over the datagram path (identity is "
            "bound at channel-establishment)")
    return TransportSelection("shm", False, "shared memory selected")


class ShmChannel:
    """A selectable SHM endpoint carrying the datagram path's wire bytes.

    Create one side with :meth:`create` (the server) and attach the other
    with :meth:`attach` (the client), using the SAME token. Messages are
    ``IPCMessage`` objects; they travel as ``to_wire()`` bytes, identical
    to the datagram transport.
    """

    def __init__(self, transport: ShmTransport, peer_id: str,
                 channel_id: Optional[str] = None):
        self._transport = transport
        self.peer_id = peer_id
        self.channel_id = channel_id

    @staticmethod
    def _require_token(channel_id: str) -> None:
        if not channel_id or len(channel_id) < MIN_CHANNEL_TOKEN_LEN:
            raise ShmChannelError(
                f"channel id must be an unguessable token of at least "
                f"{MIN_CHANNEL_TOKEN_LEN} chars (got {len(channel_id or '')})")

    @classmethod
    def create(cls, channel_id: str, peer_id: str) -> "ShmChannel":
        """Server side: create the channel's segments."""
        cls._require_token(channel_id)
        transport = ShmTransport(channel_id)
        if not transport.create():
            raise ShmChannelError(f"failed to create shm channel {channel_id!r}")
        return cls(transport, peer_id, channel_id)

    @classmethod
    def attach(cls, channel_id: str, peer_id: str) -> "ShmChannel":
        """Client side: attach to an existing channel's segments."""
        cls._require_token(channel_id)
        transport = ShmTransport(channel_id)
        if not transport.attach():
            raise ShmChannelError(f"failed to attach to shm channel {channel_id!r}")
        return cls(transport, peer_id, channel_id)

    def send_message(self, msg: IPCMessage) -> bool:
        """Send an ``IPCMessage`` (codec-identical to the datagram path)."""
        return self._transport.send(msg.to_wire())

    def recv_message(self, timeout_s: float = 5.0) -> Optional[IPCMessage]:
        """Receive an ``IPCMessage``, or None on timeout."""
        raw = self._transport.recv(timeout_s=timeout_s)
        if raw is None:
            return None
        return IPCMessage.from_wire(raw)

    def close(self) -> None:
        self._transport.close()

    @property
    def available(self) -> bool:
        return self._transport.available


# --- negotiation over the authenticated datagram path --------------------

def offer_channel(
    server,
    sender_path: str,
    call_id: str,
    peer_id: str,
    *,
    channel_id: Optional[str] = None,
) -> "ShmChannel":
    """Server side of the handshake: create a channel and reply its token.

    The token travels over the EXISTING datagram path — the only channel
    that carries kernel-attached credentials — so the client that receives
    it is the authenticated peer. ``server`` is an ``IPCDatagramServer``
    (or anything with the same ``reply`` method).
    """
    token = channel_id or new_channel_token()
    channel = ShmChannel.create(token, peer_id)
    if not server.reply(sender_path, call_id, token.encode()):
        channel.close()
        raise ShmChannelError("failed to deliver the shm channel token")
    return channel


def accept_channel(
    client,
    server_path: str,
    peer_id: str,
    *,
    op: bytes = SHM_OPEN_OP,
    timeout_s: float = 5.0,
) -> "ShmChannel":
    """Client side of the handshake: CALL for a token, then attach.

    ``client`` is an ``IPCClient`` (or anything with a compatible
    ``call``). Raises ``ShmChannelError`` if no valid token comes back
    (including the server's explicit ``FALLBACK_SENTINEL`` decline) — the
    caller must then fall back to datagram, never silently.
    """
    reply = client.call(server_path, op, timeout_s=timeout_s)
    if reply is None:
        raise ShmChannelError("no reply to the shm channel request")
    payload = getattr(reply, "payload", None)
    if payload == FALLBACK_SENTINEL:
        raise ShmChannelError(
            "server declined shared memory; use the datagram transport")
    try:
        token = payload.decode("ascii")
    except (AttributeError, UnicodeDecodeError) as exc:
        raise ShmChannelError(f"malformed shm channel token: {exc}") from exc
    return ShmChannel.attach(token, peer_id)


# --- server-side lifecycle -------------------------------------------------

class ShmChannelRegistry:
    """Per-authenticated-peer SHM channels, with a bounded lifecycle.

    Policy (IPC-001 §8):
    - one channel per authenticated peer (a repeat request re-advertises
      the existing channel's token);
    - at most ``max_channels`` live channels — beyond that, requests get
      the explicit ``FALLBACK_SENTINEL`` (the client uses datagram), never
      a silent drop;
    - ``release(peer_id)`` when a peer exits, ``close_all()`` at shutdown.
    """

    def __init__(self, server, *, max_channels: int = 32):
        self._server = server
        self._max_channels = max_channels
        self._channels: Dict[str, ShmChannel] = {}
        self._lock = threading.Lock()

    def handle_open(self, msg, sender: str, sender_path: str) -> Optional[ShmChannel]:
        """Serve an ``SHM_OPEN_OP`` call: offer a channel or decline.

        Returns the channel (new or reused) or None when declining. The
        reply is always sent, so the client's choice is never silent.
        """
        with self._lock:
            existing = self._channels.get(sender)
            if existing is not None:
                self._server.reply(sender_path, msg.message_id,
                                   str(existing.channel_id).encode())
                return existing
            if len(self._channels) >= self._max_channels:
                self._server.reply(sender_path, msg.message_id, FALLBACK_SENTINEL)
                return None
            if select_transport("shm", peer_authenticated=True).kind != "shm":
                self._server.reply(sender_path, msg.message_id, FALLBACK_SENTINEL)
                return None
            token = new_channel_token()
            channel = ShmChannel.create(token, sender)
            if not self._server.reply(sender_path, msg.message_id, token.encode()):
                channel.close()
                return None
            self._channels[sender] = channel
            return channel

    def release(self, peer_id: str) -> bool:
        """Drop a peer's channel (call when the peer exits)."""
        with self._lock:
            channel = self._channels.pop(peer_id, None)
        if channel is not None:
            channel.close()
            return True
        return False

    def close_all(self) -> None:
        with self._lock:
            channels, self._channels = list(self._channels.values()), {}
        for channel in channels:
            channel.close()

    def __len__(self) -> int:
        with self._lock:
            return len(self._channels)

    @property
    def peer_ids(self):
        with self._lock:
            return list(self._channels.keys())


def with_shm_channel_offers(inner_on_call: Callable, server, *,
                            max_channels: int = 32) -> Callable:
    """Wrap a server's ``on_call`` so SHM channel offers are served.

    The daemon opts in by setting ``server.on_call =
    with_shm_channel_offers(existing_handler, server)``; ``SHM_OPEN_OP``
    messages are handled here and every other message passes through to
    ``inner_on_call`` unchanged. The returned handler exposes
    ``.registry`` for lifecycle control (``release``/``close_all``).
    """
    registry = ShmChannelRegistry(server, max_channels=max_channels)

    def handler(msg, sender, sender_path):
        if getattr(msg, "payload", None) == SHM_OPEN_OP:
            registry.handle_open(msg, sender, sender_path)
            return
        return inner_on_call(msg, sender, sender_path)

    handler.registry = registry
    return handler
