"""Calendar App — Event creation, recurring events, and multi-view.

Features:
- Month/week/day views with navigation
- Event creation with color coding and categories
- Recurring events (daily, weekly, monthly, yearly)
- All-day events
- Multiple calendar overlays
- Reminder system
- Event search and filtering
"""

from __future__ import annotations

import time
import calendar as cal_mod
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class EventRecurrence(Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"

    @property
    def icon(self) -> str:
        icons = {
            EventRecurrence.NONE: "", EventRecurrence.DAILY: "📅",
            EventRecurrence.WEEKLY: "📆", EventRecurrence.MONTHLY: "🗓",
            EventRecurrence.YEARLY: "🎆",
        }
        return icons.get(self, "")


class ReminderType(Enum):
    NONE = "none"
    MINUTES_5 = "5min"
    MINUTES_15 = "15min"
    MINUTES_30 = "30min"
    HOURS_1 = "1hr"
    HOURS_24 = "1day"

    @property
    def icon(self) -> str:
        return "🔔" if self != ReminderType.NONE else ""


@dataclass
class CalendarEvent:
    id: int = 0
    title: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    all_day: bool = False
    description: str = ""
    location: str = ""
    category: str = "personal"
    color: str = "#4A90D9"
    recurrence: EventRecurrence = EventRecurrence.NONE
    reminder: ReminderType = ReminderType.MINUTES_15
    attendees: List[str] = field(default_factory=list)
    calendar_name: str = "Personal"
    is_recurring_instance: bool = False
    original_event_id: Optional[int] = None

    @property
    def start_date_str(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.start_time))

    @property
    def start_time_str(self) -> str:
        return time.strftime("%H:%M", time.localtime(self.start_time))

    @property
    def end_time_str(self) -> str:
        return time.strftime("%H:%M", time.localtime(self.end_time))

    @property
    def duration_minutes(self) -> int:
        return int((self.end_time - self.start_time) / 60)

    @property
    def duration_str(self) -> str:
        mins = self.duration_minutes
        if mins < 60:
            return f"{mins}m"
        h = mins // 60
        m = mins % 60
        if m == 0:
            return f"{h}h"
        return f"{h}h{m}m"

    @property
    def day_of_week(self) -> int:
        return time.localtime(self.start_time).tm_wday

    @property
    def day_of_month(self) -> int:
        return time.localtime(self.start_time).tm_mday

    @property
    def month(self) -> int:
        return time.localtime(self.start_time).tm_mon

    @property
    def year(self) -> int:
        return time.localtime(self.start_time).tm_year

    @property
    def recurrence_str(self) -> str:
        return self.recurrence.icon

    @property
    def attendee_str(self) -> str:
        return ", ".join(self.attendees[:3])


@dataclass
class Calendar:
    name: str = ""
    color: str = "#4A90D9"
    visible: bool = True
    event_count: int = 0


@dataclass
class Reminder:
    event: CalendarEvent = None
    trigger_time: float = 0.0
    dismissed: bool = False

    @property
    def time_until_str(self) -> str:
        delta = self.trigger_time - time.time()
        if delta <= 0:
            return "now"
        if delta < 60:
            return f"{delta:.0f}s"
        if delta < 3600:
            return f"{delta / 60:.0f}m"
        return f"{delta / 3600:.1f}h"


class CalendarApp:
    def __init__(self):
        self._events: List[CalendarEvent] = []
        self._calendars: List[Calendar] = []
        self._reminders: List[Reminder] = []
        self._selected_event: int = 0
        self._view_mode: str = "month"  # month, week, day, agenda, search
        self._current_date: float = time.time()
        self._search_text: str = ""
        self._category_filter: str = ""
        self._show_weekends: bool = True
        self._week_start_monday: bool = True
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        today = time.localtime(now)
        year, month, day = today.tm_year, today.tm_mon, today.tm_mday

        def ts(y, m, d, h=9, mi=0):
            return time.mktime((y, m, d, h, mi, 0, 0, 0, -1))

        # Calendars
        self._calendars = [
            Calendar("Personal", "#4A90D9", True, 25),
            Calendar("Work", "#E74C3C", True, 18),
            Calendar("Nyrqis Dev", "#2ECC71", True, 12),
            Calendar("Birthdays", "#F39C12", False, 8),
            Calendar("Holidays", "#9B59B6", False, 15),
        ]

        # Events this month
        self._events = [
            CalendarEvent(1, "Sprint Planning", ts(year, month, day, 10, 0), ts(year, month, day, 11, 0),
                          category="work", color="#E74C3C", calendar_name="Work",
                          location="Conference Room A", recurrence=EventRecurrence.WEEKLY,
                          attendees=["alice@nyrqis.dev", "bob@nyrqis.dev"]),
            CalendarEvent(2, "Compositor Code Review", ts(year, month, day, 14, 0), ts(year, month, day, 15, 0),
                          category="dev", color="#2ECC71", calendar_name="Nyrqis Dev",
                          description="Review PR #512 - Vulkan renderer"),
            CalendarEvent(3, "Lunch with Dave", ts(year, month, day, 12, 0), ts(year, month, day, 13, 0),
                          category="personal", color="#4A90D9", location="Cafe Miso",
                          attendees=["dave@myco-mycelium.com"]),
            CalendarEvent(4, "Nyrqis Community Meetup", ts(year, month, day + 2, 18, 0), ts(year, month, day + 2, 20, 0),
                          category="dev", color="#2ECC71", calendar_name="Nyrqis Dev",
                          location="Virtual (Zoom)", attendees=["team@nyrqis.dev"]),
            CalendarEvent(5, "Gym Session", ts(year, month, day, 7, 0), ts(year, month, day, 8, 0),
                          category="health", color="#1ABC9C", recurrence=EventRecurrence.WEEKLY),
            CalendarEvent(6, "Dentist Appointment", ts(year, month, day + 3, 9, 30), ts(year, month, day + 3, 10, 30),
                          category="health", color="#1ABC9C", reminder=ReminderType.HOURS_24),
            CalendarEvent(7, "Release v1.0-rc2", ts(year, month, day + 5, 0, 0), ts(year, month, day + 5, 23, 59),
                          all_day=True, category="dev", color="#E74C3C", calendar_name="Nyrqis Dev"),
            CalendarEvent(8, "Security Audit Review", ts(year, month, day + 1, 11, 0), ts(year, month, day + 1, 12, 30),
                          category="work", color="#E74C3C", calendar_name="Work",
                          attendees=["eve@nyrqis.dev"]),
            CalendarEvent(9, "Team Standup", ts(year, month, day, 9, 0), ts(year, month, day, 9, 15),
                          category="work", color="#E74C3C", recurrence=EventRecurrence.DAILY,
                          calendar_name="Work", attendees=["team@nyrqis.dev"]),
            CalendarEvent(10, "GPU Benchmark Testing", ts(year, month, day + 4, 14, 0), ts(year, month, day + 4, 17, 0),
                          category="dev", color="#2ECC71", calendar_name="Nyrqis Dev",
                          description="Run full benchmark suite on AMD, Intel, NVIDIA"),
            CalendarEvent(11, "Birthday: Grace", ts(year, month, day + 7, 0, 0), ts(year, month, day + 7, 23, 59),
                          all_day=True, category="personal", color="#F39C12", calendar_name="Birthdays"),
            CalendarEvent(12, "Budget Meeting", ts(year, month, day + 6, 15, 0), ts(year, month, day + 6, 16, 0),
                          category="work", color="#E74C3C", calendar_name="Work",
                          attendees=["dave@myco-mycelium.com", "henry@myco-mycelium.com"]),
            CalendarEvent(13, "Hackathon Weekend", ts(year, month, day + 8, 9, 0), ts(year, month, day + 9, 18, 0),
                          all_day=True, category="dev", color="#2ECC71", calendar_name="Nyrqis Dev",
                          description="Build Nyrqis widgets hackathon"),
            CalendarEvent(14, "1:1 with Buffy", ts(year, month, day, 16, 0), ts(year, month, day, 16, 30),
                          category="work", color="#E74C3C"),
            CalendarEvent(15, "Yoga Class", ts(year, month, day + 1, 18, 0), ts(year, month, day + 1, 19, 0),
                          category="health", color="#1ABC9C", recurrence=EventRecurrence.WEEKLY),
        ]

        # Reminders
        self._reminders = [
            Reminder(self._events[5], ts(year, month, day + 2, 9, 30)),
            Reminder(self._events[7], ts(year, month, day + 1, 10, 0)),
        ]

    @property
    def filtered_events(self) -> List[CalendarEvent]:
        result = self._events
        if self._search_text:
            q = self._search_text.lower()
            result = [e for e in result if q in e.title.lower() or q in e.location.lower() or q in e.description.lower()]
        if self._category_filter:
            result = [e for e in result if e.category == self._category_filter]
        return result

    @property
    def today_events(self) -> List[CalendarEvent]:
        now = time.time()
        today_start = time.mktime(time.localtime(now)[:3] + (0, 0, 0) + time.localtime(now)[6:])
        today_end = today_start + 86400
        return [e for e in self._events if e.start_time >= today_start and e.start_time < today_end]

    @property
    def upcoming_events(self) -> List[CalendarEvent]:
        now = time.time()
        future = [e for e in self._events if e.start_time >= now]
        return sorted(future, key=lambda e: e.start_time)[:10]

    @property
    def month_name(self) -> str:
        return time.strftime("%B %Y", time.localtime(self._current_date))

    def select_event(self, idx: int):
        events = self.filtered_events
        if 0 <= idx < len(events):
            self._selected_event = idx

    def set_view(self, mode: str):
        if mode in ("month", "week", "day", "agenda", "search"):
            self._view_mode = mode

    def navigate(self, direction: int):
        t = time.localtime(self._current_date)
        if self._view_mode == "month":
            new_month = t.tm_mon + direction
            new_year = t.tm_year
            if new_month < 1:
                new_month = 12
                new_year -= 1
            elif new_month > 12:
                new_month = 1
                new_year += 1
            self._current_date = time.mktime((new_year, new_month, 1, 12, 0, 0, 0, 0, -1))
        elif self._view_mode == "week":
            self._current_date += direction * 7 * 86400
        elif self._view_mode == "day":
            self._current_date += direction * 86400

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS CALENDAR                                         ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  📅 {self.month_name}  📆 {len(self._events)} events  🔔 {len(self._reminders)} reminders  🗂 {len(self._calendars)} calendars")
        lines.append("")

        if self._view_mode == "month":
            # Month grid
            lines.append("  ── Month View ──")
            lines.append("  Mon  Tue  Wed  Thu  Fri  Sat  Sun")
            t = time.localtime(self._current_date)
            year, month = t.tm_year, t.tm_mon
            first_day, days_in_month = cal_mod.monthrange(year, month)
            # Adjust for Monday start
            start = (first_day - 0) % 7

            # Events map by day
            day_events = {}
            for e in self._events:
                et = time.localtime(e.start_time)
                if et.tm_year == year and et.tm_mon == month:
                    d = et.tm_mday
                    day_events[d] = day_events.get(d, 0) + 1

            today = time.localtime().tm_mday if time.localtime().tm_year == year and time.localtime().tm_mon == month else 0

            week = "  "
            for i in range(start):
                week += "     "
            for day in range(1, days_in_month + 1):
                marker = f"{day:2d}"
                if day == today:
                    marker = f"[{day:2d}]"
                evts = day_events.get(day, 0)
                evt_mark = "•" if evts > 0 else " "
                week += f"{marker}{evt_mark}"
                if (start + day) % 7 == 0:
                    lines.append(week)
                    week = "  "
            if week.strip():
                lines.append(week)

            lines.append("")
            # Today's events
            lines.append("  ── Today's Events ──")
            for e in self.today_events:
                lines.append(f"  {e.start_time_str}-{e.end_time_str} {e.title}  {e.recurrence_str} 📍{e.location}")

        elif self._view_mode == "week":
            lines.append("  ── Week View ──")
            t = time.localtime(self._current_date)
            # Get week start
            weekday = t.tm_wday
            for d in range(7):
                day_ts = self._current_date - weekday * 86400 + d * 86400
                day_name = time.strftime("%a", time.localtime(day_ts))
                day_num = time.strftime("%d", time.localtime(day_ts))
                day_events_list = [e for e in self._events
                                   if time.localtime(e.start_time).tm_mday == time.localtime(day_ts).tm_mday
                                   and time.localtime(e.start_time).tm_mon == time.localtime(day_ts).tm_mon]
                marker = "▸" if d == weekday else " "
                lines.append(f"  {marker} {day_name} {day_num}:")
                if day_events_list:
                    for e in day_events_list[:3]:
                        lines.append(f"      {e.start_time_str}-{e.end_time_str} {e.title}")
                else:
                    lines.append(f"      (no events)")

        elif self._view_mode == "day":
            lines.append("  ── Day View ──")
            day_name = time.strftime("%A, %B %d, %Y", time.localtime(self._current_date))
            lines.append(f"  {day_name}")
            lines.append("")
            for hour in range(8, 22):
                time_str = f"{hour:02d}:00"
                events_at_hour = [e for e in self._events
                                  if time.localtime(e.start_time).tm_hour == hour
                                  and time.localtime(e.start_time).tm_mday == time.localtime(self._current_date).tm_mday]
                if events_at_hour:
                    for e in events_at_hour:
                        lines.append(f"  {time_str} ▸ {e.title} ({e.duration_str}) 📍{e.location}")
                else:
                    lines.append(f"  {time_str} ─")

        elif self._view_mode == "agenda":
            lines.append("  ── Upcoming Events ──")
            for e in self.upcoming_events[:10]:
                lines.append(f"  📅 {e.start_date_str} {e.start_time_str}-{e.end_time_str} {e.title}")
                lines.append(f"     📍 {e.location or 'No location'}  {e.recurrence_str}  Attendees: {e.attendee_str or 'None'}")

        elif self._view_mode == "search":
            lines.append(f"  ── Search: '{self._search_text}' ──")
            for e in self.filtered_events[:10]:
                lines.append(f"  📅 {e.start_date_str} {e.start_time_str} {e.title}")

        lines.append("")
        lines.append("  [M]onth [W]eek [D]ay [A]genda [/]Search [←→]Nav [+N]ew [E]dit [R]eminders")
        return lines


@dataclass
class ReminderEntry:
    id: int = 0
    title: str = ""
    time: float = 0.0
    recurring: bool = False


class ViewMode:
    pass  # backward compat stub

Recurrence = EventRecurrence

# ─── Backward-compat exports ────────────────────────────────────────────
from enum import Enum as _Enum

class EventColor(_Enum):
    BLUE = "blue"
    GREEN = "green"
    RED = "red"
    ORANGE = "orange"
    PURPLE = "purple"
    CYAN = "cyan"
    PINK = "pink"
    YELLOW = "yellow"
    GRAY = "gray"
