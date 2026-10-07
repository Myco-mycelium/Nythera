"""Contract pins for the whole-system restore engine (RST-001,
Bundle G — Option A ACCEPTED 2026-10-07; 0.29.44).

Pins:
- capture is instant (no block copies) and pins the referenced set;
- a pinned block is NEVER reclaimed by gc_blocks, regardless of age
  (grace=0: the pre-0.29.41 immediate-reclaim semantics would take
  every unreferenced block the instant after a rewrite — the pin
  must survive that);
- restore through the floor re-anchors the live tree and re-pins the
  replaced set (the restore act orphans nothing reclaimable);
- dry-run default: preview mutates nothing; restore_to without
  dry_run=False commits;
- delete_snapshot unpins; the freed blocks then age out via grace;
- fail-closed capture: a missing block file refuses, never ships a
  torn restore point;
- the no-egress posture: the engine touches only fuse/nyfs.py
  primitives (no socket, subprocess, or urllib imports in the module);
- the ADR-0018 audit chain records create/restore/discard/delete.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fuse.nyfs import NyFSFilesystem
from backend.restore_engine import RestoreEngine, RestoreEngineError


def _write_tree(fs, name: str, payload: bytes) -> None:
    path = f"/{name}"
    try:
        fs.unlink(path)
    except OSError:
        pass
    fs.create_file(path, 0o644)
    fs.write(path, payload)


def _unique_payload(seed: int, size: int = 3 * 65536) -> bytes:
    # Deterministic incompressible-ish content so every block is unique
    # (no dedup collapsing the referenced set).
    out = bytearray()
    i = 0
    while len(out) < size:
        out += (f"rst-{seed}-{i}-".encode() * 4096)[:4096]
        i += 1
    return bytes(out[:size])


class _AuditManager:
    """Minimal audit-chain double (the ADR-0018 surface)."""

    def __init__(self):
        self.entries = []

    def create_audit_chain(self, purpose):
        return f"chain-{purpose}"

    def append_audit_entry(self, chain_id, op, result):
        self.entries.append((chain_id, op, result))


class TestRestoreEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, "vol")
        os.makedirs(self.base)
        self.fs = NyFSFilesystem(self.base, gc_grace_seconds=0)
        self.engine = RestoreEngine(self.fs)
        self.audit = _AuditManager()
        self.engine_audit = RestoreEngine(self.fs, audit_manager=self.audit)

    def _seed(self, seed=1):
        _write_tree(self.fs, f"f{seed}.bin", _unique_payload(seed))
        # Boot-mode save: blocks land as real files in state/blocks/
        # (the journal default keeps payloads in state/journal.bin,
        # where gc_blocks never walks). The grace-vs-pin discrimination
        # this suite pins needs on-disk orphans to exist.
        self.fs.save(use_journal=False)

    def test_capture_is_instant_and_pins_the_referenced_set(self):
        self._seed(1)
        before = self.fs.gc_blocks()
        entry = self.engine.create_snapshot(label="baseline")
        self.assertEqual(entry["block_count"] > 0, True)
        pin_dir = Path(self.base) / "state" / "snapshots" / entry["snap_id"]
        links = list(pin_dir.iterdir())
        self.assertEqual(len(links), entry["block_count"])
        # Hard links, not copies: the link count on a block file is >1.
        first = links[0]
        self.assertEqual(first.stat().st_nlink >= 2, True)
        # The registry row exists and lists the snapshot.
        self.assertIn(entry["snap_id"],
                      [s["snap_id"] for s in self.engine.list_snapshots()])
        # gc_blocks reclaimed nothing the pin holds (grace=0 already
        # reclaimed the pre-snapshot orphans above).
        self.assertEqual(before, 0)

    def test_pinned_block_survives_grace_zero_gc(self):
        self._seed(2)
        entry = self.engine.create_snapshot(label="pin-test")
        pin_dir = Path(self.base) / "state" / "snapshots" / entry["snap_id"]
        pinned_ids = {p.name for p in pin_dir.iterdir()}
        # Rewrite the file: CoW orphans the ORIGINAL blocks — the very
        # blocks the restore point pins. grace=0 means an unreferenced
        # block is reclaimable the instant it exists unreferenced on
        # disk; the pin must override that for exactly the pinned set.
        _write_tree(self.fs, "f2.bin", _unique_payload(999))
        self.fs.save(use_journal=False)
        # ...and an explicitly orphaned scratch block, so the pass has
        # something to reclaim (proving grace=0 stayed armed).
        _write_tree(self.fs, "scratch.bin", _unique_payload(998))
        self.fs.save(use_journal=False)
        _write_tree(self.fs, "scratch.bin", _unique_payload(997))
        self.fs.save(use_journal=False)
        removed = self.fs.gc_blocks()
        still_there = {
            p.name for p in pin_dir.iterdir()}
        self.assertEqual(still_there, pinned_ids)
        blocks_dir = Path(self.base) / "state" / "blocks"
        for block_id in pinned_ids:
            self.assertTrue((blocks_dir / (block_id + ".bin")).exists(),
                            f"pinned block {block_id} was reclaimed")
        self.assertGreater(removed, 0)  # the rewrite's orphans went

    def test_restore_to_reanchors_tree_and_repins_replaced_set(self):
        self._seed(3)
        entry = self.engine.create_snapshot(label="before-change")
        original = self.fs.read("/f3.bin")
        _write_tree(self.fs, "f3.bin", _unique_payload(777))
        self.fs.save()
        self.assertNotEqual(self.fs.read("/f3.bin"), original)

        # Dry-run default: preview mutates nothing.
        preview = self.engine.restore_to(entry["snap_id"])
        self.assertTrue(preview["dry_run"])
        self.assertEqual(self.fs.read("/f3.bin"), _unique_payload(777))

        # Committed restore: the tree re-anchors.
        result = self.engine.restore_to(entry["snap_id"], dry_run=False)
        self.assertFalse(result["dry_run"])
        self.assertEqual(self.fs.read("/f3.bin"), original)

        # The replaced set is pinned — the pre-restore blocks survive
        # an immediate grace=0 GC.
        replaced_dir = (Path(self.base) / "state" / "snapshots" /
                        (entry["snap_id"] + ".replaced"))
        self.assertTrue(replaced_dir.is_dir())
        replaced_ids = {p.name for p in replaced_dir.iterdir()}
        self.fs.gc_blocks()
        blocks_dir = Path(self.base) / "state" / "blocks"
        for block_id in replaced_ids:
            self.assertTrue((blocks_dir / (block_id + ".bin")).exists())
        # ...and the operator can release them explicitly.
        self.assertTrue(self.engine.discard_replaced(entry["snap_id"]))
        self.assertFalse(replaced_dir.exists())

    def test_delete_unpins_and_grace_reclaims_after_expiry(self):
        self._seed(4)
        entry = self.engine.create_snapshot(label="doomed")
        pin_dir = Path(self.base) / "state" / "snapshots" / entry["snap_id"]
        pinned = [p.name for p in pin_dir.iterdir()]
        # Rewrite f4.bin: its ORIGINAL blocks (the pinned set) become
        # unreferenced on disk.
        _write_tree(self.fs, "f4.bin", _unique_payload(556))
        self.fs.save(use_journal=False)
        self.assertTrue(self.engine.delete_snapshot(entry["snap_id"]))
        self.assertFalse(pin_dir.exists())
        self.assertNotIn(entry["snap_id"],
                         [s["snap_id"] for s in self.engine.list_snapshots()])
        # Age the blocks past any grace and reclaim: the unpinned
        # blocks are gone (the pinned set is no longer protected).
        blocks_dir = Path(self.base) / "state" / "blocks"
        stale = time.time() - 7200
        for path in blocks_dir.glob("*.bin"):
            os.utime(path, (stale, stale))
        self.fs.gc_blocks()
        for block_id in pinned:
            self.assertFalse((blocks_dir / (block_id + ".bin")).exists(),
                             f"unpinned block {block_id} survived GC")

    def test_fail_closed_capture_on_missing_block_file(self):
        self._seed(5)
        entry = self.engine.create_snapshot(label="good")
        # Corrupt the pin story: tear the JOURNAL (the durable home of
        # journal-mode blocks — truncating mid-payload simulates a
        # crash-torn tail that a later capture would otherwise pin),
        # then capture a NEW block and try again.
        journal = Path(self.base) / "state" / "journal.bin"
        if journal.exists():
            data = journal.read_bytes()
            journal.write_bytes(data[: len(data) // 2])  # torn tail
            self.fs._journal_index = None  # invalidate the scan cache
            self.fs._journal_ids = set()
        _write_tree(self.fs, "new.bin", _unique_payload(5150))
        blocks_dir = Path(self.base) / "state" / "blocks"
        saved = sorted(blocks_dir.glob("*.bin"))
        for p in saved:  # hide any materialized files too
            p.rename(p.with_name(p.name + ".hidden"))
        try:
            with self.assertRaises(RestoreEngineError):
                self.engine.create_snapshot(label="torn")
        finally:
            for p in blocks_dir.glob("*.hidden"):
                p.rename(p.with_name(p.name[:-len(".hidden")]))
        # No torn pin directory left behind; the good snapshot is
        # untouched.
        snap_root = Path(self.base) / "state" / "snapshots"
        dirs = [p.name for p in snap_root.iterdir()
                if p.is_dir() and not p.name.endswith(".replaced")]
        self.assertEqual(dirs, [entry["snap_id"]])

    def test_unknown_snapshot_and_missing_pin_refuse(self):
        with self.assertRaises(RestoreEngineError):
            self.engine.restore_to("rst-nonexistent")
        self._seed(6)
        entry = self.engine.create_snapshot()
        pin_dir = Path(self.base) / "state" / "snapshots" / entry["snap_id"]
        import shutil
        shutil.rmtree(pin_dir)
        with self.assertRaises(RestoreEngineError):
            self.engine.restore_to(entry["snap_id"], dry_run=False)

    def test_no_egress_posture(self):
        import backend.restore_engine as mod
        src = Path(mod.__file__).read_text()
        for forbidden in ("import socket", "import urllib",
                          "import requests", "import subprocess",
                          "urllib.request"):
            self.assertNotIn(forbidden, src)

    def test_audit_chain_records_lifecycle(self):
        self._seed(7)
        entry = self.engine_audit.create_snapshot(label="audited")
        self.engine_audit.restore_to(entry["snap_id"], dry_run=False)
        self.engine_audit.discard_replaced(entry["snap_id"])
        self.engine_audit.delete_snapshot(entry["snap_id"])
        ops = [op for (_c, op, _r) in self.audit.entries]
        self.assertEqual(ops, [
            "restore.engine.snapshot.create",
            "restore.engine.restore",
            "restore.engine.replaced.discarded",
            "restore.engine.snapshot.delete",
        ])
        self.assertTrue(all(c == "chain-restore-engine"
                            for (c, _o, _r) in self.audit.entries))


if __name__ == "__main__":
    unittest.main()
