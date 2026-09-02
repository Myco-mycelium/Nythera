"""test_taskbar — Tests for the taskbar component.

References:
    - ui/taskbar.py
    - shell/defaults/default-shell.nstudio
"""

from __future__ import annotations

import os
import sys
import time
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    os.sys.path.insert(0, _HERE)


class TestTaskbar(unittest.TestCase):
    """Tests for the taskbar component."""

    def test_taskbar_create(self):
        """Can create a taskbar."""
        from ui.taskbar import Taskbar
        taskbar = Taskbar(1920, 48)
        self.assertEqual(taskbar.width, 1920)
        self.assertEqual(taskbar.height, 48)
    
    def test_taskbar_add_app(self):
        """Can add apps to the taskbar."""
        from ui.taskbar import Taskbar, COLOR_ICON_TERMINAL
        taskbar = Taskbar(1920, 48)
        
        taskbar.add_app("terminal", "Terminal", COLOR_ICON_TERMINAL)
        self.assertEqual(len(taskbar._items), 1)
    
    def test_taskbar_remove_app(self):
        """Can remove apps from the taskbar."""
        from ui.taskbar import Taskbar, COLOR_ICON_TERMINAL
        taskbar = Taskbar(1920, 48)
        
        taskbar.add_app("terminal", "Terminal", COLOR_ICON_TERMINAL)
        taskbar.remove_app("terminal")
        self.assertEqual(len(taskbar._items), 0)
    
    def test_taskbar_set_active(self):
        """Can set app as active."""
        from ui.taskbar import Taskbar, COLOR_ICON_TERMINAL
        taskbar = Taskbar(1920, 48)
        
        taskbar.add_app("terminal", "Terminal", COLOR_ICON_TERMINAL)
        taskbar.set_active("terminal", True)
        self.assertTrue(taskbar._items[0].active)
    
    def test_taskbar_render(self):
        """Can render the taskbar."""
        from ui.taskbar import Taskbar
        taskbar = Taskbar(1920, 48)
        
        pixels = taskbar.render(1032)
        self.assertGreater(len(pixels), 0)
        
        # Check that pixels are in the correct y range
        ys = [p[1] for p in pixels]
        self.assertGreaterEqual(min(ys), 1032)
        self.assertLessEqual(max(ys), 1032 + 48)
    
    def test_taskbar_height(self):
        """Taskbar returns correct height."""
        from ui.taskbar import Taskbar
        taskbar = Taskbar(1920, 48)
        self.assertEqual(taskbar.get_height(), 48)
    
    def test_toggle_start_menu(self):
        """Can toggle the start menu."""
        from ui.taskbar import Taskbar
        taskbar = Taskbar(1920, 48)
        
        self.assertFalse(taskbar._start_menu_open)
        taskbar.toggle_start_menu()
        self.assertTrue(taskbar._start_menu_open)
        taskbar.toggle_start_menu()
        self.assertFalse(taskbar._start_menu_open)
    
    def test_handle_click_start_button(self):
        """Clicking start button toggles menu."""
        from ui.taskbar import Taskbar
        taskbar = Taskbar(1920, 48)
        
        # Start button is at (8, 8) relative to taskbar, y=20 is within button
        result = taskbar.handle_click(24, 20)
        self.assertEqual(result, "start")
        self.assertTrue(taskbar._start_menu_open)
    
    def test_handle_click_app(self):
        """Clicking app indicator returns app ID."""
        from ui.taskbar import Taskbar, COLOR_ICON_TERMINAL
        taskbar = Taskbar(1920, 48)
        
        taskbar.add_app("terminal", "Terminal", COLOR_ICON_TERMINAL)
        # First app indicator is at (56, 12) relative to taskbar, y=20 is within button
        result = taskbar.handle_click(74, 20)
        self.assertEqual(result, "terminal")
    
    def test_quick_launch(self):
        """Quick launch items are set up by default."""
        from ui.taskbar import Taskbar
        taskbar = Taskbar(1920, 48)
        self.assertEqual(len(taskbar._quick_launch), 3)
    
    def test_multiple_apps(self):
        """Can add multiple apps."""
        from ui.taskbar import Taskbar, COLOR_ICON_TERMINAL, COLOR_ICON_FOLDER, COLOR_ICON_BROWSER
        taskbar = Taskbar(1920, 48)
        
        taskbar.add_app("terminal", "Terminal", COLOR_ICON_TERMINAL)
        taskbar.add_app("files", "Files", COLOR_ICON_FOLDER)
        taskbar.add_app("browser", "Browser", COLOR_ICON_BROWSER)
        
        self.assertEqual(len(taskbar._items), 3)


class TestTaskbarIntegration(unittest.TestCase):
    """Integration tests for the taskbar with desktop."""

    def test_taskbar_with_desktop(self):
        """Taskbar works with the desktop renderer."""
        from ui.taskbar import Taskbar, COLOR_ICON_TERMINAL, COLOR_ICON_FOLDER
        
        taskbar = Taskbar(1920, 48)
        taskbar.add_app("terminal", "Terminal", COLOR_ICON_TERMINAL)
        taskbar.add_app("files", "Files", COLOR_ICON_FOLDER)
        
        # Render at the bottom of a 1080p screen
        pixels = taskbar.render(1032)
        
        # Verify pixel count (width * height + borders)
        self.assertGreater(len(pixels), 1920 * 48)
    
    def test_clock_updates(self):
        """Clock shows current time."""
        from ui.taskbar import Taskbar
        taskbar = Taskbar(1920, 48)
        
        pixels = taskbar.render(1032)
        # Clock area should have non-zero pixels
        clock_pixels = [p for p in pixels if 1720 <= p[0] <= 1780]
        self.assertGreater(len(clock_pixels), 0)


if __name__ == "__main__":
    unittest.main()
