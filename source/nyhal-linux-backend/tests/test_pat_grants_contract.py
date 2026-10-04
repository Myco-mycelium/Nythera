"""PAT-grants tooling contract tests.

``scripts/verify_pat_grants.sh`` is the workstation's gate for the
credential-rotation ritual: ``scripts/run_staged_drill.sh`` dies on its
verdict before touching anything. Two regressions shipped through it
and both are pinned here so they cannot return silently:

  - The actions-write probe POSTs a workflow_dispatch of
    pat-expiry-watch.yml. GitHub's create-workflow-dispatch-event
    endpoint REJECTS a ref-less payload with 422 ("ref wasn't
    supplied") — but from 2026-09-30 to 2026-10-04 the probe shipped
    exactly that ref-less payload, so Actions write read as MISSING on
    every grant flip no matter what the token actually held (the real
    dispatch, which sends {"ref":"main"}, returned 204 the whole
    time). The probe payload MUST carry a ref.

  - The drill selects the run to watch by "newest workflow_dispatch
    run" — with no identity check. When another dispatch of the same
    workflow happened earlier (a manual with-arm64 verification, say),
    the lookup returned that STALE completed run and the drill claimed
    SUCCESS in seconds. The selection MUST pin the run by head_sha
    (the ref that was just dispatched) and by created_at (at/after the
    dispatch instant).
"""

import os
import re
import unittest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(os.path.dirname(_BACKEND_DIR))

GRANTS_SCRIPT = os.path.join(_REPO_ROOT, "scripts", "verify_pat_grants.sh")
DRILL_SCRIPT = os.path.join(_REPO_ROOT, "scripts", "run_staged_drill.sh")


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


@unittest.skipUnless(
    os.path.exists(GRANTS_SCRIPT),
    "verify_pat_grants.sh not present in this checkout")
class TestGrantsProbeDispatchPayload(unittest.TestCase):
    """The probe's dispatch must be a VALID dispatch, not a 422 machine."""

    def test_probe_script_present_with_expected_shape(self):
        text = read(GRANTS_SCRIPT)
        self.assertIn("pat-expiry-watch.yml/dispatches", text)
        self.assertIn("actions write", text)

    def test_actions_probe_payload_carries_ref(self):
        # GitHub rejects a ref-less dispatch with 422; the probe used to
        # send `-d '{}'` and read that as GRANTS MISSING forever.
        text = read(GRANTS_SCRIPT)
        m = re.search(
            r"workflows/pat-expiry-watch\.yml/dispatches", text)
        self.assertIsNotNone(m, "probe dispatch call missing")
        # The -d payload on the dispatch POST must set a ref.
        payload = re.search(
            r"-d\s+'(\{[^']*\})'\s*\\\n\s*\"https://api\.github\.com"
            r"/repos/\$REPO/actions/workflows/pat-expiry-watch\.yml"
            r"/dispatches\"", text)
        self.assertIsNotNone(payload, "dispatch -d payload not found")
        self.assertIn('"ref"', payload.group(1))

    def test_probe_failure_path_prints_the_api_error(self):
        # A 4xx body is the difference between "grants missing" and
        # "probe is wrong" — the report must surface it verbatim.
        text = read(GRANTS_SCRIPT)
        self.assertIn("api says:", text)

    def test_drill_dispatch_payload_carries_ref(self):
        # The drill dispatches the real workflow; same endpoint rule.
        # Its payload is printf'd to a file that the curl POST consumes
        # (-d @file), so pin both halves of that wiring.
        text = read(DRILL_SCRIPT)
        self.assertIn(
            'printf \'{"ref":"main","inputs":{"with-arm64":"true"}}\'',
            text)
        self.assertIn("live-iso-rootless.yml/dispatches", text)
        self.assertIn("-d @/tmp/drill-payload.json", text)


@unittest.skipUnless(
    os.path.exists(DRILL_SCRIPT),
    "run_staged_drill.sh not present in this checkout")
class TestDrillRunSelection(unittest.TestCase):
    """The watched run must be THE dispatch's run, not any newer old one."""

    def test_selection_filters_by_head_sha(self):
        text = read(DRILL_SCRIPT)
        self.assertIn("head_sha=$MAIN_SHA", text)

    def test_selection_filters_by_dispatch_instant(self):
        text = read(DRILL_SCRIPT)
        self.assertIn("DISPATCH_AT", text)
        # The instant is captured BEFORE the POST (the run is created
        # milliseconds later; a capture after the POST races the run).
        self.assertLess(
            text.index("DISPATCH_AT=\"$(date"),
            text.index("live-iso-rootless.yml/dispatches"),
            "DISPATCH_AT must be captured before the dispatch POST")
        # And the selection compares created_at against it.
        self.assertIn("r['created_at'] >= os.environ['DISPATCH_AT']", text)


if __name__ == "__main__":
    unittest.main()
