"""
Nyrqis Calendar — event scheduling with week/month views and reminders.

Features:
- Month, week, and day views
- Create/edit/delete events with time, location, notes
- Recurring events (daily, weekly, monthly, yearly)
- Reminders with alert system
- Color-coded event categories
- Event search and filtering
- Today highlighting and navigation
- Multi-day event spanning
- Export events
"""

import re
import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime, timedelta


# ─── Data Classes ────────────────────────────────────────────────────────


class EventCategory(Enum):
    WORK = "work"
    PERSONAL = "personal"
    HEALTH = "health"
    SOCIAL = "social"
    TRAVEL = "travel"
    OTHER = "other"


class Recurrence(Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class ViewMode(Enum):
    MONTH = "month"
    WEEK = "week"
    DAY = "day"


CATEGORY_COLORS = {
    EventCategory.WORK: "#4A90D9",
    EventCategory.PERSONAL: "#7B68EE",
    EventCategory.HEALTH: "#2ECC71",
    EventCategory.SOCIAL: "#E74C3C",
    EventCategory.TRAVEL: "#F39C12",
    EventCategory.OTHER: "#95A5A6",
}


@dataclass
class CalendarEvent:
    """A calendar event."""
    title: str
    start_time: float  # Unix timestamp
    end_time: float
    all_day: bool = False
    location: str = ""
    notes: str = ""
    category: EventCategory = EventCategory.PERSONAL
    recurrence: Recurrence = Recurrence.NONE
    reminder_minutes: int = 15  # minutes before
    attendees: List[str] = field(default_factory=list)
    event_id: str = ""
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.event_id:
            self.event_id = hashlib.md5(
                f"{self.title}{self.start_time}".encode()
            ).hexdigest()[:8]

    @property
    def start_date(self) -> datetime:
        return datetime.fromtimestamp(self.start_time)

    @property
    def end_date(self) -> datetime:
        return datetime.fromtimestamp(self.end_time)

    @property
    def duration_hours(self) -> float:
        return (self.end_time - self.start_time) / 3600

    @property
    def duration_str(self) -> str:
        hours = self.duration_hours
        if hours < 1:
            return f"{int(hours * 60)}m"
        h = int(hours)
        m = int((hours - h) * 60)
        return f"{h}h {m}m" if m else f"{h}h"

    @property
    def time_range(self) -> str:
        if self.all_day:
            return "All day"
        start = self.start_date.strftime("%I:%M %p")
        end = self.end_date.strftime("%I:%M %p")
        return f"{start} – {end}"

    @property
    def date_str(self) -> str:
        return self.start_date.strftime("%Y-%m-%d")

    @property
    def day_str(self) -> str:
        return self.start_date.strftime("%a %b %d")

    @property
    def color(self) -> str:
        return CATEGORY_COLORS.get(self.category, "#95A5A6")

    @property
    def is_past(self) -> bool:
        return self.end_time < time.time()

    @property
    def is_today(self) -> bool:
        today = datetime.now()
        return self.start_date.date() == today.date()

    def conflicts_with(self, other: 'CalendarEvent') -> bool:
        """Check if this event overlaps with another."""
        return self.start_time < other.end_time and self.end_time > other.start_time


# ─── Calendar App ────────────────────────────────────────────────────────


class CalendarApp:
    """
    Calendar application for Nyrqis OS.

    Manages events with multiple views and scheduling.
    """

    def __init__(self):
        self._events: List[CalendarEvent] = []
        self._view_mode: ViewMode = ViewMode.MONTH
        self._current_date: datetime = datetime.now()
        self._selected_day: datetime = datetime.now()
        self._selected_index: int = 0

        # Edit state
        self._editing_event: Optional[CalendarEvent] = None
        self._edit_field: str = "title"

        # Filter
        self._filter_category: Optional[EventCategory] = None
        self._show_all_day: bool = True

        # Callbacks
        self._on_event_change: List[Callable] = []
        self._on_reminder: List[Callable] = []

        # Init sample events
        self._init_sample_events()

    def _init_sample_events(self) -> None:
        """Create sample events."""
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        samples = [
            ("Team Standup", 9, 0, 9, 30, EventCategory.WORK, "Daily sync meeting", "Zoom"),
            ("Lunch with Alice", 12, 0, 13, 0, EventCategory.SOCIAL, "Cafe near office", ""),
            ("Code Review", 14, 0, 15, 0, EventCategory.WORK, "Review PR #42", ""),
            ("Gym Session", 17, 30, 18, 30, EventCategory.HEALTH, "Leg day", "Fitness Center"),
            ("Sprint Planning", 10, 0, 11, 30, EventCategory.WORK, "Next sprint kickoff", "Conf Room A"),
            ("Dentist Appointment", 11, 0, 12, 0, EventCategory.HEALTH, "Regular checkup", "Dr. Smith"),
            ("Birthday Party", 18, 0, 21, 0, EventCategory.SOCIAL, "Sarah's birthday!", "123 Main St"),
            ("Project Deadline", 17, 0, 17, 30, EventCategory.WORK, "Submit final report", ""),
            ("Yoga Class", 7, 0, 8, 0, EventCategory.HEALTH, "Morning flow", "Studio B"),
            ("Movie Night", 19, 0, 21, 30, EventCategory.SOCIAL, "Sci-fi marathon", "Living Room"),
        ]

        for i, (title, sh, sm, eh, em, cat, notes, loc) in enumerate(samples):
            # Place events on different days around today
            day_offset = i % 7 - 2
            event_day = today + timedelta(days=day_offset)
            start = event_day.replace(hour=sh, minute=sm)
            end = event_day.replace(hour=eh, minute=em)
            self._events.append(CalendarEvent(
                title=title,
                start_time=start.timestamp(),
                end_time=end.timestamp(),
                location=loc,
                notes=notes,
                category=cat,
            ))

        # Add a recurring meeting
        for day in range(5):  # Next 5 days
            event_day = today + timedelta(days=day)
            start = event_day.replace(hour=9, minute=0)
            end = event_day.replace(hour=9, minute=30)
            self._events.append(CalendarEvent(
                title="Daily Standup",
                start_time=start.timestamp(),
                end_time=end.timestamp(),
                category=EventCategory.WORK,
                recurrence=Recurrence.DAILY,
                location="Zoom",
            ))

    # ── Event CRUD ────────────────────────────────────────────────────

    def create_event(self, title: str, start_time: float, end_time: float, **kwargs) -> CalendarEvent:
        event = CalendarEvent(
            title=title,
            start_time=start_time,
            end_time=end_time,
            **kwargs,
        )
        self._events.append(event)
        self._notify("change")
        return event

    def update_event(self, event_id: str, **kwargs) -> bool:
        event = self.get_event(event_id)
        if not event:
            return False
        for key, value in kwargs.items():
            if hasattr(event, key):
                setattr(event, key, value)
        event.modified = time.time()
        self._notify("change")
        return True

    def delete_event(self, event_id: str) -> bool:
        for i, event in enumerate(self._events):
            if event.event_id == event_id:
                self._events.pop(i)
                self._notify("change")
                return True
        return False

    def get_event(self, event_id: str) -> Optional[CalendarEvent]:
        for event in self._events:
            if event.event_id == event_id:
                return event
        return None

    def duplicate_event(self, event_id: str, new_start: float = 0) -> Optional[CalendarEvent]:
        event = self.get_event(event_id)
        if not event:
            return None
        offset = (new_start - event.start_time) if new_start else 86400  # +1 day default
        return self.create_event(
            title=f"{event.title} (copy)",
            start_time=event.start_time + offset,
            end_time=event.end_time + offset,
            all_day=event.all_day,
            location=event.location,
            notes=event.notes,
            category=event.category,
            recurrence=event.recurrence,
        )

    # ── Queries ───────────────────────────────────────────────────────

    def get_events_for_day(self, date: datetime) -> List[CalendarEvent]:
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        day_end = day_start + 86400
        events = [e for e in self._events
                  if e.start_time < day_end and e.end_time > day_start]
        events.sort(key=lambda e: e.start_time)
        return self._apply_filter(events)

    def get_events_for_week(self, date: datetime) -> Dict[int, List[CalendarEvent]]:
        """Get events for the week containing the given date."""
        # Find start of week (Monday)
        week_start = date - timedelta(days=date.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        result = {}
        for i in range(7):
            day = week_start + timedelta(days=i)
            result[i] = self.get_events_for_day(day)
        return result

    def get_events_for_month(self, year: int, month: int) -> Dict[int, List[CalendarEvent]]:
        """Get events for each day of a month."""
        result = {}
        days_in_month = self._days_in_month(year, month)
        for day in range(1, days_in_month + 1):
            date = datetime(year, month, day)
            events = self.get_events_for_day(date)
            if events:
                result[day] = events
        return result

    def get_today_events(self) -> List[CalendarEvent]:
        return self.get_events_for_day(datetime.now())

    def get_upcoming(self, days: int = 7) -> List[CalendarEvent]:
        now = time.time()
        end = now + days * 86400
        events = [e for e in self._events if e.start_time >= now and e.start_time < end]
        events.sort(key=lambda e: e.start_time)
        return events

    def search_events(self, query: str) -> List[CalendarEvent]:
        q = query.lower()
        return [e for e in self._events
                if q in e.title.lower() or
                q in e.notes.lower() or
                q in e.location.lower()]

    def get_conflicts(self, event_id: str = None) -> List[Tuple[CalendarEvent, CalendarEvent]]:
        """Find conflicting events."""
        events = sorted(self._events, key=lambda e: e.start_time)
        conflicts = []
        for i, e1 in enumerate(events):
            if event_id and e1.event_id != event_id:
                continue
            for e2 in events[i + 1:]:
                if e1.conflicts_with(e2):
                    conflicts.append((e1, e2))
        return conflicts

    def _apply_filter(self, events: List[CalendarEvent]) -> List[CalendarEvent]:
        if self._filter_category:
            events = [e for e in events if e.category == self._filter_category]
        if not self._show_all_day:
            events = [e for e in events if not e.all_day]
        return events

    # ── Reminders ─────────────────────────────────────────────────────

    def check_reminders(self) -> List[CalendarEvent]:
        """Check for events that need reminders."""
        now = time.time()
        due = []
        for event in self._events:
            reminder_time = event.start_time - event.reminder_minutes * 60
            if now >= reminder_time and now < event.start_time:
                due.append(event)
        return due

    # ── Navigation ────────────────────────────────────────────────────

    def set_view(self, mode: ViewMode) -> None:
        self._view_mode = mode

    def cycle_view(self) -> ViewMode:
        modes = [ViewMode.MONTH, ViewMode.WEEK, ViewMode.DAY]
        idx = modes.index(self._view_mode)
        self._view_mode = modes[(idx + 1) % len(modes)]
        return self._view_mode

    def go_today(self) -> None:
        self._current_date = datetime.now()
        self._selected_day = datetime.now()

    def go_forward(self) -> datetime:
        if self._view_mode == ViewMode.DAY:
            self._current_date += timedelta(days=1)
        elif self._view_mode == ViewMode.WEEK:
            self._current_date += timedelta(weeks=1)
        else:
            # Month
            month = self._current_date.month + 1
            year = self._current_date.year
            if month > 12:
                month = 1
                year += 1
            self._current_date = self._current_date.replace(year=year, month=month, day=1)
        return self._current_date

    def go_back(self) -> datetime:
        if self._view_mode == ViewMode.DAY:
            self._current_date -= timedelta(days=1)
        elif self._view_mode == ViewMode.WEEK:
            self._current_date -= timedelta(weeks=1)
        else:
            month = self._current_date.month - 1
            year = self._current_date.year
            if month < 1:
                month = 12
                year -= 1
            self._current_date = self._current_date.replace(year=year, month=month, day=1)
        return self._current_date

    @property
    def view_mode(self) -> ViewMode:
        return self._view_mode

    @property
    def current_date(self) -> datetime:
        return self._current_date

    @property
    def selected_day(self) -> datetime:
        return self._selected_day

    def select_day(self, day: int) -> None:
        try:
            self._selected_day = self._current_date.replace(day=day)
        except ValueError:
            pass

    # ── Edit State ────────────────────────────────────────────────────

    def start_edit(self, event_id: str = None) -> CalendarEvent:
        if event_id:
            event = self.get_event(event_id)
            if event:
                self._editing_event = event
                return event
        # Create new
        now = datetime.now()
        start = now.replace(minute=0, second=0) + timedelta(hours=1)
        end = start + timedelta(hours=1)
        event = CalendarEvent(
            title="New Event",
            start_time=start.timestamp(),
            end_time=end.timestamp(),
        )
        self._editing_event = event
        return event

    def save_edit(self) -> Optional[CalendarEvent]:
        if not self._editing_event:
            return None
        if self._editing_event.event_id not in [e.event_id for e in self._events]:
            self._events.append(self._editing_event)
        self._editing_event = None
        self._notify("change")
        return self._editing_event

    def cancel_edit(self) -> None:
        self._editing_event = None

    @property
    def editing_event(self) -> Optional[CalendarEvent]:
        return self._editing_event

    # ── Filtering ─────────────────────────────────────────────────────

    def set_filter_category(self, category: Optional[EventCategory]) -> None:
        self._filter_category = category

    def toggle_all_day(self) -> bool:
        self._show_all_day = not self._show_all_day
        return self._show_all_day

    # ── Export ────────────────────────────────────────────────────────

    def export_events(self, start_date: datetime = None, end_date: datetime = None) -> str:
        """Export events as iCalendar-like text."""
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Nyrqis Calendar//EN",
        ]
        for event in self._events:
            if start_date and event.start_time < start_date.timestamp():
                continue
            if end_date and event.start_time > end_date.timestamp():
                continue
            lines.extend([
                "BEGIN:VEVENT",
                f"SUMMARY:{event.title}",
                f"DTSTART:{datetime.fromtimestamp(event.start_time).strftime('%Y%m%dT%H%M%S')}",
                f"DTEND:{datetime.fromtimestamp(event.end_time).strftime('%Y%m%dT%H%M%S')}",
                f"LOCATION:{event.location}" if event.location else "",
                f"DESCRIPTION:{event.notes}" if event.notes else "",
                f"UID:{event.event_id}@nyrqis.os",
                "END:VEVENT",
            ])
        lines.append("END:VCALENDAR")
        return "\n".join(l for l in lines if l)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_month(self, width: int = 72) -> List[str]:
        lines = []
        year = self._current_date.year
        month = self._current_date.month
        month_name = self._current_date.strftime("%B %Y")

        # Header
        lines.append(f" 📅 {month_name}")
        lines.append("─" * width)

        # Day headers
        day_names = " Mon  Tue  Wed  Thu  Fri  Sat  Sun"
        lines.append(day_names[:width])
        lines.append("─" * width)

        # Calendar grid
        first_day = datetime(year, month, 1)
        start_weekday = (first_day.weekday() + 1) % 7  # Sunday=0
        days = self._days_in_month(year, month)

        # Build weeks
        week = ["     "] * start_weekday
        for day in range(1, days + 1):
            date = datetime(year, month, day)
            events = self.get_events_for_day(date)
            is_today = date.date() == datetime.now().date()
            is_selected = date.date() == self._selected_day.date()

            # Day cell
            day_str = f"{day:3d}"
            if is_today:
                day_str = f"[{day:2d}]"
            elif is_selected:
                day_str = f"({day:2d})"
            else:
                day_str = f" {day:2d} "

            # Event indicator
            if events:
                marker = str(len(events))
                day_str = day_str[:4] + marker

            week.append(day_str)

            if len(week) == 7:
                lines.append(" ".join(week)[:width])
                week = []

        # Fill last week
        if week:
            while len(week) < 7:
                week.append("     ")
            lines.append(" ".join(week)[:width])

        # Legend
        lines.append("─" * width)
        lines.append(" 1:Today  ←→:Navigate  W:Week  D:Day  N:New Event")

        return lines

    def render_week(self, width: int = 72) -> List[str]:
        lines = []
        # Find start of week (Monday)
        week_start = self._current_date - timedelta(days=self._current_date.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

        header = f" 📅 Week of {week_start.strftime('%b %d, %Y')}"
        lines.append(header[:width])
        lines.append("─" * width)

        # Day columns
        for i in range(7):
            day = week_start + timedelta(days=i)
            day_name = day.strftime("%a %d")
            events = self.get_events_for_day(day)
            is_today = day.date() == datetime.now().date()

            prefix = "▶ " if is_today else "  "
            lines.append(f"{prefix}{day_name}:")

            if not events:
                lines.append("    (no events)")
            else:
                for event in events[:4]:  # Max 4 shown
                    time_str = event.time_range[:20]
                    title = event.title[:width - 25]
                    lines.append(f"    {time_str} {title}")
                if len(events) > 4:
                    lines.append(f"    ... +{len(events) - 4} more")

            lines.append("")

        lines.append("─" * width)
        lines.append(" ←→:Navigate  M:Month  D:Day  T:Today")
        return lines

    def render_day(self, width: int = 72) -> List[str]:
        lines = []
        date = self._current_date
        day_name = date.strftime("%A, %B %d, %Y")
        is_today = date.date() == datetime.now().date()

        header = f" 📅 {day_name}"
        if is_today:
            header += " (Today)"
        lines.append(header[:width])
        lines.append("─" * width)

        events = self.get_events_for_day(date)

        if not events:
            lines.append("")
            lines.append("  No events today.")
            lines.append("  Press N to create one.")
        else:
            for event in events:
                cat_badge = f"[{event.category.value[0].upper()}]"
                time_str = event.time_range
                title = event.title

                lines.append(f" {cat_badge} {time_str}")
                lines.append(f"    {title}")
                if event.location:
                    lines.append(f"    📍 {event.location}")
                if event.notes:
                    lines.append(f"    📝 {event.notes[:width - 8]}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ←→:Navigate  W:Week  M:Month  N:New  Enter:Edit")
        return lines

    def render(self, width: int = 72, height: int = 30) -> List[str]:
        if self._view_mode == ViewMode.WEEK:
            return self.render_week(width)
        elif self._view_mode == ViewMode.DAY:
            return self.render_day(width)
        return self.render_month(width)

    # ── Helpers ───────────────────────────────────────────────────────

    def _days_in_month(self, year: int, month: int) -> int:
        if month == 12:
            return (datetime(year + 1, 1, 1) - datetime(year, month, 1)).days
        return (datetime(year, month + 1, 1) - datetime(year, month, 1)).days

    def _notify(self, event: str) -> None:
        for cb in self._on_event_change:
            try:
                cb()
            except Exception:
                pass

    def on_event_change(self, cb: Callable) -> None:
        self._on_event_change.append(cb)

    def on_reminder(self, cb: Callable) -> None:
        self._on_reminder.append(cb)
