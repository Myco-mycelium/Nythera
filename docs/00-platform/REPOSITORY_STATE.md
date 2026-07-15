# Repository State

This file is the canonical, human-readable snapshot of what exists in the
Nythera repository. Update it in the same commit as any document or code
change, per NPC-001 §6.5 and NPC-003 §6.2.

## Last Updated
2026-07-13

## Current Milestone
Milestones 9–11 complete (Architecture Group Review, backlog closure
pass, response to external review), plus a first tested code spike
(`nyctr` container primitive). Milestone 12 — the phased security threat
model — is in progress: Phase 1 (`NPS-018` methodology, `NPS-019` attack
surface enumeration) and Phase 2 (`NPS-020` STRIDE analysis across all
10 trust boundaries) are both done. Phase 2 produced 3 genuine findings
requiring action, not just observations: two closed immediately by
amending `NPS-001` and `NPS-003` directly (with new `REQ-GPU-0002` /
`REQ-IPC-0003` entries), and one (no package signing — only checksums)
that strengthens the case for Milestone 11's package-format gap category
rather than being fixable by a quick amendment. Phases 3–7 remain planned
and sequenced but not started — see
`docs/reference/security/README.md`. Milestone 11's remaining 9 gap
categories (diagrams, API reference, ABI specification, full object
registry, package format split, governance expansion, build architecture
docs, performance budgets, developer onboarding) are still logged as a
prioritized backlog in `007-PROJECT_ROADMAP.md` — not built yet.

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
- [x] NPC-009 Requirements Database — Draft (in response to external review feedback)

## Architecture Decision Records
10 accepted, 6 held (named blockers below; 3 are new decisions pending
Architecture Group sign-off, not benchmark-blocked), 1 rejected.

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
- [x] ADR-0017 Reject domain-grouped NPS renumbering — **Rejected** (the project's first; considered and explicitly declined, not left unresolved)

## Specifications (NPS)
13 accepted, 6 held (4 named benchmark/dependency blockers, plus NPS-018
and NPS-019 which are new Draft documents pending Architecture Group
sign-off, not benchmark-blocked).

- [x] NPS-001 Kernel Architecture and Boot (NyKernel Backend) — Accepted (v1.2.0: GPU command buffer validation + submission timeout added, closing threat model findings FIND-KERNEL-001/003)
- [ ] NPS-002 Process and Thread Model — **Draft**, real-time scheduling numbers require benchmark data (§9, self-blocking)
- [ ] NPS-003 Inter-Process Communication and Capability Passing — **Draft**, IPC round-trip latency must be benchmarked before exiting Draft (§6.1, self-blocking); v1.1.0 added a shared-memory zeroing requirement, closing threat model finding FIND-CONTAINER-003
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
- [x] NPS-018 Threat Model Methodology and Trust Boundaries — Draft (Threat Model Phase 1a)
- [x] NPS-019 Attack Surface Enumeration — Draft (Threat Model Phase 1b, 24 surfaces catalogued)
- [x] NPS-020 STRIDE Analysis per Trust Boundary — Draft (Threat Model Phase 2, 10 boundaries, 3 findings drove real spec amendments this pass)

## Requirements Database
NPC-009 (Draft) + seed ledger at `docs/reference/requirements/REQUIREMENTS.md`:
31 requirements across all 17 domain prefixes. Nearly all traced to
`Accepted` specs; one (`REQ-IPC-0003`) traces to still-`Draft` NPS-003,
called out explicitly rather than silently overstating coverage quality.
One entry (`REQ-NYHAL-0003`) marked `Implemented (partial)`, referencing
the `nyctr` PoC with an explicit caveat about what it doesn't cover. Not
full coverage of NPS-001..020 by design (NPC-009 §7.3) — expand
incrementally, and going forward new normative additions should cite a
REQ ID from the start (NPC-009 §7.2).

## ABI / API References
Not started.

## Package Format
Not started.

## Source Code
One tested proof-of-concept: `source/nyhal-linux-backend/poc-container/`
(`nyctr.py`) — proves the most basic container primitive (PID/mount/UTS/
user namespace isolation + a cgroup memory/pid limit) works on stock
Linux, per NPS-017 §4.1. Verified with a repeatable test script
(`test_nyctr.sh`), all 4 cases passing. This is explicitly a spike, not
the start of a real implementation — see the PoC's own README for what it
does not prove (no capability enforcement, no IPC, no storage, no boot
integration — NPS-017 §4.2 through §4.5 remain untouched). Every other
subsystem is unstarted.

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
8. Begin implementation work on the Linux Backend (NPS-017 §6) — the container-primitive spike (`source/nyhal-linux-backend/poc-container/`) is done and passing; next up is capability enforcement (seccomp/LSM, NPS-017 §4.2), which needs the capability registry (NPS-011) mapped onto concrete Linux mechanisms, not just documented in the abstract.
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

External review response (2026-07-13), not fabricable/decided instantly:
9. Work through Milestone 11's remaining prioritized backlog
   (`007-PROJECT_ROADMAP.md`) — architecture diagrams, API reference, ABI
   specification, full object registry, package format split, governance
   expansion, build architecture docs, performance budgets, developer
   onboarding. Each is roughly the size of a prior milestone on its own;
   not attempted in a single pass.
10. Continue the threat model (Milestone 12, `docs/reference/security/`):
    Phase 3 (Privilege Boundaries & Capability Escalation Analysis) is
    next, deepening the `TB-CAPABILITY` findings NPS-020 §6 flagged as
    needing fuller treatment (`FIND-CAPABILITY-001`, `FIND-CAPABILITY-002`).
    Phases 4–7 (container escape, secure boot, AI, package trust) follow.
11. Elevate priority on Milestone 11's package-format gap category
    (specifically digital signatures) — Phase 2's `FIND-PACKAGE-001`
    found that `.nygi` integrity currently relies on checksums alone,
    which don't establish publisher authenticity; an attacker can tamper
    with an image and simply recompute a valid checksum. Not fixable by a
    quick amendment like the other two Phase 2 findings; needs a real
    package-signing/PKI specification.
