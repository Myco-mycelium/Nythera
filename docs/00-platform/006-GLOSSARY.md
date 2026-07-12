---
title: Glossary
document_id: NPC-006
version: 1.0.0
status: Draft
classification: Reference
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: Continuous
depends_on: [NTM-000]
---

# NPC-006 — Glossary

Terms are added as they enter canonical use. Entries **MUST** stay short and
factual; design rationale belongs in `docs/explanation/`, not here.

| Term | Definition |
|------|------------|
| **ABI** | Application Binary Interface — a binary compatibility contract between compiled components. |
| **ADR** | Architecture Decision Record — a document capturing a specific decision, alternatives, and rationale. |
| **Capability** | A discrete, named permission (e.g. Camera, Network) an application may request. |
| **Capability Registry** | The canonical list of all capabilities recognized by the platform. |
| **Compatibility Layer** | A subsystem translating a foreign application format (Windows, Android) to native Nythera execution without full emulation. |
| **Container** | The isolated execution boundary in which every application runs, native or compatibility-layer. |
| **NPC** | Nythera Platform Constitution document — normative governance document. |
| **NPS** | Nythera Proposal for Specification — a technical specification document. |
| **NTM** | Nythera Manifest — the foundational, timeless philosophy document (NTM-000). |
| **NyFS** | Working name for Nythera's proposed copy-on-write filesystem (see ADR-0002). |
| **.nygi** | Working extension for a Nythera Game Image — a compressed, mountable game/application disk image (see ADR-0003). |
| **Overlay** | A writable copy-on-write layer paired with a read-only image, used for saves, mods, and installer writes. |
| **Package Format** | The canonical installable unit format for native Nythera applications. |
| **Subsystem Owner** | The individual or group responsible for a subsystem's specifications and reference implementation. |

## Revision History

| Version | Date       | Change        |
|---------|------------|----------------|
| 1.0.0   | 2026-07-12 | Initial glossary at bootstrap |

---
**End of Document**
