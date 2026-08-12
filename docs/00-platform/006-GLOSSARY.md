---
title: Glossary
document_id: NPC-006
version: 1.1.0
status: Draft
classification: Reference
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-08-12
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
| **Backend** | An implementation of the NyHAL contract (see NyHAL) on a specific host — e.g. Linux Backend, Experimental Backend, NyKernel Backend. |
| **NyCore** | The behavioral contracts layer: containers (NPS-002, NPS-010), capabilities (NPS-011), IPC (NPS-003), storage (NPS-004..006). |
| **NyHAL** | Nythera Kernel Abstraction Layer — the backend abstraction boundary and contract every backend satisfies (NPS-017, ADR-0012). |
| **NyKernel** | The hybrid microkernel target defined in ADR-0006 and specified in NPS-001 (one NyHAL backend among several). |
| **NyRuntime** | The layer above NyCore: compatibility runtimes (NPS-007/008), adaptive UI shell (NPS-009), gaming (NPS-012..014), AI (NPS-015..016). |
| **NySDK** | The developer-facing SDK exposing the public API (API-001) to applications, above NyRuntime. |
| **.nypkg** | Working extension for the Nythera Package Format — the signed, installable unit processed by the installer (NPS-026). |
| **Object Registry** | The canonical catalogue of platform object types with fields, lifecycle, permissions, serialization, and relationships (NPS-025). |
| **REQ** | A Requirement — an individually-testable, traceable obligation (NPC-009), e.g. `REQ-IPC-0003`. |

## Revision History

| Version | Date       | Change        |
|---------|------------|----------------|
| 1.0.0   | 2026-07-12 | Initial glossary at bootstrap |
| 1.1.0   | 2026-08-12 | Add terms in canonical use since bootstrap: Backend, NyCore, NyHAL, NyKernel, NyRuntime, NySDK, .nypkg, Object Registry, REQ |

---
**End of Document**
