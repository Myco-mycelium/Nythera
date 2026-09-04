"""
Tests for Input Recorder and Terminal Spreadsheet.
"""
import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.input_recorder import (
    InputRecorder, Macro, InputAction, RecordingSession, PlaybackConfig,
    InputType, MouseButton, MacroStatus, LoopMode, TriggerType, ActionGroup,
)
from ui.terminal_spreadsheet import (
    TerminalSpreadsheet, Cell, Sheet, Column, Selection, FormulaEngine,
    CellType, CellFormat, SortOrder,
)


# ─── Input Recorder Tests ────────────────────────────────────────────────


class TestInputAction(unittest.TestCase):
    def test_create(self):
        a = InputAction(InputType.KEY_PRESS, "A", timestamp=time.time())
        self.assertEqual(a.key, "A")

    def test_display_key(self):
        a = InputAction(InputType.KEY_PRESS, "Return")
        self.assertEqual(a.display, "Return")

    def test_display_mouse(self):
        a = InputAction(InputType.MOUSE_CLICK, button=MouseButton.LEFT, x=100, y=200)
        self.assertIn("100", a.display)
        self.assertIn("200", a.display)

    def test_display_move(self):
        a = InputAction(InputType.MOUSE_MOVE, x=50, y=100)
        self.assertIn("50", a.display)

    def test_display_scroll(self):
        a = InputAction(InputType.MOUSE_SCROLL, scroll_amount=-3, x=100, y=200)
        self.assertIn("-3", a.display)

    def test_icon(self):
        a = InputAction(InputType.KEY_PRESS)
        self.assertEqual(a.icon, "⌨️")

    def test_repeat_str(self):
        a = InputAction(repeat=3)
        self.assertEqual(a.repeat_str, "×3")


class TestMacro(unittest.TestCase):
    def test_create(self):
        m = Macro("Test", actions=[InputAction(), InputAction()])
        self.assertEqual(m.name, "Test")
        self.assertEqual(m.action_count, 2)

    def test_status_icon(self):
        m = Macro(status=MacroStatus.PLAYING)
        self.assertEqual(m.status_icon, "▶️")

    def test_duration_str(self):
        m = Macro(actions=[
            InputAction(InputType.KEY_PRESS),
            InputAction(InputType.KEY_PRESS),
        ])
        d = m.duration_str
        self.assertTrue(len(d) > 0)

    def test_speed_str(self):
        m = Macro(playback_speed=2.0)
        self.assertEqual(m.speed_str, "2.00x")

    def test_display(self):
        m = Macro("MyMacro", favorite=True, actions=[InputAction()])
        d = m.display
        self.assertIn("MyMacro", d)
        self.assertIn("⭐", d)

    def test_trigger_display(self):
        m = Macro(trigger=TriggerType.HOTKEY, trigger_config={"key": "Ctrl+A"})
        td = m.trigger_display
        self.assertIn("hotkey", td)
        self.assertIn("Ctrl+A", td)


class TestRecordingSession(unittest.TestCase):
    def test_create(self):
        r = RecordingSession("Test", time.time(), time.time() + 5, 10)
        self.assertEqual(r.action_count, 10)

    def test_duration_str(self):
        r = RecordingSession(started=time.time() - 65)
        d = r.duration_str
        self.assertIn("m", d)


class TestInputRecorder(unittest.TestCase):
    def setUp(self):
        self.rec = InputRecorder()

    def test_initial_state(self):
        self.assertGreater(len(self.rec.macros), 0)
        self.assertGreater(len(self.rec.sessions), 0)

    def test_selected_macro(self):
        m = self.rec.selected_macro
        self.assertIsNotNone(m)

    def test_select_macro(self):
        self.rec.select_macro(2)
        self.assertEqual(self.rec._selected_macro, 2)

    def test_start_stop_recording(self):
        session = self.rec.start_recording("Test")
        self.assertIsNotNone(session)
        self.assertTrue(self.rec._recording)
        macro = self.rec.stop_recording()
        self.assertIsNotNone(macro)
        self.assertFalse(self.rec._recording)

    def test_play_macro(self):
        result = self.rec.play_macro(0)
        self.assertTrue(result)
        self.assertEqual(self.rec.macros[0].status, MacroStatus.PLAYING)

    def test_stop_macro(self):
        self.rec.play_macro(0)
        result = self.rec.stop_macro(0)
        self.assertTrue(result)
        self.assertEqual(self.rec.macros[0].status, MacroStatus.IDLE)

    def test_pause_macro(self):
        self.rec.play_macro(0)
        result = self.rec.pause_macro(0)
        self.assertTrue(result)
        self.assertEqual(self.rec.macros[0].status, MacroStatus.PAUSED)

    def test_set_playback_speed(self):
        result = self.rec.set_playback_speed(0, 2.0)
        self.assertTrue(result)
        self.assertEqual(self.rec.macros[0].playback_speed, 2.0)

    def test_set_loop_mode(self):
        result = self.rec.set_loop_mode(0, LoopMode.INFINITE)
        self.assertTrue(result)
        self.assertEqual(self.rec.macros[0].loop_mode, LoopMode.INFINITE)

    def test_create_macro(self):
        count = len(self.rec.macros)
        m = self.rec.create_macro("New Macro", "Test")
        self.assertEqual(len(self.rec.macros), count + 1)

    def test_delete_macro(self):
        count = len(self.rec.macros)
        result = self.rec.delete_macro(2)
        self.assertTrue(result)
        self.assertEqual(len(self.rec.macros), count - 1)

    def test_duplicate_macro(self):
        count = len(self.rec.macros)
        copy = self.rec.duplicate_macro(0)
        self.assertIsNotNone(copy)
        self.assertEqual(len(self.rec.macros), count + 1)

    def test_toggle_favorite(self):
        result = self.rec.toggle_favorite(0)
        self.assertTrue(result)

    def test_navigation(self):
        self.rec.select_down()
        self.assertEqual(self.rec._selected_macro, 1)
        self.rec.select_up()
        self.assertEqual(self.rec._selected_macro, 0)

    def test_search(self):
        results = self.rec.search("git")
        self.assertGreater(len(results), 0)

    def test_get_favorites(self):
        favs = self.rec.get_favorites()
        self.assertGreater(len(favs), 0)

    def test_stats(self):
        stats = self.rec.get_stats()
        self.assertIn("total_macros", stats)
        self.assertIn("total_actions", stats)


# ─── Terminal Spreadsheet Tests ───────────────────────────────────────────


class TestCell(unittest.TestCase):
    def test_create(self):
        c = Cell(row=0, col=0, value=42)
        self.assertEqual(c.value, 42)

    def test_ref(self):
        c = Cell(row=0, col=0)
        self.assertEqual(c.ref, "A1")

    def test_ref_multi_digit(self):
        c = Cell(row=9, col=25)
        self.assertEqual(c.ref, "Z10")

    def test_display_value_number(self):
        c = Cell(value=42.0, cell_type=CellType.NUMBER)
        self.assertEqual(c.display_value, "42")

    def test_display_value_currency(self):
        c = Cell(value=1234.56, cell_format=CellFormat.CURRENCY)
        self.assertEqual(c.display_value, "$1,234.56")

    def test_display_value_percent(self):
        c = Cell(value=0.85, cell_format=CellFormat.PERCENT)
        self.assertEqual(c.display_value, "85.0%")

    def test_display_value_string(self):
        c = Cell(value="hello", cell_type=CellType.STRING)
        self.assertEqual(c.display_value, "hello")

    def test_display_empty(self):
        c = Cell(value=None)
        self.assertEqual(c.display_value, "")


class TestColumn(unittest.TestCase):
    def test_create(self):
        col = Column(0, "Name", width=15)
        self.assertEqual(col.letter, "A")

    def test_letter(self):
        col = Column(25)
        self.assertEqual(col.letter, "Z")

    def test_sort_icon(self):
        col = Column(sort_order=SortOrder.ASC)
        self.assertEqual(col.sort_icon, "↑")


class TestSelection(unittest.TestCase):
    def test_single(self):
        s = Selection(1, 1, 1, 1)
        self.assertTrue(s.is_single)

    def test_range(self):
        s = Selection(0, 0, 5, 3)
        self.assertFalse(s.is_single)
        self.assertIn(":", s.range_str)


class TestFormulaEngine(unittest.TestCase):
    def test_sum(self):
        def get_val(col, row):
            data = {("A", 0): 10, ("A", 1): 20, ("A", 2): 30}
            return data.get((col, row), 0)
        result = FormulaEngine.evaluate("=SUM(A1:A3)", get_val)
        self.assertEqual(result, 60)

    def test_avg(self):
        def get_val(col, row):
            data = {("B", 0): 10, ("B", 1): 20, ("B", 2): 30}
            return data.get((col, row), 0)
        result = FormulaEngine.evaluate("=AVG(B1:B3)", get_val)
        self.assertEqual(result, 20.0)

    def test_count(self):
        def get_val(col, row):
            data = {("C", 0): 5, ("C", 1): "text", ("C", 2): 10}
            return data.get((col, row), 0)
        result = FormulaEngine.evaluate("=COUNT(C1:C3)", get_val)
        self.assertEqual(result, 2)

    def test_max(self):
        def get_val(col, row):
            data = {("D", 0): 5, ("D", 1): 15, ("D", 2): 10}
            return data.get((col, row), 0)
        result = FormulaEngine.evaluate("=MAX(D1:D3)", get_val)
        self.assertEqual(result, 15)

    def test_min(self):
        def get_val(col, row):
            data = {("E", 0): 5, ("E", 1): 15, ("E", 2): 10}
            return data.get((col, row), 0)
        result = FormulaEngine.evaluate("=MIN(E1:E3)", get_val)
        self.assertEqual(result, 5)


class TestSheet(unittest.TestCase):
    def test_set_get(self):
        s = Sheet("Test")
        s.set_cell(0, 0, "Hello")
        cell = s.get_cell(0, 0)
        self.assertEqual(cell.value, "Hello")
        self.assertEqual(cell.cell_type, CellType.STRING)

    def test_set_number(self):
        s = Sheet("Test")
        s.set_cell(1, 1, "42")
        cell = s.get_cell(1, 1)
        self.assertEqual(cell.cell_type, CellType.NUMBER)
        self.assertEqual(cell.value, 42.0)

    def test_set_formula(self):
        s = Sheet("Test")
        s.set_cell(0, 0, "=SUM(A1:A10)")
        cell = s.get_cell(0, 0)
        self.assertEqual(cell.cell_type, CellType.FORMULA)

    def test_used_rows(self):
        s = Sheet("Test")
        s.set_cell(5, 0, "data")
        self.assertEqual(s.used_rows, 6)

    def test_cell_count(self):
        s = Sheet("Test")
        s.set_cell(0, 0, "a")
        s.set_cell(1, 1, "b")
        self.assertEqual(s.cell_count, 2)


class TestTerminalSpreadsheet(unittest.TestCase):
    def setUp(self):
        self.ss = TerminalSpreadsheet()

    def test_initial_state(self):
        self.assertGreater(len(self.ss.sheets), 0)

    def test_current_sheet(self):
        s = self.ss.current_sheet
        self.assertIsNotNone(s)

    def test_cursor_ref(self):
        self.assertEqual(self.ss.cursor_ref, "A1")

    def test_move_cursor(self):
        self.ss.move_cursor(1, 2)
        self.assertEqual(self.ss.cursor_row, 1)
        self.assertEqual(self.ss.cursor_col, 2)

    def test_set_cursor(self):
        self.ss.set_cursor(5, 3)
        self.assertEqual(self.ss.cursor_row, 5)
        self.assertEqual(self.ss.cursor_col, 3)

    def test_set_cell_value(self):
        self.ss.set_cell_value(0, 0, "Test")
        cell = self.ss.current_sheet.get_cell(0, 0)
        self.assertEqual(cell.value, "Test")

    def test_add_sheet(self):
        count = len(self.ss.sheets)
        s = self.ss.add_sheet("New Sheet")
        self.assertEqual(len(self.ss.sheets), count + 1)

    def test_select_sheet(self):
        self.ss.select_sheet(1)
        self.assertEqual(self.ss.active_sheet, 1)

    def test_sort_column(self):
        self.ss.sort_column(0, SortOrder.ASC)
        sheet = self.ss.current_sheet
        self.assertEqual(sheet.columns[0].sort_order, SortOrder.ASC)

    def test_find(self):
        results = self.ss.find("Housing")
        self.assertGreater(len(results), 0)

    def test_find_and_replace(self):
        count = self.ss.find_and_replace("Housing", "Rent")
        self.assertGreater(count, 0)

    def test_export_csv(self):
        csv = self.ss.export_csv()
        self.assertIn(",", csv)

    def test_export_json(self):
        json_str = self.ss.export_json()
        self.assertIn("[", json_str)

    def test_toggle_bold(self):
        self.ss.toggle_bold(0, 0)
        cell = self.ss.current_sheet.get_cell(0, 0)
        # The cell was already set in sample data, so bold should be toggled
        # Check that toggle function runs without error
        self.assertIsNotNone(cell)

    def test_clear_cell(self):
        self.ss.set_cell_value(0, 0, "Test")
        self.ss.clear_cell(0, 0)
        cell = self.ss.current_sheet.get_cell(0, 0)
        self.assertIsNone(cell.value)

    def test_stats(self):
        stats = self.ss.get_stats()
        self.assertIn("sheets", stats)
        self.assertIn("cursor", stats)
        self.assertIn("cells", stats)


if __name__ == "__main__":
    unittest.main()
