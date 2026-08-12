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

**Status:** Experimental (Core implementation complete; data-plane security enforcement, FUSE operations, and boot hardening landed; performance optimization and conformance benchmarks pending)

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

### 2. Capability Enforcement (`backend/capability.py`, `backend/seccomp.py`, `backend/launcher.py`)

Implements the capability-based security model from NPS-011 with **two
enforcement layers**: the control plane (the backend's own API) and the
**data plane** — a seccomp-BPF filter compiled from the container's
capability set and installed *inside the container* before its command
runs, so direct syscalls for ungranted operations are refused with
`EPERM` at the kernel boundary (closes threat-model finding
`FIND-BACKEND-002`).

**Key Classes:**
- `Capability`: Enum of capabilities (core, graphics, AI, Android; media split into images/video/audio)
- `CapabilityManager`: Sole arbiter of capability validity
- `CapabilityGrant`: Audit trail entry for capability operations
- `SeccompPolicy` / `build_policy` / `build_program`: capability set → classic-BPF filter
- `simulate`: pure-Python BPF interpreter used by the test suite to prove policy decisions
- `install_filter`: `prctl(PR_SET_NO_NEW_PRIVS)` + `PR_SET_SECCOMP` via `ctypes`

**Features:**
- Capability grant/revoke/validate operations
- Capability attenuation per NPS-003 §5
- Audit trail for all operations
- Prevention of self-issued or forged capabilities
- Default capability set for new containers
- Seccomp-BPF data-plane enforcement (whole-syscall denies + flag-gated `openat`/`open` write-intent denies)
- Default-deny allowlist posture (`build_allowlist_policy`, launcher `--default-deny`): only the runtime baseline + granted capabilities are allowed; everything else is refused with `EPERM`

**Example:**
```python
from backend.capability import CapabilityManager, Capability
from backend.seccomp import build_policy, build_program, simulate, SyscallArch

manager = CapabilityManager()
manager.initialize_container("container-001")
manager.grant_capability("container-001", Capability.CAP_GRAPHICS_RENDER)

# Validate operation (control plane)
if manager.validate_operation("container-001", Capability.CAP_GRAPHICS_RENDER):
    print("Container can render graphics")

# Build the data-plane filter for a capability set and prove a decision
policy = build_policy({c.value for c in manager.get_default_capabilities()})
program = build_program(policy)
print(hex(simulate(program, 257, SyscallArch.X86_64.audit_arch, [0, 0, 1])))  # openat(O_WRONLY)
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

Implements the NyFS filesystem with copy-on-write, snapshots, checksumming, transparent compression, and FUSE operations per ADR-0016.

**Key Classes:**
- `NyFSFilesystem`: Core filesystem logic + path-based API (resolve, tree linking)
- `NyFSBlock`: Compressed data block with checksum
- `NyFSInode`: In-memory inode representation
- `NyFSOperations`: FUSE operation handlers (getattr, readdir, read/write, mkdir, unlink, rename, …)
- `NyFSMount`: FUSE mount wrapper (loads `fusepy` by path to dodge the package-name clash)

**Features:**
- Copy-on-Write (CoW) file/directory operations
- Snapshots: create, restore (restore rebinds the root inode), list
- SHA256 checksumming for data integrity
- Zstandard compression (ADR-0007)
- Path-based inode tree with parent/child linking
- Real FUSE mount via `fusepy` when available; honest deferral otherwise

**Example:**
```python
from fuse.nyfs import NyFSFilesystem, NyFSOperations, NyFSMount

fs = NyFSFilesystem("/tmp/nyfs")
file_inode = fs.create_file("/test.txt")
fs.write(file_inode, b"Hello, NyFS!")

# Create snapshot
snap_id = fs.create_snapshot()

# Read data back (path API)
data = fs.read("/test.txt")

# FUSE operations, testable without a kernel mount
ops = NyFSOperations(fs)
st = ops.getattr("/test.txt")
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
│   ├── capability.py         # Capability registry (NPS-017 §4.2)
│   ├── seccomp.py            # Data-plane capability enforcement (cBPF policy + simulator)
│   └── launcher.py           # In-namespace launcher (hostname, cgroup hardening, seccomp, exec)
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

# Run a container (seccomp data-plane enforcement on by default)
python3 nythera_backend.py container run --memory 512 /bin/sh

# Run with custom limits, capability set, and seccomp explicitly disabled
python3 nythera_backend.py container run \
  --hostname custom \
  --memory 256 \
  --pids 32 \
  --capabilities CAP_FILESYSTEM_READ,CAP_NETWORK_SOCKET \
  --default-deny \
  /bin/bash
```

(`--default-deny` switches from the default-allow deny model to the
default-deny allowlist posture: only the runtime baseline plus granted
capabilities are permitted. `--no-seccomp` disables enforcement entirely
and is NOT recommended.)

### Secure Boot Status
```bash
# Report the host's Secure Boot engagement (efivars + mokutil probes)
python3 nythera_backend.py secure-boot-status
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
| Container Primitives | ✓ Implemented | State machine, namespaces, cgroups, shell-free launcher |
| Capability Enforcement | ✓ Implemented | Registry + data-plane seccomp (default-allow deny model; allowlist deferred) |
| IPC Semantics | ✓ Implemented | All primitives, rate limiting, receive-side capability check |
| Storage Guarantees | ✓ Implemented | Core logic + FUSE operations + fusepy mount wiring |
| Boot and Lifecycle | ✓ Implemented | Four-phase sequence, transition validation, Secure Boot reporting |

**Outstanding Work:**
- [ ] Make default-deny the default posture (currently opt-in via `--default-deny`; x86_64 baseline verified, arm64 pending)
- [ ] LSM (AppArmor/SELinux) policy generation
- [ ] FUSE overhead benchmarking (ADR-0016; decides FUSE vs kernel-module fallback)
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
- **NPS-022**: Container Escape Analysis (FIND-BACKEND-002/003/004)
- **NPS-023**: Secure Boot Threat Model (FIND-BOOT-001/002)

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
