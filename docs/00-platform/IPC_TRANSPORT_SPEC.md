---
title: IPC Wire-Latency Gate — Design Note
document_id: IPC-001
version: 0.2.0
status: Accepted
classification: Technical
owners:
  - Nyrqis Architecture
created: 2026-10-08
updated: 2026-10-08
ai_assisted: true
review_cycle: Per-landing
depends_on: [NPS-003, NPS-017, ADR-0020, ADR-0021]
---

# IPC-001 — IPC Wire-Latency Gate (Design Note)

> **ACCEPTED 2026-10-08 (owner direction via the recorded session, the
> E1/F1/G1 shape — AG decision-log row H1):** Option A as recommended,
> with the measured condition that the first increment is **wire +
> measure + fix the wait strategy**. §38 (measured the same day, before
> wiring) shows the existing shared-memory transport at **p50
> 187.86 µs** — missing the gate — because the ring's **100 µs
> `sleep`-based polling**, not the syscall path, dominates. Wiring alone
> would not close the gate; the ring wait must become a yield/spin (or
> futex) wait in the same increment. §7 below is updated accordingly and
> is the implementation spec.

> Drafted per the DBG-001/CRY-001/UPD-001/RST-001 discipline: a novel gap
> is audited against the tree, options are recorded, and the build plan is
> pre-staged **before** implementation, so an acceptance ruling converts
> directly to landed work. This note exists because the roadmap's
> "IPC round-trip latency benchmark" item has been marked *pending real
> transport* since 2026-08-12 while the real transport has in fact been
> measured since 2026-08-14 — the honest remaining question is not whether
> to measure but **how (or whether) to close the NPS-003 §6.1 gate**.

## 1. Problem statement

NPS-003 §6.1 makes the `call` primitive's round-trip latency a first-class
performance metric with a **< 100 µs** target, and requires it to be
benchmarked before NPS-003 exits `Draft` (NPC-002 §5.2). The document's
inline benchmark note (v1.2.0, 2026-08-12) was written when no real
transport existed: it records the in-process control-plane cost (p50
92 µs) and states the document stays `Draft` "until the real transport is
measured under the plan's load variants."

That premise is now stale in two directions at once:

- the **real datagram transport exists and is measured** — and it *misses*
  the gate at the median; and
- a **shared-memory transport exists too**, but is **unwired and
  unbenchmarked**, so the cheapest honest path to the gate has never been
  tried.

The gate therefore sits open not because measurement is missing but
because no one has decided which transport is *supposed* to meet it, nor
what happens when a container pair cannot use it.

## 2. Surface audit (tree-verified 2026-10-08)

| Layer | What exists | Status |
|-------|-------------|--------|
| Datagram transport | `ipc/transport.py` — `AF_UNIX SOCK_DGRAM`, receiver `SO_PASSCRED` + kernel-attached `SCM_CREDENTIALS` sender identity, framed by the wire codec (`ipc/transport_codec.py`) | **Shipped, wired** |
| Compiled hot path | `rust/transport/src/lib.rs` (per-message sendto/recvmsg) behind the versioned FFI, Python floor otherwise, byte-identical contract (ADR-0020/ADR-0021) | **Shipped**, 626-line crate |
| Shared-memory transport | `ipc/shm_transport.py` — POSIX `shm_open`/`mmap` single-producer/single-consumer ring buffer (`ShmTransport.create/send/recv/close`, `is_shm_available()`), magic `0x4E595251`, header-verified on attach, mode `0600`, docstring trust model **requires the creator to hold `CAP_IPC_SEND` / `CAP_IPC_RECEIVE`** | **Shipped and unit-tested — UNWIRED; measured 2026-10-08 (§38) at p50 187.86 µs, MISSING the gate on the ring's 100 µs polling quantum** |
| SHM tests | `TestSharedMemoryTransport` (ring buffer, header, negotiate/mismatch, create/close, send/recv) in `test_backend.py` | **Present** |
| SHM wiring | no production module imports `shm_transport` — references exist only in `README_IMPLEMENTATION.md` and the test file | **Gap** |
| Measurement harness | `tests/benchmarks.py --ipc-transport` (§20), `--ipcd`/`--ipcd-dispatch`/`--ipcd-refresh`/`--ipcd-control` (the Rust serving-loop A/B, §21–§24) | **Shipped** |
| Datagram measurement | BENCHMARK_RESULTS **§20** (2026-08-14): floor p50 **188.79 µs**; **§21** Rust-v2 transport p50 **322.6 µs** (307–357 µs over 4 runs) | **Recorded** |

Load-bearing finding: **the gap is connection and measurement, not
implementation.** A shared-memory transport with the same wire format and
the same capability posture already exists and passes its own tests; it is
simply never selected by the production IPC path, and
`tests/benchmarks.py`'s own honesty note still calls it *"deferred"*
(the module landed 2026-08-28). The datagram path is measured and misses
the `< 100 µs` gate at the median; no measurement of the SHM path exists
to judge against it.

Secondary finding: NPS-003 §6.1's inline note is factually stale — it says
the transport layer "does not exist yet." It has existed since
2026-08-14, so the note must be corrected regardless of this note's
outcome.

## 3. Options

**Option A — wire the existing shared-memory transport as a selectable
endpoint path, then benchmark it (recommended).** Reuse `ipc/shm_transport.py`
behind the *same* wire codec and the *same* capability checks, selectable
per endpoint pair (the datagram transport remains the default and the
honest fallback). A zero-copy ring buffer removes the two `sendto`/`recvmsg`
syscall pairs and the `SO_PASSCRED` attachment from the hot path, which
§20 identified as the source of the wire cost. The gate is then judged on
the transport each endpoint pair actually uses.

*Ledger*: + no new implementation (the module ships and is tested);
+ no new trust surface (same codec, same `CAP_IPC_*` posture, owner-only
segment); + converts §20's measured miss into a decision; − segments are
**PID-namespace-scoped**, so it cannot serve cross-namespace container
pairs (the datagram path must stay); − needs a fallback/selection contract
pinned so a transport can never change silently mid-stream.

**Option B — optimize the datagram path.** Attack the §20/§21 costs
directly: reduce the FFI boundary cost (§20 showed the *v1* Rust transport
was slower than the Python floor at the median), batch, or try
`MSG_ZEROCOPY`. Keeps a single transport to reason about.

*Ledger*: + one transport, no selection contract; − the in-process floor
is already 92 µs, so the wire's headroom to `< 100 µs` is ~8 µs — a very
tight budget for two process hops + two syscall pairs; − risks re-opening
ADR-0021's serving-loop design for a bounded payoff.

**Option C — re-scope the gate.** Accept the measured wire cost; amend
NPS-003 §6.1 to state the `< 100 µs` target applies to the in-process /
served primitive, and document the cross-process wire cost as a
separate, separately-budgeted figure.

*Ledger*: + zero engineering cost and the document can exit `Draft`;
− gives up on the platform's own first-class latency target while an
already-built mechanism sits unused; − the compat filesystem/driver paths
NPS-003 §6.1 names run cross-process.

## 4. Open questions for the Group

1. **Which transport is the gate judged on?** Recommendation: the
   transport an endpoint pair actually uses — i.e. Option A judges the
   gate on the SHM path for same-namespace pairs and the datagram path
   (§20) otherwise. The alternative (C) judges it on nothing.
2. **Selection policy:** explicit opt-in (an endpoint `shm: true` flag,
   fail-closed to datagram when `is_shm_available()` is false) or
   automatic locality detection? Recommendation: explicit — same posture
   as every other opt-in surface, and never silent.
3. **New capability or reuse?** The module's own trust model already
   requires the segment creator to hold `CAP_IPC_SEND`/`CAP_IPC_RECEIVE`.
   Recommendation: **reuse**, register no new capability.
4. **Scope of the gate:** `call` only (NPS-003 §6.1's wording) or
   `send`/`notify` too? Recommendation: `call`.
5. **The plan's load variants:** the two-container-under-load /
   two-hardware-class variants remain, and are real-hardware gated
   independently of this decision. This note does not resolve them.

## 5. Recommendation

**Option A**, with the honest scoping that the SHM transport is
**PID-namespace-local** — it complements, and cannot replace, the datagram
path. Recommended first increment: wire the existing module behind the
codec/capability contract, add a single `--ipc-shm` benchmark using the
§20 methodology, and correct NPS-003 §6.1's stale note. If the measured
SHM `p50` meets `< 100 µs`, §6.1 is satisfied for same-namespace pairs
and NPS-003's remaining `Draft` blocker narrows to the hardware-gated load
variants; if it does not, the data decides between Option B and Option C —
either way the gate stops being *unmeasured*.

**Measured before wiring (§38, 2026-10-08):** the existing SHM transport
lands at p50 187.86 µs — statistically indistinguishable from the §20
datagram floor (188.79 µs) and equally short of the gate. The cause is
NOT the transport mechanism but the ring's wait strategy: `RingBuffer`
polls in `time.sleep(0.000_1)` (100 µs) steps, so an empty-ring reader
waits up to one quantum per message. **This makes the wait-strategy fix
part of the first increment, not a follow-up** — the acceptance ruling
records exactly that condition.

Option A is safe to accept before all §4 questions are answered **only if**
the first increment pins the fallback contract (a transport never changes
silently) in the same commit as the wiring.

## 7. Pre-staged build plan (converts to landed work on acceptance)

1. **Wire + fix the wait**: wire `ShmTransport` into `ipc/transport.py`
   as a selectable endpoint path behind the existing codec and credential
   contract (datagram stays default and fallback), and replace the ring's
   `time.sleep(0.000_1)` polling with a yield/spin (or futex) wait — §38
   shows that 100 µs quantum, not the syscall path, is what misses the
   gate.
2. **Benchmark**: `tests/benchmarks.py --ipc-shm` → BENCHMARK_RESULTS
   **§38** — the §20 methodology (20,000 iterations, 64-byte payloads,
   200 warm-up calls) over the SHM path, run in the same session as the
   §20 datagram figure for a same-host A/B.
3. **Spec**: correct NPS-003 §6.1's benchmark note (the "does not exist
   yet" premise) and add a revision row recording the measured SHM result
   and whether the `< 100 µs` gate is met.
4. **Contract pins**: codec-identical bytes on both transports; `CAP_IPC_*`
   enforced on the SHM path (NPS-017 §4.3); fallback-never-silent;
   `is_shm_available()` gate refused cleanly, never a partial send.
5. **Keep §20 measured** as the cross-namespace floor.

## 8. As-built (2026-10-08)

Landed under the H1 ruling:

- **The wait fix** (`ipc/shm_transport.py`): `RingBuffer.read`/`write`
  now call `_wait_ready` — a 50 µs busy-spin, then a bounded
  `time.sleep` — instead of the fixed 100 µs sleep that quantized every
  wait. Measured effect: §38 p50 187.86 µs → **20.41 µs** (p99
  52.58 µs), **closing the NPS-003 §6.1 `<100 µs` gate on the
  same-namespace SHM path** at the median and tail. §38b records the
  tradeoff: the shipped spin→sleep wait costs 8% idle CPU, versus 100%
  for the pure-yield alternative (which has the tighter max, 94.24 µs).
- **The selectable channel** (`ipc/shm_channel.py`): `TransportSelection`
  / `select_transport` (explicit, fail-closed, never silent — a caller
  that asks for SHM but cannot have it gets datagram back with
  `fell_back=True` and a reason) and `ShmChannel` carrying the datagram
  path's `IPCMessage.to_wire()` bytes (codec-identical), with an
  unguessable-token requirement (`MIN_CHANNEL_TOKEN_LEN`). Pinned by
  `tests/test_ipc_shm_channel.py` (10 tests: the selection policy
  including both fallbacks, the token rules, and the codec-identical
  cross-instance CALL/REPLY round trip).
- **The measurement** (`tests/benchmarks.py --ipc-shm` / `--ipc-shm-cpu`):
  BENCHMARK_RESULTS §38 (latency) and §38b (idle CPU busy fraction 1.0 —
  the documented tradeoff).

**Remaining (honest scope):** the daemon does not yet *establish* a
channel per authenticated client (server `create` + advertise the token
over the authenticated datagram path; client `attach`); a futex/eventfd
wait is the follow-on if §38b's idle CPU cost ever outweighs latency.

## Change log

- 0.2.0 (2026-10-08): **as-built** — the wait fix landed (`_wait_ready`:
  50 µs spin then a bounded sleep), §38 measures p50 20.41 µs / p99
  52.58 µs and the NPS-003 §6.1 gate is MET on the same-namespace SHM
  path at the median/tail; the selectable channel (`ipc/shm_channel.py`) and its 10 contract
  pins landed; §38b records the ~100% idle-CPU cost of the yield wait.
- 0.1.1 (2026-10-08): **ACCEPTED (Option A, AG Bundle H1)** and the §38
  measurement recorded the same day — the existing shared-memory
  transport measures p50 187.86 µs, missing the <100 µs gate; the ring's
  100 µs sleep-based polling is the bottleneck (not the syscall path), so
  the first increment now includes the wait-strategy fix. 0.1.0: initial
  draft — surface audit (the datagram
  transport shipped and measured; the shared-memory transport shipped,
  unit-tested and **unwired**; NPS-003 §6.1's note stale), three options
  (A wire + benchmark the existing SHM transport, recommended; B optimize
  the datagram path; C re-scope the gate), five open questions, and §7
  pre-stages the build plan.
