"""Tests for DiffTool, RestoreManager, VoiceRecorder"""
import time
import unittest

from ui.diff_tool import DiffTool, DiffMode, Highlight, DiffType, DiffLine, DiffFile
from ui.restore_manager import RestoreManager, RestorePoint, RestoreType, RestoreStatus
from ui.voice_recorder import VoiceRecorder, Recording, RecordFormat, NoiseReduction


class TestDiffTool(unittest.TestCase):
    def setUp(self):
        self.dt = DiffTool()

    def test_initial_state(self):
        self.assertEqual(self.dt._selected, 0)
        self.assertEqual(self.dt._mode, DiffMode.SIDE_BY_SIDE)
        self.assertGreater(len(self.dt._files), 0)

    def test_select_file(self):
        self.dt.select(1)
        self.assertEqual(self.dt._selected, 1)
        self.assertIn("python", self.dt.selected_file.language)

    def test_select_invalid(self):
        self.dt.select(99)
        self.assertEqual(self.dt._selected, 0)

    def test_total_changes(self):
        self.assertGreater(self.dt.total_changes, 0)

    def test_conflicts(self):
        self.assertEqual(self.dt.total_conflicts, 2)

    def test_resolve_conflict_ours(self):
        self.assertTrue(self.dt.merge_ours(0))
        self.assertEqual(self.dt.total_conflicts, 1)

    def test_resolve_conflict_theirs(self):
        self.assertTrue(self.dt.merge_theirs(1))
        self.assertEqual(self.dt.total_conflicts, 1)

    def test_resolve_conflict_both(self):
        self.assertTrue(self.dt.merge_both(0))
        self.assertTrue(self.dt._conflicts[0].resolved)

    def test_resolve_invalid(self):
        self.assertFalse(self.dt.merge_ours(99))

    def test_render(self):
        lines = self.dt.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("NYRQIS" in l for l in lines))

    def test_render_diff(self):
        lines = self.dt.render_file_diff(0)
        self.assertGreater(len(lines), 3)
        self.assertTrue(any("@@" in l for l in lines))

    def test_render_conflicts(self):
        lines = self.dt.render_conflicts()
        self.assertGreater(len(lines), 3)
        self.assertTrue(any("config.toml" in l for l in lines))

    def test_compare(self):
        hunks = self.dt.compare("hello\nworld", "hello\nuniverse")
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].lines[1].diff_type, DiffType.MODIFIED)


class TestRestoreManager(unittest.TestCase):
    def setUp(self):
        self.rm = RestoreManager()

    def test_initial_state(self):
        self.assertGreater(len(self.rm._points), 0)
        self.assertTrue(self.rm._auto_enabled)

    def test_select(self):
        self.rm.select(1)
        self.assertIsNotNone(self.rm.selected_point)

    def test_create_point(self):
        before = len(self.rm._points)
        p = self.rm.create("Test Backup", RestoreType.USER, "Test description")
        self.assertEqual(len(self.rm._points), before + 1)
        self.assertEqual(p.name, "Test Backup")

    def test_delete_point(self):
        # Create unprotected point
        p = self.rm.create("Delete Me", RestoreType.USER)
        idx = len(self.rm._points) - 1
        self.assertTrue(self.rm.delete(idx))

    def test_protected_not_deletable(self):
        # Find protected point
        for i, p in enumerate(self.rm._points):
            if p.is_protected:
                self.assertFalse(self.rm.delete(i))
                break

    def test_protect(self):
        p = self.rm.create("To Protect", RestoreType.USER)
        idx = len(self.rm._points) - 1
        self.rm.protect(idx)
        self.assertTrue(p.is_protected)

    def test_cleanup_expired(self):
        # Create expired point
        old = RestorePoint("Old", RestoreType.USER, "", time.time() - 86400 * 200, 100)
        self.rm._points.append(old)
        removed = self.rm.cleanup_expired()
        self.assertGreaterEqual(removed, 1)

    def test_total_size(self):
        self.assertGreater(self.rm.total_size_mb, 0)

    def test_render(self):
        lines = self.rm.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("RESTORE" in l for l in lines))

    def test_render_detail(self):
        self.rm.select(0)
        lines = self.rm.render_detail()
        self.assertGreater(len(lines), 2)

    def test_display_size(self):
        p = RestorePoint("Test", RestoreType.FULL, "", time.time(), 2048)
        self.assertIn("GB", p.display_size)


class TestVoiceRecorder(unittest.TestCase):
    def setUp(self):
        self.vr = VoiceRecorder()

    def test_initial_state(self):
        self.assertGreater(len(self.vr._recordings), 0)
        self.assertFalse(self.vr._is_recording)

    def test_select(self):
        self.vr.select(1)
        self.assertIsNotNone(self.vr.selected_recording)

    def test_total_duration(self):
        self.assertGreater(self.vr.total_duration, 0)

    def test_favorites(self):
        self.assertGreater(self.vr.favorites_count, 0)

    def test_toggle_favorite(self):
        self.vr.select(3)  # non-favorite
        self.vr.toggle_favorite()
        self.assertTrue(self.vr.selected_recording.is_favorite)

    def test_start_stop(self):
        self.vr.start_recording()
        self.assertTrue(self.vr._is_recording)
        self.vr._total_recorded_secs = 10
        rec = self.vr.stop_recording("Test Recording")
        self.assertIsNotNone(rec)
        self.assertFalse(self.vr._is_recording)

    def test_pause_resume(self):
        self.vr.start_recording()
        self.vr.pause_recording()
        self.assertTrue(self.vr._is_paused)
        self.vr.resume_recording()
        self.assertFalse(self.vr._is_paused)

    def test_delete(self):
        before = len(self.vr._recordings)
        self.assertTrue(self.vr.delete_recording(0))
        self.assertEqual(len(self.vr._recordings), before - 1)

    def test_delete_invalid(self):
        self.assertFalse(self.vr.delete_recording(99))

    def test_set_format(self):
        self.vr.set_format(RecordFormat.FLAC)
        self.assertEqual(self.vr._current_format, RecordFormat.FLAC)

    def test_set_noise(self):
        self.vr.set_noise_reduction(NoiseReduction.HIGH)
        self.assertEqual(self.vr._current_noise_reduction, NoiseReduction.HIGH)

    def test_render(self):
        lines = self.vr.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("RECORDER" in l for l in lines))

    def test_render_recording(self):
        self.vr.select(0)
        lines = self.vr.render_recording()
        self.assertGreater(len(lines), 2)

    def test_format_counts(self):
        counts = self.vr.format_counts
        self.assertIn("wav", counts)

    def test_display_duration(self):
        rec = Recording("Test", 3661, RecordFormat.WAV, 44100, 1, 128, NoiseReduction.OFF, time.time())
        self.assertIn(":", rec.display_duration)

    def test_waveform(self):
        rec = Recording("Test", 10, RecordFormat.WAV, 44100, 1, 128, NoiseReduction.OFF, time.time(), _amplitudes=[0.5] * 10)
        self.assertEqual(len(rec.waveform), 10)


if __name__ == "__main__":
    unittest.main()
