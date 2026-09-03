from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math


class DeckSide(Enum):
    A = "A"
    B = "B"


class PlaybackState(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    CUE = "cue"


class EQBand(Enum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


class LoopSize(Enum):
    OFF = "off"
    HALF = "1/2"
    ONE = "1"
    TWO = "2"
    FOUR = "4"
    EIGHT = "8"
    SIXTEEN = "16"


class FXType(Enum):
    ECHO = "echo"
    REVERB = "reverb"
    FILTER = "filter"
    PHASER = "phaser"
    FLANGER = "flanger"
    DELAY = "delay"
    BITCRUSH = "bitcrush"
    GATE = "gate"


@dataclass
class Track:
    title: str
    artist: str
    bpm: float
    duration_secs: float
    key: str = ""
    genre: str = ""
    bpm_locked: bool = False

    @property
    def duration_display(self) -> str:
        m, s = divmod(int(self.duration_secs), 60)
        return f"{m}:{s:02d}"

    @property
    def key_display(self) -> str:
        return self.key or "N/A"


@dataclass
class DeckState:
    side: DeckSide
    track: Optional[Track] = None
    state: PlaybackState = PlaybackState.STOPPED
    position_secs: float = 0
    volume: float = 0.8
    gain: float = 1.0
    eq_low: float = 0.5
    eq_mid: float = 0.5
    eq_high: float = 0.5
    pitch: float = 0.0
    loop_start: float = 0
    loop_end: float = 0
    loop_size: LoopSize = LoopSize.OFF
    cue_point: float = 0
    is_synced: bool = False

    @property
    def position_display(self) -> str:
        m, s = divmod(int(self.position_secs), 60)
        return f"{m}:{s:02d}"

    @property
    def remaining_display(self) -> str:
        if not self.track:
            return "0:00"
        remaining = self.track.duration_secs - self.position_secs
        m, s = divmod(int(max(0, remaining)), 60)
        return f"-{m}:{s:02d}"

    @property
    def pitch_display(self) -> str:
        return f"{self.pitch:+.1f}%"

    @property
    def volume_bar(self) -> str:
        filled = int(self.volume * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def position_bar(self) -> str:
        if not self.track or self.track.duration_secs == 0:
            return "░" * 32
        pct = self.position_secs / self.track.duration_secs
        pos = int(pct * 32)
        return "█" * pos + "░" * (32 - pos)

    @property
    def waveform(self) -> str:
        chars = " ▁▂▃▄▅▆▇█"
        result = ""
        for i in range(32):
            val = math.sin(i * 0.3 + self.position_secs * 0.1) * 0.5 + 0.5
            idx = int(val * 8)
            result += chars[min(idx, 8)]
        return result


@dataclass
class DJEffect:
    fx_type: FXType
    mix: float
    enabled: bool = True
    params: dict = field(default_factory=dict)

    @property
    def mix_bar(self) -> str:
        filled = int(self.mix * 10)
        return "█" * filled + "░" * (10 - filled)


class DJConsole:
    def __init__(self):
        self._deck_a: DeckState = DeckState(DeckSide.A)
        self._deck_b: DeckState = DeckState(DeckSide.B)
        self._crossfader: float = 0.5  # 0=A, 1=B
        self._master_volume: float = 0.8
        self._bpm_display: float = 120
        self._effects: list[DJEffect] = []
        self._selected_deck: DeckSide = DeckSide.A
        self._track_library: list[Track] = []
        self._is_recording: bool = False
        self._view: str = "decks"
        self._create_samples()

    def _create_samples(self):
        self._track_library = [
            Track("Bass Drop", "DJ Nyrqis", 128.0, 245, "Am", "House"),
            Track("Neon Nights", "Synthwave Master", 118.0, 312, "Cm", "Synthwave"),
            Track("Deep Groove", "Bass Theory", 124.0, 298, "Gm", "Techno"),
            Track("Sunset Drive", "Retrowave", 110.0, 267, "Dm", "Chillwave"),
            Track("Pulse", "Electronic Beats", 132.0, 189, "Em", "Trance"),
            Track("Night Rider", "Synth Lord", 126.0, 356, "Fm", "Electro"),
        ]
        self._deck_a.track = self._track_library[0]
        self._deck_a.state = PlaybackState.PLAYING
        self._deck_a.position_secs = 45.2
        self._deck_b.track = self._track_library[1]
        self._deck_b.state = PlaybackState.PAUSED
        self._deck_b.cue_point = 32.0
        self._effects = [
            DJEffect(FXType.ECHO, 0.3),
            DJEffect(FXType.REVERB, 0.2),
            DJEffect(FXType.FILTER, 0.0),
        ]

    @property
    def selected_deck(self) -> DeckState:
        return self._deck_a if self._selected_deck == DeckSide.A else self._deck_b

    def select_deck(self, side: DeckSide):
        self._selected_deck = side

    def toggle_play(self, side: DeckSide):
        deck = self._deck_a if side == DeckSide.A else self._deck_b
        if deck.state == PlaybackState.PLAYING:
            deck.state = PlaybackState.PAUSED
        else:
            deck.state = PlaybackState.PLAYING

    def set_cue(self, side: DeckSide):
        deck = self._deck_a if side == DeckSide.A else self._deck_b
        deck.cue_point = deck.position_secs

    def sync_bpm(self, target_side: DeckSide):
        source = self._deck_b if target_side == DeckSide.A else self._deck_a
        target = self._deck_a if target_side == DeckSide.A else self._deck_b
        if source.track and target.track:
            target.track.bpm = source.track.bpm
            target.is_synced = True

    def set_crossfader(self, value: float):
        self._crossfader = max(0, min(1, value))

    def set_volume(self, side: DeckSide, value: float):
        deck = self._deck_a if side == DeckSide.A else self._deck_b
        deck.volume = max(0, min(1, value))

    def set_eq(self, side: DeckSide, band: EQBand, value: float):
        deck = self._deck_a if side == DeckSide.A else self._deck_b
        if band == EQBand.LOW:
            deck.eq_low = max(0, min(1, value))
        elif band == EQBand.MID:
            deck.eq_mid = max(0, min(1, value))
        elif band == EQBand.HIGH:
            deck.eq_high = max(0, min(1, value))

    def load_track(self, side: DeckSide, track_idx: int):
        if 0 <= track_idx < len(self._track_library):
            deck = self._deck_a if side == DeckSide.A else self._deck_b
            deck.track = self._track_library[track_idx]
            deck.position_secs = 0
            deck.state = PlaybackState.STOPPED

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                      NYRQIS DJ CONSOLE                                     ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        rec = "🔴 REC" if self._is_recording else ""
        lines.append(f"  Master: [{self._deck_a.volume_bar}] {self._master_volume:.0%}  Crossfader: {self._crossfader:.2f}  BPM: {self._bpm_display:.0f}  {rec}")
        lines.append("")
        for deck in [self._deck_a, self._deck_b]:
            state_icon = {"playing": "▶", "paused": "⏸", "stopped": "⏹", "cue": "⏫"}.get(deck.state.value, "?")
            track = deck.track
            lines.append(f"  ── DECK {deck.side.value} ── {state_icon} {'[SYNC]' if deck.is_synced else ''}")
            if track:
                lines.append(f"  {track.artist} - {track.title}  ({track.bpm:.0f} BPM, {track.key_display}, {track.genre})")
                lines.append(f"  {deck.position_display} [{deck.position_bar}] {deck.remaining_display}")
                lines.append(f"  Vol:[{deck.volume_bar}]  EQ: L={deck.eq_low:.0%} M={deck.eq_mid:.0%} H={deck.eq_high:.0%}  Pitch: {deck.pitch_display}")
                lines.append(f"  Loop: {deck.loop_size.value}  Cue: {deck.cue_point:.1f}s")
                lines.append(f"  {deck.waveform}")
            lines.append("")
        lines.append("  ── Effects ──")
        for eff in self._effects:
            status = "🟢" if eff.enabled else "⚪"
            lines.append(f"  {status} {eff.fx_type.value:<10s} [{eff.mix_bar}] {eff.mix:.0%}")
        lines.append("")
        lines.append("  ── Library ──")
        for i, t in enumerate(self._track_library[:6]):
            lines.append(f"  {i+1}. {t.artist} - {t.title}  {t.bpm:.0f} BPM  {t.duration_display}  {t.genre}")
        lines.append("")
        lines.append("  [P]lay/Pause  [S]ync  [C]ue  [X]fader  [E]Q  [L]oop  [F]X  [L]oad")
        return lines

    def render_deck_detail(self, side: DeckSide) -> list:
        deck = self._deck_a if side == DeckSide.A else self._deck_b
        lines = []
        lines.append(f"  ── Deck {side.value} Details ──")
        track = deck.track
        if track:
            lines.append(f"  Title: {track.title}")
            lines.append(f"  Artist: {track.artist}")
            lines.append(f"  BPM: {track.bpm:.1f}")
            lines.append(f"  Key: {track.key_display}")
            lines.append(f"  Duration: {track.duration_display}")
            lines.append(f"  Genre: {track.genre}")
            lines.append(f"  Position: {deck.position_display}")
            lines.append(f"  Volume: {deck.volume:.0%}")
            lines.append(f"  EQ Low: {deck.eq_low:.0%}  Mid: {deck.eq_mid:.0%}  High: {deck.eq_high:.0%}")
            lines.append(f"  Pitch: {deck.pitch_display}")
            lines.append(f"  Loop: {deck.loop_size.value}")
            lines.append(f"  Cue: {deck.cue_point:.1f}s")
        return lines
