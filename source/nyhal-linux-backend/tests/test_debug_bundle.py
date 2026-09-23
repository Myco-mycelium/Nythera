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
        chain_id=[],
        redact=True,
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
            "--chain-id", "ch-1", "--chain-id", "ch-2",
        ])
        self.assertEqual(args.command, "debug-bundle")
        self.assertEqual(args.out, "/tmp/b")
        self.assertEqual(args.container, ["c1", "c2"])
        self.assertEqual(args.audit_tail, 42)
        self.assertEqual(args.chain_id, ["ch-1", "ch-2"])
        # Redaction is the DEFAULT (DBG-001 §3): opt-out only.
        self.assertIs(args.redact, True)
        args_off = parser.parse_args(
            ["debug", "bundle", "--out", "/tmp/b", "--no-redact"])
        self.assertIs(args_off.redact, False)


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
            "health": {"ok": True, "op": "health", "uptime_s": 12,
                       "vault": {"volumes": 2, "logical_bytes": 999,
                                 "physical_bytes": 888,
                                 "warned_containers": 1}},
            "status": {"ok": True, "op": "status", "version": "test",
                       "vault": {"volumes": 2, "logical_bytes": 999}},
            "container_list": {"ok": True,
                               "containers": [{"id": "c1"}, {"id": "c2"}]},
            "container_stats": {"ok": True, "cpu_pct": 1.0},
            "container_logs": {"ok": True, "lines": ["x"]},
            "container_top": {"ok": True, "procs": 3},
            "container_network_stats": {"ok": True, "rx": 1},
            "audit_log": {"ok": True, "entries": [{"seq": 1}]},
            "nui_current": {"ok": True, "op": "nui_current"},
        }
        nyrqisctl.call_daemon = self._script(replies, calls)
        rc = nyrqisctl._debug_bundle(a)
        self.assertEqual(rc, 0)

        meta = json.load(open(os.path.join(a.out, "meta.json")))
        self.assertEqual(meta["bundle_format"], 2)
        self.assertTrue(meta["redacted"])
        self.assertIn("no new daemon surface", meta["note"])
        self.assertEqual(meta["containers_requested"], ["c1", "c2"])

        # Redaction (default ON): vault aggregates stripped, volume
        # count kept, the redaction itself marked.
        health = json.load(open(os.path.join(a.out, "health.json")))
        self.assertEqual(health["vault"]["redacted"], True)
        self.assertEqual(health["vault"]["volumes"], 2)
        self.assertNotIn("logical_bytes", health["vault"])
        self.assertNotIn("physical_bytes", health["vault"])
        status = json.load(open(os.path.join(a.out, "status.json")))
        self.assertNotIn("logical_bytes", status["vault"])
        per = json.load(open(os.path.join(a.out, "per-container.json")))
        self.assertIn("c1", per)
        self.assertIn("c2", per)
        self.assertEqual(per["c1"]["stats.json"]["cpu_pct"], 1.0)
        self.assertEqual(per["c1"]["logs.json"]["lines"], ["x"])
        # The audit trail is per-container (the op REQUIRES a
        # container_id — no daemon-wide trail exists).
        self.assertEqual(per["c1"]["audit.json"]["entries"],
                         [{"seq": 1}])
        self.assertEqual(per["c2"]["audit.json"]["entries"],
                         [{"seq": 1}])
        nui = json.load(open(os.path.join(a.out, "nui-current.json")))
        self.assertEqual(nui["op"], "nui_current")

        # The composed ops are EXACTLY the pre-existing daemon surface.
        ops_seen = {p["op"] for p in calls}
        self.assertEqual(ops_seen, {
            "health", "status", "container_list", "container_stats",
            "container_logs", "container_top", "container_network_stats",
            "audit_log", "nui_current",
        })
        # Every audit_log call carried a container id.
        for p in calls:
            if p["op"] == "audit_log":
                self.assertIn(p["container_id"], ("c1", "c2"))

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
        """The per-container audit trail is supplementary — its failure
        is recorded in that container's detail, the bundle completes."""
        a = _args()
        replies = {
            "health": {"ok": True, "op": "health"},
            "status": {"ok": True, "op": "status"},
            "container_list": {"ok": True, "containers": [{"id": "c1"}]},
            "audit_log": {"ok": False, "error": "trail unavailable"},
        }
        nyrqisctl.call_daemon = self._script(replies, [])
        rc = nyrqisctl._debug_bundle(a)
        self.assertEqual(rc, 0)
        per = json.load(open(os.path.join(a.out, "per-container.json")))
        self.assertEqual(per["c1"]["audit.json"]["error"],
                         "trail unavailable")
        self.assertIn("supplementary", per["c1"]["audit.json"]["note"])

    def test_no_redact_keeps_raw_replies(self):
        """--no-redact is the operator's explicit opt-out: the raw
        vault aggregates stay in the bundle, meta records the choice."""
        a = _args(redact=False)
        replies = {
            "health": {"ok": True, "op": "health",
                       "vault": {"volumes": 1, "logical_bytes": 555}},
            "status": {"ok": True, "op": "status"},
            "container_list": {"ok": True, "containers": []},
        }
        nyrqisctl.call_daemon = self._script(replies, [])
        rc = nyrqisctl._debug_bundle(a)
        self.assertEqual(rc, 0)
        health = json.load(open(os.path.join(a.out, "health.json")))
        self.assertEqual(health["vault"]["logical_bytes"], 555)
        meta = json.load(open(os.path.join(a.out, "meta.json")))
        self.assertFalse(meta["redacted"])

    def test_chain_ids_capture_summary_and_verification(self):
        """--chain-id drives the existing summary + verification ops;
        results land in audit-chains.json keyed by chain id."""
        a = _args(chain_id=["ch-9"])
        calls = []
        replies = {
            "health": {"ok": True, "op": "health"},
            "status": {"ok": True, "op": "status"},
            "container_list": {"ok": True, "containers": []},
            "get_audit_summary": {"ok": True, "chain_id": "ch-9",
                                  "total_entries": 3},
            "verify_audit_chain": {"ok": True, "chain_id": "ch-9",
                                   "verified": True},
        }
        nyrqisctl.call_daemon = self._script(replies, calls)
        rc = nyrqisctl._debug_bundle(a)
        self.assertEqual(rc, 0)
        chains = json.load(
            open(os.path.join(a.out, "audit-chains.json")))
        self.assertEqual(chains["ch-9"]["summary"]["total_entries"], 3)
        self.assertIs(chains["ch-9"]["verification"]["verified"], True)
        meta = json.load(open(os.path.join(a.out, "meta.json")))
        self.assertEqual(meta["chains_requested"], ["ch-9"])
        ops_seen = {p["op"] for p in calls}
        self.assertIn("get_audit_summary", ops_seen)
        self.assertIn("verify_audit_chain", ops_seen)

    def test_chain_op_errors_are_recorded_not_fatal(self):
        """A failing chain op is supplementary: the error is recorded
        in its entry and the bundle completes."""
        a = _args(chain_id=["gone"])
        replies = {
            "health": {"ok": True, "op": "health"},
            "status": {"ok": True, "op": "status"},
            "container_list": {"ok": True, "containers": []},
            "get_audit_summary": {"ok": False, "error": "not found"},
        }
        nyrqisctl.call_daemon = self._script(replies, [])
        rc = nyrqisctl._debug_bundle(a)
        self.assertEqual(rc, 0)
        chains = json.load(
            open(os.path.join(a.out, "audit-chains.json")))
        self.assertEqual(chains["gone"]["summary"]["error"], "not found")
        self.assertIn("supplementary", chains["gone"]["summary"]["note"])


if __name__ == "__main__":
    unittest.main()
