---
title: Nyrqis Package Format (.nypkg)
document_id: NPS-026
version: 1.0.0
status: Draft
classification: Normative
subsystem: storage
owners:
  - Nyrqis Architecture
created: 2026-08-12
updated: 2026-08-12
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
without a single monopoly key.

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

## 13. Open Questions *(Informative)*

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

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-08-12 | Initial draft — package structure, signed manifests, integrity trees, deltas, streaming install, rollback, dependencies; closing Milestone 11 gap category 7 and threat-model finding FIND-PACKAGE-001 |

---
**End of Document**
