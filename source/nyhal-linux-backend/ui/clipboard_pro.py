"""
Nyrqis Clipboard Pro — advanced clipboard management application.

Features:
- Extended clipboard history with categories
- Pin frequently used items
- Snippet templates with variables
- Search and filter
- Auto-clear timer
- Cross-app clipboard sync
- Keyboard navigation throughout
"""

import time
import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


class ClipCategory(Enum):
    TEXT = "text"
    CODE = "code"
    LINK = "link"
    IMAGE = "image"
    FILE = "file"
    COLOR = "color"
    PASSWORD = "password"
    OTHER = "other"


class SnippetVariable(Enum):
    CURSOR = "cursor"
    DATE = "date"
    TIME = "time"
    CLIPBOARD = "clipboard"
    SELECTION = "selection"


CATEGORY_ICONS = {
    ClipCategory.TEXT: "📝",
    ClipCategory.CODE: "💻",
    ClipCategory.LINK: "🔗",
    ClipCategory.IMAGE: "🖼️",
    ClipCategory.FILE: "📁",
    ClipCategory.COLOR: "🎨",
    ClipCategory.PASSWORD: "🔐",
    ClipCategory.OTHER: "❓",
}


@dataclass
class ClipboardItem:
    """A clipboard history item."""
    content: str
    category: ClipCategory = ClipCategory.TEXT
    source_app: str = ""
    pinned: bool = False
    favorite: bool = False
    timestamp: float = field(default_factory=time.time)
    use_count: int = 1
    size_bytes: int = 0
    language: str = ""
    item_id: str = ""

    def __post_init__(self):
        if not self.item_id:
            self.item_id = hashlib.md5(f"{self.content}{self.timestamp}".encode()).hexdigest()[:8]
        if self.size_bytes <= 0:
            self.size_bytes = len(self.content.encode())

    @property
    def category_icon(self) -> str:
        return CATEGORY_ICONS.get(self.category, "❓")

    @property
    def display(self) -> str:
        pin = " 📌" if self.pinned else ""
        fav = " ⭐" if self.favorite else ""
        first_line = self.content.split("\n")[0][:50]
        return f"{self.category_icon} {first_line}{pin}{fav}"

    @property
    def preview(self) -> str:
        lines = self.content.split("\n")
        if len(lines) > 1:
            return f"{lines[0][:40]}... ({len(lines)} lines)"
        return self.content[:60]

    @property
    def size_str(self) -> str:
        if self.size_bytes >= 1048576:
            return f"{self.size_bytes / 1048576:.1f} MB"
        elif self.size_bytes >= 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes} B"

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
class SnippetTemplate:
    """A saved snippet template."""
    name: str
    content: str
    category: ClipCategory = ClipCategory.TEXT
    shortcut: str = ""  # keyboard shortcut
    tags: List[str] = field(default_factory=list)
    use_count: int = 0
    created: float = field(default_factory=time.time)
    snippet_id: str = ""

    def __post_init__(self):
        if not self.snippet_id:
            self.snippet_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def category_icon(self) -> str:
        return CATEGORY_ICONS.get(self.category, "❓")

    @property
    def display(self) -> str:
        shortcut = f" [{self.shortcut}]" if self.shortcut else ""
        return f"{self.category_icon} {self.name}{shortcut}"

    @property
    def preview(self) -> str:
        return self.content[:50] + "..." if len(self.content) > 50 else self.content

    @property
    def has_variables(self) -> bool:
        return "${" in self.content or "$(" in self.content

    def render(self, variables: Dict[str, str] = None) -> str:
        result = self.content
        if variables:
            for key, val in variables.items():
                result = result.replace(f"${{{key}}}", val)
        return result


class ClipboardPro:
    """Advanced clipboard management for Nyrqis OS."""

    def __init__(self):
        self._items: List[ClipboardItem] = []
        self._snippets: List[SnippetTemplate] = []
        self._selected_index: int = 0
        self._view_mode: str = "history"  # history, snippets, favorites
        self._filter_category: Optional[ClipCategory] = None
        self._search_query: str = ""
        self._auto_clear_seconds: int = 0  # 0 = disabled
        self._max_history: int = 500

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        self._items = [
            ClipboardItem("git commit -m 'Add feature'\ngit push origin main", ClipCategory.CODE,
                          "terminal", pinned=True, use_count=15, language="shell"),
            ClipboardItem("https://github.com/Myco-mycelium/Nythera", ClipCategory.LINK,
                          "browser", use_count=8),
            ClipboardItem("def calculate_bmi(weight, height):\n    return weight / (height ** 2)",
                          ClipCategory.CODE, "code-editor", use_count=5, language="python"),
            ClipboardItem("#7aa2f7", ClipCategory.COLOR, "color-picker", use_count=3),
            ClipboardItem("Meeting notes from sprint planning:\n- Complete UI components\n- Write tests\n- Deploy to staging",
                          ClipCategory.TEXT, "notes", pinned=True),
            ClipboardItem("ssh user@server.nyrqis.os -p 22", ClipCategory.CODE,
                          "terminal", use_count=2, language="shell"),
            ClipboardItem("https://nyrqis.os/docs/getting-started", ClipCategory.LINK,
                          "browser"),
            ClipboardItem("SELECT * FROM users WHERE active = 1 ORDER BY name;",
                          ClipCategory.CODE, "database-viewer", use_count=4, language="sql"),
            ClipboardItem("Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore.",
                          ClipCategory.TEXT, "editor"),
            ClipboardItem("S3cr3tP@ssw0rd!", ClipCategory.PASSWORD, "password-manager",
                          favorite=True),
        ]

        self._snippets = [
            SnippetTemplate("Git Commit", "git add -A && git commit -m ''\ngit push origin main",
                            ClipCategory.CODE, "Ctrl+Shift+G", ["git", "commit"]),
            SnippetTemplate("Python Function", "def function_name(params):\n    \"\"\"Docstring.\"\"\"\n    pass",
                            ClipCategory.CODE, "Ctrl+Shift+F", ["python", "function"]),
            SnippetTemplate("SQL Select", "SELECT column1, column2\nFROM table_name\nWHERE condition\nORDER BY column1\nLIMIT 10;",
                            ClipCategory.CODE, "", ["sql", "query"]),
            SnippetTemplate("Email Signature", "---\nBest regards,\nUser\nNyrqis OS Team",
                            ClipCategory.TEXT, "Ctrl+Shift+S", ["email", "signature"]),
            SnippetTemplate("Markdown Table", "| Column 1 | Column 2 | Column 3 |\n|----------|----------|----------|\n| Data     | Data     | Data     |",
                            ClipCategory.TEXT, "", ["markdown", "table"]),
            SnippetTemplate("HTML Boilerplate", "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"UTF-8\">\n  <title>${title}</title>\n</head>\n<body>\n  ${cursor}\n</body>\n</html>",
                            ClipCategory.CODE, "", ["html", "template"]),
            SnippetTemplate("Date Stamp", "$(date +%Y-%m-%d)", ClipCategory.TEXT, "", ["date"]),
            SnippetTemplate("API Response", '{\n  "status": "success",\n  "data": {},\n  "message": ""\n}',
                            ClipCategory.CODE, "", ["json", "api"]),
        ]

    def copy_item(self, content: str, category: ClipCategory = ClipCategory.TEXT,
                  source: str = "") -> ClipboardItem:
        item = ClipboardItem(content, category, source)
        self._items.insert(0, item)
        if len(self._items) > self._max_history:
            self._items.pop()
        return item

    def pin_item(self, index: int) -> bool:
        items = self._get_filtered_items()
        if 0 <= index < len(items):
            items[index].pinned = not items[index].pinned
            return True
        return False

    def favorite_item(self, index: int) -> bool:
        items = self._get_filtered_items()
        if 0 <= index < len(items):
            items[index].favorite = not items[index].favorite
            return True
        return False

    def delete_item(self, index: int) -> bool:
        items = self._get_filtered_items()
        if 0 <= index < len(items):
            self._items.remove(items[index])
            return True
        return False

    def clear_history(self) -> int:
        count = len(self._items)
        self._items.clear()
        return count

    def use_snippet(self, index: int) -> Optional[str]:
        if 0 <= index < len(self._snippets):
            snippet = self._snippets[index]
            snippet.use_count += 1
            return snippet.render()
        return None

    def add_snippet(self, name: str, content: str, category: ClipCategory = ClipCategory.TEXT) -> SnippetTemplate:
        snippet = SnippetTemplate(name=name, content=content, category=category)
        self._snippets.append(snippet)
        return snippet

    def delete_snippet(self, index: int) -> bool:
        if 0 <= index < len(self._snippets):
            self._snippets.pop(index)
            return True
        return False

    def set_search(self, query: str) -> None:
        self._search_query = query

    def set_filter(self, category: Optional[ClipCategory]) -> None:
        self._filter_category = category

    def _get_filtered_items(self) -> List[ClipboardItem]:
        items = list(self._items)
        if self._filter_category:
            items = [i for i in items if i.category == self._filter_category]
        if self._search_query:
            q = self._search_query.lower()
            items = [i for i in items if q in i.content.lower() or q in i.source_app.lower()]
        pinned = [i for i in items if i.pinned]
        unpinned = [i for i in items if not i.pinned]
        return pinned + unpinned

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        items = self._get_display_list()
        self._selected_index = min(len(items) - 1, self._selected_index + 1)

    def get_selected_item(self):
        items = self._get_display_list()
        if 0 <= self._selected_index < len(items):
            return items[self._selected_index]
        return None

    def _get_display_list(self) -> list:
        if self._view_mode == "snippets":
            return self._snippets
        elif self._view_mode == "favorites":
            return [i for i in self._items if i.favorite or i.pinned]
        return self._get_filtered_items()

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def total_items(self) -> int:
        return len(self._items)

    @property
    def pinned_count(self) -> int:
        return sum(1 for i in self._items if i.pinned)

    def render_history(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 📋 Clipboard History ({self.total_items} items, {self.pinned_count} pinned)")
        lines.append("─" * width)
        items = self._get_filtered_items()
        for i, item in enumerate(items[:15]):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {item.display}")
            lines.append(f"   {item.source_app or '—'} | {item.size_str} | {item.use_count}x | {item.time_ago}")
            lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Copy  P:Pin  F:Favorite  Del:Delete")
        lines.append(" S:Snippets  ⭐:Favorites  Esc:Clear filter")
        return lines

    def render_snippets(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 💾 Snippet Templates ({len(self._snippets)})")
        lines.append("─" * width)
        for i, snippet in enumerate(self._snippets):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {snippet.display}")
            lines.append(f"   {snippet.preview}")
            lines.append(f"   Used {snippet.use_count}x | Tags: {', '.join(snippet.tags)}")
            lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Use  N:New  Del:Delete  H:History")
        return lines

    def render_favorites(self, width: int = 70) -> List[str]:
        lines = []
        favs = self._get_display_list()
        lines.append(f" ⭐ Favorites & Pinned ({len(favs)})")
        lines.append("─" * width)
        for i, item in enumerate(favs):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {item.display}")
            lines.append(f"   {item.source_app} | {item.size_str}")
            lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Copy  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {"snippets": self.render_snippets, "favorites": self.render_favorites}
        renderer = renderers.get(self._view_mode, self.render_history)
        return renderer(width)

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "snippets":
            if key == "Escape":
                self.set_view("history")
                return "back"
            if key == "ArrowUp":
                self.select_up()
                return "select_up"
            if key == "ArrowDown":
                self.select_down()
                return "select_down"
            if key == "Enter":
                return "use_snippet" if self.use_snippet(self._selected_index) else "use_failed"
            return None
        if self._view_mode == "favorites":
            if key == "Escape":
                self.set_view("history")
                return "back"
            if key == "ArrowUp":
                self.select_up()
                return "select_up"
            if key == "ArrowDown":
                self.select_down()
                return "select_down"
            return None
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        if key == "ArrowDown":
            self.select_down()
            return "select_down"
        if key == "p":
            return "pin" if self.pin_item(self._selected_index) else "pin_failed"
        if key == "f":
            self.set_view("favorites")
            return "favorites"
        if key == "s":
            self.set_view("snippets")
            return "snippets"
        if key == "Delete":
            return "delete" if self.delete_item(self._selected_index) else "delete_failed"
        return None
