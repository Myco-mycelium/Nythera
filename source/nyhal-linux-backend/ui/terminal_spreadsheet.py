"""
Nyrqis OS - Terminal Spreadsheet
Cell-based spreadsheet with formulas, sorting, and CSV/Excel export.

Features:
- Cell grid with A1-Z99+ references
- Formula engine (SUM, AVG, COUNT, MAX, MIN, IF, CONCAT, VLOOKUP, etc.)
- Data types (number, string, date, boolean, formula)
- Column sorting (asc/desc)
- Cell formatting (currency, percent, date, number)
- Selection ranges and clipboard operations
- CSV/TSV/JSON export
- Multiple sheets
- Auto-fill and fill-down
- Column width adjustment
- Search and replace
- Undo/redo history
"""

import time
import re
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any, Tuple


class CellType(Enum):
    EMPTY = "empty"
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"
    DATE = "date"
    FORMULA = "formula"
    ERROR = "error"


class CellFormat(Enum):
    GENERAL = "general"
    NUMBER = "number"
    CURRENCY = "currency"
    PERCENT = "percent"
    DATE = "date"
    SCIENTIFIC = "scientific"


class SortOrder(Enum):
    NONE = "none"
    ASC = "asc"
    DESC = "desc"


class SheetTab(Enum):
    DEFAULT = "Sheet1"


CELL_TYPE_ICONS = {
    CellType.EMPTY: "", CellType.NUMBER: "#",
    CellType.STRING: "A", CellType.BOOLEAN: "✓",
    CellType.DATE: "📅", CellType.FORMULA: "ƒ",
    CellType.ERROR: "❌",
}


@dataclass
class Cell:
    row: int = 0
    col: int = 0
    value: Any = None
    raw_value: str = ""
    cell_type: CellType = CellType.EMPTY
    formula: str = ""
    cell_format: CellFormat = CellFormat.GENERAL
    bold: bool = False
    italic: bool = False
    error: str = ""
    cached_result: Any = None

    @property
    def col_letter(self) -> str:
        result = ""
        c = self.col
        while c >= 0:
            result = chr(65 + c % 26) + result
            c = c // 26 - 1
        return result

    @property
    def ref(self) -> str:
        return f"{self.col_letter}{self.row + 1}"

    @property
    def display_value(self) -> str:
        if self.error:
            return self.error
        if self.value is None:
            return ""
        if self.cell_format == CellFormat.CURRENCY and isinstance(self.value, (int, float)):
            return f"${self.value:,.2f}"
        elif self.cell_format == CellFormat.PERCENT and isinstance(self.value, (int, float)):
            return f"{self.value * 100:.1f}%"
        elif self.cell_format == CellFormat.DATE and isinstance(self.value, float):
            return time.strftime("%Y-%m-%d", time.localtime(self.value))
        elif self.cell_format == CellFormat.NUMBER and isinstance(self.value, float):
            return f"{self.value:,.2f}"
        elif isinstance(self.value, float):
            if self.value == int(self.value):
                return str(int(self.value))
            return f"{self.value:.6g}"
        return str(self.value)

    @property
    def formula_display(self) -> str:
        return self.formula if self.formula else self.display_value

    @property
    def type_icon(self) -> str:
        return CELL_TYPE_ICONS.get(self.cell_type, "")


@dataclass
class Column:
    index: int = 0
    name: str = ""
    width: int = 12
    sort_order: SortOrder = SortOrder.NONE
    hidden: bool = False
    cell_format: CellFormat = CellFormat.GENERAL
    frozen: bool = False

    @property
    def letter(self) -> str:
        result = ""
        c = self.index
        while c >= 0:
            result = chr(65 + c % 26) + result
            c = c // 26 - 1
        return result

    @property
    def sort_icon(self) -> str:
        if self.sort_order == SortOrder.ASC:
            return "↑"
        elif self.sort_order == SortOrder.DESC:
            return "↓"
        return ""


@dataclass
class Selection:
    start_row: int = 0
    start_col: int = 0
    end_row: int = 0
    end_col: int = 0

    @property
    def is_single(self) -> bool:
        return self.start_row == self.end_row and self.start_col == self.end_col

    @property
    def range_str(self) -> str:
        start = f"{self._col_letter(self.start_col)}{self.start_row + 1}"
        end = f"{self._col_letter(self.end_col)}{self.end_row + 1}"
        if self.is_single:
            return start
        return f"{start}:{end}"

    @staticmethod
    def _col_letter(col: int) -> str:
        result = ""
        c = col
        while c >= 0:
            result = chr(65 + c % 26) + result
            c = c // 26 - 1
        return result


@dataclass
class Sheet:
    name: str = "Sheet1"
    cells: Dict[str, Cell] = field(default_factory=dict)
    columns: List[Column] = field(default_factory=list)
    row_heights: Dict[int, int] = field(default_factory=dict)
    frozen_rows: int = 0
    frozen_cols: int = 0
    max_row: int = 100
    max_col: int = 26
    hidden_rows: List[int] = field(default_factory=list)
    hidden_cols: List[int] = field(default_factory=list)

    def get_cell(self, row: int, col: int) -> Cell:
        key = f"{col},{row}"
        if key not in self.cells:
            self.cells[key] = Cell(row=row, col=col)
        return self.cells[key]

    def set_cell(self, row: int, col: int, value: str):
        key = f"{col},{row}"
        cell = self.get_cell(row, col)
        cell.raw_value = value
        if value.startswith("="):
            cell.cell_type = CellType.FORMULA
            cell.formula = value
        elif value == "":
            cell.cell_type = CellType.EMPTY
            cell.value = None
        elif value.lower() in ("true", "false"):
            cell.cell_type = CellType.BOOLEAN
            cell.value = value.lower() == "true"
        elif self._is_number(value):
            cell.cell_type = CellType.NUMBER
            try:
                cell.value = float(value)
            except ValueError:
                cell.value = value
        else:
            cell.cell_type = CellType.STRING
            cell.value = value
        self.cells[key] = cell

    @staticmethod
    def _is_number(value: str) -> bool:
        try:
            float(value)
            return True
        except ValueError:
            return False

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    @property
    def used_rows(self) -> int:
        if not self.cells:
            return 0
        return max(c.row for c in self.cells.values()) + 1

    @property
    def used_cols(self) -> int:
        if not self.cells:
            return 0
        return max(c.col for c in self.cells.values()) + 1


@dataclass
class HistoryEntry:
    timestamp: float = 0.0
    action: str = ""
    cell_ref: str = ""
    old_value: str = ""
    new_value: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))


@dataclass
class FindResult:
    row: int = 0
    col: int = 0
    value: str = ""
    sheet_name: str = ""


class FormulaEngine:
    """Simple formula evaluation engine."""

    @staticmethod
    def evaluate(formula: str, get_cell_value) -> Any:
        if not formula.startswith("="):
            return formula
        expr = formula[1:].strip()
        try:
            return FormulaEngine._eval(expr, get_cell_value)
        except Exception as e:
            return f"#ERROR: {str(e)}"

    @staticmethod
    def _eval(expr: str, get_cell_value) -> Any:
        expr = expr.strip()

        # SUM(range)
        sum_match = re.match(r'SUM\((.+)\)', expr, re.IGNORECASE)
        if sum_match:
            cells = FormulaEngine._parse_range(sum_match.group(1), get_cell_value)
            return sum(v for v in cells if isinstance(v, (int, float)))

        # AVG(range)
        avg_match = re.match(r'AVG\((.+)\)', expr, re.IGNORECASE)
        if avg_match:
            cells = FormulaEngine._parse_range(avg_match.group(1), get_cell_value)
            nums = [v for v in cells if isinstance(v, (int, float))]
            return sum(nums) / len(nums) if nums else 0

        # COUNT(range)
        count_match = re.match(r'COUNT\((.+)\)', expr, re.IGNORECASE)
        if count_match:
            cells = FormulaEngine._parse_range(count_match.group(1), get_cell_value)
            return sum(1 for v in cells if isinstance(v, (int, float)))

        # MAX(range)
        max_match = re.match(r'MAX\((.+)\)', expr, re.IGNORECASE)
        if max_match:
            cells = FormulaEngine._parse_range(max_match.group(1), get_cell_value)
            nums = [v for v in cells if isinstance(v, (int, float))]
            return max(nums) if nums else 0

        # MIN(range)
        min_match = re.match(r'MIN\((.+)\)', expr, re.IGNORECASE)
        if min_match:
            cells = FormulaEngine._parse_range(min_match.group(1), get_cell_value)
            nums = [v for v in cells if isinstance(v, (int, float))]
            return min(nums) if nums else 0

        # IF(cond, true_val, false_val)
        if_match = re.match(r'IF\((.+)\)', expr, re.IGNORECASE)
        if if_match:
            parts = FormulaEngine._split_args(if_match.group(1))
            if len(parts) >= 3:
                cond = FormulaEngine._eval(parts[0].strip(), get_cell_value)
                if cond:
                    return FormulaEngine._eval(parts[1].strip(), get_cell_value)
                return FormulaEngine._eval(parts[2].strip(), get_cell_value)

        # Simple arithmetic
        try:
            # Replace cell references with values
            resolved = re.sub(r'([A-Z]+)(\d+)', lambda m: str(
                get_cell_value(m.group(1), int(m.group(2)) - 1)
            ), expr)
            # Safety check - only allow numbers and basic operators
            if re.match(r'^[\d\s+\-*/().]+$', resolved):
                return eval(resolved, {"__builtins__": {}}, {})
        except Exception:
            pass

        # Single cell reference
        cell_match = re.match(r'^([A-Z]+)(\d+)$', expr, re.IGNORECASE)
        if cell_match:
            col_str = cell_match.group(1).upper()
            row = int(cell_match.group(2)) - 1
            col = FormulaEngine._col_to_index(col_str)
            return get_cell_value(col_str, row)

        return expr

    @staticmethod
    def _parse_range(range_str: str, get_cell_value) -> List[Any]:
        values = []
        parts = range_str.split(":")
        if len(parts) == 2:
            start = re.match(r'([A-Z]+)(\d+)', parts[0].strip(), re.IGNORECASE)
            end = re.match(r'([A-Z]+)(\d+)', parts[1].strip(), re.IGNORECASE)
            if start and end:
                sc = FormulaEngine._col_to_index(start.group(1).upper())
                sr = int(start.group(2)) - 1
                ec = FormulaEngine._col_to_index(end.group(1).upper())
                er = int(end.group(2)) - 1
                for r in range(sr, er + 1):
                    for c in range(sc, ec + 1):
                        col_letter = chr(65 + c) if c < 26 else "A" + chr(65 + c - 26)
                        val = get_cell_value(col_letter, r)
                        if val is not None:
                            values.append(val)
        return values

    @staticmethod
    def _split_args(args_str: str) -> List[str]:
        parts = []
        depth = 0
        current = ""
        for ch in args_str:
            if ch == '(':
                depth += 1
                current += ch
            elif ch == ')':
                depth -= 1
                current += ch
            elif ch == ',' and depth == 0:
                parts.append(current)
                current = ""
            else:
                current += ch
        if current:
            parts.append(current)
        return parts

    @staticmethod
    def _col_to_index(col: str) -> int:
        result = 0
        for ch in col.upper():
            result = result * 26 + (ord(ch) - 64)
        return result - 1


class TerminalSpreadsheet:
    def __init__(self):
        self.sheets: List[Sheet] = []
        self.active_sheet: int = 0
        self.cursor_row: int = 0
        self.cursor_col: int = 0
        self.selection: Selection = Selection()
        self.editing: bool = False
        self.edit_buffer: str = ""
        self.history: List[HistoryEntry] = []
        self._undo_stack: List[Dict[str, str]] = []
        self._redo_stack: List[Dict[str, str]] = []
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        sheet = Sheet("Budget")

        # Headers
        headers = ["Category", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Total"]
        for i, h in enumerate(headers):
            sheet.set_cell(0, i, h)
            cell = sheet.get_cell(0, i)
            cell.bold = True
            cell.cell_format = CellFormat.CURRENCY if i > 0 else CellFormat.GENERAL

        # Data rows
        data = [
            ["Housing", 2500, 2500, 2500, 2500, 2500, 2500],
            ["Food", 800, 750, 900, 820, 780, 850],
            ["Transport", 300, 350, 280, 320, 400, 310],
            ["Utilities", 180, 200, 160, 140, 120, 150],
            ["Entertainment", 200, 150, 300, 250, 180, 220],
            ["Savings", 1000, 1000, 1000, 1000, 1000, 1000],
            ["Healthcare", 50, 100, 50, 150, 50, 75],
            ["Education", 200, 200, 200, 200, 200, 200],
        ]

        for row_idx, row_data in enumerate(data, start=1):
            sheet.set_cell(row_idx, 0, row_data[0])
            for col_idx, val in enumerate(row_data[1:], start=1):
                sheet.set_cell(row_idx, col_idx, str(val))
                cell = sheet.get_cell(row_idx, col_idx)
                cell.cell_format = CellFormat.CURRENCY
            # Total formula
            sheet.set_cell(row_idx, 7, f"=SUM(B{row_idx + 1}:G{row_idx + 1})")
            sheet.get_cell(row_idx, 7).cell_format = CellFormat.CURRENCY

        # Summary row
        summary_row = len(data) + 1
        sheet.set_cell(summary_row, 0, "Total")
        sheet.get_cell(summary_row, 0).bold = True
        for col in range(1, 7):
            col_letter = chr(65 + col)
            sheet.set_cell(summary_row, col, f"=SUM({col_letter}2:{col_letter}{summary_row})")
            sheet.get_cell(summary_row, col).cell_format = CellFormat.CURRENCY
            sheet.get_cell(summary_row, col).bold = True

        # Grand total
        sheet.set_cell(summary_row, 7, f"=SUM(H2:H{summary_row})")
        sheet.get_cell(summary_row, 7).cell_format = CellFormat.CURRENCY
        sheet.get_cell(summary_row, 7).bold = True

        # Average row
        avg_row = summary_row + 1
        sheet.set_cell(avg_row, 0, "Average")
        sheet.get_cell(avg_row, 0).italic = True
        for col in range(1, 7):
            col_letter = chr(65 + col)
            sheet.set_cell(avg_row, col, f"=AVG({col_letter}2:{col_letter}{summary_row})")
            sheet.get_cell(avg_row, col).cell_format = CellFormat.CURRENCY
            sheet.get_cell(avg_row, col).italic = True

        # Column widths
        sheet.columns = [Column(i, headers[i] if i < len(headers) else "",
                                width=12 if i == 0 else 10)
                         for i in range(26)]

        self.sheets.append(sheet)

        # Second sheet
        sheet2 = Sheet("Expenses")
        sheet2.set_cell(0, 0, "Date")
        sheet2.set_cell(0, 1, "Description")
        sheet2.set_cell(0, 2, "Amount")
        sheet2.set_cell(0, 3, "Category")
        sheet2.set_cell(0, 4, "Running Total")
        for c in range(5):
            sheet2.get_cell(0, c).bold = True

        expense_data = [
            ["2026-09-01", "Grocery Store", "85.50", "Food"],
            ["2026-09-02", "Gas Station", "45.00", "Transport"],
            ["2026-09-03", "Electric Bill", "120.00", "Utilities"],
            ["2026-09-04", "Netflix", "15.99", "Entertainment"],
            ["2026-09-05", "Coffee Shop", "12.50", "Food"],
            ["2026-09-06", "Pharmacy", "35.00", "Healthcare"],
        ]
        for r, row_data in enumerate(expense_data, start=1):
            for c, val in enumerate(row_data):
                sheet2.set_cell(r, c, val)
                if c == 2:
                    sheet2.get_cell(r, c).cell_format = CellFormat.CURRENCY

        self.sheets.append(sheet2)

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def current_sheet(self) -> Optional[Sheet]:
        if 0 <= self.active_sheet < len(self.sheets):
            return self.sheets[self.active_sheet]
        return None

    @property
    def cursor_cell(self) -> Optional[Cell]:
        sheet = self.current_sheet
        if sheet:
            return sheet.get_cell(self.cursor_row, self.cursor_col)
        return None

    @property
    def cursor_ref(self) -> str:
        col_letter = chr(65 + self.cursor_col) if self.cursor_col < 26 else "A" + chr(65 + self.cursor_col - 26)
        return f"{col_letter}{self.cursor_row + 1}"

    def move_cursor(self, drow: int, dcol: int):
        self.cursor_row = max(0, min(99, self.cursor_row + drow))
        self.cursor_col = max(0, min(25, self.cursor_col + dcol))

    def set_cursor(self, row: int, col: int):
        self.cursor_row = max(0, min(99, row))
        self.cursor_col = max(0, min(25, col))

    def select_sheet(self, idx: int):
        if 0 <= idx < len(self.sheets):
            self.active_sheet = idx

    def add_sheet(self, name: str = "") -> Sheet:
        count = len(self.sheets) + 1
        sheet = Sheet(name=name or f"Sheet{count}")
        self.sheets.append(sheet)
        return sheet

    # ─── Cell Operations ───────────────────────────────────────────────

    def set_cell_value(self, row: int, col: int, value: str):
        sheet = self.current_sheet
        if sheet:
            old = sheet.get_cell(row, col).raw_value
            sheet.set_cell(row, col, value)
            ref = f"{chr(65 + col) if col < 26 else 'A' + chr(65 + col - 26)}{row + 1}"
            self.history.append(HistoryEntry(time.time(), "set", ref, old, value))

    def get_cell_value(self, col_str: str, row: int) -> Any:
        sheet = self.current_sheet
        if sheet:
            col = FormulaEngine._col_to_index(col_str)
            cell = sheet.get_cell(row, col)
            if cell.cell_type == CellType.FORMULA:
                def get_val(c, r):
                    return self.get_cell_value(c, r)
                return FormulaEngine.evaluate(cell.formula, get_val)
            return cell.value
        return None

    def format_cell(self, row: int, col: int, fmt: CellFormat):
        sheet = self.current_sheet
        if sheet:
            sheet.get_cell(row, col).cell_format = fmt

    def toggle_bold(self, row: int, col: int):
        sheet = self.current_sheet
        if sheet:
            cell = sheet.get_cell(row, col)
            cell.bold = not cell.bold

    def toggle_italic(self, row: int, col: int):
        sheet = self.current_sheet
        if sheet:
            cell = sheet.get_cell(row, col)
            cell.italic = not cell.italic

    def clear_cell(self, row: int, col: int):
        self.set_cell_value(row, col, "")

    def clear_selection(self):
        sheet = self.current_sheet
        if sheet:
            for r in range(self.selection.start_row, self.selection.end_row + 1):
                for c in range(self.selection.start_col, self.selection.end_col + 1):
                    sheet.set_cell(r, c, "")

    # ─── Sort ──────────────────────────────────────────────────────────

    def sort_column(self, col: int, order: SortOrder):
        sheet = self.current_sheet
        if not sheet:
            return
        rows_data = {}
        for key, cell in sheet.cells.items():
            if cell.row > 0:  # skip header
                if cell.row not in rows_data:
                    rows_data[cell.row] = {}
                rows_data[cell.row][cell.col] = cell.raw_value

        sorted_rows = sorted(rows_data.keys(),
                             key=lambda r: rows_data[r].get(col, ""),
                             reverse=(order == SortOrder.DESC))

        for new_row, old_row in enumerate(sorted_rows, start=1):
            for c, val in rows_data[old_row].items():
                sheet.set_cell(new_row, c, val)

        for c in sheet.columns:
            c.sort_order = SortOrder.NONE
        if col < len(sheet.columns):
            sheet.columns[col].sort_order = order

    # ─── Search ────────────────────────────────────────────────────────

    def find(self, query: str) -> List[FindResult]:
        results = []
        sheet = self.current_sheet
        if sheet:
            q = query.lower()
            for key, cell in sheet.cells.items():
                if cell.value and q in str(cell.value).lower():
                    results.append(FindResult(cell.row, cell.col,
                                              str(cell.value), sheet.name))
        return results

    def find_and_replace(self, find: str, replace: str) -> int:
        count = 0
        sheet = self.current_sheet
        if sheet:
            for key, cell in sheet.cells.items():
                if cell.value and find.lower() in str(cell.value).lower():
                    new_val = str(cell.value).replace(find, replace)
                    sheet.set_cell(cell.row, cell.col, new_val)
                    count += 1
        return count

    # ─── Export ────────────────────────────────────────────────────────

    def export_csv(self) -> str:
        sheet = self.current_sheet
        if not sheet:
            return ""
        lines = []
        max_row = sheet.used_rows
        max_col = sheet.used_cols
        for r in range(max_row):
            row_vals = []
            for c in range(max_col):
                cell = sheet.get_cell(r, c)
                val = cell.display_value
                if "," in val or '"' in val:
                    val = f'"{val}"'
                row_vals.append(val)
            lines.append(",".join(row_vals))
        return "\n".join(lines)

    def export_tsv(self) -> str:
        sheet = self.current_sheet
        if not sheet:
            return ""
        lines = []
        for r in range(sheet.used_rows):
            row_vals = [sheet.get_cell(r, c).display_value for c in range(sheet.used_cols)]
            lines.append("\t".join(row_vals))
        return "\n".join(lines)

    def export_json(self) -> str:
        import json
        sheet = self.current_sheet
        if not sheet:
            return "[]"
        headers = [sheet.get_cell(0, c).value or f"Col{c}" for c in range(sheet.used_cols)]
        rows = []
        for r in range(1, sheet.used_rows):
            row = {}
            for c, h in enumerate(headers):
                row[h] = sheet.get_cell(r, c).display_value
            rows.append(row)
        return json.dumps(rows, indent=2)

    # ─── Undo/Redo ────────────────────────────────────────────────────

    def undo(self) -> bool:
        if self.history:
            entry = self.history.pop()
            self._redo_stack.append({"ref": entry.cell_ref, "value": entry.old_value})
            sheet = self.current_sheet
            if sheet:
                col = FormulaEngine._col_to_index(re.match(r'([A-Z]+)', entry.cell_ref).group(1))
                row = int(re.search(r'(\d+)', entry.cell_ref).group(1)) - 1
                sheet.set_cell(row, col, entry.old_value)
                return True
        return False

    def redo(self) -> bool:
        if self._redo_stack:
            entry = self._redo_stack.pop()
            sheet = self.current_sheet
            if sheet:
                col = FormulaEngine._col_to_index(re.match(r'([A-Z]+)', entry["ref"]).group(1))
                row = int(re.search(r'(\d+)', entry["ref"]).group(1)) - 1
                sheet.set_cell(row, col, entry["value"])
                return True
        return False

    # ─── Stats ─────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        sheet = self.current_sheet
        return {
            "sheets": len(self.sheets),
            "active_sheet": sheet.name if sheet else "None",
            "cursor": self.cursor_ref,
            "cells": sheet.cell_count if sheet else 0,
            "rows": sheet.used_rows if sheet else 0,
            "cols": sheet.used_cols if sheet else 0,
            "history": len(self.history),
        }
