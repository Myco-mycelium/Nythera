"""
Nyrqis Screen Recorder — desktop recording with annotations and format options.

Features:
- Record full screen or region selection
- Multiple output formats (WebM, MP4, MKV, GIF)
- Frame rate selection (15, 30, 60 fps)
- Quality presets (Low, Medium, High, Lossless)
- Recording timer with pause/resume
- Annotations during recording (arrows, text, highlights)
- Recording history with playback info
- Audio recording toggle (system + microphone)
- Screenshot during recording
- Hotkey support
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class RecordingFormat(Enum):
    WEBM = "WebM"
    MP4 = "MP4"
    MKV = "MKV"
    GIF = "GIF"
    AVI = "AVI"


class QualityPreset(Enum):
    LOW = "Low (480p)"
    MEDIUM = "Medium (720p)"
    HIGH = "High (1080p)"
    ULTRA = "Ultra (4K)"
    LOSSLESS = "Lossless"


class RecordingStatus(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"


class AnnotationType(Enum):
    ARROW = "arrow"
    TEXT = "text"
    HIGHLIGHT = "highlight"
    RECTANGLE = "rectangle"
    CIRCLE = "circle"
    FREEHAND = "freehand"
    BLUR = "blur"


@dataclass
class Annotation:
    """A recording annotation."""
    ann_type: AnnotationType
    x: int = 0
    y: int = 0
    x2: int = 0
    y2: int = 0
    text: str = ""
    color: str = "#FF0000"
    thickness: int = 3
    timestamp: float = 0.0

    @property
    def icon(self) -> str:
        icons = {
            AnnotationType.ARROW: "➡️",
            AnnotationType.TEXT: "📝",
            AnnotationType.HIGHLIGHT: "🟡",
            AnnotationType.RECTANGLE: "⬜",
            AnnotationType.CIRCLE: "⭕",
            AnnotationType.FREEHAND: "✏️",
            AnnotationType.BLUR: "🔲",
        }
        return icons.get(self.ann_type, "❓")


@dataclass
class RecordingRegion:
    """Screen region for recording."""
    x: int = 0
    y: int = 0
    width: int = 1920
    height: int = 1080

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass
class Recording:
    """A completed recording entry."""
    filename: str
    format: RecordingFormat = RecordingFormat.WEBM
    quality: QualityPreset = QualityPreset.HIGH
    region: RecordingRegion = field(default_factory=RecordingRegion)
    duration: float = 0.0
    file_size: int = 0
    frame_rate: int = 30
    has_audio: bool = True
    has_system_audio: bool = True
    has_microphone: bool = False
    annotations_count: int = 0
    created: float = field(default_factory=time.time)
    recording_id: str = ""
    fps_actual: float = 0.0
    bitrate: int = 0

    def __post_init__(self):
        if not self.recording_id:
            self.recording_id = hashlib.md5(f"{self.filename}{self.created}".encode()).hexdigest()[:8]

    @property
    def duration_str(self) -> str:
        h = int(self.duration // 3600)
        m = int((self.duration % 3600) // 60)
        s = int(self.duration % 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @property
    def size_str(self) -> str:
        b = self.file_size
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        return f"{b / (1024 * 1024 * 1024):.2f} GB"

    @property
    def date_str(self) -> str:
        return datetime.fromtimestamp(self.created).strftime("%Y-%m-%d %H:%M")

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.created
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.created).strftime("%b %d")

    @property
    def fps_str(self) -> str:
        return f"{self.frame_rate} fps"

    @property
    def bitrate_str(self) -> str:
        if self.bitrate < 1000:
            return f"{self.bitrate} kbps"
        return f"{self.bitrate / 1000:.1f} Mbps"


# ─── Screen Recorder ─────────────────────────────────────────────────────


class ScreenRecorder:
    """
    Screen recorder for Nyrqis OS.

    Manages recording sessions with annotations and history.
    """

    def __init__(self):
        # Recording state
        self._status: RecordingStatus = RecordingStatus.IDLE
        self._start_time: float = 0.0
        self._pause_time: float = 0.0
        self._paused_duration: float = 0.0

        # Settings
        self._format: RecordingFormat = RecordingFormat.WEBM
        self._quality: QualityPreset = QualityPreset.HIGH
        self._frame_rate: int = 30
        self._region = RecordingRegion()
        self._full_screen: bool = True
        self._system_audio: bool = True
        self._microphone: bool = False
        self._cursor_visible: bool = True
        self._countdown: int = 3

        # Annotations
        self._annotations: List[Annotation] = []
        self._active_annotation: Optional[Annotation] = None
        self._annotation_tool: AnnotationType = AnnotationType.ARROW
        self._annotation_color: str = "#FF0000"

        # History
        self._recordings: List[Recording] = []

        # View state
        self._selected_index: int = 0
        self._view_mode: str = "controls"  # controls, history, settings

        # Callbacks
        self._on_status_change: List[Callable] = []

        # Init sample history
        self._init_sample_recordings()

    def _init_sample_recordings(self) -> None:
        now = time.time()
        samples = [
            ("nyrqis_demo_2026_09_01.webm", RecordingFormat.WEBM, QualityPreset.HIGH, 125.0, 45 * 1024 * 1024, 30, True, 3, now - 86400 * 2),
            ("bug_report_terminal.mp4", RecordingFormat.MP4, QualityPreset.MEDIUM, 32.0, 12 * 1024 * 1024, 30, True, 1, now - 86400),
            ("tutorial_window_management.mkv", RecordingFormat.MKV, QualityPreset.HIGH, 240.0, 89 * 1024 * 1024, 60, True, 5, now - 3600 * 5),
            ("sprint_demo.gif", RecordingFormat.GIF, QualityPreset.LOW, 15.0, 8 * 1024 * 1024, 15, False, 0, now - 3600 * 2),
            ("code_review_session.webm", RecordingFormat.WEBM, QualityPreset.HIGH, 1800.0, 650 * 1024 * 1024, 30, True, 2, now - 86400 * 3),
        ]

        for fname, fmt, quality, dur, size, fps, audio, ann_count, ts in samples:
            self._recordings.append(Recording(
                filename=fname, format=fmt, quality=quality,
                duration=dur, file_size=size, frame_rate=fps,
                has_audio=audio, annotations_count=ann_count,
                created=ts,
                bitrate=2500 if fmt != RecordingFormat.GIF else 500,
                fps_actual=fps * 0.98,
            ))

    # ── Recording Control ─────────────────────────────────────────────

    def start_recording(self) -> None:
        self._status = RecordingStatus.RECORDING
        self._start_time = time.time()
        self._paused_duration = 0
        self._annotations.clear()
        self._notify("start")

    def pause_recording(self) -> None:
        if self._status == RecordingStatus.RECORDING:
            self._status = RecordingStatus.PAUSED
            self._pause_time = time.time()
            self._notify("pause")

    def resume_recording(self) -> None:
        if self._status == RecordingStatus.PAUSED:
            self._paused_duration += time.time() - self._pause_time
            self._status = RecordingStatus.RECORDING
            self._notify("resume")

    def stop_recording(self) -> Optional[Recording]:
        if self._status in (RecordingStatus.RECORDING, RecordingStatus.PAUSED):
            duration = time.time() - self._start_time - self._paused_duration
            self._status = RecordingStatus.STOPPED

            # Create recording entry
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = self._format.value.lower()
            filename = f"recording_{timestamp}.{ext}"

            file_size = int(duration * self._bitrate_for_quality() * 1000 / 8)

            recording = Recording(
                filename=filename,
                format=self._format,
                quality=self._quality,
                region=self._region if not self._full_screen else RecordingRegion(0, 0, 1920, 1080),
                duration=duration,
                file_size=file_size,
                frame_rate=self._frame_rate,
                has_audio=self._system_audio or self._microphone,
                has_system_audio=self._system_audio,
                has_microphone=self._microphone,
                annotations_count=len(self._annotations),
                bitrate=self._bitrate_for_quality(),
                fps_actual=self._frame_rate * 0.97,
            )
            self._recordings.insert(0, recording)

            # Reset
            self._status = RecordingStatus.IDLE
            self._notify("stop")
            return recording
        return None

    def _bitrate_for_quality(self) -> int:
        """Get bitrate in kbps for current quality."""
        bitrates = {
            QualityPreset.LOW: 1000,
            QualityPreset.MEDIUM: 2500,
            QualityPreset.HIGH: 5000,
            QualityPreset.ULTRA: 15000,
            QualityPreset.LOSSLESS: 50000,
        }
        return bitrates.get(self._quality, 5000)

    @property
    def elapsed_time(self) -> float:
        if self._status == RecordingStatus.IDLE:
            return 0.0
        elapsed = time.time() - self._start_time - self._paused_duration
        return max(0, elapsed)

    @property
    def elapsed_str(self) -> str:
        t = self.elapsed_time
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t % 1) * 100)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}.{ms:02d}"

    @property
    def is_recording(self) -> bool:
        return self._status == RecordingStatus.RECORDING

    @property
    def is_paused(self) -> bool:
        return self._status == RecordingStatus.PAUSED

    @property
    def status(self) -> RecordingStatus:
        return self._status

    @property
    def status_icon(self) -> str:
        icons = {
            RecordingStatus.IDLE: "⏹️",
            RecordingStatus.RECORDING: "🔴",
            RecordingStatus.PAUSED: "⏸️",
            RecordingStatus.STOPPED: "⏹️",
        }
        return icons.get(self._status, "❓")

    # ── Annotations ───────────────────────────────────────────────────

    def add_annotation(self, ann_type: AnnotationType = None, **kwargs) -> Annotation:
        ann = Annotation(
            ann_type=ann_type or self._annotation_tool,
            timestamp=self.elapsed_time,
            color=self._annotation_color,
            **kwargs,
        )
        self._annotations.append(ann)
        return ann

    def remove_annotation(self, index: int) -> bool:
        if 0 <= index < len(self._annotations):
            self._annotations.pop(index)
            return True
        return False

    def clear_annotations(self) -> int:
        count = len(self._annotations)
        self._annotations.clear()
        return count

    def set_annotation_tool(self, tool: AnnotationType) -> None:
        self._annotation_tool = tool

    def set_annotation_color(self, color: str) -> None:
        self._annotation_color = color

    @property
    def annotations(self) -> List[Annotation]:
        return list(self._annotations)

    @property
    def annotation_tool(self) -> AnnotationType:
        return self._annotation_tool

    # ── Settings ──────────────────────────────────────────────────────

    def set_format(self, fmt: RecordingFormat) -> None:
        self._format = fmt

    def set_quality(self, quality: QualityPreset) -> None:
        self._quality = quality

    def set_frame_rate(self, fps: int) -> None:
        self._frame_rate = max(10, min(120, fps))

    def toggle_full_screen(self) -> bool:
        self._full_screen = not self._full_screen
        return self._full_screen

    def toggle_system_audio(self) -> bool:
        self._system_audio = not self._system_audio
        return self._system_audio

    def toggle_microphone(self) -> bool:
        self._microphone = not self._microphone
        return self._microphone

    def toggle_cursor(self) -> bool:
        self._cursor_visible = not self._cursor_visible
        return self._cursor_visible

    @property
    def format(self) -> RecordingFormat:
        return self._format

    @property
    def quality(self) -> QualityPreset:
        return self._quality

    @property
    def frame_rate(self) -> int:
        return self._frame_rate

    @property
    def full_screen(self) -> bool:
        return self._full_screen

    @property
    def system_audio(self) -> bool:
        return self._system_audio

    @property
    def microphone(self) -> bool:
        return self._microphone

    # ── History ───────────────────────────────────────────────────────

    @property
    def recordings(self) -> List[Recording]:
        return list(self._recordings)

    def delete_recording(self, recording_id: str) -> bool:
        for i, r in enumerate(self._recordings):
            if r.recording_id == recording_id:
                self._recordings.pop(i)
                return True
        return False

    @property
    def total_recordings(self) -> int:
        return len(self._recordings)

    @property
    def total_duration(self) -> float:
        return sum(r.duration for r in self._recordings)

    @property
    def total_size(self) -> int:
        return sum(r.file_size for r in self._recordings)

    def total_size_str(self) -> str:
        b = self.total_size
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        return f"{b / (1024 * 1024 * 1024):.2f} GB"

    # ── View ──────────────────────────────────────────────────────────

    def set_view(self, mode: str) -> None:
        self._view_mode = mode

    def cycle_view(self) -> str:
        views = ["controls", "history", "settings"]
        idx = views.index(self._view_mode)
        self._view_mode = views[(idx + 1) % len(views)]
        return self._view_mode

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        self._selected_index = min(len(self._recordings) - 1, self._selected_index + 1)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_controls(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🎬 Screen Recorder")
        lines.append("─" * width)

        # Status
        if self.is_recording or self.is_paused:
            lines.append(f" {self.status_icon} {self._status.value.upper()} — {self.elapsed_str}")
            lines.append(f" Format: {self._format.value} | Quality: {self._quality.value} | {self._frame_rate} fps")
            lines.append(f" Region: {'Full Screen' if self._full_screen else self._region.resolution_str}")
            lines.append(f" Audio: {'🔊 System' if self._system_audio else ''} {'🎤 Mic' if self._microphone else ''}")
            lines.append(f" Annotations: {len(self._annotations)}")
        else:
            lines.append(" Ready to record")
            lines.append("")
            lines.append(f" Format:    {self._format.value}")
            lines.append(f" Quality:   {self._quality.value}")
            lines.append(f" Frame Rate: {self._frame_rate} fps")
            lines.append(f" Region:    {'Full Screen' if self._full_screen else self._region.resolution_str}")
            lines.append(f" System:    {'✅ On' if self._system_audio else '❌ Off'}")
            lines.append(f" Microphone: {'✅ On' if self._microphone else '❌ Off'}")
            lines.append(f" Cursor:    {'✅ Visible' if self._cursor_visible else '❌ Hidden'}")

        lines.append("─" * width)

        if self.is_recording or self.is_paused:
            if self.is_recording:
                lines.append(" Space:Pause  ■:Stop  A:Annotate  S:Screenshot")
            else:
                lines.append(" Space:Resume  ■:Stop  A:Annotate")
        else:
            lines.append(" ●:Start Recording  S:Settings  H:History")

        return lines

    def render_history(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🎬 Recording History")
        lines.append(f" {self.total_recordings} recordings · {self.total_size_str()} total")
        lines.append("─" * width)

        if not self._recordings:
            lines.append("  No recordings yet.")
        else:
            for i, rec in enumerate(self._recordings):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {rec.filename[:width - 8]}")
                lines.append(f"   {rec.format.value} · {rec.quality.value} · {rec.fps_str}")
                lines.append(f"   ⏱ {rec.duration_str} · 📦 {rec.size_str} · {rec.bitrate_str}")
                if rec.annotations_count:
                    lines.append(f"   🏷️ {rec.annotations_count} annotations")
                lines.append(f"   📅 {rec.date_str} ({rec.time_ago})")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Del:Delete  Esc:Back")
        return lines

    def render_settings(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🎬 Recorder Settings")
        lines.append("─" * width)

        lines.append(f"  Format:        {self._format.value}")
        lines.append(f"  Quality:       {self._quality.value}")
        lines.append(f"  Frame Rate:    {self._frame_rate} fps")
        lines.append(f"  Full Screen:   {'✅' if self._full_screen else '❌'}")
        lines.append(f"  System Audio:  {'✅' if self._system_audio else '❌'}")
        lines.append(f"  Microphone:    {'✅' if self._microphone else '❌'}")
        lines.append(f"  Show Cursor:   {'✅' if self._cursor_visible else '❌'}")
        lines.append(f"  Countdown:     {self._countdown}s")

        lines.append("")
        lines.append(f"  Estimated bitrate: {self._bitrate_for_quality()} kbps")

        lines.append("─" * width)
        lines.append(" F:Format  Q:Quality  ↑↓:FPS  A:Audio  Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        if self._view_mode == "history":
            return self.render_history(width)
        elif self._view_mode == "settings":
            return self.render_settings(width)
        return self.render_controls(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "history":
            return self._handle_history_key(key)
        elif self._view_mode == "settings":
            return self._handle_settings_key(key)
        return self._handle_controls_key(key)

    def _handle_controls_key(self, key: str) -> Optional[str]:
        if key == " ":
            if self._status == RecordingStatus.IDLE:
                self.start_recording()
                return "start"
            elif self._status == RecordingStatus.RECORDING:
                self.pause_recording()
                return "pause"
            elif self._status == RecordingStatus.PAUSED:
                self.resume_recording()
                return "resume"
        elif key == "s" or key == "S":
            if self._status == RecordingStatus.IDLE:
                self._view_mode = "settings"
                return "settings"
        elif key == "h":
            self._view_mode = "history"
            return "history"
        elif key == "f":
            formats = list(RecordingFormat)
            idx = formats.index(self._format)
            self._format = formats[(idx + 1) % len(formats)]
            return "cycle_format"
        elif key == "q":
            qualities = list(QualityPreset)
            idx = qualities.index(self._quality)
            self._quality = qualities[(idx + 1) % len(qualities)]
            return "cycle_quality"
        return None

    def _handle_history_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "controls"
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Delete":
            if 0 <= self._selected_index < len(self._recordings):
                self.delete_recording(self._recordings[self._selected_index].recording_id)
            return "delete"
        return None

    def _handle_settings_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "controls"
            return "back"
        elif key == "f":
            formats = list(RecordingFormat)
            idx = formats.index(self._format)
            self._format = formats[(idx + 1) % len(formats)]
            return "cycle_format"
        elif key == "q":
            qualities = list(QualityPreset)
            idx = qualities.index(self._quality)
            self._quality = qualities[(idx + 1) % len(qualities)]
            return "cycle_quality"
        elif key == "ArrowUp":
            self._frame_rate = min(120, self._frame_rate + 15)
            return "increase_fps"
        elif key == "ArrowDown":
            self._frame_rate = max(10, self._frame_rate - 15)
            return "decrease_fps"
        elif key == "a":
            self.toggle_system_audio()
            return "toggle_audio"
        return None

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_status_change(self, cb: Callable) -> None:
        self._on_status_change.append(cb)

    def _notify(self, event: str) -> None:
        for cb in self._on_status_change:
            try:
                cb(event, self._status)
            except Exception:
                pass
