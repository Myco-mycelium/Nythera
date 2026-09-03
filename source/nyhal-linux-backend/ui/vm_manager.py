"""Virtual Machine Manager — Create/start/stop/console and resource allocation.

Features:
- VM lifecycle management (create, start, stop, pause, delete)
- Resource allocation (CPU, RAM, disk, network)
- Console access simulation
- Snapshot and clone support
- Network bridge/NAT configuration
- Performance monitoring per VM
- Template library
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class VMState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    SAVED = "saved"
    ERROR = "error"
    CREATING = "creating"

    @property
    def icon(self) -> str:
        icons = {
            VMState.STOPPED: "⏹", VMState.RUNNING: "🟢",
            VMState.PAUSED: "⏸", VMState.SAVED: "💾",
            VMState.ERROR: "❌", VMState.CREATING: "🔄",
        }
        return icons.get(self, "?")


class VMOS(Enum):
    NYRQIS = "nyrqis"
    UBUNTU = "ubuntu"
    FEDORA = "fedora"
    ARCH = "arch"
    DEBIAN = "debian"
    WINDOWS = "windows"
    MACOS = "macos"

    @property
    def icon(self) -> str:
        icons = {
            VMOS.NYRQIS: "🍄", VMOS.UBUNTU: "🟠", VMOS.FEDORA: "🎩",
            VMOS.ARCH: "🔷", VMOS.DEBIAN: "🔴", VMOS.WINDOWS: "🪟", VMOS.MACOS: "🍎",
        }
        return icons.get(self, "?")


class NetworkMode(Enum):
    NAT = "nat"
    BRIDGED = "bridged"
    ISOLATED = "isolated"
    HOST = "host"

    @property
    def icon(self) -> str:
        icons = {
            NetworkMode.NAT: "🌐", NetworkMode.BRIDGED: "🔗",
            NetworkMode.ISOLATED: "🔒", NetworkMode.HOST: "🏠",
        }
        return icons.get(self, "?")


@dataclass
class VMStorage:
    disk_gb: float = 50.0
    used_gb: float = 0.0
    disk_type: str = "qcow2"  # qcow2, raw, vmdk

    @property
    def usage_pct(self) -> float:
        if self.disk_gb == 0:
            return 0.0
        return self.used_gb / self.disk_gb * 100

    @property
    def usage_bar(self) -> str:
        filled = min(20, int(self.usage_pct / 5))
        return "█" * filled + "░" * (20 - filled)


@dataclass
class VMNetwork:
    mode: NetworkMode = NetworkMode.NAT
    mac_address: str = ""
    ip_address: str = ""
    gateway: str = ""
    dns: str = ""
    bandwidth_mbps: int = 1000
    port_forwards: List[str] = field(default_factory=list)

    @property
    def port_str(self) -> str:
        return ", ".join(self.port_forwards[:3]) if self.port_forwards else "none"


@dataclass
class VMSnapshot:
    name: str = ""
    timestamp: float = 0.0
    size_gb: float = 0.0
    description: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp))


@dataclass
class VirtualMachine:
    id: int = 0
    name: str = ""
    os: VMOS = VMOS.NYRQIS
    state: VMState = VMState.STOPPED
    vcpus: int = 2
    ram_gb: float = 4.0
    storage: VMStorage = field(default_factory=VMStorage)
    network: VMNetwork = field(default_factory=VMNetwork)
    cpu_usage: float = 0.0
    ram_usage: float = 0.0
    uptime_s: float = 0.0
    created_at: float = 0.0
    autostart: bool = False
    snapshots: List[VMSnapshot] = field(default_factory=list)
    notes: str = ""
    template: str = ""
    os_version: str = ""

    @property
    def cpu_bar(self) -> str:
        filled = min(20, int(self.cpu_usage / 5))
        return "█" * filled + "░" * (20 - filled)

    @property
    def ram_bar(self) -> str:
        used = self.ram_usage / 100 * self.ram_gb
        pct = min(100, int(self.ram_usage))
        filled = pct // 5
        return "█" * filled + "░" * (20 - filled)

    @property
    def ram_used_str(self) -> str:
        return f"{self.ram_usage / 100 * self.ram_gb:.1f}GB"

    @property
    def uptime_str(self) -> str:
        if self.uptime_s == 0:
            return "down"
        if self.uptime_s < 3600:
            return f"{self.uptime_s / 60:.0f}m"
        if self.uptime_s < 86400:
            return f"{self.uptime_s / 3600:.1f}h"
        return f"{self.uptime_s / 86400:.0f}d"

    @property
    def snapshot_count(self) -> int:
        return len(self.snapshots)


@dataclass
class VMTemplate:
    name: str = ""
    os: VMOS = VMOS.NYRQIS
    description: str = ""
    recommended_vcpus: int = 2
    recommended_ram_gb: float = 4.0
    disk_gb: float = 50
    tags: List[str] = field(default_factory=list)


class VMManager:
    def __init__(self):
        self._vms: List[VirtualMachine] = []
        self._templates: List[VMTemplate] = []
        self._selected_vm: int = 0
        self._view_mode: str = "vms"  # vms, console, templates, network, stats
        self._console_text: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # VMs
        self._vms = [
            VirtualMachine(1, "nyrqis-dev", VMOS.NYRQIS, VMState.RUNNING,
                           4, 8.0, VMStorage(100, 42.5), VMNetwork(NetworkMode.NAT, "52:54:00:12:34:56", "192.168.122.10"),
                           15.2, 35.0, 86400 * 7 + 3600 * 6, now - 86400 * 30, True,
                           [VMSnapshot("Before v2.1", now - 86400 * 14, 8.2),
                            VMSnapshot("Clean Install", now - 86400 * 30, 5.1)],
                           "Main development VM", "Nyrqis Custom", "v2.1.0"),
            VirtualMachine(2, "ubuntu-server", VMOS.UBUNTU, VMState.RUNNING,
                           2, 4.0, VMStorage(50, 18.3), VMNetwork(NetworkMode.BRIDGED, "52:54:00:ab:cd:ef", "192.168.1.50"),
                           8.5, 55.0, 86400 * 3 + 7200, now - 86400 * 60, False,
                           notes="Web server testing", os_version="22.04 LTS"),
            VirtualMachine(3, "fedora-desktop", VMOS.FEDORA, VMState.PAUSED,
                           4, 8.0, VMStorage(80, 35.0), VMNetwork(NetworkMode.NAT, "52:54:00:de:ad:01", "192.168.122.20"),
                           0.0, 42.0, 0, now - 86400 * 14, False,
                           [VMSnapshot("Paused state", now - 3600, 12.0)],
                           "GNOME desktop testing", "Fedora 40"),
            VirtualMachine(4, "arch-lab", VMOS.ARCH, VMState.STOPPED,
                           2, 2.0, VMStorage(30, 12.8), VMNetwork(NetworkMode.ISOLATED),
                           0.0, 0.0, 0, now - 86400 * 45, False,
                           notes="Architecture experiments", os_version="rolling"),
            VirtualMachine(5, "windows-compat", VMOS.WINDOWS, VMState.STOPPED,
                           4, 8.0, VMStorage(120, 65.0), VMNetwork(NetworkMode.NAT, "52:54:00:ca:fe:01", "192.168.122.30"),
                           0.0, 0.0, 0, now - 86400 * 90, False,
                           notes="Windows compatibility testing", os_version="11 Pro"),
            VirtualMachine(6, "debian-minimal", VMOS.DEBIAN, VMState.RUNNING,
                           1, 1.0, VMStorage(20, 4.2), VMNetwork(NetworkMode.NAT, "52:54:00:be:ef:02", "192.168.122.40"),
                           2.1, 60.0, 86400 * 14, now - 86400 * 60, True,
                           notes="Minimal server for testing", os_version="12 Bookworm"),
        ]

        # Templates
        self._templates = [
            VMTemplate("Nyrqis Desktop", VMOS.NYRQIS, "Full desktop with compositor and shell", 4, 8.0, 100, ["desktop", "gui"]),
            VMTemplate("Nyrqis Headless", VMOS.NYRQIS, "Server/headless mode without GUI", 2, 2.0, 30, ["server", "minimal"]),
            VMTemplate("Ubuntu Server", VMOS.UBUNTU, "Ubuntu Server LTS", 2, 4.0, 50, ["server", "lts"]),
            VMTemplate("Ubuntu Desktop", VMOS.UBUNTU, "Ubuntu Desktop with GNOME", 4, 8.0, 80, ["desktop", "gui"]),
            VMTemplate("Fedora Workstation", VMOS.FEDORA, "Fedora with GNOME", 4, 8.0, 80, ["desktop", "gui"]),
            VMTemplate("Debian Minimal", VMOS.DEBIAN, "Minimal Debian install", 1, 1.0, 20, ["server", "minimal"]),
            VMTemplate("Windows 11", VMOS.WINDOWS, "Windows 11 Pro", 4, 8.0, 120, ["desktop", "compatibility"]),
        ]

        # Console text
        self._console_text = [
            "$ nyrqis-vm status",
            "  nyrqis-dev:    running (uptime: 7d 6h)",
            "  ubuntu-server: running (uptime: 3d 2h)",
            "  fedora-desktop: paused",
            "  arch-lab:      stopped",
            "  windows-compat: stopped",
            "  debian-minimal: running (uptime: 14d)",
            "",
            "$ nyrqis-vm stats nyrqis-dev",
            "  CPU: 15.2% (4 vCPUs)  RAM: 35.0% (2.8GB/8GB)",
            "  Disk: 42.5GB/100GB (42.5%)  Net: NAT",
            "  Snapshots: 2  Autostart: enabled",
        ]

    @property
    def running_count(self) -> int:
        return sum(1 for vm in self._vms if vm.state == VMState.RUNNING)

    @property
    def total_vcpus(self) -> int:
        return sum(vm.vcpus for vm in self._vms if vm.state == VMState.RUNNING)

    @property
    def total_ram_gb(self) -> float:
        return sum(vm.ram_gb for vm in self._vms if vm.state == VMState.RUNNING)

    def select_vm(self, idx: int):
        if 0 <= idx < len(self._vms):
            self._selected_vm = idx

    def set_view(self, mode: str):
        if mode in ("vms", "console", "templates", "network", "stats"):
            self._view_mode = mode

    def start_vm(self):
        vm = self._vms[self._selected_vm] if self._selected_vm < len(self._vms) else None
        if vm and vm.state in (VMState.STOPPED, VMState.SAVED):
            vm.state = VMState.RUNNING
            vm.uptime_s = 0

    def stop_vm(self):
        vm = self._vms[self._selected_vm] if self._selected_vm < len(self._vms) else None
        if vm and vm.state == VMState.RUNNING:
            vm.state = VMState.STOPPED
            vm.cpu_usage = 0
            vm.ram_usage = 0
            vm.uptime_s = 0

    def pause_vm(self):
        vm = self._vms[self._selected_vm] if self._selected_vm < len(self._vms) else None
        if vm and vm.state == VMState.RUNNING:
            vm.state = VMState.PAUSED
            vm.cpu_usage = 0

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS VM MANAGER                                       ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  🖥 {len(self._vms)} VMs  🟢 {self.running_count} running  ⚙️ {self.total_vcpus} vCPUs allocated  💾 {self.total_ram_gb:.0f}GB RAM allocated")
        lines.append("")

        if self._view_mode == "vms":
            lines.append("  ── Virtual Machines ──")
            for i, vm in enumerate(self._vms):
                sel = "▶" if i == self._selected_vm else " "
                os_icon = vm.os.icon
                state = vm.state.icon
                autostart = "🔁" if vm.autostart else "  "
                lines.append(f"  {sel}{state} {os_icon} {vm.name:<22s} {vm.os.value:<12s} {vm.vcpus}vCPU {vm.ram_gb:.0f}GB  {vm.uptime_str}  {autostart}")
                if vm.state == VMState.RUNNING:
                    lines.append(f"      CPU:[{vm.cpu_bar}] {vm.cpu_usage:.1f}%  RAM:[{vm.ram_bar}] {vm.ram_used_str}  Disk:[{vm.storage.usage_bar}] {vm.storage.used_gb:.1f}GB")

        elif self._view_mode == "console":
            lines.append("  ── Console ──")
            for line in self._console_text[-18:]:
                lines.append(f"  {line}")

        elif self._view_mode == "templates":
            lines.append("  ── VM Templates ──")
            for t in self._templates:
                lines.append(f"  {t.os.icon} {t.name:<24s} {t.os.value:<10s} {t.recommended_vcpus}vCPU {t.recommended_ram_gb:.0f}GB {t.disk_gb}GB")
                lines.append(f"      {t.description}  Tags: {', '.join(t.tags)}")

        elif self._view_mode == "network":
            lines.append("  ── Network Configuration ──")
            for vm in self._vms:
                net = vm.network
                lines.append(f"  {vm.os.icon} {vm.name:<22s} {net.mode.icon} {net.mode.value:<10s} IP: {net.ip_address or 'N/A'}  MAC: {net.mac_address or 'N/A'}")
                if net.port_forwards:
                    lines.append(f"      Ports: {net.port_str}")

        elif self._view_mode == "stats":
            lines.append("  ── Resource Usage ──")
            for vm in self._vms:
                if vm.state == VMState.RUNNING:
                    lines.append(f"  {vm.os.icon} {vm.name:<22s} CPU:[{vm.cpu_bar}] {vm.cpu_usage:.1f}%  RAM:[{vm.ram_bar}] {vm.ram_used_str}/{vm.ram_gb:.0f}GB  Disk:{vm.storage.usage_pct:.0f}%")

        lines.append("")
        lines.append("  [V]Ms [C]onsole [T]emplates [N]etwork [S]tats [↑↓]Nav [S]tart [P]ause [O]Stop")
        return lines
