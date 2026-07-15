"""
Boot and Lifecycle Management for the Nythera Linux Backend

Implements NPS-017 §4.5 (Boot and Lifecycle) and NPS-001 §5 (Boot Milestones).
Manages the initialization sequence, service bring-up, and graceful shutdown
of the Nythera Linux Backend.

Boot Milestones (per NPS-001 §5):
1. Hardware/Host Initialization: Set up kernel interfaces, cgroups, namespaces
2. Trusted First Process: Launch the initial Nythera service container
3. Service Bring-up: Initialize IPC, filesystem, capability systems
4. Usable Session: Containers can be created and run

References:
- NPS-017 §4.5: Boot and Lifecycle
- NPS-001 §5: Boot Milestones
- NPS-010 §4: Container Lifecycle State Machine
"""

__version__ = "0.1.0"
__status__ = "Experimental"

from boot.lifecycle import BootSequence, BootPhase

__all__ = [
    "BootSequence",
    "BootPhase",
]
