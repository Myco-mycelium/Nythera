import unittest
import time


class TestScreenshotTool(unittest.TestCase):
    def setUp(self):
        from ui.screenshot_tool import ScreenshotTool, CaptureMode, AnnotationTool
        self.st = ScreenshotTool()
        self.CM = CaptureMode
        self.AT = AnnotationTool

    def test_initial_state(self):
        self.assertGreater(len(self.st.screenshots), 0)
        self.assertGreater(len(self.st.hotkeys), 0)
        self.assertGreater(len(self.st.monitors), 0)

    def test_capture_full_screen(self):
        ss = self.st.capture_full_screen()
        self.assertEqual(ss.mode, self.CM.FULL_SCREEN)
        self.assertIn(ss, self.st.screenshots)
        self.assertEqual(self.st.current_screenshot, ss)

    def test_capture_window(self):
        ss = self.st.capture_window()
        self.assertEqual(ss.mode, self.CM.WINDOW)

    def test_capture_region(self):
        ss = self.st.capture_region(100, 200, 800, 600)
        self.assertEqual(ss.mode, self.CM.REGION)
        self.assertEqual(ss.region.width, 800)

    def test_start_delay(self):
        result = self.st.start_delay(5)
        self.assertTrue(result)
        self.assertTrue(self.st.timer.running)
        self.assertEqual(self.st.timer.seconds, 5)

    def test_add_annotation(self):
        self.st.capture_full_screen()
        ann = self.st.add_annotation(self.AT.ARROW, x1=10, y1=10, x2=100, y2=100)
        self.assertIn(ann, self.st.current_annotations)
        self.assertIn(ann, self.st.current_screenshot.annotations)

    def test_undo_annotation(self):
        self.st.capture_full_screen()
        self.st.add_annotation(self.AT.RECTANGLE, x1=0, y1=0, x2=50, y2=50)
        self.st.add_annotation(self.AT.TEXT, x1=60, y1=60, text="Test")
        result = self.st.undo_annotation()
        self.assertTrue(result)
        self.assertEqual(len(self.st.current_annotations), 1)

    def test_undo_empty(self):
        result = self.st.undo_annotation()
        self.assertFalse(result)

    def test_set_tool(self):
        self.st.set_tool(self.AT.TEXT)
        self.assertEqual(self.st.active_tool, self.AT.TEXT)

    def test_delete_screenshot(self):
        name = self.st.screenshots[0].name
        result = self.st.delete_screenshot(name)
        self.assertTrue(result)

    def test_get_recent(self):
        recent = self.st.get_recent(3)
        self.assertEqual(len(recent), 3)

    def test_search(self):
        results = self.st.search("full")
        self.assertIsInstance(results, list)

    def test_get_stats(self):
        stats = self.st.get_stats()
        self.assertIn("total_screenshots", stats)
        self.assertIn("total_annotations", stats)

    def test_screenshot_resolution(self):
        from ui.screenshot_tool import Screenshot
        ss = Screenshot(width=2560, height=1440)
        self.assertEqual(ss.resolution, "2560×1440")

    def test_annotation_tool_icon(self):
        from ui.screenshot_tool import Annotation
        ann = Annotation(tool=self.AT.ARROW)
        self.assertEqual(ann.tool_icon, "➡️")

    def test_timer_progress(self):
        self.st.start_delay(3)
        bar = self.st.timer.progress_bar
        self.assertEqual(len(bar), 20)


class TestColorPicker(unittest.TestCase):
    def setUp(self):
        from ui.color_picker import ColorPicker, Color, ColorFormat
        self.cp = ColorPicker()
        self.Color = Color
        self.CF = ColorFormat

    def test_initial_state(self):
        self.assertGreater(len(self.cp.picked_history), 0)
        self.assertGreater(len(self.cp.palettes), 0)
        self.assertGreater(len(self.cp.saved_colors), 0)

    def test_set_color(self):
        c = self.cp.set_color(255, 128, 0)
        self.assertEqual(c.r, 255)
        self.assertEqual(c.g, 128)
        self.assertEqual(c.b, 0)

    def test_set_color_clamp(self):
        self.cp.set_color(300, -10, 128)
        self.assertEqual(self.cp.current_color.r, 255)
        self.assertEqual(self.cp.current_color.g, 0)

    def test_set_color_hex(self):
        c = self.cp.set_color_hex("#ff8800")
        self.assertEqual(c.r, 255)
        self.assertEqual(c.g, 136)
        self.assertEqual(c.b, 0)

    def test_pick_from_history(self):
        c = self.cp.pick_from_history(0)
        self.assertIsNotNone(c)

    def test_pick_from_history_invalid(self):
        c = self.cp.pick_from_history(999)
        self.assertIsNone(c)

    def test_generate_palette(self):
        from ui.color_picker import PaletteType
        base = self.Color(r=100, g=50, b=200)
        palette = self.cp.generate_palette(base, PaletteType.MONOCHROMATIC, 5)
        self.assertEqual(palette.palette_type, PaletteType.MONOCHROMATIC)
        self.assertGreater(len(palette.colors), 0)

    def test_check_contrast(self):
        result = self.cp.check_contrast(
            self.Color(r=255, g=255, b=255),
            self.Color(r=0, g=0, b=0))
        self.assertGreater(result.ratio, 10)
        self.assertTrue(result.aaa_normal)

    def test_check_contrast_low(self):
        result = self.cp.check_contrast(
            self.Color(r=200, g=200, b=200),
            self.Color(r=180, g=180, b=180))
        self.assertLess(result.ratio, 2)
        self.assertEqual(result.rating, "Fail")

    def test_simulate_color_blind(self):
        from ui.color_picker import ColorBlindType
        red = self.Color(r=255, g=0, b=0)
        sim = self.cp.simulate_color_blind(red, ColorBlindType.PROTANOPIA)
        self.assertNotEqual(sim.r, 255)
        self.assertIsNotNone(sim)

    def test_save_color(self):
        sc = self.cp.save_color("Test", self.Color(r=100, g=200, b=50))
        self.assertEqual(sc.name, "Test")
        self.assertIn(sc, self.cp.saved_colors)

    def test_get_complementary(self):
        red = self.Color(r=255, g=0, b=0)
        comp = self.cp.get_complementary(red)
        self.assertIsNotNone(comp)

    def test_get_analogous(self):
        colors = self.cp.get_analogous(self.Color(r=100, g=150, b=200))
        self.assertEqual(len(colors), 4)

    def test_color_hex(self):
        c = self.Color(r=26, g=26, b=46)
        self.assertEqual(c.hex, "#1a1a2e")

    def test_color_rgb(self):
        c = self.Color(r=255, g=128, b=0)
        self.assertEqual(c.rgb, "rgb(255, 128, 0)")

    def test_color_luminance(self):
        c = self.Color(r=255, g=255, b=255)
        self.assertGreater(c.luminance, 0.9)
        c2 = self.Color(r=0, g=0, b=0)
        self.assertAlmostEqual(c2.luminance, 0.0)

    def test_get_stats(self):
        stats = self.cp.get_stats()
        self.assertIn("picked_colors", stats)
        self.assertIn("palettes", stats)


class TestBenchmarkRunner(unittest.TestCase):
    def setUp(self):
        from ui.benchmark_runner import BenchmarkRunner, BenchmarkStatus
        self.br = BenchmarkRunner()
        self.BS = BenchmarkStatus

    def test_initial_state(self):
        self.assertGreater(len(self.br.suites), 0)
        self.assertGreater(len(self.br.results), 0)
        self.assertGreater(len(self.br.comparisons), 0)

    def test_run_suite(self):
        suite = self.br.run_suite("CPU Benchmark")
        self.assertIsNotNone(suite)
        self.assertEqual(suite.status, self.BS.COMPLETED)

    def test_run_suite_not_found(self):
        result = self.br.run_suite("Nonexistent")
        self.assertIsNone(result)

    def test_run_all(self):
        results = self.br.run_all()
        self.assertEqual(len(results), len(self.br.suites))

    def test_get_suite(self):
        suite = self.br.get_suite("CPU Benchmark")
        self.assertIsNotNone(suite)
        self.assertEqual(suite.category.value, "cpu")

    def test_get_results(self):
        results = self.br.get_results(5)
        self.assertGreater(len(results), 0)
        self.assertLessEqual(len(results), 5)

    def test_get_comparison(self):
        comp = self.br.get_comparison()
        self.assertGreater(len(comp), 0)
        self.assertGreaterEqual(comp[0].score, comp[1].score)

    def test_overall_score(self):
        score = self.br.get_overall_score()
        self.assertGreater(score, 0)

    def test_export_json(self):
        from ui.benchmark_runner import ExportConfig
        result = self.br.export_results(ExportConfig(format="json"))
        self.assertIn("system_info", result)
        self.assertIn("results", result)

    def test_export_csv(self):
        from ui.benchmark_runner import ExportConfig
        result = self.br.export_results(ExportConfig(format="csv"))
        self.assertIn("Suite,Score", result)

    def test_benchmark_test_score_display(self):
        from ui.benchmark_runner import BenchmarkTest
        t = BenchmarkTest(name="test", score=1500000)
        self.assertIn("M", t.score_display)
        t.score = 5000
        self.assertIn("K", t.score_display)
        t.score = 500
        self.assertIn("500.0", t.score_display)

    def test_benchmark_test_status_icon(self):
        from ui.benchmark_runner import BenchmarkTest
        t = BenchmarkTest(name="test", status=self.BS.COMPLETED)
        self.assertEqual(t.status_icon, "✅")

    def test_benchmark_suite_total_display(self):
        from ui.benchmark_runner import BenchmarkSuite
        s = BenchmarkSuite(name="test", total_score=2500000)
        self.assertIn("M", s.total_score_display)
        s.total_score = 5000
        self.assertIn("K", s.total_score_display)

    def test_comparison_bar(self):
        from ui.benchmark_runner import ComparisonEntry
        c = ComparisonEntry(label="test", score=250000)
        bar = c.bar
        self.assertEqual(len(bar), 50)

    def test_get_stats(self):
        stats = self.br.get_stats()
        self.assertIn("suites", stats)
        self.assertIn("overall_score", stats)


if __name__ == "__main__":
    unittest.main()
