from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math


class OscWaveform(Enum):
    SINE = "sine"
    SAW = "saw"
    SQUARE = "square"
    TRIANGLE = "triangle"
    PULSE = "pulse"
    NOISE = "noise"
    FM = "fm"


class LFOShape(Enum):
    SINE = "sine"
    SQUARE = "square"
    TRIANGLE = "triangle"
    SAW = "saw"
    RANDOM = "random"


class FilterType(Enum):
    LOWPASS = "lowpass"
    HIGHPASS = "highpass"
    BANDPASS = "bandpass"
    NOTCH = "notch"
    ALLPASS = "allpass"
    PHASER = "phaser"


class EffectSlot(Enum):
    REVERB = "reverb"
    DELAY = "delay"
    CHORUS = "chorus"
    DISTORTION = "distortion"
    COMPRESSOR = "compressor"
    EQ_3BAND = "eq-3band"
    FLANGER = "flanger"
    GATE = "gate"


class ArpMode(Enum):
    OFF = "off"
    UP = "up"
    DOWN = "down"
    UP_DOWN = "up-down"
    RANDOM = "random"
    CHORD = "chord"


class EnvelopeStage(Enum):
    ATTACK = "attack"
    DECAY = "decay"
    SUSTAIN = "sustain"
    RELEASE = "release"


@dataclass
class Oscillator:
    waveform: OscWaveform
    volume: float = 0.7
    detune_cents: int = 0
    octave: int = 0
    phase: float = 0
    pulse_width: float = 0.5
    fm_amount: float = 0
    enabled: bool = True

    @property
    def vol_bar(self) -> str:
        return "█" * int(self.volume * 10) + "░" * (10 - int(self.volume * 10))

    @property
    def icon(self) -> str:
        icons = {"sine": "∿", "saw": "⩘", "square": "⌇", "triangle": "△", "pulse": "⏍", "noise": "▓", "fm": "≋"}
        return icons.get(self.waveform.value, "?")


@dataclass
class LFO:
    shape: LFOShape
    rate: float = 5.0
    depth: float = 0.5
    target: str = "pitch"
    enabled: bool = True

    @property
    def rate_bar(self) -> str:
        return "█" * int(min(self.rate / 20, 1.0) * 10) + "░" * (10 - int(min(self.rate / 20, 1.0) * 10))

    @property
    def depth_bar(self) -> str:
        return "█" * int(self.depth * 10) + "░" * (10 - int(self.depth * 10))


@dataclass
class Filter:
    filter_type: FilterType
    cutoff: float = 5000
    resonance: float = 0.3
    drive: float = 0
    key_track: float = 0.5
    enabled: bool = True

    @property
    def cutoff_bar(self) -> str:
        pct = min(self.cutoff / 20000, 1.0)
        return "█" * int(pct * 20) + "░" * (20 - int(pct * 20))

    @property
    def res_bar(self) -> str:
        return "█" * int(self.resonance * 10) + "░" * (10 - int(self.resonance * 10))


@dataclass
class Envelope:
    attack: float = 0.01
    decay: float = 0.3
    sustain: float = 0.7
    release: float = 0.3

    @property
    def attack_bar(self) -> str:
        return "█" * int(min(self.attack * 5, 10)) + "░" * (10 - int(min(self.attack * 5, 10)))

    @property
    def release_bar(self) -> str:
        return "█" * int(min(self.release * 5, 10)) + "░" * (10 - int(min(self.release * 5, 10)))


@dataclass
class SynthEffect:
    effect_type: EffectSlot
    mix: float = 0.5
    enabled: bool = True
    params: dict = field(default_factory=dict)

    @property
    def mix_bar(self) -> str:
        return "█" * int(self.mix * 10) + "░" * (10 - int(self.mix * 10))


@dataclass
class SynthPreset:
    name: str
    category: str
    osc1_wave: OscWaveform
    osc2_wave: OscWaveform
    osc_mix: float
    filter_type: FilterType
    filter_cutoff: float
    filter_res: float
    attack: float
    decay: float
    sustain: float
    release: float


class SynthPro:
    def __init__(self):
        self._oscillators: list[Oscillator] = []
        self._lfo: LFO = LFO(LFOShape.SINE)
        self._filter: Filter = Filter(FilterType.LOWPASS)
        self._envelope: Envelope = Envelope()
        self._effects: list[SynthEffect] = []
        self._presets: list[SynthPreset] = []
        self._selected_preset: int = 0
        self._selected_osc: int = 0
        self._selected_effect: int = 0
        self._volume: float = 0.8
        self._polyphony: int = 8
        self._active_voices: int = 0
        self._arp_mode: ArpMode = ArpMode.OFF
        self._arp_rate: int = 8
        self._octave: int = 4
        self._transpose: int = 0
        self._pitch_bend: float = 0
        self._mod_wheel: float = 0
        self._waveform_display: list[float] = []
        self._view: str = "osc"
        self._create_samples()

    def _create_samples(self):
        self._oscillators = [
            Oscillator(OscWaveform.SAW, 0.7, detune_cents=0, octave=0),
            Oscillator(OscWaveform.SAW, 0.7, detune_cents=7, octave=0),
            Oscillator(OscWaveform.SINE, 0.3, octave=-1),
        ]
        self._lfo = LFO(LFOShape.SINE, 5.0, 0.3, "cutoff")
        self._filter = Filter(FilterType.LOWPASS, 2500, 0.4)
        self._envelope = Envelope(0.01, 0.3, 0.7, 0.3)
        self._effects = [
            SynthEffect(EffectSlot.REVERB, 0.3),
            SynthEffect(EffectSlot.DELAY, 0.25),
            SynthEffect(EffectSlot.CHORUS, 0.2),
            SynthEffect(EffectSlot.COMPRESSOR, 0.5),
            SynthEffect(EffectSlot.EQ_3BAND, 0.6),
        ]
        self._presets = [
            SynthPreset("Supersaw Lead", "Lead", OscWaveform.SAW, OscWaveform.SAW, 0.5, FilterType.LOWPASS, 3000, 0.5, 0.005, 0.2, 0.8, 0.2),
            SynthPreset("Pad Warm", "Pad", OscWaveform.SAW, OscWaveform.TRIANGLE, 0.5, FilterType.LOWPASS, 4000, 0.3, 0.5, 0.5, 0.6, 1.0),
            SynthPreset("Bass Sub", "Bass", OscWaveform.SINE, OscWaveform.SQUARE, 0.6, FilterType.LOWPASS, 800, 0.2, 0.001, 0.1, 0.5, 0.1),
            SynthPreset("Pluck", "Pluck", OscWaveform.SAW, OscWaveform.SAW, 0.8, FilterType.LOWPASS, 5000, 0.7, 0.001, 0.05, 0.3, 0.1),
            SynthPreset("Brass Stab", "Brass", OscWaveform.SAW, OscWaveform.SQUARE, 0.5, FilterType.LOWPASS, 2000, 0.4, 0.02, 0.2, 0.7, 0.3),
            SynthPreset("Atmosphere", "Pad", OscWaveform.NOISE, OscWaveform.TRIANGLE, 0.3, FilterType.BANDPASS, 1500, 0.8, 1.0, 0.8, 0.4, 2.0),
        ]
        self._waveform_display = [math.sin(i * 0.1) * 0.8 for i in range(64)]

    @property
    def selected_preset(self) -> Optional[SynthPreset]:
        if 0 <= self._selected_preset < len(self._presets):
            return self._presets[self._selected_preset]
        return None

    @property
    def waveform_str(self) -> str:
        chars = " ▁▂▃▄▅▆▇█"
        return "".join(chars[min(int((v + 1) / 2 * 8), 8)] for v in self._waveform_display)

    def select_preset(self, idx: int):
        if 0 <= idx < len(self._presets):
            self._selected_preset = idx

    def note_on(self, note: str):
        self._active_voices += 1

    def note_off(self, note: str):
        self._active_voices = max(0, self._active_voices - 1)

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS SYNTHESIZER PRO                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        preset = self._presets[self._selected_preset].name if self._presets else "None"
        lines.append(f"  Preset: {preset}  Vol: {self._volume:.0%}  Poly: {self._active_voices}/{self._polyphony}  Oct: {self._octave}  Trans: {self._transpose:+d}")
        lines.append(f"  Arp: {self._arp_mode.value}  Rate: 1/{self._arp_rate}  Bend: {self._pitch_bend:+.1f}  Mod: {self._mod_wheel:.0%}")
        lines.append("")
        lines.append(f"  Waveform: {self.waveform_display[:40]}")
        lines.append("")
        lines.append("  ── Oscillators ──")
        for i, osc in enumerate(self._oscillators):
            sel = "▶" if i == self._selected_osc else " "
            lines.append(f"  {sel}{osc.icon} Osc {i+1} [{osc.vol_bar}] {osc.waveform.value}  detune:{osc.detune_cents:+d}ct  oct:{osc.octave:+d}  pw:{osc.pulse_width:.0%}")
        lines.append("")
        lines.append(f"  ── Filter ({self._filter.filter_type.value}) ──")
        lines.append(f"  Cutoff: [{self._filter.cutoff_bar}] {self._filter.cutoff:.0f}Hz  Res: [{self._filter.res_bar}] {self._filter.resonance:.1f}  Drive: {self._filter.drive:.1f}")
        lines.append("")
        lines.append(f"  ── Envelope ──")
        e = self._envelope
        lines.append(f"  A:[{e.attack_bar}] {e.attack:.2f}s  D:[{e.attack_bar}] {e.decay:.2f}s  S:{e.sustain:.0%}  R:[{e.release_bar}] {e.release:.2f}s")
        lines.append("")
        lfo = self._lfo
        lines.append(f"  ── LFO ──")
        lines.append(f"  {lfo.shape.value} [{lfo.rate_bar}] {lfo.rate:.1f}Hz  depth:[{lfo.depth_bar}] {lfo.depth:.0%}  target: {lfo.target}")
        lines.append("")
        lines.append("  ── Effects ──")
        for i, eff in enumerate(self._effects):
            sel = "▶" if i == self._selected_effect else " "
            status = "🟢" if eff.enabled else "⚪"
            lines.append(f"  {sel}{status} {eff.effect_type.value:<12s} [{eff.mix_bar}] {eff.mix:.0%}")
        lines.append("")
        lines.append("  ── Presets ──")
        for i, p in enumerate(self._presets):
            sel = "▶" if i == self._selected_preset else " "
            lines.append(f"  {sel} {p.name}  ({p.category})")
        lines.append("")
        lines.append("  [O]sc  [F]ilter  [E]nvelope  [L]FO  [X]FX  [P]reset  [A]rp  [N]ote")
        return lines
