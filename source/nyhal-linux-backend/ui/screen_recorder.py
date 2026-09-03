"""Screen Recording Control Panel — Capture settings, timer, and export options.

Features:
- Recording modes: full screen, window, region, webcam
- Format/codec selection (MP4, WebM, AVI, MKV)
- Quality presets (low, medium, high, ultra)
- FPS options (24, 30, 60, 120)
- Audio input selection (system, microphone, both, none)
- Hotkey configuration
- Recording timer with pause/resume
- Export settings with bitrate control
- Recording history
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class CaptureMode(Enum):
    FULL_SCREEN = "full_screen"
    WINDOW = "window"
    REGION = "region"
    WEBCAM = "webcam"
    AREA_FOLLOW = "area_follow"

    @property
    def icon(self) -> str:
        icons = {
            CaptureMode.FULL_SCREEN: "🖥", CaptureMode.WINDOW: "🪟",
            CaptureMode.REGION: "📐", CaptureMode.WEBCAM: "📷",
            CaptureMode.AREA_FOLLOW: "🔍",
        }
        return icons.get(self, "?")


class VideoFormat(Enum):
    MP4 = "mp4"
    WEBM = "webm"
    AVI = "avi"
    MKV = "mkv"
    MOV = "mov"
    GIF = "gif"

    @property
    def icon(self) -> str:
        icons = {
            VideoFormat.MP4: "🎬", VideoFormat.WEBM: "🌐",
            VideoFormat.AVI: "📼", VideoFormat.MKV: "📦",
            VideoFormat.MOV: "🎞", VideoFormat.GIF: "🖼",
        }
        return icons.get(self, "?")


class AudioSource(Enum):
    NONE = "none"
    SYSTEM = "system"
    MICROPHONE = "microphone"
    BOTH = "both"

    @property
    def icon(self) -> str:
        icons = {
            AudioSource.NONE: "🔇", AudioSource.SYSTEM: "🔊",
            AudioSource.MICROPHONE: "🎤", AudioSource.BOTH: "🎙",
        }
        return icons.get(self, "?")


class QualityPreset(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ULTRA = "ultra"

    @property
    def icon(self) -> str:
        icons = {
            QualityPreset.LOW: "📹", QualityPreset.MEDIUM: "🎬",
            QualityPreset.HIGH: "🎥", QualityPreset.ULTRA: "✨",
        }
        return icons.get(self, "?")


class RecordingState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    ENCODING = "encoding"

    @property
    def icon(self) -> str:
        icons = {
            RecordingState.IDLE: "⏹", RecordingState.RECORDING: "🔴",
            RecordingState.PAUSED: "⏸", RecordingState.ENCODING: "⚙️",
        }
        return icons.get(self, "?")


@dataclass
class RecordingPreset:
    name: str = ""
    mode: CaptureMode = CaptureMode.FULL_SCREEN
    format: VideoFormat = VideoFormat.MP4
    quality: QualityPreset = QualityPreset.HIGH
    fps: int = 60
    audio: AudioSource = AudioSource.SYSTEM
    bitrate: int = 8000  # kbps
    resolution: str = "1920x1080"
    encoder: str = "h264"

    @property
    def estimated_size_mb_per_min(self) -> float:
        return self.bitrate * 60 / 8 / 1024

    @property
    def encoder_icon(self) -> str:
        icons = {"h264": "🎬", "h265": "🎬", "vp9": "🌐", "av1": "✨", "prores": "🎞"}
        return icons.get(self.encoder, "❓")


@dataclass
class RecordingSession:
    id: int = 0
    filename: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_s: float = 0.0
    file_size_mb: float = 0.0
    format: VideoFormat = VideoFormat.MP4
    resolution: str = "1920x1080"
    fps: int = 60
    mode: CaptureMode = CaptureMode.FULL_SCREEN
    quality: QualityPreset = QualityPreset.HIGH
    bitrate: int = 8000
    audio: AudioSource = AudioSource.SYSTEM
    is_favorite: bool = False
    tags: List[str] = field(default_factory=list)

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.start_time))

    @property
    def duration_str(self) -> str:
        mins = self.duration_s / 60
        if mins < 60:
            return f"{mins:.0f}m"
        h = int(mins // 60)
        m = int(mins % 60)
        return f"{h}h {m}m"

    @property
    def size_str(self) -> str:
        if self.file_size_mb < 1024:
            return f"{self.file_size_mb:.0f} MB"
        return f"{self.file_size_mb / 1024:.1f} GB"

    @property
    def format_icon(self) -> str:
        return self.format.icon


@dataclass
class Hotkey:
    action: str = ""
    key: str = ""
    enabled: bool = True


@dataclass
class AudioDevice:
    name: str = ""
    device_type: str = ""  # system, microphone, virtual
    volume: float = 80.0
    active: bool = False

    @property
    def icon(self) -> str:
        icons = {"system": "🔊", "microphone": "🎤", "virtual": "🌐"}
        return icons.get(self.device_type, "❓")

    @property
    def volume_bar(self) -> str:
        filled = int(self.volume / 5)
        return "█" * filled + "░" * (20 - filled)


class ScreenRecorder:
    def __init__(self):
        self._presets: List[RecordingPreset] = []
        self._sessions: List[RecordingSession] = []
        self._hotkeys: List[Hotkey] = []
        self._audio_devices: List[AudioDevice] = []
        self._current_preset: int = 0
        self._state: RecordingState = RecordingState.IDLE
        self._recording_start: float = 0.0
        self._pause_start: float = 0.0
        self._total_paused_s: float = 0.0
        self._current_recording_s: float = 0.0
        self._selected_session: int = 0
        self._view_mode: str = "control"  # control, presets, history, settings, audio
        self._frame_count: int = 0
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Presets
        self._presets = [
            RecordingPreset("Gaming 1080p60", CaptureMode.FULL_SCREEN, VideoFormat.MP4,
                            QualityPreset.HIGH, 60, AudioSource.BOTH, 12000, "1920x1080", "h264"),
            RecordingPreset("Tutorial 1440p30", CaptureMode.WINDOW, VideoFormat.MP4,
                            QualityPreset.HIGH, 30, AudioSource.MICROPHONE, 8000, "2560x1440", "h264"),
            RecordingPreset("Quick GIF", CaptureMode.REGION, VideoFormat.GIF,
                            QualityPreset.LOW, 15, AudioSource.NONE, 2000, "800x600", "h264"),
            RecordingPreset("Ultra 4K", CaptureMode.FULL_SCREEN, VideoFormat.MP4,
                            QualityPreset.ULTRA, 60, AudioSource.BOTH, 50000, "3840x2160", "h265"),
            RecordingPreset("Webcam Stream", CaptureMode.WEBCAM, VideoFormat.MP4,
                            QualityPreset.MEDIUM, 30, AudioSource.MICROPHONE, 4000, "1280x720", "h264"),
        ]

        # Sessions
        self._sessions = [
            RecordingSession(1, "nyrqis_demo_v2.1.mp4", now - 86400 * 2, now - 86400 * 2 + 1200,
                             1200, 245, VideoFormat.MP4, "1920x1080", 60, CaptureMode.FULL_SCREEN,
                             QualityPreset.HIGH, 12000, AudioSource.BOTH, True, ["demo", "v2.1"]),
            RecordingSession(2, "compositor_bench.webm", now - 86400, now - 86400 + 480,
                             480, 89, VideoFormat.WEBM, "2560x1440", 30, CaptureMode.WINDOW,
                             QualityPreset.HIGH, 8000, AudioSource.NONE, False, ["benchmark"]),
            RecordingSession(3, "bug_report_crash.mp4", now - 3600 * 5, now - 3600 * 5 + 95,
                             95, 18, VideoFormat.MP4, "1920x1080", 30, CaptureMode.FULL_SCREEN,
                             QualityPreset.MEDIUM, 6000, AudioSource.SYSTEM, False, ["bug"]),
            RecordingSession(4, "tutorial_shell_setup.mp4", now - 86400 * 3, now - 86400 * 3 + 2400,
                             2400, 520, VideoFormat.MP4, "1920x1080", 30, CaptureMode.WINDOW,
                             QualityPreset.HIGH, 8000, AudioSource.MICROPHONE, True, ["tutorial"]),
            RecordingSession(5, "gpu_test_region.mp4", now - 7200, now - 7200 + 340,
                             340, 65, VideoFormat.MP4, "1920x1080", 60, CaptureMode.REGION,
                             QualityPreset.HIGH, 12000, AudioSource.NONE, False, ["gpu", "test"]),
        ]

        # Hotkeys
        self._hotkeys = [
            Hotkey("Start/Stop Recording", "Ctrl+Shift+R"),
            Hotkey("Pause/Resume", "Ctrl+Shift+P"),
            Hotkey("Screenshot", "Ctrl+Shift+S"),
            Hotkey("Toggle Audio", "Ctrl+Shift+A"),
            Hotkey("Region Select", "Ctrl+Shift+G"),
            Hotkey("Webcam Toggle", "Ctrl+Shift+W"),
        ]

        # Audio devices
        self._audio_devices = [
            AudioDevice("Speakers (Built-in)", "system", 80, True),
            AudioDevice("HDMI Audio", "system", 100, False),
            AudioDevice("USB Microphone", "microphone", 75, True),
            AudioDevice("Virtual Cable", "virtual", 50, False),
            AudioDevice("Bluetooth Headset", "system", 60, False),
        ]

    @property
    def recording_time_str(self) -> str:
        if self._state == RecordingState.IDLE:
            return "00:00:00"
        elapsed = self._current_recording_s
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)
        s = int(elapsed % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @property
    def current_preset(self) -> Optional[RecordingPreset]:
        if 0 <= self._current_preset < len(self._presets):
            return self._presets[self._current_preset]
        return None

    @property
    def estimated_size_mb(self) -> float:
        p = self.current_preset
        if not p:
            return 0.0
        return p.estimated_size_mb_per_min * (self._current_recording_s / 60)

    @property
    def total_recorded_s(self) -> float:
        return sum(s.duration_s for s in self._sessions)

    @property
    def total_size_mb(self) -> float:
        return sum(s.file_size_mb for s in self._sessions)

    def start_recording(self):
        if self._state == RecordingState.IDLE:
            self._state = RecordingState.RECORDING
            self._recording_start = time.time()
            self._current_recording_s = 0.0
            self._total_paused_s = 0.0

    def stop_recording(self):
        if self._state in (RecordingState.RECORDING, RecordingState.PAUSED):
            duration = self._current_recording_s
            self._sessions.insert(0, RecordingSession(
                id=len(self._sessions) + 1,
                filename=f"recording_{len(self._sessions) + 1}.mp4",
                start_time=self._recording_start,
                end_time=time.time(),
                duration_s=duration,
                file_size_mb=duration / 60 * (self.current_preset.estimated_size_mb_per_min if self.current_preset else 50),
                format=self.current_preset.format if self.current_preset else VideoFormat.MP4,
                resolution=self.current_preset.resolution if self.current_preset else "1920x1080",
                fps=self.current_preset.fps if self.current_preset else 30,
            ))
            self._state = RecordingState.IDLE
            self._current_recording_s = 0.0

    def pause_recording(self):
        if self._state == RecordingState.RECORDING:
            self._state = RecordingState.PAUSED
            self._pause_start = time.time()
        elif self._state == RecordingState.PAUSED:
            self._state = RecordingState.RECORDING
            self._total_paused_s += time.time() - self._pause_start

    def update_timer(self):
        if self._state == RecordingState.RECORDING:
            self._current_recording_s = time.time() - self._recording_start - self._total_paused_s
            self._frame_count += 1

    def select_preset(self, idx: int):
        if 0 <= idx < len(self._presets):
            self._current_preset = idx

    def select_session(self, idx: int):
        if 0 <= idx < len(self._sessions):
            self._selected_session = idx

    def set_view(self, mode: str):
        if mode in ("control", "presets", "history", "settings", "audio"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS SCREEN RECORDER                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        state_icon = self._state.icon
        state_label = self._state.value.upper()
        preset = self.current_preset
        preset_name = preset.name if preset else "None"
        lines.append(f"  {state_icon} {state_label}  ⏱ {self.recording_time_str}  📦 {self.estimated_size_mb:.0f}MB  🎬 {preset_name}  📊 {len(self._sessions)} recordings")
        lines.append("")

        if self._view_mode == "control":
            # Timer display
            lines.append("  ── Recording Timer ──")
            lines.append(f"  ⏱ {self.recording_time_str}")
            if self._state != RecordingState.IDLE:
                lines.append(f"  📦 Est. size: {self.estimated_size_mb:.1f} MB")
                lines.append(f"  🎞 Frames: {self._frame_count}")
            lines.append("")

            # Current preset details
            if preset:
                lines.append("  ── Current Preset ──")
                lines.append(f"  🎬 {preset.name}")
                lines.append(f"  Mode: {preset.mode.icon} {preset.mode.value}  Format: {preset.format.icon} {preset.format.value}  Quality: {preset.quality.icon} {preset.quality.value}")
                lines.append(f"  FPS: {preset.fps}  Audio: {preset.audio.icon} {preset.audio.value}  Bitrate: {preset.bitrate}kbps")
                lines.append(f"  Resolution: {preset.resolution}  Encoder: {preset.encoder_icon} {preset.encoder}")
                lines.append(f"  Size/min: {preset.estimated_size_mb_per_min:.0f} MB")
            lines.append("")

            # Quick actions
            if self._state == RecordingState.IDLE:
                lines.append("  [R] Start Recording  [P] Presets  [H] History")
            elif self._state == RecordingState.RECORDING:
                lines.append("  [S] Stop  [P] Pause  [📷] Screenshot")
            elif self._state == RecordingState.PAUSED:
                lines.append("  [S] Stop  [R] Resume  [📷] Screenshot")

        elif self._view_mode == "presets":
            lines.append("  ── Recording Presets ──")
            for i, p in enumerate(self._presets):
                sel = "▶" if i == self._current_preset else " "
                lines.append(f"  {sel} 🎬 {p.name}")
                lines.append(f"      {p.mode.icon} {p.mode.value}  {p.format.icon} {p.format.value}  {p.quality.icon} {p.quality.value}  {p.fps}fps")
                lines.append(f"      {p.resolution}  {p.audio.icon} {p.audio.value}  {p.bitrate}kbps  {p.encoder_icon} {p.encoder}")

        elif self._view_mode == "history":
            lines.append("  ── Recording History ──")
            for i, s in enumerate(self._sessions):
                sel = "▶" if i == self._selected_session else " "
                fav = "⭐" if s.is_favorite else "  "
                lines.append(f"  {sel}{fav} {s.format_icon} {s.filename}")
                lines.append(f"      {s.time_str}  {s.duration_str}  {s.size_str}  {s.resolution} {s.fps}fps")

        elif self._view_mode == "audio":
            lines.append("  ── Audio Devices ──")
            for dev in self._audio_devices:
                active = "🟢" if dev.active else "⚪"
                lines.append(f"  {active} {dev.icon} {dev.name}  [{dev.volume_bar}] {dev.volume:.0f}%")

        elif self._view_mode == "settings":
            lines.append("  ── Hotkeys ──")
            for hk in self._hotkeys:
                enabled = "🟢" if hk.enabled else "🔴"
                lines.append(f"  {enabled} {hk.action}: {hk.key}")

        lines.append("")
        lines.append("  [C]ontrol [P]resets [H]istory [A]udio [S]ettings [R]ecord [↑↓]Nav")
        return lines
