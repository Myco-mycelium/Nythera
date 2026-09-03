"""Tests for font manager, music player, and image viewer."""

import unittest

from ui.font_manager import (
    FontManager, FontFamily, FontVariant, FontCategory, FontStyle, SYSTEM_FONTS,
)
from ui.music_player import (
    MusicPlayer, Track, Playlist, PlaybackState, RepeatMode,
    EQPreset, EQBand, EQ_PRESETS,
)
from ui.image_viewer import (
    ImageViewer, ImageInfo, ZoomMode, RotateAngle, GALLERY_IMAGES,
)


# ---------------------------------------------------------------------------
# FontManager tests
# ---------------------------------------------------------------------------

class TestFontManager(unittest.TestCase):
    """Tests for FontManager."""

    def setUp(self):
        self.fm = FontManager()

    def test_initialization(self):
        self.assertTrue(len(self.fm.families) > 0)

    def test_system_fonts_loaded(self):
        self.assertTrue(len(self.fm.families) >= len(SYSTEM_FONTS))

    def test_get_family(self):
        family = self.fm.get_family("DejaVu Sans")
        self.assertIsNotNone(family)
        self.assertEqual(family.name, "DejaVu Sans")

    def test_get_family_not_found(self):
        self.assertIsNone(self.fm.get_family("Nonexistent Font"))

    def test_install_font(self):
        family = self.fm.install_font("Custom Font", "/tmp/custom.ttf")
        self.assertIsNotNone(family)
        self.assertFalse(family.is_system)
        self.assertTrue(family.is_installed)

    def test_uninstall_font(self):
        self.fm.install_font("ToRemove", "/tmp/rtf.ttf")
        self.assertTrue(self.fm.uninstall_font("ToRemove"))

    def test_cannot_uninstall_system_font(self):
        result = self.fm.uninstall_font("DejaVu Sans")
        self.assertFalse(result)

    def test_filtered_families_search(self):
        self.fm.set_search("DejaVu")
        result = self.fm.filtered_families
        self.assertTrue(all("dejavu" in f.name.lower() for f in result))

    def test_filtered_families_category(self):
        self.fm.set_category_filter(FontCategory.MONOSPACE)
        result = self.fm.filtered_families
        self.assertTrue(all(f.category == FontCategory.MONOSPACE for f in result))

    def test_filtered_families_installed(self):
        self.fm.toggle_installed_only()
        result = self.fm.filtered_families
        self.assertTrue(all(f.is_installed for f in result))

    def test_set_preview_text(self):
        self.fm.set_preview_text("Hello World")
        self.assertEqual(self.fm._preview_text, "Hello World")

    def test_set_preview_size(self):
        self.fm.set_preview_size(48)
        self.assertEqual(self.fm._preview_size, 48)

    def test_navigation(self):
        self.fm.move_down()
        self.assertEqual(self.fm._selected_index, 1)
        self.fm.move_up()
        self.assertEqual(self.fm._selected_index, 0)

    def test_select(self):
        family = self.fm.select()
        self.assertIsNotNone(family)

    def test_handle_key_down(self):
        result = self.fm.handle_key("Down")
        self.assertEqual(result, "navigate")

    def test_handle_key_enter(self):
        result = self.fm.handle_key("Enter")
        self.assertTrue(result.startswith("select:"))

    def test_handle_key_escape(self):
        result = self.fm.handle_key("Escape")
        self.assertEqual(result, "close")

    def test_handle_key_search(self):
        result = self.fm.handle_key("a")
        self.assertEqual(result, "search")

    def test_font_variant(self):
        v = FontVariant(FontStyle.BOLD, weight=700)
        self.assertEqual(v.label, "Bold")

    def test_font_family_properties(self):
        family = self.fm.get_family("DejaVu Sans")
        self.assertTrue(family.has_bold)
        self.assertGreater(family.variant_count, 0)

    def test_stats(self):
        stats = self.fm.get_stats()
        self.assertIn("total", stats)
        self.assertIn("monospace", stats)

    def test_render(self):
        rgb, w, h = self.fm.render()
        self.assertEqual(len(rgb), w * h * 3)

    def test_to_dict(self):
        d = self.fm.to_dict()
        self.assertIn("total", d)
        self.assertIn("preview_size", d)


# ---------------------------------------------------------------------------
# MusicPlayer tests
# ---------------------------------------------------------------------------

class TestMusicPlayer(unittest.TestCase):
    """Tests for MusicPlayer."""

    def setUp(self):
        self.mp = MusicPlayer()

    def test_initialization(self):
        self.assertEqual(self.mp.state, PlaybackState.STOPPED)
        self.assertIsNone(self.mp.current_track)

    def test_play(self):
        self.mp.play()
        self.assertEqual(self.mp.state, PlaybackState.PLAYING)
        self.assertIsNotNone(self.mp.current_track)

    def test_pause(self):
        self.mp.play()
        self.mp.pause()
        self.assertEqual(self.mp.state, PlaybackState.PAUSED)
        self.mp.pause()
        self.assertEqual(self.mp.state, PlaybackState.PLAYING)

    def test_stop(self):
        self.mp.play()
        self.mp.stop()
        self.assertEqual(self.mp.state, PlaybackState.STOPPED)

    def test_next_track(self):
        self.mp.play()
        first = self.mp.current_track
        self.mp.next_track()
        self.assertNotEqual(self.mp.current_track.id, first.id)

    def test_prev_track(self):
        self.mp.play()
        self.mp.next_track()
        self.mp.prev_track()
        self.assertIsNotNone(self.mp.current_track)

    def test_prev_track_restart_if_past_3s(self):
        self.mp.play()
        self.mp._current_time = 5.0
        track = self.mp.current_track
        self.mp.prev_track()
        self.assertEqual(self.mp.current_time, 0.0)
        self.assertEqual(self.mp.current_track.id, track.id)

    def test_seek(self):
        self.mp.play()
        self.mp.seek(60.0)
        self.assertEqual(self.mp.current_time, 60.0)

    def test_seek_clamped(self):
        self.mp.play()
        self.mp.seek(99999)
        self.assertEqual(self.mp.current_time, self.mp.current_track.duration)

    def test_tick(self):
        self.mp.play()
        self.mp.tick(1.0)
        self.assertGreater(self.mp.current_time, 0)

    def test_volume(self):
        self.mp.set_volume(50)
        self.assertEqual(self.mp.volume, 50)

    def test_volume_clamped(self):
        self.mp.set_volume(150)
        self.assertEqual(self.mp.volume, 100)

    def test_mute(self):
        self.assertFalse(self.mp.muted)
        self.mp.toggle_mute()
        self.assertTrue(self.mp.muted)

    def test_repeat(self):
        self.assertEqual(self.mp._repeat, RepeatMode.OFF)
        self.mp.toggle_repeat()
        self.assertEqual(self.mp._repeat, RepeatMode.ALL)
        self.mp.toggle_repeat()
        self.assertEqual(self.mp._repeat, RepeatMode.ONE)

    def test_shuffle(self):
        self.assertFalse(self.mp.shuffle)
        self.mp.toggle_shuffle()
        self.assertTrue(self.mp.shuffle)

    def test_playlists(self):
        self.assertTrue(len(self.mp.playlists) >= 1)

    def test_create_playlist(self):
        pl = self.mp.create_playlist("Test")
        self.assertEqual(pl.name, "Test")
        self.assertEqual(len(self.mp.playlists), 2)

    def test_play_playlist(self):
        result = self.mp.play_playlist(0)
        self.assertTrue(result)
        self.assertEqual(self.mp.state, PlaybackState.PLAYING)

    def test_play_playlist_invalid(self):
        result = self.mp.play_playlist(999)
        self.assertFalse(result)

    def test_add_to_playlist(self):
        track = Track("new", "New Song", "Artist")
        result = self.mp.add_to_playlist(0, track)
        self.assertTrue(result)

    def test_remove_from_playlist(self):
        result = self.mp.remove_from_playlist(0, 0)
        self.assertTrue(result)

    def test_eq_toggle(self):
        self.assertFalse(self.mp.eq_enabled)
        self.mp.toggle_eq()
        self.assertTrue(self.mp.eq_enabled)

    def test_eq_preset(self):
        result = self.mp.set_eq_preset(EQPreset.ROCK)
        self.assertTrue(result)
        self.assertEqual(self.mp.eq_preset, EQPreset.ROCK)

    def test_eq_band(self):
        result = self.mp.set_eq_band(0, 6.0)
        self.assertTrue(result)
        self.assertEqual(self.mp.eq_bands[0].gain, 6.0)

    def test_eq_band_clamped(self):
        self.mp.set_eq_band(0, 20.0)
        self.assertEqual(self.mp.eq_bands[0].gain, 12.0)

    def test_track_display_duration(self):
        track = Track("t", "Song", "Artist", duration=185)
        self.assertEqual(track.display_duration, "3:05")

    def test_playlist_duration(self):
        pl = self.mp.current_playlist
        self.assertGreater(pl.total_duration, 0)

    def test_playlist_display_duration(self):
        pl = self.mp.current_playlist
        self.assertIn("m", pl.display_duration)

    def test_tick_track_end(self):
        self.mp.play()
        self.mp._current_time = self.mp.current_track.duration + 1
        self.mp.tick(1.0)
        # Should have moved to next track or stopped

    def test_stats(self):
        self.mp.play()
        stats = self.mp.get_stats()
        self.assertIn("state", stats)
        self.assertIn("volume", stats)

    def test_render(self):
        rgb, w, h = self.mp.render()
        self.assertEqual(len(rgb), w * h * 3)

    def test_to_dict(self):
        d = self.mp.to_dict()
        self.assertIn("state", d)
        self.assertIn("playlists", d)

    def test_visibility(self):
        self.assertFalse(self.mp.visible)
        self.mp.show()
        self.assertTrue(self.mp.visible)
        self.mp.hide()
        self.assertFalse(self.mp.visible)

    def test_eq_presets_exist(self):
        for preset in EQPreset:
            self.assertIn(preset, EQ_PRESETS)


# ---------------------------------------------------------------------------
# ImageViewer tests
# ---------------------------------------------------------------------------

class TestImageViewer(unittest.TestCase):
    """Tests for ImageViewer."""

    def setUp(self):
        self.iv = ImageViewer()

    def test_initialization(self):
        self.assertEqual(len(self.iv.gallery), len(GALLERY_IMAGES))
        self.assertIsNone(self.iv.current)

    def test_open_image(self):
        result = self.iv.open_image(GALLERY_IMAGES[0].path)
        self.assertTrue(result)
        self.assertIsNotNone(self.iv.current)

    def test_next_image(self):
        self.iv.open_image(GALLERY_IMAGES[0].path)
        img = self.iv.next_image()
        self.assertIsNotNone(img)

    def test_prev_image(self):
        self.iv.open_image(GALLERY_IMAGES[1].path)
        img = self.iv.prev_image()
        self.assertIsNotNone(img)

    def test_select(self):
        img = self.iv.select(0)
        self.assertIsNotNone(img)
        self.assertEqual(img.path, GALLERY_IMAGES[0].path)

    def test_select_invalid(self):
        img = self.iv.select(999)
        self.assertIsNone(img)

    def test_zoom_in(self):
        initial = self.iv.zoom
        self.iv.zoom_in()
        self.assertGreater(self.iv.zoom, initial)

    def test_zoom_out(self):
        self.iv.zoom_in()
        initial = self.iv.zoom
        self.iv.zoom_out()
        self.assertLess(self.iv.zoom, initial)

    def test_zoom_fit(self):
        self.iv.zoom_in()
        self.iv.zoom_fit()
        self.assertEqual(self.iv.zoom, 1.0)
        self.assertEqual(self.iv._zoom_mode, ZoomMode.FIT_WINDOW)

    def test_zoom_actual(self):
        self.iv.zoom_actual()
        self.assertEqual(self.iv._zoom_mode, ZoomMode.ACTUAL_SIZE)

    def test_set_zoom(self):
        self.iv.set_zoom(2.5)
        self.assertEqual(self.iv.zoom, 2.5)

    def test_rotate_cw(self):
        result = self.iv.rotate_cw()
        self.assertEqual(result, RotateAngle.CW_90)

    def test_rotate_ccw(self):
        result = self.iv.rotate_ccw()
        self.assertEqual(result, RotateAngle.CW_270)

    def test_flip(self):
        self.assertFalse(self.iv._flipped_h)
        self.iv.flip_horizontal()
        self.assertTrue(self.iv._flipped_h)
        self.iv.flip_horizontal()
        self.assertFalse(self.iv._flipped_h)

    def test_slideshow(self):
        self.assertFalse(self.iv._slideshow)
        self.iv.toggle_slideshow()
        self.assertTrue(self.iv._slideshow)

    def test_slideshow_tick(self):
        self.iv.open_image(GALLERY_IMAGES[0].path)
        self.iv.toggle_slideshow()
        self.iv._slideshow_interval = 0.01
        changed = self.iv.tick(0.02)
        self.assertTrue(changed)

    def test_remove_from_gallery(self):
        initial = len(self.iv.gallery)
        result = self.iv.remove_from_gallery(0)
        self.assertTrue(result)
        self.assertEqual(len(self.iv.gallery), initial - 1)

    def test_clear_gallery(self):
        count = self.iv.clear_gallery()
        self.assertGreater(count, 0)
        self.assertEqual(len(self.iv.gallery), 0)

    def test_handle_key_right(self):
        self.iv.open_image(GALLERY_IMAGES[0].path)
        result = self.iv.handle_key("Right")
        self.assertEqual(result, "next")

    def test_handle_key_left(self):
        self.iv.open_image(GALLERY_IMAGES[0].path)
        result = self.iv.handle_key("Left")
        self.assertEqual(result, "prev")

    def test_handle_key_zoom(self):
        result = self.iv.handle_key("+")
        self.assertEqual(result, "zoom")

    def test_handle_key_rotate(self):
        result = self.iv.handle_key("r")
        self.assertEqual(result, "rotate")

    def test_handle_key_info(self):
        result = self.iv.handle_key("i")
        self.assertEqual(result, "info")

    def test_handle_key_escape(self):
        result = self.iv.handle_key("Escape")
        self.assertEqual(result, "close")

    def test_image_info(self):
        img = GALLERY_IMAGES[0]
        self.assertIn("×", img.display_size)
        self.assertIn("MB", img.display_file_size)
        self.assertIn(":", img.aspect_ratio)

    def test_stats(self):
        self.iv.open_image(GALLERY_IMAGES[0].path)
        stats = self.iv.get_stats()
        self.assertIn("gallery_size", stats)
        self.assertIn("zoom", stats)

    def test_render(self):
        self.iv.open_image(GALLERY_IMAGES[0].path)
        rgb, w, h = self.iv.render()
        self.assertEqual(len(rgb), w * h * 3)

    def test_to_dict(self):
        d = self.iv.to_dict()
        self.assertIn("gallery", d)
        self.assertIn("zoom", d)

    def test_visibility(self):
        self.assertFalse(self.iv.visible)
        self.iv.show()
        self.assertTrue(self.iv.visible)
        self.iv.hide()
        self.assertFalse(self.iv.visible)


if __name__ == "__main__":
    unittest.main()
