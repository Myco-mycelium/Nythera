"""
Nyrqis Web Browser — tabbed browser with URL bar, bookmarks, and history.

Features:
- Multi-tab browsing with tab management (new, close, switch)
- URL bar with typed input and navigation controls (back, forward, reload)
- Bookmarks system with add/remove/folder organization
- Browsing history with search
- Basic HTML rendering via text-based simplification
- Downloads tracking
- Find-in-page (Ctrl+F)
- Private browsing mode
"""

import re
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Bookmark ────────────────────────────────────────────────────────────


@dataclass
class Bookmark:
    """A saved bookmark."""
    title: str
    url: str
    folder: str = "Bookmarks Bar"
    created: float = field(default_factory=time.time)
    favicon_color: str = "#4A90D9"

    @property
    def display(self) -> str:
        return self.title or self.url


@dataclass
class HistoryEntry:
    """A browsing history entry."""
    url: str
    title: str
    timestamp: float = field(default_factory=time.time)
    duration: float = 0.0  # time spent on page

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        else:
            return f"{int(diff // 86400)}d ago"


@dataclass
class Download:
    """A tracked download."""
    url: str
    filename: str
    size: int = 0
    downloaded: int = 0
    started: float = field(default_factory=time.time)
    status: str = "downloading"  # downloading, complete, failed

    @property
    def progress(self) -> float:
        if self.size <= 0:
            return 0.0
        return min(1.0, self.downloaded / self.size)

    @property
    def speed_str(self) -> str:
        elapsed = time.time() - self.started
        if elapsed <= 0:
            return "0 B/s"
        speed = self.downloaded / elapsed
        if speed < 1024:
            return f"{speed:.0f} B/s"
        elif speed < 1024 * 1024:
            return f"{speed / 1024:.1f} KB/s"
        else:
            return f"{speed / (1024 * 1024):.1f} MB/s"


# ─── Tab ──────────────────────────────────────────────────────────────────


class TabState(Enum):
    LOADING = "loading"
    COMPLETE = "complete"
    ERROR = "error"
    NEW = "new"
    BLANK = "blank"


@dataclass
class BrowserTab:
    """A single browser tab."""
    url: str = "nyrqis://newtab"
    title: str = "New Tab"
    state: TabState = TabState.NEW
    content: str = ""
    scroll_y: int = 0
    zoom: float = 1.0
    history_stack: List[str] = field(default_factory=list)
    forward_stack: List[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    loading_progress: float = 0.0
    is_private: bool = False
    find_text: str = ""
    find_visible: bool = False
    find_matches: int = 0
    find_current: int = 0

    @property
    def display_title(self) -> str:
        if self.title and self.title != "New Tab":
            return self.title
        return self.url

    @property
    def favicon_char(self) -> str:
        if self.url.startswith("nyrqis://"):
            return "🍄"
        elif "github" in self.url:
            return "⚡"
        elif "youtube" in self.url:
            return "▶"
        elif "wikipedia" in self.url:
            return "W"
        else:
            return "🌐"


# ─── Web Page Renderer (text-based) ──────────────────────────────────────


class SimpleHTMLRenderer:
    """Simplified text-based HTML renderer for basic web content."""

    # Common HTML entities
    ENTITIES = {
        "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&nbsp;": " ", "&#39;": "'",
        "&mdash;": "—", "&ndash;": "–", "&hellip;": "…",
        "&copy;": "©", "&reg;": "®", "&trade;": "™",
    }

    def render(self, html: str, width: int = 80) -> str:
        """Convert HTML to simplified text representation."""
        text = html

        # Remove script/style content entirely
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

        # Headers become prominent text
        for i in range(1, 7):
            prefix = "#" * i + " "
            text = re.sub(rf'<h{i}[^>]*>(.*?)</h{i}>', rf'\n{prefix}\1\n', text, flags=re.DOTALL | re.IGNORECASE)

        # Paragraphs and breaks
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<p[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<div[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<li[^>]*>', '\n  • ', text, flags=re.IGNORECASE)
        text = re.sub(r'<hr\s*/?>', '\n' + '─' * width + '\n', text, flags=re.IGNORECASE)

        # Links — show text [url]
        text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                       r'[\2](\1)', text, flags=re.DOTALL | re.IGNORECASE)

        # Bold/strong
        text = re.sub(r'<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>', r'**\1**', text,
                       flags=re.DOTALL | re.IGNORECASE)

        # Italic/em
        text = re.sub(r'<(?:i|em)[^>]*>(.*?)</(?:i|em)>', r'_\1_', text,
                       flags=re.DOTALL | re.IGNORECASE)

        # Code
        text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text,
                       flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<pre[^>]*>(.*?)</pre>', r'\n```\n\1\n```\n', text,
                       flags=re.DOTALL | re.IGNORECASE)

        # Images — alt text or [image]
        text = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*/?>', r'[\1]', text, flags=re.IGNORECASE)
        text = re.sub(r'<img[^>]*/?>', '[image]', text, flags=re.IGNORECASE)

        # Tables
        text = re.sub(r'<t[dh][^>]*>', '| ', text, flags=re.IGNORECASE)
        text = re.sub(r'</t[dh]>', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'<tr[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</tr>', ' |\n', text, flags=re.IGNORECASE)

        # Strip remaining HTML tags
        text = re.sub(r'<[^>]+>', '', text)

        # Decode entities
        for entity, char in self.ENTITIES.items():
            text = text.replace(entity, char)

        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        return text


# ─── Built-in Pages ──────────────────────────────────────────────────────


NEW_TAB_HTML = """
<html>
<head><title>New Tab</title></head>
<body>
<h1>Welcome to Nyrqis Browser</h1>
<p>Your portal to the web, built with 🍄</p>

<h2>Quick Links</h2>
<ul>
<li>https://github.com/Myco-mycelium/Nythera</li>
<li>https://en.wikipedia.org/wiki/Mycelium</li>
<li>https://duckduckgo.com</li>
</ul>

<h2>Keyboard Shortcuts</h2>
<table>
<tr><td>Ctrl+T</td><td>New Tab</td></tr>
<tr><td>Ctrl+W</td><td>Close Tab</td></tr>
<tr><td>Ctrl+L</td><td>Focus URL Bar</td></tr>
<tr><td>Ctrl+R</td><td>Reload</td></tr>
<tr><td>Ctrl+D</td><td>Add Bookmark</td></tr>
<tr><td>Ctrl+H</td><td>History</td></tr>
<tr><td>Ctrl+F</td><td>Find in Page</td></tr>
<tr><td>Ctrl++/-</td><td>Zoom In/Out</td></tr>
</table>
</body>
</html>
"""

HISTORY_HTML_TEMPLATE = """
<html>
<head><title>History</title></head>
<body>
<h1>Browsing History</h1>
{entries}
</body>
</html>
"""

BOOKMARKS_HTML_TEMPLATE = """
<html>
<head><title>Bookmarks</title></head>
<body>
<h1>Bookmarks</h1>
{entries}
</body>
</html>
"""


# ─── Browser ──────────────────────────────────────────────────────────────


class WebBrowser:
    """
    Tabbed web browser for Nyrqis OS.

    Supports tabs, bookmarks, history, downloads, and basic HTML rendering.
    """

    def __init__(self, width: int = 1024, height: int = 768):
        self._width = width
        self._height = height

        # Tabs
        self._tabs: List[BrowserTab] = [BrowserTab()]
        self._tab_index: int = 0

        # URL bar
        self._url_text: str = ""
        self._url_editing: bool = False

        # Bookmarks
        self._bookmarks: List[Bookmark] = [
            Bookmark("Nyrqis GitHub", "https://github.com/Myco-mycelium/Nythera"),
            Bookmark("Wikipedia", "https://en.wikipedia.org"),
            Bookmark("DuckDuckGo", "https://duckduckgo.com"),
            Bookmark("MDN Web Docs", "https://developer.mozilla.org"),
            Bookmark("Python Docs", "https://docs.python.org"),
        ]

        # History
        self._history: List[HistoryEntry] = []
        self._max_history: int = 500

        # Downloads
        self._downloads: List[Download] = []
        self._download_dir: str = "~/Downloads"

        # Settings
        self._home_url: str = "nyrqis://newtab"
        self._renderer = SimpleHTMLRenderer()
        self._zoom_levels = [0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1.0,
                             1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
        self._content_width = 80  # text columns

        # Callbacks
        self._on_navigate: List[Callable] = []
        self._on_tab_change: List[Callable] = []

    # ── Tab Management ────────────────────────────────────────────────

    @property
    def tabs(self) -> List[BrowserTab]:
        return list(self._tabs)

    @property
    def tab_index(self) -> int:
        return self._tab_index

    @property
    def current_tab(self) -> Optional[BrowserTab]:
        if 0 <= self._tab_index < len(self._tabs):
            return self._tabs[self._tab_index]
        return None

    @property
    def tab_count(self) -> int:
        return len(self._tabs)

    def new_tab(self, url: str = "", private: bool = False) -> BrowserTab:
        """Open a new tab."""
        tab = BrowserTab(
            url=url or self._home_url,
            title="New Tab" if not url else self._url_to_title(url),
            state=TabState.NEW if not url else TabState.LOADING,
            is_private=private,
        )
        self._tabs.append(tab)
        self._tab_index = len(self._tabs) - 1
        if url:
            self._navigate_tab(tab, url)
        self._notify_tab_change()
        return tab

    def close_tab(self, index: int = -1) -> bool:
        """Close a tab. Returns False if last tab."""
        if len(self._tabs) <= 1:
            return False

        idx = index if index >= 0 else self._tab_index
        if idx < 0 or idx >= len(self._tabs):
            return False

        self._tabs.pop(idx)
        if self._tab_index >= len(self._tabs):
            self._tab_index = len(self._tabs) - 1
        elif self._tab_index > idx:
            self._tab_index -= 1
        self._notify_tab_change()
        return True

    def switch_tab(self, index: int) -> bool:
        """Switch to a tab by index."""
        if 0 <= index < len(self._tabs):
            self._tab_index = index
            self._notify_tab_change()
            return True
        return False

    def next_tab(self) -> None:
        """Switch to the next tab (wraps around)."""
        if self._tabs:
            self._tab_index = (self._tab_index + 1) % len(self._tabs)
            self._notify_tab_change()

    def prev_tab(self) -> None:
        """Switch to the previous tab (wraps around)."""
        if self._tabs:
            self._tab_index = (self._tab_index - 1) % len(self._tabs)
            self._notify_tab_change()

    def close_other_tabs(self) -> int:
        """Close all tabs except current. Returns count closed."""
        current = self._tabs[self._tab_index]
        count = len(self._tabs) - 1
        self._tabs = [current]
        self._tab_index = 0
        return count

    # ── Navigation ────────────────────────────────────────────────────

    def navigate(self, url: str) -> bool:
        """Navigate current tab to URL."""
        tab = self.current_tab
        if not tab:
            return False

        # Normalize URL
        if not url.startswith(("http://", "https://", "nyrqis://", "about:")):
            if "." in url and " " not in url:
                url = "https://" + url
            else:
                url = f"https://duckduckgo.com/?q={url.replace(' ', '+')}"

        self._navigate_tab(tab, url)
        self._notify_navigate(url)
        return True

    def _navigate_tab(self, tab: BrowserTab, url: str) -> None:
        """Internal navigation for a tab."""
        # Push current to history stack
        if tab.url and tab.url != "about:blank":
            tab.history_stack.append(tab.url)
        tab.forward_stack.clear()

        tab.url = url
        tab.scroll_y = 0
        tab.state = TabState.LOADING
        tab.loading_progress = 0.0

        # Handle built-in pages
        if url == "nyrqis://newtab":
            tab.title = "New Tab"
            tab.content = NEW_TAB_HTML.strip()
            tab.state = TabState.COMPLETE
            tab.loading_progress = 1.0
        elif url == "nyrqis://history":
            tab.title = "History"
            tab.content = self._build_history_html()
            tab.state = TabState.COMPLETE
        elif url == "nyrqis://bookmarks":
            tab.title = "Bookmarks"
            tab.content = self._build_bookmarks_html()
            tab.state = TabState.COMPLETE
        elif url == "nyrqis://downloads":
            tab.title = "Downloads"
            tab.content = self._build_downloads_html()
            tab.state = TabState.COMPLETE
        else:
            # Simulate page load
            tab.title = self._url_to_title(url)
            tab.content = self._generate_page_content(url)
            tab.state = TabState.COMPLETE
            tab.loading_progress = 1.0

        # Add to history (unless private)
        if not tab.is_private:
            self._add_history(url, tab.title)

    def go_back(self) -> bool:
        """Navigate back in history."""
        tab = self.current_tab
        if not tab or not tab.history_stack:
            return False
        tab.forward_stack.append(tab.url)
        url = tab.history_stack.pop()
        tab.url = url
        tab.title = self._url_to_title(url)
        tab.state = TabState.COMPLETE
        return True

    def go_forward(self) -> bool:
        """Navigate forward in history."""
        tab = self.current_tab
        if not tab or not tab.forward_stack:
            return False
        tab.history_stack.append(tab.url)
        url = tab.forward_stack.pop()
        tab.url = url
        tab.title = self._url_to_title(url)
        tab.state = TabState.COMPLETE
        return True

    def reload(self) -> bool:
        """Reload current tab."""
        tab = self.current_tab
        if not tab:
            return False
        self._navigate_tab(tab, tab.url)
        return True

    def stop(self) -> None:
        """Stop loading."""
        tab = self.current_tab
        if tab and tab.state == TabState.LOADING:
            tab.state = TabState.COMPLETE

    def go_home(self) -> None:
        """Navigate to home page."""
        self.navigate(self._home_url)

    @property
    def can_go_back(self) -> bool:
        tab = self.current_tab
        return bool(tab and tab.history_stack)

    @property
    def can_go_forward(self) -> bool:
        tab = self.current_tab
        return bool(tab and tab.forward_stack)

    # ── URL Bar ───────────────────────────────────────────────────────

    @property
    def url_text(self) -> str:
        if self._url_editing:
            return self._url_text
        tab = self.current_tab
        return tab.url if tab else ""

    def start_url_edit(self) -> None:
        """Begin editing the URL bar."""
        tab = self.current_tab
        self._url_text = tab.url if tab else ""
        self._url_editing = True

    def update_url_text(self, text: str) -> None:
        """Update URL bar text while editing."""
        self._url_text = text

    def submit_url(self) -> bool:
        """Submit the URL bar text."""
        text = self._url_text.strip()
        self._url_editing = False
        if text:
            return self.navigate(text)
        return False

    def cancel_url_edit(self) -> None:
        """Cancel URL editing."""
        self._url_editing = False

    @property
    def url_suggestions(self) -> List[str]:
        """Get URL suggestions based on current input."""
        if not self._url_editing or not self._url_text:
            return []

        text = self._url_text.lower()
        suggestions = []

        # From bookmarks
        for bm in self._bookmarks:
            if text in bm.url.lower() or text in bm.title.lower():
                suggestions.append(bm.url)

        # From history
        seen = set(suggestions)
        for entry in reversed(self._history):
            if entry.url not in seen and (text in entry.url.lower() or text in entry.title.lower()):
                suggestions.append(entry.url)
                seen.add(entry.url)

        return suggestions[:8]

    # ── Zoom ──────────────────────────────────────────────────────────

    def zoom_in(self) -> float:
        """Zoom in."""
        tab = self.current_tab
        if not tab:
            return 1.0
        for z in self._zoom_levels:
            if z > tab.zoom + 0.01:
                tab.zoom = z
                return z
        return tab.zoom

    def zoom_out(self) -> float:
        """Zoom out."""
        tab = self.current_tab
        if not tab:
            return 1.0
        for z in reversed(self._zoom_levels):
            if z < tab.zoom - 0.01:
                tab.zoom = z
                return z
        return tab.zoom

    def zoom_reset(self) -> float:
        """Reset zoom to 100%."""
        tab = self.current_tab
        if tab:
            tab.zoom = 1.0
        return 1.0

    def zoom_to(self, level: float) -> float:
        """Set zoom to specific level."""
        tab = self.current_tab
        if tab:
            tab.zoom = max(0.25, min(3.0, level))
            return tab.zoom
        return 1.0

    # ── Bookmarks ─────────────────────────────────────────────────────

    def add_bookmark(self, title: str = "", url: str = "", folder: str = "Bookmarks Bar") -> Bookmark:
        """Add a bookmark for the current page."""
        tab = self.current_tab
        bm = Bookmark(
            title=title or (tab.title if tab else ""),
            url=url or (tab.url if tab else ""),
            folder=folder,
        )
        self._bookmarks.append(bm)
        return bm

    def remove_bookmark(self, url: str) -> bool:
        """Remove a bookmark by URL."""
        for i, bm in enumerate(self._bookmarks):
            if bm.url == url:
                self._bookmarks.pop(i)
                return True
        return False

    def is_bookmarked(self, url: str = "") -> bool:
        """Check if current page is bookmarked."""
        target = url or (self.current_tab.url if self.current_tab else "")
        return any(bm.url == target for bm in self._bookmarks)

    def get_bookmarks(self, folder: str = "") -> List[Bookmark]:
        """Get bookmarks, optionally filtered by folder."""
        if folder:
            return [bm for bm in self._bookmarks if bm.folder == folder]
        return list(self._bookmarks)

    @property
    def bookmark_folders(self) -> List[str]:
        """Get unique bookmark folder names."""
        folders = sorted(set(bm.folder for bm in self._bookmarks))
        return folders

    def toggle_bookmark(self) -> bool:
        """Toggle bookmark for current page."""
        tab = self.current_tab
        if not tab:
            return False
        if self.is_bookmarked(tab.url):
            self.remove_bookmark(tab.url)
            return False
        else:
            self.add_bookmark()
            return True

    # ── History ───────────────────────────────────────────────────────

    def _add_history(self, url: str, title: str) -> None:
        entry = HistoryEntry(url=url, title=title)
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def get_history(self, search: str = "", limit: int = 100) -> List[HistoryEntry]:
        """Get history entries, optionally filtered."""
        entries = list(reversed(self._history))
        if search:
            search_lower = search.lower()
            entries = [e for e in entries
                       if search_lower in e.url.lower() or search_lower in e.title.lower()]
        return entries[:limit]

    def clear_history(self) -> int:
        """Clear all history. Returns count cleared."""
        count = len(self._history)
        self._history.clear()
        return count

    def clear_history_range(self, hours: int = 24) -> int:
        """Clear history from the last N hours."""
        cutoff = time.time() - (hours * 3600)
        before = len(self._history)
        self._history = [e for e in self._history if e.timestamp < cutoff]
        return before - len(self._history)

    # ── Downloads ─────────────────────────────────────────────────────

    def start_download(self, url: str, filename: str = "", size: int = 0) -> Download:
        """Start tracking a download."""
        fname = filename or url.split("/")[-1] or "download"
        dl = Download(url=url, filename=fname, size=size)
        self._downloads.append(dl)
        return dl

    def update_download(self, url: str, downloaded: int, status: str = "") -> Optional[Download]:
        """Update download progress."""
        for dl in self._downloads:
            if dl.url == url:
                dl.downloaded = downloaded
                if status:
                    dl.status = status
                return dl
        return None

    @property
    def downloads(self) -> List[Download]:
        return list(self._downloads)

    @property
    def active_downloads(self) -> List[Download]:
        return [dl for dl in self._downloads if dl.status == "downloading"]

    # ── Find in Page ──────────────────────────────────────────────────

    def show_find(self) -> None:
        """Show find bar."""
        tab = self.current_tab
        if tab:
            tab.find_visible = True
            tab.find_text = ""

    def hide_find(self) -> None:
        """Hide find bar."""
        tab = self.current_tab
        if tab:
            tab.find_visible = False
            tab.find_text = ""
            tab.find_matches = 0

    def update_find(self, text: str) -> int:
        """Update find text. Returns match count."""
        tab = self.current_tab
        if not tab:
            return 0
        tab.find_text = text
        if text:
            tab.find_matches = tab.content.lower().count(text.lower())
            tab.find_current = 1 if tab.find_matches > 0 else 0
        else:
            tab.find_matches = 0
            tab.find_current = 0
        return tab.find_matches

    def find_next(self) -> bool:
        """Move to next match."""
        tab = self.current_tab
        if not tab or tab.find_matches == 0:
            return False
        tab.find_current = (tab.find_current % tab.find_matches) + 1
        return True

    def find_prev(self) -> bool:
        """Move to previous match."""
        tab = self.current_tab
        if not tab or tab.find_matches == 0:
            return False
        tab.find_current = ((tab.find_current - 2) % tab.find_matches) + 1
        return True

    # ── Settings ──────────────────────────────────────────────────────

    def set_home(self, url: str) -> None:
        """Set home page URL."""
        self._home_url = url

    @property
    def home_url(self) -> str:
        return self._home_url

    def set_content_width(self, width: int) -> None:
        """Set text rendering width."""
        self._content_width = max(40, min(200, width))

    # ── Event System ──────────────────────────────────────────────────

    def on_navigate(self, callback: Callable) -> None:
        self._on_navigate.append(callback)

    def on_tab_change(self, callback: Callable) -> None:
        self._on_tab_change.append(callback)

    def _notify_navigate(self, url: str) -> None:
        for cb in self._on_navigate:
            try:
                cb(url)
            except Exception:
                pass

    def _notify_tab_change(self) -> None:
        for cb in self._on_tab_change:
            try:
                cb(self._tab_index)
            except Exception:
                pass

    # ── Rendering ─────────────────────────────────────────────────────

    def render_url_bar(self, width: int = 80) -> str:
        """Render the URL bar as text."""
        parts = []

        # Navigation buttons
        back = "◀" if self.can_go_back else "◁"
        fwd = "▶" if self.can_go_forward else "▷"
        reload = "⟳"
        parts.append(f" {back} {fwd} {reload} ")

        # URL display
        tab = self.current_tab
        if self._url_editing:
            url_display = self._url_text
        elif tab:
            url_display = tab.url
        else:
            url_display = ""

        # Pad to fill width
        used = sum(len(p) for p in parts) + len(url_display) + 4
        pad = max(0, width - used - 2)
        url_bar = f"│ {url_display}{' ' * pad} │"

        return "".join(parts) + url_bar

    def render_tabs(self, width: int = 80) -> str:
        """Render the tab bar."""
        tab_width = max(15, min(25, width // max(1, len(self._tabs))))
        parts = []
        for i, tab in enumerate(self._tabs):
            marker = "▸" if i == self._tab_index else " "
            title = tab.display_title[:tab_width - 4]
            close = "×"
            tab_str = f"{marker} {tab.favicon_char} {title} {close}"
            parts.append(tab_str[:tab_width])

        return " ".join(parts)

    def render_content(self, width: int = 80, height: int = 30) -> List[str]:
        """Render the page content as text lines."""
        tab = self.current_tab
        if not tab:
            return [""]

        # Render HTML to text
        text = self._renderer.render(tab.content, width)

        # Apply find highlighting indicator
        lines = text.split("\n")

        # Apply zoom (approximate by adjusting content width)
        effective_width = int(width / tab.zoom) if tab.zoom > 0 else width

        # Scroll
        start = max(0, tab.scroll_y)
        visible = lines[start:start + height]

        # Pad if needed
        while len(visible) < height:
            visible.append("")

        return visible

    def render_status_bar(self, width: int = 80) -> str:
        """Render the status bar."""
        tab = self.current_tab
        if not tab:
            return ""

        parts = []

        # Loading indicator
        if tab.state == TabState.LOADING:
            parts.append(f" ⏳ Loading... {int(tab.loading_progress * 100)}%")
        else:
            parts.append(" ✅ Ready")

        # Zoom
        parts.append(f" | {int(tab.zoom * 100)}%")

        # Tab info
        parts.append(f" | Tab {self._tab_index + 1}/{len(self._tabs)}")

        # Downloads
        active = len(self.active_downloads)
        if active:
            parts.append(f" | ⬇ {active} downloads")

        # Private mode
        if tab.is_private:
            parts.append(" | 🔒 Private")

        # Find
        if tab.find_visible and tab.find_text:
            parts.append(f" | 🔍 {tab.find_current}/{tab.find_matches}")

        status = "".join(parts)
        if len(status) > width:
            status = status[:width - 3] + "..."
        return status.ljust(width)

    def render(self, width: int = 80, height: int = 40) -> List[str]:
        """Render the complete browser UI."""
        lines = []

        # Tab bar
        lines.append(self.render_tabs(width))

        # URL bar
        lines.append(self.render_url_bar(width))

        # Separator
        lines.append("─" * width)

        # Content area
        content_height = height - 4  # tabs + url + separator + status
        tab = self.current_tab
        if tab and tab.find_visible:
            content_height -= 1  # find bar

        content = self.render_content(width, content_height)
        lines.extend(content)

        # Find bar
        if tab and tab.find_visible:
            find_str = f" 🔍 Find: {tab.find_text}"
            if tab.find_matches > 0:
                find_str += f" ({tab.find_current}/{tab.find_matches})"
            lines.append(find_str[:width].ljust(width))

        # Status bar
        lines.append(self.render_status_bar(width))

        return lines

    # ── Helpers ───────────────────────────────────────────────────────

    def _url_to_title(self, url: str) -> str:
        """Extract a title from a URL."""
        if url.startswith("nyrqis://"):
            return url.replace("nyrqis://", "").replace("-", " ").title()
        # Remove protocol and www
        clean = re.sub(r'^https?://(www\.)?', '', url)
        # Take first path segment
        parts = clean.split("/")
        if parts:
            return parts[0].split(".")[0].title()
        return url

    def _generate_page_content(self, url: str) -> str:
        """Generate simulated page content."""
        domain = re.sub(r'^https?://(www\.)?', '', url).split("/")[0]
        path = url.split(domain)[-1] if domain in url else ""

        return f"""<html>
<head><title>{domain}</title></head>
<body>
<h1>{domain}</h1>
<p>This page would contain content from <b>{domain}</b>.</p>
<p>Path: {path or '/'}</p>
<p>In the full Nyrqis OS, this would render actual web content
using the embedded Wayland compositor and rendering pipeline.</p>

<h2>Page Info</h2>
<table>
<tr><td>Domain</td><td>{domain}</td></tr>
<tr><td>Protocol</td><td>{"HTTPS" if url.startswith("https") else "HTTP"}</td></tr>
<tr><td>Status</td><td>200 OK</td></tr>
</table>
</body>
</html>"""

    def _build_history_html(self) -> str:
        """Build history page HTML."""
        entries_html = ""
        current_date = ""
        for entry in self._history[-50:]:  # Show last 50
            ts = datetime.fromtimestamp(entry.timestamp)
            date_str = ts.strftime("%A, %B %d, %Y")
            if date_str != current_date:
                current_date = date_str
                entries_html += f"<h2>{date_str}</h2>\n"
            entries_html += (
                f'<p><a href="{entry.url}">{entry.title or entry.url}</a> '
                f'<small>{entry.time_ago}</small></p>\n'
            )
        if not entries_html:
            entries_html = "<p>No history yet.</p>"
        return HISTORY_HTML_TEMPLATE.format(entries=entries_html)

    def _build_bookmarks_html(self) -> str:
        """Build bookmarks page HTML."""
        entries_html = ""
        for folder in self.bookmark_folders:
            entries_html += f"<h2>{folder}</h2>\n<ul>\n"
            for bm in self.get_bookmarks(folder):
                entries_html += f'<li><a href="{bm.url}">{bm.title}</a></li>\n'
            entries_html += "</ul>\n"
        if not entries_html:
            entries_html = "<p>No bookmarks yet.</p>"
        return BOOKMARKS_HTML_TEMPLATE.format(entries=entries_html)

    def _build_downloads_html(self) -> str:
        """Build downloads page HTML."""
        entries = []
        for dl in reversed(self._downloads):
            status_icon = "✅" if dl.status == "complete" else "❌" if dl.status == "failed" else "⬇️"
            entries.append(
                f"<tr><td>{status_icon}</td><td>{dl.filename}</td>"
                f"<td>{dl.progress * 100:.0f}%</td>"
                f"<td>{dl.speed_str}</td></tr>"
            )

        rows = "\n".join(entries) if entries else "<tr><td colspan='4'>No downloads.</td></tr>"
        return f"""<html>
<head><title>Downloads</title></head>
<body>
<h1>Downloads</h1>
<table>
<tr><th>Status</th><th>File</th><th>Progress</th><th>Speed</th></tr>
{rows}
</table>
</body>
</html>"""

    def handle_key(self, key: str) -> Optional[str]:
        """Handle a keyboard input. Returns action or None."""
        # URL bar editing
        if self._url_editing:
            if key == "Enter":
                self.submit_url()
                return "navigate"
            elif key == "Escape":
                self.cancel_url_edit()
                return "cancel_edit"
            elif key == "Backspace":
                self._url_text = self._url_text[:-1]
                return "edit_url"
            elif len(key) == 1:
                self._url_text += key
                return "edit_url"
            return None

        # Normal mode
        if key == "Ctrl+l":
            self.start_url_edit()
            return "url_focus"
        elif key == "Ctrl+t":
            self.new_tab()
            return "new_tab"
        elif key == "Ctrl+w":
            self.close_tab()
            return "close_tab"
        elif key == "Ctrl+r":
            self.reload()
            return "reload"
        elif key == "Ctrl+d":
            self.toggle_bookmark()
            return "bookmark"
        elif key == "Ctrl+h":
            self.navigate("nyrqis://history")
            return "history"
        elif key == "Ctrl+b":
            self.navigate("nyrqis://bookmarks")
            return "bookmarks"
        elif key == "Ctrl+j":
            self.navigate("nyrqis://downloads")
            return "downloads"
        elif key == "Ctrl+f":
            self.show_find()
            return "find"
        elif key == "Escape":
            self.hide_find()
            return "find_close"
        elif key == "Ctrl+plus" or key == "Ctrl+=":
            self.zoom_in()
            return "zoom_in"
        elif key == "Ctrl+-":
            self.zoom_out()
            return "zoom_out"
        elif key == "Ctrl+0":
            self.zoom_reset()
            return "zoom_reset"
        elif key == "Ctrl+Tab":
            self.next_tab()
            return "next_tab"
        elif key == "Ctrl+Shift+Tab":
            self.prev_tab()
            return "prev_tab"
        elif key == "Alt+Left":
            self.go_back()
            return "back"
        elif key == "Alt+Right":
            self.go_forward()
            return "forward"
        elif key == "F5":
            self.reload()
            return "reload"

        # Find bar input
        if self.current_tab and self.current_tab.find_visible:
            if key == "Enter":
                self.find_next()
                return "find_next"
            elif key == "Shift+Enter":
                self.find_prev()
                return "find_prev"
            elif key == "Backspace":
                tab = self.current_tab
                tab.find_text = tab.find_text[:-1]
                self.update_find(tab.find_text)
                return "find_update"
            elif len(key) == 1:
                self.update_find(self.current_tab.find_text + key)
                return "find_update"

        return None
