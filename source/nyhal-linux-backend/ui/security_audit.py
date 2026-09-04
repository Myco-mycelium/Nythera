"""
Nyrqis OS - Security Audit Tool
System security scanning, misconfiguration detection, and compliance checks.

Features:
- System security scan with configurable profiles
- Misconfiguration detection (SSH, firewall, permissions, services)
- Outdated package detection with CVE tracking
- Open port scanning and analysis
- User and permission audits
- Kernel parameter security checks
- File integrity monitoring
- Compliance scoring (CIS benchmark style)
- Scan history and trend tracking
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class Severity(Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CheckStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"
    ERROR = "error"


class AuditCategory(Enum):
    SSH = "SSH Hardening"
    FIREWALL = "Firewall"
    KERNEL = "Kernel Parameters"
    PACKAGES = "Package Security"
    USERS = "User Security"
    FILES = "File Permissions"
    SERVICES = "Service Security"
    NETWORK = "Network Security"
    UPDATES = "System Updates"
    ENCRYPTION = "Encryption"


class ScanProfile(Enum):
    QUICK = "quick"
    STANDARD = "standard"
    FULL = "full"
    PCI_DSS = "PCI-DSS"
    HIPAA = "HIPAA"
    STIG = "STIG"


CATEGORY_ICONS = {
    AuditCategory.SSH: "🔑", AuditCategory.FIREWALL: "🧱",
    AuditCategory.KERNEL: "⚙️", AuditCategory.PACKAGES: "📦",
    AuditCategory.USERS: "👤", AuditCategory.FILES: "📁",
    AuditCategory.SERVICES: "🔧", AuditCategory.NETWORK: "🌐",
    AuditCategory.UPDATES: "🔄", AuditCategory.ENCRYPTION: "🔐",
}

SEVERITY_ICONS = {
    Severity.INFO: "ℹ️", Severity.LOW: "🔵",
    Severity.MEDIUM: "🟡", Severity.HIGH: "🟠",
    Severity.CRITICAL: "🔴",
}

STATUS_ICONS = {
    CheckStatus.PASS: "✅", CheckStatus.FAIL: "❌",
    CheckStatus.WARN: "⚠️", CheckStatus.SKIP: "⏭️",
    CheckStatus.ERROR: "💥",
}


@dataclass
class AuditCheck:
    name: str = ""
    category: AuditCategory = AuditCategory.KERNEL
    status: CheckStatus = CheckStatus.PASS
    severity: Severity = Severity.INFO
    description: str = ""
    details: str = ""
    remediation: str = ""
    cve_id: str = ""
    cvss_score: float = 0.0
    compliance_id: str = ""  # e.g., CIS 1.1.1
    timestamp: float = 0.0
    duration_ms: int = 0

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def severity_icon(self) -> str:
        return SEVERITY_ICONS.get(self.severity, "❓")

    @property
    def category_icon(self) -> str:
        return CATEGORY_ICONS.get(self.category, "❓")

    @property
    def cvss_bar(self) -> str:
        if self.cvss_score == 0:
            return ""
        filled = int(self.cvss_score * 2)
        return "█" * filled + "░" * (20 - filled)

    @property
    def cvss_label(self) -> str:
        s = self.cvss_score
        if s >= 9.0:
            return "CRITICAL"
        elif s >= 7.0:
            return "HIGH"
        elif s >= 4.0:
            return "MEDIUM"
        elif s > 0:
            return "LOW"
        return "N/A"


@dataclass
class OpenPort:
    port: int = 0
    protocol: str = "TCP"
    state: str = "open"
    service: str = ""
    version: str = ""
    process: str = ""
    pid: int = 0
    bind_address: str = "0.0.0.0"
    risk_level: Severity = Severity.INFO
    description: str = ""

    @property
    def risk_icon(self) -> str:
        return SEVERITY_ICONS.get(self.risk_level, "❓")

    @property
    def display(self) -> str:
        return f"{self.port}/{self.protocol} ({self.service})"

    @property
    def bind_display(self) -> str:
        return self.bind_address


@dataclass
class OutdatedPackage:
    name: str = ""
    current_version: str = ""
    available_version: str = ""
    category: str = ""
    severity: Severity = Severity.LOW
    cve_ids: List[str] = field(default_factory=list)
    cvss_max: float = 0.0
    description: str = ""
    size_mb: float = 0.0

    @property
    def severity_icon(self) -> str:
        return SEVERITY_ICONS.get(self.severity, "❓")

    @property
    def cve_count(self) -> int:
        return len(self.cve_ids)

    @property
    def cve_display(self) -> str:
        if not self.cve_ids:
            return "None"
        return ", ".join(self.cve_ids[:3])

    @property
    def cvss_bar(self) -> str:
        if self.cvss_max == 0:
            return ""
        filled = int(self.cvss_max * 2)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class UserAudit:
    username: str = ""
    uid: int = 0
    gid: int = 0
    home: str = ""
    shell: str = ""
    has_password: bool = True
    password_locked: bool = False
    sudo: bool = False
    last_login: float = 0.0
    failed_logins: int = 0
    groups: List[str] = field(default_factory=list)
    ssh_keys: int = 0
    risk_level: Severity = Severity.INFO

    @property
    def risk_icon(self) -> str:
        return SEVERITY_ICONS.get(self.risk_level, "❓")

    @property
    def last_login_str(self) -> str:
        if self.last_login == 0:
            return "Never"
        delta = time.time() - self.last_login
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h ago"
        return f"{delta / 86400:.0f}d ago"

    @property
    def groups_str(self) -> str:
        return ", ".join(self.groups) if self.groups else "—"

    @property
    def flags(self) -> str:
        parts = []
        if self.sudo:
            parts.append(" sudo")
        if self.ssh_keys > 0:
            parts.append(f" ssh({self.ssh_keys})")
        if self.password_locked:
            parts.append(" locked")
        return " ".join(parts) if parts else ""


@dataclass
class KernelCheck:
    parameter: str = ""
    current_value: str = ""
    recommended_value: str = ""
    status: CheckStatus = CheckStatus.PASS
    description: str = ""

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")


@dataclass
class FilePermIssue:
    path: str = ""
    current_perm: str = ""
    expected_perm: str = ""
    owner: str = ""
    severity: Severity = Severity.LOW
    description: str = ""

    @property
    def severity_icon(self) -> str:
        return SEVERITY_ICONS.get(self.severity, "❓")


@dataclass
class ScanResult:
    scan_id: int = 0
    timestamp: float = 0.0
    profile: ScanProfile = ScanProfile.STANDARD
    duration_s: float = 0.0
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    skipped: int = 0
    score: float = 0.0  # 0-100
    checks: List[AuditCheck] = field(default_factory=list)

    @property
    def score_bar(self) -> str:
        filled = int(self.score / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def score_grade(self) -> str:
        s = self.score
        if s >= 95:
            return "A+"
        elif s >= 90:
            return "A"
        elif s >= 80:
            return "B"
        elif s >= 70:
            return "C"
        elif s >= 60:
            return "D"
        return "F"

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    @property
    def duration_str(self) -> str:
        return f"{self.duration_s:.1f}s"

    @property
    def summary(self) -> str:
        return f"✅{self.passed} ❌{self.failed} ⚠️{self.warnings}"


@dataclass
class ServiceAudit:
    name: str = ""
    enabled: bool = True
    running: bool = True
    status: CheckStatus = CheckStatus.PASS
    security_risk: Severity = Severity.INFO
    description: str = ""
    ports: List[int] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def risk_icon(self) -> str:
        return SEVERITY_ICONS.get(self.security_risk, "❓")

    @property
    def state_icon(self) -> str:
        if self.running:
            return "🟢"
        elif self.enabled:
            return "🟡"
        return "⚫"


class SecurityAudit:
    def __init__(self):
        self.checks: List[AuditCheck] = []
        self.open_ports: List[OpenPort] = []
        self.outdated_packages: List[OutdatedPackage] = []
        self.users: List[UserAudit] = []
        self.kernel_checks: List[KernelCheck] = []
        self.file_issues: List[FilePermIssue] = []
        self.services: List[ServiceAudit] = []
        self.scan_history: List[ScanResult] = []
        self._selected_check: int = 0
        self._selected_port: int = 0
        self._view_mode: str = "overview"
        self._scan_counter: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.checks = [
            AuditCheck("SSH root login disabled", AuditCategory.SSH, CheckStatus.PASS,
                       Severity.HIGH, "Root login via SSH is disabled",
                       "PermitRootLogin no", remediation="Set PermitRootLogin no in /etc/ssh/sshd_config",
                       compliance_id="CIS 5.2.10"),
            AuditCheck("SSH password auth disabled", AuditCategory.SSH, CheckStatus.PASS,
                       Severity.MEDIUM, "Password authentication disabled for SSH",
                       "PasswordAuthentication no", compliance_id="CIS 5.2.11"),
            AuditCheck("SSH protocol 2 only", AuditCategory.SSH, CheckStatus.PASS,
                       Severity.HIGH, "Only SSH protocol 2 is enabled",
                       "Protocol 2", compliance_id="CIS 5.2.4"),
            AuditCheck("SSH X11 forwarding disabled", AuditCategory.SSH, CheckStatus.FAIL,
                       Severity.MEDIUM, "X11 forwarding is enabled (risk of keylogging)",
                       "X11Forwarding yes", remediation="Set X11Forwarding no",
                       compliance_id="CIS 5.2.6"),
            AuditCheck("Firewall enabled", AuditCategory.FIREWALL, CheckStatus.PASS,
                       Severity.CRITICAL, "UFW firewall is active",
                       "Status: active"),
            AuditCheck("Default deny incoming", AuditCategory.FIREWALL, CheckStatus.PASS,
                       Severity.HIGH, "Default incoming policy is deny",
                       "Default incoming: deny"),
            AuditCheck("Default deny outgoing", AuditCategory.FIREWALL, CheckStatus.WARN,
                       Severity.MEDIUM, "Default outgoing policy is allow (should be deny for high security)",
                       "Default outgoing: allow"),
            AuditCheck("Uncomplicated Firewall logging", AuditCategory.FIREWALL, CheckStatus.PASS,
                       Severity.MEDIUM, "Firewall logging is enabled",
                       "Logging: on"),
            AuditCheck("ASLR enabled", AuditCategory.KERNEL, CheckStatus.PASS,
                       Severity.HIGH, "Address Space Layout Randomization is enabled",
                       "kernel.randomize_va_space = 2"),
            AuditCheck("IP forwarding disabled", AuditCategory.KERNEL, CheckStatus.PASS,
                       Severity.MEDIUM, "IP forwarding is disabled",
                       "net.ipv4.ip_forward = 0"),
            AuditCheck("SYN cookies enabled", AuditCategory.KERNEL, CheckStatus.PASS,
                       Severity.MEDIUM, "SYN flood protection is enabled",
                       "net.ipv4.tcp_syncookies = 1"),
            AuditCheck("Core dumps restricted", AuditCategory.KERNEL, CheckStatus.WARN,
                       Severity.MEDIUM, "Core dumps may be enabled for some processes",
                       "fs.suid_dumpable = 1", remediation="Set fs.suid_dumpable = 0"),
            AuditCheck("Automatic security updates", AuditCategory.UPDATES, CheckStatus.PASS,
                       Severity.HIGH, "Unattended-upgrades is configured",
                       "Enabled in /etc/apt/apt.conf.d/20auto-upgrades"),
            AuditCheck("Pending security updates", AuditCategory.UPDATES, CheckStatus.WARN,
                       Severity.HIGH, "12 security updates available",
                       "12 packages can be upgraded for security"),
            AuditCheck("Disk encryption active", AuditCategory.ENCRYPTION, CheckStatus.PASS,
                       Severity.CRITICAL, "LUKS full disk encryption is active",
                       "/dev/nvme0n1p3: LUKS"),
            AuditCheck("HTTPS enforced", AuditCategory.NETWORK, CheckStatus.PASS,
                       Severity.HIGH, "All web services use HTTPS",
                       "443/tcp is the only web port open"),
            AuditCheck("DNSSEC enabled", AuditCategory.NETWORK, CheckStatus.PASS,
                       Severity.MEDIUM, "DNSSEC validation is enabled",
                       "DNSSEC=yes in resolved.conf"),
            AuditCheck("Excessive SUID binaries", AuditCategory.FILES, CheckStatus.WARN,
                       Severity.MEDIUM, "8 SUID binaries found (recommended: ≤5)",
                       "8 SUID binaries in /usr/bin and /usr/sbin"),
            AuditCheck("World-writable files", AuditCategory.FILES, CheckStatus.FAIL,
                       Severity.HIGH, "3 world-writable files in /etc",
                       "/etc/crontab, /etc/hosts, /etc/environment are world-writable",
                       remediation="chmod o-w on affected files"),
            AuditCheck("Password policy", AuditCategory.USERS, CheckStatus.PASS,
                       Severity.MEDIUM, "Password aging policy is configured",
                       "PASS_MAX_DAYS=90, PASS_MIN_DAYS=7"),
            AuditCheck("Empty password accounts", AuditCategory.USERS, CheckStatus.PASS,
                       Severity.CRITICAL, "No accounts with empty passwords",
                       "0 accounts with empty passwords"),
            AuditCheck("Root account locked for direct login", AuditCategory.USERS, CheckStatus.PASS,
                       Severity.HIGH, "Root direct login is disabled",
                       "passwd -l root"),
            AuditCheck("Pending kernel update", AuditCategory.PACKAGES, CheckStatus.WARN,
                       Severity.HIGH, "Kernel update available: 6.10.6",
                       "Current: 6.10.5-nyrqis, Available: 6.10.6-nyrqis"),
        ]

        self.open_ports = [
            OpenPort(22, "TCP", "open", "SSH", "OpenSSH 9.7", "sshd", 1234,
                     "127.0.0.1", Severity.LOW, "SSH on localhost only"),
            OpenPort(80, "TCP", "open", "HTTP", "nginx 1.26", "nginx", 5678,
                     "0.0.0.0", Severity.MEDIUM, "Web server exposed"),
            OpenPort(443, "TCP", "open", "HTTPS", "nginx 1.26", "nginx", 5678,
                     "0.0.0.0", Severity.LOW, "HTTPS web server"),
            OpenPort(3000, "TCP", "open", "HTTP", "code-server", "code-server", 1234,
                     "127.0.0.1", Severity.LOW, "Code server on localhost"),
            OpenPort(5432, "TCP", "open", "PostgreSQL", "PostgreSQL 16.4", "postgres", 2345,
                     "127.0.0.1", Severity.MEDIUM, "Database on localhost"),
            OpenPort(6379, "TCP", "open", "Redis", "Redis 7.2", "redis-server", 3456,
                     "127.0.0.1", Severity.MEDIUM, "Redis on localhost"),
            OpenPort(8080, "TCP", "open", "HTTP", "code-server", "node", 7890,
                     "0.0.0.0", Severity.HIGH, "code-server exposed externally"),
            OpenPort(22000, "TCP", "open", "Syncthing", "Syncthing 1.27", "syncthing", 4567,
                     "0.0.0.0", Severity.LOW, "Syncthing sync port"),
            OpenPort(51820, "UDP", "open", "WireGuard", "WireGuard", "wireguard", 0,
                     "0.0.0.0", Severity.LOW, "WireGuard VPN"),
            OpenPort(5353, "UDP", "open", "mDNS", "Avahi", "avahi-daemon", 5678,
                     "0.0.0.0", Severity.INFO, "mDNS service discovery"),
        ]

        self.outdated_packages = [
            OutdatedPackage("openssl", "3.2.0", "3.3.1", "security",
                            Severity.CRITICAL, ["CVE-2024-5535", "CVE-2024-4741"], 9.1,
                            "OpenSSL security update", 3.2),
            OutdatedPackage("linux-image", "6.10.5-nyrqis", "6.10.6-nyrqis", "kernel",
                            Severity.HIGH, ["CVE-2024-41009"], 7.5,
                            "Kernel security update", 120.0),
            OutdatedPackage("firefox", "129.0", "130.0", "browser",
                            Severity.MEDIUM, ["CVE-2024-8383"], 5.0,
                            "Browser security update", 85.0),
            OutdatedPackage("libcurl4", "8.7.1", "8.9.1", "library",
                            Severity.MEDIUM, ["CVE-2024-7264"], 6.5,
                            "cURL security fix", 1.2),
            OutdatedPackage("sudo", "1.9.15", "1.9.16", "security",
                            Severity.HIGH, ["CVE-2024-2879"], 7.8,
                            "Sudo privilege escalation fix", 2.1),
            OutdatedPackage("systemd", "255.4", "255.5", "system",
                            Severity.LOW, [], 3.0,
                            "Systemd bug fixes", 8.5),
            OutdatedPackage("glibc", "2.38", "2.39", "library",
                            Severity.MEDIUM, ["CVE-2024-2961"], 8.0,
                            "glibc security update", 12.0),
            OutdatedPackage("nss", "3.101", "3.102", "security",
                            Severity.LOW, [], 2.5,
                            "NSS library update", 4.5),
        ]

        self.users = [
            UserAudit("root", 0, 0, "/root", "/bin/bash", True, True, False,
                      0, 0, ["root"], 0, Severity.INFO),
            UserAudit("admin", 1000, 1000, "/home/admin", "/bin/bash", True, False, True,
                      now - 3600, 0, ["admin", "sudo", "docker"], 2, Severity.INFO),
            UserAudit("nyrqis", 1001, 1001, "/home/nyrqis", "/bin/bash", True, False, False,
                      now - 7200, 0, ["nyrqis", "audio", "video"], 1, Severity.INFO),
            UserAudit("www-data", 33, 33, "/var/www", "/usr/sbin/nologin", True, True, False,
                      0, 0, ["www-data"], 0, Severity.INFO),
            UserAudit("nobody", 65534, 65534, "/nonexistent", "/usr/sbin/nologin", False, True, False,
                      0, 0, ["nogroup"], 0, Severity.INFO),
        ]

        self.kernel_checks = [
            KernelCheck("kernel.randomize_va_space", "2", "2", CheckStatus.PASS, "ASLR enabled"),
            KernelCheck("net.ipv4.ip_forward", "0", "0", CheckStatus.PASS, "IP forwarding disabled"),
            KernelCheck("net.ipv4.tcp_syncookies", "1", "1", CheckStatus.PASS, "SYN cookies enabled"),
            KernelCheck("net.ipv4.conf.all.accept_redirects", "0", "0", CheckStatus.PASS, "ICMP redirects rejected"),
            KernelCheck("net.ipv4.conf.all.accept_source_route", "0", "0", CheckStatus.PASS, "Source routing disabled"),
            KernelCheck("fs.suid_dumpable", "1", "0", CheckStatus.WARN, "Core dumps may leak suid data"),
            KernelCheck("kernel.dmesg_restrict", "1", "1", CheckStatus.PASS, "dmesg restricted to root"),
            KernelCheck("kernel.kptr_restrict", "2", "2", CheckStatus.PASS, "Kernel pointers hidden"),
        ]

        self.file_issues = [
            FilePermIssue("/etc/crontab", "0644", "0600", "root:root", Severity.MEDIUM,
                          "Crontab is readable by all users"),
            FilePermIssue("/etc/hosts", "0644", "0644", "root:root", Severity.INFO,
                          "Hosts file has correct permissions"),
            FilePermIssue("/etc/shadow", "0640", "0640", "root:shadow", Severity.INFO,
                          "Shadow file has correct permissions"),
        ]

        self.services = [
            ServiceAudit("sshd", True, True, CheckStatus.PASS, Severity.LOW,
                         "SSH daemon", [22]),
            ServiceAudit("nginx", True, True, CheckStatus.PASS, Severity.MEDIUM,
                         "Web server", [80, 443]),
            ServiceAudit("postgresql", True, True, CheckStatus.PASS, Severity.MEDIUM,
                         "PostgreSQL database", [5432]),
            ServiceAudit("redis-server", True, True, CheckStatus.PASS, Severity.MEDIUM,
                         "Redis cache", [6379]),
            ServiceAudit("ufw", True, True, CheckStatus.PASS, Severity.LOW,
                         "Uncomplicated Firewall"),
            ServiceAudit("unattended-upgrades", True, True, CheckStatus.PASS, Severity.LOW,
                         "Automatic security updates"),
            ServiceAudit("bluetooth", True, False, CheckStatus.PASS, Severity.LOW,
                         "Bluetooth service (disabled)"),
            ServiceAudit("avahi-daemon", True, True, CheckStatus.WARN, Severity.LOW,
                         "mDNS/DNS-SD service discovery", [5353]),
        ]

        self.scan_history = [
            ScanResult(1, now - 86400 * 7, ScanProfile.STANDARD, 45.2,
                       120, 105, 10, 5, 0, 87.5),
            ScanResult(2, now - 86400 * 3, ScanProfile.STANDARD, 42.8,
                       120, 108, 8, 4, 0, 90.0),
            ScanResult(3, now - 86400, ScanProfile.FULL, 185.3,
                       250, 225, 18, 7, 0, 90.0),
            ScanResult(4, now - 3600, ScanProfile.QUICK, 12.5,
                       50, 42, 5, 3, 0, 84.0),
        ]
        self._scan_counter = 5

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_check(self) -> Optional[AuditCheck]:
        if 0 <= self._selected_check < len(self.checks):
            return self.checks[self._selected_check]
        return None

    def select_check(self, idx: int):
        if 0 <= idx < len(self.checks):
            self._selected_check = idx

    def select_port(self, idx: int):
        if 0 <= idx < len(self.open_ports):
            self._selected_port = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        if self._view_mode == "checks":
            self._selected_check = min(self._selected_check + 1, len(self.checks) - 1)
        elif self._view_mode == "ports":
            self._selected_port = min(self._selected_port + 1, len(self.open_ports) - 1)

    def select_up(self):
        if self._view_mode == "checks":
            self._selected_check = max(self._selected_check - 1, 0)
        elif self._view_mode == "ports":
            self._selected_port = max(self._selected_port - 1, 0)

    # ─── Scan Actions ──────────────────────────────────────────────────

    def run_scan(self, profile: ScanProfile = ScanProfile.STANDARD) -> ScanResult:
        now = time.time()
        self._scan_counter += 1
        passed = sum(1 for c in self.checks if c.status == CheckStatus.PASS)
        failed = sum(1 for c in self.checks if c.status == CheckStatus.FAIL)
        warns = sum(1 for c in self.checks if c.status == CheckStatus.WARN)
        total = len(self.checks)
        score = (passed / total * 100) if total > 0 else 0

        result = ScanResult(
            self._scan_counter, now, profile, random.uniform(10, 60),
            total, passed, failed, warns, 0, score,
            list(self.checks),
        )
        self.scan_history.insert(0, result)
        return result

    def dismiss_check(self, idx: int) -> bool:
        if 0 <= idx < len(self.checks):
            self.checks[idx].status = CheckStatus.SKIP
            return True
        return False

    # ─── Queries ───────────────────────────────────────────────────────

    def get_failed_checks(self) -> List[AuditCheck]:
        return [c for c in self.checks if c.status == CheckStatus.FAIL]

    def get_warnings(self) -> List[AuditCheck]:
        return [c for c in self.checks if c.status == CheckStatus.WARN]

    def get_critical_ports(self) -> List[OpenPort]:
        return [p for p in self.open_ports if p.risk_level in (Severity.HIGH, Severity.CRITICAL)]

    def get_critical_cves(self) -> List[OutdatedPackage]:
        return [p for p in self.outdated_packages if p.severity == Severity.CRITICAL]

    def get_high_cves(self) -> List[OutdatedPackage]:
        return [p for p in self.outdated_packages if p.severity == Severity.HIGH]

    def search_checks(self, query: str) -> List[AuditCheck]:
        q = query.lower()
        return [c for c in self.checks if q in c.name.lower() or q in c.description.lower()]

    def search_ports(self, query: str) -> List[OpenPort]:
        q = query.lower()
        return [p for p in self.open_ports if q in p.service.lower() or q in str(p.port)]

    def get_overall_score(self) -> float:
        if not self.checks:
            return 0.0
        passed = sum(1 for c in self.checks if c.status == CheckStatus.PASS)
        return (passed / len(self.checks)) * 100

    def get_stats(self) -> Dict:
        return {
            "total_checks": len(self.checks),
            "passed": sum(1 for c in self.checks if c.status == CheckStatus.PASS),
            "failed": sum(1 for c in self.checks if c.status == CheckStatus.FAIL),
            "warnings": sum(1 for c in self.checks if c.status == CheckStatus.WARN),
            "overall_score": round(self.get_overall_score(), 1),
            "open_ports": len(self.open_ports),
            "critical_ports": len(self.get_critical_ports()),
            "outdated_packages": len(self.outdated_packages),
            "critical_cves": len(self.get_critical_cves()),
            "users": len(self.users),
            "services": len(self.services),
            "scan_history": len(self.scan_history),
        }
