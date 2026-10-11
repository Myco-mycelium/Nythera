---
title: Adopt UEFI Secure Boot with User-Enrollable Keys
document_id: ADR-0014
version: 1.1.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-07-13
updated: 2026-10-11
ai_assisted: true
depends_on: [NTM-000, NPC-001, NPS-001]
---

# ADR-0014 — UEFI Secure Boot with User-Enrollable Keys

## Context
NPS-001 §7 left secure boot key management as an open question. Boot
integrity matters directly to NPS-001 §6.2's requirement that Stages 1–4
failures halt boot with a diagnostic rather than continue in a
possibly-compromised state — but the mechanism for establishing "this
kernel image is trusted" was never decided.

## Decision (Accepted 2026-10-11)
Adopt standard **UEFI Secure Boot**, verified by the boot loader (NPS-001
§5, Stage 2) against a certificate chain that supports **user-enrollable
keys** — the same general model used by shim-based Linux distributions:
a small, minimal first-stage loader signed by a widely-trusted CA (for
out-of-the-box compatibility with default-enabled Secure Boot on consumer
hardware), which then verifies the actual Nyrqis boot loader and kernel
against Nyrqis's own key, and additionally supports the user enrolling
their own Machine Owner Key (MOK)-style key for custom or self-built
kernels/backends (relevant given ADR-0012's pluggable NyHAL backends).

- The default installation **MUST** work with Secure Boot enabled at
  defaults on consumer hardware, without requiring the user to first
  disable it, per NTM-000 §4 ("Simplicity" — the common path should not
  demand extra steps).
- A user **MUST** be able to enroll their own key and boot a
  self-built or Experimental Backend (NPS-017 §6) kernel, consistent with
  NPC-001 §10 (User Ownership) — Secure Boot MUST NOT become a vendor
  lock-in mechanism preventing users from running their own software on
  their own hardware.
- Secure Boot **MUST** be user-disable-able entirely for users who
  explicitly choose to accept the tradeoff, per NTM-000 §4
  ("Transparency") — the platform informs, it doesn't lock out.

## Alternatives Considered
- **No Secure Boot support** — rejected; leaves NPS-001 §6.2's boot
  integrity requirement without a concrete mechanism, and would require
  users to manually disable a security feature enabled by default on most
  consumer hardware just to boot at all, creating unnecessary friction.
- **Secure Boot with Nyrqis-only keys, no user enrollment** — rejected;
  directly conflicts with NPC-001 §10 (User Ownership) and would make the
  Experimental Backend (NPS-017 §6) effectively unusable without disabling
  platform security wholesale.
- **A custom, non-UEFI boot verification scheme** — rejected as
  unjustified complexity (NTM-000 §4, "Simplicity"); UEFI Secure Boot is
  the hardware-supported standard and reinventing it would fragment
  compatibility with existing firmware without a clear benefit.

## Consequences
- NPS-001 §5 Stage 2 (Boot Loader) MUST be updated in a future revision to
  reference this specific verification chain (shim-equivalent + Nyrqis
  key + optional user-enrolled MOK) rather than the current generic
  "checksum, and signature where secure boot is enabled" language.
- Key management tooling (enrolling, revoking, rotating keys) becomes a
  required piece of the update system referenced in NPS-004 §4.2's
  snapshot-based rollback design, since a bad key rotation must be
  recoverable the same way a bad kernel update is.
- This ADR does not resolve exactly how NyHAL backend selection (NPS-017)
  interacts with which key verified which backend's kernel — that
  interaction is deferred to a future NPS-001 revision once backend
  implementation begins.

## Revision History

| Version | Date       | Change              |
|---------|------------|----------------------|
| 1.1.0   | 2026-10-11 | **Accepted** (AG decision-log row I3, owner direction via the recorded session, the E1/F1/G1 shape) — as proposed, no text amendments; was never named a blocker (REPOSITORY_STATE: "not benchmark-blocked"); D3's key-trust mirror conforms to its postures; the two Consequences work items become properly-scoped follow-ups (NPS-001 Stage 2 text rides the first UEFI increment; key-management tooling rides Phase 10) |
| 1.0.0   | 2026-07-13 | Initial draft        |

## Status
**Accepted** — AG decision-log row I3 (2026-10-11, owner direction via the
recorded session, the E1/F1/G1 shape). Proposed 2026-07-13; was never named
a benchmark or dependency gate (REPOSITORY_STATE's own record: "not
benchmark-blocked") — it was simply awaiting Group review. The decision is
fully conformant in postures with the adopted ADR-0014 mirror (AG log row
D3, 2026-09-21): bundled platform root set, protected-confirmation
enrollment, revocation via platform list + expiry, cross-signature
rotation — all four mirror postures are exactly what this ADR's Decision
section specifies for the boot trust chain. The two Consequences
paragraph's work items (NPS-001 Stage 2 text update; key-management
tooling) become PROPERLY-SCOPED-FOLLOW-UPS; see below.

**Accepted as proposed, no text amendments:**

- The three Decision bullets are unchanged — they were complete as drafted,
  and each is a user-protection MUST exactly as argued.
- The first consequence (updating NPS-001 §5 Stage 2's boot-path text to
  reference the shim-equivalent + Nyrqis key + optional user-MOK chain) is
  now actionable — that update is expected to ride the first UEFI
  implementation increment (Phase 10 in `PHASES.md`), not to precede it.
- The second consequence (key-management tooling) is likewise actionable;
  the NPS-004 §4.2 snapshot-rollback integration holds, noted in the
  change log (CR-0038) as an implementation-sequencing  dependency.

**Phase 10 note:** accepting this ADR does not by itself change what Phase
10 (UEFI Secure Boot, `PHASES.md`) ships; it unblocks it. Phase 10's boot
path work rides the firmware implementation increments; the boot-path text
amendment and tool landing ride those increments, not this decision.
