from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class RecordFormat(Enum):
    WAV = "wav"
    FLAC = "flac"
    MP3 = "mp3"
    OGG = "ogg"


class NoiseReduction(Enum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    AGGRESSIVE = "aggressive"


@dataclass
class Recording:
    name: str
    duration_secs: float
    format: RecordFormat
    sample_rate: int
    channels: int
    bitrate_kbps: int
    noise_reduction: NoiseReduction
    timestamp: float
    transcription: str = ""
    is_favorite: bool = False
    tags: list = field(default_factory=list)
    _paused: bool = False
    _amplitudes: list = field(default_factory=list)

    @property
    def display_duration(self) -> str:
        m, s = divmod(int(self.duration_secs), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @property
    def display_size(self) -> str:
        bytes_val = self.duration_secs * self.sample_rate * self.channels * 2
        if bytes_val >= 1024 * 1024:
            return f"{bytes_val / (1024 * 1024):.1f} MB"
        return f"{bytes_val / 1024:.0f} KB"

    @property
    def waveform(self) -> str:
        if not self._amplitudes:
            return "░" * 32
        chars = " ▁▂▃▄▅▆▇█"
        scaled = [min(int(a * 8), 8) for a in self._amplitudes[:32]]
        return "".join(chars[c] for c in scaled)


class VoiceRecorder:
    def __init__(self):
        self._recordings: list[Recording] = []
        self._selected: int = 0
        self._is_recording: bool = False
        self._is_paused: bool = False
        self._current_format: RecordFormat = RecordFormat.WAV
        self._current_sample_rate: int = 44100
        self._current_channels: int = 1
        self._current_bitrate: int = 128
        self._current_noise_reduction: NoiseReduction = NoiseReduction.MEDIUM
        self._record_start: float = 0
        self._total_recorded_secs: float = 0
        self._view: str = "list"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        samples = [
            Recording("Meeting Notes", 180, RecordFormat.WAV, 44100, 1, 128, NoiseReduction.MEDIUM, now - 7200, "Team meeting about Nyrqis OS development priorities and next milestones", tags=["meeting", "work"]),
            Recording("Voice Memo - Ideas", 45, RecordFormat.FLAC, 48000, 1, 128, NoiseReduction.LOW, now - 3600, "Brainstorming new features for the desktop environment", tags=["ideas"]),
            Recording("Podcast Recording", 3600, RecordFormat.MP3, 44100, 2, 192, NoiseReduction.HIGH, now - 86400, "Full podcast episode about open source operating systems", tags=["podcast"], is_favorite=True),
            Recording("Quick Reminder", 12, RecordFormat.WAV, 44100, 1, 128, NoiseReduction.OFF, now - 600, "Don't forget to push the compositor update before Friday"),
            Recording("Interview Prep", 300, RecordFormat.FLAC, 48000, 2, 256, NoiseReduction.MEDIUM, now - 1800, "Practice answers for technical interview questions about Wayland", tags=["interview", "prep"]),
            Recording("Ambient Recording", 600, RecordFormat.WAV, 96000, 2, 256, NoiseReduction.OFF, now - 43200, "", tags=["ambient"]),
        ]
        import random
        rng = random.Random(42)
        for r in samples:
            n = min(int(r.duration_secs / 2), 32)
            r._amplitudes = [rng.uniform(0.1, 0.9) for _ in range(n)]
        self._recordings = samples

    @property
    def selected_recording(self) -> Optional[Recording]:
        if 0 <= self._selected < len(self._recordings):
            return self._recordings[self._selected]
        return None

    @property
    def total_recordings(self) -> int:
        return len(self._recordings)

    @property
    def total_duration(self) -> float:
        return sum(r.duration_secs for r in self._recordings)

    @property
    def total_duration_display(self) -> str:
        total = int(self.total_duration)
        h, remainder = divmod(total, 3600)
        m, s = divmod(remainder, 60)
        return f"{h}h {m}m {s}s"

    @property
    def favorites_count(self) -> int:
        return sum(1 for r in self._recordings if r.is_favorite)

    @property
    def format_counts(self) -> dict:
        counts = {}
        for r in self._recordings:
            counts[r.format.value] = counts.get(r.format.value, 0) + 1
        return counts

    def select(self, idx: int):
        if 0 <= idx < len(self._recordings):
            self._selected = idx

    def start_recording(self):
        if not self._is_recording:
            self._is_recording = True
            self._is_paused = False
            self._record_start = time.time()
            self._total_recorded_secs = 0

    def stop_recording(self, name: str = "New Recording"):
        if self._is_recording:
            self._is_recording = False
            self._is_paused = False
            duration = self._total_recorded_secs
            if duration > 0:
                import random
                rng = random.Random(int(time.time()))
                rec = Recording(
                    name=name,
                    duration_secs=duration,
                    format=self._current_format,
                    sample_rate=self._current_sample_rate,
                    channels=self._current_channels,
                    bitrate_kbps=self._current_bitrate,
                    noise_reduction=self._current_noise_reduction,
                    timestamp=time.time(),
                    _amplitudes=[rng.uniform(0.1, 0.9) for _ in range(min(int(duration / 2), 32))],
                )
                self._recordings.append(rec)
                self._selected = len(self._recordings) - 1
                return rec
        return None

    def pause_recording(self):
        if self._is_recording:
            self._is_paused = True

    def resume_recording(self):
        if self._is_recording and self._is_paused:
            self._is_paused = False

    def toggle_favorite(self):
        rec = self.selected_recording
        if rec:
            rec.is_favorite = not rec.is_favorite

    def delete_recording(self, idx: int) -> bool:
        if 0 <= idx < len(self._recordings):
            self._recordings.pop(idx)
            if self._selected >= len(self._recordings):
                self._selected = max(0, len(self._recordings) - 1)
            return True
        return False

    def set_format(self, fmt: RecordFormat):
        self._current_format = fmt

    def set_noise_reduction(self, level: NoiseReduction):
        self._current_noise_reduction = level

    def set_sample_rate(self, rate: int):
        self._current_sample_rate = rate

    def render(self, width: int = 60, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════╗")
        lines.append("║               NYRQIS VOICE RECORDER                    ║")
        lines.append("╚══════════════════════════════════════════════════════════╝")
        lines.append("")
        if self._is_recording:
            status = "⏸ PAUSED" if self._is_paused else "🔴 RECORDING"
            lines.append(f"  Status: {status}")
            lines.append(f"  Format: {self._current_format.value.upper()}  {self._current_sample_rate}Hz  {self._current_channels}ch")
            lines.append(f"  Noise Reduction: {self._current_noise_reduction.value}")
            lines.append("")
        lines.append(f"  Total: {self.total_recordings} recordings · {self.total_duration_display} · ⭐ {self.favorites_count}")
        lines.append("")
        for i, r in enumerate(self._recordings):
            sel = "▶" if i == self._selected else " "
            fav = " ⭐" if r.is_favorite else ""
            lines.append(f"  {sel} {r.name}{fav}")
            lines.append(f"    {r.display_duration} · {r.format.value.upper()} · {r.display_size}")
            if r.waveform.strip():
                lines.append(f"    {r.waveform}")
        lines.append("")
        lines.append("  [S]tart  [P]ause  [T]ranscribe  [F]avorite  [D]elete")
        return lines

    def render_recording(self) -> list:
        r = self.selected_recording
        if not r:
            return ["  No recording selected"]
        lines = []
        lines.append(f"  ── {r.name} ──")
        lines.append(f"  Duration: {r.display_duration}")
        lines.append(f"  Format: {r.format.value.upper()} · {r.sample_rate}Hz · {r.channels}ch · {r.bitrate_kbps}kbps")
        lines.append(f"  Noise Reduction: {r.noise_reduction.value}")
        lines.append(f"  Size: {r.display_size}")
        lines.append(f"  Favorite: {'Yes' if r.is_favorite else 'No'}")
        if r.tags:
            lines.append(f"  Tags: {', '.join(r.tags)}")
        if r.transcription:
            lines.append(f"  Transcription: {r.transcription}")
        lines.append(f"  Waveform: {r.waveform}")
        return lines
