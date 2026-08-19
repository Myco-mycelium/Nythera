#!/usr/bin/env python3
"""Tests for the SDL2-based NUI compositor (ui/compositor_sdl.py)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from ui.compositor_sdl import SDLCompositor, THEMES, HAS_SDL2


def _make_doc(screens=None, states=None):
    """Helper to create a minimal NstudioDocument."""
    from ui.nstudio import NstudioDocument
    return NstudioDocument(
        version="1.0.0",
        project={},
        themes={"active": "Eclipse"},
        states=states or {},
        state_scopes={},
        locales={"active": "en", "tables": {"en": {}}},
        resources={},
        animations=[],
        behaviors=[],
        bindings=[],
        reusable_components=[],
        screens=screens or [],
    )


def _make_screen(screen_id="s", width=400, height=300, root_children=None):
    from ui.nstudio import NstudioScreen, NstudioComponent
    root = NstudioComponent(
        id=f"{screen_id}_root",
        type="Window",
        layout={"x": 0, "y": 0, "width": width, "height": height},
        children=root_children or [],
    )
    return NstudioScreen(
        id=screen_id,
        size={"width": width, "height": height},
        root=root,
    )


@unittest.skipUnless(HAS_SDL2, "pysdl2 not installed")
class TestSDLCompositorInit(unittest.TestCase):
    """SDLCompositor initialization and theme handling."""

    def test_default_theme(self):
        comp = SDLCompositor()
        self.assertEqual(comp.theme_name, "Eclipse")
        self.assertIs(comp.theme, THEMES["Eclipse"])

    def test_solar_theme(self):
        comp = SDLCompositor(theme_name="Solar")
        self.assertEqual(comp.theme_name, "Solar")
        self.assertIs(comp.theme, THEMES["Solar"])

    def test_unknown_theme_falls_back(self):
        comp = SDLCompositor(theme_name="Nonexistent")
        self.assertIs(comp.theme, THEMES["Eclipse"])

    def test_headless_default(self):
        comp = SDLCompositor()
        self.assertTrue(comp.headless)

    def test_windowed_mode(self):
        comp = SDLCompositor(headless=False)
        self.assertFalse(comp.headless)


@unittest.skipUnless(HAS_SDL2, "pysdl2 not installed")
class TestSDLCompositorRender(unittest.TestCase):
    """SDLCompositor renders screens to PIL images in headless mode."""

    def test_render_empty_document(self):
        screen = _make_screen("test", 200, 100)
        doc = _make_doc(screens=[screen])
        comp = SDLCompositor(headless=True)
        img = comp.render_screen(doc)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (200, 100))

    def test_render_button(self):
        from ui.nstudio import NstudioComponent
        btn = NstudioComponent(
            id="btn1", type="Button",
            layout={"x": 10, "y": 10, "width": 120, "height": 36},
            properties={"text": "Click Me"},
        )
        screen = _make_screen("s", 400, 300, root_children=[btn])
        doc = _make_doc(screens=[screen])
        comp = SDLCompositor(headless=True)
        img = comp.render_screen(doc)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (400, 300))

    def test_render_taskbar(self):
        from ui.nstudio import NstudioComponent
        tb = NstudioComponent(
            id="tb", type="Taskbar",
            layout={"x": 0, "y": 260, "width": 400, "height": 40},
        )
        screen = _make_screen("s", 400, 300, root_children=[tb])
        doc = _make_doc(screens=[screen])
        comp = SDLCompositor(headless=True)
        img = comp.render_screen(doc)
        self.assertIsNotNone(img)
        # Taskbar area should have overlay color
        px = img.getpixel((200, 270))
        self.assertEqual(px, THEMES["Eclipse"]["surface_overlay"])

    def test_render_solar_theme(self):
        screen = _make_screen("s", 100, 100)
        doc = _make_doc(screens=[screen])
        comp = SDLCompositor(theme_name="Solar", headless=True)
        img = comp.render_screen(doc)
        self.assertIsNotNone(img)

    def test_render_scale_2x(self):
        screen = _make_screen("s", 200, 100)
        doc = _make_doc(screens=[screen])
        comp = SDLCompositor(scale=2.0, headless=True)
        img = comp.render_screen(doc)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (400, 200))

    def test_render_nonexistent_screen_raises(self):
        doc = _make_doc(screens=[])
        comp = SDLCompositor(headless=True)
        with self.assertRaises(ValueError):
            comp.render_screen(doc, screen_id="missing")

    def test_render_nested_children(self):
        from ui.nstudio import NstudioComponent
        btn = NstudioComponent(
            id="btn", type="Button",
            layout={"x": 10, "y": 10, "width": 80, "height": 30},
            properties={"text": "OK"},
        )
        container = NstudioComponent(
            id="box", type="Container",
            layout={"x": 0, "y": 0, "width": 200, "height": 100},
            children=[btn],
        )
        screen = _make_screen("s", 400, 300, root_children=[container])
        doc = _make_doc(screens=[screen])
        comp = SDLCompositor(headless=True)
        img = comp.render_screen(doc)
        self.assertIsNotNone(img)


@unittest.skipUnless(HAS_SDL2, "pysdl2 not installed")
class TestSDLCompositorSave(unittest.TestCase):
    """SDLCompositor can save images to files."""

    def test_render_to_file(self):
        screen = _make_screen("s", 200, 100)
        doc = _make_doc(screens=[screen])
        comp = SDLCompositor(headless=True)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            result = comp.render_to_file(doc, path, screen_id="s")
            self.assertTrue(os.path.exists(result))
            self.assertGreater(os.path.getsize(result), 0)
        finally:
            os.unlink(path)


@unittest.skipUnless(HAS_SDL2, "pysdl2 not installed")
class TestSDLCompositorWithFixture(unittest.TestCase):
    """Render real .nstudio fixtures through the SDL compositor."""

    def _find_fixture(self, name):
        base = os.path.dirname(__file__)
        for candidate in [
            os.path.join(base, "fixtures", name),
            os.path.join(base, "fixtures", "nstudio", name),
            os.path.join(base, "..", "tests", "fixtures", name),
            os.path.join(base, "..", "tests", "fixtures", "nstudio", name),
            os.path.join(base, "..", "..", "tests", "fixtures", name),
            os.path.join(base, "..", "..", "tests", "fixtures", "nstudio", name),
        ]:
            if os.path.exists(candidate):
                return candidate
        return None

    def test_render_desktop_fixture(self):
        from ui.nstudio import load as nstudio_load
        path = self._find_fixture("desktop.nstudio")
        if path is None:
            self.skipTest("desktop.nstudio fixture not found")

        doc = nstudio_load(path)
        comp = SDLCompositor(headless=True)

        for screen in doc.screens:
            img = comp.render_screen(doc, screen_id=screen.id)
            self.assertIsNotNone(img)
            self.assertGreater(img.size[0], 0)
            self.assertGreater(img.size[1], 0)

    def test_render_desktop_save(self):
        from ui.nstudio import load as nstudio_load
        path = self._find_fixture("desktop.nstudio")
        if path is None:
            self.skipTest("desktop.nstudio fixture not found")

        doc = nstudio_load(path)
        comp = SDLCompositor(headless=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            for screen in doc.screens:
                out = os.path.join(tmpdir, f"{screen.id}.png")
                comp.render_to_file(doc, out, screen_id=screen.id)
                self.assertTrue(os.path.exists(out))
                self.assertGreater(os.path.getsize(out), 1000)


@unittest.skipUnless(HAS_SDL2, "pysdl2 not installed")
class TestSDLBitmapFont(unittest.TestCase):
    """Bitmap font rendering works for common characters."""

    def test_init_font(self):
        from ui.compositor_sdl import _init_bitmap_font, _SIMPLE_FONT
        _init_bitmap_font()
        self.assertIn('A', _SIMPLE_FONT)
        self.assertIn('0', _SIMPLE_FONT)
        self.assertIn(' ', _SIMPLE_FONT)
        self.assertEqual(len(_SIMPLE_FONT['A']), 5)

    def test_draw_text_on_surface(self):
        from ui.compositor_sdl import _init_bitmap_font
        _init_bitmap_font()
        import sdl2
        sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO)
        try:
            surface = sdl2.SDL_CreateRGBSurfaceWithFormat(
                0, 200, 50, 32, sdl2.SDL_PIXELFORMAT_ARGB8888,
            )
            from ui.compositor_sdl import _draw_text_bitmap
            _draw_text_bitmap(surface, 10, 10, "Hello", (255, 255, 255))
            sdl2.SDL_FreeSurface(surface)
        finally:
            sdl2.SDL_Quit()


if __name__ == "__main__":
    unittest.main()
