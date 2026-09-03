from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class ClipType(Enum):
    TEXT = "text"
    CODE = "code"
    LINK = "link"
    IMAGE = "image"
    FILE = "file"
    COLOR = "color"
    PASSWORD = "password"


class SyncStatus(Enum):
    SYNCED = "synced"
    PENDING = "pending"
    CONFLICT = "conflict"
    DISABLED = "disabled"


class SnippetCategory(Enum):
    GENERAL = "general"
    CODE = "code"
    EMAIL = "email"
    URL = "url"
    TEMPLATE = "template"
    SQL = "sql"
    SHELL = "shell"
    MARKDOWN = "markdown"


@dataclass
class ClipEntry:
    content: str
    clip_type: ClipType
    source: str
    timestamp: float
    is_pinned: bool = False
    is_favorite: bool = False
    use_count: int = 0
    tags: list = field(default_factory=list)
    device: str = "local"
    sync_status: SyncStatus = SyncStatus.SYNCED

    @property
    def preview(self) -> str:
        return self.content[:50] + "..." if len(self.content) > 50 else self.content

    @property
    def age_display(self) -> str:
        age = int((time.time() - self.timestamp) / 60)
        if age < 1:
            return "just now"
        if age < 60:
            return f"{age}m ago"
        hours = age // 60
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"

    @property
    def type_icon(self) -> str:
        icons = {"text": "📝", "code": "💻", "link": "🔗", "image": "🖼️", "file": "📁", "color": "🎨", "password": "🔐"}
        return icons.get(self.clip_type.value, "📝")


@dataclass
class Snippet:
    name: str
    content: str
    category: SnippetCategory
    shortcut: str = ""
    use_count: int = 0
    tags: list = field(default_factory=list)

    @property
    def preview(self) -> str:
        return self.content[:40] + "..." if len(self.content) > 40 else self.content


@dataclass
class SyncDevice:
    name: str
    device_type: str
    last_sync: float
    status: SyncStatus
    entries_synced: int = 0


class ClipboardPro:
    def __init__(self):
        self._entries: list[ClipEntry] = []
        self._selected: int = 0
        self._snippets: list[Snippet] = []
        self._selected_snippet: int = 0
        self._devices: list[SyncDevice] = []
        self._search_query: str = ""
        self._filter_type: Optional[ClipType] = None
        self._max_history: int = 500
        self._auto_clear_mins: int = 0
        self._sync_enabled: bool = True
        self._view: str = "history"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._entries = [
            ClipEntry("def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        yield a\n        a, b = b, a + b", ClipType.CODE, "vscode", now - 120, is_pinned=True, use_count=15, tags=["python"]),
            ClipEntry("https://github.com/Myco-mycelium/Nythera", ClipType.LINK, "firefox", now - 300, use_count=8),
            ClipEntry("Meeting notes from sprint review - Q3 progress looks good", ClipType.TEXT, "thunderbird", now - 600, use_count=3),
            ClipEntry("#FF6B6B", ClipType.COLOR, "gimp", now - 900, use_count=2),
            ClipEntry("ssh root@server.nyrqis.dev", ClipType.CODE, "terminal", now - 1200, use_count=22, tags=["ssh"]),
            ClipEntry("SELECT * FROM users WHERE active = true LIMIT 10;", ClipType.CODE, "dbeaver", now - 1800, use_count=5, tags=["sql"]),
            ClipEntry("git commit -m \"Fix compositor frame pacing\"", ClipType.CODE, "terminal", now - 2400, use_count=12, tags=["git"]),
            ClipEntry("/home/user/Documents/report-final-v3.pdf", ClipType.FILE, "nautilus", now - 3600, use_count=1),
            ClipEntry("pip install --upgrade nyrqis-core", ClipType.CODE, "terminal", now - 5400, use_count=7, tags=["pip"]),
            ClipEntry("The quick brown fox jumps over the lazy dog", ClipType.TEXT, "gedit", now - 7200, use_count=2),
            ClipEntry("S3cr3tP@ssw0rd!2026", ClipType.PASSWORD, "bitwarden", now - 10800, use_count=4, tags=["password"]),
            ClipEntry("docker compose up -d --build", ClipType.CODE, "terminal", now - 14400, use_count=18, tags=["docker"]),
        ]

        self._snippets = [
            Snippet("Python Function", "def function_name(param):\n    pass", SnippetCategory.CODE, "Ctrl+Shift+F"),
            Snippet("Git Commit", "git commit -m \"message\"", SnippetCategory.SHELL, "Ctrl+Shift+G"),
            Snippet("Email Signature", "---\nBest regards,\nNyrqis Dev Team\nhttps://nyrqis.dev", SnippetCategory.EMAIL, ""),
            Snippet("SQL Select", "SELECT column FROM table WHERE condition;", SnippetCategory.SQL, ""),
            Snippet("Markdown Table", "| Header | Header |\n|--------|--------|\n| Cell   | Cell   |", SnippetCategory.MARKDOWN, ""),
            Snippet("Dockerfile", "FROM ubuntu:24.04\nRUN apt update && apt install -y python3\nCOPY . /app\nCMD [\"python3\", \"main.py\"]", SnippetCategory.CODE, ""),
            Snippet("API Response", '{\n  "status": "success",\n  "data": {},\n  "message": ""\n}', SnippetCategory.CODE, ""),
        ]

        self._devices = [
            SyncDevice("nyrqis-workstation", "desktop", now - 60, SyncStatus.SYNCED, 12),
            SyncDevice("nyrqis-laptop", "laptop", now - 300, SyncStatus.SYNCED, 12),
            SyncDevice("nyrqis-phone", "phone", now - 1800, SyncStatus.PENDING, 10),
        ]

    @property
    def selected_entry(self) -> Optional[ClipEntry]:
        if 0 <= self._selected < len(self._entries):
            return self._entries[self._selected]
        return None

    @property
    def selected_snippet(self) -> Optional[Snippet]:
        if 0 <= self._selected_snippet < len(self._snippets):
            return self._snippets[self._selected_snippet]
        return None

    @property
    def total_entries(self) -> int:
        return len(self._entries)

    @property
    def pinned_count(self) -> int:
        return sum(1 for e in self._entries if e.is_pinned)

    @property
    def favorite_count(self) -> int:
        return sum(1 for e in self._entries if e.is_favorite)

    @property
    def total_copies(self) -> int:
        return sum(e.use_count for e in self._entries)

    def select(self, idx: int):
        if 0 <= idx < len(self._entries):
            self._selected = idx

    def select_snippet(self, idx: int):
        if 0 <= idx < len(self._snippets):
            self._selected_snippet = idx

    def copy_entry(self, idx: int) -> str:
        if 0 <= idx < len(self._entries):
            entry = self._entries[idx]
            entry.use_count += 1
            return entry.content
        return ""

    def pin_entry(self, idx: int):
        if 0 <= idx < len(self._entries):
            self._entries[idx].is_pinned = not self._entries[idx].is_pinned

    def delete_entry(self, idx: int) -> bool:
        if 0 <= idx < len(self._entries):
            self._entries.pop(idx)
            if self._selected >= len(self._entries):
                self._selected = max(0, len(self._entries) - 1)
            return True
        return False

    def search(self, query: str) -> list:
        self._search_query = query
        return [e for e in self._entries if query.lower() in e.content.lower() or query.lower() in " ".join(e.tags)]

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS CLIPBOARD PRO                                    ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        sync = "🟢 ON" if self._sync_enabled else "🔴 OFF"
        lines.append(f"  History: {self.total_entries}  📌 {self.pinned_count}  ⭐ {self.favorite_count}  📋 {self.total_copies} copies  Sync: {sync}")
        lines.append(f"  Max: {self._max_history}  Auto-clear: {self._auto_clear_mins}min  Devices: {len(self._devices)}")
        lines.append("")
        lines.append("  ── Clipboard History ──")
        for i, e in enumerate(self._entries[:12]):
            sel = "▶" if i == self._selected else " "
            pin = "📌" if e.is_pinned else " "
            fav = "⭐" if e.is_favorite else " "
            sync_icons = {"synced": "🟢", "pending": "🟡", "conflict": "🔴", "disabled": "⚪"}
            sync_icon = sync_icons.get(e.sync_status.value, "?")
            lines.append(f"  {sel}{pin}{fav} {e.type_icon} {e.source:<12s} {e.preview}")
            lines.append(f"    {e.age_display}  uses: {e.use_count}  {sync_icon} {e.device}")
        lines.append("")
        lines.append("  ── Snippets ──")
        for i, s in enumerate(self._snippets[:5]):
            sel = "▶" if i == self._selected_snippet else " "
            lines.append(f"  {sel} {s.name}  [{s.category.value}]  {s.shortcut or ''}  {s.preview}")
        lines.append("")
        lines.append("  ── Devices ──")
        for d in self._devices:
            sync_icons = {"synced": "🟢", "pending": "🟡", "conflict": "🔴", "disabled": "⚪"}
            lines.append(f"  {sync_icons.get(d.status.value, '?')} {d.name}  ({d.device_type})  {d.entries_synced} entries")
        lines.append("")
        lines.append("  [C]opy  [P]in  [F]avorite  [D]elete  [S]earch  [N]ew snippet  [/]filter")
        return lines

    def render_entry_detail(self) -> list:
        e = self.selected_entry
        if not e:
            return ["  No entry selected"]
        lines = []
        lines.append(f"  ── {e.clip_type.value.upper()} ──")
        lines.append(f"  Source: {e.source}")
        lines.append(f"  Device: {e.device}")
        lines.append(f"  Age: {e.age_display}")
        lines.append(f"  Uses: {e.use_count}")
        lines.append(f"  Pinned: {'Yes' if e.is_pinned else 'No'}")
        lines.append(f"  Favorite: {'Yes' if e.is_favorite else 'No'}")
        lines.append(f"  Sync: {e.sync_status.value}")
        if e.tags:
            lines.append(f"  Tags: {', '.join(e.tags)}")
        lines.append("")
        lines.append("  ── Content ──")
        for line in e.content.split("\n"):
            lines.append(f"  │ {line}")
        return lines
