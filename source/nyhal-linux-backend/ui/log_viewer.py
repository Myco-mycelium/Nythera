"""
Nyrqis OS - System Log Viewer
Multi-file tailing, syntax highlighting, and alert rules.
"""

import time
import random
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple, Callable


class LogLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    WARN = "warning"
    ERROR = "error"
    FATAL = "fatal"
    CRITICAL = "critical"
    ALERT = "alert"
    EMERGENCY = "emergency"

    @property
    def color(self) -> Tuple[int, int, int]:
        colors = {
            LogLevel.DEBUG: (136, 136, 136),
            LogLevel.INFO: (79, 195, 247),
            LogLevel.NOTICE: (129, 199, 132),
            LogLevel.WARNING: (255, 183, 77),
            LogLevel.WARN: (255, 183, 77),
            LogLevel.ERROR: (229, 115, 115),
            LogLevel.FATAL: (244, 67, 54),
            LogLevel.CRITICAL: (244, 67, 54),
            LogLevel.ALERT: (211, 47, 47),
            LogLevel.EMERGENCY: (183, 28, 28),
        }
        return colors.get(self, (255, 255, 255))

    @property
    def priority(self) -> int:
        priorities = {
            LogLevel.DEBUG: 0,
            LogLevel.INFO: 1,
            LogLevel.NOTICE: 2,
            LogLevel.WARNING: 3,
            LogLevel.WARN: 3,
            LogLevel.ERROR: 4,
            LogLevel.FATAL: 5,
            LogLevel.CRITICAL: 5,
            LogLevel.ALERT: 6,
            LogLevel.EMERGENCY: 7,
        }
        return priorities.get(self, 0)


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
    timestamp: float = 0.0
    source: str = ""
    level: LogLevel = LogLevel.INFO
    message: str = ""
    text: str = ""
    pid: int = 0
    hostname: str = "nyrqis"
    process: str = ""
    tag: str = ""
    raw: str = ""

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if not self.text and self.message:
            self.text = self.message
        elif not self.message and self.text:
            self.message = self.text

    @property
    def level_icon(self) -> str:
        icons = {
            LogLevel.DEBUG: "🔍", LogLevel.INFO: "ℹ️", LogLevel.NOTICE: "📝",
            LogLevel.WARNING: "⚠️", LogLevel.ERROR: "❌",
            LogLevel.CRITICAL: "🚨", LogLevel.ALERT: "🔴", LogLevel.EMERGENCY: "💀",
            LogLevel.WARN: "⚠️", LogLevel.FATAL: "💀",
        }
        return icons.get(self.level, "?")


@dataclass
class LogTab:
    id: str = ""
    name: str = ""
    path: str = ""
    entries: List = field(default_factory=list)
    total_lines: int = 0
    follow: bool = True
    paused: bool = False
    level_filter: Optional[str] = None
    search_query: str = ""
    search_regex: bool = False
    bookmarks: List[int] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]


@dataclass
class LogFilter:
    name: str
    level: Optional[LogLevel] = None
    source: Optional[LogSource] = None
    text_pattern: str = ""
    exclude_pattern: str = ""
    enabled: bool = True
    match_count: int = 0


@dataclass
class AlertRule:
    name: str
    condition: str = ""
    action: str = "notify"
    severity: str = "warning"
    enabled: bool = True
    triggered_count: int = 0
    last_triggered: float = 0.0
    cooldown_s: float = 60.0


@dataclass
class LogFile:
    path: str
    source: LogSource = LogSource.SYSTEMD
    size_bytes: int = 0
    last_modified: float = 0.0
    entries: int = 0
    is_tailing: bool = False
    encoding: str = "utf-8"


@dataclass
class HighlightRule:
    pattern: str
    color: str = "#ffff00"
    background: str = ""
    bold: bool = False
    enabled: bool = True


class LogViewer:
    def __init__(self):
        self.tabs: List[LogTab] = []
        self._visible: bool = False
        self._callbacks: List[Callable] = []
        self._active_tab_index: int = 0

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self):
        self._visible = True
        self._emit("shown", {})

    def hide(self):
        self._visible = False
        self._emit("hidden", {})

    def add_tab(self, name: str, path: str = "") -> LogTab:
        tab = LogTab(name=name, path=path)
        self.tabs.append(tab)
        if path and os.path.exists(path):
            try:
                with open(path, "r") as f:
                    for line in f:
                        entry = parse_log_line(line.rstrip())
                        if entry:
                            tab.entries.append(entry)
                            tab.total_lines += 1
            except Exception:
                pass
        return tab

    def remove_tab(self, tab_id: str) -> bool:
        for i, t in enumerate(self.tabs):
            if t.id == tab_id:
                del self.tabs[i]
                return True
        return False

    def add_line(self, tab_id: str, line: str) -> Optional[LogEntry]:
        tab = self._get_tab(tab_id)
        if not tab:
            return None
        entry = parse_log_line(line)
        if not entry:
            entry = LogEntry(message=line, text=line)
        tab.entries.append(entry)
        tab.total_lines += 1
        return entry

    def add_lines(self, tab_id: str, lines: List[str]) -> int:
        count = 0
        for line in lines:
            if self.add_line(tab_id, line):
                count += 1
        return count

    def set_level_filter(self, tab_id: str, level_filter: str):
        tab = self._get_tab(tab_id)
        if tab:
            tab.level_filter = level_filter

    def set_search(self, tab_id: str, query: str, regex: bool = False):
        tab = self._get_tab(tab_id)
        if tab:
            tab.search_query = query
            tab.search_regex = regex

    def get_filtered_entries(self, tab_id: str) -> List[LogEntry]:
        tab = self._get_tab(tab_id)
        if not tab:
            return []
        entries = list(tab.entries)
        # Level filter
        if tab.level_filter:
            if "+" in tab.level_filter:
                level_name = tab.level_filter.replace("+", "").lower()
                try:
                    min_level = LogLevel(level_name)
                    entries = [e for e in entries if e.level.priority >= min_level.priority]
                except ValueError:
                    pass
        # Search filter
        if tab.search_query:
            if tab.search_regex:
                entries = [e for e in entries if re.search(tab.search_query, e.text or e.message, re.IGNORECASE)]
            else:
                q = tab.search_query.lower()
                entries = [e for e in entries if q in (e.text or e.message).lower()]
        return entries

    def toggle_follow(self, tab_id: str) -> bool:
        tab = self._get_tab(tab_id)
        if tab:
            tab.follow = not tab.follow
            return tab.follow
        return False

    def toggle_pause(self, tab_id: str) -> bool:
        tab = self._get_tab(tab_id)
        if tab:
            tab.paused = not tab.paused
            return tab.paused
        return False

    def bookmark_line(self, tab_id: str, index: int):
        tab = self._get_tab(tab_id)
        if tab and index not in tab.bookmarks:
            tab.bookmarks.append(index)

    def get_bookmarks(self, tab_id: str) -> List[int]:
        tab = self._get_tab(tab_id)
        return list(tab.bookmarks) if tab else []

    def next_tab(self) -> Optional[str]:
        if len(self.tabs) <= 1:
            return None
        self._active_tab_index = (self._active_tab_index + 1) % len(self.tabs)
        return self.tabs[self._active_tab_index].id

    def stats(self, tab_id: str) -> Dict:
        tab = self._get_tab(tab_id)
        if not tab:
            return {}
        return {
            "total_lines": tab.total_lines,
            "entries": len(tab.entries),
            "bookmarks": len(tab.bookmarks),
        }

    def render(self) -> Optional[List[str]]:
        if not self._visible:
            return None
        lines = []
        for tab in self.tabs:
            lines.append(f"--- {tab.name} ---")
            for e in tab.entries[-50:]:
                lines.append(f"[{e.level.value.upper()}] {e.text or e.message}")
        return lines

    def on_event(self, callback: Callable):
        self._callbacks.append(callback)

    def _emit(self, event_type: str, data: dict):
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def _get_tab(self, tab_id: str) -> Optional[LogTab]:
        for t in self.tabs:
            if t.id == tab_id:
                return t
        return None

    def __repr__(self) -> str:
        return f"LogViewer(tabs={len(self.tabs)}, visible={self._visible})"


import os


def parse_log_line(line: str) -> Optional[LogEntry]:
    """Parse a log line into a LogEntry."""
    if not line:
        return None

    # Syslog: "Jan  1 12:00:00 hostname process[pid]: message"
    syslog_match = re.match(
        r'(\w+\s+\d+\s+\d+:\d+:\d+)\s+\S+\s+(\S+?)(?:\[\d+\])?:\s*(.*)', line)
    if syslog_match:
        source = syslog_match.group(2)
        message = syslog_match.group(3)
        level = LogLevel.INFO
        msg_lower = message.lower()
        if "error" in msg_lower or "fail" in msg_lower:
            level = LogLevel.ERROR
        elif "warn" in msg_lower:
            level = LogLevel.WARNING
        elif "debug" in msg_lower:
            level = LogLevel.DEBUG
        return LogEntry(source=source, level=level, message=message, text=message)

    # Bracketed: "[LEVEL] message"
    bracket_match = re.match(r'\[(\w+)\]\s*(.*)', line)
    if bracket_match:
        level_str = bracket_match.group(1).lower()
        message = bracket_match.group(2)
        try:
            level = LogLevel(level_str)
        except ValueError:
            level = LogLevel.INFO
        return LogEntry(level=level, message=message, text=message)

    # Plain text
    return LogEntry(message=line, text=line)


# ---------------------------------------------------------------------------
# Sample data helpers (for backward compatibility)
# ---------------------------------------------------------------------------

class HighlightRule:
    pass  # Already defined above


# Backward compat for code that imports from log_viewer
LogFilter = LogFilter
AlertRule = AlertRule
LogFile = LogFile
