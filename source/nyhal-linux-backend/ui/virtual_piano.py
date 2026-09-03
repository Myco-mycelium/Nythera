from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math


class NoteName(Enum):
    C = "C"
    C_SHARP = "C#"
    D = "D"
    D_SHARP = "D#"
    E = "E"
    F = "F"
    F_SHARP = "F#"
    G = "G"
    G_SHARP = "G#"
    A = "A"
    A_SHARP = "A#"
    B = "B"


class PianoEffect(Enum):
    REVERB = "reverb"
    CHORUS = "chorus"
    DELAY = "delay"
    TREMOLO = "tremolo"
    OVERDRIVE = "overdrive"
    EQ = "eq"


class PianoScale(Enum):
    CHROMATIC = "chromatic"
    MAJOR = "major"
    MINOR = "minor"
    PENTATONIC = "pentatonic"
    BLUES = "blues"
    DORIAN = "dorian"
    MIXOLYDIAN = "mixolydian"


@dataclass
class PianoNote:
    note_name: NoteName
    octave: int
    velocity: int
    timestamp: float
    duration_ms: int = 0
    channel: int = 0

    @property
    def midi_number(self) -> int:
        note_vals = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
        return note_vals[self.note_name.value] + (self.octave + 1) * 12

    @property
    def display_name(self) -> str:
        return f"{self.note_name.value}{self.octave}"

    @property
    def is_black_key(self) -> bool:
        return "#" in self.note_name.value


@dataclass
class PianoEffectInstance:
    effect_type: PianoEffect
    mix: float
    enabled: bool = True
    params: dict = field(default_factory=dict)

    @property
    def mix_bar(self) -> str:
        filled = int(self.mix * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class PianoRecording:
    name: str
    notes: list = field(default_factory=list)
    timestamp: float = 0
    duration_ms: int = 0
    bpm: int = 120

    @property
    def note_count(self) -> int:
        return len(self.notes)

    @property
    def display_duration(self) -> str:
        if self.duration_ms >= 60000:
            m, s = divmod(self.duration_ms // 1000, 60)
            return f"{m}:{s:02d}"
        return f"{self.duration_ms / 1000:.1f}s"


@dataclass
class PresetSound:
    name: str
    waveform: str
    attack: float
    decay: float
    sustain: float
    release: float
    effects: list = field(default_factory=list)


class VirtualPiano:
    def __init__(self):
        self._octave: int = 4
        self._velocity: int = 100
        self._sustain: bool = False
        self._active_notes: list[PianoNote] = []
        self._effects: list[PianoEffectInstance] = []
        self._recordings: list[PianoRecording] = []
        self._selected_recording: int = 0
        self._current_recording: Optional[PianoRecording] = None
        self._is_recording: bool = False
        self._is_playing: bool = False
        self._scale: PianoScale = PianoScale.CHROMATIC
        self._volume: float = 0.8
        self._pan: float = 0.0
        self._transposition: int = 0
        self._view: str = "piano"
        self._presets: list[PresetSound] = []
        self._selected_preset: int = 0
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._effects = [
            PianoEffectInstance(PianoEffect.REVERB, 0.3),
            PianoEffectInstance(PianoEffect.CHORUS, 0.15),
        ]
        self._recordings = [
            PianoRecording("Morning Melody", [
                PianoNote(NoteName.E, 4, 80, now - 3600, 500),
                PianoNote(NoteName.G, 4, 90, now - 3500, 400),
                PianoNote(NoteName.A, 4, 85, now - 3100, 600),
                PianoNote(NoteName.G, 4, 80, now - 2500, 300),
                PianoNote(NoteName.E, 4, 75, now - 2200, 500),
            ], now - 3600, 3200, 90),
            PianoRecording("Chord Progression", [
                PianoNote(NoteName.C, 4, 100, now - 7200, 1000),
                PianoNote(NoteName.E, 4, 90, now - 7200, 1000),
                PianoNote(NoteName.G, 4, 85, now - 7200, 1000),
            ], now - 7200, 4000, 120),
            PianoRecording("Scale Practice", [
                PianoNote(NoteName.C, 4, 80, now - 86400, 300),
                PianoNote(NoteName.D, 4, 82, now - 86400 + 300, 300),
                PianoNote(NoteName.E, 4, 84, now - 86400 + 600, 300),
                PianoNote(NoteName.F, 4, 86, now - 86400 + 900, 300),
                PianoNote(NoteName.G, 4, 88, now - 86400 + 1200, 300),
            ], now - 86400, 2500, 100),
        ]
        self._presets = [
            PresetSound("Grand Piano", "sine", 0.01, 0.3, 0.7, 0.5, ["reverb"]),
            PresetSound("Electric Piano", "triangle", 0.005, 0.2, 0.6, 0.3, ["chorus", "reverb"]),
            PresetSound("Organ", "sawtooth", 0.001, 0.1, 0.9, 0.1, ["tremolo"]),
            PresetSound("Synth Lead", "sawtooth", 0.01, 0.1, 0.8, 0.2, ["overdrive", "delay"]),
            PresetSound("Harp", "sine", 0.02, 0.5, 0.4, 0.8, ["reverb"]),
            PresetSound("Clavinet", "square", 0.005, 0.15, 0.5, 0.15, ["wah"]),
        ]

    @property
    def selected_recording(self) -> Optional[PianoRecording]:
        if 0 <= self._selected_recording < len(self._recordings):
            return self._recordings[self._selected_recording]
        return None

    @property
    def selected_preset(self) -> Optional[PresetSound]:
        if 0 <= self._selected_preset < len(self._presets):
            return self._presets[self._selected_preset]
        return None

    @property
    def total_recordings(self) -> int:
        return len(self._recordings)

    @property
    def active_notes_count(self) -> int:
        return len(self._active_notes)

    def note_on(self, note_name: NoteName, octave: int, velocity: int = 100):
        note = PianoNote(note_name, octave, velocity, time.time())
        self._active_notes.append(note)
        if self._is_recording and self._current_recording:
            self._current_recording.notes.append(note)

    def note_off(self, note_name: NoteName, octave: int):
        self._active_notes = [n for n in self._active_notes if not (n.note_name == note_name and n.octave == octave)]

    def select_recording(self, idx: int):
        if 0 <= idx < len(self._recordings):
            self._selected_recording = idx

    def start_recording(self):
        self._is_recording = True
        self._current_recording = PianoRecording("New Recording", timestamp=time.time(), bpm=120)

    def stop_recording(self):
        self._is_recording = False
        if self._current_recording and self._current_recording.notes:
            self._current_recording.duration_ms = int((time.time() - self._current_recording.timestamp) * 1000)
            self._recordings.append(self._current_recording)
        self._current_recording = None

    def toggle_sustain(self):
        self._sustain = not self._sustain

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS VIRTUAL PIANO                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        rec = "🔴 REC" if self._is_recording else "⏹ STOP"
        sus = "🔘 Sustain ON" if self._sustain else "○ Sustain OFF"
        preset = self._presets[self._selected_preset].name if self._presets else "None"
        lines.append(f"  Status: {rec}  Preset: {preset}  Octave: {self._octave}  Velocity: {self._velocity}")
        lines.append(f"  Volume: {self._volume:.0%}  Pan: {self._pan:+.1f}  Transpose: {self._transposition:+d}  Scale: {self._scale.value}")
        lines.append(f"  Active: {self.active_notes_count} notes  {sus}")
        lines.append("")
        lines.append("  ── Keyboard ──")
        white_notes = ["C", "D", "E", "F", "G", "A", "B"]
        black_notes = {"C#": 1, "D#": 3, "F#": 6, "G#": 8, "A#": 10}
        # White keys
        line1 = "  "
        for note in white_notes:
            nn = NoteName(note)
            active = any(n.note_name == nn and n.octave == self._octave for n in self._active_notes)
            key = "██" if active else "░░"
            line1 += f"{key}{note}{self._octave} "
        lines.append(line1)
        # Black keys
        line2 = "  "
        valid_sharps = {"C#": NoteName.C_SHARP, "D#": NoteName.D_SHARP, "F#": NoteName.F_SHARP, "G#": NoteName.G_SHARP, "A#": NoteName.A_SHARP}
        for i in range(7):
            note = white_notes[i]
            sharp = f"{note}#"
            if sharp in valid_sharps:
                nn = valid_sharps[sharp]
                active = any(n.note_name == nn and n.octave == self._octave for n in self._active_notes)
                key = "█" if active else "░"
                line2 += f" {key}  "
            else:
                line2 += "    "
        lines.append(line2)
        lines.append("")
        lines.append("  ── Effects ──")
        for eff in self._effects:
            status = "🟢" if eff.enabled else "⚪"
            lines.append(f"  {status} {eff.effect_type.value:<12s} [{eff.mix_bar}] {eff.mix:.0%}")
        lines.append("")
        lines.append("  ── Recordings ──")
        for i, rec in enumerate(self._recordings):
            sel = "▶" if i == self._selected_recording else " "
            lines.append(f"  {sel} {rec.name}  {rec.display_duration}  {rec.note_count} notes  {rec.bpm}bpm")
        lines.append("")
        lines.append("  [N]ote on/off  [R]ecord  [S]top  [S]ustain  [E]ffects  [P]reset  [O]ctave")
        return lines

    def render_recordings(self) -> list:
        rec = self.selected_recording
        if not rec:
            return ["  No recording selected"]
        lines = []
        lines.append(f"  ── {rec.name} ──")
        lines.append(f"  Duration: {rec.display_duration}  Notes: {rec.note_count}  BPM: {rec.bpm}")
        lines.append("")
        for i, note in enumerate(rec.notes):
            age = (note.timestamp - rec.timestamp)
            lines.append(f"  {i:3d} │ {note.display_name:<6s} vel={note.velocity:3d}  t={age:.2f}s  {'⌨' if note.is_black_key else ' piano'}")
        return lines

    def render_presets(self) -> list:
        lines = []
        lines.append("  ── Sound Presets ──")
        lines.append("")
        for i, p in enumerate(self._presets):
            sel = "▶" if i == self._selected_preset else " "
            lines.append(f"  {sel} {p.name}  ({p.waveform})  A:{p.attack:.2f} D:{p.decay:.2f} S:{p.sustain:.1f} R:{p.release:.2f}")
            if p.effects:
                lines.append(f"    Effects: {', '.join(p.effects)}")
        return lines
