#!/usr/bin/env python3
"""
dispatch — the Rust serving loop's non-ping dispatch handoff
(ADR-0021 decision point 1).

The loop answers the built-in ``ping`` op itself; every OTHER authorized
CALL is queued and handed to Python as plain data (``IpcdLoop.drain_requests``).
This module is the driver of that handoff: :class:`IpcdLoopDispatcher`
pulls the queued batch, dispatches each request through a
:class:`~ipc.service.ServiceRouter` whose services reply into a
:class:`_LoopReplySink` (a server-shaped object that collects reply
wires instead of sending them), and hands the collected replies back to
the loop (``IpcdLoop.enqueue_replies``), which routes each to the
RECORDED sender address captured at recv — the reply routing never
trusts the wire, exactly the transport's attribution rule.

The batch boundary is paid once per step: the loop crosses into Python
once to drain, the services run entirely in Python (they ARE Python —
ADR-0021 leaves the handlers on the floor), and the replies cross back
once to be sent. Per-message ctypes marshalling happens only inside the
Rust process loop for the datagrams it answers itself.

Faithfulness to the floor (``IPCDatagramServer.serve_once``):

- The loop already performed sender authorization (pid/uid resolution
  + wire ``sender_id`` match) before queuing; the floor additionally
  enforces ``CAP_IPC_SEND`` for container senders before dispatch, so
  the dispatcher mirrors that check (the operator path needs no
  capability) — a sender without the grant is dropped, never
  dispatched.
- Reply wires are built with the SAME codec the floor's ``reply()``
  uses (``IPCMessage(REPLY, payload, reply_to=call_id).to_wire()``),
  so a reply is byte-identical whichever backend served it.
- The router never raises into the driver (it replies ``internal
  error``); the driver's per-request try/except is defensive only.
"""

import logging
from typing import Any, List, Optional

from .core import IPCMessage
from .loop import IpcdLoop
from .service import ServiceRouter
from .transport import DEFAULT_OPERATOR_ID, build_reply_wires

logger = logging.getLogger(__name__)


class _SinkEndpointLabel:
    """The services' ``attach()`` logs ``server.endpoint.path``; the
    sink has no socket path, so it gets a stable label instead."""

    def __init__(self, path: str) -> None:
        self.path = path


class _LoopReplySink:
    """A server-shaped reply sink for loop-dispatched requests.

    Services reply through it exactly as through an
    ``IPCDatagramServer`` (``reply(sender_path, call_id, payload)``),
    but the replies are collected instead of sent: the Rust loop
    recorded each caller's bound address at recv, so the driver hands
    the collected reply wires to ``IpcdLoop.enqueue_replies`` and the
    loop routes them. ``sender_path`` is accepted and ignored — the
    loop's recorded address is authoritative (a sender cannot choose
    where its reply goes).
    """

    def __init__(self, operator_id: str = DEFAULT_OPERATOR_ID) -> None:
        self._replies: List = []  # (call_id, payload)
        self.operator_id = operator_id
        self.endpoint = _SinkEndpointLabel("loop-dispatch")

    def reply(self, sender_path: str, call_id: str, payload: bytes) -> bool:
        self._replies.append((call_id, payload))
        return True

    def drain_replies(self) -> List:
        out, self._replies = self._replies, []
        return out


class IpcdLoopDispatcher:
    """Drives a Rust serving loop INCLUDING the non-ping dispatch
    handoff. ``serve_once`` is the loop's serve-loop step: it polls and
    drains one batch (pings answered by the loop itself), then pulls
    the queued non-ping requests and dispatches them through ``router``
    (services wired to an internal reply sink), enqueuing the replies
    for the loop to send and reaping anything the handlers declined.

    ``capability_manager`` supplies the floor's ``CAP_IPC_SEND`` check
    for container senders; ``operator_id`` is the identity the trusted
    uid fallback resolves to (the operator needs no capability).
    """

    def __init__(
        self,
        loop: IpcdLoop,
        router: ServiceRouter,
        capability_manager: Optional[Any] = None,
        operator_id: str = DEFAULT_OPERATOR_ID,
    ) -> None:
        self._loop = loop
        self._router = router
        self._capability_manager = capability_manager
        self.operator_id = operator_id
        self._sink = _LoopReplySink(operator_id)
        # Wire the router + its services to the sink: services reply
        # into the sink, the driver enqueues the sink's replies.
        router.attach(self._sink)

    def serve_once(self, timeout_ms: int = 100) -> int:
        """One serve-loop step: poll/drain one batch, then dispatch the
        queued non-ping requests. Returns the datagrams the loop
        drained (0 = clean timeout)."""
        processed = self._loop.step(timeout_ms)
        self._dispatch_pending()
        return processed

    def _dispatch_pending(self) -> None:
        wires = self._loop.drain_requests()
        if not wires:
            return
        sink = self._sink
        for wire in wires:
            try:
                msg = IPCMessage.from_wire(wire)
            except (ValueError, KeyError, TypeError) as e:
                # Malformed after the loop's own parse (defensive — the
                # loop's parse already ran): drop, like the floor.
                logger.warning("ipc dispatch: malformed drained request (%s)", e)
                continue
            # The loop authorized the sender (pid/uid + wire match).
            # The floor additionally enforces CAP_IPC_SEND for container
            # senders before dispatch — mirror it here; the operator
            # path needs no capability.
            if (msg.sender_id != self.operator_id
                    and self._capability_manager is not None):
                from backend.capability import Capability
                if not self._capability_manager.validate_operation(
                    msg.sender_id, Capability.CAP_IPC_SEND
                ):
                    continue  # drop, mirroring the floor's _authorized
            try:
                # sender_path is unused by the sink (the loop routes);
                # the authenticated sender is passed as both.
                self._router._on_call(msg, msg.sender_id, msg.sender_id)
            except Exception:  # noqa: BLE001 - the router never raises; defensive
                logger.exception("ipc dispatch: router raised")
        # ADR-0024 wire-level framing: a reply payload that exceeds the
        # single-datagram budget becomes N STREAM_CHUNK wires (the same
        # helper the floor's reply() uses, so a reply is byte-identical
        # whichever backend served it). The loop routes each wire to
        # the RECORDED sender address; STREAM_CHUNK wires do not
        # consume the pending entry (only the final REPLY does) and
        # discard_requests reaps it after the batch.
        replies: List[bytes] = []
        for call_id, payload in sink.drain_replies():
            replies.extend(build_reply_wires(call_id, payload))
        if replies:
            self._loop.enqueue_replies(replies)
        # Reap anything the handlers chose not to answer.
        self._loop.discard_requests()


__all__ = ["IpcdLoopDispatcher"]
