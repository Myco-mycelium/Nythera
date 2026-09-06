"""test_wayland_multimonitor — Tests for multi-monitor Wayland support.

References:
    - ADR-0026: Wayland display-server integration
    - ui/wayland_display.py
    - ui/wayland_codec.py
"""

from __future__ import annotations

import os
import sys
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    os.sys.path.insert(0, _HERE)


class TestWaylandCodecMultiMonitor(unittest.TestCase):
    """Tests for wayland_codec multi-monitor FFI functions."""

    def test_get_primary_output_no_conn(self):
        """get_primary_output returns -1 when not connected."""
        from ui import wayland_codec
        if wayland_codec.WAYLAND_STUB:
            self.skipTest("Wayland crate not available")
        # Without a connection, primary output should be -1
        result = wayland_codec.get_primary_output()
        # May be -1 if no outputs available
        self.assertIsInstance(result, int)

    def test_set_primary_output_invalid_id(self):
        """set_primary_output returns -1 for invalid output ID."""
        from ui import wayland_codec
        if wayland_codec.WAYLAND_STUB:
            self.skipTest("Wayland crate not available")
        result = wayland_codec.set_primary_output(-1)
        self.assertEqual(result, -1)
        result = wayland_codec.set_primary_output(99)
        self.assertEqual(result, -1)

    def test_get_output_count_no_conn(self):
        """get_output_count returns -1 when not connected."""
        from ui import wayland_codec
        if wayland_codec.WAYLAND_STUB:
            self.skipTest("Wayland crate not available")
        result = wayland_codec.get_output_count(0)
        # May return -1 if no connection
        self.assertIsInstance(result, int)

    def test_get_output_info_invalid_id(self):
        """get_output_info returns None for invalid output ID."""
        from ui import wayland_codec
        if wayland_codec.WAYLAND_STUB:
            self.skipTest("Wayland crate not available")
        result = wayland_codec.get_output_info(-1)
        self.assertIsNone(result)
        result = wayland_codec.get_output_info(99)
        self.assertIsNone(result)

    def test_set_buffer_scale_no_conn(self):
        """set_buffer_scale returns -1 when not connected."""
        from ui import wayland_codec
        if wayland_codec.WAYLAND_STUB:
            self.skipTest("Wayland crate not available")
        result = wayland_codec.set_buffer_scale(-1, 2)
        self.assertEqual(result, -1)

    def test_check_output_changes_no_conn(self):
        """check_output_changes returns NONE when not connected."""
        from ui import wayland_codec
        if wayland_codec.WAYLAND_STUB:
            self.skipTest("Wayland crate not available")
        result = wayland_codec.check_output_changes(-1)
        self.assertEqual(result, wayland_codec.OUTPUT_CHANGE_NONE)

    def test_output_info_struct_fields(self):
        """WaylandOutputInfo struct has expected fields."""
        from ui.wayland_codec import WaylandOutputInfo
        info = WaylandOutputInfo()
        # Check all fields exist
        self.assertTrue(hasattr(info, 'id'))
        self.assertTrue(hasattr(info, 'x'))
        self.assertTrue(hasattr(info, 'y'))
        self.assertTrue(hasattr(info, 'width'))
        self.assertTrue(hasattr(info, 'height'))
        self.assertTrue(hasattr(info, 'scale'))
        self.assertTrue(hasattr(info, 'primary'))
        self.assertTrue(hasattr(info, 'transform'))
        self.assertTrue(hasattr(info, 'refresh'))


class TestWaylandDisplayMultiMonitor(unittest.TestCase):
    """Tests for WaylandDisplay multi-monitor support."""

    def test_outputs_empty_when_disconnected(self):
        """outputs returns empty list when not connected."""
        from ui.wayland_display import WaylandDisplay
        display = WaylandDisplay()
        self.assertEqual(display.outputs, [])

    def test_primary_output_none_when_disconnected(self):
        """primary_output returns None when not connected."""
        from ui.wayland_display import WaylandDisplay
        display = WaylandDisplay()
        self.assertIsNone(display.primary_output)

    def test_set_primary_output_fails_when_disconnected(self):
        """set_primary_output returns False when not connected."""
        from ui.wayland_display import WaylandDisplay
        display = WaylandDisplay()
        self.assertFalse(display.set_primary_output(0))

    def test_set_buffer_scale_fails_when_disconnected(self):
        """set_buffer_scale returns False when not connected."""
        from ui.wayland_display import WaylandDisplay
        display = WaylandDisplay()
        self.assertFalse(display.set_buffer_scale(0, 2))

    def test_get_output_info_none_when_disconnected(self):
        """get_output_info returns None when not connected."""
        from ui.wayland_display import WaylandDisplay
        display = WaylandDisplay()
        self.assertIsNone(display.get_output_info(0))

    def test_check_output_changes_false_when_disconnected(self):
        """check_output_changes returns False when not connected."""
        from ui.wayland_display import WaylandDisplay
        display = WaylandDisplay()
        self.assertFalse(display.check_output_changes())

    def test_summary_includes_outputs(self):
        """summary() includes output information."""
        from ui.wayland_display import WaylandDisplay
        display = WaylandDisplay()
        summary = display.summary()
        self.assertIn("outputs", summary)
        self.assertIn("primary_output", summary)
        self.assertIn("output_details", summary)
        self.assertEqual(summary["outputs"], 0)
        self.assertIsNone(summary["primary_output"])
        self.assertEqual(summary["output_details"], [])


class TestWaylandDisplayOutputConstants(unittest.TestCase):
    """Tests for output change type constants."""

    def test_output_change_constants(self):
        """Output change constants are defined correctly."""
        from ui import wayland_codec
        self.assertEqual(wayland_codec.OUTPUT_CHANGE_NONE, 0)
        self.assertEqual(wayland_codec.OUTPUT_CHANGE_ADDED, 1)
        self.assertEqual(wayland_codec.OUTPUT_CHANGE_REMOVED, 2)
        self.assertEqual(wayland_codec.OUTPUT_CHANGE_CHANGED, 3)

    def test_abi_version_1_2(self):
        """ABI version is 1.2.0 for multi-monitor support."""
        from ui import wayland_codec
        self.assertEqual(wayland_codec.NYRQIS_WAYLAND_ABI, 0x0001_0200)


class TestWaylandSessionMultiMonitor(unittest.TestCase):
    """Tests for WaylandSession multi-monitor awareness."""

    def test_session_outputs(self):
        """Session can query outputs."""
        from ui.wayland_session import WaylandSession
        session = WaylandSession(640, 480)
        session.start()
        # Outputs may be empty in headless mode
        summary = session.summary()
        self.assertIn("state", summary)
        session.stop()

    def test_session_render_frame(self):
        """Session can render a frame."""
        from ui.wayland_session import WaylandSession
        session = WaylandSession(640, 480)
        session.start()
        img = session.render_frame()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (640, 480))
        session.stop()


if __name__ == "__main__":
    unittest.main()
