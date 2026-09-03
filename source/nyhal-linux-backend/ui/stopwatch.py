"""
Nyrqis Stopwatch — time tracking with stopwatch, timer, and intervals.

Features:
- Stopwatch with start/stop/reset and lap recording
- Countdown timer with presets
- Interval timer (HIIT/workout mode)
- Lap times with split and differential
- Multiple simultaneous timers
- Visual progress indicators
- Alarm notification on timer complete
- Keyboard shortcuts throughout
"""

import time
import threading
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable
from datetime import datetime, timedelta


# ─── Data Classes ────────────────────────────────────────────────────────


class TimerMode(Enum):
    STOPWATCH = "stopwatch"
    COUNTDOWN = "countdown"
    INTERVAL = "interval"


class TimerStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


@dataclass
class Lap:
    """A lap time recording."""
    lap_number: int
    split_time: float  # Total time from start
    lap_time: float  # Time for this lap
    timestamp: float = field(default_factory=time.time)

    @property
    def split_str(self) -> str:
        return self._fmt(self.split_time)

    @property
    def lap_str(self) -> str:
        return self._fmt(self.lap_time)

    @property
    def diff_str(self) -> str:
        """Difference from best lap."""
        return ""

    def _fmt(self, seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 100)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}.{ms:02d}"
        return f"{m:02d}:{s:02d}.{ms:02d}"


@dataclass
class TimerPreset:
    """A countdown timer preset."""
    name: str
    seconds: int
    category: str = "General"

    @property
    def display(self) -> str:
        m = self.seconds // 60
        s = self.seconds % 60
        if m > 0:
            return f"{self.name} ({m}m {s}s)"
        return f"{self.name} ({s}s)"


@dataclass
class IntervalConfig:
    """Interval timer configuration."""
    work_seconds: int = 30
    rest_seconds: int = 10
    rounds: int = 8
    name: str = "HIIT"

    @property
    def total_time(self) -> int:
        return (self.work_seconds + self.rest_seconds) * self.rounds

    @property
    def display(self) -> str:
        return f"{self.name}: {self.work_seconds}s work / {self.rest_seconds}s rest × {self.rounds}"


@dataclass
class ActiveTimer:
    """An active timer instance."""
    timer_id: str
    name: str
    mode: TimerMode = TimerMode.STOPWATCH
    status: TimerStatus = TimerStatus.IDLE
    elapsed: float = 0.0
    target: float = 0.0  # Target time for countdown
    start_time: float = 0.0
    pause_time: float = 0.0
    paused_duration: float = 0.0
    laps: List[Lap] = field(default_factory=list)
    interval: Optional[IntervalConfig] = None
    current_round: int = 0
    is_work: bool = True

    @property
    def remaining(self) -> float:
        if self.mode == TimerMode.COUNTDOWN:
            return max(0, self.target - self.elapsed)
        return self.elapsed

    @property
    def display_time(self) -> str:
        t = self.remaining if self.mode == TimerMode.COUNTDOWN else self.elapsed
        return self._fmt(t)

    @property
    def progress(self) -> float:
        if self.mode == TimerMode.COUNTDOWN and self.target > 0:
            return min(1.0, self.elapsed / self.target)
        return 0.0

    @property
    def progress_str(self) -> str:
        pct = self.progress * 100
        return f"{pct:.0f}%"

    @property
    def status_icon(self) -> str:
        icons = {
            TimerStatus.IDLE: "⏹️",
            TimerStatus.RUNNING: "▶️",
            TimerStatus.PAUSED: "⏸️",
            TimerStatus.COMPLETED: "✅",
        }
        return icons.get(self.status, "❓")

    def _fmt(self, seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 100)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}.{ms:02d}"
        return f"{m:02d}:{s:02d}.{ms:02d}"


# ─── Stopwatch & Timer ───────────────────────────────────────────────────


class Stopwatch:
    """
    Stopwatch and timer application for Nyrqis OS.
    """

    def __init__(self):
        self._timers: List[ActiveTimer] = [
            ActiveTimer(timer_id="sw1", name="Stopwatch", mode=TimerMode.STOPWATCH),
        ]
        self._active_timer_index: int = 0
        self._view_mode: str = "main"  # main, presets, intervals
        self._selected_index: int = 0
        self._next_id: int = 2

        # Presets
        self._presets = [
            TimerPreset("Quick", 30, "Short"),
            TimerPreset("1 Minute", 60, "Short"),
            TimerPreset("3 Minutes", 180, "Medium"),
            TimerPreset("5 Minutes", 300, "Medium"),
            TimerPreset("10 Minutes", 600, "Long"),
            TimerPreset("15 Minutes", 900, "Long"),
            TimerPreset("20 Minutes", 1200, "Long"),
            TimerPreset("25 Minutes (Pomodoro)", 1500, "Work"),
            TimerPreset("30 Minutes", 1800, "Work"),
            TimerPreset("45 Minutes", 2700, "Work"),
            TimerPreset("1 Hour", 3600, "Long"),
            TimerPreset("2 Hours", 7200, "Long"),
        ]

        # Interval presets
        self._intervals = [
            IntervalConfig(30, 10, 8, "HIIT Basic"),
            IntervalConfig(40, 20, 10, "HIIT Advanced"),
            IntervalConfig(20, 10, 10, "Tabata"),
            IntervalConfig(45, 15, 6, "Strength"),
            IntervalConfig(60, 30, 5, "Endurance"),
            IntervalConfig(25, 25, 12, "Balanced"),
        ]

        # Callbacks
        self._on_complete: List[Callable] = []

    # ── Timer Operations ──────────────────────────────────────────────

    @property
    def active_timer(self) -> Optional[ActiveTimer]:
        if 0 <= self._active_timer_index < len(self._timers):
            return self._timers[self._active_timer_index]
        return None

    @property
    def timers(self) -> List[ActiveTimer]:
        return list(self._timers)

    def start(self) -> None:
        timer = self.active_timer
        if timer and timer.status in (TimerStatus.IDLE, TimerStatus.PAUSED):
            if timer.status == TimerStatus.PAUSED:
                timer.paused_duration += time.time() - timer.pause_time
            timer.start_time = time.time()
            timer.status = TimerStatus.RUNNING

    def stop(self) -> None:
        timer = self.active_timer
        if timer and timer.status == TimerStatus.RUNNING:
            timer.elapsed = time.time() - timer.start_time - timer.paused_duration
            timer.status = TimerStatus.PAUSED
            timer.pause_time = time.time()

    def reset(self) -> None:
        timer = self.active_timer
        if timer:
            timer.elapsed = 0
            timer.status = TimerStatus.IDLE
            timer.laps.clear()
            timer.paused_duration = 0
            timer.current_round = 0

    def toggle(self) -> None:
        timer = self.active_timer
        if timer:
            if timer.status == TimerStatus.RUNNING:
                self.stop()
            else:
                self.start()

    def record_lap(self) -> Optional[Lap]:
        """Record a lap time."""
        timer = self.active_timer
        if timer and timer.status == TimerStatus.RUNNING:
            # Calculate current elapsed
            current_elapsed = time.time() - timer.start_time - timer.paused_duration
            lap_time = current_elapsed
            if timer.laps:
                lap_time = current_elapsed - timer.laps[-1].split_time

            lap = Lap(
                lap_number=len(timer.laps) + 1,
                split_time=current_elapsed,
                lap_time=lap_time,
            )
            timer.laps.append(lap)
            return lap
        return None

    def update_timers(self) -> None:
        """Update all running timers."""
        now = time.time()
        for timer in self._timers:
            if timer.status == TimerStatus.RUNNING:
                timer.elapsed = now - timer.start_time - timer.paused_duration

                # Check countdown completion
                if timer.mode == TimerMode.COUNTDOWN and timer.elapsed >= timer.target:
                    timer.status = TimerStatus.COMPLETED
                    timer.elapsed = timer.target
                    self._notify_complete(timer)

                # Handle interval timer
                elif timer.mode == TimerMode.INTERVAL and timer.interval:
                    self._update_interval(timer)

    def _update_interval(self, timer: ActiveTimer) -> None:
        """Update interval timer logic."""
        if not timer.interval:
            return

        interval = timer.interval
        cycle_time = interval.work_seconds if timer.is_work else interval.rest_seconds
        cycle_elapsed = timer.elapsed % (interval.work_seconds + interval.rest_seconds)

        if cycle_elapsed >= cycle_time:
            timer.is_work = not timer.is_work
            if not timer.is_work:
                timer.current_round += 1
            if timer.current_round >= interval.rounds:
                timer.status = TimerStatus.COMPLETED
                self._notify_complete(timer)

    # ── Preset Operations ─────────────────────────────────────────────

    def start_preset(self, preset_index: int) -> Optional[ActiveTimer]:
        """Start a countdown from a preset."""
        if 0 <= preset_index < len(self._presets):
            preset = self._presets[preset_index]
            timer = ActiveTimer(
                timer_id=f"t{self._next_id}",
                name=preset.name,
                mode=TimerMode.COUNTDOWN,
                target=float(preset.seconds),
            )
            self._next_id += 1
            self._timers.append(timer)
            self._active_timer_index = len(self._timers) - 1
            self.start()
            return timer
        return None

    def start_interval(self, interval_index: int) -> Optional[ActiveTimer]:
        """Start an interval timer."""
        if 0 <= interval_index < len(self._intervals):
            config = self._intervals[interval_index]
            timer = ActiveTimer(
                timer_id=f"t{self._next_id}",
                name=config.name,
                mode=TimerMode.INTERVAL,
                target=float(config.total_time),
                interval=config,
                is_work=True,
            )
            self._next_id += 1
            self._timers.append(timer)
            self._active_timer_index = len(self._timers) - 1
            self.start()
            return timer
        return None

    def add_custom_timer(self, seconds: int, name: str = "Custom") -> ActiveTimer:
        timer = ActiveTimer(
            timer_id=f"t{self._next_id}",
            name=name,
            mode=TimerMode.COUNTDOWN,
            target=float(seconds),
        )
        self._next_id += 1
        self._timers.append(timer)
        return timer

    def remove_timer(self, index: int) -> bool:
        if 0 <= index < len(self._timers) and len(self._timers) > 1:
            self._timers.pop(index)
            self._active_timer_index = min(self._active_timer_index, len(self._timers) - 1)
            return True
        return False

    @property
    def presets(self) -> List[TimerPreset]:
        return list(self._presets)

    @property
    def intervals(self) -> List[IntervalConfig]:
        return list(self._intervals)

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_complete(self, cb: Callable) -> None:
        self._on_complete.append(cb)

    def _notify_complete(self, timer: ActiveTimer) -> None:
        for cb in self._on_complete:
            try:
                cb(timer)
            except Exception:
                pass

    # ── Rendering ─────────────────────────────────────────────────────

    def render_main(self, width: int = 60) -> List[str]:
        lines = []
        timer = self.active_timer
        if not timer:
            return ["No active timer"]

        lines.append(f" ⏱️  {timer.name}")
        lines.append("─" * width)

        # Status and time
        lines.append(f" {timer.status_icon} {timer.status.value.title()}")
        lines.append("")
        lines.append(f" {timer.display_time}")

        # Progress bar (for countdown)
        if timer.mode == TimerMode.COUNTDOWN and timer.target > 0:
            pct = timer.progress
            bar_width = 40
            filled = int(pct * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            lines.append(f" [{bar}] {timer.progress_str}")

        # Interval info
        if timer.mode == TimerMode.INTERVAL and timer.interval:
            phase = "WORK" if timer.is_work else "REST"
            lines.append(f" Round {timer.current_round + 1}/{timer.interval.rounds} — {phase}")

        # Laps
        if timer.laps:
            lines.append("")
            lines.append(f" Laps ({len(timer.laps)}):")
            for lap in timer.laps[-5:]:  # Show last 5
                lines.append(f"  #{lap.lap_number}  Split: {lap.split_str}  Lap: {lap.lap_str}")

        lines.append("─" * width)

        # Controls
        if timer.status == TimerStatus.RUNNING:
            lines.append(" Space:Pause  L:Lap  R:Reset  T:Timer  P:Preset  I:Interval")
        elif timer.status == TimerStatus.PAUSED:
            lines.append(" Space:Resume  R:Reset  T:Timer  P:Preset")
        else:
            lines.append(" Space:Start  R:Reset  T:Timer  P:Preset  I:Interval")

        return lines

    def render_presets(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" ⏱️  Timer Presets")
        lines.append("─" * width)

        current_cat = ""
        for i, preset in enumerate(self._presets):
            if preset.category != current_cat:
                current_cat = preset.category
                lines.append(f"  ── {current_cat} ──")

            marker = "▸" if i == self._selected_index else " "
            lines.append(f" {marker} {preset.display}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Start  Esc:Back")
        return lines

    def render_intervals(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" ⏱️  Interval Timers")
        lines.append("─" * width)

        for i, interval in enumerate(self._intervals):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f" {marker} {interval.name}")
            lines.append(f"   {interval.work_seconds}s work / {interval.rest_seconds}s rest × {interval.rounds} rounds")
            lines.append(f"   Total: {interval.total_time}s ({interval.total_time // 60}m {interval.total_time % 60}s)")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Start  Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        if self._view_mode == "presets":
            return self.render_presets(width)
        elif self._view_mode == "intervals":
            return self.render_intervals(width)
        return self.render_main(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "presets":
            return self._handle_presets_key(key)
        elif self._view_mode == "intervals":
            return self._handle_intervals_key(key)
        return self._handle_main_key(key)

    def _handle_main_key(self, key: str) -> Optional[str]:
        if key == " ":
            self.toggle()
            return "toggle"
        elif key == "l":
            self.record_lap()
            return "lap"
        elif key == "r":
            self.reset()
            return "reset"
        elif key == "p":
            self._view_mode = "presets"
            self._selected_index = 0
            return "presets"
        elif key == "i":
            self._view_mode = "intervals"
            self._selected_index = 0
            return "intervals"
        elif key == "t":
            timer = self.add_custom_timer(60, "Custom 1m")
            self._active_timer_index = len(self._timers) - 1
            return "new_timer"
        return None

    def _handle_presets_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "main"
            return "back"
        elif key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(self._presets) - 1, self._selected_index + 1)
            return "select_down"
        elif key == "Enter":
            self.start_preset(self._selected_index)
            self._view_mode = "main"
            return "start_preset"
        return None

    def _handle_intervals_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "main"
            return "back"
        elif key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(self._intervals) - 1, self._selected_index + 1)
            return "select_down"
        elif key == "Enter":
            self.start_interval(self._selected_index)
            self._view_mode = "main"
            return "start_interval"
        return None
