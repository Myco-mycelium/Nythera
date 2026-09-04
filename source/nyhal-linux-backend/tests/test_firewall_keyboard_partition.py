import unittest
import time


class TestFirewallManager(unittest.TestCase):
    def setUp(self):
        from ui.firewall_manager import FirewallManager, RuleAction, Zone
        self.fm = FirewallManager()
        self.RA = RuleAction
        self.Zone = Zone

    def test_initial_state(self):
        self.assertGreater(len(self.fm.rules), 0)
        self.assertGreater(len(self.fm.zones), 0)
        self.assertTrue(self.fm.enabled)

    def test_add_rule(self):
        from ui.firewall_manager import FirewallRule, Protocol, Direction, RulePriority
        rule = FirewallRule(name="Test Rule", action=self.RA.DROP,
                             direction=Direction.INBOUND, protocol=Protocol.TCP,
                             dest_port=9999, zone=self.Zone.PUBLIC,
                             priority=RulePriority.NORMAL)
        self.fm.add_rule(rule)
        self.assertIn(rule, self.fm.rules)

    def test_remove_rule(self):
        result = self.fm.remove_rule("Allow SSH")
        self.assertTrue(result)
        self.assertFalse(next((r for r in self.fm.rules if r.name == "Allow SSH"), None))

    def test_remove_rule_not_found(self):
        result = self.fm.remove_rule("Nonexistent")
        self.assertFalse(result)

    def test_toggle_rule(self):
        rule = next(r for r in self.fm.rules if r.name == "Allow SSH")
        initial = rule.enabled
        self.fm.toggle_rule("Allow SSH")
        self.assertNotEqual(rule.enabled, initial)

    def test_get_rules_for_zone(self):
        rules = self.fm.get_rules_for_zone(self.Zone.PUBLIC)
        self.assertGreater(len(rules), 0)
        for r in rules:
            self.assertEqual(r.zone, self.Zone.PUBLIC)

    def test_get_enabled_rules(self):
        enabled = self.fm.get_enabled_rules()
        self.assertGreater(len(enabled), 0)
        for r in enabled:
            self.assertTrue(r.enabled)

    def test_search_rules(self):
        results = self.fm.search_rules("ssh")
        self.assertGreater(len(results), 0)

    def test_get_zone(self):
        zone = self.fm.get_zone(self.Zone.PUBLIC)
        self.assertIsNotNone(zone)
        self.assertEqual(zone.name, self.Zone.PUBLIC)

    def test_get_traffic_summary(self):
        summary = self.fm.get_traffic_summary()
        self.assertIn("total_bytes", summary)
        self.assertIn("rules", summary)

    def test_get_recent_traffic(self):
        traffic = self.fm.get_recent_traffic(5)
        self.assertEqual(len(traffic), 5)

    def test_rule_action_icon(self):
        from ui.firewall_manager import FirewallRule
        r = FirewallRule(name="test", action=self.RA.ACCEPT)
        self.assertEqual(r.action_icon, "✅")
        r.action = self.RA.DROP
        self.assertEqual(r.action_icon, "🚫")

    def test_rule_port_display(self):
        from ui.firewall_manager import FirewallRule
        r = FirewallRule(name="test", dest_port=80)
        self.assertEqual(r.port_display, "80")
        r.port_range = "1-1024"
        self.assertEqual(r.port_display, "1-1024")

    def test_traffic_stats_drop_rate(self):
        from ui.firewall_manager import TrafficStats, Protocol
        ts = TrafficStats(protocol=Protocol.TCP, accepted=90, dropped=10, rejected=0)
        self.assertAlmostEqual(ts.drop_rate, 10.0)


class TestVirtualKeyboard(unittest.TestCase):
    def setUp(self):
        from ui.virtual_keyboard import VirtualKeyboard, KeyAction
        self.vk = VirtualKeyboard()
        self.KA = KeyAction

    def test_initial_state(self):
        self.assertGreater(len(self.vk.layouts), 0)
        self.assertIsNotNone(self.vk.current_layout)
        self.assertGreater(len(self.vk.macros), 0)

    def test_layout_key_count(self):
        count = self.vk.current_layout.key_count
        self.assertGreater(count, 70)

    def test_press_key(self):
        result = self.vk.press_key("A")
        self.assertTrue(result)
        self.assertIn("A", self.vk.pressed_keys)

    def test_release_key(self):
        self.vk.press_key("A")
        result = self.vk.release_key("A")
        self.assertTrue(result)
        self.assertNotIn("A", self.vk.pressed_keys)

    def test_release_all(self):
        self.vk.press_key("A")
        self.vk.press_key("B")
        count = self.vk.release_all()
        self.assertEqual(count, 2)
        self.assertEqual(len(self.vk.pressed_keys), 0)

    def test_start_stop_macro_record(self):
        self.vk.start_macro_record()
        self.assertTrue(self.vk.macro_recording)
        self.vk.recorded_keys.append("A")
        self.vk.recorded_keys.append("B")
        keys = self.vk.stop_macro_record()
        self.assertFalse(self.vk.macro_recording)
        self.assertEqual(keys, ["A", "B"])

    def test_add_macro(self):
        macro = self.vk.add_macro("Test Macro", ["A", "B", "C"], shortcut="Ctrl+T")
        self.assertEqual(macro.name, "Test Macro")
        self.assertIn(macro, self.vk.macros)

    def test_run_macro(self):
        result = self.vk.run_macro("Screenshot")
        self.assertTrue(result)
        macro = next(m for m in self.vk.macros if m.name == "Screenshot")
        self.assertEqual(macro.use_count, 46)

    def test_add_mapping(self):
        mapping = self.vk.add_mapping("Tab", "Ctrl+Tab", description="Tab → Ctrl+Tab")
        self.assertIn(mapping, self.vk.mappings)

    def test_get_key(self):
        key = self.vk.get_key("Space")
        self.assertIsNotNone(key)
        self.assertEqual(key.label, "Space")

    def test_get_key_not_found(self):
        key = self.vk.get_key("NonExistent")
        self.assertIsNone(key)

    def test_get_macros_sorted(self):
        macros = self.vk.get_macros()
        self.assertGreater(len(macros), 0)
        for i in range(len(macros) - 1):
            self.assertGreaterEqual(macros[i].use_count, macros[i + 1].use_count)

    def test_get_stats(self):
        stats = self.vk.get_stats()
        self.assertIn("total_keys", stats)
        self.assertIn("macros", stats)

    def test_key_display_label(self):
        from ui.virtual_keyboard import KeyDef
        k = KeyDef(name="1", label="1", label_shifted="!")
        self.assertEqual(k.display_label, "!")
        k2 = KeyDef(name="A", label="A")
        self.assertEqual(k2.display_label, "A")

    def test_macro_preview(self):
        from ui.virtual_keyboard import Macro
        m = Macro(name="test", keys=["A", "B", "C", "D", "E", "F"])
        self.assertIn("...", m.preview)


class TestPartitionManager(unittest.TestCase):
    def setUp(self):
        from ui.partition_manager import PartitionManager, FileSystem
        self.pm = PartitionManager()
        self.FS = FileSystem

    def test_initial_state(self):
        self.assertGreater(len(self.pm.disks), 0)
        self.assertGreater(len(self.pm.backups), 0)
        self.assertIsNotNone(self.pm.selected_disk)

    def test_get_all_partitions(self):
        parts = self.pm.get_all_partitions()
        self.assertGreater(len(parts), 0)

    def test_get_mounted_partitions(self):
        mounted = self.pm.get_mounted_partitions()
        self.assertGreater(len(mounted), 0)
        for p in mounted:
            self.assertTrue(p.is_mounted)

    def test_get_disk(self):
        disk = self.pm.get_disk("/dev/nvme0n1")
        self.assertIsNotNone(disk)
        self.assertEqual(disk.model, "Samsung 990 Pro 2TB")

    def test_select_disk(self):
        disk = self.pm.select_disk("/dev/sda")
        self.assertIsNotNone(disk)
        self.assertEqual(self.pm.selected_disk, disk)

    def test_select_partition(self):
        part = self.pm.select_partition("/dev/nvme0n1p2")
        self.assertIsNotNone(part)
        self.assertEqual(self.pm.selected_partition, part)

    def test_format_partition(self):
        result = self.pm.format_partition("/dev/nvme0n1p2", self.FS.BTRFS, "data-btrfs")
        self.assertTrue(result)
        part = self.pm.select_partition("/dev/nvme0n1p2")
        self.assertEqual(part.filesystem, self.FS.BTRFS)

    def test_resize_partition(self):
        result = self.pm.resize_partition("/dev/nvme0n1p2", 200.0)
        self.assertTrue(result)

    def test_mount_partition(self):
        unmounted = next(p for p in self.pm.get_all_partitions() if not p.is_mounted)
        result = self.pm.mount_partition(unmounted.device, "/mnt/test")
        self.assertTrue(result)
        self.assertTrue(unmounted.is_mounted)

    def test_unmount_partition(self):
        mounted = next(p for p in self.pm.get_all_partitions() if p.is_mounted)
        result = self.pm.unmount_partition(mounted.device)
        self.assertTrue(result)
        self.assertFalse(mounted.is_mounted)

    def test_create_backup(self):
        task = self.pm.create_backup("Test Backup", "/test", "/backup/test",
                                      size_gb=10.0)
        self.assertEqual(task.name, "Test Backup")
        self.assertIn(task, self.pm.backups)

    def test_get_disk_stats(self):
        stats = self.pm.get_disk_stats()
        self.assertIn("total_disks", stats)
        self.assertIn("total_space_gb", stats)

    def test_partition_usage_bar(self):
        from ui.partition_manager import Partition
        p = Partition(device="/test", size_gb=100, used_gb=50)
        bar = p.usage_bar
        self.assertEqual(len(bar), 20)

    def test_partition_size_display(self):
        from ui.partition_manager import Partition
        p = Partition(device="/test", size_gb=0.5)
        self.assertEqual(p.size_display, "512 MB")
        p.size_gb = 500
        self.assertEqual(p.size_display, "500.0 GB")
        p.size_gb = 2000
        self.assertIn("TB", p.size_display)

    def test_disk_health_status(self):
        from ui.partition_manager import Disk, DiskType
        d = Disk(device="/test", disk_type=DiskType.SSD, health_percent=95)
        self.assertIn("🟢", d.health_status)
        d.health_percent = 60
        self.assertIn("🟠", d.health_status)

    def test_backup_progress_bar(self):
        from ui.partition_manager import BackupTask
        b = BackupTask(name="test", progress=50.0)
        bar = b.progress_bar
        self.assertEqual(len(bar), 20)

    def test_backup_status_icon(self):
        from ui.partition_manager import BackupTask
        b = BackupTask(name="test", status="running")
        self.assertEqual(b.status_icon, "🔄")


if __name__ == "__main__":
    unittest.main()
