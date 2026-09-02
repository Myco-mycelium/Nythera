#!/usr/bin/env python3
"""Clipboard manager for the Nyrqis desktop.

Features:
- Copy/paste history tracking
- Text and rich text entries
- Pin important items
- Search/filter history
- Keyboard navigation
- Max history limit with auto-pruning
- Duplicate detection
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Clipboard entry
# ---------------------------------------------------------------------------

class ClipboardType(Enum):
    """Clipboard content types."""
    TEXT = "text"
    RICH_TEXT = "rich_text"
    IMAGE = "image"
    FILE = "file"
    CODE = "code"


@dataclass
class ClipboardEntry:
    """A single clipboard entry."""
    id: str
    content: str
    content_type: ClipboardType = ClipboardType.TEXT
    label: str = ""
    source_app: str = ""
    timestamp: float = 0.0
    pinned: bool = False
    use_count: int = 0
    size: int = 0  # bytes
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if self.size == 0:
            self.size = len(self.content.encode("utf-8"))
        if not self.label:
            # Auto-generate label from first line
            first_line = self.content.split("\n")[0][:40]
            self.label = first_line if first_line else "Empty"
    
    @property
    def time_ago(self) -> str:
        elapsed = time.time() - self.timestamp
        if elapsed < 60:
            return "now"
        elif elapsed < 3600:
            return f"{int(elapsed / 60)}m ago"
        elif elapsed < 86400:
            return f"{int(elapsed / 3600)}h ago"
        return f"{int(elapsed / 86400)}d ago"
    
    @property
    def display_size(self) -> str:
        if self.size < 1024:
            return f"{self.size} B"
        elif self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        return f"{self.size / (1024 * 1024):.1f} MB"
    
    @property
    def preview(self) -> str:
        """Short preview of content."""
        text = self.content.replace("\n", " ")
        return text[:60] + ("..." if len(text) > 60 else "")


# ---------------------------------------------------------------------------
# Clipboard manager
# ---------------------------------------------------------------------------

class ClipboardManager:
    """Clipboard history manager.
    
    Parameters
    ----------
    max_history : int
        Maximum number of entries to keep.
    """
    
    def __init__(self, max_history: int = 100):
        self._entries: List[ClipboardEntry] = []
        self._max_history = max_history
        self._search_query: str = ""
        self._selected_index: int = 0
        self._visible: bool = False
        self._on_copy: List[Callable] = []
        self._on_paste: List[Callable] = []
        self._on_clear: List[Callable] = []
    
    # -- Copy/paste operations ---------------------------------------------
    
    def copy(self, content: str, content_type="text",
             source_app: str = "", label: str = "") -> ClipboardEntry:
        """Copy content to clipboard history."""
        if not content:
            return None
        
        # Normalize content_type to enum
        if isinstance(content_type, str):
            try:
                content_type = ClipboardType(content_type)
            except ValueError:
                content_type = ClipboardType.TEXT
        
        # Check for duplicate
        for entry in self._entries:
            if entry.content == content and not entry.pinned:
                entry.timestamp = time.time()
                entry.use_count += 1
                self._move_to_top(entry.id)
                return entry
        
        # Create new entry
        entry = ClipboardEntry(
            id=str(uuid.uuid4())[:8],
            content=content,
            content_type=content_type,
            source_app=source_app,
            label=label,
        )
        
        self._entries.insert(0, entry)
        self._prune()
        
        for cb in self._on_copy:
            try:
                cb("copied", entry)
            except TypeError:
                cb(entry)
        
        return entry
    
    def paste(self, entry_id: str) -> Optional[str]:
        """Paste content by entry ID."""
        for entry in self._entries:
            if entry.id == entry_id:
                entry.use_count += 1
                entry.timestamp = time.time()
                self._move_to_top(entry_id)
                for cb in self._on_paste:
                    cb(entry)
                return entry.content
        return None
    
    def paste_last(self) -> Optional[str]:
        """Paste the most recent unpinned entry."""
        for entry in self._entries:
            if not entry.pinned:
                return self.paste(entry.id)
        return None
    
    def remove(self, entry_id: str) -> bool:
        """Remove an entry."""
        for i, entry in enumerate(self._entries):
            if entry.id == entry_id:
                self._entries.pop(i)
                return True
        return False
    
    def clear(self) -> int:
        """Clear all non-pinned entries. Returns count of removed entries."""
        count = len(self._entries) - self.pinned_count
        self._entries = [e for e in self._entries if e.pinned]
        for cb in self._on_clear:
            cb()
        return count
    
    def clear_all(self) -> int:
        """Clear everything including pinned."""
        count = len(self._entries)
        self._entries.clear()
        for cb in self._on_clear:
            cb()
        return count
    
    def pin(self, entry_id: str) -> bool:
        """Pin an entry to prevent auto-pruning."""
        for entry in self._entries:
            if entry.id == entry_id:
                entry.pinned = not entry.pinned
                return entry.pinned
        return False
    
    def _move_to_top(self, entry_id: str) -> None:
        """Move an entry to the top of the list."""
        for i, entry in enumerate(self._entries):
            if entry.id == entry_id:
                self._entries.pop(i)
                self._entries.insert(0, entry)
                break
    
    def _prune(self) -> None:
        """Remove oldest unpinned entries if over limit."""
        while len(self._entries) > self._max_history:
            # Find oldest unpinned entry
            oldest_idx = -1
            for i, entry in enumerate(self._entries):
                if not entry.pinned:
                    if oldest_idx == -1 or entry.timestamp < self._entries[oldest_idx].timestamp:
                        oldest_idx = i
            if oldest_idx >= 0:
                self._entries.pop(oldest_idx)
            else:
                break
    
    # -- Search/filter -----------------------------------------------------
    
    def set_search(self, query: str) -> None:
        """Set search filter."""
        self._search_query = query
        self._selected_index = 0
    
    @property
    def search_query(self) -> str:
        return self._search_query
    
    @property
    def filtered_entries(self) -> List[ClipboardEntry]:
        """Get entries matching the search query."""
        if not self._search_query:
            return list(self._entries)
        
        query = self._search_query.lower()
        return [e for e in self._entries
                if query in e.content.lower() or query in e.label.lower()
                or query in e.source_app.lower()]
    
    # -- Navigation --------------------------------------------------------
    
    def show(self) -> None:
        self._visible = True
        self._selected_index = 0
    
    def hide(self) -> None:
        self._visible = False
    
    def toggle(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()
    
    @property
    def is_visible(self) -> bool:
        return self._visible
    
    def move_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)
    
    def move_down(self) -> None:
        entries = self.filtered_entries
        self._selected_index = min(len(entries) - 1, self._selected_index + 1)
    
    def select(self) -> Optional[ClipboardEntry]:
        """Select the current entry."""
        entries = self.filtered_entries
        if 0 <= self._selected_index < len(entries):
            return entries[self._selected_index]
        return None
    
    @property
    def selected_index(self) -> int:
        return self._selected_index
    
    def handle_key(self, key: str) -> str:
        """Handle keyboard input."""
        if not self._visible:
            return ""
        
        if key == "Up":
            self.move_up()
            return "navigate"
        elif key == "Down":
            self.move_down()
            return "navigate"
        elif key in ("Enter", "Return"):
            entry = self.select()
            if entry:
                self.paste(entry.id)
                return f"paste:{entry.id}"
            return ""
        elif key == "Escape":
            self.hide()
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
    
    # -- Properties --------------------------------------------------------
    
    @property
    def entries(self) -> List[ClipboardEntry]:
        return list(self._entries)
    
    @property
    def entry_count(self) -> int:
        return len(self._entries)
    
    @property
    def pinned_count(self) -> int:
        return sum(1 for e in self._entries if e.pinned)
    
    def find_entry(self, entry_id: str) -> Optional[ClipboardEntry]:
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        return None
    
    def get_recent(self, count: int = 5) -> List[ClipboardEntry]:
        return self._entries[:count]
    
    # -- Callbacks ---------------------------------------------------------
    
    def on_copy(self, callback: Callable) -> None:
        self._on_copy.append(callback)
    
    def on_paste(self, callback: Callable) -> None:
        self._on_paste.append(callback)
    
    def on_clear(self, callback: Callable) -> None:
        self._on_clear.append(callback)
    

    # -- Backward compatibility (used by old tests) --
    
    @property
    def count(self) -> int:
        """Alias for entry_count."""
        return len(self._entries)
    
    def paste(self, entry_id: str = None) -> Optional[str]:
        """Paste by entry ID or last entry."""
        if entry_id is None:
            return self.paste_last()
        return self._paste_by_id(entry_id)
    
    def _paste_by_id(self, entry_id: str) -> Optional[str]:
        """Internal paste by ID."""
        for entry in self._entries:
            if entry.id == entry_id:
                entry.use_count += 1
                entry.timestamp = time.time()
                self._move_to_top(entry_id)
                for cb in self._on_paste:
                    cb(entry)
                return entry.content
        return None
    
    def paste_entry(self, entry_id: str) -> Optional[str]:
        """Paste by entry ID (compatibility alias)."""
        return self._paste_by_id(entry_id)
    
    @property
    def current_text(self) -> Optional[str]:
        """Get the current clipboard text."""
        if self._entries:
            return self._entries[0].content
        return None
    
    def search(self, query: str) -> List["ClipboardEntry"]:
        """Search clipboard entries (compatibility alias)."""
        old_query = self._search_query
        self._search_query = query
        results = self.filtered_entries
        self._search_query = old_query
        return results
    
    def delete(self, entry_id: str) -> bool:
        """Delete an entry (compatibility alias)."""
        return self.remove(entry_id)
    
    def unpin(self, entry_id: str) -> bool:
        """Unpin an entry."""
        for entry in self._entries:
            if entry.id == entry_id:
                entry.pinned = False
                return True
        return False
    
    def get_entry(self, entry_id: str) -> Optional["ClipboardEntry"]:
        """Get entry by ID (compatibility alias)."""
        return self.find_entry(entry_id)
    
    def by_type(self, content_type) -> List["ClipboardEntry"]:
        """Get entries by type."""
        if isinstance(content_type, str):
            try:
                content_type = ClipboardType(content_type)
            except ValueError:
                return []
        return [e for e in self._entries if e.content_type == content_type]
    
    def on_event(self, callback: Callable) -> None:
        """Register event callback (compatibility alias).
        
        Wraps callback to receive (event_type_str, entry) for both copy and paste.
        """
        # Wrap old-style (type_str, entry) callbacks into the copy callback
        def _copy_wrapper(entry):
            try:
                callback("copied", entry)
            except TypeError:
                callback(entry)
        self._on_copy.append(_copy_wrapper)
        
        def _paste_wrapper(entry):
            try:
                callback("pasted", entry)
            except TypeError:
                callback(entry)
        self._on_paste.append(_paste_wrapper)


    @property
    def visible(self) -> bool:
        """Alias for is_visible."""
        return self._visible
    
    @property
    def pinned_entries(self) -> List["ClipboardEntry"]:
        """Get pinned entries."""
        return [e for e in self._entries if e.pinned]
    
    def pinned_entries(self) -> List["ClipboardEntry"]:
        """Get pinned entries (method alias)."""
        return [e for e in self._entries if e.pinned]
    
    def recent(self, count: int = 5) -> List["ClipboardEntry"]:
        """Get recent entries."""
        return self.get_recent(count)

    def __repr__(self) -> str:
        return (
            f"ClipboardManager(entries={len(self._entries)}, "
            f"pinned={self.pinned_count}, "
            f"query='{self._search_query}')"
        )
