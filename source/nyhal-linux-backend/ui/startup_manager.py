"""
Nyrqis OS - Startup Manager
Autostart apps, boot order, and boot time tracking.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class StartupType(Enum):
    AUTOSTART = "autostart"
    SYSTEMD = "systemd"
    CRON = "cron"
    XDG = "xdg"
    RC_LOCAL = "rc_local"


class StartupStatus(Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    FAILED = "failed"
    RUNNING = "running"
    PENDING = "pending"


@dataclass
class AutostartEntry:
    name: str
    command: str = ""
    description: str = ""
    startup_type: StartupType = StartupType.XDG
    status: StartupStatus = StartupStatus.ENABLED
    icon: str = ""
    enabled: bool = True
    delay_ms: int = 0
    run_once: bool = False
    desktop_file: str = ""
    autostart_condition: str = ""
    last_run: float = 0.0
    boot_time_ms: float = 0.0
    category: str = ""

    @property
    def status_icon(self) -> str:
        icons = {
            StartupStatus.ENABLED: "🟢", StartupStatus.DISABLED: "⚪",
            StartupStatus.FAILED: "❌", StartupStatus.RUNNING: "🔄",
            StartupStatus.PENDING: "⏳",
        }
        return icons.get(self.status, "?")

    @property
    def type_icon(self) -> str:
        icons = {
            StartupType.AUTOSTART: "🚀", StartupType.SYSTEMD: "🔧",
            StartupType.CRON: "⏰", StartupType.XDG: "📋",
            StartupType.RC_LOCAL: "📄",
        }
        return icons.get(self.startup_type, "?")


@dataclass
class BootEntry:
    kernel: str = ""
    initrd: str = ""
    options: str = ""
    timestamp: float = 0.0
    is_default: bool = False
    is_recovery: bool = False
    kernel_version: str = ""

    @property
    def date_display(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))


@dataclass
class BootTimeRecord:
    timestamp: float = 0.0
    total_ms: float = 0.0
    kernel_ms: float = 0.0
    initrd_ms: float = 0.0
    userspace_ms: float = 0.0
    firmware_ms: float = 0.0
    bootloader_ms: float = 0.0

    @property
    def total_seconds(self) -> float:
        return self.total_ms / 1000

    @property
    def total_display(self) -> str:
        s = self.total_seconds
        if s < 1:
            return f"{self.total_ms:.0f}ms"
        return f"{s:.1f}s"

    @property
    def breakdown(self) -> Dict[str, str]:
        return {
            "Firmware": f"{self.firmware_ms:.0f}ms",
            "Bootloader": f"{self.bootloader_ms:.0f}ms",
            "Kernel": f"{self.kernel_ms:.0f}ms",
            "Initrd": f"{self.initrd_ms:.0f}ms",
            "Userspace": f"{self.userspace_ms:.0f}ms",
        }

    @property
    def bar(self) -> str:
        if self.total_ms == 0:
            return ""
        parts = []
        for ms, char in [(self.firmware_ms, "F"), (self.bootloader_ms, "B"),
                          (self.kernel_ms, "K"), (self.initrd_ms, "I"),
                          (self.userspace_ms, "U")]:
            width = int((ms / self.total_ms) * 30)
            parts.append(char * max(1, width) if width > 0 else "")
        return "".join(parts)


@dataclass
class SystemdService:
    name: str = ""
    description: str = ""
    load_state: str = "loaded"
    active_state: str = "active"
    sub_state: str = "running"
    enabled: bool = True
    type: str = "simple"
    pid: int = 0
    memory_bytes: int = 0
    cpu_usage_ns: int = 0

    @property
    def active_icon(self) -> str:
        icons = {"active": "🟢", "inactive": "⚪", "failed": "🔴", "activating": "🟡"}
        return icons.get(self.active_state, "?")

    @property
    def memory_display(self) -> str:
        mb = self.memory_bytes / (1024 * 1024)
        if mb < 1:
            return f"{self.memory_bytes / 1024:.0f} KB"
        return f"{mb:.1f} MB"


class StartupManager:
    def __init__(self):
        self.autostart_entries: List[AutostartEntry] = []
        self.boot_entries: List[BootEntry] = []
        self.boot_times: List[BootTimeRecord] = []
        self.services: List[SystemdService] = []
        self.show_splash: bool = True
        self.splash_timeout_s: int = 5
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.autostart_entries = [
            AutostartEntry(name="Nyrqis Compositor", command="/usr/bin/nyrqis-compositor --wayland",
                            description="Nyrqis OS compositor", startup_type=StartupType.SYSTEMD,
                            status=StartupStatus.RUNNING, icon="🍄", enabled=True,
                            boot_time_ms=1200, category="Desktop"),
            AutostartEntry(name="Nyrqis Shell", command="/usr/bin/nyrqis-shell",
                            description="Desktop shell and panels", startup_type=StartupType.SYSTEMD,
                            status=StartupStatus.RUNNING, icon="🐚", enabled=True,
                            delay_ms=500, boot_time_ms=800, category="Desktop"),
            AutostartEntry(name="Wayland Bridge", command="/usr/libexec/wayland-bridge",
                            description="XWayland compatibility layer", startup_type=StartupType.SYSTEMD,
                            status=StartupStatus.RUNNING, icon="🌉", enabled=True,
                            delay_ms=1000, boot_time_ms=300, category="Desktop"),
            AutostartEntry(name="PulseAudio", command="/usr/bin/pulseaudio --daemonize",
                            description="Audio server", startup_type=StartupType.SYSTEMD,
                            status=StartupStatus.RUNNING, icon="🔊", enabled=True,
                            boot_time_ms=150, category="System"),
            AutostartEntry(name="NetworkManager", command="/usr/sbin/NetworkManager",
                            description="Network connection manager", startup_type=StartupType.SYSTEMD,
                            status=StartupStatus.RUNNING, icon="📶", enabled=True,
                            boot_time_ms=200, category="System"),
            AutostartEntry(name="Bluetooth", command="/usr/lib/bluetooth/bluetoothd",
                            description="Bluetooth daemon", startup_type=StartupType.SYSTEMD,
                            status=StartupStatus.RUNNING, icon="🔵", enabled=True,
                            boot_time_ms=100, category="System"),
            AutostartEntry(name="Discord", command="/usr/bin/discord --start-minimized",
                            description="Discord chat client", startup_type=StartupType.XDG,
                            status=StartupStatus.PENDING, icon="💬", enabled=True,
                            delay_ms=5000, boot_time_ms=2500, category="Apps"),
            AutostartEntry(name="Spotify", command="/usr/bin/spotify --minimized",
                            description="Music streaming", startup_type=StartupType.XDG,
                            status=StartupStatus.PENDING, icon="🎵", enabled=True,
                            delay_ms=8000, boot_time_ms=3000, category="Apps"),
            AutostartEntry(name="Syncthing", command="/usr/bin/syncthing serve",
                            description="File synchronization", startup_type=StartupType.SYSTEMD,
                            status=StartupStatus.RUNNING, icon="🔄", enabled=True,
                            boot_time_ms=500, category="System"),
            AutostartEntry(name="Backup Timer", command="/usr/local/bin/backup.sh",
                            description="Scheduled backups", startup_type=StartupType.CRON,
                            status=StartupStatus.DISABLED, icon="💾", enabled=False,
                            category="System"),
        ]

        self.boot_entries = [
            BootEntry(kernel="/boot/vmlinuz-nyrqis", initrd="/boot/initramfs-nyrqis.img",
                      options="root=/dev/nvme0n1p2 quiet splash",
                      timestamp=now - 86400, is_default=True, kernel_version="1.0.0-rc1"),
            BootEntry(kernel="/boot/vmlinuz-nyrqis", initrd="/boot/initramfs-nyrqis.img",
                      options="root=/dev/nvme0n1p2",
                      timestamp=now - 86400 * 7, kernel_version="1.0.0-pre3"),
            BootEntry(kernel="/boot/vmlinuz-nyrqis-old", initrd="/boot/initramfs-nyrqis-old.img",
                      options="root=/dev/nvme0n1p2",
                      timestamp=now - 86400 * 30, kernel_version="0.9.5"),
        ]

        for i in range(10):
            self.boot_times.append(BootTimeRecord(
                timestamp=now - i * 86400,
                total_ms=random.uniform(3000, 6000),
                kernel_ms=random.uniform(800, 1500),
                initrd_ms=random.uniform(500, 1200),
                userspace_ms=random.uniform(1500, 3000),
                firmware_ms=random.uniform(200, 500),
                bootloader_ms=random.uniform(100, 300)))

        self.services = [
            SystemdService(name="nyrqis-compositor.service", description="Nyrqis Compositor",
                            active_state="active", sub_state="running", enabled=True,
                            pid=2, memory_bytes=256 * 1024 * 1024),
            SystemdService(name="nyrqis-shell.service", description="Nyrqis Shell",
                            active_state="active", sub_state="running", enabled=True,
                            pid=3, memory_bytes=128 * 1024 * 1024),
            SystemdService(name="pulseaudio.service", description="PulseAudio Sound System",
                            active_state="active", sub_state="running", enabled=True,
                            pid=400, memory_bytes=24 * 1024 * 1024),
            SystemdService(name="NetworkManager.service", description="Network Manager",
                            active_state="active", sub_state="running", enabled=True,
                            pid=101, memory_bytes=15 * 1024 * 1024),
            SystemdService(name="sshd.service", description="OpenSSH server daemon",
                            active_state="active", sub_state="running", enabled=True,
                            pid=1024, memory_bytes=5 * 1024 * 1024),
            SystemdService(name="bluetooth.service", description="Bluetooth service",
                            active_state="active", sub_state="running", enabled=True,
                            pid=100, memory_bytes=8 * 1024 * 1024),
            SystemdService(name="cups.service", description="CUPS Printer Service",
                            active_state="inactive", sub_state="dead", enabled=False),
            SystemdService(name="thermald.service", description="Thermal Daemon",
                            active_state="active", sub_state="running", enabled=True,
                            pid=800, memory_bytes=8 * 1024 * 1024),
        ]

    def toggle_entry(self, name: str) -> bool:
        entry = next((e for e in self.autostart_entries if e.name == name), None)
        if entry:
            entry.enabled = not entry.enabled
            entry.status = StartupStatus.ENABLED if entry.enabled else StartupStatus.DISABLED
            return True
        return False

    def remove_entry(self, name: str) -> bool:
        for i, e in enumerate(self.autostart_entries):
            if e.name == name:
                del self.autostart_entries[i]
                return True
        return False

    def add_entry(self, entry: AutostartEntry) -> None:
        self.autostart_entries.append(entry)

    def get_enabled_entries(self) -> List[AutostartEntry]:
        return [e for e in self.autostart_entries if e.enabled]

    def get_entries_by_category(self, category: str) -> List[AutostartEntry]:
        return [e for e in self.autostart_entries if e.category == category]

    def get_boot_time_stats(self) -> Dict:
        if not self.boot_times:
            return {}
        avg = sum(b.total_ms for b in self.boot_times) / len(self.boot_times)
        fastest = min(b.total_ms for b in self.boot_times)
        slowest = max(b.total_ms for b in self.boot_times)
        return {
            "average_ms": round(avg, 0),
            "fastest_ms": round(fastest, 0),
            "slowest_ms": round(slowest, 0),
            "boots": len(self.boot_times),
        }

    def get_total_boot_time(self) -> float:
        return sum(e.boot_time_ms for e in self.autostart_entries if e.enabled)

    def search(self, query: str) -> List[AutostartEntry]:
        q = query.lower()
        return [e for e in self.autostart_entries if q in e.name.lower() or q in e.description.lower()]

    def get_stats(self) -> Dict:
        return {
            "entries": len(self.autostart_entries),
            "enabled": len(self.get_enabled_entries()),
            "boot_entries": len(self.boot_entries),
            "services": len(self.services),
            "total_boot_time_ms": self.get_total_boot_time(),
        }
