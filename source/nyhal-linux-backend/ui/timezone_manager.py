"""
Nyrqis OS - Time Zone Manager
World clock, DST handling, and meeting scheduler.
"""

import time
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class DSTRule(Enum):
    NONE = "none"
    US = "us"
    EU = "eu"
    AU = "au"
    CUSTOM = "custom"


@dataclass
class TimeZone:
    name: str
    city: str = ""
    country: str = ""
    utc_offset: float = 0.0
    utc_offset_dst: float = 0.0
    dst_rule: DSTRule = DSTRule.NONE
    dst_active: bool = False
    iana_tz: str = ""
    flag: str = ""

    @property
    def current_offset(self) -> str:
        offset = self.utc_offset_dst if self.dst_active else self.utc_offset
        sign = "+" if offset >= 0 else "-"
        h = int(abs(offset))
        m = int((abs(offset) - h) * 60)
        return f"UTC{sign}{h:02d}:{m:02d}"

    @property
    def offset_hours(self) -> float:
        return self.utc_offset_dst if self.dst_active else self.utc_offset


@dataclass
class WorldClockEntry:
    timezone: TimeZone
    is_pinned: bool = False
    label: str = ""

    @property
    def display_name(self) -> str:
        return self.label if self.label else self.timezone.city


@dataclass
class MeetingSlot:
    name: str
    timezone: TimeZone = field(default_factory=TimeZone)
    local_hour: int = 9
    local_minute: int = 0
    duration_minutes: int = 60
    participants: List[str] = field(default_factory=list)
    recurring: bool = False
    recurring_days: List[str] = field(default_factory=list)
    notes: str = ""

    @property
    def time_display(self) -> str:
        return f"{self.local_hour:02d}:{self.local_minute:02d}"

    @property
    def duration_display(self) -> str:
        if self.duration_minutes < 60:
            return f"{self.duration_minutes}min"
        h = self.duration_minutes // 60
        m = self.duration_minutes % 60
        if m == 0:
            return f"{h}h"
        return f"{h}h{m}m"

    @property
    def end_time(self) -> Tuple[int, int]:
        total = self.local_hour * 60 + self.local_minute + self.duration_minutes
        return (total // 60) % 24, total % 60


@dataclass
class DSTTransition:
    timezone_name: str = ""
    transition_type: str = ""  # "spring_forward" or "fall_back"
    date: str = ""
    utc_offset_before: str = ""
    utc_offset_after: str = ""
    days_until: int = 0


class TimeZoneManager:
    def __init__(self):
        self.timezones: List[TimeZone] = []
        self.world_clocks: List[WorldClockEntry] = []
        self.meetings: List[MeetingSlot] = []
        self.transitions: List[DSTTransition] = []
        self.local_tz: str = "America/New_York"
        self.show_24h: bool = True
        self._create_sample_data()

    def _create_sample_data(self):
        self.timezones = [
            TimeZone(name="New York", city="New York", country="USA",
                     utc_offset=-5.0, utc_offset_dst=-4.0, dst_rule=DSTRule.US,
                     iana_tz="America/New_York", flag="🇺🇸"),
            TimeZone(name="San Francisco", city="San Francisco", country="USA",
                     utc_offset=-8.0, utc_offset_dst=-7.0, dst_rule=DSTRule.US,
                     iana_tz="America/Los_Angeles", flag="🇺🇸"),
            TimeZone(name="London", city="London", country="UK",
                     utc_offset=0.0, utc_offset_dst=1.0, dst_rule=DSTRule.EU,
                     iana_tz="Europe/London", flag="🇬🇧"),
            TimeZone(name="Berlin", city="Berlin", country="Germany",
                     utc_offset=1.0, utc_offset_dst=2.0, dst_rule=DSTRule.EU,
                     iana_tz="Europe/Berlin", flag="🇩🇪"),
            TimeZone(name="Tokyo", city="Tokyo", country="Japan",
                     utc_offset=9.0, dst_rule=DSTRule.NONE,
                     iana_tz="Asia/Tokyo", flag="🇯🇵"),
            TimeZone(name="Sydney", city="Sydney", country="Australia",
                     utc_offset=10.0, utc_offset_dst=11.0, dst_rule=DSTRule.AU,
                     iana_tz="Australia/Sydney", flag="🇦🇺"),
            TimeZone(name="Dubai", city="Dubai", country="UAE",
                     utc_offset=4.0, dst_rule=DSTRule.NONE,
                     iana_tz="Asia/Dubai", flag="🇦🇪"),
            TimeZone(name="Singapore", city="Singapore", country="Singapore",
                     utc_offset=8.0, dst_rule=DSTRule.NONE,
                     iana_tz="Asia/Singapore", flag="🇸🇬"),
            TimeZone(name="São Paulo", city="São Paulo", country="Brazil",
                     utc_offset=-3.0, dst_rule=DSTRule.NONE,
                     iana_tz="America/Sao_Paulo", flag="🇧🇷"),
            TimeZone(name="Mumbai", city="Mumbai", country="India",
                     utc_offset=5.5, dst_rule=DSTRule.NONE,
                     iana_tz="Asia/Kolkata", flag="🇮🇳"),
        ]
        for tz in self.timezones:
            if tz.dst_rule in (DSTRule.US, DSTRule.EU, DSTRule.AU):
                tz.dst_active = True

        self.world_clocks = [
            WorldClockEntry(timezone=self.timezones[0], is_pinned=True, label="Home"),
            WorldClockEntry(timezone=self.timezones[2], is_pinned=True, label="London Team"),
            WorldClockEntry(timezone=self.timezones[4], is_pinned=True, label="Tokyo Office"),
            WorldClockEntry(timezone=self.timezones[3]),
            WorldClockEntry(timezone=self.timezones[5]),
        ]

        self.meetings = [
            MeetingSlot(name="Daily Standup", local_hour=9, local_minute=30,
                         duration_minutes=15, participants=["Team Nyrqis"],
                         recurring=True, recurring_days=["Mon", "Tue", "Wed", "Thu", "Fri"],
                         timezone=self.timezones[0]),
            MeetingSlot(name="Sprint Planning", local_hour=10, local_minute=0,
                         duration_minutes=60, participants=["Engineering", "Product"],
                         recurring=True, recurring_days=["Mon"], timezone=self.timezones[0]),
            MeetingSlot(name="Tokyo Sync", local_hour=8, local_minute=0,
                         duration_minutes=30, participants=["Tokyo Team"],
                         recurring=True, recurring_days=["Tue", "Thu"],
                         timezone=self.timezones[4]),
            MeetingSlot(name="London Review", local_hour=15, local_minute=0,
                         duration_minutes=45, participants=["London Team"],
                         recurring=True, recurring_days=["Fri"],
                         timezone=self.timezones[2]),
            MeetingSlot(name="Architecture Review", local_hour=14, local_minute=0,
                         duration_minutes=90, participants=["Senior Engineers"],
                         recurring=False, timezone=self.timezones[0]),
        ]

        self.transitions = [
            DSTTransition(timezone_name="New York", transition_type="spring_forward",
                           date="2026-03-08", utc_offset_before="UTC-05:00",
                           utc_offset_after="UTC-04:00", days_until=184),
            DSTTransition(timezone_name="London", transition_type="spring_forward",
                           date="2026-03-29", utc_offset_before="UTC+00:00",
                           utc_offset_after="UTC+01:00", days_until=205),
            DSTTransition(timezone_name="Berlin", transition_type="spring_forward",
                           date="2026-03-29", utc_offset_before="UTC+01:00",
                           utc_offset_after="UTC+02:00", days_until=205),
            DSTTransition(timezone_name="Sydney", transition_type="fall_back",
                           date="2026-04-05", utc_offset_before="UTC+11:00",
                           utc_offset_after="UTC+10:00", days_until=212),
        ]

    def get_local_time(self, tz: TimeZone) -> str:
        import datetime
        offset = tz.offset_hours
        utc_now = datetime.datetime.utcnow()
        local = utc_now + datetime.timedelta(hours=offset)
        if self.show_24h:
            return local.strftime("%H:%M:%S")
        return local.strftime("%I:%M:%S %p")

    def get_local_date(self, tz: TimeZone) -> str:
        import datetime
        offset = tz.offset_hours
        utc_now = datetime.datetime.utcnow()
        local = utc_now + datetime.timedelta(hours=offset)
        return local.strftime("%a, %b %d")

    def add_world_clock(self, tz_name: str, label: str = "") -> bool:
        tz = next((t for t in self.timezones if t.name == tz_name), None)
        if tz:
            self.world_clocks.append(WorldClockEntry(timezone=tz, label=label))
            return True
        return False

    def remove_world_clock(self, tz_name: str) -> bool:
        for i, wc in enumerate(self.world_clocks):
            if wc.timezone.name == tz_name:
                del self.world_clocks[i]
                return True
        return False

    def add_meeting(self, meeting: MeetingSlot) -> None:
        self.meetings.append(meeting)

    def remove_meeting(self, name: str) -> bool:
        for i, m in enumerate(self.meetings):
            if m.name == name:
                del self.meetings[i]
                return True
        return False

    def get_meetings_today(self, day: str) -> List[MeetingSlot]:
        return [m for m in self.meetings if not m.recurring or day in m.recurring_days]

    def convert_time(self, hour: int, minute: int, from_tz: TimeZone,
                      to_tz: TimeZone) -> Tuple[int, int]:
        offset_diff = to_tz.offset_hours - from_tz.offset_hours
        total = hour * 60 + minute + int(offset_diff * 60)
        new_hour = (total // 60) % 24
        new_minute = total % 60
        return (new_hour, new_minute)

    def get_next_transition(self, tz_name: str) -> Optional[DSTTransition]:
        return next((t for t in self.transitions if t.timezone_name == tz_name), None)

    def get_time_difference(self, tz1: TimeZone, tz2: TimeZone) -> str:
        diff = tz2.offset_hours - tz1.offset_hours
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.1f}h"

    def search(self, query: str) -> List[TimeZone]:
        q = query.lower()
        return [t for t in self.timezones if q in t.name.lower()
                or q in t.city.lower() or q in t.country.lower()]

    def get_stats(self) -> Dict:
        return {
            "timezones": len(self.timezones),
            "world_clocks": len(self.world_clocks),
            "meetings": len(self.meetings),
            "transitions": len(self.transitions),
            "24h_format": self.show_24h,
        }
