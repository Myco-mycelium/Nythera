"""Tests for display settings, audio mixer, and unified desktop demo."""

import os
import tempfile
import unittest

from ui.display_settings import (
    DisplaySettings, DisplayMode, Wallpaper, WallpaperMode,
    NightLightConfig, NightLightMode, DisplayConfig, DisplayOrientation,
)
from ui.audio_mixer import (
    AudioMixer, AudioDevice, AudioStream, AudioDeviceType,
    AudioDirection, AudioProfile, AudioProfileConfig,
)


# ---------------------------------------------------------------------------
# DisplaySettings tests
# ---------------------------------------------------------------------------

class TestDisplaySettings(unittest.TestCase):
    """Tests for DisplaySettings."""

    def setUp(self):
        self.ds = DisplaySettings(width=480, height=600)

    def test_initialization(self):
        self.assertEqual(self.ds.width, 480)
        self.assertEqual(self.ds.height, 600)
        self.assertEqual(self.ds.config.resolution, (1920, 1080))

    def test_available_modes(self):
        modes = self.ds.available_modes
        self.assertTrue(len(modes) > 0)
        self.assertTrue(all(m.available for m in modes))

    def test_set_resolution_preset(self):
        result = self.ds.set_resolution(2560, 1440)
        self.assertTrue(result)
        self.assertEqual(self.ds.config.resolution, (2560, 1440))

    def test_set_resolution_custom(self):
        result = self.ds.set_resolution(1366, 768)
        self.assertTrue(result)
        self.assertEqual(self.ds.config.resolution, (1366, 768))

    def test_set_refresh_rate(self):
        self.ds.set_refresh_rate(144)
        self.assertEqual(self.ds.config.refresh_rate, 144)

    def test_get_compatible_rates(self):
        rates = self.ds.get_compatible_rates(1920, 1080)
        self.assertIn(60, rates)
        self.assertTrue(len(rates) >= 1)

    def test_set_scaling(self):
        self.ds.set_scaling(1.5)
        self.assertEqual(self.ds.config.scaling, 1.5)

    def test_set_scaling_clamped(self):
        self.ds.set_scaling(5.0)
        self.assertEqual(self.ds.config.scaling, 3.0)
        self.ds.set_scaling(0.1)
        self.assertEqual(self.ds.config.scaling, 0.5)

    def test_get_effective_resolution(self):
        self.ds.set_scaling(2.0)
        w, h = self.ds.get_effective_resolution()
        self.assertEqual(w, 960)
        self.assertEqual(h, 540)

    def test_set_orientation(self):
        self.ds.set_orientation(DisplayOrientation.PORTRAIT)
        self.assertEqual(self.ds.config.orientation, DisplayOrientation.PORTRAIT)

    def test_set_wallpaper(self):
        result = self.ds.set_wallpaper("eclipse-dark")
        self.assertTrue(result)
        self.assertEqual(self.ds.config.wallpaper_id, "eclipse-dark")

    def test_set_wallpaper_not_found(self):
        result = self.ds.set_wallpaper("nonexistent")
        self.assertFalse(result)

    def test_add_custom_wallpaper(self):
        wp = self.ds.add_custom_wallpaper("My Photo", "/tmp/photo.jpg")
        self.assertIsNotNone(wp.id)
        self.assertFalse(wp.builtin)
        self.assertEqual(len(self.ds._custom_wallpapers), 1)

    def test_remove_custom_wallpaper(self):
        wp = self.ds.add_custom_wallpaper("Test", "/tmp/test.jpg")
        self.assertTrue(self.ds.remove_custom_wallpaper(wp.id))
        self.assertEqual(len(self.ds._custom_wallpapers), 0)

    def test_get_wallpaper(self):
        wp = self.ds.get_wallpaper("eclipse-dark")
        self.assertIsNotNone(wp)
        self.assertEqual(wp.name, "Eclipse Dark")

    def test_set_night_light(self):
        self.ds.set_night_light(NightLightMode.ALWAYS, temperature=3000, strength=0.8)
        self.assertEqual(self.ds.config.night_light.mode, NightLightMode.ALWAYS)
        self.assertEqual(self.ds.config.night_light.temperature, 3000)
        self.assertEqual(self.ds.config.night_light.strength, 0.8)

    def test_night_light_color(self):
        self.ds.set_night_light(NightLightMode.OFF)
        color = self.ds.get_night_light_color()
        self.assertEqual(color, (255, 255, 255))

        self.ds.set_night_light(NightLightMode.ALWAYS, temperature=3000)
        color = self.ds.get_night_light_color()
        self.assertNotEqual(color, (255, 255, 255))

    def test_add_monitor(self):
        monitor = self.ds.add_monitor("External", (2560, 1440))
        self.assertEqual(monitor["name"], "External")
        self.assertEqual(len(self.ds.monitors), 2)

    def test_remove_monitor(self):
        monitor = self.ds.add_monitor("External", (2560, 1440))
        self.assertTrue(self.ds.remove_monitor(monitor["id"]))

    def test_remove_primary_monitor_fails(self):
        primary = self.ds.monitors[0]
        self.assertFalse(self.ds.remove_monitor(primary["id"]))

    def test_set_primary_monitor(self):
        monitor = self.ds.add_monitor("External", (2560, 1440))
        self.assertTrue(self.ds.set_primary_monitor(monitor["id"]))
        self.assertTrue(monitor["primary"])

    def test_arrange_monitors_horizontal(self):
        self.ds.add_monitor("Ext", (2560, 1440))
        self.ds.arrange_monitors("horizontal")
        monitors = self.ds.monitors
        self.assertEqual(monitors[1]["position"][0], 1920)

    def test_arrange_monitors_vertical(self):
        self.ds.add_monitor("Ext", (2560, 1440))
        self.ds.arrange_monitors("vertical")
        monitors = self.ds.monitors
        self.assertEqual(monitors[1]["position"][1], 1080)

    def test_get_total_resolution(self):
        self.ds.add_monitor("Ext", (2560, 1440), position=(1920, 0))
        total = self.ds.get_total_resolution()
        self.assertEqual(total, (1920 + 2560, 1440))

    def test_mode_label(self):
        mode = DisplayMode(1920, 1080, 60)
        self.assertIn("1920", mode.label)
        self.assertIn("60Hz", mode.label)

    def test_mode_aspect_ratio(self):
        mode = DisplayMode(1920, 1080, 60)
        self.assertEqual(mode.aspect_ratio, "16:9")

    def test_render(self):
        rgb, w, h = self.ds.render()
        self.assertEqual(w, 480)
        self.assertEqual(h, 600)
        self.assertEqual(len(rgb), w * h * 3)

    def test_to_dict(self):
        d = self.ds.to_dict()
        self.assertIn("resolution", d)
        self.assertIn("scaling", d)
        self.assertIn("wallpaper", d)
        self.assertIn("night_light", d)

    def test_wallpapers_builtin(self):
        self.assertTrue(len(self.ds.wallpapers) >= 5)


# ---------------------------------------------------------------------------
# AudioMixer tests
# ---------------------------------------------------------------------------

class TestAudioMixer(unittest.TestCase):
    """Tests for AudioMixer."""

    def setUp(self):
        self.am = AudioMixer(width=400, height=600)

    def test_initialization(self):
        self.assertEqual(self.am.master_volume, 80)
        self.assertFalse(self.am.master_muted)

    def test_set_master_volume(self):
        self.am.set_master_volume(50)
        self.assertEqual(self.am.master_volume, 50)

    def test_set_master_volume_clamped(self):
        self.am.set_master_volume(150)
        self.assertEqual(self.am.master_volume, 100)
        self.am.set_master_volume(-10)
        self.assertEqual(self.am.master_volume, 0)

    def test_toggle_master_mute(self):
        self.assertFalse(self.am.master_muted)
        self.am.toggle_master_mute()
        self.assertTrue(self.am.master_muted)
        self.am.toggle_master_mute()
        self.assertFalse(self.am.master_muted)

    def test_output_devices(self):
        devices = self.am.output_devices
        self.assertTrue(len(devices) >= 2)
        types = {d.device_type for d in devices}
        self.assertIn(AudioDeviceType.SPEAKERS, types)

    def test_input_devices(self):
        devices = self.am.input_devices
        self.assertTrue(len(devices) >= 1)

    def test_active_output(self):
        active = self.am.active_output
        self.assertIsNotNone(active)
        self.assertTrue(active.active)

    def test_active_input(self):
        active = self.am.active_input
        self.assertIsNotNone(active)
        self.assertTrue(active.active)

    def test_set_output_device(self):
        result = self.am.set_output_device("headphones")
        self.assertTrue(result)
        self.assertEqual(self.am.active_output.id, "headphones")

    def test_set_output_device_not_found(self):
        result = self.am.set_output_device("nonexistent")
        self.assertFalse(result)

    def test_set_input_device(self):
        result = self.am.set_input_device("mic-usb")
        self.assertTrue(result)
        self.assertEqual(self.am.active_input.id, "mic-usb")

    def test_set_device_volume(self):
        result = self.am.set_device_volume("speakers", 75)
        self.assertTrue(result)
        speakers = self.am.active_output
        self.assertEqual(speakers.volume, 75)

    def test_toggle_device_mute(self):
        result = self.am.toggle_device_mute("speakers")
        self.assertTrue(result)
        self.assertTrue(self.am.active_output.muted)

    def test_add_stream(self):
        stream = self.am.add_stream("vlc", "VLC", (255, 100, 100, 255))
        self.assertEqual(stream.app_id, "vlc")
        self.assertEqual(len(self.am.streams), 4)

    def test_remove_stream(self):
        result = self.am.remove_stream("firefox")
        self.assertTrue(result)
        self.assertEqual(len(self.am.streams), 2)

    def test_remove_stream_not_found(self):
        result = self.am.remove_stream("nonexistent")
        self.assertFalse(result)

    def test_set_stream_volume(self):
        result = self.am.set_stream_volume("firefox", 30)
        self.assertTrue(result)
        for s in self.am.streams:
            if s.app_id == "firefox":
                self.assertEqual(s.volume, 30)

    def test_toggle_stream_mute(self):
        result = self.am.toggle_stream_mute("firefox")
        self.assertTrue(result)
        for s in self.am.streams:
            if s.app_id == "firefox":
                self.assertTrue(s.muted)

    def test_update_stream_peak(self):
        self.am.update_stream_peak("spotify", 0.75)
        for s in self.am.streams:
            if s.app_id == "spotify":
                self.assertAlmostEqual(s.peak, 0.75)

    def test_set_profile(self):
        result = self.am.set_profile(AudioProfile.MUSIC)
        self.assertTrue(result)
        self.assertEqual(self.am.active_profile, AudioProfile.MUSIC)
        self.assertEqual(self.am._bass, 65)

    def test_set_bass(self):
        self.am.set_bass(80)
        self.assertEqual(self.am._bass, 80)

    def test_set_treble(self):
        self.am.set_treble(30)
        self.assertEqual(self.am._treble, 30)

    def test_set_balance(self):
        self.am.set_balance(0)  # full left
        self.assertEqual(self.am._balance, 0)

    def test_toggle_spatial(self):
        self.assertFalse(self.am._spatial)
        self.am.toggle_spatial()
        self.assertTrue(self.am._spatial)

    def test_toggle_night_mode(self):
        self.assertFalse(self.am._night_mode)
        self.am.toggle_night_mode()
        self.assertTrue(self.am._night_mode)

    def test_profiles(self):
        profiles = self.am.profiles
        self.assertTrue(len(profiles) >= 4)

    def test_stats(self):
        stats = self.am.get_stats()
        self.assertIn("master_volume", stats)
        self.assertIn("streams", stats)
        self.assertIn("profile", stats)

    def test_render(self):
        rgb, w, h = self.am.render()
        self.assertEqual(w, 400)
        self.assertEqual(h, 600)
        self.assertEqual(len(rgb), w * h * 3)

    def test_to_dict(self):
        d = self.am.to_dict()
        self.assertIn("master_volume", d)
        self.assertIn("streams", d)


# ---------------------------------------------------------------------------
# Unified Desktop Demo tests
# ---------------------------------------------------------------------------

class TestUnifiedDesktop(unittest.TestCase):
    """Tests for unified desktop demo rendering."""

    def test_render_all_states(self):
        """Test that all 10 states render without error."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        from demo.unified_desktop import render_frame

        states = [
            "desktop", "notifications", "quick_settings", "launcher",
            "spotlight", "monitor", "context_menu", "power",
            "display_settings", "audio_mixer",
        ]
        for state in states:
            img = render_frame(800, 600, state)
            self.assertIsNotNone(img)
            self.assertEqual(img.size, (800, 600))

    def test_render_to_file(self):
        """Test rendering to actual files."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        from demo.unified_desktop import render_frame

        with tempfile.TemporaryDirectory() as tmpdir:
            img = render_frame(800, 600, "desktop")
            path = os.path.join(tmpdir, "test_desktop.png")
            img.save(path)
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 0)


if __name__ == "__main__":
    unittest.main()
