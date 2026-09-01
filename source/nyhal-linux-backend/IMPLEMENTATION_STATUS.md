---
title: Nyrqis Linux Backend Implementation Status
document_id: IMPL-001
version: 0.22.0
status: In Progress
classification: Technical
created: 2026-07-15
updated: 2026-08-15
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
| IPC Semantics | `ipc/core.py`, `ipc/transport.py`, `ipc/transport_codec.py`, `ipc/registry.py`, `ipc/service.py` | ✓ Implemented | send/receive/call/notify primitives, receive-side capability check, token-bucket rate limiting, **Unix-domain datagram transport** (receiver-side `SO_PASSCRED` — the kernel attaches the real `SCM_CREDENTIALS` to every inbound datagram and the sender attaches nothing, so identity is unforgeable and container-safe; wire-codec framing), **verified container→service end-to-end with an auto-maintained sender registry** (the backend registers each direct-syscall container's pid at spawn and drops it on terminate — no manual bookkeeping), **Rust transport hot path (ADR-0020 migration #6)** — sendto/recvmsg + SCM_CREDENTIALS in Rust when the crate is built, **first real service on the transport** (`BackendStatusService`: `ping` whole-chain check; `status` capability-gated on `CAP_SYSTEM_INFO` — denied fail-closed without a `CapabilityManager`; a handler bug becomes an `internal error` reply, never kills the serving thread), **control plane** (operator-only `ControlService` behind the server's trusted-uid operator path; `ServiceRouter` dispatches services on one socket) |
| Storage Guarantees | `fuse/nyfs.py` | ✓ Implemented | NyFS core, per-block CoW (fixed 64 KiB blocks), snapshots, checksumming, compression, **durability (save/load with atomic metadata + block files, NPS-004 §7)**, FUSE operations + fusepy wiring (ADR-0016) |
| Boot and Lifecycle | `boot/lifecycle.py` | ✓ Implemented | Four-phase boot per NPS-001 §5, transition validation (FIND-BOOT-002), Secure Boot reporting (FIND-BOOT-001) |

Test suite: **2548/2548 passing** (2500 backend + 31 package signing + 17 installer tests — all green locally; Rust-crate conformance classes skip on crate-less hosts but all run in CI; 7 cross-architecture conformance tests validate aarch64 syscall tables). All five NPS-017 §4 requirements are implemented and verified end-to-end.

### Rust crate migrations (ADR-0020, all Implemented and CI-verified)

| # | Crate | ABI | Purpose | Gate |
|---|-------|-----|---------|------|
| 1 | `rust/seccomp` | 1.0.0 | BPF policy compiler (byte-identical to Python floor) | `rust-seccomp-conformance` |
| 2 | `rust/syscalls` | 1.2.0 | sethostname/prctl/unshare/mount/clone/launch_child | `rust-syscalls-conformance` |
| 3 | `rust/nyfs` | 1.0.0 | SHA-256 + Zstandard block codec | `rust-nyfs-conformance` |
| 4 | `rust/ipc` | 1.0.0 | Binary wire framing | `rust-ipc-conformance` |
| 5 | `rust/container` | 1.0.0 | Launch-plan primitives + state machine | `rust-container-conformance` |
| 6 | `rust/transport` | 2.0.0 | Unix-domain datagram hot path (sendto/recvmsg) | `rust-transport-conformance` |
| 7 | `rust/ipcd` | 1.0.0 | IPC serving loop (ADR-0021, Accepted) | `rust-ipcd-conformance` |
| 8 | `rust/launcher` | — | Compiled PID-1 init binary | `rust-launcher-conformance` |
| 9 | `rust/keys` | 1.0.0 | Envelope encryption (ADR-0023) | `rust-keys-conformance` |

### Key performance gates

| Gate | Target | Result | Status |
|------|--------|--------|--------|
| NPS-003 §6.1 IPC latency | <100 µs p50 | 82–95 µs p50 (Rust loop) | **MET** |
| Container cold-start | — | 6.3–53.7 ms (compiled) vs 152–157 ms (Python) | ~3× faster |
| NyFS journal commit | — | ~60–70× faster than fsync-per-block | Implemented |

### Major features implemented

- **ADR-0021** (Rust IPC serving loop): full dispatch cycle in Rust, batched FFI, health socket, both main and health sockets behind the loop
- **ADR-0022** (NyVault storage service): volume lifecycle, byte I/O, snapshots, FUSE passthrough, path-scoped grants, per-container quotas, streaming (ADR-0024)
- **ADR-0023** (Key manager): envelope encryption (Argon2id + XChaCha20-Poly1305), KEK custody in Rust crate, per-volume DEKs, at-rest encryption, KEK rotation
- **ADR-0025** (NUI import gate): Python floor + Rust crate (byte-identical), expression language, animations, state scopes, reusable components
- **NUI visual layers**: PIL + SDL2 compositors, Rust/C++/Python code generators, runtime with state management
- **66 NUI component types** in the API registry (Shell, Data, Form, Media, Developer categories)
- **6 reference shell screens**: desktop, security center, vault workspace, widgets, windows, shell draft

## Detailed Implementation Status

### 1. Container Primitives (NPS-017 §4.1, NPS-010)

**Files:** `backend/container.py`, `backend/launcher.py`

**Implemented Features:**
- ✓ `Container` class with lifecycle state machine (CREATED → RUNNING → SUSPENDED → TERMINATED)
- ✓ `ContainerConfig` with resource limits (memory, PID count, CPU shares)
- ✓ `ContainerManager` for managing multiple containers
- ✓ Cgroups v2 support with v1 fallback and a `require_cgroups_v2` hard-require option
- ✓ Process suspension/resumption — **cgroup v2 freezer primary** (`cgroup.freeze` freezes the whole container cgroup; 2026-08-14), SIGSTOP/SIGCONT fallback everywhere else
- ✓ Graceful shutdown with SIGTERM → SIGKILL escalation
- ✓ Namespace isolation (user, PID, mount, UTS, IPC, and **network** when `network=True`) via **direct `unshare(2)` syscalls** (implementation_plan.md §4.1 — the default launch path; see `_spawn_direct`) or the legacy `unshare(1)` subprocess (`use_direct_syscalls=False`), with the backend's real command exec'd through the in-namespace launcher
- ✓ **Direct-syscall launcher** — the manager forks a namespace-setup child (the manager must not enter the namespaces itself) which performs `unshare(CLONE_NEWUSER)` + root uid/gid maps, `unshare(NEWNS|NEWUTS|NEWIPC)`, `unshare(CLONE_NEWPID)`, then forks the container's PID-1; PID-1 mounts a hardened procfs (nosuid/nodev/noexec, the `--mount-proc` equivalent) via the Rust `mount_proc` FFI and execs the launcher. The setup child relays the container PID through a pipe and exits with its status (or dies by its signal), so `wait()` keeps Popen-compatible semantics (exit code, or `-signum`)
- ✓ **Real PID tracking** of the container's root process (the container is launched directly, not via a shell or background job)
- ✓ **Shell-free hostname setting** — `sethostname(2)` via the Rust syscalls module (ctypes fallback) inside the new UTS namespace; container-supplied hostnames are argv entries, never interpolated into a shell string (closes threat-model finding **FIND-BACKEND-004**, NPS-022 §4; verified with the hostile hostname `evil; rm -rf /` on the direct path)
- ✓ **Cgroup v1 `release_agent` hardening** — the backend writes `notify_on_release=0` on the container's v1 cgroups and the launcher best-effort unmounts any cgroup filesystems leaking into the mount namespace (closes **FIND-BACKEND-003**, NPS-022 §4 / NPS-017 §4.1)
- ✓ Per-container seccomp policy files written with `0600` permissions and cleaned up after launch
- ✓ **Container resource profiling** — per-process CPU/memory/IO breakdown (`get_resource_profile` reads `/proc/[pid]/stat`, `/proc/[pid]/statm`, `/proc/[pid]/io` for each process in the container); history tracking (`get_resource_profile_history`) with bounded ring buffer; `get_resource_profile_top_consumers` ranks processes by a configurable resource (RSS, CPU time, I/O read/write). All wired through the control plane and CLI (`containers resource-profile`, `resource-profile-history`, `resource-profile-top`).
- ✓ **Batch container operations** — start, stop, or kill multiple containers at once via `_resolve_batch_targets` (AND-ed filters: label key-value pairs, name substring, lifecycle state, explicit IDs); `batch_start`/`batch_stop`/`batch_kill` return started/stopped/killed/skipped/failed lists. Wired through the control plane and CLI (`containers batch-start`, `batch-stop`, `batch-kill`).
- ✓ **Container process management** — `kill_process` sends a signal to a specific PID within a container (with ownership verification via the init's children list); `list_processes` wraps `container_top` with a dict response; `signal_all` sends a signal to all child processes. Wired through the control plane and CLI (`containers process-kill`, `process-list`, `process-signal-all`).
- ✓ **Snapshot scheduling** — automated periodic snapshots with rolling-window retention: `configure_snapshot_schedule` sets interval/max_snapshots/label_prefix; `run_scheduled_snapshot` performs a checkpoint and prunes old snapshots; `list_scheduled_snapshots` / `disable_snapshot_schedule` for management. Wired through the control plane and CLI (`containers snapshot-schedule-set`, `snapshot-schedule-get`, `snapshot-schedule-run`, `snapshot-schedule-list`, `snapshot-schedule-disable`).

**Outstanding Work:**
- [x] ~~veth/bridge outbound connectivity for `network=True` containers~~ — **Implemented**: `backend/network.py` provides veth-pair creation, bridge management, NAT/masquerade, and per-container IP allocation; wired into `ContainerManager.spawn()` and cleanup on terminate. Requires host CAP_NET_ADMIN/root at runtime.
- [x] ~~Benchmark IPC round-trip latency (NPS-003 §6.1)~~ — **Completed**: first-pass data in BENCHMARK_RESULTS.md §20–§25; Rust transport loop achieves p50 ~82–95 µs (NPS-003 §6.1 <100 µs gate MET).

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
- [x] ~~LSM policy generation (AppArmor/SELinux) as a second data-plane mechanism~~ — **Implemented**: `backend/lsm.py` generates both AppArmor and SELinux profiles from a container's capability set; wired into `ContainerManager.spawn()` and policy files cleaned up on terminate.
- [ ] Verify the default-deny baseline on real arm64 hardware (the current arm64 numbers are a conservative subset of the kernel tables)
- [x] ~~Runtime policy reload for capability revocation of running containers~~ — **Implemented**: `ContainerManager.reload_policy()` regenerates and re-applies LSM profiles after capability changes; `revoke_and_reload()` provides a convenience method for revoking + reloading in one call.

**Conformance Status:** Partial (registry complete; data-plane enforcement via seccomp implemented and verified; default-deny is now the default posture; LSM policy generation + runtime reload implemented and wired into container lifecycle; arm64 baseline verification pending)

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
- ✓ Lightweight notifications    - ✓ **Receive-side capability check** — a message is delivered only to a receiver that holds `CAP_IPC_RECEIVE`, closing the control-plane side of the enforcement gap
    - ✓ **Unix-domain datagram transport** (`ipc/transport.py`, 2026-08-14, plan §4.3): `IPCMessage`s move between processes over `AF_UNIX SOCK_DGRAM` sockets framed by the wire codec (ADR-0020 migration #4 — the framing its parser was built to protect). Sender identity is the kernel's `SCM_CREDENTIALS` (set `SO_PASSCRED`, parse the attached `ucred`): the backend maps the pid to a container, so a wire `sender_id` that does not match the authenticated process is dropped as a forgery, unknown pids are dropped, and senders lacking `CAP_IPC_SEND` are refused — all before delivery. `SO_PEERCRED` does NOT work on datagram sockets (returns `(0,-1,-1)`, verified), so `SCM_CREDENTIALS` is the mechanism. `CALL` carries the caller's reply path in `metadata['reply_path']`; the client correlates `REPLY`s by `reply_to`. Inbound enqueue stays token-bucket rate-limited (ADR-0009). Tested with a **real cross-process** exchange (`TestIPCTransport`): a subprocess client's pid is authenticated by the kernel at the server and the CALL/REPLY round-trips

**Outstanding Work:**
- [x] ~~Benchmark IPC latency (NPS-003 §6.1)~~ — **Completed**: measured in BENCHMARK_RESULTS.md §20–§25; Rust IPC loop p50 ~82–95 µs vs floor ~263–274 µs.
- [x] ~~Optimize token-bucket parameters (ADR-0009)~~ — **Implemented**: `IPCManager` now accepts configurable `default_bucket_size`/`default_tokens_per_second`; defaults raised to 200/500 to match measured Rust loop throughput; adversarial tests (refill accuracy, thread safety, sweep) added.
- [x] ~~Shared-memory transport as an alternative/complement to the Unix-domain datagram path~~ — **Implemented**: `ipc/shm_transport.py` provides a POSIX shared-memory ring-buffer transport (zero-copy, mutex+condition variable synchronization) as a high-performance alternative to Unix datagrams.
- [x] ~~Integration with seccomp for IPC syscall filtering~~ — **Implemented**: IPC-specific seccomp rules gate `sendto`/`sendmsg`/`recvmsg` on `CAP_IPC_SEND`/`CAP_IPC_RECEIVE` independently of `CAP_NETWORK_SOCKET`; containers with IPC capabilities but no network socket capability can still use the IPC transport.

**Conformance Status:** Partial (primitives implemented; Unix-domain datagram + shared-memory transports implemented; IPC syscall filtering via seccomp implemented; performance benchmarked — NPS-003 §6.1 <100 µs gate MET)

### 4. Storage Guarantees (NPS-017 §4.4, NPS-004, ADR-0016)

**File:** `fuse/nyfs.py`

**Implemented Features:**
- ✓ `NyFSFilesystem` core with inode management and a **path-based API** (`resolve`/`resolve_parent`, real parent/child tree linking, mkdir/mknod/unlink/rmdir/rename)
- ✓ `NyFSBlock` with compression and checksumming
- ✓ **Per-block Copy-on-Write (CoW)** (2026-08-12): file content is stored as fixed-size blocks (`block_size`, 64 KiB default; configurable per filesystem instance). A write rebuilds only the blocks it overlaps — untouched blocks are carried over by reference — so per-write compress cost is bounded by bytes written, not file size. This replaces the earlier single-block-per-file implementation, whose whole-file recompress on every write was the dominant cost in the first-pass NyFS benchmark (40.5 vs 884 MB/s write). Reads are block-aware (only overlapping blocks decompressed) and verify each block's checksum on every read (NPS-004 §4.3); truncation preserves leading blocks and only rewrites the boundary block; past-EOF writes zero-fill gap blocks and expose no trailing padding.
- ✓ **Durability** (2026-08-12, NPS-004 §7): `save()` persists the filesystem to `state/metadata.json` (inode tree + block refs + snapshots) and `state/blocks/` (one immutable file per block, written temp + fsync + rename). Blocks are immutable, so files already on disk are skipped (re-save is idempotent) and only new blocks are written; both containing directories are fsynced, so the commit point (the atomic metadata swap — write temp + fsync + rename) is durable on real hardware. A crash at any point leaves either the old or the new consistent state — never a mixed one. An opt-in `save(batched_fsync=True)` groups the per-block work (write all temps, fsync all, rename all) with the same crash-atomicity guarantee — kept as an option, but benchmarked as no measurable win on a single disk (§8). **`save()` defaults to the journal-style commit** (`use_journal=True`, 2026-08-12): new block payloads are appended to `state/journal.bin` and fsynced **once** per transaction before the atomic metadata swap (still the commit point); `load()` falls back to the journal for blocks without `.bin` files and ignores torn tails; the journal compacts (materialize referenced blocks, truncate) past `journal_compact_bytes` (default 64 MiB). Benchmark: **~60–70× faster commit** than fsync-per-block (0.20 s vs 11–15 s on the 17.1 MB corpus, §9; ~61× on a 3,855-file real corpus, §12) at ~0.3% on-disk overhead — the decisive commit-cost lever, correctness-tested (roundtrip, crash-mid-save, torn tail, compaction, no re-appends). Made the default on 2026-08-12 per implementer decision (full suite 103/103 green with the default flipped; the interleaved path stays available as `use_journal=False`); Architecture Group review is the formal governance step — **ADR-0019** is the review package. Compaction is exposed to daemons as `journal_bytes()` / `maybe_compact()` / `compact_journal()` (crash-safe: renames before truncate), and `NyFSMount(auto_compact=True)` runs a background watcher that trims the journal at half the save-time threshold during idle intervals, so a transaction is rarely the one that stalls on the materialize pass (compaction measured at ~27 ms/block — an interleaved save of referenced blocks; §14; mixed-loop commits ~3.7–4× faster under journal, §13). `load()` reconstructs a filesystem (missing/corrupt metadata raises rather than fabricating state; tampered block files are caught by the per-read checksum). `gc_blocks()` reclaims block files orphaned by CoW plus stale temp files. Save is explicit (the durability contract; a mounted daemon calls it at transaction boundaries via the FUSE `fsync` handler) — no implicit save on teardown, so a crash never surprises by writing state the caller didn't commit.
- ✓ Snapshots: create, restore, list — restore rebinds the root inode so path lookups reach the restored tree (snapshot immutability verified by test)
- ✓ **Snapshot diffing** (2026-08-12): `diff_snapshots(a, b)` and `diff_live(snap)` list added/removed/modified entries with before/after sizes. Content comparison uses per-block checksums (no decompression), so identical content is never reported as modified even across different writes
- ✓ SHA256 checksumming for data integrity
- ✓ Zstandard compression (with fallback if unavailable)
- ✓ **FUSE operation handlers** (`NyFSOperations`): getattr, readdir, open/release, read, write, truncate, mkdir, mknod, unlink, rmdir, rename, statfs — pure Python, testable without a kernel mount
- ✓ **Real FUSE mount wiring** (`NyFSMount`): loads `fusepy` by file path from site-packages (this package is itself named `fuse`, which would otherwise shadow the third-party module), and mounts via fusepy's `FUSE` class with a callable operations adapter (fusepy dispatches `operations(op, path, *args)` and probes handlers with `getattr`; `FUSE.__init__` runs the event loop — there is no `main()` — so non-blocking mounts run it in a daemon thread). Mount options (`max_write`, etc.) forward to fusepy via `mount(**kwargs)`. Best-effort unmount via `fusermount -u`. Graceful, honestly-reported deferral when `fusepy` or `/dev/fuse` is unavailable

**Outstanding Work:**
- [x] ~~Address the live-mount write-batching finding~~ — **fixed 2026-08-12** by negotiating `FUSE_CAP_BIG_WRITES` + `FUSE_CAP_WRITEBACK_CACHE` + `FUSE_CAP_MAX_PAGES` in the INIT handshake (`NyFSMount(writeback_cache=True)`, the default): writes now batch at 128 KiB and stream at ~40–46 MB/s (~25× the 4 KiB-batched baseline); correctness under writeback caching verified by a seeded random-overwrite test through the mount (see `tests/BENCHMARK_RESULTS.md` §6)
- [x] ~~Overlay filesystem for container-specific views~~ — **Implemented**: `fuse/overlay.py` provides per-container writable layers backed by NyFS base images with per-block dedup.
- [x] ~~Deduplication across snapshots~~ — **Implemented**: content-hash block dedup at both memory and disk level; identical blocks are stored once and referenced by hash. Save-level dedup ensures only one copy per identical block on disk.
- [ ] Kernel-module fallback (deferred per ADR-0016)

**Conformance Status:** Partial (core logic + FUSE operations + at-rest encryption (ADR-0023) + FUSE passthrough + live encrypted mount verified; live mount requires host `fusepy` + `/dev/fuse` — present on this host; kernel-module fallback deferred per ADR-0016)

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
- ✓ **Systemd host integration (plan §4.5)** — `packaging/systemd/nyrqis-backend.service` runs the backend daemon at boot (`nyrqis_backend.py service serve` on `/run/nyrqis/status.sock`): unprivileged by design (`DynamicUser` + `NoNewPrivileges` — the daemon launches containers through unprivileged user namespaces), `Restart=on-failure`, `PrivateTmp`/`ProtectHome`/`ProtectSystem`, install steps in `packaging/README.md`. New `TestSystemdUnit` (3 tests: unit wiring, `systemd-analyze verify` when systemd is present, unprivileged posture) — the class is hermetic (reads the unit file; no unit installed on the host).
- ✓ **Logging to syslog (plan §4.5)** — `setup_logging(verbose, syslog=True)` mirrors daemon records into the system journal via `/dev/log` (UDP-514 fallback; best-effort degrade to stderr). The systemd unit starts the daemon with `--syslog`, so `journalctl -u nyrqis-backend` is the operating interface. New `TestLoggingConfig` (3 tests: `/dev/log` attach, UDP fallback, graceful degrade).
- ✓ **Health checks (plan §4.5)** — the status service gains a `health` op (gated on `CAP_SYSTEM_INFO` like `status`): serve-loop liveness, container load (known/running), IPC registry size, state-persistence status, and the crash-recovery record — the payload a systemd `ExecStartPost`/operator health probe reads. New tests: `health` over a real socket, fail-closed denial without `CAP_SYSTEM_INFO`, and `state_persisted` reporting.
- ✓ **Persistent state management (plan §4.5)** — new `backend/daemon_state.py` (`DaemonStateFile`): a versioned, atomically-written (tmp + `os.replace`, the NyFS discipline) JSON record of the daemon identity (pid, version, socket) + last-known container manifest. The daemon recovers on start — a stale previous-daemon record is REPORTED (the orphan ids are logged; the `health` op returns a recovery *summary* — previous pid + orphan count; the full manifest stays in the state file for operator review), never resumed: orphaned processes are not auto-killed (NPS-010 §4 has no resume-from-pid transition). Mutating control ops (`container_run`/`container_kill`) refresh the manifest. The systemd unit persists to `/run/nyrqis/daemon-state.json` (the `RuntimeDirectory`). New `TestDaemonState` (11 tests: round-trip, atomicity under `os.replace` failure, corrupt/schema/pid-staleness handling, host recovery + persistence).

**Outstanding Work:**
- [ ] Measured-boot/TPM attestation story (`FIND-BOOT-003` — governance-level, needs a concrete need before design)

**Conformance Status:** Partial (boot sequence + host integration + persistent state/health checks/syslog landed 2026-08-14; the TPM attestation story remains open)

**2026-08-15 (0.14.5): NyVault at rest — KEK wiring, block AEAD, and the FUSE passthrough.**
The encrypted-vault lifecycle is complete (ADR-0023's core claim): `nyrqisctl vault init` writes the Argon2id-derived KEK envelope; the daemon serves with `--vault-key-file` + passphrase (unlock at serve time, fail-closed on a wrong secret); `volume_create` gives every volume its own random DEK wrapped with the KEK (`ad = volume id`); the **block layer is AEAD-encrypted** — `rust/keys` + the PyNaCl floor gain `block_encrypt`/`block_decrypt` (24-byte nonce, XChaCha20-Poly1305, checksum over ciphertext), and `NyFSFilesystem(dek=...)` threads the DEK through `_make_block`/`_decompress_verified`, so every block at rest is `nonce ‖ ciphertext ‖ tag`, verified on read (no plaintext anywhere under the vault dir — verified). `volume_delete` crypto-shreds (handles + wrapped DEK + backing image + registry entry), and the registry + wrapped DEKs **persist across a daemon restart**. **The NyVault FUSE passthrough LANDED (ADR-0022's data-plane mount):** `fuse/vault_mount.py` — `NyVaultOperations` are FUSE ops whose handlers are **storage-service CALLs** (getattr/readdir/read/write/mkdir/mknod/unlink/rmdir/rename/truncate/statfs/fsync), paging the 32 KiB per-call byte path, with errno propagation; `NyVaultMount` mirrors `NyFSMount` (honest deferral without fusepy); the service's generic file surface sits behind the same capability + handle + path gates; CLI `nyrqisctl vault mount <volume> <mountpoint>`. **§26 vault-io benchmark:** the durable `save()` commit dominates writes (~86 ms p50, one fsync per transaction), reads run at **1.6–2.8 ms p50** flat across payloads, and the block AEAD adds ~0.5 ms on 32 KiB reads. Suite 412 → **427**.

**2026-08-15 (0.14.6): KEK rotation + the encrypted vault VERIFIED through a real kernel FUSE mount + systemd vault wiring.** `volume_rekey` (OPERATOR-ONLY) rotates the KEK without re-encrypting any block — unwrap with the current KEK, re-wrap with the new one (derived daemon-side, held in the key handle table for the duration), persist; the reply carries the new envelope (its salt matches — the CLI writes it to `--new-key-file` and the operator restarts under it). Verified e2e: data reads back after restart under the NEW key; the OLD key file fails closed with an honest "vault key mismatch" (the generic handler now surfaces `StorageLockedError` instead of "internal error"). **The encrypted vault was mounted LIVE and verified (first live verification of the data-plane mount):** kernel write/fsync/read/mkdir/root-readdir/stat through `nyrqisctl vault mount` with **no plaintext under the vault dir**; the live attempt found and fixed two real bugs — `_check_path` rejected the volume ROOT (`/`, breaking `readdir("/")`/`getattr("/")`), and the CLI's background-thread mount died with the exiting process (the CLI now serves the FUSE loop in its foreground until unmounted). `volume_open` canonicalizes id-or-name resolution (the handle binds the real volume id). New `TestNyVaultLiveMount` (2, skip-gated). systemd unit: `StateDirectory=nyrqis` + `--vault-dir`/`--vault-key-file`/optional `EnvironmentFile` passphrase. Suite 427 → **432**.

**2026-08-15 (0.14.7): snapshot restore + the live encrypted-mount benchmark (§27) + the vault runbook.** `volume_restore` (snapshot table unchanged; the restored tree is what save() persists) + `nyrqisctl vault restore`, verified over the wire and through the live encrypted mount (kernel write → snapshot → kernel overwrite → restore; `NyVaultOperations` gains snapshot/restore/list_snapshots). **§27 (`--vault-mount-io`): the durable per-CALL commit is the entire write story** — 1 MiB ≈ 32 sequential CALLs ≈ 110 ms each → 0.28 MB/s vs native ~1,700 MB/s; reads ~2.1 MB/s. The benchmark exposed a real bug: the passthrough adapter never registered an `init` marker, so fusepy never wired the C callback and the write-batching INIT negotiation silently never ran (4 KiB requests → 256 commits per 1 MiB → 0.04 MB/s); the adapter now has the marker and shares `NyFSMount`'s BIG_WRITES/WRITEBACK_CACHE/MAX_PAGES negotiation — **7× on streaming writes**. Next step: write-commit batching (`volume_fsync` anchors it). Operator runbook landed (`docs/how-to/operate-the-vault.md`). Suite 432 → **434**.

**2026-08-16 (0.14.14): the quota-event ring — the operator's actionable history.** `volume_events` (OPERATOR-ONLY) + `nyrqisctl vault events` expose the in-memory quota-event ring (bounded at 64, newest first): warning-level TRANSITIONS (`near`/`at`/`over` — the same points the log lines fire) and every **EDQUOT rejection** (the hard stop — the most actionable event an operator can see). Honest scope: the ring is diagnostics, never persisted; the ledger is the durable source of truth (runbook). A container is refused the op even with the storage capability. Format `time\tvolume\tcontainer\tlevel\tusage/quota`. Suite 456 → **458**.

**2026-08-16 (0.14.15): path-scoped grants + admin-op tightening.** A grant may now carry a **`path` scope** (`volume_grant` with `path: /subtree`): the grantee can open the volume, but every data-plane op on a path outside the subtree is rejected **fail-closed** — write, read, rename (**BOTH sides** of a rename must stay inside the scope; a move cannot escape it either way), and truncate. A bare grant stays a whole-volume grant, persisted back-compatibly as `True` (the 0.14.8 shape); a scoped grant persists as `{"path": ...}` (both survive a daemon restart — tested). The creator and operator are never path-restricted. **Admin-op tightening (the finding this round):** snapshot / restore / snapshot-delete capture or rewrite the **WHOLE** volume tree — a granted container (even holding a whole-volume grant) could snapshot data outside any scope, or clobber the entire volume with a restore — so these are now CREATOR/OPERATOR-ONLY, exactly like grants themselves (a grantee's attempt fails closed with "creator or the operator", even with a valid handle — tested). CLI: `nyrqisctl vault grant --name assets container-b --path /assets`; `vault grants` prints scoped grants as `container@path` (whole-volume grants bare); the grant reply echoes the scope. Suite 458 → **461**.

**2026-08-16 (0.14.16): path-scoped grants verified end-to-end + the honest EACCES.** The grant-scope rejection now rides the CALL reply with `errno` 13 (**EACCES**) — a scope violation is a permission denial, so the FUSE passthrough surfaces the honest errno to the kernel instead of a generic `EIO`. **Verified through a REAL seccomp container:** a container holding a **path-scoped** grant (`/assets`) on an ENCRYPTED volume drives the passthrough ops over the wire — the in-scope write lands, the write AND read outside the scope are denied with `EACCES` riding the reply, the in-scope write reads back, and the operator confirms the rejected path **never reached the tree** (reads as no-such-file). Suite 461 → **462**.

**2026-08-16 (0.14.17): the access matrix joins the event ring.** The ring now records grant/revoke actions alongside the quota signal — a `grant` records who, when, and **how wide the scope**; a `revoke` records **what was actually withdrawn** (the scope the grantee held). Events carry a `kind` (`grant`/`revoke`/`quota`); quota events keep `level`/`usage`/`quota`, grant events carry `scope`. Ring stays bounded (64), newest-first, OPERATOR-ONLY. CLI `vault events` prints the kind column (quota rows unchanged; grant/revoke rows `scope=...`), header now `time\tvolume\tcontainer\tkind\tdetail`. Verified end-to-end against a real daemon (create → scoped grant → revoke → ring shows both actions newest-first). Suite 462 → **463**.

**2026-08-16 (0.14.18): the event ring survives a restart.** The ring is persisted with the registry at every commit — grant/revoke and quota-transition events ride the same registry write (the grant/revoke ops record BEFORE the persist; quota events ride the commit path's save), so the operator's recent history survives a daemon restart (tested: a scoped grant's event is present on a fresh `StorageService` over the same registry). It stays **bounded diagnostics** (64, newest first) — the registry is still the source of truth for the current state; this is durability for recent history, not a log file. Honest boundary documented: the FUSE kernel mount is operator-only and the operator is never path-restricted, so a kernel mount can never hold a scoped grant — a scoped grant's EACCES is exercised by the grantee's own data plane (verified end-to-end in 0.14.16). Suite 463 → **464**.

**2026-08-16 (0.14.21): wire-level streaming — the ADR-0024 follow-on.** STREAM_CHUNK is now a first-class wire message type (5) in the codec on BOTH halves (rust/ipc + `ipc_codec.py`, byte-identical, differential-gated); the envelope (`version ‖ stream_id ‖ call_id ‖ index ‖ count ‖ payload ‖ sha256`) rides the payload field and the codec's `reply_to` carries chunk correlation. **Both serving paths reassemble** — the floor transport (`_accept_stream_chunk`, window/TTL/sender-bind, chunked REPLYs via `build_reply_wires`) AND the Rust serving loop (rust/ipcd: type-5 acceptance, per-chunk SHA-256 via sha2, rebuilt CALL wire to pending, chunked reply routing without consuming pending; 24 crate tests) — the crux being that the daemon's service socket is loop-served in production, so loop reassembly is required for the wire-level path to be real. The client gains `wire_stream=True` (chunked send + chunked-reply reassembly, floor path); the service's plain write/read accept the wire-stream DATA budget (the 32 KiB per-call cap is now a config bound on the stream path) and `volume_open` advertises `stream_ver: 2`, with the service-level envelope + paging staying for old peers; a payload beyond the 512-chunk window is refused client-side immediately. **Also fixed the transport close-race the wire-level path exposed:** `IPCDatagramServer.close()` now joins the serve loop before releasing the socket — a server torn down with `stop.set(); close()` left its serve thread mid-poll, the next bind could reuse the freed fd, and the stale poll stole ONE datagram from the new socket (a lost STREAM_CHUNK left reassembly one chunk short; the caller timed out; reproduced ~50% in the harness with zero warnings). close() is now synchronous and safe: the loop exits within one poll window and closes the endpoint with no poll in flight; the path is unlinked before close() returns; serve-after-close returns immediately. New regression tests cover all four contracts. Suite 479 → **492** (both crate paths green; 50 expected skips crate-less).
**2026-08-16 (0.14.20): the streaming data plane — ADR-0024 first increment.** A large passthrough write/read now rides ONE pipelined stream instead of N sequential ≤32 KiB CALLs: the client splits the payload into chunks (each an ORDINARY capability-gated `volume_write` CALL with a `stream_id`/`stream_index`/`stream_count` envelope + per-chunk SHA-256), the service reassembles (out-of-order OK; the slot binds to its first chunk's sender; ≤512 chunks = 16 MiB; 30 s TTL sweep; duplicate/mismatch/checksum failure reject the whole stream fail-closed) and performs **ONE write, ONE quota check, ONE accounting charge, ONE commit** on the final chunk, replying once; streamed reads (`volume_read` + `stream=True`) page through NyFS in-process and return correlated ≤32 KiB REPLY pieces the client collects by index. **The wire codec is untouched** (chunks are ordinary CALLs — the byte-identical differential gate stays green and the Rust serving loop needs no change); the ADR's wire-level framing (a codec flag + Rust loop reassembly for all services) is the documented follow-on. **Mixed-version degradation is first-class**: `volume_open` advertises `stream: true`, and a passthrough that never sees it (an older daemon) keeps paging — the paging paths stay forever (also the fallback on a partial/timed-out stream). Client halves: `IPCClient.call_stream_write` (pipelined sends, one final reply) + `IPCClient.call_stream_reply` (correlated-piece collection), both the Python floor path by design (the Rust client half's streaming is the follow-on). **Measured §29 (`--vault-stream`)**: 1 MiB writes 5.6× faster plaintext / 6.6× encrypted (355.9 → 64.1 ms; 511.4 → 77.9 ms); reads ~1.02–1.08× (their cost was already flat — AEAD block decode, and each piece still rides its own REPLY) — the evidence Architecture Group reviews before accepting ADR-0024. New `TestStorageStreaming` (13 tests incl. two real-server e2e round trips and a quota-rejected stream). Suite 466 → **479**.

**2026-08-16 (0.14.19): per-subtree quotas — budget each scope of a shared volume.** `volume_quota_set` gains a `path` scope: the quota becomes PER-SUBTREE — an **ADDITIONAL cap** on writes under that scope; every applicable cap (the whole-volume quota AND each scoped quota containing the path) must pass, so nested scopes overlap by design (scoped figures read "bytes under this scope"). Enforcement stays fail-closed EDQUOT (errno 122) before the tree is touched; the scoped EDQUOT carries its scope in the error AND the event ring (whole-volume EDQUOTs stay scope-less — the operator can tell where it hit). Scoped usage is billed incrementally between commits and re-derived from the tree at every commit (a delete under the scope re-accounts it away — tested); quotas + scoped usage persist with the registry (restart-tested). Surface: `quota-get` rows gain a `scope` column; `usage` reports `scope_usage`; CLI `vault quota-set <vol> <container> --path /assets --bytes 500`. Verified end-to-end against a real encrypted daemon (scoped quota → in-scope write lands → over-scope write rejects with EDQUOT + "under scope /assets" → quota-get shows both rows). Advisory warning levels remain whole-volume-only for now — scoped quotas enforce the hard stop. Suite 464 → **466**.

**2026-08-16 (0.14.13): the vault at a glance — status/health carry the aggregate.** `status` and `health` now report the vault aggregate (volumes, total LOGICAL + PHYSICAL bytes, warned containers) from the CACHED ledger figures — no tree walk, so status stays O(volumes) instead of paying the §28 refresh (that is what `volume_summary` is for). The status service already holds the daemon reference, so the block rides both the main-socket and health-socket status services with zero host wiring; a bare service reports `vault: null`. `nyrqisctl status`/`health` print the line when present. **Warning levels verified through a REAL kernel mount**: a kernel write past 80% of a quota on the live encrypted mount commits at fsync, the refresh computes `near`, and `vault quota-get` reports it — the same end-to-end path as the EDQUOT verification. Suite 454 → **456**.

**2026-08-16 (0.14.12): quota warnings — the operational signal on top of the hard EDQUOT stop.** Warning levels (`near` ≥ 80%, `at` ≥ 95%, `over` > 100%) are computed at every ledger refresh from the quota ledger, logged only on a level TRANSITION (no spam for a volume parked near quota), and persisted with the registry. `over` is unreachable by writing (the write path rejects it) — only by re-derivation: a quota set below existing usage or a restore to a larger snapshot (both tested). Surfaced everywhere the operator looks: `volume_quota_get` rows carry the level, `volume_usage` carries per-container warnings, `volume_summary` rows carry `warning_count`, and the write REPLY carries the writer's post-billing level (`nyrqisctl vault write` prints `(quota warning: near)` at the point of action). Clearing a quota drops the signal at the next refresh. Suite 452 → **454**.

**2026-08-16 (0.14.11): the operator's vault view — PHYSICAL bytes, the whole-vault summary, and the ledger-refresh cost.** `volume_usage` now reports the volume-wide PHYSICAL figure (the on-disk state footprint — journal + metadata + block store, compressed + CoW-deduped; cached with the ledger at each commit); it is volume-wide, never per-container (CoW sharing makes per-container physical attribution load-dependent — honest in the ADR + runbook). Verified: 9 KiB compressible → logical 9000, physical 902. `volume_info.bytes_persisted` now uses the same helper (it previously counted only the post-compaction `blocks/` dir and reported 0 for journal-resident state). **`volume_summary` (OPERATOR-ONLY) + `nyrqisctl vault summary`**: the whole-vault aggregate — volume count, total logical/physical bytes, per-volume rows (logical, physical, consumers), re-derived fresh; a granted container is refused even with the capability. **§28 benchmark (`--ledger-refresh`)**: the ADR-0022 per-commit usage refresh measures **0.53–0.67 ms @ 1 k files, 7.79–8.93 ms @ 10 k** — a rounding error next to the ~110 ms durable save it rides on, so accounting added no measurable commit cost. Suite 450 → **452**.

**2026-08-16 (0.14.10): per-container quota & accounting — ADR-0022's follow-on design is implemented.** Every volume now accounts bytes per container (`volume_usage`), billed to the WRITING container at `volume_write` (the handle's binding container — the 0.14.8 grant matrix made sharing billable); reads are free, and `volume_truncate` credits the owner the size delta immediately. Attribution is a per-path last-writer map (`owners`) and the ledger is a **cache re-derived from the NyFS tree at every commit** (fsync / interval tick / close / restore — NyFS gains a public `walk()`): deletes, truncates, renames and restores re-account exactly what the tree holds, so the ledger can never drift from what a restore actually frees (verified: delete 100 → usage 50; restore to the 100-byte snapshot → usage 100). Sum of file sizes = LOGICAL bytes — the operator contract; physical block bytes (CoW/compression) are deliberately not billed (honest in the ADR). `volume_quota_set` (CREATOR/OPERATOR-ONLY, like grants) sets a per-container byte quota (`bytes: null` clears; unlimited default); the write path rejects **fail-closed with EDQUOT (errno 122) BEFORE touching the tree**, the errno riding the reply so the FUSE passthrough surfaces the real EDQUOT to the kernel — **verified through a REAL kernel mount** (an over-quota write on the live encrypted mount raises EDQUOT at the syscall, not a generic EIO; the fail-closed rejection does not wedge the volume). Quotas + usage + attribution persist in the registry at every commit (restart-safe; the tree re-derives anyway). CLI: `vault quota-set/quota-get/usage` (verified e2e against a real daemon: quota → over-quota write exits 1 with "quota exceeded" → quota-get/usage show the billed figure → `--unlimited` clears). Suite 440 → **450**.

**2026-08-15 (0.14.9): group commit (interval-based) + the granted-container data plane + the quota design.** The FUSE `flush` handler (close-of-last-fd) is no longer a durability boundary (POSIX: close does not promise durability — fsync does); `volume_flush` is a group-commit opportunity — the service persists the deferred batch at the commit-interval tick (`--commit-interval`, default 5 s; 0 = fsync/close only), so a burst of short-lived files pays ONE save per interval instead of one per close. `volume_fsync`/`volume_close` still commit unconditionally. Verified: flush defers (journal untouched), fsync commits, the interval tick commits the batch, close commits. §27 re-bench: writes hold ~3.2/~0.8 MB/s (already single-commit) and the new small-files burst (100×4 KiB open/write/close) runs ~260 files/s through the encrypted passthrough vs ~11–21 k native — the per-op CALL round-trip + AEAD is the ADR-0022 data-plane cost, not commits. **Granted-container e2e**: a real seccomp container with an explicit volume grant opens an ENCRYPTED volume by name and drives the passthrough's operations over the wire; honest finding — the kernel mount is operator/host-only by design (`mount`/`umount2` in seccomp's always-deny set), so the container-facing data plane is those CALLs (documented in the runbook). CLI e2e: `vault snapshot-delete` against a real daemon. **ADR-0022 gains the quota & accounting follow-on design** (per-container bytes, billing the writing container, fail-closed EDQUOT at write, tree-derived ledger) — design only, status stays honest. Suite 437 → **440**.

**2026-08-15 (0.14.8): write-commit batching + the cross-container grant matrix + snapshot deletion.** `volume_write` defers the durable commit (in-memory dirty blocks — the §26 byte-path behavior) and `volume_fsync`/`volume_flush`/`volume_close` anchor it, so a kernel write pays ONE `save()` at the fsync/flush/close boundary instead of one per CALL; the passthrough gained the `flush` FUSE handler. §27 re-bench: streaming writes **0.28 → 3.17 MB/s (11×)**, 4 KiB syscalls **0.04 → 0.78 MB/s (19×)**; §26 byte-path writes ~86 ms → ~2.2 ms p50 (~40×) — deferred data is visible in memory immediately and lost on a crash before commit (POSIX fsync semantics, spelled out in the runbook). **Cross-container volume grants (ADR-0022's access matrix is no longer future work):** `volume_grant`/`volume_revoke`/`volume_grants` (CREATOR/OPERATOR-ONLY — a granted container administers nothing, it can only open) + `nyrqisctl vault grant/revoke/grants` (by id or `--name`); grants are per-container, persisted with the registry, and never imply `CAP_STORAGE_VOLUME`; `volume_open`/`volume_list` honor them; revoke gates future opens while a live handle keeps working (open-file semantics). **Snapshot deletion:** `NyFS.delete_snapshot` + `volume_snapshot_delete` over the wire + `nyrqisctl vault snapshot-delete` (missing snapshot fails honestly). Runbook §3b. Suite 434 → **437**.

### 6. NUI Runtime Consumption (ADR-0025)

**2026-08-16 (0.14.22): the UI import gate lands.** The Nyrqis side of
the NyForge ↔ runtime pipeline: the runtime can now import, validate, and
render the `.nstudio` documents NyForge produces (NFS-001).

- **Reference floor** (`ui/nstudio.py`): parse + validate against the NUI
  contract tables (vocabulary NFS-001 §4, per-type property/event/
  action contracts §5, behavior/binding references §7–§8), strict
  schema-version gate (§9 → `NstudioVersionError`), `$state:` argument
  substitution (§7.1), the **NUI expression language** (§7.2,
  `ui/nexpr.py` — `state.name` refs, comparisons, `&&`/`||`/`!`, and
  `if`/`min`/`max`/`contains`/`format`, position-tagged syntax errors)
  for `$expr:` values and condition `expression` fields, `resolve_action()`,
  `resolve_condition()`, layout `render()` (absolute coordinates), and a
  deterministic `text_preview()` stand-in renderer. **Animations
  (NUI-SCHEMA §8.3):** the `animations` section (unique ids, targets
  that name components, non-empty properties, timing parameters) is
  validated and the `Nyrqis.Animation.Play` system action's reference
  is checked fail-closed. **State scopes (NUI-SCHEMA §8.4):** the
  `stateScopes` section (global/screen/component/session/persistent
  tables) is validated — unknown scope names and non-object tables
  rejected — and dotted `scope.key` references resolve through the
  declared tables (`resolve_state`/`resolve_states`, `_state_known`,
  scope-aware expression validation).
- **Rust import gate** (`rust/nyui/`, ABI 1.0.0): parse/validate behind
  the versioned FFI (`nyrqis_nyui_validate`/`_version`/`_last_error`),
  caller-supplied input, zero Rust-side allocation, serde_json-only. 13
  crate unit tests (incl. `nexpr` — the byte-for-byte expression
  mirror — and the animations validation, differential-tested).
  **This is the first compiled artifact of the UI layer** — the
  platform-critical execution path per ADR-0020.
- **FFI loader** (`ui/nstudio_codec.py`): the standard crate-loader
  contract (`$NYRQIS_RUST_LIB` → `target/release/` → `LD_LIBRARY_PATH`,
  ABI check, `NYRQIS_RUST_FORCE=1` semantics, error-class mapping back
  to the floor's exception hierarchy).
- **Fixtures**: the four NyForge example designs (forge-home,
  settings-app, vault-dashboard, nyrqis-shell) under
  `tests/fixtures/nstudio/` — the runtime is self-contained and
  CI-verifiable without the NyForge checkout.
- **Tests**: `TestNstudioImport` (floor) + `TestNstudioCodecConformance`
  (differential: the crate rejects exactly what the floor rejects, error
  messages byte-identical on single-issue documents) — 32 new tests,
  green on the crate path; the crate-less path skips the conformance
  class (gate pattern).
- **CI**: `rust-nyui` (build + tests) and `rust-nyui-conformance`
  (required gate, forces the two classes through the FFI).

**2026-08-18: the Nyrqis UI Runtime lands.** `ui/runtime.py`
(`NyrqisRuntime`) — the real OS runtime counterpart of Nyforge's
`ForgePreviewRuntime`. Wraps a loaded `NstudioDocument` and provides:

- **State management**: `set_state()`, `resolve_state()`,
  `resolve_states()` (flat + scoped, identical semantics to the floor
  and the Rust crate).
- **Event dispatch**: `fire_event(component_id, event_name)` — finds
  the behavior attached to the component's event, evaluates its
  condition (including AND/OR logic groups and expression language),
  and executes its action chain. Returns the list of executed
  `(target, name, arguments)` tuples.
- **Binding application**: `apply_binding()` / `apply_all_bindings()`
  — syncs state values to component properties.
- **Action execution**: handles built-in system actions (`Theme.Set`,
  `Animation.Play`, `Notification.Show`) and component-targeted actions
  (`Open`, `Close`, `Toggle`).
- **Diagnostics**: `summary()` and `text_preview()` for the compositor.

26 new tests (`tests/test_runtime.py`) — state management, event
dispatch, condition evaluation (AND/OR groups), action chains,
binding application, system actions, and summary. All green on the
floor path.

**2026-08-18: the Nyrqis Compositor lands.** `ui/compositor.py`
(`Compositor`) — PIL-based visual renderer that converts a loaded
`NstudioDocument` into PNG images. Supports:

- **Themes**: Eclipse (dark) and Solar (light), with full RGB palettes
  for all component types.
- **Scale factor**: 1.0 (native) and 2.0 (retina) rendering.
- **30+ component renderers**: Window, Taskbar, StartMenu, Button,
  Text, Input, Toggle, Slider, ProgressBar, DesktopIcon, Clock,
  SystemTray, LockScreen, ContextMenu, MenuItem, Notification,
  QuickSettings, WorkspaceSwitcher, CommandPalette, Launcher, etc.
- **`shell_render` IPC operation**: new NuiService op that renders the
  loaded (or provided) shell design to base64-encoded PNG images.

22 new tests (`tests/test_compositor.py`) — theme completeness,
render operations, save to file, and fixture rendering (loads and
renders the real 290-component desktop shell in both themes). All
green.

**2026-08-18: the SDL2 Real-Time Compositor lands.**
`ui/compositor_sdl.py` (`SDLCompositor`) — SDL2-based high-performance
renderer using pysdl2 for GPU-accelerated rendering:

- **Windowed mode**: renders the Nyrqis shell in a live SDL2 window
  when DISPLAY is available.
- **Headless mode**: renders to an off-screen SDL2 surface and exports
  to PNG via ctypes pointer access (for CI/testing).
- **Bitmap font**: built-in 5×7 bitmap font for all ASCII printable
  characters — no external font dependencies.
- Same 30+ component types and Eclipse/Solar themes as the PIL
  compositor.
- **`shell_display` IPC operation**: new NuiService op that renders
  the shell in a live window (when DISPLAY is available) or falls
  back to headless PNG export.

17 new tests (`tests/test_compositor_sdl.py`) — initialization,
render operations, save to file, fixture rendering, bitmap font.
All green.

**2026-08-16 (0.14.23): the import gate rides the control plane + a
second screen + §30.** `NuiService` (`ui/service.py`) exposes
`nui_validate` (gate only) and `nui_load` (gate + persist as the daemon's
shell UI at `<state-dir>/ui/shell.nstudio`) over the datagram control
plane — operator-only (a registered container is refused), per-call
document budget, unknown-op rejection; `nyrqisctl nui validate|load`
wraps them and a real e2e drives a live daemon with the Rust crate as the
engine. **`nui_current`** (the loaded-design surface: `loaded: false`
before any design, the persisted summary re-imported through the gate on
every call, `valid: false` with the message when the persisted file has
gone stale) rides alongside via `nyrqisctl nui current`. The Security
Center screen (`security-center.nstudio`, the second NyForge design) and
the Vault Workspace (`vault-workspace.nstudio`, the third) join the
fixtures with shape + `$state:` tests. `tests/benchmarks.py --nui` (§30)
A/Bs the gate floor-vs-crate on the largest fixture:  crate **~2.1×
faster at the median** (242 µs vs 502 µs p50, ~1/3 the variance). Suite
524 → **538**.

**2026-08-17 (0.14.24): the Nyrqis API Registry — one machine-readable
contract, three consumers.** The NUI component vocabulary (29
components, 6 system actions) moves out of the three hand-maintained
tables and into `ui/contracts/nui-api-v1.json`. `ui/nstudio.py` loads
`COMPONENT_CONTRACTS`/`SYSTEM_ACTIONS` from it at import time (a missing
or malformed registry is a hard import error — never silently empty);
`rust/nyui` embeds the same file via `include_str!` and parses it into a
`OnceLock<Registry>` with serde derive (const tables deleted; a registry
change that isn't compiled in is a build failure). Nyforge regenerates
its C# tables from a vendored copy with a CI drift gate. `TestNstudioCodecConformance`
passes unchanged — both consumers read the same file, so floor↔crate
cannot diverge. Full suite: **538** (unchanged — the migration is
behavior-preserving by construction).

**2026-08-17 (0.14.25): the first real Shell component set lands in
the registry.** 63 components (was 29) across five new categories —
Shell (Taskbar, StartMenu, WindowFrame, CommandPalette, LockScreen, …),
Data (List, DataTable, TreeView, Menu, …), Form (DatePicker, FilePicker,
SettingsPanel, …), Media (Video, Audio, MediaPlayer) and Developer
(Terminal, CodeEditor, LogViewer) — each with a real semantic contract
(Taskbar: position/alignment/autoHide/pinnedApps/runningApps/showClock/
showTray; WindowFrame: Minimize/Maximize/Restore/Close actions). All
three consumers pick it up automatically (floor at import, crate at
compile — a vocabulary change not compiled in is a build failure,
Nyforge regenerates its C#). The import-gate tests that used `Taskbar`
as their "unknown type" example now use `BogusWidget` — the old example
became real. Suite stays **538**.

**2026-08-17 (0.14.26): the real desktop shell screen — authored with
the shell vocabulary.** `desktop.nstudio` joins the fixtures: a 1440×900
desktop (DesktopSurface + DesktopIcons, Taskbar with Start/Search /
pinned List / WorkspaceSwitcher / clock / SystemTray, StartMenu, Search,
CommandPalette, NotificationCenter, QuickSettings with theme buttons)
plus a `lock` screen (LockScreen component) — 30 components, 8
behaviors, 6 bindings. Behaviors exercise the shell actions
(`StartMenu.Toggle`, `CommandPalette.Open`, `DesktopIcon.Launch`) and a
conditional DND notification. Validated by floor + crate (differential)
and opens in Nyforge itself (serializer test on the editor side). Suite
538 → **539**.

**2026-08-17 (0.14.27): the window system + power UI — second
reference shell screen.** `windows.nstudio`: WindowFrame +
WindowControls drive component-targeted actions (`Minimize`/
`Maximize`/`Close` on the frame), stacked windows (Vault behind Files)
with a toolbar and lists, and a PowerMenu with Sleep/Restart/Shutdown
wired to system notifications — 21 components, 8 behaviors, 1 binding
across 2 screens. Validated by floor + crate (differential) and opens
in Nyforge itself. Suite 539 → **540**.

**2026-08-17 (0.14.28): widgets + OSD + login — third reference shell
screen; registry 63 → 66.** `WidgetHost`, `OSD`, and `Login` join the
registry (66 components) for the remaining Phase F pieces; all three
consumers regenerated. `widgets.nstudio`: WidgetHost holding Clock +
System Monitor cards, a volume OSD with `$state:`-substituted message,
and a Login with username/password inputs — 19 components, 5 behaviors,
2 bindings across 3 screens. Validated by floor + crate (differential)
and opens in Nyforge itself. Suite 540 → **541**.

**2026-08-17 (0.14.29): typed property metadata in the registry.**
`properties` become metadata objects (`name`/`type`/`default`/
`bindable`/`required`, plus `min`/`max`/`enumValues`/`units` where
meaningful — Slider value 0–100, Taskbar position enum, MediaPlayer
position stays a number) — the vocabulary is unchanged, the
representation is richer (NFS-006's reserved fields). The floor
parses names for validation; the crate's serde structs carry the full
`PropertyDefinition`; Nyforge regenerates `ComponentContracts.cs`
(names) and the new `PropertyDefinitions.cs` (typed metadata). Suite
stays **541** (behavior-preserving).

**2026-08-17 (0.14.30): reusable component masters — `components[]`
stops being reserved (NFS-006 §9).** A document's `components[]` now
holds reusable masters (validated like any component); a node
referencing one declares `componentRef` + `overrides` and omits `type`
— both gates reject an instance that declares its own type, and
overrides must be properties the master's type contract declares. The
floor resolves the instance's contract from the master; the crate does
the same with byte-identical messages (new differential tests). The
`desktop.nstudio` shell fixture builds its taskbar from one
`TaskbarButton` master with two `componentRef` instances; Nyforge's
`ReusableComponentResolver` materializes instances (master clone +
overrides + instance children) and FEATURE_STATUS `ComponentReuse` →
implemented. Suite 541 → **546**.

**2026-08-17 (0.14.31): responsive layout constraints (NUI-SCHEMA
§4.1).** `layout` gains optional constraint fields — anchors
(`anchorLeft/Right/Top/Bottom`, booleans, all default false),
min/max bounds (non-negative ints, `min* <= max*`), and `aspectRatio`
(positive) — validated identically by both gates (new differential
tests). `resolve_layout()` applies them for any container size (both
horizontal anchors stretch the width; a single `anchorBottom` docks
from the bottom; aspect derives the non-stretched axis) and
`text_preview()` now shows adapted bounds. The `desktop.nstudio`
taskbar stretches full-width and docks with min/max bounds; a desktop
icon carries `aspectRatio: 1.0`. Suite 546 → **562**.

**2026-08-17 (0.14.32): localization — `$localize:key` through the
`locales` section (NUI-SCHEMA §8.1).** A document carries
`{"active": ..., "tables": {...}}`; the active locale must have a
table and tables map string keys to string values. `$localize:key`
references in component properties, reusable overrides, and behavior
action arguments must exist in the ACTIVE locale's table — fail-closed
at both gates with byte-identical messages (differential).
`resolve_text()` resolves them (missing keys stay literal at
resolution; the gate rejects them up front). The `desktop.nstudio`
search label and DND message are `$localize:` references (en/af
verified). Suite 562 → **573**.

**2026-08-17 (0.14.33): resources — the managed asset catalog
(NUI-SCHEMA §8.2).** A document's `resources` section (`assets` list:
unique ids, allowed kind image/svg/icon/font/audio/video/material/
animation, non-empty path, optional 64-hex sha256) is validated by
both gates; `$asset:id` references in component properties and
reusable overrides must name a declared resource — fail-closed with
byte-identical messages (differential). The `desktop.nstudio`
wallpaper is a declared image asset referenced via `$asset:wallpaper`.
Suite 573 → **585**.

**2026-08-18 (0.14.37): the extended Shell vocabulary — AppGrid, Clock,
Dock, TitleBar.** Four more desktop-specific primitives join the
registry (doc #15's "desktop-specific primitives" list is now complete:
24 Shell types), each with a typed semantic contract — `AppGrid` lays
`apps` out on a `columns` grid and `Launch`es on `appClicked`; `Clock`
renders a `format` (12h/24h) with optional `showSeconds`; `Dock` knows
`position`/`pinnedApps`/`runningApps`/`autoHide`/`iconSize`/`magnify`
and `Launch`es on `appClicked`; `TitleBar` carries `title`/`icon` and a
`doubleClicked` event. All three consumers pick the new types up
automatically (the crate embeds the same registry file); the desktop
fixture exercises them — the taskbar clock is a real `Clock` bound to a
`clockFormat` state, a `Dock` sits on the desktop surface, a `Launcher`
overlay hosts an `AppGrid`, and a real Files window is framed by
`WindowFrame` + `TitleBar` + `WindowControls` whose close button targets
the window's `Close` action. Unknown properties/events on the new types
fail both gates with byte-identical messages (differential). Suite 635
→ **642**.

**2026-08-18 (0.14.39): behavior logic graphs (NUI-SCHEMA §7.3).** A
behavior's `condition` is now a leaf or a recursively-nested
`logic: and|or` group (non-empty `conditions` list, each entry a leaf or
a group), and its `DO` is exactly one of a single `action` / a non-empty
`actions` chain run in order — both or neither rejected at parse. Groups
evaluate with all/any recursion (`resolve_condition`/`_eval_condition`);
`resolve_actions` returns the chain with per-step `$state:`/`$expr:`
substitution, `resolve_action` stays the back-compatible first step.
Both gates validate the shapes fail-closed with byte-identical messages
(unknown logic, empty/non-object group entries, unknown states in nested
groups with the space-separated element path, both-or-neither action
forms) — 4 new crate conformance cases; Nyforge mirrors as ER-NUI-024 /
ER-NUI-005 with `BehaviorEvaluator` all/any recursion and serializer
output free of empty `actions`/`conditions` noise. `desktop.nstudio`
exercises a real 2-action theme chain (Theme.Set then Animation.Play)
and an AND quiet-hours guard. Floor `TestBehaviorLogicGraphs` +11, crate
conformance +4. Suite 651 → **666**.

**2026-08-18 (0.14.38): animation keyframes (NUI-SCHEMA §8.3).** An
animation may carry an optional `keyframes` list — `[{"offset":
0.0–1.0, "value": …}]` stops with strictly increasing offsets and a
number/string/boolean value — giving the runtime the actual multi-point
curve to interpolate (without it the animation stays a single-segment
transition). Both gates validate the shape fail-closed with
byte-identical messages (keyframes must be a list; each entry an object
with a numeric offset in [0, 1] and a present value; offsets strictly
increasing) — 6 differential cases verified; Nyforge mirrors as
ER-NUI-022. The Start menu fade in `desktop.nstudio` is now a
3-keyframe curve (0 → 0.75 at 60 % → 1) played by
`behavior_start_toggle`. Floor `TestAnimations` +6, crate unit tests 13
→ 16 (+3 keyframe cases), crate conformance +3. Suite 642 → **651**.

Status: **implemented + gated** — the import gate is real and
operator-drivable end to end; a graphical shell renderer (C++/declarative
UI per the matrix) is the documented follow-on, not yet started.

### 7. Wayland Display Server Integration (ADR-0026)

**2026-09-01: Phases 1–2 complete.** The Nyrqis shell connects to a
Wayland compositor and renders via SHM buffers.

- ✓ **Rust crate** (`rust/wayland/`, ABI 1.2.0): real `wl_display`
  connection via `wayland-sys` FFI; `wl_compositor` binding via
  `wl_registry`; `wl_surface` creation; SHM buffer submission
  (`memfd_create` → `mmap` → `wl_shm_pool` → `wl_buffer` →
  `wl_surface.attach` + `damage_buffer` + `commit`); xdg-shell
  integration (`xdg_wm_base` + `xdg_surface` + `xdg_toplevel`);
  `wl_seat` binding with `get_keyboard`/`get_pointer`; `wl_output`
  enumeration for multi-monitor; event handler callback; 17 unit tests
- ✓ **Python FFI loader** (`ui/wayland_codec.py`): ctypes wrapper
  with ABI check, graceful stub fallback when cdylib absent
- ✓ **WaylandDisplay** (`ui/wayland_display.py`): high-level wrapper
  with event callbacks (`on_configure`, `on_close`, `on_key`,
  `on_pointer`); `poll_and_dispatch()` for select()-based fd polling;
  `render_frame()` with PIL Image → ARGB8888 conversion; `outputs`
  property for multi-monitor
- ✓ **DesktopSession integration** (`ui/desktop_session.py`):
  `connect_wayland()` / `render_to_wayland()` / `has_wayland`;
  `run_event_loop()` polls Wayland fd, dispatches events, renders
  frames; `_setup_wayland_events()` translates compositor events to
  session actions; `_sync_wayland_outputs()` maps outputs to monitors
- ✓ **SDL2 Wayland backend** (`ui/compositor_sdl.py`): `wayland=True`
  parameter for GPU-accelerated rendering via SDL2's Wayland driver
- ✓ **CI** (`.github/workflows/ci.yml`): `rust-wayland` job builds +
  tests 17 unit tests; `arm64-conformance.yml` cross-compiles for aarch64

Status: **implemented + CI-verified** — Phases 1–2 complete. GPU
acceleration (GBM/DRM, Phase 3) and custom compositor (Phase 4) are
follow-ons per ADR-0026.

### 8. Package Signing (NPS-026 §6)

**2026-09-01: Ed25519 signing + installer with verification.**

- ✓ **Signing module** (`backend/package_signing.py`): Ed25519
  signing/verification via PyNaCl; `SigningKeypair` with generate/
  from_private_key/from_public_key; `PackageSignature` block (97 bytes);
  `TrustStore` for trusted publisher keys; 31 unit tests
- ✓ **Installer** (`backend/installer.py`): `PackageInstaller` with
  mandatory signature verification (NPS-026 §6.1); trust store
  integration (§6.3); integrity tree verification (§7); package
  database; 17 unit tests
- ✓ **CLI tools**: `nyrqisctl sign` (generate-key/sign/verify/trust)
  and `nyrqisctl package` (create/inspect/verify/install)
- ✓ **CI**: `test_package_signing` + `test_installer` in test suite

Status: **implemented + tested** — NPS-026 §6 compliance achieved.
Manifest serialization uses compact JSON to match signing.

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
- The default-deny allowlist posture is now the **default** (x86_64 baseline verified; arm64 baseline is a conservative subset pending hardware verification)
- `openat2` write-intent is not data-plane filtered (cBPF cannot inspect flags behind a pointer) — documented residual gap
- LSM (AppArmor/SELinux) policy generation and runtime reload are implemented and wired into the container lifecycle
- The FUSE mount requires `fusepy` + `/dev/fuse` on the host (present here — live mount verified 2026-08-12 — but not guaranteed everywhere)
- The direct-syscall launcher is the default path but `unshare(1)` remains available as an opt-in legacy path; the Rust-native child entry point (one `clone(2)` FFI call, no Python between fork and exec) is the default when the crate is present, with the Python fork-setup child as the crate-less fallback (landed 2026-08-15, syscalls ABI 1.2.0); network namespaces are opt-in (`network=True`) with **veth/bridge outbound connectivity** providing real internet access via NAT (requires host root/CAP_NET_ADMIN)

## Next Steps

### Immediate (Phase 1: Core Container Primitives)
1. ✓ Direct `unshare(2)`/`fork(2)` container launch (implementation_plan.md §4.1) — landed 2026-08-13; **the Rust-native child entry point landed 2026-08-15** (syscalls ABI 1.2.0: `nyrqis_syscalls_clone` + `nyrqis_syscalls_launch_child` — one `clone(2)` FFI call creates the PID-1 directly in all its namespaces, no Python between fork and exec; the Python fork-setup child is the crate-less fallback)
2. ✓ Cgroup freezer for suspension — landed 2026-08-14 (cgroups v2 `cgroup.freeze` with SIGSTOP/SIGCONT fallback)
3. ✓ Network namespace support — landed 2026-08-14 (`network=True` config flag; loopback-only netns brought up by the launcher so 127.0.0.1 works; veth/bridge connectivity deferred — requires host root/CAP_NET_ADMIN)

### IPC (plan §4.3)
1. ✓ Unix-domain datagram transport — landed 2026-08-14 (`ipc/transport.py`: SCM_CREDENTIALS sender authentication, wire-codec framing, CALL/REPLY over sockets, ADR-0009 inbound rate limiting)
2. ✓ Shared-memory transport — landed 2026-08-28 (`ipc/shm_transport.py`: POSIX shm ring-buffer, zero-copy, mutex+condvar sync)
3. ✓ IPC syscall filtering — landed 2026-08-28 (seccomp rules gate IPC transport syscalls on CAP_IPC_SEND/RECEIVE independently of CAP_NETWORK_SOCKET)
4. ✓ IPC latency benchmarks — completed (BENCHMARK_RESULTS.md §20–§25; Rust loop p50 ~82–95 µs, NPS-003 §6.1 <100 µs gate MET)

### Short-term (Phase 2: NyFS FUSE Backend)
1. ~~Benchmark FUSE overhead (ADR-0016)~~ — **first-pass live-mount data collected 2026-08-12** (`tests/BENCHMARK_RESULTS.md` §6); the write-batching finding was **fixed 2026-08-12** by negotiating writeback caching in the INIT handshake (~25× streaming-write improvement, `tests/BENCHMARK_RESULTS.md` §6); the remaining small-write cost (per-call block compress + checksum) and the 64 KiB block-size tradeoff stay open questions
2. ~~Test CoW and snapshot functionality through a live mount~~ — **done 2026-08-12**: `TestNyFSLiveMount` in `test_backend.py` verifies fsync→save durability, snapshot round-trip, and re-mount through the real kernel path
3. ✓ Overlay filesystem for container-specific views — **Implemented**: `fuse/overlay.py` provides per-container writable layers on top of NyFS base images; integrated into `ContainerManager` for container I/O isolation

### Medium-term (Phase 3: Capability Enforcement Hardening)
1. ✓ Make the default-deny allowlist posture the default — **landed 2026-08-28** (x86_64 baseline verified; arm64 pending hardware verification)
2. ✓ Integrate LSM (AppArmor/SELinux) as a second mechanism — **landed 2026-08-28** (`backend/lsm.py`; wired into container lifecycle)
3. Address the documented `openat2` residual gap (eBPF filter with pointer-safe accessors)
4. ✓ Runtime policy reload for capability revocation — **landed 2026-08-28** (`reload_policy()` + `revoke_and_reload()`)
5. ✓ IPC syscall filtering via seccomp — **landed 2026-08-28** (IPC transport syscalls gated on CAP_IPC_SEND/RECEIVE)

### Long-term (Phase 4: Production Readiness)
1. ~~Systemd integration~~ — **landed 2026-08-14** (plan §4.5 host integration)
2. ~~Persistent state management~~ — **landed 2026-08-14** (`backend/daemon_state.py`, `--state-file`)
3. ~~Health checks and recovery~~ — **landed 2026-08-14** (status-service `health` op; recovery reporting)
4. Performance optimization
5. Full conformance assessment

## Testing and Benchmarking

The following benchmarks are required before moving from `Experimental` to `Accepted` status:

| Benchmark | Target | Status | Notes |
|-----------|--------|--------|-------|
| IPC Round-trip Latency | < 100µs | **Gate MET** | Rust IPC loop p50 ~82–95 µs vs Python floor ~263–274 µs (§22–§25). ADR-0021 Accepted. |
| FUSE I/O Overhead | < 20% | Proxy + live-mount first-pass data | **§5 (ops-layer, per-block CoW):** 1 MiB-chunk streaming write ~162 MB/s; 4 KiB-op ~3.6 MB/s write / ~2.8 MB/s read vs 541–771 / 1,064–2,131 native. **§6 (live mount):** writes ~40–46 MB/s (~25× improvement after writeback-cache fix). Vault passthrough §27: streaming writes ~3.2 MB/s. No gate declared met. |
| Token-Bucket Parameters | **Tuned** | Configurable + adversarial-tested | Defaults 200 burst / 500/s refill; configurable via IPCManager; sweep + thread-safety tests green. |
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
