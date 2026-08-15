#!/usr/bin/env python3
"""
Nyrqis Linux Backend CLI

Main entry point for the Nyrqis Linux Backend implementation.
Provides commands for:
- boot: Start the Nyrqis system
- container: Manage containers
- capability: Manage capabilities
- ipc: Manage IPC endpoints
- filesystem: Manage NyFS filesystem

References:
- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- ADR-0012: Adopt NyHAL as a pluggable kernel abstraction layer
"""

import argparse
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

# Add source directory to path
sys.path.insert(0, str(Path(__file__).parent))

from backend.container import ContainerManager, ContainerConfig, ResourceLimits
from backend.capability import CapabilityManager, Capability
from ipc.core import IPCManager
from ipc.transport import IPCDatagramServer, IPCClient, UnixDatagramEndpoint
from ipc.registry import ContainerIpcRegistry
from ipc.service import BackendStatusService, ServiceRouter
from ipc.control import ControlService, DEFAULT_OPERATOR_ID
from ipc import loop as ipc_loop
from fuse.nyfs import NyFSFilesystem
from boot.lifecycle import BootSequence
from backend.daemon_state import DaemonStateFile

logger = logging.getLogger(__name__)


def parse_capabilities(value: str) -> list:
    """Parse a comma-separated capability list, validating against the registry."""
    names = [v.strip().upper() for v in (value or "").split(",") if v.strip()]
    known = {c.value for c in Capability}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"unknown capability(ies): {', '.join(unknown)}")
    return names


def make_container_config(args) -> ContainerConfig:
    """Build a ContainerConfig from parsed CLI args (shared by create/run)."""
    capabilities = parse_capabilities(getattr(args, "capabilities", ""))
    return ContainerConfig(
        hostname=args.hostname,
        command=args.command or ["/bin/sh"],
        limits=ResourceLimits(
            memory_mb=args.memory,
            pid_limit=args.pids,
        ),
        capabilities=capabilities,
        seccomp=not getattr(args, "no_seccomp", False),
        default_deny=getattr(args, "default_deny", False),
    )


def setup_logging(verbose: bool = False, syslog: bool = False) -> None:
    """Configure logging (plan §4.5).

    ``syslog`` additionally mirrors records to the system journal via
    the Unix ``/dev/log`` socket (falling back to UDP 514 when the
    socket is unavailable). Best effort: a host without a syslog daemon
    degrades to stderr logging with a warning.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    if syslog:
        try:
            from logging.handlers import SysLogHandler
            root = logging.getLogger()
            # Guard against double-attach (a second setup_logging call
            # in the same process must not stack a duplicate mirror).
            # Name-based: robust even when SysLogHandler is patched.
            if any(type(h).__name__ == "SysLogHandler"
                   for h in root.handlers):
                logger.info("logging: syslog mirror already attached")
                return
            try:
                handler = SysLogHandler(address="/dev/log")
            except OSError:
                handler = SysLogHandler(address=("localhost", 514))
            handler.setLevel(level)
            handler.setFormatter(logging.Formatter(
                "nyrqis-backend[%(process)d]: %(levelname)s "
                "%(name)s: %(message)s"))
            root.addHandler(handler)
            logger.info("logging: syslog mirror attached")
        except Exception as exc:  # noqa: BLE001 - syslog is best effort
            logger.warning("logging: syslog unavailable (%s); "
                           "continuing with stderr", exc)


def cmd_boot(args) -> int:
    """Execute the boot sequence."""
    setup_logging(args.verbose)
    
    boot = BootSequence()
    success = boot.boot()
    
    print("\n" + boot.get_boot_report())
    
    if success:
        print("\n✓ Boot successful! System is ready.")
        if not args.no_wait:
            print("Press Ctrl+C to shutdown.")
            try:
                boot.shutdown_event.wait()
            except KeyboardInterrupt:
                print("\nShutting down...")
                boot.shutdown()
        return 0
    else:
        print("\n✗ Boot failed!")
        return 1


def cmd_secure_boot_status(args) -> int:
    """Report the host's Secure Boot posture (NPS-017 §4.5, FIND-BOOT-001)."""
    setup_logging(args.verbose)
    
    boot = BootSequence()
    status = boot.secure_boot_status()
    
    print("Secure Boot Status")
    print("=" * 60)
    if status.detected:
        print(f"  {status.mode}")
        print(f"  Source: {status.source}")
        return 0 if status.enabled else 1
    else:
        print(f"  {status.mode}")
        print(f"  Note: {status.error}")
        return 2


def cmd_container_create(args) -> int:
    """Create a new container."""
    setup_logging(args.verbose)
    
    manager = ContainerManager()
    
    config = make_container_config(args)
    
    container = manager.create(config)
    print(f"Created container: {container.id}")
    
    if args.run:
        print(f"Starting container...")
        exit_code = manager.start(container)
        print(f"Container exited with code: {exit_code}")
        return exit_code
    
    return 0


def cmd_container_run(args) -> int:
    """Create and run a container."""
    setup_logging(args.verbose)
    
    manager = ContainerManager()
    
    config = make_container_config(args)
    
    container = manager.create(config)
    print(f"Running container: {container.id}")
    
    exit_code = manager.start(container)
    print(f"Container exited with code: {exit_code}")
    
    return exit_code


def cmd_capability_list(args) -> int:
    """List all available capabilities."""
    setup_logging(args.verbose)
    
    print("Available Nyrqis Capabilities:")
    print("=" * 60)
    
    for cap in Capability:
        print(f"  {cap.value}")
    
    print("=" * 60)
    print(f"Total: {len(Capability)} capabilities")
    
    return 0


def cmd_capability_grant(args) -> int:
    """Grant a capability to a container."""
    setup_logging(args.verbose)
    
    manager = CapabilityManager()
    manager.initialize_container(args.container_id)
    
    try:
        cap = Capability[args.capability]
        manager.grant_capability(args.container_id, cap)
        print(f"✓ Granted {cap.value} to container {args.container_id}")
        return 0
    except KeyError:
        print(f"✗ Unknown capability: {args.capability}")
        return 1


class StatusServiceHost:
    """A runnable status-service daemon: the transport server serving
    the BackendStatusService with auto-maintained sender identity and
    control-plane capability grants (plan §4.3, §4.5).

    Owns the pieces that must share state for the trust chain to work:

    - ``ipc_registry`` — the pid → container mapping the server
      authenticates against, kept in sync by ``container_manager``.
    - ``capability_manager`` — the control-plane grants the server
      (CAP_IPC_SEND) and the service (CAP_SYSTEM_INFO) enforce, kept in
      sync by ``container_manager`` (each spawned container is
      initialized with its default grants at spawn, revoked on
      termination — NPS-010 §5).
    - ``container_manager`` — the manager an operator uses to spawn
      containers against this daemon; those containers are
      automatically authenticated AND granted, so they can call the
      status service with zero manual bookkeeping.
    """

    def __init__(self, socket_path: str,
                 backend_version: Optional[str] = None,
                 state_file: Optional[str] = None,
                 health_socket_path: Optional[str] = None) -> None:
        if not socket_path:
            raise ValueError("socket_path is required")
        self.socket_path = socket_path
        # Plan §4.3 / ADR-0021 health-probe socket: a dedicated path
        # served by the Rust serving loop when the crate is present
        # (ping-only, the loop's first-increment scope) and by the
        # floor's status service otherwise — both answer ping with
        # byte-identical replies. The health socket is operator/systemd
        # facing (trusted-uid policy) AND container-facing (the loop's
        # pid→container table, refreshed on spawn/terminate), while a
        # liveness probe never contends with container traffic on the
        # service socket.
        self.health_socket_path = health_socket_path
        # Plan §4.5 persistent state: a versioned, atomically-written
        # JSON record of the daemon identity + container manifest used
        # for crash-recovery reporting (never auto-resumption). None
        # disables persistence.
        self.state_file = state_file
        self.state = DaemonStateFile(state_file) if state_file else None
        self._recovery: Optional[dict] = None
        self._started_at = time.time()
        self.ipc_manager = IPCManager()
        self.ipc_manager.create_endpoint("container-svc", "ep-svc")
        self.ipc_registry = ContainerIpcRegistry()
        self.capability_manager = CapabilityManager()
        self.container_manager = ContainerManager(
            use_cgroups_v2=False, use_direct_syscalls=True,
            ipc_registry=self.ipc_registry,
            capability_manager=self.capability_manager,
        )
        self.server = IPCDatagramServer(
            self.ipc_manager, "ep-svc", socket_path,
            pid_registry=self.ipc_registry,
            capability_manager=self.capability_manager,
            # The daemon's own user is the operator (control plane);
            # container resolution stays pid-first, so daemon-spawned
            # containers are never misattributed.
            trusted_uids={os.getuid()},
        )
        self.service = BackendStatusService(
            capability_manager=self.capability_manager,
            backend_version=backend_version,
            daemon=self,
        )
        self.control = ControlService(
            self.container_manager, self.capability_manager,
            state_saver=self._save_state)
        self.router = ServiceRouter()
        self.router.register("status", self.service)
        self.router.register("control", self.control)
        self.router.attach(self.server)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Health-probe socket state (ADR-0021): bound only when
        # ``health_socket_path`` is set. One of ``health_loop`` (Rust
        # loop) or ``health_floor`` (floor fallback) is active.
        self.health_endpoint: Optional[UnixDatagramEndpoint] = None
        self.health_loop: Optional[ipc_loop.IpcdLoop] = None
        self.health_floor: Optional[IPCDatagramServer] = None
        self._health_thread: Optional[threading.Thread] = None

    def start(self) -> "StatusServiceHost":
        """Bind the socket, start the serve loop thread, then recover
        from (and record) the previous daemon's state file. If a
        health socket is configured, serve it too (Rust loop when the
        crate is present, floor otherwise)."""
        self.server.bind()
        self._thread = threading.Thread(
            target=self.server.serve, args=(self._stop,), daemon=True)
        self._thread.start()
        self._start_health_socket()
        self._recover()
        self._save_state()
        return self

    def _start_health_socket(self) -> None:
        """Serve ping on the dedicated health socket (ADR-0021): the
        Rust serving loop when the crate is present, the floor's status
        service otherwise. Both answer the operator's ping with
        byte-identical replies, so a probe cannot tell which backend
        answered.

        Loop policy: trusted-uid (operator) PLUS the live pid→container
        table snapshot (ADR-0021's per-container pid-table refresh) —
        the registry's change hook re-pushes the policy on every
        container spawn/terminate, so a container whose pid is in the
        registry can probe the health socket too."""
        if not self.health_socket_path:
            return
        if ipc_loop.available():
            # The loop takes the bound fd directly (the endpoint owns
            # 0700 + SO_PASSCRED + unlink; the loop never closes it).
            # The policy starts from the CURRENT registry snapshot (a
            # container spawned before the health socket started is
            # authorized immediately) and the registry's change hook
            # keeps it in sync thereafter.
            self.health_endpoint = UnixDatagramEndpoint(
                self.health_socket_path).bind()
            self.health_loop = ipc_loop.IpcdLoop(
                self.health_endpoint._sock.fileno(),
                batch_max=64,
                pids=self.ipc_registry.snapshot(),
                trusted_uids=[os.getuid()],
                operator_id=DEFAULT_OPERATOR_ID,
            )
            self.ipc_registry.set_on_change(self._refresh_health_policy)
            self._health_thread = threading.Thread(
                target=self._drive_health_loop, daemon=True)
            self._health_thread.start()
            logger.info(
                "status host: health socket %s served by the Rust "
                "serving loop (ADR-0021)", self.health_socket_path)
        else:
            # Floor fallback: an IPCDatagramServer binds its own
            # endpoint; the status service answers the same ping.
            self.ipc_manager.create_endpoint("container-svc", "ep-health")
            self.ipc_manager.create_endpoint("container-svc", "ep-health")
            self.health_floor = IPCDatagramServer(
                self.ipc_manager, "ep-health", self.health_socket_path,
                pid_registry=self.ipc_registry,
                capability_manager=self.capability_manager,
                trusted_uids={os.getuid()},
            )
            BackendStatusService(
                capability_manager=self.capability_manager,
                backend_version=self.service.backend_version,
                daemon=self,
            ).attach(self.health_floor)
            self.health_floor.bind()
            self._health_thread = threading.Thread(
                target=self.health_floor.serve, args=(self._stop,),
                daemon=True)
            self._health_thread.start()
            logger.info(
                "status host: health socket %s served by the Python "
                "floor (ipcd crate absent)", self.health_socket_path)

    def _refresh_health_policy(self) -> None:
        """Push the current registry snapshot into the health loop (the
        per-container pid-table refresh). Called by the registry's
        change hook on every container spawn/terminate, so the loop's
        authorization stays in sync without recreating it. Best effort:
        a closed or absent loop is a no-op (the hook cannot fail
        container lifecycle — the registry swallows its exceptions)."""
        loop = self.health_loop
        if loop is None:
            return
        try:
            loop.set_policy(
                pids=self.ipc_registry.snapshot(),
                trusted_uids=[os.getuid()],
                operator_id=DEFAULT_OPERATOR_ID,
            )
        except Exception:  # noqa: BLE001 - policy refresh is best effort
            logger.warning(
                "status host: health loop policy refresh failed",
                exc_info=True)

    def _drive_health_loop(self) -> None:
        """Drive the Rust serving loop until the host stops. A step
        error is logged and the loop keeps going (one bad datagram must
        not kill the health path); a persistent failure backs off to
        avoid a hot spin."""
        while not self._stop.is_set():
            try:
                self.health_loop.step(100)
            except Exception:  # noqa: BLE001 - the loop must keep serving
                logger.exception(
                    "status host: health loop step failed; continuing")
                self._stop.wait(0.2)

    def _stop_health_socket(self) -> None:
        """Stop the health thread and release the health socket (the
        loop does not close the fd — the endpoint owns unlink)."""
        if self._health_thread is not None:
            self._health_thread.join(timeout=2.0)
            self._health_thread = None
        if self.health_loop is not None:
            self.health_loop.close()
            self.health_loop = None
        if self.health_floor is not None:
            self.health_floor.close()
            self.health_floor = None
        if self.health_endpoint is not None:
            self.health_endpoint.close()
            self.health_endpoint = None

    def _recover(self) -> None:
        """Plan §4.5 crash recovery: report what a previous daemon left
        behind (its pid, version, socket, and last-known container
        manifest) without resuming or killing anything. Orphaned
        processes are for the operator to review."""
        if self.state is None:
            return
        prev = self.state.load()
        if prev is None:
            return
        if DaemonStateFile.is_stale(prev, current_pid=os.getpid()):
            self._recovery = {
                "previous_pid": prev.get("daemon_pid"),
                "backend_version": prev.get("backend_version"),
                "socket_path": prev.get("socket_path"),
                "containers_left": prev.get("containers", []),
            }
            orphan_ids = [
                c.get("id") for c in self._recovery["containers_left"]
                if c.get("id")
            ]
            logger.warning(
                "daemon-state: recovered %d container record(s) from "
                "previous daemon pid %s (version %s): %s; orphaned "
                "processes are NOT auto-killed — operator review "
                "advised (full manifest in %s)",
                len(self._recovery["containers_left"]),
                prev.get("daemon_pid"), prev.get("backend_version"),
                ", ".join(orphan_ids) or "(unnamed)",
                self.state_file)
        else:
            logger.warning(
                "daemon-state: %s references live pid %s — another "
                "daemon may be running against the same state file",
                self.state_file, prev.get("daemon_pid"))

    def _save_state(self) -> None:
        """Persist the daemon identity + current container manifest
        (best effort; ``DaemonStateFile.save`` degrades to a warning
        when the directory is not writable)."""
        if self.state is None:
            return
        try:
            known = list(self.container_manager.containers.values())
        except Exception:  # noqa: BLE001 - state is best effort
            known = []
        self.state.save({
            "daemon_pid": os.getpid(),
            "backend_version": self.service.backend_version,
            "socket_path": self.socket_path,
            "started_at": self._started_at,
            "recovery": self._recovery,
            "containers": DaemonStateFile.manifest(known),
        })

    def stop(self) -> None:
        """Persist the final state, signal the serve loops, let them
        exit cleanly, then release the sockets (the loops poll at
        ≤0.2s, so the joins are quick and no thread ever receives on a
        closed fd)."""
        self._save_state()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._stop_health_socket()
        self.server.close()

    def serve_until_signal(self) -> None:
        """Serve until SIGINT/SIGTERM, then stop cleanly (the CLI
        path; signal handlers must run in the main thread)."""
        def _handler(signum, frame):  # noqa: ARG001 - signal handler signature
            self._stop.set()

        old = {}
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                old[sig] = signal.signal(sig, _handler)
            self.start()
            print(f"Status service listening on {self.socket_path}")
            print("Press Ctrl+C to stop.")
            while not self._stop.is_set():
                self._stop.wait(0.5)
        finally:
            for sig, prev in old.items():
                signal.signal(sig, prev)
            self.stop()
            print("Status service stopped.")


def cmd_service_serve(args) -> int:
    """Serve the container-facing status service until interrupted.

    ``--syslog`` mirrors daemon records to the system journal (plan
    §4.5); ``--state-file`` enables persistent crash-recovery state
    (defaults to the systemd runtime directory when running as the
    packaged service, degrading to disabled elsewhere).
    """
    setup_logging(args.verbose, syslog=args.syslog)
    host = StatusServiceHost(
        socket_path=args.socket,
        backend_version=args.backend_version or None,
        state_file=args.state_file or None,
        health_socket_path=args.health_socket or None,
    )
    host.serve_until_signal()
    return 0


def cmd_control(args) -> int:
    """Drive a running daemon's control plane (operator-only).

    The client claims the operator identity; the daemon authenticates
    it by the kernel-attached uid (the daemon's own user — an
    unforgeable check, and the only identity the control service
    accepts).
    """
    setup_logging(args.verbose)
    tmp = tempfile.mkdtemp(prefix="nyrqis-ctl-")
    cli_path = os.path.join(tmp, "ctl.sock")
    client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
    try:
        payload = {"service": "control",
                   "op": (args.control_cmd or "").replace("-", "_")}
        if args.control_cmd == "container-run":
            payload.update({
                "command": args.command,
                "capabilities": parse_capabilities(args.capabilities),
                "network": args.network,
                "memory_mb": args.memory,
                "pids": args.pids,
                "name": args.name or None,
            })
        elif args.control_cmd == "container-kill":
            payload["container_id"] = args.container_id
        reply = client.call(
            args.socket, json.dumps(payload).encode("utf-8"),
            timeout_s=args.timeout,
        )
        if reply is None:
            print("✗ no reply from the daemon (is it running?)")
            return 1
        resp = json.loads(reply.payload.decode("utf-8"))
        print(json.dumps(resp, indent=2, sort_keys=True))
        return 0 if resp.get("ok") else 1
    finally:
        client.close()
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_ipc_endpoint_create(args) -> int:
    """Create an IPC endpoint."""
    setup_logging(args.verbose)
    
    manager = IPCManager()
    endpoint = manager.create_endpoint(args.container_id, args.endpoint_id)
    
    print(f"✓ Created endpoint: {endpoint.endpoint_id}")
    print(f"  Container: {endpoint.container_id}")
    print(f"  Rate limit: {endpoint.rate_limit.tokens_per_second} tokens/sec")
    
    return 0


def cmd_filesystem_create(args) -> int:
    """Create a NyFS filesystem."""
    setup_logging(args.verbose)
    
    fs = NyFSFilesystem(args.path)
    print(f"✓ Created NyFS filesystem at {args.path}")
    
    # Create a snapshot
    snap_id = fs.create_snapshot()
    print(f"✓ Created baseline snapshot: {snap_id}")
    
    return 0


def cmd_filesystem_snapshot_list(args) -> int:
    """List snapshots in a NyFS filesystem."""
    setup_logging(args.verbose)
    
    fs = NyFSFilesystem(args.path)
    snapshots = fs.list_snapshots()
    
    if snapshots:
        print("Snapshots:")
        for snap_id in snapshots:
            print(f"  {snap_id}")
    else:
        print("No snapshots found.")
    
    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Nyrqis Linux Backend CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s boot                                # Start the Nyrqis system
  %(prog)s container run --memory 256 /bin/sh  # Run a container
  %(prog)s capability list                     # List capabilities
  %(prog)s filesystem create /tmp/nyfs         # Create a filesystem
  %(prog)s service serve --socket /tmp/nyrqis-status.sock  # Run the status service daemon
  %(prog)s control --socket /tmp/nyrqis-status.sock container-run --network /bin/sleep 30
        """
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Boot command
    boot_parser = subparsers.add_parser("boot", help="Start the Nyrqis system")
    boot_parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Don't wait for shutdown signal"
    )
    boot_parser.set_defaults(func=cmd_boot)
    
    # Secure Boot status command (NPS-017 §4.5, FIND-BOOT-001)
    sb_parser = subparsers.add_parser(
        "secure-boot-status",
        help="Report the host's Secure Boot engagement status"
    )
    sb_parser.set_defaults(func=cmd_secure_boot_status)
    
    # Container commands
    container_parser = subparsers.add_parser("container", help="Manage containers")
    container_subparsers = container_parser.add_subparsers(dest="container_cmd")
    
    create_parser = container_subparsers.add_parser("create", help="Create a container")
    create_parser.add_argument("--hostname", default="nyrqis-container")
    create_parser.add_argument("--memory", type=int, default=256, help="Memory limit (MiB)")
    create_parser.add_argument("--pids", type=int, default=64, help="PID limit")
    create_parser.add_argument(
        "--capabilities", default="",
        help="Comma-separated Nyrqis capabilities to grant (default: the default set)"
    )
    create_parser.add_argument(
        "--no-seccomp", action="store_true",
        help="Disable data-plane seccomp enforcement (NOT recommended)"
    )
    create_parser.add_argument(
        "--default-deny", action="store_true",
        help="Default-deny allowlist posture: only runtime baseline + "
        "granted capabilities allowed; everything else refused with EPERM"
    )
    create_parser.add_argument("-r", "--run", action="store_true", help="Run after creation")
    create_parser.add_argument("command", nargs="*", help="Command to run")
    create_parser.set_defaults(func=cmd_container_create)
    
    run_parser = container_subparsers.add_parser("run", help="Create and run a container")
    run_parser.add_argument("--hostname", default="nyrqis-container")
    run_parser.add_argument("--memory", type=int, default=256, help="Memory limit (MiB)")
    run_parser.add_argument("--pids", type=int, default=64, help="PID limit")
    run_parser.add_argument(
        "--capabilities", default="",
        help="Comma-separated Nyrqis capabilities to grant (default: the default set)"
    )
    run_parser.add_argument(
        "--no-seccomp", action="store_true",
        help="Disable data-plane seccomp enforcement (NOT recommended)"
    )
    run_parser.add_argument(
        "--default-deny", action="store_true",
        help="Default-deny allowlist posture: only runtime baseline + "
        "granted capabilities allowed; everything else refused with EPERM"
    )
    run_parser.add_argument("command", nargs="*", help="Command to run")
    run_parser.set_defaults(func=cmd_container_run)
    
    # Capability commands
    capability_parser = subparsers.add_parser("capability", help="Manage capabilities")
    capability_subparsers = capability_parser.add_subparsers(dest="capability_cmd")
    
    list_parser = capability_subparsers.add_parser("list", help="List capabilities")
    list_parser.set_defaults(func=cmd_capability_list)
    
    grant_parser = capability_subparsers.add_parser("grant", help="Grant a capability")
    grant_parser.add_argument("container_id", help="Container ID")
    grant_parser.add_argument("capability", help="Capability name")
    grant_parser.set_defaults(func=cmd_capability_grant)
    
    # Service commands
    service_parser = subparsers.add_parser(
        "service", help="Run backend services (the container-facing daemon)")
    service_subparsers = service_parser.add_subparsers(dest="service_cmd")

    serve_parser = service_subparsers.add_parser(
        "serve", help="Serve the container-facing status service")
    serve_parser.add_argument(
        "--socket", default="/tmp/nyrqis-status.sock",
        help="Unix datagram socket path (default: /tmp/nyrqis-status.sock)"
    )
    serve_parser.add_argument(
        "--backend-version", default="",
        help="Version the status service reports (default: the backend "
        "package version)"
    )
    serve_parser.add_argument(
        "--syslog", action="store_true",
        help="Mirror daemon records to the system journal via /dev/log "
        "(plan 4.5; best effort)"
    )
    serve_parser.add_argument(
        "--state-file", default="/run/nyrqis/daemon-state.json",
        help="Persist daemon identity + container manifest for "
        "crash-recovery reporting (plan 4.5; default: "
        "/run/nyrqis/daemon-state.json — disable with --state-file '')"
    )
    serve_parser.add_argument(
        "--health-socket", default="",
        help="Serve ping on a dedicated health-probe socket via the "
        "Rust serving loop (ADR-0021; the Python floor when the crate "
        "is absent) — default: disabled ('')"
    )
    serve_parser.set_defaults(func=cmd_service_serve)

    # Control commands (against a running daemon)
    control_parser = subparsers.add_parser(
        "control",
        help="Drive a running daemon's control plane (its own user only)")
    control_parser.add_argument(
        "--socket", default="/tmp/nyrqis-status.sock",
        help="The daemon's socket path (default: /tmp/nyrqis-status.sock)"
    )
    control_parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="CALL timeout in seconds (default: 30)"
    )
    control_subparsers = control_parser.add_subparsers(dest="control_cmd")

    ctl_run = control_subparsers.add_parser(
        "container-run", help="Spawn a container on the daemon")
    ctl_run.add_argument("--name", default="")
    ctl_run.add_argument(
        "--capabilities", default="",
        help="Comma-separated data-plane capabilities (seccomp)"
    )
    ctl_run.add_argument("--network", action="store_true",
                         help="Give the container its own network namespace")
    ctl_run.add_argument("--memory", type=int, default=256,
                         help="Memory limit (MiB)")
    ctl_run.add_argument("--pids", type=int, default=64, help="PID limit")
    ctl_run.add_argument("command", nargs="+", help="Command to run")
    ctl_run.set_defaults(func=cmd_control)

    ctl_list = control_subparsers.add_parser(
        "container-list", help="List the daemon's containers")
    ctl_list.set_defaults(func=cmd_control)

    ctl_kill = control_subparsers.add_parser(
        "container-kill", help="Terminate a container on the daemon")
    ctl_kill.add_argument("container_id")
    ctl_kill.set_defaults(func=cmd_control)

    # IPC commands
    ipc_parser = subparsers.add_parser("ipc", help="Manage IPC")
    ipc_subparsers = ipc_parser.add_subparsers(dest="ipc_cmd")
    
    ep_parser = ipc_subparsers.add_parser("endpoint", help="Manage endpoints")
    ep_subparsers = ep_parser.add_subparsers(dest="ep_cmd")
    
    ep_create_parser = ep_subparsers.add_parser("create", help="Create endpoint")
    ep_create_parser.add_argument("container_id", help="Container ID")
    ep_create_parser.add_argument("--endpoint-id", help="Custom endpoint ID")
    ep_create_parser.set_defaults(func=cmd_ipc_endpoint_create)
    
    # Filesystem commands
    fs_parser = subparsers.add_parser("filesystem", help="Manage NyFS filesystem")
    fs_subparsers = fs_parser.add_subparsers(dest="fs_cmd")
    
    fs_create_parser = fs_subparsers.add_parser("create", help="Create filesystem")
    fs_create_parser.add_argument("path", help="Filesystem path")
    fs_create_parser.set_defaults(func=cmd_filesystem_create)
    
    fs_snap_parser = fs_subparsers.add_parser("snapshot", help="Manage snapshots")
    fs_snap_subparsers = fs_snap_parser.add_subparsers(dest="snap_cmd")
    
    fs_snap_list_parser = fs_snap_subparsers.add_parser("list", help="List snapshots")
    fs_snap_list_parser.add_argument("path", help="Filesystem path")
    fs_snap_list_parser.set_defaults(func=cmd_filesystem_snapshot_list)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    if hasattr(args, 'func'):
        return args.func(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
