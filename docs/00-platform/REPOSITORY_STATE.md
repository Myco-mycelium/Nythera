# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nythera repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-07-13

## Current Milestone
Milestone 9 — Architecture Group Review: complete, plus a follow-up
backlog pass (2026-07-13) that closed most remaining non-benchmark open
items: secure boot key management (ADR-0014), shared ARM instruction
translation approach (ADR-0015), NyFS's Linux Backend implementation
strategy (ADR-0016), an expanded Android permission mapping (NPS-011), a
CI-verified documentation build, and an honest benchmark methodology
document for everything that still requires real hardware measurement.
What remains open now genuinely requires either real contributors
(subsystem ownership) or actual implementation work (Linux Backend code,
then benchmarking it) — not more design decisions.

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
10 accepted, 6 held (named blockers below; 3 are new decisions this pass,
not benchmark-blocked — they're Proposed only pending Architecture Group
sign-off, same as any other new ADR).

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
- [ ] ADR-0014 UEFI Secure Boot with user-enrollable keys — **Proposed**, pending Architecture Group review (not benchmark-blocked)
- [ ] ADR-0015 Shared dynamic binary translation for ARM/x86 — **Proposed**, approach decided; performance validation blocked on benchmark data
- [ ] ADR-0016 NyFS Linux Backend as user-space FUSE filesystem — **Proposed**, initial strategy decided; kernel-module fallback blocked on FUSE-overhead benchmark data

## Specifications (NPS)
13 accepted, 4 held (named blockers below).

- [x] NPS-001 Kernel Architecture and Boot (NyKernel Backend) — Accepted
- [ ] NPS-002 Process and Thread Model — **Draft**, real-time scheduling numbers require benchmark data (§9, self-blocking)
- [ ] NPS-003 Inter-Process Communication and Capability Passing — **Draft**, IPC round-trip latency must be benchmarked before exiting Draft (§6.1, self-blocking)
- [x] NPS-004 NyFS Filesystem Core — Accepted
- [ ] NPS-005 Transparent Compression Policy — **Draft**, transitively blocked on ADR-0007 (defines default levels tied to the still-Proposed codec ADR)
- [x] NPS-006 Nythera Game/Application Image Format (.nygi) and Overlay — Accepted
- [x] NPS-007 Windows Compatibility Runtime — Accepted (ARM translation approach now decided via ADR-0015; performance validation still pending benchmark data)
- [x] NPS-008 Android Compatibility Runtime — Accepted (ARM translation approach now decided via ADR-0015; performance validation still pending benchmark data)
- [x] NPS-009 Adaptive UI Shell — Accepted (VR resolved: explicitly deferred to a future milestone, not an open mode definition)
- [ ] NPS-010 Container Runtime — **Draft**, transitively blocked on ADR-0009 (§7.1 normatively requires its still-Proposed rate-limiting mechanism)
- [x] NPS-011 Capability Registry — Accepted (25 capabilities registered: 17 from Milestone 8 plus 8 new Android permission mappings added this pass — contacts, calendar, telephony, SMS, sensors, media library, NFC, biometric; still intentionally incomplete by design)
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
Structure created; MkDocs Material configured with full nav (zero warnings
under `mkdocs build --strict`); CI workflow (`.github/workflows/docs.yml`)
builds and deploys to GitHub Pages on push to `main`. Version pinned via
`requirements-docs.txt` due to MkDocs Material's own public warning about
breaking, currently-unsuitable-for-production changes in MkDocs 2.0.

## Next Actions
Benchmark-gated (unblocks the 3 ADRs + 4 NPS documents held above; no
numbers exist yet — see `tests/BENCHMARK_PLAN.md` for methodology):
1. Benchmark IPC round-trip latency (unblocks NPS-003, transitively NPS-010's remaining path once ADR-0009 also clears).
2. Benchmark default IPC token-bucket parameters (unblocks ADR-0009, then NPS-010 §7.1).
3. Benchmark Zstd compression levels, install size vs. load time (unblocks ADR-0007, then NPS-005).
4. Benchmark EEVDF time-slice/weight-curve/real-time-admission tuning (unblocks ADR-0013 in full; algorithm family is already decided).
5. Benchmark default CPU/memory resource-limit values (NPS-010 §9, independent of the ADR-0009 blocker).
6. Benchmark FUSE overhead for NyFS's Linux Backend (ADR-0016; determines whether the FUSE decision holds or needs a kernel-module fallback).

Not benchmark-gated, resolved this pass:
- ~~Resolve shared ARM instruction-translation approach~~ — ADR-0015 (shared dynamic binary translation, JIT + hot-path cache).
- ~~Scope VR integration~~ — explicitly deferred to a future milestone (NPS-012 §5.1), not designed further; this was the decision, not a placeholder.
- ~~Evaluate vendor-neutral upscaling integration point~~ — NPS-013 §7.3, grounded in current (2026-07-13) FSR/XeSS/FSR4 vendor SDK status.
- ~~Decide NyFS's Linux Backend implementation strategy~~ — ADR-0016 (FUSE first, kernel-module fallback open pending benchmark #6 above).
- ~~Decide secure boot key management~~ — ADR-0014 (UEFI Secure Boot, shim-equivalent chain, user-enrollable keys).
- ~~Configure CI build for the MkDocs Material site~~ — `.github/workflows/docs.yml`, verified locally with `mkdocs build --strict` (zero warnings) before committing.
- Expand NPS-011 Android permission mapping — 8 new capabilities added (contacts, calendar, telephony, SMS, sensors, media library, NFC, biometric); still intentionally incomplete per NPS-011 §6, expand further as gaps are found.

Genuinely still open, not fabricable:
7. Assign real subsystem owners in `SUBSYSTEM_OWNERS.md` (currently all Unassigned) — requires actual contributors, not something to invent.
8. Begin implementation work on the Linux Backend (NPS-017 §6) — this is code, not documentation; the design is now unblocked enough to start (containers/capabilities/IPC/storage strategy all decided), but actually writing it is a separate phase of work.
9. Choose a real license (`LICENSE` is still the Milestone 1 placeholder — "no rights granted... until a formal license is adopted"). This is a legal/business decision for the repository owner, not one to pick unilaterally on their behalf.
10. Enable GitHub Pages with source "GitHub Actions" (Settings → Pages) so `.github/workflows/docs.yml`'s deploy step has somewhere to publish to — the workflow runs regardless, but won't be visibly served until this is set.

Documentation hygiene, fixed this pass:
- A prior review pass (Milestone 9) left several documents with a Markdown
  table-formatting bug: the row recording that milestone's own review
  (e.g. "Architecture Group review completed... Status: Draft → Accepted")
  had gotten separated from its revision-history table by a blank line,
  rendering as an orphaned single-row table instead of part of the
  sequence. This affected all 13 `Accepted` NPS documents (every one that
  went through the Milestone 9 review): NPS-001, 004, 006, 007, 008, 009,
  011, 012, 013, 014, 015, 016, 017 — fixed across the M10 commit and this
  pass; verified via `mkdocs build --strict` and a repo-wide grep for the
  pattern, which is now clean.
- `mkdocs.yml`'s `repo_url` was still the bootstrap placeholder
  (`your-org/Nythera`) despite the real repository existing since
  Milestone 1's first push; corrected to `Myco-mycelium/Nythera`.
