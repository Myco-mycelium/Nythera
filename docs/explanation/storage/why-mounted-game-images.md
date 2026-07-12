# Why Games Are Mounted Images, Not Folders

*(Explanation — see NPS-004, NPS-005, NPS-006 for the normative specs, and
ADR-0002, ADR-0003, ADR-0007 for the formal decision records.)*

## The Problem

The traditional model — a game is a folder full of loose files under
`C:\Games\...` — causes several accumulating problems: files fragment
across the disk over time, verifying a large install means re-hashing
thousands of small files individually, uninstalling reliably means trusting
an uninstaller script to find everything it wrote, and nothing stops game
assets from being casually modified, which both invites cheating in
multiplayer titles and makes corruption harder to detect.

At the same time, many Windows installers and launchers genuinely expect a
normal, writable filesystem underneath them — they write config files,
patch data, and save games directly into what they assume is their own
directory tree.

## The Choice

Nythera treats a game the way a physical disc always implicitly did: as a
single, verifiable, read-only unit (`.nygi`, NPS-006), mounted at launch and
unmounted at close. But unlike a real disc, every image is paired with a
writable copy-on-write overlay (NPS-004 §4.1) that absorbs saves, mods, and
anything an installer or launcher writes — so software that assumes a
normal writable directory keeps working without modification.

Compression (NPS-005) rides along transparently: the base image is stored
compressed with Zstd (ADR-0007) and decompressed on demand as content is
actually accessed, rather than requiring a full decompress-to-disk step at
install time.

## What This Buys Us

- **Verification** — checking a game's integrity means checking manifest
  checksums (NPS-006 §6), not re-scanning a scattered folder tree.
- **Clean uninstall** — removing a game removes one image; the overlay,
  holding actual user data (saves), is kept and offered separately rather
  than silently deleted (NPS-006 §7).
- **Reduced tampering surface** — the base content layer is read-only by
  construction, not by convention or permission bits that a determined
  process could still flip.
- **Backup** — a user's saves and settings live in one well-defined overlay
  location per game rather than being scattered across `AppData`,
  `Documents`, and the install folder itself.

## What It Costs Us

- Extra engineering complexity in the mount/overlay layer compared to "just
  copy files to a folder."
- A real, acknowledged limitation: kernel-level anti-cheat systems that
  expect deep system access don't have an obvious place to live in this
  model, and Nythera does not currently claim to solve that (NPS-006 §8).

## Alternatives We Considered

See ADR-0003 for the full comparison against a fully writable per-game
directory (status quo) and a fully read-only image with no overlay, and why
each was rejected in favor of the read-only-plus-overlay hybrid.
