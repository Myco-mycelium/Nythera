"""Cloud Storage Manager — S3/GCS integration, sync status, bandwidth monitoring for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import random


class CloudProvider(Enum):
    AWS_S3 = "AWS S3"
    GOOGLE_CLOUD = "Google Cloud Storage"
    AZURE_BLOB = "Azure Blob Storage"
    BACKBLAZE_B2 = "Backblaze B2"
    MINIO = "MinIO"
    R2 = "Cloudflare R2"


class SyncStatus(Enum):
    SYNCED = "Synced"
    SYNCING = "Syncing"
    UPLOADING = "Uploading"
    DOWNLOADING = "Downloading"
    CONFLICT = "Conflict"
    PENDING = "Pending"
    ERROR = "Error"
    OFFLINE = "Offline"


class StorageClass(Enum):
    STANDARD = "Standard"
    STANDARD_IA = "Standard-IA"
    GLACIER = "Glacier"
    DEEP_ARCHIVE = "Deep Archive"
    NEARLINE = "Nearline"
    COLDLINE = "Coldline"
    INTELLIGENT_TIERING = "Intelligent-Tiering"


class Permission(Enum):
    PRIVATE = "Private"
    PUBLIC_READ = "Public Read"
    AUTH_READ = "Authenticated Read"
    BUCKET_OWNER_READ = "Bucket Owner Read"
    CUSTOM = "Custom"


@dataclass
class CloudFile:
    name: str
    path: str = ""
    size_bytes: int = 0
    mime_type: str = ""
    etag: str = ""
    storage_class: StorageClass = StorageClass.STANDARD
    permission: Permission = Permission.PRIVATE
    created: float = 0.0
    modified: float = 0.0
    synced: bool = True
    local_path: str = ""
    version: int = 1
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def size_human(self) -> str:
        b = self.size_bytes
        if b < 1024:
            return f"{b} B"
        elif b < 1024**2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024**3:
            return f"{b / 1024**2:.1f} MB"
        return f"{b / 1024**3:.2f} GB"

    @property
    def sync_icon(self) -> str:
        icons = {
            SyncStatus.SYNCED: "✅", SyncStatus.SYNCING: "🔄",
            SyncStatus.UPLOADING: "⬆", SyncStatus.DOWNLOADING: "⬇",
            SyncStatus.CONFLICT: "⚠️", SyncStatus.PENDING: "⏳",
            SyncStatus.ERROR: "❌", SyncStatus.OFFLINE: "📴",
        }
        return icons.get(self.synced and SyncStatus.SYNCED or SyncStatus.PENDING, "?")

    @property
    def ext(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

    @property
    def icon(self) -> str:
        icons = {
            "pdf": "📕", "doc": "📘", "docx": "📘", "xls": "📗", "xlsx": "📗",
            "jpg": "🖼", "jpeg": "🖼", "png": "🖼", "gif": "🖼", "svg": "🖼",
            "mp4": "🎬", "mov": "🎬", "avi": "🎬", "mkv": "🎬",
            "mp3": "🎵", "wav": "🎵", "flac": "🎵",
            "zip": "📦", "tar": "📦", "gz": "📦",
            "py": "🐍", "rs": "🦀", "js": "📜", "ts": "📜", "go": "🔷",
        }
        return icons.get(self.ext, "📄")


@dataclass
class Bucket:
    name: str
    provider: CloudProvider = CloudProvider.AWS_S3
    region: str = "us-east-1"
    total_bytes: int = 0
    object_count: int = 0
    versioning: bool = False
    encryption: bool = True
    created: float = 0.0
    transfer_acceleration: bool = False
    lifecycle_rules: int = 0

    @property
    def size_human(self) -> str:
        b = self.total_bytes
        if b < 1024**2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024**3:
            return f"{b / 1024**2:.1f} MB"
        return f"{b / 1024**3:.2f} GB"

    @property
    def provider_icon(self) -> str:
        icons = {
            CloudProvider.AWS_S3: "🟠", CloudProvider.GOOGLE_CLOUD: "🔵",
            CloudProvider.AZURE_BLOB: "🔷", CloudProvider.BACKBLAZE_B2: "🟢",
            CloudProvider.MINIO: "🟡", CloudProvider.R2: "🟣",
        }
        return icons.get(self.provider, "☁️")


@dataclass
class SyncTask:
    name: str
    local_path: str = ""
    remote_path: str = ""
    bucket: str = ""
    status: SyncStatus = SyncStatus.PENDING
    direction: str = "upload"  # upload, download, bidirectional
    progress: float = 0.0
    files_total: int = 0
    files_done: int = 0
    bytes_total: int = 0
    bytes_transferred: int = 0
    started_at: float = 0.0
    last_sync: float = 0.0
    schedule: str = ""
    exclude: List[str] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        icons = {
            SyncStatus.SYNCED: "✅", SyncStatus.SYNCING: "🔄",
            SyncStatus.UPLOADING: "⬆", SyncStatus.DOWNLOADING: "⬇",
            SyncStatus.CONFLICT: "⚠️", SyncStatus.PENDING: "⏳",
            SyncStatus.ERROR: "❌",
        }
        return icons.get(self.status, "?")

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def speed_str(self) -> str:
        if self.bytes_transferred <= 0:
            return "0 B/s"
        elapsed = max(1, time.time() - self.started_at) if self.started_at > 0 else 1
        speed = self.bytes_transferred / elapsed
        if speed < 1024:
            return f"{speed:.0f} B/s"
        elif speed < 1024**2:
            return f"{speed / 1024:.1f} KB/s"
        return f"{speed / 1024**2:.1f} MB/s"


@dataclass
class BandwidthSample:
    timestamp: float = 0.0
    upload_bytes: int = 0
    download_bytes: int = 0

    @property
    def total(self) -> int:
        return self.upload_bytes + self.download_bytes


class CloudStorage:
    def __init__(self):
        self._buckets: List[Bucket] = []
        self._files: List[CloudFile] = []
        self._sync_tasks: List[SyncTask] = []
        self._bandwidth_history: List[BandwidthSample] = []
        self._selected_bucket: int = 0
        self._selected_file: int = 0
        self._view_mode: str = "buckets"
        self._total_upload_bytes: int = 0
        self._total_download_bytes: int = 0
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        self._buckets = [
            Bucket("nyrqis-production", CloudProvider.AWS_S3, "us-west-2", 45 * 1024**3, 12450,
                   versioning=True, created=now - 86400 * 365, transfer_acceleration=True, lifecycle_rules=3),
            Bucket("nyrqis-backups", CloudProvider.AWS_S3, "us-west-2", 120 * 1024**3, 3400,
                   versioning=True, created=now - 86400 * 300),
            Bucket("nyrqis-assets", CloudProvider.GOOGLE_CLOUD, "us-central1", 28 * 1024**3, 8900,
                   created=now - 86400 * 200),
            Bucket("nyrqis-staging", CloudProvider.R2, "auto", 5 * 1024**3, 2100,
                   created=now - 86400 * 90),
            Bucket("personal-photos", CloudProvider.GOOGLE_CLOUD, "us-east1", 85 * 1024**3, 24000,
                   created=now - 86400 * 500),
            Bucket("dev-artifacts", CloudProvider.MINIO, "local", 2 * 1024**3, 890,
                   created=now - 86400 * 60),
        ]

        self._files = [
            CloudFile("compositor-v1.4.tar.gz", "builds/", 45 * 1024**2, "application/gzip", storage_class=StorageClass.STANDARD),
            CloudFile("database-backup.sql", "backups/", 2.8 * 1024**3, "application/sql", storage_class=StorageClass.STANDARD_IA),
            CloudFile("logo-dark.png", "assets/branding/", 1.2 * 1024**2, "image/png"),
            CloudFile("logo-light.png", "assets/branding/", 1.1 * 1024**2, "image/png"),
            CloudFile("user-manual.pdf", "docs/", 8.5 * 1024**2, "application/pdf"),
            CloudFile("screenshot-2026.png", "assets/screenshots/", 3.2 * 1024**2, "image/png"),
            CloudFile("release-notes.md", "docs/", 12 * 1024, "text/markdown"),
            CloudFile("nyrqis-compositor", "bin/", 18 * 1024**2, "application/octet-stream"),
            CloudFile("archive-2025.tar.gz", "archive/", 35 * 1024**3, "application/gzip", storage_class=StorageClass.GLACIER),
            CloudFile("config.yml", "config/", 4 * 1024, "text/yaml"),
        ]

        self._sync_tasks = [
            SyncTask("Production Build Sync", "/home/nyrqis/build/", "builds/", "nyrqis-production",
                     SyncStatus.SYNCING, "upload", 0.73, 45, 33, 450 * 1024**2, 330 * 1024**2,
                     now - 300, now - 60, schedule="*/30 * * * *"),
            SyncTask("Backup to S3", "/home/nyrqis/data/", "backups/", "nyrqis-backups",
                     SyncStatus.SYNCED, "upload", 1.0, 120, 120, 2.8 * 1024**3, 2.8 * 1024**3,
                     now - 7200, now - 3600, schedule="0 2 * * *", exclude=["*.tmp", "*.log"]),
            SyncTask("Photos Backup", "/home/nyrqis/Pictures/", "photos/", "personal-photos",
                     SyncStatus.PENDING, "bidirectional", 0.0, 500, 0, 85 * 1024**3, 0,
                     schedule="0 3 * * 0"),
            SyncTask("Assets Mirror", "/home/nyrqis/assets/", "assets/", "nyrqis-assets",
                     SyncStatus.UPLOADING, "upload", 0.45, 200, 90, 28 * 1024**3, 12.6 * 1024**3,
                     now - 600),
            SyncTask("Config Sync", "/home/nyrqis/.config/", "config/", "nyrqis-staging",
                     SyncStatus.SYNCED, "bidirectional", 1.0, 45, 45, 12 * 1024**2, 12 * 1024**2,
                     now - 3600, now - 1800),
        ]

        # Bandwidth history (last 30 samples, ~30 minutes)
        for i in range(30):
            self._bandwidth_history.append(BandwidthSample(
                now - (30 - i) * 60,
                int(random.uniform(100 * 1024, 10 * 1024**2)),
                int(random.uniform(500 * 1024, 25 * 1024**2)),
            ))
            self._total_upload_bytes += self._bandwidth_history[-1].upload_bytes
            self._total_download_bytes += self._bandwidth_history[-1].download_bytes

    @property
    def selected_bucket(self) -> Optional[Bucket]:
        if 0 <= self._selected_bucket < len(self._buckets):
            return self._buckets[self._selected_bucket]
        return None

    @property
    def total_storage(self) -> str:
        total = sum(b.total_bytes for b in self._buckets)
        if total < 1024**3:
            return f"{total / 1024**2:.0f} MB"
        return f"{total / 1024**3:.1f} GB"

    @property
    def total_objects(self) -> int:
        return sum(b.object_count for b in self._buckets)

    @property
    def active_syncs(self) -> int:
        return sum(1 for s in self._sync_tasks if s.status in (SyncStatus.SYNCING, SyncStatus.UPLOADING, SyncStatus.DOWNLOADING))

    def select_bucket(self, idx: int):
        if 0 <= idx < len(self._buckets):
            self._selected_bucket = idx

    def handle_input(self, key: str):
        key = key.lower()
        if key == "b":
            self._view_mode = "buckets"
        elif key == "f":
            self._view_mode = "files"
        elif key == "s":
            self._view_mode = "sync"
        elif key == "m":
            self._view_mode = "bandwidth"

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS CLOUD STORAGE MANAGER                             ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  ☁️ Buckets: {len(self._buckets)}  Total: {self.total_storage}  Objects: {self.total_objects:,}  Syncs: {self.active_syncs}/{len(self._sync_tasks)}")
        lines.append(f"  ⬆ Uploaded: {self._fmt_bytes(self._total_upload_bytes)}  ⬇ Downloaded: {self._fmt_bytes(self._total_download_bytes)}")
        lines.append("")

        # Buckets
        lines.append("  ── Buckets ──")
        for i, b in enumerate(self._buckets):
            sel = "▶" if i == self._selected_bucket else " "
            ver = "🔄" if b.versioning else "  "
            enc = "🔒" if b.encryption else "  "
            accel = "⚡" if b.transfer_acceleration else "  "
            lines.append(f"  {sel} {b.provider_icon} {b.name:<30s} {b.region:<12s} {b.size_human:>10s}  {b.object_count:>6d} objects  {ver}{enc}{accel}")
        lines.append("")

        # Files
        lines.append("  ── Files ──")
        for f in self._files[:10]:
            sync = f.sync_icon
            lines.append(f"  {sync} {f.icon} {f.path}{f.name:<30s} {f.size_human:>10s}  {f.storage_class.value}")
        lines.append("")

        # Sync tasks
        lines.append("  ── Sync Tasks ──")
        for st in self._sync_tasks:
            lines.append(f"  {st.status_icon} {st.name:<25s} [{st.progress_bar}] {st.progress:.0%}  {st.speed_str}  {st.files_done}/{st.files_total} files")
            lines.append(f"      {st.local_path} → {st.remote_path} ({st.bucket})")
        lines.append("")

        # Bandwidth sparkline
        if self._bandwidth_history:
            lines.append("  ── Bandwidth (30min) ──")
            upload_bars = []
            for s in self._bandwidth_history[-20:]:
                ratio = s.upload_bytes / (10 * 1024**2)
                upload_bars.append("▁▂▃▄▅▆▇█"[min(int(ratio * 8), 7)])
            lines.append(f"  ⬆ Upload:  {''.join(upload_bars)}")
            download_bars = []
            for s in self._bandwidth_history[-20:]:
                ratio = s.download_bytes / (25 * 1024**2)
                download_bars.append("▁▂▃▄▅▆▇█"[min(int(ratio * 8), 7)])
            lines.append(f"  ⬇ Download: {''.join(download_bars)}")
            lines.append("")

        lines.append("  [B]uckets [F]iles [S]ync [M]onitor [U]pload [D]ownload")
        return lines

    @staticmethod
    def _fmt_bytes(b: int) -> str:
        if b < 1024**2:
            return f"{b / 1024:.0f} KB"
        elif b < 1024**3:
            return f"{b / 1024**2:.1f} MB"
        return f"{b / 1024**3:.2f} GB"
