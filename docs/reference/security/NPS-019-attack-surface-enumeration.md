---
title: Attack Surface Enumeration
document_id: NPS-019
version: 1.0.0
status: Draft
classification: Normative
subsystem: security
owners:
  - Nythera Architecture
created: 2026-07-13
updated: 2026-07-13
ai_assisted: true
review_cycle: Continuous
depends_on: [NTM-000, NPC-001, NPS-018]
---

# NPS-019 — Attack Surface Enumeration

## 1. Status of This Document

This document is **normative** as an inventory: every entry **MUST**
correspond to a real interface described in an existing specification,
not a hypothetical one. It is **Phase 1** of the threat model (alongside
NPS-018), cataloguing every point where untrusted or lower-trust input
reaches the system, without yet performing the STRIDE analysis on each —
that is Phase 2 (tracked in `007-PROJECT_ROADMAP.md` Milestone 12).

## 2. Scope

Every row below is a concrete interface, not a category — "network" is
too vague; "a container holding `CAP-NETWORK` making an outbound
connection" is a surface. This document **MUST** be updated whenever a
new NPS document introduces an interface crossing a trust boundary from
NPS-018 §4.

## 3. Surface Catalog

| ID | Surface | Trust Boundary | Untrusted Input | Governing Spec | STRIDE Analysis |
|----|---------|-----------------|-------------------|-------------------|-------------------|
| `SURFACE-IPC-0001` | `send`/`call` message payloads between containers | `TB-CONTAINER` | Message content and capability-transfer fields from any container holding a valid endpoint capability | NPS-003 §3, §5 | Pending Phase 2 |
| `SURFACE-IPC-0002` | IPC endpoint creation/lookup | `TB-CONTAINER` | Endpoint capability requests | NPS-003 §4 | Pending Phase 2 |
| `SURFACE-CAP-0001` | Container manifest submission (capability request) | `TB-CAPABILITY` | Requested capability set at container creation | NPS-010 §4.2 | Pending Phase 2 |
| `SURFACE-CAP-0002` | Capability grant/revocation audit interface | `TB-CAPABILITY` | User-facing but locally trusted; relevant if the audit view itself can be spoofed | NPS-010 §8 | Pending Phase 2 |
| `SURFACE-FS-0001` | `.nygi` image manifest and content parsing at mount time | `TB-PACKAGE` | Image file supplied by a package publisher or a user-provided file | NPS-006 §3, §6 | Pending Phase 2 |
| `SURFACE-FS-0002` | NyFS checksum/compression metadata parsing | `TB-BACKEND` | On-disk metadata, potentially corrupted or crafted | NPS-004 §4.3, §4.5 | Pending Phase 2 |
| `SURFACE-WIN-0001` | Windows PE (`.exe`/`.msi`) binary parsing and API translation | `TB-COMPAT-RUNTIME` | Arbitrary Windows executable content | NPS-007 §4 | Pending Phase 2 |
| `SURFACE-WIN-0002` | Virtualized registry read/write | `TB-COMPAT-RUNTIME` | Registry operations from translated Windows code | NPS-007 §5 | Pending Phase 2 |
| `SURFACE-AND-0001` | APK parsing and signature verification | `TB-PACKAGE`, `TB-COMPAT-RUNTIME` | Arbitrary APK content | NPS-008 §4 | Pending Phase 2 |
| `SURFACE-AND-0002` | Android permission-to-capability mapping evaluation | `TB-CAPABILITY` | APK manifest-declared permissions | NPS-008 §5, NPS-011 §6 | Pending Phase 2 |
| `SURFACE-NET-0001` | Outbound network connections from a `CAP-NETWORK` container | `TB-NETWORK` | Remote endpoint responses | NPS-011 §3 | Pending Phase 2 |
| `SURFACE-NET-0002` | Inbound connections to a `CAP-NETWORK-LISTEN` container | `TB-NETWORK` | Arbitrary remote connection attempts | NPS-011 §3 | Pending Phase 2 |
| `SURFACE-SYNC-0001` | Cloud sync data transmission and retrieval | `TB-NETWORK` | Sync backend responses; conflicting data from other devices | NPS-016 §4, §6 | Pending Phase 2 |
| `SURFACE-USB-0001` | Attached USB device enumeration and access | `TB-CAPABILITY` | Device descriptors and data from physically attached hardware | NPS-011 §3 (`CAP-USB`) | Pending Phase 2 |
| `SURFACE-BT-0001` | Bluetooth device pairing and data exchange | `TB-CAPABILITY` | Nearby Bluetooth devices | NPS-011 §3 (`CAP-BLUETOOTH`) | Pending Phase 2 |
| `SURFACE-NFC-0001` | NFC tag/device reads | `TB-CAPABILITY` | Physically-tapped NFC content | NPS-011 §3 (`CAP-NEAR-FIELD`) | Pending Phase 2 |
| `SURFACE-INPUT-0001` | Controller/input device event delivery | `TB-CAPABILITY` | Raw input events, including from third-party or spoofed HID devices | NPS-012 §3 | Pending Phase 2 |
| `SURFACE-GPU-0001` | Vulkan command buffer submission via the kernel fast path | `TB-KERNEL` | Command buffer content from any container with rendering access | NPS-001 §3, ADR-0010 | Pending Phase 2 |
| `SURFACE-EMU-0001` | User-supplied ROM/BIOS file parsing by emulator cores | `TB-EMULATOR` | Arbitrary, potentially malformed ROM/BIOS files | NPS-014 §5–§6 | Pending Phase 2 |
| `SURFACE-AI-0001` | Voice control input parsing | `TB-AI` | Audio input, potentially adversarial (e.g. crafted to trigger unintended commands) | NPS-015 §3, §6 | Pending Phase 2 |
| `SURFACE-AI-0002` | AI-suggested-action presentation and user confirmation flow | `TB-AI` | The suggestion content itself, and whether the confirmation UI can be spoofed or the boundary between "suggest" and "act" bypassed | NPS-015 §5 | Pending Phase 2 |
| `SURFACE-BOOT-0001` | Boot loader signature verification chain | `TB-BOOT` | Boot images, both legitimate updates and potentially tampered ones | NPS-001 §5 (Stage 2), ADR-0014 | Pending Phase 2 |
| `SURFACE-BOOT-0002` | Secure Boot key enrollment (user-enrolled MOK-equivalent) | `TB-BOOT` | User action, but relevant if the enrollment flow itself can be tricked | ADR-0014 | Pending Phase 2 |
| `SURFACE-BACKEND-0001` | NyHAL backend's native enforcement of a NyCore guarantee (e.g. Linux seccomp/LSM policy standing in for Nythera's capability model) | `TB-BACKEND` | Any gap between what NyCore assumes is enforced and what the backend's native mechanism actually enforces | NPS-017 §4, §5 | Pending Phase 2 |

## 4. Coverage Note

24 surfaces catalogued across all 10 trust boundaries defined in NPS-018
§4. This is a snapshot as of this version, not a claim of exhaustiveness
— new surfaces **MUST** be added as new specifications introduce new
interfaces (§2), following the same discipline established for the
capability registry (NPS-011 §6: intentionally incomplete, expanded
incrementally).

## 5. What "Pending Phase 2" Means *(Informative)*

Every row is currently unanalyzed for STRIDE categories — cataloguing the
surface (this document) and analyzing it (Phase 2) are deliberately
separate steps, so that Phase 1 can be reviewed for completeness of the
*inventory* before Phase 2 spends effort analyzing entries that might
still be missing or miscategorized.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-13 | Initial draft — Phase 1 of the threat model (attack surface catalog) |

---
**End of Document**
