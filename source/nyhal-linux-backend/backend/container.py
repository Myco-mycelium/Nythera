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
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
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
    seccomp: bool = True  # data-plane enforcement (NPS-017 §4.2)


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
    
    def __init__(self, use_cgroups_v2: bool = True, require_cgroups_v2: bool = False):
        """Initialize the container manager.
        
        Args:
            use_cgroups_v2: If True, attempt to use cgroups v2; fall back to v1 if unavailable.
            require_cgroups_v2: If True, refuse to fall back to an unhardened
                cgroup v1 path when v2 was expected — per NPS-017 §4.1
                ("a backend SHOULD prefer failing container creation over
                silently falling back to an unhardened v1 path").
        """
        self.containers: Dict[str, Container] = {}
        self.use_cgroups_v2 = use_cgroups_v2 and self._detect_cgroups_v2()
        if require_cgroups_v2 and not self.use_cgroups_v2:
            raise RuntimeError(
                "cgroups v2 required but unavailable; refusing to fall back to "
                "the cgroup v1 path (NPS-017 §4.1)"
            )
        self.cgroup_root = self._get_cgroup_root()
        self._policy_files: List[str] = []  # seccomp policy temp files to clean up
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
        self.spawn(container)
        try:
            return self.wait(container)
        finally:
            self._cleanup_cgroups(container)
            self._cleanup_policy_files()
    
    def spawn(self, container: Container) -> Container:
        """Start a container without waiting; the process runs detached.
        
        Sets ``container.pid`` so the container can be suspended, resumed,
        or terminated from another thread. Per NPS-010 §4, this transitions
        the container from CREATED to RUNNING.
        """
        if container.state != ContainerState.CREATED:
            raise ValueError(f"Cannot start container in {container.state.value} state")
        
        container.transition_to(ContainerState.RUNNING)
        
        try:
            self._setup_cgroups(container)
            self._spawn(container)  # sets container.pid
            self._attach_to_cgroups(container)
        except Exception as e:
            logger.error(f"Error starting container {container.id}: {e}")
            container.transition_to(ContainerState.TERMINATED)
            raise
        
        logger.info(f"Container {container.id} running (pid={container.pid})")
        return container
    
    def wait(self, container: Container, timeout_s: Optional[float] = None) -> int:
        """Wait for a spawned container to exit and return its exit code.
        
        Transitions the container to TERMINATED when the process ends.
        """
        proc = getattr(container, "_proc", None)
        if proc is None:
            raise ValueError(f"Container {container.id} was never spawned")
        try:
            exit_code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Container {container.id} did not exit within timeout")
        container.exit_code = exit_code
        container.transition_to(ContainerState.TERMINATED)
        return exit_code
    
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
    
    def _cgroup_v1_plan(self, container: Container) -> List[Tuple[Path, Dict[str, str]]]:
        """Compute the cgroup v1 hierarchy plan for a container.
        
        Returns a list of ``(cgroup_path, {file: content})`` pairs without
        touching the filesystem, so the hardening intent is testable even
        where /sys/fs/cgroup is not writable.
        """
        limits = container.config.limits
        mem_path = Path("/sys/fs/cgroup/memory") / container.id
        pids_path = Path("/sys/fs/cgroup/pids") / container.id
        return [
            (
                mem_path,
                {
                    "memory.limit_in_bytes": str(limits.memory_mb * 1024 * 1024),
                    # FIND-BACKEND-003 hardening: never allow this cgroup to
                    # trigger the v1 release_agent mechanism.
                    "notify_on_release": "0",
                },
            ),
            (
                pids_path,
                {"pids.max": str(limits.pid_limit)},
            ),
        ]
    
    def _setup_cgroups_v1(self, container: Container) -> None:
        """Set up cgroups v1 resource limits, with release_agent hardening.
        
        Per NPS-017 §4.1 (FIND-BACKEND-003): a container's mount namespace
        MUST NOT expose write access to the v1 ``release_agent``/
        ``notify_on_release`` mechanism. The backend never mounts cgroup
        filesystems into containers, and the launcher unmounts any that
        leak in; setting ``notify_on_release=0`` here is the belt-and-
        braces layer so a container cgroup can never invoke the agent.
        """
        for cgroup_path, settings in self._cgroup_v1_plan(container):
            try:
                cgroup_path.mkdir(parents=True, exist_ok=True)
                for filename, content in settings.items():
                    (cgroup_path / filename).write_text(content)
                    logger.debug(f"Set {cgroup_path.name}/{filename} = {content}")
                container.cgroup_paths.append(str(cgroup_path))
            except Exception as e:
                logger.error(f"Failed to set up cgroup {cgroup_path}: {e}")
    
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
    
    def _build_launch_command(self, container: Container, launcher: Path) -> List[str]:
        """Build the unshare(1) command that hands control to the launcher.
        
        The container's hostname and command are passed as separate argv
        entries — never interpolated into a shell string — closing the
        shell-interpolation hygiene finding FIND-BACKEND-004 (NPS-022 §4).
        """
        cmd = [
            "unshare",
            "--user", "--map-root-user",  # User namespace
            "--pid", "--mount-proc", "--fork",  # PID namespace
            "--uts",  # UTS namespace (hostname)
            "--mount",  # Mount namespace
            "--ipc",  # IPC namespace
            "--",
            sys.executable, str(launcher),
            "--hostname", container.config.hostname,
        ]
        if container.config.seccomp:
            policy_file = self._write_policy_file(container)
            cmd += ["--policy-file", str(policy_file)]
        cmd += ["--"]
        cmd += list(container.config.command)
        return cmd
    
    def _write_policy_file(self, container: Container) -> str:
        """Write the container's capability set to a 0600 temp file.
        
        The launcher reads this inside the container and compiles it into
        a seccomp filter (data-plane enforcement, FIND-BACKEND-002).
        """
        caps = container.config.capabilities
        if not caps:
            from backend.capability import CapabilityManager
            caps = [c.value for c in CapabilityManager().get_default_capabilities()]
        
        fd, path = tempfile.mkstemp(prefix="nythera-policy-", suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"capabilities": sorted(set(caps))}, fh)
        os.chmod(path, 0o600)
        self._policy_files.append(path)
        return path
    
    def _cleanup_policy_files(self) -> None:
        """Remove seccomp policy temp files."""
        for path in self._policy_files:
            try:
                os.unlink(path)
            except OSError as e:
                logger.warning(f"Failed to remove policy file {path}: {e}")
        self._policy_files.clear()
    
    def _spawn(self, container: Container) -> subprocess.Popen:
        """Spawn the container's main process in isolated namespaces.

        The container's real command runs via ``backend/launcher.py``,
        which sets the hostname (no shell), hardens cgroup mounts, and
        installs the container's seccomp filter before exec'ing.
        """
        if shutil.which("unshare") is None:
            raise RuntimeError("unshare(1) not found — required for namespace isolation")
        
        launcher = Path(__file__).resolve().parent / "launcher.py"
        cmd = self._build_launch_command(container, launcher)
        
        logger.info(
            f"Launching container {container.id} (hostname={container.config.hostname}, "
            f"memory={container.config.limits.memory_mb}MiB, "
            f"pids={container.config.limits.pid_limit}, "
            f"seccomp={container.config.seccomp})"
        )
        
        proc = subprocess.Popen(cmd, env=os.environ.copy())
        container.pid = proc.pid
        container._proc = proc
        return proc
    
    def _attach_to_cgroups(self, container: Container) -> None:
        """Move the container's main process into its cgroups.
        
        Without this the resource limits created in ``_setup_cgroups`` are
        never actually applied. Per NPS-010 §7, limits must be enforced,
        not merely configured.
        """
        if container.pid is None:
            raise ValueError(f"Container {container.id} has no PID to attach")
        pid = str(container.pid)
        
        for cgroup_path_str in container.cgroup_paths:
            cgroup_path = Path(cgroup_path_str)
            if self.use_cgroups_v2:
                member_file = cgroup_path / "cgroup.procs"
            else:
                member_file = cgroup_path / "tasks"
            try:
                member_file.write_text(pid + "\n")
                logger.debug(f"Attached pid {pid} to {member_file}")
            except OSError as e:
                logger.error(f"Failed to attach pid {pid} to {member_file}: {e}")
    
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
