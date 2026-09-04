"""
Nyrqis OS - Device Manager
PCI/USB enumeration, driver info, and hardware details.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class DeviceClass(Enum):
    GPU = "gpu"
    NETWORK = "network"
    STORAGE = "storage"
    AUDIO = "audio"
    USB = "usb"
    BRIDGE = "bridge"
    SERIAL = "serial"
    INPUT = "input"
    OTHER = "other"


class DriverStatus(Enum):
    LOADED = "loaded"
    NOT_LOADED = "not_loaded"
    ERROR = "error"
    BLACKLISTED = "blacklisted"


@dataclass
class PCIDevice:
    bus: str = ""
    device: str = ""
    function: int = 0
    vendor_id: str = ""
    device_id: str = ""
    vendor_name: str = ""
    device_name: str = ""
    device_class: DeviceClass = DeviceClass.OTHER
    driver: str = ""
    driver_status: DriverStatus = DriverStatus.LOADED
    iommu_group: int = 0
    kernel_modules: List[str] = field(default_factory=list)
    current_kernel_module: str = ""
    power_state: str = "D0"
    max_link_speed: str = ""
    max_link_width: str = ""

    @property
    def bdf(self) -> str:
        return f"{self.bus}:{self.device}.{self.function}"

    @property
    def class_icon(self) -> str:
        icons = {
            DeviceClass.GPU: "🎮", DeviceClass.NETWORK: "🌐",
            DeviceClass.STORAGE: "💾", DeviceClass.AUDIO: "🔊",
            DeviceClass.USB: "🔌", DeviceClass.BRIDGE: "🔗",
            DeviceClass.SERIAL: "📡", DeviceClass.INPUT: "⌨️",
            DeviceClass.OTHER: "❓",
        }
        return icons.get(self.device_class, "?")

    @property
    def driver_status_icon(self) -> str:
        icons = {
            DriverStatus.LOADED: "🟢", DriverStatus.NOT_LOADED: "⚪",
            DriverStatus.ERROR: "🔴", DriverStatus.BLACKLISTED: "🟡",
        }
        return icons.get(self.driver_status, "?")


@dataclass
class USBDevice:
    bus: int = 0
    port: str = ""
    vendor_id: str = ""
    product_id: str = ""
    vendor_name: str = ""
    product_name: str = ""
    device_class: DeviceClass = DeviceClass.OTHER
    speed: str = ""
    serial: str = ""
    manufacturer: str = ""
    driver: str = ""
    connected: bool = True
    power_ma: int = 0

    @property
    def class_icon(self) -> str:
        icons = {
            DeviceClass.GPU: "🎮", DeviceClass.NETWORK: "🌐",
            DeviceClass.STORAGE: "💾", DeviceClass.AUDIO: "🔊",
            DeviceClass.USB: "🔌", DeviceClass.INPUT: "⌨️",
            DeviceClass.OTHER: "❓",
        }
        return icons.get(self.device_class, "?")


@dataclass
class DriverInfo:
    name: str = ""
    version: str = ""
    description: str = ""
    author: str = ""
    license: str = ""
    size_bytes: int = 0
    status: DriverStatus = DriverStatus.LOADED
    devices_count: int = 0
    module_params: Dict[str, str] = field(default_factory=dict)

    @property
    def size_display(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MB"

    @property
    def status_icon(self) -> str:
        icons = {
            DriverStatus.LOADED: "🟢", DriverStatus.NOT_LOADED: "⚪",
            DriverStatus.ERROR: "🔴", DriverStatus.BLACKLISTED: "🟡",
        }
        return icons.get(self.status, "?")


class DeviceManager:
    def __init__(self):
        self.pci_devices: List[PCIDevice] = []
        self.usb_devices: List[USBDevice] = []
        self.drivers: List[DriverInfo] = []
        self.selected_device: Optional[PCIDevice] = None
        self._create_sample_data()

    def _create_sample_data(self):
        self.pci_devices = [
            PCIDevice(bus="01", device="00", function=0, vendor_id="10de", device_id="2684",
                      vendor_name="NVIDIA", device_name="GeForce RTX 4090",
                      device_class=DeviceClass.GPU, driver="nvidia",
                      driver_status=DriverStatus.LOADED, iommu_group=1,
                      kernel_modules=["nvidia", "nvidia_drm", "nvidia_modeset"],
                      current_kernel_module="nvidia", power_state="D0",
                      max_link_speed="16 GT/s", max_link_width="x16"),
            PCIDevice(bus="02", device="00", function=0, vendor_id="10de", device_id="22a9",
                      vendor_name="NVIDIA", device_name="GA102 Audio",
                      device_class=DeviceClass.AUDIO, driver="snd_hda_intel",
                      driver_status=DriverStatus.LOADED, iommu_group=1,
                      kernel_modules=["snd_hda_intel"],
                      current_kernel_module="snd_hda_intel"),
            PCIDevice(bus="03", device="00", function=0, vendor_id="144d", device_id="a80c",
                      vendor_name="Samsung", device_name="990 Pro 2TB NVMe",
                      device_class=DeviceClass.STORAGE, driver="nvme",
                      driver_status=DriverStatus.LOADED, iommu_group=2,
                      kernel_modules=["nvme"],
                      current_kernel_module="nvme", max_link_speed="16 GT/s",
                      max_link_width="x4"),
            PCIDevice(bus="04", device="00", function=0, vendor_id="8086", device_id="15f3",
                      vendor_name="Intel", device_name="I225-V 2.5GbE",
                      device_class=DeviceClass.NETWORK, driver="igc",
                      driver_status=DriverStatus.LOADED, iommu_group=3,
                      kernel_modules=["igc"],
                      current_kernel_module="igc"),
            PCIDevice(bus="00", device="01", function=0, vendor_id="1022", device_id="1482",
                      vendor_name="AMD", device_name="Ryzen 7950X Root Complex",
                      device_class=DeviceClass.BRIDGE, driver="pcieport",
                      driver_status=DriverStatus.LOADED, iommu_group=0,
                      kernel_modules=["pcieport"]),
            PCIDevice(bus="00", device="02", function=0, vendor_id="1022", device_id="1483",
                      vendor_name="AMD", device_name="Ryzen 7950X PCIe Bridge",
                      device_class=DeviceClass.BRIDGE, driver="pcieport",
                      driver_status=DriverStatus.LOADED, iommu_group=0,
                      kernel_modules=["pcieport"]),
            PCIDevice(bus="05", device="00", function=0, vendor_id="1b21", device_id="2142",
                      vendor_name="ASMedia", device_name="USB 3.2 Controller",
                      device_class=DeviceClass.USB, driver="xhci_pci",
                      driver_status=DriverStatus.LOADED, iommu_group=4,
                      kernel_modules=["xhci_pci"],
                      current_kernel_module="xhci_pci"),
            PCIDevice(bus="06", device="00", function=0, vendor_id="1022", device_id="15e0",
                      vendor_name="AMD", device_name="Family 17h HD Audio",
                      device_class=DeviceClass.AUDIO, driver="snd_hda_intel",
                      driver_status=DriverStatus.LOADED, iommu_group=5,
                      kernel_modules=["snd_hda_intel"],
                      current_kernel_module="snd_hda_intel"),
        ]

        self.usb_devices = [
            USBDevice(bus=1, port="1-1", vendor_id="046d", product_id="c077",
                      vendor_name="Logitech", product_name="G502 HERO Mouse",
                      device_class=DeviceClass.INPUT, speed="USB 2.0",
                      serial="MOSB12345", driver="usbhid", power_ma=100),
            USBDevice(bus=1, port="1-2", vendor_id="046d", product_id="0b22",
                      vendor_name="Logitech", product_name="G915 TKL Keyboard",
                      device_class=DeviceClass.INPUT, speed="USB 2.0",
                      serial="KBD67890", driver="usbhid", power_ma=150),
            USBDevice(bus=1, port="1-3", vendor_id="0781", product_id="5591",
                      vendor_name="SanDisk", product_name="Ultra Flair USB 3.0",
                      device_class=DeviceClass.STORAGE, speed="USB 3.0",
                      serial="040123456DEF", driver="usb-storage", power_ma=200),
            USBDevice(bus=2, port="2-1", vendor_id="04e8", product_id="6860",
                      vendor_name="Samsung", product_name="Portable SSD T7",
                      device_class=DeviceClass.STORAGE, speed="USB 3.2",
                      serial="SSK54321S5", driver="usb-storage", power_ma=350),
            USBDevice(bus=3, port="3-1", vendor_id="0bda", product_id="8153",
                      vendor_name="Realtek", product_name="USB-C Ethernet",
                      device_class=DeviceClass.NETWORK, speed="USB 3.0",
                      serial="RTK001234", driver="r8152", power_ma=180),
        ]

        self.drivers = [
            DriverInfo(name="nvidia", version="535.129.03",
                        description="NVIDIA graphics driver", author="NVIDIA Corporation",
                        license="Proprietary", size_bytes=35000000,
                        status=DriverStatus.LOADED, devices_count=2),
            DriverInfo(name="nvme", version="1.0", description="NVM Express driver",
                        author="Linux Kernel", license="GPL-2.0", size_bytes=80000,
                        status=DriverStatus.LOADED, devices_count=1),
            DriverInfo(name="igc", version="5.15.0", description="Intel I225 Ethernet",
                        author="Intel Corporation", license="GPL-2.0", size_bytes=120000,
                        status=DriverStatus.LOADED, devices_count=1),
            DriverInfo(name="xhci_pci", version="1.0", description="USB 3.0 Host Controller",
                        author="Linux Kernel", license="GPL-2.0", size_bytes=45000,
                        status=DriverStatus.LOADED, devices_count=1),
            DriverInfo(name="snd_hda_intel", version="5.15.0",
                        description="Intel HD Audio", author="Linux Kernel",
                        license="GPL-2.0", size_bytes=95000,
                        status=DriverStatus.LOADED, devices_count=2),
            DriverInfo(name="usbhid", version="1.0", description="USB HID driver",
                        author="Linux Kernel", license="GPL-2.0", size_bytes=35000,
                        status=DriverStatus.LOADED, devices_count=2),
            DriverInfo(name="r8152", version="2.17.0", description="Realtek Ethernet",
                        author="Realtek", license="GPL-2.0", size_bytes=55000,
                        status=DriverStatus.LOADED, devices_count=1),
        ]

    def get_devices_by_class(self, device_class: DeviceClass) -> List[PCIDevice]:
        return [d for d in self.pci_devices if d.device_class == device_class]

    def get_usb_by_class(self, device_class: DeviceClass) -> List[USBDevice]:
        return [d for d in self.usb_devices if d.device_class == device_class]

    def select_device(self, bdf: str) -> Optional[PCIDevice]:
        device = next((d for d in self.pci_devices if d.bdf == bdf), None)
        if device:
            self.selected_device = device
        return device

    def search_pci(self, query: str) -> List[PCIDevice]:
        q = query.lower()
        return [d for d in self.pci_devices if q in d.device_name.lower()
                or q in d.vendor_name.lower() or q in d.driver.lower()]

    def search_usb(self, query: str) -> List[USBDevice]:
        q = query.lower()
        return [d for d in self.usb_devices if q in d.product_name.lower()
                or q in d.vendor_name.lower()]

    def get_driver_info(self, name: str) -> Optional[DriverInfo]:
        return next((d for d in self.drivers if d.name == name), None)

    def get_devices_without_driver(self) -> List[PCIDevice]:
        return [d for d in self.pci_devices if not d.driver]

    def get_stats(self) -> Dict:
        return {
            "pci_devices": len(self.pci_devices),
            "usb_devices": len(self.usb_devices),
            "drivers": len(self.drivers),
            "loaded_drivers": sum(1 for d in self.drivers if d.status == DriverStatus.LOADED),
        }
