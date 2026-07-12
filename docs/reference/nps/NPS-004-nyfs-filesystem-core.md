---
title: NyFS Filesystem Core
document_id: NPS-004
version: 1.0.0
status: Draft
classification: Normative
subsystem: storage
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, ADR-0002, ADR-0007, NPS-001]
---

# NPS-004 — NyFS Filesystem Core

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
implements the copy-on-write filesystem decision recorded in ADR-0002 and
the codec decision in ADR-0007.

## 2. Scope

This specification defines the on-disk structural guarantees NyFS **MUST**
provide: copy-on-write semantics, snapshots, checksums, deduplication, and
transparent compression. It does not define the game-image format built on
top of NyFS (see NPS-006) or the storage I/O kernel fast path (see NPS-001
§3), which NyFS calls into rather than reimplements.

## 3. Placement in the Architecture

3.1. NyFS **MUST** run as an isolated user-space service, per NPS-001 §4,
not in kernel space. It **MUST** communicate with the kernel storage fast
path defined in NPS-001 §3 via the IPC primitives defined in NPS-003.

3.2. NyFS **MUST** be reachable only through capability-scoped endpoints
(NPS-003 §4); a container without a filesystem-access capability **MUST
NOT** be able to reach NyFS at all, not merely be denied at the operation
level.

## 4. Core Guarantees

### 4.1 Copy-on-Write
Every write to an existing extent **MUST** allocate new physical blocks
rather than overwriting in place, and **MUST** update metadata atomically
so that a crash mid-write leaves either the old or the new state intact,
never a partially-written mix, per NTM-000 §4 ("Reliability").

### 4.2 Snapshots
NyFS **MUST** support point-in-time, read-only snapshots at the volume or
subvolume level. Creating a snapshot **MUST** be an O(1) metadata operation
that does not copy data eagerly. Snapshots are the mechanism underlying the
atomic-update rollback behavior described in NTM-000 ("Updates") and MUST
be usable by a future update-system NPS without modification to this
document.

### 4.3 Checksums
Every data block and metadata block **MUST** be checksummed. NyFS **MUST**
detect silent corruption on read and **MUST** report it rather than
returning corrupted data silently, per NTM-000 §4 ("Transparency").
Self-healing (repairing from a redundant copy) **MAY** be supported where
redundancy exists but is not required by this version of the specification.

### 4.4 Deduplication
NyFS **SHOULD** deduplicate identical extents across files (e.g. shared
runtime libraries, shared game assets across titles) at write time or via a
background pass. Deduplication **MUST NOT** compromise the checksum
guarantee in §4.3 — a deduplicated extent's checksum **MUST** still be
independently verifiable per referencing file.

### 4.5 Transparent Compression
All data written to NyFS **MUST** be compressed by default using the codec
selected in ADR-0007 (Zstd, low level for active data), unless the write
path is explicitly marked incompressible (e.g. already-compressed media)
to avoid wasted CPU cycles. Compression **MUST** be fully transparent to
applications — no application-visible API distinguishes compressed from
uncompressed storage.

## 5. Encryption

5.1. NyFS **MUST** support per-volume encryption at rest as an optional,
user-enabled feature, consistent with NTM-000's Filesystem goals and
NPC-001 §10 (User Ownership of data).

5.2. Encryption **MUST NOT** be required for core functionality, and its
absence **MUST NOT** disable any other guarantee in §4.

## 6. SSD Optimization *(Informative)*

NyFS's copy-on-write write pattern is naturally SSD-friendly (write
amplification is a primary design concern), but exact TRIM/discard
scheduling, wear-leveling interaction, and write-amplification benchmarks
are deferred to an implementation-phase performance NPS, since publishing
specific numbers now would violate NPC-002 §5.2 without measured data.

## 7. Failure Semantics

7.1. A NyFS process crash **MUST NOT** corrupt on-disk state, by virtue of
the atomicity guarantee in §4.1; on restart, NyFS **MUST** be able to mount
using only its last consistent metadata state.

7.2. NyFS **MUST** surface I/O errors from the underlying storage fast path
(NPS-001 §3) to the calling container rather than silently retrying
indefinitely.

## 8. Open Questions *(Informative)*

- Exact on-disk metadata layout (B-tree variant, extent allocation
  strategy) is an implementation detail deferred to a lower-level NPS once
  a reference implementation begins.
- Whether NyFS is built from scratch or adapts existing Btrfs/ZFS code, as
  raised in ADR-0002, remains unresolved and does not block this
  specification, which defines behavior rather than implementation.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |

---
**End of Document**
