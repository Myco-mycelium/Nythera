"""Network Topology Mapper — device discovery, connection mapping, status monitoring for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time


class DeviceType(Enum):
    ROUTER = "Router"
    SWITCH = "Switch"
    FIREWALL = "Firewall"
    ACCESS_POINT = "Access Point"
    SERVER = "Server"
    WORKSTATION = "Workstation"
    LAPTOP = "Laptop"
    PHONE = "Phone"
    TABLET = "Tablet"
    PRINTER = "Printer"
    NAS = "NAS"
    IoT = "IoT Device"
    CAMERA = "Camera"
    UNKNOWN = "Unknown"


class DeviceStatus(Enum):
    ONLINE = "Online"
    OFFLINE = "Offline"
    DEGRADED = "Degraded"
    UNREACHABLE = "Unreachable"
    MAINTENANCE = "Maintenance"


class LinkType(Enum):
    ETHERNET = "Ethernet"
    FIBER = "Fiber"
    WIFI = "WiFi"
    BLUETOOTH = "Bluetooth"
    USB = "USB"
    VPN = "VPN"
    VLAN = "VLAN"


class LinkSpeed(Enum):
    MBPS_10 = "10 Mbps"
    MBPS_100 = "100 Mbps"
    GBPS_1 = "1 Gbps"
    GBPS_2_5 = "2.5 Gbps"
    GBPS_5 = "5 Gbps"
    GBPS_10 = "10 Gbps"
    GBPS_25 = "25 Gbps"
    GBPS_40 = "40 Gbps"
    GBPS_100 = "100 Gbps"
    WIFI_5 = "WiFi 5 (802.11ac)"
    WIFI_6 = "WiFi 6 (802.11ax)"
    WIFI_6E = "WiFi 6E"
    WIFI_7 = "WiFi 7 (802.11be)"


class Protocol(Enum):
    OSPF = "OSPF"
    BGP = "BGP"
    RIP = "RIP"
    STP = "STP"
    LACP = "LACP"
    VLAN_TRUNK = "VLAN Trunk"


@dataclass
class NetworkInterface:
    name: str = ""
    mac: str = ""
    ip: str = ""
    speed: LinkSpeed = LinkSpeed.GBPS_1
    status: DeviceStatus = DeviceStatus.ONLINE
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_errors: int = 0
    tx_errors: int = 0

    @property
    def traffic_str(self) -> str:
        rx = self._fmt(self.rx_bytes)
        tx = self._fmt(self.tx_bytes)
        return f"↓{rx} ↑{tx}"

    @property
    def error_count(self) -> int:
        return self.rx_errors + self.tx_errors

    @staticmethod
    def _fmt(b: int) -> str:
        if b < 1024**2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024**3:
            return f"{b / 1024**2:.1f} MB"
        return f"{b / 1024**3:.2f} GB"


@dataclass
class NetworkDevice:
    id: int
    name: str = ""
    device_type: DeviceType = DeviceType.UNKNOWN
    status: DeviceStatus = DeviceStatus.ONLINE
    ip: str = ""
    mac: str = ""
    vendor: str = ""
    model: str = ""
    firmware: str = ""
    uptime_s: float = 0.0
    interfaces: List[NetworkInterface] = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    cpu_usage: float = 0.0
    memory_mb: float = 0.0
    temperature: float = 0.0
    last_seen: float = 0.0
    tags: List[str] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        icons = {
            DeviceStatus.ONLINE: "🟢", DeviceStatus.OFFLINE: "🔴",
            DeviceStatus.DEGRADED: "🟡", DeviceStatus.UNREACHABLE: "⚫",
            DeviceStatus.MAINTENANCE: "🔧",
        }
        return icons.get(self.status, "?")

    @property
    def type_icon(self) -> str:
        icons = {
            DeviceType.ROUTER: "🌐", DeviceType.SWITCH: "🔀",
            DeviceType.FIREWALL: "🛡️", DeviceType.ACCESS_POINT: "📡",
            DeviceType.SERVER: "🖥", DeviceType.WORKSTATION: "💻",
            DeviceType.LAPTOP: "💻", DeviceType.PHONE: "📱",
            DeviceType.TABLET: "📱", DeviceType.PRINTER: "🖨",
            DeviceType.NAS: "💾", DeviceType.IoT: "📡",
            DeviceType.CAMERA: "📷",
        }
        return icons.get(self.device_type, "❓")

    @property
    def uptime_str(self) -> str:
        if self.uptime_s < 60:
            return f"{self.uptime_s:.0f}s"
        elif self.uptime_s < 3600:
            return f"{self.uptime_s / 60:.0f}m"
        elif self.uptime_s < 86400:
            return f"{self.uptime_s / 3600:.1f}h"
        return f"{self.uptime_s / 86400:.1f}d"

    @property
    def cpu_bar(self) -> str:
        filled = int(self.cpu_usage / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def interface_count(self) -> int:
        return len(self.interfaces)


@dataclass
class NetworkLink:
    source_id: int
    target_id: int
    link_type: LinkType = LinkType.ETHERNET
    speed: LinkSpeed = LinkSpeed.GBPS_1
    status: DeviceStatus = DeviceStatus.ONLINE
    bandwidth_used: float = 0.0  # percent
    latency_ms: float = 0.0
    packet_loss: float = 0.0
    protocols: List[Protocol] = field(default_factory=list)
    vlan_id: int = 0
    port_src: str = ""
    port_dst: str = ""

    @property
    def bandwidth_bar(self) -> str:
        filled = int(self.bandwidth_used / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def latency_str(self) -> str:
        if self.latency_ms < 1:
            return f"{self.latency_ms * 1000:.0f}µs"
        elif self.latency_ms < 1000:
            return f"{self.latency_ms:.1f}ms"
        return f"{self.latency_ms / 1000:.2f}s"

    @property
    def loss_str(self) -> str:
        return f"{self.packet_loss:.2f}%"

    @property
    def link_type_icon(self) -> str:
        icons = {
            LinkType.ETHERNET: "⚡", LinkType.FIBER: "💫", LinkType.WIFI: "📡",
            LinkType.BLUETOOTH: "🔵", LinkType.USB: "🔌", LinkType.VPN: "🔒",
            LinkType.VLAN: "🏷",
        }
        return icons.get(self.link_type, "?")


@dataclass
class DiscoveryResult:
    device_id: int = 0
    ip: str = ""
    mac: str = ""
    vendor: str = ""
    response_time_ms: float = 0.0
    timestamp: float = 0.0
    method: str = ""  # arp, ping, mdns, snmp

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))


class NetworkTopology:
    def __init__(self):
        self._devices: List[NetworkDevice] = []
        self._links: List[NetworkLink] = []
        self._discoveries: List[DiscoveryResult] = []
        self._selected_device: int = 0
        self._view_mode: str = "topology"
        self._auto_discover: bool = True
        self._show_labels: bool = True
        self._show_bandwidth: bool = True
        self._scan_running: bool = False
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        self._devices = [
            NetworkDevice(id=0, name="Gateway Router", device_type=DeviceType.ROUTER, status=DeviceStatus.ONLINE,
                          ip="192.168.1.1", mac="AA:BB:CC:DD:00:01", vendor="Cisco", model="ISR 4331", firmware="16.12.4",
                          uptime_s=now - 86400 * 30, interfaces=[
                              NetworkInterface("GigE0/0", "AA:BB:CC:DD:00:01:01", "192.168.1.1", LinkSpeed.GBPS_1),
                              NetworkInterface("GigE0/1", "AA:BB:CC:DD:00:01:02", "10.0.0.1", LinkSpeed.GBPS_1),
                          ], x=400, y=100, cpu_usage=15.0, memory_mb=512),
            NetworkDevice(id=1, name="Core Switch", device_type=DeviceType.SWITCH, status=DeviceStatus.ONLINE,
                          ip="192.168.1.2", mac="AA:BB:CC:DD:00:02", vendor="Ubiquiti", model="USW-24-PoE", firmware="7.0.0",
                          uptime_s=now - 86400 * 30, interfaces=[
                              NetworkInterface("eth0", "AA:BB:CC:DD:00:02:01", "192.168.1.2", LinkSpeed.GBPS_1),
                              NetworkInterface("eth1", "AA:BB:CC:DD:00:02:02", "192.168.1.2"),
                          ], x=400, y=250, cpu_usage=8.0, memory_mb=256),
            NetworkDevice(id=2, name="Firewall", device_type=DeviceType.FIREWALL, status=DeviceStatus.ONLINE,
                          ip="192.168.1.3", mac="AA:BB:CC:DD:00:03", vendor="pfSense", model="SG-1100", firmware="2.7.0",
                          uptime_s=now - 86400 * 60, x=200, y=100, cpu_usage=12.0, memory_mb=384),
            NetworkDevice(id=3, name="WiFi AP", device_type=DeviceType.ACCESS_POINT, status=DeviceStatus.ONLINE,
                          ip="192.168.1.10", mac="AA:BB:CC:DD:00:04", vendor="Ubiquiti", model="U6-Pro", firmware="6.5.0",
                          uptime_s=now - 86400 * 15, interfaces=[
                              NetworkInterface("wlan0", "", "192.168.1.10", LinkSpeed.WIFI_6),
                          ], x=400, y=400, cpu_usage=5.0, memory_mb=128),
            NetworkDevice(id=4, name="Web Server", device_type=DeviceType.SERVER, status=DeviceStatus.ONLINE,
                          ip="192.168.1.100", mac="AA:BB:CC:DD:00:05", vendor="Dell", model="PowerEdge R740", firmware="2.12.0",
                          uptime_s=now - 86400 * 90, interfaces=[
                              NetworkInterface("eth0", "AA:BB:CC:DD:00:05:01", "192.168.1.100", LinkSpeed.GBPS_10),
                          ], x=600, y=200, cpu_usage=45.0, memory_mb=16384),
            NetworkDevice(id=5, name="NAS", device_type=DeviceType.NAS, status=DeviceStatus.ONLINE,
                          ip="192.168.1.200", mac="AA:BB:CC:DD:00:06", vendor="Synology", model="DS920+", firmware="7.2.0",
                          uptime_s=now - 86400 * 60, x=600, y=400, cpu_usage=25.0, memory_mb=8192),
            NetworkDevice(id=6, name="Workstation", device_type=DeviceType.WORKSTATION, status=DeviceStatus.ONLINE,
                          ip="192.168.1.50", mac="AA:BB:CC:DD:00:07", vendor="Custom", model="Nyrqis Build", firmware="1.0.0",
                          uptime_s=now - 3600, x=200, y=500, cpu_usage=35.0, memory_mb=4096),
            NetworkDevice(id=7, name="Dev Laptop", device_type=DeviceType.LAPTOP, status=DeviceStatus.ONLINE,
                          ip="192.168.1.51", mac="AA:BB:CC:DD:00:08", vendor="Framework", model="16 AMD", firmware="3.06",
                          uptime_s=now - 1800, x=300, y=500, cpu_usage=28.0, memory_mb=2048),
            NetworkDevice(id=8, name="Printer", device_type=DeviceType.PRINTER, status=DeviceStatus.OFFLINE,
                          ip="192.168.1.250", mac="AA:BB:CC:DD:00:09", vendor="HP", model="LaserJet Pro", firmware="20230401",
                          uptime_s=0, x=500, y=600),
            NetworkDevice(id=9, name="IoT Hub", device_type=DeviceType.IoT, status=DeviceStatus.DEGRADED,
                          ip="192.168.1.254", mac="AA:BB:CC:DD:00:0A", vendor="Home Assistant", model="Yellow", firmware="2024.1",
                          uptime_s=now - 86400 * 5, x=300, y=300, cpu_usage=42.0, memory_mb=512),
            NetworkDevice(id=10, name="Security Camera", device_type=DeviceType.CAMERA, status=DeviceStatus.ONLINE,
                          ip="192.168.1.251", mac="AA:BB:CC:DD:00:0B", vendor="Reolink", model="RLC-810A", firmware="v3.0.0",
                          uptime_s=now - 86400 * 20, x=700, y=200),
        ]

        self._links = [
            NetworkLink(0, 1, LinkType.ETHERNET, LinkSpeed.GBPS_1, bandwidth_used=35, latency_ms=0.5,
                        protocols=[Protocol.OSPF], port_src="GigE0/1", port_dst="eth0"),
            NetworkLink(1, 4, LinkType.ETHERNET, LinkSpeed.GBPS_10, bandwidth_used=62, latency_ms=0.2,
                        port_src="eth1", port_dst="eth0"),
            NetworkLink(1, 5, LinkType.ETHERNET, LinkSpeed.GBPS_1, bandwidth_used=28, latency_ms=0.3),
            NetworkLink(1, 6, LinkType.ETHERNET, LinkSpeed.GBPS_1, bandwidth_used=15, latency_ms=0.4),
            NetworkLink(1, 7, LinkType.WIFI, LinkSpeed.WIFI_6, bandwidth_used=42, latency_ms=2.1,
                        packet_loss=0.01),
            NetworkLink(1, 3, LinkType.ETHERNET, LinkSpeed.GBPS_1, bandwidth_used=55, latency_ms=0.3),
            NetworkLink(3, 7, LinkType.WIFI, LinkSpeed.WIFI_6, bandwidth_used=38, latency_ms=3.2,
                        packet_loss=0.05),
            NetworkLink(1, 9, LinkType.ETHERNET, LinkSpeed.GBPS_1, bandwidth_used=8, latency_ms=1.5),
            NetworkLink(1, 10, LinkType.WIFI, LinkSpeed.WIFI_6, bandwidth_used=72, latency_ms=4.5),
            NetworkLink(0, 2, LinkType.ETHERNET, LinkSpeed.GBPS_1, bandwidth_used=45, latency_ms=0.1,
                        protocols=[Protocol.OSPF, Protocol.BGP]),
        ]

        self._discoveries = [
            DiscoveryResult(9, "192.168.1.254", "AA:BB:CC:DD:00:0A", "Home Assistant", 2.5, now - 60, "arp"),
            DiscoveryResult(7, "192.168.1.51", "AA:BB:CC:DD:00:08", "Framework", 1.2, now - 120, "mdns"),
            DiscoveryResult(10, "192.168.1.251", "AA:BB:CC:DD:00:0B", "Reolink", 5.0, now - 300, "ping"),
        ]

    @property
    def selected_device(self) -> Optional[NetworkDevice]:
        if 0 <= self._selected_device < len(self._devices):
            return self._devices[self._selected_device]
        return None

    @property
    def total_devices(self) -> int:
        return len(self._devices)

    @property
    def online_devices(self) -> int:
        return sum(1 for d in self._devices if d.status == DeviceStatus.ONLINE)

    @property
    def total_links(self) -> int:
        return len(self._links)

    def select_device(self, idx: int):
        if 0 <= idx < len(self._devices):
            self._selected_device = idx

    def handle_input(self, key: str):
        key = key.lower()
        if key == "s":
            self._scan_running = not self._scan_running
        elif key == "t":
            self._view_mode = "topology"
        elif key == "d":
            self._view_mode = "devices"
        elif key == "l":
            self._view_mode = "links"

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS NETWORK TOPOLOGY MAPPER                           ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  Devices: {self.online_devices}/{self.total_devices} online  Links: {self.total_links}  Scan: {'Running' if self._scan_running else 'Stopped'}  Auto: {'ON' if self._auto_discover else 'OFF'}")
        lines.append("")

        # Topology diagram
        lines.append("  ── Topology ──")
        lines.append("                    🌐 Gateway (192.168.1.1)")
        lines.append("                    │")
        lines.append("              ┌─────┴─────┐")
        lines.append("              │           │")
        lines.append("          🛡️ Firewall  🔀 Core Switch")
        lines.append("              │       ┌───┼───┬───┐")
        lines.append("              │       │   │   │   │")
        lines.append("            🌐 ISP  📡AP  💻PC  💻Laptop")
        lines.append("                    │")
        lines.append("                  📱📱📱")
        lines.append("")

        # Devices
        lines.append("  ── Devices ──")
        for i, dev in enumerate(self._devices):
            sel = "▶" if i == self._selected_device else " "
            lines.append(f"  {sel} {dev.status_icon} {dev.type_icon} {dev.name:<20s} {dev.ip:<16s} {dev.vendor} {dev.model}  {dev.uptime_str}")
        lines.append("")

        # Selected device detail
        dev = self.selected_device
        if dev:
            lines.append(f"  ── {dev.name} ({dev.device_type.value}) ──")
            lines.append(f"  Status: {dev.status.value} {dev.status_icon}  IP: {dev.ip}  MAC: {dev.mac}")
            lines.append(f"  Vendor: {dev.vendor}  Model: {dev.model}  Uptime: {dev.uptime_str}")
            lines.append(f"  CPU: [{dev.cpu_bar}] {dev.cpu_usage:.1f}%  RAM: {dev.memory_mb:.0f}MB  Temp: {dev.temperature:.0f}°C")
            if dev.interfaces:
                lines.append(f"  Interfaces:")
                for iface in dev.interfaces:
                    lines.append(f"    {iface.name} {iface.ip} [{iface.speed.value}] {iface.traffic_str} Err:{iface.error_count}")
            lines.append("")

        # Links
        lines.append("  ── Links ──")
        for link in self._links:
            src = next((d.name for d in self._devices if d.id == link.source_id), "?")
            dst = next((d.name for d in self._devices if d.id == link.target_id), "?")
            lines.append(f"  {link.link_type_icon} {src} ↔ {dst}  {link.speed.value}  [{link.bandwidth_bar}] {link.bandwidth_used:.0f}%  Latency: {link.latency_str}  Loss: {link.loss_str}")
        lines.append("")

        # Discoveries
        if self._discoveries:
            lines.append("  ── Recent Discoveries ──")
            for disc in self._discoveries[:3]:
                lines.append(f"  🔍 {disc.time_str} {disc.ip} ({disc.vendor}) via {disc.method} {disc.response_time_ms:.1f}ms")
            lines.append("")

        lines.append("  [S]can [T]opology [D]evices [L]inks [↑↓]Select [P]Ping")
        return lines
