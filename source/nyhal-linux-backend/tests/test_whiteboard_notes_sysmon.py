"""Tests for whiteboard, notes app, and system monitor."""
import unittest
import time

from ui.whiteboard import (
    WhiteboardApp, DrawStroke, StickyNote, TextObject, CollabCursor,
    Layer, DrawTool, StickyColor, Point,
)
from ui.notes_app import (
    NotesApp, Note, NoteLink, Tag, NoteType,
)
from ui.sys_monitor import (
    SysMonitor, CpuCore, MemoryInfo, NetworkIO, DiskIO, Process, SortBy,
)


# ─── Whiteboard Tests ────────────────────────────────────────────────

class TestPoint(unittest.TestCase):
    def test_distance(self):
        a = Point(0, 0)
        b = Point(3, 4)
        self.assertAlmostEqual(a.distance_to(b), 5.0)

    def test_to_tuple(self):
        p = Point(1.5, 2.5)
        self.assertEqual(p.to_tuple(), (1.5, 2.5))


class TestDrawStroke(unittest.TestCase):
    def test_bounds(self):
        s = DrawStroke(points=[Point(10, 20), Point(50, 80)])
        self.assertEqual(s.bounds, (10, 20, 50, 80))

    def test_length(self):
        s = DrawStroke(points=[Point(0, 0), Point(3, 4)])
        self.assertAlmostEqual(s.length, 5.0)


class TestStickyNote(unittest.TestCase):
    def test_text_preview(self):
        n = StickyNote(text="a" * 50)
        self.assertTrue(len(n.text_preview) <= 33)

    def test_word_count(self):
        n = StickyNote(text="one two three four")
        self.assertEqual(n.word_count, 4)


class TestWhiteboardApp(unittest.TestCase):
    def setUp(self):
        self.app = WhiteboardApp()

    def test_initial_state(self):
        self.assertGreater(len(self.app._strokes), 0)
        self.assertGreater(len(self.app._stickies), 0)

    def test_render_canvas(self):
        lines = self.app.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("WHITEBOARD" in l for l in lines))

    def test_render_layers(self):
        self.app.set_view("layers")
        lines = self.app.render()
        self.assertTrue(any("Layers" in l for l in lines))

    def test_render_history(self):
        self.app.set_view("history")
        lines = self.app.render()
        self.assertTrue(any("History" in l for l in lines))

    def test_render_collaborators(self):
        self.app.set_view("collaborators")
        lines = self.app.render()
        self.assertTrue(any("Collaborators" in l for l in lines))

    def test_zoom(self):
        initial = self.app._zoom
        self.app.zoom_in()
        self.assertGreater(self.app._zoom, initial)

    def test_toggle_grid(self):
        self.app.toggle_grid()
        self.assertFalse(self.app._grid_visible)


# ─── Notes App Tests ─────────────────────────────────────────────────

class TestNote(unittest.TestCase):
    def test_preview(self):
        n = Note(content="Hello world test content here yes indeed")
        self.assertEqual(n.preview, "Hello world test content here yes indeed")

    def test_long_preview(self):
        n = Note(content="x" * 100)
        self.assertTrue(len(n.preview) <= 83)

    def test_markdown_headings(self):
        n = Note(content="# Title\nSome text\n## Subtitle\nMore text")
        headings = n.markdown_headings
        self.assertEqual(len(headings), 2)
        self.assertIn("Title", headings[0])


class TestTag(unittest.TestCase):
    def test_display(self):
        t = Tag("vulkan", "gpu")
        self.assertEqual(t.display, "gpu/vulkan")

    def test_root_tag(self):
        t = Tag("compositor")
        self.assertEqual(t.display, "compositor")


class TestNotesApp(unittest.TestCase):
    def setUp(self):
        self.app = NotesApp()

    def test_initial_state(self):
        self.assertGreater(len(self.app._notes), 0)
        self.assertGreater(len(self.app._tags), 0)
        self.assertGreater(len(self.app._links), 0)

    def test_render_editor(self):
        lines = self.app.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("NOTES" in l for l in lines))

    def test_render_preview(self):
        self.app.set_view("preview")
        lines = self.app.render()
        self.assertTrue(any("═" in l for l in lines))

    def test_render_tags(self):
        self.app.set_view("tags")
        lines = self.app.render()
        self.assertTrue(any("Tags" in l for l in lines))

    def test_render_links(self):
        self.app.set_view("links")
        lines = self.app.render()
        self.assertTrue(any("Linked" in l or "🔗" in l for l in lines))

    def test_render_graph(self):
        self.app.set_view("graph")
        lines = self.app.render()
        self.assertTrue(any("Graph" in l for l in lines))

    def test_render_daily(self):
        self.app.set_view("daily")
        lines = self.app.render()
        self.assertTrue(any("Daily" in l for l in lines))

    def test_total_words(self):
        self.assertGreater(self.app.total_words, 0)

    def test_total_links(self):
        self.assertGreater(self.app.total_links, 0)

    def test_filtered_by_tag(self):
        self.app._filter_tag = "vulkan"
        filtered = self.app.filtered_notes
        for n in filtered:
            self.assertIn("vulkan", n.tags)


# ─── System Monitor Tests ────────────────────────────────────────────

class TestCpuCore(unittest.TestCase):
    def test_usage_bar(self):
        c = CpuCore(usage_pct=50)
        bar = c.usage_bar
        self.assertEqual(len(bar), 20)
        self.assertIn("█", bar)

    def test_temp_status(self):
        c = CpuCore(temperature=90)
        self.assertIn("🔴", c.temp_status)


class TestMemoryInfo(unittest.TestCase):
    def test_used_pct(self):
        m = MemoryInfo(total_mb=1000, used_mb=500)
        self.assertAlmostEqual(m.used_pct, 50.0)

    def test_usage_bar(self):
        m = MemoryInfo(total_mb=1000, used_mb=200)
        bar = m.usage_bar
        self.assertEqual(len(bar), 20)


class TestNetworkIO(unittest.TestCase):
    def test_rx_rate_str(self):
        n = NetworkIO(rx_rate=5000)
        self.assertIn("KB/s", n.rx_rate_str)

    def test_sparkline(self):
        n = NetworkIO()
        n.rx_history = [1, 2, 3, 4, 5]
        spark = n.sparkline(n.rx_history, 5)
        self.assertEqual(len(spark), 5)


class TestDiskIO(unittest.TestCase):
    def test_used_pct(self):
        d = DiskIO(total_gb=100, used_gb=60)
        self.assertAlmostEqual(d.used_pct, 60.0)

    def test_usage_bar(self):
        d = DiskIO(total_gb=100, used_gb=50)
        bar = d.usage_bar
        self.assertEqual(len(bar), 20)


class TestProcess(unittest.TestCase):
    def test_cpu_bar(self):
        p = Process(cpu_pct=25)
        bar = p.cpu_bar
        self.assertEqual(len(bar), 20)

    def test_state_icon(self):
        p = Process(state="R")
        self.assertEqual(p.state_icon, "🟢")


class TestSysMonitor(unittest.TestCase):
    def setUp(self):
        self.mon = SysMonitor()

    def test_initial_state(self):
        self.assertGreater(len(self.mon._cpu_cores), 0)
        self.assertGreater(len(self.mon._processes), 0)

    def test_render_overview(self):
        lines = self.mon.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("SYSTEM MONITOR" in l for l in lines))

    def test_render_cpu(self):
        self.mon.set_view("cpu")
        lines = self.mon.render()
        self.assertTrue(any("CPU" in l for l in lines))

    def test_render_memory(self):
        self.mon.set_view("memory")
        lines = self.mon.render()
        self.assertTrue(any("Memory" in l for l in lines))

    def test_render_network(self):
        self.mon.set_view("network")
        lines = self.mon.render()
        self.assertTrue(any("Network" in l for l in lines))

    def test_render_disk(self):
        self.mon.set_view("disk")
        lines = self.mon.render()
        self.assertTrue(any("Disk" in l for l in lines))

    def test_render_processes(self):
        self.mon.set_view("processes")
        lines = self.mon.render()
        self.assertTrue(any("Processes" in l for l in lines))

    def test_avg_cpu(self):
        avg = self.mon.avg_cpu
        self.assertGreater(avg, 0)
        self.assertLess(avg, 100)


if __name__ == "__main__":
    unittest.main()
