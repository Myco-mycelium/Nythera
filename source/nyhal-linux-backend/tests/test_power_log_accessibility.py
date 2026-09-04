import unittest
import time


class TestPowerManager(unittest.TestCase):
    def setUp(self):
        from ui.power_manager import PowerManager, PowerProfile, BatteryState
        self.pm = PowerManager()
        self.PP = PowerProfile
        self.BS = BatteryState

    def test_initial_state(self):
        self.assertIsNotNone(self.pm.battery)
        self.assertGreater(len(self.pm.profiles), 0)
        self.assertIsNotNone(self.pm.current_profile)

    def test_set_profile(self):
        result = self.pm.set_profile("Performance")
        self.assertTrue(result)
        self.assertEqual(self.pm.current_profile.profile, self.PP.PERFORMANCE)

    def test_set_profile_not_found(self):
        result = self.pm.set_profile("Nonexistent")
        self.assertFalse(result)

    def test_sleep_system(self):
        from ui.power_manager import SleepAction
        result = self.pm.sleep_system(SleepAction.SUSPEND)
        self.assertTrue(result)

    def test_wake_system(self):
        result = self.pm.wake_system()
        self.assertTrue(result)

    def test_set_brightness(self):
        result = self.pm.set_brightness(50)
        self.assertTrue(result)
        self.assertEqual(self.pm.current_profile.screen_brightness, 50)

    def test_set_brightness_clamp(self):
        self.pm.set_brightness(150)
        self.assertEqual(self.pm.current_profile.screen_brightness, 100)
        self.pm.set_brightness(-10)
        self.assertEqual(self.pm.current_profile.screen_brightness, 0)

    def test_battery_health(self):
        health = self.pm.battery.health_percent
        self.assertGreater(health, 0)
        self.assertLessEqual(health, 100)

    def test_battery_charge_bar(self):
        bar = self.pm.battery.charge_bar
        self.assertEqual(len(bar), 20)

    def test_battery_state_icon(self):
        from ui.power_manager import BatteryInfo
        b = BatteryInfo(state=self.BS.CHARGING)
        self.assertEqual(b.state_icon, "⚡")
        b.state = self.BS.DISCHARGING
        self.assertEqual(b.state_icon, "🔋")

    def test_get_battery_estimate(self):
        estimate = self.pm.get_battery_estimate()
        self.assertIn("time_to_full_s", estimate)
        self.assertIn("time_to_empty_s", estimate)

    def test_get_power_summary(self):
        summary = self.pm.get_power_summary()
        self.assertIn("battery_percent", summary)
        self.assertIn("profile", summary)

    def test_get_recent_events(self):
        events = self.pm.get_recent_events(3)
        self.assertEqual(len(events), 3)

    def test_power_profile_description(self):
        from ui.power_manager import PowerProfileConfig
        p = PowerProfileConfig(name="Test", profile=self.PP.PERFORMANCE)
        self.assertIn("performance", p.description.lower())


class TestLogViewer(unittest.TestCase):
    def setUp(self):
        from ui.log_viewer import LogViewer, LogLevel, LogSource
        self.lv = LogViewer()
        self.LL = LogLevel
        self.LS = LogSource

    def test_initial_state(self):
        self.assertGreater(len(self.lv.entries), 0)
        self.assertGreater(len(self.lv.files), 0)
        self.assertGreater(len(self.lv.filters), 0)

    def test_add_entry(self):
        from ui.log_viewer import LogEntry
        entry = LogEntry(timestamp=time.time(), source=self.LS.NYRQIS,
                          level=self.LL.INFO, message="Test message")
        self.lv.add_entry(entry)
        self.assertIn(entry, self.lv.entries)

    def test_search(self):
        results = self.lv.search("battery")
        self.assertIsInstance(results, list)

    def test_filter_entries(self):
        results = self.lv.filter_entries(["Errors Only"])
        for entry in results:
            self.assertEqual(entry.level, self.LL.ERROR)

    def test_get_level_counts(self):
        counts = self.lv.get_level_counts()
        self.assertIn("info", counts)
        self.assertIn("error", counts)

    def test_get_source_counts(self):
        counts = self.lv.get_source_counts()
        self.assertIn("nyrqis", counts)

    def test_add_alert_rule(self):
        from ui.log_viewer import AlertRule
        rule = AlertRule(name="Test Alert", condition="level >= ERROR",
                          action="notify")
        self.lv.add_alert_rule(rule)
        self.assertIn(rule, self.lv.alert_rules)

    def test_toggle_alert_rule(self):
        result = self.lv.toggle_alert_rule("Kernel Panic")
        self.assertTrue(result)

    def test_add_highlight(self):
        hl = self.lv.add_highlight("test", "#ff0000")
        self.assertEqual(hl.pattern, "test")

    def test_start_stop_tailing(self):
        self.lv.start_tailing("/var/log/syslog")
        f = next(f for f in self.lv.files if f.path == "/var/log/syslog")
        self.assertTrue(f.is_tailing)
        self.lv.stop_tailing("/var/log/syslog")
        self.assertFalse(f.is_tailing)

    def test_log_entry_level_icon(self):
        from ui.log_viewer import LogEntry
        e = LogEntry(timestamp=time.time(), source=self.LS.NYRQIS,
                      level=self.LL.ERROR, message="test")
        self.assertEqual(e.level_icon, "❌")

    def test_log_entry_source_icon(self):
        from ui.log_viewer import LogEntry
        e = LogEntry(timestamp=time.time(), source=self.LS.NYRQIS,
                      level=self.LL.INFO, message="test")
        self.assertEqual(e.source_icon, "🍄")

    def test_log_file_size_display(self):
        from ui.log_viewer import LogFile
        f = LogFile(path="/test", size_bytes=500)
        self.assertEqual(f.size_display, "500 B")
        f.size_bytes = 2048
        self.assertEqual(f.size_display, "2.0 KB")
        f.size_bytes = 2 * 1024 * 1024
        self.assertEqual(f.size_display, "2.0 MB")

    def test_get_stats(self):
        stats = self.lv.get_stats()
        self.assertIn("total_entries", stats)
        self.assertIn("files", stats)


class TestAccessibilitySettings(unittest.TestCase):
    def setUp(self):
        from ui.accessibility import AccessibilitySettings, ColorScheme, CursorSize
        self.acc = AccessibilitySettings()
        self.CS = ColorScheme
        self.CZS = CursorSize

    def test_initial_state(self):
        self.assertFalse(self.acc.screen_reader.enabled)
        self.assertFalse(self.acc.magnifier.enabled)
        self.assertFalse(self.acc.high_contrast.enabled)
        self.assertFalse(self.acc.keyboard_nav.enabled)
        self.assertGreater(len(self.acc.shortcuts), 0)

    def test_toggle_screen_reader(self):
        result = self.acc.toggle_screen_reader()
        self.assertTrue(result)
        self.assertTrue(self.acc.screen_reader.enabled)
        result = self.acc.toggle_screen_reader()
        self.assertFalse(result)

    def test_toggle_magnifier(self):
        result = self.acc.toggle_magnifier()
        self.assertTrue(result)
        self.assertTrue(self.acc.magnifier.enabled)

    def test_zoom_in_out(self):
        initial = self.acc.magnifier.zoom_level
        self.acc.zoom_in()
        self.assertGreater(self.acc.magnifier.zoom_level, initial)
        self.acc.zoom_out()
        self.assertEqual(self.acc.magnifier.zoom_level, initial)

    def test_set_magnifier_zoom(self):
        result = self.acc.set_magnifier_zoom(5.0)
        self.assertTrue(result)
        self.assertEqual(self.acc.magnifier.zoom_level, 5.0)
        self.acc.set_magnifier_zoom(50.0)
        self.assertEqual(self.acc.magnifier.zoom_level, 20.0)

    def test_toggle_high_contrast(self):
        result = self.acc.toggle_high_contrast()
        self.assertTrue(result)
        self.assertTrue(self.acc.high_contrast.enabled)

    def test_set_color_scheme(self):
        result = self.acc.set_color_scheme(self.CS.YELLOW_BLACK)
        self.assertTrue(result)
        self.assertEqual(self.acc.high_contrast.background, "#ffff00")

    def test_set_text_scale(self):
        result = self.acc.set_text_scale(2.0)
        self.assertTrue(result)
        self.assertEqual(self.acc.high_contrast.text_scale, 2.0)

    def test_text_scale_clamp(self):
        self.acc.set_text_scale(5.0)
        self.assertEqual(self.acc.high_contrast.text_scale, 3.0)
        self.acc.set_text_scale(0.1)
        self.assertEqual(self.acc.high_contrast.text_scale, 0.5)

    def test_toggle_keyboard_nav(self):
        result = self.acc.toggle_keyboard_nav()
        self.assertTrue(result)
        self.assertTrue(self.acc.keyboard_nav.enabled)

    def test_toggle_sticky_keys(self):
        result = self.acc.toggle_sticky_keys()
        self.assertTrue(result)
        self.assertTrue(self.acc.keyboard_nav.sticky_keys)

    def test_toggle_slow_keys(self):
        result = self.acc.toggle_slow_keys()
        self.assertTrue(result)
        self.assertTrue(self.acc.keyboard_nav.slow_keys)

    def test_toggle_bounce_keys(self):
        result = self.acc.toggle_bounce_keys()
        self.assertTrue(result)
        self.assertTrue(self.acc.keyboard_nav.bounce_keys)

    def test_set_cursor_size(self):
        result = self.acc.set_cursor_size(self.CZS.EXTRA_LARGE)
        self.assertTrue(result)
        self.assertEqual(self.acc.cursor.size, self.CZS.EXTRA_LARGE)

    def test_cursor_size_pixels(self):
        self.acc.cursor.size = self.CZS.LARGE
        self.assertEqual(self.acc.cursor.size_pixels, 36)

    def test_toggle_cursor_trails(self):
        result = self.acc.toggle_cursor_trails()
        self.assertTrue(result)
        self.assertTrue(self.acc.cursor.trails)

    def test_get_active_features(self):
        self.acc.toggle_screen_reader()
        self.acc.toggle_magnifier()
        features = self.acc.get_active_features()
        self.assertIn("Screen Reader", features)
        self.assertGreater(len(features), 0)

    def test_get_stats(self):
        stats = self.acc.get_stats()
        self.assertIn("screen_reader", stats)
        self.assertIn("shortcuts", stats)

    def test_magnifier_zoom_bar(self):
        self.acc.magnifier.zoom_level = 5.0
        bar = self.acc.magnifier.zoom_bar
        self.assertEqual(len(bar), 24)

    def test_screen_reader_rate_bar(self):
        bar = self.acc.screen_reader.rate_bar
        self.assertEqual(len(bar), 20)


if __name__ == "__main__":
    unittest.main()
