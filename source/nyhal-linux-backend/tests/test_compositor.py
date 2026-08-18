#!/usr/bin/env python3
"""Tests for the PIL-based NUI compositor (ui/compositor.py)."""

import os
import sys
import tempfile
import unittest

# Ensure the backend is on the path
sys.path.insert(0, os.path.dirname(__file__))

from ui.compositor import Compositor, THEMES
from ui.nstudio import (
    NstudioDocument,
    NstudioScreen,
    NstudioComponent,
    load as nstudio_load,
)


def _make_doc(screens=None, states=None):
    """Helper to create a minimal NstudioDocument."""
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
    """Helper to create a NstudioScreen with a Window root."""
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


class TestCompositorThemes(unittest.TestCase):
    """Theme definitions are complete and consistent."""

    def test_eclipse_theme_has_all_keys(self):
        expected = {
            "background", "surface", "surface_elevated", "surface_overlay",
            "border", "text_primary", "text_secondary", "accent", "accent_hover",
            "button_bg", "button_text", "input_bg", "input_border",
            "toggle_on", "toggle_off", "slider_track", "slider_fill",
            "progress_bg", "progress_fill",
        }
        self.assertEqual(set(THEMES["Eclipse"].keys()), expected)

    def test_solar_theme_has_all_keys(self):
        expected = {
            "background", "surface", "surface_elevated", "surface_overlay",
            "border", "text_primary", "text_secondary", "accent", "accent_hover",
            "button_bg", "button_text", "input_bg", "input_border",
            "toggle_on", "toggle_off", "slider_track", "slider_fill",
            "progress_bg", "progress_fill",
        }
        self.assertEqual(set(THEMES["Solar"].keys()), expected)

    def test_theme_colors_are_rgb_tuples(self):
        for name, theme in THEMES.items():
            for key, val in theme.items():
                self.assertIsInstance(val, tuple, f"{name}.{key} should be tuple")
                self.assertEqual(len(val), 3, f"{name}.{key} should be 3-tuple")
                for c in val:
                    self.assertIsInstance(c, int)
                    self.assertGreaterEqual(c, 0)
                    self.assertLessEqual(c, 255)

    def test_compositor_default_theme(self):
        comp = Compositor()
        self.assertEqual(comp.theme_name, "Eclipse")
        self.assertIs(comp.theme, THEMES["Eclipse"])

    def test_compositor_solar_theme(self):
        comp = Compositor(theme_name="Solar")
        self.assertEqual(comp.theme_name, "Solar")
        self.assertIs(comp.theme, THEMES["Solar"])

    def test_compositor_unknown_theme_falls_back(self):
        comp = Compositor(theme_name="Nonexistent")
        self.assertIs(comp.theme, THEMES["Eclipse"])


class TestCompositorRender(unittest.TestCase):
    """Compositor renders screens to PIL images."""

    def test_render_empty_document(self):
        screen = _make_screen("test", 800, 600)
        doc = _make_doc(screens=[screen])

        comp = Compositor(theme_name="Eclipse", scale=1.0)
        img = comp.render_screen(doc)
        self.assertEqual(img.size, (800, 600))
        self.assertEqual(img.mode, "RGB")

    def test_render_solar_theme(self):
        screen = _make_screen("s", 100, 100)
        doc = _make_doc(screens=[screen])

        comp = Compositor(theme_name="Solar", scale=1.0)
        img = comp.render_screen(doc)
        # Solar background is (253, 246, 227)
        px = img.getpixel((50, 50))
        self.assertEqual(px, (253, 246, 227))

    def test_render_scale_2x(self):
        screen = _make_screen("s", 200, 100)
        doc = _make_doc(screens=[screen])

        comp = Compositor(scale=2.0)
        img = comp.render_screen(doc)
        self.assertEqual(img.size, (400, 200))

    def test_render_nonexistent_screen_raises(self):
        doc = _make_doc(screens=[])
        comp = Compositor()
        with self.assertRaises(ValueError):
            comp.render_screen(doc, screen_id="missing")

    def test_render_button_component(self):
        btn = NstudioComponent(
            id="btn1", type="Button",
            layout={"x": 10, "y": 10, "width": 120, "height": 36},
            properties={"text": "Click Me"},
        )
        screen = _make_screen("s", 400, 300, root_children=[btn])
        doc = _make_doc(screens=[screen])

        comp = Compositor()
        img = comp.render_screen(doc)
        self.assertEqual(img.size, (400, 300))
        # Button area should not be pure background
        px = img.getpixel((70, 28))
        self.assertNotEqual(px, THEMES["Eclipse"]["background"])

    def test_render_text_component(self):
        txt = NstudioComponent(
            id="t1", type="Text",
            layout={"x": 20, "y": 20, "width": 200, "height": 30},
            properties={"text": "Hello World"},
        )
        screen = _make_screen("s", 400, 300, root_children=[txt])
        doc = _make_doc(screens=[screen])

        comp = Compositor()
        img = comp.render_screen(doc)
        self.assertEqual(img.size, (400, 300))

    def test_render_taskbar(self):
        taskbar = NstudioComponent(
            id="tb", type="Taskbar",
            layout={"x": 0, "y": 560, "width": 1440, "height": 80},
        )
        screen = NstudioScreen(
            id="s",
            size={"width": 1440, "height": 640},
            root=NstudioComponent(
                id="root", type="Window",
                layout={"x": 0, "y": 0, "width": 1440, "height": 640},
                children=[taskbar],
            ),
        )
        doc = _make_doc(screens=[screen])

        comp = Compositor()
        img = comp.render_screen(doc)
        self.assertEqual(img.size, (1440, 640))
        # Taskbar area should have overlay color
        px = img.getpixel((720, 580))
        self.assertEqual(px, THEMES["Eclipse"]["surface_overlay"])

    def test_render_toggle_on(self):
        toggle = NstudioComponent(
            id="tog", type="Toggle",
            layout={"x": 10, "y": 10, "width": 100, "height": 30},
            properties={"value": True, "label": "Dark Mode"},
        )
        screen = _make_screen("s", 400, 300, root_children=[toggle])
        doc = _make_doc(screens=[screen])

        comp = Compositor()
        img = comp.render_screen(doc)
        # Toggle on should show accent color in the toggle area
        px = img.getpixel((20, 20))
        self.assertEqual(px, THEMES["Eclipse"]["toggle_on"])

    def test_render_slider(self):
        slider = NstudioComponent(
            id="sl", type="Slider",
            layout={"x": 10, "y": 10, "width": 200, "height": 20},
            properties={"value": 75, "min": 0, "max": 100},
        )
        screen = _make_screen("s", 400, 300, root_children=[slider])
        doc = _make_doc(screens=[screen])

        comp = Compositor()
        img = comp.render_screen(doc)
        self.assertEqual(img.size, (400, 300))

    def test_render_progress_bar(self):
        pb = NstudioComponent(
            id="pb", type="ProgressBar",
            layout={"x": 10, "y": 10, "width": 200, "height": 16},
            properties={"value": 60, "min": 0, "max": 100},
        )
        screen = _make_screen("s", 400, 300, root_children=[pb])
        doc = _make_doc(screens=[screen])

        comp = Compositor()
        img = comp.render_screen(doc)
        self.assertEqual(img.size, (400, 300))

    def test_render_nested_children(self):
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

        comp = Compositor()
        img = comp.render_screen(doc)
        self.assertEqual(img.size, (400, 300))

    def test_render_lock_screen(self):
        lock = NstudioComponent(
            id="lock", type="LockScreen",
            layout={"x": 0, "y": 0, "width": 1440, "height": 900},
            properties={"clockTime": "09:41"},
        )
        screen = NstudioScreen(
            id="s",
            size={"width": 1440, "height": 900},
            root=lock,
        )
        doc = _make_doc(screens=[screen])

        comp = Compositor()
        img = comp.render_screen(doc)
        self.assertEqual(img.size, (1440, 900))
        # Lock screen background is dark blue
        px = img.getpixel((720, 450))
        self.assertEqual(px, (20, 20, 40))


class TestCompositorSave(unittest.TestCase):
    """Compositor can save images to files."""

    def test_save_png(self):
        screen = _make_screen("s", 200, 100)
        doc = _make_doc(screens=[screen])

        comp = Compositor()
        img = comp.render_screen(doc)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            img.save(path)
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 0)
        finally:
            os.unlink(path)


class TestCompositorWithFixture(unittest.TestCase):
    """Render real .nstudio fixtures through the compositor."""

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
        path = self._find_fixture("desktop.nstudio")
        if path is None:
            self.skipTest("desktop.nstudio fixture not found")

        doc = nstudio_load(path)
        comp = Compositor(theme_name="Eclipse", scale=1.0)

        for screen in doc.screens:
            img = comp.render_screen(doc, screen_id=screen.id)
            self.assertIsNotNone(img)
            self.assertEqual(img.mode, "RGB")
            self.assertGreater(img.size[0], 0)
            self.assertGreater(img.size[1], 0)

    def test_render_desktop_both_themes(self):
        path = self._find_fixture("desktop.nstudio")
        if path is None:
            self.skipTest("desktop.nstudio fixture not found")

        doc = nstudio_load(path)

        for theme_name in ("Eclipse", "Solar"):
            comp = Compositor(theme_name=theme_name, scale=1.0)
            for screen in doc.screens:
                img = comp.render_screen(doc, screen_id=screen.id)
                self.assertIsNotNone(img)

    def test_render_desktop_save(self):
        path = self._find_fixture("desktop.nstudio")
        if path is None:
            self.skipTest("desktop.nstudio fixture not found")

        doc = nstudio_load(path)
        comp = Compositor(theme_name="Eclipse", scale=1.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            for screen in doc.screens:
                img = comp.render_screen(doc, screen_id=screen.id)
                out = os.path.join(tmpdir, f"{screen.id}.png")
                img.save(out)
                self.assertTrue(os.path.exists(out))
                self.assertGreater(os.path.getsize(out), 1000)


if __name__ == "__main__":
    unittest.main()
