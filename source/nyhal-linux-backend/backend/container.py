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
    # SLA (service level agreements)
    sla_uptime_target: float = 99.9  # uptime percentage target
    sla_max_restart_count: int = 3  # max restarts before SLA breach
    sla_alert_on_breach: bool = True  # fire alert on SLA breach

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
        # SLA tracking
        self._sla_started_at: Optional[float] = None
        self._sla_downtime_s: float = 0.0
        self._sla_violations: List[Dict[str, Any]] = []

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
        self._tenant_configs: Dict[str, Dict[str, Any]] = {}
        # Container lock files (prevent concurrent access)
        self._lock_dir = Path(tempfile.gettempdir()) / "nyrqis-locks"
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        self._lock_fds: Dict[str, int] = {}  # container_id → fd
        # Webhooks (HTTP callbacks for events)
        self._webhooks: Dict[str, Dict[str, Any]] = {}  # webhook_id → config
        self._webhook_id_counter = 0
        # Billing (cost tracking)
        self._billing_rates: Dict[str, float] = {
            "memory_mb_per_hour": 0.01,  # $0.01 per GB-hour
            "cpu_per_hour": 0.05,  # $0.05 per vCPU-hour
            "pid_per_hour": 0.001,  # $0.001 per PID-hour
            "storage_mb_per_hour": 0.002,  # $0.002 per GB-hour
        }
        self._billing_records: Dict[str, List[Dict[str, Any]]] = {}  # container_id → records
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

    def correlate_events(
        self,
        time_window_s: float = 60.0,
        kinds: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Correlate events across containers within a time window.

        Finds clusters of events from different containers that
        occurred within ``time_window_s`` of each other, which may
        indicate cascading failures or coordinated changes.

        Args:
            time_window_s: Max seconds between first and last event
                in a cluster.
            kinds: Filter to these event kinds before correlating.

        Returns:
            Dict with ``clusters`` (list of correlated event groups)
            and ``total_events``.
        """
        all_events = self.container_events()

        # Filter by kinds if specified
        if kinds:
            all_events = [
                e for e in all_events if e["kind"] in kinds
            ]

        if not all_events:
            return {
                "clusters": [],
                "total_events": 0,
            }

        # Sort by time
        all_events.sort(key=lambda e: e.get("time", 0))

        # Simple sliding-window clustering: group events that are
        # within time_window_s of the cluster's first event and
        # involve at least 2 different containers.
        clusters: List[Dict[str, Any]] = []
        current_cluster: List[Dict[str, Any]] = []
        cluster_start = 0.0

        for ev in all_events:
            t = ev.get("time", 0)
            if not current_cluster:
                current_cluster.append(ev)
                cluster_start = t
                continue

            if t - cluster_start <= time_window_s:
                current_cluster.append(ev)
            else:
                # Finalize current cluster
                container_ids = set(
                    e["container_id"] for e in current_cluster)
                if len(container_ids) >= 2:
                    clusters.append({
                        "start_time": current_cluster[0]["time"],
                        "end_time": current_cluster[-1]["time"],
                        "container_ids": list(container_ids),
                        "event_count": len(current_cluster),
                        "kinds": list(set(
                            e["kind"] for e in current_cluster)),
                        "events": current_cluster,
                    })
                current_cluster = [ev]
                cluster_start = t

        # Don't forget the last cluster
        if current_cluster:
            container_ids = set(
                e["container_id"] for e in current_cluster)
            if len(container_ids) >= 2:
                clusters.append({
                    "start_time": current_cluster[0]["time"],
                    "end_time": current_cluster[-1]["time"],
                    "container_ids": list(container_ids),
                    "event_count": len(current_cluster),
                    "kinds": list(set(
                        e["kind"] for e in current_cluster)),
                    "events": current_cluster,
                })

        return {
            "clusters": clusters,
            "total_events": len(all_events),
        }

    def get_event_timeline(
        self,
        container_ids: Optional[List[str]] = None,
        time_window_s: float = 300.0,
    ) -> Dict[str, Any]:
        """Get a merged timeline of events across containers.

        Returns events from multiple containers sorted by time,
        useful for debugging cross-container interactions.

        Args:
            container_ids: IDs to include (default: all).
            time_window_s: Only include events from the last N seconds.

        Returns:
            Dict with ``events`` (sorted list) and ``summary``.
        """
        import time as _time
        cutoff = _time.time() - time_window_s

        all_events = self.container_events()
        if container_ids:
            id_set = set(container_ids)
            all_events = [
                e for e in all_events
                if e["container_id"] in id_set
            ]

        # Filter by time window
        all_events = [
            e for e in all_events if e.get("time", 0) >= cutoff
        ]

        # Sort by time
        all_events.sort(key=lambda e: e.get("time", 0))

        # Summary
        by_kind: Dict[str, int] = {}
        by_container: Dict[str, int] = {}
        for e in all_events:
            k = e["kind"]
            by_kind[k] = by_kind.get(k, 0) + 1
            cid = e["container_id"]
            by_container[cid] = by_container.get(cid, 0) + 1

        return {
            "events": all_events,
            "summary": {
                "total": len(all_events),
                "by_kind": by_kind,
                "by_container": by_container,
            },
        }

    # ------------------------------------------------------------------
    # Audit trail (immutable resource usage log)
    # ------------------------------------------------------------------

    def record_audit_entry(
        self, container: Container,
        action: str,
        actor: str = "system",
        resource: Optional[str] = None,
        old_value: Any = None,
        new_value: Any = None,
        detail: str = "",
    ) -> Dict[str, Any]:
        """Record an immutable audit entry for a resource change.

        Audit entries are append-only and cannot be modified or
        deleted (the log is write-once).  Each entry captures
        who did what, what changed, and when.

        Args:
            container: Target container.
            action: The action performed (e.g., "limit_change",
                "capability_grant", "capability_revoke").
            actor: Who performed the action (e.g., "operator",
                "auto-scaler", "health-check").
            resource: Resource type (e.g., "memory", "cpu").
            old_value: Previous value.
            new_value: New value.
            detail: Additional detail.

        Returns:
            The audit entry dict.
        """
        if not hasattr(container, "_audit_log"):
            container._audit_log = []

        entry: Dict[str, Any] = {
            "timestamp": time.time(),
            "container_id": container.id,
            "action": action,
            "actor": actor,
            "resource": resource,
            "old_value": old_value,
            "new_value": new_value,
            "detail": detail,
        }
        container._audit_log.append(entry)

        # Also record in the global event log
        self._record_event(
            "audit", container.id,
            f"{action} by {actor}: {resource} {old_value} -> {new_value}")

        return entry

    def get_audit_log(
        self, container: Container,
        tail: Optional[int] = None,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        resource: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get the immutable audit log for a container.

        Args:
            container: Target container.
            tail: If set, return only the last N entries.
            action: Filter by action type.
            actor: Filter by actor.
            resource: Filter by resource type.

        Returns:
            List of audit entry dicts (newest first).
        """
        log = getattr(container, "_audit_log", [])
        if action:
            log = [e for e in log if e["action"] == action]
        if actor:
            log = [e for e in log if e["actor"] == actor]
        if resource:
            log = [e for e in log if e["resource"] == resource]
        # Return newest first
        log = list(reversed(log))
        if tail is not None:
            log = log[:tail]
        return log

    def get_audit_summary(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Get a summary of audit activity for a container.

        Returns:
            Dict with ``total_entries``, ``by_action``, ``by_actor``,
            ``by_resource``, and ``recent`` (last 10).
        """
        log = getattr(container, "_audit_log", [])
        if not log:
            return {
                "container_id": container.id,
                "total_entries": 0,
                "by_action": {},
                "by_actor": {},
                "by_resource": {},
                "recent": [],
            }

        by_action: Dict[str, int] = {}
        by_actor: Dict[str, int] = {}
        by_resource: Dict[str, int] = {}

        for e in log:
            a = e.get("action", "unknown")
            by_action[a] = by_action.get(a, 0) + 1
            actor = e.get("actor", "unknown")
            by_actor[actor] = by_actor.get(actor, 0) + 1
            res = e.get("resource")
            if res:
                by_resource[res] = by_resource.get(res, 0) + 1

        return {
            "container_id": container.id,
            "total_entries": len(log),
            "by_action": by_action,
            "by_actor": by_actor,
            "by_resource": by_resource,
            "recent": list(reversed(log[-10:])),
        }

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

    # ------------------------------------------------------------------
    # Event log export / import (disaster recovery)
    # ------------------------------------------------------------------

    def export_event_log(
        self,
        container: Optional[Container] = None,
        include_audit: bool = True,
        include_oom: bool = True,
        include_sla: bool = True,
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Export the event log as a JSON-serializable dict.

        Captures the complete event history for a container (or all
        containers) for disaster recovery or archival. The export
        includes lifecycle events, audit entries, OOM events, and
        SLA violations.

        Args:
            container: Export events for a specific container.
                If None, export all containers.
            include_audit: Include audit log entries.
            include_oom: Include OOM event entries.
            include_sla: Include SLA violation entries.
            since: Only include events after this timestamp.
            until: Only include events before this timestamp.

        Returns:
            Dict with ``export_time``, ``containers``, ``total_events``,
            and ``metadata``.
        """
        export_time = time.time()
        containers_data: List[Dict[str, Any]] = []
        total_events = 0

        targets = ([container] if container
                   else list(self.containers.values()))

        for c in targets:
            cdata: Dict[str, Any] = {
                'container_id': c.id,
                'name': c.config.name,
                'state': c.state.value,
            }

            # Lifecycle events
            raw_events = self.container_events(container_id=c.id)
            events = raw_events
            if since is not None:
                events = [e for e in events
                          if e.get('time', 0) >= since]
            if until is not None:
                events = [e for e in events
                          if e.get('time', 0) <= until]
            cdata['lifecycle_events'] = events
            total_events += len(events)

            # Audit log
            if include_audit:
                audit_log = self.get_audit_log(c)
                if since is not None:
                    audit_log = [e for e in audit_log
                                 if e.get('timestamp', 0) >= since]
                if until is not None:
                    audit_log = [e for e in audit_log
                                 if e.get('timestamp', 0) <= until]
                cdata['audit_log'] = audit_log
                total_events += len(audit_log)
            else:
                cdata['audit_log'] = []

            # OOM events
            if include_oom:
                oom_events = list(getattr(c, '_oom_events', []))
                if since is not None:
                    oom_events = [e for e in oom_events
                                  if e.get('timestamp', 0) >= since]
                if until is not None:
                    oom_events = [e for e in oom_events
                                  if e.get('timestamp', 0) <= until]
                cdata['oom_events'] = oom_events
                total_events += len(oom_events)
            else:
                cdata['oom_events'] = []

            # SLA violations
            if include_sla:
                sla = getattr(c, '_sla', {})
                violations = sla.get('violations', [])
                if since is not None:
                    violations = [v for v in violations
                                 if v.get('timestamp', 0) >= since]
                if until is not None:
                    violations = [v for v in violations
                                 if v.get('timestamp', 0) <= until]
                cdata['sla_violations'] = violations
                total_events += len(violations)
            else:
                cdata['sla_violations'] = []

            # Remediation history
            rem = getattr(c, '_remediation', {})
            rem_history = rem.get('history', [])
            if since is not None:
                rem_history = [e for e in rem_history
                              if e.get('timestamp', 0) >= since]
            if until is not None:
                rem_history = [e for e in rem_history
                              if e.get('timestamp', 0) <= until]
            cdata['remediation_history'] = rem_history
            total_events += len(rem_history)

            containers_data.append(cdata)

        self._record_event(
            'event_log_export', 'system',
            f"exported {total_events} events from "
            f"{len(containers_data)} containers")

        return {
            'export_time': export_time,
            'containers': containers_data,
            'total_events': total_events,
            'metadata': {
                'include_audit': include_audit,
                'include_oom': include_oom,
                'include_sla': include_sla,
                'since': since,
                'until': until,
            },
        }

    def import_event_log(
        self,
        data: Dict[str, Any],
        container_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Import event log data into containers.

        Restores audit entries, OOM events, and SLA violations from
        a previously exported event log. Lifecycle events are
        informational only (not re-applied).

        Args:
            data: The exported event log dict.
            container_id: If provided, only import events for this
                container ID (match by ID from the export).

        Returns:
            Dict with ``imported_containers``, ``total_imported``,
            and ``errors``.
        """
        imported_containers = 0
        total_imported = 0
        errors: List[str] = []

        for cdata in data.get('containers', []):
            cid = cdata.get('container_id')
            if container_id and cid != container_id:
                continue

            c = self.containers.get(cid)
            if c is None:
                errors.append(
                    f"container {cid!r} not found, skipping import")
                continue

            imported_containers += 1

            # Import audit entries
            for entry in cdata.get('audit_log', []):
                if not hasattr(c, '_audit_log'):
                    c._audit_log = []
                c._audit_log.append(entry)
                total_imported += 1

            # Import OOM events
            oom_events = cdata.get('oom_events', [])
            if oom_events:
                existing_oom = getattr(c, '_oom_events', [])
                existing_oom.extend(oom_events)
                c._oom_events = existing_oom[-50:]  # keep bounded
                total_imported += len(oom_events)

            # Import SLA violations
            sla_violations = cdata.get('sla_violations', [])
            if sla_violations:
                sla = getattr(c, '_sla', {})
                existing_v = sla.get('violations', [])
                existing_v.extend(sla_violations)
                sla['violations'] = existing_v[-200:]  # keep bounded
                c._sla = sla
                total_imported += len(sla_violations)

            # Import remediation history
            rem_history = cdata.get('remediation_history', [])
            if rem_history:
                rem = getattr(c, '_remediation', {})
                existing_r = rem.get('history', [])
                existing_r.extend(rem_history)
                rem['history'] = existing_r[-500:]  # keep bounded
                c._remediation = rem
                total_imported += len(rem_history)

        self._record_event(
            'event_log_import', 'system',
            f"imported {total_imported} events into "
            f"{imported_containers} containers")

        return {
            'imported_containers': imported_containers,
            'total_imported': total_imported,
            'errors': errors,
        }

    # ------------------------------------------------------------------
    # Event log compression (long-term archival)
    # ------------------------------------------------------------------

    def compress_event_log(
        self,
        data: Dict[str, Any],
        keep_recent: int = 100,
        summarize_older: bool = True,
    ) -> Dict[str, Any]:
        """Compress an exported event log for long-term archival.

        Keeps the most recent events in full detail and summarizes
        older events into statistical aggregates. This reduces storage
        while preserving trend data.

        Args:
            data: The exported event log dict (from export_event_log).
            keep_recent: Number of recent events to keep in full.
            summarize_older: Whether to create summaries of older events.

        Returns:
            Dict with compressed containers, compression stats.
        """
        compressed_containers: List[Dict[str, Any]] = []
        original_events = 0
        compressed_events = 0

        for cdata in data.get('containers', []):
            cc: Dict[str, Any] = {
                'container_id': cdata.get('container_id'),
                'name': cdata.get('name'),
                'state': cdata.get('state'),
            }

            # Compress lifecycle events
            lifecycle = cdata.get('lifecycle_events', [])
            original_events += len(lifecycle)
            if len(lifecycle) > keep_recent:
                recent = lifecycle[-keep_recent:]
                older = lifecycle[:-keep_recent]
                compressed_events += len(recent)
                if summarize_older and older:
                    summary = self._summarize_events(older)
                    cc['lifecycle_events'] = recent
                    cc['lifecycle_summary'] = summary
                    compressed_events += 1  # summary counts as 1
                else:
                    cc['lifecycle_events'] = recent
            else:
                cc['lifecycle_events'] = lifecycle
                compressed_events += len(lifecycle)

            # Compress audit log
            audit = cdata.get('audit_log', [])
            original_events += len(audit)
            if len(audit) > keep_recent:
                recent_audit = audit[-keep_recent:]
                older_audit = audit[:-keep_recent]
                compressed_events += len(recent_audit)
                if summarize_older and older_audit:
                    summary = self._summarize_events(older_audit)
                    cc['audit_log'] = recent_audit
                    cc['audit_summary'] = summary
                    compressed_events += 1
                else:
                    cc['audit_log'] = recent_audit
            else:
                cc['audit_log'] = audit
                compressed_events += len(audit)

            # OOM events (usually rare, keep all)
            oom = cdata.get('oom_events', [])
            original_events += len(oom)
            cc['oom_events'] = oom
            compressed_events += len(oom)

            # SLA violations (usually rare, keep all)
            sla = cdata.get('sla_violations', [])
            original_events += len(sla)
            cc['sla_violations'] = sla
            compressed_events += len(sla)

            # Remediation history
            rem = cdata.get('remediation_history', [])
            original_events += len(rem)
            if len(rem) > keep_recent:
                recent_rem = rem[-keep_recent:]
                older_rem = rem[:-keep_recent]
                compressed_events += len(recent_rem)
                if summarize_older and older_rem:
                    summary = self._summarize_events(older_rem)
                    cc['remediation_history'] = recent_rem
                    cc['remediation_summary'] = summary
                    compressed_events += 1
                else:
                    cc['remediation_history'] = recent_rem
            else:
                cc['remediation_history'] = rem
                compressed_events += len(rem)

            compressed_containers.append(cc)

        self._record_event(
            'event_log_compressed', 'system',
            f"compressed {original_events} -> {compressed_events} events "
            f"across {len(compressed_containers)} containers")

        return {
            'export_time': data.get('export_time', 0),
            'compressed_at': time.time(),
            'original_events': original_events,
            'compressed_events': compressed_events,
            'compression_ratio': (
                round(1 - compressed_events / original_events, 3)
                if original_events > 0 else 0
            ),
            'containers': compressed_containers,
            'metadata': data.get('metadata', {}),
        }

    def _summarize_events(
        self,
        events: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Create a statistical summary of a list of events."""
        if not events:
            return {'count': 0}

        # Count by kind/type/action
        by_kind: Dict[str, int] = {}
        for e in events:
            kind = e.get('kind', e.get('action', e.get('type', 'unknown')))
            by_kind[kind] = by_kind.get(kind, 0) + 1

        # Time range
        timestamps = [
            e.get('timestamp', e.get('time', 0))
            for e in events if e.get('timestamp') or e.get('time')
        ]
        first_time = min(timestamps) if timestamps else 0
        last_time = max(timestamps) if timestamps else 0

        return {
            'count': len(events),
            'by_kind': by_kind,
            'first_timestamp': first_time,
            'last_timestamp': last_time,
            'span_seconds': last_time - first_time if timestamps else 0,
        }

    # ------------------------------------------------------------------
    # Event log archival scheduling
    # ------------------------------------------------------------------

    def configure_archive_schedule(
        self,
        enabled: bool = True,
        interval_s: float = 86400.0,
        keep_recent: int = 500,
        auto_compress: bool = True,
        max_archives: int = 30,
    ) -> Dict[str, Any]:
        """Configure automatic event log archival scheduling.

        When enabled, the system periodically compresses and archives
        the event logs, retaining the last ``max_archives`` compressed
        snapshots.

        Args:
            enabled: Whether scheduling is active.
            interval_s: Seconds between archives (default: 86400 = daily).
            keep_recent: Number of recent events to keep uncompressed.
            auto_compress: Whether to auto-compress during archival.
            max_archives: Maximum archive snapshots to retain.

        Returns:
            Dict with the schedule configuration.
        """
        if not hasattr(self, '_archive_schedule'):
            self._archive_schedule = {}

        self._archive_schedule.update({
            'enabled': enabled,
            'interval_s': interval_s,
            'keep_recent': keep_recent,
            'auto_compress': auto_compress,
            'max_archives': max_archives,
            'last_archive_time': (
                self._archive_schedule.get('last_archive_time')
            ),
            'archive_count': (
                self._archive_schedule.get('archive_count', 0)
            ),
        })

        self._record_event(
            'archive_schedule_configured', 'system',
            f"enabled={enabled}, interval={interval_s}s, "
            f"keep_recent={keep_recent}")

        return {
            'schedule': dict(self._archive_schedule),
        }

    def get_archive_schedule(self) -> Dict[str, Any]:
        """Get the archive schedule configuration."""
        schedule = getattr(self, '_archive_schedule', {})
        return {
            'schedule': dict(schedule) if schedule else {},
            'status': 'set' if schedule else 'unset',
        }

    def disable_archive_schedule(self) -> Dict[str, Any]:
        """Disable archive scheduling."""
        if hasattr(self, '_archive_schedule'):
            self._archive_schedule['enabled'] = False
        return {'disabled': True}

    def run_archive_now(self) -> Dict[str, Any]:
        """Perform an immediate event log archival.

        Exports, compresses, and stores the compressed log. Returns
        the archival result.
        """
        schedule = getattr(self, '_archive_schedule', {})
        keep_recent = schedule.get('keep_recent', 500)
        auto_compress = schedule.get('auto_compress', True)
        max_archives = schedule.get('max_archives', 30)

        # Export all events
        export_data = self.export_event_log()

        # Compress if enabled
        if auto_compress:
            compressed = self.compress_event_log(
                export_data, keep_recent=keep_recent)
        else:
            compressed = export_data

        # Store the archive
        if not hasattr(self, '_archives'):
            self._archives = []

        archive_entry = {
            'timestamp': time.time(),
            'original_events': export_data.get('total_events', 0),
            'compressed_events': compressed.get('compressed_events', 0),
            'compression_ratio': compressed.get('compression_ratio', 0),
            'container_count': len(compressed.get('containers', [])),
            'data': compressed,
        }

        self._archives.append(archive_entry)

        # Rolling window: keep max_archives
        if len(self._archives) > max_archives:
            self._archives = self._archives[-max_archives:]

        # Update schedule stats
        schedule['last_archive_time'] = time.time()
        schedule['archive_count'] = schedule.get('archive_count', 0) + 1

        self._record_event(
            'archive_completed', 'system',
            f"archived {archive_entry['original_events']} -> "
            f"{archive_entry['compressed_events']} events")

        return {
            'timestamp': archive_entry['timestamp'],
            'original_events': archive_entry['original_events'],
            'compressed_events': archive_entry['compressed_events'],
            'compression_ratio': archive_entry['compression_ratio'],
            'archive_count': schedule['archive_count'],
        }

    def list_archives(self, tail: Optional[int] = None) -> List[Dict[str, Any]]:
        """List stored archives (metadata only, not full data)."""
        archives = getattr(self, '_archives', [])
        result = []
        for a in archives:
            result.append({
                'timestamp': a['timestamp'],
                'original_events': a['original_events'],
                'compressed_events': a['compressed_events'],
                'compression_ratio': a['compression_ratio'],
                'container_count': a['container_count'],
            })
        result = list(reversed(result))  # newest first
        if tail is not None:
            result = result[:tail]
        return result

    def get_archive(self, index: int) -> Optional[Dict[str, Any]]:
        """Get a specific archive by index (0 = most recent)."""
        archives = getattr(self, '_archives', [])
        if not archives or index < 0 or index >= len(archives):
            return None
        # newest first, so reverse
        reversed_archives = list(reversed(archives))
        return reversed_archives[index]

    # ------------------------------------------------------------------
    # Multi-tenant fair-share enforcement
    # ------------------------------------------------------------------

    def set_tenant_config(
        self,
        owner: str,
        priority: int = 0,
        weight: float = 1.0,
        burstable_pct: float = 20.0,
        enforce: bool = True,
        eviction_policy: str = "lowest_priority",
    ) -> Dict[str, Any]:
        """Configure multi-tenant enforcement parameters.

        Args:
            owner: Tenant identifier.
            priority: Numeric priority (higher = more important).
            weight: Fair-share weight relative to other tenants.
            burstable_pct: How far above quota a tenant can burst.
            enforce: Whether to actively enforce quotas.
            eviction_policy: What to do when a tenant exceeds quota:
                ``lowest_priority`` evict lowest-priority tenant,
                ``throttle`` throttle the exceeding tenant,
                ``alert`` only alert, ``none`` do nothing.

        Returns:
            The tenant config dict.
        """
        valid_policies = {
            'lowest_priority', 'throttle', 'alert', 'none',
        }
        if eviction_policy not in valid_policies:
            raise ValueError(
                f"invalid eviction_policy {eviction_policy!r}, "
                f"must be one of {sorted(valid_policies)}")

        config = self._tenant_configs.get(owner, {})
        config['priority'] = priority
        config['weight'] = weight
        config['burstable_pct'] = burstable_pct
        config['enforce'] = enforce
        config['eviction_policy'] = eviction_policy
        config['updated_at'] = time.time()
        self._tenant_configs[owner] = config

        self._record_event(
            'tenant_config_set', owner,
            f"priority={priority}, weight={weight}, "
            f"enforce={enforce}, policy={eviction_policy}")

        return {
            'owner': owner,
            'config': dict(config),
        }

    def get_tenant_config(self, owner: str) -> Dict[str, Any]:
        """Get tenant configuration."""
        config = self._tenant_configs.get(owner, {})
        return {
            'owner': owner,
            'config': dict(config) if config else {},
            'status': 'set' if config else 'unset',
        }

    def list_tenant_configs(self) -> Dict[str, Any]:
        """List all tenant configurations."""
        return {
            'tenants': {
                k: dict(v) for k, v in self._tenant_configs.items()
            },
            'count': len(self._tenant_configs),
        }

    def calculate_fair_share(
        self,
        resource: str = "memory_mb",
    ) -> Dict[str, Any]:
        """Calculate fair-share allocation across all tenants.

        Uses weighted fair queuing: each tenant's share is
        ``weight / sum(all_weights) * total_resource``.

        Args:
            resource: Resource to calculate share for.
                One of ``memory_mb``, ``pid_limit``.

        Returns:
            Dict with per-tenant fair share, usage, and whether
            they're over their share.
        """
        quota_key_map = {
            'memory_mb': 'memory_mb',
            'pid_limit': 'pid_limit',
        }
        quota_key = quota_key_map.get(resource, resource)

        # Gather tenants and their total quota
        tenants: Dict[str, Dict[str, Any]] = {}
        for owner, quota in self._quotas.items():
            limit = quota.get(quota_key)
            if limit is not None:
                tc = self._tenant_configs.get(owner, {})
                weight = tc.get('weight', 1.0)
                tenants[owner] = {
                    'quota': limit,
                    'weight': weight,
                    'usage': 0,
                }

        if not tenants:
            return {
                'resource': resource,
                'tenants': {},
                'total_quota': 0,
                'total_weight': 0,
            }

        total_quota = sum(t['quota'] for t in tenants.values())
        total_weight = sum(t['weight'] for t in tenants.values())

        # Sum actual usage per owner
        for c in self.containers.values():
            if c.state == ContainerState.TERMINATED:
                continue
            owner = getattr(c.config, 'owner', 'default')
            if owner in tenants:
                if resource == 'memory_mb':
                    tenants[owner]['usage'] += c.config.limits.memory_mb
                elif resource == 'pid_limit':
                    tenants[owner]['usage'] += c.config.limits.pid_limit

        # Calculate fair share and status
        result_tenants: Dict[str, Dict[str, Any]] = {}
        for owner, info in tenants.items():
            fair_share = (
                (info['weight'] / total_weight * total_quota)
                if total_weight > 0 else 0
            )
            tc = self._tenant_configs.get(owner, {})
            burstable_pct = tc.get('burstable_pct', 20.0)
            burst_limit = fair_share * (1 + burstable_pct / 100)
            usage = info['usage']
            pct_of_share = (
                (usage / fair_share * 100) if fair_share > 0 else 0
            )
            status = 'ok'
            if usage > burst_limit:
                status = 'over_burst'
            elif usage > fair_share:
                status = 'over_share'

            result_tenants[owner] = {
                'quota': info['quota'],
                'fair_share': round(fair_share, 1),
                'burst_limit': round(burst_limit, 1),
                'usage': usage,
                'pct_of_share': round(pct_of_share, 1),
                'status': status,
                'weight': info['weight'],
            }

        return {
            'resource': resource,
            'tenants': result_tenants,
            'total_quota': total_quota,
            'total_weight': total_weight,
        }

    def enforce_tenant_quotas(
        self,
    ) -> List[Dict[str, Any]]:
        """Enforce tenant quotas across all tenants.

        Checks each tenant's usage against their fair share and
        burst limit, and returns enforcement actions to take.

        Returns:
            List of enforcement entries with tenant, status,
            action, and affected containers.
        """
        actions = []
        for owner, quota in self._quotas.items():
            tc = self._tenant_configs.get(owner, {})
            if not tc.get('enforce', False):
                continue

            eviction_policy = tc.get('eviction_policy', 'alert')
            if eviction_policy == 'none':
                continue

            # Get all containers for this owner
            owner_containers = [
                c for c in self.containers.values()
                if (getattr(c.config, 'owner', 'default') == owner
                    and c.state != ContainerState.TERMINATED)
            ]

            # Check memory quota
            mem_limit = quota.get('memory_mb')
            if mem_limit is not None:
                total_mem = sum(
                    c.config.limits.memory_mb for c in owner_containers)
                if total_mem > mem_limit:
                    action = {
                        'owner': owner,
                        'resource': 'memory',
                        'usage': total_mem,
                        'limit': mem_limit,
                        'overage': total_mem - mem_limit,
                        'policy': eviction_policy,
                        'containers': [c.id for c in owner_containers],
                    }
                    if eviction_policy == 'throttle':
                        action['recommended_action'] = 'throttle_containers'
                    elif eviction_policy == 'lowest_priority':
                        action['recommended_action'] = 'evict_lowest'
                    elif eviction_policy == 'alert':
                        action['recommended_action'] = 'alert_only'
                    else:
                        action['recommended_action'] = 'none'
                    actions.append(action)

            # Check container count
            max_c = quota.get('max_containers')
            if max_c is not None and len(owner_containers) > max_c:
                action = {
                    'owner': owner,
                    'resource': 'containers',
                    'usage': len(owner_containers),
                    'limit': max_c,
                    'overage': len(owner_containers) - max_c,
                    'policy': eviction_policy,
                    'containers': [c.id for c in owner_containers],
                    'recommended_action': (
                        'evict_lowest' if eviction_policy == 'lowest_priority'
                        else eviction_policy
                    ),
                }
                actions.append(action)

        if actions:
            self._record_event(
                'tenant_enforcement', 'system',
                f"{len(actions)} tenants need enforcement")

        return actions

    def get_tenant_usage_summary(
        self,
    ) -> List[Dict[str, Any]]:
        """Get a summary of resource usage across all tenants.

        Returns:
            List of per-tenant summaries with usage, quota, and
            container counts.
        """
        tenant_usage: Dict[str, Dict[str, Any]] = {}

        for owner, quota in self._quotas.items():
            tenant_usage[owner] = {
                'owner': owner,
                'quota': dict(quota),
                'containers': 0,
                'memory_used': 0,
                'pids_used': 0,
                'status': 'idle',
            }

        for c in self.containers.values():
            if c.state == ContainerState.TERMINATED:
                continue
            owner = getattr(c.config, 'owner', 'default')
            if owner not in tenant_usage:
                continue
            tenant_usage[owner]['containers'] += 1
            tenant_usage[owner]['memory_used'] += c.config.limits.memory_mb
            tenant_usage[owner]['pids_used'] += c.config.limits.pid_limit
            tenant_usage[owner]['status'] = 'active'

        # Calculate utilization percentages
        result = []
        for owner, info in tenant_usage.items():
            quota = info['quota']
            mem_limit = quota.get('memory_mb')
            mem_pct = (
                round(info['memory_used'] / mem_limit * 100, 1)
                if mem_limit and mem_limit > 0 else 0
            )
            pid_limit = quota.get('pid_limit')
            pid_pct = (
                round(info['pids_used'] / pid_limit * 100, 1)
                if pid_limit and pid_limit > 0 else 0
            )
            tc = self._tenant_configs.get(owner, {})
            result.append({
                'owner': owner,
                'priority': tc.get('priority', 0),
                'weight': tc.get('weight', 1.0),
                'containers': info['containers'],
                'memory_used_mb': info['memory_used'],
                'memory_limit_mb': mem_limit,
                'memory_pct': mem_pct,
                'pids_used': info['pids_used'],
                'pid_limit': pid_limit,
                'pid_pct': pid_pct,
                'status': info['status'],
            })

        # Sort by priority descending
        result.sort(key=lambda x: -x.get('priority', 0))
        return result

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

    # ------------------------------------------------------------------
    # Enhanced health checks with automatic restart
    # ------------------------------------------------------------------

    def configure_health_check(
        self,
        container: Container,
        cmd: Optional[List[str]] = None,
        interval: Optional[float] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        auto_restart: bool = True,
        max_auto_restarts: int = 3,
        restart_cooldown_s: float = 60.0,
    ) -> Dict[str, Any]:
        """Configure health check settings for a container.

        Args:
            container: Target container.
            cmd: Health check command (e.g., ["curl", "-f", "http://localhost"]).
            interval: Seconds between checks.
            timeout: Max seconds per check.
            retries: Consecutive failures before marking unhealthy.
            auto_restart: Whether to restart on unhealthy.
            max_auto_restarts: Max auto-restarts before giving up.
            restart_cooldown_s: Min seconds between auto-restarts.

        Returns:
            Dict with the health check configuration.
        """
        cfg = container.config
        if cmd is not None:
            cfg.health_check_cmd = cmd
        if interval is not None:
            cfg.health_check_interval = interval
        if timeout is not None:
            cfg.health_check_timeout = timeout
        if retries is not None:
            cfg.health_check_retries = retries

        # Auto-restart settings
        if not hasattr(container, '_health_restart'):
            container._health_restart = {}
        container._health_restart.update({
            "auto_restart": auto_restart,
            "max_auto_restarts": max_auto_restarts,
            "restart_cooldown_s": restart_cooldown_s,
            "restart_count": 0,
            "last_restart_time": 0,
            "restart_history": [],
        })

        logger.info(
            "configure_health_check: %s cmd=%s interval=%.1f auto_restart=%s",
            container.id, cmd, cfg.health_check_interval, auto_restart,
        )
        return {
            "container_id": container.id,
            "health_check_cmd": cfg.health_check_cmd,
            "interval": cfg.health_check_interval,
            "timeout": cfg.health_check_timeout,
            "retries": cfg.health_check_retries,
            "auto_restart": auto_restart,
            "max_auto_restarts": max_auto_restarts,
        }

    def trigger_health_check(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Manually trigger a health check and return the result.

        Returns:
            Dict with ``healthy``, ``exit_code``, ``output``, ``duration_s``.
        """
        if not container.config.health_check_cmd:
            return {
                "container_id": container.id,
                "healthy": False,
                "error": "no health check command configured",
            }

        start = time.time()
        try:
            result = self.container_exec(
                container,
                container.config.health_check_cmd,
                timeout_s=container.config.health_check_timeout,
            )
            exit_code = result.get("exit_code", -1)
            output = (result.get("stdout", "") + result.get("stderr", ""))[:1000]
            healthy = exit_code == 0
        except Exception as e:
            exit_code = -1
            output = str(e)
            healthy = False

        duration = time.time() - start

        # Update container health state
        container.health_last_check = time.time()
        container.health_last_output = output
        if healthy:
            container.health_failures = 0
            container.health_status = "healthy"
        else:
            container.health_failures += 1
            if container.health_failures >= container.config.health_check_retries:
                container.health_status = "unhealthy"
                # Check if auto-restart should trigger
                self._maybe_auto_restart(container)

        return {
            "container_id": container.id,
            "healthy": healthy,
            "exit_code": exit_code,
            "output": output,
            "duration_s": round(duration, 3),
            "failures": container.health_failures,
            "status": container.health_status,
        }

    def _maybe_auto_restart(self, container: Container) -> None:
        """Check if auto-restart should be triggered and do it."""
        if not hasattr(container, '_health_restart') or \
                not container._health_restart.get("auto_restart"):
            return

        hr = container._health_restart
        now = time.time()

        # Check if we've exceeded max restarts
        if hr["restart_count"] >= hr["max_auto_restarts"]:
            logger.warning(
                "auto_restart: %s exceeded max restarts (%d), not restarting",
                container.id, hr["max_auto_restarts"],
            )
            return

        # Check cooldown
        last_restart = hr.get("last_restart_time", 0)
        if (now - last_restart) < hr["restart_cooldown_s"]:
            logger.debug(
                "auto_restart: %s in cooldown, skipping",
                container.id,
            )
            return

        # Perform the restart
        logger.warning(
            "auto_restart: restarting %s (attempt %d/%d)",
            container.id, hr["restart_count"] + 1, hr["max_auto_restarts"],
        )
        try:
            self.stop(container, timeout=5)
            time.sleep(1)
            self.spawn(container.config)
            hr["restart_count"] += 1
            hr["last_restart_time"] = now
            container.health_failures = 0
            container.health_status = "starting"
            # Record event
            event = {
                "timestamp": now,
                "attempt": hr["restart_count"],
                "max": hr["max_auto_restarts"],
                "reason": "health_check_unhealthy",
            }
            hr["restart_history"].append(event)
            if len(hr["restart_history"]) > 50:
                hr["restart_history"] = hr["restart_history"][-50:]
            self._record_event(
                "auto_restart", container.id,
                f"attempt {hr['restart_count']}/{hr['max_auto_restarts']}",
            )
        except Exception as e:
            logger.error(
                "auto_restart: failed to restart %s: %s",
                container.id, e,
            )

    def reset_health_restart_count(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Reset the auto-restart counter (e.g., after manual intervention).

        Returns:
            Dict with ``reset``, ``previous_count``.
        """
        if not hasattr(container, '_health_restart') or \
                not container._health_restart:
            return {
                "container_id": container.id,
                "reset": False,
                "previous_count": 0,
            }
        hr = container._health_restart
        prev = hr["restart_count"]
        hr["restart_count"] = 0
        return {
            "container_id": container.id,
            "reset": True,
            "previous_count": prev,
        }

    def get_health_restart_history(
        self,
        container: Container,
        tail: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get auto-restart history for a container."""
        if not hasattr(container, '_health_restart') or \
                not container._health_restart:
            return []
        history = container._health_restart.get("restart_history", [])
        if tail is not None:
            return list(history[-tail:])
        return list(history)

    def get_health_check_config(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get the full health check configuration."""
        cfg = container.config
        hr = getattr(container, '_health_restart', {})
        return {
            "container_id": container.id,
            "health_check_cmd": cfg.health_check_cmd,
            "interval": cfg.health_check_interval,
            "timeout": cfg.health_check_timeout,
            "retries": cfg.health_check_retries,
            "auto_restart": hr.get("auto_restart", False),
            "max_auto_restarts": hr.get("max_auto_restarts", 0),
            "restart_cooldown_s": hr.get("restart_cooldown_s", 60.0),
            "restart_count": hr.get("restart_count", 0),
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
            # Start SLA tracking
            self.start_sla_tracking(container)
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
    # Alert history management (enhanced)
    # ------------------------------------------------------------------

    def acknowledge_alert(
        self,
        container: Container,
        alert_index: int,
        acknowledged_by: str = "system",
    ) -> Optional[Dict[str, Any]]:
        """Acknowledge an alert in the history.

        Args:
            container: Target container.
            alert_index: Index of the alert to acknowledge.
            acknowledged_by: Who acknowledged the alert.

        Returns:
            The updated alert dict, or None if index is invalid.
        """
        history = container._alert_history
        if alert_index < 0 or alert_index >= len(history):
            return None
        alert = history[alert_index]
        alert["acknowledged"] = True
        alert["acknowledged_at"] = time.time()
        alert["acknowledged_by"] = acknowledged_by
        return alert

    def suppress_alert(
        self,
        container: Container,
        resource: str,
        level: Optional[str] = None,
        duration_s: float = 3600.0,
    ) -> Dict[str, Any]:
        """Suppress alerts for a resource (optionally at a specific level).

        Args:
            container: Target container.
            resource: Resource to suppress (e.g., "memory", "cpu").
            level: Optional level filter (e.g., "warning"). None = all levels.
            duration_s: Suppression duration in seconds.

        Returns:
            Dict with ``suppressed``, ``expires_at``, ``resource``.
        """
        if not hasattr(container, '_alert_suppressions'):
            container._alert_suppressions = []

        suppression = {
            "resource": resource,
            "level": level,
            "expires_at": time.time() + duration_s,
            "created_at": time.time(),
        }
        container._alert_suppressions.append(suppression)
        logger.info(
            "suppress_alert: %s %s level=%s for %.0fs",
            container.id, resource, level, duration_s,
        )
        return {
            "container_id": container.id,
            "suppressed": True,
            "resource": resource,
            "level": level,
            "expires_at": suppression["expires_at"],
            "duration_s": duration_s,
        }

    def unsuppress_alert(
        self,
        container: Container,
        resource: str,
        level: Optional[str] = None,
    ) -> bool:
        """Remove an active suppression rule.

        Returns True if a suppression was removed.
        """
        if not hasattr(container, '_alert_suppressions'):
            return False
        before = len(container._alert_suppressions)
        container._alert_suppressions = [
            s for s in container._alert_suppressions
            if not (s["resource"] == resource and s["level"] == level)
        ]
        return len(container._alert_suppressions) < before

    def is_alert_suppressed(
        self,
        container: Container,
        resource: str,
        level: Optional[str] = None,
    ) -> bool:
        """Check if alerts for a resource are currently suppressed."""
        if not hasattr(container, '_alert_suppressions'):
            return False
        now = time.time()
        for s in container._alert_suppressions:
            if s["expires_at"] < now:
                continue
            if s["resource"] != resource:
                continue
            if s["level"] is not None and s["level"] != level:
                continue
            return True
        return False

    def get_alert_statistics(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get statistics about alerts for a container.

        Returns:
            Dict with counts by level, resource, and time distribution.
        """
        history = container._alert_history
        if not history:
            return {
                "container_id": container.id,
                "total_alerts": 0,
                "by_level": {},
                "by_resource": {},
                "acknowledged_count": 0,
                "unacknowledged_count": 0,
            }

        by_level: Dict[str, int] = {}
        by_resource: Dict[str, int] = {}
        ack_count = 0
        unack_count = 0

        for a in history:
            level = a.get("level", "unknown")
            resource = a.get("resource", "unknown")
            by_level[level] = by_level.get(level, 0) + 1
            by_resource[resource] = by_resource.get(resource, 0) + 1
            if a.get("acknowledged"):
                ack_count += 1
            else:
                unack_count += 1

        # Time distribution (last 1h, 6h, 24h)
        now = time.time()
        time_buckets = {
            "last_1h": 0,
            "last_6h": 0,
            "last_24h": 0,
        }
        for a in history:
            age_s = now - a.get("timestamp", 0)
            if age_s < 3600:
                time_buckets["last_1h"] += 1
            if age_s < 21600:
                time_buckets["last_6h"] += 1
            if age_s < 86400:
                time_buckets["last_24h"] += 1

        return {
            "container_id": container.id,
            "total_alerts": len(history),
            "by_level": by_level,
            "by_resource": by_resource,
            "acknowledged_count": ack_count,
            "unacknowledged_count": unack_count,
            "time_distribution": time_buckets,
        }

    def get_active_suppressions(
        self,
        container: Container,
    ) -> List[Dict[str, Any]]:
        """Get currently active suppression rules."""
        if not hasattr(container, '_alert_suppressions'):
            return []
        now = time.time()
        return [
            s for s in container._alert_suppressions
            if s["expires_at"] > now
        ]

    def check_all_thresholds(
        self,
        containers: Optional[List[Container]] = None,
    ) -> List[Dict[str, Any]]:
        """Check resource usage against thresholds for all containers.

        Scans every container's current resource usage against its
        configured thresholds and fires alerts for any breaches.
        Respects suppressions.

        Args:
            containers: Containers to check (default: all).

        Returns:
            List of alerts that were fired.
        """
        if containers is None:
            containers = list(self.containers.values())

        fired: List[Dict[str, Any]] = []

        for c in containers:
            if c.state != ContainerState.RUNNING:
                continue
            if c.pid is None:
                continue

            thresholds = getattr(c.config, "alert_thresholds", None)
            if not thresholds:
                continue

            stats = self.container_stats(c)
            if not stats.get("available"):
                continue

            # Memory check
            mem_bytes = stats.get("memory_bytes", 0)
            mem_limit = stats.get("memory_limit_bytes", 0)
            if mem_limit > 0:
                mem_pct = mem_bytes / mem_limit * 100
                if mem_pct >= thresholds.get("memory_critical", 95):
                    if not self.is_alert_suppressed(c, "memory", "critical"):
                        a = self._fire_alert(
                            c, "memory", "critical",
                            f"{mem_pct:.1f}% ({mem_bytes}/{mem_limit})")
                        if a:
                            fired.append(a)
                elif mem_pct >= thresholds.get("memory_warning", 80):
                    if not self.is_alert_suppressed(c, "memory", "warning"):
                        a = self._fire_alert(
                            c, "memory", "warning",
                            f"{mem_pct:.1f}% ({mem_bytes}/{mem_limit})")
                        if a:
                            fired.append(a)

            # PID check
            pids = stats.get("pids_current", 0)
            pid_limit = stats.get("pids_limit", 0)
            if pid_limit > 0 and pid_limit < 0x7FFFFFFFFFFFFFFF:
                pid_pct = pids / pid_limit * 100
                if pid_pct >= thresholds.get("pid_critical", 95):
                    if not self.is_alert_suppressed(c, "pids", "critical"):
                        a = self._fire_alert(
                            c, "pids", "critical",
                            f"{pid_pct:.1f}% ({pids}/{pid_limit})")
                        if a:
                            fired.append(a)
                elif pid_pct >= thresholds.get("pid_warning", 80):
                    if not self.is_alert_suppressed(c, "pids", "warning"):
                        a = self._fire_alert(
                            c, "pids", "warning",
                            f"{pid_pct:.1f}% ({pids}/{pid_limit})")
                        if a:
                            fired.append(a)

            # OOM score check
            oom_score = getattr(c, "oom_score_adj", None)
            if oom_score is not None and oom_score >= thresholds.get(
                    "oom_critical", 900):
                if not self.is_alert_suppressed(c, "oom", "critical"):
                    a = self._fire_alert(
                        c, "oom", "critical",
                        f"oom_score_adj={oom_score}")
                    if a:
                        fired.append(a)

        return fired

    def get_threshold_status(
        self,
        containers: Optional[List[Container]] = None,
    ) -> List[Dict[str, Any]]:
        """Get current threshold status for all containers.

        Returns the current resource usage and threshold levels
        without firing alerts.

        Returns:
            List of dicts with ``container_id``, ``memory``,
            ``pids``, ``thresholds``, and ``status``.
        """
        if containers is None:
            containers = list(self.containers.values())

        results: List[Dict[str, Any]] = []

        for c in containers:
            if c.state != ContainerState.RUNNING:
                continue
            if c.pid is None:
                continue

            thresholds = getattr(c.config, "alert_thresholds", None)
            stats = self.container_stats(c)
            if not stats.get("available"):
                continue

            mem_bytes = stats.get("memory_bytes", 0)
            mem_limit = stats.get("memory_limit_bytes", 0)
            pids = stats.get("pids_current", 0)
            pid_limit = stats.get("pids_limit", 0)

            status = "ok"
            mem_pct = (mem_bytes / mem_limit * 100) if mem_limit > 0 else 0
            pid_pct = (pids / pid_limit * 100) if (
                pid_limit > 0 and pid_limit < 0x7FFFFFFFFFFFFFFF) else 0

            if thresholds:
                if mem_pct >= thresholds.get("memory_critical", 95):
                    status = "critical"
                elif mem_pct >= thresholds.get("memory_warning", 80):
                    status = "warning"
                if pid_pct >= thresholds.get("pid_critical", 95):
                    status = "critical"
                elif pid_pct >= thresholds.get("pid_warning", 80) and \
                        status != "critical":
                    status = "warning"

            results.append({
                "container_id": c.id,
                "name": c.config.name,
                "memory_pct": round(mem_pct, 1),
                "pid_pct": round(pid_pct, 1),
                "thresholds": thresholds,
                "status": status,
            })

        return results

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
    # Resource usage forecasting (predictive analytics)
    # ------------------------------------------------------------------

    @staticmethod
    def _linear_regression(
        x: List[float], y: List[float],
    ) -> Optional[Tuple[float, float, float]]:
        """Simple linear regression (least squares).

        Args:
            x: Independent variable values.
            y: Dependent variable values.

        Returns:
            Tuple of (slope, intercept, r_squared), or None if
            insufficient data.
        """
        n = len(x)
        if n < 2:
            return None
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_x2 = sum(xi * xi for xi in x)
        sum_y2 = sum(yi * yi for yi in y)

        denom = n * sum_x2 - sum_x * sum_x
        if abs(denom) < 1e-10:
            return None

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        # R-squared
        ss_res = sum((yi - (slope * xi + intercept)) ** 2
                     for xi, yi in zip(x, y))
        mean_y = sum_y / n
        ss_tot = sum((yi - mean_y) ** 2 for yi in y)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 1.0

        return slope, intercept, max(0.0, min(1.0, r_squared))

    def forecast_resource(
        self, container: Container,
        resource: str = "memory",
        horizon_s: float = 3600.0,
    ) -> Dict[str, Any]:
        """Forecast resource usage for a container.

        Uses linear regression on resource history to predict future
        usage and estimate time until resource exhaustion.

        Args:
            container: Target container.
            resource: Resource to forecast ("memory", "cpu", "pids").
            horizon_s: Forecast horizon in seconds (default 1 hour).

        Returns:
            Dict with ``current_value``, ``predicted_value``,
            ``trend_per_hour``, ``confidence`` (R-squared),
            ``time_to_limit_s`` (None if no limit or no trend).
        """
        history = self.get_resource_history(container)
        if len(history) < 2:
            return {
                "container_id": container.id,
                "resource": resource,
                "current_value": None,
                "predicted_value": None,
                "trend_per_hour": None,
                "confidence": 0.0,
                "time_to_limit_s": None,
                "sufficient_data": False,
            }

        # Extract time series
        timestamps = [s["timestamp"] for s in history]
        if resource == "memory":
            values = [s.get("memory_bytes", 0) for s in history]
            # Get limit
            stats = self.container_stats(container)
            limit = stats.get("memory_limit_bytes")
            if limit is None:
                limit = container.config.limits.memory_mb * 1024 * 1024
        elif resource == "cpu":
            values = [s.get("cpu_usage_usec", 0) for s in history]
            limit = None  # CPU has no hard limit
        elif resource == "pids":
            values = [s.get("pids_current", 0) for s in history]
            limit = container.config.limits.pid_limit
        else:
            return {
                "container_id": container.id,
                "resource": resource,
                "current_value": None,
                "predicted_value": None,
                "trend_per_hour": None,
                "confidence": 0.0,
                "time_to_limit_s": None,
                "sufficient_data": False,
            }

        # Normalize timestamps to seconds from start
        t0 = timestamps[0]
        x = [(t - t0) for t in timestamps]
        y = [float(v) for v in values]

        result = self._linear_regression(x, y)
        if result is None:
            return {
                "container_id": container.id,
                "resource": resource,
                "current_value": y[-1],
                "predicted_value": None,
                "trend_per_hour": None,
                "confidence": 0.0,
                "time_to_limit_s": None,
                "sufficient_data": True,
            }

        slope, intercept, r_squared = result

        # Predict at horizon
        predicted = slope * (x[-1] + horizon_s) + intercept
        predicted = max(0, predicted)

        # Trend per hour
        trend_per_hour = slope * 3600

        # Time to limit
        time_to_limit_s = None
        if limit is not None and slope > 0:
            current = y[-1]
            if current < limit:
                remaining = limit - current
                time_to_limit_s = remaining / slope if slope > 0 else None

        return {
            "container_id": container.id,
            "resource": resource,
            "current_value": y[-1],
            "predicted_value": round(predicted, 2),
            "trend_per_hour": round(trend_per_hour, 2),
            "confidence": round(r_squared, 4),
            "time_to_limit_s": round(time_to_limit_s, 1) if time_to_limit_s else None,
            "sufficient_data": True,
            "sample_count": len(history),
        }

    def forecast_all_resources(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Forecast all trackable resources for a container.

        Returns:
            Dict with forecasts for memory, CPU, and PIDs.
        """
        return {
            "container_id": container.id,
            "memory": self.forecast_resource(container, "memory"),
            "cpu": self.forecast_resource(container, "cpu"),
            "pids": self.forecast_resource(container, "pids"),
        }

    def estimate_time_to_exhaustion(
        self, container: Container,
        resource: str = "memory",
    ) -> Optional[Dict[str, Any]]:
        """Estimate time until a resource is exhausted.

        Args:
            container: Target container.
            resource: Resource to check.

        Returns:
            Dict with ``hours``, ``minutes``, ``seconds``, ``limit``,
            ``current``, or None if not applicable.
        """
        forecast = self.forecast_resource(container, resource)
        if not forecast.get("sufficient_data"):
            return None
        time_s = forecast.get("time_to_limit_s")
        if time_s is None:
            return None
        hours = int(time_s // 3600)
        minutes = int((time_s % 3600) // 60)
        seconds = int(time_s % 60)
        return {
            "resource": resource,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
            "total_seconds": round(time_s, 1),
            "limit": forecast.get("current_value"),
            "current": forecast.get("current_value"),
        }

    # ------------------------------------------------------------------
    # Capacity planning (predict future needs)
    # ------------------------------------------------------------------

    def plan_capacity(
        self,
        container: Container,
        horizon_days: int = 30,
    ) -> Dict[str, Any]:
        """Plan capacity needs for the near future.

        Analyzes current resource usage, growth trends, and forecasts
        resource needs over the specified horizon.

        Args:
            container: Target container.
            horizon_days: Number of days to plan for.

        Returns:
            Dict with ``resources`` (per-resource plans), ``summary``,
            ``recommended_limits``.
        """
        resources = ["memory", "cpu", "pids"]
        resource_plans: Dict[str, Any] = {}

        for res in resources:
            plan = self._plan_resource(container, res, horizon_days)
            resource_plans[res] = plan

        # Generate recommended limits
        recommended = {}
        cfg = container.config.limits

        if "memory" in resource_plans:
            mem_plan = resource_plans["memory"]
            if mem_plan.get("predicted_peak"):
                # Add 20% buffer
                recommended["memory_mb"] = max(
                    cfg.memory_mb,
                    int(mem_plan["predicted_peak"] * 1.2 / (1024 * 1024)),
                )

        if "pids" in resource_plans:
            pid_plan = resource_plans["pids"]
            if pid_plan.get("predicted_peak"):
                recommended["pid_limit"] = max(
                    cfg.pid_limit,
                    int(pid_plan["predicted_peak"] * 1.2),
                )

        # Summary
        issues = []
        for res, plan in resource_plans.items():
            if plan.get("risk_level") == "high":
                issues.append(f"{res}: high risk of exhaustion")
            elif plan.get("risk_level") == "medium":
                issues.append(f"{res}: moderate growth trend")

        summary = (
            f"Planning horizon: {horizon_days} days. "
            f"{len(issues)} potential issues. "
            f"Current: mem={cfg.memory_mb}MB pids={cfg.pid_limit}."
        )

        return {
            "container_id": container.id,
            "horizon_days": horizon_days,
            "resources": resource_plans,
            "recommended_limits": recommended,
            "summary": summary,
            "issue_count": len(issues),
        }

    def _plan_resource(
        self,
        container: Container,
        resource: str,
        horizon_days: int,
    ) -> Dict[str, Any]:
        """Plan capacity for a specific resource."""
        history = self.get_resource_history(container)
        if len(history) < 5:
            return {
                "resource": resource,
                "sufficient_data": False,
                "risk_level": "unknown",
            }

        values = self._extract_resource_values(history, resource)
        timestamps = [s.get("timestamp", 0) for s in history]

        if not values or not timestamps:
            return {
                "resource": resource,
                "sufficient_data": False,
                "risk_level": "unknown",
            }

        # Current stats
        current = values[-1]
        avg = sum(values) / len(values)
        peak = max(values)
        min_val = min(values)

        # Growth trend
        x = [float(t - timestamps[0]) for t in timestamps]
        y = [float(v) for v in values]
        regression = self._linear_regression(x, y)

        if regression is None:
            growth_rate_per_hour = 0
        else:
            slope = regression[0]
            # Convert to per-day growth rate
            growth_rate_per_hour = slope * 3600

        growth_rate_per_day = growth_rate_per_hour * 24

        # Predict future values
        horizon_hours = horizon_days * 24
        predicted_peak = current + (growth_rate_per_hour * horizon_hours)
        predicted_avg = avg + (growth_rate_per_hour * horizon_hours / 2)

        # Risk assessment
        cfg = container.config.limits
        if resource == "memory":
            limit = cfg.memory_mb * 1024 * 1024
            if limit > 0:
                current_pct = current / limit * 100
                predicted_pct = predicted_peak / limit * 100 if limit > 0 else 0
            else:
                current_pct = 0
                predicted_pct = 0
        elif resource == "pids":
            limit = cfg.pid_limit
            if limit > 0:
                current_pct = current / limit * 100
                predicted_pct = predicted_peak / limit * 100 if limit > 0 else 0
            else:
                current_pct = 0
                predicted_pct = 0
        else:
            limit = None
            current_pct = 0
            predicted_pct = 0

        # Determine risk level
        if predicted_pct > 90:
            risk_level = "high"
        elif predicted_pct > 70:
            risk_level = "medium"
        else:
            risk_level = "low"

        # Time to capacity
        time_to_capacity = None
        if growth_rate_per_hour > 0 and limit and limit > current:
            remaining = limit - current
            hours_to_full = remaining / growth_rate_per_hour
            days_to_full = hours_to_full / 24
            time_to_capacity = {
                "days": round(days_to_full, 1),
                "hours": round(hours_to_full, 1),
            }

        return {
            "resource": resource,
            "sufficient_data": True,
            "current": round(current, 2),
            "average": round(avg, 2),
            "peak": round(peak, 2),
            "min": round(min_val, 2),
            "current_limit": limit,
            "current_utilization_pct": round(current_pct, 1),
            "growth_rate_per_day": round(growth_rate_per_day, 2),
            "predicted_peak": round(predicted_peak, 2),
            "predicted_avg": round(predicted_avg, 2),
            "predicted_utilization_pct": round(predicted_pct, 1),
            "time_to_capacity": time_to_capacity,
            "risk_level": risk_level,
            "horizon_days": horizon_days,
        }

    def get_capacity_summary_all(
        self,
        horizon_days: int = 30,
    ) -> Dict[str, Any]:
        """Get capacity planning summary for all containers.

        Returns:
            Dict with per-container plans and aggregate stats.
        """
        plans = []
        for cid, c in self.containers.items():
            plan = self.plan_capacity(c, horizon_days)
            plans.append(plan)

        high_risk = sum(
            1 for p in plans
            if any(
                r.get("risk_level") == "high"
                for r in p.get("resources", {}).values()
            )
        )

        return {
            "horizon_days": horizon_days,
            "container_count": len(plans),
            "high_risk_count": high_risk,
            "containers": plans,
        }

    def _extract_resource_values(
        self,
        history: List[Dict[str, Any]],
        resource: str,
    ) -> List[float]:
        """Extract numeric values for a resource from history samples."""
        key_map = {
            "memory": "memory_bytes",
            "cpu": "cpu_usage_usec",
            "pids": "pids_current",
        }
        key = key_map.get(resource, resource)
        return [float(s.get(key, 0)) for s in history]

    def detect_anomalies(
        self,
        container: Container,
        resource: str = "memory",
        window_size: int = 20,
        sensitivity: float = 2.0,
    ) -> Dict[str, Any]:
        """Detect anomalies in resource usage using Z-score analysis.

        Uses a rolling window to compute mean and standard deviation,
        then flags values that deviate more than ``sensitivity``
        standard deviations from the mean.

        Args:
            container: Target container.
            resource: Resource to analyze (memory, cpu, pids).
            window_size: Number of recent samples to analyze.
            sensitivity: Number of standard deviations for outlier flag.

        Returns:
            Dict with ``anomalies`` (list), ``mean``, ``stddev``,
            ``sample_count``, ``resource``.
        """
        history = self.get_resource_history(container, tail=window_size)
        if len(history) < 3:
            return {
                "container_id": container.id,
                "resource": resource,
                "anomalies": [],
                "mean": None,
                "stddev": None,
                "sample_count": len(history),
                "insufficient_data": True,
            }

        values = self._extract_resource_values(history, resource)
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        stddev = variance ** 0.5

        anomalies = []
        for i, (h, v) in enumerate(zip(history, values)):
            if stddev > 0:
                z_score = abs(v - mean) / stddev
            else:
                z_score = 0.0

            if z_score > sensitivity:
                anomalies.append({
                    "index": i,
                    "timestamp": h.get("timestamp"),
                    "value": v,
                    "z_score": round(z_score, 3),
                    "deviation_pct": round((v - mean) / mean * 100, 1) if mean != 0 else 0,
                    "type": "spike" if v > mean else "dip",
                })

        return {
            "container_id": container.id,
            "resource": resource,
            "anomalies": anomalies,
            "mean": round(mean, 2),
            "stddev": round(stddev, 2),
            "sample_count": len(history),
            "insufficient_data": False,
            "sensitivity": sensitivity,
        }

    def detect_anomalies_all(
        self,
        container: Container,
        window_size: int = 20,
        sensitivity: float = 2.0,
    ) -> Dict[str, Any]:
        """Detect anomalies across all resources.

        Runs anomaly detection on memory, CPU, and PIDs usage.

        Returns:
            Dict with per-resource anomaly results.
        """
        resources = ["memory", "cpu", "pids"]
        result: Dict[str, Any] = {
            "container_id": container.id,
            "resources": {},
            "total_anomalies": 0,
        }

        for res in resources:
            detection = self.detect_anomalies(
                container, res, window_size, sensitivity,
            )
            result["resources"][res] = detection
            result["total_anomalies"] += len(detection.get("anomalies", []))

        return result

    def detect_spike(
        self,
        container: Container,
        resource: str = "memory",
        threshold_pct: float = 50.0,
    ) -> Dict[str, Any]:
        """Detect sudden spikes in resource usage.

        Compares the most recent value against the rolling average
        and flags if the change exceeds ``threshold_pct``.

        Args:
            container: Target container.
            resource: Resource to check.
            threshold_pct: Minimum percentage change to flag as spike.

        Returns:
            Dict with ``is_spike``, ``current``, ``previous``,
            ``change_pct``.
        """
        history = self.get_resource_history(container, tail=10)
        if len(history) < 2:
            return {
                "container_id": container.id,
                "resource": resource,
                "is_spike": False,
                "current": None,
                "previous": None,
                "change_pct": 0.0,
                "insufficient_data": True,
            }

        all_values = self._extract_resource_values(history, resource)
        current = all_values[-1]
        # Average of previous N-1 samples
        prev_values = all_values[:-1]
        prev_avg = sum(prev_values) / len(prev_values)

        if prev_avg > 0:
            change_pct = abs(current - prev_avg) / prev_avg * 100
        else:
            change_pct = 0.0 if current == 0 else 100.0

        is_spike = change_pct >= threshold_pct

        return {
            "container_id": container.id,
            "resource": resource,
            "is_spike": is_spike,
            "current": current,
            "previous_avg": round(prev_avg, 2),
            "change_pct": round(change_pct, 1),
            "threshold_pct": threshold_pct,
            "direction": "up" if current > prev_avg else "down",
            "insufficient_data": False,
        }

    def detect_anomaly_trend(
        self,
        container: Container,
        resource: str = "memory",
        window_size: int = 20,
    ) -> Dict[str, Any]:
        """Analyze anomaly trend over time.

        Detects if anomalies are becoming more frequent, which
        could indicate an impending resource exhaustion or leak.

        Returns:
            Dict with ``trend``, ``recent_rate``, ``overall_rate``,
            ``recent_anomaly_count``.
        """
        history = self.get_resource_history(container, tail=window_size * 3)
        if len(history) < 6:
            return {
                "container_id": container.id,
                "resource": resource,
                "trend": "insufficient_data",
                "recent_rate": 0.0,
                "overall_rate": 0.0,
                "recent_anomaly_count": 0,
            }

        # Compute mean/stddev over full window
        values = self._extract_resource_values(history, resource)
        mean = sum(values) / len(values)
        stddev = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5

        if stddev == 0:
            return {
                "container_id": container.id,
                "resource": resource,
                "trend": "stable",
                "recent_rate": 0.0,
                "overall_rate": 0.0,
                "recent_anomaly_count": 0,
            }

        # Count anomalies in recent half vs overall
        recent = history[-window_size:]
        overall_count = sum(
            1 for v in values if abs(v - mean) / stddev > 2.0
        )
        recent_count = sum(
            1 for h in recent
            if abs(h["value"] - mean) / stddev > 2.0
        )

        overall_rate = overall_count / len(values)
        recent_rate = recent_count / len(recent)

        if recent_rate > overall_rate * 1.5:
            trend = "increasing"
        elif recent_rate < overall_rate * 0.5:
            trend = "decreasing"
        else:
            trend = "stable"

        return {
            "container_id": container.id,
            "resource": resource,
            "trend": trend,
            "recent_rate": round(recent_rate, 3),
            "overall_rate": round(overall_rate, 3),
            "recent_anomaly_count": recent_count,
        }

    # ------------------------------------------------------------------
    # Health scoring engine
    # ------------------------------------------------------------------

    def calculate_health_score(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Calculate a composite health score (0-100) for a container.

        Combines signals from multiple subsystems:
        - Resource usage stability (anomaly count, 0-25 pts)
        - Budget compliance (0-25 pts)
        - OOM risk (0-20 pts)
        - SLA compliance (0-15 pts)
        - Health check status (0-15 pts)

        Args:
            container: Target container.

        Returns:
            Dict with ``score`` (0-100), ``grade`` (A-F),
            ``breakdown``, and ``recommendations``.
        """
        breakdown: Dict[str, Dict[str, Any]] = {}
        total_score = 0.0
        recommendations: List[str] = []

        # --- Resource stability (25 pts) ---
        anomaly_score = 25.0
        anomaly_result = self.detect_anomalies(container, resource="memory")
        anomaly_count = len(anomaly_result.get("anomalies", []))
        if anomaly_count > 5:
            anomaly_score = 0.0
            recommendations.append("High anomaly count: investigate memory usage")
        elif anomaly_count > 2:
            anomaly_score = 12.5
            recommendations.append("Moderate anomalies: monitor memory closely")
        breakdown["resource_stability"] = {
            "score": anomaly_score,
            "max": 25,
            "anomaly_count": anomaly_count,
        }
        total_score += anomaly_score

        # --- Budget compliance (25 pts) ---
        budget_score = 25.0
        budget = getattr(container, '_resource_budget', None)
        if budget:
            budget_status = self._check_single_budget(container, budget)
            violations = budget_status.get("violations", [])
            warnings = budget_status.get("warnings", [])
            if violations:
                budget_score = 0.0
                recommendations.append(
                    "Budget exceeded: consider increasing limits or "
                    "reducing workload")
            elif warnings:
                budget_score = 12.5
                recommendations.append("Budget warning: usage approaching limit")
        breakdown["budget_compliance"] = {
            "score": budget_score,
            "max": 25,
            "has_budget": budget is not None,
        }
        total_score += budget_score

        # --- OOM risk (20 pts) ---
        oom_score = 20.0
        oom_score_val = getattr(container, 'oom_score_adj', 0)
        if oom_score_val > 500:
            oom_score = 0.0
            recommendations.append("Very high OOM risk: increase memory limit")
        elif oom_score_val > 200:
            oom_score = 10.0
            recommendations.append("Elevated OOM risk: monitor memory usage")
        breakdown["oom_risk"] = {
            "score": oom_score,
            "max": 20,
            "oom_score_adj": oom_score_val,
        }
        total_score += oom_score

        # --- SLA compliance (15 pts) ---
        sla_score = 15.0
        sla = getattr(container, '_sla', {})
        sla_violations = sla.get("violations", [])
        if sla_violations:
            recent_violations = [
                v for v in sla_violations
                if v.get("timestamp", 0) > time.time() - 3600
            ]
            if recent_violations:
                sla_score = 0.0
                recommendations.append("Recent SLA violations: investigate")
            else:
                sla_score = 7.5
                recommendations.append("Past SLA violations: monitor closely")
        breakdown["sla_compliance"] = {
            "score": sla_score,
            "max": 15,
            "violation_count": len(sla_violations),
        }
        total_score += sla_score

        # --- Health check status (15 pts) ---
        health_score = 15.0
        health_status = getattr(container, 'health_status', None)
        if health_status == "unhealthy":
            health_score = 0.0
            recommendations.append("Container is unhealthy: check health check")
        elif health_status == "starting":
            health_score = 7.5
        breakdown["health_check"] = {
            "score": health_score,
            "max": 15,
            "status": health_status,
        }
        total_score += health_score

        # Grade
        if total_score >= 90:
            grade = "A"
        elif total_score >= 75:
            grade = "B"
        elif total_score >= 60:
            grade = "C"
        elif total_score >= 40:
            grade = "D"
        else:
            grade = "F"

        self._record_event(
            'health_scored', container.id,
            f"score={total_score:.1f}, grade={grade}")

        return {
            "container_id": container.id,
            "name": container.config.name,
            "score": round(total_score, 1),
            "grade": grade,
            "breakdown": breakdown,
            "recommendations": recommendations,
            "timestamp": time.time(),
        }

    def calculate_health_scores_all(
        self,
    ) -> Dict[str, Any]:
        """Calculate health scores for all running containers.

        Returns:
            Dict with per-container scores, fleet average,
            and unhealthy count.
        """
        scores = []
        for cid, c in self.containers.items():
            if c.state == ContainerState.RUNNING:
                score_result = self.calculate_health_score(c)
                scores.append(score_result)

        if not scores:
            return {
                "container_count": 0,
                "fleet_average": 0,
                "unhealthy_count": 0,
                "containers": [],
            }

        avg_score = sum(s["score"] for s in scores) / len(scores)
        unhealthy = sum(
            1 for s in scores if s["grade"] in ("D", "F")
        )

        return {
            "container_count": len(scores),
            "fleet_average": round(avg_score, 1),
            "unhealthy_count": unhealthy,
            "containers": sorted(
                scores, key=lambda s: s["score"]),
        }

    # ------------------------------------------------------------------
    # Resource comparison (cross-container)
    # ------------------------------------------------------------------

    def compare_containers(
        self,
        container_ids: List[str],
        resource: str = "memory",
    ) -> Dict[str, Any]:
        """Compare resource usage across multiple containers.

        Computes current usage, average, min, max, and rankings
        for the given resource across all specified containers.

        Args:
            container_ids: List of container IDs to compare.
            resource: Resource to compare (memory, cpu, pids).

        Returns:
            Dict with ``rankings``, ``statistics``, ``resource``.
        """
        key_map = {
            "memory": "memory_bytes",
            "cpu": "cpu_usage_usec",
            "pids": "pids_current",
        }
        stat_key = key_map.get(resource, resource)

        entries = []
        for cid in container_ids:
            c = self.containers.get(cid)
            if c is None:
                continue
            stats = self.container_stats(c)
            current = stats.get(stat_key, 0)
            history = self.get_resource_history(c)
            if history:
                values = [s.get(stat_key, 0) for s in history]
                avg_val = sum(values) / len(values)
                min_val = min(values)
                max_val = max(values)
            else:
                avg_val = current
                min_val = current
                max_val = current

            entries.append({
                "container_id": cid,
                "current": current,
                "average": round(avg_val, 2),
                "min": min_val,
                "max": max_val,
                "samples": len(history),
            })

        # Sort by current usage descending for rankings
        entries.sort(key=lambda e: e["current"], reverse=True)
        for i, e in enumerate(entries):
            e["rank"] = i + 1

        # Compute statistics
        if entries:
            currents = [e["current"] for e in entries]
            averages = [e["average"] for e in entries]
            statistics = {
                "total_current": sum(currents),
                "total_average": round(sum(averages), 2),
                "highest_current": currents[0],
                "lowest_current": currents[-1],
                "container_count": len(entries),
                "mean_current": round(sum(currents) / len(currents), 2),
            }
        else:
            statistics = {
                "total_current": 0,
                "total_average": 0,
                "highest_current": 0,
                "lowest_current": 0,
                "container_count": 0,
                "mean_current": 0,
            }

        return {
            "resource": resource,
            "rankings": entries,
            "statistics": statistics,
        }

    def compare_all_resources(
        self,
        container_ids: List[str],
    ) -> Dict[str, Any]:
        """Compare all resources across multiple containers.

        Returns a comparison result for memory, CPU, and PIDs.

        Returns:
            Dict with per-resource comparison and overall summary.
        """
        resources = ["memory", "cpu", "pids"]
        comparisons: Dict[str, Any] = {}
        total_anomalies = 0

        for res in resources:
            comp = self.compare_containers(container_ids, res)
            comparisons[res] = comp

        return {
            "container_ids": container_ids,
            "comparisons": comparisons,
            "container_count": len(container_ids),
        }

    def get_relative_usage(
        self,
        container_id: str,
        resource: str = "memory",
    ) -> Dict[str, Any]:
        """Get a container's relative resource usage vs peers.

        Shows how a container's usage compares to the average
        of all running containers.

        Args:
            container_id: Container to analyze.
            resource: Resource to check.

        Returns:
            Dict with ``percentile``, ``vs_average_pct``,
            ``rank``, ``total``.
        """
        key_map = {
            "memory": "memory_bytes",
            "cpu": "cpu_usage_usec",
            "pids": "pids_current",
        }
        stat_key = key_map.get(resource, resource)

        # Get all running containers
        all_entries = []
        target_value = 0
        for cid, c in self.containers.items():
            stats = self.container_stats(c)
            val = stats.get(stat_key, 0)
            all_entries.append((cid, val))
            if cid == container_id:
                target_value = val

        if not all_entries:
            return {
                "container_id": container_id,
                "resource": resource,
                "percentile": 0,
                "vs_average_pct": 0,
                "rank": 0,
                "total": 0,
            }

        all_entries.sort(key=lambda e: e[1])
        total = len(all_entries)
        rank = next(
            (i + 1 for i, (cid, _) in enumerate(all_entries) if cid == container_id),
            total,
        )
        percentile = round(rank / total * 100, 1)

        avg_val = sum(v for _, v in all_entries) / total
        if avg_val > 0:
            vs_avg_pct = round((target_value - avg_val) / avg_val * 100, 1)
        else:
            vs_avg_pct = 0.0

        return {
            "container_id": container_id,
            "resource": resource,
            "current": target_value,
            "average": round(avg_val, 2),
            "percentile": percentile,
            "vs_average_pct": vs_avg_pct,
            "rank": rank,
            "total": total,
        }

    def find_top_consumers(
        self,
        resource: str = "memory",
        top_n: int = 5,
    ) -> List[Dict[str, Any]]:
        """Find the top N resource consumers.

        Args:
            resource: Resource to rank by.
            top_n: Number of top consumers to return.

        Returns:
            List of dicts with ``container_id``, ``value``, ``rank``.
        """
        key_map = {
            "memory": "memory_bytes",
            "cpu": "cpu_usage_usec",
            "pids": "pids_current",
        }
        stat_key = key_map.get(resource, resource)

        entries = []
        for cid, c in self.containers.items():
            stats = self.container_stats(c)
            val = stats.get(stat_key, 0)
            entries.append({"container_id": cid, "value": val})

        entries.sort(key=lambda e: e["value"], reverse=True)
        for i, e in enumerate(entries[:top_n]):
            e["rank"] = i + 1

        return entries[:top_n]

    # ------------------------------------------------------------------
    # Resource usage recommendations (optimization suggestions)
    # ------------------------------------------------------------------

    def get_recommendations(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Generate resource optimization recommendations for a container.

        Analyzes the container's resource usage patterns and suggests
        optimizations for memory, CPU, PIDs, and general best practices.

        Returns:
            Dict with ``recommendations`` (list of suggestion dicts),
            ``score`` (0-100 optimization score), ``summary``.
        """
        stats = self.container_stats(container)
        history = self.get_resource_history(container)
        cfg = container.config
        recommendations = []

        # --- Memory analysis ---
        mem_bytes = stats.get("memory_bytes", 0)
        mem_limit = cfg.limits.memory_mb * 1024 * 1024
        if mem_limit > 0 and mem_bytes > 0:
            mem_pct = mem_bytes / mem_limit * 100
            if mem_pct < 10:
                recommendations.append({
                    "category": "memory",
                    "severity": "info",
                    "title": "Low memory utilization",
                    "detail": (
                        f"Using {mem_pct:.1f}% of allocated memory. "
                        f"Consider reducing memory_mb from "
                        f"{cfg.limits.memory_mb} to "
                        f"{max(64, int(cfg.limits.memory_mb * 0.5))} MB."
                    ),
                    "savings_estimate": f"~{cfg.limits.memory_mb // 2} MB",
                })
            elif mem_pct > 85:
                recommendations.append({
                    "category": "memory",
                    "severity": "warning",
                    "title": "High memory utilization",
                    "detail": (
                        f"Using {mem_pct:.1f}% of allocated memory. "
                        f"Risk of OOM. Consider increasing memory_mb."
                    ),
                    "savings_estimate": None,
                })

        # Memory trend analysis
        if history and len(history) >= 5:
            mem_values = self._extract_resource_values(history, "memory")
            if len(mem_values) >= 5:
                recent_avg = sum(mem_values[-5:]) / 5
                older_avg = sum(mem_values[:-5]) / max(1, len(mem_values) - 5)
                if older_avg > 0:
                    growth_pct = (recent_avg - older_avg) / older_avg * 100
                    if growth_pct > 20:
                        recommendations.append({
                            "category": "memory",
                            "severity": "warning",
                            "title": "Memory usage growing",
                            "detail": (
                                f"Memory usage increased {growth_pct:.1f}% "
                                f"over the observation window. "
                                f"Possible memory leak."
                            ),
                            "savings_estimate": None,
                        })

        # --- CPU analysis ---
        cpu_usec = stats.get("cpu_usage_usec", 0)
        throttle = stats.get("cpu_throttle_pct", 0)
        if throttle > 50:
            recommendations.append({
                "category": "cpu",
                "severity": "warning",
                "title": "High CPU throttling",
                "detail": (
                    f"CPU throttled {throttle}% of the time. "
                    f"Container needs more CPU quota."
                ),
                "savings_estimate": None,
            })
        elif throttle < 5 and cpu_usec > 0:
            recommendations.append({
                "category": "cpu",
                "severity": "info",
                "title": "Low CPU utilization",
                "detail": "CPU throttling is minimal. Could reduce CPU quota.",
                "savings_estimate": None,
            })

        # --- PID analysis ---
        pids = stats.get("pids_current", 0)
        pid_limit = cfg.limits.pid_limit
        if pid_limit > 0 and pids > 0:
            pid_pct = pids / pid_limit * 100
            if pid_pct < 20:
                recommendations.append({
                    "category": "pids",
                    "severity": "info",
                    "title": "Low PID utilization",
                    "detail": (
                        f"Using {pids}/{pid_limit} PIDs ({pid_pct:.1f}%). "
                        f"Consider reducing pid_limit."
                    ),
                    "savings_estimate": None,
                })

        # --- General best practices ---
        if not cfg.labels:
            recommendations.append({
                "category": "general",
                "severity": "info",
                "title": "No labels set",
                "detail": (
                    "Container has no labels. Add labels like "
                    "app, env, team for better organization."
                ),
                "savings_estimate": None,
            })

        if cfg.restart_policy == "none" or not cfg.restart_policy:
            recommendations.append({
                "category": "general",
                "severity": "info",
                "title": "No restart policy",
                    "detail": (
                    "Consider setting restart_policy to "
                    "'on-failure' or 'always' for production."
                ),
                "savings_estimate": None,
            })

        if not cfg.network:
            recommendations.append({
                "category": "general",
                "severity": "info",
                "title": "Networking disabled",
                "detail": "Container has no network access. Enable if needed.",
                "savings_estimate": None,
            })

        if cfg.sla_uptime_target == 0:
            recommendations.append({
                "category": "general",
                "severity": "info",
                "title": "No SLA configured",
                "detail": (
                    "No SLA uptime target. Set sla_uptime_target "
                    "for production containers."
                ),
                "savings_estimate": None,
            })

        # Compute optimization score (100 = perfect, deductions for issues)
        score = 100
        for r in recommendations:
            if r["severity"] == "warning":
                score -= 15
            elif r["severity"] == "info":
                score -= 5
        score = max(0, score)

        # Summary
        warnings = sum(1 for r in recommendations if r["severity"] == "warning")
        infos = sum(1 for r in recommendations if r["severity"] == "info")
        summary = (
            f"{len(recommendations)} recommendations "
            f"({warnings} warnings, {infos} info). "
            f"Optimization score: {score}/100."
        )

        return {
            "container_id": container.id,
            "recommendations": recommendations,
            "score": score,
            "summary": summary,
            "warning_count": warnings,
            "info_count": infos,
        }

    def get_recommendations_all(self) -> Dict[str, Any]:
        """Get recommendations for all running containers.

        Returns:
            Dict with per-container recommendations and aggregate stats.
        """
        all_recs = []
        total_score = 0
        count = 0

        for cid, c in self.containers.items():
            recs = self.get_recommendations(c)
            all_recs.append(recs)
            total_score += recs["score"]
            count += 1

        avg_score = round(total_score / count, 1) if count > 0 else 0

        return {
            "container_count": count,
            "average_score": avg_score,
            "containers": all_recs,
        }

    def get_recommendations_by_category(
        self,
        container: Container,
        category: str = "memory",
    ) -> Dict[str, Any]:
        """Get recommendations filtered by category.

        Args:
            container: Target container.
            category: Category filter (memory, cpu, pids, general).

        Returns:
            Dict with filtered ``recommendations``.
        """
        all_recs = self.get_recommendations(container)
        filtered = [
            r for r in all_recs["recommendations"]
            if r["category"] == category
        ]
        return {
            "container_id": container.id,
            "category": category,
            "recommendations": filtered,
            "count": len(filtered),
        }

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

    def set_cpu_weight(
        self, container: Container, weight: int,
    ) -> Dict[str, Any]:
        """Set the CPU weight for a container (cgroups v2 cpu.weight).

        CPU weight is a relative priority (1–10000, default 100).
        Higher weight means more CPU time.  Only effective on cgroups
        v2 hosts.

        Args:
            container: Target container.
            weight: CPU weight (1–10000).

        Returns:
            Dict with ``ok``, ``weight``, ``container_id``.
        """
        if not 1 <= weight <= 10000:
            return {
                "ok": False,
                "error": "weight must be 1..10000",
            }
        if not container.cgroup_paths:
            return {
                "ok": False,
                "error": "container has no cgroup paths",
            }
        # Find the v2 cgroup path
        for path_str in container.cgroup_paths:
            cpu_weight_file = os.path.join(path_str, "cpu.weight")
            if os.path.exists(cpu_weight_file):
                try:
                    with open(cpu_weight_file, "w") as f:
                        f.write(str(weight))
                    container.config.cpu_weight = weight
                    self._record_event(
                        "cpu_weight_set", container.id,
                        f"weight={weight}")
                    return {
                        "ok": True,
                        "weight": weight,
                        "container_id": container.id,
                    }
                except OSError as e:
                    return {
                        "ok": False,
                        "error": str(e),
                    }
        return {
            "ok": False,
            "error": "no cgroups v2 cpu.weight found",
        }

    def set_io_weight(
        self, container: Container, weight: int,
    ) -> Dict[str, Any]:
        """Set the I/O weight for a container (cgroups v2 io.weight).

        I/O weight is a relative priority (1–100, default 100).
        Higher weight means more I/O bandwidth.  Only effective on
        cgroups v2 hosts.

        Args:
            container: Target container.
            weight: I/O weight (1–100).

        Returns:
            Dict with ``ok``, ``weight``, ``container_id``.
        """
        if not 1 <= weight <= 100:
            return {
                "ok": False,
                "error": "weight must be 1..100",
            }
        if not container.cgroup_paths:
            return {
                "ok": False,
                "error": "container has no cgroup paths",
            }
        for path_str in container.cgroup_paths:
            io_weight_file = os.path.join(path_str, "io.weight")
            if os.path.exists(io_weight_file):
                try:
                    with open(io_weight_file, "w") as f:
                        f.write(str(weight))
                    container.config.io_weight = weight
                    self._record_event(
                        "io_weight_set", container.id,
                        f"weight={weight}")
                    return {
                        "ok": True,
                        "weight": weight,
                        "container_id": container.id,
                    }
                except OSError as e:
                    return {
                        "ok": False,
                        "error": str(e),
                    }
        return {
            "ok": False,
            "error": "no cgroups v2 io.weight found",
        }

    def get_priority(self, container: Container) -> Dict[str, Any]:
        """Get all priority-related parameters for a container.

        Returns:
            Dict with ``container_id``, ``nice_value``, ``cpu_weight``,
            ``io_weight``, ``cpu_affinity``.
        """
        return {
            "container_id": container.id,
            "nice_value": container.config.nice_value,
            "cpu_weight": getattr(container.config, "cpu_weight", None),
            "io_weight": getattr(container.config, "io_weight", None),
            "cpu_affinity": container.config.cpu_affinity,
        }

    # ------------------------------------------------------------------
    # Resource usage pattern recognition
    # ------------------------------------------------------------------

    def detect_usage_patterns(
        self,
        container: Container,
        window_size: int = 30,
    ) -> Dict[str, Any]:
        """Detect resource usage patterns (periodic, trend, step).

        Analyzes historical resource data to identify usage patterns
        that can inform optimization and capacity planning.

        Pattern types:
        - ``periodic``: regular ups and downs (e.g., daily cycles)
        - ``increasing``: steady upward trend
        - ``decreasing``: steady downward trend
        - ``step``: sudden level change
        - ``stable``: no significant pattern
        - ``bursty``: high variance, sporadic spikes

        Args:
            container: Target container.
            window_size: Number of samples to analyze.

        Returns:
            Dict with per-resource patterns and confidence scores.
        """
        history = self.get_resource_history(container, tail=window_size)
        patterns: Dict[str, Dict[str, Any]] = {}

        for resource in ("memory", "cpu", "pids"):
            values = self._extract_resource_values(history, resource)
            if len(values) < 5:
                patterns[resource] = {
                    "pattern": "insufficient_data",
                    "confidence": 0.0,
                    "sample_count": len(values),
                }
                continue

            pattern_result = self._analyze_single_pattern(values)
            patterns[resource] = pattern_result

        # Cross-resource correlation
        mem_values = self._extract_resource_values(history, "memory")
        cpu_values = self._extract_resource_values(history, "cpu")
        correlation = 0.0
        if len(mem_values) >= 5 and len(cpu_values) >= 5:
            min_len = min(len(mem_values), len(cpu_values))
            m = mem_values[:min_len]
            c = cpu_values[:min_len]
            mean_m = sum(m) / len(m)
            mean_c = sum(c) / len(c)
            cov = sum((a - mean_m) * (b - mean_c)
                      for a, b in zip(m, c)) / len(m)
            std_m = (sum((a - mean_m) ** 2 for a in m) / len(m)) ** 0.5
            std_c = (sum((b - mean_c) ** 2 for b in c) / len(c)) ** 0.5
            if std_m > 0 and std_c > 0:
                correlation = cov / (std_m * std_c)

        self._record_event(
            "pattern_detected", container.id,
            f"patterns: {list(patterns.keys())}, "
            f"correlation={correlation:.3f}")

        return {
            "container_id": container.id,
            "patterns": patterns,
            "memory_cpu_correlation": round(correlation, 4),
            "sample_count": len(history),
            "window_size": window_size,
        }

    def _analyze_single_pattern(
        self,
        values: List[float],
    ) -> Dict[str, Any]:
        """Analyze a single resource's value list for patterns."""
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        stddev = variance ** 0.5
        cv = stddev / mean if mean > 0 else 0  # coefficient of variation

        # Linear regression for trend detection
        x_vals = list(range(n))
        x_mean = (n - 1) / 2
        cov_xy = sum((x - x_mean) * (v - mean)
                     for x, v in zip(x_vals, values)) / n
        var_x = sum((x - x_mean) ** 2 for x in x_vals) / n
        slope = cov_xy / var_x if var_x > 0 else 0

        # Detect periodicity (autocorrelation at lag ~n/4)
        lag = max(1, n // 4)
        if n > lag * 2:
            mean_diff = mean
            numer = sum(
                (values[i] - mean) * (values[i + lag] - mean)
                for i in range(n - lag))
            denom = sum((v - mean) ** 2 for v in values)
            autocorr = numer / denom if denom > 0 else 0
        else:
            autocorr = 0

        # Detect step changes (max consecutive jump)
        diffs = [abs(values[i + 1] - values[i]) for i in range(n - 1)]
        max_jump = max(diffs) if diffs else 0
        avg_jump = sum(diffs) / len(diffs) if diffs else 0
        step_ratio = max_jump / avg_jump if avg_jump > 0 else 0

        # Determine pattern
        if cv > 0.5 and autocorr < 0.3:
            pattern = "bursty"
            confidence = min(cv * 100, 100)
        elif autocorr > 0.5:
            pattern = "periodic"
            confidence = autocorr * 100
        elif abs(slope) > stddev * 0.05 and stddev > 0:
            if slope > 0:
                pattern = "increasing"
            else:
                pattern = "decreasing"
            confidence = min(abs(slope) / stddev * 100, 100)
        elif step_ratio > 3.0:
            pattern = "step"
            confidence = min(step_ratio * 10, 100)
        else:
            pattern = "stable"
            confidence = max(0, 100 - cv * 200)

        return {
            "pattern": pattern,
            "confidence": round(confidence, 1),
            "mean": round(mean, 2),
            "stddev": round(stddev, 2),
            "cv": round(cv, 4),
            "slope": round(slope, 4),
            "autocorrelation": round(autocorr, 4),
            "sample_count": n,
        }

    def get_usage_optimization_actions(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Generate optimization actions based on detected patterns.

        Combines pattern detection with current configuration to
        recommend specific optimization actions.

        Args:
            container: Target container.

        Returns:
            Dict with ``actions`` (list of action dicts), ``priority``
            ranking, and ``estimated_savings``.
        """
        patterns = self.detect_usage_patterns(container)
        actions: List[Dict[str, Any]] = []

        # Memory optimization
        mem_pattern = patterns.get("patterns", {}).get("memory", {})
        mem_mean = mem_pattern.get("mean", 0)
        if mem_pattern.get("pattern") == "decreasing":
            actions.append({
                "action": "reduce_memory_limit",
                "resource": "memory",
                "current": container.config.limits.memory_mb,
                "suggested": max(64, int(mem_mean * 1.2 / (1024 * 1024))
                                 if mem_mean > 1024 * 1024
                                 else int(mem_mean * 1.2)),
                "reason": "Memory usage is decreasing",
                "priority": "medium",
                "estimated_savings_pct": 20,
            })
        elif (mem_pattern.get("pattern") in ("increasing", "step")
              and mem_mean > container.config.limits.memory_mb * 0.8):
            new_limit = int(container.config.limits.memory_mb * 1.25)
            actions.append({
                "action": "increase_memory_limit",
                "resource": "memory",
                "current": container.config.limits.memory_mb,
                "suggested": new_limit,
                "reason": "Memory usage approaching limit",
                "priority": "high",
                "estimated_savings_pct": 0,
            })
        elif mem_pattern.get("pattern") == "stable":
            margin = container.config.limits.memory_mb - mem_mean / (1024 * 1024)
            if margin > container.config.limits.memory_mb * 0.5:
                new_limit = max(64, int(mem_mean * 1.3 / (1024 * 1024))
                                if mem_mean > 1024 * 1024
                                else int(mem_mean * 1.3))
                actions.append({
                    "action": "rightsize_memory",
                    "resource": "memory",
                    "current": container.config.limits.memory_mb,
                    "suggested": new_limit,
                    "reason": "Stable usage well below limit",
                    "priority": "low",
                    "estimated_savings_pct": max(
                        0, int((1 - new_limit / container.config.limits.memory_mb)
                               * 100)),
                })

        # PID optimization
        pid_pattern = patterns.get("patterns", {}).get("pids", {})
        if pid_pattern.get("pattern") == "stable":
            pid_mean = pid_pattern.get("mean", 0)
            if pid_mean < container.config.limits.pid_limit * 0.3:
                new_pid = max(4, int(pid_mean * 2))
                actions.append({
                    "action": "rightsize_pids",
                    "resource": "pids",
                    "current": container.config.limits.pid_limit,
                    "suggested": new_pid,
                    "reason": "Stable PID usage well below limit",
                    "priority": "low",
                    "estimated_savings_pct": max(
                        0, int((1 - new_pid / container.config.limits.pid_limit)
                               * 100)),
                })

        # Bursty memory pattern
        if mem_pattern.get("pattern") == "bursty":
            actions.append({
                "action": "investigate_memory_bursts",
                "resource": "memory",
                "current": container.config.limits.memory_mb,
                "suggested": container.config.limits.memory_mb,
                "reason": "Bursty memory usage detected",
                "priority": "medium",
                "estimated_savings_pct": 0,
            })

        # High memory-CPU correlation
        corr = patterns.get("memory_cpu_correlation", 0)
        if corr > 0.8:
            actions.append({
                "action": "correlated_resources",
                "resource": "memory+cpu",
                "current": None,
                "suggested": None,
                "reason": (f"High memory-CPU correlation ({corr:.2f}): "
                           "scaling one may require scaling the other"),
                "priority": "medium",
                "estimated_savings_pct": 0,
            })

        # Sort by priority
        priority_order = {"high": 0, "medium": 1, "low": 2}
        actions.sort(key=lambda a: priority_order.get(
            a.get("priority", "low"), 3))

        total_savings = sum(
            a.get("estimated_savings_pct", 0) for a in actions)

        return {
            "container_id": container.id,
            "actions": actions,
            "action_count": len(actions),
            "total_estimated_savings_pct": total_savings,
            "patterns": patterns.get("patterns", {}),
        }

    # ------------------------------------------------------------------
    # Resource right-sizing (automatic limit adjustment)
    # ------------------------------------------------------------------

    def rightsize_container(
        self,
        container: Container,
        safety_margin_pct: float = 20.0,
        min_memory_mb: int = 64,
        max_memory_mb: int = 16384,
        min_pids: int = 4,
        max_pids: int = 1024,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Right-size a container's resource limits based on usage.

        Analyzes historical usage patterns and adjusts memory and
        PID limits to better match actual usage, with a configurable
        safety margin.

        Args:
            container: Target container.
            safety_margin_pct: Percentage buffer above observed max.
            min_memory_mb: Minimum memory limit to enforce.
            max_memory_mb: Maximum memory limit to enforce.
            min_pids: Minimum PID limit to enforce.
            max_pids: Maximum PID limit to enforce.
            dry_run: If True, report changes without applying.

        Returns:
            Dict with ``changes`` (list of adjustments),
            ``current_limits``, ``suggested_limits``, and
            ``applied`` flag.
        """
        history = self.get_resource_history(container, tail=50)
        changes: List[Dict[str, Any]] = []
        current_limits = {
            'memory_mb': container.config.limits.memory_mb,
            'pid_limit': container.config.limits.pid_limit,
        }

        if len(history) < 5:
            return {
                'container_id': container.id,
                'changes': [],
                'current_limits': current_limits,
                'suggested_limits': current_limits,
                'applied': False,
                'reason': 'insufficient_data',
            }

        # Analyze memory usage
        mem_values = [
            h.get('memory_bytes', 0) / (1024 * 1024)
            for h in history if h.get('memory_bytes')
        ]
        if mem_values:
            mem_max = max(mem_values)
            mem_mean = sum(mem_values) / len(mem_values)
            suggested_mem = int(
                mem_max * (1 + safety_margin_pct / 100))
            suggested_mem = max(
                min_memory_mb,
                min(max_memory_mb, suggested_mem))

            current_mem = container.config.limits.memory_mb
            if suggested_mem != current_mem:
                changes.append({
                    'resource': 'memory_mb',
                    'current': current_mem,
                    'suggested': suggested_mem,
                    'observed_max_mb': round(mem_max, 1),
                    'observed_mean_mb': round(mem_mean, 1),
                    'savings_pct': round(
                        (1 - suggested_mem / current_mem) * 100, 1)
                        if current_mem > 0 else 0,
                })

        # Analyze PID usage
        pid_values = [
            h.get('pids_current', 0)
            for h in history if h.get('pids_current') is not None
        ]
        if pid_values:
            pid_max = max(pid_values)
            pid_mean = sum(pid_values) / len(pid_values)
            suggested_pids = int(
                pid_max * (1 + safety_margin_pct / 100))
            suggested_pids = max(
                min_pids,
                min(max_pids, suggested_pids))

            current_pids = container.config.limits.pid_limit
            if suggested_pids != current_pids:
                changes.append({
                    'resource': 'pid_limit',
                    'current': current_pids,
                    'suggested': suggested_pids,
                    'observed_max': pid_max,
                    'observed_mean': round(pid_mean, 1),
                    'savings_pct': round(
                        (1 - suggested_pids / current_pids) * 100, 1)
                        if current_pids > 0 else 0,
                })

        # Apply changes if not dry run
        applied = False
        if changes and not dry_run:
            for change in changes:
                if change['resource'] == 'memory_mb':
                    old = container.config.limits.memory_mb
                    container.config.limits.memory_mb = change['suggested']
                    self._record_event(
                        'rightsize_memory', container.id,
                        f"{old} -> {change['suggested']} MB")
                elif change['resource'] == 'pid_limit':
                    old = container.config.limits.pid_limit
                    container.config.limits.pid_limit = change['suggested']
                    self._record_event(
                        'rightsize_pids', container.id,
                        f"{old} -> {change['suggested']}")
            applied = True

        suggested_limits = {
            'memory_mb': container.config.limits.memory_mb,
            'pid_limit': container.config.limits.pid_limit,
        }
        # Override suggested with computed values for dry_run
        if dry_run and changes:
            for change in changes:
                suggested_limits[change['resource']] = change['suggested']

        return {
            'container_id': container.id,
            'changes': changes,
            'current_limits': current_limits,
            'suggested_limits': suggested_limits,
            'applied': applied,
            'dry_run': dry_run,
            'safety_margin_pct': safety_margin_pct,
        }

    def rightsize_all_containers(
        self,
        safety_margin_pct: float = 20.0,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Right-size all running containers.

        Args:
            safety_margin_pct: Safety margin percentage.
            dry_run: If True, report without applying.

        Returns:
            Dict with per-container results and summary.
        """
        results = []
        for cid, c in self.containers.items():
            if c.state == ContainerState.RUNNING:
                result = self.rightsize_container(
                    c, safety_margin_pct=safety_margin_pct,
                    dry_run=dry_run)
                results.append(result)

        total_changes = sum(
            len(r.get('changes', [])) for r in results)
        applied = sum(
            1 for r in results if r.get('applied', False))

        return {
            'container_count': len(results),
            'total_changes': total_changes,
            'containers_applied': applied,
            'dry_run': dry_run,
            'containers': results,
        }

    # ------------------------------------------------------------------
    # Workload scheduling (priority queues)
    # ------------------------------------------------------------------

    def set_scheduling_priority(
        self, container: Container, priority: int,
    ) -> Dict[str, Any]:
        """Set the scheduling priority for a container.

        Priority determines the order in which containers are
        started when resources are constrained. Higher priority
        (lower number) = started first.

        Args:
            container: Target container.
            priority: Priority (0=highest, 99=lowest, default 50).

        Returns:
            Dict with ``ok``, ``priority``, ``container_id``.
        """
        if not 0 <= priority <= 99:
            return {
                "ok": False,
                "error": "priority must be 0..99",
            }
        container.config.scheduling_priority = priority
        self._record_event(
            "scheduling_priority_set", container.id,
            f"priority={priority}")
        return {
            "ok": True,
            "priority": priority,
            "container_id": container.id,
        }

    def get_scheduling_queue(
        self,
    ) -> List[Dict[str, Any]]:
        """Get all containers sorted by scheduling priority.

        Returns:
            List of dicts sorted by priority (lowest number first),
            each with ``id``, ``name``, ``state``, ``priority``,
            ``memory_bytes``, ``pids_current``.
        """
        entries: List[Dict[str, Any]] = []
        for c in self.containers.values():
            priority = getattr(c.config, "scheduling_priority", 50)
            stats = self.container_stats(c)
            entries.append({
                "id": c.id,
                "name": c.config.name,
                "state": c.state.value,
                "priority": priority,
                "memory_bytes": stats.get("memory_bytes", 0),
                "pids_current": stats.get("pids_current", 0),
            })
        entries.sort(key=lambda x: x["priority"])
        return entries

    def get_ready_containers(
        self,
    ) -> List[Dict[str, Any]]:
        """Get containers that are ready to run, sorted by priority.

        Returns CREATED containers sorted by scheduling priority
        (highest priority first), suitable for an operator to
        decide which to start next.

        Returns:
            List of dicts with ``id``, ``name``, ``priority``.
        """
        ready: List[Dict[str, Any]] = []
        for c in self.containers.values():
            if c.state == ContainerState.CREATED:
                priority = getattr(c.config, "scheduling_priority", 50)
                ready.append({
                    "id": c.id,
                    "name": c.config.name,
                    "priority": priority,
                })
        ready.sort(key=lambda x: x["priority"])
        return ready

    # ------------------------------------------------------------------
    # Batch operations (operate on multiple containers)
    # ------------------------------------------------------------------

    def _resolve_batch_targets(
        self,
        labels: Optional[Dict[str, str]] = None,
        name_pattern: Optional[str] = None,
        states: Optional[List[str]] = None,
        container_ids: Optional[List[str]] = None,
    ) -> List[Container]:
        """Resolve a set of containers from batch filters.

        At least one filter must be provided.  Filters are AND-ed:
        a container must match all specified criteria.

        Args:
            labels: Required label key-value pairs.
            name_pattern: Substring match on container name.
            states: Restrict to these lifecycle states.
            container_ids: Restrict to these explicit IDs.

        Returns:
            List of matching containers.
        """
        candidates: List[Container] = []
        if container_ids:
            for cid in container_ids:
                c = self.containers.get(cid)
                if c is not None:
                    candidates.append(c)
        else:
            candidates = list(self.containers.values())

        if not candidates:
            return []

        result: List[Container] = []
        for c in candidates:
            # Label filter
            if labels and not all(
                c.config.labels.get(k) == v for k, v in labels.items()
            ):
                continue
            # Name pattern filter
            if name_pattern and name_pattern not in c.config.name:
                continue
            # State filter
            if states and c.state.value not in states:
                continue
            result.append(c)
        return result

    def batch_start(
        self,
        labels: Optional[Dict[str, str]] = None,
        name_pattern: Optional[str] = None,
        states: Optional[List[str]] = None,
        container_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Start multiple containers matching the given filters.

        Containers that are already running are skipped.

        Returns:
            Dict with ``started``, ``skipped``, and ``failed`` lists.
        """
        targets = self._resolve_batch_targets(
            labels=labels, name_pattern=name_pattern,
            states=states, container_ids=container_ids,
        )
        started, skipped, failed = [], [], []
        for c in targets:
            if c.state.value == "running":
                skipped.append(c.id)
                continue
            try:
                self.start(c)
                started.append(c.id)
            except Exception as e:
                failed.append({"id": c.id, "error": str(e)})
        return {
            "started": started,
            "skipped": skipped,
            "failed": failed,
            "total_matched": len(targets),
        }

    def batch_stop(
        self,
        labels: Optional[Dict[str, str]] = None,
        name_pattern: Optional[str] = None,
        states: Optional[List[str]] = None,
        container_ids: Optional[List[str]] = None,
        timeout_s: float = 10.0,
    ) -> Dict[str, Any]:
        """Stop (terminate gracefully) multiple containers.

        Containers that are already terminated are skipped.

        Returns:
            Dict with ``stopped``, ``skipped``, and ``failed`` lists.
        """
        targets = self._resolve_batch_targets(
            labels=labels, name_pattern=name_pattern,
            states=states, container_ids=container_ids,
        )
        stopped, skipped, failed = [], [], []
        for c in targets:
            if c.state.value == "terminated":
                skipped.append(c.id)
                continue
            try:
                self.terminate(c, timeout_s=timeout_s)
                stopped.append(c.id)
            except Exception as e:
                failed.append({"id": c.id, "error": str(e)})
        return {
            "stopped": stopped,
            "skipped": skipped,
            "failed": failed,
            "total_matched": len(targets),
        }

    def batch_kill(
        self,
        labels: Optional[Dict[str, str]] = None,
        name_pattern: Optional[str] = None,
        states: Optional[List[str]] = None,
        container_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Force-kill multiple containers (SIGKILL immediately).

        Containers that are already terminated are skipped.

        Returns:
            Dict with ``killed``, ``skipped``, and ``failed`` lists.
        """
        targets = self._resolve_batch_targets(
            labels=labels, name_pattern=name_pattern,
            states=states, container_ids=container_ids,
        )
        killed, skipped, failed = [], [], []
        for c in targets:
            if c.state.value == "terminated":
                skipped.append(c.id)
                continue
            try:
                if c.pid is not None:
                    try:
                        os.kill(c.pid, signal.SIGKILL)
                    except OSError:
                        pass
                self.terminate(c, timeout_s=0)
                killed.append(c.id)
            except Exception as e:
                failed.append({"id": c.id, "error": str(e)})
        return {
            "killed": killed,
            "skipped": skipped,
            "failed": failed,
            "total_matched": len(targets),
        }

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

    def add_network_rule(
        self, container: Container,
        direction: str,  # "ingress" or "egress"
        protocol: str = "tcp",
        port: Optional[int] = None,
        source: Optional[str] = None,
        action: str = "allow",
    ) -> Dict[str, Any]:
        """Add a network policy rule to a container.

        Rules are stored in the container's config and applied via
        iptables when the container's network is set up.

        Args:
            container: Target container.
            direction: "ingress" or "egress".
            protocol: "tcp", "udp", or "icmp".
            port: Port number (None = any).
            source: Source CIDR or IP (None = any).
            action: "allow" or "deny".

        Returns:
            Dict with ``ok``, ``rule_index``, ``rules_count``.
        """
        if direction not in ("ingress", "egress"):
            return {
                "ok": False,
                "error": "direction must be 'ingress' or 'egress'",
            }
        if action not in ("allow", "deny"):
            return {
                "ok": False,
                "error": "action must be 'allow' or 'deny'",
            }

        if not hasattr(container.config, "network_rules") or \
                container.config.network_rules is None:
            container.config.network_rules = []

        rule = {
            "direction": direction,
            "protocol": protocol,
            "port": port,
            "source": source,
            "action": action,
        }
        container.config.network_rules.append(rule)
        rule_index = len(container.config.network_rules) - 1

        self._record_event(
            "network_rule_added", container.id,
            f"idx={rule_index} {direction} {protocol} "
            f"port={port} src={source} {action}")

        return {
            "ok": True,
            "rule_index": rule_index,
            "rules_count": len(container.config.network_rules),
        }

    def remove_network_rule(
        self, container: Container, rule_index: int,
    ) -> Dict[str, Any]:
        """Remove a network policy rule by index.

        Args:
            container: Target container.
            rule_index: Index of the rule to remove.

        Returns:
            Dict with ``ok``, ``removed`` (the rule dict).
        """
        rules = getattr(container.config, "network_rules", None) or []
        if rule_index < 0 or rule_index >= len(rules):
            return {
                "ok": False,
                "error": f"invalid rule index {rule_index}",
            }
        removed = rules.pop(rule_index)
        self._record_event(
            "network_rule_removed", container.id,
            f"idx={rule_index}")
        return {
            "ok": True,
            "removed": removed,
        }

    def list_network_rules(
        self, container: Container,
    ) -> Dict[str, Any]:
        """List all network policy rules for a container.

        Returns:
            Dict with ``container_id``, ``rules`` list.
        """
        rules = getattr(container.config, "network_rules", None) or []
        return {
            "container_id": container.id,
            "rules": list(rules),
        }

    def clear_network_rules(self, container: Container) -> Dict[str, Any]:
        """Remove all network policy rules for a container.

        Returns:
            Dict with ``container_id``, ``cleared`` count.
        """
        rules = getattr(container.config, "network_rules", None) or []
        count = len(rules)
        container.config.network_rules = []
        self._record_event(
            "network_rules_cleared", container.id,
            f"count={count}")
        return {
            "container_id": container.id,
            "cleared": count,
        }

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
    # Resource usage reports
    # ------------------------------------------------------------------

    def generate_usage_report(
        self,
        container_ids: Optional[List[str]] = None,
        include_trends: bool = True,
    ) -> Dict[str, Any]:
        """Generate a resource usage report across containers.

        Aggregates current resource usage, identifies top consumers,
        and optionally includes trend analysis (comparing current
        usage to recent history).

        Args:
            container_ids: IDs to include (default: all containers).
            include_trends: Whether to include trend analysis.

        Returns:
            Dict with ``timestamp``, ``containers`` list, ``totals``,
            ``top_consumers``, and ``trends``.
        """
        if container_ids is None:
            container_ids = list(self.containers.keys())

        containers_data: List[Dict[str, Any]] = []
        total_memory = 0
        total_pids = 0
        total_cpu_ns = 0
        by_state: Dict[str, int] = {}

        for cid in container_ids:
            c = self.containers.get(cid)
            if c is None:
                continue
            state = c.state.value
            by_state[state] = by_state.get(state, 0) + 1
            stats = self.container_stats(c)
            mem = stats.get("memory_bytes", 0)
            pids = stats.get("pids_current", 0)
            cpu = stats.get("cpu_usage_usec", 0) * 1000  # to ns
            total_memory += mem
            total_pids += pids
            total_cpu_ns += cpu

            entry: Dict[str, Any] = {
                "id": cid,
                "state": state,
                "name": c.config.name,
                "memory_bytes": mem,
                "pids_current": pids,
                "cpu_usage_usec": stats.get("cpu_usage_usec", 0),
                "labels": self.list_labels(c),
            }

            if include_trends:
                history = self.resource_usage_history(
                    c, tail=20)
                if len(history) >= 2:
                    first = history[0]
                    last = history[-1]
                    entry["trend"] = {
                        "memory_delta": (
                            last.get("memory_bytes", 0)
                            - first.get("memory_bytes", 0)),
                        "pids_delta": (
                            last.get("pids_current", 0)
                            - first.get("pids_current", 0)),
                    }

            containers_data.append(entry)

        # Top consumers by memory
        top_memory = sorted(
            containers_data,
            key=lambda x: x.get("memory_bytes", 0),
            reverse=True)[:5]

        # Top consumers by CPU
        top_cpu = sorted(
            containers_data,
            key=lambda x: x.get("cpu_usage_usec", 0),
            reverse=True)[:5]

        return {
            "timestamp": time.time(),
            "container_count": len(containers_data),
            "totals": {
                "memory_bytes": total_memory,
                "pids": total_pids,
                "cpu_ns": total_cpu_ns,
            },
            "by_state": by_state,
            "containers": containers_data,
            "top_consumers": {
                "by_memory": [
                    {"id": c["id"], "name": c.get("name"),
                     "memory_bytes": c["memory_bytes"]}
                    for c in top_memory
                ],
                "by_cpu": [
                    {"id": c["id"], "name": c.get("name"),
                     "cpu_usage_usec": c["cpu_usage_usec"]}
                    for c in top_cpu
                ],
            },
        }

    def generate_alert_summary(self) -> Dict[str, Any]:
        """Generate a summary of all active alerts across containers.

        Aggregates alert histories from all containers into a
        single summary.

        Returns:
            Dict with ``timestamp``, ``total_alerts``, ``by_severity``,
            and ``alerts`` list.
        """
        all_alerts: List[Dict[str, Any]] = []
        by_severity: Dict[str, int] = {}

        for cid, c in self.containers.items():
            history = getattr(c, "_alert_history", [])
            for alert in history:
                if not alert.get("acknowledged", False):
                    entry = dict(alert)
                    entry["container_id"] = cid
                    all_alerts.append(entry)
                    sev = entry.get("severity", "unknown")
                    by_severity[sev] = by_severity.get(sev, 0) + 1

        # Sort by timestamp descending (newest first)
        all_alerts.sort(
            key=lambda a: a.get("timestamp", 0), reverse=True)

        return {
            "timestamp": time.time(),
            "total_alerts": len(all_alerts),
            "by_severity": by_severity,
            "alerts": all_alerts[:50],  # cap at 50
        }

    def compare_containers_detailed(
        self,
        container_ids: List[str],
        metrics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Compare resource usage across multiple containers.

        Generates a side-by-side comparison with rankings and
        percentages of total.

        Args:
            container_ids: IDs to compare (minimum 2).
            metrics: Metrics to compare (default: memory, pids, cpu).

        Returns:
            Dict with ``comparison`` list, ``rankings`` by metric,
            and ``percentages``.
        """
        if len(container_ids) < 2:
            return {
                "error": "need at least 2 containers to compare",
                "comparison": [],
            }

        if metrics is None:
            metrics = ["memory_bytes", "pids_current", "cpu_usage_usec"]

        containers_data: List[Dict[str, Any]] = []
        totals: Dict[str, float] = {m: 0 for m in metrics}

        for cid in container_ids:
            c = self.containers.get(cid)
            if c is None:
                continue
            stats = self.container_stats(c)
            entry: Dict[str, Any] = {
                "id": cid,
                "name": c.config.name,
                "state": c.state.value,
            }
            for m in metrics:
                val = stats.get(m, 0)
                entry[m] = val
                totals[m] += val
            containers_data.append(entry)

        # Compute percentages
        for entry in containers_data:
            for m in metrics:
                total = totals.get(m, 0)
                entry[f"{m}_pct"] = (
                    round(entry[m] / total * 100, 1)
                    if total > 0 else 0)

        # Rankings per metric
        rankings: Dict[str, List[Dict[str, Any]]] = {}
        for m in metrics:
            ranked = sorted(
                containers_data,
                key=lambda x: x.get(m, 0),
                reverse=True)
            rankings[m] = [
                {
                    "rank": i + 1,
                    "id": r["id"],
                    "name": r.get("name"),
                    "value": r.get(m, 0),
                    "percentage": r.get(f"{m}_pct", 0),
                }
                for i, r in enumerate(ranked)
            ]

        return {
            "container_count": len(containers_data),
            "comparison": containers_data,
            "totals": totals,
            "rankings": rankings,
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

    # ------------------------------------------------------------------
    # SLA (service level agreements)
    # ------------------------------------------------------------------

    def start_sla_tracking(self, container: Container) -> None:
        """Start SLA tracking for a container.

        Called when a container transitions to RUNNING. Records the
        start time for uptime calculations.
        """
        container._sla_started_at = time.time()
        container._sla_downtime_s = 0.0
        container._sla_violations.clear()
        logger.debug("start_sla_tracking: %s", container.id)

    def record_sla_downtime(
        self, container: Container, duration_s: float,
        reason: str = "",
    ) -> None:
        """Record downtime for SLA calculation.

        Args:
            container: Target container.
            duration_s: Downtime duration in seconds.
            reason: Reason for downtime (e.g., "crash", "oom").
        """
        container._sla_downtime_s += duration_s
        if container.config.sla_alert_on_breach:
            self._fire_alert(
                container, "sla_downtime", "warning",
                f"{duration_s:.1f}s downtime: {reason}",
            )

    def check_sla(self, container: Container) -> Dict[str, Any]:
        """Check SLA compliance for a container.

        Compares actual uptime against the configured target and
        reports violations.

        Returns:
            Dict with ``uptime_pct``, ``target``, ``breached``,
            ``downtime_s``, ``total_time_s``, ``violations``.
        """
        cfg = container.config
        started_at = container._sla_started_at
        downtime = container._sla_downtime_s

        if started_at is None:
            return {
                "container_id": container.id,
                "uptime_pct": None,
                "target": cfg.sla_uptime_target,
                "breached": False,
                "downtime_s": 0,
                "total_time_s": 0,
                "violations": [],
                "tracked": False,
            }

        total_time = time.time() - started_at
        if total_time <= 0:
            uptime_pct = 100.0
        else:
            uptime_pct = round(
                (1 - downtime / total_time) * 100, 4
            )

        breached = uptime_pct < cfg.sla_uptime_target
        violations = list(container._sla_violations)

        # Check restart count violation
        if container.restart_count > cfg.sla_max_restart_count:
            violation = {
                "timestamp": time.time(),
                "type": "restart_count",
                "detail": (
                    f"{container.restart_count} restarts "
                    f"> {cfg.sla_max_restart_count} limit"
                ),
            }
            if violation not in violations:
                container._sla_violations.append(violation)
                violations.append(violation)
                breached = True

        return {
            "container_id": container.id,
            "uptime_pct": uptime_pct,
            "target": cfg.sla_uptime_target,
            "breached": breached,
            "downtime_s": round(downtime, 3),
            "total_time_s": round(total_time, 3),
            "violations": violations,
            "tracked": True,
            "restart_count": container.restart_count,
            "max_restarts": cfg.sla_max_restart_count,
        }

    def get_sla_violations(
        self, container: Container, tail: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get SLA violations for a container.

        Args:
            container: Target container.
            tail: If set, return only the last N violations.

        Returns:
            List of violation dicts.
        """
        violations = container._sla_violations
        if tail is not None:
            return list(violations[-tail:])
        return list(violations)

    def set_sla_config(
        self, container: Container,
        uptime_target: Optional[float] = None,
        max_restart_count: Optional[int] = None,
        alert_on_breach: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Update SLA configuration for a container.

        Args:
            container: Target container.
            uptime_target: Uptime percentage target (0-100).
            max_restart_count: Max restarts before SLA breach.
            alert_on_breach: Whether to fire alert on breach.

        Returns:
            Dict with updated SLA config.
        """
        cfg = container.config
        if uptime_target is not None:
            cfg.sla_uptime_target = max(0.0, min(100.0, uptime_target))
        if max_restart_count is not None:
            cfg.sla_max_restart_count = max(0, max_restart_count)
        if alert_on_breach is not None:
            cfg.sla_alert_on_breach = alert_on_breach
        return {
            "container_id": container.id,
            "sla_uptime_target": cfg.sla_uptime_target,
            "sla_max_restart_count": cfg.sla_max_restart_count,
            "sla_alert_on_breach": cfg.sla_alert_on_breach,
        }

    # ------------------------------------------------------------------
    # SLA breach escalation
    # ------------------------------------------------------------------

    def set_sla_escalation_policy(
        self,
        container: Container,
        levels: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Set SLA breach escalation policy for a container.

        Escalation levels define actions taken after consecutive breaches.
        Each level has a ``threshold`` (number of consecutive breaches)
        and ``actions`` (list of action types).

        Args:
            container: Target container.
            levels: List of escalation level dicts, each with:
                - ``threshold``: Consecutive breaches to trigger.
                - ``actions``: List of action types
                  (alert, webhook, restart, notify, page).
                - ``cooldown_s``: Min seconds between escalations.

        Returns:
            Dict with the escalation policy.
        """
        if levels is None:
            levels = [
                {"threshold": 1, "actions": ["alert"], "cooldown_s": 0},
                {"threshold": 3, "actions": ["alert", "webhook"], "cooldown_s": 300},
                {"threshold": 5, "actions": ["alert", "webhook", "restart"], "cooldown_s": 600},
                {"threshold": 10, "actions": ["alert", "webhook", "restart", "page"], "cooldown_s": 1800},
            ]
        if not hasattr(container, '_sla_escalation_policy'):
            container._sla_escalation_policy = {}
        container._sla_escalation_policy = {
            "levels": levels,
            "consecutive_breaches": 0,
            "last_escalation_time": 0,
            "current_level": 0,
            "escalation_history": [],
        }
        logger.info(
            "set_sla_escalation_policy: %s (%d levels)",
            container.id, len(levels),
        )
        return {
            "container_id": container.id,
            "levels": levels,
            "configured": True,
        }

    def trigger_sla_escalation(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Process an SLA breach and escalate if needed.

        Increments the consecutive breach counter and triggers
        escalation actions if the threshold is met.

        Returns:
            Dict with ``escalated``, ``level``, ``actions``, ``consecutive_breaches``.
        """
        if not hasattr(container, '_sla_escalation_policy') or \
                not container._sla_escalation_policy:
            return {
                "container_id": container.id,
                "escalated": False,
                "level": 0,
                "actions": [],
                "consecutive_breaches": 0,
            }

        policy = container._sla_escalation_policy
        policy["consecutive_breaches"] += 1
        breaches = policy["consecutive_breaches"]
        now = time.time()
        levels = policy.get("levels", [])
        level_times = policy.setdefault("_level_times", {})

        # Find the highest level that matches
        triggered_level = None
        triggered_actions: List[str] = []
        for i, level in enumerate(levels):
            if breaches >= level.get("threshold", 999999):
                triggered_level = i
                triggered_actions = level.get("actions", [])
                cooldown = level.get("cooldown_s", 0)
                last_time = level_times.get(i, 0)
                if cooldown > 0 and (now - last_time) < cooldown:
                    # Still in cooldown for this level, don't re-escalate
                    triggered_actions = []

        if triggered_actions:
            level_times[triggered_level] = now
            policy["last_escalation_time"] = now
            policy["current_level"] = triggered_level
            # Record escalation
            entry = {
                "timestamp": now,
                "level": triggered_level,
                "consecutive_breaches": breaches,
                "actions": triggered_actions,
            }
            policy["escalation_history"].append(entry)
            # Keep last 100 entries
            if len(policy["escalation_history"]) > 100:
                policy["escalation_history"] = policy["escalation_history"][-100:]

            # Execute actions
            for action in triggered_actions:
                if action == "alert":
                    self._fire_alert(
                        container, "sla_escalation", "critical",
                        f"Level {triggered_level}: {breaches} consecutive breaches",
                    )
                elif action == "webhook":
                    # Fire webhook if registered
                    for wh_id, wh in self._webhooks.items():
                        if wh.get("events") is None or "sla_escalation" in wh.get("events", []):
                            self._pending_webhooks.append({
                                "webhook_id": wh_id,
                                "event": "sla_escalation",
                                "container_id": container.id,
                                "timestamp": now,
                                "level": triggered_level,
                                "breaches": breaches,
                            })
                elif action == "restart":
                    logger.warning(
                        "sla_escalation: auto-restart triggered for %s",
                        container.id,
                    )
                    # Mark for restart on next check
                    container._sla_restart_pending = True
                elif action == "page":
                    logger.critical(
                        "sla_escalation: PAGE triggered for %s",
                        container.id,
                    )

        return {
            "container_id": container.id,
            "escalated": len(triggered_actions) > 0,
            "level": triggered_level or 0,
            "actions": triggered_actions,
            "consecutive_breaches": breaches,
        }

    def reset_sla_escalation(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Reset the SLA escalation state (e.g., after recovery).

        Returns:
            Dict with ``reset``, ``previous_breaches``.
        """
        if not hasattr(container, '_sla_escalation_policy') or \
                not container._sla_escalation_policy:
            return {
                "container_id": container.id,
                "reset": False,
                "previous_breaches": 0,
            }
        policy = container._sla_escalation_policy
        prev = policy["consecutive_breaches"]
        policy["consecutive_breaches"] = 0
        policy["current_level"] = 0
        logger.info(
            "reset_sla_escalation: %s (was %d breaches)",
            container.id, prev,
        )
        return {
            "container_id": container.id,
            "reset": True,
            "previous_breaches": prev,
        }

    def get_sla_escalation_status(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get the current SLA escalation status.

        Returns:
            Dict with current level, consecutive breaches, history.
        """
        if not hasattr(container, '_sla_escalation_policy') or \
                not container._sla_escalation_policy:
            return {
                "container_id": container.id,
                "configured": False,
                "current_level": 0,
                "consecutive_breaches": 0,
                "levels": [],
                "escalation_history": [],
            }
        policy = container._sla_escalation_policy
        return {
            "container_id": container.id,
            "configured": True,
            "current_level": policy.get("current_level", 0),
            "consecutive_breaches": policy.get("consecutive_breaches", 0),
            "last_escalation_time": policy.get("last_escalation_time", 0),
            "levels": policy.get("levels", []),
            "escalation_history": policy.get("escalation_history", []),
        }

    def get_sla_escalation_history(
        self,
        container: Container,
        tail: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get escalation history for a container.

        Args:
            container: Target container.
            tail: If set, return only the last N entries.

        Returns:
            List of escalation entry dicts.
        """
        if not hasattr(container, '_sla_escalation_policy') or \
                not container._sla_escalation_policy:
            return []
        history = container._sla_escalation_policy.get("escalation_history", [])
        if tail is not None:
            return list(history[-tail:])
        return list(history)

    # ------------------------------------------------------------------
    # SLA breach auto-remediation integration
    # ------------------------------------------------------------------

    def process_sla_breach(
        self,
        container: Container,
        breach_type: str = "downtime",
        detail: str = "",
    ) -> Dict[str, Any]:
        """Process an SLA breach and trigger auto-remediation.

        This is the unified entry point that:
        1. Records the breach in SLA tracking
        2. Triggers SLA escalation
        3. If auto-remediation is configured, executes the
           corresponding remediation action

        Args:
            container: Target container.
            breach_type: Type of breach (downtime, latency,
                error_rate, custom).
            detail: Human-readable detail.

        Returns:
            Dict with breach, escalation, and remediation results.
        """
        # Record the breach (use 1s as minimal duration for the event)
        self.record_sla_downtime(
            container, duration_s=1.0, reason=f"{breach_type}: {detail}")

        # Trigger escalation
        escalation = self.trigger_sla_escalation(container)

        # Execute auto-remediation if configured
        remediation = None
        rem = getattr(container, '_remediation', {})
        policy = rem.get('policy', {})
        if policy.get('enabled', False):
            # Map breach type to remediation trigger
            trigger_map = {
                'downtime': 'threshold_exceeded',
                'latency': 'threshold_exceeded',
                'error_rate': 'threshold_exceeded',
                'budget': 'budget_exceeded',
                'oom': 'oom_risk',
            }
            trigger = trigger_map.get(breach_type, 'threshold_exceeded')
            remediation = self.execute_remediation(
                container, trigger=trigger,
                reason=f"SLA breach ({breach_type}): {detail}")

        self._record_event(
            'sla_breach_processed', container.id,
            f"type={breach_type}, escalated={escalation.get('escalated')}, "
            f"remediation={'yes' if remediation else 'no'}")

        return {
            'container_id': container.id,
            'breach_type': breach_type,
            'detail': detail,
            'escalation': escalation,
            'remediation': remediation,
        }

    def process_sla_breach_all(
        self,
        breach_type: str = "downtime",
        detail: str = "",
        container_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Process SLA breaches across multiple containers.

        Args:
            breach_type: Type of breach.
            detail: Human-readable detail.
            container_ids: Specific containers to breach.
                If None, breaches all running containers with SLA config.

        Returns:
            Dict with per-container results and summary.
        """
        results = []
        targets = []

        if container_ids:
            for cid in container_ids:
                c = self.containers.get(cid)
                if c is not None:
                    targets.append(c)
        else:
            for cid, c in self.containers.items():
                if c.state == ContainerState.RUNNING:
                    sla = getattr(c, '_sla', {})
                    if sla.get('enabled', False):
                        targets.append(c)

        for c in targets:
            result = self.process_sla_breach(
                c, breach_type=breach_type, detail=detail)
            results.append(result)

        escalated_count = sum(
            1 for r in results
            if r.get('escalation', {}).get('escalated', False))
        remediated_count = sum(
            1 for r in results
            if r.get('remediation') is not None)

        return {
            'breach_type': breach_type,
            'containers_processed': len(results),
            'escalated_count': escalated_count,
            'remediated_count': remediated_count,
            'results': results,
        }

    # ------------------------------------------------------------------
    # SLA compliance monitor (proactive enforcement)
    # ------------------------------------------------------------------

    def set_sla_compliance_rules(
        self,
        container: Container,
        max_memory_pct: float = 90.0,
        max_pid_pct: float = 80.0,
        max_daily_cost: Optional[float] = None,
        max_consecutive_anomalies: int = 5,
        auto_action: str = "alert",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        """Set SLA compliance rules for proactive monitoring.

        When enabled, the system proactively checks resource usage
        against these rules and triggers actions on violations.

        Args:
            container: Target container.
            max_memory_pct: Max memory usage percentage before violation.
            max_pid_pct: Max PID usage percentage before violation.
            max_daily_cost: Max daily cost in dollars (None = no check).
            max_consecutive_anomalies: Max anomalies before violation.
            auto_action: Action on violation (alert, remediate, escalate).
            enabled: Whether monitoring is active.

        Returns:
            Dict with the compliance rules.
        """
        rules = {
            'max_memory_pct': max_memory_pct,
            'max_pid_pct': max_pid_pct,
            'max_daily_cost': max_daily_cost,
            'max_consecutive_anomalies': max_consecutive_anomalies,
            'auto_action': auto_action,
            'enabled': enabled,
            'updated_at': time.time(),
            'violations': [],
            'last_check': 0.0,
        }
        container._sla_compliance_rules = rules

        self._record_event(
            'sla_compliance_configured', container.id,
            f"enabled={enabled}, action={auto_action}")

        return {
            'container_id': container.id,
            'rules': dict(rules),
        }

    def get_sla_compliance_rules(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get SLA compliance rules for a container."""
        rules = getattr(container, '_sla_compliance_rules', None)
        return {
            'container_id': container.id,
            'rules': dict(rules) if rules else {},
            'status': 'set' if rules else 'unset',
        }

    def check_sla_compliance(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Check SLA compliance and take action if needed.

        Returns:
            Dict with ``compliant``, ``violations``, ``action_taken``.
        """
        rules = getattr(container, '_sla_compliance_rules', None)
        if not rules or not rules.get('enabled', False):
            return {
                'container_id': container.id,
                'compliant': True,
                'violations': [],
                'action_taken': 'none',
                'reason': 'no_rules',
            }

        violations: List[Dict[str, Any]] = []
        now = time.time()

        # Check memory usage
        stats = self.container_stats(container)
        if stats and stats.get('available'):
            mem_bytes = stats.get('memory_bytes', 0)
            mem_limit = container.config.limits.memory_mb * 1024 * 1024
            if mem_limit > 0:
                mem_pct = (mem_bytes / mem_limit) * 100
                if mem_pct > rules.get('max_memory_pct', 90):
                    violations.append({
                        'rule': 'max_memory_pct',
                        'current': round(mem_pct, 1),
                        'threshold': rules['max_memory_pct'],
                        'resource': 'memory',
                    })

            # Check PID usage
            pids = stats.get('pids_current', 0)
            pid_limit = container.config.limits.pid_limit
            if pid_limit > 0:
                pid_pct = (pids / pid_limit) * 100
                if pid_pct > rules.get('max_pid_pct', 80):
                    violations.append({
                        'rule': 'max_pid_pct',
                        'current': round(pid_pct, 1),
                        'threshold': rules['max_pid_pct'],
                        'resource': 'pids',
                    })

        # Check anomaly count
        anomaly_result = self.detect_anomalies(container)
        anomaly_count = len(anomaly_result.get('anomalies', []))
        if anomaly_count > rules.get('max_consecutive_anomalies', 5):
            violations.append({
                'rule': 'max_consecutive_anomalies',
                'current': anomaly_count,
                'threshold': rules['max_consecutive_anomalies'],
                'resource': 'anomalies',
            })

        # Check daily cost
        max_daily = rules.get('max_daily_cost')
        if max_daily is not None:
            allocation = self.calculate_cost_allocation(container)
            daily_cost = allocation.get('projected_daily', 0)
            if daily_cost > max_daily:
                violations.append({
                    'rule': 'max_daily_cost',
                    'current': daily_cost,
                    'threshold': max_daily,
                    'resource': 'cost',
                })

        # Take action if there are violations
        action_taken = 'none'
        if violations:
            auto_action = rules.get('auto_action', 'alert')
            if auto_action == 'alert':
                self._fire_alert(
                    container, 'sla_compliance', 'warning',
                    f'{len(violations)} compliance violations')
                action_taken = 'alert'
            elif auto_action == 'remediate':
                result = self.execute_remediation(
                    container, trigger='threshold_exceeded',
                    reason=f'SLA compliance: {len(violations)} violations')
                action_taken = result.get('action_taken', 'none')
            elif auto_action == 'escalate':
                for _ in range(len(violations)):
                    self.trigger_sla_escalation(container)
                action_taken = f'escalated_x{len(violations)}'

            # Record violation
            rules.setdefault('violations', []).append({
                'timestamp': now,
                'violations': violations,
                'action': action_taken,
            })
            # Keep last 100 violations
            if len(rules['violations']) > 100:
                rules['violations'] = rules['violations'][-100:]

        rules['last_check'] = now

        return {
            'container_id': container.id,
            'compliant': len(violations) == 0,
            'violations': violations,
            'violation_count': len(violations),
            'action_taken': action_taken,
            'check_time': now,
        }

    def check_sla_compliance_all(
        self,
    ) -> Dict[str, Any]:
        """Check SLA compliance across all containers."""
        results = []
        for cid, c in self.containers.items():
            if c.state == ContainerState.RUNNING:
                result = self.check_sla_compliance(c)
                results.append(result)

        non_compliant = sum(
            1 for r in results if not r.get('compliant', True))
        total_violations = sum(
            r.get('violation_count', 0) for r in results)
        actions_taken = sum(
            1 for r in results if r.get('action_taken') != 'none')

        return {
            'container_count': len(results),
            'non_compliant_count': non_compliant,
            'total_violations': total_violations,
            'actions_taken': actions_taken,
            'containers': results,
        }

    # ------------------------------------------------------------------
    # Billing (cost tracking)
    # ------------------------------------------------------------------

    def set_billing_rates(
        self,
        memory_mb_per_hour: Optional[float] = None,
        cpu_per_hour: Optional[float] = None,
        pid_per_hour: Optional[float] = None,
        storage_mb_per_hour: Optional[float] = None,
    ) -> Dict[str, float]:
        """Update billing rates.

        Args:
            memory_mb_per_hour: Cost per GB-hour of memory.
            cpu_per_hour: Cost per vCPU-hour.
            pid_per_hour: Cost per PID-hour.
            storage_mb_per_hour: Cost per GB-hour of storage.

        Returns:
            Current billing rates.
        """
        if memory_mb_per_hour is not None:
            self._billing_rates["memory_mb_per_hour"] = memory_mb_per_hour
        if cpu_per_hour is not None:
            self._billing_rates["cpu_per_hour"] = cpu_per_hour
        if pid_per_hour is not None:
            self._billing_rates["pid_per_hour"] = pid_per_hour
        if storage_mb_per_hour is not None:
            self._billing_rates["storage_mb_per_hour"] = storage_mb_per_hour
        return dict(self._billing_rates)

    def get_billing_rates(self) -> Dict[str, float]:
        """Get current billing rates."""
        return dict(self._billing_rates)

    def record_billing_usage(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Record current resource usage for billing.

        Takes a snapshot of the container's resource usage and
        records it with the current billing rates.

        Returns:
            The billing record dict.
        """
        stats = self.container_stats(container)
        if not stats.get("available"):
            return {
                "container_id": container.id,
                "recorded": False,
            }

        # Calculate usage in units
        mem_bytes = stats.get("memory_bytes", 0)
        mem_gb_hours = mem_bytes / (1024 ** 3)  # Convert to GB
        cpu_usec = stats.get("cpu_usage_usec", 0)
        cpu_hours = cpu_usec / (3600 * 1_000_000)  # Convert to hours
        pids = stats.get("pids_current", 0)

        rates = self._billing_rates
        mem_cost = mem_gb_hours * rates["memory_mb_per_hour"]
        cpu_cost = cpu_hours * rates["cpu_per_hour"]
        pid_cost = pids * rates["pid_per_hour"]
        total_cost = mem_cost + cpu_cost + pid_cost

        record: Dict[str, Any] = {
            "timestamp": time.time(),
            "container_id": container.id,
            "memory_bytes": mem_bytes,
            "cpu_usage_usec": cpu_usec,
            "pids_current": pids,
            "memory_cost": round(mem_cost, 6),
            "cpu_cost": round(cpu_cost, 6),
            "pid_cost": round(pid_cost, 6),
            "total_cost": round(total_cost, 6),
            "rates": rates.copy(),
        }

        # Store record
        if container.id not in self._billing_records:
            self._billing_records[container.id] = []
        self._billing_records[container.id].append(record)
        # Keep at most 10000 records per container
        if len(self._billing_records[container.id]) > 10000:
            self._billing_records[container.id] = \
                self._billing_records[container.id][-10000:]

        return record

    def get_billing_records(
        self, container: Container,
        tail: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get billing records for a container.

        Args:
            container: Target container.
            tail: If set, return only the last N records.

        Returns:
            List of billing record dicts.
        """
        records = self._billing_records.get(container.id, [])
        if tail is not None:
            return list(records[-tail:])
        return list(records)

    def get_billing_summary(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Get billing summary for a container.

        Aggregates all billing records into a summary.

        Returns:
            Dict with ``total_cost``, ``record_count``,
            ``avg_memory_cost``, ``avg_cpu_cost``, ``avg_pid_cost``.
        """
        records = self._billing_records.get(container.id, [])
        if not records:
            return {
                "container_id": container.id,
                "total_cost": 0.0,
                "record_count": 0,
                "avg_memory_cost": 0.0,
                "avg_cpu_cost": 0.0,
                "avg_pid_cost": 0.0,
            }

        total_mem = sum(r.get("memory_cost", 0) for r in records)
        total_cpu = sum(r.get("cpu_cost", 0) for r in records)
        total_pid = sum(r.get("pid_cost", 0) for r in records)
        total = sum(r.get("total_cost", 0) for r in records)
        count = len(records)

        return {
            "container_id": container.id,
            "total_cost": round(total, 6),
            "record_count": count,
            "avg_memory_cost": round(total_mem / count, 6),
            "avg_cpu_cost": round(total_cpu / count, 6),
            "avg_pid_cost": round(total_pid / count, 6),
        }

    def get_billing_summary_all(self) -> Dict[str, Any]:
        """Get billing summary for all containers.

        Returns:
            Dict with per-container summaries and grand total.
        """
        grand_total = 0.0
        containers: List[Dict[str, Any]] = []

        for cid in self._billing_records:
            c = self.containers.get(cid)
            if c is None:
                continue
            summary = self.get_billing_summary(c)
            grand_total += summary["total_cost"]
            containers.append(summary)

        return {
            "grand_total_cost": round(grand_total, 6),
            "container_count": len(containers),
            "containers": containers,
        }

    # ------------------------------------------------------------------
    # Cost alerts and budget limits
    # ------------------------------------------------------------------

    def configure_cost_budget(
        self,
        container: Container,
        daily_limit: Optional[float] = None,
        weekly_limit: Optional[float] = None,
        monthly_limit: Optional[float] = None,
        alert_threshold_pct: float = 80.0,
        hard_limit: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Configure cost budget limits for a container.

        Args:
            container: Target container.
            daily_limit: Max daily cost in dollars.
            weekly_limit: Max weekly cost in dollars.
            monthly_limit: Max monthly cost in dollars.
            alert_threshold_pct: Alert when usage reaches this % of limit.
            hard_limit: Hard cost limit (pause container when exceeded).

        Returns:
            Dict with the budget configuration.
        """
        if not hasattr(container, '_cost_budget'):
            container._cost_budget = {}

        budget = container._cost_budget
        if daily_limit is not None:
            budget["daily_limit"] = daily_limit
        if weekly_limit is not None:
            budget["weekly_limit"] = weekly_limit
        if monthly_limit is not None:
            budget["monthly_limit"] = monthly_limit
        if hard_limit is not None:
            budget["hard_limit"] = hard_limit
        budget["alert_threshold_pct"] = alert_threshold_pct
        budget.setdefault("daily_limit", 0)
        budget.setdefault("weekly_limit", 0)
        budget.setdefault("monthly_limit", 0)
        budget.setdefault("hard_limit", 0)
        budget.setdefault("cost_alerts", [])
        budget.setdefault("last_check_time", 0)

        logger.info(
            "configure_cost_budget: %s daily=$%.2f monthly=$%.2f",
            container.id,
            budget.get("daily_limit", 0),
            budget.get("monthly_limit", 0),
        )
        return {
            "container_id": container.id,
            "budget": dict(budget),
        }

    def check_cost_budget(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Check if container is approaching or exceeding cost limits.

        Analyzes recent billing records and compares against
        configured budget limits.

        Returns:
            Dict with ``alerts``, ``usage``, ``limits``, ``within_budget``.
        """
        if not hasattr(container, '_cost_budget') or \
                not container._cost_budget:
            return {
                "container_id": container.id,
                "within_budget": True,
                "alerts": [],
                "usage": {},
                "limits": {},
            }

        budget = container._cost_budget
        now = time.time()
        records = self.get_billing_records(container)

        # Calculate usage periods
        day_ago = now - 86400
        week_ago = now - 604800
        month_ago = now - 2592000

        daily_cost = sum(
            r.get("total_cost", 0) for r in records
            if r.get("timestamp", 0) >= day_ago
        )
        weekly_cost = sum(
            r.get("total_cost", 0) for r in records
            if r.get("timestamp", 0) >= week_ago
        )
        monthly_cost = sum(
            r.get("total_cost", 0) for r in records
            if r.get("timestamp", 0) >= month_ago
        )

        usage = {
            "daily_cost": round(daily_cost, 6),
            "weekly_cost": round(weekly_cost, 6),
            "monthly_cost": round(monthly_cost, 6),
        }

        limits = {
            "daily_limit": budget.get("daily_limit", 0),
            "weekly_limit": budget.get("weekly_limit", 0),
            "monthly_limit": budget.get("monthly_limit", 0),
            "hard_limit": budget.get("hard_limit", 0),
            "alert_threshold_pct": budget.get("alert_threshold_pct", 80),
        }

        alerts = []
        threshold = budget.get("alert_threshold_pct", 80) / 100

        # Check each limit
        for period, cost, limit in [
            ("daily", daily_cost, budget.get("daily_limit", 0)),
            ("weekly", weekly_cost, budget.get("weekly_limit", 0)),
            ("monthly", monthly_cost, budget.get("monthly_limit", 0)),
        ]:
            if limit <= 0:
                continue
            pct = cost / limit
            if pct >= 1.0:
                alerts.append({
                    "period": period,
                    "severity": "critical",
                    "message": f"{period.title()} limit exceeded: ${cost:.6f} / ${limit:.2f}",
                    "cost": round(cost, 6),
                    "limit": limit,
                    "pct": round(pct * 100, 1),
                })
            elif pct >= threshold:
                alerts.append({
                    "period": period,
                    "severity": "warning",
                    "message": f"{period.title()} cost approaching limit: ${cost:.6f} / ${limit:.2f}",
                    "cost": round(cost, 6),
                    "limit": limit,
                    "pct": round(pct * 100, 1),
                })

        # Check hard limit
        hard_limit = budget.get("hard_limit", 0)
        if hard_limit > 0 and monthly_cost >= hard_limit:
            alerts.append({
                "period": "hard",
                "severity": "critical",
                "message": f"Hard limit exceeded: ${monthly_cost:.6f} / ${hard_limit:.2f}",
                "cost": round(monthly_cost, 6),
                "limit": hard_limit,
                "pct": round(monthly_cost / hard_limit * 100, 1),
            })

        within_budget = not any(
            a["severity"] == "critical" for a in alerts
        )

        # Store alerts
        if alerts:
            budget["cost_alerts"].extend(alerts)
            if len(budget["cost_alerts"]) > 200:
                budget["cost_alerts"] = budget["cost_alerts"][-200:]
            # Fire resource alerts
            for a in alerts:
                if a["severity"] == "critical":
                    self._fire_alert(
                        container, "cost", "critical",
                        a["message"],
                    )
                elif a["severity"] == "warning":
                    self._fire_alert(
                        container, "cost", "warning",
                        a["message"],
                    )

        budget["last_check_time"] = now

        return {
            "container_id": container.id,
            "within_budget": within_budget,
            "alerts": alerts,
            "usage": usage,
            "limits": limits,
        }

    def get_cost_alerts(
        self,
        container: Container,
        tail: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get cost alert history for a container."""
        if not hasattr(container, '_cost_budget') or \
                not container._cost_budget:
            return []
        alerts = container._cost_budget.get("cost_alerts", [])
        if tail is not None:
            return list(alerts[-tail:])
        return list(alerts)

    def get_cost_budget_config(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get the full cost budget configuration."""
        if not hasattr(container, '_cost_budget') or \
                not container._cost_budget:
            return {
                "container_id": container.id,
                "configured": False,
            }
        budget = container._cost_budget
        return {
            "container_id": container.id,
            "configured": True,
            "daily_limit": budget.get("daily_limit", 0),
            "weekly_limit": budget.get("weekly_limit", 0),
            "monthly_limit": budget.get("monthly_limit", 0),
            "hard_limit": budget.get("hard_limit", 0),
            "alert_threshold_pct": budget.get("alert_threshold_pct", 80),
        }

    # ------------------------------------------------------------------
    # Cost allocation per container
    # ------------------------------------------------------------------

    def calculate_cost_allocation(
        self,
        container: Container,
        rates: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Calculate the resource cost for a container.

        Computes the cost based on resource usage and configurable
        hourly rates (default: $0.01/GB-hour memory, $0.05/CPU-hour,
        $0.001/PID-hour).

        Args:
            container: Target container.
            rates: Custom rates dict with keys ``memory_per_gb_hour``,
                ``cpu_per_hour``, ``pid_per_hour``.

        Returns:
            Dict with ``container_id``, ``memory_cost``, ``cpu_cost``,
            ``pid_cost``, ``total_cost``, ``usage``, and ``rates``.
        """
        if rates is None:
            rates = {
                "memory_per_gb_hour": 0.01,
                "cpu_per_hour": 0.05,
                "pid_per_hour": 0.001,
            }

        stats = self.container_stats(container)
        if not stats.get("available"):
            return {
                "container_id": container.id,
                "memory_cost": 0.0,
                "cpu_cost": 0.0,
                "pid_cost": 0.0,
                "total_cost": 0.0,
                "usage": {},
                "rates": rates,
            }

        mem_bytes = stats.get("memory_bytes", 0)
        mem_gb = mem_bytes / (1024 ** 3)
        cpu_usec = stats.get("cpu_usage_usec", 0)
        cpu_hours = cpu_usec / (3600 * 1_000_000)
        pids = stats.get("pids_current", 0)

        mem_cost = mem_gb * rates.get("memory_per_gb_hour", 0.01)
        cpu_cost = cpu_hours * rates.get("cpu_per_hour", 0.05)
        pid_cost = pids * rates.get("pid_per_hour", 0.001)
        total_cost = mem_cost + cpu_cost + pid_cost

        # Get uptime for cost projection
        uptime_h = 0.0
        if container.started_at:
            uptime_h = (time.time() - container.started_at) / 3600

        return {
            "container_id": container.id,
            "memory_cost": round(mem_cost, 6),
            "cpu_cost": round(cpu_cost, 6),
            "pid_cost": round(pid_cost, 6),
            "total_cost": round(total_cost, 6),
            "projected_daily": round(total_cost * 24, 4),
            "projected_monthly": round(total_cost * 24 * 30, 4),
            "uptime_hours": round(uptime_h, 2),
            "usage": {
                "memory_gb": round(mem_gb, 4),
                "cpu_hours": round(cpu_hours, 6),
                "pids": pids,
            },
            "rates": rates,
        }

    def calculate_cost_allocation_all(
        self,
        rates: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Calculate cost allocation for all running containers.

        Returns:
            Dict with ``total_cost``, ``containers`` list, ``by_owner``
            aggregation, and ``rates``.
        """
        total_cost = 0.0
        by_owner: Dict[str, float] = {}
        containers_data: List[Dict[str, Any]] = []

        for cid, c in self.containers.items():
            if c.state != ContainerState.RUNNING:
                continue
            allocation = self.calculate_cost_allocation(c, rates)
            containers_data.append(allocation)
            cost = allocation["total_cost"]
            total_cost += cost
            owner = getattr(c.config, "owner", "default")
            by_owner[owner] = by_owner.get(owner, 0) + cost

        return {
            "timestamp": time.time(),
            "total_cost": round(total_cost, 6),
            "container_count": len(containers_data),
            "containers": containers_data,
            "by_owner": {
                k: round(v, 6) for k, v in by_owner.items()
            },
            "rates": rates or {
                "memory_per_gb_hour": 0.01,
                "cpu_per_hour": 0.05,
                "pid_per_hour": 0.001,
            },
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

    # ------------------------------------------------------------------
    # Resource budget tracking
    # ------------------------------------------------------------------

    def set_resource_budget(
        self,
        container: Container,
        memory_mb: Optional[int] = None,
        cpu_pct: Optional[float] = None,
        pids: Optional[int] = None,
        daily_cost_limit: Optional[float] = None,
        monthly_cost_limit: Optional[float] = None,
        alert_at_pct: float = 80.0,
    ) -> Dict[str, Any]:
        """Set a resource budget for a container.

        Budgets are advisory — the system monitors actual usage and
        emits alerts when thresholds are approached or exceeded.

        Args:
            container: Target container.
            memory_mb: Memory budget in MB.
            cpu_pct: CPU percentage budget (0-100).
            pids: PID count budget.
            daily_cost_limit: Maximum daily cost in dollars.
            monthly_cost_limit: Maximum monthly cost in dollars.
            alert_at_pct: Percentage at which to emit warning alerts.

        Returns:
            The budget dict.
        """
        if not hasattr(container, '_resource_budget'):
            container._resource_budget = {}

        budget = container._resource_budget
        if memory_mb is not None:
            budget['memory_mb'] = memory_mb
        if cpu_pct is not None:
            budget['cpu_pct'] = cpu_pct
        if pids is not None:
            budget['pids'] = pids
        if daily_cost_limit is not None:
            budget['daily_cost_limit'] = daily_cost_limit
        if monthly_cost_limit is not None:
            budget['monthly_cost_limit'] = monthly_cost_limit
        budget['alert_at_pct'] = alert_at_pct
        budget['created_at'] = budget.get('created_at', time.time())
        budget['updated_at'] = time.time()

        self._record_event(
            'budget_set', container.id,
            f"budget updated: {list(budget.keys())}")

        return {
            'container_id': container.id,
            'budget': dict(budget),
        }

    def get_resource_budget(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get the resource budget for a container.

        Returns:
            Dict with ``container_id``, ``budget``, and ``status``
            (whether the budget has been set).
        """
        budget = getattr(container, '_resource_budget', None)
        return {
            'container_id': container.id,
            'budget': dict(budget) if budget else {},
            'status': 'set' if budget else 'unset',
        }

    def check_resource_budgets(
        self,
    ) -> List[Dict[str, Any]]:
        """Check all container budgets against current usage.

        Returns:
            List of status dicts for containers with budgets.
        """
        results = []
        for cid, c in self.containers.items():
            budget = getattr(c, '_resource_budget', None)
            if not budget:
                continue
            status = self._check_single_budget(c, budget)
            results.append(status)
        return results

    def _check_single_budget(
        self,
        container: Container, budget: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Check a single container's budget against current usage."""
        stats = self.container_stats(container)
        violations = []
        warnings = []
        alert_pct = budget.get('alert_at_pct', 80.0)

        # Memory check
        mem_budget = budget.get('memory_mb')
        if mem_budget and stats.get('available'):
            mem_used_mb = stats.get('memory_bytes', 0) / (1024 * 1024)
            mem_pct = (mem_used_mb / mem_budget * 100) if mem_budget > 0 else 0
            entry = {
                'resource': 'memory',
                'budget': mem_budget,
                'used': round(mem_used_mb, 1),
                'unit': 'MB',
                'pct': round(mem_pct, 1),
            }
            if mem_pct >= 100:
                violations.append(entry)
            elif mem_pct >= alert_pct:
                warnings.append(entry)

        # PID check
        pid_budget = budget.get('pids')
        if pid_budget and stats.get('available'):
            pids_used = stats.get('pids_current', 0)
            pid_pct = (pids_used / pid_budget * 100) if pid_budget > 0 else 0
            entry = {
                'resource': 'pids',
                'budget': pid_budget,
                'used': pids_used,
                'unit': 'count',
                'pct': round(pid_pct, 1),
            }
            if pid_pct >= 100:
                violations.append(entry)
            elif pid_pct >= alert_pct:
                warnings.append(entry)

        # Cost check
        daily_limit = budget.get('daily_cost_limit')
        if daily_limit:
            allocation = self.calculate_cost_allocation(container)
            daily_cost = allocation.get('projected_daily', 0)
            cost_pct = (daily_cost / daily_limit * 100) if daily_limit > 0 else 0
            entry = {
                'resource': 'daily_cost',
                'budget': daily_limit,
                'used': daily_cost,
                'unit': '$/day',
                'pct': round(cost_pct, 1),
            }
            if cost_pct >= 100:
                violations.append(entry)
            elif cost_pct >= alert_pct:
                warnings.append(entry)

        monthly_limit = budget.get('monthly_cost_limit')
        if monthly_limit:
            allocation = self.calculate_cost_allocation(container)
            monthly_cost = allocation.get('projected_monthly', 0)
            cost_pct = (monthly_cost / monthly_limit * 100) if monthly_limit > 0 else 0
            entry = {
                'resource': 'monthly_cost',
                'budget': monthly_limit,
                'used': monthly_cost,
                'unit': '$/month',
                'pct': round(cost_pct, 1),
            }
            if cost_pct >= 100:
                violations.append(entry)
            elif cost_pct >= alert_pct:
                warnings.append(entry)

        status = 'ok'
        if violations:
            status = 'exceeded'
        elif warnings:
            status = 'warning'

        if violations or warnings:
            self._record_event(
                'budget_alert', container.id,
                f"budget {status}: "
                f"{len(violations)} violations, "
                f"{len(warnings)} warnings")

        return {
            'container_id': container.id,
            'name': container.config.name,
            'status': status,
            'violations': violations,
            'warnings': warnings,
            'budget': budget,
        }

    def clear_resource_budget(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Clear the resource budget for a container."""
        old = getattr(container, '_resource_budget', {})
        container._resource_budget = {}
        self._record_event(
            'budget_cleared', container.id,
            f"budget cleared (was: {list(old.keys())})")
        return {
            'container_id': container.id,
            'cleared': bool(old),
        }

    # ------------------------------------------------------------------
    # Auto-remediation engine
    # ------------------------------------------------------------------

    def configure_remediation(
        self,
        container: Container,
        on_budget_exceeded: str = "alert",
        on_threshold_exceeded: str = "alert",
        on_oom_risk: str = "alert",
        max_restarts: int = 3,
        cooldown_seconds: float = 300.0,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        """Configure auto-remediation policies for a container.

        Actions:
        - ``alert``: emit a webhook/event only (default)
        - ``restart``: stop + restart the container
        - ``scale_up``: increase memory limit by 25%%
        - ``scale_down``: decrease memory limit by 25%%
        - ``throttle``: lower CPU weight
        - ``migrate``: checkpoint + restore (placeholder)

        Args:
            container: Target container.
            on_budget_exceeded: Action when budget is exceeded.
            on_threshold_exceeded: Action when threshold fires.
            on_oom_risk: Action when OOM score is high.
            max_restarts: Maximum restarts in the cooldown window.
            cooldown_seconds: Cooldown between remediation actions.
            enabled: Whether remediation is active.

        Returns:
            The remediation policy dict.
        """
        valid_actions = {
            'alert', 'restart', 'scale_up', 'scale_down',
            'throttle', 'migrate', 'none',
        }
        for action in (on_budget_exceeded, on_threshold_exceeded, on_oom_risk):
            if action not in valid_actions:
                raise ValueError(
                    f"invalid action {action!r}, "
                    f"must be one of {sorted(valid_actions)}")

        if not hasattr(container, '_remediation'):
            container._remediation = {
                'history': [],
                'restart_count': 0,
                'last_action_at': 0.0,
            }

        policy = {
            'on_budget_exceeded': on_budget_exceeded,
            'on_threshold_exceeded': on_threshold_exceeded,
            'on_oom_risk': on_oom_risk,
            'max_restarts': max_restarts,
            'cooldown_seconds': cooldown_seconds,
            'enabled': enabled,
            'updated_at': time.time(),
        }
        container._remediation['policy'] = policy

        self._record_event(
            'remediation_configured', container.id,
            f"remediation {'enabled' if enabled else 'disabled'}: "
            f"budget={on_budget_exceeded}, "
            f"threshold={on_threshold_exceeded}, "
            f"oom={on_oom_risk}")

        return {
            'container_id': container.id,
            'policy': policy,
        }

    def execute_remediation(
        self,
        container: Container,
        trigger: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Execute the configured remediation action for a trigger.

        Args:
            container: Target container.
            trigger: One of ``budget_exceeded``, ``threshold_exceeded``,
                ``oom_risk``.
            reason: Human-readable reason.

        Returns:
            Dict with ``action_taken``, ``result``, ``cooldown_active``,
            and ``history``.
        """
        rem = getattr(container, '_remediation', {})
        policy = rem.get('policy', {})

        if not policy.get('enabled', False):
            return {
                'container_id': container.id,
                'action_taken': 'none',
                'result': 'remediation disabled',
                'cooldown_active': False,
                'history': rem.get('history', [])[-5:],
            }

        action_map = {
            'budget_exceeded': policy.get('on_budget_exceeded', 'alert'),
            'threshold_exceeded': policy.get('on_threshold_exceeded', 'alert'),
            'oom_risk': policy.get('on_oom_risk', 'alert'),
        }
        action = action_map.get(trigger, 'alert')

        if action == 'none':
            return {
                'container_id': container.id,
                'action_taken': 'none',
                'result': 'policy set to none',
                'cooldown_active': False,
                'history': rem.get('history', [])[-5:],
            }

        # Check cooldown
        cooldown = policy.get('cooldown_seconds', 300.0)
        last_at = rem.get('last_action_at', 0.0)
        now = time.time()
        cooldown_active = (now - last_at) < cooldown

        if cooldown_active:
            return {
                'container_id': container.id,
                'action_taken': 'skipped',
                'result': f'cooldown active ({cooldown - (now - last_at):.0f}s remaining)',
                'cooldown_active': True,
                'history': rem.get('history', [])[-5:],
            }

        # Check restart limit
        if action == 'restart':
            max_r = policy.get('max_restarts', 3)
            if rem.get('restart_count', 0) >= max_r:
                entry = {
                    'timestamp': now,
                    'trigger': trigger,
                    'action': 'restart_denied',
                    'reason': f'max restarts ({max_r}) reached',
                }
                rem.setdefault('history', []).append(entry)
                self._record_event(
                    'remediation_restart_denied', container.id,
                    f'max restarts reached: {max_r}')
                return {
                    'container_id': container.id,
                    'action_taken': 'restart_denied',
                    'result': f'max restarts ({max_r}) reached',
                    'cooldown_active': False,
                    'history': rem.get('history', [])[-5:],
                }

        # Execute the action
        result_detail = ''
        try:
            if action == 'restart':
                self.terminate(container)
                self.spawn(container)
                rem['restart_count'] = rem.get('restart_count', 0) + 1
                result_detail = 'container restarted'
            elif action == 'scale_up':
                limits = container.config.limits
                new_mem = int(limits.memory_mb * 1.25)
                limits.memory_mb = new_mem
                result_detail = f'memory scaled up to {new_mem} MB'
            elif action == 'scale_down':
                limits = container.config.limits
                new_mem = max(64, int(limits.memory_mb * 0.75))
                limits.memory_mb = new_mem
                result_detail = f'memory scaled down to {new_mem} MB'
            elif action == 'throttle':
                result_detail = 'CPU weight lowered (placeholder)'
            elif action == 'alert':
                result_detail = 'alert emitted'
            elif action == 'migrate':
                result_detail = 'migration requested (placeholder)'
            else:
                result_detail = f'unknown action: {action}'
        except Exception as e:  # noqa: BLE001
            result_detail = f'action failed: {e}'

        entry = {
            'timestamp': now,
            'trigger': trigger,
            'action': action,
            'reason': reason,
            'result': result_detail,
        }
        rem.setdefault('history', []).append(entry)
        rem['last_action_at'] = now

        self._record_event(
            'remediation_executed', container.id,
            f'{action} for {trigger}: {result_detail}')

        return {
            'container_id': container.id,
            'action_taken': action,
            'result': result_detail,
            'cooldown_active': False,
            'history': rem.get('history', [])[-5:],
        }

    def get_remediation_status(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get remediation policy and recent history for a container."""
        rem = getattr(container, '_remediation', {})
        policy = rem.get('policy', {})
        history = rem.get('history', [])
        return {
            'container_id': container.id,
            'enabled': policy.get('enabled', False),
            'policy': policy,
            'restart_count': rem.get('restart_count', 0),
            'last_action_at': rem.get('last_action_at', 0.0),
            'history': history[-20:],
            'history_total': len(history),
        }

    def get_remediation_history(
        self,
        container: Container,
        tail: Optional[int] = None,
        trigger: Optional[str] = None,
        action: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get filtered remediation history."""
        rem = getattr(container, '_remediation', {})
        history = rem.get('history', [])
        if trigger:
            history = [e for e in history if e.get('trigger') == trigger]
        if action:
            history = [e for e in history if e.get('action') == action]
        history = list(reversed(history))
        if tail is not None:
            history = history[:tail]
        return history

    # ------------------------------------------------------------------
    # Smart remediation (anomaly-aware severity scoring)
    # ------------------------------------------------------------------

    def evaluate_and_remediate(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Evaluate container health and auto-remediate based on severity.

        Combines anomaly detection, budget status, health score, and
        OOM risk into a severity score (0-100), then recommends or
        executes the appropriate remediation action.

        Severity mapping:
        - 0-20: critical (F grade) - immediate remediation
        - 21-40: high (D grade) - urgent remediation
        - 41-60: moderate (C grade) - warning + optional remediation
        - 61-80: low (B grade) - monitor only
        - 81-100: healthy (A grade) - no action

        Args:
            container: Target container.

        Returns:
            Dict with ``severity_score``, ``severity_level``,
            ``health_score``, ``anomaly_score``, ``budget_status``,
            ``recommendations``, and ``action_taken``.
        """
        # Gather signals
        health_result = self.calculate_health_score(container)
        health_score = health_result.get('score', 50.0)

        anomaly_result = self.detect_anomalies(
            container, resource='memory')
        anomaly_count = len(anomaly_result.get('anomalies', []))
        anomaly_score = min(anomaly_count * 10, 50.0)  # 0-50 penalty

        budget = getattr(container, '_resource_budget', None)
        budget_status = 'none'
        budget_penalty = 0.0
        if budget:
            budget_check = self._check_single_budget(container, budget)
            if budget_check.get('violations'):
                budget_status = 'exceeded'
                budget_penalty = 30.0
            elif budget_check.get('warnings'):
                budget_status = 'warning'
                budget_penalty = 15.0
            else:
                budget_status = 'ok'

        oom_score_val = getattr(container, 'oom_score_adj', 0)
        oom_penalty = 0.0
        if oom_score_val > 500:
            oom_penalty = 25.0
        elif oom_score_val > 200:
            oom_penalty = 10.0

        # Calculate severity (lower = worse)
        severity = health_score - anomaly_score - budget_penalty - oom_penalty
        severity = max(0.0, min(100.0, severity))

        # Map to severity level
        if severity <= 20:
            level = 'critical'
        elif severity <= 40:
            level = 'high'
        elif severity <= 60:
            level = 'moderate'
        elif severity <= 80:
            level = 'low'
        else:
            level = 'healthy'

        # Build recommendations
        recommendations = list(health_result.get('recommendations', []))
        if anomaly_count > 3:
            recommendations.append(
                f"{anomaly_count} anomalies detected: investigate resource usage")
        if budget_status == 'exceeded':
            recommendations.append("Budget exceeded: increase limits or reduce workload")
        elif budget_status == 'warning':
            recommendations.append("Budget warning: usage approaching limit")
        if oom_penalty > 0:
            recommendations.append("Elevated OOM risk: consider increasing memory")

        # Determine action based on severity and remediation config
        action_taken = 'none'
        rem = getattr(container, '_remediation', {})
        policy = rem.get('policy', {})

        if policy.get('enabled', False):
            if level == 'critical':
                # Immediately remediate critical issues
                trigger = 'threshold_exceeded'
                if budget_status == 'exceeded':
                    trigger = 'budget_exceeded'
                elif oom_penalty > 20:
                    trigger = 'oom_risk'
                result = self.execute_remediation(
                    container, trigger=trigger,
                    reason=f"smart: severity={severity:.1f}, level={level}")
                action_taken = result.get('action_taken', 'none')
                recommendations.append(
                    f"Auto-remediated: {action_taken}")
            elif level == 'high':
                recommendations.append(
                    "High severity: consider manual intervention")

        self._record_event(
            'smart_evaluation', container.id,
            f"severity={severity:.1f}, level={level}, "
            f"action={action_taken}")

        return {
            'container_id': container.id,
            'name': container.config.name,
            'severity_score': round(severity, 1),
            'severity_level': level,
            'health_score': round(health_score, 1),
            'anomaly_count': anomaly_count,
            'anomaly_penalty': round(anomaly_score, 1),
            'budget_status': budget_status,
            'budget_penalty': round(budget_penalty, 1),
            'oom_penalty': round(oom_penalty, 1),
            'recommendations': recommendations,
            'action_taken': action_taken,
            'timestamp': time.time(),
        }

    def evaluate_and_remediate_all(
        self,
    ) -> Dict[str, Any]:
        """Evaluate all running containers and remediate as needed.

        Returns:
            Dict with per-container results, fleet summary,
            and remediation counts.
        """
        results = []
        for cid, c in self.containers.items():
            if c.state == ContainerState.RUNNING:
                result = self.evaluate_and_remediate(c)
                results.append(result)

        critical = sum(
            1 for r in results if r['severity_level'] == 'critical')
        high = sum(
            1 for r in results if r['severity_level'] == 'high')
        remediated = sum(
            1 for r in results if r['action_taken'] != 'none')
        avg_severity = (
            sum(r['severity_score'] for r in results) / len(results)
            if results else 0
        )

        return {
            'container_count': len(results),
            'average_severity': round(avg_severity, 1),
            'critical_count': critical,
            'high_count': high,
            'remediated_count': remediated,
            'containers': sorted(
                results, key=lambda r: r['severity_score']),
        }

    # ------------------------------------------------------------------
    # Network traffic analysis
    # ------------------------------------------------------------------

    def get_network_traffic_analysis(
        self,
        container: Container,
        window_s: float = 300.0,
    ) -> Dict[str, Any]:
        """Analyze network traffic patterns for a container.

        Tracks bytes/packets in/out over time and computes
        bandwidth utilization and traffic patterns.

        Args:
            container: Target container.
            window_s: Analysis window in seconds.

        Returns:
            Dict with ``rx_bytes``, ``tx_bytes``, ``bandwidth``,
            ``packets``, ``errors``, ``patterns``.
        """
        if not hasattr(container, '_net_traffic'):
            container._net_traffic = []

        # Get current stats
        stats = self.container_stats(container)
        net_stats = self.container_network_stats(container)
        now = time.time()

        # Record sample
        sample = {
            "timestamp": now,
            "rx_bytes": 0,
            "tx_bytes": 0,
            "rx_packets": 0,
            "tx_packets": 0,
            "rx_errors": 0,
            "tx_errors": 0,
            "rx_drops": 0,
            "tx_drops": 0,
        }

        if net_stats:
            sample["rx_bytes"] = net_stats.get("rx_bytes", 0)
            sample["tx_bytes"] = net_stats.get("tx_bytes", 0)
            sample["rx_packets"] = net_stats.get("rx_packets", 0)
            sample["tx_packets"] = net_stats.get("tx_packets", 0)
            sample["rx_errors"] = net_stats.get("rx_errors", 0)
            sample["tx_errors"] = net_stats.get("tx_errors", 0)
            sample["rx_drops"] = net_stats.get("rx_drops", 0)
            sample["tx_drops"] = net_stats.get("tx_drops", 0)

        container._net_traffic.append(sample)
        # Keep last 1000 samples
        if len(container._net_traffic) > 1000:
            container._net_traffic = container._net_traffic[-1000:]

        # Analyze window
        cutoff = now - window_s
        window_samples = [
            s for s in container._net_traffic
            if s.get("timestamp", 0) >= cutoff
        ]

        if len(window_samples) < 2:
            return {
                "container_id": container.id,
                "insufficient_data": True,
                "current": sample,
            }

        # Calculate deltas
        first = window_samples[0]
        last = window_samples[-1]
        duration = last["timestamp"] - first["timestamp"]
        if duration <= 0:
            duration = 1

        rx_delta = last["rx_bytes"] - first["rx_bytes"]
        tx_delta = last["tx_bytes"] - first["tx_bytes"]
        rx_pkts = last["rx_packets"] - first["rx_packets"]
        tx_pkts = last["tx_packets"] - first["tx_packets"]
        rx_errs = last["rx_errors"] - first["rx_errors"]
        tx_errs = last["tx_errors"] - first["tx_errors"]
        rx_drops = last["rx_drops"] - first["rx_drops"]
        tx_drops = last["tx_drops"] - first["tx_drops"]

        # Bandwidth (bytes/sec)
        rx_bps = rx_delta / duration
        tx_bps = tx_delta / duration

        # Traffic patterns
        if len(window_samples) >= 5:
            # Detect burstiness (stddev of intervals)
            bytes_per_sample = []
            for i in range(1, len(window_samples)):
                dt = window_samples[i]["timestamp"] - window_samples[i-1]["timestamp"]
                db = (window_samples[i]["rx_bytes"] + window_samples[i]["tx_bytes"]) - \
                     (window_samples[i-1]["rx_bytes"] + window_samples[i-1]["tx_bytes"])
                if dt > 0:
                    bytes_per_sample.append(db / dt)

            if bytes_per_sample:
                avg_bps = sum(bytes_per_sample) / len(bytes_per_sample)
                variance = sum((b - avg_bps) ** 2 for b in bytes_per_sample) / len(bytes_per_sample)
                stddev = variance ** 0.5
                burstiness = stddev / avg_bps if avg_bps > 0 else 0
            else:
                burstiness = 0
                avg_bps = 0
        else:
            burstiness = 0
            avg_bps = 0

        # Error rate
        total_pkts = rx_pkts + tx_pkts
        error_rate = (rx_errs + tx_errs) / total_pkts * 100 if total_pkts > 0 else 0
        drop_rate = (rx_drops + tx_drops) / total_pkts * 100 if total_pkts > 0 else 0

        patterns = {
            "burstiness": round(burstiness, 3),
            "error_rate_pct": round(error_rate, 2),
            "drop_rate_pct": round(drop_rate, 2),
            "dominant_direction": "rx" if rx_bps > tx_bps else "tx",
            "symmetry_ratio": round(min(rx_bps, tx_bps) / max(rx_bps, tx_bps), 3) if max(rx_bps, tx_bps) > 0 else 0,
        }

        return {
            "container_id": container.id,
            "insufficient_data": False,
            "window_s": round(duration, 1),
            "sample_count": len(window_samples),
            "rx_bytes_total": last["rx_bytes"],
            "tx_bytes_total": last["tx_bytes"],
            "rx_bytes_delta": rx_delta,
            "tx_bytes_delta": tx_delta,
            "rx_bytes_per_sec": round(rx_bps, 2),
            "tx_bytes_per_sec": round(tx_bps, 2),
            "rx_packets_delta": rx_pkts,
            "tx_packets_delta": tx_pkts,
            "rx_errors_delta": rx_errs,
            "tx_errors_delta": tx_errs,
            "rx_drops_delta": rx_drops,
            "tx_drops_delta": tx_drops,
            "patterns": patterns,
            "current": sample,
        }

    def get_network_bandwidth_history(
        self,
        container: Container,
        tail: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get network traffic history for a container."""
        if not hasattr(container, '_net_traffic') or \
                not container._net_traffic:
            return []
        history = container._net_traffic
        if tail is not None:
            return list(history[-tail:])
        return list(history)

    def get_network_connections(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get active network connections for a container.

        Returns:
            Dict with ``connections`` list and ``summary``.
        """
        if not container.config.network or not container.network_ip:
            return {
                "container_id": container.id,
                "connections": [],
                "summary": {"total": 0},
            }

        connections = []
        try:
            # Try to get connection info from proc or ss
            result = subprocess.run(
                ["nsenter", "-t", str(container.pid), "-n",
                 "ss", "-tun"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n")[1:]:
                    parts = line.split()
                    if len(parts) >= 5:
                        connections.append({
                            "proto": parts[0],
                            "state": parts[1],
                            "recv_q": parts[2],
                            "send_q": parts[3],
                            "local": parts[4],
                            "peer": parts[5] if len(parts) > 5 else "",
                        })
        except Exception as e:
            logger.debug(
                "get_network_connections failed for %s: %s",
                container.id, e,
            )

        # Summary
        proto_counts: Dict[str, int] = {}
        state_counts: Dict[str, int] = {}
        for conn in connections:
            proto = conn.get("proto", "?")
            state = conn.get("state", "?")
            proto_counts[proto] = proto_counts.get(proto, 0) + 1
            state_counts[state] = state_counts.get(state, 0) + 1

        return {
            "container_id": container.id,
            "connections": connections,
            "summary": {
                "total": len(connections),
                "by_protocol": proto_counts,
                "by_state": state_counts,
            },
        }

    def get_network_traffic_by_protocol(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get traffic breakdown by protocol (TCP/UDP/ICMP)."""
        if not hasattr(container, '_net_proto_stats'):
            container._net_proto_stats = {
                "tcp": {"rx_bytes": 0, "tx_bytes": 0, "connections": 0},
                "udp": {"rx_bytes": 0, "tx_bytes": 0, "connections": 0},
                "icmp": {"rx_bytes": 0, "tx_bytes": 0, "connections": 0},
                "other": {"rx_bytes": 0, "tx_bytes": 0, "connections": 0},
            }
        return {
            "container_id": container.id,
            "protocols": dict(container._net_proto_stats),
        }

    # ------------------------------------------------------------------
    # Resource profiling (per-process breakdown)
    # ------------------------------------------------------------------

    def get_resource_profile(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get per-process resource usage breakdown for a container.

        Reads /proc entries for each process inside the container to
        report CPU time, memory (RSS), I/O bytes, and thread count
        per PID.  The result is a snapshot of the current state.

        Args:
            container: Target container.

        Returns:
            Dict with ``container_id``, ``processes`` (list of dicts
            with ``pid``, ``comm``, ``cpu_time_s``, ``rss_bytes``,
            ``io_read_bytes``, ``io_write_bytes``, ``threads``), and
            ``summary`` (aggregate totals).
        """
        if container.pid is None:
            return {
                "container_id": container.id,
                "processes": [],
                "summary": {
                    "total_cpu_s": 0.0,
                    "total_rss_bytes": 0,
                    "total_io_read_bytes": 0,
                    "total_io_write_bytes": 0,
                    "total_threads": 0,
                    "process_count": 0,
                },
            }

        processes = []
        total_cpu_s = 0.0
        total_rss = 0
        total_io_read = 0
        total_io_write = 0
        total_threads = 0

        try:
            # Find the init PID-1's children (the container's processes)
            children_path = (
                f"/proc/{container.pid}/task/{container.pid}/children"
            )
            child_pids: List[int] = []
            try:
                with open(children_path, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                    if raw:
                        child_pids = [
                            int(p) for p in raw.split() if p.isdigit()
                        ]
            except (OSError, ValueError):
                pass

            # Include the init itself
            pids_to_read = [container.pid] + child_pids

            for pid in pids_to_read:
                info: Dict[str, Any] = {
                    "pid": pid,
                    "comm": "",
                    "cpu_time_s": 0.0,
                    "rss_bytes": 0,
                    "io_read_bytes": 0,
                    "io_write_bytes": 0,
                    "threads": 1,
                }

                # comm (process name)
                try:
                    with open(
                        f"/proc/{pid}/comm", "r", encoding="utf-8"
                    ) as f:
                        info["comm"] = f.read().strip()[:16]
                except OSError:
                    pass

                # /proc/[pid]/stat — CPU time (utime + stime in ticks)
                try:
                    with open(
                        f"/proc/{pid}/stat", "r", encoding="utf-8"
                    ) as f:
                        stat_line = f.read()
                    # Fields after the comm (which may contain spaces
                    # and is wrapped in parens): split after the last ')'
                    rp = stat_line.rfind(")")
                    if rp != -1:
                        fields = stat_line[rp + 2:].split()
                        if len(fields) >= 14:
                            utime_ticks = int(fields[11])
                            stime_ticks = int(fields[12])
                            clk_tck = os.sysconf("SC_CLK_TCK")
                            if clk_tck > 0:
                                info["cpu_time_s"] = round(
                                    (utime_ticks + stime_ticks) / clk_tck,
                                    3,
                                )
                            info["threads"] = int(fields[17]) if len(fields) > 17 else 1
                except (OSError, ValueError, IndexError):
                    pass

                # /proc/[pid]/statm — RSS in pages
                try:
                    with open(
                        f"/proc/{pid}/statm", "r", encoding="utf-8"
                    ) as f:
                        mem_fields = f.read().split()
                    if len(mem_fields) >= 2:
                        page_size = os.sysconf("SC_PAGE_SIZE")
                        info["rss_bytes"] = int(mem_fields[1]) * page_size
                except (OSError, ValueError, IndexError):
                    pass

                # /proc/[pid]/io — bytes read/written
                try:
                    with open(
                        f"/proc/{pid}/io", "r", encoding="utf-8"
                    ) as f:
                        for line in f:
                            if line.startswith("read_bytes:"):
                                info["io_read_bytes"] = int(
                                    line.split(":", 1)[1].strip()
                                )
                            elif line.startswith("write_bytes:"):
                                info["io_write_bytes"] = int(
                                    line.split(":", 1)[1].strip()
                                )
                except (OSError, ValueError):
                    pass

                processes.append(info)
                total_cpu_s += info["cpu_time_s"]
                total_rss += info["rss_bytes"]
                total_io_read += info["io_read_bytes"]
                total_io_write += info["io_write_bytes"]
                total_threads += info["threads"]

        except Exception as e:
            logger.debug(
                "get_resource_profile failed for %s: %s",
                container.id, e,
            )

        # Sort by RSS descending (most memory-hungry first)
        processes.sort(key=lambda p: p["rss_bytes"], reverse=True)

        result = {
            "container_id": container.id,
            "processes": processes,
            "summary": {
                "total_cpu_s": round(total_cpu_s, 3),
                "total_rss_bytes": total_rss,
                "total_io_read_bytes": total_io_read,
                "total_io_write_bytes": total_io_write,
                "total_threads": total_threads,
                "process_count": len(processes),
            },
        }

        # Record a snapshot for history
        if not hasattr(container, "_resource_profile_history"):
            container._resource_profile_history = []
        container._resource_profile_history.append({
            "timestamp": time.time(),
            "summary": dict(result["summary"]),
        })
        # Keep last 1000 snapshots
        if len(container._resource_profile_history) > 1000:
            container._resource_profile_history = \
                container._resource_profile_history[-1000:]

        return result

    def get_resource_profile_history(
        self,
        container: Container,
        tail: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get resource profiling history for a container.

        Each entry contains a timestamp and the aggregate summary at
        that point (total CPU seconds, RSS, I/O bytes, threads).

        Args:
            container: Target container.
            tail: Return only the last *n* entries.

        Returns:
            List of profiling snapshots.
        """
        if not hasattr(container, "_resource_profile_history"):
            return []
        history = container._resource_profile_history
        if tail is not None:
            return list(history[-tail:])
        return list(history)

    def get_resource_profile_top_consumers(
        self,
        container: Container,
        resource: str = "rss_bytes",
        top_n: int = 5,
    ) -> Dict[str, Any]:
        """Get the top N processes by a specific resource.

        Args:
            container: Target container.
            resource: One of ``rss_bytes``, ``cpu_time_s``,
                ``io_read_bytes``, ``io_write_bytes``.
            top_n: Number of top consumers to return.

        Returns:
            Dict with ``container_id``, ``resource``, ``top``
            (list of process dicts), ``total``.
        """
        profile = self.get_resource_profile(container)
        processes = profile["processes"]
        valid = {
            "rss_bytes", "cpu_time_s",
            "io_read_bytes", "io_write_bytes",
        }
        if resource not in valid:
            resource = "rss_bytes"

        top = sorted(
            processes,
            key=lambda p: p.get(resource, 0),
            reverse=True,
        )[:top_n]

        total = sum(p.get(resource, 0) for p in processes)

        return {
            "container_id": container.id,
            "resource": resource,
            "top": top,
            "total": total,
        }

    # ------------------------------------------------------------------
    # Resource usage baselines (normal usage patterns)
    # ------------------------------------------------------------------

    def record_baseline(self, container: Container) -> Dict[str, Any]:
        """Record a resource usage baseline snapshot.

        Captures the current resource usage (from cgroup stats and
        resource profiling) and stores it as the baseline "normal"
        pattern for this container.  Multiple snapshots build a
        statistical profile over time.

        Returns:
            Dict with ``container_id``, ``snapshot_count``,
            ``baseline`` (the current stats).
        """
        if not hasattr(container, "_baselines"):
            container._baselines = []

        stats = self.container_stats(container)
        profile = self.get_resource_profile(container)

        snapshot = {
            "timestamp": time.time(),
            "memory_bytes": stats.get("memory_bytes", 0),
            "cpu_usage_usec": stats.get("cpu_usage_usec", 0),
            "pids_current": stats.get("pids_current", 0),
            "io_read_bytes": stats.get("io_read_bytes", 0),
            "total_cpu_s": profile["summary"].get("total_cpu_s", 0),
            "total_rss_bytes": profile["summary"].get(
                "total_rss_bytes", 0),
            "total_threads": profile["summary"].get(
                "total_threads", 0),
        }

        container._baselines.append(snapshot)
        # Keep last 100 snapshots
        if len(container._baselines) > 100:
            container._baselines = container._baselines[-100:]

        return {
            "container_id": container.id,
            "snapshot_count": len(container._baselines),
            "baseline": snapshot,
        }

    def get_baseline(self, container: Container) -> Dict[str, Any]:
        """Get the aggregated baseline for a container.

        Computes the mean and stddev of all recorded baseline
        snapshots, providing a statistical profile of "normal"
        resource usage.

        Returns:
            Dict with ``container_id``, ``snapshot_count``,
            ``mean`` (avg of each metric), ``stddev``.
        """
        baselines = getattr(container, "_baselines", [])
        if not baselines:
            return {
                "container_id": container.id,
                "snapshot_count": 0,
                "mean": {},
                "stddev": {},
            }

        metrics = [
            "memory_bytes", "cpu_usage_usec", "pids_current",
            "io_read_bytes", "total_cpu_s", "total_rss_bytes",
            "total_threads",
        ]

        mean: Dict[str, float] = {}
        stddev: Dict[str, float] = {}

        for m in metrics:
            values = [s.get(m, 0) for s in baselines]
            avg = sum(values) / len(values)
            mean[m] = round(avg, 3)
            if len(values) > 1:
                variance = sum((v - avg) ** 2 for v in values) / (
                    len(values) - 1)
                stddev[m] = round(variance ** 0.5, 3)
            else:
                stddev[m] = 0.0

        return {
            "container_id": container.id,
            "snapshot_count": len(baselines),
            "mean": mean,
            "stddev": stddev,
        }

    def compare_baseline(
        self, container: Container,
        threshold_sigma: float = 2.0,
    ) -> Dict[str, Any]:
        """Compare current resource usage against the baseline.

        Flags metrics that deviate more than ``threshold_sigma"
        standard deviations from the baseline mean.

        Args:
            container: Target container.
            threshold_sigma: How many standard deviations from the
                mean constitutes a deviation.

        Returns:
            Dict with ``container_id``, ``deviations`` (list of
            flagged metrics), ``current`` values, and ``baseline``
            (mean).
        """
        baseline = self.get_baseline(container)
        if baseline["snapshot_count"] < 2:
            return {
                "container_id": container.id,
                "deviations": [],
                "current": {},
                "baseline": baseline.get("mean", {}),
                "reason": "insufficient baseline data",
            }

        stats = self.container_stats(container)
        profile = self.get_resource_profile(container)

        current = {
            "memory_bytes": stats.get("memory_bytes", 0),
            "cpu_usage_usec": stats.get("cpu_usage_usec", 0),
            "pids_current": stats.get("pids_current", 0),
            "io_read_bytes": stats.get("io_read_bytes", 0),
            "total_cpu_s": profile["summary"].get("total_cpu_s", 0),
            "total_rss_bytes": profile["summary"].get(
                "total_rss_bytes", 0),
            "total_threads": profile["summary"].get(
                "total_threads", 0),
        }

        deviations: List[Dict[str, Any]] = []
        for m, val in current.items():
            mean = baseline["mean"].get(m, 0)
            sd = baseline["stddev"].get(m, 0)
            if sd > 0 and abs(val - mean) > threshold_sigma * sd:
                direction = "above" if val > mean else "below"
                z_score = round(
                    (val - mean) / sd, 2) if sd > 0 else 0
                deviations.append({
                    "metric": m,
                    "current": val,
                    "baseline_mean": round(mean, 3),
                    "baseline_stddev": round(sd, 3),
                    "z_score": z_score,
                    "direction": direction,
                })

        return {
            "container_id": container.id,
            "deviations": deviations,
            "current": current,
            "baseline": baseline["mean"],
        }

    def clear_baseline(self, container: Container) -> Dict[str, Any]:
        """Clear all baseline snapshots for a container.

        Returns:
            Dict with ``container_id`` and ``cleared`` (count).
        """
        baselines = getattr(container, "_baselines", [])
        count = len(baselines)
        container._baselines = []
        return {
            "container_id": container.id,
            "cleared": count,
        }

    # ------------------------------------------------------------------
    # Auto-scaling (demand-based resource adjustment)
    # ------------------------------------------------------------------

    def configure_auto_scaling(
        self,
        container: Container,
        enabled: bool = True,
        min_memory_mb: Optional[int] = None,
        max_memory_mb: Optional[int] = None,
        target_memory_pct: float = 70.0,
        min_cpu_quota: Optional[int] = None,
        max_cpu_quota: Optional[int] = None,
        target_cpu_pct: float = 70.0,
        scale_up_cooldown_s: float = 300.0,
        scale_down_cooldown_s: float = 600.0,
        evaluation_window_s: float = 300.0,
    ) -> Dict[str, Any]:
        """Configure auto-scaling for a container.

        Auto-scaling monitors resource usage and adjusts limits
        within configured bounds when usage crosses thresholds.

        Args:
            container: Target container.
            enabled: Whether auto-scaling is active.
            min_memory_mb: Minimum memory limit.
            max_memory_mb: Maximum memory limit.
            target_memory_pct: Target memory usage percentage.
            min_cpu_quota: Minimum CPU quota.
            max_cpu_quota: Maximum CPU quota.
            target_cpu_pct: Target CPU usage percentage.
            scale_up_cooldown_s: Cooldown after scaling up.
            scale_down_cooldown_s: Cooldown after scaling down.
            evaluation_window_s: Window for averaging usage.

        Returns:
            Dict with the auto-scaling configuration.
        """
        if not hasattr(container, '_autoscale'):
            container._autoscale = {}

        cfg = container.config.limits
        autoscale = container._autoscale
        autoscale.update({
            "enabled": enabled,
            "min_memory_mb": min_memory_mb or max(64, cfg.memory_mb // 4),
            "max_memory_mb": max_memory_mb or cfg.memory_mb * 4,
            "target_memory_pct": target_memory_pct,
            "min_cpu_quota": min_cpu_quota or max(100, (cfg.cpu_quota_us or 100000) // 4),
            "max_cpu_quota": max_cpu_quota or (cfg.cpu_quota_us or 100000) * 4,
            "target_cpu_pct": target_cpu_pct,
            "scale_up_cooldown_s": scale_up_cooldown_s,
            "scale_down_cooldown_s": scale_down_cooldown_s,
            "evaluation_window_s": evaluation_window_s,
            "last_scale_time": 0,
            "last_scale_direction": None,
            "scale_events": [],
            "current_memory_mb": cfg.memory_mb,
            "current_cpu_quota": cfg.cpu_quota_us or 100000,
        })
        logger.info(
            "configure_auto_scaling: %s enabled=%s target_mem=%s%%",
            container.id, enabled, target_memory_pct,
        )
        return {
            "container_id": container.id,
            "autoscale": dict(autoscale),
        }

    def evaluate_auto_scaling(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Evaluate whether auto-scaling should adjust resources.

        Analyzes recent resource usage and decides if scaling
        is needed. Returns the recommended action without applying it.

        Returns:
            Dict with ``should_scale``, ``direction``, ``reason``,
            ``current``, ``recommended``.
        """
        if not hasattr(container, '_autoscale') or \
                not container._autoscale.get("enabled"):
            return {
                "container_id": container.id,
                "should_scale": False,
                "direction": None,
                "reason": "auto-scaling not enabled",
            }

        autoscale = container._autoscale
        now = time.time()
        stats = self.container_stats(container)
        history = self.get_resource_history(container)

        # Calculate average usage over evaluation window
        window = autoscale["evaluation_window_s"]
        cutoff = now - window
        recent = [s for s in history if s.get("timestamp", 0) >= cutoff]

        if len(recent) < 2:
            return {
                "container_id": container.id,
                "should_scale": False,
                "direction": None,
                "reason": "insufficient data",
            }

        # Memory analysis
        mem_values = self._extract_resource_values(recent, "memory")
        avg_mem = sum(mem_values) / len(mem_values)
        mem_limit = container.config.limits.memory_mb * 1024 * 1024
        if mem_limit > 0:
            mem_pct = (avg_mem / mem_limit) * 100
        else:
            mem_pct = 0

        # CPU analysis (using throttle as proxy)
        cpu_throttle = stats.get("cpu_throttle_pct", 0)

        # Decide on scaling
        should_scale = False
        direction = None
        reason = ""
        scale_type = None

        # Check cooldown
        last_time = autoscale.get("last_scale_time", 0)
        last_dir = autoscale.get("last_scale_direction")

        if mem_pct > autoscale["target_memory_pct"] + 10:
            # Scale up memory
            cooldown = autoscale["scale_up_cooldown_s"]
            if now - last_time >= cooldown:
                should_scale = True
                direction = "up"
                reason = f"Memory at {mem_pct:.1f}% (target: {autoscale['target_memory_pct']}%)"
                scale_type = "memory"
        elif mem_pct < autoscale["target_memory_pct"] - 20 and mem_pct > 0:
            # Scale down memory
            cooldown = autoscale["scale_down_cooldown_s"]
            if now - last_time >= cooldown:
                should_scale = True
                direction = "down"
                reason = f"Memory at {mem_pct:.1f}% (target: {autoscale['target_memory_pct']}%)"
                scale_type = "memory"
        elif cpu_throttle > autoscale["target_cpu_pct"] + 10:
            # Scale up CPU
            cooldown = autoscale["scale_up_cooldown_s"]
            if now - last_time >= cooldown:
                should_scale = True
                direction = "up"
                reason = f"CPU throttled {cpu_throttle}% (target: {autoscale['target_cpu_pct']}%)"
                scale_type = "cpu"

        # Calculate recommended values
        recommended = {}
        if should_scale and scale_type == "memory":
            current_mb = autoscale["current_memory_mb"]
            if direction == "up":
                new_mb = min(
                    autoscale["max_memory_mb"],
                    int(current_mb * 1.5),
                )
            else:
                new_mb = max(
                    autoscale["min_memory_mb"],
                    int(current_mb * 0.75),
                )
            recommended["memory_mb"] = new_mb
        elif should_scale and scale_type == "cpu":
            current_quota = autoscale["current_cpu_quota"]
            if direction == "up":
                new_quota = min(
                    autoscale["max_cpu_quota"],
                    int(current_quota * 1.5),
                )
            else:
                new_quota = max(
                    autoscale["min_cpu_quota"],
                    int(current_quota * 0.75),
                )
            recommended["cpu_quota"] = new_quota

        return {
            "container_id": container.id,
            "should_scale": should_scale,
            "direction": direction,
            "scale_type": scale_type,
            "reason": reason,
            "current": {
                "memory_mb": autoscale["current_memory_mb"],
                "memory_pct": round(mem_pct, 1),
                "cpu_quota": autoscale["current_cpu_quota"],
                "cpu_throttle_pct": round(cpu_throttle, 1),
            },
            "recommended": recommended,
            "config": {
                "target_memory_pct": autoscale["target_memory_pct"],
                "target_cpu_pct": autoscale["target_cpu_pct"],
                "min_memory_mb": autoscale["min_memory_mb"],
                "max_memory_mb": autoscale["max_memory_mb"],
            },
        }

    def apply_auto_scaling(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Apply auto-scaling if needed.

        Evaluates and applies resource adjustments.

        Returns:
            Dict with the scaling result.
        """
        evaluation = self.evaluate_auto_scaling(container)
        if not evaluation["should_scale"]:
            return {
                "container_id": container.id,
                "scaled": False,
                "reason": evaluation["reason"],
            }

        autoscale = container._autoscale
        recommended = evaluation["recommended"]
        now = time.time()

        # Apply the scaling
        if "memory_mb" in recommended:
            new_mb = recommended["memory_mb"]
            old_mb = autoscale["current_memory_mb"]
            container.config.limits.memory_mb = new_mb
            autoscale["current_memory_mb"] = new_mb
            logger.info(
                "auto_scale: %s memory %d → %d MB",
                container.id, old_mb, new_mb,
            )

        if "cpu_quota" in recommended:
            new_quota = recommended["cpu_quota"]
            old_quota = autoscale["current_cpu_quota"]
            container.config.limits.cpu_quota_us = new_quota
            autoscale["current_cpu_quota"] = new_quota
            logger.info(
                "auto_scale: %s cpu_quota %d → %d",
                container.id, old_quota, new_quota,
            )

        # Record event
        event = {
            "timestamp": now,
            "direction": evaluation["direction"],
            "scale_type": evaluation["scale_type"],
            "reason": evaluation["reason"],
            "old": evaluation["current"],
            "new": recommended,
        }
        autoscale["scale_events"].append(event)
        autoscale["last_scale_time"] = now
        autoscale["last_scale_direction"] = evaluation["direction"]
        # Keep last 100 events
        if len(autoscale["scale_events"]) > 100:
            autoscale["scale_events"] = autoscale["scale_events"][-100:]

        return {
            "container_id": container.id,
            "scaled": True,
            "direction": evaluation["direction"],
            "scale_type": evaluation["scale_type"],
            "old": evaluation["current"],
            "new": recommended,
        }

    def get_auto_scaling_status(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Get auto-scaling status for a container.

        Returns:
            Dict with current config, status, and recent events.
        """
        if not hasattr(container, '_autoscale') or \
                not container._autoscale:
            return {
                "container_id": container.id,
                "enabled": False,
                "config": {},
                "events": [],
            }

        autoscale = container._autoscale
        return {
            "container_id": container.id,
            "enabled": autoscale.get("enabled", False),
            "config": {
                "min_memory_mb": autoscale.get("min_memory_mb"),
                "max_memory_mb": autoscale.get("max_memory_mb"),
                "target_memory_pct": autoscale.get("target_memory_pct"),
                "min_cpu_quota": autoscale.get("min_cpu_quota"),
                "max_cpu_quota": autoscale.get("max_cpu_quota"),
                "target_cpu_pct": autoscale.get("target_cpu_pct"),
                "scale_up_cooldown_s": autoscale.get("scale_up_cooldown_s"),
                "scale_down_cooldown_s": autoscale.get("scale_down_cooldown_s"),
            },
            "current": {
                "memory_mb": autoscale.get("current_memory_mb"),
                "cpu_quota": autoscale.get("current_cpu_quota"),
                "last_scale_time": autoscale.get("last_scale_time", 0),
                "last_scale_direction": autoscale.get("last_scale_direction"),
            },
            "events": autoscale.get("scale_events", [])[-10:],
            "event_count": len(autoscale.get("scale_events", [])),
        }

    def disable_auto_scaling(
        self,
        container: Container,
    ) -> Dict[str, Any]:
        """Disable auto-scaling for a container."""
        if hasattr(container, '_autoscale') and container._autoscale:
            container._autoscale["enabled"] = False
        return {
            "container_id": container.id,
            "enabled": False,
        }

    def get_auto_scaling_events(
        self,
        container: Container,
        tail: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get auto-scaling event history."""
        if not hasattr(container, '_autoscale') or \
                not container._autoscale:
            return []
        events = container._autoscale.get("scale_events", [])
        if tail is not None:
            return list(events[-tail:])
        return list(events)

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
    # Process management (per-process control within a container)
    # ------------------------------------------------------------------

    def kill_process(
        self, container: Container, pid: int,
        signal: int = signal.SIGTERM,
    ) -> Dict[str, Any]:
        """Send a signal to a specific process inside a container.

        Verifies the target PID belongs to the container by checking
        the init's children list (or matching the container's own PID).
        Sends the signal via ``os.kill()`` (which works across PID
        namespaces when the sender has CAP_SYS_PTRACE or is root,
        or when the target is in the same PID namespace).

        Args:
            container: The container whose process to signal.
            pid: The PID to signal (host PID).
            signal: Signal number (default SIGTERM).

        Returns:
            Dict with ``ok``, ``pid``, ``signal_name``.
        """
        if container.state != ContainerState.RUNNING:
            return {
                "ok": False,
                "error": f"container is {container.state.value}, not running",
            }
        if container.pid is None:
            return {
                "ok": False,
                "error": "container has no PID",
            }

        # Verify the PID is within this container's process tree
        allowed_pids = {container.pid}
        try:
            children_path = (
                f"/proc/{container.pid}/task/{container.pid}/children"
            )
            with open(children_path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
                if raw:
                    allowed_pids.update(
                        int(p) for p in raw.split() if p.isdigit()
                    )
        except (OSError, ValueError):
            pass

        if pid not in allowed_pids:
            return {
                "ok": False,
                "error": f"PID {pid} does not belong to container {container.id}",
            }

        try:
            os.kill(pid, signal)
            sig_name = signal.Signals(signal).name
            return {
                "ok": True,
                "pid": pid,
                "signal_name": sig_name,
            }
        except OSError as e:
            return {
                "ok": False,
                "error": str(e),
            }

    def list_processes(
        self, container: Container,
    ) -> Dict[str, Any]:
        """List processes in a container with resource details.

        A convenience wrapper around ``container_top`` that returns
        a dict with ``container_id`` and ``processes`` list.

        Returns:
            Dict with ``container_id``, ``processes`` list.
        """
        procs = self.container_top(container)
        return {
            "container_id": container.id,
            "processes": procs,
        }

    def signal_all(
        self, container: Container,
        signal_num: int = signal.SIGTERM,
    ) -> Dict[str, Any]:
        """Send a signal to all processes in a container.

        Iterates the container's children and sends the signal to
        each. The init process itself is not signaled (it manages
        the container's lifecycle).

        Returns:
            Dict with ``signaled`` (list of PIDs) and ``failed`` list.
        """
        if container.state != ContainerState.RUNNING:
            return {
                "signaled": [],
                "failed": [{"pid": 0, "error": "not running"}],
            }
        if container.pid is None:
            return {
                "signaled": [],
                "failed": [{"pid": 0, "error": "no PID"}],
            }

        signaled: List[int] = []
        failed: List[Dict[str, Any]] = []

        try:
            children_path = (
                f"/proc/{container.pid}/task/{container.pid}/children"
            )
            with open(children_path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
                children = (
                    [int(p) for p in raw.split() if p.isdigit()]
                    if raw else []
                )
        except (OSError, ValueError):
            children = []

        for pid in children:
            try:
                os.kill(pid, signal_num)
                signaled.append(pid)
            except OSError as e:
                failed.append({"pid": pid, "error": str(e)})

        return {
            "signaled": signaled,
            "failed": failed,
        }

    # ------------------------------------------------------------------
    # Snapshot scheduling (automated periodic snapshots)
    # ------------------------------------------------------------------

    def configure_snapshot_schedule(
        self,
        container: Container,
        enabled: bool = True,
        interval_s: float = 3600.0,
        max_snapshots: int = 10,
        label_prefix: str = "scheduled",
    ) -> Dict[str, Any]:
        """Configure automated periodic snapshots for a container.

        When enabled, the daemon periodically checkpoints the
        container and retains the last ``max_snapshots`` snapshots,
        deleting older ones (rolling window).

        Note: the actual periodic trigger runs in the daemon's
        event loop; this method configures the policy and performs
        an immediate snapshot if one doesn't exist yet.

        Args:
            container: Target container.
            enabled: Whether scheduling is active.
            interval_s: Seconds between snapshots.
            max_snapshots: Maximum snapshots to retain.
            label_prefix: Prefix for snapshot labels.

        Returns:
            Dict with the schedule configuration.
        """
        if not hasattr(container, "_snapshot_schedule"):
            container._snapshot_schedule = {}

        container._snapshot_schedule.update({
            "enabled": enabled,
            "interval_s": interval_s,
            "max_snapshots": max_snapshots,
            "label_prefix": label_prefix,
            "last_snapshot_time": (
                container._snapshot_schedule.get("last_snapshot_time")
            ),
            "snapshot_count": (
                container._snapshot_schedule.get("snapshot_count", 0)
            ),
        })

        return {
            "container_id": container.id,
            "schedule": dict(container._snapshot_schedule),
        }

    def get_snapshot_schedule(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Get the snapshot schedule for a container.

        Returns:
            Dict with ``container_id`` and ``schedule``.
        """
        schedule = getattr(container, "_snapshot_schedule", {})
        return {
            "container_id": container.id,
            "schedule": dict(schedule) if schedule else None,
        }

    def disable_snapshot_schedule(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Disable snapshot scheduling for a container.

        Returns:
            Dict with ``container_id`` and ``disabled`` flag.
        """
        if hasattr(container, "_snapshot_schedule"):
            container._snapshot_schedule["enabled"] = False
        return {
            "container_id": container.id,
            "disabled": True,
        }

    def run_scheduled_snapshot(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Run a scheduled snapshot now (triggered by daemon timer).

        Performs a checkpoint and enforces the rolling window by
        removing old snapshots beyond ``max_snapshots``.

        Returns:
            Dict with ``ok``, ``snapshot_id``, ``pruned`` count.
        """
        schedule = getattr(container, "_snapshot_schedule", {})
        if not schedule.get("enabled"):
            return {
                "ok": False,
                "error": "snapshot scheduling is not enabled",
            }

        # Take a checkpoint
        checkpoint = self.container_checkpoint(container)
        snapshot_id = (
            f"{schedule.get('label_prefix', 'scheduled')}-"
            f"{int(time.time())}"
        )
        checkpoint["snapshot_id"] = snapshot_id
        checkpoint["label"] = (
            f"{schedule.get('label_prefix', 'scheduled')}-"
            f"{int(time.time())}"
        )

        # Track in the container's checkpoint history
        if not hasattr(container, "_scheduled_snapshots"):
            container._scheduled_snapshots = []
        container._scheduled_snapshots.append(checkpoint)

        # Enforce rolling window
        max_keep = schedule.get("max_snapshots", 10)
        pruned = 0
        while len(container._scheduled_snapshots) > max_keep:
            container._scheduled_snapshots.pop(0)
            pruned += 1

        # Update schedule state
        schedule["last_snapshot_time"] = time.time()
        schedule["snapshot_count"] = (
            schedule.get("snapshot_count", 0) + 1
        )

        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "pruned": pruned,
            "total_snapshots": len(container._scheduled_snapshots),
        }

    def list_scheduled_snapshots(
        self, container: Container,
    ) -> Dict[str, Any]:
        """List all scheduled snapshots for a container.

        Returns:
            Dict with ``container_id`` and ``snapshots`` list.
        """
        snapshots = getattr(container, "_scheduled_snapshots", [])
        return {
            "container_id": container.id,
            "snapshots": [
                {
                    "snapshot_id": s.get("snapshot_id", "?"),
                    "label": s.get("label", "?"),
                    "timestamp": s.get("timestamp", 0),
                    "state": s.get("state", {}),
                }
                for s in snapshots
            ],
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

    def get_dependency_health(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Check the health of all containers a container depends on.

        For each container in ``depends_on``, reports its current
        state and health status.  A dependency is considered healthy
        if it is RUNNING and (has no health check configured OR its
        health_status is "healthy").

        Returns:
            Dict with ``container_id``, ``dependencies`` (list of
            dicts with ``id``, ``state``, ``health``, ``healthy``),
            ``all_healthy`` flag.
        """
        deps = container.config.depends_on or []
        results: List[Dict[str, Any]] = []
        all_healthy = True

        for dep_id in deps:
            c = self.containers.get(dep_id)
            if c is None:
                results.append({
                    "id": dep_id,
                    "state": "missing",
                    "health": "unknown",
                    "healthy": False,
                })
                all_healthy = False
                continue

            health = getattr(c, "health_status", "unknown")
            has_health_check = bool(c.config.health_check_cmd)

            # Consider healthy if running and (no health check or healthy)
            if c.state.value == "running":
                if has_health_check:
                    is_healthy = health == "healthy"
                else:
                    is_healthy = True
            else:
                is_healthy = False

            results.append({
                "id": dep_id,
                "state": c.state.value,
                "health": health,
                "healthy": is_healthy,
            })

            if not is_healthy:
                all_healthy = False

        return {
            "container_id": container.id,
            "dependencies": results,
            "all_healthy": all_healthy,
        }

    def get_reverse_dependency_health(
        self, container: Container,
    ) -> Dict[str, Any]:
        """Check which containers that DEPEND ON this container are affected.

        Returns the health status of all containers that list this
        container in their ``depends_on``.

        Returns:
            Dict with ``container_id``, ``dependents`` (list of
            dicts), ``all_healthy`` flag.
        """
        dependents: List[Dict[str, Any]] = []
        all_healthy = True

        for cid, c in self.containers.items():
            if cid == container.id:
                continue
            deps = c.config.depends_on or []
            if container.id not in deps:
                continue

            health = getattr(c, "health_status", "unknown")
            has_health_check = bool(c.config.health_check_cmd)
            if c.state.value == "running":
                if has_health_check:
                    is_healthy = health == "healthy"
                else:
                    is_healthy = True
            else:
                is_healthy = False

            dependents.append({
                "id": cid,
                "state": c.state.value,
                "health": health,
                "healthy": is_healthy,
            })
            if not is_healthy:
                all_healthy = False

        return {
            "container_id": container.id,
            "dependents": dependents,
            "all_healthy": all_healthy,
        }

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
