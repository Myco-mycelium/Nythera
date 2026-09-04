"""
Nyrqis OS - USB Device Manager
Device info, eject, and mount controls.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class USBDeviceClass(Enum):
    MASS_STORAGE = "mass_storage"
    HID = "hid"
    AUDIO = "audio"
    VIDEO = "video"
    NETWORK = "network"
    SERIAL = "serial"
    PRINTER = "printer"
    HUB = "hub"
    MTP = "mtp"
    CUSTOM = "custom"


class USBSpeed(Enum):
    LOW = "low"         # 1.5 Mbps
    FULL = "full"       # 12 Mbps
    HIGH = "high"       # 480 Mbps
    SUPER = "super"     # 5 Gbps
    SUPER_PLUS = "super_plus"  # 10 Gbps
    ULTRA = "ultra"     # 20 Gbps

    @property
    def display(self) -> str:
        displays = {
            USBSpeed.LOW: "USB 1.0 Low (1.5 Mbps)",
            USBSpeed.FULL: "USB 1.1 Full (12 Mbps)",
            USBSpeed.HIGH: "USB 2.0 High (480 Mbps)",
            USBSpeed.SUPER: "USB 3.0 SuperSpeed (5 Gbps)",
            USBSpeed.SUPER_PLUS: "USB 3.1 SuperSpeed+ (10 Gbps)",
            USBSpeed.ULTRA: "USB4 Ultra (20 Gbps)",
        }
        return displays.get(self, "Unknown")


class MountState(Enum):
    UNMOUNTED = "unmounted"
    MOUNTED = "mounted"
    EJECTING = "ejecting"
    ERROR = "error"


@dataclass
class USBDevice:
    bus: int = 0
    port: int = 0
    vendor_id: str = ""
    product_id: str = ""
    vendor_name: str = ""
    product_name: str = ""
    device_class: USBDeviceClass = USBDeviceClass.CUSTOM
    speed: USBSpeed = USBSpeed.HIGH
    serial_number: str = ""
    manufacturer: str = ""
    product: str = ""
    mount_point: str = ""
    mount_state: MountState = MountState.UNMOUNTED
    filesystem: str = ""
    capacity_gb: float = 0.0
    used_gb: float = 0.0
    power_ma: int = 500
    max_power_ma: int = 900
    connected_at: float = 0.0
    is_connected: bool = True

    @property
    def bus_port(self) -> str:
        return f"{self.bus}-{self.port}"

    @property
    def mount_icon(self) -> str:
        icons = {
            MountState.UNMOUNTED: "⬜",
            MountState.MOUNTED: "🟩",
            MountState.EJECTING: "🟨",
            MountState.ERROR: "🟥",
        }
        return icons.get(self.mount_state, "?")

    @property
    def class_icon(self) -> str:
        icons = {
            USBDeviceClass.MASS_STORAGE: "💾",
            USBDeviceClass.HID: "⌨️",
            USBDeviceClass.AUDIO: "🔊",
            USBDeviceClass.VIDEO: "📷",
            USBDeviceClass.NETWORK: "🌐",
            USBDeviceClass.SERIAL: "🔌",
            USBDeviceClass.PRINTER: "🖨️",
            USBDeviceClass.HUB: "🔗",
            USBDeviceClass.MTP: "📱",
            USBDeviceClass.CUSTOM: "🔧",
        }
        return icons.get(self.device_class, "❓")

    @property
    def capacity_display(self) -> str:
        if self.capacity_gb == 0:
            return ""
        return f"{self.capacity_gb:.1f} GB"

    @property
    def usage_bar(self) -> str:
        if self.capacity_gb == 0:
            return ""
        pct = (self.used_gb / self.capacity_gb) * 100
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def power_display(self) -> str:
        return f"{self.power_ma} mA / {self.max_power_ma} mA"


@dataclass
class USBEvent:
    timestamp: float
    device_name: str
    event_type: str  # connected, disconnected, mounted, ejected, error
    message: str = ""
    bus_port: str = ""

    @property
    def event_icon(self) -> str:
        icons = {
            "connected": "🟢",
            "disconnected": "🔴",
            "mounted": "🟩",
            "ejected": "⬜",
            "error": "🟥",
        }
        return icons.get(self.event_type, "?")


class USBManager:
    def __init__(self):
        self.devices: List[USBDevice] = []
        self.events: List[USBEvent] = []
        self.hub_ports: int = 16
        self.total_power_ma: int = 1500
        self.used_power_ma: int = 0
        self.auto_mount: bool = True
        self.notify_on_connect: bool = True
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.devices = [
            USBDevice(bus=1, port=1, vendor_id="0781", product_id="5591",
                      vendor_name="SanDisk", product_name="Ultra Flair USB 3.0",
                      device_class=USBDeviceClass.MASS_STORAGE, speed=USBSpeed.SUPER,
                      serial_number="040123456DEF", manufacturer="SanDisk",
                      product="Ultra Flair 128GB", mount_point="/media/zeus/sandisk",
                      mount_state=MountState.MOUNTED, filesystem="exFAT",
                      capacity_gb=128.0, used_gb=45.2, power_ma=200, max_power_ma=900,
                      connected_at=now - 3600),
            USBDevice(bus=1, port=2, vendor_id="046d", product_id="c077",
                      vendor_name="Logitech", product_name="G502 HERO Mouse",
                      device_class=USBDeviceClass.HID, speed=USBSpeed.FULL,
                      serial_number="MOSB12345", manufacturer="Logitech",
                      product="G502 HERO Gaming Mouse", power_ma=100, max_power_ma=500,
                      connected_at=now - 7200),
            USBDevice(bus=1, port=3, vendor_id="046d", product_id="0b22",
                      vendor_name="Logitech", product_name="G915 TKL Keyboard",
                      device_class=USBDeviceClass.HID, speed=USBSpeed.FULL,
                      serial_number="KBD67890", manufacturer="Logitech",
                      product="G915 TKL Lightspeed", power_ma=150, max_power_ma=500,
                      connected_at=now - 7200),
            USBDevice(bus=2, port=1, vendor_id="05ac", product_id="1238",
                      vendor_name="Apple", product_name="iPhone 15 Pro",
                      device_class=USBDeviceClass.MTP, speed=USBSpeed.SUPER_PLUS,
                      serial_number="F2LXK8HQ2P", manufacturer="Apple Inc.",
                      product="iPhone 15 Pro", mount_point="/media/zeus/iphone",
                      mount_state=MountState.MOUNTED, filesystem="Apple APFS",
                      capacity_gb=256.0, used_gb=180.5, power_ma=600, max_power_ma=3000,
                      connected_at=now - 1800),
            USBDevice(bus=2, port=2, vendor_id="04e8", product_id="6860",
                      vendor_name="Samsung", product_name="T7 Portable SSD",
                      device_class=USBDeviceClass.MASS_STORAGE, speed=USBSpeed.SUPER_PLUS,
                      serial_number="SSK54321S5", manufacturer="Samsung",
                      product="Portable SSD T7 1TB", mount_point="/media/zeus/t7",
                      mount_state=MountState.MOUNTED, filesystem="ext4",
                      capacity_gb=1000.0, used_gb=320.0, power_ma=350, max_power_ma=900,
                      connected_at=now - 600),
            USBDevice(bus=3, port=1, vendor_id="0bda", product_id="8153",
                      vendor_name="Realtek", product_name="USB-C Ethernet Adapter",
                      device_class=USBDeviceClass.NETWORK, speed=USBSpeed.SUPER,
                      serial_number="RTK001234", manufacturer="Realtek",
                      product="RTL8153 Gigabit Ethernet", power_ma=180, max_power_ma=900,
                      connected_at=now - 1200),
            USBDevice(bus=3, port=2, vendor_id="041e", product_id="4080",
                      vendor_name="Creative", product_name="Sound Blaster X4",
                      device_class=USBDeviceClass.AUDIO, speed=USBSpeed.HIGH,
                      serial_number="SBX45678", manufacturer="Creative Labs",
                      product="Sound Blaster X4", power_ma=500, max_power_ma=900,
                      connected_at=now - 2400),
        ]
        self.used_power_ma = sum(d.power_ma for d in self.devices if d.is_connected)

        self.events = [
            USBEvent(timestamp=now - 3600, device_name="SanDisk Ultra Flair",
                     event_type="connected", bus_port="1-1"),
            USBEvent(timestamp=now - 3599, device_name="SanDisk Ultra Flair",
                     event_type="mounted", message="/media/zeus/sandisk"),
            USBEvent(timestamp=now - 1800, device_name="iPhone 15 Pro",
                     event_type="connected", bus_port="2-1"),
            USBEvent(timestamp=now - 1799, device_name="iPhone 15 Pro",
                     event_type="mounted", message="/media/zeus/iphone"),
            USBEvent(timestamp=now - 1200, device_name="Realtek Ethernet",
                     event_type="connected", bus_port="3-1"),
            USBEvent(timestamp=now - 600, device_name="Samsung T7 SSD",
                     event_type="connected", bus_port="2-2"),
            USBEvent(timestamp=now - 599, device_name="Samsung T7 SSD",
                     event_type="mounted", message="/media/zeus/t7"),
        ]

    def mount_device(self, bus_port: str) -> bool:
        device = next((d for d in self.devices if d.bus_port == bus_port), None)
        if device and device.mount_state == MountState.UNMOUNTED:
            device.mount_state = MountState.MOUNTED
            device.mount_point = f"/media/zeus/{device.product_name.split()[0].lower()}"
            self.events.append(USBEvent(
                timestamp=time.time(), device_name=device.product_name,
                event_type="mounted", message=device.mount_point))
            return True
        return False

    def unmount_device(self, bus_port: str) -> bool:
        device = next((d for d in self.devices if d.bus_port == bus_port), None)
        if device and device.mount_state == MountState.MOUNTED:
            device.mount_state = MountState.UNMOUNTED
            device.mount_point = ""
            self.events.append(USBEvent(
                timestamp=time.time(), device_name=device.product_name,
                event_type="ejected"))
            return True
        return False

    def eject_device(self, bus_port: str) -> bool:
        device = next((d for d in self.devices if d.bus_port == bus_port), None)
        if device:
            device.mount_state = MountState.EJECTING
            device.mount_state = MountState.UNMOUNTED
            device.mount_point = ""
            self.events.append(USBEvent(
                timestamp=time.time(), device_name=device.product_name,
                event_type="ejected"))
            return True
        return False

    def disconnect_device(self, bus_port: str) -> bool:
        idx = next((i for i, d in enumerate(self.devices) if d.bus_port == bus_port), None)
        if idx is not None:
            device = self.devices[idx]
            device.is_connected = False
            self.used_power_ma -= device.power_ma
            self.events.append(USBEvent(
                timestamp=time.time(), device_name=device.product_name,
                event_type="disconnected"))
            return True
        return False

    def get_storage_devices(self) -> List[USBDevice]:
        return [d for d in self.devices
                if d.device_class in (USBDeviceClass.MASS_STORAGE, USBDeviceClass.MTP)
                and d.is_connected]

    def get_input_devices(self) -> List[USBDevice]:
        return [d for d in self.devices if d.device_class == USBDeviceClass.HID]

    def get_connected_devices(self) -> List[USBDevice]:
        return [d for d in self.devices if d.is_connected]

    def get_device(self, bus_port: str) -> Optional[USBDevice]:
        return next((d for d in self.devices if d.bus_port == bus_port), None)

    def get_power_summary(self) -> Dict:
        return {
            "total_ma": self.total_power_ma,
            "used_ma": self.used_power_ma,
            "available_ma": self.total_power_ma - self.used_power_ma,
            "usage_percent": round((self.used_power_ma / self.total_power_ma) * 100, 1),
        }

    def get_recent_events(self, limit: int = 10) -> List[USBEvent]:
        return sorted(self.events, key=lambda e: e.timestamp, reverse=True)[:limit]
