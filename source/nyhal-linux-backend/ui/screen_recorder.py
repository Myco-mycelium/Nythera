"""
Nyrqis OS - Screen Recorder
Codec selection, frame rate control, and quality settings.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class RecordingCodec(Enum):
    H264 = "h264"
    H265 = "h265"
    VP8 = "vp8"
    VP9 = "vp9"
    AV1 = "av1"
    PRORES = "prores"
    LOSSLESS = "lossless"


class RecordingArea(Enum):
    FULL_SCREEN = "full_screen"
    WINDOW = "window"
    REGION = "region"
    MULTI_MONITOR = "multi_monitor"


class RecordingStatus(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"


class AudioSource(Enum):
    NONE = "none"
    SYSTEM = "system"
    MICROPHONE = "microphone"
    BOTH = "both"


@dataclass
class RecordingProfile:
    name: str
    codec: RecordingCodec = RecordingCodec.H264
    quality: int = 80  # 1-100
    fps: int = 30
    resolution: str = "1920x1080"
    bitrate_mbps: float = 10.0
    preset: str = "medium"
    pixel_format: str = "yuv420p"
    audio_source: AudioSource = AudioSource.SYSTEM
    audio_bitrate: int = 192
    audio_sample_rate: int = 48000

    @property
    def codec_display(self) -> str:
        return self.codec.value.upper()

    @property
    def description(self) -> str:
        return f"{self.resolution} @ {self.fps}fps, {self.codec_display}, {self.quality}%"


@dataclass
class Recording:
    id: int = 0
    profile: RecordingProfile = field(default_factory=RecordingProfile)
    area: RecordingArea = RecordingArea.FULL_SCREEN
    status: RecordingStatus = RecordingStatus.IDLE
    start_time: float = 0.0
    end_time: float = 0.0
    duration_s: float = 0.0
    file_path: str = ""
    file_size_bytes: int = 0
    frames_captured: int = 0
    frames_dropped: int = 0
    actual_fps: float = 0.0
    monitor: int = 1

    @property
    def status_icon(self) -> str:
        icons = {
            RecordingStatus.IDLE: "⏸", RecordingStatus.RECORDING: "🔴",
            RecordingStatus.PAUSED: "🟡", RecordingStatus.STOPPED: "⏹",
        }
        return icons.get(self.status, "?")

    @property
    def duration_display(self) -> str:
        if self.duration_s < 60:
            return f"{self.duration_s:.1f}s"
        elif self.duration_s < 3600:
            return f"{self.duration_s / 60:.1f}m"
        return f"{self.duration_s / 3600:.1f}h"

    @property
    def file_size_display(self) -> str:
        s = self.file_size_bytes
        if s < 1024:
            return f"{s} B"
        elif s < 1024 * 1024:
            return f"{s / 1024:.1f} KB"
        elif s < 1024 * 1024 * 1024:
            return f"{s / (1024 * 1024):.1f} MB"
        return f"{s / (1024 * 1024 * 1024):.2f} GB"

    @property
    def quality_label(self) -> str:
        q = self.profile.quality
        if q >= 90:
            return "Excellent"
        elif q >= 70:
            return "Good"
        elif q >= 50:
            return "Medium"
        return "Low"


@dataclass
class RecordingSchedule:
    name: str = ""
    enabled: bool = True
    start_time: str = "09:00"
    end_time: str = "17:00"
    days: List[str] = field(default_factory=list)
    profile: RecordingProfile = field(default_factory=RecordingProfile)
    auto_stop: bool = True
    max_duration_s: int = 3600

    @property
    def days_display(self) -> str:
        if not self.days:
            return "Every day"
        return ", ".join(self.days)


class ScreenRecorder:
    def __init__(self):
        self.profiles: List[RecordingProfile] = []
        self.recordings: List[Recording] = []
        self.schedules: List[RecordingSchedule] = []
        self.current_recording: Optional[Recording] = None
        self.active_profile: Optional[RecordingProfile] = None
        self.recording_counter: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        self.profiles = [
            RecordingProfile(name="High Quality", codec=RecordingCodec.H264,
                              quality=95, fps=60, resolution="2560x1440",
                              bitrate_mbps=25.0, preset="slow",
                              audio_source=AudioSource.BOTH),
            RecordingProfile(name="Standard", codec=RecordingCodec.H264,
                              quality=80, fps=30, resolution="1920x1080",
                              bitrate_mbps=10.0, preset="medium",
                              audio_source=AudioSource.SYSTEM),
            RecordingProfile(name="Streaming", codec=RecordingCodec.H264,
                              quality=70, fps=30, resolution="1920x1080",
                              bitrate_mbps=6.0, preset="veryfast",
                              audio_source=AudioSource.BOTH),
            RecordingProfile(name="Compact", codec=RecordingCodec.H265,
                              quality=75, fps=30, resolution="1920x1080",
                              bitrate_mbps=5.0, preset="medium",
                              audio_source=AudioSource.SYSTEM),
            RecordingProfile(name="Lossless", codec=RecordingCodec.LOSSLESS,
                              quality=100, fps=60, resolution="1920x1080",
                              bitrate_mbps=500.0, preset="ultrafast",
                              audio_source=AudioSource.NONE),
        ]
        self.active_profile = self.profiles[1]

        now = time.time()
        self.recordings = [
            Recording(id=1, profile=self.profiles[0], area=RecordingArea.FULL_SCREEN,
                       status=RecordingStatus.STOPPED, start_time=now - 7200,
                       end_time=now - 6800, duration_s=400,
                       file_path="/home/zeus/Videos/recording-001.mp4",
                       file_size_bytes=125000000, frames_captured=24000,
                       frames_dropped=12, actual_fps=60),
            Recording(id=2, profile=self.profiles[1], area=RecordingArea.WINDOW,
                       status=RecordingStatus.STOPPED, start_time=now - 3600,
                       end_time=now - 3300, duration_s=300,
                       file_path="/home/zeus/Videos/recording-002.mp4",
                       file_size_bytes=45000000, frames_captured=9000,
                       frames_dropped=3, actual_fps=30),
            Recording(id=3, profile=self.profiles[3], area=RecordingArea.REGION,
                       status=RecordingStatus.STOPPED, start_time=now - 1800,
                       end_time=now - 1500, duration_s=300,
                       file_path="/home/zeus/Videos/recording-003.mkv",
                       file_size_bytes=18000000, frames_captured=9000,
                       frames_dropped=0, actual_fps=30),
        ]
        self.recording_counter = 3

        self.schedules = [
            RecordingSchedule(name="Work Hours", enabled=True,
                               start_time="09:00", end_time="17:00",
                               days=["Mon", "Tue", "Wed", "Thu", "Fri"],
                               profile=self.profiles[1], max_duration_s=28800),
            RecordingSchedule(name="Gaming Session", enabled=False,
                               start_time="20:00", end_time="23:00",
                               days=["Fri", "Sat"],
                               profile=self.profiles[0], max_duration_s=10800),
        ]

    def start_recording(self) -> Optional[Recording]:
        self.recording_counter += 1
        rec = Recording(
            id=self.recording_counter,
            profile=self.active_profile or self.profiles[1],
            status=RecordingStatus.RECORDING,
            start_time=time.time())
        self.current_recording = rec
        self.recordings.append(rec)
        return rec

    def stop_recording(self) -> Optional[Recording]:
        if self.current_recording and self.current_recording.status == RecordingStatus.RECORDING:
            self.current_recording.status = RecordingStatus.STOPPED
            self.current_recording.end_time = time.time()
            self.current_recording.duration_s = (
                self.current_recording.end_time - self.current_recording.start_time)
            self.current_recording.file_size_bytes = int(
                self.current_recording.duration_s * self.current_recording.profile.bitrate_mbps * 125000)
            self.current_recording.frames_captured = int(
                self.current_recording.duration_s * self.current_recording.profile.fps)
            rec = self.current_recording
            self.current_recording = None
            return rec
        return None

    def pause_recording(self) -> bool:
        if self.current_recording and self.current_recording.status == RecordingStatus.RECORDING:
            self.current_recording.status = RecordingStatus.PAUSED
            return True
        return False

    def resume_recording(self) -> bool:
        if self.current_recording and self.current_recording.status == RecordingStatus.PAUSED:
            self.current_recording.status = RecordingStatus.RECORDING
            return True
        return False

    def set_profile(self, name: str) -> bool:
        profile = next((p for p in self.profiles if p.name == name), None)
        if profile:
            self.active_profile = profile
            return True
        return False

    def get_total_recordings(self) -> int:
        return len(self.recordings)

    def get_total_duration(self) -> float:
        return sum(r.duration_s for r in self.recordings)

    def get_total_size(self) -> int:
        return sum(r.file_size_bytes for r in self.recordings)

    def get_recent_recordings(self, limit: int = 5) -> List[Recording]:
        return sorted(self.recordings, key=lambda r: r.start_time, reverse=True)[:limit]

    def get_stats(self) -> Dict:
        return {
            "profiles": len(self.profiles),
            "recordings": len(self.recordings),
            "total_duration_s": round(self.get_total_duration(), 1),
            "total_size_bytes": self.get_total_size(),
            "schedules": len(self.schedules),
            "active_profile": self.active_profile.name if self.active_profile else "None",
            "is_recording": self.current_recording is not None,
        }


class RecordingPreset(Enum):
    HIGH_QUALITY = "high_quality"
    STANDARD = "standard"
    LOW_LATENCY = "low_latency"
    STREAMING = "streaming"
