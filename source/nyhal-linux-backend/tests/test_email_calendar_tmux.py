"""
Tests for Email Client, Calendar App, and Terminal Multiplexer.
"""

import unittest
import time
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.email_client import (
    EmailClient, EmailMessage, EmailAddress, Contact, ComposeState,
    MailFolder, MessageFlag, ComposeMode
)
from ui.calendar_app import (
    CalendarApp, CalendarEvent, ViewMode, EventCategory, Recurrence
)
from ui.terminal_multiplexer import (
    TerminalMultiplexer, Session, Pane, SplitNode, SplitDirection
)


# ─── Email Client Tests ──────────────────────────────────────────────────


class TestEmailClient(unittest.TestCase):

    def setUp(self):
        self.client = EmailClient()

    def test_initial_state(self):
        self.assertGreater(len(self.client.get_messages()), 0)
        self.assertEqual(self.client.view_mode, "list")

    def test_get_inbox(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        self.assertGreater(len(msgs), 0)

    def test_get_sent(self):
        msgs = self.client.get_messages(MailFolder.SENT)
        self.assertGreater(len(msgs), 0)

    def test_get_drafts(self):
        msgs = self.client.get_messages(MailFolder.DRAFTS)
        self.assertGreater(len(msgs), 0)

    def test_get_message(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        msg = self.client.get_message(msgs[0].message_id)
        self.assertIsNotNone(msg)

    def test_move_message(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        result = self.client.move_message(msgs[0].message_id, MailFolder.ARCHIVE)
        self.assertTrue(result)
        self.assertEqual(msgs[0].folder, MailFolder.ARCHIVE)

    def test_delete_message(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        result = self.client.delete_message(msgs[0].message_id)
        self.assertTrue(result)
        self.assertEqual(msgs[0].folder, MailFolder.TRASH)

    def test_archive_message(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        result = self.client.archive_message(msgs[0].message_id)
        self.assertTrue(result)

    def test_mark_read(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        msg = msgs[0]
        was_read = msg.is_read
        if not was_read:
            self.client.mark_read(msg.message_id)
            self.assertTrue(msg.is_read)

    def test_mark_unread(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        msg = msgs[0]
        self.client.mark_unread(msg.message_id)
        self.assertFalse(msg.is_read)

    def test_toggle_star(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        msg = msgs[0]
        was_starred = msg.is_starred
        self.client.toggle_star(msg.message_id)
        self.assertNotEqual(msg.is_starred, was_starred)

    def test_compose_new(self):
        compose = self.client.compose_new()
        self.assertIsNotNone(compose)
        self.assertEqual(compose.mode, ComposeMode.NEW)
        self.assertEqual(self.client.view_mode, "compose")

    def test_compose_reply(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        compose = self.client.reply(msgs[0].message_id)
        self.assertIsNotNone(compose)
        self.assertEqual(compose.mode, ComposeMode.REPLY)
        self.assertIn("Re:", compose.subject)

    def test_compose_reply_all(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        compose = self.client.reply_all(msgs[0].message_id)
        self.assertIsNotNone(compose)
        self.assertEqual(compose.mode, ComposeMode.REPLY_ALL)

    def test_compose_forward(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        compose = self.client.forward(msgs[0].message_id)
        self.assertIsNotNone(compose)
        self.assertEqual(compose.mode, ComposeMode.FORWARD)
        self.assertIn("Fwd:", compose.subject)

    def test_send_compose(self):
        self.client.compose_new(to="test@example.com", subject="Test")
        self.client.update_compose("body", "Hello!")
        msg = self.client.send_compose()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.folder, MailFolder.SENT)
        self.assertEqual(self.client.view_mode, "list")

    def test_save_draft(self):
        self.client.compose_new(to="test@example.com")
        self.client.update_compose("subject", "Draft test")
        draft = self.client.save_draft()
        self.assertIsNotNone(draft)
        self.assertTrue(draft.is_draft)

    def test_discard_compose(self):
        self.client.compose_new()
        self.client.discard_compose()
        self.assertIsNone(self.client.compose)
        self.assertEqual(self.client.view_mode, "list")

    def test_update_compose(self):
        self.client.compose_new()
        self.client.update_compose("to", "test@example.com")
        self.client.update_compose("subject", "Hello")
        self.client.update_compose("body", "Body text")
        self.assertEqual(self.client.compose.to_text, "test@example.com")
        self.assertEqual(self.client.compose.subject, "Hello")
        self.assertTrue(self.client.compose.is_dirty)

    def test_search(self):
        results = self.client.search("welcome")
        self.assertGreater(len(results), 0)

    def test_search_empty(self):
        results = self.client.search("")
        self.assertEqual(len(results), 0)

    def test_set_folder(self):
        self.client.set_folder(MailFolder.SENT)
        self.assertEqual(self.client.current_folder, MailFolder.SENT)

    def test_folder_counts(self):
        counts = self.client.folder_counts()
        self.assertIn("Inbox", counts)
        self.assertIn("Sent", counts)

    def test_folder_total(self):
        total = self.client.folder_total(MailFolder.INBOX)
        self.assertGreater(total, 0)

    def test_contacts(self):
        contacts = self.client.contacts
        self.assertGreater(len(contacts), 0)

    def test_search_contacts(self):
        results = self.client.search_contacts("alice")
        self.assertGreater(len(results), 0)

    def test_get_thread(self):
        msgs = self.client.get_messages(MailFolder.INBOX)
        thread = self.client.get_thread(msgs[0].message_id)
        self.assertIsInstance(thread, list)

    def test_selection(self):
        self.client.select(0)
        self.assertEqual(self.client.selected_index, 0)
        self.client.select_up()
        self.assertEqual(self.client.selected_index, 0)
        self.client.select_down()

    def test_open_selected(self):
        self.client.select(0)
        msg = self.client.open_selected()
        self.assertIsNotNone(msg)
        self.assertEqual(self.client.view_mode, "read")

    def test_back_to_list(self):
        self.client.open_selected()
        self.client.back_to_list()
        self.assertEqual(self.client.view_mode, "list")

    def test_handle_key_list(self):
        self.client.handle_key("ArrowDown")
        self.client.handle_key("ArrowUp")
        self.client.handle_key("c")
        self.assertEqual(self.client.view_mode, "compose")

    def test_handle_key_read(self):
        self.client.select(0)
        self.client.open_selected()
        self.client.handle_key("Escape")
        self.assertEqual(self.client.view_mode, "list")

    def test_handle_key_compose(self):
        self.client.compose_new()
        self.client.handle_key("Tab")
        self.assertEqual(self.client.compose.active_field, "cc")
        self.client.handle_key("Escape")

    def test_render_list(self):
        lines = self.client.render_list()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_read(self):
        self.client.select(0)
        self.client.open_selected()
        lines = self.client.render_read()
        self.assertIsInstance(lines, list)

    def test_render_compose(self):
        self.client.compose_new()
        lines = self.client.render_compose()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.client.render()
        self.assertIsInstance(lines, list)


class TestEmailAddress(unittest.TestCase):

    def test_display_with_name(self):
        addr = EmailAddress(email="test@example.com", name="Test User")
        self.assertEqual(addr.display, "Test User <test@example.com>")

    def test_display_email_only(self):
        addr = EmailAddress(email="test@example.com")
        self.assertEqual(addr.display, "test@example.com")


class TestContact(unittest.TestCase):

    def test_display(self):
        c = Contact("Alice", "alice@example.com")
        self.assertEqual(c.display, "Alice <alice@example.com>")


class TestComposeState(unittest.TestCase):

    def test_parse_addresses(self):
        cs = ComposeState(to_text="Alice <alice@test.com>, bob@test.com")
        addrs = cs.to_list
        self.assertEqual(len(addrs), 2)
        self.assertEqual(addrs[0].name, "Alice")
        self.assertEqual(addrs[1].email, "bob@test.com")

    def test_parse_empty(self):
        cs = ComposeState(to_text="")
        self.assertEqual(len(cs.to_list), 0)


class TestEmailMessage(unittest.TestCase):

    def test_message_id(self):
        msg = EmailMessage(
            from_addr=EmailAddress(email="a@b.com"),
            to=[EmailAddress(email="c@d.com")],
            subject="Test",
        )
        self.assertIsNotNone(msg.message_id)
        self.assertEqual(len(msg.message_id), 12)

    def test_preview(self):
        msg = EmailMessage(
            from_addr=EmailAddress(email="a@b.com"),
            to=[],
            body="First line\nSecond line",
        )
        self.assertEqual(msg.preview, "First line")

    def test_is_read(self):
        msg = EmailMessage(
            from_addr=EmailAddress(email="a@b.com"),
            to=[],
            flags={MessageFlag.READ},
        )
        self.assertTrue(msg.is_read)

    def test_time_ago(self):
        msg = EmailMessage(
            from_addr=EmailAddress(email="a@b.com"),
            to=[],
            timestamp=time.time() - 3600,
        )
        self.assertIn("h ago", msg.time_ago)

    def test_to_display(self):
        msg = EmailMessage(
            from_addr=EmailAddress(email="a@b.com"),
            to=[EmailAddress(email="c@d.com", name="C")],
        )
        self.assertIn("C", msg.to_display)

    def test_date_str(self):
        msg = EmailMessage(
            from_addr=EmailAddress(email="a@b.com"),
            to=[],
        )
        self.assertIn("2026", msg.date_str)


# ─── Calendar App Tests ──────────────────────────────────────────────────


class TestCalendarApp(unittest.TestCase):

    def setUp(self):
        self.cal = CalendarApp()

    def test_initial_state(self):
        self.assertEqual(self.cal.view_mode, ViewMode.MONTH)

    def test_create_event(self):
        now = time.time()
        event = self.cal.create_event("Test Event", now, now + 3600)
        self.assertEqual(event.title, "Test Event")
        self.assertIsNotNone(event.event_id)

    def test_update_event(self):
        event = self.cal.create_event("Test", time.time(), time.time() + 3600)
        result = self.cal.update_event(event.event_id, title="Updated")
        self.assertTrue(result)
        self.assertEqual(event.title, "Updated")

    def test_delete_event(self):
        event = self.cal.create_event("Delete Me", time.time(), time.time() + 3600)
        result = self.cal.delete_event(event.event_id)
        self.assertTrue(result)
        self.assertIsNone(self.cal.get_event(event.event_id))

    def test_get_event(self):
        event = self.cal.create_event("Find Me", time.time(), time.time() + 3600)
        found = self.cal.get_event(event.event_id)
        self.assertIsNotNone(found)

    def test_duplicate_event(self):
        event = self.cal.create_event("Original", time.time(), time.time() + 3600)
        dup = self.cal.duplicate_event(event.event_id)
        self.assertIsNotNone(dup)
        self.assertIn("copy", dup.title)

    def test_get_today_events(self):
        events = self.cal.get_today_events()
        self.assertIsInstance(events, list)

    def test_get_upcoming(self):
        events = self.cal.get_upcoming(30)
        self.assertIsInstance(events, list)

    def test_search_events(self):
        results = self.cal.search_events("standup")
        self.assertGreater(len(results), 0)

    def test_conflicts(self):
        now = time.time()
        self.cal.create_event("A", now, now + 3600)
        self.cal.create_event("B", now + 1800, now + 5400)  # Overlaps
        conflicts = self.cal.get_conflicts()
        self.assertGreater(len(conflicts), 0)

    def test_view_cycle(self):
        self.cal.cycle_view()
        self.assertEqual(self.cal.view_mode, ViewMode.WEEK)
        self.cal.cycle_view()
        self.assertEqual(self.cal.view_mode, ViewMode.DAY)
        self.cal.cycle_view()
        self.assertEqual(self.cal.view_mode, ViewMode.MONTH)

    def test_navigation(self):
        self.cal.go_forward()
        self.cal.go_back()
        self.cal.go_today()

    def test_select_day(self):
        self.cal.select_day(15)
        self.assertEqual(self.cal.selected_day.day, 15)

    def test_filter_category(self):
        self.cal.set_filter_category(EventCategory.WORK)
        self.cal.set_filter_category(None)

    def test_check_reminders(self):
        # Create event with reminder that should trigger
        now = time.time()
        event = self.cal.create_event(
            "Remind Me", now + 600, now + 3600,
            reminder_minutes=15,
        )
        # Won't trigger yet (10 min before)
        reminders = self.cal.check_reminders()
        self.assertIsInstance(reminders, list)

    def test_export_events(self):
        exported = self.cal.export_events()
        self.assertIn("VCALENDAR", exported)
        self.assertIn("VEVENT", exported)

    def test_render_month(self):
        lines = self.cal.render_month()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_week(self):
        self.cal.set_view(ViewMode.WEEK)
        lines = self.cal.render_week()
        self.assertIsInstance(lines, list)

    def test_render_day(self):
        self.cal.set_view(ViewMode.DAY)
        lines = self.cal.render_day()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.cal.render()
        self.assertIsInstance(lines, list)

    def test_start_edit(self):
        event = self.cal.start_edit()
        self.assertIsNotNone(event)
        self.assertIsNotNone(self.cal.editing_event)

    def test_save_edit(self):
        self.cal.start_edit()
        self.cal.save_edit()
        self.assertIsNone(self.cal.editing_event)

    def test_cancel_edit(self):
        self.cal.start_edit()
        self.cal.cancel_edit()
        self.assertIsNone(self.cal.editing_event)


class TestCalendarEvent(unittest.TestCase):

    def test_duration_hours(self):
        now = time.time()
        event = CalendarEvent(title="T", start_time=now, end_time=now + 3600)
        self.assertEqual(event.duration_hours, 1.0)

    def test_duration_str(self):
        now = time.time()
        event = CalendarEvent(title="T", start_time=now, end_time=now + 5400)
        self.assertEqual(event.duration_str, "1h 30m")

    def test_time_range(self):
        now = time.time()
        event = CalendarEvent(title="T", start_time=now, end_time=now + 3600)
        self.assertIn("–", event.time_range)

    def test_all_day(self):
        now = time.time()
        event = CalendarEvent(title="T", start_time=now, end_time=now + 86400, all_day=True)
        self.assertEqual(event.time_range, "All day")

    def test_color(self):
        event = CalendarEvent(title="T", start_time=time.time(), end_time=time.time(), category=EventCategory.WORK)
        self.assertEqual(event.color, "#4A90D9")

    def test_is_past(self):
        event = CalendarEvent(title="T", start_time=time.time() - 7200, end_time=time.time() - 3600)
        self.assertTrue(event.is_past)

    def test_conflicts_with(self):
        now = time.time()
        e1 = CalendarEvent(title="A", start_time=now, end_time=now + 3600)
        e2 = CalendarEvent(title="B", start_time=now + 1800, end_time=now + 5400)
        self.assertTrue(e1.conflicts_with(e2))

    def test_no_conflict(self):
        now = time.time()
        e1 = CalendarEvent(title="A", start_time=now, end_time=now + 1800)
        e2 = CalendarEvent(title="B", start_time=now + 1800, end_time=now + 3600)
        self.assertFalse(e1.conflicts_with(e2))


# ─── Terminal Multiplexer Tests ──────────────────────────────────────────


class TestTerminalMultiplexer(unittest.TestCase):

    def setUp(self):
        self.mux = TerminalMultiplexer()

    def test_initial_state(self):
        self.assertEqual(self.mux.session_count, 1)
        self.assertIsNotNone(self.mux.current_session)

    def test_new_session(self):
        session = self.mux.new_session("Test")
        self.assertEqual(self.mux.session_count, 2)
        self.assertEqual(session.name, "Test")

    def test_close_session(self):
        self.mux.new_session()
        result = self.mux.close_session(1)
        self.assertTrue(result)
        self.assertEqual(self.mux.session_count, 1)

    def test_cannot_close_last(self):
        result = self.mux.close_session()
        self.assertFalse(result)

    def test_switch_session(self):
        self.mux.new_session()
        result = self.mux.switch_session(1)
        self.assertTrue(result)
        self.assertEqual(self.mux.session_index, 1)

    def test_next_session(self):
        self.mux.new_session()
        self.mux.switch_session(0)
        self.mux.next_session()
        self.assertEqual(self.mux.session_index, 1)

    def test_prev_session(self):
        self.mux.new_session()
        self.mux.switch_session(1)
        self.mux.prev_session()
        self.assertEqual(self.mux.session_index, 0)

    def test_rename_session(self):
        self.mux.rename_session("My Terminal")
        self.assertEqual(self.mux.current_session.name, "My Terminal")

    def test_split_horizontal(self):
        pane = self.mux.split_horizontal()
        self.assertIsNotNone(pane)
        self.assertEqual(self.mux.current_session.pane_count, 2)

    def test_split_vertical(self):
        pane = self.mux.split_vertical()
        self.assertIsNotNone(pane)
        self.assertEqual(self.mux.current_session.pane_count, 2)

    def test_close_pane(self):
        self.mux.split_horizontal()
        pane = self.mux.focused_pane
        self.mux.close_pane(pane.pane_id)
        self.assertEqual(self.mux.current_session.pane_count, 1)

    def test_cannot_close_last_pane(self):
        result = self.mux.close_pane()
        self.assertFalse(result)

    def test_focus_pane(self):
        pane1 = self.mux.split_horizontal()
        self.assertIsNotNone(pane1)
        result = self.mux.focus_pane(pane1.pane_id)
        self.assertTrue(result)

    def test_focus_next_pane(self):
        self.mux.split_horizontal()
        self.mux.focus_next_pane()
        # Should still have a focused pane
        self.assertIsNotNone(self.mux.focused_pane)

    def test_focus_prev_pane(self):
        self.mux.split_horizontal()
        self.mux.focus_prev_pane()
        self.assertIsNotNone(self.mux.focused_pane)

    def test_resize_pane(self):
        self.mux.split_horizontal()
        pane = self.mux.focused_pane
        result = self.mux.resize_pane(pane.pane_id, 1)
        self.assertTrue(result)

    def test_rename_pane(self):
        pane = self.mux.focused_pane
        result = self.mux.rename_pane(pane.pane_id, "My Pane")
        self.assertTrue(result)
        self.assertEqual(pane.title, "My Pane")

    def test_toggle_sync(self):
        result = self.mux.toggle_sync()
        self.assertTrue(result)
        self.assertTrue(self.mux.sync_mode)
        self.mux.toggle_sync()
        self.assertFalse(self.mux.sync_mode)

    def test_broadcast_input(self):
        self.mux.toggle_sync()
        count = self.mux.broadcast_input("echo hello\n")
        self.assertGreater(count, 0)

    def test_copy_pane_history(self):
        pane = self.mux.focused_pane
        pane.write("line 1\nline 2")
        text = self.mux.copy_pane_history(pane.pane_id, 5)
        self.assertIn("line 1", text)

    def test_paste_to_pane(self):
        pane = self.mux.focused_pane
        result = self.mux.paste_to_pane(pane.pane_id, "pasted text")
        self.assertTrue(result)

    def test_save_layout(self):
        layout = self.mux.save_layout()
        self.assertIsInstance(layout, str)

    def test_get_layout_dict(self):
        layout = self.mux.get_layout_dict()
        self.assertIsInstance(layout, dict)
        self.assertIn("tree", layout)

    def test_write_to_pane(self):
        pane = self.mux.focused_pane
        self.mux.write_to_pane(pane.pane_id, "test output")
        self.assertIn("test output", pane.history)

    def test_write_to_focused(self):
        self.mux.write_to_focused("hello")
        pane = self.mux.focused_pane
        self.assertIn("hello", pane.history)

    def test_handle_key_new_session(self):
        self.mux.handle_key("Ctrl+t")
        self.assertEqual(self.mux.session_count, 2)

    def test_handle_key_split(self):
        self.mux.handle_key("Ctrl+Alt+s")
        self.assertEqual(self.mux.current_session.pane_count, 2)

    def test_handle_key_focus(self):
        self.mux.split_horizontal()
        self.mux.handle_key("Ctrl+Alt+ArrowRight")
        self.mux.handle_key("Ctrl+Alt+ArrowLeft")
        self.mux.handle_key("Ctrl+Alt+ArrowUp")
        self.mux.handle_key("Ctrl+Alt+ArrowDown")

    def test_render_tab_bar(self):
        tab_bar = self.mux.render_tab_bar()
        self.assertIsInstance(tab_bar, str)

    def test_render_status_bar(self):
        status = self.mux.render_status_bar()
        self.assertIsInstance(status, str)
        self.assertIn("Terminal", status)

    def test_render_pane(self):
        pane = self.mux.focused_pane
        lines = self.mux.render_pane(pane)
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render(self):
        lines = self.mux.render()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)


class TestPane(unittest.TestCase):

    def test_pane_id(self):
        pane = Pane()
        self.assertIsNotNone(pane.pane_id)
        self.assertEqual(len(pane.pane_id), 6)

    def test_write(self):
        pane = Pane()
        pane.write("line 1")
        pane.write("line 2")
        self.assertEqual(pane.line_count, 2)

    def test_clear(self):
        pane = Pane()
        pane.write("stuff")
        pane.clear()
        self.assertEqual(pane.line_count, 0)

    def test_get_visible_lines(self):
        pane = Pane()
        for i in range(50):
            pane.write(f"line {i}")
        lines = pane.get_visible_lines(10)
        self.assertEqual(len(lines), 10)

    def test_scrollback_limit(self):
        pane = Pane(scrollback=10)
        for i in range(20):
            pane.write(f"line {i}")
        self.assertEqual(pane.line_count, 10)


class TestSplitNode(unittest.TestCase):

    def test_leaf(self):
        node = SplitNode(pane=Pane())
        self.assertTrue(node.is_leaf)

    def test_not_leaf(self):
        node = SplitNode(split=SplitDirection.HORIZONTAL, children=[SplitNode(pane=Pane())])
        self.assertFalse(node.is_leaf)

    def test_all_panes(self):
        p1, p2 = Pane(), Pane()
        node = SplitNode(
            split=SplitDirection.HORIZONTAL,
            children=[SplitNode(pane=p1), SplitNode(pane=p2)],
        )
        panes = node.all_panes()
        self.assertEqual(len(panes), 2)

    def test_find_pane(self):
        p = Pane()
        node = SplitNode(
            split=SplitDirection.HORIZONTAL,
            children=[SplitNode(pane=Pane()), SplitNode(pane=p)],
        )
        found = node.find_pane(p.pane_id)
        self.assertIsNotNone(found)

    def test_to_dict(self):
        node = SplitNode(pane=Pane(title="Test"))
        d = node.to_dict()
        self.assertIn("pane_id", d)

    def test_depth(self):
        node = SplitNode(
            split=SplitDirection.HORIZONTAL,
            children=[
                SplitNode(pane=Pane()),
                SplitNode(pane=Pane()),
            ],
        )
        self.assertEqual(node.depth, 1)


class TestSession(unittest.TestCase):

    def test_session_id(self):
        session = Session()
        self.assertIsNotNone(session.session_id)

    def test_pane_count(self):
        session = Session()
        self.assertEqual(session.pane_count, 1)

    def test_all_panes(self):
        session = Session()
        self.assertEqual(len(session.all_panes), 1)

    def test_focused_pane(self):
        session = Session()
        self.assertIsNotNone(session.focused_pane)


if __name__ == "__main__":
    unittest.main()
