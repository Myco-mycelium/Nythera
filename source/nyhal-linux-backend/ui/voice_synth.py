from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class VoiceType(Enum):
    MALE_DEEP = "male-deep"
    MALE_NORMAL = "male-normal"
    FEMALE_HIGH = "female-high"
    FEMALE_NORMAL = "female-normal"
    CHILD = "child"
    ROBOT = "robot"
    WHISPER = "whisper"
    NARRATOR = "narrator"


class SpeechRate(Enum):
    VERY_SLOW = 0.5
    SLOW = 0.75
    NORMAL = 1.0
    FAST = 1.25
    VERY_FAST = 1.5
    SPEED_READ = 2.0


class EffectType(Enum):
    REVERB = "reverb"
    ECHO = "echo"
    CHORUS = "chorus"
    PITCH_SHIFT = "pitch-shift"
    FORMANT_SHIFT = "formant-shift"
    DISTORTION = "distortion"
    TREMOLO = "tremolo"
    VOCODER = "vocoder"


class ExportFormat(Enum):
    WAV = "wav"
    MP3 = "mp3"
    OGG = "ogg"
    FLAC = "flac"
    OPUS = "opus"


class SSMLTag(Enum):
    EMPHASIS = "emphasis"
    BREAK = "break"
    PROSODY = "prosody"
    SPELL_OUT = "spell-out"
    DATE = "date"
    NUMBER = "number"
    SAY_AS = "say-as"


@dataclass
class VoiceEffect:
    effect_type: EffectType
    mix: float
    enabled: bool = True
    params: dict = field(default_factory=dict)

    @property
    def mix_bar(self) -> str:
        filled = int(self.mix * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class SynthSegment:
    text: str
    voice: VoiceType
    rate: float
    pitch: float
    volume: float
    effects: list = field(default_factory=list)
    duration_ms: int = 0

    @property
    def display_text(self) -> str:
        return self.text[:50] + "..." if len(self.text) > 50 else self.text

    @property
    def display_duration(self) -> str:
        if self.duration_ms >= 1000:
            return f"{self.duration_ms / 1000:.1f}s"
        return f"{self.duration_ms}ms"


@dataclass
class SynthJob:
    name: str
    text: str
    voice: VoiceType
    rate: float
    pitch: float
    volume: float
    timestamp: float
    duration_ms: int = 0
    format: ExportFormat = ExportFormat.WAV
    is_complete: bool = True
    file_path: str = ""

    @property
    def display_duration(self) -> str:
        if self.duration_ms >= 60000:
            m, s = divmod(self.duration_ms // 1000, 60)
            return f"{m}:{s:02d}"
        return f"{self.duration_ms / 1000:.1f}s"


@dataclass
class PronunciationEntry:
    word: str
    phonetic: str
    language: str = "en-US"


class VoiceSynth:
    def __init__(self):
        self._text: str = ""
        self._voice: VoiceType = VoiceType.MALE_NORMAL
        self._rate: SpeechRate = SpeechRate.NORMAL
        self._pitch: float = 0.0
        self._volume: float = 0.8
        self._effects: list[VoiceEffect] = []
        self._segments: list[SynthSegment] = []
        self._jobs: list[SynthJob] = []
        self._selected_job: int = 0
        self._pronunciations: list[PronunciationEntry] = []
        self._export_format: ExportFormat = ExportFormat.WAV
        self._is_playing: bool = False
        self._play_position_ms: int = 0
        self._total_duration_ms: int = 0
        self._view: str = "editor"
        self._ssml_mode: bool = False
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._effects = [
            VoiceEffect(EffectType.REVERB, 0.3, params={"room": 0.7}),
            VoiceEffect(EffectType.ECHO, 0.2, params={"delay_ms": 200}),
        ]
        self._segments = [
            SynthSegment("Welcome to Nyrqis OS, the future of computing.", VoiceType.NARRATOR, 1.0, 0, 0.8, duration_ms=4500),
            SynthSegment("This system features a custom Wayland compositor built in Rust.", VoiceType.MALE_NORMAL, 1.0, 0, 0.8, duration_ms=5200),
            SynthSegment("Enjoy seamless integration with your favorite applications.", VoiceType.FEMALE_NORMAL, 1.0, 0, 0.75, duration_ms=4800),
        ]
        self._jobs = [
            SynthJob("Welcome Message", "Welcome to Nyrqis OS", VoiceType.NARRATOR, 1.0, 0, 0.8, now - 3600, 3500, ExportFormat.WAV, True),
            SynthJob("System Announcement", "System update available. Please restart to apply changes.", VoiceType.MALE_NORMAL, 1.0, 0, 0.8, now - 7200, 5200, ExportFormat.MP3, True),
            SynthJob("Error Notification", "Warning: Disk space is running low. Please free up space.", VoiceType.FEMALE_HIGH, 0.9, 2, 0.9, now - 1800, 4100, ExportFormat.WAV, True),
            SynthJob("Tutorial Voiceover", "In this tutorial, we will explore the Nyrqis desktop environment and its powerful features.", VoiceType.NARRATOR, 1.1, 0, 0.75, now - 86400, 8200, ExportFormat.MP3, True),
        ]
        self._pronunciations = [
            PronunciationEntry("Nyrqis", "nair-kiss", "en-US"),
            PronunciationEntry("Wayland", "way-land", "en-US"),
            PronunciationEntry("NVIDIA", "en-vidia", "en-US"),
            PronunciationEntry("mycelium", "my-see-lee-um", "en-US"),
            PronunciationEntry("compositor", "com-poh-zi-ter", "en-US"),
        ]

    @property
    def selected_job(self) -> Optional[SynthJob]:
        if 0 <= self._selected_job < len(self._jobs):
            return self._jobs[self._selected_job]
        return None

    @property
    def total_jobs(self) -> int:
        return len(self._jobs)

    @property
    def total_duration(self) -> str:
        total = sum(j.duration_ms for j in self._jobs)
        if total >= 60000:
            m, s = divmod(total // 1000, 60)
            return f"{m}:{s:02d}"
        return f"{total / 1000:.1f}s"

    @property
    def play_progress(self) -> float:
        if self._total_duration_ms == 0:
            return 0
        return self._play_position_ms / self._total_duration_ms

    def select_job(self, idx: int):
        if 0 <= idx < len(self._jobs):
            self._selected_job = idx

    def synthesize(self, text: str) -> list:
        self._text = text
        words = text.split()
        chunk_size = max(1, len(words) // 3)
        segments = []
        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i:i + chunk_size])
            duration = len(chunk.split()) * 250 / self._rate.value
            seg = SynthSegment(chunk, self._voice, self._rate.value, self._pitch, self._volume,
                               list(self._effects), int(duration))
            segments.append(seg)
        self._segments = segments
        self._total_duration_ms = sum(s.duration_ms for s in segments)
        return segments

    def add_effect(self, effect: VoiceEffect):
        self._effects.append(effect)

    def remove_effect(self, idx: int):
        if 0 <= idx < len(self._effects):
            self._effects.pop(idx)

    def add_pronunciation(self, word: str, phonetic: str):
        self._pronunciations.append(PronunciationEntry(word, phonetic))

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS VOICE SYNTHESIZER                                ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        play = "▶ PLAYING" if self._is_playing else "⏹ STOPPED"
        lines.append(f"  Status: {play}  Voice: {self._voice.value}  Rate: {self._rate.value}x  Pitch: {self._pitch:+.1f}")
        lines.append(f"  Volume: {self._volume:.0%}  Format: {self._export_format.value.upper()}  SSML: {'ON' if self._ssml_mode else 'OFF'}")
        lines.append(f"  Effects: {len(self._effects)}  Pronunciations: {len(self._pronunciations)}")
        lines.append("")
        lines.append(f"  ── Input ──")
        if self._text:
            lines.append(f"  {self._text[:70]}")
            if len(self._text) > 70:
                lines.append(f"  {self._text[70:140]}")
        else:
            lines.append(f"  (enter text to synthesize)")
        lines.append("")
        lines.append(f"  ── Segments ({len(self._segments)}) ──")
        for i, s in enumerate(self._segments):
            lines.append(f"  {i+1}. {s.display_text}  [{s.display_duration}]  {s.voice.value}")
        lines.append("")
        lines.append("  ── Effects ──")
        for eff in self._effects:
            status = "🟢" if eff.enabled else "⚪"
            lines.append(f"  {status} {eff.effect_type.value:<16s} [{eff.mix_bar}] {eff.mix:.0%}")
        lines.append("")
        lines.append("  ── Recent Jobs ──")
        for i, j in enumerate(self._jobs[:4]):
            sel = "▶" if i == self._selected_job else " "
            lines.append(f"  {sel} {j.name}  {j.display_duration}  {j.format.value}  {j.voice.value}")
        lines.append("")
        lines.append("  [S]ynthesize  [P]lay  [E]ffects  [V]oice  [R]ate  [X]Export  [P]ronounce")
        return lines

    def render_voices(self) -> list:
        lines = []
        lines.append("  ── Voice Selection ──")
        lines.append("")
        for v in VoiceType:
            sel = "▶" if v == self._voice else " "
            lines.append(f"  {sel} {v.value}")
        return lines

    def render_effects(self) -> list:
        lines = []
        lines.append("  ── Available Effects ──")
        lines.append("")
        for e in EffectType:
            active = any(ef.effect_type == e for ef in self._effects)
            status = "🟢" if active else "  "
            lines.append(f"  {status} {e.value}")
        return lines

    def render_pronunciations(self) -> list:
        lines = []
        lines.append("  ── Pronunciation Dictionary ──")
        lines.append("")
        for p in self._pronunciations:
            lines.append(f"  {p.word:<16s} /{p.phonetic}/  ({p.language})")
        return lines
