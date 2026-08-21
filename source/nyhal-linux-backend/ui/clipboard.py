#!/usr/bin/env python3
"""clipboard — Nyrqis clipboard manager.

A clipboard manager that demonstrates the full Nyrqis stack:

- Copy/paste history with timestamps
- Search through clipboard history
- Pin important entries
- Delete old entries
- Maximum history size with auto-pruning
- Keyboard shortcut support (Ctrl+Shift+V for history)

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ClipboardEntry:
    """A single clipboard entry."""
    id: str
    content: str
    content_type: str = "text"    # text, image, file
    timestamp: float = field(default_factory=time.time)
    pinned: bool = False
    source: str = ""              # app or context that copied it
    preview: str = ""             # truncated preview

    def __post_init__(self):
        if not self.preview:
            self.preview = self.content[:100] + ("..." if len(self.content) > 100 else "")


class ClipboardManager:
    """Nyrqis clipboard manager.

    Parameters
    ----------
    max_history : int
        Maximum number of entries to keep.
    """

    def __init__(self, max_history: int = 50) -> None:
        self._entries: List[ClipboardEntry] = []
        self._max_history = max_history
        self._next_id = 1
        self._current: Optional[ClipboardEntry] = None
        self._callbacks: List[Callable] = []
        self._visible = False

    # -- Copy/Paste ---------------------------------------------------

    def copy(self, content: str, source: str = "",
             content_type: str = "text") -> ClipboardEntry:
        """Copy content to the clipboard.

        Deduplicates: if the same content is already at the top,
        it's not added again.
        """
        if not content:
            return self._current or ClipboardEntry(id="empty", content="")

        # Deduplicate against the most recent entry
        if (self._entries
                and self._entries[0].content == content
                and self._entries[0].source == source):
            self._entries[0].timestamp = time.time()
            self._current = self._entries[0]
            self._dispatch("updated", self._current)
            return self._current

        entry = ClipboardEntry(
            id=f"clip-{self._next_id}",
            content=content,
            content_type=content_type,
            source=source,
        )
        self._next_id += 1
        self._entries.insert(0, entry)
        self._current = entry

        # Prune old entries
        while len(self._entries) > self._max_history:
            # Don't prune pinned entries
            for i in range(len(self._entries) - 1, -1, -1):
                if not self._entries[i].pinned:
                    self._entries.pop(i)
                    break

        self._dispatch("copied", entry)
        return entry

    def paste(self) -> Optional[str]:
        """Paste (return) the most recent clipboard content."""
        if self._current:
            self._dispatch("pasted", self._current)
            return self._current.content
        return None

    def paste_entry(self, entry_id: str) -> Optional[str]:
        """Paste a specific entry by ID."""
        entry = self.get_entry(entry_id)
        if entry:
            self._current = entry
            self._dispatch("pasted", entry)
            return entry.content
        return None

    @property
    def current(self) -> Optional[ClipboardEntry]:
        return self._current

    @property
    def current_text(self) -> str:
        return self._current.content if self._current else ""

    # -- History management -------------------------------------------

    @property
    def entries(self) -> List[ClipboardEntry]:
        """All clipboard entries (newest first)."""
        return list(self._entries)

    @property
    def count(self) -> int:
        return len(self._entries)

    def get_entry(self, entry_id: str) -> Optional[ClipboardEntry]:
        """Find an entry by ID."""
        for e in self._entries:
            if e.id == entry_id:
                return e
        return None

    def search(self, query: str) -> List[ClipboardEntry]:
        """Search clipboard history by content."""
        if not query:
            return self._entries
        q = query.lower()
        return [e for e in self._entries
                if q in e.content.lower() or q in e.source.lower()]

    def pin(self, entry_id: str) -> bool:
        """Pin an entry so it's not auto-pruned."""
        entry = self.get_entry(entry_id)
        if entry:
            entry.pinned = True
            self._dispatch("pinned", entry)
            return True
        return False

    def unpin(self, entry_id: str) -> bool:
        """Unpin an entry."""
        entry = self.get_entry(entry_id)
        if entry:
            entry.pinned = False
            self._dispatch("unpinned", entry)
            return True
        return False

    def delete(self, entry_id: str) -> bool:
        """Delete an entry from history."""
        for i, e in enumerate(self._entries):
            if e.id == entry_id:
                entry = self._entries.pop(i)
                if self._current and self._current.id == entry_id:
                    self._current = self._entries[0] if self._entries else None
                self._dispatch("deleted", entry)
                return True
        return False

    def clear(self) -> int:
        """Clear all non-pinned entries.  Returns count deleted."""
        count = 0
        remaining = [e for e in self._entries if e.pinned]
        deleted = len(self._entries) - len(remaining)
        self._entries = remaining
        if self._current and self._current not in self._entries:
            self._current = self._entries[0] if self._entries else None
        self._dispatch("cleared", None)
        return deleted

    # -- Queries ------------------------------------------------------

    def recent(self, count: int = 10) -> List[ClipboardEntry]:
        """Get the N most recent entries."""
        return self._entries[:count]

    def pinned_entries(self) -> List[ClipboardEntry]:
        """Get all pinned entries."""
        return [e for e in self._entries if e.pinned]

    def by_type(self, content_type: str) -> List[ClipboardEntry]:
        """Get entries by content type."""
        return [e for e in self._entries if e.content_type == content_type]

    # -- Visibility ---------------------------------------------------

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def toggle(self) -> bool:
        self._visible = not self._visible
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Callbacks ----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        """Register a callback.  Signature: ``(event_type, entry) -> None``"""
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, entry: Optional[ClipboardEntry]) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, entry)
            except Exception:
                pass

    def _log(self, msg: str) -> None:
        import logging
        logging.getLogger(__name__).info("[Clipboard] %s", msg)


__all__ = ["ClipboardManager", "ClipboardEntry"]
