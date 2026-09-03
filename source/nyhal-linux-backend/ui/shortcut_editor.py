"""
Nyrqis Shortcut Editor — keyboard shortcut management application.

Features:
- Edit system and application keyboard shortcuts
- Conflict detection with warnings
- Per-app shortcut bindings
- Shortcut categories and groups
- Import/export shortcut profiles
- Reset to defaults
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


class ShortcutScope(Enum):
    SYSTEM = "system"
    APPLICATION = "application"
    DESKTOP = "desktop"
    WINDOW = "window"
    ACCESSIBILITY = "accessibility"


class ModifierKey(Enum):
    CTRL = "Ctrl"
    ALT = "Alt"
    SHIFT = "Shift"
    SUPER = "Super"
    META = "Meta"
    PRINT = "Print"


SCOPE_ICONS = {
    ShortcutScope.SYSTEM: "⚙️",
    ShortcutScope.APPLICATION: "📱",
    ShortcutScope.DESKTOP: "🖥️",
    ShortcutScope.WINDOW: "🪟",
    ShortcutScope.ACCESSIBILITY: "♿",
}


@dataclass
class ShortcutBinding:
    """A keyboard shortcut binding."""
    shortcut_id: str
    name: str
    description: str = ""
    scope: ShortcutScope = ShortcutScope.SYSTEM
    # Key combination
    modifiers: List[ModifierKey] = field(default_factory=list)
    key: str = ""
    # App-specific
    app_name: str = ""
    # State
    enabled: bool = True
    is_default: bool = True
    is_custom: bool = False
    # Conflicts
    conflicts_with: List[str] = field(default_factory=list)
    # Metadata
    category: str = ""
    created: float = field(default_factory=time.time)

    @property
    def display_key(self) -> str:
        parts = [m.value for m in self.modifiers]
        parts.append(self.key)
        return "+".join(parts) if parts else "(none)"

    @property
    def scope_icon(self) -> str:
        return SCOPE_ICONS.get(self.scope, "❓")

    @property
    def display(self) -> str:
        app = f" [{self.app_name}]" if self.app_name else ""
        return f"{self.scope_icon} {self.name}{app}: {self.display_key}"

    @property
    def conflict_warning(self) -> str:
        if self.conflicts_with:
            return f"⚠️ Conflicts with: {', '.join(self.conflicts_with)}"
        return ""

    @property
    def status_icon(self) -> str:
        if not self.enabled:
            return "⚫"
        if self.conflicts_with:
            return "⚠️"
        if self.is_custom:
            return "✏️"
        return "✅"


@dataclass
class ShortcutProfile:
    """A saved shortcut profile."""
    name: str
    description: str = ""
    bindings: Dict[str, str] = field(default_factory=dict)  # shortcut_id -> display_key
    created: float = field(default_factory=time.time)
    is_active: bool = False
    profile_id: str = ""

    def __post_init__(self):
        if not self.profile_id:
            self.profile_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def binding_count(self) -> int:
        return len(self.bindings)


class ShortcutEditor:
    """Keyboard shortcut editor for Nyrqis OS."""

    def __init__(self):
        self._bindings: List[ShortcutBinding] = []
        self._profiles: List[ShortcutProfile] = []
        self._selected_index: int = 0
        self._view_mode: str = "shortcuts"  # shortcuts, conflicts, profiles, record
        self._filter_scope: Optional[ShortcutScope] = None
        self._recording: bool = False
        self._recording_id: str = ""

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        self._bindings = [
            # System
            ShortcutBinding("sys_terminal", "Open Terminal", "Launch terminal emulator",
                            ShortcutScope.SYSTEM, [ModifierKey.CTRL], "t", category="launch"),
            ShortcutBinding("sys_files", "Open Files", "Launch file manager",
                            ShortcutScope.SYSTEM, [ModifierKey.CTRL], "e", category="launch"),
            ShortcutBinding("sys_browser", "Open Browser", "Launch web browser",
                            ShortcutScope.SYSTEM, [ModifierKey.CTRL, ModifierKey.SHIFT], "b", category="launch"),
            ShortcutBinding("sys_settings", "Open Settings", "Launch system settings",
                            ShortcutScope.SYSTEM, [ModifierKey.SUPER], ",", category="launch"),
            ShortcutBinding("sys_lock", "Lock Screen", "Lock the screen",
                            ShortcutScope.SYSTEM, [ModifierKey.CTRL, ModifierKey.ALT], "l", category="session"),
            ShortcutBinding("sys_logout", "Log Out", "End current session",
                            ShortcutScope.SYSTEM, [ModifierKey.CTRL, ModifierKey.ALT], "Delete", category="session"),
            ShortcutBinding("sys_power", "Power Menu", "Show power options",
                            ShortcutScope.SYSTEM, [ModifierKey.SUPER], "x", category="session"),
            ShortcutBinding("sys_screenshot", "Screenshot", "Capture full screen",
                            ShortcutScope.SYSTEM, [ModifierKey.PRINT], "", category="capture"),
            ShortcutBinding("sys_region_screenshot", "Region Screenshot", "Capture screen region",
                            ShortcutScope.SYSTEM, [ModifierKey.CTRL, ModifierKey.PRINT], "", category="capture"),
            # Window management
            ShortcutBinding("win_maximize", "Maximize Window", "Toggle maximize",
                            ShortcutScope.WINDOW, [ModifierKey.SUPER], "Up", category="window"),
            ShortcutBinding("win_minimize", "Minimize Window", "Minimize current window",
                            ShortcutScope.WINDOW, [ModifierKey.SUPER], "Down", category="window"),
            ShortcutBinding("win_left", "Snap Left", "Snap window to left half",
                            ShortcutScope.WINDOW, [ModifierKey.SUPER], "Left", category="window"),
            ShortcutBinding("win_right", "Snap Right", "Snap window to right half",
                            ShortcutScope.WINDOW, [ModifierKey.SUPER], "Right", category="window"),
            ShortcutBinding("win_close", "Close Window", "Close current window",
                            ShortcutScope.WINDOW, [ModifierKey.ALT], "F4", category="window"),
            ShortcutBinding("win_switch", "Switch Window", "Switch between windows",
                            ShortcutScope.WINDOW, [ModifierKey.ALT], "Tab", category="window"),
            ShortcutBinding("win_cycle", "Cycle Windows", "Cycle through windows",
                            ShortcutScope.WINDOW, [ModifierKey.ALT], "`", category="window"),
            # Desktop
            ShortcutBinding("desk_overview", "Overview", "Show workspace overview",
                            ShortcutScope.DESKTOP, [ModifierKey.SUPER], "s", category="navigation"),
            ShortcutBinding("desk_switch_1", "Workspace 1", "Switch to workspace 1",
                            ShortcutScope.DESKTOP, [ModifierKey.CTRL, ModifierKey.ALT], "1", category="workspace"),
            ShortcutBinding("desk_switch_2", "Workspace 2", "Switch to workspace 2",
                            ShortcutScope.DESKTOP, [ModifierKey.CTRL, ModifierKey.ALT], "2", category="workspace"),
            ShortcutBinding("desk_switch_3", "Workspace 3", "Switch to workspace 3",
                            ShortcutScope.DESKTOP, [ModifierKey.CTRL, ModifierKey.ALT], "3", category="workspace"),
            ShortcutBinding("desk_move_ws1", "Move to Workspace 1", "Move window to workspace 1",
                            ShortcutScope.DESKTOP, [ModifierKey.CTRL, ModifierKey.SUPER], "1", category="workspace"),
            # App-specific
            ShortcutBinding("app_find", "Find", "Find in current app",
                            ShortcutScope.APPLICATION, [ModifierKey.CTRL], "f", app_name="Global", category="edit"),
            ShortcutBinding("app_replace", "Find & Replace", "Find and replace in current app",
                            ShortcutScope.APPLICATION, [ModifierKey.CTRL], "h", app_name="Global", category="edit"),
            ShortcutBinding("app_undo", "Undo", "Undo last action",
                            ShortcutScope.APPLICATION, [ModifierKey.CTRL], "z", app_name="Global", category="edit"),
            ShortcutBinding("app_redo", "Redo", "Redo last action",
                            ShortcutScope.APPLICATION, [ModifierKey.CTRL, ModifierKey.SHIFT], "z", app_name="Global", category="edit"),
            ShortcutBinding("app_copy", "Copy", "Copy selection to clipboard",
                            ShortcutScope.APPLICATION, [ModifierKey.CTRL], "c", app_name="Global", category="edit"),
            ShortcutBinding("app_paste", "Paste", "Paste from clipboard",
                            ShortcutScope.APPLICATION, [ModifierKey.CTRL], "v", app_name="Global", category="edit"),
            # Accessibility
            ShortcutBinding("a11y_zoom", "Zoom In", "Magnify the screen",
                            ShortcutScope.ACCESSIBILITY, [ModifierKey.SUPER], "=", category="magnifier"),
            ShortcutBinding("a11y_unzoom", "Zoom Out", "Reduce magnification",
                            ShortcutScope.ACCESSIBILITY, [ModifierKey.SUPER], "-", category="magnifier"),
            ShortcutBinding("a11y_high_contrast", "High Contrast", "Toggle high contrast mode",
                            ShortcutScope.ACCESSIBILITY, [ModifierKey.SUPER, ModifierKey.ALT], "h", category="display"),
        ]

        # Detect conflicts
        self._detect_conflicts()

        self._profiles = [
            ShortcutProfile("Default", "Nyrqis default shortcuts", is_active=True),
            ShortcutProfile("Classic", "Traditional desktop shortcuts (GNOME-like)", is_active=False),
            ShortcutProfile("Vim", "Vim-style modal editing shortcuts", is_active=False),
            ShortcutProfile("Minimal", "Essential shortcuts only", is_active=False),
        ]

    def _detect_conflicts(self) -> None:
        key_map: Dict[str, List[str]] = {}
        for b in self._bindings:
            if b.enabled:
                key = b.display_key
                key_map.setdefault(key, []).append(b.name)
        for b in self._bindings:
            b.conflicts_with = [n for n in key_map.get(b.display_key, []) if n != b.name]

    def set_shortcut(self, index: int, modifiers: List[ModifierKey], key: str) -> bool:
        if 0 <= index < len(self._bindings):
            binding = self._bindings[index]
            binding.modifiers = modifiers
            binding.key = key
            binding.is_custom = True
            binding.is_default = False
            self._detect_conflicts()
            return True
        return False

    def toggle_binding(self, index: int) -> bool:
        if 0 <= index < len(self._bindings):
            self._bindings[index].enabled = not self._bindings[index].enabled
            self._detect_conflicts()
            return True
        return False

    def reset_to_default(self, index: int) -> bool:
        if 0 <= index < len(self._bindings):
            binding = self._bindings[index]
            binding.modifiers = []
            binding.key = ""
            binding.is_custom = False
            binding.is_default = True
            binding.enabled = True
            self._detect_conflicts()
            return True
        return False

    def save_profile(self, name: str) -> ShortcutProfile:
        bindings = {b.shortcut_id: b.display_key for b in self._bindings}
        profile = ShortcutProfile(name=name, bindings=bindings)
        self._profiles.append(profile)
        return profile

    def get_conflicts(self) -> List[Tuple[ShortcutBinding, ShortcutBinding]]:
        pairs = []
        seen = set()
        for b in self._bindings:
            for conflict_name in b.conflicts_with:
                for other in self._bindings:
                    if other.name == conflict_name and other.shortcut_id != b.shortcut_id:
                        pair = tuple(sorted([b.shortcut_id, other.shortcut_id]))
                        if pair not in seen:
                            seen.add(pair)
                            pairs.append((b, other))
        return pairs

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
        bindings = list(self._bindings)
        if self._filter_scope:
            bindings = [b for b in bindings if b.scope == self._filter_scope]
        if self._view_mode == "profiles":
            return self._profiles
        if self._view_mode == "conflicts":
            conflicts = self.get_conflicts()
            return [b for pair in conflicts for b in pair]
        return bindings

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
    def total_conflicts(self) -> int:
        return len(self.get_conflicts())

    def render_shortcuts(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" ⌨️  Keyboard Shortcuts ({len(self._bindings)} bindings, {self.total_conflicts} conflicts)")
        lines.append("─" * width)

        current_scope = None
        for b in self._get_display_list():
            if b.scope != current_scope:
                current_scope = b.scope
                lines.append(f" {SCOPE_ICONS.get(current_scope, '❓')} {current_scope.value.title()}")

            marker = "▸" if self._bindings.index(b) == self._selected_index else " "
            lines.append(f"  {marker} {b.status_icon} {b.name}: {b.display_key}")
            if b.description:
                lines.append(f"    {b.description}")
            if b.conflict_warning:
                lines.append(f"    {b.conflict_warning}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Edit  Space:Toggle  R:Reset")
        lines.append(" C:Conflicts  P:Profiles  F:Filter scope")
        return lines

    def render_conflicts(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" ⚠️  Shortcut Conflicts ({self.total_conflicts})")
        lines.append("─" * width)

        conflicts = self.get_conflicts()
        if not conflicts:
            lines.append("  No conflicts! 🎉")
        else:
            for b1, b2 in conflicts:
                lines.append(f"  ⚠️  {b1.display_key}")
                lines.append(f"    → {b1.name} ({b1.scope.value})")
                lines.append(f"    → {b2.name} ({b2.scope.value})")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render_profiles(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 👤 Shortcut Profiles")
        lines.append("─" * width)
        for i, profile in enumerate(self._profiles):
            marker = "▸" if i == self._selected_index else " "
            active = " 🟢" if profile.is_active else ""
            lines.append(f"{marker} {profile.name}{active}")
            lines.append(f"   {profile.description} | {profile.binding_count} bindings")
            lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Apply  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {"conflicts": self.render_conflicts, "profiles": self.render_profiles}
        renderer = renderers.get(self._view_mode, self.render_shortcuts)
        return renderer(width)

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "conflicts":
            if key == "Escape":
                self.set_view("shortcuts")
                return "back"
            return None
        if self._view_mode == "profiles":
            if key == "Escape":
                self.set_view("shortcuts")
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
        if key == " ":
            return "toggle" if self.toggle_binding(self._selected_index) else "toggle_failed"
        if key == "r":
            return "reset" if self.reset_to_default(self._selected_index) else "reset_failed"
        if key == "c":
            self.set_view("conflicts")
            return "conflicts"
        if key == "p":
            self.set_view("profiles")
            return "profiles"
        return None
