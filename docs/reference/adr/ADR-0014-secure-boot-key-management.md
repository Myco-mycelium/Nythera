---
title: Adopt UEFI Secure Boot with User-Enrollable Keys
document_id: ADR-0014
version: 1.0.0
status: Proposed
owners: [Nythera Architecture]
created: 2026-07-13
updated: 2026-07-13
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

## Decision (Proposed)
Adopt standard **UEFI Secure Boot**, verified by the boot loader (NPS-001
§5, Stage 2) against a certificate chain that supports **user-enrollable
keys** — the same general model used by shim-based Linux distributions:
a small, minimal first-stage loader signed by a widely-trusted CA (for
out-of-the-box compatibility with default-enabled Secure Boot on consumer
hardware), which then verifies the actual Nythera boot loader and kernel
against Nythera's own key, and additionally supports the user enrolling
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
- **Secure Boot with Nythera-only keys, no user enrollment** — rejected;
  directly conflicts with NPC-001 §10 (User Ownership) and would make the
  Experimental Backend (NPS-017 §6) effectively unusable without disabling
  platform security wholesale.
- **A custom, non-UEFI boot verification scheme** — rejected as
  unjustified complexity (NTM-000 §4, "Simplicity"); UEFI Secure Boot is
  the hardware-supported standard and reinventing it would fragment
  compatibility with existing firmware without a clear benefit.

## Consequences
- NPS-001 §5 Stage 2 (Boot Loader) MUST be updated in a future revision to
  reference this specific verification chain (shim-equivalent + Nythera
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

## Status
Proposed — pending Architecture Group review.
