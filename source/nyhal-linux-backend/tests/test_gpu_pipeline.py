"""test_gpu_pipeline — Integration tests for the full GPU rendering pipeline.

Tests the GBM → EGL → DRM pipeline on real hardware when available.
These tests verify that GPU buffers can be allocated, rendered to, and
displayed via DRM modesetting.

References:
    - ADR-0026 Phase 3: GPU acceleration
    - ADR-0010: Vulkan as native graphics API
    - NEXT_SESSION_PLAN: Priority 2 (Real GBM/DRM/EGL/Vulkan Integration)
"""

from __future__ import annotations

import os
import sys
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


class TestGBMRealHardware(unittest.TestCase):
    """Tests for real GBM hardware integration."""

    def test_gbm_available(self):
        """GBM crate is loaded and available."""
        from ui.gbm_codec import is_available
        self.assertTrue(is_available(), "GBM crate not available")

    def test_gbm_device_open_default(self):
        """GBM can open the default DRM render node."""
        from ui.gbm_codec import open_device, close_device
        dev = open_device()
        if dev < 0:
            self.skipTest("Cannot open default render node")
        try:
            self.assertGreaterEqual(dev, 0)
        finally:
            close_device(dev)

    def test_gbm_device_open_renderD128(self):
        """GBM can open /dev/dri/renderD128."""
        from ui.gbm_codec import open_device, close_device
        if not os.path.exists("/dev/dri/renderD128"):
            self.skipTest("renderD128 not available")
        dev = open_device("/dev/dri/renderD128")
        try:
            self.assertGreaterEqual(dev, 0, f"Failed to open renderD128")
        finally:
            if dev >= 0:
                close_device(dev)

    def test_gbm_surface_create(self):
        """GBM can create a surface on a real device."""
        from ui.gbm_codec import (
            open_device, close_device, create_surface, destroy_surface
        )
        dev = open_device()
        if dev < 0:
            self.skipTest("Cannot open render node")
        try:
            surf = create_surface(dev, 1920, 1080)
            self.assertGreaterEqual(surf, 0, "Failed to create surface")
            destroy_surface(surf)
        finally:
            close_device(dev)

    def test_gbm_full_lifecycle(self):
        """GBM full lifecycle: device → surface → buffer → release → close."""
        from ui.gbm_codec import (
            open_device, close_device, create_surface, destroy_surface,
            lock_buffer, release_buffer, get_buffer_info
        )
        dev = open_device()
        if dev < 0:
            self.skipTest("Cannot open render node")
        try:
            surf = create_surface(dev, 1920, 1080)
            self.assertGreaterEqual(surf, 0)

            buf = lock_buffer(surf)
            self.assertGreaterEqual(buf, 0)

            info = get_buffer_info(buf)
            self.assertIsNotNone(info)
            self.assertEqual(info[0], 1920)  # width
            self.assertEqual(info[1], 1080)  # height
            self.assertEqual(info[2], 1920 * 4)  # stride (ARGB8888)

            release_buffer(buf)
            destroy_surface(surf)
        finally:
            close_device(dev)

    def test_gbm_multiple_surfaces(self):
        """GBM supports multiple surfaces per device."""
        from ui.gbm_codec import (
            open_device, close_device, create_surface, destroy_surface,
            lock_buffer, release_buffer
        )
        dev = open_device()
        if dev < 0:
            self.skipTest("Cannot open render node")
        try:
            surfaces = []
            for w, h in [(800, 600), (1024, 768), (1920, 1080)]:
                surf = create_surface(dev, w, h)
                self.assertGreaterEqual(surf, 0)
                surfaces.append(surf)

            for surf in surfaces:
                buf = lock_buffer(surf)
                self.assertGreaterEqual(buf, 0)
                release_buffer(buf)
                destroy_surface(surf)
        finally:
            close_device(dev)


class TestEGLRealHardware(unittest.TestCase):
    """Tests for real EGL hardware integration."""

    def test_egl_available(self):
        """EGL crate is loaded and available."""
        from ui.egl_codec import is_available
        self.assertTrue(is_available(), "EGL crate not available")

    def test_egl_display(self):
        """EGL can get a display."""
        from ui.egl_codec import get_display, terminate
        display = get_display()
        self.assertGreaterEqual(display, 0, "Failed to get EGL display")
        terminate(display)

    def test_egl_initialize(self):
        """EGL can initialize."""
        from ui.egl_codec import get_display, initialize, terminate
        display = get_display()
        if display < 0:
            self.skipTest("Cannot get EGL display")
        try:
            ok = initialize(display)
            self.assertTrue(ok, "EGL initialize failed")
        finally:
            terminate(display)

    def test_egl_config(self):
        """EGL can choose a config."""
        from ui.egl_codec import get_display, initialize, choose_config, terminate
        display = get_display()
        if display < 0:
            self.skipTest("Cannot get EGL display")
        try:
            initialize(display)
            config = choose_config(display)
            self.assertGreaterEqual(config, 0, "Failed to choose EGL config")
        finally:
            terminate(display)

    def test_egl_context(self):
        """EGL can create a context."""
        from ui.egl_codec import (
            get_display, initialize, choose_config,
            create_context, destroy_context, terminate
        )
        display = get_display()
        if display < 0:
            self.skipTest("Cannot get EGL display")
        try:
            initialize(display)
            config = choose_config(display)
            if config < 0:
                self.skipTest("Cannot choose EGL config")
            ctx = create_context(display, config)
            self.assertGreaterEqual(ctx, 0, "Failed to create EGL context")
            destroy_context(ctx)
        finally:
            terminate(display)

    def test_egl_full_lifecycle(self):
        """EGL full lifecycle: display → init → config → context → destroy → terminate."""
        from ui.egl_codec import (
            get_display, initialize, choose_config,
            create_context, destroy_context, terminate
        )
        display = get_display()
        if display < 0:
            self.skipTest("Cannot get EGL display")
        try:
            ok = initialize(display)
            self.assertTrue(ok)

            config = choose_config(display)
            self.assertGreaterEqual(config, 0)

            ctx = create_context(display, config)
            self.assertGreaterEqual(ctx, 0)

            destroy_context(ctx)
        finally:
            terminate(display)


class TestVulkanRealHardware(unittest.TestCase):
    """Tests for real Vulkan hardware integration."""

    def test_vulkan_available(self):
        """Vulkan crate is loaded and available."""
        from ui.vulkan_codec import is_available
        self.assertTrue(is_available(), "Vulkan crate not available")

    def test_vulkan_instance(self):
        """Vulkan can create an instance."""
        from ui.vulkan_codec import create_instance, destroy_instance
        inst = create_instance()
        if inst < 0:
            self.skipTest("No Vulkan driver available")
        try:
            self.assertGreaterEqual(inst, 0)
        finally:
            destroy_instance(inst)

    def test_vulkan_device(self):
        """Vulkan can create a device."""
        from ui.vulkan_codec import (
            create_instance, destroy_instance,
            create_device, destroy_device
        )
        inst = create_instance()
        if inst < 0:
            self.skipTest("No Vulkan driver available")
        try:
            dev = create_device(inst)
            self.assertGreaterEqual(dev, 0, "Failed to create Vulkan device")
            destroy_device(dev)
        finally:
            destroy_instance(inst)

    def test_vulkan_swapchain(self):
        """Vulkan can create a swapchain."""
        from ui.vulkan_codec import (
            create_instance, destroy_instance,
            create_device, destroy_device,
            create_swapchain, destroy_swapchain
        )
        inst = create_instance()
        if inst < 0:
            self.skipTest("No Vulkan driver available")
        try:
            dev = create_device(inst)
            if dev < 0:
                self.skipTest("No Vulkan device available")
            try:
                sc = create_swapchain(dev, 1920, 1080, 3)
                self.assertGreaterEqual(sc, 0, "Failed to create swapchain")
                destroy_swapchain(sc)
            finally:
                destroy_device(dev)
        finally:
            destroy_instance(inst)

    def test_vulkan_full_lifecycle(self):
        """Vulkan full lifecycle: instance → device → swapchain → acquire → destroy."""
        from ui.vulkan_codec import (
            create_instance, destroy_instance,
            create_device, destroy_device,
            create_swapchain, destroy_swapchain,
            acquire_next_image
        )
        inst = create_instance()
        if inst < 0:
            self.skipTest("No Vulkan driver available")
        try:
            dev = create_device(inst)
            if dev < 0:
                self.skipTest("No Vulkan device available")
            try:
                sc = create_swapchain(dev, 1920, 1080, 3)
                if sc < 0:
                    self.skipTest("Cannot create swapchain")
                try:
                    img = acquire_next_image(sc)
                    self.assertGreaterEqual(img, 0, "Failed to acquire image")
                finally:
                    destroy_swapchain(sc)
            finally:
                destroy_device(dev)
        finally:
            destroy_instance(inst)


class TestDRMRealHardware(unittest.TestCase):
    """Tests for real DRM hardware integration."""

    def test_drm_available(self):
        """DRM crate is loaded and available."""
        from ui.drm_codec import is_available
        self.assertTrue(is_available(), "DRM crate not available")

    def test_drm_device_open(self):
        """DRM can open a device."""
        from ui.drm_codec import open_device, close_device
        dev = open_device()
        if dev < 0:
            self.skipTest("Cannot open DRM device")
        try:
            self.assertGreaterEqual(dev, 0)
        finally:
            close_device(dev)


class TestCompositorRealHardware(unittest.TestCase):
    """Tests for real compositor hardware integration."""

    def test_compositor_available(self):
        """Compositor crate is loaded and available."""
        from ui.compositor_codec import available
        self.assertTrue(available(), "Compositor crate not available")

    def test_compositor_lifecycle(self):
        """Compositor full lifecycle: start → output → surface → input → stop."""
        from ui.compositor_codec import (
            start, stop, is_running,
            add_output, output_count,
            create_surface, destroy_surface, surface_count,
            process_input, send_frame_callback, commit_surface
        )
        self.assertEqual(start(), 0)
        self.assertTrue(is_running())

        count_before = output_count()
        out = add_output(1920, 1080, "test")
        self.assertGreaterEqual(out, 0)
        self.assertEqual(output_count(), count_before + 1)

        scount_before = surface_count()
        surf = create_surface(0, 800, 600)
        self.assertGreaterEqual(surf, 0)
        self.assertEqual(surface_count(), scount_before + 1)

        self.assertEqual(process_input(1, surf, 0, 0, 0.0, 0.0), 0)
        self.assertEqual(send_frame_callback(surf, 0), 0)
        self.assertEqual(commit_surface(surf), 0)

        self.assertEqual(destroy_surface(surf), 0)
        self.assertEqual(surface_count(), scount_before)

        self.assertEqual(stop(), 0)
        self.assertFalse(is_running())


if __name__ == "__main__":
    unittest.main()
