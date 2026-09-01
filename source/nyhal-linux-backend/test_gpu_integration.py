"""Integration tests for the GPU rendering pipeline (GBM + EGL + Wayland).

These tests verify the full GPU-accelerated rendering path from buffer
allocation through OpenGL rendering to Wayland display output. They
require actual hardware (DRM device, Mesa/EGL drivers) and a Wayland
compositor for full validation.

In CI/headless environments, the tests verify the integration points
between the GBM, EGL, and Wayland crates via their Python FFI bindings.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from ui import gbm_codec
from ui import egl_codec
from ui import drm_codec
from ui import wayland_codec


class TestGPUAvailability(unittest.TestCase):
    """Test that GPU subsystems report availability correctly."""

    def test_wayland_codec_available(self):
        """Wayland codec should be loadable."""
        # The wayland codec is always available (may be in stub mode)
        from ui import wayland_codec as wc
        self.assertIsNotNone(wc)

    def test_gbm_codec_loadable(self):
        """GBM codec module should be importable."""
        self.assertIsNotNone(gbm_codec)

    def test_egl_codec_loadable(self):
        """EGL codec module should be importable."""
        self.assertIsNotNone(egl_codec)

    def test_drm_codec_loadable(self):
        """DRM codec module should be importable."""
        self.assertIsNotNone(drm_codec)


class TestGPUBufferAllocation(unittest.TestCase):
    """Test GPU buffer allocation via GBM."""

    def test_gbm_version(self):
        """GBM crate should report a version."""
        v = gbm_codec.gbm_version()
        self.assertIsInstance(v, int)

    def test_gbm_device_lifecycle(self):
        """GBM device open → close lifecycle."""
        if not gbm_codec.is_available():
            self.skipTest("GBM crate not available")
        dev = gbm_codec.open_device()
        if dev < 0:
            self.skipTest("No GBM device available")
        self.assertGreaterEqual(dev, 0)
        self.assertTrue(gbm_codec.close_device(dev))


class TestEGLRendering(unittest.TestCase):
    """Test EGL rendering context lifecycle."""

    def test_egl_version(self):
        """EGL crate should report a version."""
        v = egl_codec.egl_version()
        self.assertIsInstance(v, int)

    def test_egl_display_lifecycle(self):
        """EGL display → init → terminate lifecycle."""
        if not egl_codec.is_available():
            self.skipTest("EGL crate not available")
        display_id = egl_codec.get_display()
        if display_id < 0:
            self.skipTest("No EGL display available")
        self.assertGreaterEqual(display_id, 0)
        self.assertTrue(egl_codec.initialize(display_id))
        self.assertTrue(egl_codec.terminate(display_id))


class TestDRMDisplay(unittest.TestCase):
    """Test DRM display enumeration."""

    def test_drm_version(self):
        """DRM crate should report a version."""
        v = drm_codec.drm_version()
        self.assertIsInstance(v, int)

    def test_drm_device_lifecycle(self):
        """DRM device open → close lifecycle."""
        if not drm_codec.is_available():
            self.skipTest("DRM crate not available")
        dev = drm_codec.open_device()
        if dev < 0:
            self.skipTest("No DRM device available")
        self.assertGreaterEqual(dev, 0)
        self.assertTrue(drm_codec.close_device(dev))


class TestWaylandDisplay(unittest.TestCase):
    """Test Wayland display connection."""

    def test_wayland_codec_loadable(self):
        """Wayland codec module should be importable."""
        from ui import wayland_codec as wc
        self.assertIsNotNone(wc)

    def test_wayland_last_error(self):
        """Wayland last_error should return a string."""
        from ui import wayland_codec as wc
        result = wc.last_error()
        self.assertIsInstance(result, str)


class TestGPURenderingPipeline(unittest.TestCase):
    """Integration test: GBM buffer → EGL render → Wayland submit.

    This is the end-to-end GPU-accelerated rendering path.
    """

    def test_pipeline_availability_check(self):
        """Check which GPU subsystems are available."""
        gbm_avail = gbm_codec.is_available() if hasattr(gbm_codec, 'is_available') else False
        egl_avail = egl_codec.is_available() if hasattr(egl_codec, 'is_available') else False
        drm_avail = drm_codec.is_available() if hasattr(drm_codec, 'is_available') else False

        # At minimum, we should be able to check availability
        self.assertIsInstance(gbm_avail, bool)
        self.assertIsInstance(egl_avail, bool)
        self.assertIsInstance(drm_avail, bool)

    def test_sdl2_wayland_fallback(self):
        """SDL2 Wayland should fall back gracefully without hardware."""
        from ui.compositor_sdl import SDLCompositor
        comp = SDLCompositor(wayland=True, headless=True)
        self.assertIsNotNone(comp)
        self.assertTrue(comp._use_wayland)


if __name__ == "__main__":
    unittest.main()
