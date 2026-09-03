"""
Tests for Plugin Marketplace, System Profiler, and Remote Desktop.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.plugin_marketplace import (
    PluginMarketplace, Plugin, PluginReview,
    PluginStatus, PluginCategory
)
from ui.system_profiler import (
    SystemProfiler, HardwareInfo, BenchmarkResult, SystemComparison,
    BenchmarkCategory, TestStatus
)
from ui.remote_desktop import (
    RemoteDesktop, RemoteConnection, SessionRecording, ConnectionHistory,
    ConnectionProtocol, ConnectionStatus, ConnectionQuality
)


# ─── Plugin Marketplace Tests ────────────────────────────────────────────


class TestPluginMarketplace(unittest.TestCase):

    def setUp(self):
        self.pm = PluginMarketplace()

    def test_initial_state(self):
        self.assertEqual(self.pm.view_mode, "browse")
        self.assertGreater(len(self.pm.plugins), 0)

    def test_install_plugin(self):
        # Find an available plugin
        idx = next(i for i, p in enumerate(self.pm.plugins) if p.status == PluginStatus.AVAILABLE)
        self.assertTrue(self.pm.install_plugin(idx))
        self.assertEqual(self.pm.plugins[idx].status, PluginStatus.INSTALLED)

    def test_uninstall_plugin(self):
        idx = next(i for i, p in enumerate(self.pm.plugins) if p.status == PluginStatus.INSTALLED)
        self.assertTrue(self.pm.uninstall_plugin(idx))
        self.assertEqual(self.pm.plugins[idx].status, PluginStatus.AVAILABLE)

    def test_update_plugin(self):
        idx = next(i for i, p in enumerate(self.pm.plugins) if p.status == PluginStatus.UPDATE_AVAILABLE)
        self.assertTrue(self.pm.update_plugin(idx))
        self.assertEqual(self.pm.plugins[idx].status, PluginStatus.INSTALLED)

    def test_toggle_auto_update(self):
        initial = self.pm.plugins[0].auto_update
        self.pm.toggle_auto_update(0)
        self.assertNotEqual(self.pm.plugins[0].auto_update, initial)

    def test_add_review(self):
        review = self.pm.add_review(0, "testuser", 5, "Great plugin!", "Nice work")
        self.assertIsNotNone(review)
        self.assertEqual(review.rating, 5)

    def test_check_updates(self):
        updates = self.pm.check_updates()
        self.assertGreater(len(updates), 0)

    def test_update_all(self):
        count = self.pm.update_all()
        self.assertGreater(count, 0)

    def test_search(self):
        results = self.pm.search("git")
        self.assertGreater(len(results), 0)

    def test_sort_cycle(self):
        initial = self.pm._sort_by
        self.pm.cycle_sort()
        self.assertNotEqual(self.pm._sort_by, initial)

    def test_navigation(self):
        self.pm.select_down()
        self.assertEqual(self.pm.selected_index, 1)
        self.pm.select_up()
        self.assertEqual(self.pm.selected_index, 0)

    def test_render_browse(self):
        lines = self.pm.render_browse()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_installed(self):
        self.pm.set_view("installed")
        lines = self.pm.render_installed()
        self.assertIsInstance(lines, list)

    def test_render_updates(self):
        self.pm.set_view("updates")
        lines = self.pm.render_updates()
        self.assertIsInstance(lines, list)

    def test_render_detail(self):
        self.pm.set_view("plugin_detail")
        lines = self.pm.render_detail()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.pm.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")


class TestPlugin(unittest.TestCase):

    def test_display(self):
        p = Plugin("Test", "Author", "1.0", status=PluginStatus.INSTALLED)
        self.assertIn("Test", p.display)
        self.assertIn("✅", p.display)

    def test_rating_stars(self):
        p = Plugin("Test", "Author", "1.0", rating=4.5)
        self.assertIn("⭐", p.rating_stars)

    def test_size_str(self):
        p = Plugin("Test", "Author", "1.0", size_kb=2048)
        self.assertIn("MB", p.size_str)


class TestPluginReview(unittest.TestCase):

    def test_stars(self):
        r = PluginReview("user", 4, "Good", "Nice plugin")
        self.assertIn("⭐", r.stars)
        self.assertIn("☆", r.stars)


# ─── System Profiler Tests ───────────────────────────────────────────────


class TestSystemProfiler(unittest.TestCase):

    def setUp(self):
        self.profiler = SystemProfiler()

    def test_initial_state(self):
        self.assertEqual(self.profiler.view_mode, "overview")
        self.assertGreater(len(self.profiler.hardware.benchmarks), 0)

    def test_hardware_info(self):
        hw = self.profiler.hardware
        self.assertIn("AMD", hw.cpu_model)
        self.assertGreater(hw.cpu_cores, 0)

    def test_run_benchmark(self):
        self.assertTrue(self.profiler.run_benchmark(0))

    def test_run_all(self):
        count = self.profiler.run_all_benchmarks()
        self.assertGreaterEqual(count, 0)

    def test_export_report(self):
        report = self.profiler.export_report()
        self.assertIsInstance(report, str)
        self.assertIn("Nyrqis", report)

    def test_comparisons(self):
        comps = self.profiler.comparisons
        self.assertGreater(len(comps), 0)

    def test_navigation(self):
        self.profiler.set_view("benchmarks")
        self.profiler.select_down()
        self.assertEqual(self.profiler.selected_index, 1)
        self.profiler.select_up()
        self.assertEqual(self.profiler.selected_index, 0)

    def test_render_overview(self):
        lines = self.profiler.render_overview()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_benchmarks(self):
        self.profiler.set_view("benchmarks")
        lines = self.profiler.render_benchmarks()
        self.assertIsInstance(lines, list)

    def test_render_hardware(self):
        self.profiler.set_view("hardware")
        lines = self.profiler.render_hardware()
        self.assertIsInstance(lines, list)

    def test_render_comparison(self):
        self.profiler.set_view("comparison")
        lines = self.profiler.render_comparison()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.profiler.handle_key("b")
        self.assertEqual(result, "benchmarks")


class TestBenchmarkResult(unittest.TestCase):

    def test_score_str(self):
        bm = BenchmarkResult("Test", BenchmarkCategory.CPU, 28500, "pts")
        self.assertIn("K", bm.score_str)

    def test_bar(self):
        bm = BenchmarkResult("Test", BenchmarkCategory.CPU, 5000)
        bar = bm.bar
        self.assertIn("█", bar)


class TestHardwareInfo(unittest.TestCase):

    def test_completed_benchmarks(self):
        hw = HardwareInfo()
        hw.benchmarks = [
            BenchmarkResult("T1", BenchmarkCategory.CPU, status=TestStatus.COMPLETED),
            BenchmarkResult("T2", BenchmarkCategory.CPU, status=TestStatus.NOT_RUN),
        ]
        self.assertEqual(hw.completed_benchmarks, 1)


# ─── Remote Desktop Tests ────────────────────────────────────────────────


class TestRemoteDesktop(unittest.TestCase):

    def setUp(self):
        self.rd = RemoteDesktop()

    def test_initial_state(self):
        self.assertEqual(self.rd.view_mode, "connections")
        self.assertGreater(len(self.rd.connections), 0)
        self.assertGreater(len(self.rd.recordings), 0)
        self.assertGreater(len(self.rd.history), 0)

    def test_connect(self):
        self.assertTrue(self.rd.connect(0))
        self.assertEqual(self.rd.connections[0].status, ConnectionStatus.CONNECTED)

    def test_disconnect(self):
        self.rd.connect(0)
        self.assertTrue(self.rd.disconnect(0))
        self.assertEqual(self.rd.connections[0].status, ConnectionStatus.SAVED)

    def test_add_connection(self):
        conn = self.rd.add_connection("Test", "10.0.0.1", 5900)
        self.assertIsNotNone(conn)
        self.assertEqual(len(self.rd.connections), 7)

    def test_delete_connection(self):
        initial = len(self.rd.connections)
        self.assertTrue(self.rd.delete_connection(initial - 1))
        self.assertEqual(len(self.rd.connections), initial - 1)

    def test_connected_count(self):
        self.rd.connect(0)
        self.assertGreater(self.rd.connected_count, 0)

    def test_navigation(self):
        self.rd.select_down()
        self.assertEqual(self.rd.selected_index, 1)
        self.rd.select_up()
        self.assertEqual(self.rd.selected_index, 0)

    def test_render_connections(self):
        lines = self.rd.render_connections()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_recordings(self):
        self.rd.set_view("recordings")
        lines = self.rd.render_recordings()
        self.assertIsInstance(lines, list)

    def test_render_history(self):
        self.rd.set_view("history")
        lines = self.rd.render_history()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.rd.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")


class TestRemoteConnection(unittest.TestCase):

    def test_display(self):
        conn = RemoteConnection("Test", "10.0.0.1", 5900)
        self.assertIn("Test", conn.display)

    def test_address(self):
        conn = RemoteConnection("Test", "10.0.0.1", 5900)
        self.assertEqual(conn.address, "10.0.0.1:5900")


class TestSessionRecording(unittest.TestCase):

    def test_display(self):
        rec = SessionRecording("Test", "test.webm", 120, 5000)
        self.assertIn("Test", rec.display)
        self.assertIn("2:00", rec.duration_str)


class TestConnectionHistory(unittest.TestCase):

    def test_time_str(self):
        entry = ConnectionHistory("Test", "10.0.0.1", ConnectionProtocol.RDP,
                                   started_at=time.time() - 3600)
        self.assertIn(":", entry.time_str)


if __name__ == "__main__":
    unittest.main()
