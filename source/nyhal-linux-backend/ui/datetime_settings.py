"""DateTimeSettings — Date, time, and timezone configuration for Nyrqis.

Provides date/time management with:
- Timezone selection (major world timezones)
- Date format (US, ISO, European)
- Time format (12h/24h)
- NTP synchronization toggle
- Clock display settings
- World clock (multiple timezones)
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DateFormat(Enum):
    US = auto()        # MM/DD/YYYY
    ISO = auto()       # YYYY-MM-DD
    EUROPEAN = auto()  # DD.MM.YYYY
    UK = auto()        # DD/MM/YYYY
    JAPANESE = auto()  # YYYY年MM月DD日


class TimeFormat(Enum):
    H24 = auto()  # 24-hour
    H12 = auto()  # 12-hour with AM/PM


class WeekStart(Enum):
    MONDAY = auto()
    SUNDAY = auto()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Timezone:
    """A timezone entry."""
    id: str
    name: str
    offset_hours: float   # UTC offset in hours
    dst: bool = False     # currently in DST
    region: str = ""      # continent

    @property
    def offset_str(self) -> str:
        sign = "+" if self.offset_hours >= 0 else "-"
        h = int(abs(self.offset_hours))
        m = int((abs(self.offset_hours) - h) * 60)
        return f"UTC{sign}{h:02d}:{m:02d}"

    @property
    def local_time(self) -> str:
        utc = time.time()
        local = utc + self.offset_hours * 3600
        return time.strftime("%H:%M", time.gmtime(local))

    @property
    def local_date(self) -> str:
        utc = time.time()
        local = utc + self.offset_hours * 3600
        return time.strftime("%Y-%m-%d", time.gmtime(local))


@dataclass
class WorldClock:
    """A world clock entry."""
    timezone_id: str
    label: str = ""
    visible: bool = True


@dataclass
class DateTimeConfig:
    """Date/time configuration."""
    timezone: str = "UTC"
    date_format: DateFormat = DateFormat.ISO
    time_format: TimeFormat = TimeFormat.H24
    week_start: WeekStart = WeekStart.MONDAY
    ntp_enabled: bool = True
    ntp_server: str = "pool.ntp.org"
    show_seconds: bool = False
    show_date_in_tray: bool = True
    show_week_number: bool = False


# ---------------------------------------------------------------------------
# Major world timezones
# ---------------------------------------------------------------------------

WORLD_TIMEZONES = [
    Timezone("Pacific/Honolulu", "Honolulu", -10.0, region="Oceania"),
    Timezone("America/Anchorage", "Anchorage", -9.0, region="North America"),
    Timezone("America/Los_Angeles", "Los Angeles", -8.0, region="North America"),
    Timezone("America/Denver", "Denver", -7.0, region="North America"),
    Timezone("America/Chicago", "Chicago", -6.0, region="North America"),
    Timezone("America/New_York", "New York", -5.0, region="North America"),
    Timezone("America/Sao_Paulo", "São Paulo", -3.0, region="South America"),
    Timezone("Atlantic/Reykjavik", "Reykjavik", 0.0, region="Europe"),
    Timezone("Europe/London", "London", 0.0, region="Europe"),
    Timezone("Europe/Paris", "Paris", 1.0, region="Europe"),
    Timezone("Europe/Berlin", "Berlin", 1.0, region="Europe"),
    Timezone("Europe/Helsinki", "Helsinki", 2.0, region="Europe"),
    Timezone("Europe/Moscow", "Moscow", 3.0, region="Europe"),
    Timezone("Asia/Dubai", "Dubai", 4.0, region="Asia"),
    Timezone("Asia/Kolkata", "Mumbai", 5.5, region="Asia"),
    Timezone("Asia/Dhaka", "Dhaka", 6.0, region="Asia"),
    Timezone("Asia/Bangkok", "Bangkok", 7.0, region="Asia"),
    Timezone("Asia/Shanghai", "Shanghai", 8.0, region="Asia"),
    Timezone("Asia/Tokyo", "Tokyo", 9.0, region="Asia"),
    Timezone("Australia/Sydney", "Sydney", 10.0, region="Oceania"),
    Timezone("Pacific/Auckland", "Auckland", 12.0, region="Oceania"),
]


# ---------------------------------------------------------------------------
# DateTimeSettings
# ---------------------------------------------------------------------------

class DateTimeSettings:
    """Date, time, and timezone configuration for Nyrqis.

    Parameters
    ----------
    width, height : int
        Rendering dimensions.
    """

    def __init__(self, width: int = 400, height: int = 500):
        self.width = width
        self.height = height

        # Config
        self._config = DateTimeConfig()

        # Timezones
        self._timezones = list(WORLD_TIMEZONES)
        self._selected_tz = "UTC"

        # World clocks
        self._world_clocks: List[WorldClock] = [
            WorldClock("America/New_York", "New York"),
            WorldClock("Europe/London", "London"),
            WorldClock("Asia/Tokyo", "Tokyo"),
        ]

    @property
    def config(self) -> DateTimeConfig:
        return self._config

    @property
    def timezones(self) -> List[Timezone]:
        return list(self._timezones)

    @property
    def world_clocks(self) -> List[WorldClock]:
        return list(self._world_clocks)

    def get_timezone(self, tz_id: str) -> Optional[Timezone]:
        for tz in self._timezones:
            if tz.id == tz_id:
                return tz
        return None

    # -- Configuration ---------------------------------------------------

    def set_timezone(self, tz_id: str) -> bool:
        for tz in self._timezones:
            if tz.id == tz_id:
                self._config.timezone = tz_id
                self._selected_tz = tz_id
                return True
        return False

    def set_date_format(self, fmt: DateFormat) -> None:
        self._config.date_format = fmt

    def set_time_format(self, fmt: TimeFormat) -> None:
        self._config.time_format = fmt

    def set_week_start(self, start: WeekStart) -> None:
        self._config.week_start = start

    def toggle_ntp(self) -> bool:
        self._config.ntp_enabled = not self._config.ntp_enabled
        return self._config.ntp_enabled

    def set_ntp_server(self, server: str) -> None:
        self._config.ntp_server = server

    def toggle_seconds(self) -> bool:
        self._config.show_seconds = not self._config.show_seconds
        return self._config.show_seconds

    def toggle_week_number(self) -> bool:
        self._config.show_week_number = not self._config.show_week_number
        return self._config.show_week_number

    # -- World clocks ----------------------------------------------------

    def add_world_clock(self, tz_id: str, label: str = "") -> bool:
        tz = self.get_timezone(tz_id)
        if tz is None:
            return False
        # Check duplicate
        for wc in self._world_clocks:
            if wc.timezone_id == tz_id:
                return False
        self._world_clocks.append(WorldClock(tz_id, label or tz.name))
        return True

    def remove_world_clock(self, tz_id: str) -> bool:
        before = len(self._world_clocks)
        self._world_clocks = [wc for wc in self._world_clocks
                              if wc.timezone_id != tz_id]
        return len(self._world_clocks) < before

    # -- Formatting ------------------------------------------------------

    def format_date(self, timestamp: float = 0.0) -> str:
        """Format a date according to the current format setting."""
        if timestamp == 0.0:
            timestamp = time.time()
        t = time.localtime(timestamp)
        fmt = self._config.date_format
        if fmt == DateFormat.US:
            return time.strftime("%m/%d/%Y", t)
        elif fmt == DateFormat.ISO:
            return time.strftime("%Y-%m-%d", t)
        elif fmt == DateFormat.EUROPEAN:
            return time.strftime("%d.%m.%Y", t)
        elif fmt == DateFormat.UK:
            return time.strftime("%d/%m/%Y", t)
        elif fmt == DateFormat.JAPANESE:
            return time.strftime("%Y年%m月%d日", t)
        return time.strftime("%Y-%m-%d", t)

    def format_time(self, timestamp: float = 0.0) -> str:
        """Format a time according to the current format setting."""
        if timestamp == 0.0:
            timestamp = time.time()
        t = time.localtime(timestamp)
        if self._config.time_format == TimeFormat.H12:
            fmt = "%I:%M %p"
        else:
            fmt = "%H:%M"
        if self._config.show_seconds:
            fmt = fmt.replace(":", "%H:%M:%S") if "%H:%M" in fmt else fmt
        return time.strftime(fmt, t)

    # -- Rendering -------------------------------------------------------

    def render(self) -> Tuple[bytes, int, int]:
        """Render the date/time settings UI."""
        w, h = self.width, self.height
        buf = bytearray(w * h * 3)
        bg = (30, 30, 40)
        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        # Header
        self._fill_rect(buf, w, 0, 0, w, 48, (42, 42, 56))

        # Current time display
        now = time.strftime("%H:%M:%S") if self._config.show_seconds else time.strftime("%H:%M")
        self._fill_rect(buf, w, 20, 60, 120, 32, (80, 140, 255))

        # Current date display
        date_str = self.format_date()
        self._fill_rect(buf, w, 20, 100, 120, 16, (150, 150, 170))

        # Timezone
        self._fill_rect(buf, w, 20, 130, 160, 14, (200, 200, 210))
        self._fill_rect(buf, w, 20, 150, 80, 14, (120, 120, 140))

        # World clocks
        y = 180
        for wc in self._world_clocks:
            if not wc.visible:
                continue
            tz = self.get_timezone(wc.timezone_id)
            if tz:
                self._fill_rect(buf, w, 20, y, 100, 14, (200, 200, 210))
                self._fill_rect(buf, w, 140, y, 60, 14, (80, 140, 255))
                y += 30

        # Settings toggles
        y = max(y + 20, 300)
        toggles = [
            ("24-hour time", self._config.time_format == TimeFormat.H24),
            ("Show seconds", self._config.show_seconds),
            ("NTP sync", self._config.ntp_enabled),
            ("Week numbers", self._config.show_week_number),
        ]
        for label, active in toggles:
            toggle_color = (80, 200, 120) if active else (80, 80, 100)
            self._fill_rect(buf, w, 20, y, 100, 14, (200, 200, 210))
            self._fill_rect(buf, w, w - 60, y + 2, 40, 12, toggle_color)
            y += 26

        return bytes(buf), w, h

    def _fill_rect(self, buf: bytearray, buf_width: int,
                   x: int, y: int, w: int, h: int,
                   color: Tuple[int, int, int]) -> None:
        buf_height = len(buf) // (buf_width * 3)
        for dy in range(h):
            for dx in range(w):
                px, py = x + dx, y + dy
                if 0 <= px < buf_width and 0 <= py < buf_height:
                    idx = (py * buf_width + px) * 3
                    if idx + 2 < len(buf):
                        buf[idx] = color[0]
                        buf[idx + 1] = color[1]
                        buf[idx + 2] = color[2]

    # -- Serialization ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timezone": self._config.timezone,
            "date_format": self._config.date_format.name,
            "time_format": self._config.time_format.name,
            "ntp_enabled": self._config.ntp_enabled,
            "ntp_server": self._config.ntp_server,
            "show_seconds": self._config.show_seconds,
            "world_clocks": len(self._world_clocks),
        }


__all__ = [
    "DateTimeSettings", "DateTimeConfig", "Timezone", "WorldClock",
    "DateFormat", "TimeFormat", "WeekStart", "WORLD_TIMEZONES",
]
