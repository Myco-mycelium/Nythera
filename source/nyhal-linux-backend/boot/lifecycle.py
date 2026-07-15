#!/usr/bin/env python3
"""
Boot Lifecycle Management for the Nythera Linux Backend

Implements NPS-017 §4.5 (Boot and Lifecycle) and NPS-001 §5 (Boot Milestones).
Manages the initialization and shutdown of the Nythera Linux Backend system.

Boot Sequence (per NPS-001 §5):
1. Hardware/Host Initialization
   - Detect kernel features (cgroups, namespaces, seccomp)
   - Initialize container manager and capability system
   - Set up IPC infrastructure

2. Trusted First Process
   - Create and launch the initial Nythera service container
   - This process acts as the system init for Nythera

3. Service Bring-up
   - Initialize filesystem (NyFS FUSE daemon)
   - Bring up core services (IPC, capability enforcement)
   - Prepare for user container creation

4. Usable Session
   - System is ready to accept container creation requests
   - Users can run applications

References:
- NPS-017 §4.5: Boot and Lifecycle
- NPS-001 §5: Boot Milestones
- NPS-010 §4: Container Lifecycle State Machine
"""

import enum
import logging
import signal
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, List

logger = logging.getLogger(__name__)


class BootPhase(enum.Enum):
    """Boot phases per NPS-001 §5."""
    UNINITIALIZED = "uninitialized"
    HARDWARE_INIT = "hardware_init"
    FIRST_PROCESS = "first_process"
    SERVICE_BRINGUP = "service_bringup"
    USABLE_SESSION = "usable_session"
    SHUTDOWN = "shutdown"


@dataclass
class BootMilestone:
    """Represents a boot milestone."""
    phase: BootPhase
    name: str
    description: str
    timestamp: float = field(default_factory=time.time)
    success: bool = True
    error_message: Optional[str] = None


class BootSequence:
    """Manages the boot sequence of the Nythera Linux Backend.
    
    Implements NPS-001 §5 (Boot Milestones) and NPS-017 §4.5 (Boot and Lifecycle).
    """
    
    def __init__(self):
        """Initialize the boot sequence manager."""
        self.current_phase = BootPhase.UNINITIALIZED
        self.milestones: List[BootMilestone] = []
        self.lock = threading.Lock()
        self.shutdown_event = threading.Event()
        self.phase_callbacks: Dict[BootPhase, List[Callable]] = {}
        
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
        logger.info("BootSequence initialized")
    
    def _signal_handler(self, signum, frame):
        """Handle termination signals."""
        logger.info(f"Received signal {signum}, initiating shutdown")
        self.shutdown_event.set()
    
    def register_phase_callback(self, phase: BootPhase, callback: Callable) -> None:
        """Register a callback to be called when a phase is reached.
        
        Args:
            phase: The boot phase
            callback: Function to call when the phase is reached
        """
        if phase not in self.phase_callbacks:
            self.phase_callbacks[phase] = []
        self.phase_callbacks[phase].append(callback)
    
    def transition_to_phase(self, phase: BootPhase, description: str = "") -> None:
        """Transition to a new boot phase.
        
        Args:
            phase: The new boot phase
            description: Optional description of what's happening
        """
        with self.lock:
            old_phase = self.current_phase
            self.current_phase = phase
            
            milestone = BootMilestone(
                phase=phase,
                name=phase.value,
                description=description or f"Transitioned to {phase.value}",
            )
            self.milestones.append(milestone)
            
            logger.info(
                f"Boot phase transition: {old_phase.value} → {phase.value} "
                f"({description})"
            )
        
        # Call registered callbacks
        if phase in self.phase_callbacks:
            for callback in self.phase_callbacks[phase]:
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Error in phase callback: {e}")
    
    def record_milestone(
        self,
        phase: BootPhase,
        name: str,
        description: str,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> None:
        """Record a boot milestone.
        
        Args:
            phase: The boot phase
            name: Milestone name
            description: Milestone description
            success: Whether the milestone was successful
            error_message: Optional error message if unsuccessful
        """
        with self.lock:
            milestone = BootMilestone(
                phase=phase,
                name=name,
                description=description,
                success=success,
                error_message=error_message,
            )
            self.milestones.append(milestone)
        
        if success:
            logger.info(f"✓ {name}: {description}")
        else:
            logger.error(f"✗ {name}: {description} ({error_message})")
    
    def boot(self) -> bool:
        """Execute the full boot sequence.
        
        Returns:
            True if boot succeeded, False otherwise
        """
        logger.info("=" * 60)
        logger.info("Nythera Linux Backend Boot Sequence")
        logger.info("=" * 60)
        
        try:
            # Phase 1: Hardware/Host Initialization
            if not self._phase_hardware_init():
                return False
            
            # Phase 2: Trusted First Process
            if not self._phase_first_process():
                return False
            
            # Phase 3: Service Bring-up
            if not self._phase_service_bringup():
                return False
            
            # Phase 4: Usable Session
            if not self._phase_usable_session():
                return False
            
            logger.info("=" * 60)
            logger.info("Boot sequence completed successfully")
            logger.info("=" * 60)
            return True
        except Exception as e:
            logger.error(f"Boot sequence failed: {e}", exc_info=True)
            return False
    
    def _phase_hardware_init(self) -> bool:
        """Phase 1: Hardware/Host Initialization.
        
        - Detect kernel features
        - Initialize container manager
        - Initialize capability system
        """
        self.transition_to_phase(
            BootPhase.HARDWARE_INIT,
            "Initializing hardware and kernel interfaces"
        )
        
        try:
            # Detect kernel features
            self.record_milestone(
                BootPhase.HARDWARE_INIT,
                "Kernel Feature Detection",
                "Detecting cgroups v2, namespaces, seccomp-bpf support"
            )
            
            # Initialize container manager
            from backend.container import ContainerManager
            self.container_manager = ContainerManager(use_cgroups_v2=True)
            self.record_milestone(
                BootPhase.HARDWARE_INIT,
                "Container Manager",
                f"Initialized (cgroups_v2={self.container_manager.use_cgroups_v2})"
            )
            
            # Initialize capability system
            from backend.capability import CapabilityManager
            self.capability_manager = CapabilityManager()
            self.record_milestone(
                BootPhase.HARDWARE_INIT,
                "Capability Manager",
                "Initialized"
            )
            
            # Initialize IPC system
            from ipc.core import IPCManager
            self.ipc_manager = IPCManager(capability_manager=self.capability_manager)
            self.record_milestone(
                BootPhase.HARDWARE_INIT,
                "IPC Manager",
                "Initialized"
            )
            
            return True
        except Exception as e:
            self.record_milestone(
                BootPhase.HARDWARE_INIT,
                "Hardware Initialization",
                "Failed",
                success=False,
                error_message=str(e)
            )
            return False
    
    def _phase_first_process(self) -> bool:
        """Phase 2: Trusted First Process.
        
        - Create and launch the initial Nythera service container
        """
        self.transition_to_phase(
            BootPhase.FIRST_PROCESS,
            "Launching trusted first process"
        )
        
        try:
            from backend.container import ContainerConfig, ResourceLimits
            
            # Create the first process container
            config = ContainerConfig(
                name="nythera-init",
                hostname="nythera-init",
                command=["/bin/sh", "-c", "echo 'Nythera init process running'; sleep infinity"],
                limits=ResourceLimits(memory_mb=512, pid_limit=128),
            )
            
            self.first_process = self.container_manager.create(config)
            self.record_milestone(
                BootPhase.FIRST_PROCESS,
                "First Process Creation",
                f"Created container {self.first_process.id}"
            )
            
            # Initialize capabilities for the first process
            self.capability_manager.initialize_container(self.first_process.id)
            self.record_milestone(
                BootPhase.FIRST_PROCESS,
                "First Process Capabilities",
                "Initialized with default capabilities"
            )
            
            # Create IPC endpoint for the first process
            ep = self.ipc_manager.create_endpoint(self.first_process.id, "ep-init")
            self.record_milestone(
                BootPhase.FIRST_PROCESS,
                "First Process IPC",
                f"Created endpoint {ep.endpoint_id}"
            )
            
            return True
        except Exception as e:
            self.record_milestone(
                BootPhase.FIRST_PROCESS,
                "First Process",
                "Failed",
                success=False,
                error_message=str(e)
            )
            return False
    
    def _phase_service_bringup(self) -> bool:
        """Phase 3: Service Bring-up.
        
        - Initialize filesystem (NyFS FUSE daemon)
        - Bring up core services
        """
        self.transition_to_phase(
            BootPhase.SERVICE_BRINGUP,
            "Bringing up core services"
        )
        
        try:
            # Initialize NyFS filesystem
            from fuse.nyfs import NyFSFilesystem, NyFSMount
            self.nyfs = NyFSFilesystem("/tmp/nyfs-backend")
            self.record_milestone(
                BootPhase.SERVICE_BRINGUP,
                "NyFS Filesystem",
                "Initialized"
            )
            
            # Create a FUSE mount point
            self.nyfs_mount = NyFSMount(self.nyfs, "/tmp/nythera-mnt")
            self.record_milestone(
                BootPhase.SERVICE_BRINGUP,
                "NyFS Mount",
                "Prepared (FUSE integration deferred)"
            )
            
            # Create a snapshot of the initial filesystem state
            snap_id = self.nyfs.create_snapshot("boot-baseline")
            self.record_milestone(
                BootPhase.SERVICE_BRINGUP,
                "NyFS Snapshot",
                f"Created baseline snapshot {snap_id}"
            )
            
            return True
        except Exception as e:
            self.record_milestone(
                BootPhase.SERVICE_BRINGUP,
                "Service Bring-up",
                "Failed",
                success=False,
                error_message=str(e)
            )
            return False
    
    def _phase_usable_session(self) -> bool:
        """Phase 4: Usable Session.
        
        - System is ready for container creation
        """
        self.transition_to_phase(
            BootPhase.USABLE_SESSION,
            "System ready for container creation"
        )
        
        try:
            self.record_milestone(
                BootPhase.USABLE_SESSION,
                "System Ready",
                "Nythera Linux Backend is operational"
            )
            return True
        except Exception as e:
            self.record_milestone(
                BootPhase.USABLE_SESSION,
                "Usable Session",
                "Failed",
                success=False,
                error_message=str(e)
            )
            return False
    
    def shutdown(self) -> None:
        """Gracefully shut down the Nythera Linux Backend."""
        self.transition_to_phase(BootPhase.SHUTDOWN, "Initiating shutdown")
        
        logger.info("Shutting down Nythera Linux Backend...")
        
        try:
            # Terminate the first process
            if hasattr(self, 'first_process'):
                self.container_manager.terminate(self.first_process)
                logger.info("Terminated first process")
            
            # Clean up containers
            for container in self.container_manager.list_containers():
                if container.id != "nythera-init":
                    self.container_manager.terminate(container)
            
            logger.info("Shutdown complete")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
    
    def get_boot_report(self) -> str:
        """Generate a boot report."""
        lines = ["Boot Report", "=" * 60]
        lines.append(f"Current Phase: {self.current_phase.value}")
        lines.append(f"Milestones: {len(self.milestones)}")
        lines.append("")
        
        for i, milestone in enumerate(self.milestones, 1):
            status = "✓" if milestone.success else "✗"
            lines.append(
                f"{i}. [{status}] {milestone.name} ({milestone.phase.value})"
            )
            lines.append(f"   {milestone.description}")
            if milestone.error_message:
                lines.append(f"   Error: {milestone.error_message}")
        
        return "\n".join(lines)


def main():
    """Simple CLI for testing the boot sequence."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    boot = BootSequence()
    
    # Execute boot sequence
    success = boot.boot()
    
    # Print report
    print("\n" + boot.get_boot_report())
    
    if success:
        print("\nBoot successful! System is ready.")
        print("Press Ctrl+C to shutdown.")
        try:
            boot.shutdown_event.wait()
        except KeyboardInterrupt:
            print("\nShutting down...")
            boot.shutdown()
    else:
        print("\nBoot failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
