---
title: Adopt an AOSP-Based Container Runtime for Android Compatibility
document_id: ADR-0008
version: 1.0.1
status: Accepted
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0004, ADR-0006]
---

# ADR-0008 — AOSP-Based Container Runtime for Android Compatibility

## Context
Nythera commits to running `.apk` applications (NTM-000, original design
discussion). As with Windows compatibility (ADR-0005), the choice is
between full emulation of an Android device and a lighter-weight
compatibility layer built from Android's own open-source components.

## Decision (Proposed)
Adopt a container-based Android runtime built on AOSP (Android Open Source
Project) libraries — the same general approach as existing projects like
Waydroid/Anbox — running inside the container model defined in ADR-0004,
rather than a full Android device emulator (e.g. QEMU-based Android
emulation). Google Play Services is treated as an **optional, user-supplied
component**, not a bundled dependency, consistent with keeping the default
platform free of closed-source requirements (NTM-000 §5, "What Nythera Will
Never Become" — vendor lock-in).

On x86 hardware, ARM-compiled APKs require instruction translation, the
same category of risk flagged for Windows/ARM in ADR-0005; this ADR treats
it as a shared, cross-cutting translation problem rather than solving it
twice.

## Alternatives Considered
- **Full Android emulator (QEMU-based)** — rejected as the default;
  significant performance overhead conflicts with NTM-000 "Performance,"
  though it MAY remain available as an optional fallback for unsupported
  APKs, mirroring the Windows VM fallback position in ADR-0005.
- **Bundle Google Play Services by default** — rejected; makes a core
  compatibility path depend on a closed, licensed component the user did
  not choose, conflicting with NTM-000 §5 and §7 ("Community").
- **Write a from-scratch Android API-compatible runtime** — rejected as
  unjustified complexity (NTM-000 §4, "Simplicity") given AOSP components
  already solve most of this problem under a compatible open-source
  license.

## Consequences
- Apps depending on Google Play Services (push notifications, Play
  Billing, Maps) MAY fail to run correctly unless the user supplies
  compatible components; this MUST be documented as a known limitation
  (mirroring NPS-006 §8's approach to anti-cheat), not silently omitted.
- The Android runtime container MUST follow the same capability-scoped
  model as every other application class, per ADR-0004 and NPS-002 §7.2 —
  no implicit elevated Android-side permissions.
- x86/ARM instruction translation for Android APKs SHOULD share
  engineering effort with the equivalent Windows/ARM translation work
  raised in ADR-0005, rather than maintaining two independent translators.

## Status
Accepted — 2026-07-12, following Architecture Group review (Milestone 9). The pending runtime specification work (NPS-008 Android Compatibility Runtime) is complete. Shared ARM instruction-translation risk with ADR-0005 remains open, tracked in REPOSITORY_STATE.md.
