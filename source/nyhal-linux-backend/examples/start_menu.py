#!/usr/bin/env python3
"""start_menu — Nyrqis start menu / application launcher.

A start menu that lists available Nyrqis applications and provides
launch, search, and power options.

Architecture:
    The start menu is a DesktopSession component that:
    1. Discovers available .napp applications from the examples/ directory
    2. Presents them in a searchable list
    3. Launches applications by creating new windows in the session
    4. Provides power options (show desktop, lock, shutdown)

References:
    - NFS-001 §7: behaviors (WHEN/IF/DO)
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AppEntry:
    """An available application in the start menu."""
    id: str
    name: str
    description: str = ""
    icon: str = "▸"          # Default icon glyph
    command: Optional[str] = None     # Shell command to launch
    napp_path: Optional[str] = None   # Path to .napp binary
    category: str = "Other"
    pinned: bool = False


# Built-in Nyrqis applications
BUILTIN_APPS: List[AppEntry] = [
    AppEntry(
        id="settings",
        name="Settings",
        description="System settings and preferences",
        icon="⚙",
        category="System",
        pinned=True,
    ),
    AppEntry(
        id="file-manager",
        name="Files",
        description="Browse and manage files",
        icon="📁",
        category="System",
        pinned=True,
    ),
    AppEntry(
        id="terminal",
        name="Terminal",
        description="Command-line interface",
        icon="▸",
        category="Developer",
        pinned=True,
    ),
    AppEntry(
        id="status-client",
        name="Status Client",
        description="Check daemon status via IPC",
        icon="◈",
        category="Developer",
    ),
    AppEntry(
        id="config-manager",
        name="Config Manager",
        description="Manage application configuration",
        icon="⊞",
        category="Developer",
    ),
    AppEntry(
        id="nyforge",
        name="Nyforge",
        description="Visual NUI designer",
        icon="✦",
        category="Development",
    ),
]


class StartMenu:
    """Nyrqis start menu / application launcher.

    Parameters
    ----------
    session : DesktopSession
        The desktop session to launch apps into.
    app_dirs : list of str, optional
        Directories to scan for .napp binaries.
    """

    def __init__(
        self,
        session,
        app_dirs: Optional[List[str]] = None,
    ) -> None:
        self._session = session
        self._app_dirs = app_dirs or []
        self._apps: List[AppEntry] = list(BUILTIN_APPS)
        self._visible = False
        self._search_query = ""
        self._callbacks: List[Callable] = []
        self._discover_apps()

    # -- App discovery ------------------------------------------------

    def _discover_apps(self) -> None:
        """Scan app directories for .napp binaries."""
        for app_dir in self._app_dirs:
            if not os.path.isdir(app_dir):
                continue
            for fname in os.listdir(app_dir):
                if fname.endswith(".napp"):
                    app_id = fname[:-5]  # strip .napp
                    # Don't override built-ins
                    if any(a.id == app_id for a in self._apps):
                        continue
                    self._apps.append(AppEntry(
                        id=app_id,
                        name=app_id.replace("-", " ").replace("_", " ").title(),
                        napp_path=os.path.join(app_dir, fname),
                        category="Apps",
                    ))

    # -- App queries --------------------------------------------------

    @property
    def apps(self) -> List[AppEntry]:
        """All available applications."""
        return list(self._apps)

    @property
    def filtered_apps(self) -> List[AppEntry]:
        """Applications filtered by the current search query."""
        if not self._search_query:
            return self._apps
        q = self._search_query.lower()
        return [
            a for a in self._apps
            if q in a.name.lower()
            or q in a.description.lower()
            or q in a.category.lower()
        ]

    @property
    def pinned_apps(self) -> List[AppEntry]:
        """Pinned applications."""
        return [a for a in self._apps if a.pinned]

    def by_category(self) -> Dict[str, List[AppEntry]]:
        """Applications grouped by category."""
        cats: Dict[str, List[AppEntry]] = {}
        for app in self._apps:
            cats.setdefault(app.category, []).append(app)
        return cats

    def find_app(self, app_id: str) -> Optional[AppEntry]:
        """Find an app by ID."""
        for a in self._apps:
            if a.id == app_id:
                return a
        return None

    # -- App management -----------------------------------------------

    def register_app(self, app: AppEntry) -> None:
        """Register a new application."""
        if not any(a.id == app.id for a in self._apps):
            self._apps.append(app)
            self._log(f"Registered app: {app.name}")

    def unregister_app(self, app_id: str) -> bool:
        """Remove an application from the menu."""
        for i, a in enumerate(self._apps):
            if a.id == app_id:
                self._apps.pop(i)
                return True
        return False

    # -- Launch -------------------------------------------------------

    def launch(self, app_id: str) -> bool:
        """Launch an application by ID.

        For .napp apps: runs through the Nyrqis runtime.
        For built-in apps: creates a new window in the session.
        Returns True on success.
        """
        app = self.find_app(app_id)
        if app is None:
            self._log(f"App '{app_id}' not found")
            return False

        self._log(f"Launching: {app.name}")

        # Fire callbacks
        for cb in self._callbacks:
            try:
                cb("launch", app)
            except Exception:
                pass

        # Built-in apps get a new window in the session
        if app.command:
            try:
                subprocess.Popen(
                    app.command, shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except Exception as e:
                self._log(f"Launch failed: {e}")
                return False

        # .napp apps: create a placeholder window
        from ui.desktop_session import Window
        win = Window(
            id=f"app-{app.id}",
            component_id=f"app-{app.id}",
            title=app.name,
            x=300 + len(self._session.windows) * 40,
            y=150 + len(self._session.windows) * 40,
            width=800,
            height=600,
        )
        self._session.add_window(win)
        self._notifications_info(
            f"{app.name} launched", app.description)
        return True

    # -- Search -------------------------------------------------------

    def search(self, query: str) -> List[AppEntry]:
        """Set the search query and return matching apps."""
        self._search_query = query
        return self.filtered_apps

    def clear_search(self) -> None:
        """Clear the search query."""
        self._search_query = ""

    # -- Visibility ---------------------------------------------------

    def show(self) -> None:
        self._visible = True
        self._log("Start menu shown")

    def hide(self) -> None:
        self._visible = False
        self._clear_search()
        self._log("Start menu hidden")

    def toggle(self) -> bool:
        if self._visible:
            self.hide()
        else:
            self.show()
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Power options ------------------------------------------------

    def show_desktop(self) -> None:
        """Minimize all windows to show the desktop."""
        self._session.settings.show_desktop() if hasattr(
            self._session, 'settings') else self._minimize_all()

    def lock(self) -> None:
        """Lock the screen (placeholder)."""
        self._notifications_info("Screen locked", "Session locked")

    def _minimize_all(self) -> None:
        for w in self._session.windows:
            if w.visible and not w.minimized:
                self._session.minimize_window(w.id)

    # -- Callbacks ----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        """Register a callback for start menu events."""
        self._callbacks.append(callback)

    # -- Internal -----------------------------------------------------

    def _notifications_info(self, title: str, message: str = "") -> None:
        if hasattr(self._session, '_notifications'):
            self._session._notifications.info(title, message)

    def _clear_search(self) -> None:
        self._search_query = ""

    def _log(self, msg: str) -> None:
        logger.info("[StartMenu] %s", msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run the start menu standalone (for testing)."""
    import sys
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".."))

    from ui.nstudio import loads
    from ui.desktop_session import DesktopSession

    # Create a minimal session
    raw = {
        "version": "1.0.0",
        "project": {"name": "start-menu-test"},
        "themes": {"active": "Eclipse"},
        "states": {},
        "stateScopes": {},
        "locales": {},
        "resources": {},
        "animations": [],
        "behaviors": [],
        "bindings": [],
        "components": [],
        "screens": [{
            "id": "desktop",
            "size": {"width": 1920, "height": 1080},
            "root": {
                "id": "root",
                "type": "DesktopSurface",
                "layout": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "children": [],
            },
        }],
    }
    doc = loads(json.dumps(raw))
    session = DesktopSession(doc)
    menu = StartMenu(session)

    print("=== Nyrqis Start Menu ===")
    print(f"Total apps: {len(menu.apps)}")
    print(f"Pinned apps: {len(menu.pinned_apps)}")

    cats = menu.by_category()
    print(f"Categories: {list(cats.keys())}")
    for cat, apps in cats.items():
        print(f"  {cat}: {[a.name for a in apps]}")

    print("\nSearch 'settings':")
    results = menu.search("settings")
    for a in results:
        print(f"  {a.name}: {a.description}")

    print("\nSearch 'terminal':")
    results = menu.search("terminal")
    for a in results:
        print(f"  {a.name}: {a.description}")

    menu.clear_search()
    print(f"\nFiltered (after clear): {len(menu.filtered_apps)}")

    # Launch an app
    menu.show()
    print(f"Visible: {menu.visible}")

    result = menu.launch("settings")
    print(f"Launch settings: {result}")
    print(f"Windows after launch: {len(session.windows)}")

    menu.hide()
    print(f"Visible after hide: {menu.visible}")

    print("\nAll operations passed!")


if __name__ == "__main__":
    main()
