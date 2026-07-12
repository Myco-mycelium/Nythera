---
title: Engineering Handbook
document_id: NPC-003
version: 1.0.1
status: Accepted
classification: Normative
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: Semiannual
depends_on: [NTM-000, NPC-001]
---

# NPC-003 — Engineering Handbook

## 1. Status of This Document

This document is **normative** for repository structure, documentation
standards, and contribution mechanics. RFC 2119 terms apply as in NPC-001.

---

## 2. Repository Layout

```
Nythera/
├── docs/
│   ├── 00-platform/        # Foundational governance docs (this series)
│   ├── explanation/        # Diátaxis: understanding-oriented
│   │   ├── platform/  architecture/  gaming/  ai/  storage/  runtime/  security/
│   ├── reference/          # Diátaxis: information-oriented
│   │   ├── npc/  nps/  adr/  abi/  api/  object-registry/
│   │   ├── capability-registry/  package-format/
│   ├── tutorials/          # Diátaxis: learning-oriented
│   ├── how-to/             # Diátaxis: task-oriented
│   ├── diagrams/
│   └── assets/
├── source/                 # Kernel, subsystems, runtime code
├── tools/                  # Build tooling, CLI utilities
├── tests/                  # Conformance, integration, benchmark tests
├── sdk/                    # Developer SDK and templates
├── examples/                # Example applications
└── engineering/            # RFC drafts, working notes, meeting records
```

Top-level directories **MUST NOT** be renamed without an ADR, since external
tooling (CI, doc site generation) depends on this layout.

---

## 3. Diátaxis Usage *(Informative)*

- **Tutorials** teach a newcomer by doing (e.g. "Build your first Nythera
  package"). They **MUST** assume no prior knowledge.
- **How-to guides** solve a specific task for someone who already knows the
  basics (e.g. "How to add a new capability to the registry").
- **Reference** documents facts precisely and completely (ABI layouts, API
  signatures, package format fields). Reference docs **SHOULD** contain
  minimal prose.
- **Explanation** documents provide context and reasoning ("Why Nythera uses
  image-based game installation").

A change that adds a new subsystem **SHOULD** eventually populate all four
categories, though not necessarily in the same change set.

---

## 4. Documentation Standards

4.1. Every document under `docs/reference/` **MUST** carry the front-matter
schema defined in NPC-001 §4.

4.2. Every normative document **MUST** state its dependencies via
`depends_on` so tooling can build a dependency graph and detect orphaned or
circular references.

4.3. Diagrams **SHOULD** be stored as source (Mermaid or SVG) under
`docs/diagrams/`, not as embedded binary images without a source file.

4.4. The documentation site **MUST** build with MkDocs Material from the
`docs/` tree without manual post-processing.

---

## 5. Source Code Standards

5.1. Each subsystem under `source/` **MUST** contain a `README.md` describing
its purpose and linking to its governing specification(s) in
`docs/reference/`.

5.2. Code implementing a normative interface (ABI, API, package format)
**MUST** reference the document ID and version it implements, e.g.
`// Implements: NAB-002 v1.1.0`.

5.3. Every subsystem **MUST** have a corresponding test suite under
`tests/<subsystem>/` before it can be marked `Accepted` in
`SPECIFICATION_INDEX.md`.

---

## 6. Commit and Branch Conventions

6.1. Commit messages **SHOULD** follow Conventional Commits
(`docs:`, `feat:`, `fix:`, `refactor:`, `test:`, `chore:`).

6.2. Commits that add or modify a normative document **MUST** update
`REPOSITORY_STATE.md` and, if applicable, `SPECIFICATION_INDEX.md` in the
same commit.

6.3. Branch names **SHOULD** reference the document or milestone being
worked on, e.g. `npc-004/repository-standards`.

---

## 7. Milestones *(Informative)*

Work is organized into milestones so the repository remains coherent at every
point in its history:

| Milestone | Scope |
|-----------|-------|
| M1 | Repository Bootstrap — governance docs, structure, tooling |
| M2 | Platform Constitution — NPC series complete |
| M3 | Core Architecture — kernel/runtime ADRs and initial NPS set |
| M4 | Storage — filesystem, compression, image-mounting specs |
| M5 | Runtime — Windows/Android compatibility layers |
| M6 | Security — container model, capability registry |
| M7 | Gaming Subsystem — disk-image games, controller/GPU support |
| M8 | AI Subsystem — local assistant boundaries and integration |

---

## 8. Review Requirements

8.1. A document or code change touching a subsystem listed in
`SPECIFICATION_INDEX.md` **MUST** be reviewed by that subsystem's owner.

8.2. Cross-cutting changes (touching more than one subsystem) **MUST** be
reviewed by the Architecture Group per NPC-001 §3.1.

---

## Revision History

| Version | Date       | Change                  |
|---------|------------|--------------------------|
| 1.0.0   | 2026-07-12 | Initial draft for review |
| 1.0.1   | 2026-07-12 | Architecture Group review completed. Status: Draft → Accepted (Milestone 2). |

---
**End of Document**
