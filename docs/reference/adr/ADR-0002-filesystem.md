---
title: Adopt Copy-on-Write Filesystem with Built-in Compression
document_id: ADR-0002
version: 1.0.0
status: Proposed
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-12
depends_on: [NTM-000, NPC-001]
---

# ADR-0002 — Copy-on-Write Filesystem with Built-in Compression

## Context
Nythera's design calls for transparent application/game compression,
snapshots, deduplication, and integrity checks (NTM-000 vision;
"Filesystem" discussion). Legacy filesystems (NTFS, ext4) do not provide
these natively, requiring bolt-on user-space tooling that is harder to make
reliable.

## Decision (Proposed)
Adopt a copy-on-write filesystem model, drawing on Btrfs/ZFS concepts, as the
default Nythera filesystem (working name: **NyFS**), providing:

- Transparent per-file/per-extent compression (Zstd default, LZ4 for
  latency-sensitive paths).
- Snapshots and atomic updates (supports NTM-000 "Updates" — immutable
  system images).
- Checksums for silent-corruption detection.
- Deduplication for shared runtime/library data.

## Alternatives Considered
- **Use ext4/NTFS with a user-space compression layer** — rejected; violates
  "Security is created through architecture" and adds fragility at the
  application layer.
- **Adopt Btrfs unmodified** — remains open; may be the actual implementation
  vehicle rather than a ground-up design. To be resolved in NPS storage
  specifications (Milestone M4).

## Consequences
- Requires a dedicated storage specification series under
  `docs/reference/nps/` before implementation begins.
- All game/application image mounting (ADR-0003) depends on this decision.

## Status
Proposed — pending Milestone M4 storage specification work.
