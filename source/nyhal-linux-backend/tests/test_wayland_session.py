"""
Tests for ui.wayland_session — real Wayland session launcher.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PIL import Image
except ImportError:
    Image = None

from ui.wayland_session import WaylandSession, WaylandState


class TestWaylandState(unittest.TestCase):
    def test_states(self):
        self.assertEqual(WaylandState.UNINITIALIZED, "uninitialized")
        self.assertEqual(WaylandState.RUNNING, "running")
        self.assertEqual(WaylandState.STOPPED, "stopped")


class TestWaylandSession(unittest.TestCase):
    def setUp(self):
        self.session = WaylandSession(640, 480)

    def tearDown(self):
        self.session.stop()

    def test_initial_state(self):
        self.assertEqual(self.session.state, WaylandState.UNINITIALIZED)

    def test_start_stop(self):
        self.assertTrue(self.session.start())
        self.assertEqual(self.session.state, WaylandState.RUNNING)
        self.session.stop()
        self.assertEqual(self.session.state, WaylandState.STOPPED)

    def test_dimensions(self):
        self.assertEqual(self.session.width, 640)
        self.assertEqual(self.session.height, 480)

    def test_render_frame(self):
        self.session.start()
        img = self.session.render_frame()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (640, 480))

    def test_frame_count(self):
        self.session.start()
        self.session.render_frame()
        self.assertEqual(self.session.frame_count, 1)

    def test_screenshot(self):
        self.session.start()
        self.session.render_frame()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.png")
            self.assertTrue(self.session.screenshot(path))
            self.assertTrue(os.path.exists(path))

    def test_window_management(self):
        self.session.start()
        h1 = self.session.create_window("Win1")
        h2 = self.session.create_window("Win2")
        self.assertEqual(self.session.window_count, 5)  # 3 default + 2
        self.assertTrue(self.session.focus_window(h1))
        self.assertTrue(self.session.destroy_window(h1))
        self.assertEqual(self.session.window_count, 4)

    def test_input(self):
        self.session.start()
        self.assertEqual(self.session.handle_input({"type": "mouse_move", "x": 100, "y": 100}), "cursor_moved")

    def test_summary(self):
        s = self.session.summary()
        self.assertIn("state", s)
        self.assertIn("fps", s)

    def test_uses_rust(self):
        self.assertIsInstance(self.session.using_rust, bool)


class TestWaylandSessionFactory(unittest.TestCase):
    def test_create_and_start(self):
        session = WaylandSession(320, 240)
        session.start()
        self.assertEqual(session.state, WaylandState.RUNNING)
        img = session.render_frame()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (320, 240))
        session.stop()


if __name__ == "__main__":
    unittest.main()
