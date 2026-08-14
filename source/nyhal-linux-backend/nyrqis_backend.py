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
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Add source directory to path
sys.path.insert(0, str(Path(__file__).parent))

from backend.container import ContainerManager, ContainerConfig, ResourceLimits
from backend.capability import CapabilityManager, Capability
from ipc.core import IPCManager
from ipc.transport import IPCDatagramServer
from ipc.registry import ContainerIpcRegistry
from ipc.service import BackendStatusService
from fuse.nyfs import NyFSFilesystem
from boot.lifecycle import BootSequence


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


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


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
                 backend_version: Optional[str] = None) -> None:
        if not socket_path:
            raise ValueError("socket_path is required")
        self.socket_path = socket_path
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
        )
        self.service = BackendStatusService(
            capability_manager=self.capability_manager,
            backend_version=backend_version,
        )
        self.service.attach(self.server)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> "StatusServiceHost":
        """Bind the socket and start the serve loop thread."""
        self.server.bind()
        self._thread = threading.Thread(
            target=self.server.serve, args=(self._stop,), daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Signal the serve loop, let it exit cleanly, then release the
        socket (the loop polls at 0.2s, so the join is quick and the
        thread never receives on a closed fd)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
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
    """Serve the container-facing status service until interrupted."""
    setup_logging(args.verbose)
    host = StatusServiceHost(
        socket_path=args.socket,
        backend_version=args.backend_version or None,
    )
    host.serve_until_signal()
    return 0


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
    serve_parser.set_defaults(func=cmd_service_serve)

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
