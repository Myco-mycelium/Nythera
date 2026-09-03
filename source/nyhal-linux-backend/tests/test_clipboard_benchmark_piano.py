"""Tests for ClipboardPro, BenchmarkSuite, VirtualPiano"""
import time
import unittest

from ui.clipboard_pro import (
    ClipboardPro, ClipEntry, Snippet, SyncDevice,
    ClipType, SnippetCategory, SyncStatus
)
from ui.benchmark_suite import (
    BenchmarkSuite, BenchResult, BenchJob, ComparisonEntry,
    BenchCategory, BenchStatus, SystemTier
)
from ui.virtual_piano import (
    VirtualPiano, PianoNote, PianoEffectInstance, PianoRecording, PresetSound,
    NoteName, PianoEffect, PianoScale
)


class TestClipboardPro(unittest.TestCase):
    def setUp(self):
        self.cp = ClipboardPro()

    def test_initial_state(self):
        self.assertGreater(len(self.cp._entries), 0)
        self.assertEqual(self.cp._selected, 0)

    def test_select(self):
        self.cp.select(1)
        self.assertEqual(self.cp._selected, 1)

    def test_select_invalid(self):
        self.cp.select(99)
        self.assertEqual(self.cp._selected, 0)

    def test_total_entries(self):
        self.assertGreater(self.cp.total_entries, 0)

    def test_pinned_count(self):
        self.assertGreater(self.cp.pinned_count, 0)

    def test_favorite_count(self):
        self.assertGreaterEqual(self.cp.favorite_count, 0)

    def test_total_copies(self):
        self.assertGreater(self.cp.total_copies, 0)

    def test_copy_entry(self):
        content = self.cp.copy_entry(0)
        self.assertGreater(len(content), 0)

    def test_pin_entry(self):
        self.cp.pin_entry(1)
        self.assertTrue(self.cp._entries[1].is_pinned)

    def test_delete_entry(self):
        before = self.cp.total_entries
        self.cp.delete_entry(0)
        self.assertEqual(self.cp.total_entries, before - 1)

    def test_delete_invalid(self):
        self.assertFalse(self.cp.delete_entry(99))

    def test_search(self):
        results = self.cp.search("fibonacci")
        self.assertGreater(len(results), 0)

    def test_entry_preview(self):
        e = self.cp.selected_entry
        self.assertGreater(len(e.preview), 0)

    def test_entry_age(self):
        e = self.cp.selected_entry
        self.assertGreater(len(e.age_display), 0)

    def test_entry_type_icon(self):
        e = self.cp._entries[0]
        self.assertIn(e.type_icon, ["📝", "💻", "🔗", "🖼️", "📁", "🎨", "🔐"])

    def test_snippets(self):
        self.assertGreater(len(self.cp._snippets), 0)

    def test_devices(self):
        self.assertGreater(len(self.cp._devices), 0)

    def test_render(self):
        lines = self.cp.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("CLIPBOARD" in l for l in lines))

    def test_render_entry_detail(self):
        self.cp.select(0)
        lines = self.cp.render_entry_detail()
        self.assertGreater(len(lines), 5)


class TestBenchmarkSuite(unittest.TestCase):
    def setUp(self):
        self.bs = BenchmarkSuite()

    def test_initial_state(self):
        self.assertGreater(len(self.bs._results), 0)
        self.assertGreater(len(self.bs._comparisons), 0)

    def test_select_result(self):
        self.bs.select_result(1)
        self.assertEqual(self.bs._selected_result, 1)

    def test_select_comparison(self):
        self.bs.select_comparison(1)
        self.assertEqual(self.bs._selected_comparison, 1)

    def test_select_invalid(self):
        self.bs.select_result(99)
        self.assertEqual(self.bs._selected_result, 0)

    def test_total_benchmarks(self):
        self.assertEqual(self.bs.total_benchmarks, 11)

    def test_overall_score(self):
        self.assertGreater(self.bs.overall_score, 0)

    def test_result_score_display(self):
        r = self.bs._results[0]
        self.assertIn("pts", r.score_display)

    def test_result_percentile(self):
        r = self.bs._results[0]
        self.assertIn(r.percentile, ["Top 1%", "Top 10%", "Above Avg", "Below Avg"])

    def test_result_score_bar(self):
        r = self.bs._results[0]
        self.assertIn("█", r.score_bar)

    def test_comparison_overall(self):
        c = self.bs._comparisons[0]
        self.assertGreater(c.overall_score, 0)

    def test_comparison_tier_icon(self):
        c = self.bs._comparisons[0]
        self.assertIn(c.tier_icon, ["🏆", "⚖️", "💰", "🖥️", "📦"])

    def test_jobs(self):
        self.assertGreater(len(self.bs._jobs), 0)

    def test_render(self):
        lines = self.bs.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("BENCHMARK" in l for l in lines))

    def test_render_result_detail(self):
        self.bs.select_result(0)
        lines = self.bs.render_result_detail()
        self.assertGreater(len(lines), 5)

    def test_render_comparison(self):
        lines = self.bs.render_comparison()
        self.assertGreater(len(lines), 5)


class TestVirtualPiano(unittest.TestCase):
    def setUp(self):
        self.vp = VirtualPiano()

    def test_initial_state(self):
        self.assertEqual(self.vp._octave, 4)
        self.assertFalse(self.vp._is_recording)
        self.assertGreater(len(self.vp._effects), 0)

    def test_note_on_off(self):
        self.vp.note_on(NoteName.C, 4, 100)
        self.assertEqual(len(self.vp._active_notes), 1)
        self.vp.note_off(NoteName.C, 4)
        self.assertEqual(len(self.vp._active_notes), 0)

    def test_active_notes_count(self):
        self.vp.note_on(NoteName.C, 4, 100)
        self.vp.note_on(NoteName.E, 4, 90)
        self.assertEqual(self.vp.active_notes_count, 2)

    def test_start_stop_recording(self):
        self.vp.start_recording()
        self.assertTrue(self.vp._is_recording)
        self.vp.stop_recording()
        self.assertFalse(self.vp._is_recording)

    def test_recording_captures_notes(self):
        self.vp.start_recording()
        self.vp.note_on(NoteName.C, 4, 100)
        self.vp.note_on(NoteName.E, 4, 90)
        self.vp.stop_recording()
        self.assertEqual(len(self.vp._recordings[-1].notes), 2)

    def test_toggle_sustain(self):
        self.vp.toggle_sustain()
        self.assertTrue(self.vp._sustain)
        self.vp.toggle_sustain()
        self.assertFalse(self.vp._sustain)

    def test_select_recording(self):
        self.vp.select_recording(1)
        self.assertEqual(self.vp._selected_recording, 1)

    def test_total_recordings(self):
        self.assertGreater(self.vp.total_recordings, 0)

    def test_note_midi_number(self):
        note = PianoNote(NoteName.C, 4, 100, time.time())
        self.assertEqual(note.midi_number, 60)

    def test_note_display_name(self):
        note = PianoNote(NoteName.A, 3, 100, time.time())
        self.assertEqual(note.display_name, "A3")

    def test_note_is_black_key(self):
        note = PianoNote(NoteName.C_SHARP, 4, 100, time.time())
        self.assertTrue(note.is_black_key)
        note2 = PianoNote(NoteName.C, 4, 100, time.time())
        self.assertFalse(note2.is_black_key)

    def test_effect_mix_bar(self):
        eff = PianoEffectInstance(PianoEffect.REVERB, 0.7)
        self.assertIn("█", eff.mix_bar)

    def test_recording_note_count(self):
        rec = self.vp.selected_recording
        self.assertGreater(rec.note_count, 0)

    def test_recording_duration(self):
        rec = self.vp.selected_recording
        self.assertGreater(len(rec.display_duration), 0)

    def test_presets(self):
        self.assertGreater(len(self.vp._presets), 0)

    def test_preset_attack(self):
        p = self.vp.selected_preset
        self.assertGreater(p.attack, 0)

    def test_render(self):
        lines = self.vp.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("PIANO" in l for l in lines))

    def test_render_recordings(self):
        self.vp.select_recording(0)
        lines = self.vp.render_recordings()
        self.assertGreater(len(lines), 3)

    def test_render_presets(self):
        lines = self.vp.render_presets()
        self.assertGreater(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
