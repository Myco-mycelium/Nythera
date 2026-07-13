---
title: Adopt a Hybrid Microkernel as the Nythera Kernel Base
document_id: ADR-0006
version: 1.0.1
status: Accepted
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0004]
---

# ADR-0006 — Hybrid Microkernel as the Nythera Kernel Base

## Context
Nythera must run on hardware ranging from phones to gaming desktops, support
containerized execution for every application class (ADR-0004), and remain
maintainable for decades (NTM-000 §4, "Longevity"). The kernel architecture
choice shapes nearly every subsystem that follows, so it must be decided
before process model or IPC specifications can be written.

Three broad options exist:

1. **Monolithic kernel** (Linux-style): drivers, filesystem, network stack,
   and scheduler share one address space.
2. **Pure microkernel** (seL4-style): only scheduling, IPC, and minimal
   memory management run in kernel space; everything else, including
   drivers and filesystems, runs as isolated user-space servers.
3. **Hybrid microkernel** (Windows NT / XNU-style): a small trusted core plus
   a defined set of performance-critical subsystems (scheduler, core memory
   management, low-level graphics/interrupt paths) in kernel space, with
   drivers, filesystem logic, and services isolated in user space wherever
   performance allows.

## Decision (Proposed)
Adopt a **hybrid microkernel**. The kernel space is limited to: scheduler,
memory management, interrupt handling, capability/IPC primitives, and a
minimal set of performance-critical drivers (GPU command submission,
storage I/O path) where user-space overhead would violate NTM-000
"Performance." All other drivers, the filesystem (NyFS, per ADR-0002), the
Windows and Android compatibility runtimes (ADR-0005), and system services
run as isolated, capability-scoped user-space processes.

This directly extends the containerization model from ADR-0004: if
compatibility runtimes and system services are already expected to run in
capability-scoped containers, a microkernel-style boundary between them and
the kernel is a natural, consistent continuation of the same security
architecture, rather than a second, unrelated isolation mechanism.

## Alternatives Considered
- **Pure monolithic kernel** — rejected. A single fault in a driver or
  filesystem module can crash the entire system, which conflicts with
  NTM-000 "Reliability" ("the system should recover gracefully from
  failure"). It also concentrates trust in ways that fight the
  containerization model already adopted in ADR-0004.
- **Pure microkernel** — rejected as the default. Excellent for
  reliability and security, but historically pays a real IPC-latency cost
  for performance-critical paths (GPU submission, storage), which risks
  violating NTM-000 "Performance" on gaming workloads that are central to
  the project. Kept as an option for a future "hardened mode" if warranted.
- **Exokernel** — rejected as unjustified complexity for this project's
  goals; would require every subsystem to reimplement resource management
  that a hybrid design provides once, centrally.

## Consequences
- Driver and filesystem faults SHOULD NOT crash the kernel; they MUST be
  recoverable by restarting the affected user-space service where
  technically possible.
- The IPC mechanism (NPS-003) becomes a first-class, performance-sensitive
  subsystem rather than a convenience layer, since most system
  functionality communicates with the kernel through it.
- Performance-critical exceptions (GPU submission, storage I/O) MUST be
  explicitly enumerated and justified in NPS-001; the kernel-space
  exception list MUST NOT grow silently over time.
- This decision does not itself select a scheduler algorithm, memory
  allocator, or capability model — those are defined in NPS-001 through
  NPS-003.

## Status
Accepted — 2026-07-12, following Architecture Group review (Milestone 9). NPS-001 (NyKernel Backend) implements this decision. Scheduler algorithm is resolved by ADR-0013; exact IPC latency and resource-limit tuning remain pending benchmark data (NPS-002 §9, NPS-003 §6.1), which does not block acceptance of the architectural decision itself.
