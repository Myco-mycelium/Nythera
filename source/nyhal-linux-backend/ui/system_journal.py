"""System Journal Viewer — systemd logs, service status, log filtering for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import random


class LogLevel(Enum):
    EMERGENCY = "emerg"
    ALERT = "alert"
    CRITICAL = "crit"
    ERROR = "err"
    WARNING = "warning"
    NOTICE = "notice"
    INFO = "info"
    DEBUG = "debug"


class ServiceState(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    ACTIVATING = "activating"
    DEACTIVATING = "deactivating"
    MAINTENANCE = "maintenance"


class UnitType(Enum):
    SERVICE = "service"
    SOCKET = "socket"
    TIMER = "timer"
    MOUNT = "mount"
    TARGET = "target"
    PATH = "path"
    SLICE = "slice"
    SCOPE = "scope"


@dataclass
class JournalEntry:
    timestamp: float = 0.0
    level: LogLevel = LogLevel.INFO
    service: str = ""
    message: str = ""
    hostname: str = "nyrqis"
    pid: int = 0
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def time_str(self) -> str:
        return time.strftime("%b %d %H:%M:%S", time.localtime(self.timestamp))

    @property
    def short_time(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def level_icon(self) -> str:
        icons = {
            LogLevel.EMERGENCY: "🚨", LogLevel.ALERT: "🚨", LogLevel.CRITICAL: "🔴",
            LogLevel.ERROR: "❌", LogLevel.WARNING: "⚠️", LogLevel.NOTICE: "📋",
            LogLevel.INFO: "ℹ️", LogLevel.DEBUG: "🔍",
        }
        return icons.get(self.level, "")

    @property
    def level_color(self) -> str:
        colors = {
            LogLevel.EMERGENCY: "red", LogLevel.ALERT: "red", LogLevel.CRITICAL: "red",
            LogLevel.ERROR: "red", LogLevel.WARNING: "yellow", LogLevel.NOTICE: "cyan",
            LogLevel.INFO: "white", LogLevel.DEBUG: "gray",
        }
        return colors.get(self.level, "white")

    @property
    def display(self) -> str:
        return f"{self.level_icon} {self.time_str} {self.service}: {self.message}"


@dataclass
class ServiceUnit:
    name: str
    unit_type: UnitType = UnitType.SERVICE
    state: ServiceState = ServiceState.ACTIVE
    sub_state: str = "running"
    description: str = ""
    pid: int = 0
    main_pid: int = 0
    memory_current: int = 0  # bytes
    cpu_usage: float = 0.0
    tasks: int = 0
    restart_count: int = 0
    active_enter: float = 0.0
    active_exit: float = 0.0
    load_state: str = "loaded"
    description_short: str = ""

    @property
    def state_icon(self) -> str:
        icons = {
            ServiceState.ACTIVE: "🟢", ServiceState.INACTIVE: "⚪",
            ServiceState.FAILED: "🔴", ServiceState.ACTIVATING: "🟡",
            ServiceState.DEACTIVATING: "🟡", ServiceState.MAINTENANCE: "🔧",
        }
        return icons.get(self.state, "?")

    @property
    def memory_str(self) -> str:
        b = self.memory_current
        if b < 1024:
            return f"{b} B"
        elif b < 1024**2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024**3:
            return f"{b / 1024**2:.1f} MB"
        return f"{b / 1024**3:.2f} GB"

    @property
    def memory_bar(self) -> str:
        filled = int(min(self.memory_current / (500 * 1024**2), 1.0) * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def cpu_bar(self) -> str:
        filled = int(self.cpu_usage / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def uptime_str(self) -> str:
        if self.active_enter <= 0:
            return "N/A"
        uptime = time.time() - self.active_enter
        if uptime < 60:
            return f"{uptime:.0f}s"
        elif uptime < 3600:
            return f"{uptime / 60:.0f}m"
        elif uptime < 86400:
            return f"{uptime / 3600:.1f}h"
        return f"{uptime / 86400:.1f}d"


class SystemJournal:
    def __init__(self):
        self._entries: List[JournalEntry] = []
        self._services: List[ServiceUnit] = []
        self._selected_entry: int = 0
        self._selected_service: int = 0
        self._view_mode: str = "logs"
        self._filter_level: Optional[LogLevel] = None
        self._filter_service: str = ""
        self._filter_text: str = ""
        self._follow_mode: bool = False
        self._show_kernel: bool = False
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Sample journal entries
        services = ["nyrqis-compositor", "nyrqis-shell", "systemd", "NetworkManager",
                    "sshd", "dbus", "polkitd", "cron", "kernel", "pulseaudio",
                    "bluetooth", "cups", "gdm", "firewalld"]
        messages = [
            "Started Wayland compositor session",
            "Shell loaded successfully",
            "Listening on port 22",
            "Network connection established (eth0: 192.168.1.100)",
            "Bluetooth adapter hci0 powered on",
            "Timer triggered: /etc/cron.daily/logrotate",
            "User session started for uid=1000",
            "GPU driver loaded: nvidia 560.50",
            "Compositor: 60fps stable, memory 256MB",
            "Failed to start cups.service: Connection refused",
            "Certificate verification failed for nyrqis.io",
            "Low disk warning: /home at 92%",
            "Security audit completed: 0 critical, 2 warnings",
            "Kernel: NVRM: loading driver version 560.50",
            "System clock synchronized via chronyd",
            "Firewall: blocked connection from 185.220.101.34",
            "Backup completed: 2.4GB archived",
            "Package manager: 3 security updates available",
        ]

        for i in range(80):
            level = random.choices(list(LogLevel), weights=[1, 1, 2, 5, 15, 10, 50, 16])[0]
            service = random.choice(services)
            msg = random.choice(messages)
            if service == "kernel":
                msg = f"kernel: {msg}"
            self._entries.append(JournalEntry(
                timestamp=now - i * random.randint(5, 120),
                level=level, service=service, message=msg,
                hostname="nyrqis", pid=random.randint(1000, 99999),
            ))

        # Pre-bookmark some entries for sample data
        if len(self._entries) > 5:
            self._entries[3]._bookmarked = True
            self._entries[7]._bookmarked = True
            self._entries[12]._bookmarked = True

        # Sample services
        self._services = [
            ServiceUnit("nyrqis-compositor.service", UnitType.SERVICE, ServiceState.ACTIVE, "running",
                        "Nyrqis Wayland Compositor", 1234, 1234, 256 * 1024**2, 12.5, 24, 0, now - 86400 * 5),
            ServiceUnit("nyrqis-shell.service", UnitType.SERVICE, ServiceState.ACTIVE, "running",
                        "Nyrqis Desktop Shell", 1235, 1235, 128 * 1024**2, 3.2, 16, 0, now - 86400 * 5),
            ServiceUnit("systemd-resolved.service", UnitType.SERVICE, ServiceState.ACTIVE, "running",
                        "DNS Name Resolution", 456, 456, 12 * 1024**2, 0.5, 4, 0, now - 86400 * 10),
            ServiceUnit("NetworkManager.service", UnitType.SERVICE, ServiceState.ACTIVE, "running",
                        "Network Manager", 678, 678, 28 * 1024**2, 1.0, 8, 2, now - 86400 * 10),
            ServiceUnit("sshd.service", UnitType.SERVICE, ServiceState.ACTIVE, "running",
                        "OpenSSH Server", 890, 890, 8 * 1024**2, 0.2, 3, 0, now - 86400 * 15),
            ServiceUnit("dbus.service", UnitType.SERVICE, ServiceState.ACTIVE, "running",
                        "D-Bus System Message Bus", 567, 567, 5 * 1024**2, 0.3, 2, 0, now - 86400 * 20),
            ServiceUnit("cups.service", UnitType.SERVICE, ServiceState.FAILED, "failed",
                        "CUPS Printing Service", 0, 0, 0, 0, 0, 5, now - 86400),
            ServiceUnit("bluetooth.service", UnitType.SERVICE, ServiceState.ACTIVE, "running",
                        "Bluetooth Service", 1122, 1122, 15 * 1024**2, 0.1, 3, 1, now - 86400 * 8),
            ServiceUnit("firewalld.service", UnitType.SERVICE, ServiceState.ACTIVE, "running",
                        "Firewall Daemon", 1345, 1345, 22 * 1024**2, 0.8, 5, 0, now - 86400 * 20),
            ServiceUnit("polkit.service", UnitType.SERVICE, ServiceState.ACTIVE, "running",
                        "Authorization Manager", 1567, 1567, 8 * 1024**2, 0.2, 2, 0, now - 86400 * 20),
            ServiceUnit("logrotate.timer", UnitType.TIMER, ServiceState.ACTIVE, "waiting",
                        "Daily log rotation"),
            ServiceUnit("fstrim.timer", UnitType.TIMER, ServiceState.ACTIVE, "waiting",
                        "Weekly TRIM for SSDs"),
            ServiceUnit("chronyd.service", UnitType.SERVICE, ServiceState.ACTIVE, "running",
                        "NTP Client/Sync", 2345, 2345, 4 * 1024**2, 0.1, 2, 0, now - 86400 * 30),
        ]

    @property
    def selected_entry(self) -> Optional[JournalEntry]:
        filtered = self._filtered_entries()
        if 0 <= self._selected_entry < len(filtered):
            return filtered[self._selected_entry]
        return None

    @property
    def selected_service(self) -> Optional[ServiceUnit]:
        if 0 <= self._selected_service < len(self._services):
            return self._services[self._selected_service]
        return None

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def total_count(self) -> int:
        return len(self._entries)

    @property
    def selected_index(self) -> int:
        return self._selected_entry

    @property
    def total_entries(self) -> int:
        return len(self._filtered_entries())

    @property
    def active_services(self) -> int:
        return sum(1 for s in self._services if s.state == ServiceState.ACTIVE)

    @property
    def failed_services(self) -> int:
        return sum(1 for s in self._services if s.state == ServiceState.FAILED)

    def _filtered_entries(self) -> List[JournalEntry]:
        entries = self._entries
        if self._filter_level:
            entries = [e for e in entries if e.level == self._filter_level]
        if self._filter_service:
            entries = [e for e in entries if self._filter_service in e.service]
        if self._filter_text:
            entries = [e for e in entries if self._filter_text.lower() in e.message.lower()]
        if not self._show_kernel:
            entries = [e for e in entries if e.service != "kernel"]
        return entries

    def select_entry(self, idx: int):
        self._selected_entry = idx

    def select_service(self, idx: int):
        self._selected_service = idx

    def set_filter(self, level: LogLevel = None, service: str = "", text: str = ""):
        self._filter_level = level
        self._filter_service = service
        self._filter_text = text
        self._selected_entry = 0

    def handle_input(self, key: str):
        key = key.lower()
        if key == "f":
            self._follow_mode = not self._follow_mode
        elif key == "k":
            self._show_kernel = not self._show_kernel
        elif key == "e":
            self._view_mode = "errors"
        elif key == "s":
            self._view_mode = "services"

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS SYSTEM JOURNAL                                    ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  Entries: {self.total_entries}  Services: {self.active_services}/{len(self._services)} active  Failed: {self.failed_services}  Follow: {'ON' if self._follow_mode else 'OFF'}  Kernel: {'ON' if self._show_kernel else 'OFF'}")
        if self._filter_level or self._filter_service or self._filter_text:
            filters = []
            if self._filter_level:
                filters.append(f"level={self._filter_level.value}")
            if self._filter_service:
                filters.append(f"service={self._filter_service}")
            if self._filter_text:
                filters.append(f"text={self._filter_text}")
            lines.append(f"  Filter: {' & '.join(filters)}")
        lines.append("")

        # Journal entries
        lines.append("  ── Journal ──")
        entries = self._filtered_entries()
        for i, entry in enumerate(entries[:15]):
            sel = "▶" if i == self._selected_entry else " "
            lines.append(f"  {sel} {entry.level_icon} {entry.time_str} {entry.hostname} {entry.service:<24s} [{entry.pid}] {entry.message[:50]}")
        if len(entries) > 15:
            lines.append(f"  ... ({len(entries) - 15} more entries)")
        lines.append("")

        # Selected entry
        entry = self.selected_entry
        if entry:
            lines.append(f"  ── Entry Detail ──")
            lines.append(f"  Time: {entry.time_str}  Level: {entry.level.value}  Service: {entry.service}")
            lines.append(f"  Host: {entry.hostname}  PID: {entry.pid}")
            lines.append(f"  Message: {entry.message}")
            if entry.extra:
                for k, v in entry.extra.items():
                    lines.append(f"  {k}: {v}")
            lines.append("")

        # Services
        lines.append("  ── Services ──")
        for i, svc in enumerate(self._services[:10]):
            sel = "▶" if i == self._selected_service else " "
            lines.append(f"  {sel} {svc.state_icon} {svc.name:<35s} {svc.state.value:<10s} PID:{svc.pid:>6d}  Mem:[{svc.memory_bar}] {svc.memory_str}  CPU:[{svc.cpu_bar}]")
        lines.append("")

        # Error summary
        errors = [e for e in self._entries if e.level in (LogLevel.ERROR, LogLevel.CRITICAL, LogLevel.EMERGENCY)]
        if errors:
            lines.append(f"  ── Recent Errors ({len(errors)}) ──")
            for e in errors[:3]:
                lines.append(f"  {e.level_icon} {e.time_str} {e.service}: {e.message[:55]}")
            lines.append("")

        lines.append("  [F]Follow [K]ernel [E]Errors [S]Services [↑↓]Select [/]Search")
        lines.append("  [1]Emerg [2]Err [3]Warn [4]Info [5]Debug [0]Clear Filter")
        return lines

    # -- Test-facing API --

    def add_entry(self, level: LogLevel, service: str, message: str) -> JournalEntry:
        entry = JournalEntry(
            timestamp=time.time(),
            level=level,
            service=service,
            message=message,
        )
        self._entries.insert(0, entry)
        return entry

    def toggle_bookmark(self, idx: int):
        entries = self._filtered_entries()
        if 0 <= idx < len(entries):
            entry = entries[idx]
            if not hasattr(entry, '_bookmarked'):
                entry._bookmarked = False
            entry._bookmarked = not entry._bookmarked
            return entry
        return None

    def set_level_filter(self, level: LogLevel):
        self._filter_level = level

    def set_search(self, query: str):
        self._filter_text = query

    def toggle_regex(self) -> bool:
        self._regex_mode = not getattr(self, '_regex_mode', False)
        return self._regex_mode

    def toggle_tail(self) -> bool:
        self._follow_mode = not self._follow_mode
        return self._follow_mode

    def get_stats(self, hours: int = 24) -> "LogStats":
        cutoff = time.time() - hours * 3600
        entries = [e for e in self._entries if e.timestamp > cutoff]
        level_counts = {}
        for e in entries:
            key = e.level.value
            level_counts[key] = level_counts.get(key, 0) + 1
        return LogStats(
            total=len(entries),
            hours=hours,
            level_counts=level_counts,
        )

    def get_services(self) -> Dict:
        result = {}
        for svc in self._services:
            result[svc.name] = {
                'state': svc.state.value,
                'pid': svc.pid,
                'memory': svc.memory_str,
                'cpu': f"{svc.cpu_usage:.1f}%",
            }
        return result

    def get_bookmarked_entries(self) -> List[JournalEntry]:
        return [e for e in self._entries if getattr(e, '_bookmarked', False)]

    def export_logs(self) -> str:
        lines = []
        for e in self._entries[:100]:
            lines.append(e.display)
        return "\n".join(lines)

    def select_down(self):
        self._selected_entry += 1

    def select_up(self):
        if self._selected_entry > 0:
            self._selected_entry -= 1

    def _get_filtered_entries(self) -> List[JournalEntry]:
        return self._filtered_entries()

    def set_view(self, view: str):
        self._view_mode = view

    def render_logs(self) -> List[str]:
        lines = []
        for e in self._filtered_entries()[:20]:
            lines.append(e.display)
        return lines

    def render_stats(self) -> List[str]:
        stats = self.get_stats(24)
        return [
            f"Total entries: {stats.total}",
            f"Hours: {stats.hours}",
        ]

    def render_services(self) -> List[str]:
        lines = []
        for svc in self._services:
            lines.append(f"  {svc.state_icon} {svc.name}: {svc.state.value}")
        return lines

    def render_bookmarks(self) -> List[str]:
        bookmarks = self.get_bookmarked_entries()
        lines = []
        for e in bookmarks[:20]:
            lines.append(e.display)
        return lines

    def handle_key(self, key: str) -> str:
        if key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "f":
            self.toggle_tail()
            return "toggle_tail"
        elif key == "r":
            self.toggle_regex()
            return "toggle_regex"
        elif key == "b":
            self.toggle_bookmark(self._selected_entry)
            return "bookmark"
        return "noop"


class LogStats:
    """Statistics container for journal entries."""
    def __init__(self, total: int = 0, hours: int = 24, level_counts: Dict = None, **kwargs):
        self.total = total
        self.hours = hours
        self.level_counts = level_counts or {}
        self.error = kwargs.get('error', self.level_counts.get('err', 0))
        self.critical = kwargs.get('critical', self.level_counts.get('crit', 0))

    @property
    def error_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return round((self.error + self.critical) / self.total * 100, 1)
