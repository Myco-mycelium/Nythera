"""Log Aggregator — Multi-source collection, pattern detection, and alerting.

Features:
- 8 log sources: syslog, journal, nginx, postgres, kernel, auth, app, cloudwatch
- 6 log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL, ALERT
- Pattern detection with regex support
- Alert rules with thresholds and cooldowns
- Log statistics and rate tracking
- Source health monitoring
- Search and filter capabilities
- Real-time tail mode
"""

from __future__ import annotations

import re
import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    ALERT = "ALERT"

    @property
    def icon(self) -> str:
        icons = {
            LogLevel.DEBUG: "🔍", LogLevel.INFO: "ℹ️",
            LogLevel.WARNING: "⚠️", LogLevel.ERROR: "❌",
            LogLevel.CRITICAL: "🚨", LogLevel.ALERT: "🔔",
        }
        return icons.get(self, "?")


@dataclass
class LogEntry:
    timestamp: float
    source: str
    level: LogLevel
    message: str
    host: str = ""
    pid: int = 0
    tags: List[str] = field(default_factory=list)
    structured: Dict[str, str] = field(default_factory=dict)

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def time_full(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))

    @property
    def truncated_message(self) -> str:
        return self.message[:72] + "..." if len(self.message) > 75 else self.message

    @property
    def level_width(self) -> int:
        return len(self.level.value)

    @property
    def tag_str(self) -> str:
        return " ".join(f"[{t}]" for t in self.tags) if self.tags else ""


class LogSource:
    def __init__(self, name: str, source_type: str, path: str = "",
                 enabled: bool = True):
        self.name = name
        self.source_type = source_type  # file, systemd, api, tcp, udp
        self.path = path
        self.enabled = enabled
        self.total_lines: int = 0
        self.error_count: int = 0
        self.last_line_time: float = time.time()
        self.rate_per_sec: float = 0.0
        self.avg_latency_ms: float = 0.0
        self._rate_history: List[float] = []

    @property
    def status_icon(self) -> str:
        if not self.enabled:
            return "⏸"
        if self.error_count > 100:
            return "🔴"
        if self.error_count > 10:
            return "🟡"
        return "🟢"

    @property
    def type_icon(self) -> str:
        icons = {
            "file": "📄", "systemd": "🔧", "api": "🌐",
            "tcp": "📡", "udp": "📨", "journald": "📋",
            "syslog": "📰", "cloud": "☁️",
        }
        return icons.get(self.source_type, "❓")

    @property
    def rate_bar(self) -> str:
        filled = min(20, int(self.rate_per_sec / 5))
        return "█" * filled + "░" * (20 - filled)

    def update_rate(self, count: int):
        self._rate_history.append(float(count))
        if len(self._rate_history) > 60:
            self._rate_history.pop(0)
        if self._rate_history:
            self.rate_per_sec = sum(self._rate_history) / len(self._rate_history)


@dataclass
class AlertRule:
    name: str
    pattern: str = ""
    source_filter: str = ""
    level_filter: LogLevel = LogLevel.ERROR
    threshold: int = 10  # occurrences within window
    window_s: int = 300  # 5 minutes
    cooldown_s: int = 600  # 10 minutes
    enabled: bool = True
    action: str = "log"  # log, notify, webhook, email
    last_triggered: float = 0.0
    trigger_count: int = 0
    description: str = ""

    @property
    def status_icon(self) -> str:
        if not self.enabled:
            return "⏸"
        if self.trigger_count > 0:
            return "🔔"
        return "🟢"

    @property
    def action_icon(self) -> str:
        icons = {
            "log": "📝", "notify": "📲", "webhook": "🪝",
            "email": "📧", "slack": "💬", "pagerduty": "📟",
        }
        return icons.get(self.action, "❓")

    @property
    def matches_text(self) -> str:
        if self.pattern:
            return f"/{self.pattern}/"
        if self.source_filter:
            return f"src:{self.source_filter}"
        return self.level_filter.value


@dataclass
class LogPattern:
    name: str
    regex: str
    description: str = ""
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    example: str = ""

    @property
    def frequency_bar(self) -> str:
        filled = min(20, self.count // 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def recency(self) -> str:
        if self.last_seen == 0:
            return "never"
        ago = time.time() - self.last_seen
        if ago < 60:
            return f"{ago:.0f}s ago"
        if ago < 3600:
            return f"{ago / 60:.0f}m ago"
        return f"{ago / 3600:.1f}h ago"


class LogAggregator:
    def __init__(self):
        self._entries: List[LogEntry] = []
        self._sources: List[LogSource] = []
        self._alert_rules: List[AlertRule] = []
        self._patterns: List[LogPattern] = []
        self._selected_source: int = 0
        self._selected_entry: int = 0
        self._selected_rule: int = 0
        self._filter_level: Optional[LogLevel] = None
        self._filter_source: str = ""
        self._search_text: str = ""
        self._tail_mode: bool = False
        self._auto_scroll: bool = True
        self._max_entries: int = 10000
        self._view_mode: str = "logs"  # logs, sources, alerts, patterns, stats
        self._stats: Dict[str, int] = {}
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Sources
        self._sources = [
            LogSource("system-journal", "systemd", "/run/log/journal/", True),
            LogSource("syslog", "file", "/var/log/syslog", True),
            LogSource("nginx-access", "file", "/var/log/nginx/access.log", True),
            LogSource("nginx-error", "file", "/var/log/nginx/error.log", True),
            LogSource("postgres-log", "file", "/var/log/postgresql/", True),
            LogSource("kernel-ring", "syslog", "/dev/kmsg", True),
            LogSource("auth-log", "file", "/var/log/auth.log", True),
            LogSource("app-nyrqis", "journald", "nyrqis-compositor", True),
        ]
        for s in self._sources:
            s.total_lines = random.randint(1000, 50000)
            s.error_count = random.randint(0, 50)
            s.rate_per_sec = random.uniform(0.5, 25.0)
            s.avg_latency_ms = random.uniform(0.1, 5.0)

        # Alert rules
        self._alert_rules = [
            AlertRule("High Error Rate", pattern="error|ERROR", threshold=50,
                      window_s=300, action="notify", description="Alert when error rate exceeds 50/5min"),
            AlertRule("OOM Killer", pattern="Out of memory|oom-kill", level_filter=LogLevel.CRITICAL,
                      threshold=1, action="pagerduty", description="Immediate page on OOM"),
            AlertRule("Auth Failures", pattern="authentication failure|Failed password",
                      source_filter="auth-log", threshold=10, action="webhook",
                      description="Brute force detection"),
            AlertRule("Disk Space Low", pattern="No space left|ENOSPC", threshold=3,
                      window_s=600, action="email", description="Disk space critical"),
            AlertRule("SSL Cert Expiry", pattern="certificate.*(expir|renew)", threshold=5,
                      window_s=86400, action="slack", description="SSL certificate expiration warning"),
            AlertRule("Postgres Deadlocks", pattern="deadlock detected",
                      source_filter="postgres-log", threshold=5, action="notify",
                      description="Database deadlock alert"),
        ]

        # Patterns
        self._patterns = [
            LogPattern("HTTP 5xx", r"HTTP/\d\.\d\" [5]\d{2}", "Server errors", random.randint(5, 200)),
            LogPattern("Connection Timeout", r"(?i)connection timed? ?out", "Network timeouts", random.randint(1, 50)),
            LogPattern("OOM Event", r"(?i)out of memory|oom.kill|oom_reaper", "OOM killer events", random.randint(0, 5)),
            LogPattern("Segfault", r"(?i)segmentation fault|segfault|signal 11", "Crashes", random.randint(0, 3)),
            LogPattern("Auth Fail", r"(?i)(authentication|auth).*(fail|denied|invalid)", "Auth failures", random.randint(5, 100)),
            LogPattern("Slow Query", r"(?i)slow query|duration: \d{4,}ms", "Slow database queries", random.randint(1, 30)),
            LogPattern("SSL Warning", r"(?i)ssl.*(warn|error|expire|renew)", "SSL issues", random.randint(0, 10)),
            LogPattern("Disk Warning", r"(?i)(disk|space|storage).*(low|full|warn)", "Disk issues", random.randint(0, 8)),
        ]
        for p in self._patterns:
            p.first_seen = now - random.uniform(3600, 86400)
            p.last_seen = now - random.uniform(0, 3600)
            if p.count > 0:
                p.example = f"[{random.choice(['nginx', 'postgres', 'kernel', 'syslog'])}] sample log line matching pattern"

        # Sample log entries
        levels = [LogLevel.DEBUG, LogLevel.INFO, LogLevel.INFO, LogLevel.INFO,
                  LogLevel.WARNING, LogLevel.WARNING, LogLevel.ERROR,
                  LogLevel.CRITICAL, LogLevel.ALERT]
        sources = ["syslog", "nginx-access", "nginx-error", "postgres-log",
                   "kernel-ring", "auth-log", "app-nyrqis", "system-journal"]
        hosts = ["nyrqis-web-01", "nyrqis-db-01", "nyrqis-cache-01", "nyrqis-worker-01"]
        messages = [
            "POST /api/v1/auth/login 200 OK 12ms",
            "GET /api/v1/compositor/status 200 OK 5ms",
            "POST /api/v1/wayland/connect 101 Switching Protocols 3ms",
            "Connection from 192.168.1.50:45678 established",
            "SSL handshake completed: TLSv1.3 ECDHE-RSA-AES256-GCM-SHA384",
            "Query executed in 2345ms: SELECT * FROM compositor_surfaces WHERE active=true",
            "WARNING: Nyrqis compositor memory usage at 78% (6.2GB/8GB)",
            "ERROR: Failed to allocate GBM buffer: No memory available (errno=12)",
            "CRITICAL: OOM killer invoked for pid 1234 (nyrqis-render)",
            "ALERT: Unauthenticated access attempt from 10.0.0.50:22222",
            "Worker pool size adjusted: 4 → 8 (load average: 4.5)",
            "Certificate renewal required for *.nyrqis.dev (expires in 7 days)",
            "Segmentation fault (core dumped) in nyrqis-gpu-driver",
            "Disk usage at 92% on /dev/sda1, consider cleanup",
            "Received SIGHUP, reloading configuration",
            "New SSH connection from 192.168.1.100 port 22",
            "PostgreSQL: checkpoint complete: wrote 1234 buffers (5.2%)",
            "Nyrqis Wayland bridge connected: display=wayland-0",
            "GC pause: 15ms (heap: 2048MB, live: 1024MB)",
            "Rate limit exceeded for client 192.168.1.50: 150 req/min",
            "Thread pool exhausted, queuing request",
            "Backup completed: 2.3GB compressed, uploaded to cloud storage",
            "User admin logged in from 192.168.1.50",
            "Failed login attempt: user=root, source=10.0.0.99",
            "Configuration reloaded: 15 rules, 3 patterns, 6 alerts active",
        ]

        self._entries = []
        for i in range(80):
            ts = now - random.uniform(0, 7200)
            src = random.choice(sources)
            lvl = random.choice(levels)
            msg = random.choice(messages)
            host = random.choice(hosts)
            self._entries.append(LogEntry(
                timestamp=ts, source=src, level=lvl, message=msg,
                host=host, pid=random.randint(100, 9999),
                tags=random.sample(["web", "api", "db", "cache", "auth", "system", "network"],
                                   k=random.randint(0, 2)),
            ))
        self._entries.sort(key=lambda e: e.timestamp, reverse=True)

    @property
    def sources(self) -> List[LogSource]:
        return self._sources

    @property
    def alert_rules(self) -> List[AlertRule]:
        return self._alert_rules

    @property
    def patterns(self) -> List[LogPattern]:
        return self._patterns

    @property
    def filtered_entries(self) -> List[LogEntry]:
        result = self._entries
        if self._filter_level:
            result = [e for e in result if e.level == self._filter_level]
        if self._filter_source:
            result = [e for e in result if e.source == self._filter_source]
        if self._search_text:
            q = self._search_text.lower()
            result = [e for e in result if q in e.message.lower() or q in e.source.lower()]
        return result

    @property
    def selected_entry(self) -> Optional[LogEntry]:
        entries = self.filtered_entries
        if 0 <= self._selected_entry < len(entries):
            return entries[self._selected_entry]
        return None

    @property
    def total_errors(self) -> int:
        return sum(1 for e in self._entries if e.level in (LogLevel.ERROR, LogLevel.CRITICAL, LogLevel.ALERT))

    @property
    def total_warnings(self) -> int:
        return sum(1 for e in self._entries if e.level == LogLevel.WARNING)

    @property
    def entries_per_second(self) -> float:
        if not self._entries:
            return 0.0
        now = time.time()
        recent = [e for e in self._entries if now - e.timestamp < 60]
        return len(recent) / 60.0

    @property
    def level_distribution(self) -> Dict[LogLevel, int]:
        dist = {}
        for lvl in LogLevel:
            dist[lvl] = sum(1 for e in self._entries if e.level == lvl)
        return dist

    @property
    def source_distribution(self) -> Dict[str, int]:
        dist = {}
        for e in self._entries:
            dist[e.source] = dist.get(e.source, 0) + 1
        return dist

    def select_source(self, idx: int):
        if 0 <= idx < len(self._sources):
            self._selected_source = idx

    def select_entry(self, idx: int):
        self._selected_entry = idx

    def select_rule(self, idx: int):
        if 0 <= idx < len(self._alert_rules):
            self._selected_rule = idx

    def set_filter_level(self, level: Optional[LogLevel]):
        self._filter_level = level
        self._selected_entry = 0

    def set_filter_source(self, source: str):
        self._filter_source = source
        self._selected_entry = 0

    def set_search(self, text: str):
        self._search_text = text
        self._selected_entry = 0

    def toggle_tail(self):
        self._tail_mode = not self._tail_mode
        self._auto_scroll = self._tail_mode

    def toggle_source(self, idx: int):
        if 0 <= idx < len(self._sources):
            self._sources[idx].enabled = not self._sources[idx].enabled

    def cycle_view(self):
        views = ["logs", "sources", "alerts", "patterns", "stats"]
        idx = views.index(self._view_mode) if self._view_mode in views else 0
        self._view_mode = views[(idx + 1) % len(views)]

    def toggle_alert_rule(self, idx: int):
        if 0 <= idx < len(self._alert_rules):
            self._alert_rules[idx].enabled = not self._alert_rules[idx].enabled

    def clear_logs(self):
        self._entries.clear()
        self._selected_entry = 0

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS LOG AGGREGATOR                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Stats bar
        err = self.total_errors
        warn = self.total_warnings
        total = len(self._entries)
        eps = self.entries_per_second
        lines.append(f"  📊 {total} logs  ❌ {err} errors  ⚠️ {warn} warnings  📈 {eps:.1f}/s  🔄 Tail:{'ON' if self._tail_mode else 'OFF'}  View:{self._view_mode}")
        lines.append("")

        # Filters
        filter_parts = []
        if self._filter_level:
            filter_parts.append(f"level:{self._filter_level.value}")
        if self._filter_source:
            filter_parts.append(f"source:{self._filter_source}")
        if self._search_text:
            filter_parts.append(f"search:{self._search_text}")
        if filter_parts:
            lines.append(f"  🔎 Filters: {' | '.join(filter_parts)}")
            lines.append("")

        if self._view_mode == "logs":
            # Log entries
            entries = self.filtered_entries[:20]
            for i, entry in enumerate(entries):
                sel = "▶" if i == self._selected_entry else " "
                lines.append(f"  {sel} {entry.level.icon} {entry.time_str} [{entry.source:<16s}] {entry.truncated_message}")

        elif self._view_mode == "sources":
            lines.append("  ── Log Sources ──")
            lines.append(f"  {'':3s} {'Status':6s} {'Type':5s} {'Name':<22s} {'Lines':>8s} {'Errors':>7s} {'Rate':>8s}")
            for i, src in enumerate(self._sources):
                sel = "▶" if i == self._selected_source else " "
                lines.append(f"  {sel} {src.status_icon} {src.type_icon} {src.name:<22s} {src.total_lines:>8,d} {src.error_count:>7d} {src.rate_per_sec:>7.1f}/s")
                lines.append(f"      [{src.rate_bar}] {src.path}")

        elif self._view_mode == "alerts":
            lines.append("  ── Alert Rules ──")
            for i, rule in enumerate(self._alert_rules):
                sel = "▶" if i == self._selected_rule else " "
                lines.append(f"  {sel} {rule.status_icon} {rule.action_icon} {rule.name}")
                lines.append(f"      Match: {rule.matches_text}  Threshold: {rule.threshold}/{rule.window_s}s  Triggers: {rule.trigger_count}")

        elif self._view_mode == "patterns":
            lines.append("  ── Detected Patterns ──")
            for p in self._patterns:
                lines.append(f"  📐 {p.name}: {p.description}")
                lines.append(f"      /{p.regex}/  Count: {p.count}  Last: {p.recency}")
                lines.append(f"      [{p.frequency_bar}]")

        elif self._view_mode == "stats":
            lines.append("  ── Log Statistics ──")
            dist = self.level_distribution
            max_count = max(dist.values()) if dist.values() else 1
            for lvl in LogLevel:
                cnt = dist.get(lvl, 0)
                bar_len = int(cnt / max_count * 30) if max_count > 0 else 0
                bar = "█" * bar_len + "░" * (30 - bar_len)
                lines.append(f"  {lvl.icon} {lvl.value:<10s} [{bar}] {cnt:>5d}")

            lines.append("")
            lines.append("  ── Source Distribution ──")
            src_dist = self.source_distribution
            max_src = max(src_dist.values()) if src_dist.values() else 1
            for src_name, cnt in sorted(src_dist.items(), key=lambda x: -x[1]):
                bar_len = int(cnt / max_src * 30) if max_src > 0 else 0
                bar = "█" * bar_len + "░" * (30 - bar_len)
                lines.append(f"  📄 {src_name:<20s} [{bar}] {cnt:>5d}")

        lines.append("")
        lines.append("  [V]iew [L]evel [S]ource [/]Search [T]ail [C]lear [R]ules [↑↓]Nav")
        return lines
