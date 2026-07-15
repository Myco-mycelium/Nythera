---
title: Capability Registry
document_id: NPS-011
version: 1.3.0
status: Accepted
classification: Normative
subsystem: security
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
review_cycle: Continuous
depends_on: [NTM-000, NPC-001, NPS-002, NPS-008, NPS-010]
---

# NPS-011 — Capability Registry

## 1. Status of This Document

This document is **normative** and is the canonical capability registry
required by NPC-001 §9.3: *"Any subsystem requesting a new capability
class MUST document it in `docs/reference/capability-registry/` before it
may be used by any application."* Every capability referenced by earlier
documents (NPC-001 §9, NPS-002 §7, NPS-008 §5, NPS-010 §5) as "the
capability registry" refers to this document.

Unlike most NPS documents, this one is expected to grow continuously as new
capability classes are needed — new entries **MUST** be added here (via the
normal change process, NPC-001 §6) rather than referenced before they
exist.

## 2. Entry Format

Every capability class **MUST** be recorded with:

| Field | Meaning |
|-------|---------|
| ID | Stable identifier, `CAP-<NAME>` |
| Description | What holding this capability allows |
| Risk Tier | `Low`, `Medium`, `High` — informs default prompt behavior (§4) |
| Android Permission Mapping | Corresponding Android permission(s), per NPS-008 §5, or "—" if none |
| Default Grant | Whether a container may hold this without an explicit user prompt |

## 3. Registered Capabilities (v1)

| ID | Description | Risk Tier | Android Mapping | Default Grant |
|----|--------------|-----------|-------------------|----------------|
| `CAP-CAMERA` | Access one or more camera devices | High | `android.permission.CAMERA` | Prompt required |
| `CAP-MICROPHONE` | Access audio input devices | High | `android.permission.RECORD_AUDIO` | Prompt required |
| `CAP-NETWORK` | Open outbound network connections | Medium | `android.permission.INTERNET` | Prompt required |
| `CAP-NETWORK-LISTEN` | Accept inbound network connections | High | — | Prompt required |
| `CAP-DOCUMENTS` | Read/write the user's document storage locations | Medium | `android.permission.READ/WRITE_EXTERNAL_STORAGE`-equivalent | Prompt required |
| `CAP-FILESYSTEM-NYFS` | Direct NyFS access beyond the container's own image/overlay (NPS-004, NPS-006) | High | — | Denied by default |
| `CAP-USB` | Access attached USB devices | High | `android.hardware.usb.*`-equivalent | Prompt required |
| `CAP-BLUETOOTH` | Access Bluetooth radio and paired devices | Medium | `android.permission.BLUETOOTH*` | Prompt required |
| `CAP-LOCATION` | Access coarse or fine device location | High | `android.permission.ACCESS_*_LOCATION` | Prompt required |
| `CAP-DISPLAY` | Present a window/surface via the adaptive UI shell (NPS-009) | Low | — (implicit for any GUI app) | Default grant |
| `CAP-INPUT` | Receive keyboard/mouse/controller/touch input events | Low | — (implicit for any GUI app) | Default grant |
| `CAP-NOTIFY` | Post user-facing notifications | Low | `android.permission.POST_NOTIFICATIONS` | Default grant |
| `CAP-BACKGROUND-EXEC` | Continue executing while not the foreground container | Medium | — (approximated by Android background-execution limits) | Prompt required |
| `CAP-IPC-HIGH-THROUGHPUT` | Non-default IPC token-bucket parameters (ADR-0009 §"Decision") | Medium | — | Denied by default; requires justification per NPS-010 §7.1 |
| `CAP-AI-DIAGNOSTICS-READ` | Read-only access to system diagnostics and performance counters (ADR-0011) | Low | — | Default grant for AI assistant containers only; not grantable to arbitrary applications without justification |
| `CAP-AI-SUGGEST-ACTION` | Present a suggested system/settings change to the user for their own explicit action (ADR-0011); does NOT permit executing the change itself | Low | — | Default grant for AI assistant containers only |
| `CAP-CLOUD-SYNC` | Transmit user-selected data (saves, settings, installed-app list) to a user-configured cloud endpoint | Medium | — | Denied by default; requires explicit opt-in per NPC-001 §10.2 |
| `CAP-CONTACTS` | Read/write the user's contacts data | High | `android.permission.READ_CONTACTS` / `WRITE_CONTACTS` | Prompt required |
| `CAP-CALENDAR` | Read/write the user's calendar data | Medium | `android.permission.READ_CALENDAR` / `WRITE_CALENDAR` | Prompt required |
| `CAP-TELEPHONY` | Place/receive calls and read call state (relevant to Android-compat phone-class devices only) | High | `android.permission.CALL_PHONE`, `READ_PHONE_STATE` | Prompt required |
| `CAP-SMS` | Send/read SMS and MMS messages | High | `android.permission.SEND_SMS`, `READ_SMS`, `RECEIVE_SMS` | Prompt required |
| `CAP-SENSORS` | Access motion/environmental sensors (accelerometer, gyroscope, ambient light, etc.) | Low | `android.permission.BODY_SENSORS`, `HIGH_SAMPLING_RATE_SENSORS` | Prompt required for body sensors; default grant for basic motion sensors |
| `CAP-MEDIA-IMAGES` | Read the user's photo/image library | Medium | `android.permission.READ_MEDIA_IMAGES` | Prompt required |
| `CAP-MEDIA-VIDEO` | Read the user's video library | Medium | `android.permission.READ_MEDIA_VIDEO` | Prompt required |
| `CAP-MEDIA-AUDIO` | Read the user's audio library | Medium | `android.permission.READ_MEDIA_AUDIO` | Prompt required |
| `CAP-NEAR-FIELD` | Access NFC hardware | Medium | `android.permission.NFC` | Prompt required |
| `CAP-BIOMETRIC` | Request biometric authentication (fingerprint/face) via the platform's own prompt, without exposing raw biometric data to the application | Low | `android.permission.USE_BIOMETRIC` | Default grant (the OS-owned prompt itself, not raw sensor access, is what's exposed) |

## 4. Default Grant Behavior

4.1. `Default grant` capabilities **MAY** be included in a container
manifest without an interactive user prompt, since they carry no access to
sensitive user data or hardware, per Risk Tier `Low`.

4.2. `Prompt required` capabilities **MUST NOT** be silently granted; the
container manifest evaluation step (NPS-010 §4.2) **MUST** result in a
user-visible prompt before the grant completes, unless the user has
previously granted that exact capability to that exact application and has
not revoked it.

4.3. `Denied by default` capabilities **MUST NOT** be grantable through the
standard prompt flow; they require an explicit administrative or
developer-mode action, since their Risk Tier reflects platform-level
rather than per-app-data risk.

## 5. Adding a New Capability

Per NPC-001 §9.3 and NPC-001 §3.2 (subsystem ownership), adding a capability
requires:

1. An entry proposed in this document via the standard change process
   (NPC-001 §6), including ID, description, risk tier, and default grant
   behavior.
2. Review by the `security` subsystem owner (NPC-008) and, if the
   capability is requested by another subsystem, that subsystem's owner
   too.
3. Once accepted, the capability becomes usable in container manifests
   (NPS-010 §4.2); it **MUST NOT** be referenced by any other document as
   available before this step completes.

## 6. Relationship to Android Permissions

Per NPS-008 §5.2, an Android permission with no entry in this table
**MUST NOT** be silently granted. As of this version, common Android
permissions are mapped (§3), but the mapping is intentionally incomplete —
unmapped permissions **MUST** be denied until a corresponding entry is
added here, rather than approximated.

## 7. Open Questions *(Informative)*

- Whether `CAP-FILESYSTEM-NYFS` should be split into finer-grained
  sub-capabilities (e.g. read-only cross-container access for backup
  tools) is deferred pending a concrete use case.
- The full Android permission surface is large; remaining mappings will be
  added incrementally as specific compatibility gaps are identified during
  NPS-008 implementation work, per §6.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial registry populated (Milestone 6) |
| 1.1.0   | 2026-07-12 | Add `CAP-AI-DIAGNOSTICS-READ`, `CAP-AI-SUGGEST-ACTION`, `CAP-CLOUD-SYNC` (Milestone 8, per ADR-0011 and NPC-001 §10.2) |
| 1.1.1   | 2026-07-12 | Architecture Group review completed (Milestone 9). Status: Draft → Accepted. |
| 1.2.0   | 2026-07-13 | Add `CAP-CONTACTS`, `CAP-CALENDAR`, `CAP-TELEPHONY`, `CAP-SMS`, `CAP-SENSORS`, `CAP-MEDIA-LIBRARY`, `CAP-NEAR-FIELD`, `CAP-BIOMETRIC` — expanding Android permission mapping per §6, still intentionally incomplete |
| 1.3.0   | 2026-07-13 | Split `CAP-MEDIA-LIBRARY` into `CAP-MEDIA-IMAGES`/`CAP-MEDIA-VIDEO`/`CAP-MEDIA-AUDIO`, closing threat model finding FIND-CAPABILITY-004 (NPS-021 §5.3): the single coarse capability could over-grant relative to a narrower Android permission request |

---
**End of Document**
