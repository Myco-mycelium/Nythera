"""
Nyrqis Update Manager — system update management application.

Features:
- Check for system and package updates
- Changelog viewer per update
- Rollback to previous versions
- Delta update support
- Update scheduling
- Update history with rollback capability
- Security update priority
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional
from datetime import datetime


class UpdateType(Enum):
    SYSTEM = "system"
    SECURITY = "security"
    APPLICATION = "application"
    DRIVER = "driver"
    FIRMWARE = "firmware"


class UpdateStatus(Enum):
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    INSTALLED = "installed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


UPDATE_TYPE_ICONS = {
    UpdateType.SYSTEM: "⚙️",
    UpdateType.SECURITY: "🔒",
    UpdateType.APPLICATION: "📱",
    UpdateType.DRIVER: "🔧",
    UpdateType.FIRMWARE: "💻",
}

STATUS_ICONS = {
    UpdateStatus.AVAILABLE: "📦",
    UpdateStatus.DOWNLOADING: "⬇️",
    UpdateStatus.INSTALLING: "🔄",
    UpdateStatus.INSTALLED: "✅",
    UpdateStatus.FAILED: "❌",
    UpdateStatus.ROLLED_BACK: "⏪",
}


@dataclass
class ChangelogEntry:
    """A single changelog entry."""
    version: str
    date: str
    changes: List[str] = field(default_factory=list)
    breaking: List[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        return f"v{self.version} ({self.date})"


@dataclass
class PackageUpdate:
    """A package update."""
    name: str
    current_version: str
    new_version: str
    update_type: UpdateType = UpdateType.APPLICATION
    status: UpdateStatus = UpdateStatus.AVAILABLE
    size_kb: int = 0
    delta_size_kb: int = 0  # delta update size
    is_delta: bool = True
    # Changelog
    changelog: Optional[ChangelogEntry] = None
    # Metadata
    repository: str = "nyrqis-stable"
    priority: int = 0  # higher = more important
    dependencies: List[str] = field(default_factory=list)
    download_progress: float = 0.0
    # Rollback
    previous_versions: List[str] = field(default_factory=list)
    # Timing
    available_at: float = field(default_factory=time.time)
    installed_at: float = 0.0
    update_id: str = ""

    def __post_init__(self):
        if not self.update_id:
            self.update_id = hashlib.md5(f"{self.name}{self.new_version}".encode()).hexdigest()[:8]

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def type_icon(self) -> str:
        return UPDATE_TYPE_ICONS.get(self.update_type, "📦")

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.name} {self.current_version} → {self.new_version}"

    @property
    def size_str(self) -> str:
        if self.is_delta and self.delta_size_kb > 0:
            full = self.size_kb
            delta = self.delta_size_kb
            if full >= 1024:
                full_str = f"{full / 1024:.1f} MB"
            else:
                full_str = f"{full} KB"
            if delta >= 1024:
                delta_str = f"{delta / 1024:.1f} MB"
            else:
                delta_str = f"{delta} KB"
            return f"{delta_str} (delta) / {full_str} (full)"
        if self.size_kb >= 1024:
            return f"{self.size_kb / 1024:.1f} MB"
        return f"{self.size_kb} KB"

    @property
    def progress_bar(self) -> str:
        filled = int(self.download_progress / 100 * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def is_security(self) -> bool:
        return self.update_type == UpdateType.SECURITY

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.available_at
        if diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.available_at).strftime("%b %d")


@dataclass
class UpdateHistory:
    """An update history entry."""
    package_name: str
    old_version: str
    new_version: str
    installed_at: float = field(default_factory=time.time)
    rolled_back: bool = False
    rolled_back_at: float = 0.0
    update_id: str = ""

    def __post_init__(self):
        if not self.update_id:
            self.update_id = hashlib.md5(f"{self.package_name}{self.installed_at}".encode()).hexdigest()[:8]

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.installed_at).strftime("%Y-%m-%d %H:%M")

    @property
    def display(self) -> str:
        rb = " ⏪ rolled back" if self.rolled_back else ""
        return f"✅ {self.package_name} {self.old_version} → {self.new_version}{rb}"


class UpdateManager:
    """System update management for Nyrqis OS."""

    def __init__(self):
        self._updates: List[PackageUpdate] = []
        self._history: List[UpdateHistory] = []
        self._selected_index: int = 0
        self._view_mode: str = "updates"  # updates, history, detail
        self._auto_update: bool = True
        self._auto_download: bool = True
        self._security_only: bool = False
        self._last_check: float = time.time() - 3600
        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        self._updates = [
            PackageUpdate("nyrqis-kernel", "6.11.0", "6.12.0", UpdateType.SYSTEM,
                          UpdateStatus.AVAILABLE, 85000, 12000, True,
                          ChangelogEntry("6.12.0", "2026-09-03",
                                         ["NVIDIA 560 driver support", "AMD RDNA4 improvements",
                                          "Bluetooth 5.4 fix", "USB4 stability"],
                                         ["Removed deprecated io_uring syscalls"]),
                          priority=10, previous_versions=["6.10.5", "6.11.0"]),
            PackageUpdate("nyrqis-compositor", "2.4.0", "2.5.0", UpdateType.SYSTEM,
                          UpdateStatus.AVAILABLE, 18000, 3200, True,
                          ChangelogEntry("2.5.0", "2026-09-02",
                                         ["HDR10 support", "VRR (Variable Refresh Rate)",
                                          "Per-monitor color profile", "Fractional scaling fix"],
                                         []),
                          priority=8),
            PackageUpdate("firefox", "130.0", "131.0", UpdateType.APPLICATION,
                          UpdateStatus.AVAILABLE, 65000, 8500, True,
                          ChangelogEntry("131.0", "2026-09-01",
                                         ["Enhanced tracking protection v3", "WebGPU improvements",
                                          "Picture-in-picture fixes", "Memory usage reduction"],
                                         []),
                          priority=5, previous_versions=["129.0", "130.0"]),
            PackageUpdate("openssl", "3.3.1", "3.3.2", UpdateType.SECURITY,
                          UpdateStatus.AVAILABLE, 2500, 800, True,
                          ChangelogEntry("3.3.2", "2026-09-01",
                                         ["Fix CVE-2026-1234: X.509 buffer overflow",
                                          "Fix CVE-2026-1235: TLS handshake DoS"],
                                         []),
                          priority=15),
            PackageUpdate("nvidia-driver", "550.100", "560.50", UpdateType.DRIVER,
                          UpdateStatus.AVAILABLE, 120000, 25000, True,
                          ChangelogEntry("560.50", "2026-08-30",
                                         ["RDNA4 support", "CUDA 12.6 compatibility",
                                          "Wayland explicit sync", "VRAM reporting fix"],
                                         []),
                          priority=7),
            PackageUpdate("linux-firmware", "20260801", "20260901", UpdateType.FIRMWARE,
                          UpdateStatus.AVAILABLE, 45000, 0, False,
                          ChangelogEntry("20260901", "2026-09-01",
                                         ["Intel WiFi 7 firmware update", "AMD GPU microcode",
                                          "Realtek NIC firmware"]),
                          priority=6),
            PackageUpdate("code", "1.92.0", "1.93.0", UpdateType.APPLICATION,
                          UpdateStatus.AVAILABLE, 95000, 15000, True,
                          ChangelogEntry("1.93.0", "2026-08-28",
                                         ["New terminal profile system", "AI code completion",
                                          "Performance improvements"]),
                          priority=3),
            PackageUpdate("nyrkis-base", "1.0.0", "1.0.1", UpdateType.SYSTEM,
                          UpdateStatus.INSTALLED, 5000, 1200, True,
                          installed_at=now - 86400),
        ]

        self._history = [
            UpdateHistory("nyrqis-kernel", "6.10.5", "6.11.0", now - 604800),
            UpdateHistory("nyrqis-compositor", "2.3.2", "2.4.0", now - 604800),
            UpdateHistory("firefox", "129.0", "130.0", now - 432000),
            UpdateHistory("nyrkis-base", "1.0.0", "1.0.1", now - 86400),
            UpdateHistory("code", "1.91.0", "1.92.0", now - 259200),
        ]

    def install_update(self, index: int) -> bool:
        if 0 <= index < len(self._updates):
            upd = self._updates[index]
            if upd.status == UpdateStatus.AVAILABLE:
                upd.status = UpdateStatus.INSTALLED
                upd.installed_at = time.time()
                self._history.insert(0, UpdateHistory(
                    upd.name, upd.current_version, upd.new_version, time.time()))
                return True
        return False

    def rollback_update(self, index: int) -> bool:
        if 0 <= index < len(self._history):
            hist = self._history[index]
            if not hist.rolled_back and hist.old_version:
                hist.rolled_back = True
                hist.rolled_back_at = time.time()
                return True
        return False

    def install_all(self) -> int:
        count = 0
        for i, upd in enumerate(self._updates):
            if upd.status == UpdateStatus.AVAILABLE:
                self.install_update(i)
                count += 1
        return count

    @property
    def available_count(self) -> int:
        return sum(1 for u in self._updates if u.status == UpdateStatus.AVAILABLE)

    @property
    def security_count(self) -> int:
        return sum(1 for u in self._updates if u.is_security and u.status == UpdateStatus.AVAILABLE)

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        items = self._get_display_list()
        self._selected_index = min(len(items) - 1, self._selected_index + 1)

    def get_selected_item(self):
        items = self._get_display_list()
        if 0 <= self._selected_index < len(items):
            return items[self._selected_index]
        return None

    def _get_display_list(self) -> list:
        if self._view_mode == "history":
            return self._history
        return self._updates

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    def render_updates(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" ⬆️  System Updates ({self.available_count} available, {self.security_count} security)")
        lines.append("─" * width)
        for i, upd in enumerate(self._updates):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {upd.type_icon} {upd.display}")
            lines.append(f"   {upd.update_type.value.title()} | {upd.size_str} | {upd.time_ago}")
            if upd.changelog:
                lines.append(f"   📝 {upd.changelog.display}")
            if upd.status == UpdateStatus.DOWNLOADING:
                lines.append(f"   [{upd.progress_bar}] {upd.download_progress:.0f}%")
            lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Install  A:Install all  D:Details  H:History")
        return lines

    def render_history(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 📜 Update History ({len(self._history)} entries)")
        lines.append("─" * width)
        for i, hist in enumerate(self._history):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {hist.display}")
            lines.append(f"   Installed: {hist.time_str}")
            lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  R:Rollback  Esc:Back")
        return lines

    def render_detail(self, width: int = 70) -> List[str]:
        upd = self.get_selected_item()
        if not upd:
            return ["No update selected"]
        lines = []
        lines.append(f" {upd.type_icon} {upd.name}")
        lines.append("─" * width)
        lines.append(f" Current:   {upd.current_version}")
        lines.append(f" New:       {upd.new_version}")
        lines.append(f" Type:      {upd.update_type.value.title()}")
        lines.append(f" Size:      {upd.size_str}")
        lines.append(f" Repo:      {upd.repository}")
        if upd.changelog:
            lines.append("")
            lines.append(f" 📝 Changelog — {upd.changelog.display}:")
            for change in upd.changelog.changes:
                lines.append(f"   ✨ {change}")
            for breaking in upd.changelog.breaking:
                lines.append(f"   ⚠️  {breaking}")
        if upd.previous_versions:
            lines.append("")
            lines.append(f" Previous:  {', '.join(upd.previous_versions)}")
        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {"history": self.render_history, "detail": self.render_detail}
        renderer = renderers.get(self._view_mode, self.render_updates)
        return renderer(width)

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "history":
            if key == "Escape":
                self.set_view("updates")
                return "back"
            if key == "ArrowUp":
                self.select_up()
                return "select_up"
            if key == "ArrowDown":
                self.select_down()
                return "select_down"
            if key == "r":
                return "rollback" if self.rollback_update(self._selected_index) else "rollback_failed"
            return None
        if self._view_mode == "detail":
            if key == "Escape":
                self.set_view("updates")
                return "back"
            return None
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        if key == "ArrowDown":
            self.select_down()
            return "select_down"
        if key == "Enter":
            return "install" if self.install_update(self._selected_index) else "install_failed"
        if key == "a":
            count = self.install_all()
            return "install_all" if count > 0 else "no_updates"
        if key == "d":
            self.set_view("detail")
            return "detail"
        if key == "h":
            self.set_view("history")
            return "history"
        return None
