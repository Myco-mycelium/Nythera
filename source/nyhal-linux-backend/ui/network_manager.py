"""NetworkManager — Network connection management UI for Nyrqis.

Provides a complete network management interface:
- WiFi scanning and connection (with signal strength)
- Ethernet connection management
- VPN support
- Proxy settings
- Connection profiles (save/load)
- Network diagnostics (ping, DNS, speed test)
- Apple HIG clean aesthetics

References:
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import hashlib
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class NetworkType(Enum):
    WIFI = auto()
    ETHERNET = auto()
    VPN = auto()
    LOOPBACK = auto()


class WifiSecurity(Enum):
    NONE = auto()
    WEP = auto()
    WPA = auto()
    WPA2 = auto()
    WPA3 = auto()
    ENTERPRISE = auto()


class ConnectionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    FAILED = auto()
    UNAVAILABLE = auto()


class ProxyMode(Enum):
    NONE = auto()
    MANUAL = auto()
    AUTO = auto()
    SYSTEM = auto()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class NetworkInterface:
    """A network interface."""
    id: str
    name: str
    type: NetworkType
    state: ConnectionState = ConnectionState.DISCONNECTED
    mac: str = ""
    ip: str = ""
    gateway: str = ""
    dns: List[str] = field(default_factory=list)
    speed: int = 0  # Mbps
    mtu: int = 1500
    rx_bytes: int = 0
    tx_bytes: int = 0
    driver: str = ""
    auto_connect: bool = True


@dataclass
class WifiNetwork:
    """A discovered WiFi network."""
    ssid: str
    bssid: str = ""
    signal: int = 0  # 0-100
    security: WifiSecurity = WifiSecurity.NONE
    frequency: int = 0  # MHz
    channel: int = 0
    band: str = ""  # "2.4GHz" or "5GHz"
    connected: bool = False
    saved: bool = False
    hidden: bool = False
    speed: int = 0  # max PHY rate in Mbps


@dataclass
class VpnConfig:
    """VPN connection configuration."""
    name: str
    vpn_type: str = "WireGuard"  # WireGuard, OpenVPN, IPSec
    server: str = ""
    port: int = 51820
    interface: str = ""
    state: ConnectionState = ConnectionState.DISCONNECTED
    auto_connect: bool = False
    dns: List[str] = field(default_factory=list)
    routes: List[str] = field(default_factory=list)


@dataclass
class ProxyConfig:
    """Proxy configuration."""
    mode: ProxyMode = ProxyMode.NONE
    http_host: str = ""
    http_port: int = 8080
    https_host: str = ""
    https_port: int = 8080
    socks_host: str = ""
    socks_port: int = 1080
    no_proxy: List[str] = field(default_factory=lambda: [
        "localhost", "127.0.0.1", "::1"
    ])
    pac_url: str = ""


@dataclass
class ConnectionProfile:
    """A saved connection profile."""
    id: str
    name: str
    interface: str
    type: NetworkType
    auto_connect: bool = True
    settings: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PingResult:
    """Result of a ping test."""
    host: str
    packets_sent: int = 0
    packets_received: int = 0
    loss_percent: float = 0.0
    min_ms: float = 0.0
    avg_ms: float = 0.0
    max_ms: float = 0.0


@dataclass
class SpeedTestResult:
    """Result of a speed test."""
    download_mbps: float = 0.0
    upload_mbps: float = 0.0
    latency_ms: float = 0.0
    server: str = ""
    timestamp: float = 0.0


@dataclass
class DnsResult:
    """Result of a DNS lookup."""
    hostname: str
    records: List[Tuple[str, str]] = field(default_factory=list)  # (type, value)
    query_time_ms: float = 0.0
    server: str = ""


# ---------------------------------------------------------------------------
# NetworkManager
# ---------------------------------------------------------------------------

class NetworkManager:
    """Network connection management UI for Nyrqis.

    Provides WiFi scanning, ethernet management, VPN, proxy settings,
    connection profiles, and network diagnostics.

    Parameters
    ----------
    hostname : str
        System hostname for display.
    """

    def __init__(self, hostname: str = "nyrqis"):
        self.hostname = hostname

        # Interfaces
        self._interfaces: List[NetworkInterface] = []
        self._wifi_networks: List[WifiNetwork] = []
        self._active_interface: Optional[str] = None

        # VPN
        self._vpn_configs: List[VpnConfig] = []

        # Proxy
        self._proxy = ProxyConfig()

        # Profiles
        self._profiles: List[ConnectionProfile] = []

        # Diagnostics history
        self._ping_history: List[PingResult] = []
        self._speed_history: List[SpeedTestResult] = []

        # Scanning state
        self._scanning = False
        self._last_scan: float = 0

        # Initialize with simulated interfaces
        self._init_interfaces()

    def _init_interfaces(self) -> None:
        """Initialize simulated network interfaces."""
        self._interfaces = [
            NetworkInterface(
                id="eth0",
                name="eth0",
                type=NetworkType.ETHERNET,
                state=ConnectionState.CONNECTED,
                mac="02:42:ac:11:00:02",
                ip="192.168.1.42",
                gateway="192.168.1.1",
                dns=["8.8.8.8", "8.8.4.4"],
                speed=1000,
                driver="e1000e",
            ),
            NetworkInterface(
                id="wlan0",
                name="wlan0",
                type=NetworkType.WIFI,
                state=ConnectionState.CONNECTED,
                mac="02:42:ac:11:00:03",
                ip="192.168.1.105",
                gateway="192.168.1.1",
                dns=["8.8.8.8", "8.8.4.4"],
                speed=867,
                driver="iwlwifi",
            ),
            NetworkInterface(
                id="lo",
                name="lo",
                type=NetworkType.LOOPBACK,
                state=ConnectionState.CONNECTED,
                ip="127.0.0.1",
                speed=0,
            ),
        ]
        self._active_interface = "eth0"

        # Default WiFi networks
        self._wifi_networks = [
            WifiNetwork("NyrqisHome", signal=95, security=WifiSecurity.WPA3,
                       frequency=5240, channel=48, band="5GHz",
                       connected=True, saved=True, speed=1200),
            WifiNetwork("Nyrqis-5G", signal=82, security=WifiSecurity.WPA2,
                       frequency=5745, channel=149, band="5GHz",
                       saved=True, speed=867),
            WifiNetwork("Neighbor-2.4G", signal=45, security=WifiSecurity.WPA2,
                       frequency=2437, channel=6, band="2.4GHz", speed=150),
            WifiNetwork("CoffeeShop", signal=38, security=WifiSecurity.WPA2,
                       frequency=2412, channel=1, band="2.4GHz", speed=72),
            WifiNetwork("Guest-Network", signal=25, security=WifiSecurity.WPA,
                       frequency=2462, channel=11, band="2.4GHz", speed=54),
            WifiNetwork("HiddenNet", signal=18, security=WifiSecurity.WPA3,
                       hidden=True, speed=300),
        ]

    # -- Interface management -------------------------------------------

    @property
    def interfaces(self) -> List[NetworkInterface]:
        return list(self._interfaces)

    @property
    def active_interface(self) -> Optional[NetworkInterface]:
        if self._active_interface is None:
            return None
        for iface in self._interfaces:
            if iface.id == self._active_interface:
                return iface
        return None

    def get_interface(self, iface_id: str) -> Optional[NetworkInterface]:
        for iface in self._interfaces:
            if iface.id == iface_id:
                return iface
        return None

    # -- WiFi management ------------------------------------------------

    @property
    def wifi_networks(self) -> List[WifiNetwork]:
        return sorted(self._wifi_networks, key=lambda n: -n.signal)

    @property
    def wifi_enabled(self) -> bool:
        return any(i.type == NetworkType.WIFI and i.state != ConnectionState.UNAVAILABLE
                   for i in self._interfaces)

    def scan_wifi(self) -> List[WifiNetwork]:
        """Simulate WiFi scanning."""
        self._scanning = True
        # Simulate signal fluctuation
        for net in self._wifi_networks:
            net.signal = max(5, min(100,
                net.signal + random.randint(-3, 3)))
        self._scanning = False
        self._last_scan = time.time()
        return self.wifi_networks

    def connect_wifi(self, ssid: str, password: str = "") -> bool:
        """Connect to a WiFi network."""
        for net in self._wifi_networks:
            if net.ssid == ssid:
                # Disconnect current WiFi
                for n in self._wifi_networks:
                    n.connected = False
                net.connected = True

                # Update WiFi interface
                for iface in self._interfaces:
                    if iface.type == NetworkType.WIFI:
                        iface.state = ConnectionState.CONNECTED
                        iface.ip = f"192.168.1.{random.randint(100, 200)}"
                return True
        return False

    def disconnect_wifi(self) -> bool:
        """Disconnect from WiFi."""
        for n in self._wifi_networks:
            n.connected = False
        for iface in self._interfaces:
            if iface.type == NetworkType.WIFI:
                iface.state = ConnectionState.DISCONNECTED
                iface.ip = ""
        return True

    def forget_wifi(self, ssid: str) -> bool:
        """Forget a saved WiFi network."""
        for net in self._wifi_networks:
            if net.ssid == ssid:
                net.saved = False
                return True
        return False

    def get_wifi_security_label(self, security: WifiSecurity) -> str:
        """Get display label for WiFi security type."""
        labels = {
            WifiSecurity.NONE: "None",
            WifiSecurity.WEP: "WEP",
            WifiSecurity.WPA: "WPA",
            WifiSecurity.WPA2: "WPA2",
            WifiSecurity.WPA3: "WPA3",
            WifiSecurity.ENTERPRISE: "WPA2-Enterprise",
        }
        return labels.get(security, "Unknown")

    # -- VPN management -------------------------------------------------

    @property
    def vpn_configs(self) -> List[VpnConfig]:
        return list(self._vpn_configs)

    def add_vpn(self, name: str, vpn_type: str = "WireGuard",
                server: str = "", port: int = 51820) -> VpnConfig:
        """Add a VPN configuration."""
        config = VpnConfig(
            name=name, vpn_type=vpn_type, server=server, port=port,
        )
        self._vpn_configs.append(config)
        return config

    def connect_vpn(self, name: str) -> bool:
        """Connect to a VPN."""
        for vpn in self._vpn_configs:
            if vpn.name == name:
                vpn.state = ConnectionState.CONNECTED
                return True
        return False

    def disconnect_vpn(self, name: str) -> bool:
        """Disconnect from a VPN."""
        for vpn in self._vpn_configs:
            if vpn.name == name:
                vpn.state = ConnectionState.DISCONNECTED
                return True
        return False

    def remove_vpn(self, name: str) -> bool:
        """Remove a VPN configuration."""
        before = len(self._vpn_configs)
        self._vpn_configs = [v for v in self._vpn_configs if v.name != name]
        return len(self._vpn_configs) < before

    # -- Proxy ----------------------------------------------------------

    @property
    def proxy(self) -> ProxyConfig:
        return self._proxy

    def set_proxy_mode(self, mode: ProxyMode) -> None:
        self._proxy.mode = mode

    def set_proxy(self, http_host: str = "", http_port: int = 8080,
                  https_host: str = "", https_port: int = 8080) -> None:
        self._proxy.http_host = http_host
        self._proxy.http_port = http_port
        self._proxy.https_host = https_host or http_host
        self._proxy.https_port = https_port or http_port

    # -- Connection profiles --------------------------------------------

    @property
    def profiles(self) -> List[ConnectionProfile]:
        return list(self._profiles)

    def save_profile(self, name: str, interface: str,
                     net_type: NetworkType,
                     auto_connect: bool = True,
                     settings: Optional[Dict[str, Any]] = None) -> ConnectionProfile:
        """Save a connection profile."""
        profile_id = hashlib.md5(
            f"{name}:{interface}:{time.time()}".encode()
        ).hexdigest()[:12]
        profile = ConnectionProfile(
            id=profile_id, name=name, interface=interface,
            type=net_type, auto_connect=auto_connect,
            settings=settings or {},
        )
        self._profiles.append(profile)
        return profile

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a connection profile."""
        before = len(self._profiles)
        self._profiles = [p for p in self._profiles if p.id != profile_id]
        return len(self._profiles) < before

    # -- Diagnostics ----------------------------------------------------

    def ping(self, host: str = "8.8.8.8", count: int = 4) -> PingResult:
        """Run a simulated ping test."""
        result = PingResult(host=host, packets_sent=count)
        latencies = [round(random.uniform(2.0, 50.0), 2) for _ in range(count)]
        result.packets_received = count  # simulate 0% loss
        result.loss_percent = 0.0
        result.min_ms = min(latencies)
        result.avg_ms = round(sum(latencies) / len(latencies), 2)
        result.max_ms = max(latencies)
        self._ping_history.append(result)
        return result

    def speed_test(self) -> SpeedTestResult:
        """Run a simulated speed test."""
        result = SpeedTestResult(
            download_mbps=round(random.uniform(50, 200), 1),
            upload_mbps=round(random.uniform(10, 50), 1),
            latency_ms=round(random.uniform(5, 30), 1),
            server=f"speedtest-{random.randint(1, 10)}.nyrqis.net",
            timestamp=time.time(),
        )
        self._speed_history.append(result)
        return result

    def dns_lookup(self, hostname: str) -> DnsResult:
        """Simulate a DNS lookup."""
        # Generate realistic-looking results
        result = DnsResult(
            hostname=hostname,
            query_time_ms=round(random.uniform(5, 50), 2),
            server="8.8.8.8",
        )
        # Generate a fake IP from the hostname hash
        h = int(hashlib.md5(hostname.encode()).hexdigest()[:8], 16)
        ip = f"{(h >> 24) & 255}.{(h >> 16) & 255}.{(h >> 8) & 255}.{h & 255}"
        result.records.append(("A", ip))
        if not hostname.startswith("192."):
            result.records.append(("AAAA", f"2001:db8::{ip.replace('.', ':')}"))
        result.records.append(("MX", f"mail.{hostname}"))
        return result

    # -- Statistics -----------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get overall network statistics."""
        connected = sum(1 for i in self._interfaces
                        if i.state == ConnectionState.CONNECTED)
        wifi_connected = any(
            n.connected for n in self._wifi_networks
        )
        return {
            "interfaces": len(self._interfaces),
            "connected": connected,
            "wifi_enabled": self.wifi_enabled,
            "wifi_connected": wifi_connected,
            "wifi_networks": len(self._wifi_networks),
            "vpn_configs": len(self._vpn_configs),
            "vpn_active": sum(1 for v in self._vpn_configs
                              if v.state == ConnectionState.CONNECTED),
            "profiles": len(self._profiles),
            "proxy_mode": self._proxy.mode.name,
        }

    # -- Rendering ------------------------------------------------------

    def render(self, width: int = 400, height: int = 600) -> Tuple[bytes, int, int]:
        """Render the network manager UI to an RGB byte buffer.

        Returns (rgb_bytes, width, height).
        """
        buf = bytearray(width * height * 3)
        bg = (30, 30, 40)

        # Fill background
        for i in range(0, len(buf), 3):
            buf[i] = bg[0]
            buf[i + 1] = bg[1]
            buf[i + 2] = bg[2]

        # Draw header bar (48px)
        header_color = (42, 42, 56)
        for y in range(48):
            for x in range(width):
                idx = (y * width + x) * 3
                buf[idx] = header_color[0]
                buf[idx + 1] = header_color[1]
                buf[idx + 2] = header_color[2]

        # Title area placeholder (colored rectangle)
        title_color = (80, 140, 255)
        self._fill_rect(buf, width, 16, 14, 80, 20, title_color)

        # WiFi section header
        section_y = 64
        self._fill_rect(buf, width, 8, section_y, width - 16, 1,
                        (60, 60, 80))

        # WiFi networks list
        y_pos = section_y + 16
        for i, net in enumerate(self.wifi_networks[:6]):
            # Row background on hover/select
            if net.connected:
                self._fill_rect(buf, width, 4, y_pos, width - 8, 56,
                                (42, 42, 56))

            # Signal bars (simplified)
            bar_count = 4
            filled = max(1, round(net.signal / 25))
            for b in range(bar_count):
                bar_h = 4 + b * 4
                bar_x = 16 + b * 6
                bar_y = y_pos + 24 - bar_h // 2
                color = (80, 200, 120) if b < filled else (60, 60, 80)
                self._fill_rect(buf, width, bar_x, bar_y, 4, bar_h, color)

            # SSID placeholder (small colored rect)
            ssid_color = (200, 200, 210) if net.connected else (150, 150, 170)
            self._fill_rect(buf, width, 44, y_pos + 8, 100, 12, ssid_color)

            # Security badge
            sec_colors = {
                WifiSecurity.WPA3: (80, 200, 120),
                WifiSecurity.WPA2: (80, 140, 255),
                WifiSecurity.WPA: (255, 200, 60),
                WifiSecurity.NONE: (150, 150, 170),
            }
            sec_color = sec_colors.get(net.security, (150, 150, 170))
            self._fill_rect(buf, width, 44, y_pos + 26, 28, 10, sec_color)

            y_pos += 64

        return bytes(buf), width, height

    def _fill_rect(self, buf: bytearray, buf_width: int,
                   x: int, y: int, w: int, h: int,
                   color: Tuple[int, int, int]) -> None:
        """Fill a rectangle in an RGB buffer."""
        for dy in range(h):
            for dx in range(w):
                px = x + dx
                py = y + dy
                if 0 <= px < buf_width and 0 <= py < len(buf) // (buf_width * 3):
                    idx = (py * buf_width + px) * 3
                    if idx + 2 < len(buf):
                        buf[idx] = color[0]
                        buf[idx + 1] = color[1]
                        buf[idx + 2] = color[2]

    # -- Serialization --------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "hostname": self.hostname,
            "interfaces": [
                {"id": i.id, "name": i.name, "type": i.type.name,
                 "state": i.state.name, "ip": i.ip}
                for i in self._interfaces
            ],
            "wifi_networks": len(self._wifi_networks),
            "wifi_connected": any(n.connected for n in self._wifi_networks),
            "vpn_count": len(self._vpn_configs),
            "proxy_mode": self._proxy.mode.name,
            "profiles": len(self._profiles),
        }


__all__ = [
    "NetworkManager",
    "NetworkType",
    "WifiSecurity",
    "ConnectionState",
    "ProxyMode",
    "NetworkInterface",
    "WifiNetwork",
    "VpnConfig",
    "ProxyConfig",
    "ConnectionProfile",
    "PingResult",
    "SpeedTestResult",
    "DnsResult",
]
