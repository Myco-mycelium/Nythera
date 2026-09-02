"""Tests for the Vulkan rendering crate.

Covers the Python FFI bindings for the Vulkan crate.
The actual Vulkan rendering requires Mesa/Vulkan drivers and is tested manually.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from ui import vulkan_codec


class TestVulkanCodecAvailable(unittest.TestCase):
    """Test Vulkan codec availability detection."""

    def test_is_available_returns_bool(self):
        """is_available() should return a boolean."""
        result = vulkan_codec.is_available()
        self.assertIsInstance(result, bool)

    def test_vulkan_version_returns_int(self):
        """vulkan_version() should return an integer."""
        v = vulkan_codec.vulkan_version()
        self.assertIsInstance(v, int)


class TestVulkanCodecInstanceOps(unittest.TestCase):
    """Test Vulkan instance operations."""

    def test_create_instance(self):
        """create_instance() should return a non-negative ID."""
        if not vulkan_codec.is_available():
            self.skipTest("Vulkan crate not available")
        instance_id = vulkan_codec.create_instance()
        self.assertGreaterEqual(instance_id, 0)
        vulkan_codec.destroy_instance(instance_id)

    def test_destroy_instance_invalid_id(self):
        """destroy_instance() with invalid ID returns False."""
        if not vulkan_codec.is_available():
            self.skipTest("Vulkan crate not available")
        result = vulkan_codec.destroy_instance(-1)
        self.assertFalse(result)


class TestVulkanCodecDeviceOps(unittest.TestCase):
    """Test Vulkan device operations."""

    def test_create_device_invalid_instance(self):
        """create_device() with invalid instance returns -1."""
        if not vulkan_codec.is_available():
            self.skipTest("Vulkan crate not available")
        result = vulkan_codec.create_device(-1)
        self.assertEqual(result, -1)

    def test_destroy_device_invalid_id(self):
        """destroy_device() with invalid ID returns False."""
        if not vulkan_codec.is_available():
            self.skipTest("Vulkan crate not available")
        result = vulkan_codec.destroy_device(-1)
        self.assertFalse(result)


class TestVulkanCodecSwapchainOps(unittest.TestCase):
    """Test Vulkan swapchain operations."""

    def test_create_swapchain_invalid_device(self):
        """create_swapchain() with invalid device returns -1."""
        if not vulkan_codec.is_available():
            self.skipTest("Vulkan crate not available")
        result = vulkan_codec.create_swapchain(-1, 1920, 1080, 3)
        self.assertEqual(result, -1)

    def test_destroy_swapchain_invalid_id(self):
        """destroy_swapchain() with invalid ID returns False."""
        if not vulkan_codec.is_available():
            self.skipTest("Vulkan crate not available")
        result = vulkan_codec.destroy_swapchain(-1)
        self.assertFalse(result)

    def test_acquire_next_image_invalid_swapchain(self):
        """acquire_next_image() with invalid swapchain returns -1."""
        if not vulkan_codec.is_available():
            self.skipTest("Vulkan crate not available")
        result = vulkan_codec.acquire_next_image(-1)
        self.assertEqual(result, -1)


class TestVulkanCodecErrorHandling(unittest.TestCase):
    """Test Vulkan codec error handling."""

    def test_last_error_returns_string(self):
        """last_error() should return a string."""
        result = vulkan_codec.last_error()
        self.assertIsInstance(result, str)


class TestVulkanCodecLifecycle(unittest.TestCase):
    """Test Vulkan full lifecycle."""

    def test_full_vulkan_lifecycle(self):
        """Complete Vulkan lifecycle: instance → device → swapchain → destroy."""
        if not vulkan_codec.is_available():
            self.skipTest("Vulkan crate not available")

        instance_id = vulkan_codec.create_instance()
        if instance_id < 0:
            self.skipTest("No Vulkan driver available on this hardware")
        self.assertGreaterEqual(instance_id, 0)

        device_id = vulkan_codec.create_device(instance_id)
        self.assertGreaterEqual(device_id, 0)

        swapchain_id = vulkan_codec.create_swapchain(device_id, 1920, 1080, 3)
        self.assertGreaterEqual(swapchain_id, 0)

        img = vulkan_codec.acquire_next_image(swapchain_id)
        self.assertGreaterEqual(img, 0)

        self.assertTrue(vulkan_codec.destroy_swapchain(swapchain_id))
        self.assertTrue(vulkan_codec.destroy_device(device_id))
        self.assertTrue(vulkan_codec.destroy_instance(instance_id))


if __name__ == "__main__":
    unittest.main()
