---
title: Nyrqis Package Format (.nypkg)
document_id: NPS-026
version: 1.2.0
status: Draft
classification: Normative
subsystem: storage
owners:
  - Nyrqis Architecture
created: 2026-08-12
updated: 2026-09-21
ai_assisted: true
review_cycle: Continuous
depends_on: [NTM-000, NPC-001, ADR-0004, NPS-004, NPS-005, NPS-006, NPS-010]
---

# NPS-026 — Nyrqis Package Format (.nypkg)

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It is
the "package-format NPS" explicitly deferred by NPS-006 §2 (installer /
package-manager workflow) and NPS-006 §9 (exact manifest format), and the
direct response to threat-model finding `FIND-PACKAGE-001` (NPS-020 §8):
`.nygi` integrity currently relies on checksums alone, which do not
establish publisher authenticity — an attacker can tamper with an image
and simply recompute a valid checksum.

It is a `Draft`: the *model* below is proposed; exact manifest
serialization and the concrete signing scheme require implementation
validation before `Accepted` (NPC-002 §5.1/§5.2). Closing Milestone 11
gap category 7 (package format specification).

On 2026-09-21 the Architecture Group decided the publisher key-trust
mechanism (decision log D3, `AG_AGENDA.md`) and its §6.3 text below was
reviewed and landed in the same sitting — the last open *design* this
document was waiting on. What remains before `Accepted` is the NPC-002
§6.2 reserved concrete crypto review and implementation validation.
The implementation surface for that machinery — key store, verification
pipeline, revocation channel, enrollment flow, audit trail — is drafted
as [`NPS-028`](../security/NPS-028-package-pki-implementation-surface.md).

## 2. Scope

This specification covers the `.nypkg` installable unit: its structure,
the manifest it carries, publisher signatures, integrity verification,
compression, delta updates, streaming install, rollback, and dependency
resolution. It does not cover the runtime container lifecycle (NPS-010) —
a package *requests* capabilities in its manifest; it does not grant
them — nor anti-cheat compatibility (NPS-006 §8's known limitation).

## 3. Definitions

- **Package (.nypkg)** — the signed, installable unit distributed by a
  publisher and processed by the installer.
- **Package Manifest** — the declarative content of a package: identity,
  requested capability set, resource limits, dependencies, and the images
  it installs.
- **Publisher** — the entity that signs the package; the trust anchor the
  user (or their configured policy) accepts for installs.

## 4. Package Structure

A `.nypkg` **MUST** consist of:

- A **package manifest** (identity, requested capabilities and resource
  limits per NPS-010 §4.2, runtime class, dependencies, images).
- One or more **content images** in the `.nygi` format (NPS-006 §3),
  produced at install time per NPS-006 §9's deferral.
- A **signature block** (§6) covering the manifest and the content
  images' integrity trees (§7).
- **Update metadata** (§8) for delta updates and rollback.

## 5. Package Manifest

5.1. The manifest **MUST** carry: application identity (id, name,
publisher), version, runtime class (native / windows-compat /
android-compat, per NPS-007/008), the requested capability set (valid
`CAP-*` entries from NPS-011 — a manifest requesting an undefined
capability **MUST** be rejected, NPC-001 §9.3, NPS-010 §4.2), requested
resource limits (NPS-010 §7), and dependencies (§10).

5.2. The manifest **MUST NOT** be able to grant anything by itself — it
is a request evaluated atomically at install/launch (NPS-010 §4.2). The
user-facing permission prompt (NPS-011 §4.2) is driven by the manifest's
capability requests.

## 6. Digital Signatures (the FIND-PACKAGE-001 response)

6.1. Every package **MUST** be signed by its publisher. A package whose
signature fails verification **MUST NOT** be installed, and the failure
**MUST** be reported to the user (mirroring NPS-006 §6.2's no-silent-
failure rule for integrity).

6.2. The signature **MUST** cover the manifest and the integrity trees of
all content images, so that no file can be swapped, altered, or replaced
without invalidating the signature — closing the "recompute a valid
checksum" hole in `FIND-PACKAGE-001`.

6.3. Trust anchors **SHOULD** follow the key-management model established
for boot in ADR-0014: a platform trust anchor plus user-enrollable
keys, so self-built packages and third-party stores remain possible
without a single monopoly key. The mechanism, decided by the
Architecture Group on 2026-09-21 (`AG_AGENDA.md` decision log D3,
pre-read `AG_BRIEF_NPS026_KEY_TRUST.md`):

6.3.1. Initial trust distribution. The system **MUST** ship with a
platform root set of publisher trust anchors as part of the OS image
(ADR-0014's firmware-anchor analogue). A key **MUST NOT** be trusted
merely because it arrived alongside a package that references it —
trust-on-first-use is rejected: it provides no authenticity against a
network attacker intercepting the download (`FIND-PACKAGE-003`).

6.3.2. Enrollment. A publisher key outside the platform root set
**MUST NOT** verify packages until the user has explicitly enrolled
it, through a protected confirmation (the NPS-015 §5.5 pattern)
displaying the publisher identity, the key fingerprint, and the
enrollment source. The confirmation **MUST NOT** be skippable by the
enrolling party.

6.3.3. Revocation inputs. Publisher keys **MUST** carry an expiry
date. The platform **MUST** distribute a revocation list out-of-band
(the ADR-0014 ``dbx`` analogue). A revoked or expired key **MUST**
fail verification for new installs, updates, and every
package-verification touchpoint immediately upon list delivery or
expiry.

6.3.4. Propagation to installed packages. Revocation **MUST NOT**
silently block launching already-installed, previously-verified
content: the system **MUST** surface an advisory notice at launch for
affected packages and **MUST** block hard at install, update, and
re-verification touchpoints. The advisory/block split preserves user
agency over owned software while making a compromised-publisher state
non-silent and non-propagating.

6.3.5. Rotation. A publisher **MAY** rotate keys by cross-signing the
new key with the previously trusted one, preserving continuity for
already-installed content; a rotation that cannot cross-sign requires
fresh user enrollment per 6.3.2.

6.3.6. Conformance scope. The signature scheme, algorithms, and
key parameters for all of the above are out of scope of this section —
reserved per NPC-002 §6.2 for dedicated human review.

6.4. A package **MAY** be updated only by a publisher able to produce a
valid signature for the update (see §8); update and original signatures
are verified through the same path.

## 7. Integrity Tree

7.1. Each content image **MUST** carry a hash tree (Merkle) over its
files, extending NPS-006 §6.1's verification guarantee: Nyrqis **MUST**
be able to verify an image against its integrity tree without fully
decompressing it.

7.2. The integrity tree is the object of both the checksum-based
integrity check (NPS-006 §6) and the signature check (§6) — one
structure, two verifications with different guarantees (authenticity vs.
bit-rot).

## 8. Compression

Content compression follows NPS-005 §5 in full: the codec (Zstd default,
ADR-0007) and the explicit per-region override are specified there, not
duplicated here (NPS-018 §8's no-duplication rule).

## 9. Delta Updates

9.1. Updates **MUST** be installable as deltas against the previously
installed version where the publisher supplies one; a delta **MUST**
verify as a first-class package (§6, §7) before any bytes replace
installed content.

9.2. A failed or interrupted update **MUST** leave the previous known-good
version intact and runnable (rollback, NPS-001 §6.3's known-good
retention, formalized here).

## 10. Dependency Resolution

10.1. Packages **MUST** declare their dependencies (other packages and
their version ranges) in the manifest (§5.1). The installer **MUST**
resolve the dependency graph before install and **MUST** refuse a graph
that is unsatisfiable or contains a cycle.

10.2. Dependencies **MUST** be installed with their own manifests
evaluated independently — a dependency never inherits a dependent's
capability requests (each container's grants are its own, NPS-010 §5).

## 11. Streaming Install

11.1. Installation **SHOULD** support streaming: verifying and writing
content as it arrives, rather than requiring the whole package in memory
or on disk first, using the integrity tree (§7) to validate each
streamed chunk.

## 12. Uninstall

Follows NPS-006 §7: uninstall removes the base images; the overlay
(saves, mods) **MUST** be retained by default and offered for deletion as
a separate, explicit user choice (NPC-001 §10).

## 13. Relationship to NyVault Volumes (implementation findings, 2026-09-18)

ADR-0022/0023 shipped the NyVault storage service (daemon-hosted
volumes on the IPC transport, envelope-encrypted at the block layer).
The package format interacts with it at four points; this section
records the interactions without duplicating the ADRs (NPS-018 §8):

13.1. **A vault volume is a NyFS filesystem image** — the same CoW,
checksum, compression, and journal-commit machinery a package's
content images use (ADR-0022 §"Decision"). Packages therefore install
INTO volumes coherently: the content-image semantics of §4 and the
integrity tree of §7 apply unchanged inside a volume.

13.2. **Integrity trees are computed at package-build time over
PLAINTEXT content; at-rest protection inside an encrypted volume is
the volume's AEAD block layer** (ADR-0023: every block at rest is
`nonce ‖ ciphertext ‖ tag`, checksum over ciphertext). The two
mechanisms compose without re-work: the signature (§6) and tree (§7)
verify the publisher's bytes BEFORE encryption; the AEAD tag protects
the encrypted-at-rest form; the tree never needs re-encryption when a
volume is re-keyed (ADR-0023's rotation re-wraps DEKs only).

13.3. **Streaming install (§11) into a vault volume rides the
storage-service data plane** (ADR-0022/0024). Two cost layers are
measured, and both have since been addressed — the numbers below are
the historical baselines, cited because they name the mechanisms:
the 32 KiB per-CALL paging cost (BENCHMARK_RESULTS §27, pre-batching:
~0.28 MB/s encrypted writes, the durable per-CALL commit dominating)
was addressed by write-commit batching (0.14.8/0.14.9) and then by
the streaming data plane (ADR-0024, 0.14.20/0.14.21): streamed 1 MiB
writes are 5.6× faster plaintext / 6.6× encrypted than paged (§29);
reads were already AEAD-decode-bound and barely move (~1.02–1.08×).
A streaming installer targeting a vault SHOULD use the streaming
data plane (chunk envelope or wire-level STREAM_CHUNK) rather than
raw paging; the residual costs are the AEAD block layer and the
single-datagram read path, not the install protocol.

13.4. **Uninstall (§12) maps onto volume lifecycle**: removing a
package removes its content images; an application's DATA volumes are
creator-scoped assets of the container (ADR-0022 — grants never imply
the capability, admin ops are creator/operator-only) and follow §12's
retain-by-default rule unchanged.

## 14. Open Questions *(Informative)*

## 14. Open Questions *(Informative)*

- Exact manifest serialization (binary vs. structured text) — the item
  NPS-006 §9 deferred — remains open here too; the tutorial
  [Authoring Your First Container Manifest](../../tutorials/authoring-your-first-manifest.md)
  uses a field-name vocabulary consistent with this document pending that
  decision.
- The concrete signature scheme (algorithm, key sizes, certificate
  format) will be proposed with implementation, per NPC-002 §6.2's rule
  that security-critical crypto design receives dedicated human expert
  review before being treated as settled.
- Cross-package asset deduplication (NPS-006 §9) may interact with the
  integrity tree and is deferred.
- **One hardware root for both trust anchors (added 2026-09-18):** the
  §6 package-signing design and the vault KEK custody (ADR-0023) will
  both want a hardware root of trust — ADR-0023 already names TPM2 /
  PKCS#11 as pluggable backends behind a Rust trait. When the signing
  scheme is designed, the two SHOULD converge on the same hardware-
  key surface rather than growing separate token stacks. (The §6.3
  trust-model decision of 2026-09-21 does not prejudge this — whether
  the package root set shares the boot anchor's hardware root stays
  open here.)
- **Manifest vocabulary vs the vault registry (added 2026-09-18):**
  ADR-0023 persists the volume registry + wrapped DEKs across daemon
  restarts; if manifests settle on a structured-text serialization,
  the registry's persisted format SHOULD follow the same vocabulary
  so install/restore tooling reads one syntax.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-08-12 | Initial draft — package structure, signed manifests, integrity trees, deltas, streaming install, rollback, dependencies; closing Milestone 11 gap category 7 and threat-model finding FIND-PACKAGE-001 |
| 1.1.0   | 2026-09-18 | §13 (new): implementation findings from ADR-0022/0023 (NyVault) — volumes are NyFS images, integrity trees cover plaintext while vault AEAD covers at-rest (composition without re-encryption), streaming install into vaults inherits 32 KiB CALL paging and is commit-bound until write batching, uninstall maps onto creator-scoped volume lifecycle; §14: hardware-root convergence and registry-vocabulary open questions added. Closes the M14 Phase 1 "package format update" item |
| 1.2.0   | 2026-09-21 | §6.3 expanded from the ADR-0014 pattern into the decided mechanism (AG decision log D3): 6.3.1 bundled platform root set + TOFU rejection, 6.3.2 protected-confirmation enrollment, 6.3.3 revocation inputs (expiry + out-of-band list), 6.3.4 advisory/block propagation split, 6.3.5 cross-signature rotation, 6.3.6 crypto reserved per NPC-002 §6.2; the 2026-09-20 non-normative pointer note replaced by the decision record. Closes REQ-SEC-0004 |

---
**End of Document**
