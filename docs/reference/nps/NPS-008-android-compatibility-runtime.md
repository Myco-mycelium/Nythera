---
title: Android Compatibility Runtime
document_id: NPS-008
version: 1.0.1
status: Accepted
classification: Normative
subsystem: runtime
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, ADR-0004, ADR-0006, ADR-0008, NPS-002, NPS-003]
---

# NPS-008 — Android Compatibility Runtime

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
implements ADR-0008 (AOSP-based container runtime), defining how `.apk`
applications are installed, executed, and windowed within Nythera.

## 2. Scope

This specification covers APK installation and process placement, the
Android permission-to-capability mapping, and windowed vs. full-screen
presentation. It does not cover ARM/x86 instruction translation mechanics
in detail (§7, deferred, shared concern with NPS-007 §7) or Google Play
Services replacement components, which remain explicitly optional per
ADR-0008.

## 3. Process and Container Placement

3.1. Each installed Android application **MUST** run inside its own
container, per NPS-002 §4 and ADR-0004 — mirroring Android's own
per-application process model, which maps naturally onto Nythera's
container boundary.

3.2. The AOSP runtime components (Android system services required for app
execution) **MUST** themselves run inside a dedicated, minimally-privileged
container, distinct from any individual application's container, so a
faulting system service does not take down running apps unnecessarily
(consistent with the crash-isolation goals of NPS-002 §8).

## 4. Installation

4.1. Installing an `.apk` **MUST** produce a `.nygi` image (NPS-006),
identically to any other application or game, so Android apps benefit from
the same verification, overlay, and uninstall guarantees as native and
Windows-compat software.

4.2. APK signature verification **MUST** be performed at install time in
addition to, not instead of, the `.nygi` manifest checksum verification
defined in NPS-006 §6.

## 5. Permission-to-Capability Mapping

5.1. Android's permission model (camera, microphone, storage, location,
etc.) **MUST** be mapped onto Nythera's capability model (NPC-001 §9.3);
an Android app's manifest-declared permissions **MUST** translate directly
into the capability set requested for its container.

5.2. An Android permission with no corresponding Nythera capability
**MUST NOT** be silently granted; it MUST either map to a defined
capability or be denied, and this mapping gap MUST be tracked in the
capability registry (Milestone M6) as new capability classes are defined.

5.3. Runtime permission prompts (Android's request-at-use-time model)
**SHOULD** be presented through Nythera's native permission UI rather than
an emulated Android permission dialog, so the user experience is
consistent across application classes, per NTM-000 §4 ("Transparency").

## 6. Windowing and Presentation

6.1. On devices in Desktop Mode (NTM-000 "Performance Modes"), Android
apps **MUST** be presentable as ordinary resizable windows alongside
native and Windows-compat applications.

6.2. On devices in Phone Mode, Android apps **MUST** run full-screen by
default, consistent with the adaptive UI shell's per-device-type behavior
(see NPS-009).

6.3. An application's declared orientation/resizability constraints (from
its Android manifest) **SHOULD** be respected where they don't conflict
with the current device mode.

## 7. ARM/x86 Instruction Translation *(Informative — deferred)*

Running ARM-compiled APKs on x86 hardware (and the inverse, less common
case) requires instruction translation. Per ADR-0008, this work SHOULD be
shared with the equivalent Windows/ARM translation effort raised in
NPS-007 §7 rather than duplicated; neither document claims this problem is
solved yet.

## 8. Google Play Services *(Informative)*

Per ADR-0008, Google Play Services is not bundled by default. Nythera MAY
support user-supplied Play Services components or open-source
alternatives (e.g. microG-style replacements) as an optional installation,
but this runtime MUST function for permission-compliant, Play-Services-free
APKs without it.

## 9. Known Limitations *(Informative)*

Apps that hard-depend on Google Play Services (push notifications, Play
Billing, Maps SDK, SafetyNet/Play Integrity checks) MAY fail to run
correctly without user-supplied replacement components. This MUST be
surfaced in user-facing compatibility information rather than presented as
a generic app crash.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |

| 1.0.1 | 2026-07-12 | Architecture Group review completed (Milestone 9). Status: Draft → Accepted. |

---
**End of Document**
