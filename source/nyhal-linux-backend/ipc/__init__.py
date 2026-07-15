"""
Inter-Process Communication (IPC) for the Nythera Linux Backend

Implements NPS-017 §4.3 (IPC Semantics) and NPS-003 (IPC and Capability Passing).
Provides the send/receive/call/notify primitives with capability transfer and
token-bucket rate limiting (ADR-0009).

References:
- NPS-017 §4.3: IPC Semantics
- NPS-003: Inter-Process Communication and Capability Passing
- ADR-0009: Per-container token-bucket rate limiting for IPC
"""

__version__ = "0.1.0"
__status__ = "Experimental"

from ipc.core import IPCEndpoint, IPCMessage, IPCManager

__all__ = [
    "IPCEndpoint",
    "IPCMessage",
    "IPCManager",
]
