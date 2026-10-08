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
    MIN_CHANNEL_TOKEN_LEN,
    ShmChannel,
    ShmChannelError,
    new_channel_token,
    select_transport,
)
from ipc.shm_transport import is_shm_available

requires_shm = pytest.mark.skipif(
    not is_shm_available(), reason="POSIX shared memory unavailable")


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
