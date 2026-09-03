"""AudioMixer — Audio management UI for Nyrqis.

Provides a complete audio management interface:
- Master volume control
- Per-app volume sliders
- Output device selection (speakers, headphones, HDMI, Bluetooth)
- Input device selection (microphone)
- Audio profiles (music, movie, voice, gaming)
- Mute/unmute per app and master
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AudioDeviceType(Enum):
    SPEAKERS = auto()
    HEADPHONES = auto()
    HDMI = auto()
    BLUETOOTH = auto()
    USB = auto()
    ANALOG = auto()


class AudioDirection(Enum):
    OUTPUT = auto()
    INPUT = auto()


class AudioProfile(Enum):
    CUSTOM = auto()
    MUSIC = auto()
    MOVIE = auto()
    VOICE = auto()
    GAMING = auto()
    FLAT = auto()  # no EQ


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AudioDevice:
    """An audio device (input or output)."""
    id: str
    name: str
    device_type: AudioDeviceType
    direction: AudioDirection
    volume: int = 100  # 0-100
    muted: bool = False
    active: bool = False
    channels: int = 2
    sample_rate: int = 44100
    description: str = ""


@dataclass
class AudioStream:
    """An application audio stream."""
    app_id: str
    app_name: str
    icon_color: Tuple[int, int, int, int] = (180, 180, 200, 255)
    volume: int = 100  # 0-100
    muted: bool = False
    peak: float = 0.0  # 0.0-1.0 current audio level
    playing: bool = True


@dataclass
class AudioProfileConfig:
    """Audio profile settings."""
    name: str
    profile: AudioProfile
    master_volume: int = 80
    bass: int = 50       # 0-100
    treble: int = 50
    balance: int = 50    # 0=left, 50=center, 100=right
    spatial: bool = False
    night_mode: bool = False  # compress dynamic range


# ---------------------------------------------------------------------------
# AudioMixer
# ---------------------------------------------------------------------------

class AudioMixer:
    """Audio management UI for Nyrqis.

    Provides master volume, per-app mixing, device selection,
    and audio profiles.

    Parameters
    ----------
    width, height : int
        Rendering dimensions.
    """

    # Built-in profiles
    BUILTIN_PROFILES = [
        AudioProfileConfig("Flat", AudioProfile.FLAT,
                          bass=50, treble=50),
        AudioProfileConfig("Music", AudioProfile.MUSIC,
                          bass=65, treble=55),
        AudioProfileConfig("Movie", AudioProfile.MOVIE,
                          bass=70, treble=45, spatial=True),
        AudioProfileConfig("Voice", AudioProfile.VOICE,
                          bass=30, treble=70),
        AudioProfileConfig("Gaming", AudioProfile.GAMING,
                          bass=60, treble=60, spatial=True),
    ]

    def __init__(self, width: int = 400, height: int = 600):
        self.width = width
        self.height = height

        # Master volume
        self._master_volume = 80
        self._master_muted = False

        # Devices
        self._output_devices: List[AudioDevice] = []
        self._input_devices: List[AudioDevice] = []
        self._active_output: Optional[str] = None
        self._active_input: Optional[str] = None

        # App streams
        self._streams: List[AudioStream] = []

        # Profiles
        self._profiles = list(self.BUILTIN_PROFILES)
        self._active_profile = AudioProfile.FLAT

        # EQ state
        self._bass = 50
        self._treble = 50
        self._balance = 50
        self._spatial = False
        self._night_mode = False

        # UI state
        self._tab = "output"  # output, input, apps, profiles

        # Initialize devices
        self._init_devices()

    def _init_devices(self) -> None:
        """Initialize simulated audio devices."""
        self._output_devices = [
            AudioDevice("speakers", "Built-in Speakers",
                       AudioDeviceType.SPEAKERS, AudioDirection.OUTPUT,
                       active=True, channels=2, sample_rate=48000,
                       description="Realtek ALC256"),
            AudioDevice("headphones", "Headphones",
                       AudioDeviceType.HEADPHONES, AudioDirection.OUTPUT,
                       channels=2, sample_rate=44100,
                       description="3.5mm analog jack"),
            AudioDevice("hdmi", "HDMI Output",
                       AudioDeviceType.HDMI, AudioDirection.OUTPUT,
                       channels=8, sample_rate=48000,
                       description="Intel HD Audio"),
            AudioDevice("bluetooth", "AirPods Pro",
                       AudioDeviceType.BLUETOOTH, AudioDirection.OUTPUT,
                       channels=2, sample_rate=44100,
                       description="AAC codec"),
        ]
        self._active_output = "speakers"

        self._input_devices = [
            AudioDevice("mic-internal", "Built-in Microphone",
                       AudioDeviceType.ANALOG, AudioDirection.INPUT,
                       active=True, channels=1, sample_rate=44100,
                       description="Realtek ALC256"),
            AudioDevice("mic-usb", "USB Microphone",
                       AudioDeviceType.USB, AudioDirection.INPUT,
                       channels=1, sample_rate=48000,
                       description="Blue Yeti"),
        ]
        self._active_input = "mic-internal"

        # Default app streams
        self._streams = [
            AudioStream("firefox", "Firefox", (255, 120, 60, 255),
                       volume=70, peak=0.3),
            AudioStream("spotify", "Spotify", (30, 215, 96, 255),
                       volume=85, peak=0.6),
            AudioStream("terminal", "Terminal", (60, 200, 120, 255),
                       volume=50, muted=True),
        ]

    # -- Master volume --------------------------------------------------

    @property
    def master_volume(self) -> int:
        return self._master_volume

    @property
    def master_muted(self) -> bool:
        return self._master_muted

    def set_master_volume(self, volume: int) -> None:
        self._master_volume = max(0, min(100, volume))

    def toggle_master_mute(self) -> bool:
        self._master_muted = not self._master_muted
        return self._master_muted

    # -- Device management ----------------------------------------------

    @property
    def output_devices(self) -> List[AudioDevice]:
        return list(self._output_devices)

    @property
    def input_devices(self) -> List[AudioDevice]:
        return list(self._input_devices)

    @property
    def active_output(self) -> Optional[AudioDevice]:
        for d in self._output_devices:
            if d.id == self._active_output:
                return d
        return None

    @property
    def active_input(self) -> Optional[AudioDevice]:
        for d in self._input_devices:
            if d.id == self._active_input:
                return d
        return None

    def set_output_device(self, device_id: str) -> bool:
        for d in self._output_devices:
            d.active = (d.id == device_id)
            if d.active:
                self._active_output = device_id
        return self._active_output == device_id

    def set_input_device(self, device_id: str) -> bool:
        for d in self._input_devices:
            d.active = (d.id == device_id)
            if d.active:
                self._active_input = device_id
        return self._active_input == device_id

    def set_device_volume(self, device_id: str, volume: int) -> bool:
        for d in self._output_devices + self._input_devices:
            if d.id == device_id:
                d.volume = max(0, min(100, volume))
                return True
        return False

    def toggle_device_mute(self, device_id: str) -> Optional[bool]:
        for d in self._output_devices + self._input_devices:
            if d.id == device_id:
                d.muted = not d.muted
                return d.muted
        return None

    # -- App streams ----------------------------------------------------

    @property
    def streams(self) -> List[AudioStream]:
        return list(self._streams)

    def add_stream(self, app_id: str, app_name: str,
                   icon_color: Tuple[int, int, int, int] = (180, 180, 200, 255)) -> AudioStream:
        stream = AudioStream(app_id, app_name, icon_color)
        self._streams.append(stream)
        return stream

    def remove_stream(self, app_id: str) -> bool:
        before = len(self._streams)
        self._streams = [s for s in self._streams if s.app_id != app_id]
        return len(self._streams) < before

    def set_stream_volume(self, app_id: str, volume: int) -> bool:
        for s in self._streams:
            if s.app_id == app_id:
                s.volume = max(0, min(100, volume))
                return True
        return False

    def toggle_stream_mute(self, app_id: str) -> Optional[bool]:
        for s in self._streams:
            if s.app_id == app_id:
                s.muted = not s.muted
                return s.muted
        return None

    def update_stream_peak(self, app_id: str, peak: float) -> None:
        """Update the audio peak level for a stream (for VU meter)."""
        for s in self._streams:
            if s.app_id == app_id:
                s.peak = max(0.0, min(1.0, peak))

    # -- Profiles -------------------------------------------------------

    @property
    def profiles(self) -> List[AudioProfileConfig]:
        return list(self._profiles)

    @property
    def active_profile(self) -> AudioProfile:
        return self._active_profile

    def set_profile(self, profile: AudioProfile) -> bool:
        for p in self._profiles:
            if p.profile == profile:
                self._active_profile = profile
                self._bass = p.bass
                self._treble = p.treble
                self._spatial = p.spatial
                self._night_mode = p.night_mode
                return True
        return False

    def set_bass(self, value: int) -> None:
        self._bass = max(0, min(100, value))

    def set_treble(self, value: int) -> None:
        self._treble = max(0, min(100, value))

    def set_balance(self, value: int) -> None:
        self._balance = max(0, min(100, value))

    def toggle_spatial(self) -> bool:
        self._spatial = not self._spatial
        return self._spatial

    def toggle_night_mode(self) -> bool:
        self._night_mode = not self._night_mode
        return self._night_mode

    # -- Statistics -----------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        playing = sum(1 for s in self._streams if s.playing and not s.muted)
        return {
            "master_volume": self._master_volume,
            "master_muted": self._master_muted,
            "active_output": self._active_output,
            "active_input": self._active_input,
            "output_devices": len(self._output_devices),
            "input_devices": len(self._input_devices),
            "streams": len(self._streams),
            "playing": playing,
            "profile": self._active_profile.name,
            "spatial": self._spatial,
        }

    # -- Rendering ------------------------------------------------------

    def render(self) -> Tuple[bytes, int, int]:
        """Render the audio mixer UI to an RGB byte buffer."""
        w, h = self.width, self.height
        buf = bytearray(w * h * 3)
        bg = (30, 30, 40)
        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        # Header
        self._fill_rect(buf, w, 0, 0, w, 48, (42, 42, 56))

        # Master volume bar
        bar_y = 60
        bar_w = w - 40
        bar_h = 24
        self._fill_rect(buf, w, 20, bar_y, bar_w, bar_h, (50, 50, 65))
        fill_w = int(bar_w * self._master_volume / 100)
        fill_color = (255, 80, 80) if self._master_volume > 90 else (
            (255, 200, 60) if self._master_volume > 70 else (80, 200, 120))
        if self._master_muted:
            fill_color = (100, 100, 120)
        self._fill_rect(buf, w, 20, bar_y, fill_w, bar_h, fill_color)

        # App streams
        y = bar_y + bar_h + 24
        for stream in self._streams:
            # Stream name placeholder
            name_color = stream.icon_color[:3]
            self._fill_rect(buf, w, 20, y, 16, 16, name_color)

            # Volume slider
            slider_y = y + 22
            slider_w = w - 120
            self._fill_rect(buf, w, 20, slider_y, slider_w, 10, (50, 50, 65))
            vol_w = int(slider_w * stream.volume / 100)
            vol_color = (80, 140, 255) if not stream.muted else (80, 80, 100)
            self._fill_rect(buf, w, 20, slider_y, vol_w, 10, vol_color)

            y += 64

        return bytes(buf), w, h

    # -- Serialization --------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "master_volume": self._master_volume,
            "master_muted": self._master_muted,
            "active_output": self._active_output,
            "active_input": self._active_input,
            "streams": len(self._streams),
            "profile": self._active_profile.name,
        }

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


__all__ = [
    "AudioMixer", "AudioDevice", "AudioStream", "AudioDeviceType",
    "AudioDirection", "AudioProfile", "AudioProfileConfig",
]
