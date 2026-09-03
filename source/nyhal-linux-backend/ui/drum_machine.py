from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class DrumPad(Enum):
    KICK = "kick"
    SNARE = "snare"
    HI_HAT_CLOSED = "hihat-closed"
    HI_HAT_OPEN = "hihat-open"
    RIDE = "ride"
    CRASH = "crash"
    TOM_LOW = "tom-low"
    TOM_MID = "tom-mid"
    TOM_HIGH = "tom-high"
    CLAP = "clap"
    RIMSHOT = "rimshot"
    SHAKER = "shaker"
    CONGA = "conga"
    BONGO = "bongo"
    COWBELL = "cowbell"
    TAMB = "tambourine"


class PatternMode(Enum):
    SINGLE = "single"
    CHAIN = "chain"
    SONG = "song"


class TimeSignature(Enum):
    FOUR_FOUR = "4/4"
    THREE_FOUR = "3/4"
    SIX_EIGHT = "6/8"


@dataclass
class PadHit:
    pad: DrumPad
    velocity: int
    step: int

    @property
    def velocity_display(self) -> str:
        filled = self.velocity // 10
        return "█" * filled + "░" * (12 - filled)


@dataclass
class DrumPattern:
    name: str
    genre: str
    steps: int
    time_sig: TimeSignature
    bpm: int
    hits: list = field(default_factory=list)
    swing: float = 0.0

    def get_hit(self, step: int, pad: DrumPad) -> Optional[PadHit]:
        for h in self.hits:
            if h.step == step and h.pad == pad:
                return h
        return None

    def add_hit(self, pad: DrumPad, step: int, velocity: int = 100):
        self.hits.append(PadHit(pad, velocity, step))

    def remove_hit(self, pad: DrumPad, step: int):
        self.hits = [h for h in self.hits if not (h.pad == pad and h.step == step)]

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    @property
    def pads_used(self) -> int:
        return len(set(h.pad for h in self.hits))


@dataclass
class KitPreset:
    name: str
    genre: str
    pads: dict = field(default_factory=dict)
    effects: list = field(default_factory=list)


class DrumMachine:
    def __init__(self):
        self._patterns: list[DrumPattern] = []
        self._selected_pattern: int = 0
        self._selected_step: int = 0
        self._selected_pad: int = 0
        self._is_playing: bool = False
        self._current_step: int = 0
        self._bpm: int = 120
        self._swing: float = 0.0
        self._volume: float = 0.8
        self._master_volume: float = 1.0
        self._view: str = "pads"
        self._recording: bool = False
        self._loop: bool = True
        self._step_count: int = 16
        self._kits: list[KitPreset] = []
        self._selected_kit: int = 0
        self._mute_states: dict = {}
        self._solo_pad: Optional[DrumPad] = None
        self._all_pads = list(DrumPad)
        self._create_samples()

    def _create_samples(self):
        self._kits = [
            KitPreset("808 Classic", "Hip-Hop", effects=["reverb", "compression"]),
            KitPreset("Jazz Brushes", "Jazz", effects=["room", "eq"]),
            KitPreset("Rock Standard", "Rock", effects=["compression", "eq"]),
            KitPreset("EDM Punch", "Electronic", effects=["distortion", "limiter"]),
        ]

        p1 = DrumPattern("Basic Beat", "Rock", 16, TimeSignature.FOUR_FOUR, 120)
        for step in [0, 4, 8, 12]:
            p1.add_hit(DrumPad.KICK, step, 110)
        for step in [4, 12]:
            p1.add_hit(DrumPad.SNARE, step, 100)
        for step in range(0, 16, 2):
            p1.add_hit(DrumPad.HI_HAT_CLOSED, step, 80)
        for step in [6, 14]:
            p1.add_hit(DrumPad.HI_HAT_OPEN, step, 70)
        self._patterns.append(p1)

        p2 = DrumPattern("Funky Groove", "Funk", 16, TimeSignature.FOUR_FOUR, 110, swing=0.15)
        for step in [0, 3, 6, 10, 14]:
            p2.add_hit(DrumPad.KICK, step, 100)
        for step in [2, 6, 10, 14]:
            p2.add_hit(DrumPad.SNARE, step, 95)
        for step in range(0, 16, 2):
            p2.add_hit(DrumPad.HI_HAT_CLOSED, step, 75)
        for step in [4, 12]:
            p2.add_hit(DrumPad.CLAP, step, 90)
        for step in [7, 15]:
            p2.add_hit(DrumPad.RIMSHOT, step, 60)
        self._patterns.append(p2)

        p3 = DrumPattern("House 4-on-Floor", "House", 16, TimeSignature.FOUR_FOUR, 128)
        for step in [0, 4, 8, 12]:
            p3.add_hit(DrumPad.KICK, step, 120)
        for step in range(0, 16, 2):
            p3.add_hit(DrumPad.HI_HAT_CLOSED, step, 90)
        for step in [2, 6, 10, 14]:
            p3.add_hit(DrumPad.HI_HAT_OPEN, step, 60)
        for step in [4, 12]:
            p3.add_hit(DrumPad.CLAP, step, 100)
        for step in [11]:
            p3.add_hit(DrumPad.SHAKER, step, 50)
        self._patterns.append(p3)

        p4 = DrumPattern("Latin Fiesta", "Latin", 16, TimeSignature.FOUR_FOUR, 100)
        for step in [0, 6, 10, 14]:
            p4.add_hit(DrumPad.KICK, step, 100)
        for step in [3, 7, 11, 15]:
            p4.add_hit(DrumPad.SNARE, step, 85)
        for step in [0, 3, 8, 11]:
            p4.add_hit(DrumPad.CONGA, step, 80)
        for step in [2, 6, 10, 14]:
            p4.add_hit(DrumPad.BONGO, step, 70)
        for step in [4, 12]:
            p4.add_hit(DrumPad.COWBELL, step, 75)
        for step in range(0, 16, 4):
            p4.add_hit(DrumPad.TAMB, step, 65)
        self._patterns.append(p4)

    @property
    def selected_pattern(self) -> Optional[DrumPattern]:
        if 0 <= self._selected_pattern < len(self._patterns):
            return self._patterns[self._selected_pattern]
        return None

    @property
    def selected_kit(self) -> Optional[KitPreset]:
        if 0 <= self._selected_kit < len(self._kits):
            return self._kits[self._selected_kit]
        return None

    @property
    def total_patterns(self) -> int:
        return len(self._patterns)

    @property
    def total_hits(self) -> int:
        return sum(p.hit_count for p in self._patterns)

    def select_pattern(self, idx: int):
        if 0 <= idx < len(self._patterns):
            self._selected_pattern = idx
            self._current_step = 0

    def start_playback(self):
        self._is_playing = True
        self._current_step = 0

    def stop_playback(self):
        self._is_playing = False
        self._current_step = 0

    def add_hit(self, pad: DrumPad, step: int, velocity: int = 100):
        pat = self.selected_pattern
        if pat:
            pat.add_hit(pad, step, velocity)

    def remove_hit(self, pad: DrumPad, step: int):
        pat = self.selected_pattern
        if pat:
            pat.remove_hit(pad, step)

    def toggle_mute(self, pad: DrumPad):
        if pad in self._mute_states:
            del self._mute_states[pad]
        else:
            self._mute_states[pad] = True

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                      NYRQIS DRUM MACHINE                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        play = "▶ PLAYING" if self._is_playing else "⏹ STOPPED"
        rec = " 🔴 REC" if self._recording else ""
        lines.append(f"  Status: {play}{rec}  BPM: {self._bpm}  Time: {self._patterns[self._selected_pattern].time_sig.value if self._patterns else '4/4'}  Swing: {self._swing:.0%}")
        lines.append(f"  Volume: {self._volume:.0%}  Master: {self._master_volume:.0%}  Steps: {self._step_count}  Loop: {'ON' if self._loop else 'OFF'}")
        lines.append("")
        lines.append("  ── Kits ──")
        for i, k in enumerate(self._kits):
            sel = "▶" if i == self._selected_kit else " "
            lines.append(f"  {sel} 🥁 {k.name}  ({k.genre})  {', '.join(k.effects)}")
        lines.append("")
        lines.append("  ── Patterns ──")
        for i, p in enumerate(self._patterns):
            sel = "▶" if i == self._selected_pattern else " "
            lines.append(f"  {sel} {p.name}  {p.genre}  {p.bpm}bpm  {p.hit_count} hits  {p.pads_used} pads")
        lines.append("")
        lines.append("  ── Sequencer ──")
        # Show sequencer grid for selected pads
        key_pads = [DrumPad.KICK, DrumPad.SNARE, DrumPad.HI_HAT_CLOSED, DrumPad.CLAP]
        for pad in key_pads:
            mute = "🔇" if self._mute_states.get(pad, False) else "  "
            row = f"  {mute}{pad.value[:4]:>4} │"
            for step in range(self._step_count):
                hit = self.selected_pattern.get_hit(step, pad) if self.selected_pattern else None
                marker = "●" if step == self._current_step else " "
                if hit:
                    row += f"{marker}█{hit.velocity // 40:X}"
                elif step % 4 == 0:
                    row += f"{marker}┃ "
                else:
                    row += f"{marker}· "
            lines.append(row)
        lines.append("")
        lines.append("  ── Pads ──")
        pad_row = "  "
        for i, pad in enumerate(self._all_pads[:8]):
            active = pad.value == self._all_pads[self._selected_pad].value if self._all_pads else False
            icon = "🟢" if active else "⬛"
            pad_row += f"{icon}{pad.value[:4]:>4} "
        lines.append(pad_row)
        lines.append("")
        lines.append("  [P]lay  [S]top  [R]ecord  [B]pm  [+]pad  [-]remove  [M]ute  [L]oop")
        return lines

    def render_pads(self) -> list:
        lines = []
        lines.append("  ── Drum Pads (4×4) ──")
        lines.append("")
        for row in range(4):
            line = "  "
            for col in range(4):
                idx = row * 4 + col
                if idx < len(self._all_pads):
                    pad = self._all_pads[idx]
                    active = idx == self._selected_pad
                    mute = self._mute_states.get(pad, False)
                    icon = "🟡" if active else ("🔇" if mute else "⬛")
                    line += f" {icon} {pad.value[:8]:>8} "
            lines.append(line)
        lines.append("")
        return lines

    def render_pattern_detail(self) -> list:
        p = self.selected_pattern
        if not p:
            return ["  No pattern selected"]
        lines = []
        lines.append(f"  ── {p.name} ({p.genre}) ──")
        lines.append(f"  BPM: {p.bpm}  Time: {p.time_sig.value}  Steps: {p.steps}  Swing: {p.swing:.0%}")
        lines.append(f"  Hits: {p.hit_count}  Pads: {p.pads_used}")
        lines.append("")
        lines.append("  Steps with hits:")
        for step in range(p.steps):
            hits_at_step = [h for h in p.hits if h.step == step]
            if hits_at_step:
                pads = ", ".join(f"{h.pad.value}({h.velocity})" for h in hits_at_step)
                lines.append(f"    Step {step:2d}: {pads}")
        return lines
