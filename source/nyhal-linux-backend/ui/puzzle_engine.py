from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import random


class PuzzleType(Enum):
    SUDOKU = "sudoku"
    CROSSWORD = "crossword"
    LOGIC_GRID = "logic-grid"
    WORD_SEARCH = "word-search"
    KAKURO = "kakuro"
    NONOGRAM = "nonogram"


class Difficulty(Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"


class CellState(Enum):
    EMPTY = "empty"
    FILLED = "filled"
    LOCKED = "locked"
    ERROR = "error"
    HINT = "hint"


@dataclass
class SudokuCell:
    row: int
    col: int
    value: int = 0
    state: CellState = CellState.EMPTY
    candidates: list = field(default_factory=list)

    @property
    def display(self) -> str:
        if self.value == 0:
            return "·"
        return str(self.value)


@dataclass
class CrosswordClue:
    number: int
    direction: str
    clue: str
    answer: str
    row: int
    col: int
    solved: bool = False

    @property
    def length(self) -> int:
        return len(self.answer)


@dataclass
class LogicEntry:
    category: str
    value: str
    solved: bool = False


@dataclass
class WordSearchWord:
    word: str
    row: int
    col: int
    direction: str
    found: bool = False


@dataclass
class PuzzleStats:
    puzzles_played: int = 0
    puzzles_solved: int = 0
    best_times: dict = field(default_factory=dict)
    total_time_secs: float = 0
    hints_used: int = 0
    errors_made: int = 0

    @property
    def solve_rate(self) -> float:
        if self.puzzles_played == 0:
            return 0
        return self.puzzles_solved / self.puzzles_played * 100

    @property
    def avg_time(self) -> str:
        if self.puzzles_solved == 0:
            return "N/A"
        avg = self.total_time_secs / self.puzzles_solved
        m, s = divmod(int(avg), 60)
        return f"{m}:{s:02d}"


class PuzzleEngine:
    def __init__(self):
        self._current_type: PuzzleType = PuzzleType.SUDOKU
        self._difficulty: Difficulty = Difficulty.MEDIUM
        self._sudoku_board: list[list[SudokuCell]] = []
        self._crossword_clues: list[CrosswordClue] = []
        self._word_search_words: list[WordSearchWord] = []
        self._word_search_grid: list[list[str]] = []
        self._stats: PuzzleStats = PuzzleStats()
        self._selected_row: int = 0
        self._selected_col: int = 0
        self._timer_start: float = 0
        self._timer_secs: int = 0
        self._is_paused: bool = False
        self._hints_remaining: int = 3
        self._view: str = "puzzle"
        self._create_samples()

    def _create_samples(self):
        self._sudoku_board = []
        # Pre-filled puzzle
        puzzle = [
            [5, 3, 0, 0, 7, 0, 0, 0, 0],
            [6, 0, 0, 1, 9, 5, 0, 0, 0],
            [0, 9, 8, 0, 0, 0, 0, 6, 0],
            [8, 0, 0, 0, 6, 0, 0, 0, 3],
            [4, 0, 0, 8, 0, 3, 0, 0, 1],
            [7, 0, 0, 0, 2, 0, 0, 0, 6],
            [0, 6, 0, 0, 0, 0, 2, 8, 0],
            [0, 0, 0, 4, 1, 9, 0, 0, 5],
            [0, 0, 0, 0, 8, 0, 0, 7, 9],
        ]
        for r in range(9):
            row = []
            for c in range(9):
                val = puzzle[r][c]
                state = CellState.LOCKED if val != 0 else CellState.EMPTY
                row.append(SudokuCell(r, c, val, state))
            self._sudoku_board.append(row)

        self._crossword_clues = [
            CrosswordClue(1, "across", "Programming language created by Guido van Rossum", "PYTHON", 0, 0),
            CrosswordClue(2, "down", "Operating system kernel written by Linus Torvalds", "LINUX", 0, 0),
            CrosswordClue(3, "across", "Display server protocol for Unix-like systems", "WAYLAND", 2, 0),
            CrosswordClue(4, "down", "Version control system by Linus", "GIT", 0, 4),
            CrosswordClue(5, "across", "Containerization platform", "DOCKER", 4, 0),
            CrosswordClue(6, "down", "Package manager for Rust", "CARGO", 0, 6),
        ]

        words = ["PYTHON", "RUST", "LINUX", "WAYLAND", "DOCKER", "GIT", "TUX"]
        self._word_search_grid = [['.' for _ in range(10)] for _ in range(10)]
        self._word_search_words = []
        for word in words:
            placed = False
            for _ in range(50):
                r = random.randint(0, 9)
                c = random.randint(0, 9)
                d = random.choice(["right", "down"])
                if d == "right" and c + len(word) <= 10:
                    for i, ch in enumerate(word):
                        self._word_search_grid[r][c + i] = ch
                    self._word_search_words.append(WordSearchWord(word, r, c, d))
                    placed = True
                    break
                elif d == "down" and r + len(word) <= 10:
                    for i, ch in enumerate(word):
                        self._word_search_grid[r + i][c] = ch
                    self._word_search_words.append(WordSearchWord(word, r, c, d))
                    placed = True
                    break

        self._stats = PuzzleStats(
            puzzles_played=42, puzzles_solved=38, total_time_secs=3600,
            hints_used=12, errors_made=15,
            best_times={"easy": 120, "medium": 300, "hard": 600, "expert": 1200}
        )

    @property
    def timer_display(self) -> str:
        m, s = divmod(self._timer_secs, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @property
    def sudoku_errors(self) -> int:
        count = 0
        for row in self._sudoku_board:
            for cell in row:
                if cell.state == CellState.ERROR:
                    count += 1
        return count

    @property
    def sudoku_filled(self) -> int:
        count = 0
        for row in self._sudoku_board:
            for cell in row:
                if cell.value != 0:
                    count += 1
        return count

    def place_sudoku_value(self, row: int, col: int, value: int) -> str:
        if 0 <= row < 9 and 0 <= col < 9:
            cell = self._sudoku_board[row][col]
            if cell.state == CellState.LOCKED:
                return "Can't modify a locked cell!"
            cell.value = value
            cell.state = CellState.FILLED
            # Simple validation
            if self._check_sudoku_conflict(row, col, value):
                cell.state = CellState.ERROR
                self._stats.errors_made += 1
                return f"Error! {value} conflicts with existing number."
            return f"Placed {value} at ({row},{col})"
        return "Invalid position"

    def _check_sudoku_conflict(self, row: int, col: int, value: int) -> bool:
        # Check row
        for c in range(9):
            if c != col and self._sudoku_board[row][c].value == value:
                return True
        # Check column
        for r in range(9):
            if r != row and self._sudoku_board[r][col].value == value:
                return True
        # Check box
        box_r, box_c = 3 * (row // 3), 3 * (col // 3)
        for r in range(box_r, box_r + 3):
            for c in range(box_c, box_c + 3):
                if (r, c) != (row, col) and self._sudoku_board[r][c].value == value:
                    return True
        return False

    def find_word_search(self, word: str) -> bool:
        for ws in self._word_search_words:
            if ws.word == word:
                ws.found = True
                return True
        return False

    def use_hint(self) -> str:
        if self._hints_remaining <= 0:
            return "No hints remaining!"
        self._hints_remaining -= 1
        self._stats.hints_used += 1
        # Find an empty cell and fill it
        for r in range(9):
            for c in range(9):
                if self._sudoku_board[r][c].value == 0:
                    # Simple hint: find valid value
                    for v in range(1, 10):
                        if not self._check_sudoku_conflict(r, c, v):
                            self._sudoku_board[r][c].value = v
                            self._sudoku_board[r][c].state = CellState.HINT
                            return f"Hint: Place {v} at row {r+1}, col {c+1}"
        return "No empty cells found"

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                     NYRQIS PUZZLE ENGINE                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Type: {self._current_type.value}  Difficulty: {self._difficulty.value}  Timer: {self.timer_display}  Hints: {self._hints_remaining}")
        lines.append(f"  Played: {self._stats.puzzles_played}  Solved: {self._stats.puzzles_solved}  Rate: {self._stats.solve_rate:.0f}%  Avg: {self._stats.avg_time}")
        lines.append("")
        if self._current_type == PuzzleType.SUDOKU:
            lines.extend(self._render_sudoku())
        elif self._current_type == PuzzleType.CROSSWORD:
            lines.extend(self._render_crossword())
        elif self._current_type == PuzzleType.WORD_SEARCH:
            lines.extend(self._render_word_search())
        lines.append("")
        lines.append("  [N]ew puzzle  [H]int  [P]ause  [T]ype  [D]ifficulty  [R]eset  [S]tats")
        return lines

    def _render_sudoku(self) -> list:
        lines = []
        lines.append(f"  ── Sudoku ({self.sudoku_filled}/81 filled, {self.sudoku_errors} errors) ──")
        lines.append("")
        lines.append("     1   2   3   4   5   6   7   8   9")
        lines.append("   ┌───┬───┬───┬───┬───┬───┬───┬───┬───┐")
        for r in range(9):
            if r > 0 and r % 3 == 0:
                lines.append("   ├───┼───┼───┼───┼───┼───┼───┼───┼───┤")
            row_str = f" {r+1} │"
            for c in range(9):
                cell = self._sudoku_board[r][c]
                if cell.state == CellState.ERROR:
                    row_str += f" \033[91m{cell.display}\033[0m │"
                elif cell.state == CellState.HINT:
                    row_str += f" \033[93m{cell.display}\033[0m │"
                elif cell.state == CellState.LOCKED:
                    row_str += f" \033[1m{cell.display}\033[0m │"
                else:
                    row_str += f" {cell.display} │"
            lines.append(row_str)
        lines.append("   └───┴───┴───┴───┴───┴───┴───┴───┴───┘")
        return lines

    def _render_crossword(self) -> list:
        lines = []
        lines.append("  ── Crossword ──")
        lines.append("")
        lines.append("  Across:")
        for clue in self._crossword_clues:
            if clue.direction == "across":
                solved = "✓" if clue.solved else " "
                lines.append(f"    {clue.number}. {clue.clue} ({clue.length} letters) {solved}")
        lines.append("")
        lines.append("  Down:")
        for clue in self._crossword_clues:
            if clue.direction == "down":
                solved = "✓" if clue.solved else " "
                lines.append(f"    {clue.number}. {clue.clue} ({clue.length} letters) {solved}")
        return lines

    def _render_word_search(self) -> list:
        lines = []
        lines.append("  ── Word Search ──")
        lines.append("")
        header = "     " + " ".join(str(i) for i in range(1, 11))
        lines.append(header)
        for r in range(10):
            row_str = f"  {r+1:2d}  " + " ".join(self._word_search_grid[r][c] if self._word_search_grid[r][c] != '.' else '·' for c in range(10))
            lines.append(row_str)
        lines.append("")
        lines.append("  Words to find:")
        for ws in self._word_search_words:
            status = "✓" if ws.found else " "
            lines.append(f"    {status} {ws.word}")
        return lines

    def render_stats(self) -> list:
        lines = []
        lines.append("  ── Puzzle Statistics ──")
        lines.append("")
        lines.append(f"  Played: {self._stats.puzzles_played}")
        lines.append(f"  Solved: {self._stats.puzzles_solved}")
        lines.append(f"  Solve Rate: {self._stats.solve_rate:.1f}%")
        lines.append(f"  Total Time: {self._stats.total_time_secs / 3600:.1f} hours")
        lines.append(f"  Average Time: {self._stats.avg_time}")
        lines.append(f"  Hints Used: {self._stats.hints_used}")
        lines.append(f"  Errors: {self._stats.errors_made}")
        lines.append("")
        lines.append("  Best Times:")
        for diff, secs in self._stats.best_times.items():
            m, s = divmod(secs, 60)
            lines.append(f"    {diff}: {m}:{s:02d}")
        return lines
