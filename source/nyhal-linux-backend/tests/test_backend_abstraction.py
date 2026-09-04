"""Tests for backend abstraction layer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.backend_abstraction import (
    BackendType, BackendSet,
    get_backend, switch_backend, detect_backend,
    DisplayBackend, GPUBackend, InputBackend, CompositorBackend, FilesystemBackend,
    LinuxDisplayBackend, LinuxGPUBackend, LinuxInputBackend, LinuxCompositorBackend, LinuxFilesystemBackend,
    NyrqisDisplayBackend, NyrqisGPUBackend, NyrqisInputBackend, NyrqisCompositorBackend, NyrqisFilesystemBackend,
    HeadlessDisplayBackend, HeadlessGPUBackend, HeadlessInputBackend, HeadlessCompositorBackend, HeadlessFilesystemBackend,
)


class TestBackendType(unittest.TestCase):
    def test_enum_values(self):
        self.assertEqual(BackendType.LINUX.value, "linux")
        self.assertEqual(BackendType.NYRQIS.value, "nyrqis")
        self.assertEqual(BackendType.HEADLESS.value, "headless")

    def test_detect_backend(self):
        bt = detect_backend()
        self.assertIsInstance(bt, BackendType)


class TestHeadlessBackend(unittest.TestCase):
    def setUp(self):
        self.backend = get_backend(BackendType.HEADLESS)

    def test_backend_set(self):
        self.assertIsInstance(self.backend, BackendSet)
        self.assertEqual(self.backend.backend_type, BackendType.HEADLESS)

    def test_display(self):
        monitors = self.backend.display.enumerate_monitors()
        self.assertGreater(len(monitors), 0)
        self.assertTrue(self.backend.display.set_mode("headless", 1920, 1080, 60))
        fb = self.backend.display.get_framebuffer()
        self.assertIsNotNone(fb)

    def test_gpu(self):
        self.assertTrue(self.backend.gpu.initialize())
        buf = self.backend.gpu.allocate_buffer(640, 480)
        self.assertIsNotNone(buf)
        frame = self.backend.gpu.render_frame(640, 480)
        self.assertIsNotNone(frame)

    def test_input(self):
        events = self.backend.input_backend.poll_events()
        self.assertIsInstance(events, list)
        self.assertTrue(self.backend.input_backend.grab_keyboard())
        self.assertTrue(self.backend.input_backend.release_keyboard())

    def test_compositor(self):
        sid = self.backend.compositor.create_surface(800, 600)
        self.assertIsNotNone(sid)
        self.backend.compositor.commit_surface(sid)
        self.assertTrue(self.backend.compositor.destroy_surface(sid))

    def test_filesystem(self):
        self.assertTrue(self.backend.filesystem.mkdir("/tmp/test_nyrqis"))
        self.assertTrue(self.backend.filesystem.write_file("/tmp/test_nyrqis/test.txt", b"hello"))
        data = self.backend.filesystem.read_file("/tmp/test_nyrqis/test.txt")
        self.assertEqual(data, b"hello")


class TestLinuxBackend(unittest.TestCase):
    def setUp(self):
        self.backend = get_backend(BackendType.LINUX)

    def test_backend_set(self):
        self.assertIsInstance(self.backend, BackendSet)
        self.assertEqual(self.backend.backend_type, BackendType.LINUX)

    def test_display(self):
        monitors = self.backend.display.enumerate_monitors()
        self.assertGreater(len(monitors), 0)

    def test_gpu(self):
        self.assertTrue(self.backend.gpu.initialize())

    def test_compositor(self):
        sid = self.backend.compositor.create_surface(800, 600)
        self.assertIsNotNone(sid)
        self.assertTrue(self.backend.compositor.destroy_surface(sid))

    def test_filesystem(self):
        dirs = self.backend.filesystem.list_dir("/tmp")
        self.assertIsInstance(dirs, list)


class TestNyrqisBackend(unittest.TestCase):
    def setUp(self):
        self.backend = get_backend(BackendType.NYRQIS)

    def test_backend_set(self):
        self.assertIsInstance(self.backend, BackendSet)
        self.assertEqual(self.backend.backend_type, BackendType.NYRQIS)

    def test_display(self):
        monitors = self.backend.display.enumerate_monitors()
        self.assertGreater(len(monitors), 0)

    def test_gpu(self):
        self.assertTrue(self.backend.gpu.initialize())

    def test_compositor(self):
        sid = self.backend.compositor.create_surface(800, 600)
        self.assertIsNotNone(sid)
        self.assertTrue(self.backend.compositor.destroy_surface(sid))


class TestBackendSwitching(unittest.TestCase):
    def test_switch_linux_to_nyrqis(self):
        b1 = switch_backend(BackendType.LINUX)
        self.assertEqual(b1.backend_type, BackendType.LINUX)
        b2 = switch_backend(BackendType.NYRQIS)
        self.assertEqual(b2.backend_type, BackendType.NYRQIS)

    def test_switch_to_headless(self):
        b = switch_backend(BackendType.HEADLESS)
        self.assertEqual(b.backend_type, BackendType.HEADLESS)

    def test_get_backend_caches(self):
        b1 = get_backend(BackendType.HEADLESS)
        b2 = get_backend()  # Should return same cached
        self.assertIs(b1, b2)


class TestAbstractInterfaces(unittest.TestCase):
    def test_isinstance_checks(self):
        self.assertTrue(issubclass(HeadlessDisplayBackend, DisplayBackend))
        self.assertTrue(issubclass(HeadlessGPUBackend, GPUBackend))
        self.assertTrue(issubclass(HeadlessInputBackend, InputBackend))
        self.assertTrue(issubclass(HeadlessCompositorBackend, CompositorBackend))
        self.assertTrue(issubclass(HeadlessFilesystemBackend, FilesystemBackend))

    def test_all_linux_implement(self):
        self.assertTrue(issubclass(LinuxDisplayBackend, DisplayBackend))
        self.assertTrue(issubclass(LinuxGPUBackend, GPUBackend))
        self.assertTrue(issubclass(LinuxInputBackend, InputBackend))
        self.assertTrue(issubclass(LinuxCompositorBackend, CompositorBackend))
        self.assertTrue(issubclass(LinuxFilesystemBackend, FilesystemBackend))

    def test_all_nyrqis_implement(self):
        self.assertTrue(issubclass(NyrqisDisplayBackend, DisplayBackend))
        self.assertTrue(issubclass(NyrqisGPUBackend, GPUBackend))
        self.assertTrue(issubclass(NyrqisInputBackend, InputBackend))
        self.assertTrue(issubclass(NyrqisCompositorBackend, CompositorBackend))
        self.assertTrue(issubclass(NyrqisFilesystemBackend, FilesystemBackend))


if __name__ == "__main__":
    unittest.main()
