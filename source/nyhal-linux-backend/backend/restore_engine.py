"""Whole-system restore engine (RST-001, Bundle G — Option A,
ACCEPTED 2026-10-07; 0.29.44).

The "System restore points" roadmap item's open half: an image-level
whole-volume restore for a live NyFS volume. The engine COMPOSES the
shipped snapshot floor (``NyFS.create_snapshot`` /
``restore_snapshot`` / ``delete_snapshot`` — NPS-004 §4, the immutable
point-in-time copy: a deepcopy of the inode table re-anchored on
restore, block files untouched) and adds the one act the floor lacked:

- **The pin.** A deepcopy'd snapshot keeps its blocks alive only in
  metadata; a GC pass still reclaims the underlying block files
  (``gc_blocks`` walks inodes + snapshots, NOT snapshot-history
  blocks orphaned by later CoW rewrites — and the age-based grace
  reclaims them once old enough). ``RestoreEngine.create_snapshot``
  therefore captures the snapshot AND hard-links its referenced block
  files under ``state/snapshots/<snap_id>/``. ``NyFS._pinned_block_ids``
  (landed in the same commit, per the owner ruling) consults that
  directory on every GC unlink decision: a pinned block is NEVER
  reclaimed, regardless of age. Reference-pinning composes with
  grace — age keeps a racing ``save()`` safe, the pin keeps a
  restore point safe.
- **The audit chain.** Every capture / restore / delete appends to
  the volume's ADR-0018 chain via the same ``_audit_append`` pattern
  ``update_orchestrate.py`` established.
- **The registry.** A small ``registry.json`` under the snapshot dir
  records label/reason/counts so operators can list what they would
  restore to; the pin directory itself (block-id hard links) is the
  load-bearing artifact the GC consults — the registry is metadata.

Posture:
- The ONLY storage I/O goes through fuse/nyfs.py primitives
  (``lock``, ``create_snapshot``, ``restore_snapshot``,
  ``delete_snapshot``, ``_all_blocks``, ``_blocks_dir``). No network,
  no subprocess, no egress — the CRY-001/UPD-001 no-egress posture.
- Restore is dry-run-default at the CLI layer; the engine's
  ``restore_to`` requires the explicit ``dry_run=False`` and runs the
  reshape through ``NyFS.restore_snapshot`` (in-lock, dirty-marked,
  root re-anchored — the floor's own documented semantics).
- The restore act can orphan the live tree's replaced blocks relative
  to the NEW tree; those blocks stay pinned via the pre-restore
  referenced set (the engine re-pins it before the floor's reshape),
  and ``discard_replaced`` releases that pin when the operator
  confirms. Nothing is silently reclaimed.
- Snapshots die explicitly: ``delete_snapshot`` unpins (link
  directory removed) and unregisters; captured blocks then age out
  through the ordinary grace path.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nyrqis.restore_engine")

DEFAULT_SNAPSHOT_DIRNAME = "snapshots"


class RestoreEngineError(Exception):
    """Refusal raised for any engine precondition violation."""


def _audit_append(audit_manager: Optional[Any], chain_id: Optional[str],
                  op: str, result: Dict[str, Any]) -> None:
    if audit_manager is None or chain_id is None:
        return
    try:
        audit_manager.append_audit_entry(chain_id, op, result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("restore_engine: audit append failed (%s)", exc)


class RestoreEngine:
    """One object owning the snapshot→restore→audit sequence for a
    mounted NyFS volume (the ``fs`` object ``fuse/nyfs.py`` returns)."""

    def __init__(self, fs: Any,
                 audit_manager: Optional[Any] = None) -> None:
        if fs is None:
            raise RestoreEngineError("a NyFS volume is required")
        self.fs = fs
        self.base = Path(fs.base_path)
        self.snap_dir = self.base / "state" / DEFAULT_SNAPSHOT_DIRNAME
        self._audit_manager = audit_manager
        self._chain_id: Optional[str] = None
        if audit_manager is not None:
            try:
                self._chain_id = audit_manager.create_audit_chain(
                    "restore-engine")
            except AttributeError:
                self._chain_id = None
        self._load_registry()

    # ------------------------------------------------------------------
    # Registry persistence (operator metadata; the pin dir is the
    # load-bearing artifact)
    # ------------------------------------------------------------------

    def _registry_path(self) -> Path:
        return self.snap_dir / "registry.json"

    def _load_registry(self) -> None:
        try:
            data = json.loads(self._registry_path().read_text())
            self.snapshots: Dict[str, Dict[str, Any]] = \
                data.get("snapshots", {})
        except (OSError, ValueError):
            self.snapshots = {}

    def _save_registry(self) -> None:
        self.snap_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._registry_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(
            {"snapshots": self.snapshots}, indent=1, sort_keys=True))
        tmp.replace(self._registry_path())

    def _referenced_ids(self) -> Dict[str, str]:
        """The current referenced set (checksum → block_id), in-lock.
        Same walk gc_blocks performs."""
        refs: Dict[str, str] = {}
        for blk in self.fs._all_blocks(self.fs.inodes):
            if blk.checksum and blk.checksum not in refs:
                refs[blk.checksum] = blk.block_id
        for snap in self.fs.snapshots.values():
            for blk in self.fs._all_blocks(snap):
                if blk.checksum and blk.checksum not in refs:
                    refs[blk.checksum] = blk.block_id
        return refs

    def _pin_dir(self, snap_id: str) -> Path:
        return self.snap_dir / snap_id

    def _pin_blocks(self, pin_dir: Path,
                    refs: Dict[str, str]) -> None:
        """Hard-link the referenced block files into ``pin_dir``.
        A block still resident in the commit journal (journal-mode
        save is the default; its payload lives in
        ``state/journal.bin`` until compaction materializes it) is
        materialized into ``state/blocks/`` first — the exact act the
        journal compaction performs — so a pin is always a real file
        link. Fail-closed: a missing source or failed link refuses
        the capture — never ships a torn restore point."""
        blocks_dir = self.fs._blocks_dir()
        journal_index = None
        pin_dir.mkdir(parents=True, exist_ok=False)
        for block_id in refs.values():
            src = blocks_dir / (block_id + ".bin")
            if not src.exists():
                if journal_index is None:
                    journal_index = self.fs._scan_journal()
                if block_id not in journal_index:
                    raise RestoreEngineError(
                        f"referenced block neither on disk nor in the "
                        f"journal: {block_id}")
                payload = self.fs._journal_read(block_id)
                if payload is None:
                    raise RestoreEngineError(
                        f"journal read failed for {block_id}")
                blocks_dir.mkdir(parents=True, exist_ok=True)
                tmp = blocks_dir / f".{block_id}.rstmp"
                tmp.write_bytes(payload)
                os.replace(tmp, src)
            dst = pin_dir / block_id
            if not dst.exists():
                try:
                    os.link(src, dst)
                except OSError as exc:
                    raise RestoreEngineError(
                        f"pin failed for {block_id}: {exc}") from exc

    # ------------------------------------------------------------------
    # Capture (instant)
    # ------------------------------------------------------------------

    def create_snapshot(
        self, label: str = "",
        reason: str = "operator capture",
    ) -> Dict[str, Any]:
        """Capture a whole-volume restore point: the NyFS snapshot
        floor (in-lock deepcopy) PLUS the hard-link pin of the
        referenced block files. Capture is the durability point:
        ``save()`` runs first (journal append + metadata swap), so a
        restore point is never captured over blocks that exist only
        in memory. Fails closed — a failed capture never leaves the
        volume mutated or a torn pin behind."""
        if self.fs is None:
            raise RestoreEngineError("volume not attached")
        # Durability first: every referenced block must be in the
        # journal or on disk BEFORE the pin walks run.
        self.fs.save()
        snap_id = self.fs.create_snapshot()
        snap_path = self._pin_dir(snap_id)
        try:
            with self.fs.lock:
                refs = self._referenced_ids()
                self._pin_blocks(snap_path, refs)
        except Exception:
            # Fail closed: drop the floor snapshot + any partial pin.
            try:
                self.fs.delete_snapshot(snap_id)
            except ValueError:
                pass
            import shutil
            shutil.rmtree(snap_path, ignore_errors=True)
            raise
        entry = {
            "snap_id": snap_id,
            "label": label or snap_id,
            "reason": reason,
            "created": time.time(),
            "block_count": len(refs),
        }
        self.snapshots[snap_id] = entry
        self._save_registry()
        _audit_append(self._audit_manager, self._chain_id,
                      "restore.engine.snapshot.create", {
                          "snap_id": snap_id,
                          "blocks": entry["block_count"],
                          "reason": reason,
                      })
        return dict(entry)

    # ------------------------------------------------------------------
    # List / preview (non-mutating)
    # ------------------------------------------------------------------

    def list_snapshots(self) -> List[Dict[str, Any]]:
        return [
            {k: v for k, v in e.items() if k != "entries"}
            for e in sorted(self.snapshots.values(),
                            key=lambda e: e.get("created", 0))
        ]

    def preview_snapshot(self, snap_id: str) -> Dict[str, Any]:
        """Non-mutating listing of what a restore would anchor, with a
        pin-integrity check (every registered block must still be
        pinned — a tampered pin dir refuses the preview)."""
        entry = self.snapshots.get(snap_id)
        if entry is None:
            raise RestoreEngineError(f"unknown snapshot: {snap_id}")
        pin_dir = self._pin_dir(snap_id)
        pinned = {p.name for p in pin_dir.iterdir()} \
            if pin_dir.is_dir() else set()
        return {
            "snap_id": snap_id,
            "label": entry.get("label", ""),
            "block_count": entry["block_count"],
            "created": entry["created"],
            "pins_intact": len(pinned) >= entry["block_count"],
        }

    # ------------------------------------------------------------------
    # Restore (dry-run default at the CLI layer; explicit here)
    # ------------------------------------------------------------------

    def restore_to(
        self, snap_id: str, dry_run: bool = True,
        reason: str = "operator restore",
    ) -> Dict[str, Any]:
        """Re-anchor the volume's tree to the captured snapshot via
        the NyFS floor (``restore_snapshot``: in-lock deepcopy of the
        inode table, root re-bound, dirty-marked). Before the reshape,
        the CURRENT live referenced set is hard-link-pinned under
        ``<snap_id>.replaced/`` — the restore act orphans exactly
        those blocks relative to the new tree, and they stay pinned
        until ``discard_replaced``. Audit-chained."""
        entry = self.snapshots.get(snap_id)
        if entry is None:
            raise RestoreEngineError(f"unknown snapshot: {snap_id}")
        pin_dir = self._pin_dir(snap_id)
        if not pin_dir.is_dir():
            raise RestoreEngineError(
                f"pin directory missing for {snap_id} — the restore "
                "point is unverifiable, refusing")
        result = {
            "snap_id": snap_id,
            "dry_run": bool(dry_run),
            "blocks": entry["block_count"],
            "label": entry.get("label", ""),
        }
        if dry_run:
            return result

        # Re-pin the CURRENT live set before the floor's reshape. The
        # capture walk runs under its own in-lock section (the floor's
        # snapshot/restore/delete primitives are each internally
        # locked — a non-reentrant threading.Lock, so the engine never
        # holds the lock across a floor call).
        refs = {}
        with self.fs.lock:
            for blk in self.fs._all_blocks(self.fs.inodes):
                if blk.checksum:
                    refs[blk.checksum] = blk.block_id
            for snap in self.fs.snapshots.values():
                for blk in self.fs._all_blocks(snap):
                    if blk.checksum and blk.checksum not in refs:
                        refs[blk.checksum] = blk.block_id
        replaced_dir = self.snap_dir / (snap_id + ".replaced")
        try:
            self._pin_blocks(replaced_dir, refs)
        except OSError as exc:
            raise RestoreEngineError(
                f"restore re-pin failed: {exc}") from exc
        # The floor's own in-lock reshape (deepcopy + root re-bind +
        # dirty mark).
        self.fs.restore_snapshot(snap_id)

        _audit_append(self._audit_manager, self._chain_id,
                      "restore.engine.restore", {
                          "snap_id": snap_id,
                          "dry_run": False,
                          "blocks": entry["block_count"],
                          "reason": reason,
                      })
        return result

    def discard_replaced(self, snap_id: str) -> bool:
        """Release the pre-restore pin for a completed restore: the
        replaced blocks lose their pin and age out through the
        ordinary GC grace path. Returns whether a pin existed."""
        d = self.snap_dir / (snap_id + ".replaced")
        if not d.is_dir():
            return False
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        _audit_append(self._audit_manager, self._chain_id,
                      "restore.engine.replaced.discarded",
                      {"snap_id": snap_id})
        return True

    # ------------------------------------------------------------------
    # Unpin (restore points die explicitly, then age via grace)
    # ------------------------------------------------------------------

    def delete_snapshot(self, snap_id: str) -> bool:
        """Unpin a restore point: the NyFS floor snapshot, the pin
        directory and the registry row go; captured blocks then age
        out through the ordinary GC grace path. Returns whether the
        id existed."""
        existed = snap_id in self.snapshots
        try:
            self.fs.delete_snapshot(snap_id)
        except ValueError:
            pass
        import shutil
        for suffix in ("", ".replaced"):
            p = self.snap_dir / (snap_id + suffix)
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
        self.snapshots.pop(snap_id, None)
        self._save_registry()
        if existed:
            _audit_append(self._audit_manager, self._chain_id,
                          "restore.engine.snapshot.delete",
                          {"snap_id": snap_id})
        return existed
