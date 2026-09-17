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

    def test_open_device_invalid_path_fails_in_both_modes(self):
        """open_device() on a bogus path returns -1 in stub mode AND
        with the real crate loaded (the real driver also refuses to
        open a nonexistent card) — no skip either way."""
        result = drm_codec.open_device("/nonexistent/nyrqis-test-card")
        self.assertEqual(result, -1)

    def test_open_device_default_path_matches_mode(self):
        """open_device() with the default path: -1 in stub mode; with
        the crate, a valid fd (which is then closed cleanly) or -1 on
        hosts without a DRM node."""
        result = drm_codec.open_device()
        if drm_codec.is_available():
            if result >= 0:
                self.assertTrue(drm_codec.close_device(result))
        else:
            self.assertEqual(result, -1)

    def test_enumerate_connectors_invalid_device(self):
        """enumerate_connectors() with invalid device returns -1 in
        both stub and crate modes (invalid handles are errors, never
        exceptions)."""
        result = drm_codec.enumerate_connectors(-1)
        self.assertEqual(result, -1)

    def test_get_connector_info_invalid_id(self):
        """get_connector_info() with invalid ID returns None in both
        modes."""
        result = drm_codec.get_connector_info(-1)
        self.assertIsNone(result)

    def test_atomic_commit_invalid(self):
        """atomic_commit() with invalid params returns False in both
        modes."""
        result = drm_codec.atomic_commit(-1, 0, 0, 1)
        self.assertFalse(result)

    def test_close_device_invalid(self):
        """close_device() with invalid ID returns False in both
        modes."""
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
