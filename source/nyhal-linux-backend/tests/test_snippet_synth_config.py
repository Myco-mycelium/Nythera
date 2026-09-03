"""Tests for SnippetLibrary, Synthesizer, ConfigBackup"""
import time
import unittest

from ui.snippet_library import (
    SnippetLibrary, Snippet, Collection, SnippetLanguage,
    SnippetCategory, SortMode
)
from ui.synthesizer import (
    Synthesizer, Oscillator, Filter, LFO, Envelope, Effect,
    Waveform, FilterType, LFOShape, EffectType, SynthPreset, ArpeggioMode
)
from ui.config_backup import (
    ConfigBackup, ConfigFile, BackupSnapshot, DiffEntry,
    ConfigType, BackupStatus, DiffLineType
)


class TestSnippetLibrary(unittest.TestCase):
    def setUp(self):
        self.sl = SnippetLibrary()

    def test_initial_state(self):
        self.assertGreater(len(self.sl._snippets), 0)
        self.assertEqual(self.sl._selected, 0)

    def test_select(self):
        self.sl.select(1)
        self.assertEqual(self.sl._selected, 1)
        self.assertEqual(self.sl.selected_snippet.name, "HashMap with Default")

    def test_select_invalid(self):
        self.sl.select(99)
        self.assertEqual(self.sl._selected, 0)

    def test_total_snippets(self):
        self.assertEqual(self.sl.total_snippets, 12)

    def test_favorites(self):
        self.assertGreater(self.sl.favorites_count, 0)

    def test_total_uses(self):
        self.assertGreater(self.sl.total_uses, 0)

    def test_languages(self):
        langs = self.sl.languages_used
        self.assertIn(SnippetLanguage.PYTHON, langs)

    def test_search(self):
        results = self.sl.search("Fibonacci")
        self.assertEqual(len(results), 1)

    def test_search_code(self):
        results = self.sl.search("TcpListener")
        self.assertGreater(len(results), 0)

    def test_toggle_favorite(self):
        self.sl.select(1)
        before = self.sl.selected_snippet.is_favorite
        self.sl.toggle_favorite()
        self.assertNotEqual(self.sl.selected_snippet.is_favorite, before)

    def test_use_snippet(self):
        self.sl.use_snippet(0)
        self.assertEqual(self.sl._clipboard, self.sl._snippets[0].code)

    def test_add_snippet(self):
        before = self.sl.total_snippets
        s = Snippet("Test", "print('hello')", SnippetLanguage.PYTHON, SnippetCategory.SNIPPET)
        self.sl.add_snippet(s)
        self.assertEqual(self.sl.total_snippets, before + 1)

    def test_delete_snippet(self):
        before = self.sl.total_snippets
        self.sl.delete_snippet(0)
        self.assertEqual(self.sl.total_snippets, before - 1)

    def test_delete_invalid(self):
        self.assertFalse(self.sl.delete_snippet(99))

    def test_collections(self):
        self.assertGreater(len(self.sl._collections), 0)

    def test_filter_by_language(self):
        self.sl.filter_by_language(SnippetLanguage.PYTHON)
        filtered = self.sl.get_filtered()
        self.assertTrue(all(s.language == SnippetLanguage.PYTHON for s in filtered))

    def test_snippet_preview(self):
        s = self.sl.selected_snippet
        self.assertGreater(len(s.preview), 0)

    def test_snippet_line_count(self):
        s = self.sl.selected_snippet
        self.assertGreater(s.line_count, 0)

    def test_render(self):
        lines = self.sl.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("SNIPPET" in l for l in lines))

    def test_render_snippet(self):
        self.sl.select(0)
        lines = self.sl.render_snippet()
        self.assertGreater(len(lines), 5)

    def test_sort_modes(self):
        self.assertEqual(len(SortMode), 6)


class TestSynthesizer(unittest.TestCase):
    def setUp(self):
        self.syn = Synthesizer()

    def test_initial_simple(self):
        self.assertGreater(len(self.syn._oscillators), 0)
        self.assertIsNotNone(self.syn._filter)
        self.assertIsNotNone(self.syn._envelope)
        self.assertIsNotNone(self.syn._lfo)
        self.assertGreater(len(self.syn._effects), 0)

    def test_select_preset(self):
        self.syn.select_preset(1)
        self.assertEqual(self.syn._selected_preset, 1)
        self.assertEqual(self.syn.selected_preset.name, "808 Bass")

    def test_select_invalid(self):
        self.syn.select_preset(99)
        self.assertEqual(self.syn._selected_preset, 0)

    def test_total_presets(self):
        self.assertEqual(self.syn.total_presets, 5)

    def test_note_on_off(self):
        self.syn.note_on("C4")
        self.assertIn("C4", self.syn._active_notes)
        self.syn.note_off("C4")
        self.assertNotIn("C4", self.syn._active_notes)

    def test_active_voices(self):
        self.syn.note_on("C4")
        self.syn.note_on("E4")
        self.assertEqual(self.syn._active_voices, 2)

    def test_waveform_str(self):
        w = self.syn.waveform_str
        self.assertEqual(len(w), 64)

    def test_osc_volume_bar(self):
        osc = self.syn._oscillators[0]
        self.assertIn("█", osc.volume_bar)

    def test_osc_waveform_icon(self):
        osc = self.syn._oscillators[0]
        self.assertIn(osc.waveform_icon, ["∿", "⌇", "⩘", "△", "⏍", "▓"])

    def test_filter_cutoff_bar(self):
        f = self.syn._filter
        self.assertIn("█", f.cutoff_bar)

    def test_lfo_rate_bar(self):
        l = self.syn._lfo
        self.assertIn("█", l.rate_bar)

    def test_envelope_bars(self):
        e = self.syn._envelope
        # attack is 10ms, may be 0 filled bars; just check it returns a string
        self.assertIsInstance(e.attack_bar, str)
        self.assertEqual(len(e.attack_bar), 10)

    def test_effect_mix_bar(self):
        eff = self.syn._effects[0]
        self.assertIn("█", eff.mix_bar)

    def test_render(self):
        lines = self.syn.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("SYNTHESIZER" in l for l in lines))

    def test_render_presets(self):
        lines = self.syn.render_presets()
        self.assertGreater(len(lines), 3)

    def test_render_preset_detail(self):
        self.syn.select_preset(0)
        lines = self.syn.render_preset_detail()
        self.assertGreater(len(lines), 3)

    def test_waveform_types(self):
        self.assertEqual(len(Waveform), 6)

    def test_filter_types(self):
        self.assertEqual(len(FilterType), 6)

    def test_effect_types(self):
        self.assertEqual(len(EffectType), 8)


class TestConfigBackup(unittest.TestCase):
    def setUp(self):
        self.cb = ConfigBackup()

    def test_initial_state(self):
        self.assertGreater(len(self.cb._config_files), 0)
        self.assertGreater(len(self.cb._snapshots), 0)

    def test_select_file(self):
        self.cb.select_file(1)
        self.assertEqual(self.cb._selected_file, 1)

    def test_select_invalid(self):
        self.cb.select_file(99)
        self.assertEqual(self.cb._selected_file, 0)

    def test_total_files(self):
        self.assertEqual(self.cb.total_files, 7)

    def test_total_snapshots(self):
        self.assertEqual(self.cb.total_snapshots, 4)

    def test_critical_files(self):
        self.assertGreater(self.cb.critical_files, 0)

    def test_type_counts(self):
        counts = self.cb.type_counts
        self.assertIn("system", counts)

    def test_create_snapshot(self):
        before = self.cb.total_snapshots
        self.cb.create_snapshot("Test Backup", "Test")
        self.assertEqual(self.cb.total_snapshots, before + 1)

    def test_select_snapshot(self):
        self.cb.select_snapshot(1)
        self.assertIsNotNone(self.cb.selected_snapshot)

    def test_config_file_properties(self):
        f = self.cb._config_files[0]
        self.assertGreater(f.size_bytes, 0)
        self.assertEqual(f.filename, "compositor.toml")

    def test_snapshot_properties(self):
        s = self.cb._snapshots[0]
        self.assertGreater(s.file_count, 0)
        self.assertIn("KB", s.display_size)

    def test_diff_entries(self):
        self.assertGreater(len(self.cb._diff_entries), 0)

    def test_compare_snapshots(self):
        diffs = self.cb.compare_snapshots(0, 3)
        self.assertIsInstance(diffs, list)

    def test_render(self):
        lines = self.cb.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("BACKUP" in l for l in lines))

    def test_render_file_detail(self):
        self.cb.select_file(0)
        lines = self.cb.render_file_detail()
        self.assertGreater(len(lines), 5)

    def test_render_diff(self):
        lines = self.cb.render_diff()
        self.assertGreater(len(lines), 3)

    def test_auto_backup(self):
        self.assertTrue(self.cb._auto_backup)

    def test_config_types(self):
        self.assertEqual(len(ConfigType), 8)


if __name__ == "__main__":
    unittest.main()
