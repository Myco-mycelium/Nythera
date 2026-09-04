"""Tests for desktop backend integration."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.backend_abstraction import BackendType, get_backend
from ui.desktop_backend import DesktopBackend, WindowState, DesktopState, create_desktop


class TestWindowState(unittest.TestCase):
    def test_creation(self):
        w = WindowState(id="w1", title="Test", width=800, height=600)
        self.assertEqual(w.id, "w1")
        self.assertTrue(w.visible)
        self.assertFalse(w.focused)

    def test_defaults(self):
        w = WindowState(id="w1")
        self.assertEqual(w.width, 800)
        self.assertEqual(w.height, 600)
        self.assertFalse(w.minimized)
        self.assertFalse(w.maximized)


class TestDesktopState(unittest.TestCase):
    def test_initial(self):
        s = DesktopState()
        self.assertEqual(len(s.windows), 0)
        self.assertTrue(s.taskbar_visible)

    def test_windows(self):
        s = DesktopState()
        s.windows.append(WindowState(id="w1"))
        self.assertEqual(len(s.windows), 1)


class TestDesktopBackend(unittest.TestCase):
    def setUp(self):
        self.backend = create_desktop(backend_type=BackendType.HEADLESS)

    def test_creation(self):
        self.assertIsInstance(self.backend, DesktopBackend)
        self.assertEqual(self.backend.backend_type, BackendType.HEADLESS)

    def test_create_window(self):
        win = self.backend.create_window("Terminal", 800, 600)
        self.assertEqual(win.title, "Terminal")
        self.assertEqual(len(self.backend.state.windows), 1)

    def test_create_multiple_windows(self):
        self.backend.create_window("Terminal", 800, 600)
        self.backend.create_window("Editor", 1000, 700)
        self.assertEqual(len(self.backend.state.windows), 2)

    def test_close_window(self):
        win = self.backend.create_window("Test")
        self.assertTrue(self.backend.close_window(win.id))
        self.assertEqual(len(self.backend.state.windows), 0)

    def test_close_nonexistent(self):
        self.assertFalse(self.backend.close_window("no-such"))

    def test_focus_window(self):
        w1 = self.backend.create_window("A")
        w2 = self.backend.create_window("B")
        self.backend.focus_window(w2.id)
        self.assertTrue(w2.focused)
        self.assertFalse(w1.focused)
        self.assertEqual(self.backend.state.active_window, w2.id)

    def test_minimize_window(self):
        win = self.backend.create_window("Test")
        self.assertTrue(self.backend.minimize_window(win.id))
        self.assertTrue(win.minimized)
        self.assertFalse(win.visible)

    def test_maximize_window(self):
        win = self.backend.create_window("Test", 800, 600)
        self.assertTrue(self.backend.maximize_window(win.id))
        self.assertTrue(win.maximized)
        self.assertEqual(win.width, self.backend._width)

    def test_render_to_image(self):
        self.backend.create_window("Terminal", 800, 600)
        img = self.backend.render_to_image()
        self.assertIsNotNone(img)
        if img:
            self.assertTrue(hasattr(img, 'size'))

    def test_render_to_png(self):
        self.backend.create_window("Terminal", 800, 600)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            result = self.backend.render_to_png(path)
            self.assertTrue(result)
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 0)
        finally:
            os.unlink(path)

    def test_render_shell(self):
        result = self.backend.render_shell()
        # Shell may be None if no design loaded
        self.assertTrue(result is None or isinstance(result, dict))

    def test_handle_key_super(self):
        result = self.backend.handle_key("super")
        self.assertEqual(result, "toggle_taskbar")
        self.assertFalse(self.backend.state.taskbar_visible)

    def test_handle_key_alt_tab(self):
        self.backend.create_window("A")
        self.backend.create_window("B")
        self.backend.focus_window("A")
        result = self.backend.handle_key("alt+tab")
        self.assertEqual(result, "cycle_focus")

    def test_handle_key_alt_f4(self):
        self.backend.create_window("Test")
        self.backend.focus_window(self.backend.state.windows[0].id)
        result = self.backend.handle_key("alt+f4")
        self.assertEqual(result, "close_window")
        self.assertEqual(len(self.backend.state.windows), 0)

    def test_poll_input(self):
        events = self.backend.poll_input()
        self.assertIsInstance(events, list)

    def test_summary(self):
        self.backend.create_window("Test")
        s = self.backend.summary()
        self.assertEqual(s["windows"], 1)
        self.assertIn("headless", s["backend"])

    def test_render_count(self):
        self.backend.render_to_image()
        self.backend.render_to_image()
        self.assertEqual(self.backend._render_count, 2)


class TestCreateDesktop(unittest.TestCase):
    def test_headless(self):
        d = create_desktop(backend_type=BackendType.HEADLESS)
        self.assertEqual(d.backend_type, BackendType.HEADLESS)

    def test_with_design(self):
        design = "tests/fixtures/nstudio/desktop.nstudio"
        if os.path.exists(design):
            d = create_desktop(design_path=design, backend_type=BackendType.HEADLESS)
            self.assertIsNotNone(d._shell)

    def test_custom_size(self):
        d = create_desktop(backend_type=BackendType.HEADLESS, width=1280, height=720)
        self.assertEqual(d._width, 1280)
        self.assertEqual(d._height, 720)


class TestDesktopBackendWithDesign(unittest.TestCase):
    """Test with actual .nstudio design loaded."""

    def setUp(self):
        design = "tests/fixtures/nstudio/desktop.nstudio"
        if os.path.exists(design):
            self.backend = create_desktop(design_path=design, backend_type=BackendType.HEADLESS)
        else:
            self.backend = create_desktop(backend_type=BackendType.HEADLESS)

    def test_shell_render(self):
        if self.backend._shell:
            result = self.backend.render_shell()
            self.assertIsNotNone(result)
            self.assertTrue(result.get("ok"))

    def test_render_with_shell(self):
        self.backend.create_window("Shell", 1280, 720)
        img = self.backend.render_to_image()
        self.assertIsNotNone(img)


if __name__ == "__main__":
    unittest.main()
