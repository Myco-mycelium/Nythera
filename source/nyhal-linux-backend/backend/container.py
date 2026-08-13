#!/usr/bin/env python3
"""
Container Management for the Nyrqis Linux Backend

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
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from backend import rust_syscalls  # ADR-0020 priority #2 FFI loader

logger = logging.getLogger(__name__)

# Namespace setup timeout: the direct path blocks on a pipe until the
# forked child reports the container's PID; a hung kernel call would
# otherwise block the manager forever, so the read is bounded.
_DIRECT_LAUNCH_TIMEOUT_S = 30.0


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
    hostname: str = "nyrqis-container"
    command: List[str] = field(default_factory=lambda: ["/bin/sh"])
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    capabilities: List[str] = field(default_factory=list)  # Nyrqis capabilities
    environment: Dict[str, str] = field(default_factory=dict)
    seccomp: bool = True  # data-plane enforcement (NPS-017 §4.2)
    default_deny: bool = False  # default-deny allowlist posture (opt-in)


class Container:
    """Represents a single Nyrqis container instance.
    
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
        # Legacy path: the unshare(1) Popen. Direct path: the forked
        # namespace-setup child that wait() reaps (its exit status is the
        # container's exit status). Exactly one of the two is set.
        self._proc: Optional[subprocess.Popen] = None
        self._direct_launcher_pid: Optional[int] = None
        
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
    """Manages the lifecycle of multiple Nyrqis containers.
    
    Implements NPS-017 §4.1 (Container Primitives) on the Linux Backend.
    """
    
    def __init__(
        self,
        use_cgroups_v2: bool = True,
        require_cgroups_v2: bool = False,
        use_direct_syscalls: bool = True,
    ):
        """Initialize the container manager.
        
        Args:
            use_cgroups_v2: If True, attempt to use cgroups v2; fall back to v1 if unavailable.
            require_cgroups_v2: If True, refuse to fall back to an unhardened
                cgroup v1 path when v2 was expected — per NPS-017 §4.1
                ("a backend SHOULD prefer failing container creation over
                silently falling back to an unhardened v1 path").
            use_direct_syscalls: If True (default), launch containers with
                direct ``unshare(2)``/``fork(2)`` syscalls via the
                ``rust_syscalls`` module (ADR-0020 priority #2, the
                direct-syscall launcher transition of
                ``docs/implementation_plan.md`` §4.1). If False, retain
                the legacy ``unshare(1)`` subprocess path.
        """
        self.containers: Dict[str, Container] = {}
        self.use_direct_syscalls = use_direct_syscalls
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
        Works for both launch paths: the legacy ``unshare(1)`` Popen and
        the direct-syscall launcher (whose reaped child carries the
        container's exit status).
        """
        if container._direct_launcher_pid is not None:
            return self._wait_direct(container, timeout_s)
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

    def _wait_direct(
        self, container: Container, timeout_s: Optional[float]
    ) -> int:
        """Reap the direct-syscall launcher child and decode its status.

        The launcher child exits with the container's exit status (or
        dies by the container's signal, matching Popen semantics: a
        signaled container returns ``-signum``).
        """
        pid = container._direct_launcher_pid
        deadline = None if timeout_s is None else time.time() + timeout_s
        while True:
            try:
                wpid, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                raise ValueError(
                    f"Container {container.id} (pid {pid}) already reaped"
                )
            if wpid == pid:
                break
            if deadline is not None and time.time() >= deadline:
                raise TimeoutError(
                    f"Container {container.id} did not exit within timeout"
                )
            time.sleep(0.05)
        if os.WIFEXITED(status):
            exit_code = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            exit_code = -os.WTERMSIG(status)
        else:
            exit_code = 1
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
        cgroup_path = self.cgroup_root / "nyrqis" / container.id
        
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
    
    def _launcher_args(self, container: Container, launcher: Path) -> List[str]:
        """The launcher invocation the container runs: the argv handed to
        ``launcher.py`` inside the new namespaces (shared by both launch
        paths). The container's hostname and command are separate argv
        entries — never interpolated into a shell string — closing the
        shell-interpolation hygiene finding FIND-BACKEND-004 (NPS-022
        §4). The trailing ``--`` separates the launcher's own options
        from the container command (argparse REMAINDER).
        """
        argv = [
            sys.executable, str(launcher),
            "--hostname", container.config.hostname,
        ]
        if container.config.seccomp:
            policy_file = self._write_policy_file(container)
            argv += ["--policy-file", str(policy_file)]
            if container.config.default_deny:
                argv += ["--default-deny"]
        argv += ["--"]
        argv += list(container.config.command)
        return argv

    def _build_launch_command(self, container: Container, launcher: Path) -> List[str]:
        """Build the legacy ``unshare(1)`` command (the opt-in path when
        ``use_direct_syscalls=False``). The launcher argv it carries is
        the same ``_launcher_args`` used by the direct path.
        """
        cmd = [
            "unshare",
            "--user", "--map-root-user",  # User namespace
            "--pid", "--mount-proc", "--fork",  # PID namespace
            "--uts",  # UTS namespace (hostname)
            "--mount",  # Mount namespace
            "--ipc",  # IPC namespace
            "--",
        ]
        cmd += self._launcher_args(container, launcher)
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
        
        fd, path = tempfile.mkstemp(prefix="nyrqis-policy-", suffix=".json")
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
    
    def _spawn(self, container: Container):
        """Spawn the container's main process in isolated namespaces.

        The container's real command runs via ``backend/launcher.py``,
        which sets the hostname (no shell), hardens cgroup mounts, and
        installs the container's seccomp filter before exec'ing. The
        direct path forks a namespace-setup child that performs the
        ``unshare(2)`` dance and execs the launcher; the legacy path
        shells out to ``unshare(1)``.
        """
        if self.use_direct_syscalls:
            return self._spawn_direct(container)
        return self._spawn_unshare(container)

    def _spawn_unshare(self, container: Container) -> subprocess.Popen:
        """Legacy path: spawn via the ``unshare(1)`` subprocess."""
        if shutil.which("unshare") is None:
            raise RuntimeError("unshare(1) not found — required for namespace isolation")
        
        launcher = Path(__file__).resolve().parent / "launcher.py"
        cmd = self._build_launch_command(container, launcher)
        
        logger.info(
            f"Launching container {container.id} (hostname={container.config.hostname}, "
            f"memory={container.config.limits.memory_mb}MiB, "
            f"pids={container.config.limits.pid_limit}, "
            f"seccomp={container.config.seccomp}, "
            f"default_deny={container.config.default_deny}, direct_syscalls=False)"
        )
        
        proc = subprocess.Popen(cmd, env=os.environ.copy())
        container.pid = proc.pid
        container._proc = proc
        return proc

    def _spawn_direct(self, container: Container):
        """Direct-syscall launch (ADR-0020 priority #2, plan §4.1).

        ``unshare(2)`` moves the *calling* process into the new
        namespaces, so the manager must never call it itself. Instead the
        manager forks a namespace-setup child which performs the same
        sequence ``unshare(1)`` used to, then forks the container's PID-1:

        1. ``unshare(CLONE_NEWUSER)`` + write the root uid/gid maps
           (``--map-root-user`` equivalent).
        2. ``unshare(CLONE_NEWNS | CLONE_NEWUTS | CLONE_NEWIPC)``.
        3. ``unshare(CLONE_NEWPID)`` — affects only the *next* fork, so
           the child forks again.
        4. PID-1 mounts a hardened procfs (``mount_proc``, the
           ``--mount-proc`` equivalent) and execs the launcher.

        The setup child relays the container's PID through a pipe, waits
        for it, and exits with its exit status (or dies by its signal),
        so ``wait()`` reaps the setup child and decodes the container's
        status exactly as the Popen path does. ``container.pid`` is the
        container's PID-1 (what suspend/resume/terminate/cgroup-attach
        must signal), ``container._direct_launcher_pid`` is the setup
        child (what ``wait()`` reaps).

        Fork-safety note: between ``fork`` and ``exec`` the child runs
        only the syscall wrappers and plain file writes — no logging, no
        Python allocation beyond the FFI call itself. The Rust backend
        is pre-loaded here so the child never dlopens. Spawn from a
        quiescent manager (no other threads holding locks), matching the
        fork rule Python's own ``subprocess`` documents.
        """
        launcher = Path(__file__).resolve().parent / "launcher.py"
        launcher_argv = self._launcher_args(container, launcher)

        # Pre-load so the forked child never calls dlopen between fork
        # and exec (the loader cache is inherited by the child).
        rust_syscalls._load_rust_backend()

        logger.info(
            f"Launching container {container.id} (hostname={container.config.hostname}, "
            f"memory={container.config.limits.memory_mb}MiB, "
            f"pids={container.config.limits.pid_limit}, "
            f"seccomp={container.config.seccomp}, "
            f"default_deny={container.config.default_deny}, direct_syscalls=True)"
        )

        read_fd, write_fd = os.pipe()
        try:
            launcher_pid = os.fork()
        except OSError:
            os.close(read_fd)
            os.close(write_fd)
            raise
        if launcher_pid == 0:
            # Namespace-setup child: never returns; exits via os._exit.
            os.close(read_fd)
            try:
                _direct_launch_child(write_fd, launcher_argv)
            except BaseException:
                os._exit(125)
        os.close(write_fd)

        # Bounded read: the setup child writes the container PID (or an
        # ERR: marker) and closes the pipe before waiting for PID-1.
        data = b""
        try:
            ready, _, _ = select.select([read_fd], [], [], _DIRECT_LAUNCH_TIMEOUT_S)
            if ready:
                data = os.read(read_fd, 4096)
        finally:
            os.close(read_fd)

        if data.startswith(b"ERR:") or not data:
            # The setup child already exited (it reports failures before
            # dying); kill is a no-op on a zombie and waitpid reaps it.
            try:
                os.kill(launcher_pid, 9)
            except OSError:
                pass
            try:
                os.waitpid(launcher_pid, 0)
            except ChildProcessError:
                pass
            if data.startswith(b"ERR:"):
                raise RuntimeError(
                    "direct-syscall launcher failed: "
                    f"{data[4:].decode('utf-8', 'replace')}"
                )
            raise RuntimeError(
                "direct-syscall launcher died during namespace setup "
                "(no PID reported)"
            )

        container.pid = int(data.decode())
        container._direct_launcher_pid = launcher_pid
        return None
    
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


def _write_root_maps(uid: Optional[int] = None, gid: Optional[int] = None) -> None:
    """Write the ``--map-root-user`` uid/gid maps for this process.

    Must run in the process that created the user namespace (a
    namespace's creator may write its own maps). ``setgroups`` is set to
    ``deny`` first so the ``gid_map`` write is permitted; the file may
    not exist on kernels without the knob, so its write is best-effort.
    ``uid_map``/``gid_map`` then map the caller to root inside the
    namespace (``0 <uid> 1``). Plain file writes (open/write/close
    syscalls) — safe between fork and exec.

    ``uid``/``gid`` MUST be captured BEFORE ``unshare(CLONE_NEWUSER)``:
    inside the new (still unmapped) namespace, ``getuid()`` reports the
    overflow uid 65534 (nobody), so reading them here would map the
    wrong id and the kernel would refuse it with EPERM.
    """
    if uid is None or gid is None:
        uid = os.getuid()
        gid = os.getgid()
    for path, content in (
        ("/proc/self/setgroups", b"deny\n"),
        ("/proc/self/uid_map", f"0 {uid} 1\n".encode()),
        ("/proc/self/gid_map", f"0 {gid} 1\n".encode()),
    ):
        try:
            fd = os.open(path, os.O_WRONLY)
        except OSError:
            continue  # e.g. the setgroups knob is absent on older kernels
        try:
            os.write(fd, content)
        finally:
            os.close(fd)


def _direct_launch_child(write_fd: int, launcher_argv: List[str]) -> None:
    """The manager's forked namespace-setup child (direct-syscall path).

    Performs the ``unshare(2)`` dance ``unshare(1)`` used to do, forks
    the container's PID-1, relays its PID to the manager through
    ``write_fd``, then waits for it and exits with its status (or dies
    by its signal). Never returns on success. All failure exits use
    ``os._exit``: between fork and exec the child must not run Python's
    cleanup machinery, and any error is reported to the manager as an
    ``ERR:`` pipe message rather than an exception (the manager reaps
    the child and raises).

    ``launcher_argv`` is the argv handed to ``os.execv`` (Python
    interpreter + launcher.py + container command) — built by the
    manager before forking so the child does no allocation here.
    """
    def _fail(msg: str) -> None:
        try:
            os.write(write_fd, b"ERR:" + msg.encode("utf-8", "replace"))
        except OSError:
            pass
        os._exit(1)

    # The caller's real uid/gid MUST be captured before entering the new
    # user namespace: afterwards getuid() reports 65534 (unmapped), and
    # the kernel refuses to map an id that is not the caller's own.
    uid = os.getuid()
    gid = os.getgid()

    # 1. User namespace, then the root maps (--map-root-user equivalent).
    try:
        rust_syscalls.unshare(rust_syscalls.CLONE_NEWUSER)
    except OSError as e:
        _fail(f"unshare(CLONE_NEWUSER): {e}")
    try:
        _write_root_maps(uid, gid)
    except OSError as e:
        _fail(f"root map write: {e}")

    # 2. Mount/UTS/IPC namespaces (now permitted: full caps in the new
    #    user namespace).
    try:
        rust_syscalls.unshare(
            rust_syscalls.CLONE_NEWNS
            | rust_syscalls.CLONE_NEWUTS
            | rust_syscalls.CLONE_NEWIPC
        )
    except OSError as e:
        _fail(f"unshare(NS|UTS|IPC): {e}")

    # 3. PID namespace — affects only the NEXT fork, so we fork again.
    try:
        rust_syscalls.unshare(rust_syscalls.CLONE_NEWPID)
    except OSError as e:
        _fail(f"unshare(CLONE_NEWPID): {e}")

    # 4. PID-1: harden against losing the manager mid-setup, mount a
    #    fresh procfs, then exec the launcher.
    pid1 = os.fork()
    if pid1 == 0:
        try:
            os.close(write_fd)
        except OSError:
            pass
        # PR_SET_PDEATHSIG (1) = SIGKILL (9): if the setup child dies
        # before PID-1 exits (manager timeout path, manager crash), the
        # container is killed instead of orphaned. Cleared on exec, so
        # the residual window is only between this fork and the launcher
        # exec — documented in IMPLEMENTATION_STATUS.
        try:
            rust_syscalls.prctl(1, 9)
        except OSError:
            pass
        rc = rust_syscalls.mount_proc()
        if rc != 0:
            err = -rc
            detail = (
                os.strerror(err) if 1 <= err <= 4095 else f"rc {rc}"
            )
            os.write(2, f"nyrqis launcher: mount proc failed: {detail}\n".encode())
            os._exit(125)
        try:
            os.execv(launcher_argv[0], launcher_argv)
        except OSError as e:
            os.write(2, f"nyrqis launcher: exec failed: {e}\n".encode())
            os._exit(126)

    # Setup child: relay PID-1, wait for it, exit with its status.
    try:
        os.write(write_fd, str(pid1).encode())
        os.close(write_fd)
    except OSError:
        os._exit(1)
    _, status = os.waitpid(pid1, 0)
    if os.WIFEXITED(status):
        os._exit(os.WEXITSTATUS(status))
    if os.WIFSIGNALED(status):
        # Die by the same signal so the manager's waitpid observes
        # WIFSIGNALED, matching Popen's negative-returncode semantics.
        os.kill(os.getpid(), os.WTERMSIG(status))
    os._exit(1)


def main():
    """Simple CLI for testing the container manager."""
    logging.basicConfig(level=logging.INFO)
    
    manager = ContainerManager()
    
    # Create and run a simple test container
    config = ContainerConfig(
        hostname="nyrqis-test",
        command=["sh", "-c", "echo 'Hello from Nyrqis!'; sleep 2"],
        limits=ResourceLimits(memory_mb=128, pid_limit=32),
    )
    
    container = manager.create(config)
    print(f"Created: {container}")
    
    exit_code = manager.start(container)
    print(f"Exit code: {exit_code}")
    print(f"Final state: {container.state.value}")


if __name__ == "__main__":
    main()
