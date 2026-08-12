---
title: Nyrqis
---

# Nyrqis

**Nyrqis** is an operating system project: a hybrid microkernel with a
capability-based security model, gaming-first design, and pluggable kernel
backends (NyHAL). The Linux Backend is the first real implementation and
lives in `source/nyhal-linux-backend/`.

This site is the project's living documentation — every specification
(NPS), architecture decision (ADR), and governance document (NPC) is
published here, and every change to the project is reflected in it.

## Start here

- [The Nyrqis Manifest](00-platform/000-THE_NYRQIS_MANIFEST.md) — why the project exists (NTM-000)
- [Project Roadmap](00-platform/007-PROJECT_ROADMAP.md) — what's planned and what's done
- [Repository State](00-platform/REPOSITORY_STATE.md) — the canonical snapshot of this repository
- [Specification Index](00-platform/004-SPECIFICATION_INDEX.md) — every NPS/NPC document
- [A Tour of the Repository](tutorials/first-repository-tour.md) — how the repo is laid out
- [Rebrand Notice](00-platform/REBRAND_NOTICE.md) — the project was renamed from *Nythera* to *Nyrqis* on 2026-08-12

## Status

- Milestones 9–11 complete; Milestone 12 (security threat model) phases
  1–6 done, Phase 7 (Package Trust Model) is the last planned phase.
- Linux Backend: 64/64 tests passing; default-deny seccomp posture
  implemented and verified end-to-end.
- Documentation: MkDocs Material, strict build clean, deployed to
  GitHub Pages on every push to `main`.
