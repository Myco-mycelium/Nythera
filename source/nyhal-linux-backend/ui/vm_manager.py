"""
Nyrqis OS - Virtual Machine Manager
Create, start, stop, and resource monitoring.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class VMState(Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    PAUSED = "paused"
    SAVED = "saved"
    ERROR = "error"
    CREATING = "creating"
    MIGRATING = "migrating"


class VMOSType(Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    BSD = "bsd"
    OTHER = "other"


class VMBackend(Enum):
    QEMU = "qemu"
    KVM = "kvm"
    XEN = "xen"
    VIRTUALBOX = "virtualbox"
    DOCKER = "docker"
    PODMAN = "podman"


# ─── Backward-compat aliases ──────────────────────────────────────────
VMStatus = VMState
VMOS = VMOSType
Snapshot = None  # placeholder, defined below

from enum import Enum as _Enum

class VMStatus(_Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    PAUSED = "paused"
    SAVED = "saved"
    ERROR = "error"
    CREATING = "creating"
    DELETING = "deleting"


class NetworkMode(_Enum):
    NAT = "nat"
    BRIDGED = "bridged"
    HOST = "host"
    ISOLATED = "isolated"
    CUSTOM = "custom"


class DiskFormat(_Enum):
    QCOW2 = "qcow2"
    RAW = "raw"
    VMDK = "vmdk"
    VDI = "vdi"
    VHDX = "vhdx"
    VHD = "vhd"


@dataclass
class VirtualMachine:
    name: str
    # Accept both VMState and VMStatus for backward compat
    _state: object = VMState.STOPPED
    os_type: VMOSType = VMOSType.LINUX
    os_name: str = ""
    backend: VMBackend = VMBackend.QEMU
    cpu_cores: int = 2
    ram_gb: float = 4.0
    disk_gb: float = 50.0
    disk_used_gb: float = 0.0
    cpu_usage: float = 0.0
    ram_usage_gb: float = 0.0
    network_rx_mb: float = 0.0
    network_tx_mb: float = 0.0
    ip_address: str = ""
    mac_address: str = ""
    vnc_port: int = 0
    ssh_port: int = 0
    gpu_passthrough: bool = False
    auto_start: bool = False
    created_at: float = 0.0
    uptime_s: float = 0.0
    uptime_seconds: float = 0.0  # backward compat alias
    snapshots: int = 0
    memory_mb: float = 0.0  # backward compat
    state: VMState = None  # set in __post_init__

    def __post_init__(self):
        # Handle positional args: VirtualMachine("name", VMOSType, VMStatus)
        # The _state field holds the 2nd positional arg; os_type holds the 3rd.
        passed_os_type = self.os_type  # save the 3rd positional arg before we overwrite

        # Determine if state was explicitly passed via keyword or position
        # If self.state is not None, it was set explicitly via keyword
        # (since default is None, not being None means keyword was provided)
        state_was_explicitly_set = self.state is not None

        if state_was_explicitly_set:
            # state=VMState.RUNNING or state=VMStatus.RUNNING was passed as keyword
            if isinstance(self.state, VMStatus):
                self.state = VMState(self.state.value)
            elif isinstance(self.state, str):
                self.state = VMState(self.state)
            # else it's already a VMState, leave it
        elif isinstance(self._state, VMOSType):
            # Caller passed (name, VMOSType, ...)
            if isinstance(passed_os_type, VMStatus):
                self.state = VMState(passed_os_type.value)
                self.os_type = self._state
            elif isinstance(passed_os_type, VMState):
                self.state = passed_os_type
                self.os_type = self._state
            else:
                self.state = VMState.STOPPED
                self.os_type = self._state
        elif isinstance(self._state, VMStatus):
            self.state = VMState(self._state.value)
        elif isinstance(self._state, VMState):
            self.state = self._state
        else:
            self.state = VMState.STOPPED

        # Map uptime_seconds -> uptime_s if provided
        if self.uptime_seconds > 0 and self.uptime_s == 0:
            self.uptime_s = self.uptime_seconds
        # Map memory_mb -> ram_gb if provided
        if self.memory_mb > 0 and self.ram_gb == 4.0:
            self.ram_gb = self.memory_mb / 1024.0

    @property
    def status(self) -> VMStatus:
        """Backward-compat: return VMStatus so tests comparing to VMStatus.X pass."""
        return VMStatus(self.state.value)

    @status.setter
    def status(self, value):
        if isinstance(value, VMStatus):
            self.state = VMState(value.value)
        elif isinstance(value, VMState):
            self.state = value
        elif isinstance(value, str):
            self.state = VMState(value)

    @property
    def display_name(self) -> str:
        icon = "\u25b6\ufe0f" if self.state == VMState.RUNNING else "\u23f8"
        return f"{self.name} {icon}"

    @property
    def memory_str(self) -> str:
        if self.memory_mb > 0:
            return f"{self.memory_mb / 1024:.1f} GB"
        return f"{self.ram_gb:.1f} GB"

    @property
    def uptime_str(self) -> str:
        secs = self.uptime_s or self.uptime_seconds
        if secs < 60:
            return f"{secs:.0f}s"
        elif secs < 3600:
            minutes = int(secs / 60)
            return f"{minutes}m"
        elif secs < 86400:
            hours = int(secs / 3600)
            return f"{hours}h"
        days = int(secs / 86400)
        return f"{days}d"

    @property
    def is_running(self) -> bool:
        return self.state == VMState.RUNNING

    @property
    def can_start(self) -> bool:
        return self.state in (VMState.STOPPED, VMState.SAVED)

    @property
    def state_icon(self) -> str:
        icons = {
            VMState.RUNNING: "\U0001f7e2", VMState.STOPPED: "\U0001f534",
            VMState.PAUSED: "\u23f8", VMState.SAVED: "\U0001f4be",
            VMState.ERROR: "\u274c", VMState.CREATING: "\U0001f504",
            VMState.MIGRATING: "\U0001f4e6",
        }
        return icons.get(self.state, "?")

    @property
    def os_icon(self) -> str:
        icons = {
            VMOSType.LINUX: "\U0001f427", VMOSType.WINDOWS: "\U0001faa7",
            VMOSType.MACOS: "\U0001f34e", VMOSType.BSD: "\U0001f608",
            VMOSType.OTHER: "\u2753",
        }
        return icons.get(self.os_type, "?")

    @property
    def cpu_bar(self) -> str:
        filled = int(self.cpu_usage / 5)
        return "\u2588" * filled + "\u2591" * (20 - filled)

    @property
    def ram_bar(self) -> str:
        pct = (self.ram_usage_gb / self.ram_gb * 100) if self.ram_gb else 0
        filled = int(pct / 5)
        return "\u2588" * filled + "\u2591" * (20 - filled)

    @property
    def disk_bar(self) -> str:
        pct = (self.disk_used_gb / self.disk_gb * 100) if self.disk_gb else 0
        filled = int(pct / 5)
        return "\u2588" * filled + "\u2591" * (20 - filled)

    @property
    def ram_display(self) -> str:
        return f"{self.ram_usage_gb:.1f}/{self.ram_gb:.0f} GB"

    @property
    def disk_display(self) -> str:
        return f"{self.disk_used_gb:.1f}/{self.disk_gb:.0f} GB"

    @property
    def uptime_display(self) -> str:
        return self.uptime_str

    @property
    def network_display(self) -> str:
        return f"\u2193{self.network_rx_mb:.1f}MB \u2191{self.network_tx_mb:.1f}MB"


@dataclass
class VMSnapshot:
    name: str
    vm_name: str = ""
    timestamp: float = 0.0
    created: float = 0.0  # backward compat alias
    size_gb: float = 0.0
    description: str = ""
    memory_state: bool = True

    def __post_init__(self):
        if self.created > 0 and self.timestamp == 0:
            self.timestamp = self.created
        if self.timestamp == 0:
            self.timestamp = time.time()

    @property
    def time_ago(self) -> str:
        elapsed = time.time() - self.timestamp
        if elapsed < 60:
            return f"{elapsed:.0f}s ago"
        elif elapsed < 3600:
            return f"{elapsed / 60:.0f}m ago"
        elif elapsed < 86400:
            return f"{elapsed / 3600:.0f}h ago"
        return f"{elapsed / 86400:.0f}d ago"

    @property
    def size_display(self) -> str:
        if self.size_gb < 1:
            return f"{self.size_gb * 1024:.0f} MB"
        return f"{self.size_gb:.2f} GB"


@dataclass
class VMTemplate:
    name: str
    os_type: VMOSType = VMOSType.LINUX
    os_name: str = ""
    cpu_cores: int = 2
    ram_gb: float = 4.0
    disk_gb: float = 50.0
    description: str = ""
    is_official: bool = True

    @property
    def specs(self) -> str:
        return f"{self.cpu_cores} vCPU, {self.ram_gb}GB RAM, {self.disk_gb}GB Disk"


# Fix forward reference
Snapshot = VMSnapshot


@dataclass
class VirtualDisk:
    name: str = ""
    size_gb: int = 0
    bus: str = "virtio"
    path: str = ""
    used_gb: float = 0.0  # backward compat

    @property
    def usage_pct(self) -> float:
        if self.size_gb <= 0:
            return 0.0
        return (self.used_gb / self.size_gb) * 100.0

    @property
    def free_gb(self) -> float:
        return self.size_gb - self.used_gb

    @property
    def display_size(self) -> str:
        return f"{self.size_gb} GB"


@dataclass
class VMStorage:
    name: str = ""
    size_gb: int = 0
    format: str = "qcow2"
    path: str = ""


class VMNetwork:
    pass  # backward compat stub


class VMManager:
    def __init__(self):
        self.vms: List[VirtualMachine] = []
        self.snapshots: List[VMSnapshot] = []
        self.templates: List[VMTemplate] = []
        self.selected_vm: Optional[VirtualMachine] = None
        self.backend: VMBackend = VMBackend.QEMU
        self.auto_cleanup: bool = True
        # Backward-compat fields
        self.view_mode: str = "list"
        self.selected_index: int = 0
        self.console_lines: List[str] = []
        self._create_sample_data()
        # Set selected_vm to first VM
        if self.vms:
            self.selected_vm = self.vms[0]

    def _create_sample_data(self):
        now = time.time()
        self.vms = [
            VirtualMachine(name="nyrqis-dev", state=VMState.RUNNING,
                            os_type=VMOSType.LINUX, os_name="Nyrqis OS 0.1.0",
                            backend=VMBackend.QEMU, cpu_cores=4, ram_gb=8.0,
                            disk_gb=100.0, disk_used_gb=35.2,
                            cpu_usage=45.0, ram_usage_gb=5.8,
                            network_rx_mb=125.0, network_tx_mb=45.0,
                            ip_address="192.168.122.10", mac_address="52:54:00:12:34:56",
                            vnc_port=5900, ssh_port=2222,
                            created_at=now - 86400 * 30, uptime_s=86400 * 7, snapshots=3),
            VirtualMachine(name="windows-11", state=VMState.RUNNING,
                            os_type=VMOSType.WINDOWS, os_name="Windows 11 Pro",
                            backend=VMBackend.QEMU, cpu_cores=4, ram_gb=16.0,
                            disk_gb=256.0, disk_used_gb=85.0,
                            cpu_usage=25.0, ram_usage_gb=9.5,
                            network_rx_mb=450.0, network_tx_mb=120.0,
                            ip_address="192.168.122.11", mac_address="52:54:00:12:34:57",
                            vnc_port=5901, gpu_passthrough=True,
                            created_at=now - 86400 * 60, uptime_s=86400 * 3, snapshots=1),
            VirtualMachine(name="nixos-server", state=VMState.STOPPED,
                            os_type=VMOSType.LINUX, os_name="NixOS 24.05",
                            backend=VMBackend.QEMU, cpu_cores=2, ram_gb=4.0,
                            disk_gb=50.0, disk_used_gb=12.0,
                            auto_start=True,
                            created_at=now - 86400 * 90, snapshots=2),
            VirtualMachine(name="freebsd-jail", state=VMState.RUNNING,
                            os_type=VMOSType.BSD, os_name="FreeBSD 14.0",
                            backend=VMBackend.QEMU, cpu_cores=2, ram_gb=2.0,
                            disk_gb=20.0, disk_used_gb=5.5,
                            cpu_usage=8.0, ram_usage_gb=1.2,
                            ip_address="192.168.122.13", ssh_port=2224,
                            created_at=now - 86400 * 15, uptime_s=86400 * 15, snapshots=0),
            VirtualMachine(name="alpine-vm", state=VMState.STOPPED,
                            os_type=VMOSType.LINUX, os_name="Alpine Linux",
                            backend=VMBackend.QEMU, cpu_cores=1, ram_gb=1.0,
                            disk_gb=10.0, disk_used_gb=2.0,
                            created_at=now - 86400 * 5, snapshots=0),
        ]

        self.snapshots = [
            VMSnapshot(name="pre-update", vm_name="nyrqis-dev",
                        timestamp=now - 86400, size_gb=2.5, description="Before system update"),
            VMSnapshot(name="clean-install", vm_name="nyrqis-dev",
                        timestamp=now - 86400 * 30, size_gb=8.0,
                        description="Fresh installation"),
            VMSnapshot(name="working-state", vm_name="nyrqis-dev",
                        timestamp=now - 86400 * 7, size_gb=12.0,
                        description="All tests passing"),
            VMSnapshot(name="baseline", vm_name="windows-11",
                        timestamp=now - 86400 * 60, size_gb=25.0,
                        description="Windows setup complete"),
        ]

        self.templates = [
            VMTemplate(name="Ubuntu Server", os_type=VMOSType.LINUX,
                        os_name="Ubuntu 24.04 LTS", cpu_cores=2, ram_gb=4,
                        disk_gb=50, description="General purpose server"),
            VMTemplate(name="Nyrqis Dev", os_type=VMOSType.LINUX,
                        os_name="Nyrqis OS", cpu_cores=4, ram_gb=8,
                        disk_gb=100, description="Development environment"),
            VMTemplate(name="Windows Desktop", os_type=VMOSType.WINDOWS,
                        os_name="Windows 11 Pro", cpu_cores=4, ram_gb=16,
                        disk_gb=256, description="Windows desktop with GPU"),
            VMTemplate(name="FreeBSD", os_type=VMOSType.BSD,
                        os_name="FreeBSD 14.0", cpu_cores=2, ram_gb=2,
                        disk_gb=20, description="Minimal FreeBSD install"),
        ]

    # ─── Backward-compat index-based operations ───────────────────────
    def start_vm(self, vm_id) -> bool:
        """Accept name (str) or index (int)."""
        if isinstance(vm_id, int):
            if 0 <= vm_id < len(self.vms):
                vm = self.vms[vm_id]
                if vm.state in (VMState.STOPPED, VMState.SAVED):
                    vm.state = VMState.RUNNING
                    vm.uptime_s = 0
                    return True
            return False
        # String name
        vm = next((v for v in self.vms if v.name == vm_id), None)
        if vm and vm.state in (VMState.STOPPED, VMState.SAVED):
            vm.state = VMState.RUNNING
            vm.uptime_s = 0
            return True
        return False

    def stop_vm(self, vm_id, force: bool = False) -> bool:
        if isinstance(vm_id, int):
            if 0 <= vm_id < len(self.vms):
                vm = self.vms[vm_id]
                if vm.state == VMState.RUNNING:
                    vm.state = VMState.STOPPED
                    vm.cpu_usage = 0
                    vm.ram_usage_gb = 0
                    return True
            return False
        vm = next((v for v in self.vms if v.name == vm_id), None)
        if vm and vm.state == VMState.RUNNING:
            vm.state = VMState.STOPPED
            vm.cpu_usage = 0
            vm.ram_usage_gb = 0
            return True
        return False

    def pause_vm(self, vm_id) -> bool:
        if isinstance(vm_id, int):
            if 0 <= vm_id < len(self.vms):
                vm = self.vms[vm_id]
                if vm.state == VMState.RUNNING:
                    vm.state = VMState.PAUSED
                    return True
            return False
        vm = next((v for v in self.vms if v.name == vm_id), None)
        if vm and vm.state == VMState.RUNNING:
            vm.state = VMState.PAUSED
            return True
        return False

    def resume_vm(self, vm_id) -> bool:
        if isinstance(vm_id, int):
            if 0 <= vm_id < len(self.vms):
                vm = self.vms[vm_id]
                if vm.state == VMState.PAUSED:
                    vm.state = VMState.RUNNING
                    return True
            return False
        vm = next((v for v in self.vms if v.name == vm_id), None)
        if vm and vm.state == VMState.PAUSED:
            vm.state = VMState.RUNNING
            return True
        return False

    def create_vm(self, name_or_template=None, template=None, **kwargs) -> Optional[VirtualMachine]:
        """Support create_vm("name", template) and create_vm(template=...) and create_vm(name=..., ...)."""
        actual_template = None
        vm_name = None

        if isinstance(name_or_template, str):
            vm_name = name_or_template
        elif isinstance(name_or_template, VMTemplate):
            actual_template = name_or_template

        if template is not None:
            actual_template = template

        if actual_template:
            vm = VirtualMachine(
                name=vm_name or f"vm-{len(self.vms) + 1}",
                os_type=actual_template.os_type, os_name=actual_template.os_name,
                cpu_cores=actual_template.cpu_cores, ram_gb=actual_template.ram_gb,
                disk_gb=actual_template.disk_gb, **kwargs)
        elif vm_name:
            vm = VirtualMachine(name=vm_name, **kwargs)
        else:
            vm = VirtualMachine(**kwargs)
        self.vms.append(vm)
        return vm

    def delete_vm(self, vm_id) -> bool:
        if isinstance(vm_id, int):
            if 0 <= vm_id < len(self.vms):
                del self.vms[vm_id]
                return True
            return False
        for i, v in enumerate(self.vms):
            if v.name == vm_id:
                del self.vms[i]
                return True
        return False

    def create_snapshot(self, vm_id, name: str, description: str = "") -> Optional[VMSnapshot]:
        """Accept name (str) or index (int) for vm_id."""
        if isinstance(vm_id, int):
            if 0 <= vm_id < len(self.vms):
                vm = self.vms[vm_id]
                snap = VMSnapshot(name=name, vm_name=vm.name, size_gb=vm.disk_used_gb * 0.3,
                                   description=description)
                self.snapshots.append(snap)
                vm.snapshots += 1
                return snap
            return None
        vm = next((v for v in self.vms if v.name == vm_id), None)
        if vm:
            snap = VMSnapshot(name=name, vm_name=vm.name, size_gb=vm.disk_used_gb * 0.3,
                               description=description)
            self.snapshots.append(snap)
            vm.snapshots += 1
            return snap
        return None

    def delete_snapshot(self, vm_id, snap_idx: int) -> bool:
        """Delete a snapshot by VM index/name and snapshot index."""
        if isinstance(vm_id, int):
            vm_name = self.vms[vm_id].name if 0 <= vm_id < len(self.vms) else None
        else:
            vm_name = vm_id
        if not vm_name:
            return False
        vm_snaps = [s for s in self.snapshots if s.vm_name == vm_name]
        if 0 <= snap_idx < len(vm_snaps):
            self.snapshots.remove(vm_snaps[snap_idx])
            vm = next((v for v in self.vms if v.name == vm_name), None)
            if vm:
                vm.snapshots = max(0, vm.snapshots - 1)
            return True
        return False

    # ─── Navigation ───────────────────────────────────────────────────
    def select_up(self):
        if self.selected_index > 0:
            self.selected_index -= 1
        if self.selected_index < len(self.vms):
            self.selected_vm = self.vms[self.selected_index]

    def select_down(self):
        if self.selected_index < len(self.vms) - 1:
            self.selected_index += 1
        if self.selected_index < len(self.vms):
            self.selected_vm = self.vms[self.selected_index]

    def set_view(self, mode: str):
        self.view_mode = mode

    def handle_key(self, key: str) -> str:
        if key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "Enter":
            return "open"
        elif key == "Escape":
            return "back"
        return ""

    # ─── Console ──────────────────────────────────────────────────────
    def _init_console(self):
        if not self.console_lines:
            self.console_lines = [
                "Nyrqis VM Console v1.0",
                "Type 'help' for available commands.",
                "",
                "nyrqis@nyrqis-dev:~$",
            ]

    def send_console_input(self, cmd: str):
        self._init_console()
        self.console_lines.append(f"> {cmd}")
        # Simulate some output
        if cmd.startswith("uname"):
            self.console_lines.append("Linux nyrqis-dev 6.11.0-nyrqis #1 SMP")
        elif cmd.startswith("ls"):
            self.console_lines.append("Documents/  Downloads/  Pictures/  .config/")
        elif cmd.startswith("neofetch"):
            self.console_lines.append("      _  _           nyrqis@nyrqis-dev")
            self.console_lines.append("     | \\||          OS: Nyrqis OS 0.1.0")
        elif cmd == "help":
            self.console_lines.append("Available: uname, ls, cat, neofetch, free, df")
        else:
            self.console_lines.append(f"nyrqis@nyrqis-dev:~$ {cmd}: command executed")
        self.console_lines.append("nyrqis@nyrqis-dev:~$")

    # ─── Properties ───────────────────────────────────────────────────
    @property
    def running_count(self) -> int:
        return len([v for v in self.vms if v.state == VMState.RUNNING])

    @property
    def total_vm_disk(self) -> float:
        return sum(v.disk_gb for v in self.vms)

    # ─── Render methods ───────────────────────────────────────────────
    def render_list(self) -> List[str]:
        lines = ["VM MANAGER - List View", "=" * 40]
        for i, vm in enumerate(self.vms):
            marker = " > " if i == self.selected_index else "   "
            lines.append(f"{marker}{vm.state_icon} {vm.name} ({vm.os_type.value})")
        lines.append(f"\nRunning: {self.running_count}/{len(self.vms)}")
        return lines

    def render_details(self) -> List[str]:
        vm = self.selected_vm or (self.vms[0] if self.vms else None)
        if not vm:
            return ["No VM selected"]
        lines = [f"VM Details: {vm.name}", "=" * 40]
        lines.append(f"OS: {vm.os_name} ({vm.os_type.value})")
        lines.append(f"State: {vm.state.value}")
        lines.append(f"CPU: {vm.cpu_cores} cores ({vm.cpu_usage:.0f}%)")
        lines.append(f"RAM: {vm.ram_display}")
        lines.append(f"Disk: {vm.disk_display}")
        lines.append(f"Network: {vm.network_display}")
        if vm.ip_address:
            lines.append(f"IP: {vm.ip_address}")
        return lines

    def render_storage(self) -> List[str]:
        lines = ["VM Storage", "=" * 40]
        for vm in self.vms:
            lines.append(f"  {vm.name}: {vm.disk_display} ({vm.disk_gb:.0f}GB total)")
        lines.append(f"\nTotal: {self.total_vm_disk:.0f} GB across {len(self.vms)} VMs")
        return lines

    def render_console(self) -> List[str]:
        self._init_console()
        return self.console_lines[-20:]  # Last 20 lines

    # ─── Original methods ─────────────────────────────────────────────
    def restore_snapshot(self, snapshot_name: str) -> bool:
        snap = next((s for s in self.snapshots if s.name == snapshot_name), None)
        if snap:
            vm = next((v for v in self.vms if v.name == snap.vm_name), None)
            if vm:
                vm.state = VMState.RUNNING
                return True
        return False

    def get_running_vms(self) -> List[VirtualMachine]:
        return [v for v in self.vms if v.state == VMState.RUNNING]

    def get_stopped_vms(self) -> List[VirtualMachine]:
        return [v for v in self.vms if v.state == VMState.STOPPED]

    def search(self, query: str) -> List[VirtualMachine]:
        q = query.lower()
        return [v for v in self.vms if q in v.name.lower() or q in v.os_name.lower()]

    def get_snapshots_for_vm(self, vm_name: str) -> List[VMSnapshot]:
        return [s for s in self.snapshots if s.vm_name == vm_name]

    def get_stats(self) -> Dict:
        running = self.get_running_vms()
        total_cpu = sum(v.cpu_cores for v in self.vms)
        used_cpu = sum(v.cpu_usage * v.cpu_cores / 100 for v in running)
        total_ram = sum(v.ram_gb for v in self.vms)
        used_ram = sum(v.ram_usage_gb for v in running)
        return {
            "total_vms": len(self.vms),
            "running": len(running),
            "stopped": len(self.get_stopped_vms()),
            "total_cpu": total_cpu,
            "used_cpu": round(used_cpu, 1),
            "total_ram_gb": total_ram,
            "used_ram_gb": round(used_ram, 1),
            "snapshots": len(self.snapshots),
            "templates": len(self.templates),
        }
