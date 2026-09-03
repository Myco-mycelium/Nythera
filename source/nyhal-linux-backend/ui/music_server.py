"""Music Streaming Server — playlist management, equalizer, lyrics display for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import random


class RepeatMode(Enum):
    OFF = "Off"
    ALL = "All"
    ONE = "One"


class AudioQuality(Enum):
    LOW = "Low (128kbps)"
    MEDIUM = "Medium (256kbps)"
    HIGH = "High (320kbps)"
    LOSSLESS = "Lossless (FLAC)"
    HI_RES = "Hi-Res (24bit/96kHz)"


class Genre(Enum):
    ROCK = "Rock"
    POP = "Pop"
    ELECTRONIC = "Electronic"
    JAZZ = "Jazz"
    CLASSICAL = "Classical"
    HIPHOP = "Hip-Hop"
    RNB = "R&B"
    COUNTRY = "Country"
    METAL = "Metal"
    AMBIENT = "Ambient"
    FOLK = "Folk"
    LATIN = "Latin"


@dataclass
class AudioEQBand:
    freq: str = "60Hz"
    gain: float = 0.0  # -12 to +12 dB

    @property
    def bar(self) -> str:
        normalized = int((self.gain + 12) / 24 * 10)
        return "█" * normalized + "░" * (10 - normalized)

    @property
    def gain_str(self) -> str:
        return f"{self.gain:+.1f}dB"


@dataclass
class Track:
    id: int
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_s: float = 0.0
    genre: Genre = Genre.ROCK
    year: int = 2024
    bpm: int = 120
    bitrate: int = 320
    sample_rate: int = 44100
    album_art_url: str = ""
    liked: bool = False
    play_count: int = 0
    last_played: float = 0.0

    @property
    def duration_str(self) -> str:
        m = int(self.duration_s // 60)
        s = int(self.duration_s % 60)
        return f"{m}:{s:02d}"

    @property
    def artist_title(self) -> str:
        return f"{self.artist} — {self.title}"

    @property
    def like_icon(self) -> str:
        return "❤️" if self.liked else "🤍"


@dataclass
class LyricLine:
    timestamp_s: float = 0.0
    text: str = ""


@dataclass
class Lyrics:
    track_id: int = 0
    lines: List[LyricLine] = field(default_factory=list)
    source: str = "LRC"
    language: str = "en"
    synced: bool = True

    def get_current_line(self, position_s: float) -> str:
        current = ""
        for line in self.lines:
            if line.timestamp_s <= position_s:
                current = line.text
            else:
                break
        return current


@dataclass
class Playlist:
    id: int
    name: str = ""
    tracks: List[Track] = field(default_factory=list)
    description: str = ""
    owner: str = ""
    public: bool = True
    created_at: float = 0.0
    image_url: str = ""
    followers: int = 0

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def total_duration(self) -> str:
        total = sum(t.duration_s for t in self.tracks)
        if total < 3600:
            m = int(total // 60)
            return f"{m} min"
        h = int(total // 3600)
        m = int((total % 3600) // 60)
        return f"{h}h {m}m"


@dataclass
class QueueItem:
    track: Track
    added_by: str = ""
    position: int = 0


class MusicServer:
    def __init__(self):
        self._library: List[Track] = []
        self._playlists: List[Playlist] = []
        self._queue: List[QueueItem] = []
        self._current_track: Optional[Track] = None
        self._position_s: float = 0.0
        self._playing: bool = False
        self._volume: float = 0.75
        self._shuffle: bool = False
        self._repeat: RepeatMode = RepeatMode.OFF
        self._crossfade: bool = True
        self._gapless: bool = True
        self._eq_bands: List[AudioEQBand] = []
        self._eq_preset: str = "Flat"
        self._quality: AudioQuality = AudioQuality.HIGH
        self._lyrics: Optional[Lyrics] = None
        self._selected_playlist: int = 0
        self._selected_track: int = 0
        self._view_mode: str = "now_playing"
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        self._library = [
            Track(1, "Midnight City", "M83", "Hurry Up, We're Dreaming", 243, Genre.ELECTRONIC, 2011, 105, 320, play_count=156),
            Track(2, "Everything In Its Right Place", "Radiohead", "Kid A", 251, Genre.ROCK, 2000, 128, 320, play_count=89),
            Track(3, "Blue in Green", "Miles Davis", "Kind of Blue", 327, Genre.JAZZ, 1959, 72, 320, play_count=203),
            Track(4, "Clair de Lune", "Debussy", "Suite Bergamasque", 302, Genre.CLASSICAL, 1905, 60, 320, play_count=312, liked=True),
            Track(5, "Blinding Lights", "The Weeknd", "After Hours", 200, Genre.POP, 2020, 171, 320, play_count=245, liked=True),
            Track(6, "Bohemian Rhapsody", "Queen", "A Night at the Opera", 354, Genre.ROCK, 1975, 72, 320, play_count=567, liked=True),
            Track(7, "Strobe", "Deadmau5", "For Lack of a Better Name", 627, Genre.ELECTRONIC, 2009, 128, 320, play_count=98),
            Track(8, "So What", "Miles Davis", "Kind of Love", 562, Genre.JAZZ, 1959, 136, 320, play_count=178),
            Track(9, "Take Five", "Dave Brubeck", "Time Out", 324, Genre.JAZZ, 1959, 173, 320, play_count=134),
            Track(10, "Get Lucky", "Daft Punk", "Random Access Memories", 369, Genre.ELECTRONIC, 2013, 116, 320, play_count=412, liked=True),
            Track(11, "Lose Yourself to Dance", "Daft Punk", "Random Access Memories", 353, Genre.ELECTRONIC, 2013, 100, 320, play_count=289),
            Track(12, "Intro", "The xx", "xx", 128, Genre.ROCK, 2009, 110, 320, play_count=167),
            Track(13, "Weightless", "Marconi Union", "Weightless", 480, Genre.AMBIENT, 2011, 60, 320, play_count=89),
            Track(14, "Nuvole Bianche", "Ludovico Einaudi", "Una Mattina", 341, Genre.CLASSICAL, 2004, 76, 320, play_count=234, liked=True),
            Track(15, "Redbone", "Childish Gambino", "Awaken, My Love!", 327, Genre.RNB, 2016, 82, 320, play_count=198),
        ]

        self._playlists = [
            Playlist(1, "Chill Vibes", [self._library[2], self._library[3], self._library[12], self._library[13], self._library[1]],
                     "Relaxing tunes for focus", "Nyrqis", True, now - 86400 * 30, followers=42),
            Playlist(2, "Electronic Essentials", [self._library[0], self._library[6], self._library[9], self._library[10]],
                     "Best electronic tracks", "Nyrqis", True, now - 86400 * 60, followers=128),
            Playlist(3, "Jazz Classics", [self._library[2], self._library[7], self._library[8]],
                     "Timeless jazz standards", "Nyrqis", True, now - 86400 * 90, followers=67),
            Playlist(4, "Liked Songs", [t for t in self._library if t.liked],
                     "", "You", False, now - 86400 * 120),
            Playlist(5, "Party Mix", [self._library[4], self._library[5], self._library[9], self._library[14]],
                     "Get the party started", "Nyrqis", True, now - 86400 * 15, followers=89),
        ]

        self._queue = [
            QueueItem(self._library[0]),
            QueueItem(self._library[9]),
            QueueItem(self._library[4]),
        ]

        self._current_track = self._library[5]  # Bohemian Rhapsody
        self._position_s = 45.0
        self._playing = True

        self._eq_bands = [
            AudioEQBand("32Hz", 2.0), AudioEQBand("64Hz", 1.5),
            AudioEQBand("125Hz", 0.5), AudioEQBand("250Hz", 0.0),
            AudioEQBand("500Hz", -0.5), AudioEQBand("1kHz", 0.0),
            AudioEQBand("2kHz", 1.0), AudioEQBand("4kHz", 1.5),
            AudioEQBand("8kHz", 0.5), AudioEQBand("16kHz", -1.0),
        ]

        self._lyrics = Lyrics(5, [
            LyricLine(0.0, "Is this the real life?"),
            LyricLine(4.0, "Is this just fantasy?"),
            LyricLine(8.0, "Caught in a landslide"),
            LyricLine(12.0, "No escape from reality"),
            LyricLine(16.0, "Open your eyes"),
            LyricLine(20.0, "Look up to the skies and see"),
            LyricLine(24.0, "I'm just a poor boy"),
            LyricLine(28.0, "I need no sympathy"),
            LyricLine(32.0, "Because I'm easy come, easy go"),
            LyricLine(37.0, "Little high, little low"),
            LyricLine(42.0, "Any way the wind blows"),
            LyricLine(47.0, "Doesn't really matter to me"),
            LyricLine(52.0, "To me"),
        ])

    @property
    def current_track(self) -> Optional[Track]:
        return self._current_track

    @property
    def position_str(self) -> str:
        m = int(self._position_s // 60)
        s = int(self._position_s % 60)
        return f"{m}:{s:02d}"

    @property
    def progress_bar(self) -> str:
        if not self._current_track:
            return "░" * 30
        progress = self._position_s / self._current_track.duration_s
        filled = int(progress * 30)
        return "█" * filled + "░" * (30 - filled)

    @property
    def volume_bar(self) -> str:
        filled = int(self._volume * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def total_tracks(self) -> int:
        return len(self._library)

    def toggle_play(self):
        self._playing = not self._playing

    def next_track(self):
        if self._queue:
            item = self._queue.pop(0)
            self._current_track = item.track
            self._position_s = 0
            item.track.play_count += 1

    def prev_track(self):
        self._position_s = 0

    def set_volume(self, vol: float):
        self._volume = max(0.0, min(1.0, vol))

    def handle_input(self, key: str):
        key = key.lower()
        if key == " ":
            self.toggle_play()
        if key == "n":
            self.next_track()
        if key == "p":
            self.prev_track()
        if key == "s":
            self._shuffle = not self._shuffle
        if key == "r":
            modes = list(RepeatMode)
            idx = (modes.index(self._repeat) + 1) % len(modes)
            self._repeat = modes[idx]

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS MUSIC SERVER                                       ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Now playing
        t = self._current_track
        if t:
            play = "▶" if self._playing else "⏸"
            shuffle = "🔀" if self._shuffle else "➡️"
            repeat = {"Off": "➡️", "All": "🔁", "One": "🔂"}[self._repeat.value]
            lines.append(f"  {play} Now Playing: {t.artist_title}  {t.album} ({t.year})")
            lines.append(f"  [{self.progress_bar}] {self.position_str} / {t.duration_str}  Vol: [{self.volume_bar}]  {shuffle} {repeat}")
            lines.append(f"  Genre: {t.genre.value}  BPM: {t.bpm}  Quality: {self._quality.value}  Plays: {t.play_count}  {t.like_icon}")
            lines.append("")

        # Lyrics
        if self._lyrics and self._lyrics.synced:
            current = self._lyrics.get_current_line(self._position_s)
            lines.append("  ── Lyrics ──")
            for ll in self._lyrics.lines:
                marker = "  ▶ " if ll.text == current else "    "
                text = ll.text[:60]
                lines.append(f"  {marker}{text}")
            lines.append("")

        # Queue
        if self._queue:
            lines.append(f"  ── Queue ({len(self._queue)} tracks) ──")
            for i, qi in enumerate(self._queue[:5]):
                lines.append(f"  {i+1}. {qi.track.artist_title}  {qi.track.duration_str}")
            lines.append("")

        # Library
        lines.append(f"  ── Library ({self.total_tracks} tracks) ──")
        for i, track in enumerate(self._library[:8]):
            sel = "▶" if track.id == (t.id if t else -1) else " "
            lines.append(f"  {sel} {track.like_icon} {track.artist:<20s} {track.title:<30s} {track.duration_str}  {track.genre.value}")
        lines.append("")

        # Playlists
        lines.append(f"  ── Playlists ──")
        for pl in self._playlists:
            lines.append(f"  📁 {pl.name}  {pl.track_count} tracks  {pl.total_duration}  ❤️ {pl.followers}")
        lines.append("")

        # EQ
        lines.append(f"  ── Equalizer ({self._eq_preset}) ──")
        eq_line = "  ".join(f"{b.freq[:4]}:[{b.bar}]" for b in self._eq_bands[:5])
        lines.append(f"  {eq_line}")
        eq_line2 = "  ".join(f"{b.freq[:4]}:[{b.bar}]" for b in self._eq_bands[5:])
        lines.append(f"  {eq_line2}")
        lines.append("")

        lines.append("  [Space]Play/Pause [N]Next [P]Prev [S]Shuffle [R]Repeat [↑↓]Library")
        lines.append("  [L]Lyrics [E]Equalizer [Q]Queue [P]Playlists [+/-]Volume")
        return lines
