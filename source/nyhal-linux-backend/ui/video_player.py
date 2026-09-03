"""
Nyrqis Video Player — full-featured video player with controls and playlist.

Features:
- Playback controls (play/pause, seek, speed, stop)
- Volume control with mute toggle
- Playlist management with queue
- Fullscreen toggle
- Aspect ratio selection (auto, 16:9, 4:3, 1:1, fill)
- Subtitle/caption support (text overlay)
- Video info display (resolution, codec, duration)
- Frame-by-frame stepping
- Chapter navigation
"""

import os
import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple, Callable
from datetime import datetime, timedelta


# ─── Data Classes ────────────────────────────────────────────────────────


class AspectRatio(Enum):
    AUTO = "auto"
    SIXTEEN_NINE = "16:9"
    FOUR_THREE = "4:3"
    ONE_ONE = "1:1"
    FILL = "fill"


class RepeatMode(Enum):
    OFF = "off"
    ALL = "all"
    ONE = "one"


@dataclass
class Chapter:
    """A video chapter marker."""
    title: str
    start_time: float  # seconds
    end_time: float = 0.0

    @property
    def duration_str(self) -> str:
        d = self.end_time - self.start_time
        return self._fmt(d)

    @property
    def start_str(self) -> str:
        return self._fmt(self.start_time)

    def _fmt(self, seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


@dataclass
class Subtitle:
    """A subtitle entry."""
    start_time: float
    end_time: float
    text: str
    style: str = ""  # bold, italic, etc.


@dataclass
class VideoInfo:
    """Metadata about a video file."""
    title: str
    filename: str
    url: str = ""
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    duration: float = 0.0  # seconds
    codec: str = "h264"
    audio_codec: str = "aac"
    bitrate: int = 5000  # kbps
    file_size: int = 0  # bytes
    audio_channels: int = 2
    subtitle_count: int = 0

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def duration_str(self) -> str:
        return self._fmt_time(self.duration)

    @property
    def size_str(self) -> str:
        if self.file_size <= 0:
            return "Unknown"
        b = self.file_size
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        else:
            return f"{b / (1024 * 1024 * 1024):.2f} GB"

    @property
    def bitrate_str(self) -> str:
        if self.bitrate < 1000:
            return f"{self.bitrate} kbps"
        return f"{self.bitrate / 1000:.1f} Mbps"

    @property
    def fps_str(self) -> str:
        return f"{self.fps:.1f} fps"

    def _fmt_time(self, seconds: float) -> str:
        seconds = max(0, seconds)
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


@dataclass
class PlaylistItem:
    """An item in the playlist."""
    info: VideoInfo
    added_at: float = field(default_factory=time.time)
    play_count: int = 0
    last_played: float = 0.0
    rating: int = 0  # 0-5 stars
    watched: bool = False
    progress: float = 0.0  # 0.0-1.0 playback position

    @property
    def display_title(self) -> str:
        return self.info.title or self.info.filename

    @property
    def is_watched(self) -> bool:
        return self.progress >= 0.95


# ─── Video Player ────────────────────────────────────────────────────────


class VideoPlayer:
    """
    Video player for Nyrqis OS.

    Manages playback, playlists, and video rendering state.
    """

    def __init__(self, width: int = 1280, height: int = 720):
        self._width = width
        self._height = height

        # Playback state
        self._playing: bool = False
        self._current_time: float = 0.0
        self._playback_speed: float = 1.0
        self._volume: int = 75
        self._muted: bool = False
        self._last_update: float = 0.0

        # Current video
        self._current_video: Optional[VideoInfo] = None
        self._current_index: int = -1

        # Playlist
        self._playlist: List[PlaylistItem] = []
        self._queue: List[int] = []  # indices into playlist

        # Settings
        self._aspect_ratio: AspectRatio = AspectRatio.AUTO
        self._repeat_mode: RepeatMode = RepeatMode.OFF
        self._shuffle: bool = False
        self._fullscreen: bool = False

        # Subtitles
        self._subtitles: List[Subtitle] = []
        self._subtitle_enabled: bool = True
        self._subtitle_size: int = 100  # percent
        self._active_subtitle: Optional[Subtitle] = None

        # Chapters
        self._chapters: List[Chapter] = []

        # UI state
        self._show_controls: bool = True
        self._show_playlist: bool = False
        self._show_info: bool = False
        self._show_speed_menu: bool = False
        self._show_volume_slider: bool = False
        self._controls_timer: float = 0.0

        # Speed options
        self._speed_options = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]

        # Callbacks
        self._on_play: List[Callable] = []
        self._on_pause: List[Callable] = []
        self._on_seek: List[Callable] = []
        self._on_track_change: List[Callable] = []
        self._on_end: List[Callable] = []

    # ── Playback Control ──────────────────────────────────────────────

    def play(self) -> bool:
        """Start or resume playback."""
        if not self._current_video and self._playlist:
            self._load_playlist_item(0)
        if not self._current_video:
            return False

        self._playing = True
        self._last_update = time.time()
        self._notify("play")
        return True

    def pause(self) -> bool:
        """Pause playback."""
        if self._playing:
            self._playing = False
            self._notify("pause")
            return True
        return False

    def toggle_play(self) -> bool:
        """Toggle play/pause."""
        if self._playing:
            return self.pause()
        else:
            return self.play()

    def stop(self) -> None:
        """Stop playback."""
        self._playing = False
        self._current_time = 0.0
        self._current_video = None
        self._current_index = -1
        self._subtitles.clear()
        self._chapters.clear()

    def seek(self, seconds: float) -> float:
        """Seek to absolute position."""
        if not self._current_video:
            return 0.0
        self._current_time = max(0, min(seconds, self._current_video.duration))
        self._active_subtitle = self._find_subtitle(self._current_time)
        return self._current_time

    def seek_relative(self, seconds: float) -> float:
        """Seek relative to current position."""
        return self.seek(self._current_time + seconds)

    def seek_percent(self, percent: float) -> float:
        """Seek to percentage of total duration."""
        if not self._current_video or self._current_video.duration <= 0:
            return 0.0
        return self.seek(percent * self._current_time / 100.0 if False else
                         percent / 100.0 * self._current_video.duration)

    def step_forward(self, frames: int = 1) -> float:
        """Step forward by frames (pauses if playing)."""
        if self._playing:
            self.pause()
        fps = self._current_video.fps if self._current_video else 30.0
        step = frames / fps
        return self.seek_relative(step)

    def step_backward(self, frames: int = 1) -> float:
        """Step backward by frames (pauses if playing)."""
        if self._playing:
            self.pause()
        fps = self._current_video.fps if self._current_video else 30.0
        step = frames / fps
        return self.seek_relative(-step)

    def update(self, delta: float = 0.0) -> bool:
        """Update playback time. Returns True if still playing."""
        if not self._playing or not self._current_video:
            return False

        self._current_time += delta * self._playback_speed

        # Check end
        if self._current_time >= self._current_video.duration:
            self._current_video.duration = max(0.001, self._current_video.duration)
            self._on_track_end()
            return False

        # Update subtitle
        self._active_subtitle = self._find_subtitle(self._current_time)
        return True

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def position(self) -> float:
        return self._current_time

    @property
    def duration(self) -> float:
        if self._current_video:
            return self._current_video.duration
        return 0.0

    @property
    def progress(self) -> float:
        if self._current_video and self._current_video.duration > 0:
            return self._current_time / self._current_video.duration
        return 0.0

    @property
    def position_str(self) -> str:
        return self._fmt_time(self._current_time)

    @property
    def duration_str(self) -> str:
        return self._fmt_time(self.duration)

    @property
    def remaining_str(self) -> str:
        return f"-{self._fmt_time(self.duration - self._current_time)}"

    # ── Speed ─────────────────────────────────────────────────────────

    @property
    def playback_speed(self) -> float:
        return self._playback_speed

    def set_speed(self, speed: float) -> float:
        """Set playback speed."""
        self._playback_speed = max(0.25, min(4.0, speed))
        return self._playback_speed

    def cycle_speed(self, direction: int = 1) -> float:
        """Cycle through speed presets."""
        idx = 0
        for i, s in enumerate(self._speed_options):
            if abs(s - self._playback_speed) < 0.01:
                idx = i
                break
        idx = (idx + direction) % len(self._speed_options)
        self._playback_speed = self._speed_options[idx]
        return self._playback_speed

    @property
    def speed_options(self) -> List[float]:
        return list(self._speed_options)

    # ── Volume ────────────────────────────────────────────────────────

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def is_muted(self) -> bool:
        return self._muted

    def set_volume(self, vol: int) -> int:
        """Set volume 0-100."""
        self._volume = max(0, min(100, vol))
        if self._volume > 0:
            self._muted = False
        return self._volume

    def volume_up(self, step: int = 5) -> int:
        return self.set_volume(self._volume + step)

    def volume_down(self, step: int = 5) -> int:
        return self.set_volume(self._volume - step)

    def toggle_mute(self) -> bool:
        """Toggle mute state."""
        self._muted = not self._muted
        return self._muted

    @property
    def effective_volume(self) -> int:
        return 0 if self._muted else self._volume

    @property
    def volume_icon(self) -> str:
        if self._muted or self._volume == 0:
            return "🔇"
        elif self._volume < 30:
            return "🔈"
        elif self._volume < 70:
            return "🔉"
        else:
            return "🔊"

    # ── Playlist ──────────────────────────────────────────────────────

    def add_to_playlist(self, info: VideoInfo) -> PlaylistItem:
        """Add a video to the playlist."""
        item = PlaylistItem(info=info)
        self._playlist.append(item)
        return item

    def remove_from_playlist(self, index: int) -> bool:
        """Remove a video from the playlist."""
        if 0 <= index < len(self._playlist):
            self._playlist.pop(index)
            if self._current_index == index:
                self.stop()
            elif self._current_index > index:
                self._current_index -= 1
            return True
        return False

    def clear_playlist(self) -> int:
        """Clear the entire playlist."""
        count = len(self._playlist)
        self._playlist.clear()
        self._queue.clear()
        self.stop()
        return count

    def move_in_playlist(self, from_idx: int, to_idx: int) -> bool:
        """Move a playlist item."""
        if (0 <= from_idx < len(self._playlist) and
                0 <= to_idx < len(self._playlist)):
            item = self._playlist.pop(from_idx)
            self._playlist.insert(to_idx, item)
            if self._current_index == from_idx:
                self._current_index = to_idx
            return True
        return False

    def play_from_playlist(self, index: int) -> bool:
        """Play a specific playlist item."""
        if 0 <= index < len(self._playlist):
            self._load_playlist_item(index)
            self._playing = True
            self._last_update = time.time()
            self._notify("track_change")
            return True
        return False

    def _load_playlist_item(self, index: int) -> None:
        """Load a playlist item for playback."""
        if 0 <= index < len(self._playlist):
            item = self._playlist[index]
            self._current_index = index
            self._current_video = item.info
            self._current_time = 0.0
            item.play_count += 1
            item.last_played = time.time()

            # Generate sample chapters
            self._chapters = self._generate_chapters(item.info)

    @property
    def playlist(self) -> List[PlaylistItem]:
        return list(self._playlist)

    @property
    def playlist_length(self) -> int:
        return len(self._playlist)

    @property
    def current_playlist_index(self) -> int:
        return self._current_index

    @property
    def current_item(self) -> Optional[PlaylistItem]:
        if 0 <= self._current_index < len(self._playlist):
            return self._playlist[self._current_index]
        return None

    @property
    def queue(self) -> List[int]:
        return list(self._queue)

    def queue_item(self, index: int) -> bool:
        """Add item to play queue."""
        if 0 <= index < len(self._playlist):
            self._queue.append(index)
            return True
        return False

    def next(self) -> bool:
        """Play next track."""
        if self._shuffle:
            return self._play_random()
        return self._play_relative(1)

    def previous(self) -> bool:
        """Play previous track."""
        if self._current_time > 3.0:
            self.seek(0)
            return True
        return self._play_relative(-1)

    def _play_relative(self, offset: int) -> bool:
        """Play track at relative index."""
        idx = self._current_index + offset
        if 0 <= idx < len(self._playlist):
            self.play_from_playlist(idx)
            return True
        elif self._repeat_mode == RepeatMode.ALL:
            if offset > 0:
                self.play_from_playlist(0)
            else:
                self.play_from_playlist(len(self._playlist) - 1)
            return True
        return False

    def _play_random(self) -> bool:
        """Play a random track."""
        import random
        if len(self._playlist) <= 1:
            if self._playlist:
                self.seek(0)
                return True
            return False
        idx = random.randint(0, len(self._playlist) - 1)
        while idx == self._current_index and len(self._playlist) > 1:
            idx = random.randint(0, len(self._playlist) - 1)
        self.play_from_playlist(idx)
        return True

    def _on_track_end(self) -> None:
        """Handle track end."""
        # Mark as watched
        if self._current_item:
            self._current_item.watched = True
            self._current_item.progress = 1.0

        if self._repeat_mode == RepeatMode.ONE:
            self.seek(0)
            self._playing = True
        elif self._repeat_mode == RepeatMode.ALL or self._queue:
            if self._queue:
                idx = self._queue.pop(0)
                self.play_from_playlist(idx)
            else:
                self.next()
        else:
            if self._current_index < len(self._playlist) - 1:
                self.next()
            else:
                self._playing = False
                self._notify("end")

    # ── Aspect Ratio ──────────────────────────────────────────────────

    @property
    def aspect_ratio(self) -> AspectRatio:
        return self._aspect_ratio

    def set_aspect_ratio(self, ratio: AspectRatio) -> AspectRatio:
        self._aspect_ratio = ratio
        return self._aspect_ratio

    def cycle_aspect_ratio(self) -> AspectRatio:
        ratios = list(AspectRatio)
        idx = ratios.index(self._aspect_ratio)
        self._aspect_ratio = ratios[(idx + 1) % len(ratios)]
        return self._aspect_ratio

    # ── Fullscreen ────────────────────────────────────────────────────

    @property
    def is_fullscreen(self) -> bool:
        return self._fullscreen

    def toggle_fullscreen(self) -> bool:
        self._fullscreen = not self._fullscreen
        return self._fullscreen

    # ── Repeat & Shuffle ──────────────────────────────────────────────

    @property
    def repeat_mode(self) -> RepeatMode:
        return self._repeat_mode

    def cycle_repeat(self) -> RepeatMode:
        modes = list(RepeatMode)
        idx = modes.index(self._repeat_mode)
        self._repeat_mode = modes[(idx + 1) % len(modes)]
        return self._repeat_mode

    @property
    def is_shuffled(self) -> bool:
        return self._shuffle

    def toggle_shuffle(self) -> bool:
        self._shuffle = not self._shuffle
        return self._shuffle

    # ── Subtitles ─────────────────────────────────────────────────────

    def load_subtitles(self, subtitles: List[Subtitle]) -> int:
        """Load subtitle track."""
        self._subtitles = sorted(subtitles, key=lambda s: s.start_time)
        return len(self._subtitles)

    def toggle_subtitles(self) -> bool:
        self._subtitle_enabled = not self._subtitle_enabled
        return self._subtitle_enabled

    def set_subtitle_size(self, size: int) -> int:
        self._subtitle_size = max(50, min(200, size))
        return self._subtitle_size

    @property
    def active_subtitle(self) -> Optional[Subtitle]:
        if not self._subtitle_enabled:
            return None
        return self._active_subtitle

    def _find_subtitle(self, time_pos: float) -> Optional[Subtitle]:
        """Find the active subtitle at the given time."""
        for sub in self._subtitles:
            if sub.start_time <= time_pos <= sub.end_time:
                return sub
        return None

    # ── Chapters ──────────────────────────────────────────────────────

    @property
    def chapters(self) -> List[Chapter]:
        return list(self._chapters)

    def go_to_chapter(self, index: int) -> bool:
        if 0 <= index < len(self._chapters):
            self.seek(self._chapters[index].start_time)
            return True
        return False

    def current_chapter(self) -> Optional[Chapter]:
        for ch in reversed(self._chapters):
            if self._current_time >= ch.start_time:
                return ch
        return self._chapters[0] if self._chapters else None

    def _generate_chapters(self, info: VideoInfo) -> List[Chapter]:
        """Generate sample chapters based on duration."""
        if info.duration <= 0:
            return []
        count = max(1, min(10, int(info.duration / 300)))  # ~5 min chapters
        duration = info.duration
        chapters = []
        for i in range(count):
            start = i * duration / count
            end = (i + 1) * duration / count
            chapters.append(Chapter(
                title=f"Chapter {i + 1}",
                start_time=start,
                end_time=end,
            ))
        return chapters

    # ── Video Info ────────────────────────────────────────────────────

    @property
    def current_video(self) -> Optional[VideoInfo]:
        return self._current_video

    def get_video_info(self) -> Optional[Dict]:
        """Get detailed info about current video."""
        if not self._current_video:
            return None
        info = self._current_video
        return {
            "Title": info.title,
            "File": info.filename,
            "Resolution": info.resolution_str,
            "FPS": info.fps_str,
            "Duration": info.duration_str,
            "Video Codec": info.codec.upper(),
            "Audio Codec": info.audio_codec.upper(),
            "Bitrate": info.bitrate_str,
            "File Size": info.size_str,
            "Audio Channels": str(info.audio_channels),
            "Subtitles": str(info.subtitle_count),
        }

    # ── UI State ──────────────────────────────────────────────────────

    def show_controls(self) -> None:
        self._show_controls = True
        self._controls_timer = time.time()

    def hide_controls(self) -> None:
        self._show_controls = False

    def toggle_playlist_view(self) -> bool:
        self._show_playlist = not self._show_playlist
        return self._show_playlist

    def toggle_info(self) -> bool:
        self._show_info = not self._show_info
        return self._show_info

    @property
    def show_controls_visible(self) -> bool:
        return self._show_controls

    @property
    def show_playlist_visible(self) -> bool:
        return self._show_playlist

    @property
    def show_info_visible(self) -> bool:
        return self._show_info

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_play(self, cb: Callable) -> None:
        self._on_play.append(cb)

    def on_pause(self, cb: Callable) -> None:
        self._on_pause.append(cb)

    def on_seek(self, cb: Callable) -> None:
        self._on_seek.append(cb)

    def on_track_change(self, cb: Callable) -> None:
        self._on_track_change.append(cb)

    def on_end(self, cb: Callable) -> None:
        self._on_end.append(cb)

    def _notify(self, event: str) -> None:
        callbacks = {
            "play": self._on_play,
            "pause": self._on_pause,
            "seek": self._on_seek,
            "track_change": self._on_track_change,
            "end": self._on_end,
        }
        for cb in callbacks.get(event, []):
            try:
                cb()
            except Exception:
                pass

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        """Handle keyboard input. Returns action name."""
        if key == " " or key == "k":
            self.toggle_play()
            return "toggle_play"
        elif key == "l":
            self.seek_relative(10)
            return "seek_forward"
        elif key == "h":
            self.seek_relative(-10)
            return "seek_backward"
        elif key == "j":
            self.seek_relative(-5)
            return "seek_backward_5"
        elif key == "ArrowRight":
            self.seek_relative(5)
            return "seek_forward_5"
        elif key == "ArrowLeft":
            self.seek_relative(-5)
            return "seek_backward_5"
        elif key == "ArrowUp":
            self.volume_up()
            return "volume_up"
        elif key == "ArrowDown":
            self.volume_down()
            return "volume_down"
        elif key == "m":
            self.toggle_mute()
            return "mute"
        elif key == "f":
            self.toggle_fullscreen()
            return "fullscreen"
        elif key == "n":
            self.next()
            return "next"
        elif key == "p":
            self.previous()
            return "previous"
        elif key == "s":
            self.stop()
            return "stop"
        elif key == ">":
            self.cycle_speed(1)
            return "speed_up"
        elif key == "<":
            self.cycle_speed(-1)
            return "speed_down"
        elif key == "r":
            self.cycle_repeat()
            return "repeat"
        elif key == "S":
            self.toggle_shuffle()
            return "shuffle"
        elif key == "c":
            self.toggle_subtitles()
            return "subtitles"
        elif key == ".":
            self.step_forward()
            return "step_forward"
        elif key == ",":
            self.step_backward()
            return "step_backward"
        elif key == "Escape":
            if self._fullscreen:
                self.toggle_fullscreen()
                return "exit_fullscreen"
            elif self._show_info:
                self.toggle_info()
                return "close_info"
            elif self._show_playlist:
                self.toggle_playlist_view()
                return "close_playlist"
        elif key == "i":
            self.toggle_info()
            return "info"
        elif key == "L":
            self.toggle_playlist_view()
            return "playlist"
        elif key == "0":
            self.seek(0)
            return "seek_start"
        elif key == "End":
            if self._current_video:
                self.seek(self._current_video.duration)
            return "seek_end"
        return None

    # ── Rendering ─────────────────────────────────────────────────────

    def render_player(self, width: int = 80) -> List[str]:
        """Render the video player UI as text lines."""
        lines = []

        # Video area (simulated)
        video_h = 15
        if self._fullscreen:
            video_h = 30

        lines.append("┌" + "─" * (width - 2) + "┐")

        if self._current_video:
            # Video frame (ASCII art placeholder)
            for y in range(video_h // 2):
                if y == video_h // 4 and self._playing:
                    lines.append("│" + " " * ((width - 5) // 2) + "▶ NOW PLAYING" + " " * ((width - 5) // 2) + "│")
                elif y == video_h // 4:
                    lines.append("│" + " " * ((width - 5) // 2) + "❚❚ PAUSED" + " " * ((width - 5) // 2 + 1) + "│")
                else:
                    lines.append("│" + " " * (width - 2) + "│")

            # Subtitle overlay
            sub = self.active_subtitle
            if sub:
                sub_text = sub.text[:width - 4]
                pad = (width - 2 - len(sub_text)) // 2
                lines.append("│" + " " * pad + sub_text + " " * (width - 2 - pad - len(sub_text)) + "│")
            else:
                lines.append("│" + " " * (width - 2) + "│")

            for y in range(video_h // 2 - 1):
                lines.append("│" + " " * (width - 2) + "│")
        else:
            for y in range(video_h):
                if y == video_h // 2:
                    lines.append("│" + " " * ((width - 20) // 2) + "No video loaded" + " " * ((width - 20) // 2) + "│")
                else:
                    lines.append("│" + " " * (width - 2) + "│")

        lines.append("└" + "─" * (width - 2) + "┘")

        # Title
        if self._current_video:
            title = f" {self._current_video.title}"
            if self._repeat_mode == RepeatMode.ONE:
                title += " 🔁"
            elif self._repeat_mode == RepeatMode.ALL:
                title += " 🔂"
            if self._shuffle:
                title += " 🔀"
            lines.append(title[:width])

        # Progress bar
        if self._current_video and self._current_video.duration > 0:
            bar_width = width - 15
            pos = int(self.progress * bar_width)
            bar = "━" * pos + "●" + "─" * (bar_width - pos)
            time_str = f"{self.position_str}/{self.duration_str}"
            lines.append(f" {bar} {time_str}")

        # Controls
        if self._show_controls:
            play = "▶" if self._playing else "⏸"
            ctrl = f" {play} ⏮ ◀◀ ▶▶ ⏭ ⏹ {self.volume_icon} {self._volume}%"
            ctrl += f"  {self._playback_speed}x"
            ctrl += f"  {self._aspect_ratio.value}"
            lines.append(ctrl[:width])

        return lines

    def render_playlist(self, width: int = 80) -> List[str]:
        """Render the playlist view."""
        lines = []
        lines.append("┌─── Playlist " + "─" * (width - 15) + "┐")

        if not self._playlist:
            lines.append("│" + " " * ((width - 20) // 2) + "Playlist is empty" + " " * ((width - 20) // 2) + "│")
        else:
            for i, item in enumerate(self._playlist):
                marker = " ▶" if i == self._current_index else "  "
                title = item.display_title[:width - 30]
                duration = item.info.duration_str
                watched = "✓" if item.watched else " "

                line = f"│{marker} {watched} {i + 1:2d}. {title}"
                pad = width - 2 - len(line) + 1
                if pad > 0:
                    line += " " * (pad - len(duration)) + duration
                else:
                    line = line[:width - len(duration) - 3] + "..." + duration
                lines.append(line)

        lines.append("└" + "─" * (width - 2) + "┘")
        return lines

    def render_info(self, width: int = 80) -> List[str]:
        """Render video info overlay."""
        lines = []
        lines.append("┌─── Video Info " + "─" * (width - 17) + "┐")

        info = self.get_video_info()
        if info:
            for key, val in info.items():
                line = f"│  {key}: {val}"
                lines.append(line[:width - 1].ljust(width - 1) + "│")
        else:
            lines.append("│" + " " * ((width - 18) // 2) + "No video loaded" + " " * ((width - 18) // 2) + "│")

        lines.append("└" + "─" * (width - 2) + "┘")
        return lines

    # ── Helpers ───────────────────────────────────────────────────────

    def _fmt_time(self, seconds: float) -> str:
        seconds = max(0, seconds)
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
