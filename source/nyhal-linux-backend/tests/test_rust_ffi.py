"""
Tests for ui.rust_ffi — Python wrappers for all Nyrqis Rust crates.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRustCompositor(unittest.TestCase):
    """Tests for RustCompositor FFI wrapper."""

    def setUp(self):
        from ui.rust_ffi import RustCompositor
        import ui.rust_ffi as mod
        mod._rust_backend = None
        self.comp = RustCompositor()
        # Ensure clean state: stop and destroy all surfaces
        if self.comp.available:
            if self.comp.is_running():
                self.comp.stop()
            # Ensure stopped
            self.assertFalse(self.comp.is_running())

    def tearDown(self):
        if self.comp.available and self.comp.is_running():
            self.comp.stop()

    def test_available(self):
        self.assertIsInstance(self.comp.available, bool)

    def test_version(self):
        v = self.comp.version()
        self.assertIsInstance(v, int)
        if self.comp.available:
            self.assertGreaterEqual(v, 0x0000_0100)

    def test_start_stop(self):
        if not self.comp.available:
            self.skipTest("Compositor not available")
        self.assertTrue(self.comp.start())
        self.assertTrue(self.comp.is_running())
        self.assertTrue(self.comp.started)
        self.assertTrue(self.comp.stop())
        self.assertFalse(self.comp.is_running())

    def test_start_idempotent(self):
        if not self.comp.available:
            self.skipTest("Compositor not available")
        self.assertTrue(self.comp.start())
        self.assertTrue(self.comp.start())  # Second start is ok
        self.assertTrue(self.comp.stop())

    def test_add_output(self):
        if not self.comp.available:
            self.skipTest("Compositor not available")
        self.comp.start()
        oid = self.comp.add_output(1920, 1080, "test-output")
        self.assertGreaterEqual(oid, 0)
        self.assertGreaterEqual(self.comp.output_count(), 1)
        self.comp.stop()

    def test_create_surface(self):
        if not self.comp.available:
            self.skipTest("Compositor not available")
        self.comp.start()
        count_before = self.comp.surface_count()
        sid = self.comp.create_surface(0, 800, 600)
        self.assertGreaterEqual(sid, 0)
        self.assertGreaterEqual(self.comp.surface_count(), count_before + 1)
        self.comp.stop()

    def test_destroy_surface(self):
        if not self.comp.available:
            self.skipTest("Compositor not available")
        self.comp.start()
        count_before = self.comp.surface_count()
        sid = self.comp.create_surface(0, 800, 600)
        self.assertGreaterEqual(self.comp.surface_count(), count_before + 1)
        self.assertTrue(self.comp.destroy_surface(sid))
        self.comp.stop()

    def test_commit_surface(self):
        if not self.comp.available:
            self.skipTest("Compositor not available")
        self.comp.start()
        sid = self.comp.create_surface(0, 800, 600)
        self.assertTrue(self.comp.commit_surface(sid))
        self.comp.stop()

    def test_last_error(self):
        if not self.comp.available:
            self.skipTest("Compositor not available")
        err = self.comp.last_error()
        self.assertIsInstance(err, str)

    def test_surface_lifecycle(self):
        if not self.comp.available:
            self.skipTest("Compositor not available")
        self.comp.start()
        count_before = self.comp.surface_count()
        # Create multiple surfaces
        surfaces = []
        for i in range(5):
            sid = self.comp.create_surface(i, 100 + i * 50, 100 + i * 50)
            surfaces.append(sid)
        self.assertGreaterEqual(self.comp.surface_count(), count_before + 5)
        # Commit all
        for sid in surfaces:
            self.assertTrue(self.comp.commit_surface(sid))
        # Destroy all
        for sid in surfaces:
            self.assertTrue(self.comp.destroy_surface(sid))
        self.comp.stop()


class TestRustDRM(unittest.TestCase):
    """Tests for RustDRM FFI wrapper."""

    def setUp(self):
        from ui.rust_ffi import RustDRM
        self.drm = RustDRM()

    def test_available(self):
        self.assertIsInstance(self.drm.available, bool)

    def test_version(self):
        v = self.drm.version()
        self.assertIsInstance(v, int)

    def test_last_error(self):
        err = self.drm.last_error()
        self.assertIsInstance(err, str)


class TestRustGBM(unittest.TestCase):
    """Tests for RustGBM FFI wrapper."""

    def setUp(self):
        from ui.rust_ffi import RustGBM
        self.gbm = RustGBM()

    def test_available(self):
        self.assertIsInstance(self.gbm.available, bool)

    def test_version(self):
        v = self.gbm.version()
        self.assertIsInstance(v, int)

    def test_last_error(self):
        err = self.gbm.last_error()
        self.assertIsInstance(err, str)


class TestRustEGL(unittest.TestCase):
    """Tests for RustEGL FFI wrapper."""

    def setUp(self):
        from ui.rust_ffi import RustEGL
        self.egl = RustEGL()

    def test_available(self):
        self.assertIsInstance(self.egl.available, bool)

    def test_version(self):
        v = self.egl.version()
        self.assertIsInstance(v, int)

    def test_last_error(self):
        err = self.egl.last_error()
        self.assertIsInstance(err, str)


class TestRustVulkan(unittest.TestCase):
    """Tests for RustVulkan FFI wrapper."""

    def setUp(self):
        from ui.rust_ffi import RustVulkan
        self.vk = RustVulkan()

    def test_available(self):
        self.assertIsInstance(self.vk.available, bool)

    def test_version(self):
        v = self.vk.version()
        self.assertIsInstance(v, int)

    def test_instance_lifecycle(self):
        if not self.vk.available:
            self.skipTest("Vulkan not available")
        iid = self.vk.create_instance()
        self.assertGreaterEqual(iid, 0)
        self.assertTrue(self.vk.destroy_instance())

    def test_device_lifecycle(self):
        if not self.vk.available:
            self.skipTest("Vulkan not available")
        iid = self.vk.create_instance()
        did = self.vk.create_device()
        self.assertGreaterEqual(did, 0)
        self.assertTrue(self.vk.destroy_device())
        self.assertTrue(self.vk.destroy_instance())

    def test_last_error(self):
        err = self.vk.last_error()
        self.assertIsInstance(err, str)


class TestRustBackend(unittest.TestCase):
    """Tests for unified RustBackend."""

    def setUp(self):
        from ui.rust_ffi import RustBackend
        self.rb = RustBackend()

    def test_available(self):
        self.assertIsInstance(self.rb.available, bool)

    def test_info(self):
        info = self.rb.info()
        self.assertIn("compositor", info)
        self.assertIn("drm", info)
        self.assertIn("gbm", info)
        self.assertIn("egl", info)
        self.assertIn("vulkan", info)
        for name, status in info.items():
            self.assertIn("available", status)
            self.assertIn("version", status)

    def test_info_has_versions(self):
        info = self.rb.info()
        for name, status in info.items():
            self.assertIsInstance(status["version"], int)


class TestRustFFIModule(unittest.TestCase):
    """Tests for module-level functions."""

    def test_get_rust_backend_singleton(self):
        from ui.rust_ffi import get_rust_backend
        rb1 = get_rust_backend()
        rb2 = get_rust_backend()
        self.assertIs(rb1, rb2)

    def test_import_all_classes(self):
        from ui.rust_ffi import (
            RustCompositor, RustDRM, RustGBM, RustEGL,
            RustVulkan, RustBackend, get_rust_backend,
        )


if __name__ == "__main__":
    unittest.main()
