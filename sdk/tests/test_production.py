"""Tests for Nyrqis SDK production hardening modules."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add SDK to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nyrqis_sdk.telemetry import Telemetry, TelemetryStatus
from nyrqis_sdk.performance import Timer, MemoryTracker, FrameRateMonitor, PerformanceBudget
from nyrqis_sdk.restore import RestoreManager, RestorePoint


class TestTelemetry(unittest.TestCase):
    """Test telemetry and crash reporting."""

    def setUp(self):
        """Create temp directory for test data."""
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-telemetry-")
        self.telemetry = Telemetry(data_dir=self.tmpdir, auto_flush=False)

    def tearDown(self):
        """Clean up test data."""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_initial_status(self):
        """Telemetry starts disabled."""
        self.assertEqual(self.telemetry.status, TelemetryStatus.DISABLED)
        self.assertFalse(self.telemetry.is_enabled)

    def test_enable_disable(self):
        """Telemetry can be enabled and disabled."""
        self.assertTrue(self.telemetry.enable())
        self.assertTrue(self.telemetry.is_enabled)
        self.telemetry.disable()
        self.assertFalse(self.telemetry.is_enabled)

    def test_record_metric(self):
        """Metrics can be recorded when enabled."""
        self.telemetry.enable()
        self.telemetry.metric("test_metric", 42.0)
        self.assertEqual(self.telemetry.metric_count, 1)

    def test_metric_ignored_when_disabled(self):
        """Metrics are ignored when disabled."""
        self.telemetry.metric("test_metric", 42.0)
        self.assertEqual(self.telemetry.metric_count, 0)

    def test_report_error(self):
        """Errors can be reported when enabled."""
        self.telemetry.enable()
        self.telemetry.error("test_error", "Test message")
        self.assertEqual(self.telemetry.crash_report_count, 1)

    def test_flush(self):
        """Data can be flushed to disk."""
        self.telemetry.enable()
        self.telemetry.metric("test_metric", 42.0)
        self.telemetry.error("test_error", "Test message")
        
        count = self.telemetry.flush()
        self.assertEqual(count, 2)

    def test_system_info_collected(self):
        """System info is collected."""
        self.assertIn("os", self.telemetry._system_info)
        self.assertIn("arch", self.telemetry._system_info)


class TestTimer(unittest.TestCase):
    """Test timing utilities."""

    def test_context_manager(self):
        """Timer works as context manager."""
        with Timer("test") as timer:
            time.sleep(0.01)
        
        self.assertIsNotNone(timer.result)
        self.assertGreater(timer.result.duration_ms, 0)

    def test_decorator(self):
        """Timer works as decorator."""
        @Timer("test_func")
        def my_func():
            time.sleep(0.01)
            return 42
        
        result = my_func()
        self.assertEqual(result, 42)

    def test_callback(self):
        """Timer callback is called."""
        results = []
        
        with Timer("test", callback=results.append):
            time.sleep(0.01)
        
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0].duration_ms, 0)


class TestMemoryTracker(unittest.TestCase):
    """Test memory tracking."""

    def test_snapshot(self):
        """Memory snapshots can be taken."""
        tracker = MemoryTracker()
        rss = tracker.snapshot("test")
        self.assertGreater(rss, 0)

    def test_delta(self):
        """Memory delta can be calculated."""
        tracker = MemoryTracker()
        tracker.snapshot("start")
        
        # Allocate some memory
        data = [0] * (1024 * 1024)  # 1M integers
        
        tracker.snapshot("end")
        delta = tracker.delta("start", "end")
        self.assertIsNotNone(delta)
        self.assertGreater(delta, 0)

    def test_summary(self):
        """Summary provides statistics."""
        tracker = MemoryTracker()
        tracker.snapshot("a")
        tracker.snapshot("b")
        
        summary = tracker.summary()
        self.assertEqual(summary["snapshots"], 2)
        self.assertIn("min_mb", summary)
        self.assertIn("max_mb", summary)


class TestFrameRateMonitor(unittest.TestCase):
    """Test frame rate monitoring."""

    def test_tick(self):
        """Frame rate monitor records ticks."""
        monitor = FrameRateMonitor()
        monitor.tick()
        time.sleep(0.05)
        monitor.tick()
        time.sleep(0.05)
        monitor.tick()
        
        self.assertGreater(monitor.fps, 0)

    def test_frame_time(self):
        """Frame time is calculated."""
        monitor = FrameRateMonitor()
        monitor.tick()
        time.sleep(0.01)
        monitor.tick()
        
        self.assertGreater(monitor.frame_time_ms, 0)

    def test_reset(self):
        """Monitor can be reset."""
        monitor = FrameRateMonitor()
        monitor.tick()
        time.sleep(0.01)
        monitor.tick()
        
        monitor.reset()
        self.assertEqual(monitor.fps, 0.0)

    def test_summary(self):
        """Summary provides statistics."""
        monitor = FrameRateMonitor()
        monitor.tick()
        time.sleep(0.01)
        monitor.tick()
        
        summary = monitor.summary()
        self.assertIn("fps", summary)
        self.assertIn("frame_time_ms", summary)


class TestPerformanceBudget(unittest.TestCase):
    """Test performance budget tracking."""

    def test_set_budget(self):
        """Budgets can be set."""
        budget = PerformanceBudget()
        budget.set("test_metric", 100.0)
        
        summary = budget.summary()
        self.assertIn("test_metric", summary["budgets"])

    def test_check_within_budget(self):
        """Values within budget pass."""
        budget = PerformanceBudget()
        budget.set("test_metric", 100.0)
        
        self.assertTrue(budget.check("test_metric", 50.0))
        self.assertEqual(budget.violation_count, 0)

    def test_check_exceeds_budget(self):
        """Values exceeding budget fail."""
        budget = PerformanceBudget()
        budget.set("test_metric", 100.0)
        
        self.assertFalse(budget.check("test_metric", 150.0))
        self.assertEqual(budget.violation_count, 1)


class TestRestoreManager(unittest.TestCase):
    """Test restore point management."""

    def setUp(self):
        """Create temp directory for test data."""
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-restore-")
        self.manager = RestoreManager(state_dir=self.tmpdir, max_points=3)

    def tearDown(self):
        """Clean up test data."""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_restore_point(self):
        """Restore points can be created."""
        point = self.manager.create("Test point")
        self.assertIsNotNone(point)
        self.assertEqual(point.description, "Test point")
        self.assertEqual(self.manager.point_count, 1)

    def test_list_points(self):
        """Restore points can be listed."""
        self.manager.create("Point 1")
        time.sleep(0.01)  # Ensure different timestamps
        self.manager.create("Point 2")
        
        points = self.manager.list_points()
        self.assertEqual(len(points), 2)

    def test_delete_point(self):
        """Restore points can be deleted."""
        point = self.manager.create("Test point")
        self.assertTrue(self.manager.delete_point(point.id))
        self.assertEqual(self.manager.point_count, 0)

    def test_max_points_cleanup(self):
        """Old points are cleaned up when over limit."""
        for i in range(5):
            self.manager.create(f"Point {i}")
        
        self.assertEqual(self.manager.point_count, 3)

    def test_point_metadata(self):
        """Restore points can have metadata."""
        point = self.manager.create("Test", metadata={"key": "value"})
        self.assertEqual(point.metadata["key"], "value")


if __name__ == "__main__":
    unittest.main()
