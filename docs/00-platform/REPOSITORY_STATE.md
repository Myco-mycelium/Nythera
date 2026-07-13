# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nythera repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-07-13

## Current Milestone
Milestone 9 — Architecture Group Review: complete. Every document from
M1–M8 has been reviewed; documents whose own text does not gate acceptance
on a pending benchmark or an unresolved dependency have moved to
`Accepted`. Two items — VR scoping (NPS-012 §5.1/NPS-009 §8) and the
scheduler algorithm (NPS-001 §7) — were also resolved during this review
(the former deferred explicitly to a future milestone, the latter decided
via new ADR-0013).

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
10 accepted, 3 held (named blockers below).

- [x] ADR-0001 Diátaxis + MkDocs Material — Accepted
- [x] ADR-0002 Copy-on-write filesystem — Accepted
- [x] ADR-0003 Game disk images with overlay — Accepted
- [x] ADR-0004 Containerized execution model — Accepted
- [x] ADR-0005 Windows compatibility translation layer — Accepted
- [x] ADR-0006 Hybrid microkernel as kernel base — Accepted
- [ ] ADR-0007 Zstandard as default compression codec — **Proposed**, blocked on compression-level benchmark data
- [x] ADR-0008 AOSP-based container runtime for Android compatibility — Accepted
- [ ] ADR-0009 Per-container token-bucket IPC rate limiting — **Proposed**, blocked on default bucket-parameter benchmark data
- [x] ADR-0010 Vulkan as native graphics API foundation — Accepted
- [x] ADR-0011 AI assistant runs as an ordinary capability-scoped container — Accepted
- [x] ADR-0012 NyHAL pluggable kernel abstraction layer — Accepted
- [ ] ADR-0013 EEVDF-derived scheduler with real-time priority class — **Proposed**, algorithm family decided, tuning parameters blocked on benchmark data

## Specifications (NPS)
13 accepted, 4 held (named blockers below).

- [x] NPS-001 Kernel Architecture and Boot (NyKernel Backend) — Accepted
- [ ] NPS-002 Process and Thread Model — **Draft**, real-time scheduling numbers require benchmark data (§9, self-blocking)
- [ ] NPS-003 Inter-Process Communication and Capability Passing — **Draft**, IPC round-trip latency must be benchmarked before exiting Draft (§6.1, self-blocking)
- [x] NPS-004 NyFS Filesystem Core — Accepted
- [ ] NPS-005 Transparent Compression Policy — **Draft**, transitively blocked on ADR-0007 (defines default levels tied to the still-Proposed codec ADR)
- [x] NPS-006 Nythera Game/Application Image Format (.nygi) and Overlay — Accepted
- [x] NPS-007 Windows Compatibility Runtime — Accepted (ARM translation remains an explicitly deferred, unsolved risk — §7)
- [x] NPS-008 Android Compatibility Runtime — Accepted (ARM translation remains an explicitly deferred, unsolved risk — §7)
- [x] NPS-009 Adaptive UI Shell — Accepted (VR resolved: explicitly deferred to a future milestone, not an open mode definition)
- [ ] NPS-010 Container Runtime — **Draft**, transitively blocked on ADR-0009 (§7.1 normatively requires its still-Proposed rate-limiting mechanism)
- [x] NPS-011 Capability Registry — Accepted (17 capabilities registered; continuous by design, new entries added via normal change process)
- [x] NPS-012 Controller and Input Subsystem — Accepted (VR capability formally deferred, not left ambiguous — §5.1)
- [x] NPS-013 GPU Feature Support — Accepted (§7.3 documents current FSR/XeSS/FSR4 vendor SDK status, verified 2026-07-13)
- [x] NPS-014 Emulator Hub — Accepted
- [x] NPS-015 Local AI Assistant — Accepted
- [x] NPS-016 Optional Cloud Synchronization — Accepted
- [x] NPS-017 NyHAL — Kernel Abstraction Layer and Backend Contract — Accepted

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
Benchmark-gated (unblocks the 3 ADRs + 4 NPS documents held above):
1. Benchmark IPC round-trip latency (unblocks NPS-003, transitively NPS-010's remaining path once ADR-0009 also clears).
2. Benchmark default IPC token-bucket parameters (unblocks ADR-0009, then NPS-010 §7.1).
3. Benchmark Zstd compression levels, install size vs. load time (unblocks ADR-0007, then NPS-005).
4. Benchmark EEVDF time-slice/weight-curve/real-time-admission tuning (unblocks ADR-0013 in full; algorithm family is already decided).
5. Benchmark default CPU/memory resource-limit values (NPS-010 §9, independent of the ADR-0009 blocker).

Not benchmark-gated:
6. Assign real subsystem owners in `SUBSYSTEM_OWNERS.md` (currently all Unassigned) as contributors join.
7. Resolve shared ARM instruction-translation approach (NPS-007 §7 / NPS-008 §7) — acknowledged limitation, not yet a solved design.
8. Scope VR integration end-to-end (input, rendering, UI mode) — currently deferred, not designed (NPS-012 §5.1, NPS-009 §7 note).
9. Evaluate vendor-neutral upscaling integration point (NPS-013 §9) given the FSR/XeSS/FSR4 licensing landscape documented in §7.3.
10. Begin implementation work on the Linux Backend (NPS-017 §6) as the practical near-term NyHAL target.
11. Decide NyFS's Linux Backend implementation strategy — FUSE, kernel module, or user-space daemon (NPS-017 §8).
12. Expand NPS-011 Android permission mapping incrementally as gaps are found (NPS-011 §6).
13. Configure CI build for the MkDocs Material site.
