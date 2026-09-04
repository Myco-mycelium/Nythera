import unittest
import time


class TestProcessManager(unittest.TestCase):
    def setUp(self):
        from ui.process_manager import ProcessManager, ProcessState, ProcessPriority
        self.pm = ProcessManager()
        self.PS = ProcessState
        self.PP = ProcessPriority

    def test_initial_state(self):
        self.assertGreater(len(self.pm.processes), 0)
        self.assertGreater(len(self.pm.groups), 0)
        self.assertGreater(len(self.pm.resource_limits), 0)

    def test_get_process(self):
        proc = self.pm.get_process(2)
        self.assertIsNotNone(proc)
        self.assertEqual(proc.name, "nyrqis-compositor")

    def test_get_process_not_found(self):
        proc = self.pm.get_process(99999)
        self.assertIsNone(proc)

    def test_tree_view(self):
        tree = self.pm.get_tree_view()
        self.assertGreater(len(tree), 0)
        self.assertEqual(tree[0][0], 0)  # root at depth 0

    def test_filtered_processes(self):
        procs = self.pm.get_filtered_processes()
        self.assertEqual(len(procs), len(self.pm.processes))

    def test_filter_by_user(self):
        self.pm.filter_user = "zeus"
        procs = self.pm.get_filtered_processes()
        for p in procs:
            self.assertEqual(p.user, "zeus")
        self.pm.filter_user = ""

    def test_kill_process(self):
        result = self.pm.kill_process(800, "KILL")
        self.assertTrue(result)

    def test_kill_nonexistent(self):
        result = self.pm.kill_process(99999)
        self.assertFalse(result)

    def test_set_priority(self):
        result = self.pm.set_priority(2, self.PP.REALTIME)
        self.assertTrue(result)
        proc = self.pm.get_process(2)
        self.assertEqual(proc.priority, self.PP.REALTIME)
        self.assertEqual(proc.nice_value, -20)

    def test_set_resource_limit(self):
        result = self.pm.set_resource_limit(2, max_memory_mb=2048)
        self.assertTrue(result)
        limit = next(l for l in self.pm.resource_limits if l.pid == 2)
        self.assertEqual(limit.max_memory_mb, 2048)

    def test_get_top_cpu(self):
        top = self.pm.get_top_cpu(3)
        self.assertEqual(len(top), 3)
        self.assertGreaterEqual(top[0].cpu_percent, top[1].cpu_percent)

    def test_get_top_memory(self):
        top = self.pm.get_top_memory(3)
        self.assertEqual(len(top), 3)
        self.assertGreaterEqual(top[0].memory_mb, top[1].memory_mb)

    def test_get_group_stats(self):
        stats = self.pm.get_group_stats()
        self.assertGreater(len(stats), 0)
        self.assertIn("total_cpu", stats[0])

    def test_process_state_icon(self):
        from ui.process_manager import Process
        p = Process(pid=1, name="test", state=self.PS.RUNNING)
        self.assertEqual(p.state_icon, "🟢")

    def test_process_cpu_bar(self):
        from ui.process_manager import Process
        p = Process(pid=1, name="test", cpu_percent=50.0)
        bar = p.cpu_bar
        self.assertEqual(len(bar), 20)
        self.assertIn("█", bar)

    def test_process_uptime_display(self):
        from ui.process_manager import Process
        p = Process(pid=1, name="test", uptime_s=30)
        self.assertEqual(p.uptime_display, "30s")
        p.uptime_s = 90
        self.assertEqual(p.uptime_display, "1.5m")
        p.uptime_s = 7200
        self.assertEqual(p.uptime_display, "2.0h")
        p.uptime_s = 172800
        self.assertEqual(p.uptime_display, "2.0d")


class TestUSBManager(unittest.TestCase):
    def setUp(self):
        from ui.usb_manager import USBManager, MountState, USBSpeed
        self.usb = USBManager()
        self.MS = MountState
        self.US = USBSpeed

    def test_initial_state(self):
        self.assertGreater(len(self.usb.devices), 0)
        self.assertGreater(len(self.usb.events), 0)

    def test_mount_device(self):
        result = self.usb.mount_device("1-4")  # not connected
        self.assertFalse(result)
        # find an unmounted device if any
        unmounted = [d for d in self.usb.devices if d.mount_state == self.MS.UNMOUNTED]
        if unmounted:
            result = self.usb.mount_device(unmounted[0].bus_port)
            self.assertTrue(result)

    def test_unmount_device(self):
        mounted = [d for d in self.usb.devices if d.mount_state == self.MS.MOUNTED]
        self.assertGreater(len(mounted), 0)
        result = self.usb.unmount_device(mounted[0].bus_port)
        self.assertTrue(result)
        self.assertEqual(mounted[0].mount_state, self.MS.UNMOUNTED)

    def test_eject_device(self):
        mounted = [d for d in self.usb.devices if d.mount_state == self.MS.MOUNTED]
        self.assertGreater(len(mounted), 0)
        result = self.usb.eject_device(mounted[0].bus_port)
        self.assertTrue(result)

    def test_get_storage_devices(self):
        storage = self.usb.get_storage_devices()
        self.assertGreater(len(storage), 0)
        for d in storage:
            self.assertIn(d.device_class.value, ["mass_storage", "mtp"])

    def test_get_input_devices(self):
        inputs = self.usb.get_input_devices()
        self.assertGreater(len(inputs), 0)

    def test_get_connected_devices(self):
        connected = self.usb.get_connected_devices()
        self.assertEqual(len(connected), len(self.usb.devices))

    def test_get_device(self):
        device = self.usb.get_device("1-1")
        self.assertIsNotNone(device)
        self.assertEqual(device.product_name, "Ultra Flair USB 3.0")

    def test_get_power_summary(self):
        summary = self.usb.get_power_summary()
        self.assertIn("total_ma", summary)
        self.assertIn("used_ma", summary)
        self.assertGreater(summary["usage_percent"], 0)

    def test_get_recent_events(self):
        events = self.usb.get_recent_events(5)
        self.assertEqual(len(events), 5)
        self.assertGreaterEqual(events[0].timestamp, events[1].timestamp)

    def test_device_class_icon(self):
        from ui.usb_manager import USBDevice, USBDeviceClass
        d = USBDevice(device_class=USBDeviceClass.MASS_STORAGE)
        self.assertEqual(d.class_icon, "💾")

    def test_device_usage_bar(self):
        from ui.usb_manager import USBDevice
        d = USBDevice(capacity_gb=100, used_gb=50)
        bar = d.usage_bar
        self.assertEqual(len(bar), 20)

    def test_disconnect_device(self):
        result = self.usb.disconnect_device("1-1")
        self.assertTrue(result)
        device = self.usb.get_device("1-1")
        self.assertFalse(device.is_connected)


class TestClipboardManager(unittest.TestCase):
    def setUp(self):
        from ui.clipboard_manager import ClipboardManager, ClipboardType, SyncStatus
        self.cm = ClipboardManager()
        self.CT = ClipboardType
        self.SS = SyncStatus

    def test_initial_state(self):
        self.assertGreater(len(self.cm.history), 0)
        self.assertGreater(len(self.cm.snippets), 0)
        self.assertGreater(len(self.cm.devices), 0)

    def test_copy(self):
        entry = self.cm.copy("test content", self.CT.TEXT, "terminal")
        self.assertEqual(entry.content, "test content")
        self.assertEqual(self.cm.current_entry, entry)
        self.assertEqual(self.cm.history[0], entry)

    def test_paste(self):
        content = self.cm.paste(0)
        self.assertIsNotNone(content)

    def test_paste_invalid_index(self):
        content = self.cm.paste(99999)
        self.assertIsNone(content)

    def test_pin_entry(self):
        self.cm.pin_entry(1)
        self.assertTrue(self.cm.history[1].is_pinned)

    def test_delete_entry(self):
        initial = len(self.cm.history)
        # find a non-pinned entry
        for i, e in enumerate(self.cm.history):
            if not e.is_pinned:
                result = self.cm.delete_entry(i)
                self.assertTrue(result)
                self.assertEqual(len(self.cm.history), initial - 1)
                break

    def test_search(self):
        results = self.cm.search("python")
        self.assertGreater(len(results), 0)

    def test_filter_by_type(self):
        results = self.cm.filter_by_type(self.CT.CODE)
        self.assertGreater(len(results), 0)
        for e in results:
            self.assertEqual(e.entry_type, self.CT.CODE)

    def test_get_pinned(self):
        pinned = self.cm.get_pinned()
        self.assertGreater(len(pinned), 0)
        for e in pinned:
            self.assertTrue(e.is_pinned)

    def test_add_snippet(self):
        snippet = self.cm.add_snippet("Test Snippet", "echo hello",
                                       shortcut="ts", category="Test")
        self.assertEqual(snippet.name, "Test Snippet")
        self.assertIn(snippet, self.cm.snippets)

    def test_use_snippet(self):
        content = self.cm.use_snippet("Git Push")
        self.assertIsNotNone(content)
        self.assertIn("git add", content)

    def test_get_snippets_by_category(self):
        cats = self.cm.get_snippets_by_category()
        self.assertIn("Git", cats)
        self.assertIn("Code", cats)

    def test_get_stats(self):
        stats = self.cm.get_stats()
        self.assertGreater(stats.total_entries, 0)
        self.assertGreater(stats.snippets_count, 0)

    def test_clear_history(self):
        cleared = self.cm.clear_history(keep_pinned=True)
        self.assertGreater(cleared, 0)
        for e in self.cm.history:
            self.assertTrue(e.is_pinned)

    def test_entry_type_icon(self):
        from ui.clipboard_manager import ClipboardEntry
        e = ClipboardEntry(content="test", entry_type=self.CT.CODE)
        self.assertEqual(e.type_icon, "💻")

    def test_entry_preview(self):
        from ui.clipboard_manager import ClipboardEntry
        e = ClipboardEntry(content="line1\nline2\nline3")
        self.assertIn("line1", e.preview)
        self.assertIn("+2 lines", e.preview)

    def test_entry_size_display(self):
        from ui.clipboard_manager import ClipboardEntry
        e = ClipboardEntry(content="x" * 500)
        self.assertEqual(e.size_display, "500 B")
        e.content = "x" * 2048
        e.size_bytes = 2048
        self.assertEqual(e.size_display, "2.0 KB")


if __name__ == "__main__":
    unittest.main()
