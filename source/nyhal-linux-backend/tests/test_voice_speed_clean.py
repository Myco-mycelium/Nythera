"""Tests for VoiceSynth, SpeedTest, SystemCleaner"""
import time
import unittest

from ui.voice_synth import (
    VoiceSynth, SynthSegment, SynthJob, VoiceEffect, PronunciationEntry,
    VoiceType, SpeechRate, EffectType, ExportFormat, SSMLTag
)
from ui.speed_test import (
    SpeedTest, SpeedServer, TestResult, PingResult,
    TestPhase, ServerRegion
)
from ui.system_cleaner import (
    SystemCleaner, CleanItem, CleanRule, CleanJob,
    CleanCategory, CleanPriority, CleanStatus
)


class TestVoiceSynth(unittest.TestCase):
    def setUp(self):
        self.vs = VoiceSynth()

    def test_initial_state(self):
        self.assertEqual(self.vs._text, "")
        self.assertEqual(self.vs._voice, VoiceType.MALE_NORMAL)
        self.assertFalse(self.vs._is_playing)

    def test_synthesize(self):
        segments = self.vs.synthesize("Hello world test")
        self.assertGreater(len(segments), 0)
        self.assertGreater(self.vs._total_duration_ms, 0)

    def test_synthesize_long(self):
        text = "This is a longer text that should be split into multiple segments for better synthesis quality."
        segments = self.vs.synthesize(text)
        self.assertGreater(len(segments), 1)

    def test_segments_duration(self):
        self.vs.synthesize("Test")
        self.assertGreater(self.vs._total_duration_ms, 0)

    def test_add_effect(self):
        before = len(self.vs._effects)
        self.vs.add_effect(VoiceEffect(EffectType.ECHO, 0.5))
        self.assertEqual(len(self.vs._effects), before + 1)

    def test_remove_effect(self):
        before = len(self.vs._effects)
        self.vs.remove_effect(0)
        self.assertEqual(len(self.vs._effects), before - 1)

    def test_add_pronunciation(self):
        before = len(self.vs._pronunciations)
        self.vs.add_pronunciation("test", "tehst")
        self.assertEqual(len(self.vs._pronunciations), before + 1)

    def test_total_jobs(self):
        self.assertGreater(self.vs.total_jobs, 0)

    def test_total_duration(self):
        self.assertGreater(len(self.vs.total_duration), 0)

    def test_select_job(self):
        self.vs.select_job(1)
        self.assertEqual(self.vs._selected_job, 1)

    def test_effect_mix_bar(self):
        eff = VoiceEffect(EffectType.REVERB, 0.7)
        self.assertIn("█", eff.mix_bar)

    def test_segment_display(self):
        seg = SynthSegment("Hello", VoiceType.MALE_NORMAL, 1.0, 0, 0.8, duration_ms=1500)
        self.assertEqual(seg.display_duration, "1.5s")

    def test_render(self):
        lines = self.vs.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("SYNTHESIZER" in l for l in lines))

    def test_render_voices(self):
        lines = self.vs.render_voices()
        self.assertGreater(len(lines), 5)

    def test_render_effects(self):
        lines = self.vs.render_effects()
        self.assertGreater(len(lines), 5)

    def test_render_pronunciations(self):
        lines = self.vs.render_pronunciations()
        self.assertGreater(len(lines), 3)

    def test_pronunciation_entry(self):
        p = PronunciationEntry("Nyrqis", "nair-kiss")
        self.assertEqual(p.language, "en-US")


class TestSpeedTest(unittest.TestCase):
    def setUp(self):
        self.st = SpeedTest()

    def test_initial_state(self):
        self.assertGreater(len(self.st._servers), 0)
        self.assertEqual(self.st._phase, TestPhase.IDLE)

    def test_select_server(self):
        self.st.select_server(1)
        self.assertEqual(self.st._selected_server, 1)

    def test_select_invalid(self):
        self.st.select_server(99)
        self.assertEqual(self.st._selected_server, 0)

    def test_best_server(self):
        best = self.st.best_server
        self.assertIsNotNone(best)
        self.assertEqual(best.name, "Nyrqis East")

    def test_total_tests(self):
        self.assertGreater(self.st.total_tests, 0)

    def test_server_ping_bar(self):
        srv = self.st._servers[0]
        self.assertIn("█", srv.ping_bar)

    def test_server_ping_status(self):
        srv = self.st._servers[0]
        self.assertIn(srv.ping_status, ["Excellent", "Good", "Fair", "Poor", "N/A"])

    def test_server_distance(self):
        srv = self.st._servers[1]
        self.assertIn("km", srv.distance_display)

    def test_ping_result_loss(self):
        p = PingResult("8.8.8.8", 100, 100, 8, 15, 11, 2, time.time())
        self.assertEqual(p.packet_loss, 0)

    def test_ping_result_loss_partial(self):
        p = PingResult("8.8.8.8", 100, 90, 8, 15, 11, 2, time.time())
        self.assertAlmostEqual(p.packet_loss, 10, places=0)

    def test_ping_latency_bar(self):
        p = PingResult("8.8.8.8", 100, 100, 8, 15, 11, 2, time.time())
        self.assertIn("█", p.latency_bar)

    def test_result_value_display(self):
        r = TestResult(time.time(), TestPhase.DOWNLOAD, 945.2, "Mbps")
        self.assertIn("Mbps", r.value_display)

    def test_render(self):
        lines = self.st.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("SPEED TEST" in l for l in lines))

    def test_render_ping(self):
        lines = self.st.render_ping()
        self.assertGreater(len(lines), 3)

    def test_render_history(self):
        lines = self.st.render_history()
        self.assertGreater(len(lines), 5)

    def test_start_test(self):
        self.st.start_test()
        self.assertTrue(self.st._is_running)
        self.assertEqual(self.st._phase, TestPhase.PING)


class TestSystemCleaner(unittest.TestCase):
    def setUp(self):
        self.sc = SystemCleaner()

    def test_initial_state(self):
        self.assertGreater(len(self.sc._items), 0)
        self.assertGreater(len(self.sc._rules), 0)

    def test_select_item(self):
        self.sc.select_item(1)
        self.assertEqual(self.sc._selected_item, 1)

    def test_select_invalid(self):
        self.sc.select_item(99)
        self.assertEqual(self.sc._selected_item, 0)

    def test_total_size(self):
        self.assertGreater(self.sc.total_size_cleanable, 0)
        self.assertGreater(len(self.sc.total_size_display), 0)

    def test_category_totals(self):
        totals = self.sc.category_totals
        self.assertGreater(len(totals), 0)

    def test_clean_item(self):
        for i, item in enumerate(self.sc._items):
            if item.status == CleanStatus.PENDING and item.is_safe:
                self.assertTrue(self.sc.clean_item(i))
                break

    def test_clean_all_safe(self):
        count = self.sc.clean_all_safe()
        self.assertGreater(count, 0)

    def test_skip_item(self):
        self.sc.skip_item(0)
        self.assertEqual(self.sc._items[0].status, CleanStatus.SKIPPED)

    def test_item_display_size(self):
        item = self.sc._items[0]
        self.assertGreater(len(item.display_size), 0)

    def test_item_age(self):
        item = self.sc._items[0]
        self.assertIsInstance(item.age_display, str)

    def test_item_priority_icon(self):
        item = self.sc._items[0]
        self.assertIn(item.priority_icon, ["🟢", "🟡", "🟠", "🔴"])

    def test_rules(self):
        self.assertEqual(len(self.sc._rules), 7)

    def test_jobs(self):
        self.assertGreater(len(self.sc._jobs), 0)

    def test_job_freed_display(self):
        j = self.sc._jobs[0]
        self.assertGreater(len(j.freed_display), 0)

    def test_render(self):
        lines = self.sc.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("CLEANER" in l for l in lines))

    def test_render_rules(self):
        lines = self.sc.sc.render_rules() if hasattr(self.sc, 'sc') else self.sc.render_rules()
        self.assertGreater(len(lines), 3)

    def test_clean_status(self):
        self.assertEqual(self.sc._items[0].status, CleanStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
