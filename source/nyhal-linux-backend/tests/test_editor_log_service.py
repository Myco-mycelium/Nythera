#!/usr/bin/env python3
"""Tests for log viewer, text editor, and service manager."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


# ===================================================================
# Log Viewer Tests
# ===================================================================

class TestLogLevel(unittest.TestCase):
    """Tests for LogLevel."""

    def test_color(self):
        from ui.log_viewer import LogLevel
        c = LogLevel.ERROR.color
        self.assertEqual(len(c), 3)

    def test_priority(self):
        from ui.log_viewer import LogLevel
        self.assertGreater(LogLevel.ERROR.priority, LogLevel.INFO.priority)
        self.assertGreater(LogLevel.FATAL.priority, LogLevel.WARN.priority)


class TestParseLogLine(unittest.TestCase):
    """Tests for log line parsing."""

    def test_syslog(self):
        from ui.log_viewer import parse_log_line, LogLevel
        entry = parse_log_line("Jan  1 12:00:00 hostname sshd[1234]: connection accepted")
        self.assertEqual(entry.source, "sshd")

    def test_level_detection(self):
        from ui.log_viewer import parse_log_line, LogLevel
        entry = parse_log_line("[ERROR] Something went wrong")
        self.assertEqual(entry.level, LogLevel.ERROR)

    def test_plain_line(self):
        from ui.log_viewer import parse_log_line
        entry = parse_log_line("just a plain line")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.text, "just a plain line")


class TestLogViewer(unittest.TestCase):
    """Tests for LogViewer."""

    def setUp(self):
        from ui.log_viewer import LogViewer
        self.lv = LogViewer()

    def test_initial_state(self):
        self.assertFalse(self.lv.visible)
        self.assertEqual(len(self.lv.tabs), 0)

    def test_show_hide(self):
        self.lv.show()
        self.assertTrue(self.lv.visible)
        self.lv.hide()
        self.assertFalse(self.lv.visible)

    def test_add_tab(self):
        tab = self.lv.add_tab("Test Log")
        self.assertIsNotNone(tab)
        self.assertEqual(len(self.lv.tabs), 1)
        self.assertEqual(tab.name, "Test Log")

    def test_remove_tab(self):
        tab = self.lv.add_tab("Test")
        result = self.lv.remove_tab(tab.id)
        self.assertTrue(result)
        self.assertEqual(len(self.lv.tabs), 0)

    def test_add_line(self):
        tab = self.lv.add_tab("Log")
        entry = self.lv.add_line(tab.id, "[INFO] Hello world")
        self.assertIsNotNone(entry)
        self.assertEqual(tab.total_lines, 1)

    def test_add_lines(self):
        tab = self.lv.add_tab("Log")
        count = self.lv.add_lines(tab.id, ["line 1", "line 2", "line 3"])
        self.assertEqual(count, 3)

    def test_level_filter(self):
        from ui.log_viewer import LogLevel
        tab = self.lv.add_tab("Log")
        self.lv.add_line(tab.id, "[INFO] info msg")
        self.lv.add_line(tab.id, "[ERROR] error msg")
        self.lv.set_level_filter(tab.id, "error+")
        filtered = self.lv.get_filtered_entries(tab.id)
        self.assertEqual(len(filtered), 1)

    def test_search_filter(self):
        tab = self.lv.add_tab("Log")
        self.lv.add_line(tab.id, "hello world")
        self.lv.add_line(tab.id, "foo bar")
        self.lv.set_search(tab.id, "hello")
        filtered = self.lv.get_filtered_entries(tab.id)
        self.assertEqual(len(filtered), 1)

    def test_search_regex(self):
        tab = self.lv.add_tab("Log")
        self.lv.add_line(tab.id, "error 404")
        self.lv.add_line(tab.id, "error 500")
        self.lv.set_search(tab.id, r"error \d+", regex=True)
        filtered = self.lv.get_filtered_entries(tab.id)
        self.assertEqual(len(filtered), 2)

    def test_follow_toggle(self):
        tab = self.lv.add_tab("Log")
        result = self.lv.toggle_follow(tab.id)
        self.assertFalse(result)  # was True, toggled to False
        result2 = self.lv.toggle_follow(tab.id)
        self.assertTrue(result2)

    def test_pause_toggle(self):
        tab = self.lv.add_tab("Log")
        result = self.lv.toggle_pause(tab.id)
        self.assertTrue(result)

    def test_bookmark(self):
        tab = self.lv.add_tab("Log")
        self.lv.add_line(tab.id, "line 1")
        self.lv.bookmark_line(tab.id, 0)
        bookmarks = self.lv.get_bookmarks(tab.id)
        self.assertEqual(len(bookmarks), 1)

    def test_next_tab(self):
        self.lv.add_tab("Tab1")
        self.lv.add_tab("Tab2")
        next_id = self.lv.next_tab()
        self.assertIsNotNone(next_id)

    def test_stats(self):
        tab = self.lv.add_tab("Log")
        self.lv.add_line(tab.id, "[INFO] info")
        self.lv.add_line(tab.id, "[ERROR] err")
        stats = self.lv.stats(tab.id)
        self.assertEqual(stats["total_lines"], 2)

    def test_render_hidden(self):
        self.assertIsNone(self.lv.render())

    def test_load_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
            f.write("[INFO] line 1\n[ERROR] line 2\n[DEBUG] line 3\n")
            path = f.name
        try:
            tab = self.lv.add_tab("File Log", path)
            self.assertGreater(tab.total_lines, 0)
        finally:
            os.unlink(path)

    def test_callback(self):
        events = []
        self.lv.on_event(lambda t, d: events.append(t))
        self.lv.show()
        self.assertIn("shown", events)

    def test_repr(self):
        r = repr(self.lv)
        self.assertIn("LogViewer", r)


# ===================================================================
# Text Editor Tests
# ===================================================================

class TestEnhancedTextEditor(unittest.TestCase):
    """Tests for EnhancedTextEditor."""

    def setUp(self):
        from ui.editor_enhanced import EnhancedTextEditor
        self.ed = EnhancedTextEditor()

    def test_new_file(self):
        tab = self.ed.new_file("test.py")
        self.assertIsNotNone(tab)
        self.assertEqual(tab.name, "test.py")
        self.assertTrue(tab.dirty)

    def test_insert_text(self):
        self.ed.new_file()
        self.ed.insert_text("hello")
        tab = self.ed.active_tab
        self.assertEqual(tab.lines[0], "hello")

    def test_insert_newline(self):
        self.ed.new_file()
        self.ed.insert_text("line1")
        self.ed.insert_text("\n")
        self.ed.insert_text("line2")
        tab = self.ed.active_tab
        self.assertEqual(len(tab.lines), 2)
        self.assertEqual(tab.lines[0], "line1")

    def test_delete_char(self):
        self.ed.new_file()
        self.ed.insert_text("abc")
        self.ed.delete_char()
        tab = self.ed.active_tab
        self.assertEqual(tab.lines[0], "ab")

    def test_move_cursor(self):
        self.ed.new_file()
        self.ed.insert_text("hello")
        # cursor is at col 5, move left 2
        self.ed.move_cursor(col_delta=-2)
        tab = self.ed.active_tab
        self.assertEqual(tab.cursor.col, 3)

    def test_go_to_line(self):
        self.ed.new_file()
        self.ed.insert_text("line1")
        self.ed.insert_text("\n")
        self.ed.insert_text("line2")
        self.ed.insert_text("\n")
        self.ed.insert_text("line3")
        self.ed.go_to_line(3)
        tab = self.ed.active_tab
        self.assertEqual(tab.cursor.line, 2)

    def test_go_to_start_end(self):
        self.ed.new_file()
        self.ed.insert_text("line1")
        self.ed.insert_text("\n")
        self.ed.insert_text("line2")
        self.ed.insert_text("\n")
        self.ed.insert_text("line3")
        self.ed.go_to_end()
        tab = self.ed.active_tab
        self.assertEqual(tab.cursor.line, 2)
        self.ed.go_to_start()
        self.assertEqual(tab.cursor.line, 0)

    def test_undo(self):
        self.ed.new_file()
        self.ed.insert_text("hello")
        result = self.ed.undo()
        self.assertTrue(result)
        tab = self.ed.active_tab
        self.assertEqual(len(tab.lines[0]), 0)  # back to empty

    def test_redo(self):
        self.ed.new_file()
        self.ed.insert_text("hello")
        self.ed.undo()
        result = self.ed.redo()
        self.assertTrue(result)
        tab = self.ed.active_tab
        self.assertEqual(tab.lines[0], "hello")

    def test_find(self):
        self.ed.new_file()
        self.ed.insert_text("hello world\nhello again")
        count = self.ed.find("hello")
        self.assertEqual(count, 2)

    def test_find_regex(self):
        self.ed.new_file()
        self.ed.insert_text("error 404\nerror 500")
        count = self.ed.find(r"error \d+", regex=True)
        self.assertEqual(count, 2)

    def test_find_next(self):
        self.ed.new_file()
        self.ed.insert_text("abc abc abc")
        self.ed.find("abc")
        r1 = self.ed.find_next()
        r2 = self.ed.find_next()
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)
        self.assertGreater(r2.col, r1.col)

    def test_replace_current(self):
        self.ed.new_file()
        self.ed.insert_text("hello world")
        self.ed.find("world")
        result = self.ed.replace_current("earth")
        self.assertTrue(result)
        tab = self.ed.active_tab
        self.assertEqual(tab.lines[0], "hello earth")

    def test_replace_all(self):
        self.ed.new_file()
        self.ed.insert_text("aaa bbb aaa ccc aaa")
        self.ed.find("aaa")
        count = self.ed.replace_all("xxx")
        self.assertEqual(count, 3)
        tab = self.ed.active_tab
        self.assertEqual(tab.lines[0], "xxx bbb xxx ccc xxx")

    def test_close_tab(self):
        tab = self.ed.new_file()
        result = self.ed.close_tab(tab.id)
        self.assertTrue(result)
        self.assertEqual(len(self.ed.tabs), 0)

    def test_open_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("print('hello')\n")
            path = f.name
        try:
            tab = self.ed.open_file(path)
            self.assertIsNotNone(tab)
            self.assertEqual(tab.language.value, "python")
        finally:
            os.unlink(path)

    def test_open_already_open(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("x = 1\n")
            path = f.name
        try:
            tab1 = self.ed.open_file(path)
            tab2 = self.ed.open_file(path)
            self.assertEqual(tab1.id, tab2.id)
        finally:
            os.unlink(path)

    def test_toggle_word_wrap(self):
        self.ed.new_file()
        result = self.ed.toggle_word_wrap()
        tab = self.ed.active_tab
        self.assertFalse(tab.word_wrap)

    def test_toggle_line_numbers(self):
        self.ed.new_file()
        result = self.ed.toggle_line_numbers()
        tab = self.ed.active_tab
        self.assertFalse(tab.show_line_numbers)

    def test_language_detection(self):
        from ui.editor_enhanced import detect_language, SyntaxLanguage
        self.assertEqual(detect_language("test.py"), SyntaxLanguage.PYTHON)
        self.assertEqual(detect_language("test.rs"), SyntaxLanguage.RUST)
        self.assertEqual(detect_language("test.js"), SyntaxLanguage.JAVASCRIPT)
        self.assertEqual(detect_language("test.txt"), SyntaxLanguage.NONE)

    def test_syntax_highlighting(self):
        from ui.editor_enhanced import highlight_line, SyntaxLanguage
        spans = highlight_line("def foo():", SyntaxLanguage.PYTHON)
        self.assertGreater(len(spans), 0)

    def test_render_hidden(self):
        self.assertIsNone(self.ed.render())

    def test_find_visible(self):
        self.assertFalse(self.ed.find_visible)
        self.ed.new_file()
        self.ed.insert_text("test")
        self.ed.find("test")
        self.assertTrue(self.ed.find_visible)
        self.ed.close_find()
        self.assertFalse(self.ed.find_visible)

    def test_callback(self):
        events = []
        self.ed.on_event(lambda t, d: events.append(t))
        self.ed.new_file()
        self.assertIn("file_created", events)

    def test_repr(self):
        r = repr(self.ed)
        self.assertIn("EnhancedTextEditor", r)


# ===================================================================
# Service Manager Tests
# ===================================================================

class TestServiceManager(unittest.TestCase):
    """Tests for ServiceManager."""

    def setUp(self):
        from ui.service_manager import ServiceManager
        self.sm = ServiceManager()

    def test_initial_state(self):
        self.assertFalse(self.sm.visible)
        self.assertGreater(self.sm.service_count, 0)

    def test_show_hide(self):
        self.sm.show()
        self.assertTrue(self.sm.visible)
        self.sm.hide()
        self.assertFalse(self.sm.visible)

    def test_toggle(self):
        result = self.sm.toggle()
        self.assertTrue(result)
        result2 = self.sm.toggle()
        self.assertFalse(result2)

    def test_search(self):
        self.sm.search("nyrqis")
        for svc in self.sm.services:
            self.assertIn("nyrqis", svc.name.lower())

    def test_filter_state(self):
        from ui.service_manager import ServiceState
        self.sm.filter_state(ServiceState.RUNNING)
        for svc in self.sm.services:
            self.assertEqual(svc.state, ServiceState.RUNNING)

    def test_filter_type(self):
        from ui.service_manager import ServiceType
        self.sm.filter_type(ServiceType.TIMER)
        for svc in self.sm.services:
            self.assertEqual(svc.service_type, ServiceType.TIMER)

    def test_start_service(self):
        result = self.sm.start_service("sshd")
        self.assertTrue(result)
        svc = self.sm.get_service("sshd")
        self.assertEqual(svc.state.value, "running")

    def test_stop_service(self):
        result = self.sm.stop_service("nyrqis-daemon")
        self.assertTrue(result)
        svc = self.sm.get_service("nyrqis-daemon")
        self.assertEqual(svc.state.value, "stopped")

    def test_restart_service(self):
        result = self.sm.restart_service("nyrqis-daemon")
        self.assertTrue(result)
        svc = self.sm.get_service("nyrqis-daemon")
        self.assertGreater(svc.restart_count, 0)

    def test_enable_disable(self):
        self.sm.enable_service("sshd")
        svc = self.sm.get_service("sshd")
        self.assertTrue(svc.enabled)
        self.sm.disable_service("sshd")
        self.assertFalse(svc.enabled)

    def test_get_dependencies(self):
        deps = self.sm.get_dependencies("nyrqis-shell")
        self.assertGreater(len(deps), 0)

    def test_get_dependents(self):
        deps = self.sm.get_dependents("nyrqis-daemon")
        self.assertGreater(len(deps), 0)

    def test_select_service(self):
        result = self.sm.select_service("nyrqis-daemon")
        self.assertTrue(result)
        self.assertEqual(self.sm.current_view, "detail")

    def test_dashboard(self):
        dash = self.sm.dashboard()
        self.assertIn("running", dash)
        self.assertIn("failed", dash)
        self.assertIn("total_cpu_percent", dash)

    def test_running_count(self):
        self.assertGreater(self.sm.running_count, 0)

    def test_failed_count(self):
        self.assertGreater(self.sm.failed_count, 0)

    def test_navigation(self):
        self.sm.navigate_down()
        self.assertEqual(self.sm.selected_index, 1)
        self.sm.navigate_up()
        self.assertEqual(self.sm.selected_index, 0)

    def test_activate_selected(self):
        svc = self.sm.activate_selected()
        self.assertIsNotNone(svc)
        self.assertEqual(self.sm.current_view, "detail")

    def test_render_hidden(self):
        self.assertIsNone(self.sm.render())

    def test_sort(self):
        self.sm.set_sort("cpu")
        svcs = self.sm.services
        self.assertGreater(len(svcs), 1)

    def test_callback(self):
        events = []
        self.sm.on_event(lambda t, d: events.append(t))
        self.sm.show()
        self.assertIn("shown", events)

    def test_repr(self):
        r = repr(self.sm)
        self.assertIn("ServiceManager", r)


class TestServiceInfo(unittest.TestCase):
    """Tests for ServiceInfo."""

    def test_uptime(self):
        from ui.service_manager import ServiceInfo, ServiceState, ServiceType
        svc = ServiceInfo(id="t", name="t", uptime_seconds=90000)
        self.assertIn("d", svc.uptime)

    def test_uptime_short(self):
        from ui.service_manager import ServiceInfo, ServiceState, ServiceType
        svc = ServiceInfo(id="t", name="t", uptime_seconds=300)
        self.assertIn("m", svc.uptime)

    def test_status_dot(self):
        from ui.service_manager import ServiceInfo, ServiceState, ServiceType
        svc = ServiceInfo(id="t", name="t", active=True)
        self.assertEqual(svc.status_dot, "●")


if __name__ == "__main__":
    unittest.main()
