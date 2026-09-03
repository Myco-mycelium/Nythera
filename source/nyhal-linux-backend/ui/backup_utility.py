"""
Nyrqis Backup — system backup and restore utility.

Features:
- Backup profiles with source/destination configuration
- Scheduled backups (daily, weekly, monthly)
- Snapshot management with size tracking
- Incremental and full backup modes
- Restore from snapshot with file selection
- Backup history with status and duration
- Space usage analysis
- Exclude patterns for large/unnecessary files
"""

import os
import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Set
from datetime import datetime, timedelta


# ─── Data Classes ────────────────────────────────────────────────────────


class BackupMode(Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    DIFFERENTIAL = "differential"


class BackupStatus(Enum):
    COMPLETED = "completed"
    RUNNING = "running"
    FAILED = "failed"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class ScheduleFrequency(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    MANUAL = "manual"


STATUS_ICONS = {
    BackupStatus.COMPLETED: "✅",
    BackupStatus.RUNNING: "🔄",
    BackupStatus.FAILED: "❌",
    BackupStatus.SCHEDULED: "📅",
    BackupStatus.CANCELLED: "🚫",
}


@dataclass
class Snapshot:
    """A backup snapshot."""
    name: str
    mode: BackupMode = BackupMode.FULL
    status: BackupStatus = BackupStatus.COMPLETED
    size_bytes: int = 0
    file_count: int = 0
    created: float = field(default_factory=time.time)
    duration_seconds: float = 0.0
    source: str = ""
    destination: str = ""
    errors: int = 0
    snapshot_id: str = ""

    def __post_init__(self):
        if not self.snapshot_id:
            self.snapshot_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def size_str(self) -> str:
        b = self.size_bytes
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        return f"{b / (1024 * 1024 * 1024):.2f} GB"

    @property
    def duration_str(self) -> str:
        if self.duration_seconds < 60:
            return f"{self.duration_seconds:.0f}s"
        m = int(self.duration_seconds // 60)
        s = int(self.duration_seconds % 60)
        return f"{m}m {s}s"

    @property
    def date_str(self) -> str:
        return datetime.fromtimestamp(self.created).strftime("%Y-%m-%d %H:%M")

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.created
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        elif diff < 604800:
            return f"{int(diff // 86400)}d ago"
        return datetime.fromtimestamp(self.created).strftime("%b %d")

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")


@dataclass
class BackupProfile:
    """A backup profile with source/destination configuration."""
    name: str
    source_paths: List[str] = field(default_factory=list)
    destination: str = "/backup"
    mode: BackupMode = BackupMode.INCREMENTAL
    schedule: ScheduleFrequency = ScheduleFrequency.MANUAL
    exclude_patterns: List[str] = field(default_factory=list)
    include_hidden: bool = False
    compress: bool = True
    encrypt: bool = False
    enabled: bool = True
    last_run: float = 0.0
    profile_id: str = ""

    def __post_init__(self):
        if not self.profile_id:
            self.profile_id = hashlib.md5(f"{self.name}{time.time()}".encode()).hexdigest()[:8]

    @property
    def schedule_str(self) -> str:
        return self.schedule.value.title()

    @property
    def last_run_str(self) -> str:
        if self.last_run <= 0:
            return "Never"
        diff = time.time() - self.last_run
        if diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return f"{int(diff // 86400)}d ago"

    @property
    def source_str(self) -> str:
        return ", ".join(self.source_paths) if self.source_paths else "None"


@dataclass
class ExcludePattern:
    """An exclusion pattern for backups."""
    pattern: str
    description: str = ""
    is_builtin: bool = False


# ─── Backup Utility ──────────────────────────────────────────────────────


class BackupUtility:
    """
    Backup and restore utility for Nyrqis OS.

    Manages backup profiles, snapshots, and restore operations.
    """

    def __init__(self):
        self._profiles: List[BackupProfile] = []
        self._snapshots: List[Snapshot] = []
        self._selected_index: int = 0
        self._view_mode: str = "profiles"  # profiles, snapshots, restore
        self._current_profile: Optional[BackupProfile] = None
        self._selected_snapshot: Optional[Snapshot] = None
        self._running_backup: Optional[Snapshot] = None

        # Default exclude patterns
        self._default_excludes = [
            ExcludePattern("*.tmp", "Temporary files", True),
            ExcludePattern("*.log", "Log files", True),
            ExcludePattern("__pycache__", "Python cache", True),
            ExcludePattern("node_modules", "Node.js dependencies", True),
            ExcludePattern(".git/objects", "Git objects", True),
            ExcludePattern("*.swp", "Vim swap files", True),
            ExcludePattern(".cache/", "Application caches", True),
            ExcludePattern("*.pyc", "Python bytecode", True),
        ]

        # Callbacks
        self._on_backup_complete: List[Callable] = []

        # Init sample data
        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()

        # Profiles
        self._profiles = [
            BackupProfile(
                "System Config", ["/etc", "/boot/config"],
                "/backup/system", BackupMode.FULL, ScheduleFrequency.WEEKLY,
                ["*.log", "*.tmp"], False, True, True,
                profile_id="prof_sys",
            ),
            BackupProfile(
                "Home Directory", ["/home/user"],
                "/backup/home", BackupMode.INCREMENTAL, ScheduleFrequency.DAILY,
                ["__pycache__", "node_modules", ".cache"], True, True, False,
                last_run=now - 86400, profile_id="prof_home",
            ),
            BackupProfile(
                "Documents", ["/home/user/Documents", "/home/user/Notes"],
                "/backup/docs", BackupMode.INCREMENTAL, ScheduleFrequency.DAILY,
                ["*.tmp"], False, True, False,
                last_run=now - 3600 * 5, profile_id="prof_docs",
            ),
            BackupProfile(
                "Nyrqis Source", ["/opt/nyrqis", "/home/user/projects"],
                "/backup/code", BackupMode.INCREMENTAL, ScheduleFrequency.WEEKLY,
                ["__pycache__", "node_modules", ".git/objects", "*.pyc"],
                True, True, False,
                last_run=now - 86400 * 3, profile_id="prof_code",
            ),
        ]

        # Snapshots
        for i in range(12):
            day_offset = i * 7
            mode = BackupMode.FULL if i % 4 == 0 else BackupMode.INCREMENTAL
            size = 500 * 1024 * 1024 if mode == BackupMode.FULL else 50 * 1024 * 1024 * (12 - i)
            self._snapshots.append(Snapshot(
                name=f"backup_{datetime.fromtimestamp(now - day_offset * 86400).strftime('%Y%m%d')}",
                mode=mode,
                status=BackupStatus.COMPLETED,
                size_bytes=size,
                file_count=1000 + i * 200,
                created=now - day_offset * 86400,
                duration_seconds=120 + i * 15,
                source=self._profiles[i % len(self._profiles)].name,
                destination=self._profiles[i % len(self._profiles)].destination,
                errors=0 if i > 2 else 1,
            ))

    # ── Profile Management ────────────────────────────────────────────

    def create_profile(self, name: str, source_paths: List[str] = None,
                       destination: str = "/backup") -> BackupProfile:
        profile = BackupProfile(
            name=name,
            source_paths=source_paths or [],
            destination=destination,
        )
        self._profiles.append(profile)
        return profile

    def delete_profile(self, profile_id: str) -> bool:
        for i, p in enumerate(self._profiles):
            if p.profile_id == profile_id:
                self._profiles.pop(i)
                return True
        return False

    def get_profile(self, profile_id: str) -> Optional[BackupProfile]:
        for p in self._profiles:
            if p.profile_id == profile_id:
                return p
        return None

    @property
    def profiles(self) -> List[BackupProfile]:
        return list(self._profiles)

    # ── Snapshot Management ───────────────────────────────────────────

    def get_snapshots(self, profile_id: str = None) -> List[Snapshot]:
        if profile_id:
            profile = self.get_profile(profile_id)
            if profile:
                return [s for s in self._snapshots if s.source == profile.name]
        return sorted(self._snapshots, key=lambda s: -s.created)

    def get_snapshot(self, snapshot_id: str) -> Optional[Snapshot]:
        for s in self._snapshots:
            if s.snapshot_id == snapshot_id:
                return s
        return None

    def delete_snapshot(self, snapshot_id: str) -> bool:
        for i, s in enumerate(self._snapshots):
            if s.snapshot_id == snapshot_id:
                self._snapshots.pop(i)
                return True
        return False

    # ── Backup Operations ─────────────────────────────────────────────

    def start_backup(self, profile_id: str) -> Optional[Snapshot]:
        """Start a backup for a profile."""
        profile = self.get_profile(profile_id)
        if not profile:
            return None

        snapshot = Snapshot(
            name=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            mode=profile.mode,
            status=BackupStatus.RUNNING,
            source=profile.name,
            destination=profile.destination,
        )
        self._snapshots.insert(0, snapshot)
        self._running_backup = snapshot

        # Simulate completion
        snapshot.status = BackupStatus.COMPLETED
        snapshot.size_bytes = 100 * 1024 * 1024  # 100MB simulated
        snapshot.file_count = 500
        snapshot.duration_seconds = 45.0
        snapshot.created = time.time()
        profile.last_run = time.time()
        self._running_backup = None

        self._notify("complete", snapshot)
        return snapshot

    def restore_snapshot(self, snapshot_id: str, dest: str = "/") -> bool:
        """Restore from a snapshot."""
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot and snapshot.status == BackupStatus.COMPLETED:
            return True
        return False

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def is_backup_running(self) -> bool:
        return self._running_backup is not None

    # ── Space Analysis ────────────────────────────────────────────────

    def total_backup_size(self) -> int:
        return sum(s.size_bytes for s in self._snapshots)

    def total_snapshot_count(self) -> int:
        return len(self._snapshots)

    def profile_snapshot_count(self, profile_id: str) -> int:
        profile = self.get_profile(profile_id)
        if profile:
            return len([s for s in self._snapshots if s.source == profile.name])
        return 0

    def oldest_snapshot(self) -> Optional[Snapshot]:
        if self._snapshots:
            return min(self._snapshots, key=lambda s: s.created)
        return None

    def newest_snapshot(self) -> Optional[Snapshot]:
        if self._snapshots:
            return max(self._snapshots, key=lambda s: s.created)
        return None

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        if self._view_mode == "profiles":
            self._selected_index = min(len(self._profiles) - 1, self._selected_index + 1)
        elif self._view_mode == "snapshots":
            self._selected_index = min(len(self._snapshots) - 1, self._selected_index + 1)

    def open_selected(self) -> None:
        if self._view_mode == "profiles":
            if 0 <= self._selected_index < len(self._profiles):
                self._current_profile = self._profiles[self._selected_index]
                self._view_mode = "snapshots"
                self._selected_index = 0
        elif self._view_mode == "snapshots":
            snapshots = self.get_snapshots()
            if 0 <= self._selected_index < len(snapshots):
                self._selected_snapshot = snapshots[self._selected_index]

    # ── Rendering ─────────────────────────────────────────────────────

    def render_profiles(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 💾 Backup Manager")
        lines.append("─" * width)

        # Summary
        total_size = self.total_backup_size()
        total_snap = self.total_snapshot_count()
        lines.append(f" 📊 {len(self._profiles)} profiles · {total_snap} snapshots · {total_size / (1024*1024):.0f} MB total")
        lines.append("─" * width)

        for i, profile in enumerate(self._profiles):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {profile.name}")
            lines.append(f"   📁 {profile.source_str}")
            lines.append(f"   💾 {profile.destination} ({profile.mode.value})")
            lines.append(f"   📅 {profile.schedule_str} · Last: {profile.last_run_str}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:View Snapshots  N:New  B:Backup")
        return lines

    def render_snapshots(self, width: int = 60) -> List[str]:
        lines = []
        profile = self._current_profile
        name = profile.name if profile else "All Snapshots"
        lines.append(f" 💾 Snapshots — {name}")
        lines.append("─" * width)

        snapshots = self.get_snapshots(profile.profile_id if profile else None)
        if not snapshots:
            lines.append("  No snapshots yet.")
        else:
            for i, snap in enumerate(snapshots):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {snap.status_icon} {snap.name}")
                lines.append(f"   {snap.mode.value.title()} · {snap.size_str} · {snap.file_count} files · {snap.duration_str}")
                lines.append(f"   {snap.date_str} ({snap.time_ago})")
                if snap.errors:
                    lines.append(f"   ⚠️  {snap.errors} errors")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Restore  B:Backup  Del:Delete  Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        if self._view_mode == "snapshots":
            return self.render_snapshots(width)
        return self.render_profiles(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.open_selected()
            return "open"
        elif key == "Escape":
            if self._view_mode == "snapshots":
                self._view_mode = "profiles"
                self._selected_index = 0
                return "back"
        elif key == "b":
            if self._view_mode == "profiles" and 0 <= self._selected_index < len(self._profiles):
                self.start_backup(self._profiles[self._selected_index].profile_id)
            return "backup"
        elif key == "r":
            if self._selected_snapshot:
                self.restore_snapshot(self._selected_snapshot.snapshot_id)
            return "restore"
        elif key == "Delete":
            if self._selected_snapshot:
                self.delete_snapshot(self._selected_snapshot.snapshot_id)
                self._selected_snapshot = None
            return "delete"
        return None

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_backup_complete(self, cb: Callable) -> None:
        self._on_backup_complete.append(cb)

    def _notify(self, event: str, *args) -> None:
        if event == "complete":
            for cb in self._on_backup_complete:
                try:
                    cb(*args)
                except Exception:
                    pass
