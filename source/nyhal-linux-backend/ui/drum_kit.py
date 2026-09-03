from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class DrumType(Enum):
    KICK = "kick"
    SNARE = "snare"
    HI_HAT_CLOSED = "hihat-closed"
    HI_HAT_OPEN = "hihat-open"
    RIDE = "ride"
    CRASH = "crash"
    TOM_HIGH = "tom-high"
    TOM_MID = "tom-mid"
    TOM_LOW = "tom-low"
    FLOOR_TOM = "floor-tom"
    CLAP = "clap"
    RIMSHOT = "rimshot"
    SHAKER = "shaker"
    COWBELL = "cowbell"
    TAMBOURINE = "tambourine"
    CONGA = "conga"


class VelocityCurve(Enum):
    LINEAR = "linear"
    SOFT = "soft"
    HARD = "hard"
    LOG = "logarithmic"


class RecordingMode(Enum):
    OVERDUB = "overdub"
    REPLACE = "replace"
    PUNCH_IN = "punch-in"


@dataclass
class DrumPad:
    drum_type: DrumType
    velocity: int = 0
    last_hit_time: float = 0
    is_muted: bool = False
    volume: float = 1.0
    pan: float = 0.0
    tune_cents: int = 0

    @property
    def velocity_bar(self) -> str:
        filled = self.velocity // 10
        return "█" * filled + "░" * (12 - filled)

    @property
    def icon(self) -> str:
        icons = {
            DrumType.KICK: "🥁", DrumType.SNARE: "🪘", DrumType.HI_HAT_CLOSED: "🔔",
            DrumType.HI_HAT_OPEN: "🔔", DrumType.RIDE: "🛎️", DrumType.CRASH: "💥",
            DrumType.TOM_HIGH: "🪘", DrumType.TOM_MID: "🪘", DrumType.TOM_LOW: "🪘",
            DrumType.FLOOR_TOM: "🪘", DrumType.CLAP: "👏", DrumType.RIMSHOT: "🔫",
            DrumType.SHAKER: "🫙", DrumType.COWBELL: "🐄", DrumType.TAMBOURINE: "🎶",
            DrumType.CONGA: "🪘",
        }
        return icons.get(self.drum_type, "🥁")

    @property
    def volume_bar(self) -> str:
        filled = int(self.volume * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def pan_display(self) -> str:
        pos = int((self.pan + 1) / 2 * 10)
        bar = ["L"] + ["·"] * 9 + ["R"]
        bar[pos] = "■"
        return "".join(bar)

    @property
    def age_ms(self) -> float:
        if self.last_hit_time == 0:
            return -1
        return (time.time() - self.last_hit_time) * 1000


@dataclass
class HitEvent:
    drum_type: DrumType
    velocity: int
    timestamp: float
    step: int = 0
    duration_ms: int = 0


class DrumKit:
    def __init__(self):
        self._pads: list[DrumPad] = []
        self._selected_pad: int = 0
        self._velocity_curve: VelocityCurve = VelocityCurve.LINEAR
        self._recording_mode: RecordingMode = RecordingMode.OVERDUB
        self._is_recording: bool = False
        self._record_start: float = 0
        self._recording: list[HitEvent] = []
        self._playback: list[HitEvent] = []
        self._is_playing: bool = False
        self._bpm: int = 120
        self._volume: float = 0.8
        self._reverb: float = 0.3
        self._compressor: bool = True
        self._limiter: bool = True
        self._history: list[HitEvent] = []
        self._view: str = "pads"
        self._quantize: int = 4
        self._create_samples()

    def _create_samples(self):
        self._pads = [
            DrumPad(DrumType.KICK, volume=1.0, pan=0.0),
            DrumPad(DrumType.SNARE, volume=0.9, pan=0.0),
            DrumPad(DrumType.HI_HAT_CLOSED, volume=0.7, pan=0.1),
            DrumPad(DrumType.HI_HAT_OPEN, volume=0.65, pan=0.1),
            DrumPad(DrumType.RIDE, volume=0.6, pan=-0.2),
            DrumPad(DrumType.CRASH, volume=0.8, pan=0.0),
            DrumPad(DrumType.TOM_HIGH, volume=0.75, pan=-0.3),
            DrumPad(DrumType.TOM_MID, volume=0.75, pan=-0.1),
            DrumPad(DrumType.TOM_LOW, volume=0.75, pan=0.1),
            DrumPad(DrumType.FLOOR_TOM, volume=0.8, pan=0.3),
            DrumPad(DrumType.CLAP, volume=0.85, pan=0.0),
            DrumPad(DrumType.RIMSHOT, volume=0.6, pan=0.0),
            DrumPad(DrumType.SHAKER, volume=0.5, pan=-0.1),
            DrumPad(DrumType.COWBELL, volume=0.55, pan=0.0),
            DrumPad(DrumType.TAMBOURINE, volume=0.5, pan=0.2),
            DrumPad(DrumType.CONGA, volume=0.65, pan=-0.2),
        ]

        # Simulate some hits
        now = time.time()
        self._history = [
            HitEvent(DrumType.KICK, 110, now - 120),
            HitEvent(DrumType.SNARE, 100, now - 118),
            HitEvent(DrumType.HI_HAT_CLOSED, 80, now - 119),
            HitEvent(DrumType.KICK, 105, now - 116),
            HitEvent(DrumType.SNARE, 95, now - 114),
            HitEvent(DrumType.HI_HAT_CLOSED, 75, now - 115),
            HitEvent(DrumType.CRASH, 120, now - 112),
            HitEvent(DrumType.TOM_HIGH, 90, now - 110),
            HitEvent(DrumType.TOM_LOW, 85, now - 108),
            HitEvent(DrumType.CLAP, 100, now - 106),
            HitEvent(DrumType.KICK, 115, now - 104),
            HitEvent(DrumType.SNARE, 105, now - 102),
        ]

        # Set some pad velocities from history
        for hit in self._history:
            for pad in self._pads:
                if pad.drum_type == hit.drum_type:
                    pad.velocity = hit.velocity
                    pad.last_hit_time = hit.timestamp

    @property
    def selected_pad(self) -> Optional[DrumPad]:
        if 0 <= self._selected_pad < len(self._pads):
            return self._pads[self._selected_pad]
        return None

    @property
    def total_pads(self) -> int:
        return len(self._pads)

    @property
    def total_hits(self) -> int:
        return len(self._history)

    @property
    def recording_duration(self) -> str:
        if not self._is_recording:
            return "0:00"
        elapsed = time.time() - self._record_start
        m, s = divmod(int(elapsed), 60)
        return f"{m}:{s:02d}"

    @property
    def active_pads(self) -> int:
        return sum(1 for p in self._pads if p.velocity > 0 and not p.is_muted)

    def select_pad(self, idx: int):
        if 0 <= idx < len(self._pads):
            self._selected_pad = idx

    def hit_pad(self, pad_idx: int, velocity: int = 100):
        if 0 <= pad_idx < len(self._pads):
            pad = self._pads[pad_idx]
            pad.velocity = velocity
            pad.last_hit_time = time.time()
            event = HitEvent(pad.drum_type, velocity, time.time())
            self._history.append(event)
            if self._is_recording:
                self._recording.append(event)

    def start_recording(self):
        self._is_recording = True
        self._record_start = time.time()
        self._recording.clear()

    def stop_recording(self):
        self._is_recording = False

    def toggle_mute(self, pad_idx: int):
        if 0 <= pad_idx < len(self._pads):
            self._pads[pad_idx].is_muted = not self._pads[pad_idx].is_muted

    def set_pad_volume(self, pad_idx: int, volume: float):
        if 0 <= pad_idx < len(self._pads):
            self._pads[pad_idx].volume = max(0, min(1, volume))

    def set_pad_pan(self, pad_idx: int, pan: float):
        if 0 <= pad_idx < len(self._pads):
            self._pads[pad_idx].pan = max(-1, min(1, pan))

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                      NYRQIS DRUM KIT                                       ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        rec = "🔴 REC" if self._is_recording else "⏹ STOP"
        play = "▶" if self._is_playing else "⏹"
        lines.append(f"  Status: {rec} {play}  BPM: {self._bpm}  Duration: {self.recording_duration}")
        lines.append(f"  Volume: {self._volume:.0%}  Reverb: {self._reverb:.0%}  Compressor: {'ON' if self._compressor else 'OFF'}  Limiter: {'ON' if self._limiter else 'OFF'}")
        lines.append(f"  Velocity Curve: {self._velocity_curve.value}  Quantize: 1/{self._quantize}")
        lines.append(f"  Pads: {self.active_pads}/{self.total_pads} active  Total Hits: {self.total_hits}  Recording: {len(self._recording)} hits")
        lines.append("")
        lines.append("  ── 4×4 Pad Grid ──")
        for row in range(4):
            line = "  "
            for col in range(4):
                idx = row * 4 + col
                if idx < len(self._pads):
                    pad = self._pads[idx]
                    active = idx == self._selected_pad
                    muted = "🔇" if pad.is_muted else ""
                    velocity = pad.velocity // 10
                    icon = "🟡" if active else pad.icon
                    line += f" {icon}{pad.drum_type.value[:6]:>6} "
            lines.append(line)
        lines.append("")
        lines.append("  ── Pad Details ──")
        pad = self.selected_pad
        if pad:
            lines.append(f"  {pad.icon} {pad.drum_type.value}")
            lines.append(f"  Velocity: [{pad.velocity_bar}] {pad.velocity}")
            lines.append(f"  Volume: [{pad.volume_bar}] {pad.volume:.0%}")
            lines.append(f"  Pan: {pad.pan_display}")
            lines.append(f"  Tune: {pad.tune_cents:+d} cents")
            lines.append(f"  Muted: {'Yes' if pad.is_muted else 'No'}")
            lines.append(f"  Last Hit: {pad.age_ms:.0f}ms ago" if pad.age_ms >= 0 else "  Last Hit: Never")
        lines.append("")
        lines.append("  ── Recent History ──")
        for hit in self._history[-6:]:
            age = (time.time() - hit.timestamp) * 1000
            lines.append(f"  {hit.drum_type.value:<16s} vel={hit.velocity:3d}  {age:.0f}ms ago")
        lines.append("")
        lines.append("  [H]it  [R]ecord  [S]top  [M]ute  [V]olume  [P]an  [C]urve  [Q]uantize")
        return lines

    def render_pads_grid(self) -> list:
        lines = []
        lines.append("  ── Pad Grid (Performance View) ──")
        lines.append("")
        for row in range(4):
            line = "  "
            for col in range(4):
                idx = row * 4 + col
                if idx < len(self._pads):
                    pad = self._pads[idx]
                    vel_level = pad.velocity // 30
                    brightness = ["░", "▒", "▓", "█"][min(vel_level, 3)]
                    icon = "🟢" if pad.velocity > 0 else "⬛"
                    muted = " X" if pad.is_muted else ""
                    line += f" {icon}{brightness*2}{pad.drum_type.value[:4]:>4}{muted} "
            lines.append(line)
        return lines

    def render_recording(self) -> list:
        lines = []
        lines.append(f"  ── Recording ({len(self._recording)} hits) ──")
        lines.append("")
        for i, hit in enumerate(self._recording):
            age = (hit.timestamp - self._record_start)
            lines.append(f"  {i:3d} │ {hit.drum_type.value:<16s} vel={hit.velocity:3d}  t={age:.2f}s")
        if not self._recording:
            lines.append("  (no recorded hits yet)")
        return lines
