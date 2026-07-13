---
title: Adopt Vulkan as the Native Graphics API Foundation
document_id: ADR-0010
version: 1.0.1
status: Accepted
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0005, ADR-0006, NPS-001, NPS-007]
---

# ADR-0010 — Vulkan as the Native Graphics API Foundation

## Context
NPS-007 §4 already commits to DirectX-to-Vulkan translation for Windows
compatibility, and NPS-001 §3 already reserves a kernel-space GPU command
submission fast path. Neither document establishes what native Nythera
applications and games render through. Without a decision, native
graphics work has no target API, and the gaming features listed in the
original design discussion — HDR, VRR, ray tracing, upscaling (FSR/XeSS) —
have no common foundation to sit on.

## Decision (Proposed)
Adopt **Vulkan** as the native graphics API for Nythera. Native
applications and games target Vulkan directly; the Windows compatibility
runtime's DirectX-to-Vulkan translation (NPS-007 §4) becomes a specific
case of a single underlying graphics foundation rather than a parallel
graphics stack. The kernel-space GPU command submission fast path (NPS-001
§3) is specified in terms of Vulkan command buffer submission.

This choice is consistent with NTM-000 §4 ("Simplicity"): one native
graphics API, with translation layers built to target it, rather than
maintaining a Nythera-specific graphics API that Windows translation would
then need to target as a second step.

## Alternatives Considered
- **A custom native graphics API** — rejected; unjustified complexity per
  NTM-000 §4, and would require the Windows compatibility layer to
  translate DirectX into a bespoke target with no existing driver or
  tooling ecosystem, rather than the well-supported DirectX→Vulkan path
  already assumed in NPS-007.
- **OpenGL as the native API** — rejected; lacks the explicit,
  low-overhead command submission model Vulkan provides, which matters
  directly for the kernel-space fast path's latency goals (NPS-001 §3) and
  for ray tracing / advanced feature support.
- **Metal-style platform-specific APIs per device class** — rejected;
  fragments the graphics stack exactly where NTM-000 §4 ("Longevity") asks
  for a single foundation that works across desktop, handheld, and console
  hardware.

## Consequences
- A future GPU Feature Support specification (NPS-013) MUST define HDR,
  VRR, ray tracing, and upscaling (FSR/XeSS) support in terms of Vulkan
  extensions and driver requirements.
- Android compatibility (NPS-008), which already runs OpenGL ES / Vulkan
  content natively on most modern devices, is largely unaffected — Android
  apps using Vulkan already target compatible surfaces; those using OpenGL
  ES require the AOSP runtime's own translation, which is unchanged by
  this decision.
- Driver support breadth (older or unusual GPU hardware lacking modern
  Vulkan support) is an acknowledged compatibility risk, to be tracked
  alongside the other known limitations in NPS-007 §9 and NPS-006 §8
  rather than omitted.

## Status
Accepted — 2026-07-12, following Architecture Group review (Milestone 9).
