from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class CompressionAlgo(Enum):
    GZIP = "gzip"
    BZIP2 = "bzip2"
    XZ = "xz"
    ZSTD = "zstd"
    LZ4 = "lz4"
    BROTLI = "brotli"
    ZOPFLI = "zopfli"
    DEFLATE = "deflate"


class ArchiveFormat(Enum):
    TAR = "tar"
    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"
    TAR_XZ = "tar.xz"
    TAR_ZSTD = "tar.zst"
    ZIP = "zip"
    SEVEN_Z = "7z"
    TAR_LZ4 = "tar.lz4"


class CompressionLevel(Enum):
    FASTEST = 1
    FAST = 3
    NORMAL = 6
    HIGH = 9
    ULTRA = 12
    BEST = 19


class OperationStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class FileCategory(Enum):
    SOURCE = "source"
    DOCUMENT = "document"
    MEDIA = "media"
    ARCHIVE = "archive"
    DATA = "data"
    OTHER = "other"


@dataclass
class CompressibleFile:
    name: str
    path: str
    original_size: int
    category: FileCategory
    timestamp: float
    compressed_size: int = 0
    algorithm: Optional[CompressionAlgo] = None
    level: CompressionLevel = CompressionLevel.NORMAL
    status: OperationStatus = OperationStatus.PENDING
    is_favorite: bool = False
    error: str = ""

    @property
    def display_original(self) -> str:
        return self._format_size(self.original_size)

    @property
    def display_compressed(self) -> str:
        if self.compressed_size == 0:
            return "N/A"
        return self._format_size(self.compressed_size)

    @property
    def ratio_percent(self) -> float:
        if self.original_size == 0:
            return 0
        return ((self.original_size - self.compressed_size) / self.original_size * 100) if self.compressed_size > 0 else 0

    @property
    def ratio_display(self) -> str:
        ratio = self.ratio_percent
        if ratio == 0:
            return "N/A"
        return f"-{ratio:.1f}%"

    @property
    def filename(self) -> str:
        return self.path.split("/")[-1]

    @property
    def ratio_bar(self) -> str:
        filled = int(self.ratio_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @staticmethod
    def _format_size(b: int) -> str:
        if b >= 1073741824:
            return f"{b / 1073741824:.2f} GB"
        if b >= 1048576:
            return f"{b / 1048576:.1f} MB"
        if b >= 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b} B"


@dataclass
class BatchJob:
    name: str
    files: list = field(default_factory=list)
    algorithm: CompressionAlgo = CompressionAlgo.ZSTD
    level: CompressionLevel = CompressionLevel.NORMAL
    status: OperationStatus = OperationStatus.PENDING
    progress: float = 0.0
    start_time: float = 0.0
    complete_time: float = 0.0
    total_files: int = 0
    completed_files: int = 0
    failed_files: int = 0
    total_saved_bytes: int = 0

    @property
    def saved_display(self) -> str:
        return CompressibleFile._format_size(self.total_saved_bytes) if self.total_saved_bytes else "N/A"


class CompressionTool:
    def __init__(self):
        self._files: list[CompressibleFile] = []
        self._selected_file: int = 0
        self._default_algorithm: CompressionAlgo = CompressionAlgo.ZSTD
        self._default_level: CompressionLevel = CompressionLevel.NORMAL
        self._batch_jobs: list[BatchJob] = []
        self._selected_job: int = 0
        self._multi_thread: bool = True
        self._threads: int = 8
        self._preserve_permissions: bool = True
        self._overwrite: bool = False
        self._view: str = "files"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._files = [
            CompressibleFile("nyrqis-kernel-6.12.tar", "/home/user/builds/nyrqis-kernel-6.12.tar", 2_147_483_648, FileCategory.DATA, now - 86400, 687_194_767, CompressionAlgo.ZSTD, CompressionLevel.NORMAL, OperationStatus.COMPLETE, is_favorite=True),
            CompressibleFile("database-dump.sql", "/home/user/backups/database-dump.sql", 536_870_912, FileCategory.DATA, now - 7200, 89_125_888, CompressionAlgo.ZSTD, CompressionLevel.HIGH, OperationStatus.COMPLETE),
            CompressibleFile("project-sources.tar", "/home/user/projects/nyrqis.tar", 335_544_320, FileCategory.SOURCE, now - 3600, 47_185_920, CompressionAlgo.XZ, CompressionLevel.NORMAL, OperationStatus.COMPLETE),
            CompressibleFile("photos-2026.zip", "/home/user/Pictures/photos-2026.zip", 1_073_741_824, FileCategory.MEDIA, now - 86400 * 3, 1_020_054_732, CompressionAlgo.ZSTD, CompressionLevel.FASTEST, OperationStatus.COMPLETE),
            CompressibleFile("logs-archive.tar", "/var/log/archive.tar", 268_435_456, FileCategory.DATA, now - 86400 * 7, 12_582_912, CompressionAlgo.LZ4, CompressionLevel.FASTEST, OperationStatus.COMPLETE),
            CompressibleFile("neovim-config", "/home/user/.config/nvim", 2_048_000, FileCategory.CONFIG if hasattr(FileCategory, 'CONFIG') else FileCategory.OTHER, now - 1800, 409_600, CompressionAlgo.BROTLI, CompressionLevel.HIGH, OperationStatus.COMPLETE),
            CompressibleFile("vm-disk.qcow2", "/home/user/vms/nycrqis-dev.qcow2", 10_737_418_240, FileCategory.OTHER, now - 86400 * 14, 0, CompressionAlgo.ZSTD, CompressionLevel.NORMAL, OperationStatus.PENDING),
            CompressibleFile("rust-target.tar", "/home/user/projects/target.tar", 4_294_967_296, FileCategory.SOURCE, now - 3600 * 5, 0, CompressionAlgo.ZSTD, CompressionLevel.FAST, OperationStatus.PENDING),
        ]

        self._batch_jobs = [
            BatchJob("Compress build artifacts", ["nyrqis-kernel-6.12.tar", "rust-target.tar"], CompressionAlgo.ZSTD, CompressionLevel.NORMAL, OperationStatus.COMPLETE, 100.0, now - 3600, now, 2, 2, 0, 1_500_000_000),
            BatchJob("Backup compress", ["database-dump.sql", "logs-archive.tar"], CompressionAlgo.ZSTD, CompressionLevel.HIGH, OperationStatus.COMPLETE, 100.0, now - 7200, now - 3500, 2, 2, 0, 700_000_000),
        ]

    @property
    def selected_file(self) -> Optional[CompressibleFile]:
        if 0 <= self._selected_file < len(self._files):
            return self._files[self._selected_file]
        return None

    @property
    def total_files(self) -> int:
        return len(self._files)

    @property
    def total_original_size(self) -> int:
        return sum(f.original_size for f in self._files)

    @property
    def total_compressed_size(self) -> int:
        return sum(f.compressed_size for f in self._files if f.compressed_size > 0)

    @property
    def total_saved(self) -> int:
        return self.total_original_size - self.total_compressed_size

    @property
    def overall_ratio(self) -> float:
        if self.total_original_size == 0:
            return 0
        return ((self.total_original_size - self.total_compressed_size) / self.total_original_size * 100) if self.total_compressed_size > 0 else 0

    @property
    def completed_count(self) -> int:
        return sum(1 for f in self._files if f.status == OperationStatus.COMPLETE)

    @property
    def pending_count(self) -> int:
        return sum(1 for f in self._files if f.status == OperationStatus.PENDING)

    def select_file(self, idx: int):
        if 0 <= idx < len(self._files):
            self._selected_file = idx

    def compress_file(self, idx: int) -> bool:
        if 0 <= idx < len(self._files):
            f = self._files[idx]
            if f.status == OperationStatus.PENDING and f.original_size > 0:
                # Simulate compression
                import random
                rng = random.Random(idx)
                ratio = rng.uniform(0.3, 0.7)
                f.compressed_size = int(f.original_size * (1 - ratio))
                f.algorithm = self._default_algorithm
                f.level = self._default_level
                f.status = OperationStatus.COMPLETE
                return True
        return False

    def compress_all_pending(self):
        for i, f in enumerate(self._files):
            if f.status == OperationStatus.PENDING:
                self.compress_file(i)

    def add_file(self, path: str, size: int, category: FileCategory = FileCategory.OTHER) -> CompressibleFile:
        f = CompressibleFile(path.split("/")[-1], path, size, category, time.time())
        self._files.append(f)
        self._selected_file = len(self._files) - 1
        return f

    def delete_file(self, idx: int) -> bool:
        if 0 <= idx < len(self._files):
            self._files.pop(idx)
            if self._selected_file >= len(self._files):
                self._selected_file = max(0, len(self._files) - 1)
            return True
        return False

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                   NYRQIS COMPRESSION TOOL                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Algorithm: {self._default_algorithm.value.upper()}  Level: {self._default_level.value}  Threads: {self._threads if self._multi_thread else 1}")
        lines.append(f"  Files: {self.total_files}  Original: {CompressibleFile._format_size(self.total_original_size)}  Compressed: {CompressibleFile._format_size(self.total_compressed_size)}  Saved: -{self.overall_ratio:.1f}%")
        lines.append(f"  Completed: {self.completed_count}  Pending: {self.pending_count}  Permissions: {'Preserve' if self._preserve_permissions else 'Ignore'}")
        lines.append("")
        for i, f in enumerate(self._files):
            sel = "▶" if i == self._selected_file else " "
            status = {"complete": "✅", "pending": "⏳", "running": "🔄", "failed": "❌"}.get(f.status.value, "?")
            cat_icons = {"source": "📜", "document": "📄", "media": "🖼️", "archive": "📦", "data": "💾", "other": "📁"}
            icon = cat_icons.get(f.category.value, "📄")
            lines.append(f"  {sel}{status} {icon} {f.filename}")
            lines.append(f"    {f.display_original} → {f.display_compressed} [{f.ratio_bar}] {f.ratio_display}")
        lines.append("")
        lines.append("  ── Batch Jobs ──")
        for j in self._batch_jobs:
            status = {"complete": "✅", "pending": "⏳", "running": "🔄"}.get(j.status.value, "?")
            lines.append(f"  {status} {j.name}  {j.completed_files}/{j.total_files} files  Saved: {j.saved_display}")
        lines.append("")
        lines.append("  [C]ompress  [A]dd  [D]elete  [B]atch  [L]evel  [T]hreads  [E]xtract")
        return lines

    def render_file_detail(self) -> list:
        f = self.selected_file
        if not f:
            return ["  No file selected"]
        lines = []
        lines.append(f"  ── {f.filename} ──")
        lines.append(f"  Path: {f.path}")
        lines.append(f"  Category: {f.category.value}")
        lines.append(f"  Original: {f.display_original}")
        lines.append(f"  Compressed: {f.display_compressed}")
        lines.append(f"  Ratio: {f.ratio_display}")
        lines.append(f"  Algorithm: {f.algorithm.value if f.algorithm else 'N/A'}")
        lines.append(f"  Level: {f.level.value}")
        lines.append(f"  Status: {f.status.value}")
        return lines

    def render_algorithms(self) -> list:
        lines = []
        lines.append("  ── Algorithms ──")
        lines.append("")
        algos = [
            ("zstd", "Best balance", "★★★★★"),
            ("lz4", "Fastest", "★★★★★"),
            ("xz", "Best ratio", "★★★★☆"),
            ("brotli", "Web optimized", "★★★★☆"),
            ("bzip2", "Legacy", "★★★☆☆"),
            ("gzip", "Universal", "★★★☆☆"),
            ("zopfli", "Best gzip-compatible", "★★★★☆"),
            ("deflate", "Legacy", "★★☆☆☆"),
        ]
        for name, desc, stars in algos:
            sel = "▶" if name == self._default_algorithm.value else " "
            lines.append(f"  {sel} {name:<10s} {desc:<20s} {stars}")
        return lines
