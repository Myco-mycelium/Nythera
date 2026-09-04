import unittest
import time


class TestStartupManager(unittest.TestCase):
    def setUp(self):
        from ui.startup_manager import StartupManager, StartupStatus, StartupType
        self.sm = StartupManager()
        self.SS = StartupStatus
        self.ST = StartupType

    def test_initial_state(self):
        self.assertGreater(len(self.sm.autostart_entries), 0)
        self.assertGreater(len(self.sm.boot_entries), 0)
        self.assertGreater(len(self.sm.boot_times), 0)
        self.assertGreater(len(self.sm.services), 0)

    def test_toggle_entry(self):
        result = self.sm.toggle_entry("Spotify")
        self.assertTrue(result)
        entry = next(e for e in self.sm.autostart_entries if e.name == "Spotify")
        self.assertFalse(entry.enabled)

    def test_remove_entry(self):
        result = self.sm.remove_entry("Backup Timer")
        self.assertTrue(result)

    def test_add_entry(self):
        from ui.startup_manager import AutostartEntry
        entry = AutostartEntry(name="Test App", command="/usr/bin/test")
        self.sm.add_entry(entry)
        self.assertIn(entry, self.sm.autostart_entries)

    def test_get_enabled_entries(self):
        enabled = self.sm.get_enabled_entries()
        self.assertGreater(len(enabled), 0)
        for e in enabled:
            self.assertTrue(e.enabled)

    def test_get_entries_by_category(self):
        entries = self.sm.get_entries_by_category("Desktop")
        self.assertGreater(len(entries), 0)

    def test_get_boot_time_stats(self):
        stats = self.sm.get_boot_time_stats()
        self.assertIn("average_ms", stats)
        self.assertIn("fastest_ms", stats)

    def test_search(self):
        results = self.sm.search("nyrqis")
        self.assertGreater(len(results), 0)

    def test_get_stats(self):
        stats = self.sm.get_stats()
        self.assertIn("entries", stats)
        self.assertIn("enabled", stats)

    def test_autostart_entry_status_icon(self):
        from ui.startup_manager import AutostartEntry
        e = AutostartEntry(name="test", status=self.SS.RUNNING)
        self.assertEqual(e.status_icon, "🔄")

    def test_boot_time_display(self):
        from ui.startup_manager import BootTimeRecord
        b = BootTimeRecord(total_ms=4500)
        self.assertEqual(b.total_display, "4.5s")
        b.total_ms = 500
        self.assertEqual(b.total_display, "500ms")

    def test_service_active_icon(self):
        from ui.startup_manager import SystemdService
        s = SystemdService(active_state="active")
        self.assertEqual(s.active_icon, "🟢")


class TestDeviceManager(unittest.TestCase):
    def setUp(self):
        from ui.device_manager import DeviceManager, DeviceClass, DriverStatus
        self.dm = DeviceManager()
        self.DC = DeviceClass
        self.DS = DriverStatus

    def test_initial_state(self):
        self.assertGreater(len(self.dm.pci_devices), 0)
        self.assertGreater(len(self.dm.usb_devices), 0)
        self.assertGreater(len(self.dm.drivers), 0)

    def test_get_devices_by_class(self):
        devices = self.dm.get_devices_by_class(self.DC.GPU)
        self.assertGreater(len(devices), 0)
        for d in devices:
            self.assertEqual(d.device_class, self.DC.GPU)

    def test_get_usb_by_class(self):
        devices = self.dm.get_usb_by_class(self.DC.INPUT)
        self.assertGreater(len(devices), 0)

    def test_select_device(self):
        device = self.dm.select_device("01:00.0")
        self.assertIsNotNone(device)
        self.assertEqual(device.vendor_name, "NVIDIA")

    def test_search_pci(self):
        results = self.dm.search_pci("samsung")
        self.assertGreater(len(results), 0)

    def test_search_usb(self):
        results = self.dm.search_usb("logitech")
        self.assertGreater(len(results), 0)

    def test_get_driver_info(self):
        info = self.dm.get_driver_info("nvidia")
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "535.129.03")

    def test_get_stats(self):
        stats = self.dm.get_stats()
        self.assertIn("pci_devices", stats)
        self.assertIn("usb_devices", stats)

    def test_pci_device_bdf(self):
        from ui.device_manager import PCIDevice
        d = PCIDevice(bus="01", device="00", function=0)
        self.assertEqual(d.bdf, "01:00.0")

    def test_pci_device_class_icon(self):
        from ui.device_manager import PCIDevice
        d = PCIDevice(device_class=self.DC.GPU)
        self.assertEqual(d.class_icon, "🎮")

    def test_driver_size_display(self):
        from ui.device_manager import DriverInfo
        d = DriverInfo(size_bytes=500)
        self.assertEqual(d.size_display, "500 B")
        d.size_bytes = 50000
        self.assertEqual(d.size_display, "48.8 KB")


class TestShellEditor(unittest.TestCase):
    def setUp(self):
        from ui.shell_editor import ShellEditor, ShellType
        self.se = ShellEditor()
        self.ST = ShellType

    def test_initial_state(self):
        self.assertGreater(len(self.se.documents), 0)
        self.assertGreater(len(self.se.snippets), 0)
        self.assertIsNotNone(self.se.current_document)

    def test_new_document(self):
        doc = self.se.new_document("test.sh", "#!/bin/bash\necho hello")
        self.assertEqual(doc.name, "test.sh")
        self.assertIn(doc, self.se.documents)

    def test_close_document(self):
        result = self.se.close_document("healthcheck.sh")
        self.assertTrue(result)

    def test_insert_text(self):
        self.se.cursor_line = 1
        self.se.cursor_col = 1
        result = self.se.insert_text("# test\n")
        self.assertTrue(result)
        self.assertTrue(self.se.current_document.modified)

    def test_insert_snippet(self):
        initial = self.se.current_document.content
        result = self.se.insert_snippet("Shebang")
        self.assertTrue(result)

    def test_goto_line(self):
        result = self.se.goto_line(5)
        self.assertTrue(result)
        self.assertEqual(self.se.cursor_line, 5)

    def test_goto_line_invalid(self):
        result = self.se.goto_line(99999)
        self.assertFalse(result)

    def test_search(self):
        results = self.se.search("cargo")
        self.assertGreater(len(results), 0)

    def test_toggle_breakpoint(self):
        initial = len(self.se.debug_state.breakpoints)
        self.se.toggle_breakpoint(20)
        self.assertEqual(len(self.se.debug_state.breakpoints), initial + 1)

    def test_start_debug(self):
        result = self.se.start_debug()
        self.assertTrue(result)
        self.assertTrue(self.se.debug_state.running)

    def test_step_over(self):
        self.se.start_debug()
        self.se.step_over()
        self.assertEqual(self.se.debug_state.current_line, 2)

    def test_stop_debug(self):
        self.se.start_debug()
        result = self.se.stop_debug()
        self.assertTrue(result)
        self.assertFalse(self.se.debug_state.running)

    def test_document_line_count(self):
        doc = self.se.documents[0]
        self.assertGreater(doc.line_count, 0)

    def test_get_stats(self):
        stats = self.se.get_stats()
        self.assertIn("documents", stats)
        self.assertIn("snippets", stats)

    def test_snippet_preview(self):
        snippet = self.se.snippets[0]
        self.assertIn("!", snippet.preview)


if __name__ == "__main__":
    unittest.main()
