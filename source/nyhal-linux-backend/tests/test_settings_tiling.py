#!/usr/bin/env python3
"""Tests for ui.settings_panel, ui.tiling, and demo.boot_splash."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.settings_panel import (
    SettingsPanel,
    Theme,
    Toggle,
    BUILTIN_THEMES,
    THEME_ECLIPSE,
    THEME_SOLAR,
    THEME_DRACULA,
)

from ui.tiling import (
    TilingManager,
    TileWindow,
    LayoutMode,
    _layout_horizontal,
    _layout_vertical,
    _layout_grid,
    _layout_master_stack,
    _layout_monocle,
)


# ---------------------------------------------------------------------------
# Settings Panel Tests
# ---------------------------------------------------------------------------

class TestTheme(unittest.TestCase):
    """Tests for Theme dataclass."""

    def test_theme_creation(self):
        t = Theme(name="Test")
        self.assertEqual(t.name, "Test")
        self.assertEqual(t.bg, (30, 30, 42))

    def test_builtin_themes(self):
        self.assertEqual(len(BUILTIN_THEMES), 3)
        self.assertEqual(BUILTIN_THEMES[0].name, "Eclipse")
        self.assertEqual(BUILTIN_THEMES[1].name, "Solar")
        self.assertEqual(BUILTIN_THEMES[2].name, "Dracula")


class TestToggle(unittest.TestCase):
    """Tests for Toggle dataclass."""

    def test_toggle_default(self):
        t = Toggle(label="WiFi")
        self.assertFalse(t.enabled)

    def test_toggle_enabled(self):
        t = Toggle(label="BT", enabled=True)
        self.assertTrue(t.enabled)


class TestSettingsPanel(unittest.TestCase):
    """Tests for SettingsPanel."""

    def setUp(self):
        self.panel = SettingsPanel()

    def test_creation(self):
        self.assertIsNotNone(self.panel)

    def test_volume_default(self):
        self.assertEqual(self.panel.volume, 75)

    def test_set_volume(self):
        self.panel.set_volume(50)
        self.assertEqual(self.panel.volume, 50)

    def test_volume_clamp(self):
        self.panel.set_volume(150)
        self.assertEqual(self.panel.volume, 100)
        self.panel.set_volume(-10)
        self.assertEqual(self.panel.volume, 0)

    def test_brightness_default(self):
        self.assertEqual(self.panel.brightness, 100)

    def test_set_brightness(self):
        self.panel.set_brightness(75)
        self.assertEqual(self.panel.brightness, 75)

    def test_brightness_clamp(self):
        self.panel.set_brightness(200)
        self.assertEqual(self.panel.brightness, 100)

    def test_selected_theme(self):
        self.assertEqual(self.panel.selected_theme.name, "Eclipse")

    def test_cycle_theme(self):
        theme = self.panel.cycle_theme()
        self.assertEqual(theme.name, "Solar")
        theme = self.panel.cycle_theme()
        self.assertEqual(theme.name, "Dracula")
        theme = self.panel.cycle_theme()
        self.assertEqual(theme.name, "Eclipse")

    def test_select_theme(self):
        self.panel.select_theme(1)
        self.assertEqual(self.panel.selected_theme.name, "Solar")

    def test_toggles(self):
        toggles = self.panel.toggles
        self.assertGreater(len(toggles), 0)
        self.assertEqual(toggles[0].label, "WiFi")

    def test_toggle_setting(self):
        result = self.panel.toggle_setting(0)
        self.assertFalse(result)  # Was True, now False
        self.assertFalse(self.panel.toggles[0].enabled)

    def test_key_up_volume(self):
        result = self.panel.handle_key("Up")
        self.assertEqual(result, "volume")
        self.assertEqual(self.panel.volume, 80)

    def test_key_down_volume(self):
        result = self.panel.handle_key("Down")
        self.assertEqual(result, "volume")
        self.assertEqual(self.panel.volume, 70)

    def test_key_right_brightness(self):
        result = self.panel.handle_key("Right")
        self.assertEqual(result, "brightness")
        self.assertEqual(self.panel.brightness, 100)  # Already at max

    def test_key_left_brightness(self):
        result = self.panel.handle_key("Left")
        self.assertEqual(result, "brightness")
        self.assertEqual(self.panel.brightness, 95)

    def test_key_theme_cycle(self):
        result = self.panel.handle_key("t")
        self.assertEqual(result, "theme")

    def test_render(self):
        pixels, w, h = self.panel.render()
        self.assertEqual(w, 400)
        self.assertEqual(h, 700)
        self.assertEqual(len(pixels), w * h)

    def test_render_rgb(self):
        data, w, h = self.panel.render_to_rgb()
        self.assertEqual(len(data), w * h * 3)

    def test_render_with_different_theme(self):
        self.panel.select_theme(1)  # Solar
        pixels, w, h = self.panel.render()
        self.assertEqual(len(pixels), w * h)


# ---------------------------------------------------------------------------
# Tiling Tests
# ---------------------------------------------------------------------------

class TestTileWindow(unittest.TestCase):
    """Tests for TileWindow."""

    def test_creation(self):
        w = TileWindow(id="w1")
        self.assertEqual(w.id, "w1")
        self.assertTrue(w.visible)

    def test_rect(self):
        w = TileWindow(id="w1", x=10, y=20, width=300, height=200)
        self.assertEqual(w.rect, (10, 20, 300, 200))


class TestLayoutFunctions(unittest.TestCase):
    """Tests for layout algorithms."""

    def setUp(self):
        self.windows = [
            TileWindow(id=f"w{i}", visible=True) for i in range(4)
        ]

    def test_horizontal(self):
        _layout_horizontal(self.windows, 0, 0, 1000, 500)
        # First window
        self.assertEqual(self.windows[0].x, 0)
        self.assertEqual(self.windows[0].y, 0)
        self.assertEqual(self.windows[0].height, 500)
        # Windows should be side by side
        for i in range(1, len(self.windows)):
            self.assertGreater(self.windows[i].x, self.windows[i-1].x)

    def test_vertical(self):
        _layout_vertical(self.windows, 0, 0, 1000, 500)
        # All windows same width
        for w in self.windows:
            self.assertEqual(w.width, 1000)
        # Windows stacked vertically
        for i in range(1, len(self.windows)):
            self.assertGreater(self.windows[i].y, self.windows[i-1].y)

    def test_grid(self):
        _layout_grid(self.windows, 0, 0, 1000, 500)
        # 4 windows in 2x2 grid
        self.assertEqual(self.windows[0].x, 0)
        self.assertEqual(self.windows[0].y, 0)
        self.assertEqual(self.windows[2].x, 0)
        self.assertGreater(self.windows[2].y, self.windows[0].y)

    def test_master_stack(self):
        _layout_master_stack(self.windows, 0, 0, 1000, 500)
        # Master window on left
        self.assertEqual(self.windows[0].x, 0)
        self.assertGreater(self.windows[0].width, 400)
        # Stack windows on right
        self.assertGreater(self.windows[1].x, self.windows[0].x)

    def test_monocle(self):
        _layout_monocle(self.windows, 0, 0, 1000, 500)
        # Only last window visible
        for w in self.windows[:-1]:
            self.assertFalse(w.visible)
        self.assertTrue(self.windows[-1].visible)
        self.assertEqual(self.windows[-1].width, 1000)
        self.assertEqual(self.windows[-1].height, 500)

    def test_empty_windows(self):
        _layout_horizontal([], 0, 0, 1000, 500)
        _layout_vertical([], 0, 0, 1000, 500)
        _layout_grid([], 0, 0, 1000, 500)
        _layout_master_stack([], 0, 0, 1000, 500)
        _layout_monocle([], 0, 0, 1000, 500)

    def test_single_window(self):
        windows = [TileWindow(id="w1", visible=True)]
        _layout_master_stack(windows, 0, 0, 1000, 500)
        self.assertEqual(windows[0].width, 1000)
        self.assertEqual(windows[0].height, 500)


class TestTilingManager(unittest.TestCase):
    """Tests for TilingManager."""

    def setUp(self):
        self.tm = TilingManager()

    def test_creation(self):
        self.assertIsNotNone(self.tm)
        self.assertEqual(self.tm.window_count, 0)

    def test_add_window(self):
        self.tm.add_window(TileWindow(id="w1"))
        self.assertEqual(self.tm.window_count, 1)

    def test_remove_window(self):
        self.tm.add_window(TileWindow(id="w1"))
        result = self.tm.remove_window("w1")
        self.assertTrue(result)
        self.assertEqual(self.tm.window_count, 0)

    def test_remove_nonexistent(self):
        result = self.tm.remove_window("nope")
        self.assertFalse(result)

    def test_find_window(self):
        self.tm.add_window(TileWindow(id="w1"))
        found = self.tm.find_window("w1")
        self.assertIsNotNone(found)

    def test_focus_window(self):
        self.tm.add_window(TileWindow(id="w1"))
        self.tm.add_window(TileWindow(id="w2"))
        result = self.tm.focus_window("w2")
        self.assertTrue(result)
        self.assertEqual(self.tm.focused_window.id, "w2")

    def test_focus_next(self):
        self.tm.add_window(TileWindow(id="w1"))
        self.tm.add_window(TileWindow(id="w2"))
        win = self.tm.focus_next()
        self.assertIsNotNone(win)

    def test_focus_prev(self):
        self.tm.add_window(TileWindow(id="w1"))
        self.tm.add_window(TileWindow(id="w2"))
        win = self.tm.focus_prev()
        self.assertIsNotNone(win)

    def test_set_layout(self):
        self.tm.set_layout(LayoutMode.GRID)
        self.assertEqual(self.tm.layout_mode, LayoutMode.GRID)

    def test_cycle_layout(self):
        initial = self.tm.layout_mode
        mode = self.tm.cycle_layout()
        self.assertNotEqual(mode, initial)

    def test_set_area(self):
        self.tm.add_window(TileWindow(id="w1"))
        self.tm.set_area(0, 0, 800, 600)
        self.assertEqual(self.tm.area, (0, 0, 800, 600))

    def test_set_gap(self):
        self.tm.set_gap(8)
        self.assertEqual(self.tm.gap, 8)

    def test_set_master_ratio(self):
        self.tm.set_master_ratio(0.7)
        self.assertEqual(self.tm.master_ratio, 0.7)
        self.tm.set_master_ratio(0.1)  # Should clamp
        self.assertEqual(self.tm.master_ratio, 0.3)

    def test_handle_key_layout(self):
        result = self.tm.handle_key("l", {"ctrl": True, "shift": True})
        self.assertEqual(result, "layout")

    def test_handle_key_focus(self):
        self.tm.add_window(TileWindow(id="w1"))
        result = self.tm.handle_key("j", {"ctrl": True})
        self.assertEqual(result, "focus")

    def test_handle_key_swap(self):
        self.tm.add_window(TileWindow(id="w1"))
        self.tm.add_window(TileWindow(id="w2"))
        self.tm.focus_window("w2")
        result = self.tm.handle_key("space", {"ctrl": True})
        self.assertEqual(result, "swap")
        # After swap, focused window (w2) moved to position 0, w1 moved to position 1
        # Focus is now at index 0, which has w2
        self.assertEqual(self.tm.focused_window.id, "w2")

    def test_visible_count(self):
        self.tm.add_window(TileWindow(id="w1"))
        self.tm.add_window(TileWindow(id="w2"))
        self.assertEqual(self.tm.visible_count, 2)

    def test_minimized_window(self):
        self.tm.add_window(TileWindow(id="w1"))
        self.tm.add_window(TileWindow(id="w2", minimized=True))
        self.assertEqual(self.tm.visible_count, 1)

    def test_multiple_layouts(self):
        self.tm.add_window(TileWindow(id="w1"))
        self.tm.add_window(TileWindow(id="w2"))
        
        for mode in LayoutMode:
            self.tm.set_layout(mode)
            self.assertEqual(self.tm.layout_mode, mode)
            # All windows should have valid positions
            for w in self.tm.windows:
                self.assertGreaterEqual(w.width, 0)
                self.assertGreaterEqual(w.height, 0)


# ---------------------------------------------------------------------------
# Boot Splash Tests
# ---------------------------------------------------------------------------

class TestBootSplash(unittest.TestCase):
    """Tests for boot splash rendering."""
    
    def test_import(self):
        from demo.boot_splash import BootSplashRenderer
        self.assertIsNotNone(BootSplashRenderer)

    def test_creation(self):
        from demo.boot_splash import BootSplashRenderer
        renderer = BootSplashRenderer(1920, 1080, 30)
        self.assertEqual(renderer.width, 1920)
        self.assertEqual(renderer.height, 1080)


if __name__ == "__main__":
    unittest.main()
