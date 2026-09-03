"""Home Automation Dashboard — device controls, scenes, energy monitoring for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import random


class DeviceType(Enum):
    LIGHT = "Light"
    SWITCH = "Switch"
    THERMOSTAT = "Thermostat"
    LOCK = "Lock"
    CAMERA = "Camera"
    SENSOR = "Sensor"
    SPEAKER = "Speaker"
    BLIND = "Blinds"
    PLUG = "Smart Plug"
    SPRINKLER = "Sprinkler"
    GARAGE = "Garage Door"
    DOORBELL = "Doorbell"


class DeviceState(Enum):
    ON = "On"
    OFF = "Off"
    OPEN = "Open"
    CLOSED = "Closed"
    LOCKED = "Locked"
    UNLOCKED = "Unlocked"
    ARMED = "Armed"
    DISARMED = "Disarmed"
    IDLE = "Idle"


class Room(Enum):
    LIVING_ROOM = "Living Room"
    BEDROOM = "Bedroom"
    KITCHEN = "Kitchen"
    BATHROOM = "Bathroom"
    GARAGE = "Garage"
    OFFICE = "Office"
    HALLWAY = "Hallway"
    GARDEN = "Garden"
    BASEMENT = "Basement"


class SceneType(Enum):
    CUSTOM = "Custom"
    PRESET = "Preset"
    AUTOMATION = "Automation"
    SCHEDULE = "Schedule"


class AlertType(Enum):
    MOTION = "Motion Detected"
    DOOR = "Door Opened"
    WINDOW = "Window Opened"
    TEMP_HIGH = "Temperature High"
    TEMP_LOW = "Temperature Low"
    HUMIDITY_HIGH = "Humidity High"
    SMOKE = "Smoke Detected"
    WATER = "Water Leak"
    POWER = "Power Outage"
    INTRUSION = "Intrusion Alert"


@dataclass
class SmartDevice:
    name: str
    device_type: DeviceType = DeviceType.LIGHT
    room: Room = Room.LIVING_ROOM
    state: DeviceState = DeviceState.OFF
    brightness: int = 100  # 0-100
    temperature: float = 22.0  # for thermostat
    color_temp: int = 4000  # Kelvin
    humidity: float = 45.0  # percent
    power_watts: float = 0.0
    signal_strength: int = 100  # percent
    firmware: str = "1.0.0"
    ip_address: str = ""
    last_seen: float = 0.0
    battery: int = 100  # percent, -1 if wired
    automations: List[str] = field(default_factory=list)

    @property
    def is_on(self) -> bool:
        return self.state in (DeviceState.ON, DeviceState.OPEN, DeviceState.UNLOCKED, DeviceState.ARMED)

    @property
    def state_icon(self) -> str:
        icons = {
            DeviceState.ON: "💡",
            DeviceState.OFF: "⭕",
            DeviceState.OPEN: "🔓",
            DeviceState.CLOSED: "🔒",
            DeviceState.LOCKED: "🔒",
            DeviceState.UNLOCKED: "🔓",
            DeviceState.ARMED: "🛡️",
            DeviceState.DISARMED: "⚠️",
            DeviceState.IDLE: "⏸",
        }
        return icons.get(self.state, "?")

    @property
    def brightness_bar(self) -> str:
        filled = int(self.brightness / 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def signal_bar(self) -> str:
        filled = int(self.signal_strength / 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def battery_bar(self) -> str:
        if self.battery < 0:
            return "⚡ Wired"
        filled = int(self.battery / 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def power_str(self) -> str:
        return f"{self.power_watts:.1f}W"


@dataclass
class Scene:
    name: str
    scene_type: SceneType = SceneType.CUSTOM
    devices: Dict[str, Dict] = field(default_factory=dict)  # device_name -> {state, brightness, etc}
    icon: str = "🎬"
    active: bool = False
    triggered_count: int = 0
    last_triggered: float = 0.0
    schedule: str = ""  # cron expression
    conditions: List[str] = field(default_factory=list)

    @property
    def active_icon(self) -> str:
        return "🟢" if self.active else "⚪"

    @property
    def device_count(self) -> int:
        return len(self.devices)


@dataclass
class EnergyReading:
    timestamp: float = 0.0
    total_watts: float = 0.0
    solar_watts: float = 0.0
    grid_watts: float = 0.0
    battery_level: float = 80.0
    cost_today: float = 0.0
    cost_month: float = 0.0
    kwh_today: float = 0.0
    kwh_month: float = 0.0
    history: List[float] = field(default_factory=list)  # last 24 hourly readings

    @property
    def solar_bar(self) -> str:
        filled = int(min(self.solar_watts / 50, 1.0) * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def grid_bar(self) -> str:
        filled = int(min(self.grid_watts / 50, 1.0) * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def battery_bar(self) -> str:
        filled = int(self.battery_level / 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class Alert:
    timestamp: float = 0.0
    alert_type: AlertType = AlertType.MOTION
    device_name: str = ""
    room: str = ""
    message: str = ""
    acknowledged: bool = False
    severity: int = 1  # 1=info, 2=warning, 3=critical

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M", time.localtime(self.timestamp))

    @property
    def severity_icon(self) -> str:
        icons = {1: "ℹ️", 2: "⚠️", 3: "🚨"}
        return icons.get(self.severity, "ℹ️")


class HomeAuto:
    def __init__(self):
        self._devices: List[SmartDevice] = []
        self._scenes: List[Scene] = []
        self._energy = EnergyReading()
        self._alerts: List[Alert] = []
        self._selected_device: int = 0
        self._selected_scene: int = 0
        self._view_mode: str = "rooms"
        self._away_mode: bool = False
        self._night_mode: bool = False
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        self._devices = [
            SmartDevice("Ceiling Light", DeviceType.LIGHT, Room.LIVING_ROOM, DeviceState.ON,
                        brightness=80, power_watts=12.5, ip_address="192.168.1.101",
                        last_seen=now, automations=["Sunset: On", "Midnight: Off"]),
            SmartDevice("Floor Lamp", DeviceType.LIGHT, Room.LIVING_ROOM, DeviceState.ON,
                        brightness=60, color_temp=3000, power_watts=8.0, ip_address="192.168.1.102"),
            SmartDevice("TV", DeviceType.SWITCH, Room.LIVING_ROOM, DeviceState.ON,
                        power_watts=120.0, ip_address="192.168.1.110"),
            SmartDevice("Thermostat", DeviceType.THERMOSTAT, Room.LIVING_ROOM, DeviceState.ON,
                        temperature=22.5, humidity=45.0, ip_address="192.168.1.120"),
            SmartDevice("Front Door", DeviceType.LOCK, Room.HALLWAY, DeviceState.LOCKED,
                        battery=85, ip_address="192.168.1.130", automations=["Auto-lock: 5min"]),
            SmartDevice("Bedroom Light", DeviceType.LIGHT, Room.BEDROOM, DeviceState.OFF,
                        brightness=0, power_watts=0.0, ip_address="192.168.1.140"),
            SmartDevice("Bedroom Blinds", DeviceType.BLIND, Room.BEDROOM, DeviceState.CLOSED,
                        ip_address="192.168.1.141", automations=["Sunrise: Open"]),
            SmartDevice("Kitchen Light", DeviceType.LIGHT, Room.KITCHEN, DeviceState.ON,
                        brightness=100, power_watts=15.0, ip_address="192.168.1.150"),
            SmartDevice("Kitchen Plug", DeviceType.PLUG, Room.KITCHEN, DeviceState.ON,
                        power_watts=45.0, ip_address="192.168.1.151"),
            SmartDevice("Motion Sensor", DeviceType.SENSOR, Room.HALLWAY, DeviceState.IDLE,
                        battery=92, signal_strength=98, ip_address="192.168.1.160"),
            SmartDevice("Front Camera", DeviceType.CAMERA, Room.GARDEN, DeviceState.ON,
                        power_watts=8.5, signal_strength=85, ip_address="192.168.1.170"),
            SmartDevice("Doorbell", DeviceType.DOORBELL, Room.GARDEN, DeviceState.IDLE,
                        battery=78, ip_address="192.168.1.171"),
            SmartDevice("Garage Door", DeviceType.GARAGE, Room.GARAGE, DeviceState.CLOSED,
                        ip_address="192.168.1.180", automations=["Auto-close: 10min"]),
            SmartDevice("Office Light", DeviceType.LIGHT, Room.OFFICE, DeviceState.ON,
                        brightness=90, color_temp=5000, power_watts=10.0, ip_address="192.168.1.190"),
            SmartDevice("Sprinkler", DeviceType.SPRINKLER, Room.GARDEN, DeviceState.OFF,
                        ip_address="192.168.1.200", automations=["Schedule: 6am daily"]),
            SmartDevice("Living Speaker", DeviceType.SPEAKER, Room.LIVING_ROOM, DeviceState.OFF,
                        power_watts=5.0, ip_address="192.168.1.210"),
        ]

        self._scenes = [
            Scene("Good Morning", SceneType.PRESET, {"Kitchen Light": {"state": "On"}, "Bedroom Blinds": {"state": "Open"}},
                  "🌅", True, 45, now - 21600, schedule="0 7 * * *"),
            Scene("Movie Night", SceneType.CUSTOM, {"Ceiling Light": {"brightness": 20}, "Floor Lamp": {"brightness": 30}, "TV": {"state": "On"}},
                  "🎬", False, 12, now - 86400),
            Scene("Away Mode", SceneType.AUTOMATION, {"All Lights": {"state": "Off"}, "Front Door": {"state": "Locked"}, "Cameras": {"state": "On"}},
                  "🏠", False, 8, now - 172800),
            Scene("Good Night", SceneType.PRESET, {"All Lights": {"state": "Off"}, "Front Door": {"state": "Locked"}, "Bedroom Blinds": {"state": "Closed"}},
                  "🌙", False, 90, now - 28800),
            Scene("Work From Home", SceneType.CUSTOM, {"Office Light": {"brightness": 90, "color_temp": 5000}, "Living Speaker": {"state": "On"}},
                  "💼", True, 30, now - 7200, schedule="0 9 * * 1-5"),
        ]

        self._energy = EnergyReading(
            now, 2850.0, 1800.0, 1050.0, 80.0, 4.25, 128.50, 18.4, 520.0,
            history=[1200, 1100, 1000, 900, 850, 900, 1500, 2200, 2800, 3200, 3500,
                    3800, 4000, 3800, 3500, 3200, 2800, 2500, 2200, 2000, 1800, 1600, 1400, 1300]
        )

        self._alerts = [
            Alert(now - 300, AlertType.MOTION, "Motion Sensor", "Hallway", "Motion detected in hallway"),
            Alert(now - 1800, AlertType.DOOR, "Front Door", "Hallway", "Front door opened"),
            Alert(now - 3600, AlertType.TEMP_HIGH, "Thermostat", "Living Room", "Temperature reached 25°C"),
            Alert(now - 7200, AlertType.INTRUSION, "Front Camera", "Garden", "Unrecognized face detected", severity=3),
            Alert(now - 14400, AlertType.MOTION, "Front Camera", "Garden", "Package delivery detected"),
        ]

    @property
    def selected_device(self) -> Optional[SmartDevice]:
        if 0 <= self._selected_device < len(self._devices):
            return self._devices[self._selected_device]
        return None

    @property
    def total_devices(self) -> int:
        return len(self._devices)

    @property
    def active_devices(self) -> int:
        return sum(1 for d in self._devices if d.is_on)

    @property
    def total_power(self) -> float:
        return sum(d.power_watts for d in self._devices if d.is_on)

    @property
    def devices_by_room(self) -> Dict[str, List[SmartDevice]]:
        rooms = {}
        for d in self._devices:
            room = d.room.value
            if room not in rooms:
                rooms[room] = []
            rooms[room].append(d)
        return rooms

    def select_device(self, idx: int):
        if 0 <= idx < len(self._devices):
            self._selected_device = idx

    def toggle_device(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_device
        if 0 <= i < len(self._devices):
            d = self._devices[i]
            if d.is_on:
                d.state = DeviceState.OFF
                d.power_watts = 0.0
            else:
                d.state = DeviceState.ON
                d.power_watts = 10.0  # default
            self._history.append(f"Toggled {d.name}")

    def trigger_scene(self, idx: int):
        if 0 <= idx < len(self._scenes):
            scene = self._scenes[idx]
            scene.active = True
            scene.triggered_count += 1
            scene.last_triggered = time.time()
            self._history.append(f"Triggered scene: {scene.name}")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "t":
            self.toggle_device()
        elif key == "a":
            self._away_mode = not self._away_mode
        elif key == "n":
            self._night_mode = not self._night_mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS HOME AUTOMATION                                    ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Status
        away = "🏠 AWAY" if self._away_mode else ""
        night = "🌙 NIGHT" if self._night_mode else ""
        lines.append(f"  Devices: {self.active_devices}/{self.total_devices} active  Power: {self.total_power:.0f}W  Scenes: {len(self._scenes)}  Alerts: {len(self._alerts)}  {away} {night}")
        lines.append("")

        # Energy
        e = self._energy
        lines.append(f"  ── Energy ──")
        lines.append(f"  ⚡ Total: {e.total_watts:.0f}W  ☀️ Solar: [{e.solar_bar}] {e.solar_watts:.0f}W  🔌 Grid: [{e.grid_bar}] {e.grid_watts:.0f}W  🔋 Battery: [{e.battery_bar}] {e.battery_level:.0f}%")
        lines.append(f"  💰 Today: ${e.cost_today:.2f} ({e.kwh_today:.1f} kWh)  Month: ${e.cost_month:.2f} ({e.kwh_month:.0f} kWh)")
        lines.append("")

        # Devices by room
        lines.append(f"  ── Devices by Room ──")
        for room_name, devices in self.devices_by_room.items():
            active = sum(1 for d in devices if d.is_on)
            room_icon = {"Living Room": "🛋", "Bedroom": "🛏", "Kitchen": "🍳", "Hallway": "🚪",
                         "Garden": "🌿", "Office": "💼", "Garage": "🚗"}.get(room_name, "🏠")
            lines.append(f"  {room_icon} {room_name} ({active}/{len(devices)})")
            for d in devices:
                sel = "▶" if d.name == (self.selected_device.name if self.selected_device else "") else " "
                state = d.state_icon
                info = ""
                if d.device_type == DeviceType.LIGHT and d.is_on:
                    info = f"  [{d.brightness_bar}] {d.brightness}%"
                elif d.device_type == DeviceType.THERMOSTAT:
                    info = f"  {d.temperature}°C  {d.humidity:.0f}%"
                elif d.device_type in (DeviceType.LOCK, DeviceType.GARAGE):
                    info = f"  {d.state.value}"
                elif d.device_type == DeviceType.CAMERA:
                    info = f"  📹 Recording"
                elif d.device_type == DeviceType.SENSOR:
                    info = f"  Signal: {d.signal_bar}"
                lines.append(f"  {sel} {state} {d.name:<20s} {d.power_str:>8s}{info}")
            lines.append("")

        # Scenes
        lines.append(f"  ── Scenes ──")
        for i, scene in enumerate(self._scenes):
            sel = "▶" if i == self._selected_scene else " "
            sched = f"⏰ {scene.schedule}" if scene.schedule else ""
            lines.append(f"  {sel} {scene.active_icon} {scene.icon} {scene.name:<20s} {scene.device_count} devices  Triggered: {scene.triggered_count}x  {sched}")
        lines.append("")

        # Recent alerts
        if self._alerts:
            lines.append(f"  ── Recent Alerts ──")
            for alert in self._alerts[:4]:
                ack = "✅" if alert.acknowledged else "  "
                lines.append(f"  {alert.severity_icon} {alert.time_str} {alert.alert_type.value:<20s} {alert.room}  {ack}")
            lines.append("")

        lines.append("  [T]oggle Device [A]way Mode [N]ight Mode [↑↓]Select [Enter]Scene")
        lines.append("  [L]ights [T]hermostat [L]ocks [📷]Cameras")
        return lines
