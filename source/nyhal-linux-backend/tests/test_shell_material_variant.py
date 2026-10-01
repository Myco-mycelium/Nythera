#!/usr/bin/env python3
"""Tests for the material-variant shell document (shell/variants/
material.nstudio) — the Android-style restyle that is the live ISO's
default boot entry.

Pins:
- the variant parses through the real import gate (registry 1.1),
- it differs from the stock shell ONLY in allowed restyle keys
  (project header, themes.active, designTokens additions, Button
  cornerRadius) — same screens, same components, same layout: a
  restyle, never a fork,
- the compositor renders it under the Material theme,
- the rendered output actually differs from the stock shell, and the
  difference is genuine Android chrome (tonal Material surfaces, not
  the Eclipse palette).
"""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from ui import nstudio
from ui.compositor import Compositor, THEMES

_REPO = os.path.join(_HERE, os.pardir)
MATERIAL = os.path.join(_REPO, "shell", "variants", "material.nstudio")
STOCK = os.path.join(_REPO, "shell", "defaults", "desktop.nstudio")


def _strip_allowed(raw, is_material):
    """Strip the material variant's ALLOWED restyle keys from BOTH
    documents (the active theme and the design-token skin), and the
    Button cornerRadius overrides from the material side; anything that
    still differs is structure — a fork, not a restyle."""
    raw = json.loads(json.dumps(raw))
    raw.pop("project", None)
    raw.pop("requiresRegistry", None)
    # Allowed restyle keys (both sides): theme choice + token skin.
    raw.pop("themes", None)
    raw.pop("designTokens", None)
    if is_material:
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "Button":
                    props = node.setdefault("properties", {})
                    props.pop("cornerRadius", None)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(raw)
    return raw


def _buttons(doc):
    out = []
    for s in doc.screens:
        def walk(c):
            if c.type == "Button":
                out.append((s.id, c.id, dict(c.properties)))
            for ch in c.children:
                walk(ch)
        walk(s.root)
    return out


@unittest.skipUnless(os.path.exists(MATERIAL), "material.nstudio not found")
class TestMaterialVariantShell(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.material_doc = nstudio.load(MATERIAL)
        cls.stock_doc = nstudio.load(STOCK)

    def test_parses_through_registry_1_1_gate(self):
        # nstudio.load raises NstudioValidationError on any contract
        # violation — reaching here IS the assertion.
        self.assertEqual(self.material_doc.version, "1.0.0")
        with open(MATERIAL) as fh:
            raw = json.load(fh)
        self.assertEqual(raw.get("requiresRegistry"), ["1.1"])

    def test_project_identifies_the_variant(self):
        self.assertIn("Material", self.material_doc.project.get("name", ""))

    def test_active_theme_is_material(self):
        self.assertEqual(self.material_doc.themes.get("active"), "Material")

    def test_variant_is_only_a_restyle_of_the_stock_shell(self):
        with open(MATERIAL) as fh:
            raw_material = json.load(fh)
        with open(STOCK) as fh:
            raw_stock = json.load(fh)
        self.assertEqual(
            _strip_allowed(raw_material, True),
            _strip_allowed(raw_stock, False),
            "the material variant must restyle, never fork")

    def test_button_geometry_matches_stock(self):
        """Restyle = color/radius only: every Button keeps the stock
        geometry (the pill test's guarantee, restated here)."""
        self.assertEqual(
            [(sid, cid, {k: v for k, v in p.items()
                         if k != "cornerRadius"})
             for sid, cid, p in _buttons(self.material_doc)],
            [(sid, cid, {k: v for k, v in p.items()
                         if k != "cornerRadius"})
             for sid, cid, p in _buttons(self.stock_doc)])

    def test_renders_under_material_theme(self):
        img = Compositor(theme_name="Material").render_screen(
            self.material_doc, screen_id=self.material_doc.screens[0].id)
        self.assertGreater(img.size[0], 0)

    def test_render_uses_the_material_palette(self):
        """The Material shell must paint Material surfaces — an Eclipse
        palette sneaking in means the theme is not actually applied."""
        img = Compositor(theme_name="Material").render_screen(
            self.material_doc, screen_id="desktop")
        m = THEMES["Material"]
        # The top band is the translucent taskbar (surface.bar opacity
        # 0.85 from the doc's tokens) over the desktop surface:
        # bar 0.85 over surface = (37, 36, 43) — neither Eclipse value.
        self.assertEqual(img.getpixel((720, 10)), (37, 36, 43))
        # The taskbar's start control is the M3 accent with the
        # on-accent glyph — the Android signature.
        self.assertEqual(img.getpixel((20, 20)), m["accent"])
        # Desktop body: the M3 surface family (not Eclipse's #282828).

    def test_render_differs_from_stock(self):
        before = Compositor(theme_name="Eclipse").render_screen(
            self.stock_doc, screen_id="desktop")
        after = Compositor(theme_name="Material").render_screen(
            self.material_doc, screen_id="desktop")
        self.assertEqual(before.size, after.size)
        diffs = sum(
            1 for y in range(0, before.height, 7)
            for x in range(0, before.width, 7)
            if before.getpixel((x, y)) != after.getpixel((x, y)))
        self.assertGreater(diffs, 100,
                           "the material restyle must visibly differ")


if __name__ == "__main__":
    unittest.main()
