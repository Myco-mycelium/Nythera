"""
Nyrqis Plugin Marketplace — plugin discovery and management application.

Features:
- Browse and search plugins by category
- Install, update, and remove plugins
- Ratings and reviews system
- Auto-update checking and management
- Plugin dependency resolution
- Featured and trending plugins
- Plugin configuration
- Keyboard navigation throughout
"""

import time
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class PluginStatus(Enum):
    AVAILABLE = "available"
    INSTALLED = "installed"
    UPDATE_AVAILABLE = "update_available"
    DISABLED = "disabled"
    BROKEN = "broken"


class PluginCategory(Enum):
    THEMES = "themes"
    EXTENSIONS = "extensions"
    UTILITIES = "utilities"
    DEVELOPMENT = "development"
    PRODUCTIVITY = "productivity"
    MEDIA = "media"
    SYSTEM = "system"
    SECURITY = "security"


CATEGORY_ICONS = {
    PluginCategory.THEMES: "🎨",
    PluginCategory.EXTENSIONS: "🧩",
    PluginCategory.UTILITIES: "🔧",
    PluginCategory.DEVELOPMENT: "💻",
    PluginCategory.PRODUCTIVITY: "📋",
    PluginCategory.MEDIA: "🎬",
    PluginCategory.SYSTEM: "⚙️",
    PluginCategory.SECURITY: "🔒",
}

STATUS_ICONS = {
    PluginStatus.AVAILABLE: "📦",
    PluginStatus.INSTALLED: "✅",
    PluginStatus.UPDATE_AVAILABLE: "⬆️",
    PluginStatus.DISABLED: "⚫",
    PluginStatus.BROKEN: "❌",
}


@dataclass
class PluginReview:
    """A plugin review."""
    author: str
    rating: int  # 1-5
    title: str = ""
    content: str = ""
    helpful: int = 0
    timestamp: float = field(default_factory=time.time)

    @property
    def stars(self) -> str:
        return "⭐" * self.rating + "☆" * (5 - self.rating)

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 86400:
            return f"{int(diff // 3600)}h ago"
        elif diff < 2592000:
            return f"{int(diff // 86400)}d ago"
        return datetime.fromtimestamp(self.timestamp).strftime("%b %d")


@dataclass
class Plugin:
    """A marketplace plugin."""
    name: str
    author: str
    version: str
    description: str = ""
    category: PluginCategory = PluginCategory.UTILITIES
    status: PluginStatus = PluginStatus.AVAILABLE
    # Rating
    rating: float = 0.0
    review_count: int = 0
    reviews: List[PluginReview] = field(default_factory=list)
    # Downloads
    downloads: int = 0
    weekly_downloads: int = 0
    # Size
    size_kb: int = 0
    # Update
    installed_version: str = ""
    latest_version: str = ""
    auto_update: bool = True
    # Dependencies
    dependencies: List[str] = field(default_factory=list)
    # Config
    configurable: bool = False
    # Tags
    tags: List[str] = field(default_factory=list)
    # Metadata
    homepage: str = ""
    license: str = ""
    min_os_version: str = "1.0"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    plugin_id: str = ""

    def __post_init__(self):
        if not self.plugin_id:
            self.plugin_id = hashlib.md5(f"{self.name}{self.author}".encode()).hexdigest()[:8]

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "📦")

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.name} v{self.version}"

    @property
    def category_icon(self) -> str:
        return CATEGORY_ICONS.get(self.category, "🧩")

    @property
    def size_str(self) -> str:
        if self.size_kb >= 1024:
            return f"{self.size_kb / 1024:.1f} MB"
        return f"{self.size_kb} KB"

    @property
    def downloads_str(self) -> str:
        if self.downloads >= 1000000:
            return f"{self.downloads / 1000000:.1f}M"
        elif self.downloads >= 1000:
            return f"{self.downloads / 1000:.1f}K"
        return str(self.downloads)

    @property
    def rating_stars(self) -> str:
        full = int(self.rating)
        half = 1 if self.rating - full >= 0.5 else 0
        return "⭐" * full + ("½" if half else "") + "☆" * (5 - full - half)

    @property
    def version_status(self) -> str:
        if self.status == PluginStatus.UPDATE_AVAILABLE:
            return f"{self.installed_version} → {self.latest_version}"
        return self.version

    @property
    def updated_ago(self) -> str:
        diff = time.time() - self.updated
        if diff < 86400:
            return f"{int(diff // 3600)}h ago"
        elif diff < 2592000:
            return f"{int(diff // 86400)}d ago"
        return datetime.fromtimestamp(self.updated).strftime("%b %d, %Y")


# ─── Plugin Marketplace ──────────────────────────────────────────────────


class PluginMarketplace:
    """
    Plugin marketplace for Nyrqis OS.
    """

    def __init__(self):
        self._plugins: List[Plugin] = []
        self._selected_index: int = 0
        self._view_mode: str = "browse"  # browse, installed, updates, plugin_detail
        self._filter_category: Optional[PluginCategory] = None
        self._search_query: str = ""
        self._sort_by: str = "popular"  # popular, rating, new, name

        self._init_sample_plugins()

    def _init_sample_plugins(self) -> None:
        now = time.time()
        self._plugins = [
            Plugin(name="Nyrqis Dark Pro", author="Nyrqis Team", version="2.1.0",
                   description="Premium dark theme with OLED-friendly colors",
                   category=PluginCategory.THEMES, status=PluginStatus.INSTALLED,
                   rating=4.8, review_count=234, downloads=45600, weekly_downloads=12000,
                   size_kb=850, installed_version="2.0.0", configurable=True,
                   tags=["dark", "oled"],
                   reviews=[PluginReview("user123", 5, "Best dark theme", "Love the OLED blacks", 45)]),
            Plugin(name="Nyrqis Light", author="Nyrqis Team", version="1.5.0",
                   description="Clean light theme optimized for daytime use",
                   category=PluginCategory.THEMES, status=PluginStatus.UPDATE_AVAILABLE,
                   rating=4.5, review_count=156, downloads=28900, size_kb=600,
                   installed_version="1.4.0", tags=["light", "clean"]),
            Plugin(name="Git Lens", author="CodeTools Inc", version="3.2.1",
                   description="Visual Git history and blame annotations",
                   category=PluginCategory.DEVELOPMENT, status=PluginStatus.INSTALLED,
                   rating=4.7, review_count=892, downloads=125000, size_kb=3200,
                   installed_version="3.2.1", tags=["git", "history"]),
            Plugin(name="Terminal Plus", author="Nyrqis Team", version="2.0.0",
                   description="Enhanced terminal with split panes and auto-complete",
                   category=PluginCategory.DEVELOPMENT, status=PluginStatus.INSTALLED,
                   rating=4.6, review_count=567, downloads=89000, size_kb=2800,
                   installed_version="2.0.0", tags=["terminal", "split"]),
            Plugin(name="Markdown Preview", author="DocTools", version="1.8.3",
                   description="Live markdown preview with LaTeX support",
                   category=PluginCategory.PRODUCTIVITY, status=PluginStatus.AVAILABLE,
                   rating=4.4, review_count=234, downloads=45000, size_kb=1500,
                   tags=["markdown", "preview"]),
            Plugin(name="System Monitor Pro", author="SysWatch", version="4.1.0",
                   description="Advanced system monitoring with dashboards",
                   category=PluginCategory.SYSTEM, status=PluginStatus.AVAILABLE,
                   rating=4.3, review_count=189, downloads=34000, size_kb=4200,
                   configurable=True, tags=["monitor", "dashboard"]),
            Plugin(name="Clipboard Manager", author="ClipTools", version="2.5.0",
                   description="Advanced clipboard with history and sync",
                   category=PluginCategory.UTILITIES, status=PluginStatus.UPDATE_AVAILABLE,
                   rating=4.2, review_count=345, downloads=56000, size_kb=1800,
                   installed_version="2.4.0", tags=["clipboard", "sync"]),
            Plugin(name="VPN Client", author="NetSecure", version="3.0.0",
                   description="Multi-protocol VPN client with kill switch",
                   category=PluginCategory.SECURITY, status=PluginStatus.AVAILABLE,
                   rating=4.5, review_count=678, downloads=98000, size_kb=5500,
                   tags=["vpn", "security"]),
            Plugin(name="Weather Widget", author="Nyrqis Team", version="1.1.0",
                   description="Desktop weather with forecasts and alerts",
                   category=PluginCategory.UTILITIES, status=PluginStatus.INSTALLED,
                   rating=4.3, review_count=234, downloads=34000, size_kb=800,
                   installed_version="1.1.0", tags=["weather", "widget"]),
        ]

    # ── Plugin Operations ─────────────────────────────────────────────

    def install_plugin(self, index: int) -> bool:
        if 0 <= index < len(self._plugins):
            plugin = self._plugins[index]
            if plugin.status == PluginStatus.AVAILABLE:
                plugin.status = PluginStatus.INSTALLED
                plugin.installed_version = plugin.version
                return True
        return False

    def uninstall_plugin(self, index: int) -> bool:
        if 0 <= index < len(self._plugins):
            plugin = self._plugins[index]
            if plugin.status in (PluginStatus.INSTALLED, PluginStatus.UPDATE_AVAILABLE):
                plugin.status = PluginStatus.AVAILABLE
                plugin.installed_version = ""
                return True
        return False

    def update_plugin(self, index: int) -> bool:
        if 0 <= index < len(self._plugins):
            plugin = self._plugins[index]
            if plugin.status == PluginStatus.UPDATE_AVAILABLE:
                plugin.status = PluginStatus.INSTALLED
                plugin.installed_version = plugin.latest_version
                plugin.version = plugin.latest_version
                return True
        return False

    def toggle_auto_update(self, index: int) -> bool:
        if 0 <= index < len(self._plugins):
            self._plugins[index].auto_update = not self._plugins[index].auto_update
            return self._plugins[index].auto_update
        return False

    def add_review(self, plugin_index: int, author: str, rating: int,
                   title: str = "", content: str = "") -> Optional[PluginReview]:
        if 0 <= plugin_index < len(self._plugins):
            plugin = self._plugins[plugin_index]
            review = PluginReview(author=author, rating=rating, title=title, content=content)
            plugin.reviews.append(review)
            plugin.review_count = len(plugin.reviews)
            # Recalculate average
            total = sum(r.rating for r in plugin.reviews)
            plugin.rating = total / len(plugin.reviews)
            return review
        return None

    def check_updates(self) -> List[Plugin]:
        return [p for p in self._plugins if p.status == PluginStatus.UPDATE_AVAILABLE]

    def update_all(self) -> int:
        count = 0
        for i, plugin in enumerate(self._plugins):
            if plugin.status == PluginStatus.UPDATE_AVAILABLE:
                self.update_plugin(i)
                count += 1
        return count

    # ── Search & Filter ───────────────────────────────────────────────

    def search(self, query: str) -> List[Plugin]:
        self._search_query = query
        if not query:
            return self._get_filtered_plugins()
        q = query.lower()
        return [p for p in self._get_filtered_plugins()
                if q in p.name.lower() or q in p.description.lower()
                or any(q in t for t in p.tags)]

    def set_category_filter(self, category: Optional[PluginCategory]) -> None:
        self._filter_category = category
        self._selected_index = 0

    def _get_filtered_plugins(self) -> List[Plugin]:
        plugins = list(self._plugins)
        if self._filter_category:
            plugins = [p for p in plugins if p.category == self._filter_category]
        if self._sort_by == "popular":
            plugins.sort(key=lambda p: p.downloads, reverse=True)
        elif self._sort_by == "rating":
            plugins.sort(key=lambda p: p.rating, reverse=True)
        elif self._sort_by == "new":
            plugins.sort(key=lambda p: p.created, reverse=True)
        elif self._sort_by == "name":
            plugins.sort(key=lambda p: p.name.lower())
        return plugins

    def cycle_sort(self) -> str:
        sorts = ["popular", "rating", "new", "name"]
        idx = sorts.index(self._sort_by) if self._sort_by in sorts else 0
        self._sort_by = sorts[(idx + 1) % len(sorts)]
        return self._sort_by

    # ── Navigation ────────────────────────────────────────────────────

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
        if self._view_mode == "installed":
            return [p for p in self._plugins if p.status in (PluginStatus.INSTALLED, PluginStatus.UPDATE_AVAILABLE)]
        elif self._view_mode == "updates":
            return [p for p in self._plugins if p.status == PluginStatus.UPDATE_AVAILABLE]
        return self.search(self._search_query)

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def plugins(self) -> List[Plugin]:
        return list(self._plugins)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def installed_count(self) -> int:
        return sum(1 for p in self._plugins if p.status == PluginStatus.INSTALLED)

    @property
    def update_count(self) -> int:
        return sum(1 for p in self._plugins if p.status == PluginStatus.UPDATE_AVAILABLE)

    @property
    def available_count(self) -> int:
        return sum(1 for p in self._plugins if p.status == PluginStatus.AVAILABLE)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_browse(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 🧩 Plugin Marketplace")
        lines.append("─" * width)
        lines.append(f" {self.available_count} available | {self.installed_count} installed | {self.update_count} updates")
        lines.append(f" Sort: {self._sort_by} | Filter: {self._filter_category.value if self._filter_category else 'all'}")
        lines.append("─" * width)

        plugins = self.search(self._search_query)
        if not plugins:
            lines.append("  No plugins found.")
        else:
            for i, plugin in enumerate(plugins[:12]):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {plugin.display}")
                lines.append(f"   {plugin.category_icon} {plugin.category.value.title()} | {plugin.rating_stars} ({plugin.review_count}) | {plugin.downloads_str} downloads | {plugin.size_str}")
                lines.append(f"   {plugin.description[:width - 5]}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Details  I:Install  U:Update  S:Sort")
        lines.append(" T:Installed  Y:Updates  Tab:Categories  Esc:Clear")
        return lines

    def render_installed(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" ✅ Installed Plugins ({self.installed_count})")
        lines.append("─" * width)

        plugins = self._get_display_list()
        if not plugins:
            lines.append("  No plugins installed.")
        else:
            for i, plugin in enumerate(plugins):
                marker = "▸" if i == self._selected_index else " "
                update = " ⬆️" if plugin.status == PluginStatus.UPDATE_AVAILABLE else ""
                lines.append(f"{marker} {plugin.display}{update}")
                lines.append(f"   {plugin.category_icon} {plugin.category.value} | v{plugin.version_status}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Details  R:Remove  A:Auto-update  Esc:Back")
        return lines

    def render_updates(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" ⬆️  Available Updates ({self.update_count})")
        lines.append("─" * width)

        plugins = self._get_display_list()
        if not plugins:
            lines.append("  All plugins are up to date! 🎉")
        else:
            for i, plugin in enumerate(plugins):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {plugin.name} {plugin.version_status}")
                lines.append(f"   {plugin.size_str}")
            lines.append("")
            lines.append(" U:Update all")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Details  Esc:Back")
        return lines

    def render_detail(self, width: int = 70) -> List[str]:
        plugin = self.get_selected_item()
        if not plugin:
            return ["No plugin selected"]

        lines = []
        lines.append(f" {plugin.category_icon} {plugin.name} v{plugin.version}")
        lines.append("─" * width)
        lines.append(f" by {plugin.author}")
        lines.append(f" {plugin.description}")
        lines.append("")
        lines.append(f" {plugin.rating_stars} {plugin.rating:.1f} ({plugin.review_count} reviews)")
        lines.append(f" 📥 {plugin.downloads_str} downloads ({plugin.weekly_downloads}/week)")
        lines.append(f" 📦 {plugin.size_str} | License: {plugin.license}")
        lines.append(f" 🔄 Updated: {plugin.updated_ago} | Min OS: {plugin.min_os_version}")
        lines.append(f" Status: {plugin.status.value.title()}")

        if plugin.tags:
            lines.append(f" Tags: {', '.join(plugin.tags)}")

        # Reviews
        if plugin.reviews:
            lines.append("")
            lines.append(f" 📝 Reviews ({len(plugin.reviews)}):")
            for review in plugin.reviews[:3]:
                lines.append(f"  {review.stars} {review.title} — {review.author} ({review.time_ago})")
                if review.content:
                    lines.append(f"    {review.content[:width - 8]}")

        lines.append("─" * width)
        if plugin.status == PluginStatus.AVAILABLE:
            lines.append(" I:Install")
        elif plugin.status == PluginStatus.INSTALLED:
            lines.append(" R:Remove  A:Toggle auto-update")
        elif plugin.status == PluginStatus.UPDATE_AVAILABLE:
            lines.append(" U:Update")
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "installed": self.render_installed,
            "updates": self.render_updates,
            "plugin_detail": self.render_detail,
        }
        renderer = renderers.get(self._view_mode, self.render_browse)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "installed":
            return self._handle_installed_key(key)
        elif self._view_mode == "updates":
            return self._handle_updates_key(key)
        elif self._view_mode == "plugin_detail":
            return self._handle_detail_key(key)
        return self._handle_browse_key(key)

    def _handle_browse_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.set_view("plugin_detail")
            return "plugin_detail"
        elif key == "i":
            return "install" if self.install_plugin(self._selected_index) else "install_failed"
        elif key == "s":
            self.cycle_sort()
            return "sort"
        elif key == "t":
            self.set_view("installed")
            return "installed"
        elif key == "y":
            self.set_view("updates")
            return "updates"
        elif key == "Escape":
            self._filter_category = None
            self._search_query = ""
            return "clear"
        return None

    def _handle_installed_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("browse")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.set_view("plugin_detail")
            return "plugin_detail"
        elif key == "r":
            return "uninstall" if self.uninstall_plugin(self._selected_index) else "uninstall_failed"
        return None

    def _handle_updates_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("browse")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "u":
            count = self.update_all()
            return "update_all" if count > 0 else "no_updates"
        return None

    def _handle_detail_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("browse")
            return "back"
        elif key == "i":
            plugin = self.get_selected_item()
            if plugin and plugin.status == PluginStatus.AVAILABLE:
                self.install_plugin(self._selected_index)
                return "install"
        elif key == "r":
            plugin = self.get_selected_item()
            if plugin and plugin.status in (PluginStatus.INSTALLED, PluginStatus.UPDATE_AVAILABLE):
                self.uninstall_plugin(self._selected_index)
                return "uninstall"
        elif key == "u":
            plugin = self.get_selected_item()
            if plugin and plugin.status == PluginStatus.UPDATE_AVAILABLE:
                self.update_plugin(self._selected_index)
                return "update"
        return None
