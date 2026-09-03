"""Tests for network speed test, markdown editor, and system info."""
import unittest
import time

from ui.net_speed_test import (
    NetSpeedTest, PingResult, TraceHop, Server, BandwidthResult, TestState,
)
from ui.markdown_editor import (
    MarkdownEditor, Document, TOCEntry, MarkdownElement, ViewMode,
)
from ui.sys_info import (
    SysInfo, CPUInfo, GPUInfo, MemoryInfo, StorageDevice, NetworkAdapter,
    DriverInfo, BenchmarkResult, InfoCategory,
)


# ─── Network Speed Test Tests ────────────────────────────────────────

class TestPingResult(unittest.TestCase):
    def test_packet_loss_pct(self):
        p = PingResult(packets_sent=100, packets_received=95)
        self.assertAlmostEqual(p.packet_loss_pct, 5.0)

    def test_quality(self):
        p = PingResult(avg_ms=10, packets_sent=100, packets_received=100)
        self.assertIn("Excellent", p.quality)


class TestTraceHop(unittest.TestCase):
    def test_latency_str(self):
        h = TraceHop(latency_ms=15.5)
        self.assertIn("15.5", h.latency_str)

    def test_timeout(self):
        h = TraceHop(timed_out=True)
        self.assertIn("*", h.latency_str)


class TestBandwidthResult(unittest.TestCase):
    def test_quality_score(self):
        b = BandwidthResult(download_mbps=200, upload_mbps=100, ping_ms=5)
        self.assertGreater(b.quality_score, 50)

    def test_download_bar(self):
        b = BandwidthResult(download_mbps=250)
        bar = b.download_bar
        self.assertEqual(len(bar), 20)


class TestNetSpeedTest(unittest.TestCase):
    def setUp(self):
        self.test = NetSpeedTest()

    def test_initial_state(self):
        self.assertGreater(len(self.test._servers), 0)
        self.assertGreater(len(self.test._bandwidth_history), 0)

    def test_render_speed(self):
        lines = self.test.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("SPEED TEST" in l for l in lines))

    def test_render_ping(self):
        self.test.set_view("ping")
        lines = self.test.render()
        self.assertTrue(any("Ping" in l for l in lines))

    def test_render_traceroute(self):
        self.test.set_view("traceroute")
        lines = self.test.render()
        self.assertTrue(any("Traceroute" in l for l in lines))

    def test_render_servers(self):
        self.test.set_view("servers")
        lines = self.test.render()
        self.assertTrue(any("Server" in l for l in lines))

    def test_render_history(self):
        self.test.set_view("history")
        lines = self.test.render()
        self.assertTrue(any("History" in l for l in lines))


# ─── Markdown Editor Tests ───────────────────────────────────────────

class TestDocument(unittest.TestCase):
    def test_stats(self):
        d = Document(word_count=100, line_count=50)
        self.assertIn("100", d.stats)


class TestTOCEntry(unittest.TestCase):
    def test_indent(self):
        e = TOCEntry(level=2)
        self.assertEqual(e.indent, "  ")


class TestMarkdownEditor(unittest.TestCase):
    def setUp(self):
        self.editor = MarkdownEditor()

    def test_initial_state(self):
        self.assertGreater(len(self.editor._documents), 0)
        self.assertGreater(len(self.editor._toc), 0)

    def test_render_split(self):
        lines = self.editor.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("MARKDOWN EDITOR" in l for l in lines))

    def test_render_editor(self):
        self.editor.set_view(ViewMode.EDITOR)
        lines = self.editor.render()
        self.assertTrue(any("Editor" in l for l in lines))

    def test_render_preview(self):
        self.editor.set_view(ViewMode.PREVIEW)
        lines = self.editor.render()
        self.assertTrue(any("Preview" in l or "═══" in l for l in lines))

    def test_render_outline(self):
        self.editor.set_view(ViewMode.OUTLINE)
        lines = self.editor.render()
        self.assertTrue(any("Outline" in l for l in lines))

    def test_render_export(self):
        self.editor.set_view(ViewMode.EXPORT)
        lines = self.editor.render()
        self.assertTrue(any("Export" in l for l in lines))

    def test_current_doc(self):
        doc = self.editor.current_doc
        self.assertIsNotNone(doc)
        self.assertGreater(len(doc.content), 0)

    def test_preview_lines(self):
        lines = self.editor.preview_lines
        self.assertGreater(len(lines), 0)


# ─── System Info Tests ───────────────────────────────────────────────

class TestCPUInfo(unittest.TestCase):
    def test_core_config(self):
        c = CPUInfo(cores=16, threads=32)
        self.assertEqual(c.core_config, "16C/32T")

    def test_clock_str(self):
        c = CPUInfo(base_clock_ghz=4.5, boost_clock_ghz=5.7)
        self.assertIn("4.5", c.clock_str)


class TestGPUInfo(unittest.TestCase):
    def test_vram_str(self):
        g = GPUInfo(vram_gb=12, vram_type="GDDR6X")
        self.assertIn("12", g.vram_str)


class TestStorageDevice(unittest.TestCase):
    def test_capacity_str_tb(self):
        s = StorageDevice(capacity_gb=2000)
        self.assertIn("TB", s.capacity_str)

    def test_health_bar(self):
        s = StorageDevice(health_pct=95)
        bar = s.health_bar
        self.assertEqual(len(bar), 20)


class TestBenchmarkResult(unittest.TestCase):
    def test_score_bar(self):
        b = BenchmarkResult(score=75, max_score=100)
        bar = b.score_bar
        self.assertEqual(len(bar), 20)


class TestSysInfo(unittest.TestCase):
    def setUp(self):
        self.info = SysInfo()

    def test_initial_state(self):
        self.assertIsNotNone(self.info._cpu.model)
        self.assertGreater(len(self.info._storage), 0)
        self.assertGreater(len(self.info._drivers), 0)

    def test_render_overview(self):
        lines = self.info.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("SYSTEM INFORMATION" in l for l in lines))

    def test_render_cpu(self):
        self.info.set_view(InfoCategory.CPU)
        lines = self.info.render()
        self.assertTrue(any("CPU" in l for l in lines))

    def test_render_gpu(self):
        self.info.set_view(InfoCategory.GPU)
        lines = self.info.render()
        self.assertTrue(any("GPU" in l for l in lines))

    def test_render_memory(self):
        self.info.set_view(InfoCategory.MEMORY)
        lines = self.info.render()
        self.assertTrue(any("Memory" in l for l in lines))

    def test_render_storage(self):
        self.info.set_view(InfoCategory.STORAGE)
        lines = self.info.render()
        self.assertTrue(any("Storage" in l for l in lines))

    def test_render_network(self):
        self.info.set_view(InfoCategory.NETWORK)
        lines = self.info.render()
        self.assertTrue(any("Network" in l for l in lines))

    def test_render_drivers(self):
        self.info.set_view(InfoCategory.DRIVERS)
        lines = self.info.render()
        self.assertTrue(any("Drivers" in l for l in lines))

    def test_render_benchmarks(self):
        self.info.set_view(InfoCategory.BENCHMARKS)
        lines = self.info.render()
        self.assertTrue(any("Benchmark" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
