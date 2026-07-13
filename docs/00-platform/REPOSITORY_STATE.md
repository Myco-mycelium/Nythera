# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nythera repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-07-12

## Current Milestone
Milestone 8 — AI Subsystem: complete. All eight original roadmap milestones
(M1–M8) now have draft-level content. A cross-cutting addition — NyHAL
pluggable kernel backend (ADR-0012, NPS-017) — has also landed, reframing
NPS-001 as the NyKernel-specific backend rather than the only backend.

## Governance Documents

- [x] NTM-000 The Nythera Manifest — Accepted
- [x] NPC-001 Project Constitution — Accepted
- [x] NPC-002 AI Collaboration Protocol — Accepted
- [x] NPC-003 Engineering Handbook — Accepted
- [x] NPC-004 Specification Index — Draft
- [x] NPC-005 ADR Index — Draft
- [x] NPC-006 Glossary — Draft
- [x] NPC-007 Project Roadmap — Draft
- [x] NPC-008 Subsystem Owners — Draft (all subsystems currently Unassigned)

## Architecture Decision Records

- [x] ADR-0001 Diátaxis + MkDocs Material — Accepted
- [x] ADR-0002 Copy-on-write filesystem — Proposed
- [x] ADR-0003 Game disk images with overlay — Proposed
- [x] ADR-0004 Containerized execution model — Proposed
- [x] ADR-0005 Windows compatibility translation layer — Proposed
- [x] ADR-0006 Hybrid microkernel as kernel base — Proposed
- [x] ADR-0007 Zstandard as default compression codec — Proposed
- [x] ADR-0008 AOSP-based container runtime for Android compatibility — Proposed
- [x] ADR-0009 Per-container token-bucket IPC rate limiting — Proposed
- [x] ADR-0010 Vulkan as native graphics API foundation — Proposed
- [x] ADR-0011 AI assistant runs as an ordinary capability-scoped container — Proposed
- [x] ADR-0012 NyHAL pluggable kernel abstraction layer — Proposed

## Specifications (NPS)
0 accepted, 17 drafted.

- [x] NPS-001 Kernel Architecture and Boot (NyKernel Backend) — Draft
- [x] NPS-002 Process and Thread Model — Draft
- [x] NPS-003 Inter-Process Communication and Capability Passing — Draft
- [x] NPS-004 NyFS Filesystem Core — Draft
- [x] NPS-005 Transparent Compression Policy — Draft
- [x] NPS-006 Nythera Game/Application Image Format (.nygi) and Overlay — Draft
- [x] NPS-007 Windows Compatibility Runtime — Draft
- [x] NPS-008 Android Compatibility Runtime — Draft
- [x] NPS-009 Adaptive UI Shell — Draft
- [x] NPS-010 Container Runtime — Draft
- [x] NPS-011 Capability Registry — Draft (17 capabilities registered)
- [x] NPS-012 Controller and Input Subsystem — Draft
- [x] NPS-013 GPU Feature Support — Draft
- [x] NPS-014 Emulator Hub — Draft
- [x] NPS-015 Local AI Assistant — Draft
- [x] NPS-016 Optional Cloud Synchronization — Draft
- [x] NPS-017 NyHAL — Kernel Abstraction Layer and Backend Contract — Draft

## ABI / API References
Not started.

## Package Format
Not started.

## Source Code
Not started.

## Build System
Not started.

## Documentation Site
Structure created; MkDocs Material configuration pending.

## Next Actions
1. Assign real subsystem owners in `SUBSYSTEM_OWNERS.md` (currently all Unassigned) as contributors join.
2. Benchmark IPC round-trip latency before NPS-003 exits Draft (per NPC-002 §5.2).
3. Benchmark Zstd compression levels (install size vs. load time) before ADR-0007 exits Proposed.
4. Decide scheduler algorithm and secure-boot key management (NPS-001 §7 open questions).
5. Resolve shared ARM instruction-translation approach (NPS-007 §7 / NPS-008 §7) before either runtime exits Draft.
6. Benchmark default IPC token-bucket parameters before ADR-0009 exits Proposed.
7. Expand NPS-011 Android permission mapping incrementally as gaps are found (NPS-011 §6).
8. Scope VR integration to define the deferred VR input capability (NPS-012 §5.1, NPS-009 §8).
9. Evaluate vendor-neutral upscaling integration point (NPS-013 §9) once specific SDKs are reviewed.
10. Begin implementation work on the Linux Backend (NPS-017 §6) as the practical near-term NyHAL target.
11. Decide NyFS's Linux Backend implementation strategy — FUSE, kernel module, or user-space daemon (NPS-017 §8).
12. Configure CI build for the MkDocs Material site.
13. Architecture Group review of ADR-0012/NPS-017, since it is cross-cutting per NPC-001 §3.1 and affects every backend-facing contract.
