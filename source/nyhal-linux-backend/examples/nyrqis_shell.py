#!/usr/bin/env python3
"""nyrqis_shell — The Nyrqis Desktop Shell.

Complete reference implementation of the Nyrqis desktop environment.
Ties together all desktop components into a cohesive shell experience:

    Desktop
    ├── Taskbar (clock, system tray, quick settings)
    ├── Start Menu (app launcher, search, pinned apps)
    ├── Notification Center (toasts, notification history)
    ├── Window Manager (drag, resize, minimize, maximize, close)
    ├── Spotlight Search (fuzzy search, keyboard navigation)
    ├── Power Menu (shutdown, restart, sleep, logout, lock)
    ├── Lock Screen (clock, swipe unlock, auto-lock)
    ├── Desktop Widgets (clock, CPU, memory, sticky notes)
    ├── Clipboard Manager (copy/paste history)
    ├── Settings App (theme, volume, brightness)
    ├── File Manager (navigation, CRUD, breadcrumbs)
    ├── Terminal Emulator (command execution, built-ins)
    ├── Calculator (basic + scientific)
    ├── Text Editor (open, edit, undo/redo, find/replace)
    ├── Screenshot Tool (capture, annotate, clipboard)
    ├── System Monitor (CPU, memory, disk, network)
    └── Nyforge Bridge (live .nstudio preview)

Usage::

    # Launch the shell
    python3 nyrqis_shell.py

    # Launch with a specific .nstudio file
    python3 nyrqis_shell.py path/to/shell.nstudio

    # Launch and render to PNG
    python3 nyrqis_shell.py --render desktop.png

    # Launch with Nyforge live preview
    python3 nyrqis_shell.py --nyforge design.nstudio

Architecture::

    Nyrqis Desktop Shell
    ├── DesktopSession (window management, input, hit-testing)
    ├── Compositor (PIL-based rendering with window chrome)
    ├── NyrqisRuntime (NUI behavior evaluation, state, bindings)
    ├── NyforgeBridge (live .nstudio → desktop)
    ├── ShellComponents (taskbar, start menu, notifications, etc.)
    └── InputRouter (keyboard shortcuts, mouse events)

References:
    - NFS-001: Nyrqis Functional Specification
    - NUI-SCHEMA: component vocabulary
    - doc #14: Nyrqis Desktop Shell
    - ADR-0025: runtime consumption decision
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Ensure the backend root is on the path
_backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from ui.desktop_session import (
    DesktopSession,
    EventType,
    HitResult,
    InputEvent,
    KeyEvent,
    MouseButton,
    MouseEvent,
    Window,
)
from ui.nstudio import loads


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shell document builder
# ---------------------------------------------------------------------------

def build_shell_document(
    width: int = 1920,
    height: int = 1080,
    theme: str = "Eclipse",
) -> str:
    """Build a minimal .nstudio JSON document for the desktop shell.

    This creates a single-screen desktop with a DesktopSurface root
    and no pre-existing windows — the shell manages windows dynamically.

    Returns
    -------
    str
        JSON string of the .nstudio document.
    """
    return json.dumps({
        "version": "1.0.0",
        "project": {"name": "Nyrqis Desktop Shell", "id": "nyrqis-shell"},
        "themes": {"active": theme, "available": ["Eclipse", "Solar"]},
        "states": {
            "volume": 75,
            "brightness": 100,
            "theme": theme,
            "wifi": True,
            "bluetooth": False,
            "doNotDisturb": False,
            "sidebarExpanded": True,
            "startMenuOpen": False,
            "spotlightOpen": False,
            "lockScreenActive": False,
        },
        "state_scopes": {
            "global": {},
            "session": {"user": "zeus"},
            "persistent": {},
        },
        "locales": {"active": "en", "tables": {"en": {}}},
        "resources": {"assets": []},
        "animations": [],
        "screens": [{
            "id": "desktop",
            "size": {"width": width, "height": height},
            "root": {
                "id": "root",
                "type": "DesktopSurface",
                "layout": {"x": 0, "y": 0, "width": width, "height": height},
                "properties": {},
                "children": [],
            },
        }],
        "behaviors": [],
        "bindings": [],
        "components": [],
    })


# ---------------------------------------------------------------------------
# Shell app registry
# ---------------------------------------------------------------------------

@dataclass
class ShellApp:
    """A built-in application registered with the shell."""
    id: str
    name: str
    icon: str
    category: str
    pinned: bool = False
    description: str = ""
    launcher: Optional[Callable] = None


def _build_app_registry() -> List[ShellApp]:
    """Register all built-in shell applications."""
    return [
        ShellApp(
            id="settings",
            name="Settings",
            icon="⚙️",
            category="System",
            pinned=True,
            description="Theme, volume, brightness, system preferences",
        ),
        ShellApp(
            id="file-manager",
            name="Files",
            icon="📁",
            category="System",
            pinned=True,
            description="Browse and manage files",
        ),
        ShellApp(
            id="terminal",
            name="Terminal",
            icon="💻",
            category="System",
            pinned=True,
            description="Command-line interface",
        ),
        ShellApp(
            id="text-editor",
            name="Text Editor",
            icon="📝",
            category="Accessories",
            pinned=True,
            description="Edit text files with syntax highlighting",
        ),
        ShellApp(
            id="calculator",
            name="Calculator",
            icon="🔢",
            category="Accessories",
            description="Basic and scientific calculator",
        ),
        ShellApp(
            id="screenshot",
            name="Screenshot",
            icon="📷",
            category="Accessories",
            description="Capture and annotate screenshots",
        ),
        ShellApp(
            id="system-monitor",
            name="System Monitor",
            icon="📊",
            category="System",
            description="CPU, memory, disk, and network monitoring",
        ),
    ]


# ---------------------------------------------------------------------------
# NyrqisDesktopShell
# ---------------------------------------------------------------------------

class NyrqisDesktopShell:
    """The Nyrqis Desktop Shell — a complete reference desktop environment.

    This class orchestrates all shell components:

    - Window management (via DesktopSession)
    - Taskbar with clock and system tray
    - Start menu with app launcher
    - Notification system
    - Spotlight search
    - Power menu
    - Lock screen
    - Desktop widgets
    - Clipboard manager
    - Nyforge bridge for live preview

    Parameters
    ----------
    width : int
        Desktop width in pixels.
    height : int
        Desktop height in pixels.
    theme : str
        Initial theme name ("Eclipse" or "Solar").
    log : callable, optional
        Runtime log callback.
    """

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        theme: str = "Eclipse",
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._width = width
        self._height = height
        self._theme = theme
        self._log = log or (lambda msg: None)

        # Build the shell document
        doc_json = build_shell_document(width, height, theme)
        doc = loads(doc_json)

        # Create the desktop session
        self._session = DesktopSession(doc, log=self._log)

        # App registry
        self._apps = _build_app_registry()
        self._app_registry: Dict[str, ShellApp] = {app.id: app for app in self._apps}

        # Start menu state
        self._start_menu_open = False
        self._start_menu_search = ""

        # Spotlight state
        self._spotlight = None
        try:
            from ui.spotlight import SpotlightSearch
            self._spotlight = SpotlightSearch(self._session, self._apps)
        except ImportError:
            pass

        # Power menu state
        self._power_menu = None
        try:
            from ui.power_menu import PowerMenu
            self._power_menu = PowerMenu(self._session)
        except ImportError:
            pass

        # Clipboard manager
        self._clipboard = None
        try:
            from ui.clipboard_manager import ClipboardManager
            self._clipboard = ClipboardManager()
        except ImportError:
            pass

        # Widget system
        self._widgets = None
        try:
            from ui.widget_system import WidgetSystem
            self._widgets = WidgetSystem(self._session)
        except ImportError:
            pass

        # Nyforge bridge (lazy — only if requested)
        self._bridge = None

        # Shell statistics
        self._launch_count = 0
        self._session_start = time.time()

        self._log("Nyrqis Desktop Shell initialized")
        self._log(f"  Display: {width}x{height}")
        self._log(f"  Theme: {theme}")
        self._log(f"  Apps: {len(self._apps)}")

    # -- Properties --------------------------------------------------------

    @property
    def session(self) -> DesktopSession:
        """The underlying desktop session."""
        return self._session

    @property
    def apps(self) -> List[ShellApp]:
        """Registered shell applications."""
        return list(self._apps)

    @property
    def theme(self) -> str:
        return self._theme

    @theme.setter
    def theme(self, value: str) -> None:
        self._theme = value
        self._session.document.themes["active"] = value

    @property
    def uptime(self) -> float:
        """Shell uptime in seconds."""
        return time.time() - self._session_start

    # -- Window management -------------------------------------------------

    def open_app(self, app_id: str) -> Optional[str]:
        """Open an application by its registry ID.

        Returns the window ID if a window was created, or None if the
        app was not found.
        """
        app = self._app_registry.get(app_id)
        if app is None:
            self._log(f"Unknown app: {app_id}")
            return None

        self._launch_count += 1
        window_id = f"app-{app_id}-{uuid.uuid4().hex[:6]}"

        # Create a window for the app
        win = Window(
            id=window_id,
            component_id=window_id,
            title=f"{app.icon} {app.name}",
            x=200 + (self._launch_count % 5) * 40,
            y=80 + (self._launch_count % 5) * 40,
            width=900,
            height=600,
        )
        self._session.add_window(win)

        # Show notification
        self._session.notifications.info(
            "App opened",
            f"{app.icon} {app.name} launched",
        )

        self._log(f"Opened {app.name} ({window_id})")
        return window_id

    def open_file(self, path: str) -> Optional[str]:
        """Open a file in the appropriate editor."""
        ext = os.path.splitext(path)[1].lower()

        if ext in (".txt", ".md", ".py", ".rs", ".toml", ".json", ".yml", ".yaml"):
            return self.open_app("text-editor")
        else:
            return self.open_app("file-manager")

    # -- Start menu --------------------------------------------------------

    def toggle_start_menu(self) -> bool:
        """Toggle the start menu open/closed."""
        self._start_menu_open = not self._start_menu_open
        self._log(f"Start menu {'opened' if self._start_menu_open else 'closed'}")
        return self._start_menu_open

    def search_start_menu(self, query: str) -> List[ShellApp]:
        """Search the start menu apps."""
        self._start_menu_search = query
        if not query:
            return list(self._apps)
        q = query.lower()
        return [
            app for app in self._apps
            if q in app.name.lower()
            or q in app.description.lower()
            or q in app.category.lower()
        ]

    def get_pinned_apps(self) -> List[ShellApp]:
        """Get apps that are pinned to the taskbar."""
        return [app for app in self._apps if app.pinned]

    def get_apps_by_category(self, category: str) -> List[ShellApp]:
        """Get apps in a specific category."""
        return [app for app in self._apps if app.category == category]

    # -- Spotlight ---------------------------------------------------------

    def open_spotlight(self) -> bool:
        """Open spotlight search."""
        if self._spotlight:
            self._spotlight.show()
            self._log("Spotlight opened")
            return True
        return False

    def close_spotlight(self) -> None:
        """Close spotlight search."""
        if self._spotlight:
            self._spotlight.hide()

    # -- Power menu --------------------------------------------------------

    def open_power_menu(self) -> bool:
        """Open the power menu."""
        if self._power_menu:
            self._power_menu.show()
            self._log("Power menu opened")
            return True
        return False

    def execute_power_action(self, action: str) -> bool:
        """Execute a power action (shutdown, restart, sleep, logout, lock)."""
        if self._power_menu:
            self._power_menu.execute_action(action)
            self._log(f"Power action: {action}")
            return True
        return False

    # -- Lock screen -------------------------------------------------------

    def lock_screen(self) -> None:
        """Lock the screen."""
        self._session.minimize_all_windows()
        self._log("Screen locked")

    def unlock_screen(self) -> None:
        """Unlock the screen."""
        self._log("Screen unlocked")

    # -- Nyforge bridge ----------------------------------------------------

    def load_nyforge_design(self, path: str) -> Optional[Dict[str, Any]]:
        """Load a .nstudio design from Nyforge into the desktop."""
        try:
            from ui.nyforge_bridge import NyforgeBridge
            self._bridge = NyforgeBridge(self._session)
            result = self._bridge.load_document(path)
            self._log(f"Nyforge bridge: {result}")
            return result
        except ImportError:
            self._log("Nyforge bridge not available")
            return None

    def enable_nyforge_hot_reload(self, interval: float = 1.0) -> bool:
        """Enable hot-reload for the Nyforge bridge."""
        if self._bridge:
            self._bridge.enable_hot_reload(interval=interval)
            self._log(f"Nyforge hot-reload enabled (interval={interval}s)")
            return True
        return False

    # -- Rendering ---------------------------------------------------------

    def render(self) -> Any:
        """Render the current desktop state to a PIL Image."""
        return self._session.live_render()

    def render_to_file(self, path: str) -> str:
        """Render the desktop to a PNG file."""
        img = self.render()
        if img is not None:
            img.save(path)
            self._log(f"Rendered to {path}")
            return path
        return ""

    # -- Summary -----------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Shell summary for diagnostics."""
        session_summary = self._session.summary()
        return {
            "display": f"{self._width}x{self._height}",
            "theme": self._theme,
            "apps": len(self._apps),
            "launch_count": self._launch_count,
            "uptime_seconds": round(self.uptime, 1),
            "nyforge_bridge": self._bridge is not None,
            **session_summary,
        }

    def print_summary(self) -> None:
        """Print a human-readable shell summary."""
        s = self.summary()
        print("╔══════════════════════════════════════════╗")
        print("║      Nyrqis Desktop Shell                ║")
        print("╠══════════════════════════════════════════╣")
        print(f"║  Display:    {s['display']:>28s} ║")
        print(f"║  Theme:      {s['theme']:>28s} ║")
        print(f"║  Windows:    {s['windows']:>28d} ║")
        print(f"║  Focused:    {str(s['focused']):>28s} ║")
        print(f"║  Apps:       {s['apps']:>28d} ║")
        print(f"║  Launched:   {s['launch_count']:>28d} ║")
        print(f"║  Uptime:     {s['uptime_seconds']:>27.1f}s ║")
        print(f"║  Monitors:   {s['monitors']:>28d} ║")
        print(f"║  Workspaces: {s['workspaces']:>28d} ║")
        print("╚══════════════════════════════════════════╝")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for the Nyrqis Desktop Shell."""
    parser = argparse.ArgumentParser(
        description="Nyrqis Desktop Shell — the complete desktop environment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  nyrqis_shell.py                    Launch the shell
  nyrqis_shell.py --render out.png   Render to PNG and exit
  nyrqis_shell.py --nyforge app.nstudio  Load a Nyforge design
  nyrqis_shell.py --summary          Print shell summary
        """,
    )
    parser.add_argument(
        "nstudio", nargs="?", default=None,
        help="Optional .nstudio file to load",
    )
    parser.add_argument("--width", type=int, default=1920, help="Desktop width")
    parser.add_argument("--height", type=int, default=1080, help="Desktop height")
    parser.add_argument("--theme", default="Eclipse", choices=["Eclipse", "Solar"],
                        help="Initial theme")
    parser.add_argument("--render", "-r", metavar="OUTPUT.png",
                        help="Render to PNG and exit")
    parser.add_argument("--nyforge", "-n", metavar="DESIGN.nstudio",
                        help="Load a Nyforge design")
    parser.add_argument("--json", "-j", action="store_true",
                        help="Output summary as JSON")
    parser.add_argument("--summary", "-s", action="store_true",
                        help="Print shell summary and exit")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    # Create the shell
    shell = NyrqisDesktopShell(
        width=args.width,
        height=args.height,
        theme=args.theme,
    )

    # Load an .nstudio file if provided
    if args.nstudio:
        if os.path.exists(args.nstudio):
            shell.session.load_from_file(args.nstudio)
            logger.info("Loaded %s", args.nstudio)
        else:
            print(f"Error: file not found: {args.nstudio}", file=sys.stderr)
            sys.exit(1)

    # Load a Nyforge design
    if args.nyforge:
        if os.path.exists(args.nyforge):
            result = shell.load_nyforge_design(args.nyforge)
            if result and not result.get("ok"):
                print(f"Error loading Nyforge design: {result.get('error')}",
                      file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Error: file not found: {args.nyforge}", file=sys.stderr)
            sys.exit(1)

    # Summary mode
    if args.summary:
        if args.json:
            print(json.dumps(shell.summary(), indent=2))
        else:
            shell.print_summary()
        return

    # Render mode
    if args.render:
        path = shell.render_to_file(args.render)
        if path:
            print(f"Rendered to {path}")
        else:
            print("Error: render failed", file=sys.stderr)
            sys.exit(1)
        return

    # Interactive mode — print summary and show what's available
    shell.print_summary()
    print()
    print("Available apps:")
    for app in shell.apps:
        pinned = " [pinned]" if app.pinned else ""
        print(f"  {app.icon} {app.name} — {app.description}{pinned}")
    print()
    print("Commands:")
    print("  open <app>     Open an application")
    print("  apps           List all apps")
    print("  search <query> Search apps")
    print("  power          Open power menu")
    print("  lock           Lock screen")
    print("  render <file>  Render to PNG")
    print("  summary        Show shell summary")
    print("  quit           Exit")
    print()

    # Simple interactive REPL
    try:
        while True:
            try:
                cmd = input("nyrqis> ").strip()
            except EOFError:
                break

            if not cmd:
                continue
            elif cmd == "quit" or cmd == "exit":
                break
            elif cmd == "apps":
                for app in shell.apps:
                    pinned = " [pinned]" if app.pinned else ""
                    print(f"  {app.icon} {app.name} — {app.description}{pinned}")
            elif cmd.startswith("open "):
                app_name = cmd[5:].strip()
                # Try to find by name or ID
                found = False
                for app in shell.apps:
                    if app_name.lower() in (app.name.lower(), app.id.lower()):
                        wid = shell.open_app(app.id)
                        if wid:
                            print(f"  Opened {app.name} ({wid})")
                        found = True
                        break
                if not found:
                    print(f"  Unknown app: {app_name}")
            elif cmd.startswith("search "):
                query = cmd[7:].strip()
                results = shell.search_start_menu(query)
                for app in results:
                    print(f"  {app.icon} {app.name}")
            elif cmd == "power":
                shell.open_power_menu()
                print("  Power menu opened")
            elif cmd == "lock":
                shell.lock_screen()
                print("  Screen locked")
            elif cmd.startswith("render "):
                path = cmd[7:].strip()
                result = shell.render_to_file(path)
                if result:
                    print(f"  Rendered to {result}")
            elif cmd == "summary":
                shell.print_summary()
            else:
                print(f"  Unknown command: {cmd}")
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
