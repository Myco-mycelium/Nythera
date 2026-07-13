---
title: Adopt NyHAL as a Pluggable Kernel Abstraction Layer
document_id: ADR-0012
version: 1.0.1
status: Accepted
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0006, NPS-001, NPS-002, NPS-003]
---

# ADR-0012 — NyHAL as a Pluggable Kernel Abstraction Layer

## Context
ADR-0006 commits Nythera to a hybrid microkernel design (NyKernel) as the
long-term target. Building a kernel from scratch, however, is a multi-year
effort, and NTM-000 §4 ("Longevity") explicitly asks that decisions be
evaluated not just architecturally but against what keeps the platform
real and usable along the way — a project with no runnable system for
years risks becoming purely aspirational.

Every subsystem specified so far (NPS-002 through NPS-016) is written in
terms of *behavioral contracts* — containers, capabilities, IPC semantics,
storage guarantees — rather than in terms of specific kernel internals.
This means the rest of the OS does not actually need to know which kernel
provides those guarantees, only that they hold.

## Decision (Proposed)
Introduce **NyHAL** (Nythera Hardware Abstraction Layer) as a stable
interface boundary beneath the rest of the operating system, with kernel
functionality provided by swappable backends:

```
NySDK       — developer-facing SDK and application-level APIs
   │
NyRuntime   — compatibility runtimes (NPS-007, NPS-008), adaptive UI shell (NPS-009),
              gaming subsystem (NPS-012..014), AI subsystem (NPS-015..016)
   │
NyCore      — the behavioral contracts already specified: containers (NPS-002, NPS-010),
              capabilities (NPS-011), IPC (NPS-003), storage (NPS-004..006)
   │
NyHAL       — backend abstraction boundary
 ├── Linux Backend           — NyCore contracts implemented atop the Linux kernel
 │                              (namespaces/cgroups/seccomp/LSM as the initial
 │                              mechanism behind containers and capabilities)
 ├── Experimental Backend    — a staging ground for partial NyKernel components
 │                              and other backend experiments
 └── NyKernel Backend        — the hybrid microkernel target defined in ADR-0006
                                 and NPS-001, once implemented
```

Everything above NyCore — applications, compatibility runtimes, the UI
shell, gaming and AI subsystems — **MUST** depend only on NyCore's
contracts, never on a specific backend, so it does not know or care which
backend is running underneath.

NPS-001 (Kernel Architecture and Boot) continues to define the **NyKernel
Backend** specifically: its kernel-space/user-space component list, boot
sequence, and failure handling remain normative for that backend. Other
backends **MUST** provide equivalent guarantees (NPS-002's process model,
NPS-003's IPC semantics, NPS-010's capability enforcement) through
whatever native mechanism they have available, without being required to
match NPS-001's internal structure.

## Alternatives Considered
- **Build only the NyKernel backend, ship nothing until it's ready** —
  rejected; leaves the project without a runnable system for an extended
  period, which conflicts with treating the platform as something meant
  to actually exist, not only be designed (NTM-000's closing statement:
  "a platform that... users trust with their work").
- **Abandon the hybrid microkernel goal and commit permanently to Linux**
  — rejected; ADR-0006's reasoning (fault isolation across
  drivers/filesystem/compat-runtimes, IPC as a first-class primitive) is
  still the right long-term target and remains unchanged by this decision.
  This ADR is about sequencing and abstraction, not abandoning ADR-0006.
- **Abstract only storage or only IPC, leave the rest kernel-specific** —
  rejected; a partial abstraction would leave some subsystems
  backend-portable and others not, undermining the point of the layering
  and creating exactly the kind of inconsistent, informally-scoped
  boundary NTM-000 §6 ("Architecture is more valuable than
  implementation") warns against.

## Consequences
- The Linux Backend becomes the practical near-term implementation path:
  containers (NPS-002 §4) map to Linux namespaces/cgroups, capability
  enforcement (NPS-010, NPS-011) maps to seccomp/LSM policy, and NyFS
  (NPS-004) can initially run as a user-space or FUSE-backed filesystem
  atop Linux rather than requiring a from-scratch kernel storage stack.
- Every existing NPS document's behavioral requirements (containers must
  be capability-scoped, IPC must go through the kernel as sole arbiter,
  etc.) become **backend-portability requirements**: a Linux Backend that
  fails to enforce them is non-conformant, exactly as a NyKernel backend
  that failed to would be.
- A new NyHAL specification (NPS-017) is required to define the backend
  interface contract itself — what every backend must implement — before
  implementation work on any backend begins.
- This introduces real engineering cost: maintaining backend-agnostic
  contracts is harder than writing directly against one kernel. This
  is accepted as the price of NTM-000 §4 ("Longevity") applied practically
  rather than only architecturally.

## Status
Accepted — 2026-07-12, following Architecture Group review (Milestone 9). Flagged and reviewed as cross-cutting per NPC-001 §3.1. NPS-017 implements the backend contract; NPS-001 has been amended to scope itself to the NyKernel Backend specifically.
