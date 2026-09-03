"""Tests for DuplicateFinder, AsciiArt, ResourceLimiter"""
import time
import unittest

from ui.duplicate_finder import (
    DuplicateFinder, DuplicateEntry, DuplicateGroupEntry, ScanResult,
    HashMethod, FileStatus
)
from ui.ascii_art import (
    AsciiArt, ArtPiece, ArtStyle, PatternType, Charset
)
from ui.resource_limiter import (
    ResourceLimiter, AppProfile, ResourceRule, ProcessLimit, SystemLimits,
    ResourceLimit, LimitScope, ProfileType, CGroupVersion
)


class TestDuplicateFinder(unittest.TestCase):
    def setUp(self):
        self.df = DuplicateFinder()

    def test_initial_state(self):
        self.assertGreater(len(self.df._groups), 0)
        self.assertEqual(self.df._selected_group, 0)

    def test_select_group(self):
        self.df.select_group(1)
        self.assertEqual(self.df._selected_group, 1)

    def test_select_invalid(self):
        self.df.select_group(99)
        self.assertEqual(self.df._selected_group, 0)

    def test_total_duplicates(self):
        self.assertGreater(self.df.total_duplicates, 0)

    def test_total_waste(self):
        self.assertGreater(self.df.total_waste, 0)
        self.assertGreater(len(self.df.total_waste_display), 0)

    def test_total_files(self):
        self.assertGreater(self.df.total_files, 0)

    def test_mark_delete(self):
        self.df.mark_delete(0)
        self.assertEqual(self.df._entries[0].status, FileStatus.DELETE)

    def test_mark_keep(self):
        self.df.mark_keep(0)
        self.assertEqual(self.df._entries[0].status, FileStatus.KEEP)

    def test_auto_select(self):
        self.df.auto_select()
        # After auto-select, first file in each group should be KEEP
        for g in self.df._groups:
            if g.files:
                self.assertEqual(g.files[0].status, FileStatus.KEEP)

    def test_entry_display_size(self):
        e = DuplicateEntry("/test", 2_457_600, "abc", time.time())
        self.assertIn("MB", e.display_size)

    def test_entry_hash_display(self):
        e = DuplicateEntry("/test", 1000, "a1b2c3d4e5f6g7h8", time.time())
        self.assertIn("...", e.hash_display)

    def test_group_waste_display(self):
        g = DuplicateGroupEntry(1, [], 1048576, 1048576)
        self.assertIn("MB", g.waste_display)

    def test_scan_results(self):
        self.assertGreater(len(self.df._scan_results), 0)

    def test_compute_hash(self):
        result = DuplicateFinder.compute_hash("test", HashMethod.SHA256)
        self.assertEqual(len(result), 64)

    def test_compute_hash_md5(self):
        result = DuplicateFinder.compute_hash("test", HashMethod.MD5)
        self.assertEqual(len(result), 32)

    def test_render(self):
        lines = self.df.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("FINDER" in l for l in lines))

    def test_render_group_detail(self):
        self.df.select_group(0)
        lines = self.df.render_group_detail()
        self.assertGreater(len(lines), 3)


class TestAsciiArt(unittest.TestCase):
    def setUp(self):
        self.aa = AsciiArt()

    def test_initial_state(self):
        self.assertGreater(len(self.aa._pieces), 0)
        self.assertEqual(self.aa._current_text, "Nyrqis")

    def test_select_piece(self):
        self.aa.select_piece(1)
        self.assertEqual(self.aa._selected_piece, 1)

    def test_select_invalid(self):
        self.aa.select_piece(99)
        self.assertEqual(self.aa._selected_piece, 0)

    def test_total_pieces(self):
        self.assertEqual(self.aa.total_pieces, 4)

    def test_favorites(self):
        self.assertGreater(self.aa.favorites_count, 0)

    def test_toggle_favorite(self):
        self.aa.select_piece(1)
        before = self.aa.selected_piece.is_favorite
        self.aa.toggle_favorite()
        self.assertNotEqual(self.aa.selected_piece.is_favorite, before)

    def test_generate_text_art(self):
        art = self.aa.generate_text_art("NY")
        self.assertIn("█", art)

    def test_generate_text_art_empty(self):
        art = self.aa.generate_text_art("")
        self.assertIsInstance(art, str)

    def test_generate_pattern_gradient(self):
        art = self.aa.generate_pattern(PatternType.GRADIENT, 10, 5)
        self.assertEqual(len(art.split("\n")), 5)

    def test_generate_pattern_wave(self):
        art = self.aa.generate_pattern(PatternType.WAVE, 10, 5)
        self.assertIn("@", art)

    def test_generate_pattern_checkerboard(self):
        art = self.aa.generate_pattern(PatternType.CHECKERBOARD, 10, 5)
        self.assertEqual(len(art.split("\n")), 5)

    def test_generate_pattern_diamond(self):
        art = self.aa.generate_pattern(PatternType.DIAMOND, 10, 5)
        self.assertIn("@", art)

    def test_generate_pattern_circle(self):
        art = self.aa.generate_pattern(PatternType.CIRCLE, 10, 5)
        self.assertIsInstance(art, str)

    def test_generate_pattern_heart(self):
        art = self.aa.generate_pattern(PatternType.HEART, 10, 5)
        self.assertIsInstance(art, str)

    def test_piece_preview(self):
        p = self.aa.selected_piece
        self.assertGreater(len(p.preview), 0)

    def test_piece_line_count(self):
        p = self.aa.selected_piece
        self.assertGreater(p.line_count, 0)

    def test_render(self):
        lines = self.aa.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("ASCII" in l for l in lines))

    def test_render_preview(self):
        self.aa.select_piece(0)
        lines = self.aa.render_preview()
        self.assertGreater(len(lines), 5)

    def test_render_pattern(self):
        lines = self.aa.render_pattern()
        self.assertGreater(len(lines), 3)


class TestResourceLimiter(unittest.TestCase):
    def setUp(self):
        self.rl = ResourceLimiter()

    def test_initial_state(self):
        self.assertGreater(len(self.rl._profiles), 0)
        self.assertGreater(len(self.rl._process_limits), 0)
        self.assertIsNotNone(self.rl._system_limits)

    def test_select_profile(self):
        self.rl.select_profile(1)
        self.assertEqual(self.rl._selected_profile, 1)

    def test_select_process(self):
        self.rl.select_process(1)
        self.assertEqual(self.rl._selected_process, 1)

    def test_select_invalid(self):
        self.rl.select_profile(99)
        self.assertEqual(self.rl._selected_profile, 0)

    def test_total_profiles(self):
        self.assertEqual(self.rl.total_profiles, 4)

    def test_active_profiles(self):
        self.assertGreater(self.rl.active_profiles, 0)

    def test_throttled_processes(self):
        self.assertGreater(self.rl.throttled_processes, 0)

    def test_toggle_profile(self):
        self.rl.toggle_profile(0)
        self.assertFalse(self.rl._profiles[0].is_active)

    def test_add_rule(self):
        rule = ResourceRule("Test", ResourceLimit.CPU, 50, "%")
        before = len(self.rl._profiles[0].rules)
        self.rl.add_rule(0, rule)
        self.assertEqual(len(self.rl._profiles[0].rules), before + 1)

    def test_rule_value_display(self):
        r = ResourceRule("CPU", ResourceLimit.CPU, 80, "%")
        self.assertEqual(r.value_display, "80%")

    def test_rule_bar(self):
        r = ResourceRule("CPU", ResourceLimit.CPU, 80, "%")
        self.assertIn("█", r.bar)

    def test_process_cpu_bar(self):
        p = self.rl._process_limits[0]
        self.assertIn("█", p.cpu_bar)

    def test_process_memory_display(self):
        p = self.rl._process_limits[0]
        self.assertIn("GB", p.memory_display)

    def test_system_limits(self):
        s = self.rl._system_limits
        self.assertEqual(s.total_cpu_cores, 16)
        self.assertTrue(s.oom_enabled)

    def test_render(self):
        lines = self.rl.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("LIMITER" in l for l in lines))

    def test_render_profile_detail(self):
        self.rl.select_profile(0)
        lines = self.rl.render_profile_detail()
        self.assertGreater(len(lines), 3)

    def test_render_system(self):
        lines = self.rl.render_system()
        self.assertGreater(len(lines), 5)


if __name__ == "__main__":
    unittest.main()
