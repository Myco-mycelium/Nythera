"""Tests for tablet config, text diff, and backup manager."""
import unittest
import time

from ui.tablet_config import (
    TabletConfig, TabletDevice, TabletButton, PressureCurve, PressurePoint,
    TouchRing, DisplayConfig, TabletProfile, ButtonAction, CurveType, DisplayMapping,
)
from ui.text_diff import (
    TextDiff, TextDocument, DiffHunk, DiffMode,
)
from ui.backup_manager import (
    BackupManager, BackupSnapshot, RestorePoint, BackupSchedule, StorageInfo,
    BackupMode, BackupStatus, ScheduleFreq,
)


# ─── Tablet Config Tests ─────────────────────────────────────────────

class TestPressureCurve(unittest.TestCase):
    def test_point_count(self):
        pc = PressureCurve(control_points=[PressurePoint(0, 0), PressurePoint(50, 50)])
        self.assertEqual(pc.point_count, 2)

    def test_curve_visual(self):
        pc = PressureCurve(control_points=[PressurePoint(0, 0), PressurePoint(50, 50), PressurePoint(100, 100)])
        visual = pc.curve_visual
        self.assertIn("●", visual)


class TestTabletDevice(unittest.TestCase):
    def test_battery_bar(self):
        d = TabletDevice(battery_pct=50)
        bar = d.battery_bar
        self.assertEqual(len(bar), 20)

    def test_area_str(self):
        d = TabletDevice(active_area_mm=(318.0, 198.0))
        self.assertIn("318", d.area_str)


class TestTabletButton(unittest.TestCase):
    def test_display(self):
        b = TabletButton(action=ButtonAction.ERASER)
        self.assertIn("🧹", b.display)

    def test_unassigned(self):
        b = TabletButton(action=ButtonAction.NONE)
        self.assertIn("Unassigned", b.display)


class TestTabletConfig(unittest.TestCase):
    def setUp(self):
        self.config = TabletConfig()

    def test_initial_state(self):
        self.assertGreater(len(self.config._devices), 0)
        self.assertGreater(len(self.config._profiles), 0)

    def test_render_device(self):
        lines = self.config.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("TABLET CONFIGURATOR" in l for l in lines))

    def test_render_pressure(self):
        self.config.set_view("pressure")
        lines = self.config.render()
        self.assertTrue(any("Pressure" in l for l in lines))

    def test_render_buttons(self):
        self.config.set_view("buttons")
        lines = self.config.render()
        self.assertTrue(any("Buttons" in l or "Pen" in l for l in lines))

    def test_render_display(self):
        self.config.set_view("display")
        lines = self.config.render()
        self.assertTrue(any("Display" in l for l in lines))

    def test_render_profiles(self):
        self.config.set_view("profiles")
        lines = self.config.render()
        self.assertTrue(any("Profiles" in l for l in lines))


# ─── Text Diff Tests ─────────────────────────────────────────────────

class TestTextDocument(unittest.TestCase):
    def test_load(self):
        doc = TextDocument("Test")
        doc.load("line one\nline two\nline three")
        self.assertEqual(doc.line_count, 3)
        self.assertEqual(doc.word_count, 6)


class TestTextDiff(unittest.TestCase):
    def setUp(self):
        self.diff = TextDiff()

    def test_initial_state(self):
        self.assertGreater(self.diff._left.line_count, 0)
        self.assertGreater(self.diff._right.line_count, 0)

    def test_render_side_by_side(self):
        lines = self.diff.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("TEXT DIFF" in l for l in lines))

    def test_render_unified(self):
        self.diff.set_view("unified")
        lines = self.diff.render()
        self.assertTrue(any("Unified" in l for l in lines))

    def test_render_inline(self):
        self.diff.set_view("inline")
        lines = self.diff.render()
        self.assertTrue(any("Inline" in l for l in lines))

    def test_render_merge(self):
        self.diff.set_view("merge")
        lines = self.diff.render()
        self.assertTrue(any("Merge" in l for l in lines))

    def test_additions_deletions(self):
        self.assertGreaterEqual(self.diff.total_additions, 0)
        self.assertGreaterEqual(self.diff.total_deletions, 0)

    def test_similarity(self):
        sim = self.diff.similarity_pct
        self.assertGreater(sim, 0)
        self.assertLessEqual(sim, 100)

    def test_hunks(self):
        self.assertGreater(len(self.diff._hunks), 0)


# ─── Backup Manager Tests ────────────────────────────────────────────

class TestBackupSnapshot(unittest.TestCase):
    def test_compression_ratio(self):
        s = BackupSnapshot(size_gb=100, compressed_gb=60)
        self.assertAlmostEqual(s.compression_ratio, 40.0)

    def test_duration_str(self):
        s = BackupSnapshot(duration_s=3600)
        self.assertIn("h", s.duration_str)

    def test_size_str(self):
        s = BackupSnapshot(size_gb=45.2)
        self.assertIn("45.2", s.size_str)


class TestStorageInfo(unittest.TestCase):
    def test_usage_pct(self):
        s = StorageInfo(total_gb=1000, used_gb=500)
        self.assertAlmostEqual(s.usage_pct, 50.0)

    def test_usage_bar(self):
        s = StorageInfo(total_gb=1000, used_gb=200)
        bar = s.usage_bar
        self.assertEqual(len(bar), 20)


class TestBackupManager(unittest.TestCase):
    def setUp(self):
        self.mgr = BackupManager()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr._snapshots), 0)
        self.assertGreater(len(self.mgr._schedules), 0)
        self.assertGreater(len(self.mgr._restore_points), 0)

    def test_render_snapshots(self):
        lines = self.mgr.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("BACKUP MANAGER" in l for l in lines))

    def test_render_schedules(self):
        self.mgr.set_view("schedules")
        lines = self.mgr.render()
        self.assertTrue(any("Schedules" in l for l in lines))

    def test_render_restore(self):
        self.mgr.set_view("restore")
        lines = self.mgr.render()
        self.assertTrue(any("Restore" in l for l in lines))

    def test_render_storage(self):
        self.mgr.set_view("storage")
        lines = self.mgr.render()
        self.assertTrue(any("Storage" in l for l in lines))

    def test_render_history(self):
        self.mgr.set_view("history")
        lines = self.mgr.render()
        self.assertTrue(any("History" in l for l in lines))

    def test_total_backup_size(self):
        self.assertGreater(self.mgr.total_backup_size, 0)

    def test_snapshots_today(self):
        self.assertIsInstance(self.mgr.snapshots_today, int)


if __name__ == "__main__":
    unittest.main()
