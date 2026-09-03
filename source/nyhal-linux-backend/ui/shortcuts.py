"""Shortcuts — Unified keyboard shortcut system for Nyrqis.

Provides a central shortcut registry that:
- Registers shortcuts with modifiers + key + action
- Dispatches key events to the correct component
- Supports customizable keybindings
- Shows shortcut overlay (hold Super to see all shortcuts)
- Per-application shortcut overrides
- Conflict detection and resolution

References:
    - NFS-001 §7: behaviors (WHEN/IF/DO)
    - Apple HIG: Keyboard Shortcuts
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Modifier(Enum):
    NONE = 0
    CTRL = auto()
    ALT = auto()
    SHIFT = auto()
    SUPER = auto()  # Command on macOS, Super on Linux
    CTRL_SHIFT = auto()
    CTRL_ALT = auto()
    ALT_SHIFT = auto()
    SUPER_SHIFT = auto()


class ShortcutScope(Enum):
    GLOBAL = auto()      # Always active
    DESKTOP = auto()     # Active on desktop
    WINDOW = auto()      # Active when a window is focused
    APP = auto()         # Active within a specific app
    TEXT = auto()         # Active in text input fields
    LOCK_SCREEN = auto()  # Active on lock screen


@dataclass
class Shortcut:
    """A keyboard shortcut definition."""
    id: str
    modifier: Modifier
    key: str
    action: str  # action ID or callback name
    label: str = ""
    description: str = ""
    scope: ShortcutScope = ShortcutScope.GLOBAL
    app_id: Optional[str] = None  # for APP scope
    category: str = "General"
    enabled: bool = True
    hidden: bool = False  # don't show in overlay


@dataclass
class ShortcutEvent:
    """A resolved shortcut event."""
    shortcut: Shortcut
    handled: bool = False


# ---------------------------------------------------------------------------
# ShortcutRegistry
# ---------------------------------------------------------------------------

class ShortcutRegistry:
    """Central keyboard shortcut registry for Nyrqis.

    Registers shortcuts, detects conflicts, dispatches key events,
    and provides a shortcut overlay.

    Parameters
    ----------
    app_id : str
        Application identifier for app-scoped shortcuts.
    """

    def __init__(self, app_id: str = "nyrqis"):
        self.app_id = app_id
        self._shortcuts: Dict[str, Shortcut] = {}
        self._key_map: Dict[Tuple[Modifier, str], str] = {}
        self._callbacks: Dict[str, List[Callable]] = {}
        self._global_overrides: Dict[str, Shortcut] = {}
        self._app_overrides: Dict[str, Dict[str, Shortcut]] = {}
        self._enabled = True
        self._overlay_visible = False

        # Register defaults
        self._register_defaults()

    # -- Registration ---------------------------------------------------

    def register(
        self,
        modifier: Modifier,
        key: str,
        action: str,
        label: str = "",
        description: str = "",
        scope: ShortcutScope = ShortcutScope.GLOBAL,
        app_id: Optional[str] = None,
        category: str = "General",
        hidden: bool = False,
    ) -> Shortcut:
        """Register a keyboard shortcut.

        Returns the created Shortcut. Raises ValueError on conflict.
        """
        shortcut_id = f"{modifier.name}:{key}"
        if shortcut_id in self._shortcuts:
            existing = self._shortcuts[shortcut_id]
            if existing and existing.scope != ShortcutScope.APP:
                raise ValueError(
                    f"Conflict: {modifier.name}+{key} is already bound "
                    f"to '{existing.action}' (scope={existing.scope.name})"
                )

        shortcut = Shortcut(
            id=shortcut_id,
            modifier=modifier,
            key=key,
            action=action,
            label=label or action,
            description=description,
            scope=scope,
            app_id=app_id,
            category=category,
            hidden=hidden,
        )
        self._shortcuts[shortcut_id] = shortcut
        self._key_map[(modifier, key)] = shortcut_id
        return shortcut

    def unregister(self, modifier: Modifier, key: str) -> bool:
        """Unregister a shortcut."""
        shortcut_id = f"{modifier.name}:{key}"
        if shortcut_id in self._shortcuts:
            del self._shortcuts[shortcut_id]
            self._key_map.pop((modifier, key), None)
            return True
        return False

    def get(self, modifier: Modifier, key: str) -> Optional[Shortcut]:
        """Get a shortcut by modifier + key."""
        shortcut_id = f"{modifier.name}:{key}"
        return self._shortcuts.get(shortcut_id)

    def get_by_action(self, action: str) -> List[Shortcut]:
        """Get all shortcuts for a given action."""
        return [s for s in self._shortcuts.values() if s.action == action]

    @property
    def all_shortcuts(self) -> List[Shortcut]:
        """All registered shortcuts."""
        return list(self._shortcuts.values())

    # -- Callbacks ------------------------------------------------------

    def on(self, action: str, callback: Callable) -> None:
        """Register a callback for a shortcut action."""
        self._callbacks.setdefault(action, []).append(callback)

    def off(self, action: str, callback: Callable) -> None:
        """Remove a callback."""
        if action in self._callbacks:
            self._callbacks[action] = [
                cb for cb in self._callbacks[action] if cb != callback
            ]

    # -- Dispatch -------------------------------------------------------

    def dispatch(
        self,
        modifier: Modifier,
        key: str,
        scope: ShortcutScope = ShortcutScope.GLOBAL,
        app_id: Optional[str] = None,
    ) -> Optional[ShortcutEvent]:
        """Dispatch a key event.

        Checks all matching shortcuts (global first, then scope-specific).
        Returns a ShortcutEvent if a shortcut was found and fired.
        """
        if not self._enabled:
            return None

        # Check app overrides first
        if app_id and app_id in self._app_overrides:
            override_id = f"{modifier.name}:{key}"
            if override_id in self._app_overrides[app_id]:
                shortcut = self._app_overrides[app_id][override_id]
                if shortcut.enabled:
                    event = ShortcutEvent(shortcut=shortcut, handled=True)
                    self._fire_callbacks(shortcut.action)
                    return event

        # Check global overrides
        override_id = f"{modifier.name}:{key}"
        if override_id in self._global_overrides:
            shortcut = self._global_overrides[override_id]
            if shortcut.enabled:
                event = ShortcutEvent(shortcut=shortcut, handled=True)
                self._fire_callbacks(shortcut.action)
                return event

        # Normal lookup
        shortcut_id = f"{modifier.name}:{key}"
        shortcut = self._shortcuts.get(shortcut_id)
        if shortcut is None:
            return None

        if not shortcut.enabled:
            return None

        # Scope check
        if not self._scope_matches(shortcut.scope, scope):
            return None

        # App scope check
        if shortcut.scope == ShortcutScope.APP:
            if shortcut.app_id and shortcut.app_id != app_id:
                return None

        event = ShortcutEvent(shortcut=shortcut, handled=True)
        self._fire_callbacks(shortcut.action)
        return event

    def _scope_matches(self, shortcut_scope: ShortcutScope,
                       current_scope: ShortcutScope) -> bool:
        """Check if a shortcut's scope matches the current context."""
        # GLOBAL always matches
        if shortcut_scope == ShortcutScope.GLOBAL:
            return True
        # Exact match
        return shortcut_scope == current_scope

    def _fire_callbacks(self, action: str) -> None:
        """Fire all callbacks for an action."""
        for cb in self._callbacks.get(action, []):
            try:
                cb()
            except Exception:
                pass

    # -- Overrides ------------------------------------------------------

    def override_global(self, modifier: Modifier, key: str,
                        action: str, label: str = "") -> Shortcut:
        """Override a global shortcut."""
        shortcut_id = f"{modifier.name}:{key}"
        shortcut = Shortcut(
            id=shortcut_id, modifier=modifier, key=key,
            action=action, label=label or action,
            scope=ShortcutScope.GLOBAL,
        )
        self._global_overrides[shortcut_id] = shortcut
        return shortcut

    def override_app(self, app_id: str, modifier: Modifier, key: str,
                     action: str, label: str = "") -> Shortcut:
        """Override a shortcut for a specific app."""
        shortcut_id = f"{modifier.name}:{key}"
        shortcut = Shortcut(
            id=shortcut_id, modifier=modifier, key=key,
            action=action, label=label or action,
            scope=ShortcutScope.APP, app_id=app_id,
        )
        self._app_overrides.setdefault(app_id, {})[shortcut_id] = shortcut
        return shortcut

    # -- Overlay --------------------------------------------------------

    def show_overlay(self) -> None:
        """Show the shortcut overlay."""
        self._overlay_visible = True

    def hide_overlay(self) -> None:
        """Hide the shortcut overlay."""
        self._overlay_visible = False

    @property
    def overlay_visible(self) -> bool:
        return self._overlay_visible

    def get_overlay_shortcuts(self, scope: ShortcutScope = ShortcutScope.GLOBAL,
                              app_id: Optional[str] = None) -> List[Shortcut]:
        """Get shortcuts to display in the overlay, grouped by category."""
        result = []
        seen_actions: Set[str] = set()

        for shortcut in sorted(self._shortcuts.values(), key=lambda s: s.category):
            if shortcut.hidden:
                continue
            if not shortcut.enabled:
                continue
            if not self._scope_matches(shortcut.scope, scope):
                continue
            if shortcut.scope == ShortcutScope.APP and shortcut.app_id != app_id:
                continue
            # Deduplicate by action within scope
            if shortcut.action in seen_actions:
                continue
            seen_actions.add(shortcut.action)
            result.append(shortcut)

        return result

    def render_overlay(self, width: int = 800,
                       height: int = 600) -> Tuple[bytes, int, int]:
        """Render the shortcut overlay to an RGB buffer.

        Shows all shortcuts grouped by category.
        Returns (rgb_bytes, width, height).
        """
        buf = bytearray(width * height * 3)

        # Semi-transparent dark background
        overlay_bg = (20, 20, 30)
        for i in range(0, len(buf), 3):
            buf[i] = overlay_bg[0]
            buf[i + 1] = overlay_bg[1]
            buf[i + 2] = overlay_bg[2]

        shortcuts = self.get_overlay_shortcuts()
        categories: Dict[str, List[Shortcut]] = {}
        for s in shortcuts:
            categories.setdefault(s.category, []).append(s)

        # Render category headers and shortcuts
        y = 40
        cat_color = (80, 140, 255)
        key_color = (230, 230, 240)
        desc_color = (150, 150, 170)

        for cat_name, cat_shortcuts in categories.items():
            # Category header placeholder
            self._fill_rect(buf, width, 40, y, 120, 16, cat_color)
            y += 28

            for shortcut in cat_shortcuts:
                # Key combo placeholder
                self._fill_rect(buf, width, 60, y, 80, 14, key_color)
                # Description placeholder
                self._fill_rect(buf, width, 160, y, 200, 14, desc_color)
                y += 22

            y += 12

        return bytes(buf), width, height

    # -- Enable/Disable -------------------------------------------------

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    # -- Conflict detection ---------------------------------------------

    def check_conflicts(self) -> List[Tuple[Shortcut, Shortcut]]:
        """Check for conflicting shortcuts.

        Returns pairs of shortcuts that share the same modifier+key.
        """
        conflicts = []
        by_key: Dict[str, List[Shortcut]] = {}
        for s in self._shortcuts.values():
            key = f"{s.modifier.name}:{s.key}"
            by_key.setdefault(key, []).append(s)

        for key, shortcuts in by_key.items():
            if len(shortcuts) > 1:
                for i in range(len(shortcuts)):
                    for j in range(i + 1, len(shortcuts)):
                        conflicts.append((shortcuts[i], shortcuts[j]))

        return conflicts

    # -- Serialization --------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "app_id": self.app_id,
            "shortcuts": [
                {
                    "id": s.id,
                    "modifier": s.modifier.name,
                    "key": s.key,
                    "action": s.action,
                    "label": s.label,
                    "description": s.description,
                    "scope": s.scope.name,
                    "category": s.category,
                    "enabled": s.enabled,
                }
                for s in self._shortcuts.values()
            ],
        }

    def import_overrides(self, data: Dict[str, Any]) -> int:
        """Import shortcut overrides from a dictionary.

        Returns the number of overrides imported.
        """
        count = 0
        for key, override_data in data.get("overrides", {}).items():
            try:
                modifier = Modifier[override_data["modifier"]]
                key_str = override_data["key"]
                action = override_data["action"]
                self.override_global(modifier, key_str, action)
                count += 1
            except (KeyError, ValueError):
                continue
        return count

    # -- Default shortcuts ----------------------------------------------

    def _register_defaults(self) -> None:
        """Register the default Nyrqis keyboard shortcuts."""

        # -- Window management --
        self.register(Modifier.CTRL, "w", "window.close",
                      label="Close Window", description="Close the focused window",
                      scope=ShortcutScope.WINDOW, category="Window")
        self.register(Modifier.CTRL, "n", "window.minimize",
                      label="Minimize Window", description="Minimize the focused window",
                      scope=ShortcutScope.WINDOW, category="Window")
        self.register(Modifier.CTRL, "m", "window.maximize",
                      label="Maximize Window", description="Toggle maximize",
                      scope=ShortcutScope.WINDOW, category="Window")
        self.register(Modifier.CTRL, "f", "window.fullscreen",
                      label="Fullscreen", description="Toggle fullscreen mode",
                      scope=ShortcutScope.WINDOW, category="Window")

        # -- Window snapping --
        self.register(Modifier.SUPER, "Left", "window.snap_left",
                      label="Snap Left", description="Snap window to left half",
                      scope=ShortcutScope.WINDOW, category="Window")
        self.register(Modifier.SUPER, "Right", "window.snap_right",
                      label="Snap Right", description="Snap window to right half",
                      scope=ShortcutScope.WINDOW, category="Window")
        self.register(Modifier.SUPER, "Up", "window.snap_top",
                      label="Snap Top", description="Snap window to top half",
                      scope=ShortcutScope.WINDOW, category="Window")
        self.register(Modifier.SUPER, "Down", "window.snap_bottom",
                      label="Snap Bottom", description="Snap window to bottom half",
                      scope=ShortcutScope.WINDOW, category="Window")

        # -- Application shortcuts --
        self.register(Modifier.CTRL, "t", "app.terminal",
                      label="Open Terminal", description="Open a new terminal window",
                      scope=ShortcutScope.DESKTOP, category="Applications")
        self.register(Modifier.CTRL, "e", "app.files",
                      label="Open Files", description="Open the file manager",
                      scope=ShortcutScope.DESKTOP, category="Applications")
        self.register(Modifier.CTRL, ",", "app.settings",
                      label="Open Settings", description="Open system settings",
                      scope=ShortcutScope.DESKTOP, category="Applications")

        # -- Workspace shortcuts --
        self.register(Modifier.CTRL, "1", "workspace.1",
                      label="Workspace 1", description="Switch to workspace 1",
                      scope=ShortcutScope.GLOBAL, category="Workspace")
        self.register(Modifier.CTRL, "2", "workspace.2",
                      label="Workspace 2", description="Switch to workspace 2",
                      scope=ShortcutScope.GLOBAL, category="Workspace")
        self.register(Modifier.CTRL, "3", "workspace.3",
                      label="Workspace 3", description="Switch to workspace 3",
                      scope=ShortcutScope.GLOBAL, category="Workspace")
        self.register(Modifier.CTRL, "4", "workspace.4",
                      label="Workspace 4", description="Switch to workspace 4",
                      scope=ShortcutScope.GLOBAL, category="Workspace")
        self.register(Modifier.CTRL_SHIFT, "Left", "workspace.prev",
                      label="Previous Workspace", description="Switch to previous workspace",
                      scope=ShortcutScope.GLOBAL, category="Workspace")
        self.register(Modifier.CTRL_SHIFT, "Right", "workspace.next",
                      label="Next Workspace", description="Switch to next workspace",
                      scope=ShortcutScope.GLOBAL, category="Workspace")

        # -- System shortcuts --
        self.register(Modifier.CTRL, "l", "system.lock",
                      label="Lock Screen", description="Lock the screen",
                      scope=ShortcutScope.GLOBAL, category="System")
        self.register(Modifier.CTRL_SHIFT, "Escape", "system.monitor",
                      label="System Monitor", description="Open system monitor",
                      scope=ShortcutScope.GLOBAL, category="System")
        self.register(Modifier.SUPER, "space", "system.launcher",
                      label="App Launcher", description="Open the app launcher",
                      scope=ShortcutScope.GLOBAL, category="System")
        self.register(Modifier.CTRL, "p", "system.palette",
                      label="Command Palette", description="Open command palette",
                      scope=ShortcutScope.GLOBAL, category="System")

        # -- Editing shortcuts --
        self.register(Modifier.CTRL, "z", "edit.undo",
                      label="Undo", description="Undo last action",
                      scope=ShortcutScope.TEXT, category="Editing")
        self.register(Modifier.CTRL_SHIFT, "z", "edit.redo",
                      label="Redo", description="Redo last action",
                      scope=ShortcutScope.TEXT, category="Editing")
        self.register(Modifier.CTRL, "a", "edit.select_all",
                      label="Select All", description="Select all content",
                      scope=ShortcutScope.TEXT, category="Editing")
        self.register(Modifier.CTRL, "c", "edit.copy",
                      label="Copy", description="Copy selection to clipboard",
                      scope=ShortcutScope.TEXT, category="Editing")
        self.register(Modifier.CTRL, "v", "edit.paste",
                      label="Paste", description="Paste from clipboard",
                      scope=ShortcutScope.TEXT, category="Editing")
        self.register(Modifier.CTRL, "x", "edit.cut",
                      label="Cut", description="Cut selection to clipboard",
                      scope=ShortcutScope.TEXT, category="Editing")

        # -- Accessibility --
        self.register(Modifier.CTRL, "=", "a11y.zoom_in",
                      label="Zoom In", description="Increase zoom level",
                      scope=ShortcutScope.GLOBAL, category="Accessibility")
        self.register(Modifier.CTRL, "-", "a11y.zoom_out",
                      label="Zoom Out", description="Decrease zoom level",
                      scope=ShortcutScope.GLOBAL, category="Accessibility")
        self.register(Modifier.CTRL, "0", "a11y.zoom_reset",
                      label="Reset Zoom", description="Reset zoom to 100%",
                      scope=ShortcutScope.GLOBAL, category="Accessibility")

        # -- Screenshot --
        self.register(Modifier.CTRL_SHIFT, "3", "screenshot.full",
                      label="Full Screenshot", description="Capture full screen",
                      scope=ShortcutScope.GLOBAL, category="Screenshot")
        self.register(Modifier.CTRL_SHIFT, "4", "screenshot.region",
                      label="Region Screenshot", description="Capture a region",
                      scope=ShortcutScope.GLOBAL, category="Screenshot")

        # -- Navigation --
        self.register(Modifier.ALT, "Tab", "nav.switcher",
                      label="Task Switcher", description="Switch between windows",
                      scope=ShortcutScope.GLOBAL, category="Navigation")
        self.register(Modifier.CTRL, "Tab", "nav.switcher",
                      label="Task Switcher", description="Switch between windows",
                      scope=ShortcutScope.GLOBAL, category="Navigation")

        # -- Spotlight --
        # Super+Space is already registered as system.launcher above

    # -- Helpers --------------------------------------------------------

    def _fill_rect(self, buf: bytearray, buf_width: int,
                   x: int, y: int, w: int, h: int,
                   color: Tuple[int, int, int]) -> None:
        """Fill a rectangle in an RGB buffer."""
        buf_height = len(buf) // (buf_width * 3)
        for dy in range(h):
            for dx in range(w):
                px = x + dx
                py = y + dy
                if 0 <= px < buf_width and 0 <= py < buf_height:
                    idx = (py * buf_width + px) * 3
                    if idx + 2 < len(buf):
                        buf[idx] = color[0]
                        buf[idx + 1] = color[1]
                        buf[idx + 2] = color[2]

    def format_shortcut(self, shortcut: Shortcut) -> str:
        """Format a shortcut as a human-readable string."""
        parts = []
        if shortcut.modifier == Modifier.CTRL_SHIFT:
            parts.append("Ctrl+Shift")
        elif shortcut.modifier == Modifier.CTRL_ALT:
            parts.append("Ctrl+Alt")
        elif shortcut.modifier == Modifier.ALT_SHIFT:
            parts.append("Alt+Shift")
        elif shortcut.modifier == Modifier.SUPER_SHIFT:
            parts.append("Super+Shift")
        elif shortcut.modifier == Modifier.CTRL:
            parts.append("Ctrl")
        elif shortcut.modifier == Modifier.ALT:
            parts.append("Alt")
        elif shortcut.modifier == Modifier.SHIFT:
            parts.append("Shift")
        elif shortcut.modifier == Modifier.SUPER:
            parts.append("Super")
        parts.append(shortcut.key)
        return "+".join(parts)


__all__ = [
    "ShortcutRegistry",
    "Shortcut",
    "ShortcutEvent",
    "Modifier",
    "ShortcutScope",
]
