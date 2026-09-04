import unittest
import time


class TestPacketAnalyzer(unittest.TestCase):
    def setUp(self):
        from ui.packet_analyzer import PacketAnalyzer, Protocol, CaptureState
        self.analyzer = PacketAnalyzer()
        self.Protocol = Protocol
        self.CS = CaptureState

    def test_initial_state(self):
        self.assertGreater(len(self.analyzer.packets), 0)
        self.assertGreater(len(self.analyzer.interfaces), 0)
        self.assertGreater(len(self.analyzer.filters), 0)

    def test_start_stop_capture(self):
        self.analyzer.start_capture()
        self.assertEqual(self.analyzer.state, self.CS.RUNNING)
        count = self.analyzer.stop_capture()
        self.assertGreater(count, 0)
        self.assertEqual(self.analyzer.state, self.CS.STOPPED)

    def test_pause_resume(self):
        self.analyzer.start_capture()
        self.analyzer.pause_capture()
        self.assertEqual(self.analyzer.state, self.CS.PAUSED)
        self.analyzer.resume_capture()
        self.assertEqual(self.analyzer.state, self.CS.RUNNING)

    def test_apply_filter(self):
        results = self.analyzer.apply_filter("tcp.port == 80 || tcp.port == 8080")
        self.assertIsInstance(results, list)

    def test_apply_empty_filter(self):
        results = self.analyzer.apply_filter("")
        self.assertEqual(len(results), len(self.analyzer.packets))

    def test_get_packet_detail(self):
        pkt = self.analyzer.get_packet_detail(1)
        self.assertIsNotNone(pkt)
        self.assertEqual(pkt.number, 1)

    def test_get_packet_detail_not_found(self):
        pkt = self.analyzer.get_packet_detail(99999)
        self.assertIsNone(pkt)

    def test_get_protocol_stats(self):
        stats = self.analyzer.get_protocol_stats()
        self.assertGreater(len(stats), 0)
        self.assertGreater(stats[0].packet_count, 0)

    def test_get_conversations(self):
        convos = self.analyzer.get_conversations()
        self.assertGreater(len(convos), 0)

    def test_get_traffic_timeline(self):
        timeline = self.analyzer.get_traffic_timeline(buckets=10)
        self.assertEqual(len(timeline), 10)

    def test_capture_summary(self):
        summary = self.analyzer.get_capture_summary()
        self.assertIn("packets", summary)
        self.assertIn("bytes", summary)

    def test_packet_direction_icon(self):
        from ui.packet_analyzer import Packet, PacketDirection
        pkt = Packet(number=1, timestamp=time.time(), source_ip="a", dest_ip="b",
                     direction=PacketDirection.INBOUND)
        self.assertEqual(pkt.direction_icon, "⬇️")

    def test_protocol_stats_size_display(self):
        from ui.packet_analyzer import ProtocolStats, Protocol
        ps = ProtocolStats(protocol=Protocol.TCP, byte_count=1500000)
        self.assertEqual(ps.size_display, "1.4 MB")
        ps.byte_count = 500
        self.assertEqual(ps.size_display, "500 B")

    def test_conversation_bytes_display(self):
        from ui.packet_analyzer import Conversation
        c = Conversation(ip_a="a", ip_b="b", bytes_a_to_b=500, bytes_b_to_a=300)
        self.assertEqual(c.bytes_display, "800 B")
        c.bytes_a_to_b = 5000
        c.bytes_b_to_a = 3000
        self.assertEqual(c.bytes_display, "7.8 KB")


class TestFontInstaller(unittest.TestCase):
    def setUp(self):
        from ui.font_installer import FontInstaller, FontStatus, FontCategory
        self.installer = FontInstaller()
        self.FS = FontStatus
        self.FC = FontCategory

    def test_initial_state(self):
        self.assertGreater(len(self.installer.fonts), 0)
        self.assertGreater(len(self.installer.collections), 0)

    def test_search_fonts(self):
        results = self.installer.search_fonts("inter")
        self.assertGreater(len(results), 0)

    def test_filter_by_category(self):
        results = self.installer.filter_by_category(self.FC.MONOSPACE)
        self.assertGreater(len(results), 0)
        for f in results:
            self.assertEqual(f.category, self.FC.MONOSPACE)

    def test_filter_all(self):
        results = self.installer.filter_by_category(None)
        self.assertEqual(len(results), len(self.installer.fonts))

    def test_get_font_families(self):
        families = self.installer.get_font_families()
        self.assertIn("Inter", families)

    def test_select_font(self):
        font = self.installer.select_font("Inter-Regular")
        self.assertIsNotNone(font)
        self.assertEqual(font.family, "Inter")

    def test_install_font(self):
        result = self.installer.install_font("SourceCodePro-Regular")
        self.assertTrue(result)
        font = next(f for f in self.installer.fonts if f.name == "SourceCodePro-Regular")
        self.assertEqual(font.status, self.FS.INSTALLED)

    def test_uninstall_font(self):
        result = self.installer.uninstall_font("Inter-Regular")
        self.assertTrue(result)
        font = next(f for f in self.installer.fonts if f.name == "Inter-Regular")
        self.assertEqual(font.status, self.FS.AVAILABLE)

    def test_batch_install(self):
        names = ["SourceCodePro-Regular", "Roboto-Regular"]
        count = self.installer.batch_install(names)
        self.assertEqual(count, 2)

    def test_comparison_list(self):
        self.installer.add_to_comparison("Inter-Regular")
        self.installer.add_to_comparison("Roboto-Regular")
        self.assertEqual(len(self.installer.comparison_fonts), 2)

    def test_comparison_limit(self):
        for i in range(5):
            self.installer.add_to_comparison(f"font-{i}")
        self.assertEqual(len(self.installer.comparison_fonts), 4)

    def test_update_preview(self):
        preview = self.installer.update_preview(size_pt=48, color="#ff0000")
        self.assertEqual(preview.size_pt, 48)
        self.assertEqual(preview.color, "#ff0000")

    def test_get_installed_fonts(self):
        installed = self.installer.get_installed_fonts()
        self.assertGreater(len(installed), 0)
        for f in installed:
            self.assertEqual(f.status, self.FS.INSTALLED)

    def test_get_font_stats(self):
        stats = self.installer.get_font_stats()
        self.assertIn("total", stats)
        self.assertIn("installed", stats)

    def test_collection_fonts(self):
        fonts = self.installer.get_collection_fonts("Developer Set")
        self.assertGreater(len(fonts), 0)

    def test_font_status_icon(self):
        from ui.font_installer import FontFile, FontStatus
        f = FontFile(name="test", family="test", status=FontStatus.INSTALLED)
        self.assertEqual(f.status_icon, "✅")

    def test_font_size_display(self):
        from ui.font_installer import FontFile
        f = FontFile(name="test", family="test", size_bytes=500)
        self.assertEqual(f.size_display, "500 B")
        f.size_bytes = 2048
        self.assertEqual(f.size_display, "2.0 KB")
        f.size_bytes = 2 * 1024 * 1024
        self.assertEqual(f.size_display, "2.0 MB")


class TestSystemProfiler(unittest.TestCase):
    def setUp(self):
        from ui.sys_profiler import SystemProfiler, BenchmarkStatus
        self.profiler = SystemProfiler()
        self.BS = BenchmarkStatus

    def test_initial_state(self):
        self.assertIsNotNone(self.profiler.cpu)
        self.assertIsNotNone(self.profiler.ram)
        self.assertIsNotNone(self.profiler.gpu)
        self.assertGreater(len(self.profiler.storage), 0)
        self.assertGreater(len(self.profiler.benchmarks), 0)

    def test_cpu_temp_status(self):
        self.assertIn("🟡", self.profiler.cpu.temp_status)
        self.profiler.cpu.temperature_c = 30
        self.assertIn("🟢", self.profiler.cpu.temp_status)
        self.profiler.cpu.temperature_c = 80
        self.assertIn("🔴", self.profiler.cpu.temp_status)

    def test_cpu_usage_bar(self):
        bar = self.profiler.cpu.usage_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)
        self.assertEqual(len(bar), 20)

    def test_ram_usage_bar(self):
        bar = self.profiler.ram.usage_bar
        self.assertEqual(len(bar), 20)

    def test_gpu_vram_bar(self):
        bar = self.profiler.gpu.vram_bar
        self.assertEqual(len(bar), 20)

    def test_gpu_temp_status(self):
        self.assertIn("🟡", self.profiler.gpu.temp_status)
        self.profiler.gpu.temperature_c = 30
        self.assertIn("🟢", self.profiler.gpu.temp_status)
        self.profiler.gpu.temperature_c = 85
        self.assertIn("🔴", self.profiler.gpu.temp_status)

    def test_run_benchmark(self):
        result = self.profiler.run_benchmark("CPU Single-Core")
        self.assertEqual(result.status, self.BS.COMPLETED)
        self.assertGreater(result.score, 0)

    def test_run_all_benchmarks(self):
        results = self.profiler.run_all_benchmarks()
        self.assertEqual(len(results), len(self.profiler.benchmarks))
        for r in results:
            self.assertEqual(r.status, self.BS.COMPLETED)

    def test_overall_score(self):
        score = self.profiler.get_overall_score()
        self.assertGreater(score, 0)
        self.assertLessEqual(score, 100)

    def test_system_summary(self):
        summary = self.profiler.get_system_summary()
        self.assertIn("cpu", summary)
        self.assertIn("gpu", summary)

    def test_power_consumption(self):
        power = self.profiler.get_power_consumption()
        self.assertIn("cpu_watts", power)
        self.assertIn("gpu_watts", power)

    def test_storage_health(self):
        for s in self.profiler.storage:
            self.assertIn("🟢", s.health_status)

    def test_storage_usage_bar(self):
        bar = self.profiler.storage[0].usage_bar
        self.assertEqual(len(bar), 20)

    def test_benchmark_result_grade(self):
        from ui.sys_profiler import BenchmarkResult
        br = BenchmarkResult(name="test", score=85, max_score=100)
        self.assertEqual(br.grade, "A")
        br.score = 95
        self.assertEqual(br.grade, "A+")
        br.score = 55
        self.assertEqual(br.grade, "D")

    def test_benchmark_result_score_bar(self):
        from ui.sys_profiler import BenchmarkResult
        br = BenchmarkResult(name="test", score=75, max_score=100)
        bar = br.score_bar
        self.assertEqual(len(bar), 20)

    def test_benchmark_result_status_icon(self):
        from ui.sys_profiler import BenchmarkResult, BenchmarkStatus
        br = BenchmarkResult(name="test", status=BenchmarkStatus.COMPLETED)
        self.assertEqual(br.status_icon, "✅")


if __name__ == "__main__":
    unittest.main()
