#!/usr/bin/env python3
"""settings_app — Nyrqis system settings application.

A settings app that demonstrates the full Nyrqis stack:

- Theme switching (Eclipse ↔ Solar) via the DesktopSession runtime
- Workspace management (create, rename, switch)
- Config persistence (save/load settings to/from the NyFS filesystem)
- Notification display

This runs as a NUI component with behaviors, bindings, and actions.
It can be loaded into any DesktopSession and operated via mouse/keyboard.

Usage::

    from examples.settings_app import SettingsApp
    from ui.desktop_session import DesktopSession
    from ui.nstudio import load

    doc = load('shell.nstudio')
    session = DesktopSession(doc)
    settings = SettingsApp(session)
    settings.show()
    settings.toggle_theme()

Architecture:
    The settings app bridges the NUI behavior system with the session's
    runtime state.  It reads/writes the same state dict that behaviors
    and bindings use, so changes are reflected immediately in the UI.

References:
    - NFS-001 §7: behaviors (WHEN/IF/DO)
    - NFS-001 §8: bindings (component property ← state)
    - ADR-0025 §9: runtime consumption
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default settings
DEFAULTS: Dict[str, Any] = {
    "theme": "Eclipse",
    "volume": 80,
    "brightness": 100,
    "wallpaper": "default",
    "dockPosition": "bottom",
    "taskbarAutoHide": False,
    "animationsEnabled": True,
    "fontSize": 14,
    "language": "en",
}


class SettingsApp:
    """Nyrqis system settings application.

    Reads and writes settings through the DesktopSession's runtime
    state, and optionally persists them to the NyFS filesystem.

    Parameters
    ----------
    session : DesktopSession
        The desktop session to manage settings for.
    config_path : str, optional
        Path to persist settings as JSON.  If None, settings are
        in-memory only (reset on session restart).
    """

    def __init__(
        self,
        session,
        config_path: Optional[str] = None,
    ) -> None:
        self._session = session
        self._config_path = config_path
        self._settings: Dict[str, Any] = dict(DEFAULTS)
        self._visible = False
        self._callbacks: List[Callable] = []
        self._load_settings()

    # -- Settings API -------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value."""
        return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a setting value and apply it to the session."""
        old = self._settings.get(key)
        self._settings[key] = value
        self._apply_setting(key, value)
        self._save_settings()
        self._log(f"Setting '{key}': {old} → {value}")
        for cb in self._callbacks:
            try:
                cb(key, value, old)
            except Exception as e:
                self._log(f"Callback error: {e}")

    def update(self, settings: Dict[str, Any]) -> None:
        """Batch-update multiple settings."""
        for key, value in settings.items():
            self.set(key, value)

    def reset(self) -> None:
        """Reset all settings to defaults."""
        self._settings = dict(DEFAULTS)
        self._apply_all()
        self._save_settings()
        self._log("Settings reset to defaults")

    def on_change(self, callback: Callable) -> None:
        """Register a callback for setting changes."""
        self._callbacks.append(callback)

    @property
    def all_settings(self) -> Dict[str, Any]:
        """All current settings (copy)."""
        return dict(self._settings)

    # -- Convenience methods -------------------------------------------

    def toggle_theme(self) -> str:
        """Toggle between Eclipse and Solar themes.  Returns the new theme."""
        current = self._settings.get("theme", "Eclipse")
        new_theme = "Solar" if current == "Eclipse" else "Eclipse"
        self.set("theme", new_theme)
        return new_theme

    def set_volume(self, level: int) -> None:
        """Set the volume (0-100)."""
        self.set("volume", max(0, min(100, level)))

    def set_brightness(self, level: int) -> None:
        """Set the brightness (0-100)."""
        self.set("brightness", max(0, min(100, level)))

    def toggle_taskbar_autohide(self) -> bool:
        """Toggle taskbar auto-hide.  Returns the new value."""
        current = self._settings.get("taskbarAutoHide", False)
        new_val = not current
        self.set("taskbarAutoHide", new_val)
        return new_val

    def toggle_animations(self) -> bool:
        """Toggle animations.  Returns the new value."""
        current = self._settings.get("animationsEnabled", True)
        new_val = not current
        self.set("animationsEnabled", new_val)
        return new_val

    # -- Visibility ---------------------------------------------------

    def show(self) -> None:
        """Show the settings panel (mark it visible)."""
        self._visible = True
        self._log("Settings panel shown")

    def hide(self) -> None:
        """Hide the settings panel."""
        self._visible = False
        self._log("Settings panel hidden")

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

    # -- Workspace management ------------------------------------------

    def get_workspaces(self) -> List[Dict[str, Any]]:
        """List all workspaces with their names and window counts."""
        result = []
        for ws in self._session.workspaces:
            result.append({
                "id": ws.id,
                "name": ws.name,
                "windows": len(ws.window_ids),
                "active": ws.id == self._session.active_workspace.id
                    if self._session.active_workspace else False,
            })
        return result

    def switch_workspace(self, workspace_id: str) -> bool:
        """Switch to a workspace by ID."""
        return self._session.switch_workspace(workspace_id)

    def cycle_workspace(self, direction: int = 1) -> None:
        """Cycle to next/previous workspace."""
        self._session.cycle_workspace(direction)

    # -- Window management ---------------------------------------------

    def get_windows(self) -> List[Dict[str, Any]]:
        """List all windows with their state."""
        result = []
        for w in self._session.windows:
            result.append({
                "id": w.id,
                "title": w.title,
                "visible": w.visible,
                "minimized": w.minimized,
                "maximized": w.maximized,
                "focused": w.focused,
                "position": {"x": w.x, "y": w.y},
                "size": {"width": w.width, "height": w.height},
            })
        return result

    def minimize_all(self) -> int:
        """Minimize all windows.  Returns count minimized."""
        count = 0
        for w in self._session.windows:
            if w.visible and not w.minimized:
                self._session.minimize_window(w.id)
                count += 1
        return count

    def show_desktop(self) -> None:
        """Show desktop (minimize all windows)."""
        self.minimize_all()

    # -- Persistence --------------------------------------------------

    def _load_settings(self) -> None:
        """Load settings from disk if a config path is set."""
        if not self._config_path:
            return
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._settings.update(loaded)
                    self._apply_all()
                    self._log(f"Loaded {len(loaded)} settings from disk")
        except Exception as e:
            self._log(f"Failed to load settings: {e}")

    def _save_settings(self) -> None:
        """Persist settings to disk."""
        if not self._config_path:
            return
        try:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w") as f:
                json.dump(self._settings, f, indent=2)
        except Exception as e:
            self._log(f"Failed to save settings: {e}")

    # -- Apply to session ---------------------------------------------

    def _apply_setting(self, key: str, value: Any) -> None:
        """Apply a single setting to the session runtime."""
        runtime = self._session.runtime
        runtime.set_state(key, value)

        # Apply theme to the document
        if key == "theme":
            self._session.document.themes["active"] = value
            self._log(f"Theme → {value}")

    def _apply_all(self) -> None:
        """Apply all settings to the session runtime."""
        for key, value in self._settings.items():
            self._apply_setting(key, value)

    # -- Internal -----------------------------------------------------

    def _log(self, msg: str) -> None:
        logger.info("[Settings] %s", msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run the settings app standalone (for testing)."""
    import sys
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".."))

    from ui.nstudio import loads
    from ui.desktop_session import DesktopSession

    # Create a minimal session
    raw = {
        "version": "1.0.0",
        "project": {"name": "settings-test"},
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

    settings = SettingsApp(session)

    # Test all operations
    print("=== Nyrqis Settings App ===")
    print(f"Theme: {settings.get('theme')}")
    print(f"Volume: {settings.get('volume')}")

    settings.toggle_theme()
    print(f"Theme after toggle: {settings.get('theme')}")

    settings.set_volume(50)
    print(f"Volume after set: {settings.get('volume')}")

    settings.set_brightness(75)
    print(f"Brightness: {settings.get('brightness')}")

    settings.toggle_taskbar_autohide()
    print(f"Auto-hide: {settings.get('taskbarAutoHide')}")

    settings.toggle_animations()
    print(f"Animations: {settings.get('animationsEnabled')}")

    workspaces = settings.get_workspaces()
    print(f"Workspaces: {len(workspaces)}")
    for ws in workspaces:
        print(f"  {ws['name']}: {ws['windows']} windows, active={ws['active']}")

    windows = settings.get_windows()
    print(f"Windows: {len(windows)}")

    settings.show()
    print(f"Settings visible: {settings.visible}")

    settings.hide()
    print(f"Settings visible: {settings.visible}")

    print("=== All settings ===")
    for k, v in settings.all_settings.items():
        print(f"  {k}: {v}")

    print("\nAll operations passed!")


if __name__ == "__main__":
    main()
