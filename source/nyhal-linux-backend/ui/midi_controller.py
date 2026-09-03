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


class Octave(Enum):
    C2 = 2
    C3 = 3
    C4 = 4  # Middle C
    C5 = 5
    C6 = 6


class InstrumentType(Enum):
    PIANO = "piano"
    GUITAR = "guitar"
    BASS = "bass"
    DRUMS = "drums"
    SYNTH = "synth"
    STRINGS = "strings"
    BRASS = "brass"
    WOODWIND = "woodwind"
    PERCUSSION = "percussion"
    FX = "fx"


class ChannelMode(Enum):
    MONO = "mono"
    POLY = "poly"
    LEGATO = "legato"


class TimeSignature(Enum):
    FOUR_FOUR = "4/4"
    THREE_FOUR = "3/4"
    SIX_EIGHT = "6/8"
    FIVE_FOUR = "5/4"
    SEVEN_EIGHT = "7/8"


class VelocityCurve(Enum):
    LINEAR = "linear"
    SOFT = "soft"
    HARD = "hard"
    EXPONENTIAL = "exponential"


@dataclass
class MidiNote:
    note_name: NoteName
    octave: int
    velocity: int
    start_step: int
    duration_steps: int
    channel: int = 0
    is_active: bool = True

    @property
    def midi_number(self) -> int:
        note_vals = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
        return note_vals[self.note_name.value] + (self.octave + 1) * 12

    @property
    def display_name(self) -> str:
        return f"{self.note_name.value}{self.octave}"

    @property
    def velocity_display(self) -> str:
        if self.velocity >= 110:
            return "fff"
        if self.velocity >= 90:
            return "ff"
        if self.velocity >= 70:
            return "f"
        if self.velocity >= 50:
            return "mf"
        if self.velocity >= 30:
            return "mp"
        if self.velocity >= 15:
            return "p"
        return "pp"


@dataclass
class InstrumentPreset:
    name: str
    instrument_type: InstrumentType
    channel: int
    volume: float
    pan: float
    attack_ms: float
    release_ms: float
    cutoff_hz: float
    resonance: float
    effects: list = field(default_factory=list)

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


@dataclass
class SequencerStep:
    step: int
    notes: list = field(default_factory=list)
    accent: bool = False
    skip: bool = False

    @property
    def note_names(self) -> str:
        return " ".join(n.display_name for n in self.notes) if self.notes else "·"


@dataclass
class Scale:
    name: str
    intervals: list
    root: NoteName = NoteName.C

    @property
    def notes(self) -> list:
        result = []
        for interval in self.intervals:
            note_idx = (NoteName[self.root.value].value if hasattr(NoteName[self.root.value], 'value') else 0) + interval
            # Simplified: just return interval names
            result.append(interval % 12)
        return result


class MidiController:
    def __init__(self):
        self._presets: list[InstrumentPreset] = []
        self._selected_preset: int = 0
        self._sequencer_steps: list[SequencerStep] = []
        self._total_steps: int = 16
        self._current_step: int = 0
        self._is_playing: bool = False
        self._bpm: int = 120
        self._time_signature: TimeSignature = TimeSignature.FOUR_FOUR
        self._swing: float = 0.0
        self._velocity_curve: VelocityCurve = VelocityCurve.LINEAR
        self._octave: int = 4
        self._channel: int = 0
        self._velocity: int = 100
        self._active_notes: list[MidiNote] = []
        self._history: list = []
        self._view: str = "keyboard"
        self._keyboard_low: int = 36  # C3
        self._keyboard_high: int = 84  # C6
        self._quantize: int = 1
        self._scales: list[Scale] = [
            Scale("Major", [0, 2, 4, 5, 7, 9, 11]),
            Scale("Minor", [0, 2, 3, 5, 7, 8, 10]),
            Scale("Dorian", [0, 2, 3, 5, 7, 9, 10]),
            Scale("Mixolydian", [0, 2, 4, 5, 7, 9, 10]),
            Scale("Pentatonic", [0, 2, 4, 7, 9]),
            Scale("Blues", [0, 3, 5, 6, 7, 10]),
            Scale("Chromatic", list(range(12))),
        ]
        self._create_samples()

    def _create_samples(self):
        self._presets = [
            InstrumentPreset("Grand Piano", InstrumentType.PIANO, 0, 0.85, 0.0, 5, 200, 2000, 0.3, ["reverb", "chorus"]),
            InstrumentPreset("Electric Bass", InstrumentType.BASS, 1, 0.75, -0.2, 10, 150, 1500, 0.5, ["compressor"]),
            InstrumentPreset("Distortion Guitar", InstrumentType.GUITAR, 2, 0.65, 0.1, 5, 180, 3000, 0.7, ["distortion", "delay"]),
            InstrumentPreset("Lead Synth", InstrumentType.SYNTH, 3, 0.70, 0.0, 15, 300, 4000, 0.8, ["filter", "reverb"]),
            InstrumentPreset("String Ensemble", InstrumentType.STRINGS, 4, 0.60, 0.0, 500, 800, 8000, 0.2, ["reverb", "chorus"]),
            InstrumentPreset("Drum Kit", InstrumentType.DRUMS, 9, 0.80, 0.0, 1, 100, 5000, 0.4, ["compressor", "eq"]),
            InstrumentPreset("Trumpet", InstrumentType.BRASS, 5, 0.70, 0.3, 20, 250, 3500, 0.4, ["reverb"]),
            InstrumentPreset("Flute", InstrumentType.WOODWIND, 6, 0.55, -0.1, 30, 400, 6000, 0.2, ["reverb"]),
        ]

        # Initialize sequencer steps
        for i in range(self._total_steps):
            self._sequencer_steps.append(SequencerStep(i))

        # Add some notes to the sequencer
        bass_notes = [MidiNote(NoteName.E, 3, 100, 0, 2), MidiNote(NoteName.E, 3, 80, 4, 2),
                      MidiNote(NoteName.A, 3, 100, 8, 2), MidiNote(NoteName.A, 3, 80, 12, 2)]
        for note in bass_notes:
            self._sequencer_steps[note.start_step].notes.append(note)

        drum_notes = [
            MidiNote(NoteName.C, 2, 120, 0, 1), MidiNote(NoteName.C, 2, 90, 4, 1),
            MidiNote(NoteName.C, 2, 100, 8, 1), MidiNote(NoteName.C, 2, 90, 12, 1),
            MidiNote(NoteName.F_SHARP, 3, 100, 0, 1), MidiNote(NoteName.F_SHARP, 3, 70, 2, 1),
            MidiNote(NoteName.F_SHARP, 3, 100, 8, 1), MidiNote(NoteName.F_SHARP, 3, 70, 10, 1),
        ]
        for note in drum_notes:
            self._sequencer_steps[note.start_step].notes.append(note)

    @property
    def selected_preset(self) -> Optional[InstrumentPreset]:
        if 0 <= self._selected_preset < len(self._presets):
            return self._presets[self._selected_preset]
        return None

    @property
    def total_presets(self) -> int:
        return len(self._presets)

    @property
    def steps_with_notes(self) -> int:
        return sum(1 for s in self._sequencer_steps if s.notes)

    @property
    def total_notes(self) -> int:
        return sum(len(s.notes) for s in self._sequencer_steps)

    @property
    def bar_width(self) -> int:
        ts_div = {"4/4": 4, "3/4": 3, "6/8": 6, "5/4": 5, "7/8": 7}
        return ts_div.get(self._time_signature.value, 4)

    def select_preset(self, idx: int):
        if 0 <= idx < len(self._presets):
            self._selected_preset = idx

    def note_on(self, note_name: NoteName, octave: int, velocity: int = 100):
        note = MidiNote(note_name, octave, velocity, self._current_step, 1, self._channel)
        self._active_notes.append(note)
        self._history.append(f"Note ON: {note_name.value}{octave} vel={velocity}")

    def note_off(self, note_name: NoteName, octave: int):
        self._active_notes = [n for n in self._active_notes if not (n.note_name == note_name and n.octave == octave)]

    def add_note_to_step(self, step: int, note: MidiNote):
        if 0 <= step < len(self._sequencer_steps):
            self._sequencer_steps[step].notes.append(note)

    def remove_note_from_step(self, step: int, note_idx: int) -> bool:
        if 0 <= step < len(self._sequencer_steps):
            notes = self._sequencer_steps[step].notes
            if 0 <= note_idx < len(notes):
                notes.pop(note_idx)
                return True
        return False

    def clear_step(self, step: int):
        if 0 <= step < len(self._sequencer_steps):
            self._sequencer_steps[step].notes.clear()

    def start_playback(self):
        self._is_playing = True
        self._current_step = 0

    def stop_playback(self):
        self._is_playing = False
        self._current_step = 0

    def toggle_step(self, step: int):
        if 0 <= step < len(self._sequencer_steps):
            self._sequencer_steps[step].accent = not self._sequencer_steps[step].accent

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS MIDI CONTROLLER                                 ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        play = "▶ PLAYING" if self._is_playing else "⏹ STOPPED"
        lines.append(f"  Status: {play}  BPM: {self._bpm}  Time: {self._time_signature.value}  Swing: {self._swing:.0%}")
        lines.append(f"  Velocity: {self._velocity}  Octave: {self._octave}  Channel: {self._channel}")
        lines.append("")
        lines.append("  ── Instruments ──")
        for i, p in enumerate(self._presets):
            sel = "▶" if i == self._selected_preset else " "
            lines.append(f"  {sel} {p.name}  [{p.volume_bar}]  Ch:{p.channel}  {p.pan_display}")
        lines.append("")
        lines.append("  ── Sequencer ──")
        seq_line = "  "
        for i in range(self._total_steps):
            step = self._sequencer_steps[i]
            marker = "▼" if i == self._current_step else " "
            note_str = step.note_names if step.notes else "·"
            accent = "●" if step.accent else " "
            if i % self.bar_width == 0:
                seq_line += "│"
            seq_line += f"{accent}{note_str[:2]:>2}"
        lines.append(seq_line)
        lines.append("")
        lines.append("  ── Keyboard ──")
        lines.append(self._render_keyboard())
        lines.append("")
        lines.append("  [P]lay  [S]top  [B]pm  [V]elocity  [O]ctave  [N]ote on/off  [E]dit step")
        return lines

    def _render_keyboard(self) -> str:
        white_notes = ["C", "D", "E", "F", "G", "A", "B"]
        black_notes = ["C#", "D#", None, "F#", "G#", "A#", None]
        result = "  "
        for octave in range(self._octave, self._octave + 2):
            for i, note in enumerate(white_notes):
                nn = NoteName(note)
                active = any(n.note_name == nn and n.octave == octave for n in self._active_notes)
                key = "█" if active else "░"
                result += f"{note}{octave}"
                if black_notes[i]:
                    result += " "
        return result

    def render_sequencer(self) -> list:
        lines = []
        lines.append(f"  ── Step Sequencer ({self._total_steps} steps) ──")
        lines.append("")
        for i in range(self._total_steps):
            step = self._sequencer_steps[i]
            marker = "▶" if i == self._current_step else " "
            notes_str = step.note_names
            accent = "⚡" if step.accent else " "
            lines.append(f"  {marker} Step {i:2d}{accent}: {notes_str}")
        return lines

    def render_preset_detail(self) -> list:
        p = self.selected_preset
        if not p:
            return ["  No preset selected"]
        lines = []
        lines.append(f"  ── {p.name} ({p.instrument_type.value}) ──")
        lines.append(f"  Channel: {p.channel}  Volume: {p.volume:.0%}")
        lines.append(f"  Pan: {p.pan_display}")
        lines.append(f"  Attack: {p.attack_ms}ms  Release: {p.release_ms}ms")
        lines.append(f"  Cutoff: {p.cutoff_hz}Hz  Resonance: {p.resonance}")
        if p.effects:
            lines.append(f"  Effects: {', '.join(p.effects)}")
        return lines
