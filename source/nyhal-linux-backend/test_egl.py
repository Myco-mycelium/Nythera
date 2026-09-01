"""Tests for the EGL integration crate.

Covers the Python FFI bindings for the EGL crate.
The actual EGL rendering requires Mesa/EGL drivers and is tested manually.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from ui import egl_codec


class TestEglCodecAvailable(unittest.TestCase):
    """Test EGL codec availability detection."""

    def test_is_available_returns_bool(self):
        """is_available() should return a boolean."""
        result = egl_codec.is_available()
        self.assertIsInstance(result, bool)

    def test_egl_version_returns_int(self):
        """egl_version() should return an integer."""
        v = egl_codec.egl_version()
        self.assertIsInstance(v, int)


class TestEglCodecDisplayOps(unittest.TestCase):
    """Test EGL display operations."""

    def test_get_display_returns_valid_id(self):
        """get_display() should return a non-negative ID."""
        if not egl_codec.is_available():
            self.skipTest("EGL crate not available")
        display_id = egl_codec.get_display()
        self.assertGreaterEqual(display_id, 0)
        egl_codec.terminate(display_id)

    def test_initialize_invalid_display(self):
        """initialize() with invalid display returns False."""
        if not egl_codec.is_available():
            self.skipTest("EGL crate not available")
        result = egl_codec.initialize(-1)
        self.assertFalse(result)

    def test_choose_config_invalid_display(self):
        """choose_config() with invalid display returns -1."""
        if not egl_codec.is_available():
            self.skipTest("EGL crate not available")
        result = egl_codec.choose_config(-1)
        self.assertEqual(result, -1)

    def test_create_window_surface_invalid_display(self):
        """create_window_surface() with invalid display returns -1."""
        if not egl_codec.is_available():
            self.skipTest("EGL crate not available")
        result = egl_codec.create_window_surface(-1, 800, 600)
        self.assertEqual(result, -1)

    def test_create_context_invalid_display(self):
        """create_context() with invalid display returns -1."""
        if not egl_codec.is_available():
            self.skipTest("EGL crate not available")
        result = egl_codec.create_context(-1)
        self.assertEqual(result, -1)

    def test_destroy_surface_invalid_id(self):
        """destroy_surface() with invalid ID returns False."""
        if not egl_codec.is_available():
            self.skipTest("EGL crate not available")
        result = egl_codec.destroy_surface(-1)
        self.assertFalse(result)

    def test_destroy_context_invalid_id(self):
        """destroy_context() with invalid ID returns False."""
        if not egl_codec.is_available():
            self.skipTest("EGL crate not available")
        result = egl_codec.destroy_context(-1)
        self.assertFalse(result)

    def test_terminate_invalid_display(self):
        """terminate() with invalid display returns False."""
        if not egl_codec.is_available():
            self.skipTest("EGL crate not available")
        result = egl_codec.terminate(-1)
        self.assertFalse(result)


class TestEglCodecErrorHandling(unittest.TestCase):
    """Test EGL codec error handling."""

    def test_last_error_returns_string(self):
        """last_error() should return a string."""
        result = egl_codec.last_error()
        self.assertIsInstance(result, str)


class TestEglCodecLifecycle(unittest.TestCase):
    """Test EGL display lifecycle."""

    def test_full_display_lifecycle(self):
        """Complete display lifecycle: get → init → config → surface → context → destroy → terminate."""
        if not egl_codec.is_available():
            self.skipTest("EGL crate not available")

        display_id = egl_codec.get_display()
        self.assertGreaterEqual(display_id, 0)

        self.assertTrue(egl_codec.initialize(display_id))

        config_id = egl_codec.choose_config(display_id)
        self.assertGreaterEqual(config_id, 0)

        surface_id = egl_codec.create_window_surface(display_id, 1920, 1080)
        self.assertGreaterEqual(surface_id, 0)

        context_id = egl_codec.create_context(display_id)
        self.assertGreaterEqual(context_id, 0)

        self.assertTrue(egl_codec.destroy_context(context_id))
        self.assertTrue(egl_codec.destroy_surface(surface_id))
        self.assertTrue(egl_codec.terminate(display_id))


if __name__ == "__main__":
    unittest.main()
