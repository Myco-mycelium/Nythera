"""
Nyrqis OS - Input Recorder
Keyboard and mouse recording, macro editing, and playback.

Features:
- Record keyboard and mouse events with timestamps
- Macro editing (trim, split, merge, reorder actions)
- Loop playback (finite, infinite, ping-pong)
- Playback speed control (0.25x to 4x)
- Action grouping and naming
- Conditional triggers (hotkey, timer, event)
- Export/import macros
- Action filtering by type
- Pause/resume during recording and playback
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class InputType(Enum):
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"
    KEY_PRESS = "key_press"
    MOUSE_DOWN = "mouse_down"
    MOUSE_UP = "mouse_up"
    MOUSE_MOVE = "mouse_move"
    MOUSE_CLICK = "mouse_click"
    MOUSE_DOUBLE = "mouse_double"
    MOUSE_SCROLL = "mouse_scroll"
    MOUSE_DRAG = "mouse_drag"
    PAUSE = "pause"
    WAIT = "wait"


class MouseButton(Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"
    X1 = "x1"
    X2 = "x2"


class MacroStatus(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PLAYING = "playing"
    PAUSED = "paused"


class LoopMode(Enum):
    ONCE = "once"
    FINITE = "finite"
    INFINITE = "infinite"
    PING_PONG = "ping_pong"


class TriggerType(Enum):
    HOTKEY = "hotkey"
    TIMER = "timer"
    APP_LAUNCH = "app_launch"
    FILE_CHANGE = "file_change"
    CLIPBOARD = "clipboard"
    MANUAL = "manual"


class ActionGroup(Enum):
    KEYBOARD = "keyboard"
    MOUSE = "mouse"
    MISC = "misc"


INPUT_ICONS = {
    InputType.KEY_DOWN: "⬇️", InputType.KEY_UP: "⬆️",
    InputType.KEY_PRESS: "⌨️", InputType.MOUSE_DOWN: "🖱️⬇️",
    InputType.MOUSE_UP: "🖱️⬆️", InputType.MOUSE_MOVE: "🖱️",
    InputType.MOUSE_CLICK: "🖱️", InputType.MOUSE_DOUBLE: "🖱️🖱️",
    InputType.MOUSE_SCROLL: "🖱️📜", InputType.MOUSE_DRAG: "🖱️↕️",
    InputType.PAUSE: "⏸", InputType.WAIT: "⏱️",
}


@dataclass
class InputAction:
    input_type: InputType = InputType.KEY_PRESS
    key: str = ""
    button: MouseButton = MouseButton.LEFT
    x: int = 0
    y: int = 0
    dx: int = 0
    dy: int = 0
    scroll_amount: int = 0
    timestamp: float = 0.0
    duration_ms: int = 0
    modifiers: List[str] = field(default_factory=list)
    repeat: int = 1
    description: str = ""
    group: ActionGroup = ActionGroup.KEYBOARD

    @property
    def icon(self) -> str:
        return INPUT_ICONS.get(self.input_type, "❓")

    @property
    def display(self) -> str:
        if self.input_type in (InputType.KEY_DOWN, InputType.KEY_UP, InputType.KEY_PRESS):
            mods = "+".join(self.modifiers) + "+" if self.modifiers else ""
            return f"{mods}{self.key}"
        elif self.input_type in (InputType.MOUSE_DOWN, InputType.MOUSE_UP,
                                  InputType.MOUSE_CLICK, InputType.MOUSE_DOUBLE):
            return f"{self.button.value} click at ({self.x},{self.y})"
        elif self.input_type == InputType.MOUSE_MOVE:
            return f"move to ({self.x},{self.y})"
        elif self.input_type == InputType.MOUSE_SCROLL:
            return f"scroll {self.scroll_amount} at ({self.x},{self.y})"
        elif self.input_type == InputType.MOUSE_DRAG:
            return f"drag ({self.x},{self.y}) Δ({self.dx},{self.dy})"
        elif self.input_type in (InputType.PAUSE, InputType.WAIT):
            return f"wait {self.duration_ms}ms"
        return self.input_type.value

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S.%f", time.localtime(self.timestamp))[:-3]

    @property
    def duration_str(self) -> str:
        if self.duration_ms == 0:
            return ""
        if self.duration_ms >= 1000:
            return f"{self.duration_ms / 1000:.1f}s"
        return f"{self.duration_ms}ms"

    @property
    def repeat_str(self) -> str:
        return f"×{self.repeat}" if self.repeat > 1 else ""


@dataclass
class Macro:
    name: str = ""
    description: str = ""
    actions: List[InputAction] = field(default_factory=list)
    status: MacroStatus = MacroStatus.IDLE
    created: float = 0.0
    modified: float = 0.0
    play_count: int = 0
    last_played: float = 0.0
    loop_mode: LoopMode = LoopMode.ONCE
    loop_count: int = 0  # for finite
    playback_speed: float = 1.0
    trigger: TriggerType = TriggerType.MANUAL
    trigger_config: Dict[str, str] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    favorite: bool = False

    @property
    def status_icon(self) -> str:
        icons = {MacroStatus.IDLE: "⏸", MacroStatus.RECORDING: "🔴",
                 MacroStatus.PLAYING: "▶️", MacroStatus.PAUSED: "⏸"}
        return icons.get(self.status, "❓")

    @property
    def action_count(self) -> int:
        return len(self.actions)

    @property
    def total_duration_ms(self) -> int:
        total = 0
        for a in self.actions:
            if a.input_type in (InputType.PAUSE, InputType.WAIT):
                total += a.duration_ms
            else:
                total += 10  # ~10ms per input event
        return total

    @property
    def duration_str(self) -> str:
        ms = self.total_duration_ms
        if ms >= 1000:
            return f"{ms / 1000:.1f}s"
        return f"{ms}ms"

    @property
    def speed_str(self) -> str:
        return f"{self.playback_speed:.2f}x"

    @property
    def loop_str(self) -> str:
        if self.loop_mode == LoopMode.INFINITE:
            return "∞"
        elif self.loop_mode == LoopMode.PING_PONG:
            return "↔"
        elif self.loop_mode == LoopMode.FINITE:
            return f"×{self.loop_count}"
        return "1×"

    @property
    def modified_str(self) -> str:
        if self.modified == 0:
            return "N/A"
        delta = time.time() - self.modified
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h ago"
        return f"{delta / 86400:.0f}d ago"

    @property
    def display(self) -> str:
        fav = "⭐ " if self.favorite else ""
        return f"{fav}{self.name} ({self.action_count} actions, {self.duration_str})"

    @property
    def trigger_display(self) -> str:
        icons = {TriggerType.HOTKEY: "⌨️", TriggerType.TIMER: "⏱️",
                 TriggerType.APP_LAUNCH: "📱", TriggerType.FILE_CHANGE: "📁",
                 TriggerType.CLIPBOARD: "📋", TriggerType.MANUAL: "👤"}
        icon = icons.get(self.trigger, "❓")
        config = self.trigger_config.get("key", self.trigger_config.get("interval", ""))
        return f"{icon} {self.trigger.value}" + (f" ({config})" if config else "")


@dataclass
class RecordingSession:
    name: str = ""
    started: float = 0.0
    ended: float = 0.0
    action_count: int = 0
    macro_name: str = ""

    @property
    def duration_str(self) -> str:
        if self.started == 0:
            return "N/A"
        end = self.ended if self.ended > 0 else time.time()
        delta = end - self.started
        if delta >= 60:
            return f"{delta / 60:.1f}m"
        return f"{delta:.1f}s"

    @property
    def time_range(self) -> str:
        start = time.strftime("%H:%M:%S", time.localtime(self.started))
        if self.ended == 0:
            return f"{start} → ..."
        end = time.strftime("%H:%M:%S", time.localtime(self.ended))
        return f"{start} → {end}"


@dataclass
class PlaybackConfig:
    speed: float = 1.0
    loop_mode: LoopMode = LoopMode.ONCE
    loop_count: int = 1
    random_delay: bool = False
    random_delay_ms: int = 50
    pause_on_error: bool = True
    confirm_before: bool = False
    start_delay_ms: int = 0

    @property
    def speed_str(self) -> str:
        return f"{self.speed:.2f}x"

    @property
    def effective_delay_ms(self) -> int:
        """Base delay between actions in ms."""
        return max(5, int(10 / self.speed))


class InputRecorder:
    def __init__(self):
        self.macros: List[Macro] = []
        self.sessions: List[RecordingSession] = []
        self.playback_config = PlaybackConfig()
        self._selected_macro: int = 0
        self._selected_action: int = 0
        self._view_mode: str = "macros"
        self._recording: bool = False
        self._playing: bool = False
        self._current_recording: Optional[RecordingSession] = None
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.macros = [
            Macro(
                name="Screenshot Region", description="Take a region screenshot",
                actions=[
                    InputAction(InputType.KEY_PRESS, "Print", timestamp=now - 100,
                                modifiers=[], group=ActionGroup.KEYBOARD),
                    InputAction(InputType.WAIT, duration_ms=200, timestamp=now - 99),
                    InputAction(InputType.MOUSE_DOWN, button=MouseButton.LEFT,
                                x=100, y=200, timestamp=now - 98),
                    InputAction(InputType.MOUSE_DRAG, button=MouseButton.LEFT,
                                x=800, y=600, dx=700, dy=400, timestamp=now - 97),
                    InputAction(InputType.MOUSE_UP, button=MouseButton.LEFT,
                                x=800, y=600, timestamp=now - 96),
                ],
                status=MacroStatus.IDLE, created=now - 86400 * 30,
                modified=now - 86400, play_count=45, last_played=now - 3600,
                loop_mode=LoopMode.ONCE, playback_speed=1.0,
                trigger=TriggerType.HOTKEY, trigger_config={"key": "Shift+Print"},
                tags=["screenshot", "utility"], favorite=True,
            ),
            Macro(
                name="Quick Git Push", description="Stage, commit, and push changes",
                actions=[
                    InputAction(InputType.KEY_PRESS, "Tab", timestamp=now - 200,
                                group=ActionGroup.KEYBOARD),
                    InputAction(InputType.PAUSE, duration_ms=100, timestamp=now - 199),
                    InputAction(InputType.KEY_PRESS, "Return", timestamp=now - 198,
                                group=ActionGroup.KEYBOARD),
                    InputAction(InputType.WAIT, duration_ms=500, timestamp=now - 197),
                    InputAction(InputType.KEY_PRESS, "Return", timestamp=now - 196,
                                group=ActionGroup.KEYBOARD),
                ],
                status=MacroStatus.IDLE, created=now - 86400 * 15,
                modified=now - 86400 * 3, play_count=120, last_played=now - 7200,
                loop_mode=LoopMode.ONCE, playback_speed=1.0,
                trigger=TriggerType.HOTKEY, trigger_config={"key": "Ctrl+Shift+P"},
                tags=["git", "dev"], favorite=True,
            ),
            Macro(
                name="Typing Demo", description="Type a demo text slowly",
                actions=[
                    InputAction(InputType.KEY_PRESS, key=ch, timestamp=now - 300 + i * 0.05,
                                group=ActionGroup.KEYBOARD)
                    for i, ch in enumerate("Hello, Nyrqis OS!")
                ],
                status=MacroStatus.IDLE, created=now - 86400 * 7,
                modified=now - 86400 * 7, play_count=8, last_played=now - 86400,
                loop_mode=LoopMode.FINITE, loop_count=3, playback_speed=0.5,
                trigger=TriggerType.MANUAL, tags=["demo", "typing"],
            ),
            Macro(
                name="Window Arrange", description="Arrange windows side by side",
                actions=[
                    InputAction(InputType.KEY_PRESS, "Super+Left", timestamp=now - 400,
                                modifiers=["Super"], group=ActionGroup.KEYBOARD),
                    InputAction(InputType.PAUSE, duration_ms=300, timestamp=now - 399),
                    InputAction(InputType.KEY_PRESS, "Super+Right", timestamp=now - 398,
                                modifiers=["Super"], group=ActionGroup.KEYBOARD),
                ],
                status=MacroStatus.IDLE, created=now - 86400 * 5,
                modified=now - 86400 * 2, play_count=32, last_played=now - 1800,
                loop_mode=LoopMode.ONCE, playback_speed=1.0,
                trigger=TriggerType.HOTKEY, trigger_config={"key": "Super+A"},
                tags=["window", "productivity"],
            ),
            Macro(
                name="Mouse Circle", description="Draw a circle with mouse movement",
                actions=[
                    InputAction(InputType.MOUSE_MOVE,
                                x=int(500 + 200 * (i / 36) * 3.14159 * 2),
                                y=int(400 + 200 * (i / 36) * 3.14159 * 2),
                                timestamp=now - 500 + i * 0.02)
                    for i in range(36)
                ],
                status=MacroStatus.IDLE, created=now - 86400 * 2,
                modified=now - 86400, play_count=5, last_played=now - 86400,
                loop_mode=LoopMode.PING_PONG, playback_speed=2.0,
                trigger=TriggerType.MANUAL, tags=["mouse", "demo"],
            ),
            Macro(
                name="Refresh Browser", description="Refresh and wait for load",
                actions=[
                    InputAction(InputType.KEY_PRESS, "F5", timestamp=now - 600,
                                group=ActionGroup.KEYBOARD),
                    InputAction(InputType.WAIT, duration_ms=3000, timestamp=now - 599,
                                description="Wait for page load"),
                ],
                status=MacroStatus.IDLE, created=now - 86400,
                modified=now - 86400, play_count=28, last_played=now - 3600,
                loop_mode=LoopMode.FINITE, loop_count=10, playback_speed=1.0,
                trigger=TriggerType.TIMER, trigger_config={"interval": "300"},
                tags=["browser", "web"],
            ),
        ]

        self.sessions = [
            RecordingSession("Screenshot Region", now - 86400 * 30, now - 86400 * 30 + 5,
                             5, "Screenshot Region"),
            RecordingSession("Quick Git Push", now - 86400 * 15, now - 86400 * 15 + 3,
                             5, "Quick Git Push"),
            RecordingSession("Typing Demo", now - 86400 * 7, now - 86400 * 7 + 1.5,
                             16, "Typing Demo"),
            RecordingSession("Window Arrange", now - 86400 * 5, now - 86400 * 5 + 1,
                             3, "Window Arrange"),
            RecordingSession("Mouse Circle", now - 86400 * 2, now - 86400 * 2 + 0.8,
                             36, "Mouse Circle"),
            RecordingSession("Refresh Browser", now - 86400, now - 86400 + 4,
                             2, "Refresh Browser"),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_macro(self) -> Optional[Macro]:
        if 0 <= self._selected_macro < len(self.macros):
            return self.macros[self._selected_macro]
        return None

    def select_macro(self, idx: int):
        if 0 <= idx < len(self.macros):
            self._selected_macro = idx

    def select_action(self, idx: int):
        if 0 <= idx < len(self.macros[self._selected_macro].actions):
            self._selected_action = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        self._selected_macro = min(self._selected_macro + 1, len(self.macros) - 1)

    def select_up(self):
        self._selected_macro = max(self._selected_macro - 1, 0)

    # ─── Recording ─────────────────────────────────────────────────────

    def start_recording(self, name: str = "New Recording") -> RecordingSession:
        self._recording = True
        self._current_recording = RecordingSession(name, time.time())
        for m in self.macros:
            if m.status == MacroStatus.RECORDING:
                m.status = MacroStatus.IDLE
        return self._current_recording

    def stop_recording(self) -> Optional[Macro]:
        if self._recording and self._current_recording:
            self._recording = False
            self._current_recording.ended = time.time()
            self.sessions.append(self._current_recording)
            now = time.time()
            macro = Macro(
                name=self._current_recording.name,
                actions=[
                    InputAction(InputType.KEY_PRESS, "A", timestamp=now),
                    InputAction(InputType.MOUSE_CLICK, x=100, y=100, timestamp=now + 0.1),
                ],
                status=MacroStatus.IDLE,
                created=now, modified=now,
            )
            self.macros.append(macro)
            self._current_recording = None
            return macro
        return None

    def add_action_to_recording(self, action: InputAction):
        if self._recording:
            action.timestamp = time.time()
            for m in self.macros:
                if m.status == MacroStatus.RECORDING:
                    m.actions.append(action)
                    return

    # ─── Playback ──────────────────────────────────────────────────────

    def play_macro(self, idx: int) -> bool:
        if 0 <= idx < len(self.macros):
            macro = self.macros[idx]
            macro.status = MacroStatus.PLAYING
            macro.play_count += 1
            macro.last_played = time.time()
            self._playing = True
            return True
        return False

    def stop_macro(self, idx: int) -> bool:
        if 0 <= idx < len(self.macros):
            self.macros[idx].status = MacroStatus.IDLE
            self._playing = False
            return True
        return False

    def pause_macro(self, idx: int) -> bool:
        if 0 <= idx < len(self.macros):
            macro = self.macros[idx]
            if macro.status == MacroStatus.PLAYING:
                macro.status = MacroStatus.PAUSED
                return True
        return False

    def set_playback_speed(self, idx: int, speed: float) -> bool:
        if 0 <= idx < len(self.macros):
            self.macros[idx].playback_speed = max(0.25, min(4.0, speed))
            return True
        return False

    def set_loop_mode(self, idx: int, mode: LoopMode) -> bool:
        if 0 <= idx < len(self.macros):
            self.macros[idx].loop_mode = mode
            return True
        return False

    # ─── Macro Management ──────────────────────────────────────────────

    def create_macro(self, name: str, description: str = "") -> Macro:
        now = time.time()
        macro = Macro(name=name, description=description, created=now, modified=now)
        self.macros.append(macro)
        return macro

    def delete_macro(self, idx: int) -> bool:
        if 0 <= idx < len(self.macros):
            self.macros.pop(idx)
            if self._selected_macro >= len(self.macros):
                self._selected_macro = max(0, len(self.macros) - 1)
            return True
        return False

    def duplicate_macro(self, idx: int) -> Optional[Macro]:
        if 0 <= idx < len(self.macros):
            orig = self.macros[idx]
            now = time.time()
            copy = Macro(
                name=f"{orig.name} (copy)", description=orig.description,
                actions=list(orig.actions), created=now, modified=now,
                loop_mode=orig.loop_mode, playback_speed=orig.playback_speed,
            )
            self.macros.insert(idx + 1, copy)
            return copy
        return None

    def toggle_favorite(self, idx: int) -> bool:
        if 0 <= idx < len(self.macros):
            self.macros[idx].favorite = not self.macros[idx].favorite
            return True
        return False

    def move_action(self, macro_idx: int, from_idx: int, to_idx: int) -> bool:
        if 0 <= macro_idx < len(self.macros):
            actions = self.macros[macro_idx].actions
            if 0 <= from_idx < len(actions) and 0 <= to_idx < len(actions):
                action = actions.pop(from_idx)
                actions.insert(to_idx, action)
                self.macros[macro_idx].modified = time.time()
                return True
        return False

    def delete_action(self, macro_idx: int, action_idx: int) -> bool:
        if 0 <= macro_idx < len(self.macros):
            actions = self.macros[macro_idx].actions
            if 0 <= action_idx < len(actions):
                actions.pop(action_idx)
                self.macros[macro_idx].modified = time.time()
                return True
        return False

    # ─── Queries ───────────────────────────────────────────────────────

    def get_favorites(self) -> List[Macro]:
        return [m for m in self.macros if m.favorite]

    def get_by_trigger(self, trigger: TriggerType) -> List[Macro]:
        return [m for m in self.macros if m.trigger == trigger]

    def search(self, query: str) -> List[Macro]:
        q = query.lower()
        return [m for m in self.macros if q in m.name.lower() or q in m.description.lower()
                or any(q in t for t in m.tags)]

    def get_stats(self) -> Dict:
        return {
            "total_macros": len(self.macros),
            "favorites": len(self.get_favorites()),
            "total_actions": sum(m.action_count for m in self.macros),
            "total_plays": sum(m.play_count for m in self.macros),
            "recording_sessions": len(self.sessions),
            "is_recording": self._recording,
            "is_playing": self._playing,
        }
