#!/usr/bin/env python3
"""Tests for the Inspector panel (ui/inspector_panel.py) — the visual
half of the Nyforge preflight.

Pins:
- ``report_lines`` renders the full verdict content (honored + dropped
  cases) from an ``inspect_version`` report,
- the composed NUI document passes the REAL import gate
  (``ui.nstudio.loads``) and carries exactly the panel lines,
- the compositor renders the panel on both brand themes,
- the plain RGBA render matches the document's geometry and theme
  palette (content parity between the two paths),
- the CLI writes its PNG next to the input document.

All string building uses %-formatting — 3.11-safe by construction
(the shipped tree compiles under the image's python3).
"""

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from ui import nstudio
from ui.compositor import Compositor, THEMES
from ui.inspector_panel import (
    PADDING, ROW_HEIGHT,
    build_inspector_document,
    main as panel_main,
    render_inspector_panel,
    report_lines,
)
from ui.nyforge_bridge import NyforgeBridge

_REPO = os.path.join(_HERE, os.pardir)
PILL = os.path.join(_REPO, "shell", "variants", "pill.nstudio")
STOCK = os.path.join(_REPO, "shell", "defaults", "desktop.nstudio")

_VERDICT_OK = "Verdict: fully honored by this build"
_VERDICT_BAD = "Verdict: WOULD DROP features"


def _bridge():
    return NyforgeBridge(None)


def _future_doc_text(registry_version="1.2"):
    with open(STOCK, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    raw["requiresRegistry"] = [registry_version]
    fd, path = tempfile.mkstemp(suffix=".nstudio")
    with os.fdopen(fd, "w") as fh:
        json.dump(raw, fh)
    return path


class TestReportLines(unittest.TestCase):
    """The single content definition for both render paths."""

    def test_honored_document_lines(self):
        report = _bridge().inspect_version(path=PILL)
        lines = report_lines(report)
        self.assertEqual(lines[0], "Inspector preflight")
        self.assertTrue(any("schema 1.0.0 (supported: yes)" in ln
                            for ln in lines))
        self.assertTrue(any("requires: 1.1" in ln for ln in lines))
        self.assertTrue(any(ln.startswith("1.0: Baseline") for ln in lines))
        self.assertTrue(any(ln.startswith("1.1: Added optional Button")
                            for ln in lines))
        self.assertEqual(lines[-1], _VERDICT_OK)

    def test_dropped_document_lines_name_the_loss(self):
        path = _future_doc_text()
        try:
            report = _bridge().inspect_version(path=path)
        finally:
            os.unlink(path)
        lines = report_lines(report)
        self.assertTrue(any(
            "Newer than this build (will be dropped):" in ln
            for ln in lines))
        self.assertTrue(any("1.2" in ln for ln in lines))
        self.assertEqual(lines[-1], _VERDICT_BAD)

    def test_error_report_is_honest(self):
        lines = report_lines({"ok": False, "error": "boom"})
        self.assertEqual(lines, ["Inspector preflight", "",
                                 "error: boom"])


class TestInspectorDocument(unittest.TestCase):
    """The compositor path: a gate-valid document, not a picture."""

    def test_document_passes_the_real_gate(self):
        report = _bridge().inspect_version(path=PILL)
        doc = nstudio.loads(json.dumps(build_inspector_document(report)))
        self.assertEqual(doc.screens[0].id, "inspector")

    def test_document_carries_exactly_the_panel_lines(self):
        report = _bridge().inspect_version(path=PILL)
        lines = report_lines(report)
        doc = nstudio.loads(json.dumps(build_inspector_document(report)))
        children = doc.screens[0].root.children
        self.assertEqual(len(children), len(lines))
        for child, line in zip(children, lines):
            self.assertEqual(child.type, "Text")
            self.assertEqual(child.properties.get("text"),
                             line if line else " ")

    def test_document_renders_on_both_brand_themes(self):
        report = _bridge().inspect_version(path=PILL)
        doc = nstudio.loads(json.dumps(build_inspector_document(report)))
        for theme in ("Eclipse", "Solar"):
            img = Compositor(theme_name=theme).render_screen(
                doc, screen_id=doc.screens[0].id)
            self.assertGreater(img.size[0], 0)

    def test_document_geometry_matches_line_count(self):
        report = _bridge().inspect_version(path=PILL)
        lines = report_lines(report)
        doc = nstudio.loads(json.dumps(build_inspector_document(report)))
        size = doc.screens[0].size
        self.assertEqual(size["height"], PADDING * 2 + len(lines) * ROW_HEIGHT)


class TestPlainRender(unittest.TestCase):
    """The plain path: same content, same palette, no document."""

    def test_render_matches_document_geometry(self):
        report = _bridge().inspect_version(path=PILL)
        lines = report_lines(report)
        img = render_inspector_panel(report)
        self.assertEqual(img.mode, "RGBA")
        self.assertEqual(img.size, (560, PADDING * 2 + len(lines) * ROW_HEIGHT))

    def test_render_uses_the_theme_palette(self):
        report = _bridge().inspect_version(path=PILL)
        for theme in ("Eclipse", "Solar"):
            img = render_inspector_panel(report, theme=theme)
            self.assertEqual(img.getpixel((2, 2)),
                             THEMES[theme]["background"] + (255,))

    def test_dropped_verdict_shows_red_badge(self):
        path = _future_doc_text()
        try:
            report = _bridge().inspect_version(path=path)
        finally:
            os.unlink(path)
        img = render_inspector_panel(report)
        # The badge rectangle is solid fill — exact pixel match at its
        # left edge, one row above the verdict line.
        verdict_row = PADDING + (len(report_lines(report)) - 1) * ROW_HEIGHT
        probe_y = verdict_row - 2
        self.assertEqual(img.getpixel((PADDING + 2, probe_y)),
                         (220, 80, 80, 255))

    def test_honored_verdict_shows_accent_badge(self):
        report = _bridge().inspect_version(path=PILL)
        img = render_inspector_panel(report)
        verdict_row = PADDING + (len(report_lines(report)) - 1) * ROW_HEIGHT
        probe_y = verdict_row - 2
        self.assertEqual(img.getpixel((PADDING + 2, probe_y)),
                         THEMES["Eclipse"]["accent"] + (255,))


class TestPanelCLI(unittest.TestCase):
    """The command-line entry point."""

    def test_cli_validates_and_writes_png(self):
        fd, path = tempfile.mkstemp(suffix=".nstudio")
        os.close(fd)
        try:
            with open(PILL, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
            png = path.rsplit(".", 1)[0] + "-inspector.png"
            rc = panel_main([path])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(png))
            self.assertGreater(os.path.getsize(png), 0)
            os.unlink(png)
        finally:
            os.unlink(path)

    def test_cli_without_arguments_is_a_usage_error(self):
        self.assertEqual(panel_main([]), 2)


if __name__ == "__main__":
    unittest.main()
