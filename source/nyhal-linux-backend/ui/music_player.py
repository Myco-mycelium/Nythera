"""MusicPlayer — Music playback UI for Nyrqis.

Provides a complete music player with:
- Playlist management
- Playback controls (play/pause, prev, next, seek)
- Volume control with mute
- Repeat modes (off, all, one)
- Shuffle toggle
- Now playing view with progress bar
- Equalizer with presets
- Album art placeholder
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RepeatMode(Enum):
    OFF = auto()
    ALL = auto()
    ONE = auto()


class PlaybackState(Enum):
    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()


class EQPreset(Enum):
    FLAT = auto()
    ROCK = auto()
    POP = auto()
    JAZZ = auto()
    CLASSICAL = auto()
    ELECTRONIC = auto()
    BASS_BOOST = auto()
    TREBLE_BOOST = auto()
    VOCAL = auto()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Track:
    """A music track."""
    id: str
    title: str
    artist: str
    album: str = ""
    duration: float = 0.0       # seconds
    path: str = ""
    album_color: Tuple[int, int, int] = (80, 140, 255)
    track_number: int = 0
    year: int = 0
    genre: str = ""

    @property
    def display_duration(self) -> str:
        m = int(self.duration // 60)
        s = int(self.duration % 60)
        return f"{m}:{s:02d}"

    @property
    def display_track_number(self) -> str:
        return f"{self.track_number:02d}" if self.track_number > 0 else ""


@dataclass
class Playlist:
    """A playlist of tracks."""
    id: str
    name: str
    tracks: List[Track] = field(default_factory=list)
    color: Tuple[int, int, int] = (80, 140, 255)

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def total_duration(self) -> float:
        return sum(t.duration for t in self.tracks)

    @property
    def display_duration(self) -> str:
        total = self.total_duration
        h = int(total // 3600)
        m = int((total % 3600) // 60)
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"


@dataclass
class EQBand:
    """An equalizer band."""
    name: str
    frequency: str = ""
    gain: float = 0.0    # -12 to +12 dB


# ---------------------------------------------------------------------------
# Default playlist
# ---------------------------------------------------------------------------

def _default_playlist() -> Playlist:
    tracks = [
        Track("t1", "Midnight City", "M83", "Hurry Up, We're Dreaming",
              243, album_color=(120, 80, 200), track_number=1, year=2011),
        Track("t2", "Digital Love", "Daft Punk", "Discovery",
              301, album_color=(80, 80, 200), track_number=2, year=2001),
        Track("t3", "Genesis", "Grimes", "Visions",
              252, album_color=(200, 100, 180), track_number=3, year=2012),
        Track("t4", "Electric Feel", "MGMT", "Oracular Spectacular",
              227, album_color=(80, 200, 120), track_number=4, year=2007),
        Track("t5", "Crystalised", "The xx", "xx",
              195, album_color=(180, 180, 200), track_number=5, year=2009),
        Track("t6", "Hyperballad", "Björk", "Post",
              325, album_color=(200, 60, 60), track_number=6, year=1995),
        Track("t7", "Everything In Its Right Place", "Radiohead", "Kid A",
              251, album_color=(60, 120, 200), track_number=1, year=2000),
        Track("t8", "Teardrop", "Massive Attack", "Mezzanine",
              328, album_color=(40, 40, 60), track_number=4, year=1998),
    ]
    return Playlist("pl-1", "Nyrqis Favorites", tracks, (80, 140, 255))


# ---------------------------------------------------------------------------
# Equalizer presets
# ---------------------------------------------------------------------------

EQ_PRESETS = {
    EQPreset.FLAT: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    EQPreset.ROCK: [5, 4, 3, 1, -1, -1, 0, 2, 3, 4],
    EQPreset.POP: [-1, 2, 4, 5, 3, 0, -1, -1, 2, 3],
    EQPreset.JAZZ: [4, 3, 1, 2, -2, -2, 0, 1, 3, 4],
    EQPreset.CLASSICAL: [5, 4, 3, 2, -1, -1, 0, 2, 3, 5],
    EQPreset.ELECTRONIC: [6, 5, 2, 0, -2, -1, 1, 3, 5, 6],
    EQPreset.BASS_BOOST: [8, 6, 4, 1, 0, 0, 0, 0, 0, 0],
    EQPreset.TREBLE_BOOST: [0, 0, 0, 0, 0, 1, 3, 5, 7, 8],
    EQPreset.VOCAL: [-2, -1, 2, 5, 5, 4, 2, 0, -1, -2],
}

EQ_FREQUENCIES = ["31", "62", "125", "250", "500", "1K", "2K", "4K", "8K", "16K"]


# ---------------------------------------------------------------------------
# MusicPlayer
# ---------------------------------------------------------------------------

class MusicPlayer:
    """Music playback UI for Nyrqis.

    Parameters
    ----------
    width, height : int
        Rendering dimensions.
    """

    def __init__(self, width: int = 400, height: int = 600):
        self.width = width
        self.height = height

        # Playback state
        self._state = PlaybackState.STOPPED
        self._current_track: Optional[Track] = None
        self._current_time: float = 0.0
        self._volume: int = 80
        self._muted: bool = False
        self._repeat = RepeatMode.OFF
        self._shuffle: bool = False

        # Playlist
        self._playlists: List[Playlist] = [_default_playlist()]
        self._current_playlist_idx: int = 0
        self._track_index: int = 0

        # Queue
        self._queue: List[Track] = []

        # Equalizer
        self._eq_enabled: bool = False
        self._eq_preset: EQPreset = EQPreset.FLAT
        self._eq_bands: List[EQBand] = [
            EQBand(f"Band {i}", freq, 0.0)
            for i, freq in enumerate(EQ_FREQUENCIES)
        ]

        # UI state
        self._tab: str = "now_playing"  # now_playing, playlist, equalizer
        self._visible: bool = False

    # -- Playback --------------------------------------------------------

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def current_track(self) -> Optional[Track]:
        return self._current_track

    @property
    def current_time(self) -> float:
        return self._current_time

    @property
    def progress(self) -> float:
        if self._current_track and self._current_track.duration > 0:
            return self._current_time / self._current_track.duration
        return 0.0

    def play(self) -> None:
        if self._current_track is None:
            playlist = self.current_playlist
            if playlist and playlist.tracks:
                self._current_track = playlist.tracks[0]
                self._track_index = 0
        self._state = PlaybackState.PLAYING

    def pause(self) -> None:
        if self._state == PlaybackState.PLAYING:
            self._state = PlaybackState.PAUSED
        elif self._state == PlaybackState.PAUSED:
            self._state = PlaybackState.PLAYING

    def stop(self) -> None:
        self._state = PlaybackState.STOPPED
        self._current_time = 0.0

    def next_track(self) -> Optional[Track]:
        playlist = self.current_playlist
        if not playlist or not playlist.tracks:
            return None
        if self._shuffle:
            import random
            self._track_index = random.randint(0, len(playlist.tracks) - 1)
        else:
            self._track_index = (self._track_index + 1) % len(playlist.tracks)
        self._current_track = playlist.tracks[self._track_index]
        self._current_time = 0.0
        self._state = PlaybackState.PLAYING
        return self._current_track

    def prev_track(self) -> Optional[Track]:
        if self._current_time > 3:
            self._current_time = 0.0
            return self._current_track
        playlist = self.current_playlist
        if not playlist or not playlist.tracks:
            return None
        self._track_index = (self._track_index - 1) % len(playlist.tracks)
        self._current_track = playlist.tracks[self._track_index]
        self._current_time = 0.0
        self._state = PlaybackState.PLAYING
        return self._current_track

    def seek(self, time_sec: float) -> None:
        if self._current_track:
            self._current_time = max(0, min(self._current_track.duration, time_sec))

    def seek_relative(self, delta: float) -> None:
        if self._current_track:
            self.seek(self._current_time + delta)

    def tick(self, elapsed: float = 0.1) -> None:
        """Advance playback by elapsed seconds."""
        if self._state == PlaybackState.PLAYING and self._current_track:
            self._current_time += elapsed
            if self._current_time >= self._current_track.duration:
                self._on_track_end()

    def _on_track_end(self) -> None:
        if self._repeat == RepeatMode.ONE:
            self._current_time = 0.0
        elif self._repeat == RepeatMode.ALL or self._track_index < len(self.current_playlist.tracks) - 1:
            self.next_track()
        else:
            self.stop()

    # -- Volume ----------------------------------------------------------

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    def set_volume(self, vol: int) -> None:
        self._volume = max(0, min(100, vol))

    def toggle_mute(self) -> bool:
        self._muted = not self._muted
        return self._muted

    # -- Repeat / Shuffle ------------------------------------------------

    def toggle_repeat(self) -> RepeatMode:
        modes = list(RepeatMode)
        idx = modes.index(self._repeat)
        self._repeat = modes[(idx + 1) % len(modes)]
        return self._repeat

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    def toggle_shuffle(self) -> bool:
        self._shuffle = not self._shuffle
        return self._shuffle

    # -- Playlists -------------------------------------------------------

    @property
    def playlists(self) -> List[Playlist]:
        return list(self._playlists)

    @property
    def current_playlist(self) -> Optional[Playlist]:
        if 0 <= self._current_playlist_idx < len(self._playlists):
            return self._playlists[self._current_playlist_idx]
        return None

    def create_playlist(self, name: str, color: Tuple[int, int, int] = (80, 140, 255)) -> Playlist:
        pl = Playlist(f"pl-{len(self._playlists) + 1}", name, color=color)
        self._playlists.append(pl)
        return pl

    def add_to_playlist(self, playlist_idx: int, track: Track) -> bool:
        if 0 <= playlist_idx < len(self._playlists):
            self._playlists[playlist_idx].tracks.append(track)
            return True
        return False

    def remove_from_playlist(self, playlist_idx: int, track_idx: int) -> bool:
        if 0 <= playlist_idx < len(self._playlists):
            pl = self._playlists[playlist_idx]
            if 0 <= track_idx < len(pl.tracks):
                pl.tracks.pop(track_idx)
                return True
        return False

    def play_playlist(self, index: int, track_index: int = 0) -> bool:
        if 0 <= index < len(self._playlists):
            self._current_playlist_idx = index
            pl = self._playlists[index]
            if pl.tracks:
                self._track_index = track_index
                self._current_track = pl.tracks[track_index]
                self._current_time = 0.0
                self._state = PlaybackState.PLAYING
                return True
        return False

    # -- Equalizer -------------------------------------------------------

    @property
    def eq_enabled(self) -> bool:
        return self._eq_enabled

    @property
    def eq_preset(self) -> EQPreset:
        return self._eq_preset

    @property
    def eq_bands(self) -> List[EQBand]:
        return list(self._eq_bands)

    def toggle_eq(self) -> bool:
        self._eq_enabled = not self._eq_enabled
        return self._eq_enabled

    def set_eq_preset(self, preset: EQPreset) -> bool:
        if preset in EQ_PRESETS:
            self._eq_preset = preset
            gains = EQ_PRESETS[preset]
            for i, band in enumerate(self._eq_bands):
                band.gain = gains[i] if i < len(gains) else 0.0
            return True
        return False

    def set_eq_band(self, index: int, gain: float) -> bool:
        if 0 <= index < len(self._eq_bands):
            self._eq_bands[index].gain = max(-12, min(12, gain))
            self._eq_preset = EQPreset.FLAT  # custom = flat preset
            return True
        return False

    # -- Navigation ------------------------------------------------------

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def toggle_visibility(self) -> bool:
        self._visible = not self._visible
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Stats -----------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "state": self._state.name,
            "track": self._current_track.title if self._current_track else None,
            "playlist": self.current_playlist.name if self.current_playlist else None,
            "volume": self._volume,
            "repeat": self._repeat.name,
            "shuffle": self._shuffle,
        }

    # -- Rendering -------------------------------------------------------

    def render(self) -> Tuple[bytes, int, int]:
        """Render the music player UI."""
        w, h = self.width, self.height
        buf = bytearray(w * h * 3)
        bg = (30, 30, 40)
        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        # Header
        self._fill_rect(buf, w, 0, 0, w, 48, (42, 42, 56))

        if self._current_track:
            # Album art placeholder
            art_size = 200
            art_x = (w - art_size) // 2
            art_y = 60
            self._fill_rect(buf, w, art_x, art_y, art_size, art_size,
                           self._current_track.album_color)

            # Track info
            info_y = art_y + art_size + 20
            self._fill_rect(buf, w, 40, info_y, 160, 16, (230, 230, 240))
            self._fill_rect(buf, w, 40, info_y + 22, 120, 12, (150, 150, 170))

            # Progress bar
            bar_y = info_y + 50
            self._fill_rect(buf, w, 40, bar_y, w - 80, 6, (50, 50, 65))
            fill_w = int((w - 80) * self.progress)
            self._fill_rect(buf, w, 40, bar_y, fill_w, 6, (80, 140, 255))

            # Time labels
            self._fill_rect(buf, w, 40, bar_y + 12, 40, 10, (120, 120, 140))
            self._fill_rect(buf, w, w - 80, bar_y + 12, 40, 10, (120, 120, 140))

            # Controls placeholder
            ctrl_y = bar_y + 30
            btn_size = 20
            btns = [(-60, (150, 150, 170)), (-20, (230, 230, 240)),
                    (20, (80, 140, 255)), (60, (150, 150, 170))]
            for offset, color in btns:
                cx = w // 2 + offset
                self._fill_rect(buf, w, cx - btn_size // 2, ctrl_y,
                               btn_size, btn_size, color)

        elif self._tab == "playlist" and self.current_playlist:
            # Playlist view
            y = 60
            for i, track in enumerate(self.current_playlist.tracks[:15]):
                is_current = (i == self._track_index and self._current_track)
                bg_row = (50, 50, 70) if is_current else (35, 35, 48)
                self._fill_rect(buf, w, 4, y, w - 8, 36, bg_row)

                # Track number
                self._fill_rect(buf, w, 12, y + 10, 20, 12, (120, 120, 140))
                # Title
                self._fill_rect(buf, w, 40, y + 6, 140, 12, (200, 200, 210))
                # Artist
                self._fill_rect(buf, w, 40, y + 20, 100, 10, (150, 150, 170))
                # Duration
                self._fill_rect(buf, w, w - 50, y + 10, 36, 10, (120, 120, 140))
                y += 40

        # Volume bar
        vol_y = h - 30
        self._fill_rect(buf, w, 12, vol_y, 16, 16, (150, 150, 170))
        self._fill_rect(buf, w, 36, vol_y + 4, w - 48, 8, (50, 50, 65))
        vol_w = int((w - 48) * self._volume / 100)
        vol_color = (80, 200, 120) if not self._muted else (80, 80, 100)
        self._fill_rect(buf, w, 36, vol_y + 4, vol_w, 8, vol_color)

        return bytes(buf), w, h

    def _fill_rect(self, buf: bytearray, buf_width: int,
                   x: int, y: int, w: int, h: int,
                   color: Tuple[int, int, int]) -> None:
        buf_height = len(buf) // (buf_width * 3)
        for dy in range(h):
            for dx in range(w):
                px, py = x + dx, y + dy
                if 0 <= px < buf_width and 0 <= py < buf_height:
                    idx = (py * buf_width + px) * 3
                    if idx + 2 < len(buf):
                        buf[idx] = color[0]
                        buf[idx + 1] = color[1]
                        buf[idx + 2] = color[2]

    # -- Serialization ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self._state.name,
            "track": self._current_track.title if self._current_track else None,
            "volume": self._volume,
            "repeat": self._repeat.name,
            "shuffle": self._shuffle,
            "playlists": len(self._playlists),
        }


__all__ = [
    "MusicPlayer", "Track", "Playlist", "PlaybackState",
    "RepeatMode", "EQPreset", "EQBand", "EQ_PRESETS",
]
