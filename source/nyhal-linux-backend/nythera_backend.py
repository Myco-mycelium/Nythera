#!/usr/bin/env python3
"""
Nythera Linux Backend CLI

Main entry point for the Nythera Linux Backend implementation.
Provides commands for:
- boot: Start the Nythera system
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
import sys
import time
from pathlib import Path

# Add source directory to path
sys.path.insert(0, str(Path(__file__).parent))

from backend.container import ContainerManager, ContainerConfig, ResourceLimits
from backend.capability import CapabilityManager, Capability
from ipc.core import IPCManager
from fuse.nyfs import NyFSFilesystem
from boot.lifecycle import BootSequence


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


def cmd_container_create(args) -> int:
    """Create a new container."""
    setup_logging(args.verbose)
    
    manager = ContainerManager()
    
    config = ContainerConfig(
        hostname=args.hostname,
        command=args.command or ["/bin/sh"],
        limits=ResourceLimits(
            memory_mb=args.memory,
            pid_limit=args.pids,
        ),
    )
    
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
    
    config = ContainerConfig(
        hostname=args.hostname,
        command=args.command or ["/bin/sh"],
        limits=ResourceLimits(
            memory_mb=args.memory,
            pid_limit=args.pids,
        ),
    )
    
    container = manager.create(config)
    print(f"Running container: {container.id}")
    
    exit_code = manager.start(container)
    print(f"Container exited with code: {exit_code}")
    
    return exit_code


def cmd_capability_list(args) -> int:
    """List all available capabilities."""
    setup_logging(args.verbose)
    
    print("Available Nythera Capabilities:")
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
        description="Nythera Linux Backend CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s boot                                # Start the Nythera system
  %(prog)s container run --memory 256 /bin/sh  # Run a container
  %(prog)s capability list                     # List capabilities
  %(prog)s filesystem create /tmp/nyfs         # Create a filesystem
        """
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Boot command
    boot_parser = subparsers.add_parser("boot", help="Start the Nythera system")
    boot_parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Don't wait for shutdown signal"
    )
    boot_parser.set_defaults(func=cmd_boot)
    
    # Container commands
    container_parser = subparsers.add_parser("container", help="Manage containers")
    container_subparsers = container_parser.add_subparsers(dest="container_cmd")
    
    create_parser = container_subparsers.add_parser("create", help="Create a container")
    create_parser.add_argument("--hostname", default="nythera-container")
    create_parser.add_argument("--memory", type=int, default=256, help="Memory limit (MiB)")
    create_parser.add_argument("--pids", type=int, default=64, help="PID limit")
    create_parser.add_argument("-r", "--run", action="store_true", help="Run after creation")
    create_parser.add_argument("command", nargs="*", help="Command to run")
    create_parser.set_defaults(func=cmd_container_create)
    
    run_parser = container_subparsers.add_parser("run", help="Create and run a container")
    run_parser.add_argument("--hostname", default="nythera-container")
    run_parser.add_argument("--memory", type=int, default=256, help="Memory limit (MiB)")
    run_parser.add_argument("--pids", type=int, default=64, help="PID limit")
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
