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


@dataclass
class VirtualMachine:
    name: str
    state: VMState = VMState.STOPPED
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
    snapshots: int = 0

    @property
    def state_icon(self) -> str:
        icons = {
            VMState.RUNNING: "🟢", VMState.STOPPED: "🔴",
            VMState.PAUSED: "⏸", VMState.SAVED: "💾",
            VMState.ERROR: "❌", VMState.CREATING: "🔄",
            VMState.MIGRATING: "📦",
        }
        return icons.get(self.state, "?")

    @property
    def os_icon(self) -> str:
        icons = {
            VMOSType.LINUX: "🐧", VMOSType.WINDOWS: "🪟",
            VMOSType.MACOS: "🍎", VMOSType.BSD: "😈",
            VMOSType.OTHER: "❓",
        }
        return icons.get(self.os_type, "?")

    @property
    def cpu_bar(self) -> str:
        filled = int(self.cpu_usage / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def ram_bar(self) -> str:
        pct = (self.ram_usage_gb / self.ram_gb * 100) if self.ram_gb else 0
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def disk_bar(self) -> str:
        pct = (self.disk_used_gb / self.disk_gb * 100) if self.disk_gb else 0
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def ram_display(self) -> str:
        return f"{self.ram_usage_gb:.1f}/{self.ram_gb:.0f} GB"

    @property
    def disk_display(self) -> str:
        return f"{self.disk_used_gb:.1f}/{self.disk_gb:.0f} GB"

    @property
    def uptime_display(self) -> str:
        if self.uptime_s < 60:
            return f"{self.uptime_s:.0f}s"
        elif self.uptime_s < 3600:
            return f"{self.uptime_s / 60:.1f}m"
        elif self.uptime_s < 86400:
            return f"{self.uptime_s / 3600:.1f}h"
        return f"{self.uptime_s / 86400:.1f}d"

    @property
    def network_display(self) -> str:
        return f"↓{self.network_rx_mb:.1f}MB ↑{self.network_tx_mb:.1f}MB"


@dataclass
class VMSnapshot:
    name: str
    vm_name: str = ""
    timestamp: float = 0.0
    size_gb: float = 0.0
    description: str = ""
    memory_state: bool = True

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


class VMManager:
    def __init__(self):
        self.vms: List[VirtualMachine] = []
        self.snapshots: List[VMSnapshot] = []
        self.templates: List[VMTemplate] = []
        self.selected_vm: Optional[VirtualMachine] = None
        self.backend: VMBackend = VMBackend.QEMU
        self.auto_cleanup: bool = True
        self._create_sample_data()

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
                            vnc_port=5901, ssh_port=0, gpu_passthrough=True,
                            created_at=now - 86400 * 60, uptime_s=86400 * 3, snapshots=1),
            VirtualMachine(name="nixos-server", state=VMState.STOPPED,
                            os_type=VMOSType.LINUX, os_name="NixOS 24.05",
                            backend=VMBackend.QEMU, cpu_cores=2, ram_gb=4.0,
                            disk_gb=50.0, disk_used_gb=12.0,
                            cpu_usage=0, ram_usage_gb=0,
                            ip_address="192.168.122.12", ssh_port=2223,
                            auto_start=True,
                            created_at=now - 86400 * 90, snapshots=2),
            VirtualMachine(name="freebsd-jail", state=VMState.RUNNING,
                            os_type=VMOSType.BSD, os_name="FreeBSD 14.0",
                            backend=VMBackend.QEMU, cpu_cores=2, ram_gb=2.0,
                            disk_gb=20.0, disk_used_gb=5.5,
                            cpu_usage=8.0, ram_usage_gb=1.2,
                            ip_address="192.168.122.13", ssh_port=2224,
                            created_at=now - 86400 * 15, uptime_s=86400 * 15, snapshots=0),
            VirtualMachine(name="docker-sandbox", state=VMState.RUNNING,
                            os_type=VMOSType.LINUX, os_name="Ubuntu 24.04",
                            backend=VMBackend.DOCKER, cpu_cores=2, ram_gb=2.0,
                            disk_gb=30.0, disk_used_gb=8.0,
                            cpu_usage=15.0, ram_usage_gb=1.5,
                            ip_address="172.17.0.2",
                            created_at=now - 86400 * 5, uptime_s=86400 * 5, snapshots=0),
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
            VMTemplate(name="Nyrqis Dev", os_type=VMOSType.LINUX,
                        os_name="Nyrqis OS", cpu_cores=4, ram_gb=8,
                        disk_gb=100, description="Development environment"),
            VMTemplate(name="Ubuntu Server", os_type=VMOSType.LINUX,
                        os_name="Ubuntu 24.04 LTS", cpu_cores=2, ram_gb=4,
                        disk_gb=50, description="General purpose server"),
            VMTemplate(name="Windows Desktop", os_type=VMOSType.WINDOWS,
                        os_name="Windows 11 Pro", cpu_cores=4, ram_gb=16,
                        disk_gb=256, description="Windows desktop with GPU"),
            VMTemplate(name="FreeBSD", os_type=VMOSType.BSD,
                        os_name="FreeBSD 14.0", cpu_cores=2, ram_gb=2,
                        disk_gb=20, description="Minimal FreeBSD install"),
        ]

    def start_vm(self, name: str) -> bool:
        vm = next((v for v in self.vms if v.name == name), None)
        if vm and vm.state in (VMState.STOPPED, VMState.SAVED):
            vm.state = VMState.RUNNING
            vm.uptime_s = 0
            return True
        return False

    def stop_vm(self, name: str, force: bool = False) -> bool:
        vm = next((v for v in self.vms if v.name == name), None)
        if vm and vm.state == VMState.RUNNING:
            vm.state = VMState.STOPPED
            vm.cpu_usage = 0
            vm.ram_usage_gb = 0
            return True
        return False

    def pause_vm(self, name: str) -> bool:
        vm = next((v for v in self.vms if v.name == name), None)
        if vm and vm.state == VMState.RUNNING:
            vm.state = VMState.PAUSED
            return True
        return False

    def resume_vm(self, name: str) -> bool:
        vm = next((v for v in self.vms if v.name == name), None)
        if vm and vm.state == VMState.PAUSED:
            vm.state = VMState.RUNNING
            return True
        return False

    def create_vm(self, template: Optional[VMTemplate] = None, **kwargs) -> VirtualMachine:
        if template:
            vm = VirtualMachine(
                name=kwargs.get("name", f"vm-{len(self.vms) + 1}"),
                os_type=template.os_type, os_name=template.os_name,
                cpu_cores=template.cpu_cores, ram_gb=template.ram_gb,
                disk_gb=template.disk_gb, **{k: v for k, v in kwargs.items() if k != "name"})
        else:
            vm = VirtualMachine(**kwargs)
        self.vms.append(vm)
        return vm

    def delete_vm(self, name: str) -> bool:
        for i, v in enumerate(self.vms):
            if v.name == name:
                del self.vms[i]
                return True
        return False

    def create_snapshot(self, vm_name: str, name: str, description: str = "") -> Optional[VMSnapshot]:
        vm = next((v for v in self.vms if v.name == vm_name), None)
        if vm:
            snap = VMSnapshot(name=name, vm_name=vm_name, size_gb=vm.disk_used_gb * 0.3,
                               description=description)
            self.snapshots.append(snap)
            vm.snapshots += 1
            return snap
        return None

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


@dataclass
class VMStorage:
    name: str = ""
    size_gb: int = 0
    format: str = "qcow2"
    path: str = ""


@dataclass
class VirtualDisk:
    name: str = ""
    size_gb: int = 0
    bus: str = "virtio"
    path: str = ""


class VMNetwork:
    pass  # backward compat stub

Snapshot = VMSnapshot

VMOS = VMOSType

# ─── Backward-compat exports ────────────────────────────────────────────
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
