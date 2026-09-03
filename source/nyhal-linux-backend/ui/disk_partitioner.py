"""
Nyrqis Disk Partitioner — disk partition management application.

Features:
- View and manage disk partitions
- Create, resize, delete, and format partitions
- Filesystem tools (mkfs, fsck, mount, unmount)
- Partition table editor (MBR, GPT)
- Disk information and S.M.A.R.T. status
- RAID configuration (JBOD, RAID 0/1/5/10)
- LVM (Logical Volume Manager) support
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class FilesystemType(Enum):
    EXT4 = "ext4"
    EXT3 = "ext3"
    XFS = "xfs"
    BTRFS = "btrfs"
    FAT32 = "FAT32"
    NTFS = "NTFS"
    EXFAT = "exFAT"
    SWAP = "swap"
    ZFS = "ZFS"
    REISERFS = "ReiserFS"
    UNFORMATTED = "unformatted"


class PartitionType(Enum):
    PRIMARY = "Primary"
    LOGICAL = "Logical"
    EXTENDED = "Extended"
    EFI = "EFI System"
    SWAP = "Linux Swap"
    LVM = "LVM"
    RAID = "RAID"
    Microsoft = "Microsoft Basic"
    Apple = "Apple APFS"


class TableType(Enum):
    MBR = "MBR"
    GPT = "GPT"
    NONE = "None"


class DiskInterface(Enum):
    SATA = "SATA"
    NVME = "NVMe"
    USB = "USB"
    SCSI = "SCSI"
    VIRTIO = "VirtIO"


FS_ICONS = {
    FilesystemType.EXT4: "🐧",
    FilesystemType.EXT3: "🐧",
    FilesystemType.XFS: "🦊",
    FilesystemType.BTRFS: "🌳",
    FilesystemType.FAT32: "💾",
    FilesystemType.NTFS: "🪟",
    FilesystemType.EXFAT: "📁",
    FilesystemType.SWAP: "🔄",
    FilesystemType.ZFS: "🐠",
    FilesystemType.REISERFS: "📦",
    FilesystemType.UNFORMATTED: "❓",
}


@dataclass
class Partition:
    """A disk partition."""
    name: str
    device: str  # e.g., /dev/sda1
    start_mb: int
    size_mb: int
    filesystem: FilesystemType = FilesystemType.UNFORMATTED
    partition_type: PartitionType = PartitionType.PRIMARY
    mount_point: str = ""
    label: str = ""
    uuid: str = ""
    flags: List[str] = field(default_factory=list)
    bootable: bool = False
    encrypted: bool = False
    read_only: bool = False
    # Usage
    used_mb: float = 0.0

    def __post_init__(self):
        if not self.uuid:
            self.uuid = hashlib.md5(f"{self.device}{self.start_mb}".encode()).hexdigest()[:8]

    @property
    def end_mb(self) -> int:
        return self.start_mb + self.size_mb

    @property
    def usage_pct(self) -> float:
        return (self.used_mb / self.size_mb * 100) if self.size_mb > 0 else 0

    @property
    def free_mb(self) -> float:
        return self.size_mb - self.used_mb

    @property
    def display_size(self) -> str:
        if self.size_mb >= 1048576:
            return f"{self.size_mb / 1048576:.1f} TB"
        elif self.size_mb >= 1024:
            return f"{self.size_mb / 1024:.1f} GB"
        return f"{self.size_mb} MB"

    @property
    def display_used(self) -> str:
        if self.size_mb >= 1024:
            used = self.used_mb / 1024
            total = self.size_mb / 1024
            return f"{used:.1f} / {total:.1f} GB ({self.usage_pct:.0f}%)"
        return f"{self.used_mb:.0f} / {self.size_mb} MB ({self.usage_pct:.0f}%)"

    @property
    def status_str(self) -> str:
        parts = []
        if self.mount_point:
            parts.append(f"mounted at {self.mount_point}")
        if self.encrypted:
            parts.append("encrypted")
        if self.read_only:
            parts.append("read-only")
        if self.bootable:
            parts.append("bootable")
        return ", ".join(parts) if parts else self.filesystem.value


@dataclass
class Disk:
    """A physical disk."""
    name: str
    device: str  # e.g., /dev/sda
    interface: DiskInterface = DiskInterface.SATA
    total_mb: int = 0
    model: str = ""
    serial: str = ""
    table_type: TableType = TableType.GPT
    partitions: List[Partition] = field(default_factory=list)
    # S.M.A.R.T.
    temperature: int = 35
    health_pct: int = 100
    power_on_hours: int = 0
    total_lbas: int = 0

    @property
    def display_size(self) -> str:
        if self.total_mb >= 1048576:
            return f"{self.total_mb / 1048576:.1f} TB"
        elif self.total_mb >= 1024:
            return f"{self.total_mb / 1024:.0f} GB"
        return f"{self.total_mb} MB"

    @property
    def used_mb(self) -> float:
        return sum(p.size_mb for p in self.partitions)

    @property
    def free_mb(self) -> float:
        return self.total_mb - self.used_mb

    @property
    def free_str(self) -> str:
        if self.free_mb >= 1024:
            return f"{self.free_mb / 1024:.1f} GB"
        return f"{self.free_mb:.0f} MB"

    @property
    def health_str(self) -> str:
        if self.health_pct >= 90:
            return f"✅ {self.health_pct}%"
        elif self.health_pct >= 70:
            return f"⚠️ {self.health_pct}%"
        return f"❌ {self.health_pct}%"


@dataclass
class RAIDArray:
    """A RAID array."""
    name: str
    level: int  # 0, 1, 5, 10, JBOD
    disks: List[str] = field(default_factory=list)
    status: str = "active"
    total_mb: int = 0
    chunk_size_kb: int = 64

    @property
    def display(self) -> str:
        level_str = f"RAID {self.level}" if self.level > 0 else "JBOD"
        return f"{level_str}: {self.name} ({len(self.disks)} disks)"

    @property
    def effective_size(self) -> int:
        if self.level == 0:
            return self.total_mb * len(self.disks)
        elif self.level == 1:
            return self.total_mb
        elif self.level == 5:
            return self.total_mb * (len(self.disks) - 1)
        elif self.level == 10:
            return self.total_mb * (len(self.disks) // 2)
        return self.total_mb * len(self.disks)  # JBOD


@dataclass
class LogicalVolume:
    """An LVM logical volume."""
    name: str
    vg_name: str  # Volume group
    size_mb: int
    filesystem: FilesystemType = FilesystemType.EXT4
    mount_point: str = ""
    thin_provisioned: bool = False

    @property
    def display_size(self) -> str:
        if self.size_mb >= 1024:
            return f"{self.size_mb / 1024:.1f} GB"
        return f"{self.size_mb} MB"


# ─── Disk Partitioner ────────────────────────────────────────────────────


class DiskPartitioner:
    """
    Disk partition management for Nyrqis OS.
    """

    def __init__(self):
        self._disks: List[Disk] = []
        self._raid_arrays: List[RAIDArray] = []
        self._logical_volumes: List[LogicalVolume] = []
        self._selected_disk: int = 0
        self._selected_partition: int = 0
        self._view_mode: str = "disks"  # disks, partitions, filesystem, smart, raid, lvm
        self._fs_operation: str = ""  # current FS operation
        self._operation_log: List[str] = []

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        self._disks = [
            Disk(
                "System Disk", "/dev/sda", DiskInterface.NVME, 1024000,
                "Samsung 990 PRO 1TB", "S5JYNS0T123456", TableType.GPT,
                temperature=38, health_pct=98, power_on_hours=2150,
                partitions=[
                    Partition("EFI System", "/dev/sda1", 0, 512,
                              FilesystemType.FAT32, PartitionType.EFI, "/boot/efi",
                              label="EFI", bootable=True, used_mb=128),
                    Partition("Boot", "/dev/sda2", 512, 2048,
                              FilesystemType.EXT4, PartitionType.PRIMARY, "/boot",
                              label="boot", used_mb=1024),
                    Partition("Root", "/dev/sda3", 2560, 102400,
                              FilesystemType.BTRFS, PartitionType.PRIMARY, "/",
                              label="nixos-root", used_mb=45000),
                    Partition("Home", "/dev/sda4", 104960, 204800,
                              FilesystemType.EXT4, PartitionType.PRIMARY, "/home",
                              label="home", used_mb=120000),
                    Partition("Swap", "/dev/sda5", 309760, 32768,
                              FilesystemType.SWAP, PartitionType.SWAP,
                              label="swap"),
                ],
            ),
            Disk(
                "Data Disk", "/dev/sdb", DiskInterface.SATA, 2048000,
                "WD Red Plus 2TB", "WD-WMC4T0123456", TableType.GPT,
                temperature=32, health_pct=95, power_on_hours=8760,
                partitions=[
                    Partition("Data", "/dev/sdb1", 0, 1024000,
                              FilesystemType.EXT4, PartitionType.PRIMARY, "/data",
                              label="data", used_mb=680000),
                    Partition("Media", "/dev/sdb2", 1024000, 512000,
                              FilesystemType.XFS, PartitionType.PRIMARY, "/media",
                              label="media", used_mb=420000),
                    Partition("Backup", "/dev/sdb3", 1536000, 512000,
                              FilesystemType.BTRFS, PartitionType.PRIMARY, "/backup",
                              label="backup", used_mb=180000),
                ],
            ),
            Disk(
                "USB Drive", "/dev/sdc", DiskInterface.USB, 61440,
                "SanDisk Ultra 64GB", "SD-0123456789", TableType.MBR,
                temperature=28, health_pct=87, power_on_hours=500,
                partitions=[
                    Partition("USB Partition", "/dev/sdc1", 0, 61440,
                              FilesystemType.EXFAT, PartitionType.PRIMARY, "/mnt/usb",
                              label="USB_DRIVE", used_mb=15000),
                ],
            ),
        ]

        self._raid_arrays = [
            RAIDArray("fast-array", 0, ["/dev/sdd", "/dev/sde", "/dev/sdf"],
                      "active", 512000, 128),
            RAIDArray("safe-mirror", 1, ["/dev/sdg", "/dev/sdh"],
                      "active", 1024000),
        ]

        self._logical_volumes = [
            LogicalVolume("root", "vg-system", 51200, FilesystemType.BTRFS, "/", True),
            LogicalVolume("home", "vg-system", 102400, FilesystemType.EXT4, "/home"),
            LogicalVolume("var", "vg-system", 20480, FilesystemType.EXT4, "/var"),
            LogicalVolume("srv", "vg-data", 204800, FilesystemType.XFS, "/srv"),
        ]

    # ── Operations ────────────────────────────────────────────────────

    def create_partition(self, disk_idx: int, name: str, size_mb: int,
                         filesystem: FilesystemType = FilesystemType.EXT4) -> Optional[Partition]:
        if 0 <= disk_idx < len(self._disks):
            disk = self._disks[disk_idx]
            start = disk.free_mb + disk.used_mb if disk.partitions else 0
            # Find actual free space
            if disk.partitions:
                last_end = max(p.end_mb for p in disk.partitions)
                start = last_end
            if start + size_mb > disk.total_mb:
                return None
            part = Partition(
                name=name, device=f"/dev/sd{chr(97 + disk_idx)}{len(disk.partitions) + 1}",
                start_mb=start, size_mb=size_mb, filesystem=filesystem,
            )
            disk.partitions.append(part)
            self._log(f"Created partition {part.device} ({part.display_size}) on {disk.name}")
            return part
        return None

    def delete_partition(self, disk_idx: int, part_idx: int) -> bool:
        if 0 <= disk_idx < len(self._disks):
            disk = self._disks[disk_idx]
            if 0 <= part_idx < len(disk.partitions):
                part = disk.partitions[part_idx]
                if part.mount_point:
                    self.unmount(disk_idx, part_idx)
                disk.partitions.pop(part_idx)
                self._log(f"Deleted partition {part.device} from {disk.name}")
                return True
        return False

    def resize_partition(self, disk_idx: int, part_idx: int, new_size_mb: int) -> bool:
        if 0 <= disk_idx < len(self._disks):
            disk = self._disks[disk_idx]
            if 0 <= part_idx < len(disk.partitions):
                part = disk.partitions[part_idx]
                diff = new_size_mb - part.size_mb
                if disk.free_mb + part.size_mb >= new_size_mb and new_size_mb > 0:
                    part.size_mb = new_size_mb
                    self._log(f"Resized {part.device}: {part.display_size}")
                    return True
        return False

    def format_partition(self, disk_idx: int, part_idx: int,
                         filesystem: FilesystemType) -> bool:
        if 0 <= disk_idx < len(self._disks):
            disk = self._disks[disk_idx]
            if 0 <= part_idx < len(disk.partitions):
                part = disk.partitions[part_idx]
                part.filesystem = filesystem
                part.used_mb = 0
                self._log(f"Formatted {part.device} as {filesystem.value}")
                return True
        return False

    def mount(self, disk_idx: int, part_idx: int, mount_point: str) -> bool:
        if 0 <= disk_idx < len(self._disks):
            disk = self._disks[disk_idx]
            if 0 <= part_idx < len(disk.partitions):
                part = disk.partitions[part_idx]
                if part.filesystem != FilesystemType.SWAP:
                    part.mount_point = mount_point
                    self._log(f"Mounted {part.device} at {mount_point}")
                    return True
        return False

    def unmount(self, disk_idx: int, part_idx: int) -> bool:
        if 0 <= disk_idx < len(self._disks):
            disk = self._disks[disk_idx]
            if 0 <= part_idx < len(disk.partitions):
                part = disk.partitions[part_idx]
                mp = part.mount_point
                part.mount_point = ""
                self._log(f"Unmounted {part.device} from {mp}")
                return True
        return False

    def fsck(self, disk_idx: int, part_idx: int) -> str:
        if 0 <= disk_idx < len(self._disks):
            disk = self._disks[disk_idx]
            if 0 <= part_idx < len(disk.partitions):
                part = disk.partitions[part_idx]
                self._log(f"fsck on {part.device}: OK (0 errors)")
                return "OK"
        return "ERROR"

    # ── Navigation ────────────────────────────────────────────────────

    def select_disk_up(self) -> None:
        self._selected_disk = max(0, self._selected_disk - 1)

    def select_disk_down(self) -> None:
        self._selected_disk = min(len(self._disks) - 1, self._selected_disk + 1)

    def select_partition_up(self) -> None:
        self._selected_partition = max(0, self._selected_partition - 1)

    def select_partition_down(self) -> None:
        disk = self.get_selected_disk()
        if disk:
            self._selected_partition = min(len(disk.partitions) - 1, self._selected_partition + 1)

    def get_selected_disk(self) -> Optional[Disk]:
        if 0 <= self._selected_disk < len(self._disks):
            return self._disks[self._selected_disk]
        return None

    def get_selected_partition(self) -> Optional[Partition]:
        disk = self.get_selected_disk()
        if disk and 0 <= self._selected_partition < len(disk.partitions):
            return disk.partitions[self._selected_partition]
        return None

    def set_view(self, mode: str) -> None:
        self._view_mode = mode

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._operation_log.append(f"[{ts}] {msg}")

    # ── Properties ────────────────────────────────────────────────────

    @property
    def disks(self) -> List[Disk]:
        return list(self._disks)

    @property
    def selected_disk(self) -> int:
        return self._selected_disk

    @property
    def selected_partition(self) -> int:
        return self._selected_partition

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def operation_log(self) -> List[str]:
        return list(self._operation_log)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_disks(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 💾 Disk Manager")
        lines.append("─" * width)

        for i, disk in enumerate(self._disks):
            marker = "▸" if i == self._selected_disk else " "
            lines.append(f"{marker} {disk.name} — {disk.display_size} [{disk.interface.value}]")
            lines.append(f"   {disk.device} | {disk.model} | {disk.table_type.value}")

            # Partition bar
            if disk.partitions:
                total = disk.total_mb
                bar_width = width - 6
                filled = 0
                bar = "│"
                for part in disk.partitions:
                    part_len = max(1, int(part.size_mb / total * bar_width))
                    icon = FS_ICONS.get(part.filesystem, "❓")
                    segment = f"{part.filesystem.value[0].upper()}" * part_len
                    bar += segment
                    filled += part_len
                bar += "░" * max(0, bar_width - filled) + "│"
                lines.append(f"   {bar}")

            lines.append(f"   Used: {disk.used_mb / 1024:.1f} / {disk.display_size} | Free: {disk.free_str} | Health: {disk.health_str}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Partitions  S:SMART  R:RAID  L:LVM")
        return lines

    def render_partitions(self, width: int = 60) -> List[str]:
        disk = self.get_selected_disk()
        if not disk:
            return ["No disk selected"]

        lines = []
        lines.append(f" 📋 Partitions — {disk.name} ({disk.display_size})")
        lines.append("─" * width)

        for i, part in enumerate(disk.partitions):
            marker = "▸" if i == self._selected_partition else " "
            icon = FS_ICONS.get(part.filesystem, "❓")
            lines.append(f"{marker} {icon} {part.name} — {part.display_size}")
            lines.append(f"   {part.device} | {part.filesystem.value} | {part.partition_type.value}")
            lines.append(f"   {part.status_str}")

            # Usage bar
            bar_width = 40
            used_len = int(part.usage_pct / 100 * bar_width)
            bar = "█" * used_len + "░" * (bar_width - used_len)
            lines.append(f"   [{bar}] {part.display_used}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  F:Format  M:Mount  U:Unmount  D:Delete  R:Resize")
        lines.append(" Backspace:Back")
        return lines

    def render_smart(self, width: int = 60) -> List[str]:
        disk = self.get_selected_disk()
        if not disk:
            return ["No disk selected"]

        lines = []
        lines.append(f" 📊 S.M.A.R.T. — {disk.name}")
        lines.append("─" * width)
        lines.append(f" Model:      {disk.model}")
        lines.append(f" Serial:     {disk.serial}")
        lines.append(f" Interface:  {disk.interface.value}")
        lines.append(f" Capacity:   {disk.display_size}")
        lines.append(f" Partition:  {disk.table_type.value}")
        lines.append("")
        lines.append(f" Health:     {disk.health_str}")
        lines.append(f" Temperature: {disk.temperature}°C")
        lines.append(f" Power-On:   {disk.power_on_hours:,} hours")
        lines.append("")

        # Simulated S.M.A.R.T. attributes
        attrs = [
            ("Raw Read Error Rate", "0", "OK"),
            ("Seek Time Performance", "100%", "OK"),
            ("Spin-Up Time", "98%", "OK"),
            ("Power-On Hours", f"{disk.power_on_hours:,}", "OK" if disk.power_on_hours < 50000 else "WARN"),
            ("Temperature", f"{disk.temperature}°C", "OK" if disk.temperature < 50 else "WARN"),
            ("Reallocated Sectors", "0", "OK"),
            ("Current Pending Sectors", "0", "OK"),
            ("Uncorrectable Errors", "0", "OK"),
        ]

        lines.append(" Attribute              Value   Status")
        for attr, val, status in attrs:
            status_icon = "✅" if status == "OK" else "⚠️"
            lines.append(f" {attr:<22s} {val:<8s} {status_icon}")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_raid(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🔄 RAID Arrays")
        lines.append("─" * width)

        if not self._raid_arrays:
            lines.append("  No RAID arrays configured.")
        else:
            for array in self._raid_arrays:
                lines.append(f" {array.display}")
                level_desc = {
                    0: "Striping — performance",
                    1: "Mirroring — redundancy",
                    5: "Parity — balance",
                    10: "Mirror+Stripe — performance + redundancy",
                }
                desc = level_desc.get(array.level, "Just a Bunch of Disks")
                lines.append(f"   {desc}")
                lines.append(f"   Status: {array.status} | Chunk: {array.chunk_size_kb} KB")
                lines.append(f"   Disks: {', '.join(array.disks)}")
                lines.append(f"   Raw: {array.total_mb / 1024:.0f} GB × {len(array.disks)} = {array.effective_size / 1024:.0f} GB effective")
                lines.append("")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_lvm(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 📦 Logical Volume Manager")
        lines.append("─" * width)

        if not self._logical_volumes:
            lines.append("  No logical volumes configured.")
        else:
            # Group by VG
            vgs: Dict[str, List[LogicalVolume]] = {}
            for lv in self._logical_volumes:
                vgs.setdefault(lv.vg_name, []).append(lv)

            for vg_name, lvs in vgs.items():
                total = sum(lv.size_mb for lv in lvs)
                lines.append(f" 🏷️  {vg_name} ({total / 1024:.0f} GB)")
                for lv in lvs:
                    icon = FS_ICONS.get(lv.filesystem, "❓")
                    thin = " (thin)" if lv.thin_provisioned else ""
                    lines.append(f"   {icon} {lv.name} — {lv.display_size}{thin}")
                    lines.append(f"      {lv.filesystem.value} | {lv.mount_point or 'not mounted'}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        renderers = {
            "partitions": self.render_partitions,
            "smart": self.render_smart,
            "raid": self.render_raid,
            "lvm": self.render_lvm,
        }
        renderer = renderers.get(self._view_mode, self.render_disks)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "partitions":
            return self._handle_partitions_key(key)
        elif self._view_mode == "smart":
            return self._handle_smart_key(key)
        elif self._view_mode == "raid":
            return self._handle_raid_key(key)
        elif self._view_mode == "lvm":
            return self._handle_lvm_key(key)
        return self._handle_disks_key(key)

    def _handle_disks_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_disk_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_disk_down()
            return "select_down"
        elif key == "Enter":
            self._view_mode = "partitions"
            self._selected_partition = 0
            return "partitions"
        elif key == "s":
            self._view_mode = "smart"
            return "smart"
        elif key == "r":
            self._view_mode = "raid"
            return "raid"
        elif key == "l":
            self._view_mode = "lvm"
            return "lvm"
        return None

    def _handle_partitions_key(self, key: str) -> Optional[str]:
        if key == "Escape" or key == "Backspace":
            self._view_mode = "disks"
            return "back"
        elif key == "ArrowUp":
            self.select_partition_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_partition_down()
            return "select_down"
        elif key == "u":
            return "unmount" if self.unmount(self._selected_disk, self._selected_partition) else "unmount_failed"
        elif key == "f":
            return "format"
        return None

    def _handle_smart_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "disks"
            return "back"
        return None

    def _handle_raid_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "disks"
            return "back"
        return None

    def _handle_lvm_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "disks"
            return "back"
        return None
