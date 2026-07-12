# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nythera repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-07-12

## Current Milestone
Milestone 5 — Runtime (in progress). Milestones 1, 2, 3, 4 complete.

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

## Specifications (NPS)
0 accepted, 9 drafted.

- [x] NPS-001 Kernel Architecture and Boot — Draft
- [x] NPS-002 Process and Thread Model — Draft
- [x] NPS-003 Inter-Process Communication and Capability Passing — Draft
- [x] NPS-004 NyFS Filesystem Core — Draft
- [x] NPS-005 Transparent Compression Policy — Draft
- [x] NPS-006 Nythera Game/Application Image Format (.nygi) and Overlay — Draft
- [x] NPS-007 Windows Compatibility Runtime — Draft
- [x] NPS-008 Android Compatibility Runtime — Draft
- [x] NPS-009 Adaptive UI Shell — Draft

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
6. Configure CI build for the MkDocs Material site.
7. Begin Milestone 6 (Security): container runtime NPS, capability registry population.
