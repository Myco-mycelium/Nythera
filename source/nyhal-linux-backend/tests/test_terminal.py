#!/usr/bin/env python3
"""Tests for ui.terminal — terminal emulator component."""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.terminal import (
    TerminalEmulator,
    TerminalConfig,
    Cell,
    Cursor,
    AnsiColor,
    DEFAULT_PALETTE,
    _get_char_glyph,
)


class TestCell(unittest.TestCase):
    """Tests for the Cell dataclass."""

    def test_default_cell(self):
        cell = Cell()
        self.assertEqual(cell.char, " ")
        self.assertEqual(cell.fg, AnsiColor.WHITE)
        self.assertEqual(cell.bg, AnsiColor.BLACK)
        self.assertFalse(cell.bold)
        self.assertFalse(cell.underline)
        self.assertFalse(cell.reverse)

    def test_custom_cell(self):
        cell = Cell(char="A", fg=AnsiColor.RED, bold=True)
        self.assertEqual(cell.char, "A")
        self.assertEqual(cell.fg, AnsiColor.RED)
        self.assertTrue(cell.bold)


class TestCursor(unittest.TestCase):
    """Tests for the Cursor dataclass."""

    def test_default_cursor(self):
        cursor = Cursor()
        self.assertEqual(cursor.row, 0)
        self.assertEqual(cursor.col, 0)
        self.assertTrue(cursor.visible)
        self.assertTrue(cursor.blink_state)


class TestFont(unittest.TestCase):
    """Tests for the bitmap font."""

    def test_space_glyph(self):
        glyph = _get_char_glyph(" ")
        self.assertEqual(len(glyph), 7)
        self.assertEqual(glyph, [0x00] * 7)

    def test_A_glyph(self):
        glyph = _get_char_glyph("A")
        self.assertEqual(len(glyph), 7)
        # A should have some pixels set
        self.assertTrue(any(g != 0 for g in glyph))

    def test_unknown_char_uses_block(self):
        glyph = _get_char_glyph("\x01")  # Control char not in font
        self.assertEqual(len(glyph), 7)
        self.assertTrue(all(g == 0x1F for g in glyph))


class TestTerminalEmulator(unittest.TestCase):
    """Tests for the TerminalEmulator class."""

    def setUp(self):
        self.term = TerminalEmulator()

    def test_creation(self):
        self.assertIsNotNone(self.term)
        self.assertEqual(self.term.config.cols, 80)
        self.assertEqual(self.term.config.rows, 24)

    def test_creation_with_config(self):
        config = TerminalConfig(cols=40, rows=12)
        term = TerminalEmulator(config)
        self.assertEqual(term.config.cols, 40)
        self.assertEqual(term.config.rows, 12)

    def test_initial_cursor(self):
        self.assertEqual(self.term.cursor.row, 0)
        self.assertEqual(self.term.cursor.col, 0)

    def test_initial_screen_blank(self):
        """All cells should be spaces initially."""
        for row in self.term.screen:
            for cell in row:
                self.assertEqual(cell.char, " ")

    # -- Basic writing ----------------------------------------------------

    def test_write_character(self):
        self.term.feed("A")
        self.assertEqual(self.term.get_line_text(0), "A" + " " * 79)

    def test_write_string(self):
        self.term.feed("Hello")
        self.assertEqual(self.term.get_line_text(0), "Hello" + " " * 75)

    def test_write_multiple_lines(self):
        self.term.feed("Line1\r\nLine2")
        self.assertEqual(self.term.get_line_text(0), "Line1" + " " * 75)
        self.assertEqual(self.term.get_line_text(1), "Line2" + " " * 75)

    def test_write_wraps(self):
        """Writing past the end of a line should wrap."""
        config = TerminalConfig(cols=5, rows=3)
        term = TerminalEmulator(config)
        term.feed("HelloWorld")
        self.assertEqual(term.get_line_text(0), "Hello")
        self.assertEqual(term.get_line_text(1), "World")

    def test_writeln(self):
        self.term.writeln("Hello")
        self.assertEqual(self.term.get_line_text(0), "Hello" + " " * 75)
        self.assertEqual(self.term.cursor.row, 1)

    def test_clear(self):
        self.term.feed("Hello")
        self.term.clear()
        self.assertEqual(self.term.get_line_text(0), " " * 80)
        self.assertEqual(self.term.cursor.row, 0)
        self.assertEqual(self.term.cursor.col, 0)

    # -- Cursor movement ---------------------------------------------------

    def test_backspace(self):
        self.term.feed("AB\x08")
        # Backspace moves cursor back but doesn't erase the cell
        self.assertEqual(self.term.cursor.col, 1)

    def test_carriage_return(self):
        self.term.feed("Hello\rWorld")
        self.assertEqual(self.term.get_line_text(0), "World" + " " * 75)

    def test_line_feed(self):
        self.term.feed("Hello\n")
        self.assertEqual(self.term.cursor.row, 1)
        self.assertEqual(self.term.cursor.col, 5)

    def test_tab(self):
        self.term.feed("A\tB")
        text = self.term.get_line_text(0)
        self.assertEqual(text[0], "A")
        self.assertEqual(text[8], "B")

    # -- ANSI escape sequences ---------------------------------------------

    def test_cursor_up(self):
        self.term.feed("\x1b[2A")  # Move up 2
        self.assertEqual(self.term.cursor.row, 0)  # Can't go below 0

    def test_cursor_down(self):
        self.term.feed("\x1b[3B")  # Move down 3
        self.assertEqual(self.term.cursor.row, 3)

    def test_cursor_forward(self):
        self.term.feed("\x1b[5C")  # Move right 5
        self.assertEqual(self.term.cursor.col, 5)

    def test_cursor_back(self):
        self.term.feed("Hello\x1b[3D")  # Move left 3
        self.assertEqual(self.term.cursor.col, 2)

    def test_cursor_position(self):
        self.term.feed("\x1b[5;10H")  # Row 5, Col 10
        self.assertEqual(self.term.cursor.row, 4)  # 0-indexed
        self.assertEqual(self.term.cursor.col, 9)

    def test_cursor_goto_home(self):
        self.term.feed("Hello\x1b[H")  # Home
        self.assertEqual(self.term.cursor.row, 0)
        self.assertEqual(self.term.cursor.col, 0)

    def test_cursor_horizontal_absolute(self):
        self.term.feed("\x1b[20G")  # Col 20
        self.assertEqual(self.term.cursor.col, 19)  # 0-indexed

    def test_cursor_hide_show(self):
        self.term.feed("\x1b[?25l")  # Hide
        self.assertFalse(self.term.cursor.visible)
        self.term.feed("\x1b[?25h")  # Show
        self.assertTrue(self.term.cursor.visible)

    # -- SGR (colors and attributes) --------------------------------------

    def test_sgr_reset(self):
        self.term.feed("\x1b[1mBold\x1b[0mNormal")
        self.assertFalse(self.term._current_bold)

    def test_sgr_bold(self):
        self.term.feed("\x1b[1m")
        self.assertTrue(self.term._current_bold)

    def test_sgr_underline(self):
        self.term.feed("\x1b[4m")
        self.assertTrue(self.term._current_underline)

    def test_sgr_reverse(self):
        self.term.feed("\x1b[7m")
        self.assertTrue(self.term._current_reverse)

    def test_sgr_fg_color(self):
        self.term.feed("\x1b[31m")  # Red foreground
        self.assertEqual(self.term._current_fg, AnsiColor.RED)

    def test_sgr_bg_color(self):
        self.term.feed("\x1b[44m")  # Blue background
        self.assertEqual(self.term._current_bg, AnsiColor.BLUE)

    def test_sgr_bright_colors(self):
        self.term.feed("\x1b[91m")  # Bright red
        self.assertEqual(self.term._current_fg, AnsiColor.BRIGHT_RED)

    def test_sgr_256_color(self):
        self.term.feed("\x1b[38;5;200m")  # 256-color mode
        self.assertEqual(self.term._current_fg, 200)

    def test_sgr_multiple_attributes(self):
        self.term.feed("\x1b[1;4;31m")  # Bold + Underline + Red
        self.assertTrue(self.term._current_bold)
        self.assertTrue(self.term._current_underline)
        self.assertEqual(self.term._current_fg, AnsiColor.RED)

    def test_sgr_reset_all(self):
        self.term.feed("\x1b[1;4;31m\x1b[0m")
        self.assertFalse(self.term._current_bold)
        self.assertFalse(self.term._current_underline)
        self.assertEqual(self.term._current_fg, self.term.config.fg_color)

    # -- Erase operations -------------------------------------------------

    def test_erase_display(self):
        self.term.feed("Hello\x1b[2J")  # Clear screen
        self.assertEqual(self.term.get_line_text(0), " " * 80)

    def test_erase_to_end(self):
        self.term.feed("Hello\x1b[0K")  # Clear to end of line
        self.assertEqual(self.term.get_line_text(0), "Hello" + " " * 75)

    def test_erase_to_start(self):
        self.term.feed("\x1b[5G\x1b[1K")  # Move to col 5, clear to start
        text = self.term.get_line_text(0)
        self.assertEqual(text[4], " ")  # Col 5 (0-indexed 4) should be cleared

    def test_erase_line(self):
        self.term.feed("Hello\x1b[2K")  # Clear entire line
        self.assertEqual(self.term.get_line_text(0), " " * 80)

    def test_erase_chars(self):
        self.term.feed("Hello")
        self.term.cursor.col = 2
        self.term.feed("\x1b[3X")  # Erase 3 chars starting at col 2
        text = self.term.get_line_text(0)
        self.assertEqual(text[0], "H")
        self.assertEqual(text[1], "e")
        # Erased: positions 2,3,4 are blank; 'o' was at position 4 but got erased
        self.assertEqual(text[2], " ")
        self.assertEqual(text[3], " ")
        self.assertEqual(text[4], " ")

    # -- Line insert/delete ------------------------------------------------

    def test_insert_blank_lines(self):
        self.term.feed("L1\r\nL2\r\nL3")
        self.term.cursor.row = 1
        self.term.feed("\x1b[1L")  # Insert 1 blank line at row 1
        self.assertEqual(self.term.get_line_text(0)[:2], "L1")
        self.assertEqual(self.term.get_line_text(1), " " * 80)
        self.assertEqual(self.term.get_line_text(2)[:2], "L2")

    def test_delete_blank_lines(self):
        self.term.feed("L1\r\nL2\r\nL3")
        self.term.cursor.row = 0
        self.term.feed("\x1b[1M")  # Delete 1 line at row 0
        self.assertEqual(self.term.get_line_text(0)[:2], "L2")
        self.assertEqual(self.term.get_line_text(1)[:2], "L3")

    # -- Scrolling ---------------------------------------------------------

    def test_scroll_up(self):
        config = TerminalConfig(cols=10, rows=3)
        term = TerminalEmulator(config)
        term.feed("L1\r\nL2\r\nL3")
        term.feed("\x1b[1S")  # Scroll up 1
        self.assertEqual(term.get_line_text(0), "L2" + " " * 8)
        self.assertEqual(term.get_line_text(1), "L3" + " " * 8)
        self.assertEqual(term.get_line_text(2), " " * 10)
        # L1 should be in scrollback
        self.assertEqual(len(term.scrollback), 1)

    def test_scroll_down(self):
        config = TerminalConfig(cols=10, rows=3)
        term = TerminalEmulator(config)
        term.feed("L1\r\nL2\r\nL3")
        term.cursor.row = 0
        term.feed("\x1b[1T")  # Scroll down 1
        self.assertEqual(term.get_line_text(0), " " * 10)
        self.assertEqual(term.get_line_text(1), "L1" + " " * 8)
        self.assertEqual(term.get_line_text(2), "L2" + " " * 8)

    def test_scroll_region(self):
        config = TerminalConfig(cols=10, rows=5)
        term = TerminalEmulator(config)
        term.feed("L1\r\nL2\r\nL3\r\nL4\r\nL5")
        term.feed("\x1b[2;4r")  # Set scroll region rows 2-4
        term.cursor.row = 3  # At bottom of region
        term.feed("\n")  # Should scroll within region
        self.assertEqual(term.get_line_text(0), "L1" + " " * 8)
        self.assertEqual(term.get_line_text(4), "L5" + " " * 8)

    # -- Insert mode -------------------------------------------------------

    def test_insert_mode(self):
        # Insert mode: characters are inserted at cursor, shifting right
        self.term.feed("ABCDE")
        self.term.cursor.col = 2
        self.term.feed("\x1b[4h")  # Enable insert mode
        self.term.feed("XY")
        text = self.term.get_line_text(0)
        # Cursor was at 2, insert XY, cursor now at 4
        self.assertEqual(self.term.cursor.col, 4)
        self.assertTrue(text.startswith("ABXY"))

    def test_insert_mode_off(self):
        self.term.feed("\x1b[4l")  # Turn off insert mode
        self.assertFalse(self.term._insert_mode)

    # -- Get visible text --------------------------------------------------

    def test_get_visible_text(self):
        self.term.feed("Hello\r\nWorld")
        text = self.term.get_visible_text()
        self.assertIn("Hello", text)
        self.assertIn("World", text)

    def test_get_full_text(self):
        self.term.feed("Hello\r\nWorld")
        text = self.term.get_full_text()
        self.assertIn("Hello", text)
        self.assertIn("World", text)

    # -- Selection ---------------------------------------------------------

    def test_selection(self):
        self.term.feed("ABCDE\r\nFGHIJ")
        # get_line_text returns full 80-char line; selection extracts substring
        selected = self.term.get_selection(0, 0, 0, 4)
        self.assertEqual(selected, "ABCDE")

    def test_selection_multiline(self):
        self.term.feed("ABCDE\r\nFGHIJ")
        # Selection extracts substrings from each line's get_line_text
        selected = self.term.get_selection(0, 0, 1, 4)
        # The selection includes trailing spaces from the full-width line
        self.assertIn("ABCDE", selected)
        self.assertIn("FGHIJ", selected)

    # -- Keyboard input mapping --------------------------------------------

    def test_key_enter(self):
        result = self.term.handle_key("Enter")
        self.assertEqual(result, "\r")

    def test_key_backspace(self):
        result = self.term.handle_key("Backspace")
        self.assertEqual(result, "\x7f")

    def test_key_escape(self):
        result = self.term.handle_key("Escape")
        self.assertEqual(result, "\x1b")

    def test_key_arrow_up(self):
        result = self.term.handle_key("Up")
        self.assertEqual(result, "\x1b[A")

    def test_key_arrow_down(self):
        result = self.term.handle_key("Down")
        self.assertEqual(result, "\x1b[B")

    def test_key_ctrl_c(self):
        result = self.term.handle_key("c", {"ctrl": True})
        self.assertEqual(result, "\x03")

    def test_key_ctrl_a(self):
        result = self.term.handle_key("a", {"ctrl": True})
        self.assertEqual(result, "\x01")

    def test_key_regular_char(self):
        result = self.term.handle_key("a")
        self.assertEqual(result, "a")

    def test_key_alt_a(self):
        result = self.term.handle_key("a", {"alt": True})
        self.assertEqual(result, "\x1ba")

    def test_key_function_keys(self):
        self.assertEqual(self.term.handle_key("F1"), "\x1bOP")
        self.assertEqual(self.term.handle_key("F5"), "\x1b[15~")
        self.assertEqual(self.term.handle_key("F12"), "\x1b[24~")

    # -- Rendering ---------------------------------------------------------

    def test_render_pixels(self):
        pixels, width, height = self.term.render_pixels()
        self.assertEqual(width, 80 * 5 * 2 + 16)  # cols * char_w * scale + padding*2
        self.assertEqual(height, 24 * 7 * 2 + 16)
        self.assertEqual(len(pixels), width * height)

    def test_render_rgb(self):
        data, width, height = self.term.render_to_rgb()
        self.assertEqual(len(data), width * height * 3)

    def test_render_with_characters(self):
        self.term.feed("Hello World")
        pixels, width, height = self.term.render_pixels()
        # Should have non-background pixels where text is
        bg = self.term.config.bg_color
        non_bg = sum(1 for p in pixels if p != bg)
        self.assertGreater(non_bg, 0)

    # -- Reset -------------------------------------------------------------

    def test_reset(self):
        self.term.feed("Hello")
        self.term.feed("\x1b[1m")  # Bold
        self.term.reset()
        self.assertEqual(self.term.get_line_text(0), " " * 80)
        self.assertFalse(self.term._current_bold)
        self.assertEqual(self.term.cursor.row, 0)
        self.assertEqual(self.term.cursor.col, 0)
        self.assertEqual(len(self.term.scrollback), 0)

    # -- Cursor blink ------------------------------------------------------

    def test_cursor_blink(self):
        self.term.feed("A")
        self.assertTrue(self.term.cursor.blink_state)
        self.term.update_blink()
        self.assertFalse(self.term.cursor.blink_state)
        self.term.update_blink()
        self.assertTrue(self.term.cursor.blink_state)

    # -- Complex ANSI sequences -------------------------------------------

    def test_complex_ansi(self):
        """Test a typical ANSI sequence from a real terminal program."""
        # typical ls --color output
        self.term.feed("\x1b[1;32mfile.txt\x1b[0m")
        cell = self.term.screen[0][0]
        self.assertEqual(cell.char, "f")
        # Bold + green: fg should be 32 (GREEN), but bold doesn't change stored fg
        self.assertEqual(cell.fg, AnsiColor.GREEN)

    def test_complex_color_output(self):
        """Test ANSI color sequence."""
        self.term.feed("\x1b[31;44mRed on Blue\x1b[0m")
        cell = self.term.screen[0][0]
        self.assertEqual(cell.fg, AnsiColor.RED)
        self.assertEqual(cell.bg, AnsiColor.BLUE)

    # -- Reverse index -----------------------------------------------------

    def test_reverse_index(self):
        """Test ESC M (reverse index)."""
        config = TerminalConfig(cols=10, rows=3)
        term = TerminalEmulator(config)
        term.feed("L1\r\nL2\r\nL3")
        term.cursor.row = 0  # At top
        term.feed("\x1bM")  # Reverse index — should scroll down
        self.assertEqual(term.get_line_text(0), " " * 10)
        self.assertEqual(term.get_line_text(1), "L1" + " " * 8)

    # -- Tab stops ---------------------------------------------------------

    def test_multiple_tabs(self):
        self.term.feed("A\tB\tC")
        text = self.term.get_line_text(0)
        self.assertEqual(text[0], "A")
        self.assertEqual(text[8], "B")
        self.assertEqual(text[16], "C")


class TestTerminalEmulatorEdgeCases(unittest.TestCase):
    """Tests for edge cases and error handling."""

    def test_feed_empty(self):
        term = TerminalEmulator()
        term.feed("")
        self.assertEqual(term.get_line_text(0), " " * 80)

    def test_feed_control_chars(self):
        term = TerminalEmulator()
        # BEL, ENQ, ACK — should be ignored
        term.feed("\x07\x05\x06")
        self.assertEqual(term.get_line_text(0), " " * 80)

    def test_cursor_bounds(self):
        term = TerminalEmulator()
        # Move way past screen bounds
        term.feed("\x1b[999;999H")
        self.assertEqual(term.cursor.row, 23)  # Clamped to rows-1
        self.assertEqual(term.cursor.col, 79)  # Clamped to cols-1

    def test_cursor_negative_bounds(self):
        term = TerminalEmulator()
        term.feed("\x1b[0A")  # Move up 0 (should stay at 0)
        self.assertEqual(term.cursor.row, 0)
        term.feed("\x1b[0D")  # Move left 0
        self.assertEqual(term.cursor.col, 0)

    def test_scrollback_limit(self):
        config = TerminalConfig(cols=5, rows=2, scrollback=3)
        term = TerminalEmulator(config)
        # Scroll more than scrollback limit
        for i in range(10):
            term.feed(f"L{i}\r\n")
        self.assertEqual(len(term.scrollback), 3)

    def test_large_text(self):
        term = TerminalEmulator()
        text = "X" * 1000
        term.feed(text)
        # Should wrap across multiple lines
        self.assertGreater(term.cursor.row, 0)

    def test_repr(self):
        term = TerminalEmulator()
        r = repr(term)
        self.assertIn("TerminalEmulator", r)
        self.assertIn("cols=80", r)
        self.assertIn("rows=24", r)

    def test_insert_mode_preserves_width(self):
        config = TerminalConfig(cols=5, rows=2)
        term = TerminalEmulator(config)
        term.feed("ABCDE")
        term.cursor.col = 0
        term.feed("\x1b[4hX")  # Insert X
        # Row should still be exactly 5 chars
        self.assertEqual(len(term.screen[0]), 5)


if __name__ == "__main__":
    unittest.main()
