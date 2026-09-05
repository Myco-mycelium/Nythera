"""Tests for TerminalEmulator, ScreenRecorder, FileHasher"""
import time
import unittest

from ui.terminal_emulator import TerminalEmulator, TerminalTab, TerminalTheme, TerminalProfile, THEMES, SplitDirection, HistoryEntry
from ui.screen_recorder import ScreenRecorder, RecordingSession, RecordPreset, RecordFormat, RecordArea, RecordStatus, AudioSource, OverlayType
from ui.file_hasher import FileHasher, FileEntry, BatchJob, IntegrityCheck, HashAlgorithm, VerifyStatus, BatchStatus


class TestTerminalEmulator(unittest.TestCase):
    def setUp(self):
        self.te = TerminalEmulator()

    def test_initial_state(self):
        self.assertGreater(len(self.te._tabs), 0)
        self.assertEqual(self.te._selected_tab, 0)

    def test_select_tab(self):
        self.te.select_tab(1)
        self.assertEqual(self.te._selected_tab, 1)
        self.assertIn("ssh", self.te.selected_tab.title)

    def test_select_invalid(self):
        self.te.select_tab(99)
        self.assertEqual(self.te._selected_tab, 0)

    def test_new_tab(self):
        before = self.te.tab_count
        tab = self.te.new_tab("test", "/tmp")
        self.assertEqual(self.te.tab_count, before + 1)
        self.assertEqual(tab.title, "test")

    def test_close_tab(self):
        self.te.new_tab("close-me", "/tmp")
        idx = self.te.tab_count - 1
        self.assertTrue(self.te.close_tab(idx))
        self.assertLess(self.te.tab_count, idx + 2)

    def test_execute_command(self):
        result = self.te.execute_command("ls")
        self.assertIn("Desktop", result)

    def test_execute_unknown(self):
        result = self.te.execute_command("foobarnonexistent")
        self.assertIn("command not found", result)

    def test_execute_clear(self):
        self.te.execute_command("clear")
        tab = self.te.selected_tab
        # clear empties buffer (may leave empty string from trailing append)
        self.assertTrue(len(tab.output_buffer) <= 1)

    def test_execute_help(self):
        self.te.execute_command("help")
        tab = self.te.selected_tab
        output_text = " ".join(tab.output_buffer)
        self.assertIn("Available", output_text)

    def test_search_history(self):
        results = self.te.search_history("git")
        self.assertGreater(len(results), 0)

    def test_set_theme(self):
        self.te.set_theme(TerminalProfile.MONOKAI)
        self.assertEqual(self.te._current_theme, TerminalProfile.MONOKAI)
        self.assertEqual(self.te.theme.name, "Monokai")

    def test_themes_exist(self):
        self.assertEqual(len(THEMES), 8)

    def test_render(self):
        lines = self.te.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("TERMINAL" in l for l in lines))

    def test_render_terminal(self):
        lines = self.te.render_terminal()
        self.assertGreater(len(lines), 2)

    def test_render_theme_preview(self):
        lines = self.te.render_theme_preview()
        self.assertGreater(len(lines), 5)

    def test_tab_display_title(self):
        tab = TerminalTab("zsh", "/home/user")
        self.assertEqual(tab.display_title, "zsh")

    def test_tab_history_count(self):
        self.assertGreater(self.te.history_count, 0)

    def test_failed_commands(self):
        self.assertGreaterEqual(self.te.failed_commands, 0)


class TestScreenRecorder(unittest.TestCase):
    def setUp(self):
        self.sr = ScreenRecorder()

    def test_initial_state(self):
        self.assertEqual(self.sr._status, RecordStatus.IDLE)
        self.assertGreater(len(self.sr._sessions), 0)

    def test_select(self):
        self.sr.select(1)
        self.assertIsNotNone(self.sr.selected_session)

    def test_total_recordings(self):
        self.assertGreater(self.sr.total_recordings, 0)

    def test_total_duration(self):
        self.assertGreater(self.sr.total_duration_secs, 0)
        self.assertIn("h", self.sr.total_duration_display)

    def test_total_size(self):
        self.assertGreater(self.sr.total_size, 0)

    def test_start_stop(self):
        self.sr.start_recording()
        self.assertTrue(self.sr.is_recording)
        self.sr._rec_start = time.time() - 10
        session = self.sr.stop_recording("Test")
        self.assertIsNotNone(session)
        self.assertEqual(self.sr._status, RecordStatus.IDLE)

    def test_pause_resume(self):
        self.sr.start_recording()
        self.sr.pause_recording()
        self.assertEqual(self.sr._status, RecordStatus.PAUSED)
        self.sr.resume_recording()
        self.assertTrue(self.sr.is_recording)

    def test_delete_session(self):
        before = self.sr.total_recordings
        self.assertTrue(self.sr.delete_session(0))
        self.assertEqual(self.sr.total_recordings, before - 1)

    def test_delete_invalid(self):
        self.assertFalse(self.sr.delete_session(99))

    def test_set_area(self):
        self.sr.set_area(RecordArea.WINDOW)
        self.assertEqual(self.sr._current_area, RecordArea.WINDOW)

    def test_set_format(self):
        self.sr.set_format(RecordFormat.WEBM)
        self.assertEqual(self.sr._current_format, RecordFormat.WEBM)

    def test_set_fps(self):
        self.sr.set_fps(120)
        self.assertEqual(self.sr._current_fps, 120)

    def test_toggle_overlay(self):
        before = len(self.sr._current_overlays)
        self.sr.toggle_overlay(OverlayType.WATERMARK)
        self.assertEqual(len(self.sr._current_overlays), before + 1)

    def test_render(self):
        lines = self.sr.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("RECORDER" in l for l in lines))

    def test_render_session_detail(self):
        self.sr.select(0)
        lines = self.sr.render_session_detail()
        self.assertGreater(len(lines), 2)

    def test_presets(self):
        self.assertEqual(len(self.sr._presets), 4)


class TestFileHasher(unittest.TestCase):
    def setUp(self):
        self.fh = FileHasher()

    def test_initial_state(self):
        self.assertGreater(len(self.fh._files), 0)
        self.assertEqual(self.fh._selected, 0)

    def test_select(self):
        self.fh.select(2)
        self.assertIsNotNone(self.fh.selected_file)

    def test_select_invalid(self):
        self.fh.select(99)
        self.assertEqual(self.fh._selected, 0)

    def test_total_files(self):
        self.assertGreater(self.fh.total_files, 0)

    def test_total_size(self):
        self.assertGreater(self.fh.total_size, 0)

    def test_verified_count(self):
        self.assertGreater(self.fh.verified_count, 0)

    def test_mismatch_count(self):
        self.assertGreater(self.fh.mismatch_count, 0)

    def test_bookmarked(self):
        self.assertGreater(self.fh.bookmarked_count, 0)

    def test_status_counts(self):
        counts = self.fh.status_counts
        self.assertIn("match", counts)

    def test_algorithm_counts(self):
        counts = self.fh.algorithm_counts
        self.assertIn("sha256", counts)

    def test_compute_hash(self):
        result = FileHasher.compute_hash("test", HashAlgorithm.SHA256)
        self.assertEqual(len(result), 64)

    def test_compute_hash_md5(self):
        result = FileHasher.compute_hash("test", HashAlgorithm.MD5)
        self.assertEqual(len(result), 32)

    def test_hash_file(self):
        f = self.fh._files[0]
        result = self.fh.hash_file(f, HashAlgorithm.BLAKE2)
        self.assertEqual(len(result), 128)
        self.assertIn(HashAlgorithm.BLAKE2, f.hashes)

    def test_verify_file(self):
        f = self.fh._files[1]
        self.assertTrue(self.fh.verify_file(f))

    def test_compare_hashes(self):
        self.assertTrue(self.fh.compare_hashes("abc123", "abc123"))
        self.assertFalse(self.fh.compare_hashes("abc123", "def456"))

    def test_toggle_bookmark(self):
        self.fh.select(3)
        before = self.fh.selected_file.is_bookmarked
        self.fh.toggle_bookmark()
        self.assertNotEqual(self.fh.selected_file.is_bookmarked, before)

    def test_add_remove(self):
        entry = self.fh.add_file("/tmp/test.txt", 1024)
        self.assertEqual(self.fh._files[-1].path, "/tmp/test.txt")
        idx = len(self.fh._files) - 1
        self.assertTrue(self.fh.remove_file(idx))

    def test_remove_invalid(self):
        self.assertFalse(self.fh.remove_file(99))

    def test_export_hashes(self):
        output = self.fh.export_hashes(HashAlgorithm.SHA256)
        self.assertGreater(len(output), 0)
        self.assertIn("/home/user", output)

    def test_render(self):
        lines = self.fh.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("HASHER" in l for l in lines))

    def test_render_file_detail(self):
        self.fh.select(0)
        lines = self.fh.render_file_detail()
        self.assertGreater(len(lines), 2)

    def test_render_history(self):
        lines = self.fh.render_verify_history()
        self.assertGreater(len(lines), 2)

    def test_file_entry_properties(self):
        f = FileEntry("/path/to/file.iso", 4_294_967_296, time.time())
        self.assertIn("GB", f.display_size)
        self.assertEqual(f.filename, "file.iso")
        self.assertEqual(f.extension, "iso")


if __name__ == "__main__":
    unittest.main()
