---
title: Kernel Architecture and Boot
document_id: NPS-001
version: 1.2.0
status: Accepted
classification: Normative
subsystem: core-architecture
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, ADR-0006, ADR-0012, ADR-0013, ADR-0014, NPS-020]
---

# NPS-001 — Kernel Architecture and Boot

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
defines the kernel-space/user-space boundary and the boot sequence for
Nythera, implementing the decision recorded in ADR-0006.

## 2. Scope

This specification covers:

- What runs in kernel space vs. user space.
- The boot sequence from firmware handoff to first user-space process.
- The minimal kernel-space exception list referenced in ADR-0006.

It does **not** cover process/thread semantics (see NPS-002) or IPC message
format (see NPS-003).

**Backend scope note (per ADR-0012, NPS-017):** this document defines the
**NyKernel Backend** specifically. Other NyHAL backends (e.g. the Linux
Backend) MUST satisfy the behavioral contract in NPS-017 §4 but are not
required to match this document's kernel-space component list or boot
stage structure internally — NPS-001 remains normative for NyKernel, not
for every backend.

## 3. Kernel-Space Components

Per ADR-0006, kernel space **MUST** be limited to the following components,
and **MUST NOT** grow beyond this list without a new ADR:

| Component | Responsibility |
|-----------|----------------|
| Scheduler | Thread/process scheduling across all cores |
| Memory Manager | Physical memory allocation, virtual address space setup, page fault handling |
| Interrupt Layer | Hardware interrupt routing to the scheduler or registered handlers |
| Capability/IPC Primitives | Creation, transfer, and revocation of capabilities (NPS-003); message passing primitives |
| GPU Command Submission Path | Low-latency submission of command buffers to the GPU, where user-space indirection would violate performance requirements. This path **MUST** validate command buffer structure before execution and **MUST** enforce a submission timeout with preemption, so that a malformed or malicious buffer cannot achieve kernel-adjacent memory corruption (per the threat model, `FIND-KERNEL-001`) or hang the GPU for every other container (`FIND-KERNEL-003`, NPS-020 §4). |
| Storage I/O Fast Path | Low-latency block I/O submission/completion, where user-space indirection would violate performance requirements |

Any component not listed here **MUST** run in user space.

## 4. User-Space Components *(Informative)*

The following run as isolated, capability-scoped user-space services, per
ADR-0004 and ADR-0006: device drivers (excluding the fast paths in §3),
NyFS filesystem logic (ADR-0002), network stack, Windows compatibility
runtime (ADR-0005), Android compatibility runtime, system services (login,
power management, update agent), and all applications regardless of class.

## 5. Boot Sequence

The boot sequence **MUST** proceed through the following stages in order:

1. **Firmware Handoff** — UEFI (or equivalent) hands control to the Nythera
   boot loader.
2. **Boot Loader** — verifies kernel image integrity (checksum, and
   signature where secure boot is enabled), loads the kernel and an initial
   minimal root filesystem ("boot image") into memory.
3. **Kernel Init** — the kernel initializes memory management, interrupt
   handling, and the scheduler; it **MUST NOT** mount NyFS itself, since
   filesystem logic is user-space (§4).
4. **First Process** — the kernel starts a single, minimal, trusted
   user-space process ("init") with elevated initial capabilities.
5. **Service Bring-Up** — `init` starts, in dependency order: the NyFS
   service, core drivers, and the capability/service registry, then hands
   off to the platform's adaptive UI shell (desktop, touch, console, or
   handheld mode, selected by the hardware profile detected in this stage).
6. **User Session** — once required services report ready, the OS reaches
   a usable state (login screen or default session, depending on device
   configuration).

Each stage **MUST** be independently observable via boot logs to satisfy
NTM-000 §4 ("Transparency").

## 6. Failure Handling

6.1. A failure in Stage 5 (Service Bring-Up) **MUST NOT** halt the entire
boot process if the failing service is not required for a minimal usable
session; the OS **SHOULD** continue boot and surface the failure to the
user, per NTM-000 "Reliability."

6.2. A failure in Stages 1–4 **MUST** halt boot and present a
diagnostic screen, since these stages are prerequisites for any recovery
tooling.

6.3. The boot loader **SHOULD** retain the previous known-good kernel/boot
image to support rollback, consistent with the atomic-update model described
in NTM-000 ("Updates") and to be formally specified in a future update-system
NPS.

## 7. Open Questions *(Informative)*

- ~~Exact scheduler algorithm (e.g. EEVDF-derived vs. custom) is not yet
  decided~~ — resolved by ADR-0013: EEVDF-derived, with a separate
  real-time priority class. Tuning parameters (time slice, weight curve,
  admission limits) remain pending benchmark data per NPC-002 §5.2.
- ~~Secure boot / signature verification key management is out of
  scope for this document~~ — resolved by ADR-0014: standard UEFI Secure
  Boot with a shim-equivalent first-stage loader, verified against a
  Nythera key, with user-enrollable keys supported for self-built kernels
  or Experimental Backend (NPS-017 §6) use. Exact interaction with NyHAL
  backend selection remains open pending backend implementation.

## Revision History

| Version | Date       | Change            |
|---------|------------|--------------------|
| 1.0.0   | 2026-07-12 | Initial draft      |
| 1.1.0   | 2026-07-12 | Add backend scope note (§2): this document defines the NyKernel Backend specifically, per ADR-0012/NPS-017 |
| 1.1.1   | 2026-07-12 | Architecture Group review completed (Milestone 9). Status: Draft → Accepted. |
| 1.1.2   | 2026-07-13 | Resolve §7 secure boot open question via ADR-0014 (UEFI Secure Boot, user-enrollable keys) |
| 1.2.0   | 2026-07-13 | Add GPU command buffer validation and submission-timeout requirements to §3, closing threat model findings FIND-KERNEL-001/003 (NPS-020) |

---
**End of Document**
