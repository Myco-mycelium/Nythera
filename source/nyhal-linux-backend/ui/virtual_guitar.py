from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class Tuning(Enum):
    STANDARD = "EADGBE"
    DROP_D = "DADGBE"
    DADGAD = "DADGAD"
    OPEN_G = "DGDGBD"
    OPEN_D = "DADF#AD"
    HALF_STEP_DOWN = "EbAbDbGbBbEb"
    WHOLE_STEP_DOWN = "DGCFAD"


class GuitarType(Enum):
    ACOUSTIC = "acoustic"
    ELECTRIC = "electric"
    CLASSICAL = "classical"
    BASS = "bass"
    twelve_STRING = "12-string"


class StrumPattern(Enum):
    DOWN = "D"
    UP = "U"
    DOWN_UP = "DU"
    UP_DOWN = "UD"
    DDU = "DDU"
    DUDU = "DUDU"
    DDDD = "DDDD"
    DUDUDU = "DUDUDU"


class GuitarEffect(Enum):
    REVERB = "reverb"
    CHORUS = "chorus"
    DELAY = "delay"
    DISTORTION = "distortion"
    OVERDRIVE = "overdrive"
    TREMOLO = "tremolo"
    COMPRESSOR = "compressor"
    EQ = "eq"


@dataclass
class Chord:
    name: str
    notes: list
    fingers: list = field(default_factory=list)
    barre: int = 0
    category: str = "open"
    difficulty: int = 1

    @property
    def difficulty_stars(self) -> str:
        return "★" * self.difficulty + "☆" * (5 - self.difficulty)

    @property
    def diagram(self) -> str:
        return " ".join(self.notes)


@dataclass
class StrummingPattern:
    name: str
    pattern: str
    bpm: int
    time_sig: str

    @property
    def beats(self) -> list:
        return list(self.pattern)

    @property
    def length(self) -> int:
        return len(self.pattern)


@dataclass
class GuitarEffectInstance:
    effect_type: GuitarEffect
    mix: float
    enabled: bool = True
    params: dict = field(default_factory=dict)

    @property
    def mix_bar(self) -> str:
        filled = int(self.mix * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class Recording:
    name: str
    notes: list = field(default_factory=list)
    timestamp: float = 0
    duration_ms: int = 0
    bpm: int = 120
    tuning: Tuning = Tuning.STANDARD

    @property
    def display_duration(self) -> str:
        if self.duration_ms >= 60000:
            m, s = divmod(self.duration_ms // 1000, 60)
            return f"{m}:{s:02d}"
        return f"{self.duration_ms / 1000:.1f}s"

    @property
    def note_count(self) -> int:
        return len(self.notes)


class VirtualGuitar:
    def __init__(self):
        self._guitar_type: GuitarType = GuitarType.ACOUSTIC
        self._tuning: Tuning = Tuning.STANDARD
        self._selected_chord: int = 0
        self._selected_pattern: int = 0
        self._effects: list[GuitarEffectInstance] = []
        self._chords: list[Chord] = []
        self._patterns: list[StrummingPattern] = []
        self._recordings: list[Recording] = []
        self._selected_recording: int = 0
        self._is_recording: bool = False
        self._volume: float = 0.8
        self._tone: float = 0.5
        self._gain: float = 0.3
        self._capo: int = 0
        self._active_chord: Optional[Chord] = None
        self._view: str = "chords"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._chords = [
            Chord("C", ["x", "3", "2", "0", "1", "0"], [0, 3, 2, 0, 1, 0], category="open", difficulty=1),
            Chord("Am", ["x", "0", "2", "2", "1", "0"], [0, 0, 2, 2, 1, 0], category="open", difficulty=1),
            Chord("G", ["3", "2", "0", "0", "3", "3"], [2, 1, 0, 0, 3, 4], category="open", difficulty=1),
            Chord("Em", ["0", "2", "2", "0", "0", "0"], [0, 2, 3, 0, 0, 0], category="open", difficulty=1),
            Chord("D", ["x", "x", "0", "2", "3", "2"], [0, 0, 0, 1, 2, 3], category="open", difficulty=2),
            Chord("F", ["1", "3", "3", "2", "1", "1"], [1, 3, 4, 2, 1, 1], barre=1, category="barre", difficulty=3),
            Chord("Bm", ["x", "2", "4", "4", "3", "2"], [0, 1, 3, 4, 2, 1], barre=2, category="barre", difficulty=3),
            Chord("E", ["0", "2", "2", "1", "0", "0"], [0, 2, 3, 1, 0, 0], category="open", difficulty=1),
            Chord("A", ["x", "0", "2", "2", "2", "0"], [0, 0, 1, 2, 3, 0], category="open", difficulty=1),
            Chord("Dm", ["x", "x", "0", "2", "3", "1"], [0, 0, 0, 1, 3, 2], category="open", difficulty=2),
        ]
        self._patterns = [
            StrummingPattern("Basic Down", "DDDD", 120, "4/4"),
            StrummingPattern("Down-Up", "DUDU", 120, "4/4"),
            StrummingPattern("Folk", "DDUUDD", 100, "6/8"),
            StrummingPattern("Rock", "DUDUDD", 140, "4/4"),
            StrummingPattern("Waltz", "DDU", 100, "3/4"),
            StrummingPattern("Syncopated", "D-DU-UD", 110, "4/4"),
        ]
        self._effects = [
            GuitarEffectInstance(GuitarEffect.REVERB, 0.3),
            GuitarEffectInstance(GuitarEffect.CHORUS, 0.15),
        ]
        self._recordings = [
            Recording("Campfire Song", ["C", "Am", "F", "G"], now - 3600, 32000, 100),
            Recording("Blues Jam", ["E", "A", "B", "E"], now - 7200, 45000, 120),
        ]

    @property
    def selected_chord(self) -> Optional[Chord]:
        if 0 <= self._selected_chord < len(self._chords):
            return self._chords[self._selected_chord]
        return None

    @property
    def selected_pattern(self) -> Optional[StrummingPattern]:
        if 0 <= self._selected_pattern < len(self._patterns):
            return self._patterns[self._selected_pattern]
        return None

    @property
    def selected_recording(self) -> Optional[Recording]:
        if 0 <= self._selected_recording < len(self._recordings):
            return self._recordings[self._selected_recording]
        return None

    @property
    def total_chords(self) -> int:
        return len(self._chords)

    def select_chord(self, idx: int):
        if 0 <= idx < len(self._chords):
            self._selected_chord = idx
            self._active_chord = self._chords[idx]

    def select_pattern(self, idx: int):
        if 0 <= idx < len(self._patterns):
            self._selected_pattern = idx

    def strum(self, pattern: StrummingPattern):
        self._active_chord = self.selected_chord

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS VIRTUAL GUITAR                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Type: {self._guitar_type.value}  Tuning: {self._tuning.value}  Capo: {self._capo}")
        lines.append(f"  Volume: {self._volume:.0%}  Tone: {self._tone:.0%}  Gain: {self._gain:.0%}")
        lines.append("")
        lines.append("  ── Chords ──")
        for i, c in enumerate(self._chords):
            sel = "▶" if i == self._selected_chord else " "
            active = " 🎸" if self._active_chord == c else ""
            lines.append(f"  {sel}{active} {c.name:<5s} [{c.diagram}]  {c.difficulty_stars}  ({c.category})")
        lines.append("")
        lines.append("  ── Strumming Patterns ──")
        for i, p in enumerate(self._patterns):
            sel = "▶" if i == self._selected_pattern else " "
            lines.append(f"  {sel} {p.name:<15s} {p.pattern:<10s} {p.bpm}bpm  {p.time_sig}")
        lines.append("")
        lines.append("  ── Effects ──")
        for eff in self._effects:
            status = "🟢" if eff.enabled else "⚪"
            lines.append(f"  {status} {eff.effect_type.value:<12s} [{eff.mix_bar}] {eff.mix:.0%}")
        lines.append("")
        lines.append("  ── Recordings ──")
        for i, r in enumerate(self._recordings):
            sel = "▶" if i == self._selected_recording else " "
            lines.append(f"  {sel} {r.name}  {r.display_duration}  {r.note_count} chords  {r.bpm}bpm")
        lines.append("")
        lines.append("  [S]trum  [R]ecord  [T]uning  [E]ffects  [C]apo  [P]attern  [N]ew chord")
        return lines

    def render_chord_detail(self) -> list:
        c = self.selected_chord
        if not c:
            return ["  No chord selected"]
        lines = []
        lines.append(f"  ── {c.name} Chord ──")
        lines.append(f"  Category: {c.category}  Difficulty: {c.difficulty_stars}")
        lines.append(f"  Strings: {c.diagram}")
        if c.barre:
            lines.append(f"  Barre: Fret {c.barre}")
        lines.append("")
        lines.append("  Fretboard:")
        for fret in range(4):
            line = f"  F{fret+1} "
            for s in range(6):
                if fret == 0:
                    # Open strings
                    if c.notes[s] == "x":
                        line += "  ×  "
                    else:
                        line += "  ○  "
                else:
                    line += "────"
            lines.append(line)
        return lines

    def render_tuning(self) -> list:
        lines = []
        lines.append("  ── Tunings ──")
        lines.append("")
        for t in Tuning:
            sel = "▶" if t == self._tuning else " "
            strings = t.value
            lines.append(f"  {sel} {t.name:<15s}  {strings}")
        return lines
