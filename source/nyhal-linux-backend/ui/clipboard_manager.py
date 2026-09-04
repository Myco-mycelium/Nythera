"""
Nyrqis OS - Clipboard Manager
History, snippets, and cross-device sync.
"""

import time
import hashlib
import random
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
    source_app: str = ""
    timestamp: float = 0.0
    is_pinned: bool = False
    tags: List[str] = field(default_factory=list)
    size_bytes: int = 0
    language: str = ""
    device_name: str = ""
    sync_status: SyncStatus = SyncStatus.DISABLED
    access_count: int = 0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if self.size_bytes == 0:
            self.size_bytes = len(self.content.encode("utf-8"))

    @property
    def type_icon(self) -> str:
        icons = {
            ClipboardType.TEXT: "📝",
            ClipboardType.IMAGE: "🖼️",
            ClipboardType.FILE: "📁",
            ClipboardType.HTML: "🌐",
            ClipboardType.RICH_TEXT: "📄",
            ClipboardType.COLOR: "🎨",
            ClipboardType.CODE: "💻",
        }
        return icons.get(self.entry_type, "?")

    @property
    def sync_icon(self) -> str:
        icons = {
            SyncStatus.DISABLED: "⬜",
            SyncStatus.SYNCED: "🟢",
            SyncStatus.PENDING: "🟡",
            SyncStatus.CONFLICT: "🟠",
            SyncStatus.ERROR: "🔴",
        }
        return icons.get(self.sync_status, "?")

    @property
    def preview(self) -> str:
        lines = self.content.split("\n")
        first = lines[0][:80]
        if len(lines) > 1:
            first += f" (+{len(lines) - 1} lines)"
        return first

    @property
    def size_display(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MB"

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
    shortcut: str = ""
    category: str = "General"
    tags: List[str] = field(default_factory=list)
    use_count: int = 0
    created_at: float = 0.0
    last_used: float = 0.0
    description: str = ""
    language: str = ""

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    @property
    def preview(self) -> str:
        return self.content[:60].replace("\n", " ")

    @property
    def shortcut_display(self) -> str:
        return f"⚡ {self.shortcut}" if self.shortcut else ""


@dataclass
class SyncDevice:
    name: str
    device_type: str = ""  # laptop, desktop, phone, tablet
    os: str = ""
    last_sync: float = 0.0
    status: SyncStatus = SyncStatus.DISABLED
    entries_synced: int = 0
    ip_address: str = ""

    @property
    def status_icon(self) -> str:
        icons = {
            SyncStatus.DISABLED: "⬜",
            SyncStatus.SYNCED: "🟢",
            SyncStatus.PENDING: "🟡",
            SyncStatus.CONFLICT: "🟠",
            SyncStatus.ERROR: "🔴",
        }
        return icons.get(self.status, "?")


@dataclass
class ClipboardStats:
    total_entries: int = 0
    total_size_bytes: int = 0
    pinned_count: int = 0
    snippets_count: int = 0
    sync_devices: int = 0
    today_copies: int = 0
    today_pastes: int = 0


class ClipboardManager:
    def __init__(self):
        self.history: List[ClipboardEntry] = []
        self.snippets: List[Snippet] = []
        self.devices: List[SyncDevice] = []
        self.current_entry: Optional[ClipboardEntry] = None
        self.max_history: int = 500
        self.auto_sync: bool = True
        self.sync_enabled: bool = True
        self.search_query: str = ""
        self.filter_type: Optional[ClipboardType] = None
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        sample_entries = [
            ("def calculate_fibonacci(n):\n    if n <= 1:\n        return n\n    return calculate_fibonacci(n-1) + calculate_fibonacci(n-2)",
             ClipboardType.CODE, "code-server", 120, True, ["python", "algorithm"], "python"),
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
            ("rgba(255, 128, 0, 0.8)",
             ClipboardType.COLOR, "figma", 1800, False, ["color", "orange"]),
            ("#!/usr/bin/env python3\nimport asyncio\n\nasync def main():\n    print('Hello from Nyrqis!')\n\nasyncio.run(main())",
             ClipboardType.CODE, "code-server", 4200, False, ["python", "async"], "python"),
            ("To set up the Nyrqis HAL backend:\n1. Install Rust toolchain: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh\n2. Clone the repository: git clone github.com/Myco-mycelium/Nythera\n3. Build the backend: cd Nyrqis && cargo build --release",
             ClipboardType.RICH_TEXT, "docs", 5400, True, ["setup", "guide"]),
            ("┌──────────────────────────────────────────────────────────────┐\n│  Nyrqis OS v0.1.0 - Build 20260904                          │\n│  Kernel: nyrqis-kernel 1.0.0-rc1                              │\n│  Compositor: nyrqis-compositor (Wayland)                      │\n│  Shell: nyrqis-shell                                          │\n└──────────────────────────────────────────────────────────────┘",
             ClipboardType.TEXT, "terminal", 7200, False, ["ascii-art"]),
            ("https://www.figma.com/file/abc123/Nyrqis-UI-Design",
             ClipboardType.TEXT, "firefox", 8400, False, ["url", "design"]),
        ]
        for i, (content, ctype, app, age_s, pinned, tags, *lang) in enumerate(sample_entries):
            lang_str = lang[0] if lang else ""
            self.history.append(ClipboardEntry(
                content=content, entry_type=ctype, source_app=app,
                timestamp=now - age_s, is_pinned=pinned, tags=tags,
                language=lang_str, access_count=random.randint(1, 15),
                sync_status=random.choice([SyncStatus.SYNCED, SyncStatus.SYNCED, SyncStatus.PENDING]),
            ))

        self.snippets = [
            Snippet(name="Git Push", content="git add -A && git commit -m \"$MSG\" && git push origin main",
                     shortcut="gp", category="Git", tags=["git", "deploy"],
                     use_count=45, description="Stage all, commit, and push"),
            Snippet(name="Python Boilerplate", content="#!/usr/bin/env python3\n\nimport sys\n\ndef main():\n    print('Hello, World!')\n\nif __name__ == '__main__':\n    main()",
                     shortcut="pyb", category="Code", tags=["python", "template"],
                     use_count=23, description="Python script boilerplate", language="python"),
            Snippet(name="SSH Tunnel", content="ssh -L $LOCAL_PORT:localhost:$REMOTE_PORT $USER@$HOST -N",
                     shortcut="ssht", category="Network", tags=["ssh", "tunnel"],
                     use_count=12, description="SSH port forwarding tunnel"),
            Snippet(name="Nyrqis Build", content="cd /opt/Nyrqis && cargo build --release 2>&1 | tee build.log",
                     shortcut="nbuild", category="Nyrqis", tags=["nyrqis", "build", "rust"],
                     use_count=38, description="Build Nyrqis OS backend"),
            Snippet(name="Lorem Ipsum", content="Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
                     shortcut="lorem", category="Text", tags=["placeholder", "text"],
                     use_count=8, description="Standard placeholder text"),
            Snippet(name="Docker Compose", content="version: '3.8'\nservices:\n  app:\n    build: .\n    ports:\n      - '8080:80'\n    volumes:\n      - .:/app\n    environment:\n      - NODE_ENV=development",
                     shortcut="dc", category="DevOps", tags=["docker", "compose"],
                     use_count=15, description="Docker Compose template", language="yaml"),
            Snippet(name="Color Palette", content="--primary: #1a1a2e\n--secondary: #16213e\n--accent: #0f3460\n--highlight: #e94560\n--text: #ffffff\n--bg: #0f0f23",
                     shortcut="palette", category="Design", tags=["css", "colors"],
                     use_count=6, description="Nyrqis OS color scheme"),
            Snippet(name="SQL Create User", content="CREATE USER nyrqis WITH PASSWORD 'changeme';\nGRANT ALL PRIVILEGES ON DATABASE nyrqis_prod TO nyrqis;\nALTER USER nyrqis CREATEDB;",
                     shortcut="sqlu", category="Database", tags=["sql", "user"],
                     use_count=9, description="Create PostgreSQL user", language="sql"),
        ]

        self.devices = [
            SyncDevice(name="Nyrqis Desktop", device_type="desktop", os="Nyrqis OS",
                        last_sync=now, status=SyncStatus.SYNCED, entries_synced=12,
                        ip_address="192.168.1.100"),
            SyncDevice(name="Framework Laptop", device_type="laptop", os="NixOS",
                        last_sync=now - 300, status=SyncStatus.SYNCED, entries_synced=12,
                        ip_address="192.168.1.105"),
            SyncDevice(name="iPhone 15 Pro", device_type="phone", os="iOS 18",
                        last_sync=now - 3600, status=SyncStatus.PENDING, entries_synced=8,
                        ip_address="192.168.1.110"),
        ]

        if self.history:
            self.current_entry = self.history[0]

    def copy(self, content: str, entry_type: ClipboardType = ClipboardType.TEXT,
             source_app: str = "", **kwargs) -> ClipboardEntry:
        entry = ClipboardEntry(content=content, entry_type=entry_type,
                                source_app=source_app, **kwargs)
        self.history.insert(0, entry)
        if len(self.history) > self.max_history:
            self.history = self.history[:self.max_history]
        self.current_entry = entry
        return entry

    def paste(self, index: int = 0) -> Optional[str]:
        if 0 <= index < len(self.history):
            entry = self.history[index]
            entry.access_count += 1
            return entry.content
        return None

    def pin_entry(self, index: int) -> bool:
        if 0 <= index < len(self.history):
            self.history[index].is_pinned = not self.history[index].is_pinned
            return True
        return False

    def delete_entry(self, index: int) -> bool:
        if 0 <= index < len(self.history):
            entry = self.history[index]
            if not entry.is_pinned:
                del self.history[index]
                return True
        return False

    def search(self, query: str) -> List[ClipboardEntry]:
        self.search_query = query
        q = query.lower()
        return [e for e in self.history if q in e.content.lower()
                or any(q in tag for tag in e.tags)]

    def filter_by_type(self, entry_type: Optional[ClipboardType]) -> List[ClipboardEntry]:
        self.filter_type = entry_type
        if entry_type is None:
            return self.history
        return [e for e in self.history if e.entry_type == entry_type]

    def get_pinned(self) -> List[ClipboardEntry]:
        return [e for e in self.history if e.is_pinned]

    def add_snippet(self, name: str, content: str, **kwargs) -> Snippet:
        snippet = Snippet(name=name, content=content, **kwargs)
        self.snippets.append(snippet)
        return snippet

    def use_snippet(self, name: str) -> Optional[str]:
        snippet = next((s for s in self.snippets if s.name == name), None)
        if snippet:
            snippet.use_count += 1
            snippet.last_used = time.time()
            return snippet.content
        return None

    def get_snippets_by_category(self) -> Dict[str, List[Snippet]]:
        cats: Dict[str, List[Snippet]] = {}
        for s in self.snippets:
            cats.setdefault(s.category, []).append(s)
        return cats

    def get_stats(self) -> ClipboardStats:
        return ClipboardStats(
            total_entries=len(self.history),
            total_size_bytes=sum(e.size_bytes for e in self.history),
            pinned_count=sum(1 for e in self.history if e.is_pinned),
            snippets_count=len(self.snippets),
            sync_devices=sum(1 for d in self.devices if d.status != SyncStatus.DISABLED),
        )

    def clear_history(self, keep_pinned: bool = True) -> int:
        before = len(self.history)
        if keep_pinned:
            self.history = [e for e in self.history if e.is_pinned]
        else:
            self.history = []
        return before - len(self.history)


class SnippetCategory(Enum):
    CODE = "code"
    TEXT = "text"
    EMAIL = "email"
    URL = "url"
    OTHER = "other"
