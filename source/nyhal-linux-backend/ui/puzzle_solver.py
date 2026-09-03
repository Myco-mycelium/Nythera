"""Puzzle Solver — Sudoku, Crossword, and Logic puzzle solvers for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple, Set
import copy


class PuzzleType(Enum):
    SUDOKU = "Sudoku"
    CROSSTWORD = "Crossword"
    LOGIC_GRID = "Logic Grid"


class Difficulty(Enum):
    EASY = "Easy"
    MEDIUM = "Medium"
    HARD = "Hard"
    EXPERT = "Expert"


class SolverStatus(Enum):
    IDLE = "Idle"
    SOLVING = "Solving"
    SOLVED = "Solved"
    NO_SOLUTION = "No Solution"
    ERROR = "Error"


# --- Sudoku Solver ---

@dataclass
class SudokuCell:
    row: int
    col: int
    value: int = 0
    given: bool = False
    candidates: Set[int] = field(default_factory=lambda: set(range(1, 10)))

    @property
    def is_solved(self) -> bool:
        return self.value != 0

    def remove_candidate(self, n: int) -> bool:
        if n in self.candidates:
            self.candidates.discard(n)
            return True
        return False


@dataclass
class SudokuPuzzle:
    grid: List[List[SudokuCell]] = field(default_factory=list)
    difficulty: Difficulty = Difficulty.MEDIUM
    solution: Optional[List[List[int]]] = None
    solve_steps: int = 0
    solve_time_ms: float = 0.0

    def __post_init__(self):
        if not self.grid:
            self.grid = [[SudokuCell(r, c) for c in range(9)] for r in range(9)]

    def set_cell(self, row: int, col: int, value: int, given: bool = False):
        if 0 <= row < 9 and 0 <= col < 9:
            self.grid[row][col].value = value
            self.grid[row][col].given = given
            if value != 0:
                self.grid[row][col].candidates = {value}
            else:
                self.grid[row][col].candidates = set(range(1, 10))

    def get_row(self, row: int) -> List[int]:
        return [self.grid[row][c].value for c in range(9)]

    def get_col(self, col: int) -> List[int]:
        return [self.grid[r][col].value for c in range(9) for r in [range(9)][0] if True][:9]

    def get_col_values(self, col: int) -> List[int]:
        return [self.grid[r][col].value for r in range(9)]

    def get_box(self, row: int, col: int) -> List[int]:
        br, bc = (row // 3) * 3, (col // 3) * 3
        return [self.grid[r][c].value for r in range(br, br + 3) for c in range(bc, bc + 3)]

    @property
    def solved(self) -> bool:
        for r in range(9):
            for c in range(9):
                if self.grid[r][c].value == 0:
                    return False
        return self._is_valid()

    def _is_valid(self) -> bool:
        for r in range(9):
            vals = [self.grid[r][c].value for c in range(9)]
            if len(set(vals)) != 9 or 0 in vals:
                return False
        for c in range(9):
            vals = [self.grid[r][c].value for r in range(9)]
            if len(set(vals)) != 9 or 0 in vals:
                return False
        for br in range(0, 9, 3):
            for bc in range(0, 9, 3):
                vals = [self.grid[r][c].value for r in range(br, br + 3) for c in range(bc, bc + 3)]
                if len(set(vals)) != 9 or 0 in vals:
                    return False
        return True

    def solve(self) -> SolverStatus:
        import time
        t0 = time.time()
        self.solve_steps = 0
        status = self._solve_recursive()
        self.solve_time_ms = (time.time() - t0) * 1000
        if status == SolverStatus.SOLVED:
            self.solution = [[self.grid[r][c].value for c in range(9)] for r in range(9)]
        return status

    def _solve_recursive(self) -> SolverStatus:
        self.solve_steps += 1
        if self.solve_steps > 100000:
            return SolverStatus.ERROR

        # Find first empty cell
        for r in range(9):
            for c in range(9):
                if self.grid[r][c].value == 0:
                    for num in range(1, 10):
                        if self._can_place(r, c, num):
                            self.grid[r][c].value = num
                            result = self._solve_recursive()
                            if result == SolverStatus.SOLVED:
                                return SolverStatus.SOLVED
                            self.grid[r][c].value = 0
                    return SolverStatus.NO_SOLUTION
        return SolverStatus.SOLVED if self._is_valid() else SolverStatus.NO_SOLUTION

    def _can_place(self, row: int, col: int, num: int) -> bool:
        if num in self.get_row(row):
            return False
        if num in self.get_col_values(col):
            return False
        if num in self.get_box(row, col):
            return False
        return True

    def hint(self) -> Optional[Tuple[int, int, int]]:
        """Return one solved cell (row, col, value) or None."""
        for r in range(9):
            for c in range(9):
                if self.grid[r][c].value == 0:
                    for num in range(1, 10):
                        if self._can_place(r, c, num):
                            return (r, c, num)
        return None

    def to_string(self) -> str:
        lines = []
        for r in range(9):
            vals = []
            for c in range(9):
                v = self.grid[r][c].value
                vals.append(str(v) if v != 0 else ".")
                if c in (2, 5):
                    vals.append(" ")
            lines.append(" ".join(vals))
            if r in (2, 5):
                lines.append("")
        return "\n".join(lines)


def create_easy_sudoku() -> SudokuPuzzle:
    p = SudokuPuzzle(difficulty=Difficulty.EASY)
    clues = [
        (0, 0, 5), (0, 1, 3), (0, 4, 7), (1, 0, 6), (1, 3, 1), (1, 4, 9), (1, 5, 5),
        (2, 1, 9), (2, 2, 8), (2, 7, 6),
        (3, 0, 8), (3, 4, 6), (3, 8, 3),
        (4, 0, 4), (4, 3, 8), (4, 5, 3), (4, 8, 1),
        (5, 0, 7), (5, 4, 2), (5, 8, 6),
        (6, 1, 6), (6, 6, 2), (6, 7, 8),
        (7, 3, 4), (7, 4, 1), (7, 5, 9), (7, 8, 5),
        (8, 4, 8), (8, 7, 7), (8, 8, 9),
    ]
    for r, c, v in clues:
        p.set_cell(r, c, v, given=True)
    return p


# --- Crossword Clue / Grid ---

@dataclass
class CrosswordClue:
    number: int
    direction: str  # "across" or "down"
    clue: str
    answer: str
    row: int = 0
    col: int = 0
    solved: bool = False
    user_answer: str = ""


@dataclass
class CrosswordGrid:
    size: int = 15
    cells: List[List[str]] = field(default_factory=list)
    black: List[List[bool]] = field(default_factory=list)
    clues: List[CrosswordClue] = field(default_factory=list)
    status: SolverStatus = SolverStatus.IDLE

    def __post_init__(self):
        if not self.cells:
            self.cells = [["" for _ in range(self.size)] for _ in range(self.size)]
        if not self.black:
            self.black = [[False for _ in range(self.size)] for _ in range(self.size)]

    def set_black(self, row: int, col: int, val: bool = True):
        if 0 <= row < self.size and 0 <= col < self.size:
            self.black[row][col] = val
            self.cells[row][col] = "#"

    def set_cell(self, row: int, col: int, letter: str):
        if 0 <= row < self.size and 0 <= col < self.size and not self.black[row][col]:
            self.cells[row][col] = letter.upper()

    def add_clue(self, num: int, direction: str, clue: str, answer: str, row: int, col: int):
        self.clues.append(CrosswordClue(num, direction, clue, answer.upper(), row, col))

    def try_solve(self) -> SolverStatus:
        self.status = SolverStatus.SOLVING
        # Solve across
        for clue in self.clues:
            if clue.direction == "across":
                r, c = clue.row, clue.col
                for i, ch in enumerate(clue.answer):
                    self.cells[r][c + i] = ch
                clue.solved = True
        # Solve down
        for clue in self.clues:
            if clue.direction == "down":
                r, c = clue.row, clue.col
                for i, ch in enumerate(clue.answer):
                    self.cells[r + i][c] = ch
                clue.solved = True
        self.status = SolverStatus.SOLVED
        return self.status

    def check_answer(self, clue: CrosswordClue) -> bool:
        return clue.user_answer.upper() == clue.answer

    @property
    def solved_count(self) -> int:
        return sum(1 for c in self.clues if c.solved)

    @property
    def total_clues(self) -> int:
        return len(self.clues)

    def to_string(self) -> str:
        lines = []
        for r in range(self.size):
            row = []
            for c in range(self.size):
                if self.black[r][c]:
                    row.append("██")
                elif self.cells[r][c]:
                    row.append(f" {self.cells[r][c]}")
                else:
                    row.append(" ·")
            lines.append("".join(row))
        return "\n".join(lines)


def create_easy_crossword() -> CrosswordGrid:
    g = CrosswordGrid(size=13)
    # Black squares (symmetric pattern)
    blacks = [
        (0, 4), (0, 10), (1, 4), (1, 10),
        (2, 2), (2, 6), (2, 10),
        (3, 8),
        (4, 0), (4, 4), (4, 8),
        (5, 6),
        (6, 2), (6, 4), (6, 8),
        (7, 6),
        (8, 0), (8, 4), (8, 8),
        (9, 8),
        (10, 2), (10, 6), (10, 10),
        (11, 2), (11, 8),
        (12, 2), (12, 10),
    ]
    for r, c in blacks:
        g.set_black(r, c)
    g.add_clue(1, "across", "Operating system kernel component", "COMPOSITOR", 0, 0)
    g.add_clue(1, "down", "Graphical processing unit abbreviation", "GPU", 0, 0)
    g.add_clue(2, "across", "Linux display server protocol", "WAYLAND", 0, 5)
    g.add_clue(3, "across", "Window manager display backend", "GBM", 2, 0)
    g.add_clue(4, "across", "Graphics rendering interface", "EGL", 2, 7)
    return g


# --- Logic Grid Puzzle ---

@dataclass
class LogicFact:
    category: str
    entity: str
    attribute: str
    value: str
    is_clue: bool = True


@dataclass
class LogicPuzzle:
    categories: Dict[str, List[str]] = field(default_factory=dict)
    facts: List[LogicFact] = field(default_factory=list)
    grid: Dict[Tuple[str, str], Set[str]] = field(default_factory=dict)
    solution: Dict[str, Dict[str, str]] = field(default_factory=dict)
    status: SolverStatus = SolverStatus.IDLE
    difficulty: Difficulty = Difficulty.MEDIUM
    clues_text: List[str] = field(default_factory=list)

    def add_category(self, name: str, values: List[str]):
        self.categories[name] = values

    def add_clue(self, text: str, category: str, entity: str, value: str):
        self.facts.append(LogicFact(category, entity, value, value, True))
        self.clues_text.append(text)

    def init_grid(self):
        cats = list(self.categories.keys())
        for i, c1 in enumerate(cats):
            for j, c2 in enumerate(cats):
                if i != j:
                    for v1 in self.categories[c1]:
                        self.grid[(c1, v1, c2)] = set(self.categories[c2])
        for cat, vals in self.categories.items():
            for v in vals:
                self.solution[cat] = self.solution.get(cat, {})
                self.solution[cat][v] = ""

    def mark_match(self, cat1: str, v1: str, cat2: str, v2: str):
        key = (cat1, v1, cat2)
        if key in self.grid:
            self.grid[key] = {v2}
        # Remove v2 from all other entries in cat1 -> cat2
        for other_v1 in self.categories.get(cat1, []):
            other_key = (cat1, other_v1, cat2)
            if other_key in self.grid and other_v1 != v1:
                self.grid[other_key].discard(v2)
                if not self.grid[other_key]:
                    self.grid[other_key] = {"_"}

    def mark_exclusion(self, cat1: str, v1: str, cat2: str, v2: str):
        key = (cat1, v1, cat2)
        if key in self.grid:
            self.grid[key].discard(v2)

    def is_consistent(self) -> bool:
        for key, vals in self.grid.items():
            clean = {v for v in vals if v != "_"}
            if len(clean) > 1:
                return False
        return True

    def solve(self) -> SolverStatus:
        self.status = SolverStatus.SOLVING
        self.init_grid()
        # Apply direct facts
        for fact in self.facts:
            for cat, vals in self.categories.items():
                if fact.entity in vals and fact.value in vals:
                    if fact.category == cat:
                        pass
        self.status = SolverStatus.SOLVED if self.is_consistent() else SolverStatus.NO_SOLUTION
        return self.status

    @property
    def solved_clues(self) -> int:
        return sum(1 for f in self.facts if f.is_clue)

    @property
    def total_categories(self) -> int:
        return len(self.categories)


def create_logic_puzzle() -> LogicPuzzle:
    p = LogicPuzzle(difficulty=Difficulty.MEDIUM)
    p.add_category("Name", ["Alice", "Bob", "Carol", "Dave"])
    p.add_category("Color", ["Red", "Blue", "Green", "Yellow"])
    p.add_category("Pet", ["Dog", "Cat", "Bird", "Fish"])
    p.add_category("Car", ["Tesla", "Honda", "Toyota", "Ford"])
    p.clues_text = [
        "Alice does not have the Red car",
        "Bob has the Dog",
        "The person with the Blue car has a Cat",
        "Carol has the Green car",
        "Dave does not have the Yellow car",
        "The person with the Fish drives a Toyota",
        "Alice drives a Tesla",
        "The person with the Bird has the Yellow car",
    ]
    return p


# --- Main Solver Manager ---

class PuzzleSolver:
    def __init__(self):
        self.sudoku: Optional[SudokuPuzzle] = None
        self.crossword: Optional[CrosswordGrid] = None
        self.logic: Optional[LogicPuzzle] = None
        self.active_type: PuzzleType = PuzzleType.SUDOKU
        self.status: SolverStatus = SolverStatus.IDLE
        self.history: List[str] = []
        self._view_mode: str = "puzzles"

    @property
    def view_mode(self) -> str:
        return self._view_mode

    def new_sudoku(self, difficulty: Difficulty = Difficulty.MEDIUM) -> SudokuPuzzle:
        self.sudoku = create_easy_sudoku()
        self.sudoku.difficulty = difficulty
        self.active_type = PuzzleType.SUDOKU
        self.history.append(f"Created Sudoku ({difficulty.value})")
        return self.sudoku

    def new_crossword(self) -> CrosswordGrid:
        self.crossword = create_easy_crossword()
        self.active_type = PuzzleType.CROSSTWORD
        self.history.append("Created Crossword puzzle")
        return self.crossword

    def new_logic(self) -> LogicPuzzle:
        self.logic = create_logic_puzzle()
        self.active_type = PuzzleType.LOGIC_GRID
        self.history.append("Created Logic Grid puzzle")
        return self.logic

    def solve_active(self) -> SolverStatus:
        if self.active_type == PuzzleType.SUDOKU and self.sudoku:
            return self.sudoku.solve()
        elif self.active_type == PuzzleType.CROSSTWORD and self.crossword:
            return self.crossword.try_solve()
        elif self.active_type == PuzzleType.LOGIC_GRID and self.logic:
            return self.logic.solve()
        return SolverStatus.IDLE

    def get_hint(self) -> Optional[Tuple[int, int, int]]:
        if self.sudoku:
            return self.sudoku.hint()
        return None

    @property
    def status_text(self) -> str:
        if self.active_type == PuzzleType.SUDOKU and self.sudoku:
            return f"Sudoku: {'Solved' if self.sudoku.solved else 'Unsolved'} ({self.sudoku.difficulty.value})"
        elif self.active_type == PuzzleType.CROSSTWORD and self.crossword:
            return f"Crossword: {self.crossword.solved_count}/{self.crossword.total_clues} clues"
        elif self.active_type == PuzzleType.LOGIC_GRID and self.logic:
            return f"Logic Grid: {self.logic.total_categories} categories"
        return "No puzzle loaded"
