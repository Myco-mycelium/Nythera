---
title: GPU Feature Support
document_id: NPS-013
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
depends_on: [NTM-000, NPC-001, ADR-0010, NPS-001, NPS-007]
---

# NPS-013 — GPU Feature Support

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
defines HDR, Variable Refresh Rate (VRR), ray tracing, and upscaling
support in terms of the Vulkan foundation adopted in ADR-0010.

## 2. Scope

This specification covers display and rendering feature exposure to
applications. It does not cover the graphics API choice itself (ADR-0010)
or controller input (NPS-012).

## 3. Feature Exposure Model

3.1. GPU features **MUST** be exposed to applications as queryable Vulkan
capabilities/extensions, consistent with ADR-0010 — Nythera **MUST NOT**
introduce a parallel, Nythera-specific feature-query API that duplicates
what Vulkan already provides.

3.2. An application **MUST** be able to query supported features (HDR,
VRR, ray tracing, upscaling) at startup and **MUST** degrade gracefully
when a feature is unsupported by the underlying hardware, rather than
failing to launch.

## 4. High Dynamic Range (HDR)

4.1. Nythera **MUST** support HDR output on displays and GPUs that
support it, exposed through the corresponding Vulkan color-space
extensions.

4.2. The adaptive UI shell (NPS-009) **MUST** correctly composite HDR and
standard-dynamic-range content simultaneously (e.g. an HDR game windowed
alongside SDR applications in Desktop Mode), since NPS-009 §5.1 requires
multi-window desktop presentation.

## 5. Variable Refresh Rate (VRR)

5.1. Nythera **MUST** support VRR (e.g. FreeSync/G-Sync-class protocols)
end-to-end from application frame submission through display output, on
hardware that supports it.

5.2. VRR **SHOULD** be enabled by default in Gaming Mode and Handheld Mode
(NPS-009 §5.2, §5.5) where supported, and **MAY** be left off by default
in Desktop Mode to avoid unexpected behavior in general productivity use,
subject to user override (NPS-009 §4.2).

## 6. Ray Tracing

6.1. Ray tracing support **MUST** be exposed through standard Vulkan
ray-tracing extensions; Nythera **MUST NOT** require application-specific
integration work beyond what those extensions already demand.

6.2. Ray tracing availability **MUST** be one of the features covered by
the graceful-degradation requirement in §3.2 — hardware without ray
tracing support MUST still run the application, at reduced fidelity,
rather than being blocked from launch.

## 7. Upscaling (FSR, XeSS, and Similar)

7.1. Nythera **SHOULD** provide a vendor-neutral upscaling integration
point so applications can request upscaling without hard-coding a specific
vendor technology (FSR, XeSS, or others), consistent with NTM-000 §4
("Compatibility") and avoiding vendor lock-in per NTM-000 §5.

7.2. Where a specific upscaling technology is only available via a
vendor-provided library, that library **MUST** be treated as optional,
consistent with the same reasoning applied to Oodle in ADR-0007 — the
platform's default rendering path MUST NOT require a closed-source
upscaling dependency to function.

## 8. Interaction with Compatibility Runtimes

8.1. The Windows compatibility runtime's DirectX-to-Vulkan translation
(NPS-007 §4) **MUST** pass through HDR, VRR, ray tracing, and upscaling
requests made by translated titles wherever the underlying DirectX feature
has a corresponding Vulkan equivalent, rather than silently dropping them.

8.2. Where no direct equivalent exists, the translation layer **MUST**
document the gap as a known limitation (consistent with NPS-007 §9's
existing pattern) rather than silently degrading rendering without
informing the user.

## 9. Open Questions *(Informative)*

- The vendor-neutral upscaling integration point (§7.1) requires further
  design once specific upscaling SDKs are evaluated for Vulkan
  integration maturity.
- Exact HDR tone-mapping behavior when compositing mixed HDR/SDR windows
  (§4.2) is deferred to the adaptive UI shell's visual design work.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |

---
**End of Document**
