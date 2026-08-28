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
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import List, Tuple
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple

from backend import rust_syscalls  # ADR-0020 priority #2 FFI loader
from backend import container_codec  # ADR-0020 priority #5 FFI loader
from backend import rust_launcher  # ADR-0020 launcher-init binary locator
from ipc.registry import ContainerIpcRegistry  # transport sender auth

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
    # Cgroup2 advanced enforcement
    cpu_weight: Optional[int] = None  # 1-10000, proportional CPU sharing
    memory_high: Optional[int] = None  # soft limit in bytes (pressure)
    io_max_rbps: Optional[int] = None  # max read bytes/sec
    io_max_wbps: Optional[int] = None  # max write bytes/sec
    # OOM killer protection
    memory_swap_max: Optional[int] = None  # max swap in bytes (None=unlimited)
    oom_score_adj: int = 0  # -1000 to 1000 (lower = less likely to OOM)
    oom_kill_disable: bool = False  # True = disable OOM killer for container


@dataclass
class ContainerConfig:
    """Configuration for a new container."""
    name: Optional[str] = None
    hostname: str = "nyrqis-container"
    command: List[str] = field(default_factory=lambda: ["/bin/sh"])
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    capabilities: List[str] = field(default_factory=list)  # Nyrqis capabilities
    environment: Dict[str, str] = field(default_factory=dict)
    inherit_host_env: bool = True  # inherit host env vars into container
    seccomp: bool = True  # data-plane enforcement (NPS-017 §4.2)
    # Fail-closed posture (NPS-017 §5.1): a container whose seccomp
    # install fails must NOT silently run unfiltered. Off only for
    # hosts where enforcement is impossible and the operator accepts
    # the conformance consequence.
    strict_seccomp: bool = True
    default_deny: bool = True   # default-deny allowlist posture (NPS-017 §5.1)
    network: bool = False  # own network namespace (loopback only), opt-in
    app_path: Optional[str] = None  # Nyrqis application path (.napp binary)
    # Overlay filesystem: when ``rootfs`` is set, the container gets a
    # writable overlay layer on top of the shared base at ``rootfs``.
    # The overlay is a ``fuse.overlay.OverlayFilesystem`` instance
    # attached to ``container.overlay`` after spawn.
    rootfs: Optional[str] = None  # base path for overlay (None = no overlay)
    # LSM (Linux Security Module) — set by _setup_lsm during spawn;
    # the launcher uses these to apply the container's AppArmor/SELinux
    # policy (second data-plane enforcement layer, NPS-017 §4.2).
    aa_profile: Optional[str] = None   # path to AppArmor profile file
    se_module_dir: Optional[str] = None  # path to SELinux module directory
    log_capture: bool = False  # capture stdout/stderr into a ring buffer
    log_max_lines: int = 1000  # max lines in the ring buffer per stream
    # Health check: periodic liveness probe via nsenter into the container
    health_check_cmd: Optional[List[str]] = None  # command to run
    health_check_interval: float = 30.0  # seconds between checks
    health_check_timeout: float = 5.0  # max seconds per check
    health_check_retries: int = 3  # consecutive failures before unhealthy
    # Priority scheduling
    nice_value: int = 0  # -20 (highest) to 19 (lowest), default 0
    cpu_affinity: Optional[List[int]] = None  # CPU core IDs (None = any)
    # Network policy
    network_policy: Optional[Dict[str, Any]] = None  # ingress/egress rules
    # Dependency ordering
    depends_on: Optional[List[str]] = None  # container IDs this depends on
    # Auto-restart policy
    restart_policy: str = "no"  # "no" | "always" | "on-failure"
    restart_max_retries: int = 5  # max restart attempts (for on-failure)
    restart_delay: float = 1.0  # seconds between restart attempts
    # Labels / metadata (key-value tags for organization)
    labels: Dict[str, str] = field(default_factory=dict)
    # Alert thresholds (percentage)
    alert_memory_warning: float = 75.0  # memory warning threshold
    alert_memory_critical: float = 90.0  # memory critical threshold
    alert_pid_warning: float = 75.0  # PID warning threshold
    alert_pid_critical: float = 90.0  # PID critical threshold
    alert_cpu_throttle: float = 50.0  # CPU throttle warning threshold

class RingBuffer:
    """Thread-safe bounded ring buffer for log line capture."""

    def __init__(self, max_lines: int = 1000):
        self.max_lines = max_lines
        self._lines: List[str] = []
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > self.max_lines:
                self._lines = self._lines[-self.max_lines:]

    def get_lines(self, tail: Optional[int] = None) -> List[str]:
        with self._lock:
            if tail is None:
                return list(self._lines)
            return list(self._lines[-tail:])

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._lines)


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
        # Overlay filesystem for this container (set by ContainerManager
        # when config.rootfs is provided)
        self.overlay = None
        # Direct path: the namespace's PID-1 — the launcher-init that
        # supervises the real command (``self.pid``). The init is what
        # suspend/freeze/terminate escalation addresses as belt-and-
        # braces; killing PID 1 tears down the whole namespace.
        self._init_pid: Optional[int] = None
        # True when the last suspend froze the container through its
        # cgroup (cgroup.freeze) rather than SIGSTOP; resume must then
        # thaw before anything else, and terminate must thaw so SIGTERM
        # is deliverable (a frozen cgroup defers non-SIGKILL signals).
        self._frozen_via_cgroup: bool = False
        # Network IP assigned when network=True (set by _setup_network)
        self.network_ip: Optional[str] = None
        # Log capture (when config.log_capture is True)
        self._stdout_buffer: Optional[RingBuffer] = None
        self._stderr_buffer: Optional[RingBuffer] = None
        self._log_threads: List[threading.Thread] = []
        # Health check state
        self.health_status: str = "starting"  # starting|healthy|unhealthy
        self.health_failures: int = 0
        self.health_last_check: Optional[float] = None
        self.health_last_output: str = ""
        self._health_stop: Optional[threading.Event] = None
        self._health_thread: Optional[threading.Thread] = None
        # Auto-restart state
        self.restart_count: int = 0
        self._restart_stop: Optional[threading.Event] = None
        # Resource recording state
        self._resource_stop: Optional[threading.Event] = None
        self._resource_thread: Optional[threading.Thread] = None
        # Alert history (bounded ring buffer)
        self._alert_history: List[Dict[str, Any]] = []
        # OOM event tracking
        self._oom_events: List[Dict[str, Any]] = []

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
        # NPS-010 §4 state machine, enforced through the Rust
        # launch-plan primitives (ADR-0020 priority #5): the crate's
        # transition_valid answers 0 (legal), -4098 (disallowed pair) or
        # -22 (out-of-range state); the loader maps -4098 to False. The
        # pure-Python floor in container_codec is byte-identical.
        if not container_codec.transition_valid(
            self.state.value, new_state.value
        ):
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
        ipc_registry: Optional[ContainerIpcRegistry] = None,
        capability_manager: Optional["CapabilityManager"] = None,
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
            ipc_registry: If given, the transport's sender registry
                (``ipc.registry.ContainerIpcRegistry``) is kept in sync
                automatically: each direct-syscall container's host pid
                is registered at spawn (its command is exec'd as PID-1,
                so ``container.pid`` IS the pid the kernel attaches to
                the container's datagrams) and unregistered when the
                container terminates. Pass the same object as the
                ``IPCDatagramServer``'s ``pid_registry`` to authenticate
                container senders with no manual bookkeeping.
            capability_manager: If given, the control-plane capability
                registry (``backend.capability.CapabilityManager``) is
                kept in sync automatically per NPS-010 §5: each
                container is initialized with its default capability
                set at spawn (so it can authenticate with CAP_IPC_SEND
                at the transport server and call capability-gated
                services) and its grants are revoked when it
                terminates. Pass the same object as the
                ``IPCDatagramServer``'s ``capability_manager``.
        """
        self.containers: Dict[str, Container] = {}
        self.ipc_registry = ipc_registry
        self.capability_manager = capability_manager
        self.use_direct_syscalls = use_direct_syscalls
        self.use_cgroups_v2 = use_cgroups_v2 and self._detect_cgroups_v2()
        if require_cgroups_v2 and not self.use_cgroups_v2:
            raise RuntimeError(
                "cgroups v2 required but unavailable; refusing to fall back to "
                "the cgroup v1 path (NPS-017 §4.1)"
            )
        self.cgroup_root = self._get_cgroup_root()
        self._policy_files: List[str] = []  # seccomp policy temp files to clean up
        self._bpf_files: List[str] = []  # serialized seccomp programs (Rust launcher)
        self._lsm_files: List[str] = []  # LSM policy files (AppArmor/SELinux)
        # Container lifecycle event ring (bounded, newest first)
        self._events: RingBuffer = RingBuffer(max_lines=500)
        # Resource quotas: owner → {"memory_mb": int, "pid_limit": int, "max_containers": int}
        self._quotas: Dict[str, Dict[str, Any]] = {}
        # Container lock files (prevent concurrent access)
        self._lock_dir = Path(tempfile.gettempdir()) / "nyrqis-locks"
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        self._lock_fds: Dict[str, int] = {}  # container_id → fd
        # Webhooks (HTTP callbacks for events)
        self._webhooks: Dict[str, Dict[str, Any]] = {}  # webhook_id → config
        self._webhook_id_counter = 0
        logger.info(f"ContainerManager initialized (cgroups_v2={self.use_cgroups_v2})")

    def _record_event(self, kind: str, container_id: str,
                      detail: str = "") -> None:
        """Record a lifecycle event in the bounded ring buffer."""
        ts = time.time()
        entry = f"{ts:.3f}\t{kind}\t{container_id}\t{detail}"
        self._events.append(entry)
        # Fire webhooks for matching events
        self._fire_webhooks(kind, container_id, detail)
        logger.debug("event: %s", entry)

    # ------------------------------------------------------------------
    # Webhooks (HTTP callbacks for events)
    # ------------------------------------------------------------------

    def register_webhook(
        self, url: str,
        events: Optional[List[str]] = None,
        secret: Optional[str] = None,
        container_filter: Optional[str] = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        """Register a webhook for container events.

        Args:
            url: HTTP URL to POST event payloads to.
            events: List of event types to subscribe to (None = all).
            secret: Optional HMAC secret for payload signing.
            container_filter: Optional container ID filter.
            enabled: Whether the webhook is active.

        Returns:
            The webhook config dict with ``id``.
        """
        self._webhook_id_counter += 1
        webhook_id = f"wh-{self._webhook_id_counter}"
        config = {
            "id": webhook_id,
            "url": url,
            "events": events,
            "secret": secret,
            "container_filter": container_filter,
            "enabled": enabled,
            "created_at": time.time(),
            "last_fired": None,
            "fire_count": 0,
        }
        self._webhooks[webhook_id] = config
        logger.info("register_webhook: %s → %s", webhook_id, url)
        return config

    def unregister_webhook(self, webhook_id: str) -> bool:
        """Unregister a webhook.

        Returns:
            True if the webhook existed and was removed.
        """
        existed = webhook_id in self._webhooks
        self._webhooks.pop(webhook_id, None)
        if existed:
            logger.info("unregister_webhook: %s", webhook_id)
        return existed

    def list_webhooks(self) -> List[Dict[str, Any]]:
        """List all registered webhooks."""
        return list(self._webhooks.values())

    def get_webhook(self, webhook_id: str) -> Optional[Dict[str, Any]]:
        """Get a webhook config by ID."""
        return self._webhooks.get(webhook_id)

    def enable_webhook(self, webhook_id: str) -> bool:
        """Enable a webhook."""
        wh = self._webhooks.get(webhook_id)
        if wh is None:
            return False
        wh["enabled"] = True
        return True

    def disable_webhook(self, webhook_id: str) -> bool:
        """Disable a webhook."""
        wh = self._webhooks.get(webhook_id)
        if wh is None:
            return False
        wh["enabled"] = False
        return True

    def _fire_webhooks(
        self, event_type: str, container_id: str, detail: str = "",
    ) -> None:
        """Fire matching webhooks for an event.

        Sends HTTP POST in a background thread to avoid blocking.
        """
        for wh in self._webhooks.values():
            if not wh["enabled"]:
                continue
            # Check event filter
            if wh["events"] and event_type not in wh["events"]:
                continue
            # Check container filter
            if wh["container_filter"] and wh["container_filter"] != container_id:
                continue
            # Fire in background
            payload = {
                "event": event_type,
                "container_id": container_id,
                "detail": detail,
                "timestamp": time.time(),
                "webhook_id": wh["id"],
            }
            wh["last_fired"] = time.time()
            wh["fire_count"] += 1
            threading.Thread(
                target=self._send_webhook,
                args=(wh["url"], payload, wh.get("secret")),
                daemon=True,
            ).start()

    def _send_webhook(
        self, url: str, payload: Dict[str, Any],
        secret: Optional[str] = None,
    ) -> None:
        """Send a webhook HTTP POST (blocking, run in thread)."""
        import urllib.request
        import urllib.error
        try:
            body = json.dumps(payload, default=str).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            # HMAC signature if secret provided
            if secret:
                import hashlib
                import hmac
                sig = hmac.new(
                    secret.encode(), body, hashlib.sha256
                ).hexdigest()
                headers["X-Nyrqis-Signature"] = f"sha256={sig}"
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    logger.debug(
                        "_send_webhook: %s → %d", url, resp.status,
                    )
            except urllib.error.URLError as e:
                logger.warning("_send_webhook: %s failed: %s", url, e)
        except Exception as e:
            logger.warning("_send_webhook: error: %s", e)

    # ------------------------------------------------------------------
    # Container lock files (prevent concurrent access)
    # ------------------------------------------------------------------

    def _lock_path(self, container_id: str) -> Path:
        """Return the lock file path for a container."""
        return self._lock_dir / f"{container_id}.lock"

    def acquire_lock(self, container_id: str, non_blocking: bool = False) -> bool:
        """Acquire an exclusive lock on a container.

        Uses ``fcntl.flock(LOCK_EX)`` on a lock file to prevent
        concurrent operations on the same container. The lock is
        automatically released when the process exits or
        ``release_lock`` is called.

        Args:
            container_id: The container to lock.
            non_blocking: If True, raise ``BlockingIOError`` instead
                of waiting when the lock is held.

        Returns:
            True if the lock was acquired.

        Raises:
            BlockingIOError: When ``non_blocking=True`` and lock is held.
        """
        lock_file = self._lock_path(container_id)
        try:
            import fcntl
            fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o600)
            flag = fcntl.LOCK_EX if not non_blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fcntl.flock(fd, flag)
            except (BlockingIOError, OSError):
                os.close(fd)
                raise
            self._lock_fds[container_id] = fd
            logger.debug("acquire_lock: %s", container_id)
            return True
        except ImportError:
            # fcntl not available (non-Linux), use in-process lock
            logger.debug("acquire_lock: fcntl unavailable, using in-process lock")
            return True

    def release_lock(self, container_id: str) -> None:
        """Release the lock on a container.

        Args:
            container_id: The container to unlock.
        """
        fd = self._lock_fds.pop(container_id, None)
        if fd is not None:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            try:
                os.close(fd)
            except OSError:
                pass
            logger.debug("release_lock: %s", container_id)
        # Clean up lock file
        lock_file = self._lock_path(container_id)
        try:
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass

    def is_locked(self, container_id: str) -> bool:
        """Check if a container is currently locked."""
        return container_id in self._lock_fds

    def list_locks(self) -> List[Dict[str, Any]]:
        """List all currently held locks."""
        locks: List[Dict[str, Any]] = []
        for cid, fd in self._lock_fds.items():
            locks.append({
                "container_id": cid,
                "fd": fd,
                "lock_file": str(self._lock_path(cid)),
            })
        return locks

    def container_events(self, tail: Optional[int] = None,
                         container_id: Optional[str] = None,
                         kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query container lifecycle events.

        Args:
            tail: If set, return only the last N events.
            container_id: Filter by container id.
            kind: Filter by event kind (created, started, suspended,
                  resumed, terminated, network_setup, log_capture).

        Returns:
            List of event dicts with ``time``, ``kind``,
            ``container_id``, and ``detail``.
        """
        raw = self._events.get_lines(tail)
        events: List[Dict[str, Any]] = []
        for line in raw:
            parts = line.split("\t", 3)
            if len(parts) < 3:
                continue
            ev = {
                "time": float(parts[0]) if parts[0] else 0,
                "kind": parts[1],
                "container_id": parts[2],
                "detail": parts[3] if len(parts) > 3 else "",
            }
            if container_id and ev["container_id"] != container_id:
                continue
            if kind and ev["kind"] != kind:
                continue
            events.append(ev)
        return events

    # -- resource quotas ------------------------------------------------

    def set_quota(self, owner: str, memory_mb: Optional[int] = None,
                  pid_limit: Optional[int] = None,
                  max_containers: Optional[int] = None) -> Dict[str, Any]:
        """Set resource quotas for an owner (user or group).

        Args:
            owner: The owner identifier (e.g. username or group).
            memory_mb: Maximum total memory in MiB across all containers.
            pid_limit: Maximum total PIDs across all containers.
            max_containers: Maximum number of concurrent containers.

        Returns:
            The quota dict that was set.
        """
        quota: Dict[str, Any] = self._quotas.get(owner, {}).copy()
        if memory_mb is not None:
            quota["memory_mb"] = memory_mb
        if pid_limit is not None:
            quota["pid_limit"] = pid_limit
        if max_containers is not None:
            quota["max_containers"] = max_containers
        self._quotas[owner] = quota
        self._record_event("quota_set", owner, str(quota))
        logger.info("quota set for %s: %s", owner, quota)
        return quota

    def get_quota(self, owner: str) -> Optional[Dict[str, Any]]:
        """Get the resource quota for an owner."""
        return self._quotas.get(owner)

    def list_quotas(self) -> Dict[str, Dict[str, Any]]:
        """List all resource quotas."""
        return dict(self._quotas)

    def delete_quota(self, owner: str) -> bool:
        """Delete the resource quota for an owner."""
        if owner in self._quotas:
            del self._quotas[owner]
            self._record_event("quota_deleted", owner)
            logger.info("quota deleted for %s", owner)
            return True
        return False

    def check_quota(self, owner: str, memory_mb: int = 0,
                    pids: int = 0) -> Tuple[bool, str]:
        """Check if creating a container would exceed the owner's quota.

        Args:
            owner: The owner identifier.
            memory_mb: Memory request for the new container.
            pids: PID request for the new container.

        Returns:
            Tuple of (allowed, reason). ``allowed`` is True if the
            quota is not exceeded.
        """
        quota = self._quotas.get(owner)
        if quota is None:
            return True, "no quota set"

        # Count existing containers for this owner
        existing = [
            c for c in self.containers.values()
            if c.state != ContainerState.TERMINATED
        ]

        max_c = quota.get("max_containers")
        if max_c is not None and len(existing) >= max_c:
            return False, (
                f"max_containers limit reached: {len(existing)}/{max_c}"
            )

        # Sum current resource usage
        total_mem = memory_mb
        total_pids = pids
        for c in existing:
            total_mem += c.config.limits.memory_mb
            total_pids += c.config.limits.pid_limit

        mem_limit = quota.get("memory_mb")
        if mem_limit is not None and total_mem > mem_limit:
            return False, (
                f"memory quota exceeded: {total_mem}/{mem_limit} MiB"
            )

        pid_limit = quota.get("pid_limit")
        if pid_limit is not None and total_pids > pid_limit:
            return False, (
                f"pid quota exceeded: {total_pids}/{pid_limit}"
            )

        return True, "within quota"

    def quota_usage(self, owner: str) -> Dict[str, Any]:
        """Get current quota usage for an owner."""
        quota = self._quotas.get(owner, {})
        existing = [
            c for c in self.containers.values()
            if c.state != ContainerState.TERMINATED
        ]
        total_mem = sum(c.config.limits.memory_mb for c in existing)
        total_pids = sum(c.config.limits.pid_limit for c in existing)
        return {
            "owner": owner,
            "quota": quota,
            "containers": len(existing),
            "memory_used_mb": total_mem,
            "pid_used": total_pids,
            "memory_limit_mb": quota.get("memory_mb"),
            "pid_limit": quota.get("pid_limit"),
            "max_containers": quota.get("max_containers"),
        }

    # -- health checks ---------------------------------------------------

    def start_health_check(self, container: Container) -> None:
        """Start a background health check thread for a container.

        Periodically runs the container's health check command via nsenter
        and updates the container's ``health_status`` (healthy/unhealthy)
        based on consecutive failures.

        Only activates when ``config.health_check_cmd`` is set.
        """
        if not container.config.health_check_cmd:
            return
        if container.state != ContainerState.RUNNING:
            return

        container._health_stop = threading.Event()

        def _probe() -> None:
            stop = container._health_stop
            while stop is not None and not stop.is_set():
                try:
                    result = self.container_exec(
                        container,
                        container.config.health_check_cmd,
                        timeout_s=container.config.health_check_timeout,
                    )
                    ok = result.get("exit_code", -1) == 0
                except Exception:
                    ok = False

                container.health_last_check = time.time()
                container.health_last_output = (
                    result.get("stdout", "") + result.get("stderr", "")
                ) if isinstance(result, dict) else ""

                if ok:
                    container.health_failures = 0
                    container.health_status = "healthy"
                else:
                    container.health_failures += 1
                    if container.health_failures >= container.config.health_check_retries:
                        container.health_status = "unhealthy"

                stop.wait(container.config.health_check_interval)

        t = threading.Thread(
            target=_probe, daemon=True,
            name=f"{container.id}-health",
        )
        t.start()
        container._health_thread = t
        self._record_event("health_check_started", container.id,
                           f"interval={container.config.health_check_interval}s")

    def stop_health_check(self, container: Container) -> None:
        """Stop the background health check thread."""
        if container._health_stop is not None:
            container._health_stop.set()
        container._health_thread = None
        container._health_stop = None

    def container_health(self, container: Container) -> Dict[str, Any]:
        """Get the health status of a container.

        Returns:
            Dict with ``status`` (starting/healthy/unhealthy),
            ``failures`` count, ``last_check`` timestamp,
            ``last_output`` (truncated), and ``check_cmd``.
        """
        return {
            "container_id": container.id,
            "status": container.health_status,
            "failures": container.health_failures,
            "last_check": container.health_last_check,
            "last_output": container.health_last_output[:500],
            "check_cmd": container.config.health_check_cmd,
        }

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
    
    def create(self, config: ContainerConfig,
               owner: Optional[str] = None) -> Container:
        """Create a new container (does not start it yet).

        Per NPS-010 §4, creation transitions the container to CREATED state.
        If an ``owner`` is provided and a quota exists, the quota is
        checked before creation.

        Args:
            config: The container configuration.
            owner: Optional owner for quota enforcement.

        Raises:
            ValueError: If the owner's quota would be exceeded.
        """
        if owner:
            allowed, reason = self.check_quota(
                owner,
                memory_mb=config.limits.memory_mb,
                pids=config.limits.pid_limit,
            )
            if not allowed:
                raise ValueError(f"Quota exceeded for {owner}: {reason}")

        container = Container(config)
        self.containers[container.id] = container
        self._record_event("created", container.id,
                           f"hostname={config.hostname}")
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
    
    def app_launch(self, app_id: str) -> Optional[Container]:
        """Launch an app through the compatibility framework.

        Resolves the app ID to a platform-specific launch configuration
        and creates a container with the appropriate command, capabilities,
        and settings.

        Args:
            app_id: App identifier (e.g. 'android:com.example.app',
                    'windows:notepad.exe', 'nyrqis:calculator')

        Returns:
            The running Container, or None if the app is not installed.
        """
        from ui.app_compat import get_app_manager
        manager = get_app_manager()
        launch_info = manager.launch(app_id)
        if launch_info is None:
            logger.error(f"Cannot launch app: {app_id} not installed")
            return None

        container_config = launch_info['container_config']
        # Attach overlay if rootfs is set
        if container_config.rootfs:
            self._setup_overlay_fn = self._setup_overlay

        container = self.create(container_config)
        self.spawn(container)
        # Track the running container in the app registry
        if hasattr(self, "_apps") and app_id in self._apps:
            self._apps[app_id]["container_id"] = container.id
            self._apps[app_id]["status"] = "running"
        logger.info(f"Launched app {app_id} in container {container.id}")
        return container
    
    def spawn(self, container: Container) -> Container:
        """Start a container without waiting; the process runs detached.
        
        Sets ``container.pid`` so the container can be suspended, resumed,
        or terminated from another thread. Per NPS-010 §4, this transitions
        the container from CREATED to RUNNING.
        """
        if container.state != ContainerState.CREATED:
            raise ValueError(f"Cannot start container in {container.state.value} state")
        
        container.transition_to(ContainerState.RUNNING)
        self._record_event("started", container.id,
                           f"cmd={' '.join(container.config.command)}")

        try:
            self._setup_cgroups(container)
            self._setup_overlay(container)
            self._setup_lsm(container)
            self._spawn(container)  # sets container.pid
            self._setup_network(container)
            self._ipc_register(container)
            self._cap_initialize(container)
            self._attach_to_cgroups(container)
            # Apply advanced cgroup2 limits (cpu.weight, memory.high, io.max)
            self.apply_cgroup2_advanced(container)
            # Apply OOM protection settings
            self.apply_oom_protection(container)
            self.start_health_check(container)
            # Apply scheduling parameters if configured
            if container.config.nice_value != 0:
                self.set_nice(container, container.config.nice_value)
            if container.config.cpu_affinity:
                self.set_cpu_affinity(container, container.config.cpu_affinity)
            # Apply network policy if configured
            self.apply_network_policy(container)
        except Exception as e:
            logger.error(f"Error starting container {container.id}: {e}")
            container.transition_to(ContainerState.TERMINATED)
            self._cap_reset(container)
            self._ipc_unregister(container)
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
        self._cap_reset(container)
        self._ipc_unregister(container)
        self._cleanup_network(container)
        container.overlay = None  # release overlay reference
        # Auto-restart check
        self._maybe_restart(container)
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
        self._cap_reset(container)
        self._ipc_unregister(container)
        self._cleanup_network(container)
        container.overlay = None  # release overlay reference
        # Auto-restart check
        self._maybe_restart(container)
        return exit_code

    def _cleanup_network(self, container: Container) -> None:
        """Remove the veth pair for a terminated container."""
        if not container.config.network:
            return
        try:
            from backend.network import teardown_container_network
            teardown_container_network(container.id)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"Network cleanup failed for {container.id}: {e}")

    # ------------------------------------------------------------------
    # Auto-restart policy
    # ------------------------------------------------------------------

    def _should_restart(self, container: Container) -> bool:
        """Determine if a terminated container should be restarted.

        Returns True when the restart policy warrants a restart:
        - ``always``: restart regardless of exit code.
        - ``on-failure``: restart only when exit_code != 0.
        - ``no``: never restart.

        Also respects ``restart_max_retries`` (0 = unlimited).
        """
        policy = container.config.restart_policy
        if policy == "no":
            return False
        max_retries = container.config.restart_max_retries
        if max_retries > 0 and container.restart_count >= max_retries:
            logger.info(
                "_should_restart: %s hit max retries (%d)",
                container.id, max_retries,
            )
            return False
        if policy == "always":
            return True
        if policy == "on-failure":
            return (container.exit_code or 0) != 0
        return False

    def _maybe_restart(self, container: Container) -> None:
        """Schedule a background restart if the policy allows it.

        Called after a container terminates.  Spawns a daemon thread
        that sleeps ``restart_delay`` seconds, re-creates the container
        config, and re-spawns it.
        """
        if not self._should_restart(container):
            return
        # Check for explicit stop (terminate was called)
        if container._restart_stop is not None and container._restart_stop.is_set():
            return
        container.restart_count += 1
        logger.info(
            "_maybe_restart: %s restarting (attempt %d)",
            container.id, container.restart_count,
        )
        self._record_event(
            "restart", container.id,
            f"attempt={container.restart_count}",
        )
        # Create a new stop event for this restart cycle
        container._restart_stop = threading.Event()

        def _restart_worker():
            delay = container.config.restart_delay
            # Wait but allow cancellation
            if container._restart_stop.wait(timeout=delay):
                return  # stopped before delay elapsed
            try:
                # Reset state for re-spawn
                container.state = ContainerState.CREATED
                container.exit_code = None
                container.pid = None
                container._direct_launcher_pid = None
                container._proc = None
                container.started_at = None
                container.terminated_at = None
                container.overlay = None
                self.spawn(container)
                logger.info(
                    "_maybe_restart: %s re-spawned (pid=%s)",
                    container.id, container.pid,
                )
            except Exception as e:
                logger.error(
                    "_maybe_restart: %s re-spawn failed: %s",
                    container.id, e,
                )

        t = threading.Thread(
            target=_restart_worker,
            name=f"restart-{container.id}",
            daemon=True,
        )
        t.start()

    def stop_restart(self, container: Container) -> None:
        """Cancel any pending restart for a container.

        Called by ``terminate()`` so that an explicit stop prevents
        the auto-restart policy from re-launching the container.
        """
        if container._restart_stop is not None:
            container._restart_stop.set()

    def get_restart_info(self, container: Container) -> Dict[str, Any]:
        """Return restart policy state for a container."""
        return {
            "restart_policy": container.config.restart_policy,
            "restart_count": container.restart_count,
            "restart_max_retries": container.config.restart_max_retries,
            "restart_delay": container.config.restart_delay,
        }

    def set_restart_policy(
        self, container: Container, policy: str,
        max_retries: Optional[int] = None,
        delay: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Change the restart policy for a running/created container.

        Args:
            container: Target container.
            policy: One of "no", "always", "on-failure".
            max_retries: Optional override for max restart attempts.
            delay: Optional override for restart delay.

        Returns:
            The updated restart info dict.

        Raises:
            ValueError: On invalid policy.
        """
        valid = ("no", "always", "on-failure")
        if policy not in valid:
            raise ValueError(
                f"invalid restart policy {policy!r}; "
                f"must be one of {valid}"
            )
        # Use object.__setattr__ to bypass dataclass frozen-ness if any
        object.__setattr__(container.config, "restart_policy", policy)
        if max_retries is not None:
            object.__setattr__(container.config, "restart_max_retries", max_retries)
        if delay is not None:
            object.__setattr__(container.config, "restart_delay", delay)
        logger.info(
            "set_restart_policy: %s → %s", container.id, policy,
        )
        return self.get_restart_info(container)

    # ------------------------------------------------------------------
    # Environment variable management
    # ------------------------------------------------------------------

    def set_env(self, container: Container, key: str, value: str) -> None:
        """Set an environment variable on a container.

        The variable is available to the container's command at startup.
        Can be called before or after spawn (after spawn, only affects
        future re-spawns via auto-restart).

        Args:
            container: Target container.
            key: Environment variable name.
            value: Environment variable value.
        """
        container.config.environment[key] = value
        logger.debug("set_env: %s %s=...", container.id, key)

    def unset_env(self, container: Container, key: str) -> bool:
        """Remove an environment variable from a container.

        Returns True if the key existed and was removed.
        """
        existed = key in container.config.environment
        container.config.environment.pop(key, None)
        if existed:
            logger.debug("unset_env: %s %s", container.id, key)
        return existed

    def get_env(self, container: Container, key: str) -> Optional[str]:
        """Get the value of an environment variable, or None."""
        return container.config.environment.get(key)

    def list_env(self, container: Container) -> Dict[str, str]:
        """Return a copy of all environment variables for a container."""
        return dict(container.config.environment)

    def _write_env_file(self, container: Container) -> Optional[str]:
        """Write container env vars to a JSON temp file for the launcher.

        Returns the path to the env file, or None if there are no
        custom env vars.
        """
        env = container.config.environment
        if not env:
            return None
        fd, path = tempfile.mkstemp(prefix="nyrqis-env-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(env, fh)
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        os.chmod(path, 0o600)
        self._policy_files.append(path)  # cleaned up with other temp files
        return path

    def _freeze_control(
        self, container: Container
    ) -> Optional[Tuple[Path, str, str]]:
        """The container's cgroup freezer control file, if freeze-capable.

        Returns ``(control_path, freeze_value, thaw_value)`` when the
        container is attached to a cgroup the backend can freeze, else
        ``None`` (suspend/resume then fall back to SIGSTOP/SIGCONT).

        The cgroups v2 unified hierarchy exposes the kernel freezer as
        ``cgroup.freeze`` (write ``1`` = frozen, ``0`` = thawed) — the
        whole cgroup, including future children, so a container's forks
        cannot outrun its suspension. cgroups v1 has no unified freezer:
        the legacy ``freezer`` controller is a separate hierarchy this
        backend does not provision (its v1 resource plan covers
        memory/pids/cpu only), so v1 containers keep the signal path.
        A container whose cgroup setup failed (``cgroup_paths`` empty)
        also falls back to signals — honest degradation, never a raise.
        """
        if not self.use_cgroups_v2 or not container.cgroup_paths:
            return None
        return (
            Path(container.cgroup_paths[0]) / "cgroup.freeze",
            "1",
            "0",
        )

    @staticmethod
    def _wait_frozen(control_path: Path, timeout_s: float = 1.5) -> bool:
        """Best-effort confirmation that a cgroup freeze took effect.

        On v2, ``cgroup.events`` next to ``cgroup.freeze`` carries
        ``frozen 1`` once every task in the cgroup is frozen. The file
        may be unreadable (permissions) or absent (tests); the freeze
        write itself already succeeded, so failure to confirm is not an
        error — the caller proceeds and the kernel freezes asynchronously.
        """
        events = control_path.parent / "cgroup.events"
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if "frozen 1" in events.read_text():
                    return True
            except OSError:
                return False
            time.sleep(0.02)
        return False

    def suspend(self, container: Container) -> None:
        """Suspend a running container (pause its execution).
        
        Per NPS-010 §4, this transitions the container from RUNNING to
        SUSPENDED. When the container is attached to a cgroups v2
        cgroup, the whole cgroup is frozen via ``cgroup.freeze`` (so
        descendants cannot keep running); otherwise the signal fallback
        SIGSTOPs the container's PID-1. On a failed freeze the backend
        falls back to SIGSTOP rather than failing the transition.
        """
        if container.state != ContainerState.RUNNING:
            raise ValueError(f"Cannot suspend container in {container.state.value} state")
        
        if container.pid is None:
            raise ValueError(f"Container {container.id} has no associated PID")
        
        frozen_via_cgroup = False
        control = self._freeze_control(container)
        if control is not None:
            path, freeze_value, _ = control
            try:
                path.write_text(freeze_value + "\n")
                if self._wait_frozen(path):
                    logger.debug(
                        f"cgroup freeze confirmed for {container.id} "
                        "(cgroup.events: frozen 1)"
                    )
                else:
                    logger.debug(
                        f"cgroup freeze write succeeded for {container.id} "
                        "but could not be confirmed (async freeze)"
                    )
                frozen_via_cgroup = True
            except OSError as e:
                logger.warning(
                    f"cgroup freeze failed for {container.id} ({e}); "
                    "falling back to SIGSTOP"
                )
        if not frozen_via_cgroup:
            os.kill(container.pid, signal.SIGSTOP)
        container._frozen_via_cgroup = frozen_via_cgroup
        self._record_event("suspended", container.id,
                           f"method={'cgroup' if frozen_via_cgroup else 'sigstop'}")
        container.transition_to(ContainerState.SUSPENDED)
        logger.info(
            f"Suspended container {container.id} (PID {container.pid}, "
            f"{'cgroup freeze' if frozen_via_cgroup else 'SIGSTOP'})"
        )

    def resume(self, container: Container) -> None:
        """Resume a suspended container.
        
        Per NPS-010 §4, this transitions the container from SUSPENDED to
        RUNNING. A container frozen through its cgroup is thawed via
        ``cgroup.freeze``; a thaw-write failure raises (the cgroup is
        still frozen, and a frozen cgroup defers SIGCONT — the caller
        retries or escalates to terminate). One stopped by signal is
        resumed with SIGCONT.
        """
        if container.state != ContainerState.SUSPENDED:
            raise ValueError(f"Cannot resume container in {container.state.value} state")
        
        if container.pid is None:
            raise ValueError(f"Container {container.id} has no associated PID")
        
        if container._frozen_via_cgroup:
            control = self._freeze_control(container)
            if control is not None:
                path, _, thaw_value = control
                try:
                    path.write_text(thaw_value + "\n")
                except OSError:
                    # The cgroup is still frozen. A frozen cgroup defers
                    # every signal except SIGKILL, so a SIGCONT fallback
                    # would NOT resume the container — it would silently
                    # report RUNNING for a process the kernel still
                    # holds frozen. Raise instead: the caller can retry
                    # the thaw or escalate to terminate(), whose SIGKILL
                    # still applies.
                    raise
                container._frozen_via_cgroup = False
                self._record_event("resumed", container.id, "method=cgroup")
                container.transition_to(ContainerState.RUNNING)
                logger.info(
                    f"Resumed container {container.id} (PID {container.pid}, "
                    "cgroup thaw)"
                )
                return
            # The cgroup is gone (cgroup_paths emptied). A cgroup with
            # live members cannot be rmdir'd, so the container is dead;
            # clear the flag and let the signal path report honestly.
            container._frozen_via_cgroup = False
        
        os.kill(container.pid, signal.SIGCONT)
        self._record_event("resumed", container.id, "method=sigcont")
        container.transition_to(ContainerState.RUNNING)
        logger.info(f"Resumed container {container.id} (PID {container.pid}, SIGCONT)")
    
    def terminate(self, container: Container, timeout_s: float = 10.0) -> None:
        """Terminate a container gracefully, with forced kill as fallback.

        The container command runs as a plain child of the PID-1
        launcher-init (not as PID 1 itself — see ``launcher.py``), so
        normal kernel signal semantics apply: SIGTERM terminates it
        unless it installs its own handler, and a well-behaved command
        exits within milliseconds (the pre-init design always burned the
        full window because Linux discards SIGTERM sent to a namespace
        PID 1 without a handler). Escalation SIGKILLs the command and
        (belt and braces) the PID-1 init — killing PID 1 tears down the
        whole namespace. The direct-path setup child is reaped
        best-effort so a killed container leaves no zombie behind.

        Per NPS-010 §4, this transitions the container to TERMINATED.
        """
        if container.state == ContainerState.TERMINATED:
            return  # Already terminated

        # Cancel any pending auto-restart so terminate prevents re-launch
        self.stop_restart(container)
        self.stop_health_check(container)
        self.remove_network_policy(container)

        if container.pid is None:
            container.transition_to(ContainerState.TERMINATED)
            self._cap_reset(container)  # idempotent; no registry entry to drop
            return
        
        try:
            # A cgroup-frozen container defers non-SIGKILL signals, so
            # thaw first (best-effort) to give SIGTERM a real graceful
            # window; SIGKILL below still works if the thaw fails.
            if container._frozen_via_cgroup:
                control = self._freeze_control(container)
                if control is not None:
                    path, _, thaw_value = control
                    try:
                        path.write_text(thaw_value + "\n")
                    except OSError as e:
                        logger.warning(
                            f"cgroup thaw before terminate failed for "
                            f"{container.id} ({e}); SIGKILL escalation "
                            "will still apply"
                        )
                container._frozen_via_cgroup = False
            
            # Try SIGTERM first for graceful shutdown. The command is a
            # plain child of the PID-1 init, so SIGTERM terminates it
            # normally (see the docstring / launcher.py).
            os.kill(container.pid, signal.SIGTERM)
            
            # Wait for graceful termination
            start_time = time.time()
            while time.time() - start_time < timeout_s:
                if not container.is_running():
                    break
                time.sleep(0.1)
            
            # Force kill if still running: the command and (belt and
            # braces) the PID-1 init — killing PID 1 tears down the
            # whole namespace.
            if container.is_running():
                os.kill(container.pid, signal.SIGKILL)
                if container._init_pid is not None:
                    try:
                        os.kill(container._init_pid, signal.SIGKILL)
                    except OSError:
                        pass
                logger.warning(f"Force-killed container {container.id} (PID {container.pid})")
            
            self._record_event("terminated", container.id,
                               f"exit_code={container.exit_code}")
            container.transition_to(ContainerState.TERMINATED)
            self._cap_reset(container)
            self._ipc_unregister(container)
            logger.info(f"Terminated container {container.id}")
        except OSError as e:
            logger.error(f"Error terminating container {container.id}: {e}")
            self._record_event("terminated", container.id,
                               f"error={e}")
            container.transition_to(ContainerState.TERMINATED)
            self._cap_reset(container)
            self._ipc_unregister(container)
        finally:
            self._reap_direct_child(container)
    
    def _reap_direct_child(self, container: Container) -> None:
        """Best-effort WNOHANG reap of the direct-path setup child.

        The setup child exits when the container's PID-1 init does; if
        nothing waits on it it lingers as a zombie (holding its cgroup).
        ``wait()`` reaps it normally; ``terminate()`` reaps it here so a
        killed container does not leave a zombie behind.
        """
        pid = getattr(container, "_direct_launcher_pid", None)
        if pid is None:
            return
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass

    # -- app compatibility --------------------------------------------

    def register_app(self, app_info: dict, app_path: str,
                     name: Optional[str] = None,
                     sandbox: bool = True) -> str:
        """Register an installed app in the manager's app registry.

        Args:
            app_info: Dict from ``analyze_apk`` or ``analyze_exe``.
            app_path: Path to the binary on disk.
            name: Optional display-name override.
            sandbox: Whether to launch in a sandboxed container.

        Returns:
            The app_id string (e.g. ``android:com.example.app``).
        """
        if not hasattr(self, "_apps"):
            self._apps = {}
        compat = app_info.get("compatibility", {})
        platform = compat.get("platform", "unknown")
        package = app_info.get("package", app_info.get("name", "unknown"))
        app_id = f"{platform}:{package}"
        self._apps[app_id] = {
            "app_id": app_id,
            "name": name or app_info.get("name", package),
            "version": app_info.get("version", "0.0.0"),
            "path": app_path,
            "platform": platform,
            "compatibility": compat,
            "sandbox": sandbox,
            "status": "installed",
            "container_id": None,
        }
        logger.info(f"Registered app {app_id} from {app_path}")
        return app_id

    def list_apps(self) -> list:
        """Return a list of registered app dicts."""
        if not hasattr(self, "_apps"):
            return []
        return list(self._apps.values())

    def terminate_app(self, app_id: str) -> bool:
        """Terminate a running app by id.

        Returns True if the container was found and terminated, False if
        the app is not running or not found.
        """
        if not hasattr(self, "_apps"):
            return False
        info = self._apps.get(app_id)
        if info is None:
            return False
        cid = info.get("container_id")
        if cid is None:
            return False
        container = self.containers.get(cid)
        if container is not None and container.is_running():
            self.terminate(container)
        info["container_id"] = None
        info["status"] = "installed"
        return True

    def _cap_initialize(self, container: Container) -> None:
        """Initialize the container's control-plane capability grants.

        Per NPS-010 §5, capabilities are assigned when a container is
        created and the backend is the sole arbiter (NPS-017 §4.2). The
        container's id receives its default set at spawn — the defaults
        include CAP_IPC_SEND (the transport server's check) and
        CAP_SYSTEM_INFO (the status service's check) — so a spawned
        container is immediately able to authenticate and call
        capability-gated services. Unlike the pid-based sender
        registry, this is not launch-path-dependent: grants are keyed
        by container id, so both the direct-syscall and legacy paths
        initialize.

        Note: ``initialize_container`` resets any pre-existing grants
        for the id before granting the defaults — pre-spawn grants via
        ``grant_capability`` are superseded at spawn (the standard
        flow is defaults at spawn, extras granted afterwards).
        """
        if self.capability_manager is None:
            return
        self.capability_manager.initialize_container(container.id)

    def _cap_reset(self, container: Container) -> None:
        """Revoke the container's capability grants on termination
        (NPS-010 §5), idempotent."""
        if self.capability_manager is None:
            return
        self.capability_manager.reset_container(container.id)

    def reload_policy(self, container: Container) -> bool:
        """Regenerate and re-apply the LSM policy for a running container.

        Called after a capability grant or revocation to update the
        container's AppArmor / SELinux policy at runtime.  Seccomp-BPF
        filters are one-shot (cannot be removed once installed), so this
        only refreshes the LSM layer.

        Returns True on success, False on failure (logged, never raises).
        """
        try:
            from backend.lsm import (
                build_lsm_policy, AppArmorProfile, SEPolicy, lsm_audit,
            )
            from backend.capability import Capability
            # Clean up old LSM files
            for path in list(self._lsm_files):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            self._lsm_files.clear()
            # Derive current capability set from the capability manager
            caps = set()
            if self.capability_manager is not None:
                for c in self.capability_manager.get_capabilities(container.id):
                    caps.add(c)
            policy = build_lsm_policy(container.id, caps)
            warnings = lsm_audit(policy)
            for w in warnings:
                logger.warning(f"LSM audit ({container.id}): {w}")
            # Write AppArmor profile
            aa_dir = Path(tempfile.mkdtemp(prefix=f"nyrqis-aa-{container.id}-"))
            aa_profile = AppArmorProfile(policy)
            aa_path = str(aa_dir / f"nyrqis.{container.id}")
            aa_profile.write(aa_path)
            self._lsm_files.append(aa_path)
            container.config.aa_profile = aa_path
            # Write SELinux module
            se_dir = Path(tempfile.mkdtemp(prefix=f"nyrqis-se-{container.id}-"))
            se_policy = SEPolicy(policy)
            se_paths = se_policy.write(str(se_dir))
            for p in se_paths.values():
                self._lsm_files.append(p)
            container.config.se_module_dir = str(se_dir)
            # Persist the policy for audit
            policy_json = os.path.join(str(se_dir), "lsm_policy.json")
            with open(policy_json, "w", encoding="utf-8") as f:
                json.dump(policy.to_dict(), f, indent=2)
            self._lsm_files.append(policy_json)
            logger.info(
                f"LSM policy reloaded for {container.id} "
                f"({len(caps)} capabilities)"
            )
            return True
        except ImportError:
            logger.debug(f"LSM module not available for {container.id}")
            return False
        except Exception as e:
            logger.warning(f"LSM policy reload failed for {container.id}: {e}")
            return False

    def _ipc_register(self, container: Container) -> None:
        """Register a spawned container in the transport sender registry.

        Direct-syscall path only: the container's command is exec'd as
        its PID-1, so ``container.pid`` (the host-visible pid) is exactly
        the pid the kernel attaches to the container's datagrams
        (``SCM_CREDENTIALS`` reports the global pid — probe-verified
        2026-08-14). The legacy ``unshare(1)`` path is NOT tracked: the
        command runs as a grandchild with a different pid, and its
        datagrams fail closed (dropped as unknown) unless the service
        supplies its own mapping — documented in ``ipc/registry.py``.
        """
        if self.ipc_registry is None or container._direct_launcher_pid is None:
            return
        if container.pid is not None:
            self.ipc_registry.register(container.pid, container.id)

    def revoke_and_reload(self, container: Container,
                          capability) -> bool:
        """Revoke a capability from a running container and reload its LSM policy.

        This is the runtime capability-revocation entry point: it updates
        the capability manager's grant set AND regenerates the container's
        AppArmor/SELinux policies so the new capability set takes effect
        immediately.

        Seccomp-BPF filters are one-shot (cannot be removed or weakened
        once installed), so the seccomp layer is NOT changed — only the
        LSM layer is updated.  A full seccomp reload requires a container
        restart.

        Args:
            container: The running container.
            capability: A ``Capability`` enum value to revoke.

        Returns:
            True if the revocation and reload succeeded.
        """
        from backend.capability import Capability
        if isinstance(capability, str):
            capability = Capability(capability)
        if self.capability_manager is not None:
            self.capability_manager.revoke_capability(
                container.id, capability
            )
        return self.reload_policy(container)

    def _ipc_unregister(self, container: Container) -> None:
        """Drop the container's pid from the transport sender registry
        when it terminates (idempotent)."""
        if self.ipc_registry is None:
            return
        self.ipc_registry.unregister(container.pid)

    def container_stats(self, container: Container) -> Dict[str, Any]:
        """Read live resource usage stats from a running container's cgroup.

        Returns a dict with memory, CPU, and PID stats read from the
        kernel cgroup files.  On cgroups v2 the unified hierarchy provides
        ``memory.current``, ``cpu.stat``, and ``pids.current``; on v1 the
        backend reads ``memory.usage_in_bytes``, ``cpuacct.usage``, and
        ``pids.current`` (or ``pids.max``) from the respective
        controller hierarchies.

        When the container has no cgroup paths (setup failed) or is not
        running, the returned dict contains ``"available": false``.
        """
        stats: Dict[str, Any] = {
            "container_id": container.id,
            "state": container.state.value,
            "available": False,
            "pid": container.pid,
            "uptime_s": None,
        }
        if container.started_at is not None:
            stats["uptime_s"] = round(time.time() - container.started_at, 3)

        if container.state != ContainerState.RUNNING or not container.cgroup_paths:
            return stats

        stats["available"] = True
        cgroup_root = Path(container.cgroup_paths[0])

        def _read_int(path: Path) -> Optional[int]:
            try:
                return int(path.read_text().strip())
            except (OSError, ValueError):
                return None

        def _read_cpu_stat(path: Path) -> Dict[str, int]:
            """Parse cpu.stat key-value pairs."""
            result: Dict[str, int] = {}
            try:
                for line in path.read_text().splitlines():
                    parts = line.split()
                    if len(parts) == 2:
                        result[parts[0]] = int(parts[1])
            except (OSError, ValueError):
                pass
            return result

        if self.use_cgroups_v2:
            # Cgroups v2 unified hierarchy
            mem = _read_int(cgroup_root / "memory.current")
            if mem is not None:
                stats["memory_bytes"] = mem
            mem_max = _read_int(cgroup_root / "memory.max")
            if mem_max is not None and mem_max != 0x7FFFFFFFFFFFFFFF:
                stats["memory_limit_bytes"] = mem_max
            elif mem_max == 0x7FFFFFFFFFFFFFFF:
                stats["memory_limit_bytes"] = None  # unlimited

            cpu = _read_cpu_stat(cgroup_root / "cpu.stat")
            if cpu:
                # usage_usec is the kernel's cumulative CPU time
                stats["cpu_usage_usec"] = cpu.get("usage_usec", 0)
                stats["cpu_user_usec"] = cpu.get("user_usec", 0)
                stats["cpu_system_usec"] = cpu.get("system_usec", 0)
                nr = cpu.get("nr_periods", 0)
                throttled = cpu.get("nr_throttled", 0)
                if nr > 0:
                    stats["cpu_throttle_pct"] = round(
                        throttled / nr * 100, 2
                    )

            pids = _read_int(cgroup_root / "pids.current")
            if pids is not None:
                stats["pids_current"] = pids
            pids_max = _read_int(cgroup_root / "pids.max")
            if pids_max is not None and pids_max != 0x7FFFFFFFFFFFFFFF:
                stats["pids_limit"] = pids_max

            io_r = _read_int(cgroup_root / "io.stat")
            if io_r is not None:
                stats["io_read_bytes"] = io_r
        else:
            # Cgroups v1 — read from each controller hierarchy
            for cgroup_path in container.cgroup_paths:
                cg = Path(cgroup_path)
                # memory controller
                mem = _read_int(cg / "memory.usage_in_bytes")
                if mem is not None:
                    stats["memory_bytes"] = mem
                mem_limit = _read_int(cg / "memory.limit_in_bytes")
                if mem_limit is not None and mem_limit < (1 << 62):
                    stats["memory_limit_bytes"] = mem_limit
                # cpuacct controller
                cpu_ns = _read_int(cg / "cpuacct.usage")
                if cpu_ns is not None:
                    stats["cpu_usage_usec"] = cpu_ns // 1000
                # pids controller
                pids = _read_int(cg / "pids.current")
                if pids is not None:
                    stats["pids_current"] = pids
                pids_max = _read_int(cg / "pids.max")
                if pids_max is not None and pids_max != 0x7FFFFFFFFFFFFFFF:
                    stats["pids_limit"] = pids_max

        return stats

    def container_resource_limits(self, container: Container) -> Dict[str, Any]:
        """Check resource usage against configured limits and report alerts.

        Uses configurable thresholds from ContainerConfig and tracks
        alert history. Compares cgroup stats against limits and returns
        alert levels for memory, PID, and CPU throttle.

        Returns:
            Dict with ``memory_alert`` (ok/warning/critical/at_limit),
            ``pid_alert``, ``cpu_throttle_alert``, ``memory_pct``,
            ``pid_pct``, ``alerts`` (new alerts fired), and the raw
            ``stats`` snapshot.
        """
        stats = self.container_stats(container)
        result: Dict[str, Any] = {
            "container_id": container.id,
            "available": stats.get("available", False),
            "memory_alert": "ok",
            "pid_alert": "ok",
            "cpu_throttle_alert": "ok",
            "memory_pct": None,
            "pid_pct": None,
            "cpu_throttle_pct": None,
            "alerts": [],
            "stats": stats,
        }

        if not stats.get("available"):
            return result

        cfg = container.config

        # Memory check (uses configurable thresholds)
        mem_bytes = stats.get("memory_bytes")
        mem_limit = stats.get("memory_limit_bytes")
        configured_limit_mb = cfg.limits.memory_mb

        if mem_bytes is not None and configured_limit_mb > 0:
            limit_bytes = (mem_limit if mem_limit else
                           configured_limit_mb * 1024 * 1024)
            pct = round(mem_bytes / limit_bytes * 100, 1) if limit_bytes > 0 else 0
            result["memory_pct"] = pct
            if pct >= 100:
                result["memory_alert"] = "at_limit"
                alert = self._fire_alert(container, "memory", "at_limit",
                                         f"{pct}% of limit")
                if alert:
                    result["alerts"].append(alert)
            elif pct >= cfg.alert_memory_critical:
                result["memory_alert"] = "critical"
                alert = self._fire_alert(container, "memory", "critical",
                                         f"{pct}% of limit")
                if alert:
                    result["alerts"].append(alert)
            elif pct >= cfg.alert_memory_warning:
                result["memory_alert"] = "warning"

        # PID check (uses configurable thresholds)
        pids = stats.get("pids_current")
        pid_limit = cfg.limits.pid_limit
        if pids is not None and pid_limit > 0:
            pct = round(pids / pid_limit * 100, 1)
            result["pid_pct"] = pct
            if pct >= 100:
                result["pid_alert"] = "at_limit"
                alert = self._fire_alert(container, "pid", "at_limit",
                                         f"{pids}/{pid_limit}")
                if alert:
                    result["alerts"].append(alert)
            elif pct >= cfg.alert_pid_critical:
                result["pid_alert"] = "critical"
                alert = self._fire_alert(container, "pid", "critical",
                                         f"{pids}/{pid_limit}")
                if alert:
                    result["alerts"].append(alert)
            elif pct >= cfg.alert_pid_warning:
                result["pid_alert"] = "warning"

        # CPU throttle check
        throttle_pct = stats.get("cpu_throttle_pct")
        if throttle_pct is not None:
            result["cpu_throttle_pct"] = throttle_pct
            if throttle_pct >= cfg.alert_cpu_throttle:
                result["cpu_throttle_alert"] = "warning"
                alert = self._fire_alert(container, "cpu_throttle",
                                         "warning",
                                         f"{throttle_pct}% throttled")
                if alert:
                    result["alerts"].append(alert)

        return result

    def _fire_alert(
        self, container: Container, resource: str,
        level: str, detail: str,
    ) -> Optional[Dict[str, Any]]:
        """Record an alert in the container's history.

        Deduplicates: same resource+level within 60 seconds is skipped.

        Returns:
            The alert dict if fired, None if deduplicated.
        """
        now = time.time()
        # Dedup check: skip if same resource+level within 60s
        for prev in reversed(container._alert_history):
            if (prev["resource"] == resource and
                    prev["level"] == level and
                    now - prev["timestamp"] < 60):
                return None

        alert: Dict[str, Any] = {
            "timestamp": now,
            "resource": resource,
            "level": level,
            "detail": detail,
            "container_id": container.id,
        }
        container._alert_history.append(alert)
        # Keep at most 200 alerts per container
        if len(container._alert_history) > 200:
            container._alert_history = container._alert_history[-200:]
        logger.warning(
            "alert: %s %s=%s %s",
            container.id, resource, level, detail,
        )
        self._record_event(
            "alert", container.id,
            f"{resource}={level}: {detail}",
        )
        return alert

    def get_alert_history(
        self, container: Container, tail: Optional[int] = None,
        resource: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the alert history for a container.

        Args:
            container: Target container.
            tail: If set, return only the last N alerts.
            resource: Filter by resource type.

        Returns:
            List of alert dicts.
        """
        history = container._alert_history
        if resource:
            history = [a for a in history if a["resource"] == resource]
        if tail is not None:
            return list(history[-tail:])
        return list(history)

    def clear_alert_history(self, container: Container) -> int:
        """Clear the alert history for a container.

        Returns:
            Number of alerts cleared.
        """
        count = len(container._alert_history)
        container._alert_history.clear()
        return count

    def set_alert_thresholds(
        self, container: Container,
        memory_warning: Optional[float] = None,
        memory_critical: Optional[float] = None,
        pid_warning: Optional[float] = None,
        pid_critical: Optional[float] = None,
        cpu_throttle: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Update alert thresholds for a container.

        Args:
            container: Target container.
            memory_warning: Memory warning threshold (%).
            memory_critical: Memory critical threshold (%).
            pid_warning: PID warning threshold (%).
            pid_critical: PID critical threshold (%).
            cpu_throttle: CPU throttle warning threshold (%).

        Returns:
            Dict with the updated thresholds.
        """
        cfg = container.config
        if memory_warning is not None:
            cfg.alert_memory_warning = memory_warning
        if memory_critical is not None:
            cfg.alert_memory_critical = memory_critical
        if pid_warning is not None:
            cfg.alert_pid_warning = pid_warning
        if pid_critical is not None:
            cfg.alert_pid_critical = pid_critical
        if cpu_throttle is not None:
            cfg.alert_cpu_throttle = cpu_throttle
        return {
            "container_id": container.id,
            "alert_memory_warning": cfg.alert_memory_warning,
            "alert_memory_critical": cfg.alert_memory_critical,
            "alert_pid_warning": cfg.alert_pid_warning,
            "alert_pid_critical": cfg.alert_pid_critical,
            "alert_cpu_throttle": cfg.alert_cpu_throttle,
        }

    # ------------------------------------------------------------------
    # OOM killer protection
    # ------------------------------------------------------------------

    def apply_oom_protection(self, container: Container) -> bool:
        """Apply OOM killer protection settings to a container.

        Writes oom_score_adj and oom_kill_disable to cgroup files.
        Also configures memory.swap.max if specified.

        Args:
            container: Target container with cgroup paths.

        Returns:
            True if all settings were applied successfully.
        """
        if not container.cgroup_paths:
            return False

        cgroup_path = Path(container.cgroup_paths[0])
        limits = container.config.limits
        success = True

        # oom_score_adj (controls OOM killer priority)
        oom_adj = max(-1000, min(1000, limits.oom_score_adj))
        try:
            (cgroup_path / "memory.oom.group").write_text(
                "1" if limits.oom_kill_disable else "0"
            )
            logger.debug(
                "apply_oom_protection: %s oom.group=%s",
                container.id, "1" if limits.oom_kill_disable else "0",
            )
        except OSError as e:
            logger.debug(
                "apply_oom_protection: %s oom.group failed: %s",
                container.id, e,
            )
            # Not all kernels support this; not fatal

        # Set oom_score_adj via /proc/<pid>/oom_score_adj
        if container.pid is not None:
            try:
                oom_adj_path = f"/proc/{container.pid}/oom_score_adj"
                with open(oom_adj_path, "w") as f:
                    f.write(str(oom_adj))
                logger.debug(
                    "apply_oom_protection: %s oom_score_adj=%d",
                    container.id, oom_adj,
                )
            except (OSError, IOError) as e:
                logger.warning(
                    "apply_oom_protection: %s oom_score_adj failed: %s",
                    container.id, e,
                )
                success = False

        # memory.swap.max (cgroup2 only)
        if self.use_cgroups_v2 and limits.memory_swap_max is not None:
            try:
                swap_path = cgroup_path / "memory.swap.max"
                swap_path.write_text(str(limits.memory_swap_max))
                logger.debug(
                    "apply_oom_protection: %s swap.max=%d",
                    container.id, limits.memory_swap_max,
                )
            except OSError as e:
                logger.debug(
                    "apply_oom_protection: %s swap.max failed: %s",
                    container.id, e,
                )

        return success

    def get_oom_status(self, container: Container) -> Dict[str, Any]:
        """Get OOM status and configuration for a container.

        Returns:
            Dict with OOM settings, events, and cgroup OOM stats.
        """
        cfg = container.config.limits
        status: Dict[str, Any] = {
            "container_id": container.id,
            "oom_score_adj": cfg.oom_score_adj,
            "oom_kill_disable": cfg.oom_kill_disable,
            "memory_swap_max": cfg.memory_swap_max,
            "oom_events": list(container._oom_events),
            "oom_event_count": len(container._oom_events),
        }

        # Read cgroup OOM stats if available
        if container.cgroup_paths:
            cgroup_path = Path(container.cgroup_paths[0])
            if self.use_cgroups_v2:
                try:
                    oom_group = (cgroup_path / "memory.oom.group").read_text().strip()
                    status["oom_group"] = oom_group == "1"
                except (OSError, IOError):
                    status["oom_group"] = None
                try:
                    swap_max = (cgroup_path / "memory.swap.max").read_text().strip()
                    status["cgroup_swap_max"] = swap_max
                except (OSError, IOError):
                    status["cgroup_swap_max"] = None

        return status

    def record_oom_event(
        self, container: Container, detail: str = "",
    ) -> Dict[str, Any]:
        """Record an OOM event for a container.

        Called when an OOM kill is detected (via dmesg monitoring
        or cgroup memory events).

        Args:
            container: Target container.
            detail: Optional detail about the OOM event.

        Returns:
            The recorded event dict.
        """
        event: Dict[str, Any] = {
            "timestamp": time.time(),
            "container_id": container.id,
            "detail": detail,
        }
        container._oom_events.append(event)
        # Keep at most 50 OOM events per container
        if len(container._oom_events) > 50:
            container._oom_events = container._oom_events[-50:]
        self._record_event("oom", container.id, detail)
        logger.warning("oom_event: %s %s", container.id, detail)
        return event

    def get_oom_events(
        self, container: Container, tail: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get OOM events for a container.

        Args:
            container: Target container.
            tail: If set, return only the last N events.

        Returns:
            List of OOM event dicts.
        """
        events = container._oom_events
        if tail is not None:
            return list(events[-tail:])
        return list(events)

    def set_oom_protection(
        self, container: Container,
        oom_score_adj: Optional[int] = None,
        oom_kill_disable: Optional[bool] = None,
        memory_swap_max: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update OOM protection settings for a container.

        Args:
            container: Target container.
            oom_score_adj: -1000 to 1000 (lower = less likely to OOM).
            oom_kill_disable: True to disable OOM killer.
            memory_swap_max: Max swap in bytes (None=unlimited, 0=no swap).

        Returns:
            Dict with updated settings.
        """
        cfg = container.config.limits
        if oom_score_adj is not None:
            cfg.oom_score_adj = max(-1000, min(1000, oom_score_adj))
        if oom_kill_disable is not None:
            cfg.oom_kill_disable = oom_kill_disable
        if memory_swap_max is not None:
            cfg.memory_swap_max = memory_swap_max
        # Apply immediately if container is running
        if container.pid is not None:
            self.apply_oom_protection(container)
        return {
            "container_id": container.id,
            "oom_score_adj": cfg.oom_score_adj,
            "oom_kill_disable": cfg.oom_kill_disable,
            "memory_swap_max": cfg.memory_swap_max,
        }

    # ------------------------------------------------------------------
    # Resource usage history (time-series)
    # ------------------------------------------------------------------

    def _init_resource_history(self, container: Container) -> None:
        """Initialize the resource history buffer for a container."""
        if not hasattr(self, "_resource_history"):
            self._resource_history: Dict[str, List[Dict[str, Any]]] = {}
        if container.id not in self._resource_history:
            self._resource_history[container.id] = []

    def record_resource_sample(
        self, container: Container,
    ) -> Optional[Dict[str, Any]]:
        """Take a resource usage sample and append to history.

        Returns the sample dict (with ``timestamp``, ``memory_bytes``,
        ``cpu_usage_usec``, ``pids_current``), or None if stats are
        unavailable.
        """
        stats = self.container_stats(container)
        if not stats.get("available"):
            return None
        sample: Dict[str, Any] = {
            "timestamp": time.time(),
            "memory_bytes": stats.get("memory_bytes", 0),
            "cpu_usage_usec": stats.get("cpu_usage_usec", 0),
            "pids_current": stats.get("pids_current", 0),
        }
        self._init_resource_history(container)
        self._resource_history[container.id].append(sample)
        # Keep at most 1000 samples per container
        if len(self._resource_history[container.id]) > 1000:
            self._resource_history[container.id] = \
                self._resource_history[container.id][-1000:]
        return sample

    def get_resource_history(
        self, container: Container,
        tail: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return the resource usage history for a container.

        Args:
            container: Target container.
            tail: If set, return only the last N samples.

        Returns:
            List of sample dicts with ``timestamp``, ``memory_bytes``,
            ``cpu_usage_usec``, ``pids_current``.
        """
        self._init_resource_history(container)
        history = self._resource_history[container.id]
        if tail is not None:
            return list(history[-tail:])
        return list(history)

    def start_resource_recording(
        self, container: Container,
        interval: float = 5.0,
    ) -> None:
        """Start periodic resource usage sampling.

        Spawns a daemon thread that samples resource usage at the
        given interval (in seconds).

        Args:
            container: Target container.
            interval: Seconds between samples (default 5.0).
        """
        self._init_resource_history(container)
        stop_event = threading.Event()
        container._resource_stop = stop_event

        def _recorder():
            while not stop_event.wait(timeout=interval):
                if container.state != ContainerState.RUNNING:
                    break
                self.record_resource_sample(container)

        t = threading.Thread(
            target=_recorder,
            name=f"resource-recorder-{container.id}",
            daemon=True,
        )
        t.start()
        container._resource_thread = t
        logger.info(
            "start_resource_recording: %s (interval=%.1fs)",
            container.id, interval,
        )

    def stop_resource_recording(self, container: Container) -> None:
        """Stop periodic resource sampling for a container."""
        if hasattr(container, "_resource_stop") and container._resource_stop is not None:
            container._resource_stop.set()
            logger.debug("stop_resource_recording: %s", container.id)

    # ------------------------------------------------------------------
    # Labels / metadata management
    # ------------------------------------------------------------------

    def set_label(self, container: Container, key: str, value: str) -> None:
        """Set a label on a container.

        Labels are key-value metadata tags for organizing and
        filtering containers (e.g., ``app=web``, ``env=prod``).

        Args:
            container: Target container.
            key: Label key (e.g., "app", "env", "team").
            value: Label value.
        """
        container.config.labels[key] = value
        logger.debug("set_label: %s %s=%s", container.id, key, value)

    def unset_label(self, container: Container, key: str) -> bool:
        """Remove a label from a container.

        Returns True if the key existed and was removed.
        """
        existed = key in container.config.labels
        container.config.labels.pop(key, None)
        if existed:
            logger.debug("unset_label: %s %s", container.id, key)
        return existed

    def get_label(self, container: Container, key: str) -> Optional[str]:
        """Get a label value, or None if not set."""
        return container.config.labels.get(key)

    def list_labels(self, container: Container) -> Dict[str, str]:
        """Return a copy of all labels for a container."""
        return dict(container.config.labels)

    def filter_by_labels(
        self, labels: Dict[str, str],
    ) -> List[Container]:
        """Find containers matching all given label key-value pairs.

        Args:
            labels: Dict of required label key-value pairs.

        Returns:
            List of containers whose labels are a superset of the
            given labels.
        """
        result: List[Container] = []
        for c in self.containers.values():
            c_labels = c.config.labels
            if all(c_labels.get(k) == v for k, v in labels.items()):
                result.append(c)
        return result

    def set_nice(self, container: Container, nice_value: int) -> bool:
        """Set the nice value (priority) for a running container.

        Uses ``setpriority(2)`` via ``os.setpriority()`` to adjust
        the scheduling priority of the container's PID-1 init process.
        A negative nice value requires root or CAP_SYS_NICE.

        Args:
            container: A running container with a valid PID.
            nice_value: Priority from -20 (highest) to 19 (lowest).

        Returns:
            True if the nice value was set successfully.
        """
        if not (-20 <= nice_value <= 19):
            raise ValueError(f"nice value must be -20..19, got {nice_value}")
        if container.pid is None:
            raise ValueError("Container has no PID")

        target_pid = container._init_pid or container.pid
        try:
            os.setpriority(os.PRIO_PROCESS, target_pid, nice_value)
            container.config.nice_value = nice_value
            self._record_event("nice_set", container.id,
                               f"nice={nice_value}, pid={target_pid}")
            logger.info(
                "nice set for %s: %d (pid=%d)",
                container.id, nice_value, target_pid,
            )
            return True
        except OSError as e:
            logger.warning(
                "nice set failed for %s: %s", container.id, e,
            )
            return False

    def set_cpu_affinity(self, container: Container,
                          cores: List[int]) -> bool:
        """Set CPU affinity for a running container.

        Uses ``sched_setaffinity(2)`` via ``os.sched_setaffinity()`` to
        pin the container's PID-1 init to specific CPU cores.

        Args:
            container: A running container with a valid PID.
            cores: List of CPU core IDs (0-indexed).

        Returns:
            True if affinity was set successfully.
        """
        if not cores:
            raise ValueError("cores list must not be empty")
        if container.pid is None:
            raise ValueError("Container has no PID")

        target_pid = container._init_pid or container.pid
        try:
            os.sched_setaffinity(target_pid, set(cores))
            container.config.cpu_affinity = list(cores)
            self._record_event("affinity_set", container.id,
                               f"cores={cores}, pid={target_pid}")
            logger.info(
                "CPU affinity set for %s: cores=%s (pid=%d)",
                container.id, cores, target_pid,
            )
            return True
        except OSError as e:
            logger.warning(
                "CPU affinity set failed for %s: %s", container.id, e,
            )
            return False

    def get_scheduling(self, container: Container) -> Dict[str, Any]:
        """Get the current scheduling parameters for a container.

        Returns:
            Dict with ``nice_value``, ``cpu_affinity`` (current cores
            from ``sched_getaffinity``), and ``cpu_count``.
        """
        result: Dict[str, Any] = {
            "container_id": container.id,
            "nice_value": container.config.nice_value,
            "cpu_affinity_config": container.config.cpu_affinity,
            "cpu_count": os.cpu_count(),
        }
        target_pid = container._init_pid or container.pid
        if target_pid is not None:
            try:
                current_affinity = sorted(
                    os.sched_getaffinity(target_pid)
                )
                result["cpu_affinity_current"] = current_affinity
            except OSError:
                result["cpu_affinity_current"] = None
            try:
                result["nice_value_current"] = os.getpriority(
                    os.PRIO_PROCESS, target_pid,
                )
            except OSError:
                result["nice_value_current"] = None
        else:
            result["cpu_affinity_current"] = None
            result["nice_value_current"] = None
        return result

    # ------------------------------------------------------------------
    # Resource limits hot-update (modify at runtime)
    # ------------------------------------------------------------------

    def update_resource_limits(
        self, container: Container,
        memory_mb: Optional[int] = None,
        pid_limit: Optional[int] = None,
        cpu_quota_us: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update resource limits for a running container at runtime.

        Writes new values to the container's cgroup files so the kernel
        enforces the updated limits immediately.  The container must
        have cgroup paths (i.e., be spawned with cgroup support).

        Args:
            container: Target container (must be RUNNING or SUSPENDED).
            memory_mb: New memory limit in MiB (None = no change).
            pid_limit: New PID limit (None = no change).
            cpu_quota_us: New CPU quota in microseconds per period
                (None = no change, 0 = unlimited).

        Returns:
            Dict with ``updated`` list of changed limits and
            ``previous`` dict of old values.

        Raises:
            ValueError: If container has no cgroup paths.
        """
        if not container.cgroup_paths:
            raise ValueError(
                f"container {container.id} has no cgroup paths; "
                "cannot update limits at runtime"
            )

        # Capture previous values
        previous = {
            "memory_mb": container.config.limits.memory_mb,
            "pid_limit": container.config.limits.pid_limit,
            "cpu_quota_us": container.config.limits.cpu_quota_us,
        }

        updated: List[str] = []
        cgroup_path = Path(container.cgroup_paths[0])

        if self.use_cgroups_v2:
            # Cgroups v2: write to unified hierarchy
            if memory_mb is not None:
                limit_bytes = memory_mb * 1024 * 1024
                try:
                    (cgroup_path / "memory.max").write_text(
                        str(limit_bytes)
                    )
                    container.config.limits.memory_mb = memory_mb
                    updated.append("memory_mb")
                    logger.info(
                        "update_resource_limits: %s memory → %d MiB",
                        container.id, memory_mb,
                    )
                except OSError as e:
                    logger.warning(
                        "update_resource_limits: %s memory write failed: %s",
                        container.id, e,
                    )

            if pid_limit is not None:
                try:
                    (cgroup_path / "pids.max").write_text(
                        str(pid_limit)
                    )
                    container.config.limits.pid_limit = pid_limit
                    updated.append("pid_limit")
                    logger.info(
                        "update_resource_limits: %s pids → %d",
                        container.id, pid_limit,
                    )
                except OSError as e:
                    logger.warning(
                        "update_resource_limits: %s pids write failed: %s",
                        container.id, e,
                    )

            if cpu_quota_us is not None:
                period = container.config.limits.cpu_period_us
                if cpu_quota_us == 0:
                    # Unlimited: write "max 100000"
                    cpu_max = "max"
                else:
                    cpu_max = f"{cpu_quota_us} {period}"
                try:
                    (cgroup_path / "cpu.max").write_text(cpu_max)
                    container.config.limits.cpu_quota_us = (
                        cpu_quota_us if cpu_quota_us > 0 else None
                    )
                    updated.append("cpu_quota_us")
                    logger.info(
                        "update_resource_limits: %s cpu → %s",
                        container.id, cpu_max,
                    )
                except OSError as e:
                    logger.warning(
                        "update_resource_limits: %s cpu write failed: %s",
                        container.id, e,
                    )
        else:
            # Cgroups v1: write to each controller
            for cgroup_dir in container.cgroup_paths:
                cg = Path(cgroup_dir)
                if memory_mb is not None:
                    limit_bytes = memory_mb * 1024 * 1024
                    mem_file = cg / "memory.limit_in_bytes"
                    if mem_file.exists():
                        try:
                            mem_file.write_text(str(limit_bytes))
                            container.config.limits.memory_mb = memory_mb
                            updated.append("memory_mb")
                        except OSError as e:
                            logger.warning(
                                "update_resource_limits: v1 memory failed: %s",
                                e,
                            )
                if pid_limit is not None:
                    pids_file = cg / "pids.max"
                    if pids_file.exists():
                        try:
                            pids_file.write_text(str(pid_limit))
                            container.config.limits.pid_limit = pid_limit
                            updated.append("pid_limit")
                        except OSError as e:
                            logger.warning(
                                "update_resource_limits: v1 pids failed: %s",
                                e,
                            )

        if updated:
            self._record_event(
                "limits_updated", container.id,
                f"updated={updated}",
            )

        return {
            "container_id": container.id,
            "updated": updated,
            "previous": previous,
        }

    # ------------------------------------------------------------------
    # Cgroup2 advanced enforcement
    # ------------------------------------------------------------------

    def apply_cgroup2_advanced(self, container: Container) -> bool:
        """Apply advanced cgroup2 limits (cpu.weight, memory.high, io.max).

        These are cgroup2-only features that provide finer-grained
        resource control beyond the basic memory/pid/cpu limits.

        Args:
            container: Target container with cgroup paths.

        Returns:
            True if all applicable limits were applied successfully.
        """
        if not self.use_cgroups_v2 or not container.cgroup_paths:
            return False

        cgroup_path = Path(container.cgroup_paths[0])
        limits = container.config.limits
        success = True

        # CPU weight (proportional sharing, 1-10000, default 100)
        if limits.cpu_weight is not None:
            weight = max(1, min(10000, limits.cpu_weight))
            try:
                (cgroup_path / "cpu.weight").write_text(str(weight))
                logger.debug(
                    "apply_cgroup2_advanced: %s cpu.weight=%d",
                    container.id, weight,
                )
            except OSError as e:
                logger.warning(
                    "apply_cgroup2_advanced: %s cpu.weight failed: %s",
                    container.id, e,
                )
                success = False

        # Memory high watermark (soft limit, triggers pressure)
        if limits.memory_high is not None:
            try:
                (cgroup_path / "memory.high").write_text(
                    str(limits.memory_high)
                )
                logger.debug(
                    "apply_cgroup2_advanced: %s memory.high=%d",
                    container.id, limits.memory_high,
                )
            except OSError as e:
                logger.warning(
                    "apply_cgroup2_advanced: %s memory.high failed: %s",
                    container.id, e,
                )
                success = False

        # IO max (bandwidth limiting)
        io_parts: List[str] = []
        if limits.io_max_rbps is not None:
            io_parts.append(f"rbps={limits.io_max_rbps}")
        if limits.io_max_wbps is not None:
            io_parts.append(f"wbps={limits.io_max_wbps}")
        if io_parts:
            # Format: "MAJ:MIN rbps=X wbps=Y" (use device 0:0 for all)
            io_value = f"0:0 {' '.join(io_parts)}"
            try:
                (cgroup_path / "io.max").write_text(io_value)
                logger.debug(
                    "apply_cgroup2_advanced: %s io.max=%s",
                    container.id, io_value,
                )
            except OSError as e:
                logger.warning(
                    "apply_cgroup2_advanced: %s io.max failed: %s",
                    container.id, e,
                )
                success = False

        return success

    def get_cgroup2_status(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Read comprehensive cgroup2 status for a container.

        Returns all current cgroup2 file values for monitoring and
        verification.

        Args:
            container: Target container with cgroup paths.

        Returns:
            Dict with memory, CPU, PID, and IO status.
        """
        if not container.cgroup_paths:
            return {
                "container_id": container.id,
                "available": False,
            }

        cgroup_path = Path(container.cgroup_paths[0])
        status: Dict[str, Any] = {
            "container_id": container.id,
            "available": True,
            "cgroup_path": str(cgroup_path),
        }

        def _read(path: Path) -> Optional[str]:
            try:
                return path.read_text().strip()
            except (OSError, ValueError):
                return None

        if self.use_cgroups_v2:
            # Memory
            status["memory_current"] = _read(cgroup_path / "memory.current")
            status["memory_max"] = _read(cgroup_path / "memory.max")
            status["memory_high"] = _read(cgroup_path / "memory.high")
            status["memory_stat"] = _read(cgroup_path / "memory.stat")
            # CPU
            status["cpu_stat"] = _read(cgroup_path / "cpu.stat")
            status["cpu_weight"] = _read(cgroup_path / "cpu.weight")
            status["cpu_max"] = _read(cgroup_path / "cpu.max")
            # PIDs
            status["pids_current"] = _read(cgroup_path / "pids.current")
            status["pids_max"] = _read(cgroup_path / "pids.max")
            # IO
            status["io_stat"] = _read(cgroup_path / "io.stat")
            status["io_max"] = _read(cgroup_path / "io.max")
            # Pressure
            status["cpu_pressure"] = _read(cgroup_path / "cpu.pressure")
            status["io_pressure"] = _read(cgroup_path / "io.pressure")
            status["memory_pressure"] = _read(
                cgroup_path / "memory.pressure"
            )

        return status

    def verify_enforcement(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Verify that resource limits are being enforced.

        Compares current usage against configured limits and reports
        any violations or nearing-limit conditions.

        Args:
            container: Target container.

        Returns:
            Dict with ``enforced`` (bool), ``violations`` list,
            ``warnings`` list, and ``current`` usage snapshot.
        """
        stats = self.container_stats(container)
        result: Dict[str, Any] = {
            "container_id": container.id,
            "enforced": True,
            "violations": [],
            "warnings": [],
            "current": stats,
        }

        if not stats.get("available"):
            result["enforced"] = False
            return result

        limits = container.config.limits

        # Memory check
        mem = stats.get("memory_bytes")
        if mem is not None and limits.memory_mb > 0:
            limit_bytes = limits.memory_mb * 1024 * 1024
            pct = mem / limit_bytes * 100 if limit_bytes > 0 else 0
            if pct >= 100:
                result["violations"].append(
                    f"memory: {mem:,} bytes exceeds "
                    f"{limit_bytes:,} byte limit"
                )
            elif pct >= 90:
                result["warnings"].append(
                    f"memory: {pct:.1f}% of limit used"
                )

        # PID check
        pids = stats.get("pids_current")
        if pids is not None and limits.pid_limit > 0:
            pct = pids / limits.pid_limit * 100
            if pct >= 100:
                result["violations"].append(
                    f"pids: {pids} exceeds {limits.pid_limit} limit"
                )
            elif pct >= 90:
                result["warnings"].append(
                    f"pids: {pct:.1f}% of limit used"
                )

        # CPU throttle check
        throttle_pct = stats.get("cpu_throttle_pct")
        if throttle_pct is not None and throttle_pct > 0:
            if throttle_pct >= 50:
                result["warnings"].append(
                    f"cpu: {throttle_pct}% throttled"
                )

        if result["violations"]:
            result["enforced"] = False

        return result

    def apply_network_policy(self, container: Container) -> bool:
        """Apply the container's configured network policy.

        Uses iptables on the host to filter ingress/egress traffic
        on the container's veth interface.  Requires ``config.network``
        and ``config.network_policy`` to be set.

        Returns:
            True if policy was applied successfully.
        """
        if not container.config.network or not container.config.network_policy:
            return False
        try:
            from backend.network import apply_network_policy
            ok = apply_network_policy(
                container.id, container.config.network_policy,
            )
            if ok:
                self._record_event("network_policy_applied", container.id,
                                   str(container.config.network_policy))
            return ok
        except ImportError:
            logger.debug("network_policy: network module not available")
            return False
        except Exception as e:
            logger.warning("network_policy failed for %s: %s", container.id, e)
            return False

    def remove_network_policy(self, container: Container) -> bool:
        """Remove the network policy rules for a container."""
        try:
            from backend.network import remove_network_policy
            ok = remove_network_policy(container.id)
            if ok:
                self._record_event("network_policy_removed", container.id)
            return ok
        except ImportError:
            return False
        except Exception as e:
            logger.warning("remove_network_policy failed for %s: %s", container.id, e)
            return False

    def get_network_policy(self, container: Container) -> Optional[Dict[str, Any]]:
        """Get the current network policy rules for a container."""
        try:
            from backend.network import get_network_policy
            return get_network_policy(container.id)
        except ImportError:
            return None
        except Exception:
            return None

    def _start_log_capture(self, container: Container,
                           proc: subprocess.Popen) -> None:
        """Start background threads to capture stdout/stderr into ring buffers."""
        max_lines = container.config.log_max_lines
        container._stdout_buffer = RingBuffer(max_lines)
        container._stderr_buffer = RingBuffer(max_lines)

        def _reader(stream: Any, buf: RingBuffer, stream_name: str) -> None:
            try:
                for line in iter(stream.readline, b""):
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").rstrip("\n")
                    ts = time.time()
                    buf.append(f"[{ts:.3f}] {decoded}")
            except (OSError, ValueError):
                pass
            finally:
                try:
                    stream.close()
                except OSError:
                    pass

        if proc.stdout is not None:
            t = threading.Thread(
                target=_reader,
                args=(proc.stdout, container._stdout_buffer, "stdout"),
                daemon=True, name=f"{container.id}-stdout",
            )
            t.start()
            container._log_threads.append(t)

        if proc.stderr is not None:
            t = threading.Thread(
                target=_reader,
                args=(proc.stderr, container._stderr_buffer, "stderr"),
                daemon=True, name=f"{container.id}-stderr",
            )
            t.start()
            container._log_threads.append(t)

    def container_logs(self, container: Container,
                       tail: Optional[int] = None,
                       stream: str = "both") -> Dict[str, Any]:
        """Retrieve captured log lines for a container.

        Args:
            container: The container to read logs from.
            tail: If set, return only the last N lines per stream.
            stream: Which stream(s) to return: ``"stdout"``,
                    ``"stderr"``, or ``"both"`` (default).

        Returns:
            Dict with ``stdout`` and/or ``stderr`` line lists, and
            ``available`` indicating whether log capture is active.
        """
        result: Dict[str, Any] = {
            "container_id": container.id,
            "available": container._stdout_buffer is not None,
        }
        if container._stdout_buffer is None:
            result["stdout"] = []
            result["stderr"] = []
            return result

        if stream in ("stdout", "both"):
            result["stdout"] = container._stdout_buffer.get_lines(tail)
        if stream in ("stderr", "both"):
            result["stderr"] = container._stderr_buffer.get_lines(tail)
        return result

    def container_exec(self, container: Container, command: List[str],
                       timeout_s: float = 10.0) -> Dict[str, Any]:
        """Execute a command inside a running container's namespaces.

        Uses ``nsenter(1)`` to join the container's PID, mount, UTS,
        IPC, and (if present) network namespaces, then runs the given
        command.  The container must be in the RUNNING state with a
        valid host PID.

        Args:
            container: A running container.
            command: Command and arguments to execute.
            timeout_s: Maximum seconds to wait for the command.

        Returns:
            Dict with ``exit_code``, ``stdout``, ``stderr``.
        """
        if container.state != ContainerState.RUNNING:
            raise ValueError(
                f"Cannot exec in container in {container.state.value} state"
            )
        if container.pid is None:
            raise ValueError("Container has no PID")

        pid = container.pid
        nsenter = shutil.which("nsenter")
        if nsenter is None:
            raise RuntimeError("nsenter(1) not found — required for container exec")

        # Enter all the container's namespaces
        ns_args = [
            nsenter,
            f"--pid=/proc/{pid}/ns/pid",
            f"--mount=/proc/{pid}/ns/mnt",
            f"--uts=/proc/{pid}/ns/uts",
            f"--ipc=/proc/{pid}/ns/ipc",
        ]
        if container.config.network and container.network_ip:
            ns_args.append(f"--net=/proc/{pid}/ns/net")

        # Run as root inside the namespace (the namespace maps root)
        ns_args.extend(["--"])
        ns_args.extend(command)

        logger.debug("container_exec: %s %s", container.id, command)
        try:
            result = subprocess.run(
                ns_args,
                capture_output=True,
                timeout=timeout_s,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout.decode("utf-8", errors="replace"),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"command timed out after {timeout_s}s",
            }

    def container_top(self, container: Container,
                       sort_by: Optional[str] = None,
                       descending: bool = True,
                       max_depth: Optional[int] = None) -> List[Dict[str, Any]]:
        """List processes running inside a container with resource usage.

        Reads ``/proc/<pid>/task/<pid>/children`` to discover the
        container's process tree and ``/proc/<pid>/stat`` for each
        process's CPU time and state. Enhanced with per-process
        details: name, ppid, nice, threads, fd count, start time.

        Args:
            container: A running container with a valid host PID.
            sort_by: Optional field to sort by (pid, cpu, memory, rss).
            descending: Sort direction (default True).
            max_depth: Max tree depth to scan (None = unlimited).

        Returns:
            List of dicts, each with ``pid``, ``ppid``, ``state``,
            ``name``, ``cmd``, ``user_time_s``, ``system_time_s``,
            ``vsize_kb``, ``rss_kb``, ``nice``, ``threads``,
            ``fd_count``, ``start_time_s``.
        """
        if container.state != ContainerState.RUNNING:
            return []
        if container.pid is None:
            return []

        procs: List[Dict[str, Any]] = []
        pids_to_scan = [(container.pid, 0)]  # (pid, depth)
        seen_pids: set = set()
        page_size = os.sysconf("SC_PAGE_SIZE")

        try:
            clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        except (KeyError, OSError, AttributeError):
            clk_tck = 100

        while pids_to_scan:
            pid, depth = pids_to_scan.pop()
            if pid in seen_pids:
                continue
            seen_pids.add(pid)

            # Read /proc/<pid>/stat
            stat_path = f"/proc/{pid}/stat"
            try:
                with open(stat_path) as f:
                    stat_text = f.read()
            except (OSError, IOError):
                continue

            # Parse stat: find last ')' to skip comm (may contain spaces)
            close_paren = stat_text.rfind(")")
            if close_paren < 0:
                continue
            fields_after_comm = stat_text[close_paren + 2:].split()
            # Fields after comm:
            # [0]=state [1]=ppid [3]=threads [11]=utime [12]=stime
            # [17]=nice [20]=vsize [21]=rss [38]=starttime

            state = fields_after_comm[0] if len(fields_after_comm) > 0 else "?"
            ppid = int(fields_after_comm[1]) if len(fields_after_comm) > 1 else 0
            threads = int(fields_after_comm[17]) if len(fields_after_comm) > 17 else 1
            nice = int(fields_after_comm[17]) if len(fields_after_comm) > 17 else 0
            try:
                utime_ticks = int(fields_after_comm[11]) if len(fields_after_comm) > 11 else 0
                stime_ticks = int(fields_after_comm[12]) if len(fields_after_comm) > 12 else 0
                vsize = int(fields_after_comm[20]) if len(fields_after_comm) > 20 else 0
                rss_pages = int(fields_after_comm[21]) if len(fields_after_comm) > 21 else 0
                start_ticks = int(fields_after_comm[19]) if len(fields_after_comm) > 19 else 0
            except (ValueError, IndexError):
                utime_ticks = stime_ticks = vsize = rss_pages = start_ticks = 0
                threads = 1
                nice = 0

            # Read the process name from /proc/<pid>/comm
            name = ""
            try:
                with open(f"/proc/{pid}/comm") as f:
                    name = f.read().strip()
            except (OSError, IOError):
                pass

            # Read the command line
            cmdline = ""
            try:
                with open(f"/proc/{pid}/cmdline") as f:
                    cmdline = f.read(4096).replace("\x00", " ").strip()
            except (OSError, IOError):
                pass

            # Count file descriptors
            fd_count = 0
            try:
                fd_count = len(os.listdir(f"/proc/{pid}/fd"))
            except (OSError, IOError):
                pass

            # Calculate start time in seconds since boot
            start_time_s = 0.0
            if start_ticks > 0:
                try:
                    with open("/proc/uptime") as f:
                        uptime_text = f.read().split()[0]
                    uptime_s = float(uptime_text)
                    boot_time = time.time() - uptime_s
                    start_time_s = round(boot_time + start_ticks / clk_tck, 1)
                except (OSError, IOError, ValueError, IndexError):
                    pass

            procs.append({
                "pid": pid,
                "ppid": ppid,
                "state": state,
                "name": name,
                "cmd": cmdline or f"[{name or state}]",
                "user_time_s": round(utime_ticks / clk_tck, 3),
                "system_time_s": round(stime_ticks / clk_tck, 3),
                "vsize_kb": vsize // 1024,
                "rss_kb": rss_pages * (page_size // 1024),
                "nice": nice,
                "threads": threads,
                "fd_count": fd_count,
                "start_time_s": start_time_s,
                "depth": depth,
            })

            # Check depth limit
            if max_depth is not None and depth >= max_depth:
                continue

            # Discover children
            try:
                with open(f"/proc/{pid}/task/{pid}/children") as f:
                    children_text = f.read().strip()
                if children_text:
                    for child_pid_str in children_text.split():
                        pids_to_scan.append((int(child_pid_str), depth + 1))
            except (OSError, IOError, ValueError):
                pass

        # Sort if requested
        if sort_by:
            sort_key_map = {
                "pid": lambda p: p["pid"],
                "cpu": lambda p: p["user_time_s"] + p["system_time_s"],
                "memory": lambda p: p["vsize_kb"],
                "rss": lambda p: p["rss_kb"],
                "fd": lambda p: p["fd_count"],
                "threads": lambda p: p["threads"],
            }
            key_fn = sort_key_map.get(sort_by)
            if key_fn:
                procs.sort(key=key_fn, reverse=descending)

        return procs

    def container_top_summary(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Return a summary of processes in the container.

        Provides aggregate stats: total processes, total threads,
        total memory, total CPU time.

        Returns:
            Dict with ``total_processes``, ``total_threads``,
            ``total_rss_kb``, ``total_vsize_kb``, ``total_cpu_s``,
            ``states`` (count by state).
        """
        procs = self.container_top(container)
        if not procs:
            return {
                "container_id": container.id,
                "total_processes": 0,
                "total_threads": 0,
                "total_rss_kb": 0,
                "total_vsize_kb": 0,
                "total_cpu_s": 0.0,
                "states": {},
            }

        states: Dict[str, int] = {}
        total_threads = 0
        total_rss = 0
        total_vsize = 0
        total_cpu = 0.0

        for p in procs:
            st = p.get("state", "?")
            states[st] = states.get(st, 0) + 1
            total_threads += p.get("threads", 1)
            total_rss += p.get("rss_kb", 0)
            total_vsize += p.get("vsize_kb", 0)
            total_cpu += p.get("user_time_s", 0) + p.get("system_time_s", 0)

        return {
            "container_id": container.id,
            "total_processes": len(procs),
            "total_threads": total_threads,
            "total_rss_kb": total_rss,
            "total_vsize_kb": total_vsize,
            "total_cpu_s": round(total_cpu, 3),
            "states": states,
        }

    def container_dashboard(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Return a comprehensive resource dashboard for a container.

        Aggregates cgroup stats, process summary, resource limits,
        alerts, OOM status, labels, and health into a single view.

        Returns:
            Dict with all dashboard sections.
        """
        stats = self.container_stats(container)
        limits = self.container_resource_limits(container)
        top = self.container_top_summary(container)
        history = self.get_resource_history(container, tail=10)
        alerts = self.get_alert_history(container, tail=10)
        oom = self.get_oom_status(container)
        labels = self.list_labels(container)
        restart = self.get_restart_info(container)
        scheduling = self.get_scheduling(container)

        # Compute uptime
        uptime_s = None
        if container.started_at is not None:
            uptime_s = round(time.time() - container.started_at, 1)

        # Compute memory usage percent
        mem_pct = None
        if stats.get("available"):
            mem_bytes = stats.get("memory_bytes")
            mem_limit = stats.get("memory_limit_bytes")
            if mem_bytes is not None and mem_limit and mem_limit > 0:
                mem_pct = round(mem_bytes / mem_limit * 100, 1)

        return {
            "container_id": container.id,
            "state": container.state.value,
            "pid": container.pid,
            "uptime_s": uptime_s,
            # Resource stats
            "stats": stats,
            "limits": {
                "memory_mb": container.config.limits.memory_mb,
                "pid_limit": container.config.limits.pid_limit,
                "cpu_quota_us": container.config.limits.cpu_quota_us,
                "cpu_weight": container.config.limits.cpu_weight,
                "memory_high": container.config.limits.memory_high,
                "oom_score_adj": container.config.limits.oom_score_adj,
            },
            # Usage percentages
            "memory_pct": mem_pct,
            "memory_alert": limits.get("memory_alert", "ok"),
            "pid_pct": limits.get("pid_pct"),
            "pid_alert": limits.get("pid_alert", "ok"),
            "cpu_throttle_pct": limits.get("cpu_throttle_pct"),
            "cpu_throttle_alert": limits.get("cpu_throttle_alert", "ok"),
            # Process summary
            "processes": top,
            # Recent history
            "resource_history": history,
            "alert_history": alerts,
            # OOM
            "oom": {
                "score_adj": oom.get("oom_score_adj", 0),
                "kill_disable": oom.get("oom_kill_disable", False),
                "event_count": oom.get("oom_event_count", 0),
            },
            # Metadata
            "labels": labels,
            "restart": restart,
            "scheduling": {
                "nice_value": scheduling.get("nice_value", 0),
                "cpu_affinity": scheduling.get("cpu_affinity_current"),
            },
            # Network
            "network": {
                "enabled": container.config.network,
                "ip": container.network_ip,
            },
        }

    def dashboard_all(self) -> Dict[str, Any]:
        """Return a dashboard summary for all containers.

        Returns:
            Dict with ``total_containers``, ``by_state`` counts,
            ``total_memory_bytes``, ``total_pids``, and per-container
            summaries.
        """
        total_memory = 0
        total_pids = 0
        by_state: Dict[str, int] = {}
        containers: List[Dict[str, Any]] = []

        for c in self.containers.values():
            state = c.state.value
            by_state[state] = by_state.get(state, 0) + 1
            stats = self.container_stats(c)
            if stats.get("available"):
                total_memory += stats.get("memory_bytes", 0)
                total_pids += stats.get("pids_current", 0)
            containers.append({
                "id": c.id,
                "state": state,
                "pid": c.pid,
                "memory_bytes": stats.get("memory_bytes"),
                "pids_current": stats.get("pids_current"),
                "labels": self.list_labels(c),
            })

        return {
            "total_containers": len(self.containers),
            "by_state": by_state,
            "total_memory_bytes": total_memory,
            "total_pids": total_pids,
            "containers": containers,
        }

    # ------------------------------------------------------------------
    # Resource usage export (CSV/JSON)
    # ------------------------------------------------------------------

    def export_resource_history(
        self, container: Container,
        output_path: str,
        format: str = "json",
        tail: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Export resource usage history to a file.

        Supports JSON and CSV formats. JSON includes full metadata;
        CSV is a flat table suitable for spreadsheet import.

        Args:
            container: Target container.
            output_path: Path to write the export file.
            format: "json" or "csv".
            tail: If set, export only the last N samples.

        Returns:
            Dict with ``path``, ``format``, ``samples`` count,
            ``bytes_written``.

        Raises:
            ValueError: On invalid format.
        """
        if format not in ("json", "csv"):
            raise ValueError(f"invalid format {format!r}; must be 'json' or 'csv'")

        history = self.get_resource_history(container, tail=tail)
        stats = self.container_stats(container)
        limits = container.config.limits

        if format == "json":
            data = {
                "container_id": container.id,
                "state": container.state.value,
                "exported_at": time.time(),
                "limits": {
                    "memory_mb": limits.memory_mb,
                    "pid_limit": limits.pid_limit,
                    "cpu_quota_us": limits.cpu_quota_us,
                },
                "current_stats": stats,
                "history": history,
            }
            content = json.dumps(data, indent=2)
            with open(output_path, "w") as f:
                f.write(content)
            bytes_written = len(content.encode("utf-8"))
        else:  # csv
            import csv
            import io
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                "timestamp", "memory_bytes", "cpu_usage_usec",
                "pids_current",
            ])
            for sample in history:
                writer.writerow([
                    sample.get("timestamp", 0),
                    sample.get("memory_bytes", 0),
                    sample.get("cpu_usage_usec", 0),
                    sample.get("pids_current", 0),
                ])
            content = buf.getvalue()
            with open(output_path, "w") as f:
                f.write(content)
            bytes_written = len(content.encode("utf-8"))

        logger.info(
            "export_resource_history: %s → %s (%s, %d samples)",
            container.id, output_path, format, len(history),
        )
        return {
            "container_id": container.id,
            "path": output_path,
            "format": format,
            "samples": len(history),
            "bytes_written": bytes_written,
        }

    def export_container_snapshot(
        self, container: Container,
        output_path: str,
    ) -> Dict[str, Any]:
        """Export a complete container snapshot (config + stats + history).

        Creates a JSON file with the container's full state for
        debugging or archival purposes.

        Args:
            container: Target container.
            output_path: Path to write the snapshot.

        Returns:
            Dict with ``path``, ``bytes_written``.
        """
        dash = self.container_dashboard(container)
        # Add config details
        dash["config"] = {
            "name": container.config.name,
            "hostname": container.config.hostname,
            "command": container.config.command,
            "network": container.config.network,
            "rootfs": container.config.rootfs,
            "seccomp": container.config.seccomp,
            "default_deny": container.config.default_deny,
            "log_capture": container.config.log_capture,
            "restart_policy": container.config.restart_policy,
            "environment": container.config.environment,
            "labels": container.config.labels,
            "depends_on": container.config.depends_on,
        }
        dash["snapshot_time"] = time.time()
        content = json.dumps(dash, indent=2, default=str)
        with open(output_path, "w") as f:
            f.write(content)
        bytes_written = len(content.encode("utf-8"))
        logger.info(
            "export_container_snapshot: %s → %s",
            container.id, output_path,
        )
        return {
            "container_id": container.id,
            "path": output_path,
            "bytes_written": bytes_written,
        }

    def container_network_stats(self, container: Container) -> Optional[Dict[str, Any]]:
        """Get network interface stats for a container.

        Reads the host-side veth interface statistics to report
        bytes/packets in/out, errors, and drops.

        Args:
            container: The container to query.

        Returns:
            Dict with rx/tx stats or None if no veth exists.
        """
        if container.config.network and container.network_ip:
            try:
                from backend.network import get_network_stats
                return get_network_stats(container.id)
            except ImportError:
                return None
            except Exception as e:
                logger.debug(
                    "network_stats failed for %s: %s", container.id, e,
                )
                return None
        return None

    def list_images(self, base_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        """List available base images for overlay filesystems.

        Scans ``base_dir`` (default: a well-known ``images/`` directory
        relative to the working dir) for NyFS filesystem roots —
        directories containing ``state/metadata.json``.

        Args:
            base_dir: Optional override for the image directory.

        Returns:
            List of dicts with ``path``, ``name``, ``size_bytes``,
            ``inode_count``, and ``block_count``.
        """
        if base_dir is None:
            base_dir = os.path.join(os.getcwd(), "images")
        images: List[Dict[str, Any]] = []
        base = Path(base_dir)
        if not base.is_dir():
            return images

        for entry in sorted(base.iterdir()):
            if not entry.is_dir():
                continue
            meta = entry / "state" / "metadata.json"
            if not meta.is_file():
                continue

            info: Dict[str, Any] = {
                "path": str(entry),
                "name": entry.name,
                "size_bytes": 0,
                "inode_count": 0,
                "block_count": 0,
            }

            # Compute size from block files
            blocks_dir = entry / "state" / "blocks"
            if blocks_dir.is_dir():
                block_files = list(blocks_dir.iterdir())
                info["block_count"] = len(block_files)
                info["size_bytes"] = sum(
                    f.stat().st_size for f in block_files if f.is_file()
                )

            # Count inodes from metadata (tree structure)
            try:
                with open(meta) as f:
                    meta_data = json.load(f)
                # NyFS stores a tree with nested children
                tree = meta_data.get("tree", {})
                def _count_nodes(node: dict) -> int:
                    count = 1
                    for child in node.get("children", []):
                        count += _count_nodes(child)
                    return count
                info["inode_count"] = _count_nodes(tree) if tree else 0
            except (json.JSONDecodeError, OSError):
                pass

            images.append(info)

        return images

    def remove_image(self, path: str) -> bool:
        """Remove a base image directory.

        Deletes the entire image directory at ``path``.  The image
        must not be in use by any running container (checked against
        ``container.config.rootfs``).

        Args:
            path: Absolute path to the image directory.

        Returns:
            True if removed successfully.

        Raises:
            ValueError: If the image is in use or doesn't exist.
        """
        target = Path(path).resolve()
        if not target.is_dir():
            raise ValueError(f"Image not found: {path}")

        # Check if any running container uses this image
        for c in self.containers.values():
            if (c.config.rootfs and
                    Path(c.config.rootfs).resolve() == target):
                raise ValueError(
                    f"Image in use by container {c.id}"
                )

        shutil.rmtree(str(target))
        logger.info("image removed: %s", path)
        return True

    def export_image(self, image_path: str,
                     tar_path: Optional[str] = None) -> str:
        """Export a base image as a tar archive.

        Creates a gzip-compressed tar archive of the image directory
        that can be transferred to another host and imported.

        Args:
            image_path: Path to the image directory to export.
            tar_path: Optional output path (auto-generated if omitted).

        Returns:
            Path to the created tar archive.

        Raises:
            ValueError: If the image directory doesn't exist.
        """
        import tarfile

        source = Path(image_path).resolve()
        if not source.is_dir():
            raise ValueError(f"Image not found: {image_path}")

        if tar_path is None:
            tar_path = str(
                Path(tempfile.gettempdir()) /
                f"nyrqis-image-{source.name}.tar.gz"
            )

        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(str(source), arcname=source.name)

        size = os.path.getsize(tar_path)
        self._record_event("image_exported", source.name,
                           f"path={tar_path}, size={size}")
        logger.info("image exported: %s → %s (%d bytes)",
                    image_path, tar_path, size)
        return tar_path

    def import_image(self, tar_path: str,
                     dest_dir: Optional[str] = None,
                     name: Optional[str] = None) -> str:
        """Import an image from a tar archive.

        Extracts a gzip-compressed tar archive (as created by
        ``export_image``) into the destination directory.

        Args:
            tar_path: Path to the tar archive.
            dest_dir: Directory to extract into (default: ./images).
            name: Optional override for the image directory name.

        Returns:
            Path to the imported image directory.

        Raises:
            ValueError: If the tar file doesn't exist or is invalid.
        """
        import tarfile

        if not os.path.isfile(tar_path):
            raise ValueError(f"Tar file not found: {tar_path}")

        if dest_dir is None:
            dest_dir = os.path.join(os.getcwd(), "images")
        os.makedirs(dest_dir, exist_ok=True)

        with tarfile.open(tar_path, "r:gz") as tar:
            # Get the top-level directory name from the archive
            members = tar.getmembers()
            if not members:
                raise ValueError("Tar archive is empty")
            top_name = members[0].name.split("/")[0]
            if name:
                # Rename the top-level directory
                for m in members:
                    if m.name == top_name:
                        m.name = name
                    elif m.name.startswith(top_name + "/"):
                        m.name = name + m.name[len(top_name):]
                top_name = name
            tar.extractall(dest_dir)

        imported_path = os.path.join(dest_dir, top_name)
        self._record_event("image_imported", top_name,
                           f"from={tar_path}, dest={imported_path}")
        logger.info("image imported: %s → %s", tar_path, imported_path)
        return imported_path

    def container_checkpoint(self, container: Container,
                             path: Optional[str] = None) -> Dict[str, Any]:
        """Checkpoint a container's filesystem state.

        Captures the overlay upper layer (if present) and the container
        configuration so the container can be restored later.  The
        checkpoint is serialized as JSON to ``path`` (or a temp file
        when omitted).

        The container does NOT need to be stopped — the overlay snapshot
        is taken under a lock — but a running container's filesystem
        may be inconsistent if the command is actively writing.

        Args:
            container: The container to checkpoint.
            path: Optional file path to write the checkpoint JSON.

        Returns:
            Dict with ``checkpoint_path``, ``container_id``,
            ``overlay_entries`` count, and ``config`` summary.
        """
        checkpoint: Dict[str, Any] = {
            "container_id": container.id,
            "config": {
                "hostname": container.config.hostname,
                "command": container.config.command,
                "limits": {
                    "memory_mb": container.config.limits.memory_mb,
                    "pid_limit": container.config.limits.pid_limit,
                },
                "seccomp": container.config.seccomp,
                "default_deny": container.config.default_deny,
                "network": container.config.network,
                "rootfs": container.config.rootfs,
                "capabilities": container.config.capabilities,
                "log_capture": container.config.log_capture,
            },
            "state": container.state.value,
            "pid": container.pid,
            "created_at": container.created_at,
            "started_at": container.started_at,
            "network_ip": container.network_ip,
        }

        # Capture overlay state if present
        if container.overlay is not None:
            try:
                overlay_snap = container.overlay.snapshot()
                checkpoint["overlay"] = overlay_snap
                checkpoint["overlay_entries"] = len(
                    overlay_snap.get("entries", {})
                )
            except Exception as e:
                logger.warning(
                    "checkpoint: overlay snapshot failed for %s: %s",
                    container.id, e,
                )
                checkpoint["overlay"] = None
                checkpoint["overlay_entries"] = 0
        else:
            checkpoint["overlay"] = None
            checkpoint["overlay_entries"] = 0

        # Write to file
        if path is None:
            fd, path = tempfile.mkstemp(
                suffix=".checkpoint.json",
                prefix=f"nyctr-{container.id}-",
            )
            os.close(fd)
        with open(path, "w") as f:
            json.dump(checkpoint, f, indent=2)

        checkpoint["checkpoint_path"] = path
        logger.info(
            "container_checkpoint: %s → %s (%d overlay entries)",
            container.id, path, checkpoint["overlay_entries"],
        )
        return checkpoint

    def container_restore(self, checkpoint: Dict[str, Any]) -> Container:
        """Restore a container from a checkpoint.

        Creates a new container from the checkpointed configuration and
        restores the overlay state.  The container is left in CREATED
        state — call ``spawn()`` to start it.

        Args:
            checkpoint: The checkpoint dict (as returned by
                ``container_checkpoint``).

        Returns:
            The new container in CREATED state.
        """
        cfg_data = checkpoint.get("config", {})
        limits_data = cfg_data.get("limits", {})
        config = ContainerConfig(
            name=f"{checkpoint.get('container_id', 'nyctr')}-restored",
            hostname=cfg_data.get("hostname", "nyrqis-container"),
            command=cfg_data.get("command", ["/bin/sh"]),
            limits=ResourceLimits(
                memory_mb=limits_data.get("memory_mb", 256),
                pid_limit=limits_data.get("pid_limit", 64),
            ),
            seccomp=cfg_data.get("seccomp", True),
            default_deny=cfg_data.get("default_deny", True),
            network=cfg_data.get("network", False),
            rootfs=cfg_data.get("rootfs"),
            capabilities=cfg_data.get("capabilities", []),
            log_capture=cfg_data.get("log_capture", False),
        )

        container = self.create(config)

        # Restore overlay state if captured
        overlay_data = checkpoint.get("overlay")
        if overlay_data is not None and container.config.rootfs is not None:
            try:
                from fuse.overlay import OverlayFilesystem
                from fuse.nyfs import NyFSFilesystem
                lower = NyFSFilesystem(container.config.rootfs)
                container.overlay = OverlayFilesystem(
                    lower, container_id=container.id,
                )
                # Restore the upper layer entries
                entries = overlay_data.get("entries", {})
                for path, entry_data in entries.items():
                    kind = entry_data.get("kind", "file")
                    deleted = entry_data.get("deleted", False)
                    mode = entry_data.get("mode", 0o644)
                    if kind == "dir":
                        container.overlay.mkdir(path, mode)
                        if deleted:
                            container.overlay._upper[path].deleted = True
                    elif kind == "file":
                        data = bytes.fromhex(
                            entry_data.get("data", "")
                        ) if entry_data.get("data") else b""
                        container.overlay.create_file(path, mode)
                        if data:
                            container.overlay.write(path, data)
                        if deleted:
                            container.overlay._upper[path].deleted = True
                logger.info(
                    "container_restore: %s overlay restored (%d entries)",
                    container.id, len(entries),
                )
            except Exception as e:
                logger.warning(
                    "container_restore: overlay restore failed for %s: %s",
                    container.id, e,
                )

        logger.info("container_restore: %s from checkpoint", container.id)
        return container

    @staticmethod
    def snapshot_diff(checkpoint_a: Dict[str, Any],
                      checkpoint_b: Dict[str, Any]) -> Dict[str, Any]:
        """Compare two checkpoints and report the differences.

        Compares the overlay entries (files added, removed, modified)
        and configuration changes between two checkpoint dicts.

        Args:
            checkpoint_a: The earlier checkpoint (baseline).
            checkpoint_b: The later checkpoint (comparison target).

        Returns:
            Dict with ``added``, ``removed``, ``modified`` file lists,
            ``config_changes`` dict, and ``summary`` string.
        """
        entries_a = (
            checkpoint_a.get("overlay") or {}
        ).get("entries", {})
        entries_b = (
            checkpoint_b.get("overlay") or {}
        ).get("entries", {})

        paths_a = set(entries_a.keys())
        paths_b = set(entries_b.keys())

        added = sorted(paths_b - paths_a)
        removed = sorted(paths_a - paths_b)

        modified: List[Dict[str, Any]] = []
        for path in sorted(paths_a & paths_b):
            ea = entries_a[path]
            eb = entries_b[path]
            # Compare meaningful fields
            changes: List[str] = []
            if ea.get("data") != eb.get("data"):
                changes.append("data")
            if ea.get("mode") != eb.get("mode"):
                changes.append("mode")
            if ea.get("deleted") != eb.get("deleted"):
                changes.append("deleted")
            if ea.get("size") != eb.get("size"):
                changes.append("size")
            if changes:
                modified.append({"path": path, "changes": changes})

        # Config changes
        cfg_a = checkpoint_a.get("config", {})
        cfg_b = checkpoint_b.get("config", {})
        config_changes: Dict[str, Any] = {}
        all_keys = set(cfg_a.keys()) | set(cfg_b.keys())
        for key in sorted(all_keys):
            if cfg_a.get(key) != cfg_b.get(key):
                config_changes[key] = {
                    "from": cfg_a.get(key),
                    "to": cfg_b.get(key),
                }

        # Summary
        parts = []
        if added:
            parts.append(f"{len(added)} added")
        if removed:
            parts.append(f"{len(removed)} removed")
        if modified:
            parts.append(f"{len(modified)} modified")
        if config_changes:
            parts.append(
                f"{len(config_changes)} config change(s)"
            )
        summary = ", ".join(parts) if parts else "no differences"

        return {
            "added": added,
            "removed": removed,
            "modified": modified,
            "config_changes": config_changes,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Snapshot export / import
    # ------------------------------------------------------------------

    def snapshot_export(
        self, container: Container, export_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Export a container checkpoint as a portable tar.gz archive.

        The archive contains ``checkpoint.json`` (the full checkpoint
        data) and optionally ``overlay/`` with raw file blobs for
        large files (files > 1 KiB) stored as separate entries for
        better compression.

        Args:
            container: The container to export.
            export_path: Optional output path; auto-generated if omitted.

        Returns:
            Dict with ``export_path``, ``archive_size``, ``container_id``.
        """
        import tarfile

        # First, create a checkpoint
        checkpoint = self.container_checkpoint(container)

        if export_path is None:
            export_path = f"nyctr-{container.id}-export.tar.gz"

        with tarfile.open(export_path, "w:gz") as tar:
            # Write the checkpoint JSON
            ckpt_json = json.dumps(checkpoint, indent=2).encode("utf-8")
            info = tarfile.TarInfo(name="checkpoint.json")
            info.size = len(ckpt_json)
            import io
            tar.addfile(info, io.BytesIO(ckpt_json))

            # Write overlay file blobs as separate entries if large
            overlay = checkpoint.get("overlay") or {}
            entries = overlay.get("entries", {})
            blob_count = 0
            for path, entry_data in entries.items():
                if entry_data.get("kind") != "file":
                    continue
                hex_data = entry_data.get("data", "")
                if len(hex_data) > 2048:  # > 1 KiB
                    raw = bytes.fromhex(hex_data)
                    blob_name = f"overlay{path}.blob"
                    blob_info = tarfile.TarInfo(name=blob_name)
                    blob_info.size = len(raw)
                    tar.addfile(blob_info, io.BytesIO(raw))
                    blob_count += 1

        archive_size = os.path.getsize(export_path)
        logger.info(
            "snapshot_export: %s → %s (%d bytes, %d blobs)",
            container.id, export_path, archive_size, blob_count,
        )
        return {
            "export_path": export_path,
            "archive_size": archive_size,
            "container_id": container.id,
            "overlay_entries": len(entries),
            "blob_count": blob_count,
        }

    def snapshot_import(
        self, archive_path: str,
    ) -> Dict[str, Any]:
        """Import a checkpoint from a portable tar.gz archive.

        Reads the archive, loads ``checkpoint.json``, and restores
        any blob data that was stored as separate entries.

        Args:
            archive_path: Path to the tar.gz archive.

        Returns:
            The loaded checkpoint dict (ready for ``container_restore``).

        Raises:
            FileNotFoundError: If archive does not exist.
            ValueError: If archive is missing checkpoint.json.
        """
        import tarfile

        if not os.path.isfile(archive_path):
            raise FileNotFoundError(f"archive not found: {archive_path}")

        checkpoint = None
        blobs: Dict[str, bytes] = {}

        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name == "checkpoint.json":
                    f = tar.extractfile(member)
                    if f is not None:
                        checkpoint = json.loads(f.read())
                elif member.name.startswith("overlay") and member.name.endswith(".blob"):
                    f = tar.extractfile(member)
                    if f is not None:
                        # /path/to/file.blob → /path/to/file
                        blob_path = member.name[7:-5]  # strip overlay prefix and .blob suffix
                        blobs[blob_path] = f.read()

        if checkpoint is None:
            raise ValueError(
                f"archive {archive_path!r} missing checkpoint.json"
            )

        # Re-link blobs to overlay entries
        overlay = checkpoint.get("overlay")
        if overlay and blobs:
            entries = overlay.get("entries", {})
            for path, raw in blobs.items():
                if path in entries:
                    entries[path]["data"] = raw.hex()

        logger.info(
            "snapshot_import: %s (%d blobs restored)",
            archive_path, len(blobs),
        )
        return checkpoint

    # ------------------------------------------------------------------
    # Dependency ordering
    # ------------------------------------------------------------------

    def _compute_start_order(
        self, container_ids: List[str]
    ) -> List[str]:
        """Topological sort of containers for ordered start.

        Containers listed in ``depends_on`` are started before the
        containers that reference them.  Raises ``ValueError`` on
        circular dependencies or missing container IDs.

        Args:
            container_ids: The IDs to start (order will be computed).

        Returns:
            Sorted list of container IDs in start order.

        Raises:
            ValueError: On circular dependency or missing ID.
        """
        # Build adjacency: id → set of IDs it depends on
        adj: Dict[str, set] = {}
        for cid in container_ids:
            c = self.containers.get(cid)
            if c is None:
                raise ValueError(f"container {cid!r} not found")
            deps = c.config.depends_on or []
            # Only include deps that are in the requested set
            adj[cid] = {d for d in deps if d in set(container_ids)}

        # Kahn's algorithm
        in_degree: Dict[str, int] = {cid: 0 for cid in container_ids}
        reverse: Dict[str, set] = {cid: set() for cid in container_ids}
        for cid, deps in adj.items():
            for dep in deps:
                reverse[dep].add(cid)
                in_degree[cid] += 1

        queue = [cid for cid, d in in_degree.items() if d == 0]
        order: List[str] = []
        while queue:
            queue.sort()  # deterministic
            node = queue.pop(0)
            order.append(node)
            for dependent in reverse[node]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(order) != len(container_ids):
            missing = set(container_ids) - set(order)
            raise ValueError(
                f"circular dependency detected among: {missing}"
            )
        return order

    def _compute_stop_order(
        self, container_ids: List[str]
    ) -> List[str]:
        """Reverse topological sort: dependents stop before deps."""
        return list(reversed(self._compute_start_order(container_ids)))

    def start_ordered(
        self, container_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Start containers in dependency order.

        Respects ``depends_on`` declarations so that prerequisites
        are started (and reach RUNNING) before dependents.

        Args:
            container_ids: IDs to start.

        Returns:
            List of ``{id, exit_code}`` dicts in start order.
        """
        order = self._compute_start_order(container_ids)
        results: List[Dict[str, Any]] = []
        for cid in order:
            c = self.containers.get(cid)
            if c is None:
                results.append({
                    "id": cid,
                    "exit_code": -1,
                    "error": "not found",
                })
                continue
            try:
                rc = self.start(c)
                results.append({"id": cid, "exit_code": rc})
            except Exception as e:
                results.append({
                    "id": cid,
                    "exit_code": -1,
                    "error": str(e),
                })
        return results

    def stop_ordered(
        self, container_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Stop containers in reverse dependency order.

        Dependents are stopped before their prerequisites.

        Args:
            container_ids: IDs to stop.

        Returns:
            List of ``{id, exit_code}`` dicts in stop order.
        """
        order = self._compute_stop_order(container_ids)
        results: List[Dict[str, Any]] = []
        for cid in order:
            c = self.containers.get(cid)
            if c is None:
                results.append({
                    "id": cid,
                    "exit_code": -1,
                    "error": "not found",
                })
                continue
            try:
                rc = self.stop(c)
                results.append({"id": cid, "exit_code": rc})
            except Exception as e:
                results.append({
                    "id": cid,
                    "exit_code": -1,
                    "error": str(e),
                })
        return results

    def get_dependency_graph(
        self, container_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Return the dependency graph for a set of containers.

        Args:
            container_ids: IDs to include (default: all containers).

        Returns:
            Dict mapping each container ID to its ``depends_on`` list,
            ``dependents`` (reverse edges), and ``state``.
        """
        if container_ids is None:
            container_ids = list(self.containers.keys())
        graph: Dict[str, Any] = {}
        for cid in container_ids:
            c = self.containers.get(cid)
            if c is None:
                continue
            deps = c.config.depends_on or []
            graph[cid] = {
                "depends_on": deps,
                "dependents": [],
                "state": c.state.value,
            }
        # Fill reverse edges
        for cid, info in graph.items():
            for dep in info["depends_on"]:
                if dep in graph:
                    graph[dep]["dependents"].append(cid)
        return graph

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
        where /sys/fs/cgroup is not writable. The plan is computed by the
        Rust launch-plan primitives (ADR-0020 priority #5); the pure-Python
        floor in container_codec is byte-identical.
        """
        limits = container.config.limits
        plan = container_codec.cgroup_plan(
            container.id,
            limits.memory_mb, limits.pid_limit,
            limits.cpu_quota_us, limits.cpu_period_us,
        )
        return [(Path(path), pairs) for path, pairs in plan["v1"]]
    
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
        """Set up cgroups v2 resource limits (unified hierarchy). The
        settings (memory.max / pids.max / cpu.max) come from the Rust
        launch-plan primitives (ADR-0020 priority #5); the pure-Python
        floor in container_codec is byte-identical."""
        limits = container.config.limits
        cgroup_path = self.cgroup_root / "nyrqis" / container.id
        
        try:
            cgroup_path.mkdir(parents=True, exist_ok=True)
            
            plan = container_codec.cgroup_plan(
                container.id,
                limits.memory_mb, limits.pid_limit,
                limits.cpu_quota_us, limits.cpu_period_us,
            )
            for filename, content in plan["v2"]:
                (cgroup_path / filename).write_text(content)
                logger.debug(f"Set {filename}: {content}")
            
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
        from the container command (argparse REMAINDER). The argv is
        built by the Rust launch-plan primitives (ADR-0020 priority
        #5); the pure-Python floor in container_codec is byte-identical.
        """
        policy_path = ""
        if container.config.seccomp:
            policy_path = str(self._write_policy_file(container))
        argv = container_codec.launcher_argv(
            sys.executable, str(launcher), container.config.hostname,
            policy_path, container.config.default_deny,
            list(container.config.command),
        )
        if container.config.seccomp and container.config.strict_seccomp:
            # Fail-closed: a launcher that cannot install the filter
            # must refuse to run the command (exit 4), not silently run
            # unfiltered. Inserted AFTER the codec built the argv, so
            # the codec's byte-identical contract is untouched.
            argv.insert(argv.index("--"), "--strict-seccomp")
        # If a Nyrqis application is specified, pass it to the launcher
        # so it can execute through the NyRuntime instead of the raw command.
        if container.config.app_path:
            argv.insert(argv.index("--"), "--nyrqis-app")
            argv.insert(argv.index("--"), container.config.app_path)
        # Pass environment variables to the launcher via a JSON file
        env_path = self._write_env_file(container)
        if env_path is not None:
            argv.insert(argv.index("--"), "--env-file")
            argv.insert(argv.index("--"), env_path)
        # If inherit_host_env is False, tell the launcher not to inherit
        if not container.config.inherit_host_env:
            argv.insert(argv.index("--"), "--no-inherit-env")
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
        ]
        if container.config.network:
            cmd.append("--net")  # own network namespace (loopback only)
        cmd.append("--")
        cmd += self._launcher_args(container, launcher)
        return cmd
    
    def _write_bpf_file(self, container: Container) -> str:
        """Serialize the container's compiled seccomp program to a file
        the Rust launcher-init installs via ``prctl`` (ADR-0020). The
        policy COMPILATION stays here — above the platform boundary;
        the install is the compiled binary's job. Byte layout: classic-
        BPF ``sock_filter`` records, little-endian ``<HBBI`` — the
        format ``rust/launcher``'s ``parse_bpf`` reads.
        """
        from backend.seccomp import (  # lazy: no import cycle
            SyscallArch, build_allowlist_policy, build_policy,
            build_program,
        )
        caps = container.config.capabilities
        if not caps:
            from backend.capability import CapabilityManager
            caps = [
                c.value
                for c in CapabilityManager().get_default_capabilities()
            ]
        caps = sorted(set(caps))
        arch = SyscallArch.from_machine()
        if container.config.default_deny:
            policy = build_allowlist_policy(caps, arch=arch)
        else:
            policy = build_policy(caps, arch=arch)
        program = build_program(policy)
        fd, path = tempfile.mkstemp(prefix="nyrqis-bpf-", suffix=".bpf")
        try:
            with os.fdopen(fd, "wb") as fh:
                for code, jt, jf, k in program:
                    fh.write(struct.pack("<HBBI", code, jt, jf, k))
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        os.chmod(path, 0o600)
        self._bpf_files.append(path)
        return path

    def _launcher_exec(self, container: Container) -> List[str]:
        """The container's full launcher argv — ``argv[0]`` IS the
        executable (handed to ``os.execv`` as-is). The compiled
        launcher-init (``rust/launcher``, ADR-0020 — zero Python
        between clone and exec: the clone child execs the binary
        directly) when available; the Python launcher otherwise. The
        hostname and command are separate argv entries — never
        interpolated into a shell string (FIND-BACKEND-004). The Rust
        path carries the PRE-BUILT seccomp program (``--bpf-file``)
        instead of the policy JSON: policy compilation stays in the
        backend, the install is the binary's.
        """
        launcher = Path(__file__).resolve().parent / "launcher.py"
        if rust_launcher.available():
            argv = [
                rust_launcher.launcher_path(),
                "--hostname", container.config.hostname,
            ]
            if container.config.seccomp:
                argv += ["--bpf-file", self._write_bpf_file(container)]
                if container.config.strict_seccomp:
                    argv.append("--strict-seccomp")
            argv += ["--"] + list(container.config.command)
            return argv
        return self._launcher_args(container, launcher)

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
        """Remove seccomp policy + BPF + LSM temp files."""
        for path in self._policy_files + self._bpf_files + self._lsm_files:
            try:
                os.unlink(path)
            except OSError as e:
                logger.warning(f"Failed to remove policy file {path}: {e}")
        self._policy_files.clear()
        self._bpf_files.clear()
        self._lsm_files.clear()
    
    def _setup_overlay(self, container: Container) -> None:
        """Create an overlay filesystem for the container if rootfs is set.

        When ``container.config.rootfs`` points to a valid NyFS base
        directory, an ``OverlayFilesystem`` is attached to
        ``container.overlay`` so the container's storage operations
        can use a per-container writable view.
        """
        rootfs = container.config.rootfs
        if not rootfs:
            return
        try:
            from fuse.nyfs import NyFSFilesystem
            from fuse.overlay import OverlayFilesystem
            # Try to load an existing saved filesystem; fall back to
            # creating a new one if no saved state exists.
            meta = os.path.join(rootfs, "state", "metadata.json")
            if os.path.exists(meta):
                lower = NyFSFilesystem.load(rootfs)
            else:
                lower = NyFSFilesystem(rootfs)
            overlay = OverlayFilesystem(lower, container_id=container.id)
            container.overlay = overlay
            logger.info(f"Container {container.id} overlay attached "
                        f"(lower={rootfs})")
        except Exception as e:
            logger.warning(f"Container {container.id} overlay setup failed: "
                           f"{e} — running without overlay")

    def _setup_lsm(self, container: Container) -> None:
        """Generate and stage an LSM policy for the container.

        Produces an AppArmor profile (and optionally an SELinux module)
        from the container's capability set and writes them to the
        container's policy directory.  The launcher picks them up via
        ``--aa-profile`` / ``--se-module`` flags.

        A failure here is logged and swallowed — the container still
        runs without the LSM layer (seccomp + control plane still apply).
        """
        try:
            from backend.lsm import (
                build_lsm_policy,
                AppArmorProfile,
                SEPolicy,
                lsm_audit,
            )
            from backend.capability import Capability
            # Derive capabilities from the container config
            caps = set()
            for cap_name in (container.config.capabilities or []):
                try:
                    caps.add(Capability(cap_name))
                except ValueError:
                    pass
            policy = build_lsm_policy(container.id, caps)
            # Audit the policy
            warnings = lsm_audit(policy)
            for w in warnings:
                logger.warning(f"LSM audit ({container.id}): {w}")
            # Write AppArmor profile
            aa_dir = Path(tempfile.mkdtemp(prefix=f"nyrqis-aa-{container.id}-"))
            aa_profile = AppArmorProfile(policy)
            aa_path = str(aa_dir / f"nyrqis.{container.id}")
            aa_profile.write(aa_path)
            self._lsm_files.append(aa_path)
            container.config.aa_profile = aa_path
            logger.info(f"AppArmor profile for {container.id}: {aa_path}")
            # Write SELinux module
            se_dir = Path(tempfile.mkdtemp(prefix=f"nyrqis-se-{container.id}-"))
            se_policy = SEPolicy(policy)
            se_paths = se_policy.write(str(se_dir))
            for p in se_paths.values():
                self._lsm_files.append(p)
            container.config.se_module_dir = str(se_dir)
            logger.info(f"SELinux module for {container.id}: {se_dir}")
            # Persist the LSM policy as JSON for audit
            policy_json = os.path.join(str(se_dir), "lsm_policy.json")
            with open(policy_json, "w", encoding="utf-8") as f:
                json.dump(policy.to_dict(), f, indent=2)
            self._lsm_files.append(policy_json)
        except ImportError:
            logger.debug(f"LSM module not available for {container.id}")
        except Exception as e:
            logger.warning(f"LSM setup failed for {container.id}: {e}")

    def _setup_network(self, container: Container) -> None:
        """Set up veth/bridge outbound connectivity for network=True containers.

        Creates a veth pair, attaches one end to the bridge, and moves
        the other end into the container's network namespace.  Configures
        an IP and default route so the container can reach the internet.

        Best-effort: a failure means the container runs with loopback only
        (the pre-existing behavior).
        """
        if not container.config.network or container.pid is None:
            return
        try:
            from backend.network import setup_container_network
            ip = setup_container_network(container.id, container.pid)
            if ip is not None:
                container.network_ip = ip
                logger.info(
                    f"Container {container.id} network: {ip}"
                )
            else:
                logger.warning(
                    f"Container {container.id} network setup failed "
                    "— running with loopback only"
                )
        except ImportError:
            logger.debug(f"network module not available for {container.id}")
        except Exception as e:
            logger.warning(f"Network setup failed for {container.id}: {e}")

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
            f"default_deny={container.config.default_deny}, "
            f"network={container.config.network}, direct_syscalls=False)"
        )
        
        # Set up log capture if configured
        stdout_target = subprocess.PIPE if container.config.log_capture else None
        stderr_target = subprocess.PIPE if container.config.log_capture else None

        proc = subprocess.Popen(
            cmd, env=os.environ.copy(),
            stdout=stdout_target, stderr=stderr_target,
        )
        container.pid = proc.pid
        container._proc = proc

        if container.config.log_capture:
            self._start_log_capture(container, proc)

        return proc

    def _spawn_direct(self, container: Container):
        """Direct-syscall launch (ADR-0020 priority #2, plan §4.1).

        ``unshare(2)`` moves the *calling* process into the new
        namespaces, so the manager must never call it itself. Two child
        entry points create the container's PID-1 (the launcher-init):

        - **Rust-native** (syscalls ABI 1.2.0, crate present): ONE
          ``clone(2)`` FFI call creates the PID-1 directly in ALL its
          namespaces (user/mount/UTS/IPC/pid + net); the Rust entry
          writes the root maps, sets PDEATHSIG, mounts a hardened
          procfs, and execs the launcher. No Python runs between fork
          and exec on this path.
        - **Python fork child** (crate-less fallback): the manager
          forks a namespace-setup child which performs the same
          sequence ``unshare(1)`` used to (unshare NEWUSER + maps →
          NS|UTS|IPC[|NET] → NEWPID → fork PID-1), relays the PID-1
          through a pipe, waits for it, and exits with its status.

        Both entry points produce the same observable outcome: the
        launcher-init is the namespace's PID 1 (Linux discards signals
        sent to a namespace PID 1 that has no handler — see
        ``launcher.py``), it runs the container command as its child,
        and the manager resolves the command's HOST pid itself through
        the init's /proc children file (see ``_resolve_command_pid`` —
        the manager's /proc is host-scoped). ``container.pid`` is the
        REAL command (what suspend/resume/terminate/cgroup-attach and
        the IPC registry must address), ``container._init_pid`` is the
        PID-1 launcher-init, and ``container._direct_launcher_pid`` is
        what ``wait()`` reaps (the setup child on the fork path — which
        exits with the container's status — or the init itself on the
        clone path).

        Fork-safety note: between ``fork`` and ``exec`` the child runs
        only the syscall wrappers and plain file writes — no logging,
        no Python allocation beyond the FFI call itself. The Rust
        backend is pre-loaded here so the child never dlopens. Spawn
        from a quiescent manager (no other threads holding locks),
        matching the fork rule Python's own ``subprocess`` documents.
        """
        launcher_argv = self._launcher_exec(container)

        # Pre-load so the forked child never calls dlopen between fork
        # and exec (the loader cache is inherited by the child). The
        # container codec too: the child's _write_root_maps routes the
        # uid/gid map contents through it.
        rust_syscalls._load_rust_backend()
        container_codec._load_rust_backend()

        logger.info(
            f"Launching container {container.id} (hostname={container.config.hostname}, "
            f"memory={container.config.limits.memory_mb}MiB, "
            f"pids={container.config.limits.pid_limit}, "
            f"seccomp={container.config.seccomp}, "
            f"default_deny={container.config.default_deny}, "
            f"network={container.config.network}, direct_syscalls=True, "
            f"child={'rust' if rust_syscalls.available() else 'python'}, "
            f"launcher={'rust' if rust_launcher.available() else 'python'})"
        )

        if rust_syscalls.available():
            init_pid, reap_pid = self._spawn_direct_clone(
                launcher_argv, container.config.network)
        else:
            init_pid, reap_pid = self._spawn_direct_fork(
                launcher_argv, container.config.network)

        # Resolve the container command's HOST pid. The launcher-init
        # does not exec the command (it stays the namespace's PID 1 so
        # kernel signal semantics apply to the command), and a pid
        # reported from inside the namespace would be the ns-local
        # value — so THIS process (whose /proc is host-scoped; the
        # container's procfs lives in its own mount namespace) polls the
        # init's /proc children file: the command is the init's only
        # direct child, and its host pid appears there.
        cmd_pid = _resolve_command_pid(init_pid, _DIRECT_LAUNCH_TIMEOUT_S)
        if cmd_pid is None:
            # None means the command never materialized — either the
            # init is gone/a zombie (the command exited within ~1ms of
            # forking; pid=None is correct and wait() reports the
            # status) OR the deadline elapsed with the init still
            # alive (a live container the manager has no handle on).
            # Distinguish the two: a live init must be torn down and
            # the spawn failed, not silently orphaned.
            alive = False
            try:
                with open(f"/proc/{init_pid}/stat") as fh:
                    fields = fh.read().split()
                alive = len(fields) >= 3 and fields[2] != "Z"
            except OSError:
                alive = False
            if alive:
                try:
                    os.kill(init_pid, 9)  # killing PID 1 tears down the ns
                except OSError:
                    pass
                raise RuntimeError(
                    "direct-syscall launcher never reported a command "
                    f"pid within {_DIRECT_LAUNCH_TIMEOUT_S:.0f}s"
                )

        container.pid = cmd_pid  # None → the command never materialized
        container._init_pid = init_pid
        container._direct_launcher_pid = reap_pid
        return None

    @staticmethod
    def _kill_launcher(pid: int) -> None:
        """SIGKILL a setup child / init and reap it (best effort — a
        zombie's kill is a no-op; waitpid reaps it)."""
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass

    def _spawn_direct_clone(
        self, launcher_argv: List[str], network: bool
    ) -> Tuple[int, int]:
        """The Rust-native child entry (syscalls ABI 1.2.0): one
        ``clone(2)`` FFI call creates the container's PID-1 directly in
        ALL its namespaces; the Rust entry (``launch_child``) writes the
        root maps (uid/gid captured HERE — inside the new user
        namespace getuid() reports 65534), sets PDEATHSIG, mounts a
        hardened procfs, and execs the launcher. The pipe is an
        error-reporting channel only (the init pid comes from clone's
        return): EOF on the read means the entry closed its copy and
        exec'd; ERR: means it failed.

        Returns ``(init_pid, reap_pid)`` — the clone child IS the init,
        so ``wait()`` reaps it directly (it exits with the container's
        status via the launcher-init). Raises on failure (the child is
        SIGKILLed and reaped first)."""
        uid = os.getuid()
        gid = os.getgid()
        flags = (
            rust_syscalls.CLONE_NEWUSER | rust_syscalls.CLONE_NEWNS
            | rust_syscalls.CLONE_NEWUTS | rust_syscalls.CLONE_NEWIPC
            | rust_syscalls.CLONE_NEWPID | rust_syscalls.CLONE_SIGCHLD
        )
        if network:
            flags |= rust_syscalls.CLONE_NEWNET
        read_fd, write_fd = os.pipe()
        try:
            try:
                args = rust_syscalls.LaunchArgs.build(
                    write_fd, uid, gid, launcher_argv)
                init_pid = rust_syscalls.clone(flags, args)
            except OSError as e:
                raise RuntimeError(
                    f"direct-syscall launcher failed: clone: {e}"
                ) from e
        finally:
            os.close(write_fd)

        # Bounded read for the ERR: channel (the entry closes its copy
        # of the pipe before exec; EOF = success).
        data = b""
        try:
            ready, _, _ = select.select(
                [read_fd], [], [], _DIRECT_LAUNCH_TIMEOUT_S)
            if ready:
                data = os.read(read_fd, 4096)
        finally:
            os.close(read_fd)

        if data.startswith(b"ERR:") or data:
            self._kill_launcher(init_pid)
            if data.startswith(b"ERR:"):
                raise RuntimeError(
                    "direct-syscall launcher failed: "
                    f"{data[4:].decode('utf-8', 'replace')}"
                )
            raise RuntimeError(
                "direct-syscall launcher failed: unexpected setup-child "
                "output"
            )
        return init_pid, init_pid

    def _spawn_direct_fork(
        self, launcher_argv: List[str], network: bool
    ) -> Tuple[int, int]:
        """The crate-less fallback child entry: the manager forks a
        namespace-setup child (``_direct_launch_child``) which performs
        the ``unshare(2)`` dance ``unshare(1)`` used to and forks the
        container's PID-1, relaying its pid through a pipe.

        Returns ``(init_pid, reap_pid)`` — the init pid (the relayed
        PID-1) and the setup child (what ``wait()`` reaps; it exits
        with the container's status). Raises on failure."""
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
                _direct_launch_child(
                    write_fd, launcher_argv, network
                )
            except BaseException:
                os._exit(125)
        os.close(write_fd)

        # Bounded read: the setup child writes the container's PID-1
        # (or an ERR: marker) and closes the pipe before waiting for it.
        data = b""
        try:
            ready, _, _ = select.select(
                [read_fd], [], [], _DIRECT_LAUNCH_TIMEOUT_S)
            if ready:
                data = os.read(read_fd, 4096)
        finally:
            os.close(read_fd)

        if data.startswith(b"ERR:") or not data:
            # The setup child already exited (it reports failures before
            # dying); kill is a no-op on a zombie and waitpid reaps it.
            self._kill_launcher(launcher_pid)
            if data.startswith(b"ERR:"):
                raise RuntimeError(
                    "direct-syscall launcher failed: "
                    f"{data[4:].decode('utf-8', 'replace')}"
                )
            raise RuntimeError(
                "direct-syscall launcher died during namespace setup "
                "(no PID reported)"
            )
        init_pid = int(data.decode())
        return init_pid, launcher_pid
    
    def _attach_to_cgroups(self, container: Container) -> None:
        """Move the container's processes into its cgroups.
        
        Without this the resource limits created in ``_setup_cgroups`` are
        never actually applied. Per NPS-010 §7, limits must be enforced,
        not merely configured. Both the command and (direct path) the
        PID-1 launcher-init are attached: limits apply to the whole
        container, and an un-attached init's memory would escape the
        container's accounting.
        """
        if container.pid is None:
            # The command exited during launch (never became an
            # observable process); there is nothing to attach and no
            # limit to enforce on a dead container.
            logger.debug(f"Container {container.id} exited during launch; "
                         "skipping cgroup attach")
            return
        pids = [str(container.pid)]
        if container._init_pid is not None:
            pids.append(str(container._init_pid))
        
        for cgroup_path_str in container.cgroup_paths:
            cgroup_path = Path(cgroup_path_str)
            if self.use_cgroups_v2:
                member_file = cgroup_path / "cgroup.procs"
            else:
                member_file = cgroup_path / "tasks"
            for pid in pids:
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
    syscalls) — safe between fork and exec. The map contents come from
    the Rust launch-plan primitives (ADR-0020 priority #5), pre-loaded
    by the manager before forking (never dlopen'd in the child); the
    pure-Python floor in container_codec is byte-identical.

    ``uid``/``gid`` MUST be captured BEFORE ``unshare(CLONE_NEWUSER)``:
    inside the new (still unmapped) namespace, ``getuid()`` reports the
    overflow uid 65534 (nobody), so reading them here would map the
    wrong id and the kernel would refuse it with EPERM.
    """
    if uid is None or gid is None:
        uid = os.getuid()
        gid = os.getgid()
    setgroups, uid_map, gid_map = container_codec.root_maps(uid, gid)
    for path, content in (
        ("/proc/self/setgroups", setgroups),
        ("/proc/self/uid_map", uid_map),
        ("/proc/self/gid_map", gid_map),
    ):
        try:
            fd = os.open(path, os.O_WRONLY)
        except OSError:
            continue  # e.g. the setgroups knob is absent on older kernels
        try:
            os.write(fd, content)
        finally:
            os.close(fd)


def _resolve_command_pid(init_pid: int, timeout_s: float) -> Optional[int]:
    """Resolve the container command's HOST pid from the host side.

    The launcher-init does not exec the command (it stays the
    namespace's PID 1 so kernel signal semantics apply to the command),
    and a pid reported from inside the namespace would be the ns-local
    value. This function runs in the manager, whose /proc is
    host-scoped (the container's procfs lives in the container's own
    mount namespace): the command is the init's only direct child, so
    its host pid appears in the init's /proc children file. Polls until
    it appears, the init is gone or a zombie (its namespace died with
    it — a zombie's children file is empty and kill(pid, 0) still
    reports it alive), or ``timeout_s`` elapses. Returns None when the
    command never materialized as an observable process (e.g. it
    exited within ~1ms of forking — the container's lifetime already
    ended, and ``wait()`` reports the exit status).
    """
    children_path = f"/proc/{init_pid}/task/{init_pid}/children"
    stat_path = f"/proc/{init_pid}/stat"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with open(children_path, "r") as fh:
                children = fh.read().split()
        except OSError:
            return None  # the init is gone
        if children:
            return int(children[0])
        # Stop when the init is gone OR a zombie (dead but not yet
        # reaped — its namespace died with it, so no child can appear).
        try:
            with open(stat_path, "r") as fh:
                fields = fh.read().split()
            if len(fields) < 3 or fields[2] == "Z":
                return None
        except OSError:
            return None
        time.sleep(0.001)
    return None


def _direct_launch_child(
    write_fd: int, launcher_argv: List[str], network: bool = False
) -> None:
    """The manager's forked namespace-setup child (direct-syscall path).

    Performs the ``unshare(2)`` dance ``unshare(1)`` used to do, forks
    the container's PID-1 (the launcher-init), relays its PID to the
    manager through ``write_fd``, then waits for it and exits with its
    status (or dies by its signal). Never returns on success. All
    failure exits use ``os._exit``: between fork and exec the child
    must not run Python's cleanup machinery, and any error is reported
    to the manager as an ``ERR:`` pipe message rather than an exception
    (the manager reaps the child and raises).

    ``launcher_argv`` is the argv handed to ``os.execv`` (Python
    interpreter + launcher.py + container command) — built by the
    manager before forking so the child does no allocation here. The
    manager resolves the container command's HOST pid itself (see
    ``_resolve_command_pid``). ``network`` adds ``CLONE_NEWNET`` to the
    mount/UTS/IPC unshare, so the container gets its own network
    namespace (loopback only).
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
    #    user namespace), plus the network namespace when the container
    #    opted in. Creating the netns here (inside the new user
    #    namespace) needs no extra privileges; the container then sees
    #    only loopback — outbound connectivity is deliberately not
    #    wired yet (veth/bridge is future work).
    ns_flags = (
        rust_syscalls.CLONE_NEWNS
        | rust_syscalls.CLONE_NEWUTS
        | rust_syscalls.CLONE_NEWIPC
    )
    if network:
        ns_flags |= rust_syscalls.CLONE_NEWNET
    try:
        rust_syscalls.unshare(ns_flags)
    except OSError as e:
        _fail(f"unshare(NS|UTS|IPC{'|NET' if network else ''}): {e}")

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
