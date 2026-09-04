"""
Nyrqis OS - File Integrity Checker
Hash verification, file monitoring, and change detection.
"""

import hashlib
import time
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class HashAlgorithm(Enum):
    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"
    SHA512 = "sha512"
    BLAKE2 = "blake2"


class FileStatus(Enum):
    OK = "ok"
    MODIFIED = "modified"
    DELETED = "deleted"
    NEW = "new"
    PERMISSION_CHANGED = "permission_changed"


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class FileRecord:
    path: str
    size: int = 0
    permissions: str = "0644"
    owner: str = "root"
    group: str = "root"
    hashes: Dict[str, str] = field(default_factory=dict)
    first_seen: float = 0.0
    last_modified: float = 0.0
    last_verified: float = 0.0
    status: FileStatus = FileStatus.OK
    checksum_verified: bool = False

    @property
    def status_icon(self) -> str:
        icons = {
            FileStatus.OK: "✅",
            FileStatus.MODIFIED: "⚠️",
            FileStatus.DELETED: "❌",
            FileStatus.NEW: "🆕",
            FileStatus.PERMISSION_CHANGED: "🔒",
        }
        return icons.get(self.status, "?")


@dataclass
class Alert:
    timestamp: float
    file_path: str
    message: str
    severity: AlertSeverity = AlertSeverity.INFO
    old_hash: str = ""
    new_hash: str = ""
    acknowledged: bool = False

    @property
    def severity_icon(self) -> str:
        icons = {
            AlertSeverity.INFO: "ℹ️",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.CRITICAL: "🚨",
        }
        return icons.get(self.severity, "?")


@dataclass
class MonitorRule:
    name: str
    paths: List[str] = field(default_factory=list)
    algorithm: HashAlgorithm = HashAlgorithm.SHA256
    recursive: bool = False
    enabled: bool = True
    alert_on_change: bool = True
    exclude_patterns: List[str] = field(default_factory=list)
    last_scan: float = 0.0
    files_monitored: int = 0


@dataclass
class ScanResult:
    timestamp: float
    duration_s: float = 0.0
    files_scanned: int = 0
    files_ok: int = 0
    files_modified: int = 0
    files_new: int = 0
    files_deleted: int = 0
    alerts_generated: int = 0

    @property
    def status(self) -> str:
        if self.files_modified == 0 and self.files_deleted == 0:
            return "✅ All Clear"
        return "⚠️ Changes Detected"


class FileIntegrityChecker:
    def __init__(self):
        self.records: Dict[str, FileRecord] = {}
        self.alerts: List[Alert] = []
        self.rules: List[MonitorRule] = []
        self.scan_history: List[ScanResult] = []
        self.current_algorithm: HashAlgorithm = HashAlgorithm.SHA256
        self.monitoring_active: bool = False
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        sample_files = [
            ("/etc/passwd", 2048, "0644", "root", "root",
             {HashAlgorithm.SHA256.value: hashlib.sha256(b"passwd").hexdigest()}),
            ("/etc/shadow", 1024, "0640", "root", "shadow",
             {HashAlgorithm.SHA256.value: hashlib.sha256(b"shadow").hexdigest()}),
            ("/etc/ssh/sshd_config", 4096, "0600", "root", "root",
             {HashAlgorithm.SHA256.value: hashlib.sha256(b"sshd").hexdigest()}),
            ("/boot/grub/grub.cfg", 8192, "0644", "root", "root",
             {HashAlgorithm.SHA256.value: hashlib.sha256(b"grub").hexdigest()}),
            ("/etc/hosts", 512, "0644", "root", "root",
             {HashAlgorithm.SHA256.value: hashlib.sha256(b"hosts").hexdigest()}),
            ("/usr/bin/nyrqis-shell", 1048576, "0755", "root", "root",
             {HashAlgorithm.SHA256.value: hashlib.sha256(b"shell").hexdigest()}),
            ("/etc/resolv.conf", 256, "0644", "root", "root",
             {HashAlgorithm.SHA256.value: hashlib.sha256(b"resolv").hexdigest()}),
            ("/var/log/auth.log", 65536, "0640", "root", "adm",
             {HashAlgorithm.SHA256.value: hashlib.sha256(b"auth").hexdigest()}),
        ]
        for path, size, perms, owner, group, hashes in sample_files:
            self.records[path] = FileRecord(
                path=path, size=size, permissions=perms, owner=owner,
                group=group, hashes=hashes,
                first_seen=now - 86400 * random.randint(1, 30),
                last_modified=now - random.randint(0, 86400),
                last_verified=now - random.randint(0, 3600),
                status=random.choice([FileStatus.OK, FileStatus.OK, FileStatus.OK, FileStatus.MODIFIED]),
            )
        self.records["/etc/passwd"].status = FileStatus.MODIFIED
        self.records["/var/log/auth.log"].status = FileStatus.MODIFIED

        self.rules = [
            MonitorRule(name="System Config", paths=["/etc"], algorithm=HashAlgorithm.SHA256,
                        recursive=True, files_monitored=5),
            MonitorRule(name="Binaries", paths=["/usr/bin", "/usr/sbin"], algorithm=HashAlgorithm.SHA512,
                        recursive=False, files_monitored=1),
            MonitorRule(name="Boot", paths=["/boot"], algorithm=HashAlgorithm.SHA256,
                        recursive=True, files_monitored=1),
            MonitorRule(name="SSH Keys", paths=["/etc/ssh"], algorithm=HashAlgorithm.BLAKE2,
                        recursive=False, files_monitored=1, alert_on_change=True),
        ]

        self.alerts = [
            Alert(timestamp=now - 300, file_path="/etc/passwd",
                  message="File hash changed: /etc/passwd",
                  severity=AlertSeverity.CRITICAL,
                  old_hash="abc123...", new_hash="def456..."),
            Alert(timestamp=now - 600, file_path="/var/log/auth.log",
                  message="File modified: /var/log/auth.log",
                  severity=AlertSeverity.WARNING),
            Alert(timestamp=now - 1800, file_path="/etc/hosts",
                  message="New file detected: /etc/hosts",
                  severity=AlertSeverity.INFO),
        ]

    def compute_hash(self, data: bytes, algorithm: HashAlgorithm = HashAlgorithm.SHA256) -> str:
        h = hashlib.new(algorithm.value)
        h.update(data)
        return h.hexdigest()

    def verify_file(self, path: str, data: bytes) -> Dict:
        record = self.records.get(path)
        if not record:
            return {"status": "unknown", "message": "File not tracked"}
        expected = record.hashes.get(self.current_algorithm.value, "")
        actual = self.compute_hash(data, self.current_algorithm)
        record.last_verified = time.time()
        if expected == actual:
            record.checksum_verified = True
            return {"status": "ok", "hash": actual}
        record.status = FileStatus.MODIFIED
        record.checksum_verified = False
        self.alerts.append(Alert(
            timestamp=time.time(), file_path=path,
            message=f"Hash mismatch: {path}",
            severity=AlertSeverity.CRITICAL,
            old_hash=expected, new_hash=actual,
        ))
        return {"status": "mismatch", "expected": expected, "actual": actual}

    def run_scan(self, rule_name: str = "") -> ScanResult:
        start = time.time()
        result = ScanResult(timestamp=start)
        rules_to_scan = [r for r in self.rules if r.enabled]
        if rule_name:
            rules_to_scan = [r for r in rules_to_scan if r.name == rule_name]

        for rule in rules_to_scan:
            rule.last_scan = time.time()
            result.files_scanned += rule.files_monitored

        for path, record in self.records.items():
            result.files_scanned += 1
            if record.status == FileStatus.OK:
                result.files_ok += 1
            elif record.status == FileStatus.MODIFIED:
                result.files_modified += 1
                self.alerts.append(Alert(
                    timestamp=time.time(), file_path=path,
                    message=f"Modified: {path}",
                    severity=AlertSeverity.WARNING,
                ))
            elif record.status == FileStatus.NEW:
                result.files_new += 1
            elif record.status == FileStatus.DELETED:
                result.files_deleted += 1

        result.duration_s = time.time() - start
        result.alerts_generated = len(self.alerts)
        self.scan_history.append(result)
        return result

    def acknowledge_alert(self, index: int) -> bool:
        if 0 <= index < len(self.alerts):
            self.alerts[index].acknowledged = True
            return True
        return False

    def add_monitor_rule(self, name: str, paths: List[str], **kwargs) -> MonitorRule:
        rule = MonitorRule(name=name, paths=paths, **kwargs)
        self.rules.append(rule)
        return rule

    def get_summary(self) -> Dict:
        total = len(self.records)
        ok = sum(1 for r in self.records.values() if r.status == FileStatus.OK)
        modified = sum(1 for r in self.records.values() if r.status == FileStatus.MODIFIED)
        unack = sum(1 for a in self.alerts if not a.acknowledged)
        return {
            "total_files": total,
            "ok": ok,
            "modified": modified,
            "unack_alerts": unack,
            "rules": len(self.rules),
            "scans": len(self.scan_history),
        }

import random
