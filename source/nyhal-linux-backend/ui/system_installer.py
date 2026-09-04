"""
Nyrqis OS - System Installer Wizard
Partition setup, user creation, and bootloader configuration.

Features:
- Disk selection and partitioning (GPT/MBR)
- Filesystem creation (ext4, btrfs, xfs, zfs, f2fs)
- Partition layout (manual, auto, guided)
- User account creation with password
- Timezone and locale configuration
- Bootloader setup (GRUB, systemd-boot, rEFInd)
- Network configuration during install
- Package selection (base, desktop, minimal, custom)
- Installation progress with log output
- Post-install configuration
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class InstallerStep(Enum):
    WELCOME = "welcome"
    LANGUAGE = "language"
    KEYBOARD = "keyboard"
    TIMEZONE = "timezone"
    DISK_SELECT = "disk_select"
    PARTITIONING = "partitioning"
    USER_SETUP = "user_setup"
    NETWORK = "network"
    PACKAGES = "packages"
    BOOTLOADER = "bootloader"
    SUMMARY = "summary"
    INSTALLING = "installing"
    COMPLETE = "complete"


class FilesystemType(Enum):
    EXT4 = "ext4"
    BTRFS = "btrfs"
    XFS = "xfs"
    ZFS = "zfs"
    F2FS = "f2fs"
    SWAP = "swap"
    FAT32 = "vfat"
    NTFS = "ntfs"


class PartitionTable(Enum):
    GPT = "GPT"
    MBR = "MBR"


class BootloaderType(Enum):
    GRUB = "GRUB"
    SYSTEMD_BOOT = "systemd-boot"
    REFIND = "rEFInd"
    LIMINE = "Limine"


class InstallStatus(Enum):
    NOT_STARTED = "not_started"
    PREPARING = "preparing"
    PARTITIONING = "partitioning"
    FORMATING = "formatting"
    EXTRACTING = "extracting"
    CONFIGURING = "configuring"
    BOOTLOADER = "bootloader"
    CLEANUP = "cleanup"
    COMPLETE = "complete"
    FAILED = "failed"


class PackageGroup(Enum):
    BASE = "base"
    DESKTOP = "desktop-environment"
    TERMINAL = "terminal"
    DEVELOPMENT = "development"
    OFFICE = "office"
    MULTIMEDIA = "multimedia"
    GAMING = "gaming"
    UTILITIES = "utilities"
    NETWORK = "network-tools"
    SECURITY = "security"


STEP_ICONS = {
    InstallerStep.WELCOME: "👋", InstallerStep.LANGUAGE: "🌐",
    InstallerStep.KEYBOARD: "⌨️", InstallerStep.TIMEZONE: "🕐",
    InstallerStep.DISK_SELECT: "💿", InstallerStep.PARTITIONING: "📐",
    InstallerStep.USER_SETUP: "👤", InstallerStep.NETWORK: "🌐",
    InstallerStep.PACKAGES: "📦", InstallerStep.BOOTLOADER: "🔧",
    InstallerStep.SUMMARY: "📋", InstallerStep.INSTALLING: "⚙️",
    InstallerStep.COMPLETE: "✅",
}

FS_ICONS = {
    FilesystemType.EXT4: "📁", FilesystemType.BTRFS: "🌲",
    FilesystemType.XFS: "📁", FilesystemType.ZFS: "🌊",
    FilesystemType.F2FS: "📁", FilesystemType.SWAP: "💤",
    FilesystemType.FAT32: "📁", FilesystemType.NTFS: "📁",
}

BOOT_ICONS = {
    BootloaderType.GRUB: "🔧", BootloaderType.SYSTEMD_BOOT: "⚡",
    BootloaderType.REFIND: "🍎", BootloaderType.LIMINE: "🚀",
}

PKG_ICONS = {
    PackageGroup.BASE: "⚙️", PackageGroup.DESKTOP: "🖥️",
    PackageGroup.TERMINAL: "💻", PackageGroup.DEVELOPMENT: "🛠️",
    PackageGroup.OFFICE: "📝", PackageGroup.MULTIMEDIA: "🎬",
    PackageGroup.GAMING: "🎮", PackageGroup.UTILITIES: "🧰",
    PackageGroup.NETWORK: "🌐", PackageGroup.SECURITY: "🔒",
}


@dataclass
class DiskInfo:
    device: str = ""
    model: str = ""
    serial: str = ""
    size_bytes: int = 0
    interface: str = ""  # NVMe, SATA, USB
    rotational: bool = False
    removable: bool = False
    health: str = "Good"
    partitions: int = 0
    temperature_c: float = 0.0

    @property
    def size_str(self) -> str:
        b = self.size_bytes
        if b >= 1024 ** 4:
            return f"{b / 1024 ** 4:.1f} TB"
        return f"{b / 1024 ** 3:.0f} GB"

    @property
    def type_label(self) -> str:
        if self.rotational:
            return "HDD"
        elif "NVMe" in self.interface:
            return "NVMe"
        return "SSD"

    @property
    def removable_icon(self) -> str:
        return "🔌" if self.removable else "💾"

    @property
    def selected_icon(self) -> str:
        return "✅"


@dataclass
class Partition:
    device: str = ""
    number: int = 0
    mount_point: str = ""
    filesystem: FilesystemType = FilesystemType.EXT4
    size_bytes: int = 0
    used_bytes: int = 0
    flags: List[str] = field(default_factory=list)
    encrypted: bool = False
    is_new: bool = False

    @property
    def size_str(self) -> str:
        b = self.size_bytes
        if b >= 1024 ** 3:
            return f"{b / 1024 ** 3:.1f} GB"
        return f"{b / 1024 ** 2:.0f} MB"

    @property
    def mount_label(self) -> str:
        return self.mount_point if self.mount_point else "—"

    @property
    def fs_label(self) -> str:
        return FS_ICONS.get(self.filesystem, "📁") + " " + self.filesystem.value

    @property
    def flags_str(self) -> str:
        return ", ".join(self.flags) if self.flags else "—"


@dataclass
class PartitionLayout:
    name: str = ""
    description: str = ""
    partitions: List[Partition] = field(default_factory=list)
    recommended: bool = False

    @property
    def total_size_str(self) -> str:
        total = sum(p.size_bytes for p in self.partitions)
        if total >= 1024 ** 3:
            return f"{total / 1024 ** 3:.1f} GB"
        return f"{total / 1024 ** 2:.0f} MB"


@dataclass
class UserInfo:
    username: str = ""
    password: str = ""
    full_name: str = ""
    hostname: str = "nyrqis-pc"
    auto_login: bool = True
    require_password: bool = True
    admin_group: bool = True

    @property
    def password_strength(self) -> str:
        pw = self.password
        if len(pw) == 0:
            return "None"
        score = 0
        if len(pw) >= 8:
            score += 1
        if len(pw) >= 12:
            score += 1
        if any(c.isupper() for c in pw):
            score += 1
        if any(c.isdigit() for c in pw):
            score += 1
        if any(c in "!@#$%^&*()_+-=" for c in pw):
            score += 1
        labels = ["Very Weak", "Weak", "Fair", "Good", "Strong", "Very Strong"]
        return labels[min(score, 5)]

    @property
    def strength_bar(self) -> str:
        pw = self.password
        score = 0
        if len(pw) >= 8: score += 1
        if len(pw) >= 12: score += 1
        if any(c.isupper() for c in pw): score += 1
        if any(c.isdigit() for c in pw): score += 1
        if any(c in "!@#$%^&*()_+-=" for c in pw): score += 1
        filled = int(score * 4)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class InstallLogEntry:
    timestamp: float = 0.0
    step: str = ""
    message: str = ""
    level: str = "info"  # info, warning, error, success

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def icon(self) -> str:
        icons = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "success": "✅"}
        return icons.get(self.level, "❓")


@dataclass
class PackageSelection:
    group: PackageGroup = PackageGroup.BASE
    selected: bool = True
    description: str = ""
    package_count: int = 0
    size_mb: float = 0.0

    @property
    def icon(self) -> str:
        return PKG_ICONS.get(self.group, "📦")

    @property
    def check_icon(self) -> str:
        return "☑️" if self.selected else "☐"

    @property
    def size_str(self) -> str:
        return f"{self.size_mb:.0f} MB"


@dataclass
class BootloaderConfig:
    bootloader: BootloaderType = BootloaderType.GRUB
    install_device: str = ""
    efi_partition: str = ""
    timeout_s: int = 5
    default_entry: str = "Nyrqis OS"
    enable_recover: bool = True
    os_prober: bool = False
    theme: str = "Nyrqis Dark"

    @property
    def display(self) -> str:
        return f"{BOOT_ICONS.get(self.bootloader, '🔧')} {self.bootloader.value}"


class SystemInstaller:
    def __init__(self):
        self.disks: List[DiskInfo] = []
        self.layouts: List[PartitionLayout] = []
        self.partitions: List[Partition] = []
        self.user: UserInfo = UserInfo()
        self.packages: List[PackageSelection] = []
        self.bootloader_config: BootloaderConfig = BootloaderConfig()
        self.logs: List[InstallLogEntry] = []
        self.current_step: InstallerStep = InstallerStep.WELCOME
        self.install_status: InstallStatus = InstallStatus.NOT_STARTED
        self.install_progress: float = 0.0
        self._selected_disk: int = 0
        self._selected_layout: int = 0
        self._selected_step: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        self.disks = [
            DiskInfo("/dev/nvme0n1", "Samsung 990 Pro 2TB", "S5KXNS0T123456",
                     2 * 1024 ** 4, "NVMe", False, False, "Good", 3, 42),
            DiskInfo("/dev/sda", "Samsung 870 EVO 1TB", "S4EVNX0R123456",
                     1 * 1024 ** 4, "SATA", False, False, "Good", 2, 35),
            DiskInfo("/dev/sdb", "WD Red Plus 4TB", "WX11A1234567",
                     4 * 1024 ** 4, "SATA", True, False, "Good", 1, 38),
            DiskInfo("/dev/sdc", "SanDisk Ultra 64GB", "SDD123456789",
                     64 * 1024 ** 3, "USB", False, True, "Good", 1, 0),
        ]

        self.layouts = [
            PartitionLayout(
                "Guided - Use entire disk", "Simple partitioning with LVM",
                [
                    Partition("/dev/nvme0n1", 1, "/boot/efi", FilesystemType.FAT32,
                              512 * 1024 ** 2, 0, ["boot", "efi"], False, True),
                    Partition("/dev/nvme0n1", 2, "/", FilesystemType.BTRFS,
                              50 * 1024 ** 3, 0, ["root"], False, True),
                    Partition("/dev/nvme0n1", 3, "/home", FilesystemType.BTRFS,
                              1400 * 1024 ** 3, 0, [], False, True),
                    Partition("/dev/nvme0n1", 4, "swap", FilesystemType.SWAP,
                              16 * 1024 ** 3, 0, [], False, True),
                ],
                recommended=True,
            ),
            PartitionLayout(
                "Guided - Use entire disk (Simple)", "Single root partition",
                [
                    Partition("/dev/nvme0n1", 1, "/boot/efi", FilesystemType.FAT32,
                              512 * 1024 ** 2, 0, ["boot", "efi"], False, True),
                    Partition("/dev/nvme0n1", 2, "/", FilesystemType.EXT4,
                              1900 * 1024 ** 3, 0, ["root"], False, True),
                    Partition("/dev/nvme0n1", 3, "swap", FilesystemType.SWAP,
                              16 * 1024 ** 3, 0, [], False, True),
                ],
            ),
            PartitionLayout(
                "Manual", "Full manual control",
                [], False,
            ),
        ]

        self.partitions = [
            Partition("/dev/nvme0n1", 1, "/boot/efi", FilesystemType.FAT32,
                      512 * 1024 ** 2, 48 * 1024 ** 2, ["boot", "efi"]),
            Partition("/dev/nvme0n1", 2, "/", FilesystemType.BTRFS,
                      50 * 1024 ** 3, 12 * 1024 ** 3, ["root"]),
            Partition("/dev/nvme0n1", 3, "/home", FilesystemType.BTRFS,
                      1400 * 1024 ** 3, 420 * 1024 ** 3),
            Partition("/dev/nvme0n1", 4, "swap", FilesystemType.SWAP,
                      16 * 1024 ** 3, 8 * 1024 ** 3),
        ]

        self.packages = [
            PackageSelection(PackageGroup.BASE, True, "Core system packages", 180, 450),
            PackageSelection(PackageGroup.DESKTOP, True, "Nyrqis desktop environment", 95, 1200),
            PackageSelection(PackageGroup.TERMINAL, True, "Terminal and shell tools", 42, 85),
            PackageSelection(PackageGroup.DEVELOPMENT, True, "Compilers, editors, git", 78, 520),
            PackageSelection(PackageGroup.OFFICE, False, "Office suite (LibreOffice)", 15, 680),
            PackageSelection(PackageGroup.MULTIMEDIA, False, "Audio/video tools", 35, 420),
            PackageSelection(PackageGroup.GAMING, False, "Gaming support (Steam, Proton)", 28, 350),
            PackageSelection(PackageGroup.UTILITIES, True, "System utilities", 45, 120),
            PackageSelection(PackageGroup.NETWORK, True, "Network management tools", 22, 65),
            PackageSelection(PackageGroup.SECURITY, True, "Firewall, encryption tools", 18, 45),
        ]

        self.bootloader_config = BootloaderConfig(
            BootloaderType.SYSTEMD_BOOT, "/dev/nvme0n1", "/dev/nvme0n1p1",
            5, "Nyrqis OS", True, False, "Nyrqis Dark",
        )

        self.logs = [
            InstallLogEntry(time.time() - 300, "Partitioning", "Disk /dev/nvme0n1 selected"),
            InstallLogEntry(time.time() - 290, "Partitioning", "GPT partition table created"),
            InstallLogEntry(time.time() - 280, "Partitioning", "EFI partition created: 512 MB"),
            InstallLogEntry(time.time() - 270, "Partitioning", "Root partition created: 50 GB (btrfs)"),
            InstallLogEntry(time.time() - 260, "Partitioning", "Home partition created: 1400 GB (btrfs)"),
            InstallLogEntry(time.time() - 250, "Partitioning", "Swap partition created: 16 GB"),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_disk(self) -> Optional[DiskInfo]:
        if 0 <= self._selected_disk < len(self.disks):
            return self.disks[self._selected_disk]
        return None

    @property
    def selected_layout(self) -> Optional[PartitionLayout]:
        if 0 <= self._selected_layout < len(self.layouts):
            return self.layouts[self._selected_layout]
        return None

    def select_disk(self, idx: int):
        if 0 <= idx < len(self.disks):
            self._selected_disk = idx
            self.bootloader_config.install_device = self.disks[idx].device

    def select_layout(self, idx: int):
        if 0 <= idx < len(self.layouts):
            self._selected_layout = idx

    # ─── Step Navigation ───────────────────────────────────────────────

    def go_next(self) -> bool:
        steps = list(InstallerStep)
        idx = steps.index(self.current_step)
        if idx < len(steps) - 1:
            self.current_step = steps[idx + 1]
            return True
        return False

    def go_back(self) -> bool:
        steps = list(InstallerStep)
        idx = steps.index(self.current_step)
        if idx > 0:
            self.current_step = steps[idx - 1]
            return True
        return False

    def goto_step(self, step: InstallerStep):
        self.current_step = step

    @property
    def step_index(self) -> int:
        return list(InstallerStep).index(self.current_step)

    @property
    def total_steps(self) -> int:
        return len(InstallerStep)

    @property
    def step_progress_bar(self) -> str:
        pct = (self.step_index / (self.total_steps - 1)) * 100
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    # ─── User Setup ────────────────────────────────────────────────────

    def set_user(self, username: str, password: str, full_name: str = "",
                 hostname: str = "nyrqis-pc"):
        self.user.username = username
        self.user.password = password
        self.user.full_name = full_name or username
        self.user.hostname = hostname

    def toggle_auto_login(self):
        self.user.auto_login = not self.user.auto_login

    # ─── Package Selection ─────────────────────────────────────────────

    def toggle_package(self, idx: int) -> bool:
        if 0 <= idx < len(self.packages):
            self.packages[idx].selected = not self.packages[idx].selected
            return True
        return False

    def select_all_packages(self):
        for p in self.packages:
            p.selected = True

    def deselect_all_packages(self):
        for p in self.packages:
            p.selected = False

    def select_base_packages(self):
        for p in self.packages:
            p.selected = p.group in (PackageGroup.BASE, PackageGroup.DESKTOP,
                                     PackageGroup.TERMINAL, PackageGroup.UTILITIES,
                                     PackageGroup.NETWORK, PackageGroup.SECURITY)

    @property
    def selected_packages(self) -> List[PackageSelection]:
        return [p for p in self.packages if p.selected]

    @property
    def total_install_size_mb(self) -> float:
        return sum(p.size_mb for p in self.packages if p.selected)

    @property
    def total_packages(self) -> int:
        return sum(p.package_count for p in self.packages if p.selected)

    # ─── Bootloader ────────────────────────────────────────────────────

    def set_bootloader(self, bt: BootloaderType):
        self.bootloader_config.bootloader = bt

    def set_boot_timeout(self, seconds: int):
        self.bootloader_config.timeout_s = max(0, min(30, seconds))

    # ─── Installation ──────────────────────────────────────────────────

    def start_install(self):
        self.install_status = InstallStatus.PREPARING
        self.install_progress = 0.0
        self.logs.append(InstallLogEntry(
            time.time(), "Install", "Installation started", "info"
        ))

    def update_progress(self, progress: float):
        self.install_progress = max(0, min(100, progress))
        if progress < 10:
            self.install_status = InstallStatus.PARTITIONING
        elif progress < 20:
            self.install_status = InstallStatus.FORMATING
        elif progress < 70:
            self.install_status = InstallStatus.EXTRACTING
        elif progress < 90:
            self.install_status = InstallStatus.CONFIGURING
        elif progress < 95:
            self.install_status = InstallStatus.BOOTLOADER
        elif progress < 100:
            self.install_status = InstallStatus.CLEANUP
        else:
            self.install_status = InstallStatus.COMPLETE

    def add_log(self, step: str, message: str, level: str = "info"):
        self.logs.append(InstallLogEntry(time.time(), step, message, level))

    @property
    def install_progress_bar(self) -> str:
        filled = int(self.install_progress / 5)
        return "█" * filled + "░" * (20 - filled)

    # ─── Summary ───────────────────────────────────────────────────────

    def get_summary(self) -> Dict:
        return {
            "disk": self.disks[self._selected_disk].device if self.disks else "None",
            "layout": self.layouts[self._selected_layout].name if self.layouts else "None",
            "user": self.user.username or "root",
            "hostname": self.user.hostname,
            "bootloader": self.bootloader_config.bootloader.value,
            "packages": self.total_packages,
            "install_size": f"{self.total_install_size_mb:.0f} MB",
            "timezone": "UTC",
            "locale": "en_US.UTF-8",
        }

    # ─── Stats ─────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        return {
            "disks": len(self.disks),
            "total_disk_space": sum(d.size_bytes for d in self.disks),
            "partitions": len(self.partitions),
            "packages_available": len(self.packages),
            "packages_selected": len(self.selected_packages),
            "total_steps": self.total_steps,
            "current_step": self.current_step.value,
        }
