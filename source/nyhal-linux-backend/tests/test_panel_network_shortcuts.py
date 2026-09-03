"""Tests for desktop panel, network manager, and keyboard shortcuts."""

import time
import unittest
from unittest.mock import MagicMock

from ui.desktop_panel import (
    DesktopPanel, TrayIcon, TrayIconType, PinnedApp, RunningApp, PanelTheme,
)
from ui.network_manager import (
    NetworkManager, NetworkType, WifiSecurity, ConnectionState, ProxyMode,
    NetworkInterface, WifiNetwork, VpnConfig, ProxyConfig,
    ConnectionProfile, PingResult, SpeedTestResult, DnsResult,
)
from ui.shortcuts import (
    ShortcutRegistry, Shortcut, ShortcutEvent, Modifier, ShortcutScope,
)


# ---------------------------------------------------------------------------
# DesktopPanel tests
# ---------------------------------------------------------------------------

class TestDesktopPanel(unittest.TestCase):
    """Tests for DesktopPanel."""

    def setUp(self):
        self.panel = DesktopPanel(width=1920, height=48)

    def test_initialization(self):
        self.assertEqual(self.panel.width, 1920)
        self.assertEqual(self.panel.height, 48)
        self.assertEqual(self.panel.position, "bottom")
        self.assertEqual(len(self.panel._tray), 4)
        self.assertEqual(len(self.panel._pinned), 4)

    def test_render_returns_pixels(self):
        pixels = self.panel.render(y=0)
        self.assertIsInstance(pixels, list)
        self.assertTrue(len(pixels) > 0)
        # Each pixel is (x, y, (r, g, b, a))
        x, y, color = pixels[0]
        self.assertEqual(len(color), 4)

    def test_render_dimensions(self):
        pixels = self.panel.render(y=100)
        # Check x ranges
        xs = [p[0] for p in pixels]
        self.assertTrue(min(xs) >= 0)
        self.assertTrue(max(xs) < 1920)
        # Check y ranges
        ys = [p[1] for p in pixels]
        self.assertTrue(min(ys) >= 100)
        self.assertTrue(max(ys) < 148)

    def test_add_running_app(self):
        self.panel.add_running_app("term", "Terminal", (60, 200, 120, 255))
        self.assertEqual(len(self.panel._running), 1)
        self.assertEqual(self.panel._running[0].id, "term")

    def test_remove_running_app(self):
        self.panel.add_running_app("term", "Terminal", (60, 200, 120, 255))
        self.panel.add_running_app("files", "Files", (255, 200, 60, 255))
        self.panel.remove_running_app("term")
        self.assertEqual(len(self.panel._running), 1)
        self.assertEqual(self.panel._running[0].id, "files")

    def test_set_app_active(self):
        self.panel.add_running_app("a", "A", (255, 0, 0, 255))
        self.panel.add_running_app("b", "B", (0, 255, 0, 255))
        self.panel.set_app_active("a")
        self.assertTrue(self.panel._running[0].active)
        self.assertFalse(self.panel._running[1].active)

    def test_pin_unpin_app(self):
        initial = len(self.panel._pinned)
        self.panel.pin_app("calc", "Calculator", (200, 200, 200, 255))
        self.assertEqual(len(self.panel._pinned), initial + 1)
        self.panel.unpin_app("calc")
        self.assertEqual(len(self.panel._pinned), initial)

    def test_set_volume(self):
        self.panel.set_volume(50)
        for icon in self.panel._tray:
            if icon.icon_type == TrayIconType.VOLUME:
                self.assertEqual(icon.value, 50)
                self.assertIn("50", icon.tooltip)

    def test_set_volume_clamped(self):
        self.panel.set_volume(150)
        for icon in self.panel._tray:
            if icon.icon_type == TrayIconType.VOLUME:
                self.assertEqual(icon.value, 100)

    def test_set_battery(self):
        self.panel.set_battery(25)
        for icon in self.panel._tray:
            if icon.icon_type == TrayIconType.BATTERY:
                self.assertEqual(icon.value, 25)
                # 25 is mid (>20), not low
                self.assertEqual(icon.color, PanelTheme.ICON_BATTERY_MID)

    def test_set_battery_full(self):
        self.panel.set_battery(90)
        for icon in self.panel._tray:
            if icon.icon_type == TrayIconType.BATTERY:
                self.assertEqual(icon.color, PanelTheme.ICON_BATTERY_FULL)

    def test_set_network(self):
        self.panel.set_network(True, wifi=True)
        for icon in self.panel._tray:
            if icon.icon_type == TrayIconType.NETWORK:
                self.assertTrue(icon.active)

    def test_set_bluetooth(self):
        self.panel.set_bluetooth(True)
        for icon in self.panel._tray:
            if icon.icon_type == TrayIconType.BLUETOOTH:
                self.assertTrue(icon.active)
                self.assertIn("On", icon.tooltip)

    def test_set_notifications(self):
        self.panel.set_notifications(5)
        self.assertEqual(self.panel._notification_count, 5)

    def test_set_workspace(self):
        self.panel.set_workspace(2)
        self.assertEqual(self.panel._active_workspace, 2)

    def test_set_workspace_clamped(self):
        self.panel.set_workspace(10)
        self.assertEqual(self.panel._active_workspace, 3)

    def test_handle_click_start(self):
        result = self.panel.handle_click(24, 24)
        self.assertEqual(result, "start")

    def test_handle_click_outside(self):
        result = self.panel.handle_click(100, -10)
        self.assertIsNone(result)

    def test_handle_click_clock(self):
        # Clock is at the far right
        result = self.panel.handle_click(self.panel.width - 40, 24)
        self.assertEqual(result, "clock")

    def test_toggle_clock_format(self):
        self.assertTrue(self.panel._clock_24h)
        self.panel._clock_24h = False
        self.assertFalse(self.panel._clock_24h)

    def test_render_calendar(self):
        pixels = self.panel.render_calendar(2026, 9)
        self.assertIsInstance(pixels, list)
        self.assertTrue(len(pixels) > 0)

    def test_to_dict(self):
        d = self.panel.to_dict()
        self.assertIn("width", d)
        self.assertIn("pinned", d)
        self.assertIn("running", d)
        self.assertIn("tray", d)
        self.assertEqual(d["width"], 1920)

    def test_top_position(self):
        panel = DesktopPanel(width=800, height=48, position="top")
        self.assertEqual(panel.position, "top")

    def test_render_to_rgb(self):
        rgb, w, h = self.panel.render_to_rgb()
        self.assertEqual(w, 1920)
        self.assertEqual(h, 48)
        self.assertEqual(len(rgb), w * h * 3)


# ---------------------------------------------------------------------------
# NetworkManager tests
# ---------------------------------------------------------------------------

class TestNetworkManager(unittest.TestCase):
    """Tests for NetworkManager."""

    def setUp(self):
        self.nm = NetworkManager(hostname="test-host")

    def test_initialization(self):
        self.assertEqual(self.nm.hostname, "test-host")
        self.assertTrue(len(self.nm.interfaces) > 0)

    def test_interfaces(self):
        ifaces = self.nm.interfaces
        self.assertTrue(len(ifaces) >= 2)
        types = {i.type for i in ifaces}
        self.assertIn(NetworkType.ETHERNET, types)
        self.assertIn(NetworkType.WIFI, types)

    def test_active_interface(self):
        active = self.nm.active_interface
        self.assertIsNotNone(active)
        self.assertIn("192.168", active.ip)

    def test_get_interface(self):
        eth = self.nm.get_interface("eth0")
        self.assertIsNotNone(eth)
        self.assertEqual(eth.type, NetworkType.ETHERNET)

    def test_get_interface_not_found(self):
        self.assertIsNone(self.nm.get_interface("nonexistent"))

    def test_wifi_networks(self):
        networks = self.nm.wifi_networks
        self.assertTrue(len(networks) > 0)
        # Should be sorted by signal descending
        for i in range(len(networks) - 1):
            self.assertTrue(networks[i].signal >= networks[i + 1].signal)

    def test_wifi_enabled(self):
        self.assertTrue(self.nm.wifi_enabled)

    def test_scan_wifi(self):
        networks = self.nm.scan_wifi()
        self.assertIsInstance(networks, list)
        self.assertTrue(len(networks) > 0)

    def test_connect_wifi(self):
        result = self.nm.connect_wifi("NyrqisHome", "password")
        self.assertTrue(result)
        # Check connected state
        for n in self.nm.wifi_networks:
            if n.ssid == "NyrqisHome":
                self.assertTrue(n.connected)

    def test_connect_wifi_unknown(self):
        result = self.nm.connect_wifi("NonExistent", "pass")
        self.assertFalse(result)

    def test_disconnect_wifi(self):
        self.nm.connect_wifi("NyrqisHome")
        result = self.nm.disconnect_wifi()
        self.assertTrue(result)
        for n in self.nm.wifi_networks:
            self.assertFalse(n.connected)

    def test_forget_wifi(self):
        result = self.nm.forget_wifi("NyrqisHome")
        self.assertTrue(result)
        for n in self.nm.wifi_networks:
            if n.ssid == "NyrqisHome":
                self.assertFalse(n.saved)

    def test_wifi_security_label(self):
        self.assertEqual(self.nm.get_wifi_security_label(WifiSecurity.WPA3), "WPA3")
        self.assertEqual(self.nm.get_wifi_security_label(WifiSecurity.WPA2), "WPA2")
        self.assertEqual(self.nm.get_wifi_security_label(WifiSecurity.NONE), "None")

    def test_vpn_add(self):
        vpn = self.nm.add_vpn("MyVPN", "WireGuard", "vpn.example.com")
        self.assertEqual(vpn.name, "MyVPN")
        self.assertEqual(len(self.nm.vpn_configs), 1)

    def test_vpn_connect_disconnect(self):
        self.nm.add_vpn("TestVPN")
        self.assertTrue(self.nm.connect_vpn("TestVPN"))
        vpn = self.nm.vpn_configs[0]
        self.assertEqual(vpn.state, ConnectionState.CONNECTED)
        self.assertTrue(self.nm.disconnect_vpn("TestVPN"))
        self.assertEqual(vpn.state, ConnectionState.DISCONNECTED)

    def test_vpn_remove(self):
        self.nm.add_vpn("ToRemove")
        self.assertTrue(self.nm.remove_vpn("ToRemove"))
        self.assertEqual(len(self.nm.vpn_configs), 0)

    def test_proxy_mode(self):
        self.nm.set_proxy_mode(ProxyMode.MANUAL)
        self.assertEqual(self.nm.proxy.mode, ProxyMode.MANUAL)

    def test_proxy_settings(self):
        self.nm.set_proxy("proxy.example.com", 3128)
        self.assertEqual(self.nm.proxy.http_host, "proxy.example.com")
        self.assertEqual(self.nm.proxy.http_port, 3128)

    def test_profiles(self):
        profile = self.nm.save_profile("Home WiFi", "wlan0", NetworkType.WIFI)
        self.assertIsNotNone(profile.id)
        self.assertEqual(len(self.nm.profiles), 1)

    def test_delete_profile(self):
        profile = self.nm.save_profile("Test", "wlan0", NetworkType.WIFI)
        self.assertTrue(self.nm.delete_profile(profile.id))
        self.assertEqual(len(self.nm.profiles), 0)

    def test_ping(self):
        result = self.nm.ping("8.8.8.8", count=4)
        self.assertEqual(result.packets_sent, 4)
        self.assertEqual(result.packets_received, 4)
        self.assertEqual(result.loss_percent, 0.0)
        self.assertGreater(result.avg_ms, 0)

    def test_speed_test(self):
        result = self.nm.speed_test()
        self.assertGreater(result.download_mbps, 0)
        self.assertGreater(result.upload_mbps, 0)
        self.assertGreater(result.latency_ms, 0)

    def test_dns_lookup(self):
        result = self.nm.dns_lookup("example.com")
        self.assertEqual(result.hostname, "example.com")
        self.assertTrue(len(result.records) > 0)
        # Should have at least an A record
        a_records = [r for r in result.records if r[0] == "A"]
        self.assertTrue(len(a_records) > 0)

    def test_stats(self):
        stats = self.nm.get_stats()
        self.assertIn("interfaces", stats)
        self.assertIn("wifi_networks", stats)
        self.assertIn("vpn_configs", stats)
        self.assertIn("proxy_mode", stats)

    def test_render(self):
        rgb, w, h = self.nm.render(400, 600)
        self.assertEqual(w, 400)
        self.assertEqual(h, 600)
        self.assertEqual(len(rgb), w * h * 3)

    def test_to_dict(self):
        d = self.nm.to_dict()
        self.assertIn("interfaces", d)
        self.assertIn("wifi_networks", d)


# ---------------------------------------------------------------------------
# ShortcutRegistry tests
# ---------------------------------------------------------------------------

class TestShortcutRegistry(unittest.TestCase):
    """Tests for ShortcutRegistry."""

    def setUp(self):
        self.registry = ShortcutRegistry(app_id="test-app")

    def test_initialization(self):
        self.assertEqual(self.registry.app_id, "test-app")
        self.assertTrue(self.registry.enabled)

    def test_default_shortcuts_registered(self):
        shortcuts = self.registry.all_shortcuts
        self.assertTrue(len(shortcuts) > 10)

    def test_register_shortcut(self):
        s = self.registry.register(
            Modifier.CTRL, "b", "test.action",
            label="Test", category="Test",
        )
        self.assertEqual(s.action, "test.action")
        self.assertEqual(s.modifier, Modifier.CTRL)
        self.assertEqual(s.key, "b")

    def test_register_conflict_raises(self):
        # Ctrl+w is already registered as window.close
        with self.assertRaises(ValueError):
            self.registry.register(
                Modifier.CTRL, "w", "other.action",
                label="Conflict",
            )

    def test_unregister(self):
        self.registry.register(Modifier.ALT, "x", "test.action")
        self.assertTrue(self.registry.unregister(Modifier.ALT, "x"))
        self.assertIsNone(self.registry.get(Modifier.ALT, "x"))

    def test_dispatch_global(self):
        fired = []
        self.registry.on("window.close", lambda: fired.append(True))
        event = self.registry.dispatch(Modifier.CTRL, "w",
                                       scope=ShortcutScope.WINDOW)
        self.assertIsNotNone(event)
        self.assertTrue(event.handled)
        self.assertEqual(event.shortcut.action, "window.close")
        self.assertTrue(len(fired) > 0)

    def test_dispatch_wrong_scope(self):
        event = self.registry.dispatch(Modifier.CTRL, "w",
                                       scope=ShortcutScope.LOCK_SCREEN)
        self.assertIsNone(event)

    def test_dispatch_unknown_key(self):
        event = self.registry.dispatch(Modifier.CTRL, "z",
                                       scope=ShortcutScope.GLOBAL)
        # Ctrl+Z is registered under TEXT scope
        self.assertIsNone(event)

    def test_dispatch_text_scope(self):
        event = self.registry.dispatch(Modifier.CTRL, "z",
                                       scope=ShortcutScope.TEXT)
        self.assertIsNotNone(event)
        self.assertEqual(event.shortcut.action, "edit.undo")

    def test_callback_registration(self):
        fired = []
        self.registry.on("test.action", lambda: fired.append(1))
        self.registry.on("test.action", lambda: fired.append(2))
        # Manually fire
        self.registry._fire_callbacks("test.action")
        self.assertEqual(fired, [1, 2])

    def test_callback_removal(self):
        fired = []
        cb = lambda: fired.append(1)
        self.registry.on("test.action", cb)
        self.registry.off("test.action", cb)
        self.registry._fire_callbacks("test.action")
        self.assertEqual(fired, [])

    def test_override_global(self):
        s = self.registry.override_global(
            Modifier.CTRL, "b", "custom.action", "Custom"
        )
        self.assertEqual(s.action, "custom.action")
        # Override should take priority
        event = self.registry.dispatch(Modifier.CTRL, "b",
                                       scope=ShortcutScope.GLOBAL)
        self.assertIsNotNone(event)
        self.assertEqual(event.shortcut.action, "custom.action")

    def test_override_app(self):
        self.registry.override_app(
            "my-app", Modifier.CTRL, "b", "app.action"
        )
        event = self.registry.dispatch(
            Modifier.CTRL, "b",
            scope=ShortcutScope.APP, app_id="my-app",
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.shortcut.action, "app.action")

    def test_disable_enable(self):
        self.registry.disable()
        event = self.registry.dispatch(Modifier.CTRL, "w",
                                       scope=ShortcutScope.WINDOW)
        self.assertIsNone(event)
        self.registry.enable()
        event = self.registry.dispatch(Modifier.CTRL, "w",
                                       scope=ShortcutScope.WINDOW)
        self.assertIsNotNone(event)

    def test_show_hide_overlay(self):
        self.registry.show_overlay()
        self.assertTrue(self.registry.overlay_visible)
        self.registry.hide_overlay()
        self.assertFalse(self.registry.overlay_visible)

    def test_overlay_shortcuts(self):
        shortcuts = self.registry.get_overlay_shortcuts()
        self.assertTrue(len(shortcuts) > 0)
        # Hidden shortcuts should not appear
        for s in shortcuts:
            self.assertFalse(s.hidden)

    def test_render_overlay(self):
        rgb, w, h = self.registry.render_overlay(800, 600)
        self.assertEqual(w, 800)
        self.assertEqual(h, 600)
        self.assertEqual(len(rgb), w * h * 3)

    def test_check_conflicts(self):
        conflicts = self.registry.check_conflicts()
        # Default shortcuts should have no conflicts
        self.assertEqual(len(conflicts), 0)

    def test_format_shortcut(self):
        s = self.registry.get(Modifier.CTRL, "w")
        self.assertIsNotNone(s)
        formatted = self.registry.format_shortcut(s)
        self.assertIn("Ctrl", formatted)
        self.assertIn("w", formatted)

    def test_format_shortcut_ctrl_shift(self):
        s = Shortcut(
            id="test", modifier=Modifier.CTRL_SHIFT, key="z",
            action="test", label="Test",
        )
        formatted = self.registry.format_shortcut(s)
        self.assertIn("Ctrl+Shift", formatted)

    def test_get_by_action(self):
        shortcuts = self.registry.get_by_action("window.close")
        self.assertTrue(len(shortcuts) >= 1)

    def test_to_dict(self):
        d = self.registry.to_dict()
        self.assertIn("app_id", d)
        self.assertIn("shortcuts", d)
        self.assertTrue(len(d["shortcuts"]) > 10)

    def test_import_overrides(self):
        data = {
            "overrides": {
                "Ctrl:b": {"modifier": "CTRL", "key": "b", "action": "custom"}
            }
        }
        count = self.registry.import_overrides(data)
        self.assertEqual(count, 1)

    def test_scope_global_always_matches(self):
        # Global shortcuts should work from any scope
        event = self.registry.dispatch(Modifier.CTRL, "l",
                                       scope=ShortcutScope.LOCK_SCREEN)
        # Ctrl+L is GLOBAL scope, so it should match
        self.assertIsNotNone(event)
        self.assertEqual(event.shortcut.action, "system.lock")

    def test_categories(self):
        shortcuts = self.registry.all_shortcuts
        categories = {s.category for s in shortcuts}
        self.assertIn("Window", categories)
        self.assertIn("Applications", categories)
        self.assertIn("Workspace", categories)
        self.assertIn("System", categories)


if __name__ == "__main__":
    unittest.main()
