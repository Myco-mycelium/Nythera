---
title: AG Pre-read — Publisher Key Trust (NPS-026 §6.3, REQ-SEC-0004)
document_id: AG-BRIEF-2026-09-PK
version: 1.0.0
status: Pre-read — input to NPS-026's review, not a standing item of its own
owners: [Nyrqis Architecture]
created: 2026-09-20
ai_assisted: true
depends_on: [NPS-026, NPS-027, ADR-0014, AG-AGENDA-2026-09, NPC-001]
---

# AG Pre-read — Publisher Key Trust: the NPS-026 §6.3 Design Decision

**What this document is:** an AI-drafted pre-read for the design
decision that `NPS-027` (`FIND-PACKAGE-003`, `REQ-SEC-0004`) routed to
NPS-026's review: how a publisher's public key first becomes trusted,
and how it is revoked or rotated. Standing item D2's brief
(`AG_BRIEF_NPS027_PACKAGE_TRUST.md`) explicitly fenced this off; this
document prepares it so the decision does not block NPS-026's path to
`Accepted`.

**What this document is not:** a decision, and not a crypto-scheme
proposal. Per NPC-001 §11.1 the recommendation is overridable Group
judgment; per NPC-002 §6.2 (and NPS-026's own status note) the concrete
cryptographic scheme needs dedicated human review — this brief designs
the *trust model*, not the algorithms.

---

## The decision required

`NPS-026` §6.3 says trust anchors **SHOULD** follow ADR-0014's boot
model — a platform trust anchor plus user-enrollable keys — but names
only the pattern. Nothing specifies: how the platform anchor reaches a
system, what a user is shown before trusting a new publisher key, how a
compromised or expired key is revoked, or whether revocation reaches
already-installed packages. `FIND-PACKAGE-003` (Medium/High) records
that a key delivered alongside the package it signs — silent
trust-on-first-use — provides no authenticity against a network
attacker and defeats the mechanism.

## What ADR-0014's boot model actually establishes (the mirror source)

The boot side ships: platform keys anchored in firmware (the
shim-equivalent chain), a user-enrollable key database (MOK-style), and
revocation via a platform-maintained forbidden-keys db (`dbx`) updated
out-of-band. The package mirror therefore has a precedent for every
sub-decision below — the design question is which parts carry over and
where packages legitimately differ (publishers are many and dynamic;
boot keys are few and stable).

## The design axes (the actual sub-decisions)

1. **Initial trust distribution.** (a) A platform root set bundled in
   the OS image (the firmware-keys analogue — anchors arrive with the
   system, not the download); (b) out-of-band from the publisher's
   site for new publishers; (c) TOFU. The finding already rejects
   (c)-alone; (b)-alone has no anchor; the coherent shape is (a) as the
   default trust base plus (b) with *explicit user enrollment* for
   publishers outside it.
2. **Enrollment UX.** What the user confirms before a key becomes
   trusted: publisher identity, key fingerprint, the enrollment source
   — with a protected confirmation step, mirroring the
   suggest-vs-act UI pattern NPS-015 §5.5 established for a different
   boundary. Enrollment is a per-system user decision, never silent.
3. **Revocation semantics — the real policy judgment.** Three
   mechanisms compose: a platform-pushed revocation list (the `dbx`
   analogue), expiry dates on publisher keys, and the manifest itself
   (a revoked key stops verifying for *new* installs and *updates*
   immediately). The hard question the finding poses is propagation to
   **already-installed** packages: hard-blocking at launch gives the
   platform a kill switch over installed software (conflicts with
   ownership expectations); advisory-only leaves users running
   compromised-publisher software unknowingly. A two-step middle —
   advisory notice at launch, hard block only at install/update/verify
   touchpoints — keeps user agency while making the compromised state
   visible and non-propagating.
4. **Rotation.** Publishers rotate by cross-signing the new key with
   the old (chain of continuity) or by fresh user enrollment; the
   former avoids a support cliff for installed-base updates.

## Options and the brief's recommendation

| Option | Shape | Brief's recommendation |
|---|---|---|
| **A. The ADR-0014 mirror** | Bundled platform root set; explicit user enrollment for new publishers (protected confirmation); revocation via platform list + expiry; two-step propagation (advisory at launch, block at install/update); cross-signature rotation | **Recommended.** Parity with the boot model means one mental model and reusable mechanisms; every element of FIND-PACKAGE-003's demand (informed, user-controlled trust; defined revocation) is met; the two-step propagation preserves ownership expectations while making compromise non-silent. |
| **B. Pure TOFU with fingerprint confirmation** | Web-style first-use trust, user confirms fingerprint | Rejected as the default: the finding itself shows it provides no authenticity against a network attacker. Could exist later as an explicit opt-in mode for advanced users — not the trust base. |
| **C. Single platform CA signs everything** | One centralized PKI | Rejected: §6.3 explicitly avoids a monopoly key; kills self-built and third-party-store publishers; concentrates the attacker's target. |

**Recommendation: Option A**, with the Group's judgment exercised
explicitly on sub-decision 3 (the advisory/block propagation split) —
that is the one place where this is policy, not mechanism.

## Consequences ledger — what Option A mechanically triggers

| When | Tree edits |
|---|---|
| NPS-026 amendment (its path to Accepted) | §6.3 expanded into the mechanism (root-set distribution, enrollment flow, revocation list + expiry, propagation semantics, rotation); `REQ-SEC-0004` closed; NPS-027's FIND-PACKAGE-003 disposition note updated to point at the design |
| Implementation era | Key store + verification states in the future package manager; revocation-list distribution channel; enrollment UI per the protected-confirmation pattern; hardware-root convergence question (NPS-026 §14, 2026-09-18) may fold the package root set into the boot anchor |

## What this pre-read deliberately does not answer

1. **Concrete cryptographic scheme and algorithms** — signature
   format, key lengths, hash choices: NPC-002 §6.2 reserves that for
   dedicated human review, and NPS-026's status line already records
   the deferral.
2. **Implementation sequencing** — no package manager exists; when the
   key store and revocation channel get built is a roadmap question.
3. **The hardware-root convergence question** (NPS-026 §14) — whether
   the package trust anchor and the boot anchor share one hardware root
   is its own decision, flagged there and left there.

---

**End of Document**
