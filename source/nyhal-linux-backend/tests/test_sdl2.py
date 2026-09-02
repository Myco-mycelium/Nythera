"""test_sdl2 — Tests for SDL2 headless rendering backend.

References:
    - ADR-0026 Phase 3: GPU acceleration
    - ui/sdl2_codec.py
"""

from __future__ import annotations

import os
import struct
import sys
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    os.sys.path.insert(0, _HERE)


class TestSDL2Codec(unittest.TestCase):
    """Tests for the SDL2 codec."""

    def test_sdl2_create(self):
        """Can create an SDL2 codec."""
        from ui.sdl2_codec import SDL2Codec
        sdl = SDL2Codec()
        self.assertFalse(sdl.is_initialized)
    
    def test_sdl2_initialize(self):
        """Can initialize SDL2 (if available)."""
        from ui.sdl2_codec import SDL2Codec
        sdl = SDL2Codec()
        
        result = sdl.initialize()
        if result:
            self.assertTrue(sdl.is_initialized)
            sdl.shutdown()
        else:
            self.skipTest("SDL2 not available")
    
    def test_sdl2_create_surface(self):
        """Can create a surface (if SDL2 available)."""
        from ui.sdl2_codec import SDL2Codec
        sdl = SDL2Codec()
        
        if not sdl.initialize():
            self.skipTest("SDL2 not available")
        
        try:
            surface = sdl.create_surface(800, 600)
            self.assertIsNotNone(surface)
            self.assertEqual(surface.width, 800)
            self.assertEqual(surface.height, 600)
            
            info = sdl.get_surface_info(surface)
            self.assertEqual(info["width"], 800)
            self.assertEqual(info["height"], 600)
            
            sdl.destroy_surface(surface)
        finally:
            sdl.shutdown()
    
    def test_sdl2_shutdown_idempotent(self):
        """Shutdown is idempotent."""
        from ui.sdl2_codec import SDL2Codec
        sdl = SDL2Codec()
        sdl.shutdown()
        sdl.shutdown()
    
    def test_sdl2_stats(self):
        """Get statistics."""
        from ui.sdl2_codec import SDL2Codec
        sdl = SDL2Codec()
        stats = sdl.get_stats()
        self.assertIn("initialized", stats)
        self.assertIn("surfaces", stats)
        self.assertFalse(stats["initialized"])
        self.assertEqual(stats["surfaces"], 0)


class TestSDL2Integration(unittest.TestCase):
    """Integration tests for SDL2 with the render pipeline."""

    def test_render_pipeline_with_sdl2(self):
        """Render pipeline can use SDL2 as backend."""
        from ui.render_pipeline import RenderPipeline, RenderConfig
        
        config = RenderConfig(width=640, height=480, use_gbm=False, use_egl=False)
        pipeline = RenderPipeline(config)
        
        stats = pipeline.get_stats()
        self.assertIn("frame_count", stats)
        
        pipeline.cleanup()


if __name__ == "__main__":
    unittest.main()
