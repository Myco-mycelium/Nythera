"""
Tests for ui.desktop_preview — real-time desktop preview renderer.
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

from ui.desktop_preview import DesktopPreview


class TestDesktopPreviewInit(unittest.TestCase):
    """Tests for DesktopPreview initialization."""

    def setUp(self):
        self.preview = DesktopPreview(640, 480)

    def test_dimensions(self):
        self.assertEqual(self.preview.width, 640)
        self.assertEqual(self.preview.height, 480)

    def test_not_started(self):
        self.assertIsNone(self.preview.framebuffer)


class TestDesktopPreviewCapture(unittest.TestCase):
    """Tests for capture functionality."""

    def setUp(self):
        self.preview = DesktopPreview(640, 480)
        self.preview.start()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        self.preview.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_framebuffer_exists(self):
        self.assertIsNotNone(self.preview.framebuffer)
        self.assertEqual(self.preview.framebuffer.size, (640, 480))

    def test_capture_png(self):
        path = os.path.join(self.tmpdir, "test.png")
        self.assertTrue(self.preview.capture(path))
        self.assertTrue(os.path.exists(path))
        img = Image.open(path)
        self.assertEqual(img.size, (640, 480))

    def test_capture_not_started(self):
        p = DesktopPreview(640, 480)
        self.assertFalse(p.capture("/tmp/nope.png"))


class TestDesktopPreviewStates(unittest.TestCase):
    """Tests for rendering all desktop states."""

    def setUp(self):
        self.preview = DesktopPreview(640, 480)
        self.preview.start()

    def tearDown(self):
        self.preview.stop()

    def test_render_default(self):
        img = self.preview.render_default_state()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (640, 480))

    def test_render_notifications(self):
        img = self.preview.render_notification_shade()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (640, 480))

    def test_render_quick_settings(self):
        img = self.preview.render_quick_settings()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (640, 480))

    def test_render_app_launcher(self):
        img = self.preview.render_app_launcher()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (640, 480))

    def test_render_boot_splash(self):
        img = self.preview.render_boot_splash()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (640, 480))


class TestDesktopPreviewRecording(unittest.TestCase):
    """Tests for GIF recording."""

    def setUp(self):
        self.preview = DesktopPreview(320, 240)
        self.preview.start()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        self.preview.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_render_all_states(self):
        paths = self.preview.render_all_states(self.tmpdir)
        self.assertEqual(len(paths), 5)
        for path in paths:
            self.assertTrue(os.path.exists(path))
            img = Image.open(path)
            self.assertEqual(img.size, (320, 240))


class TestDesktopPreviewGIF(unittest.TestCase):
    """Tests for animated GIF rendering."""

    def setUp(self):
        self.preview = DesktopPreview(320, 240)
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_animated_gif(self):
        path = os.path.join(self.tmpdir, "desktop.gif")
        result = self.preview.render_animated_gif(path, seconds=1.0, fps=5)
        self.assertEqual(result, path)
        self.assertTrue(os.path.exists(path))
        img = Image.open(path)
        self.assertEqual(img.format, "GIF")
        self.assertTrue(img.n_frames > 1)


if __name__ == "__main__":
    unittest.main()
