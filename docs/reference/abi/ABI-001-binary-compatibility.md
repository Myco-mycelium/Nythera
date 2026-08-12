---
title: ABI — Binary Compatibility and Calling Conventions
document_id: ABI-001
version: 1.0.0
status: Draft
classification: Normative
subsystem: core-architecture
owners:
  - Nyrqis Architecture
created: 2026-08-12
updated: 2026-08-12
ai_assisted: true
review_cycle: Continuous
depends_on: [NTM-000, NPC-001, NPS-003, NPS-017]
---

# ABI-001 — Binary Compatibility and Calling Conventions

## 1. Status of This Document

This document is **normative** for the *rules* that govern Nyrqis's
binary-level contracts, and **normative** that the areas in §4 exist. It
is a `Draft`: concrete layouts — exact message header bytes, register
conventions, symbol mangling — are deferred to implementation work, which
is the honest place for them (NPC-002 §5.2: no unverifiable detail
published as settled). The IPC wire format explicitly deferred by NPS-003
§9 ("Exact wire format / message header layout is deferred to an ABI
document") is specified in scope here (§4.2). Closing Milestone 11 gap
category 5 (ABI specification).

## 2. Purpose *(Informative)*

Nyrqis promises applications built against NySDK run unmodified across
every conformant backend (NPS-017 §7.1), and promises binary stability
within an ABI MAJOR version (NPC-001 §8.1). Neither promise can be kept
without an explicit ABI: a compiled application and the platform must
agree on how functions are called, how messages are laid out, how symbols
are versioned, and how plugins, drivers, and runtime components connect.

## 3. Compatibility Guarantees

3.1. Once this document (or a per-area section of it) reaches `Accepted`
status, its MAJOR version **MUST NOT** change without a deprecation period
of no less than one platform release cycle (NPC-001 §8.1).

3.2. A breaking ABI change **MUST** carry a MAJOR version increment and a
migration guide under `docs/how-to/` (NPC-001 §7).

3.3. ABI and API MAJOR versions **MUST** advance together at
platform-release granularity (API-001 §6.2).

3.4. The compatibility layers (Windows, Android) **MAY** evolve their
translation ABIs independently, provided native guarantees are unaffected
(NPC-001 §8.3).

## 4. ABI Areas

### 4.1 Calling Conventions
The native Nyrqis calling convention — register usage, stack layout,
argument passing, return values — **MUST** be defined before any native
code is shipped, and **MUST** be identical across all backends for the
same architecture (an application's own code cannot depend on which
backend runs beneath it, NPS-017 §7.1). For compatibility-runtime
translated code (NPS-007, NPS-008), the *source* ABI (Windows x64, ARM
AAPCS64, Android) is the translator's input contract; the *native*
calling convention is the output contract.

### 4.2 IPC Wire Format
Implements NPS-003 §9's deferral. The IPC message layout **MUST** cover,
at minimum:

- A **message header** identifying the primitive (`send`/`receive`/
  `call`/`notify`, NPS-003 §3), the target endpoint, and a bounded size
  (NPS-003 §3.1).
- A **dedicated capability-transfer field**, distinct from ordinary
  payload bytes, so transfers are explicit and auditable (NPS-003 §5.1).
- Defined encodings for the failure semantics of NPS-003 §7 (target
  terminated, endpoint revoked, capability missing).

The layout **MUST** be versioned (§5) and **MUST** be designed so the
shared-memory zeroing requirement (NPS-003 §3.1) is enforceable at the
primitive boundary.

### 4.3 Symbol Versioning
Public native symbols **MUST** carry a version (e.g. symbol-versioning
nodes) so that a binary linked against an older MAJOR continues to run
against a newer one within the same MAJOR, per §3.1. Unversioned exports
**MUST NOT** be added to a released ABI.

### 4.4 Plugin ABI
A plugin is an ordinary container with an explicit capability set
(API-001 §4.9); its *binary* contract is: a declared entry point, the
public API's ABI surface, and versioned symbol resolution against the
host. Plugin binaries **MUST** be treated as untrusted input at load time
(the plugin's own container is the isolation boundary, ADR-0004).

### 4.5 Driver ABI
Driver ABI: the interface between kernel-space components (NPS-001 §3
exception list) and user-space drivers (NPS-001 §4). **MUST** be versioned
separately from the application ABI, since drivers ship on different
cadence; the IPC fast paths it defines are the same primitive layer as
§4.2.

### 4.6 Runtime ABI
The binary contract of NyRuntime services (NPS-017 §3.1) exposed to
NySDK-compiled applications. It is the compiled counterpart of API-001
§4.3 and **MUST** be identical across backends (NPS-017 §7.1).

### 4.7 Backend ABI
The interface a NyHAL backend (NPS-017 §6) presents to NyCore. Unlike the
other areas, this ABI is *internal*: it is not exposed to applications,
and different backends **MAY** differ internally — only the NyCore
guarantees they deliver must be identical (NPS-017 §4, §5.2).

## 5. Versioning Rules

5.1. Every ABI area **MUST** carry its own MAJOR.MINOR.PATCH version,
advanced per NPC-001 §7 (MAJOR = breaking change to the contract,
MINOR = backward-compatible addition, PATCH = clarification).

5.2. ABI-001 as a whole and the application-facing areas (§4.1–§4.4,
§4.6) version in lockstep with the platform release cycle (§3.3); the
driver and backend ABIs (§4.5, §4.7) version on their own cadence.

5.3. Object serialization (NPS-025 §5) **MUST** follow ABI versioning
rules; the `Object IDs` decision deferred by NPS-025 §6 is settled here:
object IDs **SHOULD** be globally unique within a device (registry
prefix + per-registry sequence), since audit records may outlive the
registry that issued them.

## 6. Backend-Agnostic Enforcement

6.1. A binary compiled against NySDK on one conformant backend **MUST**
run unmodified on any other conformant backend of the same architecture
(NPS-017 §7.1) — this is the acceptance test for §4.1 and §4.6, and the
repository **MUST NOT** claim ABI conformance without a passing
cross-backend conformance test (NPC-003 §5.3, NPC-009 §7.4).

## 7. Open Questions *(Informative)*

- Whether the native calling convention follows an existing ABI (e.g. an
  established open convention) or is defined fresh is undecided; the
  answer shapes API-001 §8's language-binding decision.
- The driver ABI's exact boundary with the kernel fast paths (NPS-001 §3)
  will be decided during NyKernel Backend implementation.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-08-12 | Initial draft — compatibility rules and ABI areas, incl. the NPS-003 §9 IPC wire-format deferral; closing Milestone 11 gap category 5 |

---
**End of Document**
