"""Tests for the DRM atomic modesetting crate integration.

Covers the Python FFI bindings for the DRM crate.
The actual DRM hardware integration requires /dev/dri/card0
and is tested manually.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from ui import drm_codec


class TestDrmCodecAvailable(unittest.TestCase):
    """Test DRM codec availability detection."""

    def test_is_available_returns_bool(self):
        """is_available() should return a boolean."""
        result = drm_codec.is_available()
        self.assertIsInstance(result, bool)

    def test_drm_version_returns_int(self):
        """drm_version() should return an integer."""
        # May return 0 in stub mode
        v = drm_codec.drm_version()
        self.assertIsInstance(v, int)


class TestDrmCodecDeviceOps(unittest.TestCase):
    """Test DRM device operations."""

    def test_open_device_returns_negative_in_stub(self):
        """open_device() returns -1 when DRM is not available."""
        if drm_codec.is_available():
            self.skipTest("DRM crate is available")
        result = drm_codec.open_device()
        self.assertEqual(result, -1)

    def test_open_device_with_path(self):
        """open_device() with a path returns -1 in stub mode."""
        if drm_codec.is_available():
            self.skipTest("DRM crate is available")
        result = drm_codec.open_device("/dev/dri/card0")
        self.assertEqual(result, -1)

    def test_enumerate_connectors_invalid_device(self):
        """enumerate_connectors() with invalid device returns -1."""
        if drm_codec.is_available():
            self.skipTest("DRM crate is available")
        result = drm_codec.enumerate_connectors(-1)
        self.assertEqual(result, -1)

    def test_get_connector_info_invalid_id(self):
        """get_connector_info() with invalid ID returns None."""
        if drm_codec.is_available():
            self.skipTest("DRM crate is available")
        result = drm_codec.get_connector_info(-1)
        self.assertIsNone(result)

    def test_atomic_commit_invalid(self):
        """atomic_commit() with invalid params returns False."""
        if drm_codec.is_available():
            self.skipTest("DRM crate is available")
        result = drm_codec.atomic_commit(-1, 0, 0, 1)
        self.assertFalse(result)

    def test_close_device_invalid(self):
        """close_device() with invalid ID returns False."""
        if drm_codec.is_available():
            self.skipTest("DRM crate is available")
        result = drm_codec.close_device(-1)
        self.assertFalse(result)


class TestDrmCodecErrorHandling(unittest.TestCase):
    """Test DRM codec error handling."""

    def test_last_error_returns_string(self):
        """last_error() should return a string."""
        result = drm_codec.last_error()
        self.assertIsInstance(result, str)


class TestDrmCodecConnectorInfo(unittest.TestCase):
    """Test DRM connector info struct."""

    def test_connector_info_has_fields(self):
        """DrmConnectorInfo should have width, height, refresh, status fields."""
        info = drm_codec.DrmConnectorInfo()
        self.assertTrue(hasattr(info, 'width'))
        self.assertTrue(hasattr(info, 'height'))
        self.assertTrue(hasattr(info, 'refresh'))
        self.assertTrue(hasattr(info, 'status'))


if __name__ == "__main__":
    unittest.main()
