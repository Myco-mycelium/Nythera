from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math


class PadSound(Enum):
    KICK = "kick"
    SNARE = "snare"
    HI_HAT = "hihat"
    CLAP = "clap"
    RIM = "rim"
    TOM = "tom"
    CRASH = "crash"
    RIDE = "ride"
    PERC = "perc"
    BASS = "bass"
    VOX = "vox"
    SYNTH = "synth"
    FX = "fx"
    CHORD = "chord"
    STRING = "string"
    WOOD = "wood"


class PadLayout(Enum):
    GRID_4X4 = "4x4"
    GRID_3X3 = "3x3"
    GRID_2X2 = "2x2"
    LINEAR_16 = "linear-16"
    LINEAR_8 = "linear-8"


class SampleRate(Enum):
    SR_44100 = 44100
    SR_48000 = 48000
    SR_96000 = 96000


@dataclass
class DrumPadSound:
    pad_id: int
    sound_type: PadSound
    name: str
    velocity: int = 0
    volume: float = 0.8
    pitch: float = 1.0
    pan: float = 0.0
    attack: float = 0.01
    decay: float = 0.2
    sustain: float = 0.7
    release: float = 0.3
    filter_cutoff: float = 1.0
    filter_resonance: float = 0.0
    last_hit_time: float = 0
    is_muted: bool = False
    is_solo: bool = False
    color: str = "#333333"

    @property
    def velocity_bar(self) -> str:
        filled = self.velocity // 10
        return "█" * filled + "░" * (12 - filled)

    @property
    def volume_bar(self) -> str:
        filled = int(self.volume * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def icon(self) -> str:
        icons = {"kick": "🥁", "snare": "🪘", "hihat": "🔔", "clap": "👏", "rim": "🔫",
                 "tom": "🪘", "crash": "💥", "ride": "🛎️", "perc": "🫙", "bass": "🎸",
                 "vox": "🎤", "synth": "🎹", "fx": "✨", "chord": "🎵", "string": "🎻", "wood": "🪵"}
        return icons.get(self.sound_type.value, "🎵")

    @property
    def age_ms(self) -> float:
        if self.last_hit_time == 0:
            return -1
        return (time.time() - self.last_hit_time) * 1000


@dataclass
class SamplePad:
    name: str
    category: str
    bpm: int
    key: str
    duration_ms: int
    waveform: list = field(default_factory=list)

    @property
    def duration_display(self) -> str:
        if self.duration_ms >= 1000:
            return f"{self.duration_ms / 1000:.1f}s"
        return f"{self.duration_ms}ms"

    @property
    def waveform_str(self) -> str:
        chars = " ▁▂▃▄▅▆▇█"
        return "".join(chars[min(int(v * 8), 8)] for v in self.waveform[:24])


class DrumPadSampler:
    def __init__(self):
        self._pads: list[DrumPadSound] = []
        self._selected_pad: int = 0
        self._layout: PadLayout = PadLayout.GRID_4X4
        self._bpm: int = 120
        self._swing: float = 0
        self._master_volume: float = 0.8
        self._samples: list[SamplePad] = []
        self._selected_sample: int = 0
        self._is_recording: bool = False
        self._record_start: float = 0
        self._recorded_hits: list = []
        self._velocity_curve: str = "linear"
        self._view: str = "pads"
        self._create_samples()

    def _create_samples(self):
        self._pads = [
            DrumPadSound(i, PadSound.KICK if i < 4 else PadSound.SNARE if i < 8 else PadSound.HI_HAT if i < 12 else PadSound.CLAP,
                         f"Pad {i+1}", volume=0.8) for i in range(16)
        ]
        sounds = [PadSound.KICK, PadSound.SNARE, PadSound.HI_HAT, PadSound.CLAP,
                  PadSound.RIM, PadSound.TOM, PadSound.CRASH, PadSound.RIDE,
                  PadSound.PERC, PadSound.BASS, PadSound.VOX, PadSound.SYNTH,
                  PadSound.FX, PadSound.CHORD, PadSound.STRING, PadSound.WOOD]
        for i, pad in enumerate(self._pads):
            pad.sound_type = sounds[i]
            pad.name = sounds[i].value.capitalize()
        self._samples = [
            SamplePad("808 Kick", "Drums", 128, "C", 500, [0.1 + math.sin(i * 0.5) * 0.4 for i in range(24)]),
            SamplePad("Snare Hit", "Drums", 128, "C", 200, [0.8 - i * 0.03 for i in range(24)]),
            SamplePad("Hi-Hat Closed", "Drums", 128, "C", 100, [0.5 - i * 0.02 for i in range(24)]),
            SamplePad("Clap", "Drums", 120, "C", 150, [0.7 if i < 3 else 0.3 for i in range(24)]),
            SamplePad("Bass Stab", "Bass", 128, "C", 300, [0.3 + math.sin(i * 0.8) * 0.5 for i in range(24)]),
            SamplePad("Synth Chord", "Synth", 120, "Am", 400, [0.2 + math.sin(i * 0.3) * 0.6 for i in range(24)]),
            SamplePad("Vocal Chop", "Vocals", 110, "C", 250, [0.4 + math.sin(i * 1.2) * 0.4 for i in range(24)]),
            SamplePad("FX Sweep", "FX", 128, "C", 800, [i / 24 for i in range(24)]),
        ]

    @property
    def selected_pad(self) -> Optional[DrumPadSound]:
        if 0 <= self._selected_pad < len(self._pads):
            return self._pads[self._selected_pad]
        return None

    @property
    def total_pads(self) -> int:
        return len(self._pads)

    @property
    def active_pads(self) -> int:
        return sum(1 for p in self._pads if p.velocity > 0)

    def select_pad(self, idx: int):
        if 0 <= idx < len(self._pads):
            self._selected_pad = idx

    def hit_pad(self, idx: int, velocity: int = 100):
        if 0 <= idx < len(self._pads):
            pad = self._pads[idx]
            pad.velocity = velocity
            pad.last_hit_time = time.time()
            if self._is_recording:
                self._recorded_hits.append((idx, velocity, time.time()))

    def toggle_mute(self, idx: int):
        if 0 <= idx < len(self._pads):
            self._pads[idx].is_muted = not self._pads[idx].is_muted

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS DRUM PAD SAMPLER                                 ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        rec = "🔴 REC" if self._is_recording else "⏹ STOP"
        lines.append(f"  Status: {rec}  Layout: {self._layout.value}  BPM: {self._bpm}  Swing: {self._swing:.0%}")
        lines.append(f"  Volume: {self._master_volume:.0%}  Active: {self.active_pads}/{self.total_pads}  Hits: {len(self._recorded_hits)}")
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
                    vel = pad.velocity // 30
                    brightness = ["░", "▒", "▓", "█"][min(vel, 3)]
                    icon = "🟡" if active else pad.icon
                    line += f" {icon}{brightness*2}{pad.name[:4]:>4}{muted} "
            lines.append(line)
        lines.append("")
        lines.append("  ── Selected Pad ──")
        pad = self.selected_pad
        if pad:
            lines.append(f"  {pad.icon} {pad.name} ({pad.sound_type.value})")
            lines.append(f"  Velocity: [{pad.velocity_bar}] {pad.velocity}")
            lines.append(f"  Volume: [{pad.volume_bar}] {pad.volume:.0%}")
            lines.append(f"  Pitch: {pad.pitch:.1f}x  Pan: {pad.pan:+.1f}")
            lines.append(f"  ADSR: A={pad.attack:.2f} D={pad.decay:.2f} S={pad.sustain:.1f} R={pad.release:.2f}")
            lines.append(f"  Filter: {pad.filter_cutoff:.0%} cutoff  {pad.filter_resonance:.0%} res")
        lines.append("")
        lines.append("  ── Samples ──")
        for i, s in enumerate(self._samples):
            sel = "▶" if i == self._selected_sample else " "
            lines.append(f"  {sel} {s.name}  {s.category}  {s.bpm}bpm  {s.key}  {s.duration_display}")
            lines.append(f"    {s.waveform_str}")
        lines.append("")
        lines.append("  [H]it  [M]ute  [V]olume  [P]itch  [S]ample  [R]ecord  [L]ayout  [T]empo")
        return lines
