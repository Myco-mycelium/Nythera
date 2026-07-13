---
title: NyFS Linux Backend Implemented as a User-Space FUSE Filesystem
document_id: ADR-0016
version: 1.0.0
status: Proposed
owners: [Nythera Architecture]
created: 2026-07-13
updated: 2026-07-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0002, ADR-0012, NPS-004, NPS-017]
---

# ADR-0016 — NyFS Linux Backend as a User-Space FUSE Filesystem

## Context
NPS-017 §8 left NyFS's Linux Backend implementation strategy — FUSE,
kernel module, or user-space daemon with a VFS shim — as an open question.
This blocks concrete implementation work on the Linux Backend (NPS-017
§6), which ADR-0012 identified as the practical near-term path to a
runnable Nythera.

## Decision (Proposed)
Implement NyFS's Linux Backend as a **user-space filesystem via FUSE**
(Filesystem in Userspace) for the initial implementation, rather than a
Linux kernel module.

- This is consistent with NyFS already being specified as running in user
  space under NPS-004 §3.1, even on the NyKernel Backend — a FUSE
  implementation on Linux is the natural extension of the same placement,
  not an exception to it.
- FUSE avoids kernel module signing/certification friction (relevant given
  ADR-0014's Secure Boot decision — a kernel module would need its own
  signing story, while a user-space FUSE process does not) and avoids
  tying NyFS's implementation to a specific Linux kernel ABI version.
- Performance cost is an accepted, known tradeoff of this decision: FUSE
  introduces additional context-switch overhead per I/O operation compared
  to a kernel module. This MUST be measured (per NPC-002 §5.2) before any
  claim is made about whether it's acceptable for gaming workloads: if
  benchmarking shows it is not, this ADR MUST be revisited in favor of a
  kernel module, which remains an explicitly available fallback, not a
  closed door.

## Alternatives Considered
- **Linux kernel module** — rejected as the *initial* implementation;
  faster in principle, but requires kernel-version-specific maintenance,
  a separate Secure Boot signing path (ADR-0014), and is harder to iterate
  on during early development. Remains available as a future optimization
  if FUSE's overhead proves unacceptable under benchmarking.
- **User-space daemon with a custom Linux VFS shim (no FUSE)** — rejected;
  effectively reimplements what FUSE already provides, adding engineering
  cost without a clear benefit over using the existing, well-supported
  FUSE interface.
- **Defer the decision further, block Linux Backend work entirely** —
  rejected; NPS-017 §6 already identifies Linux Backend implementation as
  the practical near-term priority (ADR-0012), and NyFS is a prerequisite
  for nearly everything else (game images, overlays, capability-scoped
  filesystem access) — leaving it undecided blocks disproportionately more
  downstream work than the other open questions in the backlog.

## Consequences
- NPS-004's copy-on-write, snapshot, checksum, deduplication, and
  compression guarantees (NPS-004 §4) MUST all be implementable within
  FUSE's operation model; if any guarantee turns out to be impractical
  under FUSE, this MUST surface as a documented limitation or trigger
  revisiting this ADR, not a silent relaxation of NPS-004's requirements.
- I/O performance benchmarking (deferred, per the pattern established
  throughout this backlog) now has a concrete target to measure: FUSE
  overhead specifically, compared against the kernel-module alternative,
  rather than an abstract "storage performance" question.
- This decision is scoped to the Linux Backend only; it does not affect
  NPS-001's NyKernel Backend storage design, which remains a separate,
  native implementation question.

## Status
Proposed — initial implementation strategy decided; kernel-module
fallback remains explicitly open pending FUSE overhead benchmarking.
