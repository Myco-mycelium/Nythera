"""
Nyrqis Speech — text-to-speech and speech-to-text accessibility tools.

Features:
- Text-to-speech with voice selection, speed, pitch, volume
- Speech-to-text with language detection and punctuation
- Voice presets (Male, Female, Child, Robot)
- SSML support for advanced speech control
- Speech history with playback
- Language selection (English, Spanish, French, German, Japanese, etc.)
- Reading speed control (0.5x to 3.0x)
- Pronunciation dictionary
- Clipboard speak (read clipboard aloud)
- Screen reader integration hooks
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class VoiceType(Enum):
    MALE = "Male"
    FEMALE = "Female"
    CHILD = "Child"
    ROBOT = "Neutral"


class SpeechLanguage(Enum):
    ENGLISH = "English"
    SPANISH = "Spanish"
    FRENCH = "French"
    GERMAN = "German"
    JAPANESE = "Japanese"
    PORTUGUESE = "Portuguese"
    CHINESE = "Chinese"
    KOREAN = "Korean"
    ITALIAN = "Italian"
    RUSSIAN = "Russian"


LANGUAGE_CODES = {
    SpeechLanguage.ENGLISH: "en-US",
    SpeechLanguage.SPANISH: "es-ES",
    SpeechLanguage.FRENCH: "fr-FR",
    SpeechLanguage.GERMAN: "de-DE",
    SpeechLanguage.JAPANESE: "ja-JP",
    SpeechLanguage.PORTUGUESE: "pt-BR",
    SpeechLanguage.CHINESE: "zh-CN",
    SpeechLanguage.KOREAN: "ko-KR",
    SpeechLanguage.ITALIAN: "it-IT",
    SpeechLanguage.RUSSIAN: "ru-RU",
}


class SpeechStatus(Enum):
    IDLE = "idle"
    SPEAKING = "speaking"
    PAUSED = "paused"
    RECORDING = "recording"
    PROCESSING = "processing"


@dataclass
class Voice:
    """A TTS voice configuration."""
    name: str
    voice_type: VoiceType
    language: SpeechLanguage
    speed: float = 1.0
    pitch: float = 1.0
    volume: float = 1.0

    @property
    def display(self) -> str:
        return f"{self.name} ({self.voice_type.value})"

    @property
    def lang_code(self) -> str:
        return LANGUAGE_CODES.get(self.language, "en-US")


@dataclass
class SpeechEntry:
    """A text-to-speech or speech-to-text entry."""
    text: str
    is_tts: bool = True  # True = TTS, False = STT
    language: SpeechLanguage = SpeechLanguage.ENGLISH
    voice: str = "Default"
    speed: float = 1.0
    timestamp: float = field(default_factory=time.time)
    duration_seconds: float = 0.0
    entry_id: str = ""

    def __post_init__(self):
        if not self.entry_id:
            self.entry_id = hashlib.md5(f"{self.text}{self.timestamp}".encode()).hexdigest()[:8]

    @property
    def preview(self) -> str:
        return self.text[:60] + "..." if len(self.text) > 60 else self.text

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.timestamp).strftime("%b %d")

    @property
    def duration_str(self) -> str:
        m = int(self.duration_seconds // 60)
        s = int(self.duration_seconds % 60)
        if m > 0:
            return f"{m}m {s}s"
        return f"{s}s"


@dataclass
class PronunciationEntry:
    """A custom pronunciation."""
    word: str
    phonetic: str
    language: SpeechLanguage = SpeechLanguage.ENGLISH


# ─── Speech Tools ────────────────────────────────────────────────────────


class SpeechTools:
    """
    Text-to-speech and speech-to-text tools for Nyrqis OS.
    """

    def __init__(self):
        # TTS state
        self._tts_status: SpeechStatus = SpeechStatus.IDLE
        self._tts_text: str = ""
        self._tts_voice: Voice = Voice("David", VoiceType.MALE, SpeechLanguage.ENGLISH)
        self._tts_speed: float = 1.0
        self._tts_pitch: float = 1.0
        self._tts_volume: float = 1.0
        self._tts_position: int = 0  # Current speaking position in text

        # STT state
        self._stt_status: SpeechStatus = SpeechStatus.IDLE
        self._stt_text: str = ""
        self._stt_language: SpeechLanguage = SpeechLanguage.ENGLISH
        self._stt_buffer: str = ""

        # Voices
        self._voices = [
            Voice("David", VoiceType.MALE, SpeechLanguage.ENGLISH, 1.0, 1.0),
            Voice("Sarah", VoiceType.FEMALE, SpeechLanguage.ENGLISH, 1.0, 1.2),
            Voice("Tommy", VoiceType.CHILD, SpeechLanguage.ENGLISH, 1.1, 1.4),
            Voice("Nova", VoiceType.ROBOT, SpeechLanguage.ENGLISH, 1.0, 0.8),
            Voice("Carlos", VoiceType.MALE, SpeechLanguage.SPANISH, 1.0, 1.0),
            Voice("Marie", VoiceType.FEMALE, SpeechLanguage.FRENCH, 1.0, 1.1),
            Voice("Hans", VoiceType.MALE, SpeechLanguage.GERMAN, 1.0, 0.9),
            Voice("Yuki", VoiceType.FEMALE, SpeechLanguage.JAPANESE, 1.0, 1.3),
        ]
        self._selected_voice_index: int = 0

        # History
        self._history: List[SpeechEntry] = []
        self._max_history: int = 100

        # Pronunciation dictionary
        self._pronunciations: List[PronunciationEntry] = [
            PronunciationEntry("Nyrqis", "NIR-kiss"),
            PronunciationEntry("Wayland", "WAY-land"),
            PronunciationEntry("mycelium", "my-SEE-lee-um"),
            PronunciationEntry("compositor", "com-POZ-i-ter"),
        ]

        # View state
        self._view_mode: str = "tts"  # tts, stt, voices, history, pronunciations
        self._selected_index: int = 0

        # SSML templates
        self._ssml_templates = [
            ("Emphasis", '<emphasis level="strong">important text</emphasis>'),
            ("Break", ' sentence.<break time="500ms"/> Next sentence.'),
            ("Spell out", '<say-as interpret-as="characters">NASA</say-as>'),
            ("Date", '<say-as interpret-as="date" format="mdy">12/25/2026</say-as>'),
            ("Number", '<say-as interpret-as="cardinal">12345</say-as>'),
            ("Prosody", '<prosody rate="slow" pitch="+2st">slow and high</prosody>'),
        ]

        # Callbacks
        self._on_speech: List[Callable] = []

    # ── Text-to-Speech ────────────────────────────────────────────────

    def speak(self, text: str = None) -> Optional[SpeechEntry]:
        """Start speaking text."""
        target = text or self._tts_text
        if not target:
            return None

        self._tts_text = target
        self._tts_status = SpeechStatus.SPEAKING
        self._tts_position = 0

        # Estimate duration (avg 150 words/min at 1x speed)
        words = len(target.split())
        duration = (words / 150) * 60 / self._tts_speed

        entry = SpeechEntry(
            text=target,
            is_tts=True,
            language=self._tts_voice.language,
            voice=self._tts_voice.name,
            speed=self._tts_speed,
            duration_seconds=duration,
        )
        self._history.insert(0, entry)
        if len(self._history) > self._max_history:
            self._history.pop()

        self._notify("speak", entry)
        return entry

    def stop_speaking(self) -> None:
        self._tts_status = SpeechStatus.IDLE
        self._tts_position = 0

    def pause_speaking(self) -> None:
        if self._tts_status == SpeechStatus.SPEAKING:
            self._tts_status = SpeechStatus.PAUSED

    def resume_speaking(self) -> None:
        if self._tts_status == SpeechStatus.PAUSED:
            self._tts_status = SpeechStatus.SPEAKING

    def speak_clipboard(self) -> Optional[SpeechEntry]:
        """Speak text from clipboard (simulated)."""
        return self.speak("[Clipboard content would be read here]")

    @property
    def tts_status(self) -> SpeechStatus:
        return self._tts_status

    @property
    def tts_progress(self) -> float:
        if not self._tts_text:
            return 0.0
        return min(1.0, self._tts_position / len(self._tts_text)) if self._tts_text else 0.0

    @property
    def tts_status_icon(self) -> str:
        icons = {
            SpeechStatus.IDLE: "⏹️",
            SpeechStatus.SPEAKING: "🔊",
            SpeechStatus.PAUSED: "⏸️",
        }
        return icons.get(self._tts_status, "❓")

    # ── Speech-to-Text ────────────────────────────────────────────────

    def start_listening(self) -> None:
        self._stt_status = SpeechStatus.RECORDING
        self._stt_buffer = ""

    def stop_listening(self) -> Optional[SpeechEntry]:
        if self._stt_status == SpeechStatus.RECORDING:
            self._stt_status = SpeechStatus.IDLE
            text = self._stt_buffer.strip()
            if text:
                entry = SpeechEntry(
                    text=text,
                    is_tts=False,
                    language=self._stt_language,
                )
                self._history.insert(0, entry)
                self._stt_text = text
                self._notify("transcribe", entry)
                return entry
        return None

    def add_transcription(self, text: str) -> None:
        """Simulate receiving transcribed text."""
        if self._stt_status == SpeechStatus.RECORDING:
            self._stt_buffer += text + " "

    @property
    def stt_status(self) -> SpeechStatus:
        return self._stt_status

    @property
    def stt_text(self) -> str:
        return self._stt_text

    @property
    def stt_buffer(self) -> str:
        return self._stt_buffer

    # ── Voice Management ──────────────────────────────────────────────

    @property
    def voices(self) -> List[Voice]:
        return list(self._voices)

    @property
    def current_voice(self) -> Voice:
        return self._voices[self._selected_voice_index]

    def select_voice(self, index: int) -> bool:
        if 0 <= index < len(self._voices):
            self._selected_voice_index = index
            self._tts_voice = self._voices[index]
            return True
        return False

    def set_speed(self, speed: float) -> float:
        self._tts_speed = max(0.5, min(3.0, speed))
        return self._tts_speed

    def set_pitch(self, pitch: float) -> float:
        self._tts_pitch = max(0.5, min(2.0, pitch))
        return self._tts_pitch

    def set_volume(self, volume: float) -> float:
        self._tts_volume = max(0.0, min(1.0, volume))
        return self._tts_volume

    def set_language(self, lang: SpeechLanguage) -> None:
        self._stt_language = lang

    # ── Pronunciation ─────────────────────────────────────────────────

    def add_pronunciation(self, word: str, phonetic: str) -> PronunciationEntry:
        entry = PronunciationEntry(word=word, phonetic=phonetic)
        self._pronunciations.append(entry)
        return entry

    def remove_pronunciation(self, index: int) -> bool:
        if 0 <= index < len(self._pronunciations):
            self._pronunciations.pop(index)
            return True
        return False

    @property
    def pronunciations(self) -> List[PronunciationEntry]:
        return list(self._pronunciations)

    # ── History ───────────────────────────────────────────────────────

    @property
    def history(self) -> List[SpeechEntry]:
        return list(self._history)

    def clear_history(self) -> int:
        count = len(self._history)
        self._history.clear()
        return count

    # ── SSML ──────────────────────────────────────────────────────────

    @property
    def ssml_templates(self) -> List[Tuple[str, str]]:
        return list(self._ssml_templates)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_tts(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🔊 Text-to-Speech")
        lines.append("─" * width)

        # Status
        lines.append(f" {self.tts_status_icon} {self._tts_status.value.title()}")

        # Voice info
        voice = self.current_voice
        lines.append(f" Voice: {voice.display}")
        lines.append(f" Speed: {self._tts_speed:.1f}x  Pitch: {self._tts_pitch:.1f}  Vol: {self._tts_volume:.0%}")
        lines.append(f" Language: {voice.language.value}")

        lines.append("─" * width)

        # Text input
        lines.append(" Text:")
        text = self._tts_text or "(enter text to speak)"
        for line in text.split("\n")[:5]:
            lines.append(f" │ {line[:width - 4]}")

        # Progress
        if self._tts_status == SpeechStatus.SPEAKING:
            pct = self.tts_progress
            bar_width = 40
            filled = int(pct * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            lines.append(f" [{bar}] {pct * 100:.0f}%")

        lines.append("─" * width)
        lines.append(" Space:Speak  P:Pause  S:Stop  V:Voices  H:History")
        return lines

    def render_stt(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🎤 Speech-to-Text")
        lines.append("─" * width)

        # Status
        status_icon = "🔴" if self._stt_status == SpeechStatus.RECORDING else "⏹️"
        lines.append(f" {status_icon} {self._stt_status.value.title()}")
        lines.append(f" Language: {self._stt_language.value}")

        lines.append("─" * width)

        # Transcription
        if self._stt_buffer:
            lines.append(" Live transcription:")
            lines.append(f" │ {self._stt_buffer[:width - 4]}")

        if self._stt_text:
            lines.append("")
            lines.append(" Last result:")
            lines.append(f" │ {self._stt_text[:width - 4]}")

        lines.append("─" * width)
        lines.append(" Space:Toggle listening  L:Language  Esc:Back")
        return lines

    def render_voices(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🗣️ Voice Selection")
        lines.append("─" * width)

        for i, voice in enumerate(self._voices):
            marker = "▸" if i == self._selected_index else " "
            current = " ←" if i == self._selected_voice_index else ""
            lines.append(f"{marker} {voice.display} [{voice.lang_code}]{current}")
            lines.append(f"   Speed: {voice.speed:.1f}  Pitch: {voice.pitch:.1f}  Vol: {voice.volume:.0%}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Choose  Esc:Back")
        return lines

    def render_history(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 📜 Speech History")
        lines.append(f" {len(self._history)} entries")
        lines.append("─" * width)

        for i, entry in enumerate(self._history[:15]):
            marker = "▸" if i == self._selected_index else " "
            icon = "🔊" if entry.is_tts else "🎤"
            lines.append(f"{marker} {icon} {entry.preview}")
            lines.append(f"   {entry.language.value} · {entry.voice} · {entry.time_ago}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Replay  Del:Clear  Esc:Back")
        return lines

    def render_pronunciations(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 📖 Pronunciation Dictionary")
        lines.append("─" * width)

        for i, entry in enumerate(self._pronunciations):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {entry.word} → /{entry.phonetic}/")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  N:New  Del:Delete  Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        renderers = {
            "tts": self.render_tts,
            "stt": self.render_stt,
            "voices": self.render_voices,
            "history": self.render_history,
            "pronunciations": self.render_pronunciations,
        }
        renderer = renderers.get(self._view_mode, self.render_tts)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "voices":
            return self._handle_voices_key(key)
        elif self._view_mode == "history":
            return self._handle_history_key(key)
        elif self._view_mode == "stt":
            return self._handle_stt_key(key)
        elif self._view_mode == "pronunciations":
            return self._handle_pron_key(key)
        return self._handle_tts_key(key)

    def _handle_tts_key(self, key: str) -> Optional[str]:
        if key == " ":
            if self._tts_status == SpeechStatus.IDLE:
                self.speak()
                return "speak"
            elif self._tts_status == SpeechStatus.SPEAKING:
                self.pause_speaking()
                return "pause"
            elif self._tts_status == SpeechStatus.PAUSED:
                self.resume_speaking()
                return "resume"
        elif key == "s":
            self.stop_speaking()
            return "stop"
        elif key == "v":
            self._view_mode = "voices"
            self._selected_index = 0
            return "voices"
        elif key == "h":
            self._view_mode = "history"
            self._selected_index = 0
            return "history"
        elif key == "m":
            self._view_mode = "stt"
            return "stt"
        elif key == "ArrowUp":
            self._tts_speed = min(3.0, self._tts_speed + 0.1)
            return "speed_up"
        elif key == "ArrowDown":
            self._tts_speed = max(0.5, self._tts_speed - 0.1)
            return "speed_down"
        return None

    def _handle_stt_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "tts"
            return "back"
        elif key == " ":
            if self._stt_status == SpeechStatus.RECORDING:
                self.stop_listening()
                return "stop_listening"
            else:
                self.start_listening()
                return "start_listening"
        elif key == "l":
            langs = list(SpeechLanguage)
            idx = langs.index(self._stt_language)
            self._stt_language = langs[(idx + 1) % len(langs)]
            return "cycle_language"
        return None

    def _handle_voices_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "tts"
            return "back"
        elif key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(self._voices) - 1, self._selected_index + 1)
            return "select_down"
        elif key == "Enter":
            self.select_voice(self._selected_index)
            self._view_mode = "tts"
            return "select_voice"
        return None

    def _handle_history_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "tts"
            return "back"
        elif key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(self._history) - 1, self._selected_index + 1)
            return "select_down"
        elif key == "Enter":
            if 0 <= self._selected_index < len(self._history):
                entry = self._history[self._selected_index]
                if entry.is_tts:
                    self.speak(entry.text)
            return "replay"
        elif key == "Delete":
            self.clear_history()
            return "clear_history"
        return None

    def _handle_pron_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "tts"
            return "back"
        return None

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_speech(self, cb: Callable) -> None:
        self._on_speech.append(cb)

    def _notify(self, event: str, *args) -> None:
        for cb in self._on_speech:
            try:
                cb(event, *args)
            except Exception:
                pass
