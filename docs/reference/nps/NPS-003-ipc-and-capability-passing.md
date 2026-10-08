---
title: Inter-Process Communication and Capability Passing
document_id: NPS-003
version: 1.2.0
status: Draft
classification: Normative
subsystem: core-architecture
owners:
  - Nyrqis Architecture
created: 2026-07-12
updated: 2026-08-12
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, ADR-0006, NPS-001, NPS-002]
---

# NPS-003 — Inter-Process Communication and Capability Passing

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. Because
most kernel-space functionality (NPS-001 §3) is reached only through IPC in
a hybrid microkernel design (ADR-0006), this specification is
performance-sensitive and security-critical.

## 2. Scope

This specification defines the IPC primitives processes use to communicate,
and how capabilities (NPS-002 §7) are transferred between processes. It does
not define the specific capability classes themselves (deferred to the
Milestone M6 capability registry).

## 3. IPC Primitives

Nyrqis **MUST** provide the following kernel-mediated primitives:

| Primitive | Semantics |
|-----------|-----------|
| `send` | Non-blocking or blocking transfer of a bounded message to a target endpoint the caller holds a capability for. |
| `receive` | Blocks the calling thread until a message arrives at an endpoint it owns. |
| `call` | Combined `send` + `receive` for synchronous request/reply patterns; **MUST** be implemented as a single kernel operation to avoid the scheduling overhead of two separate primitives. |
| `notify` | Asynchronous, payload-free signal (e.g. "data ready") for producer/consumer patterns that do not need message content. |

3.1. All message payloads **MUST** be bounded in size at the primitive
level. Bulk data transfer (e.g. large buffers) **MUST** use shared-memory
regions established via an explicit capability grant, not by inflating
message size limits. A shared-memory region **MUST** be cleared (zeroed)
by the kernel before being made available to a different container than
the one that last held it, so that a container cannot read stale data
left by a prior occupant of the same physical memory (per the threat
model, `FIND-CONTAINER-003`, NPS-020 §5).

3.2. `send`/`receive`/`call` **MUST** target an **endpoint**, not a raw
process ID, so that access is mediated by capability possession rather than
by guessable identifiers.

## 4. Endpoints

4.1. An endpoint **MUST** be created with an owning process and **MUST**
require a capability to send to it.

4.2. Possessing a send-capability to an endpoint **MUST NOT** by itself
grant the ability to enumerate other holders of that capability, to avoid
leaking topology information across trust boundaries.

4.3. An endpoint's owning process **MAY** revoke all outstanding
send-capabilities to it; revocation **MUST** take effect for all future
`send`/`call` operations without requiring cooperation from capability
holders.

## 5. Capability Passing

5.1. A capability **MAY** be transferred from one process to another only
as part of an IPC message, using a dedicated capability-transfer field
distinct from ordinary message payload bytes, so that transfers are
explicit and auditable rather than inferred from opaque data.

5.2. Capability transfer **MUST** respect NPS-002 §7: a process **MUST NOT**
transfer a capability it does not itself hold, and **MUST NOT** transfer a
capability broader than what it holds.

5.3. Transferred capabilities **MAY** be attenuated (narrowed) at transfer
time — e.g. converting a read/write file capability into a read-only one —
but **MUST NOT** be widened at transfer time.

5.4. The kernel **MUST** be the sole arbiter of capability validity;
user-space processes **MUST NOT** be able to forge or self-issue
capabilities.

## 6. Performance Requirements

6.1. Because IPC sits on the critical path for filesystem (NyFS), driver,
and compatibility-runtime operations (NPS-001 §4), the `call` primitive's
round-trip latency **MUST** be treated as a first-class performance metric
and **MUST** be benchmarked before this document exits Draft status, per
NPC-002 §5.2.

> **Benchmark note (2026-08-12; CORRECTED 2026-10-08):** the first-pass
> measurement of `call` (in-process `IPCManager`, 64-byte payloads, 20,000
> iterations, warmup 200) is recorded in `tests/BENCHMARK_RESULTS.md`:
> **p50 92 µs, p95 157 µs, p99 213 µs, mean 104 µs** — the in-process
> control-plane cost. The real transport this note originally called
> pending **has since been built and measured**: the Unix-domain datagram
> path (`ipc/transport.py`, `tests/benchmarks.py --ipc-transport`) records
> **p50 188.79 µs** (§20, 2026-08-14; the Rust serving-loop A/B at
> §21–§24), so **the <100 µs target is NOT met over the wire at the
> median**. A shared-memory transport (`ipc/shm_transport.py`, landed
> 2026-08-28) exists but is not yet wired into the IPC path or
> benchmarked. This document therefore remains `Draft`: the gate is now
> judged on a MEASURED wire cost, not on pending measurement. Closing (or
> re-scoping) that gate is the subject of `IPC-001`. The shared-memory
> transport was then benchmarked (`IPC-001` §38, 2026-10-08): **p50
> 187.86 µs — also missing the gate**, because the ring's 100 µs
> sleep-based polling (not the syscall path) dominates; `IPC-001`
> (Accepted, AG Bundle H1) records the wiring + wait-strategy fix as the
> next increment.

6.2. Shared-memory bulk transfer (§3.1) **SHOULD** avoid unnecessary data
copies between sender and receiver address spaces where the underlying
hardware and memory manager (NPS-001 §3) support zero-copy mapping.

## 7. Failure Semantics

7.1. If the target of a `send`/`call` has terminated (NPS-002 §5.6), the
kernel **MUST** return a defined error to the caller rather than blocking
indefinitely.

7.2. A `receive` on an endpoint whose owning process has terminated
**MUST** fail deterministically; the endpoint's resources **MUST** be
reclaimed once all references are gone.

## 8. Security Considerations

8.1. IPC **MUST NOT** provide any implicit trust based on runtime class
(NPS-002 §3); a `windows-compat` process communicating with a `native`
service is subject to the exact same capability checks as one native
process communicating with another.

8.2. Denial-of-service via endpoint flooding **SHOULD** be mitigated by
per-container rate limiting, to be defined precisely in the Milestone M6
security specifications.

## 9. Open Questions *(Informative)*

- Exact wire format / message header layout is deferred to an ABI document
  once the primitive set in §3 is validated by prototype implementation.
- Whether `notify` should support a small integer payload (vs. fully
  payload-free) is undecided and open for proposal.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |
| 1.1.0   | 2026-07-13 | Add shared-memory zeroing requirement to §3.1, closing threat model finding FIND-CONTAINER-003 (NPS-020) |
| 1.2.0   | 2026-08-12 | §6.1: record first-pass IPC latency measurements (`tests/BENCHMARK_RESULTS.md`); document remains Draft — median meets the <100 µs target, tail exceeds, real transport pending |
| 1.4.0   | 2026-10-08 | §6.1: the shared-memory transport measured (`IPC-001` §38, p50 187.86 µs — also misses the <100 µs gate; the ring's 100 µs sleep-based polling is the bottleneck); `IPC-001` Accepted (AG Bundle H1) with the wire + wait-strategy-fix increment pre-staged |
| 1.3.0   | 2026-10-08 | §6.1: correct the stale benchmark note — the real transport has existed and been measured since 2026-08-14 (§20 p50 188.79 µs) and MISSES the <100 µs target at the median; the shared-memory transport ships (2026-08-28) but is unwired; the gate-closure options are staged in `IPC-001` |

---
**End of Document**
