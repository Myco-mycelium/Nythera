from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class RecordFormat(Enum):
    WEBM = "webm"
    MKV = "mkv"
    MP4 = "mp4"
    AVI = "avi"
    GIF = "gif"


class RecordArea(Enum):
    FULL_SCREEN = "full-screen"
    WINDOW = "window"
    CUSTOM = "custom"
    MONITOR_1 = "monitor-1"
    MONITOR_2 = "monitor-2"


class OverlayType(Enum):
    NONE = "none"
    CURSOR = "cursor"
    CURSOR_CLICK = "cursor-click"
    WEBCAM = "webcam"
    WATERMARK = "watermark"


class RecordStatus(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    PROCESSING = "processing"


class AudioSource(Enum):
    NONE = "none"
    SYSTEM = "system"
    MICROPHONE = "microphone"
    BOTH = "both"


@dataclass
class RecordPreset:
    name: str
    area: RecordArea
    format: RecordFormat
    fps: int
    quality: str
    audio: AudioSource
    overlays: list = field(default_factory=list)


@dataclass
class RecordingSession:
    name: str
    area: RecordArea
    format: RecordFormat
    fps: int
    quality: str
    audio_source: AudioSource
    overlays: list
    timestamp: float
    duration_secs: float
    file_size_mb: float
    resolution: str
    bitrate_kbps: int
    is_completed: bool = True

    @property
    def display_duration(self) -> str:
        m, s = divmod(int(self.duration_secs), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @property
    def display_size(self) -> str:
        if self.file_size_mb >= 1024:
            return f"{self.file_size_mb / 1024:.1f} GB"
        return f"{self.file_size_mb:.1f} MB"


class ScreenRecorder:
    def __init__(self):
        self._sessions: list[RecordingSession] = []
        self._selected: int = 0
        self._status: RecordStatus = RecordStatus.IDLE
        self._current_area: RecordArea = RecordArea.FULL_SCREEN
        self._current_format: RecordFormat = RecordFormat.MP4
        self._current_fps: int = 60
        self._current_quality: str = "High"
        self._current_audio: AudioSource = AudioSource.SYSTEM
        self._current_overlays: list[OverlayType] = [OverlayType.CURSOR]
        self._current_duration: float = 0
        self._rec_start: float = 0
        self._cursor_highlight: bool = True
        self._hotkey_start: str = "Ctrl+Shift+R"
        self._hotkey_stop: str = "Ctrl+Shift+S"
        self._hotkey_pause: str = "Ctrl+Shift+P"
        self._output_dir: str = "/home/user/Videos/Recordings"
        self._auto_stop_mins: int = 0
        self._view: str = "sessions"
        self._presets: list[RecordPreset] = [
            RecordPreset("4K Gaming", RecordArea.FULL_SCREEN, RecordFormat.MP4, 60, "Ultra", AudioSource.BOTH, [OverlayType.CURSOR]),
            RecordPreset("Tutorial", RecordArea.WINDOW, RecordFormat.WEBM, 30, "High", AudioSource.BOTH, [OverlayType.CURSOR, OverlayType.WATERMARK]),
            RecordPreset("Quick Clip", RecordArea.FULL_SCREEN, RecordFormat.GIF, 15, "Medium", AudioSource.NONE, [OverlayType.CURSOR]),
            RecordPreset("Webinar", RecordArea.FULL_SCREEN, RecordFormat.MP4, 30, "High", AudioSource.BOTH, [OverlayType.WEBCAM, OverlayType.CURSOR]),
        ]
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        samples = [
            RecordingSession("Nyrqis Demo Recording", RecordArea.FULL_SCREEN, RecordFormat.MP4, 60, "Ultra", AudioSource.SYSTEM, [OverlayType.CURSOR], now - 3600, 180, 245.6, "2560x1440", 15000),
            RecordingSession("Tutorial - Window Manager", RecordArea.WINDOW, RecordFormat.WEBM, 30, "High", AudioSource.BOTH, [OverlayType.CURSOR, OverlayType.WATERMARK], now - 7200, 600, 180.2, "1920x1080", 8000),
            RecordingSession("Bug Report Screenshot", RecordArea.CUSTOM, RecordFormat.GIF, 15, "Medium", AudioSource.NONE, [OverlayType.CURSOR_CLICK], now - 1800, 8, 2.4, "800x600", 2000),
            RecordingSession("Compositor Performance Test", RecordArea.FULL_SCREEN, RecordFormat.MKV, 120, "Ultra", AudioSource.NONE, [OverlayType.CURSOR], now - 86400, 30, 89.5, "3840x2160", 30000),
            RecordingSession("Team Standup", RecordArea.MONITOR_1, RecordFormat.MP4, 30, "High", AudioSource.BOTH, [OverlayType.WEBCAM, OverlayType.CURSOR], now - 43200, 1800, 520.8, "2560x1440", 8000),
        ]
        self._sessions = samples

    @property
    def selected_session(self) -> Optional[RecordingSession]:
        if 0 <= self._selected < len(self._sessions):
            return self._sessions[self._selected]
        return None

    @property
    def total_recordings(self) -> int:
        return len(self._sessions)

    @property
    def total_duration_secs(self) -> float:
        return sum(s.duration_secs for s in self._sessions)

    @property
    def total_duration_display(self) -> str:
        total = int(self.total_duration_secs)
        h, remainder = divmod(total, 3600)
        m, s = divmod(remainder, 60)
        return f"{h}h {m}m {s}s"

    @property
    def total_size(self) -> float:
        return sum(s.file_size_mb for s in self._sessions)

    @property
    def is_recording(self) -> bool:
        return self._status == RecordStatus.RECORDING

    def select(self, idx: int):
        if 0 <= idx < len(self._sessions):
            self._selected = idx

    def start_recording(self):
        if self._status == RecordStatus.IDLE:
            self._status = RecordStatus.RECORDING
            self._rec_start = time.time()
            self._current_duration = 0

    def stop_recording(self, name: str = "New Recording"):
        if self._status in (RecordStatus.RECORDING, RecordStatus.PAUSED):
            duration = time.time() - self._rec_start
            session = RecordingSession(
                name=name,
                area=self._current_area,
                format=self._current_format,
                fps=self._current_fps,
                quality=self._current_quality,
                audio_source=self._current_audio,
                overlays=list(self._current_overlays),
                timestamp=time.time(),
                duration_secs=duration,
                file_size_mb=duration * 0.5,
                resolution="2560x1440" if self._current_area == RecordArea.FULL_SCREEN else "1920x1080",
                bitrate_kbps=8000 if self._current_quality == "High" else 15000 if self._current_quality == "Ultra" else 4000,
            )
            self._sessions.append(session)
            self._selected = len(self._sessions) - 1
            self._status = RecordStatus.IDLE
            return session
        return None

    def pause_recording(self):
        if self._status == RecordStatus.RECORDING:
            self._status = RecordStatus.PAUSED

    def resume_recording(self):
        if self._status == RecordStatus.PAUSED:
            self._status = RecordStatus.RECORDING

    def delete_session(self, idx: int) -> bool:
        if 0 <= idx < len(self._sessions):
            self._sessions.pop(idx)
            if self._selected >= len(self._sessions):
                self._selected = max(0, len(self._sessions) - 1)
            return True
        return False

    def set_area(self, area: RecordArea):
        self._current_area = area

    def set_format(self, fmt: RecordFormat):
        self._current_format = fmt

    def set_fps(self, fps: int):
        self._current_fps = fps

    def toggle_overlay(self, overlay: OverlayType):
        if overlay in self._current_overlays:
            self._current_overlays.remove(overlay)
        else:
            self._current_overlays.append(overlay)

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                      NYRQIS SCREEN RECORDER                                ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        status_icons = {RecordStatus.IDLE: "⏹ IDLE", RecordStatus.RECORDING: "🔴 RECORDING", RecordStatus.PAUSED: "⏸ PAUSED", RecordStatus.PROCESSING: "⚙️ PROCESSING"}
        lines.append(f"  Status: {status_icons[self._status]}  Area: {self._current_area.value}  Format: {self._current_format.value.upper()}")
        lines.append(f"  FPS: {self._current_fps}  Quality: {self._current_quality}  Audio: {self._current_audio.value}")
        overlays = ", ".join(o.value for o in self._current_overlays) or "none"
        lines.append(f"  Overlays: {overlays}  Stop: {self._hotkey_stop}")
        lines.append(f"  Output: {self._output_dir}  Total: {self.total_recordings} recordings ({self.total_duration_display}, {self.total_size:.0f} MB)")
        lines.append("")
        lines.append("  ── Recordings ────────────────────────────────────────────")
        for i, s in enumerate(self._sessions):
            sel = "▶" if i == self._selected else " "
            lines.append(f"  {sel} {s.name}")
            lines.append(f"    {s.display_duration} · {s.format.value.upper()} · {s.resolution} · {s.fps}fps · {s.display_size}")
        lines.append("")
        lines.append("  ── Presets ──────────────────────────────────────────────")
        for p in self._presets:
            lines.append(f"  ⚡ {p.name}: {p.area.value} · {p.format.value} · {p.fps}fps · {p.quality}")
        lines.append("")
        lines.append("  [R]ecord  [P]ause  [S]top  [A]rea  [F]ormat  [O]verlay")
        return lines

    def render_session_detail(self) -> list:
        s = self.selected_session
        if not s:
            return ["  No recording selected"]
        lines = []
        lines.append(f"  ── {s.name} ──")
        lines.append(f"  Duration: {s.display_duration}")
        lines.append(f"  Format: {s.format.value.upper()} · {s.fps}fps · {s.quality}")
        lines.append(f"  Resolution: {s.resolution}")
        lines.append(f"  Bitrate: {s.bitrate_kbps} kbps")
        lines.append(f"  Size: {s.display_size}")
        lines.append(f"  Audio: {s.audio_source.value}")
        overlays = ", ".join(o.value for o in s.overlays) or "none"
        lines.append(f"  Overlays: {overlays}")
        return lines
