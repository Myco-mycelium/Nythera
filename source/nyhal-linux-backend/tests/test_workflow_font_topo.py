"""Tests for Workflow Builder, Font Manager, and Network Topology."""
import unittest
import time
from ui.workflow_builder import (
    WorkflowBuilder, Workflow, WorkflowNode, WorkflowEdge, WorkflowRun,
    WorkflowVariable, LogEntry,
    TriggerType, ActionType, NodeStatus, LogLevel,
)
from ui.font_manager import (
    FontManager, Font, FontVariant, FontComparison,
    FontType, FontCategory, FontLicense, FontWeight,
)
from ui.network_topo import (
    NetworkTopology, NetworkDevice, NetworkLink, NetworkInterface, DiscoveryResult,
    DeviceType, DeviceStatus, LinkType, LinkSpeed, Protocol,
)


# ==================== WorkflowBuilder Tests ====================

class TestWorkflowNode(unittest.TestCase):
    def test_status_icon(self):
        n = WorkflowNode(0, "T", status=NodeStatus.SUCCESS)
        self.assertEqual(n.status_icon, "✅")

    def test_type_icon_trigger(self):
        n = WorkflowNode(0, "T", "trigger", trigger_type=TriggerType.SCHEDULE)
        self.assertEqual(n.type_icon, "⏰")

    def test_type_icon_action(self):
        n = WorkflowNode(0, "T", "action", action_type=ActionType.NOTIFY)
        self.assertEqual(n.type_icon, "🔔")

    def test_config_summary(self):
        n = WorkflowNode(0, "T", config={"command": "ls -la"})
        self.assertIn("ls", n.config_summary)


class TestWorkflowRun(unittest.TestCase):
    def test_duration(self):
        r = WorkflowRun(1, started_at=time.time() - 10, finished_at=time.time())
        self.assertGreater(r.duration_s, 0)

    def test_progress_bar(self):
        r = WorkflowRun(1, nodes_run=5, nodes_total=10)
        bar = r.progress_bar
        self.assertIn("█", bar)


class TestWorkflow(unittest.TestCase):
    def test_node_count(self):
        w = Workflow(1, "T", nodes=[WorkflowNode(0, "A"), WorkflowNode(1, "B")])
        self.assertEqual(w.node_count, 2)

    def test_trigger_count(self):
        w = Workflow(1, "T", nodes=[
            WorkflowNode(0, "A", "trigger"), WorkflowNode(1, "B", "action")])
        self.assertEqual(w.trigger_count, 1)


class TestWorkflowBuilder(unittest.TestCase):
    def setUp(self):
        self.builder = WorkflowBuilder()

    def test_initial_state(self):
        self.assertGreater(self.builder.total_workflows, 0)

    def test_selected_workflow(self):
        wf = self.builder.selected_workflow
        self.assertIsNotNone(wf)

    def test_select_workflow(self):
        self.builder.select_workflow(1)
        self.assertEqual(self.builder._selected_workflow, 1)

    def test_total_runs(self):
        self.assertGreater(self.builder.total_runs, 0)

    def test_render(self):
        lines = self.builder.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("WORKFLOW" in l for l in lines))


# ==================== FontManager Tests ====================

class TestFontVariant(unittest.TestCase):
    def test_weight_name(self):
        v = FontVariant("T", FontWeight.BOLD)
        self.assertEqual(v.weight_name, "Bold")

    def test_style_str(self):
        v = FontVariant("T", FontWeight.REGULAR, italic=True)
        self.assertIn("Italic", v.style_str)

    def test_size_str(self):
        v = FontVariant("T", file_size=1500)
        self.assertIn("KB", v.size_str)


class TestFont(unittest.TestCase):
    def test_variant_count(self):
        f = Font("Test", variants=[FontVariant("A"), FontVariant("B")])
        self.assertEqual(f.variant_count, 2)

    def test_is_installed(self):
        f = Font("T", installed=True)
        self.assertEqual(f.is_installed, "✅")


class TestFontComparison(unittest.TestCase):
    def test_font_count(self):
        c = FontComparison(fonts=["A", "B", "C"])
        self.assertEqual(c.font_count, 3)


class TestFontManager(unittest.TestCase):
    def setUp(self):
        self.fm = FontManager()

    def test_initial_state(self):
        self.assertGreater(self.fm.total_fonts, 0)
        self.assertGreater(self.fm.installed_fonts, 0)

    def test_select_font(self):
        self.fm.select_font(2)
        self.assertEqual(self.fm._selected_font, 2)

    def test_selected_font(self):
        f = self.fm.selected_font
        self.assertIsNotNone(f)

    def test_toggle_install(self):
        f = self.fm.selected_font
        old = f.installed
        self.fm.toggle_install()
        self.assertNotEqual(f.installed, old)

    def test_render(self):
        lines = self.fm.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("FONT MANAGER" in l for l in lines))


# ==================== NetworkTopology Tests ====================

class TestNetworkInterface(unittest.TestCase):
    def test_traffic_str(self):
        i = NetworkInterface("eth0", rx_bytes=1024*1024, tx_bytes=512*1024)
        ts = i.traffic_str
        self.assertIn("↓", ts)
        self.assertIn("↑", ts)

    def test_error_count(self):
        i = NetworkInterface("eth0", rx_errors=5, tx_errors=3)
        self.assertEqual(i.error_count, 8)


class TestNetworkDevice(unittest.TestCase):
    def test_status_icon(self):
        d = NetworkDevice(0, status=DeviceStatus.ONLINE)
        self.assertEqual(d.status_icon, "🟢")

    def test_type_icon(self):
        d = NetworkDevice(0, device_type=DeviceType.SERVER)
        self.assertEqual(d.type_icon, "🖥")

    def test_uptime_str(self):
        d = NetworkDevice(0, uptime_s=7200)
        self.assertIn("h", d.uptime_str)

    def test_cpu_bar(self):
        d = NetworkDevice(0, cpu_usage=50)
        bar = d.cpu_bar
        self.assertIn("█", bar)


class TestNetworkLink(unittest.TestCase):
    def test_bandwidth_bar(self):
        l = NetworkLink(0, 1, bandwidth_used=65)
        bar = l.bandwidth_bar
        self.assertIn("█", bar)

    def test_latency_str(self):
        l = NetworkLink(0, 1, latency_ms=2.5)
        self.assertIn("ms", l.latency_str)

    def test_link_type_icon(self):
        l = NetworkLink(0, 1, link_type=LinkType.WIFI)
        self.assertEqual(l.link_type_icon, "📡")


class TestNetworkTopology(unittest.TestCase):
    def setUp(self):
        self.topo = NetworkTopology()

    def test_initial_state(self):
        self.assertGreater(self.topo.total_devices, 0)
        self.assertGreater(self.topo.online_devices, 0)

    def test_select_device(self):
        self.topo.select_device(3)
        self.assertEqual(self.topo._selected_device, 3)

    def test_selected_device(self):
        d = self.topo.selected_device
        self.assertIsNotNone(d)

    def test_total_links(self):
        self.assertGreater(self.topo.total_links, 0)

    def test_discoveries(self):
        self.assertGreater(len(self.topo._discoveries), 0)

    def test_render(self):
        lines = self.topo.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("NETWORK TOPOLOGY" in l for l in lines))


class TestDeviceStatus(unittest.TestCase):
    def test_values(self):
        self.assertEqual(DeviceStatus.ONLINE.value, "Online")
        self.assertEqual(DeviceStatus.OFFLINE.value, "Offline")


if __name__ == "__main__":
    unittest.main()
