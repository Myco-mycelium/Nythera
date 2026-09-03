"""Tests for ImageEditor, CalendarApp, PasswordGenerator"""
import time
import unittest

from ui.image_editor import (
    ImageEditor, ImageProject, Layer, EditHistory, EditTool, FilterType,
    ExportFormat, BlendMode, ResizeMethod, Rect, Point, FilterPreset
)
from ui.calendar_app import (
    CalendarApp, CalendarEvent, ReminderEntry, ViewMode, Recurrence,
    EventColor, Reminder, EventStatus
)
from ui.password_generator import (
    PasswordGenerator, PasswordEntry, PasswordType, CharPool,
    StrengthLevel, StorageLocation
)


class TestImageEditor(unittest.TestCase):
    def setUp(self):
        self.ie = ImageEditor()

    def test_initial_state(self):
        self.assertGreater(len(self.ie._projects), 0)
        self.assertEqual(self.ie._selected_project, 0)

    def test_select_project(self):
        self.ie.select_project(1)
        self.assertEqual(self.ie._selected_project, 1)

    def test_select_invalid(self):
        self.ie.select_project(99)
        self.assertEqual(self.ie._selected_project, 0)

    def test_project_dimensions(self):
        proj = self.ie.selected_project
        self.assertIn("×", proj.dimensions)

    def test_project_megapixels(self):
        proj = self.ie.selected_project
        self.assertGreater(proj.megapixels, 0)

    def test_layers(self):
        proj = self.ie.selected_project
        self.assertGreater(len(proj.layers), 0)

    def test_select_layer(self):
        self.ie.select_layer(1)
        self.assertIsNotNone(self.ie.selected_layer)

    def test_add_layer(self):
        proj = self.ie.selected_project
        before = len(proj.layers)
        self.ie.add_layer("Test Layer")
        self.assertEqual(len(proj.layers), before + 1)

    def test_remove_layer(self):
        proj = self.ie.selected_project
        before = len(proj.layers)
        self.ie.remove_layer(1)
        self.assertEqual(len(proj.layers), before - 1)

    def test_remove_first_layer_fails(self):
        self.assertFalse(self.ie.remove_layer(0))

    def test_toggle_visibility(self):
        self.ie.select_layer(1)
        result = self.ie.toggle_layer_visibility(1)
        self.assertIsInstance(result, bool)

    def test_set_tool(self):
        self.ie.set_tool(EditTool.CROP)
        self.assertEqual(self.ie._current_tool, EditTool.CROP)

    def test_set_brush_size(self):
        self.ie.set_brush_size(50)
        self.assertEqual(self.ie._brush_size, 50)

    def test_set_brush_color(self):
        self.ie.set_brush_color("#FF0000")
        self.assertEqual(self.ie._brush_color, "#FF0000")

    def test_set_zoom(self):
        self.ie.set_zoom(2.0)
        self.assertEqual(self.ie._zoom, 2.0)

    def test_undo_redo(self):
        self.assertTrue(self.ie.undo())
        self.assertTrue(self.ie.redo())

    def test_crop(self):
        self.ie.crop(0, 0, 1920, 1080)
        proj = self.ie.selected_project
        self.assertEqual(proj.width, 1920)

    def test_resize(self):
        self.ie.resize(100, 100)
        proj = self.ie.selected_project
        self.assertEqual(proj.width, 100)

    def test_export(self):
        result = self.ie.export(ExportFormat.PNG, 90)
        self.assertEqual(result["format"], "png")

    def test_filter_presets(self):
        self.assertGreater(len(self.ie._filter_presets), 0)

    def test_render(self):
        lines = self.ie.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("EDITOR" in l for l in lines))

    def test_render_canvas(self):
        lines = self.ie.render_canvas()
        self.assertGreater(len(lines), 2)

    def test_render_filters(self):
        lines = self.ie.render_filters()
        self.assertGreater(len(lines), 5)

    def test_render_export(self):
        lines = self.ie.render_export()
        self.assertGreater(len(lines), 3)

    def test_layer_id(self):
        layer = Layer("Test")
        self.assertEqual(len(layer.layer_id), 6)

    def test_rect(self):
        r = Rect(0, 0, 100, 50)
        self.assertEqual(r.area, 5000)


class TestCalendarApp(unittest.TestCase):
    def setUp(self):
        self.cal = CalendarApp()

    def test_initial_state(self):
        self.assertGreater(len(self.cal._events), 0)
        self.assertEqual(self.cal._view_mode, ViewMode.MONTH)

    def test_select_event(self):
        self.cal.select_event(1)
        self.assertIsNotNone(self.cal.selected_event)

    def test_total_events(self):
        self.assertGreater(self.cal.total_events, 0)

    def test_events_this_month(self):
        self.assertGreater(self.cal.events_this_month, 0)

    def test_busy_days(self):
        self.assertGreater(len(self.cal.busy_days_this_month), 0)

    def test_month_name(self):
        self.assertEqual(self.cal.month_name, "September")

    def test_add_event(self):
        before = self.cal.total_events
        e = CalendarEvent("Test", 10, 0, 60, 15, 9, 2026)
        self.cal.add_event(e)
        self.assertEqual(self.cal.total_events, before + 1)

    def test_delete_event(self):
        before = self.cal.total_events
        self.cal.delete_event(0)
        self.assertEqual(self.cal.total_events, before - 1)

    def test_delete_invalid(self):
        self.assertFalse(self.cal.delete_event(99))

    def test_search(self):
        results = self.cal.search("Sprint")
        self.assertGreater(len(results), 0)

    def test_get_day_events(self):
        events = self.cal.get_day_events(3)
        self.assertGreater(len(events), 0)

    def test_set_view(self):
        self.cal.set_view(ViewMode.WEEK)
        self.assertEqual(self.cal._view_mode, ViewMode.WEEK)

    def test_toggle_calendar(self):
        self.cal.toggle_calendar("Work")
        self.assertFalse(self.cal._calendars["Work"])

    def test_active_calendars(self):
        self.assertGreater(len(self.cal.active_calendars), 0)

    def test_upcoming_events(self):
        upcoming = self.cal.upcoming_events
        self.assertGreater(len(upcoming), 0)

    def test_reminders(self):
        self.assertGreater(len(self.cal._reminders), 0)

    def test_event_time_display(self):
        e = CalendarEvent("Test", 14, 30, 60, 1, 1, 2026)
        self.assertEqual(e.time_display, "14:30")

    def test_event_end_time(self):
        e = CalendarEvent("Test", 14, 30, 90, 1, 1, 2026)
        self.assertEqual(e.end_time_display, "16:00")

    def test_event_all_day(self):
        e = CalendarEvent("Holiday", 0, 0, 1440, 1, 1, 2026, all_day=True)
        self.assertEqual(e.time_display, "All Day")

    def test_render(self):
        lines = self.cal.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("CALENDAR" in l for l in lines))

    def test_render_event_detail(self):
        self.cal.select_event(0)
        lines = self.cal.render_event_detail()
        self.assertGreater(len(lines), 3)

    def test_render_calendars(self):
        lines = self.cal.render_calendars()
        self.assertGreater(len(lines), 3)


class TestPasswordGenerator(unittest.TestCase):
    def setUp(self):
        self.pg = PasswordGenerator()

    def test_initial_state(self):
        self.assertGreater(len(self.pg._entries), 0)
        self.assertEqual(self.pg._selected, 0)

    def test_select(self):
        self.pg.select(1)
        self.assertIsNotNone(self.pg.selected_entry)

    def test_select_invalid(self):
        self.pg.select(99)
        self.assertEqual(self.pg._selected, 0)

    def test_total_entries(self):
        self.assertGreater(self.pg.total_entries, 0)

    def test_expired_count(self):
        self.assertGreater(self.pg.expired_count, 0)

    def test_favorite_count(self):
        self.assertGreater(self.pg.favorite_count, 0)

    def test_avg_strength(self):
        self.assertGreater(self.pg.avg_strength, 0)

    def test_strength_distribution(self):
        dist = self.pg.strength_distribution
        self.assertGreater(len(dist), 0)

    def test_generate_random(self):
        self.pg._password_type = PasswordType.RANDOM
        result = self.pg.generate()
        self.assertEqual(len(result), 20)

    def test_generate_passphrase(self):
        self.pg._password_type = PasswordType.PASSPHRASE
        result = self.pg.generate()
        self.assertIn("-", result)

    def test_generate_pin(self):
        self.pg._password_type = PasswordType.PIN
        result = self.pg.generate()
        self.assertTrue(result.isdigit())

    def test_generate_memorable(self):
        self.pg._password_type = PasswordType.MEMORABLE
        result = self.pg.generate()
        self.assertEqual(len(result), 20)

    def test_calculate_entropy(self):
        entropy = PasswordGenerator.calculate_entropy("Abc123!@#")
        self.assertGreater(entropy, 0)

    def test_calculate_strength(self):
        self.assertEqual(PasswordGenerator.calculate_strength(10), StrengthLevel.VERY_WEAK)
        self.assertEqual(PasswordGenerator.calculate_strength(50), StrengthLevel.FAIR)
        self.assertEqual(PasswordGenerator.calculate_strength(90), StrengthLevel.VERY_STRONG)
        self.assertEqual(PasswordGenerator.calculate_strength(150), StrengthLevel.EXCELLENT)

    def test_add_entry(self):
        before = self.pg.total_entries
        entry = PasswordEntry("Test", "user", "pass")
        self.pg.add_entry(entry)
        self.assertEqual(self.pg.total_entries, before + 1)

    def test_delete_entry(self):
        before = self.pg.total_entries
        self.pg.delete_entry(0)
        self.assertEqual(self.pg.total_entries, before - 1)

    def test_delete_invalid(self):
        self.assertFalse(self.pg.delete_entry(99))

    def test_toggle_favorite(self):
        self.pg.select(3)
        before = self.pg.selected_entry.is_favorite
        self.pg.toggle_favorite()
        self.assertNotEqual(self.pg.selected_entry.is_favorite, before)

    def test_search(self):
        results = self.pg.search("GitHub")
        self.assertGreater(len(results), 0)

    def test_masked_password(self):
        entry = PasswordEntry("Test", "user", "MySecretPass123")
        self.assertEqual(entry.masked_password, "•" * 15)

    def test_strength_bar(self):
        entry = PasswordEntry("Test", "user", "pass", strength=StrengthLevel.EXCELLENT)
        self.assertEqual(entry.strength_bar, "▓▓▓▓▓▓▓▓▓▓")

    def test_render(self):
        lines = self.pg.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("PASSWORD" in l for l in lines))

    def test_render_generator(self):
        self.pg.generate()
        lines = self.pg.render_generator()
        self.assertGreater(len(lines), 5)

    def test_render_entry_detail(self):
        self.pg.select(0)
        lines = self.pg.render_entry_detail()
        self.assertGreater(len(lines), 5)

    def test_entry_age(self):
        entry = PasswordEntry("Test", "user", "pass", created_at=time.time() - 86400 * 10, last_modified=time.time() - 86400 * 10)
        self.assertEqual(entry.age_days, 10)


if __name__ == "__main__":
    unittest.main()
