#!/usr/bin/env python3
"""Tests for per-screen variant mixing (``ui.variant_mix``) — the last
design-language candidate: a pill dock on a stock desktop, or the
reverse, composed from the shipped trees.

Pins:
- screens are the unit: untouched screens are structurally identical to
  the base's; taken screens come wholly from the overlay,
- the header contract describes the RESULT: union over the base plus
  only sources whose screens were actually taken (a zero-screen mix
  never inherits the overlay's ``requiresRegistry``),
- the mixed document passes the real import gate and — when the Rust
  crate is available — gets the SAME inspect verdict from both engines
  (differential continuity for this new document class),
- refusals: unknown screen, base missing the screen, schema mismatch.
"""

import json
import os
import sys
import tempfile
import unittest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from ui.variant_mix import mix_screens
from ui.nyforge_bridge import NyforgeBridge

STOCK = os.path.join(_BACKEND_DIR, "shell", "defaults", "desktop.nstudio")
PILL = os.path.join(_BACKEND_DIR, "shell", "variants", "pill.nstudio")


def _find(node, cid):
    if node.get("id") == cid:
        return node
    for child in node.get("children") or []:
        found = _find(child, cid)
        if found is not None:
            return found
    return None


def _screen(doc, sid):
    for s in doc.get("screens", []):
        if s.get("id") == sid:
            return s
    raise AssertionError("no screen %r" % sid)


def _radius(doc, screen_id, comp_id):
    root = _screen(doc, screen_id)["root"]
    comp = _find(root, comp_id)
    if comp is None:
        return None
    return (comp.get("properties") or {}).get("cornerRadius")


class TestMixScreens(unittest.TestCase):
    def setUp(self):
        with open(STOCK, "r", encoding="utf-8") as fh:
            self.stock = json.load(fh)
        with open(PILL, "r", encoding="utf-8") as fh:
            self.pill = json.load(fh)
        self.bridge = NyforgeBridge(None)

    def test_full_overlay_is_pill_everywhere(self):
        mixed = mix_screens(self.stock, self.pill)
        self.assertEqual(_radius(mixed, "desktop", "btn_eclipse"), 64)
        self.assertEqual(_radius(mixed, "lock", "btn_unlock"), 64)
        self.assertEqual(mixed.get("requiresRegistry"), ["1.1"])

    def test_pill_dock_on_stock_desktop(self):
        """The canonical mix: stock desktop screen, pill lock screen.

        (The dock itself is shared structure; the pill look on the
        desktop screen is carried by its buttons.)
        """
        mixed = mix_screens(self.stock, self.pill, screens=["lock"])
        # Taken screen is pill's:
        self.assertEqual(_radius(mixed, "lock", "btn_unlock"), 64)
        # Untouched screen is structurally the stock one — no pill
        # cornerRadius leaks across:
        self.assertIsNone(_radius(mixed, "desktop", "btn_eclipse"))
        self.assertIsNone(_radius(mixed, "desktop", "btn_solar"))
        self.assertEqual(mixed["screens"][[s["id"] for s in
                                           mixed["screens"]].index("desktop")],
                         self.stock["screens"][[s["id"] for s in
                                                self.stock["screens"]].index("desktop")])

    def test_reverse_mix_keeps_pill_buttons_loses_lock_radius(self):
        mixed = mix_screens(self.pill, self.stock, screens=["lock"])
        self.assertEqual(_radius(mixed, "desktop", "btn_eclipse"), 64)
        self.assertEqual(_radius(mixed, "desktop", "btn_solar"), 64)
        self.assertIsNone(_radius(mixed, "lock", "btn_unlock"))
        # The base declared 1.1 and its screens are still in the result:
        self.assertEqual(mixed.get("requiresRegistry"), ["1.1"])

    def test_zero_overlay_screens_inherit_nothing(self):
        """The honest-header pin: not taking any overlay screen must
        not make the result claim the overlay's requirements."""
        mixed = mix_screens(self.stock, self.pill, screens=[])
        self.assertNotIn("requiresRegistry", mixed)

    def test_mixed_document_passes_gate_and_engines_agree(self):
        import ui.nstudio as nstudio
        mixed = mix_screens(self.stock, self.pill, screens=["lock"])
        text = json.dumps(mixed)
        gate = nstudio.loads(text)  # the real import gate
        self.assertEqual(gate.screens[0].id, "desktop")
        report = self.bridge.inspect_version(text=text)
        self.assertTrue(report["ok"])
        self.assertFalse(report["anyDropped"])
        try:
            from ui import nstudio_codec
            if not nstudio_codec.available():
                self.skipTest("Rust nyui crate not available")
            rs = nstudio_codec.inspect_version_rust(text)
        except ImportError:
            self.skipTest("nstudio_codec import failed")
        for key in ("docHeaderVersions", "notYetInRegistry",
                    "unknownDocRequirements", "anyDropped",
                    "schemaSupported"):
            self.assertEqual(report[key], rs[key], key)

    def test_future_overlay_mix_is_honest(self):
        """A mix whose overlay requires a future registry says so —
        and only when the overlay screens were actually taken."""
        future = json.loads(json.dumps(self.pill))
        future["requiresRegistry"] = ["1.2"]
        taken = mix_screens(self.stock, future, screens=["lock"])
        self.assertEqual(taken.get("requiresRegistry"), ["1.2"])
        self.assertTrue(
            self.bridge.inspect_version(text=json.dumps(taken))["anyDropped"])
        untouched = mix_screens(self.stock, future, screens=[])
        self.assertNotIn("requiresRegistry", untouched)

    def test_refusals(self):
        with self.assertRaises(ValueError):
            mix_screens(self.stock, self.pill, screens=["nope"])
        with self.assertRaises(ValueError):
            mix_screens(self.stock, {"version": "1.0.0", "screens": [
                {"id": "other", "root": {}}]})
        with self.assertRaises(ValueError):
            mix_screens(self.stock, {**self.pill, "version": "9.9.9"})


class TestMixCli(unittest.TestCase):
    def test_cli_writes_and_reports(self):
        import subprocess
        out = os.path.join(tempfile.mkdtemp(), "mixed.nstudio")
        proc = subprocess.run(
            [sys.executable, "-m", "ui.variant_mix", STOCK, PILL,
             "--screens", "lock", "--out", out],
            capture_output=True, text=True, cwd=_BACKEND_DIR, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.exists(out))
        with open(out, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc.get("requiresRegistry"), ["1.1"])
        self.assertIn("fully honored", proc.stdout)


if __name__ == "__main__":
    unittest.main()
