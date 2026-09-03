"""
Tests for Unit Converter, Stopwatch, and Virtual Keyboard.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.unit_converter import (
    UnitConverter, Unit, ConversionResult, ConversionHistory, UnitCategory
)
from ui.stopwatch import (
    Stopwatch, ActiveTimer, Lap, TimerPreset, IntervalConfig,
    TimerMode, TimerStatus
)
from ui.virtual_keyboard import (
    VirtualKeyboard, Key, KeyPress, KeyboardLayout, KeyboardMode, LAYOUTS
)


# ─── Unit Converter Tests ────────────────────────────────────────────────


class TestUnitConverter(unittest.TestCase):

    def setUp(self):
        self.uc = UnitConverter()

    def test_initial_state(self):
        self.assertEqual(self.uc.current_category, UnitCategory.LENGTH)

    def test_convert_length(self):
        self.uc.set_value("1")
        self.uc.set_from_unit(0)  # Meter
        self.uc.set_to_unit(1)    # Kilometer
        result = self.uc.convert()
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.value, 0.001, places=4)

    def test_convert_weight(self):
        self.uc.set_category(UnitCategory.WEIGHT)
        self.uc.set_value("1")
        self.uc.set_from_unit(0)  # Kilogram
        self.uc.set_to_unit(3)    # Pound
        result = self.uc.convert()
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.value, 2.205, places=1)

    def test_convert_temperature(self):
        self.uc.set_category(UnitCategory.TEMPERATURE)
        self.uc.set_value("100")
        self.uc.set_from_unit(0)  # Celsius
        self.uc.set_to_unit(1)    # Fahrenheit
        result = self.uc.convert()
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.value, 212.0, places=1)

    def test_convert_temperature_kelvin(self):
        self.uc.set_category(UnitCategory.TEMPERATURE)
        self.uc.set_value("0")
        self.uc.set_from_unit(0)  # Celsius
        self.uc.set_to_unit(2)    # Kelvin
        result = self.uc.convert()
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.value, 273.15, places=1)

    def test_swap_units(self):
        self.uc.set_value("1")
        self.uc.set_from_unit(0)
        self.uc.set_to_unit(1)
        self.uc.swap_units()
        result = self.uc.convert()
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.value, 1000.0, places=1)

    def test_set_category(self):
        self.uc.set_category(UnitCategory.DATA)
        self.assertEqual(self.uc.current_category, UnitCategory.DATA)

    def test_cycle_category(self):
        cat = self.uc.cycle_category()
        self.assertEqual(cat, UnitCategory.WEIGHT)

    def test_history(self):
        self.uc.set_value("1")
        self.uc.convert()
        self.assertGreater(len(self.uc.history), 0)

    def test_quick_refs(self):
        refs = self.uc.quick_refs
        self.assertIsInstance(refs, list)
        self.assertGreater(len(refs), 0)

    def test_render_converter(self):
        lines = self.uc.render_converter()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_history(self):
        lines = self.uc.render_history()
        self.assertIsInstance(lines, list)

    def test_render_categories(self):
        lines = self.uc.render_categories()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.uc.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_convert(self):
        self.uc.set_value("1")
        self.uc.handle_key("Enter")
        self.assertIsNotNone(self.uc.result)

    def test_handle_key_categories(self):
        self.uc.handle_key("Tab")
        self.assertEqual(self.uc._view_mode, "categories")


class TestConversionResult(unittest.TestCase):

    def test_result_str(self):
        r = ConversionResult(value=1234.5678, from_unit="m", to_unit="km",
                             from_symbol="m", to_symbol="km", category=UnitCategory.LENGTH)
        self.assertIn("1234.57", r.result_str)

    def test_expression(self):
        r = ConversionResult(value=0.001, from_unit="Meter", to_unit="Kilometer",
                             from_symbol="m", to_symbol="km", category=UnitCategory.LENGTH)
        self.assertIn("1 m", r.expression)
        self.assertIn("km", r.expression)


class TestUnit(unittest.TestCase):

    def test_display(self):
        u = Unit("Meter", "m", 1.0)
        self.assertEqual(u.display, "Meter (m)")


# ─── Stopwatch Tests ─────────────────────────────────────────────────────


class TestStopwatch(unittest.TestCase):

    def setUp(self):
        self.sw = Stopwatch()

    def test_initial_state(self):
        self.assertIsNotNone(self.sw.active_timer)
        self.assertEqual(self.sw.active_timer.status, TimerStatus.IDLE)

    def test_start_stop(self):
        self.sw.start()
        self.assertTrue(self.sw.active_timer.status == TimerStatus.RUNNING)
        self.sw.stop()
        self.assertEqual(self.sw.active_timer.status, TimerStatus.PAUSED)

    def test_toggle(self):
        self.sw.toggle()
        self.assertEqual(self.sw.active_timer.status, TimerStatus.RUNNING)
        self.sw.toggle()
        self.assertEqual(self.sw.active_timer.status, TimerStatus.PAUSED)

    def test_reset(self):
        self.sw.start()
        time.sleep(0.01)
        self.sw.reset()
        self.assertEqual(self.sw.active_timer.status, TimerStatus.IDLE)
        self.assertEqual(self.sw.active_timer.elapsed, 0)

    def test_record_lap(self):
        self.sw.start()
        time.sleep(0.01)
        lap = self.sw.record_lap()
        self.assertIsNotNone(lap)
        self.assertEqual(lap.lap_number, 1)

    def test_multiple_laps(self):
        self.sw.start()
        time.sleep(0.01)
        self.sw.record_lap()
        time.sleep(0.01)
        lap = self.sw.record_lap()
        self.assertEqual(lap.lap_number, 2)

    def test_presets(self):
        presets = self.sw.presets
        self.assertGreater(len(presets), 0)

    def test_start_preset(self):
        timer = self.sw.start_preset(0)
        self.assertIsNotNone(timer)
        self.assertEqual(timer.mode, TimerMode.COUNTDOWN)
        self.assertEqual(timer.status, TimerStatus.RUNNING)

    def test_intervals(self):
        intervals = self.sw.intervals
        self.assertGreater(len(intervals), 0)

    def test_start_interval(self):
        timer = self.sw.start_interval(0)
        self.assertIsNotNone(timer)
        self.assertEqual(timer.mode, TimerMode.INTERVAL)

    def test_add_custom_timer(self):
        initial = len(self.sw.timers)
        timer = self.sw.add_custom_timer(60, "Custom")
        self.assertEqual(len(self.sw.timers), initial + 1)

    def test_remove_timer(self):
        self.sw.add_custom_timer(60)
        result = self.sw.remove_timer(len(self.sw.timers) - 1)
        self.assertTrue(result)

    def test_update_timers(self):
        self.sw.start()
        self.sw.update_timers()

    def test_render_main(self):
        lines = self.sw.render_main()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_presets(self):
        lines = self.sw.render_presets()
        self.assertIsInstance(lines, list)

    def test_render_intervals(self):
        lines = self.sw.render_intervals()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.sw.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_toggle(self):
        self.sw.handle_key(" ")
        self.assertEqual(self.sw.active_timer.status, TimerStatus.RUNNING)

    def test_handle_key_lap(self):
        self.sw.start()
        self.sw.handle_key("l")
        self.assertGreater(len(self.sw.active_timer.laps), 0)

    def test_handle_key_presets(self):
        self.sw.handle_key("p")
        self.assertEqual(self.sw._view_mode, "presets")


class TestActiveTimer(unittest.TestCase):

    def test_display_time(self):
        t = ActiveTimer(timer_id="t1", name="Test", elapsed=65.5)
        self.assertIn("01:05", t.display_time)

    def test_status_icon(self):
        t = ActiveTimer(timer_id="t1", name="Test", status=TimerStatus.RUNNING)
        self.assertEqual(t.status_icon, "▶️")


class TestLap(unittest.TestCase):

    def test_split_str(self):
        lap = Lap(lap_number=1, split_time=65.5, lap_time=65.5)
        self.assertIn("01:05", lap.split_str)

    def test_lap_str(self):
        lap = Lap(lap_number=1, split_time=10.5, lap_time=10.5)
        self.assertIn("00:10", lap.lap_str)


class TestTimerPreset(unittest.TestCase):

    def test_display(self):
        p = TimerPreset("5 Min", 300)
        self.assertIn("5m", p.display)


class TestIntervalConfig(unittest.TestCase):

    def test_total_time(self):
        c = IntervalConfig(30, 10, 8)
        self.assertEqual(c.total_time, 320)


# ─── Virtual Keyboard Tests ──────────────────────────────────────────────


class TestVirtualKeyboard(unittest.TestCase):

    def setUp(self):
        self.kb = VirtualKeyboard()

    def test_initial_state(self):
        self.assertEqual(self.kb.layout, KeyboardLayout.QWERTY)
        self.assertEqual(self.kb.mode, KeyboardMode.LETTERS)
        self.assertEqual(self.kb.input_text, "")

    def test_press_key(self):
        key = Key("A")
        result = self.kb.press_key(key)
        self.assertEqual(result, "A")
        self.assertEqual(self.kb.input_text, "A")

    def test_press_multiple_keys(self):
        for letter in "Hello":
            key = Key(letter)
            self.kb.press_key(key)
        self.assertEqual(self.kb.input_text, "Hello")

    def test_backspace(self):
        self.kb.press_key(Key("A"))
        self.kb.press_key(Key("B"))
        self.kb.press_key(Key("⌫", is_function=True))
        self.assertEqual(self.kb.input_text, "A")

    def test_space(self):
        self.kb.press_key(Key(" ", is_function=True))
        self.assertEqual(self.kb.input_text, " ")

    def test_enter(self):
        self.kb.press_key(Key("⏎", is_function=True))
        self.assertEqual(self.kb.input_text, "\n")

    def test_shift(self):
        shift = Key("⇧", is_modifier=True)
        self.kb.press_key(shift)
        self.assertTrue(self.kb.shift_active)
        self.kb.press_key(Key("a"))
        self.assertEqual(self.kb.input_text, "A")
        self.assertFalse(self.kb.shift_active)

    def test_caps_lock(self):
        self.kb.toggle_caps_lock()
        self.assertTrue(self.kb.caps_lock)
        self.kb.press_key(Key("a"))
        self.assertEqual(self.kb.input_text, "A")

    def test_set_layout(self):
        self.kb.set_layout(KeyboardLayout.DVORAK)
        self.assertEqual(self.kb.layout, KeyboardLayout.DVORAK)

    def test_cycle_layout(self):
        layout = self.kb.cycle_layout()
        self.assertNotEqual(layout, KeyboardLayout.QWERTY)

    def test_set_mode(self):
        self.kb.set_mode(KeyboardMode.NUMBERS)
        self.assertEqual(self.kb.mode, KeyboardMode.NUMBERS)

    def test_cycle_mode(self):
        mode = self.kb.cycle_mode()
        self.assertEqual(mode, KeyboardMode.NUMBERS)

    def test_mode_switch_key(self):
        self.kb.press_key(Key("123", is_function=True))
        self.assertEqual(self.kb.mode, KeyboardMode.NUMBERS)

    def test_clear_input(self):
        self.kb.press_key(Key("A"))
        self.kb.clear_input()
        self.assertEqual(self.kb.input_text, "")

    def test_set_input_text(self):
        self.kb.set_input_text("Hello World")
        self.assertEqual(self.kb.input_text, "Hello World")
        self.assertEqual(self.kb._cursor_pos, 11)

    def test_move_cursor(self):
        self.kb.set_input_text("Hello")
        self.kb.move_cursor(-2)
        self.assertEqual(self.kb._cursor_pos, 3)

    def test_sticky_keys(self):
        result = self.kb.toggle_sticky_keys()
        self.assertTrue(result)

    def test_high_contrast(self):
        result = self.kb.toggle_high_contrast()
        self.assertTrue(result)

    def test_magnifier(self):
        result = self.kb.toggle_magnifier()
        self.assertTrue(result)

    def test_emoji(self):
        self.kb.set_mode(KeyboardMode.EMOJI)
        emoji_key = Key("😀", emoji="😀")
        self.kb.press_key(emoji_key)
        self.assertIn("😀", self.kb.input_text)

    def test_recent_emojis(self):
        self.kb.set_mode(KeyboardMode.EMOJI)
        self.kb.press_key(Key("😀", emoji="😀"))
        self.assertIn("😀", self.kb.recent_emojis)

    def test_current_keys(self):
        keys = self.kb.current_keys
        self.assertGreater(len(keys), 0)

    def test_predictions(self):
        self.kb.set_input_text("th")
        self.kb.move_cursor(0)
        self.kb._update_predictions()
        preds = self.kb.predictions
        self.assertIsInstance(preds, list)

    def test_render(self):
        lines = self.kb.render()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_key_history(self):
        self.kb.press_key(Key("A"))
        self.assertGreater(len(self.kb._key_history), 0)


class TestKey(unittest.TestCase):

    def test_display(self):
        k = Key("A")
        self.assertEqual(k.display, "A")

    def test_width(self):
        k = Key("A", width=2)
        self.assertEqual(k.physical_width, 2)


class TestKeyPress(unittest.TestCase):

    def test_key_press(self):
        kp = KeyPress(key="A")
        self.assertEqual(kp.key, "A")
        self.assertGreater(kp.timestamp, 0)


if __name__ == "__main__":
    unittest.main()
