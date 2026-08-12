# Nythera Proposals for Specification (NPS)

An NPS is a technical specification document — see NPC-001 §4 and §6 for
the document class definition and change process. Most NPS records live in
this directory; documents that belong to a topical reference area live in
that area's directory instead (noted below), which does not change their
status as NPS documents.

| ID | Title | Subsystem | Status |
|----|-------|-----------|--------|
| [NPS-001](NPS-001-kernel-architecture-and-boot.md) | Kernel Architecture and Boot (NyKernel Backend) | core-architecture | Accepted |
| [NPS-002](NPS-002-process-and-thread-model.md) | Process and Thread Model | core-architecture | Draft — benchmark-blocked (§9) |
| [NPS-003](NPS-003-ipc-and-capability-passing.md) | Inter-Process Communication and Capability Passing | core-architecture | Draft — benchmark-blocked (§6.1) |
| [NPS-004](NPS-004-nyfs-filesystem-core.md) | NyFS Filesystem Core | storage | Accepted |
| [NPS-005](NPS-005-transparent-compression-policy.md) | Transparent Compression Policy | storage | Draft — blocked on ADR-0007 |
| [NPS-006](NPS-006-game-image-format.md) | Nythera Game/Application Image Format (.nygi) and Overlay | storage | Accepted |
| [NPS-007](NPS-007-windows-compatibility-runtime.md) | Windows Compatibility Runtime | runtime | Accepted |
| [NPS-008](NPS-008-android-compatibility-runtime.md) | Android Compatibility Runtime | runtime | Accepted |
| [NPS-009](NPS-009-adaptive-ui-shell.md) | Adaptive UI Shell | runtime | Accepted |
| [NPS-010](NPS-010-container-runtime.md) | Container Runtime | security | Draft — blocked on ADR-0009 (§7.1) |
| [NPS-011](../capability-registry/NPS-011-capability-registry.md) | Capability Registry | security | Accepted (lives in `reference/capability-registry/`) |
| [NPS-012](NPS-012-controller-and-input-subsystem.md) | Controller and Input Subsystem | gaming | Accepted |
| [NPS-013](NPS-013-gpu-feature-support.md) | GPU Feature Support | gaming | Accepted |
| [NPS-014](NPS-014-emulator-hub.md) | Emulator Hub | gaming | Accepted |
| [NPS-015](NPS-015-local-ai-assistant.md) | Local AI Assistant | ai | Accepted |
| [NPS-016](NPS-016-optional-cloud-synchronization.md) | Optional Cloud Synchronization | ai | Accepted |
| [NPS-017](NPS-017-nyhal-kernel-abstraction.md) | NyHAL — Kernel Abstraction Layer and Backend Contract | core-architecture | Accepted |
| [NPS-018](../security/NPS-018-threat-model-methodology.md) | Threat Model Methodology and Trust Boundaries | security | Draft (Threat Model Phase 1a, lives in `reference/security/`) |
| [NPS-019](../security/NPS-019-attack-surface-enumeration.md) | Attack Surface Enumeration | security | Draft (Threat Model Phase 1b) |
| [NPS-020](../security/NPS-020-stride-analysis.md) | STRIDE Analysis per Trust Boundary | security | Draft (Threat Model Phase 2) |
| [NPS-021](../security/NPS-021-privilege-and-escalation-analysis.md) | Privilege Boundaries and Capability Escalation Analysis | security | Draft (Threat Model Phase 3) |
| [NPS-022](../security/NPS-022-container-escape-analysis.md) | Container Escape Analysis and Runtime Isolation | security | Draft (Threat Model Phase 4) |
| [NPS-023](../security/NPS-023-secure-boot-threat-model.md) | Secure Boot Threat Model | security | Draft (Threat Model Phase 5) |
| [NPS-024](../security/NPS-024-ai-threat-model.md) | AI Threat Model | security | Draft (Threat Model Phase 6) |
| [NPS-025](../object-registry/NPS-025-object-registry.md) | Object Registry | core-architecture | Draft (lives in `reference/object-registry/`) |
| [NPS-026](../package-format/NPS-026-package-format.md) | Nythera Package Format (.nypkg) | storage | Draft (lives in `reference/package-format/`) |

## Status Conventions

- **Accepted** — binding; implementation MUST conform (NPC-001 §5).
- **Draft** — not binding. Documents held at `Draft` are held for a
  *named, specific reason* (a pending benchmark or an upstream dependency
  that is itself benchmark-blocked), not for incompleteness — see each
  document's Status / Open Questions section.
- **Rejected** — considered and formally declined (see ADR-0017 for the
  only instance of an ID-level rejection so far; it applies to ADRs, not
  NPS, and is recorded here for completeness of the status vocabulary).

The authoritative status table for all documents is
[`NPC-004 Specification Index`](../../00-platform/004-SPECIFICATION_INDEX.md).
