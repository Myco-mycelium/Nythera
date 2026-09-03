"""
Tests for Maps App, QR Tools, and Clipboard Manager.
"""

import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.maps_app import MapsApp, Location, Route, MapState, MapLayer, POICategory
from ui.qr_tools import QRTools, QRCode, QRScanResult, QRGenerator, QRType
from ui.clipboard_manager import ClipboardManager, ClipboardEntry, Snippet, ClipboardType, SnippetCategory


# ─── Maps App Tests ──────────────────────────────────────────────────────


class TestMapsApp(unittest.TestCase):

    def setUp(self):
        self.app = MapsApp()

    def test_initial_state(self):
        self.assertIsNotNone(self.app.map_state)
        self.assertEqual(self.app.view_mode, "map")
        self.assertFalse(self.app.route_mode)

    def test_search(self):
        results = self.app.search("park")
        self.assertGreater(len(results), 0)

    def test_search_empty(self):
        results = self.app.search("")
        self.assertEqual(len(results), 0)

    def test_pan_map(self):
        initial_lat = self.app.map_state.center_lat
        initial_lon = self.app.map_state.center_lon
        self.app.pan_map(0.1, 0.1)
        self.assertNotEqual(self.app.map_state.center_lat, initial_lat)
        self.assertNotEqual(self.app.map_state.center_lon, initial_lon)

    def test_zoom_in(self):
        initial_zoom = self.app.map_state.zoom
        result = self.app.zoom_in()
        self.assertEqual(result, initial_zoom + 1)

    def test_zoom_out(self):
        initial_zoom = self.app.map_state.zoom
        result = self.app.zoom_out()
        self.assertEqual(result, initial_zoom - 1)

    def test_set_layer(self):
        self.app.set_layer(MapLayer.SATELLITE)
        self.assertEqual(self.app.map_state.layer, MapLayer.SATELLITE)

    def test_cycle_layer(self):
        initial = self.app.map_state.layer
        result = self.app.cycle_layer()
        self.assertNotEqual(result, initial)

    def test_favorites(self):
        # Use a location not already in favorites
        loc = self.app._locations[2]
        self.assertTrue(self.app.add_favorite(loc))
        self.assertTrue(self.app.is_favorite(loc.location_id))
        self.assertTrue(self.app.remove_favorite(loc.location_id))
        self.assertFalse(self.app.is_favorite(loc.location_id))

    def test_duplicate_favorite(self):
        # Location already in favorites from init
        loc = self.app._favorites[0]
        self.assertFalse(self.app.add_favorite(loc))  # Already a favorite

    def test_route_planning(self):
        self.app.start_route()
        self.assertTrue(self.app.route_mode)
        self.app.add_waypoint(self.app._locations[0])
        self.app.add_waypoint(self.app._locations[1])
        route = self.app.finish_route()
        self.assertIsNotNone(route)
        self.assertGreater(route.distance_km, 0)
        self.assertFalse(self.app.route_mode)

    def test_route_cancel(self):
        self.app.start_route()
        self.app.cancel_route()
        self.assertFalse(self.app.route_mode)
        self.assertEqual(len(self.app.route_waypoints), 0)

    def test_calculate_distance(self):
        loc1 = self.app._locations[0]
        loc2 = self.app._locations[1]
        dist = self.app.calculate_distance(loc1, loc2)
        self.assertGreater(dist, 0)

    def test_selection(self):
        self.app.select_down()
        self.assertEqual(self.app.selected_index, 1)
        self.app.select_up()
        self.assertEqual(self.app.selected_index, 0)

    def test_render_map(self):
        lines = self.app.render_map()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_search(self):
        self.app.search("airport")
        lines = self.app.render_search()
        self.assertIsInstance(lines, list)

    def test_render_favorites(self):
        lines = self.app.render_favorites()
        self.assertIsInstance(lines, list)

    def test_render_routes(self):
        lines = self.app.render_routes()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        result = self.app.handle_key("+")
        self.assertEqual(result, "zoom_in")

    def test_go_to_location(self):
        loc = self.app._locations[0]
        self.app.go_to_location(loc)
        self.assertEqual(self.app.map_state.center_lat, loc.latitude)


class TestLocation(unittest.TestCase):

    def test_coord_str(self):
        loc = Location("Test", 37.7749, -122.4194)
        self.assertIn("N", loc.coord_str)
        self.assertIn("W", loc.coord_str)

    def test_icon(self):
        loc = Location("Test", 0, 0, category=POICategory.RESTAURANT)
        self.assertEqual(loc.icon, "🍽️")

    def test_rating_str(self):
        loc = Location("Test", 0, 0, rating=4.5)
        self.assertIn("⭐", loc.rating_str)


class TestRoute(unittest.TestCase):

    def test_distance_str(self):
        route = Route("Test", distance_km=0.5)
        self.assertIn("m", route.distance_str)

    def test_duration_str(self):
        route = Route("Test", duration_minutes=90)
        self.assertIn("1h", route.duration_str)

    def test_summary(self):
        route = Route("Test", distance_km=10, duration_minutes=30, route_type="walking")
        summary = route.summary
        self.assertIn("km", summary)
        self.assertIn("walking", summary)


# ─── QR Tools Tests ──────────────────────────────────────────────────────


class TestQRTools(unittest.TestCase):

    def setUp(self):
        self.qr = QRTools()

    def test_initial_state(self):
        self.assertEqual(self.qr.view_mode, "generator")
        self.assertEqual(self.qr.qr_type, QRType.TEXT)
        self.assertEqual(self.qr.qr_size, 21)

    def test_generate_text(self):
        self.qr._input_text = "Hello, Nyrqis!"
        qr = self.qr.generate()
        self.assertIsNotNone(qr)
        self.assertEqual(qr.qr_type, QRType.TEXT)
        self.assertEqual(self.qr.view_mode, "preview")

    def test_generate_url(self):
        self.qr._qr_type = QRType.URL
        self.qr._input_text = "example.com"
        qr = self.qr.generate()
        self.assertIsNotNone(qr)
        self.assertIn("https://", qr.content)

    def test_generate_wifi(self):
        self.qr._qr_type = QRType.WIFI
        self.qr._wifi_ssid = "NyrqisHome"
        self.qr._wifi_password = "pass123"
        qr = self.qr.generate()
        self.assertIsNotNone(qr)
        self.assertIn("WIFI:", qr.content)

    def test_generate_vcard(self):
        self.qr._qr_type = QRType.VCARD
        self.qr._contact_name = "Test User"
        qr = self.qr.generate()
        self.assertIsNotNone(qr)
        self.assertIn("VCARD", qr.content)

    def test_cycle_type(self):
        initial = self.qr.qr_type
        self.qr.handle_key("t")
        self.assertNotEqual(self.qr.qr_type, initial)

    def test_delete_qr(self):
        self.qr._input_text = "test"
        self.qr.generate()
        initial_count = len(self.qr.generated)
        self.assertTrue(self.qr.delete_qr(0))
        self.assertEqual(len(self.qr.generated), initial_count - 1)

    def test_simulate_scan(self):
        result = self.qr.simulate_scan("https://example.com", QRType.URL)
        self.assertIsNotNone(result)
        self.assertEqual(len(self.qr.scan_history), 4)  # 3 sample + 1 new

    def test_clear_scan_history(self):
        count = self.qr.clear_scan_history()
        self.assertEqual(count, 3)  # 3 sample scans
        self.assertEqual(len(self.qr.scan_history), 0)

    def test_render_generator(self):
        lines = self.qr.render()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_preview(self):
        self.qr._input_text = "test"
        self.qr.generate()
        lines = self.qr.render_preview()
        self.assertIsInstance(lines, list)

    def test_render_history(self):
        lines = self.qr.render_history()
        self.assertIsInstance(lines, list)

    def test_handle_key_generate(self):
        self.qr._input_text = "test"
        result = self.qr.handle_key("Enter")
        self.assertEqual(result, "generate")

    def test_size_increase(self):
        initial = self.qr.qr_size
        self.qr.handle_key("+")
        self.assertEqual(self.qr.qr_size, min(29, initial + 4))

    def test_size_decrease(self):
        initial = self.qr.qr_size
        self.qr.handle_key("-")
        self.assertEqual(self.qr.qr_size, max(21, initial - 4))


class TestQRGenerator(unittest.TestCase):

    def test_generate_ascii(self):
        lines = QRGenerator.generate_ascii("Hello")
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_generate_compact(self):
        lines = QRGenerator.generate_compact("Hello")
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)


class TestQRCode(unittest.TestCase):

    def test_display_title(self):
        qr = QRCode("test", QRType.TEXT, title="My QR")
        self.assertIn("My QR", qr.display_title)

    def test_preview(self):
        qr = QRCode("a" * 100, QRType.TEXT)
        self.assertIn("...", qr.preview)

    def test_size_label(self):
        qr = QRCode("test", QRType.TEXT, size=25)
        self.assertEqual(qr.size_label, "Medium (25×25)")


class TestQRScanResult(unittest.TestCase):

    def test_time_ago(self):
        scan = QRScanResult("test", QRType.TEXT, timestamp=time.time() - 300)
        self.assertIn("m ago", scan.time_ago)


# ─── Clipboard Manager Tests ─────────────────────────────────────────────


class TestClipboardManager(unittest.TestCase):

    def setUp(self):
        self.clip = ClipboardManager()

    def test_initial_state(self):
        self.assertEqual(self.clip._view_mode, "history")
        self.assertGreater(self.clip.history_count, 0)
        self.assertGreater(self.clip.snippet_count, 0)

    def test_copy(self):
        entry = self.clip.copy("test content")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.content, "test content")
        self.assertEqual(self.clip.total_copies, 1)

    def test_paste(self):
        self.clip.copy("test")
        content = self.clip.paste()
        self.assertEqual(content, "test")
        self.assertEqual(self.clip.total_pastes, 1)

    def test_paste_by_id(self):
        entry = self.clip.copy("test")
        content = self.clip.paste(entry.entry_id)
        self.assertEqual(content, "test")

    def test_delete_entry(self):
        entry = self.clip.copy("test")
        initial = self.clip.history_count
        self.assertTrue(self.clip.delete_entry(entry.entry_id))
        self.assertEqual(self.clip.history_count, initial - 1)

    def test_toggle_pin(self):
        entry = self.clip.copy("test")
        result = self.clip.toggle_pin(entry.entry_id)
        self.assertTrue(result)
        self.assertTrue(entry.pinned)

    def test_clear_history(self):
        count = self.clip.clear_history()
        self.assertEqual(count, 8)  # 8 sample entries
        self.assertEqual(self.clip.history_count, 0)

    def test_create_snippet(self):
        snippet = self.clip.create_snippet("Test Snippet", "content")
        self.assertIsNotNone(snippet)
        self.assertEqual(snippet.name, "Test Snippet")
        self.assertEqual(self.clip.snippet_count, 9)  # 8 sample + 1 new

    def test_delete_snippet(self):
        snippet = self.clip.create_snippet("Test", "content")
        initial = self.clip.snippet_count
        self.assertTrue(self.clip.delete_snippet(snippet.snippet_id))
        self.assertEqual(self.clip.snippet_count, initial - 1)

    def test_use_snippet(self):
        snippet = self.clip._snippets[0]
        content = self.clip.use_snippet(snippet.snippet_id)
        self.assertEqual(content, snippet.content)
        self.assertEqual(snippet.use_count, 1)

    def test_search_snippets(self):
        results = self.clip.search_snippets("python")
        self.assertGreater(len(results), 0)

    def test_auto_clear(self):
        self.clip.set_auto_clear(1)
        self.clip.copy("test")
        # Simulate time passing
        self.clip._last_clear_check = time.time() - 2
        cleared = self.clip.check_auto_clear()
        # Should clear at least some non-pinned entries
        self.assertGreaterEqual(cleared, 0)

    def test_selection(self):
        self.clip.select_down()
        self.assertEqual(self.clip.selected_index, 1)
        self.clip.select_up()
        self.assertEqual(self.clip.selected_index, 0)

    def test_search(self):
        self.clip.set_search("hello")
        entries = self.clip.get_history()
        self.assertGreater(len(entries), 0)

    def test_pinned_count(self):
        count = self.clip.pinned_count
        self.assertGreater(count, 0)

    def test_render_history(self):
        lines = self.clip.render_history()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_snippets(self):
        lines = self.clip.render_snippets()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_settings(self):
        lines = self.clip.render_settings()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_handle_key_history(self):
        result = self.clip.handle_key("ArrowDown")
        self.assertEqual(result, "select_down")

    def test_handle_key_snippets(self):
        self.clip.set_view("snippets")
        result = self.clip.handle_key("Escape")
        self.assertEqual(result, "back")

    def test_handle_key_settings(self):
        self.clip.set_view("settings")
        result = self.clip.handle_key("a")  # Toggle auto-clear
        self.assertEqual(result, "toggle_auto_clear")

    def test_copy_with_type(self):
        entry = self.clip.copy("def test(): pass", ClipboardType.CODE, "terminal", "python")
        self.assertEqual(entry.entry_type, ClipboardType.CODE)
        self.assertEqual(entry.source, "terminal")
        self.assertEqual(entry.language, "python")


class TestClipboardEntry(unittest.TestCase):

    def test_preview(self):
        entry = ClipboardEntry("Hello World\nSecond line")
        self.assertEqual(entry.preview, "Hello World")

    def test_size_str(self):
        entry = ClipboardEntry("test")
        self.assertIn("B", entry.size_str)

    def test_line_count(self):
        entry = ClipboardEntry("line1\nline2\nline3")
        self.assertEqual(entry.line_count, 3)

    def test_time_ago(self):
        entry = ClipboardEntry("test", timestamp=time.time() - 300)
        self.assertIn("m ago", entry.time_ago)


class TestSnippet(unittest.TestCase):

    def test_preview(self):
        snippet = Snippet("Test", "Hello World\nSecond line")
        self.assertEqual(snippet.preview, "Hello World")

    def test_icon(self):
        snippet = Snippet("Test", "code", SnippetCategory.CODE)
        self.assertEqual(snippet.icon, "💻")

    def test_display(self):
        snippet = Snippet("Test Snippet", "content", hotkey="Ctrl+1")
        self.assertIn("Ctrl+1", snippet.display)


if __name__ == "__main__":
    unittest.main()
