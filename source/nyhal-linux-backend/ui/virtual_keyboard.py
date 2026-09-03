"""
Nyrqis Virtual Keyboard — on-screen keyboard for touch and accessibility.

Features:
- QWERTY, AZERTY, Dvorak, Colemak layouts
- Number row toggle
- Symbol/emoji panel
- Predictive text (basic)
- Auto-repeat on hold
- Key press sound feedback (simulated)
- Sticky keys for accessibility
- High contrast mode
- Key magnifier for low vision
- Adjustable size and position
- Keyboard shortcuts display
"""

import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class KeyboardLayout(Enum):
    QWERTY = "QWERTY"
    AZERTY = "AZERTY"
    DVORAK = "Dvorak"
    COLEMAK = "Colemak"
    NUMERIC = "Numeric"


class KeyboardMode(Enum):
    LETTERS = "letters"
    NUMBERS = "numbers"
    SYMBOLS = "symbols"
    EMOJI = "emoji"


@dataclass
class Key:
    """A single keyboard key."""
    label: str
    width: int = 1  # Width units (1 = standard)
    code: str = ""
    shift_label: str = ""
    is_modifier: bool = False
    is_function: bool = False
    emoji: str = ""

    @property
    def display(self) -> str:
        return self.label

    @property
    def physical_width(self) -> int:
        """Width in character units."""
        return self.width


@dataclass
class KeyPress:
    """A recorded key press."""
    key: str
    timestamp: float = field(default_factory=time.time)
    duration: float = 0.0
    is_shifted: bool = False


# ─── Layout Definitions ──────────────────────────────────────────────────


LAYOUTS = {
    KeyboardLayout.QWERTY: {
        "letters": [
            [Key("Q"), Key("W"), Key("E"), Key("R"), Key("T"), Key("Y"), Key("U"), Key("I"), Key("O"), Key("P")],
            [Key("A"), Key("S"), Key("D"), Key("F"), Key("G"), Key("H"), Key("J"), Key("K"), Key("L")],
            [Key("⇧", 1.5, is_modifier=True), Key("Z"), Key("X"), Key("C"), Key("V"), Key("B"), Key("N"), Key("M"), Key("⌫", 1.5, is_function=True)],
            [Key("123", 1, is_function=True), Key(",", 1), Key(" ", 5), Key(".", 1), Key("⏎", 2, is_function=True)],
        ],
        "numbers": [
            [Key("1"), Key("2"), Key("3"), Key("4"), Key("5"), Key("6"), Key("7"), Key("8"), Key("9"), Key("0")],
            [Key("-"), Key("/"), Key(":"), Key(";"), Key("("), Key(")"), Key("$"), Key("&"), Key("@"), Key('"')],
            [Key("#+=", 1.5, is_function=True), Key("."), Key(","), Key("?"), Key("!"), Key("'"), Key(":"), Key("="), Key("⌫", 1.5, is_function=True)],
            [Key("ABC", 1, is_function=True), Key(" ", 5), Key("⏎", 2, is_function=True)],
        ],
        "symbols": [
            [Key("["), Key("]"), Key("{"), Key("}"), Key("#"), Key("%"), Key("^"), Key("*"), Key("+"), Key("=")],
            [Key("_"), Key("\\"), Key("|"), Key("~"), Key("<"), Key(">"), Key("€"), Key("£"), Key("¥"), Key("•")],
            [Key("ABC", 1.5, is_function=True), Key("."), Key(","), Key("?"), Key("!"), Key("'"), Key(":"), Key('"'), Key("⌫", 1.5, is_function=True)],
            [Key("123", 1, is_function=True), Key(" ", 5), Key("⏎", 2, is_function=True)],
        ],
        "emoji": [
            [Key("😀", emoji="😀"), Key("😂", emoji="😂"), Key("😍", emoji="😍"), Key("🤔", emoji="🤔"), Key("😎", emoji="😎"), Key("👍", emoji="👍"), Key("❤️", emoji="❤️"), Key("🔥", emoji="🔥"), Key("✨", emoji="✨"), Key("🎉", emoji="🎉")],
            [Key("🌟", emoji="🌟"), Key("💪", emoji="💪"), Key("🙏", emoji="🙏"), Key("👋", emoji="👋"), Key("🎨", emoji="🎨"), Key("📷", emoji="📷"), Key("🎵", emoji="🎵"), Key("📱", emoji="📱"), Key("💻", emoji="💻"), Key("🎮", emoji="🎮")],
            [Key("ABC", 1.5, is_function=True), Key("🍄", emoji="🍄"), Key("🌍", emoji="🌍"), Key("⚡", emoji="⚡"), Key("💡", emoji="💡"), Key("🚀", emoji="🚀"), Key("🎯", emoji="🎯"), Key("📊", emoji="📊"), Key("⌫", 1.5, is_function=True)],
            [Key("123", 1, is_function=True), Key(" ", 5), Key("⏎", 2, is_function=True)],
        ],
    },
    KeyboardLayout.DVORAK: {
        "letters": [
            [Key(","), Key("."), Key("/"), Key("P"), Key("Y"), Key("F"), Key("G"), Key("C"), Key("R"), Key("L")],
            [Key("A"), Key("O"), Key("E"), Key("U"), Key("I"), Key("D"), Key("H"), Key("T"), Key("N"), Key("S")],
            [Key("⇧", 1.5, is_modifier=True), Key(";"), Key("Q"), Key("J"), Key("K"), Key("X"), Key("B"), Key("M"), Key("⌫", 1.5, is_function=True)],
            [Key("123", 1, is_function=True), Key("'", 1), Key(" ", 5), Key("W", 1), Key("⏎", 2, is_function=True)],
        ],
    },
}


# ─── Virtual Keyboard ────────────────────────────────────────────────────


class VirtualKeyboard:
    """
    Virtual keyboard for Nyrqis OS.

    Provides on-screen keyboard input with multiple layouts.
    """

    def __init__(self, width: int = 800, height: int = 300):
        self._width = width
        self._height = height

        # Layout
        self._layout: KeyboardLayout = KeyboardLayout.QWERTY
        self._mode: KeyboardMode = KeyboardMode.LETTERS

        # State
        self._shift_active: bool = False
        self._caps_lock: bool = False
        self._ctrl_active: bool = False
        self._alt_active: bool = False
        self._input_text: str = ""
        self._cursor_pos: int = 0

        # Accessibility
        self._sticky_keys: bool = False
        self._high_contrast: bool = False
        self._key_magnifier: bool = False
        self._repeat_delay: float = 0.5
        self._repeat_rate: float = 0.05

        # History
        self._key_history: List[KeyPress] = []
        self._max_history: int = 100

        # Predictive text (simple)
        self._word_list = [
            "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
            "her", "was", "one", "our", "out", "day", "get", "has", "him", "his",
            "how", "its", "may", "new", "now", "old", "see", "way", "who", "did",
            "nyrqis", "terminal", "compositor", "wayland", "python", "rust",
        ]
        self._predictions: List[str] = []
        self._selected_prediction: int = 0

        # Recent emojis
        self._recent_emojis: List[str] = ["😀", "👍", "❤️", "🔥", "✨", "🎉"]

        # Callbacks
        self._on_key_press: List[Callable] = []
        self._on_text_change: List[Callable] = []

    # ── Layout Management ─────────────────────────────────────────────

    def set_layout(self, layout: KeyboardLayout) -> None:
        self._layout = layout
        self._mode = KeyboardMode.LETTERS

    def cycle_layout(self) -> KeyboardLayout:
        layouts = [KeyboardLayout.QWERTY, KeyboardLayout.DVORAK, KeyboardLayout.AZERTY, KeyboardLayout.COLEMAK]
        idx = layouts.index(self._layout) if self._layout in layouts else 0
        self._layout = layouts[(idx + 1) % len(layouts)]
        return self._layout

    def set_mode(self, mode: KeyboardMode) -> None:
        self._mode = mode

    def cycle_mode(self) -> KeyboardMode:
        modes = [KeyboardMode.LETTERS, KeyboardMode.NUMBERS, KeyboardMode.SYMBOLS, KeyboardMode.EMOJI]
        idx = modes.index(self._mode)
        self._mode = modes[(idx + 1) % len(modes)]
        return self._mode

    @property
    def current_keys(self) -> List[List[Key]]:
        layout = LAYOUTS.get(self._layout, LAYOUTS[KeyboardLayout.QWERTY])
        return layout.get(self._mode.value, layout.get("letters", []))

    @property
    def layout(self) -> KeyboardLayout:
        return self._layout

    @property
    def mode(self) -> KeyboardMode:
        return self._mode

    # ── Input Handling ────────────────────────────────────────────────

    def press_key(self, key: Key) -> Optional[str]:
        """Process a key press."""
        # Record press
        press = KeyPress(key=key.label, is_shifted=self._shift_active)
        self._key_history.append(press)
        if len(self._key_history) > self._max_history:
            self._key_history.pop(0)

        # Handle function keys
        if key.is_function:
            return self._handle_function_key(key)

        # Handle modifier
        if key.is_modifier:
            if key.label == "⇧":
                self._shift_active = not self._shift_active
            return None

        # Handle emoji
        if self._mode == KeyboardMode.EMOJI and key.emoji:
            self._insert_char(key.emoji)
            self._add_recent_emoji(key.emoji)
            return key.emoji

        # Handle regular key
        char = key.label
        if self._shift_active or self._caps_lock:
            char = char.upper()
            self._shift_active = False

        self._insert_char(char)
        self._update_predictions()
        return char

    def _handle_function_key(self, key: Key) -> Optional[str]:
        if key.label == "⌫":
            self._backspace()
            return "backspace"
        elif key.label == "⏎":
            self._insert_char("\n")
            return "enter"
        elif key.label == " ":
            self._insert_char(" ")
            return "space"
        elif key.label == "123":
            self.set_mode(KeyboardMode.NUMBERS)
            return "mode_numbers"
        elif key.label == "ABC":
            self.set_mode(KeyboardMode.LETTERS)
            return "mode_letters"
        elif key.label == "#+=":
            self.set_mode(KeyboardMode.SYMBOLS)
            return "mode_symbols"
        return None

    def _insert_char(self, char: str) -> None:
        self._input_text = (
            self._input_text[:self._cursor_pos] +
            char +
            self._input_text[self._cursor_pos:]
        )
        self._cursor_pos += len(char)
        self._notify("text_change")

    def _backspace(self) -> None:
        if self._cursor_pos > 0:
            self._input_text = (
                self._input_text[:self._cursor_pos - 1] +
                self._input_text[self._cursor_pos:]
            )
            self._cursor_pos -= 1
            self._notify("text_change")

    def _delete_forward(self) -> None:
        if self._cursor_pos < len(self._input_text):
            self._input_text = (
                self._input_text[:self._cursor_pos] +
                self._input_text[self._cursor_pos + 1:]
            )
            self._notify("text_change")

    # ── Text Management ───────────────────────────────────────────────

    @property
    def input_text(self) -> str:
        return self._input_text

    def set_input_text(self, text: str) -> None:
        self._input_text = text
        self._cursor_pos = len(text)

    def clear_input(self) -> None:
        self._input_text = ""
        self._cursor_pos = 0

    def move_cursor(self, offset: int) -> None:
        self._cursor_pos = max(0, min(len(self._input_text), self._cursor_pos + offset))

    def insert_prediction(self, index: int) -> Optional[str]:
        if 0 <= index < len(self._predictions):
            word = self._predictions[index]
            # Find start of current word
            pos = self._cursor_pos
            while pos > 0 and self._input_text[pos - 1] != " ":
                pos -= 1
            # Replace current word
            self._input_text = self._input_text[:pos] + word + " " + self._input_text[self._cursor_pos:]
            self._cursor_pos = pos + len(word) + 1
            self._predictions.clear()
            return word
        return None

    # ── Accessibility ─────────────────────────────────────────────────

    def toggle_sticky_keys(self) -> bool:
        self._sticky_keys = not self._sticky_keys
        return self._sticky_keys

    def toggle_high_contrast(self) -> bool:
        self._high_contrast = not self._high_contrast
        return self._high_contrast

    def toggle_magnifier(self) -> bool:
        self._key_magnifier = not self._key_magnifier
        return self._key_magnifier

    def toggle_caps_lock(self) -> bool:
        self._caps_lock = not self._caps_lock
        return self._caps_lock

    def toggle_ctrl(self) -> bool:
        self._ctrl_active = not self._ctrl_active
        return self._ctrl_active

    def toggle_alt(self) -> bool:
        self._alt_active = not self._alt_active
        return self._alt_active

    @property
    def sticky_keys(self) -> bool:
        return self._sticky_keys

    @property
    def high_contrast(self) -> bool:
        return self._high_contrast

    @property
    def caps_lock(self) -> bool:
        return self._caps_lock

    @property
    def shift_active(self) -> bool:
        return self._shift_active

    # ── Predictive Text ───────────────────────────────────────────────

    def _update_predictions(self) -> None:
        """Simple word prediction based on prefix."""
        # Get current word
        pos = self._cursor_pos
        start = pos
        while start > 0 and self._input_text[start - 1] != " ":
            start -= 1
        current = self._input_text[start:pos].lower()

        if len(current) < 2:
            self._predictions.clear()
            return

        self._predictions = [w for w in self._word_list if w.startswith(current)][:5]
        self._selected_prediction = 0

    @property
    def predictions(self) -> List[str]:
        return list(self._predictions)

    # ── Emoji ─────────────────────────────────────────────────────────

    def _add_recent_emoji(self, emoji: str) -> None:
        if emoji in self._recent_emojis:
            self._recent_emojis.remove(emoji)
        self._recent_emojis.insert(0, emoji)
        if len(self._recent_emojis) > 20:
            self._recent_emojis.pop()

    @property
    def recent_emojis(self) -> List[str]:
        return list(self._recent_emojis[:12])

    # ── Rendering ─────────────────────────────────────────────────────

    def render(self, width: int = 60, height: int = 20) -> List[str]:
        lines = []
        layout_name = self._layout.value
        mode_name = self._mode.value.title()
        lines.append(f" ⌨️  Virtual Keyboard — {layout_name} ({mode_name})")

        # Status indicators
        status = ""
        if self._caps_lock:
            status += " CAPS"
        if self._shift_active:
            status += " SHIFT"
        if self._ctrl_active:
            status += " CTRL"
        if self._alt_active:
            status += " ALT"
        if self._sticky_keys:
            status += " STICKY"
        if self._high_contrast:
            status += " HC"
        lines.append(f" {status.strip() or 'Ready'}")

        lines.append("─" * width)

        # Key rows
        keys = self.current_keys
        for row in keys:
            row_str = " "
            for key in row:
                label = key.label
                if self._mode != KeyboardMode.EMOJI and (self._shift_active or self._caps_lock):
                    if len(label) == 1 and label.isalpha():
                        label = label.upper()

                cell_width = max(1, key.width) * 4
                row_str += f" {label:^{cell_width}} "
            lines.append(row_str[:width])

        lines.append("─" * width)

        # Input preview
        preview = self._input_text[-50:] if self._input_text else "(empty)"
        lines.append(f" Input: {preview}")

        # Predictions
        if self._predictions:
            pred_str = " ".join(f"[{i+1}] {p}" for i, p in enumerate(self._predictions[:3]))
            lines.append(f" 💡 {pred_str}")

        lines.append("─" * width)

        # Recent emojis
        if self._mode == KeyboardMode.EMOJI:
            lines.append(f" Recent: {' '.join(self.recent_emojis)}")

        return lines

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_key_press(self, cb: Callable) -> None:
        self._on_key_press.append(cb)

    def on_text_change(self, cb: Callable) -> None:
        self._on_text_change.append(cb)

    def _notify(self, event: str) -> None:
        cbs = {"key_press": self._on_key_press, "text_change": self._on_text_change}
        for cb in cbs.get(event, []):
            try:
                cb()
            except Exception:
                pass
