---
title: NyHAL — Kernel Abstraction Layer and Backend Contract
document_id: NPS-017
version: 1.0.1
status: Accepted
classification: Normative
subsystem: core-architecture
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, ADR-0006, ADR-0012, NPS-001, NPS-002, NPS-003, NPS-004, NPS-010, NPS-011]
---

# NPS-017 — NyHAL: Kernel Abstraction Layer and Backend Contract

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
implements ADR-0012, defining what every NyHAL backend **MUST** provide so
that NyCore, NyRuntime, and NySDK can remain backend-agnostic.

## 2. Scope

This specification defines the backend interface contract: the set of
guarantees any backend (Linux, Experimental, NyKernel, or a future one)
**MUST** satisfy. It does not define how any specific backend satisfies
the contract internally — that is backend-specific implementation detail,
covered by a per-backend document once implementation begins (e.g. a
future "Linux Backend Implementation Notes" reference).

## 3. Layering

3.1. The stack **MUST** consist of four layers, each depending only on the
layer directly beneath it:

| Layer | Contains | Depends On |
|-------|----------|------------|
| NySDK | Developer-facing SDK, application-level APIs | NyRuntime |
| NyRuntime | Compatibility runtimes (NPS-007, NPS-008), adaptive UI shell (NPS-009), gaming subsystem (NPS-012..014), AI subsystem (NPS-015..016) | NyCore |
| NyCore | Container/process model (NPS-002, NPS-010), capabilities (NPS-011), IPC (NPS-003), storage (NPS-004..006) | NyHAL |
| NyHAL | Backend abstraction boundary and its active backend | Hardware / host kernel |

3.2. Code in NySDK or NyRuntime **MUST NOT** call directly into a specific
backend's native mechanisms (e.g. a Linux syscall, a Linux namespace API)
— all such access **MUST** pass through NyCore's contracts, which
themselves are implemented against NyHAL.

## 4. Backend Contract

Every backend **MUST** implement the following, regardless of internal
mechanism:

### 4.1 Container Primitives
A backend **MUST** provide process/container creation, teardown,
suspension, and resource-limit enforcement sufficient to satisfy NPS-002
(process/thread model) and NPS-010 (container lifecycle) in full,
including the state machines defined in NPS-002 §5 and NPS-010 §4.

### 4.2 Capability Enforcement
A backend **MUST** be the sole arbiter of capability validity for its
containers, per NPS-003 §5.4 and NPS-010 §5 — a backend that allows a
container to self-issue or forge access beyond its granted capability set
is non-conformant, regardless of what native mechanism it uses to enforce
this (e.g. Linux LSM/seccomp policy, or NyKernel's native capability
primitives per NPS-003).

### 4.3 IPC Semantics
A backend **MUST** provide the `send`/`receive`/`call`/`notify` primitives
and endpoint model defined in NPS-003 §3–§4, including the capability
transfer and attenuation rules in NPS-003 §5, and the token-bucket rate
limiting decided in ADR-0009.

### 4.4 Storage Guarantees
A backend **MUST** provide the copy-on-write, snapshot, checksum, and
transparent-compression guarantees defined in NPS-004 §4, whether through
a native filesystem, a user-space/FUSE-backed implementation, or another
mechanism — the guarantee is what's normative, not the implementation
path.

### 4.5 Boot and Lifecycle
A backend **MUST** reach the boot milestones described in NPS-001 §5 in
substance — hardware/host initialization, a trusted first process, service
bring-up, and a usable session — even where its internal stages don't
literally match NPS-001 §5's NyKernel-specific stage names.

## 5. Backend Conformance

5.1. A backend **MUST NOT** be presented as Nythera-conformant unless it
satisfies §4 in full; partial conformance **MUST** be documented as such
(e.g. "Experimental Backend — storage guarantees not yet implemented") and
**MUST NOT** be silently assumed complete.

5.2. Differences in performance, feature completeness, or hardware support
between backends **MAY** exist and **MUST** be documented per backend,
consistent with the transparency requirements already established for
runtime and storage limitations (NPS-007 §9, NPS-006 §8).

## 6. Backend Registry *(Informative)*

| Backend | Status | Notes |
|---------|--------|-------|
| Linux Backend | Not started | Initial practical target per ADR-0012; containers via namespaces/cgroups, capabilities via seccomp/LSM, storage initially via user-space/FUSE implementation of NyFS semantics |
| Experimental Backend | Not started | Staging ground for partial NyKernel components and other experiments; explicitly not required to be conformant per §5.1 |
| NyKernel Backend | Not started | The hybrid microkernel target defined in ADR-0006 and specified in NPS-001; the long-term reference backend |

This table **MUST** be updated as backend work begins or status changes,
following the same discipline as `REPOSITORY_STATE.md` for the
documentation tree overall.

## 7. Migration and Portability

7.1. An application built against NySDK **MUST** run unmodified across any
conformant backend, since no layer above NyCore may depend on
backend-specific behavior (§3.2).

7.2. Data written under one backend's storage implementation **SHOULD** be
readable after switching backends on the same device, to the extent the
underlying storage medium is shared; exact migration tooling is deferred
to future work once more than one backend exists to migrate between.

## 8. Open Questions *(Informative)*

- Exact performance parity expectations between the Linux Backend and a
  future NyKernel Backend are unknown pending implementation and
  benchmarking (per NPC-002 §5.2).
- Whether NyFS (NPS-004) on the Linux Backend should be a FUSE
  implementation, a kernel module, or a user-space daemon with a Linux VFS
  shim is deferred to backend-specific implementation work.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |

| 1.0.1 | 2026-07-12 | Architecture Group review completed (Milestone 9). Status: Draft → Accepted. |

---
**End of Document**
