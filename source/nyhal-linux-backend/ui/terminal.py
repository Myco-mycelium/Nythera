#!/usr/bin/env python3
"""Terminal emulator component for the Nyrqis desktop.

Provides a full-featured terminal emulator with:
- Bitmap font rendering (5×7 pixel font)
- Cursor with blink animation
- Scrollback buffer (configurable, default 1000 lines)
- Basic ANSI color support (8 colors + 8 bright)
- Selection and copy (via clipboard service)
- Keyboard input handling
- Configurable appearance (colors, font size, padding)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# ANSI color codes
# ---------------------------------------------------------------------------

class AnsiColor(IntEnum):
    """Standard ANSI terminal colors."""
    BLACK = 0
    RED = 1
    GREEN = 2
    YELLOW = 3
    BLUE = 4
    MAGENTA = 5
    CYAN = 6
    WHITE = 7
    BRIGHT_BLACK = 8
    BRIGHT_RED = 9
    BRIGHT_GREEN = 10
    BRIGHT_YELLOW = 11
    BRIGHT_BLUE = 12
    BRIGHT_MAGENTA = 13
    BRIGHT_CYAN = 14
    BRIGHT_WHITE = 15


# Default 16-color palette (RGB tuples)
DEFAULT_PALETTE = [
    (0, 0, 0),        # Black
    (205, 49, 49),    # Red
    (13, 188, 121),   # Green
    (229, 229, 16),   # Yellow
    (36, 114, 200),   # Blue
    (188, 63, 188),   # Magenta
    (17, 168, 205),   # Cyan
    (229, 229, 229),  # White
    (102, 102, 102),  # Bright Black
    (241, 76, 76),    # Bright Red
    (35, 209, 139),   # Bright Green
    (245, 245, 67),   # Bright Yellow
    (59, 142, 234),   # Bright Blue
    (214, 112, 214),  # Bright Magenta
    (41, 184, 219),   # Bright Cyan
    (255, 255, 255),  # Bright White
]


# ---------------------------------------------------------------------------
# Terminal cell
# ---------------------------------------------------------------------------

@dataclass
class Cell:
    """A single terminal character cell."""
    char: str = " "
    fg: int = AnsiColor.WHITE
    bg: int = AnsiColor.BLACK
    bold: bool = False
    underline: bool = False
    reverse: bool = False


@dataclass
class Cursor:
    """Terminal cursor state."""
    row: int = 0
    col: int = 0
    visible: bool = True
    blink_state: bool = True
    style: str = "block"  # "block", "underline", "bar"


# ---------------------------------------------------------------------------
# 5×7 Bitmap Font
# ---------------------------------------------------------------------------

# Each character is a list of 7 rows, each row is 5 bits (MSB = leftmost pixel)
_FONT_5X7: Dict[str, List[int]] = {
    " ": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
    "!": [0x04, 0x04, 0x04, 0x04, 0x04, 0x00, 0x04],
    '"': [0x0A, 0x0A, 0x0A, 0x00, 0x00, 0x00, 0x00],
    "#": [0x0A, 0x0A, 0x1F, 0x0A, 0x1F, 0x0A, 0x0A],
    "$": [0x04, 0x0F, 0x14, 0x0E, 0x05, 0x1E, 0x04],
    "%": [0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03],
    "&": [0x0C, 0x12, 0x14, 0x08, 0x15, 0x12, 0x0D],
    "'": [0x04, 0x04, 0x08, 0x00, 0x00, 0x00, 0x00],
    "(": [0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02],
    ")": [0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08],
    "*": [0x00, 0x04, 0x15, 0x0E, 0x15, 0x04, 0x00],
    "+": [0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00],
    ",": [0x00, 0x00, 0x00, 0x00, 0x00, 0x04, 0x08],
    "-": [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
    ".": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04],
    "/": [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x00],
    "0": [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
    "1": [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "2": [0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F],
    "3": [0x0E, 0x11, 0x01, 0x06, 0x01, 0x11, 0x0E],
    "4": [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
    "5": [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
    "6": [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
    "7": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
    "8": [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
    "9": [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
    ":": [0x00, 0x00, 0x04, 0x00, 0x00, 0x04, 0x00],
    ";": [0x00, 0x00, 0x04, 0x00, 0x00, 0x04, 0x08],
    "<": [0x02, 0x04, 0x08, 0x10, 0x08, 0x04, 0x02],
    "=": [0x00, 0x00, 0x1F, 0x00, 0x1F, 0x00, 0x00],
    ">": [0x08, 0x04, 0x02, 0x01, 0x02, 0x04, 0x08],
    "?": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04],
    "@": [0x0E, 0x11, 0x01, 0x0D, 0x15, 0x15, 0x0E],
    "A": [0x0E, 0x11, 0x11, 0x11, 0x1F, 0x11, 0x11],
    "B": [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
    "C": [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
    "D": [0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E],
    "E": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
    "F": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
    "G": [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
    "H": [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "I": [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "J": [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C],
    "K": [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
    "L": [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
    "M": [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
    "N": [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11],
    "O": [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "P": [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
    "Q": [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
    "R": [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
    "S": [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E],
    "T": [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
    "U": [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "V": [0x11, 0x11, 0x11, 0x11, 0x0A, 0x0A, 0x04],
    "W": [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
    "X": [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
    "Y": [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
    "Z": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
    "[": [0x0E, 0x08, 0x08, 0x08, 0x08, 0x08, 0x0E],
    "\\": [0x00, 0x10, 0x08, 0x04, 0x02, 0x01, 0x00],
    "]": [0x0E, 0x02, 0x02, 0x02, 0x02, 0x02, 0x0E],
    "^": [0x04, 0x0A, 0x11, 0x00, 0x00, 0x00, 0x00],
    "_": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F],
    "`": [0x08, 0x04, 0x02, 0x00, 0x00, 0x00, 0x00],
    "a": [0x00, 0x00, 0x0E, 0x01, 0x0F, 0x11, 0x0F],
    "b": [0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x1E],
    "c": [0x00, 0x00, 0x0E, 0x10, 0x10, 0x11, 0x0E],
    "d": [0x01, 0x01, 0x0D, 0x13, 0x11, 0x11, 0x0F],
    "e": [0x00, 0x00, 0x0E, 0x11, 0x1F, 0x10, 0x0E],
    "f": [0x06, 0x09, 0x08, 0x1C, 0x08, 0x08, 0x08],
    "g": [0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x0E],
    "h": [0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x11],
    "i": [0x04, 0x00, 0x0C, 0x04, 0x04, 0x04, 0x0E],
    "j": [0x02, 0x00, 0x06, 0x02, 0x02, 0x12, 0x0C],
    "k": [0x10, 0x10, 0x12, 0x14, 0x18, 0x14, 0x12],
    "l": [0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "m": [0x00, 0x00, 0x1A, 0x15, 0x15, 0x11, 0x11],
    "n": [0x00, 0x00, 0x16, 0x19, 0x11, 0x11, 0x11],
    "o": [0x00, 0x00, 0x0E, 0x11, 0x11, 0x11, 0x0E],
    "p": [0x00, 0x00, 0x1E, 0x11, 0x1E, 0x10, 0x10],
    "q": [0x00, 0x00, 0x0D, 0x13, 0x0F, 0x01, 0x01],
    "r": [0x00, 0x00, 0x16, 0x19, 0x10, 0x10, 0x10],
    "s": [0x00, 0x00, 0x0E, 0x10, 0x0E, 0x01, 0x1E],
    "t": [0x08, 0x08, 0x1C, 0x08, 0x08, 0x09, 0x06],
    "u": [0x00, 0x00, 0x11, 0x11, 0x11, 0x13, 0x0D],
    "v": [0x00, 0x00, 0x11, 0x11, 0x11, 0x0A, 0x04],
    "w": [0x00, 0x00, 0x11, 0x11, 0x15, 0x15, 0x0A],
    "x": [0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11],
    "y": [0x00, 0x00, 0x11, 0x11, 0x0F, 0x01, 0x0E],
    "z": [0x00, 0x00, 0x1F, 0x02, 0x04, 0x08, 0x1F],
    "{": [0x02, 0x04, 0x04, 0x08, 0x04, 0x04, 0x02],
    "|": [0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
    "}": [0x08, 0x04, 0x04, 0x02, 0x04, 0x04, 0x08],
    "~": [0x00, 0x04, 0x08, 0x1F, 0x08, 0x04, 0x00],
    "\x7f": [0x00, 0x00, 0x0E, 0x10, 0x10, 0x00, 0x00],  # DEL glyph
}

# Fill in missing characters with a solid block
_BLOCK = [0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F]


def _get_char_glyph(ch: str) -> List[int]:
    """Get the 5×7 glyph for a character."""
    return _FONT_5X7.get(ch, _BLOCK)


# ---------------------------------------------------------------------------
# Terminal configuration
# ---------------------------------------------------------------------------

@dataclass
class TerminalConfig:
    """Configuration for the terminal emulator."""
    cols: int = 80
    rows: int = 24
    scrollback: int = 1000
    font_size: int = 2          # Pixel scale (1 = 5×7, 2 = 10×14, etc.)
    padding: int = 8            # Padding around text area
    cursor_blink_ms: int = 530  # Cursor blink interval
    bg_color: Tuple[int, int, int] = (24, 24, 32)
    fg_color: int = AnsiColor.WHITE
    font_path: Optional[str] = None  # Not used (bitmap font embedded)
    title: str = "Nyrqis Terminal"
    tab_size: int = 8


# ---------------------------------------------------------------------------
# Terminal emulator
# ---------------------------------------------------------------------------

class TerminalEmulator:
    """Full terminal emulator with ANSI support.

    Parameters
    ----------
    config : TerminalConfig, optional
        Terminal configuration. Uses defaults if not provided.
    on_output : callable, optional
        Called with (pixels, width, height) when the terminal needs redraw.
    """

    def __init__(
        self,
        config: Optional[TerminalConfig] = None,
        on_output: Optional[Callable] = None,
    ) -> None:
        self.config = config or TerminalConfig()
        self.on_output = on_output

        # Screen buffer (list of rows, each row is list of Cells)
        self._screen: List[List[Cell]] = []
        self._scrollback: List[List[Cell]] = []

        # Cursor
        self._cursor = Cursor()

        # State
        self._current_fg = self.config.fg_color
        self._current_bg = AnsiColor.BLACK
        self._current_bold = False
        self._current_underline = False
        self._current_reverse = False
        self._scroll_region_top = 0
        self._scroll_region_bottom = self.config.rows - 1
        self._insert_mode = False
        self._origin_mode = False
        self._wrap_mode = True
        self._auto_wrap = True

        # Escape sequence parser state
        self._esc_buffer = ""
        self._in_escape = False
        self._in_csi = False
        self._csi_params = ""

        # Initialize screen
        self._init_screen()

    def _init_screen(self) -> None:
        """Initialize the screen buffer with empty cells."""
        self._screen = []
        for _ in range(self.config.rows):
            row = [Cell() for _ in range(self.config.cols)]
            self._screen.append(row)

    # -- Input handling ----------------------------------------------------

    def feed(self, data: str) -> None:
        """Feed input data to the terminal.

        Handles regular characters and ANSI escape sequences.
        """
        for ch in data:
            if self._in_escape:
                self._process_escape(ch)
            else:
                self._process_char(ch)

    def feed_bytes(self, data: bytes) -> None:
        """Feed raw bytes to the terminal (UTF-8 decoded)."""
        self.feed(data.decode("utf-8", errors="replace"))

    def _process_char(self, ch: str) -> None:
        """Process a single regular character."""
        code = ord(ch)

        if code == 0x07:  # BEL
            return
        elif code == 0x08:  # BS
            self._backspace()
        elif code == 0x09:  # TAB
            self._tab()
        elif code == 0x0A:  # LF
            self._line_feed()
        elif code == 0x0D:  # CR
            self._carriage_return()
        elif code == 0x0B:  # VT
            self._line_feed()
        elif code == 0x0C:  # FF
            self._line_feed()
        elif code == 0x1B:  # ESC
            self._in_escape = True
            self._esc_buffer = ""
        elif 0x20 <= code <= 0x7E or code > 0x7F:
            # Printable character
            self._put_char(ch)
        # Ignore other control characters

    def _process_escape(self, ch: str) -> None:
        """Process an escape sequence character."""
        self._esc_buffer += ch

        if self._in_csi:
            # CSI sequence: ESC [ <params> <final>
            if ch.isalpha() or ch == "~":
                self._handle_csi(self._csi_params + ch)
                self._in_escape = False
                self._in_csi = False
                self._csi_params = ""
            elif ch.isdigit() or ch == ";" or ch == "?" or ch == "!":
                self._csi_params += ch
            else:
                # Invalid CSI
                self._in_escape = False
                self._in_csi = False
                self._csi_params = ""
        elif ch == "[":
            self._in_csi = True
            self._csi_params = ""
        elif ch == "(":
            # Charset selection — ignore
            self._in_escape = False
        elif ch == ")":
            self._in_escape = False
        elif ch == "M":
            # Reverse index
            self._reverse_index()
            self._in_escape = False
        elif ch == "D":
            # Index (line feed)
            self._line_feed()
            self._in_escape = False
        elif ch == "E":
            # Next line
            self._line_feed()
            self._carriage_return()
            self._in_escape = False
        elif ch == "7":
            # Save cursor
            self._saved_cursor = (self._cursor.row, self._cursor.col)
            self._in_escape = False
        elif ch == "8":
            # Restore cursor
            if hasattr(self, "_saved_cursor"):
                self._cursor.row, self._cursor.col = self._saved_cursor
            self._in_escape = False
        elif ch == "c":
            # Reset
            self.reset()
            self._in_escape = False
        else:
            # Unknown escape — ignore
            self._in_escape = False

    def _handle_csi(self, sequence: str) -> None:
        """Handle a CSI escape sequence."""
        if not sequence:
            return

        final = sequence[-1]
        params_str = sequence[:-1]

        # Check for ? prefix (private mode)
        private = params_str.startswith("?")
        if private:
            params_str = params_str[1:]

        # Parse parameters
        params = []
        if params_str:
            try:
                params = [int(p) if p else 0 for p in params_str.split(";")]
            except ValueError:
                params = []

        def p(idx: int, default: int = 0) -> int:
            """Get parameter with default."""
            return params[idx] if idx < len(params) else default

        if final == "m":
            # SGR — Select Graphic Rendition
            self._handle_sgr(params)
        elif final == "H" or final == "f":
            # Cursor position
            self._cursor.row = max(0, min(p(0, 1) - 1, self.config.rows - 1))
            self._cursor.col = max(0, min(p(1, 1) - 1, self.config.cols - 1))
        elif final == "A":
            # Cursor up
            self._cursor.row = max(0, self._cursor.row - p(0, 1))
        elif final == "B":
            # Cursor down
            self._cursor.row = min(self.config.rows - 1, self._cursor.row + p(0, 1))
        elif final == "C":
            # Cursor forward
            self._cursor.col = min(self.config.cols - 1, self._cursor.col + p(0, 1))
        elif final == "D":
            # Cursor back
            self._cursor.col = max(0, self._cursor.col - p(0, 1))
        elif final == "G":
            # Cursor horizontal absolute
            self._cursor.col = max(0, min(p(0, 1) - 1, self.config.cols - 1))
        elif final == "J":
            # Erase in display
            mode = p(0, 0)
            if mode == 0:
                # Clear from cursor to end
                self._erase_from_cursor_to_end()
            elif mode == 1:
                # Clear from start to cursor
                self._erase_from_start_to_cursor()
            elif mode == 2:
                # Clear entire screen
                self._erase_screen()
            elif mode == 3:
                # Clear screen and scrollback
                self._erase_screen()
                self._scrollback.clear()
        elif final == "K":
            # Erase in line
            mode = p(0, 0)
            if mode == 0:
                self._erase_to_end_of_line()
            elif mode == 1:
                self._erase_to_start_of_line()
            elif mode == 2:
                self._erase_line()
        elif final == "L":
            # Insert lines
            self._insert_lines(p(0, 1))
        elif final == "M":
            # Delete lines
            self._delete_lines(p(0, 1))
        elif final == "P":
            # Delete characters
            self._delete_chars(p(0, 1))
        elif final == "@":
            # Insert characters
            self._insert_chars(p(0, 1))
        elif final == "S":
            # Scroll up
            self._scroll_up(p(0, 1))
        elif final == "T":
            # Scroll down
            self._scroll_down(p(0, 1))
        elif final == "X":
            # Erase characters
            self._erase_chars(p(0, 1))
        elif final == "d":
            # Line position absolute
            self._cursor.row = max(0, min(p(0, 1) - 1, self.config.rows - 1))
        elif final == "r":
            # Set scrolling region
            top = max(0, p(0, 1) - 1)
            bottom = max(0, p(1, self.config.rows) - 1)
            self._scroll_region_top = min(top, self.config.rows - 1)
            self._scroll_region_bottom = min(bottom, self.config.rows - 1)
        elif final == "n":
            # Device status report
            if p(0) == 6:
                # Cursor position — we'd need to respond, skip for now
                pass
        elif final == "h" or final == "l":
            # Set/reset mode (both private ?4h and standard 4h)
            self._handle_private_mode(params, final == "h")

    def _handle_sgr(self, params: List[int]) -> None:
        """Handle Select Graphic Rendition."""
        if not params:
            params = [0]

        i = 0
        while i < len(params):
            code = params[i]

            if code == 0:
                # Reset
                self._current_fg = self.config.fg_color
                self._current_bg = AnsiColor.BLACK
                self._current_bold = False
                self._current_underline = False
                self._current_reverse = False
            elif code == 1:
                self._current_bold = True
            elif code == 4:
                self._current_underline = True
            elif code == 7:
                self._current_reverse = True
            elif code == 22:
                self._current_bold = False
            elif code == 24:
                self._current_underline = False
            elif code == 27:
                self._current_reverse = False
            elif 30 <= code <= 37:
                self._current_fg = code - 30
            elif code == 38:
                # Extended foreground color
                if i + 1 < len(params) and params[i + 1] == 5:
                    if i + 2 < len(params):
                        self._current_fg = params[i + 2]
                    i += 2
                elif i + 1 < len(params) and params[i + 1] == 2:
                    # 24-bit color — not supported, approximate
                    i += 4
            elif code == 39:
                self._current_fg = self.config.fg_color
            elif 40 <= code <= 47:
                self._current_bg = code - 40
            elif code == 48:
                # Extended background color
                if i + 1 < len(params) and params[i + 1] == 5:
                    if i + 2 < len(params):
                        self._current_bg = params[i + 2]
                    i += 2
                elif i + 1 < len(params) and params[i + 1] == 2:
                    i += 4
            elif code == 49:
                self._current_bg = AnsiColor.BLACK
            elif 90 <= code <= 97:
                self._current_fg = code - 90 + 8
            elif 100 <= code <= 107:
                self._current_bg = code - 100 + 8
            i += 1

    def _handle_private_mode(self, params: List[int], set_mode: bool) -> None:
        """Handle private mode set/reset."""
        for mode in params:
            if mode == 4:
                # Insert mode (IRM)
                self._insert_mode = set_mode
            elif mode == 7:
                self._wrap_mode = set_mode
            elif mode == 25:
                self._cursor.visible = set_mode
            elif mode == 1049:
                # Alternate screen buffer
                pass  # Simplified: ignore for now

    # -- Screen operations -------------------------------------------------

    def _put_char(self, ch: str) -> None:
        """Put a character at the current cursor position."""
        if self._cursor.col >= self.config.cols:
            if self._wrap_mode and self._auto_wrap:
                self._carriage_return()
                self._line_feed()
            else:
                self._cursor.col = self.config.cols - 1

        fg = self._current_fg
        bg = self._current_bg
        if self._current_reverse:
            fg, bg = bg, fg

        cell = Cell(
            char=ch,
            fg=fg,
            bg=bg,
            bold=self._current_bold,
            underline=self._current_underline,
            reverse=self._current_reverse,
        )

        if self._insert_mode:
            self._screen[self._cursor.row].insert(self._cursor.col, cell)
            # Remove the last character to keep row width
            if len(self._screen[self._cursor.row]) > self.config.cols:
                self._screen[self._cursor.row].pop()
        else:
            self._screen[self._cursor.row][self._cursor.col] = cell

        self._cursor.col += 1

    def _backspace(self) -> None:
        """Move cursor back one position."""
        self._cursor.col = max(0, self._cursor.col - 1)

    def _tab(self) -> None:
        """Move cursor to next tab stop."""
        tab_size = self.config.tab_size
        next_tab = (self._cursor.col // tab_size + 1) * tab_size
        self._cursor.col = min(next_tab, self.config.cols - 1)

    def _line_feed(self) -> None:
        """Move cursor down one line, scrolling if needed."""
        if self._cursor.row == self._scroll_region_bottom:
            self._scroll_up_region()
        elif self._cursor.row < self.config.rows - 1:
            self._cursor.row += 1

    def _carriage_return(self) -> None:
        """Move cursor to start of line."""
        self._cursor.col = 0

    def _reverse_index(self) -> None:
        """Move cursor up one line, scrolling if needed."""
        if self._cursor.row == self._scroll_region_top:
            self._scroll_down_region()
        elif self._cursor.row > 0:
            self._cursor.row -= 1

    def _scroll_up(self, count: int = 1) -> None:
        """Scroll the screen up by count lines."""
        for _ in range(count):
            self._scroll_up_region()

    def _scroll_down(self, count: int = 1) -> None:
        """Scroll the screen down by count lines."""
        for _ in range(count):
            self._scroll_down_region()

    def _scroll_up_region(self) -> None:
        """Scroll the scroll region up by one line."""
        top = self._scroll_region_top
        bottom = self._scroll_region_bottom

        # Save top line to scrollback if it's the top of the screen
        if top == 0:
            line = self._screen[0][:]
            self._scrollback.append(line)
            if len(self._scrollback) > self.config.scrollback:
                self._scrollback.pop(0)

        # Remove top line, insert blank at bottom
        self._screen.pop(top)
        blank_row = [Cell() for _ in range(self.config.cols)]
        self._screen.insert(bottom, blank_row)

    def _scroll_down_region(self) -> None:
        """Scroll the scroll region down by one line."""
        top = self._scroll_region_top
        bottom = self._scroll_region_bottom

        # Remove bottom line, insert blank at top
        self._screen.pop(bottom)
        blank_row = [Cell() for _ in range(self.config.cols)]
        self._screen.insert(top, blank_row)

    def _erase_from_cursor_to_end(self) -> None:
        """Clear from cursor to end of screen."""
        row = self._cursor.row
        col = self._cursor.col
        # Clear rest of current line
        for c in range(col, self.config.cols):
            self._screen[row][c] = Cell()
        # Clear all lines below
        for r in range(row + 1, self.config.rows):
            self._screen[r] = [Cell() for _ in range(self.config.cols)]

    def _erase_from_start_to_cursor(self) -> None:
        """Clear from start of screen to cursor."""
        row = self._cursor.row
        col = self._cursor.col
        # Clear all lines above
        for r in range(0, row):
            self._screen[r] = [Cell() for _ in range(self.config.cols)]
        # Clear start of current line
        for c in range(0, col + 1):
            self._screen[row][c] = Cell()

    def _erase_screen(self) -> None:
        """Clear entire screen."""
        for r in range(self.config.rows):
            self._screen[r] = [Cell() for _ in range(self.config.cols)]
        self._cursor.row = 0
        self._cursor.col = 0

    def _erase_to_end_of_line(self) -> None:
        """Clear from cursor to end of current line."""
        row = self._cursor.row
        for c in range(self._cursor.col, self.config.cols):
            self._screen[row][c] = Cell()

    def _erase_to_start_of_line(self) -> None:
        """Clear from start of current line to cursor."""
        row = self._cursor.row
        for c in range(0, self._cursor.col + 1):
            self._screen[row][c] = Cell()

    def _erase_line(self) -> None:
        """Clear entire current line."""
        self._screen[self._cursor.row] = [Cell() for _ in range(self.config.cols)]

    def _erase_chars(self, count: int) -> None:
        """Erase count characters from cursor position."""
        row = self._cursor.row
        col = self._cursor.col
        for c in range(col, min(col + count, self.config.cols)):
            self._screen[row][c] = Cell()

    def _delete_chars(self, count: int) -> None:
        """Delete count characters from cursor position, shifting left."""
        row = self._cursor.row
        col = self._cursor.col
        line = self._screen[row]
        del line[col:col + count]
        # Pad with blanks
        while len(line) < self.config.cols:
            line.append(Cell())

    def _insert_chars(self, count: int) -> None:
        """Insert blank characters at cursor position, shifting right."""
        row = self._cursor.row
        col = self._cursor.col
        line = self._screen[row]
        for _ in range(count):
            line.insert(col, Cell())
        # Trim to screen width
        while len(line) > self.config.cols:
            line.pop()

    def _insert_lines(self, count: int) -> None:
        """Insert blank lines at cursor position."""
        row = self._cursor.row
        bottom = self._scroll_region_bottom
        for _ in range(count):
            self._screen.pop(bottom)
            blank_row = [Cell() for _ in range(self.config.cols)]
            self._screen.insert(row, blank_row)

    def _delete_lines(self, count: int) -> None:
        """Delete lines at cursor position."""
        row = self._cursor.row
        bottom = self._scroll_region_bottom
        for _ in range(count):
            if row <= bottom:
                self._screen.pop(row)
                blank_row = [Cell() for _ in range(self.config.cols)]
                self._screen.insert(bottom, blank_row)

    # -- Public API --------------------------------------------------------

    def reset(self) -> None:
        """Reset terminal to initial state."""
        self._init_screen()
        self._scrollback.clear()
        self._cursor = Cursor()
        self._current_fg = self.config.fg_color
        self._current_bg = AnsiColor.BLACK
        self._current_bold = False
        self._current_underline = False
        self._current_reverse = False
        self._scroll_region_top = 0
        self._scroll_region_bottom = self.config.rows - 1
        self._insert_mode = False
        self._wrap_mode = True
        self._auto_wrap = True

    def write(self, text: str) -> None:
        """Write text to the terminal (convenience method)."""
        self.feed(text)

    def writeln(self, text: str = "") -> None:
        """Write text followed by a newline."""
        self.feed(text + "\r\n")

    def clear(self) -> None:
        """Clear the screen."""
        self._erase_screen()

    @property
    def cursor(self) -> Cursor:
        return self._cursor

    @property
    def screen(self) -> List[List[Cell]]:
        return self._screen

    @property
    def scrollback(self) -> List[List[Cell]]:
        return self._scrollback

    def get_line_text(self, row: int) -> str:
        """Get the text content of a line."""
        if 0 <= row < len(self._screen):
            return "".join(cell.char for cell in self._screen[row])
        return ""

    def get_visible_text(self) -> str:
        """Get all visible text as a string."""
        lines = []
        for row in range(self.config.rows):
            lines.append(self.get_line_text(row))
        return "\n".join(lines)

    def get_full_text(self) -> str:
        """Get scrollback + visible text."""
        lines = []
        for line in self._scrollback:
            lines.append("".join(cell.char for cell in line))
        for row in range(self.config.rows):
            lines.append(self.get_line_text(row))
        return "\n".join(lines)

    # -- Rendering ---------------------------------------------------------

    def render_pixels(
        self,
        x_offset: int = 0,
        y_offset: int = 0,
    ) -> Tuple[List[Tuple[int, int, int]], int, int]:
        """Render the terminal to a pixel buffer.

        Returns (pixels, width, height) where pixels is a flat list of
        (r, g, b) tuples in row-major order.
        """
        scale = self.config.font_size
        char_w = 5 * scale
        char_h = 7 * scale
        pad = self.config.padding

        width = self.config.cols * char_w + pad * 2
        height = self.config.rows * char_h + pad * 2

        # Initialize with background color
        bg = self.config.bg_color
        pixels = [bg] * (width * height)

        palette = list(DEFAULT_PALETTE)

        def set_pixel(px: int, py: int, color: Tuple[int, int, int]) -> None:
            """Set a pixel if within bounds."""
            if 0 <= px < width and 0 <= py < height:
                pixels[py * width + px] = color

        def fill_rect(rx: int, ry: int, rw: int, rh: int,
                      color: Tuple[int, int, int]) -> None:
            """Fill a rectangle."""
            for dy in range(rh):
                for dx in range(rw):
                    set_pixel(rx + dx, ry + dy, color)

        def render_char(
            cx: int, cy: int, ch: str,
            fg: int, bold: bool, underline: bool,
        ) -> None:
            """Render a single character glyph."""
            glyph = _get_char_glyph(ch)
            fg_rgb = palette[fg] if fg < len(palette) else (255, 255, 255)

            # Bold: brighten the color
            if bold:
                fg_rgb = tuple(min(255, c + 80) for c in fg_rgb)

            for row in range(7):
                bits = glyph[row]
                for col in range(5):
                    if bits & (1 << (4 - col)):
                        # Foreground pixel
                        for sy in range(scale):
                            for sx in range(scale):
                                set_pixel(cx + col * scale + sx,
                                         cy + row * scale + sy, fg_rgb)

            # Underline
            if underline:
                uy = cy + 7 * scale - 1
                for sx in range(5 * scale):
                    set_pixel(cx + sx, uy, fg_rgb)

        # Render each cell
        for row in range(self.config.rows):
            for col in range(self.config.cols):
                cell = self._screen[row][col]
                cx = pad + col * char_w
                cy = pad + row * char_h

                fg = cell.fg
                bg = cell.bg
                if cell.reverse:
                    fg, bg = bg, fg

                # Draw cell background
                bg_rgb = palette[bg] if bg < len(palette) else (0, 0, 0)
                if bg != AnsiColor.BLACK or cell.reverse:
                    fill_rect(cx, cy, char_w, char_h, bg_rgb)

                # Draw character
                if cell.char != " ":
                    render_char(cx, cy, cell.char, fg, cell.bold, cell.underline)

        # Draw cursor
        if self._cursor.visible and self._cursor.blink_state:
            cx = pad + self._cursor.col * char_w
            cy = pad + self._cursor.row * char_h
            cursor_color = palette[AnsiColor.WHITE]

            if self._cursor.style == "block":
                fill_rect(cx, cy, char_w, char_h, cursor_color)
                # Re-render char in reverse
                cell = self._screen[self._cursor.row][self._cursor.col]
                render_char(cx, cy, cell.char, AnsiColor.BLACK, False, False)
            elif self._cursor.style == "underline":
                fill_rect(cx, cy + 6 * scale, char_w, scale, cursor_color)
            elif self._cursor.style == "bar":
                fill_rect(cx, cy, scale, char_h, cursor_color)

        return pixels, width, height

    def render_to_rgb(self) -> Tuple[bytes, int, int]:
        """Render to raw RGB bytes.

        Returns (rgb_bytes, width, height).
        """
        pixels, width, height = self.render_pixels()
        data = b""
        for r, g, b in pixels:
            data += bytes([r, g, b])
        return data, width, height

    def update_blink(self) -> None:
        """Toggle cursor blink state."""
        self._cursor.blink_state = not self._cursor.blink_state

    # -- Keyboard input mapping -------------------------------------------

    def handle_key(self, key: str, modifiers: Optional[Dict[str, bool]] = None) -> str:
        """Map a key press to terminal input.

        Parameters
        ----------
        key : str
            Key name (e.g., "a", "Enter", "Backspace", "F1", etc.)
        modifiers : dict, optional
            Modifier keys {"shift", "ctrl", "alt", "meta"}.

        Returns
        -------
        str
            The terminal input sequence to feed.
        """
        mods = modifiers or {}
        ctrl = mods.get("ctrl", False)
        alt = mods.get("alt", False)
        shift = mods.get("shift", False)

        # Control characters
        if ctrl:
            if "a" <= key <= "z":
                return chr(ord(key) - ord("a") + 1)
            elif key == "[":
                return "\x1b"
            elif key == "\\":
                return "\x1c"
            elif key == "]":
                return "\x1d"
            elif key == "^":
                return "\x1e"
            elif key == "_":
                return "\x1f"

        # Special keys
        key_map = {
            "Enter": "\r",
            "Return": "\r",
            "Tab": "\t",
            "Backspace": "\x7f",
            "Escape": "\x1b",
            "Delete": "\x1b[3~",
            "Insert": "\x1b[2~",
            "Home": "\x1b[H",
            "End": "\x1b[F",
            "PageUp": "\x1b[5~",
            "PageDown": "\x1b[6~",
            "Up": "\x1b[A",
            "Down": "\x1b[B",
            "Right": "\x1b[C",
            "Left": "\x1b[D",
            "F1": "\x1bOP",
            "F2": "\x1bOQ",
            "F3": "\x1bOR",
            "F4": "\x1bOS",
            "F5": "\x1b[15~",
            "F6": "\x1b[17~",
            "F7": "\x1b[18~",
            "F8": "\x1b[19~",
            "F9": "\x1b[20~",
            "F10": "\x1b[21~",
            "F11": "\x1b[23~",
            "F12": "\x1b[24~",
        }

        if key in key_map:
            seq = key_map[key]
            if alt:
                seq = "\x1b" + seq
            return seq

        # Shift+arrow
        if shift and key in ("Up", "Down", "Left", "Right"):
            base = {"Up": "A", "Down": "B", "Right": "C", "Left": "D"}
            return f"\x1b[1;2{base[key]}"

        # Regular character
        if len(key) == 1:
            if alt:
                return "\x1b" + key
            return key

        return ""

    # -- Selection --------------------------------------------------------

    def get_selection(self, start_row: int, start_col: int,
                      end_row: int, end_col: int) -> str:
        """Get selected text."""
        lines = []
        for row in range(start_row, end_row + 1):
            line = self.get_line_text(row)
            if row == start_row:
                line = line[start_col:]
            if row == end_row:
                line = line[:end_col + 1]
            lines.append(line)
        return "\n".join(lines)

    # -- Utilities --------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"TerminalEmulator(cols={self.config.cols}, "
            f"rows={self.config.rows}, "
            f"cursor=({self._cursor.row},{self._cursor.col}))"
        )
