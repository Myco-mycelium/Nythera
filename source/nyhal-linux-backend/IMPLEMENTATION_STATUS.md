---
title: Nyrqis Linux Backend Implementation Status
document_id: IMPL-001
version: 0.3.0
status: In Progress
classification: Technical
created: 2026-07-15
updated: 2026-08-12
ai_assisted: true
---

# Nyrqis Linux Backend Implementation Status

## Overview

This document tracks the implementation status of the Nyrqis Linux Backend, which implements the NyHAL (Nyrqis Kernel Abstraction Layer) contract on standard Linux systems. The implementation is guided by NPS-017 §4 (Backend Requirements) and aims to provide a conformant backend per NPS-017 §5.

## Implementation Scope

The Linux Backend must implement five core requirements to be conformant (NPS-017 §4):

| Requirement | Module | Status | Notes |
|-------------|--------|--------|-------|
| Container Primitives | `backend/container.py`, `backend/launcher.py` | ✓ Implemented | Process/namespace isolation, cgroups v1/v2, shell-free launcher, cgroup v1 hardening |
| Capability Enforcement | `backend/capability.py`, `backend/seccomp.py`, `backend/launcher.py` | ✓ Implemented | Registry + data-plane seccomp-BPF enforcement in-container (FIND-BACKEND-002) |
| IPC Semantics | `ipc/core.py` | ✓ Implemented | send/receive/call/notify primitives, receive-side capability check, token-bucket rate limiting |
| Storage Guarantees | `fuse/nyfs.py` | ✓ Implemented | NyFS core, per-block CoW (fixed 64 KiB blocks), snapshots, checksumming, compression, **durability (save/load with atomic metadata + block files, NPS-004 §7)**, FUSE operations + fusepy wiring (ADR-0016) |
| Boot and Lifecycle | `boot/lifecycle.py` | ✓ Implemented | Four-phase boot per NPS-001 §5, transition validation (FIND-BOOT-002), Secure Boot reporting (FIND-BOOT-001) |

Test suite: **94/94 passing** (`python3 test_backend.py`), including end-to-end container launches and a live FUSE mount verified on this host.

## Detailed Implementation Status

### 1. Container Primitives (NPS-017 §4.1, NPS-010)

**Files:** `backend/container.py`, `backend/launcher.py`

**Implemented Features:**
- ✓ `Container` class with lifecycle state machine (CREATED → RUNNING → SUSPENDED → TERMINATED)
- ✓ `ContainerConfig` with resource limits (memory, PID count, CPU shares)
- ✓ `ContainerManager` for managing multiple containers
- ✓ Cgroups v2 support with v1 fallback and a `require_cgroups_v2` hard-require option
- ✓ Process suspension/resumption via SIGSTOP/SIGCONT
- ✓ Graceful shutdown with SIGTERM → SIGKILL escalation
- ✓ Namespace isolation (user, PID, mount, UTS, IPC) via `unshare(1)` with the backend's real command exec'd through the in-namespace launcher
- ✓ **Real PID tracking** of the container's root process (the container is launched directly, not via a shell or background job)
- ✓ **Shell-free hostname setting** — `sethostname(2)` via `ctypes` inside the new UTS namespace; container-supplied hostnames are argv entries, never interpolated into a shell string (closes threat-model finding **FIND-BACKEND-004**, NPS-022 §4)
- ✓ **Cgroup v1 `release_agent` hardening** — the backend writes `notify_on_release=0` on the container's v1 cgroups and the launcher best-effort unmounts any cgroup filesystems leaking into the mount namespace (closes **FIND-BACKEND-003**, NPS-022 §4 / NPS-017 §4.1)
- ✓ Per-container seccomp policy files written with `0600` permissions and cleaned up after launch

**Outstanding Work:**
- [ ] Direct `clone()`/`unshare()` syscall wrappers (currently uses `unshare(1)`)
- [ ] Cgroup freezer integration for suspension
- [ ] Network namespace support
- [ ] Benchmark IPC round-trip latency (NPS-003 §6.1)

**Conformance Status:** Partial (NPS-010 state machine implemented; some performance optimizations deferred)

### 2. Capability Enforcement (NPS-017 §4.2, NPS-011)

**Files:** `backend/capability.py`, `backend/seccomp.py`, `backend/launcher.py`

**Implemented Features:**
- ✓ `Capability` enum with capabilities from NPS-011 (core, graphics, AI, Android). `CAP-MEDIA-LIBRARY` split into `CAP_MEDIA_IMAGES`/`CAP_MEDIA_VIDEO`/`CAP_MEDIA_AUDIO` per threat-model finding **FIND-CAPABILITY-004** (NPS-021 §4)
- ✓ `CapabilityManager` as sole arbiter of capability validity
- ✓ Capability grant/revoke/validate operations
- ✓ Capability attenuation per NPS-003 §5
- ✓ Audit trail for all capability operations
- ✓ Default capability set for new containers (includes `CAP_IPC_SEND`/`CAP_IPC_RECEIVE`)
- ✓ Prevention of self-issued or forged capabilities
- ✓ **Seccomp-BPF data-plane enforcement** (`backend/seccomp.py` + `backend/launcher.py`): a container's granted capability set is compiled into a classic-BPF filter (whole-syscall denies for always-dangerous and capability-gated syscalls; flag-gated denies for `openat`/`open` write intent) and installed via `prctl(PR_SET_NO_NEW_PRIVS)` + `PR_SET_SECCOMP` **inside the container's own execution context** before its command runs — closing threat-model finding **FIND-BACKEND-002** (NPS-022 §4), the most severe finding to date- ✓ BPF policy compiler with jump-fixup validation and a pure-Python BPF
  **simulator** so policies are proven by tests without touching the kernel
- ✓ **Default-deny allowlist posture** (`build_allowlist_policy` + the
  launcher's `--default-deny` flag): an opt-in filter whose default
  action is `EPERM` — only a runtime baseline (empirically verified on
  x86_64 by running `/bin/echo`, `/bin/ls`, `/bin/sh`, and CPython under
  the filter) plus capability-granted families and read-only
  `openat`/`open` are allowed. Unknown syscalls, including ones added to
  the kernel after compilation, are refused. Syscall numbers are
  transcribed from the kernel's own tables (`syscall_64.tbl`,
  `asm-generic/unistd.h`), not from memory; the arm64 baseline is a
  conservative subset pending verification on real arm64 hardware.
- ✓ `--strict-seccomp` launcher mode: refuse to run a container whose filter could not be installed (for hosts where enforcement is mandatory)
- ✓ CLI: `container run --capabilities ... --no-seccomp --default-deny`

**Verified end-to-end on this host:** a read-only container's `open(...O_WRONLY)` attempt fails with `Operation not permitted` at the syscall level; `mount(2)` is denied even for fully-granted containers — in both postures.

**Known residual gap (recorded honestly, not half-enforced):** `openat2` cannot be flag-filtered from classic BPF — its flags live inside a `struct open_how` behind a pointer, which cBPF cannot dereference (masking the pointer value is nondeterministic and is deliberately not done). A write-capable `openat2` in a read-only container is therefore not blocked by this layer; the `openat`/`open` write-intent rules cover the common path and the control plane still mediates backend API operations. In default-deny mode `openat2` is allowed outright for the same reason (denying it wholesale breaks glibc, which hard-fails rather than falling back to `openat`).

**Outstanding Work:**
- [ ] LSM policy generation (AppArmor/SELinux) as a second data-plane mechanism
- [ ] Verify the default-deny baseline on real arm64 hardware (the current arm64 numbers are a conservative subset of the kernel tables)
- [ ] Runtime policy reload for capability revocation of running containers

**Conformance Status:** Partial (registry complete; data-plane enforcement via seccomp implemented and verified; default-deny posture and LSM deferred)

### 3. IPC Semantics (NPS-017 §4.3, NPS-003)

**File:** `ipc/core.py`

**Implemented Features:**
- ✓ `IPCMessage` with payload, capabilities, and metadata
- ✓ `IPCEndpoint` for receiving messages
- ✓ `IPCManager` for routing and managing endpoints
- ✓ Four primitives: `send`, `receive`, `call`, `notify`
- ✓ Token-bucket rate limiting per ADR-0009
- ✓ Capability transfer and attenuation
- ✓ Synchronous call-reply pattern
- ✓ Async message send
- ✓ Lightweight notifications
- ✓ **Receive-side capability check** — a message is delivered only to a receiver that holds `CAP_IPC_RECEIVE`, closing the control-plane side of the enforcement gap

**Outstanding Work:**
- [ ] Benchmark IPC latency (NPS-003 §6.1)
- [ ] Optimize token-bucket parameters (ADR-0009 tuning-blocked)
- [ ] Unix domain socket or shared-memory transport layer
- [ ] Integration with seccomp for IPC syscall filtering

**Conformance Status:** Partial (primitives implemented; transport and performance benchmarking deferred)

### 4. Storage Guarantees (NPS-017 §4.4, NPS-004, ADR-0016)

**File:** `fuse/nyfs.py`

**Implemented Features:**
- ✓ `NyFSFilesystem` core with inode management and a **path-based API** (`resolve`/`resolve_parent`, real parent/child tree linking, mkdir/mknod/unlink/rmdir/rename)
- ✓ `NyFSBlock` with compression and checksumming
- ✓ **Per-block Copy-on-Write (CoW)** (2026-08-12): file content is stored as fixed-size blocks (`block_size`, 64 KiB default; configurable per filesystem instance). A write rebuilds only the blocks it overlaps — untouched blocks are carried over by reference — so per-write compress cost is bounded by bytes written, not file size. This replaces the earlier single-block-per-file implementation, whose whole-file recompress on every write was the dominant cost in the first-pass NyFS benchmark (40.5 vs 884 MB/s write). Reads are block-aware (only overlapping blocks decompressed) and verify each block's checksum on every read (NPS-004 §4.3); truncation preserves leading blocks and only rewrites the boundary block; past-EOF writes zero-fill gap blocks and expose no trailing padding.
- ✓ **Durability** (2026-08-12, NPS-004 §7): `save()` persists the filesystem to `state/metadata.json` (inode tree + block refs + snapshots) and `state/blocks/` (one immutable file per block, written temp + fsync + rename). Blocks are immutable, so files already on disk are skipped (re-save is idempotent) and only new blocks are written; both containing directories are fsynced, so the commit point (the atomic metadata swap — write temp + fsync + rename) is durable on real hardware. A crash at any point leaves either the old or the new consistent state — never a mixed one. An opt-in `save(batched_fsync=True)` groups the per-block work (write all temps, fsync all, rename all) with the same crash-atomicity guarantee — kept as an option, but benchmarked as no measurable win on a single disk (§8). `load()` reconstructs a filesystem (missing/corrupt metadata raises rather than fabricating state; tampered block files are caught by the per-read checksum). `gc_blocks()` reclaims block files orphaned by CoW plus stale temp files. Save is explicit (the durability contract; a mounted daemon calls it at transaction boundaries via the FUSE `fsync` handler) — no implicit save on teardown, so a crash never surprises by writing state the caller didn't commit.
- ✓ Snapshots: create, restore, list — restore rebinds the root inode so path lookups reach the restored tree (snapshot immutability verified by test)
- ✓ **Snapshot diffing** (2026-08-12): `diff_snapshots(a, b)` and `diff_live(snap)` list added/removed/modified entries with before/after sizes. Content comparison uses per-block checksums (no decompression), so identical content is never reported as modified even across different writes
- ✓ SHA256 checksumming for data integrity
- ✓ Zstandard compression (with fallback if unavailable)
- ✓ **FUSE operation handlers** (`NyFSOperations`): getattr, readdir, open/release, read, write, truncate, mkdir, mknod, unlink, rmdir, rename, statfs — pure Python, testable without a kernel mount
- ✓ **Real FUSE mount wiring** (`NyFSMount`): loads `fusepy` by file path from site-packages (this package is itself named `fuse`, which would otherwise shadow the third-party module), and mounts via fusepy's `FUSE` class with a callable operations adapter (fusepy dispatches `operations(op, path, *args)` and probes handlers with `getattr`; `FUSE.__init__` runs the event loop — there is no `main()` — so non-blocking mounts run it in a daemon thread). Mount options (`max_write`, etc.) forward to fusepy via `mount(**kwargs)`. Best-effort unmount via `fusermount -u`. Graceful, honestly-reported deferral when `fusepy` or `/dev/fuse` is unavailable

**Outstanding Work:**
- [x] ~~Address the live-mount write-batching finding~~ — **fixed 2026-08-12** by negotiating `FUSE_CAP_BIG_WRITES` + `FUSE_CAP_WRITEBACK_CACHE` + `FUSE_CAP_MAX_PAGES` in the INIT handshake (`NyFSMount(writeback_cache=True)`, the default): writes now batch at 128 KiB and stream at ~40–46 MB/s (~25× the 4 KiB-batched baseline); correctness under writeback caching verified by a seeded random-overwrite test through the mount (see `tests/BENCHMARK_RESULTS.md` §6)
- [ ] Kernel-module fallback (deferred per ADR-0016)
- [ ] Overlay filesystem for container-specific views
- [ ] Deduplication across snapshots

**Conformance Status:** Partial (core logic + FUSE operations implemented; live mount requires host `fusepy` + `/dev/fuse` — present on this host, and live-mount (§6) + persisted-image (§7) benchmarks were recorded 2026-08-12, see `tests/BENCHMARK_RESULTS.md`)

### 5. Boot and Lifecycle (NPS-017 §4.5, NPS-001 §5)

**File:** `boot/lifecycle.py`

**Implemented Features:**
- ✓ `BootSequence` with four-phase boot per NPS-001 §5
- ✓ Phase 1: Hardware/Host Initialization (kernel feature detection, manager init)
- ✓ Phase 2: Trusted First Process (init container creation)
- ✓ Phase 3: Service Bring-up (NyFS, IPC, capability systems)
- ✓ Phase 4: Usable Session (system ready for containers)
- ✓ Milestone recording and audit trail
- ✓ Signal handlers for graceful shutdown (SIGTERM, SIGINT)
- ✓ Boot report generation
- ✓ **Legal transition validation** — out-of-order phase transitions are rejected with `ValueError`; `restart()` resets to UNINITIALIZED (closes **FIND-BOOT-002**, NPS-001 §5 / NPS-023 §4)
- ✓ **Secure Boot status reporting** — probes `efivars` (`SecureBoot` variable, attribute-prefixed parse) and `mokutil`; reports enabled/disabled/unknown honestly (closes **FIND-BOOT-001**, NPS-023 §4 / NPS-017 §4.5)
- ✓ CLI: `secure-boot-status`

**Outstanding Work:**
- [ ] Systemd unit file for backend service
- [ ] Persistent state management
- [ ] Health checks and recovery
- [ ] Logging to syslog
- [ ] Measured-boot/TPM attestation story (`FIND-BOOT-003` — governance-level, needs a concrete need before design)

**Conformance Status:** Partial (boot sequence implemented; systemd integration deferred)

## Conformance Assessment

Per NPS-017 §5.1:

> A backend **MUST NOT** be presented as Nyrqis-conformant unless it satisfies §4 in full; partial conformance **MUST** be documented as such.

**Current Status:** **Experimental Backend — Core Implementation Complete, Performance/Integration Work Pending**

The Linux Backend implementation provides:
- ✓ All five core requirements from NPS-017 §4 (in some form)
- ✓ Data-plane capability enforcement (seccomp-BPF, installed in-container) — the highest-priority finding from the threat model is now closed and verified end-to-end
- ✓ Real FUSE operation handlers with `fusepy` wiring per ADR-0016
- ✓ Boot transition validation and Secure Boot status reporting
- ✓ Clear delineation of implemented vs. deferred work

The implementation is **NOT YET conformant** because:
- The default-deny allowlist posture is opt-in and its baseline is verified on x86_64 only; the default posture remains default-allow with explicit denies
- `openat2` write-intent is not data-plane filtered (cBPF cannot inspect flags behind a pointer) — documented residual gap
- LSM (AppArmor/SELinux) enforcement is not integrated
- The FUSE mount requires `fusepy` + `/dev/fuse` on the host (present here — live mount verified 2026-08-12 — but not guaranteed everywhere)
- Some optimizations (direct syscalls, network namespaces) are deferred

## Next Steps

### Immediate (Phase 1: Core Container Primitives)
1. Refactor container primitives to use direct `clone()`/`unshare()` syscalls
2. Implement cgroup freezer for suspension
3. Add network namespace support
4. Run IPC latency benchmarks (NPS-003 §6.1)

### Short-term (Phase 2: NyFS FUSE Backend)
1. ~~Benchmark FUSE overhead (ADR-0016)~~ — **first-pass live-mount data
   collected 2026-08-12** (`tests/BENCHMARK_RESULTS.md` §6); the
   write-batching finding was **fixed 2026-08-12** by negotiating
   writeback caching in the INIT handshake (~25× streaming-write
   improvement, `tests/BENCHMARK_RESULTS.md` §6); the remaining
   small-write cost (per-call block compress + checksum) and the 64 KiB
   block-size tradeoff stay open questions
2. ~~Test CoW and snapshot functionality through a live mount~~ — **done
   2026-08-12**: `TestNyFSLiveMount` in `test_backend.py` verifies
   fsync→save durability, snapshot round-trip, and re-mount through the
   real kernel path
3. Add overlay filesystem for container-specific views

### Medium-term (Phase 3: Capability Enforcement Hardening)
1. Make the default-deny allowlist posture the default (currently opt-in; x86_64 baseline verified, arm64 pending)
2. Research and integrate LSM (AppArmor or SELinux) as a second mechanism
3. Address the documented `openat2` residual gap (eBPF filter with pointer-safe accessors)
4. Runtime policy reload for capability revocation

### Long-term (Phase 4: Production Readiness)
1. Systemd integration
2. Persistent state management
3. Health checks and recovery
4. Performance optimization
5. Full conformance assessment

## Testing and Benchmarking

The following benchmarks are required before moving from `Experimental` to `Accepted` status:

| Benchmark | Target | Status | Notes |
|-----------|--------|--------|-------|
| IPC Round-trip Latency | < 100µs | First-pass data collected | In-process control plane: p50 92µs / p95 157µs / p99 213µs (2026-08-12). Real transport + load variants pending — target not yet judged |
| FUSE I/O Overhead | < 20% | Proxy + live-mount first-pass data | **§5 (ops-layer, per-block CoW):** 1 MiB-chunk streaming write ~162 MB/s (~4× the old whole-file 40.5 MB/s); 4 KiB-op pattern dominated by per-call block compress + per-read SHA-256 verification (~3.6 MB/s write / ~2.8 MB/s read vs 541–771 / 1,064–2,131 native). **§6 (live mount, 2026-08-12):** real kernel mount verified end-to-end; the kernel's 4 KiB write-batching limit was **fixed by INIT-handshake negotiation** (writeback_cache=True) — writes batch at 128 KiB and stream at ~40–46 MB/s (~25×), reads ~26–46 MB/s. No gate declared met |
| Token-Bucket Parameters | TBD | First-pass data collected | Default bucket caps a client→endpoint call path at ~50 calls/s steady state — ADR-0009 defaults need revisiting; sweep + adversarial test pending |
| Compression Ratio | > 30% | Pending | ADR-0007 |

See `tests/BENCHMARK_PLAN.md` for methodology and `tests/BENCHMARK_RESULTS.md` for the first-pass measurements.

## References

- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- NPS-001: Kernel Architecture and Boot (NyKernel Backend)
- NPS-010: Container Runtime
- NPS-011: Capability Registry
- NPS-003: Inter-Process Communication and Capability Passing
- NPS-004: NyFS Filesystem Core
- NPS-022: Container Escape Analysis and Runtime Isolation (FIND-BACKEND-002/003/004)
- NPS-023: Secure Boot Threat Model (FIND-BOOT-001/002)
- ADR-0012: Adopt NyHAL as a pluggable kernel abstraction layer
- ADR-0016: NyFS Linux Backend implemented as a user-space FUSE filesystem
- ADR-0009: Per-container token-bucket rate limiting for IPC
- ADR-0007: Adopt Zstandard as the default compression codec

---

**End of Document**
