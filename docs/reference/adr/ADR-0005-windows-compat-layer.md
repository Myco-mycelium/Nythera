---
title: Windows Compatibility via Translation Layer, Not Full Emulation
document_id: ADR-0005
version: 1.0.0
status: Proposed
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0004]
---

# ADR-0005 — Windows Compatibility via Translation Layer

## Context
Nythera aims to run `.exe`/`.msi` applications without requiring a full
Windows kernel or VM. Full emulation is costly in performance and
maintenance; API-level translation (in the spirit of Wine/Proton) is a
proven, lighter-weight approach.

## Decision (Proposed)
Implement Windows compatibility as a translation subsystem covering Win32,
Win64, DirectX-to-Vulkan translation, .NET compatibility, DirectInput/XInput,
and a virtualized registry — rather than emulating a full Windows
environment. On non-x86 hardware (e.g. ARM handhelds/phones), this
additionally requires CPU instruction translation, which is called out as
the highest-risk element of this decision.

## Alternatives Considered
- **Full Windows VM/emulation** — rejected as the default path; excessive
  resource overhead conflicts with NTM-000 "Performance," though it MAY
  remain available as an optional fallback mode for unsupported titles.
- **No Windows compatibility** — rejected; contradicts the project's
  original compatibility goals and the "Compatibility" principle in
  NTM-000 §4.

## Consequences
- Games/applications relying on kernel-level anti-cheat MAY be unsupported
  absent vendor cooperation; this MUST be documented as a known limitation,
  not silently omitted.
- ARM instruction translation is a distinct, high-risk workstream and
  SHOULD be tracked as its own NPS rather than bundled into the general
  Windows compatibility specification.

## Status
Proposed — pending Milestone M5 runtime specification work.
