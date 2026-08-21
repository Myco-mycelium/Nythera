#!/usr/bin/env python3
"""spotlight — Nyrqis spotlight / quick search.

A spotlight-style search overlay that demonstrates the full Nyrqis stack:

- Search apps, files, settings, and commands
- Fuzzy matching with ranked results
- Keyboard-driven navigation (up/down/enter/escape)
- Result categories (apps, files, commands, settings)
- Quick actions (open app, navigate to file, change setting)
- Visual overlay rendering

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class SearchResult:
    """A single search result."""
    id: str
    title: str
    subtitle: str = ""
    category: str = "apps"     # apps, files, commands, settings
    icon: str = "▸"
    action: Optional[str] = None  # Action ID to execute
    score: float = 0.0          # Relevance score
    data: Dict[str, Any] = field(default_factory=dict)


# Built-in searchable commands
BUILTIN_COMMANDS = [
    {"id": "cmd-theme", "title": "Change Theme",
     "subtitle": "Switch between Eclipse and Solar",
     "icon": "🎨", "category": "commands",
     "action": "toggle_theme"},
    {"id": "cmd-settings", "title": "Open Settings",
     "subtitle": "System settings and preferences",
     "icon": "⚙", "category": "commands",
     "action": "open_settings"},
    {"id": "cmd-terminal", "title": "Open Terminal",
     "subtitle": "Command-line interface",
     "icon": "▸", "category": "commands",
     "action": "open_terminal"},
    {"id": "cmd-files", "title": "Open File Manager",
     "subtitle": "Browse and manage files",
     "icon": "📁", "category": "commands",
     "action": "open_files"},
    {"id": "cmd-lock", "title": "Lock Screen",
     "subtitle": "Lock the desktop",
     "icon": "🔒", "category": "commands",
     "action": "lock_screen"},
    {"id": "cmd-workspace", "title": "Switch Workspace",
     "subtitle": "Cycle to next workspace",
     "icon": "🖥", "category": "commands",
     "action": "cycle_workspace"},
    {"id": "cmd-desktop", "title": "Show Desktop",
     "subtitle": "Minimize all windows",
     "icon": "🖥", "category": "commands",
     "action": "show_desktop"},
    {"id": "cmd-clipboard", "title": "Open Clipboard",
     "subtitle": "View clipboard history",
     "icon": "📋", "category": "commands",
     "action": "open_clipboard"},
]


class Spotlight:
    """Nyrqis spotlight / quick search.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session for executing actions.
    """

    def __init__(self, session=None) -> None:
        self._session = session
        self._query = ""
        self._results: List[SearchResult] = []
        self._selected_index: int = 0
        self._visible = False
        self._callbacks: List[Callable] = []
        self._searchable_apps = self._build_app_list()

    def _build_app_list(self) -> List[Dict[str, Any]]:
        """Build the list of searchable applications."""
        apps = [
            {"id": "app-settings", "title": "Settings",
             "subtitle": "System settings and preferences",
             "icon": "⚙", "category": "apps"},
            {"id": "app-files", "title": "Files",
             "subtitle": "File manager",
             "icon": "📁", "category": "apps"},
            {"id": "app-terminal", "title": "Terminal",
             "subtitle": "Command-line interface",
             "icon": "▸", "category": "apps"},
            {"id": "app-status", "title": "Status Client",
             "subtitle": "Check daemon status",
             "icon": "◈", "category": "apps"},
            {"id": "app-config", "title": "Config Manager",
             "subtitle": "Manage application configuration",
             "icon": "⊞", "category": "apps"},
            {"id": "app-nyforge", "title": "Nyforge",
             "subtitle": "Visual NUI designer",
             "icon": "✦", "category": "apps"},
        ]
        return apps

    # -- API ----------------------------------------------------------

    def show(self) -> None:
        """Show the spotlight search overlay."""
        self._visible = True
        self._query = ""
        self._results = []
        self._selected_index = 0
        self._dispatch("shown")

    def hide(self) -> None:
        """Hide the spotlight search."""
        self._visible = False
        self._query = ""
        self._results = []
        self._dispatch("hidden")

    def toggle(self) -> bool:
        """Toggle visibility.  Returns new state."""
        if self._visible:
            self.hide()
        else:
            self.show()
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def query(self) -> str:
        return self._query

    @property
    def results(self) -> List[SearchResult]:
        return list(self._results)

    @property
    def selected(self) -> Optional[SearchResult]:
        if 0 <= self._selected_index < len(self._results):
            return self._results[self._selected_index]
        return None

    @property
    def selected_index(self) -> int:
        return self._selected_index

    # -- Input --------------------------------------------------------

    def type_char(self, char: str) -> None:
        """Add a character to the search query."""
        if not self._visible:
            return
        self._query += char
        self._search()

    def backspace(self) -> None:
        """Remove the last character from the query."""
        if not self._visible or not self._query:
            return
        self._query = self._query[:-1]
        self._search()

    def clear_query(self) -> None:
        """Clear the search query."""
        self._query = ""
        self._results = []
        self._selected_index = 0

    def navigate_up(self) -> None:
        """Move selection up."""
        if self._results:
            self._selected_index = (
                (self._selected_index - 1) % len(self._results))

    def navigate_down(self) -> None:
        """Move selection down."""
        if self._results:
            self._selected_index = (
                (self._selected_index + 1) % len(self._results))

    def execute_selected(self) -> Optional[SearchResult]:
        """Execute the currently selected result.

        Returns the executed result, or None.
        """
        result = self.selected
        if result is None:
            return None
        self._execute_result(result)
        # Dispatch before hide so callbacks see the event
        self._dispatch("executed")
        self.hide()
        return result

    # -- Search -------------------------------------------------------

    def _search(self) -> None:
        """Run the search and update results."""
        if not self._query:
            self._results = []
            self._selected_index = 0
            return

        q = self._query.lower()
        results: List[SearchResult] = []

        # Search apps
        for app in self._searchable_apps:
            score = self._fuzzy_score(q, app["title"], app["subtitle"])
            if score > 0:
                results.append(SearchResult(
                    id=app["id"],
                    title=app["title"],
                    subtitle=app["subtitle"],
                    category=app["category"],
                    icon=app["icon"],
                    score=score,
                    data=app,
                ))

        # Search commands
        for cmd in BUILTIN_COMMANDS:
            score = self._fuzzy_score(q, cmd["title"], cmd.get("subtitle", ""))
            if score > 0:
                results.append(SearchResult(
                    id=cmd["id"],
                    title=cmd["title"],
                    subtitle=cmd.get("subtitle", ""),
                    category=cmd["category"],
                    icon=cmd["icon"],
                    action=cmd.get("action"),
                    score=score,
                    data=cmd,
                ))

        # Search settings
        settings_items = [
            {"id": "set-theme", "title": "Theme",
             "subtitle": "Eclipse / Solar", "icon": "🎨"},
            {"id": "set-volume", "title": "Volume",
             "subtitle": "0-100", "icon": "🔊"},
            {"id": "set-brightness", "title": "Brightness",
             "subtitle": "0-100", "icon": "☀"},
            {"id": "set-font", "title": "Font Size",
             "subtitle": "8-32", "icon": "A"},
            {"id": "set-lang", "title": "Language",
             "subtitle": "en, af, etc.", "icon": "🌐"},
        ]
        for s in settings_items:
            score = self._fuzzy_score(q, s["title"], s["subtitle"])
            if score > 0:
                results.append(SearchResult(
                    id=s["id"],
                    title=s["title"],
                    subtitle=s["subtitle"],
                    category="settings",
                    icon=s["icon"],
                    score=score,
                    data=s,
                ))

        # Sort by score (descending)
        results.sort(key=lambda r: r.score, reverse=True)
        self._results = results[:10]  # Top 10
        self._selected_index = 0
        self._dispatch("searched")

    def _fuzzy_score(self, query: str, title: str, subtitle: str = "") -> float:
        """Calculate a fuzzy match score.

        Returns 0.0 for no match, higher is better.
        """
        title_lower = title.lower()
        subtitle_lower = subtitle.lower()

        # Exact match
        if query == title_lower:
            return 1.0

        # Starts with
        if title_lower.startswith(query):
            return 0.9

        # Contains
        if query in title_lower:
            return 0.7

        # Fuzzy: all characters present in order
        qi = 0
        matched = 0
        for ch in title_lower:
            if qi < len(query) and ch == query[qi]:
                qi += 1
                matched += 1
        if qi == len(query):
            return 0.5 + (matched / len(title_lower)) * 0.2

        # Check subtitle
        if query in subtitle_lower:
            return 0.3

        return 0.0

    # -- Actions ------------------------------------------------------

    def _execute_result(self, result: SearchResult) -> None:
        """Execute a search result."""
        action = result.action or result.data.get("action")
        if not action:
            return

        self._log(f"Executing: {result.title} ({action})")

        if self._session is None:
            return

        # Route actions
        if action == "toggle_theme":
            doc = self._session.document
            current = doc.themes.get("active", "Eclipse")
            doc.themes["active"] = "Solar" if current == "Eclipse" else "Eclipse"
            self._session.runtime.set_state(
                "theme", doc.themes["active"])
            if hasattr(self._session, '_notifications'):
                self._session._notifications.info(
                    "Theme changed", doc.themes["active"])

        elif action == "open_settings":
            self._session._notifications.info("Settings", "Opening settings...")

        elif action == "open_terminal":
            self._session._notifications.info("Terminal", "Opening terminal...")

        elif action == "open_files":
            self._session._notifications.info("Files", "Opening file manager...")

        elif action == "lock_screen":
            if hasattr(self._session, '_lock_screen'):
                self._session._lock_screen.lock()
            self._session._notifications.info("Locked", "Screen locked")

        elif action == "cycle_workspace":
            self._session.cycle_workspace(1)

        elif action == "show_desktop":
            for w in self._session.windows:
                if w.visible and not w.minimized:
                    self._session.minimize_window(w.id)

        elif action == "open_clipboard":
            self._session._notifications.info("Clipboard", "Opening clipboard...")

    # -- Render to PIL ------------------------------------------------

    def render(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Any:
        """Render the spotlight overlay as a transparent PIL Image."""
        if not self._visible:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        img = Image.new("RGBA", (screen_width, screen_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        except (OSError, IOError):
            font = font_large = font_small = ImageFont.load_default()

        # Search box
        box_w = 600
        box_h = 50
        box_x = (screen_width - box_w) // 2
        box_y = 80

        # Overlay background
        draw.rectangle([0, 0, screen_width, screen_height],
                       fill=(0, 0, 0, 100))

        # Search box
        draw.rounded_rectangle(
            [box_x, box_y, box_x + box_w, box_y + box_h],
            radius=12, fill=(30, 30, 30, 240), outline=(80, 80, 80))

        # Search icon
        draw.text((box_x + 16, box_y + 14), "🔍",
                  fill=(150, 150, 150), font=font)

        # Query text
        query_display = self._query or "Search..."
        color = (230, 230, 230) if self._query else (120, 120, 120)
        draw.text((box_x + 44, box_y + 14), query_display,
                  fill=color, font=font_large)

        # Results
        if self._results:
            result_h = 48
            results_y = box_y + box_h + 8

            # Results background
            total_h = len(self._results) * result_h + 8
            draw.rounded_rectangle(
                [box_x, results_y, box_x + box_w, results_y + total_h],
                radius=12, fill=(30, 30, 30, 240), outline=(80, 80, 80))

            for i, result in enumerate(self._results):
                ry = results_y + 4 + i * result_h
                is_selected = (i == self._selected_index)

                # Selection highlight
                if is_selected:
                    draw.rounded_rectangle(
                        [box_x + 4, ry, box_x + box_w - 4, ry + result_h - 2],
                        radius=8, fill=(50, 80, 130, 200))

                # Icon
                draw.text((box_x + 16, ry + 12), result.icon,
                          fill=(200, 200, 200), font=font_large)

                # Title
                draw.text((box_x + 44, ry + 8), result.title,
                          fill=(230, 230, 230), font=font)

                # Subtitle
                if result.subtitle:
                    draw.text((box_x + 44, ry + 28), result.subtitle,
                              fill=(130, 130, 130), font=font_small)

                # Category badge
                cat_colors = {
                    "apps": (100, 149, 237),
                    "commands": (100, 200, 100),
                    "settings": (220, 180, 60),
                    "files": (200, 100, 100),
                }
                cat_color = cat_colors.get(result.category, (150, 150, 150))
                draw.text((box_x + box_w - 60, ry + 14), result.category,
                          fill=cat_color, font=font_small)
        else:
            # No results
            if self._query:
                draw.text(
                    (box_x + 20, box_y + box_h + 20),
                    "No results found",
                    fill=(120, 120, 120), font=font)
            else:
                draw.text(
                    (box_x + 20, box_y + box_h + 20),
                    "Type to search apps, commands, settings...",
                    fill=(100, 100, 100), font=font)

        return img

    # -- Callbacks ----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, None)
            except Exception:
                pass

    def _log(self, msg: str) -> None:
        import logging
        logging.getLogger(__name__).info("[Spotlight] %s", msg)


__all__ = ["Spotlight", "SearchResult"]
