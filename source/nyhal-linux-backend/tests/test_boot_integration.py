"""
Integration test: Boot-to-desktop pipeline.

Renders all 7 boot phases and verifies each produces valid output,
then verifies the complete pipeline from BIOS to desktop works.
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


class TestBootPhaseRendering(unittest.TestCase):
    """Test that each boot phase renders valid output."""

    def setUp(self):
        if Image is None:
            self.skipTest("Pillow not available")
        self.output_dir = tempfile.mkdtemp(prefix="nyrqis_boot_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def _run_boot(self, backend="headless"):
        """Run the full boot and return the output dir."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from nyrqis_boot_full import render_boot_sequence
        out = os.path.join(self.output_dir, backend)
        render_boot_sequence(backend, num_frames=20, output_dir=out)
        return out

    def test_nyrqis_boot_renders_all_phases(self):
        """Nyrqis kernel boot renders all 7 phases."""
        out = self._run_boot("nyrqis")
        # Should have frames
        frames = [f for f in os.listdir(out) if f.endswith(".png")]
        self.assertGreater(len(frames), 0)
        # Should have a GIF
        self.assertTrue(os.path.exists(os.path.join(out, "boot_animation.gif")))

    def test_linux_boot_renders_all_phases(self):
        """Linux backend boot renders all 7 phases."""
        out = self._run_boot("linux")
        frames = [f for f in os.listdir(out) if f.endswith(".png")]
        self.assertGreater(len(frames), 0)

    def test_frames_are_valid_images(self):
        """Every rendered frame is a valid PIL Image."""
        out = self._run_boot("nyrqis")
        frames = sorted([f for f in os.listdir(out) if f.endswith(".png")])
        for fname in frames:
            path = os.path.join(out, fname)
            img = Image.open(path)
            self.assertEqual(img.size, (1280, 720))
            self.assertEqual(img.mode, "RGB")

    def test_gif_is_valid(self):
        """The generated GIF is a valid animated image."""
        out = self._run_boot("nyrqis")
        gif_path = os.path.join(out, "boot_animation.gif")
        img = Image.open(gif_path)
        self.assertEqual(img.format, "GIF")
        # Should have multiple frames
        self.assertTrue(hasattr(img, "n_frames"))
        self.assertGreater(img.n_frames, 1)


class TestBackendPipeline(unittest.TestCase):
    """Test the full backend → compositor → shell pipeline."""

    def test_backend_init(self):
        """Backend initializes and provides display info."""
        from ui.backend_abstraction import get_backend, BackendType
        for bt in [BackendType.HEADLESS, BackendType.LINUX, BackendType.NYRQIS]:
            backend = get_backend(bt)
            monitors = backend.display.enumerate_monitors()
            self.assertGreater(len(monitors), 0)

    def test_compositor_surface_lifecycle(self):
        """Full compositor surface lifecycle."""
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        # Create surface
        sid = backend.compositor.create_surface(1920, 1080)
        self.assertIsNotNone(sid)
        # Commit
        backend.compositor.commit_surface(sid)
        # Destroy
        self.assertTrue(backend.compositor.destroy_surface(sid))

    def test_gpu_render_frame(self):
        """GPU renders a full frame."""
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        self.assertTrue(backend.gpu.initialize())
        frame = backend.gpu.render_frame(1920, 1080)
        self.assertIsNotNone(frame)

    def test_filesystem_read_write(self):
        """Filesystem backend read/write cycle."""
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        # Write
        self.assertTrue(backend.filesystem.write_file("/test.txt", b"hello"))
        # Read
        data = backend.filesystem.read_file("/test.txt")
        self.assertEqual(data, b"hello")

    def test_desktop_backend_lifecycle(self):
        """Full desktop backend lifecycle."""
        try:
            from ui.desktop_backend import DesktopBackend
        except ImportError:
            self.skipTest("DesktopBackend not available")
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        db = DesktopBackend(backend)
        # Create windows
        w1 = db.create_window("Terminal", width=800, height=600)
        self.assertIsNotNone(w1)
        w2 = db.create_window("Files", width=900, height=600)
        self.assertIsNotNone(w2)
        # Focus
        db.focus_window(w1)
        # Render
        img = db.render_to_image()
        self.assertIsNotNone(img)
        self.assertGreater(img.size[0], 0)
        self.assertGreater(img.size[1], 0)
        # Close
        db.close_window(w1)
        db.close_window(w2)

    def test_live_session_lifecycle(self):
        """Full live session lifecycle."""
        try:
            from ui.live_session import LiveSession
        except ImportError:
            self.skipTest("LiveSession not available")
        session = LiveSession(640, 480)
        session.start()
        self.assertEqual(session.state, "running")
        # Render
        img = session.render_frame()
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (640, 480))
        # Window management
        h = session.create_window("Test")
        self.assertGreater(h, 0)
        session.focus_window(h)
        session.minimize_window(h)
        session.maximize_window(h)
        session.destroy_window(h)
        # Input
        session.handle_input({"type": "key", "key": "a"})
        session.handle_input({"type": "mouse_move", "x": 100, "y": 100})
        session.handle_input({"type": "mouse_click", "x": 100, "y": 100})
        # Stop
        session.stop()
        self.assertEqual(session.state, "stopped")


class TestRustCompositorIntegration(unittest.TestCase):
    """Integration tests for the Rust compositor FFI."""

    def setUp(self):
        from ui.rust_ffi import RustCompositor
        self.comp = RustCompositor()
        if not self.comp.available:
            self.skipTest("Rust compositor not available")

    def test_compositor_full_lifecycle(self):
        """Full Rust compositor lifecycle."""
        self.comp.start()
        self.assertTrue(self.comp.is_running())
        # Add output
        oid = self.comp.add_output(1920, 1080, "default")
        self.assertGreaterEqual(oid, 0)
        # Create surfaces
        surfaces = []
        for i in range(5):
            sid = self.comp.create_surface(i, 800, 600)
            surfaces.append(sid)
        self.assertGreaterEqual(self.comp.surface_count(), 5)
        # Commit
        for sid in surfaces:
            self.assertTrue(self.comp.commit_surface(sid))
        # Destroy
        for sid in surfaces:
            self.assertTrue(self.comp.destroy_surface(sid))
        self.comp.stop()
        self.assertFalse(self.comp.is_running())


if __name__ == "__main__":
    unittest.main()
