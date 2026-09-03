"""
Nyrqis Feed Reader — RSS/Atom news aggregator with offline reading.

Features:
- Subscribe to RSS/Atom feeds with categories
- Mark articles as read/unread, starred
- Full article view with HTML-stripped text
- Feed management (add, remove, rename, reorder)
- Category organization (Tech, News, Dev, Science, etc.)
- Search across all articles
- Offline reading with local cache
- Keyboard navigation throughout
"""

import re
import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class FeedCategory(Enum):
    TECH = "Technology"
    NEWS = "News"
    DEV = "Development"
    SCIENCE = "Science"
    DESIGN = "Design"
    CULTURE = "Culture"
    FINANCE = "Finance"
    SPORTS = "Sports"
    OTHER = "Other"


CATEGORY_COLORS = {
    FeedCategory.TECH: "#4A90D9",
    FeedCategory.NEWS: "#E74C3C",
    FeedCategory.DEV: "#2ECC71",
    FeedCategory.SCIENCE: "#9B59B6",
    FeedCategory.DESIGN: "#F39C12",
    FeedCategory.CULTURE: "#E67E22",
    FeedCategory.FINANCE: "#1ABC9C",
    FeedCategory.SPORTS: "#3498DB",
    FeedCategory.OTHER: "#95A5A6",
}


@dataclass
class Article:
    """A single article from a feed."""
    title: str
    url: str
    summary: str = ""
    content: str = ""
    author: str = ""
    published: float = 0.0
    feed_id: str = ""
    feed_title: str = ""
    is_read: bool = False
    is_starred: bool = False
    article_id: str = ""
    categories: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.article_id:
            self.article_id = hashlib.md5(f"{self.url}{self.title}".encode()).hexdigest()[:10]

    @property
    def display_title(self) -> str:
        marker = "● " if not self.is_read else "  "
        star = " ⭐" if self.is_starred else ""
        return f"{marker}{self.title[:60]}{star}"

    @property
    def time_ago(self) -> str:
        if self.published <= 0:
            return ""
        diff = time.time() - self.published
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        elif diff < 604800:
            return f"{int(diff // 86400)}d ago"
        return datetime.fromtimestamp(self.published).strftime("%b %d")

    @property
    def date_str(self) -> str:
        if self.published <= 0:
            return "Unknown"
        return datetime.fromtimestamp(self.published).strftime("%Y-%m-%d %H:%M")

    @property
    def summary_preview(self) -> str:
        plain = re.sub(r'<[^>]+>', '', self.summary)
        return plain[:120].strip() if plain else "(no summary)"


@dataclass
class Feed:
    """An RSS/Atom feed source."""
    title: str
    url: str
    site_url: str = ""
    description: str = ""
    category: FeedCategory = FeedCategory.OTHER
    articles: List[Article] = field(default_factory=list)
    last_fetched: float = 0.0
    feed_id: str = ""
    is_active: bool = True
    article_count: int = 0
    unread_count: int = 0

    def __post_init__(self):
        if not self.feed_id:
            self.feed_id = hashlib.md5(self.url.encode()).hexdigest()[:8]

    @property
    def display(self) -> str:
        return self.title

    @property
    def badge(self) -> str:
        if self.unread_count > 0:
            return f" ({self.unread_count})"
        return ""


# ─── Feed Reader ─────────────────────────────────────────────────────────


class FeedReader:
    """
    RSS/Atom feed reader for Nyrqis OS.

    Manages feeds, articles, and reading state.
    """

    def __init__(self):
        self._feeds: List[Feed] = []
        self._articles: List[Article] = []
        self._current_feed: Optional[Feed] = None
        self._selected_article: Optional[Article] = None
        self._view_mode: str = "list"  # list, article, feed_manage
        self._selected_index: int = 0
        self._filter_read: bool = False
        self._filter_starred: bool = False
        self._search_query: str = ""
        self._sort_newest: bool = True
        self._scroll_pos: int = 0

        # Callbacks
        self._on_refresh: List[Callable] = []

        # Init sample data
        self._init_sample_feeds()
        self._init_sample_articles()

    def _init_sample_feeds(self) -> None:
        feeds = [
            ("Hacker News", "https://hnews.io/rss", "https://news.ycombinator.com",
             "Social news for programmers", FeedCategory.TECH),
            ("Lobste.rs", "https://lobste.rs/rss", "https://lobste.rs",
             "Computing-focused link aggregation", FeedCategory.DEV),
            ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index",
             "https://arstechnica.com", "Technology news and analysis", FeedCategory.TECH),
            ("MIT Technology Review", "https://www.technologyreview.com/feed/",
             "https://technologyreview.com", "Emerging technologies", FeedCategory.SCIENCE),
            ("CSS-Tricks", "https://css-tricks.com/feed/",
             "https://css-tricks.com", "Web design and development", FeedCategory.DESIGN),
            ("The Verge", "https://www.theverge.com/rss/index.xml",
             "https://theverge.com", "Technology, science, art", FeedCategory.TECH),
            ("Dev.to", "https://dev.to/feed", "https://dev.to",
             "Developer community", FeedCategory.DEV),
            ("Nature", "https://www.nature.com/nature.rss",
             "https://nature.com", "Scientific research", FeedCategory.SCIENCE),
        ]
        for title, url, site, desc, cat in feeds:
            self._feeds.append(Feed(
                title=title, url=url, site_url=site,
                description=desc, category=cat,
            ))

    def _init_sample_articles(self) -> None:
        now = time.time()
        samples = [
            ("Nyrqis OS Reaches v1.0", "https://example.com/nyrqis-v1",
             "The mycelium-powered operating system hits its first stable release with Wayland compositor, "
             "hardware acceleration, and a complete app ecosystem.",
             "After 18 months of development, Nyrqis OS v1.0 is here. The release includes a custom Wayland "
             "compositor with DRM/KMS backend, hardware-accelerated rendering via Vulkan and EGL, a complete "
             "desktop environment with window management, and over 90 built-in applications.", "Alice Wunderland",
             now - 3600, self._feeds[0].feed_id, FeedCategory.TECH),
            ("Understanding Rust's Ownership Model", "https://example.com/rust-ownership",
             "A deep dive into how Rust's borrow checker ensures memory safety without garbage collection.",
             "Rust's ownership system is one of its most distinctive features. Unlike garbage-collected languages, "
             "Rust tracks memory ownership at compile time. Each value has exactly one owner, and when the owner "
             "goes out of scope, the value is dropped. This prevents data races and use-after-free bugs.",
             "Bob Builder", now - 7200, self._feeds[1].feed_id, FeedCategory.DEV),
            ("Quantum Computing Breakthrough", "https://example.com/quantum-breakthrough",
             "Researchers achieve 99.9% gate fidelity in a 100-qubit quantum processor.",
             "A team at MIT has demonstrated a 100-qubit quantum processor with unprecedented gate fidelity. "
             "The breakthrough could accelerate practical quantum computing applications in drug discovery, "
             "cryptography, and materials science.", "Charlie Root", now - 14400, self._feeds[3].feed_id, FeedCategory.SCIENCE),
            ("CSS Container Queries Are Here", "https://example.com/css-container-queries",
             "Container queries are finally shipping in all major browsers, changing how we write responsive CSS.",
             "Container queries allow components to adapt based on their parent container's size rather than "
             "the viewport. This is a game-changer for component-based design systems where the same component "
             "needs to look different in a sidebar vs main content area.", "Dev Team", now - 21600, self._feeds[4].feed_id, FeedCategory.DESIGN),
            ("Apple Announces M4 Ultra Chip", "https://example.com/m4-ultra",
             "Apple's new M4 Ultra delivers 3x performance for machine learning workloads.",
             "The M4 Ultra features a 40-core GPU, 16-core Neural Engine with 38 TOPS, and up to 256GB "
             "unified memory. Early benchmarks show dramatic improvements in AI inference tasks.",
             "Editor", now - 28800, self._feeds[5].feed_id, FeedCategory.TECH),
            ("The State of WebAssembly 2026", "https://example.com/wasm-2026",
             "WebAssembly expands beyond the browser with WASI preview 2 and component model.",
             "WebAssembly continues to evolve beyond its browser origins. WASI preview 2 brings a standardized "
             "system interface, enabling Wasm modules to run on servers, edge networks, and IoT devices. "
             "The component model allows Wasm modules to compose together.", "Developer", now - 36000, self._feeds[6].feed_id, FeedCategory.DEV),
            ("New Exoplanet Discovered in Habitable Zone", "https://example.com/exoplanet",
             "JWST confirms an Earth-sized planet with water vapor in its atmosphere.",
             "The James Webb Space Telescope has confirmed the discovery of an Earth-sized exoplanet orbiting "
             "a Sun-like star in the habitable zone. Spectroscopic analysis reveals water vapor and possible "
             "biosignature gases in the atmosphere.", "Dr. Nova", now - 43200, self._feeds[7].feed_id, FeedCategory.SCIENCE),
            ("Building Accessible UIs with Nyrqis", "https://example.com/accessible-ui",
             "How Nyrqis OS implements WCAG 2.1 AA compliance across all system components.",
             "Nyrqis OS takes accessibility seriously with a built-in screen reader, high contrast themes, "
             "keyboard navigation for all components, and an accessibility audit tool that identifies "
             "unlabeled elements. All 90+ UI components support ARIA semantics.", "A11y Lead", now - 50400, self._feeds[0].feed_id, FeedCategory.TECH),
            ("Rust vs Go for Systems Programming", "https://example.com/rust-go",
             "Comparing Rust and Go for building operating system components in 2026.",
             "Both Rust and Go have matured significantly. Rust offers zero-cost abstractions and fearless "
             "concurrency, while Go provides simpler syntax and faster compilation. For OS-level code, "
             "Rust's ownership model gives it an edge in memory safety.", "Systems Blog", now - 57600, self._feeds[1].feed_id, FeedCategory.DEV),
            ("Dark Matter Map Reveals New Structures", "https://example.com/dark-matter",
             "The largest dark matter map ever created reveals cosmic web filaments in unprecedented detail.",
             "Using data from the Euclid space telescope, astronomers have created a dark matter map covering "
             "10 billion light-years. The map reveals previously unseen filaments connecting galaxy clusters.",
             "Science Daily", now - 64800, self._feeds[3].feed_id, FeedCategory.SCIENCE),
            ("Figma's New Variable System", "https://example.com/figma-variables",
             "Figma introduces typed variables, mode switching, and advanced aliasing for design systems.",
             "Figma's variable system now supports String, Number, Boolean, and Color types with multiple "
             "modes for dark/light themes. Advanced aliasing allows tokens to reference other tokens, "
             "creating flexible design system architectures.", "Design Weekly", now - 72000, self._feeds[4].feed_id, FeedCategory.DESIGN),
            ("Linux Kernel 7.0 Released", "https://example.com/linux-7",
             "Major release brings Rust driver support, improved scheduling, and better power management.",
             "Linux 7.0 includes initial Rust support for out-of-tree drivers, the EEVDF scheduler for "
             "better latency, and significant power management improvements for ARM laptops.",
             "Kernel Dev", now - 79200, self._feeds[6].feed_id, FeedCategory.DEV),
        ]

        for title, url, summary, content, author, ts, feed_id, cat in samples:
            feed_title = ""
            for f in self._feeds:
                if f.feed_id == feed_id:
                    feed_title = f.title
                    break
            article = Article(
                title=title, url=url, summary=summary, content=content,
                author=author, published=ts, feed_id=feed_id,
                feed_title=feed_title,
                is_read=(ts < now - 40000),
                categories=[cat.value],
            )
            self._articles.append(article)
            # Add to feed
            for f in self._feeds:
                if f.feed_id == feed_id:
                    f.articles.append(article)
                    f.article_count += 1
                    if not article.is_read:
                        f.unread_count += 1
                    break

    # ── Feed Management ───────────────────────────────────────────────

    def add_feed(self, title: str, url: str, category: FeedCategory = FeedCategory.OTHER) -> Feed:
        feed = Feed(title=title, url=url, category=category)
        self._feeds.append(feed)
        return feed

    def remove_feed(self, feed_id: str) -> bool:
        for i, feed in enumerate(self._feeds):
            if feed.feed_id == feed_id:
                self._feeds.pop(i)
                self._articles = [a for a in self._articles if a.feed_id != feed_id]
                return True
        return False

    def get_feed(self, feed_id: str) -> Optional[Feed]:
        for f in self._feeds:
            if f.feed_id == feed_id:
                return f
        return None

    @property
    def feeds(self) -> List[Feed]:
        return list(self._feeds)

    def select_feed(self, feed_id: str) -> None:
        self._current_feed = self.get_feed(feed_id)
        self._selected_index = 0

    def select_all_feeds(self) -> None:
        self._current_feed = None
        self._selected_index = 0

    @property
    def current_feed(self) -> Optional[Feed]:
        return self._current_feed

    # ── Article Operations ────────────────────────────────────────────

    def get_articles(self) -> List[Article]:
        """Get filtered and sorted articles."""
        if self._current_feed:
            articles = list(self._current_feed.articles)
        else:
            articles = list(self._articles)

        if self._filter_read:
            articles = [a for a in articles if not a.is_read]
        if self._filter_starred:
            articles = [a for a in articles if a.is_starred]
        if self._search_query:
            q = self._search_query.lower()
            articles = [a for a in articles
                        if q in a.title.lower() or q in a.summary.lower() or q in a.author.lower()]

        articles.sort(key=lambda a: a.published, reverse=self._sort_newest)
        return articles

    def mark_read(self, article_id: str) -> bool:
        for a in self._articles:
            if a.article_id == article_id:
                if not a.is_read:
                    a.is_read = True
                    self._update_feed_unread(a.feed_id, -1)
                return True
        return False

    def mark_unread(self, article_id: str) -> bool:
        for a in self._articles:
            if a.article_id == article_id:
                if a.is_read:
                    a.is_read = False
                    self._update_feed_unread(a.feed_id, 1)
                return True
        return False

    def toggle_star(self, article_id: str) -> bool:
        for a in self._articles:
            if a.article_id == article_id:
                a.is_starred = not a.is_starred
                return a.is_starred
        return False

    def mark_all_read(self) -> int:
        count = 0
        for a in self._articles:
            if not a.is_read:
                a.is_read = True
                count += 1
        for f in self._feeds:
            f.unread_count = 0
        return count

    def _update_feed_unread(self, feed_id: str, delta: int) -> None:
        for f in self._feeds:
            if f.feed_id == feed_id:
                f.unread_count = max(0, f.unread_count + delta)
                break

    @property
    def total_unread(self) -> int:
        return sum(f.unread_count for f in self._feeds)

    @property
    def total_articles(self) -> int:
        return len(self._articles)

    # ── View State ────────────────────────────────────────────────────

    def open_article(self, article_id: str = None) -> Optional[Article]:
        if article_id:
            for a in self._articles:
                if a.article_id == article_id:
                    self._selected_article = a
                    self._view_mode = "article"
                    self.mark_read(article_id)
                    return a
        articles = self.get_articles()
        if 0 <= self._selected_index < len(articles):
            a = articles[self._selected_index]
            self._selected_article = a
            self._view_mode = "article"
            self.mark_read(a.article_id)
            return a
        return None

    def close_article(self) -> None:
        self._selected_article = None
        self._view_mode = "list"
        self._scroll_pos = 0

    def scroll_article(self, delta: int) -> None:
        self._scroll_pos = max(0, self._scroll_pos + delta)

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def selected_article(self) -> Optional[Article]:
        return self._selected_article

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        articles = self.get_articles()
        self._selected_index = min(len(articles) - 1, self._selected_index + 1)

    # ── Search & Filter ───────────────────────────────────────────────

    def set_search(self, query: str) -> None:
        self._search_query = query
        self._selected_index = 0

    def toggle_filter_read(self) -> bool:
        self._filter_read = not self._filter_read
        return self._filter_read

    def toggle_filter_starred(self) -> bool:
        self._filter_starred = not self._filter_starred
        return self._filter_starred

    def toggle_sort(self) -> bool:
        self._sort_newest = not self._sort_newest
        return self._sort_newest

    # ── Rendering ─────────────────────────────────────────────────────

    def render_feed_list(self, width: int = 30) -> List[str]:
        """Render the feed sidebar."""
        lines = []
        lines.append(f" 📰 Feeds")
        lines.append("─" * width)

        # All feeds
        all_unread = self.total_unread
        marker = "▸" if self._current_feed is None else " "
        lines.append(f"{marker} 📥 All Feeds ({all_unread})")

        # By category
        categories = {}
        for feed in self._feeds:
            cat = feed.category
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(feed)

        for cat, feeds in categories.items():
            color = CATEGORY_COLORS.get(cat, "#95A5A6")
            lines.append(f"  ── {cat.value} ──")
            for feed in sorted(feeds, key=lambda f: -f.unread_count):
                marker = "▸" if (self._current_feed and self._current_feed.feed_id == feed.feed_id) else " "
                unread = f" ({feed.unread_count})" if feed.unread_count > 0 else ""
                lines.append(f"  {marker} {feed.title[:width - 10]}{unread}")

        lines.append("─" * width)
        return lines

    def render_article_list(self, width: int = 60) -> List[str]:
        """Render the article list."""
        lines = []

        # Header
        feed_name = self._current_feed.title if self._current_feed else "All Feeds"
        lines.append(f" 📰 {feed_name}")
        if self._search_query:
            lines.append(f" 🔍 \"{self._search_query}\"")
        lines.append(f" {self.total_unread} unread · {self.total_articles} total")
        lines.append("─" * width)

        # Articles
        articles = self.get_articles()
        if not articles:
            lines.append("  No articles match your filter.")
        else:
            for i, article in enumerate(articles):
                marker = "▸" if i == self._selected_index else " "
                star = "⭐" if article.is_starred else "  "
                unread = "●" if not article.is_read else "○"

                line = f"{marker}{unread}{star} {article.title[:width - 12]}"
                time_str = article.time_ago
                if time_str:
                    line += f"{' ' * max(1, width - len(line) - len(time_str))}{time_str}"
                lines.append(line[:width])

                # Summary preview
                if not article.is_read:
                    preview = f"    {article.summary_preview[:width - 5]}"
                    lines.append(preview[:width])

                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Read  S:Star  R:Read/Unread")
        return lines

    def render_article(self, width: int = 72) -> List[str]:
        """Render a full article view."""
        a = self._selected_article
        if not a:
            return ["No article selected"]

        lines = []
        lines.append(f" 📰 {a.title}")
        lines.append("─" * width)
        lines.append(f" By: {a.author}")
        lines.append(f" Date: {a.date_str}")
        lines.append(f" Feed: {a.feed_title}")
        lines.append(f" 🔗 {a.url[:width - 5]}")
        lines.append("─" * width)

        # Content
        content = a.content or a.summary
        plain = re.sub(r'<[^>]+>', '', content)

        # Word wrap
        for paragraph in plain.split("\n\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                lines.append("")
                continue
            while len(paragraph) > width - 2:
                split = paragraph[:width - 2].rfind(" ")
                if split <= 0:
                    split = width - 2
                lines.append(f" {paragraph[:split]}")
                paragraph = paragraph[split:].lstrip()
            lines.append(f" {paragraph}")

        lines.append("")
        lines.append("─" * width)
        lines.append(" Esc:Back  S:Star  U:Unread  ←→:Navigate")
        return lines

    def render(self, width: int = 72, height: int = 30) -> List[str]:
        if self._view_mode == "article":
            return self.render_article(width)
        return self.render_article_list(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "article":
            return self._handle_article_key(key)
        return self._handle_list_key(key)

    def _handle_list_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.open_article()
            return "open"
        elif key == "s" or key == "S":
            articles = self.get_articles()
            if 0 <= self._selected_index < len(articles):
                self.toggle_star(articles[self._selected_index].article_id)
            return "star"
        elif key == "r":
            articles = self.get_articles()
            if 0 <= self._selected_index < len(articles):
                a = articles[self._selected_index]
                if a.is_read:
                    self.mark_unread(a.article_id)
                else:
                    self.mark_read(a.article_id)
            return "toggle_read"
        elif key == "/":
            return "search"
        elif key == "f":
            self.toggle_filter_read()
            return "filter_read"
        elif key == "F":
            self.toggle_filter_starred()
            return "filter_starred"
        elif key == "n":
            self.toggle_sort()
            return "toggle_sort"
        elif key == "a":
            self.mark_all_read()
            return "mark_all_read"
        return None

    def _handle_article_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.close_article()
            return "back"
        elif key == "s" or key == "S":
            if self._selected_article:
                self.toggle_star(self._selected_article.article_id)
            return "star"
        elif key == "u":
            if self._selected_article:
                self.mark_unread(self._selected_article.article_id)
            return "unread"
        elif key == "ArrowUp" or key == "k":
            self.scroll_article(-3)
            return "scroll_up"
        elif key == "ArrowDown" or key == "j":
            self.scroll_article(3)
            return "scroll_down"
        return None
