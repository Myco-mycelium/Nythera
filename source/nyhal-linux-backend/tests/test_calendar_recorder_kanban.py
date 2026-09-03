"""Tests for calendar app, screen recorder, and kanban board."""
import unittest
import time

from ui.calendar_app import (
    CalendarApp, CalendarEvent, Calendar, Reminder,
    EventRecurrence, ReminderType,
)
from ui.screen_recorder import (
    ScreenRecorder, RecordingPreset, RecordingSession, Hotkey,
    AudioDevice, CaptureMode, VideoFormat, AudioSource,
    QualityPreset, RecordingState,
)
from ui.kanban_board import (
    KanbanBoard, Board, Column, Card, Label, Subtask, Comment,
    CardPriority,
)


# ─── Calendar App Tests ──────────────────────────────────────────────

class TestCalendarEvent(unittest.TestCase):
    def test_duration_str(self):
        e = CalendarEvent(start_time=time.time(), end_time=time.time() + 3600)
        self.assertEqual(e.duration_str, "1h")

    def test_duration_minutes(self):
        e = CalendarEvent(start_time=time.time(), end_time=time.time() + 90 * 60)
        self.assertEqual(e.duration_minutes, 90)

    def test_short_duration(self):
        e = CalendarEvent(start_time=time.time(), end_time=time.time() + 30 * 60)
        self.assertEqual(e.duration_str, "30m")

    def test_recurrence_str(self):
        e = CalendarEvent(recurrence=EventRecurrence.WEEKLY)
        self.assertEqual(e.recurrence_str, "📆")

    def test_attendee_str(self):
        e = CalendarEvent(attendees=["a@b.com", "c@d.com"])
        self.assertIn("a@b.com", e.attendee_str)


class TestCalendar(unittest.TestCase):
    def test_display_with_unread(self):
        c = Calendar("Test", "#fff", True, 5)
        self.assertEqual(c.name, "Test")
        self.assertEqual(c.event_count, 5)


class TestCalendarApp(unittest.TestCase):
    def setUp(self):
        self.app = CalendarApp()

    def test_initial_state(self):
        self.assertGreater(len(self.app._events), 0)
        self.assertGreater(len(self.app._calendars), 0)

    def test_render_month(self):
        self.app.set_view("month")
        lines = self.app.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("Month" in l for l in lines))

    def test_render_week(self):
        self.app.set_view("week")
        lines = self.app.render()
        self.assertTrue(any("Week" in l for l in lines))

    def test_render_day(self):
        self.app.set_view("day")
        lines = self.app.render()
        self.assertTrue(any("Day" in l for l in lines))

    def test_render_agenda(self):
        self.app.set_view("agenda")
        lines = self.app.render()
        self.assertTrue(any("Upcoming" in l or "📅" in l for l in lines))

    def test_today_events(self):
        events = self.app.today_events
        self.assertIsInstance(events, list)

    def test_upcoming_events(self):
        events = self.app.upcoming_events
        self.assertGreater(len(events), 0)

    def test_navigate(self):
        initial = self.app._current_date
        self.app.navigate(1)
        self.assertNotEqual(self.app._current_date, initial)


# ─── Screen Recorder Tests ───────────────────────────────────────────

class TestRecordingPreset(unittest.TestCase):
    def test_estimated_size(self):
        p = RecordingPreset(bitrate=10000)
        size = p.estimated_size_mb_per_min
        self.assertGreater(size, 0)

    def test_encoder_icon(self):
        p = RecordingPreset(encoder="h265")
        self.assertEqual(p.encoder_icon, "🎬")


class TestRecordingSession(unittest.TestCase):
    def test_duration_str(self):
        s = RecordingSession(duration_s=3600)
        self.assertIn("h", s.duration_str)

    def test_size_str_mb(self):
        s = RecordingSession(file_size_mb=500)
        self.assertIn("MB", s.size_str)

    def test_size_str_gb(self):
        s = RecordingSession(file_size_mb=1500)
        self.assertIn("GB", s.size_str)


class TestAudioDevice(unittest.TestCase):
    def test_volume_bar(self):
        d = AudioDevice(volume=50)
        bar = d.volume_bar
        self.assertEqual(len(bar), 20)


class TestScreenRecorder(unittest.TestCase):
    def setUp(self):
        self.rec = ScreenRecorder()

    def test_initial_state(self):
        self.assertGreater(len(self.rec._presets), 0)
        self.assertGreater(len(self.rec._sessions), 0)
        self.assertEqual(self.rec._state, RecordingState.IDLE)

    def test_render_control(self):
        lines = self.rec.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("SCREEN RECORDER" in l for l in lines))

    def test_render_presets(self):
        self.rec.set_view("presets")
        lines = self.rec.render()
        self.assertTrue(any("Presets" in l for l in lines))

    def test_render_history(self):
        self.rec.set_view("history")
        lines = self.rec.render()
        self.assertTrue(any("History" in l for l in lines))

    def test_render_audio(self):
        self.rec.set_view("audio")
        lines = self.rec.render()
        self.assertTrue(any("Audio" in l for l in lines))

    def test_render_settings(self):
        self.rec.set_view("settings")
        lines = self.rec.render()
        self.assertTrue(any("Hotkeys" in l for l in lines))

    def test_start_stop(self):
        self.rec.start_recording()
        self.assertEqual(self.rec._state, RecordingState.RECORDING)
        self.rec.stop_recording()
        self.assertEqual(self.rec._state, RecordingState.IDLE)

    def test_pause_resume(self):
        self.rec.start_recording()
        self.rec.pause_recording()
        self.assertEqual(self.rec._state, RecordingState.PAUSED)
        self.rec.pause_recording()
        self.assertEqual(self.rec._state, RecordingState.RECORDING)
        self.rec.stop_recording()

    def test_recording_time(self):
        self.assertEqual(self.rec.recording_time_str, "00:00:00")

    def test_total_recorded(self):
        total = self.rec.total_recorded_s
        self.assertGreater(total, 0)


# ─── Kanban Board Tests ──────────────────────────────────────────────

class TestSubtask(unittest.TestCase):
    def test_checkbox(self):
        s = Subtask("test", done=True)
        self.assertEqual(s.checkbox, "☑")

    def test_pending(self):
        s = Subtask("test", done=False)
        self.assertEqual(s.checkbox, "☐")


class TestComment(unittest.TestCase):
    def test_preview(self):
        c = Comment("test", "a" * 100)
        self.assertTrue(len(c.preview) <= 63)

    def test_short_preview(self):
        c = Comment("test", "short")
        self.assertEqual(c.preview, "short")


class TestCard(unittest.TestCase):
    def test_subtask_progress(self):
        card = Card(subtasks=[Subtask("a", True), Subtask("b", False), Subtask("c", True)])
        self.assertEqual(card.subtask_progress, "☑2/3")

    def test_points_str(self):
        card = Card(story_points=8)
        self.assertEqual(card.points_str, "⭐8")

    def test_comment_count(self):
        card = Card(comments=[Comment("a", "hi"), Comment("b", "bye")])
        self.assertEqual(card.comment_count, "💬2")

    def test_is_overdue(self):
        card = Card(due_date=time.time() - 86400)
        self.assertTrue(card.is_overdue)


class TestColumn(unittest.TestCase):
    def test_wip_status_unlimited(self):
        col = Column("Test", wip_limit=0, cards=[Card(), Card()])
        self.assertEqual(col.wip_status, "2")

    def test_wip_status_limited(self):
        col = Column("Test", wip_limit=3, cards=[Card(), Card()])
        self.assertEqual(col.wip_status, "2/3")

    def test_over_limit(self):
        col = Column("Test", wip_limit=2, cards=[Card(), Card(), Card()])
        self.assertTrue(col.over_limit)


class TestBoard(unittest.TestCase):
    def test_total_cards(self):
        b = Board(columns=[Column("a", cards=[Card(), Card()]), Column("b", cards=[Card()])])
        self.assertEqual(b.total_cards, 3)


class TestKanbanBoard(unittest.TestCase):
    def setUp(self):
        self.board = KanbanBoard()

    def test_initial_state(self):
        self.assertGreater(len(self.board._boards), 0)
        self.assertGreater(len(self.board._members), 0)

    def test_render_board(self):
        lines = self.board.render()
        self.assertGreater(len(lines), 5)
        self.assertTrue(any("KANBAN" in l for l in lines))

    def test_render_card(self):
        self.board.set_view("card")
        self.board._selected_column = 0
        self.board._selected_card = 0
        lines = self.board.render()
        self.assertTrue(any("Priority" in l for l in lines) or any("─" in l for l in lines))

    def test_render_stats(self):
        self.board.set_view("stats")
        lines = self.board.render()
        self.assertTrue(any("Statistics" in l for l in lines))

    def test_render_members(self):
        self.board.set_view("members")
        lines = self.board.render()
        self.assertTrue(any("Members" in l for l in lines))

    def test_total_cards(self):
        self.assertGreater(self.board.total_cards, 0)

    def test_overdue_count(self):
        self.assertIsInstance(self.board.overdue_count, int)

    def test_select_board(self):
        self.board.select_board(1)
        self.assertEqual(self.board._current_board, 1)

    def test_select_column(self):
        self.board.select_column(1)
        self.assertEqual(self.board._selected_column, 1)

    def test_current_board(self):
        board = self.board.current_board
        self.assertIsNotNone(board)
        self.assertGreater(len(board.columns), 0)


if __name__ == "__main__":
    unittest.main()
