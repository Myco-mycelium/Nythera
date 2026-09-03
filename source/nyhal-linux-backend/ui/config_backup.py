from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import hashlib


class ConfigType(Enum):
    SYSTEM = "system"
    NETWORK = "network"
    DISPLAY = "display"
    AUDIO = "audio"
    SERVICE = "service"
    SECURITY = "security"
    APPLICATION = "application"
    CUSTOM = "custom"


class BackupStatus(Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    IN_PROGRESS = "in-progress"


class DiffLineType(Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


@dataclass
class ConfigFile:
    path: str
    config_type: ConfigType
    content: str
    checksum: str
    size_bytes: int
    last_modified: float
    is_critical: bool = False
    owner: str = "root"
    permissions: str = "644"

    @property
    def filename(self) -> str:
        return self.path.split("/")[-1]

    @property
    def display_size(self) -> str:
        if self.size_bytes >= 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes} B"

    @property
    def age_display(self) -> str:
        age = int((time.time() - self.last_modified) / 86400)
        if age == 0:
            return "today"
        return f"{age}d ago"


@dataclass
class BackupSnapshot:
    name: str
    timestamp: float
    config_files: list = field(default_factory=list)
    status: BackupStatus = BackupStatus.COMPLETE
    size_bytes: int = 0
    description: str = ""
    is_auto: bool = False

    @property
    def display_size(self) -> str:
        if self.size_bytes >= 1024 * 1024:
            return f"{self.size_bytes / (1024 * 1024):.1f} MB"
        if self.size_bytes >= 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes} B"

    @property
    def age_display(self) -> str:
        age = int((time.time() - self.timestamp) / 86400)
        if age == 0:
            return "today"
        if age == 1:
            return "yesterday"
        return f"{age}d ago"

    @property
    def file_count(self) -> int:
        return len(self.config_files)


@dataclass
class DiffEntry:
    file_path: str
    old_content: str
    new_content: str
    lines: list = field(default_factory=list)
    added: int = 0
    removed: int = 0

    @property
    def has_changes(self) -> bool:
        return self.added > 0 or self.removed > 0


class ConfigBackup:
    def __init__(self):
        self._config_files: list[ConfigFile] = []
        self._snapshots: list[BackupSnapshot] = []
        self._selected_file: int = 0
        self._selected_snapshot: int = 0
        self._auto_backup: bool = True
        self._auto_interval_hours: int = 6
        self._max_snapshots: int = 20
        self._compression: bool = True
        self._encryption: bool = False
        self._view: str = "files"
        self._diff_entries: list[DiffEntry] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        self._config_files = [
            ConfigFile("/etc/nyrqis/compositor.toml", ConfigType.SYSTEM, "[compositor]\nvsync = true\nrefresh_rate = 144\nbackend = \"vulkan\"\n\n[render]\nvsync_method = \"mailbox\"\ntriple_buffer = true", hashlib.md5(b"comp").hexdigest(), 245, now - 86400, is_critical=True),
            ConfigFile("/etc/nyrqis/shell.toml", ConfigType.APPLICATION, "[shell]\ntheme = \"dracula\"\nfont = \"JetBrains Mono\"\nfont_size = 14\n\n[dock]\nposition = \"bottom\"\nauto_hide = false", hashlib.md5(b"shell").hexdigest(), 189, now - 3600),
            ConfigFile("/etc/NetworkManager/NetworkManager.conf", ConfigType.NETWORK, "[main]\nplugins=keyfile\n\n[logging]\nlevel=INFO\nrate-limit=10", hashlib.md5(b"nm").hexdigest(), 112, now - 86400 * 7, is_critical=True),
            ConfigFile("/etc/pipewire/pipewire.conf", ConfigType.AUDIO, "context.properties = {\n    log.level = 0\n    default.clock.rate = 48000\n    default.clock.quantum = 1024\n}", hashlib.md5(b"pw").hexdigest(), 156, now - 86400 * 3),
            ConfigFile("/etc/systemd/system/nyrqis-compositor.service", ConfigType.SERVICE, "[Unit]\nDescription=Nyrqis Compositor\nAfter=graphical-session.target\n\n[Service]\nExecStart=/usr/bin/nyrqis-compositor\nRestart=always\n\n[Install]\nWantedBy=graphical-session.target", hashlib.md5(b"svc").hexdigest(), 234, now - 86400 * 14, is_critical=True),
            ConfigFile("/etc/nyrqis/security.toml", ConfigType.SECURITY, "[firewall]\nenabled = true\ndefault_policy = \"deny\"\n\n[auth]\nsudo_timeout = 5\nlockout_attempts = 3", hashlib.md5(b"sec").hexdigest(), 134, now - 86400 * 5),
            ConfigFile("/etc/X11/xorg.conf.d/10-nvidia.conf", ConfigType.DISPLAY, 'Section "Device"\n    Identifier "NVIDIA"\n    Driver "nvidia"\n    Option "Coolbits" "28"\n    Option "TripleBuffer" "True"\nEndSection', hashlib.md5(b"xorg").hexdigest(), 178, now - 86400 * 30),
        ]

        self._snapshots = [
            BackupSnapshot("Pre-kernel-update", now - 86400 * 2, self._config_files, BackupStatus.COMPLETE, 1250, "Before kernel 6.12 update", False),
            BackupSnapshot("Auto-backup", now - 86400, self._config_files[:5], BackupStatus.COMPLETE, 890, "", True),
            BackupSnapshot("Post-driver-install", now - 86400 * 7, self._config_files, BackupStatus.COMPLETE, 1250, "After NVIDIA 560 driver install"),
            BackupSnapshot("Fresh-install", now - 86400 * 30, self._config_files[:4], BackupStatus.PARTIAL, 567, "Initial system config snapshot"),
        ]

        self._diff_entries = [
            DiffEntry("/etc/nyrqis/compositor.toml", "vsync = true\nrefresh_rate = 120", "vsync = true\nrefresh_rate = 144", added=1, removed=1),
            DiffEntry("/etc/nyrqis/shell.toml", "font_size = 12", "font_size = 14", added=1, removed=1),
        ]

    @property
    def selected_file(self) -> Optional[ConfigFile]:
        if 0 <= self._selected_file < len(self._config_files):
            return self._config_files[self._selected_file]
        return None

    @property
    def selected_snapshot(self) -> Optional[BackupSnapshot]:
        if 0 <= self._selected_snapshot < len(self._snapshots):
            return self._snapshots[self._selected_snapshot]
        return None

    @property
    def total_files(self) -> int:
        return len(self._config_files)

    @property
    def total_snapshots(self) -> int:
        return len(self._snapshots)

    @property
    def total_size(self) -> int:
        return sum(f.size_bytes for f in self._config_files)

    @property
    def critical_files(self) -> int:
        return sum(1 for f in self._config_files if f.is_critical)

    @property
    def type_counts(self) -> dict:
        counts = {}
        for f in self._config_files:
            counts[f.config_type.value] = counts.get(f.config_type.value, 0) + 1
        return counts

    def select_file(self, idx: int):
        if 0 <= idx < len(self._config_files):
            self._selected_file = idx

    def select_snapshot(self, idx: int):
        if 0 <= idx < len(self._snapshots):
            self._selected_snapshot = idx

    def create_snapshot(self, name: str, description: str = "") -> BackupSnapshot:
        snap = BackupSnapshot(name, time.time(), list(self._config_files), BackupStatus.COMPLETE, self.total_size, description)
        self._snapshots.insert(0, snap)
        return snap

    def restore_snapshot(self, idx: int) -> bool:
        if 0 <= idx < len(self._snapshots):
            return True
        return False

    def compare_snapshots(self, idx_a: int, idx_b: int) -> list:
        diffs = []
        if idx_a < len(self._snapshots) and idx_b < len(self._snapshots):
            a_files = {f.path: f for f in self._snapshots[idx_a].config_files}
            b_files = {f.path: f for f in self._snapshots[idx_b].config_files}
            for path in set(list(a_files.keys()) + list(b_files.keys())):
                a = a_files.get(path)
                b = b_files.get(path)
                if a and b and a.checksum != b.checksum:
                    diffs.append(DiffEntry(path, a.content, b.content, added=1, removed=1))
                elif a and not b:
                    diffs.append(DiffEntry(path, a.content, "", removed=1))
                elif not a and b:
                    diffs.append(DiffEntry(path, "", b.content, added=1))
        return diffs

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                  NYRQIS CONFIG BACKUP MANAGER                              ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        auto = "🟢 ON" if self._auto_backup else "🔴 OFF"
        lines.append(f"  Auto-backup: {auto}  Interval: {self._auto_interval_hours}h  Max: {self._max_snapshots}")
        lines.append(f"  Files: {self.total_files} ({self.critical_files} critical)  Size: {sum(f.size_bytes for f in self._config_files) / 1024:.1f} KB  Snapshots: {self.total_snapshots}")
        lines.append("")
        lines.append("  ── Config Files ──")
        for i, f in enumerate(self._config_files):
            sel = "▶" if i == self._selected_file else " "
            crit = "🔴" if f.is_critical else "  "
            type_icons = {"system": "⚙️", "network": "🌐", "display": "🖥️", "audio": "🔊", "service": "🔧", "security": "🔒", "application": "📱", "custom": "📄"}
            icon = type_icons.get(f.config_type.value, "📄")
            lines.append(f"  {sel}{crit} {icon} {f.filename}  {f.config_type.value}  {f.display_size}  {f.age_display}")
        lines.append("")
        lines.append("  ── Snapshots ──")
        for i, s in enumerate(self._snapshots):
            sel = "▶" if i == self._selected_snapshot else " "
            auto = " 🔄" if s.is_auto else ""
            status = {"complete": "✅", "partial": "⚠️", "failed": "❌", "in-progress": "⏳"}.get(s.status.value, "?")
            lines.append(f"  {sel}{status} {s.name}{auto}  {s.age_display}  {s.file_count} files  {s.display_size}")
            if s.description:
                lines.append(f"    {s.description}")
        lines.append("")
        lines.append("  ── Types ──")
        for t, count in self.type_counts.items():
            lines.append(f"  {t}: {count}")
        lines.append("")
        lines.append("  [B]ackup  [R]estore  [D]iff  [C]ompare  [E]dit  [A]uto  [S]earch")
        return lines

    def render_file_detail(self) -> list:
        f = self.selected_file
        if not f:
            return ["  No file selected"]
        lines = []
        lines.append(f"  ── {f.filename} ──")
        lines.append(f"  Path: {f.path}")
        lines.append(f"  Type: {f.config_type.value}  Critical: {'Yes' if f.is_critical else 'No'}")
        lines.append(f"  Size: {f.display_size}  Permissions: {f.permissions}  Owner: {f.owner}")
        lines.append(f"  Checksum: {f.checksum}")
        lines.append(f"  Modified: {f.age_display}")
        lines.append("")
        lines.append("  ── Content ──")
        for line in f.content.split("\n"):
            lines.append(f"  │ {line}")
        return lines

    def render_diff(self) -> list:
        lines = []
        lines.append("  ── Configuration Diff ──")
        lines.append("")
        for d in self._diff_entries:
            lines.append(f"  📄 {d.file_path}")
            if d.added:
                lines.append(f"    +{d.new_content}")
            if d.removed:
                lines.append(f"    -{d.old_content}")
            lines.append("")
        return lines
