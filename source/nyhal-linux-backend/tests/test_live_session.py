"""
Tests for ui.live_session — live Wayland session with compositor rendering.
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSessionState(unittest.TestCase):
    """Tests for SessionState constants."""

    def test_states(self):
        from ui.live_session import SessionState
        self.assertEqual(SessionState.BOOTING, "booting")
        self.assertEqual(SessionState.RUNNING, "running")
        self.assertEqual(SessionState.PAUSED, "paused")
        self.assertEqual(SessionState.STOPPING, "stopping")
        self.assertEqual(SessionState.STOPPED, "stopped")


class TestWindowInfo(unittest.TestCase):
    """Tests for WindowInfo data class."""

    def test_defaults(self):
        from ui.live_session import WindowInfo
        w = WindowInfo(1)
        self.assertEqual(w.handle, 1)
        self.assertEqual(w.title, "")
        self.assertEqual(w.width, 800)
        self.assertEqual(w.height, 600)
        self.assertTrue(w.visible)
        self.assertFalse(w.focused)
        self.assertFalse(w.minimized)
        self.assertFalse(w.maximized)

    def test_custom(self):
        from ui.live_session import WindowInfo
        w = WindowInfo(42, "My App", 100, 50, 640, 480)
        self.assertEqual(w.handle, 42)
        self.assertEqual(w.title, "My App")
        self.assertEqual(w.x, 100)
        self.assertEqual(w.y, 50)


class TestLiveSession(unittest.TestCase):
    """Tests for LiveSession."""

    def setUp(self):
        from ui.live_session import LiveSession
        self.session = LiveSession(640, 480)

    def tearDown(self):
        self.session.stop()

    def test_initial_state(self):
        self.assertEqual(self.session.state, "stopped")

    def test_start_stop(self):
        self.assertTrue(self.session.start())
        self.assertEqual(self.session.state, "running")
        self.session.stop()
        self.assertEqual(self.session.state, "stopped")

    def test_double_start(self):
        self.assertTrue(self.session.start())
        self.assertFalse(self.session.start())  # Already running
        self.session.stop()

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
        self.assertEqual(self.session.frame_count, 0)
        self.session.render_frame()
        self.assertEqual(self.session.frame_count, 1)
        self.session.render_frame()
        self.assertEqual(self.session.frame_count, 2)

    def test_fps(self):
        self.session.start()
        self.session.render_frame()
        time.sleep(0.01)
        self.session.render_frame()
        self.assertGreater(self.session.fps, 0)

    def test_window_count(self):
        self.session.start()
        # Default windows created on start
        self.assertGreaterEqual(self.session.window_count, 0)

    def test_create_window(self):
        self.session.start()
        handle = self.session.create_window("Test", 10, 20, 400, 300)
        self.assertGreater(handle, 0)
        self.assertEqual(self.session.window_count, 4)  # 3 default + 1
        win = self.session.get_window(handle)
        self.assertIsNotNone(win)
        self.assertEqual(win.title, "Test")
        self.assertEqual(win.width, 400)

    def test_destroy_window(self):
        self.session.start()
        handle = self.session.create_window("Test")
        self.assertTrue(self.session.destroy_window(handle))
        self.assertIsNone(self.session.get_window(handle))

    def test_destroy_nonexistent(self):
        self.session.start()
        self.assertFalse(self.session.destroy_window(999))

    def test_focus_window(self):
        self.session.start()
        h1 = self.session.create_window("Win1")
        h2 = self.session.create_window("Win2")
        self.assertTrue(self.session.focus_window(h1))
        self.assertTrue(self.session.get_window(h1).focused)
        self.assertFalse(self.session.get_window(h2).focused)
        self.assertTrue(self.session.focus_window(h2))
        self.assertFalse(self.session.get_window(h1).focused)
        self.assertTrue(self.session.get_window(h2).focused)

    def test_focus_nonexistent(self):
        self.session.start()
        self.assertFalse(self.session.focus_window(999))

    def test_minimize_window(self):
        self.session.start()
        handle = self.session.create_window("Test")
        self.assertTrue(self.session.minimize_window(handle))
        self.assertTrue(self.session.get_window(handle).minimized)

    def test_maximize_window(self):
        self.session.start()
        handle = self.session.create_window("Test")
        self.assertTrue(self.session.maximize_window(handle))
        self.assertTrue(self.session.get_window(handle).maximized)
        # Toggle back
        self.assertTrue(self.session.maximize_window(handle))
        self.assertFalse(self.session.get_window(handle).maximized)

    def test_get_windows(self):
        self.session.start()
        windows = self.session.get_windows()
        self.assertIsInstance(windows, list)

    def test_using_rust(self):
        self.session.start()
        self.assertIsInstance(self.session.using_rust, bool)


class TestLiveSessionInput(unittest.TestCase):
    """Tests for live session input handling."""

    def setUp(self):
        from ui.live_session import LiveSession
        self.session = LiveSession(640, 480)
        self.session.start()

    def tearDown(self):
        self.session.stop()

    def test_mouse_move(self):
        result = self.session.handle_input({"type": "mouse_move", "x": 100, "y": 200})
        self.assertEqual(result, "cursor_moved")

    def test_key_event(self):
        result = self.session.handle_input({"type": "key", "key": "a"})
        self.assertIn("key", result)

    def test_click(self):
        result = self.session.handle_input({"type": "mouse_click", "x": 300, "y": 300})
        self.assertTrue(result.startswith("window_focused") or result.startswith("click"))

    def test_unknown_event(self):
        result = self.session.handle_input({"type": "unknown"})
        self.assertEqual(result, "unknown")


class TestLiveSessionSummary(unittest.TestCase):
    """Tests for session summary."""

    def test_summary(self):
        from ui.live_session import LiveSession
        session = LiveSession(1280, 720)
        session.start()
        summary = session.summary()
        self.assertEqual(summary["state"], "running")
        self.assertEqual(summary["width"], 1280)
        self.assertEqual(summary["height"], 720)
        self.assertIn("frame_count", summary)
        self.assertIn("fps", summary)
        self.assertIn("window_count", summary)
        session.stop()


class TestCreateSession(unittest.TestCase):
    """Tests for session factory."""

    def test_create_session(self):
        from ui.live_session import create_session
        session = create_session(640, 480)
        self.assertEqual(session.state, "running")
        self.assertEqual(session.width, 640)
        session.stop()


if __name__ == "__main__":
    unittest.main()
