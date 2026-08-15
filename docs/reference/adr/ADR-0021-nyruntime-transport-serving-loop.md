---
title: NyRuntime Direction — IPC Serving Loop Behind the FFI Boundary
document_id: ADR-0021
version: 1.0.0
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-08-14
updated: 2026-08-14
ai_assisted: true
depends_on: [NPS-003, ADR-0020, ABI-001, ADR-0009]
---

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
