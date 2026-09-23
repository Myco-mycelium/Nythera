---
title: Privilege Boundaries and Capability Escalation Analysis
document_id: NPS-021
version: 1.1.0
status: Draft
classification: Normative
subsystem: security
owners:
  - Nyrqis Architecture
created: 2026-07-13
updated: 2026-09-23
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

**2026-09-23 addendum (v1.1.0):** §4.8 and §5.5 extend the analysis to
the debug-attach surface decided by the Architecture Group (AG decision
log **D7**, AG_AGENDA §"Standing items, registered 2026-09-23"): the
`CAP-DEBUG-ATTACH` capability and the `debug: true` manifest class,
with the seccomp profile's ptrace denial relaxed for that manifest class
only. This addendum is the precondition D7 itself set: NPS-021
"requires an escalation-pass addendum over the new surface before
implementation lands."

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

### 4.8 New surface (D7): the debug attach channel — ptrace relaxation inside debug-class containers

**The surface, precisely:** AG decision log **D7** (2026-09-23) adds a
`debug: true` manifest class plus a `CAP-DEBUG-ATTACH` capability
(NPS-011 v1.4.0), with debugpy (Python) / gdbserver (Rust) running
**inside** the debugged container and the seccomp profile's ptrace
denial relaxed **for that manifest class only** — a named,
capability-gated exception to the `FIND-BACKEND-002` hardening. This
section extends the attack tree to that surface before the
implementation lands, per D7's own precondition.

**Why the relaxation is bounded — three fences, in enforcement order:**

1. **The PID namespace fence (structural, already in place):**
   containers run in their own PID namespaces (the backend's direct
   `unshare(2)`/`fork(2)` launch path, `backend/container.py`; the
   legacy `unshare(1)` path shares the property). `ptrace(2)`
   attach is scoped to processes the tracer can see; a debugger
   *inside* the container can therefore only trace processes within
   that same PID namespace — never sibling containers, never host
   processes — regardless of the seccomp change. What the relaxation
   restores is intra-container introspection: visibility comparable to
   what the container's own root already has over its own processes.
2. **The construction-time gate (to be built):** the ptrace denial
   lives in `_ALWAYS_DENY` (`backend/seccomp.py`) — syscalls "denied
   regardless of what capabilities a container holds." A runtime
   capability hook would contradict that invariant and add a
   per-syscall evaluation to race against. The exception MUST instead
   be a **manifest-class-conditional policy construction**: the debug
   class builds its policy with ptrace/process_vm_readv/
   process_vm_writev omitted from the always-deny set. The gate is
   then evaluated exactly once, at policy construction, from the
   manifest — there is no runtime flag to flip.
3. **The authorization fence (unchanged):** default-deny operator
   authorization at the IPC layer (NPS-011 §4.3, NPS-010 §4.2) applies
   to every control op touching debug-class containers; attach-class
   operations are operator-only (the same posture the `nui-validate`
   op already has). The only known container-entry channel today is
   operator-mediated exec; D7 does not change that.

**Attack-tree nodes this surface adds:**

- **Attack the manifest:** a production workload declares `debug: true`
  (or has it retrofitted) to inherit the relaxed posture. Manifest
  evaluation (NPS-010 §4.2) binds what was validated to what runs —
  `FIND-CAPABILITY-001`'s atomicity requirement applies to the class
  declaration exactly as it does to capability grants — and the debug
  class MUST be user-visible at evaluation time, not silently accepted.
- **Attack the grant:** `CAP-DEBUG-ATTACH` treated as ceremonial —
  granted by habit the way §5.4's soft paths arise. §5.5's requirements
  close this by making the entry denied-by-default and class-conditional.
- **Attack the relaxation's scope:** a debug-class container used as a
  stepping stone. The PID-namespace fence caps what ptrace itself can
  reach; lateral movement toward sibling containers or the host would
  require the §3 boundaries to fail *independently* of this change. No
  new cross-container path is opened by D7.
- **Attack the tooling:** debugpy/gdbserver are network listeners by
  nature, inside the boundary. `CAP-NETWORK-LISTEN` (High tier,
  prompt-required) applies to any non-loopback debug endpoint; the
  honest default is loopback-only binding inside the container.

**Cross-cutting — the `FIND-BACKEND-002` relationship:** the hardening
that denied ptrace did so as a static always-deny list; D7 creates the
platform's first named exception to it. The exception must be as loud
as the denial: the policy-construction site carries the D7 citation,
and the class name is greppable in the builder — never a bare boolean
flag passed through an unmarked code path.

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

### 5.5 `FIND-CAPABILITY-006` — The Debug-Class Manifest as a Privilege Gradient

**The gap, precisely:** D7's `debug: true` manifest class plus
`CAP-DEBUG-ATTACH` create a second manifest class whose isolation is
deliberately weaker, entered by a single boolean in the manifest. The
capability model's own history is the warning: classes that begin as
developer conveniences drift into production postures (§5.4's lesson —
process facts outlive their justifications). If debug-class is merely
advisory metadata, the ptrace relaxation is a load-bearing flag that
nobody is accountable for.

**Why this matters:** unlike `FIND-CAPABILITY-004`'s over-grant (one
capability at the wrong granularity), this is an over-grant at the
**policy-construction layer**: the container's entire syscall posture
differs by a manifest boolean. It is the widest single-bit privilege
gradient in the platform, and it is being added *by decision* — which
is exactly why it must be made visible, gated, and auditable now rather
than after debug containers are normalized.

**Resolution — requirements the implementation MUST satisfy** (the
registry entry is applied in §6):

1. `CAP-DEBUG-ATTACH` is registered **High** risk tier, default grant
   **"Denied by default; debug manifest class only; operator-only
   operations"** — the first capability whose grantability is
   conditional on a manifest class (§2's entry format accommodates it
   in the Default Grant field).
2. The debug manifest class MUST be visible in every surface that
   reports container state (status, containers list, the debug
   bundle's meta): a debug-class container is never indistinguishable
   from a production one in operator tooling.
3. The seccomp relaxation MUST be constructed at policy-build time
   from the manifest class — never a runtime per-syscall evaluation
   against a shared static profile — keeping the gate inside the
   audited construction site (§4.8, fence 2).
4. Debug tooling endpoints MUST default to loopback-only binding
   inside the container; any wider binding additionally requires
   `CAP-NETWORK-LISTEN` per its own registry entry.
5. The hash-chained audit log (ADR-0018) MUST record the manifest
   class at evaluation time, so that "this container ran debug-class"
   is tamper-evidently part of the very trail the debug tooling
   captures (DBG-001 Phase A `--chain-id`).

**Severity:** High/High as a design requirement — the mitigations are
cheap now (class visibility, a construction-time gate, one registry
row) and expensive later (retrofitting visibility after the class is
deployed).

## 6. Resolutions Applied This Pass

| Finding | Resolution | Where |
|---------|------------|-------|
| `FIND-CAPABILITY-001` | NPS-010 §4.2 amended: validity check and grant MUST be one atomic operation | `NPS-010` v1.1.0 |
| `FIND-CAPABILITY-002` | New ADR-0018 (hash-chained append-only audit log); NPS-010 §8 amended to require it | `ADR-0018`, `NPS-010` v1.1.0 |
| `FIND-CAPABILITY-003` | Formalized as an individually-testable requirement | `REQ-IPC-0004` |
| `FIND-CAPABILITY-004` | `CAP-MEDIA-LIBRARY` split into `CAP-MEDIA-IMAGES`, `CAP-MEDIA-VIDEO`, `CAP-MEDIA-AUDIO` | `NPS-011` v1.3.0 |
| `FIND-CAPABILITY-005` | Recorded, not resolved technically — flagged for a future `NPC-008` governance revision | This document only |
| `FIND-CAPABILITY-006` | `CAP-DEBUG-ATTACH` registered (High tier, denied-by-default, debug-manifest-class-only, operator-only ops); debug class user-visible in state surfaces; seccomp relaxation construction-time only; loopback-default debug endpoints; manifest class recorded in the audit chain | `NPS-011` v1.4.0, this document §4.8/§5.5, AG decision log D7 |

All five findings from this phase have a disposition; none are left as a
bare observation with nowhere to go, per NPS-018 §8.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-13 | Initial draft — Phase 3 of the threat model (privilege boundaries and capability escalation) |
| 1.1.0   | 2026-09-23 | D7 addendum: §4.8 analyzes the debug-attach surface (PID-namespace fence, construction-time seccomp gate, authorization fence, attack nodes); §5.5 adds `FIND-CAPABILITY-006` (the debug-class privilege gradient) with the requirements the implementation MUST satisfy. Precondition for the D7 implementation landing |

---
**End of Document**
