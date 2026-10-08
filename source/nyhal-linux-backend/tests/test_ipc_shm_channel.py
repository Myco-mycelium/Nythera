#!/usr/bin/env python3
"""IPC-001 §7 contract pins — the selectable shared-memory channel.

Pins the safety contract the accepted Option A ruling requires:
transport selection is explicit and NEVER silent (fallback is observable),
SHM requires an unguessable channel token, and a message over SHM is the
same wire bytes as the datagram path (codec-identical).
"""

import pytest

from ipc.core import IPCMessage, IPCMessageType
from ipc.shm_channel import (
    FALLBACK_SENTINEL,
    MIN_CHANNEL_TOKEN_LEN,
    SHM_OPEN_OP,
    ShmChannel,
    ShmChannelError,
    ShmChannelRegistry,
    accept_channel,
    new_channel_token,
    offer_channel,
    select_transport,
    with_shm_channel_offers,
)
from ipc.shm_transport import is_shm_available

requires_shm = pytest.mark.skipif(
    not is_shm_available(), reason="POSIX shared memory unavailable")


class _FakeServer:
    """Minimal IPCDatagramServer stand-in: records replies."""

    def __init__(self):
        self.replies = []

    def reply(self, sender_path, call_id, payload):
        self.replies.append((sender_path, call_id, payload))
        return True


def _open_msg(call_id="c1"):
    from types import SimpleNamespace
    return SimpleNamespace(payload=SHM_OPEN_OP, message_id=call_id)


# --- selection policy (never silent) -------------------------------------

def test_explicit_datagram_returns_datagram_without_fallback():
    sel = select_transport("datagram")
    assert sel.kind == "datagram"
    assert sel.fell_back is False
    assert sel.reason


def test_shm_requested_but_unavailable_falls_back_observably():
    sel = select_transport("shm", shm_available=False, peer_authenticated=True)
    assert sel.kind == "datagram"
    assert sel.fell_back is True
    assert "unavailable" in sel.reason


def test_shm_requested_without_authenticated_peer_falls_back_observably():
    sel = select_transport("shm", shm_available=True, peer_authenticated=False)
    assert sel.kind == "datagram"
    assert sel.fell_back is True
    assert "authenticated" in sel.reason


def test_shm_granted_only_when_available_and_authenticated():
    sel = select_transport("shm", shm_available=True, peer_authenticated=True)
    assert sel.kind == "shm"
    assert sel.fell_back is False


def test_unknown_preference_is_refused():
    with pytest.raises(ValueError):
        select_transport("carrier-pigeon")


def test_every_selection_carries_a_reason():
    for pref in ("datagram", "shm"):
        for avail in (True, False):
            for auth in (True, False):
                sel = select_transport(
                    pref, shm_available=avail, peer_authenticated=auth)
                assert sel.reason, (pref, avail, auth)


# --- unguessable channel token -------------------------------------------

def test_channel_token_is_long_and_random():
    a, b = new_channel_token(), new_channel_token()
    assert len(a) >= MIN_CHANNEL_TOKEN_LEN
    assert a != b


def test_short_or_empty_channel_ids_are_refused():
    for bad in ("", "abc", "short"):
        with pytest.raises(ShmChannelError):
            ShmChannel.create(bad, "peer")


# --- codec-identical bytes + real round trip -----------------------------

@requires_shm
def test_message_over_shm_is_the_same_wire_bytes():
    token = new_channel_token()
    server = ShmChannel.create(token, "svc")
    try:
        msg = IPCMessage(
            message_type=IPCMessageType.CALL,
            sender_id="svc", payload=b"hello-shm", capabilities=["CAP_IPC_SEND"])
        assert msg.to_wire()  # the datagram path's framing, non-empty
        assert server.send_message(msg) is True
    finally:
        server.close()


@requires_shm
def test_channel_is_negotiated_over_the_authenticated_datagram_path():
    """The token must travel over the datagram path (the only one carrying
    kernel credentials); the client then attaches and talks over SHM."""
    import os
    import tempfile
    import threading
    from ipc.core import IPCManager, TokenBucket
    from ipc.transport import IPCClient, IPCDatagramServer

    base = tempfile.mkdtemp(prefix="nyrqis-shm-nego-")
    svc_path = os.path.join(base, "svc.sock")
    cli_path = os.path.join(base, "cli.sock")

    mgr = IPCManager()
    svc = mgr.create_endpoint("svc", "ep-svc")
    svc.rate_limit = TokenBucket(
        bucket_size=1_000_000, tokens_per_second=1_000_000.0)
    server = IPCDatagramServer(mgr, "ep-svc", svc_path)
    server.bind()
    server.pid_registry = {os.getpid(): "cli"}  # the in-process client is us
    offered = []
    stop = threading.Event()

    def handler(msg, sender, sender_path):
        offered.append(
            offer_channel(server, sender_path, msg.message_id, "cli"))

    server.on_call = handler
    threading.Thread(target=server.serve, args=(stop,), daemon=True).start()

    client = IPCClient("cli", cli_path).bind()
    try:
        channel = accept_channel(client, svc_path, "svc")
        try:
            assert channel.available
            msg = IPCMessage(
                message_type=IPCMessageType.SEND,
                sender_id="cli", payload=b"over-shm")
            assert channel.send_message(msg) is True
            got = offered[0].recv_message(timeout_s=5.0)
            assert got is not None
            assert got.payload == b"over-shm"
        finally:
            channel.close()
            for c in offered:
                c.close()
    finally:
        client.close()
        stop.set()
        server.close()


@requires_shm
def test_accept_channel_refuses_when_no_token_comes_back():
    class _NoReplyClient:
        def call(self, *a, **k):
            return None

    with pytest.raises(ShmChannelError):
        accept_channel(_NoReplyClient(), "/nonexistent", "svc", timeout_s=0.01)


@requires_shm
def test_registry_reuses_one_channel_per_peer():
    server = _FakeServer()
    reg = ShmChannelRegistry(server)
    first = reg.handle_open(_open_msg("c1"), "peer-a", "/p/a")
    again = reg.handle_open(_open_msg("c2"), "peer-a", "/p/a")
    assert first is not None and first is again
    assert len(reg) == 1
    assert server.replies[0][2] == server.replies[1][2]  # same token re-advertised
    reg.close_all()


@requires_shm
def test_registry_declines_observably_beyond_the_limit():
    server = _FakeServer()
    reg = ShmChannelRegistry(server, max_channels=1)
    assert reg.handle_open(_open_msg("c1"), "peer-a", "/p/a") is not None
    declined = reg.handle_open(_open_msg("c2"), "peer-b", "/p/b")
    assert declined is None
    assert server.replies[-1][2] == FALLBACK_SENTINEL  # explicit, never silent
    reg.close_all()


@requires_shm
def test_registry_release_and_close_all():
    server = _FakeServer()
    reg = ShmChannelRegistry(server)
    reg.handle_open(_open_msg(), "peer-a", "/p/a")
    assert len(reg) == 1
    assert reg.release("peer-a") is True
    assert reg.release("peer-a") is False
    assert len(reg) == 0
    reg.handle_open(_open_msg(), "peer-b", "/p/b")
    reg.close_all()
    assert len(reg) == 0


@requires_shm
def test_wrapper_intercepts_opens_and_passes_through_everything_else():
    from types import SimpleNamespace
    server = _FakeServer()
    seen = []

    def inner(msg, sender, sender_path):
        seen.append(msg.payload)
        return "inner"

    handler = with_shm_channel_offers(inner, server)
    handler(_open_msg(), "peer-a", "/p/a")               # intercepted
    assert seen == []
    handler(SimpleNamespace(payload=b"status", message_id="c2"),
            "peer-a", "/p/a")                          # passthrough
    assert seen == [b"status"]
    handler.registry.close_all()


def test_accept_channel_raises_on_the_fallback_sentinel():
    from types import SimpleNamespace

    class _Client:
        def call(self, *a, **k):
            return SimpleNamespace(payload=FALLBACK_SENTINEL)

    with pytest.raises(ShmChannelError) as exc:
        accept_channel(_Client(), "/x", "svc")
    assert "declined" in str(exc.value)


@requires_shm
def test_call_reply_round_trip_over_shm():
    token = new_channel_token()
    server = ShmChannel.create(token, "svc")
    client = ShmChannel.attach(token, "cli")
    try:
        req = IPCMessage(
            message_type=IPCMessageType.CALL,
            sender_id="cli", payload=b"req", capabilities=["CAP_IPC_SEND"])
        assert client.send_message(req) is True
        got = server.recv_message(timeout_s=5.0)
        assert got is not None
        # codec-identical: the decoded message equals the sent one
        assert got.payload == b"req"
        assert got.sender_id == "cli"
        assert got.message_type == IPCMessageType.CALL

        reply = IPCMessage(
            message_type=IPCMessageType.REPLY,
            sender_id="svc", payload=b"resp")
        assert server.send_message(reply) is True
        back = client.recv_message(timeout_s=5.0)
        assert back is not None
        assert back.payload == b"resp"
        assert back.sender_id == "svc"
    finally:
        client.close()
        server.close()
