"""
Nyrqis Weather — weather widget with forecasts and conditions.

Features:
- Current conditions with temperature, humidity, wind, UV index
- 7-day forecast with high/low temperatures
- Hourly forecast for 24 hours
- Weather alerts and warnings
- Sunrise/sunset times
- Location management with favorites
- Weather maps placeholder
- Weather icons (emoji-based)
- Air quality index
"""

import time
import math
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable
from datetime import datetime, timedelta


# ─── Weather Conditions ──────────────────────────────────────────────────


class WeatherCondition(Enum):
    CLEAR = "clear"
    PARTLY_CLOUDY = "partly_cloudy"
    CLOUDY = "cloudy"
    OVERCAST = "overcast"
    RAIN = "rain"
    LIGHT_RAIN = "light_rain"
    HEAVY_RAIN = "heavy_rain"
    THUNDERSTORM = "thunderstorm"
    SNOW = "snow"
    LIGHT_SNOW = "light_snow"
    HEAVY_SNOW = "heavy_snow"
    SLEET = "sleet"
    FREEZING_RAIN = "freezing_rain"
    FOG = "fog"
    WINDY = "windy"
    HAZE = "haze"
    DRIZZLE = "drizzle"


class AlertSeverity(Enum):
    ADVISORY = "advisory"
    WATCH = "watch"
    WARNING = "warning"
    EMERGENCY = "emergency"


CONDITION_ICONS = {
    WeatherCondition.CLEAR: "☀️",
    WeatherCondition.PARTLY_CLOUDY: "⛅",
    WeatherCondition.CLOUDY: "☁️",
    WeatherCondition.OVERCAST: "☁️",
    WeatherCondition.RAIN: "🌧️",
    WeatherCondition.LIGHT_RAIN: "🌦️",
    WeatherCondition.HEAVY_RAIN: "⛈️",
    WeatherCondition.THUNDERSTORM: "⛈️",
    WeatherCondition.SNOW: "🌨️",
    WeatherCondition.LIGHT_SNOW: "🌨️",
    WeatherCondition.HEAVY_SNOW: "❄️",
    WeatherCondition.SLEET: "🌧️",
    WeatherCondition.FREEZING_RAIN: "🌧️",
    WeatherCondition.FOG: "🌫️",
    WeatherCondition.WINDY: "💨",
    WeatherCondition.HAZE: "🌫️",
    WeatherCondition.DRIZZLE: "🌦️",
}

WIND_DIRECTIONS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                   "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

AIR_QUALITY_LEVELS = {
    (0, 50): ("Good", "🟢"),
    (51, 100): ("Moderate", "🟡"),
    (101, 150): ("Unhealthy for Sensitive", "🟠"),
    (151, 200): ("Unhealthy", "🔴"),
    (201, 300): ("Very Unhealthy", "🟣"),
    (301, 500): ("Hazardous", "🟤"),
}


# ─── Data Classes ────────────────────────────────────────────────────────


@dataclass
class WeatherAlert:
    """A weather alert or warning."""
    title: str
    description: str
    severity: AlertSeverity
    start_time: float
    end_time: float
    source: str = "NWS"

    @property
    def icon(self) -> str:
        icons = {
            AlertSeverity.ADVISORY: "ℹ️",
            AlertSeverity.WATCH: "👁️",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.EMERGENCY: "🚨",
        }
        return icons.get(self.severity, "❓")

    @property
    def is_active(self) -> bool:
        now = time.time()
        return self.start_time <= now <= self.end_time

    @property
    def time_range(self) -> str:
        start = datetime.fromtimestamp(self.start_time).strftime("%b %d %I:%M %p")
        end = datetime.fromtimestamp(self.end_time).strftime("%b %d %I:%M %p")
        return f"{start} – {end}"


@dataclass
class HourlyForecast:
    """An hourly weather forecast."""
    time: float  # Unix timestamp
    temperature: float  # °F
    feels_like: float
    condition: WeatherCondition
    precipitation_percent: float
    humidity: float
    wind_speed: float  # mph
    wind_direction: str

    @property
    def hour_str(self) -> str:
        return datetime.fromtimestamp(self.time).strftime("%I %p")

    @property
    def temp_str(self) -> str:
        return f"{self.temperature:.0f}°"

    @property
    def icon(self) -> str:
        return CONDITION_ICONS.get(self.condition, "❓")

    @property
    def wind_str(self) -> str:
        return f"{self.wind_speed:.0f} mph {self.wind_direction}"


@dataclass
class DailyForecast:
    """A daily weather forecast."""
    date: float  # Unix timestamp (start of day)
    high: float
    low: float
    condition: WeatherCondition
    precipitation_percent: float
    humidity: float
    wind_speed: float
    sunrise: float
    sunset: float
    uv_index: float
    air_quality: int = 50

    @property
    def day_name(self) -> str:
        d = datetime.fromtimestamp(self.date)
        today = datetime.now().date()
        if d.date() == today:
            return "Today"
        elif d.date() == today + timedelta(days=1):
            return "Tomorrow"
        return d.strftime("%a")

    @property
    def date_str(self) -> str:
        return datetime.fromtimestamp(self.date).strftime("%b %d")

    @property
    def icon(self) -> str:
        return CONDITION_ICONS.get(self.condition, "❓")

    @property
    def temp_range(self) -> str:
        return f"{self.high:.0f}° / {self.low:.0f}°"

    @property
    def precipitation_str(self) -> str:
        if self.precipitation_percent <= 0:
            return ""
        return f"💧{self.precipitation_percent:.0f}%"

    @property
    def sunrise_str(self) -> str:
        return datetime.fromtimestamp(self.sunrise).strftime("%I:%M %p")

    @property
    def sunset_str(self) -> str:
        return datetime.fromtimestamp(self.sunset).strftime("%I:%M %p")

    @property
    def air_quality_str(self) -> str:
        for (low, high), (label, icon) in AIR_QUALITY_LEVELS.items():
            if low <= self.air_quality <= high:
                return f"{icon} {label}"
        return "❓ Unknown"


@dataclass
class CurrentWeather:
    """Current weather conditions."""
    temperature: float  # °F
    feels_like: float
    condition: WeatherCondition
    description: str
    humidity: float
    wind_speed: float
    wind_direction: str
    wind_gust: float
    pressure: float  # hPa
    visibility: float  # miles
    uv_index: float
    dew_point: float
    air_quality: int
    sunrise: float
    sunset: float
    timestamp: float = field(default_factory=time.time)

    @property
    def temp_str(self) -> str:
        return f"{self.temperature:.0f}°F"

    @property
    def feels_like_str(self) -> str:
        return f"{self.feels_like:.0f}°F"

    @property
    def icon(self) -> str:
        return CONDITION_ICONS.get(self.condition, "❓")

    @property
    def wind_str(self) -> str:
        return f"{self.wind_speed:.0f} mph {self.wind_direction}"

    @property
    def visibility_str(self) -> str:
        if self.visibility >= 10:
            return "Clear"
        elif self.visibility >= 5:
            return "Good"
        elif self.visibility >= 2:
            return "Moderate"
        return "Poor"

    @property
    def uv_str(self) -> str:
        if self.uv_index <= 2:
            return f"{self.uv_index:.0f} Low"
        elif self.uv_index <= 5:
            return f"{self.uv_index:.0f} Moderate"
        elif self.uv_index <= 7:
            return f"{self.uv_index:.0f} High"
        elif self.uv_index <= 10:
            return f"{self.uv_index:.0f} Very High"
        return f"{self.uv_index:.0f} Extreme"

    @property
    def sunrise_str(self) -> str:
        return datetime.fromtimestamp(self.sunrise).strftime("%I:%M %p")

    @property
    def sunset_str(self) -> str:
        return datetime.fromtimestamp(self.sunset).strftime("%I:%M %p")

    @property
    def air_quality_str(self) -> str:
        for (low, high), (label, icon) in AIR_QUALITY_LEVELS.items():
            if low <= self.air_quality <= high:
                return f"{icon} {label}"
        return "❓ Unknown"

    @property
    def pressure_str(self) -> str:
        return f"{self.pressure:.0f} hPa"


# ─── Weather Location ────────────────────────────────────────────────────


@dataclass
class WeatherLocation:
    """A weather location."""
    name: str
    region: str = ""
    country: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    is_favorite: bool = False

    @property
    def display(self) -> str:
        if self.region:
            return f"{self.name}, {self.region}"
        return self.name


# ─── Weather Widget ──────────────────────────────────────────────────────


class WeatherWidget:
    """
    Weather widget for Nyrqis OS.

    Displays current conditions, forecasts, and alerts.
    """

    def __init__(self):
        self._locations: List[WeatherLocation] = [
            WeatherLocation("San Francisco", "CA", "US", 37.7749, -122.4194, True),
            WeatherLocation("New York", "NY", "US", 40.7128, -74.0060),
            WeatherLocation("London", "", "UK", 51.5074, -0.1278),
            WeatherLocation("Tokyo", "", "JP", 35.6762, 139.6503),
        ]
        self._current_location: int = 0
        self._view_mode: str = "current"  # current, hourly, daily, alerts
        self._selected_day: int = 0

        # Data
        self._current: Optional[CurrentWeather] = None
        self._hourly: List[HourlyForecast] = []
        self._daily: List[DailyForecast] = []
        self._alerts: List[WeatherAlert] = []

        # Callbacks
        self._on_refresh: List[Callable] = []

        # Generate sample data
        self._generate_sample_data()

    def _generate_sample_data(self) -> None:
        """Generate simulated weather data."""
        now = time.time()
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Current conditions
        self._current = CurrentWeather(
            temperature=72.0,
            feels_like=70.0,
            condition=WeatherCondition.PARTLY_CLOUDY,
            description="Partly cloudy with a chance of rain later",
            humidity=65.0,
            wind_speed=12.0,
            wind_direction="NW",
            wind_gust=18.0,
            pressure=1013.25,
            visibility=10.0,
            uv_index=5.0,
            dew_point=58.0,
            air_quality=42,
            sunrise=today.timestamp() + 6 * 3600 + 15 * 60,
            sunset=today.timestamp() + 19 * 3600 + 30 * 60,
        )

        # Hourly forecast
        conditions = [
            WeatherCondition.PARTLY_CLOUDY, WeatherCondition.CLOUDY,
            WeatherCondition.LIGHT_RAIN, WeatherCondition.RAIN,
            WeatherCondition.CLOUDY, WeatherCondition.PARTLY_CLOUDY,
            WeatherCondition.CLEAR, WeatherCondition.CLEAR,
        ]

        for i in range(24):
            hour_time = now + i * 3600
            temp = 72 - 10 * math.sin((i - 6) * math.pi / 12)  # Diurnal variation
            cond = conditions[i % len(conditions)]
            self._hourly.append(HourlyForecast(
                time=hour_time,
                temperature=temp,
                feels_like=temp - 2,
                condition=cond,
                precipitation_percent=max(0, 30 - abs(i - 6) * 5),
                humidity=65 + 10 * math.sin(i * math.pi / 12),
                wind_speed=12 + 5 * math.sin(i * math.pi / 6),
                wind_direction=WIND_DIRECTIONS[i % 16],
            ))

        # 7-day forecast
        daily_conditions = [
            WeatherCondition.PARTLY_CLOUDY,
            WeatherCondition.CLOUDY,
            WeatherCondition.RAIN,
            WeatherCondition.LIGHT_RAIN,
            WeatherCondition.PARTLY_CLOUDY,
            WeatherCondition.CLEAR,
            WeatherCondition.CLEAR,
        ]
        high_temps = [75, 72, 65, 68, 73, 78, 80]
        low_temps = [58, 55, 52, 54, 57, 60, 62]

        for i in range(7):
            day = today + timedelta(days=i)
            sunrise = day.timestamp() + 6 * 3600 + (15 - i) * 60
            sunset = day.timestamp() + 19 * 3600 + (30 + i * 2) * 60
            self._daily.append(DailyForecast(
                date=day.timestamp(),
                high=high_temps[i],
                low=low_temps[i],
                condition=daily_conditions[i],
                precipitation_percent=max(0, 40 - i * 10),
                humidity=60 + i * 5,
                wind_speed=10 + i * 2,
                sunrise=sunrise,
                sunset=sunset,
                uv_index=max(1, 8 - i),
                air_quality=30 + i * 10,
            ))

        # Alerts
        self._alerts = [
            WeatherAlert(
                title="Wind Advisory",
                description="Sustained winds 25-35 mph with gusts up to 50 mph expected.",
                severity=AlertSeverity.ADVISORY,
                start_time=now,
                end_time=now + 7200,
                source="NWS",
            ),
        ]

    # ── Navigation ────────────────────────────────────────────────────

    @property
    def location(self) -> WeatherLocation:
        if 0 <= self._current_location < len(self._locations):
            return self._locations[self._current_location]
        return self._locations[0]

    def next_location(self) -> None:
        if self._locations:
            self._current_location = (self._current_location + 1) % len(self._locations)

    def prev_location(self) -> None:
        if self._locations:
            self._current_location = (self._current_location - 1) % len(self._locations)

    def set_location(self, index: int) -> None:
        if 0 <= index < len(self._locations):
            self._current_location = index

    @property
    def locations(self) -> List[WeatherLocation]:
        return list(self._locations)

    def add_location(self, name: str, region: str = "") -> WeatherLocation:
        loc = WeatherLocation(name=name, region=region)
        self._locations.append(loc)
        return loc

    def remove_location(self, index: int) -> bool:
        if 0 <= index < len(self._locations) and len(self._locations) > 1:
            self._locations.pop(index)
            self._current_location = min(self._current_location, len(self._locations) - 1)
            return True
        return False

    def toggle_favorite(self) -> bool:
        loc = self.location
        loc.is_favorite = not loc.is_favorite
        return loc.is_favorite

    # ── View Mode ─────────────────────────────────────────────────────

    def set_view(self, mode: str) -> None:
        self._view_mode = mode

    def cycle_view(self) -> str:
        views = ["current", "hourly", "daily", "alerts"]
        idx = views.index(self._view_mode) if self._view_mode in views else 0
        self._view_mode = views[(idx + 1) % len(views)]
        return self._view_mode

    @property
    def view_mode(self) -> str:
        return self._view_mode

    # ── Data Access ───────────────────────────────────────────────────

    @property
    def current(self) -> Optional[CurrentWeather]:
        return self._current

    @property
    def hourly(self) -> List[HourlyForecast]:
        return list(self._hourly)

    @property
    def daily(self) -> List[DailyForecast]:
        return list(self._daily)

    @property
    def alerts(self) -> List[WeatherAlert]:
        return [a for a in self._alerts if a.is_active]

    @property
    def all_alerts(self) -> List[WeatherAlert]:
        return list(self._alerts)

    @property
    def has_alerts(self) -> bool:
        return len(self.alerts) > 0

    def refresh(self) -> None:
        """Simulate weather data refresh."""
        self._generate_sample_data()
        for cb in self._on_refresh:
            try:
                cb()
            except Exception:
                pass

    def on_refresh(self, cb: Callable) -> None:
        self._on_refresh.append(cb)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_current(self, width: int = 40) -> List[str]:
        """Render current conditions view."""
        lines = []
        c = self._current
        if not c:
            return ["No weather data"]

        # Header
        loc = self.location
        fav = "⭐" if loc.is_favorite else ""
        lines.append(f" 🌤️ {loc.display} {fav}")
        lines.append("─" * width)

        # Main display
        lines.append(f"  {c.icon}  {c.temp_str}")
        lines.append(f"  Feels like {c.feels_like_str}")
        lines.append(f"  {c.description}")
        lines.append("")

        # Details
        lines.append(f"  💧 Humidity:     {c.humidity:.0f}%")
        lines.append(f"  💨 Wind:         {c.wind_str}")
        lines.append(f"  🌀 Gusts:        {c.wind_gust:.0f} mph")
        lines.append(f"  🌡️  Pressure:     {c.pressure_str}")
        lines.append(f"  👁️  Visibility:   {c.visibility_str}")
        lines.append(f"  ☀️  UV Index:     {c.uv_str}")
        lines.append(f"  🌡️  Dew Point:    {c.dew_point:.0f}°F")
        lines.append(f"  🫁 Air Quality:  {c.air_quality_str}")
        lines.append("")

        # Sunrise/Sunset
        lines.append(f"  🌅 Sunrise: {c.sunrise_str}")
        lines.append(f"  🌇 Sunset:  {c.sunset_str}")

        return lines

    def render_hourly(self, width: int = 60) -> List[str]:
        """Render hourly forecast view."""
        lines = []
        loc = self.location
        lines.append(f" 🌤️ {loc.display} — Hourly Forecast")
        lines.append("─" * width)

        # Header
        lines.append(f" {'Time':>7}  {'':3}  {'Temp':>5}  {'Precip':>6}  {'Humid':>5}  {'Wind'}")
        lines.append("─" * width)

        for h in self._hourly[:12]:
            precip = f"💧{h.precipitation_percent:.0f}%" if h.precipitation_percent > 0 else ""
            line = (
                f" {h.hour_str:>7}  {h.icon}  {h.temp_str:>5}  "
                f"{precip:>6}  {h.humidity:>4.0f}%  {h.wind_str}"
            )
            lines.append(line[:width])

        lines.append("─" * width)
        lines.append(" ←→:Location  ↑↓:View  T:Today")
        return lines

    def render_daily(self, width: int = 60) -> List[str]:
        """Render 7-day forecast view."""
        lines = []
        loc = self.location
        lines.append(f" 🌤️ {loc.display} — 7-Day Forecast")
        lines.append("─" * width)

        lines.append(f" {'Day':<10}  {'':3}  {'High':>5}  {'Low':>5}  {'Precip':>6}  {'Wind':>8}")
        lines.append("─" * width)

        for i, d in enumerate(self._daily):
            marker = " ▸" if i == self._selected_day else "  "
            precip = f"💧{d.precipitation_percent:.0f}%" if d.precipitation_percent > 0 else ""
            line = (
                f"{marker}{d.day_name:<10}  {d.icon}  "
                f"{d.high:>4.0f}°  {d.low:>4.0f}°  {precip:>6}  {d.wind_speed:>6.0f}mph"
            )
            lines.append(line[:width])

            # Additional detail for selected day
            if i == self._selected_day:
                lines.append(f"   🌅 {d.sunrise_str}  🌇 {d.sunset_str}  ☀️ UV {d.uv_index:.0f}")
                lines.append(f"   {d.air_quality_str}")

        lines.append("─" * width)
        lines.append(" ←→:Location  ↑↓:Day  V:View")
        return lines

    def render_alerts(self, width: int = 60) -> List[str]:
        """Render weather alerts view."""
        lines = []
        lines.append(" 🚨 Weather Alerts")
        lines.append("─" * width)

        active = self.alerts
        if not active:
            lines.append("")
            lines.append("  ✅ No active alerts")
            lines.append("  All clear!")
        else:
            for alert in active:
                lines.append(f" {alert.icon} [{alert.severity.value.upper()}] {alert.title}")
                lines.append(f"   {alert.description}")
                lines.append(f"   📅 {alert.time_range}")
                lines.append(f"   Source: {alert.source}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ←→:Location  ↑↓:View  Enter:Details")
        return lines

    def render_compact(self, width: int = 40) -> str:
        """Render a compact single-line weather display."""
        c = self._current
        if not c:
            return "Weather: N/A"
        loc = self.location
        return f" {c.icon} {c.temp_str} {c.condition.value.replace('_', ' ').title()} — {loc.display}"

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        if self._view_mode == "hourly":
            return self.render_hourly(width)
        elif self._view_mode == "daily":
            return self.render_daily(width)
        elif self._view_mode == "alerts":
            return self.render_alerts(width)
        return self.render_current(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if key == "ArrowLeft":
            self.prev_location()
            return "prev_location"
        elif key == "ArrowRight":
            self.next_location()
            return "next_location"
        elif key == "v" or key == "V":
            self.cycle_view()
            return "cycle_view"
        elif key == "r" or key == "R":
            self.refresh()
            return "refresh"
        elif key == "f" or key == "F":
            self.toggle_favorite()
            return "toggle_favorite"
        elif key == "ArrowUp" and self._view_mode == "daily":
            self._selected_day = max(0, self._selected_day - 1)
            return "select_day"
        elif key == "ArrowDown" and self._view_mode == "daily":
            self._selected_day = min(len(self._daily) - 1, self._selected_day + 1)
            return "select_day"
        elif key == "t" or key == "T":
            self._selected_day = 0
            return "today"
        return None
