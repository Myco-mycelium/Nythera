---
title: Secure Boot Threat Model
document_id: NPS-023
version: 1.0.0
status: Draft
classification: Normative
subsystem: security
owners:
  - Nythera Architecture
created: 2026-07-15
updated: 2026-07-15
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, NPC-009, NPS-018, NPS-019, ADR-0014, NPS-001]
---

# NPS-023 — Secure Boot Threat Model

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It is
**Phase 5** of the threat model, covering `TB-BOOT`. Unlike Phases 3–4,
this is a first full pass, not a deepening — Phase 2 (`NPS-020` §2)
explicitly deferred `TB-BOOT` here rather than giving it even a survey
pass, so there is no prior finding to build on.

As in Phase 4, this analysis is grounded in the real, merged Linux
Backend implementation (`source/nyhal-linux-backend/boot/lifecycle.py`)
where one exists, and the requirement wins over the code where they
diverge — findings are resolved by stating what the system **MUST** do,
not by describing what it currently does as though that settles the
question.

## 2. Scope

This document covers `SURFACE-BOOT-0001` (boot loader signature
verification) and `SURFACE-BOOT-0002` (Secure Boot key enrollment) from
NPS-019 §3, plus one architectural question those two surfaces didn't
anticipate: what "Secure Boot" even means for a NyHAL backend that runs
entirely as userspace code *after* a host operating system has already
booted, rather than owning its own boot chain.

## 3. The Architectural Question: Who Verifies What, on Which Backend

`ADR-0014` was written describing a boot chain — shim, Nythera key,
user-enrolled MOK — that verifies "the Nythera boot loader and kernel."
That framing fits the **NyKernel Backend** (`NPS-001`, where Nythera owns
the whole boot sequence from firmware handoff) directly. It does not fit
the **Linux Backend** the same way: on Linux, UEFI Secure Boot
verification of the boot loader and kernel is performed by the host
Linux distribution's own chain (shim → GRUB → kernel), entirely *before*
any Nythera code runs. The Linux Backend's `BootSequence` class
(`boot/lifecycle.py`) starts at what NPS-001 §5 would call Stage 5
(Service Bring-Up) — hardware/kernel-feature detection, container
manager initialization, capability manager initialization — not Stages
1–4, which have already happened by the time Python starts executing.

This is architecturally fine — it's a natural consequence of `ADR-0012`'s
whole premise, that a backend provides native mechanisms for what NyCore
requires rather than reimplementing them. But it produces a real gap:
**nothing checks or reports whether the host's own Secure Boot chain was
actually engaged.** A user could be running Nythera's Linux Backend on a
system with Secure Boot disabled, or on a system where it was bypassed,
and receive no indication of that from Nythera at all.

## 4. Findings

### `FIND-BOOT-001` — No Secure Boot Status Visibility on the Linux Backend

**What was checked:** `boot/lifecycle.py` for any reference to Secure
Boot status, EFI variables, or `mokutil`-equivalent checks.

**What was found:** none. Zero awareness of whether the underlying
system's Secure Boot chain is engaged.

**Why this matters:** NTM-000 §4 ("Transparency") and NPC-001 §9.1
require the platform explain its security posture to the user, not just
enforce it silently where it happens to be enforced. A user has no way to
learn, from Nythera itself, that they're running without the boot
integrity guarantee `ADR-0014` describes as the platform's default
posture.

**Severity: Medium / High** (the check itself is trivial — Linux exposes
Secure Boot state via `/sys/firmware/efi/efivars` — but the gap is
certain to exist in every current install, not merely possible).

**Resolution:** `NPS-017` §4.5 (Boot and Lifecycle) amended (§6 below) to
require a conformant backend report Secure Boot engagement status as part
of reaching a usable session, so absence of the guarantee is visible
rather than silent. New `REQ-BOOT-0004`.

### `FIND-BOOT-002` — Boot Phase Transitions Are Not Order-Enforced at the API Level

**What was checked:** whether `BootSequence.transition_to_phase()` — a
public method — validates that the requested phase is a legal next step
from the current one.

**What was found:** it does not. It accepts any `BootPhase` value,
records it, and fires callbacks. The primary `boot()` entry point does
call phases in the correct order and short-circuits on failure, so the
*intended* path is safe — but any other code with a reference to a
`BootSequence` instance (a future service, a test harness, a bug) could
call `transition_to_phase()` directly and skip stages, and nothing would
object.

**Severity: Low / Medium.** Not currently exploitable through the normal
path, but it's exactly the kind of gap that becomes a real problem once
more code exists that holds a reference to this object — worth closing
while it's cheap, not after something depends on the unsafe behavior.

**Resolution:** `NPS-001` §5 amended (§6 below) to require that phase
transitions be validated against the legal sequence at the API level, not
merely produced correctly by one call path.

### `FIND-BOOT-003` — No Measured Boot / Attestation Story

**What was checked:** whether `ADR-0014` or any other document addresses
anti-rollback protection or remote attestation (proving to a third party,
e.g. an enterprise management system, that a specific known-good boot
chain executed) via TPM measured boot.

**What was found:** neither `ADR-0014` nor any NPS document mentions TPM,
measured boot, or attestation. Secure Boot as specified verifies the boot
chain is *signed*; it says nothing about proving *which* signed version
ran, which matters for rollback-attack resistance and any future
enterprise/managed-device scenario.

**Severity: Low** (no current use case depends on it) **but not fixable
by a quick amendment** — like `FIND-PACKAGE-001` in Phase 2, this needs
real design work, not a one-line requirement.

**Resolution:** not resolved here. Logged as a tracked gap in
`REPOSITORY_STATE.md`, deferred until a concrete need (e.g. enterprise
deployment support) justifies the design cost, consistent with how
`FIND-PACKAGE-001` was handled.

### `FIND-BOOT-004` — MOK Enrollment's Physical-Presence Authentication (Confirmed, Accepted)

**What was checked:** how `ADR-0014`'s user-enrollable key mechanism
authenticates an enrollment request.

**What was found:** the shim/MOK ecosystem `ADR-0014` builds on
authenticates key enrollment via physical presence at a one-time
interactive boot-time prompt — not a password, not a signature, just
"someone is standing at the machine during this reboot." This is a
well-understood, inherent property of the shim/MOK design used broadly
across Linux distributions, not a Nythera-specific weakness.

**Severity: N/A — accepted, not a new risk.** This matches the "Physical
attacker" profile in NPS-018 §5 directly: physical access during an
enrollment window is already assumed to grant significant trust in this
model. No action beyond recording that this was checked and matches
expectations, so it isn't mistaken for an oversight later.

## 5. What This Phase Deliberately Does Not Cover

This document does not specify the **NyKernel Backend's** actual Secure
Boot implementation (the shim-equivalent chain `ADR-0014` describes) —
that backend doesn't exist yet (`NPS-017` §6), so there's no code to
ground an analysis in, and writing one now would repeat Phase 1's mistake
of analyzing a hypothetical. When NyKernel Backend implementation begins,
its boot chain needs its own pass through this same method, not an
assumption that the Linux Backend's findings transfer.

## 6. Specification Amendments

**NPS-017** (NyHAL Backend Contract) — §4.5 amended to require Secure
Boot status reporting, closing `FIND-BOOT-001`.

**NPS-001** (Kernel Architecture and Boot) — §5 amended to require
phase-transition order validation at the API level, closing
`FIND-BOOT-002`.

Both applied directly (see their own revision history), per NPS-018 §8.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-15 | Initial draft — Phase 5 of the threat model (secure boot), first full pass on TB-BOOT since Phase 2 deferred it without analysis |

---
**End of Document**
