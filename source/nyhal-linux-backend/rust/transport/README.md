# Rust IPC transport — FFI boundary contract and conformance status

**Status: IMPLEMENTED 2026-08-14.** The Rust transport hot path
(`src/lib.rs`) implements the per-message syscall half of the
cross-process IPC transport (`ipc/transport.py`): one `sendto` per
outbound frame, one `poll`+`recvmsg` per inbound frame, and the
`SCM_CREDENTIALS` ancillary parse that yields the sender's real
`(pid, uid, gid)`. CI (`.github/workflows/ci.yml`) builds and unit-tests
the crate on every push; the dev host has no Rust toolchain, so **CI is
the compiler and the test runner for this crate**. The pure-Python
implementation (`ipc/transport.py` socket floor) remains the shipped
correctness floor that the tests run against on hosts without the crate.

## Why this module now

ADR-0020 priority #4 (networking = Rust) is gated on the Python IPC
semantics being stable and benchmarked — they are, and the real
transport shipped with its gate data point (`tests/BENCHMARK_RESULTS.md`
§20, 2026-08-14: p50 188.79 µs over the wire vs 87.28 µs in-process).
The extraction boundary is the **transport hot path**: the syscall
round-trip around the wire codec (migration #4, which already owns
framing/parsing — the parsing trust boundary). The measured gap over
the in-process control plane is dominated by two process hops of Python
per-message overhead around the syscalls; this crate removes the Python
`recvmsg`/CMSG parse and `sendto` framing from the measured path. The
benchmark re-run with the crate active is the NPS-003 §6.1 (<100 µs)
close-path evidence (the next data point once a host has the built
crate).

## What stays on the Python floor

Path management is deliberately NOT here: binding (0700 perms,
`SO_PASSCRED`, unlink on close) stays on `UnixDatagramEndpoint`, which
passes the bound socket's fd in. The crate never sets socket options
and never touches the wire codec — it moves opaque frames and
kernel-attached identity across the process boundary.

## FFI surface (the ABI rule of ADR-0020 / ABI-001)

Versioned, plain-data entry points, no shared mutable state, no
pointers into Python objects.

**FFI surface v2 (ABI 2.0.0, 2026-08-14): caller-supplied output
buffers.** The v1 surface (`nyrqis_transport_free` ownership contract)
was measured slower than the Python floor (BENCHMARK_RESULTS.md §20:
+23 µs per raw round trip isolated — per-recv `libc::malloc` of the
wire buffer AND a sender-path C string, plus per-message copies). v2
removes the allocation entirely: `nyrqis_transport_recv` `recvmsg`s
**directly into the caller's wire buffer** (the `iovec` points at it —
zero intermediate copy) and writes the sender path into the caller's
path buffer; lengths/creds return through out params. The Python
caller (`UnixDatagramEndpoint`) owns one reusable buffer pair per
endpoint, so the hot path does zero allocations and zero frees. The
`nyrqis_transport_free` symbol is GONE.

| Entry point | Args | Returns |
|-------------|------|---------|
| `nyrqis_transport_version` | — | u32 ABI version (0x0002_0000) |
| `nyrqis_transport_send` | fd, wire*, wire_len, peer_path* | 0, -errno, or internal |
| `nyrqis_transport_recv` | fd, timeout_ms, wire_buf*, wire_cap, out_wire_len*, path_buf*, path_cap, out_path_len*, pid*, uid*, gid* | 0, -errno, or internal (0 with *out_wire_len == 0 = timeout) |

**Error contract** (`NyrqisErr` codes, negative i32):

- `-errno` (1..=4095) — real failures (`EINVAL` for null args/bad path,
  `EFBIG` beyond the 16 MiB frame bound, `ENOENT` for a missing peer).
- `ERR_INTERNAL` (`-4096`) — module failure; outside the errno range by
  design, maps to `RuntimeError` on the Python side.

**Timeout semantics:** `recv` `poll`s first and `recvmsg`s with
`MSG_DONTWAIT`, so it never blocks past `timeout_ms` (negative = block
until data) and is safe on both blocking and non-blocking fds. A
timeout returns 0 with `*wire_len = 0`.

The loader (`ipc/transport_codec.py`) raises `BackendUnavailable` when
the crate is absent and the endpoint falls back to the Python floor —
the Python path is the correctness floor — and `NYRQIS_RUST_FORCE=1`
turns routing failures into errors (the conformance gate's guarantee
that every call drives this module).

## Conformance evidence

1. **Crate unit tests** (`cargo test`): `sockaddr_un` packing bounds
   (empty/oversized paths), invalid-arg rejection, a real same-process
   datagram round-trip asserting the frame bytes, the kernel-attached
   `(pid, uid, gid)` (equal to `getpid/getuid/getgid`), the sender's
   bound path, and the timeout-with-no-data path.
2. **Differential conformance** (`TestTransportConformance` in
   `test_backend.py`): the FFI-driven endpoint must reproduce the
   Python floor's contract exactly — payload round-trip, kernel-attached
   credentials, sender path, timeout semantics, and error surfacing.
   Runs wherever the crate is built (the CI conformance job); skipped
   on hosts without it. Uses raw wire bytes only, so the transport-only
   gate never trips the separate ipc-codec loader's force check.
3. **Forced-mode conformance gate.** The CI `rust-transport-conformance`
   job builds the crate and runs the transport test classes
   (`TestTransportRustLoader`, `TestTransportConformance`) with
   `NYRQIS_RUST_FORCE=1` and `NYRQIS_RUST_LIB` set to the built cdylib.
   The job is **required and blocking** — a semantic regression in the
   Rust transport fails the build.
4. **The whole suite stays green both ways.** 251/251 locally (228 run,
   23 skipped without the crates); the endpoint, transport, and
   container→service end-to-end tests pass unchanged on the floor path.
