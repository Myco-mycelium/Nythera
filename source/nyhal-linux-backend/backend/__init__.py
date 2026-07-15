"""
Nythera Linux Backend (NyHAL Implementation)

This module implements the NyHAL backend contract (NPS-017 §4) for Linux systems.
It provides container primitives, capability enforcement, IPC semantics, storage
guarantees (via FUSE), and boot/lifecycle management.

Architecture:
- backend.container: Container creation, teardown, and resource management
- backend.capability: Capability enforcement via LSM and seccomp
- ipc.core: IPC primitives (send/receive/call/notify)
- fuse.nyfs: NyFS filesystem implementation via FUSE
- boot.lifecycle: Boot sequence and service orchestration

References:
- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- ADR-0012: Adopt NyHAL as a pluggable kernel abstraction layer
- ADR-0016: NyFS Linux Backend implemented as a user-space FUSE filesystem
"""

__version__ = "0.1.0"
__status__ = "Experimental"

from backend.container import Container, ContainerManager
from backend.capability import CapabilityManager

__all__ = [
    "Container",
    "ContainerManager",
    "CapabilityManager",
]
