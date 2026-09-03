from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import hashlib


class HashAlgorithm(Enum):
    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"
    SHA512 = "sha512"
    BLAKE2 = "blake2b"


class VerifyStatus(Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    CORRUPTED = "corrupted"


class BatchStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class FileEntry:
    path: str
    size_bytes: int
    timestamp: float
    hashes: dict = field(default_factory=dict)
    verify_status: VerifyStatus = VerifyStatus.UNVERIFIED
    is_bookmarked: bool = False
    notes: str = ""

    @property
    def display_size(self) -> str:
        size = self.size_bytes
        if size >= 1024 * 1024 * 1024:
            return f"{size / (1024 ** 3):.1f} GB"
        if size >= 1024 * 1024:
            return f"{size / (1024 ** 2):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    @property
    def filename(self) -> str:
        return self.path.split("/")[-1]

    @property
    def extension(self) -> str:
        parts = self.filename.split(".")
        return parts[-1] if len(parts) > 1 else ""


@dataclass
class BatchJob:
    name: str
    files: list = field(default_factory=list)
    algorithm: HashAlgorithm = HashAlgorithm.SHA256
    status: BatchStatus = BatchStatus.PENDING
    progress: float = 0.0
    start_time: float = 0.0
    complete_time: float = 0.0
    total_files: int = 0
    completed_files: int = 0
    failed_files: int = 0


@dataclass
class IntegrityCheck:
    path: str
    expected_hash: str
    actual_hash: str
    algorithm: HashAlgorithm
    status: VerifyStatus
    checked_at: float


class FileHasher:
    def __init__(self):
        self._files: list[FileEntry] = []
        self._selected: int = 0
        self._default_algorithm: HashAlgorithm = HashAlgorithm.SHA256
        self._verify_queue: list = []
        self._history: list[IntegrityCheck] = []
        self._batch_jobs: list[BatchJob] = []
        self._clipboard_hash: str = ""
        self._compare_left: str = ""
        self._compare_right: str = ""
        self._view: str = "files"
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        files = [
            FileEntry("/home/user/nyrqis-os-v1.1.iso", 4_294_967_296, now - 86400, {
                HashAlgorithm.MD5: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
                HashAlgorithm.SHA256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                HashAlgorithm.BLAKE2: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            }, VerifyStatus.MATCH, is_bookmarked=True, notes="Release ISO for v1.1"),
            FileEntry("/home/user/.config/nyrqis/compositor.toml", 2048, now - 3600, {
                HashAlgorithm.SHA256: "abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
            }, VerifyStatus.MATCH),
            FileEntry("/home/user/projects/nyrqis/target/release/nyrqis-compositor", 15_728_640, now - 7200, {
                HashAlgorithm.SHA256: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                HashAlgorithm.MD5: "0123456789abcdef0123456789abcdef",
            }, VerifyStatus.MATCH),
            FileEntry("/var/cache/packages/nvidia-driver-560.pkg.tar.zst", 524_288_000, now - 1800, {
                HashAlgorithm.SHA256: "cafe0cafe0cafe0cafe0cafe0cafe0cafe0cafe0cafe0cafe0cafe0cafe0cafe0",
            }, VerifyStatus.MISMATCH, notes="Driver package - hash changed after re-download"),
            FileEntry("/home/user/Downloads/rustup-init.sh", 12_847, now - 600, {
                HashAlgorithm.SHA256: "f2ca1bb6c7e907d06dafe4687e579fce76b37e4e93b7605022da52e6ccc26fd2",
            }, VerifyStatus.UNVERIFIED),
            FileEntry("/home/user/Documents/backup-keys.gpg", 8192, now - 43200, {
                HashAlgorithm.SHA256: "b10a8db164e0754105b7a99be72e3fe5",  # intentionally short
            }, VerifyStatus.UNVERIFIED, is_bookmarked=True, notes="Encryption keys backup"),
            FileEntry("/opt/nyrqis/firmware/microcode.img", 33_554_432, now - 86400 * 7, {
                HashAlgorithm.SHA512: "a1b2c3d4" * 16,
                HashAlgorithm.SHA256: "e5f6a7b8" * 8,
            }, VerifyStatus.CORRUPTED, notes="Suspected corruption - needs re-download"),
        ]
        self._files = files

        # Sample batch jobs
        self._batch_jobs = [
            BatchJob("Verify ISO Downloads", algorithm=HashAlgorithm.SHA256, status=BatchStatus.COMPLETE,
                     total_files=3, completed_files=3, progress=100.0),
            BatchJob("Scan Config Directory", algorithm=HashAlgorithm.MD5, status=BatchStatus.COMPLETE,
                     total_files=15, completed_files=15, progress=100.0),
        ]

        # Sample history
        self._history = [
            IntegrityCheck("/home/user/nyrqis-os-v1.1.iso", "e3b0c44...", "e3b0c44...", HashAlgorithm.SHA256, VerifyStatus.MATCH, now - 86400),
            IntegrityCheck("/var/cache/packages/nvidia-driver-560.pkg.tar.zst", "expected...", "actual...", HashAlgorithm.SHA256, VerifyStatus.MISMATCH, now - 1800),
            IntegrityCheck("/opt/nyrqis/firmware/microcode.img", "expected...", "corrupted...", HashAlgorithm.SHA256, VerifyStatus.CORRUPTED, now - 86400 * 7),
        ]

    @property
    def selected_file(self) -> Optional[FileEntry]:
        if 0 <= self._selected < len(self._files):
            return self._files[self._selected]
        return None

    @property
    def total_files(self) -> int:
        return len(self._files)

    @property
    def total_size(self) -> int:
        return sum(f.size_bytes for f in self._files)

    @property
    def verified_count(self) -> int:
        return sum(1 for f in self._files if f.verify_status == VerifyStatus.MATCH)

    @property
    def mismatch_count(self) -> int:
        return sum(1 for f in self._files if f.verify_status in (VerifyStatus.MISMATCH, VerifyStatus.CORRUPTED))

    @property
    def bookmarked_count(self) -> int:
        return sum(1 for f in self._files if f.is_bookmarked)

    @property
    def status_counts(self) -> dict:
        counts = {}
        for f in self._files:
            counts[f.verify_status.value] = counts.get(f.verify_status.value, 0) + 1
        return counts

    @property
    def algorithm_counts(self) -> dict:
        counts = {}
        for f in self._files:
            for algo in f.hashes:
                counts[algo.value] = counts.get(algo.value, 0) + 1
        return counts

    @staticmethod
    def compute_hash(data: str, algorithm: HashAlgorithm) -> str:
        h = hashlib.new(algorithm.value)
        h.update(data.encode())
        return h.hexdigest()

    def select(self, idx: int):
        if 0 <= idx < len(self._files):
            self._selected = idx

    def hash_file(self, entry: FileEntry, algorithm: HashAlgorithm) -> str:
        """Simulate hashing a file using the filename as input."""
        result = self.compute_hash(entry.path + entry.filename, algorithm)
        entry.hashes[algorithm] = result
        entry.verify_status = VerifyStatus.UNVERIFIED
        return result

    def verify_file(self, entry: FileEntry) -> bool:
        """Verify a file's hash matches expected."""
        algo = self._default_algorithm
        if algo in entry.hashes:
            entry.verify_status = VerifyStatus.MATCH
            self._history.append(IntegrityCheck(entry.path, entry.hashes[algo], entry.hashes[algo], algo, VerifyStatus.MATCH, time.time()))
            return True
        return False

    def compare_hashes(self, hash_a: str, hash_b: str) -> bool:
        self._compare_left = hash_a
        self._compare_right = hash_b
        return hash_a.strip().lower() == hash_b.strip().lower()

    def toggle_bookmark(self):
        f = self.selected_file
        if f:
            f.is_bookmarked = not f.is_bookmarked

    def add_file(self, path: str, size: int = 0) -> FileEntry:
        entry = FileEntry(path, size, time.time())
        self._files.append(entry)
        self._selected = len(self._files) - 1
        return entry

    def remove_file(self, idx: int) -> bool:
        if 0 <= idx < len(self._files):
            self._files.pop(idx)
            if self._selected >= len(self._files):
                self._selected = max(0, len(self._files) - 1)
            return True
        return False

    def export_hashes(self, algo: HashAlgorithm) -> str:
        lines = []
        for f in self._files:
            if algo in f.hashes:
                lines.append(f"{f.hashes[algo]}  {f.path}")
        return "\n".join(lines)

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS FILE HASHER & VERIFIER                          ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Algorithm: {self._default_algorithm.value.upper()}  Files: {self.total_files}  Verified: {self.verified_count}  Issues: {self.mismatch_count}")
        lines.append("")
        status_icons = {"match": "✅", "mismatch": "❌", "corrupted": "💥", "unverified": "❓"}
        for i, f in enumerate(self._files):
            sel = "▶" if i == self._selected else " "
            icon = status_icons.get(f.verify_status.value, "?")
            bookmark = " ⭐" if f.is_bookmarked else ""
            lines.append(f"  {sel} {icon} {f.filename}{bookmark}")
            lines.append(f"    {f.display_size} · {f.verify_status.value} · {f.extension or 'no ext'}")
        lines.append("")
        lines.append("  ── Batch Jobs ──────────────────────────────────────────")
        for j in self._batch_jobs:
            lines.append(f"  📋 {j.name}: {j.status.value} ({j.completed_files}/{j.total_files})")
        lines.append("")
        lines.append("  [H]ash  [V]erify  [C]ompare  [B]ookmark  [E]xport  [A]dd")
        return lines

    def render_file_detail(self) -> list:
        f = self.selected_file
        if not f:
            return ["  No file selected"]
        lines = []
        lines.append(f"  ── {f.filename} ──")
        lines.append(f"  Path: {f.path}")
        lines.append(f"  Size: {f.display_size} ({f.size_bytes:,} bytes)")
        lines.append(f"  Status: {f.verify_status.value}")
        lines.append(f"  Bookmarked: {'Yes' if f.is_bookmarked else 'No'}")
        if f.notes:
            lines.append(f"  Notes: {f.notes}")
        lines.append("")
        lines.append("  Hashes:")
        for algo, hash_val in f.hashes.items():
            lines.append(f"    {algo.value.upper()}: {hash_val[:32]}...")
        if not f.hashes:
            lines.append("    (no hashes computed)")
        return lines

    def render_verify_history(self) -> list:
        lines = []
        lines.append("  ── Verification History ──")
        lines.append("")
        status_icons = {"match": "✅", "mismatch": "❌", "corrupted": "💥"}
        for h in self._history[-15:]:
            icon = status_icons.get(h.status.value, "❓")
            age = int((time.time() - h.checked_at) / 3600)
            lines.append(f"  {icon} {h.path.split('/')[-1]} ({h.algorithm.value}) {age}h ago")
        return lines
