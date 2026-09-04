import unittest
import time


class TestResourceMonitor(unittest.TestCase):
    def setUp(self):
        from ui.resource_monitor import ResourceMonitor, ProcessSortBy
        self.rm = ResourceMonitor()
        self.PSB = ProcessSortBy

    def test_initial_state(self):
        self.assertGreater(len(self.rm.processes), 0)
        self.assertIsNotNone(self.rm.memory)
        self.assertIsNotNone(self.rm.network)

    def test_get_sorted_processes(self):
        procs = self.rm.get_sorted_processes()
        self.assertGreater(len(procs), 0)
        self.assertGreaterEqual(procs[0].cpu_percent, procs[-1].cpu_percent)

    def test_sort_by_memory(self):
        self.rm.sort_by = self.PSB.MEMORY
        procs = self.rm.get_sorted_processes()
        self.assertGreaterEqual(procs[0].memory_mb, procs[-1].memory_mb)

    def test_filter_by_user(self):
        self.rm.filter_user = "zeus"
        procs = self.rm.get_sorted_processes()
        for p in procs:
            self.assertEqual(p.user, "zeus")
        self.rm.filter_user = ""

    def test_kill_process(self):
        initial = len(self.rm.processes)
        result = self.rm.kill_process(1)
        self.assertTrue(result)
        self.assertEqual(len(self.rm.processes), initial - 1)

    def test_search_processes(self):
        results = self.rm.search_processes("firefox")
        self.assertGreater(len(results), 0)

    def test_get_top_cpu(self):
        top = self.rm.get_top_cpu(3)
        self.assertEqual(len(top), 3)

    def test_get_top_memory(self):
        top = self.rm.get_top_memory(3)
        self.assertEqual(len(top), 3)

    def test_get_stats(self):
        stats = self.rm.get_stats()
        self.assertIn("processes", stats)
        self.assertIn("cpu_usage", stats)

    def test_cpu_history(self):
        self.assertGreater(len(self.rm.cpu_history), 0)

    def test_memory_usage_bar(self):
        bar = self.rm.memory.usage_bar
        self.assertEqual(len(bar), 20)

    def test_network_rx_display(self):
        display = self.rm.network.rx_display
        self.assertIn("MB/s", display)

    def test_process_cpu_bar(self):
        from ui.resource_monitor import ResourceProcess
        p = ResourceProcess(cpu_percent=50.0)
        bar = p.cpu_bar
        self.assertEqual(len(bar), 20)


class TestFileManager(unittest.TestCase):
    def setUp(self):
        from ui.file_manager import FileManager, ViewMode
        self.fm = FileManager()
        self.VM = ViewMode

    def test_initial_state(self):
        self.assertGreater(len(self.fm.tabs), 0)
        self.assertGreater(len(self.fm.bookmarks), 0)
        self.assertIsNotNone(self.fm.current_tab)

    def test_new_tab(self):
        tab = self.fm.new_tab("/tmp")
        self.assertIn(tab, self.fm.tabs)

    def test_close_tab(self):
        tab = self.fm.new_tab()
        result = self.fm.close_tab(tab.id)
        self.assertTrue(result)

    def test_switch_tab(self):
        result = self.fm.switch_tab(self.fm.tabs[1].id)
        self.assertTrue(result)
        self.assertEqual(self.fm.current_tab.id, self.fm.tabs[1].id)

    def test_navigate_to(self):
        result = self.fm.navigate_to("/tmp")
        self.assertTrue(result)
        self.assertEqual(self.fm.current_tab.path, "/tmp")

    def test_get_files(self):
        files = self.fm.get_files_for_path("/home/zeus")
        self.assertGreater(len(files), 0)

    def test_select_file(self):
        result = self.fm.select_file("Desktop")
        self.assertTrue(result)
        self.assertIn("Desktop", self.fm.current_tab.selected_files)

    def test_copy_paste(self):
        self.fm.copy_files(["file1.txt", "file2.txt"])
        self.assertEqual(len(self.fm.clipboard_files), 2)
        count = self.fm.paste_files()
        self.assertEqual(count, 2)

    def test_add_bookmark(self):
        bm = self.fm.add_bookmark("Test", "/test")
        self.assertEqual(bm.name, "Test")
        self.assertIn(bm, self.fm.bookmarks)

    def test_remove_bookmark(self):
        result = self.fm.remove_bookmark("Root")
        self.assertTrue(result)

    def test_get_stats(self):
        stats = self.fm.get_stats()
        self.assertIn("tabs", stats)
        self.assertIn("bookmarks", stats)

    def test_file_entry_icon(self):
        from ui.file_manager import FileEntry, FileType
        f = FileEntry(name="test.py", file_type=FileType.FILE)
        self.assertEqual(f.icon, "🐍")

    def test_file_entry_size_display(self):
        from ui.file_manager import FileEntry, FileType
        f = FileEntry(name="test.txt", file_type=FileType.FILE, size_bytes=500)
        self.assertEqual(f.size_display, "500 B")
        f.size_bytes = 2048
        self.assertEqual(f.size_display, "2.0 KB")


class TestScreenRecorder(unittest.TestCase):
    def setUp(self):
        from ui.screen_recorder import ScreenRecorder, RecordingStatus
        self.sr = ScreenRecorder()
        self.RS = RecordingStatus

    def test_initial_state(self):
        self.assertGreater(len(self.sr.profiles), 0)
        self.assertGreater(len(self.sr.recordings), 0)
        self.assertIsNotNone(self.sr.active_profile)

    def test_start_recording(self):
        rec = self.sr.start_recording()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, self.RS.RECORDING)
        self.assertIsNotNone(self.sr.current_recording)

    def test_stop_recording(self):
        self.sr.start_recording()
        rec = self.sr.stop_recording()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, self.RS.STOPPED)
        self.assertIsNone(self.sr.current_recording)

    def test_pause_resume(self):
        self.sr.start_recording()
        self.sr.pause_recording()
        self.assertEqual(self.sr.current_recording.status, self.RS.PAUSED)
        self.sr.resume_recording()
        self.assertEqual(self.sr.current_recording.status, self.RS.RECORDING)
        self.sr.stop_recording()

    def test_set_profile(self):
        result = self.sr.set_profile("Lossless")
        self.assertTrue(result)
        self.assertEqual(self.sr.active_profile.name, "Lossless")

    def test_get_recent_recordings(self):
        recs = self.sr.get_recent_recordings(2)
        self.assertEqual(len(recs), 2)

    def test_get_stats(self):
        stats = self.sr.get_stats()
        self.assertIn("profiles", stats)
        self.assertIn("recordings", stats)

    def test_recording_status_icon(self):
        from ui.screen_recorder import Recording, RecordingProfile
        r = Recording(status=self.RS.RECORDING, profile=RecordingProfile(name="test"))
        self.assertEqual(r.status_icon, "🔴")

    def test_recording_duration_display(self):
        from ui.screen_recorder import Recording, RecordingProfile
        r = Recording(duration_s=5.5, profile=RecordingProfile(name="test"))
        self.assertIn("s", r.duration_display)
        r.duration_s = 90
        self.assertIn("m", r.duration_display)

    def test_recording_file_size_display(self):
        from ui.screen_recorder import Recording, RecordingProfile
        r = Recording(file_size_bytes=500, profile=RecordingProfile(name="test"))
        self.assertEqual(r.file_size_display, "500 B")
        r.file_size_bytes = 2048 * 1024
        self.assertIn("MB", r.file_size_display)

    def test_profile_description(self):
        from ui.screen_recorder import RecordingProfile
        p = RecordingProfile(name="test", resolution="1920x1080", fps=30)
        self.assertIn("1920x1080", p.description)
        self.assertIn("30fps", p.description)


if __name__ == "__main__":
    unittest.main()
