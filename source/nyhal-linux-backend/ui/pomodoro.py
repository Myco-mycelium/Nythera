"""Pomodoro Timer — Session tracking, break reminders, and productivity stats.

Features:
- Configurable work/break durations
- Session counter with daily/weekly goals
- Break reminders (short and long)
- Productivity tracking and statistics
- Tag-based session categorization
- History log with completion rates
- Focus score calculation
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class PomodoroState(Enum):
    IDLE = "idle"
    WORKING = "working"
    SHORT_BREAK = "short_break"
    LONG_BREAK = "long_break"
    PAUSED = "paused"

    @property
    def icon(self) -> str:
        icons = {
            PomodoroState.IDLE: "⏹", PomodoroState.WORKING: "🍅",
            PomodoroState.SHORT_BREAK: "☕", PomodoroState.LONG_BREAK: "🌴",
            PomodoroState.PAUSED: "⏸",
        }
        return icons.get(self, "?")


class SessionTag(Enum):
    CODING = "coding"
    WRITING = "writing"
    READING = "reading"
    STUDYING = "studying"
    DESIGN = "design"
    PLANNING = "planning"
    MEETING = "meeting"
    BREAK = "break"
    OTHER = "other"

    @property
    def icon(self) -> str:
        icons = {
            SessionTag.CODING: "💻", SessionTag.WRITING: "✍️",
            SessionTag.READING: "📖", SessionTag.STUDYING: "📚",
            SessionTag.DESIGN: "🎨", SessionTag.PLANNING: "📋",
            SessionTag.MEETING: "👥", SessionTag.BREAK: "☕",
            SessionTag.OTHER: "📌",
        }
        return icons.get(self, "?")


@dataclass
class PomodoroConfig:
    work_minutes: int = 25
    short_break_minutes: int = 5
    long_break_minutes: int = 15
    long_break_interval: int = 4
    auto_start_breaks: bool = True
    auto_start_work: bool = False
    sound_enabled: bool = True
    notification_enabled: bool = True

    @property
    def work_seconds(self) -> int:
        return self.work_minutes * 60

    @property
    def short_break_seconds(self) -> int:
        return self.short_break_minutes * 60

    @property
    def long_break_seconds(self) -> int:
        return self.long_break_minutes * 60


@dataclass
class PomodoroSession:
    id: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    duration_s: int = 0
    planned_s: int = 0
    completed: bool = True
    tag: SessionTag = SessionTag.OTHER
    title: str = ""
    interruptions: int = 0
    notes: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M", time.localtime(self.start_time))

    @property
    def date_str(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.start_time))

    @property
    def completion_pct(self) -> float:
        if self.planned_s == 0:
            return 0.0
        return min(100, self.duration_s / self.planned_s * 100)

    @property
    def duration_str(self) -> str:
        mins = self.duration_s // 60
        secs = self.duration_s % 60
        return f"{mins}:{secs:02d}"

    @property
    def completed_icon(self) -> str:
        return "✅" if self.completed else "⚠️"


@dataclass
class DailyStats:
    date: str = ""
    sessions_completed: int = 0
    total_work_minutes: int = 0
    total_break_minutes: int = 0
    tags: Dict[str, int] = field(default_factory=dict)
    focus_score: float = 0.0
    goal_met: bool = False

    @property
    def work_hours_str(self) -> str:
        h = self.total_work_minutes // 60
        m = self.total_work_minutes % 60
        return f"{h}h {m}m"

    @property
    def focus_bar(self) -> str:
        filled = int(self.focus_score / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def goal_icon(self) -> str:
        return "🎯" if self.goal_met else "○"


class PomodoroTimer:
    def __init__(self):
        self._config = PomodoroConfig()
        self._sessions: List[PomodoroSession] = []
        self._state: PomodoroState = PomodoroState.IDLE
        self._current_tag: SessionTag = SessionTag.CODING
        self._current_title: str = ""
        self._remaining_s: int = 0
        self._session_count: int = 0
        self._daily_goal: int = 8
        self._selected_session: int = 0
        self._view_mode: str = "timer"  # timer, history, stats, settings
        self._daily_stats: List[DailyStats] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Sessions
        for i in range(20):
            ts = now - random.uniform(0, 86400 * 14)
            tag = random.choice(list(SessionTag))
            planned = self._config.work_seconds
            duration = int(planned * random.uniform(0.7, 1.0))
            self._sessions.append(PomodoroSession(
                id=i + 1, start_time=ts, end_time=ts + duration,
                duration_s=duration, planned_s=planned,
                completed=random.random() > 0.15,
                tag=tag,
                title=random.choice(["Nyrqis compositor", "Shell UI modules", "Documentation", "Code review",
                                     "GPU testing", "Bug fixes", "Architecture planning", "Security audit"]),
                interruptions=random.randint(0, 3),
            ))
        self._sessions.sort(key=lambda s: s.start_time, reverse=True)

        # Daily stats
        for day in range(14):
            day_ts = now - day * 86400
            date_str = time.strftime("%Y-%m-%d", time.localtime(day_ts))
            day_sessions = [s for s in self._sessions
                            if time.strftime("%Y-%m-%d", time.localtime(s.start_time)) == date_str]
            completed = sum(1 for s in day_sessions if s.completed)
            total_min = sum(s.duration_s for s in day_sessions) // 60

            tags = {}
            for s in day_sessions:
                tags[s.tag.value] = tags.get(s.tag.value, 0) + 1

            self._daily_stats.append(DailyStats(
                date=date_str,
                sessions_completed=completed,
                total_work_minutes=total_min,
                total_break_minutes=total_min // 5,
                tags=tags,
                focus_score=random.uniform(50, 95),
                goal_met=completed >= self._daily_goal,
            ))

    @property
    def total_sessions(self) -> int:
        return len([s for s in self._sessions if s.completed])

    @property
    def total_focus_hours(self) -> float:
        return sum(s.duration_s for s in self._sessions if s.completed) / 3600

    @property
    def avg_focus_score(self) -> float:
        if not self._daily_stats:
            return 0.0
        return sum(d.focus_score for d in self._daily_stats) / len(self._daily_stats)

    @property
    def today_sessions(self) -> int:
        today = time.strftime("%Y-%m-%d")
        return sum(1 for s in self._sessions
                   if time.strftime("%Y-%m-%d", time.localtime(s.start_time)) == today and s.completed)

    @property
    def remaining_str(self) -> str:
        mins = self._remaining_s // 60
        secs = self._remaining_s % 60
        return f"{mins:02d}:{secs:02d}"

    @property
    def progress_pct(self) -> float:
        if self._state == PomodoroState.IDLE:
            return 0.0
        total = {
            PomodoroState.WORKING: self._config.work_seconds,
            PomodoroState.SHORT_BREAK: self._config.short_break_seconds,
            PomodoroState.LONG_BREAK: self._config.long_break_seconds,
        }.get(self._state, 1)
        elapsed = total - self._remaining_s
        return min(100, elapsed / total * 100)

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress_pct / 5)
        return "█" * filled + "░" * (20 - filled)

    def set_view(self, mode: str):
        if mode in ("timer", "history", "stats", "settings"):
            self._view_mode = mode

    def select_session(self, idx: int):
        if 0 <= idx < len(self._sessions):
            self._selected_session = idx

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS POMODORO TIMER                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        state_icon = self._state.icon
        lines.append(f"  {state_icon} {self._state.value.replace('_', ' ').title()}  ⏱ {self.remaining_str}  🍅 {self.today_sessions}/{self._daily_goal} today  📊 {self.total_sessions} total  ⏰ {self.total_focus_hours:.0f}h focused  🎯 Focus: {self.avg_focus_score:.0f}%")
        lines.append("")

        if self._view_mode == "timer":
            # Timer display
            lines.append(f"  ── Timer ──")
            lines.append(f"  [{self.progress_bar}] {self.progress_pct:.0f}%")
            lines.append(f"  ⏱ {self.remaining_str}")
            lines.append(f"  🏷 {self._current_tag.icon} {self._current_tag.value}")
            if self._current_title:
                lines.append(f"  📝 {self._current_title}")
            lines.append("")

            # Session counts
            lines.append(f"  🍅 Work: {self._config.work_minutes}min  ☕ Short: {self._config.short_break_minutes}min  🌴 Long: {self._config.long_break_minutes}min  🔄 After: {self._config.long_break_interval}")
            lines.append(f"  Today: {self.today_sessions} sessions  Goal: {self._daily_goal}")

        elif self._view_mode == "history":
            lines.append("  ── Session History ──")
            for i, s in enumerate(self._sessions[:15]):
                sel = "▶" if i == self._selected_session else " "
                tag = s.tag.icon
                completed = s.completed_icon
                lines.append(f"  {sel}{completed} {s.time_str} {tag} {s.title:<24s} {s.duration_str}  interruptions:{s.interruptions}")

        elif self._view_mode == "stats":
            lines.append("  ── Daily Stats ──")
            for ds in self._daily_stats[:7]:
                lines.append(f"  {ds.goal_icon} {ds.date}  🍅 {ds.sessions_completed}/{self._daily_goal}  ⏰ {ds.work_hours_str}  🎯 [{ds.focus_bar}] {ds.focus_score:.0f}%")

            lines.append("")
            lines.append("  ── Tag Distribution ──")
            tag_totals = {}
            for s in self._sessions:
                tag_totals[s.tag.value] = tag_totals.get(s.tag.value, 0) + 1
            for tag, count in sorted(tag_totals.items(), key=lambda x: -x[1])[:6]:
                tag_enum = SessionTag(tag)
                bar = "█" * min(20, count)
                lines.append(f"  {tag_enum.icon} {tag:<12s} [{bar}] {count}")

        elif self._view_mode == "settings":
            lines.append("  ── Timer Settings ──")
            c = self._config
            lines.append(f"  Work Duration:    {c.work_minutes} min")
            lines.append(f"  Short Break:      {c.short_break_minutes} min")
            lines.append(f"  Long Break:       {c.long_break_minutes} min")
            lines.append(f"  Long Break After: {c.long_break_interval} sessions")
            lines.append(f"  Auto-start Breaks: {'✓' if c.auto_start_breaks else '✗'}")
            lines.append(f"  Auto-start Work:  {'✓' if c.auto_start_work else '✗'}")
            lines.append(f"  Sound:            {'✓' if c.sound_enabled else '✗'}")
            lines.append(f"  Notifications:    {'✓' if c.notification_enabled else '✗'}")

        lines.append("")
        lines.append("  [T]imer [H]istory [S]tats [G]Settings [S]tart [P]ause [S]top [↑↓]Nav")
        return lines
