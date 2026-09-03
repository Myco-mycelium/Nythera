"""
Nyrqis Encryption Manager — disk encryption and key management application.

Features:
- LUKS2 encrypted volume management
- Key slot management (passphrase, keyfile, TPM)
- Volume mount/unmount with passphrase
- Encryption status monitoring
- Key rotation and backup
- Volume creation and resizing
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional
from datetime import datetime


class EncryptionType(Enum):
    LUKS2 = "LUKS2"
    LUKS1 = "LUKS1"
    NONE = "none"


class KeySlotType(Enum):
    PASSPHRASE = "passphrase"
    KEYFILE = "keyfile"
    TPM = "tpm2"
    RECOVERY = "recovery"


class VolumeStatus(Enum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    MOUNTED = "mounted"
    ERRORS = "errors"


ENCRYPTION_ICONS = {
    EncryptionType.LUKS2: "🔐",
    EncryptionType.LUKS1: "🔒",
    EncryptionType.NONE: "🔓",
}

STATUS_ICONS = {
    VolumeStatus.LOCKED: "🔒",
    VolumeStatus.UNLOCKED: "🔓",
    VolumeStatus.MOUNTED: "🟢",
    VolumeStatus.ERRORS: "🔴",
}


@dataclass
class KeySlot:
    """A LUKS key slot."""
    slot_id: int
    slot_type: KeySlotType = KeySlotType.PASSPHRASE
    enabled: bool = True
    created: float = field(default_factory=time.time)
    last_used: float = 0.0
    key_fingerprint: str = ""

    def __post_init__(self):
        if not self.key_fingerprint:
            self.key_fingerprint = hashlib.md5(f"slot{self.slot_id}{self.created}".encode()).hexdigest()[:12]

    @property
    def type_icon(self) -> str:
        icons = {"passphrase": "🔑", "keyfile": "📄", "tpm2": "🔲", "recovery": "🆘"}
        return icons.get(self.slot_type.value, "❓")

    @property
    def display(self) -> str:
        status = "✅" if self.enabled else "❌"
        return f"{status} Slot {self.slot_id}: {self.slot_type.value.title()} ({self.key_fingerprint})"


@dataclass
class EncryptedVolume:
    """An encrypted disk volume."""
    name: str
    device: str  # /dev/sda2
    mapper: str = ""  # /dev/mapper/nyrqis-root
    encryption: EncryptionType = EncryptionType.LUKS2
    status: VolumeStatus = VolumeStatus.LOCKED
    # Size
    total_bytes: int = 0
    used_bytes: int = 0
    # Mount
    mount_point: str = ""
    filesystem: str = "ext4"
    # LUKS info
    cipher: str = "aes-xts-plain64"
    key_size: int = 512
    hash: str = "sha256"
    iterations: int = 5000
    # Key slots
    key_slots: List[KeySlot] = field(default_factory=list)
    # Metadata
    created: float = field(default_factory=time.time)
    unlocked_at: float = 0.0
    volume_id: str = ""

    def __post_init__(self):
        if not self.volume_id:
            self.volume_id = hashlib.md5(f"{self.device}".encode()).hexdigest()[:8]

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def enc_icon(self) -> str:
        return ENCRYPTION_ICONS.get(self.encryption, "❓")

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.name} [{self.encryption.value}]"

    @property
    def size_str(self) -> str:
        if self.total_bytes >= 1099511627776:
            return f"{self.total_bytes / 1099511627776:.1f} TB"
        elif self.total_bytes >= 1073741824:
            return f"{self.total_bytes / 1073741824:.1f} GB"
        return f"{self.total_bytes / 1048576:.0f} MB"

    @property
    def usage_pct(self) -> float:
        return (self.used_bytes / self.total_bytes * 100) if self.total_bytes > 0 else 0

    @property
    def usage_bar(self) -> str:
        filled = int(self.usage_pct / 100 * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def active_slots(self) -> int:
        return sum(1 for s in self.key_slots if s.enabled)

    @property
    def is_mounted(self) -> bool:
        return self.status == VolumeStatus.MOUNTED

    @property
    def unlocked_time_str(self) -> str:
        if self.unlocked_at <= 0:
            return "—"
        diff = time.time() - self.unlocked_at
        if diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.unlocked_at).strftime("%b %d")


@dataclass
class EncryptionBackup:
    """A LUKS header backup."""
    volume_name: str
    filename: str
    size_kb: int = 0
    created: float = field(default_factory=time.time)
    backup_id: str = ""

    def __post_init__(self):
        if not self.backup_id:
            self.backup_id = hashlib.md5(f"{self.filename}{self.created}".encode()).hexdigest()[:8]

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.created
        if diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.created).strftime("%b %d")


class EncryptionManager:
    """Disk encryption and key management for Nyrqis OS."""

    def __init__(self):
        self._volumes: List[EncryptedVolume] = []
        self._backups: List[EncryptionBackup] = []
        self._selected_index: int = 0
        self._selected_slot: int = 0
        self._view_mode: str = "volumes"  # volumes, keys, details, backups
        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        self._volumes = [
            EncryptedVolume("Nyrqis Root", "/dev/nvme0n1p3", "/dev/mapper/nyrqis-root",
                            EncryptionType.LUKS2, VolumeStatus.MOUNTED, 512_000_000_000, 340_000_000_000,
                            "/", "ext4", "aes-xts-plain64", 512, "sha256", 5000,
                            [KeySlot(0, KeySlotType.PASSPHRASE, True, now - 2592000, now - 3600),
                             KeySlot(1, KeySlotType.TPM, True, now - 2592000, now - 3600),
                             KeySlot(2, KeySlotType.RECOVERY, True, now - 2592000)],
                            now - 2592000, now - 3600),
            EncryptedVolume("Home Directory", "/dev/nvme0n1p4", "/dev/mapper/nyrqis-home",
                            EncryptionType.LUKS2, VolumeStatus.MOUNTED, 256_000_000_000, 160_000_000_000,
                            "/home", "ext4", "aes-xts-plain64", 512, "sha256", 5000,
                            [KeySlot(0, KeySlotType.PASSPHRASE, True, now - 2592000, now - 86400),
                             KeySlot(1, KeySlotType.KEYFILE, True, now - 2592000, now - 86400)],
                            now - 2592000, now - 86400),
            EncryptedVolume("Data Vault", "/dev/sdb1", "/dev/mapper/data-vault",
                            EncryptionType.LUKS2, VolumeStatus.MOUNTED, 1_000_000_000_000, 680_000_000_000,
                            "/data", "xfs", "aes-xts-plain64", 512, "sha256", 5000,
                            [KeySlot(0, KeySlotType.PASSPHRASE, True, now - 1296000, now - 14400)],
                            now - 1296000, now - 14400),
            EncryptedVolume("Backup Archive", "/dev/sdc1", "",
                            EncryptionType.LUKS2, VolumeStatus.LOCKED, 2_000_000_000_000, 850_000_000_000,
                            "", "ext4", "aes-xts-plain64", 512, "sha256", 5000,
                            [KeySlot(0, KeySlotType.PASSPHRASE, True, now - 604800),
                             KeySlot(1, KeySlotType.KEYFILE, True, now - 604800),
                             KeySlot(2, KeySlotType.RECOVERY, True, now - 604800)],
                            now - 604800),
            EncryptedVolume("Swap", "/dev/nvme0n1p2", "/dev/mapper/nyrqis-swap",
                            EncryptionType.LUKS2, VolumeStatus.UNLOCKED, 32_000_000_000, 0,
                            "[swap]", "swap", "aes-xts-plain64", 512, "sha256", 5000,
                            [KeySlot(0, KeySlotType.PASSPHRASE, True, now - 2592000)],
                            now - 2592000, now - 2592000),
        ]

        self._backups = [
            EncryptionBackup("Nyrqis Root", "luks-header-root-2026-09-01.bin", 16384, now - 172800),
            EncryptionBackup("Home Directory", "luks-header-home-2026-09-01.bin", 16384, now - 172800),
            EncryptionBackup("Data Vault", "luks-header-data-2026-08-15.bin", 16384, now - 1296000),
        ]

    def unlock_volume(self, index: int) -> bool:
        if 0 <= index < len(self._volumes):
            vol = self._volumes[index]
            if vol.status == VolumeStatus.LOCKED:
                vol.status = VolumeStatus.UNLOCKED
                vol.unlocked_at = time.time()
                return True
        return False

    def lock_volume(self, index: int) -> bool:
        if 0 <= index < len(self._volumes):
            vol = self._volumes[index]
            if vol.status in (VolumeStatus.UNLOCKED, VolumeStatus.MOUNTED):
                vol.status = VolumeStatus.LOCKED
                vol.mount_point = ""
                vol.unlocked_at = 0
                return True
        return False

    def mount_volume(self, index: int) -> bool:
        if 0 <= index < len(self._volumes):
            vol = self._volumes[index]
            if vol.status == VolumeStatus.UNLOCKED:
                vol.status = VolumeStatus.MOUNTED
                return True
        return False

    def add_key_slot(self, vol_index: int, slot_type: KeySlotType) -> Optional[KeySlot]:
        if 0 <= vol_index < len(self._volumes):
            vol = self._volumes[vol_index]
            next_id = max(s.slot_id for s in vol.key_slots) + 1 if vol.key_slots else 0
            slot = KeySlot(next_id, slot_type)
            vol.key_slots.append(slot)
            return slot
        return None

    def remove_key_slot(self, vol_index: int, slot_id: int) -> bool:
        if 0 <= vol_index < len(self._volumes):
            vol = self._volumes[vol_index]
            for i, slot in enumerate(vol.key_slots):
                if slot.slot_id == slot_id:
                    vol.key_slots.pop(i)
                    return True
        return False

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
        if self._view_mode == "backups":
            return self._backups
        return self._volumes

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    @property
    def volumes(self) -> List[EncryptedVolume]:
        return list(self._volumes)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def mounted_count(self) -> int:
        return sum(1 for v in self._volumes if v.is_mounted)

    def render_volumes(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 🔐 Encryption Manager ({self.mounted_count} mounted)")
        lines.append("─" * width)
        for i, vol in enumerate(self._volumes):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {vol.display}")
            lines.append(f"   {vol.device} → {vol.mapper or '—'} | {vol.filesystem} | {vol.size_str}")
            lines.append(f"   [{vol.usage_bar}] {vol.usage_pct:.0f}% | Cipher: {vol.cipher} {vol.key_size}-bit")
            lines.append(f"   Keys: {vol.active_slots} active | Mount: {vol.mount_point or '—'}")
            lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Details  U:Unlock  L:Lock  M:Mount  K:Keys  B:Backups")
        return lines

    def render_details(self, width: int = 70) -> List[str]:
        vol = self.get_selected_item()
        if not vol:
            return ["No volume selected"]
        lines = []
        lines.append(f" {vol.enc_icon} {vol.name}")
        lines.append("─" * width)
        lines.append(f" Device:       {vol.device}")
        lines.append(f" Mapper:       {vol.mapper or '—'}")
        lines.append(f" Encryption:   {vol.encryption.value}")
        lines.append(f" Status:       {vol.status.value}")
        lines.append(f" Cipher:       {vol.cipher}")
        lines.append(f" Key Size:     {vol.key_size} bit")
        lines.append(f" Hash:         {vol.hash}")
        lines.append(f" Iterations:   {vol.iterations}")
        lines.append(f" Size:         {vol.size_str} [{vol.usage_bar}] {vol.usage_pct:.0f}%")
        lines.append(f" Filesystem:   {vol.filesystem}")
        lines.append(f" Mount:        {vol.mount_point or '—'}")
        lines.append(f" Active Keys:  {vol.active_slots}")
        for slot in vol.key_slots:
            lines.append(f"   {slot.display}")
        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_backups(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 💾 LUKS Header Backups ({len(self._backups)})")
        lines.append("─" * width)
        for i, backup in enumerate(self._backups):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {backup.volume_name} — {backup.filename}")
            lines.append(f"   Size: {backup.size_kb} KB | Created: {backup.time_ago}")
            lines.append("")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {"details": self.render_details, "backups": self.render_backups}
        renderer = renderers.get(self._view_mode, self.render_volumes)
        return renderer(width)

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "details":
            if key == "Escape":
                self.set_view("volumes")
                return "back"
            return None
        if self._view_mode == "backups":
            if key == "Escape":
                self.set_view("volumes")
                return "back"
            if key == "ArrowUp":
                self.select_up()
                return "select_up"
            if key == "ArrowDown":
                self.select_down()
                return "select_down"
            return None
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        if key == "ArrowDown":
            self.select_down()
            return "select_down"
        if key == "Enter":
            self.set_view("details")
            return "details"
        if key == "u":
            return "unlock" if self.unlock_volume(self._selected_index) else "unlock_failed"
        if key == "l":
            return "lock" if self.lock_volume(self._selected_index) else "lock_failed"
        if key == "m":
            return "mount" if self.mount_volume(self._selected_index) else "mount_failed"
        if key == "b":
            self.set_view("backups")
            return "backups"
        return None
