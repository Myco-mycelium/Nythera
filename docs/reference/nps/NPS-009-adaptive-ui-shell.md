---
title: Adaptive UI Shell
document_id: NPS-009
version: 1.1.1
status: Accepted
classification: Normative
subsystem: runtime
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, NPS-001, NPS-007, NPS-008]
---

# NPS-009 — Adaptive UI Shell

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
defines how Nythera selects and presents a device-appropriate interface,
implementing the "Performance Modes" concept from the original design
discussion and referenced in NPS-001 §5 (Stage 5, Service Bring-Up).

## 2. Scope

This specification covers device-mode detection, the required behaviors of
each mode, and mode-switching (e.g. a tablet with a keyboard attached). It
does not cover the visual design system itself (a future how-to/reference
pair once implementation begins) or per-application windowing details
already covered for Android apps in NPS-008 §6.

## 3. Device Modes

Nythera **MUST** support at least the following modes:

| Mode | Primary Input | Typical Hardware |
|------|----------------|-------------------|
| Desktop | Mouse and keyboard | PCs, laptops |
| Gaming | Controller, full-screen launcher | Gaming PCs, consoles |
| Phone | Touch | Phones |
| Tablet | Touch, optional pen | Tablets |
| Handheld | Controller, small touch screen | Handheld gaming devices |
| Server | None (headless) | Headless/remote-managed systems |

## 4. Mode Detection

4.1. The mode **MUST** be determined during boot (NPS-001 §5, Stage 5)
based on detected hardware characteristics: presence/absence of a
touchscreen, presence/absence of pointing devices, form-factor hints from
firmware/hardware identifiers, and, where available, an explicit user
override.

4.2. A user **MUST** be able to override the detected mode manually, per
NTM-000 §4 ("Transparency") — automatic detection is a convenience default,
not a locked-in behavior.

4.3. Mode detection **MUST NOT** block reaching a minimally usable session
(NPS-001 §6.1) if detection is inconclusive; the system **SHOULD** fall
back to Desktop Mode as the safest default when hardware signals are
ambiguous.

## 5. Mode Behaviors

5.1. **Desktop Mode** — multi-window desktop interface; Windows-compat
(NPS-007) and Android-compat (NPS-008 §6.1) applications both present as
resizable windows.

5.2. **Gaming Mode** — full-screen launcher-style interface; supports
suspend/resume of the foreground application; prioritizes controller
navigation over mouse/keyboard, per NPS-002 §6.2's real-time scheduling
class for frame pacing.

5.3. **Phone Mode** — touch-first, single-foreground-app-focused interface;
Android-compat applications run full-screen by default (NPS-008 §6.2);
battery-aware scheduling **SHOULD** deprioritize background containers not
currently visible.

5.4. **Tablet Mode** — touch-first with support for split-screen
multi-app layouts and pen input where hardware supports it.

5.5. **Handheld Mode** — controller-first with a compact, gaming-focused
interface, distinct from full Gaming Mode in that the physical display is
integrated and typically smaller; **SHOULD** share the suspend/resume
behavior of Gaming Mode (§5.2).

5.6. **Server Mode** — headless; no interactive shell is started; the
system **MUST** remain remotely manageable via a defined administrative
interface (to be specified in a future systems-administration NPS).

## 6. Mode Transitions

6.1. A device capable of physical reconfiguration (e.g. a tablet with a
detachable keyboard) **MUST** support transitioning between modes (e.g.
Tablet ↔ Desktop) without requiring a reboot.

6.2. A mode transition **MUST NOT** terminate running applications;
running containers (NPS-002 §4) **MUST** persist across a UI mode change,
since the mode is a presentation-layer concept, not a process-lifecycle
concept.

## 7. Consistency Across Modes

7.1. A given application **MUST** behave identically at the container and
capability level (ADR-0004, NPS-002 §7) regardless of which UI mode
presents it; the adaptive shell **MUST NOT** become a second, informal
permission system layered on top of the capability model.

7.2. Settings, themes, and user preferences **SHOULD** be shared across
modes on the same device, consistent with NTM-000's optional cloud sync of
settings across devices.

## 8. Open Questions *(Informative)*

- The exact hardware-signal heuristics for mode detection (§4.1) require
  testing across real device classes and are left unspecified pending that
  work.
- ~~Whether VR headset presentation... is a distinct mode or a Gaming Mode
  variant~~ — resolved by NPS-012 §5.1: VR is deferred to a future
  milestone (tentatively M9) entirely; this document's mode table (§3)
  intentionally excludes VR pending that scoping work, rather than
  guessing at a mode definition ahead of it.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |
| 1.1.0   | 2026-07-13 | Cross-reference NPS-012 §5.1's VR deferral decision instead of leaving it as an independent open question |

| 1.1.1 | 2026-07-12 | Architecture Group review completed (Milestone 9). Status: Draft → Accepted. |

---
**End of Document**
