"""Tests for ``nyrqisctl debug bundle`` (DBG-001 Phase A).

The bundle command is a pure client-side composition of the daemon's
EXISTING authorized ops (health/status/containers/audit_log) — these
tests pin exactly that contract: no new daemon surface, no partial
bundle on a failed core op, supplementary (non-fatal) audit trail,
and per-file provenance in the output.
"""

import argparse
import importlib
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

nyrqisctl = importlib.import_module("nyrqisctl")


def _args(**over):
    base = dict(
        out=os.path.join(tempfile.mkdtemp(prefix="dbg-test-"), "bundle"),
        socket="/tmp/none.sock",
        timeout=1.0,
        container=[],
        audit_tail=100,
    )
    base.update(over)
    return argparse.Namespace(**base)


class DebugBundleCLITests(unittest.TestCase):
    """The `debug bundle` subcommand parses with its documented flags."""

    def test_cli_wires_debug_bundle_subcommand(self):
        parser = nyrqisctl.build_parser()
        args = parser.parse_args([
            "debug", "bundle", "--out", "/tmp/b",
            "--container", "c1", "--container", "c2",
            "--audit-tail", "42",
        ])
        self.assertEqual(args.command, "debug-bundle")
        self.assertEqual(args.out, "/tmp/b")
        self.assertEqual(args.container, ["c1", "c2"])
        self.assertEqual(args.audit_tail, 42)


class DebugBundleBehaviourTests(unittest.TestCase):
    """Composer behavior against a scripted (fake) transport."""

    def setUp(self):
        self._old_call = nyrqisctl.call_daemon

    def tearDown(self):
        nyrqisctl.call_daemon = self._old_call

    @staticmethod
    def _script(replies, calls):
        def fake_call(socket_path, payload, timeout_s=30.0):
            calls.append(payload)
            op = payload.get("op")
            if op in replies:
                return replies[op]
            return {"ok": True, "op": op}
        return fake_call

    def test_bundle_refuses_existing_output(self):
        """Refuses to merge into an existing bundle (exit 2, no writes)."""
        a = _args()
        os.makedirs(a.out)
        rc = nyrqisctl._debug_bundle(a)
        self.assertEqual(rc, 2)
        self.assertEqual(os.listdir(a.out), [])

    def test_bundle_aborts_without_daemon_no_partial(self):
        """A failed CORE op aborts the whole bundle — no partial output.
        `call_daemon` returns None when the daemon did not reply —
        exactly the no-daemon condition."""
        a = _args()
        nyrqisctl.call_daemon = (
            lambda socket_path, payload, timeout_s=30.0: None)
        rc = nyrqisctl._debug_bundle(a)
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(a.out))

    def test_bundle_composes_from_existing_ops(self):
        """The bundle is exactly the existing ops' replies on disk."""
        a = _args()
        calls = []
        replies = {
            "health": {"ok": True, "op": "health", "uptime_s": 12},
            "status": {"ok": True, "op": "status", "version": "test"},
            "container_list": {"ok": True,
                               "containers": [{"id": "c1"}, {"id": "c2"}]},
            "container_stats": {"ok": True, "cpu_pct": 1.0},
            "container_logs": {"ok": True, "lines": ["x"]},
            "container_top": {"ok": True, "procs": 3},
            "container_network_stats": {"ok": True, "rx": 1},
            "audit_log": {"ok": True, "records": [{"seq": 1}]},
        }
        nyrqisctl.call_daemon = self._script(replies, calls)
        rc = nyrqisctl._debug_bundle(a)
        self.assertEqual(rc, 0)

        meta = json.load(open(os.path.join(a.out, "meta.json")))
        self.assertEqual(meta["bundle_format"], 1)
        self.assertIn("no new daemon surface", meta["note"])
        self.assertEqual(meta["containers_requested"], ["c1", "c2"])

        health = json.load(open(os.path.join(a.out, "health.json")))
        self.assertEqual(health["op"], "health")
        status = json.load(open(os.path.join(a.out, "status.json")))
        self.assertEqual(status["version"], "test")
        per = json.load(open(os.path.join(a.out, "per-container.json")))
        self.assertIn("c1", per)
        self.assertIn("c2", per)
        self.assertEqual(per["c1"]["stats.json"]["cpu_pct"], 1.0)
        self.assertEqual(per["c1"]["logs.json"]["lines"], ["x"])
        audit = json.load(open(os.path.join(a.out, "audit-log.json")))
        self.assertEqual(audit["records"], [{"seq": 1}])
        nui = json.load(open(os.path.join(a.out, "nui-current.json")))
        self.assertEqual(nui["op"], "nui_current")

        # The composed ops are EXACTLY the pre-existing daemon surface.
        ops_seen = {p["op"] for p in calls}
        self.assertEqual(ops_seen, {
            "health", "status", "container_list", "container_stats",
            "container_logs", "container_top", "container_network_stats",
            "audit_log", "nui_current",
        })

    def test_bundle_records_per_container_errors(self):
        """A failing detail op is recorded as an error entry, not fatal."""
        a = _args(container=["c1"])

        def fake_call(socket_path, payload, timeout_s=30.0):
            op = payload.get("op")
            if op == "container_logs":
                return {"ok": False, "error": "logs refused"}
            return {"ok": True, "op": op}

        nyrqisctl.call_daemon = fake_call
        rc = nyrqisctl._debug_bundle(a)
        self.assertEqual(rc, 0)
        per = json.load(open(os.path.join(a.out, "per-container.json")))
        self.assertEqual(per["c1"]["logs.json"]["error"], "logs refused")
        self.assertEqual(per["c1"]["stats.json"]["op"], "container_stats")

    def test_bundle_survives_audit_trail_failure(self):
        """The audit tail is supplementary — its failure is recorded,
        the bundle still completes."""
        a = _args()
        replies = {
            "health": {"ok": True, "op": "health"},
            "status": {"ok": True, "op": "status"},
            "container_list": {"ok": True, "containers": []},
            "audit_log": {"ok": False, "error": "trail unavailable"},
        }
        nyrqisctl.call_daemon = self._script(replies, [])
        rc = nyrqisctl._debug_bundle(a)
        self.assertEqual(rc, 0)
        audit = json.load(open(os.path.join(a.out, "audit-log.json")))
        self.assertEqual(audit["error"], "trail unavailable")
        self.assertIn("supplementary", audit["note"])


if __name__ == "__main__":
    unittest.main()
