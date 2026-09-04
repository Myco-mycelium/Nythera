"""
Tests for Bandwidth Monitor and Session Manager.
"""
import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.bandwidth_monitor import (
    BandwidthMonitor, InterfaceStats, AppBandwidth, Connection,
    ProtocolStats, DataUsage, TrafficAlert, BandwidthSample,
    AppCategory, AlertType,
)
from ui.session_manager import (
    SessionManager, SessionSnapshot, AppSession, WorkspaceState,
    SessionTemplate, SessionEvent, WindowInfo,
    AppState, SessionType, WindowState, RestorePriority,
)


# ─── Bandwidth Monitor Tests ──────────────────────────────────────────────


class TestBandwidthSample(unittest.TestCase):
    def test_create(self):
        s = BandwidthSample(time.time(), 1024, 512)
        self.assertEqual(s.rx_bytes, 1024)

    def test_rx_str(self):
        s = BandwidthSample(rx_bytes=1024 * 1024)
        self.assertIn("MB", s.rx_str)

    def test_tx_str(self):
        s = BandwidthSample(tx_bytes=500)
        self.assertIn("B", s.tx_str)

    def test_total_bytes(self):
        s = BandwidthSample(rx_bytes=1000, tx_bytes=500)
        self.assertEqual(s.total_bytes, 1500)


class TestInterfaceStats(unittest.TestCase):
    def test_create(self):
        i = InterfaceStats("eth0", speed_mbps=2500)
        self.assertEqual(i.name, "eth0")

    def test_utilization_bar(self):
        # 12.5 MB/s = 100 Mbps, which is 4% of 2500 Mbps
        # Use a higher rate: 250 Mbps = 31.25 MB/s
        i = InterfaceStats(speed_mbps=2500, rx_rate=31.25 * 1024 ** 2)
        bar = i.utilization_bar
        self.assertIn("█", bar)

    def test_state_icon(self):
        i = InterfaceStats(is_up=True)
        self.assertEqual(i.state_icon, "🟢")


class TestAppBandwidth(unittest.TestCase):
    def test_create(self):
        a = AppBandwidth("Firefox", 1234, AppCategory.BROWSER)
        self.assertEqual(a.name, "Firefox")

    def test_category_icon(self):
        a = AppBandwidth(category=AppCategory.GAMING)
        self.assertEqual(a.category_icon, "🎮")

    def test_bar(self):
        a = AppBandwidth(rx_rate=5 * 1024 ** 2, tx_rate=1 * 1024 ** 2)
        bar = a.bar
        self.assertIn("█", bar)

    def test_endpoint(self):
        a = AppBandwidth(remote_host="1.1.1.1", remote_port=443)
        self.assertEqual(a.endpoint, "1.1.1.1:443")


class TestConnection(unittest.TestCase):
    def test_create(self):
        c = Connection("192.168.1.1", 80, "10.0.0.1", 443, "ESTABLISHED", "TCP", "Firefox")
        self.assertEqual(c.process, "Firefox")

    def test_state_icon(self):
        c = Connection(state="ESTABLISHED")
        self.assertEqual(c.state_icon, "🟢")

    def test_traffic_str(self):
        c = Connection(rx_bytes=1024 * 1024, tx_bytes=512 * 1024)
        ts = c.traffic_str
        self.assertIn("↓", ts)
        self.assertIn("↑", ts)


class TestProtocolStats(unittest.TestCase):
    def test_create(self):
        p = ProtocolStats("TCP", 1024 ** 3, 512 * 1024 ** 3, 10)
        self.assertEqual(p.name, "TCP")

    def test_bar(self):
        p = ProtocolStats("TCP", 10 * 1024 ** 3, 2 * 1024 ** 3, 10)
        bar = p.bar
        self.assertIn("█", bar)


class TestDataUsage(unittest.TestCase):
    def test_create(self):
        d = DataUsage("today", 10 * 1024 ** 3, 2 * 1024 ** 3, 50 * 1024 ** 3)
        self.assertEqual(d.period, "today")

    def test_usage_percent(self):
        d = DataUsage("today", 25 * 1024 ** 3, 0, 100 * 1024 ** 3)
        self.assertAlmostEqual(d.usage_percent, 25.0)


class TestBandwidthMonitor(unittest.TestCase):
    def setUp(self):
        self.mgr = BandwidthMonitor()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.interfaces), 0)
        self.assertGreater(len(self.mgr.app_bandwidth), 0)
        self.assertGreater(len(self.mgr.connections), 0)

    def test_selected_app(self):
        app = self.mgr.selected_app
        self.assertIsNotNone(app)

    def test_select_app(self):
        self.mgr.select_app(2)
        self.assertEqual(self.mgr._selected_app, 2)

    def test_top_downloaders(self):
        top = self.mgr.get_top_downloaders(3)
        self.assertEqual(len(top), 3)
        # First should have highest rx_rate
        self.assertGreaterEqual(top[0].rx_rate, top[1].rx_rate)

    def test_top_uploaders(self):
        top = self.mgr.get_top_uploaders(3)
        self.assertEqual(len(top), 3)

    def test_active_apps(self):
        active = self.mgr.get_active_apps()
        self.assertGreater(len(active), 0)

    def test_active_connections(self):
        active = self.mgr.get_active_connections()
        self.assertGreater(len(active), 0)

    def test_total_rates(self):
        rx = self.mgr.get_total_rx_rate()
        tx = self.mgr.get_total_tx_rate()
        self.assertGreater(rx, 0)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_app, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_app, 0)

    def test_set_view(self):
        self.mgr.set_view("connections")
        self.assertEqual(self.mgr._view_mode, "connections")

    def test_search_apps(self):
        results = self.mgr.search_apps("firefox")
        self.assertGreater(len(results), 0)

    def test_search_connections(self):
        results = self.mgr.search_connections("firefox")
        self.assertGreater(len(results), 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("interfaces", stats)
        self.assertIn("total_connections", stats)


# ─── Session Manager Tests ────────────────────────────────────────────────


class TestWindowInfo(unittest.TestCase):
    def test_create(self):
        w = WindowInfo("Test", "App", 1234, WindowState.NORMAL, 100, 100, 800, 600)
        self.assertEqual(w.title, "Test")

    def test_state_icon(self):
        w = WindowInfo(state=WindowState.TILED_LEFT)
        self.assertEqual(w.state_icon, "◀️")

    def test_display(self):
        w = WindowInfo("My Window", "MyApp", focused=True)
        d = w.display
        self.assertIn("My Window", d)
        self.assertIn("◀️", d)


class TestAppSession(unittest.TestCase):
    def test_create(self):
        a = AppSession("Firefox", "/usr/bin/firefox", AppState.SAVED, 1234)
        self.assertEqual(a.app_name, "Firefox")

    def test_state_icon(self):
        a = AppSession(state=AppState.RUNNING)
        self.assertEqual(a.state_icon, "🟢")

    def test_window_count(self):
        a = AppSession(windows=[WindowInfo("W1"), WindowInfo("W2")])
        self.assertEqual(a.window_count, 2)


class TestWorkspaceState(unittest.TestCase):
    def test_create(self):
        ws = WorkspaceState(0, "Desktop", "wall.png")
        self.assertEqual(ws.name, "Desktop")

    def test_active_icon(self):
        ws = WorkspaceState(is_active=True)
        self.assertEqual(ws.active_icon, "🟢")

    def test_display(self):
        ws = WorkspaceState(1, "Code", window_count=4, is_active=True)
        d = ws.display
        self.assertIn("Code", d)
        self.assertIn("4 windows", d)


class TestSessionSnapshot(unittest.TestCase):
    def test_create(self):
        s = SessionSnapshot(id=1, name="Test", timestamp=time.time())
        self.assertEqual(s.name, "Test")

    def test_type_icon(self):
        s = SessionSnapshot(session_type=SessionType.FULL)
        self.assertEqual(s.type_icon, "🖥️")

    def test_total_windows(self):
        s = SessionSnapshot(apps=[
            AppSession(windows=[WindowInfo("W1"), WindowInfo("W2")]),
            AppSession(windows=[WindowInfo("W3")]),
        ])
        self.assertEqual(s.total_windows, 3)

    def test_total_apps(self):
        s = SessionSnapshot(apps=[AppSession(), AppSession()])
        self.assertEqual(s.total_apps, 2)


class TestSessionManager(unittest.TestCase):
    def setUp(self):
        self.mgr = SessionManager()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.snapshots), 0)
        self.assertGreater(len(self.mgr.templates), 0)

    def test_selected_snapshot(self):
        s = self.mgr.selected_snapshot
        self.assertIsNotNone(s)

    def test_select_snapshot(self):
        self.mgr.select_snapshot(2)
        self.assertEqual(self.mgr._selected_snapshot, 2)

    def test_save_session(self):
        count = len(self.mgr.snapshots)
        s = self.mgr.save_session("Test Save", "Testing")
        self.assertEqual(len(self.mgr.snapshots), count + 1)
        self.assertEqual(s.name, "Test Save")

    def test_restore_session(self):
        result = self.mgr.restore_session(0)
        self.assertTrue(result)

    def test_delete_session(self):
        count = len(self.mgr.snapshots)
        result = self.mgr.delete_session(2)
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.snapshots), count - 1)

    def test_duplicate_session(self):
        count = len(self.mgr.snapshots)
        copy = self.mgr.duplicate_session(0)
        self.assertIsNotNone(copy)
        self.assertEqual(len(self.mgr.snapshots), count + 1)
        self.assertIn("copy", copy.name)

    def test_create_from_template(self):
        count = len(self.mgr.snapshots)
        s = self.mgr.create_from_template(0)
        self.assertIsNotNone(s)
        self.assertEqual(len(self.mgr.snapshots), count + 1)

    def test_toggle_auto_save(self):
        initial = self.mgr._auto_save_enabled
        self.mgr.toggle_auto_save()
        self.assertNotEqual(self.mgr._auto_save_enabled, initial)

    def test_set_auto_save_interval(self):
        self.mgr.set_auto_save_interval(30)
        self.assertEqual(self.mgr._auto_save_interval_min, 30)

    def test_get_full_sessions(self):
        full = self.mgr.get_full_sessions()
        self.assertGreater(len(full), 0)

    def test_get_recent(self):
        recent = self.mgr.get_recent(2)
        self.assertEqual(len(recent), 2)

    def test_search(self):
        results = self.mgr.search("firefox")
        self.assertGreater(len(results), 0)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_snapshot, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_snapshot, 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("snapshots", stats)
        self.assertIn("total_apps", stats)
        self.assertIn("total_windows", stats)


class TestSessionTemplate(unittest.TestCase):
    def test_create(self):
        t = SessionTemplate("Dev", "Code environment", ["code", "terminal"])
        self.assertEqual(t.name, "Dev")

    def test_icon(self):
        t = SessionTemplate(layout="coding")
        self.assertEqual(t.icon, "💻")


class TestSessionEvent(unittest.TestCase):
    def test_create(self):
        e = SessionEvent(time.time(), "save", "Test", "Saved")
        self.assertEqual(e.event_type, "save")

    def test_icon(self):
        e = SessionEvent(event_type="save")
        self.assertEqual(e.icon, "💾")


if __name__ == "__main__":
    unittest.main()
