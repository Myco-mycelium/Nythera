"""
Nyrqis Boot Manager — system boot configuration application.

Features:
- GRUB2 bootloader configuration
- Kernel selection and default boot
- Boot parameters (kernel command line)
- Initramfs management
- Boot menu customization (timeout, theme, colors)
- UEFI/BIOS boot mode detection
- Recovery mode boot options
- Boot partition management
- Dual boot detection
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class BootMode(Enum):
    UEFI = "UEFI"
    BIOS = "Legacy BIOS"
    UEFI_CSM = "UEFI (CSM)"


class KernelStatus(Enum):
    ACTIVE = "active"
    INSTALLED = "installed"
    RECOVERY = "recovery"
    RT = "real-time"


class InitramfsType(Enum):
    DRACUT = "dracut"
    MKINITCPIO = "mkinitcpio"
    MKINITRAMFS = "mkinitramfs"
    CUSTOM = "custom"


BOOT_MODE_ICONS = {
    BootMode.UEFI: "UEFI",
    BootMode.BIOS: "BIOS",
    BootMode.UEFI_CSM: "CSM",
}

KERNEL_STATUS_ICONS = {
    KernelStatus.ACTIVE: "🟢",
    KernelStatus.INSTALLED: "⚪",
    KernelStatus.RECOVERY: "🔴",
    KernelStatus.RT: "⚡",
}


@dataclass
class KernelEntry:
    """A boot kernel entry."""
    version: str
    status: KernelStatus = KernelStatus.INSTALLED
    initramfs: InitramfsType = InitramfsType.DRACUT
    initramfs_size_mb: float = 65.0
    has_modules: bool = True
    is_default: bool = False
    # Kernel parameters
    root_device: str = "/dev/sda3"
    root_uuid: str = ""
    boot_params: List[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.root_uuid:
            self.root_uuid = hashlib.md5(self.version.encode()).hexdigest()[:8]

    @property
    def display(self) -> str:
        icon = KERNEL_STATUS_ICONS.get(self.status, "❓")
        default = " ★" if self.is_default else ""
        return f"{icon} {self.version}{default}"

    @property
    def full_line(self) -> str:
        """GRUB menuentry title."""
        status = self.status.value.title()
        return f"Nyrqis OS {self.version} ({status})"

    @property
    def date_str(self) -> str:
        return datetime.fromtimestamp(self.created).strftime("%Y-%m-%d")


@dataclass
class BootEntry:
    """A boot menu entry (OS or tool)."""
    name: str
    icon: str = ""
    os_type: str = ""
    kernel_version: str = ""
    is_default: bool = False
    hidden: bool = False
    recovery: bool = False
    position: int = 0

    @property
    def display(self) -> str:
        default = " ★" if self.is_default else ""
        hidden_mark = " 👁️‍🗨️" if self.hidden else ""
        return f"{self.icon} {self.name}{default}{hidden_mark}"


@dataclass
class GRUBConfig:
    """GRUB bootloader configuration."""
    # Menu settings
    timeout_seconds: int = 5
    hidden_timeout: bool = False
    default_entry: int = 0
    # Appearance
    theme: str = "Nyrqis Dark"
    terminal_font: str = "Terminus 14"
    menu_width: int = 800
    menu_height: int = 600
    bg_color: str = "#1a1b26"
    menu_bg: str = "#24283b"
    title_color: str = "#7aa2f7"
    selected_color: str = "#bb9af7"
    text_color: str = "#c0caf5"
    # Security
    password_protected: bool = False
    secure_boot: bool = True
    # Advanced
    generate_reboot: bool = True
    os_prober: bool = True  # detect other OSes
    quiet_boot: bool = True
    splash: bool = True


@dataclass
class BootPartition:
    """A boot-related partition."""
    name: str
    device: str
    mount_point: str
    filesystem: str
    size_mb: int
    used_mb: float = 0.0
    is_efi: bool = False

    @property
    def display_size(self) -> str:
        if self.size_mb >= 1024:
            return f"{self.size_mb / 1024:.1f} GB"
        return f"{self.size_mb} MB"


# ─── Boot Manager ────────────────────────────────────────────────────────


class BootManager:
    """
    Boot configuration manager for Nyrqis OS.
    """

    def __init__(self):
        self._kernels: List[KernelEntry] = []
        self._boot_entries: List[BootEntry] = []
        self._config: GRUBConfig = GRUBConfig()
        self._boot_partitions: List[BootPartition] = []
        self._boot_mode: BootMode = BootMode.UEFI
        self._selected_index: int = 0
        self._view_mode: str = "overview"  # overview, kernels, entries, config, partitions
        self._config_section: int = 0  # for config editing
        self._config_sections = ["Menu", "Appearance", "Security", "Advanced"]

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        self._kernels = [
            KernelEntry("6.11.0-nyrqis", KernelStatus.ACTIVE, InitramfsType.DRACUT, 68.5,
                        is_default=True, boot_params=["quiet", "splash", "loglevel=3",
                        "nvidia-drm.modeset=1", "rd.udev.log_priority=3"],
                        created=now - 86400),
            KernelEntry("6.10.5-nyrqis", KernelStatus.INSTALLED, InitramfsType.DRACUT, 65.2,
                        boot_params=["quiet", "splash", "loglevel=3"],
                        created=now - 604800),
            KernelEntry("6.11.0-nyrqis", KernelStatus.RECOVERY, InitramfsType.DRACUT, 42.1,
                        boot_params=["single", "loglevel=5"],
                        created=now - 86400),
            KernelEntry("6.9.12-rt-nyrqis", KernelStatus.RT, InitramfsType.DRACUT, 62.8,
                        boot_params=["quiet", "threadirqs", "preempt=full", "loglevel=3"],
                        created=now - 1209600),
        ]

        self._boot_entries = [
            BootEntry("Nyrqis OS", "🍄", "Linux", "6.11.0-nyrqis",
                      is_default=True, position=0),
            BootEntry("Nyrqis OS (Recovery)", "🍄", "Linux", "6.11.0-nyrqis",
                      recovery=True, hidden=True, position=1),
            BootEntry("Windows 11", "🪟", "Windows", "", position=2),
            BootEntry("UEFI Firmware Settings", "⚙️", "UEFI", "", hidden=True, position=3),
            BootEntry("Memory Test (memtest86+)", "🧠", "Tool", "", hidden=True, position=4),
        ]

        self._boot_partitions = [
            BootPartition("EFI System", "/dev/sda1", "/boot/efi", "vfat (FAT32)", 512, 128, True),
            BootPartition("Boot", "/dev/sda2", "/boot", "ext4", 2048, 1024),
            BootPartition("EFI (Windows)", "/dev/sdc1", "/boot/efi", "vfat (FAT32)", 260, 80, True),
        ]

    # ── Kernel Operations ─────────────────────────────────────────────

    def set_default_kernel(self, index: int) -> bool:
        if 0 <= index < len(self._kernels):
            for k in self._kernels:
                k.is_default = False
            self._kernels[index].is_default = True
            self._config.default_entry = index
            return True
        return False

    def remove_kernel(self, index: int) -> bool:
        if 0 <= index < len(self._kernels):
            kernel = self._kernels[index]
            if kernel.status != KernelStatus.ACTIVE:
                self._kernels.pop(index)
                return True
        return False

    # ── Boot Entry Operations ─────────────────────────────────────────

    def set_default_entry(self, index: int) -> bool:
        if 0 <= index < len(self._boot_entries):
            for e in self._boot_entries:
                e.is_default = False
            self._boot_entries[index].is_default = True
            self._config.default_entry = index
            return True
        return False

    def toggle_hidden(self, index: int) -> bool:
        if 0 <= index < len(self._boot_entries):
            entry = self._boot_entries[index]
            entry.hidden = not entry.hidden
            return entry.hidden
        return False

    def move_entry(self, index: int, direction: int) -> bool:
        new_idx = index + direction
        if 0 <= index < len(self._boot_entries) and 0 <= new_idx < len(self._boot_entries):
            self._boot_entries[index], self._boot_entries[new_idx] = (
                self._boot_entries[new_idx], self._boot_entries[index]
            )
            for i, e in enumerate(self._boot_entries):
                e.position = i
            return True
        return False

    # ── Config Operations ─────────────────────────────────────────────

    def set_timeout(self, seconds: int) -> None:
        self._config.timeout_seconds = max(0, min(60, seconds))

    def toggle_quiet_boot(self) -> bool:
        self._config.quiet_boot = not self._config.quiet_boot
        return self._config.quiet_boot

    def toggle_splash(self) -> bool:
        self._config.splash = not self._config.splash
        return self._config.splash

    def toggle_os_prober(self) -> bool:
        self._config.os_prober = not self._config.os_prober
        return self._config.os_prober

    def toggle_secure_boot(self) -> bool:
        self._config.secure_boot = not self._config.secure_boot
        return self._config.secure_boot

    def cycle_theme(self) -> str:
        themes = ["Nyrqis Dark", "Nyrqis Light", "Nyrqis Minimal", "Nyrqis Retro"]
        idx = themes.index(self._config.theme) if self._config.theme in themes else 0
        self._config.theme = themes[(idx + 1) % len(themes)]
        return self._config.theme

    def generate_config(self) -> str:
        """Generate GRUB configuration file."""
        lines = [
            "# Nyrqis GRUB Configuration — Auto-generated",
            f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"GRUB_TIMEOUT={self._config.timeout_seconds}",
            f"GRUB_DEFAULT={self._config.default_entry}",
            f"GRUB_HIDDEN_TIMEOUT={'true' if self._config.hidden_timeout else 'false'}",
            f"GRUB_CMDLINE_LINUX_DEFAULT=\"{'quiet splash' if self._config.quiet_boot else ''}\"",
            f"GRUB_CMDLINE_LINUX=\"\"",
            "",
            f"GRUB_THEME=\"/boot/grub/themes/{self._config.theme.lower().replace(' ', '-')}/theme.txt\"",
            f"GRUB_TERMINAL_FONT=\"{self._config.terminal_font}\"",
            "",
            f"GRUB_PASSWORD={'set' if self._config.password_protected else ''}",
            f"GRUB_ENABLE_CRYPTODISK=y",
            f"GRUB_DISABLE_OS_PROBER={'false' if self._config.os_prober else 'true'}",
            "",
            "# Boot entries:",
        ]
        for i, entry in enumerate(self._boot_entries):
            if not entry.hidden:
                default = " (default)" if entry.is_default else ""
                lines.append(f"menuentry '{entry.name}'{default} {{ ... }}")

        return "\n".join(lines)

    # ── Navigation ────────────────────────────────────────────────────

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        items = self._get_current_list()
        self._selected_index = min(len(items) - 1, self._selected_index + 1)

    def get_selected_item(self):
        items = self._get_current_list()
        if 0 <= self._selected_index < len(items):
            return items[self._selected_index]
        return None

    def _get_current_list(self) -> list:
        if self._view_mode == "kernels":
            return self._kernels
        elif self._view_mode == "entries":
            return self._boot_entries
        elif self._view_mode == "partitions":
            return self._boot_partitions
        return []

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def kernels(self) -> List[KernelEntry]:
        return list(self._kernels)

    @property
    def boot_entries(self) -> List[BootEntry]:
        return list(self._boot_entries)

    @property
    def config(self) -> GRUBConfig:
        return self._config

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def boot_mode(self) -> BootMode:
        return self._boot_mode

    @property
    def active_kernel(self) -> Optional[KernelEntry]:
        for k in self._kernels:
            if k.status == KernelStatus.ACTIVE:
                return k
        return None

    # ── Rendering ─────────────────────────────────────────────────────

    def render_overview(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🚀 Boot Manager — Overview")
        lines.append("─" * width)

        # Boot mode
        lines.append(f" Boot Mode:   {BOOT_MODE_ICONS.get(self._boot_mode, '?')} ({self._boot_mode.value})")
        active = self.active_kernel
        if active:
            lines.append(f" Active Kernel: {active.version} ({active.status.value})")
        lines.append(f" Default Entry: {self._boot_entries[0].name if self._boot_entries else 'None'}")
        lines.append(f" Timeout:      {self._config.timeout_seconds}s")
        lines.append(f" Theme:        {self._config.theme}")
        lines.append(f" Secure Boot:  {'✅ Enabled' if self._config.secure_boot else '❌ Disabled'}")
        lines.append(f" Splash:       {'✅ On' if self._config.splash else '❌ Off'}")
        lines.append(f" OS Prober:    {'✅ On' if self._config.os_prober else '❌ Off'}")
        lines.append("")

        # Boot entries summary
        lines.append(f" Boot Entries ({len(self._boot_entries)}):")
        for entry in self._boot_entries:
            lines.append(f"  {entry.display}")
        lines.append("")

        # Kernels summary
        lines.append(f" Kernels ({len(self._kernels)}):")
        for kernel in self._kernels:
            lines.append(f"  {kernel.display}")

        lines.append("─" * width)
        lines.append(" K:Kernels  E:Entries  C:Config  P:Partitions")
        return lines

    def render_kernels(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🧬 Kernel Management")
        lines.append("─" * width)

        for i, kernel in enumerate(self._kernels):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {kernel.display}")
            lines.append(f"   Initramfs: {kernel.initramfs.value} ({kernel.initramfs_size_mb} MB)")
            lines.append(f"   Root: {kernel.root_device} (UUID={kernel.root_uuid})")
            if kernel.boot_params:
                lines.append(f"   Params: {' '.join(kernel.boot_params[:5])}")
            lines.append(f"   Date: {kernel.date_str}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Set default  Del:Remove  Esc:Back")
        return lines

    def render_entries(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 📋 Boot Menu Entries")
        lines.append("─" * width)

        for i, entry in enumerate(self._boot_entries):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {entry.display}")
            if entry.kernel_version:
                lines.append(f"   Kernel: {entry.kernel_version}")
            lines.append(f"   Type: {entry.os_type} | Position: {entry.position}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Set default  H:Toggle hidden")
        lines.append(" ↑/↓+Shift:Move  Esc:Back")
        return lines

    def render_config(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" ⚙️  GRUB Configuration")
        lines.append("─" * width)

        cfg = self._config

        # Section tabs
        tabs = "  ".join(
            f"[{'▸' if i == self._config_section else ' '}] {s}"
            for i, s in enumerate(self._config_sections)
        )
        lines.append(f" {tabs}")
        lines.append("─" * width)

        if self._config_section == 0:  # Menu
            lines.append(f" Timeout:          {cfg.timeout_seconds}s")
            lines.append(f" Default Entry:    #{cfg.default_entry}")
            lines.append(f" Hidden Timeout:   {'Yes' if cfg.hidden_timeout else 'No'}")
            lines.append(f" Quiet Boot:       {'Yes' if cfg.quiet_boot else 'No'}")
        elif self._config_section == 1:  # Appearance
            lines.append(f" Theme:            {cfg.theme}")
            lines.append(f" Font:             {cfg.terminal_font}")
            lines.append(f" Menu Size:        {cfg.menu_width}×{cfg.menu_height}")
            lines.append(f" Background:       {cfg.bg_color}")
            lines.append(f" Menu Background:  {cfg.menu_bg}")
            lines.append(f" Title Color:      {cfg.title_color}")
            lines.append(f" Selected Color:   {cfg.selected_color}")
            lines.append(f" Text Color:       {cfg.text_color}")
        elif self._config_section == 2:  # Security
            lines.append(f" Password:         {'Protected' if cfg.password_protected else 'None'}")
            lines.append(f" Secure Boot:      {'Enabled' if cfg.secure_boot else 'Disabled'}")
        elif self._config_section == 3:  # Advanced
            lines.append(f" OS Prober:        {'On' if cfg.os_prober else 'Off'}")
            lines.append(f" Splash Screen:    {'On' if cfg.splash else 'Off'}")
            lines.append(f" Generate /reboot: {'Yes' if cfg.generate_reboot else 'No'}")

        lines.append("")
        lines.append(f" Tab:Section  ←→:Edit  G:Generate config")
        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render_partitions(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 📁 Boot Partitions")
        lines.append("─" * width)

        for i, part in enumerate(self._boot_partitions):
            marker = "▸" if i == self._selected_index else " "
            efi = " [EFI]" if part.is_efi else ""
            lines.append(f"{marker} {part.name}{efi}")
            lines.append(f"   {part.device} → {part.mount_point}")
            lines.append(f"   {part.filesystem} | {part.display_size} | {part.used_mb} MB used")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        renderers = {
            "kernels": self.render_kernels,
            "entries": self.render_entries,
            "config": self.render_config,
            "partitions": self.render_partitions,
        }
        renderer = renderers.get(self._view_mode, self.render_overview)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "kernels":
            return self._handle_kernels_key(key)
        elif self._view_mode == "entries":
            return self._handle_entries_key(key)
        elif self._view_mode == "config":
            return self._handle_config_key(key)
        elif self._view_mode == "partitions":
            return self._handle_partitions_key(key)
        return self._handle_overview_key(key)

    def _handle_overview_key(self, key: str) -> Optional[str]:
        if key == "k":
            self.set_view("kernels")
            return "kernels"
        elif key == "e":
            self.set_view("entries")
            return "entries"
        elif key == "c":
            self.set_view("config")
            return "config"
        elif key == "p":
            self.set_view("partitions")
            return "partitions"
        return None

    def _handle_kernels_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            return "set_default" if self.set_default_kernel(self._selected_index) else "set_failed"
        elif key == "Delete":
            return "remove" if self.remove_kernel(self._selected_index) else "remove_failed"
        return None

    def _handle_entries_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            return "set_default" if self.set_default_entry(self._selected_index) else "set_failed"
        elif key == "h":
            return "toggle_hidden" if self.toggle_hidden(self._selected_index) else "toggle_failed"
        elif key == "H":
            return "move_up" if self.move_entry(self._selected_index, -1) else "move_failed"
        elif key == "L":
            return "move_down" if self.move_entry(self._selected_index, 1) else "move_failed"
        return None

    def _handle_config_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == "Tab":
            self._config_section = (self._config_section + 1) % len(self._config_sections)
            return "next_section"
        elif key == "g":
            config_text = self.generate_config()
            return "generate"
        elif key == "ArrowRight":
            self._config_section = min(len(self._config_sections) - 1, self._config_section + 1)
            return "next_section"
        elif key == "ArrowLeft":
            self._config_section = max(0, self._config_section - 1)
            return "prev_section"
        elif key == "+":
            if self._config_section == 0:
                self.set_timeout(self._config.timeout_seconds + 1)
                return "timeout_up"
        elif key == "-":
            if self._config_section == 0:
                self.set_timeout(self._config.timeout_seconds - 1)
                return "timeout_down"
        return None

    def _handle_partitions_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("overview")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        return None
