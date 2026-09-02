#!/usr/bin/env python3
"""accessibility — Nyrqis desktop accessibility system.

Full screen reader and keyboard navigation support:

- Screen reader announcements (polite/assertive)
- Focus tracking with tab order
- Keyboard shortcut manager with configurable bindings
- High contrast mode
- Reduced motion mode
- Magnifier/zoom support
- Readable text scaling
- Focus indicators with configurable ring
- ARIA-like live regions for dynamic content
- Keyboard-only navigation (Tab, Shift+Tab, Arrow keys, Enter, Escape)
- Screen reader voice profile (speech rate, pitch, volume)
- Accessibility audit tool

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FocusDirection(Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    NEXT = "next"
    PREVIOUS = "previous"
    FIRST = "first"
    LAST = "last"


class AnnouncementPriority(Enum):
    POLITE = "polite"
    ASSERTIVE = "assertive"
    OFF = "off"


class ReadingMode(Enum):
    NORMAL = "normal"
    SCREEN_READER = "screen_reader"
    HIGH_CONTRAST = "high_contrast"
    LARGE_TEXT = "large_text"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class FocusableElement:
    """A focusable element in the UI tree."""
    id: str
    role: str               # button, textbox, menu, window, etc.
    label: str = ""         # accessible name
    description: str = ""   # accessible description
    shortcut: str = ""      # keyboard shortcut hint
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h
    enabled: bool = True
    visible: bool = True
    tab_index: int = 0       # 0 = normal, -1 = programmatic, N = explicit order
    group_id: str = ""       # grouping for arrow-key navigation
    parent_id: str = ""      # parent in tree
    state: Dict[str, Any] = field(default_factory=dict)  # role-specific state

    @property
    def is_focusable(self) -> bool:
        return self.enabled and self.visible and self.tab_index >= 0

    @property
    def center(self) -> Tuple[int, int]:
        x, y, w, h = self.rect
        return (x + w // 2, y + h // 2)


@dataclass
class Announcement:
    """A screen reader announcement."""
    text: str
    priority: AnnouncementPriority = AnnouncementPriority.POLITE
    timestamp: float = 0.0
    source_id: str = ""

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class KeyboardShortcut:
    """A registered keyboard shortcut."""
    id: str
    key: str           # e.g. "Ctrl+Shift+T"
    action: str        # Action ID to execute
    description: str = ""
    category: str = "general"
    enabled: bool = True


@dataclass
class VoiceProfile:
    """Screen reader voice settings."""
    rate: float = 1.0       # 0.5 - 2.0
    pitch: float = 1.0      # 0.5 - 2.0
    volume: float = 1.0     # 0.0 - 1.0
    voice_name: str = "default"


@dataclass
class FocusRing:
    """Focus indicator configuration."""
    width: int = 3
    color: str = "#5090ff"
    offset: int = 2
    style: str = "solid"     # solid, dashed, dotted
    animate: bool = True


# ---------------------------------------------------------------------------
# Screen reader
# ---------------------------------------------------------------------------

class ScreenReader:
    """Screen reader that announces UI events.

    Parameters
    ----------
    voice : VoiceProfile
        Voice settings.
    """

    def __init__(self, voice: Optional[VoiceProfile] = None) -> None:
        self._voice = voice or VoiceProfile()
        self._queue: List[Announcement] = []
        self._history: List[Announcement] = []
        self._max_history = 50
        self._enabled = True
        self._muted = False
        self._callbacks: List[Callable] = []

    def announce(
        self,
        text: str,
        priority: AnnouncementPriority = AnnouncementPriority.POLITE,
        source_id: str = "",
    ) -> None:
        """Make a screen reader announcement."""
        if not self._enabled or self._muted:
            return
        if priority == AnnouncementPriority.OFF:
            return

        ann = Announcement(text=text, priority=priority, source_id=source_id)

        if priority == AnnouncementPriority.ASSERTIVE:
            # Assertive clears the queue
            self._queue.clear()
            self._queue.append(ann)
        else:
            self._queue.append(ann)

        self._history.append(ann)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        self._dispatch(ann)

    def say(self, text: str) -> None:
        """Shorthand for polite announcement."""
        self.announce(text, AnnouncementPriority.POLITE)

    def interrupt(self, text: str) -> None:
        """Shorthand for assertive announcement."""
        self.announce(text, AnnouncementPriority.ASSERTIVE)

    def read_element(self, element: FocusableElement) -> None:
        """Read out a focusable element."""
        parts = []
        if element.label:
            parts.append(element.label)
        parts.append(element.role)
        if element.description:
            parts.append(element.description)
        if element.shortcut:
            parts.append(f"Shortcut: {element.shortcut}")
        if not element.enabled:
            parts.append("disabled")
        self.say(", ".join(parts))

    def read_focus_change(self, old: Optional[FocusableElement],
                          new: Optional[FocusableElement]) -> None:
        """Announce a focus change."""
        if new is None:
            self.say("Focus lost")
            return
        if old is None:
            self.read_element(new)
            return
        # Determine navigation direction
        ox, oy = old.center
        nx, ny = new.center
        direction = ""
        if abs(nx - ox) > abs(ny - oy):
            direction = "right" if nx > ox else "left"
        else:
            direction = "down" if ny > oy else "up"
        self.say(f"Moved {direction}")
        self.read_element(new)

    def read_page(self, title: str, element_count: int) -> None:
        """Announce page load."""
        self.say(f"{title} loaded, {element_count} items")

    def read_list_position(self, position: int, total: int, label: str) -> None:
        """Announce list position."""
        self.say(f"{label}, item {position} of {total}")

    def clear_queue(self) -> None:
        self._queue.clear()

    @property
    def queue(self) -> List[Announcement]:
        return list(self._queue)

    @property
    def next_announcement(self) -> Optional[Announcement]:
        return self._queue[0] if self._queue else None

    def consume_next(self) -> Optional[Announcement]:
        """Consume the next announcement from the queue."""
        if self._queue:
            return self._queue.pop(0)
        return None

    @property
    def history(self) -> List[Announcement]:
        return list(self._history)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self._muted = value

    @property
    def voice(self) -> VoiceProfile:
        return self._voice

    @voice.setter
    def voice(self, profile: VoiceProfile) -> None:
        self._voice = profile

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, ann: Announcement) -> None:
        for cb in self._callbacks:
            try:
                cb(ann)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Focus manager
# ---------------------------------------------------------------------------

class FocusManager:
    """Manages keyboard focus across the UI.

    Tracks focusable elements, handles Tab/arrow navigation,
    and maintains a focus ring.
    """

    def __init__(self) -> None:
        self._elements: Dict[str, FocusableElement] = {}
        self._focus_order: List[str] = []
        self._focused_id: Optional[str] = None
        self._ring = FocusRing()
        self._screen_reader: Optional[ScreenReader] = None
        self._callbacks: List[Callable] = []
        self._history: List[str] = []

    @property
    def screen_reader(self) -> Optional[ScreenReader]:
        return self._screen_reader

    @screen_reader.setter
    def screen_reader(self, sr: ScreenReader) -> None:
        self._screen_reader = sr

    # -- Element management -------------------------------------------

    def register(self, element: FocusableElement) -> None:
        """Register a focusable element."""
        self._elements[element.id] = element
        if element.tab_index >= 0:
            self._update_order()

    def unregister(self, element_id: str) -> bool:
        """Remove an element."""
        if element_id in self._elements:
            del self._elements[element_id]
            if element_id in self._focus_order:
                self._focus_order.remove(element_id)
            if self._focused_id == element_id:
                self._focused_id = None
            return True
        return False

    def get(self, element_id: str) -> Optional[FocusableElement]:
        return self._elements.get(element_id)

    @property
    def elements(self) -> List[FocusableElement]:
        return list(self._elements.values())

    @property
    def focusable_elements(self) -> List[FocusableElement]:
        return [self._elements[eid] for eid in self._focus_order
                if eid in self._elements and self._elements[eid].is_focusable]

    # -- Focus operations ---------------------------------------------

    def focus(self, element_id: str) -> bool:
        """Set focus to a specific element."""
        elem = self._elements.get(element_id)
        if elem is None or not elem.is_focusable:
            return False

        old = self._elements.get(self._focused_id) if self._focused_id else None
        self._focused_id = element_id
        self._history.append(element_id)

        self._dispatch("focus_changed", {"from": old, "to": elem})

        if self._screen_reader:
            self._screen_reader.read_focus_change(old, elem)

        return True

    def focus_first(self) -> bool:
        """Focus the first focusable element."""
        order = self.focusable_elements
        if order:
            return self.focus(order[0].id)
        return False

    def focus_last(self) -> bool:
        """Focus the last focusable element."""
        order = self.focusable_elements
        if order:
            return self.focus(order[-1].id)
        return False

    def focus_next(self) -> bool:
        """Move focus to the next element."""
        return self._move_focus(1)

    def focus_previous(self) -> bool:
        """Move focus to the previous element."""
        return self._move_focus(-1)

    def focus_by_direction(self, direction: FocusDirection) -> bool:
        """Move focus in a direction."""
        current = self._elements.get(self._focused_id)
        if current is None:
            return self.focus_first()

        if direction == FocusDirection.NEXT:
            return self.focus_next()
        elif direction == FocusDirection.PREVIOUS:
            return self.focus_previous()
        elif direction == FocusDirection.FIRST:
            return self.focus_first()
        elif direction == FocusDirection.LAST:
            return self.focus_last()

        # Spatial navigation
        cx, cy = current.center
        candidates = []
        for elem in self.focusable_elements:
            if elem.id == current.id:
                continue
            ex, ey = elem.center

            if direction == FocusDirection.UP and ey < cy:
                dist = (cy - ey) + abs(ex - cx) * 0.5
                candidates.append((dist, elem))
            elif direction == FocusDirection.DOWN and ey > cy:
                dist = (ey - cy) + abs(ex - cx) * 0.5
                candidates.append((dist, elem))
            elif direction == FocusDirection.LEFT and ex < cx:
                dist = (cx - ex) + abs(ey - cy) * 0.5
                candidates.append((dist, elem))
            elif direction == FocusDirection.RIGHT and ex > cx:
                dist = (ex - cx) + abs(ey - cy) * 0.5
                candidates.append((dist, elem))

        if candidates:
            candidates.sort(key=lambda x: x[0])
            return self.focus(candidates[0][1].id)
        return False

    def _move_focus(self, delta: int) -> bool:
        """Move focus by delta in the tab order."""
        order = self.focusable_elements
        if not order:
            return False

        if self._focused_id is None:
            return self.focus(order[0].id)

        current_idx = -1
        for i, elem in enumerate(order):
            if elem.id == self._focused_id:
                current_idx = i
                break

        if current_idx == -1:
            return self.focus(order[0].id)

        new_idx = (current_idx + delta) % len(order)
        return self.focus(order[new_idx].id)

    def _update_order(self) -> None:
        """Rebuild the tab order."""
        self._focus_order = sorted(
            self._elements.keys(),
            key=lambda eid: (
                self._elements[eid].tab_index,
                self._elements[eid].id,
            ),
        )

    @property
    def focused(self) -> Optional[FocusableElement]:
        if self._focused_id:
            return self._elements.get(self._focused_id)
        return None

    @property
    def focused_id(self) -> Optional[str]:
        return self._focused_id

    @property
    def ring(self) -> FocusRing:
        return self._ring

    @ring.setter
    def ring(self, value: FocusRing) -> None:
        self._ring = value

    @property
    def history(self) -> List[str]:
        return list(self._history)

    def clear(self) -> None:
        """Clear focus."""
        self._focused_id = None

    # -- Callbacks ----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, data: Optional[Dict] = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Keyboard shortcut manager
# ---------------------------------------------------------------------------

class KeyboardManager:
    """Manages keyboard shortcuts and key bindings."""

    def __init__(self) -> None:
        self._shortcuts: Dict[str, KeyboardShortcut] = {}
        self._enabled = True
        self._callbacks: List[Callable] = []
        self._pressed_keys: Set[str] = set()
        self._history: List[Tuple[str, float]] = []

    def register(
        self,
        key: str,
        action: str,
        description: str = "",
        category: str = "general",
        shortcut_id: Optional[str] = None,
    ) -> KeyboardShortcut:
        """Register a keyboard shortcut."""
        sid = shortcut_id or f"kb-{len(self._shortcuts)}"
        kb = KeyboardShortcut(
            id=sid, key=key, action=action,
            description=description, category=category,
        )
        self._shortcuts[sid] = kb
        return kb

    def unregister(self, shortcut_id: str) -> bool:
        if shortcut_id in self._shortcuts:
            del self._shortcuts[shortcut_id]
            return True
        return False

    def get_shortcut(self, key_combo: str) -> Optional[KeyboardShortcut]:
        """Find a shortcut by key combo."""
        for kb in self._shortcuts.values():
            if kb.key == key_combo and kb.enabled:
                return kb
        return None

    @property
    def shortcuts(self) -> List[KeyboardShortcut]:
        return list(self._shortcuts.values())

    def shortcuts_by_category(self, category: str) -> List[KeyboardShortcut]:
        return [s for s in self._shortcuts.values() if s.category == category]

    def handle_key(self, key_combo: str) -> Optional[str]:
        """Handle a key combination.

        Returns the action string if a shortcut matched, None otherwise.
        """
        if not self._enabled:
            return None

        kb = self.get_shortcut(key_combo)
        if kb:
            self._history.append((kb.action, time.time()))
            self._dispatch("shortcut_activated", kb)
            return kb.action
        return None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def history(self) -> List[Tuple[str, float]]:
        return list(self._history)

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, data: Any) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass

    # -- Default shortcuts --------------------------------------------

    @classmethod
    def with_defaults(cls) -> "KeyboardManager":
        """Create a KeyboardManager with default Nyrqis shortcuts."""
        km = cls()
        defaults = [
            ("Ctrl+T", "open_terminal", "Open terminal", "apps"),
            ("Ctrl+E", "open_files", "Open file manager", "apps"),
            ("Ctrl+comma", "open_settings", "Open settings", "apps"),
            ("Ctrl+L", "lock_screen", "Lock screen", "system"),
            ("Ctrl+Q", "quit_app", "Quit current app", "window"),
            ("Alt+F4", "close_window", "Close window", "window"),
            ("Ctrl+W", "close_tab", "Close tab", "window"),
            ("Ctrl+N", "new_window", "New window", "window"),
            ("Ctrl+Shift+N", "minimize", "Minimize window", "window"),
            ("Alt+Tab", "switch_window", "Switch window", "window"),
            ("Ctrl+1", "workspace_1", "Workspace 1", "workspace"),
            ("Ctrl+2", "workspace_2", "Workspace 2", "workspace"),
            ("Ctrl+3", "workspace_3", "Workspace 3", "workspace"),
            ("Ctrl+4", "workspace_4", "Workspace 4", "workspace"),
            ("Ctrl+Space", "toggle_tiling", "Toggle tiling", "layout"),
            ("Super", "toggle_launcher", "Toggle launcher", "apps"),
            ("Ctrl+Shift+V", "open_clipboard", "Open clipboard", "apps"),
            ("Ctrl+Shift+C", "open_spotlight", "Spotlight search", "apps"),
            ("F11", "toggle_fullscreen", "Toggle fullscreen", "window"),
            ("Ctrl+Shift+M", "toggle_monitor", "System monitor", "system"),
            ("Tab", "focus_next", "Next element", "navigation"),
            ("Shift+Tab", "focus_previous", "Previous element", "navigation"),
            ("Ctrl+Home", "focus_first", "First element", "navigation"),
            ("Ctrl+End", "focus_last", "Last element", "navigation"),
            ("Escape", "dismiss", "Dismiss overlay", "navigation"),
            ("Ctrl+plus", "zoom_in", "Zoom in", "accessibility"),
            ("Ctrl+minus", "zoom_out", "Zoom out", "accessibility"),
            ("Ctrl+0", "zoom_reset", "Reset zoom", "accessibility"),
            ("Ctrl+Shift+A", "toggle_a11y", "Toggle accessibility", "accessibility"),
            ("Ctrl+Shift+H", "high_contrast", "High contrast mode", "accessibility"),
            ("Ctrl+Shift+R", "reduce_motion", "Reduce motion", "accessibility"),
        ]
        for key, action, desc, cat in defaults:
            km.register(key, action, desc, cat)
        return km


# ---------------------------------------------------------------------------
# Accessibility system
# ---------------------------------------------------------------------------

class AccessibilitySystem:
    """Full accessibility system for the Nyrqis desktop.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session.
    """

    def __init__(self, session=None) -> None:
        self._session = session

        # Sub-systems
        self._screen_reader = ScreenReader()
        self._focus = FocusManager()
        self._focus.screen_reader = self._screen_reader
        self._keyboard = KeyboardManager.with_defaults()

        # Modes
        self._high_contrast = False
        self._reduce_motion = False
        self._large_text = False
        self._screen_reader_mode = False
        self._magnifier_zoom = 1.0
        self._reading_mode = ReadingMode.NORMAL

        # Focus ring colors for high contrast
        self._hc_ring = FocusRing(width=4, color="#ffffff", offset=3, style="solid")
        self._normal_ring = FocusRing()

        self._callbacks: List[Callable] = []

    # -- Screen reader -------------------------------------------------

    @property
    def screen_reader(self) -> ScreenReader:
        return self._screen_reader

    def announce(self, text: str, assertive: bool = False) -> None:
        """Make a screen reader announcement."""
        prio = (AnnouncementPriority.ASSERTIVE if assertive
                else AnnouncementPriority.POLITE)
        self._screen_reader.announce(text, prio)

    # -- Focus ---------------------------------------------------------

    @property
    def focus(self) -> FocusManager:
        return self._focus

    def register_focusable(self, element: FocusableElement) -> None:
        self._focus.register(element)

    def focus_element(self, element_id: str) -> bool:
        return self._focus.focus(element_id)

    def focus_next(self) -> bool:
        return self._focus.focus_next()

    def focus_previous(self) -> bool:
        return self._focus.focus_previous()

    @property
    def focused(self) -> Optional[FocusableElement]:
        return self._focus.focused

    # -- Keyboard shortcuts --------------------------------------------

    @property
    def keyboard(self) -> KeyboardManager:
        return self._keyboard

    def register_shortcut(self, key: str, action: str, **kwargs) -> KeyboardShortcut:
        return self._keyboard.register(key, action, **kwargs)

    def handle_shortcut(self, key_combo: str) -> Optional[str]:
        return self._keyboard.handle_key(key_combo)

    # -- Accessibility modes -------------------------------------------

    @property
    def high_contrast(self) -> bool:
        return self._high_contrast

    def set_high_contrast(self, enabled: bool) -> None:
        """Toggle high contrast mode."""
        self._high_contrast = enabled
        if enabled:
            self._focus.ring = self._hc_ring
        else:
            self._focus.ring = self._normal_ring
        self._dispatch("mode_changed", {"high_contrast": enabled})

    @property
    def reduce_motion(self) -> bool:
        return self._reduce_motion

    def set_reduce_motion(self, enabled: bool) -> None:
        """Toggle reduced motion mode."""
        self._reduce_motion = enabled
        self._dispatch("mode_changed", {"reduce_motion": enabled})

    @property
    def large_text(self) -> bool:
        return self._large_text

    def set_large_text(self, enabled: bool) -> None:
        """Toggle large text mode."""
        self._large_text = enabled
        self._dispatch("mode_changed", {"large_text": enabled})

    @property
    def screen_reader_mode(self) -> bool:
        return self._screen_reader_mode

    def set_screen_reader_mode(self, enabled: bool) -> None:
        """Toggle screen reader mode."""
        self._screen_reader_mode = enabled
        self._screen_reader.enabled = enabled
        self._dispatch("mode_changed", {"screen_reader": enabled})

    @property
    def magnifier_zoom(self) -> float:
        return self._magnifier_zoom

    def set_magnifier_zoom(self, zoom: float) -> None:
        """Set magnifier zoom level (1.0 = normal)."""
        self._magnifier_zoom = max(1.0, min(5.0, zoom))
        self._dispatch("mode_changed", {"magnifier_zoom": self._magnifier_zoom})

    def zoom_in(self) -> float:
        """Increase magnifier zoom."""
        self.set_magnifier_zoom(self._magnifier_zoom + 0.25)
        return self._magnifier_zoom

    def zoom_out(self) -> float:
        """Decrease magnifier zoom."""
        self.set_magnifier_zoom(self._magnifier_zoom - 0.25)
        return self._magnifier_zoom

    def zoom_reset(self) -> None:
        self.set_magnifier_zoom(1.0)

    @property
    def reading_mode(self) -> ReadingMode:
        return self._reading_mode

    def set_reading_mode(self, mode: ReadingMode) -> None:
        self._reading_mode = mode
        self._dispatch("mode_changed", {"reading_mode": mode.value})

    def text_scale(self) -> float:
        """Get text scale factor based on current modes."""
        scale = 1.0
        if self._large_text:
            scale *= 1.25
        if self._magnifier_zoom > 1.0:
            scale *= self._magnifier_zoom
        return scale

    # -- Audit ---------------------------------------------------------

    def audit_focusable(self) -> List[Dict[str, Any]]:
        """Audit all registered focusable elements for issues."""
        issues = []
        for elem in self._focus.elements:
            if elem.is_focusable and not elem.label:
                issues.append({
                    "severity": "warning",
                    "element_id": elem.id,
                    "message": f"Focusable {elem.role} '{elem.id}' has no accessible label",
                })
            if elem.tab_index < -1:
                issues.append({
                    "severity": "error",
                    "element_id": elem.id,
                    "message": f"Invalid tab_index {elem.tab_index} on '{elem.id}'",
                })
        return issues

    # -- State summary -------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Get accessibility system status."""
        return {
            "screen_reader_enabled": self._screen_reader.enabled,
            "screen_reader_muted": self._screen_reader.muted,
            "high_contrast": self._high_contrast,
            "reduce_motion": self._reduce_motion,
            "large_text": self._large_text,
            "screen_reader_mode": self._screen_reader_mode,
            "magnifier_zoom": self._magnifier_zoom,
            "reading_mode": self._reading_mode.value,
            "text_scale": self.text_scale(),
            "focused": self._focus.focused_id,
            "focusable_count": len(self._focus.focusable_elements),
            "shortcut_count": len(self._keyboard.shortcuts),
            "announcement_queue": len(self._screen_reader.queue),
        }

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, data: Optional[Dict] = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (
            f"AccessibilitySystem(sr={self._screen_reader.enabled}, "
            f"hc={self._high_contrast}, "
            f"zoom={self._magnifier_zoom})"
        )


__all__ = [
    "AccessibilitySystem", "ScreenReader", "FocusManager", "KeyboardManager",
    "FocusableElement", "Announcement", "KeyboardShortcut",
    "FocusRing", "VoiceProfile", "FocusDirection",
    "AnnouncementPriority", "ReadingMode",
]
