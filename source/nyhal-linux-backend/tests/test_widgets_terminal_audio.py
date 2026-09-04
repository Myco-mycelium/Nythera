import unittest
import time


class TestDesktopWidgets(unittest.TestCase):
    def setUp(self):
        from ui.desktop_widgets import DesktopWidgetToolkit, WidgetType, StickyColor
        self.dw = DesktopWidgetToolkit()
        self.WT = WidgetType
        self.SC = StickyColor

    def test_initial_state(self):
        self.assertGreater(len(self.dw.widgets), 0)
        self.assertGreater(len(self.dw.sticky_notes), 0)
        self.assertGreater(len(self.dw.forecasts), 0)

    def test_add_widget(self):
        widget = self.dw.add_widget(self.WT.CLOCK, "New Clock")
        self.assertEqual(widget.widget_type, self.WT.CLOCK)
        self.assertIn(widget, self.dw.widgets)

    def test_remove_widget(self):
        widget_id = self.dw.widgets[0].id
        result = self.dw.remove_widget(widget_id)
        self.assertTrue(result)

    def test_toggle_widget(self):
        widget_id = self.dw.widgets[0].id
        initial = self.dw.widgets[0].visible
        self.dw.toggle_widget(widget_id)
        self.assertNotEqual(self.dw.widgets[0].visible, initial)

    def test_move_widget(self):
        widget_id = self.dw.widgets[0].id
        result = self.dw.move_widget(widget_id, 100, 200)
        self.assertTrue(result)
        self.assertEqual(self.dw.widgets[0].position.x, 100)

    def test_add_sticky_note(self):
        note = self.dw.add_sticky_note("Test note", self.SC.PINK)
        self.assertEqual(note.content, "Test note")
        self.assertEqual(note.color, self.SC.PINK)

    def test_update_sticky_note(self):
        result = self.dw.update_sticky_note(1, "Updated content")
        self.assertTrue(result)
        note = next(n for n in self.dw.sticky_notes if n.id == 1)
        self.assertEqual(note.content, "Updated content")

    def test_delete_sticky_note(self):
        result = self.dw.delete_sticky_note(1)
        self.assertTrue(result)

    def test_get_visible_widgets(self):
        visible = self.dw.get_visible_widgets()
        self.assertGreater(len(visible), 0)

    def test_get_weather_summary(self):
        summary = self.dw.get_weather_summary()
        self.assertIn("temperature", summary)
        self.assertIn("condition", summary)

    def test_get_stats(self):
        stats = self.dw.get_stats()
        self.assertIn("widgets", stats)
        self.assertIn("sticky_notes", stats)

    def test_clock_time_display(self):
        display = self.dw.clock.time_display
        self.assertIn(":", display)

    def test_clock_date_display(self):
        display = self.dw.clock.date_display
        self.assertIn(time.strftime("%B"), display)

    def test_cpu_monitor_bar(self):
        bar = self.dw.cpu_monitor.usage_bar
        self.assertEqual(len(bar), 20)

    def test_cpu_monitor_sparkline(self):
        sparkline = self.dw.cpu_monitor.sparkline
        self.assertIn("▃", sparkline)


class TestTerminalEmulator(unittest.TestCase):
    def setUp(self):
        from ui.terminal_emulator import TerminalEmulator, ShellType
        self.te = TerminalEmulator()
        self.ST = ShellType

    def test_initial_state(self):
        self.assertGreater(len(self.te.tabs), 0)
        self.assertGreater(len(self.te.profiles), 0)
        self.assertIsNotNone(self.te.current_tab)

    def test_new_tab(self):
        tab = self.te.new_tab()
        self.assertIn(tab, self.te.tabs)
        self.assertEqual(len(self.te.tabs), 4)

    def test_close_tab(self):
        tab = self.te.new_tab()
        result = self.te.close_tab(tab.id)
        self.assertTrue(result)

    def test_switch_tab(self):
        result = self.te.switch_tab(self.te.tabs[1].id)
        self.assertTrue(result)
        self.assertEqual(self.te.current_tab.id, self.te.tabs[1].id)

    def test_execute_command(self):
        output = self.te.execute_command("pwd")
        self.assertIn(self.te.current_tab.cwd, output)

    def test_execute_cd(self):
        self.te.execute_command("cd /tmp")
        self.assertEqual(self.te.current_tab.cwd, "/tmp")

    def test_execute_echo(self):
        output = self.te.execute_command("echo hello")
        self.assertIn("hello", output)

    def test_zoom_in(self):
        initial = self.te.current_tab.zoom_level
        result = self.te.zoom_in()
        self.assertGreater(result, initial)

    def test_zoom_out(self):
        self.te.zoom_in()
        result = self.te.zoom_out()
        self.assertIsNotNone(result)

    def test_search_history(self):
        results = self.te.search_history("cargo")
        self.assertGreater(len(results), 0)

    def test_set_profile(self):
        result = self.te.set_profile(self.te.tabs[0].id, "Matrix")
        self.assertTrue(result)
        self.assertEqual(self.te.tabs[0].profile.name, "Matrix")

    def test_clear_tab(self):
        result = self.te.clear_tab(self.te.tabs[0].id)
        self.assertTrue(result)

    def test_get_stats(self):
        stats = self.te.get_stats()
        self.assertIn("tabs", stats)
        self.assertIn("history_entries", stats)

    def test_tab_display_title(self):
        tab = self.te.tabs[0]
        self.assertIn("dev", tab.display_title)


class TestAudioMixer(unittest.TestCase):
    def setUp(self):
        from ui.audio_mixer import AudioMixer, AudioDeviceState
        self.am = AudioMixer()
        self.ADS = AudioDeviceState

    def test_initial_state(self):
        self.assertGreater(len(self.am.devices), 0)
        self.assertGreater(len(self.am.apps), 0)
        self.assertGreater(len(self.am.eq_bands), 0)
        self.assertGreater(len(self.am.eq_presets), 0)

    def test_set_master_volume(self):
        result = self.am.set_master_volume(50)
        self.assertTrue(result)
        self.assertEqual(self.am.master_volume, 50)

    def test_toggle_master_mute(self):
        result = self.am.toggle_master_mute()
        self.assertTrue(result)
        self.assertTrue(self.am.master_muted)

    def test_set_app_volume(self):
        result = self.am.set_app_volume("Spotify", 90)
        self.assertTrue(result)

    def test_toggle_app_mute(self):
        result = self.am.toggle_app_mute("Firefox")
        self.assertTrue(result)

    def test_set_device_volume(self):
        result = self.am.set_device_volume("Built-in Audio", 85)
        self.assertTrue(result)

    def test_set_default_device(self):
        result = self.am.set_default_device("Sony WH-1000XM5")
        self.assertTrue(result)
        sony = next(d for d in self.am.devices if d.name == "Sony WH-1000XM5")
        self.assertTrue(sony.is_default)

    def test_set_eq_band(self):
        result = self.am.set_eq_band(0, 6.0)
        self.assertTrue(result)
        self.assertEqual(self.am.eq_bands[0].gain_db, 6.0)

    def test_set_eq_band_invalid(self):
        result = self.am.set_eq_band(99, 5.0)
        self.assertFalse(result)

    def test_apply_eq_preset(self):
        result = self.am.apply_eq_preset("Bass Boost")
        self.assertTrue(result)
        self.assertEqual(self.am.eq_bands[0].gain_db, 6.0)

    def test_toggle_effect(self):
        result = self.am.toggle_effect("Noise Suppression")
        self.assertTrue(result)
        effect = next(e for e in self.am.effects if e.name == "Noise Suppression")
        self.assertFalse(effect.enabled)

    def test_get_active_devices(self):
        active = self.am.get_active_devices()
        self.assertGreater(len(active), 0)

    def test_get_playing_apps(self):
        playing = self.am.get_playing_apps()
        self.assertGreater(len(playing), 0)

    def test_get_stats(self):
        stats = self.am.get_stats()
        self.assertIn("devices", stats)
        self.assertIn("master_volume", stats)

    def test_device_volume_bar(self):
        from ui.audio_mixer import AudioDevice, AudioDeviceType
        d = AudioDevice(name="test", device_type=AudioDeviceType.SPEAKER, volume=50)
        bar = d.volume_bar
        self.assertEqual(len(bar), 20)

    def test_device_mute_icon(self):
        from ui.audio_mixer import AudioDevice, AudioDeviceType
        d = AudioDevice(name="test", device_type=AudioDeviceType.SPEAKER, muted=True)
        self.assertEqual(d.mute_icon, "🔇")

    def test_eq_band_gain_bar(self):
        from ui.audio_mixer import EQBand
        b = EQBand(gain_db=0.0, min_db=-12.0, max_db=12.0)
        bar = b.gain_bar
        self.assertEqual(len(bar), 20)

    def test_app_volume_bar(self):
        from ui.audio_mixer import AudioApp
        a = AudioApp(name="test", volume=50)
        bar = a.volume_bar
        self.assertEqual(len(bar), 20)


if __name__ == "__main__":
    unittest.main()
