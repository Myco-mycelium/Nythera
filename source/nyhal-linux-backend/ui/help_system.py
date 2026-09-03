"""
Nyrqis Help — built-in help system with docs, shortcuts, and tutorials.

Features:
- Searchable documentation with categories
- Keyboard shortcuts reference (all system shortcuts)
- Interactive tutorials and guides
- FAQ section
- System information display
- What's New / release notes
- Context-sensitive help
- Keyboard navigation
"""

import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class HelpCategory(Enum):
    GETTING_STARTED = "Getting Started"
    KEYBOARD = "Keyboard Shortcuts"
    TUTORIALS = "Tutorials"
    FAQ = "FAQ"
    APPS = "Applications"
    SYSTEM = "System"
    TROUBLESHOOTING = "Troubleshooting"
    ABOUT = "About Nyrqis"


@dataclass
class HelpArticle:
    """A help article or documentation page."""
    title: str
    category: HelpCategory
    content: str
    tags: List[str] = field(default_factory=list)
    article_id: str = ""

    def __post_init__(self):
        if not self.article_id:
            self.article_id = self.title.lower().replace(" ", "_")[:30]


@dataclass
class ShortcutEntry:
    """A keyboard shortcut entry."""
    action: str
    keys: str
    category: str = "General"
    description: str = ""


@dataclass
class Tutorial:
    """An interactive tutorial."""
    title: str
    steps: List[str]
    current_step: int = 0
    completed: bool = False

    @property
    def progress(self) -> float:
        if not self.steps:
            return 100.0
        return (self.current_step / len(self.steps)) * 100

    @property
    def progress_str(self) -> str:
        return f"{self.current_step}/{len(self.steps)}"

    def next_step(self) -> bool:
        if self.current_step < len(self.steps) - 1:
            self.current_step += 1
            return True
        self.completed = True
        return False

    def prev_step(self) -> bool:
        if self.current_step > 0:
            self.current_step -= 1
            return True
        return False


# ─── Help System ─────────────────────────────────────────────────────────


class HelpSystem:
    """
    Built-in help system for Nyrqis OS.

    Provides searchable documentation, shortcuts reference, and tutorials.
    """

    def __init__(self):
        self._articles: List[HelpArticle] = []
        self._shortcuts: List[ShortcutEntry] = []
        self._tutorials: List[Tutorial] = []
        self._view_mode: str = "home"  # home, article, shortcuts, tutorial
        self._selected_index: int = 0
        self._current_article: Optional[HelpArticle] = None
        self._current_tutorial: Optional[Tutorial] = None
        self._search_query: str = ""
        self._filter_category: Optional[HelpCategory] = None
        self._scroll_pos: int = 0

        # Init data
        self._init_articles()
        self._init_shortcuts()
        self._init_tutorials()

    def _init_articles(self) -> None:
        self._articles = [
            HelpArticle(
                "Welcome to Nyrqis",
                HelpCategory.GETTING_STARTED,
                "Welcome to Nyrqis OS! This guide will help you get started with your new operating system.\n\n"
                "Nyrqis is a mycelium-powered operating system built on Wayland with hardware-accelerated "
                "rendering. It includes a complete desktop environment with window management, a file manager, "
                "terminal, web browser, and over 90 built-in applications.\n\n"
                "Key features:\n"
                "- Wayland compositor with Vulkan/EGL acceleration\n"
                "- Tiling and floating window management\n"
                "- Built-in package manager\n"
                "- Plugin system for third-party apps\n"
                "- Accessibility-first design\n"
                "- Theme engine with dark/light modes",
                ["welcome", "introduction", "start"],
            ),
            HelpArticle(
                "Desktop Navigation",
                HelpCategory.GETTING_STARTED,
                "Navigating the Nyrqis desktop:\n\n"
                "• Click the app launcher (bottom-left) to open apps\n"
                "• Use Spotlight (Ctrl+Space) to search for anything\n"
                "• Right-click the desktop for a context menu\n"
                "• Drag windows by their title bar to move them\n"
                "• Snap windows to screen edges for split view\n"
                "• Use Ctrl+Alt+Arrow to switch virtual desktops\n"
                "• Use Alt+Tab to cycle through open windows\n"
                "• The taskbar shows running apps and system tray",
                ["navigation", "desktop", "windows"],
            ),
            HelpArticle(
                "Window Management",
                HelpCategory.GETTING_STARTED,
                "Nyrqis supports both floating and tiling window modes:\n\n"
                "• Drag title bar to move windows\n"
                "• Resize by dragging window edges\n"
                "• Snap to left/right edge for split view\n"
                "• Snap to top for maximize\n"
                "• Double-click title bar to maximize\n"
                "• Ctrl+Q to close focused window\n"
                "• Ctrl+M to minimize\n"
                "• Ctrl+F to toggle fullscreen\n"
                "• Use workspace dots in taskbar for virtual desktops",
                ["windows", "tiling", "snap"],
            ),
            HelpArticle(
                "Terminal Usage",
                HelpCategory.TUTORIALS,
                "The Nyrqis terminal supports:\n\n"
                "• Full ANSI color support (256 colors + truecolor)\n"
                "• Split panes (Ctrl+Alt+S for horizontal, Ctrl+Alt+V for vertical)\n"
                "• Multiple tabs (Ctrl+T for new tab)\n"
                "• Copy/paste (Ctrl+Shift+C/V)\n"
                "• Scrollback buffer (5000 lines)\n"
                "• Font zoom (Ctrl+=/-)\n"
                "• Find text (Ctrl+Shift+F)\n"
                "• Profile-based configuration",
                ["terminal", "shell", "command"],
            ),
            HelpArticle(
                "File Manager",
                HelpCategory.TUTORIALS,
                "The Nyrqis file manager features:\n\n"
                "• Dual pane view for easy file operations\n"
                "• Built-in search (Ctrl+F)\n"
                "• File operations: copy, move, delete, rename\n"
                "• Archive support (zip, tar, gz)\n"
                "• File previews\n"
                "• Breadcrumb navigation\n"
                "• Sort by name, size, date, type\n"
                "• Bookmarks for frequent locations",
                ["files", "manager", "browse"],
            ),
            HelpArticle(
                "Keyboard Shortcuts",
                HelpCategory.KEYBOARD,
                "Nyrqis has extensive keyboard shortcuts. Press ? in any app to see "
                "context-specific shortcuts. Common shortcuts:\n\n"
                "Global:\n"
                "• Ctrl+Space — Spotlight search\n"
                "• Ctrl+Alt+T — Open terminal\n"
                "• Ctrl+Alt+L — Lock screen\n"
                "• Alt+Tab — Switch windows\n"
                "• Super — App launcher\n\n"
                "Window:\n"
                "• Ctrl+Q — Close window\n"
                "• Ctrl+M — Minimize\n"
                "• Ctrl+F — Fullscreen\n"
                "• Ctrl+←/→ — Snap left/right\n\n"
                "Workspace:\n"
                "• Ctrl+1/2/3/4 — Switch workspace\n"
                "• Ctrl+Alt+←/→ — Next/prev workspace\n"
                "• Ctrl+Shift+←/→ — Move window to workspace",
                ["shortcuts", "keyboard", "keys"],
            ),
            HelpArticle(
                "Accessibility Features",
                HelpCategory.APPS,
                "Nyrqis includes comprehensive accessibility:\n\n"
                "• Screen reader with polite/assertive announcements\n"
                "• High contrast mode (4px white focus ring)\n"
                "• Large text mode (1.25x scaling)\n"
                "• Reduced motion mode\n"
                "• Keyboard-only navigation for all apps\n"
                "• Focus indicators on all interactive elements\n"
                "• Accessibility audit tool (Ctrl+Shift+A)\n"
                "• Magnifier zoom (1x-5x)\n"
                "• Color-blind friendly themes",
                ["accessibility", "a11y", "screen reader"],
            ),
            HelpArticle(
                "Theme Customization",
                HelpCategory.APPS,
                "Customize your desktop appearance:\n\n"
                "• Open Settings → Appearance\n"
                "• Choose from 4 built-in themes (Eclipse, Solar, Dracula, Nord)\n"
                "• Create custom themes with the theme editor\n"
                "• WCAG contrast checking for all themes\n"
                "• Import/export themes as JSON\n"
                "• Per-app theme overrides\n"
                "• Animated transitions\n"
                "• Wallpaper selection with blur effects",
                ["themes", "appearance", "customize"],
            ),
            HelpArticle(
                "Plugin System",
                HelpCategory.APPS,
                "Extend Nyrqis with plugins:\n\n"
                "• Browse plugins in the Package Manager\n"
                "• Install plugins from the community registry\n"
                "• Plugin permissions system\n"
                "• Inter-plugin messaging bus\n"
                "• Hook into system events (on_click, on_key, on_render)\n"
                "• Plugins can add widgets, panels, and tray items\n"
                "• Sandboxed execution for security",
                ["plugins", "extensions", "add-ons"],
            ),
            HelpArticle(
                "Troubleshooting",
                HelpCategory.TROUBLESHOOTING,
                "Common issues and solutions:\n\n"
                "• Display not detected: Run 'nrvis --scan' to rescan outputs\n"
                "• Audio not working: Check audio mixer in Quick Settings\n"
                "• Network issues: Open Network Manager from system tray\n"
                "• Slow performance: Check System Monitor for resource usage\n"
                "• App not launching: Check service status in Service Manager\n"
                "• Theme not applied: Restart the compositor (Ctrl+Alt+Esc)\n"
                "• Plugin crash: Disable the plugin in Settings → Plugins\n"
                "• Check logs: Use the Log Viewer app for system logs",
                ["troubleshooting", "help", "fix", "problem"],
            ),
            HelpArticle(
                "About Nyrqis",
                HelpCategory.ABOUT,
                "Nyrqis OS v1.0\n"
                "The mycelium-powered operating system\n\n"
                "Built with:\n"
                "• Python (UI layer)\n"
                "• Rust (compositor, GPU backends)\n"
                "• Wayland (display protocol)\n"
                "• Vulkan/EGL (hardware rendering)\n"
                "• GBM/DRM (buffer management)\n\n"
                "License: MIT\n"
                "Repository: github.com/Myco-mycelium/Nythera\n"
                "Authors: The Nyrqis Community\n\n"
                "🍄 Nyrqis — Growing together.",
                ["about", "version", "credits"],
            ),
            HelpArticle(
                "What's New in v1.0",
                HelpCategory.ABOUT,
                "Release Notes — v1.0\n\n"
                "New features:\n"
                "• Wayland compositor with Vulkan acceleration\n"
                "• 90+ built-in applications\n"
                "• Web browser with tabs and bookmarks\n"
                "• Email client with folders and threading\n"
                "• Calendar with events and reminders\n"
                "• Terminal multiplexer with split panes\n"
                "• Process manager with resource graphs\n"
                "• Weather widget with forecasts\n"
                "• Disk analyzer with treemap visualization\n"
                "• RSS feed reader\n"
                "• Task manager with kanban view\n"
                "• Full accessibility suite\n"
                "• Theme engine with 4 built-in themes\n"
                "• Plugin system for extensibility\n"
                "• 2193+ tests passing",
                ["new", "release", "changelog", "v1.0"],
            ),
        ]

    def _init_shortcuts(self) -> None:
        self._shortcuts = [
            # System
            ShortcutEntry("Spotlight Search", "Ctrl+Space", "System"),
            ShortcutEntry("App Launcher", "Super", "System"),
            ShortcutEntry("Lock Screen", "Ctrl+Alt+L", "System"),
            ShortcutEntry("Power Menu", "Ctrl+Alt+P", "System"),
            ShortcutEntry("Screenshot", "Print Screen", "System"),
            ShortcutEntry("Toggle Notifications", "Ctrl+Alt+N", "System"),
            ShortcutEntry("System Monitor", "Ctrl+Alt+M", "System"),
            ShortcutEntry("Quick Settings", "Ctrl+Alt+Q", "System"),
            # Window
            ShortcutEntry("Close Window", "Ctrl+Q", "Window"),
            ShortcutEntry("Minimize", "Ctrl+M", "Window"),
            ShortcutEntry("Maximize", "Ctrl+J", "Window"),
            ShortcutEntry("Fullscreen", "Ctrl+F", "Window"),
            ShortcutEntry("Snap Left", "Ctrl+←", "Window"),
            ShortcutEntry("Snap Right", "Ctrl+→", "Window"),
            ShortcutEntry("Snap Top", "Ctrl+↑", "Window"),
            ShortcutEntry("Cycle Windows", "Alt+Tab", "Window"),
            # Workspace
            ShortcutEntry("Workspace 1", "Ctrl+1", "Workspace"),
            ShortcutEntry("Workspace 2", "Ctrl+2", "Workspace"),
            ShortcutEntry("Workspace 3", "Ctrl+3", "Workspace"),
            ShortcutEntry("Workspace 4", "Ctrl+4", "Workspace"),
            ShortcutEntry("Next Workspace", "Ctrl+Alt+→", "Workspace"),
            ShortcutEntry("Prev Workspace", "Ctrl+Alt+←", "Workspace"),
            ShortcutEntry("Move to Workspace 1", "Ctrl+Shift+1", "Workspace"),
            ShortcutEntry("Move to Workspace 2", "Ctrl+Shift+2", "Workspace"),
            # Apps
            ShortcutEntry("Open Terminal", "Ctrl+Alt+T", "Apps"),
            ShortcutEntry("Open File Manager", "Ctrl+Alt+E", "Apps"),
            ShortcutEntry("Open Settings", "Ctrl+Alt+,", "Apps"),
            ShortcutEntry("Open Browser", "Ctrl+Alt+B", "Apps"),
            # Accessibility
            ShortcutEntry("Zoom In", "Ctrl+=", "Accessibility"),
            ShortcutEntry("Zoom Out", "Ctrl+-", "Accessibility"),
            ShortcutEntry("Zoom Reset", "Ctrl+0", "Accessibility"),
            ShortcutEntry("Accessibility Audit", "Ctrl+Shift+A", "Accessibility"),
        ]

    def _init_tutorials(self) -> None:
        self._tutorials = [
            Tutorial(
                "Getting Started with Nyrqis",
                [
                    "Welcome! Let's set up your new Nyrqis desktop.\n\n"
                    "Step 1: Your desktop has a taskbar at the bottom with your app launcher on the left, "
                    "pinned apps, running apps, system tray, and clock on the right.",

                    "Step 2: Try opening the app launcher by clicking the Nyrqis logo (🍄) in the "
                    "bottom-left corner. You can also press the Super key or Ctrl+Space for Spotlight search.",

                    "Step 3: Open the Settings app to customize your desktop. You can change the theme, "
                    "display resolution, audio settings, and more.",

                    "Step 4: Right-click the desktop to access the context menu with options for "
                    "changing wallpapers, display settings, and opening a terminal.",

                    "Step 5: Try the terminal! Press Ctrl+Alt+T or open it from the launcher. "
                    "The terminal supports full ANSI colors, split panes, and multiple tabs.",

                    "Step 6: You're all set! Explore the apps, customize your desktop, and "
                    "check out the Help system (? key in any app) for more tips.",
                ],
            ),
            Tutorial(
                "Window Management Basics",
                [
                    "Nyrqis supports both floating and tiling window modes.\n\n"
                    "Step 1: Drag any window by its title bar to move it around the screen.",

                    "Step 2: Resize windows by dragging any edge or corner.",

                    "Step 3: Try window snapping! Drag a window to the left or right edge of the "
                    "screen to snap it to that half. Drag to a corner for quarter-snap.",

                    "Step 4: Use Ctrl+←/→ to snap the focused window to left/right half.",

                    "Step 5: Use Alt+Tab to cycle through open windows. Hold Alt and press Tab "
                    "repeatedly to cycle, then release Alt to switch.",

                    "Step 6: Press Super+1/2/3/4 to switch between virtual desktops. "
                    "Each desktop can have its own set of windows.",

                    "Step 7: Try the window overview! Press Super+W or drag a window to the "
                    "top of the screen to see all open windows.",
                ],
            ),
            Tutorial(
                "Terminal Power User",
                [
                    "Master the Nyrqis terminal with these power tips.\n\n"
                    "Step 1: Open the terminal with Ctrl+Alt+T. You get a full bash/zsh shell.",

                    "Step 2: Split the terminal! Press Ctrl+Alt+S for a horizontal split or "
                    "Ctrl+Alt+V for a vertical split. Each pane is an independent shell.",

                    "Step 3: Switch between panes with Ctrl+Alt+Arrow keys or Ctrl+Alt+N/P "
                    "for next/previous.",

                    "Step 4: Open multiple tabs with Ctrl+T. Switch with Ctrl+Tab.",

                    "Step 5: Resize panes with Ctrl+Alt++/-. The focused pane gets larger.",

                    "Step 6: Enable synchronized input mode with Ctrl+Alt+Z to type in all "
                    "panes simultaneously — great for managing multiple servers.",

                    "Step 7: Copy text from the terminal with Ctrl+Shift+C and paste with "
                    "Ctrl+Shift+V.",
                ],
            ),
        ]

    # ── Navigation ────────────────────────────────────────────────────

    def get_articles(self, category: HelpCategory = None) -> List[HelpArticle]:
        articles = self._articles
        if category:
            articles = [a for a in articles if a.category == category]
        if self._search_query:
            q = self._search_query.lower()
            articles = [a for a in articles
                        if q in a.title.lower() or q in a.content.lower() or
                        any(q in tag for tag in a.tags)]
        return articles

    def open_article(self, article_id: str) -> Optional[HelpArticle]:
        for a in self._articles:
            if a.article_id == article_id:
                self._current_article = a
                self._view_mode = "article"
                self._scroll_pos = 0
                return a
        return None

    def close_article(self) -> None:
        self._current_article = None
        self._view_mode = "home"
        self._scroll_pos = 0

    def start_tutorial(self, index: int = 0) -> Optional[Tutorial]:
        if 0 <= index < len(self._tutorials):
            self._current_tutorial = self._tutorials[index]
            self._current_tutorial.current_step = 0
            self._current_tutorial.completed = False
            self._view_mode = "tutorial"
            return self._current_tutorial
        return None

    def next_tutorial_step(self) -> bool:
        if self._current_tutorial:
            return self._current_tutorial.next_step()
        return False

    def prev_tutorial_step(self) -> bool:
        if self._current_tutorial:
            return self._current_tutorial.prev_step()
        return False

    def close_tutorial(self) -> None:
        self._current_tutorial = None
        self._view_mode = "home"

    def set_view(self, mode: str) -> None:
        self._view_mode = mode

    def set_search(self, query: str) -> None:
        self._search_query = query

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def current_article(self) -> Optional[HelpArticle]:
        return self._current_article

    @property
    def current_tutorial(self) -> Optional[Tutorial]:
        return self._current_tutorial

    @property
    def shortcuts(self) -> List[ShortcutEntry]:
        return list(self._shortcuts)

    @property
    def tutorials(self) -> List[Tutorial]:
        return list(self._tutorials)

    def scroll(self, delta: int) -> None:
        self._scroll_pos = max(0, self._scroll_pos + delta)

    def get_shortcuts_by_category(self) -> Dict[str, List[ShortcutEntry]]:
        cats = {}
        for s in self._shortcuts:
            if s.category not in cats:
                cats[s.category] = []
            cats[s.category].append(s)
        return cats

    # ── Rendering ─────────────────────────────────────────────────────

    def render_home(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🍄 Nyrqis Help Center")
        lines.append("─" * width)
        lines.append("")
        lines.append("  Welcome to Nyrqis Help! Choose a topic:")
        lines.append("")

        categories = [c for c in HelpCategory]
        for i, cat in enumerate(categories):
            count = len([a for a in self._articles if a.category == cat])
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"  {marker} {cat.value} ({count} articles)")

        lines.append("")
        lines.append("─" * width)
        lines.append(f"  Tutorials: {len(self._tutorials)} available")
        lines.append(f"  Shortcuts: {len(self._shortcuts)} registered")
        lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Open  S:Shortcuts  T:Tutorials  /:Search")
        return lines

    def render_article_list(self, width: int = 60) -> List[str]:
        lines = []
        cat = HelpCategory(list(HelpCategory)[self._selected_index]) if self._selected_index < len(HelpCategory) else HelpCategory.GETTING_STARTED
        lines.append(f" 📖 {cat.value}")
        lines.append("─" * width)

        articles = self.get_articles(cat)
        if self._search_query:
            lines.append(f" 🔍 \"{self._search_query}\" ({len(articles)} results)")

        for i, article in enumerate(articles):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"  {marker} {article.title}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Read  Esc:Back")
        return lines

    def render_article(self, width: int = 60) -> List[str]:
        if not self._current_article:
            return ["No article selected"]

        a = self._current_article
        lines = []
        lines.append(f" 📖 {a.title}")
        lines.append("─" * width)

        content_lines = a.content.split("\n")
        for line in content_lines[self._scroll_pos:self._scroll_pos + 25]:
            # Word wrap
            while len(line) > width - 2:
                split = line[:width - 2].rfind(" ")
                if split <= 0:
                    split = width - 2
                lines.append(f" {line[:split]}")
                line = line[split:].lstrip()
            lines.append(f" {line}")

        lines.append("─" * width)
        lines.append(" Esc:Back  ↑↓:Scroll  N:Next Article")
        return lines

    def render_shortcuts(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" ⌨️  Keyboard Shortcuts")
        lines.append("─" * width)

        cats = self.get_shortcuts_by_category()
        for cat_name, shortcuts in cats.items():
            lines.append(f"  ── {cat_name} ──")
            for s in shortcuts:
                key_padding = width - len(s.keys) - len(s.action) - 8
                lines.append(f"  {s.action}{' ' * max(1, key_padding)}{s.keys}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" Esc:Back  /:Search  ↑↓:Scroll")
        return lines

    def render_tutorial(self, width: int = 60) -> List[str]:
        if not self._current_tutorial:
            return ["No tutorial selected"]

        t = self._current_tutorial
        lines = []

        lines.append(f" 📚 {t.title}")
        lines.append(f" Step {t.current_step + 1}/{len(t.steps)}")

        # Progress bar
        pct = t.progress
        bar_width = 30
        filled = int(pct / 100 * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        lines.append(f" [{bar}] {pct:.0f}%")

        lines.append("─" * width)

        # Current step content
        step = t.steps[t.current_step]
        for line in step.split("\n"):
            while len(line) > width - 2:
                split = line[:width - 2].rfind(" ")
                if split <= 0:
                    split = width - 2
                lines.append(f" {line[:split]}")
                line = line[split:].lstrip()
            lines.append(f" {line}")

        lines.append("")
        lines.append("─" * width)

        nav = " Esc:Exit"
        if t.current_step > 0:
            nav += "  ←:Previous"
        if t.current_step < len(t.steps) - 1:
            nav += "  →:Next"
        else:
            nav += "  Enter:Complete"
        lines.append(nav)

        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        if self._view_mode == "article":
            return self.render_article(width)
        elif self._view_mode == "shortcuts":
            return self.render_shortcuts(width)
        elif self._view_mode == "tutorial":
            return self.render_tutorial(width)
        elif self._view_mode == "article_list":
            return self.render_article_list(width)
        return self.render_home(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "article":
            return self._handle_article_key(key)
        elif self._view_mode == "shortcuts":
            return self._handle_shortcuts_key(key)
        elif self._view_mode == "tutorial":
            return self._handle_tutorial_key(key)
        elif self._view_mode == "article_list":
            return self._handle_article_list_key(key)
        return self._handle_home_key(key)

    def _handle_home_key(self, key: str) -> Optional[str]:
        cats = list(HelpCategory)
        if key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(cats) - 1, self._selected_index + 1)
            return "select_down"
        elif key == "Enter":
            self._view_mode = "article_list"
            return "open_category"
        elif key == "s":
            self._view_mode = "shortcuts"
            return "shortcuts"
        elif key == "t":
            self.start_tutorial(0)
            return "tutorial"
        elif key == "/":
            return "search"
        return None

    def _handle_article_list_key(self, key: str) -> Optional[str]:
        cats = list(HelpCategory)
        cat = cats[self._selected_index] if self._selected_index < len(cats) else cats[0]
        articles = self.get_articles(cat)
        if key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(articles) - 1, self._selected_index + 1)
            return "select_down"
        elif key == "Enter":
            if 0 <= self._selected_index < len(articles):
                self.open_article(articles[self._selected_index].article_id)
            return "open_article"
        elif key == "Escape":
            self._view_mode = "home"
            self._selected_index = 0
            return "back"
        return None

    def _handle_article_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.close_article()
            return "back"
        elif key == "ArrowUp" or key == "k":
            self.scroll(-3)
            return "scroll_up"
        elif key == "ArrowDown" or key == "j":
            self.scroll(3)
            return "scroll_down"
        return None

    def _handle_shortcuts_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "home"
            return "back"
        elif key == "ArrowUp":
            self.scroll(-3)
            return "scroll_up"
        elif key == "ArrowDown":
            self.scroll(3)
            return "scroll_down"
        return None

    def _handle_tutorial_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.close_tutorial()
            return "back"
        elif key == "ArrowRight" or key == "Enter":
            if not self.next_tutorial_step():
                self.close_tutorial()
            return "next_step"
        elif key == "ArrowLeft":
            self.prev_tutorial_step()
            return "prev_step"
        return None
