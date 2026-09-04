"""
Tests for Security Audit tool.
"""
import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.security_audit import (
    SecurityAudit, AuditCheck, OpenPort, OutdatedPackage, UserAudit,
    KernelCheck, FilePermIssue, ServiceAudit, ScanResult,
    Severity, CheckStatus, AuditCategory, ScanProfile,
)


class TestAuditCheck(unittest.TestCase):
    def test_create(self):
        c = AuditCheck("SSH disabled", AuditCategory.SSH, CheckStatus.PASS, Severity.HIGH)
        self.assertEqual(c.name, "SSH disabled")

    def test_status_icon(self):
        c = AuditCheck(status=CheckStatus.FAIL)
        self.assertEqual(c.status_icon, "❌")

    def test_severity_icon(self):
        c = AuditCheck(severity=Severity.CRITICAL)
        self.assertEqual(c.severity_icon, "🔴")

    def test_cvss_bar(self):
        c = AuditCheck(cvss_score=7.5)
        bar = c.cvss_bar
        self.assertIn("█", bar)

    def test_cvss_label(self):
        c = AuditCheck(cvss_score=9.1)
        self.assertEqual(c.cvss_label, "CRITICAL")


class TestOpenPort(unittest.TestCase):
    def test_create(self):
        p = OpenPort(22, "TCP", "open", "SSH", risk_level=Severity.LOW)
        self.assertEqual(p.port, 22)

    def test_display(self):
        p = OpenPort(443, "TCP", service="HTTPS")
        self.assertEqual(p.display, "443/TCP (HTTPS)")

    def test_risk_icon(self):
        p = OpenPort(risk_level=Severity.HIGH)
        self.assertEqual(p.risk_icon, "🟠")


class TestOutdatedPackage(unittest.TestCase):
    def test_create(self):
        p = OutdatedPackage("openssl", "3.2.0", "3.3.1", "security", Severity.CRITICAL)
        self.assertEqual(p.name, "openssl")

    def test_severity_icon(self):
        p = OutdatedPackage(severity=Severity.CRITICAL)
        self.assertEqual(p.severity_icon, "🔴")

    def test_cve_count(self):
        p = OutdatedPackage(cve_ids=["CVE-2024-1234", "CVE-2024-5678"])
        self.assertEqual(p.cve_count, 2)

    def test_cvss_bar(self):
        p = OutdatedPackage(cvss_max=8.0)
        bar = p.cvss_bar
        self.assertIn("█", bar)


class TestUserAudit(unittest.TestCase):
    def test_create(self):
        u = UserAudit("admin", 1000, 1000, "/home/admin", "/bin/bash")
        self.assertEqual(u.username, "admin")

    def test_risk_icon(self):
        u = UserAudit(risk_level=Severity.HIGH)
        self.assertEqual(u.risk_icon, "🟠")

    def test_flags(self):
        u = UserAudit(sudo=True, ssh_keys=2)
        self.assertIn("sudo", u.flags)
        self.assertIn("ssh(2)", u.flags)


class TestKernelCheck(unittest.TestCase):
    def test_create(self):
        k = KernelCheck("kernel.randomize_va_space", "2", "2", CheckStatus.PASS)
        self.assertEqual(k.parameter, "kernel.randomize_va_space")

    def test_status_icon(self):
        k = KernelCheck(status=CheckStatus.WARN)
        self.assertEqual(k.status_icon, "⚠️")


class TestScanResult(unittest.TestCase):
    def test_create(self):
        s = ScanResult(1, time.time(), ScanProfile.STANDARD, 45.0, 100, 90, 5, 5, 0, 90.0)
        self.assertEqual(s.scan_id, 1)

    def test_score_bar(self):
        s = ScanResult(score=80.0)
        bar = s.score_bar
        self.assertIn("█", bar)

    def test_score_grade(self):
        s = ScanResult(score=92.0)
        self.assertEqual(s.score_grade, "A")

    def test_summary(self):
        s = ScanResult(passed=90, failed=5, warnings=5)
        s_sum = s.summary
        self.assertIn("✅90", s_sum)
        self.assertIn("❌5", s_sum)


class TestServiceAudit(unittest.TestCase):
    def test_create(self):
        s = ServiceAudit("sshd", True, True, CheckStatus.PASS, Severity.LOW)
        self.assertEqual(s.name, "sshd")

    def test_state_icon_running(self):
        s = ServiceAudit(running=True)
        self.assertEqual(s.state_icon, "🟢")


class TestSecurityAudit(unittest.TestCase):
    def setUp(self):
        self.audit = SecurityAudit()

    def test_initial_state(self):
        self.assertGreater(len(self.audit.checks), 0)
        self.assertGreater(len(self.audit.open_ports), 0)
        self.assertGreater(len(self.audit.outdated_packages), 0)

    def test_selected_check(self):
        c = self.audit.selected_check
        self.assertIsNotNone(c)

    def test_select_check(self):
        self.audit.select_check(2)
        self.assertEqual(self.audit._selected_check, 2)

    def test_run_scan(self):
        result = self.audit.run_scan(ScanProfile.STANDARD)
        self.assertIsNotNone(result)
        self.assertGreater(result.total_checks, 0)

    def test_run_full_scan(self):
        result = self.audit.run_scan(ScanProfile.FULL)
        self.assertIsNotNone(result)

    def test_dismiss_check(self):
        result = self.audit.dismiss_check(0)
        self.assertTrue(result)
        self.assertEqual(self.audit.checks[0].status, CheckStatus.SKIP)

    def test_get_failed_checks(self):
        failed = self.audit.get_failed_checks()
        self.assertGreater(len(failed), 0)

    def test_get_warnings(self):
        warns = self.audit.get_warnings()
        self.assertGreater(len(warns), 0)

    def test_get_critical_ports(self):
        ports = self.audit.get_critical_ports()
        self.assertGreater(len(ports), 0)

    def test_get_critical_cves(self):
        cves = self.audit.get_critical_cves()
        self.assertGreater(len(cves), 0)

    def test_get_high_cves(self):
        cves = self.audit.get_high_cves()
        self.assertGreater(len(cves), 0)

    def test_overall_score(self):
        score = self.audit.get_overall_score()
        self.assertGreater(score, 0)

    def test_navigation(self):
        self.audit.set_view("checks")
        self.audit.select_down()
        self.assertEqual(self.audit._selected_check, 1)
        self.audit.select_up()
        self.assertEqual(self.audit._selected_check, 0)

    def test_search_checks(self):
        results = self.audit.search_checks("ssh")
        self.assertGreater(len(results), 0)

    def test_search_ports(self):
        results = self.audit.search_ports("ssh")
        self.assertGreater(len(results), 0)

    def test_stats(self):
        stats = self.audit.get_stats()
        self.assertIn("total_checks", stats)
        self.assertIn("overall_score", stats)
        self.assertIn("open_ports", stats)

    def test_scan_history(self):
        self.assertGreater(len(self.audit.scan_history), 0)


if __name__ == "__main__":
    unittest.main()
