"""
Tests for Web Browser, Video Player, and Notes App.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.web_browser import (
    WebBrowser, BrowserTab, Bookmark, HistoryEntry, Download,
    TabState, SimpleHTMLRenderer
)
from ui.video_player import (
    VideoPlayer, VideoInfo, PlaylistItem, Chapter, Subtitle,
    AspectRatio, RepeatMode
)
from ui.notes_app import (
    NotesApp, Note, Folder, NoteEditor, MarkdownRenderer,
    SortMode, NoteColor
)


# ─── Web Browser Tests ───────────────────────────────────────────────────


class TestWebBrowser(unittest.TestCase):

    def setUp(self):
        self.browser = WebBrowser()

    def test_initial_state(self):
        self.assertEqual(self.browser.tab_count, 1)
        self.assertEqual(self.browser.tab_index, 0)
        self.assertIsNotNone(self.browser.current_tab)
        self.assertTrue(self.browser.current_tab.url.startswith("nyrqis://"))

    def test_new_tab(self):
        tab = self.browser.new_tab("https://example.com")
        self.assertEqual(self.browser.tab_count, 2)
        self.assertEqual(self.browser.tab_index, 1)
        self.assertEqual(tab.url, "https://example.com")

    def test_new_tab_default(self):
        tab = self.browser.new_tab()
        self.assertEqual(tab.url, "nyrqis://newtab")
        self.assertEqual(tab.state, TabState.NEW)

    def test_new_tab_private(self):
        tab = self.browser.new_tab(private=True)
        self.assertTrue(tab.is_private)

    def test_close_tab(self):
        self.browser.new_tab()
        self.assertEqual(self.browser.tab_count, 2)
        result = self.browser.close_tab(1)
        self.assertTrue(result)
        self.assertEqual(self.browser.tab_count, 1)

    def test_cannot_close_last_tab(self):
        result = self.browser.close_tab()
        self.assertFalse(result)
        self.assertEqual(self.browser.tab_count, 1)

    def test_close_tab_wraps_index(self):
        self.browser.new_tab()
        self.browser.new_tab()
        self.browser.switch_tab(2)
        self.browser.close_tab(2)
        self.assertEqual(self.browser.tab_index, 1)

    def test_switch_tab(self):
        self.browser.new_tab()
        self.browser.new_tab()
        result = self.browser.switch_tab(2)
        self.assertTrue(result)
        self.assertEqual(self.browser.tab_index, 2)

    def test_switch_tab_invalid(self):
        result = self.browser.switch_tab(99)
        self.assertFalse(result)

    def test_next_tab(self):
        self.browser.new_tab()
        self.browser.new_tab()
        self.browser.switch_tab(0)
        self.browser.next_tab()
        self.assertEqual(self.browser.tab_index, 1)

    def test_next_tab_wraps(self):
        self.browser.next_tab()
        self.assertEqual(self.browser.tab_index, 0)

    def test_prev_tab(self):
        self.browser.new_tab()
        self.browser.switch_tab(1)
        self.browser.prev_tab()
        self.assertEqual(self.browser.tab_index, 0)

    def test_close_other_tabs(self):
        self.browser.new_tab()
        self.browser.new_tab()
        count = self.browser.close_other_tabs()
        self.assertEqual(count, 2)
        self.assertEqual(self.browser.tab_count, 1)

    def test_navigate(self):
        result = self.browser.navigate("https://example.com")
        self.assertTrue(result)
        self.assertEqual(self.browser.current_tab.url, "https://example.com")

    def test_navigate_auto_protocol(self):
        self.browser.navigate("example.com")
        self.assertEqual(self.browser.current_tab.url, "https://example.com")

    def test_navigate_search(self):
        self.browser.navigate("hello world")
        self.assertIn("duckduckgo.com", self.browser.current_tab.url)

    def test_navigate_builtin(self):
        self.browser.navigate("nyrqis://newtab")
        self.assertEqual(self.browser.current_tab.title, "New Tab")
        self.assertEqual(self.browser.current_tab.state, TabState.COMPLETE)

    def test_go_back(self):
        self.browser.navigate("https://first.com")
        self.browser.navigate("https://second.com")
        result = self.browser.go_back()
        self.assertTrue(result)
        self.assertEqual(self.browser.current_tab.url, "https://first.com")

    def test_go_back_empty(self):
        result = self.browser.go_back()
        self.assertFalse(result)

    def test_go_forward(self):
        self.browser.navigate("https://first.com")
        self.browser.navigate("https://second.com")
        self.browser.go_back()
        result = self.browser.go_forward()
        self.assertTrue(result)
        self.assertEqual(self.browser.current_tab.url, "https://second.com")

    def test_go_forward_empty(self):
        result = self.browser.go_forward()
        self.assertFalse(result)

    def test_reload(self):
        self.browser.navigate("https://example.com")
        result = self.browser.reload()
        self.assertTrue(result)

    def test_go_home(self):
        self.browser.navigate("https://example.com")
        self.browser.go_home()
        self.assertIn("newtab", self.browser.current_tab.url)

    def test_can_go_back(self):
        self.assertFalse(self.browser.can_go_back)
        self.browser.navigate("https://example.com")
        self.browser.navigate("https://other.com")
        self.assertTrue(self.browser.can_go_back)

    def test_can_go_forward(self):
        self.assertFalse(self.browser.can_go_forward)

    def test_url_bar_editing(self):
        self.browser.start_url_edit()
        self.assertTrue(self.browser._url_editing)
        self.browser.update_url_text("example.com")
        self.browser.submit_url()
        self.assertFalse(self.browser._url_editing)
        self.assertIn("example.com", self.browser.current_tab.url)

    def test_url_bar_cancel(self):
        self.browser.start_url_edit()
        self.browser.update_url_text("example.com")
        self.browser.cancel_url_edit()
        self.assertFalse(self.browser._url_editing)

    def test_url_suggestions(self):
        self.browser.start_url_edit()
        self.browser.update_url_text("github")
        suggestions = self.browser.url_suggestions
        self.assertIsInstance(suggestions, list)

    def test_zoom_in(self):
        zoom = self.browser.zoom_in()
        self.assertGreater(zoom, 1.0)

    def test_zoom_out(self):
        self.browser.zoom_in()
        zoom = self.browser.zoom_out()
        self.assertEqual(zoom, 1.0)

    def test_zoom_reset(self):
        self.browser.zoom_in()
        zoom = self.browser.zoom_reset()
        self.assertEqual(zoom, 1.0)

    def test_zoom_to(self):
        zoom = self.browser.zoom_to(2.0)
        self.assertEqual(zoom, 2.0)

    def test_add_bookmark(self):
        self.browser.navigate("https://example.com")
        bm = self.browser.add_bookmark("Example")
        self.assertEqual(bm.url, "https://example.com")
        self.assertTrue(self.browser.is_bookmarked("https://example.com"))

    def test_remove_bookmark(self):
        self.browser.add_bookmark("Example", "https://example.com")
        result = self.browser.remove_bookmark("https://example.com")
        self.assertTrue(result)
        self.assertFalse(self.browser.is_bookmarked("https://example.com"))

    def test_is_bookmarked(self):
        self.assertFalse(self.browser.is_bookmarked("https://example.com"))
        self.browser.add_bookmark("Example", "https://example.com")
        self.assertTrue(self.browser.is_bookmarked("https://example.com"))

    def test_get_bookmarks(self):
        bms = self.browser.get_bookmarks()
        self.assertIsInstance(bms, list)
        self.assertGreater(len(bms), 0)

    def test_bookmark_folders(self):
        folders = self.browser.bookmark_folders
        self.assertIsInstance(folders, list)

    def test_toggle_bookmark(self):
        self.browser.navigate("https://example.com")
        result = self.browser.toggle_bookmark()
        self.assertTrue(result)
        result = self.browser.toggle_bookmark()
        self.assertFalse(result)

    def test_history(self):
        self.browser.navigate("https://example.com")
        self.browser.navigate("https://other.com")
        history = self.browser.get_history()
        self.assertEqual(len(history), 2)

    def test_history_search(self):
        self.browser.navigate("https://example.com")
        self.browser.navigate("https://other.com")
        results = self.browser.get_history("example")
        self.assertEqual(len(results), 1)

    def test_clear_history(self):
        self.browser.navigate("https://example.com")
        count = self.browser.clear_history()
        self.assertEqual(count, 1)
        self.assertEqual(len(self.browser.get_history()), 0)

    def test_history_not_private(self):
        tab = self.browser.new_tab(private=True)
        self.browser.switch_tab(1)
        self.browser.navigate("https://secret.com")
        history = self.browser.get_history()
        # Private tabs shouldn't add to history
        self.assertEqual(len(history), 0)

    def test_download(self):
        dl = self.browser.start_download("https://example.com/file.zip", "file.zip", 1000)
        self.assertEqual(dl.status, "downloading")
        self.assertIn(dl, self.browser.downloads)

    def test_update_download(self):
        self.browser.start_download("https://example.com/file.zip", "file.zip", 1000)
        dl = self.browser.update_download("https://example.com/file.zip", 500)
        self.assertEqual(dl.downloaded, 500)
        self.assertEqual(dl.progress, 0.5)

    def test_active_downloads(self):
        self.browser.start_download("https://example.com/a.zip", "a.zip", 1000)
        self.browser.start_download("https://example.com/b.zip", "b.zip", 1000)
        self.browser.update_download("https://example.com/a.zip", 1000, "complete")
        active = self.browser.active_downloads
        self.assertEqual(len(active), 1)

    def test_find_in_page(self):
        self.browser.navigate("nyrqis://newtab")
        self.browser.show_find()
        self.assertTrue(self.browser.current_tab.find_visible)
        count = self.browser.update_find("Nyrqis")
        self.assertGreater(count, 0)
        self.browser.find_next()
        self.browser.find_prev()
        self.browser.hide_find()
        self.assertFalse(self.browser.current_tab.find_visible)

    def test_set_home(self):
        self.browser.set_home("https://example.com")
        self.assertEqual(self.browser.home_url, "https://example.com")

    def test_handle_key_ctrl_t(self):
        result = self.browser.handle_key("Ctrl+t")
        self.assertEqual(result, "new_tab")
        self.assertEqual(self.browser.tab_count, 2)

    def test_handle_key_ctrl_w(self):
        self.browser.new_tab()
        result = self.browser.handle_key("Ctrl+w")
        self.assertEqual(result, "close_tab")

    def test_handle_key_ctrl_l(self):
        result = self.browser.handle_key("Ctrl+l")
        self.assertEqual(result, "url_focus")
        self.assertTrue(self.browser._url_editing)

    def test_handle_key_ctrl_d(self):
        self.browser.navigate("https://example.com")
        result = self.browser.handle_key("Ctrl+d")
        self.assertEqual(result, "bookmark")

    def test_handle_key_f5(self):
        result = self.browser.handle_key("F5")
        self.assertEqual(result, "reload")

    def test_render(self):
        lines = self.browser.render()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_tabs(self):
        self.browser.new_tab()
        tab_bar = self.browser.render_tabs()
        self.assertIsInstance(tab_bar, str)

    def test_render_url_bar(self):
        url_bar = self.browser.render_url_bar()
        self.assertIsInstance(url_bar, str)

    def test_render_status_bar(self):
        status = self.browser.render_status_bar()
        self.assertIsInstance(status, str)

    def test_render_content(self):
        self.browser.navigate("nyrqis://newtab")
        content = self.browser.render_content()
        self.assertIsInstance(content, list)

    def test_tab_display_title(self):
        tab = BrowserTab(url="https://example.com", title="Example")
        self.assertEqual(tab.display_title, "Example")

    def test_tab_favicon(self):
        tab = BrowserTab(url="https://github.com/test")
        self.assertEqual(tab.favicon_char, "⚡")


# ─── Video Player Tests ──────────────────────────────────────────────────


class TestVideoPlayer(unittest.TestCase):

    def setUp(self):
        self.player = VideoPlayer()
        self.sample_video = VideoInfo(
            title="Big Buck Bunny",
            filename="big_buck_bunny.mp4",
            url="https://example.com/video.mp4",
            width=1920, height=1080,
            duration=596.0,
            fps=30.0,
        )

    def test_initial_state(self):
        self.assertFalse(self.player.is_playing)
        self.assertEqual(self.player.position, 0.0)
        self.assertEqual(self.player.volume, 75)
        self.assertFalse(self.player.is_muted)

    def test_play(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        result = self.player.play()
        self.assertTrue(result)
        self.assertTrue(self.player.is_playing)

    def test_pause(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.play()
        result = self.player.pause()
        self.assertTrue(result)
        self.assertFalse(self.player.is_playing)

    def test_toggle_play(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.play()
        self.assertTrue(self.player.is_playing)
        self.player.toggle_play()
        self.assertFalse(self.player.is_playing)

    def test_stop(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.play()
        self.player.stop()
        self.assertFalse(self.player.is_playing)
        self.assertEqual(self.player.position, 0.0)

    def test_seek(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        pos = self.player.seek(100.0)
        self.assertEqual(pos, 100.0)

    def test_seek_clamps(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        pos = self.player.seek(9999.0)
        self.assertEqual(pos, self.sample_video.duration)

    def test_seek_relative(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.seek(100.0)
        pos = self.player.seek_relative(50.0)
        self.assertEqual(pos, 150.0)

    def test_progress(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.seek(298.0)
        self.assertAlmostEqual(self.player.progress, 0.5, places=1)

    def test_speed(self):
        speed = self.player.set_speed(2.0)
        self.assertEqual(speed, 2.0)
        speed = self.player.set_speed(0.1)  # Clamped
        self.assertEqual(speed, 0.25)

    def test_cycle_speed(self):
        self.player.cycle_speed(1)
        self.assertGreater(self.player.playback_speed, 1.0)

    def test_volume(self):
        vol = self.player.set_volume(50)
        self.assertEqual(vol, 50)

    def test_volume_clamps(self):
        self.player.set_volume(150)
        self.assertEqual(self.player.volume, 100)
        self.player.set_volume(-10)
        self.assertEqual(self.player.volume, 0)

    def test_mute(self):
        self.player.toggle_mute()
        self.assertTrue(self.player.is_muted)
        self.assertEqual(self.player.effective_volume, 0)
        self.player.toggle_mute()
        self.assertFalse(self.player.is_muted)

    def test_volume_icon(self):
        self.player.set_volume(80)
        self.assertEqual(self.player.volume_icon, "🔊")
        self.player.set_volume(50)
        self.assertEqual(self.player.volume_icon, "🔉")
        self.player.set_volume(10)
        self.assertEqual(self.player.volume_icon, "🔈")
        self.player.toggle_mute()
        self.assertEqual(self.player.volume_icon, "🔇")

    def test_add_to_playlist(self):
        item = self.player.add_to_playlist(self.sample_video)
        self.assertEqual(self.player.playlist_length, 1)
        self.assertEqual(item.info.title, "Big Buck Bunny")

    def test_remove_from_playlist(self):
        self.player.add_to_playlist(self.sample_video)
        result = self.player.remove_from_playlist(0)
        self.assertTrue(result)
        self.assertEqual(self.player.playlist_length, 0)

    def test_clear_playlist(self):
        self.player.add_to_playlist(self.sample_video)
        count = self.player.clear_playlist()
        self.assertEqual(count, 1)

    def test_move_in_playlist(self):
        v2 = VideoInfo(title="Second", filename="2.mp4")
        self.player.add_to_playlist(self.sample_video)
        self.player.add_to_playlist(v2)
        self.player.move_in_playlist(1, 0)
        self.assertEqual(self.player.playlist[0].info.title, "Second")

    def test_play_from_playlist(self):
        self.player.add_to_playlist(self.sample_video)
        result = self.player.play_from_playlist(0)
        self.assertTrue(result)
        self.assertTrue(self.player.is_playing)

    def test_next(self):
        v1 = VideoInfo(title="First", filename="1.mp4", duration=100)
        v2 = VideoInfo(title="Second", filename="2.mp4", duration=200)
        self.player.add_to_playlist(v1)
        self.player.add_to_playlist(v2)
        self.player.play_from_playlist(0)
        self.player.next()
        self.assertEqual(self.player.current_playlist_index, 1)

    def test_previous(self):
        v1 = VideoInfo(title="First", filename="1.mp4", duration=100)
        v2 = VideoInfo(title="Second", filename="2.mp4", duration=200)
        self.player.add_to_playlist(v1)
        self.player.add_to_playlist(v2)
        self.player.play_from_playlist(1)
        self.player.previous()
        self.assertEqual(self.player.current_playlist_index, 0)

    def test_previous_restart(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.seek(5.0)
        self.player.previous()  # Should restart if <3s
        self.assertEqual(self.player.position, 0.0)

    def test_repeat_mode(self):
        self.player.cycle_repeat()
        self.assertEqual(self.player.repeat_mode, RepeatMode.ALL)
        self.player.cycle_repeat()
        self.assertEqual(self.player.repeat_mode, RepeatMode.ONE)
        self.player.cycle_repeat()
        self.assertEqual(self.player.repeat_mode, RepeatMode.OFF)

    def test_shuffle(self):
        self.player.toggle_shuffle()
        self.assertTrue(self.player.is_shuffled)
        self.player.toggle_shuffle()
        self.assertFalse(self.player.is_shuffled)

    def test_aspect_ratio(self):
        self.player.set_aspect_ratio(AspectRatio.SIXTEEN_NINE)
        self.assertEqual(self.player.aspect_ratio, AspectRatio.SIXTEEN_NINE)
        self.player.cycle_aspect_ratio()
        self.assertNotEqual(self.player.aspect_ratio, AspectRatio.SIXTEEN_NINE)

    def test_fullscreen(self):
        self.player.toggle_fullscreen()
        self.assertTrue(self.player.is_fullscreen)
        self.player.toggle_fullscreen()
        self.assertFalse(self.player.is_fullscreen)

    def test_subtitles(self):
        subs = [
            Subtitle(0.0, 2.0, "Hello world"),
            Subtitle(3.0, 5.0, "Second subtitle"),
        ]
        self.player.load_subtitles(subs)
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.seek(1.0)
        self.player.update(1.0)
        self.assertIsNotNone(self.player.active_subtitle)

    def test_subtitle_toggle(self):
        subs = [Subtitle(0.0, 5.0, "Test")]
        self.player.load_subtitles(subs)
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.seek(1.0)
        self.player.update(1.0)
        self.assertIsNotNone(self.player.active_subtitle)
        self.player.toggle_subtitles()
        self.assertIsNone(self.player.active_subtitle)

    def test_chapters(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        chapters = self.player.chapters
        self.assertGreater(len(chapters), 0)

    def test_go_to_chapter(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        result = self.player.go_to_chapter(0)
        self.assertTrue(result)

    def test_video_info(self):
        info = self.player.get_video_info()
        self.assertIsNone(info)
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        info = self.player.get_video_info()
        self.assertIsNotNone(info)
        self.assertIn("Title", info)

    def test_position_str(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.seek(125.0)
        self.assertEqual(self.player.position_str, "2:05")

    def test_duration_str(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.assertIn(":", self.player.duration_str)

    def test_queue(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.queue_item(0)
        self.assertEqual(len(self.player.queue), 1)

    def test_step_forward(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.play()
        pos = self.player.step_forward()
        self.assertFalse(self.player.is_playing)
        self.assertGreater(pos, 0)

    def test_step_backward(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.seek(5.0)
        pos = self.player.step_backward()
        self.assertLess(pos, 5.0)

    def test_handle_key_space(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        self.player.play()
        self.assertTrue(self.player.is_playing)
        self.player.handle_key(" ")
        self.assertFalse(self.player.is_playing)

    def test_handle_key_m(self):
        self.player.handle_key("m")
        self.assertTrue(self.player.is_muted)

    def test_handle_key_f(self):
        self.player.handle_key("f")
        self.assertTrue(self.player.is_fullscreen)

    def test_handle_key_n(self):
        self.player.add_to_playlist(self.sample_video)
        v2 = VideoInfo(title="Second", filename="2.mp4")
        self.player.add_to_playlist(v2)
        self.player.play_from_playlist(0)
        self.player.handle_key("n")
        self.assertEqual(self.player.current_playlist_index, 1)

    def test_render_player(self):
        lines = self.player.render_player()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_playlist(self):
        self.player.add_to_playlist(self.sample_video)
        lines = self.player.render_playlist()
        self.assertIsInstance(lines, list)
        self.assertIn("Big Buck Bunny", "\n".join(lines))

    def test_render_info(self):
        self.player.add_to_playlist(self.sample_video)
        self.player.play_from_playlist(0)
        lines = self.player.render_info()
        self.assertIsInstance(lines, list)


# ─── Notes App Tests ─────────────────────────────────────────────────────


class TestNotesApp(unittest.TestCase):

    def setUp(self):
        self.notes = NotesApp()

    def test_initial_state(self):
        self.assertGreater(self.notes.total_notes, 0)
        self.assertIsNotNone(self.notes.current_folder)

    def test_create_note(self):
        initial = self.notes.total_notes
        note = self.notes.create_note("Test Note", "Hello world")
        self.assertEqual(note.title, "Test Note")
        self.assertEqual(self.notes.total_notes, initial + 1)

    def test_delete_note(self):
        note = self.notes.create_note("Delete Me")
        result = self.notes.delete_note(note.note_id)
        self.assertTrue(result)
        self.assertEqual(note.folder, "Trash")

    def test_permanently_delete(self):
        note = self.notes.create_note("Delete Me")
        self.notes.delete_note(note.note_id)
        result = self.notes.permanently_delete(note.note_id)
        self.assertTrue(result)

    def test_restore_note(self):
        note = self.notes.create_note("Restore Me")
        self.notes.delete_note(note.note_id)
        result = self.notes.restore_note(note.note_id)
        self.assertTrue(result)
        self.assertEqual(note.folder, "Notes")

    def test_archive_note(self):
        note = self.notes.create_note("Archive Me")
        result = self.notes.archive_note(note.note_id)
        self.assertTrue(result)
        self.assertEqual(note.folder, "Archive")

    def test_unarchive_note(self):
        note = self.notes.create_note("Unarchive Me")
        self.notes.archive_note(note.note_id)
        result = self.notes.unarchive_note(note.note_id)
        self.assertTrue(result)

    def test_get_note(self):
        note = self.notes.create_note("Find Me")
        found = self.notes.get_note(note.note_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.title, "Find Me")

    def test_duplicate_note(self):
        note = self.notes.create_note("Original")
        dup = self.notes.duplicate_note(note.note_id)
        self.assertIsNotNone(dup)
        self.assertIn("copy", dup.title)

    def test_toggle_pin(self):
        note = self.notes.create_note("Pin Me")
        self.notes.toggle_pin(note.note_id)
        self.assertTrue(note.pinned)
        self.notes.toggle_pin(note.note_id)
        self.assertFalse(note.pinned)

    def test_pinned_notes(self):
        note = self.notes.create_note("Pinned")
        self.notes.toggle_pin(note.note_id)
        pinned = self.notes.pinned_notes
        self.assertIn(note, pinned)

    def test_add_tag(self):
        note = self.notes.create_note("Tagged")
        result = self.notes.add_tag(note.note_id, "important")
        self.assertTrue(result)
        self.assertIn("important", note.tags)

    def test_remove_tag(self):
        note = self.notes.create_note("Tagged")
        self.notes.add_tag(note.note_id, "important")
        result = self.notes.remove_tag(note.note_id, "important")
        self.assertTrue(result)
        self.assertNotIn("important", note.tags)

    def test_all_tags(self):
        note = self.notes.create_note("Tagged")
        self.notes.add_tag(note.note_id, "work")
        tags = self.notes.all_tags
        self.assertIn("work", tags)

    def test_create_folder(self):
        folder = self.notes.create_folder("Projects")
        self.assertEqual(folder.name, "Projects")

    def test_delete_folder(self):
        self.notes.create_folder("Temp")
        result = self.notes.delete_folder("Temp")
        self.assertTrue(result)

    def test_cannot_delete_default_folder(self):
        result = self.notes.delete_folder("Notes")
        self.assertFalse(result)

    def test_get_notes_in_folder(self):
        self.notes.create_note("Folder Note", folder="TestFolder")
        notes = self.notes.get_notes_in_folder("TestFolder")
        self.assertEqual(len(notes), 1)

    def test_folder_note_count(self):
        self.notes.create_note("Note 1", folder="Counted")
        self.notes.create_note("Note 2", folder="Counted")
        count = self.notes.folder_note_count("Counted")
        self.assertEqual(count, 2)

    def test_search(self):
        results = self.notes.search("Welcome")
        self.assertGreater(len(results), 0)

    def test_search_by_content(self):
        results = self.notes.search("mushroom")
        self.assertGreater(len(results), 0)

    def test_search_empty(self):
        results = self.notes.search("")
        self.assertEqual(len(results), 0)

    def test_sort_mode(self):
        self.notes.set_sort_mode(SortMode.TITLE)
        self.assertEqual(self.notes.sort_mode, SortMode.TITLE)
        self.notes.cycle_sort_mode()

    def test_filter_tag(self):
        self.notes.set_filter_tag("test")

    def test_filter_color(self):
        self.notes.set_filter_color(NoteColor.GREEN)

    def test_open_note(self):
        note = self.notes.create_note("Open Me")
        result = self.notes.open_note(note.note_id)
        self.assertTrue(result)
        self.assertEqual(self.notes.view_mode, "edit")

    def test_close_editor(self):
        note = self.notes.create_note("Close Me")
        self.notes.open_note(note.note_id)
        result = self.notes.close_editor()
        self.assertTrue(result)
        self.assertEqual(self.notes.view_mode, "list")

    def test_selection(self):
        self.notes.select(0)
        self.assertEqual(self.notes.selected_index, 0)
        self.notes.select_up()
        self.assertEqual(self.notes.selected_index, 0)
        self.notes.select_down()

    def test_export_note(self):
        note = self.notes.create_note("Export Me", content="Hello world")
        exported = self.notes.export_note(note.note_id)
        self.assertIn("Export Me", exported)
        self.assertIn("Hello world", exported)

    def test_export_all(self):
        exported = self.notes.export_all()
        self.assertIsInstance(exported, dict)
        self.assertGreater(len(exported), 0)

    def test_statistics(self):
        self.assertGreater(self.notes.total_notes, 0)
        self.assertGreater(self.notes.total_words, 0)
        self.assertGreater(self.notes.total_characters, 0)

    def test_notes_by_folder(self):
        by_folder = self.notes.notes_by_folder
        self.assertIsInstance(by_folder, dict)

    def test_set_note_color(self):
        note = self.notes.create_note("Color Me")
        result = self.notes.set_note_color(note.note_id, NoteColor.PINK)
        self.assertTrue(result)
        self.assertEqual(note.color, NoteColor.PINK)

    def test_handle_key_new(self):
        result = self.notes.handle_key("Ctrl+n")
        self.assertEqual(result, "new")
        self.assertEqual(self.notes.view_mode, "edit")

    def test_handle_key_escape(self):
        note = self.notes.create_note("Escape Me")
        self.notes.open_note(note.note_id)
        result = self.notes.handle_key("Escape")
        self.assertEqual(result, "close_editor")

    def test_render_list(self):
        lines = self.notes.render_list()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_editor(self):
        note = self.notes.create_note("Render Me")
        self.notes.open_note(note.note_id)
        lines = self.notes.render_editor()
        self.assertIsInstance(lines, list)


class TestNoteEditor(unittest.TestCase):

    def setUp(self):
        self.note = Note(title="Test", content="Hello world")
        self.editor = NoteEditor(self.note)

    def test_insert(self):
        self.editor.insert(" beautiful")
        self.assertEqual(self.note.content, "Hello world beautiful")

    def test_delete_backward(self):
        self.editor.move_to_start()
        self.editor.move_cursor(5)  # cursor at pos 5
        deleted = self.editor.delete_backward(3)
        self.assertEqual(deleted, "llo")
        self.assertEqual(self.note.content, "He world")

    def test_delete_forward(self):
        self.editor.move_to_start()
        deleted = self.editor.delete_forward(5)
        self.assertEqual(deleted, "Hello")

    def test_move_cursor(self):
        self.editor.move_to_start()
        pos = self.editor.move_cursor(5)
        self.assertEqual(pos, 5)

    def test_move_to_start(self):
        pos = self.editor.move_to_start()
        self.assertEqual(pos, 0)

    def test_move_to_end(self):
        pos = self.editor.move_to_end()
        self.assertEqual(pos, len(self.note.content))

    def test_undo(self):
        self.editor.insert(" test")
        result = self.editor.undo()
        self.assertTrue(result)
        self.assertEqual(self.note.content, "Hello world")

    def test_redo(self):
        self.editor.insert(" test")
        self.editor.undo()
        result = self.editor.redo()
        self.assertTrue(result)
        self.assertEqual(self.note.content, "Hello world test")

    def test_is_dirty(self):
        self.editor.insert("x")
        self.assertTrue(self.editor.is_dirty)
        self.editor.mark_clean()
        self.assertFalse(self.editor.is_dirty)

    def test_line_col(self):
        self.editor.insert("line2\nline3")
        line, col = self.editor.line_col
        self.assertGreater(line, 0)


class TestMarkdownRenderer(unittest.TestCase):

    def setUp(self):
        self.md = MarkdownRenderer()

    def test_render_headers(self):
        result = self.md.render("# Title")
        self.assertIn("Title", result)

    def test_render_bold(self):
        result = self.md.render("**bold**")
        self.assertIn("**bold**", result)

    def test_render_list(self):
        result = self.md.render("- item")
        self.assertIn("• item", result)

    def test_render_code_block(self):
        result = self.md.render("```python\ncode\n```")
        self.assertIn("code", result)

    def test_strip_markdown(self):
        result = self.md.strip_markdown("# Hello **world**")
        self.assertNotIn("#", result)
        self.assertNotIn("**", result)
        self.assertIn("Hello world", result)

    def test_word_count(self):
        count = self.md.word_count("Hello beautiful world")
        self.assertEqual(count, 3)

    def test_char_count(self):
        count = self.md.char_count("Hello")
        self.assertEqual(count, 5)

    def test_line_count(self):
        count = self.md.line_count("line1\nline2\nline3")
        self.assertEqual(count, 3)


class TestVideoInfo(unittest.TestCase):

    def test_resolution(self):
        info = VideoInfo(title="T", filename="f.mp4", width=1920, height=1080)
        self.assertEqual(info.resolution_str, "1920x1080")

    def test_duration(self):
        info = VideoInfo(title="T", filename="f.mp4", duration=3661.0)
        self.assertEqual(info.duration_str, "1:01:01")

    def test_size(self):
        info = VideoInfo(title="T", filename="f.mp4", file_size=1073741824)
        self.assertIn("GB", info.size_str)

    def test_bitrate(self):
        info = VideoInfo(title="T", filename="f.mp4", bitrate=50000)
        self.assertIn("Mbps", info.bitrate_str)


class TestBookmark(unittest.TestCase):

    def test_display(self):
        bm = Bookmark(title="Example", url="https://example.com")
        self.assertEqual(bm.display, "Example")

    def test_display_empty_title(self):
        bm = Bookmark(title="", url="https://example.com")
        self.assertEqual(bm.display, "https://example.com")


class TestDownload(unittest.TestCase):

    def test_progress(self):
        dl = Download(url="u", filename="f", size=1000, downloaded=500)
        self.assertEqual(dl.progress, 0.5)

    def test_progress_zero_size(self):
        dl = Download(url="u", filename="f", size=0)
        self.assertEqual(dl.progress, 0.0)

    def test_speed(self):
        dl = Download(url="u", filename="f", downloaded=2048)
        dl.started = time.time() - 1
        speed = dl.speed_str
        self.assertIn("KB/s", speed)


class TestHistoryEntry(unittest.TestCase):

    def test_time_ago_just_now(self):
        entry = HistoryEntry(url="u", title="t", timestamp=time.time())
        self.assertEqual(entry.time_ago, "just now")

    def test_time_ago_minutes(self):
        entry = HistoryEntry(url="u", title="t", timestamp=time.time() - 120)
        self.assertIn("m ago", entry.time_ago)

    def test_time_ago_hours(self):
        entry = HistoryEntry(url="u", title="t", timestamp=time.time() - 7200)
        self.assertIn("h ago", entry.time_ago)

    def test_time_ago_days(self):
        entry = HistoryEntry(url="u", title="t", timestamp=time.time() - 172800)
        self.assertIn("d ago", entry.time_ago)


class TestPlaylistItem(unittest.TestCase):

    def test_display_title(self):
        info = VideoInfo(title="Test Video", filename="test.mp4")
        item = PlaylistItem(info=info)
        self.assertEqual(item.display_title, "Test Video")

    def test_is_watched(self):
        info = VideoInfo(title="T", filename="f.mp4")
        item = PlaylistItem(info=info, progress=1.0)
        self.assertTrue(item.is_watched)

    def test_not_watched(self):
        info = VideoInfo(title="T", filename="f.mp4")
        item = PlaylistItem(info=info, progress=0.5)
        self.assertFalse(item.is_watched)


class TestChapter(unittest.TestCase):

    def test_start_str(self):
        ch = Chapter(title="Ch1", start_time=65.0, end_time=130.0)
        self.assertEqual(ch.start_str, "1:05")

    def test_duration_str(self):
        ch = Chapter(title="Ch1", start_time=0.0, end_time=3661.0)
        self.assertEqual(ch.duration_str, "1:01:01")


class TestSubtitle(unittest.TestCase):

    def test_subtitle(self):
        sub = Subtitle(start_time=0.0, end_time=2.0, text="Hello")
        self.assertEqual(sub.text, "Hello")
        self.assertEqual(sub.start_time, 0.0)


class TestNote(unittest.TestCase):

    def test_word_count(self):
        note = Note(title="T", content="Hello world foo bar")
        self.assertEqual(note.word_count, 4)

    def test_preview(self):
        note = Note(title="T", content="# Title\n\nSome content here")
        preview = note.preview
        self.assertIsInstance(preview, str)

    def test_time_ago(self):
        note = Note(title="T", modified=time.time() - 120)
        self.assertIn("m ago", note.time_ago)

    def test_note_id(self):
        note = Note(title="T")
        self.assertIsNotNone(note.note_id)
        self.assertEqual(len(note.note_id), 8)


if __name__ == "__main__":
    unittest.main()
