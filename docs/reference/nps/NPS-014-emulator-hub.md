---
title: Emulator Hub
document_id: NPS-014
version: 1.0.1
status: Accepted
classification: Normative
subsystem: gaming
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, NPS-006, NPS-010, NPS-012, NPS-013]
---

# NPS-014 — Emulator Hub

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
defines the optional Emulator Hub feature from the original design
discussion: organizing user-supplied ROM and BIOS files for classic
systems, without the platform itself distributing copyrighted content.

## 2. Scope

This specification covers ROM/BIOS organization, the legal responsibility
model, and integration with the container and image systems already
defined. It does not cover any specific emulator's implementation, which
remains out of scope for platform-level specification.

## 3. Legal Responsibility Model

3.1. Nythera **MUST NOT** bundle, distribute, or facilitate acquisition of
copyrighted ROM or BIOS files. The Emulator Hub is an organizational and
execution feature only.

3.2. Users are solely responsible for supplying legally obtained game
files and BIOS firmware where required, consistent with the original
design discussion's explicit framing. This requirement **MUST** be
displayed to the user before the Emulator Hub's ROM-import function is
first used, and **MUST NOT** be buried in terms-of-service text alone, per
NTM-000 §4 ("Transparency").

3.3. The Emulator Hub **MUST NOT** include built-in search, download, or
acquisition functionality for ROM or BIOS files; it operates only on files
the user has already placed on their own storage.

## 4. Supported System Organization

4.1. The Emulator Hub **MUST** organize user-supplied ROM files by system
(e.g. NES, SNES, Game Boy, Nintendo DS, PlayStation, PlayStation 2, PSP,
Wii, GameCube, Sega Genesis, Dreamcast — per the original design
discussion's candidate list), using file signatures/headers to detect
system where possible rather than relying solely on file extension.

4.2. A ROM library organized by the Emulator Hub **SHOULD** be stored on
NyFS with the same checksum and transparent-compression guarantees as any
other content (NPS-004 §4.3, §4.5) — a user's ROM collection is their data,
per NPC-001 §10 (User Ownership), and MUST benefit from the same integrity
and backup properties as game saves (NPS-006 §4.4).

## 5. Execution Model

5.1. Each supported system's emulator **MUST** run inside its own
container, per NPS-002 §4 and ADR-0004 — the Emulator Hub does not get an
exception from the platform's containerization model.

5.2. An emulator container **MUST** request only the capabilities it
needs (e.g. `CAP-DISPLAY`, `CAP-INPUT`, `CAP-FILESYSTEM-NYFS` scoped to
the user's ROM directory) per NPS-011 and NPS-010 §4.2; it **MUST NOT**
receive broad filesystem access to satisfy convenience.

5.3. Controller input for emulated titles **MUST** flow through the same
Controller and Input Subsystem defined in NPS-012, including remapping
support, since classic-system control schemes frequently differ from
modern controller layouts.

5.4. GPU features from NPS-013 (upscaling in particular) **MAY** be
applied to emulated output where the emulator supports it, subject to the
same graceful-degradation requirement (NPS-013 §3.2).

## 6. BIOS Firmware Handling

6.1. Where a supported system requires BIOS firmware to function
accurately, the Emulator Hub **MUST** clearly indicate which systems have
this requirement and **MUST NOT** claim full functionality is available
without user-supplied firmware.

6.2. BIOS files, like ROMs, are subject to the same legal-responsibility
framing in §3.2 and **MUST NOT** be bundled by Nythera.

## 7. Relationship to `.nygi` Images

7.1. The Emulator Hub organizes loose, user-supplied files rather than
producing `.nygi` images (NPS-006) by default, since ROM libraries are
typically open collections a user actively manages, not fixed installs.

7.2. A user **MAY** optionally package a specific emulator configuration
(emulator + settings, excluding ROMs) as a `.nygi` image for consistency
with how other software is installed, but this is a convenience feature,
not a requirement.

## 8. Open Questions *(Informative)*

- Whether per-system emulator selection is fixed (one default emulator per
  system) or user-configurable (choice of emulator core) is deferred to
  implementation-phase design.
- Save-state handling (distinct from in-game saves) and its interaction
  with the overlay model (NPS-006 §4) is deferred pending emulator core
  selection.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |

| 1.0.1 | 2026-07-12 | Architecture Group review completed (Milestone 9). Status: Draft → Accepted. |

---
**End of Document**
