import unittest
import time


class TestTrafficAnalyzer(unittest.TestCase):
    def setUp(self):
        from ui.traffic_analyzer import TrafficAnalyzer, Protocol
        self.ta = TrafficAnalyzer()
        self.Proto = Protocol

    def test_initial_state(self):
        self.assertGreater(len(self.ta.protocol_stats), 0)
        self.assertGreater(len(self.ta.flows), 0)
        self.assertGreater(len(self.ta.bandwidth_history), 0)
        self.assertGreater(len(self.ta.geo_locations), 0)

    def test_get_protocol_stats(self):
        stats = self.ta.get_protocol_stats()
        self.assertGreater(len(stats), 0)
        self.assertGreaterEqual(stats[0].total_bytes, stats[-1].total_bytes)

    def test_get_flows_by_protocol(self):
        flows = self.ta.get_flows_by_protocol(self.Proto.HTTPS)
        self.assertGreater(len(flows), 0)

    def test_get_flows_by_process(self):
        flows = self.ta.get_flows_by_process("firefox")
        self.assertGreater(len(flows), 0)

    def test_search_flows(self):
        results = self.ta.search_flows("ssh")
        self.assertGreater(len(results), 0)

    def test_get_top_flows(self):
        top = self.ta.get_top_flows(3)
        self.assertEqual(len(top), 3)

    def test_get_bandwidth_summary(self):
        summary = self.ta.get_bandwidth_summary()
        self.assertIn("rx_rate", summary)
        self.assertIn("tx_rate", summary)

    def test_get_stats(self):
        stats = self.ta.get_stats()
        self.assertIn("protocols", stats)
        self.assertIn("flows", stats)

    def test_protocol_stats_total_display(self):
        from ui.traffic_analyzer import ProtocolStats
        ps = ProtocolStats(rx_bytes=500, tx_bytes=300)
        self.assertEqual(ps.total_display, "800 B")
        ps.rx_bytes = 2048
        ps.tx_bytes = 1024
        self.assertIn("KB", ps.total_display)

    def test_protocol_stats_icon(self):
        from ui.traffic_analyzer import ProtocolStats, Protocol
        ps = ProtocolStats(protocol=Protocol.HTTPS)
        self.assertEqual(ps.protocol_icon, "🔒")

    def test_flow_direction_icon(self):
        from ui.traffic_analyzer import TrafficFlow, TrafficDirection
        f = TrafficFlow(direction=TrafficDirection.INBOUND)
        self.assertEqual(f.direction_icon, "⬇️")


class TestDiskHealth(unittest.TestCase):
    def setUp(self):
        from ui.disk_health import DiskHealthMonitor, DiskType
        self.dhm = DiskHealthMonitor()
        self.DT = DiskType

    def test_initial_state(self):
        self.assertGreater(len(self.dhm.disks), 0)
        self.assertGreater(len(self.dhm.alerts), 0)

    def test_get_disk(self):
        disk = self.dhm.get_disk("/dev/nvme0n1")
        self.assertIsNotNone(disk)
        self.assertEqual(disk.model, "Samsung 990 Pro 2TB")

    def test_get_nvme_disks(self):
        nvme = self.dhm.get_nvme_disks()
        self.assertGreater(len(nvme), 0)

    def test_get_ssd_disks(self):
        ssd = self.dhm.get_ssd_disks()
        self.assertGreater(len(ssd), 0)

    def test_get_hdd_disks(self):
        hdd = self.dhm.get_hdd_disks()
        self.assertGreater(len(hdd), 0)

    def test_get_worst_disk(self):
        worst = self.dhm.get_worst_disk()
        self.assertIsNotNone(worst)

    def test_get_total_capacity(self):
        total = self.dhm.get_total_capacity()
        self.assertGreater(total, 0)

    def test_get_stats(self):
        stats = self.dhm.get_stats()
        self.assertIn("disks", stats)
        self.assertIn("total_capacity_gb", stats)

    def test_disk_health_bar(self):
        from ui.disk_health import DiskHealth
        d = DiskHealth(health_percent=95)
        bar = d.health_bar
        self.assertEqual(len(bar), 20)

    def test_disk_health_status(self):
        from ui.disk_health import DiskHealth
        d = DiskHealth(health_percent=95)
        self.assertIn("🟢", d.health_status)
        d.health_percent = 60
        self.assertIn("🟠", d.health_status)

    def test_disk_temp_status(self):
        from ui.disk_health import DiskHealth
        d = DiskHealth(temperature_c=35)
        self.assertIn("🟢", d.temp_status)
        d.temperature_c = 60
        self.assertIn("🟠", d.temp_status)

    def test_disk_failure_risk(self):
        from ui.disk_health import DiskHealth
        d = DiskHealth(uncorrectable_errors=0, reallocated_sectors=5)
        self.assertIn("🟢", d.failure_risk)
        d.uncorrectable_errors = 15
        self.assertIn("🔴", d.failure_risk)

    def test_smart_attribute_status(self):
        from ui.disk_health import SMARTAttribute
        a = SMARTAttribute(value=100, worst=100)
        self.assertEqual(a.status_icon, "🟢")
        a.failed = True
        self.assertEqual(a.status_icon, "🔴")


class TestVirtualAssistant(unittest.TestCase):
    def setUp(self):
        from ui.virtual_assistant import VirtualAssistant, AssistantMode
        self.va = VirtualAssistant()
        self.AM = AssistantMode

    def test_initial_state(self):
        self.assertGreater(len(self.va.conversation), 0)
        self.assertGreater(len(self.va.reminders), 0)
        self.assertGreater(len(self.va.quick_actions), 0)

    def test_send_message(self):
        initial = len(self.va.conversation)
        response = self.va.send_message("Hello!")
        self.assertIn("Hello", response)
        self.assertEqual(len(self.va.conversation), initial + 2)

    def test_send_command(self):
        response = self.va.send_message("What's the status?")
        self.assertIn("CPU", response)

    def test_add_reminder(self):
        reminder = self.va.add_reminder("Test", time.time() + 3600, priority="high")
        self.assertEqual(reminder.title, "Test")
        self.assertIn(reminder, self.va.reminders)

    def test_complete_reminder(self):
        result = self.va.complete_reminder(1)
        self.assertTrue(result)
        self.assertTrue(self.va.reminders[0].completed)

    def test_delete_reminder(self):
        result = self.va.delete_reminder(1)
        self.assertTrue(result)

    def test_get_pending_reminders(self):
        pending = self.va.get_pending_reminders()
        self.assertGreater(len(pending), 0)

    def test_execute_quick_action(self):
        result = self.va.execute_quick_action("System Status")
        self.assertIsNotNone(result)
        self.assertEqual(result, "/status")

    def test_search_actions(self):
        results = self.va.search_actions("terminal")
        self.assertGreater(len(results), 0)

    def test_get_conversation_history(self):
        history = self.va.get_conversation_history(5)
        self.assertEqual(len(history), 5)

    def test_get_stats(self):
        stats = self.va.get_stats()
        self.assertIn("messages", stats)
        self.assertIn("reminders", stats)

    def test_reminder_time_until(self):
        from ui.virtual_assistant import Reminder
        r = Reminder(due_time=time.time() - 100)
        self.assertEqual(r.time_until, "Overdue")
        r.due_time = time.time() + 30
        self.assertIn("s", r.time_until)

    def test_reminder_priority_icon(self):
        from ui.virtual_assistant import Reminder
        r = Reminder(priority="urgent")
        self.assertEqual(r.priority_icon, "🔴")

    def test_quick_action_shortcut(self):
        from ui.virtual_assistant import QuickAction
        a = QuickAction(shortcut="Ctrl+T")
        self.assertIn("Ctrl+T", a.shortcut_display)


if __name__ == "__main__":
    unittest.main()
