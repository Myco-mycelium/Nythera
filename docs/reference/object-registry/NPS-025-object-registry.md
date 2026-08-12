---
title: Object Registry
document_id: NPS-025
version: 1.0.0
status: Draft
classification: Normative
subsystem: core-architecture
owners:
  - Nyrqis Architecture
created: 2026-08-12
updated: 2026-08-12
ai_assisted: true
review_cycle: Continuous
depends_on: [NTM-000, NPC-001, NPS-004, NPS-006, NPS-009, NPS-010, NPS-011, NPS-012, NPS-013, NPS-014, NPS-015]
---

# NPS-025 — Object Registry

## 1. Status of This Document

This document is **normative** as a catalogue: every object type the
platform manages, with its fields, lifecycle, permissions, serialization,
and relationships. It is a `Draft` — the object model below is proposed
against the existing specification set and will be tightened as
implementation begins. New object types **MUST** be added here (via the
normal change process, NPC-001 §6) rather than referenced before they
exist, following the same discipline the capability registry established
(NPS-011 §6: intentionally incomplete, expanded incrementally).

## 2. Purpose *(Informative)*

Individual specifications reference objects — workspaces (NPS-009),
containers (NPS-010), capabilities (NPS-011), game images (NPS-006) — but
no single document previously defined what those objects *are*: their
fields, who may see or touch them, how they are serialized, and how they
relate to each other. This document is that single place, so that an
implementation, an API design (API-001), and an ABI design (ABI-001) all
start from the same object model instead of each inventing one.

## 3. Cross-Cutting Rules

### 3.1 Object IDs
Every object instance **MUST** have a stable, unique object ID. Object IDs
**MUST NOT** be reused after the object is destroyed, for the same reason
document IDs are never renumbered (ADR-0017): a log entry, a capability
grant, or an audit record may reference it.

### 3.2 Permissions
Access to any object — reading its state, mutating it, enumerating it —
**MUST** be gated by the same capability model as everything else
(NPS-010 §4–§5, NPS-011). An object's entry below lists which capability
class (if any) gates the *principal* operations; the object registry must
never become a second, informal permission system (cf. NPS-009 §7.1's
prohibition on the UI shell becoming one).

### 3.3 Serialization
Exact wire/serialization layouts for these objects are deferred to the
ABI (ABI-001, per the NPS-003 §9 deferral precedent) and the public API
(API-001). This document fixes *what the objects are*, not *how they are
encoded*.

### 3.4 Registry Service
The object registry is exposed as a user-space service started during
Service Bring-Up (NPS-001 §5, Stage 5 — the "capability/service registry").
It **MUST** itself run as a capability-scoped container (ADR-0004,
NPS-010), holding only the capabilities needed to maintain the object
graph.

## 4. Object Type Catalog

Each entry lists: **Purpose**, **Key fields**, **Lifecycle**,
**Permissions**, **Relationships**. Fields marked *(informative)* are
suggested for implementation and not yet normatively required.

### 4.1 Workspace

| | |
|---|---|
| **Purpose** | The UI shell's organization of a device mode's screens (NPS-009 §3). One active workspace per session. |
| **Key fields** | `id`, `mode` (one of NPS-009 §3's modes), `windows` (ordered set), `active-window`, `device-profile` *(informative)* |
| **Lifecycle** | Created at session start (NPS-001 §5 Stage 6); persists across mode transitions — mode change **MUST NOT** terminate running applications or their containers (NPS-009 §6.2). |
| **Permissions** | Presentation and input governed by `CAP-DISPLAY` / `CAP-INPUT`. |
| **Relationships** | Owns `Window` objects (4.2); one per user session. |

### 4.2 Window

| | |
|---|---|
| **Purpose** | A presentation surface owned by one application container (NPS-009 §5.1, NPS-008 §6 for Android-compat presentation). |
| **Key fields** | `id`, `owner-container`, `bounds`, `z-order`, `focus`, `mode-presentation` (how the window presents in the current mode) |
| **Lifecycle** | Created when its container presents a surface; destroyed when the container exits or the surface is closed. Persists across mode transitions with its owner (NPS-009 §6.2). |
| **Permissions** | Presenting requires `CAP-DISPLAY`; receiving input requires `CAP-INPUT`. |
| **Relationships** | Owned by a `Workspace`; hosted by an `Application` (4.3). |

### 4.3 Application

| | |
|---|---|
| **Purpose** | A running instance of an installed package, hosted in a container (ADR-0004, NPS-010). |
| **Key fields** | `id`, `package` (ref), `container` (ref), `runtime-class` (native / windows-compat / android-compat, per NPS-007/008), `state` (the NPS-010 §4 state machine), `capability-grants` (refs to 4.5), `resource-limits` (NPS-010 §7) |
| **Lifecycle** | Exactly the container lifecycle of NPS-010 §4: REQUESTED → EVALUATING → ACTIVE → SUSPENDED ⇄ ACTIVE → TERMINATING → TERMINATED. |
| **Permissions** | Whatever its container holds; nothing else. A runtime class provides **no** implicit trust (NPS-003 §8.1). |
| **Relationships** | Owns `Capability` instances (4.5); hosts `Window` objects (4.2); instance of a `Package` (4.4). |

### 4.4 Package

| | |
|---|---|
| **Purpose** | An installed, verifiable unit — the `.nypkg` installable (NPS-026) that produces runnable `.nygi` images and container manifests. |
| **Key fields** | `id`, `manifest` (application identity, requested capabilities, resource limits, dependencies), `version`, `signature` (NPS-026 §6), `images` (refs to `Game` / `.nygi` images), `install-state` |
| **Lifecycle** | Install (verify signature → evaluate manifest per NPS-010 §4.2 → write images) → mount on launch (NPS-006 §5) → update (delta, NPS-026 §9) → uninstall (base image removed, overlay retained by default, NPS-006 §7). |
| **Permissions** | Installation **MUST** be a user-initiated action; the granted capability set comes from the manifest evaluated at install/launch (NPS-010 §4.2), never from the package's own claims alone. |
| **Relationships** | Produces `Application` instances (4.3); references `Game` objects (4.6); declares dependencies resolved by the package manager (NPS-026 §10). |

### 4.5 Capability (instance)

| | |
|---|---|
| **Purpose** | A specific grant of a registered capability class (NPS-011) to a specific container. |
| **Key fields** | `id`, `class` (a `CAP-*` from NPS-011), `grantee-container`, `grantor`, `attenuation` (narrowing applied at transfer, NPS-003 §5.3), `granted-at`, `revoked-at`, `audit-entry` (ref, per ADR-0018) |
| **Lifecycle** | Granted atomically at end of EVALUATING (NPS-010 §5.1); **MUST NOT** be widened after grant; may be voluntarily narrowed irreversibly (NPS-010 §5.2); revoked per NPS-010 §6. Every grant and revocation is in the tamper-evident audit log (NPS-010 §8.1). |
| **Permissions** | The kernel is the sole arbiter of validity (NPS-003 §5.4); no user-space process can forge or self-issue. |
| **Relationships** | Held by `Application` containers (4.3); class definitions live in NPS-011. |

### 4.6 Game

| | |
|---|---|
| **Purpose** | A mounted game/application image plus its writable overlay (ADR-0003, NPS-006). |
| **Key fields** | `id`, `image` (`.nygi` ref), `overlay` (ref), `mount-state`, `launcher-config`, `saves` (refs into the overlay) |
| **Lifecycle** | The NPS-006 §5 lifecycle: mount → decompress-on-demand → cache → unmount. Overlay persists independently of the base image (NPS-006 §4.3); uninstall retains the overlay by default (NPS-006 §7). |
| **Permissions** | Mounting/launching requires the container to hold the capabilities its manifest requests; the image itself is read-only (NPS-006 §3.2). |
| **Relationships** | Produced from a `Package` (4.4); hosts `Mod` objects (4.7); may be launched by `Application` (4.3). |

### 4.7 Mod

| | |
|---|---|
| **Purpose** | User- or community-authored content written into a game's overlay (NPS-006 §4.2). |
| **Key fields** | `id`, `game` (ref), `overlay` (ref), `author`, `version`, `enabled` |
| **Lifecycle** | Created when content is written into the overlay; persists with the overlay (NPS-006 §4.3); removable without touching the base image. |
| **Permissions** | Written through the container's ordinary filesystem capability for its own install directory — no special capability. |
| **Relationships** | Belongs to a `Game` (4.6). |

### 4.8 Controller

| | |
|---|---|
| **Purpose** | An input device usable for gaming and navigation (NPS-012). |
| **Key fields** | `id`, `device`, `type`, `button-map` *(informative)*, `assigned-game` *(informative)* |
| **Lifecycle** | Enumerated on connection; removed on disconnect. |
| **Permissions** | Raw input events delivered only to containers holding `CAP-INPUT` (NPS-011); device access via `CAP-USB` where applicable. |
| **Relationships** | Backed by a `Device` (4.12); consumed by `Application` (4.3). |

### 4.9 GPU

| | |
|---|---|
| **Purpose** | A graphics adapter and the feature set Nyrqis can use with it (NPS-013). |
| **Key fields** | `id`, `adapter`, `vulkan-version`, `features` (HDR, VRR, ray tracing, upscaling — per NPS-013), `driver` (ref) |
| **Lifecycle** | Enumerated at boot; updated as drivers change; the GPU command submission path is a kernel-space fast path (NPS-001 §3) that **MUST** validate command buffers and enforce submission timeouts. |
| **Permissions** | Containers with rendering access submit command buffers through the validated fast path (SURFACE-GPU-0001, NPS-019 §3). |
| **Relationships** | Consumed by `Application` (4.3); governed by NPS-013. |

### 4.10 Notification

| | |
|---|---|
| **Purpose** | A user-facing notification posted by a container holding `CAP-NOTIFY` (NPS-011). |
| **Key fields** | `id`, `source-container`, `message`, `timestamp`, `priority`, `dismissed` |
| **Lifecycle** | Posted → presented by the UI shell → dismissed or expired. |
| **Permissions** | Posting requires `CAP-NOTIFY`; the notification surface itself **MUST NOT** be spoofable as a system confirmation surface (cf. NPS-015 §5.2's unspoofable-confirmation requirement). |
| **Relationships** | Belongs to an `Application` (4.3). |

### 4.11 AI Conversation

| | |
|---|---|
| **Purpose** | A session with the local AI assistant (NPS-015). |
| **Key fields** | `id`, `assistant-container`, `transcript`, `suggestions` (refs into the suggestion audit log, ADR-0018), `outcomes` (approved/declined/ignored) |
| **Lifecycle** | Created per session; suggestions and outcomes **MUST** be recorded in the tamper-evident log (NPS-015 §5.5); transcripts persist per user choice. |
| **Permissions** | The assistant runs as an ordinary capability-scoped container (ADR-0011, NPS-015 §4); `CAP-AI-DIAGNOSTICS-READ` for read-only diagnostics, `CAP-AI-SUGGEST-ACTION` for suggestions only — never execution (NPS-011). |
| **Relationships** | Hosted by an `Application` (4.3) representing the assistant. |

### 4.12 Device

| | |
|---|---|
| **Purpose** | A hardware device attached to the system (USB, Bluetooth, NFC, storage, display). |
| **Key fields** | `id`, `class`, `bus`, `vendor` / `product`, `required-capabilities` (e.g. `CAP-USB`, `CAP-BLUETOOTH`, `CAP-NEAR-FIELD`, per NPS-011) |
| **Lifecycle** | Enumerated at attach; removed at detach. Driver architecture is user-space except the enumerated kernel fast paths (NPS-001 §4). |
| **Permissions** | Access gated by the matching `CAP-*` class; enumeration is itself a surface (SURFACE-USB-0001 etc., NPS-019 §3). |
| **Relationships** | Backs `Controller` (4.8), `GPU` (4.9), and storage objects. |

### 4.13 Service

| | |
|---|---|
| **Purpose** | A boot-time or runtime system service (NPS-001 §5 Stage 5). |
| **Key fields** | `id`, `dependencies` (ordered), `stage`, `status` (starting / ready / degraded / failed), `container` (ref) |
| **Lifecycle** | Started in dependency order during Service Bring-Up; a failure in a non-essential service **MUST NOT** halt boot (NPS-001 §6.1); failures in Stages 1–4 halt with a diagnostic screen (NPS-001 §6.2). Stage transitions **MUST** be order-validated at the API level (NPS-001 §5). |
| **Permissions** | Each service runs as a container with its own granted capability set (NPS-010). |
| **Relationships** | Composes the runtime; referenced by `Application` (4.3) and the boot sequence. |

### 4.14 Identity *(placeholder — requires its own NPS)*

The roadmap (NPC-007, Milestone 11) and the external review both surfaced
an **Identity subsystem** (user accounts, authentication, per-user data
separation) that has **no governing NPS yet**. This entry is deliberately
a placeholder: rather than specify identity objects here by indirection,
an Identity subsystem **SHOULD** get its own NPS (per the roadmap's rule
that new subsystems get their own NPS before being specified elsewhere),
at which point the object type(s) it defines — at minimum a `User` object
with ownership relationships to Workspaces, Packages, and per-user data
(NPC-001 §10) — **MUST** be added to this catalogue.

## 5. Serialization and Evolution

5.1. Object serialization formats **MUST** be versioned, and **MUST NOT**
break readers of older versions within the same ABI MAJOR version
(NPC-001 §8.1).

5.2. Adding a field to an object type is a backward-compatible MINOR
change; removing or reinterpreting a field is a MAJOR change requiring a
migration guide under `docs/how-to/` (NPC-001 §7).

## 6. Open Questions *(Informative)*

- Whether the registry is a single service or one per trust boundary is
  undecided; the boot sequence references a single capability/service
  registry (NPS-001 §5 Stage 5) which is assumed here.- The `Identity` entry (§4.14) is unresolved by design pending its own
  NPS.
- Object ID format (globally unique vs. per-registry unique) is settled in
  ABI-001 §5.3 (globally unique within a device); this entry is retained
  only for the record.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-08-12 | Initial draft — object type catalogue, closing Milestone 11 gap category 2 (Object Registry) |

---
**End of Document**
