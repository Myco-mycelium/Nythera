"""Tests for DrumKit, CompressionTool, SystemInfoDashboard"""
import time
import unittest

from ui.drum_kit import DrumKit, DrumPad, HitEvent, DrumType, VelocityCurve, RecordingMode
from ui.compression_tool import CompressionTool, CompressibleFile, BatchJob, CompressionAlgo, CompressionLevel, OperationStatus, FileCategory
from ui.system_info import SystemInfoDashboard, HardwareSpec, BenchmarkResult, SystemInfo, HardwareCategory, BenchmarkType


class TestDrumKit(unittest.TestCase):
    def setUp(self):
        self.dk = DrumKit()

    def test_initial_state(self):
        self.assertEqual(self.dk.total_pads, 16)
        self.assertFalse(self.dk._is_recording)

    def test_select_pad(self):
        self.dk.select_pad(5)
        self.assertEqual(self.dk._selected_pad, 5)
        self.assertEqual(self.dk.selected_pad.drum_type, DrumType.CRASH)

    def test_select_invalid(self):
        self.dk.select_pad(99)
        self.assertEqual(self.dk._selected_pad, 0)

    def test_hit_pad(self):
        before = self.dk.total_hits
        self.dk.hit_pad(0, 110)
        self.assertEqual(self.dk.total_hits, before + 1)

    def test_hit_updates_velocity(self):
        self.dk.hit_pad(3, 85)
        self.assertEqual(self.dk._pads[3].velocity, 85)

    def test_start_stop_recording(self):
        self.dk.start_recording()
        self.assertTrue(self.dk._is_recording)
        self.dk.stop_recording()
        self.assertFalse(self.dk._is_recording)

    def test_recording_captures_hits(self):
        self.dk.start_recording()
        self.dk.hit_pad(0, 100)
        self.dk.hit_pad(1, 90)
        self.assertEqual(len(self.dk._recording), 2)

    def test_recording_duration(self):
        self.dk._is_recording = True
        self.dk._record_start = time.time() - 65
        self.assertEqual(self.dk.recording_duration, "1:05")

    def test_toggle_mute(self):
        self.dk.toggle_mute(0)
        self.assertTrue(self.dk._pads[0].is_muted)
        self.dk.toggle_mute(0)
        self.assertFalse(self.dk._pads[0].is_muted)

    def test_set_pad_volume(self):
        self.dk.set_pad_volume(5, 0.5)
        self.assertEqual(self.dk._pads[5].volume, 0.5)

    def test_set_pad_pan(self):
        self.dk.set_pad_pan(5, -0.8)
        self.assertEqual(self.dk._pads[5].pan, -0.8)

    def test_active_pads(self):
        self.assertGreater(self.dk.active_pads, 0)

    def test_pad_velocity_bar(self):
        pad = self.dk._pads[0]
        pad.velocity = 80
        self.assertIn("█", pad.velocity_bar)

    def test_pad_volume_bar(self):
        pad = self.dk._pads[0]
        self.assertIn("█", pad.volume_bar)

    def test_pad_pan_display(self):
        pad = self.dk._pads[0]
        self.assertIn("■", pad.pan_display)

    def test_render(self):
        lines = self.dk.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("DRUM KIT" in l for l in lines))

    def test_render_pads_grid(self):
        lines = self.dk.render_pads_grid()
        self.assertGreater(len(lines), 3)

    def test_render_recording(self):
        lines = self.dk.render_recording()
        self.assertGreaterEqual(len(lines), 3)


class TestCompressionTool(unittest.TestCase):
    def setUp(self):
        self.ct = CompressionTool()

    def test_initial_state(self):
        self.assertGreater(len(self.ct._files), 0)
        self.assertEqual(self.ct._selected_file, 0)

    def test_select_file(self):
        self.ct.select_file(1)
        self.assertEqual(self.ct._selected_file, 1)

    def test_select_invalid(self):
        self.ct.select_file(99)
        self.assertEqual(self.ct._selected_file, 0)

    def test_total_files(self):
        self.assertEqual(self.ct.total_files, 8)

    def test_total_original_size(self):
        self.assertGreater(self.ct.total_original_size, 0)

    def test_completed_count(self):
        self.assertGreater(self.ct.completed_count, 0)

    def test_pending_count(self):
        self.assertGreater(self.ct.pending_count, 0)

    def test_overall_ratio(self):
        self.assertGreater(self.ct.overall_ratio, 0)

    def test_compress_file(self):
        # Find a pending file
        for i, f in enumerate(self.ct._files):
            if f.status == OperationStatus.PENDING:
                self.assertTrue(self.ct.compress_file(i))
                self.assertEqual(f.status, OperationStatus.COMPLETE)
                break

    def test_add_file(self):
        before = self.ct.total_files
        self.ct.add_file("/tmp/test.txt", 1024)
        self.assertEqual(self.ct.total_files, before + 1)

    def test_delete_file(self):
        before = self.ct.total_files
        self.ct.delete_file(0)
        self.assertEqual(self.ct.total_files, before - 1)

    def test_delete_invalid(self):
        self.assertFalse(self.ct.delete_file(99))

    def test_batch_jobs(self):
        self.assertGreater(len(self.ct._batch_jobs), 0)

    def test_file_ratio_bar(self):
        f = self.ct._files[0]
        self.assertIn("█", f.ratio_bar)

    def test_file_display(self):
        f = self.ct._files[0]
        self.assertIn("GB", f.display_original)

    def test_render(self):
        lines = self.ct.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("COMPRESSION" in l for l in lines))

    def test_render_file_detail(self):
        self.ct.select_file(0)
        lines = self.ct.render_file_detail()
        self.assertGreater(len(lines), 5)

    def test_render_algorithms(self):
        lines = self.ct.render_algorithms()
        self.assertGreater(len(lines), 5)


class TestSystemInfoDashboard(unittest.TestCase):
    def setUp(self):
        self.sid = SystemInfoDashboard()

    def test_initial_state(self):
        self.assertGreater(len(self.sid._hardware), 0)
        self.assertGreater(len(self.sid._benchmarks), 0)
        self.assertIsNotNone(self.sid._system)

    def test_select_hardware(self):
        self.sid.select_hardware(1)
        self.assertEqual(self.sid._selected_hardware, 1)

    def test_select_benchmark(self):
        self.sid.select_benchmark(1)
        self.assertEqual(self.sid._selected_benchmark, 1)

    def test_hardware_count(self):
        self.assertEqual(len(self.sid._hardware), 8)

    def test_benchmark_count(self):
        self.assertEqual(len(self.sid._benchmarks), 10)

    def test_overall_score(self):
        self.assertGreater(self.sid.overall_score, 0)

    def test_system_info(self):
        s = self.sid._system
        self.assertIn("d", s.uptime_display)

    def test_hardware_specs(self):
        h = self.sid._hardware[0]
        self.assertIn("Cores", h.specs)

    def test_benchmark_score(self):
        b = self.sid._benchmarks[0]
        self.assertIn("pts", b.score_display)

    def test_benchmark_percentile(self):
        b = self.sid._benchmarks[0]
        self.assertIn(b.percentile_display, ["Top 10%", "Above Average", "Below Average"])

    def test_render(self):
        lines = self.sid.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("INFORMATION" in l for l in lines))

    def test_render_hardware_detail(self):
        self.sid.select_hardware(0)
        lines = self.sid.render_hardware_detail()
        self.assertGreater(len(lines), 3)

    def test_render_benchmark_detail(self):
        self.sid.select_benchmark(0)
        lines = self.sid.render_benchmark_detail()
        self.assertGreater(len(lines), 3)

    def test_render_benchmarks(self):
        lines = self.sid.render_benchmarks()
        self.assertGreater(len(lines), 5)

    def test_render_system_report(self):
        lines = self.sid.render_system_report()
        self.assertGreater(len(lines), 5)

    def test_hardware_categories(self):
        cats = set(h.category for h in self.sid._hardware)
        self.assertIn(HardwareCategory.CPU, cats)
        self.assertIn(HardwareCategory.GPU, cats)

    def test_hardware_icon(self):
        h = self.sid._hardware[0]
        self.assertIn(h.icon, ["🔲", "🎮", "🧠", "💾", "📟", "🌐", "🔊", "🖥️", "⌨️"])


if __name__ == "__main__":
    unittest.main()
