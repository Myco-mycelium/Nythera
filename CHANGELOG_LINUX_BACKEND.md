---
title: Nyrqis Linux Backend Implementation Changelog
document_id: CHANGELOG-IMPL-001
version: 0.1.0
status: In Progress
classification: Technical
created: 2026-07-15
updated: 2026-07-15
ai_assisted: true
---

# Nyrqis Linux Backend Implementation Changelog

> **Naming note (2026-08-12):** this changelog was originally kept under the
> project name *Nythera*. On 2026-08-12 the project was renamed to *Nyrqis*
> (CR-0035 — see `docs/00-platform/REBRAND_NOTICE.md`). Entries below dated
> before that date refer to the same project under its former name.

## [0.5.0] — 2026-08-14

### Unix-Domain Datagram IPC Transport

#### Added

- **`ipc/transport.py`** — the inter-process channel for NPS-017 §4.3 (plan §4.3), activating the ADR-0020 migration #4 wire codec as a real transport
  - `UnixDatagramEndpoint` — one `AF_UNIX SOCK_DGRAM` socket bound to a path (0700, path-length guarded), with `SO_PASSCRED` on the receiver and the sender's real `SCM_CREDENTIALS` attached on send
  - `IPCDatagramServer` — serves one endpoint path: parses the wire (malformed datagrams dropped at the trust boundary), authenticates the sender via the kernel-attached `(pid, uid, gid)` mapped to a container, **drops forged `sender_id`s, unknown pids, and senders lacking `CAP_IPC_SEND`** before delivery, then enqueues through the endpoint's token bucket (ADR-0009) or dispatches `CALL`s to an `on_call` handler with direct reply
  - `IPCClient` — the caller side: `send`/`notify`/`call`/`receive` over the socket; `CALL` carries the reply path in `metadata['reply_path']`, replies are correlated by `reply_to` (the client-side trust anchor)
  - `SO_PEERCRED` does NOT work on datagram sockets (returns `(0,-1,-1)` — verified on this host), so `SCM_CREDENTIALS` is the mechanism; an unprivileged sender cannot forge credentials (the kernel refuses a non-matching `ucred` with EPERM)
- **`test_backend.py`** — `TestIPCTransport` (9 tests): authenticated same-process send/receive, unknown-sender drop, forged-sender drop, malformed-wire drop, `CAP_IPC_SEND` denial, in-process CALL/REPLY, **a real cross-process CALL/REPLY with kernel-pid authentication**, inbound ADR-0009 rate limiting, and the socket-path guard. Suite **229 → 238**

#### Changed

- `IMPLEMENTATION_STATUS` 0.14.0; `implementation_plan.md` §4.3 records the landed transport (shared-memory remains deferred)

## [0.4.0] — 2026-08-14

### Loopback Up in Network Containers

#### Added

- **`backend/launcher.py`** — `bring_loopback_up()` (step 2b, before the seccomp install): best-effort `SIOCSIFFLAGS` sets `lo` up so a `network=True` container has a usable 127.0.0.1. It succeeds because the container's netns is owned by its user namespace (where the launcher is root, so CAP_NET_ADMIN applies); sharing the host netns (default) it EPERMs harmlessly — the host's `lo` is already up. Never fatal; runs before the filter so it is backend setup, not container behavior. Covers both launch paths (the launcher runs inside the container either way).
- **`test_backend.py`** — 4 unit tests for `bring_loopback_up` (sets IFF_UP, already-up no-op, EPERM graceful, no-socket graceful) plus an end-to-end bind test: a netns container granted `CAP_NETWORK_SOCKET`/`CAP_NETWORK_BIND`/`CAP_FILESYSTEM_WRITE` binds 127.0.0.1 through the real launch path *with the seccomp filter active* and writes a marker to the shared rootfs (the seccomp data plane correctly EPERMs the marker write without the filesystem grant — caught live). Suite **224 → 229**

#### Changed

- `IMPLEMENTATION_STATUS` 0.13.0; `implementation_plan.md` §4.1 records the usable-localhost posture (veth/bridge remains future work, requires host root)

## [0.3.0] — 2026-08-14

### Network Namespace Support

#### Added

- **`backend/container.py`** — opt-in per-container network namespace isolation (implementation_plan.md §4.1)
  - `ContainerConfig.network` (default `False`): when enabled, the container gets its own network namespace and sees only loopback — a pure isolation boundary; no host interfaces are visible
  - Direct-syscall path: `CLONE_NEWNET` is added to the mount/UTS/IPC unshare in the namespace-setup child (`_direct_launch_child(..., network=...)`)
  - Legacy `unshare(1)` path: `--net` flag added to the launch command
  - Outbound connectivity (veth/bridge) is deliberate future work — the netns is an isolation boundary, not a network pipe
- **`backend/rust_syscalls.py`** — `CLONE_NEWNET` constant exported (the existing `unshare(flags)` FFI passes flags through raw, so no crate change was needed)
- **`test_backend.py`** — `TestNetworkNamespaceIsolation` (2 real-launch tests, skip-gated on an honest host probe that actually launches a netns container): `network=True` container's netns inode differs from the host's and its own procfs lists only `lo`; the default container's netns equals the host's. Plus 5 unit tests (config default, legacy `--net` presence/absence, direct-child `CLONE_NEWNET` flags, manager→child flag forwarding). Suite **217 → 224**

#### Changed

- `IMPLEMENTATION_STATUS` 0.12.0; `implementation_plan.md` §4.1 documents the netns posture (loopback-only, veth future work)

## [0.2.0] — 2026-08-14

### Cgroup Freezer for Suspension

#### Added

- **`backend/container.py`** — cgroup v2 freezer integration for suspension (implementation_plan.md §4.1)
  - `suspend()` now freezes the container's **whole cgroup** via `cgroup.freeze` (write `1`, best-effort confirmation through `cgroup.events`' `frozen 1`) when attached to a v2 cgroup — descendants and future forks cannot outrun the suspension (SIGSTOP alone only stopped PID-1)
  - `resume()` thaws via `cgroup.freeze` (write `0`)
  - `terminate()` thaws a frozen container first so SIGTERM gets its graceful window (a frozen cgroup defers non-SIGKILL signals)
  - SIGSTOP/SIGCONT remains the fallback for v1 hosts (no unified freezer provisioned), failed cgroup setup, and failed freeze writes; a **failed thaw raises** instead — a frozen cgroup defers every signal except SIGKILL, so a SIGCONT fallback would report RUNNING for a process the kernel still holds frozen (the caller retries or escalates to `terminate()`, whose SIGKILL still applies)
  - `_freeze_control()` computes the control file (testable without touching `/sys/fs/cgroup`); `_wait_frozen()` confirms the freeze best-effort
- **`test_backend.py`** — `TestContainerFreezer` (12 tests): control-file decision (v2/v1/no-cgroup), freeze/thaw writes, the raise-on-thaw-failure contract, every fallback path, terminate-thaw ordering, and an end-to-end real-process signal suspend/resume; suite **205 → 217**

#### Changed

- `suspend`/`resume`/`terminate` use `signal.SIGSTOP`/`SIGCONT`/`SIGTERM`/`SIGKILL` constants instead of numeric literals

## [0.1.0] — 2026-07-15

### Initial Implementation

This release provides a complete, structurally-sound implementation of the NyHAL Linux Backend, implementing all five core requirements from NPS-017 §4. The implementation is **Experimental** and requires performance benchmarking and FUSE integration before conformance.

#### Added

##### Core Infrastructure
- **`backend/container.py`** — Container primitives (NPS-017 §4.1, NPS-010)
  - `Container` class with lifecycle state machine (CREATED → RUNNING → SUSPENDED → TERMINATED)
  - `ContainerManager` for managing multiple containers
  - `ContainerConfig` for container configuration
  - `ResourceLimits` for memory, CPU, and process limits
  - Namespace isolation (user, PID, mount, UTS, IPC)
  - Cgroups v2 support with v1 fallback
  - Process suspension/resumption via SIGSTOP/SIGCONT
  - Graceful shutdown with SIGTERM → SIGKILL escalation

- **`backend/capability.py`** — Capability enforcement (NPS-017 §4.2, NPS-011)
  - `Capability` enum with 23 capabilities from NPS-011 registry
  - `CapabilityManager` as sole arbiter of capability validity
  - Capability grant/revoke/validate operations
  - Capability attenuation per NPS-003 §5
  - Audit trail for all capability operations
  - Default capability set for new containers
  - Prevention of self-issued or forged capabilities

- **`ipc/core.py`** — IPC semantics (NPS-017 §4.3, NPS-003)
  - `IPCMessage` with payload, capabilities, and metadata
  - `IPCEndpoint` for receiving messages
  - `IPCManager` for routing and managing endpoints
  - Four primitives: `send`, `receive`, `call`, `notify`
  - Token-bucket rate limiting per ADR-0009
  - Capability transfer and attenuation
  - Synchronous call-reply pattern
  - Async message send
  - Lightweight notifications

- **`fuse/nyfs.py`** — Storage guarantees (NPS-017 §4.4, NPS-004, ADR-0016)
  - `NyFSFilesystem` core with inode management
  - `NyFSBlock` with compression and checksumming
  - Copy-on-Write (CoW) file/directory operations
  - Snapshots: create, restore, list
  - SHA256 checksumming for data integrity
  - Zstandard compression (with fallback if unavailable)
  - `NyFSMount` FUSE wrapper (structural placeholder)

- **`boot/lifecycle.py`** — Boot and lifecycle (NPS-017 §4.5, NPS-001 §5)
  - `BootSequence` with four-phase boot per NPS-001 §5
  - Phase 1: Hardware/Host Initialization
  - Phase 2: Trusted First Process
  - Phase 3: Service Bring-up
  - Phase 4: Usable Session
  - Milestone recording and audit trail
  - Signal handlers for graceful shutdown
  - Boot report generation

##### CLI and Tools
- **`nyrqis_backend.py`** — Command-line interface
  - `boot` command: Start the Nyrqis system
  - `container create/run` commands: Manage containers
  - `capability list/grant` commands: Manage capabilities
  - `ipc endpoint create` command: Create IPC endpoints
  - `filesystem create/snapshot` commands: Manage NyFS

##### Testing
- **`test_backend.py`** — Comprehensive test suite
  - 20 unit tests covering all five core requirements
  - Tests for container primitives and state machine
  - Tests for capability grant/revoke/validate
  - Tests for IPC send/receive/call/notify
  - Tests for storage write/read/snapshot
  - Tests for boot sequence phases
  - Conformance verification tests

##### Documentation
- **`IMPLEMENTATION_STATUS.md`** — Detailed implementation status
  - Requirement-by-requirement breakdown
  - Implementation status for each module
  - Outstanding work and deferred items
  - Conformance assessment
  - Next steps and roadmap

- **`README_IMPLEMENTATION.md`** — Implementation guide
  - Architecture overview
  - Quick start guide
  - Detailed module documentation with examples
  - File structure
  - CLI reference
  - Testing instructions
  - Conformance status
  - References to specifications

- **`requirements.txt`** — Python dependencies
  - zstandard (compression)
  - pytest (testing)
  - sphinx (documentation)

- **`docs/implementation_plan.md`** — Design and implementation plan
  - Overview of Nyrqis vision and principles
  - NyHAL backend requirements
  - Implementation strategy for each requirement
  - Key dependencies and challenges
  - High-level implementation roadmap

#### Changed

- Extended `source/nyhal-linux-backend/README.md` with status of implementation work

#### Notes

##### Architectural Decisions

1. **Container Primitives**: Uses `unshare(1)` for the PoC; production implementation should use direct `clone()`/`unshare()` syscalls for finer control.

2. **Capability Enforcement**: Capability registry and validation logic are complete; LSM/seccomp enforcement is deferred pending integration work.

3. **IPC Semantics**: All four primitives are implemented with token-bucket rate limiting; transport layer (Unix domain sockets or shared memory) is deferred.

4. **Storage Guarantees**: Core NyFS logic is complete; FUSE daemon integration is deferred pending pyfuse3 or fusepy integration.

5. **Boot and Lifecycle**: Four-phase boot sequence is implemented; systemd integration is deferred.

##### Conformance Status

Per NPS-017 §5.1, the Linux Backend is **NOT YET conformant** but provides all five core requirements in some form:

- ✓ Container Primitives: Fully implemented
- ⚠ Capability Enforcement: Registry complete; enforcement deferred
- ✓ IPC Semantics: Fully implemented
- ⚠ Storage Guarantees: Core logic; FUSE integration deferred
- ✓ Boot and Lifecycle: Fully implemented

##### Performance Benchmarks

The following benchmarks are required before conformance:
- IPC Round-trip Latency: < 100µs (NPS-003 §6.1)
- FUSE I/O Overhead: < 20% (ADR-0016)
- Token-Bucket Parameters: TBD (ADR-0009)
- Compression Ratio: > 30% (ADR-0007)

See `tests/BENCHMARK_PLAN.md` for methodology.

##### Next Steps

**Immediate (Phase 1):**
- Refactor container primitives to use direct syscalls
- ~~Implement cgroup freezer for suspension~~ — landed 2026-08-14
- ~~Add network namespace support~~ — landed 2026-08-14
- Run IPC latency benchmarks

**Short-term (Phase 2):**
- Integrate pyfuse3 or fusepy for FUSE daemon
- Implement FUSE operation handlers
- Test CoW and snapshot functionality
- Benchmark FUSE overhead

**Medium-term (Phase 3):**
- Research and integrate LSM (AppArmor or SELinux)
- Implement seccomp-bpf profile generation
- Map capabilities to syscalls
- Test enforcement with real containers

**Long-term (Phase 4):**
- Systemd integration
- Persistent state management
- Health checks and recovery
- Performance optimization
- Full conformance assessment

---

## Revision History

| Version | Date       | Status      | Notes |
|---------|------------|-------------|-------|
| 0.1.0   | 2026-07-15 | In Progress | Initial implementation complete |

---

## References

### Nyrqis Specifications
- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- NPS-001: Kernel Architecture and Boot (NyKernel Backend)
- NPS-010: Container Runtime
- NPS-011: Capability Registry
- NPS-003: Inter-Process Communication and Capability Passing
- NPS-004: NyFS Filesystem Core

### Architecture Decision Records
- ADR-0012: Adopt NyHAL as a pluggable kernel abstraction layer
- ADR-0016: NyFS Linux Backend implemented as a user-space FUSE filesystem
- ADR-0009: Per-container token-bucket rate limiting for IPC
- ADR-0007: Adopt Zstandard as the default compression codec
- ADR-0006: Adopt a hybrid microkernel as the Nyrqis kernel base

### Other Resources
- NTM-000: The Nyrqis Manifest
- tests/BENCHMARK_PLAN.md: Benchmarking methodology
- REPOSITORY_STATE.md: Project status tracking

---

**End of Document**
