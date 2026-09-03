"""Tests for PuzzleEngine, DJConsole, SystemWatchdog"""
import time
import unittest

from ui.puzzle_engine import (
    PuzzleEngine, SudokuCell, CrosswordClue, WordSearchWord, PuzzleStats,
    PuzzleType, Difficulty, CellState
)
from ui.dj_console import (
    DJConsole, DeckState, Track, DJEffect, DeckSide,
    PlaybackState, LoopSize, FXType, EQBand
)
from ui.system_watchdog import (
    SystemWatchdog, HealthCheck, Alert, RecoveryLog, WatchdogConfig,
    CheckType, CheckStatus, RecoveryAction, AlertLevel
)


class TestPuzzleEngine(unittest.TestCase):
    def setUp(self):
        self.pe = PuzzleEngine()

    def test_initial_state(self):
        self.assertGreater(len(self.pe._sudoku_board), 0)
        self.assertEqual(len(self.pe._sudoku_board), 9)

    def test_sudoku_board_size(self):
        for row in self.pe._sudoku_board:
            self.assertEqual(len(row), 9)

    def test_sudoku_locked_cells(self):
        locked = sum(1 for row in self.pe._sudoku_board for cell in row if cell.state == CellState.LOCKED)
        self.assertGreater(locked, 0)

    def test_place_value(self):
        result = self.pe.place_sudoku_value(0, 2, 4)
        self.assertIn("4", result)

    def test_place_locked(self):
        # Find a locked cell
        for r in range(9):
            for c in range(9):
                if self.pe._sudoku_board[r][c].state == CellState.LOCKED:
                    result = self.pe.place_sudoku_value(r, c, 5)
                    self.assertIn("locked", result)
                    return

    def test_place_conflict(self):
        result = self.pe.place_sudoku_value(0, 2, 5)  # 5 already in row
        self.assertIn("conflict", result.lower())

    def test_crossword_clues(self):
        self.assertGreater(len(self.pe._crossword_clues), 0)

    def test_word_search_grid(self):
        self.assertEqual(len(self.pe._word_search_grid), 10)

    def test_word_search_words(self):
        self.assertGreater(len(self.pe._word_search_words), 0)

    def test_find_word(self):
        result = self.pe.find_word_search("PYTHON")
        self.assertTrue(result)

    def test_stats(self):
        self.assertGreater(self.pe._stats.puzzles_played, 0)
        self.assertGreater(self.pe._stats.solve_rate, 0)

    def test_use_hint(self):
        result = self.pe.use_hint()
        self.assertIn("Hint", result)
        self.assertEqual(self.pe._hints_remaining, 2)

    def test_use_hint_exhausted(self):
        for _ in range(3):
            self.pe.use_hint()
        result = self.pe.use_hint()
        self.assertIn("No hints", result)

    def test_timer(self):
        self.pe._timer_secs = 125
        self.assertEqual(self.pe.timer_display, "2:05")

    def test_cell_display(self):
        cell = SudokuCell(0, 0, 5, CellState.LOCKED)
        self.assertEqual(cell.display, "5")

    def test_cell_display_empty(self):
        cell = SudokuCell(0, 0, 0, CellState.EMPTY)
        self.assertEqual(cell.display, "·")

    def test_render(self):
        lines = self.pe.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("PUZZLE" in l for l in lines))

    def test_render_stats(self):
        lines = self.pe.render_stats()
        self.assertGreater(len(lines), 5)


class TestDJConsole(unittest.TestCase):
    def setUp(self):
        self.dj = DJConsole()

    def test_initial_state(self):
        self.assertIsNotNone(self.dj._deck_a.track)
        self.assertIsNotNone(self.dj._deck_b.track)
        self.assertEqual(self.dj._crossfader, 0.5)

    def test_select_deck(self):
        self.dj.select_deck(DeckSide.B)
        self.assertEqual(self.dj._selected_deck, DeckSide.B)

    def test_toggle_play(self):
        self.dj.toggle_play(DeckSide.A)
        self.assertEqual(self.dj._deck_a.state, PlaybackState.PAUSED)
        self.dj.toggle_play(DeckSide.A)
        self.assertEqual(self.dj._deck_a.state, PlaybackState.PLAYING)

    def test_set_cue(self):
        self.dj._deck_a.position_secs = 45.0
        self.dj.set_cue(DeckSide.A)
        self.assertEqual(self.dj._deck_a.cue_point, 45.0)

    def test_sync_bpm(self):
        self.dj.sync_bpm(DeckSide.A)
        self.assertEqual(self.dj._deck_a.track.bpm, self.dj._deck_b.track.bpm)

    def test_crossfader(self):
        self.dj.set_crossfader(0.7)
        self.assertEqual(self.dj._crossfader, 0.7)

    def test_volume(self):
        self.dj.set_volume(DeckSide.A, 0.6)
        self.assertEqual(self.dj._deck_a.volume, 0.6)

    def test_eq(self):
        self.dj.set_eq(DeckSide.A, EQBand.LOW, 0.3)
        self.assertEqual(self.dj._deck_a.eq_low, 0.3)

    def test_load_track(self):
        self.dj.load_track(DeckSide.A, 2)
        self.assertEqual(self.dj._deck_a.track.title, "Deep Groove")

    def test_track_duration(self):
        t = Track("Test", "Artist", 120, 245)
        self.assertEqual(t.duration_display, "4:05")

    def test_deck_position_bar(self):
        deck = self.dj._deck_a
        self.assertIn("█", deck.position_bar)

    def test_deck_waveform(self):
        deck = self.dj._deck_a
        self.assertEqual(len(deck.waveform), 32)

    def test_effect_mix_bar(self):
        eff = DJEffect(FXType.ECHO, 0.7)
        self.assertIn("█", eff.mix_bar)

    def test_render(self):
        lines = self.dj.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("DJ CONSOLE" in l for l in lines))

    def test_render_deck_detail(self):
        lines = self.dj.render_deck_detail(DeckSide.A)
        self.assertGreater(len(lines), 3)

    def test_track_library(self):
        self.assertGreater(len(self.dj._track_library), 0)


class TestSystemWatchdog(unittest.TestCase):
    def setUp(self):
        self.sw = SystemWatchdog()

    def test_initial_state(self):
        self.assertGreater(len(self.sw._checks), 0)
        self.assertFalse(self.sw._is_running)

    def test_select_check(self):
        self.sw.select_check(1)
        self.assertEqual(self.sw._selected_check, 1)

    def test_select_invalid(self):
        self.sw.select_check(99)
        self.assertEqual(self.sw._selected_check, 0)

    def test_total_checks(self):
        self.assertEqual(self.sw.total_checks, 12)

    def test_ok_count(self):
        self.assertGreater(self.sw.ok_count, 0)

    def test_warning_count(self):
        self.assertGreater(self.sw.warning_count, 0)

    def test_critical_count(self):
        self.assertGreater(self.sw.critical_count, 0)

    def test_unacked_alerts(self):
        self.assertGreater(self.sw.unacked_alerts, 0)

    def test_toggle_check(self):
        self.sw.toggle_check(0)
        self.assertFalse(self.sw._checks[0].enabled)

    def test_acknowledge_alert(self):
        self.sw.acknowledge_alert(0)
        self.assertTrue(self.sw._alerts[0].acknowledged)

    def test_add_check(self):
        before = self.sw.total_checks
        self.sw.add_check(HealthCheck("test", CheckType.HTTP, "http://localhost", 60, 10))
        self.assertEqual(self.sw.total_checks, before + 1)

    def test_check_status_icon(self):
        c = self.sw._checks[0]
        self.assertIn(c.status_icon, ["🟢", "🟡", "🔴", "❓", "⚪"])

    def test_check_age(self):
        c = self.sw._checks[0]
        self.assertGreater(len(c.age_display), 0)

    def test_config(self):
        self.assertIsNotNone(self.sw._config)
        self.assertTrue(self.sw._config.auto_recovery)

    def test_alerts(self):
        self.assertGreater(len(self.sw._alerts), 0)

    def test_recovery_log(self):
        self.assertGreater(len(self.sw._recovery_log), 0)

    def test_render(self):
        lines = self.sw.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("WATCHDOG" in l for l in lines))

    def test_render_check_detail(self):
        self.sw.select_check(0)
        lines = self.sw.render_check_detail()
        self.assertGreater(len(lines), 5)

    def test_render_config(self):
        lines = self.sw.render_config()
        self.assertGreater(len(lines), 5)


if __name__ == "__main__":
    unittest.main()
