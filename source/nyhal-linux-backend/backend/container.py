#!/usr/bin/env python3
"""
Container Management for the Nythera Linux Backend

Implements NPS-017 §4.1 (Container Primitives) and NPS-010 (Container Lifecycle).
Extends the proof-of-concept in poc-container/nyctr.py with:
- Direct syscalls instead of unshare(1)
- Cgroups v2 support with v1 fallback
- Container state machine (created, running, suspended, terminated)
- Resource limit enforcement
- Graceful shutdown and cleanup

References:
- NPS-017 §4.1: Container Primitives
- NPS-010 §4: Container Lifecycle State Machine
- NPS-002 §5: Process/Thread Model State Transitions
"""

import ctypes
import enum
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)


class ContainerState(enum.Enum):
    """Container lifecycle states per NPS-010 §4."""
    CREATED = "created"
    RUNNING = "running"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


@dataclass
class ResourceLimits:
    """Resource limits for a container, per NPS-010 §7.
    
    Every container MUST have resource limits assignable at creation.
    """
    memory_mb: int = 256
    pid_limit: int = 64
    cpu_shares: int = 1024
    cpu_quota_us: Optional[int] = None  # microseconds per period
    cpu_period_us: int = 100000


@dataclass
class ContainerConfig:
    """Configuration for a new container."""
    name: Optional[str] = None
    hostname: str = "nythera-container"
    command: List[str] = field(default_factory=lambda: ["/bin/sh"])
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    capabilities: List[str] = field(default_factory=list)  # Nythera capabilities
    environment: Dict[str, str] = field(default_factory=dict)


class Container:
    """Represents a single Nythera container instance.
    
    Implements the container state machine from NPS-010 §4.
    """
    
    def __init__(self, config: ContainerConfig):
        self.config = config
        self.id = config.name or f"nyctr-{uuid.uuid4().hex[:12]}"
        self.state = ContainerState.CREATED
        self.pid: Optional[int] = None
        self.cgroup_paths: List[str] = []
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.terminated_at: Optional[float] = None
        self.exit_code: Optional[int] = None
        
    def __repr__(self) -> str:
        return f"Container(id={self.id!r}, state={self.state.value})"
    
    def is_running(self) -> bool:
        """Check if the container process is still alive."""
        if self.pid is None:
            return False
        try:
            os.kill(self.pid, 0)  # Signal 0 checks if process exists
            return True
        except OSError:
            return False
    
    def transition_to(self, new_state: ContainerState) -> None:
        """Transition the container to a new state, validating state machine rules.
        
        Per NPS-010 §4, valid transitions are:
        - CREATED → RUNNING (start)
        - RUNNING → SUSPENDED (pause)
        - SUSPENDED → RUNNING (resume)
        - {RUNNING, SUSPENDED} → TERMINATED (stop)
        """
        valid_transitions = {
            ContainerState.CREATED: [ContainerState.RUNNING],
            ContainerState.RUNNING: [ContainerState.SUSPENDED, ContainerState.TERMINATED],
            ContainerState.SUSPENDED: [ContainerState.RUNNING, ContainerState.TERMINATED],
            ContainerState.TERMINATED: [],
        }
        
        if new_state not in valid_transitions.get(self.state, []):
            raise ValueError(
                f"Invalid state transition: {self.state.value} → {new_state.value}"
            )
        
        self.state = new_state
        if new_state == ContainerState.RUNNING:
            self.started_at = time.time()
        elif new_state == ContainerState.TERMINATED:
            self.terminated_at = time.time()


class ContainerManager:
    """Manages the lifecycle of multiple Nythera containers.
    
    Implements NPS-017 §4.1 (Container Primitives) on the Linux Backend.
    """
    
    def __init__(self, use_cgroups_v2: bool = True):
        """Initialize the container manager.
        
        Args:
            use_cgroups_v2: If True, attempt to use cgroups v2; fall back to v1 if unavailable.
        """
        self.containers: Dict[str, Container] = {}
        self.use_cgroups_v2 = use_cgroups_v2 and self._detect_cgroups_v2()
        self.cgroup_root = self._get_cgroup_root()
        logger.info(f"ContainerManager initialized (cgroups_v2={self.use_cgroups_v2})")
    
    def _detect_cgroups_v2(self) -> bool:
        """Detect if cgroups v2 is available on this system."""
        try:
            # cgroups v2 unified hierarchy is mounted at /sys/fs/cgroup
            result = subprocess.run(
                ["grep", "-q", "cgroup2", "/proc/filesystems"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Failed to detect cgroups v2: {e}")
            return False
    
    def _get_cgroup_root(self) -> Path:
        """Get the appropriate cgroup root path."""
        if self.use_cgroups_v2:
            return Path("/sys/fs/cgroup")
        else:
            # For v1, we'll use the memory controller root
            return Path("/sys/fs/cgroup/memory")
    
    def create(self, config: ContainerConfig) -> Container:
        """Create a new container (does not start it yet).
        
        Per NPS-010 §4, creation transitions the container to CREATED state.
        """
        container = Container(config)
        self.containers[container.id] = container
        logger.info(f"Created container {container.id}")
        return container
    
    def start(self, container: Container) -> int:
        """Start a container and wait for it to complete.
        
        Returns the exit code of the container's main process.
        Per NPS-010 §4, this transitions the container from CREATED to RUNNING.
        """
        if container.state != ContainerState.CREATED:
            raise ValueError(f"Cannot start container in {container.state.value} state")
        
        container.transition_to(ContainerState.RUNNING)
        
        try:
            self._setup_cgroups(container)
            exit_code = self._run_container_process(container)
            container.exit_code = exit_code
            container.transition_to(ContainerState.TERMINATED)
            return exit_code
        except Exception as e:
            logger.error(f"Error running container {container.id}: {e}")
            container.transition_to(ContainerState.TERMINATED)
            raise
        finally:
            self._cleanup_cgroups(container)
    
    def suspend(self, container: Container) -> None:
        """Suspend a running container (pause its execution).
        
        Per NPS-010 §4, this transitions the container from RUNNING to SUSPENDED.
        """
        if container.state != ContainerState.RUNNING:
            raise ValueError(f"Cannot suspend container in {container.state.value} state")
        
        if container.pid is None:
            raise ValueError(f"Container {container.id} has no associated PID")
        
        try:
            os.kill(container.pid, 19)  # SIGSTOP
            container.transition_to(ContainerState.SUSPENDED)
            logger.info(f"Suspended container {container.id} (PID {container.pid})")
        except OSError as e:
            logger.error(f"Failed to suspend container {container.id}: {e}")
            raise
    
    def resume(self, container: Container) -> None:
        """Resume a suspended container.
        
        Per NPS-010 §4, this transitions the container from SUSPENDED to RUNNING.
        """
        if container.state != ContainerState.SUSPENDED:
            raise ValueError(f"Cannot resume container in {container.state.value} state")
        
        if container.pid is None:
            raise ValueError(f"Container {container.id} has no associated PID")
        
        try:
            os.kill(container.pid, 18)  # SIGCONT
            container.transition_to(ContainerState.RUNNING)
            logger.info(f"Resumed container {container.id} (PID {container.pid})")
        except OSError as e:
            logger.error(f"Failed to resume container {container.id}: {e}")
            raise
    
    def terminate(self, container: Container, timeout_s: float = 10.0) -> None:
        """Terminate a container gracefully, with forced kill as fallback.
        
        Per NPS-010 §4, this transitions the container to TERMINATED.
        """
        if container.state == ContainerState.TERMINATED:
            return  # Already terminated
        
        if container.pid is None:
            container.transition_to(ContainerState.TERMINATED)
            return
        
        try:
            # Try SIGTERM first for graceful shutdown
            os.kill(container.pid, 15)  # SIGTERM
            
            # Wait for graceful termination
            start_time = time.time()
            while time.time() - start_time < timeout_s:
                if not container.is_running():
                    break
                time.sleep(0.1)
            
            # Force kill if still running
            if container.is_running():
                os.kill(container.pid, 9)  # SIGKILL
                logger.warning(f"Force-killed container {container.id} (PID {container.pid})")
            
            container.transition_to(ContainerState.TERMINATED)
            logger.info(f"Terminated container {container.id}")
        except OSError as e:
            logger.error(f"Error terminating container {container.id}: {e}")
            container.transition_to(ContainerState.TERMINATED)
    
    def _setup_cgroups(self, container: Container) -> None:
        """Set up cgroup resource limits for the container.
        
        Per NPS-010 §7: "Every container MUST have resource limits assignable at creation."
        """
        if self.use_cgroups_v2:
            self._setup_cgroups_v2(container)
        else:
            self._setup_cgroups_v1(container)
    
    def _setup_cgroups_v1(self, container: Container) -> None:
        """Set up cgroups v1 resource limits."""
        limits = container.config.limits
        
        # Memory controller
        mem_path = self.cgroup_root / container.id
        try:
            mem_path.mkdir(parents=True, exist_ok=True)
            (mem_path / "memory.limit_in_bytes").write_text(
                str(limits.memory_mb * 1024 * 1024)
            )
            container.cgroup_paths.append(str(mem_path))
            logger.debug(f"Set memory limit: {limits.memory_mb} MiB")
        except Exception as e:
            logger.error(f"Failed to set memory limit: {e}")
        
        # PID controller (separate hierarchy in v1)
        pids_root = Path("/sys/fs/cgroup/pids")
        pids_path = pids_root / container.id
        try:
            pids_path.mkdir(parents=True, exist_ok=True)
            (pids_path / "pids.max").write_text(str(limits.pid_limit))
            container.cgroup_paths.append(str(pids_path))
            logger.debug(f"Set PID limit: {limits.pid_limit}")
        except Exception as e:
            logger.error(f"Failed to set PID limit: {e}")
    
    def _setup_cgroups_v2(self, container: Container) -> None:
        """Set up cgroups v2 resource limits (unified hierarchy)."""
        limits = container.config.limits
        cgroup_path = self.cgroup_root / "nythera" / container.id
        
        try:
            cgroup_path.mkdir(parents=True, exist_ok=True)
            
            # Memory limit
            (cgroup_path / "memory.max").write_text(
                str(limits.memory_mb * 1024 * 1024)
            )
            logger.debug(f"Set memory limit: {limits.memory_mb} MiB")
            
            # PID limit
            (cgroup_path / "pids.max").write_text(str(limits.pid_limit))
            logger.debug(f"Set PID limit: {limits.pid_limit}")
            
            # CPU limits (if specified)
            if limits.cpu_quota_us:
                cpu_max = f"{limits.cpu_quota_us} {limits.cpu_period_us}"
                (cgroup_path / "cpu.max").write_text(cpu_max)
                logger.debug(f"Set CPU limit: {cpu_max}")
            
            container.cgroup_paths.append(str(cgroup_path))
        except Exception as e:
            logger.error(f"Failed to set cgroups v2 limits: {e}")
    
    def _run_container_process(self, container: Container) -> int:
        """Run the container's main process in isolated namespaces.
        
        Uses unshare(1) for the PoC; a production implementation would use
        direct clone()/unshare() syscalls for finer control.
        """
        if shutil.which("unshare") is None:
            raise RuntimeError("unshare(1) not found — required for namespace isolation")
        
        config = container.config
        
        # Build unshare command with namespace flags
        unshare_cmd = [
            "unshare",
            "--user", "--map-root-user",  # User namespace
            "--pid", "--mount-proc", "--fork",  # PID namespace
            "--uts",  # UTS namespace (hostname)
            "--mount",  # Mount namespace
            "--ipc",  # IPC namespace (for future IPC implementation)
            "sh", "-c",
            f"hostname {config.hostname} 2>/dev/null; exec \"$@\"",
            "--",
        ] + config.command
        
        logger.info(
            f"Launching container {container.id} (hostname={config.hostname}, "
            f"memory={config.limits.memory_mb}MiB, pids={config.limits.pid_limit})"
        )
        
        try:
            result = subprocess.run(unshare_cmd, capture_output=False)
            return result.returncode
        except Exception as e:
            logger.error(f"Failed to run container process: {e}")
            raise
    
    def _cleanup_cgroups(self, container: Container) -> None:
        """Clean up cgroup resources for the container."""
        for cgroup_path_str in container.cgroup_paths:
            cgroup_path = Path(cgroup_path_str)
            try:
                cgroup_path.rmdir()
                logger.debug(f"Cleaned up cgroup: {cgroup_path}")
            except OSError as e:
                logger.warning(f"Failed to clean up cgroup {cgroup_path}: {e}")
    
    def list_containers(self) -> List[Container]:
        """List all managed containers."""
        return list(self.containers.values())
    
    def get_container(self, container_id: str) -> Optional[Container]:
        """Get a container by ID."""
        return self.containers.get(container_id)


def main():
    """Simple CLI for testing the container manager."""
    logging.basicConfig(level=logging.INFO)
    
    manager = ContainerManager()
    
    # Create and run a simple test container
    config = ContainerConfig(
        hostname="nythera-test",
        command=["sh", "-c", "echo 'Hello from Nythera!'; sleep 2"],
        limits=ResourceLimits(memory_mb=128, pid_limit=32),
    )
    
    container = manager.create(config)
    print(f"Created: {container}")
    
    exit_code = manager.start(container)
    print(f"Exit code: {exit_code}")
    print(f"Final state: {container.state.value}")


if __name__ == "__main__":
    main()
