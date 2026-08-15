---
title: NyRuntime Direction — IPC Serving Loop Behind the FFI Boundary
document_id: ADR-0021
version: 1.0.0
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-08-14
updated: 2026-08-15
ai_assisted: true
depends_on: [NPS-003, ADR-0020, ABI-001, ADR-0009]
---

> **Status (2026-08-15):** the first increment LANDED — `rust/ipcd/`
> (ABI 1.0.0) + `ipc/loop.py` + the differential conformance gate
> (CI `rust-ipcd-conformance`, required) + the `--ipcd` benchmark.
> Measured on the build host (BENCHMARK_RESULTS.md §22): the loop
> beats the Python floor ~2.8× at the wire median (p50 ~136 µs vs
> ~387–394 µs) — the differential gate is GREEN. **The same day the
> loop was wired into the daemon:** `service serve --health-socket`
> serves a dedicated health-probe path through the loop; the floor
> serves the health socket on crate-less hosts with byte-identical
> replies. **The per-container pid-table refresh LANDED the same day:**
> `nyrqis_ipcd_loop_set_policy` (the policy refresh FFI entry, policy
> behind a `Mutex` — refresh while the drive thread is stepping), the
> registry's `set_on_change` hook, and the host's
> `_refresh_health_policy`, so a container whose pid enters the
> registry can probe the health socket as itself (operator/trusted-uid
> policy PLUS the pid table, re-pushed on every spawn/terminate).
> **Decision point 1 LANDED the same day — the non-ping dispatch
> handoff:** authorized non-ping CALLs are queued and handed to Python
> as plain data (`nyrqis_ipcd_loop_drain_requests` → the driver
> dispatches through the Python service handlers → reply wires built
> with the floor's own codec come back through
> `nyrqis_ipcd_loop_enqueue_replies`, routed to the RECORDED sender
> address; unanswered requests reaped by `discard_requests`). The
> health socket now serves `status`/`health` through the loop
> (verified end-to-end by a real container), with the floor's
> `CAP_IPC_SEND` gate mirrored in the driver. Measured (§23): the
> dispatch path reaches close parity with the floor (~490 vs ~405 µs
> p50 — the Python handler cost is inherent per this ADR's design)
> while ping stays ~2.8× faster; the pid-table refresh costs ~9.6 µs
> p50. The close gate (NPS-003 §6.1 <100 µs median) stays OPEN: the
> residual is the client-side Python per-call cost, which is the next
> increment (the client half of the loop behind the boundary). The
> floor remains shipped; this ADR stays Proposed until the close gate
> is met.

# ADR-0021 — NyRuntime Direction: The IPC Serving Loop Behind the FFI Boundary

## Context

NPS-003 §6.1 sets a <100 µs round-trip latency gate for IPC. The
in-process control plane meets it (p50 87–92 µs); the real cross-process
Unix-domain datagram transport does not (BENCHMARK_RESULTS.md §20). Two
FFI surfaces of the Rust transport (ADR-0020 migration #6,
`rust/transport/`) have been measured on the build host:

| Surface | Isolated p50 | Wire p50 |
|--------|-------------|----------|
| Python floor | 9.1–9.5 µs | 195–231 µs |
| v1 (per-recv `libc::malloc` + `free`) | 32.50 µs | ~426 µs |
| v2 (caller-supplied buffers, zero-copy send) | 24.33 µs | 307–357 µs |

v2 removed the allocation pathology and cut ~120 µs off the wire p50
(~28%), but the crate still does NOT close the gate: it remains ~1.6×
the floor at the wire median and ~2.6× isolated. The residual is the
**ctypes boundary tax** — two FFI calls with eleven marshalled
arguments per round trip, the per-send path encode, and the unavoidable
copy into immutable Python bytes. This is the honest floor of any
*per-message* FFI transport driven from Python: as long as the dispatch
loop (`poll` → `recvmsg` → parse → authorize → route → reply) lives in
Python and crosses the boundary once per message in each direction, the
boundary tax is paid on every hop.

The platform-boundary rule (ADR-0020, normative) already obliges the
shipped transport to not depend on the Python interpreter. Migration #6
satisfied that rule at the syscall level. This ADR records the next,
evidence-driven step: **the serving loop itself moves behind the
boundary** — the direction ADR-0020's component map calls **NyRuntime**
(the Rust runtime layer that hosts the platform's services).

## Decision

Adopt the NyRuntime direction for the IPC close path: the next Rust
artifact is not a faster per-message FFI — it is a **Rust IPC serving
loop** (`rust/ipcd/` or equivalent) that owns the whole dispatch cycle
for the daemon's service socket:

1. **Own the loop**: `poll` on the socket fd, `recvmsg` (v2
   caller-buffer contract), wire-codec parse, sender authorization
   against a caller-supplied pid→container table, capability check,
   service dispatch (CALL → handler → REPLY), all inside the Rust
   process loop. Python provides the *policy data* (registry, grants,
   service handlers) across the boundary — not the per-message
   execution.
2. **Batch the boundary**: instead of one FFI call per message each
   way, the loop crosses the boundary once per *batch* (a bounded
   number of drained datagrams), exchanging only plain data — no
   pointers into Python objects (ABI-001).
3. **Keep the Python transport as the reference floor**: the existing
   `ipc/transport.py` + `rust/transport` v2 remains the byte-identical
   fallback and the differential against which the loop is
   conformance-tested (the migration rule's evidence bar, ADR-0020).
4. **Gate on data**: the loop lands only when its wire-level p50 beats
   the floor in a same-session A/B AND meets NPS-003 §6.1's <100 µs
   median — the same evidence discipline that produced this ADR.

### What moves and what stays

| Concern | Stays on the Python floor | Moves behind the boundary |
|--------|--------------------------|---------------------------|
| Socket binding, 0700 perms, `SO_PASSCRED`, unlink | ✓ | — |
| `poll` + `recvmsg` + `SCM_CREDENTIALS` parse | — | ✓ |
| Wire-codec parse (trust boundary) | — | ✓ (already `rust/ipc`) |
| Sender authorization (pid→container, capability check) | policy data only | ✓ execution |
| Token-bucket rate limiting (ADR-0009) | parameters | ✓ execution |
| Service handlers | ✓ (caller-supplied via boundary) | dispatch only |

### What this is NOT

- **Not a rewrite for style**: this is the boundary rule plus measured
  evidence (the §20 A/B) — the migration rule's two allowed
  justifications (ADR-0020).
- **Not a new IPC mechanism**: same `AF_UNIX SOCK_DGRAM`, same wire
  codec, same kernel-attached identity. The shared-memory alternative
  remains deferred (NPS-003).
- **Not the whole NyRuntime**: this is the first NyRuntime-shaped
  artifact, scoped to the transport close path.

## Alternatives considered

1. **Third FFI pass on the per-message surface** (pre-encoded paths,
   marshal thinning): recovers maybe a few µs; the boundary is crossed
   twice per round trip regardless — structurally bounded below by
   ~2× the tax. Rejected: cannot reach <100 µs from the measured
   trajectory.
2. **Keep the Python floor as shipped**: violates the platform-boundary
   rule for the shipped transport and leaves the gate open. Rejected.
3. **Shared-memory transport**: removes syscalls entirely but is a new
   mechanism with its own zeroing requirement (NPS-003 v1.1.0,
   FIND-CONTAINER-003) and does not address the boundary-tax argument;
   remains the complement, not the close path. Deferred.

## Consequences

- **Positive**: the boundary tax is paid per batch, not per message;
  the shipped transport stops depending on the interpreter per message;
  the loop is the first NyRuntime artifact and the pattern for the
  other hot paths (seccomp install, FUSE ops, container launch).
- **Negative**: real work — a new crate, a batch FFI contract, and the
  conformance differential must prove byte-identical semantics before
  the floor is demoted; service handlers that today are Python
  callables must be exposed across the boundary (a boundary-shaped
  service ABI, versioned per ABI-001).
- **Risk**: the batch loop complicates backpressure (the floor's token
  bucket runs per message); the boundary service ABI is a new design
  surface. Mitigation: land with the differential gate green and the
  gate A/B measured; keep the floor as the fallback.

## References

- NPS-003 §6.1: the latency gate this direction exists to close
- ADR-0020: platform-boundary rule, migration rule, component map
  (NyRuntime = Rust)
- BENCHMARK_RESULTS.md §20: the v1/v2/floor A/B data that drives this
- ABI-001: the versioned FFI contract the batch surface must follow
- ADR-0009: token-bucket parameters the loop must enforce
