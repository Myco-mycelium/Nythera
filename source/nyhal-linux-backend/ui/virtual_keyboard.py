"""
Nyrqis OS - Virtual Keyboard
Layout editor, key mapping, and macro support.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class KeyAction(Enum):
    KEY_PRESS = "key_press"
    KEY_COMBINATION = "key_combination"
    MACRO = "macro"
    MOUSE_CLICK = "mouse_click"
    TEXT_OUTPUT = "text_output"
    SWITCH_LAYER = "switch_layer"


class Modifier(Enum):
    NONE = ""
    SHIFT = "Shift"
    CTRL = "Ctrl"
    ALT = "Alt"
    SUPER = "Super"
    CAPS = "CapsLock"


@dataclass
class KeyDef:
    name: str
    scancode: int = 0
    width: int = 1
    height: int = 1
    label: str = ""
    label_shifted: str = ""
    action: KeyAction = KeyAction.KEY_PRESS
    modifiers: List[Modifier] = field(default_factory=list)
    repeat: bool = True
    layer: int = 0
    color: str = ""
    macro_id: str = ""

    @property
    def display_label(self) -> str:
        return self.label_shifted if self.label_shifted else self.label

    @property
    def width_px(self) -> str:
        return f"{self.width * 60}px"


@dataclass
class KeyboardLayout:
    name: str
    language: str = "en"
    variant: str = "QWERTY"
    layers: int = 3
    rows: List[List[KeyDef]] = field(default_factory=list)
    description: str = ""
    is_custom: bool = False

    @property
    def key_count(self) -> int:
        return sum(len(row) for row in self.rows)


@dataclass
class Macro:
    name: str
    shortcut: str = ""
    keys: List[str] = field(default_factory=list)
    delays_ms: List[int] = field(default_factory=list)
    repeat_count: int = 1
    description: str = ""
    use_count: int = 0
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    @property
    def preview(self) -> str:
        return " → ".join(self.keys[:5]) + ("..." if len(self.keys) > 5 else "")


@dataclass
class KeyMapping:
    original_key: str
    mapped_action: KeyAction = KeyAction.KEY_PRESS
    target_key: str = ""
    macro_id: str = ""
    modifiers: List[Modifier] = field(default_factory=list)
    description: str = ""
    enabled: bool = True


class VirtualKeyboard:
    def __init__(self):
        self.layouts: List[KeyboardLayout] = []
        self.current_layout: Optional[KeyboardLayout] = None
        self.macros: List[Macro] = []
        self.mappings: List[KeyMapping] = []
        self.active_layer: int = 0
        self.pressed_keys: List[str] = []
        self.macro_recording: bool = False
        self.recorded_keys: List[str] = []
        self.repeat_enabled: bool = True
        self.key_repeat_delay_ms: int = 500
        self.key_repeat_rate_ms: int = 33
        self.volume: int = 50
        self._create_sample_data()

    def _create_sample_data(self):
        row0 = [KeyDef(name="Esc", label="Esc", width=1, scancode=1),
                KeyDef(name="F1", label="F1", scancode=59),
                KeyDef(name="F2", label="F2", scancode=60),
                KeyDef(name="F3", label="F3", scancode=61),
                KeyDef(name="F4", label="F4", scancode=62),
                KeyDef(name="F5", label="F5", scancode=63),
                KeyDef(name="F6", label="F6", scancode=64),
                KeyDef(name="F7", label="F7", scancode=65),
                KeyDef(name="F8", label="F8", scancode=66),
                KeyDef(name="F9", label="F9", scancode=67),
                KeyDef(name="F10", label="F10", scancode=68),
                KeyDef(name="F11", label="F11", scancode=87),
                KeyDef(name="F12", label="F12", scancode=88),
                KeyDef(name="PrtSc", label="PrtSc", scancode=99),
                KeyDef(name="ScrollLock", label="ScrLk", scancode=70),
                KeyDef(name="Pause", label="Pause", scancode=119)]

        row1 = [KeyDef(name="Backquote", label="`", label_shifted="~", scancode=41),
                KeyDef(name="1", label="1", label_shifted="!", scancode=2),
                KeyDef(name="2", label="2", label_shifted="@", scancode=3),
                KeyDef(name="3", label="3", label_shifted="#", scancode=4),
                KeyDef(name="4", label="4", label_shifted="$", scancode=5),
                KeyDef(name="5", label="5", label_shifted="%", scancode=6),
                KeyDef(name="6", label="6", label_shifted="^", scancode=7),
                KeyDef(name="7", label="7", label_shifted="&", scancode=8),
                KeyDef(name="8", label="8", label_shifted="*", scancode=9),
                KeyDef(name="9", label="9", label_shifted="(", scancode=10),
                KeyDef(name="0", label="0", label_shifted=")", scancode=11),
                KeyDef(name="Minus", label="-", label_shifted="_", scancode=12),
                KeyDef(name="Equal", label="=", label_shifted="+", scancode=13),
                KeyDef(name="Backspace", label="⌫", width=2, scancode=14)]

        row2 = [KeyDef(name="Tab", label="Tab", width=1, scancode=15),
                KeyDef(name="Q", label="Q", scancode=16),
                KeyDef(name="W", label="W", scancode=17),
                KeyDef(name="E", label="E", scancode=18),
                KeyDef(name="R", label="R", scancode=19),
                KeyDef(name="T", label="T", scancode=20),
                KeyDef(name="Y", label="Y", scancode=21),
                KeyDef(name="U", label="U", scancode=22),
                KeyDef(name="I", label="I", scancode=23),
                KeyDef(name="O", label="O", scancode=24),
                KeyDef(name="P", label="P", scancode=25),
                KeyDef(name="BracketLeft", label="[", label_shifted="{", scancode=26),
                KeyDef(name="BracketRight", label="]", label_shifted="}", scancode=27),
                KeyDef(name="Backslash", label="\\", label_shifted="|", scancode=43)]

        row3 = [KeyDef(name="CapsLock", label="CapsLock", width=1, scancode=58),
                KeyDef(name="A", label="A", scancode=30),
                KeyDef(name="S", label="S", scancode=31),
                KeyDef(name="D", label="D", scancode=32),
                KeyDef(name="F", label="F", scancode=33),
                KeyDef(name="G", label="G", scancode=34),
                KeyDef(name="H", label="H", scancode=35),
                KeyDef(name="J", label="J", scancode=36),
                KeyDef(name="K", label="K", scancode=37),
                KeyDef(name="L", label="L", scancode=38),
                KeyDef(name="Semicolon", label=";", label_shifted=":", scancode=39),
                KeyDef(name="Quote", label="'", label_shifted='"', scancode=40),
                KeyDef(name="Enter", label="Enter", width=2, scancode=28)]

        row4 = [KeyDef(name="ShiftLeft", label="Shift", width=1, scancode=42),
                KeyDef(name="Z", label="Z", scancode=44),
                KeyDef(name="X", label="X", scancode=45),
                KeyDef(name="C", label="C", scancode=46),
                KeyDef(name="V", label="V", scancode=47),
                KeyDef(name="B", label="B", scancode=48),
                KeyDef(name="N", label="N", scancode=49),
                KeyDef(name="M", label="M", scancode=50),
                KeyDef(name="Comma", label=",", label_shifted="<", scancode=51),
                KeyDef(name="Period", label=".", label_shifted=">", scancode=52),
                KeyDef(name="Slash", label="/", label_shifted="?", scancode=53),
                KeyDef(name="ShiftRight", label="Shift", width=2, scancode=54)]

        row5 = [KeyDef(name="CtrlLeft", label="Ctrl", scancode=29),
                KeyDef(name="SuperLeft", label="Super", scancode=125),
                KeyDef(name="AltLeft", label="Alt", scancode=56),
                KeyDef(name="Space", label="Space", width=6, scancode=57),
                KeyDef(name="AltRight", label="Alt", scancode=100),
                KeyDef(name="SuperRight", label="Super", scancode=126),
                KeyDef(name="Menu", label="Menu", scancode=127),
                KeyDef(name="CtrlRight", label="Ctrl", scancode=29)]

        qwerty = KeyboardLayout(
            name="QWERTY (US)", language="en", variant="QWERTY",
            rows=[row0, row1, row2, row3, row4, row5],
            description="Standard US QWERTY layout")

        self.layouts = [qwerty]
        self.current_layout = qwerty

        self.macros = [
            Macro(name="Screenshot", shortcut="Print", keys=["PrintScreen"],
                  delays_ms=[0], description="Take a screenshot", use_count=45),
            Macro(name="Copy Line", shortcut="Ctrl+Shift+C",
                  keys=["Home", "Shift", "End", "Ctrl", "C", "Right"],
                  delays_ms=[0, 0, 0, 0, 0, 0], description="Select and copy current line",
                  use_count=120),
            Macro(name="Date Stamp", shortcut="", keys=[], description="Insert current date",
                  use_count=28),
            Macro(name="Nyrqis Build", shortcut="Super+B",
                  keys=["Ctrl", "Shift", "B"], delays_ms=[0, 50, 0],
                  description="Trigger Nyrqis OS build", use_count=38),
            Macro(name="Emoji Picker", shortcut="Super+.",
                  keys=["Super", "."], delays_ms=[0],
                  description="Open emoji picker", use_count=65),
        ]

        self.mappings = [
            KeyMapping(original_key="CapsLock", mapped_action=KeyAction.KEY_PRESS,
                       target_key="CtrlLeft", description="CapsLock → Ctrl (SpaceCadet)"),
            KeyMapping(original_key="Menu", mapped_action=KeyAction.KEY_COMBINATION,
                       target_key="Ctrl+Shift+Escape", description="Menu → Task Manager"),
            KeyMapping(original_key="Print", mapped_action=KeyAction.MACRO,
                       macro_id="Screenshot", description="Print → Screenshot area"),
        ]

    def press_key(self, key_name: str) -> bool:
        if key_name not in self.pressed_keys:
            self.pressed_keys.append(key_name)
            mapping = next((m for m in self.mappings if m.original_key == key_name and m.enabled), None)
            if mapping:
                return True
        return True

    def release_key(self, key_name: str) -> bool:
        if key_name in self.pressed_keys:
            self.pressed_keys.remove(key_name)
            return True
        return False

    def release_all(self) -> int:
        count = len(self.pressed_keys)
        self.pressed_keys.clear()
        return count

    def start_macro_record(self) -> bool:
        self.macro_recording = True
        self.recorded_keys.clear()
        return True

    def stop_macro_record(self) -> Optional[List[str]]:
        self.macro_recording = False
        keys = self.recorded_keys.copy()
        self.recorded_keys.clear()
        return keys

    def add_macro(self, name: str, keys: List[str], **kwargs) -> Macro:
        macro = Macro(name=name, keys=keys, **kwargs)
        self.macros.append(macro)
        return macro

    def run_macro(self, name: str) -> bool:
        macro = next((m for m in self.macros if m.name == name), None)
        if macro:
            macro.use_count += 1
            return True
        return False

    def add_mapping(self, original_key: str, target: str, **kwargs) -> KeyMapping:
        mapping = KeyMapping(original_key=original_key, target_key=target, **kwargs)
        self.mappings.append(mapping)
        return mapping

    def get_key(self, name: str) -> Optional[KeyDef]:
        if not self.current_layout:
            return None
        for row in self.current_layout.rows:
            for key in row:
                if key.name == name:
                    return key
        return None

    def get_layer_keys(self, layer: int) -> List[KeyDef]:
        if not self.current_layout:
            return []
        return [k for row in self.current_layout.rows for k in row if k.layer == layer]

    def get_macros(self) -> List[Macro]:
        return sorted(self.macros, key=lambda m: m.use_count, reverse=True)

    def get_stats(self) -> Dict:
        total_keys = self.current_layout.key_count if self.current_layout else 0
        return {
            "layouts": len(self.layouts),
            "total_keys": total_keys,
            "macros": len(self.macros),
            "mappings": len(self.mappings),
            "active_layer": self.active_layer,
            "pressed": len(self.pressed_keys),
        }


@dataclass
class Key:
    name: str = ""
    keycode: int = 0
    label: str = ""
    width: int = 1
    modifier: bool = False

KeyPress = KeyDef

# ─── Backward-compat exports ────────────────────────────────────────────
from enum import Enum as _Enum

class KeyboardMode(_Enum):
    LETTERS = "letters"
    NUMBERS = "numbers"
    SYMBOLS = "symbols"
    EMOJI = "emoji"
    HANDWRITING = "handwriting"
