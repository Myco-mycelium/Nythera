#!/usr/bin/env python3
"""log_viewer — Nyrqis log viewer with syntax highlighting.

A full-featured log viewer for system and application logs:

- Follow (tail -f) mode with live updates
- Syntax highlighting: ERROR=red, WARN=yellow, INFO=blue, DEBUG=gray
- Timestamp parsing and formatting
- Level filtering (show only errors, warnings, etc.)
- Text search with match highlighting
- Bookmark lines for quick reference
- Jump to line / go to end
- Pause/resume following
- Auto-scroll with manual override
- Multiple log file tabs
- Wrap/nowrap toggle
- Export selection to file
- Log level color coding per source component

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class LogLevel(Enum):
    """Log levels with display properties."""
    TRACE = "trace"
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    FATAL = "fatal"

    @property
    def color(self) -> Tuple[int, int, int]:
        return {
            LogLevel.TRACE: (100, 100, 120),
            LogLevel.DEBUG: (120, 120, 150),
            LogLevel.INFO: (80, 180, 220),
            LogLevel.WARN: (220, 180, 60),
            LogLevel.ERROR: (220, 80, 80),
            LogLevel.FATAL: (255, 60, 60),
        }[self]

    @property
    def tag_color(self) -> Tuple[int, int, int]:
        return {
            LogLevel.TRACE: (60, 60, 80),
            LogLevel.DEBUG: (70, 70, 100),
            LogLevel.INFO: (40, 100, 140),
            LogLevel.WARN: (140, 110, 30),
            LogLevel.ERROR: (140, 40, 40),
            LogLevel.FATAL: (180, 30, 30),
        }[self]

    @property
    def priority(self) -> int:
        """Higher = more severe."""
        return list(LogLevel).index(self)


# Level filter presets
LEVEL_FILTERS = {
    "all": set(LogLevel),
    "info+": {LogLevel.INFO, LogLevel.WARN, LogLevel.ERROR, LogLevel.FATAL},
    "warn+": {LogLevel.WARN, LogLevel.ERROR, LogLevel.FATAL},
    "error+": {LogLevel.ERROR, LogLevel.FATAL},
    "debug+": set(LogLevel) - {LogLevel.TRACE},
}


@dataclass
class LogEntry:
    """A single log line."""
    text: str
    level: LogLevel = LogLevel.INFO
    timestamp: float = 0.0
    source: str = ""           # component/module name
    line_number: int = 0       # original line number in file
    bookmarked: bool = False
    highlighted: bool = False  # search match
    raw: str = ""              # original unprocessed text

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if not self.raw:
            self.raw = self.text


@dataclass
class LogFilter:
    """Active filters for the log viewer."""
    level_filter: str = "all"       # key into LEVEL_FILTERS
    search_query: str = ""
    search_regex: bool = False
    source_filter: str = ""         # filter by source component
    hide_duplicates: bool = False
    max_lines: int = 10000          # max buffered lines


@dataclass
class LogTab:
    """A log file tab."""
    id: str
    name: str
    path: str = ""
    entries: deque = field(default_factory=lambda: deque(maxlen=10000))
    filter: LogFilter = field(default_factory=LogFilter)
    following: bool = True
    paused: bool = False
    wrap: bool = True
    scroll_offset: int = 0
    selected_line: int = 0
    total_lines: int = 0
    sources: Set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Log parser
# ---------------------------------------------------------------------------

# Common log patterns
_LOG_PATTERNS = [
    # syslog: "Jan  1 12:00:00 hostname component[pid]: message"
    re.compile(
        r"^(?P<ts>\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+\S+\s+(?P<src>\S+?)(?:\[\d+\])?:\s+(?P<msg>.+)$"
    ),
    # ISO timestamp: "2024-01-15 12:00:00,123 LEVEL message"
    re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,\.]\d{3})\s+(?P<level>\w+)\s+(?P<msg>.+)$"
    ),
    # Python logging: "LEVEL:source: message"
    re.compile(
        r"^(?P<level>\w+):\s*(?P<src>\S+?):\s+(?P<msg>.+)$"
    ),
    # Simple: "[LEVEL] message"
    re.compile(
        r"^\[(?P<level>\w+)\]\s+(?P<msg>.+)$"
    ),
    # systemd journal: "Jan 15 12:00:00 hostname component: message"
    re.compile(
        r"^(?P<ts>\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+\S+\s+(?P<src>\S+?):\s+(?P<msg>.+)$"
    ),
]

_LEVEL_KEYWORDS = {
    "TRACE": LogLevel.TRACE, "TRACEBACK": LogLevel.TRACE,
    "DEBUG": LogLevel.DEBUG, "DBG": LogLevel.DEBUG,
    "INFO": LogLevel.INFO, "INF": LogLevel.INFO, "INFORMATION": LogLevel.INFO,
    "WARN": LogLevel.WARN, "WARNING": LogLevel.WARN,
    "ERROR": LogLevel.ERROR, "ERR": LogLevel.ERROR,
    "FATAL": LogLevel.FATAL, "CRITICAL": LogLevel.FATAL, "CRIT": LogLevel.FATAL,
    "PANIC": LogLevel.FATAL,
}


def parse_log_line(text: str, line_number: int = 0) -> LogEntry:
    """Parse a log line into a LogEntry."""
    level = LogLevel.INFO
    source = ""
    timestamp = time.time()

    # Try patterns
    for pattern in _LOG_PATTERNS:
        m = pattern.match(text)
        if m:
            groups = m.groupdict()
            msg = groups.get("msg", text)
            source = groups.get("src", "")

            # Parse level
            level_str = groups.get("level", "").upper()
            if level_str in _LEVEL_KEYWORDS:
                level = _LEVEL_KEYWORDS[level_str]

            # Check message for level keywords if not found
            if level == LogLevel.INFO:
                for kw, lv in _LEVEL_KEYWORDS.items():
                    if f"[{kw}]" in msg or f" {kw} " in msg:
                        level = lv
                        break

            return LogEntry(
                text=msg, level=level, timestamp=timestamp,
                source=source, line_number=line_number, raw=text,
            )

    # Fallback: keyword detection
    upper = text.upper()
    for kw, lv in _LEVEL_KEYWORDS.items():
        if kw in upper:
            level = lv
            break

    return LogEntry(
        text=text, level=level, timestamp=timestamp,
        line_number=line_number, raw=text,
    )


# ---------------------------------------------------------------------------
# Log viewer
# ---------------------------------------------------------------------------

class LogViewer:
    """Log viewer with syntax highlighting and filtering.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session.
    max_lines : int
        Maximum lines to buffer per tab.
    """

    def __init__(self, session=None, max_lines: int = 10000) -> None:
        self._session = session
        self._max_lines = max_lines
        self._tabs: Dict[str, LogTab] = {}
        self._active_tab_id: Optional[str] = None
        self._visible = False
        self._tab_counter = 0
        self._callbacks: List[Callable] = []
        self._tail_fd = None  # for follow mode

    # -- Tab management ------------------------------------------------

    def add_tab(self, name: str, path: str = "") -> LogTab:
        """Add a new log tab."""
        self._tab_counter += 1
        tab_id = f"log-{self._tab_counter}"
        tab = LogTab(id=tab_id, name=name, path=path)
        tab.filter.max_lines = self._max_lines
        self._tabs[tab_id] = tab
        if self._active_tab_id is None:
            self._active_tab_id = tab_id

        # Load file if path provided
        if path and os.path.isfile(path):
            self._load_file(tab_id)

        self._dispatch("tab_added", tab_id)
        return tab

    def remove_tab(self, tab_id: str) -> bool:
        """Remove a log tab."""
        if tab_id not in self._tabs:
            return False
        del self._tabs[tab_id]
        if self._active_tab_id == tab_id:
            self._active_tab_id = (
                next(iter(self._tabs)) if self._tabs else None)
        self._dispatch("tab_removed", tab_id)
        return True

    def get_tab(self, tab_id: str) -> Optional[LogTab]:
        return self._tabs.get(tab_id)

    @property
    def tabs(self) -> List[LogTab]:
        return list(self._tabs.values())

    @property
    def active_tab(self) -> Optional[LogTab]:
        if self._active_tab_id:
            return self._tabs.get(self._active_tab_id)
        return None

    @property
    def active_tab_id(self) -> Optional[str]:
        return self._active_tab_id

    def set_active_tab(self, tab_id: str) -> bool:
        if tab_id in self._tabs:
            self._active_tab_id = tab_id
            self._dispatch("tab_changed", tab_id)
            return True
        return False

    def next_tab(self) -> Optional[str]:
        """Switch to next tab."""
        ids = list(self._tabs.keys())
        if not ids:
            return None
        idx = ids.index(self._active_tab_id) if self._active_tab_id in ids else 0
        next_idx = (idx + 1) % len(ids)
        self._active_tab_id = ids[next_idx]
        return self._active_tab_id

    def prev_tab(self) -> Optional[str]:
        """Switch to previous tab."""
        ids = list(self._tabs.keys())
        if not ids:
            return None
        idx = ids.index(self._active_tab_id) if self._active_tab_id in ids else 0
        prev_idx = (idx - 1) % len(ids)
        self._active_tab_id = ids[prev_idx]
        return self._active_tab_id

    # -- Log input -----------------------------------------------------

    def add_line(self, tab_id: str, text: str) -> LogEntry:
        """Add a log line to a tab."""
        tab = self._tabs.get(tab_id)
        if tab is None:
            return LogEntry(text="")

        tab.total_lines += 1
        entry = parse_log_line(text, tab.total_lines)
        entry.highlighted = self._matches_filter(entry, tab.filter)

        if tab.filter.hide_duplicates:
            if tab.entries and tab.entries[-1].text == entry.text:
                return entry

        tab.entries.append(entry)
        if entry.source:
            tab.sources.add(entry.source)

        self._dispatch("line_added", {"tab_id": tab_id, "entry": entry})
        return entry

    def add_lines(self, tab_id: str, lines: List[str]) -> int:
        """Add multiple lines. Returns count added."""
        for line in lines:
            self.add_line(tab_id, line)
        return len(lines)

    def _load_file(self, tab_id: str) -> int:
        """Load lines from file."""
        tab = self._tabs.get(tab_id)
        if tab is None or not tab.path:
            return 0
        try:
            with open(tab.path, "r", errors="replace") as f:
                lines = f.readlines()
            for line in lines[-self._max_lines:]:
                self.add_line(tab_id, line.rstrip("\n"))
            return len(lines)
        except OSError:
            return 0

    # -- Filtering -----------------------------------------------------

    def set_level_filter(self, tab_id: str, filter_name: str) -> None:
        """Set level filter: all, info+, warn+, error+, debug+."""
        tab = self._tabs.get(tab_id)
        if tab:
            tab.filter.level_filter = filter_name
            self._refresh_highlights(tab_id)

    def set_search(self, tab_id: str, query: str, regex: bool = False) -> None:
        """Set search filter."""
        tab = self._tabs.get(tab_id)
        if tab:
            tab.filter.search_query = query
            tab.filter.search_regex = regex
            self._refresh_highlights(tab_id)

    def set_source_filter(self, tab_id: str, source: str) -> None:
        """Filter by source component."""
        tab = self._tabs.get(tab_id)
        if tab:
            tab.filter.source_filter = source

    def _matches_filter(self, entry: LogEntry, f: LogFilter) -> bool:
        """Check if an entry matches current filters."""
        # Level filter
        allowed = LEVEL_FILTERS.get(f.level_filter, set(LogLevel))
        if entry.level not in allowed:
            return False

        # Source filter
        if f.source_filter and entry.source != f.source_filter:
            return False

        # Search filter
        if f.search_query:
            if f.search_regex:
                try:
                    if not re.search(f.search_query, entry.text, re.IGNORECASE):
                        return False
                except re.error:
                    return False
            else:
                if f.search_query.lower() not in entry.text.lower():
                    return False

        return True

    def _refresh_highlights(self, tab_id: str) -> None:
        """Refresh search highlights on all entries."""
        tab = self._tabs.get(tab_id)
        if tab is None:
            return
        for entry in tab.entries:
            entry.highlighted = self._matches_filter(entry, tab.filter)

    def get_filtered_entries(self, tab_id: str) -> List[LogEntry]:
        """Get entries matching current filters."""
        tab = self._tabs.get(tab_id)
        if tab is None:
            return []
        return [e for e in tab.entries if self._matches_filter(e, tab.filter)]

    # -- Navigation ----------------------------------------------------

    def scroll_up(self, tab_id: str, lines: int = 10) -> None:
        tab = self._tabs.get(tab_id)
        if tab:
            tab.scroll_offset = max(0, tab.scroll_offset - lines)

    def scroll_down(self, tab_id: str, lines: int = 10) -> None:
        tab = self._tabs.get(tab_id)
        if tab:
            max_offset = max(0, len(tab.entries) - 50)
            tab.scroll_offset = min(max_offset, tab.scroll_offset + lines)

    def go_to_end(self, tab_id: str) -> None:
        """Jump to last line."""
        tab = self._tabs.get(tab_id)
        if tab:
            tab.scroll_offset = max(0, len(tab.entries) - 50)
            tab.following = True

    def go_to_line(self, tab_id: str, line_num: int) -> None:
        """Jump to a specific line number."""
        tab = self._tabs.get(tab_id)
        if tab:
            tab.scroll_offset = max(0, line_num - 25)
            tab.following = False

    def toggle_follow(self, tab_id: str) -> bool:
        """Toggle follow (tail) mode."""
        tab = self._tabs.get(tab_id)
        if tab:
            tab.following = not tab.following
            if tab.following:
                self.go_to_end(tab_id)
            return tab.following
        return False

    def toggle_pause(self, tab_id: str) -> bool:
        """Toggle pause."""
        tab = self._tabs.get(tab_id)
        if tab:
            tab.paused = not tab.paused
            return tab.paused
        return False

    def toggle_wrap(self, tab_id: str) -> bool:
        """Toggle line wrapping."""
        tab = self._tabs.get(tab_id)
        if tab:
            tab.wrap = not tab.wrap
            return tab.wrap
        return False

    # -- Bookmarks -----------------------------------------------------

    def bookmark_line(self, tab_id: str, entry_index: int) -> bool:
        tab = self._tabs.get(tab_id)
        if tab and 0 <= entry_index < len(tab.entries):
            tab.entries[entry_index].bookmarked = not tab.entries[entry_index].bookmarked
            return True
        return False

    def get_bookmarks(self, tab_id: str) -> List[LogEntry]:
        tab = self._tabs.get(tab_id)
        if tab is None:
            return []
        return [e for e in tab.entries if e.bookmarked]

    def jump_to_next_bookmark(self, tab_id: str) -> Optional[LogEntry]:
        tab = self._tabs.get(tab_id)
        if tab is None:
            return None
        for i in range(tab.selected_line + 1, len(tab.entries)):
            if tab.entries[i].bookmarked:
                tab.selected_line = i
                tab.scroll_offset = max(0, i - 25)
                return tab.entries[i]
        return None

    # -- Statistics ----------------------------------------------------

    def stats(self, tab_id: str) -> Dict[str, Any]:
        """Get statistics for a tab."""
        tab = self._tabs.get(tab_id)
        if tab is None:
            return {}
        entries = list(tab.entries)
        level_counts = {}
        for lv in LogLevel:
            level_counts[lv.value] = sum(1 for e in entries if e.level == lv)
        return {
            "total_lines": tab.total_lines,
            "buffered": len(entries),
            "bookmarked": sum(1 for e in entries if e.bookmarked),
            "sources": list(tab.sources),
            "levels": level_counts,
            "following": tab.following,
            "paused": tab.paused,
        }

    # -- View ----------------------------------------------------------

    def show(self) -> None:
        self._visible = True
        self._dispatch("shown")

    def hide(self) -> None:
        self._visible = False
        self._dispatch("hidden")

    def toggle(self) -> bool:
        if self._visible:
            self.hide()
        else:
            self.show()
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Rendering -----------------------------------------------------

    def render(self, width: int = 1920, height: int = 1080) -> Any:
        """Render the log viewer."""
        if not self._visible:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
            font_mono = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
            font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15)
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        except (OSError, IOError):
            font = font_mono = font_bold = font_small = ImageFont.load_default()

        # Panel
        px, py = 80, 40
        pw, ph = width - 160, height - 80
        draw.rounded_rectangle(
            [px, py, px + pw, py + ph],
            radius=16, fill=(20, 20, 25, 240), outline=(60, 60, 70))

        # Title
        draw.text((px + 20, py + 16), "Log Viewer",
                  fill=(220, 220, 220), font=font_bold)

        # Tab bar
        tab_x = px + 140
        for tab in self._tabs.values():
            is_active = (tab.id == self._active_tab_id)
            tw = len(tab.name) * 9 + 30
            if is_active:
                draw.rounded_rectangle(
                    [tab_x, py + 12, tab_x + tw, py + 36],
                    radius=6, fill=(50, 50, 65))
            draw.text((tab_x + 10, py + 16), tab.name,
                      fill=(230, 230, 230) if is_active else (120, 120, 120),
                      font=font)
            tab_x += tw + 4

        # Active tab content
        tab = self.active_tab
        if tab is None:
            return img

        # Filter bar
        fy = py + 44
        draw.text((px + 20, fy), f"Filter: {tab.filter.level_filter}",
                  fill=(140, 140, 140), font=font_small)
        draw.text((px + 160, fy), f"Search: {tab.filter.search_query or '-'}",
                  fill=(140, 140, 140), font=font_small)
        status = "following" if tab.following else ("paused" if tab.paused else "stopped")
        draw.text((px + pw - 120, fy), status,
                  fill=(100, 200, 100) if tab.following else (180, 180, 180),
                  font=font_small)

        # Log lines
        line_y = fy + 22
        line_h = 16
        visible_lines = (ph - 80) // line_h
        filtered = self.get_filtered_entries(tab.id)
        start = tab.scroll_offset
        end = min(start + visible_lines, len(filtered))

        for i, entry in enumerate(filtered[start:end]):
            ly = line_y + (i - start) * line_h

            # Line number
            ln = f"{entry.line_number:>6}"
            draw.text((px + 16, ly), ln, fill=(80, 80, 100), font=font_mono)

            # Level tag
            tag = f"[{entry.level.value.upper():>5}]"
            draw.text((px + 70, ly), tag,
                      fill=entry.level.tag_color, font=font_mono)

            # Source
            if entry.source:
                src = f" {entry.source[:12]:>12}"
                draw.text((px + 140, ly), src,
                          fill=(100, 140, 180), font=font_mono)

            # Message text
            msg_x = px + 280 if entry.source else px + 140
            msg_color = entry.level.color if not entry.highlighted else (255, 220, 80)
            msg = entry.text[:80]
            draw.text((msg_x, ly), msg, fill=msg_color, font=font_mono)

            # Bookmark indicator
            if entry.bookmarked:
                draw.text((px + 12, ly), "★", fill=(255, 200, 60), font=font_small)

        if not filtered:
            draw.text((px + 20, line_y + 20), "No log entries",
                      fill=(100, 100, 100), font=font)

        # Scrollbar
        if len(filtered) > visible_lines:
            sb_x = px + pw - 16
            sb_h = ph - 80
            sb_thumb_h = max(20, int(sb_h * visible_lines / len(filtered)))
            sb_thumb_y = line_y + int(sb_h * tab.scroll_offset / len(filtered))
            draw.rounded_rectangle(
                [sb_x, line_y, sb_x + 8, line_y + sb_h],
                radius=4, fill=(40, 40, 50))
            draw.rounded_rectangle(
                [sb_x, sb_thumb_y, sb_x + 8, sb_thumb_y + sb_thumb_h],
                radius=4, fill=(80, 80, 100))

        return img

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, data: Any = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (
            f"LogViewer(tabs={len(self._tabs)}, "
            f"active='{self._active_tab_id}')"
        )


__all__ = [
    "LogViewer", "LogTab", "LogEntry", "LogFilter", "LogLevel",
    "parse_log_line", "LEVEL_FILTERS",
]
