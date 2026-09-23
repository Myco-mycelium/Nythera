---
title: Identity and User Data Separation
document_id: NPS-029
version: 1.0.0
status: Draft
classification: Normative
subsystem: identity
owners:
  - Nyrqis Architecture
created: 2026-09-23
updated: 2026-09-23
ai_assisted: true
review_cycle: Continuous
depends_on: [NTM-000, NPC-001, NPS-001, NPS-004, NPS-010, NPS-011, NPS-025, ADR-0002, ADR-0022]
---

# NPS-029 — Identity and User Data Separation

## 1. Status of This Document

This document is **normative** for the Identity subsystem: user
accounts, authentication, and per-user data separation. It is a
`Draft` — proposed against the existing specification set, closing the
gap NPS-025 §4.14 records as a deliberate placeholder and the external
review surfaced under Milestone 11's gap categories. It follows the
roadmap's rule: a new subsystem gets its own NPS before its objects are
specified anywhere else. Where this document defers, it names the
deferral explicitly (§9); it does not invent mechanisms that belong to
other specifications.

## 2. Purpose *(Informative)*

The platform's existing specifications are identity-blind: containers
(NPS-010), capabilities (NPS-011), and the object model (NPS-025)
describe *what runs* and *what it may do*, but not *who it runs for*.
NPC-001 §10's data-separation guarantees and NPS-025 §4.14's `User`
placeholder both point at the missing layer. This document supplies it:
a `User` is the subject that owns sessions, objects, and data; the
`Session` is the boundary at which authentication happens; per-user
data separation is enforced with machinery that already exists (NyFS
volumes, container capability sets) rather than a new enforcement
plane.

## 3. Identity Model

### 3.1 User

A **User** is a persistent identity that owns objects and data. A User:

1. **MUST** have a stable, unique ID that is never reused (NPS-025
   §3.1's rule, applied at identity scope).
2. **MUST** own its Workspaces, Applications, Packages, Games, and
   per-user data volumes (the ownership relationships NPS-025 §4.14
   requires); §5 makes ownership the basis of object visibility.
3. **MUST NOT** be a capability holder directly — capabilities are
   granted to containers (NPS-010 §4–§5). A User *possesses* no
   authority; a Session *inherits* authority into the containers it
   starts. This keeps the kernel's sole-arbiter rule (NPS-003 §5.4)
   untouched: identity composes with the capability model; it does not
   extend or bypass it.

### 3.2 Session

A **Session** is an authenticated interval of use, created at login and
destroyed at logout or lock:

1. Every Session **MUST** reference exactly one User.
2. Every Session **MUST** have a lifecycle state: `authenticating →
   active → locked ⇄ active → terminating → terminated`. Transitions
   into and out of `locked` **MUST NOT** terminate running
   Applications or their containers (the same continuity rule as
   NPS-009 §6.2's mode transitions; a locked session preserves its
   containers, frozen or running, behind the lock surface).
3. A Session **MUST** be hosted in its own session container whose
   capability set contains nothing beyond what the login surface needs
   (ADR-0004, NPS-010 §4.2) — authentication is an ordinary
   capability-scoped program, not a privileged singleton.
4. Exactly one Session per User **MUST** be `active` system-wide at a
   time; concurrent login of the same User on a second seat **MUST**
   either refuse or terminate the first Session per an explicit,
   user-visible policy choice (implementation decides which; silent
   dual-active is forbidden).

### 3.3 Authentication

1. The login surface **MUST** be an unspoofable system confirmation
   surface in the sense of NPS-015 §5.2: a credential prompt presented
   by the session manager is part of the trusted computing base for
   identity, and MUST NOT be renderable by an ordinary container.
2. Credential verification **MUST** happen in the session manager's
   container; credential material (verifiers, keys) **MUST** be
   persisted only in vault custody (ADR-0022/0023 — envelope
   encryption, keys never at rest in plaintext) or as salted,
   memory-hard hashes (Argon2id or its successor standard), never in
   plaintext or unsalted fast hashes.
3. Failure handling: authentication failure **MUST** be
   rate-limited/backoff'd and **MUST** appear in the tamper-evident
   audit log (ADR-0018) as `identity_auth_failure` records; repeated
   failures **MUST NOT** leak which part of a multi-factor prompt
   failed.
4. Local-first: authentication **SHOULD** be local-account
   authentication at launch. Cloud-synced identities (NPS-016) are an
   extension: a cloud identity **MUST** map onto exactly one local
   User, and loss of connectivity **MUST NOT** prevent local login.

### 3.4 Authorization composition

On Session start, the session manager requests the User's session
containers from the ordinary container path (NPS-010 §4); the granted
capability set is evaluated exactly as for any container — with one
addition: the session manager **MUST** stamp every container started
for a Session with the owning User's ID (§5), so ownership is
mechanically visible to the object registry and the audit log. Identity
thus adds **no new authorization mechanism**: it adds a subject, an
authentication boundary, and an ownership stamp.

## 4. Object Types Added to NPS-025

This section's types **MUST** be added to the NPS-025 catalogue
(NPS-025 §1's process; done in this document's landing commit for
NPS-025 as its v1.1.0):

**User** — Purpose: the persistent identity owning objects and data
(NPS-025 §4.14's placeholder resolved). Key fields: `id`, `display-name`,
`data-volume` (ref, §6.1), `auth-config` (ref), `created-at`,
`last-login-at` *(informative)*, `state` (active / disabled). Lifecycle:
created by an administrator action (first-boot setup creates the first
User); disabled rather than deleted — a disabled User's data and object
ownership persist, and deletion is a migration-grade MAJOR operation
(NPS-025 §5.2) requiring explicit data-disposition. Permissions: none
directly (§3.1.3). Relationships: owns Workspaces, Sessions, Packages,
Games, per-user data volumes.

**Session** — Purpose: an authenticated interval of use (§3.2). Key
fields: `id`, `user` (ref), `state` (the §3.2.2 state machine),
`session-container` (ref), `started-at`, `locked-at` *(informative)*.
Lifecycle: the §3.2.2 state machine. Permissions: the session
container's own granted set only. Relationships: belongs to one User;
stamps the containers it starts.

## 5. Per-User Data Separation

### 5.1 Ownership stamp

Every object the registry records for a Session's containers **MUST**
carry the owning User's ID. Object visibility follows: the registry
service **MUST NOT** enumerate or expose one User's objects to another
User's containers except through a granted capability that
deliberately crosses the boundary (e.g. a shared media library,
`CAP-MEDIA-*`), which remains subject to the ordinary grant flow
(NPS-010 §4.2) — and any such cross-User grant **MUST** be recorded in
the audit log with both User IDs.

### 5.2 Data volumes

Per-user data **MUST** live in per-User NyFS volumes (NPS-004,
ADR-0002 copy-on-write), created at User creation and named by the
User's ID:

1. A container started for a Session **MUST** receive only its own
   User's volume mounted (NPS-006 §5 mount path), plus volumes another
   User's grant deliberately shares.
2. The kernel-enforced mount namespace boundary is the enforcement
   point; identity adds **no** second filesystem mechanism.
3. A User's volume **MUST** be unloadable only when no container of
   that User holds it — the ordinary NyFS lifecycle decides, identity
   adds no override.
4. Vault custody (ADR-0022) is per-User: a User's vault keys are
   derived from material bound to the User's ID and credential state
   (the exact derivation is an implementation parameter deferred to
   validation, §9), so that disabling a User renders its vault
   materials unusable without deleting data.

### 5.3 Multi-User isolation posture

The isolation guarantee is composed, not new: containers isolate code
(NPS-010), capabilities isolate authority (NPS-003/NPS-011), volumes
isolate data (NPS-004). Identity's contribution is the **ownership
stamp** (§5.1) that makes "whose" mechanically checkable, and the
default rule: cross-User access is **deny-by-default**, permitted only
through explicit, audited, capability-mediated grants. NPC-001 §10's
separation guarantee is thus enforceable with no new kernel surface.

## 6. Administrative Surfaces

### 6.1 User management

User creation, disabling, and credential change **MUST** be
administrator actions surfaced through the system settings surface,
**MUST** be audited (ADR-0018) as `identity_user_admin` records, and
**MUST NOT** be reachable from ordinary application containers except
through a granted administrative capability class (a new
`CAP-IDENTITY-ADMIN` **MUST** be registered in NPS-011 before
implementation; this document does not add it to the registry
directly, per NPS-011 §6's process).

### 6.2 The first-boot User

First boot **MUST** present local account creation before the desktop
session (NPS-001 §5 Stage 6 order): the first User is created by the
person holding the machine, and the login surface's unspoofability
(§3.3.1) applies from that moment. No default or backdoor account
**MUST** exist.

### 6.3 Remote/administrative sessions

An administrative session path (e.g. recovery console) **MUST**
authenticate with the same machinery (§3.3), **MUST** be audited with a
distinct record type, and **MUST NOT** bypass the per-User volume
rules; it may hold `CAP-IDENTITY-ADMIN` but gains no implicit data
access.

## 7. Threat-Model Considerations

Recorded for the threat model's next pass (the SURFACE enumeration
methodology, NPS-019):

- **SURFACE-IDENT-0001 (candidate)** — the login surface: spoofing
  (mitigated by §3.3.1's unspoofability), credential-stuffing (§3.3.3
  rate limits), and lock-surface bypass.
- **SURFACE-IDENT-0002 (candidate)** — the session manager container:
  it stamps ownership (§5.1), so its compromise is an identity-plane
  compromise; its capability set (§3.2.3) must be minimal and its
  audit records chained.
- **FIND-IDENT-0001 (candidate)** — the lock transition (§3.2.2)
  preserves running containers: the lock surface must be as trusted as
  the login surface, or a locked session's display path becomes the
  spoofing vector.

These are candidates for NPS-019's methodology to formalize, not
findings yet — recorded here so the next threat-model pass starts from
this document rather than rediscovering it.

## 8. What This Document Does NOT Specify

- **Cloud identity federation** (SSO/OIDC): out of scope; NPS-016's
  sync covers settings, not identity provision. The local-first rule
  (§3.3.4) is the fence.
- **Credential-change flows' UI specifics**: policy is normative
  (audited, authenticated), screens are the shell's (NPS-009).
- **User deletion**: disabled-state only in this revision; deletion is
  deferred until data-disposition semantics (overlay retention vs
  export vs destruction) get their own review.
- **Biometrics / hardware-bound credentials**: the ADR-0014 trust
  anchor and secure-boot story may eventually bind identity to
  hardware; nothing here requires or forbids it.

## 9. Open Questions *(Informative)*

- Vault key derivation per User (§5.2.4): bind to credential hash,
  hardware state, or both — deferred to implementation validation with
  ADR-0023's custodian.
- Session container placement (per-User manager vs shared manager with
  stamps): the boot sequence's Stage 5 registry is assumed single; §6.3
  complicates that; decide with NPS-001's owner.
- Guest sessions: a ephemeral User with no persistent volume would
  compose cleanly with §5; deferred as policy, not mechanism.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-09-23 | Initial draft — Identity subsystem: User/Session objects, authentication rules, per-user data separation via the existing capability/volume machinery; closes NPS-025 §4.14's placeholder |
