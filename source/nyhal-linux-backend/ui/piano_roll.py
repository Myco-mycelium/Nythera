from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class NoteName(Enum):
    C = "C"; C_SHARP = "C#"; D = "D"; D_SHARP = "D#"; E = "E"; F = "F"
    F_SHARP = "F#"; G = "G"; G_SHARP = "G#"; A = "A"; A_SHARP = "A#"; B = "B"


class Quantize(Enum):
    NONE = "off"
    QUARTER = "1/4"
    EIGHTH = "1/8"
    SIXTEENTH = "1/16"
    THIRTY_SECOND = "1/32"


class ToolMode(Enum):
    SELECT = "select"
    PAINT = "paint"
    ERASE = "erase"
    SELECT_RANGE = "select-range"
    GLIDE = "glide"


@dataclass
class MidiNote:
    note: NoteName
    octave: int
    start_beat: float
    duration_beats: float
    velocity: int = 100
    channel: int = 0
    selected: bool = False

    @property
    def end_beat(self) -> float:
        return self.start_beat + self.duration_beats

    @property
    def display_name(self) -> str:
        return f"{self.note.value}{self.octave}"


@dataclass
class MidiTrack:
    name: str
    channel: int
    instrument: str
    notes: list = field(default_factory=list)
    muted: bool = False
    solo: bool = False
    volume: float = 0.8
    pan: float = 0.0
    color: str = "#4CAF50"

    @property
    def note_count(self) -> int:
        return len(self.notes)

    @property
    def volume_bar(self) -> str:
        filled = int(self.volume * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class TimeSignature:
    numerator: int
    denominator: int

    @property
    def display(self) -> str:
        return f"{self.numerator}/{self.denominator}"


@dataclass
class MidiClip:
    name: str
    track_idx: int
    start_beat: float
    length_beats: float
    notes: list = field(default_factory=list)
    color: str = "#2196F3"


class PianoRoll:
    def __init__(self):
        self._tracks: list[MidiTrack] = []
        self._selected_track: int = 0
        self._clips: list[MidiClip] = []
        self._tool: ToolMode = ToolMode.SELECT
        self._quantize: Quantize = Quantize.SIXTEENTH
        self._bpm: int = 120
        self._time_sig: TimeSignature = TimeSignature(4, 4)
        self._total_beats: int = 64
        self._view_start: float = 0
        self._view_end: float = 32
        self._selected_notes: list[MidiNote] = []
        self._cursor_beat: float = 0
        self._loop_start: float = 0
        self._loop_end: float = 16
        self._loop_enabled: bool = False
        self._is_playing: bool = False
        self._view: str = "roll"
        self._zoom: int = 1
        self._create_samples()

    def _create_samples(self):
        t1 = MidiTrack("Piano", 0, "Grand Piano", volume=0.8)
        base_notes = [
            (NoteName.C, 4, 0, 2, 100), (NoteName.E, 4, 2, 2, 90),
            (NoteName.G, 4, 4, 2, 95), (NoteName.C, 5, 6, 2, 85),
            (NoteName.A, 4, 8, 2, 90), (NoteName.F, 4, 10, 2, 80),
            (NoteName.G, 4, 12, 4, 100),
        ]
        for n, o, s, d, v in base_notes:
            t1.notes.append(MidiNote(n, o, s, d, v))

        t2 = MidiTrack("Bass", 1, "Electric Bass", volume=0.7)
        bass_notes = [
            (NoteName.C, 2, 0, 4, 110), (NoteName.A, 1, 4, 4, 100),
            (NoteName.F, 2, 8, 4, 105), (NoteName.G, 2, 12, 4, 100),
        ]
        for n, o, s, d, v in bass_notes:
            t2.notes.append(MidiNote(n, o, s, d, v))

        t3 = MidiTrack("Drums", 9, "Drum Kit", volume=0.9)
        for beat in range(16):
            t3.notes.append(MidiNote(NoteName.C, 2, beat, 0.5, 100))  # Kick
            if beat % 4 == 2:
                t3.notes.append(MidiNote(NoteName.D, 2, beat, 0.5, 90))  # Snare

        self._tracks = [t1, t2, t3]
        self._clips = [
            MidiClip("Melody", 0, 0, 16, t1.notes[:4]),
            MidiClip("Bass Line", 1, 0, 16, t2.notes[:2]),
            MidiClip("Beat", 2, 0, 16, t3.notes[:8]),
        ]

    @property
    def selected_track(self) -> Optional[MidiTrack]:
        if 0 <= self._selected_track < len(self._tracks):
            return self._tracks[self._selected_track]
        return None

    @property
    def total_notes(self) -> int:
        return sum(t.note_count for t in self._tracks)

    @property
    def selected_notes_count(self) -> int:
        return sum(1 for t in self._tracks for n in t.notes if n.selected)

    def select_track(self, idx: int):
        if 0 <= idx < len(self._tracks):
            self._selected_track = idx

    def add_note(self, track_idx: int, note: NoteName, octave: int, start: float, duration: float, velocity: int = 100):
        if 0 <= track_idx < len(self._tracks):
            midi_note = MidiNote(note, octave, start, duration, velocity)
            self._tracks[track_idx].notes.append(midi_note)

    def delete_selected(self):
        track = self.selected_track
        if track:
            track.notes = [n for n in track.notes if not n.selected]

    def select_notes_in_range(self, start_beat: float, end_beat: float, low_note: NoteName, high_note: NoteName):
        track = self.selected_track
        if track:
            for n in track.notes:
                if (start_beat <= n.start_beat <= end_beat and
                    low_note.value <= n.note.value <= high_note.value):
                    n.selected = True

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS PIANO ROLL EDITOR                               ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        play = "▶" if self._is_playing else "⏹"
        loop = "🔁" if self._loop_enabled else "  "
        lines.append(f"  {play} BPM: {self._bpm}  Time: {self._time_sig.display}  Tool: {self._tool.value}  Quantize: {self._quantize.value}")
        lines.append(f"  Total: {self.total_notes} notes  Selected: {self.selected_notes_count}  {loop} Loop: {self._loop_start:.0f}-{self._loop_end:.0f}")
        lines.append("")
        lines.append("  ── Tracks ──")
        for i, t in enumerate(self._tracks):
            sel = "▶" if i == self._selected_track else " "
            mute = "🔇" if t.muted else "  "
            solo = "⭐" if t.solo else "  "
            lines.append(f"  {sel}{mute}{solo} {t.name:<15s} Ch:{t.channel:<2d} [{t.volume_bar}] {t.instrument}  {t.note_count} notes")
        lines.append("")
        lines.append("  ── Piano Roll ──")
        # Show a simplified roll view
        notes = ["C5", "B4", "A4", "G4", "F4", "E4", "D4", "C4", "B3", "A3", "G3", "F3", "E3", "D3", "C3", "C2"]
        track = self.selected_track
        if track:
            for note_label in notes:
                row = f"  {note_label:>3s} │"
                for beat in range(int(self._view_start), int(self._view_end)):
                    has_note = any(n.display_name == note_label and n.start_beat <= beat < n.end_beat for n in track.notes)
                    row += "██" if has_note else "░░"
                lines.append(row)
        lines.append("")
        lines.append("  ── Clips ──")
        for c in self._clips:
            lines.append(f"  🎬 {c.name}  Track {c.track_idx}  {c.start_beat:.0f}-{c.start_beat + c.length_beats:.0f}  {c.length_beats:.0f} beats")
        lines.append("")
        lines.append("  [N]ew note  [D]elete  [S]elect  [P]lay  [T]rack  [Q]uantize  [L]oop  [Z]oom")
        return lines

    def render_clip_detail(self) -> list:
        lines = []
        lines.append("  ── Clip Details ──")
        for c in self._clips:
            lines.append(f"  {c.name}: {len(c.notes)} notes, beats {c.start_beat:.0f}-{c.start_beat + c.length_beats:.0f}")
        return lines
