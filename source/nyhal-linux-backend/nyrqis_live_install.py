#!/usr/bin/env python3
"""
Nyrqis OS — Live OS Installer Demo
====================================

Interactive terminal-based OS installer that simulates a live Linux
installation experience. Run it to walk through a full OS install flow
with keyboard navigation — just like installing a real distro.

Usage:
    python3 nyrqis_live_install.py                  # start the installer
    python3 nyrqis_live_install.py --auto            # autopilot demo
    python3 nyrqis_live_install.py --auto 3          # autopilot with 3s steps
    python3 nyrqis_live_install.py --skip            # skip to install progress
"""

from __future__ import annotations

import os
import sys
import time
import random
import textwrap
import curses
from typing import List, Optional, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum, auto

# ---------------------------------------------------------------------------
# Curses helpers
# ---------------------------------------------------------------------------

def _init_curses(stdscreen: "curses._CursesWindow") -> None:
    curses.curs_set(0)
    stdscreen.nodelay(False)
    stdscreen.keypad(True)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)   # title bar
    curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)  # panel bg
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLUE)  # highlight
    curses.init_pair(4, curses.COLOR_GREEN, curses.COLOR_BLACK)  # success
    curses.init_pair(5, curses.COLOR_RED, curses.COLOR_BLACK)    # error
    curses.init_pair(6, curses.COLOR_CYAN, curses.COLOR_BLUE)   # accent
    curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLACK)  # normal
    curses.init_pair(8, curses.COLOR_YELLOW, curses.COLOR_BLACK) # warn
    curses.init_pair(9, curses.COLOR_MAGENTA, curses.COLOR_BLACK) # brand
    curses.init_pair(10, curses.COLOR_BLACK, curses.COLOR_CYAN)  # selected item
    curses.init_pair(11, curses.COLOR_WHITE, curses.COLOR_BLUE)  # brand light


def _wrap(stdscreen: "curses._CursesWindow", text: str, width: int) -> List[str]:
    lines = []
    for para in text.split("\n"):
        if para.strip():
            lines.extend(textwrap.wrap(para, width=width))
        else:
            lines.append("")
    return lines


def _center_text(stdscreen: "curses._CursesWindow", y: int, text: str,
                 color: int = 7, width: int = 80) -> None:
    stdscreen.attron(curses.color_pair(color))
    stdscreen.addstr(y, max(0, (width - len(text)) // 2), text)
    stdscreen.attroff(curses.color_pair(color))


def _draw_box(stdscreen: "curses._CursesWindow", y: int, x: int,
              h: int, w: int, title: str = "", border: str = "─│┌┐└┘") -> None:
    uy, ux = curses.ACS_UARROW, curses.ACS_UARROW  # fallback
    stdscreen.attron(curses.A_BOLD)
    stdscreen.addstr(y, x, title)
    stdscreen.attroff(curses.A_BOLD)
    # top border
    stdscreen.addstr(y, x + len(title), " " * (w - len(title)))
    stdscreen.addstr(y, x, border[2])
    stdscreen.addstr(y, x + 1, border[0] * (w - 2))
    stdscreen.addstr(y, x + w - 1, border[3])
    for yy in range(y + 1, y + h - 1):
        stdscreen.addstr(yy, x, border[1])
        stdscreen.addstr(yy, x + w - 1, border[1])
    stdscreen.addstr(y + h - 1, x, border[6])
    stdscreen.addstr(y + h - 1, x + 1, border[0] * (w - 2))
    stdscreen.addstr(y + h - 1, x + w - 1, border[7])


def _draw_panel(stdscreen: "curses._CursesWindow", y: int, x: int,
                h: int, w: int, title: str = "", selected: bool = False) -> None:
    color = 10 if selected else 2
    stdscreen.attron(curses.color_pair(color))
    stdscreen.attron(curses.A_BOLD if title else curses.A_NORMAL)
    stdscreen.addstr(y, x, title + (" " * max(0, w - len(title))))
    stdscreen.attroff(curses.color_pair(color))
    stdscreen.attroff(curses.A_BOLD)
    for yy in range(y + 1, y + h):
        stdscreen.attron(curses.color_pair(color))
        stdscreen.addstr(yy, x, " " * w)
        stdscreen.attroff(curses.color_pair(color))


# ---------------------------------------------------------------------------
# Installer state
# ---------------------------------------------------------------------------

class InstallerStep(Enum):
    BOOT_SPLASH = auto()
    ISO_SELECT = auto()
    WELCOME = auto()
    LANGUAGE = auto()
    KEYBOARD = auto()
    TIMEZONE = auto()
    DISK_SELECT = auto()
    PARTITIONING = auto()
    USER_SETUP = auto()
    NETWORK = auto()
    PACKAGES = auto()
    BOOTLOADER = auto()
    THEME = auto()
    SUMMARY = auto()
    INSTALLING = auto()
    COMPLETE = auto()


class FS(Enum):
    EXT4 = "ext4"
    BTRFS = "btrfs"
    XFS = "xfs"
    ZFS = "zfs"
    F2FS = "f2fs"
    SWAP = "swap"
    FAT32 = "vfat"


class BLTYPE(Enum):
    GRUB = "GRUB"
    SYSTEMD_BOOT = "systemd-boot"
    REFIND = "rEFInd"
    LIMINE = "Limine"


class PKG(Enum):
    BASE = ("base", "Core system", 450)
    DESKTOP = ("desktop", "Nyrqis Desktop", 1200)
    TERMINAL = ("terminal", "Terminal tools", 85)
    DEV = ("dev", "Development tools", 520)
    OFFICE = ("office", "Office suite", 680)
    MEDIA = ("media", "Multimedia", 420)
    GAMING = ("gaming", "Gaming support", 350)
    UTILS = ("utils", "System utilities", 120)
    NETWORK = ("network", "Network tools", 65)
    SECURITY = ("security", "Security tools", 45)

    def __init__(self, key, label, mb): ...
    @property
    def label(self): return self.value[1]
    @property
    def mb(self): return self.value[2]


@dataclass
class Disk:
    device: str = ""
    model: str = ""
    size_gb: int = 0
    interface: str = ""
    rotational: bool = False
    removable: bool = False
    partitions: int = 0
    selected: bool = False

    @property
    def type_label(self) -> str:
        if self.rotational: return "HDD"
        if "NVMe" in self.interface: return "NVMe"
        return "SSD"

    @property
    def icon(self) -> str:
        return "🔌" if self.removable else "💾"


@dataclass
class Partition:
    num: int = 0
    mount: str = ""
    fs: FS = FS.EXT4
    size_gb: float = 0.0
    flags: List[str] = field(default_factory=list)
    is_new: bool = False

    @property
    def fs_icon(self) -> str:
        return {"ext4": "📁", "btrfs": "🌲", "xfs": "📁", "zfs": "🌊",
                "f2fs": "📁", "swap": "💤", "vfat": "📁"}.get(self.fs.value, "📁")


@dataclass
class Layout:
    name: str = ""
    desc: str = ""
    partitions: List[Partition] = field(default_factory=list)
    recommended: bool = False
    selected: bool = False


class User:
    def __init__(self, username="", password="", full_name="", hostname="nyrqis", auto_login=True):
        self.username = username
        self.password = password
        self.full_name = full_name
        self.hostname = hostname
        self.auto_login = auto_login


class InstallLog:
    def __init__(self, ts: float, step: str, msg: str, level: str = "info"):
        self.ts = ts
        self.step = step
        self.msg = msg
        self.level = level

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.ts))

    @property
    def icon(self) -> str:
        return {"info": "ℹ", "warn": "⚠", "error": "✗", "success": "✓"}.get(self.level, "?")


@dataclass
class NavState:
    current_step: InstallerStep = InstallerStep.BOOT_SPLASH
    selected_disk: int = 0
    selected_layout_idx: int = 0
    selected_pkg_indices: set = field(default_factory=lambda: {0, 1, 2, 3, 7, 8, 9})
    bl_type: BLTYPE = BLTYPE.SYSTEMD_BOOT
    boot_timeout: int = 5
    theme_index: int = 0
    preview_enabled: bool = False
    log_entries: List[InstallLog] = field(default_factory=list)
    progress: float = 0.0
    install_done: bool = False
    scroll_offset: int = 0
    cursor_y: int = 0
    page: int = 0
    _boot_time: float = field(default_factory=time.time)
    gui_mode: bool = False  # when True, use PIL-based rendering


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------

ISO_MEDIA = [
    {"label": "Nyrqis OS 0.14.39 — Installer", "size": "3.2 GB", "type": "ISO",
     "contents": "Desktop, development tools, games", "status": "inserted"},
    {"label": "Nyrqis OS 0.14.39 — Live", "size": "2.8 GB", "type": "ISO",
     "contents": "Live desktop only, no install", "status": "available"},
    {"label": "Nyrqis OS 0.14.39 — Minimal", "size": "850 MB", "type": "ISO",
     "contents": "Base system + CLI only", "status": "available"},
    {"label": "Nyrqis OS 0.14.39 — ARM64", "size": "2.1 GB", "type": "ISO",
     "contents": "ARM64 build (Raspberry Pi, etc.)", "status": "available"},
    {"label": "No media inserted", "size": "—", "type": "—",
     "contents": "Insert installation media to continue", "status": "empty"},
]

SAMPLE_DISKS = [
    Disk("/dev/nvme0n1", "Samsung 990 Pro 2TB", 2000, "NVMe", False, False, 3),
    Disk("/dev/sda", "Samsung 870 EVO 1TB", 1000, "SATA", False, False, 2),
    Disk("/dev/sdb", "WD Red Plus 4TB", 4000, "SATA", True, False, 1),
    Disk("/dev/sdc", "SanDisk Ultra 64GB", 64, "USB", False, True, 1),
]

THEMES = [
    {"name": "Nyrqis Dark", "desc": "Default — deep space blue accents",
     "wall": (25, 25, 45), "accent": (80, 180, 255), "mode": "dark"},
    {"name": "Nyrqis Light", "desc": "Bright — clean daylight palette",
     "wall": (235, 238, 245), "accent": (40, 110, 220), "mode": "light"},
    {"name": "Midnight Ember", "desc": "Warm — charcoal with ember orange",
     "wall": (28, 22, 26), "accent": (255, 140, 70), "mode": "dark"},
    {"name": "Forest Fern", "desc": "Natural — deep green with moss accents",
     "wall": (20, 32, 26), "accent": (90, 200, 130), "mode": "dark"},
    {"name": "Amethyst", "desc": "Vibrant — purple gradient with violet accents",
     "wall": (34, 22, 48), "accent": (170, 120, 255), "mode": "dark"},
]

SAMPLE_LAYOUTS = [
    Layout("Guided — Use entire disk",
          "LVM with separate /, /home, /boot/efi, swap",
          [
              Partition(1, "/boot/efi", FS.FAT32, 0.5, ["boot", "efi"], True),
              Partition(2, "/", FS.BTRFS, 50.0, ["root"], True),
              Partition(3, "/home", FS.BTRFS, 1400.0, [], True),
              Partition(4, "swap", FS.SWAP, 16.0, [], True),
          ],
          recommended=True),
    Layout("Guided — Simple",
          "Single root partition, EFI, swap",
          [
              Partition(1, "/boot/efi", FS.FAT32, 0.5, ["boot", "efi"], True),
              Partition(2, "/", FS.EXT4, 1900.0, ["root"], True),
              Partition(3, "swap", FS.SWAP, 16.0, [], True),
          ]),
    Layout("Manual",
          "Full manual control — create partitions yourself",
          []),
    Layout("Install alongside existing OS",
          "Dual-boot: shrink existing, install Nyrqis",
          [
              Partition(1, "/boot/efi", FS.FAT32, 0.5, ["boot", "efi"], True),
              Partition(2, "/", FS.BTRFS, 50.0, ["root"], True),
              Partition(3, "swap", FS.SWAP, 8.0, [], True),
          ]),
]

# ---------------------------------------------------------------------------
# Screen renderers
# ---------------------------------------------------------------------------

class Screen:
    """Base class for installer screens — each renders into a given curses pad."""

    STEP: InstallerStep
    TITLE: str = ""
    SUBTITLE: str = ""

    def render(self, stdscreen: "curses._CursesWindow", ny: int, nx: int,
               nav: NavState) -> None:
        raise NotImplementedError

    def handle_input(self, key: int, nav: NavState) -> Optional[InstallerStep]:
        """Return next step on navigation, or None to stay."""
        raise NotImplementedError


# ── Boot splash ───────────────────────────────────────────────────────────

class BootSplashScreen(Screen):
    STEP = InstallerStep.BOOT_SPLASH
    TITLE = "Nyrqis OS"
    SUBTITLE = "Build a platform worthy of the decades ahead"

    _KERNEL_MESSAGES = [
        "[    0.000000] Linux version 6.8.0-nyrqis+ (builder@nyrqis) (gcc-13)",
        "[    0.000000] Command line: root=/dev/nvme0n1p2 ro quiet splash",
        "[    0.000000] Kernel command line: root=UUID=abcd1234 ro quiet",
        "[    0.000000] BIOS-provided physical RAM map:",
        "[    0.000000] BIOS-e820: [mem 0x0000000000000000-0x000000000009fbff] usable",
        "[    0.000000] BIOS-e820: [mem 0x0000000000100000-0x00007fffffffffff] usable",
        "[    0.000000] NX (Execute Disable) protection: active",
        "[    0.000000] SMBIOS 3.4.0 present.",
        "[    0.000000] DMI: Nyrqis Development Board v1.0",
        "[    0.000000] Last level iTLB entries: 4KB 512, 2MB 512, 4MB 256",
        "[    0.000000] Found SMP MP-table at [mem 0x000f6140] mapped at [pa 0xf6140]",
        "[    0.000000] Initramfs unpacking daemon started",
        "[    0.000000] Freeing initrd memory: 2048K",
        "[    0.012345] ACPI: Core revision 20231115",
        "[    0.023456] ACPI: 5 ACPI AML tables successfully acquired and loaded",
        "[    0.045678] Memory: 32768M/32768M available (24576M kernel, 8192M user)",
        "[    0.067890] SLUB: HWalign=64, Order=0-3, Minobjects=0, CPUs=8, nodes=1",
        "[    0.089012] rcu: Preemptible hierarchical RCU implementation.",
        "[    0.112345] NR_IRQS: 524288, nr_irqs: 32768, preallocated irqs: 16",
        "[    0.134567] random: crng init done (entropy pool: 256 bits)",
        "[    0.156789] smpboot: Allowing 8 CPUs, 0 hotplug CPUs",
        "[    0.178901] PM: Registered nosave memory: [mem 0x000a0000-0x000fffff]",
        "[    0.201234] pci_bus 0000:00: root bus resource [io  0x0000-0x0cf7]",
        "[    0.223456] pci 0000:00:00.0: [8086:5840] type 00 class 0x060000",
        "[    0.245678] pci 0000:00:02.0: [8086:5881] type 00 class 0x030000 (VGA)",
        "[    0.267890] pci 0000:00:14.0: [8086:58f1] type 00 class 0x0c0330 (USB3)",
        "[    0.289012] nvme nvme0: pci function 0000:00:0e.0",
        "[    0.312345] nvme0n1: pci function 0000:00:0e.0",
        "[    0.334567] NVMe device nvme0n1: 2000 GB, 4096 bytes, 3906250000 sectors",
        "[    0.356789] scsi host0: nvme",
        "[    0.378901] sd 0:0:0:0: [sda] 2000000000 512-byte logical blocks",
        "[    0.401234] md: RAID0 personality registered",
        "[    0.423456] md: RAID1 personality registered",
        "[    0.445678] md: RAID10 personality registered",
        "[    0.467890] device-mapper: ioctl on foil failed: No such file or directory",
        "[    0.489012] nvme nvme0: 8/0/0 default/read/poll queues",
        "[    0.512345] EXT4-fs (nvme0n1p2): mounted filesystem with ordered data mode.",
        "[    0.534567] VFS: Mounted root (ext4 filesystem) on device 259:2.",
        "[    0.556789] devtmpfs: initialized",
        "[    0.578901] clocksource: timekeeping: Switched to clocksource arch_sys_counter",
        "[    0.601234] systemd[1]: Started System Initialization.",
        "[    0.623456] systemd[1]: Reached target Local File Systems.",
        "[    0.645678] systemd[1]: Starting Nyrqis Display Manager...",
        "[    0.667890] systemd[1]: Started Nyrqis Display Manager.",
        "[    0.689012] systemd[1]: Reached target Graphical Interface.",
        "[    0.712345] [drm] Initialized nyrqis-drm 1.0.0 for uvd0 on minor 0",
        "[    0.734567] [drm] Found VRAM at 0xc0000000 256MB",
        "[    0.756789] [drm] Initialized async page flip support",
        "[    0.778901] ALSA device list:",
        "[    0.801234]   No soundcards found.",
        "[    0.823456] input: Nyrqis Keyboard as /devices/platform/KBD/serio0/input0",
        "[    0.845678] input: Nyrqis Mouse as /devices/platform/PTR/serio1/input1",
        "[    0.867890] usb 1-1: new high-speed USB device number 2 using xhci_hcd",
        "[    0.889012] 8192K cache, 8 CPUs, 32768MB RAM, 1.2.0-nyrqis SMP PREEMPT_DYNAMIC",
    ]

    _frames = [
        "  ██████████████████████████████████████████",
        "  ██▓▒░ N Y R Q I S   B O O T ░▒▓█████████",
        "  ██                                          ██",
        "  ██   ┌────────────────────────────────────┐ ██",
        "   ██ │                                    │  ██",
        "   ██ │        ██████████████████████       │  ██",
        "   ██ │        ██   NYRQUIS OS   ██       │  ██",
        "   ██ │        ██████████████████████       │  ██",
        "   ██ │                                    │  ██",
        "   ██ │    Starting system services...     │  ██",
        "   ██ │    Please wait.                   │  ██",
        "   ██ └────────────────────────────────────┘  ██",
        "  ██                                          ██",
        "  ██████████████████████████████████████████",
    ]

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()

        # Boot splash frame (centered)
        stdscreen.addstr(2, nx // 2 - 30, "╔" + "═" * 59 + "╗")
        stdscreen.addstr(3, nx // 2 - 30, "║" + " " * 59 + "║")
        stdscreen.addstr(3, nx // 2 - 28, "  " + "▒" * 55 + "  ")
        stdscreen.addstr(5, nx // 2 - 30, "║" + " " * 59 + "║")
        stdscreen.addstr(7, nx // 2 - 28, "  " + "▒" * 55 + "  ")
        stdscreen.addstr(9, nx // 2 - 30, "║" + " " * 59 + "║")
        stdscreen.addstr(11, nx // 2 - 30, "╚" + "═" * 59 + "╝")

        stdscreen.attron(curses.color_pair(9) | curses.A_BOLD)
        _center_text(stdscreen, 4, "N Y R Q I S   O S", 9, nx)
        stdscreen.attroff(curses.color_pair(9) | curses.A_BOLD)

        stdscreen.attron(curses.color_pair(3))
        _center_text(stdscreen, 6, "Nykernel v0.14.39     NyHAL Linux Backend",
                     3, nx)
        stdscreen.attroff(curses.color_pair(3))

        # animated dots
        dots = "." * (int(time.time()) % 4)
        stdscreen.attron(curses.color_pair(7))
        _center_text(stdscreen, 8, "  Loading initramfs" + dots, 7, nx)
        _center_text(stdscreen, 10, "  Please wait...", 7, nx)
        stdscreen.attroff(curses.color_pair(7))

        # boot progress bar
        bar_x = nx // 2 - 25
        bar_w = 50
        pct = min(100, (time.time() % 8) / 8 * 100)
        filled = int(pct / 100 * bar_w)
        stdscreen.attron(curses.color_pair(4))
        stdscreen.addstr(12, bar_x, "█" * filled + "░" * (bar_w - filled))
        stdscreen.attroff(curses.color_pair(4))
        stdscreen.addstr(12, bar_x + bar_w + 2, f"{pct:.0f}%")

        # Scrolling kernel messages at the bottom
        boot_time = getattr(nav, "_boot_time", time.time())
        nav._boot_time = boot_time
        elapsed = time.time() - boot_time
        msg_idx = int(elapsed * 0.8)  # ~8 messages per second
        max_msgs = (ny - 14) if ny > 14 else 6
        start_idx = max(0, msg_idx - max_msgs)
        visible_msgs = self._KERNEL_MESSAGES[start_idx:msg_idx + 1]

        msg_y = ny - 1
        stdscreen.attron(curses.A_DIM | curses.color_pair(7))
        for i, msg in enumerate(reversed(visible_msgs[-max_msgs:])):
            yy = msg_y - i
            if yy > 12:
                prefix = "[t+%.3fs]" % (elapsed - (msg_idx - start_idx - i) * 0.8)
                col = 2 if i % 2 == 0 else 1
                stdscreen.attron(curses.color_pair(col) | curses.A_DIM)
                stdscreen.addstr(yy, 1, prefix[:12])
                stdscreen.attroff(curses.color_pair(col) | curses.A_DIM)
                stdscreen.addstr(yy, 14, msg[:nx - 18])
                stdscreen.attroff(curses.A_DIM)
        stdscreen.attroff(curses.A_DIM | curses.color_pair(7))

    def handle_input(self, key, nav):
        if key in (curses.KEY_ENTER, 10, 13, ord(" "), ord("\n")):
            return InstallerStep.ISO_SELECT
        return None


# ── Welcome ───────────────────────────────────────────────────────────────

class IsoSelectScreen(Screen):
    STEP = InstallerStep.ISO_SELECT
    TITLE = "Select Installation Media"
    SUBTITLE = "Choose the ISO to install from"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        stdscreen.attron(curses.color_pair(6))
        stdscreen.addstr(2, 7, "  Select the installation medium to use.")
        stdscreen.addstr(3, 7, "  The media must be inserted before continuing.")
        stdscreen.attroff(curses.color_pair(6))

        for i, iso in enumerate(ISO_MEDIA):
            yy = 5 + i
            if yy >= ny - 3:
                break
            if iso["status"] == "inserted":
                stdscreen.attron(curses.color_pair(3) | curses.A_REVERSE | curses.A_BOLD)
                stdscreen.addstr(yy, 7, f"▶ {i + 1}. {iso['label']}")
                stdscreen.attroff(curses.color_pair(3) | curses.A_REVERSE | curses.A_BOLD)
            elif iso["status"] == "available":
                stdscreen.attron(curses.color_pair(7))
                stdscreen.addstr(yy, 7, f"  {i + 1}. {iso['label']}")
                stdscreen.attroff(curses.color_pair(7))
            else:  # empty
                stdscreen.attron(curses.color_pair(8) | curses.A_DIM)
                stdscreen.addstr(yy, 7, f"  {i + 1}. {iso['label']}")
                stdscreen.attroff(curses.color_pair(8) | curses.A_DIM)

            if iso["status"] != "empty":
                stdscreen.addstr(yy, 32, f"{iso['size']:<8} {iso['type']:<5} {iso['contents']}")

        # Details panel (right side)
        if ISO_MEDIA and ISO_MEDIA[0]["status"] == "inserted":
            dx = nx // 2 + 2
            dw = nx - dx - 7
            _draw_panel(stdscreen, 5, dx, ny - 7, dw, "Media Details", selected=True)
            d = ISO_MEDIA[0]
            lines = [
                f"Label:   {d['label']}",
                f"Size:    {d['size']}",
                f"Type:    {d['type']}",
                f"Contents: {d['contents']}",
                "",
                "  Status:  ✅ Inserted",
                "",
                "  SHA-256: a1b2c3d4e5...",
                "  Signature: ✓ verified",
                "  Publisher: Nyrqis Project",
                "",
                "  ✓ Media ready for install.",
            ]
            for i, line in enumerate(lines):
                if 6 + i < ny - 1:
                    stdscreen.attron(curses.color_pair(7))
                    stdscreen.addstr(6 + i, dx + 2, line[:dw - 4])
                    stdscreen.attroff(curses.color_pair(7))

        hby = max(5 + len(ISO_MEDIA) + 2, ny - 4)
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue with selected media")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(8))
        stdscreen.addstr(hby + 1, 7, "    Eject and insert different media")
        stdscreen.addstr(hby + 2, 7, "    Back")
        stdscreen.attroff(curses.color_pair(8))

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate  ·  Enter select  ·  Esc back", curses.A_DIM)

    def handle_input(self, key, nav):
        if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.WELCOME
        elif key == 27:
            return InstallerStep.BOOT_SPLASH
        return None


class WelcomeScreen(Screen):
    STEP = InstallerStep.WELCOME
    TITLE = "Welcome to Nyrqis OS"
    SUBTITLE = "Choose your installation path"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        y = 4
        lines = [
            "This will install Nyrqis OS on your computer.",
            "",
            "Before you begin, make sure you have:",
            "  • A stable power source (laptop connected to charger)",
            "  • At least 20 GB of free disk space",
            "  • A working internet connection (for package downloads)",
            "",
            "The installer will guide you through:",
            "  1. Selecting your language and keyboard layout",
            "  2. Choosing a disk and partition scheme",
            "  3. Creating your user account",
            "  4. Selecting software packages",
            "  5. Installing the bootloader",
            "",
            "Your current operating system and data will not be affected",
            "unless you choose to overwrite them.",
        ]
        for i, line in enumerate(lines):
            stdscreen.attron(curses.color_pair(7))
            stdscreen.addstr(y + i, 7, line[:nx - 14])
            stdscreen.attroff(curses.color_pair(7))

        by = y + len(lines) + 2
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(by, 7, "  > Start installation")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(7))
        stdscreen.addstr(by + 1, 7, "    Quit")
        stdscreen.attroff(curses.color_pair(7))

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate  ·  Enter select  ·  Esc back",
                         curses.A_DIM)

    def handle_input(self, key, nav):
        if key in (curses.KEY_UP,):
            nav.page = 0
            return None
        if key in (curses.KEY_DOWN,):
            nav.page = 1
            return None
        if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            if nav.page == 0:
                return InstallerStep.LANGUAGE
            else:
                return None  # quit not implemented in demo
        return None


# ── Language ──────────────────────────────────────────────────────────────

_LANGUAGES = [
    ("English (United States)", "en_US.UTF-8"),
    ("English (United Kingdom)", "en_GB.UTF-8"),
    ("Español (España)", "es_ES.UTF-8"),
    ("Français (France)", "fr_FR.UTF-8"),
    ("Deutsch (Deutschland)", "de_DE.UTF-8"),
    ("Português (Brasil)", "pt_BR.UTF-8"),
    ("Русский (Россия)", "ru_RU.UTF-8"),
    ("中文 (简体)", "zh_CN.UTF-8"),
    ("日本語 (日本)", "ja_JP.UTF-8"),
    ("한국어 (대한민국)", "ko_KR.UTF-8"),
    ("العربية (المملكة العربية السعودية)", "ar_SA.UTF-8"),
    ("हिन्दी (भारत)", "hi_IN.UTF-8"),
]


class LanguageScreen(Screen):
    STEP = InstallerStep.LANGUAGE
    TITLE = "Select Language"
    SUBTITLE = "Choose your system language"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        col_w = (nx - 20) // 2
        # left column labels, right column values
        for i, (name, code) in enumerate(_LANGUAGES):
            if i >= ny - 4:
                break
            yy = 3 + i
            sel = nav.page * 6 + i  # two pages of 6
            if sel >= len(_LANGUAGES):
                break
            name, code = _LANGUAGES[sel]
            stdscreen.attron(curses.color_pair(7))
            stdscreen.addstr(yy, 7, f"{sel + 1:>2}. {name}")
            stdscreen.attroff(curses.color_pair(7))
            stdscreen.addstr(yy, 7 + col_w, f"    {code}")
            if sel == nav.selected_disk:  # reuse as language index
                stdscreen.attron(curses.A_REVERSE)
                stdscreen.addstr(yy, 7, f"{sel + 1:>2}. {name}")
                stdscreen.attroff(curses.A_REVERSE)

        # hack: keep actual language selection state
        # We'll repurpose nav.selected_disk temporarily to hold lang index
        # Better approach: just store in nav as an attribute
        if not hasattr(nav, "lang_index"):
            nav.lang_index = 0
        if nav.lang_index < len(_LANGUAGES):
            stdscreen.attron(curses.color_pair(3) | curses.A_BOLD)
            yy = 3 + nav.lang_index
            name, code = _LANGUAGES[nav.lang_index]
            stdscreen.addstr(yy, 7, f">> {name}")
            stdscreen.attroff(curses.color_pair(3) | curses.A_BOLD)

        hby = max(3 + len(_LANGUAGES) + 2, ny - 4)
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(8))
        stdscreen.addstr(hby + 1, 7, "    Set region automatically")
        stdscreen.attroff(curses.color_pair(8))

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate  ·  Enter select  ·  Esc back",
                         curses.A_DIM)

    def handle_input(self, key, nav):
        if not hasattr(nav, "lang_index"):
            nav.lang_index = 0
        if key == curses.KEY_UP:
            nav.lang_index = (nav.lang_index - 1) % len(_LANGUAGES)
        elif key == curses.KEY_DOWN:
            nav.lang_index = (nav.lang_index + 1) % len(_LANGUAGES)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.KEYBOARD
        return None


# ── Keyboard ──────────────────────────────────────────────────────────────

_KEYBOARDS = [
    ("English (US)", "us"),
    ("English (UK)", "gb"),
    ("German (Germany)", "de"),
    ("French (France)", "fr"),
    ("Spanish (Spain)", "es"),
    ("Portuguese (Brazil)", "br"),
    ("Russian", "ru"),
    ("Japanese", "jp"),
    ("Korean", "kr"),
    ("Chinese (Simplified)", "cn"),
    ("Turkish", "tr"),
    ("Swedish", "se"),
    ("Norwegian", "no"),
    ("Finnish", "fi"),
    ("Danish", "dk"),
    ("Italian", "it"),
    ("Dutch", "nl"),
    ("Polish", "pl"),
    ("Arabic", "ara"),
    ("Hebrew", "il"),
]


class KeyboardScreen(Screen):
    STEP = InstallerStep.KEYBOARD
    TITLE = "Select Keyboard Layout"
    SUBTITLE = "Choose your keyboard layout"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        for i, (name, code) in enumerate(_KEYBOARDS):
            if i >= ny - 4:
                break
            yy = 3 + i
            stdscreen.attron(curses.color_pair(7))
            stdscreen.addstr(yy, 7, f"{i + 1:>2}. {name}")
            stdscreen.attroff(curses.color_pair(7))
            stdscreen.addstr(yy, 20, f"  ({code})")

        if not hasattr(nav, "kb_index"):
            nav.kb_index = 0
        kb_idx = nav.kb_index % len(_KEYBOARDS)
        stdscreen.attron(curses.color_pair(3) | curses.A_BOLD)
        yy = 3 + kb_idx
        name, code = _KEYBOARDS[kb_idx]
        stdscreen.addstr(yy, 7, f">> {name}")
        stdscreen.attroff(curses.color_pair(3) | curses.A_BOLD)

        hby = max(3 + len(_KEYBOARDS) + 2, ny - 4)
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate  ·  Enter select  ·  Esc back",
                         curses.A_DIM)

        # live preview of keys
        if kb_idx < len(_KEYBOARDS):
            keysyms = ["Esc", "Tab", "Caps", "Shift", "Ctrl", "Alt",
                       "Space", "Enter", "Backsp", "Lady"]
            py = hby + 3
            stdscreen.attron(curses.color_pair(6))
            stdscreen.addstr(py, 7, "Preview layout: " + ", ".join(keysyms))
            stdscreen.attroff(curses.color_pair(6))

    def handle_input(self, key, nav):
        if not hasattr(nav, "kb_index"):
            nav.kb_index = 0
        if key == curses.KEY_UP:
            nav.kb_index = (nav.kb_index - 1) % len(_KEYBOARDS)
        elif key == curses.KEY_DOWN:
            nav.kb_index = (nav.kb_index + 1) % len(_KEYBOARDS)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.TIMEZONE
        return None


# ── Timezone ──────────────────────────────────────────────────────────────

_TIMEZONES = [
    ("Coordinated Universal Time", "UTC"),
    ("Eastern Time (US & Canada)", "America/New_York"),
    ("Central Time (US & Canada)", "America/Chicago"),
    ("Mountain Time (US & Canada)", "America/Denver"),
    ("Pacific Time (US & Canada)", "America/Los_Angeles"),
    ("Alaska Time", "America/Anchorage"),
    ("Hawaii Time", "Pacific/Honolulu"),
    ("Atlantic Time (Canada)", "America/Halifax"),
    ("Newfoundland Time", "America/St_Johns"),
    ("London (GMT/BST)", "Europe/London"),
    ("Central Europe", "Europe/Berlin"),
    ("Eastern Europe", "Europe/Kyiv"),
    ("Moscow", "Europe/Moscow"),
    ("Istanbul", "Europe/Istanbul"),
    ("Dubai", "Asia/Dubai"),
    ("Mumbai", "Asia/Kolkata"),
    ("Singapore", "Asia/Singapore"),
    ("Beijing", "Asia/Shanghai"),
    ("Tokyo", "Asia/Tokyo"),
    ("Seoul", "Asia/Seoul"),
    ("Sydney", "Australia/Sydney"),
    ("Auckland", "Pacific/Auckland"),
    ("São Paulo", "America/Sao_Paulo"),
    ("Buenos Aires", "America/Argentina/Buenos_Aires"),
]


class TimezoneScreen(Screen):
    STEP = InstallerStep.TIMEZONE
    TITLE = "Select Timezone"
    SUBTITLE = "Choose your timezone"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        for i, (name, tz) in enumerate(_TIMEZONES):
            if i >= ny - 4:
                break
            yy = 3 + i
            stdscreen.attron(curses.color_pair(7))
            stdscreen.addstr(yy, 7, f"{i + 1:>2}. {name}")
            stdscreen.attroff(curses.color_pair(7))
            stdscreen.addstr(yy, 28, f"  {tz}")

        if not hasattr(nav, "tz_index"):
            nav.tz_index = 0
        tz_idx = nav.tz_index % len(_TIMEZONES)
        stdscreen.attron(curses.color_pair(3) | curses.A_BOLD)
        yy = 3 + tz_idx
        name, tz = _TIMEZONES[tz_idx]
        stdscreen.addstr(yy, 7, f">> {name}")
        stdscreen.attroff(curses.color_pair(3) | curses.A_BOLD)

        hby = max(3 + len(_TIMEZONES) + 2, ny - 4)
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(8))
        stdscreen.addstr(hby + 1, 7, "    Detect timezone automatically")
        stdscreen.attroff(curses.color_pair(8))

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate  ·  Enter select  ·  Esc back",
                         curses.A_DIM)

        # Show current time for selected TZ
        if tz_idx < len(_TIMEZONES):
            py = hby + 3
            stdscreen.attron(curses.color_pair(6))
            stdscreen.addstr(py, 7, f"Current: {time.strftime('%Y-%m-%d %H:%M:%S')} (UTC)")
            stdscreen.attroff(curses.color_pair(6))

    def handle_input(self, key, nav):
        if not hasattr(nav, "tz_index"):
            nav.tz_index = 0
        if key == curses.KEY_UP:
            nav.tz_index = (nav.tz_index - 1) % len(_TIMEZONES)
        elif key == curses.KEY_DOWN:
            nav.tz_index = (nav.tz_index + 1) % len(_TIMEZONES)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.DISK_SELECT
        return None


# ── Disk selection ────────────────────────────────────────────────────────

class DiskSelectScreen(Screen):
    STEP = InstallerStep.DISK_SELECT
    TITLE = "Select Installation Disk"
    SUBTITLE = "Choose where to install Nyrqis OS"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        for i, d in enumerate(SAMPLE_DISKS):
            yy = 3 + i
            stdscreen.attron(curses.color_pair(7))
            icon = "✅" if i == nav.selected_disk else "  "
            stdscreen.addstr(yy, 7, f"{icon} {i + 1}. {d.device}")
            stdscreen.attroff(curses.color_pair(7))
            stdscreen.addstr(yy, 22, f"{d.model}")
            stdscreen.addstr(yy, 42, f"{d.size_gb} GB  {d.type_label}")
            stdscreen.addstr(yy, 54, f"{d.interface}  {d.partitions} partitions")
            if i == nav.selected_disk:
                stdscreen.attron(curses.color_pair(3) | curses.A_REVERSE)
                stdscreen.addstr(yy, 7, f"  {i + 1}. {d.device}")
                stdscreen.attroff(curses.color_pair(3) | curses.A_REVERSE)

        # Disk details panel (right side)
        d = SAMPLE_DISKS[nav.selected_disk]
        dy = 3
        dx = nx // 2 + 2
        dw = nx - dx - 7
        _draw_panel(stdscreen, dy - 1, dx, ny - dy - 1, dw,
                    "Disk Details", selected=True)
        detail_lines = [
            f"Device:    {d.device}",
            f"Model:     {d.model}",
            f"Size:      {d.size_gb} GB",
            f"Type:      {d.type_label} ({d.interface})",
            f"Partitions: {d.partitions} existing",
            f"Removable: {d.removable}",
            "",
            "⚠  All data on this disk will be",
            "   erased during installation!",
            "",
            f"Total detected: {len(SAMPLE_DISKS)} disks",
            f"Total space:    {sum(x.size_gb for x in SAMPLE_DISKS)} GB",
        ]
        for i, line in enumerate(detail_lines):
            if dy + i < ny - 1:
                stdscreen.attron(curses.color_pair(7))
                stdscreen.addstr(dy + i, dx + 2, line[:dw - 4])
                stdscreen.attroff(curses.color_pair(7))

        hby = ny - 3
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(8))
        stdscreen.addstr(hby + 1, 7, "    Refresh disk list")
        stdscreen.attroff(curses.color_pair(8))
        stdscreen.addstr(hby + 2, 7, "    Back")
        stdscreen.attroff(curses.color_pair(8))

        stdscreen.addstr(ny - 1, 5, "  ↑/↓ navigate  ·  Enter select  ·  Esc back",
                         curses.A_DIM)

    def handle_input(self, key, nav):
        if key == curses.KEY_UP:
            nav.selected_disk = (nav.selected_disk - 1) % len(SAMPLE_DISKS)
        elif key == curses.KEY_DOWN:
            nav.selected_disk = (nav.selected_disk + 1) % len(SAMPLE_DISKS)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.PARTITIONING
        return None


# ── Partitioning ──────────────────────────────────────────────────────────

class PartitioningScreen(Screen):
    STEP = InstallerStep.PARTITIONING
    TITLE = "Partitioning"
    SUBTITLE = "Choose a partition layout"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        d = SAMPLE_DISKS[nav.selected_disk]
        stdscreen.attron(curses.color_pair(6))
        stdscreen.addstr(2, 7, f"Disk: {d.device} ({d.size_gb} GB) — "
                             f"{d.partitions} existing partitions")
        stdscreen.attroff(curses.color_pair(6))

        for i, layout in enumerate(SAMPLE_LAYOUTS):
            yy = 4 + i
            sel = i == nav.selected_layout_idx
            if sel:
                stdscreen.attron(curses.color_pair(3) | curses.A_REVERSE | curses.A_BOLD)
            else:
                stdscreen.attron(curses.color_pair(7))
            icon = "★" if layout.recommended else "○"
            stdscreen.addstr(yy, 7, f"{icon} {i + 1}. {layout.name}")
            stdscreen.attroff(curses.color_pair(3) | curses.A_REVERSE | curses.A_BOLD
                            if sel else curses.color_pair(7))
            stdscreen.addstr(yy, 28, layout.desc[:nx - 36])

            if layout.partitions:
                for j, p in enumerate(layout.partitions):
                    py = yy + 1 + j
                    if py < ny - 3:
                        stdscreen.attron(curses.color_pair(7) | curses.A_DIM)
                        stdscreen.addstr(py, 9,
                                         f"    {p.fs_icon} {p.mount:<15} "
                                         f"{p.fs.value:<8} "
                                         f"{p.size_gb:>8.1f} GB"
                                         + ("  [new]" if p.is_new else ""))
                        stdscreen.attroff(curses.color_pair(7) | curses.A_DIM)

        hby = max(4 + len(SAMPLE_LAYOUTS) + 1, ny - 4)
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(8))
        stdscreen.addstr(hby + 1, 7, "    Back")
        stdscreen.attroff(curses.color_pair(8))

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate  ·  Enter select  ·  Esc back",
                         curses.A_DIM)

    def handle_input(self, key, nav):
        if key == curses.KEY_UP:
            nav.selected_layout_idx = (nav.selected_layout_idx - 1) % len(SAMPLE_LAYOUTS)
        elif key == curses.KEY_DOWN:
            nav.selected_layout_idx = (nav.selected_layout_idx + 1) % len(SAMPLE_LAYOUTS)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.USER_SETUP
        return None


# ── User setup ────────────────────────────────────────────────────────────

class UserSetupScreen(Screen):
    STEP = InstallerStep.USER_SETUP
    TITLE = "Create User Account"
    SUBTITLE = "Set up your user account"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        # Form fields
        fields = [
            ("Username", nav.user.username if hasattr(nav, "user") else "",
             "Lowercase letters, numbers, hyphens"),
            ("Full name", nav.user.full_name if hasattr(nav, "user") else "",
             "Your real name (optional)"),
            ("Hostname", nav.user.hostname if hasattr(nav, "user") else "nyrqis",
             "Computer name on the network"),
            ("Password", "*" * len(nav.user.password) if hasattr(nav, "user") else "",
             "At least 8 characters"),
            ("Confirm password", "*" * len(nav.user.password) if hasattr(nav, "user") else "",
             "Re-enter your password"),
        ]

        if not hasattr(nav, "user"):
            nav.user = User()

        form_h = len(fields) * 2 + 2
        form_y = 3
        form_x = 7
        form_w = nx - 30

        _draw_box(stdscreen, form_y - 1, form_x, form_h + 2, form_w, "Account Details")

        for i, (label, value, hint) in enumerate(fields):
            yy = form_y + i * 2
            stdscreen.attron(curses.color_pair(7))
            stdscreen.addstr(yy, form_x + 2, label)
            stdscreen.attroff(curses.color_pair(7))
            if value:
                if i == 3 or i == 4:  # password fields
                    display = "•" * min(len(value), 20)
                else:
                    display = value[:form_w - 6]
                stdscreen.attron(curses.color_pair(3))
                stdscreen.addstr(yy, form_x + 12, display)
                stdscreen.attroff(curses.color_pair(3))

            stdscreen.attron(curses.color_pair(8) | curses.A_DIM)
            stdscreen.addstr(yy + 1, form_x + 2, hint[:form_w - 6])
            stdscreen.attroff(curses.color_pair(8) | curses.A_DIM)

        # Password strength (if password set)
        pw = nav.user.password
        if pw:
            fy = form_y + 8
            strength = self._password_strength(pw)
            stdscreen.attron(curses.color_pair(7))
            stdscreen.addstr(fy, form_x + 2, "Password strength:")
            stdscreen.attroff(curses.color_pair(7))
            bar_color = {1: 5, 2: 8, 3: 3, 4: 4, 5: 4}[strength]
            stdscreen.attron(curses.color_pair(bar_color) | curses.A_BOLD)
            filled = strength * 4
            stdscreen.addstr(fy, form_x + 20, "█" * filled + "░" * (20 - filled))
            stdscreen.attroff(curses.color_pair(bar_color) | curses.A_BOLD)
            labels = ["", "Very Weak", "Weak", "Fair", "Good", "Strong"]
            stdscreen.addstr(fy, form_x + 42, labels[strength])

        # Right panel: options
        ry = form_y
        rx = form_x + form_w + 3
        rw = nx - rx - 7
        _draw_panel(stdscreen, ry - 1, rx, form_h + 2, rw, "Login Options")
        opts = [
            ("☑  Auto-login", nav.user.auto_login,
             "Log in without password on boot"),
            ("☐  Require password", not nav.user.auto_login,
             "Always require password to login"),
            ("☑  Make user administrator", True,
             "User has sudo/administrative privileges"),
        ]
        for i, (label, active, hint) in enumerate(opts):
            oyy = ry + i
            stdscreen.attron(curses.color_pair(7))
            stdscreen.addstr(oyy, rx + 2, label)
            stdscreen.attroff(curses.color_pair(7))
            stdscreen.addstr(oyy + 1, rx + 2, hint[:rw - 6],
                             curses.A_DIM)

        hby = form_y + form_h + 3
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate fields  ·  Enter continue  ·  Esc back",
                         curses.A_DIM)

    def _password_strength(self, pw: str) -> int:
        score = 0
        if len(pw) >= 8: score += 1
        if len(pw) >= 12: score += 1
        if any(c.isupper() for c in pw): score += 1
        if any(c.isdigit() for c in pw): score += 1
        if any(c in "!@#$%^&*()_+-=" for c in pw): score += 1
        return min(score, 5)

    def handle_input(self, key, nav):
        if not hasattr(nav, "user"):
            nav.user = User()
        # Simple: go next on Enter, back on Esc
        if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.NETWORK
        elif key == 27:  # Esc
            return InstallerStep.PARTITIONING
        return None


# ── Network ───────────────────────────────────────────────────────────────

class NetworkScreen(Screen):
    STEP = InstallerStep.NETWORK
    TITLE = "Network Configuration"
    SUBTITLE = "Configure your network connection"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        # Connection status
        connections = [
            ("Ethernet (enp3s0)", "connected", "192.168.1.105",
             "200 Mbps", "Wired connection active"),
            ("Wi-Fi (wlan0)", "available", "—",
             "—", "Nyrqis_Home_5G  →  Not connected"),
            ("Wi-Fi (wlan1)", "available", "—",
             "—", "Guest_Network  →  Not connected"),
        ]

        for i, (name, status, ip, speed, detail) in enumerate(connections):
            yy = 3 + i
            if status == "connected":
                stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
                icon = "✓"
            elif status == "available":
                stdscreen.attron(curses.color_pair(8))
                icon = "○"
            else:
                stdscreen.attron(curses.color_pair(7))
                icon = "✗"
            stdscreen.addstr(yy, 7, f"{icon} {name}")
            stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD
                            if status == "connected" else
                            curses.color_pair(8) if status == "available" else
                            curses.color_pair(7))
            stdscreen.addstr(yy, 30, f"{status}")
            stdscreen.addstr(yy, 42, f"{ip}")
            stdscreen.addstr(yy, 56, f"{speed}")
            stdscreen.addstr(yy, 66, detail[:nx - 73])

        hby = 3 + len(connections) + 2
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue with this connection")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(8))
        stdscreen.addstr(hby + 1, 7, "    Configure network manually")
        stdscreen.attroff(curses.color_pair(8))
        stdscreen.addstr(hby + 2, 7, "    Set up later")
        stdscreen.attroff(curses.color_pair(8))

        # Right panel: connection info
        info_x = nx // 2 + 2
        info_w = nx - info_x - 7
        _draw_panel(stdscreen, 2, info_x, hby + 3, info_w, "Connection Info")
        info_lines = [
            "Hostname:    nyrqis-pc",
            "MAC:         00:1a:2b:3c:4d:5e",
            "DHCP:        Active",
            "DNS:         8.8.8.8, 8.8.4.4",
            "Gateway:     192.168.1.1",
            "",
            "✓ Internet connection detected",
            "  Package downloads will work.",
        ]
        for i, line in enumerate(info_lines):
            if 3 + i < hby + 1:
                stdscreen.attron(curses.color_pair(7))
                stdscreen.addstr(3 + i, info_x + 2, line[:info_w - 4])
                stdscreen.attroff(curses.color_pair(7))

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate  ·  Enter select  ·  Esc back",
                         curses.A_DIM)

    def handle_input(self, key, nav):
        if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.PACKAGES
        elif key == 27:
            return InstallerStep.USER_SETUP
        return None


# ── Package selection ─────────────────────────────────────────────────────

class PackageScreen(Screen):
    STEP = InstallerStep.PACKAGES
    TITLE = "Software Selection"
    SUBTITLE = "Choose software packages to install"

    _PKG_LIST = list(PKG)

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        # Right panel: total size
        total_mb = sum(p.mb for p in PKG if p.value[0] in
                      {k for k, _ in nav.selected_pkg_indices})
        pw = 22
        px = nx - pw - 7
        _draw_panel(stdscreen, 2, px, ny - 4, pw, "Total Size")
        stdscreen.attron(curses.color_pair(6) | curses.A_BOLD)
        stdscreen.addstr(4, px + 2, f"{total_mb} MB")
        stdscreen.attroff(curses.color_pair(6) | curses.A_BOLD)
        stdscreen.addstr(6, px + 2, f"~{total_mb // 1024} GB")
        stdscreen.addstr(8, px + 2, "estimated")

        # Package list
        for i, pkg in enumerate(self._PKG_LIST):
            yy = 3 + i
            if yy >= ny - 3:
                break
            selected = i in nav.selected_pkg_indices

            stdscreen.attron(curses.color_pair(3) | curses.A_REVERSE
                            if selected else curses.color_pair(7))
            check = "☑" if selected else "☐"
            stdscreen.addstr(yy, 7, f"  {check}  {pkg.label}")
            stdscreen.addstr(yy, 7 + len(pkg.label) + 8, f"{pkg.mb} MB")
            stdscreen.attroff(curses.color_pair(3) | curses.A_REVERSE
                             if selected else curses.color_pair(7))

        hby = max(3 + len(self._PKG_LIST) + 2, ny - 4)
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(8))
        stdscreen.addstr(hby + 1, 7, "    Select all")
        stdscreen.addstr(hby + 2, 7, "    Deselect all")
        stdscreen.addstr(hby + 3, 7, "    Back")
        stdscreen.attroff(curses.color_pair(8))

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate  ·  Space toggle  ·  Enter continue  ·  Esc back",
                         curses.A_DIM)

    def handle_input(self, key, nav):
        if not hasattr(nav, "selected_pkg_indices"):
            nav.selected_pkg_indices = {0, 1, 2, 3, 7, 8, 9}

        if key == curses.KEY_UP:
            nav.scroll_offset = max(0, nav.scroll_offset - 1)
        elif key == curses.KEY_DOWN:
            nav.scroll_offset = min(len(self._PKG_LIST) - 1, nav.scroll_offset + 1)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            idx = nav.scroll_offset
            if idx < len(self._PKG_LIST):
                if idx in nav.selected_pkg_indices:
                    nav.selected_pkg_indices.discard(idx)
                else:
                    nav.selected_pkg_indices.add(idx)
            return None  # stay on page
        elif key == 27:
            return InstallerStep.NETWORK
        elif key == ord("a"):  # select all
            nav.selected_pkg_indices = set(range(len(self._PKG_LIST)))
        elif key == ord("d"):  # deselect all
            nav.selected_pkg_indices = set()
        return None


# ── Bootloader ────────────────────────────────────────────────────────────

class BootloaderScreen(Screen):
    STEP = InstallerStep.BOOTLOADER
    TITLE = "Bootloader Configuration"
    SUBTITLE = "Choose and configure the bootloader"

    _BL_OPTIONS = list(BLTYPE)

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        d = SAMPLE_DISKS[nav.selected_disk]

        # Bootloader options
        for i, bl in enumerate(self._BL_OPTIONS):
            yy = 3 + i
            sel = nav.bl_type == bl
            stdscreen.attron(curses.color_pair(3) | curses.A_REVERSE
                            if sel else curses.color_pair(7))
            icon = "▶" if sel else "  "
            stdscreen.addstr(yy, 7, f"{icon} {i + 1}. {bl.value}")
            stdscreen.attroff(curses.color_pair(3) | curses.A_REVERSE
                             if sel else curses.color_pair(7))
            desc_map = {
                "GRUB": "Universal — works with most OSes",
                "systemd-boot": "Simple — EFI-only, fast boot",
                "rEFInd": "Graphical — auto-detects OSes",
                "Limine": "Modern — EFI, flexible config",
            }
            stdscreen.addstr(yy, 22, desc_map.get(bl.value, "")[:nx - 30])

        # Settings panel (right side)
        sx = nx // 2 + 2
        sw = nx - sx - 7
        _draw_panel(stdscreen, 2, sx, 10, sw, "Settings")
        settings = [
            f"Device:    {d.device}",
            f"EFI partition: {d.device}p1",
            f"Timeout:   {nav.boot_timeout}s",
            f"Default:   Nyrqis OS",
            f"Theme:     Nyrqis Dark",
            "",
            "⏱ Timeout: " + "█" * min(nav.boot_timeout, 10) + "░" * max(0, 10 - nav.boot_timeout),
        ]
        for i, line in enumerate(settings):
            if 3 + i < 11:
                stdscreen.attron(curses.color_pair(7))
                stdscreen.addstr(3 + i, sx + 2, line[:sw - 4])
                stdscreen.attroff(curses.color_pair(7))

        hby = max(3 + len(self._BL_OPTIONS) + 2, ny - 4)
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(8))
        stdscreen.addstr(hby + 1, 7, "    Back")
        stdscreen.attroff(curses.color_pair(8))

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate  ·  Enter select  ·  Esc back",
                         curses.A_DIM)

    def handle_input(self, key, nav):
        if key == curses.KEY_UP:
            idx = self._BL_OPTIONS.index(nav.bl_type)
            nav.bl_type = self._BL_OPTIONS[(idx - 1) % len(self._BL_OPTIONS)]
        elif key == curses.KEY_DOWN:
            idx = self._BL_OPTIONS.index(nav.bl_type)
            nav.bl_type = self._BL_OPTIONS[(idx + 1) % len(self._BL_OPTIONS)]
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.THEME
        return None


# ── Theme ─────────────────────────────────────────────────────────────────

class ThemeScreen(Screen):
    STEP = InstallerStep.THEME
    TITLE = "Appearance"
    SUBTITLE = "Choose your desktop theme"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        if not hasattr(nav, "theme_index"):
            nav.theme_index = 0

        # Theme list (left)
        for i, theme in enumerate(THEMES):
            yy = 3 + i
            sel = i == nav.theme_index
            stdscreen.attron(curses.color_pair(3) | curses.A_REVERSE
                            if sel else curses.color_pair(7))
            icon = "▶" if sel else "  "
            stdscreen.addstr(yy, 7, f"{icon} {i + 1}. {theme['name']}")
            stdscreen.attroff(curses.color_pair(3) | curses.A_REVERSE
                             if sel else curses.color_pair(7))
            stdscreen.addstr(yy, 30, theme["desc"][:nx // 2 - 26])

        # Preview panel (right) — colour swatches drawn with blocks
        sx = nx // 2 + 2
        sw = nx - sx - 7
        _draw_panel(stdscreen, 2, sx, 12, sw, "Preview")
        t = THEMES[nav.theme_index]
        stdscreen.attron(curses.color_pair(7))
        stdscreen.addstr(4, sx + 2, f"Theme:  {t['name']}")
        stdscreen.addstr(5, sx + 2, f"Mode:   {t['mode']}")
        stdscreen.attroff(curses.color_pair(7))
        # Swatch rows: wallpaper / accent / window
        swatch_w = max(8, sw - 14)
        stdscreen.attron(curses.color_pair(2))
        stdscreen.addstr(7, sx + 2, " " * swatch_w)
        stdscreen.attroff(curses.color_pair(2))
        stdscreen.addstr(7, sx + 2, "wallpaper")
        stdscreen.attron(curses.color_pair(6))
        stdscreen.addstr(9, sx + 2, " " * swatch_w)
        stdscreen.attroff(curses.color_pair(6))
        stdscreen.addstr(9, sx + 2, "accent")
        stdscreen.attron(curses.color_pair(1))
        stdscreen.addstr(11, sx + 2, " " * swatch_w)
        stdscreen.attroff(curses.color_pair(1))
        stdscreen.addstr(11, sx + 2, "window")

        hby = max(3 + len(THEMES) + 2, ny - 4)
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Continue")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(8))
        stdscreen.addstr(hby + 1, 7, "    Back")
        stdscreen.attroff(curses.color_pair(8))

        stdscreen.addstr(ny - 2, 5,
                         "  ↑/↓ navigate  ·  Enter select  ·  p preview  ·  Esc back",
                         curses.A_DIM)

    def handle_input(self, key, nav):
        if not hasattr(nav, "theme_index"):
            nav.theme_index = 0
        if key == curses.KEY_UP:
            nav.theme_index = (nav.theme_index - 1) % len(THEMES)
        elif key == curses.KEY_DOWN:
            nav.theme_index = (nav.theme_index + 1) % len(THEMES)
        elif key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.SUMMARY
        elif key == ord("p"):
            nav.preview_enabled = True
            return None
        elif key == 27:
            return InstallerStep.BOOTLOADER
        return None


# ── Summary ───────────────────────────────────────────────────────────────

class SummaryScreen(Screen):
    STEP = InstallerStep.SUMMARY
    TITLE = "Installation Summary"
    SUBTITLE = "Review your choices before installing"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        d = SAMPLE_DISKS[nav.selected_disk]
        layout = SAMPLE_LAYOUTS[nav.selected_layout_idx]
        total_mb = sum(p.mb for p in PKG if p.value[0] in
                      {k for k, _ in (nav.selected_pkg_indices or set())})
        user = nav.user if hasattr(nav, "user") else User()

        summary_items = [
            ("Language", _LANGUAGES[nav.lang_index][0] if hasattr(nav, "lang_index") else "English (US)"),
            ("Keyboard", _KEYBOARDS[nav.kb_index][0] if hasattr(nav, "kb_index") else "English (US)"),
            ("Timezone", _TIMEZONES[nav.tz_index][0] if hasattr(nav, "tz_index") else "UTC"),
            ("Disk", f"{d.device} ({d.size_gb} GB {d.type_label})"),
            ("Partitioning", layout.name),
            ("Username", user.username or "user"),
            ("Hostname", user.hostname or "nyrqis"),
            ("Auto-login", "Yes" if user.auto_login else "No"),
            ("Bootloader", nav.bl_type.value),
            ("Boot timeout", f"{nav.boot_timeout}s"),
            ("Theme", THEMES[getattr(nav, "theme_index", 0)]["name"]),
            ("Packages", f"{len(nav.selected_pkg_indices or set())} groups ({total_mb} MB)"),
        ]

        col1_cols = nx // 2 - 8
        col2_x = nx // 2 + 2

        for i, (key, val) in enumerate(summary_items):
            yy = 3 + i
            if yy >= ny - 3:
                break
            stdscreen.attron(curses.color_pair(6) | curses.A_BOLD)
            stdscreen.addstr(yy, 7, key)
            stdscreen.attroff(curses.color_pair(6) | curses.A_BOLD)
            stdscreen.attron(curses.color_pair(7))
            val_display = val[:col1_cols - 6]
            stdscreen.addstr(yy, 7 + len(key) + 2, val_display)
            stdscreen.attroff(curses.color_pair(7))

            # second column
            if i >= len(summary_items) // 2:
                yy2 = 3 + (i - len(summary_items) // 2)
                k2, v2 = summary_items[i]
                stdscreen.attron(curses.color_pair(6) | curses.A_BOLD)
                stdscreen.addstr(yy2, col2_x, k2)
                stdscreen.attroff(curses.color_pair(6) | curses.A_BOLD)
                stdscreen.attron(curses.color_pair(7))
                stdscreen.addstr(yy2, col2_x + len(k2) + 2,
                                 v2[:nx - col2_x - 10])
                stdscreen.attroff(curses.color_pair(7))

        hby = max(3 + len(summary_items) // 2 + 2, ny - 4)
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 7, "  > Begin installation")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(8))
        stdscreen.addstr(hby + 1, 7, "    Back")
        stdscreen.addstr(hby + 2, 7, "    Cancel installation")
        stdscreen.attroff(curses.color_pair(8))

        stdscreen.addstr(ny - 2, 5, "  ↑/↓ navigate  ·  Enter select  ·  Esc back",
                         curses.A_DIM)

    def handle_input(self, key, nav):
        if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            return InstallerStep.INSTALLING
        elif key == 27:
            return InstallerStep.THEME
        return None


# ── Installing ────────────────────────────────────────────────────────────

class InstallingScreen(Screen):
    STEP = InstallerStep.INSTALLING
    TITLE = "Installing Nyrqis OS"
    SUBTITLE = "Please do not turn off your computer"

    _INSTALL_STEPS: List[Tuple[str, str, Optional[FS], float]] = [
        ("Preparing filesystem", "Preparing partition filesystem...", FS.FAT32, 5),
        ("Formatting /boot/efi", "Creating EFI system partition...", FS.FAT32, 10),
        ("Formatting / (root)", "Formatting root partition (btrfs)...", FS.BTRFS, 20),
        ("Formatting /home", "Formatting home partition (btrfs)...", FS.BTRFS, 35),
        ("Creating swap", "Setting up swap area...", FS.SWAP, 42),
        ("Mounting filesystems", "Mounting all filesystems...", None, 48),
        ("Installing base system", "Extracting core packages...", None, 55),
        ("Installing kernel", "Installing Nykernel 0.14.39...", None, 62),
        ("Installing NyHAL", "Installing NyHAL backend...", None, 68),
        ("Copying desktop", "Installing Nyrqis Desktop...", None, 75),
        ("Configuring system", "Writing system configuration...", None, 82),
        ("Setting up user", "Creating user account...", None, 87),
        ("Installing bootloader", "Installing bootloader...", None, 92),
        ("Generating initramfs", "Building initramfs image...", None, 96),
        ("Cleanup", "Cleaning up temporary files...", None, 99),
        ("Complete!", "Installation finished successfully!", None, 100),
    ]

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()
        _draw_box(stdscreen, 1, 5, ny - 2, nx - 10, self.TITLE)

        # Progress bar at top
        pct = min(100, nav.progress)
        bar_x = 7
        bar_w = nx - 24
        filled = int(pct / 100 * bar_w)
        # background
        stdscreen.attron(curses.color_pair(2))
        stdscreen.addstr(2, bar_x, " " * bar_w)
        stdscreen.attroff(curses.color_pair(2))
        # fill
        if filled > 0:
            stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
            stdscreen.addstr(2, bar_x, "█" * filled)
            stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        # remaining
        if filled < bar_w:
            stdscreen.attron(curses.color_pair(8))
            stdscreen.addstr(2, bar_x + filled, "░" * (bar_w - filled))
            stdscreen.attroff(curses.color_pair(8))
        # percentage
        stdscreen.attron(curses.color_pair(7))
        stdscreen.addstr(2, bar_x + bar_w + 2, f"{pct:.0f}%")
        stdscreen.attroff(curses.color_pair(7))

        # Current operation
        current_step = self._current_operation(nav)
        op_y = 5
        stdscreen.attron(curses.color_pair(6) | curses.A_BOLD)
        stdscreen.addstr(op_y, 7, "Current operation:")
        stdscreen.attroff(curses.color_pair(6) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(3))
        stdscreen.addstr(op_y, 22, current_step[0])
        stdscreen.attroff(curses.color_pair(3))
        stdscreen.attron(curses.color_pair(7))
        stdscreen.addstr(op_y + 1, 22, current_step[1])
        stdscreen.attroff(curses.color_pair(7))

        # Log entries (scrolling)
        log_y = 9
        log_h = ny - log_y - 3
        log_w = nx - 18

        _draw_box(stdscreen, log_y - 1, 7, log_h + 1, log_w, "Installation Log")

        visible_logs = nav.log_entries[-log_h:] if nav.log_entries else []
        for i, log in enumerate(visible_logs):
            yy = log_y + i
            if yy >= ny - 2:
                break
            icon = log.icon
            stdscreen.attron(curses.color_pair(7))
            stdscreen.addstr(yy, 9, f"{log.time_str}  {icon}  {log.msg[:log_w - 14]}")
            stdscreen.attroff(curses.color_pair(7))

        # Bottom bar with stats
        by = ny - 2
        stdscreen.attron(curses.color_pair(2))
        stdscreen.addstr(by, 7, " " * (nx - 14))
        stdscreen.attroff(curses.color_pair(2))
        stdscreen.attron(curses.color_pair(7))
        stats = (
            f"Disk: {SAMPLE_DISKS[nav.selected_disk].device}  |  "
            f"Progress: {pct:.0f}%  |  "
            f"Elapsed: {self._elapsed(nav):.1f}s"
        )
        stdscreen.addstr(by, 7, stats[:nx - 14])
        stdscreen.attroff(curses.color_pair(7))

        stdscreen.addstr(ny - 1, 5, "  Installing... (automatic)  ·  Esc to cancel",
                         curses.A_DIM)

    def _current_operation(self, nav: NavState) -> Tuple[str, str]:
        for label, desc, fs, threshold in self._INSTALL_STEPS:
            if nav.progress < threshold:
                return (label, desc)
        return self._INSTALL_STEPS[-1]

    def _elapsed(self, nav):
        return nav.progress / 100 * 45  # ~45s total simulated time

    def handle_input(self, key, nav):
        if key == 27:  # Esc to cancel
            nav.install_done = True
            return InstallerStep.COMPLETE
        # During install we auto-advance — just return None to keep rendering
        return None


# ── Complete ──────────────────────────────────────────────────────────────

class CompleteScreen(Screen):
    STEP = InstallerStep.COMPLETE
    TITLE = "Installation Complete!"
    SUBTITLE = "Nyrqis OS is ready to use"

    def render(self, stdscreen, ny, nx, nav):
        stdscreen.clear()

        # Centered big success screen
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        _center_text(stdscreen, 3, "✓", 4, nx)
        _center_text(stdscreen, 4, "INSTALLATION COMPLETE", 4, nx)
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)

        stdscreen.attron(curses.color_pair(6))
        _center_text(stdscreen, 6, "Nyrqis OS 0.14.39", 6, nx)
        _center_text(stdscreen, 7, "Nykernel 0.14.39  ·  NyHAL Linux Backend", 7, nx)
        stdscreen.attroff(curses.color_pair(6))

        # Summary
        lines = [
            "Your computer is now running Nyrqis OS.",
            "",
            "What was installed:",
        ]
        d = SAMPLE_DISKS[nav.selected_disk]
        total_mb = sum(p.mb for p in PKG if p.value[0] in
                      {k for k, _ in (nav.selected_pkg_indices or set())})
        user = nav.user if hasattr(nav, "user") else User()
        lines += [
            f"  • System on {d.device} ({d.size_gb} GB)",
            f"  • {nav.bl_type.value} bootloader",
            f"  • User: {user.username or 'user'}@{user.hostname or 'nyrqis'}",
            f"  • {len(nav.selected_pkg_indices or set())} package groups ({total_mb} MB)",
            "",
            "Next steps:",
            "  1. Remove the installation media",
            "  2. Press Restart Now to reboot",
            "  3. Log in with your username and password",
        ]

        col_w = nx - 20
        lines_wrapped = []
        for para in lines:
            if para.strip():
                lines_wrapped.extend(textwrap.wrap(para, width=col_w))
            else:
                lines_wrapped.append("")

        start_y = 10
        for i, line in enumerate(lines_wrapped):
            yy = start_y + i
            if yy >= ny - 3:
                break
            if line.startswith("Next steps:") or line.startswith("What was"):
                stdscreen.attron(curses.color_pair(6) | curses.A_BOLD)
                stdscreen.addstr(yy, 10, line)
                stdscreen.attroff(curses.color_pair(6) | curses.A_BOLD)
            elif line.startswith("  •") or line.startswith("  1") or line.startswith("  2") or line.startswith("  3"):
                stdscreen.attron(curses.color_pair(7))
                stdscreen.addstr(yy, 10, line)
                stdscreen.attroff(curses.color_pair(7))
            else:
                stdscreen.attron(curses.color_pair(7))
                stdscreen.addstr(yy, 10, line)
                stdscreen.attroff(curses.color_pair(7))

        hby = max(start_y + len(lines_wrapped) + 2, ny - 4)
        stdscreen.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.addstr(hby, 10, "  > Restart Now")
        stdscreen.attroff(curses.color_pair(4) | curses.A_BOLD)
        stdscreen.attron(curses.color_pair(7))
        stdscreen.addstr(hby + 1, 10, "    Boot from disk")
        stdscreen.addstr(hby + 2, 10, "    Back to desktop (demo only)")
        stdscreen.attroff(curses.color_pair(7))

    def handle_input(self, key, nav):
        if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            # In demo mode we just exit — real installer would reboot
            return None  # stay on screen
        return None


# ---------------------------------------------------------------------------
# Screen registry and main loop
# ---------------------------------------------------------------------------

_SCREENS: dict = {}

def _register_screen(screen_cls):
    _SCREENS[screen_cls.STEP] = screen_cls()
    return screen_cls


for _cls in [BootSplashScreen, IsoSelectScreen, WelcomeScreen, LanguageScreen, KeyboardScreen,
             TimezoneScreen, DiskSelectScreen, PartitioningScreen, UserSetupScreen,
             NetworkScreen, PackageScreen, BootloaderScreen, ThemeScreen, SummaryScreen,
             InstallingScreen, CompleteScreen]:
    _register_screen(_cls)


class LiveInstaller:
    """Main interactive installer loop."""

    def __init__(self, auto: bool = False, auto_speed: float = 2.0,
                 skip_to_install: bool = False):
        self.auto = auto
        self.auto_speed = auto_speed
        self.skip_to_install = skip_to_install
        self.nav = NavState()
        self.running = True
        self.start_time = time.time()
        self.last_progress_update = 0.0
        self.screen_iter = 0
        self.install_thread_active = False

        # Set default selections for auto/skip modes
        self.nav.lang_index = 0
        self.nav.kb_index = 0
        self.nav.tz_index = 0
        self.nav.selected_disk = 0
        self.nav.selected_layout_idx = 0
        self.nav.selected_pkg_indices = {0, 1, 2, 3, 7, 8, 9}
        self.nav.bl_type = BLTYPE.SYSTEMD_BOOT
        self.nav.boot_timeout = 5
        self.nav.theme_index = 0
        self.nav.user = User(username="demo", password="demo1234",
                             hostname="nyrqis-demo")
        self.nav.scroll_offset = 0

        # Pre-populate some logs
        for msg in ["System ready", "Starting installer...",
                     "Detected 4 storage devices",
                     "Network: connected (192.168.1.105)"]:
            self.nav.log_entries.append(InstallLog(time.time(), "Setup", msg))

    def run(self, stdscreen: "curses._CursesWindow") -> None:
        _init_curses(stdscreen)
        ny, nx = stdscreen.getmaxyx()

        # For very small screens, set minimal size
        if ny < 20 or nx < 60:
            stdscreen.clear()
            stdscreen.addstr(5, 5, "Terminal too small — need at least 60×20")
            stdscreen.addstr(6, 5, "Current: %dx%d" % (ny, nx))
            stdscreen.refresh()
            time.sleep(3)
            return

        if self.skip_to_install:
            # Fast-forward through setup steps
            self.nav.current_step = InstallerStep.INSTALLING
            self.nav.progress = 0.0
            self.nav.log_entries.clear()
            for msg in ["Installation started",
                         "Preparing filesystem...",
                         "Formatting partitions...",
                         "Extracting packages..."]:
                self.nav.log_entries.append(
                    InstallLog(time.time(), "Install", msg))

        while self.running:
            ny, nx = stdscreen.getmaxyx()
            if ny < 20 or nx < 60:
                stdscreen.clear()
                stdscreen.addstr(5, 5, "Terminal resized too small")
                stdscreen.refresh()
                time.sleep(0.5)
                continue

            stdscreen.clear()

            # Get current screen
            step = self.nav.current_step
            screen = _SCREENS.get(step)
            if screen is None:
                stdscreen.addstr(5, 5, f"Unknown step: {step}")
                stdscreen.refresh()
                time.sleep(0.5)
                continue

            # Render
            screen.render(stdscreen, ny, nx, self.nav)

            # Auto-advance for boot splash and install
            if self.auto and step == InstallerStep.BOOT_SPLASH:
                if time.time() - self.start_time > 3:
                    self.nav.current_step = InstallerStep.WELCOME
                    self.start_time = time.time()

            if self.auto and step == InstallerStep.INSTALLING:
                self._auto_advance_install(stdscreen, ny, nx)

            # Handle input
            if not self.auto or step not in (InstallerStep.BOOT_SPLASH,
                                              InstallerStep.INSTALLING):
                key = stdscreen.getch()
                next_step = screen.handle_input(key, self.nav)
                if next_step is not None:
                    self.nav.current_step = next_step
                    if next_step == InstallerStep.INSTALLING:
                        self.nav.progress = 0.0
                        self.start_time = time.time()
                        self.nav.log_entries.append(
                            InstallLog(time.time(), "Install", "Installation started"))
                        self.nav.log_entries.append(
                            InstallLog(time.time(), "Install", "Preparing to write to disk..."))
                    elif next_step == InstallerStep.COMPLETE:
                        self.nav.log_entries.append(
                            InstallLog(time.time(), "Install", "✓ Installation complete!"))
                        self.running = False
            else:
                stdscreen.nodelay(True)
                stdscreen.getch()  # consume any pending input
                stdscreen.nodelay(False)

            stdscreen.refresh()
            time.sleep(0.05)

    def _auto_advance_install(self, stdscreen, ny, nx) -> None:
        """Simulate install progress over time."""
        now = time.time()
        elapsed = now - self.start_time
        progress = min(100, elapsed / 45 * 100)  # 45 seconds total
        self.nav.progress = progress

        # Add log entries as we go
        if progress > 5 and not any("Preparing filesystem" in l.msg
                                     for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Formatting", "Preparing partition filesystem..."))
        if progress > 10 and not any("EFI" in l.msg
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Formatting", "Creating EFI system partition (vfat)..."))
        if progress > 20 and not any("root" in l.msg.lower()
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Formatting", "Formatting root partition (btrfs)..."))
        if progress > 35 and not any("home" in l.msg.lower()
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Formatting", "Formatting home partition (btrfs)..."))
        if progress > 42 and not any("swap" in l.msg.lower()
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Setup", "Creating swap area (16 GB)..."))
        if progress > 48 and not any("mounting" in l.msg.lower()
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Mounting", "Mounting all filesystems..."))
        if progress > 55 and not any("base system" in l.msg.lower()
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Extracting", "Installing base system packages..."))
        if progress > 62 and not any("kernel" in l.msg.lower()
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Extracting", "Installing Nykernel 0.14.39..."))
        if progress > 68 and not any("NyHAL" in l.msg
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Extracting", "Installing NyHAL Linux Backend..."))
        if progress > 75 and not any("Desktop" in l.msg
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Extracting", "Installing Nyrqis Desktop Environment..."))
        if progress > 82 and not any("config" in l.msg.lower()
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Configuring", "Writing system configuration..."))
        if progress > 87 and not any("user" in l.msg.lower()
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Configuring", f"Creating user '{self.nav.user.username}'..."))
        if progress > 92 and not any("bootloader" in l.msg.lower()
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "Bootloader",
                           f"Installing {self.nav.bl_type.value}..."))
        if progress > 96 and not any("initramfs" in l.msg.lower()
                                      for l in self.nav.log_entries[-3:]):
            self.nav.log_entries.append(
                InstallLog(now, "System", "Generating initramfs..."))
        if progress > 99 and not self.nav.install_done:
            self.nav.log_entries.append(
                InstallLog(now, "Complete", "✓ Installation complete!"))
            self.nav.install_done = True
            self.nav.current_step = InstallerStep.COMPLETE
            self.running = False


def _run_curses(main_func: Callable) -> None:
    """Run a curses application with proper cleanup."""
    try:
        curses.wrapper(main_func)
    except curses.error:
        pass  # non-TTY, handled in main()
    except KeyboardInterrupt:
        sys.exit(0)


def _print_banner() -> None:
    banner = """
╔══════════════════════════════════════════════════════════════════════════╗
║                                                                          ║
║    ██████████████████████████████████████████████████████████████████    ║
║    ██                                                              ██    ║
║    ██   ┌────────────────────────────────────────────────────────┐ ██    ║
║    ██   │                                                        │ ██    ║
║    ██   │           N Y R Q I S   O S                           │ ██    ║
║    ██   │                                                        │ ██    ║
║    ██   │         Live OS Installer Demo                        │ ██    ║
║    ██   │                                                        │ ██    ║
║    ██   └────────────────────────────────────────────────────────┘ ██    ║
║    ██                                                              ██    ║
║    ██████████████████████████████████████████████████████████████████    ║
║                                                                          ║
║    Build a platform worthy of the decades ahead.                         ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
    for line in banner.split("\n"):
        print(line)


def _run_text_mode(installer: LiveInstaller) -> None:
    """Fallback text-mode installer for non-TTY environments."""
    print("\n  [text mode] Nyrqis OS Live Installer Demo\n")
    print("  ╔══════════════════════════════════════════╗")
    print("  ║       Nyrqis OS — Live Installer        ║")
    print("  ╚══════════════════════════════════════════╝")
    print()
    print("  [BOOT SPLASH]   Loading initramfs.... ✓")
    time.sleep(0.4)
    print("  [WELCOME]       Starting installer... ✓")
    time.sleep(0.3)
    print("  [LANGUAGE]      English (United States) ✓")
    time.sleep(0.3)
    print("  [KEYBOARD]      English (US) ✓")
    time.sleep(0.3)
    print("  [TIMEZONE]      Coordinated Universal Time ✓")
    time.sleep(0.3)
    print()
    print("  ── Disk Selection ──")
    for i, d in enumerate(SAMPLE_DISKS):
        sel = "→" if i == installer.nav.selected_disk else " "
        print(f"    {sel} {i+1}. {d.device} — {d.model} ({d.size_gb} GB {d.type_label})")
    print(f"\n  → Selected: {SAMPLE_DISKS[installer.nav.selected_disk].device}")
    time.sleep(0.3)
    print()
    print("  ── Partitioning ──")
    layout = SAMPLE_LAYOUTS[installer.nav.selected_layout_idx]
    print(f"    → {layout.name}")
    if layout.partitions:
        for p in layout.partitions:
            print(f"       {p.fs_icon} {p.mount:<15} {p.fs.value:<8} {p.size_gb:>8.1f} GB {'[new]' if p.is_new else ''}")
    time.sleep(0.3)
    print()
    print("  ── User Setup ──")
    u = installer.nav.user
    print(f"    Username:    {u.username or 'user'}")
    print(f"    Hostname:    {u.hostname}")
    print(f"    Auto-login:  {'Yes' if u.auto_login else 'No'}")
    print(f"    Password:    {'*' * len(u.password) if u.password else '[not set]'}")
    time.sleep(0.3)
    print()
    print("  ── Network ──")
    print("    ✓ Ethernet (enp3s0) connected")
    print("    IP: 192.168.1.105  |  200 Mbps")
    time.sleep(0.3)
    print()
    sel_pkgs = installer.nav.selected_pkg_indices or set()
    total_mb = sum(list(PKG)[i].mb for i in sel_pkgs if i < len(PKG))
    print(f"  ── Package Selection ({len(sel_pkgs)} groups, ~{total_mb} MB) ──")
    for idx in sorted(sel_pkgs):
        if idx < len(PKG):
            p = list(PKG)[idx]
            print(f"    ☑ {p.label:<25} {p.mb:>5} MB")
    time.sleep(0.3)
    print()
    print("  ── Bootloader ──")
    print(f"    → {installer.nav.bl_type.value} (timeout: {installer.nav.boot_timeout}s)")
    time.sleep(0.3)
    print()
    theme = THEMES[getattr(installer.nav, "theme_index", 0)]
    print("  ── Appearance ──")
    print(f"    → {theme['name']} ({theme['mode']})")
    time.sleep(0.3)
    print()
    print("  ═══════════════════════════════════════════")
    print("  ║       INSTALLATION PROGRESS              ║")
    print("  ╚══════════════════════════════════════════")
    print()
    print("  Installing Nyrqis OS 0.14.39")
    print("  Please do not turn off your computer")
    print()
    steps = [
        ("Preparing filesystem", 5),
        ("Creating EFI partition (vfat)", 10),
        ("Formatting root (btrfs)", 20),
        ("Formatting home (btrfs)", 35),
        ("Creating swap area", 42),
        ("Mounting filesystems", 48),
        ("Installing base system", 55),
        ("Installing Nykernel 0.14.39", 62),
        ("Installing NyHAL backend", 68),
        ("Installing Desktop Environment", 75),
        ("Writing system configuration", 82),
        ("Creating user account", 87),
        ("Installing systemd-boot", 92),
        ("Generating initramfs", 96),
        ("Cleaning up", 99),
    ]
    for msg, thresh in steps:
        while installer.nav.progress < thresh:
            installer.nav.progress += 0.5
        print(f"  ✓ {msg}")
        time.sleep(0.1)
    installer.nav.progress = 100
    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║         ✓ INSTALLATION COMPLETE          ║")
    print("  ╚══════════════════════════════════════════╝")
    print()
    print("  🍄 Nyrqis OS Live Installer Demo — complete!")
    d = SAMPLE_DISKS[installer.nav.selected_disk]
    print(f"   Disk:        {d.device} ({d.size_gb} GB)")
    print(f"   Layout:      {SAMPLE_LAYOUTS[installer.nav.selected_layout_idx].name}")
    print(f"   User:        {installer.nav.user.username}@{installer.nav.user.hostname}")
    print(f"   Bootloader:  {installer.nav.bl_type.value}")
    theme = THEMES[getattr(installer.nav, "theme_index", 0)]
    print(f"   Theme:       {theme['name']}")
    print(f"   Packages:    {len(sel_pkgs)} groups ({total_mb} MB)")
    print("   (This was a demo — no actual installation was performed)\n")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Nyrqis OS — Live OS Installer Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 nyrqis_live_install.py          # interactive installer
  python3 nyrqis_live_install.py --auto   # autopilot demo (2s steps)
  python3 nyrqis_live_install.py --auto 5 # autopilot with 5s steps
  python3 nyrqis_live_install.py --skip   # skip straight to install progress
  python3 nyrqis_live_install.py --gui    # render GUI screens as PNG/GIF
        """)
    parser.add_argument("--auto", nargs="?", const=2.0, type=float,
                        help="Run in autopilot mode (optionally specify speed in seconds)")
    parser.add_argument("--skip", action="store_true",
                        help="Skip setup screens and go straight to install progress")
    parser.add_argument("--gui", nargs="?", const="/tmp/nyrqis_gui", metavar="DIR",
                        help="Render GUI screens as PNGs/GIFs (optionally specify output dir)")
    args = parser.parse_args()

    _print_banner()

    if args.gui:
        try:
            from ui.installer_gui import run_gui
            run_gui(args.gui)
            return
        except ImportError as e:
            print(f"  ✗ GUI rendering unavailable: {e}")
            print("    (Pillow required: pip install Pillow)")
            return

    installer = LiveInstaller(
        auto=args.auto is not None,
        auto_speed=args.auto if args.auto else 2.0,
        skip_to_install=args.skip,
    )

    # Check if we have a real TTY for curses
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            _run_curses(installer.run)
        except curses.error:
            _run_text_mode(installer)
    else:
        # Non-TTY environment — use text mode
        _run_text_mode(installer)

    if installer.nav.install_done:
        print("\n🍄 Nyrqis OS Live Installer Demo — complete!")
        d = SAMPLE_DISKS[installer.nav.selected_disk]
        print("   Installation finished successfully.")
        print(f"   Disk: {d.device}")
        print(f"   Layout: {SAMPLE_LAYOUTS[installer.nav.selected_layout_idx].name}")
        print(f"   User: {installer.nav.user.username}@{installer.nav.user.hostname}")
        print(f"   Bootloader: {installer.nav.bl_type.value}")
        pkgs = len(installer.nav.selected_pkg_indices or set())
        print(f"   Packages: {pkgs} groups")
        print("   (This was a demo — no actual installation was performed)")


if __name__ == "__main__":
    main()
