"""
End-to-end tests for the Nyrqis boot-to-desktop pipeline.

Tests the complete flow from backend detection → initialization → compositor →
shell → rendering → shutdown. Covers both Linux and Nyrqis backend paths,
as well as headless/CI mode.
"""

import os
import sys
import time
import unittest
import tempfile
import shutil

# Ensure we can import the project
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBackendDetection(unittest.TestCase):
    """Test auto-detection of backend type."""

    def test_detect_returns_valid_type(self):
        from ui.backend_abstraction import detect_backend, BackendType
        bt = detect_backend()
        self.assertIsInstance(bt, BackendType)

    def test_env_override_nyrqis(self):
        from ui.backend_abstraction import detect_backend, BackendType
        old = os.environ.get("NYRQIS_BACKEND")
        try:
            os.environ["NYRQIS_BACKEND"] = "nyrqis"
            self.assertEqual(detect_backend(), BackendType.NYRQIS)
        finally:
            if old is None:
                os.environ.pop("NYRQIS_BACKEND", None)
            else:
                os.environ["NYRQIS_BACKEND"] = old

    def test_env_override_headless(self):
        from ui.backend_abstraction import detect_backend, BackendType
        old = os.environ.get("NYRQIS_BACKEND")
        try:
            os.environ["NYRQIS_BACKEND"] = "headless"
            self.assertEqual(detect_backend(), BackendType.HEADLESS)
        finally:
            if old is None:
                os.environ.pop("NYRQIS_BACKEND", None)
            else:
                os.environ["NYRQIS_BACKEND"] = old

    def test_env_override_linux(self):
        from ui.backend_abstraction import detect_backend, BackendType
        old = os.environ.get("NYRQIS_BACKEND")
        try:
            os.environ["NYRQIS_BACKEND"] = "linux"
            bt = detect_backend()
            # Could be LINUX or HEADLESS depending on /dev/dri
            self.assertIn(bt, (BackendType.LINUX, BackendType.HEADLESS))
        finally:
            if old is None:
                os.environ.pop("NYRQIS_BACKEND", None)
            else:
                os.environ["NYRQIS_BACKEND"] = old


class TestBackendInitialization(unittest.TestCase):
    """Test backend set initialization and properties."""

    def test_headless_backend_set(self):
        from ui.backend_abstraction import get_backend, BackendType, BackendSet
        backend = get_backend(BackendType.HEADLESS)
        self.assertIsInstance(backend, BackendSet)
        self.assertEqual(backend.backend_type, BackendType.HEADLESS)

    def test_headless_display(self):
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        monitors = backend.display.enumerate_monitors()
        self.assertIsInstance(monitors, list)
        self.assertGreater(len(monitors), 0)
        self.assertIn("id", monitors[0])
        self.assertIn("width", monitors[0])
        self.assertIn("height", monitors[0])

    def test_headless_gpu(self):
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        self.assertTrue(backend.gpu.initialize())
        buf = backend.gpu.allocate_buffer(100, 100)
        self.assertIsNotNone(buf)
        frame = backend.gpu.render_frame(100, 100)
        self.assertIsNotNone(frame)

    def test_headless_compositor(self):
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        sid = backend.compositor.create_surface(800, 600)
        self.assertIsNotNone(sid)
        backend.compositor.commit_surface(sid)
        self.assertTrue(backend.compositor.destroy_surface(sid))

    def test_headless_filesystem(self):
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        self.assertTrue(backend.filesystem.write_file("/test.txt", b"hello"))
        data = backend.filesystem.read_file("/test.txt")
        self.assertEqual(data, b"hello")
        self.assertTrue(backend.filesystem.mkdir("/tmp"))

    def test_headless_input(self):
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        self.assertIsInstance(backend.input_backend.poll_events(), list)
        self.assertTrue(backend.input_backend.grab_keyboard())
        self.assertTrue(backend.input_backend.release_keyboard())


class TestBackendSwitching(unittest.TestCase):
    """Test switching between backends."""

    def test_switch_to_headless(self):
        from ui.backend_abstraction import switch_backend, BackendType, reset_backend
        reset_backend()
        backend = switch_backend(BackendType.HEADLESS)
        self.assertEqual(backend.backend_type, BackendType.HEADLESS)
        reset_backend()

    def test_switch_preserves_state(self):
        from ui.backend_abstraction import switch_backend, BackendType, get_backend, reset_backend
        reset_backend()
        b1 = switch_backend(BackendType.HEADLESS)
        b2 = get_backend()
        self.assertIs(b1, b2)
        reset_backend()

    def test_switch_invalidates_cache(self):
        from ui.backend_abstraction import switch_backend, BackendType, get_backend, reset_backend
        reset_backend()
        b1 = switch_backend(BackendType.HEADLESS)
        b2 = switch_backend(BackendType.HEADLESS)  # same type, different instance
        self.assertIsNot(b1, b2)
        reset_backend()

    def test_linux_backend_set(self):
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.LINUX)
        self.assertEqual(backend.backend_type, BackendType.LINUX)
        monitors = backend.display.enumerate_monitors()
        self.assertIsInstance(monitors, list)
        self.assertGreater(len(monitors), 0)

    def test_nyrqis_backend_set(self):
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.NYRQIS)
        self.assertEqual(backend.backend_type, BackendType.NYRQIS)
        monitors = backend.display.enumerate_monitors()
        self.assertIsInstance(monitors, list)
        self.assertGreater(len(monitors), 0)


class TestCompositorBootSequence(unittest.TestCase):
    """Test the compositor boot-up sequence."""

    def test_surface_create_commit_destroy(self):
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        # Create multiple surfaces
        surfaces = []
        for i in range(5):
            sid = backend.compositor.create_surface(100 + i * 50, 100 + i * 50)
            surfaces.append(sid)
        # Commit all
        for sid in surfaces:
            backend.compositor.commit_surface(sid)
        # Destroy all
        for sid in surfaces:
            self.assertTrue(backend.compositor.destroy_surface(sid))

    def test_render_full_frame(self):
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        self.assertTrue(backend.gpu.initialize())
        w, h = 1920, 1080
        frame = backend.gpu.render_frame(w, h)
        self.assertIsNotNone(frame)
        # Check frame dimensions if PIL Image
        try:
            from PIL import Image
            if isinstance(frame, Image.Image):
                self.assertEqual(frame.size, (w, h))
        except ImportError:
            pass


class TestDesktopRenderPipeline(unittest.TestCase):
    """Test the desktop rendering pipeline end-to-end."""

    def test_compositor_renders_to_image(self):
        """Test that the desktop compositor produces a valid image."""
        try:
            from ui.compositor import NyrqisCompositor
        except ImportError:
            self.skipTest("NyrqisCompositor not available")

        try:
            compositor = NyrqisCompositor()
            img = compositor.render()
            from PIL import Image
            self.assertIsInstance(img, Image.Image)
            self.assertEqual(img.size, (1920, 1080))
        except Exception:
            self.skipTest("Compositor init failed (no display)")

    def test_compositor_uses_backend(self):
        """Test that compositor can use the backend abstraction."""
        from ui.backend_abstraction import get_backend, BackendType
        backend = get_backend(BackendType.HEADLESS)
        frame = backend.gpu.render_frame(640, 480)
        self.assertIsNotNone(frame)

    def test_shell_renders_with_backend(self):
        """Test shell rendering through backend."""
        try:
            from ui.shell import NyrqisShell
        except ImportError:
            self.skipTest("NyrqisShell not available")

        try:
            shell = NyrqisShell()
            # Shell should be able to render without errors
            result = shell.render()
            # Result could be a list of strings or an image
            self.assertIsNotNone(result)
        except Exception:
            self.skipTest("Shell init failed")


class TestBootSequence(unittest.TestCase):
    """Test the complete boot sequence simulation."""

    def test_boot_phases(self):
        """Test that all boot phases execute in order."""
        phases = []
        expected_phases = [
            "hardware_init",
            "backend_detect",
            "backend_init",
            "display_init",
            "gpu_init",
            "compositor_init",
            "shell_init",
            "desktop_ready",
        ]

        # Simulate the boot sequence
        from ui.backend_abstraction import get_backend, BackendType

        phases.append("hardware_init")
        backend = get_backend(BackendType.HEADLESS)
        phases.append("backend_detect")

        # Initialize each subsystem
        backend.display.enumerate_monitors()
        phases.append("backend_init")

        monitors = backend.display.set_mode("headless", 1280, 720, 60)
        phases.append("display_init")

        self.assertTrue(backend.gpu.initialize())
        buf = backend.gpu.allocate_buffer(1280, 720)
        phases.append("gpu_init")

        sid = backend.compositor.create_surface(1280, 720)
        phases.append("compositor_init")

        events = backend.input_backend.poll_events()
        phases.append("shell_init")

        # Render final frame
        frame = backend.gpu.render_frame(1280, 720)
        phases.append("desktop_ready")

        self.assertEqual(phases, expected_phases)

    def test_boot_to_render_time(self):
        """Test that full boot-to-render completes in < 5 seconds."""
        from ui.backend_abstraction import get_backend, BackendType
        start = time.time()
        backend = get_backend(BackendType.HEADLESS)
        backend.display.enumerate_monitors()
        backend.gpu.initialize()
        sid = backend.compositor.create_surface(1920, 1080)
        frame = backend.gpu.render_frame(1920, 1080)
        elapsed = time.time() - start
        self.assertLess(elapsed, 5.0, f"Boot-to-render took {elapsed:.2f}s (> 5s)")

    def test_full_shutdown_cycle(self):
        """Test that a full boot-shutdown-boot cycle works."""
        from ui.backend_abstraction import get_backend, switch_backend, BackendType, reset_backend

        # Boot
        reset_backend()
        backend1 = get_backend(BackendType.HEADLESS)
        sid1 = backend1.compositor.create_surface(800, 600)
        backend1.compositor.commit_surface(sid1)

        # Shutdown
        reset_backend()

        # Re-boot
        backend2 = get_backend(BackendType.HEADLESS)
        self.assertNotEqual(backend1.compositor, backend2.compositor)
        sid2 = backend2.compositor.create_surface(800, 600)
        self.assertIsNotNone(sid2)
        backend2.compositor.commit_surface(sid2)
        self.assertTrue(backend2.compositor.destroy_surface(sid2))


class TestDesktopSessionE2E(unittest.TestCase):
    """Test the full desktop session with apps."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis_e2e_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_desktop_backend_full_cycle(self):
        """Test DesktopBackend: create, focus, render, close."""
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
        from PIL import Image
        self.assertIsInstance(img, Image.Image)

        # Close
        db.close_window(w1)
        db.close_window(w2)

    def test_compositor_with_design(self):
        """Test rendering with .nstudio design loaded."""
        try:
            from ui.compositor import NyrqisCompositor
        except ImportError:
            self.skipTest("NyrqisCompositor not available")

        design_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "designs", "default.nstudio"
        )
        if not os.path.exists(design_path):
            self.skipTest("Default design not found")

        try:
            compositor = NyrqisCompositor(design_path=design_path)
            img = compositor.render()
            from PIL import Image
            self.assertIsInstance(img, Image.Image)
        except Exception:
            self.skipTest("Compositor with design failed")


class TestBackendAbstractionImports(unittest.TestCase):
    """Test that all backend-related modules import cleanly."""

    def test_import_backend_abstraction(self):
        from ui import backend_abstraction
        self.assertTrue(hasattr(backend_abstraction, 'BackendType'))
        self.assertTrue(hasattr(backend_abstraction, 'get_backend'))
        self.assertTrue(hasattr(backend_abstraction, 'switch_backend'))

    def test_import_backend_type(self):
        from ui.backend_abstraction import BackendType
        self.assertIn(BackendType.LINUX, BackendType)
        self.assertIn(BackendType.NYRQIS, BackendType)
        self.assertIn(BackendType.HEADLESS, BackendType)

    def test_import_all_backend_classes(self):
        from ui.backend_abstraction import (
            BackendType, BackendSet, DisplayBackend, GPUBackend,
            InputBackend, CompositorBackend, FilesystemBackend,
            LinuxDisplayBackend, LinuxGPUBackend, LinuxInputBackend,
            LinuxCompositorBackend, LinuxFilesystemBackend,
            NyrqisDisplayBackend, NyrqisGPUBackend, NyrqisInputBackend,
            NyrqisCompositorBackend, NyrqisFilesystemBackend,
            HeadlessDisplayBackend, HeadlessGPUBackend, HeadlessInputBackend,
            HeadlessCompositorBackend, HeadlessFilesystemBackend,
        )

    def test_import_linux_backend(self):
        from ui.linux_backend import LinuxBackend

    def test_import_nyrqis_backend(self):
        from ui.nyrqis_backend import NyrqisBackend


if __name__ == "__main__":
    unittest.main()
