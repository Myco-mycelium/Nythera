# Why Nythera Uses a Hybrid Microkernel

*(Explanation — see NPS-001, NPS-002, NPS-003 for the normative specs, and
ADR-0006 for the formal decision record.)*

## The Problem

Nythera needs to satisfy two goals that usually pull against each other:

- **Reliability and security** (NTM-000 §4) — a fault in one driver or
  service shouldn't be able to take down the whole system, and every
  application class (native, Windows-compat, Android-compat) needs the same
  containment guarantees (ADR-0004).
- **Performance** (NTM-000 §4) — gaming is a first-class capability of the
  platform, not an afterthought, and gaming workloads are unusually
  sensitive to latency in exactly the paths — GPU submission, storage I/O —
  that a "pure" microkernel tends to push into user space.

A monolithic kernel gets performance largely for free but gives up fault
isolation. A pure microkernel gets fault isolation but historically pays for
it in IPC latency on the paths that matter most for a gaming-focused OS.

## The Choice

A hybrid microkernel keeps the kernel small — scheduler, memory management,
interrupts, and the capability/IPC layer — and only pulls in the specific
paths (GPU command submission, storage fast path) where user-space
indirection would be a measurable performance cost. Everything else,
including the filesystem and both compatibility runtimes, runs isolated in
user space behind the same capability boundary that already governs
containers under ADR-0004.

The result is that "container" and "kernel-space/user-space boundary" are
not two separate security models bolted together — they're the same idea
applied consistently, which is what NTM-000 §6 ("Architecture is more
valuable than implementation") is asking for.

## What This Buys Us

- A crashing driver or a crashing Windows-compat process can be restarted
  without a full system reboot (NPS-002 §8).
- The capability system that keeps applications sandboxed is the same
  system the kernel itself uses to mediate IPC (NPS-003 §5) — there isn't a
  second, weaker permission model hiding underneath the visible one.
- The performance-critical exception list (NPS-001 §3) is small and
  explicit, so it can be audited rather than growing informally over time.

## What It Costs Us

- More engineering effort than a monolithic design, since drivers and
  filesystem logic need real process isolation and a well-designed IPC
  path rather than direct function calls.
- IPC becomes a performance-critical subsystem in its own right (NPS-003
  §6), which means it needs to be benchmarked, not just implemented, before
  it can be trusted for shipping gaming workloads.

## Alternatives We Considered

See ADR-0006 for the full comparison against a pure monolithic kernel and a
pure microkernel, including why each was rejected as the *default* choice
(a pure microkernel remains an option for a possible future "hardened
mode").
