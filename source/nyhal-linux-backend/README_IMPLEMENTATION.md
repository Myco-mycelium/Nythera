---
title: Nythera Linux Backend — Implementation Guide
document_id: IMPL-README-001
version: 0.1.0
status: In Progress
classification: Technical
created: 2026-07-15
updated: 2026-07-15
ai_assisted: true
---

# Nythera Linux Backend — Implementation Guide

## Overview

This directory contains the implementation of the **Nythera Linux Backend**, a conformant implementation of the NyHAL (Nythera Kernel Abstraction Layer) contract on standard Linux systems. The backend provides a practical near-term path for running Nythera containers while the long-term NyKernel backend matures.

**Status:** Experimental (Core implementation complete; performance optimization and FUSE integration in progress)

## Quick Start

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Verify installation
python3 test_backend.py
```

### Running the Backend

```bash
# Boot the Nythera system
python3 nythera_backend.py boot

# In another terminal, create and run a container
python3 nythera_backend.py container run /bin/sh

# List available capabilities
python3 nythera_backend.py capability list
```

## Architecture

The Linux Backend implements the five core requirements from NPS-017 §4:

### 1. Container Primitives (`backend/container.py`)

Provides process isolation and resource management using Linux namespaces and cgroups.

**Key Classes:**
- `Container`: Represents a single container with lifecycle state machine
- `ContainerManager`: Manages multiple containers
- `ContainerConfig`: Configuration for container creation
- `ResourceLimits`: Memory, CPU, and process limits

**Features:**
- Namespace isolation (user, PID, mount, UTS, IPC)
- Cgroups v2 with v1 fallback
- Container state machine (CREATED → RUNNING → SUSPENDED → TERMINATED)
- Graceful shutdown with SIGTERM → SIGKILL escalation

**Example:**
```python
from backend.container import ContainerManager, ContainerConfig, ResourceLimits

manager = ContainerManager()
config = ContainerConfig(
    hostname="my-container",
    command=["/bin/sh"],
    limits=ResourceLimits(memory_mb=256, pid_limit=64),
)
container = manager.create(config)
exit_code = manager.start(container)
```

### 2. Capability Enforcement (`backend/capability.py`)

Implements the capability-based security model from NPS-011, ensuring containers can only access resources they've been explicitly granted.

**Key Classes:**
- `Capability`: Enum of 23 capabilities (core, graphics, AI, Android)
- `CapabilityManager`: Sole arbiter of capability validity
- `CapabilityGrant`: Audit trail entry for capability operations

**Features:**
- Capability grant/revoke/validate operations
- Capability attenuation per NPS-003 §5
- Audit trail for all operations
- Prevention of self-issued or forged capabilities
- Default capability set for new containers

**Example:**
```python
from backend.capability import CapabilityManager, Capability

manager = CapabilityManager()
manager.initialize_container("container-001")
manager.grant_capability("container-001", Capability.CAP_GRAPHICS_RENDER)

# Validate operation
if manager.validate_operation("container-001", Capability.CAP_GRAPHICS_RENDER):
    print("Container can render graphics")
```

### 3. IPC Semantics (`ipc/core.py`)

Implements the four core IPC primitives (send, receive, call, notify) with capability transfer and rate limiting.

**Key Classes:**
- `IPCMessage`: Message with payload, capabilities, and metadata
- `IPCEndpoint`: Endpoint for receiving messages
- `IPCManager`: Routes messages between endpoints
- `TokenBucket`: Rate limiting per ADR-0009

**Features:**
- Asynchronous message send
- Blocking message receive
- Synchronous call-reply pattern
- Lightweight notifications
- Token-bucket rate limiting
- Capability transfer and attenuation

**Example:**
```python
from ipc.core import IPCManager

manager = IPCManager()
ep1 = manager.create_endpoint("container-1", "ep-service")
ep2 = manager.create_endpoint("container-2", "ep-client")

# Send message
manager.send("container-2", "ep-service", b"Hello!")

# Receive message
msg = manager.receive("ep-service", timeout_s=5.0)
```

### 4. Storage Guarantees (`fuse/nyfs.py`)

Implements the NyFS filesystem with copy-on-write, snapshots, checksumming, and transparent compression.

**Key Classes:**
- `NyFSFilesystem`: Core filesystem logic
- `NyFSBlock`: Compressed data block with checksum
- `NyFSInode`: In-memory inode representation
- `NyFSMount`: FUSE mount wrapper

**Features:**
- Copy-on-Write (CoW) file/directory operations
- Snapshots: create, restore, list
- SHA256 checksumming for data integrity
- Zstandard compression (ADR-0007)
- Inode-based file management

**Example:**
```python
from fuse.nyfs import NyFSFilesystem

fs = NyFSFilesystem("/tmp/nyfs")
file_inode = fs.create_file("/test.txt")
fs.write_block(file_inode.inode_number, b"Hello, NyFS!")

# Create snapshot
snap_id = fs.create_snapshot()

# Read data back
data = fs.read_block(file_inode.inode_number)
```

### 5. Boot and Lifecycle (`boot/lifecycle.py`)

Manages the four-phase boot sequence per NPS-001 §5.

**Key Classes:**
- `BootSequence`: Manages boot phases and milestones
- `BootPhase`: Enum of boot phases
- `BootMilestone`: Records boot events

**Boot Phases:**
1. **Hardware/Host Initialization**: Detect kernel features, initialize managers
2. **Trusted First Process**: Create and launch init container
3. **Service Bring-up**: Initialize NyFS, IPC, capability systems
4. **Usable Session**: System ready for container creation

**Example:**
```python
from boot.lifecycle import BootSequence

boot = BootSequence()
success = boot.boot()
print(boot.get_boot_report())
```

## File Structure

```
nyhal-linux-backend/
├── backend/
│   ├── __init__.py           # Backend module exports
│   ├── container.py          # Container primitives (NPS-017 §4.1)
│   └── capability.py         # Capability enforcement (NPS-017 §4.2)
├── ipc/
│   ├── __init__.py           # IPC module exports
│   └── core.py               # IPC primitives (NPS-017 §4.3)
├── fuse/
│   ├── __init__.py           # FUSE module exports
│   └── nyfs.py               # NyFS filesystem (NPS-017 §4.4)
├── boot/
│   ├── __init__.py           # Boot module exports
│   └── lifecycle.py          # Boot sequence (NPS-017 §4.5)
├── poc-container/            # Original proof-of-concept
│   ├── nyctr.py
│   ├── test_nyctr.sh
│   └── README.md
├── nythera_backend.py        # CLI entry point
├── test_backend.py           # Test suite
├── requirements.txt          # Python dependencies
├── IMPLEMENTATION_STATUS.md  # Detailed status report
└── README_IMPLEMENTATION.md  # This file
```

## Command-Line Interface

The `nythera_backend.py` script provides a CLI for managing the backend:

### Boot the System
```bash
python3 nythera_backend.py boot
python3 nythera_backend.py boot --no-wait  # Don't wait for shutdown
```

### Container Management
```bash
# Create a container
python3 nythera_backend.py container create --hostname my-container

# Run a container
python3 nythera_backend.py container run --memory 512 /bin/sh

# Run with custom limits
python3 nythera_backend.py container run \
  --hostname custom \
  --memory 256 \
  --pids 32 \
  /bin/bash
```

### Capability Management
```bash
# List all capabilities
python3 nythera_backend.py capability list

# Grant a capability
python3 nythera_backend.py capability grant container-001 CAP_GRAPHICS_RENDER
```

### IPC Management
```bash
# Create an IPC endpoint
python3 nythera_backend.py ipc endpoint create container-001 --endpoint-id ep-service
```

### Filesystem Management
```bash
# Create a NyFS filesystem
python3 nythera_backend.py filesystem create /tmp/nyfs

# List snapshots
python3 nythera_backend.py filesystem snapshot list /tmp/nyfs
```

## Testing

Run the comprehensive test suite:

```bash
# Run all tests
python3 test_backend.py

# Run with verbose output
python3 test_backend.py -v

# Run specific test class
python3 -m unittest test_backend.TestContainerPrimitives
```

**Test Coverage:**
- Container primitives and state machine
- Capability grant/revoke/validate
- IPC send/receive/call/notify
- Storage write/read/snapshot
- Boot sequence phases

## Conformance Status

Per NPS-017 §5.1, the Linux Backend is **NOT YET conformant** but provides:

| Requirement | Status | Notes |
|-------------|--------|-------|
| Container Primitives | ✓ Implemented | State machine, namespaces, cgroups |
| Capability Enforcement | ⚠ Partial | Registry complete; LSM/seccomp deferred |
| IPC Semantics | ✓ Implemented | All primitives, rate limiting |
| Storage Guarantees | ⚠ Partial | Core logic; FUSE integration deferred |
| Boot and Lifecycle | ✓ Implemented | Four-phase sequence |

**Outstanding Work:**
- [ ] LSM/seccomp policy generation and enforcement
- [ ] FUSE daemon integration for NyFS
- [ ] Performance benchmarking (IPC latency, FUSE overhead)
- [ ] Direct syscall optimization (currently uses `unshare(1)`)
- [ ] Systemd integration

## Performance Benchmarks

The following benchmarks are required before conformance (see `tests/BENCHMARK_PLAN.md`):

| Benchmark | Target | Status |
|-----------|--------|--------|
| IPC Round-trip Latency | < 100µs | Pending |
| FUSE I/O Overhead | < 20% | Pending |
| Token-Bucket Parameters | TBD | Pending |
| Compression Ratio | > 30% | Pending |

## References

### Nythera Specifications
- **NPS-017**: NyHAL Kernel Abstraction Layer and Backend Contract
- **NPS-001**: Kernel Architecture and Boot (NyKernel Backend)
- **NPS-010**: Container Runtime
- **NPS-011**: Capability Registry
- **NPS-003**: Inter-Process Communication and Capability Passing
- **NPS-004**: NyFS Filesystem Core

### Architecture Decision Records
- **ADR-0012**: Adopt NyHAL as a pluggable kernel abstraction layer
- **ADR-0016**: NyFS Linux Backend implemented as a user-space FUSE filesystem
- **ADR-0009**: Per-container token-bucket rate limiting for IPC
- **ADR-0007**: Adopt Zstandard as the default compression codec
- **ADR-0006**: Adopt a hybrid microkernel as the Nythera kernel base

### Other Resources
- **NTM-000**: The Nythera Manifest
- **tests/BENCHMARK_PLAN.md**: Benchmarking methodology
- **REPOSITORY_STATE.md**: Project status tracking

## Contributing

When contributing to the Linux Backend:

1. Follow the NPS-017 §4 requirements
2. Maintain architectural integrity with NPS specifications
3. Add tests for new functionality
4. Update IMPLEMENTATION_STATUS.md with progress
5. Document any deferred work with clear reasoning

## License

The Nythera project is licensed under the terms specified in the repository's LICENSE file.

---

**End of Document**
