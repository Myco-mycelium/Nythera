"""
Nyrqis Unit Converter — convert between measurement units.

Features:
- Length (meter, km, mile, foot, inch, yard, cm, mm)
- Weight (kg, g, mg, pound, ounce, ton)
- Temperature (Celsius, Fahrenheit, Kelvin)
- Volume (liter, ml, gallon, cup, tablespoon, teaspoon)
- Speed (km/h, mph, m/s, knot)
- Data (byte, KB, MB, GB, TB, PB)
- Time (second, minute, hour, day, week, month, year)
- Currency (USD, EUR, GBP, JPY, CAD, AUD)
- Quick conversion display
- History of recent conversions
- Keyboard navigation
"""

import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class UnitCategory(Enum):
    LENGTH = "Length"
    WEIGHT = "Weight"
    TEMPERATURE = "Temperature"
    VOLUME = "Volume"
    SPEED = "Speed"
    DATA = "Data Storage"
    TIME = "Time"
    CURRENCY = "Currency"


CATEGORY_ICONS = {
    UnitCategory.LENGTH: "📏",
    UnitCategory.WEIGHT: "⚖️",
    UnitCategory.TEMPERATURE: "🌡️",
    UnitCategory.VOLUME: "🧪",
    UnitCategory.SPEED: "💨",
    UnitCategory.DATA: "💾",
    UnitCategory.TIME: "⏱️",
    UnitCategory.CURRENCY: "💱",
}


@dataclass
class Unit:
    """A measurement unit."""
    name: str
    symbol: str
    factor: float = 1.0  # Conversion factor to base unit
    category: UnitCategory = UnitCategory.LENGTH

    @property
    def display(self) -> str:
        return f"{self.name} ({self.symbol})"


@dataclass
class ConversionResult:
    """Result of a unit conversion."""
    value: float
    from_unit: str
    to_unit: str
    from_symbol: str
    to_symbol: str
    category: UnitCategory

    @property
    def result_str(self) -> str:
        if abs(self.value) >= 1000000:
            return f"{self.value:.2e}"
        elif abs(self.value) >= 100:
            return f"{self.value:.2f}"
        elif abs(self.value) >= 1:
            return f"{self.value:.4f}"
        else:
            return f"{self.value:.6f}"

    @property
    def expression(self) -> str:
        return f"1 {self.from_symbol} = {self.result_str} {self.to_symbol}"


@dataclass
class ConversionHistory:
    """A saved conversion."""
    value: float
    from_unit: str
    to_unit: str
    result: float
    from_symbol: str = ""
    to_symbol: str = ""
    timestamp: float = field(default_factory=time.time)
    category: UnitCategory = UnitCategory.LENGTH

    @property
    def display(self) -> str:
        return f"{self.value} {self.from_symbol} = {self.result:.4g} {self.to_symbol}"

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.timestamp).strftime("%b %d")


# ─── Unit Definitions ────────────────────────────────────────────────────


def _build_units() -> Dict[UnitCategory, List[Unit]]:
    units = {}

    # Length (base: meter)
    units[UnitCategory.LENGTH] = [
        Unit("Meter", "m", 1.0, UnitCategory.LENGTH),
        Unit("Kilometer", "km", 1000.0, UnitCategory.LENGTH),
        Unit("Centimeter", "cm", 0.01, UnitCategory.LENGTH),
        Unit("Millimeter", "mm", 0.001, UnitCategory.LENGTH),
        Unit("Mile", "mi", 1609.344, UnitCategory.LENGTH),
        Unit("Yard", "yd", 0.9144, UnitCategory.LENGTH),
        Unit("Foot", "ft", 0.3048, UnitCategory.LENGTH),
        Unit("Inch", "in", 0.0254, UnitCategory.LENGTH),
    ]

    # Weight (base: kilogram)
    units[UnitCategory.WEIGHT] = [
        Unit("Kilogram", "kg", 1.0, UnitCategory.WEIGHT),
        Unit("Gram", "g", 0.001, UnitCategory.WEIGHT),
        Unit("Milligram", "mg", 0.000001, UnitCategory.WEIGHT),
        Unit("Pound", "lb", 0.453592, UnitCategory.WEIGHT),
        Unit("Ounce", "oz", 0.0283495, UnitCategory.WEIGHT),
        Unit("Metric Ton", "t", 1000.0, UnitCategory.WEIGHT),
    ]

    # Temperature (special handling)
    units[UnitCategory.TEMPERATURE] = [
        Unit("Celsius", "°C", 1.0, UnitCategory.TEMPERATURE),
        Unit("Fahrenheit", "°F", 1.0, UnitCategory.TEMPERATURE),
        Unit("Kelvin", "K", 1.0, UnitCategory.TEMPERATURE),
    ]

    # Volume (base: liter)
    units[UnitCategory.VOLUME] = [
        Unit("Liter", "L", 1.0, UnitCategory.VOLUME),
        Unit("Milliliter", "mL", 0.001, UnitCategory.VOLUME),
        Unit("Gallon (US)", "gal", 3.78541, UnitCategory.VOLUME),
        Unit("Cup (US)", "cup", 0.236588, UnitCategory.VOLUME),
        Unit("Tablespoon", "tbsp", 0.0147868, UnitCategory.VOLUME),
        Unit("Teaspoon", "tsp", 0.00492892, UnitCategory.VOLUME),
    ]

    # Speed (base: m/s)
    units[UnitCategory.SPEED] = [
        Unit("Meters/second", "m/s", 1.0, UnitCategory.SPEED),
        Unit("Kilometers/hour", "km/h", 0.277778, UnitCategory.SPEED),
        Unit("Miles/hour", "mph", 0.44704, UnitCategory.SPEED),
        Unit("Knot", "kn", 0.514444, UnitCategory.SPEED),
    ]

    # Data (base: byte)
    units[UnitCategory.DATA] = [
        Unit("Byte", "B", 1.0, UnitCategory.DATA),
        Unit("Kilobyte", "KB", 1024.0, UnitCategory.DATA),
        Unit("Megabyte", "MB", 1048576.0, UnitCategory.DATA),
        Unit("Gigabyte", "GB", 1073741824.0, UnitCategory.DATA),
        Unit("Terabyte", "TB", 1099511627776.0, UnitCategory.DATA),
        Unit("Petabyte", "PB", 1125899906842624.0, UnitCategory.DATA),
    ]

    # Time (base: second)
    units[UnitCategory.TIME] = [
        Unit("Second", "s", 1.0, UnitCategory.TIME),
        Unit("Minute", "min", 60.0, UnitCategory.TIME),
        Unit("Hour", "h", 3600.0, UnitCategory.TIME),
        Unit("Day", "d", 86400.0, UnitCategory.TIME),
        Unit("Week", "wk", 604800.0, UnitCategory.TIME),
        Unit("Month", "mo", 2629746.0, UnitCategory.TIME),
        Unit("Year", "yr", 31556952.0, UnitCategory.TIME),
    ]

    # Currency (base: USD) — approximate rates
    units[UnitCategory.CURRENCY] = [
        Unit("US Dollar", "USD", 1.0, UnitCategory.CURRENCY),
        Unit("Euro", "EUR", 1.08, UnitCategory.CURRENCY),
        Unit("British Pound", "GBP", 1.27, UnitCategory.CURRENCY),
        Unit("Japanese Yen", "JPY", 0.0067, UnitCategory.CURRENCY),
        Unit("Canadian Dollar", "CAD", 0.74, UnitCategory.CURRENCY),
        Unit("Australian Dollar", "AUD", 0.65, UnitCategory.CURRENCY),
        Unit("Swiss Franc", "CHF", 1.13, UnitCategory.CURRENCY),
        Unit("Chinese Yuan", "CNY", 0.14, UnitCategory.CURRENCY),
    ]

    return units


# ─── Unit Converter ──────────────────────────────────────────────────────


class UnitConverter:
    """
    Unit converter for Nyrqis OS.

    Converts between measurement units across multiple categories.
    """

    def __init__(self):
        self._units = _build_units()
        self._categories = list(UnitCategory)
        self._current_category: UnitCategory = UnitCategory.LENGTH
        self._from_unit_index: int = 0
        self._to_unit_index: int = 1
        self._input_value: str = "1"
        self._result: Optional[ConversionResult] = None
        self._history: List[ConversionHistory] = []
        self._selected_index: int = 0
        self._view_mode: str = "converter"  # converter, history, categories

        # Quick reference
        self._quick_refs: Dict[UnitCategory, List[str]] = {
            UnitCategory.LENGTH: [
                "1 mile = 1.609 km",
                "1 foot = 30.48 cm",
                "1 inch = 2.54 cm",
                "1 meter = 3.281 feet",
            ],
            UnitCategory.WEIGHT: [
                "1 kg = 2.205 lb",
                "1 lb = 453.6 g",
                "1 oz = 28.35 g",
                "1 ton = 1000 kg",
            ],
            UnitCategory.TEMPERATURE: [
                "°F = °C × 9/5 + 32",
                "°C = (°F - 32) × 5/9",
                "K = °C + 273.15",
            ],
            UnitCategory.CURRENCY: [
                "Rates approximate",
                "1 USD ≈ 1.08 EUR",
                "1 USD ≈ 0.80 GBP",
                "1 USD ≈ 149 JPY",
            ],
        }

    # ── Conversion ────────────────────────────────────────────────────

    def convert(self) -> Optional[ConversionResult]:
        """Perform the current conversion."""
        try:
            value = float(self._input_value)
        except ValueError:
            return None

        units = self._units.get(self._current_category, [])
        if not units or self._from_unit_index >= len(units) or self._to_unit_index >= len(units):
            return None

        from_unit = units[self._from_unit_index]
        to_unit = units[self._to_unit_index]

        # Special handling for temperature
        if self._current_category == UnitCategory.TEMPERATURE:
            result = self._convert_temperature(value, from_unit.name, to_unit.name)
        else:
            # Standard factor-based conversion
            base_value = value * from_unit.factor
            result = base_value / to_unit.factor

        self._result = ConversionResult(
            value=result,
            from_unit=from_unit.name,
            to_unit=to_unit.name,
            from_symbol=from_unit.symbol,
            to_symbol=to_unit.symbol,
            category=self._current_category,
        )

        # Add to history
        self._history.insert(0, ConversionHistory(
            value=value,
            from_unit=from_unit.name,
            to_unit=to_unit.name,
            result=result,
            from_symbol=from_unit.symbol,
            to_symbol=to_unit.symbol,
            category=self._current_category,
        ))

        return self._result

    def _convert_temperature(self, value: float, from_name: str, to_name: str) -> float:
        """Convert temperature with special formulas."""
        # Convert to Celsius first
        if from_name == "Celsius":
            celsius = value
        elif from_name == "Fahrenheit":
            celsius = (value - 32) * 5 / 9
        elif from_name == "Kelvin":
            celsius = value - 273.15
        else:
            celsius = value

        # Convert from Celsius to target
        if to_name == "Celsius":
            return celsius
        elif to_name == "Fahrenheit":
            return celsius * 9 / 5 + 32
        elif to_name == "Kelvin":
            return celsius + 273.15
        return celsius

    def set_value(self, value: str) -> None:
        self._input_value = value

    def set_from_unit(self, index: int) -> None:
        units = self._units.get(self._current_category, [])
        if 0 <= index < len(units):
            self._from_unit_index = index

    def set_to_unit(self, index: int) -> None:
        units = self._units.get(self._current_category, [])
        if 0 <= index < len(units):
            self._to_unit_index = index

    def swap_units(self) -> None:
        self._from_unit_index, self._to_unit_index = self._to_unit_index, self._from_unit_index

    # ── Category Navigation ───────────────────────────────────────────

    def set_category(self, category: UnitCategory) -> None:
        self._current_category = category
        self._from_unit_index = 0
        self._to_unit_index = min(1, len(self._units.get(category, [])) - 1)

    def cycle_category(self) -> UnitCategory:
        idx = self._categories.index(self._current_category)
        self._current_category = self._categories[(idx + 1) % len(self._categories)]
        self._from_unit_index = 0
        self._to_unit_index = 1
        return self._current_category

    @property
    def current_category(self) -> UnitCategory:
        return self._current_category

    @property
    def current_units(self) -> List[Unit]:
        return self._units.get(self._current_category, [])

    @property
    def result(self) -> Optional[ConversionResult]:
        return self._result

    @property
    def history(self) -> List[ConversionHistory]:
        return list(self._history[:50])

    @property
    def quick_refs(self) -> List[str]:
        return self._quick_refs.get(self._current_category, [])

    # ── Rendering ─────────────────────────────────────────────────────

    def render_converter(self, width: int = 60) -> List[str]:
        lines = []
        icon = CATEGORY_ICONS.get(self._current_category, "📏")
        lines.append(f" {icon} {self._current_category.value} Converter")
        lines.append("─" * width)

        units = self.current_units

        # From unit
        if units:
            from_unit = units[self._from_unit_index]
            lines.append(f" From: {from_unit.display}")

        # Input value
        lines.append(f" Value: {self._input_value}")

        # To unit
        if units:
            to_unit = units[self._to_unit_index]
            lines.append(f" To:   {to_unit.display}")

        lines.append("─" * width)

        # Result
        if self._result:
            lines.append(f" = {self._result.result_str} {self._result.to_symbol}")
            lines.append("")
            lines.append(f" {self._result.expression}")
        else:
            lines.append(" Press Enter to convert")

        lines.append("─" * width)

        # Quick reference
        refs = self.quick_refs
        if refs:
            lines.append(" Quick Reference:")
            for ref in refs:
                lines.append(f"  {ref}")

        lines.append("─" * width)
        lines.append(" Enter:Convert  Tab:Category  S:Swap  H:History  ↑↓:From unit")
        return lines

    def render_history(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 📜 Conversion History")
        lines.append(f" {len(self._history)} conversions")
        lines.append("─" * width)

        if not self._history:
            lines.append("  No conversions yet.")
        else:
            for i, entry in enumerate(self._history[:20]):
                marker = "▸" if i == self._selected_index else " "
                icon = CATEGORY_ICONS.get(entry.category, "📏")
                lines.append(f"{marker} {icon} {entry.display}")
                lines.append(f"   {entry.time_ago}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Re-convert  Esc:Back")
        return lines

    def render_categories(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 📏 Unit Categories")
        lines.append("─" * width)

        for i, cat in enumerate(self._categories):
            marker = "▸" if cat == self._current_category else " "
            icon = CATEGORY_ICONS.get(cat, "📏")
            unit_count = len(self._units.get(cat, []))
            lines.append(f" {marker} {icon} {cat.value} ({unit_count} units)")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Choose  Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        if self._view_mode == "history":
            return self.render_history(width)
        elif self._view_mode == "categories":
            return self.render_categories(width)
        return self.render_converter(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "history":
            return self._handle_history_key(key)
        elif self._view_mode == "categories":
            return self._handle_categories_key(key)
        return self._handle_converter_key(key)

    def _handle_converter_key(self, key: str) -> Optional[str]:
        if key == "Enter":
            self.convert()
            return "convert"
        elif key == "Tab":
            self._view_mode = "categories"
            return "categories"
        elif key == "s":
            self.swap_units()
            return "swap"
        elif key == "h":
            self._view_mode = "history"
            return "history"
        elif key == "ArrowUp":
            units = self.current_units
            self._from_unit_index = (self._from_unit_index - 1) % len(units) if units else 0
            return "prev_from"
        elif key == "ArrowDown":
            units = self.current_units
            self._from_unit_index = (self._from_unit_index + 1) % len(units) if units else 0
            return "next_from"
        elif key == "ArrowLeft":
            units = self.current_units
            self._to_unit_index = (self._to_unit_index - 1) % len(units) if units else 0
            return "prev_to"
        elif key == "ArrowRight":
            units = self.current_units
            self._to_unit_index = (self._to_unit_index + 1) % len(units) if units else 0
            return "next_to"
        return None

    def _handle_history_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "converter"
            return "back"
        elif key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(self._history) - 1, self._selected_index + 1)
            return "select_down"
        return None

    def _handle_categories_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "converter"
            return "back"
        elif key == "Enter":
            if 0 <= self._selected_index < len(self._categories):
                self.set_category(self._categories[self._selected_index])
            self._view_mode = "converter"
            return "select_category"
        elif key == "ArrowUp":
            self._selected_index = max(0, self._selected_index - 1)
            return "select_up"
        elif key == "ArrowDown":
            self._selected_index = min(len(self._categories) - 1, self._selected_index + 1)
            return "select_down"
        return None
