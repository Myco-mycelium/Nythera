"""ArchiveManager — Archive management for Nyrqis.

Provides archive operations with:
- Create ZIP archives
- Extract ZIP archives
- Browse archive contents
- Progress tracking
- Password-protected archives
- Compression level selection
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import os
import time
import zipfile
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ArchiveFormat(Enum):
    ZIP = auto()
    TAR = auto()
    TAR_GZ = auto()
    TAR_BZ2 = auto()


class CompressionLevel(Enum):
    STORED = 0      # No compression
    FAST = 1
    NORMAL = 6
    BEST = 9


class ArchiveState(Enum):
    IDLE = auto()
    BROWSING = auto()
    EXTRACTING = auto()
    CREATING = auto()
    COMPLETED = auto()
    FAILED = auto()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ArchiveEntry:
    """A file entry within an archive."""
    name: str
    path: str
    is_dir: bool = False
    size: int = 0           # uncompressed size
    compressed_size: int = 0
    modified: float = 0.0
    ratio: float = 0.0      # compression ratio

    @property
    def display_size(self) -> str:
        if self.is_dir:
            return "—"
        if self.size < 1024:
            return f"{self.size} B"
        if self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        return f"{self.size / (1024 * 1024):.1f} MB"

    @property
    def display_ratio(self) -> str:
        if self.ratio <= 0:
            return ""
        return f"{self.ratio:.0%}"

    @property
    def extension(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""


@dataclass
class ArchiveOperation:
    """An archive operation in progress."""
    id: str
    archive_path: str
    extracting: bool = True  # True=extracting, False=creating
    state: ArchiveState = ArchiveState.IDLE
    progress: float = 0.0
    total_files: int = 0
    processed_files: int = 0
    total_bytes: int = 0
    processed_bytes: int = 0
    errors: List[str] = field(default_factory=list)
    password: str = ""
    compression: CompressionLevel = CompressionLevel.NORMAL
    started_at: float = 0.0
    completed_at: float = 0.0

    @property
    def display_progress(self) -> str:
        return f"{self.progress * 100:.0f}%"

    @property
    def speed_bytes_per_sec(self) -> float:
        if self.started_at == 0 or self.state != ArchiveState.EXTRACTING:
            return 0
        elapsed = time.time() - self.started_at
        if elapsed <= 0:
            return 0
        return self.processed_bytes / elapsed

    @property
    def display_speed(self) -> str:
        speed = self.speed_bytes_per_sec
        if speed < 1024:
            return f"{speed:.0f} B/s"
        if speed < 1024 * 1024:
            return f"{speed / 1024:.1f} KB/s"
        return f"{speed / (1024 * 1024):.1f} MB/s"


# ---------------------------------------------------------------------------
# ArchiveManager
# ---------------------------------------------------------------------------

class ArchiveManager:
    """Archive management for Nyrqis.

    Handles ZIP archive creation, extraction, and browsing.

    Parameters
    ----------
    width, height : int
        Rendering dimensions.
    """

    def __init__(self, width: int = 480, height: int = 500):
        self.width = width
        self.height = height

        # State
        self._state = ArchiveState.IDLE
        self._current_archive: Optional[str] = None
        self._entries: List[ArchiveEntry] = []
        self._operations: List[ArchiveOperation] = []
        self._history: List[ArchiveOperation] = []

        # UI state
        self._selected_index: int = 0
        self._show_hidden: bool = False

    # -- Browsing --------------------------------------------------------

    def open_archive(self, path: str) -> bool:
        """Open and browse an archive."""
        if not os.path.exists(path) or not zipfile.is_zipfile(path):
            return False

        self._current_archive = path
        self._entries.clear()
        self._state = ArchiveState.BROWSING

        try:
            with zipfile.ZipFile(path, "r") as zf:
                for info in zf.infolist():
                    mod_time = 0.0
                    try:
                        mod_time = time.mktime(info.date_time) if info.date_time else 0.0
                    except (TypeError, ValueError, OverflowError):
                        mod_time = 0.0
                    entry = ArchiveEntry(
                        name=info.filename,
                        path=info.filename,
                        is_dir=info.is_dir(),
                        size=info.file_size,
                        compressed_size=info.compress_size,
                        modified=mod_time,
                    )
                    if entry.size > 0:
                        entry.ratio = 1 - (entry.compressed_size / entry.size)
                    self._entries.append(entry)
            return True
        except (zipfile.BadZipFile, OSError):
            self._state = ArchiveState.FAILED
            return False

    @property
    def history(self) -> List[ArchiveOperation]:
        return list(self._history)

    @property
    def entries(self) -> List[ArchiveEntry]:
        return list(self._entries)

    @property
    def total_size(self) -> int:
        return sum(e.size for e in self._entries if not e.is_dir)

    @property
    def compressed_size(self) -> int:
        return sum(e.compressed_size for e in self._entries if not e.is_dir)

    @property
    def file_count(self) -> int:
        return sum(1 for e in self._entries if not e.is_dir)

    # -- Extraction ------------------------------------------------------

    def extract(self, archive_path: str, dest_dir: str,
                password: str = "",
                files: Optional[List[str]] = None) -> Optional[ArchiveOperation]:
        """Extract an archive.

        If files is None, extracts all files.
        """
        if not os.path.exists(archive_path):
            return None

        op = ArchiveOperation(
            id=f"arch-{int(time.time() * 1000) % 1000000}",
            archive_path=archive_path,
            extracting=True,
            password=password,
        )

        # Calculate totals
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                file_list = zf.namelist()
                if files:
                    file_list = [f for f in file_list if f in files]
                op.total_files = len(file_list)
                op.total_bytes = sum(
                    zf.getinfo(f).file_size for f in file_list
                    if not zf.getinfo(f).is_dir()
                )
        except (zipfile.BadZipFile, OSError):
            return None

        self._operations.append(op)
        self._start_operation(op, dest_dir)
        return op

    def _start_operation(self, op: ArchiveOperation, dest_dir: str) -> None:
        """Start an archive operation."""
        op.state = ArchiveState.EXTRACTING
        op.started_at = time.time()

        try:
            with zipfile.ZipFile(op.archive_path, "r") as zf:
                members = zf.namelist()
                for i, name in enumerate(members):
                    # Skip directories
                    if name.endswith("/"):
                        op.processed_files += 1
                        op.progress = op.processed_files / max(1, op.total_files)
                        continue

                    try:
                        zf.extract(name, dest_dir, pwd=op.password.encode() if op.password else None)
                        info = zf.getinfo(name)
                        op.processed_bytes += info.file_size
                    except (zipfile.BadZipFile, OSError, RuntimeError) as e:
                        op.errors.append(f"{name}: {e}")

                    op.processed_files = i + 1
                    op.progress = op.processed_files / max(1, op.total_files)

            op.state = ArchiveState.COMPLETED
            op.progress = 1.0
        except Exception as e:
            op.state = ArchiveState.FAILED
            op.errors.append(str(e))

        op.completed_at = time.time()
        self._operations.remove(op)
        self._history.insert(0, op)

    # -- Creation --------------------------------------------------------

    def create(self, archive_path: str, source_files: List[str],
               compression: CompressionLevel = CompressionLevel.NORMAL,
               password: str = "") -> Optional[ArchiveOperation]:
        """Create a ZIP archive from files."""
        op = ArchiveOperation(
            id=f"arch-{int(time.time() * 1000) % 1000000}",
            archive_path=archive_path,
            extracting=False,
            compression=compression,
            password=password,
            total_files=len(source_files),
        )

        # Calculate total bytes
        total = 0
        for f in source_files:
            if os.path.isfile(f):
                total += os.path.getsize(f)
        op.total_bytes = total

        self._operations.append(op)
        op.state = ArchiveState.CREATING
        op.started_at = time.time()

        # Map CompressionLevel to zipfile constants
        _compress_map = {
            CompressionLevel.STORED: zipfile.ZIP_STORED,
            CompressionLevel.FAST: zipfile.ZIP_DEFLATED,
            CompressionLevel.NORMAL: zipfile.ZIP_DEFLATED,
            CompressionLevel.BEST: zipfile.ZIP_DEFLATED,
        }
        try:
            with zipfile.ZipFile(archive_path, "w",
                                compression=_compress_map.get(compression, zipfile.ZIP_DEFLATED)) as zf:
                for i, filepath in enumerate(source_files):
                    arcname = os.path.basename(filepath)
                    zf.write(filepath, arcname)
                    if os.path.isfile(filepath):
                        op.processed_bytes += os.path.getsize(filepath)
                    op.processed_files = i + 1
                    op.progress = op.processed_files / max(1, op.total_files)

            op.state = ArchiveState.COMPLETED
            op.progress = 1.0
        except Exception as e:
            op.state = ArchiveState.FAILED
            op.errors.append(str(e))

        op.completed_at = time.time()
        self._operations.remove(op)
        self._history.insert(0, op)
        return op

    # -- Selection -------------------------------------------------------

    def select(self, index: int) -> Optional[ArchiveEntry]:
        if 0 <= index < len(self._entries):
            self._selected_index = index
            return self._entries[index]
        return None

    def move_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def move_down(self) -> None:
        self._selected_index = min(len(self._entries) - 1, self._selected_index + 1)

    def handle_key(self, key: str) -> str:
        if key == "Up":
            self.move_up()
            return "navigate"
        elif key == "Down":
            self.move_down()
            return "navigate"
        elif key in ("Enter", "Return"):
            entry = self.select(self._selected_index)
            if entry and entry.is_dir:
                return f"enter:{entry.path}"
            return f"select:{self._selected_index}"
        elif key == "Escape":
            self.close()
            return "close"
        return ""

    def close(self) -> None:
        self._current_archive = None
        self._entries.clear()
        self._state = ArchiveState.IDLE
        self._selected_index = 0

    # -- Stats -----------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "current_archive": self._current_archive,
            "entries": len(self._entries),
            "files": self.file_count,
            "total_size": self.total_size,
            "compressed_size": self.compressed_size,
            "operations": len(self._operations),
            "history": len(self._history),
        }

    # -- Rendering -------------------------------------------------------

    def render(self) -> Tuple[bytes, int, int]:
        """Render the archive manager UI."""
        w, h = self.width, self.height
        buf = bytearray(w * h * 3)
        bg = (30, 30, 40)
        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        # Header
        self._fill_rect(buf, w, 0, 0, w, 48, (42, 42, 56))

        # Archive info
        if self._current_archive:
            name = os.path.basename(self._current_archive)
            self._fill_rect(buf, w, 12, 56, 200, 14, (200, 200, 210))
            self._fill_rect(buf, w, 12, 74, 120, 14, (120, 120, 140))

        # File list
        y = 100
        for i, entry in enumerate(self._entries[:20]):
            is_selected = (i == self._selected_index)
            row_bg = (50, 50, 70) if is_selected else (35, 35, 48)
            self._fill_rect(buf, w, 4, y, w - 8, 28, row_bg)

            # Icon color
            icon_color = (255, 200, 60) if entry.is_dir else (80, 140, 255)
            self._fill_rect(buf, w, 12, y + 6, 12, 12, icon_color)

            # Name
            self._fill_rect(buf, w, 32, y + 8, 160, 10, (200, 200, 210))

            # Size
            self._fill_rect(buf, w, w - 80, y + 8, 60, 10, (120, 120, 140))

            y += 30

        # Progress bar for active operations
        for op in self._operations:
            bar_y = h - 30
            self._fill_rect(buf, w, 12, bar_y, w - 24, 12, (50, 50, 65))
            fill_w = int((w - 24) * op.progress)
            color = (80, 140, 255) if op.extracting else (80, 200, 120)
            self._fill_rect(buf, w, 12, bar_y, fill_w, 12, color)

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
            "archive": self._current_archive,
            "entries": len(self._entries),
            "history": len(self._history),
        }


__all__ = [
    "ArchiveManager", "ArchiveEntry", "ArchiveOperation",
    "ArchiveFormat", "CompressionLevel", "ArchiveState",
]
