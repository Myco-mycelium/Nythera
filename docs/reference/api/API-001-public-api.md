---
title: Public API Specification
document_id: API-001
version: 1.0.0
status: Draft
classification: Normative
subsystem: core-architecture
owners:
  - Nythera Architecture
created: 2026-08-12
updated: 2026-08-12
ai_assisted: true
review_cycle: Continuous
depends_on: [NTM-000, NPC-001, NPS-003, NPS-004, NPS-006, NPS-007, NPS-008, NPS-009, NPS-010, NPS-011, NPS-012, NPS-013, NPS-014, NPS-015, NPS-017, NPS-025]
---

# API-001 — Public API Specification

## 1. Status of This Document

This document is **normative** for the *shape* of Nythera's public API: the
areas it is divided into, the layering rules, the naming and versioning
conventions, and the error model. It is a `Draft`: exact function
signatures, data structures, and constants are deliberately **not**
specified yet — they will be authored against a working implementation
(per NPC-002 §5.1, technical claims about interfaces should not reach
`Review` without being verifiable) and this document will then grow a
per-area reference. Closing Milestone 11 gap category 4 (Public API
specification).

## 2. Purpose *(Informative)*

Every application on Nythera — native, Windows-compat, or Android-compat —
interacts with the platform through one public API surface. That surface
is what `NySDK` (NPS-017 §3) exposes to developers, and it **MUST** be
identical regardless of which NyHAL backend is underneath (NPS-017 §7.1).
This document defines the contract of that surface so the SDK, the
runtimes, and the ABI (ABI-001) can be designed against the same
boundaries.

## 3. Layering

3.1. The public API follows the platform layering of NPS-017 §3.1
verbatim — code in NySDK or NyRuntime **MUST NOT** reach past NyCore into
a backend's native mechanisms (NPS-017 §3.2). In API terms:

| Area | Layer | Exposed by |
|------|-------|-----------|
| NyHAL | Abstraction boundary | Backend providers, not application developers |
| NyCore | Containers, capabilities, IPC, storage | NyCore services |
| NyRuntime | Compatibility, UI shell, gaming, AI | NyRuntime services |
| NySDK | All of the above, curated for developers | The SDK |

3.2. The public API **MUST** be exposed in a way that is identical across
conformant backends — an application built against NySDK runs unmodified
on any backend (NPS-017 §7.1).

## 4. API Areas

Each area names its principal interfaces at a contract level. Signatures
are deferred (see §1); the *operations that must exist* are defined by
the governing specifications cited.

### 4.1 NyHAL Backend API
For backend implementers (not application developers): container
primitives, capability enforcement, IPC semantics, storage guarantees,
boot/lifecycle — exactly the NPS-017 §4 contract, one interface per
section. The Linux Backend's own internal API
(`source/nyhal-linux-backend/`) is a prototype of this area, not yet
conformant (NPS-017 §6).

### 4.2 NyCore API
The services every other layer builds on, one per NyCore contract:

| Interface | Governed by | Principal operations |
|-----------|-------------|----------------------|
| Container | NPS-010 §4–§7 | create, evaluate manifest, suspend, resume, terminate; resource limits |
| Capability | NPS-011, NPS-010 §5–§6 | grant, attenuate, revoke, enumerate (audit view, NPS-010 §8.2) |
| IPC | NPS-003 §3–§4 | send, receive, call, notify; endpoint create/revoke |
| Storage | NPS-004 | open, read, write, snapshot, checksum-verify |

### 4.3 Runtime API
| Interface | Governed by | Principal operations |
|-----------|-------------|----------------------|
| Windows Runtime | NPS-007 | load/translate `.exe`/`.msi`, registry read/write |
| Android Runtime | NPS-008 | load/verify `.apk`, permission→capability mapping |
| UI Shell | NPS-009 | present surface, mode query/override, window management |
| Gaming | NPS-012..014 | controller events, GPU feature query, emulator launch |
| AI | NPS-015 | suggest (never act), confirmation handling, diagnostics read |

### 4.4 Package API
Install, verify, update, uninstall, and dependency resolution of `.nypkg`
packages — the operations defined in NPS-026 §5–§10.

### 4.5 Filesystem API
The NyFS user-facing operations (NPS-004 §4): files, directories,
overlays, snapshots, transparent compression behavior, checksum
verification.

### 4.6 Window API
Surface creation, bounds, focus, presentation per mode (NPS-009 §5) —
operating on the `Window` object (NPS-025 §4.2).

### 4.7 AI API
The assistant's public surface (NPS-015): request a suggestion, receive a
confirmation decision, read diagnostics under `CAP-AI-DIAGNOSTICS-READ`.
It **MUST NOT** expose any operation that executes a system change
(NPS-015 §5.1, NTM-000 §9).

### 4.8 Gaming API
Controller input, GPU feature query, game-image mount/launch (NPS-012,
NPS-013, NPS-006 §5).

### 4.9 Plugin API
A plugin **MUST** run as a container with an explicit capability set
(NPC-001 §9.1) — there is no "trusted plugin" exception. The plugin API is
the ordinary public API plus a declared entry point and a manifest
(NPS-026 §5); plugin ABI compatibility is specified in ABI-001 §"Plugin
ABI".

## 5. Naming Conventions

5.1. API names **SHOULD** use a consistent scheme: `ny_` prefix for
NyCore-level operations, subsystem-scoped prefixes for NyRuntime areas
(e.g. `ny_win_*`, `ny_ui_*`, `ny_gpu_*`). Exact naming is fixed when
signatures are authored (§1) and **MUST NOT** change without a MINOR
version bump (NPC-001 §7).

5.2. Error names **SHOULD** follow NPS-003 §7's defined-error philosophy:
operations return a defined error rather than blocking or failing
silently (e.g. target terminated, endpoint revoked, capability missing).

## 6. Versioning

6.1. The public API **MUST** be versioned with semantic versioning
(NPC-001 §7). A breaking change to any public interface **MUST** carry a
MAJOR version increment and a migration guide under `docs/how-to/`.

6.2. The API and the ABI **MUST** version together at the platform-release
granularity: a platform release cycle publishes one API MAJOR and one ABI
MAJOR, so that "what source compiles against" and "what binaries run
against" never drift (NPC-001 §8.1).

## 7. Error Model

7.1. Every API operation **MUST** return a defined, documented result —
success or a defined error — and **MUST NOT** silently degrade.

7.2. Errors **MUST** distinguish, at minimum: capability-missing (the
caller's container lacks the required capability), not-found (no such
object), revoked (the referenced grant/endpoint was revoked, NPS-003
§4.3), and terminated (target container ended, NPS-003 §7.1).

7.3. An operation a caller lacks permission for **MUST** fail closed: no
partial effect, no information beyond the error itself (consistent with
NPS-018 §5's "a profile MUST NOT be assumed to already hold a capability
it hasn't been granted").

## 8. Open Questions *(Informative)*

- Whether the public API is primarily C (for ABI stability), a defined
  IDL with generated bindings, or both is undecided and will be resolved
  in the ABI document (ABI-001 §"Calling Conventions") before signatures
  are authored.
- `Server Mode`'s administrative interface (NPS-009 §5.6) will be
  specified here once a systems-administration NPS exists.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-08-12 | Initial draft — API areas, layering, naming/versioning conventions, error model; closing Milestone 11 gap category 4 |

---
**End of Document**
