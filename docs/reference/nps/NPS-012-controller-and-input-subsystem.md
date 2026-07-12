---
title: Controller and Input Subsystem
document_id: NPS-012
version: 1.0.0
status: Draft
classification: Normative
subsystem: gaming
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, NPS-002, NPS-009, NPS-011]
---

# NPS-012 — Controller and Input Subsystem

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
defines native controller and input-device support, implementing the
gaming-input goals from the original design discussion and consuming the
`CAP-INPUT` capability defined in NPS-011 §3.

## 2. Scope

This specification covers native controller device support, the Steam
Input compatibility path, and VR headset input. It does not cover
keyboard/mouse/touch input already assumed by NPS-009's per-mode input
model, or rendering (see NPS-013 for GPU/display features).

## 3. Native Controller Support

3.1. Nythera **MUST** natively recognize, without requiring third-party
drivers, at minimum: Xbox controllers, PlayStation controllers, and
Nintendo Switch-style controllers, connected via USB or Bluetooth
(`CAP-USB` / `CAP-BLUETOOTH`, per NPS-011 §3).

3.2. Controller input **MUST** be delivered to the foreground container
(per NPS-009's mode model) through the `CAP-INPUT` capability, using the
same IPC path (NPS-003) as keyboard/mouse/touch input — controllers
**MUST NOT** require a separate capability class or delivery mechanism.

3.3. Controller identity (which physical device, vendor/product ID,
capabilities such as gyroscope or adaptive triggers) **MUST** be exposed to
the receiving application in a normalized form, so applications do not need
per-vendor detection logic for common features.

## 4. Steam Input Compatibility

4.1. Nythera **MUST** support Steam Input as a compatibility path for
titles that expect it, consistent with the Windows compatibility runtime's
goal of running unmodified titles (NPS-007).

4.2. Steam Input support **MUST** run within the same container as the
application using it (NPS-002 §4.2), not as a separate elevated service —
consistent with NPC-001 §9.2's "no implicit elevated privileges" rule.

## 5. VR Headset Input

5.1. VR headset tracking and controller input **MAY** be supported as an
optional capability distinct from `CAP-INPUT`, given its higher data rate
and different latency requirements; the exact capability definition is
deferred to a future NPS-011 registry addition once a concrete VR
integration is scoped (see NPS-009 §8's open question on whether VR is a
distinct UI mode).

5.2. VR input, when supported, **MUST** go through the same capability
evaluation path as any other input source (NPS-010 §4.2) — it MUST NOT
bypass container capability checks for latency reasons; latency
requirements are addressed through IPC and scheduling priority (NPS-002
§6.2), not through weakened isolation.

## 6. Real-Time Scheduling Interaction

6.1. Input event delivery **MUST** be eligible for the real-time-priority
scheduling class defined in NPS-002 §6.2, since input latency directly
affects the gaming experience goals in NTM-000.

6.2. Input delivery **MUST NOT** be subject to the IPC token-bucket rate
limiting in ADR-0009 at a level that would introduce perceptible input
lag under normal use; input-capability containers **SHOULD** receive
bucket parameters appropriate to their expected event rate, evaluated per
NPS-010 §7.1.

## 7. Open Questions *(Informative)*

- Exact normalized controller capability schema (§3.3) — e.g. how
  adaptive triggers or haptic feedback are represented — is deferred to
  implementation-phase work.
- VR capability definition (§5.1) is deferred pending a concrete VR
  integration scope.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |

---
**End of Document**
