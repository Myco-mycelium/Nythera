---
title: Windows Compatibility Runtime
document_id: NPS-007
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
depends_on: [NTM-000, NPC-001, ADR-0004, ADR-0005, ADR-0006, NPS-002, NPS-003]
---

# NPS-007 — Windows Compatibility Runtime

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
implements ADR-0005 (translation layer, not full emulation), defining the
subsystems the Windows compatibility runtime **MUST** provide and how it
sits inside the container and process model already defined.

## 2. Scope

This specification covers the required translation subsystems, process
placement, and the registry-virtualization model. It does not cover
ARM instruction translation in detail (§7, deferred), the file-mounting
model for installed Windows games (see NPS-006, which already covers this
independent of application class), or anti-cheat compatibility (out of
scope per NPS-006 §8).

## 3. Process and Container Placement

3.1. A launched `.exe`/`.msi` application **MUST** run as one or more
processes inside a single container, per NPS-002 §4 — the same container
model native applications use, per ADR-0004.

3.2. The Windows compatibility runtime **MUST NOT** grant its containers
any capability class not defined in the shared capability registry
(Milestone M6); "being a Windows application" **MUST NOT** itself be a
capability.

3.3. Helper processes the runtime spawns on the application's behalf
(translation shims, service processes) **MUST** remain inside the same
container as the application they serve, per NPS-002 §4.2.

## 4. Required Translation Subsystems

The runtime **MUST** provide the following, at minimum, before it can be
considered feature-complete for Milestone M5:

| Subsystem | Requirement |
|-----------|-------------|
| Win32 / Win64 API | Core process, threading, filesystem, and windowing API surface translated to native equivalents. |
| Graphics | DirectX-to-Vulkan command translation, sufficient to run unmodified DirectX 11/12 titles at acceptable performance (exact target deferred to §8). |
| .NET Compatibility | A .NET runtime compatible with common .NET Framework and .NET (Core) application binaries. |
| Input | DirectInput and XInput translated to the native input subsystem, preserving controller support goals from NTM-000. |
| Registry | A virtualized registry (§5) — Nythera MUST NOT expose or depend on a real Windows registry. |

## 5. Registry Virtualization

5.1. Each container **MUST** receive its own isolated virtual registry
namespace; no container **MAY** read or write another container's registry
data, consistent with the capability-scoped isolation model (ADR-0004).

5.2. The virtual registry **MUST** be backed by NyFS (NPS-004) as ordinary
structured data, not a separate storage mechanism, so it benefits from the
same copy-on-write, checksum, and backup guarantees as the rest of the
overlay (NPS-006 §4.4).

5.3. Registry writes **MUST** be captured in the same writable overlay as
other application writes (NPS-006 §4.2) so a game/application update or
uninstall does not silently discard registry state a user depends on.

## 6. Interaction with Game Images

6.1. Windows applications installed as `.nygi` images (NPS-006) **MUST**
see their base image as read-only and their own writes (including registry
writes, per §5.3) redirected into the overlay, identically to native
applications — the compatibility runtime **MUST NOT** require a different
storage model.

## 7. ARM Instruction Translation *(Informative — deferred)*

Running x86/x64 Windows applications on ARM hardware (phones, some
handhelds) requires CPU instruction translation. This is flagged in
ADR-0005 as the highest-risk element of Windows compatibility and is
deferred to a dedicated follow-up specification once a concrete ARM target
device class is chosen; this document does not claim ARM support is solved.

## 8. Performance and Compatibility Targets

8.1. Specific performance targets (e.g. "within N% of native Windows
performance for title X") **MUST NOT** be published in this document until
backed by reproducible benchmarks under `tests/`, per NPC-002 §5.2.

8.2. A compatibility database (which titles run, with what caveats)
**SHOULD** be maintained as a separate, continuously-updated reference
artifact once the runtime reaches an implementable state, rather than as
static claims in this specification.

## 9. Known Limitations *(Informative)*

Per ADR-0005 and NPS-006 §8: kernel-driver-dependent anti-cheat is not
supported without vendor cooperation. This limitation applies equally here
and MUST be surfaced to users attempting to run affected titles rather than
failing silently or with a generic error.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |

| 1.0.1 | 2026-07-12 | Architecture Group review completed (Milestone 9). Status: Draft → Accepted. |

---
**End of Document**
