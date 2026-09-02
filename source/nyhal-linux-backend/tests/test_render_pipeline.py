"""test_render_pipeline — Tests for the GPU rendering pipeline and multi-monitor.

References:
    - ADR-0026 Phase 3: GPU acceleration
    - ui/render_pipeline.py
    - ui/multi_monitor.py
"""

from __future__ import annotations

import os
import sys
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


class TestRenderPipeline(unittest.TestCase):
    """Tests for the GPU rendering pipeline."""

    def test_render_config_defaults(self):
        """RenderConfig has sensible defaults."""
        from ui.render_pipeline import RenderConfig
        config = RenderConfig()
        self.assertEqual(config.width, 1920)
        self.assertEqual(config.height, 1080)
        self.assertTrue(config.use_gbm)
        self.assertTrue(config.use_egl)
        self.assertTrue(config.use_drm)

    def test_render_state_defaults(self):
        """RenderState starts uninitialized."""
        from ui.render_pipeline import RenderState
        state = RenderState()
        self.assertFalse(state.initialized)
        self.assertEqual(state.frame_count, 0)

    def test_render_pipeline_create(self):
        """RenderPipeline can be created."""
        from ui.render_pipeline import RenderPipeline, RenderConfig
        config = RenderConfig(width=800, height=600)
        pipeline = RenderPipeline(config)
        self.assertEqual(pipeline.config.width, 800)
        self.assertEqual(pipeline.config.height, 600)

    def test_render_pipeline_context_manager(self):
        """RenderPipeline works as a context manager."""
        from ui.render_pipeline import RenderPipeline, RenderConfig
        config = RenderConfig(width=640, height=480)
        with RenderPipeline(config) as pipeline:
            self.assertIsNotNone(pipeline)

    def test_render_pipeline_stats(self):
        """RenderPipeline provides stats."""
        from ui.render_pipeline import RenderPipeline
        pipeline = RenderPipeline()
        stats = pipeline.get_stats()
        self.assertIn("frame_count", stats)
        self.assertIn("fps", stats)
        self.assertFalse(stats["initialized"])

    def test_render_pipeline_cleanup_idempotent(self):
        """Cleanup is idempotent."""
        from ui.render_pipeline import RenderPipeline
        pipeline = RenderPipeline()
        pipeline.cleanup()  # should not raise
        pipeline.cleanup()  # second call should be safe


class TestMultiMonitor(unittest.TestCase):
    """Tests for the multi-monitor manager."""

    def test_manager_create(self):
        """MultiMonitorManager can be created."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        self.assertEqual(len(manager.outputs), 0)

    def test_add_output(self):
        """Can add an output."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        output = manager.add_output(1920, 1080, "test-monitor")
        self.assertEqual(output.width, 1920)
        self.assertEqual(output.height, 1080)
        self.assertEqual(output.name, "test-monitor")
        self.assertTrue(output.primary)  # first output is primary

    def test_add_multiple_outputs(self):
        """Can add multiple outputs."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        out1 = manager.add_output(1920, 1080, "monitor-1")
        out2 = manager.add_output(2560, 1440, "monitor-2")
        self.assertFalse(out2.primary)  # second is not primary
        self.assertEqual(manager.get_output_count(), 2)

    def test_remove_output(self):
        """Can remove an output."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        out1 = manager.add_output(1920, 1080)
        out2 = manager.add_output(2560, 1440)
        migrated = manager.remove_output(out1.id)
        self.assertIsInstance(migrated, list)
        self.assertEqual(manager.get_output_count(), 1)

    def test_remove_nonexistent_output(self):
        """Removing nonexistent output returns empty list."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        self.assertEqual(manager.remove_output(999), [])

    def test_bind_workspace(self):
        """Can bind workspace to output."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        out = manager.add_output(1920, 1080)
        self.assertTrue(manager.bind_workspace(0, out.id))
        
        result = manager.get_output_for_workspace(0)
        self.assertIsNotNone(result)
        self.assertEqual(result.id, out.id)

    def test_bind_workspace_rebind(self):
        """Rebinding workspace moves it to new output."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        out1 = manager.add_output(1920, 1080)
        out2 = manager.add_output(2560, 1440)
        
        manager.bind_workspace(0, out1.id)
        manager.bind_workspace(0, out2.id)  # rebind
        
        result = manager.get_output_for_workspace(0)
        self.assertEqual(result.id, out2.id)

    def test_bind_workspace_invalid_output(self):
        """Binding to nonexistent output fails."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        self.assertFalse(manager.bind_workspace(0, 999))

    def test_get_output_for_unbound_workspace(self):
        """Unbound workspace returns None."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        self.assertIsNone(manager.get_output_for_workspace(0))

    def test_get_primary_output(self):
        """First output is primary."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        out = manager.add_output(1920, 1080)
        primary = manager.get_primary_output()
        self.assertIsNotNone(primary)
        self.assertEqual(primary.id, out.id)

    def test_get_primary_output_empty(self):
        """No outputs returns None."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        self.assertIsNone(manager.get_primary_output())

    def test_get_total_resolution(self):
        """Total resolution covers all outputs."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        manager.add_output(1920, 1080)
        manager.add_output(2560, 1440)
        total = manager.get_total_resolution()
        self.assertEqual(total, (2560, 1440))

    def test_get_total_resolution_empty(self):
        """Empty returns default."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        total = manager.get_total_resolution()
        self.assertEqual(total, (1920, 1080))

    def test_remove_output_cleans_bindings(self):
        """Removing output cleans up workspace bindings."""
        from ui.multi_monitor import MultiMonitorManager
        manager = MultiMonitorManager()
        out = manager.add_output(1920, 1080)
        manager.bind_workspace(0, out.id)
        manager.remove_output(out.id)
        self.assertIsNone(manager.get_output_for_workspace(0))


class TestOutputInfo(unittest.TestCase):
    """Tests for OutputInfo dataclass."""

    def test_create_output(self):
        """Can create OutputInfo."""
        from ui.multi_monitor import OutputInfo, OutputStatus
        output = OutputInfo(
            id=0,
            name="test",
            width=1920,
            height=1080,
            refresh_rate=60000,
            status=OutputStatus.CONNECTED,
        )
        self.assertEqual(output.width, 1920)
        self.assertEqual(output.refresh_rate, 60000)

    def test_output_defaults(self):
        """OutputInfo has sensible defaults."""
        from ui.multi_monitor import OutputInfo, OutputStatus
        output = OutputInfo(
            id=0, name="test", width=1920, height=1080,
            refresh_rate=60000, status=OutputStatus.CONNECTED,
        )
        self.assertEqual(output.x, 0)
        self.assertEqual(output.y, 0)
        self.assertFalse(output.primary)


class TestHotPlugMonitor(unittest.TestCase):
    """Tests for the hot-plug monitor."""

    def test_monitor_create(self):
        """Can create a hot-plug monitor."""
        from ui.multi_monitor import MultiMonitorManager, HotPlugMonitor
        manager = MultiMonitorManager()
        monitor = HotPlugMonitor(manager)
        self.assertFalse(monitor._running)

    def test_monitor_start_stop(self):
        """Can start and stop the monitor."""
        from ui.multi_monitor import MultiMonitorManager, HotPlugMonitor
        import time
        manager = MultiMonitorManager()
        monitor = HotPlugMonitor(manager, poll_interval=0.1)
        monitor.start()
        self.assertTrue(monitor._running)
        time.sleep(0.2)  # Let it poll once
        monitor.stop()
        self.assertFalse(monitor._running)

    def test_monitor_callbacks(self):
        """Callbacks are set correctly."""
        from ui.multi_monitor import MultiMonitorManager, HotPlugMonitor
        manager = MultiMonitorManager()
        monitor = HotPlugMonitor(manager)
        
        connected = []
        disconnected = []
        monitor.set_callbacks(
            on_connect=lambda o: connected.append(o),
            on_disconnect=lambda o: disconnected.append(o),
        )
        self.assertIsNotNone(monitor._on_connect)
        self.assertIsNotNone(monitor._on_disconnect)

    def test_monitor_double_start(self):
        """Double start is idempotent."""
        from ui.multi_monitor import MultiMonitorManager, HotPlugMonitor
        manager = MultiMonitorManager()
        monitor = HotPlugMonitor(manager)
        monitor.start()
        monitor.start()  # should not raise
        monitor.stop()


if __name__ == "__main__":
    unittest.main()
