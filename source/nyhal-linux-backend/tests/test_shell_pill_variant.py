#!/usr/bin/env python3
"""Tests for the pill-variant shell document (shell/variants/
desktop-pill.nstudio) — the reference design exercising the registry-1.1
``Button.cornerRadius`` contract end-to-end.

Pins:
- the variant parses through the real import gate (registry 1.1),
- it differs from the stock shell ONLY in Button cornerRadius + project
  metadata (the variant is a restyle, never a fork),
- the compositor renders it on both brand themes,
- the rendered geometry actually changes vs the stock shell, confined
  to Button regions.
"""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from ui import nstudio
from ui.compositor import Compositor

_REPO = os.path.join(_HERE, os.pardir)
PILL = os.path.join(_REPO, "shell", "variants", "desktop-pill.nstudio")
STOCK = os.path.join(_REPO, "shell", "defaults", "desktop.nstudio")


def _buttons(doc):
    """Every (screen_id, component_id, props) Button in document order."""
    out = []

    for s in doc.screens:
        def walk(c):
            if c.type == "Button":
                out.append((s.id, c.id, dict(c.properties)))
            for ch in c.children:
                walk(ch)

        walk(s.root)
    return out


@unittest.skipUnless(os.path.exists(PILL), "desktop-pill.nstudio not found")
class TestPillVariantShell(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pill_doc = nstudio.load(PILL)
        cls.stock_doc = nstudio.load(STOCK)

    def test_parses_through_registry_1_1_gate(self):
        # nstudio.load raises NstudioValidationError on any contract
        # violation — reaching here IS the assertion.
        self.assertEqual(self.pill_doc.version, "1.0.0")

    def test_project_identifies_the_variant(self):
        self.assertIn("Pill", self.pill_doc.project.get("name", ""))
        self.assertNotEqual(self.pill_doc.project.get("id"),
                            self.stock_doc.project.get("id"))

    def test_every_button_overrides_cornerRadius(self):
        buttons = _buttons(self.pill_doc)
        self.assertTrue(buttons, "the pill variant must contain Buttons")
        for screen_id, comp_id, props in buttons:
            self.assertEqual(props.get("cornerRadius"), 64,
                             f"{screen_id}/{comp_id} must be a pill")

    def test_variant_is_only_a_restyle_of_the_stock_shell(self):
        """Same screens/components/layout; only Button cornerRadius and
        the project header may differ. A variant that forks structure
        is a maintenance trap — this pins it against drift."""
        with open(PILL) as fh:
            raw_pill = json.load(fh)
        with open(STOCK) as fh:
            raw_stock = json.load(fh)

        def strip_allowed(raw, is_pill):
            raw = json.loads(json.dumps(raw))
            raw.pop("project", None)

            def walk(node):
                if isinstance(node, dict):
                    if node.get("type") == "Button":
                        props = node.setdefault("properties", {})
                        if is_pill:
                            props.pop("cornerRadius", None)
                    for v in node.values():
                        walk(v)
                elif isinstance(node, list):
                    for v in node:
                        walk(v)

            walk(raw)
            return raw

        self.assertEqual(strip_allowed(raw_pill, True),
                         strip_allowed(raw_stock, False),
                         "the pill variant must restyle, never fork")

    def test_renders_on_both_brand_themes(self):
        for theme in ("Eclipse", "Solar"):
            img = Compositor(theme_name=theme).render_screen(
                self.pill_doc, screen_id=self.pill_doc.screens[0].id)
            self.assertGreater(img.size[0], 0)

    def test_render_geometry_differs_from_stock_on_buttons_only(self):
        before = Compositor(theme_name="Eclipse").render_screen(
            self.stock_doc, screen_id="desktop")
        after = Compositor(theme_name="Eclipse").render_screen(
            self.pill_doc, screen_id="desktop")
        self.assertEqual(before.size, after.size)

        diffs = [(x, y) for y in range(before.height)
                 for x in range(before.width)
                 if before.getpixel((x, y)) != after.getpixel((x, y))]
        self.assertTrue(diffs, "pill corners must change the rendering")

        # The delta must live inside Button bounding boxes (with a
        # tolerance for the stock renderer's shadow offset).
        boxes = []
        for screen_id, comp_id, _ in _buttons(self.stock_doc):
            if screen_id != "desktop":
                continue
            for s in self.stock_doc.screens:
                if s.id != "desktop":
                    continue
                stack = [s.root]
                while stack:
                    c = stack.pop()
                    if c.id == comp_id:
                        L = c.layout
                        boxes.append((L["x"], L["y"],
                                      L["x"] + L["width"],
                                      L["y"] + L["height"]))
                    stack.extend(c.children)
        self.assertTrue(boxes, "desktop screen must contain the buttons")
        tol = 6  # shadow/chrome slop around each box
        for x, y in diffs:
            self.assertTrue(
                any(bx0 - tol <= x <= bx1 + tol and
                    by0 - tol <= y <= by1 + tol
                    for bx0, by0, bx1, by1 in boxes),
                f"delta at ({x},{y}) is outside every Button box")


if __name__ == "__main__":
    unittest.main()
