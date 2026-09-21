---
title: ADR Index
document_id: NPC-005
version: 1.22.0
status: Draft
classification: Reference
owners:
  - Nyrqis Architecture
created: 2026-07-12
updated: 2026-09-21
ai_assisted: true
review_cycle: Continuous
depends_on: [NPC-001]
---

# NPC-005 — Architecture Decision Record Index

Architecture Decision Records (ADRs) capture a specific decision, the
alternatives considered, and the reasoning — per NPC-001 §6. Full ADR text
lives under `docs/reference/adr/ADR-XXXX-slug.md`. This index tracks status
only.

| ID | Title | Status | Date | Supersedes |
|----|-------|--------|------|------------|
| ADR-0001 | Adopt Diátaxis + MkDocs Material for documentation | Accepted | 2026-07-12 | — |
| ADR-0002 | Adopt copy-on-write filesystem with built-in compression | Accepted | 2026-07-13 | — |
| ADR-0003 | Games distributed as mounted disk images with writable overlay | Accepted | 2026-07-13 | — |
| ADR-0004 | Containerized execution model for all application classes | Accepted | 2026-07-13 | — |
| ADR-0005 | Windows compatibility via translation layer, not full emulation | Accepted | 2026-07-13 | — |
| ADR-0006 | Adopt a hybrid microkernel as the Nyrqis kernel base | Accepted | 2026-07-13 | — |
| ADR-0007 | Adopt Zstandard as the default compression codec | Accepted | 2026-07-12 | — |
| ADR-0008 | Adopt an AOSP-based container runtime for Android compatibility | Accepted | 2026-07-13 | — |
| ADR-0009 | Per-container token-bucket rate limiting for IPC | Accepted | 2026-07-12 | — |
| ADR-0010 | Adopt Vulkan as the native graphics API foundation | Accepted | 2026-07-13 | — |
| ADR-0011 | AI assistant runs as an ordinary capability-scoped container | Accepted | 2026-07-13 | — |
| ADR-0012 | Adopt NyHAL as a pluggable kernel abstraction layer | Accepted | 2026-07-13 | — |
| ADR-0013 | Adopt an EEVDF-derived scheduler with a real-time priority class | Accepted | 2026-07-13 | — |
| ADR-0014 | Adopt UEFI Secure Boot with user-enrollable keys | Proposed | 2026-07-13 | — |
| ADR-0015 | Shared dynamic binary translation approach for ARM/x86 compatibility | Proposed | 2026-07-13 | — |
| ADR-0016 | NyFS Linux Backend implemented as a user-space FUSE filesystem | Accepted | 2026-07-13 | — |
| ADR-0017 | Reject domain-grouped NPS renumbering | **Rejected** | 2026-07-13 | — |
| ADR-0018 | Hash-chained append-only log for capability audit records | Accepted | 2026-07-13 | — |
| ADR-0019 | Journal commit as the default NyFS save() mode | Proposed | 2026-08-12 | — |
| ADR-0020 | Implementation languages and the platform boundary | **Accepted** | 2026-08-13 | ADR-0020 v1 superseded by v2 (Python and Rust, 2026-08-12) |
| ADR-0021 | NyRuntime direction — IPC serving loop behind the FFI boundary | **Accepted** | 2026-08-15 | — |
| ADR-0022 | NyVault — storage as a daemon-hosted service on the IPC transport | Accepted | 2026-08-15 | — |
| ADR-0023 | NyVault key manager — envelope encryption with Rust-held key custody | Accepted | 2026-08-15 | — |
| ADR-0024 | Streaming data plane — chunked framing for large CALL payloads | Proposed | 2026-08-16 | — |
| ADR-0025 | NUI runtime consumption | Accepted | 2026-08-16 | status cell added 2026-09-20 — the index row itself was missing (caught by the frontmatter-vs-index sweep; its 2026-09-06 frontmatter flip is NOT recorded in any Group decision log — same flag as ADR-0019, see that row) |
| ADR-0026 | Wayland Display Server Integration for the Nyrqis Shell | Accepted | 2026-09-01 | status cell added 2026-09-20 — the index row itself was missing; Accepted by implementation record (M13's display-server integration milestone, roadmap-checked), no separate AG session noted |
| ADR-0027 | Wire-verified protocol constants and fail-closed acceptance gates | **Accepted** | 2026-09-16 | — |

## ADR Statuses

- **Proposed** — open for discussion, not yet binding.
- **Accepted** — binding; implementation MUST conform.
- **Superseded** — replaced by a later ADR (see `Supersedes` column).
- **Rejected** — considered and declined; kept for historical record.

## Revision History

| Version | Date       | Change              |
|---------|------------|----------------------|
| 1.0.0   | 2026-07-12 | Initial index        |
| 1.1.0   | 2026-07-12 | Add ADR-0006 (kernel base selection) |
| 1.2.0   | 2026-07-12 | Add ADR-0007 (compression codec selection) |
| 1.3.0   | 2026-07-12 | Add ADR-0008 (Android runtime approach) |
| 1.4.0   | 2026-07-12 | Add ADR-0009 (IPC rate limiting) |
| 1.5.0   | 2026-07-12 | Add ADR-0010 (Vulkan graphics foundation) |
| 1.6.0   | 2026-07-12 | Add ADR-0011 (AI assistant containerization) |
| 1.7.0   | 2026-07-12 | Add ADR-0012 (NyHAL pluggable kernel backend) |
| 1.8.0   | 2026-07-13 | Milestone 9 review: accept ADR-0002/0003/0004/0005/0006/0008/0010/0011/0012; add ADR-0013 (scheduler algorithm, Proposed — tuning-blocked). ADR-0007 and ADR-0009 remain Proposed pending benchmark data. |
| 1.9.0   | 2026-07-13 | Add ADR-0014 (secure boot), ADR-0015 (shared ARM translation), ADR-0016 (NyFS Linux Backend FUSE strategy) — closing three of the backlog's open architecture-decision items |
| 1.10.0  | 2026-07-13 | Add ADR-0017 — the project's first Rejected ADR, declining a proposed NPS domain-renumbering scheme |
| 1.11.0  | 2026-07-13 | Add ADR-0018 (hash-chained audit log), from threat model Phase 3 |
| 1.12.0  | 2026-08-12 | Add ADR-0019 (journal commit as the default save() mode) — the Architecture Group review package for the 2026-08-12 implementer default flip |
| 1.13.0  | 2026-08-12 | Add ADR-0020 (Python and Rust as the implementation languages) — first recorded language strategy |
| 1.14.0  | 2026-08-13 | ADR-0020 v2.0.0 — canonical language matrix (Rust/C++/C platform languages; NyHAL resolved Rust-first) + platform-boundary principle (platform-critical execution paths must not depend on the Python interpreter) |
| 1.15.0  | 2026-08-13 | ADR-0020 **Accepted** by Architecture Group (issue #2) — matrix and boundary rule binding |
| 1.16.0  | 2026-08-15 | Add ADR-0021 (NyRuntime direction — IPC serving loop behind the FFI boundary); ADR-0021 **Accepted** — close gate met |
| 1.17.0  | 2026-08-15 | Add ADR-0022 (NyVault storage service) and ADR-0023 (NyVault key manager) |
| 1.18.0  | 2026-08-16 | Add ADR-0024 (streaming data plane — chunked framing for large CALL payloads) |
| 1.19.0  | 2026-09-10 | ADR-0009 v1.3.1: Implementation Note — per-sender fairness implemented (`FairTokenBucket`, fair-by-default endpoints, operator control-plane ops, dynamic-shares opt-in), §32b–d benchmark record complete, review package published (`ADR-0009-review-package.md`) |
| 1.20.0  | 2026-09-16 | Add ADR-0027 (wire-verified protocol constants and fail-closed acceptance gates) — **Accepted** |
| 1.21.0  | 2026-09-20 | Add the missing ADR-0025/0026 status rows; correct six stale adr/README.md cells (0007/0009/0013/0016/0022/0023 still read pre-2026-09-19-review Proposed); the ADR-0019 frontmatter reverted to Proposed per its own governance line and this index (its 2026-09-06 flip to Accepted was not sanctioned by any Group record) — caught by the frontmatter-vs-index status sweep |
| 1.22.0  | 2026-09-21 | ADR-0009 v1.3.2 — the standing dynamic-shares-as-default item decided (AG decision log D1): static default retained; nothing remains open in the ADR. Frontmatter version caught up to this table (it had lagged at 1.18.0 through the 1.19–1.21 entries) |

---
**End of Document**
