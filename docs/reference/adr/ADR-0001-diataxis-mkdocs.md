---
title: Adopt Diátaxis + MkDocs Material for Documentation
document_id: ADR-0001
version: 1.0.0
status: Accepted
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-12
depends_on: [NPC-001]
---

# ADR-0001 — Adopt Diátaxis + MkDocs Material

## Context
Nythera's documentation will grow across many subsystems over many years,
authored by both humans and AI assistants. Without a structural convention,
large documentation sets tend to collapse into an unsorted pile of files
mixing tutorials, reference material, and design rationale.

## Decision
Nythera adopts the Diátaxis framework (tutorials / how-to / reference /
explanation) for `docs/`, rendered with MkDocs Material as the documentation
site generator.

## Alternatives Considered
- **Flat wiki-style docs** — rejected; does not scale past a few dozen pages.
- **Docusaurus** — viable alternative generator; MkDocs Material chosen for
  simpler Python-based tooling consistent with the project's tooling stack.
- **Custom documentation framework** — rejected as unjustified complexity
  per NTM-000 §4 ("Simplicity").

## Consequences
- All new documentation MUST be placed in the correct Diátaxis category
  (NPC-003 §3).
- The doc site MUST build without manual post-processing (NPC-003 §4.4).

## Status
Accepted — 2026-07-12.
