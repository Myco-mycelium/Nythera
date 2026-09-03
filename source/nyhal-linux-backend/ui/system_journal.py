"""
Nyrqis System Journal — system log viewer application.

Features:
- Real-time log tailing with live updates
- Log levels (debug, info, warning, error, critical)
- Filter by service, level, and time range
- Full-text search with regex support
- Log statistics and timeline
- Bookmark important log entries
- Export logs to file
- Log rotation management
- Keyboard navigation throughout
"""

import time
import re
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime, timedelta


# ─── Data Classes ────────────────────────────────────────────────────────


class LogLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


LEVEL_ICONS = {
    LogLevel.DEBUG: "🔍",
    LogLevel.INFO: "ℹ️",
    LogLevel.WARNING: "⚠️",
    LogLevel.ERROR: "❌",
    LogLevel.CRITICAL: "🔥",
}

LEVEL_COLORS = {
    LogLevel.DEBUG: "#808080",
    LogLevel.INFO: "#7aa2f7",
    LogLevel.WARNING: "#e0af68",
    LogLevel.ERROR: "#f7768e",
    LogLevel.CRITICAL: "#ff0000",
}


@dataclass
class LogEntry:
    """A single log entry."""
    timestamp: float
    level: LogLevel
    service: str
    message: str
    pid: int = 0
    uid: int = 0
    hostname: str = "nyrqis"
    extra: Dict[str, str] = field(default_factory=dict)
    bookmarked: bool = False
    entry_id: str = ""

    def __post_init__(self):
        if not self.entry_id:
            self.entry_id = hashlib.md5(
                f"{self.timestamp}{self.service}{self.message[:50]}".encode()
            ).hexdigest()[:8]

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S.%f")[:-3]

    @property
    def date_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def level_str(self) -> str:
        return self.level.value.upper().ljust(8)

    @property
    def level_icon(self) -> str:
        return LEVEL_ICONS.get(self.level, "❓")

    @property
    def display(self) -> str:
        return f"{self.time_str} {self.level_icon} [{self.service}] {self.message[:80]}"

    @property
    def full_display(self) -> str:
        return f"{self.date_str} {self.level_str} {self.hostname} {self.service}[{self.pid}]: {self.message}"

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.timestamp).strftime("%b %d")


@dataclass
class LogFilter:
    """Active log filters."""
    level_min: LogLevel = LogLevel.DEBUG
    services: List[str] = field(default_factory=list)
    search_query: str = ""
    time_range_hours: int = 0  # 0 = all
    hide_services: List[str] = field(default_factory=list)
    regex_enabled: bool = False


@dataclass
class LogStats:
    """Log statistics for a time period."""
    total: int = 0
    debug: int = 0
    info: int = 0
    warning: int = 0
    error: int = 0
    critical: int = 0
    services: Dict[str, int] = field(default_factory=dict)

    @property
    def error_rate(self) -> float:
        errors = self.error + self.critical
        return (errors / self.total * 100) if self.total > 0 else 0

    @property
    def level_bar(self) -> str:
        if self.total == 0:
            return ""
        parts = []
        for level, count in [(LogLevel.DEBUG, self.debug), (LogLevel.INFO, self.info),
                              (LogLevel.WARNING, self.warning), (LogLevel.ERROR, self.error),
                              (LogLevel.CRITICAL, self.critical)]:
            if count > 0:
                pct = int(count / self.total * 20)
                icon = LEVEL_ICONS[level][0]  # first char
                parts.append(f"{icon}" * max(1, pct))
        return " ".join(parts)


# ─── System Journal ──────────────────────────────────────────────────────


class SystemJournal:
    """
    System log viewer for Nyrqis OS.
    """

    def __init__(self):
        self._entries: List[LogEntry] = []
        self._filter: LogFilter = LogFilter()
        self._bookmarks: List[str] = []  # entry_ids
        self._selected_index: int = 0
        self._view_mode: str = "logs"  # logs, stats, services, bookmarks
        self._auto_scroll: bool = True
        self._wrap_lines: bool = False
        self._show_timestamps: bool = True
        self._show_service: bool = True
        self._tail_mode: bool = False
        self._export_path: str = ""

        # Stats cache
        self._stats_cache: Optional[LogStats] = None
        self._stats_time: float = 0

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        services = [
            "nyrqis-compositor", "nyrqis-shell", "nyrqis-panel",
            "systemd", "kernel", "dbus", "NetworkManager",
            "pipewire", "polkitd", "sshd", "crond", "kernel",
        ]

        messages = {
            LogLevel.DEBUG: [
                "Cache hit for key 'theme-dark'",
                "Rendering frame 1452 at 60fps",
                "Buffer pool: 12/16 buffers allocated",
                "Input event processed: key press code=28",
                "Wayland surface committed: width=1920 height=1080",
                "GPU memory usage: 256MB / 8192MB",
                "Texture atlas updated: 1024x1024",
            ],
            LogLevel.INFO: [
                "Service started successfully",
                "Session locked by user",
                "New connection from 192.168.1.100",
                "Package updated: firefox 130.0",
                "Backup completed: 2.3 GB archived",
                "NTP sync completed, offset: +0.002s",
                "Volume mounted at /mnt/data",
                "Desktop session started for user",
                "Wayland display initialized: 2560x1440@144Hz",
                "Audio device detected: PulseAudio HD",
            ],
            LogLevel.WARNING: [
                "Disk space below 10% on /home",
                "Certificate expires in 14 days",
                "Connection timeout after 30s, retrying",
                "Memory usage above 80% threshold",
                "Deprecated API call from module 'legacy'",
                "Failed to load font 'Fira Code', falling back",
                "High CPU usage detected: 95% for 60s",
                "Network interface eth0: link flapping",
            ],
            LogLevel.ERROR: [
                "Failed to connect to database: timeout",
                "Permission denied: /etc/shadow",
                "Segmentation fault in module 'gpu-accel'",
                "Out of memory: killed process 1234",
                "Failed to start service 'docker': port 2375 in use",
                "SSL handshake failed: certificate invalid",
                "Core dump generated for process 5678",
                "Read-only file system: cannot write /tmp",
            ],
            LogLevel.CRITICAL: [
                "Kernel panic: unable to mount root filesystem",
                "Hardware failure: NVMe drive /dev/nvme0n1 unrecoverable",
                "System overheating: CPU at 105°C, shutting down",
                "Power supply failure detected",
                "Filesystem corruption on /dev/sda2",
            ],
        }

        # Generate 200 sample log entries spread over 24 hours
        import random
        random.seed(42)
        for i in range(200):
            age = random.uniform(0, 86400)
            level = random.choices(
                list(LogLevel),
                weights=[10, 50, 20, 15, 5]
            )[0]
            service = random.choice(services)
            msg = random.choice(messages[level])
            pid = random.randint(100, 99999)

            entry = LogEntry(
                timestamp=now - age,
                level=level,
                service=service,
                message=msg,
                pid=pid,
                uid=1000 if service.startswith("nyrqis") else 0,
            )
            self._entries.append(entry)

        # Sort by timestamp (newest first)
        self._entries.sort(key=lambda e: e.timestamp, reverse=True)

        # Bookmark a few entries
        if len(self._entries) > 10:
            self._entries[5].bookmarked = True
            self._bookmarks.append(self._entries[5].entry_id)
        if len(self._entries) > 20:
            self._entries[15].bookmarked = True
            self._bookmarks.append(self._entries[15].entry_id)

    # ── Entry Operations ──────────────────────────────────────────────

    def add_entry(self, level: LogLevel, service: str, message: str, pid: int = 0) -> LogEntry:
        entry = LogEntry(
            timestamp=time.time(),
            level=level,
            service=service,
            message=message,
            pid=pid,
        )
        self._entries.insert(0, entry)
        self._stats_cache = None  # invalidate cache
        return entry

    def toggle_bookmark(self, index: int = -1) -> bool:
        idx = index if index >= 0 else self._selected_index
        entries = self._get_filtered_entries()
        if 0 <= idx < len(entries):
            entry = entries[idx]
            entry.bookmarked = not entry.bookmarked
            if entry.bookmarked:
                if entry.entry_id not in self._bookmarks:
                    self._bookmarks.append(entry.entry_id)
            else:
                if entry.entry_id in self._bookmarks:
                    self._bookmarks.remove(entry.entry_id)
            return entry.bookmarked
        return False

    def clear_bookmarks(self) -> int:
        count = len(self._bookmarks)
        self._bookmarks.clear()
        for entry in self._entries:
            entry.bookmarked = False
        return count

    def export_logs(self) -> str:
        """Export filtered logs as text."""
        lines = []
        for entry in self._get_filtered_entries():
            lines.append(entry.full_display)
        self._export_path = f"/tmp/nyrqis-logs-{int(time.time())}.txt"
        return "\n".join(lines)

    # ── Filtering ─────────────────────────────────────────────────────

    def set_level_filter(self, level: LogLevel) -> None:
        self._filter.level_min = level

    def set_service_filter(self, services: List[str]) -> None:
        self._filter.services = services

    def add_service_filter(self, service: str) -> None:
        if service not in self._filter.services:
            self._filter.services.append(service)

    def remove_service_filter(self, service: str) -> None:
        if service in self._filter.services:
            self._filter.services.remove(service)

    def set_search(self, query: str) -> None:
        self._filter.search_query = query

    def toggle_regex(self) -> bool:
        self._filter.regex_enabled = not self._filter.regex_enabled
        return self._filter.regex_enabled

    def toggle_auto_scroll(self) -> bool:
        self._auto_scroll = not self._auto_scroll
        return self._auto_scroll

    def toggle_tail(self) -> bool:
        self._tail_mode = not self._tail_mode
        return self._tail_mode

    def clear_filters(self) -> None:
        self._filter = LogFilter()

    def _get_filtered_entries(self) -> List[LogEntry]:
        entries = list(self._entries)

        # Level filter
        level_order = [LogLevel.DEBUG, LogLevel.INFO, LogLevel.WARNING,
                       LogLevel.ERROR, LogLevel.CRITICAL]
        min_idx = level_order.index(self._filter.level_min)
        entries = [e for e in entries if level_order.index(e.level) >= min_idx]

        # Service filter
        if self._filter.services:
            entries = [e for e in entries if e.service in self._filter.services]

        # Hide services
        if self._filter.hide_services:
            entries = [e for e in entries if e.service not in self._filter.hide_services]

        # Time range
        if self._filter.time_range_hours > 0:
            cutoff = time.time() - (self._filter.time_range_hours * 3600)
            entries = [e for e in entries if e.timestamp >= cutoff]

        # Search
        if self._filter.search_query:
            q = self._filter.search_query
            if self._filter.regex_enabled:
                try:
                    pattern = re.compile(q, re.IGNORECASE)
                    entries = [e for e in entries if pattern.search(e.message) or pattern.search(e.service)]
                except re.error:
                    pass
            else:
                ql = q.lower()
                entries = [e for e in entries
                           if ql in e.message.lower() or ql in e.service.lower()]

        return entries

    def get_bookmarked_entries(self) -> List[LogEntry]:
        return [e for e in self._entries if e.bookmarked]

    # ── Statistics ────────────────────────────────────────────────────

    def get_stats(self, hours: int = 24) -> LogStats:
        now = time.time()
        if self._stats_cache and (now - self._stats_time) < 30:
            return self._stats_cache

        cutoff = now - (hours * 3600)
        recent = [e for e in self._entries if e.timestamp >= cutoff]

        stats = LogStats(total=len(recent))
        for e in recent:
            if e.level == LogLevel.DEBUG:
                stats.debug += 1
            elif e.level == LogLevel.INFO:
                stats.info += 1
            elif e.level == LogLevel.WARNING:
                stats.warning += 1
            elif e.level == LogLevel.ERROR:
                stats.error += 1
            elif e.level == LogLevel.CRITICAL:
                stats.critical += 1
            stats.services[e.service] = stats.services.get(e.service, 0) + 1

        self._stats_cache = stats
        self._stats_time = now
        return stats

    def get_services(self) -> Dict[str, Dict]:
        """Get services with their log counts and levels."""
        services: Dict[str, Dict] = {}
        for entry in self._entries:
            if entry.service not in services:
                services[entry.service] = {
                    "total": 0, "errors": 0, "warnings": 0, "last_seen": entry.timestamp
                }
            svc = services[entry.service]
            svc["total"] += 1
            if entry.level in (LogLevel.ERROR, LogLevel.CRITICAL):
                svc["errors"] += 1
            elif entry.level == LogLevel.WARNING:
                svc["warnings"] += 1
        return services

    # ── Navigation ────────────────────────────────────────────────────

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        entries = self._get_filtered_entries()
        self._selected_index = min(len(entries) - 1, self._selected_index + 1)

    def get_selected_entry(self) -> Optional[LogEntry]:
        entries = self._get_filtered_entries()
        if 0 <= self._selected_index < len(entries):
            return entries[self._selected_index]
        return None

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def entries(self) -> List[LogEntry]:
        return list(self._entries)

    @property
    def filtered_count(self) -> int:
        return len(self._get_filtered_entries())

    @property
    def total_count(self) -> int:
        return len(self._entries)

    @property
    def bookmark_count(self) -> int:
        return len(self._bookmarks)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def auto_scroll(self) -> bool:
        return self._auto_scroll

    @property
    def filter(self) -> LogFilter:
        return self._filter

    # ── Rendering ─────────────────────────────────────────────────────

    def render_logs(self, width: int = 80) -> List[str]:
        lines = []
        stats = self.get_stats(24)
        tail = " 🔄 LIVE" if self._tail_mode else ""
        lines.append(f" 📋 System Journal ({self.filtered_count}/{self.total_count}){tail}")
        lines.append("─" * width)

        # Quick stats bar
        lines.append(f" {stats.level_bar} | Errors: {stats.error + stats.critical} | "
                     f"Warnings: {stats.warning} | Error rate: {stats.error_rate:.1f}%")

        # Active filters
        filters = []
        if self._filter.level_min != LogLevel.DEBUG:
            filters.append(f"level≥{self._filter.level_min.value}")
        if self._filter.services:
            filters.append(f"svc={','.join(self._filter.services[:2])}")
        if self._filter.search_query:
            filters.append(f"search=\"{self._filter.search_query[:20]}\"")
        if self._filter.time_range_hours > 0:
            filters.append(f"time={self._filter.time_range_hours}h")
        if filters:
            lines.append(f" 🔍 Filters: {' | '.join(filters)}")

        lines.append("─" * width)

        entries = self._get_filtered_entries()
        if not entries:
            lines.append("  No log entries match the current filters.")
        else:
            start = max(0, self._selected_index - 10)
            for i, entry in enumerate(entries[start:start + 20]):
                actual_idx = start + i
                marker = "▸" if actual_idx == self._selected_index else " "
                bookmark = " 📌" if entry.bookmarked else ""

                if self._show_timestamps:
                    ts = entry.time_str
                else:
                    ts = ""

                level_icon = entry.level_icon
                if self._show_service:
                    svc = f"[{entry.service}]"
                else:
                    svc = ""

                msg = entry.message[:width - len(ts) - len(svc) - 6]
                lines.append(f"{marker} {ts} {level_icon} {svc} {msg}{bookmark}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  B:Bookmark  F:Filter  S:Search  T:Tail")
        lines.append(" L:Level  Tab:Stats  R:Regex  Esc:Clear filters")
        return lines

    def render_stats(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📊 Log Statistics (24h)")
        lines.append("─" * width)

        stats = self.get_stats(24)
        lines.append(f" Total entries: {stats.total}")
        lines.append("")

        # Level breakdown
        lines.append(" By Level:")
        for level, count in [(LogLevel.DEBUG, stats.debug), (LogLevel.INFO, stats.info),
                              (LogLevel.WARNING, stats.warning), (LogLevel.ERROR, stats.error),
                              (LogLevel.CRITICAL, stats.critical)]:
            icon = LEVEL_ICONS[level]
            bar_len = int(count / max(stats.total, 1) * 30)
            bar = "█" * bar_len + "░" * (30 - bar_len)
            lines.append(f"  {icon} {level.value:<10s} {count:>5d}  [{bar}]")

        lines.append("")
        lines.append(f" Error rate: {stats.error_rate:.1f}%")

        # Top services
        if stats.services:
            lines.append("")
            lines.append(" Top Services:")
            sorted_svcs = sorted(stats.services.items(), key=lambda x: x[1], reverse=True)
            for svc, count in sorted_svcs[:8]:
                lines.append(f"  {svc:<25s} {count:>5d} entries")

        lines.append("─" * width)
        lines.append(" Tab:Logs  Esc:Back")
        return lines

    def render_services(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📡 Services")
        lines.append("─" * width)

        services = self.get_services()
        sorted_svcs = sorted(services.items(), key=lambda x: x[1]["total"], reverse=True)

        for svc, info in sorted_svcs:
            error_icon = " ❌" if info["errors"] > 0 else ""
            warn_icon = " ⚠️" if info["warnings"] > 0 else ""
            lines.append(f" 📦 {svc}{error_icon}{warn_icon}")
            lines.append(f"    {info['total']} entries | {info['errors']} errors | {info['warnings']} warnings")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Filter by service  Esc:Back")
        return lines

    def render_bookmarks(self, width: int = 80) -> List[str]:
        lines = []
        lines.append(f" 📌 Bookmarked Entries ({self.bookmark_count})")
        lines.append("─" * width)

        entries = self.get_bookmarked_entries()
        if not entries:
            lines.append("  No bookmarked entries.")
        else:
            for i, entry in enumerate(entries):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {entry.display}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Del:Remove bookmark  Esc:Back")
        return lines

    def render(self, width: int = 80, height: int = 30) -> List[str]:
        renderers = {
            "stats": self.render_stats,
            "services": self.render_services,
            "bookmarks": self.render_bookmarks,
        }
        renderer = renderers.get(self._view_mode, self.render_logs)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "stats":
            return self._handle_stats_key(key)
        elif self._view_mode == "services":
            return self._handle_services_key(key)
        elif self._view_mode == "bookmarks":
            return self._handle_bookmarks_key(key)
        return self._handle_logs_key(key)

    def _handle_logs_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "b":
            return "bookmark" if self.toggle_bookmark() else "unbookmark"
        elif key == "t":
            return "tail_on" if self.toggle_tail() else "tail_off"
        elif key == "\t":
            self.set_view("stats")
            return "stats"
        elif key == "r":
            return "regex_on" if self.toggle_regex() else "regex_off"
        elif key == "Escape":
            self.clear_filters()
            return "clear_filters"
        return None

    def _handle_stats_key(self, key: str) -> Optional[str]:
        if key in ("Escape", "\t"):
            self.set_view("logs")
            return "back"
        return None

    def _handle_services_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("logs")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            services = list(self.get_services().keys())
            if 0 <= self._selected_index < len(services):
                self.add_service_filter(services[self._selected_index])
                self.set_view("logs")
                return "filter_service"
        return None

    def _handle_bookmarks_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("logs")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Delete":
            entries = self.get_bookmarked_entries()
            if 0 <= self._selected_index < len(entries):
                self.toggle_bookmark(self._selected_index)
                return "remove_bookmark"
        return None
