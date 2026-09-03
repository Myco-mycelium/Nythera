"""
Tests for Process Manager, Weather Widget, and Disk Analyzer.
"""

import unittest
import time
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.process_manager import (
    ProcessManager, ProcessInfo, SystemResources,
    ProcessStatus, SortField, ProcessGroup
)
from ui.weather_widget import (
    WeatherWidget, CurrentWeather, DailyForecast, HourlyForecast,
    WeatherAlert, WeatherLocation, WeatherCondition, AlertSeverity
)
from ui.disk_analyzer import (
    DiskAnalyzer, DiskEntry, CleanupSuggestion, FileType
)


# ─── Process Manager Tests ───────────────────────────────────────────────


class TestProcessManager(unittest.TestCase):

    def setUp(self):
        self.pm = ProcessManager()

    def test_initial_state(self):
        self.assertGreater(self.pm.process_count, 0)
        self.assertEqual(self.pm.view_mode, "list")

    def test_get_processes(self):
        procs = self.pm.get_processes()
        self.assertGreater(len(procs), 0)

    def test_get_process(self):
        p = self.pm.get_process(100)
        self.assertIsNotNone(p)
        self.assertEqual(p.pid, 100)

    def test_kill_process(self):
        initial = self.pm.process_count
        result = self.pm.kill_process(100)
        self.assertTrue(result)
        self.assertEqual(self.pm.process_count, initial - 1)

    def test_confirm_kill(self):
        p = self.pm.confirm_kill(100)
        self.assertIsNotNone(p)
        self.assertEqual(self.pm.confirm_kill_target.pid, 100)

    def test_execute_kill(self):
        self.pm.confirm_kill(100)
        result = self.pm.execute_kill()
        self.assertTrue(result)
        self.assertIsNone(self.pm.confirm_kill_target)

    def test_cancel_kill(self):
        self.pm.confirm_kill(100)
        self.pm.cancel_kill()
        self.assertIsNone(self.pm.confirm_kill_target)

    def test_set_nice(self):
        result = self.pm.set_nice(100, -5)
        self.assertTrue(result)
        p = self.pm.get_process(100)
        self.assertEqual(p.nice, -5)

    def test_sort(self):
        self.pm.set_sort(SortField.MEMORY)
        procs = self.pm.get_processes()
        # Should be sorted by memory descending
        for i in range(len(procs) - 1):
            self.assertGreaterEqual(procs[i].memory_percent, procs[i + 1].memory_percent)

    def test_filter(self):
        self.pm.set_filter("firefox")
        procs = self.pm.get_processes()
        for p in procs:
            self.assertIn("firefox", p.name.lower())

    def test_group_user(self):
        self.pm.set_group(ProcessGroup.USER)
        procs = self.pm.get_processes()
        for p in procs:
            self.assertEqual(p.user, "user")

    def test_group_system(self):
        self.pm.set_group(ProcessGroup.SYSTEM)
        procs = self.pm.get_processes()
        for p in procs:
            self.assertEqual(p.user, "root")

    def test_selection(self):
        self.pm.select(0)
        self.assertEqual(self.pm.selected_index, 0)
        self.pm.select_up()
        self.assertEqual(self.pm.selected_index, 0)
        self.pm.select_down()

    def test_open_detail(self):
        p = self.pm.open_detail(100)
        self.assertIsNotNone(p)
        self.assertEqual(self.pm.view_mode, "detail")

    def test_close_detail(self):
        self.pm.open_detail(100)
        self.pm.close_detail()
        self.assertEqual(self.pm.view_mode, "list")

    def test_sparkline(self):
        spark = ProcessManager.sparkline([1, 2, 3, 4, 5], width=5)
        self.assertIsInstance(spark, str)
        self.assertEqual(len(spark), 5)

    def test_update_processes(self):
        self.pm.update_processes()
        # Should not crash

    def test_render_summary(self):
        lines = self.pm.render_summary()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_list(self):
        lines = self.pm.render_list()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_detail(self):
        self.pm.open_detail(100)
        lines = self.pm.render_detail()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render(self):
        lines = self.pm.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_list(self):
        self.pm.handle_key("ArrowDown")
        self.pm.handle_key("ArrowUp")
        self.pm.handle_key("s")
        self.pm.handle_key("Enter")
        self.assertEqual(self.pm.view_mode, "detail")

    def test_handle_key_detail(self):
        self.pm.open_detail(100)
        self.pm.handle_key("Escape")
        self.assertEqual(self.pm.view_mode, "list")


class TestProcessInfo(unittest.TestCase):

    def test_memory_str(self):
        p = ProcessInfo(pid=1, name="test", memory_mb=512)
        self.assertEqual(p.memory_str, "512 MB")

    def test_memory_str_gb(self):
        p = ProcessInfo(pid=1, name="test", memory_mb=2048)
        self.assertEqual(p.memory_str, "2.0 GB")

    def test_status_icon(self):
        p = ProcessInfo(pid=1, name="test", status=ProcessStatus.RUNNING)
        self.assertEqual(p.status_icon, "🟢")

    def test_nice_str(self):
        p = ProcessInfo(pid=1, name="test", nice=-5)
        self.assertEqual(p.nice_str, "HIGH (-5)")

    def test_uptime_str(self):
        p = ProcessInfo(pid=1, name="test", start_time=time.time() - 3600)
        self.assertIn("h", p.uptime_str)

    def test_update_history(self):
        p = ProcessInfo(pid=1, name="test", cpu_percent=50.0)
        p.update_history()
        self.assertEqual(len(p.cpu_history), 1)


class TestSystemResources(unittest.TestCase):

    def test_memory_percent(self):
        r = SystemResources(total_memory_mb=16384, used_memory_mb=8192)
        self.assertEqual(r.memory_percent, 50.0)

    def test_disk_percent(self):
        r = SystemResources(total_disk_gb=500, used_disk_gb=250)
        self.assertEqual(r.disk_percent, 50.0)

    def test_uptime_str(self):
        r = SystemResources(uptime_seconds=90000)
        self.assertIn("d", r.uptime_str)

    def test_memory_str(self):
        r = SystemResources(total_memory_mb=16384, used_memory_mb=8192)
        self.assertIn("GB", r.memory_str)


# ─── Weather Widget Tests ────────────────────────────────────────────────


class TestWeatherWidget(unittest.TestCase):

    def setUp(self):
        self.w = WeatherWidget()

    def test_initial_state(self):
        self.assertIsNotNone(self.w.current)
        self.assertEqual(self.w.view_mode, "current")

    def test_location(self):
        loc = self.w.location
        self.assertIsNotNone(loc)

    def test_next_location(self):
        initial = self.w._current_location
        self.w.next_location()
        self.assertNotEqual(self.w._current_location, initial)

    def test_prev_location(self):
        self.w.next_location()
        self.w.prev_location()

    def test_add_location(self):
        initial = len(self.w.locations)
        self.w.add_location("Paris", "FR")
        self.assertEqual(len(self.w.locations), initial + 1)

    def test_remove_location(self):
        initial = len(self.w.locations)
        self.w.remove_location(1)
        self.assertEqual(len(self.w.locations), initial - 1)

    def test_cannot_remove_last(self):
        while len(self.w.locations) > 1:
            self.w.remove_location(0)
        result = self.w.remove_location(0)
        self.assertFalse(result)

    def test_toggle_favorite(self):
        loc = self.w.location
        was = loc.is_favorite
        self.w.toggle_favorite()
        self.assertNotEqual(loc.is_favorite, was)

    def test_hourly_forecast(self):
        hourly = self.w.hourly
        self.assertGreater(len(hourly), 0)

    def test_daily_forecast(self):
        daily = self.w.daily
        self.assertEqual(len(daily), 7)

    def test_alerts(self):
        alerts = self.w.alerts
        self.assertIsInstance(alerts, list)

    def test_cycle_view(self):
        self.w.cycle_view()
        self.assertEqual(self.w.view_mode, "hourly")
        self.w.cycle_view()
        self.assertEqual(self.w.view_mode, "daily")
        self.w.cycle_view()
        self.assertEqual(self.w.view_mode, "alerts")
        self.w.cycle_view()
        self.assertEqual(self.w.view_mode, "current")

    def test_render_current(self):
        lines = self.w.render_current()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_hourly(self):
        lines = self.w.render_hourly()
        self.assertIsInstance(lines, list)

    def test_render_daily(self):
        lines = self.w.render_daily()
        self.assertIsInstance(lines, list)

    def test_render_alerts(self):
        lines = self.w.render_alerts()
        self.assertIsInstance(lines, list)

    def test_render_compact(self):
        compact = self.w.render_compact()
        self.assertIsInstance(compact, str)
        self.assertIn("°", compact)

    def test_render(self):
        lines = self.w.render()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        self.w.handle_key("ArrowRight")
        self.w.handle_key("ArrowLeft")
        self.w.handle_key("v")
        self.w.handle_key("r")
        self.w.handle_key("f")

    def test_refresh(self):
        self.w.refresh()


class TestCurrentWeather(unittest.TestCase):

    def test_icon(self):
        c = CurrentWeather(
            temperature=70, feels_like=68,
            condition=WeatherCondition.CLEAR,
            description="Clear", humidity=50,
            wind_speed=5, wind_direction="N", wind_gust=10,
            pressure=1013, visibility=10, uv_index=3,
            dew_point=55, air_quality=30,
            sunrise=time.time(), sunset=time.time(),
        )
        self.assertIn("☀️", c.icon)

    def test_wind_str(self):
        c = CurrentWeather(
            temperature=70, feels_like=68,
            condition=WeatherCondition.CLEAR,
            description="Clear", humidity=50,
            wind_speed=15, wind_direction="NW", wind_gust=25,
            pressure=1013, visibility=10, uv_index=3,
            dew_point=55, air_quality=30,
            sunrise=time.time(), sunset=time.time(),
        )
        self.assertIn("NW", c.wind_str)

    def test_uv_str(self):
        c = CurrentWeather(
            temperature=70, feels_like=68,
            condition=WeatherCondition.CLEAR,
            description="Clear", humidity=50,
            wind_speed=5, wind_direction="N", wind_gust=10,
            pressure=1013, visibility=10, uv_index=8,
            dew_point=55, air_quality=30,
            sunrise=time.time(), sunset=time.time(),
        )
        self.assertIn("High", c.uv_str)


class TestDailyForecast(unittest.TestCase):

    def test_day_name_today(self):
        d = DailyForecast(
            date=time.time(), high=75, low=55,
            condition=WeatherCondition.CLEAR,
            precipitation_percent=0, humidity=50,
            wind_speed=10, sunrise=time.time(), sunset=time.time(),
            uv_index=5,
        )
        self.assertEqual(d.day_name, "Today")

    def test_icon(self):
        d = DailyForecast(
            date=time.time(), high=75, low=55,
            condition=WeatherCondition.RAIN,
            precipitation_percent=80, humidity=80,
            wind_speed=10, sunrise=time.time(), sunset=time.time(),
            uv_index=3,
        )
        self.assertIn("🌧️", d.icon)


class TestWeatherAlert(unittest.TestCase):

    def test_is_active(self):
        a = WeatherAlert(
            title="Test", description="Test alert",
            severity=AlertSeverity.WARNING,
            start_time=time.time() - 3600,
            end_time=time.time() + 3600,
        )
        self.assertTrue(a.is_active)

    def test_is_not_active(self):
        a = WeatherAlert(
            title="Test", description="Test alert",
            severity=AlertSeverity.WARNING,
            start_time=time.time() + 7200,
            end_time=time.time() + 10800,
        )
        self.assertFalse(a.is_active)


class TestWeatherLocation(unittest.TestCase):

    def test_display(self):
        loc = WeatherLocation(name="SF", region="CA", country="US")
        self.assertEqual(loc.display, "SF, CA")

    def test_display_no_region(self):
        loc = WeatherLocation(name="London", country="UK")
        self.assertEqual(loc.display, "London")


class TestHourlyForecast(unittest.TestCase):

    def test_temp_str(self):
        h = HourlyForecast(
            time=time.time(), temperature=72, feels_like=70,
            condition=WeatherCondition.CLEAR,
            precipitation_percent=0, humidity=50,
            wind_speed=10, wind_direction="N",
        )
        self.assertEqual(h.temp_str, "72°")


# ─── Disk Analyzer Tests ─────────────────────────────────────────────────


class TestDiskAnalyzer(unittest.TestCase):

    def setUp(self):
        self.da = DiskAnalyzer()

    def test_initial_state(self):
        self.assertIsNotNone(self.da.root)

    def test_current_entries(self):
        entries = self.da.current_entries
        self.assertGreater(len(entries), 0)

    def test_enter_directory(self):
        entries = self.da.current_entries
        dirs = [e for e in entries if e.is_dir]
        if dirs:
            result = self.da.enter_directory(dirs[0].name)
            self.assertTrue(result)

    def test_go_up(self):
        entries = self.da.current_entries
        dirs = [e for e in entries if e.is_dir]
        if dirs:
            self.da.enter_directory(dirs[0].name)
            result = self.da.go_up()
            self.assertTrue(result)

    def test_go_up_from_root(self):
        result = self.da.go_up()
        self.assertFalse(result)

    def test_breadcrumbs(self):
        bc = self.da.breadcrumbs
        self.assertIsInstance(bc, list)
        self.assertEqual(bc[0], "/")

    def test_selection(self):
        self.da.select(0)
        self.assertEqual(self.da.selected_index, 0)
        self.da.select_up()
        self.assertEqual(self.da.selected_index, 0)
        self.da.select_down()

    def test_open_selected(self):
        entries = self.da.current_entries
        dirs = [e for e in entries if e.is_dir]
        if dirs:
            self.da.select(0)
            result = self.da.open_selected()
            self.assertTrue(result)

    def test_cycle_view(self):
        self.da.cycle_view()
        self.assertEqual(self.da._view_mode, "treemap")
        self.da.cycle_view()
        self.assertEqual(self.da._view_mode, "types")
        self.da.cycle_view()
        self.assertEqual(self.da._view_mode, "large")
        self.da.cycle_view()
        self.assertEqual(self.da._view_mode, "tree")

    def test_toggle_sort(self):
        self.da.toggle_sort()
        self.assertFalse(self.da._sort_by_size)

    def test_toggle_files(self):
        self.da.toggle_files()
        self.assertFalse(self.da._show_files)

    def test_render_tree(self):
        lines = self.da.render_tree()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_treemap(self):
        lines = self.da.render_treemap()
        self.assertIsInstance(lines, list)

    def test_render_types(self):
        lines = self.da.render_types()
        self.assertIsInstance(lines, list)

    def test_render_large(self):
        lines = self.da.render_large()
        self.assertIsInstance(lines, list)

    def test_render_cleanup(self):
        self.da._view_mode = "cleanup"
        lines = self.da.render_cleanup()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.da.render()
        self.assertIsInstance(lines, list)

    def test_handle_key(self):
        self.da.handle_key("ArrowDown")
        self.da.handle_key("ArrowUp")
        self.da.handle_key("v")
        self.da.handle_key("s")
        self.da.handle_key("f")
        self.da.handle_key("Backspace")

    def test_type_stats(self):
        stats = self.da._type_stats
        self.assertIsInstance(stats, dict)
        self.assertGreater(len(stats), 0)

    def test_largest_files(self):
        files = self.da._largest_files
        self.assertGreater(len(files), 0)
        # Should be sorted by size descending
        for i in range(len(files) - 1):
            self.assertGreaterEqual(files[i].size, files[i + 1].size)

    def test_cleanup_suggestions(self):
        suggestions = self.da._cleanup_suggestions
        self.assertGreater(len(suggestions), 0)


class TestDiskEntry(unittest.TestCase):

    def test_size_str_bytes(self):
        e = DiskEntry(name="f", path="/f", size=500)
        self.assertEqual(e.size_str, "500 B")

    def test_size_str_kb(self):
        e = DiskEntry(name="f", path="/f", size=2048)
        self.assertEqual(e.size_str, "2.0 KB")

    def test_size_str_mb(self):
        e = DiskEntry(name="f", path="/f", size=5 * 1024 * 1024)
        self.assertEqual(e.size_str, "5.0 MB")

    def test_size_str_gb(self):
        e = DiskEntry(name="f", path="/f", size=2 * 1024 * 1024 * 1024)
        self.assertEqual(e.size_str, "2.00 GB")

    def test_icon_dir(self):
        e = DiskEntry(name="d", path="/d", is_dir=True)
        self.assertEqual(e.icon, "📁")

    def test_icon_file(self):
        e = DiskEntry(name="f.py", path="/f.py", file_type=FileType.CODE)
        self.assertEqual(e.icon, "📄")


class TestCleanupSuggestion(unittest.TestCase):

    def test_size_str(self):
        s = CleanupSuggestion("Test", "/tmp/test", 500 * 1024 * 1024, "Temp")
        self.assertIn("MB", s.size_str)

    def test_risk_icon(self):
        s = CleanupSuggestion("Test", "/tmp/test", 1024, "Temp", "high")
        self.assertIn("🔴", s.risk_icon)


if __name__ == "__main__":
    unittest.main()
