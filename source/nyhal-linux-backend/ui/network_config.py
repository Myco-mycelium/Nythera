"""
Nyrqis OS - Network Configuration Manager
Interface settings, DNS configuration, VPN controls, and connection profiles.

Features:
- Network interface management (WiFi, Ethernet, Loopback)
- DNS server configuration with health checks
- VPN client controls (WireGuard, OpenVPN, IPSec)
- Connection profiles (Home, Work, Mobile, Public WiFi)
- IP configuration (DHCP, static, SLAAC)
- Firewall zone assignment per interface
- Traffic shaping / QoS controls
- Bandwidth monitoring per interface
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple


class InterfaceType(Enum):
    ETHERNET = "ethernet"
    WIFI = "wifi"
    LOOPBACK = "loopback"
    BRIDGE = "bridge"
    BOND = "bond"
    TUNNEL = "tunnel"


class InterfaceState(Enum):
    UP = "up"
    DOWN = "down"
    CONNECTING = "connecting"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class IPMode(Enum):
    DHCP = "dhcp"
    STATIC = "static"
    SLAAC = "slaac"
    LINK_LOCAL = "link-local"


class VPNProtocol(Enum):
    WIREGUARD = "WireGuard"
    OPENVPN = "OpenVPN"
    IPSEC = "IPSec"
    L2TP = "L2TP"
    SSTP = "SSTP"


class VPNState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"
    RECONNECTING = "reconnecting"


class FirewallZone(Enum):
    PUBLIC = "public"
    HOME = "home"
    WORK = "work"
    TRUSTED = "trusted"
    BLOCKED = "blocked"
    DMZ = "dmz"


class QoSPriority(Enum):
    HIGHEST = "highest"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    LOWEST = "lowest"


INTERFACE_ICONS = {
    InterfaceType.ETHERNET: "🔌",
    InterfaceType.WIFI: "📶",
    InterfaceType.LOOPBACK: "🔁",
    InterfaceType.BRIDGE: "🌉",
    InterfaceType.BOND: "🔗",
    InterfaceType.TUNNEL: "🚇",
}

STATE_ICONS = {
    InterfaceState.UP: "🟢",
    InterfaceState.DOWN: "🔴",
    InterfaceState.CONNECTING: "🟡",
    InterfaceState.DISCONNECTED: "⚫",
    InterfaceState.FAILED: "❌",
}

VPN_STATE_ICONS = {
    VPNState.DISCONNECTED: "🔴",
    VPNState.CONNECTING: "🟡",
    VPNState.CONNECTED: "🟢",
    VPNState.FAILED: "❌",
    VPNState.RECONNECTING: "🔄",
}


# ─── Data Classes ─────────────────────────────────────────────────────────


@dataclass
class IPAddress:
    address: str = ""
    netmask: str = ""
    gateway: str = ""
    mode: IPMode = IPMode.DHCP

    @property
    def cidr(self) -> str:
        if not self.netmask:
            return self.address
        # Convert netmask to CIDR
        parts = self.netmask.split(".")
        if len(parts) == 4:
            cidr = sum(bin(int(p)).count("1") for p in parts)
            return f"{self.address}/{cidr}"
        return self.address


@dataclass
class DNSServer:
    address: str = ""
    label: str = ""
    provider: str = ""
    is_custom: bool = False
    latency_ms: float = 0.0
    healthy: bool = True
    last_check: float = 0.0

    @property
    def status_icon(self) -> str:
        if not self.healthy:
            return "❌"
        if self.latency_ms < 20:
            return "🟢"
        elif self.latency_ms < 50:
            return "🟡"
        return "🔴"

    @property
    def latency_bar(self) -> str:
        ms = min(self.latency_ms, 100)
        filled = int(ms / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def display(self) -> str:
        return f"{self.label} ({self.address})"


@dataclass
class DNSConfig:
    servers: List[DNSServer] = field(default_factory=list)
    search_domains: List[str] = field(default_factory=list)
    dns_over_tls: bool = False
    dnssec: bool = True
    fallback: bool = True

    @property
    def primary(self) -> Optional[DNSServer]:
        return self.servers[0] if self.servers else None

    @property
    def server_count(self) -> int:
        return len(self.servers)


@dataclass
class VPNConnection:
    name: str = ""
    protocol: VPNProtocol = VPNProtocol.WIREGUARD
    server: str = ""
    port: int = 51820
    state: VPNState = VPNState.DISCONNECTED
    ip_address: str = ""
    dns: str = ""
    uptime_s: float = 0.0
    bytes_sent: float = 0.0
    bytes_received: float = 0.0
    split_tunnel: bool = False
    kill_switch: bool = True
    auto_connect: bool = False
    last_connected: float = 0.0
    mtu: int = 1420

    @property
    def state_icon(self) -> str:
        return VPN_STATE_ICONS.get(self.state, "❓")

    @property
    def uptime_str(self) -> str:
        if self.uptime_s == 0:
            return "N/A"
        h = int(self.uptime_s // 3600)
        m = int((self.uptime_s % 3600) // 60)
        s = int(self.uptime_s % 60)
        if h > 0:
            return f"{h}h {m}m"
        elif m > 0:
            return f"{m}m {s}s"
        return f"{s}s"

    @property
    def traffic_str(self) -> str:
        up = self._fmt_bytes(self.bytes_sent)
        down = self._fmt_bytes(self.bytes_received)
        return f"↓{down} ↑{up}"

    @staticmethod
    def _fmt_bytes(b: float) -> str:
        if b < 1024:
            return f"{b:.0f} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 ** 3:
            return f"{b / 1024 ** 2:.1f} MB"
        return f"{b / 1024 ** 3:.2f} GB"


@dataclass
class NetworkInterface:
    name: str = ""
    mac: str = ""
    iface_type: InterfaceType = InterfaceType.ETHERNET
    state: InterfaceState = InterfaceState.UP
    ip: IPAddress = field(default_factory=IPAddress)
    dns: DNSConfig = field(default_factory=DNSConfig)
    firewall_zone: FirewallZone = FirewallZone.HOME
    speed_mbps: int = 0
    mtu: int = 1500
    rx_bytes: float = 0.0
    tx_bytes: float = 0.0
    rx_packets: int = 0
    tx_packets: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    driver: str = ""
    firmware: str = ""
    connected_since: float = 0.0

    @property
    def icon(self) -> str:
        return INTERFACE_ICONS.get(self.iface_type, "❓")

    @property
    def state_icon(self) -> str:
        return STATE_ICONS.get(self.state, "❓")

    @property
    def speed_str(self) -> str:
        if self.speed_mbps >= 1000:
            return f"{self.speed_mbps / 1000:.1f} Gbps"
        return f"{self.speed_mbps} Mbps"

    @property
    def traffic_str(self) -> str:
        up = self._fmt_bytes(self.tx_bytes)
        down = self._fmt_bytes(self.rx_bytes)
        return f"↓{down} ↑{up}"

    @property
    def error_rate(self) -> float:
        total = self.rx_packets + self.tx_packets
        if total == 0:
            return 0.0
        return (self.rx_errors + self.tx_errors) / total * 100

    @property
    def uptime_str(self) -> str:
        if self.connected_since == 0:
            return "N/A"
        delta = time.time() - self.connected_since
        if delta < 3600:
            return f"{delta / 60:.0f}m"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h"
        return f"{delta / 86400:.1f}d"

    @property
    def is_wireless(self) -> bool:
        return self.iface_type == InterfaceType.WIFI

    @staticmethod
    def _fmt_bytes(b: float) -> str:
        if b < 1024:
            return f"{b:.0f} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 ** 3:
            return f"{b / 1024 ** 2:.1f} MB"
        return f"{b / 1024 ** 3:.2f} GB"


@dataclass
class WiFiNetwork:
    ssid: str = ""
    signal: int = 0  # percent
    encrypted: bool = True
    cipher: str = "WPA2"
    frequency: str = ""
    channel: int = 0
    band: str = ""  # 2.4G, 5G, 6G
    connected: bool = False
    saved: bool = False
    hidden: bool = False
    bssid: str = ""
    speed_mbps: int = 0

    @property
    def signal_bars(self) -> str:
        if self.signal >= 80:
            return "▂▄▆█"
        elif self.signal >= 60:
            return "▂▄▆░"
        elif self.signal >= 40:
            return "▂▄░░"
        elif self.signal >= 20:
            return "▂░░░"
        return "░░░░"

    @property
    def lock_icon(self) -> str:
        return "🔒" if self.encrypted else "🔓"


@dataclass
class ConnectionProfile:
    name: str = ""
    description: str = ""
    interfaces: Dict[str, Dict] = field(default_factory=dict)
    dns_servers: List[str] = field(default_factory=list)
    vpn_name: str = ""
    firewall_zone: FirewallZone = FirewallZone.HOME
    auto_activate: bool = False
    priority: int = 0
    is_default: bool = False

    @property
    def display(self) -> str:
        star = " ⭐" if self.is_default else ""
        return f"{self.name}{star}"


@dataclass
class QoSRule:
    name: str = ""
    interface: str = ""
    priority: QoSPriority = QoSPriority.NORMAL
    max_bandwidth_kbps: int = 0
    min_bandwidth_kbps: int = 0
    burst_kbps: int = 0
    enabled: bool = True
    protocol: str = ""
    port: int = 0

    @property
    def bandwidth_str(self) -> str:
        if self.max_bandwidth_kbps == 0:
            return "Unlimited"
        if self.max_bandwidth_kbps >= 1000:
            return f"{self.max_bandwidth_kbps / 1000:.1f} Mbps"
        return f"{self.max_bandwidth_kbps} Kbps"

    @property
    def priority_icon(self) -> str:
        icons = {
            QoSPriority.HIGHEST: "🔴",
            QoSPriority.HIGH: "🟠",
            QoSPriority.NORMAL: "🟢",
            QoSPriority.LOW: "🔵",
            QoSPriority.LOWEST: "⚪",
        }
        return icons.get(self.priority, "❓")


@dataclass
class BandwidthSample:
    timestamp: float = 0.0
    rx_kbps: float = 0.0
    tx_kbps: float = 0.0

    @property
    def rx_str(self) -> str:
        if self.rx_kbps >= 1000:
            return f"{self.rx_kbps / 1000:.1f} Mbps"
        return f"{self.rx_kbps:.0f} Kbps"

    @property
    def tx_str(self) -> str:
        if self.tx_kbps >= 1000:
            return f"{self.tx_kbps / 1000:.1f} Mbps"
        return f"{self.tx_kbps:.0f} Kbps"


# ─── Network Config Manager ───────────────────────────────────────────────


class NetworkConfigManager:
    def __init__(self):
        self.interfaces: List[NetworkInterface] = []
        self.wifi_networks: List[WiFiNetwork] = []
        self.vpn_connections: List[VPNConnection] = []
        self.profiles: List[ConnectionProfile] = []
        self.qos_rules: List[QoSRule] = []
        self.bandwidth_history: Dict[str, List[BandwidthSample]] = {}
        self._selected_interface: int = 0
        self._selected_vpn: int = 0
        self._selected_profile: int = 0
        self._view_mode: str = "interfaces"
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        # Interfaces
        self.interfaces = [
            NetworkInterface(
                name="eth0", mac="AA:BB:CC:DD:EE:01",
                iface_type=InterfaceType.ETHERNET,
                state=InterfaceState.UP,
                ip=IPAddress("192.168.1.100", "255.255.255.0", "192.168.1.1", IPMode.DHCP),
                dns=DNSConfig(
                    servers=[
                        DNSServer("1.1.1.1", "Cloudflare", "Cloudflare", latency_ms=8.2, last_check=now - 60),
                        DNSServer("8.8.8.8", "Google", "Google", latency_ms=12.5, last_check=now - 60),
                        DNSServer("9.9.9.9", "Quad9", "Quad9", latency_ms=15.1, last_check=now - 120),
                    ],
                    search_domains=["home.arpa", "local"],
                    dns_over_tls=True,
                ),
                firewall_zone=FirewallZone.HOME,
                speed_mbps=2500, mtu=1500,
                rx_bytes=15.2 * 1024 ** 3, tx_bytes=3.8 * 1024 ** 3,
                rx_packets=12500000, tx_packets=8200000,
                rx_errors=12, tx_errors=3,
                driver="igc", firmware="Intel I225-V",
                connected_since=now - 86400 * 3,
            ),
            NetworkInterface(
                name="wlan0", mac="AA:BB:CC:DD:EE:02",
                iface_type=InterfaceType.WIFI,
                state=InterfaceState.UP,
                ip=IPAddress("192.168.1.105", "255.255.255.0", "192.168.1.1", IPMode.DHCP),
                dns=DNSConfig(
                    servers=[
                        DNSServer("1.1.1.1", "Cloudflare", "Cloudflare", latency_ms=15.3, last_check=now - 30),
                        DNSServer("8.8.4.4", "Google Alt", "Google", latency_ms=18.7, last_check=now - 30),
                    ],
                    search_domains=["home.arpa"],
                ),
                firewall_zone=FirewallZone.HOME,
                speed_mbps=300, mtu=1500,
                rx_bytes=8.5 * 1024 ** 3, tx_bytes=1.2 * 1024 ** 3,
                rx_packets=5400000, tx_packets=2100000,
                driver="ath11k_pci", firmware="Qualcomm WCN6855",
                connected_since=now - 7200,
            ),
            NetworkInterface(
                name="lo", mac="00:00:00:00:00:00",
                iface_type=InterfaceType.LOOPBACK,
                state=InterfaceState.UP,
                ip=IPAddress("127.0.0.1", "255.0.0.0", "", IPMode.STATIC),
                speed_mbps=10000, mtu=65536,
                driver="loopback",
            ),
            NetworkInterface(
                name="br0", mac="AA:BB:CC:DD:EE:10",
                iface_type=InterfaceType.BRIDGE,
                state=InterfaceState.DOWN,
                ip=IPAddress("", "", "", IPMode.DHCP),
                firewall_zone=FirewallZone.PUBLIC,
                driver="bridge",
            ),
            NetworkInterface(
                name="tun0", mac="AA:BB:CC:DD:EE:20",
                iface_type=InterfaceType.TUNNEL,
                state=InterfaceState.UP,
                ip=IPAddress("10.8.0.2", "255.255.255.0", "10.8.0.1", IPMode.STATIC),
                firewall_zone=FirewallZone.TRUSTED,
                speed_mbps=1000, mtu=1420,
                driver="wireguard",
            ),
        ]

        # WiFi networks
        self.wifi_networks = [
            WiFiNetwork("HomeNet-5G", 95, True, "WPA3", "5240 MHz", 48, "5G", True, True, speed_mbps=300),
            WiFiNetwork("HomeNet", 88, True, "WPA3", "2437 MHz", 6, "2.4G", False, True, speed_mbps=150),
            WiFiNetwork("Neighbor-5G", 42, True, "WPA2", "5180 MHz", 36, "5G", False, True, speed_mbps=200),
            WiFiNetwork("CoffeeShop", 35, True, "WPA2", "2412 MHz", 1, "2.4G", False, False, speed_mbps=50),
            WiFiNetwork("GuestNet", 28, True, "WPA2", "5745 MHz", 149, "5G", False, False, speed_mbps=100),
            WiFiNetwork("IoT-Net", 65, True, "WPA2", "2437 MHz", 6, "2.4G", False, True, speed_mbps=30),
            WiFiNetwork("HiddenNet", 15, True, "WPA2", "2462 MHz", 11, "2.4G", False, False, hidden=True, speed_mbps=10),
        ]

        # VPN connections
        self.vpn_connections = [
            VPNConnection(
                name="Nyrqis-Office", protocol=VPNProtocol.WIREGUARD,
                server="vpn.nyrqis.dev", port=51820,
                state=VPNState.CONNECTED,
                ip_address="10.100.0.5", dns="10.100.0.1",
                uptime_s=7200, bytes_sent=256 * 1024 ** 2, bytes_received=1.2 * 1024 ** 3,
                kill_switch=True, auto_connect=True, mtu=1420,
                last_connected=now - 7200,
            ),
            VPNConnection(
                name="Mullvad-VPN", protocol=VPNProtocol.WIREGUARD,
                server="us-ewr-001.mullvad.net", port=51820,
                state=VPNState.DISCONNECTED,
                kill_switch=True, split_tunnel=True, mtu=1280,
            ),
            VPNConnection(
                name="Work-IPSec", protocol=VPNProtocol.IPSEC,
                server="corp.nyrqis.dev", port=500,
                state=VPNState.DISCONNECTED,
                kill_switch=False, mtu=1400,
            ),
            VPNConnection(
                name="Personal-OpenVPN", protocol=VPNProtocol.OPENVPN,
                server="personal.example.com", port=1194,
                state=VPNState.FAILED,
                last_connected=now - 86400,
                kill_switch=True,
            ),
        ]

        # Connection profiles
        self.profiles = [
            ConnectionProfile(
                name="Home Network", description="Default home WiFi/Ethernet",
                interfaces={"eth0": {"dhcp": True}, "wlan0": {"dhcp": True}},
                dns_servers=["1.1.1.1", "8.8.8.8"],
                firewall_zone=FirewallZone.HOME, is_default=True,
                auto_activate=True, priority=100,
            ),
            ConnectionProfile(
                name="Office VPN", description="Corporate network via WireGuard",
                interfaces={"tun0": {"dhcp": False, "ip": "10.100.0.5"}},
                dns_servers=["10.100.0.1", "10.100.0.2"],
                vpn_name="Nyrqis-Office",
                firewall_zone=FirewallZone.WORK, priority=90,
            ),
            ConnectionProfile(
                name="Public WiFi", description="Restrictive rules for hotspots",
                interfaces={"wlan0": {"dhcp": True}},
                dns_servers=["1.1.1.1", "8.8.8.8"],
                firewall_zone=FirewallZone.PUBLIC, priority=50,
            ),
            ConnectionProfile(
                name="Mobile Tethering", description="Phone USB tethering",
                interfaces={"usb0": {"dhcp": True}},
                dns_servers=["1.1.1.1"],
                firewall_zone=FirewallZone.PUBLIC, priority=40,
            ),
            ConnectionProfile(
                name="Gaming (Low Latency)", description="Prioritize gaming traffic",
                interfaces={"eth0": {"dhcp": True}},
                dns_servers=["1.1.1.1", "8.8.8.8"],
                firewall_zone=FirewallZone.HOME, priority=95,
            ),
        ]

        # QoS rules
        self.qos_rules = [
            QoSRule("Gaming Priority", "eth0", QoSPriority.HIGHEST, 0, 50000, 100000, True, "UDP", 3478),
            QoSRule("VoIP Priority", "eth0", QoSPriority.HIGH, 512, 256, 1024, True, "UDP", 0),
            QoSRule("Video Conferencing", "wlan0", QoSPriority.HIGH, 10000, 5000, 20000, True, "UDP", 0),
            QoSRule("Web Browsing", "eth0", QoSPriority.NORMAL, 50000, 0, 0, True, "TCP", 80),
            QoSRule("File Downloads", "eth0", QoSPriority.LOW, 20000, 0, 0, True, "TCP", 0),
            QoSRule("System Updates", "wlan0", QoSPriority.LOWEST, 5000, 0, 0, True, "TCP", 0),
        ]

        # Bandwidth history
        for iface_name in ["eth0", "wlan0"]:
            samples = []
            for i in range(30):
                samples.append(BandwidthSample(
                    timestamp=now - (30 - i) * 60,
                    rx_kbps=5000 + (i * 200) + (i % 5 * 300),
                    tx_kbps=1000 + (i * 100) + (i % 3 * 200),
                ))
            self.bandwidth_history[iface_name] = samples

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_interface(self) -> Optional[NetworkInterface]:
        if 0 <= self._selected_interface < len(self.interfaces):
            return self.interfaces[self._selected_interface]
        return None

    @property
    def selected_vpn(self) -> Optional[VPNConnection]:
        if 0 <= self._selected_vpn < len(self.vpn_connections):
            return self.vpn_connections[self._selected_vpn]
        return None

    @property
    def selected_profile(self) -> Optional[ConnectionProfile]:
        if 0 <= self._selected_profile < len(self.profiles):
            return self.profiles[self._selected_profile]
        return None

    def select_interface(self, idx: int):
        if 0 <= idx < len(self.interfaces):
            self._selected_interface = idx

    def select_vpn(self, idx: int):
        if 0 <= idx < len(self.vpn_connections):
            self._selected_vpn = idx

    def select_profile(self, idx: int):
        if 0 <= idx < len(self.profiles):
            self._selected_profile = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        if self._view_mode == "interfaces":
            self._selected_interface = min(self._selected_interface + 1, len(self.interfaces) - 1)
        elif self._view_mode == "vpn":
            self._selected_vpn = min(self._selected_vpn + 1, len(self.vpn_connections) - 1)
        elif self._view_mode == "profiles":
            self._selected_profile = min(self._selected_profile + 1, len(self.profiles) - 1)

    def select_up(self):
        if self._view_mode == "interfaces":
            self._selected_interface = max(self._selected_interface - 1, 0)
        elif self._view_mode == "vpn":
            self._selected_vpn = max(self._selected_vpn - 1, 0)
        elif self._view_mode == "profiles":
            self._selected_profile = max(self._selected_profile - 1, 0)

    # ─── Interface Actions ──────────────────────────────────────────────

    def toggle_interface(self, idx: int = -1) -> bool:
        i = idx if idx >= 0 else self._selected_interface
        if 0 <= i < len(self.interfaces):
            iface = self.interfaces[i]
            if iface.iface_type == InterfaceType.LOOPBACK:
                return False
            if iface.state == InterfaceState.UP:
                iface.state = InterfaceState.DOWN
                iface.rx_bytes = 0
                iface.tx_bytes = 0
            else:
                iface.state = InterfaceState.UP
                iface.connected_since = time.time()
            return True
        return False

    def set_ip_mode(self, idx: int, mode: IPMode) -> bool:
        if 0 <= idx < len(self.interfaces):
            self.interfaces[idx].ip.mode = mode
            return True
        return False

    def set_static_ip(self, idx: int, address: str, netmask: str, gateway: str) -> bool:
        if 0 <= idx < len(self.interfaces):
            iface = self.interfaces[idx]
            iface.ip.address = address
            iface.ip.netmask = netmask
            iface.ip.gateway = gateway
            iface.ip.mode = IPMode.STATIC
            return True
        return False

    def add_dns_server(self, idx: int, server: DNSServer) -> bool:
        if 0 <= idx < len(self.interfaces):
            self.interfaces[idx].dns.servers.append(server)
            return True
        return False

    def remove_dns_server(self, iface_idx: int, dns_idx: int) -> bool:
        if 0 <= iface_idx < len(self.interfaces):
            servers = self.interfaces[iface_idx].dns.servers
            if 0 <= dns_idx < len(servers):
                servers.pop(dns_idx)
                return True
        return False

    # ─── VPN Actions ───────────────────────────────────────────────────

    def connect_vpn(self, idx: int = -1) -> bool:
        i = idx if idx >= 0 else self._selected_vpn
        if 0 <= i < len(self.vpn_connections):
            vpn = self.vpn_connections[i]
            if vpn.state in (VPNState.DISCONNECTED, VPNState.FAILED):
                vpn.state = VPNState.CONNECTED
                vpn.uptime_s = 0
                vpn.bytes_sent = 0
                vpn.bytes_received = 0
                vpn.last_connected = time.time()
                return True
        return False

    def disconnect_vpn(self, idx: int = -1) -> bool:
        i = idx if idx >= 0 else self._selected_vpn
        if 0 <= i < len(self.vpn_connections):
            vpn = self.vpn_connections[i]
            if vpn.state == VPNState.CONNECTED:
                vpn.state = VPNState.DISCONNECTED
                vpn.uptime_s = 0
                return True
        return False

    def toggle_vpn_kill_switch(self, idx: int = -1) -> bool:
        i = idx if idx >= 0 else self._selected_vpn
        if 0 <= i < len(self.vpn_connections):
            self.vpn_connections[i].kill_switch = not self.vpn_connections[i].kill_switch
            return True
        return False

    # ─── Profile Actions ───────────────────────────────────────────────

    def activate_profile(self, idx: int = -1) -> bool:
        i = idx if idx >= 0 else self._selected_profile
        if 0 <= i < len(self.profiles):
            for p in self.profiles:
                p.is_default = False
            self.profiles[i].is_default = True
            self.profiles[i].auto_activate = True
            return True
        return False

    def delete_profile(self, idx: int) -> bool:
        if 0 <= idx < len(self.profiles) and len(self.profiles) > 1:
            self.profiles.pop(idx)
            if self._selected_profile >= len(self.profiles):
                self._selected_profile = len(self.profiles) - 1
            return True
        return False

    # ─── QoS ───────────────────────────────────────────────────────────

    def toggle_qos_rule(self, idx: int) -> bool:
        if 0 <= idx < len(self.qos_rules):
            self.qos_rules[idx].enabled = not self.qos_rules[idx].enabled
            return True
        return False

    # ─── WiFi ──────────────────────────────────────────────────────────

    def connect_wifi(self, idx: int) -> bool:
        if 0 <= idx < len(self.wifi_networks):
            for w in self.wifi_networks:
                w.connected = False
            self.wifi_networks[idx].connected = True
            self.wifi_networks[idx].saved = True
            return True
        return False

    def disconnect_wifi(self) -> bool:
        for w in self.wifi_networks:
            w.connected = False
        return True

    # ─── Stats ─────────────────────────────────────────────────────────

    def get_connected_interfaces(self) -> List[NetworkInterface]:
        return [i for i in self.interfaces if i.state == InterfaceState.UP]

    def get_active_vpn(self) -> Optional[VPNConnection]:
        return next((v for v in self.vpn_connections if v.state == VPNState.CONNECTED), None)

    def get_active_profile(self) -> Optional[ConnectionProfile]:
        return next((p for p in self.profiles if p.is_default), None)

    def get_wifi_connected(self) -> Optional[WiFiNetwork]:
        return next((w for w in self.wifi_networks if w.connected), None)

    def get_stats(self) -> Dict:
        connected = len(self.get_connected_interfaces())
        active_vpn = 1 if self.get_active_vpn() else 0
        total_rx = sum(i.rx_bytes for i in self.interfaces)
        total_tx = sum(i.tx_bytes for i in self.interfaces)
        return {
            "total_interfaces": len(self.interfaces),
            "connected": connected,
            "wifi_networks": len(self.wifi_networks),
            "vpn_connections": len(self.vpn_connections),
            "active_vpn": active_vpn,
            "profiles": len(self.profiles),
            "qos_rules": len(self.qos_rules),
            "total_rx_bytes": total_rx,
            "total_tx_bytes": total_tx,
        }

    # ─── Search ────────────────────────────────────────────────────────

    def search_interfaces(self, query: str) -> List[NetworkInterface]:
        q = query.lower()
        return [i for i in self.interfaces if q in i.name.lower() or q in i.driver.lower()]

    def search_vpn(self, query: str) -> List[VPNConnection]:
        q = query.lower()
        return [v for v in self.vpn_connections if q in v.name.lower() or q in v.server.lower()]

    def search_wifi(self, query: str) -> List[WiFiNetwork]:
        q = query.lower()
        return [w for w in self.wifi_networks if q in w.ssid.lower()]

    # ─── Export ─────────────────────────────────────────────────────────

    def export_config(self) -> str:
        lines = ["# Nyrqis Network Configuration Export", f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
        for iface in self.interfaces:
            lines.append(f"[{iface.name}]")
            lines.append(f"  Type: {iface.iface_type.value}")
            lines.append(f"  State: {iface.state.value}")
            lines.append(f"  MAC: {iface.mac}")
            if iface.ip.address:
                lines.append(f"  IP: {iface.ip.cidr}")
            if iface.ip.gateway:
                lines.append(f"  Gateway: {iface.ip.gateway}")
            lines.append(f"  DNS:")
            for dns in iface.dns.servers:
                lines.append(f"    - {dns.address} ({dns.label})")
            lines.append(f"  Zone: {iface.firewall_zone.value}")
            lines.append(f"  MTU: {iface.mtu}")
            lines.append("")

        if self.vpn_connections:
            lines.append("# VPN Connections")
            for vpn in self.vpn_connections:
                lines.append(f"[vpn:{vpn.name}]")
                lines.append(f"  Protocol: {vpn.protocol.value}")
                lines.append(f"  Server: {vpn.server}:{vpn.port}")
                lines.append(f"  State: {vpn.state.value}")
                lines.append(f"  Kill Switch: {vpn.kill_switch}")
                lines.append("")

        return "\n".join(lines)
