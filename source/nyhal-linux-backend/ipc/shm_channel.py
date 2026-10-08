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

import uuid
from dataclasses import dataclass
from typing import Optional

from ipc.core import IPCMessage
from ipc.shm_transport import ShmTransport, is_shm_available

__all__ = [
    "ShmChannelError",
    "TransportSelection",
    "select_transport",
    "new_channel_token",
    "ShmChannel",
    "MIN_CHANNEL_TOKEN_LEN",
]

# Channel ids must be unguessable tokens, not short names: the segment name
# is the only access control below the kernel's 0600.
MIN_CHANNEL_TOKEN_LEN = 16


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

    def __init__(self, transport: ShmTransport, peer_id: str):
        self._transport = transport
        self.peer_id = peer_id

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
        return cls(transport, peer_id)

    @classmethod
    def attach(cls, channel_id: str, peer_id: str) -> "ShmChannel":
        """Client side: attach to an existing channel's segments."""
        cls._require_token(channel_id)
        transport = ShmTransport(channel_id)
        if not transport.attach():
            raise ShmChannelError(f"failed to attach to shm channel {channel_id!r}")
        return cls(transport, peer_id)

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
