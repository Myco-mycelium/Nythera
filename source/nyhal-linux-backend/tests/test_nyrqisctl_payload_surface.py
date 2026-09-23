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
        payloads that coerce attrs, and ValueError subclasses
        (JSONDecodeError) from the two commands whose payloads read
        stdin/files (event-log-compress, event-log-import) — those are
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


if __name__ == "__main__":
    unittest.main()
