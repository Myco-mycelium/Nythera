"""
Tests for Update Manager, Network Config Manager, and Log Aggregator.
"""
import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.network_config import (
    NetworkConfigManager, NetworkInterface, WiFiNetwork, VPNConnection,
    ConnectionProfile, QoSRule, BandwidthSample, DNSServer, DNSConfig,
    IPAddress, InterfaceType, InterfaceState, IPMode, VPNProtocol, VPNState,
    FirewallZone, QoSPriority,
)
from ui.update_manager import (
    UpdateManager, PackageUpdate, ChangelogEntry, UpdateHistory, UpdateType, UpdateStatus,
)
from ui.log_aggregator import (
    LogAggregator, LogEntry, LogLevel, LogSource, AlertRule, LogPattern,
)


# ─── Network Config Tests ─────────────────────────────────────────────────


class TestIPAddress(unittest.TestCase):
    def test_create(self):
        ip = IPAddress("192.168.1.100", "255.255.255.0", "192.168.1.1")
        self.assertEqual(ip.address, "192.168.1.100")

    def test_cidr(self):
        ip = IPAddress("192.168.1.100", "255.255.255.0")
        self.assertEqual(ip.cidr, "192.168.1.100/24")

    def test_cidr_no_mask(self):
        ip = IPAddress("10.0.0.1", "")
        self.assertEqual(ip.cidr, "10.0.0.1")


class TestDNSServer(unittest.TestCase):
    def test_create(self):
        dns = DNSServer("1.1.1.1", "Cloudflare", "Cloudflare", latency_ms=8.0)
        self.assertEqual(dns.address, "1.1.1.1")

    def test_status_icon_fast(self):
        dns = DNSServer("1.1.1.1", latency_ms=5.0, healthy=True)
        self.assertEqual(dns.status_icon, "🟢")

    def test_status_icon_medium(self):
        dns = DNSServer("1.1.1.1", latency_ms=30.0, healthy=True)
        self.assertEqual(dns.status_icon, "🟡")

    def test_status_icon_slow(self):
        dns = DNSServer("1.1.1.1", latency_ms=80.0, healthy=True)
        self.assertEqual(dns.status_icon, "🔴")

    def test_status_icon_unhealthy(self):
        dns = DNSServer("1.1.1.1", healthy=False)
        self.assertEqual(dns.status_icon, "❌")

    def test_latency_bar(self):
        dns = DNSServer("1.1.1.1", latency_ms=50.0)
        bar = dns.latency_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_display(self):
        dns = DNSServer("1.1.1.1", "CF", "Cloudflare")
        self.assertIn("CF", dns.display)
        self.assertIn("1.1.1.1", dns.display)


class TestDNSConfig(unittest.TestCase):
    def test_primary(self):
        cfg = DNSConfig(servers=[DNSServer("1.1.1.1"), DNSServer("8.8.8.8")])
        self.assertEqual(cfg.primary.address, "1.1.1.1")

    def test_server_count(self):
        cfg = DNSConfig(servers=[DNSServer("1.1.1.1"), DNSServer("8.8.8.8")])
        self.assertEqual(cfg.server_count, 2)


class TestVPNConnection(unittest.TestCase):
    def test_create(self):
        vpn = VPNConnection("Test-VPN", VPNProtocol.WIREGUARD, "vpn.test.com")
        self.assertEqual(vpn.name, "Test-VPN")

    def test_state_icon(self):
        vpn = VPNConnection(state=VPNState.CONNECTED)
        self.assertEqual(vpn.state_icon, "🟢")

    def test_uptime_str(self):
        vpn = VPNConnection(uptime_s=3660)
        self.assertEqual(vpn.uptime_str, "1h 1m")

    def test_uptime_str_short(self):
        vpn = VPNConnection(uptime_s=90)
        self.assertEqual(vpn.uptime_str, "1m 30s")

    def test_uptime_str_none(self):
        vpn = VPNConnection(uptime_s=0)
        self.assertEqual(vpn.uptime_str, "N/A")

    def test_traffic_str(self):
        vpn = VPNConnection(bytes_sent=1024 * 1024, bytes_received=2 * 1024 * 1024)
        ts = vpn.traffic_str
        self.assertIn("↓", ts)
        self.assertIn("↑", ts)
        self.assertIn("MB", ts)


class TestNetworkInterface(unittest.TestCase):
    def test_create(self):
        iface = NetworkInterface("eth0", "AA:BB:CC:DD:EE:01", InterfaceType.ETHERNET)
        self.assertEqual(iface.name, "eth0")

    def test_icon(self):
        iface = NetworkInterface(iface_type=InterfaceType.WIFI)
        self.assertEqual(iface.icon, "📶")

    def test_state_icon(self):
        iface = NetworkInterface(state=InterfaceState.UP)
        self.assertEqual(iface.state_icon, "🟢")

    def test_speed_str(self):
        iface = NetworkInterface(speed_mbps=2500)
        self.assertIn("Gbps", iface.speed_str)

    def test_speed_str_mbps(self):
        iface = NetworkInterface(speed_mbps=100)
        self.assertIn("Mbps", iface.speed_str)

    def test_traffic_str(self):
        iface = NetworkInterface(rx_bytes=1024 * 1024, tx_bytes=512 * 1024)
        ts = iface.traffic_str
        self.assertIn("↓", ts)
        self.assertIn("↑", ts)

    def test_is_wireless(self):
        iface = NetworkInterface(iface_type=InterfaceType.WIFI)
        self.assertTrue(iface.is_wireless)
        iface2 = NetworkInterface(iface_type=InterfaceType.ETHERNET)
        self.assertFalse(iface2.is_wireless)

    def test_error_rate(self):
        iface = NetworkInterface(rx_packets=1000, tx_packets=1000, rx_errors=10, tx_errors=5)
        self.assertAlmostEqual(iface.error_rate, 0.75, places=2)


class TestWiFiNetwork(unittest.TestCase):
    def test_create(self):
        w = WiFiNetwork("HomeNet", 95, True, "WPA3")
        self.assertEqual(w.ssid, "HomeNet")

    def test_signal_bars_full(self):
        w = WiFiNetwork(signal=90)
        self.assertEqual(w.signal_bars, "▂▄▆█")

    def test_signal_bars_none(self):
        w = WiFiNetwork(signal=5)
        self.assertEqual(w.signal_bars, "░░░░")

    def test_lock_icon(self):
        w = WiFiNetwork(encrypted=True)
        self.assertEqual(w.lock_icon, "🔒")
        w2 = WiFiNetwork(encrypted=False)
        self.assertEqual(w2.lock_icon, "🔓")


class TestQoSRule(unittest.TestCase):
    def test_create(self):
        q = QoSRule("Gaming", "eth0", QoSPriority.HIGHEST, 0, 50000)
        self.assertEqual(q.name, "Gaming")

    def test_bandwidth_str_unlimited(self):
        q = QoSRule(max_bandwidth_kbps=0)
        self.assertEqual(q.bandwidth_str, "Unlimited")

    def test_bandwidth_str_mbps(self):
        q = QoSRule(max_bandwidth_kbps=50000)
        self.assertIn("Mbps", q.bandwidth_str)

    def test_priority_icon(self):
        q = QoSRule(priority=QoSPriority.HIGHEST)
        self.assertEqual(q.priority_icon, "🔴")


class TestConnectionProfile(unittest.TestCase):
    def test_create(self):
        p = ConnectionProfile("Home", "Default home")
        self.assertEqual(p.name, "Home")

    def test_display_default(self):
        p = ConnectionProfile("Home", is_default=True)
        self.assertIn("⭐", p.display)

    def test_display_not_default(self):
        p = ConnectionProfile("Work", is_default=False)
        self.assertNotIn("⭐", p.display)


class TestNetworkConfigManager(unittest.TestCase):
    def setUp(self):
        self.mgr = NetworkConfigManager()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.interfaces), 0)
        self.assertGreater(len(self.mgr.wifi_networks), 0)
        self.assertGreater(len(self.mgr.vpn_connections), 0)
        self.assertGreater(len(self.mgr.profiles), 0)

    def test_selected_interface(self):
        iface = self.mgr.selected_interface
        self.assertIsNotNone(iface)

    def test_select_interface(self):
        self.mgr.select_interface(2)
        self.assertEqual(self.mgr._selected_interface, 2)

    def test_toggle_interface(self):
        self.mgr.toggle_interface(0)
        self.assertEqual(self.mgr.interfaces[0].state, InterfaceState.DOWN)
        self.mgr.toggle_interface(0)
        self.assertEqual(self.mgr.interfaces[0].state, InterfaceState.UP)

    def test_toggle_loopback_fails(self):
        result = self.mgr.toggle_interface(2)  # lo
        self.assertFalse(result)

    def test_set_static_ip(self):
        result = self.mgr.set_static_ip(0, "10.0.0.100", "255.255.255.0", "10.0.0.1")
        self.assertTrue(result)
        self.assertEqual(self.mgr.interfaces[0].ip.address, "10.0.0.100")
        self.assertEqual(self.mgr.interfaces[0].ip.mode, IPMode.STATIC)

    def test_add_dns_server(self):
        count = len(self.mgr.interfaces[0].dns.servers)
        result = self.mgr.add_dns_server(0, DNSServer("9.9.9.9"))
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.interfaces[0].dns.servers), count + 1)

    def test_remove_dns_server(self):
        result = self.mgr.remove_dns_server(0, 0)
        self.assertTrue(result)

    def test_connect_vpn(self):
        result = self.mgr.connect_vpn(1)  # Mullvad
        self.assertTrue(result)
        self.assertEqual(self.mgr.vpn_connections[1].state, VPNState.CONNECTED)

    def test_disconnect_vpn(self):
        self.mgr.connect_vpn(1)
        result = self.mgr.disconnect_vpn(1)
        self.assertTrue(result)
        self.assertEqual(self.mgr.vpn_connections[1].state, VPNState.DISCONNECTED)

    def test_connect_wifi(self):
        result = self.mgr.connect_wifi(2)
        self.assertTrue(result)
        self.assertTrue(self.mgr.wifi_networks[2].connected)

    def test_activate_profile(self):
        result = self.mgr.activate_profile(2)
        self.assertTrue(result)
        self.assertTrue(self.mgr.profiles[2].is_default)
        self.assertFalse(self.mgr.profiles[0].is_default)

    def test_delete_profile(self):
        count = len(self.mgr.profiles)
        result = self.mgr.delete_profile(3)
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.profiles), count - 1)

    def test_toggle_qos_rule(self):
        result = self.mgr.toggle_qos_rule(0)
        self.assertTrue(result)
        self.assertFalse(self.mgr.qos_rules[0].enabled)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_interface, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_interface, 0)

    def test_set_view(self):
        self.mgr.set_view("vpn")
        self.assertEqual(self.mgr._view_mode, "vpn")
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_vpn, 1)

    def test_get_connected(self):
        connected = self.mgr.get_connected_interfaces()
        self.assertGreater(len(connected), 0)

    def test_get_active_vpn(self):
        vpn = self.mgr.get_active_vpn()
        self.assertIsNotNone(vpn)

    def test_get_wifi_connected(self):
        w = self.mgr.get_wifi_connected()
        self.assertIsNotNone(w)

    def test_search_interfaces(self):
        results = self.mgr.search_interfaces("eth")
        self.assertGreater(len(results), 0)

    def test_search_vpn(self):
        results = self.mgr.search_vpn("mullvad")
        self.assertGreater(len(results), 0)

    def test_search_wifi(self):
        results = self.mgr.search_wifi("HomeNet")
        self.assertGreater(len(results), 0)

    def test_export_config(self):
        cfg = self.mgr.export_config()
        self.assertIn("eth0", cfg)
        self.assertIn("VPN", cfg)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("total_interfaces", stats)
        self.assertIn("connected", stats)
        self.assertGreater(stats["total_interfaces"], 0)


# ─── Update Manager Tests ──────────────────────────────────────────────────


class TestUpdateManager(unittest.TestCase):
    def setUp(self):
        self.mgr = UpdateManager()

    def test_initial_state(self):
        self.assertIsNotNone(self.mgr)
        self.assertTrue(hasattr(self.mgr, 'updates') or hasattr(self.mgr, '_updates') or hasattr(self.mgr, 'channels'))


# ─── Log Aggregator Tests ─────────────────────────────────────────────────


class TestLogEntry(unittest.TestCase):
    def test_create(self):
        entry = LogEntry(time.time(), "syslog", LogLevel.INFO, "Test message")
        self.assertEqual(entry.message, "Test message")

    def test_level_icon(self):
        entry = LogEntry(time.time(), "syslog", LogLevel.ERROR, "Error")
        self.assertEqual(entry.level.icon, "❌")


class TestLogAggregator(unittest.TestCase):
    def setUp(self):
        self.agg = LogAggregator()

    def test_initial_state(self):
        self.assertIsNotNone(self.agg)
        self.assertTrue(hasattr(self.agg, 'entries') or hasattr(self.agg, '_entries'))


class TestLogPattern(unittest.TestCase):
    def test_create(self):
        pd = LogPattern("test", "test.*pattern")
        self.assertIsNotNone(pd)


if __name__ == "__main__":
    unittest.main()
