from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math


class Waveform(Enum):
    SINE = "sine"
    SQUARE = "square"
    SAWTOOTH = "sawtooth"
    TRIANGLE = "triangle"
    PULSE = "pulse"
    NOISE = "noise"


class FilterType(Enum):
    LOWPASS = "lowpass"
    HIGHPASS = "highpass"
    BANDPASS = "bandpass"
    NOTCH = "notch"
    ALLPASS = "allpass"
    RESONANT = "resonant"


class LFOShape(Enum):
    SINE = "sine"
    SQUARE = "square"
    TRIANGLE = "triangle"
    SAWTOOTH = "sawtooth"


class EffectType(Enum):
    REVERB = "reverb"
    DELAY = "delay"
    CHORUS = "chorus"
    DISTORTION = "distortion"
    COMPRESSOR = "compressor"
    EQ = "eq"
    PHASER = "phaser"
    FLANGER = "flanger"


class EnvelopeStage(Enum):
    ATTACK = "attack"
    DECAY = "decay"
    SUSTAIN = "sustain"
    RELEASE = "release"


class ArpeggioMode(Enum):
    OFF = "off"
    UP = "up"
    DOWN = "down"
    UP_DOWN = "up-down"
    RANDOM = "random"
    ORDERED = "ordered"


@dataclass
class Oscillator:
    waveform: Waveform
    volume: float
    detune_cents: int = 0
    octave_shift: int = 0
    phase: float = 0.0

    @property
    def volume_bar(self) -> str:
        filled = int(self.volume * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def waveform_icon(self) -> str:
        icons = {Waveform.SINE: "∿", Waveform.SQUARE: "⌇", Waveform.SAWTOOTH: "⩘", Waveform.TRIANGLE: "△", Waveform.PULSE: "⏍", Waveform.NOISE: "▓"}
        return icons.get(self.waveform, "?")


@dataclass
class Filter:
    filter_type: FilterType
    cutoff_hz: float
    resonance: float
    enabled: bool = True
    key_track: float = 0.5

    @property
    def cutoff_bar(self) -> str:
        pct = min(self.cutoff_hz / 20000, 1.0)
        filled = int(pct * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def resonance_bar(self) -> str:
        filled = int(self.resonance * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class LFO:
    shape: LFOShape
    rate_hz: float
    depth: float
    target: str = "pitch"
    enabled: bool = True

    @property
    def rate_bar(self) -> str:
        filled = int(min(self.rate_hz / 20, 1.0) * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class Envelope:
    attack_ms: float
    decay_ms: float
    sustain_level: float
    release_ms: float

    @property
    def attack_bar(self) -> str:
        filled = int(min(self.attack_ms / 2000, 1.0) * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def release_bar(self) -> str:
        filled = int(min(self.release_ms / 2000, 1.0) * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class Effect:
    effect_type: EffectType
    mix: float
    enabled: bool = True
    params: dict = field(default_factory=dict)

    @property
    def mix_bar(self) -> str:
        filled = int(self.mix * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class SynthPreset:
    name: str
    category: str
    oscillators: list = field(default_factory=list)
    filter: Optional[Filter] = None
    envelope: Optional[Envelope] = None
    effects: list = field(default_factory=list)


class Synthesizer:
    def __init__(self):
        self._oscillators: list[Oscillator] = []
        self._filter: Optional[Filter] = None
        self._envelope: Optional[Envelope] = None
        self._lfo: Optional[LFO] = None
        self._effects: list[Effect] = []
        self._presets: list[SynthPreset] = []
        self._selected_preset: int = 0
        self._volume: float = 0.8
        self._polyphony: int = 8
        self._active_voices: int = 0
        self._pitch_bend: float = 0.0
        self._mod_wheel: float = 0.0
        self._octave: int = 4
        self._transpose: int = 0
        self._arpeggio_mode: ArpeggioMode = ArpeggioMode.OFF
        self._arpeggio_rate: int = 8
        self._view: str = "oscillators"
        self._view_tab: int = 0
        self._active_notes: list[str] = []
        self._waveform_display: list[float] = []
        self._create_samples()

    def _create_samples(self):
        self._oscillators = [
            Oscillator(Waveform.SAWTOOTH, 0.7, detune_cents=0, octave_shift=0),
            Oscillator(Waveform.SAWTOOTH, 0.7, detune_cents=7, octave_shift=0),
            Oscillator(Waveform.SINE, 0.3, octave_shift=-1),
        ]
        self._filter = Filter(FilterType.LOWPASS, 2500, 0.4)
        self._envelope = Envelope(10, 200, 0.7, 300)
        self._lfo = LFO(LFOShape.SINE, 5.0, 0.3, "cutoff")
        self._effects = [
            Effect(EffectType.REVERB, 0.3, params={"room_size": 0.7, "damping": 0.5}),
            Effect(EffectType.DELAY, 0.25, params={"time_ms": 375, "feedback": 0.4}),
            Effect(EffectType.CHORUS, 0.2, params={"rate_hz": 1.5, "depth_ms": 20}),
            Effect(EffectType.COMPRESSOR, 0.5, params={"threshold_db": -20, "ratio": 4}),
            Effect(EffectType.EQ, 0.6, params={"low_gain": 3, "mid_gain": 0, "high_gain": -2}),
        ]
        self._presets = [
            SynthPreset("Supersaw Lead", "Lead", [Oscillator(Waveform.SAWTOOTH, 0.7), Oscillator(Waveform.SAWTOOTH, 0.7, detune_cents=12)], Filter(FilterType.LOWPASS, 3000, 0.5), Envelope(5, 150, 0.8, 200)),
            SynthPreset("808 Bass", "Bass", [Oscillator(Waveform.SINE, 1.0, octave_shift=-1)], Filter(FilterType.LOWPASS, 800, 0.2), Envelope(1, 100, 0.5, 50)),
            SynthPreset("Pad Warm", "Pad", [Oscillator(Waveform.SAWTOOTH, 0.5), Oscillator(Waveform.TRIANGLE, 0.5)], Filter(FilterType.LOWPASS, 4000, 0.3), Envelope(500, 500, 0.6, 1000)),
            SynthPreset("Pluck", "Pluck", [Oscillator(Waveform.SAWTOOTH, 0.8)], Filter(FilterType.LOWPASS, 5000, 0.7), Envelope(1, 50, 0.3, 100)),
            SynthPreset("Brass Stab", "Brass", [Oscillator(Waveform.SAWTOOTH, 0.6), Oscillator(Waveform.SQUARE, 0.4)], Filter(FilterType.LOWPASS, 2000, 0.4), Envelope(20, 200, 0.7, 300)),
        ]
        # Generate waveform display
        self._waveform_display = [math.sin(i * 0.1) * 0.8 for i in range(64)]

    @property
    def selected_preset(self) -> Optional[SynthPreset]:
        if 0 <= self._selected_preset < len(self._presets):
            return self._presets[self._selected_preset]
        return None

    @property
    def total_presets(self) -> int:
        return len(self._presets)

    @property
    def waveform_str(self) -> str:
        chars = " ▁▂▃▄▅▆▇█"
        result = ""
        for val in self._waveform_display:
            idx = int((val + 1) / 2 * 8)
            idx = max(0, min(8, idx))
            result += chars[idx]
        return result

    def select_preset(self, idx: int):
        if 0 <= idx < len(self._presets):
            self._selected_preset = idx

    def note_on(self, note: str):
        if note not in self._active_notes:
            self._active_notes.append(note)
            self._active_voices = len(self._active_notes)

    def note_off(self, note: str):
        if note in self._active_notes:
            self._active_notes.remove(note)
            self._active_voices = len(self._active_notes)

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS VIRTUAL SYNTHESIZER                              ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Preset: {self._presets[self._selected_preset].name if self._presets else 'None'}  Volume: {self._volume:.0%}  Poly: {self._active_voices}/{self._polyphony}")
        lines.append(f"  Octave: {self._octave}  Transpose: {self._transpose:+d}  Pitch Bend: {self._pitch_bend:+.1f}")
        lines.append(f"  Arp: {self._arpeggio_mode.value}  Rate: 1/{self._arpeggio_rate}")
        lines.append("")
        lines.append(f"  Waveform: {self.waveform_str}")
        lines.append("")
        lines.append("  ── Oscillators ──")
        for i, osc in enumerate(self._oscillators):
            lines.append(f"  {osc.waveform_icon} Osc {i+1}: [{osc.volume_bar}] {osc.waveform.value}  detune: {osc.detune_cents:+d}ct  oct: {osc.octave_shift:+d}")
        lines.append("")
        if self._filter:
            f = self._filter
            lines.append(f"  ── Filter ({f.filter_type.value}) ──")
            lines.append(f"  Cutoff: [{f.cutoff_bar}] {f.cutoff_hz:.0f}Hz  Res: [{f.resonance_bar}] {f.resonance:.1f}")
        lines.append("")
        if self._envelope:
            e = self._envelope
            lines.append(f"  ── Envelope ──")
            lines.append(f"  A:[{e.attack_bar}] {e.attack_ms:.0f}ms  D:[{e.release_bar}] {e.decay_ms:.0f}ms  S:{e.sustain_level:.0%}  R:[{e.release_bar}] {e.release_ms:.0f}ms")
        lines.append("")
        if self._lfo:
            l = self._lfo
            lines.append(f"  ── LFO ──")
            lines.append(f"  {l.shape.value} [{l.rate_bar}] {l.rate_hz:.1f}Hz  depth: {l.depth:.0%}  target: {l.target}")
        lines.append("")
        lines.append("  ── Effects ──")
        for eff in self._effects:
            status = "🟢" if eff.enabled else "⚪"
            lines.append(f"  {status} {eff.effect_type.value:<12s} [{eff.mix_bar}] {eff.mix:.0%}")
        lines.append("")
        lines.append("  [O]scillators  [F]ilter  [E]nvelope  [L]FO  [X]FX  [P]resets  [A]rp")
        return lines

    def render_presets(self) -> list:
        lines = []
        lines.append("  ── Presets ──")
        lines.append("")
        for i, p in enumerate(self._presets):
            sel = "▶" if i == self._selected_preset else " "
            lines.append(f"  {sel} {p.name}  ({p.category})")
        return lines

    def render_preset_detail(self) -> list:
        p = self.selected_preset
        if not p:
            return ["  No preset selected"]
        lines = []
        lines.append(f"  ── {p.name} ({p.category}) ──")
        lines.append("")
        for i, osc in enumerate(p.oscillators):
            lines.append(f"  Osc {i+1}: {osc.waveform.value} vol={osc.volume:.0%} detune={osc.detune_cents:+d}ct oct={osc.octave_shift:+d}")
        if p.filter:
            lines.append(f"  Filter: {p.filter.filter_type.value} cutoff={p.filter.cutoff_hz:.0f}Hz res={p.filter.resonance:.1f}")
        if p.envelope:
            lines.append(f"  ADSR: {p.envelope.attack_ms:.0f}/{p.envelope.decay_ms:.0f}/{p.envelope.sustain_level:.0%}/{p.envelope.release_ms:.0f}")
        return lines

    def render_keyboard(self) -> str:
        notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        white = ["C", "D", "E", "F", "G", "A", "B"]
        line1 = "  "
        line2 = "  "
        for note in white:
            octave_note = f"{note}{self._octave}"
            active = octave_note in self._active_notes
            key = "██" if active else "░░"
            line1 += f"{key} "
            sharp = f"{note}#{self._octave}"
            s_active = sharp in self._active_notes
            s_key = "█" if s_active else "░"
            line2 += f" {s_key} "
        return f"{line1}\n{line2}"
