---
title: Nythera Game/Application Image Format (.nygi) and Overlay
document_id: NPS-006
version: 1.0.0
status: Draft
classification: Normative
subsystem: storage
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
review_cycle: As needed
depends_on: [NTM-000, NPC-001, ADR-0003, ADR-0007, NPS-004, NPS-005]
---

# NPS-006 — Nythera Game/Application Image Format (.nygi) and Overlay

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
implements the game-disk-image decision recorded in ADR-0003, defining the
`.nygi` image format and its writable overlay.

## 2. Scope

This specification covers the structure of a `.nygi` image, the mount/unmount
lifecycle, and the read-only-image-plus-writable-overlay model. It does not
cover the installer/package-manager workflow that produces a `.nygi` (a
future package-format NPS) or anti-cheat compatibility, which is called out
as an explicit known limitation rather than solved here.

## 3. Image Structure

3.1. A `.nygi` image **MUST** consist of:

- A **header** identifying format version, compression codec (ADR-0007),
  and content checksum.
- A **read-only content layer** containing the installed application/game's
  files, compressed per NPS-005 §5.1.
- A **manifest** listing all files, their checksums (per NyFS §4.3
  guarantees, extended to the image format), and metadata needed for
  verification.

3.2. The read-only content layer **MUST NOT** be modifiable after image
creation; any change requires producing a new image (e.g. on update),
consistent with the "reduced cheating by making assets read-only" goal from
the original design discussion.

## 4. Overlay

4.1. Every mounted `.nygi` image **MUST** be paired with a writable
copy-on-write overlay, stored on NyFS per NPS-004, using the compression
defaults in NPS-005 §5.2.

4.2. Writes an application makes into its own install directory (saves,
config files, installer-written data, mods) **MUST** be transparently
redirected into the overlay; the application **MUST NOT** need to be aware
that its "install directory" is actually a read-only image plus overlay.

4.3. The overlay **MUST** persist independently of the mounted state of the
base image — deleting/re-verifying the base image **MUST NOT** delete the
overlay, so that save data survives a game update or reinstall, per NPC-001
§10 (User Ownership).

4.4. Overlay data **MUST** be included in user backups by default, per the
"easier backup" goal from the original design discussion; the base image
**MAY** be excluded from backups by default since it is reproducible from
the original install source, provided the user is informed of this default
and can override it.

## 5. Mount Lifecycle

Per the original design's four-step model, a `.nygi` image **MUST** follow:

1. **Mount** — the image is attached read-only; its overlay is attached
   read-write; both are combined into a single logical view for the
   launched process's container (per NPS-002 §4).
2. **Decompress-on-Demand** — content is decompressed as accessed, not
   eagerly in bulk, per NPS-004 §4.5's transparency requirement and to
   minimize launch latency.
3. **Cache** — frequently accessed decompressed content **SHOULD** be
   cached to avoid redundant decompression across sessions, subject to
   available memory/storage budget.
4. **Unmount** — on process/container exit, the image **MUST** unmount;
   the overlay **MUST** remain on disk and **MUST NOT** require the base
   image to be mounted to be readable by backup or file-management tools.

## 6. Verification

6.1. Nythera **MUST** be able to verify a `.nygi` image's integrity against
its manifest checksums (§3.1) without fully decompressing the image,
supporting the "faster verification" goal from the original design
discussion.

6.2. A failed verification **MUST** be reported to the user before launch
rather than allowed to fail silently mid-session.

## 7. Uninstall

7.1. Uninstalling MUST remove the base image; the overlay **MUST** be
retained by default and offered for deletion as a separate, explicit user
choice, per NPC-001 §10 and to avoid accidental save-data loss — satisfying
"clean uninstall" without silently discarding user data.

## 8. Known Limitations *(Informative)*

Per ADR-0003 and the original architecture discussion: multiplayer titles
relying on kernel-level anti-cheat drivers may be unsupported without
vendor cooperation, since Nythera's containerization model (ADR-0004) and
hybrid microkernel boundary (ADR-0006) do not grant the kind of
system-wide kernel access such anti-cheat software typically expects. This
limitation **MUST** be documented in user-facing compatibility information
rather than omitted.

## 9. Open Questions *(Informative)*

- Exact manifest format (binary vs. structured text) is deferred to a
  package-format NPS covering install-time production of `.nygi` files.
- Cross-title asset deduplication (distinct from within-image
  deduplication, per NPS-004 §4.4) is a possible future optimization, not
  required by this version.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |

---
**End of Document**
