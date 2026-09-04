"""
Nyrqis OS - Desktop Widget Toolkit
Clock, weather, CPU monitor, and sticky notes.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class WidgetType(Enum):
    CLOCK = "clock"
    WEATHER = "weather"
    CPU_MONITOR = "cpu_monitor"
    STICKY_NOTE = "sticky_note"
    CALENDAR = "calendar"
    SYSTEM_TRAY = "system_tray"
    MUSIC_PLAYER = "music_player"
    BATTERY = "battery"


class WidgetSize(Enum):
    TINY = "tiny"       # 1x1
    SMALL = "small"     # 2x1
    MEDIUM = "medium"   # 2x2
    LARGE = "large"     # 4x2
    FULL = "full"       # 4x4


class StickyColor(Enum):
    YELLOW = "#ffeb3b"
    PINK = "#f48fb1"
    BLUE = "#81d4fa"
    GREEN = "#a5d6a7"
    ORANGE = "#ffcc80"
    PURPLE = "#ce93d8"
    WHITE = "#ffffff"


@dataclass
class WidgetPosition:
    x: int = 0
    y: int = 0
    width: int = 200
    height: int = 100


@dataclass
class ClockWidget:
    format_24h: bool = True
    show_seconds: bool = True
    show_date: bool = True
    show_timezone: bool = True
    timezone: str = "local"
    analog: bool = False
    theme: str = "digital"

    @property
    def time_display(self) -> str:
        if self.format_24h:
            return time.strftime("%H:%M:%S")
        return time.strftime("%I:%M:%S %p")

    @property
    def date_display(self) -> str:
        return time.strftime("%A, %B %d, %Y")

    @property
    def short_date(self) -> str:
        return time.strftime("%b %d")

    @property
    def day_of_week(self) -> str:
        return time.strftime("%A")

    @property
    def analog_hands(self) -> Dict[str, float]:
        t = time.localtime()
        h = t.tm_hour % 12
        m = t.tm_min
        s = t.tm_sec
        return {"hour": h * 30 + m * 0.5, "minute": m * 6, "second": s * 6}


@dataclass
class WeatherData:
    temperature_c: float = 22.0
    feels_like_c: float = 20.0
    humidity: int = 65
    wind_kph: float = 12.0
    wind_direction: str = "NW"
    condition: str = "Partly Cloudy"
    icon: str = "⛅"
    uv_index: float = 5.0
    visibility_km: float = 10.0
    pressure_hpa: float = 1013.0
    sunrise: str = "06:15"
    sunset: str = "19:45"
    city: str = "New York"
    country: str = "US"

    @property
    def temperature_f(self) -> float:
        return self.temperature_c * 9 / 5 + 32

    @property
    def wind_display(self) -> str:
        return f"{self.wind_kph:.0f} km/h {self.wind_direction}"

    @property
    def uv_level(self) -> str:
        if self.uv_index < 3:
            return "🟢 Low"
        elif self.uv_index < 6:
            return "🟡 Moderate"
        elif self.uv_index < 8:
            return "🟠 High"
        return "🔴 Very High"


@dataclass
class WeatherForecast:
    date: str = ""
    high_c: float = 0.0
    low_c: float = 0.0
    condition: str = ""
    icon: str = ""
    precipitation_pct: int = 0

    @property
    def temp_range(self) -> str:
        return f"{self.low_c:.0f}° / {self.high_c:.0f}°"


@dataclass
class CPUMonitorWidget:
    usage_percent: float = 0.0
    temperature_c: float = 0.0
    frequency_ghz: float = 0.0
    cores: int = 0
    threads: int = 0
    processes: int = 0
    load_avg: List[float] = field(default_factory=list)
    history: List[float] = field(default_factory=list)
    show_per_core: bool = False
    core_usages: List[float] = field(default_factory=list)

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def temp_bar(self) -> str:
        filled = int(self.temperature_c / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def status(self) -> str:
        if self.usage_percent < 30:
            return "🟢 Idle"
        elif self.usage_percent < 70:
            return "🟡 Active"
        return "🔴 Busy"

    @property
    def sparkline(self) -> str:
        if not self.history:
            return ""
        chars = "▁▂▃▄▅▆▇█"
        result = []
        for v in self.history[-30:]:
            idx = min(7, int(v / 100 * 8))
            result.append(chars[idx])
        return "".join(result)


@dataclass
class StickyNote:
    id: int = 0
    content: str = ""
    color: StickyColor = StickyColor.YELLOW
    x: int = 0
    y: int = 0
    width: int = 200
    height: int = 200
    created_at: float = 0.0
    modified_at: float = 0.0
    pinned: bool = False
    font_size: int = 14
    opacity: float = 1.0
    tags: List[str] = field(default_factory=list)

    def __post_init__(self):
        now = time.time()
        if self.created_at == 0.0:
            self.created_at = now
        if self.modified_at == 0.0:
            self.modified_at = now

    @property
    def preview(self) -> str:
        return self.content[:60].replace("\n", " ") if self.content else "(empty)"

    @property
    def time_ago(self) -> str:
        delta = time.time() - self.modified_at
        if delta < 60:
            return "just now"
        elif delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.0f}h ago"
        return f"{delta / 86400:.0f}d ago"


@dataclass
class Widget:
    id: str = ""
    widget_type: WidgetType = WidgetType.CLOCK
    title: str = ""
    size: WidgetSize = WidgetSize.MEDIUM
    position: WidgetPosition = field(default_factory=WidgetPosition)
    visible: bool = True
    opacity: float = 1.0
    theme: str = "default"
    refresh_interval_s: int = 1
    locked: bool = False


class DesktopWidgetToolkit:
    def __init__(self):
        self.widgets: List[Widget] = []
        self.clock = ClockWidget()
        self.weather = WeatherData()
        self.forecasts: List[WeatherForecast] = []
        self.cpu_monitor = CPUMonitorWidget()
        self.sticky_notes: List[StickyNote] = []
        self.note_counter: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        self.widgets = [
            Widget(id="clock-1", widget_type=WidgetType.CLOCK, title="Clock",
                   size=WidgetSize.MEDIUM, position=WidgetPosition(10, 10, 300, 150)),
            Widget(id="weather-1", widget_type=WidgetType.WEATHER, title="Weather",
                   size=WidgetSize.LARGE, position=WidgetPosition(320, 10, 400, 200)),
            Widget(id="cpu-1", widget_type=WidgetType.CPU_MONITOR, title="CPU Monitor",
                   size=WidgetSize.MEDIUM, position=WidgetPosition(10, 170, 300, 150)),
            Widget(id="notes-1", widget_type=WidgetType.STICKY_NOTE, title="Sticky Notes",
                   size=WidgetSize.LARGE, position=WidgetPosition(730, 10, 400, 300)),
        ]

        self.cpu_monitor = CPUMonitorWidget(
            usage_percent=34.5, temperature_c=62.0, frequency_ghz=4.5,
            cores=16, threads=32, processes=312,
            load_avg=[4.2, 3.8, 3.5],
            history=[25, 30, 45, 60, 55, 40, 35, 32, 28, 30, 35, 42, 38, 34, 30, 28, 25, 30, 35, 38],
            core_usages=[35, 42, 28, 55, 30, 38, 25, 60, 45, 32, 28, 40, 50, 35, 22, 48])

        self.forecasts = [
            WeatherForecast(date="Today", high_c=24, low_c=16, condition="Partly Cloudy", icon="⛅", precipitation_pct=20),
            WeatherForecast(date="Tomorrow", high_c=26, low_c=18, condition="Sunny", icon="☀️", precipitation_pct=5),
            WeatherForecast(date="Wed", high_c=22, low_c=15, condition="Rain", icon="🌧️", precipitation_pct=80),
            WeatherForecast(date="Thu", high_c=19, low_c=13, condition="Overcast", icon="☁️", precipitation_pct=40),
            WeatherForecast(date="Fri", high_c=23, low_c=14, condition="Partly Cloudy", icon="⛅", precipitation_pct=15),
        ]

        self.sticky_notes = [
            StickyNote(id=1, content="Remember to review Nyrqis PR #42 before end of day",
                       color=StickyColor.YELLOW, x=740, y=20, pinned=True,
                       tags=["work", "review"]),
            StickyNote(id=2, content="TODO:\n- Update compositor tests\n- Fix wayland bridge memory leak\n- Release notes for v0.2.0",
                       color=StickyColor.BLUE, x=740, y=180,
                       tags=["todo", "nyrqis"]),
            StickyNote(id=3, content="Meeting notes from architecture review:\n- Use Vulkan compute for GPU scheduling\n- Add hot-plug support for monitors",
                       color=StickyColor.GREEN, x=740, y=340,
                       tags=["meeting"]),
        ]
        self.note_counter = 3

    def add_widget(self, widget_type: WidgetType, title: str = "", **kwargs) -> Widget:
        self.note_counter += 1
        widget = Widget(id=f"{widget_type.value}-{self.note_counter}",
                         widget_type=widget_type, title=title or widget_type.value.title(),
                         **kwargs)
        self.widgets.append(widget)
        return widget

    def remove_widget(self, widget_id: str) -> bool:
        for i, w in enumerate(self.widgets):
            if w.id == widget_id:
                del self.widgets[i]
                return True
        return False

    def toggle_widget(self, widget_id: str) -> bool:
        widget = next((w for w in self.widgets if w.id == widget_id), None)
        if widget:
            widget.visible = not widget.visible
            return True
        return False

    def move_widget(self, widget_id: str, x: int, y: int) -> bool:
        widget = next((w for w in self.widgets if w.id == widget_id), None)
        if widget:
            widget.position.x = x
            widget.position.y = y
            return True
        return False

    def add_sticky_note(self, content: str, color: StickyColor = StickyColor.YELLOW, **kwargs) -> StickyNote:
        self.note_counter += 1
        note = StickyNote(id=self.note_counter, content=content, color=color, **kwargs)
        self.sticky_notes.append(note)
        return note

    def update_sticky_note(self, note_id: int, content: str) -> bool:
        note = next((n for n in self.sticky_notes if n.id == note_id), None)
        if note:
            note.content = content
            note.modified_at = time.time()
            return True
        return False

    def delete_sticky_note(self, note_id: int) -> bool:
        for i, n in enumerate(self.sticky_notes):
            if n.id == note_id:
                del self.sticky_notes[i]
                return True
        return False

    def get_visible_widgets(self) -> List[Widget]:
        return [w for w in self.widgets if w.visible]

    def get_weather_summary(self) -> Dict:
        return {
            "temperature": f"{self.weather.temperature_c:.0f}°C",
            "feels_like": f"{self.weather.feels_like_c:.0f}°C",
            "condition": self.weather.condition,
            "wind": self.weather.wind_display,
            "humidity": f"{self.weather.humidity}%",
        }

    def get_stats(self) -> Dict:
        return {
            "widgets": len(self.widgets),
            "visible": len(self.get_visible_widgets()),
            "sticky_notes": len(self.sticky_notes),
            "forecasts": len(self.forecasts),
        }
