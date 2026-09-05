"""
Nyrqis OS - Audio Mixer
Per-app volume, equalizer, and device switching.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class AudioDeviceType(Enum):
    SPEAKER = "speaker"
    HEADPHONE = "headphone"
    USB_DAC = "usb_dac"
    HDMI = "hdmi"
    BLUETOOTH = "bluetooth"
    DIGITAL = "digital"


class AudioDeviceState(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNPLUGGED = "unplugged"
    CONNECTING = "connecting"


@dataclass
class AudioDevice:
    name: str
    device_type: AudioDeviceType = AudioDeviceType.SPEAKER
    state: AudioDeviceState = AudioDeviceState.ACTIVE
    volume: int = 75
    muted: bool = False
    max_volume: int = 100
    sample_rate: int = 48000
    bit_depth: int = 16
    channels: int = 2
    latency_ms: float = 5.0
    is_default: bool = False
    icon: str = ""

    @property
    def volume_bar(self) -> str:
        filled = int(self.volume / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def state_icon(self) -> str:
        icons = {
            AudioDeviceState.ACTIVE: "🟢", AudioDeviceState.INACTIVE: "⚪",
            AudioDeviceState.UNPLUGGED: "🔴", AudioDeviceState.CONNECTING: "🟡",
        }
        return icons.get(self.state, "?")

    @property
    def mute_icon(self) -> str:
        return "🔇" if self.muted else "🔊"

    @property
    def type_icon(self) -> str:
        icons = {
            AudioDeviceType.SPEAKER: "🔊", AudioDeviceType.HEADPHONE: "🎧",
            AudioDeviceType.USB_DAC: "🎵", AudioDeviceType.HDMI: "📺",
            AudioDeviceType.BLUETOOTH: "📶", AudioDeviceType.DIGITAL: "💿",
        }
        return icons.get(self.device_type, "?")


@dataclass
class AudioApp:
    name: str
    pid: int = 0
    icon: str = ""
    volume: int = 80
    muted: bool = False
    peak_volume: float = 0.0
    is_playing: bool = False
    device: str = ""

    @property
    def volume_bar(self) -> str:
        filled = int(self.volume / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def peak_bar(self) -> str:
        filled = int(self.peak_volume / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def mute_icon(self) -> str:
        return "🔇" if self.muted else "🔊"

    @property
    def play_icon(self) -> str:
        return "▶️" if self.is_playing else "⏸"


@dataclass
class EQBand:
    frequency: float = 0.0
    label: str = ""
    gain_db: float = 0.0
    min_db: float = -12.0
    max_db: float = 12.0

    @property
    def gain_bar(self) -> str:
        normalized = (self.gain_db - self.min_db) / (self.max_db - self.min_db)
        filled = int(normalized * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def value_display(self) -> str:
        sign = "+" if self.gain_db > 0 else ""
        return f"{sign}{self.gain_db:.0f} dB"


@dataclass
class EQPreset:
    name: str
    bands: List[float] = field(default_factory=list)
    description: str = ""

    @property
    def band_display(self) -> str:
        return " ".join(f"{g:+.0f}" for g in self.bands)


@dataclass
class AudioEffect:
    name: str
    enabled: bool = False
    parameters: Dict[str, float] = field(default_factory=dict)


class AudioMixer:
    def __init__(self, width: int = 0, height: int = 0):
        self._width = width
        self._height = height
        self.devices: List[AudioDevice] = []
        self.apps: List[AudioApp] = []
        self.master_volume: int = 80
        self.master_muted: bool = False
        # backward-compat properties
        self._bass: float = 50.0
        self._treble: float = 50.0
        self._balance: float = 0.0
        self._night_mode: bool = False
        self._spatial: bool = False
        self._active_profile: str = "Standard"
        self._active_input: str = "Microphone"
        self._active_output: str = "Built-in Audio"
        # Pre-populate streams
        self._streams = []
        for app_id, name, vol in [("firefox", "Firefox", 70),
                                   ("spotify", "Spotify", 85),
                                   ("discord", "Discord", 60)]:
            s = type("Stream", (), {"app_id": app_id, "name": name,
                                      "volume": vol, "muted": False,
                                      "peak": 0.0})()
            self._streams.append(s)
        self.eq_bands: List[EQBand] = []
        self.eq_enabled: bool = False
        self.eq_presets: List[EQPreset] = []
        self.active_preset: Optional[EQPreset] = None
        self.effects: List[AudioEffect] = []
        self._create_sample_data()

    def _create_sample_data(self):
        self.devices = [
            AudioDevice(name="Built-in Audio", device_type=AudioDeviceType.SPEAKER,
                         state=AudioDeviceState.ACTIVE, volume=80, is_default=True,
                         sample_rate=48000, bit_depth=24, latency_ms=10),
            AudioDevice(name="Sony WH-1000XM5", device_type=AudioDeviceType.BLUETOOTH,
                         state=AudioDeviceState.ACTIVE, volume=70,
                         sample_rate=44100, bit_depth=16, latency_ms=40),
            AudioDevice(name="Topping DX3 Pro+", device_type=AudioDeviceType.USB_DAC,
                         state=AudioDeviceState.INACTIVE, volume=65,
                         sample_rate=192000, bit_depth=32, latency_ms=2),
            AudioDevice(name="HDMI - LG C2", device_type=AudioDeviceType.HDMI,
                         state=AudioDeviceState.UNPLUGGED, volume=50,
                         sample_rate=48000, bit_depth=16, latency_ms=20),
            AudioDevice(name="AirPods Pro", device_type=AudioDeviceType.BLUETOOTH,
                         state=AudioDeviceState.INACTIVE, volume=60,
                         sample_rate=44100, bit_depth=16, latency_ms=50),
        ]

        self.apps = [
            AudioApp(name="Spotify", pid=2001, icon="🎵", volume=85,
                      peak_volume=60.0, is_playing=True),
            AudioApp(name="Firefox", pid=2002, icon="🦊", volume=70,
                      peak_volume=25.0, is_playing=False),
            AudioApp(name="Discord", pid=2003, icon="💬", volume=90,
                      peak_volume=45.0, is_playing=True),
            AudioApp(name="OBS Studio", pid=2004, icon="📺", volume=100,
                      peak_volume=80.0, is_playing=True),
            AudioApp(name="System Sounds", pid=1, icon="🔊", volume=60,
                      peak_volume=0.0),
            AudioApp(name="Code", pid=3001, icon="📝", volume=0, muted=True),
        ]

        self.eq_bands = [
            EQBand(frequency=32, label="32", gain_db=2.0),
            EQBand(frequency=64, label="64", gain_db=3.0),
            EQBand(frequency=125, label="125", gain_db=1.0),
            EQBand(frequency=250, label="250", gain_db=-1.0),
            EQBand(frequency=500, label="500", gain_db=0.0),
            EQBand(frequency=1000, label="1K", gain_db=2.0),
            EQBand(frequency=2000, label="2K", gain_db=3.0),
            EQBand(frequency=4000, label="4K", gain_db=1.0),
            EQBand(frequency=8000, label="8K", gain_db=-1.0),
            EQBand(frequency=16000, label="16K", gain_db=-2.0),
        ]

        self.eq_presets = [
            EQPreset(name="Flat", bands=[0]*10, description="No EQ adjustments"),
            EQPreset(name="Bass Boost", bands=[6, 5, 3, 1, 0, 0, 0, 0, 0, 0],
                      description="Enhanced low frequencies"),
            EQPreset(name="Treble Boost", bands=[0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
                      description="Enhanced high frequencies"),
            EQPreset(name="Vocal", bands=[-2, -1, 0, 2, 4, 4, 2, 0, -1, -2],
                      description="Optimized for speech"),
            EQPreset(name="Rock", bands=[4, 3, 1, 0, -1, 0, 2, 3, 4, 4],
                      description="Enhanced for rock music"),
            EQPreset(name="Electronic", bands=[5, 4, 2, 0, -1, 0, 1, 3, 4, 5],
                      description="Enhanced for electronic music"),
        ]
        self.active_preset = self.eq_presets[0]

        self.effects = [
            AudioEffect(name="Noise Suppression", enabled=True,
                        parameters={"level": 0.7}),
            AudioEffect(name="Echo Cancellation", enabled=False,
                        parameters={"delay_ms": 50, "decay": 0.3}),
            AudioEffect(name="Loudness Normalization", enabled=True,
                        parameters={"target_lufs": -16.0}),
        ]

    def set_master_volume(self, volume: int) -> bool:
        self.master_volume = max(0, min(100, volume))
        return True

    def toggle_master_mute(self) -> bool:
        self.master_muted = not self.master_muted
        return self.master_muted

    def set_app_volume(self, app_name: str, volume: int) -> bool:
        app = next((a for a in self.apps if a.name == app_name), None)
        if app:
            app.volume = max(0, min(100, volume))
            return True
        return False

    def toggle_app_mute(self, app_name: str) -> bool:
        app = next((a for a in self.apps if a.name == app_name), None)
        if app:
            app.muted = not app.muted
            return True
        return False

    def set_device_volume(self, device_name: str, volume: int) -> bool:
        device = next((d for d in self.devices if d.name == device_name), None)
        if device:
            device.volume = max(0, min(100, volume))
            return True
        return False

    def set_default_device(self, device_name: str) -> bool:
        for d in self.devices:
            d.is_default = (d.name == device_name)
        return True

    def set_eq_band(self, band_index: int, gain_db: float) -> bool:
        if 0 <= band_index < len(self.eq_bands):
            band = self.eq_bands[band_index]
            band.gain_db = max(band.min_db, min(band.max_db, gain_db))
            return True
        return False

    def apply_eq_preset(self, preset_name: str) -> bool:
        preset = next((p for p in self.eq_presets if p.name == preset_name), None)
        if preset:
            self.active_preset = preset
            for i, gain in enumerate(preset.bands):
                if i < len(self.eq_bands):
                    self.eq_bands[i].gain_db = gain
            return True
        return False

    def toggle_effect(self, name: str) -> bool:
        effect = next((e for e in self.effects if e.name == name), None)
        if effect:
            effect.enabled = not effect.enabled
            return True
        return False

    def get_active_devices(self) -> List[AudioDevice]:
        return [d for d in self.devices if d.state == AudioDeviceState.ACTIVE]

    def get_playing_apps(self) -> List[AudioApp]:
        return [a for a in self.apps if a.is_playing]

    def get_stats(self) -> Dict:
        return {
            "devices": len(self.devices),
            "active_devices": len(self.get_active_devices()),
            "apps": len(self.apps),
            "playing": len(self.get_playing_apps()),
            "streams": len(self._streams),
            "eq_bands": len(self.eq_bands),
            "effects": len(self.effects),
            "master_volume": self.master_volume,
            "master_muted": self.master_muted,
            "profile": self._active_profile,
        }

    # --- backward-compat properties and methods ---

    @property
    def active_input(self) -> str:
        return self._active_input

    @active_input.setter
    def active_input(self, value: str):
        self._active_input = value

    @property
    def active_output(self) -> str:
        return self._active_output

    @active_output.setter
    def active_output(self, value: str):
        self._active_output = value

    @property
    def active_profile(self) -> str:
        return self._active_profile

    @property
    def profiles(self) -> list:
        return ["Standard", "Music", "Movie", "Gaming", "Voice"]

    @property
    def input_devices(self) -> list:
        return [d.name for d in self.devices]

    @property
    def output_devices(self) -> list:
        return [d.name for d in self.devices]

    @property
    def streams(self) -> list:
        return self._streams

    @property
    def _bass_val(self) -> float:
        return self._bass

    @property
    def _treble_val(self) -> float:
        return self._treble

    def set_bass(self, value: float) -> bool:
        self._bass = max(0.0, min(100.0, value))
        return True

    def set_treble(self, value: float) -> bool:
        self._treble = max(0.0, min(100.0, value))
        return True

    def set_balance(self, value: float) -> bool:
        self._balance = max(-100.0, min(100.0, value))
        return True

    def set_profile(self, profile: str) -> bool:
        self._active_profile = profile
        return True

    def set_input_device(self, device: str) -> bool:
        self._active_input = device
        return True

    def set_output_device(self, device: str) -> bool:
        if not any(d.name == device for d in self.devices):
            return False
        self._active_output = device
        return True

    def add_stream(self, app_id: str = "", name: str = "", color: tuple = None) -> object:
        s = type("Stream", (), {"app_id": app_id, "name": name or app_id,
                                  "volume": 50, "muted": False, "peak": 0.0,
                                  "color": color})()
        self._streams.append(s)
        return s

    def remove_stream(self, app_id: str) -> bool:
        for i, s in enumerate(self._streams):
            if s.app_id == app_id:
                self._streams.pop(i)
                return True
        return False

    def set_stream_volume(self, app_id: str, volume: int) -> bool:
        for s in self._streams:
            if s.app_id == app_id:
                s.volume = volume
                return True
        return False

    def toggle_stream_mute(self, app_id: str) -> bool:
        for s in self._streams:
            if s.app_id == app_id:
                s.muted = not s.muted
                return True
        return False

    def update_stream_peak(self, app_id: str, peak: float) -> bool:
        for s in self._streams:
            if s.app_id == app_id:
                s.peak = peak
                return True
        return False

    def toggle_night_mode(self) -> bool:
        self._night_mode = not self._night_mode
        return self._night_mode

    def toggle_spatial(self) -> bool:
        self._spatial = not self._spatial
        return self._spatial

    def to_dict(self) -> Dict:
        return {
            "master_volume": self.master_volume,
            "master_muted": self.master_muted,
            "profile": self._active_profile,
            "devices": len(self.devices),
            "active_devices": len(self.get_active_devices()),
            "apps": len(self.apps),
            "streams": len(self._streams),
            "bass": self._bass,
            "treble": self._treble,
            "balance": self._balance,
            "night_mode": self._night_mode,
            "spatial": self._spatial,
            "active_profile": self._active_profile,
        }

    def render(self, width: int = 0, height: int = 0) -> tuple:
        w = width or self._width or 400
        h = height or self._height or 600
        try:
            from PIL import Image
            img = Image.new("RGB", (w, h), (20, 20, 38))
            return list(img.tobytes()), w, h
        except ImportError:
            return [], w, h


@dataclass
class AudioStream:
    name: str = ""
    source: str = ""
    volume: float = 1.0
    muted: bool = False
    sample_rate: int = 44100


class AudioDirection:
    pass  # backward compat stub

AudioProfile = AudioDevice

# ─── Backward-compat exports ────────────────────────────────────────────
from dataclasses import dataclass as _dataclass, field as _field
from typing import Dict as _Dict

@_dataclass
class AudioProfileConfig:
    name: str = "Default"
    volume: float = 0.8
    bass: float = 0.5
    treble: float = 0.5
    balance: float = 0.0
    enabled: bool = True
    preset: str = "standard"
    custom_eq: _Dict[str, float] = _field(default_factory=dict)
