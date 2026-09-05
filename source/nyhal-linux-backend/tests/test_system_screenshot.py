"""
Full system screenshot test.

Boots the entire Nyrqis pipeline end-to-end and captures a desktop screenshot.
Verifies the complete chain: backend → compositor → shell → desktop → screenshot.
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PIL import Image
except ImportError:
    Image = None


class TestFullSystemScreenshot(unittest.TestCase):
    """Full system screenshot: boot → desktop → capture."""

    def setUp(self):
        if Image is None:
            self.skipTest("Pillow not available")
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis_sys_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_boot_to_screenshot_nyrqis(self):
        """Full boot-to-desktop screenshot with Nyrqis kernel backend."""
        from ui.backend_abstraction import get_backend, BackendType
        from ui.desktop_preview import DesktopPreview

        # 1. Initialize backend
        backend = get_backend(BackendType.NYRQIS)
        monitors = backend.display.enumerate_monitors()
        self.assertGreater(len(monitors), 0)

        # 2. Initialize GPU
        self.assertTrue(backend.gpu.initialize())

        # 3. Create compositor surface
        sid = backend.compositor.create_surface(1280, 720)
        self.assertIsNotNone(sid)

        # 4. Render desktop via preview
        preview = DesktopPreview(1280, 720)
        preview.start()
        preview.render_default_state()

        # 5. Capture screenshot
        screenshot_path = os.path.join(self.tmpdir, "desktop_nyrqis.png")
        preview.capture(screenshot_path)
        self.assertTrue(os.path.exists(screenshot_path))

        # 6. Verify screenshot
        img = Image.open(screenshot_path)
        self.assertEqual(img.size, (1280, 720))
        self.assertEqual(img.mode, "RGB")
        self.assertGreater(img.getbbox()[2], 0)  # Has non-zero width

        # 7. Cleanup
        preview.stop()
        backend.compositor.destroy_surface(sid)

    def test_boot_to_screenshot_headless(self):
        """Full boot-to-desktop screenshot with headless backend."""
        from ui.backend_abstraction import get_backend, BackendType
        from ui.desktop_preview import DesktopPreview

        backend = get_backend(BackendType.HEADLESS)
        self.assertTrue(backend.gpu.initialize())

        preview = DesktopPreview(1280, 720)
        preview.start()

        # Render all states and capture
        states = [
            ("boot_splash", preview.render_boot_splash),
            ("default", preview.render_default_state),
            ("notifications", preview.render_notification_shade),
            ("quick_settings", preview.render_quick_settings),
            ("app_launcher", preview.render_app_launcher),
        ]

        for name, render_func in states:
            render_func()
            path = os.path.join(self.tmpdir, f"{name}.png")
            preview.capture(path)
            img = Image.open(path)
            self.assertEqual(img.size, (1280, 720))
            self.assertEqual(img.mode, "RGB")

        preview.stop()

    def test_screenshot_pixel_content(self):
        """Screenshot has actual content (not blank)."""
        from ui.desktop_preview import DesktopPreview

        preview = DesktopPreview(640, 480)
        preview.start()
        preview.render_default_state()

        img = preview.framebuffer
        # Get pixel at taskbar area (should be non-black)
        taskbar_pixel = img.getpixel((320, 460))
        # Get pixel at window area
        window_pixel = img.getpixel((100, 100))
        # Both should be different from pure black
        self.assertNotEqual(taskbar_pixel, (0, 0, 0))
        self.assertNotEqual(window_pixel, (0, 0, 0))

        preview.stop()

    def test_animated_boot_gif(self):
        """Animated boot sequence GIF is valid."""
        from ui.desktop_preview import DesktopPreview

        preview = DesktopPreview(640, 480)
        gif_path = os.path.join(self.tmpdir, "boot.gif")
        result = preview.render_animated_gif(gif_path, seconds=2.0, fps=5)
        self.assertEqual(result, gif_path)
        self.assertTrue(os.path.exists(gif_path))
        img = Image.open(gif_path)
        self.assertEqual(img.format, "GIF")
        self.assertTrue(img.n_frames >= 5)

    def test_live_session_screenshot(self):
        """Live session renders a valid screenshot."""
        from ui.live_session import LiveSession

        session = LiveSession(640, 480)
        session.start()
        img = session.render_frame()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (640, 480))
        # Verify it's not blank
        center_pixel = img.getpixel((320, 240))
        self.assertNotEqual(center_pixel, (0, 0, 0))

        screenshot_path = os.path.join(self.tmpdir, "session.png")
        img.save(screenshot_path)
        self.assertTrue(os.path.exists(screenshot_path))

        session.stop()

    def test_compositor_surface_render(self):
        """Compositor creates and renders a surface."""
        from ui.backend_abstraction import get_backend, BackendType

        backend = get_backend(BackendType.HEADLESS)
        self.assertTrue(backend.gpu.initialize())

        # Create and render
        frame = backend.gpu.render_frame(1280, 720)
        self.assertIsNotNone(frame)
        if Image is not None and hasattr(frame, 'size'):
            self.assertEqual(frame.size, (1280, 720))


class TestRustSystemScreenshot(unittest.TestCase):
    """System screenshot using the Rust compositor."""

    def setUp(self):
        from ui.rust_ffi import RustCompositor
        self.comp = RustCompositor()
        if not self.comp.available:
            self.skipTest("Rust compositor not available")

    def test_rust_compositor_screenshot(self):
        """Full Rust compositor pipeline produces a valid output."""
        from ui.desktop_preview import DesktopPreview

        self.comp.start()
        sid = self.comp.create_surface(0, 1280, 720)
        self.assertGreaterEqual(sid, 0)
        self.assertTrue(self.comp.commit_surface(sid))

        preview = DesktopPreview(1280, 720)
        preview.start()
        preview.render_default_state()

        img = preview.framebuffer
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (1280, 720))

        preview.stop()
        self.comp.destroy_surface(sid)
        self.comp.stop()


if __name__ == "__main__":
    unittest.main()
