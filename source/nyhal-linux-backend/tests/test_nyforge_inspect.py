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
PILL = os.path.join(_REPO, "shell", "variants", "pill.nstudio")


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
        # The document reaches beyond this build: the FULL log is the
        # renderable context (what this build DOES have — never a
        # truncated tail with no decision context).
        self.assertEqual(
            [e["registryVersion"] for e in report["changesSinceOldestRequirement"]],
            ["1.0", "1.1"])

    def test_future_only_document_gets_full_change_context(self):
        # A document authored ONLY against a newer registry must still
        # see this build's whole contract lineage — telling the user
        # "would drop" with zero context is not actionable.
        report = _bridge().inspect_version(
            text=_stock_text(requiresRegistry=["1.2"]))
        self.assertTrue(report["anyDropped"])
        self.assertEqual(
            [e["registryVersion"] for e in report["changesSinceOldestRequirement"]],
            ["1.0", "1.1"])


class TestSafeHotReload(unittest.TestCase):
    """refresh() preflights before it tears down: a broken edit must
    never destroy the live session, and every real reload carries the
    Inspector verdict."""

    class _FakeSession:
        def __init__(self):
            self.closed = 0

        def add_window(self, window):
            pass

        def close_window(self, window_id):
            self.closed += 1

    def _write(self, path, raw):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)

    def _stock(self):
        with open(STOCK, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def test_broken_edit_is_rejected_and_session_kept(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "doc.nstudio")
            good = self._stock()
            self._write(path, good)
            session = self._FakeSession()
            bridge = NyforgeBridge(session)
            self.assertTrue(bridge.load_document(path)["ok"])
            before = len(bridge.mapped_windows)
            self.assertGreater(before, 0)

            broken = self._stock()
            broken["screens"] = "not-a-list"
            self._write(path, broken)
            result = bridge.refresh()

            self.assertFalse(result["ok"])
            self.assertIn("session kept", result["error"])
            self.assertIn("inspector", result)
            self.assertEqual(len(bridge.mapped_windows), before,
                             "a rejected edit must not tear down windows")
            self.assertEqual(session.closed, 0)
        finally:
            shutil.rmtree(tmp)

    def test_valid_edit_reloads_with_verdict(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "doc.nstudio")
            good = self._stock()
            good["requiresRegistry"] = ["1.1"]
            self._write(path, good)
            bridge = NyforgeBridge(self._FakeSession())
            self.assertTrue(bridge.load_document(path)["ok"])

            edited = json.loads(json.dumps(good))
            edited["project"] = {
                "name": "edited v2", "id": "demo-v2"}
            self._write(path, edited)
            result = bridge.refresh()

            self.assertTrue(result["ok"])
            self.assertNotIn("unchanged", result)
            self.assertEqual(
                result["inspector"]["docHeaderVersions"], ["1.1"])
            self.assertFalse(result["inspector"]["anyDropped"])
        finally:
            shutil.rmtree(tmp)

    def test_rejection_is_transition_driven_not_per_poll(self):
        """A file that STAYS broken fires the callback once per edit;
        re-polling identical broken bytes returns the cached verdict
        silently. Repairing to the ORIGINAL bytes is honestly
        ``unchanged`` (the session never stopped running them); a
        different valid edit reloads exactly once."""
        import shutil
        import tempfile
        import threading
        import time
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "doc.nstudio")
            good = self._stock()
            edited = json.loads(json.dumps(good))
            edited["project"] = {"name": "v2", "id": "v2-id"}
            broken = json.loads(json.dumps(good))
            broken["screens"] = "oops"

            self._write(path, good)
            bridge = NyforgeBridge(self._FakeSession())
            bridge.load_document(path)
            events = []
            done = threading.Event()

            def callback(event, data):
                events.append(event)
                if event == "reload":
                    done.set()

            bridge.enable_hot_reload(interval=0.05, callback=callback)
            try:
                self._write(path, broken)
                time.sleep(0.3)
                self._write(path, broken)  # same broken bytes again
                time.sleep(0.2)
                self.assertEqual(events.count("reload_rejected"), 1,
                                 "one rejection per broken edit")

                self._write(path, edited)  # different, valid
                self.assertTrue(done.wait(timeout=3.0),
                                "a different valid edit must reload")
                self.assertEqual(events.count("reload"), 1)
            finally:
                bridge.disable_hot_reload()
        finally:
            shutil.rmtree(tmp)

    def test_identical_content_reports_unchanged(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "doc.nstudio")
            self._write(path, self._stock())
            bridge = NyforgeBridge(self._FakeSession())
            bridge.load_document(path)
            # Rewrite byte-identical content.
            self._write(path, self._stock())
            result = bridge.refresh()
            self.assertTrue(result["ok"])
            self.assertTrue(result["unchanged"])
        finally:
            shutil.rmtree(tmp)


class TestHotSwap(unittest.TestCase):
    """swap_document: variant switching with refresh's guarantee —
    the target is preflighted before teardown; a broken target keeps
    the live session; the watch path follows the new document."""

    class _FakeSession:
        def __init__(self):
            self.closed = 0

        def add_window(self, window):
            pass

        def close_window(self, window_id):
            self.closed += 1

    def _write(self, path, raw):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)

    def _stock(self):
        with open(STOCK, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def test_swap_to_valid_document_switches(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        try:
            a = os.path.join(tmp, "stock.nstudio")
            b = os.path.join(tmp, "other.nstudio")
            other = self._stock()
            other["requiresRegistry"] = ["1.1"]
            other["project"] = {"name": "other", "id": "other-id"}
            self._write(a, self._stock())
            self._write(b, other)

            events = []
            bridge = NyforgeBridge(self._FakeSession())
            bridge._callbacks.append(lambda ev, d: events.append(ev))
            self.assertTrue(bridge.load_document(a)["ok"])

            result = bridge.swap_document(b)
            self.assertTrue(result["ok"])
            self.assertTrue(result["previous_path"].endswith("stock.nstudio"))
            self.assertEqual(result["inspector"]["docHeaderVersions"], ["1.1"])
            self.assertFalse(result["inspector"]["anyDropped"])
            self.assertTrue(bridge.doc_path.endswith("other.nstudio"))
            self.assertIn("swap", events)
        finally:
            shutil.rmtree(tmp)

    def test_swap_to_broken_target_keeps_session(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        try:
            a = os.path.join(tmp, "stock.nstudio")
            bad = os.path.join(tmp, "bad.nstudio")
            broken = self._stock()
            broken["screens"] = "oops"
            self._write(a, self._stock())
            self._write(bad, broken)

            bridge = NyforgeBridge(self._FakeSession())
            self.assertTrue(bridge.load_document(a)["ok"])
            before = len(bridge.mapped_windows)

            result = bridge.swap_document(bad)
            self.assertFalse(result["ok"])
            self.assertIn("session kept", result["error"])
            self.assertTrue(result["previous_path_kept"])
            self.assertIn("inspector", result)
            self.assertTrue(bridge.doc_path.endswith("stock.nstudio"))
            self.assertEqual(len(bridge.mapped_windows), before)
        finally:
            shutil.rmtree(tmp)

    def test_swap_without_loaded_document_preflights(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        try:
            a = os.path.join(tmp, "doc.nstudio")
            self._write(a, self._stock())
            bridge = NyforgeBridge(self._FakeSession())
            result = bridge.swap_document(a)
            self.assertTrue(result["ok"])
            self.assertIn("inspector", result)
        finally:
            shutil.rmtree(tmp)

    def test_swap_to_missing_target_is_rejected(self):
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        try:
            a = os.path.join(tmp, "doc.nstudio")
            self._write(a, self._stock())
            bridge = NyforgeBridge(self._FakeSession())
            bridge.load_document(a)
            result = bridge.swap_document(os.path.join(tmp, "nope.nstudio"))
            self.assertFalse(result["ok"])
            self.assertIn("not found", result["error"])
        finally:
            shutil.rmtree(tmp)


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

    def test_inspect_json_cli_is_machine_readable(self):
        """The Nyforge contract panel consumes this exact surface."""
        import json
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, os.path.join(_HERE, "..", "examples",
                                          "nyforge_live.py"),
             "--inspect-json",
             os.path.join(_HERE, "..", "shell", "variants",
                          "pill.nstudio")],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["docHeaderVersions"], ["1.1"])
        self.assertFalse(report["anyDropped"])


if __name__ == "__main__":
    unittest.main()
