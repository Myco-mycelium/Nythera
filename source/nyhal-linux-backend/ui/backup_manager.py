"""System Backup Manager — Snapshots, restore points, and scheduling.

Features:
- Snapshot creation with incremental/differential/full modes
- Restore point management
- Backup schedules (daily, weekly, monthly)
- Storage usage tracking
- Backup integrity verification
- Compression and deduplication stats
- Backup/restore history
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class BackupMode(Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    DIFFERENTIAL = "differential"
    SNAPSHOT = "snapshot"

    @property
    def icon(self) -> str:
        icons = {
            BackupMode.FULL: "📦", BackupMode.INCREMENTAL: "📑",
            BackupMode.DIFFERENTIAL: "📋", BackupMode.SNAPSHOT: "📸",
        }
        return icons.get(self, "?")


class BackupStatus(Enum):
    COMPLETED = "completed"
    RUNNING = "running"
    SCHEDULED = "scheduled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    VERIFIED = "verified"

    @property
    def icon(self) -> str:
        icons = {
            BackupStatus.COMPLETED: "✅", BackupStatus.RUNNING: "🔄",
            BackupStatus.SCHEDULED: "📅", BackupStatus.FAILED: "❌",
            BackupStatus.CANCELLED: "🚫", BackupStatus.VERIFIED: "✔️",
        }
        return icons.get(self, "?")


class ScheduleFreq(Enum):
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    BOOT = "on_boot"

    @property
    def icon(self) -> str:
        icons = {
            ScheduleFreq.HOURLY: "🕐", ScheduleFreq.DAILY: "📅",
            ScheduleFreq.WEEKLY: "📆", ScheduleFreq.MONTHLY: "🗓",
            ScheduleFreq.BOOT: "🚀",
        }
        return icons.get(self, "?")


@dataclass
class BackupSnapshot:
    id: int = 0
    name: str = ""
    mode: BackupMode = BackupMode.FULL
    status: BackupStatus = BackupStatus.COMPLETED
    timestamp: float = 0.0
    size_gb: float = 0.0
    compressed_gb: float = 0.0
    file_count: int = 0
    included_paths: List[str] = field(default_factory=list)
    excluded_paths: List[str] = field(default_factory=list)
    duration_s: float = 0.0
    parent_id: int = 0  # parent snapshot for incremental
    verified: bool = False
    notes: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    @property
    def age_str(self) -> str:
        age = time.time() - self.timestamp
        if age < 3600:
            return f"{age / 60:.0f}m ago"
        if age < 86400:
            return f"{age / 3600:.1f}h ago"
        return f"{age / 86400:.0f}d ago"

    @property
    def compression_ratio(self) -> float:
        if self.size_gb == 0:
            return 0.0
        return (1 - self.compressed_gb / self.size_gb) * 100

    @property
    def compression_bar(self) -> str:
        pct = max(0, min(100, int(self.compression_ratio)))
        filled = pct // 5
        return "█" * filled + "░" * (20 - filled)

    @property
    def duration_str(self) -> str:
        if self.duration_s < 60:
            return f"{self.duration_s:.0f}s"
        if self.duration_s < 3600:
            return f"{self.duration_s / 60:.1f}m"
        return f"{self.duration_s / 3600:.1f}h"

    @property
    def size_str(self) -> str:
        return f"{self.size_gb:.1f} GB"

    @property
    def compressed_str(self) -> str:
        return f"{self.compressed_gb:.1f} GB"

    @property
    def verify_icon(self) -> str:
        return "✔️" if self.verified else "❓"


@dataclass
class RestorePoint:
    id: int = 0
    name: str = ""
    description: str = ""
    timestamp: float = 0.0
    snapshot_id: int = 0
    system_state: str = ""
    kernel_version: str = ""
    packages_hash: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))

    @property
    def age_str(self) -> str:
        age = time.time() - self.timestamp
        if age < 86400:
            return f"{age / 3600:.0f}h ago"
        return f"{age / 86400:.0f}d ago"


@dataclass
class BackupSchedule:
    name: str = ""
    frequency: ScheduleFreq = ScheduleFreq.DAILY
    paths: List[str] = field(default_factory=list)
    enabled: bool = True
    last_run: float = 0.0
    next_run: float = 0.0
    retention_count: int = 7
    mode: BackupMode = BackupMode.INCREMENTAL
    compression: bool = True
    verify_after: bool = True

    @property
    def last_run_str(self) -> str:
        if self.last_run == 0:
            return "never"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.last_run))

    @property
    def next_run_str(self) -> str:
        if self.next_run == 0:
            return "not scheduled"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.next_run))

    @property
    def status_icon(self) -> str:
        if not self.enabled:
            return "⏸"
        return "🟢"


@dataclass
class StorageInfo:
    total_gb: float = 0.0
    used_gb: float = 0.0
    backup_used_gb: float = 0.0
    backup_count: int = 0

    @property
    def free_gb(self) -> float:
        return self.total_gb - self.used_gb

    @property
    def usage_pct(self) -> float:
        if self.total_gb == 0:
            return 0.0
        return self.used_gb / self.total_gb * 100

    @property
    def usage_bar(self) -> str:
        filled = min(20, int(self.usage_pct / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def backup_pct(self) -> float:
        if self.total_gb == 0:
            return 0.0
        return self.backup_used_gb / self.total_gb * 100


class BackupManager:
    def __init__(self):
        self._snapshots: List[BackupSnapshot] = []
        self._restore_points: List[RestorePoint] = []
        self._schedules: List[BackupSchedule] = []
        self._storage = StorageInfo()
        self._selected_snapshot: int = 0
        self._view_mode: str = "snapshots"  # snapshots, schedules, restore, storage, history
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Snapshots
        self._snapshots = [
            BackupSnapshot(1, "Pre-v2.1 Update", BackupMode.FULL, BackupStatus.COMPLETED,
                           now - 86400 * 14, 45.2, 28.5, 15432,
                           ["/home", "/etc", "/var/lib"],
                           ["/var/cache", "/tmp", "*.log"],
                           320, 0, True, "Clean snapshot before v2.1.0 update"),
            BackupSnapshot(2, "Daily Backup", BackupMode.INCREMENTAL, BackupStatus.COMPLETED,
                           now - 86400 * 7, 12.8, 8.2, 2341,
                           ["/home"], [], 85, 1, True),
            BackupSnapshot(3, "Daily Backup", BackupMode.INCREMENTAL, BackupStatus.COMPLETED,
                           now - 86400 * 6, 8.5, 5.1, 1876,
                           ["/home"], [], 62, 2, True),
            BackupSnapshot(4, "Weekly Full", BackupMode.FULL, BackupStatus.COMPLETED,
                           now - 86400 * 3, 48.1, 31.2, 16789,
                           ["/home", "/etc", "/root"],
                           ["/var/cache", "/tmp", "/swapfile"],
                           445, 0, True),
            BackupSnapshot(5, "Incremental", BackupMode.INCREMENTAL, BackupStatus.COMPLETED,
                           now - 86400 * 2, 5.3, 3.1, 892,
                           ["/home"], [], 42, 4, True),
            BackupSnapshot(6, "Incremental", BackupMode.INCREMENTAL, BackupStatus.COMPLETED,
                           now - 86400, 3.8, 2.2, 567,
                           ["/home"], [], 28, 5, True),
            BackupSnapshot(7, "Today's Backup", BackupMode.DIFFERENTIAL, BackupStatus.RUNNING,
                           now - 1800, 0, 0, 0,
                           ["/home", "/etc"], [], 0, 4, False, "In progress..."),
            BackupSnapshot(8, "Failed Backup", BackupMode.INCREMENTAL, BackupStatus.FAILED,
                           now - 86400 * 5, 0, 0, 0,
                           ["/home"], [], 0, 4, False, "Error: disk space full"),
        ]
        self._snapshots.sort(key=lambda s: s.timestamp, reverse=True)

        # Restore points
        self._restore_points = [
            RestorePoint(1, "Before v2.1 Update", "Clean state before major update",
                         now - 86400 * 14, 1, "stable", "6.8.0-nyrqis", "a1b2c3d"),
            RestorePoint(2, "Post GPU Fix", "After DRM memory leak fix",
                         now - 86400 * 5, 5, "stable", "6.8.1-nyrqis", "b2c3d4e"),
            RestorePoint(3, "Pre-Hackathon", "Before weekend hackathon",
                         now - 86400 * 3, 4, "development", "6.8.2-dev", "c3d4e5f"),
        ]

        # Schedules
        self._schedules = [
            BackupSchedule("Daily Home Backup", ScheduleFreq.DAILY,
                           ["/home"], True, now - 86400, now + 3600 * 18, 7,
                           BackupMode.INCREMENTAL, True, True),
            BackupSchedule("Weekly Full System", ScheduleFreq.WEEKLY,
                           ["/home", "/etc", "/root"], True, now - 86400 * 3, now + 86400 * 4, 4,
                           BackupMode.FULL, True, True),
            BackupSchedule("Hourly Config", ScheduleFreq.HOURLY,
                           ["/etc"], False, 0, 0, 48,
                           BackupMode.SNAPSHOT, False, False),
            BackupSchedule("Monthly Archive", ScheduleFreq.MONTHLY,
                           ["/home", "/var", "/etc"], True, now - 86400 * 30, now + 86400 * 2, 12,
                           BackupMode.FULL, True, True),
            BackupSchedule("Boot Snapshot", ScheduleFreq.BOOT,
                           ["/"], True, now - 86400 * 2, 0, 5,
                           BackupMode.SNAPSHOT, True, False),
        ]

        # Storage
        self._storage = StorageInfo(
            total_gb=2000, used_gb=1450, backup_used_gb=120, backup_count=8,
        )

    @property
    def total_backup_size(self) -> float:
        return sum(s.compressed_gb for s in self._snapshots if s.status == BackupStatus.COMPLETED)

    @property
    def snapshots_today(self) -> int:
        now = time.time()
        today_start = now - (now % 86400)
        return sum(1 for s in self._snapshots if s.timestamp >= today_start)

    def select_snapshot(self, idx: int):
        if 0 <= idx < len(self._snapshots):
            self._selected_snapshot = idx

    def set_view(self, mode: str):
        if mode in ("snapshots", "schedules", "restore", "storage", "history"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS BACKUP MANAGER                                  ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        running = sum(1 for s in self._snapshots if s.status == BackupStatus.RUNNING)
        failed = sum(1 for s in self._snapshots if s.status == BackupStatus.FAILED)
        lines.append(f"  📦 {len(self._snapshots)} snapshots  💾 {self.total_backup_size:.1f}GB backed up  🔄 {running} running  ❌ {failed} failed  📅 {len(self._schedules)} schedules")
        lines.append("")

        if self._view_mode == "snapshots":
            lines.append("  ── Snapshots ──")
            for i, s in enumerate(self._snapshots[:10]):
                sel = "▶" if i == self._selected_snapshot else " "
                status = s.status.icon
                mode = s.mode.icon
                verify = s.verify_icon
                lines.append(f"  {sel}{status} {mode} {s.name} ({s.mode.value})  {s.time_str}  {s.size_str} → {s.compressed_str}")
                if s.duration_s > 0:
                    lines.append(f"      [{s.compression_bar}] {s.compression_ratio:.0f}% compressed  {s.duration_str}  {s.file_count:,} files  {verify}")

        elif self._view_mode == "schedules":
            lines.append("  ── Backup Schedules ──")
            for sched in self._schedules:
                lines.append(f"  {sched.status_icon} {sched.frequency.icon} {sched.name}")
                lines.append(f"      Mode: {sched.mode.icon} {sched.mode.value}  Retention: {sched.retention_count}  Compression: {'✓' if sched.compression else '✗'}  Verify: {'✓' if sched.verify_after else '✗'}")
                lines.append(f"      Paths: {', '.join(sched.paths)}  Last: {sched.last_run_str}  Next: {sched.next_run_str}")

        elif self._view_mode == "restore":
            lines.append("  ── Restore Points ──")
            for rp in self._restore_points:
                lines.append(f"  🔧 {rp.name} ({rp.time_str})")
                lines.append(f"      {rp.description}")
                lines.append(f"      Kernel: {rp.kernel_version}  State: {rp.system_state}  Hash: {rp.packages_hash}")

        elif self._view_mode == "storage":
            s = self._storage
            lines.append("  ── Storage Usage ──")
            lines.append(f"  Total: {s.total_gb:.0f} GB  Used: {s.used_gb:.0f} GB  Free: {s.free_gb:.0f} GB")
            lines.append(f"  [{s.usage_bar}] {s.usage_pct:.1f}%")
            lines.append(f"  Backup data: {s.backup_used_gb:.1f} GB ({s.backup_pct:.1f}% of total)")
            lines.append(f"  Snapshots: {s.backup_count}")

        elif self._view_mode == "history":
            lines.append("  ── Backup History ──")
            completed = [s for s in self._snapshots if s.status == BackupStatus.COMPLETED]
            for s in completed[:8]:
                lines.append(f"  {s.mode.icon} {s.time_str}  {s.name}  {s.size_str} → {s.compressed_str}  {s.duration_str}")

        lines.append("")
        lines.append("  [S]napshots [C]hchedules [R]estore [T]storage [H]istory [↑↓]Nav [N]ew backup")
        return lines
