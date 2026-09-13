#!/usr/bin/env python3
"""Tests for the in-editor Inspector side panel: the preview+verdict
companion view (``compose_inspector_view``) and its ``nyforge_live``
CLI glue (``--side-panel``), closing the "Inspector side panel" plan
candidate.

Pins:
- geometry: preview left, panel right, honest degradation to the panel
  alone when there is no preview (document did not import),
- content: the WOULD-DROP verdict badge is on the panel side only, and
  an honored document shows no red anywhere in the panel,
- CLI: --side-panel writes the PNG and reports the verdict (both
  polarities), via the same build_side_panel the watch-mode callback
  re-invokes on every accepted reload.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from PIL import Image

from ui.inspector_panel import (
    PANEL_WIDTH,
    compose_inspector_view,
    render_inspector_panel,
)
from ui.nyforge_bridge import NyforgeBridge

STOCK = os.path.join(_BACKEND_DIR, "shell", "defaults", "desktop.nstudio")
PILL = os.path.join(_BACKEND_DIR, "shell", "variants", "pill.nstudio")

_RED = (220, 80, 80, 255)


def _future_doc_text() -> str:
    """A pill document authored against a hypothetical registry 1.2."""
    with open(PILL, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["requiresRegistry"] = ["1.2"]
    return json.dumps(doc)


def _count_red(img: Image.Image, x0: int, x1: int) -> int:
    found = 0
    for x in range(x0, x1, 3):
        for y in range(0, img.size[1], 3):
            if img.getpixel((x, y)) == _RED:
                found += 1
    return found


class TestComposeInspectorView(unittest.TestCase):
    """Geometry + content polarities of the composed view."""

    def setUp(self):
        self.bridge = NyforgeBridge(None)
        self.preview = Image.new("RGBA", (800, 450), (10, 10, 10, 255))

    def _report(self, text: str):
        return self.bridge.inspect_version(text=text)

    def test_full_view_is_preview_plus_panel(self):
        rep = self._report(_future_doc_text())
        panel_h = render_inspector_panel(rep).size[1]
        img = compose_inspector_view(self.preview, rep)
        self.assertEqual(img.size[0], 800 + 24 + PANEL_WIDTH)
        self.assertEqual(img.size[1], max(450, panel_h))

    def test_bad_document_degrades_to_panel_alone(self):
        rep = self._report("{nope")
        self.assertFalse(rep["ok"])
        img = compose_inspector_view(None, rep)
        self.assertEqual(img.size[0], PANEL_WIDTH)
        # The error line is part of the panel content, not the preview.
        self.assertIn("error", " ".join(
            __import__("ui.inspector_panel", fromlist=["report_lines"])
            .report_lines(rep)))

    def test_dropped_view_badge_on_panel_side_only(self):
        rep = self._report(_future_doc_text())
        self.assertTrue(rep["anyDropped"])
        img = compose_inspector_view(self.preview, rep)
        panel_x0 = self.preview.size[0] + 24
        self.assertGreater(
            _count_red(img, panel_x0, img.size[0]), 20,
            "expected the solid red WOULD-DROP badge in the panel")
        self.assertEqual(
            _count_red(img, 0, panel_x0), 0,
            "the preview half must not carry the verdict color")

    def test_honored_view_has_no_red_badge(self):
        with open(STOCK, "r", encoding="utf-8") as fh:
            rep = self._report(fh.read())
        self.assertFalse(rep["anyDropped"])
        img = compose_inspector_view(self.preview, rep)
        self.assertEqual(_count_red(img, 0, img.size[0]), 0)


class TestSidePanelCli(unittest.TestCase):
    """The nyforge_live --side-panel glue (both verdict polarities)."""

    def _run(self, doc_path: str) -> dict:
        out = os.path.join(tempfile.mkdtemp(), "sidepanel.png")
        proc = subprocess.run(
            [sys.executable, os.path.join(_BACKEND_DIR, "examples",
                                          "nyforge_live.py"),
             "--side-panel", out, "--json", doc_path],
            capture_output=True, text=True, cwd=_BACKEND_DIR, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertTrue(os.path.exists(summary["panel"]))
        return summary

    def test_honored_document(self):
        summary = self._run(STOCK)
        self.assertEqual(summary["verdict"], "fully honored by this build")
        self.assertTrue(summary["preview_rendered"])

    def test_future_document_reports_drop(self):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".nstudio", delete=False,
                encoding="utf-8") as fh:
            fh.write(_future_doc_text())
            doc_path = fh.name
        self.addCleanup(os.unlink, doc_path)
        summary = self._run(doc_path)
        self.assertEqual(summary["verdict"], "WOULD DROP features")

    def test_build_side_panel_direct(self):
        """The function the watch-mode callback re-invokes on reload."""
        from examples.nyforge_live import build_side_panel
        out = os.path.join(tempfile.mkdtemp(), "live.png")
        summary = build_side_panel(PILL, out)
        self.assertTrue(summary["preview_rendered"])
        self.assertIn("verdict", summary)
        self.assertTrue(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
