---
title: Nyrqis Linux Backend Implementation Status
document_id: IMPL-001
version: 0.20.0
status: In Progress
classification: Technical
created: 2026-07-15
updated: 2026-08-14
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
| IPC Semantics | `ipc/core.py`, `ipc/transport.py`, `ipc/transport_codec.py`, `ipc/registry.py`, `ipc/service.py` | ✓ Implemented | send/receive/call/notify primitives, receive-side capability check, token-bucket rate limiting, **Unix-domain datagram transport** (receiver-side `SO_PASSCRED` — the kernel attaches the real `SCM_CREDENTIALS` to every inbound datagram and the sender attaches nothing, so identity is unforgeable and container-safe; wire-codec framing), **verified container→service end-to-end with an auto-maintained sender registry** (the backend registers each direct-syscall container's pid at spawn and drops it on terminate — no manual bookkeeping), **Rust transport hot path (ADR-0020 migration #6)** — sendto/recvmsg + SCM_CREDENTIALS in Rust when the crate is built, **first real service on the transport** (`BackendStatusService`: `ping` whole-chain check; `status` capability-gated on `CAP_SYSTEM_INFO` — denied fail-closed without a `CapabilityManager`; a handler bug becomes an `internal error` reply, never kills the serving thread) |
| Storage Guarantees | `fuse/nyfs.py` | ✓ Implemented | NyFS core, per-block CoW (fixed 64 KiB blocks), snapshots, checksumming, compression, **durability (save/load with atomic metadata + block files, NPS-004 §7)**, FUSE operations + fusepy wiring (ADR-0016) |
| Boot and Lifecycle | `boot/lifecycle.py` | ✓ Implemented | Four-phase boot per NPS-001 §5, transition validation (FIND-BOOT-002), Secure Boot reporting (FIND-BOOT-001) |

Test suite: **276/276 passing** (`python3 test_backend.py` — 250 run + 26 skipped on hosts without the Rust crates: the seeded seccomp differential test, the syscalls/NyFS/IPC/container conformance classes; all RUN in CI where the crates are built; the real-launch netns isolation tests run on hosts with unprivileged user+network namespaces), including end-to-end container launches (both the direct-syscall and legacy paths, hostile hostnames verified verbatim) and a live FUSE mount verified on this host. **ADR-0020 migration #1 (seccomp) IMPLEMENTED 2026-08-13:** the Rust policy compiler (`rust/seccomp/`) compiles policies to classic BPF, validates programs, and simulates verdicts; CI builds and unit-tests it on every push, its golden programs are **byte-identical** to the pure-Python compiler's, and the forced-mode conformance gate (`NYRQIS_RUST_FORCE=1`, CI `rust-seccomp-conformance`) runs the full suite through the FFI and is **green and a required, blocking job** — a semantic regression in the Rust compiler now fails the build. **Migration #2 (syscalls) IMPLEMENTED 2026-08-13, INCLUDING the direct-syscall launcher:** `rust/syscalls/` (ABI 1.1.0) ships `sethostname`/`prctl`/`unshare`/`mount`/`mount_proc` wrappers (0 or -errno) behind the versioned FFI surface; `backend/rust_syscalls.py` is the shared loader (search order, ABI check, ctypes fallback, `NYRQIS_RUST_FORCE=1`) wired into `launcher.py`'s `set_hostname` (FIND-BACKEND-004) and into `container.py`'s **direct-syscall launcher** (implementation_plan.md §4.1): the manager forks a namespace-setup child that performs the `unshare(2)` dance (`CLONE_NEWUSER` + root maps → `NEWNS|NEWUTS|NEWIPC` → `NEWPID`), the container's PID-1 mounts a hardened procfs via the no-arg `mount_proc` FFI (post-fork, pre-exec — zero Python allocation) and execs the launcher, and the setup child relays the container PID through a pipe and exits with its status so `wait()` keeps Popen-compatible semantics. The caller's uid/gid are captured before `CLONE_NEWUSER` (inside the unmapped namespace `getuid()` reports 65534 — the classic map-write failure). `unshare(1)` remains only as an opt-in legacy path (`use_direct_syscalls=False`). `clone` is deliberately deferred — `fork(2)` is the child-creation primitive here (a raw `clone(2)` FFI would need a Rust child entry point, future work). The `rust-syscalls-conformance` CI gate runs the syscalls-facing test classes forced through the FFI and is **required and blocking**. **Migration #3 (NyFS block codec) IMPLEMENTED 2026-08-13:** `rust/nyfs/` (ABI 1.0.0) ships the storage hot paths — SHA-256 per-block checksum (NPS-004 §4) and Zstandard compress/decompress-with-verify (ADR-0007, level 3 default) — behind the versioned FFI surface; `fuse/nyfs_codec.py` is the loader (search order, ABI check, hashlib/zstandard fallbacks, `NYRQIS_RUST_FORCE=1`) and `NyFSBlock` routes `compute_checksum`/`compress`/`decompress` (now verified on read) through it. The extraction boundary is the benchmark evidence (BENCHMARK_RESULTS.md §5: read-path verification dominates NyFS read cost). The `rust-nyfs` build job and the required `rust-nyfs-conformance` gate (the differential test — Rust ≡ pure-Python floor on checksums, roundtrips, and integrity failures — run forced through the FFI) are green in CI. **Migration #4 (IPC wire codec) IMPLEMENTED 2026-08-13:** `rust/ipc/` (ABI 1.0.0, dependency-free) ships the binary message framing `IPCMessage` will transport cross-process (NPS-003 §3 / NPS-017 §4.3) — a canonical length-prefixed format whose parsing is the future transport's trust boundary; `ipc/ipc_codec.py` is the loader (search order, ABI gate, `struct` floor, `NYRQIS_RUST_FORCE=1`; `-4097` invalid-wire maps to the floor's `ValueError`), wired as `IPCMessage.to_wire()`/`from_wire()`. The `rust-ipc` build job and the required `rust-ipc-conformance` gate (Rust ≡ Python floor, byte-identical wire, field-for-field decode, same malformed-input rejection) are green in CI. **Migration #5 (container launch-plan primitives) IMPLEMENTED 2026-08-13:** `rust/container/` (ABI 1.0.0, `libc` the only dependency) ships the pure, well-bounded launch-plan computations — the launcher argv (FIND-BACKEND-004: hostname and command are argv entries, never shell-interpolated), the cgroup v1/v2 resource plan (FIND-BACKEND-003: `notify_on_release=0`), the `--map-root-user` uid/gid maps, and the NPS-010 §4 lifecycle state machine — behind the versioned FFI surface; `backend/container_codec.py` is the loader (search order, ABI gate, byte-identical `struct` floor, `NYRQIS_RUST_FORCE=1`; `-4097` malformed flat → the floor's `ValueError`, `-4098` invalid transition → `False`), wired into `transition_to`, `_launcher_args`, `_cgroup_v1_plan`, `_setup_cgroups_v2`, and the direct-syscall child's root maps. The `rust-container` build job and the required `rust-container-conformance` gate (the container-facing classes — including the end-to-end launch tests — forced through the FFI) are green in CI. **Cgroup freezer for suspension IMPLEMENTED 2026-08-14** (implementation_plan.md §4.1): `suspend()` freezes the container's **whole cgroup** via the cgroups v2 `cgroup.freeze` control (write `1`, confirmed best-effort via `cgroup.events`' `frozen 1`) when the container is attached to a v2 cgroup, so descendants and future forks cannot outrun the suspension; `resume()` thaws (write `0`); `terminate()` thaws first so SIGTERM gets its graceful window (a frozen cgroup defers non-SIGKILL signals). SIGSTOP/SIGCONT remains the fallback for v1 hosts (no unified freezer — the legacy `freezer` controller is not provisioned), failed cgroup setup, and failed freeze writes; a **failed thaw raises** instead of falling back — a frozen cgroup defers every signal except SIGKILL, so a SIGCONT fallback would report RUNNING for a process the kernel still holds frozen (the caller retries or escalates to `terminate()`, whose SIGKILL still applies). New `TestContainerFreezer` (12 tests) covers the control-file decision, freeze/thaw writes, every fallback, the terminate-thaw ordering, and an end-to-end real-process signal suspend/resume. **Network namespace support IMPLEMENTED 2026-08-14** (implementation_plan.md §4.1): `ContainerConfig.network=True` (opt-in, default off — the default container keeps sharing the host netns) gives the container its own network namespace via `CLONE_NEWNET` in the direct-syscall child's mount/UTS/IPC unshare (and `--net` on the legacy `unshare(1)` path), so it sees only loopback — a pure isolation boundary; outbound connectivity (veth/bridge) is deliberately future work. `CLONE_NEWNET` is exposed through the existing `unshare(flags)` FFI (no crate change — flags are passed through raw). **Loopback brought up 2026-08-14:** the launcher (step 2b, before the seccomp install) best-effort `SIOCSIFFLAGS`s `lo` up — in an owned netns (where the container is root in the owning user namespace) it succeeds, so a `network=True` container has a usable 127.0.0.1; sharing the host netns it EPERMs harmlessly (host `lo` is already up). Verified end-to-end: a netns container granted `CAP_NETWORK_SOCKET`/`CAP_NETWORK_BIND` (and `CAP_FILESYSTEM_WRITE` to write the marker under the active filter) binds 127.0.0.1 through the real launch path. Verified end-to-end on this host: a `network=True` container's netns inode differs from the host's and its own procfs lists only `lo`; the default container's netns equals the host's (`TestNetworkNamespaceIsolation`, skip-gated on a real-launch probe). **IPC transport hardened + verified container→service 2026-08-14:** `ipc/transport.py` now authenticates purely receiver-side — the sender attaches nothing and `SO_PASSCRED` makes the kernel attach the real `SCM_CREDENTIALS` `(pid, uid, gid)` to every inbound datagram, so the sender cannot influence its identity at all (and the namespace-scoped pid/uid problem of explicit credentials inside a container is gone). The attached identity is the sender's **global** pid and **real** uid/gid — probe-verified 2026-08-14: a sender in an unprivileged new pid+user namespace presented its host pid and host uid/gid to the receiver, not its namespace-local pid (1) or mapped root — so the backend's host-pid registry works for containerized senders. Verified **end-to-end through a real container**: a `network=True` container granted the network + filesystem caps runs an `IPCClient` under the active seccomp filter and completes a kernel-authenticated CALL/REPLY with a host-side service (`test_container_ipc_call_service`, suite 238 → 239). **Over-transport latency measured 2026-08-14** (BENCHMARK_RESULTS.md §20, `--ipc-transport`): cross-process CALL/REPLY p50 188.79 µs / p95 295.23 µs / p99 373.51 µs vs 87.28 µs in-process — NPS-003 §6.1's <100 µs gate is **NOT met** at the median over the real transport, so NPS-003 stays Draft with the ADR-0020 Rust transport as the documented close path. **ADR-0020 migration #6 (IPC transport hot path) IMPLEMENTED 2026-08-14:** `rust/transport/` (ABI 1.0.0, `libc` the only dependency) ships the per-message syscall half of the Unix-domain datagram transport — `sendto`, `poll`+`recvmsg` with `MSG_DONTWAIT` (never blocks past the timeout, safe on blocking and non-blocking fds), and the `SCM_CREDENTIALS` parse yielding the sender's global `(pid, uid, gid)` and bound path — behind the versioned FFI surface; `ipc/transport_codec.py` is the loader (search order, ABI gate, `BackendUnavailable` → Python-floor fallback, `NYRQIS_RUST_FORCE=1`; `-4096` internal → `RuntimeError`) wired into `UnixDatagramEndpoint.send`/`receive` for raw frames (the wire codec, migration #4, still owns framing). Binding/0700/`SO_PASSCRED` stays on the floor. The `rust-transport` build job and the required `rust-transport-conformance` gate (the transport loader + differential classes — the FFI-driven endpoint reproduces the floor's contract: payload, kernel creds, sender path, timeout — forced through the FFI) land with this push; the crate is the documented close path for the NPS-003 §6.1 gate, and the benchmark delta with it active is the next data point. Suite 239 → 251 (225 run + 26 skipped: the 3 transport-conformance tests add to the skip set on hosts without the crate). **Auto-maintained sender registry IMPLEMENTED 2026-08-14:** `ipc/registry.py` (`ContainerIpcRegistry`) is the pid → container_id mapping the server authenticates against, kept in sync automatically — `ContainerManager(ipc_registry=...)` registers each direct-syscall container's host pid at spawn (its command is exec'd as PID-1, so `container.pid` IS the kernel-attached sender pid — probe-verified) and unregisters on terminate/wait; the legacy `unshare(1)` path is deliberately not tracked (the command runs as a grandchild with a different pid; its datagrams fail closed). The container→service e2e now uses the auto-registry end-to-end (no manual `pid_registry`), and `TestContainerIpcRegistry` (6 tests) pins the registry + manager hooks. Suite 257 → 265 (239 run + 26 skipped). **First real backend service on the transport IMPLEMENTED 2026-08-14** (`ipc/service.py`, `BackendStatusService`): a container-facing status endpoint attached to an `IPCDatagramServer` — `ping` (whole-chain check) and `status` (capability-gated on `CAP_SYSTEM_INFO`, denied fail-closed without a `CapabilityManager`; reports backend version, uptime, and the caller's own identity and capability set); the server's CALL dispatch swallows handler exceptions so a service bug becomes an `internal error` reply instead of killing the serving thread. Verified end-to-end through a REAL container (`test_container_calls_status_service`). **Runnable status-service daemon + control-plane capability lifecycle IMPLEMENTED 2026-08-14:** `ContainerManager(..., capability_manager=...)` now initializes each spawned container with its default capability set (so it can authenticate with CAP_IPC_SEND and call capability-gated services) and revokes the grants on every terminate/wait path (NPS-010 §5); `nyrqis_backend.py service serve` runs a `StatusServiceHost` daemon that owns the shared registry + capability manager + container manager and serves the status service until SIGINT/SIGTERM. The status e2e now proves the WHOLE chain automatically — a real container spawns, is registered AND granted by its manager, and completes a status CALL with zero manual bookkeeping. Verified through the RUNNABLE daemon itself: `test_host_container_completes_status_call` spawns a real container via the `StatusServiceHost`'s own `ContainerManager` and it completes the status CALL against the daemon's own server — the operator flow end-to-end. Suite 265 → 276 (250 run + 26 skipped). **Daemon lifecycle implemented per `DAEMON_LIFECYCLE.md`:** dirty-flag tracking, `NyFSMount.shutdown()` (dirty-gated final commit → unmount), SIGINT/SIGTERM handlers in blocking mode, and `auto_compact` is now the mount default (AG tuning review still pending per ADR-0019 open question 1). **Language strategy (ADR-0020 v2.0.0, Accepted 2026-08-13):** the canonical matrix is Rust-first for NyHAL and compiled-language-only below the platform boundary — this Python backend is the *reference implementation* for platform-critical paths (seccomp enforcement, FUSE ops, container launch, IPC core), which under the **platform-boundary rule** must not depend on the Python interpreter in their shipped form; the **rust/seccomp** (#1) and **rust/syscalls** (#2) modules are **implemented and CI-verified** (details above), while tests, benchmarks, and tooling stay Python (above the boundary).

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

**Outstanding Work:**
- [ ] Direct `clone(2)` FFI with a Rust child entry point (deferred by design — `fork(2)` is the current child-creation primitive; a raw `clone(2)` would need a Rust child entry point, no Python between fork and exec)
- [ ] veth/bridge outbound connectivity for `network=True` containers (requires host CAP_NET_ADMIN/root — the netns is currently a loopback-only isolation boundary with a usable localhost)
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
- ✓ Lightweight notifications    - ✓ **Receive-side capability check** — a message is delivered only to a receiver that holds `CAP_IPC_RECEIVE`, closing the control-plane side of the enforcement gap
    - ✓ **Unix-domain datagram transport** (`ipc/transport.py`, 2026-08-14, plan §4.3): `IPCMessage`s move between processes over `AF_UNIX SOCK_DGRAM` sockets framed by the wire codec (ADR-0020 migration #4 — the framing its parser was built to protect). Sender identity is the kernel's `SCM_CREDENTIALS` (set `SO_PASSCRED`, parse the attached `ucred`): the backend maps the pid to a container, so a wire `sender_id` that does not match the authenticated process is dropped as a forgery, unknown pids are dropped, and senders lacking `CAP_IPC_SEND` are refused — all before delivery. `SO_PEERCRED` does NOT work on datagram sockets (returns `(0,-1,-1)`, verified), so `SCM_CREDENTIALS` is the mechanism. `CALL` carries the caller's reply path in `metadata['reply_path']`; the client correlates `REPLY`s by `reply_to`. Inbound enqueue stays token-bucket rate-limited (ADR-0009). Tested with a **real cross-process** exchange (`TestIPCTransport`): a subprocess client's pid is authenticated by the kernel at the server and the CALL/REPLY round-trips

**Outstanding Work:**
- [ ] Benchmark IPC latency (NPS-003 §6.1)
- [ ] Optimize token-bucket parameters (ADR-0009 tuning-blocked)
- [ ] Shared-memory transport as an alternative/complement to the Unix-domain datagram path
- [ ] Integration with seccomp for IPC syscall filtering

**Conformance Status:** Partial (primitives implemented; transport and performance benchmarking deferred)

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
- [ ] Kernel-module fallback (deferred per ADR-0016)
- [ ] Overlay filesystem for container-specific views
- [ ] Deduplication across snapshots (the current CoW reference-sharing already yields ~49× on a 20%-churn snapshot chain — measured 2026-08-12, `tests/BENCHMARK_RESULTS.md` §10; content-hash dedup of equal-byte different files is the unimplemented remainder)

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
- The direct-syscall launcher is the default path but `unshare(1)` remains available as an opt-in legacy path; a fully Rust-native child entry point (no Python between fork and exec) is future work; network namespaces are opt-in (`network=True`) with loopback-only isolation — veth/bridge outbound connectivity is deferred

## Next Steps

### Immediate (Phase 1: Core Container Primitives)
1. ✓ Direct `unshare(2)`/`fork(2)` container launch (implementation_plan.md §4.1) — landed 2026-08-13; `clone(2)` FFI with a Rust child entry point remains future work
2. ✓ Cgroup freezer for suspension — landed 2026-08-14 (cgroups v2 `cgroup.freeze` with SIGSTOP/SIGCONT fallback)
3. ✓ Network namespace support — landed 2026-08-14 (`network=True` config flag; loopback-only netns brought up by the launcher so 127.0.0.1 works; veth/bridge connectivity deferred — requires host root/CAP_NET_ADMIN)

### IPC (plan §4.3)
1. ✓ Unix-domain datagram transport — landed 2026-08-14 (`ipc/transport.py`: SCM_CREDENTIALS sender authentication, wire-codec framing, CALL/REPLY over sockets, ADR-0009 inbound rate limiting); shared-memory transport deferred
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
