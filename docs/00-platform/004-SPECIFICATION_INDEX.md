---
title: Specification Index
document_id: NPC-004
version: 1.18.0
status: Draft
classification: Reference
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: Continuous
depends_on: [NPC-001]
---

# NPC-004 — Specification Index

This document is the master index of every canonical document in the
Nythera repository. It **MUST** be updated in the same commit that adds,
accepts, deprecates, or rejects any normative document (NPC-001 §6.5).

## 00 — Platform (Foundational)

| ID | Title | Status | Version |
|----|-------|--------|---------|
| NTM-000 | The Nythera Manifest | Accepted | 1.0.0 |
| NPC-001 | Project Constitution | Accepted | 1.1.0 |
| NPC-002 | AI Collaboration Protocol | Accepted | 1.0.1 |
| NPC-003 | Engineering Handbook | Accepted | 1.0.1 |
| NPC-004 | Specification Index (this document) | Draft | 1.3.0 |
| NPC-005 | ADR Index | Draft | 1.2.0 |
| NPC-006 | Glossary | Draft | 1.0.0 |
| NPC-007 | Project Roadmap | Draft | 1.1.0 |
| NPC-008 | Subsystem Owners | Draft | 1.0.0 |
| NPC-009 | Requirements Database | Draft | 1.0.0 |

## Architecture Decision Records

| ID | Title | Status |
|----|-------|--------|
| ADR-0001 | Adopt Diátaxis + MkDocs Material for documentation | Accepted |
| ADR-0002 | Adopt copy-on-write filesystem with built-in compression | Accepted |
| ADR-0003 | Games distributed as mounted disk images with writable overlay | Accepted |
| ADR-0004 | Containerized execution model for all application classes | Accepted |
| ADR-0005 | Windows compatibility via translation layer, not full emulation | Accepted |
| ADR-0006 | Adopt a hybrid microkernel as the Nythera kernel base | Accepted |
| ADR-0007 | Adopt Zstandard as the default compression codec | Proposed — benchmark-blocked |
| ADR-0008 | Adopt an AOSP-based container runtime for Android compatibility | Accepted |
| ADR-0009 | Per-container token-bucket rate limiting for IPC | Proposed — benchmark-blocked |
| ADR-0010 | Adopt Vulkan as the native graphics API foundation | Accepted |
| ADR-0011 | AI assistant runs as an ordinary capability-scoped container | Accepted |
| ADR-0012 | Adopt NyHAL as a pluggable kernel abstraction layer | Accepted |
| ADR-0013 | Adopt an EEVDF-derived scheduler with a real-time priority class | Proposed — tuning-blocked |
| ADR-0014 | Adopt UEFI Secure Boot with user-enrollable keys | Proposed |
| ADR-0015 | Shared dynamic binary translation approach for ARM/x86 compatibility | Proposed |
| ADR-0016 | NyFS Linux Backend implemented as a user-space FUSE filesystem | Proposed |
| ADR-0017 | Reject domain-grouped NPS renumbering | **Rejected** |
| ADR-0018 | Hash-chained append-only log for capability audit records | Proposed |

See `docs/reference/adr/` for full records and NPC-005 for the governing
index.

## Nythera Proposals for Specification (NPS)

| ID | Title | Subsystem | Status |
|----|-------|-----------|--------|
| NPS-001 | Kernel Architecture and Boot (NyKernel Backend) | core-architecture | Accepted |
| NPS-002 | Process and Thread Model | core-architecture | Draft — benchmark-blocked (§9) |
| NPS-003 | Inter-Process Communication and Capability Passing | core-architecture | Draft — benchmark-blocked (§6.1) |
| NPS-004 | NyFS Filesystem Core | storage | Accepted |
| NPS-005 | Transparent Compression Policy | storage | Draft — blocked on ADR-0007 |
| NPS-006 | Nythera Game/Application Image Format (.nygi) and Overlay | storage | Accepted |
| NPS-007 | Windows Compatibility Runtime | runtime | Accepted |
| NPS-008 | Android Compatibility Runtime | runtime | Accepted |
| NPS-009 | Adaptive UI Shell | runtime | Accepted |
| NPS-010 | Container Runtime | security | Draft — blocked on ADR-0009 (§7.1) |
| NPS-011 | Capability Registry | security | Accepted |
| NPS-012 | Controller and Input Subsystem | gaming | Accepted |
| NPS-013 | GPU Feature Support | gaming | Accepted |
| NPS-014 | Emulator Hub | gaming | Accepted |
| NPS-015 | Local AI Assistant | ai | Accepted |
| NPS-016 | Optional Cloud Synchronization | ai | Accepted |
| NPS-017 | NyHAL — Kernel Abstraction Layer and Backend Contract | core-architecture | Accepted |
| NPS-018 | Threat Model Methodology and Trust Boundaries | security | Draft |
| NPS-019 | Attack Surface Enumeration | security | Draft |
| NPS-020 | STRIDE Analysis per Trust Boundary | security | Draft |
| NPS-021 | Privilege Boundaries and Capability Escalation Analysis | security | Draft |
| NPS-022 | Container Escape Analysis and Runtime Isolation | security | Draft |
| NPS-023 | Secure Boot Threat Model | security | Draft |
| NPS-024 | AI Threat Model | security | Draft |

Following the Milestone 9 Architecture Group review, 13 of 17 NPS documents
and 10 of 13 ADRs are `Accepted`. The remainder are held at `Draft`/
`Proposed` for a specific, named reason (a pending benchmark or a
dependency on another document that is itself benchmark-blocked) rather
than incompleteness — see each document's Open Questions / Status section,
and `REPOSITORY_STATE.md` for the consolidated list.

ADR-0017 is the project's first `Rejected` ADR — a proposal to renumber
NPS identifiers into domain-grouped ranges was considered and declined;
see the ADR for the reasoning. The existing sequential IDs and each
document's `subsystem` front-matter field remain the domain-organization
mechanism.

## Requirements Database

Governed by [`NPC-009`](009-REQUIREMENTS_DATABASE.md). The ledger itself
lives at
[`docs/reference/requirements/REQUIREMENTS.md`](../reference/requirements/REQUIREMENTS.md)
— 29 seed requirements across all 17 domain prefixes, each traced to a
specific section of an already-`Accepted` specification. Not full
coverage by design (NPC-009 §7.3); expand incrementally via the normal
change process.

## ABI / API References

| ID | Title | Status |
|----|-------|--------|
| — | — | Not started |

## Package Format

| ID | Title | Status |
|----|-------|--------|
| — | Nythera Package Format (.nypkg) | Not started |

---

## Revision History

| Version | Date       | Change                          |
|---------|------------|-----------------------------------|
| 1.0.0   | 2026-07-12 | Initial index at repository bootstrap |
| 1.1.0   | 2026-07-12 | Add ADR-0006 and NPS-001..003 (Core Architecture, M3) |
| 1.2.0   | 2026-07-12 | Add ADR-0007 and NPS-004..006 (Storage, M4) |
| 1.3.0   | 2026-07-12 | Accept NPC-001/002/003 (Draft → Accepted); add NPC-008 Subsystem Owners (Milestone 2) |
| 1.4.0   | 2026-07-12 | Add ADR-0008 and NPS-007..009 (Runtime, M5) |
| 1.5.0   | 2026-07-12 | Add ADR-0009 and NPS-010..011 (Security, M6) |
| 1.6.0   | 2026-07-12 | Add ADR-0010 and NPS-012..014 (Gaming Subsystem, M7) |
| 1.7.0   | 2026-07-12 | Add ADR-0011 and NPS-015..016 (AI Subsystem, M8); add CAP-AI-DIAGNOSTICS-READ, CAP-AI-SUGGEST-ACTION, CAP-CLOUD-SYNC to NPS-011 |
| 1.8.0   | 2026-07-12 | Add ADR-0012 and NPS-017 (NyHAL pluggable kernel backend, cross-cutting); amend NPS-001 scope to NyKernel Backend specifically |
| 1.9.0   | 2026-07-13 | Milestone 9 Architecture Group review: add ADR-0013 (scheduler algorithm, Proposed); accept ADR-0002/0003/0004/0005/0006/0008/0010/0011/0012 and NPS-001/004/006/007/008/009/011/012/013/014/015/016/017 (Draft/Proposed → Accepted); NPS-002/003/005/010 remain Draft, each with a named benchmark or upstream-dependency blocker |
| 1.10.0  | 2026-07-13 | Backlog pass: add ADR-0014 (secure boot), ADR-0015 (shared ARM translation), ADR-0016 (NyFS Linux Backend FUSE strategy); resolve remaining open questions in NPS-001/007/008 via cross-reference; expand NPS-011 Android permission mapping (8 new capabilities); add tests/BENCHMARK_PLAN.md and CI docs build workflow |
| 1.11.0  | 2026-07-13 | In response to external review: add ADR-0017 (Rejected — declines NPS domain-renumbering proposal); add NPC-009 Requirements Database and its seed ledger (29 requirements, 17 domains); bump NPC-001 to 1.1.0 for the new REQ document class |
| 1.12.0  | 2026-07-13 | Add NPS-018 and NPS-019 (Threat Model Phase 1: methodology + trust boundaries, attack surface enumeration), new `docs/reference/security/` directory |
| 1.13.0  | 2026-07-13 | Add NPS-020 (Threat Model Phase 2: STRIDE analysis, all 10 trust boundaries); amend NPS-001 (GPU command buffer validation, 1.1.2→1.2.0) and NPS-003 (shared-memory zeroing, 1.0.0→1.1.0) to close two findings; add REQ-GPU-0002 and REQ-IPC-0003 |
| 1.14.0  | 2026-07-13 | Add NPS-021 (Threat Model Phase 3: privilege boundaries, capability escalation) and ADR-0018 (hash-chained audit log); also add ADR-0017 to this table, missed in the prior revision; amend NPS-010 (atomic grant-check, tamper-evident audit) and NPS-011 (split CAP-MEDIA-LIBRARY into three capabilities); add REQ-IPC-0004 |
| 1.15.0  | 2026-07-15 | Reconcile with an externally-contributed Linux Backend implementation (`source/nyhal-linux-backend/`, merged after a push conflict, independently verified — 20/20 tests pass): NPS-017 §6 backend registry updated from "Not started" to "Experimental," REQ-NYHAL-0003 upgraded to `Tested` |
| 1.16.0  | 2026-07-15 | Add NPS-022 (Threat Model Phase 4: container escape analysis, grounded in the real implementation); amend NPS-017 §4.1/§4.2 (cgroup v1 hardening, control-plane/data-plane enforcement distinction); add REQ-NYHAL-0004; implementation flagged non-conformant against the tightened §4.2 requirement |
| 1.17.0  | 2026-07-15 | Add NPS-023 (Threat Model Phase 5: secure boot, first full pass on TB-BOOT); amend NPS-017 §4.5 (Secure Boot status reporting) and NPS-001 §5 (phase-transition order validation); add REQ-BOOT-0004; measured-boot/TPM gap logged, not resolved |
| 1.18.0  | 2026-07-15 | Add NPS-024 (Threat Model Phase 6: AI, first full pass on TB-AI); amend NPS-015 §5.2/§5.3/§5.4 and add §5.5 (unspoofable confirmation UI, corrected file-search capability, persistence-mechanism exclusion, suggestion audit log); add REQ-AI-0003 and REQ-AI-0004 |

---
**End of Document**
