"""
Screen Recorder — record, pause, resume, and manage screen recordings.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


# ─── Enums ───────────────────────────────────────────────────────────────

class RecordingCodec(Enum):
    H264 = "H.264"
    H265 = "H.265"
    VP8 = "VP8"
    VP9 = "VP9"
    AV1 = "AV1"
    WEBM = "WebM"


class RecordingArea(Enum):
    FULLSCREEN = "fullscreen"
    WINDOW = "window"
    REGION = "region"
    MONITOR = "monitor"


class RecordingStatus(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"


class AudioSource(Enum):
    SYSTEM = "system"
    MICROPHONE = "microphone"
    BOTH = "both"
    NONE = "none"


# Backward-compat aliases
RecordFormat = RecordingCodec
RecordArea = RecordingArea
RecordStatus = RecordingStatus


class RecordingPreset(Enum):
    SCREENCAST = "screencast"
    GAMING = "gaming"
    PRESENTATION = "presentation"
    CUSTOM = "custom"


class OverlayType(Enum):
    NONE = "none"
    CAMERA = "camera"
    TEXT = "text"
    WATERMARK = "watermark"
    CURSOR = "cursor"


# ─── Data classes ────────────────────────────────────────────────────────

@dataclass
class RecordingSession:
    name: str = ""
    filename: str = ""
    duration_s: float = 0.0
    file_size: int = 0
    codec: RecordingCodec = RecordingCodec.H264
    area: RecordingArea = RecordingArea.FULLSCREEN
    fps: int = 30
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def status_icon(self) -> str:
        return "🎬"

    @property
    def duration_display(self) -> str:
        mins = int(self.duration_s) // 60
        secs = int(self.duration_s) % 60
        return f"{mins:02d}:{secs:02d}"

    @property
    def file_size_display(self) -> str:
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        return f"{self.file_size / (1024*1024):.1f} MB"


@dataclass
class Recording:
    """Legacy Recording class."""
    name: str = ""
    duration_s: float = 0.0
    status: RecordingStatus = RecordingStatus.IDLE
    codec: RecordingCodec = RecordingCodec.H264
    profile: Optional["RecordingProfile"] = None
    file_size_bytes: int = 0

    @property
    def status_icon(self) -> str:
        icons = {
            RecordingStatus.IDLE: "⏹",
            RecordingStatus.RECORDING: "🔴",
            RecordingStatus.PAUSED: "⏸",
            RecordingStatus.STOPPED: "⏹",
        }
        return icons.get(self.status, "?")

    @property
    def duration_display(self) -> str:
        if self.duration_s < 60:
            return f"{self.duration_s:.1f}s"
        mins = int(self.duration_s) // 60
        secs = int(self.duration_s) % 60
        return f"{mins}m {secs}s"

    @property
    def file_size_display(self) -> str:
        size = self.file_size_bytes
        if size == 0:
            return "0 B"
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size / (1024*1024):.1f} MB"

    @property
    def quality_label(self) -> str:
        return self.codec.value


@dataclass
class RecordingSchedule:
    days: List[str] = field(default_factory=list)
    start_time: str = "09:00"
    end_time: str = "17:00"

    @property
    def days_display(self) -> str:
        return ", ".join(self.days) if self.days else "None"


@dataclass
class RecordingProfile:
    name: str = ""
    codec: RecordingCodec = RecordingCodec.H264
    fps: int = 30
    width: int = 1920
    height: int = 1080
    resolution: str = "1920x1080"

    @property
    def codec_display(self) -> str:
        return self.codec.value

    @property
    def description(self) -> str:
        res = self.resolution or f"{self.width}x{self.height}"
        return f"{res} @ {self.fps}fps ({self.codec.value})"


# ─── Screen Recorder ─────────────────────────────────────────────────────

class RecordingSchedule:
    days: List[str] = field(default_factory=list)
    start_time: str = "09:00"
    end_time: str = "17:00"

    @property
    def days_display(self) -> str:
        return ", ".join(self.days) if self.days else "None"


@dataclass
class RecordingProfile:
    name: str = ""
    codec: RecordingCodec = RecordingCodec.H264
    fps: int = 30
    width: int = 1920
    height: int = 1080
    resolution: str = "1920x1080"

    @property
    def codec_display(self) -> str:
        return self.codec.value

    @property
    def description(self) -> str:
        res = self.resolution or f"{self.width}x{self.height}"
        return f"{res} @ {self.fps}fps ({self.codec.value})"


# ─── Screen Recorder ─────────────────────────────────────────────────────

class ScreenRecorder:
    """Screen recording manager with session tracking and rendering."""

    def __init__(self):
        self._status: RecordingStatus = RecordingStatus.IDLE
        self._sessions: List[RecordingSession] = []
        self._selected_index: int = 0
        self._current_area: RecordingArea = RecordingArea.FULLSCREEN
        self._current_format: RecordingCodec = RecordingCodec.H264
        self._current_fps: int = 30
        self._current_overlays: List[OverlayType] = []
        self._rec_start: float = 0.0
        self._presets: List[RecordingPreset] = [
            RecordingPreset.SCREENCAST,
            RecordingPreset.GAMING,
            RecordingPreset.PRESENTATION,
            RecordingPreset.CUSTOM,
        ]
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self._sessions = [
            RecordingSession(name="Desktop capture", filename="desktop_2026.mkv",
                             duration_s=3425.0, file_size=245_000_000, fps=30,
                             timestamp=now - 3600),
            RecordingSession(name="Tutorial part 1", filename="tutorial_p1.webm",
                             duration_s=1245.0, file_size=890_000_000, fps=60,
                             timestamp=now - 86400),
            RecordingSession(name="Bug report demo", filename="bug_demo.mkv",
                             duration_s=89.2, file_size=62_000_000, fps=30,
                             timestamp=now - 172800),
        ]

    @property
    def selected_session(self) -> Optional[RecordingSession]:
        if 0 <= self._selected_index < len(self._sessions):
            return self._sessions[self._selected_index]
        return None

    @property
    def profiles(self) -> List[RecordingProfile]:
        return [
            RecordingProfile("Standard", "1920x1080", 30),
            RecordingProfile("High Quality", "1920x1080", 60),
            RecordingProfile("Lossless", "3840x2160", 60),
            RecordingProfile("Compact", "1280x720", 30),
        ]

    @property
    def recordings(self) -> List[RecordingSession]:
        return self._sessions

    @property
    def active_profile(self) -> RecordingProfile:
        name = getattr(self, "_active_profile_name", "Standard")
        return RecordingProfile(
            name=name,
            resolution=f"{1920}x{1080}",
            fps=self._current_fps,
        )

    @property
    def current_recording(self) -> Optional[Recording]:
        if self._status == RecordingStatus.IDLE:
            return None
        return Recording(
            name="Active Recording",
            duration_s=time.time() - self._rec_start if self._rec_start else 0,
            status=self._status,
            profile=self.active_profile,
        )

    @property
    def total_recordings(self) -> int:
        return len(self._sessions)

    @property
    def total_duration_secs(self) -> float:
        return sum(s.duration_s for s in self._sessions)

    @property
    def total_duration_display(self) -> str:
        total = self.total_duration_secs
        hours = int(total) // 3600
        mins = (int(total) % 3600) // 60
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"

    @property
    def total_size(self) -> int:
        return sum(s.file_size for s in self._sessions)

    @property
    def is_recording(self) -> bool:
        return self._status == RecordingStatus.RECORDING

    def select(self, index: int):
        if 0 <= index < len(self._sessions):
            self._selected_index = index

    def start_recording(self) -> Optional[Recording]:
        self._status = RecordingStatus.RECORDING
        self._rec_start = time.time()
        return Recording(name="Recording", status=RecordingStatus.RECORDING, profile=self.active_profile)

    def stop_recording(self, name: str = "") -> Optional[Recording]:
        if self._status in (RecordingStatus.RECORDING, RecordingStatus.PAUSED):
            duration = time.time() - self._rec_start if self._rec_start else 0
            rec = Recording(
                name=name or f"Recording {len(self._sessions) + 1}",
                duration_s=max(duration, 1.0),
                status=RecordingStatus.STOPPED,
                codec=self._current_format,
                profile=self.active_profile,
                file_size_bytes=int(duration * 700_000),
            )
            session = RecordingSession(
                name=rec.name,
                filename=f"rec_{len(self._sessions) + 1}.mkv",
                duration_s=rec.duration_s,
                file_size=rec.file_size_bytes,
                codec=self._current_format,
                area=self._current_area,
                fps=self._current_fps,
            )
            self._sessions.insert(0, session)
            self._status = RecordingStatus.IDLE
            self._rec_start = 0.0
            return rec
        return None

    def pause_recording(self):
        if self._status == RecordingStatus.RECORDING:
            self._status = RecordingStatus.PAUSED

    def resume_recording(self):
        if self._status == RecordingStatus.PAUSED:
            self._status = RecordingStatus.RECORDING

    def delete_session(self, index: int) -> bool:
        if 0 <= index < len(self._sessions):
            del self._sessions[index]
            return True
        return False

    def set_profile(self, name: str) -> bool:
        for p in self.profiles:
            if p.name == name:
                self._current_fps = p.fps
                self._active_profile_name = name
                return True
        return True

    def set_area(self, area: RecordingArea):
        self._current_area = area

    def set_format(self, codec: RecordingCodec):
        self._current_format = codec

    def set_fps(self, fps: int):
        self._current_fps = fps

    def toggle_overlay(self, overlay: OverlayType):
        if overlay in self._current_overlays:
            self._current_overlays.remove(overlay)
        else:
            self._current_overlays.append(overlay)

    def render(self) -> List[str]:
        lines = [
            f"── SCREEN RECORDER ──",
            f"Status: {self._status.value}",
            f"Format: {self._current_format.value} | FPS: {self._current_fps}",
            f"Area: {self._current_area.value}",
            f"Overlays: {', '.join(o.value for o in self._current_overlays) or 'None'}",
            f"Total: {self.total_recordings} recordings ({self.total_duration_display})",
            f"Size: {self.total_size / (1024*1024):.1f} MB",
            "",
        ]
        for i, s in enumerate(self._sessions):
            marker = "▸ " if i == self._selected_index else "  "
            lines.append(f"{marker}{s.name} [{s.duration_display}] {s.file_size_display}")
        return lines

    def render_session_detail(self) -> List[str]:
        s = self.selected_session
        if not s:
            return ["No session selected."]
        return [
            f"── {s.name} ──",
            f"File: {s.filename}",
            f"Duration: {s.duration_display}",
            f"Size: {s.file_size_display}",
            f"Codec: {s.codec.value} | FPS: {s.fps}",
        ]

    # ─── Legacy API ──────────────────────────────────────────────────


    def get_total_recordings(self) -> int:
        return self.total_recordings

    def get_total_duration(self) -> float:
        return self.total_duration_secs

    def get_total_size(self) -> int:
        return self.total_size

    def get_recent_recordings(self, limit: int = 5) -> List[RecordingSession]:
        return self._sessions[:limit]

    def get_stats(self) -> Dict:
        return {
            "profiles": len(self.profiles),
            "recordings": self.total_recordings,
            "total": self.total_recordings,
            "duration": self.total_duration_secs,
            "size": self.total_size,
        }
RecordPreset = RecordingPreset

# ─── Backward-compat exports ────────────────────────────────────────────
from dataclasses import dataclass as _dataclass

@_dataclass
class Hotkey:
    key: str = ""
    action: str = ""
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False
