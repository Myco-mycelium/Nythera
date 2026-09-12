#!/usr/bin/env python3
"""Tests for the PIL-based NUI compositor (ui/compositor.py)."""

import json
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
    loads as nstudio_loads,
    NstudioValidationError,
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


class TestCompositorDesignTokens(unittest.TestCase):
    """Design-language tokens (docs/reference/design-language.md):
    opt-in via a document's designTokens section; documents without
    tokens render exactly as before (pixel-compatible default)."""

    def _taskbar_doc(self, tokens=None):
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
        doc.design_tokens = tokens or {}
        return doc

    def test_alpha_over_math(self):
        from ui.compositor import _alpha_over
        # 50% chrome over white wallpaper: channels land midway.
        r = _alpha_over((255, 255, 255, 255), (40, 40, 40), 0.5)
        self.assertEqual(r, (148, 148, 148))
        # alpha 0 keeps the base, alpha 1 takes the top.
        self.assertEqual(
            _alpha_over((10, 20, 30, 255), (200, 200, 200), 0.0),
            (10, 20, 30))
        self.assertEqual(
            _alpha_over((10, 20, 30, 255), (200, 200, 200), 1.0),
            (200, 200, 200))

    def test_taskbar_translucency_is_opt_in(self):
        bar = THEMES["Eclipse"]["surface_overlay"]
        bg = THEMES["Eclipse"]["background"]
        # Without tokens: opaque (the pre-tokens pixel).
        img = Compositor().render_screen(self._taskbar_doc())
        self.assertEqual(img.getpixel((720, 580)), bar)
        # With bar opacity 0.5: a real blend of wallpaper under chrome.
        doc = self._taskbar_doc({"surface": {"bar": {"opacity": 0.5}}})
        img = Compositor().render_screen(doc)
        px = img.getpixel((720, 580))
        self.assertNotEqual(px, bar)
        for got, expected in zip(px, _expected_blend(bg, bar, 0.5)):
            self.assertAlmostEqual(got, expected, delta=2)

    def test_tokens_do_not_leak_between_documents(self):
        doc_tok = self._taskbar_doc({"surface": {"bar": {"opacity": 0.5}}})
        doc_plain = self._taskbar_doc()
        comp = Compositor()
        comp.render_screen(doc_tok)
        img = comp.render_screen(doc_plain)  # same compositor instance
        self.assertEqual(img.getpixel((720, 580)),
                         THEMES["Eclipse"]["surface_overlay"])

    def test_radius_tokens_apply_to_buttons(self):
        btn = NstudioComponent(
            id="btn", type="Button",
            layout={"x": 10, "y": 10, "width": 120, "height": 36},
            properties={"text": "OK"},
        )
        screen = _make_screen("s", 400, 300, root_children=[btn])
        doc = _make_doc(screens=[screen])
        # The screen root is a Window: its fill (surface_overlay) is what
        # shows through outside the button's rounded corner.
        under = THEMES["Eclipse"]["surface_overlay"]
        # Default radius.sm=8: the (1,1)-offset corner pixel is outside
        # the rounded corner → the Window fill beneath.
        img = Compositor().render_screen(doc)
        self.assertEqual(img.getpixel((11, 11)), under)
        # radius.sm=2: the same pixel is inside → button fill.
        doc.design_tokens = {"radius": {"sm": 2}}
        img = Compositor().render_screen(doc)
        self.assertEqual(img.getpixel((11, 11)),
                         THEMES["Eclipse"]["button_bg"])

    def test_loader_parses_design_tokens(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "shell", "defaults",
            "default-shell.nstudio")
        if not os.path.exists(path):
            self.skipTest("default-shell.nstudio not found")
        doc = nstudio_load(path)
        self.assertEqual(doc.design_tokens["motion"]["enter"]["duration"], 200)
        self.assertEqual(doc.design_tokens["radius"]["lg"], 16)
        # And the compositor picks them up (surface.bar opt-in recorded).
        comp = Compositor()
        comp.render_screen(doc)
        self.assertEqual(
            comp.tokens["surface"]["bar"]["opacity"], 0.85)

    def _list_doc(self, tokens=None, props=None):
        """A Window + List of three items, selectable via tokens/props."""
        lst = NstudioComponent(
            id="lst", type="List",
            layout={"x": 10, "y": 10, "width": 200, "height": 72},
            properties=props if props is not None else {"items": ["a", "b", "c"]},
        )
        screen = _make_screen("s", 400, 300, root_children=[lst])
        doc = _make_doc(screens=[screen])
        doc.design_tokens = tokens or {}
        return doc

    def _region_has(self, img, box, color):
        """True if any pixel in ``(x0, y0, x1, y1)`` equals ``color``.

        Region scans rather than single-pixel probes: glyph coverage is
        sparse (a one-character item is ~10 anti-aliased pixels), so a
        point sample can land on a gap.
        """
        x0, y0, x1, y1 = box
        return any(img.getpixel((x, y)) == color
                   for y in range(y0, y1) for x in range(x0, x1))

    def test_list_default_row_pitch_is_pixel_identical(self):
        # Token-less: rows at the historical 24 px pitch, no selection.
        img = Compositor().render_screen(self._list_doc())
        tp = THEMES["Eclipse"]["text_primary"]
        for row_y in (10, 34, 58):   # y + i * 24
            self.assertTrue(
                self._region_has(img, (12, row_y + 3, 60, row_y + 13), tp),
                f"no row text near y={row_y}")

    def test_list_row_pitch_follows_space_token(self):
        # space.xl=12: rows land at y = 10, 22, 34 — not 10, 34, 58.
        doc = self._list_doc(tokens={"space": {"xl": 12}})
        img = Compositor().render_screen(doc)
        tp = THEMES["Eclipse"]["text_primary"]
        for row_y in (10, 22, 34):
            self.assertTrue(
                self._region_has(img, (12, row_y + 3, 60, row_y + 13), tp),
                f"no row text near y={row_y}")
        # The historical third row (y=58) no longer carries text.
        self.assertFalse(
            self._region_has(img, (12, 58 + 3, 60, 58 + 13), tp))

    def test_list_selection_highlight_uses_accent(self):
        doc = self._list_doc(props={"items": ["a", "b", "c"],
                                    "selectedIndex": 1})
        img = Compositor().render_screen(doc)
        acc = THEMES["Eclipse"]["accent"]
        # Row 1 spans y = 34..57 at the 24-px pitch: solid accent fill.
        self.assertTrue(
            self._region_has(img, (12, 36, 100, 56), acc))
        self.assertTrue(
            self._region_has(img, (150, 36, 200, 56), acc))
        # Rows 0/2 keep plain (non-accent) text.
        tp = THEMES["Eclipse"]["text_primary"]
        self.assertTrue(
            self._region_has(img, (12, 13, 60, 23), tp))
        self.assertTrue(
            self._region_has(img, (12, 61, 60, 71), tp))

    def test_list_selection_contrast_text(self):
        # Selected row text draws in surface_elevated (contrast on accent).
        img = Compositor().render_screen(self._list_doc(
            props={"items": ["a", "b", "c"], "selectedIndex": 0}))
        self.assertTrue(self._region_has(
            img, (12, 13, 60, 23), THEMES["Eclipse"]["surface_elevated"]))
        # Without a selection, the same row keeps text_primary — and no
        # accent bar is drawn anywhere in the list area.
        img2 = Compositor().render_screen(self._list_doc())
        self.assertTrue(self._region_has(
            img2, (12, 13, 60, 23), THEMES["Eclipse"]["text_primary"]))
        self.assertFalse(self._region_has(
            img2, (11, 11, 205, 80), THEMES["Eclipse"]["accent"]))

    def _bar_doc(self, comp_type, tokens=None, props=None, h=16):
        comp = NstudioComponent(
            id="bar", type=comp_type,
            layout={"x": 10, "y": 10, "width": 200, "height": h},
            properties=props if props is not None else {"value": 75},
        )
        screen = _make_screen("s", 400, 300, root_children=[comp])
        doc = _make_doc(screens=[screen])
        doc.design_tokens = tokens or {}
        return doc

    def test_slider_radius_pixel_compatible(self):
        # Any radius >= half the 5 px track height clamps to the same
        # pill: token-less (r=8 from radius.sm) must equal the
        # historical r=2 render.
        from PIL import ImageChops
        base = Compositor().render_screen(self._bar_doc("Slider", h=20))
        r8 = Compositor().render_screen(
            self._bar_doc("Slider", tokens={"radius": {"sm": 8}}, h=20))
        self.assertIsNone(ImageChops.difference(base, r8).getbbox())
        # A sub-clamp radius IS a real change (squared track ends).
        r0 = Compositor().render_screen(
            self._bar_doc("Slider", tokens={"radius": {"sm": 0}}, h=20))
        self.assertIsNotNone(ImageChops.difference(base, r0).getbbox())

    def test_progress_radius_control_token(self):
        # Default = the historical r=4 corner; radius.control retunes it.
        acc = THEMES["Eclipse"]["progress_fill"]
        base = Compositor().render_screen(self._bar_doc("ProgressBar"))
        # r=4 on a 16 px bar: at y=10 (top row) the fill's left corner
        # leaves x=10..12 empty (corner probe: x=13 first filled).
        self.assertFalse(self._region_has(base, (10, 10, 13, 11), acc))
        # radius.control=0 → square corners: x=10 IS filled at y=10.
        sharp = Compositor().render_screen(
            self._bar_doc("ProgressBar", tokens={"radius": {"control": 0}}))
        self.assertTrue(self._region_has(sharp, (10, 10, 12, 11), acc))


def _expected_blend(base, top, alpha):
    """Mirror of the renderer's alpha-over, for test expectations."""
    return tuple(round(t * (1.0 - alpha) + c * alpha)
                 for t, c in zip(base, top))


class TestButtonCornerRadiusContract(unittest.TestCase):
    """Registry 1.1: ``cornerRadius`` on Button (nui-api-v1.json
    versionHistory 1.1) — a deliberate, additive contract bump.

    Pins the whole chain: the registry metadata itself, the import
    gate's per-type enforcement, and the renderer's override semantics
    (positive override applies, clamped to the inscribed maximum;
    0/absent/junk = the ``radius.sm`` token default, pixel-compatible
    with every pre-1.1 document).
    """

    def _button_doc(self, props, comp_type="Button"):
        btn = NstudioComponent(
            id="btn", type=comp_type,
            layout={"x": 10, "y": 10, "width": 120, "height": 40},
            properties=dict(props),
        )
        screen = _make_screen("s", 400, 300, root_children=[btn])
        return _make_doc(screens=[screen])

    def _button_raw(self, props, comp_type="Button"):
        """The same minimal document as raw JSON-able dict (for the
        import-gate tests, which exercise the loader itself)."""
        return {
            "version": "1.0.0",
            "project": {},
            "themes": {"active": "Eclipse", "overrides": {}},
            "states": {}, "stateScopes": {},
            "locales": {"active": "en", "tables": {"en": {}}},
            "resources": {}, "animations": [], "behaviors": [],
            "bindings": [],
            "screens": [{
                "id": "s",
                "size": {"width": 400, "height": 300},
                "root": {"id": "root", "type": "Window",
                         "layout": {"x": 0, "y": 0,
                                    "width": 400, "height": 300},
                         "properties": {"title": "t"},
                         "children": [{
                             "id": "btn", "type": comp_type,
                             "layout": {"x": 10, "y": 10,
                                        "width": 120, "height": 40},
                             "properties": dict(props), "children": []}]}},
            ],
        }

    def _corner_inside_fill(self, props, px=2, py=2):
        """Is pixel (px,py) — relative to the button's top-left corner —
        the button fill color? Probes empirically pinned against PIL's
        rounded_rectangle: (2,2) is INSIDE for r <= 4, OUTSIDE for
        r >= 8; (1,6) is inside at r=8, outside at r >= 16."""
        comp = Compositor()
        img = comp.render_screen(self._button_doc(props))
        return img.getpixel((10 + px, 10 + py)) == comp.theme["button_bg"]

    def test_registry_is_1_1_and_declares_cornerRadius(self):
        import json
        reg = json.load(open(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            os.pardir, "ui", "contracts", "nui-api-v1.json")))
        self.assertEqual(reg["registryVersion"], "1.1")
        self.assertTrue(any(
            h.get("registryVersion") == "1.1" and not h.get("breaking")
            for h in reg.get("versionHistory", [])),
            "registry 1.1 must carry a non-breaking versionHistory entry")
        btn = next(c for c in reg["components"] if c["type"] == "Button")
        cr = next(p for p in btn["properties"] if p["name"] == "cornerRadius")
        self.assertEqual(cr["type"], "number")
        self.assertEqual(cr["default"], 0)
        self.assertEqual(cr["min"], 0)
        self.assertEqual(cr["max"], 64)
        self.assertEqual(cr["units"], "px")

    def test_fixture_registry_copy_matches_canonical(self):
        # The fixture copy must never drift again (it silently rotted to
        # 0.4.0 while the canonical registry moved to 1.0.0).
        with open(os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                os.pardir, "ui", "contracts", "nui-api-v1.json")) as fh:
            canonical = fh.read()
        with open(os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "fixtures", "nstudio", "nui-api-v1.json")) as fh:
            fixture = fh.read()
        self.assertEqual(canonical, fixture)

    # ---- import gate -------------------------------------------------------

    def test_gate_accepts_cornerRadius_on_button(self):
        doc = nstudio_loads(json.dumps(self._button_raw(
            {"text": "Hi", "cornerRadius": 12})))
        btn = next(c for c in doc.screens[0].root.children
                   if c.id == "btn")
        self.assertEqual(btn.properties["cornerRadius"], 12)

    def test_gate_still_rejects_unknown_properties(self):
        with self.assertRaises(NstudioValidationError):
            nstudio_loads(json.dumps(self._button_raw(
                {"text": "Hi", "frobnicate": 1})))

    def test_gate_rejects_cornerRadius_on_non_button(self):
        with self.assertRaises(NstudioValidationError):
            nstudio_loads(json.dumps(self._button_raw(
                {"text": "x", "cornerRadius": 12}, comp_type="Text")))

    # ---- renderer ----------------------------------------------------------

    def test_zero_or_absent_override_is_token_default(self):
        # 0 means "use radius.sm" — pixel-identical to no property at all.
        self.assertEqual(
            self._corner_inside_fill({"text": "B"}),
            self._corner_inside_fill({"text": "B", "cornerRadius": 0}))

    def test_default_radius_rounds_the_corner(self):
        # radius.sm = 8: (2,2) lies outside the fill (empirically pinned).
        self.assertFalse(self._corner_inside_fill({"text": "B"}))

    def test_squarer_override_keeps_corner_inside(self):
        # cornerRadius=4 (less round than the 8 default): (2,2) inside.
        self.assertTrue(self._corner_inside_fill(
            {"text": "B", "cornerRadius": 4}))

    def test_override_geometry_differs_from_default(self):
        # (2,2): outside under r=8 (default), inside under r=4.
        self.assertNotEqual(
            self._corner_inside_fill({"text": "B"}),
            self._corner_inside_fill({"text": "B", "cornerRadius": 4}))

    def test_roundier_override_pushes_corner_out(self):
        # (1,6): inside at r=8 (default), outside at r>=16 — the override
        # makes the button rounder than any default the tokens give.
        self.assertTrue(self._corner_inside_fill({"text": "B"}, px=1, py=6))
        self.assertFalse(self._corner_inside_fill(
            {"text": "B", "cornerRadius": 16}, px=1, py=6))

    def test_junk_override_renders_default(self):
        self.assertEqual(
            self._corner_inside_fill({"text": "B"}),
            self._corner_inside_fill({"text": "B", "cornerRadius": "round"}))

    def test_override_clamps_to_half_min_side(self):
        # 120x40 button: inscribed max is 20; 64 clamps to 20 (PIL would
        # otherwise draw an invalid shape). (1,6): outside under r=20,
        # inside under the default — different geometry, no crash.
        self.assertNotEqual(
            self._corner_inside_fill({"text": "B"}, px=1, py=6),
            self._corner_inside_fill({"text": "B", "cornerRadius": 64},
                                     px=1, py=6))


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
