"""ActivityHistory — Tracks recent files, apps, and documents for Nyrqis.

Provides a unified activity history with:
- Recent files with app association
- Recent applications with launch count
- Frequent files ranking
- Search and filter
- Pin/star important items
- Clear history by time range
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ActivityType(Enum):
    FILE = auto()
    APP = auto()
    FOLDER = auto()
    URL = auto()
    COMMAND = auto()


class SortOrder(Enum):
    RECENT = auto()
    FREQUENT = auto()
    ALPHABETICAL = auto()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ActivityEntry:
    """A single activity history entry."""
    id: str
    activity_type: ActivityType
    name: str
    path: str = ""
    app_name: str = ""
    app_color: Tuple[int, int, int, int] = (180, 180, 200, 255)
    timestamp: float = 0.0
    access_count: int = 1
    pinned: bool = False
    size: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def time_ago(self) -> str:
        elapsed = time.time() - self.timestamp
        if elapsed < 60:
            return "Just now"
        if elapsed < 3600:
            return f"{int(elapsed / 60)}m ago"
        if elapsed < 86400:
            return f"{int(elapsed / 3600)}h ago"
        if elapsed < 604800:
            return f"{int(elapsed / 86400)}d ago"
        return time.strftime("%b %d", time.localtime(self.timestamp))

    @property
    def display_size(self) -> str:
        if self.size <= 0:
            return ""
        if self.size < 1024:
            return f"{self.size} B"
        if self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        return f"{self.size / (1024 * 1024):.1f} MB"

    @property
    def type_icon_color(self) -> Tuple[int, int, int]:
        colors = {
            ActivityType.FILE: (80, 140, 255),
            ActivityType.APP: (80, 200, 120),
            ActivityType.FOLDER: (255, 200, 60),
            ActivityType.URL: (180, 80, 255),
            ActivityType.COMMAND: (200, 200, 200),
        }
        return colors.get(self.activity_type, (180, 180, 200))


# ---------------------------------------------------------------------------
# ActivityHistory
# ---------------------------------------------------------------------------

class ActivityHistory:
    """Activity history tracker for Nyrqis.

    Tracks recent files, apps, folders, URLs, and commands.

    Parameters
    ----------
    max_entries : int
        Maximum number of entries to keep.
    """

    def __init__(self, max_entries: int = 500):
        self._entries: List[ActivityEntry] = []
        self._max_entries = max_entries
        self._callbacks: List[Callable] = []

        # Search state
        self._search_query: str = ""
        self._selected_index: int = 0
        self._sort_order: SortOrder = SortOrder.RECENT
        self._filter_type: Optional[ActivityType] = None

    # -- Recording ------------------------------------------------------

    def record_file(self, path: str, app_name: str = "",
                    app_color: Tuple[int, int, int, int] = (180, 180, 200, 255),
                    size: int = 0) -> ActivityEntry:
        """Record a file access."""
        name = path.rsplit("/", 1)[-1] if "/" in path else path
        # Check for duplicate (same path in last 5 seconds)
        for entry in self._entries:
            if entry.path == path and entry.activity_type == ActivityType.FILE:
                if time.time() - entry.timestamp < 5:
                    entry.access_count += 1
                    entry.timestamp = time.time()
                    return entry

        entry = self._add_entry(ActivityEntry(
            id=f"act-{int(time.time() * 1000) % 1000000}",
            activity_type=ActivityType.FILE,
            name=name,
            path=path,
            app_name=app_name,
            app_color=app_color,
            size=size,
        ))
        return entry

    def record_app(self, app_name: str, app_color: Tuple[int, int, int, int] = (80, 200, 120, 255),
                   path: str = "") -> ActivityEntry:
        """Record an app launch."""
        # Deduplicate: if same app launched within 5 seconds, just bump count
        for entry in self._entries:
            if entry.name == app_name and entry.activity_type == ActivityType.APP:
                if time.time() - entry.timestamp < 5:
                    entry.access_count += 1
                    entry.timestamp = time.time()
                    return entry

        entry = self._add_entry(ActivityEntry(
            id=f"act-{int(time.time() * 1000) % 1000000}",
            activity_type=ActivityType.APP,
            name=app_name,
            path=path,
            app_name=app_name,
            app_color=app_color,
        ))
        return entry

    def record_folder(self, path: str) -> ActivityEntry:
        """Record a folder access."""
        name = path.rsplit("/", 1)[-1] if "/" in path else path
        return self._add_entry(ActivityEntry(
            id=f"act-{int(time.time() * 1000) % 1000000}",
            activity_type=ActivityType.FOLDER,
            name=name,
            path=path,
        ))

    def record_url(self, url: str, app_name: str = "Browser") -> ActivityEntry:
        """Record a URL visit."""
        return self._add_entry(ActivityEntry(
            id=f"act-{int(time.time() * 1000) % 1000000}",
            activity_type=ActivityType.URL,
            name=url,
            path=url,
            app_name=app_name,
            app_color=(80, 140, 255, 255),
        ))

    def record_command(self, command: str) -> ActivityEntry:
        """Record a command execution."""
        return self._add_entry(ActivityEntry(
            id=f"act-{int(time.time() * 1000) % 1000000}",
            activity_type=ActivityType.COMMAND,
            name=command,
            path=command,
        ))

    def _add_entry(self, entry: ActivityEntry) -> ActivityEntry:
        """Add an entry and prune if needed."""
        self._entries.insert(0, entry)
        self._prune()
        self._dispatch("recorded", entry)
        return entry

    def _prune(self) -> None:
        """Remove oldest unpinned entries if over limit."""
        while len(self._entries) > self._max_entries:
            oldest_idx = -1
            for i, e in enumerate(self._entries):
                if not e.pinned:
                    if oldest_idx == -1 or e.timestamp < self._entries[oldest_idx].timestamp:
                        oldest_idx = i
            if oldest_idx >= 0:
                self._entries.pop(oldest_idx)
            else:
                break

    # -- Query -----------------------------------------------------------

    @property
    def entries(self) -> List[ActivityEntry]:
        return list(self._entries)

    @property
    def filtered_entries(self) -> List[ActivityEntry]:
        """Get entries matching search and filter."""
        result = list(self._entries)

        # Filter by type
        if self._filter_type is not None:
            result = [e for e in result if e.activity_type == self._filter_type]

        # Search
        if self._search_query:
            q = self._search_query.lower()
            result = [e for e in result
                      if q in e.name.lower() or q in e.path.lower()
                      or q in e.app_name.lower()]

        # Sort
        if self._sort_order == SortOrder.FREQUENT:
            result.sort(key=lambda e: -e.access_count)
        elif self._sort_order == SortOrder.ALPHABETICAL:
            result.sort(key=lambda e: e.name.lower())

        return result

    @property
    def recent_files(self) -> List[ActivityEntry]:
        return [e for e in self._entries
                if e.activity_type == ActivityType.FILE][:20]

    @property
    def recent_apps(self) -> List[ActivityEntry]:
        return [e for e in self._entries
                if e.activity_type == ActivityType.APP][:20]

    @property
    def frequent_files(self) -> List[ActivityEntry]:
        files = [e for e in self._entries if e.activity_type == ActivityType.FILE]
        return sorted(files, key=lambda e: -e.access_count)[:20]

    def get_entry(self, entry_id: str) -> Optional[ActivityEntry]:
        for e in self._entries:
            if e.id == entry_id:
                return e
        return None

    # -- Management -----------------------------------------------------

    def pin(self, entry_id: str) -> bool:
        for e in self._entries:
            if e.id == entry_id:
                e.pinned = not e.pinned
                return e.pinned
        return False

    def remove(self, entry_id: str) -> bool:
        for i, e in enumerate(self._entries):
            if e.id == entry_id:
                self._entries.pop(i)
                return True
        return False

    def clear(self, older_than_hours: int = 0) -> int:
        """Clear entries. If older_than_hours > 0, only clear older entries."""
        if older_than_hours <= 0:
            count = len([e for e in self._entries if not e.pinned])
            self._entries = [e for e in self._entries if e.pinned]
            return count

        cutoff = time.time() - older_than_hours * 3600
        before = len(self._entries)
        self._entries = [
            e for e in self._entries
            if e.pinned or e.timestamp > cutoff
        ]
        return before - len(self._entries)

    def clear_by_type(self, activity_type: ActivityType) -> int:
        before = len(self._entries)
        self._entries = [
            e for e in self._entries
            if e.activity_type != activity_type or e.pinned
        ]
        return before - len(self._entries)

    # -- Search / Filter ------------------------------------------------

    def set_search(self, query: str) -> None:
        self._search_query = query
        self._selected_index = 0

    def set_filter(self, activity_type: Optional[ActivityType]) -> None:
        self._filter_type = activity_type
        self._selected_index = 0

    def set_sort(self, order: SortOrder) -> None:
        self._sort_order = order

    # -- Navigation -----------------------------------------------------

    def move_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def move_down(self) -> None:
        entries = self.filtered_entries
        self._selected_index = min(len(entries) - 1, self._selected_index + 1)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select(self) -> Optional[ActivityEntry]:
        entries = self.filtered_entries
        if 0 <= self._selected_index < len(entries):
            return entries[self._selected_index]
        return None

    def handle_key(self, key: str) -> str:
        """Handle keyboard input."""
        if key == "Up":
            self.move_up()
            return "navigate"
        elif key == "Down":
            self.move_down()
            return "navigate"
        elif key in ("Enter", "Return"):
            entry = self.select()
            if entry:
                entry.access_count += 1
                return f"open:{entry.id}"
            return ""
        elif key == "Escape":
            return "close"
        elif key == "p":
            entry = self.select()
            if entry:
                self.pin(entry.id)
                return "pin"
        elif key == "d":
            entry = self.select()
            if entry:
                self.remove(entry.id)
                return "delete"
        elif key == "BackSpace":
            self._search_query = self._search_query[:-1]
            self._selected_index = 0
            return "search"
        elif len(key) == 1 and key.isprintable():
            self._search_query += key
            self._selected_index = 0
            return "search"
        return ""

    # -- Callbacks -------------------------------------------------------

    def on(self, event: str, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event: str, entry: ActivityEntry) -> None:
        for cb in self._callbacks:
            try:
                cb(event, entry)
            except Exception:
                pass

    # -- Stats -----------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total": len(self._entries),
            "pinned": sum(1 for e in self._entries if e.pinned),
            "files": sum(1 for e in self._entries if e.activity_type == ActivityType.FILE),
            "apps": sum(1 for e in self._entries if e.activity_type == ActivityType.APP),
            "folders": sum(1 for e in self._entries if e.activity_type == ActivityType.FOLDER),
            "urls": sum(1 for e in self._entries if e.activity_type == ActivityType.URL),
        }

    # -- Rendering -------------------------------------------------------

    def render(self, width: int = 400, height: int = 600) -> Tuple[bytes, int, int]:
        """Render activity history UI."""
        buf = bytearray(width * height * 3)
        bg = (30, 30, 40)
        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        # Header
        self._fill_rect(buf, width, 0, 0, width, 48, (42, 42, 56))

        # Search bar
        self._fill_rect(buf, width, 12, 56, width - 24, 32, (42, 42, 56))

        # Entries
        y = 100
        entries = self.filtered_entries[:15]
        for i, entry in enumerate(entries):
            is_selected = (i == self._selected_index)
            row_bg = (50, 50, 70) if is_selected else (35, 35, 48)
            self._fill_rect(buf, width, 4, y, width - 8, 40, row_bg)

            # Type icon
            self._fill_rect(buf, width, 12, y + 12, 16, 16,
                           entry.type_icon_color)

            # Name placeholder
            self._fill_rect(buf, width, 36, y + 8, 140, 12, (200, 200, 210))

            # Time ago
            self._fill_rect(buf, width, width - 80, y + 12, 60, 10, (120, 120, 140))

            # Pin indicator
            if entry.pinned:
                self._fill_rect(buf, width, width - 96, y + 12, 10, 10,
                               (255, 200, 60))

            y += 44

        return bytes(buf), width, height

    def _fill_rect(self, buf: bytearray, buf_width: int,
                   x: int, y: int, w: int, h: int,
                   color: Tuple[int, int, int]) -> None:
        buf_height = len(buf) // (buf_width * 3)
        for dy in range(h):
            for dx in range(w):
                px, py = x + dx, y + dy
                if 0 <= px < buf_width and 0 <= py < buf_height:
                    idx = (py * buf_width + px) * 3
                    if idx + 2 < len(buf):
                        buf[idx] = color[0]
                        buf[idx + 1] = color[1]
                        buf[idx + 2] = color[2]

    # -- Serialization ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entries": len(self._entries),
            "stats": self.get_stats(),
        }


__all__ = [
    "ActivityHistory", "ActivityEntry", "ActivityType", "SortOrder",
]
