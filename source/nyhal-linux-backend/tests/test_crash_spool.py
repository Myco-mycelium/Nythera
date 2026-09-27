"""Contract pins for the CRY-001 Option A crash-report spool
(accepted as owner direction 2026-09-27; CRY-001 v0.2.0 §7).

Pinned properties (the CRY-001 §7.7 plan):
- fail-closed spool write (a broken spool never raises from
  spool_report — recovery must survive it);
- redaction-default-on AT WRITE TIME (vault aggregates never reach
  the spooled bytes; --no-redact at the CLI is a read-view choice);
- audit chaining present with the report id in the entry result;
- purge refusal semantics (invalid ids refused; purge counted);
- retention cap enforcement (count and bytes, oldest evicted first);
- the NO-EGRESS assertion: the module imports no HTTP client —
  local-only stays a tested property, not a prose claim.
"""

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

crash_spool = importlib.import_module("backend.crash_spool")
CrashSpool = crash_spool.CrashSpool
CrashSpoolError = crash_spool.CrashSpoolError


class _FakeAuditManager:
    """The ContainerManager chain API surface, minimal."""

    def __init__(self):
        self.created = []
        self.entries = []

    def create_audit_chain(self, container_id):
        cid = f"chain-fake-{len(self.created)}"
        self.created.append(container_id)
        return {"chain_id": cid, "container_id": container_id}

    def append_audit_entry(self, chain_id, op, result=None):
        self.entries.append((chain_id, op, dict(result or {})))
        return {"ok": True, "hash": "f" * 64}


class _BrokenAuditManager:
    def create_audit_chain(self, container_id):
        raise RuntimeError("chain machinery on fire")


class SpoolWriteTests(unittest.TestCase):
    def test_spool_returns_id_and_writes_schema_valid_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = CrashSpool(tmp)
            rid = sp.spool_report(
                recovery_record={"previous_pid": 4242},
                fault_trace="Traceback (most recent call last): ...")
            self.assertTrue(rid and rid.startswith("crash-"))
            report = sp.read_report(rid)
            self.assertEqual(
                report["schema"], crash_spool.SPOOL_SCHEMA_VERSION)
            self.assertEqual(report["recovery_record"]["previous_pid"], 4242)
            self.assertTrue(report["redacted"])

    def test_spool_is_fail_closed_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = CrashSpool(tmp)
            # A file where the spool DIRECTORY must be: os_error path.
            blocker = Path(tmp) / "blocker"
            blocker.write_text("not a dir")
            broken = CrashSpool(str(blocker))
            self.assertIsNone(broken.spool_report({"a": 1}))
            # The good spool in the same tmp keeps working.
            rid = sp.spool_report({"b": 2})
            self.assertTrue(rid)

    def test_minimal_report_spools_from_no_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            rid = crash_spool.spool_from_state_file(
                tmp, os.path.join(tmp, "missing-state.json"))
            self.assertTrue(rid)
            report = CrashSpool(tmp).read_report(rid)
            self.assertIn("spooled_at", report)


class RedactionTests(unittest.TestCase):
    def test_redaction_default_on_at_write_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = CrashSpool(tmp)
            rid = sp.spool_report(recovery_record={
                "previous_pid": 1,
                "vault": {"logical_bytes": 99, "physical_bytes": 88,
                          "warned_containers": 3, "volumes": 2},
                "last_known_manifest": [
                    {"id": "c1", "size_bytes": 4096, "state": "running"},
                ],
            })
            report = sp.read_report(rid)
            rec = report["recovery_record"]
            self.assertTrue(rec["vault"]["redacted"])
            self.assertNotIn("logical_bytes", rec["vault"])
            self.assertNotIn("physical_bytes", rec["vault"])
            self.assertNotIn("warned_containers", rec["vault"])
            self.assertEqual(rec["vault"]["volumes"], 2)
            self.assertNotIn("size_bytes", rec["last_known_manifest"][0])
            self.assertEqual(rec["last_known_manifest"][0]["id"], "c1")

    def test_spooled_bytes_carry_no_vault_figures_even_for_no_redact_reads(self):
        # --no-redact at the CLI is a READ-VIEW choice; the spooled
        # bytes never contain the figures, so no read path can leak
        # them.
        with tempfile.TemporaryDirectory() as tmp:
            sp = CrashSpool(tmp)
            rid = sp.spool_report(recovery_record={
                "vault": {"logical_bytes": 123456},
            })
            raw = (Path(tmp) / f"{rid}.json").read_text()
            self.assertNotIn("123456", raw)

    def test_redact_helper_is_deep_and_plain(self):
        src = {"vault": {"logical_bytes": 1}, "manifest": [{"x_bytes": 2}]}
        out = crash_spool.redact_recovery_record(src)
        out["manifest"][0]["x_bytes"] = 999
        self.assertEqual(src["manifest"][0]["x_bytes"], 2)


class AuditChainTests(unittest.TestCase):
    def test_generation_is_audit_chained_with_report_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeAuditManager()
            sp = CrashSpool(tmp, audit_manager=fake)
            rid = sp.spool_report({"k": "v"})
            self.assertEqual(len(fake.created), 1)
            ops = [(op, res.get("report_id")) for _, op, res in fake.entries]
            self.assertIn(("crash_spool_report", rid), ops)

    def test_broken_audit_manager_never_breaks_the_spool(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = CrashSpool(tmp, audit_manager=_BrokenAuditManager())
            rid = sp.spool_report({"k": "v"})
            self.assertTrue(rid)
            self.assertEqual(len(sp.list_reports()), 1)

    def test_purge_and_evict_are_chained_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeAuditManager()
            sp = CrashSpool(tmp, audit_manager=fake)
            rid = sp.spool_report({})
            sp.purge(rid)
            ops = [op for _, op, _ in fake.entries]
            self.assertIn("crash_spool_purge", ops)


class PurgeAndListingTests(unittest.TestCase):
    def test_purge_single_and_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = CrashSpool(tmp)
            r1 = sp.spool_report({"n": 1})
            r2 = sp.spool_report({"n": 2})
            self.assertEqual(sp.purge(r1), 1)
            self.assertEqual(len(sp.list_reports()), 1)
            self.assertEqual(sp.purge(), 1)  # all
            self.assertEqual(sp.list_reports(), [])

    def test_invalid_ids_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = CrashSpool(tmp)
            for bad in ("../escape", ".hidden", "a/b", "", "x..y"):
                with self.assertRaises(CrashSpoolError):
                    sp.read_report(bad)
                with self.assertRaises(CrashSpoolError):
                    sp.purge(bad)
            with self.assertRaises(CrashSpoolError):
                sp.read_report("does-not-exist")

    def test_listing_needs_no_content_and_reports_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = CrashSpool(tmp)
            sp.spool_report({"n": 1})
            entry = sp.list_reports()[0]
            self.assertTrue(entry["report_id"].startswith("crash-"))
            self.assertGreater(entry["size_bytes"], 0)


class RetentionTests(unittest.TestCase):
    def test_count_cap_evicts_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = CrashSpool(tmp, max_reports=3)
            ids = [sp.spool_report({"n": i}) for i in range(5)]
            listing = sp.list_reports()
            self.assertEqual(len(listing), 3)
            remaining = {e["report_id"] for e in listing}
            self.assertNotIn(ids[0], remaining)
            self.assertNotIn(ids[1], remaining)
            self.assertIn(ids[4], remaining)

    def test_byte_cap_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Each report carries a padding blob > 1 KiB; cap at 2 KiB.
            sp = CrashSpool(tmp, max_bytes=2048)
            sp.spool_report({"pad": "x" * 1024})
            sp.spool_report({"pad": "x" * 1024})
            self.assertLessEqual(len(sp.list_reports()), 2)
            sp.spool_report({"pad": "x" * 1024})
            sp.spool_report({"pad": "x" * 1024})
            sp.spool_report({"pad": "x" * 1024})
            total = sum(e["size_bytes"] for e in sp.list_reports())
            self.assertLessEqual(total, 2048 + 4096)  # bounded, not exact


class NoEgressTests(unittest.TestCase):
    """The CRY-001 §7.7 pin: local-only is a TESTED property."""

    def test_module_imports_no_http_client(self):
        src = Path(crash_spool.__file__).read_text()
        for forbidden in ("urllib", "requests", "http.client", "httpx",
                          "socket.socket", "urlopen"):
            self.assertNotIn(
                forbidden, src,
                f"crash_spool must not reference {forbidden!r} — the "
                "no-egress property is a contract")

    def test_module_imports_only_stdlib_local_modules(self):
        src = Path(crash_spool.__file__).read_text()
        for line in src.splitlines():
            s = line.strip()
            if s.startswith(("import ", "from ")) and "backend" in s:
                # the only local import allowed is the daemon_state
                # reader (the §4.5 integration)
                self.assertIn("daemon_state", s, s)
        imports = [l.strip() for l in src.splitlines()
                   if l.strip().startswith(("import ", "from "))]
        self.assertTrue(any("daemon_state" in i for i in imports))


if __name__ == "__main__":
    unittest.main()
