---
title: Privilege Boundaries and Capability Escalation Analysis
document_id: NPS-021
version: 1.0.0
status: Draft
classification: Normative
subsystem: security
owners:
  - Nythera Architecture
created: 2026-07-13
updated: 2026-07-13
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, NPC-009, NPS-018, NPS-019, NPS-020, NPS-010, NPS-011]
---

# NPS-021 — Privilege Boundaries and Capability Escalation Analysis

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It is
**Phase 3** of the threat model, deepening `TB-CAPABILITY` beyond
NPS-020's survey-level pass: specifically `FIND-CAPABILITY-001` and
`FIND-CAPABILITY-002`, which NPS-020 §6 explicitly deferred here rather
than resolving on the spot.

## 2. Scope

This document maps every point where a capability can legitimately change
hands or scope (the privilege boundary map, §3), systematically walks
through how an attacker might make that happen *illegitimately* (the
escalation attack tree, §4), and resolves what can be resolved now (§6).
It does not re-litigate `FIND-BACKEND-001` (a NyHAL backend actually
enforcing what NyCore assumes) — that belongs to Phase 4 (Container
Escape Analysis & Runtime Isolation), since it's a backend-conformance
question, not a capability-model question.

## 3. Privilege Boundary Map

`TB-CAPABILITY` from NPS-018 §4 is one line in the trust boundary table;
in practice it's five distinct sub-boundaries, each with its own
enforcement point:

| Sub-boundary | Where enforced | Governing spec |
|---------------|-----------------|-------------------|
| **Grant** — a container receives a capability at creation | Manifest evaluation, EVALUATING state | NPS-010 §4.2, §5.1 |
| **Attenuation** — a capability is narrowed during IPC transfer | Kernel, at `send`/`call` time | NPS-003 §5.3 |
| **Voluntary narrowing** — a container drops its own capability | Self-initiated, kernel-enforced | NPS-002 §7.3 |
| **Revocation** — a capability is removed from an active container | User/administrative action, or endpoint owner action | NPS-010 §6, NPS-003 §4.3 |
| **Audit** — the record of what happened at the other four boundaries | Audit store, read by user | NPS-010 §8 |

Every escalation path in §4 is an attempt to make one of these five
sub-boundaries behave incorrectly — grant something it shouldn't, fail to
narrow something it should, or falsify the record of what happened.

## 4. Capability Escalation Attack Tree

For each leaf, "Existing Mitigation" cites the spec already governing it;
"Assessment" states whether that mitigation is a genuine control or an
unverified claim (implementation doesn't exist yet).

### 4.1 Attack the Grant boundary
- **Request an undefined capability, hope it's silently allowed.**
  Mitigated by NPS-010 §4.2 ("a manifest requesting an undefined
  capability MUST be rejected"). Assessment: sound as specified;
  unverified in implementation (none exists).
- **Request a capability that was valid when the registry was last read
  but has since been deprecated (the race NPS-020 flagged as
  `FIND-CAPABILITY-001`).** Analyzed in depth: §5.1.
- **Submit a manifest requesting a capability broader than the requesting
  process's own container holds, hoping evaluation doesn't check the
  requester's own grant.** Mitigated by NPS-002 §7.1 (subset
  inheritance) and NPS-010 §5.1 (fixed at evaluation). Assessment: sound
  as specified.

### 4.2 Attack the Attenuation boundary
- **Craft a capability-transfer descriptor that claims to attenuate but
  actually widens.** Mitigated by NPS-003 §5.2–§5.3 ("MUST NOT transfer
  broader than held," "MUST NOT be widened at transfer time"). Assessment:
  this is `FIND-CAPABILITY-003` — the requirement exists and is correct,
  but nothing currently formalizes it as an individually-testable
  obligation. Resolved in §6 by adding `REQ-IPC-0004`.

### 4.3 Attack the Voluntary Narrowing boundary
- **Narrow a capability, then attempt to re-widen it later.** Explicitly
  forbidden by NPS-002 §7.3 ("MUST be irreversible for the lifetime of
  that container instance"). Assessment: sound as specified; this is the
  cleanest of the five sub-boundaries because the rule has no exception
  clause to attack.

### 4.4 Attack the Revocation boundary
- **Continue using a capability after it's been revoked, by racing the
  revocation.** Mitigated by NPS-003 §4.3 (revocation "MUST take effect
  for all future operations without requiring cooperation from capability
  holders") and NPS-010 §6.1 (revocation "MUST take effect... immediately").
  Assessment: sound as specified; the actual race-safety depends on
  implementation, not specification — a future NPS-017 backend
  conformance test should specifically exercise this.

### 4.5 Attack the Audit boundary
- **Compromise a container, then falsify or delete the audit trail of
  what that container was granted, to hide the compromise from the
  user.** This is `FIND-CAPABILITY-002`. Analyzed in depth: §5.2.

### 4.6 Cross-cutting: attack via a mapping gap, not the model itself
- **Exploit a capability whose scope is coarser than the permission model
  it's mapped from, obtaining more access than the original request
  implied.** This is a new finding, `FIND-CAPABILITY-004`, surfaced by
  this deeper pass rather than carried from Phase 2. Analyzed in depth:
  §5.3.

### 4.7 Cross-cutting: attack the governance process, not the runtime
- **Claim ownership of the `security` subsystem in `SUBSYSTEM_OWNERS.md`
  (currently Unassigned, and per NPC-008 §"Process for Assigning an
  Owner," claimable without an Architecture Group vote) to gain outsized
  influence over future capability registry changes.** This is
  `FIND-CAPABILITY-005` — a process/governance risk, not a runtime
  security control. Noted at low severity and explicitly **not** resolved
  by a technical amendment (see §5.4 and NPS-018 §7's non-goals — this
  project's threat model scopes technical controls, not organizational
  ones, and conflating them would blur where a fix actually belongs).

## 5. Deep-Dive Analysis

### 5.1 `FIND-CAPABILITY-001` — Capability Definition Race

**The race, precisely:** NPS-010 §4.2 checks a requested capability
against the registry during EVALUATING; §5.1 says the grant is fixed "at
the end of" that state. If those are two separate reads of the registry
rather than one atomic operation, a capability could be deprecated
between the check and the grant, and the container could still receive
it.

**Why this was scored Low/Low in Phase 2:** it requires a capability
*removal* event, which has never happened in the registry's history (it
has only ever grown, per NPS-011's revision history). But "hasn't
happened yet" is a schedule fact, not a design guarantee — the registry
process (NPS-011 §5) doesn't forbid deprecation, so the race is real
whenever it first occurs.

**Resolution:** amend NPS-010 §4.2 to require the validity check and the
grant to be a single atomic operation against one consistent read of the
capability registry, closing the race by construction rather than by
making deprecation rare. Applied in §6.

### 5.2 `FIND-CAPABILITY-002` — Audit Log Tamper-Evidence

**The gap, precisely:** NPS-010 §8.1 requires grants/revocations be
recorded in a user-inspectable form. It says nothing about whether that
record can be altered after the fact by something with write access to
wherever it's stored — including, notably, a compromised system service
that legitimately needs to *write* to it in the first place.

**Why this matters more than `FIND-CAPABILITY-001`:** an attacker who can
falsify the audit trail doesn't just gain a capability — they gain a
capability *and* the ability to hide that they have it, defeating the
entire purpose NPC-001 §9.1 and NTM-000 §4 ("Transparency") assign to
capability visibility.

**Resolution:** this needs a real mechanism decision, not a one-line
amendment — the natural options (append-only storage, cryptographic
hash-chaining of entries, write-once media) have different cost/benefit
tradeoffs worth recording as their own ADR rather than silently picked.
Applied in §6 via new `ADR-0018`.

### 5.3 `FIND-CAPABILITY-004` — Capability Granularity Mismatch

**The gap, precisely:** NPS-011 §3 defines a single `CAP-MEDIA-LIBRARY`
capability covering "the user's photo/video/audio library," mapped from
three *separate* Android permissions (`READ_MEDIA_IMAGES`,
`READ_MEDIA_VIDEO`, `READ_MEDIA_AUDIO`). An Android app that declares
only `READ_MEDIA_IMAGES` in its manifest and is granted the single
coarser `CAP-MEDIA-LIBRARY` capability would, under a naive
implementation, receive video and audio access it never asked for and
the user never consciously approved for that specific app.

**Severity:** Medium/Medium — this is a real over-grant, but bounded to
one capability class discovered in this pass; it's the kind of thing a
systematic review is specifically good at catching before it ships,
rather than a deep architectural flaw.

**Resolution:** split `CAP-MEDIA-LIBRARY` into three capabilities
matching the three Android permissions it was mapped from, so grant
granularity matches request granularity. Applied in §6.

### 5.4 `FIND-CAPABILITY-005` — Subsystem Ownership as a Soft Privilege Path

Noted, not resolved technically. `NPC-008`'s "claim an Unassigned slot
without a vote" design was a deliberate simplicity choice for a
single-contributor-plus-AI project (NPC-008 §"Notes"). As the project
gains contributors, this **SHOULD** be revisited — but that's a
governance-document change (`NPC-001` §3, `NPC-008`), not a threat-model
finding with a runtime fix. Recorded here so it isn't lost, tracked
against `NPC-008` rather than against a capability-enforcement mechanism
that wouldn't be the right tool for this particular risk.

## 6. Resolutions Applied This Pass

| Finding | Resolution | Where |
|---------|------------|-------|
| `FIND-CAPABILITY-001` | NPS-010 §4.2 amended: validity check and grant MUST be one atomic operation | `NPS-010` v1.1.0 |
| `FIND-CAPABILITY-002` | New ADR-0018 (hash-chained append-only audit log); NPS-010 §8 amended to require it | `ADR-0018`, `NPS-010` v1.1.0 |
| `FIND-CAPABILITY-003` | Formalized as an individually-testable requirement | `REQ-IPC-0004` |
| `FIND-CAPABILITY-004` | `CAP-MEDIA-LIBRARY` split into `CAP-MEDIA-IMAGES`, `CAP-MEDIA-VIDEO`, `CAP-MEDIA-AUDIO` | `NPS-011` v1.3.0 |
| `FIND-CAPABILITY-005` | Recorded, not resolved technically — flagged for a future `NPC-008` governance revision | This document only |

All five findings from this phase have a disposition; none are left as a
bare observation with nowhere to go, per NPS-018 §8.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-13 | Initial draft — Phase 3 of the threat model (privilege boundaries and capability escalation) |

---
**End of Document**
