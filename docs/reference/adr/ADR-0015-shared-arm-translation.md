---
title: Shared Dynamic Binary Translation Approach for ARM/x86 Compatibility
document_id: ADR-0015
version: 1.0.0
status: Proposed
owners: [Nythera Architecture]
created: 2026-07-13
updated: 2026-07-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0005, ADR-0008, NPS-007, NPS-008]
---

# ADR-0015 — Shared Dynamic Binary Translation for ARM/x86

## Context
Both NPS-007 §7 (Windows compatibility on ARM) and NPS-008 §7 (Android
APKs on x86) independently flagged instruction translation as their
highest-risk, deferred item, and both explicitly pointed at each other to
avoid solving the same problem twice. Neither document picked an actual
translation strategy. This ADR makes that shared choice.

## Decision (Proposed)
Adopt **dynamic binary translation (DBT) with a JIT-compiled hot-path
cache**, in the general family of Rosetta 2 or FEX-Emu, as the shared
translation approach used by both NPS-007 (Windows/ARM) and NPS-008
(Android/x86), implemented as a single shared translation subsystem rather
than two independent ones.

- The subsystem **MUST** live below both compatibility runtimes as a
  shared component (architecturally, part of NyRuntime per ADR-0012's
  layering, consumed by both NPS-007 and NPS-008), not duplicated inside
  each.
- Translated code **MUST** execute inside the same container/capability
  model as any other process (NPS-002 §4, ADR-0004) — the translator
  itself gets no elevated access merely because instruction translation is
  a low-level operation.
- Ahead-of-time (AOT) caching of translated hot paths **SHOULD** be
  supported to reduce repeated JIT overhead across launches of the same
  application, consistent with NPS-006's game-image model (a translated
  cache **MAY** be stored as part of an application's writable overlay,
  NPS-006 §4).

## Alternatives Considered
- **Full CPU emulation (interpretation, no JIT)** — rejected as the
  default; correct but far too slow for the gaming and general-application
  performance goals in NTM-000 §4 ("Performance"), especially compared to
  a JIT-based approach's typical overhead profile.
- **Separate, independent translators for NPS-007 and NPS-008** —
  rejected; duplicates significant engineering effort solving the same
  core problem (x86↔ARM instruction translation) twice, conflicting with
  NTM-000 §4 ("Simplicity"), and risks the two translators drifting into
  inconsistent performance or correctness characteristics.
- **Require native ARM or x86 builds only, no translation** — rejected;
  would mean Windows and Android compatibility (ADR-0005, ADR-0008)
  simply don't work across the phone/handheld hardware class the original
  design discussion explicitly targeted, defeating the purpose of
  supporting those application formats at all.

## Consequences
- A new shared specification (future NPS, subsystem `runtime`) is needed
  to define the translation subsystem's interface once implementation
  begins; this ADR fixes the *approach*, not the interface contract.
- Performance claims about specific translated workloads MUST NOT be
  published until backed by benchmarks (NPC-002 §5.2) — this remains
  unresolved and is explicitly not closed by this ADR, only the
  architectural approach is.
- Because the translator sits below both NPS-007 and NPS-008, a defect in
  it is a cross-cutting risk affecting both runtimes simultaneously; this
  MUST be accounted for in whatever test suite eventually covers it
  (NPC-003 §5.3).

## Status
Proposed — approach decided; performance validation remains open pending
benchmark data, per NPC-002 §5.2.
