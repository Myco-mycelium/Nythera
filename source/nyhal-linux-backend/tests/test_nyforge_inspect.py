#!/usr/bin/env python3
"""Tests for NyforgeBridge.inspect_version — the Inspector preflight
that reports a document's contract/version situation against the
registry's versionHistory WITHOUT importing or injecting it.

Pins:
- the stock shell and the pill variant (requiresRegistry: ["1.1"]) are
  fully honored by this build, with the renderable change span,
- newer-than-shipped requirements land in the named "will lose" set
  (notYetInRegistry), including cleanly-newer unknown versions
  (the cross-build case, dotted-numeric compare — "1.10" > "1.9"),
- junk requirements (typos, skipped versions) are flagged unknown and
  still make the verdict actionable (anyDropped),
- verdicts are deterministic (never set-iteration order),
- failure modes are honest: malformed JSON, missing arguments,
  non-object roots, unsupported schema versions.
"""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from ui.nyforge_bridge import NyforgeBridge, _registry_version_key

_REPO = os.path.join(_HERE, os.pardir)
STOCK = os.path.join(_REPO, "shell", "defaults", "desktop.nstudio")
PILL = os.path.join(_REPO, "shell", "variants", "desktop-pill.nstudio")


def _bridge():
    # inspect_version never touches the session; None is honest.
    return NyforgeBridge(None)


def _stock_text(**header):
    with open(STOCK, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    raw.update(header)
    return json.dumps(raw)


class TestInspectVersionHonored(unittest.TestCase):
    """Documents this build can open completely."""

    def test_stock_shell_fully_honored(self):
        report = _bridge().inspect_version(text=_stock_text())
        self.assertTrue(report["ok"])
        self.assertEqual(report["documentSchemaVersion"], "1.0.0")
        self.assertTrue(report["schemaSupported"])
        self.assertEqual(report["docHeaderVersions"], [])
        self.assertEqual(report["notYetInRegistry"], [])
        self.assertEqual(report["unknownDocRequirements"], [])
        self.assertFalse(report["anyDropped"])
        self.assertEqual(report["changesSinceOldestRequirement"], [])

    def test_pill_variant_declares_and_is_honored(self):
        report = _bridge().inspect_version(path=PILL)
        self.assertTrue(report["ok"])
        self.assertEqual(report["docHeaderVersions"], ["1.1"])
        self.assertFalse(report["anyDropped"])
        self.assertEqual(
            [e["registryVersion"] for e in report["changesSinceOldestRequirement"]],
            ["1.0", "1.1"],
        )
        self.assertIn("cornerRadius",
                      report["changesSinceOldestRequirement"][-1]["change"])

    def test_explicit_11_requirement_on_stock_doc(self):
        report = _bridge().inspect_version(
            text=_stock_text(requiresRegistry=["1.1"]))
        self.assertFalse(report["anyDropped"])
        self.assertEqual(
            [e["registryVersion"] for e in report["changesSinceOldestRequirement"]],
            ["1.0", "1.1"])


class TestInspectVersionLosses(unittest.TestCase):
    """The "will lose these" semantics."""

    def test_known_future_requirement_is_nameable(self):
        report = _bridge().inspect_version(
            text=_stock_text(requiresRegistry=["1.2"]))
        self.assertTrue(report["anyDropped"])
        self.assertEqual(report["notYetInRegistry"], ["1.2"])
        self.assertEqual(report["unknownDocRequirements"], [])

    def test_cleanly_newer_unknown_requirement_is_nameable(self):
        # A document authored on a newer build: "1.2" is not in this
        # registry's history, but it is cleanly newer than "1.1" — the
        # Inspector names it, it is not buried as junk.
        report = _bridge().inspect_version(
            text=_stock_text(requiresRegistry=["1.2"]))
        self.assertEqual(report["notYetInRegistry"], ["1.2"])

    def test_dotted_numeric_compare_not_lexicographic(self):
        # "1.10" > "1.9" numerically; lexicographic order would flip it.
        self.assertGreater(_registry_version_key("1.10"),
                           _registry_version_key("1.9"))
        report = _bridge().inspect_version(
            text=_stock_text(requiresRegistry=["1.10"]))
        self.assertEqual(report["notYetInRegistry"], ["1.10"])
        self.assertTrue(report["anyDropped"])

    def test_junk_requirements_flagged_unknown(self):
        for junk in ("1,1", "v1.2", "0.9", "1.0.0"):
            report = _bridge().inspect_version(
                text=_stock_text(requiresRegistry=[junk]))
            self.assertEqual(report["unknownDocRequirements"], [junk],
                             f"{junk!r} must be flagged unknown")
            self.assertTrue(report["anyDropped"],
                            f"{junk!r} cannot be honored here")
            self.assertEqual(report["notYetInRegistry"], [])

    def test_multi_requirement_verdict_and_order(self):
        report = _bridge().inspect_version(
            text=_stock_text(requiresRegistry=["1.10", "1.0", "1.2"]))
        self.assertTrue(report["anyDropped"])
        # Deterministic, history-ordered — never set-iteration order.
        self.assertEqual(report["notYetInRegistry"], ["1.2", "1.10"])
        self.assertEqual(report["unknownDocRequirements"], [])
        # The document reaches beyond this build: the span shows what
        # this build's log DOES have.
        self.assertEqual(
            [e["registryVersion"] for e in report["changesSinceOldestRequirement"]],
            ["1.0"])


class TestInspectVersionFailures(unittest.TestCase):
    """Honest failure modes."""

    def test_malformed_json(self):
        report = _bridge().inspect_version(text="{nope")
        self.assertFalse(report["ok"])
        self.assertIn("malformed JSON", report["error"])

    def test_missing_arguments(self):
        report = _bridge().inspect_version()
        self.assertFalse(report["ok"])
        self.assertIn("provide", report["error"])

    def test_missing_file(self):
        report = _bridge().inspect_version(
            path=os.path.join(_HERE, "no-such-document.nstudio"))
        self.assertFalse(report["ok"])

    def test_non_object_root(self):
        report = _bridge().inspect_version(text="[1, 2, 3]")
        self.assertFalse(report["ok"])
        self.assertIn("JSON object", report["error"])

    def test_unsupported_schema_flagged_not_fatal(self):
        report = _bridge().inspect_version(
            text=_stock_text(version="0.4.0"))
        self.assertTrue(report["ok"])
        self.assertFalse(report["schemaSupported"])


if __name__ == "__main__":
    unittest.main()
