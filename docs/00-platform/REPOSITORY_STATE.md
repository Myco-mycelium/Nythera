# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nythera repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-07-12

## Current Milestone
Milestone 3 — Core Architecture (in progress). Milestone 1 complete.

## Governance Documents

- [x] NTM-000 The Nythera Manifest — Accepted
- [x] NPC-001 Project Constitution — Draft
- [x] NPC-002 AI Collaboration Protocol — Draft
- [x] NPC-003 Engineering Handbook — Draft
- [x] NPC-004 Specification Index — Draft
- [x] NPC-005 ADR Index — Draft
- [x] NPC-006 Glossary — Draft
- [x] NPC-007 Project Roadmap — Draft

## Architecture Decision Records

- [x] ADR-0001 Diátaxis + MkDocs Material — Accepted
- [x] ADR-0002 Copy-on-write filesystem — Proposed
- [x] ADR-0003 Game disk images with overlay — Proposed
- [x] ADR-0004 Containerized execution model — Proposed
- [x] ADR-0005 Windows compatibility translation layer — Proposed
- [x] ADR-0006 Hybrid microkernel as kernel base — Proposed

## Specifications (NPS)
0 accepted, 3 drafted.

- [x] NPS-001 Kernel Architecture and Boot — Draft
- [x] NPS-002 Process and Thread Model — Draft
- [x] NPS-003 Inter-Process Communication and Capability Passing — Draft

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
1. Architecture Group review to move NPC-001/002/003 to Accepted (Milestone 2).
2. Assign subsystem owners per NPC-001 §3.2, including a Core Architecture owner.
3. Benchmark IPC round-trip latency before NPS-003 exits Draft (per NPC-002 §5.2).
4. Decide scheduler algorithm and secure-boot key management (NPS-001 §7 open questions).
5. Configure CI build for the MkDocs Material site.
