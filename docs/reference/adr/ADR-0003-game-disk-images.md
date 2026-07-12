---
title: Games Distributed as Mounted Disk Images with Writable Overlay
document_id: ADR-0003
version: 1.0.0
status: Proposed
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-12
depends_on: [NTM-000, NPC-001, ADR-0002]
---

# ADR-0003 — Game Disk Images with Writable Overlay

## Context
The original design goal is for every installed game to behave like a
mounted virtual disk image rather than a loose directory tree, reducing
fragmentation and simplifying backup, verification, and uninstall. However,
many Windows installers and launchers expect a normal writable filesystem.

## Decision (Proposed)
Each installed game is packaged as a read-only, compressed image (`.nygi`)
mounted at launch, paired with a writable copy-on-write overlay (per
ADR-0002) for save data, mods, and installer-written files. On close, the
image unmounts; the overlay persists independently and is included in
backups.

## Alternatives Considered
- **Fully writable per-game filesystem (status quo, e.g. `C:\Games\...`)** —
  rejected; does not deliver deduplication, read-only integrity, or clean
  uninstall guarantees.
- **Fully read-only images with no overlay** — rejected; breaks
  installers/launchers that write into their own directory, and blocks save
  games under `NPC-001 §10` (User Ownership of data).

## Consequences
- Requires an NPS specification defining the `.nygi` image format,
  compression codec selection, and overlay semantics (Milestone M4/M7).
- Anti-cheat compatibility remains an open risk, noted in the original
  design discussion; kernel-driver-dependent anti-cheat may require vendor
  cooperation and is out of scope for the initial specification.

## Status
Proposed — pending Milestone M7 gaming subsystem specification work.
