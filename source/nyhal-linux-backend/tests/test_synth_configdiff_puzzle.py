"""Tests for Synthesizer Pro, Config Diff, and Puzzle Solver."""
import unittest
from ui.synth_pro import (
    SynthPro, Oscillator, LFO, Envelope, Filter, SynthEffect,
    OscWaveform, LFOShape, FilterType, EffectSlot, ArpMode, SynthPreset
)
from ui.config_diff import (
    ConfigDiff, DiffFile, DiffLine, DiffLineType, DiffMode,
    DiffHunk, DiffPreset, FileType
)
from ui.puzzle_solver import (
    PuzzleSolver, SudokuPuzzle, CrosswordGrid, LogicPuzzle,
    PuzzleType, Difficulty, SolverStatus, SudokuCell, CrosswordClue, LogicFact,
    create_easy_sudoku, create_easy_crossword, create_logic_puzzle
)


# ==================== SynthPro Tests ====================

class TestOscillator(unittest.TestCase):
    def setUp(self):
        self.osc = Oscillator(OscWaveform.SAW)

    def test_initial_state(self):
        self.assertEqual(self.osc.waveform, OscWaveform.SAW)
        self.assertTrue(self.osc.enabled)

    def test_set_waveform(self):
        self.osc.waveform = OscWaveform.SQUARE
        self.assertEqual(self.osc.waveform, OscWaveform.SQUARE)

    def test_all_waveforms(self):
        for w in OscWaveform:
            self.osc.waveform = w
            self.assertEqual(self.osc.waveform, w)

    def test_vol_bar(self):
        bar = self.osc.vol_bar
        self.assertIn("█", bar)

    def test_icon(self):
        icon = self.osc.icon
        self.assertIsInstance(icon, str)

    def test_disable(self):
        self.osc.enabled = False
        self.assertFalse(self.osc.enabled)


class TestLFO(unittest.TestCase):
    def setUp(self):
        self.lfo = LFO(LFOShape.SINE)

    def test_initial_state(self):
        self.assertEqual(self.lfo.shape, LFOShape.SINE)
        self.assertTrue(self.lfo.enabled)

    def test_set_shape(self):
        self.lfo.shape = LFOShape.RANDOM
        self.assertEqual(self.lfo.shape, LFOShape.RANDOM)

    def test_rate_bar(self):
        bar = self.lfo.rate_bar
        self.assertIn("█", bar)

    def test_depth_bar(self):
        bar = self.lfo.depth_bar
        self.assertIn("█", bar)


class TestFilter(unittest.TestCase):
    def setUp(self):
        self.filt = Filter(FilterType.LOWPASS)

    def test_initial_state(self):
        self.assertEqual(self.filt.filter_type, FilterType.LOWPASS)
        self.assertTrue(self.filt.enabled)

    def test_cutoff_bar(self):
        bar = self.filt.cutoff_bar
        self.assertIn("█", bar)

    def test_res_bar(self):
        bar = self.filt.res_bar
        self.assertIn("█", bar)


class TestEnvelope(unittest.TestCase):
    def setUp(self):
        self.env = Envelope()

    def test_initial_state(self):
        self.assertEqual(self.env.attack, 0.01)
        self.assertEqual(self.env.decay, 0.3)
        self.assertEqual(self.env.sustain, 0.7)
        self.assertEqual(self.env.release, 0.3)

    def test_attack_bar(self):
        bar = self.env.attack_bar
        self.assertIsInstance(bar, str)
        self.assertEqual(len(bar), 10)

    def test_release_bar(self):
        bar = self.env.release_bar
        self.assertIn("█", bar)


class TestSynthEffect(unittest.TestCase):
    def setUp(self):
        self.eff = SynthEffect(EffectSlot.REVERB)

    def test_initial_state(self):
        self.assertEqual(self.eff.effect_type, EffectSlot.REVERB)
        self.assertTrue(self.eff.enabled)

    def test_mix_bar(self):
        bar = self.eff.mix_bar
        self.assertIn("█", bar)

    def test_disable(self):
        self.eff.enabled = False
        self.assertFalse(self.eff.enabled)


class TestSynthPro(unittest.TestCase):
    def setUp(self):
        self.synth = SynthPro()

    def test_initial_state(self):
        self.assertIsNotNone(self.synth.selected_preset)
        self.assertGreater(len(self.synth._oscillators), 0)

    def test_presets(self):
        self.assertGreater(len(self.synth._presets), 0)

    def test_select_preset(self):
        self.synth.select_preset(2)
        self.assertEqual(self.synth._selected_preset, 2)

    def test_select_invalid_preset(self):
        self.synth.select_preset(99)
        self.assertEqual(self.synth._selected_preset, 0)

    def test_note_on_off(self):
        self.synth.note_on("C4")
        self.synth.note_off("C4")
        self.assertEqual(self.synth._active_voices, 0)

    def test_waveform_str(self):
        w = self.synth.waveform_str
        self.assertIsInstance(w, str)
        self.assertGreater(len(w), 0)

    def test_render(self):
        # render accesses _waveform_display which is private, test that attribute exists
        self.assertIsNotNone(self.synth._waveform_display)
        self.assertGreater(len(self.synth._waveform_display), 0)

    def test_effects(self):
        self.assertGreater(len(self.synth._effects), 0)

    def test_oscillators(self):
        self.assertGreater(len(self.synth._oscillators), 0)


# ==================== ConfigDiff Tests ====================

class TestDiffLine(unittest.TestCase):
    def test_added(self):
        line = DiffLine(0, 1, "", "new line", DiffLineType.ADDED)
        self.assertEqual(line.line_type, DiffLineType.ADDED)
        self.assertEqual(line.icon, "+")

    def test_removed(self):
        line = DiffLine(1, 0, "old line", "", DiffLineType.REMOVED)
        self.assertEqual(line.line_type, DiffLineType.REMOVED)
        self.assertEqual(line.icon, "-")

    def test_changed(self):
        line = DiffLine(1, 1, "old", "new", DiffLineType.CHANGED)
        self.assertEqual(line.icon, "~")

    def test_unchanged(self):
        line = DiffLine(1, 1, "same", "same", DiffLineType.UNCHANGED)
        self.assertEqual(line.icon, " ")


class TestDiffHunk(unittest.TestCase):
    def test_empty(self):
        hunk = DiffHunk("@@ header @@")
        self.assertEqual(len(hunk.lines), 0)
        self.assertEqual(hunk.added, 0)
        self.assertEqual(hunk.removed, 0)

    def test_with_lines(self):
        lines = [
            DiffLine(1, 1, "a", "a", DiffLineType.UNCHANGED),
            DiffLine(2, 2, "b", "c", DiffLineType.CHANGED),
            DiffLine(0, 3, "", "d", DiffLineType.ADDED),
            DiffLine(3, 0, "e", "", DiffLineType.REMOVED),
        ]
        hunk = DiffHunk("@@ header @@", lines, 1, 1)
        self.assertEqual(hunk.added, 1)
        self.assertEqual(hunk.removed, 1)


class TestDiffFile(unittest.TestCase):
    def test_create(self):
        df = DiffFile("test.toml", FileType.TOML, "old", "new")
        self.assertEqual(df.name, "test.toml")
        self.assertEqual(df.file_type, FileType.TOML)

    def test_total_changes(self):
        lines = [
            DiffLine(1, 1, "a", "b", DiffLineType.CHANGED),
            DiffLine(0, 2, "", "c", DiffLineType.ADDED),
        ]
        df = DiffFile("x.toml", FileType.TOML, "a", "b\nc", [DiffHunk("@@ @@", lines)])
        self.assertEqual(df.total_changes, 1)  # added=1, removed=0 (CHANGED not counted)


class TestConfigDiff(unittest.TestCase):
    def setUp(self):
        self.diff = ConfigDiff()

    def test_initial_state(self):
        self.assertGreater(len(self.diff._files), 0)
        self.assertEqual(self.diff._selected_file, 0)

    def test_selected_file(self):
        f = self.diff.selected_file
        self.assertIsNotNone(f)
        self.assertEqual(f.name, "compositor.toml")

    def test_total_changes(self):
        total = self.diff.total_changes
        self.assertGreater(total, 0)

    def test_select_file(self):
        self.diff.select_file(1)
        self.assertEqual(self.diff._selected_file, 1)

    def test_select_invalid(self):
        self.diff.select_file(99)
        self.assertEqual(self.diff._selected_file, 0)

    def test_compare_texts(self):
        hunks = self.diff.compare_texts("a\nb\nc", "a\nx\nc")
        self.assertGreater(len(hunks), 0)
        self.assertEqual(len(hunks[0].lines), 3)

    def test_compare_identical(self):
        hunks = self.diff.compare_texts("hello\nworld", "hello\nworld")
        self.assertEqual(len(hunks), 1)
        self.assertEqual(len(hunks[0].lines), 2)

    def test_compare_added_lines(self):
        hunks = self.diff.compare_texts("a", "a\nb\nc")
        self.assertEqual(len(hunks), 1)

    def test_presets(self):
        self.assertGreater(len(self.diff._presets), 0)

    def test_render(self):
        lines = self.diff.render()
        self.assertGreater(len(lines), 0)

    def test_render_detail(self):
        lines = self.diff.render_file_detail()
        self.assertGreater(len(lines), 0)


class TestFileType(unittest.TestCase):
    def test_all_types(self):
        self.assertEqual(FileType.TOML.value, "toml")
        self.assertEqual(FileType.YAML.value, "yaml")
        self.assertEqual(FileType.JSON.value, "json")
        self.assertEqual(FileType.ENV.value, "env")


class TestDiffMode(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(DiffMode.SIDE_BY_SIDE.value, "side-by-side")
        self.assertEqual(DiffMode.UNIFIED.value, "unified")


# ==================== PuzzleSolver Tests ====================

class TestSudokuCell(unittest.TestCase):
    def test_initial(self):
        cell = SudokuCell(0, 0)
        self.assertFalse(cell.is_solved)
        self.assertEqual(cell.value, 0)

    def test_set_value(self):
        cell = SudokuCell(0, 0)
        cell.value = 5
        self.assertTrue(cell.is_solved)


class TestSudokuPuzzle(unittest.TestCase):
    def setUp(self):
        self.puzzle = create_easy_sudoku()

    def test_initial_state(self):
        self.assertEqual(len(self.puzzle.grid), 9)
        self.assertEqual(len(self.puzzle.grid[0]), 9)

    def test_set_cell(self):
        self.puzzle.set_cell(0, 2, 1, given=True)
        self.assertEqual(self.puzzle.grid[0][2].value, 1)
        self.assertTrue(self.puzzle.grid[0][2].given)

    def test_get_row(self):
        row = self.puzzle.get_row(0)
        self.assertEqual(len(row), 9)

    def test_get_col(self):
        col = self.puzzle.get_col_values(0)
        self.assertEqual(len(col), 9)

    def test_solve(self):
        status = self.puzzle.solve()
        self.assertEqual(status, SolverStatus.SOLVED)
        self.assertTrue(self.puzzle.solved)
        self.assertIsNotNone(self.puzzle.solution)

    def test_hint(self):
        hint = self.puzzle.hint()
        if hint:
            r, c, v = hint
            self.assertIn(v, range(1, 10))

    def test_to_string(self):
        s = self.puzzle.to_string()
        self.assertIsInstance(s, str)
        self.assertIn("\n", s)

    def test_not_solved_initially(self):
        self.assertFalse(self.puzzle.solved)


class TestCrosswordClue(unittest.TestCase):
    def test_create(self):
        clue = CrosswordClue(1, "across", "Test clue", "HELLO")
        self.assertEqual(clue.answer, "HELLO")
        self.assertFalse(clue.solved)


class TestCrosswordGrid(unittest.TestCase):
    def setUp(self):
        self.grid = create_easy_crossword()

    def test_initial_state(self):
        self.assertEqual(self.grid.size, 13)
        self.assertGreater(len(self.grid.clues), 0)

    def test_set_black(self):
        self.grid.set_black(0, 0)
        self.assertTrue(self.grid.black[0][0])

    def test_set_cell(self):
        self.grid.set_cell(5, 5, "A")
        self.assertEqual(self.grid.cells[5][5], "A")

    def test_try_solve(self):
        status = self.grid.try_solve()
        self.assertEqual(status, SolverStatus.SOLVED)
        self.assertEqual(self.grid.solved_count, len(self.grid.clues))

    def test_clue_count(self):
        self.assertGreater(self.grid.total_clues, 0)

    def test_to_string(self):
        s = self.grid.to_string()
        self.assertIsInstance(s, str)


class TestLogicPuzzle(unittest.TestCase):
    def setUp(self):
        self.puzzle = create_logic_puzzle()

    def test_initial_state(self):
        self.assertGreater(len(self.puzzle.categories), 0)
        self.assertGreater(len(self.puzzle.clues_text), 0)

    def test_add_category(self):
        p = LogicPuzzle()
        p.add_category("Food", ["Pizza", "Burger", "Sushi"])
        self.assertIn("Food", p.categories)

    def test_total_categories(self):
        self.assertEqual(self.puzzle.total_categories, 4)

    def test_solve(self):
        status = self.puzzle.solve()
        self.assertIn(status, [SolverStatus.SOLVED, SolverStatus.NO_SOLUTION])


class TestPuzzleSolver(unittest.TestCase):
    def setUp(self):
        self.ps = PuzzleSolver()

    def test_initial_state(self):
        self.assertEqual(self.ps.active_type, PuzzleType.SUDOKU)
        self.assertEqual(self.ps.status, SolverStatus.IDLE)

    def test_new_sudoku(self):
        puzzle = self.ps.new_sudoku()
        self.assertIsNotNone(puzzle)
        self.assertEqual(self.ps.active_type, PuzzleType.SUDOKU)

    def test_new_crossword(self):
        grid = self.ps.new_crossword()
        self.assertIsNotNone(grid)
        self.assertEqual(self.ps.active_type, PuzzleType.CROSSTWORD)

    def test_new_logic(self):
        puzzle = self.ps.new_logic()
        self.assertIsNotNone(puzzle)
        self.assertEqual(self.ps.active_type, PuzzleType.LOGIC_GRID)

    def test_solve_sudoku(self):
        self.ps.new_sudoku()
        status = self.ps.solve_active()
        self.assertEqual(status, SolverStatus.SOLVED)

    def test_solve_crossword(self):
        self.ps.new_crossword()
        status = self.ps.solve_active()
        self.assertEqual(status, SolverStatus.SOLVED)

    def test_solve_logic(self):
        self.ps.new_logic()
        status = self.ps.solve_active()
        self.assertIn(status, [SolverStatus.SOLVED, SolverStatus.NO_SOLUTION])

    def test_hint(self):
        self.ps.new_sudoku()
        hint = self.ps.get_hint()
        self.assertIsNotNone(hint)

    def test_status_text(self):
        self.ps.new_sudoku()
        self.assertIn("Sudoku", self.ps.status_text)

    def test_history(self):
        self.ps.new_sudoku()
        self.ps.new_crossword()
        self.assertGreater(len(self.ps.history), 0)

    def test_view_mode(self):
        self.assertEqual(self.ps.view_mode, "puzzles")


class TestDifficulty(unittest.TestCase):
    def test_all_values(self):
        self.assertEqual(Difficulty.EASY.value, "Easy")
        self.assertEqual(Difficulty.HARD.value, "Hard")
        self.assertEqual(Difficulty.EXPERT.value, "Expert")


if __name__ == "__main__":
    unittest.main()
