#!/usr/bin/env python3
"""Tests for ui.window_manager, ui.screen_capture, and ui.launcher."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.window_manager import (
    WindowManager, ManagedWindow, WindowState, DragMode, SnapZone,
)

from ui.screen_capture import (
    ScreenCapture, CaptureMode, RecordingState, CaptureRegion, Frame,
)

from ui.launcher import (
    Launcher, AppEntry, DEFAULT_APPS,
)


# ---------------------------------------------------------------------------
# Window Manager Tests
# ---------------------------------------------------------------------------

class TestManagedWindow(unittest.TestCase):
    def test_creation(self):
        w = ManagedWindow(id="w1", title="Test")
        self.assertEqual(w.id, "w1")
        self.assertEqual(w.state, WindowState.NORMAL)
        self.assertTrue(w.visible)

    def test_save_restore(self):
        w = ManagedWindow(id="w1", x=100, y=200, width=800, height=600)
        w.save_state()
        w.x = 50
        w.restore_state()
        self.assertEqual(w.x, 100)
        self.assertEqual(w.width, 800)


class TestWindowManager(unittest.TestCase):
    def setUp(self):
        self.wm = WindowManager(1920, 1080, 48)

    def test_creation(self):
        self.assertIsNotNone(self.wm)
        self.assertEqual(self.wm.active_workspace, 0)

    def test_add_window(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.assertEqual(len(self.wm.windows), 1)

    def test_remove_window(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.assertTrue(self.wm.remove_window("w1"))
        self.assertEqual(len(self.wm.windows), 0)

    def test_focus(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.wm.add_window(ManagedWindow(id="w2"))
        self.wm.focus_next()
        self.assertIsNotNone(self.wm.focused_window)

    def test_minimize(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.assertTrue(self.wm.minimize("w1"))
        win = self.wm.find_window("w1")
        self.assertEqual(win.state, WindowState.MINIMIZED)
        self.assertFalse(win.visible)

    def test_maximize(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.assertTrue(self.wm.maximize("w1"))
        win = self.wm.find_window("w1")
        self.assertEqual(win.state, WindowState.MAXIMIZED)
        self.assertEqual(win.width, 1920)

    def test_restore(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.wm.maximize("w1")
        self.assertTrue(self.wm.restore("w1"))
        win = self.wm.find_window("w1")
        self.assertEqual(win.state, WindowState.NORMAL)

    def test_workspace_switch(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.assertTrue(self.wm.switch_workspace(1))
        self.assertEqual(self.wm.active_workspace, 1)
        self.assertFalse(self.wm.find_window("w1").visible)

    def test_move_to_workspace(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.assertTrue(self.wm.move_to_workspace("w1", 2))
        self.assertEqual(self.wm.find_window("w1").workspace, 2)

    def test_start_move(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.assertTrue(self.wm.start_move("w1", 500, 300))

    def test_start_resize(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.assertTrue(self.wm.start_resize("w1", 500, 500, "bottom_right"))

    def test_hit_test(self):
        self.wm.add_window(ManagedWindow(id="w1", x=100, y=100, width=400, height=300))
        result = self.wm.hit_test(200, 200)
        self.assertEqual(result, "w1")

    def test_hit_test_miss(self):
        self.wm.add_window(ManagedWindow(id="w1", x=100, y=100, width=400, height=300))
        result = self.wm.hit_test(900, 900)
        self.assertIsNone(result)

    def test_detect_resize_edge(self):
        self.wm.add_window(ManagedWindow(id="w1", x=100, y=100, width=400, height=300))
        edge = self.wm.detect_resize_edge("w1", 102, 102)
        self.assertEqual(edge, "top_left")

    def test_raise_lower(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.wm.add_window(ManagedWindow(id="w2"))
        self.wm.raise_window("w1")
        self.assertEqual(self.wm.windows[-1].id, "w1")
        self.wm.lower_window("w1")
        self.assertEqual(self.wm.windows[0].id, "w1")

    def test_snap_zones(self):
        zone = self.wm._detect_snap_zone(5, 5)
        self.assertEqual(zone, SnapZone.TOP_LEFT)
        zone = self.wm._detect_snap_zone(1900, 5)
        self.assertEqual(zone, SnapZone.TOP_RIGHT)

    def test_key_handlers(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.wm.handle_key("Tab", {"alt": True})
        self.wm.handle_key("w", {"ctrl": True})

    def test_workspace_windows(self):
        self.wm.add_window(ManagedWindow(id="w1"))
        self.wm.add_window(ManagedWindow(id="w2"))
        self.assertEqual(len(self.wm.workspace_windows), 2)


# ---------------------------------------------------------------------------
# Screen Capture Tests
# ---------------------------------------------------------------------------

class TestCaptureRegion(unittest.TestCase):
    def test_creation(self):
        r = CaptureRegion(0, 0, 1920, 1080)
        self.assertEqual(r.area, 1920 * 1080)

    def test_contains(self):
        r = CaptureRegion(100, 100, 400, 300)
        self.assertTrue(r.contains(200, 200))
        self.assertFalse(r.contains(50, 50))


class TestFrame(unittest.TestCase):
    def test_creation(self):
        f = Frame(frame_id=1, timestamp=time.time(), width=100, height=100, rgb_data=b"\x00" * 30000)
        self.assertEqual(f.size, 30000)

    def test_save_png(self):
        f = Frame(frame_id=1, timestamp=time.time(), width=4, height=4, rgb_data=b"\x80" * 48)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            f.save_png(tmp.name)
            self.assertTrue(os.path.exists(tmp.name))
            self.assertGreater(os.path.getsize(tmp.name), 0)
            os.unlink(tmp.name)


class TestScreenCapture(unittest.TestCase):
    def setUp(self):
        self.sc = ScreenCapture(1920, 1080)

    def test_creation(self):
        self.assertIsNotNone(self.sc)
        self.assertEqual(self.sc.state, RecordingState.IDLE)

    def test_capture_full(self):
        pixels = [(24, 24, 32)] * (1920 * 1080)
        frame = self.sc.capture_full(pixels, 1920, 1080)
        self.assertEqual(frame.frame_id, 1)
        self.assertEqual(frame.width, 1920)

    def test_capture_region(self):
        pixels = [(24, 24, 32)] * (1920 * 1080)
        region = CaptureRegion(100, 100, 200, 200)
        frame = self.sc.capture_region(pixels, 1920, region)
        self.assertEqual(frame.width, 200)

    def test_recording(self):
        self.sc.start_recording(100)
        self.assertTrue(self.sc.is_recording)
        self.sc.stop_recording()
        self.assertFalse(self.sc.is_recording)

    def test_pause_resume(self):
        self.sc.start_recording()
        self.sc.pause_recording()
        self.assertEqual(self.sc.state, RecordingState.PAUSED)
        self.sc.resume_recording()
        self.assertTrue(self.sc.is_recording)

    def test_timed_capture(self):
        self.sc.start_timed_capture(1000, 5)
        self.assertTrue(self.sc.should_timed_capture())
        self.sc.increment_timed_count()
        self.assertEqual(self.sc._timed_count, 1)

    def test_export(self):
        pixels = [(24, 24, 32)] * (100 * 100)
        frame = self.sc.capture_full(pixels, 100, 100)
        self.sc.add_frame(frame)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = self.sc.export_frames(tmpdir)
            self.assertEqual(len(paths), 1)
            self.assertTrue(os.path.exists(paths[0]))

    def test_set_region(self):
        self.sc.set_region(100, 100, 500, 400)
        self.assertEqual(self.sc.region.x, 100)
        self.assertEqual(self.sc.region.width, 500)

    def test_overlay(self):
        pixels = [(24, 24, 32)] * (100 * 100)
        self.sc.start_recording()
        result = self.sc.render_recording_overlay(pixels, 100, 100)
        self.assertEqual(len(result), 100 * 100)
        self.sc.stop_recording()

    def test_region_overlay(self):
        pixels = [(24, 24, 32)] * (100 * 100)
        self.sc._mode = CaptureMode.REGION
        result = self.sc.render_region_overlay(pixels, 100, 100)
        self.assertEqual(len(result), 100 * 100)


# ---------------------------------------------------------------------------
# Launcher Tests
# ---------------------------------------------------------------------------

class TestAppEntry(unittest.TestCase):
    def test_creation(self):
        app = AppEntry(id="test", name="Test App")
        self.assertEqual(app.id, "test")
        self.assertFalse(app.favorite)

    def test_search_text(self):
        app = AppEntry(id="test", name="Terminal", category="System")
        self.assertIn("terminal", app.search_text)
        self.assertIn("system", app.search_text)


class TestLauncher(unittest.TestCase):
    def setUp(self):
        self.launcher = Launcher(500, 700)

    def test_creation(self):
        self.assertIsNotNone(self.launcher)
        self.assertGreater(len(self.launcher.apps), 0)

    def test_show_hide(self):
        self.launcher.show()
        self.assertTrue(self.launcher.is_visible)
        self.launcher.hide()
        self.assertFalse(self.launcher.is_visible)

    def test_toggle(self):
        self.launcher.toggle()
        self.assertTrue(self.launcher.is_visible)
        self.launcher.toggle()
        self.assertFalse(self.launcher.is_visible)

    def test_search(self):
        self.launcher.show()
        self.launcher.set_search("terminal")
        self.assertGreater(len(self.launcher.filtered_apps), 0)
        self.assertEqual(self.launcher.filtered_apps[0].name, "Terminal")

    def test_search_empty(self):
        self.launcher.show()
        self.launcher.set_search("")
        self.assertEqual(len(self.launcher.filtered_apps), len(self.launcher.apps))

    def test_navigate(self):
        self.launcher.show()
        self.launcher.move_down()
        self.assertEqual(self.launcher._selected_index, 1)
        self.launcher.move_up()
        self.assertEqual(self.launcher._selected_index, 0)

    def test_select_app(self):
        self.launcher.show()
        app = self.launcher.select()
        self.assertIsNotNone(app)

    def test_toggle_favorite(self):
        # Add a fresh app to avoid shared state issues
        fresh = AppEntry(id="fresh_toggle_test", name="Fresh")
        self.launcher.add_app(fresh)
        self.assertFalse(fresh.favorite)
        self.launcher.toggle_favorite(fresh.id)
        self.assertTrue(fresh.favorite)
        self.assertIn(fresh, self.launcher.favorites)

    def test_record_launch(self):
        app = self.launcher.apps[0]
        self.launcher.record_launch(app.id)
        self.assertGreater(app.use_count, 0)
        self.assertTrue(app.recent)

    def test_add_remove_app(self):
        new_app = AppEntry(id="new", name="New App")
        self.launcher.add_app(new_app)
        self.assertEqual(len(self.launcher.apps), len(DEFAULT_APPS) + 1)
        self.launcher.remove_app("new")
        self.assertEqual(len(self.launcher.apps), len(DEFAULT_APPS))

    def test_fuzzy_search(self):
        self.launcher.show()
        self.launcher.set_search("term")
        self.assertGreater(len(self.launcher.filtered_apps), 0)
        self.assertEqual(self.launcher.filtered_apps[0].name, "Terminal")

    def test_key_handlers(self):
        self.launcher.show()
        self.launcher.handle_key("Down")
        self.launcher.handle_key("Up")
        self.launcher.handle_key("Escape")
        self.assertFalse(self.launcher.is_visible)

    def test_render(self):
        self.launcher.show()
        pixels, w, h = self.launcher.render()
        self.assertEqual(w, 500)
        self.assertEqual(h, 700)
        self.assertEqual(len(pixels), w * h)

    def test_render_rgb(self):
        self.launcher.show()
        data, w, h = self.launcher.render_to_rgb()
        self.assertEqual(len(data), w * h * 3)

    def test_favorites_priority(self):
        self.launcher.toggle_favorite("terminal")
        self.launcher.show()
        favs = self.launcher.filtered_apps[:len(self.launcher.favorites)]
        for app in favs:
            self.assertTrue(app.favorite)

    def test_recent(self):
        self.launcher.record_launch("terminal")
        self.launcher.record_launch("files")
        recent = self.launcher.recent
        self.assertGreater(len(recent), 0)


if __name__ == "__main__":
    unittest.main()
