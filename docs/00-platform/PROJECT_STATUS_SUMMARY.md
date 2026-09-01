# Nyrqis Linux Backend — Project Status Summary

*As of 2026-09-01*

## Executive Summary

The Nyrqis Linux Backend is a **substantially complete** reference
implementation of the NyHAL (Nyrqis Kernel Abstraction Layer) contract
on standard Linux. All five core NPS-017 §4 requirements are
implemented and verified with **2,500 passing tests**.

The backend is production-grade for container management, IPC, and
storage. The remaining work is the runtime integration (display server
connection, actual app compatibility runtimes) which are large
architectural tasks beyond the backend's scope.

## What's Built

### Core Requirements (all Implemented)

| Requirement | What it does | Key features |
|-------------|-------------|--------------|
| **Container Primitives** | Process/namespace isolation | Direct `unshare(2)`/`clone(2)` launch, cgroups v1/v2, PID-1 launcher-init (compiled Rust binary), network namespaces, cgroup freezer, resource profiling, batch operations |
| **Capability Enforcement** | Data-plane security | Seccomp-BPF (default-deny allowlist), LSM (AppArmor/SELinux), runtime policy reload, 66 capability types |
| **IPC Semantics** | Inter-process communication | Unix-domain datagram + shared-memory transports, SCM_CREDENTIALS auth, Rust serving loop (82–95 µs p50), wire-level streaming |
| **Storage Guarantees** | Durable filesystem | NyFS with per-block CoW, snapshots, compression (Zstandard), checksumming, FUSE operations, journal commits (~60× faster) |
| **Boot and Lifecycle** | Daemon management | 4-phase boot, systemd integration, health checks, persistent state, graceful shutdown |

### Rust Crates (ADR-0020)

All 9 Rust crates are implemented, CI-verified, and have byte-identical
conformance gates:

| Crate | Purpose |
|-------|---------|
| `rust/seccomp` | BPF policy compiler |
| `rust/syscalls` | Linux syscall wrappers + clone/launch_child |
| `rust/nyfs` | SHA-256 + Zstandard block codec |
| `rust/ipc` | Binary wire framing |
| `rust/container` | Launch-plan primitives |
| `rust/transport` | Unix-domain datagram hot path |
| `rust/ipcd` | IPC serving loop (ADR-0021) |
| `rust/launcher` | Compiled PID-1 init binary |
| `rust/keys` | Envelope encryption (ADR-0023) |

### NyVault Storage Service (ADR-0022)

- Volume lifecycle (create/open/close/delete)
- Byte I/O with per-block CoW
- Snapshots and restore
- Path-scoped grants (cross-container sharing)
- Per-container quotas with EDQUOT enforcement
- Wire-level streaming (ADR-0024) — 5.6× faster writes
- **At-rest encryption** (ADR-0023) — XChaCha20-Poly1305 per block
- KEK rotation without re-encryption
- FUSE passthrough for kernel-visible mounts

### NUI Runtime (ADR-0025)

- Expression language (Python + Rust mirror, byte-identical)
- Document model (55K lines, 66 component types)
- Import gate with differential conformance testing
- PIL + SDL2 compositors
- Rust/C++/Python code generators
- Runtime with state management, event dispatch, bindings
- 6 reference shell screens (desktop, security center, vault, widgets, windows, shell draft)

### Operator CLI (`nyrqisctl.py`)

Full control-plane client with 500+ subcommands covering:
- Container management (run/list/kill/stats/logs/exec)
- Vault operations (create/open/write/read/snapshot/grant/quota)
- NUI operations (validate/load/current)
- App management (install/list/launch/terminate)
- Monitoring, alerting, cost tracking, and more

## Performance Gates

| Gate | Target | Result | Status |
|------|--------|--------|--------|
| IPC latency (NPS-003 §6.1) | <100 µs p50 | 82–95 µs p50 (Rust loop) | **MET** |
| Container cold-start | — | 6–54 ms (compiled) vs 152–157 ms (Python) | ~3× faster |
| NyFS journal commit | — | ~60–70× faster than fsync-per-block | Implemented |
| FUSE I/O overhead | <20% | ~40–46 MB/s streaming writes | Partial data |

## Test Coverage

- **2,500 tests** — all passing
- **300 test classes** covering containers, IPC, storage, seccomp, NUI, CLI
- **7 cross-architecture conformance tests** for aarch64 syscall tables
- **CI jobs**: 20+ GitHub Actions jobs (Rust builds, conformance gates, Python tests, benchmarks)

## Security Posture

- **7 threat model phases complete** (29 findings, most resolved)
- **Seccomp-BPF enforcement** with default-deny allowlist
- **LSM integration** (AppArmor/SELinux)
- **At-rest encryption** with envelope encryption (Argon2id + XChaCha20-Poly1305)
- **KEK custody** in Rust crate (plaintext never crosses FFI)
- **Capability-gated** every operation

## What's Not Done

| Item | Why it's open | Priority |
|------|--------------|----------|
| arm64 baseline verification | Needs real aarch64 hardware | Medium (CI tests provide confidence) |
| `openat2` flag filtering | Classic BPF can't inspect pointer args; needs eBPF | Low (openat/open rules cover common path) |
| TPM attestation | Governance-level, no concrete need yet | Low |
| FUSE vs kernel-module decision | Needs more performance data | Low (FUSE works for current workloads) |
| Display server integration | Wayland/X11 connection for real shell rendering | High (next major milestone) |
| App compat runtimes (Wine/ART) | Run inside containers, not on host | High (next major milestone) |

## Documentation

- **115 markdown files** across Diátaxis framework
- **4 tutorials** (repository tour, manifest authoring, backend quickstart, this status)
- **7 how-to guides** (vault operations, running tests, adding capabilities, etc.)
- **64 reference documents** (NPS, ADRs, ABI, API, security)
- **14 explanation documents** (architecture rationale, design decisions)
- **6 diagrams** (boot sequence, capability flow, container lifecycle, etc.)
