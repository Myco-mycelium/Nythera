#!/usr/bin/env python3
"""
IPC Core Implementation for the Nyrqis Linux Backend

Implements NPS-017 §4.3 (IPC Semantics) and NPS-003 (IPC and Capability Passing).
Provides the four core IPC primitives:
- send: asynchronous message send
- receive: blocking message receive
- call: synchronous request-reply
- notify: lightweight notification

Also implements token-bucket rate limiting per ADR-0009 to prevent resource
exhaustion and denial-of-service attacks.

References:
- NPS-017 §4.3: IPC Semantics
- NPS-003 §3-4: IPC Primitives and Endpoint Model
- NPS-003 §5: Capability Transfer and Attenuation
- ADR-0009: Per-container token-bucket rate limiting for IPC
"""

import enum
import json
import logging
import os
import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable

from . import ipc_codec  # ADR-0020 priority #4 FFI loader (wire codec)

logger = logging.getLogger(__name__)

# The ADR-0009 default-parameter postures. The LIBRARY defaults (used
# whenever code builds an IPCManager/IPCEndpoint without arguments) are
# the conservative, under-review values; the DAEMON (nyrqis_backend,
# systemd unit) already ships the AG-proposed input-class envelope.
# When Architecture Group acceptance lands (ADR-0009 review package
# §6), flip LIBRARY_DEFAULT_* to ACCEPTED_PROPOSED_* — the
# test_default_flip_readiness gate then enforces the alignment so the
# two cannot silently diverge.
LIBRARY_DEFAULT_BUCKET_SIZE = 200
LIBRARY_DEFAULT_TOKENS_PER_SECOND = 500.0
ACCEPTED_PROPOSED_BUCKET_SIZE = 256
ACCEPTED_PROPOSED_TOKENS_PER_SECOND = 2000.0


class IPCMessageType(enum.Enum):
    """IPC message types per NPS-003 §3, plus the ADR-0024 streaming
    chunk type (wire-level framing; ordinal 5)."""
    SEND = "send"  # Asynchronous message
    RECEIVE = "receive"  # Receive acknowledgment
    CALL = "call"  # Synchronous request
    REPLY = "reply"  # Reply to call
    NOTIFY = "notify"  # Lightweight notification
    STREAM_CHUNK = "stream_chunk"  # ADR-0024: one chunk of a streamed CALL/REPLY


# The wire's message_type is the enum's ordinal (NPS-003 §3 order:
# send, receive, call, reply, notify). These tables keep the enum and
# the wire format in lockstep; the codec validates the range.
_TYPE_INDEX = {t: i for i, t in enumerate(IPCMessageType)}
_INDEX_TYPE = {i: t for i, t in enumerate(IPCMessageType)}


@dataclass
class IPCMessage:
    """Represents an IPC message per NPS-003 §3.
    
    Messages can carry:
    - Payload data (bytes)
    - Capabilities (which may be attenuated per NPS-003 §5)
    - Metadata (sender, receiver, type, etc.)
    """
    # message_id is opaque on the wire (``u32-len id`` in the codec)
    # and excluded from the conformance differential (per-message by
    # design), so its generator is a free choice. NPS-003 §3.2's
    # "unguessable identifiers" rule covers *endpoints*, not message
    # ids — but keeping the id CSPRNG-random preserves that property
    # anyway. ``os.urandom(6).hex()`` (48 bits) is unguessable and
    # collision-safe for correlation, and it avoids the ~6 µs per-call
    # ``uuid4`` cost that dominates the client hot path (ADR-0021).
    message_id: str = field(default_factory=lambda: os.urandom(6).hex())
    message_type: IPCMessageType = IPCMessageType.SEND
    sender_id: str = ""
    receiver_id: str = ""
    payload: bytes = b""
    capabilities: List[str] = field(default_factory=list)  # Capability names being transferred
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    reply_to: Optional[str] = None  # For REPLY messages
    
    def __repr__(self) -> str:
        return (
            f"IPCMessage(id={self.message_id[:8]}, type={self.message_type.value}, "
            f"sender={self.sender_id}, receiver={self.receiver_id}, "
            f"payload_size={len(self.payload)}, caps={len(self.capabilities)})"
        )

    def to_wire(self) -> bytes:
        """Serialize this message to the canonical wire format (the
        transport boundary extracted in ADR-0020 priority #4). Routed
        through ``ipc_codec``: the Rust codec when loaded, the
        byte-identical ``struct`` floor otherwise. ``metadata`` is
        serialized with ``json.dumps(sort_keys=True)`` so identical
        dicts produce identical wire bytes on both paths (an empty
        dict is the constant ``b"{}"`` — byte-identical to the JSON
        serialization, and it keeps the per-call client hot path free
        of a ``json.dumps`` round trip)."""
        metadata_blob = (
            b"{}" if not self.metadata
            else json.dumps(self.metadata, sort_keys=True).encode("utf-8")
        )
        return ipc_codec.encode(
            _TYPE_INDEX[self.message_type],
            self.timestamp,
            self.message_id,
            self.sender_id,
            self.receiver_id,
            self.reply_to,
            self.payload,
            self.capabilities,
            metadata_blob,
        )

    @classmethod
    def from_wire(cls, buf: bytes) -> "IPCMessage":
        """Parse a wire buffer back into a message (the inverse of
        ``to_wire``). Raises ``ValueError`` on a malformed buffer."""
        fields = ipc_codec.decode(buf)
        message = cls(
            message_id=fields["message_id"].decode("utf-8"),
            message_type=_INDEX_TYPE[fields["message_type"]],
            sender_id=fields["sender_id"].decode("utf-8"),
            receiver_id=fields["receiver_id"].decode("utf-8"),
            payload=fields["payload"],
            capabilities=ipc_codec.split_caps_flat(fields["caps_flat"]),
            metadata=json.loads(fields["metadata"].decode("utf-8")),
            timestamp=fields["timestamp"],
        )
        if fields["reply_to"]:
            message.reply_to = fields["reply_to"].decode("utf-8")
        return message


@dataclass
class TokenBucket:
    """Token bucket for rate limiting per ADR-0009.
    
    Implements a standard token bucket algorithm:
    - Tokens are added at a fixed rate (tokens_per_second)
    - Each operation consumes a token
    - If no tokens are available, the operation is rate-limited
    - Maximum burst is limited by bucket_size
    """
    bucket_size: int = 100  # Maximum tokens
    tokens_per_second: float = 10.0  # Refill rate
    tokens: float = field(default_factory=lambda: 100.0)
    last_refill: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)
    
    def refill(self) -> None:
        """Refill tokens based on elapsed time."""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_refill
            self.tokens = min(
                self.bucket_size,
                self.tokens + elapsed * self.tokens_per_second
            )
            self.last_refill = now
    
    def try_consume(self, tokens: int = 1, sender_id: Optional[str] = None) -> bool:
        """Try to consume tokens. Returns True if successful.

        ``sender_id`` is accepted for interface compatibility with
        ``FairTokenBucket`` and ignored here — the plain bucket has a
        single shared pool (the ADR-0009 §32b benchmark showed that
        shared pool starves well-behaved senders under flood; endpoints
        that need fairness use ``FairTokenBucket``).
        """
        self.refill()
        with self.lock:
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False
    
    def consume(self, tokens: int = 1, timeout_s: float = 10.0) -> bool:
        """Consume tokens, waiting if necessary. Returns True if successful."""
        start_time = time.time()
        while time.time() - start_time < timeout_s:
            if self.try_consume(tokens):
                return True
            time.sleep(0.01)  # Brief sleep before retry
        return False


@dataclass
class FairTokenBucket(TokenBucket):
    """Per-sender-fair token bucket (ADR-0009 §"Benchmark Data — Sweep +
    Adversarial, 2026-09-10").

    The inherited ``bucket_size``/``tokens_per_second`` envelope is the
    endpoint's SHARED budget, exactly as before. Each distinct
    ``sender_id`` is additionally limited to a per-sender sub-bucket of
    ``sender_burst`` tokens whose refill is the envelope rate divided by
    ``fair_shares`` — so ONE sender's sustained intake can never exceed
    ``tokens_per_second / fair_shares`` (plus its burst), and the rest of
    the envelope stays available to well-behaved senders.

    The adversarial benchmark showed a naive shared bucket starves a
    legitimate 250 Hz client to ~9 admitted/s under a full-speed flood
    (96% throttled) while the flood still passes ~1,025/s. With the
    per-sender share, a flooder is confined to its own slice and the
    legitimate client sees its requested rate (regression-tested).

    Sizing rule for operators (the ADR's "refill scaled to workload
    class"): set ``tokens_per_second >= expected_senders x per-sender
    demand`` — e.g. an input endpoint serving 8 clients at 250 Hz wants
    an envelope of >=2,000/s with the default ``fair_shares=8``, giving
    every sender a guaranteed 250/s slice.

    ``try_consume()`` without ``sender_id`` keeps the legacy shared-pool
    semantics (the envelope only) so existing callers are unaffected.

    ``dynamic_shares=True`` lifts the lone-sender cap (ADR-0009 review
    package §5.1): ``fair_shares`` then means "shares at full
    occupancy" and the effective per-sender refill is the envelope
    divided by ``max(active senders, 1)`` — a lone sender may use the
    whole envelope, and at ``fair_shares``+ active senders the share is
    exactly the static ``envelope/fair_shares`` (the §32d guarantee is
    unchanged under full occupancy). Envelope admission still applies
    per consume, so N senders with N < fair_shares are bounded by the
    envelope and their own bursts.

    Sender bookkeeping is pruned when the table grows past
    ``_MAX_SENDER_ENTRIES`` (idle entries evicted first).
    """

    sender_burst: int = 64        # per-sender spike absorption
    fair_shares: int = 8          # per-sender refill = envelope / shares
    dynamic_shares: bool = False  # effective shares = max(active, fair_shares)
    sender_tokens: Dict[str, float] = field(default_factory=dict)
    sender_last: Dict[str, float] = field(default_factory=dict)

    _MAX_SENDER_ENTRIES: int = 1024  # class constant, not a field
    _SENDER_IDLE_EVICTION_S: float = 300.0

    def _sender_refill_rate(self) -> float:
        if not self.dynamic_shares:
            return self.tokens_per_second / max(self.fair_shares, 1)
        # dynamic_shares: shares shrink to the active-sender count (>= 1)
        # when the endpoint is under-occupied, so a lone sender can use
        # the whole envelope; at >= fair_shares active senders the
        # per-sender share is exactly the static envelope/fair_shares.
        return self.tokens_per_second / max(len(self.sender_tokens), 1)

    def active_senders(self) -> int:
        """Distinct senders currently tracked (the dynamic-shares
        denominator); operators see it as ``active_senders`` in the
        control-plane snapshot."""
        with self.lock:
            return len(self.sender_tokens)

    def _prune_senders(self, now: float) -> None:
        """Bound sender-table growth: evict idle entries when large."""
        if len(self.sender_tokens) <= self._MAX_SENDER_ENTRIES:
            return
        # dynamic_shares note: eviction only trims the TABLE; refill
        # rates already follow the live count via _sender_refill_rate,
        # so evicting an idle sender cannot inflate another's share
        # beyond the live occupancy (the evicted entry is, by
        # definition, not part of it).
        idle_cutoff = now - self._SENDER_IDLE_EVICTION_S
        stale = [s for s, last in self.sender_last.items() if last < idle_cutoff]
        for s in stale:
            self.sender_tokens.pop(s, None)
            self.sender_last.pop(s, None)
        # If still over (all entries busy), drop the oldest half.
        if len(self.sender_tokens) > self._MAX_SENDER_ENTRIES:
            by_age = sorted(self.sender_last.items(), key=lambda kv: kv[1])
            for s, _ in by_age[: len(by_age) // 2]:
                self.sender_tokens.pop(s, None)
                self.sender_last.pop(s, None)

    def try_consume(self, tokens: int = 1, sender_id: Optional[str] = None) -> bool:
        """Try to consume tokens for ``sender_id`` (or the shared pool
        when no sender is given). Returns True if successful."""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_refill
            self.tokens = min(
                self.bucket_size,
                self.tokens + elapsed * self.tokens_per_second,
            )
            self.last_refill = now

            if sender_id is None:
                # Legacy shared-pool path.
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True
                return False

            # Per-sender sub-bucket, refilled at envelope/fair_shares.
            rate = self._sender_refill_rate()
            last = self.sender_last.get(sender_id, now)
            s_tokens = min(
                float(self.sender_burst),
                self.sender_tokens.get(sender_id, float(self.sender_burst))
                + (now - last) * rate,
            )
            self.sender_last[sender_id] = now
            self._prune_senders(now)

            if self.tokens >= tokens and s_tokens >= tokens:
                self.tokens -= tokens
                self.sender_tokens[sender_id] = s_tokens - tokens
                return True
            self.sender_tokens[sender_id] = s_tokens
            return False


class IPCEndpoint:
    """Represents an IPC endpoint for a container per NPS-003 §4.
    
    Each container has one or more endpoints for receiving messages.
    Endpoints are identified by a unique name and are scoped to their container.
    """
    
    def __init__(self, endpoint_id: str, container_id: str, rate_limit: Optional[TokenBucket] = None):
        """Initialize an IPC endpoint.
        
        Args:
            endpoint_id: Unique identifier for this endpoint
            container_id: The container that owns this endpoint
            rate_limit: Optional token bucket for rate limiting
                (defaults to a ``FairTokenBucket`` — per-sender fairness
                per ADR-0009 §32b — so one flooding container cannot
                starve well-behaved senders on this endpoint)
        """
        self.endpoint_id = endpoint_id
        self.container_id = container_id
        self.message_queue: queue.Queue = queue.Queue()
        self.rate_limit = rate_limit or FairTokenBucket()
        self.created_at = time.time()
        self.message_count = 0
        self.lock = threading.Lock()
        # Store-and-forward admission samples (ADR-0009 ops story):
        # ring of (ts, admitted) for the metrics op — bounded, in
        # memory, oldest dropped. Granularity is per send_message call;
        # consumers aggregate into rates/rejection ratios.
        self._admission_samples: deque = deque(maxlen=_ADMISSION_SAMPLE_MAX)
    
    def __repr__(self) -> str:
        return f"IPCEndpoint(id={self.endpoint_id}, container={self.container_id})"

    def _admit_sample(self, admitted: bool, ts: float) -> None:
        self._admission_samples.append((ts, admitted))

    def admission_metrics(self, window_s: float = 60.0) -> Dict[str, Any]:
        """Admission metrics over the trailing ``window_s`` (clamped to
        what the sample ring retains): total/admitted/rejected counts,
        rates per second, and the rejection ratio. Zero samples → zero
        rates, ratio None ("no data"), never NaN."""
        cutoff = time.time() - max(window_s, 0.0)
        with self.lock:
            recent = [
                (ts, ok) for (ts, ok) in self._admission_samples
                if ts >= cutoff
            ]
        total = len(recent)
        admitted = sum(1 for _, ok in recent if ok)
        rejected = total - admitted
        return {
            "window_s": window_s,
            "retained_s": (
                min(window_s, _ADMISSION_SAMPLE_MAX / max(total / window_s,
                                                          1e-9))
                if total else 0.0),
            "total": total,
            "admitted": admitted,
            "rejected": rejected,
            "admitted_per_s": round(admitted / window_s, 2),
            "rejected_per_s": round(rejected / window_s, 2),
            "rejection_ratio": (
                round(rejected / total, 4) if total else None),
        }
    
    def send_message(self, message: IPCMessage) -> bool:
        """Enqueue a message for this endpoint.
        
        Respects rate limiting per ADR-0009. When the endpoint's limiter
        is a ``FairTokenBucket``, the per-sender share is enforced using
        the message's ``sender_id`` so one flooding container cannot
        starve well-behaved ones (the naive shared bucket did — see
        BENCHMARK_RESULTS §32b).
        
        Args:
            message: The message to enqueue
            
        Returns:
            True if the message was queued, False if rate-limited
        """
        sender = getattr(message, "sender_id", None)
        if not self.rate_limit.try_consume(sender_id=sender):
            logger.warning(f"IPC rate limit exceeded for endpoint {self.endpoint_id}")
            with self.lock:
                self._admit_sample(False, time.time())
            return False
        
        with self.lock:
            self.message_queue.put(message)
            self.message_count += 1
            self._admit_sample(True, time.time())
        
        logger.debug(f"Enqueued message {message.message_id[:8]} to {self.endpoint_id}")
        return True
    
    def receive_message(self, timeout_s: float = 5.0) -> Optional[IPCMessage]:
        """Dequeue a message from this endpoint.
        
        Args:
            timeout_s: Maximum time to wait for a message
            
        Returns:
            The next message, or None if timeout expires
        """
        try:
            message = self.message_queue.get(timeout=timeout_s)
            logger.debug(f"Received message {message.message_id[:8]} on {self.endpoint_id}")
            return message
        except queue.Empty:
            return None
    
    def has_messages(self) -> bool:
        """Check if there are pending messages."""
        return not self.message_queue.empty()
    
    def pending_count(self) -> int:
        """Get the number of pending messages."""
        return self.message_queue.qsize()


_ADMISSION_SAMPLE_MAX = 4096


class IPCManager:
    """Manages IPC endpoints and message routing for all containers.
    
    Implements NPS-017 §4.3 (IPC Semantics) and NPS-003 (IPC Primitives).
    """
    
    def __init__(self, capability_manager=None,
                 default_bucket_size: Optional[int] = None,
                 default_tokens_per_second: Optional[float] = None,
                 default_fair_shares: int = 8,
                 default_sender_burst: int = 64):
        """Initialize the IPC manager.

        New endpoints get a ``FairTokenBucket`` (ADR-0009 §32b): the
        envelope below is the shared budget and each sender is confined
        to ``tokens_per_second / default_fair_shares`` (+
        ``default_sender_burst`` burst), so a flooding container cannot
        starve well-behaved ones.

        Sizing rule: set ``default_tokens_per_second >= expected_senders
        x per-sender demand`` (e.g. 8 clients at 250 Hz → >=2,000/s with
        the default 8 shares).

        Args:
            capability_manager: Optional reference to CapabilityManager for
                               enforcing CAP_IPC_SEND/RECEIVE
            default_bucket_size: Burst capacity of the shared envelope for
                                new endpoint buckets
            default_tokens_per_second: Refill rate (calls/s) of the shared
                                      envelope for new endpoints
            default_fair_shares: Sender count the envelope is divided by
                                 (per-sender guaranteed share)
            default_sender_burst: Per-sender spike absorption (tokens)
        """
        self.endpoints: Dict[str, IPCEndpoint] = {}
        self.container_endpoints: Dict[str, List[str]] = {}  # container_id -> [endpoint_ids]
        self.capability_manager = capability_manager
        self.pending_calls: Dict[str, threading.Event] = {}  # message_id -> event
        self.call_replies: Dict[str, IPCMessage] = {}  # message_id -> reply
        self.lock = threading.Lock()
        if default_bucket_size is None:
            default_bucket_size = LIBRARY_DEFAULT_BUCKET_SIZE
        if default_tokens_per_second is None:
            default_tokens_per_second = LIBRARY_DEFAULT_TOKENS_PER_SECOND
        self.default_bucket_size = default_bucket_size
        self.default_tokens_per_second = default_tokens_per_second
        self.default_fair_shares = max(default_fair_shares, 1)
        self.default_sender_burst = default_sender_burst
        logger.info("IPCManager initialized (burst=%d, rate=%.0f/s, "
                    "fair_shares=%d, sender_burst=%d)",
                    default_bucket_size, default_tokens_per_second,
                    self.default_fair_shares, self.default_sender_burst)
    
    def create_endpoint(self, container_id: str, endpoint_id: Optional[str] = None) -> IPCEndpoint:
        """Create a new IPC endpoint for a container.
        
        Per NPS-003 §4, each container can have multiple endpoints.
        
        Args:
            container_id: The container that owns this endpoint
            endpoint_id: Optional custom endpoint ID (auto-generated if not provided)
            
        Returns:
            The created endpoint
        """
        if endpoint_id is None:
            endpoint_id = f"ep-{uuid.uuid4().hex[:12]}"
        
        # Create rate limiter for this endpoint — per-sender-fair by
        # default (ADR-0009 §32b): one flooder must not starve the rest.
        rate_limit = FairTokenBucket(
            bucket_size=self.default_bucket_size,
            tokens_per_second=self.default_tokens_per_second,
            fair_shares=self.default_fair_shares,
            sender_burst=self.default_sender_burst,
        )
        
        endpoint = IPCEndpoint(endpoint_id, container_id, rate_limit)
        
        with self.lock:
            self.endpoints[endpoint_id] = endpoint
            if container_id not in self.container_endpoints:
                self.container_endpoints[container_id] = []
            self.container_endpoints[container_id].append(endpoint_id)
        
        logger.info(f"Created endpoint {endpoint_id} for container {container_id}")
        return endpoint
    
    def send(
        self,
        sender_id: str,
        receiver_endpoint_id: str,
        payload: bytes,
        capabilities: Optional[List[str]] = None,
    ) -> bool:
        """Send an asynchronous message per NPS-003 §3.1.
        
        Args:
            sender_id: Container sending the message
            receiver_endpoint_id: Target endpoint
            payload: Message payload
            capabilities: Optional list of capabilities to transfer
            
        Returns:
            True if sent successfully, False if denied or rate-limited
        """
        # Check capability
        if self.capability_manager:
            from backend.capability import Capability
            if not self.capability_manager.validate_operation(sender_id, Capability.CAP_IPC_SEND):
                logger.warning(f"Container {sender_id} lacks CAP_IPC_SEND")
                return False
        
        endpoint = self.endpoints.get(receiver_endpoint_id)
        if endpoint is None:
            logger.error(f"Endpoint {receiver_endpoint_id} not found")
            return False
        
        message = IPCMessage(
            message_type=IPCMessageType.SEND,
            sender_id=sender_id,
            receiver_id=endpoint.container_id,
            payload=payload,
            capabilities=capabilities or [],
        )
        
        success = endpoint.send_message(message)
        if success:
            logger.info(f"Sent message from {sender_id} to {receiver_endpoint_id}")
        return success
    
    def receive(self, endpoint_id: str, timeout_s: float = 5.0) -> Optional[IPCMessage]:
        """Receive a message from an endpoint per NPS-003 §3.2.
        
        Control-plane capability check: the endpoint's owning container
        must hold CAP_IPC_RECEIVE, mirroring the send-side check. This is
        the control-plane half of NPS-017 §4.2 (data-plane enforcement is
        seccomp, see ``backend/seccomp.py``).
        
        Args:
            endpoint_id: The endpoint to receive from
            timeout_s: Maximum wait time
            
        Returns:
            The received message, or None if timeout or denied
        """
        endpoint = self.endpoints.get(endpoint_id)
        if endpoint is None:
            logger.error(f"Endpoint {endpoint_id} not found")
            return None
        
        if self.capability_manager:
            from backend.capability import Capability
            if not self.capability_manager.validate_operation(
                endpoint.container_id, Capability.CAP_IPC_RECEIVE
            ):
                logger.warning(f"Container {endpoint.container_id} lacks CAP_IPC_RECEIVE")
                return None
        
        message = endpoint.receive_message(timeout_s)
        if message:
            logger.info(f"Received message on {endpoint_id}")
        return message
    
    def call(
        self,
        sender_id: str,
        receiver_endpoint_id: str,
        payload: bytes,
        capabilities: Optional[List[str]] = None,
        timeout_s: float = 10.0,
    ) -> Optional[IPCMessage]:
        """Make a synchronous call (request-reply) per NPS-003 §3.3.
        
        Args:
            sender_id: Container making the call
            receiver_endpoint_id: Target endpoint
            payload: Request payload
            capabilities: Optional capabilities to transfer
            timeout_s: Maximum wait time for reply
            
        Returns:
            The reply message, or None if timeout
        """
        # Check capability
        if self.capability_manager:
            from backend.capability import Capability
            if not self.capability_manager.validate_operation(sender_id, Capability.CAP_IPC_SEND):
                logger.warning(f"Container {sender_id} lacks CAP_IPC_SEND")
                return None
        
        endpoint = self.endpoints.get(receiver_endpoint_id)
        if endpoint is None:
            logger.error(f"Endpoint {receiver_endpoint_id} not found")
            return None
        
        # Create call message
        message = IPCMessage(
            message_type=IPCMessageType.CALL,
            sender_id=sender_id,
            receiver_id=endpoint.container_id,
            payload=payload,
            capabilities=capabilities or [],
        )
        
        # Register pending call
        event = threading.Event()
        with self.lock:
            self.pending_calls[message.message_id] = event
        
        try:
            # Send the call
            if not endpoint.send_message(message):
                return None
            
            # Wait for reply
            if not event.wait(timeout=timeout_s):
                logger.warning(f"Call {message.message_id[:8]} timed out")
                return None
            
            # Retrieve reply
            with self.lock:
                reply = self.call_replies.pop(message.message_id, None)
            
            if reply:
                logger.info(f"Received reply to call {message.message_id[:8]}")
            return reply
        finally:
            # Clean up
            with self.lock:
                self.pending_calls.pop(message.message_id, None)
                self.call_replies.pop(message.message_id, None)
    
    def reply(self, original_call_id: str, payload: bytes) -> bool:
        """Send a reply to a call per NPS-003 §3.3.
        
        Args:
            original_call_id: The message ID of the original call
            payload: Reply payload
            
        Returns:
            True if reply was sent successfully
        """
        with self.lock:
            if original_call_id not in self.pending_calls:
                logger.error(f"No pending call with ID {original_call_id}")
                return False
            
            # Create reply message
            reply = IPCMessage(
                message_type=IPCMessageType.REPLY,
                payload=payload,
                reply_to=original_call_id,
            )
            
            # Store reply
            self.call_replies[original_call_id] = reply
            
            # Signal waiting thread
            event = self.pending_calls[original_call_id]
            event.set()
        
        logger.info(f"Sent reply to call {original_call_id[:8]}")
        return True
    
    def notify(
        self,
        sender_id: str,
        receiver_endpoint_id: str,
        event_type: str,
    ) -> bool:
        """Send a lightweight notification per NPS-003 §3.4.
        
        Notifications are simpler than full messages and are used for
        signaling events (e.g., "process exited", "resource available").
        
        Args:
            sender_id: Container sending the notification
            receiver_endpoint_id: Target endpoint
            event_type: Type of event being notified
            
        Returns:
            True if notification was sent successfully
        """
        endpoint = self.endpoints.get(receiver_endpoint_id)
        if endpoint is None:
            logger.error(f"Endpoint {receiver_endpoint_id} not found")
            return False
        
        message = IPCMessage(
            message_type=IPCMessageType.NOTIFY,
            sender_id=sender_id,
            receiver_id=endpoint.container_id,
            payload=event_type.encode(),
        )
        
        success = endpoint.send_message(message)
        if success:
            logger.info(f"Sent notification from {sender_id} to {receiver_endpoint_id}")
        return success
    
    def get_endpoint(self, endpoint_id: str) -> Optional[IPCEndpoint]:
        """Get an endpoint by ID."""
        return self.endpoints.get(endpoint_id)
    
    def get_container_endpoints(self, container_id: str) -> List[IPCEndpoint]:
        """Get all endpoints for a container."""
        endpoint_ids = self.container_endpoints.get(container_id, [])
        return [self.endpoints[eid] for eid in endpoint_ids if eid in self.endpoints]
    
    def cleanup_container(self, container_id: str) -> None:
        """Clean up all endpoints for a container (e.g., on termination).
        
        Per NPS-010 §5, when a container is terminated, its IPC endpoints
        are cleaned up.
        """
        with self.lock:
            endpoint_ids = self.container_endpoints.pop(container_id, [])
            for endpoint_id in endpoint_ids:
                self.endpoints.pop(endpoint_id, None)
        
        logger.info(f"Cleaned up {len(endpoint_ids)} endpoints for container {container_id}")


def main():
    """Simple CLI for testing the IPC manager."""
    logging.basicConfig(level=logging.INFO)
    
    manager = IPCManager()
    
    # Create endpoints for two containers
    ep1 = manager.create_endpoint("container-1", "ep-service")
    ep2 = manager.create_endpoint("container-2", "ep-client")
    
    print(f"Created endpoints: {ep1}, {ep2}")
    
    # Send a message
    manager.send("container-2", "ep-service", b"Hello from client!")
    
    # Receive the message
    msg = manager.receive("ep-service", timeout_s=2.0)
    if msg:
        print(f"Received: {msg.payload.decode()}")
    
    # Test call-reply
    def reply_thread():
        time.sleep(0.5)
        msg = manager.receive("ep-service", timeout_s=5.0)
        if msg and msg.message_type == IPCMessageType.CALL:
            manager.reply(msg.message_id, b"Reply from service")
    
    thread = threading.Thread(target=reply_thread, daemon=True)
    thread.start()
    
    reply = manager.call("container-2", "ep-service", b"Request from client")
    if reply:
        print(f"Call reply: {reply.payload.decode()}")
    
    thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
