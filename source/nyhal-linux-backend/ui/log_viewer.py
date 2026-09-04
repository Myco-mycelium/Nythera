"""
Nyrqis OS - System Log Viewer
Multi-file tailing, syntax highlighting, and alert rules.
"""

import time
import random
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class LogLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    ALERT = "alert"
    EMERGENCY = "emergency"


class LogSource(Enum):
    SYSTEMD = "systemd"
    KERNEL = "kernel"
    AUTH = "auth"
    APACHE = "apache"
    NGINX = "nginx"
    NYRQIS = "nyrqis"
    SSHD = "sshd"
    CRON = "cron"
    DBUS = "dbus"
    NETWORK = "network"


@dataclass
class LogEntry:
    timestamp: float
    source: LogSource
    level: LogLevel
    message: str
    pid: int = 0
    hostname: str = "nyrqis"
    process: str = ""
    tag: str = ""
    raw: str = ""

    def __post_init__(self):
        if not self.raw:
            ts = time.strftime("%b %d %H:%M:%S", time.localtime(self.timestamp))
            self.raw = f"{ts} {self.hostname} {self.process}[{self.pid}]: {self.message}"

    @property
    def level_icon(self) -> str:
        icons = {
            LogLevel.DEBUG: "🔍", LogLevel.INFO: "ℹ️", LogLevel.NOTICE: "📝",
            LogLevel.WARNING: "⚠️", LogLevel.ERROR: "❌",
            LogLevel.CRITICAL: "🚨", LogLevel.ALERT: "🔴", LogLevel.EMERGENCY: "💀",
        }
        return icons.get(self.level, "?")

    @property
    def level_color(self) -> str:
        colors = {
            LogLevel.DEBUG: "#888888", LogLevel.INFO: "#4fc3f7",
            LogLevel.NOTICE: "#81c784", LogLevel.WARNING: "#ffb74d",
            LogLevel.ERROR: "#e57373", LogLevel.CRITICAL: "#f44336",
            LogLevel.ALERT: "#d32f2f", LogLevel.EMERGENCY: "#b71c1c",
        }
        return colors.get(self.level, "#ffffff")

    @property
    def source_icon(self) -> str:
        icons = {
            LogSource.SYSTEMD: "🔧", LogSource.KERNEL: "🐧", LogSource.AUTH: "🔐",
            LogSource.APACHE: "🌐", LogSource.NGINX: "🌐", LogSource.NYRQIS: "🍄",
            LogSource.SSHD: "🔑", LogSource.CRON: "⏰", LogSource.DBUS: "📡",
            LogSource.NETWORK: "📶",
        }
        return icons.get(self.source, "?")


@dataclass
class LogFilter:
    name: str
    level: Optional[LogLevel] = None
    source: Optional[LogSource] = None
    text_pattern: str = ""
    exclude_pattern: str = ""
    enabled: bool = True
    match_count: int = 0

    @property
    def description(self) -> str:
        parts = []
        if self.level:
            parts.append(f"level={self.level.value}")
        if self.source:
            parts.append(f"source={self.source.value}")
        if self.text_pattern:
            parts.append(f"pattern={self.text_pattern}")
        return " AND ".join(parts) if parts else "All entries"


@dataclass
class AlertRule:
    name: str
    condition: str = ""  # e.g. "level >= ERROR", "source == sshd", "message contains 'failed'"
    action: str = "notify"  # notify, log, sound, execute
    severity: str = "warning"
    enabled: bool = True
    triggered_count: int = 0
    last_triggered: float = 0.0
    cooldown_s: float = 60.0

    @property
    def status_icon(self) -> str:
        return "🟢" if self.enabled else "⚪"


@dataclass
class LogFile:
    path: str
    source: LogSource = LogSource.SYSTEMD
    size_bytes: int = 0
    last_modified: float = 0.0
    entries: int = 0
    is_tailing: bool = False
    encoding: str = "utf-8"

    @property
    def size_display(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        elif self.size_bytes < 1024 * 1024 * 1024:
            return f"{self.size_bytes / (1024 * 1024):.1f} MB"
        return f"{self.size_bytes / (1024 * 1024 * 1024):.2f} GB"


@dataclass
class HighlightRule:
    pattern: str
    color: str = "#ffff00"
    background: str = ""
    bold: bool = False
    enabled: bool = True


class LogViewer:
    def __init__(self):
        self.entries: List[LogEntry] = []
        self.files: List[LogFile] = []
        self.filters: List[LogFilter] = []
        self.alert_rules: List[AlertRule] = []
        self.highlights: List[HighlightRule] = []
        self.active_filters: List[str] = []
        self.search_query: str = ""
        self.wrap_lines: bool = True
        self.auto_scroll: bool = True
        self.font_size: int = 14
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        processes = [
            (LogSource.SYSTEMD, "systemd", 1),
            (LogSource.KERNEL, "kernel", 0),
            (LogSource.AUTH, "sshd", 1024),
            (LogSource.NYRQIS, "nyrqis-compositor", 2),
            (LogSource.NYRQIS, "nyrqis-shell", 3),
            (LogSource.SSHD, "sshd", 1024),
            (LogSource.CRON, "cron", 500),
            (LogSource.NETWORK, "NetworkManager", 101),
        ]

        messages = {
            LogLevel.DEBUG: [
                "Processing input event type=pointer button=1",
                "Buffer allocation: 1920x1080 DRM_FORMAT_ARGB8888",
                "Cache hit for glyph rendering, skipping rasterize",
            ],
            LogLevel.INFO: [
                "Started nyrqis-compositor.service",
                "Wayland display initialized on wayland-0",
                "GPU device /dev/dri/card0 initialized",
                "SSH session opened for user zeus from 192.168.1.50",
                "Connection from 192.168.1.50 port 42312",
                "User zeus logged in on pts/0",
            ],
            LogLevel.NOTICE: [
                "Service nyrqis-shell reached main process",
                "Automatic journal trim activated",
                "Device connected: Samsung T7 SSD",
            ],
            LogLevel.WARNING: [
                "Battery level below 20%, switching to power saver",
                "Temperature threshold exceeded: CPU at 78°C",
                "Disk /dev/sdb approaching capacity: 92% used",
                "Failed login attempt from 10.0.0.5",
                "High memory usage detected: 85% utilized",
            ],
            LogLevel.ERROR: [
                "Failed to initialize Vulkan device: VK_ERROR_INITIALIZATION_FAILED",
                "Could not mount /dev/sdb2: no such device",
                "Connection refused to 192.168.1.1:22",
                "Permission denied for user nobody on /var/log/auth.log",
                "OOM killer invoked: process firefox killed",
            ],
            LogLevel.CRITICAL: [
                "Kernel panic - not syncing: Fatal exception",
                "Hardware error: Machine check exception",
                "Filesystem corruption detected on /home",
            ],
        }

        for i in range(80):
            source, process, pid = random.choice(processes)
            level = random.choices(
                list(LogLevel),
                weights=[5, 30, 10, 15, 20, 10, 5, 5])[0]
            msg_list = messages.get(level, messages[LogLevel.INFO])
            msg = random.choice(msg_list)
            if "{port}" in msg:
                msg = msg.replace("{port}", str(random.randint(1024, 65535)))

            self.entries.append(LogEntry(
                timestamp=now - (80 - i) * random.uniform(1, 30),
                source=source, level=level, message=msg,
                pid=pid, process=process,
            ))

        self.files = [
            LogFile(path="/var/log/syslog", source=LogSource.SYSTEMD,
                     size_bytes=2048000, last_modified=now, entries=15230),
            LogFile(path="/var/log/auth.log", source=LogSource.AUTH,
                     size_bytes=512000, last_modified=now - 60, entries=3420),
            LogFile(path="/var/log/kern.log", source=LogSource.KERNEL,
                     size_bytes=1024000, last_modified=now - 30, entries=8900),
            LogFile(path="/var/log/nyrqis/compositor.log", source=LogSource.NYRQIS,
                     size_bytes=256000, last_modified=now - 5, entries=4560),
            LogFile(path="/var/log/nyrqis/shell.log", source=LogSource.NYRQIS,
                     size_bytes=128000, last_modified=now - 10, entries=2100),
            LogFile(path="/var/log/audit/audit.log", source=LogSource.AUTH,
                     size_bytes=8192000, last_modified=now - 15, entries=25600),
        ]

        self.filters = [
            LogFilter(name="Errors Only", level=LogLevel.ERROR),
            LogFilter(name="Nyrqis Logs", source=LogSource.NYRQIS),
            LogFilter(name="Auth Events", source=LogSource.AUTH),
            LogFilter(name="Failed Connections", text_pattern="failed|refused|denied"),
            LogFilter(name="High Priority", level=LogLevel.CRITICAL),
        ]

        self.alert_rules = [
            AlertRule(name="Kernel Panic", condition="level >= CRITICAL AND source == kernel",
                      action="sound", severity="emergency", triggered_count=2,
                      last_triggered=now - 86400),
            AlertRule(name="SSH Brute Force", condition="message contains 'Failed password'",
                      action="notify", severity="critical", triggered_count=15,
                      last_triggered=now - 3600),
            AlertRule(name="OOM Events", condition="message contains 'OOM killer'",
                      action="notify", severity="critical", triggered_count=1,
                      last_triggered=now - 7200),
            AlertRule(name="Disk Errors", condition="source == kernel AND message contains 'error'",
                      action="notify", severity="warning", triggered_count=8,
                      last_triggered=now - 1800),
        ]

        self.highlights = [
            HighlightRule(pattern="error|failed|denied", color="#ff6b6b", bold=True),
            HighlightRule(pattern="warning|warn", color="#ffd93d"),
            HighlightRule(pattern="nyrqis", color="#6bcb77"),
            HighlightRule(pattern="root|admin", color="#4d96ff"),
        ]

    def add_entry(self, entry: LogEntry) -> None:
        self.entries.append(entry)

    def search(self, query: str) -> List[LogEntry]:
        self.search_query = query
        q = query.lower()
        return [e for e in self.entries if q in e.message.lower() or q in e.process.lower()]

    def filter_entries(self, filters: Optional[List[str]] = None) -> List[LogEntry]:
        entries = self.entries
        if not filters:
            return entries
        result = []
        for entry in entries:
            for fname in filters:
                filt = next((f for f in self.filters if f.name == fname), None)
                if filt and filt.enabled:
                    if filt.level and entry.level != filt.level:
                        continue
                    if filt.source and entry.source != filt.source:
                        continue
                    if filt.text_pattern:
                        if not re.search(filt.text_pattern, entry.message, re.IGNORECASE):
                            continue
                    result.append(entry)
                    break
        return result

    def get_level_counts(self) -> Dict[str, int]:
        counts = {}
        for entry in self.entries:
            level = entry.level.value
            counts[level] = counts.get(level, 0) + 1
        return counts

    def get_source_counts(self) -> Dict[str, int]:
        counts = {}
        for entry in self.entries:
            source = entry.source.value
            counts[source] = counts.get(source, 0) + 1
        return counts

    def add_alert_rule(self, rule: AlertRule) -> None:
        self.alert_rules.append(rule)

    def toggle_alert_rule(self, name: str) -> bool:
        rule = next((r for r in self.alert_rules if r.name == name), None)
        if rule:
            rule.enabled = not rule.enabled
            return True
        return False

    def add_highlight(self, pattern: str, color: str, **kwargs) -> HighlightRule:
        hl = HighlightRule(pattern=pattern, color=color, **kwargs)
        self.highlights.append(hl)
        return hl

    def start_tailing(self, path: str) -> bool:
        f = next((f for f in self.files if f.path == path), None)
        if f:
            f.is_tailing = True
            return True
        return False

    def stop_tailing(self, path: str) -> bool:
        f = next((f for f in self.files if f.path == path), None)
        if f:
            f.is_tailing = False
            return True
        return False

    def get_stats(self) -> Dict:
        return {
            "total_entries": len(self.entries),
            "files": len(self.files),
            "tailing": sum(1 for f in self.files if f.is_tailing),
            "filters": len(self.filters),
            "alert_rules": len(self.alert_rules),
            "highlights": len(self.highlights),
        }


def parse_log_line(line: str) -> Optional[Dict]:
    """Parse a log line into structured data."""
    import re
    match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\w+) (.+)', line)
    if match:
        return {"timestamp": match.group(1), "level": match.group(2), "message": match.group(3)}
    return {"level": "INFO", "message": line}
