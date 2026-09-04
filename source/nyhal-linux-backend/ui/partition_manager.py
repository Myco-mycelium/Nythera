"""
Nyrqis OS - Disk Partition Manager
Resize, format, and backup capabilities.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class FileSystem(Enum):
    EXT4 = "ext4"
    XFS = "xfs"
    BTRFS = "btrfs"
    NTFS = "ntfs"
    FAT32 = "fat32"
    EXFAT = "exfat"
    SWAP = "swap"
    ZFS = "zfs"
    F2FS = "f2fs"


class PartitionType(Enum):
    LINUX = "Linux"
    LINUX_SWAP = "Linux Swap"
    EFI = "EFI System"
    MICROSOFT_BASIC = "Microsoft Basic Data"
    MICROSOFT_RESERVED = "Microsoft Reserved"
    HPFS_NTFS = "HPFS/NTFS"
    UNKNOWN = "Unknown"


class DiskType(Enum):
    SSD = "SSD"
    NVME = "NVMe SSD"
    HDD = "HDD"
    USB = "USB Storage"


class MountOption(Enum):
    READ_ONLY = "ro"
    READ_WRITE = "rw"
    NO_EXEC = "noexec"
    NOSUID = "nosuid"
    NODEV = "nodev"
    NOATIME = "noatime"


@dataclass
class Partition:
    device: str
    filesystem: FileSystem = FileSystem.EXT4
    partition_type: PartitionType = PartitionType.LINUX
    size_gb: float = 0.0
    used_gb: float = 0.0
    start_sector: int = 0
    end_sector: int = 0
    label: str = ""
    uuid: str = ""
    mount_point: str = ""
    is_mounted: bool = False
    is_bootable: bool = False
    flags: List[str] = field(default_factory=list)
    mount_options: List[MountOption] = field(default_factory=list)
    dirty: bool = False
    encrypted: bool = False

    @property
    def usage_percent(self) -> float:
        if self.size_gb == 0:
            return 0.0
        return (self.used_gb / self.size_gb) * 100

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def size_display(self) -> str:
        if self.size_gb < 1:
            return f"{self.size_gb * 1024:.0f} MB"
        elif self.size_gb < 1024:
            return f"{self.size_gb:.1f} GB"
        return f"{self.size_gb / 1024:.2f} TB"

    @property
    def free_display(self) -> str:
        free = self.size_gb - self.used_gb
        if free < 1:
            return f"{free * 1024:.0f} MB"
        return f"{free:.1f} GB"

    @property
    def status_icon(self) -> str:
        if self.dirty:
            return "⚠️"
        if self.encrypted:
            return "🔒"
        if self.is_mounted:
            return "🟢"
        return "⚪"


@dataclass
class Disk:
    device: str
    model: str = ""
    serial: str = ""
    disk_type: DiskType = DiskType.SSD
    total_gb: float = 0.0
    sector_size: int = 512
    total_sectors: int = 0
    partitions: List[Partition] = field(default_factory=list)
    temperature_c: float = 0.0
    health_percent: float = 100.0
    power_on_hours: int = 0
    total_bytes_written_tb: float = 0.0
    read_speed_mbps: float = 0.0
    write_speed_mbps: float = 0.0
    firmware_version: str = ""
    rotation_rpm: int = 0

    @property
    def used_gb(self) -> float:
        return sum(p.used_gb for p in self.partitions)

    @property
    def free_gb(self) -> float:
        return self.total_gb - self.used_gb

    @property
    def health_status(self) -> str:
        if self.health_percent > 90:
            return "🟢 Excellent"
        elif self.health_percent > 70:
            return "🟡 Good"
        elif self.health_percent > 50:
            return "🟠 Fair"
        return "🔴 Poor"

    @property
    def temp_status(self) -> str:
        if self.temperature_c < 40:
            return "🟢 Cool"
        elif self.temperature_c < 55:
            return "🟡 Warm"
        return "🔴 Hot"

    @property
    def partition_count(self) -> int:
        return len(self.partitions)


@dataclass
class BackupTask:
    name: str
    source: str = ""
    destination: str = ""
    partition_device: str = ""
    status: str = "pending"  # pending, running, completed, failed
    progress: float = 0.0
    size_gb: float = 0.0
    speed_mbps: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    compressed: bool = True
    encrypted: bool = False
    checksum: str = ""

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def status_icon(self) -> str:
        icons = {
            "pending": "⏳",
            "running": "🔄",
            "completed": "✅",
            "failed": "❌",
        }
        return icons.get(self.status, "?")


class PartitionManager:
    def __init__(self):
        self.disks: List[Disk] = []
        self.backups: List[BackupTask] = []
        self.selected_disk: Optional[Disk] = None
        self.selected_partition: Optional[Partition] = None
        self.unallocated_space: Dict[str, float] = {}
        self._create_sample_data()

    def _create_sample_data(self):
        nvme_disk = Disk(
            device="/dev/nvme0n1", model="Samsung 990 Pro 2TB",
            serial="S6BFNX0T123456", disk_type=DiskType.NVME,
            total_gb=2000.0, sector_size=512, total_sectors=4194304000,
            temperature_c=42, health_percent=98, power_on_hours=3200,
            total_bytes_written_tb=12.5, read_speed_mbps=7450,
            write_speed_mbps=6900, firmware_version="5B2QGXD7",
            partitions=[
                Partition(device="/dev/nvme0n1p1", filesystem=FileSystem.FAT32,
                           partition_type=PartitionType.EFI, size_gb=0.5,
                           used_gb=0.1, label="EFI", uuid="A1B2-C3D4",
                           mount_point="/boot/efi", is_mounted=True,
                           is_bootable=True, flags=["esp", "boot"]),
                Partition(device="/dev/nvme0n1p2", filesystem=FileSystem.EXT4,
                           partition_type=PartitionType.LINUX, size_gb=100.0,
                           used_gb=45.0, label="Nyrqis OS", uuid="e4f5a6b7-c8d9-0e1f",
                           mount_point="/", is_mounted=True, flags=[""]),
                Partition(device="/dev/nvme0n1p3", filesystem=FileSystem.SWAP,
                           partition_type=PartitionType.LINUX_SWAP, size_gb=8.0,
                           used_gb=0.5, label="swap", uuid="1a2b-3c4d-5e6f",
                           is_mounted=True, flags=["swap"]),
                Partition(device="/dev/nvme0n1p4", filesystem=FileSystem.BTRFS,
                           partition_type=PartitionType.LINUX, size_gb=1891.5,
                           used_gb=320.0, label="data", uuid="a1b2c3d4-e5f6-7890",
                           mount_point="/home", is_mounted=True, flags=[""]),
            ])

        sata_disk = Disk(
            device="/dev/sda", model="Samsung 870 EVO 1TB",
            serial="S4EWNX0R654321", disk_type=DiskType.SSD,
            total_gb=1000.0, sector_size=512, total_sectors=2000000000,
            temperature_c=35, health_percent=95, power_on_hours=12000,
            total_bytes_written_tb=45.0, read_speed_mbps=560,
            write_speed_mbps=530, firmware_version="SVT02B6Q",
            partitions=[
                Partition(device="/dev/sda1", filesystem=FileSystem.EXT4,
                           partition_type=PartitionType.LINUX, size_gb=500.0,
                           used_gb=180.0, label="backup", uuid="b2c3d4e5-f6a7-8901",
                           mount_point="/mnt/backup", is_mounted=True, flags=[""]),
                Partition(device="/dev/sda2", filesystem=FileSystem.XFS,
                           partition_type=PartitionType.LINUX, size_gb=500.0,
                           used_gb=210.0, label="projects", uuid="c3d4e5f6-a7b8-9012",
                           mount_point="/mnt/projects", is_mounted=True, flags=[""]),
            ])

        hdd_disk = Disk(
            device="/dev/sdb", model="WD Red Plus 4TB",
            serial="WD-CC4H3456", disk_type=DiskType.HDD,
            total_gb=4000.0, sector_size=512, total_sectors=7814033168,
            temperature_c=38, health_percent=92, power_on_hours=25000,
            total_bytes_written_tb=120.0, read_speed_mbps=180,
            write_speed_mbps=175, firmware_version="82.00A82",
            rotation_rpm=7200,
            partitions=[
                Partition(device="/dev/sdb1", filesystem=FileSystem.EXT4,
                           partition_type=PartitionType.LINUX, size_gb=2000.0,
                           used_gb=1400.0, label="storage", uuid="d4e5f6a7-b8c9-0123",
                           mount_point="/mnt/storage", is_mounted=True, flags=[""]),
                Partition(device="/dev/sdb2", filesystem=FileSystem.NTFS,
                           partition_type=PartitionType.MICROSOFT_BASIC, size_gb=2000.0,
                           used_gb=800.0, label="windows-data", uuid="E5F6A7B8-C9D0-1234",
                           mount_point="/mnt/windows", is_mounted=False, flags=[""]),
            ])

        self.disks = [nvme_disk, sata_disk, hdd_disk]
        self.selected_disk = nvme_disk

        self.backups = [
            BackupTask(name="Full System Backup", source="/", destination="/mnt/backup/system",
                       partition_device="/dev/nvme0n1p2", status="completed",
                       progress=100.0, size_gb=45.0, speed_mbps=350,
                       started_at=time.time() - 3600, completed_at=time.time() - 2400,
                       compressed=True, encrypted=True, checksum="sha256:a1b2c3..."),
            BackupTask(name="Home Directory", source="/home", destination="/mnt/backup/home",
                       partition_device="/dev/nvme0n1p4", status="running",
                       progress=65.0, size_gb=208.0, speed_mbps=280,
                       started_at=time.time() - 600, compressed=True, encrypted=False),
            BackupTask(name="EFI Partition", source="/boot/efi", destination="/mnt/backup/efi",
                       partition_device="/dev/nvme0n1p1", status="pending",
                       size_gb=0.1, compressed=False),
        ]

    def get_all_partitions(self) -> List[Partition]:
        partitions = []
        for disk in self.disks:
            partitions.extend(disk.partitions)
        return partitions

    def get_mounted_partitions(self) -> List[Partition]:
        return [p for p in self.get_all_partitions() if p.is_mounted]

    def get_disk(self, device: str) -> Optional[Disk]:
        return next((d for d in self.disks if d.device == device), None)

    def select_disk(self, device: str) -> Optional[Disk]:
        disk = self.get_disk(device)
        if disk:
            self.selected_disk = disk
        return disk

    def select_partition(self, device: str) -> Optional[Partition]:
        for disk in self.disks:
            part = next((p for p in disk.partitions if p.device == device), None)
            if part:
                self.selected_partition = part
                return part
        return None

    def format_partition(self, device: str, filesystem: FileSystem, label: str = "") -> bool:
        part = self.select_partition(device)
        if part:
            part.filesystem = filesystem
            part.label = label
            part.used_gb = 0
            return True
        return False

    def resize_partition(self, device: str, new_size_gb: float) -> bool:
        part = self.select_partition(device)
        if part and new_size_gb > part.used_gb:
            part.size_gb = new_size_gb
            return True
        return False

    def mount_partition(self, device: str, mount_point: str) -> bool:
        part = self.select_partition(device)
        if part and not part.is_mounted:
            part.mount_point = mount_point
            part.is_mounted = True
            return True
        return False

    def unmount_partition(self, device: str) -> bool:
        part = self.select_partition(device)
        if part and part.is_mounted:
            part.mount_point = ""
            part.is_mounted = False
            return True
        return False

    def create_backup(self, name: str, source: str, destination: str, **kwargs) -> BackupTask:
        task = BackupTask(name=name, source=source, destination=destination, **kwargs)
        self.backups.append(task)
        return task

    def get_disk_stats(self) -> Dict:
        total_space = sum(d.total_gb for d in self.disks)
        used_space = sum(d.used_gb for d in self.disks)
        return {
            "total_disks": len(self.disks),
            "total_partitions": len(self.get_all_partitions()),
            "total_space_gb": round(total_space, 1),
            "used_space_gb": round(used_space, 1),
            "free_space_gb": round(total_space - used_space, 1),
            "backups": len(self.backups),
        }
