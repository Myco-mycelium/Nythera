"""Video Editor — timeline, transitions, export presets for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time


class TransitionType(Enum):
    NONE = "None"
    CROSSFADE = "Crossfade"
    WIPE_LEFT = "Wipe Left"
    WIPE_RIGHT = "Wipe Right"
    WIPE_UP = "Wipe Up"
    WIPE_DOWN = "Wipe Down"
    FADE_BLACK = "Fade to Black"
    FADE_WHITE = "Fade to White"
    DISSOLVE = "Dissolve"
    ZOOM_IN = "Zoom In"
    ZOOM_OUT = "Zoom Out"
    SLIDE_LEFT = "Slide Left"
    SLIDE_RIGHT = "Slide Right"
    PUSH = "Push"
    MORPH = "Morph"


class EffectType(Enum):
    NONE = "None"
    BRIGHTNESS = "Brightness"
    CONTRAST = "Contrast"
    SATURATION = "Saturation"
    BLUR = "Blur"
    SHARPEN = "Sharpen"
    VIGNETTE = "Vignette"
    SPEED = "Speed"
    REVERSE = "Reverse"
    STABILIZE = "Stabilize"
    CHROMA_KEY = "Chroma Key"
    COLOR_GRADE = "Color Grade"
    FILM_GRAIN = "Film Grain"
    VHS = "VHS Effect"
    GLITCH = "Glitch"


class ExportFormat(Enum):
    MP4_H264 = "MP4 (H.264)"
    MP4_H265 = "MP4 (H.265/HEVC)"
    WEBM_VP9 = "WebM (VP9)"
    MOV_PRORES = "MOV (ProRes)"
    MKV = "MKV"
    GIF = "GIF"
    MP3 = "MP3 (Audio Only)"
    WAV = "WAV (Audio Only)"


class Resolution(Enum):
    R_4K = "3840x2160"
    R_1440P = "2560x1440"
    R_1080P = "1920x1080"
    R_720P = "1280x720"
    R_480P = "854x480"
    R_360P = "640x360"
    CUSTOM = "Custom"


class AspectRatio(Enum):
    R_16_9 = "16:9"
    R_4_3 = "4:3"
    R_21_9 = "21:9 (Ultrawide)"
    R_1_1 = "1:1 (Square)"
    R_9_16 = "9:16 (Vertical)"
    R_4_5 = "4:5 (Instagram)"


class AudioCodec(Enum):
    AAC = "AAC"
    MP3 = "MP3"
    FLAC = "FLAC"
    PCM = "PCM"
    OPUS = "Opus"
    NONE = "None"


@dataclass
class VideoClip:
    id: int
    name: str
    file_path: str = ""
    duration_s: float = 10.0
    fps: float = 30.0
    width: int = 1920
    height: int = 1080
    codec: str = "h264"
    bitrate: int = 8000  # kbps
    start_frame: int = 0
    end_frame: int = 0
    in_point: float = 0.0
    out_point: float = 0.0
    speed: float = 1.0
    volume: float = 1.0
    muted: bool = False
    opacity: float = 1.0
    position_x: float = 0.0
    position_y: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0
    effects: List[EffectType] = field(default_factory=list)
    color: str = "#4a9eff"

    def __post_init__(self):
        if self.end_frame == 0:
            self.end_frame = int(self.duration_s * self.fps)
        if self.out_point == 0:
            self.out_point = self.duration_s

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def duration_str(self) -> str:
        total = self.duration_s
        m = int(total // 60)
        s = total % 60
        return f"{m}:{s:05.2f}"

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def bitrate_str(self) -> str:
        if self.bitrate >= 10000:
            return f"{self.bitrate / 1000:.1f} Mbps"
        return f"{self.bitrate} kbps"

    @property
    def thumbnail(self) -> str:
        return f"[{'▓' * 20}]"


@dataclass
class AudioClip:
    id: int
    name: str
    file_path: str = ""
    duration_s: float = 10.0
    sample_rate: int = 44100
    channels: int = 2
    start_frame: int = 0
    end_frame: int = 0
    volume: float = 1.0
    muted: bool = False
    pan: float = 0.0
    effects: List[str] = field(default_factory=list)
    color: str = "#69db7c"

    @property
    def duration_str(self) -> str:
        m = int(self.duration_s // 60)
        s = self.duration_s % 60
        return f"{m}:{s:05.2f}"

    @property
    def waveform(self) -> str:
        import random
        random.seed(self.id)
        return "".join("▁▂▃▄▅▆▇█"[random.randint(0, 7)] for _ in range(20))


@dataclass
class Transition:
    clip_a_id: int
    clip_b_id: int
    transition_type: TransitionType = TransitionType.CROSSFADE
    duration_s: float = 0.5
    easing: str = "linear"

    @property
    def duration_frames(self) -> float:
        return self.duration_s * 30  # assume 30fps


@dataclass
class TextOverlay:
    text: str
    start_frame: int = 0
    end_frame: int = 90
    x: float = 0.5  # 0.0 to 1.0
    y: float = 0.5
    font_size: int = 48
    font: str = "Arial"
    color: str = "#ffffff"
    bg_color: str = ""
    opacity: float = 1.0
    animation: str = "None"  # None, Fade In, Fade Out, Typewriter


@dataclass
class ExportPreset:
    name: str
    format: ExportFormat = ExportFormat.MP4_H264
    resolution: Resolution = Resolution.R_1080P
    fps: float = 30.0
    video_bitrate: int = 8000
    audio_codec: AudioCodec = AudioCodec.AAC
    audio_bitrate: int = 192
    quality: str = "High"  # Low, Medium, High, Ultra
    two_pass: bool = False
    hardware_accel: bool = False

    @property
    def quality_bar(self) -> str:
        levels = {"Low": 2, "Medium": 5, "High": 8, "Ultra": 10}
        filled = levels.get(self.quality, 5)
        return "█" * filled + "░" * (10 - filled)

    @property
    def estimated_size(self) -> str:
        # Rough estimate: (video_bitrate + audio_bitrate) * duration / 8
        total_kbps = self.video_bitrate + self.audio_bitrate
        return f"~{total_kbps / 8:.0f} KB/s"


@dataclass
class Marker:
    name: str
    frame: int = 0
    color: str = "#ff6b6b"


class VideoEditor:
    def __init__(self):
        self._video_tracks: List[List[VideoClip]] = [[] for _ in range(4)]
        self._audio_tracks: List[List[AudioClip]] = [[] for _ in range(3)]
        self._transitions: List[Transition] = []
        self._text_overlays: List[TextOverlay] = []
        self._markers: List[Marker] = []
        self._selected_track: int = 0
        self._selected_clip_idx: int = 0
        self._selected_clip: Optional[VideoClip] = None
        self._playhead_frame: int = 0
        self._total_frames: int = 0
        self._fps: float = 30.0
        self._playing: bool = False
        self._view_mode: str = "timeline"
        self._zoom: float = 1.0
        self._export_presets: List[ExportPreset] = []
        self._selected_preset: int = 0
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        # Video clips on track 0
        self._video_tracks[0] = [
            VideoClip(1, "Intro", duration_s=5.0, color="#ff6b6b",
                      effects=[EffectType.COLOR_GRADE]),
            VideoClip(2, "Interview A", duration_s=45.0, color="#ffa94d",
                      effects=[EffectType.STABILIZE, EffectType.COLOR_GRADE]),
            VideoClip(3, "B-Roll City", duration_s=15.0, color="#69db7c",
                      effects=[EffectType.SPEED]),
            VideoClip(4, "Interview B", duration_s=35.0, color="#748ffc",
                      effects=[EffectType.STABILIZE]),
            VideoClip(5, "Outro", duration_s=8.0, color="#da77f2",
                      effects=[EffectType.VIGNETTE]),
        ]
        self._total_frames = int(108 * self._fps)

        # Text overlays
        self._text_overlays = [
            TextOverlay("Nyrqis OS Demo", 0, 150, 0.5, 0.1, 48, color="#ffffff",
                        animation="Fade In"),
            TextOverlay("Chapter 1: Introduction", 150, 300, 0.1, 0.9, 24,
                        bg_color="#00000080"),
            TextOverlay("www.nyrqis.io", self._total_frames - 150, self._total_frames,
                        0.5, 0.9, 20, color="#aaaaaa"),
        ]

        # Transitions
        self._transitions = [
            Transition(1, 2, TransitionType.CROSSFADE, 0.5),
            Transition(2, 3, TransitionType.WIPE_LEFT, 0.3),
            Transition(3, 4, TransitionType.DISSOLVE, 0.5),
            Transition(4, 5, TransitionType.FADE_BLACK, 1.0),
        ]

        # Audio tracks
        self._audio_tracks[0] = [
            AudioClip(10, "Main Audio", duration_s=108.0, color="#69db7c"),
        ]
        self._audio_tracks[1] = [
            AudioClip(11, "Background Music", duration_s=108.0, volume=0.3, color="#748ffc"),
        ]

        # Markers
        self._markers = [
            Marker("Intro", 0),
            Marker("Interview A", 150),
            Marker("B-Roll", 1500),
            Marker("Interview B", 1950),
            Marker("Outro", 3000),
        ]

        # Export presets
        self._export_presets = [
            ExportPreset("YouTube 1080p", ExportFormat.MP4_H264, Resolution.R_1080P, 30, 8000,
                         AudioCodec.AAC, 192, "High"),
            ExportPreset("YouTube 4K", ExportFormat.MP4_H265, Resolution.R_4K, 30, 35000,
                         AudioCodec.AAC, 320, "Ultra", two_pass=True),
            ExportPreset("Instagram Reel", ExportFormat.MP4_H264, Resolution.R_1080P, 30, 6000,
                         AudioCodec.AAC, 128, "High"),
            ExportPreset("Twitter/X", ExportFormat.MP4_H264, Resolution.R_720P, 30, 5000,
                         AudioCodec.AAC, 128, "Medium"),
            ExportPreset("Web Preview", ExportFormat.WEBM_VP9, Resolution.R_720P, 30, 3000,
                         AudioCodec.OPUS, 128, "Medium"),
            ExportPreset("Archive", ExportFormat.MOV_PRORES, Resolution.R_4K, 30, 100000,
                         AudioCodec.PCM, 1536, "Ultra", hardware_accel=True),
            ExportPreset("GIF Animation", ExportFormat.GIF, Resolution.R_480P, 15, 0,
                         AudioCodec.NONE, 0, "Medium"),
        ]
        self._selected_preset = 0

    @property
    def selected_clip(self) -> Optional[VideoClip]:
        if 0 <= self._selected_track < len(self._video_tracks):
            clips = self._video_tracks[self._selected_track]
            if 0 <= self._selected_clip_idx < len(clips):
                return clips[self._selected_clip_idx]
        return None

    @property
    def playhead_time(self) -> str:
        t = self._playhead_frame / self._fps
        m = int(t // 60)
        s = t % 60
        f = self._playhead_frame % int(self._fps)
        return f"{m:02d}:{s:05.2f}.{f:02d}"

    @property
    def total_duration(self) -> str:
        t = self._total_frames / self._fps
        m = int(t // 60)
        s = t % 60
        return f"{m:02d}:{s:05.2f}"

    @property
    def total_clips(self) -> int:
        return sum(len(tr) for tr in self._video_tracks)

    @property
    def total_audio_clips(self) -> int:
        return sum(len(tr) for tr in self._audio_tracks)

    def select_track(self, idx: int):
        if 0 <= idx < len(self._video_tracks):
            self._selected_track = idx

    def select_clip(self, track: int, clip_idx: int):
        if 0 <= track < len(self._video_tracks):
            clips = self._video_tracks[track]
            if 0 <= clip_idx < len(clips):
                self._selected_track = track
                self._selected_clip_idx = clip_idx
                self._selected_clip = clips[clip_idx]

    def toggle_play(self):
        self._playing = not self._playing

    def handle_input(self, key: str):
        key = key.lower()
        if key == " ":
            self.toggle_play()
        elif key == "n":
            self.add_clip()
        elif key == "d":
            self.delete_clip()
        elif key == "t":
            self._view_mode = "transitions"
        elif key == "e":
            self._view_mode = "export"

    def add_clip(self):
        track = self._selected_track
        if track < len(self._video_tracks):
            clip_id = max(c.id for tr in self._video_tracks for c in tr) + 1
            clip = VideoClip(clip_id, f"Clip {clip_id}", duration_s=5.0, color="#868e96")
            self._video_tracks[track].append(clip)
            self._history.append(f"Added clip to track {track}")

    def delete_clip(self):
        if self._selected_clip:
            self._video_tracks[self._selected_track].remove(self._selected_clip)
            self._selected_clip = None
            self._history.append("Deleted clip")

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS VIDEO EDITOR                                      ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Info
        play = "▶" if self._playing else "⏸"
        lines.append(f"  {play}  {self.playhead_time} / {self.total_duration}  {self._fps:.0f}fps  Clips: {self.total_clips}  Audio: {self.total_audio_clips}")
        lines.append("")

        # Timeline
        lines.append("  ── Timeline ──")
        for track_idx, clips in enumerate(self._video_tracks):
            sel = "▶" if track_idx == self._selected_track else " "
            track_name = f"V{track_idx + 1}"
            track_str = ""
            for clip in clips:
                bars = int(min(clip.duration_s / 2, 15))
                track_str += f"{'▓' * bars}"
                track_str += "│"
            lines.append(f"  {sel} {track_name} {track_str}")

        # Audio tracks
        for track_idx, clips in enumerate(self._audio_tracks):
            track_name = f"A{track_idx + 1}"
            track_str = ""
            for clip in clips:
                bars = int(min(clip.duration_s / 2, 60))
                track_str += f"{'░' * bars}"
            lines.append(f"     {track_name} {track_str}")
        lines.append("")

        # Selected clip detail
        clip = self.selected_clip
        if clip:
            lines.append(f"  ── Selected: {clip.name} ──")
            lines.append(f"  Duration: {clip.duration_str}  Resolution: {clip.resolution_str}  Codec: {clip.codec}  Bitrate: {clip.bitrate_str}")
            lines.append(f"  Speed: {clip.speed}x  Volume: {clip.volume:.0%}  Opacity: {clip.opacity:.0%}  Scale: {clip.scale:.1f}x")
            if clip.effects:
                lines.append(f"  Effects: {' → '.join(e.value for e in clip.effects)}")
            lines.append(f"  Thumbnail: {clip.thumbnail}")
            lines.append("")

        # Transitions
        if self._transitions:
            lines.append("  ── Transitions ──")
            for t in self._transitions:
                lines.append(f"  ↔ {t.transition_type.value} ({t.duration_s:.1f}s)")
            lines.append("")

        # Text overlays
        if self._text_overlays:
            lines.append("  ── Text Overlays ──")
            for to in self._text_overlays:
                lines.append(f"  T \"{to.text}\"  Size: {to.font_size}  Anim: {to.animation}")
            lines.append("")

        # Export
        lines.append(f"  ── Export Presets ──")
        for i, p in enumerate(self._export_presets):
            sel = "▶" if i == self._selected_preset else " "
            lines.append(f"  {sel} {p.name}  {p.format.value}  {p.resolution.value}  [{p.quality_bar}] {p.estimated_size}")
        lines.append("")

        lines.append("  [Space]Play/Pause [N]New Clip [D]Delete [T]Transitions [E]Export")
        lines.append("  [↑↓]Track [←→]Clip [Ctrl+E]Export")
        return lines
