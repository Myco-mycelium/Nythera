#!/usr/bin/env python3
"""calculator — Nyrqis calculator application.

A full-featured calculator for the Nyrqis desktop with:

- Basic arithmetic (+, -, ×, ÷)
- Scientific functions (sin, cos, tan, log, ln, sqrt, pow, factorial)
- Memory (M+, M-, MR, MC)
- Calculation history
- Parentheses support
- Percentage and reciprocal
- Constant display (π, e)
- Keyboard input support

Usage::

    from ui.calculator import Calculator
    calc = Calculator()
    calc.press("5")
    calc.press("+")
    calc.press("3")
    calc.press("=")
    assert calc.display == "8"

References:
    - NFS-001 §5: component vocabulary
    - doc #14: Nyrqis Desktop Shell
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# PIL imported lazily to avoid 5-15s import penalty in containers.
_PIL_AVAILABLE: Optional[bool] = None


def _ensure_pil():
    global _PIL_AVAILABLE
    if _PIL_AVAILABLE is not None:
        if _PIL_AVAILABLE is False:
            raise ImportError("PIL/Pillow is required: pip install Pillow")
        return
    try:
        from PIL import Image as _Img  # noqa: F401
        _PIL_AVAILABLE = True
    except ImportError:
        _PIL_AVAILABLE = False
        raise ImportError("PIL/Pillow is required: pip install Pillow")


def _pil():
    _ensure_pil()
    from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont


@dataclass
class CalcEntry:
    """A single entry in the calculator history."""
    expression: str
    result: str
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class Calculator:
    """Nyrqis calculator with scientific functions and history.

    The calculator maintains an internal state machine:

    - **display**: the current number shown (str).
    - **_stack**: the expression being built (list of tokens).
    - **_operator**: the pending binary operator.
    - **_operand**: the left operand stored after pressing an operator.
    - **_just_evaluated**: True after pressing = (next digit starts fresh).
    - **_paren_depth**: open-parentheses count for validation.
    - **_memory**: stored memory value.
    """

    def __init__(self) -> None:
        self._display: str = "0"
        self._stack: List[str] = []
        self._operator: Optional[str] = None
        self._operand: Optional[float] = None
        self._just_evaluated: bool = False
        self._paren_depth: int = 0
        self._memory: float = 0.0
        self._history: List[CalcEntry] = []
        self._error: bool = False
        self._callbacks: List[Callable] = []
        self._visible: bool = False
        self._angle_mode: str = "deg"  # "deg" or "rad"

    # -- Display ------------------------------------------------------

    @property
    def display(self) -> str:
        """The current display value."""
        return self._display

    @property
    def error(self) -> bool:
        return self._error

    @property
    def expression(self) -> str:
        """The current expression string."""
        parts = []
        if self._operand is not None:
            parts.append(self._format_number(self._operand))
        if self._operator:
            parts.append(self._operator)
        parts.append(self._display)
        return " ".join(parts)

    @property
    def history(self) -> List[CalcEntry]:
        return list(self._history)

    @property
    def memory(self) -> float:
        return self._memory

    @property
    def angle_mode(self) -> str:
        return self._angle_mode

    # -- Button presses -----------------------------------------------

    def press(self, key: str) -> None:
        """Press a calculator button.

        Supported keys:
        - Digits: 0-9, .
        - Operators: +, -, *, /, ×, ÷
        - Equals: =
        - Functions: sin, cos, tan, log, ln, sqrt, pow, factorial, abs
        - Parentheses: (, )
        - Special: %, 1/x, π, e, ±, C, CE, backspace
        - Memory: m+, m-, mr, mc
        - History: ans
        """
        if self._error and key not in ("C", "CE"):
            return

        key = key.strip().lower()

        # Digit input
        if key in "0123456789":
            self._input_digit(key)
        elif key == ".":
            self._input_decimal()
        # Operators
        elif key in ("+", "-", "*", "/", "×", "÷"):
            self._input_operator(key)
        # Equals
        elif key == "=":
            self._evaluate()
        # Clear
        elif key == "c":
            self._clear()
        elif key == "ce":
            self._clear_entry()
        elif key == "backspace" or key == "back":
            self._backspace()
        # Parentheses
        elif key == "(":
            self._input_open_paren()
        elif key == ")":
            self._input_close_paren()
        # Functions
        elif key in ("sin", "cos", "tan", "asin", "acos", "atan",
                      "log", "ln", "sqrt", "abs", "factorial"):
            self._apply_unary_function(key)
        elif key == "pow" or key == "^":
            self._input_operator("^")
        # Special
        elif key == "%":
            self._apply_percent()
        elif key == "1/x":
            self._apply_reciprocal()
        elif key == "±" or key == "negate":
            self._negate()
        elif key == "π" or key == "pi":
            self._input_constant(math.pi)
        elif key == "e":
            self._input_constant(math.e)
        elif key == "ans":
            self._recall_answer()
        # Memory
        elif key == "m+":
            self._memory_add()
        elif key == "m-":
            self._memory_subtract()
        elif key == "mr":
            self._memory_recall()
        elif key == "mc":
            self._memory_clear()
        # Angle mode
        elif key == "deg":
            self._angle_mode = "deg"
        elif key == "rad":
            self._angle_mode = "rad"

        self._notify("press", key)

    # -- History ------------------------------------------------------

    def clear_history(self) -> int:
        count = len(self._history)
        self._history.clear()
        return count

    def get_history_entry(self, index: int) -> Optional[CalcEntry]:
        if 0 <= index < len(self._history):
            return self._history[index]
        return None

    # -- Visibility ---------------------------------------------------

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def toggle(self) -> bool:
        self._visible = not self._visible
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    # -- Callbacks ----------------------------------------------------

    def on_press(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    # -- Rendering ----------------------------------------------------

    def render(
        self,
        width: int = 320,
        height: int = 480,
        theme: Optional[Dict] = None,
    ):
        """Render the calculator to a PIL Image."""
        Image, ImageDraw, ImageFont = _pil()
        if theme is None:
            theme = {
                "background": (30, 30, 30),
                "surface": (45, 45, 45),
                "display_bg": (25, 25, 25),
                "text_primary": (230, 230, 230),
                "text_secondary": (150, 150, 150),
                "accent": (100, 149, 237),
                "btn_number": (60, 60, 60),
                "btn_operator": (255, 159, 10),
                "btn_function": (80, 80, 80),
                "btn_equal": (100, 149, 237),
                "btn_text": (230, 230, 230),
            }

        img = Image.new("RGB", (width, height), theme["background"])
        draw = ImageDraw.Draw(img)

        try:
            font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
            font_medium = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except (OSError, IOError):
            font_large = font_medium = font_small = ImageFont.load_default()

        # Display area
        display_h = 100
        draw.rectangle([0, 0, width, display_h], fill=theme["display_bg"])
        # Expression
        expr = self.expression
        if len(expr) > 40:
            expr = "..." + expr[-37:]
        draw.text((16, 15), expr, fill=theme["text_secondary"], font=font_small)
        # Main display
        display_text = self._display
        if len(display_text) > 15:
            display_text = display_text[:15] + "..."
        draw.text((16, 45), display_text, fill=theme["text_primary"], font=font_large)
        # Error indicator
        if self._error:
            draw.text((width - 60, 80), "ERR", fill=(255, 80, 80), font=font_small)
        # Memory indicator
        if self._memory != 0:
            draw.text((16, 80), "M", fill=theme["accent"], font=font_small)

        # Button grid
        btn_h = (height - display_h) // 5
        btn_w = width // 4
        pad = 2

        buttons = [
            # Row 0: functions
            ["sin", "cos", "tan", "π"],
            # Row 1: memory + clear
            ["m+", "m-", "mr", "mc"],
            # Row 2: parens, clear, backspace
            ["(", ")", "C", "⌫"],
            # Row 3: numbers + operators
            ["7", "8", "9", "÷"],
            ["4", "5", "6", "×"],
            ["1", "2", "3", "-"],
            ["0", ".", "±", "+"],
            ["=", "=", "=", "="],
        ]

        for row_idx, row in enumerate(buttons):
            for col_idx, btn in enumerate(row):
                if btn == "=" and col_idx > 0:
                    continue  # = spans multiple columns
                x = col_idx * btn_w + pad
                y = display_h + row_idx * btn_h + pad
                w = btn_w - 2 * pad
                h = btn_h - 2 * pad

                # Button color
                if btn in ("=",):
                    color = theme["btn_equal"]
                elif btn in ("+", "-", "×", "÷", "^"):
                    color = theme["btn_operator"]
                elif btn in ("sin", "cos", "tan", "π", "m+", "m-", "mr", "mc",
                             "(", ")", "C", "⌫"):
                    color = theme["btn_function"]
                else:
                    color = theme["btn_number"]

                draw.rounded_rectangle(
                    [x, y, x + w, y + h],
                    radius=8, fill=color)

                # Button label
                bbox = draw.textbbox((0, 0), btn, font=font_medium)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                draw.text(
                    (x + (w - tw) // 2, y + (h - th) // 2),
                    btn, fill=theme["btn_text"], font=font_medium)

        return img

    # -- Internal: digit input ----------------------------------------

    def _input_digit(self, digit: str) -> None:
        if self._just_evaluated:
            self._display = digit
            self._just_evaluated = False
        elif self._display == "0":
            self._display = digit
        else:
            self._display += digit

    def _input_decimal(self) -> None:
        if self._just_evaluated:
            self._display = "0."
            self._just_evaluated = False
        elif "." not in self._display:
            self._display += "."

    def _input_operator(self, op: str) -> None:
        # Normalize display operators
        op_map = {"×": "*", "÷": "/"}
        internal_op = op_map.get(op, op)

        if self._just_evaluated:
            self._operand = float(self._display)
            self._operator = internal_op
            self._just_evaluated = False
            return

        if self._operator and self._operand is not None:
            # Chain evaluation
            result = self._compute(self._operator, self._operand, float(self._display))
            self._operand = result
        else:
            self._operand = float(self._display)

        self._operator = internal_op
        self._display = self._format_number(self._operand)
        self._display = "0"  # reset for next operand

    def _input_open_paren(self) -> None:
        if self._just_evaluated:
            self._display = "0"
            self._just_evaluated = False
        self._display += "("
        self._paren_depth += 1

    def _input_close_paren(self) -> None:
        if self._paren_depth > 0:
            self._paren_depth -= 1
            self._display += ")"

    def _input_constant(self, value: float) -> None:
        if self._just_evaluated:
            self._just_evaluated = False
        self._display = self._format_number(value)

    def _recall_answer(self) -> None:
        if self._history:
            self._display = self._history[-1].result
            self._just_evaluated = True

    # -- Internal: evaluation -----------------------------------------

    def _evaluate(self) -> None:
        try:
            if self._operator and self._operand is not None:
                result = self._compute(
                    self._operator, self._operand, float(self._display))
                expr = (f"{self._format_number(self._operand)} "
                        f"{self._operator} "
                        f"{self._format_number(float(self._display))}")
                self._history.append(CalcEntry(
                    expression=expr,
                    result=self._format_number(result),
                ))
                self._display = self._format_number(result)
                self._operand = None
                self._operator = None
                self._just_evaluated = True
            else:
                # Try evaluating the display as a math expression
                try:
                    result = self._safe_eval(self._display)
                    if result != float(self._display):
                        self._history.append(CalcEntry(
                            expression=self._display,
                            result=self._format_number(result),
                        ))
                    self._display = self._format_number(result)
                    self._just_evaluated = True
                except Exception:
                    pass
        except (ZeroDivisionError, ValueError, OverflowError) as e:
            self._display = "Error"
            self._error = True
            self._log(f"Error: {e}")

    def _compute(self, op: str, left: float, right: float) -> float:
        if op == "+":
            return left + right
        elif op == "-":
            return left - right
        elif op == "*":
            return left * right
        elif op == "/":
            if right == 0:
                raise ZeroDivisionError("Division by zero")
            return left / right
        elif op == "^":
            return math.pow(left, right)
        raise ValueError(f"Unknown operator: {op}")

    def _safe_eval(self, expr: str) -> float:
        """Evaluate a simple math expression safely."""
        # Replace display operators
        expr = expr.replace("×", "*").replace("÷", "/")
        # Only allow digits, operators, parens, dots
        allowed = set("0123456789.+-*/() ")
        if not all(c in allowed for c in expr):
            raise ValueError("Invalid expression")
        return float(eval(expr))  # noqa: S307 — restricted by allowed chars

    # -- Internal: unary functions ------------------------------------

    def _apply_unary_function(self, func: str) -> None:
        try:
            val = float(self._display)
            if func == "sin":
                if self._angle_mode == "deg":
                    val = math.sin(math.radians(val))
                else:
                    val = math.sin(val)
            elif func == "cos":
                if self._angle_mode == "deg":
                    val = math.cos(math.radians(val))
                else:
                    val = math.cos(val)
            elif func == "tan":
                if self._angle_mode == "deg":
                    val = math.tan(math.radians(val))
                else:
                    val = math.tan(val)
            elif func == "asin":
                val = math.asin(val)
                if self._angle_mode == "deg":
                    val = math.degrees(val)
            elif func == "acos":
                val = math.acos(val)
                if self._angle_mode == "deg":
                    val = math.degrees(val)
            elif func == "atan":
                val = math.atan(val)
                if self._angle_mode == "deg":
                    val = math.degrees(val)
            elif func == "log":
                val = math.log10(val)
            elif func == "ln":
                val = math.log(val)
            elif func == "sqrt":
                val = math.sqrt(val)
            elif func == "abs":
                val = abs(val)
            elif func == "factorial":
                val = math.factorial(int(val))

            self._display = self._format_number(val)
            self._just_evaluated = True
        except (ValueError, OverflowError, ZeroDivisionError) as e:
            self._display = "Error"
            self._error = True
            self._log(f"Function error ({func}): {e}")

    def _apply_percent(self) -> None:
        try:
            val = float(self._display)
            if self._operand is not None and self._operator:
                val = self._operand * val / 100
            else:
                val = val / 100
            self._display = self._format_number(val)
        except (ValueError, OverflowError):
            pass

    def _apply_reciprocal(self) -> None:
        try:
            val = float(self._display)
            if val == 0:
                raise ZeroDivisionError("Reciprocal of zero")
            self._display = self._format_number(1.0 / val)
            self._just_evaluated = True
        except (ValueError, ZeroDivisionError) as e:
            self._display = "Error"
            self._error = True
            self._log(f"Reciprocal error: {e}")

    def _negate(self) -> None:
        try:
            val = float(self._display)
            self._display = self._format_number(-val)
        except ValueError:
            pass

    # -- Internal: clear / backspace ----------------------------------

    def _clear(self) -> None:
        self._display = "0"
        self._operator = None
        self._operand = None
        self._just_evaluated = False
        self._paren_depth = 0
        self._error = False

    def _clear_entry(self) -> None:
        self._display = "0"
        self._error = False

    def _backspace(self) -> None:
        if len(self._display) > 1:
            self._display = self._display[:-1]
        else:
            self._display = "0"

    # -- Internal: memory ---------------------------------------------

    def _memory_add(self) -> None:
        try:
            self._memory += float(self._display)
        except ValueError:
            pass

    def _memory_subtract(self) -> None:
        try:
            self._memory -= float(self._display)
        except ValueError:
            pass

    def _memory_recall(self) -> None:
        self._display = self._format_number(self._memory)
        self._just_evaluated = True

    def _memory_clear(self) -> None:
        self._memory = 0.0

    # -- Internal: formatting -----------------------------------------

    def _format_number(self, value: float) -> str:
        """Format a number for display."""
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        if abs(value) < 0.0001 or abs(value) > 1e15:
            return f"{value:.6e}"
        # Remove trailing zeros
        formatted = f"{value:.10g}"
        return formatted

    # -- Internal: callbacks ------------------------------------------

    def _notify(self, event: str, data: Any = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event, data)
            except Exception as e:
                self._log(f"Callback error: {e}")

    def _log(self, msg: str) -> None:
        logger.info("[Calculator] %s", msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run the calculator standalone (for testing)."""
    calc = Calculator()

    print("=== Nyrqis Calculator ===")

    # Basic arithmetic
    calc.press("5")
    calc.press("+")
    calc.press("3")
    calc.press("=")
    print(f"5 + 3 = {calc.display}")
    assert calc.display == "8", f"Expected 8, got {calc.display}"

    calc.press("C")
    calc.press("1")
    calc.press("0")
    calc.press("*")
    calc.press("2")
    calc.press("=")
    print(f"10 × 2 = {calc.display}")
    assert calc.display == "20", f"Expected 20, got {calc.display}"

    # Division
    calc.press("C")
    calc.press("1")
    calc.press("5")
    calc.press("/")
    calc.press("3")
    calc.press("=")
    print(f"15 ÷ 3 = {calc.display}")
    assert calc.display == "5", f"Expected 5, got {calc.display}"

    # Division by zero
    calc.press("C")
    calc.press("5")
    calc.press("/")
    calc.press("0")
    calc.press("=")
    print(f"5 ÷ 0 = {calc.display}")
    assert calc.display == "Error", f"Expected Error, got {calc.display}"

    # Clear
    calc.press("C")
    assert calc.display == "0"
    assert not calc.error

    # Decimal
    calc.press("C")
    calc.press("3")
    calc.press(".")
    calc.press("1")
    calc.press("4")
    print(f"3.14 = {calc.display}")
    assert calc.display == "3.14", f"Expected 3.14, got {calc.display}"

    # Scientific functions
    calc.press("C")
    calc.press("9")
    calc.press("sqrt")
    print(f"√9 = {calc.display}")
    assert calc.display == "3", f"Expected 3, got {calc.display}"

    calc.press("C")
    calc.press("0")
    calc.press("sin")
    print(f"sin(0) = {calc.display}")
    assert calc.display == "0", f"Expected 0, got {calc.display}"

    # Memory
    calc.press("C")
    calc.press("4")
    calc.press("2")
    calc.press("m+")
    calc.press("C")
    calc.press("mr")
    print(f"Memory recall = {calc.display}")
    assert calc.display == "42", f"Expected 42, got {calc.display}"
    calc.press("mc")

    # History
    print(f"History entries: {len(calc.history)}")
    assert len(calc.history) > 0

    # Negate
    calc.press("C")
    calc.press("5")
    calc.press("±")
    print(f"Negate 5 = {calc.display}")
    assert calc.display == "-5", f"Expected -5, got {calc.display}"

    # Reciprocal
    calc.press("C")
    calc.press("4")
    calc.press("1/x")
    print(f"1/4 = {calc.display}")
    assert calc.display == "0.25", f"Expected 0.25, got {calc.display}"

    # Constants
    calc.press("C")
    calc.press("π")
    print(f"π = {calc.display}")
    assert calc.display == "3.141592654", f"Expected pi, got {calc.display}"

    # Render
    img = calc.render(320, 480)
    print(f"Rendered: {img.size}")

    print("\nAll calculator operations passed!")


if __name__ == "__main__":
    main()
