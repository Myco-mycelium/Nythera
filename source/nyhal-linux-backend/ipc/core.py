#!/usr/bin/env python3
"""
IPC Core Implementation for the Nythera Linux Backend

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
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable

logger = logging.getLogger(__name__)


class IPCMessageType(enum.Enum):
    """IPC message types per NPS-003 §3."""
    SEND = "send"  # Asynchronous message
    RECEIVE = "receive"  # Receive acknowledgment
    CALL = "call"  # Synchronous request
    REPLY = "reply"  # Reply to call
    NOTIFY = "notify"  # Lightweight notification


@dataclass
class IPCMessage:
    """Represents an IPC message per NPS-003 §3.
    
    Messages can carry:
    - Payload data (bytes)
    - Capabilities (which may be attenuated per NPS-003 §5)
    - Metadata (sender, receiver, type, etc.)
    """
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
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
    
    def try_consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if successful."""
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
        """
        self.endpoint_id = endpoint_id
        self.container_id = container_id
        self.message_queue: queue.Queue = queue.Queue()
        self.rate_limit = rate_limit or TokenBucket()
        self.created_at = time.time()
        self.message_count = 0
        self.lock = threading.Lock()
    
    def __repr__(self) -> str:
        return f"IPCEndpoint(id={self.endpoint_id}, container={self.container_id})"
    
    def send_message(self, message: IPCMessage) -> bool:
        """Enqueue a message for this endpoint.
        
        Respects rate limiting per ADR-0009.
        
        Args:
            message: The message to enqueue
            
        Returns:
            True if the message was queued, False if rate-limited
        """
        if not self.rate_limit.try_consume():
            logger.warning(f"IPC rate limit exceeded for endpoint {self.endpoint_id}")
            return False
        
        with self.lock:
            self.message_queue.put(message)
            self.message_count += 1
        
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


class IPCManager:
    """Manages IPC endpoints and message routing for all containers.
    
    Implements NPS-017 §4.3 (IPC Semantics) and NPS-003 (IPC Primitives).
    """
    
    def __init__(self, capability_manager=None):
        """Initialize the IPC manager.
        
        Args:
            capability_manager: Optional reference to CapabilityManager for
                               enforcing CAP_IPC_SEND/RECEIVE
        """
        self.endpoints: Dict[str, IPCEndpoint] = {}
        self.container_endpoints: Dict[str, List[str]] = {}  # container_id -> [endpoint_ids]
        self.capability_manager = capability_manager
        self.pending_calls: Dict[str, threading.Event] = {}  # message_id -> event
        self.call_replies: Dict[str, IPCMessage] = {}  # message_id -> reply
        self.lock = threading.Lock()
        logger.info("IPCManager initialized")
    
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
        
        # Create rate limiter for this endpoint
        rate_limit = TokenBucket(bucket_size=100, tokens_per_second=50.0)
        
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
        
        Args:
            endpoint_id: The endpoint to receive from
            timeout_s: Maximum wait time
            
        Returns:
            The received message, or None if timeout
        """
        endpoint = self.endpoints.get(endpoint_id)
        if endpoint is None:
            logger.error(f"Endpoint {endpoint_id} not found")
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
