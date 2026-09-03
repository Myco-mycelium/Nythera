"""
Nyrqis Clipboard Manager — advanced clipboard with history and snippets.

Features:
- Clipboard history with timestamps and sources
- Pin frequently used items
- Text snippets with categories and hotkeys
- Code snippet library with syntax highlighting labels
- Search across history and snippets
- Auto-clear timer for security
- Copy/paste formatting options
- Clipboard statistics
- Import/export snippets
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class ClipboardType(Enum):
    TEXT = "text"
    CODE = "code"
    LINK = "link"
    IMAGE = "image"
    FILE = "file"


class SnippetCategory(Enum):
    GENERAL = "General"
    CODE = "Code"
    EMAIL = "Email"
    URL = "URL"
    TEMPLATE = "Template"
    SQL = "SQL"
    SHELL = "Shell"
    MARKDOWN = "Markdown"


SNIPPET_CATEGORY_ICONS = {
    SnippetCategory.GENERAL: "📝",
    SnippetCategory.CODE: "💻",
    SnippetCategory.EMAIL: "📧",
    SnippetCategory.URL: "🔗",
    SnippetCategory.TEMPLATE: "📋",
    SnippetCategory.SQL: "🗄️",
    SnippetCategory.SHELL: "🖥️",
    SnippetCategory.MARKDOWN: "📖",
}


@dataclass
class ClipboardEntry:
    """A clipboard history entry."""
    content: str
    entry_type: ClipboardType = ClipboardType.TEXT
    source: str = ""
    pinned: bool = False
    timestamp: float = field(default_factory=time.time)
    entry_id: str = ""
    use_count: int = 0
    language: str = ""

    def __post_init__(self):
        if not self.entry_id:
            self.entry_id = hashlib.md5(f"{self.content}{self.timestamp}".encode()).hexdigest()[:8]

    @property
    def preview(self) -> str:
        first_line = self.content.split("\n")[0]
        return first_line[:60] + "..." if len(first_line) > 60 else first_line

    @property
    def size_str(self) -> str:
        b = len(self.content.encode())
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"

    @property
    def line_count(self) -> int:
        return len(self.content.split("\n"))

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
class Snippet:
    """A saved text snippet."""
    name: str
    content: str
    category: SnippetCategory = SnippetCategory.GENERAL
    hotkey: str = ""
    tags: List[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    last_used: float = 0.0
    use_count: int = 0
    snippet_id: str = ""

    def __post_init__(self):
        if not self.snippet_id:
            self.snippet_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def preview(self) -> str:
        first_line = self.content.split("\n")[0]
        return first_line[:50] + "..." if len(first_line) > 50 else first_line

    @property
    def icon(self) -> str:
        return SNIPPET_CATEGORY_ICONS.get(self.category, "📝")

    @property
    def display(self) -> str:
        hotkey = f" [{self.hotkey}]" if self.hotkey else ""
        return f"{self.icon} {self.name}{hotkey}"

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.last_used if self.last_used else time.time() - self.created
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.last_used or self.created).strftime("%b %d")


# ─── Clipboard Manager ───────────────────────────────────────────────────


class ClipboardManager:
    """
    Advanced clipboard manager for Nyrqis OS.
    """

    def __init__(self):
        self._history: List[ClipboardEntry] = []
        self._snippets: List[Snippet] = []
        self._max_history: int = 200
        self._auto_clear_seconds: int = 0  # 0 = disabled
        self._last_clear_check: float = time.time()

        # View state
        self._view_mode: str = "history"  # history, snippets, settings
        self._selected_index: int = 0
        self._search_query: str = ""
        self._filter_type: Optional[ClipboardType] = None
        self._filter_category: Optional[SnippetCategory] = None

        # Clipboard stats
        self._total_copies: int = 0
        self._total_pastes: int = 0

        # Callbacks
        self._on_copy: List[Callable] = []

        # Init sample data
        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        self._history = [
            ClipboardEntry("def hello():\n    print('Hello, Nyrqis!')", ClipboardType.CODE,
                           "terminal", False, now - 120, language="python"),
            ClipboardEntry("https://github.com/Myco-mycelium/Nythera", ClipboardType.LINK,
                           "browser", True, now - 300),
            ClipboardEntry("The quick brown fox jumps over the lazy dog.", ClipboardType.TEXT,
                           "editor", False, now - 600),
            ClipboardEntry("SELECT * FROM users WHERE active = 1 LIMIT 10;", ClipboardType.CODE,
                           "database_viewer", False, now - 900, language="sql"),
            ClipboardEntry("git commit -m 'Add new feature'\ngit push origin main", ClipboardType.CODE,
                           "terminal", False, now - 1800, language="shell"),
            ClipboardEntry("Meeting notes from sprint planning:\n- Finish UI components\n- Write tests\n- Update docs", ClipboardType.TEXT,
                           "notes", True, now - 3600),
            ClipboardEntry("user@nyrqis.os", ClipboardType.TEXT,
                           "settings", False, now - 7200),
            ClipboardEntry("npm install react react-dom", ClipboardType.CODE,
                           "terminal", False, now - 14400, language="shell"),
        ]

        self._snippets = [
            Snippet("Python Function Template", "def function_name(params):\n    \"\"\"Docstring.\"\"\"\n    pass",
                    SnippetCategory.CODE, "Ctrl+Shift+1", ["python", "template"]),
            Snippet("Git Commit", "git add -A && git commit -m ''",
                    SnippetCategory.SHELL, "Ctrl+Shift+2", ["git"]),
            Snippet("Email Signature", "---\nBest regards,\nUser\nNyrqis OS Team",
                    SnippetCategory.EMAIL, "Ctrl+Shift+3", ["email"]),
            Snippet("Markdown Table", "| Column 1 | Column 2 | Column 3 |\n|----------|----------|----------|\n| Data     | Data     | Data     |",
                    SnippetCategory.MARKDOWN, "", ["table"]),
            Snippet("SQL Select", "SELECT column1, column2\nFROM table_name\nWHERE condition\nORDER BY column1\nLIMIT 10;",
                    SnippetCategory.SQL, "", ["query"]),
            Snippet("API Response", '{\n  "status": "success",\n  "data": {},\n  "message": ""\n}',
                    SnippetCategory.CODE, "", ["json", "api"]),
            Snippet("SSH Command", "ssh user@server.nyrqis.os -p 22",
                    SnippetCategory.SHELL, "", ["ssh", "remote"]),
            Snippet("Docker Compose", "version: '3'\nservices:\n  app:\n    build: .\n    ports:\n      - '3000:3000'",
                    SnippetCategory.CODE, "", ["docker", "compose"]),
        ]

    # ── Clipboard Operations ──────────────────────────────────────────

    def copy(self, content: str, entry_type: ClipboardType = ClipboardType.TEXT,
             source: str = "", language: str = "") -> ClipboardEntry:
        """Copy content to clipboard."""
        entry = ClipboardEntry(
            content=content,
            entry_type=entry_type,
            source=source,
            language=language,
        )
        self._history.insert(0, entry)
        self._total_copies += 1

        if len(self._history) > self._max_history:
            self._history.pop()

        self._notify("copy", entry)
        return entry

    def paste(self, entry_id: str = None) -> Optional[str]:
        """Paste from clipboard history."""
        if entry_id:
            for entry in self._history:
                if entry.entry_id == entry_id:
                    entry.use_count += 1
                    self._total_pastes += 1
                    return entry.content
        # Paste most recent
        if self._history:
            self._history[0].use_count += 1
            self._total_pastes += 1
            return self._history[0].content
        return None

    def delete_entry(self, entry_id: str) -> bool:
        for i, entry in enumerate(self._history):
            if entry.entry_id == entry_id:
                self._history.pop(i)
                return True
        return False

    def toggle_pin(self, entry_id: str) -> bool:
        for entry in self._history:
            if entry.entry_id == entry_id:
                entry.pinned = not entry.pinned
                return entry.pinned
        return False

    def clear_history(self) -> int:
        count = len(self._history)
        self._history.clear()
        return count

    def get_history(self) -> List[ClipboardEntry]:
        entries = list(self._history)
        if self._filter_type:
            entries = [e for e in entries if e.entry_type == self._filter_type]
        if self._search_query:
            q = self._search_query.lower()
            entries = [e for e in entries if q in e.content.lower() or q in e.source.lower()]
        # Pinned first
        pinned = [e for e in entries if e.pinned]
        unpinned = [e for e in entries if not e.pinned]
        return pinned + unpinned

    # ── Snippet Operations ────────────────────────────────────────────

    def create_snippet(self, name: str, content: str,
                       category: SnippetCategory = SnippetCategory.GENERAL) -> Snippet:
        snippet = Snippet(name=name, content=content, category=category)
        self._snippets.append(snippet)
        return snippet

    def delete_snippet(self, snippet_id: str) -> bool:
        for i, snippet in enumerate(self._snippets):
            if snippet.snippet_id == snippet_id:
                self._snippets.pop(i)
                return True
        return False

    def use_snippet(self, snippet_id: str) -> Optional[str]:
        for snippet in self._snippets:
            if snippet.snippet_id == snippet_id:
                snippet.use_count += 1
                snippet.last_used = time.time()
                self._total_pastes += 1
                return snippet.content
        return None

    def search_snippets(self, query: str) -> List[Snippet]:
        if not query:
            return list(self._snippets)
        q = query.lower()
        return [s for s in self._snippets
                if q in s.name.lower() or q in s.content.lower() or
                any(q in tag for tag in s.tags)]

    def get_snippets(self) -> List[Snippet]:
        snippets = list(self._snippets)
        if self._filter_category:
            snippets = [s for s in snippets if s.category == self._filter_category]
        if self._search_query:
            snippets = self.search_snippets(self._search_query)
        return snippets

    # ── Auto-Clear ────────────────────────────────────────────────────

    def set_auto_clear(self, seconds: int) -> None:
        self._auto_clear_seconds = max(0, seconds)

    def check_auto_clear(self) -> int:
        """Check if auto-clear should trigger. Returns count of cleared items."""
        if self._auto_clear_seconds <= 0:
            return 0
        now = time.time()
        if now - self._last_clear_check >= self._auto_clear_seconds:
            self._last_clear_check = now
            cutoff = now - self._auto_clear_seconds
            before = len(self._history)
            self._history = [e for e in self._history if e.pinned or e.timestamp > cutoff]
            return before - len(self._history)
        return 0

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        if self._view_mode == "history":
            max_idx = len(self.get_history()) - 1
        else:
            max_idx = len(self.get_snippets()) - 1
        self._selected_index = min(max_idx, self._selected_index + 1)

    # ── Search ────────────────────────────────────────────────────────

    def set_search(self, query: str) -> None:
        self._search_query = query
        self._selected_index = 0

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Stats ─────────────────────────────────────────────────────────

    @property
    def total_copies(self) -> int:
        return self._total_copies

    @property
    def total_pastes(self) -> int:
        return self._total_pastes

    @property
    def history_count(self) -> int:
        return len(self._history)

    @property
    def snippet_count(self) -> int:
        return len(self._snippets)

    @property
    def pinned_count(self) -> int:
        return len([e for e in self._history if e.pinned])

    # ── Rendering ─────────────────────────────────────────────────────

    def render_history(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(f" 📋 Clipboard History ({self.history_count} items, {self.pinned_count} pinned)")
        lines.append("─" * width)

        entries = self.get_history()
        if not entries:
            lines.append("  No clipboard entries.")
        else:
            for i, entry in enumerate(entries[:20]):
                marker = "▸" if i == self._selected_index else " "
                pin = " 📌" if entry.pinned else ""
                type_icon = {"text": "📝", "code": "💻", "link": "🔗", "image": "🖼️", "file": "📁"}.get(entry.entry_type.value, "❓")
                lines.append(f"{marker} {type_icon} {entry.preview}{pin}")
                lines.append(f"   {entry.size_str} · {entry.line_count} lines · {entry.source} · {entry.time_ago}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Copy  P:Pin  Del:Delete  S:Search  T:Snippets")
        return lines

    def render_snippets(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(f" 💾 Snippets ({self.snippet_count})")
        lines.append("─" * width)

        snippets = self.get_snippets()
        if not snippets:
            lines.append("  No snippets.")
        else:
            for i, snippet in enumerate(snippets):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {snippet.display}")
                lines.append(f"   {snippet.preview}")
                lines.append(f"   Used {snippet.use_count}x · {snippet.time_ago}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Use  N:New  Del:Delete  H:History  /:Search")
        return lines

    def render_settings(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" ⚙️  Clipboard Settings")
        lines.append("─" * width)
        lines.append(f"  Auto-clear: {'Disabled' if self._auto_clear_seconds == 0 else f'{self._auto_clear_seconds}s'}")
        lines.append(f"  Max history: {self._max_history}")
        lines.append("")
        lines.append("  Statistics:")
        lines.append(f"    Total copies: {self._total_copies}")
        lines.append(f"    Total pastes: {self._total_pastes}")
        lines.append(f"    History size: {self.history_count}")
        lines.append(f"    Snippets: {self.snippet_count}")
        lines.append("─" * width)
        lines.append(" A:Auto-clear  Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        renderers = {
            "snippets": self.render_snippets,
            "settings": self.render_settings,
        }
        renderer = renderers.get(self._view_mode, self.render_history)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "snippets":
            return self._handle_snippets_key(key)
        elif self._view_mode == "settings":
            return self._handle_settings_key(key)
        return self._handle_history_key(key)

    def _handle_history_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            entries = self.get_history()
            if 0 <= self._selected_index < len(entries):
                self.paste(entries[self._selected_index].entry_id)
            return "paste"
        elif key == "p":
            entries = self.get_history()
            if 0 <= self._selected_index < len(entries):
                self.toggle_pin(entries[self._selected_index].entry_id)
            return "toggle_pin"
        elif key == "Delete":
            entries = self.get_history()
            if 0 <= self._selected_index < len(entries):
                self.delete_entry(entries[self._selected_index].entry_id)
            return "delete"
        elif key == "t":
            self._view_mode = "snippets"
            self._selected_index = 0
            return "snippets"
        elif key == "/":
            return "search"
        elif key == "Escape":
            self._search_query = ""
            return "clear_search"
        return None

    def _handle_snippets_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "history"
            self._selected_index = 0
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            snippets = self.get_snippets()
            if 0 <= self._selected_index < len(snippets):
                self.use_snippet(snippets[self._selected_index].snippet_id)
            return "use_snippet"
        elif key == "h":
            self._view_mode = "history"
            self._selected_index = 0
            return "history"
        return None

    def _handle_settings_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "history"
            return "back"
        elif key == "a":
            if self._auto_clear_seconds == 0:
                self._auto_clear_seconds = 300  # 5 minutes
            elif self._auto_clear_seconds == 300:
                self._auto_clear_seconds = 600
            elif self._auto_clear_seconds == 600:
                self._auto_clear_seconds = 3600
            else:
                self._auto_clear_seconds = 0
            return "toggle_auto_clear"
        return None

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_copy(self, cb: Callable) -> None:
        self._on_copy.append(cb)

    def _notify(self, event: str, *args) -> None:
        for cb in self._on_copy:
            try:
                cb(event, *args)
            except Exception:
                pass
