from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import calendar


class ViewMode(Enum):
    MONTH = "month"
    WEEK = "week"
    DAY = "day"
    YEAR = "year"
    AGENDA = "agenda"


class Recurrence(Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    CUSTOM = "custom"


class EventColor(Enum):
    BLUE = "blue"
    RED = "red"
    GREEN = "green"
    PURPLE = "purple"
    ORANGE = "orange"
    TEAL = "teal"
    PINK = "pink"


class Reminder(Enum):
    NONE = "none"
    AT_TIME = "at-time"
    MIN_5 = "5-min"
    MIN_10 = "10-min"
    MIN_15 = "15-min"
    MIN_30 = "30-min"
    HOUR_1 = "1-hour"
    DAY_1 = "1-day"


class EventStatus(Enum):
    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    CANCELLED = "cancelled"


@dataclass
class CalendarEvent:
    title: str
    start_hour: int
    start_min: int
    duration_mins: int
    day: int
    month: int
    year: int
    description: str = ""
    location: str = ""
    color: EventColor = EventColor.BLUE
    recurrence: Recurrence = Recurrence.NONE
    reminder: Reminder = Reminder.MIN_15
    status: EventStatus = EventStatus.CONFIRMED
    attendees: list = field(default_factory=list)
    all_day: bool = False
    calendar_name: str = "Personal"
    is_busy: bool = True
    event_id: str = ""

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"{self.title[:8]}-{self.day}{self.month}"

    @property
    def time_display(self) -> str:
        if self.all_day:
            return "All Day"
        return f"{self.start_hour:02d}:{self.start_min:02d}"

    @property
    def end_time_display(self) -> str:
        end_min = self.start_hour * 60 + self.start_min + self.duration_mins
        h, m = divmod(end_min, 60)
        return f"{h:02d}:{m:02d}"

    @property
    def date_display(self) -> str:
        return f"{self.year}-{self.month:02d}-{self.day:02d}"


@dataclass
class ReminderEntry:
    event: CalendarEvent
    trigger_time: float
    fired: bool = False


class CalendarApp:
    def __init__(self):
        self._events: list[CalendarEvent] = []
        self._selected_event: int = 0
        self._view_mode: ViewMode = ViewMode.MONTH
        self._current_year: int = 2026
        self._current_month: int = 9
        self._current_day: int = 3
        self._current_week: int = 36
        self._selected_hour: int = 9
        self._calendars: dict[str, bool] = {"Personal": True, "Work": True, "Family": True, "Birthdays": True, "Holidays": True}
        self._search_query: str = ""
        self._search_results: list = []
        self._reminders: list[ReminderEntry] = []
        self._show_busy: bool = True
        self._view: str = "calendar"
        self._create_samples()

    def _create_samples(self):
        self._events = [
            CalendarEvent("Nyrqis Sprint Review", 10, 0, 60, 3, 9, 2026, "Review Q3 progress", "Conference Room A", EventColor.BLUE, Recurrence.NONE, Reminder.MIN_15, EventStatus.CONFIRMED, ["alice@nyrqis.dev", "bob@nyrqis.dev"]),
            CalendarEvent("Lunch with Team", 12, 30, 60, 3, 9, 2026, "Team lunch at Cafe Mosaic", "Cafe Mosaic, Downtown", EventColor.GREEN),
            CalendarEvent("Compositor Demo", 14, 0, 90, 3, 9, 2026, "Demo Wayland compositor to stakeholders", "Demo Lab", EventColor.PURPLE, Reminder.HOUR_1),
            CalendarEvent("Yoga Class", 7, 0, 60, 4, 9, 2026, "", "Studio B", EventColor.TEAL, Recurrence.WEEKLY),
            CalendarEvent("Code Review", 11, 0, 45, 5, 9, 2026, "Review PRs for nyrqis-core", "", EventColor.ORANGE, Recurrence.DAILY),
            CalendarEvent("Sprint Planning", 9, 0, 120, 8, 9, 2026, "Plan next sprint", "Conference Room B", EventColor.BLUE, Recurrence.NONE, Reminder.HOUR_1),
            CalendarEvent("Birthday Party", 18, 0, 180, 15, 9, 2026, "Alice's birthday", "Park Avenue", EventColor.PINK, Reminder.DAY_1),
            CalendarEvent("Dentist", 15, 0, 45, 20, 9, 2026, "", "Dr. Smith Clinic", EventColor.RED, Reminder.HOUR_1),
            CalendarEvent("Conference Talk", 14, 0, 120, 25, 9, 2026, "Nyrqis OS: Building the Future", "Convention Center", EventColor.PURPLE, EventStatus.CONFIRMED),
            CalendarEvent("Flight to SF", 8, 0, 300, 1, 10, 2026, "Flight AA1234", "SFO Airport", EventColor.RED, all_day=False),
            CalendarEvent("Monthly All-Hands", 16, 0, 60, 1, 10, 2026, "Company-wide sync", "Main Auditorium", EventColor.BLUE, Recurrence.MONTHLY),
            CalendarEvent("1:1 with Manager", 10, 0, 30, 6, 9, 2026, "Weekly check-in", "", EventColor.TEAL, Recurrence.WEEKLY),
        ]
        self._reminders = [
            ReminderEntry(self._events[0], time.time() + 900),
            ReminderEntry(self._events[2], time.time() + 3600),
        ]

    @property
    def selected_event(self) -> Optional[CalendarEvent]:
        if 0 <= self._selected_event < len(self._events):
            return self._events[self._selected_event]
        return None

    @property
    def total_events(self) -> int:
        return len(self._events)

    @property
    def events_this_month(self) -> int:
        return sum(1 for e in self._events if e.month == self._current_month and e.year == self._current_year)

    @property
    def upcoming_events(self) -> list:
        return sorted(self._events, key=lambda e: (e.year, e.month, e.day, e.start_hour))[:5]

    @property
    def busy_days_this_month(self) -> set:
        return {e.day for e in self._events if e.month == self._current_month and e.year == self._current_year}

    @property
    def month_name(self) -> str:
        return calendar.month_name[self._current_month]

    @property
    def active_calendars(self) -> list:
        return [name for name, active in self._calendars.items() if active]

    def select_event(self, idx: int):
        if 0 <= idx < len(self._events):
            self._selected_event = idx

    def add_event(self, event: CalendarEvent):
        self._events.append(event)
        self._selected_event = len(self._events) - 1

    def delete_event(self, idx: int) -> bool:
        if 0 <= idx < len(self._events):
            self._events.pop(idx)
            if self._selected_event >= len(self._events):
                self._selected_event = max(0, len(self._events) - 1)
            return True
        return False

    def toggle_calendar(self, name: str):
        if name in self._calendars:
            self._calendars[name] = not self._calendars[name]

    def search(self, query: str) -> list:
        self._search_query = query
        self._search_results = [e for e in self._events if query.lower() in e.title.lower() or query.lower() in e.description.lower()]
        return self._search_results

    def get_day_events(self, day: int) -> list:
        return [e for e in self._events if e.day == day and e.month == self._current_month and e.year == self._current_year]

    def set_view(self, view: ViewMode):
        self._view_mode = view

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                        NYRQIS CALENDAR                                     ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  View: {self._view_mode.value}  Date: {self.month_name} {self._current_day}, {self._current_year}")
        lines.append(f"  Events this month: {self.events_this_month}  Total: {self.total_events}")
        lines.append("")
        if self._view_mode == ViewMode.MONTH:
            lines.extend(self._render_month())
        elif self._view_mode == ViewMode.WEEK:
            lines.extend(self._render_week())
        elif self._view_mode == ViewMode.DAY:
            lines.extend(self._render_day())
        elif self._view_mode == ViewMode.AGENDA:
            lines.extend(self._render_agenda())
        lines.append("")
        lines.append("  [V]iew  [N]ew event  [E]dit  [D]elete  [S]earch  [/]filter")
        return lines

    def _render_month(self) -> list:
        lines = []
        lines.append("  ── September 2026 ──")
        lines.append("  Mon  Tue  Wed  Thu  Fri  Sat  Sun")
        busy = self.busy_days_this_month
        cal = calendar.monthcalendar(self._current_year, self._current_month)
        for week in cal:
            row = "  "
            for day in week:
                if day == 0:
                    row += "    "
                elif day == self._current_day:
                    marker = "●" if day in busy else "·"
                    row += f"[{day:2d}]{marker}"[:5]
                elif day in busy:
                    row += f" {day:2d} ●"[:5]
                else:
                    row += f" {day:2d}  "[:5]
            lines.append(row)
        return lines

    def _render_week(self) -> list:
        lines = []
        lines.append("  ── Week 37 ──")
        for hour in range(8, 20):
            events = [e for e in self._events if e.day == self._current_day and e.start_hour == hour]
            ev_str = ", ".join(e.title for e in events) or ""
            lines.append(f"  {hour:02d}:00  {'│' + ev_str if ev_str else '│'}")
        return lines

    def _render_day(self) -> list:
        lines = []
        lines.append(f"  ── {self.month_name} {self._current_day} ──")
        day_events = self.get_day_events(self._current_day)
        for e in day_events:
            color_icons = {"blue": "🔵", "red": "🔴", "green": "🟢", "purple": "🟣", "orange": "🟠", "teal": "🔹", "pink": "💗"}
            icon = color_icons.get(e.color.value, "⚪")
            lines.append(f"  {icon} {e.time_display} - {e.end_time_display}  {e.title}")
            if e.location:
                lines.append(f"    📍 {e.location}")
        if not day_events:
            lines.append("  No events today")
        return lines

    def _render_agenda(self) -> list:
        lines = []
        lines.append("  ── Upcoming Events ──")
        for e in self.upcoming_events:
            color_icons = {"blue": "🔵", "red": "🔴", "green": "🟢", "purple": "🟣", "orange": "🟠", "teal": "🔹", "pink": "💗"}
            icon = color_icons.get(e.color.value, "⚪")
            lines.append(f"  {icon} {e.date_display} {e.time_display}  {e.title}")
            if e.location:
                lines.append(f"    📍 {e.location}")
        return lines

    def render_event_detail(self) -> list:
        e = self.selected_event
        if not e:
            return ["  No event selected"]
        lines = []
        lines.append(f"  ── {e.title} ──")
        lines.append(f"  Date: {e.date_display}")
        lines.append(f"  Time: {e.time_display} - {e.end_time_display} ({e.duration_mins} min)")
        lines.append(f"  Location: {e.location or 'None'}")
        lines.append(f"  Description: {e.description or 'None'}")
        lines.append(f"  Color: {e.color.value}")
        lines.append(f"  Recurrence: {e.recurrence.value}")
        lines.append(f"  Reminder: {e.reminder.value}")
        lines.append(f"  Status: {e.status.value}")
        if e.attendees:
            lines.append(f"  Attendees: {', '.join(e.attendees)}")
        lines.append(f"  Calendar: {e.calendar_name}")
        return lines

    def render_calendars(self) -> list:
        lines = []
        lines.append("  ── Calendars ──")
        for name, active in self._calendars.items():
            status = "✅" if active else "⬜"
            count = sum(1 for e in self._events if e.calendar_name == name or (name == "Personal" and e.calendar_name == "Personal"))
            lines.append(f"  {status} {name} ({count})")
        return lines
