"""
Nyrqis OS — Accessibility System
Screen reader, focus management, keyboard shortcuts, magnifier, and WCAG compliance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AnnouncementPriority(Enum):
    POLITE = "polite"
    ASSERTIVE = "assertive"


class FocusDirection(Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class ReadingMode(Enum):
    VISUAL = "visual"
    SCREEN_READER = "screen_reader"
    LARGE_TEXT = "large_text"


class ColorScheme(Enum):
    DEFAULT = "default"
    HIGH_CONTRAST = "high_contrast"
    YELLOW_BLACK = "yellow_black"
    DARK_BLUE = "dark_blue"


class CursorSize(Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    EXTRA_LARGE = "extra_large"


class ScreenReaderVoice(Enum):
    MALE = "male"
    FEMALE = "female"
    NEUTRAL = "neutral"


# ---------------------------------------------------------------------------
# Announcement
# ---------------------------------------------------------------------------

@dataclass
class Announcement:
    text: str
    priority: AnnouncementPriority = AnnouncementPriority.POLITE
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            import time
            self.timestamp = time.time()


# ---------------------------------------------------------------------------
# FocusableElement
# ---------------------------------------------------------------------------

@dataclass
class FocusableElement:
    id: str = ""
    role: str = "generic"
    label: str = ""
    tab_index: int = 0
    enabled: bool = True
    focusable: bool = True
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)

    def focus(self):
        self.focused = True

    def blur(self):
        self.focused = False


# ---------------------------------------------------------------------------
# FocusRing
# ---------------------------------------------------------------------------

@dataclass
class FocusRing:
    width: int = 2
    color: str = "#0066ff"
    offset: int = 2
    radius: int = 4


# ---------------------------------------------------------------------------
# FocusManager
# ---------------------------------------------------------------------------

class FocusManager:
    def __init__(self):
        self._elements: List[FocusableElement] = []
        self._focused_id: Optional[str] = None
        self._history: List[str] = []
        self._callbacks: List[Callable] = []
        self.ring = FocusRing()

    def register(self, element: FocusableElement):
        self._elements.append(element)

    def unregister(self, element_or_id) -> bool:
        if isinstance(element_or_id, str):
            before = len(self._elements)
            self._elements = [e for e in self._elements if e.id != element_or_id]
            if self._focused_id == element_or_id:
                self._focused_id = None
            return len(self._elements) < before
        else:
            before = len(self._elements)
            self._elements = [e for e in self._elements if e is not element_or_id]
            if self._focused_id == getattr(element_or_id, 'id', None):
                self._focused_id = None
            return len(self._elements) < before

    def focus(self, element_id: str) -> bool:
        for e in self._elements:
            if e.id == element_id and e.enabled and e.focusable:
                for old in self._elements:
                    old.blur()
                e.focus()
                self._focused_id = element_id
                self._history.append(element_id)
                self._emit("focus_changed", {"id": element_id})
                return True
        return False

    def focus_first(self):
        for e in sorted(self._elements, key=lambda x: x.tab_index):
            if e.enabled and e.focusable:
                self.focus(e.id)
                return

    def focus_last(self):
        focusable = [e for e in self._elements if e.enabled and e.focusable]
        if focusable:
            self.focus(focusable[-1].id)

    def focus_next(self):
        focusable = [e for e in self._elements if e.enabled and e.focusable]
        if not focusable:
            return
        current_idx = -1
        for i, e in enumerate(focusable):
            if e.id == self._focused_id:
                current_idx = i
                break
        next_idx = (current_idx + 1) % len(focusable)
        self.focus(focusable[next_idx].id)

    def focus_previous(self):
        focusable = [e for e in self._elements if e.enabled and e.focusable]
        if not focusable:
            return
        current_idx = -1
        for i, e in enumerate(focusable):
            if e.id == self._focused_id:
                current_idx = i
                break
        prev_idx = (current_idx - 1) % len(focusable) if current_idx > 0 else len(focusable) - 1
        self.focus(focusable[prev_idx].id)

    def focus_by_direction(self, direction: FocusDirection) -> bool:
        """Focus next element in given direction (simplified: just focus_next)."""
        self.focus_next()
        return self._focused_id is not None

    def get(self, element_id: str) -> Optional[FocusableElement]:
        for e in self._elements:
            if e.id == element_id:
                return e
        return None

    def clear(self):
        for e in self._elements:
            e.blur()
        self._focused_id = None

    @property
    def focused(self) -> Optional[FocusableElement]:
        return self.get(self._focused_id) if self._focused_id else None

    @property
    def focused_id(self) -> Optional[str]:
        return self._focused_id

    @property
    def element_count(self) -> int:
        return len(self._elements)

    @property
    def elements(self) -> List[FocusableElement]:
        return list(self._elements)

    @property
    def focusable_elements(self) -> List[FocusableElement]:
        return [e for e in self._elements if e.enabled and e.focusable]

    @property
    def history(self) -> List[str]:
        return list(self._history)

    def on_event(self, callback: Callable):
        self._callbacks.append(callback)

    def _emit(self, event_type: str, data: dict):
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# KeyboardBinding
# ---------------------------------------------------------------------------

@dataclass
class KeyboardBinding:
    id: str = ""
    keys: str = ""
    action: str = ""
    category: str = "general"
    enabled: bool = True


# ---------------------------------------------------------------------------
# KeyboardManager
# ---------------------------------------------------------------------------

class KeyboardManager:
    def __init__(self):
        self._shortcuts: Dict[str, KeyboardBinding] = {}
        self._callbacks: List[Callable] = []
        self._history: List[str] = []
        self.enabled: bool = True

    @classmethod
    def with_defaults(cls) -> "KeyboardManager":
        km = cls()
        # Register default shortcuts
        defaults = [
            ("Ctrl+T", "open_terminal", "apps", "Open Terminal"),
            ("Ctrl+E", "open_editor", "apps", "Open Editor"),
            ("Ctrl+F", "open_file_manager", "apps", "Open File Manager"),
            ("Ctrl+B", "open_browser", "apps", "Open Browser"),
            ("Ctrl+N", "open_notes", "apps", "Open Notes"),
            ("Ctrl+M", "open_music", "apps", "Open Music"),
            ("Ctrl+P", "screenshot", "system", "Screenshot"),
            ("Alt+Tab", "switch_window", "windows", "Switch Window"),
            ("Alt+F4", "close_window", "windows", "Close Window"),
            ("Super", "show_app_grid", "system", "Show App Grid"),
            ("Ctrl+L", "lock_screen", "system", "Lock Screen"),
            ("Ctrl+Q", "logout", "system", "Logout"),
            ("Ctrl+Shift+T", "open_terminal_admin", "apps", "Open Terminal as Admin"),
            ("F11", "toggle_fullscreen", "windows", "Toggle Fullscreen"),
            ("Ctrl+comma", "open_settings", "system", "Open Settings"),
        ]
        for keys, action, category, _label in defaults:
            km.register(keys, action, category)
        return km

    def register(self, keys: str, action: str, category: str = "general") -> KeyboardBinding:
        import uuid
        kb = KeyboardBinding(
            id=str(uuid.uuid4())[:8],
            keys=keys,
            action=action,
            category=category,
        )
        self._shortcuts[keys] = kb
        return kb

    def unregister(self, binding_id: str) -> bool:
        for keys, kb in list(self._shortcuts.items()):
            if kb.id == binding_id:
                del self._shortcuts[keys]
                return True
        return False

    def handle_key(self, keys: str) -> Optional[str]:
        if not self.enabled:
            return None
        if keys in self._shortcuts:
            kb = self._shortcuts[keys]
            self._history.append(keys)
            self._emit("shortcut_activated", {"keys": keys, "action": kb.action})
            return kb.action
        return None

    def get_shortcut(self, keys: str) -> Optional[KeyboardBinding]:
        return self._shortcuts.get(keys)

    def shortcuts_by_category(self, category: str) -> List[KeyboardBinding]:
        return [kb for kb in self._shortcuts.values() if kb.category == category]

    @property
    def shortcuts(self) -> List[KeyboardBinding]:
        return list(self._shortcuts.values())

    @property
    def history(self) -> List[str]:
        return list(self._history)

    def on_event(self, callback: Callable):
        self._callbacks.append(callback)

    def _emit(self, event_type: str, data: dict):
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# ScreenReader
# ---------------------------------------------------------------------------

class ScreenReader:
    def __init__(self):
        self.enabled: bool = True
        self.muted: bool = False
        self.rate: float = 1.0
        self._queue: List[Announcement] = []
        self._priority_queue: List[Announcement] = []
        self._history: List[Announcement] = []
        self._callbacks: List[Callable] = []

    @property
    def queue(self) -> List[Announcement]:
        return list(self._queue)

    @property
    def history(self) -> List[Announcement]:
        return list(self._history)

    def announce(self, text: str, priority: AnnouncementPriority = AnnouncementPriority.POLITE):
        if self.muted or not self.enabled:
            return
        ann = Announcement(text=text, priority=priority)
        if priority == AnnouncementPriority.ASSERTIVE:
            self._queue.clear()  # assertive clears normal queue
            self._queue.append(ann)  # then add urgent item
        else:
            self._queue.append(ann)
        self._history.append(ann)
        self._emit_announcement(ann)

    def say(self, text: str):
        """Shorthand for announce."""
        self.announce(text)

    def next(self) -> Optional[Announcement]:
        if self._priority_queue:
            return self._priority_queue.pop(0)
        if self._queue:
            return self._queue.pop(0)
        return None

    def consume_next(self) -> Optional[Announcement]:
        """Same as next() — consume and return the next announcement."""
        return self.next()

    def clear(self):
        self._queue.clear()
        self._priority_queue.clear()

    def assertive_clears_queue(self):
        self._priority_queue.clear()

    def read_element(self, element: FocusableElement):
        """Announce an element's label and role."""
        label = element.label or element.id or "unknown"
        role = element.role or "element"
        self.announce(f"{label}, {role}")

    def read_focus_change(self, old: Optional[FocusableElement], new: FocusableElement):
        """Announce a focus change."""
        label = new.label or new.id or "unknown"
        role = new.role or "element"
        if old:
            self.announce(f"Focus moved from {old.label or old.id} to {label}, {role}")
        else:
            self.announce(f"Focused on {label}, {role}")

    def on_event(self, callback: Callable):
        self._callbacks.append(callback)

    def _emit_announcement(self, ann: Announcement):
        for cb in self._callbacks:
            try:
                cb(ann)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# ScreenReaderConfig
# ---------------------------------------------------------------------------

@dataclass
class ScreenReaderConfig:
    voice: ScreenReaderVoice = ScreenReaderVoice.NEUTRAL
    rate: float = 1.0
    pitch: float = 1.0
    volume: float = 0.8
    verbose: bool = False

    @property
    def rate_display(self) -> str:
        speeds = {0.5: "Very Slow", 0.75: "Slow", 1.0: "Normal", 1.5: "Fast", 2.0: "Very Fast"}
        return speeds.get(self.rate, f"{self.rate:.1f}x")

    @property
    def rate_bar(self) -> str:
        filled = int((self.rate / 2.0) * 20)
        return "█" * filled + "░" * (20 - filled)


# ---------------------------------------------------------------------------
# Proxy sub-objects for backward-compatible access
# ---------------------------------------------------------------------------

class _ScreenReaderProxy:
    def __init__(self, parent):
        self._parent = parent
        self._enabled = False
        self.rate: float = 1.0
    @property
    def enabled(self) -> bool:
        return self._enabled
    @enabled.setter
    def enabled(self, v: bool):
        self._enabled = v
        self._parent.screen_reader_enabled = v
    @property
    def rate_bar(self) -> str:
        filled = int((self.rate / 2.0) * 20)
        return "█" * filled + "░" * (20 - filled)

class _MagnifierProxy:
    def __init__(self, parent):
        self._parent = parent
    @property
    def enabled(self) -> bool:
        return self._parent.magnifier_enabled
    @property
    def zoom_level(self) -> float:
        return self._parent._magnifier_zoom
    @zoom_level.setter
    def zoom_level(self, v: float):
        self._parent._magnifier_zoom = max(1.0, min(20.0, v))
    @property
    def zoom_bar(self) -> str:
        filled = int(((self._parent._magnifier_zoom - 1.0) / 19.0) * 24)
        return "█" * filled + "░" * (24 - filled)

class _HighContrastProxy:
    def __init__(self, parent):
        self._parent = parent
    @property
    def enabled(self) -> bool:
        return self._parent._high_contrast
    @property
    def text_scale(self) -> float:
        return self._parent._text_scale
    @text_scale.setter
    def text_scale(self, v: float):
        self._parent._text_scale = v
    @property
    def background(self) -> str:
        return "#ffff00" if self._parent._color_scheme == ColorScheme.YELLOW_BLACK else "#000000"

class _KeyboardNavProxy:
    def __init__(self, parent):
        self._parent = parent
    @property
    def enabled(self) -> bool:
        return self._parent._keyboard_nav
    @property
    def sticky_keys(self) -> bool:
        return self._parent._sticky_keys
    @property
    def slow_keys(self) -> bool:
        return self._parent._slow_keys
    @property
    def bounce_keys(self) -> bool:
        return self._parent._bounce_keys

class _CursorProxy:
    def __init__(self, parent):
        self._parent = parent
    @property
    def size(self):
        return self._parent._cursor_size
    @size.setter
    def size(self, v):
        self._parent._cursor_size = v
    @property
    def trails(self) -> bool:
        return self._parent._cursor_trails
    @property
    def size_pixels(self) -> int:
        sizes = {CursorSize.SMALL: 24, CursorSize.MEDIUM: 32, CursorSize.LARGE: 36, CursorSize.EXTRA_LARGE: 48}
        return sizes.get(self._parent._cursor_size, 32)


# ---------------------------------------------------------------------------
# AccessibilitySettings (base)
# ---------------------------------------------------------------------------

class AccessibilitySettings:
    def __init__(self):
        self.screen_reader_enabled: bool = False
        self.magnifier_enabled: bool = False
        self._magnifier_zoom: float = 1.0
        self._high_contrast: bool = False
        self._color_scheme: ColorScheme = ColorScheme.DEFAULT
        self._reduce_motion: bool = False
        self._large_text: bool = False
        self._keyboard_nav: bool = False
        self._sticky_keys: bool = False
        self._slow_keys: bool = False
        self._bounce_keys: bool = False
        self._cursor_size: CursorSize = CursorSize.MEDIUM
        self._cursor_trails: bool = False
        self._text_scale: float = 1.0
        # Sub-objects for backward-compatible access
        self._sr_obj = _ScreenReaderProxy(self)
        self._magnifier_obj = _MagnifierProxy(self)
        self._hc_obj = _HighContrastProxy(self)
        self._kb_obj = _KeyboardNavProxy(self)
        self._cursor_obj = _CursorProxy(self)
        self._shortcuts: List[KeyboardBinding] = []
        # Register some default shortcuts
        for keys, action, cat in [
            ("Ctrl+T", "open_terminal", "apps"),
            ("Ctrl+E", "open_editor", "apps"),
            ("Alt+Tab", "switch_window", "windows"),
            ("Ctrl+L", "lock_screen", "system"),
        ]:
            self._shortcuts.append(KeyboardBinding(id=keys, keys=keys, action=action, category=cat))

    @property
    def screen_reader(self):
        return self._sr_obj

    @property
    def magnifier(self):
        return self._magnifier_obj

    @property
    def high_contrast(self):
        return self._hc_obj

    @property
    def keyboard_nav(self):
        return self._kb_obj

    @property
    def cursor(self):
        return self._cursor_obj

    @property
    def shortcuts(self):
        return self._shortcuts

    @property
    def reduce_motion(self) -> bool:
        return self._reduce_motion

    @property
    def large_text(self) -> bool:
        return self._large_text

    @property
    def magnifier_zoom(self) -> float:
        return self._magnifier_zoom

    def toggle_screen_reader(self) -> bool:
        self.screen_reader_enabled = not self.screen_reader_enabled
        self._sr_obj._enabled = self.screen_reader_enabled
        return self.screen_reader_enabled

    def toggle_magnifier(self) -> bool:
        self.magnifier_enabled = not self.magnifier_enabled
        return self.magnifier_enabled

    def set_magnifier_zoom(self, level: float) -> bool:
        self._magnifier_zoom = max(1.0, min(5.0, level))
        return True

    def zoom_in(self) -> float:
        self._magnifier_zoom = min(5.0, self._magnifier_zoom + 0.25)
        return self._magnifier_zoom

    def zoom_out(self) -> float:
        self._magnifier_zoom = max(1.0, self._magnifier_zoom - 0.25)
        return self._magnifier_zoom

    def toggle_high_contrast(self) -> bool:
        self._high_contrast = not self._high_contrast
        return self._high_contrast

    def set_color_scheme(self, scheme: ColorScheme) -> bool:
        self._color_scheme = scheme
        if scheme == ColorScheme.HIGH_CONTRAST:
            self._high_contrast = True
        elif scheme == ColorScheme.YELLOW_BLACK:
            self._high_contrast = True
        return True

    def set_text_scale(self, scale: float) -> bool:
        self._text_scale = max(0.5, min(3.0, scale))
        return True

    def set_magnifier_zoom(self, level: float) -> bool:
        self._magnifier_zoom = max(1.0, min(20.0, level))
        return True

    def toggle_keyboard_nav(self) -> bool:
        self._keyboard_nav = not self._keyboard_nav
        return self._keyboard_nav

    def toggle_sticky_keys(self) -> bool:
        self._sticky_keys = not self._sticky_keys
        return self._sticky_keys

    def toggle_slow_keys(self) -> bool:
        self._slow_keys = not self._slow_keys
        return self._slow_keys

    def toggle_bounce_keys(self) -> bool:
        self._bounce_keys = not self._bounce_keys
        return self._bounce_keys

    def set_cursor_size(self, size: CursorSize) -> bool:
        self._cursor_size = size
        return True

    def toggle_cursor_trails(self) -> bool:
        self._cursor_trails = not self._cursor_trails
        return self._cursor_trails

    def get_active_features(self) -> List[str]:
        features = []
        if self.screen_reader_enabled:
            features.append("Screen Reader")
        if self.magnifier_enabled:
            features.append("Magnifier")
        if self._high_contrast:
            features.append("High Contrast")
        if self._reduce_motion:
            features.append("Reduce Motion")
        if self._large_text:
            features.append("Large Text")
        if self._keyboard_nav:
            features.append("Keyboard Navigation")
        return features

    def get_stats(self) -> Dict:
        return {
            "screen_reader": self.screen_reader_enabled,
            "magnifier": self.magnifier_enabled,
            "high_contrast": self._high_contrast,
            "reduce_motion": self._reduce_motion,
            "large_text": self._large_text,
            "keyboard_nav": self._keyboard_nav,
            "features_active": len(self.get_active_features()),
            "shortcuts": len(self._shortcuts),
        }

    def audit(self) -> List[str]:
        issues = []
        if not self.screen_reader_enabled:
            issues.append("Screen reader is disabled")
        if self._text_scale < 1.0:
            issues.append("Text scale below 1.0 may be hard to read")
        return issues

    def __repr__(self) -> str:
        return f"AccessibilitySettings(features={len(self.get_active_features())})"


# ---------------------------------------------------------------------------
# AccessibilitySystem (full — composes all sub-systems)
# ---------------------------------------------------------------------------

class AccessibilitySystem(AccessibilitySettings):
    """Top-level accessibility system: composes screen reader, focus manager,
    keyboard manager, and settings into one unified interface."""

    def __init__(self):
        super().__init__()
        self.screen_reader = ScreenReader()
        self.keyboard = KeyboardManager.with_defaults()
        self.focus = FocusManager()
        self._callbacks: List[Callable] = []
        self._reading_mode: ReadingMode = ReadingMode.VISUAL

    # -- Settings shortcuts that fire events --

    def set_high_contrast(self, value: bool):
        self._high_contrast = value
        if value:
            self.focus.ring = FocusRing(width=4)
        self._emit("mode_changed", {"high_contrast": value})

    def set_reduce_motion(self, value: bool):
        self._reduce_motion = value
        self._emit("mode_changed", {"reduce_motion": value})

    def set_large_text(self, value: bool):
        self._large_text = value
        if value:
            self._text_scale = max(self._text_scale, 1.25)
        self._emit("mode_changed", {"large_text": value})

    def set_magnifier_zoom(self, level: float):
        self._magnifier_zoom = max(1.0, min(5.0, level))
        self._emit("zoom_changed", {"zoom": self._magnifier_zoom})

    def text_scale(self) -> float:
        scale = self._text_scale
        if self._large_text and scale < 1.25:
            scale = 1.25
        return scale * self._magnifier_zoom

    def zoom_reset(self):
        self._magnifier_zoom = 1.0

    def set_screen_reader_mode(self, enabled: bool):
        self.screen_reader.enabled = enabled

    def set_reading_mode(self, mode: ReadingMode):
        self._reading_mode = mode

    @property
    def reading_mode(self) -> ReadingMode:
        return self._reading_mode

    # -- Focus --

    def register_focusable(self, element: FocusableElement):
        self.focus.register(element)

    def focus_element(self, element_id: str):
        self.focus.focus(element_id)

    @property
    def focused(self) -> Optional[FocusableElement]:
        return self.focus.focused

    # -- Announcements --

    def announce(self, text: str):
        self.screen_reader.announce(text)

    # -- Shortcuts --

    def register_shortcut(self, keys: str, action: str) -> KeyboardBinding:
        return self.keyboard.register(keys, action)

    def handle_shortcut(self, keys: str) -> Optional[str]:
        return self.keyboard.handle_key(keys)

    # -- Audit --

    def audit_focusable(self) -> List[Dict]:
        issues = []
        for elem in self.focus.elements:
            if not elem.label:
                issues.append({
                    "element_id": elem.id,
                    "issue": "Missing accessible label",
                    "role": elem.role,
                })
        return issues

    # -- Summary --

    def summary(self) -> Dict:
        return {
            "high_contrast": self._high_contrast,
            "reduce_motion": self._reduce_motion,
            "large_text": self._large_text,
            "magnifier_zoom": self._magnifier_zoom,
            "text_scale": self.text_scale(),
            "screen_reader_enabled": self.screen_reader.enabled,
            "shortcut_count": len(self.keyboard.shortcuts),
            "focusable_count": self.focus.element_count,
            "active_features": self.get_active_features(),
        }

    # -- Events --

    def on_event(self, callback: Callable):
        self._callbacks.append(callback)

    def _emit(self, event_type: str, data: dict):
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (
            f"AccessibilitySystem(high_contrast={self._high_contrast}, "
            f"zoom={self._magnifier_zoom}, features={len(self.get_active_features())})"
        )
