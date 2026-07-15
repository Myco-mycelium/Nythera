#!/usr/bin/env python3
"""
Capability Management for the Nythera Linux Backend

Implements NPS-017 §4.2 (Capability Enforcement) and NPS-011 (Capability Registry).
Manages the assignment, validation, and enforcement of Nythera capabilities for containers.

The backend acts as the sole arbiter of capability validity, preventing containers
from self-issuing or forging access beyond their granted capability set.

References:
- NPS-017 §4.2: Capability Enforcement
- NPS-011: Capability Registry
- NPS-003 §5.4: Capability Validation in IPC
- NPS-010 §5: Container Capability Assignment and Revocation
"""

import enum
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional

logger = logging.getLogger(__name__)


class Capability(enum.Enum):
    """Nythera capabilities from NPS-011 Capability Registry.
    
    Each capability grants a specific privilege or access right to a container.
    Capabilities are the sole mechanism for authorization; no implicit elevated
    access is granted to any container (per NPS-011 and ADR-0011).
    """
    
    # Core capabilities
    CAP_PROCESS_SPAWN = "CAP_PROCESS_SPAWN"  # Create child processes
    CAP_FILESYSTEM_READ = "CAP_FILESYSTEM_READ"  # Read filesystem
    CAP_FILESYSTEM_WRITE = "CAP_FILESYSTEM_WRITE"  # Write filesystem
    CAP_NETWORK_SOCKET = "CAP_NETWORK_SOCKET"  # Create network sockets
    CAP_NETWORK_BIND = "CAP_NETWORK_BIND"  # Bind to network addresses
    CAP_IPC_SEND = "CAP_IPC_SEND"  # Send IPC messages
    CAP_IPC_RECEIVE = "CAP_IPC_RECEIVE"  # Receive IPC messages
    CAP_DEVICE_ACCESS = "CAP_DEVICE_ACCESS"  # Access hardware devices
    
    # Graphics and gaming capabilities
    CAP_GRAPHICS_RENDER = "CAP_GRAPHICS_RENDER"  # Use GPU/graphics
    CAP_AUDIO_PLAYBACK = "CAP_AUDIO_PLAYBACK"  # Play audio
    CAP_AUDIO_RECORD = "CAP_AUDIO_RECORD"  # Record audio
    CAP_INPUT_DEVICE = "CAP_INPUT_DEVICE"  # Access input devices (keyboard, mouse, controller)
    
    # AI and diagnostics capabilities
    CAP_AI_DIAGNOSTICS_READ = "CAP_AI_DIAGNOSTICS_READ"  # Read system diagnostics for AI
    CAP_AI_SUGGEST_ACTION = "CAP_AI_SUGGEST_ACTION"  # Allow AI to suggest actions
    CAP_CLOUD_SYNC = "CAP_CLOUD_SYNC"  # Synchronize data with cloud services
    
    # Android compatibility capabilities
    CAP_CONTACTS = "CAP_CONTACTS"  # Access contacts
    CAP_CALENDAR = "CAP_CALENDAR"  # Access calendar
    CAP_TELEPHONY = "CAP_TELEPHONY"  # Make calls and send SMS
    CAP_SMS = "CAP_SMS"  # Send/receive SMS
    CAP_SENSORS = "CAP_SENSORS"  # Access device sensors
    CAP_MEDIA_LIBRARY = "CAP_MEDIA_LIBRARY"  # Access media library
    CAP_NEAR_FIELD = "CAP_NEAR_FIELD"  # NFC communication
    CAP_BIOMETRIC = "CAP_BIOMETRIC"  # Biometric authentication
    
    # System capabilities
    CAP_SYSTEM_TIME = "CAP_SYSTEM_TIME"  # Read system time
    CAP_SYSTEM_INFO = "CAP_SYSTEM_INFO"  # Read system information
    CAP_MEMORY_ALLOCATE = "CAP_MEMORY_ALLOCATE"  # Allocate memory


@dataclass
class CapabilityGrant:
    """Represents a capability granted to a container."""
    capability: Capability
    container_id: str
    granted_at: float = field(default_factory=lambda: __import__('time').time())
    attenuated_by: Optional['CapabilityGrant'] = None  # Parent capability if attenuated


class CapabilityManager:
    """Manages capability assignment, validation, and enforcement.
    
    Per NPS-017 §4.2, the backend is the sole arbiter of capability validity.
    This manager:
    1. Maintains the registry of capabilities granted to each container
    2. Validates capability requests before allowing operations
    3. Enforces capability attenuation rules (NPS-003 §5)
    4. Prevents containers from forging or self-issuing capabilities
    """
    
    def __init__(self):
        """Initialize the capability manager."""
        # Map: container_id -> Set[Capability]
        self.grants: Dict[str, Set[Capability]] = {}
        # Map: container_id -> List[CapabilityGrant] (for audit trail)
        self.audit_trail: Dict[str, List[CapabilityGrant]] = {}
        logger.info("CapabilityManager initialized")
    
    def grant_capability(self, container_id: str, capability: Capability) -> None:
        """Grant a capability to a container.
        
        Per NPS-010 §5, capabilities are assigned at container creation or
        revoked/granted during the container's lifetime only by the backend.
        
        Args:
            container_id: The container to grant the capability to
            capability: The capability to grant
        """
        if container_id not in self.grants:
            self.grants[container_id] = set()
            self.audit_trail[container_id] = []
        
        if capability not in self.grants[container_id]:
            self.grants[container_id].add(capability)
            grant = CapabilityGrant(capability, container_id)
            self.audit_trail[container_id].append(grant)
            logger.info(f"Granted {capability.value} to container {container_id}")
        else:
            logger.debug(f"Capability {capability.value} already granted to {container_id}")
    
    def revoke_capability(self, container_id: str, capability: Capability) -> None:
        """Revoke a capability from a container.
        
        Per NPS-010 §5, capability revocation is a valid operation that the
        backend can perform at any time.
        
        Args:
            container_id: The container to revoke the capability from
            capability: The capability to revoke
        """
        if container_id in self.grants and capability in self.grants[container_id]:
            self.grants[container_id].remove(capability)
            logger.info(f"Revoked {capability.value} from container {container_id}")
        else:
            logger.warning(
                f"Attempted to revoke non-existent capability {capability.value} "
                f"from container {container_id}"
            )
    
    def has_capability(self, container_id: str, capability: Capability) -> bool:
        """Check if a container has a specific capability.
        
        This is the primary validation point: no operation should proceed
        without calling this method first.
        
        Args:
            container_id: The container to check
            capability: The capability to verify
            
        Returns:
            True if the container has the capability, False otherwise
        """
        if container_id not in self.grants:
            return False
        return capability in self.grants[container_id]
    
    def get_capabilities(self, container_id: str) -> Set[Capability]:
        """Get all capabilities granted to a container.
        
        Args:
            container_id: The container to query
            
        Returns:
            A set of capabilities granted to the container
        """
        return self.grants.get(container_id, set()).copy()
    
    def validate_operation(self, container_id: str, required_capability: Capability) -> bool:
        """Validate that a container can perform an operation.
        
        This is the enforcement point: before any privileged operation,
        the backend calls this method. If it returns False, the operation
        is denied.
        
        Per NPS-017 §4.2: "A backend that allows a container to self-issue
        or forge access beyond its granted capability set is non-conformant."
        
        Args:
            container_id: The container requesting the operation
            required_capability: The capability required for the operation
            
        Returns:
            True if the operation is allowed, False otherwise
        """
        has_cap = self.has_capability(container_id, required_capability)
        if not has_cap:
            logger.warning(
                f"Container {container_id} attempted operation requiring "
                f"{required_capability.value} but does not have it"
            )
        return has_cap
    
    def attenuate_capability(
        self,
        source_container_id: str,
        target_container_id: str,
        capability: Capability,
    ) -> bool:
        """Transfer a capability from one container to another with attenuation.
        
        Per NPS-003 §5 (Capability Attenuation Rules), when a capability is
        transferred between containers, it may be attenuated (restricted).
        This method implements that rule: the target container receives a
        capability only if the source container has it.
        
        Args:
            source_container_id: The container transferring the capability
            target_container_id: The container receiving the capability
            capability: The capability to transfer
            
        Returns:
            True if the transfer succeeded, False if denied
        """
        if not self.has_capability(source_container_id, capability):
            logger.warning(
                f"Container {source_container_id} attempted to transfer "
                f"{capability.value} but does not have it"
            )
            return False
        
        # Grant the capability to the target
        self.grant_capability(target_container_id, capability)
        logger.info(
            f"Transferred {capability.value} from {source_container_id} "
            f"to {target_container_id}"
        )
        return True
    
    def get_audit_trail(self, container_id: str) -> List[CapabilityGrant]:
        """Get the audit trail of capability grants for a container.
        
        Per NPS-011 and the transparency requirements of the Nythera Manifest,
        all capability operations are logged for audit and debugging.
        
        Args:
            container_id: The container to audit
            
        Returns:
            A list of capability grants in chronological order
        """
        return self.audit_trail.get(container_id, []).copy()
    
    def reset_container(self, container_id: str) -> None:
        """Reset all capabilities for a container (e.g., on termination).
        
        Per NPS-010 §5, when a container is terminated, all its capabilities
        are revoked.
        
        Args:
            container_id: The container to reset
        """
        if container_id in self.grants:
            self.grants[container_id].clear()
            logger.info(f"Reset all capabilities for container {container_id}")
    
    def get_default_capabilities(self) -> Set[Capability]:
        """Get the default set of capabilities for a new container.
        
        Returns a minimal set of capabilities sufficient for basic operation.
        Additional capabilities must be explicitly granted.
        """
        return {
            Capability.CAP_PROCESS_SPAWN,
            Capability.CAP_FILESYSTEM_READ,
            Capability.CAP_SYSTEM_TIME,
            Capability.CAP_SYSTEM_INFO,
            Capability.CAP_MEMORY_ALLOCATE,
        }
    
    def initialize_container(self, container_id: str) -> None:
        """Initialize a container with default capabilities.
        
        Called when a new container is created.
        
        Args:
            container_id: The container to initialize
        """
        self.grants[container_id] = set()
        self.audit_trail[container_id] = []
        
        # Grant default capabilities
        for cap in self.get_default_capabilities():
            self.grant_capability(container_id, cap)
        
        logger.info(f"Initialized container {container_id} with default capabilities")


def main():
    """Simple CLI for testing the capability manager."""
    logging.basicConfig(level=logging.INFO)
    
    manager = CapabilityManager()
    
    # Initialize a test container
    container_id = "test-container-001"
    manager.initialize_container(container_id)
    
    # Check capabilities
    print(f"Capabilities: {[c.value for c in manager.get_capabilities(container_id)]}")
    
    # Grant additional capability
    manager.grant_capability(container_id, Capability.CAP_GRAPHICS_RENDER)
    
    # Validate operation
    can_render = manager.validate_operation(container_id, Capability.CAP_GRAPHICS_RENDER)
    print(f"Can render graphics: {can_render}")
    
    # Try to use a capability the container doesn't have
    can_network = manager.validate_operation(container_id, Capability.CAP_NETWORK_SOCKET)
    print(f"Can use network: {can_network}")
    
    # Print audit trail
    print(f"Audit trail: {len(manager.get_audit_trail(container_id))} entries")


if __name__ == "__main__":
    main()
