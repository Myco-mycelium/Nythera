#!/usr/bin/env python3
"""Tests for drag-and-drop system and system monitor."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.drag_drop import (
    DragDropManager, DragData, DropZone, DropAction,
    DragState, DropEffect, DragPreview, DragEvent,
)
from ui.system_monitor import (
    SystemMonitor, MonitorView, SystemInfo,
    CpuInfo, MemoryInfo, DiskInfo, NetworkInfo, ProcessInfo,
)


# ===================================================================
# Drag-and-Drop Tests
# ===================================================================

class TestDragData(unittest.TestCase):
    """Tests for DragData."""

    def test_creation(self):
        data = DragData(id="d1", mime_type="text/plain", content="hello")
        self.assertEqual(data.id, "d1")
        self.assertEqual(data.mime_type, "text/plain")
        self.assertEqual(data.content, "hello")
        self.assertGreater(data.created_at, 0)

    def test_auto_id(self):
        data = DragData(id="", mime_type="text/plain", content="x")
        self.assertNotEqual(data.id, "")

    def test_auto_timestamp(self):
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.assertAlmostEqual(data.created_at, time.time(), delta=1)


class TestDropZone(unittest.TestCase):
    """Tests for DropZone."""

    def test_creation(self):
        zone = DropZone(id="z1", rect=(0, 0, 200, 200))
        self.assertEqual(zone.x, 0)
        self.assertEqual(zone.y, 0)
        self.assertEqual(zone.width, 200)
        self.assertEqual(zone.height, 200)
        self.assertTrue(zone.enabled)

    def test_contains(self):
        zone = DropZone(id="z1", rect=(100, 100, 200, 200))
        self.assertTrue(zone.contains(150, 150))
        self.assertTrue(zone.contains(100, 100))
        self.assertFalse(zone.contains(99, 100))
        self.assertFalse(zone.contains(300, 300))

    def test_accepts_wildcard(self):
        zone = DropZone(id="z1", rect=(0, 0, 100, 100))
        self.assertTrue(zone.accepts("text/plain"))
        self.assertTrue(zone.accepts("image/png"))

    def test_accepts_specific(self):
        zone = DropZone(id="z1", rect=(0, 0, 100, 100),
                        accepted_types={"text/plain"})
        self.assertTrue(zone.accepts("text/plain"))
        self.assertFalse(zone.accepts("image/png"))

    def test_disabled_rejects(self):
        zone = DropZone(id="z1", rect=(0, 0, 100, 100), enabled=False)
        self.assertFalse(zone.accepts("text/plain"))


class TestDragDropManager(unittest.TestCase):
    """Tests for DragDropManager."""

    def setUp(self):
        self.dnd = DragDropManager()

    def test_initial_state(self):
        self.assertEqual(self.dnd.state, DragState.IDLE)
        self.assertFalse(self.dnd.is_dragging)
        self.assertEqual(self.dnd.item_count, 0)

    def test_start_drag(self):
        data = DragData(id="d1", mime_type="text/plain", content="hello",
                        label="Hello")
        result = self.dnd.start_drag(data, 100, 200)
        self.assertTrue(result)
        self.assertTrue(self.dnd.is_dragging)
        self.assertEqual(self.dnd.cursor_x, 100)
        self.assertEqual(self.dnd.cursor_y, 200)
        self.assertEqual(self.dnd.item_count, 1)

    def test_cannot_start_when_dragging(self):
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        data2 = DragData(id="d2", mime_type="text/plain", content="y")
        result = self.dnd.start_drag(data2, 0, 0)
        self.assertFalse(result)

    def test_move_drag(self):
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 100, 200)
        self.dnd.move_drag(150, 250)
        self.assertEqual(self.dnd.cursor_x, 150)
        self.assertEqual(self.dnd.cursor_y, 250)

    def test_move_ignored_when_idle(self):
        self.dnd.move_drag(50, 50)
        self.assertEqual(self.dnd.cursor_x, 0)

    def test_cancel(self):
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        self.dnd.cancel()
        self.assertEqual(self.dnd.state, DragState.IDLE)
        self.assertFalse(self.dnd.is_dragging)
        self.assertEqual(self.dnd.item_count, 0)

    def test_register_zone(self):
        zone = self.dnd.register_zone(
            rect=(100, 100, 300, 300),
            label="Drop here",
        )
        self.assertIsNotNone(zone)
        self.assertEqual(zone.id, "zone-0")
        self.assertEqual(len(self.dnd.zones), 1)

    def test_unregister_zone(self):
        zone = self.dnd.register_zone(rect=(0, 0, 100, 100))
        result = self.dnd.unregister_zone(zone.id)
        self.assertTrue(result)
        self.assertEqual(len(self.dnd.zones), 0)

    def test_unregister_nonexistent(self):
        result = self.dnd.unregister_zone("no-such-zone")
        self.assertFalse(result)

    def test_clear_zones(self):
        self.dnd.register_zone(rect=(0, 0, 100, 100))
        self.dnd.register_zone(rect=(200, 200, 100, 100))
        count = self.dnd.clear_zones()
        self.assertEqual(count, 2)
        self.assertEqual(len(self.dnd.zones), 0)

    def test_zone_enabled(self):
        zone = self.dnd.register_zone(rect=(0, 0, 100, 100))
        self.dnd.set_zone_enabled(zone.id, False)
        self.assertFalse(zone.enabled)

    def test_drop_on_zone(self):
        zone = self.dnd.register_zone(
            rect=(100, 100, 200, 200),
            label="Target",
        )
        data = DragData(id="d1", mime_type="text/plain", content="hello")
        self.dnd.start_drag(data, 50, 50)
        self.dnd.move_drag(150, 150)  # Over zone
        result = self.dnd.drop(150, 150)
        self.assertTrue(result)
        self.assertEqual(self.dnd.state, DragState.IDLE)

    def test_drop_outside_zone(self):
        self.dnd.register_zone(rect=(100, 100, 200, 200))
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 50, 50)
        result = self.dnd.drop(50, 50)  # Outside zone
        self.assertFalse(result)

    def test_drop_rejected_when_disabled(self):
        zone = self.dnd.register_zone(rect=(0, 0, 200, 200))
        self.dnd.set_zone_enabled(zone.id, False)
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        result = self.dnd.drop(100, 100)
        self.assertFalse(result)

    def test_add_item(self):
        data1 = DragData(id="d1", mime_type="text/plain", content="a")
        data2 = DragData(id="d2", mime_type="text/plain", content="b")
        self.dnd.start_drag(data1, 0, 0)
        self.dnd.add_item(data2)
        self.assertEqual(self.dnd.item_count, 2)
        self.assertEqual(len(self.dnd.items), 2)

    def test_zone_hit_test(self):
        zone = self.dnd.register_zone(
            rect=(100, 100, 200, 200),
            accepted_types={"text/plain"},
        )
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 50, 50)
        self.dnd.move_drag(150, 150)
        self.assertEqual(self.dnd.active_zone, zone)

    def test_zone_type_mismatch(self):
        zone = self.dnd.register_zone(
            rect=(0, 0, 200, 200),
            accepted_types={"image/png"},
        )
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        self.dnd.move_drag(100, 100)
        self.assertIsNone(self.dnd.active_zone)

    def test_modifier_ctrl_copies(self):
        zone = self.dnd.register_zone(rect=(0, 0, 200, 200))
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        self.dnd.set_modifier("ctrl", True)
        self.dnd.move_drag(100, 100)
        self.assertEqual(zone.effect, DropEffect.COPY)

    def test_modifier_shift_moves(self):
        zone = self.dnd.register_zone(rect=(0, 0, 200, 200))
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        self.dnd.set_modifier("shift", True)
        self.dnd.move_drag(100, 100)
        self.assertEqual(zone.effect, DropEffect.MOVE)

    def test_modifier_alt_links(self):
        zone = self.dnd.register_zone(rect=(0, 0, 200, 200))
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        self.dnd.set_modifier("alt", True)
        self.dnd.move_drag(100, 100)
        self.assertEqual(zone.effect, DropEffect.LINK)

    def test_preview(self):
        self.assertIsNone(self.dnd.preview)
        data = DragData(id="d1", mime_type="text/plain", content="x",
                        label="Test Item")
        self.dnd.start_drag(data, 0, 0)
        preview = self.dnd.preview
        self.assertIsNotNone(preview)
        self.assertEqual(preview.label, "Test Item")
        self.assertEqual(preview.item_count, 1)

    def test_multi_item_preview(self):
        d1 = DragData(id="d1", mime_type="text/plain", content="a")
        d2 = DragData(id="d2", mime_type="text/plain", content="b")
        self.dnd.start_drag(d1, 0, 0)
        self.dnd.add_item(d2)
        self.assertEqual(self.dnd.preview.item_count, 2)

    def test_drag_duration(self):
        self.assertEqual(self.dnd.drag_duration, 0.0)
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        time.sleep(0.05)
        self.assertGreater(self.dnd.drag_duration, 0)

    def test_history_recorded(self):
        zone = self.dnd.register_zone(rect=(0, 0, 200, 200))
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        self.dnd.move_drag(100, 100)
        self.dnd.drop(100, 100)
        self.assertEqual(len(self.dnd.history), 1)
        self.assertIn("d1", self.dnd.history[0]["items"])

    def test_event_callbacks(self):
        events = []
        self.dnd.on_event(lambda e: events.append(e.type))
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 50, 50)
        self.dnd.move_drag(100, 100)
        self.dnd.cancel()
        self.assertIn("started", events)
        self.assertIn("moved", events)
        self.assertIn("cancelled", events)

    def test_zone_enter_leave_events(self):
        events = []
        self.dnd.on_event(lambda e: events.append(e.type))
        zone = self.dnd.register_zone(rect=(0, 0, 200, 200))
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        self.dnd.move_drag(100, 100)  # Enter zone
        self.dnd.move_drag(300, 300)  # Leave zone
        self.assertIn("entered_zone", events)
        self.assertIn("left_zone", events)

    def test_drop_event(self):
        events = []
        self.dnd.on_event(lambda e: events.append(e.type))
        zone = self.dnd.register_zone(rect=(0, 0, 200, 200))
        data = DragData(id="d1", mime_type="text/plain", content="x")
        self.dnd.start_drag(data, 0, 0)
        self.dnd.move_drag(100, 100)
        self.dnd.drop(100, 100)
        self.assertIn("dropped", events)

    def test_zones_for_type(self):
        z1 = self.dnd.register_zone(
            rect=(0, 0, 100, 100), accepted_types={"text/plain"})
        z2 = self.dnd.register_zone(
            rect=(200, 200, 100, 100), accepted_types={"image/png"})
        z3 = self.dnd.register_zone(
            rect=(300, 300, 100, 100))  # wildcard
        text_zones = self.dnd.zones_for_type("text/plain")
        self.assertEqual(len(text_zones), 2)  # z1 + z3

    def test_repr(self):
        r = repr(self.dnd)
        self.assertIn("DragDropManager", r)
        self.assertIn("idle", r)


# ===================================================================
# System Monitor Tests
# ===================================================================

class TestSystemMonitor(unittest.TestCase):
    """Tests for SystemMonitor."""

    def setUp(self):
        self.mon = SystemMonitor(update_interval=0)

    def test_initial_state(self):
        self.assertFalse(self.mon.visible)
        self.assertEqual(self.mon.cpu_overall, 0.0)
        self.assertEqual(self.mon.memory.usage_percent, 0.0)

    def test_show_hide(self):
        self.mon.show()
        self.assertTrue(self.mon.visible)
        self.mon.hide()
        self.assertFalse(self.mon.visible)

    def test_toggle(self):
        result = self.mon.toggle()
        self.assertTrue(result)
        result2 = self.mon.toggle()
        self.assertFalse(result2)

    def test_update(self):
        result = self.mon.update()
        self.assertTrue(result)
        # Should have read real system data
        self.assertGreaterEqual(self.mon.system.cpu_count, 1)

    def test_cpu_read(self):
        self.mon.update()
        self.assertGreater(self.mon.system.cpu_count, 0)
        self.assertGreaterEqual(len(self.mon.cpu_cores), 0)

    def test_memory_read(self):
        self.mon.update()
        m = self.mon.memory
        self.assertGreater(m.total_mb, 0)
        self.assertGreaterEqual(m.used_mb, 0)
        self.assertGreaterEqual(m.usage_percent, 0)
        self.assertLessEqual(m.usage_percent, 100)

    def test_disk_read(self):
        self.mon.update()
        # Should at least have root filesystem
        self.assertGreater(len(self.mon.disks), 0)
        root_found = any(d.mount_point == "/" for d in self.mon.disks)
        self.assertTrue(root_found)

    def test_network_read(self):
        self.mon.update()
        # Should have at least one interface
        self.assertGreater(len(self.mon.networks), 0)

    def test_process_read(self):
        self.mon.update()
        self.assertGreater(len(self.mon.processes), 0)
        # Process list should have valid data
        p = self.mon.processes[0]
        self.assertGreater(p.pid, 0)
        self.assertNotEqual(p.name, "")

    def test_process_sort(self):
        self.mon.update()
        self.mon.sort_processes("memory")
        procs = self.mon.processes
        # Just verify it doesn't crash
        self.assertIsInstance(procs, list)

    def test_process_sort_toggle(self):
        self.mon.update()
        self.mon.sort_processes("cpu")  # Toggles from True to False
        self.assertFalse(self.mon.process_reverse)
        self.mon.sort_processes("cpu")  # Toggles from False to True
        self.assertTrue(self.mon.process_reverse)

    def test_uptime(self):
        self.mon.update()
        uptime = self.mon.uptime
        self.assertIsInstance(uptime, str)
        self.assertGreater(len(uptime), 0)

    def test_history_size(self):
        self.mon = SystemMonitor(history_size=10, update_interval=0)
        for _ in range(20):
            self.mon.update()
        # History should be trimmed
        if self.mon.cpu_cores:
            self.assertLessEqual(len(self.mon.cpu_cores[0].history), 10)

    def test_sparkline(self):
        data = [0, 25, 50, 75, 100, 75, 50, 25, 0]
        spark = self.mon.sparkline(data, width=9)
        self.assertEqual(len(spark), 9)
        self.assertIsInstance(spark, str)

    def test_sparkline_empty(self):
        self.assertEqual(self.mon.sparkline([]), "")

    def test_format_bytes(self):
        self.assertEqual(self.mon.format_bytes(1024), "1.0 KB")
        self.assertEqual(self.mon.format_bytes(1024 * 1024), "1.0 MB")
        self.assertEqual(self.mon.format_bytes(1024 ** 3), "1.0 GB")

    def test_format_speed(self):
        self.assertIn("KB/s", self.mon.format_speed(50))
        self.assertIn("MB/s", self.mon.format_speed(2048))

    def test_view_set(self):
        self.mon.set_view(MonitorView.DETAILED)
        self.assertEqual(self.mon._view, MonitorView.DETAILED)

    def test_tab_set(self):
        self.mon.set_tab("cpu")
        self.assertEqual(self.mon._selected_tab, "cpu")

    def test_scroll(self):
        self.mon.scroll(5)
        self.assertEqual(self.mon._scroll_offset, 5)
        self.mon.scroll(-10)
        self.assertEqual(self.mon._scroll_offset, 0)

    def test_render_when_hidden(self):
        self.assertIsNone(self.mon.render())

    def test_render_when_visible(self):
        self.mon.show()
        img = self.mon.render()
        self.assertIsNotNone(img)

    def test_callback(self):
        events = []
        self.mon.on_event(lambda e: events.append(e))
        self.mon.show()
        self.mon.update()
        self.mon.hide()
        self.assertIn("shown", events)
        self.assertIn("updated", events)
        self.assertIn("hidden", events)

    def test_repr(self):
        self.mon.update()
        r = repr(self.mon)
        self.assertIn("SystemMonitor", r)

    def test_cpu_overall_no_cores(self):
        mon = SystemMonitor()
        self.assertEqual(mon.cpu_overall, 0.0)


class TestSystemInfo(unittest.TestCase):
    """Tests for SystemInfo dataclass."""

    def test_creation(self):
        info = SystemInfo(hostname="test", os_name="Linux")
        self.assertEqual(info.hostname, "test")
        self.assertEqual(info.os_name, "Linux")


class TestCpuInfo(unittest.TestCase):
    """Tests for CpuInfo dataclass."""

    def test_creation(self):
        core = CpuInfo(core_id=0, usage=45.5)
        self.assertEqual(core.core_id, 0)
        self.assertEqual(core.usage, 45.5)


class TestMemoryInfo(unittest.TestCase):
    """Tests for MemoryInfo dataclass."""

    def test_creation(self):
        m = MemoryInfo(total_mb=8192, used_mb=4096)
        self.assertEqual(m.total_mb, 8192)
        self.assertEqual(m.used_mb, 4096)


class TestDiskInfo(unittest.TestCase):
    """Tests for DiskInfo dataclass."""

    def test_creation(self):
        d = DiskInfo(device="/dev/sda1", mount_point="/")
        self.assertEqual(d.device, "/dev/sda1")
        self.assertEqual(d.mount_point, "/")


class TestNetworkInfo(unittest.TestCase):
    """Tests for NetworkInfo dataclass."""

    def test_creation(self):
        n = NetworkInfo(interface="eth0")
        self.assertEqual(n.interface, "eth0")
        self.assertTrue(n.is_up)


class TestProcessInfo(unittest.TestCase):
    """Tests for ProcessInfo dataclass."""

    def test_creation(self):
        p = ProcessInfo(pid=1, name="init")
        self.assertEqual(p.pid, 1)
        self.assertEqual(p.name, "init")


if __name__ == "__main__":
    unittest.main()
