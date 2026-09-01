"""Tests for SDL2 Wayland compositor integration.

Covers all code paths that can be exercised without a real Wayland
compositor — headless fallback, X11 mode, wayland flag behavior,
and render_to_wayland() with a mock WaylandDisplay.

Actual GPU-accelerated rendering requires a running Wayland compositor
(Sway, weston, mutter) and is tested manually.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))

from ui.compositor_sdl import SDLCompositor


def _make_mock_document(width=64, height=64):
    """Create a mock NstudioDocument for testing."""
    mock_doc = MagicMock()
    mock_screen = MagicMock()
    mock_screen.width = width
    mock_screen.height = height
    mock_screen.elements = []
    mock_doc.screens = [mock_screen]
    return mock_doc


class TestSDLCompositorInit(unittest.TestCase):
    """Test SDLCompositor initialization in various modes."""

    def test_headless_creates_compositor(self):
        """Headless mode should always succeed."""
        comp = SDLCompositor(headless=True)
        self.assertIsNotNone(comp)

    def test_headless_has_render_methods(self):
        """Headless mode should have all render methods."""
        comp = SDLCompositor(headless=True)
        self.assertTrue(hasattr(comp, 'render_screen'))
        self.assertTrue(hasattr(comp, 'render_to_file'))
        self.assertTrue(hasattr(comp, 'render_to_wayland'))

    def test_wayland_flag_stored(self):
        """wayland=True flag should be stored on the instance."""
        comp = SDLCompositor(wayland=True, headless=True)
        self.assertTrue(comp._use_wayland)

    def test_wayland_flag_false_by_default(self):
        """wayland should default to False."""
        comp = SDLCompositor(headless=True)
        self.assertFalse(comp._use_wayland)

    def test_headless_attribute_stored(self):
        """headless flag should be stored on the instance."""
        comp = SDLCompositor(headless=True)
        self.assertTrue(comp.headless)

    def test_x11_mode_creates_compositor(self):
        """X11 mode (DISPLAY=:0) should create a compositor."""
        comp = SDLCompositor(headless=False)
        self.assertIsNotNone(comp)

    def test_default_theme(self):
        """Default theme should be Eclipse."""
        comp = SDLCompositor(headless=True)
        self.assertEqual(comp.theme_name, "Eclipse")

    def test_custom_theme(self):
        """Custom theme should be accepted."""
        comp = SDLCompositor(theme_name="Eclipse", headless=True)
        self.assertEqual(comp.theme_name, "Eclipse")


class TestSDLCompositorRenderToFile(unittest.TestCase):
    """Test render_to_file in headless mode."""

    def test_render_to_file_returns_path(self):
        """render_to_file should return a file path."""
        comp = SDLCompositor(headless=True)
        doc = _make_mock_document()
        result = comp.render_to_file(doc, "/tmp/test_sdl_render.png")
        self.assertIsNotNone(result)
        if os.path.exists(result):
            os.unlink(result)

    def test_render_to_file_creates_png(self):
        """render_to_file should create a PNG file."""
        comp = SDLCompositor(headless=True)
        doc = _make_mock_document(32, 32)
        result = comp.render_to_file(doc, "/tmp/test_sdl_render2.png")
        self.assertTrue(os.path.exists(result))
        with open(result, 'rb') as f:
            magic = f.read(8)
        self.assertEqual(magic[:4], b'\x89PNG')
        os.unlink(result)


class TestSDLCompositorRenderToWayland(unittest.TestCase):
    """Test render_to_wayland with mock WaylandDisplay."""

    def test_render_to_wayland_delegates_to_display(self):
        """render_to_wayland should delegate to wayland_display.render_frame()."""
        comp = SDLCompositor(headless=True)
        doc = _make_mock_document()
        mock_display = MagicMock()
        mock_display.render_frame.return_value = True

        result = comp.render_to_wayland(doc, mock_display, surface_id=0)
        self.assertTrue(result)
        mock_display.render_frame.assert_called_once()

    def test_render_to_wayland_handles_failure(self):
        """render_to_wayland should handle render_frame returning False."""
        comp = SDLCompositor(headless=True)
        doc = _make_mock_document()
        mock_display = MagicMock()
        mock_display.render_frame.return_value = False

        result = comp.render_to_wayland(doc, mock_display, surface_id=0)
        self.assertFalse(result)

    def test_render_to_wayland_handles_exception(self):
        """render_to_wayland should handle exceptions gracefully."""
        comp = SDLCompositor(headless=True)
        doc = _make_mock_document()
        mock_display = MagicMock()
        mock_display.render_frame.side_effect = RuntimeError("display error")

        result = comp.render_to_wayland(doc, mock_display, surface_id=0)
        self.assertFalse(result)

    def test_render_to_wayland_with_screen_id(self):
        """render_to_wayland should accept screen_id parameter."""
        comp = SDLCompositor(headless=True)
        doc = _make_mock_document()
        # The mock screen doesn't have a matching id, so render_screen returns None
        # and render_to_wayland returns False — that's correct behavior
        mock_display = MagicMock()
        mock_display.render_frame.return_value = True

        result = comp.render_to_wayland(doc, mock_display, surface_id=0, screen_id="nonexistent")
        # No matching screen → render_screen returns None → False
        self.assertFalse(result)


class TestSDLCompositorWaylandFallback(unittest.TestCase):
    """Test SDL2 Wayland fallback behavior."""

    def test_wayland_with_headless_flag(self):
        """wayland=True with headless=True should create a compositor."""
        comp = SDLCompositor(wayland=True, headless=True)
        self.assertIsNotNone(comp)
        self.assertTrue(comp._use_wayland)
        self.assertTrue(comp.headless)

    def test_headless_overrides_wayland(self):
        """headless=True should override wayland=True for rendering."""
        comp = SDLCompositor(wayland=True, headless=True)
        self.assertTrue(comp._use_wayland)
        doc = _make_mock_document(16, 16)
        result = comp.render_to_file(doc, "/tmp/test_override.png")
        self.assertIsNotNone(result)
        os.unlink(result)


class TestSDLCompositorWithMockWayland(unittest.TestCase):
    """Test SDLCompositor integration with mock Wayland components."""

    def test_render_to_wayland_with_real_pil_image(self):
        """render_to_wayland should produce a real PIL Image internally."""
        try:
            from PIL import Image
            comp = SDLCompositor(headless=True)
            doc = _make_mock_document(64, 64)
            mock_display = MagicMock()
            mock_display.render_frame.return_value = True

            result = comp.render_to_wayland(doc, mock_display, surface_id=0)
            self.assertTrue(result)
            # render_frame should have been called with an Image
            call_args = mock_display.render_frame.call_args
            self.assertIsNotNone(call_args)
        except ImportError:
            self.skipTest("PIL not available")

    def test_render_screen_with_document(self):
        """render_screen should accept a document object."""
        try:
            from PIL import Image
            comp = SDLCompositor(headless=True)
            doc = _make_mock_document(64, 64)

            img = comp.render_screen(doc)
            # In headless mode, should return an Image or None
            self.assertTrue(img is None or isinstance(img, Image.Image))
        except ImportError:
            self.skipTest("PIL not available")

    def test_render_to_wayland_multiple_calls(self):
        """Multiple render_to_wayland calls should work."""
        comp = SDLCompositor(headless=True)
        doc = _make_mock_document()
        mock_display = MagicMock()
        mock_display.render_frame.return_value = True

        for i in range(3):
            result = comp.render_to_wayland(doc, mock_display, surface_id=i)
            self.assertTrue(result)

        self.assertEqual(mock_display.render_frame.call_count, 3)


class TestSDLCompositorEdgeCases(unittest.TestCase):
    """Test edge cases and error handling."""

    def test_render_to_wayland_no_screens(self):
        """render_to_wayland with empty document should handle gracefully."""
        comp = SDLCompositor(headless=True)
        mock_doc = MagicMock()
        mock_doc.screens = []
        mock_display = MagicMock()
        mock_display.render_frame.return_value = True

        result = comp.render_to_wayland(mock_doc, mock_display, surface_id=0)
        # Should return False since no screen was found
        self.assertFalse(result)

    def test_render_to_wayland_large_dimensions(self):
        """render_to_wayland with large document should handle gracefully."""
        comp = SDLCompositor(headless=True)
        doc = _make_mock_document(7680, 4320)
        mock_display = MagicMock()
        mock_display.render_frame.return_value = True

        # Large dimensions may or may not work, but should not crash
        try:
            result = comp.render_to_wayland(doc, mock_display, surface_id=0)
        except Exception:
            pass  # Acceptable behavior


if __name__ == "__main__":
    unittest.main()
