"""
Nyrqis VM Manager — virtual machine management application.

Features:
- Create, start, stop, pause, resume, and delete VMs
- Resource allocation (CPU, RAM, disk, network)
- Console access with terminal emulation
- VM snapshots and restore
- Network configuration (NAT, bridged, host-only)
- Storage management (virtual disks, ISO mounting)
- Performance monitoring (CPU, memory, disk I/O)
- VM templates (Linux, Windows, macOS, BSD)
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class VMStatus(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    SAVING = "saving"
    RESTORING = "restoring"
    ERROR = "error"
    CREATING = "creating"


class VMOSType(Enum):
    LINUX = "Linux"
    WINDOWS = "Windows"
    MACOS = "macOS"
    BSD = "BSD"
    OTHER = "Other"


class NetworkMode(Enum):
    NAT = "NAT"
    BRIDGED = "Bridged"
    HOST_ONLY = "Host-Only"
    INTERNAL = "Internal"
    DISABLED = "Disabled"


class DiskFormat(Enum):
    RAW = "Raw"
    VMDK = "VMDK"
    QCOW2 = "QCOW2"
    VDI = "VDI"
    VHDX = "VHDX"


VM_OS_ICONS = {
    VMOSType.LINUX: "🐧",
    VMOSType.WINDOWS: "🪟",
    VMOSType.MACOS: "🍎",
    VMOSType.BSD: "😈",
    VMOSType.OTHER: "💿",
}

VM_STATUS_ICONS = {
    VMStatus.STOPPED: "⏹️",
    VMStatus.RUNNING: "▶️",
    VMStatus.PAUSED: "⏸️",
    VMStatus.SAVING: "💾",
    VMStatus.RESTORING: "🔄",
    VMStatus.ERROR: "❌",
    VMStatus.CREATING: "⏳",
}


@dataclass
class VirtualDisk:
    """A virtual disk for a VM."""
    name: str
    size_gb: int
    format: DiskFormat = DiskFormat.QCOW2
    used_gb: float = 0.0
    path: str = ""
    created: float = field(default_factory=time.time)
    disk_id: str = ""

    def __post_init__(self):
        if not self.disk_id:
            self.disk_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def usage_pct(self) -> float:
        return (self.used_gb / self.size_gb * 100) if self.size_gb > 0 else 0

    @property
    def free_gb(self) -> float:
        return self.size_gb - self.used_gb

    @property
    def display_size(self) -> str:
        return f"{self.size_gb} GB"

    @property
    def display_used(self) -> str:
        return f"{self.used_gb:.1f} GB / {self.size_gb} GB ({self.usage_pct:.0f}%)"


@dataclass
class Snapshot:
    """A VM snapshot."""
    name: str
    description: str = ""
    size_gb: float = 0.0
    created: float = field(default_factory=time.time)
    snapshot_id: str = ""

    def __post_init__(self):
        if not self.snapshot_id:
            self.snapshot_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.created
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.created).strftime("%b %d")


@dataclass
class VirtualMachine:
    """A virtual machine."""
    name: str
    os_type: VMOSType = VMOSType.LINUX
    status: VMStatus = VMStatus.STOPPED
    cpu_cores: int = 2
    memory_mb: int = 2048
    disk: Optional[VirtualDisk] = None
    network_mode: NetworkMode = NetworkMode.NAT
    mac_address: str = ""
    ip_address: str = ""
    boot_order: str = "disk"
    iso_path: str = ""
    snapshots: List[Snapshot] = field(default_factory=list)
    # Runtime stats
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    disk_read_mb: float = 0.0
    disk_write_mb: float = 0.0
    network_rx_mb: float = 0.0
    network_tx_mb: float = 0.0
    uptime_seconds: float = 0.0
    created: float = field(default_factory=time.time)
    started_at: float = 0.0
    vm_id: str = ""

    def __post_init__(self):
        if not self.vm_id:
            self.vm_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]
        if not self.mac_address:
            seed = hashlib.md5(self.vm_id.encode()).hexdigest()
            self.mac_address = ":".join(seed[i:i+2] for i in range(0, 12, 2))

    @property
    def display_name(self) -> str:
        icon = VM_OS_ICONS.get(self.os_type, "💿")
        status = VM_STATUS_ICONS.get(self.status, "❓")
        return f"{icon} {self.name} {status}"

    @property
    def uptime_str(self) -> str:
        if self.uptime_seconds <= 0:
            return "—"
        h = int(self.uptime_seconds // 3600)
        m = int((self.uptime_seconds % 3600) // 60)
        s = int(self.uptime_seconds % 60)
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m {s}s"

    @property
    def memory_str(self) -> str:
        if self.memory_mb >= 1024:
            return f"{self.memory_mb / 1024:.1f} GB"
        return f"{self.memory_mb} MB"

    @property
    def is_running(self) -> bool:
        return self.status in (VMStatus.RUNNING, VMStatus.PAUSED)

    @property
    def can_start(self) -> bool:
        return self.status in (VMStatus.STOPPED, VMStatus.PAUSED)

    @property
    def can_stop(self) -> bool:
        return self.status in (VMStatus.RUNNING, VMStatus.PAUSED)


@dataclass
class VMTemplate:
    """A VM creation template."""
    name: str
    os_type: VMOSType
    description: str
    recommended_cpu: int
    recommended_ram_mb: int
    recommended_disk_gb: int
    icon: str = ""

    @property
    def display(self) -> str:
        icon = self.icon or VM_OS_ICONS.get(self.os_type, "💿")
        return f"{icon} {self.name}"


# ─── VM Manager ──────────────────────────────────────────────────────────


class VMManager:
    """
    Virtual machine manager for Nyrqis OS.
    """

    def __init__(self):
        self._vms: List[VirtualMachine] = []
        self._templates: List[VMTemplate] = []
        self._selected_index: int = 0
        self._view_mode: str = "list"  # list, details, console, create, storage
        self._console_lines: List[str] = []
        self._console_input: str = ""
        self._create_name: str = ""
        self._create_template_idx: int = 0
        self._create_cpu: int = 2
        self._create_ram_mb: int = 2048
        self._create_disk_gb: int = 40
        self._create_step: int = 0  # 0=name, 1=template, 2=cpu, 3=ram, 4=disk, 5=confirm

        # Storage
        self._host_disk_total_gb: int = 500
        self._host_disk_used_gb: float = 120.0

        # Init
        self._init_templates()
        self._init_sample_vms()

    def _init_templates(self) -> None:
        self._templates = [
            VMTemplate("Ubuntu 24.04 LTS", VMOSType.LINUX,
                       "Ubuntu Server with GNOME desktop", 2, 4096, 40, "🐧"),
            VMTemplate("Fedora 40 Workstation", VMOSType.LINUX,
                       "Fedora with latest packages", 2, 4096, 40, "🎩"),
            VMTemplate("Debian 12 (Bookworm)", VMOSType.LINUX,
                       "Stable Linux distribution", 1, 2048, 20, "🦊"),
            VMTemplate("Windows 11 Pro", VMOSType.WINDOWS,
                       "Windows 11 with TPM 2.0", 4, 8192, 80, "🪟"),
            VMTemplate("Windows 10 LTSC", VMOSType.WINDOWS,
                       "Minimal Windows 10", 2, 4096, 40, "🪟"),
            VMTemplate("macOS Sonoma", VMOSType.MACOS,
                       "macOS (requires KVM)", 4, 8192, 80, "🍎"),
            VMTemplate("FreeBSD 14", VMOSType.BSD,
                       "FreeBSD Unix", 2, 4096, 40, "😈"),
            VMTemplate("Alpine Linux", VMOSType.LINUX,
                       "Minimal security-oriented Linux", 1, 1024, 10, "🏔️"),
            VMTemplate("Arch Linux", VMOSType.LINUX,
                       "Rolling release Linux", 2, 2048, 30, "🏗️"),
            VMTemplate("Nyrqis OS (Dev)", VMOSType.LINUX,
                       "Nyrqis development VM", 2, 4096, 40, "🍄"),
        ]

    def _init_sample_vms(self) -> None:
        now = time.time()
        self._vms = [
            VirtualMachine(
                "Nyrqis-Dev", VMOSType.LINUX, VMStatus.RUNNING,
                cpu_cores=4, memory_mb=8192,
                disk=VirtualDisk("nyrqis-dev.vmdk", 100, DiskFormat.VMDK, 45.2),
                network_mode=NetworkMode.BRIDGED, ip_address="192.168.1.100",
                cpu_usage=23.5, memory_usage=68.0,
                disk_read_mb=1200, disk_write_mb=800,
                network_rx_mb=50, network_tx_mb=30,
                uptime_seconds=86400 + 7200,
                started_at=now - 86400 - 7200, created=now - 2592000,
            ),
            VirtualMachine(
                "Web-Server", VMOSType.LINUX, VMStatus.RUNNING,
                cpu_cores=2, memory_mb=4096,
                disk=VirtualDisk("web-server.qcow2", 50, DiskFormat.QCOW2, 12.5),
                network_mode=NetworkMode.NAT, ip_address="10.0.2.15",
                cpu_usage=8.2, memory_usage=42.0,
                disk_read_mb=300, disk_write_mb=150,
                network_rx_mb=200, network_tx_mb=150,
                uptime_seconds=604800,
                started_at=now - 604800, created=now - 5184000,
                snapshots=[
                    Snapshot("Pre-update", "Before package updates", 2.1, now - 86400),
                    Snapshot("Clean install", "Fresh installation", 1.5, now - 5184000),
                ],
            ),
            VirtualMachine(
                "Win11-Apps", VMOSType.WINDOWS, VMStatus.STOPPED,
                cpu_cores=4, memory_mb=8192,
                disk=VirtualDisk("win11-apps.vhdx", 120, DiskFormat.VHDX, 78.3),
                network_mode=NetworkMode.NAT,
                disk_read_mb=0, disk_write_mb=0,
            ),
            VirtualMachine(
                "FreeBSD-Test", VMOSType.BSD, VMStatus.PAUSED,
                cpu_cores=2, memory_mb=2048,
                disk=VirtualDisk("freebsd-test.qcow2", 30, DiskFormat.QCOW2, 8.0),
                network_mode=NetworkMode.HOST_ONLY,
                cpu_usage=0, memory_usage=35.0,
                uptime_seconds=14400, created=now - 1296000,
                snapshots=[
                    Snapshot("Configured", "After base config", 1.0, now - 1296000),
                ],
            ),
            VirtualMachine(
                "Alpine-Sandbox", VMOSType.LINUX, VMStatus.STOPPED,
                cpu_cores=1, memory_mb=1024,
                disk=VirtualDisk("alpine-sandbox.raw", 10, DiskFormat.RAW, 1.2),
                network_mode=NetworkMode.INTERNAL,
                created=now - 259200,
            ),
        ]

    # ── VM Operations ─────────────────────────────────────────────────

    def start_vm(self, index: int = -1) -> bool:
        idx = index if index >= 0 else self._selected_index
        if 0 <= idx < len(self._vms):
            vm = self._vms[idx]
            if vm.can_start:
                vm.status = VMStatus.RUNNING
                vm.started_at = time.time()
                vm.cpu_usage = 0.0
                vm.memory_usage = 0.0
                return True
        return False

    def stop_vm(self, index: int = -1) -> bool:
        idx = index if index >= 0 else self._selected_index
        if 0 <= idx < len(self._vms):
            vm = self._vms[idx]
            if vm.can_stop:
                vm.status = VMStatus.STOPPED
                vm.uptime_seconds = 0
                vm.cpu_usage = 0.0
                vm.memory_usage = 0.0
                return True
        return False

    def pause_vm(self, index: int = -1) -> bool:
        idx = index if index >= 0 else self._selected_index
        if 0 <= idx < len(self._vms):
            vm = self._vms[idx]
            if vm.status == VMStatus.RUNNING:
                vm.status = VMStatus.PAUSED
                return True
        return False

    def resume_vm(self, index: int = -1) -> bool:
        idx = index if index >= 0 else self._selected_index
        if 0 <= idx < len(self._vms):
            vm = self._vms[idx]
            if vm.status == VMStatus.PAUSED:
                vm.status = VMStatus.RUNNING
                return True
        return False

    def delete_vm(self, index: int = -1) -> bool:
        idx = index if index >= 0 else self._selected_index
        if 0 <= idx < len(self._vms):
            vm = self._vms[idx]
            if vm.status == VMStatus.STOPPED:
                self._vms.pop(idx)
                self._selected_index = min(self._selected_index, len(self._vms) - 1)
                return True
        return False

    def create_vm(self, name: str, template: VMTemplate,
                  cpu: int = 0, ram_mb: int = 0, disk_gb: int = 0) -> VirtualMachine:
        vm = VirtualMachine(
            name=name,
            os_type=template.os_type,
            status=VMStatus.STOPPED,
            cpu_cores=cpu or template.recommended_cpu,
            memory_mb=ram_mb or template.recommended_ram_mb,
            disk=VirtualDisk(
                f"{name.lower().replace(' ', '-')}.qcow2",
                disk_gb or template.recommended_disk_gb,
                DiskFormat.QCOW2, 0.0
            ),
        )
        self._vms.append(vm)
        return vm

    # ── Snapshot Operations ───────────────────────────────────────────

    def create_snapshot(self, vm_index: int, name: str, desc: str = "") -> Optional[Snapshot]:
        if 0 <= vm_index < len(self._vms):
            vm = self._vms[vm_index]
            if vm.disk:
                snap = Snapshot(name, desc, vm.disk.used_gb * 0.1)
                vm.snapshots.append(snap)
                return snap
        return None

    def delete_snapshot(self, vm_index: int, snap_index: int) -> bool:
        if 0 <= vm_index < len(self._vms):
            vm = self._vms[vm_index]
            if 0 <= snap_index < len(vm.snapshots):
                vm.snapshots.pop(snap_index)
                return True
        return False

    # ── Navigation ────────────────────────────────────────────────────

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        self._selected_index = min(len(self._vms) - 1, self._selected_index + 1)

    def get_selected_vm(self) -> Optional[VirtualMachine]:
        if 0 <= self._selected_index < len(self._vms):
            return self._vms[self._selected_index]
        return None

    def set_view(self, mode: str) -> None:
        self._view_mode = mode

    # ── Console ───────────────────────────────────────────────────────

    def _init_console(self) -> None:
        vm = self.get_selected_vm()
        if vm:
            self._console_lines = [
                f"Nyrqis VM Console — {vm.name}",
                f"OS: {vm.os_type.value} | CPU: {vm.cpu_cores} cores | RAM: {vm.memory_str}",
                f"Status: {vm.status.value} | IP: {vm.ip_address or 'N/A'}",
                "",
                f"[{vm.name}:~]$ uname -a",
                f"NyrqisVM 6.8.0-nyrqis #1 SMP PREEMPT_DYNAMIC x86_64 GNU/Linux",
                f"[{vm.name}:~]$ free -h",
                f"              total   used   free   shared  buff/cache  available",
                f"Mem:          {vm.memory_str:>8}  {int(vm.memory_mb * vm.memory_usage / 100 / 1024)}M  ...",
                f"[{vm.name}:~]$ df -h /",
                f"Filesystem      Size  Used  Avail  Use%  Mounted on",
                f"/dev/sda1       {vm.disk.display_used if vm.disk else 'N/A'}  /",
                f"[{vm.name}:~]$ uptime",
                f" {datetime.now().strftime('%H:%M:%S')} up {vm.uptime_str},  1 user,  load average: 0.{int(vm.cpu_usage):02d}, 0.{int(vm.cpu_usage*0.8):02d}, 0.{int(vm.cpu_usage*0.6):02d}",
                "",
                f"[{vm.name}:~]$ ",
            ]

    def send_console_input(self, text: str) -> None:
        vm = self.get_selected_vm()
        if not vm:
            return

        cmd = text.strip().lower()
        self._console_lines[-1] = f"[{vm.name}:~]$ {text}"

        responses = {
            "help": [
                "Available commands: help, uname, free, df, uptime, top, ps,",
                "ip addr, ping, cat /etc/os-release, clear, exit",
            ],
            "uname": ["NyrqisVM 6.8.0-nyrqis #1 SMP PREEMPT_DYNAMIC x86_64 GNU/Linux"],
            "free": [
                f"              total   used   free   shared  buff/cache  available",
                f"Mem:          {vm.memory_mb}MB  {int(vm.memory_mb * vm.memory_usage / 100)}MB  ...",
            ],
            "uptime": [
                f" {datetime.now().strftime('%H:%M:%S')} up {vm.uptime_str},  1 user"
            ],
            "ip addr": [
                f"2: eth0: <BROADCAST,MULTICAST,UP> mtu 1500",
                f"    inet {vm.ip_address or '10.0.2.15'}/24 brd 10.0.2.255 scope global eth0",
            ],
            "ps": [
                "  PID TTY          TIME CMD",
                "    1 ?        00:00:02 systemd",
                "  234 ?        00:00:00 sshd",
                "  567 ?        00:00:01 nginx",
                "  890 pts/0    00:00:00 bash",
            ],
            "top": [
                f"%Cpu(s): {vm.cpu_usage:.1f} us, 2.0 sy, 0.0 ni",
                f"MiB Mem : {vm.memory_mb} total, {int(vm.memory_mb * 0.3)} free, {int(vm.memory_mb * vm.memory_usage / 100)} used",
            ],
            "clear": ["__CLEAR__"],
        }

        if cmd in responses:
            for line in responses[cmd]:
                if line == "__CLEAR__":
                    self._console_lines = [f"[{vm.name}:~]$ "]
                    return
                self._console_lines.append(line)
        elif cmd:
            self._console_lines.append(f"bash: {cmd}: command not found")
        self._console_lines.append(f"[{vm.name}:~]$ ")

    # ── Properties ────────────────────────────────────────────────────

    @property
    def vms(self) -> List[VirtualMachine]:
        return list(self._vms)

    @property
    def templates(self) -> List[VMTemplate]:
        return list(self._templates)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def console_lines(self) -> List[str]:
        return list(self._console_lines)

    @property
    def host_disk_info(self) -> Tuple[int, float]:
        return self._host_disk_total_gb, self._host_disk_used_gb

    @property
    def total_vm_disk(self) -> float:
        return sum(vm.disk.size_gb for vm in self._vms if vm.disk)

    @property
    def running_count(self) -> int:
        return sum(1 for vm in self._vms if vm.status == VMStatus.RUNNING)

    @property
    def total_vms(self) -> int:
        return len(self._vms)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_list(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🖥️  Virtual Machines")
        lines.append("─" * width)
        lines.append(f" {self.running_count} running / {self.total_vms} total")
        lines.append("─" * width)

        if not self._vms:
            lines.append("  No VMs. Press N to create one.")
        else:
            for i, vm in enumerate(self._vms):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {vm.display_name}")
                status_line = f"   {vm.status.value.title()}"
                if vm.is_running:
                    status_line += f" | CPU: {vm.cpu_usage:.1f}% | RAM: {vm.memory_usage:.1f}%"
                lines.append(status_line)
                if vm.disk:
                    lines.append(f"   {vm.disk.display_used} | {vm.network_mode.value}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Details  S:Start  X:Stop  P:Pause")
        lines.append(" N:New  Del:Delete  C:Console  O:Storage")
        return lines

    def render_details(self, width: int = 60) -> List[str]:
        vm = self.get_selected_vm()
        if not vm:
            return ["No VM selected"]

        lines = []
        lines.append(f" {vm.display_name}")
        lines.append("─" * width)
        lines.append(f" ID:     {vm.vm_id}")
        lines.append(f" OS:     {vm.os_type.value}")
        lines.append(f" Status: {vm.status.value}")
        lines.append(f" CPU:    {vm.cpu_cores} cores ({vm.cpu_usage:.1f}% usage)")
        lines.append(f" Memory: {vm.memory_str} ({vm.memory_usage:.1f}% usage)")
        if vm.disk:
            lines.append(f" Disk:   {vm.disk.display_used} [{vm.disk.format.value}]")
        lines.append(f" Network: {vm.network_mode.value}")
        lines.append(f" MAC:    {vm.mac_address}")
        lines.append(f" IP:     {vm.ip_address or 'N/A'}")
        lines.append(f" Boot:   {vm.boot_order}")
        if vm.uptime_seconds > 0:
            lines.append(f" Uptime: {vm.uptime_str}")
        if vm.iso_path:
            lines.append(f" ISO:    {vm.iso_path}")

        # Snapshots
        lines.append("")
        lines.append(f" 📸 Snapshots ({len(vm.snapshots)})")
        for snap in vm.snapshots:
            lines.append(f"  • {snap.name} — {snap.time_ago} ({snap.size_gb:.1f} GB)")

        # I/O stats
        if vm.is_running:
            lines.append("")
            lines.append(f" I/O: R {vm.disk_read_mb:.0f} MB / W {vm.disk_write_mb:.0f} MB")
            lines.append(f" Net: RX {vm.network_rx_mb:.0f} MB / TX {vm.network_tx_mb:.0f} MB")

        lines.append("─" * width)
        lines.append(" S:Start  X:Stop  P:Pause  C:Console  T:Snapshot  Esc:Back")
        return lines

    def render_console(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 💻 VM Console")
        lines.append("─" * width)
        for line in self._console_lines[-20:]:
            lines.append(f" {line[:width - 3]}")
        lines.append("─" * width)
        lines.append(" Type commands. Esc:Back to VM list")
        return lines

    def render_storage(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 💾 Storage Manager")
        lines.append("─" * width)

        total, used = self.host_disk_info
        pct = (used / total * 100) if total > 0 else 0
        bar_len = int(pct / 100 * 40)
        bar = "█" * bar_len + "░" * (40 - bar_len)
        lines.append(f" Host Disk: {used:.1f} / {total} GB ({pct:.0f}%)")
        lines.append(f" [{bar}]")
        lines.append("")

        lines.append(" Virtual Disks:")
        for vm in self._vms:
            if vm.disk:
                lines.append(f"  {vm.disk.name} — {vm.disk.display_used} [{vm.disk.format.value}]")

        lines.append("")
        lines.append(f" Total VM Disk: {self.total_vm_disk:.0f} GB")
        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        renderers = {
            "details": self.render_details,
            "console": self.render_console,
            "storage": self.render_storage,
        }
        renderer = renderers.get(self._view_mode, self.render_list)
        return renderer(width, height) if self._view_mode == "list" else renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "details":
            return self._handle_details_key(key)
        elif self._view_mode == "console":
            return self._handle_console_key(key)
        elif self._view_mode == "storage":
            return self._handle_storage_key(key)
        return self._handle_list_key(key)

    def _handle_list_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self._view_mode = "details"
            return "details"
        elif key == "s":
            return "start" if self.start_vm() else "start_failed"
        elif key == "x":
            return "stop" if self.stop_vm() else "stop_failed"
        elif key == "p":
            return "pause" if self.pause_vm() else "pause_failed"
        elif key == "Delete":
            return "delete" if self.delete_vm() else "delete_failed"
        elif key == "c":
            self._init_console()
            self._view_mode = "console"
            return "console"
        elif key == "o":
            self._view_mode = "storage"
            return "storage"
        return None

    def _handle_details_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "list"
            return "back"
        elif key == "s":
            return "start" if self.start_vm() else "start_failed"
        elif key == "x":
            return "stop" if self.stop_vm() else "stop_failed"
        elif key == "p":
            return "pause" if self.pause_vm() else "pause_failed"
        elif key == "c":
            self._init_console()
            self._view_mode = "console"
            return "console"
        return None

    def _handle_console_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "details"
            return "back"
        return None

    def _handle_storage_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "list"
            return "back"
        return None
