---
title: Package Trust Model
document_id: NPS-027
version: 1.0.0
status: Draft
classification: Normative
subsystem: security
owners:
  - Nyrqis Architecture
created: 2026-08-12
updated: 2026-08-12
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, NPC-009, NPS-018, NPS-019, NPS-006, NPS-026, ADR-0014, ADR-0018]
---

# NPS-027 — Package Trust Model

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It is
**Phase 7** of the threat model, covering `TB-PACKAGE` (package
publisher/source ↔ installed, running software; NPS-018 §4). It deepens
the survey-level treatment `TB-PACKAGE` received in Phase 2
(`FIND-PACKAGE-001`, NPS-020 §8) and, per the phase plan in
`docs/reference/security/README.md`, **extends NPS-006**.

No installer or package manager exists yet in this repository (the
`NPS-026` package format is itself `Draft`), so like Phases 3 and 6 this
analysis works from the specified guarantees of `NPS-006` (`.nygi` image
format) and `NPS-026` (package format) rather than from real code.

## 2. Scope

This document covers `SURFACE-FS-0001` (`.nygi` image manifest and content
parsing at mount time; NPS-019 §3) and — at the signature-verification
boundary only — `SURFACE-AND-0001` (APK parsing and signature
verification, NPS-019 §3), examined against `TB-PACKAGE`.

The attacker profile assumed is the **Malicious package publisher**
(NPS-018 §5): can produce a `.nygi` image or APK that a user might choose
to install, and has no capability grant yet. The **Unprivileged local
application** profile (NPS-018 §5) is additionally assumed for
`FIND-PACKAGE-004` (overlay content), since a user-installed mod is
ordinary container-scoped content, not a publisher artifact.

## 3. Findings

### `FIND-PACKAGE-001` — Checksums Don't Establish Authenticity (Phase 2 finding, disposition)

**TB-PACKAGE · STRIDE: Spoofing / Tampering** (originally recorded in NPS-020 §8).

**What was checked:** whether the gap recorded in NPS-020 §8 (`FIND-PACKAGE-001`,
**High / High** — a `.nygi` manifest with checksums alone lets an attacker
tamper with an image and simply recompute a valid checksum) has a
spec-level response anywhere in the current document set.

**What was found:** it now does — `NPS-026` §6 (Draft) requires every
package **MUST** be signed, with the signature covering the manifest and
the content images' integrity trees, so tampering invalidates the
signature rather than being masked by a recomputed checksum. This is the
response the finding called for.

**Disposition:** the finding stands until `NPS-026` exits `Draft` and the
`.nygi` verification path actually requires signatures. The remaining
gaps this phase identifies are the ones between that Draft response and
the rest of the system — see `FIND-PACKAGE-002` through `FIND-PACKAGE-005`.

### `FIND-PACKAGE-002` — The Governing (Accepted) Spec Still Requires Only Checksums

**TB-PACKAGE · STRIDE: Tampering / Spoofing.**

**What was checked:** whether `NPS-006` — the `Accepted`, normatively
binding spec for `.nygi` images, which governs `SURFACE-FS-0001` at mount
time — has been reconciled with `NPS-026`'s signature requirement.

**What was found:** it has not. `NPS-006` §3.1 defines the image header as
carrying a "content checksum" and §6 requires only checksum-based
integrity verification; it does not cite `NPS-026` or require signatures,
and its `depends_on` does not include it. The authenticity requirement
lives only in a `Draft` document. As things stand, a conformant
implementation of the *governing* spec (`NPS-006`) could still verify
images checksum-only, and if `NPS-026` never exits `Draft`, that is the
only normative verification path that exists.

**Why this matters:** threat-model findings must flow back into a real
specification revision (NPS-018 §8); a Draft response that the Accepted
spec doesn't reference is not yet a closure.

**Severity: High / Medium** (Impact High — the trust property at stake is
authenticity of all installed software; Likelihood Medium — requires
`NPS-026` to stall or be ignored, but nothing currently forces otherwise).

**Resolution:** `NPS-006` §6 amended (§4 below) to require authenticity
verification through `NPS-026`'s signature path with an explicit status
note, mirroring the `NPS-010` §9 precedent for normatively referencing a
not-yet-accepted artifact. New `REQ-SEC-0003`.

### `FIND-PACKAGE-003` — No Publisher Key-Distribution or Revocation Story

**TB-PACKAGE · STRIDE: Spoofing.**

**What was checked:** how a publisher's public key first becomes trusted,
and how it is revoked or rotated, per `NPS-026` §6.3 ("a platform trust
anchor plus user-enrollable keys, mirroring the pattern chosen for boot
in ADR-0014").

**What was found:** the *pattern* is named but the *mechanism* is not.
Nothing specifies: whether keys ship in a bundled platform root set,
arrive out-of-band from the publisher's site, or are enrolled by the user
at first install; what the user is shown before trusting a key; or how a
compromised/expired publisher key is revoked and whether revocation
propagates to already-installed packages. These choices have very
different security properties — e.g. silent trust-on-first-use of a key
delivered alongside the package it signs provides no authenticity
against a network attacker intercepting the download, defeating the
entire mechanism.

**Why this matters:** the `Malicious package publisher` profile is only
meaningful if the trust decision at install time is actually
user-controlled and informed.

**Severity: Medium / High** (Likelihood High — first-install trust is on
the critical path of every package; Impact Medium — affects one trust
boundary).

**Resolution:** recorded as `REQ-SEC-0004`; `NPS-026` §6 to be extended on
its path to `Accepted` with an explicit enrollment/revocation design. This
phase does not invent the design unilaterally — it fixes the requirement
and flags the decision for Architecture Group review alongside `NPS-026`.

### `FIND-PACKAGE-004` — No Verification Story for the Overlay

**TB-PACKAGE · STRIDE: Tampering.**

**What was checked:** how `NPS-006` §6 verification interacts with the
writable copy-on-write overlay (`NPS-006` §4) — the layer that holds
saves, config, installer-written data, and user-installed mods.

**What was found:** `NPS-006` §6 verifies the read-only base image; the
overlay is never mentioned in the verification section. In the mounted
view (base + overlay merged, `NPS-006` §4.1–§4.2) a file the user sees
can come from either layer, with different provenance and different trust
value: base content is signed (once `REQ-SEC-0003` lands), overlay content
is user-modified and unsigned. Nothing defines how the system
distinguishes them when reporting integrity, and nothing constrains what
an unsigned mod may shadow (a mod replacing a signed base executable is
indistinguishable, in the current spec, from the game's own updated
assets — with direct anti-cheat and consistency implications for
multiplayer titles, which `NPS-006` §8 already acknowledges matter).

**Why this matters:** the `.nygi` model's whole point is read-only,
verifiable base content; if the verification story ends at the base layer
while the user's runtime view is the overlay, the guarantee is incomplete
at exactly the seam where mods (the `Unprivileged local application`
profile) meet publisher content.

**Severity: Medium / Medium.**

**Resolution:** `NPS-006` §6 amended (§4 below) to require verification
to distinguish base-image from overlay provenance and to constrain which
content classes an overlay may shadow. New `REQ-SEC-0005`.

### `FIND-PACKAGE-005` — No Audit Trail for Package Events

**TB-PACKAGE · STRIDE: Repudiation.**

**What was checked:** whether install, uninstall, or verification events
are required to be recorded anywhere — the `NPS-006` lifecycle (§5, §7)
and `NPS-026` install flow (§7) both describe the operations but neither
requires logging them.

**What was found:** they aren't. `NPS-010` §8 / `ADR-0018` require
capability events, and `NPS-015` §5.5 (from `FIND-AI-002`) extends the
same mechanism to AI suggestions; nothing extends it to packages. After
the fact, there is no answer to "what software is installed, from which
publisher, at which version, and did it verify" — a repudiation and
forensics gap.

**Severity: Low / Medium.**

**Resolution:** `REQ-SEC-0006` records the requirement, reusing the
`ADR-0018` mechanism (not a new logging design), consistent with the
precedent set by `FIND-AI-002`.

## 4. Specification Amendments

**NPS-006** (Nyrqis Game/Application Image Format) — amended in the same
document (see its revision history), per NPS-018 §8:

- §6 gains a requirement that `.nygi` verification **MUST** include
  signature-based authenticity verification per `NPS-026` §6, with an
  explicit status note that `NPS-026` is `Draft` (closing
  `FIND-PACKAGE-002`);
- §6 gains a requirement that verification **MUST** distinguish
  base-image content from overlay content and constrain what an overlay
  may shadow (closing `FIND-PACKAGE-004`);
- §6 gains a requirement that verification/install/uninstall events be
  recorded in the `ADR-0018` audit log (closing `FIND-PACKAGE-005`).

**NPS-026** (Package Format, `Draft`) — flagged, not amended here: its §6
must gain the publisher key enrollment/revocation design on its path to
`Accepted` (per `FIND-PACKAGE-003`, `REQ-SEC-0004`). The phase records the
requirement; the design decision belongs with `NPS-026`'s own review.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-08-12 | Initial draft — Phase 7 of the threat model (Package Trust), deepening TB-PACKAGE; 4 new findings plus disposition of FIND-PACKAGE-001; NPS-006 §6 amended |

---
**End of Document**
