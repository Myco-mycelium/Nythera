"""DAW Audio Workstation — multi-track recording, effects, MIDI for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import math


class TrackType(Enum):
    AUDIO = "Audio"
    MIDI = "Midi"
    INSTRUMENT = "Instrument"
    BUS = "Bus"
    AUX = "Aux"
    MASTER = "Master"


class TrackState(Enum):
    ARMED = "Armed"
    RECORDING = "Recording"
    PLAYING = "Playing"
    MUTED = "Muted"
    SOLO = "Solo"
    IDLE = "Idle"


class EffectType(Enum):
    EQ = "EQ"
    COMPRESSOR = "Compressor"
    REVERB = "Reverb"
    DELAY = "Delay"
    CHORUS = "Chorus"
    FLANGER = "Flanger"
    DISTORTION = "Distortion"
    GATE = "Gate"
    LIMITER = "Limiter"
    PHASER = "Phaser"
    TREMOLO = "Tremolo"
    AUTO_PAN = "Auto-Pan"


class MidiNote(Enum):
    C = 0; C_SHARP = 1; D = 2; D_SHARP = 3; E = 4; F = 5
    F_SHARP = 6; G = 7; G_SHARP = 8; A = 9; A_SHARP = 10; B = 11


class TimeSignature(Enum):
    FOUR_FOUR = "4/4"
    THREE_FOUR = "3/4"
    SIX_EIGHT = "6/8"
    FIVE_FOUR = "5/4"
    SEVEN_EIGHT = "7/8"


class LoopMode(Enum):
    OFF = "Off"
    REGION = "Region"
    SONG = "Song"


@dataclass
class AudioClip:
    name: str
    start_beat: float = 0.0
    duration_beats: float = 4.0
    file_path: str = ""
    sample_rate: int = 44100
    channels: int = 2
    gain_db: float = 0.0
    fade_in_ms: float = 0.0
    fade_out_ms: float = 0.0
    color: str = "#4a9eff"

    @property
    def duration_bars(self) -> float:
        return self.duration_beat / 4.0 if hasattr(self, '_x') else self.duration_beats / 4.0

    @property
    def waveform_display(self) -> str:
        import random
        random.seed(hash(self.name))
        return "".join("▁▂▃▄▅▆▇█"[random.randint(0, 7)] for _ in range(20))


@dataclass
class MidiNoteEvent:
    note: int = 60  # MIDI note number (0-127)
    velocity: int = 100
    start_beat: float = 0.0
    duration_beats: float = 1.0

    @property
    def note_name(self) -> str:
        names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        octave = (self.note // 12) - 1
        name = names[self.note % 12]
        return f"{name}{octave}"

    @property
    def velocity_bar(self) -> str:
        return "█" * int(self.velocity / 12.8) + "░" * (10 - int(self.velocity / 12.8))


@dataclass
class MidiClip:
    name: str
    start_beat: float = 0.0
    duration_beats: float = 16.0
    notes: List[MidiNoteEvent] = field(default_factory=list)
    color: str = "#ff6b6b"

    @property
    def note_count(self) -> int:
        return len(self.notes)

    @property
    def range_str(self) -> str:
        if not self.notes:
            return "---"
        names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        min_n = min(n.note for n in self.notes)
        max_n = max(n.note for n in self.notes)
        return f"{names[min_n % 12]}{min_n // 12 - 1} - {names[max_n % 12]}{max_n // 12 - 1}"


@dataclass
class Effect:
    effect_type: EffectType
    enabled: bool = True
    mix: float = 0.5
    params: Dict[str, float] = field(default_factory=dict)

    @property
    def mix_bar(self) -> str:
        return "█" * int(self.mix * 10) + "░" * (10 - int(self.mix * 10))

    @property
    def status_icon(self) -> str:
        return "🟢" if self.enabled else "⚪"


@dataclass
class Track:
    id: int
    name: str
    track_type: TrackType = TrackType.AUDIO
    state: TrackState = TrackState.IDLE
    volume_db: float = 0.0
    pan: float = 0.0  # -1.0 left, 0.0 center, 1.0 right
    gain_db: float = 0.0
    armed: bool = False
    muted: bool = False
    solo: bool = False
    color: str = "#4a9eff"
    input_source: str = ""
    output_dest: str = "Master"
    effects: List[Effect] = field(default_factory=list)
    audio_clips: List[AudioClip] = field(default_factory=list)
    midi_clips: List[MidiClip] = field(default_factory=list)
    volume_history: List[float] = field(default_factory=list)
    peak_db: float = -60.0
    rms_db: float = -60.0

    @property
    def volume_bar(self) -> str:
        vol = (self.volume_db + 60) / 60  # normalize -60..0 to 0..1
        filled = int(max(0, min(1, vol)) * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def pan_bar(self) -> str:
        pos = int((self.pan + 1) / 2 * 20)
        return "░" * pos + "█" + "░" * (20 - pos)

    @property
    def state_icon(self) -> str:
        icons = {
            TrackState.ARMED: "🔴",
            TrackState.RECORDING: "⏺",
            TrackState.PLAYING: "▶",
            TrackState.MUTED: "🔇",
            TrackState.SOLO: "🔊",
            TrackState.IDLE: "⏸",
        }
        return icons.get(self.state, "?")

    @property
    def clip_count(self) -> int:
        return len(self.audio_clips) + len(self.midi_clips)

    @property
    def effect_chain(self) -> str:
        if not self.effects:
            return "None"
        return " → ".join(e.effect_type.value for e in self.effects)


@dataclass
class TransportState:
    playing: bool = False
    recording: bool = False
    position_beat: float = 0.0
    bpm: float = 120.0
    time_signature: TimeSignature = TimeSignature.FOUR_FOUR
    loop_mode: LoopMode = LoopMode.OFF
    loop_start: float = 0.0
    loop_end: float = 16.0
    metronome: bool = True
    count_in: bool = False
    punch_in: bool = False
    punch_out: bool = False

    @property
    def position_bar(self) -> str:
        pos = int(min(self.position_beat / 64, 1.0) * 30)
        return "░" * pos + "▼" + "░" * (30 - pos)

    @property
    def bar(self) -> int:
        return int(self.position_beat // 4) + 1

    @property
    def beat(self) -> int:
        return int(self.position_beat % 4) + 1

    @property
    def position_str(self) -> str:
        return f"{self.bar}.{self.beat}"


@dataclass
class Marker:
    name: str
    position_beat: float = 0.0
    color: str = "#ff6b6b"


class AudioDAW:
    def __init__(self):
        self._tracks: List[Track] = []
        self._selected_track: int = 0
        self._transport = TransportState()
        self._markers: List[Marker] = []
        self._sample_rate: int = 44100
        self._buffer_size: int = 256
        self._view_mode: str = "arrange"
        self._zoom: float = 1.0
        self._scroll_x: float = 0.0
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        self._transport.bpm = 128.0
        self._transport.loop_end = 32.0

        self._tracks = [
            Track(0, "Drums", TrackType.AUDIO, color="#ff6b6b",
                  volume_db=-3.0, pan=0.0,
                  effects=[
                      Effect(EffectType.EQ, True, 0.7, {"high": 3.0}),
                      Effect(EffectType.COMPRESSOR, True, 0.6, {"threshold": -12}),
                  ],
                  audio_clips=[
                      AudioClip("Drums A", 0, 8, color="#ff6b6b"),
                      AudioClip("Drums B", 8, 8, color="#ff6b6b"),
                      AudioClip("Drums A", 16, 8, color="#ff6b6b"),
                  ]),
            Track(1, "Bass", TrackType.AUDIO, color="#ffa94d",
                  volume_db=-6.0, pan=0.0,
                  effects=[
                      Effect(EffectType.EQ, True, 0.5, {"low": 2.0}),
                      Effect(EffectType.COMPRESSOR, True, 0.4),
                  ],
                  audio_clips=[
                      AudioClip("Bass Line", 0, 16, color="#ffa94d"),
                      AudioClip("Bass Fill", 16, 8, color="#ffa94d"),
                  ]),
            Track(2, "Synth Pad", TrackType.INSTRUMENT, color="#69db7c",
                  volume_db=-10.0, pan=0.2,
                  effects=[
                      Effect(EffectType.REVERB, True, 0.4, {"room": 0.6}),
                      Effect(EffectType.CHORUS, True, 0.3),
                  ],
                  midi_clips=[
                      MidiClip("Chord Pad", 0, 16, [
                          MidiNoteEvent(60, 80, 0, 4),
                          MidiNoteEvent(64, 80, 0, 4),
                          MidiNoteEvent(67, 80, 0, 4),
                          MidiNoteEvent(72, 70, 4, 4),
                      ], "#69db7c"),
                  ]),
            Track(3, "Lead", TrackType.MIDI, color="#748ffc",
                  volume_db=-8.0, pan=-0.3,
                  effects=[
                      Effect(EffectType.DELAY, True, 0.3, {"time": 0.375}),
                      Effect(EffectType.DISTORTION, False, 0.2),
                  ],
                  midi_clips=[
                      MidiClip("Melody", 8, 16, [
                          MidiNoteEvent(72, 100, 0, 1),
                          MidiNoteEvent(74, 90, 1, 1),
                          MidiNoteEvent(76, 95, 2, 1),
                          MidiNoteEvent(79, 85, 3, 2),
                          MidiNoteEvent(77, 80, 5, 1),
                          MidiNoteEvent(76, 90, 6, 2),
                      ], "#748ffc"),
                  ]),
            Track(4, "Vocals", TrackType.AUDIO, color="#da77f2",
                  volume_db=-4.0, pan=0.0, armed=True,
                  effects=[
                      Effect(EffectType.EQ, True, 0.6),
                      Effect(EffectType.COMPRESSOR, True, 0.5),
                      Effect(EffectType.REVERB, True, 0.2, {"room": 0.3}),
                      Effect(EffectType.GATE, True, 0.3),
                  ],
                  audio_clips=[
                      AudioClip("Verse", 0, 16, color="#da77f2"),
                      AudioClip("Chorus", 16, 16, color="#da77f2"),
                  ]),
            Track(5, "Master", TrackType.MASTER, color="#868e96",
                  volume_db=0.0, pan=0.0,
                  effects=[
                      Effect(EffectType.EQ, True, 0.5),
                      Effect(EffectType.LIMITER, True, 0.8, {"ceiling": -0.3}),
                  ]),
        ]

        self._markers = [
            Marker("Intro", 0, "#868e96"),
            Marker("Verse 1", 8, "#69db7c"),
            Marker("Chorus", 16, "#ff6b6b"),
            Marker("Bridge", 24, "#ffa94d"),
            Marker("Outro", 32, "#868e96"),
        ]

    @property
    def transport(self) -> TransportState:
        return self._transport

    @property
    def selected_track(self) -> Optional[Track]:
        if 0 <= self._selected_track < len(self._tracks):
            return self._tracks[self._selected_track]
        return None

    @property
    def total_tracks(self) -> int:
        return len(self._tracks)

    @property
    def total_clips(self) -> int:
        return sum(t.clip_count for t in self._tracks)

    @property
    def total_effects(self) -> int:
        return sum(len(t.effects) for t in self._tracks)

    @property
    def armed_tracks(self) -> int:
        return sum(1 for t in self._tracks if t.armed)

    def select_track(self, idx: int):
        if 0 <= idx < len(self._tracks):
            self._selected_track = idx

    def toggle_play(self):
        self._transport.playing = not self._transport.playing
        self._transport.recording = False
        self._history.append(f"{'Play' if self._transport.playing else 'Stop'}")

    def toggle_record(self):
        self._transport.recording = not self._transport.recording
        self._transport.playing = self._transport.recording
        self._history.append(f"{'Record' if self._transport.recording else 'Stop Record'}")

    def toggle_metronome(self):
        self._transport.metronome = not self._transport.metronome

    def set_bpm(self, bpm: float):
        self._transport.bpm = max(20, min(300, bpm))

    def handle_input(self, key: str):
        key = key.lower()
        if key == " ":
            self.toggle_play()
        elif key == "r":
            self.toggle_record()
        elif key == "m":
            self.toggle_metronome()
        elif key == "n":
            self.add_track()
        elif key == "d":
            self.delete_track()

    def add_track(self, name: str = "New Track", track_type: TrackType = TrackType.AUDIO):
        track_id = len(self._tracks)
        self._tracks.insert(-1, Track(track_id, name, track_type))
        self._history.append(f"Added track: {name}")

    def delete_track(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_track
        if 0 <= i < len(self._tracks) - 1:  # don't delete master
            name = self._tracks[i].name
            self._tracks.pop(i)
            self._selected_track = min(self._selected_track, len(self._tracks) - 1)
            self._history.append(f"Deleted track: {name}")

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS AUDIO DAW                                         ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Transport
        t = self._transport
        play = "▶" if t.playing else "⏸"
        rec = "⏺" if t.recording else "  "
        metro = "🔔" if t.metronome else "🔕"
        lines.append(f"  {play} {rec}  BPM: {t.bpm:.0f}  {t.time_signature.value}  Pos: {t.position_str}  {metro}  Loop: {t.loop_mode.value}")
        lines.append(f"  Timeline: [{t.position_bar}]")
        lines.append("")

        # Markers
        if self._markers:
            marker_str = "  ".join(f"📍{m.name}({m.position_beat:.0f})" for m in self._markers[:5])
            lines.append(f"  {marker_str}")
            lines.append("")

        # Tracks
        lines.append(f"  ── Tracks ({self.total_tracks}) ──")
        for i, track in enumerate(self._tracks):
            sel = "▶" if i == self._selected_track else " "
            state = track.state_icon
            arm = "🔴" if track.armed else "  "
            mute = "🔇" if track.muted else "  "
            solo = "🔊" if track.solo else "  "

            # Volume meter
            vol_display = f"[{track.volume_bar}]"

            lines.append(f"  {sel} {state} {arm} {mute} {solo} {track.name:<16s} Vol: {track.volume_db:+.1f}dB {vol_display} Pan: [{track.pan_bar}]")

            # Clips
            clips = track.audio_clips + track.midi_clips
            if clips:
                clip_names = " | ".join(c.name for c in clips[:4])
                lines.append(f"      🎵 {clip_names}")

            # Effects
            if track.effects:
                eff_str = " → ".join(f"{e.status_icon}{e.effect_type.value}" for e in track.effects)
                lines.append(f"      🔊 {eff_str}")

            lines.append("")

        # Selected track detail
        track = self.selected_track
        if track:
            lines.append(f"  ── {track.name} Detail ──")
            lines.append(f"  Type: {track.track_type.value}  State: {track.state.value}  Output: {track.output_dest}")
            lines.append(f"  Volume: {track.volume_db:+.1f}dB  Gain: {track.gain_db:+.1f}dB  Pan: {track.pan:.2f}")
            if track.effects:
                lines.append(f"  Effects: {track.effect_chain}")
            lines.append("")

        # CPU/Memory
        lines.append(f"  ── System ──")
        lines.append(f"  Sample Rate: {self._sample_rate}Hz  Buffer: {self._buffer_size}  Tracks: {self.total_tracks}  Clips: {self.total_clips}  Effects: {self.total_effects}")
        lines.append("")

        lines.append("  [Space]Play/Pause [R]Record [M]Metronome [N]New Track [D]Delete")
        lines.append("  [↑↓]Select Track [+/-]Volume")
        return lines
