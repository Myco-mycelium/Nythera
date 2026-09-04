"""
Nyrqis OS - Clipboard Manager
History, snippets, and cross-device sync.
"""

import time
import hashlib
import random
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class ClipboardType(Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    HTML = "html"
    RICH_TEXT = "rich_text"
    COLOR = "color"
    CODE = "code"


class SnippetCategory(Enum):
    CODE = "code"
    TEXT = "text"
    EMAIL = "email"
    URL = "url"
    OTHER = "other"


class SyncStatus(Enum):
    DISABLED = "disabled"
    SYNCED = "synced"
    PENDING = "pending"
    CONFLICT = "conflict"
    ERROR = "error"


@dataclass
class ClipboardEntry:
    content: str
    entry_type: ClipboardType = ClipboardType.TEXT
    source: str = ""
    timestamp: float = 0.0
    pinned: bool = False

    @property
    def is_pinned(self) -> bool:
        return self.pinned
    tags: List[str] = field(default_factory=list)
    language: str = ""
    entry_id: str = ""

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if not self.entry_id:
            self.entry_id = str(uuid.uuid4())[:8]

    @property
    def preview(self) -> str:
        lines = self.content.split("\n")
        first = lines[0][:80]
        if len(lines) > 1:
            first += f" (+{len(lines) - 1} lines)"
        return first

    @property
    def size_str(self) -> str:
        size = len(self.content.encode("utf-8"))
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    @property
    def line_count(self) -> int:
        return len(self.content.split("\n"))

    @property
    def size_bytes(self) -> int:
        return getattr(self, '_size_bytes', len(self.content.encode("utf-8")))

    @size_bytes.setter
    def size_bytes(self, v: int):
        self._size_bytes = v

    @property
    def size_display(self) -> str:
        s = self.size_bytes
        if s < 1024:
            return f"{s} B"
        elif s < 1024 * 1024:
            return f"{s / 1024:.1f} KB"
        return f"{s / (1024 * 1024):.1f} MB"

    @property
    def type_icon(self) -> str:
        icons = {
            ClipboardType.TEXT: "📝", ClipboardType.CODE: "💻",
            ClipboardType.IMAGE: "🖼️", ClipboardType.COLOR: "🎨",
            ClipboardType.HTML: "🌐", ClipboardType.FILE: "📁",
            ClipboardType.RICH_TEXT: "📄",
        }
        return icons.get(self.entry_type, "?")

    @property
    def time_ago(self) -> str:
        delta = time.time() - self.timestamp
        if delta < 60:
            return f"{delta:.0f}s ago"
        elif delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.0f}h ago"
        return f"{delta / 86400:.0f}d ago"


@dataclass
class Snippet:
    name: str
    content: str
    category: SnippetCategory = SnippetCategory.TEXT
    hotkey: str = ""
    tags: List[str] = field(default_factory=list)
    use_count: int = 0
    created_at: float = 0.0
    description: str = ""
    language: str = ""
    snippet_id: str = ""

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()
        if not self.snippet_id:
            self.snippet_id = str(uuid.uuid4())[:8]

    @property
    def preview(self) -> str:
        lines = self.content.split("\n")
        first = lines[0][:80]
        if len(lines) > 1:
            first += f" (+{len(lines) - 1} lines)"
        return first

    @property
    def icon(self) -> str:
        icons = {
            SnippetCategory.CODE: "💻",
            SnippetCategory.TEXT: "📝",
            SnippetCategory.EMAIL: "📧",
            SnippetCategory.URL: "🔗",
            SnippetCategory.OTHER: "📌",
        }
        return icons.get(self.category, "📌")

    @property
    def display(self) -> str:
        parts = [self.name]
        if self.hotkey:
            parts.append(self.hotkey)
        return " ".join(parts)


class ClipboardManager:
    def __init__(self):
        self._history: List[ClipboardEntry] = []
        self._snippets: List[Snippet] = []
        self._view_mode: str = "history"
        self._selected_index: int = 0
        self._search_query: str = ""
        self._auto_clear_minutes: int = 0
        self._last_clear_check: float = time.time()
        self._total_copies: int = 0
        self._total_pastes: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        sample_entries = [
            ("Hello from Nyrqis! Welcome to the clipboard manager.",
             ClipboardType.TEXT, "nyrqis-shell", 120, False, ["hello", "welcome"]),
            ("def calculate_fibonacci(n):\n    if n <= 1:\n        return n",
             ClipboardType.CODE, "code-server", 180, True, ["python", "algorithm"], "python"),
            ("def calculate_fibonacci(n):\n    if n <= 1:\n        return n\n    return calculate_fibonacci(n-1) + calculate_fibonacci(n-2)",
             ClipboardType.CODE, "code-server", 240, True, ["python", "algorithm"], "python"),
            ("https://github.com/Myco-mycelium/Nythera",
             ClipboardType.TEXT, "firefox", 60, False, ["url", "project"]),
            ("The Nyrqis OS compositor uses a Wayland-based architecture with hardware-accelerated rendering via Vulkan and GBM buffer management.",
             ClipboardType.TEXT, "nyrqis-shell", 1800, False, ["docs", "architecture"]),
            ("#00ff88",
             ClipboardType.COLOR, "gimp", 300, False, ["color", "green"]),
            ("SELECT u.username, u.email, COUNT(s.id) as session_count\nFROM users u\nLEFT JOIN sessions s ON u.id = s.user_id\nWHERE u.is_active = true\nGROUP BY u.id\nORDER BY session_count DESC\nLIMIT 10;",
             ClipboardType.CODE, "db-client", 2400, True, ["sql", "query"], "sql"),
            ("mkdir -p /opt/nyrqis/{bin,lib,share}\ncp -r target/release/* /opt/nyrqis/bin/\nldconfig",
             ClipboardType.CODE, "terminal", 3000, False, ["bash", "deploy"], "bash"),
            ("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQD...",
             ClipboardType.TEXT, "terminal", 3600, False, ["ssh", "key"]),
        ]
        for content, ctype, app, age_s, pinned, tags, *lang in sample_entries:
            lang_str = lang[0] if lang else ""
            self._history.append(ClipboardEntry(
                content=content, entry_type=ctype, source=app,
                timestamp=now - age_s, pinned=pinned, tags=tags,
                language=lang_str,
            ))

        self._snippets = [
            Snippet(name="Git Push", content="git add -A && git commit -m \"$MSG\" && git push origin main",
                    hotkey="Ctrl+Shift+G", 
                    category="Git",
                    tags=["git", "deploy"], use_count=0),
            Snippet(name="Python Boilerplate", content="#!/usr/bin/env python3\n\nimport sys\n\ndef main():\n    print('Hello, World!')\n\nif __name__ == '__main__':\n    main()",
                    hotkey="Ctrl+Shift+P", 
                    category="Code",
                    tags=["python", "template"], use_count=0),
            Snippet(name="SSH Tunnel", content="ssh -L $LOCAL_PORT:localhost:$REMOTE_PORT $USER@$HOST -N",
                    hotkey="Ctrl+Shift+S", 
                    category="Code",
                    tags=["ssh", "tunnel"], use_count=0),
            Snippet(name="Nyrqis Build", content="cd /opt/Nyrqis && cargo build --release 2>&1 | tee build.log",
                    hotkey="Ctrl+Shift+B", 
                    category="Code",
                    tags=["nyrqis", "build"], use_count=0),
            Snippet(name="Lorem Ipsum", content="Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
                    hotkey="", 
                    category="Text",
                    tags=["placeholder", "text"], use_count=0),
            Snippet(name="Docker Compose", content="version: '3.8'\nservices:\n  app:\n    build: .\n    ports:\n      - '8080:80'\n    volumes:\n      - .:/app\n    environment:\n      - NODE_ENV=development",
                    hotkey="Ctrl+Shift+D", 
                    category="Code",
                    tags=["docker", "compose"], use_count=0),
            Snippet(name="Color Palette", content="--primary: #1a1a2e\n--secondary: #16213e\n--accent: #0f3460\n--highlight: #e94560",
                    hotkey="", 
                    category="Design",
                    tags=["css", "colors"], use_count=0),
            Snippet(name="SQL Create User", content="CREATE USER nyrqis WITH PASSWORD 'changeme';\nGRANT ALL PRIVILEGES ON DATABASE nyrqis_prod TO nyrqis;\nALTER USER nyrqis CREATEDB;",
                    hotkey="", 
                    category="Code",
                    tags=["sql", "user"], use_count=0),
        ]

    @property
    def history_count(self) -> int:
        return len(self._history)

    @property
    def snippet_count(self) -> int:
        return len(self._snippets)

    @property
    def pinned_count(self) -> int:
        return sum(1 for e in self._history if e.pinned)

    @property
    def total_copies(self) -> int:
        return self._total_copies

    @property
    def total_pastes(self) -> int:
        return self._total_pastes

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def copy(self, content: str, entry_type: ClipboardType = ClipboardType.TEXT,
             source: str = "", language: str = "") -> ClipboardEntry:
        entry = ClipboardEntry(content=content, entry_type=entry_type,
                               source=source, language=language)
        self._history.insert(0, entry)
        self._total_copies += 1
        return entry

    def paste(self, entry_id_or_index=None) -> Optional[str]:
        if entry_id_or_index is None:
            if self._history:
                self._total_pastes += 1
                return self._history[0].content
            return None
        if isinstance(entry_id_or_index, int):
            if 0 <= entry_id_or_index < len(self._history):
                self._total_pastes += 1
                return self._history[entry_id_or_index].content
            return None
        for e in self._history:
            if e.entry_id == entry_id_or_index:
                self._total_pastes += 1
                return e.content
        return None

    def delete_entry(self, entry_id_or_index) -> bool:
        if isinstance(entry_id_or_index, int):
            if 0 <= entry_id_or_index < len(self._history):
                del self._history[entry_id_or_index]
                return True
            return False
        for i, e in enumerate(self._history):
            if e.entry_id == entry_id_or_index:
                del self._history[i]
                return True
        return False

    def toggle_pin(self, entry_id: str) -> bool:
        for e in self._history:
            if e.entry_id == entry_id:
                e.pinned = not e.pinned
                return True
        return False

    def clear_history(self, keep_pinned: bool = False) -> int:
        if keep_pinned:
            before = len(self._history)
            self._history = [e for e in self._history if e.pinned]
            return before - len(self._history)
        else:
            count = len(self._history)
            self._history.clear()
            return count

    def create_snippet(self, name: str, content: str, **kwargs) -> Snippet:
        if "shortcut" in kwargs:
            kwargs["hotkey"] = kwargs.pop("shortcut")
        if "category" in kwargs and isinstance(kwargs["category"], str):
            kwargs["category"] = SnippetCategory.OTHER
        snippet = Snippet(name=name, content=content, **kwargs)
        self._snippets.append(snippet)
        return snippet

    def delete_snippet(self, snippet_id: str) -> bool:
        for i, s in enumerate(self._snippets):
            if s.snippet_id == snippet_id:
                del self._snippets[i]
                return True
        return False

    def use_snippet(self, snippet_id_or_name: str) -> Optional[str]:
        for s in self._snippets:
            if s.snippet_id == snippet_id_or_name or s.name == snippet_id_or_name:
                s.use_count += 1
                return s.content
        return None

    def search_snippets(self, query: str) -> List[Snippet]:
        q = query.lower()
        return [s for s in self._snippets if q in s.name.lower()
                or q in s.content.lower() or q in " ".join(s.tags).lower()]

    def set_view(self, view: str):
        self._view_mode = view

    def set_search(self, query: str):
        self._search_query = query

    def set_auto_clear(self, minutes: int):
        self._auto_clear_minutes = minutes

    def check_auto_clear(self) -> int:
        """Check and clear old non-pinned entries. Returns count cleared."""
        if self._auto_clear_minutes <= 0:
            return 0
        now = time.time()
        cutoff = now - (self._auto_clear_minutes * 60)
        cleared = 0
        remaining = []
        for e in self._history:
            if e.pinned or e.timestamp > cutoff:
                remaining.append(e)
            else:
                cleared += 1
        self._history = remaining
        self._last_clear_check = now
        return cleared

    def get_history(self) -> List[ClipboardEntry]:
        if self._search_query:
            q = self._search_query.lower()
            return [e for e in self._history if q in e.content.lower()
                    or q in e.source.lower() or q in " ".join(e.tags).lower()]
        return list(self._history)

    def select_down(self):
        if self._selected_index < len(self._history) - 1:
            self._selected_index += 1

    def select_up(self):
        if self._selected_index > 0:
            self._selected_index -= 1

    def render_history(self) -> List[str]:
        lines = ["=== Clipboard History ==="]
        for i, e in enumerate(self._history):
            pin = "📌 " if e.pinned else "   "
            lines.append(f"{pin}{i}: {e.preview}")
        return lines

    def render_snippets(self) -> List[str]:
        lines = ["=== Snippets ==="]
        for s in self._snippets:
            lines.append(f"  {s.icon} {s.name} ({s.hotkey or 'no hotkey'})")
        return lines

    def render_settings(self) -> List[str]:
        lines = ["=== Clipboard Settings ==="]
        lines.append(f"  Auto-clear: {'Every ' + str(self._auto_clear_minutes) + ' min' if self._auto_clear_minutes else 'Disabled'}")
        lines.append(f"  History: {len(self._history)} entries")
        lines.append(f"  Snippets: {len(self._snippets)}")
        return lines

    # -- Backward-compatible API --

    @property
    def devices(self):
        if not hasattr(self, '_devices'):
            self._devices = [
                type('SyncDevice', (), {'name': 'Nyrqis Desktop', 'status': 'synced', 'entries_synced': 12})(),
                type('SyncDevice', (), {'name': 'Framework Laptop', 'status': 'synced', 'entries_synced': 12})(),
                type('SyncDevice', (), {'name': 'iPhone 15 Pro', 'status': 'pending', 'entries_synced': 8})(),
            ]
        return self._devices

    @property
    def snippets(self):
        return self._snippets

    @property
    def history(self):
        return self._history

    @property
    def current_entry(self):
        return self._history[0] if self._history else None

    def pin_entry(self, entry_id_or_index):
        if isinstance(entry_id_or_index, int):
            if 0 <= entry_id_or_index < len(self._history):
                e = self._history[entry_id_or_index]
                e.pinned = True
                return True
            return False
        return self.toggle_pin(entry_id_or_index)

    def add_snippet(self, name, content, **kwargs):
        return self.create_snippet(name, content, **kwargs)

    def get_pinned(self):
        return [e for e in self._history if e.pinned]

    def filter_by_type(self, entry_type):
        if entry_type is None:
            return list(self._history)
        return [e for e in self._history if e.entry_type == entry_type]

    def get_snippets_by_category(self):
        cats = {}
        for s in self._snippets:
            cat = str(s.category) if s.category else 'General'
            cats.setdefault(cat, []).append(s)
        return cats

    def get_stats(self):
        class Stats:
            def __init__(self, total, size, pinned, snippets):
                self.total_entries = total
                self.total_size_bytes = size
                self.pinned_count = pinned
                self.snippets_count = snippets
        return Stats(len(self._history),
                     sum(len(e.content.encode('utf-8')) for e in self._history),
                     self.pinned_count, self.snippet_count)

    def search(self, query):
        q = query.lower()
        return [e for e in self._history if q in e.content.lower()
                or q in e.source.lower() or q in " ".join(e.tags).lower()]

    def handle_key(self, key: str) -> str:
        if key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "Escape":
            return "back"
        elif key == "a":
            return "toggle_auto_clear"
        return "noop"
