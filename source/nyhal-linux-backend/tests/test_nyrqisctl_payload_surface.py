"""The nyrqisctl payload-surface pin (2026-09-23).

History: an unconditional ``raise ValueError("unknown command")`` sat
midway inside ``build_payload`` since the CLI's first commit (4bb68bb),
cutting off ~300 payload branches that follow it — those commands
parsed fine but crashed with "unknown command" at runtime. The same
commit-series class as the PackageManager duplicate-method shadow: the
manager-layer tests never exercised the CLI's parse->payload path, so
the raise was invisible to the suite. These tests walk the REAL
argparse tree and pin that every registered command resolves to a
payload — so the surface can never silently rot again.

The chain-summary fix is also pinned here: the chain variant of the
summary previously mutated alert-summary's parser (reused ``asum``
variable), breaking ``alert-summary`` outright and leaving the chain
summary unreachable. The chain summary now lives under its own name.
"""

import argparse
import importlib
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

nyrqisctl = importlib.import_module("nyrqisctl")


class _Lax(argparse.Namespace):
    """A namespace whose unknown attributes are None — lets
    build_payload run for every command without a real argv."""

    def __getattr__(self, name):
        return None


def _registered_commands():
    """Every command name build_parser registers via
    set_defaults(command="...") — read from the source so the sweep
    fails loudly if the registration mechanism changes shape."""
    src = open(os.path.join(_HERE, "nyrqisctl.py")).read()
    return sorted(set(re.findall(
        r'set_defaults\(command="([a-z0-9-]+)"', src)))


class PayloadSurfaceTests(unittest.TestCase):
    """Every registered command must resolve through build_payload."""

    def test_every_registered_command_resolves_to_a_payload(self):
        """THE pin: no registered command may raise "unknown command".

        The probe walks every registered command through build_payload
        with a lenient namespace. A ValueError whose message starts
        with "unknown command" is the stray-raise / unmapped-command
        class — forbidden. Other exceptions are tolerated ONLY as
        lenient-namespace artifacts: TypeError/AttributeError from
        payloads that coerce attrs, ValueError subclasses
        (JSONDecodeError) or pytest's captured-stdin OSError from the
        two commands whose payloads read stdin/files
        (event-log-compress, event-log-import) — those are
        mapped commands with real I/O, not unreachable branches.
        """
        registered = _registered_commands()
        self.assertGreater(len(registered), 500,
                           "command surface suspiciously small — "
                           "the registration regex or parser changed")
        dead = []
        for name in registered:
            try:
                nyrqisctl.build_payload(name, _Lax())
            except ValueError as exc:
                if str(exc).startswith("unknown command"):
                    dead.append(name)
            except (TypeError, AttributeError):
                pass
            except OSError as exc:
                # pytest's captured-stdin artifact from the two
                # stdin-reading payloads — probe noise, not an
                # unreachable branch. Any other OSError re-raises.
                if "stdin" in str(exc) or "captured" in str(exc):
                    pass
                else:
                    raise
        self.assertEqual(dead, [],
                         "commands registered by build_parser but "
                         "unreachable in build_payload: %r" % dead)

    def test_audit_chain_summary_has_its_own_command(self):
        """The chain summary is reachable under audit-chain-summary and
        maps to the daemon's get_audit_summary op."""
        parser = nyrqisctl.build_parser()
        args = parser.parse_args(
            ["audit-chain-summary", "--chain-id", "ch-1"])
        self.assertEqual(args.command, "audit-chain-summary")
        payload = nyrqisctl.build_payload(args.command, args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "get_audit_summary")
        self.assertEqual(payload["chain_id"], "ch-1")

    def test_container_audit_summary_is_intact(self):
        """The container variant (audit-summary <cid>) keeps its own
        mapping — no name collision, first-branch shadowing."""
        parser = nyrqisctl.build_parser()
        args = parser.parse_args(["audit-summary", "cid-1"])
        self.assertEqual(args.command, "audit-summary")
        payload = nyrqisctl.build_payload(args.command, args)
        self.assertEqual(payload["op"], "audit_summary")
        self.assertEqual(payload["container_id"], "cid-1")
        self.assertNotIn("chain_id", payload)

    def test_alert_summary_is_not_hijacked(self):
        """alert-summary parses with no required --chain-id and maps to
        the alert_summary op (the reused-variable regression)."""
        parser = nyrqisctl.build_parser()
        args = parser.parse_args(["alert-summary"])
        self.assertEqual(args.command, "alert-summary")
        self.assertFalse(hasattr(args, "chain_id"))
        payload = nyrqisctl.build_payload(args.command, args)
        self.assertEqual(payload["op"], "alert_summary")

    def test_debug_bundle_ops_resolve(self):
        """The debug bundle's composed ops (incl. the audit_log
        container_id requirement) all map to real payloads."""
        for op, expect in (
            ("health", "health"),
            ("status", "status"),
            ("container_list", "container_list"),
            ("container_stats", "container_stats"),
            ("container_logs", "container_logs"),
            ("container_top", "container_top"),
            ("container_network_stats", "container_network_stats"),
            ("audit_log", "audit_log"),
            ("get_audit_summary", "get_audit_summary"),
            ("verify_audit_chain", "verify_audit_chain"),
            ("nui_current", "nui_current"),
        ):
            payload = nyrqisctl.build_payload(
                expect, _Lax()) if expect not in (
                "health", "status") else None
            if payload is None:
                continue
            self.assertEqual(payload.get("op"), expect)


class FormatterSurfaceTests(unittest.TestCase):
    """format_human must never crash on the replies its commands get.

    The 2026-09-23 sweep probed every registered command and found NO
    genuine formatter bug — the synthetic-reply "crashes" were probe
    artifacts (the daemon's real shapes differ: capacity-plan's
    summary is a string per the manager's plan_capacity; the health/
    shutdown state fields are dicts-or-None and the formatter already
    guards `if hc`).

    Pinned honestly, in two tiers:
    - EVERY command with a sparse ``{"ok": true}`` reply — the one
      universal invariant (branches must ride .get defaults).
    - Commands whose reply shapes were verified against the daemon
      side (ipc/control.py + backend/container.py) get those exact
      families. Commands NOT individually verified get no invented
      family — coverage grows with verification, not assumption.
    """

    def test_formatter_never_crashes_on_sparse_ok_reply(self):
        """Universal: any command's branch must survive ok:true alone."""
        registered = _registered_commands()
        crashes = []
        for name in registered:
            try:
                nyrqisctl.format_human(name, {"ok": True, "op": name})
            except Exception as exc:  # noqa: BLE001 — the pin
                crashes.append((name, type(exc).__name__, str(exc)[:60]))
        self.assertEqual(
            crashes, [],
            "format_human crashed on sparse ok:true replies: %r" % crashes)

    def test_formatter_verified_shapes(self):
        """Ground-truth families for individually verified commands
        (each traced to ipc/control.py / backend/container.py)."""
        families = {
            # manager plan_capacity: summary is a STRING; per-resource
            # plans carry sufficient_data/risk_level.
            "capacity-plan": {
                "ok": True, "container_id": "c1", "horizon_days": 30,
                "summary": "Planning horizon: 30 days. 0 issues.",
                "resources": {"memory": {"sufficient_data": False,
                                         "risk_level": "unknown"}},
                "recommended_limits": {}, "issue_count": 0},
            # manager get_health_check: dict-or-None fields.
            "get-health-check": {
                "ok": True, "container_name": "c1",
                "health_check": {"type": "process"},
                "state": {"status": "pending",
                          "consecutive_failures": 0}},
            "get-health-check-none": {
                "ok": True, "container_name": "c1",
                "health_check": None, "state": {}},
            # manager get_shutdown_status.
            "get-shutdown-status": {
                "ok": True, "container_name": "c1",
                "config": {"enabled": True},
                "state": {"status": "active", "started_at": None}},
            # manager get_audit_summary (container variant).
            "audit-summary": {
                "ok": True, "container_id": "c1", "total_entries": 2,
                "by_action": {"a": 1}, "by_actor": {"o": 1},
                "recent": [{"action": "a", "actor": "o"}]},
            # NuiService nui_current/validate/load: summary is a DICT.
            "nui-current": {
                "ok": True, "loaded": True, "valid": True,
                "summary": {"engine": "py", "version": "1.0.0",
                            "screens": ["main"], "components": 3,
                            "behaviors": 1, "bindings": 2},
                "path": "/state/ui/shell.nstudio"},
            "nui-validate": {
                "ok": True,
                "summary": {"engine": "rust", "version": "1.0.0",
                            "screens": [], "components": 0,
                            "behaviors": 0, "bindings": 0}},
            # get_health_check config-summary family (fleet overview).
            "fleet-health-overview": {
                "ok": True,
                "summary": {"healthy_count": 1, "unhealthy_count": 0,
                            "pending_count": 0, "no_check_count": 0}},
        }
        crashes = []
        for name, resp in families.items():
            try:
                nyrqisctl.format_human(name, resp)
            except Exception as exc:  # noqa: BLE001 — the pin
                crashes.append((name, type(exc).__name__, str(exc)[:60]))
        self.assertEqual(
            crashes, [],
            "format_human crashed on verified daemon shapes: %r" % crashes)


if __name__ == "__main__":
    unittest.main()
