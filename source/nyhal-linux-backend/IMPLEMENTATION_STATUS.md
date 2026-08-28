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

Test suite: **810/810 passing** (`python3 test_backend.py` — 273 run + 26 skipped on hosts without the Rust crates: the seeded seccomp differential test, the syscalls/NyFS/IPC/container conformance classes; all RUN in CI where the crates are built; the real-launch netns isolation tests run on hosts with unprivileged user+network namespaces), including end-to-end container launches (both the direct-syscall and legacy paths, hostile hostnames verified verbatim) and a live FUSE mount verified on this host. **ADR-0021 first increment (Rust IPC serving loop) IMPLEMENTED 2026-08-15:** `rust/ipcd/` (ABI 1.0.0, `libc`-only) is the first NyRuntime-shaped artifact — the loop owns the whole dispatch cycle for the daemon's service socket (poll → recvmsg `SCM_CREDENTIALS` → wire parse → sender authorization → dispatch → sendto reply) inside the Rust process and crosses the FFI boundary once per *batch* (a bounded drain per step) instead of once per message. First-increment scope (the ADR's gate-on-data rule): the built-in `ping` op of the status service with byte-identical reply semantics to the Python floor; non-CALL / non-ping / malformed wire / unknown or forged senders drop at the trust boundary (forged = wire `sender_id` that does not match the kernel-resolved identity — the floor's `_authorized` contract). Authorization policy (pid→container table, trusted uids, operator id) crosses as plain data at loop creation (ABI-001). `ipc/loop.py` is the driver (established search/ABI/force loader contract); 14 crate unit tests + `TestRustIpcdLoader` (8) + `TestIpcdLoopConformance` (3: ping reply ≡ floor — correlation, empty sender/receiver, metadata `{}`, byte-identical payload; 5-ping batch drain in one step; non-ping + forged drops); CI gains the `rust-ipcd` build job and the required `rust-ipcd-conformance` gate. **Measured 2026-08-15 (BENCHMARK_RESULTS.md §22, `--ipcd`): the loop beats the Python floor ~2.8× at the wire median — p50 ~136 µs vs ~387–394 µs (like-for-like ping A/B, cross-process)** — ADR-0021's differential gate is GREEN; the close gate (NPS-003 §6.1 <100 µs) stayed OPEN with the residual being the client-side Python per-call cost — **and the client half LANDED the same day (2026-08-15), closing the gate**: `nyrqis_ipcd_client_call` (sendto → poll → recvmsg → correlation in one FFI call per round trip, thread-local reply buffer) is wired into `IPCClient.call` (Python floor loop = crate-less fallback; a timeout never re-sends the CALL), and the remaining client-side Python was measured and eliminated — the codec's per-field `create_string_buffer` marshalling (encode 31.6→8.1 µs, decode 18.3→13.4 µs), the per-call `json.dumps({})` metadata round trip (constant `b"{}"`), the per-call 64 KiB reply-buffer allocation (thread-local reuse + `string_at`), and the ~6 µs `uuid4` message-id (48-bit CSPRNG `os.urandom(6).hex()`). **Measured §22: the loop's wire p50 is 82–95 µs vs the floor's 263–274 µs — the close gate is MET (both criteria), and ADR-0021 moved to Accepted.** Also landed: `test_daemon_restart_recovers_stale_state` — the plan §4.5 recovery path end-to-end through a real daemon subprocess (stale state pre-seeded → recovery logged + state atomically replaced with the new daemon's identity). Suite 317 → **342** (302 run + 29 skipped on crate-less hosts; all run locally). **Wired into the daemon the same day:** `service serve --health-socket` binds a dedicated health-probe socket served by the Rust loop when the crate is present (operator/trusted-uid policy — containers keep using the main service socket; the loop's per-container pid-table refresh LANDED the same day (`nyrqis_ipcd_loop_set_policy` + the registry's `set_on_change` hook + the host's `_refresh_health_policy` — a container whose pid is in the registry can probe the health socket as itself; the refresh is re-pushed on every spawn/terminate). **ADR-0021 decision point 1 LANDED the same day — the non-ping dispatch handoff:** `rust/ipcd/` queues authorized non-ping CALLs and gains `drain_requests` / `enqueue_replies` / `discard_requests` (plain-data batch boundary; replies routed to the RECORDED sender address); `ipc/dispatch.py` (`IpcdLoopDispatcher`) drains, dispatches through a `ServiceRouter` into a reply sink, and enqueues the reply wires (built with the floor's own codec — byte-identical either way), mirroring the floor's `CAP_IPC_SEND` gate for container senders; the health socket now serves `status`/`health` through the loop (real-container e2e verified). **The MAIN service socket moved behind the loop the same day (ADR-0021's "daemon's service socket" scope fully realized):** `StatusServiceHost.start()` serves `--socket` through the Rust loop when the crate is present — the loop takes the bound fd, the policy starts from the live registry snapshot (refreshed on every spawn/terminate by the single change hook, which now updates EVERY active loop), and the FULL router (status + control) is driven by the dispatch handoff; the floor `IPCDatagramServer` is the crate-less fallback. Control ops (container_run/list/kill) cross the loop's batch boundary — verified end-to-end by the real-container control test, which now exercises the loop path. Suite 347 → 350 (new: backend selection + control ops + container-control denial through the main socket, all backend-agnostic; both paths green locally — loop and crate-less floor). Measured (§23): dispatch ~490 vs ~405 µs p50 (close parity — the Python handler cost is inherent), ping ~2.8× faster unchanged, refresh ~9.6 µs p50). **The Rust-native child entry point LANDED the same day (2026-08-15) — syscalls ABI 1.2.0, ADR-0020 migration #2 completion / plan §4.1:** `nyrqis_syscalls_clone` (real `clone(2)`, per-call mmap'd child stack — glibc's x86_64 clone switches the child's stack even without `CLONE_VM`) + `nyrqis_syscalls_launch_child` (the Rust child entry: root maps, PDEATHSIG, hardened procfs mount, pipe close, `execv` of the launcher — zero Python between fork and exec) — `_spawn_direct_clone` creates the container's PID-1 in ALL its namespaces with ONE FFI call when the crate is present; the Python fork-setup child is the crate-less fallback. The argv marshalling is a `c_void_p` array of raw addresses with the `argc+1` NULL terminator (a `c_char_p` array re-copies into temporary-owned buffers that die, leaving execv reading freed memory — EFAULT/126; pinned by tests). Real container launches verified end-to-end through the clone child (hostname set inside, exit status propagated, network netns path). Suite 350 → **358**; also fixed the test race the faster clone path exposed: `test_init_forwards_sigterm_to_command` waits for the init's `SigCgt` mask before signaling (kernel PID-1 semantics discard signals sent before a handler is installed). Measured (§24, `--ipcd-control`): a REAL main-socket control op (`status`) through the loop is close parity with the floor — p50 ~336–342 vs ~290 µs (+16–18%), the Python handler cost being inherent on both sides) and by the floor's status service otherwise (byte-identical ping replies, so a probe cannot tell which backend answered); the systemd unit passes `--health-socket /run/nyrqis/health.sock`. The health path never contends with container traffic on the main socket, and a probe round trip runs through the loop (~2.8× faster at the median, §22). Verified end-to-end through a real daemon subprocess (both sockets answer ping; health socket unlinked on stop). New tests: `test_host_health_socket_serves_ping` (loop + floor paths), `test_cli_service_serve_wires_health_socket`, `TestSystemdUnit` flag assert. **ADR-0020 migration #1 (seccomp) IMPLEMENTED 2026-08-13:** the Rust policy compiler (`rust/seccomp/`) compiles policies to classic BPF, validates programs, and simulates verdicts; CI builds and unit-tests it on every push, its golden programs are **byte-identical** to the pure-Python compiler's, and the forced-mode conformance gate (`NYRQIS_RUST_FORCE=1`, CI `rust-seccomp-conformance`) runs the full suite through the FFI and is **green and a required, blocking job** — a semantic regression in the Rust compiler now fails the build. **Migration #2 (syscalls) IMPLEMENTED 2026-08-13, INCLUDING the direct-syscall launcher:** `rust/syscalls/` (ABI 1.1.0) ships `sethostname`/`prctl`/`unshare`/`mount`/`mount_proc` wrappers (0 or -errno) behind the versioned FFI surface; `backend/rust_syscalls.py` is the shared loader (search order, ABI check, ctypes fallback, `NYRQIS_RUST_FORCE=1`) wired into `launcher.py`'s `set_hostname` (FIND-BACKEND-004) and into `container.py`'s **direct-syscall launcher** (implementation_plan.md §4.1): the manager forks a namespace-setup child that performs the `unshare(2)` dance (`CLONE_NEWUSER` + root maps → `NEWNS|NEWUTS|NEWIPC` → `NEWPID`), the container's PID-1 mounts a hardened procfs via the no-arg `mount_proc` FFI (post-fork, pre-exec — zero Python allocation) and execs the launcher, and the setup child relays the container PID through a pipe and exits with its status so `wait()` keeps Popen-compatible semantics. The caller's uid/gid are captured before `CLONE_NEWUSER` (inside the unmapped namespace `getuid()` reports 65534 — the classic map-write failure). `unshare(1)` remains only as an opt-in legacy path (`use_direct_syscalls=False`). **ABI 1.2.0 (2026-08-15) completes the child-creation story: `clone(2)` FFI + a Rust child entry point (`nyrqis_syscalls_clone` + `nyrqis_syscalls_launch_child`) — one FFI call creates the container's PID-1 directly in all its namespaces with zero Python between fork and exec; the Python fork-setup child is the crate-less fallback (see the §4.1 status line).** The `rust-syscalls-conformance` CI gate runs the syscalls-facing test classes forced through the FFI and is **required and blocking**. **Migration #3 (NyFS block codec) IMPLEMENTED 2026-08-13:** `rust/nyfs/` (ABI 1.0.0) ships the storage hot paths — SHA-256 per-block checksum (NPS-004 §4) and Zstandard compress/decompress-with-verify (ADR-0007, level 3 default) — behind the versioned FFI surface; `fuse/nyfs_codec.py` is the loader (search order, ABI check, hashlib/zstandard fallbacks, `NYRQIS_RUST_FORCE=1`) and `NyFSBlock` routes `compute_checksum`/`compress`/`decompress` (now verified on read) through it. The extraction boundary is the benchmark evidence (BENCHMARK_RESULTS.md §5: read-path verification dominates NyFS read cost). The `rust-nyfs` build job and the required `rust-nyfs-conformance` gate (the differential test — Rust ≡ pure-Python floor on checksums, roundtrips, and integrity failures — run forced through the FFI) are green in CI. **Migration #4 (IPC wire codec) IMPLEMENTED 2026-08-13:** `rust/ipc/` (ABI 1.0.0, dependency-free) ships the binary message framing `IPCMessage` will transport cross-process (NPS-003 §3 / NPS-017 §4.3) — a canonical length-prefixed format whose parsing is the future transport's trust boundary; `ipc/ipc_codec.py` is the loader (search order, ABI gate, `struct` floor, `NYRQIS_RUST_FORCE=1`; `-4097` invalid-wire maps to the floor's `ValueError`), wired as `IPCMessage.to_wire()`/`from_wire()`. The `rust-ipc` build job and the required `rust-ipc-conformance` gate (Rust ≡ Python floor, byte-identical wire, field-for-field decode, same malformed-input rejection) are green in CI. **Migration #5 (container launch-plan primitives) IMPLEMENTED 2026-08-13:** `rust/container/` (ABI 1.0.0, `libc` the only dependency) ships the pure, well-bounded launch-plan computations — the launcher argv (FIND-BACKEND-004: hostname and command are argv entries, never shell-interpolated), the cgroup v1/v2 resource plan (FIND-BACKEND-003: `notify_on_release=0`), the `--map-root-user` uid/gid maps, and the NPS-010 §4 lifecycle state machine — behind the versioned FFI surface; `backend/container_codec.py` is the loader (search order, ABI gate, byte-identical `struct` floor, `NYRQIS_RUST_FORCE=1`; `-4097` malformed flat → the floor's `ValueError`, `-4098` invalid transition → `False`), wired into `transition_to`, `_launcher_args`, `_cgroup_v1_plan`, `_setup_cgroups_v2`, and the direct-syscall child's root maps. The `rust-container` build job and the required `rust-container-conformance` gate (the container-facing classes — including the end-to-end launch tests — forced through the FFI) are green in CI. **Cgroup freezer for suspension IMPLEMENTED 2026-08-14** (implementation_plan.md §4.1): `suspend()` freezes the container's **whole cgroup** via the cgroups v2 `cgroup.freeze` control (write `1`, confirmed best-effort via `cgroup.events`' `frozen 1`) when the container is attached to a v2 cgroup, so descendants and future forks cannot outrun the suspension; `resume()` thaws (write `0`); `terminate()` thaws first so SIGTERM gets its graceful window (a frozen cgroup defers non-SIGKILL signals). SIGSTOP/SIGCONT remains the fallback for v1 hosts (no unified freezer — the legacy `freezer` controller is not provisioned), failed cgroup setup, and failed freeze writes; a **failed thaw raises** instead of falling back — a frozen cgroup defers every signal except SIGKILL, so a SIGCONT fallback would report RUNNING for a process the kernel still holds frozen (the caller retries or escalates to `terminate()`, whose SIGKILL still applies). New `TestContainerFreezer` (12 tests) covers the control-file decision, freeze/thaw writes, every fallback, the terminate-thaw ordering, and an end-to-end real-process signal suspend/resume. **Network namespace support IMPLEMENTED 2026-08-14** (implementation_plan.md §4.1): `ContainerConfig.network=True` (opt-in, default off — the default container keeps sharing the host netns) gives the container its own network namespace via `CLONE_NEWNET` in the direct-syscall child's mount/UTS/IPC unshare (and `--net` on the legacy `unshare(1)` path), so it sees only loopback — a pure isolation boundary; outbound connectivity (veth/bridge) is deliberately future work. `CLONE_NEWNET` is exposed through the existing `unshare(flags)` FFI (no crate change — flags are passed through raw). **Loopback brought up 2026-08-14:** the launcher (step 2b, before the seccomp install) best-effort `SIOCSIFFLAGS`s `lo` up — in an owned netns (where the container is root in the owning user namespace) it succeeds, so a `network=True` container has a usable 127.0.0.1; sharing the host netns it EPERMs harmlessly (host `lo` is already up). Verified end-to-end: a netns container granted `CAP_NETWORK_SOCKET`/`CAP_NETWORK_BIND` (and `CAP_FILESYSTEM_WRITE` to write the marker under the active filter) binds 127.0.0.1 through the real launch path. Verified end-to-end on this host: a `network=True` container's netns inode differs from the host's and its own procfs lists only `lo`; the default container's netns equals the host's (`TestNetworkNamespaceIsolation`, skip-gated on a real-launch probe). **IPC transport hardened + verified container→service 2026-08-14:** `ipc/transport.py` now authenticates purely receiver-side — the sender attaches nothing and `SO_PASSCRED` makes the kernel attach the real `SCM_CREDENTIALS` `(pid, uid, gid)` to every inbound datagram, so the sender cannot influence its identity at all (and the namespace-scoped pid/uid problem of explicit credentials inside a container is gone). The attached identity is the sender's **global** pid and **real** uid/gid — probe-verified 2026-08-14: a sender in an unprivileged new pid+user namespace presented its host pid and host uid/gid to the receiver, not its namespace-local pid (1) or mapped root — so the backend's host-pid registry works for containerized senders. Verified **end-to-end through a real container**: a `network=True` container granted the network + filesystem caps runs an `IPCClient` under the active seccomp filter and completes a kernel-authenticated CALL/REPLY with a host-side service (`test_container_ipc_call_service`, suite 238 → 239). **Over-transport latency measured 2026-08-14** (BENCHMARK_RESULTS.md §20, `--ipc-transport`): cross-process CALL/REPLY p50 188.79 µs / p95 295.23 µs / p99 373.51 µs vs 87.28 µs in-process — NPS-003 §6.1's <100 µs gate is **NOT met** at the median over the real transport, so NPS-003 stays Draft with the ADR-0020 Rust transport as the documented close path. **ADR-0020 migration #6 (IPC transport hot path) IMPLEMENTED 2026-08-14:** `rust/transport/` (ABI 1.0.0, `libc` the only dependency) ships the per-message syscall half of the Unix-domain datagram transport — `sendto`, `poll`+`recvmsg` with `MSG_DONTWAIT` (never blocks past the timeout, safe on blocking and non-blocking fds), and the `SCM_CREDENTIALS` parse yielding the sender's global `(pid, uid, gid)` and bound path — behind the versioned FFI surface; `ipc/transport_codec.py` is the loader (search order, ABI gate, `BackendUnavailable` → Python-floor fallback, `NYRQIS_RUST_FORCE=1`; `-4096` internal → `RuntimeError`) wired into `UnixDatagramEndpoint.send`/`receive` for raw frames (the wire codec, migration #4, still owns framing). Binding/0700/`SO_PASSCRED` stays on the floor. The `rust-transport` build job and the required `rust-transport-conformance` gate (the transport loader + differential classes — the FFI-driven endpoint reproduces the floor's contract: payload, kernel creds, sender path, timeout — forced through the FFI) land with this push; the crate is the documented close path for the NPS-003 §6.1 gate. **Benchmark delta measured 2026-08-14 on the build host** (BENCHMARK_RESULTS.md §20): the v1 FFI surface (per-recv malloc of the wire + sender-path buffers) was slower than the Python floor at the median (wire p50 ~426 µs Rust vs ~231 µs floor; +23 µs per raw round trip isolated). **FFI surface v2 (ABI 2.0.0, same day) fixes the allocation pathology and measured 2026-08-14: `recv` now `recvmsg`s directly into the caller's reusable wire buffer (zero copy, zero malloc/free — `nyrqis_transport_free` is gone) and `send` passes the immutable wire bytes by pointer; the endpoint owns one buffer pair per socket.** Wire p50 307–357 µs across four runs (~28% under v1, ~1.6× the ~200 µs floor) and isolated p50 24.3 µs vs floor 9.5 µs. The residual is the ctypes boundary tax (two calls with eleven marshalled args per round trip + the unavoidable copy into immutable Python bytes) — the honest floor of any compiled transport driven from Python, not an allocation bug. The migration stands on ADR-0020's platform-boundary rule and the byte-identical conformance gate; NPS-003 remains Draft with the gate open — closing it needs the serving loop itself behind the boundary (the NyRuntime direction). Suite 239 → 251 (225 run + 26 skipped: the 3 transport-conformance tests add to the skip set on hosts without the crate). **Auto-maintained sender registry IMPLEMENTED 2026-08-14:** `ipc/registry.py` (`ContainerIpcRegistry`) is the pid → container_id mapping the server authenticates against, kept in sync automatically — `ContainerManager(ipc_registry=...)` registers each direct-syscall container's host pid at spawn (`container.pid` is the command's host pid — probe-verified that `SCM_CREDENTIALS` reports the sender's global pid even from inside a new pid namespace — so it IS the kernel-attached sender pid) and unregisters on terminate/wait; the legacy `unshare(1)` path is deliberately not tracked (the command runs as a grandchild with a different pid; its datagrams fail closed). The container→service e2e now uses the auto-registry end-to-end (no manual `pid_registry`), and `TestContainerIpcRegistry` (6 tests) pins the registry + manager hooks. Suite 257 → 265 (239 run + 26 skipped). **First real backend service on the transport IMPLEMENTED 2026-08-14** (`ipc/service.py`, `BackendStatusService`): a container-facing status endpoint attached to an `IPCDatagramServer` — `ping` (whole-chain check) and `status` (capability-gated on `CAP_SYSTEM_INFO`, denied fail-closed without a `CapabilityManager`; reports backend version, uptime, and the caller's own identity and capability set); the server's CALL dispatch swallows handler exceptions so a service bug becomes an `internal error` reply instead of killing the serving thread. Verified end-to-end through a REAL container (`test_container_calls_status_service`). **Runnable status-service daemon + control-plane capability lifecycle IMPLEMENTED 2026-08-14:** `ContainerManager(..., capability_manager=...)` now initializes each spawned container with its default capability set (so it can authenticate with CAP_IPC_SEND and call capability-gated services) and revokes the grants on every terminate/wait path (NPS-010 §5); `nyrqis_backend.py service serve` runs a `StatusServiceHost` daemon that owns the shared registry + capability manager + container manager and serves the status service until SIGINT/SIGTERM. The status e2e now proves the WHOLE chain automatically — a real container spawns, is registered AND granted by its manager, and completes a status CALL with zero manual bookkeeping. Verified through the RUNNABLE daemon itself: `test_host_container_completes_status_call` spawns a real container via the `StatusServiceHost`'s own `ContainerManager` and it completes the status CALL against the daemon's own server — the operator flow end-to-end. **PID-1 launcher-init IMPLEMENTED 2026-08-14:** the launcher no longer execs the container command — it stays alive as the namespace's PID-1 **init**, forks the command as a plain child, forwards supervisor signals (SIGHUP/INT/QUIT/TERM/USR1/USR2/WINCH), reaps it, and exits with its status (or dies by its signal, preserving Popen-compatible `wait()` semantics). Why: Linux discards signals (other than SIGKILL/SIGSTOP) sent to a namespace PID 1 that has no handler installed, so a command running AS PID 1 could never be terminated gracefully — the old design always burned the full 10s SIGTERM window and escalated to SIGKILL (verified empirically). With the init, SIGTERM reaches the command directly and `terminate()` completes in milliseconds; `kill -TERM <pid-1>` forwards too. The init runs **unfiltered** by design (the model tini uses): the seccomp policy is applied by the command child before its exec, so a container without `CAP_PROCESS_SPAWN` cannot EPERM the init's own fork, and the command no longer inherits Python's SIG_IGN SIGPIPE/SIGXFSZ (the init resets them to default). The manager resolves the command's HOST pid itself (a pid reported from inside the namespace is ns-local) by polling the init's `/proc/<pid>/task/<pid>/children` — the init's only direct child — so `container.pid` stays the real command for kill/cgroup/registry. Both pids are attached to the container's cgroups (the init's memory can no longer escape accounting); `terminate()` escalation SIGKILLs both, and the setup child is reaped best-effort so a killed container leaves no zombie. New `TestPid1Init` (7 tests): the command is a plain child (pid 2 in-namespace) of the init (pid 1), SIGTERM terminates in <3s, signals to the INIT forward to the command, exit statuses propagate through the init, a fast-exit command spawns and reports its status with no zombie, the host-pid relay never touches the environment, and the legacy `unshare(1)` path still runs the command through the init. Suite 288 → 295 (269 run + 26 skipped). A deadline-expiry where the init is still alive (a launcher that never forked the command) is torn down and the spawn fails — a live container is never silently orphaned. **Control plane LANDED 2026-08-14:** the daemon's own user can drive it over the same transport — `IPCDatagramServer` gains a trusted-uid operator path (`trusted_uids`, `host-operator`; container-FIRST, so daemon-spawned containers are never misattributed), `ServiceRouter` dispatches multiple services on one socket (payload `service` field, default `status` for back-compat), and the operator-only `ControlService` spawns/lists/kills containers through the daemon's `ContainerManager` (`nyrqis_backend.py control container-run|container-list|container-kill`). Verified end-to-end: `test_host_control_plane_runs_and_kills_container` drives a REAL container through the wire. Suite 276 → 288 (262 run + 26 skipped); the PID-1 init brings it to 295 (269 run + 26 skipped). **Daemon lifecycle implemented per `DAEMON_LIFECYCLE.md`:** dirty-flag tracking, `NyFSMount.shutdown()` (dirty-gated final commit → unmount), SIGINT/SIGTERM handlers in blocking mode, and `auto_compact` is now the mount default (AG tuning review still pending per ADR-0019 open question 1). **Language strategy (ADR-0020 v2.0.0, Accepted 2026-08-13):** the canonical matrix is Rust-first for NyHAL and compiled-language-only below the platform boundary — this Python backend is the *reference implementation* for platform-critical paths (seccomp enforcement, FUSE ops, container launch, IPC core), which under the **platform-boundary rule** must not depend on the Python interpreter in their shipped form; the **rust/seccomp** (#1) and **rust/syscalls** (#2) modules are **implemented and CI-verified** (details above), while tests, benchmarks, and tooling stay Python (above the boundary). **Operator CLI LANDED 2026-08-15 (`nyrqisctl.py`):** a standalone, user-facing control-plane client over the daemon's main socket — `ping`/`status`/`health` (status service) and `containers list|run|kill` (control service), human-readable by default with `--json` for raw replies and a clean no-daemon error (both client halves: the floor's `None` and the Rust half's `ENOENT`/`ECONNREFUSED` map to one message). The status service gained the **operator carve-out**: `status`/`health` were `CAP_SYSTEM_INFO`-gated with no operator path, so the daemon's own user could not read its own health through the wire — the operator (kernel-authenticated trusted-uid process, which has full control of the daemon anyway) is now authorized outright, the same model the control service uses; container callers stay capability-gated fail-closed. New `TestOperatorCli` (10 tests, incl. a real-container run/list/kill e2e through the CLI). Suite 358 → **368**. **The launcher-init moved behind the platform boundary the same day (ADR-0020) — the container's PID-1 is now a compiled binary (`rust/launcher/`, `nyrqis-launcher`):** hostname, cgroup-mount hardening, loopback bring-up, SIGPIPE/SIGXFSZ reset, fork + seccomp install (prctl) + exec, signal forwarding, reaping, signal-death propagation, orphan sweep — all in Rust, so the path from clone(2) to the container command is zero Python. The seccomp POLICY COMPILATION stays in the backend (the allowlist tables live there): the manager serializes the compiled classic-BPF program to a `--bpf-file` the binary installs; `backend/rust_launcher.py` locates the binary (override → crate target → PATH; `NYRQIS_LAUNCHER_FORCE=1` gate; deliberately UNCACHED so a vanished binary can never be exec'd). Python `launcher.py` is the crate-less fallback. New CI jobs `rust-launcher` (build + 10 unit tests) and the required `rust-launcher-conformance` gate. Verified end-to-end through real containers: exit status 7 propagated, UTS hostname set, the container's seccomp filter ACTIVE (default-cap file create denied), SIGTERM to the init forwarded (wait → 128+15). Suite 368 → **382**. **Same day: `nyrqisctl --health-socket`** (ping/status/health on the ADR-0021 health socket; control refuses it), **packaging** (man page + bash/zsh completion), and **ADR-0022 drafted (Proposed)** — NyVault as a daemon-hosted storage service on the IPC transport (capability-gated volume handles, FUSE passthrough byte path, key management deferred to a follow-on ADR). **Next round (2026-08-15): strict seccomp, the NyVault storage service first increment, the cold-start benchmark, and ADR-0023.** `ContainerConfig.strict_seccomp=True` installs the container's filter with `SECCOMP_FILTER_FLAG_LOG` removed (hard kill on violation instead of logged-and-continue) — wired through BOTH launcher paths (`--strict-seccomp` on the compiled init and on `launcher.py`), riding only when a filter is actually installed (`seccomp=True`); new test pins both argv paths. **The NyVault storage service LANDED as a real backend service (ADR-0022 first increment):** `ipc/storage.py` (`StorageService`) registers on the daemon's router with the lifecycle ops `volume_create` / `volume_open` / `volume_list` / `volume_close` / `volume_info` (the ADR's byte-path ops — read/write/snapshot, the FUSE passthrough — are the next increment), every op gated on the new **`CAP_STORAGE_VOLUME`** capability at the same enforcement point as `CAP_SYSTEM_INFO` (fail-closed), a per-creator volume registry, and REAL NyFS backing — `volume_create` constructs a `NyFSFilesystem` root for the volume. Wired into the daemon host alongside status/control (operator authorized outright, container calls capability-gated — the ADR-0022 trust model); `TestStorageService` (7 tests: operator lifecycle with NyFS backing, container capability gate, fail-closed without a capability manager, duplicate/unknown rejection, creator scoping, main-socket serving on floor + loop paths). Suite 382 → **390**. **Container cold-start A/B measured (§25, `--launcher-coldstart`):** the compiled `nyrqis-launcher` beats the Python launcher in every run and at every percentile — Python p50 stable at 152–157 ms, compiled p50 6.3–53.7 ms across runs (userns-clone/scheduler noise; p95 ~55 ms in every run, ~3× faster than the Python p50) — the interpreter-spin-up gap on the platform-critical path, now closed by default with Python only as the crate-less fallback. **ADR-0023 drafted (Proposed):** the NyVault key manager — envelope encryption (per-volume XChaCha20-Poly1305 DEKs wrapped by a daemon-held KEK), the KEK never stored in plaintext (Argon2id passphrase unlock default; TPM2/PKCS#11 hardware backends behind a Rust trait, deferred), crypto-shredding revocation, rotation without re-encryption, and key custody in a Rust crate behind the FFI boundary — Python holds opaque handles only, never plaintext keys (ADR-0020's rule on the most sensitive path); approves libsodium as the first non-libc dependency for the keys crate. **Next round (2026-08-15): the NyVault byte path, the operator vault CLI, and the key manager landed.** The storage service gained `volume_write` / `volume_read` / `volume_snapshot` / `volume_snapshots` — REAL NyFS I/O through the capability-gated creator-scoped handles: create-on-write with mkdir -p (a blob store), offset writes overwrite in place (CoW), reads page with offset/size, snapshots ride NyFS's CoW (old data survives an overwrite — verified), `..`/trailing-slash paths rejected, per-call payloads capped at 32 KiB (the 64 KiB datagram budget; streaming is the FUSE-passthrough increment), registry-only volumes refuse the byte ops, and `volume_open` opens by NAME too. **`nyrqisctl vault`** (`create|open|list|close|write|read|snapshot|snapshots`) drives it all — write from `--file`/stdin, read raw bytes to stdout or `--output`, health socket refused — verified end-to-end against a REAL daemon (create → open → write → read byte-identical → offset write → snapshot → snapshots → close). **The key manager LANDED (ADR-0023 first increment):** `backend/keys.py` — the pure PyNaCl floor (Argon2id KEK derivation at p=1, XChaCha20-Poly1305 envelope, deterministic 110-byte KEK envelope with an AEAD check value, per-volume DEK wrap/unwrap, fail-closed on wrong secret and tampering) AND the loader (search order, ABI 1.0.0 gate, `NYRQIS_KEYS_LIB` override, `NYRQIS_RUST_FORCE=1` gate, floor fallback, uncached-until-first-load scan). **`rust/keys/`** is the custody boundary — the same construction in Rust (RustCrypto argon2 + chacha20poly1305, the ADR's approved non-libc dependencies; the crate is the seam, not the vendor), with the KEK held ONLY in the crate's handle table: `unlock` returns an opaque u64 handle and the plaintext KEK never crosses FFI. **Differential conformance verified:** Argon2id bytes and wrapped-DEK bytes byte-identical to the floor, cross-implementation blob interop both ways, wrong-secret/tamper rejection on both, handle shred invalidates. New CI jobs `rust-keys` (build + 6 unit tests) + the required `rust-keys-conformance` gate. Suite 390 → **412**. Wiring the KEK into `volume_create` (per-volume wrapped DEKs) is the next increment — at-rest encryption is NOT yet claimed.

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
