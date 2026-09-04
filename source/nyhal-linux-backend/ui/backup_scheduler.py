"""
Nyrqis OS - Backup Scheduler
Incremental backups, encryption, and cloud sync.
"""

import time
import random
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class BackupType(Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    DIFFERENTIAL = "differential"
    MIRROR = "mirror"
    SNAPSHOT = "snapshot"


class BackupStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class EncryptionType(Enum):
    NONE = "none"
    AES256 = "aes256"
    GPG = "gpg"
    LUKS = "luks"


class CloudProvider(Enum):
    LOCAL = "local"
    S3 = "s3"
    GCS = "gcs"
    AZURE = "azure"
    B2 = "b2"
    R2 = "r2"
    WEBDAV = "webdav"


@dataclass
class BackupJob:
    name: str
    backup_type: BackupType = BackupType.INCREMENTAL
    status: BackupStatus = BackupStatus.PENDING
    source_paths: List[str] = field(default_factory=list)
    destination: str = ""
    cloud_provider: CloudProvider = CloudProvider.LOCAL
    encryption: EncryptionType = EncryptionType.AES256
    compression: bool = True
    progress: float = 0.0
    size_gb: float = 0.0
    files_count: int = 0
    files_changed: int = 0
    speed_mbps: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    next_run: float = 0.0
    last_run: float = 0.0
    run_count: int = 0
    error_count: int = 0
    retention_days: int = 30
    schedule: str = ""
    enabled: bool = True

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def status_icon(self) -> str:
        icons = {
            BackupStatus.PENDING: "⏳", BackupStatus.RUNNING: "🔄",
            BackupStatus.COMPLETED: "✅", BackupStatus.FAILED: "❌",
            BackupStatus.PAUSED: "⏸", BackupStatus.CANCELLED: "⬜",
        }
        return icons.get(self.status, "?")

    @property
    def size_display(self) -> str:
        if self.size_gb < 1:
            return f"{self.size_gb * 1024:.0f} MB"
        return f"{self.size_gb:.2f} GB"

    @property
    def encryption_icon(self) -> str:
        icons = {EncryptionType.NONE: "🔓", EncryptionType.AES256: "🔐",
                 EncryptionType.GPG: "🔐", EncryptionType.LUKS: "🔐"}
        return icons.get(self.encryption, "?")


@dataclass
class BackupVersion:
    job_name: str = ""
    version: int = 0
    timestamp: float = 0.0
    size_gb: float = 0.0
    files_count: int = 0
    checksum: str = ""
    backup_type: BackupType = BackupType.INCREMENTAL
    is_valid: bool = True

    @property
    def size_display(self) -> str:
        if self.size_gb < 1:
            return f"{self.size_gb * 1024:.0f} MB"
        return f"{self.size_gb:.2f} GB"

    @property
    def time_ago(self) -> str:
        delta = time.time() - self.timestamp
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.0f}h ago"
        return f"{delta / 86400:.0f}d ago"


@dataclass
class RestorePoint:
    name: str
    job_name: str = ""
    version: int = 0
    timestamp: float = 0.0
    size_gb: float = 0.0
    description: str = ""
    can_restore: bool = True


@dataclass
class CloudSync:
    provider: CloudProvider = CloudProvider.S3
    bucket: str = ""
    prefix: str = ""
    last_sync: float = 0.0
    synced_gb: float = 0.0
    files_synced: int = 0
    enabled: bool = True

    @property
    def last_sync_display(self) -> str:
        if self.last_sync == 0:
            return "Never"
        delta = time.time() - self.last_sync
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        return f"{delta / 3600:.0f}h ago"


class BackupScheduler:
    def __init__(self):
        self.jobs: List[BackupJob] = []
        self.versions: List[BackupVersion] = []
        self.restore_points: List[RestorePoint] = []
        self.cloud_syncs: List[CloudSync] = []
        self.total_backup_size_gb: float = 0.0
        self.auto_verify: bool = True
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.jobs = [
            BackupJob(name="System Config", backup_type=BackupType.INCREMENTAL,
                       source_paths=["/etc", "/boot/grub"],
                       destination="/mnt/backup/system-config",
                       encryption=EncryptionType.AES256, compression=True,
                       progress=100.0, size_gb=0.5, files_count=845,
                       files_changed=12, speed_mbps=150,
                       started_at=now - 7200, completed_at=now - 7000,
                       last_run=now - 3600, run_count=365,
                       schedule="daily 02:00", enabled=True, retention_days=30),
            BackupJob(name="Home Directory", backup_type=BackupType.INCREMENTAL,
                       source_paths=["/home/zeus"],
                       destination="/mnt/backup/home",
                       encryption=EncryptionType.AES256, compression=True,
                       progress=65.0, size_gb=45.0, files_count=12500,
                       files_changed=890, speed_mbps=280,
                       started_at=now - 600, last_run=now - 600,
                       run_count=52, schedule="weekly sun 03:00",
                       enabled=True, retention_days=60),
            BackupJob(name="Database Backup", backup_type=BackupType.FULL,
                       source_paths=["/var/lib/postgresql"],
                       destination="/mnt/backup/databases",
                       encryption=EncryptionType.GPG, compression=True,
                       progress=100.0, size_gb=12.0, files_count=15,
                       files_changed=15, speed_mbps=350,
                       started_at=now - 86400, completed_at=now - 85800,
                       last_run=now - 86400, run_count=180,
                       schedule="daily 04:00", enabled=True, retention_days=14),
            BackupJob(name="Nyrqis Source", backup_type=BackupType.FULL,
                       source_paths=["/opt/Nyrqis", "/home/zeus/Projects"],
                       destination="/mnt/backup/projects",
                       encryption=EncryptionType.AES256,
                       progress=100.0, size_gb=2.8, files_count=3200,
                       files_changed=45, speed_mbps=400,
                       started_at=now - 43200, completed_at=now - 43000,
                       last_run=now - 43200, run_count=120,
                       schedule="daily 01:00", enabled=True, retention_days=90),
            BackupJob(name="Cloud Sync", backup_type=BackupType.INCREMENTAL,
                       source_paths=["/home/zeus/Documents", "/home/zeus/Photos"],
                       destination="s3://nyrqis-backups/user-data",
                       cloud_provider=CloudProvider.S3,
                       encryption=EncryptionType.AES256,
                       progress=35.0, size_gb=28.0, files_count=8500,
                       files_changed=1200, speed_mbps=15,
                       started_at=now - 1800, last_run=now - 1800,
                       run_count=30, schedule="daily 05:00",
                       enabled=True, retention_days=365),
        ]
        self.total_backup_size_gb = sum(j.size_gb for j in self.jobs)

        for job in self.jobs:
            for v in range(3):
                self.versions.append(BackupVersion(
                    job_name=job.name, version=3 - v,
                    timestamp=now - v * 86400 * 7,
                    size_gb=job.size_gb * (1 - v * 0.1),
                    files_count=job.files_count,
                    checksum=hashlib.md5(f"{job.name}{v}".encode()).hexdigest()[:16],
                    backup_type=job.backup_type))

        self.restore_points = [
            RestorePoint(name="Before System Update", job_name="System Config",
                          version=2, timestamp=now - 86400 * 7, size_gb=0.45,
                          description="Clean config before kernel update"),
            RestorePoint(name="Working Home State", job_name="Home Directory",
                          version=2, timestamp=now - 86400 * 7, size_gb=42.0,
                          description="All projects building successfully"),
        ]

        self.cloud_syncs = [
            CloudSync(provider=CloudProvider.S3, bucket="nyrqis-backups",
                      prefix="user-data/", last_sync=now - 1800,
                      synced_gb=28.0, files_synced=8500),
            CloudSync(provider=CloudProvider.R2, bucket="nyrqis-archive",
                      prefix="documents/", last_sync=now - 86400,
                      synced_gb=15.0, files_synced=4200),
        ]

    def create_job(self, name: str, **kwargs) -> BackupJob:
        job = BackupJob(name=name, **kwargs)
        self.jobs.append(job)
        return job

    def delete_job(self, name: str) -> bool:
        for i, j in enumerate(self.jobs):
            if j.name == name:
                del self.jobs[i]
                return True
        return False

    def toggle_job(self, name: str) -> bool:
        job = next((j for j in self.jobs if j.name == name), None)
        if job:
            job.enabled = not job.enabled
            return True
        return False

    def run_job(self, name: str) -> Optional[BackupJob]:
        job = next((j for j in self.jobs if j.name == name), None)
        if job:
            job.status = BackupStatus.RUNNING
            job.started_at = time.time()
            job.status = BackupStatus.COMPLETED
            job.completed_at = time.time()
            job.run_count += 1
            job.last_run = time.time()
            return job
        return None

    def restore(self, restore_point_name: str) -> bool:
        rp = next((r for r in self.restore_points if r.name == restore_point_name), None)
        if rp and rp.can_restore:
            return True
        return False

    def get_running_jobs(self) -> List[BackupJob]:
        return [j for j in self.jobs if j.status == BackupStatus.RUNNING]

    def get_enabled_jobs(self) -> List[BackupJob]:
        return [j for j in self.jobs if j.enabled]

    def search(self, query: str) -> List[BackupJob]:
        q = query.lower()
        return [j for j in self.jobs if q in j.name.lower()]

    def get_stats(self) -> Dict:
        return {
            "jobs": len(self.jobs),
            "enabled": len(self.get_enabled_jobs()),
            "total_size_gb": round(self.total_backup_size_gb, 2),
            "versions": len(self.versions),
            "restore_points": len(self.restore_points),
            "cloud_syncs": len(self.cloud_syncs),
        }
