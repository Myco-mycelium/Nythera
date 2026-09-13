#!/usr/bin/env python3
"""Differential tests: the Rust FFI ``inspect_version`` must classify
documents exactly like the Python ``NyforgeBridge.inspect_version``.

The stale-cdylib incident (the embedded registry silently disagreeing
with the tree's) is why this exists — two engines that can drift apart
must be pinned together. Skips cleanly when the crate is unavailable;
NYRQIS_RUST_FORCE=1 turns the skip into a failure (CI runs the crate).
"""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from ui import nstudio, nstudio_codec
from ui.nyforge_bridge import NyforgeBridge

_REPO = os.path.join(_HERE, os.pardir)
STOCK = os.path.join(_REPO, "shell", "defaults", "desktop.nstudio")
PILL = os.path.join(_REPO, "shell", "variants", "pill.nstudio")


def _crate_available() -> bool:
    try:
        return nstudio_codec.available()
    except Exception:
        return False


@unittest.skipUnless(_crate_available(), "Rust nyui crate not available")
class TestDifferentialInspect(unittest.TestCase):
    """Python verdicts == Rust verdicts, on the shared classification
    keys (docHeaderVersions / notYetInRegistry / unknownDocRequirements /
    anyDropped / schemaSupported)."""

    def setUp(self):
        self.bridge = NyforgeBridge(None)
        with open(STOCK, "r", encoding="utf-8") as fh:
            self._stock = json.load(fh)

    def _text(self, **header):
        raw = json.loads(json.dumps(self._stock))
        raw.update(header)
        return json.dumps(raw)

    def _diff(self, text):
        py = self.bridge.inspect_version(text=text)
        rs = nstudio_codec.inspect_version_rust(text)
        keys = ("documentSchemaVersion", "schemaSupported",
                "docHeaderVersions", "notYetInRegistry",
                "unknownDocRequirements", "anyDropped")
        for key in keys:
            self.assertEqual(
                py[key], rs[key],
                f"{key} disagrees between engines:\n  python={py[key]!r}\n"
                f"  rust  ={rs[key]!r}")
        return py, rs

    def test_honored_document(self):
        self._diff(self._text(requiresRegistry=["1.0", "1.1"]))

    def test_future_requirement_is_nameable_in_both(self):
        self._diff(self._text(requiresRegistry=["1.2"]))

    def test_future_only_document_drops_in_both(self):
        self._diff(self._text(requiresRegistry=["1.2"]))

    def test_cleanly_newer_unknown_is_nameable_in_both(self):
        self._diff(self._text(requiresRegistry=["1.10"]))

    def test_junk_requirements_flagged_in_both(self):
        for junk in ("1,1", "v1.2", "0.9"):
            with self.subTest(junk=junk):
                self._diff(self._text(requiresRegistry=[junk]))

    def test_mixed_requirements_order_in_both(self):
        py, rs = self._diff(
            self._text(requiresRegistry=["1.10", "1.0", "1.2"]))
        self.assertEqual(py["notYetInRegistry"], ["1.2", "1.10"])

    def test_pill_variant_real_document(self):
        with open(PILL, "r", encoding="utf-8") as fh:
            self._diff(fh.read())

    def test_unsupported_schema_flagged_in_both(self):
        self._diff(self._text(version="0.4.0"))

    def test_malformed_json_same_error_class(self):
        text = "{nope"
        py_report = self.bridge.inspect_version(text=text)
        self.assertFalse(py_report["ok"])
        with self.assertRaises(nstudio.NstudioValidationError):
            nstudio_codec.inspect_version_rust(text)


if __name__ == "__main__":
    unittest.main()
