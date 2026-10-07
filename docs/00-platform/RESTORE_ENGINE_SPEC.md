---
title: Whole-System Restore Engine — Design Note
document_id: RST-001
version: 0.1.0
status: Draft
classification: Technical
owners:
  - Nyrqis Architecture
created: 2026-10-07
updated: 2026-10-07
ai_assisted: true
review_cycle: Per-landing
depends_on: [NPS-011, ADR-0018, ADR-0019]
---

# RST-001 — Whole-System Restore Engine (Design Note)

> Drafted first per the DBG-001/CRY-001/UPD-001 discipline: the roadmap's
> "System restore points" item is PARTIAL (2026-10-07 re-probe) — the
> deployment/update-scoped half is shipped and wired; the open half is a
> whole-system image-level restore. This note audits the surface, records
> options, and pre-stages the build plan so an acceptance ruling converts
> directly to landed work.

## 1. Problem statement

A user can today roll back a deployment, a package update, and — since
0.29.27 — an entire signed-package update (restore point → apply →
rollback, audit-chained). What no surface provides is **"put the whole
system back to a previous point in time"**: the OS state a boot recovers
into. The desktop's Restore app (`ui/system_restore.py`) renders exactly
this surface — snapshots with sizes, bootable flags, a pre-breaking-change
restore point — but its data layer is the 0.28.0 spec-suite **simulation**
(sample data constructed in `_create_sample_data()`; nothing is written
to or read from disk). The app is a promise the platform does not keep.

## 2. Surface audit (tree-verified 2026-10-07)

| Layer | What exists | Status |
|-------|-------------|--------|
| Deployment/snapshot scope | `rollback_to_snapshot` (backend/container.py, dry-run-default) + deployment version rollback; five rollback IPC ops with five matching `nyrqisctl rollback-*` verbs | **Shipped, wired**, pinned (the `upd001-audit-rollback-pin` still holds at recorded counts) |
| Update scope | `UpdateOrchestrator` (backend/update_orchestrate.py) — resolve on the fully-verified signed index, verify BEFORE restore point BEFORE apply, operator-invoked-only rollback behind `validate_rollback`, JSONL history + audit chain | **Shipped**, 16 contract pins (`test_update_orchestrate.py`) |
| Storage layer | NyFS (fuse/nyfs.py): Copy-on-Write blocks (writes never mutate existing blocks; 64 KiB default), `gc_blocks()` with `gc_grace_seconds` (default 3600, 0.29.41 wired the at-rest GC) | **Shipped** — CoW is the primitive a whole-volume restore point wants |
| OS delivery | Live-ISO machinery: rootless two-phase build, squashfs rootfs, GRUB/isolinux entries incl. shell variants; 0.29.41/42 shipped the real-hardware firmware/microcode set | **Shipped** — a restore boot path can reuse this |
| Desktop surface | `ui/system_restore.py` (SystemRestore/Snapshot/RestorePoint/BackupScheduleEntry), `ui/backup_manager.py`, `ui/backup_scheduler.py` | **Simulation** — spec-suite data only; no engine behind it |
| IPC/CLI for system-level restore | none | **Gap** |

Load-bearing finding: the gap is **an engine and a boot path, not
cryptographic or audit machinery** — the restore-point concept, the
ordering discipline, and the audit chain all exist and are pinned; what
is missing is (a) a whole-volume point-in-time primitive on NyFS and
(b) a way to boot into a restore environment when the installed system
is the thing being restored.

## 3. Options

**Option A — NyFS-native volume restore points + a live-ISO restore boot
path (recommended).** A restore point is the volume's referenced block
set at a moment in time: CoW means historical blocks still exist until
GC reclaims them, so a restore point is (1) a PIN that marks the current
referenced set as non-reclaimable (restoring = re-pointing the volume
metadata at that set) and (2) a small manifest entry in the existing
audit chain. The boot path reuses the live ISO: a GRUB "Nyrqis Restore"
entry boots the same kernel/initrd the release ISO already ships, mounts
the target volume read-only, verifies the manifest through the existing
checker, and re-points. The desktop Restore app's data layer is swapped
from the simulation to this engine — the UI keeps its tested shape.

*Ledger*: + zero new storage dependency; + CoW already paid for;
+ reuses the verified boot machinery and the ADR-0018 chain;
+ the desktop surface becomes true without a rewrite; − needs the
GC-pin mechanism (a restore point must survive `gc_grace_seconds` —
age-based grace is not reference-pinning; §4 Q3); − restore-of-a-mounted
volume needs the unmount-or-readonly discipline the §4.5 recovery
already enforces.

**Option B — delegate to the filesystem (btrfs/LVM snapshots).**
Subvolume-level snapshots give whole-system restore below NyFS.

*Ledger*: + battle-tested kernel machinery; − a NEW storage dependency
violates the posture that NyFS owns volume semantics (the live rootfs is
ext4; the ISO boots on hardware without btrfs); − duplicates CoW
capability the platform already maintains and benchmarks (§37); − the
desktop app still needs an engine-facing data layer.

**Option C — observational close.** Keep the shipped deployment/update
scope; declare image-level restore out of scope; retire the simulated
desktop surface to an honest placeholder.

*Ledger*: + zero cost; − the roadmap half stays open and the desktop
keeps promising what the platform does not do.

## 4. Open questions for the Group

1. **Scope**: per-User NyFS volumes only, or the daemon host state
   (config, vault metadata) too? Recommendation: volumes first; host
   state rides the existing package/update path.
2. **Boot path shape**: a full restore ISO per release, or a restore
   entry in the installed system's GRUB pointing at the release ISO's
   kernel/initrd? Recommendation: the GRUB entry reusing the release
   ISO's boot assets — no second image to maintain.
3. **GC interplay**: restore points must pin their referenced blocks
   against `gc_blocks()` (grace is age-based, not reference-based).
   Mechanism: a pinned-set register consulted by the GC, audited like
   every other chain entry.
4. **Composition with updates**: a system restore while an update is
   pending — forbid (restore refuses while `apply_update` is in flight)
   or define an ordering? Recommendation: forbid; the UpdateOrchestrator
   ordering already treats a restore point as part of its own
   transaction.

## 5. Recommendation

**Option A**, phased per §7, with Q3 (the GC pin) as the one load-bearing
mechanism question — it is answerable by construction (a pinned-set
register) but changes `gc_blocks()`'s contract, which 0.29.41's pins
currently state as age-only. A is safe to accept before Q3 is answered
only if the first increment pins restore points AND runs the GC pins'
failure paths against the new mechanism in the same commit.

## 7. Pre-staged build plan (converts to landed work on acceptance)

1. `backend/restore_engine.py`: `RestoreEngine(volume)` composing the
   shipped primitives — referenced-set capture (the walk `gc_blocks()`
   already performs), the pinned-set register, manifest entries on the
   ADR-0018 chain, `restore_to(point_id)` (dry-run default, mirroring
   `rollback_to_snapshot`'s posture).
2. IPC + CLI: a `system_restore` op family and `nyrqisctl restore
   create/list/show/rollback` — client-side composition discipline, zero
   new trust surface.
3. The GC pin: `gc_blocks()` consults the pinned-set register; the
   existing GC pins (grace spares young orphans / reclaims aged ones)
   re-verified with a pin in place, plus a new pin that GC NEVER reclaims
   a pinned block.
4. Boot path: a GRUB "Nyrqis Restore" entry in both arch templates
   booting the release ISO's kernel/initrd with a restore target; the
   boot-smoke contract gains a restore-mode smoke.
5. Desktop: `ui/system_restore.py`'s data layer swapped to the engine
   behind its existing tested API (the spec suite keeps passing); the
   simulation's sample-data constructor removed.
6. Contract pins: no-egress assertion, restore-never-breaks-§4.5
   recovery, dry-run default, operator-only verbs.

## Change log

- 0.1.0 (2026-10-07): initial draft — surface audit, three options,
  four open questions, §7 pre-staged plan; staged for the Group as
  AG_AGENDA Bundle G.
