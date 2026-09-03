from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class CheckType(Enum):
    PROCESS = "process"
    PORT = "port"
    DISK = "disk"
    MEMORY = "memory"
    CPU = "cpu"
    NETWORK = "network"
    SERVICE = "service"
    FILE = "file"
    SCRIPT = "script"
    HTTP = "http"


class CheckStatus(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"
    DISABLED = "disabled"


class RecoveryAction(Enum):
    RESTART = "restart"
    KILL = "kill"
    ALERT = "alert"
    SCRIPT = "script"
    FAILOVER = "failover"
    REBOOT = "reboot"
    NONE = "none"


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class HealthCheck:
    name: str
    check_type: CheckType
    target: str
    interval_secs: int
    timeout_secs: int
    status: CheckStatus = CheckStatus.UNKNOWN
    last_check: float = 0
    last_value: str = ""
    threshold_warn: float = 0
    threshold_crit: float = 0
    enabled: bool = True
    recovery: RecoveryAction = RecoveryAction.ALERT

    @property
    def status_icon(self) -> str:
        icons = {"ok": "🟢", "warning": "🟡", "critical": "🔴", "unknown": "❓", "disabled": "⚪"}
        return icons.get(self.status.value, "?")

    @property
    def age_display(self) -> str:
        if self.last_check == 0:
            return "never"
        age = int((time.time() - self.last_check) / 60)
        if age < 1:
            return "just now"
        if age < 60:
            return f"{age}m ago"
        return f"{age // 60}h ago"

    @property
    def interval_display(self) -> str:
        if self.interval_secs < 60:
            return f"{self.interval_secs}s"
        return f"{self.interval_secs // 60}m"


@dataclass
class Alert:
    level: AlertLevel
    message: str
    source: str
    timestamp: float
    acknowledged: bool = False
    resolved: bool = False

    @property
    def icon(self) -> str:
        icons = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "critical": "🚨"}
        return icons.get(self.level.value, "?")


@dataclass
class RecoveryLog:
    timestamp: float
    action: RecoveryAction
    check_name: str
    target: str
    success: bool
    details: str = ""

    @property
    def age_display(self) -> str:
        age = int((time.time() - self.timestamp) / 60)
        if age < 60:
            return f"{age}m ago"
        return f"{age // 60}h ago"


@dataclass
class WatchdogConfig:
    global_interval: int = 60
    max_retries: int = 3
    retry_delay: int = 30
    alert_on_ok: bool = False
    auto_recovery: bool = True
    log_retention_days: int = 30
    notification_email: str = ""
    notification_webhook: str = ""


class SystemWatchdog:
    def __init__(self):
        self._checks: list[HealthCheck] = []
        self._selected_check: int = 0
        self._alerts: list[Alert] = []
        self._recovery_log: list[RecoveryLog] = []
        self._config: WatchdogConfig = WatchdogConfig()
        self._is_running: bool = False
        self._view: str = "checks"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._checks = [
            HealthCheck("nyrqis-compositor", CheckType.PROCESS, "nyrqis-compositor", 30, 5, CheckStatus.OK, now - 30, "PID 456, 35.2% CPU", 80, 95, recovery=RecoveryAction.RESTART),
            HealthCheck("firefox", CheckType.PROCESS, "firefox", 30, 5, CheckStatus.OK, now - 30, "PID 789, 28.5% CPU", 80, 95, recovery=RecoveryAction.RESTART),
            HealthCheck("SSH Port", CheckType.PORT, "22", 60, 10, CheckStatus.OK, now - 60, "open", 0, 0, recovery=RecoveryAction.ALERT),
            HealthCheck("HTTP Port", CheckType.PORT, "8080", 60, 10, CheckStatus.WARNING, now - 60, "open (high latency)", 100, 500, recovery=RecoveryAction.RESTART),
            HealthCheck("Root Disk", CheckType.DISK, "/", 300, 30, CheckStatus.OK, now - 300, "44% used (847/1920 GB)", 80, 95, recovery=RecoveryAction.SCRIPT),
            HealthCheck("System Memory", CheckType.MEMORY, "total", 60, 10, CheckStatus.WARNING, now - 60, "60% used (38.7/64 GB)", 75, 90, recovery=RecoveryAction.ALERT),
            HealthCheck("CPU Load", CheckType.CPU, "1min", 30, 5, CheckStatus.OK, now - 30, "Load: 2.45 (15% of 16 cores)", 50, 80, recovery=RecoveryAction.ALERT),
            HealthCheck("Internet", CheckType.NETWORK, "8.8.8.8", 120, 30, CheckStatus.OK, now - 120, "Ping: 11ms, Packet loss: 0%", 50, 100, recovery=RecoveryAction.NONE),
            HealthCheck("Docker", CheckType.SERVICE, "docker", 60, 10, CheckStatus.OK, now - 60, "active (running)", 0, 0, recovery=RecoveryAction.RESTART),
            HealthCheck("PostgreSQL", CheckType.SERVICE, "postgresql", 60, 10, CheckStatus.OK, now - 60, "active (running)", 0, 0, recovery=RecoveryAction.RESTART),
            HealthCheck("Config File", CheckType.FILE, "/etc/nyrqis/compositor.toml", 300, 10, CheckStatus.OK, now - 300, "exists, 245 bytes", 0, 0, recovery=RecoveryAction.ALERT),
            HealthCheck("Backup Script", CheckType.SCRIPT, "/usr/local/bin/backup.sh", 3600, 60, CheckStatus.CRITICAL, now - 3600, "script not found", 0, 0, recovery=RecoveryAction.ALERT),
        ]

        self._alerts = [
            Alert(AlertLevel.WARNING, "HTTP latency above threshold (>100ms)", "HTTP Port", now - 300),
            Alert(AlertLevel.WARNING, "Memory usage at 60% (threshold: 75%)", "System Memory", now - 600),
            Alert(AlertLevel.CRITICAL, "Backup script not found at expected path", "Backup Script", now - 1800),
            Alert(AlertLevel.INFO, "nyrqis-compositor restarted successfully", "Recovery", now - 3600),
        ]

        self._recovery_log = [
            RecoveryLog(now - 3600, RecoveryAction.RESTART, "nyrqis-compositor", "nyrqis-compositor", True, "Process restarted successfully"),
            RecoveryLog(now - 7200, RecoveryAction.ALERT, "HTTP Port", "8080", True, "Alert sent to admin"),
            RecoveryLog(now - 86400, RecoveryAction.SCRIPT, "Root Disk", "/", True, "Cleanup script executed, freed 2.3 GB"),
        ]

    @property
    def selected_check(self) -> Optional[HealthCheck]:
        if 0 <= self._selected_check < len(self._checks):
            return self._checks[self._selected_check]
        return None

    @property
    def total_checks(self) -> int:
        return len(self._checks)

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self._checks if c.status == CheckStatus.OK)

    @property
    def warning_count(self) -> int:
        return sum(1 for c in self._checks if c.status == CheckStatus.WARNING)

    @property
    def critical_count(self) -> int:
        return sum(1 for c in self._checks if c.status == CheckStatus.CRITICAL)

    @property
    def unacked_alerts(self) -> int:
        return sum(1 for a in self._alerts if not a.acknowledged)

    def select_check(self, idx: int):
        if 0 <= idx < len(self._checks):
            self._selected_check = idx

    def toggle_check(self, idx: int):
        if 0 <= idx < len(self._checks):
            self._checks[idx].enabled = not self._checks[idx].enabled
            if not self._checks[idx].enabled:
                self._checks[idx].status = CheckStatus.DISABLED

    def acknowledge_alert(self, idx: int):
        if 0 <= idx < len(self._alerts):
            self._alerts[idx].acknowledged = True

    def add_check(self, check: HealthCheck):
        self._checks.append(check)

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS SYSTEM WATCHDOG                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        status = "🟢 RUNNING" if self._is_running else "⏹ STOPPED"
        lines.append(f"  Status: {status}  Auto-recovery: {'ON' if self._config.auto_recovery else 'OFF'}  Interval: {self._config.global_interval}s")
        lines.append(f"  Checks: {self.total_checks} (🟢{self.ok_count} 🟡{self.warning_count} 🔴{self.critical_count})  Alerts: {self.unacked_alerts} unacknowledged")
        lines.append("")
        lines.append("  ── Health Checks ──")
        for i, c in enumerate(self._checks):
            sel = "▶" if i == self._selected_check else " "
            en = "🟢" if c.enabled else "⚪"
            lines.append(f"  {sel}{en} {c.status_icon} {c.name:<20s} {c.check_type.value:<10s} [{c.interval_display}]  {c.age_display}")
            lines.append(f"    {c.last_value[:50]}  Recovery: {c.recovery.value}")
        lines.append("")
        lines.append("  ── Recent Alerts ──")
        for a in self._alerts[:4]:
            ack = "✅" if a.acknowledged else "  "
            age = int((time.time() - a.timestamp) / 60)
            lines.append(f"  {ack}{a.icon} {a.message[:50]}  {a.source}  {age}m ago")
        lines.append("")
        lines.append("  ── Recovery Log ──")
        for r in self._recovery_log[:3]:
            status = "✅" if r.success else "❌"
            lines.append(f"  {status} {r.action.value} on {r.check_name}  {r.age_display}  {r.details[:30]}")
        lines.append("")
        lines.append("  [S]tart  [T]op  [A]dd check  [D]isable  [R]ecover  [C]onfig  [L]og")
        return lines

    def render_check_detail(self) -> list:
        c = self.selected_check
        if not c:
            return ["  No check selected"]
        lines = []
        lines.append(f"  ── {c.name} ({c.check_type.value}) ──")
        lines.append(f"  Status: {c.status_icon} {c.status.value}")
        lines.append(f"  Target: {c.target}")
        lines.append(f"  Last Value: {c.last_value}")
        lines.append(f"  Interval: {c.interval_display}  Timeout: {c.timeout_secs}s")
        lines.append(f"  Enabled: {'Yes' if c.enabled else 'No'}")
        lines.append(f"  Thresholds: Warning={c.threshold_warn}  Critical={c.threshold_crit}")
        lines.append(f"  Recovery: {c.recovery.value}")
        lines.append(f"  Last Check: {c.age_display}")
        return lines

    def render_config(self) -> list:
        cfg = self._config
        lines = []
        lines.append("  ── Watchdog Configuration ──")
        lines.append("")
        lines.append(f"  Global Interval:     {cfg.global_interval}s")
        lines.append(f"  Max Retries:         {cfg.max_retries}")
        lines.append(f"  Retry Delay:         {cfg.retry_delay}s")
        lines.append(f"  Alert on OK:         {'Yes' if cfg.alert_on_ok else 'No'}")
        lines.append(f"  Auto Recovery:       {'Yes' if cfg.auto_recovery else 'No'}")
        lines.append(f"  Log Retention:       {cfg.log_retention_days} days")
        lines.append(f"  Email Notifications: {cfg.notification_email or 'Not configured'}")
        lines.append(f"  Webhook:             {cfg.notification_webhook or 'Not configured'}")
        return lines
