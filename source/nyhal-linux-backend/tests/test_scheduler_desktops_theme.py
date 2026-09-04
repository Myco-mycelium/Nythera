import unittest
import time


class TestTaskScheduler(unittest.TestCase):
    def setUp(self):
        from ui.task_scheduler import TaskScheduler, TaskStatus, TaskPriority
        self.ts = TaskScheduler()
        self.TS = TaskStatus
        self.TP = TaskPriority

    def test_initial_state(self):
        self.assertGreater(len(self.ts.tasks), 0)
        self.assertGreater(len(self.ts.executions), 0)

    def test_add_task(self):
        from ui.task_scheduler import ScheduledTask
        task = ScheduledTask(name="Test Task", command="echo test")
        self.ts.add_task(task)
        self.assertIn(task, self.ts.tasks)

    def test_remove_task(self):
        result = self.ts.remove_task("System Backup")
        self.assertTrue(result)

    def test_remove_task_not_found(self):
        result = self.ts.remove_task("Nonexistent")
        self.assertFalse(result)

    def test_toggle_task(self):
        result = self.ts.toggle_task("Security Scan")
        self.assertTrue(result)
        task = next(t for t in self.ts.tasks if t.name == "Security Scan")
        self.assertFalse(task.enabled)

    def test_run_task(self):
        exec_entry = self.ts.run_task("Weather Update")
        self.assertIsNotNone(exec_entry)
        self.assertEqual(exec_entry.status, self.TS.COMPLETED)

    def test_run_task_not_found(self):
        result = self.ts.run_task("Nonexistent")
        self.assertIsNone(result)

    def test_get_tasks_by_status(self):
        tasks = self.ts.get_tasks_by_status(self.TS.COMPLETED)
        self.assertGreater(len(tasks), 0)

    def test_get_tasks_by_priority(self):
        tasks = self.ts.get_tasks_by_priority(self.TP.HIGH)
        self.assertGreater(len(tasks), 0)

    def test_search_tasks(self):
        results = self.ts.search_tasks("backup")
        self.assertGreater(len(results), 0)

    def test_get_executions(self):
        execs = self.ts.get_executions(limit=5)
        self.assertGreater(len(execs), 0)

    def test_cron_expression(self):
        from ui.task_scheduler import CronExpression
        cron = CronExpression(minute="0", hour="9", day_of_week="1-5")
        self.assertIn("9", cron.human_readable)

    def test_task_status_icon(self):
        from ui.task_scheduler import ScheduledTask
        task = ScheduledTask(name="test", status=self.TS.COMPLETED)
        self.assertEqual(task.status_icon, "✅")

    def test_task_priority_icon(self):
        from ui.task_scheduler import ScheduledTask
        task = ScheduledTask(name="test", priority=self.TP.CRITICAL)
        self.assertEqual(task.priority_icon, "🔴")

    def test_get_stats(self):
        stats = self.ts.get_stats()
        self.assertIn("total_tasks", stats)
        self.assertIn("success_rate", stats)


class TestVirtualDesktops(unittest.TestCase):
    def setUp(self):
        from ui.virtual_desktops import VirtualDesktopManager, TileMode, HotCornerAction
        self.vdm = VirtualDesktopManager()
        self.TM = TileMode
        self.HCA = HotCornerAction

    def test_initial_state(self):
        self.assertGreater(len(self.vdm.workspaces), 0)
        self.assertGreater(len(self.vdm.hot_corners), 0)
        self.assertGreater(len(self.vdm.layouts), 0)
        self.assertIsNotNone(self.vdm.current_workspace)

    def test_switch_workspace(self):
        result = self.vdm.switch_workspace(2)
        self.assertTrue(result)
        self.assertEqual(self.vdm.current_workspace.id, 2)

    def test_switch_workspace_invalid(self):
        result = self.vdm.switch_workspace(99)
        self.assertFalse(result)

    def test_move_window_to_workspace(self):
        result = self.vdm.move_window_to_workspace(300, 2)
        self.assertTrue(result)

    def test_focus_window(self):
        result = self.vdm.focus_window(301)
        self.assertTrue(result)

    def test_tile_window(self):
        result = self.vdm.tile_window(400)
        self.assertTrue(result)

    def test_float_window(self):
        result = self.vdm.float_window(300)
        self.assertTrue(result)

    def test_minimize_window(self):
        result = self.vdm.minimize_window(200)
        self.assertTrue(result)

    def test_maximize_window(self):
        result = self.vdm.maximize_window(200)
        self.assertTrue(result)

    def test_close_window(self):
        initial = self.vdm.window_count
        result = self.vdm.close_window(600)
        self.assertTrue(result)
        self.assertEqual(self.vdm.window_count, initial - 1)

    def test_cycle_layout(self):
        initial = self.vdm.current_layout
        layout = self.vdm.cycle_layout()
        self.assertIsNotNone(layout)

    def test_set_layout(self):
        result = self.vdm.set_layout("Monocle")
        self.assertTrue(result)
        self.assertEqual(self.vdm.current_layout.name, "Monocle")

    def test_get_windows_on_workspace(self):
        windows = self.vdm.get_windows_on_workspace(0)
        self.assertGreater(len(windows), 0)

    def test_get_all_windows(self):
        windows = self.vdm.get_all_windows()
        self.assertEqual(len(windows), self.vdm.window_count)

    def test_get_hot_corner(self):
        corner = self.vdm.get_hot_corner("top-left")
        self.assertIsNotNone(corner)
        self.assertEqual(corner.action, self.HCA.SHOW_OVERVIEW)

    def test_trigger_hot_corner(self):
        action = self.vdm.trigger_hot_corner("top-right")
        self.assertEqual(action, self.HCA.SHOW_NOTIFICATIONS)

    def test_window_state_icon(self):
        from ui.virtual_desktops import Window, WindowState
        w = Window(state=WindowState.TILED)
        self.assertEqual(w.state_icon, "🔲")

    def test_workspace_window_count(self):
        ws = self.vdm.workspaces[0]
        self.assertGreater(ws.window_count, 0)

    def test_get_stats(self):
        stats = self.vdm.get_stats()
        self.assertIn("workspaces", stats)
        self.assertIn("windows", stats)


class TestThemeEditor(unittest.TestCase):
    def setUp(self):
        from ui.theme_editor import ThemeEditor, ThemeVariant, AccentColor
        self.te = ThemeEditor()
        self.TV = ThemeVariant
        self.AC = AccentColor

    def test_initial_state(self):
        self.assertGreater(len(self.te.schemes), 0)
        self.assertGreater(len(self.te.presets), 0)
        self.assertIsNotNone(self.te.current_scheme)

    def test_set_accent(self):
        result = self.te.set_accent(self.AC.GREEN)
        self.assertTrue(result)
        self.assertEqual(self.te.current_scheme.accent, self.AC.GREEN)

    def test_set_variant_dark(self):
        result = self.te.set_variant(self.TV.DARK)
        self.assertTrue(result)
        self.assertEqual(self.te.current_scheme.variant, self.TV.DARK)

    def test_set_variant_light(self):
        result = self.te.set_variant(self.TV.LIGHT)
        self.assertTrue(result)
        self.assertIn("fafafa", self.te.current_scheme.background)

    def test_apply_preset(self):
        result = self.te.apply_preset("Material Dark")
        self.assertTrue(result)

    def test_apply_preset_not_found(self):
        result = self.te.apply_preset("Nonexistent")
        self.assertFalse(result)

    def test_create_scheme(self):
        scheme = self.te.create_scheme("Custom Theme", primary="#ff0000")
        self.assertEqual(scheme.name, "Custom Theme")
        self.assertIn(scheme, self.te.schemes)

    def test_duplicate_scheme(self):
        new_scheme = self.te.duplicate_scheme("Nyrqis Default")
        self.assertIsNotNone(new_scheme)
        self.assertIn("Copy", new_scheme.name)

    def test_export_css(self):
        css = self.te.export_css()
        self.assertIn("--primary", css)
        self.assertIn("--background", css)

    def test_export_gtk(self):
        gtk = self.te.export_gtk()
        self.assertIn("[Settings]", gtk)
        self.assertIn("gtk-theme-name", gtk)

    def test_export_qt(self):
        qt = self.te.export_qt()
        self.assertIn("[ColorScheme]", qt)
        self.assertIn("name=", qt)

    def test_get_preview_html(self):
        html = self.te.get_preview_html()
        self.assertIn("<html>", html)
        self.assertIn("Button", html)

    def test_scheme_variant_icon(self):
        from ui.theme_editor import ColorScheme
        s = ColorScheme(name="test", variant=self.TV.DARK)
        self.assertEqual(s.variant_icon, "🌙")
        s.variant = self.TV.LIGHT
        self.assertEqual(s.variant_icon, "☀️")

    def test_widget_style_css(self):
        from ui.theme_editor import WidgetStyle
        ws = WidgetStyle(name="test", background="#000", foreground="#fff",
                          border_radius=8)
        css = ws.css_properties
        self.assertIn("background: #000", css)

    def test_get_stats(self):
        stats = self.te.get_stats()
        self.assertIn("schemes", stats)
        self.assertIn("presets", stats)


if __name__ == "__main__":
    unittest.main()
