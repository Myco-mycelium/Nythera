"""Tests for file operations, date/time settings, and user accounts."""

import os
import tempfile
import time
import unittest

from ui.file_operations import (
    FileOperations, FileOperation, FileItem, ConflictInfo,
    OperationType, OperationState, ConflictAction,
)
from ui.datetime_settings import (
    DateTimeSettings, DateTimeConfig, Timezone, WorldClock,
    DateFormat, TimeFormat, WeekStart, WORLD_TIMEZONES,
)
from ui.user_accounts import (
    UserAccounts, UserProfile, UserAvatar, LoginSession,
    UserType, PasswordStrength, AvatarStyle, AVATAR_COLORS,
)


# ---------------------------------------------------------------------------
# FileOperations tests
# ---------------------------------------------------------------------------

class TestFileOperations(unittest.TestCase):
    """Tests for FileOperations."""

    def setUp(self):
        self.fo = FileOperations()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_initialization(self):
        self.assertEqual(len(self.fo.active_operations), 0)
        self.assertEqual(len(self.fo.history), 0)

    def test_copy_operation(self):
        src = os.path.join(self.tmpdir, "test.txt")
        with open(src, "w") as f:
            f.write("hello")
        dst = os.path.join(self.tmpdir, "copy")

        op = self.fo.copy([src], dst)
        self.assertEqual(op.op_type, OperationType.COPY)
        self.assertEqual(op.state, OperationState.RUNNING)
        self.assertGreater(op.total_files, 0)

    def test_move_operation(self):
        src = os.path.join(self.tmpdir, "move.txt")
        with open(src, "w") as f:
            f.write("data")
        dst = os.path.join(self.tmpdir, "moved")

        op = self.fo.move([src], dst)
        self.assertEqual(op.op_type, OperationType.MOVE)
        self.assertIn(op, self.fo.active_operations)

    def test_delete_operation_requires_confirm(self):
        src = os.path.join(self.tmpdir, "delete.txt")
        with open(src, "w") as f:
            f.write("delete me")

        op = self.fo.delete([src], requires_confirm=True)
        self.assertEqual(op.state, OperationState.PENDING)

        result = self.fo.confirm_delete(op.id)
        self.assertTrue(result)
        self.assertEqual(op.state, OperationState.RUNNING)

    def test_delete_operation_no_confirm(self):
        src = os.path.join(self.tmpdir, "delete2.txt")
        with open(src, "w") as f:
            f.write("delete me")

        op = self.fo.delete([src], requires_confirm=False)
        self.assertEqual(op.state, OperationState.RUNNING)

    def test_rename_operation(self):
        src = os.path.join(self.tmpdir, "old.txt")
        with open(src, "w") as f:
            f.write("data")

        op = self.fo.rename(src, "new.txt")
        self.assertEqual(op.op_type, OperationType.RENAME)
        self.assertEqual(op.state, OperationState.RUNNING)

    def test_mkdir_operation(self):
        new_dir = os.path.join(self.tmpdir, "newdir")
        op = self.fo.mkdir(new_dir)
        self.assertEqual(op.op_type, OperationType.MKDIR)

    def test_tick_advances_progress(self):
        src = os.path.join(self.tmpdir, "tick.txt")
        with open(src, "w") as f:
            f.write("data" * 1000)

        op = self.fo.copy([src], os.path.join(self.tmpdir, "out"))
        initial_progress = op.progress
        self.fo.tick(0.5)
        self.assertGreater(op.progress, initial_progress)

    def test_tick_completes_operation(self):
        src = os.path.join(self.tmpdir, "small.txt")
        with open(src, "w") as f:
            f.write("x")

        op = self.fo.copy([src], os.path.join(self.tmpdir, "out"))
        # Tick enough to complete
        for _ in range(100):
            self.fo.tick(1.0)
        self.assertEqual(op.state, OperationState.COMPLETED)
        self.assertIn(op, self.fo.history)

    def test_pause_resume(self):
        src = os.path.join(self.tmpdir, "pause.txt")
        with open(src, "w") as f:
            f.write("data")

        op = self.fo.copy([src], os.path.join(self.tmpdir, "out"))
        self.assertTrue(self.fo.pause(op.id))
        self.assertEqual(op.state, OperationState.PAUSED)
        self.assertTrue(self.fo.resume(op.id))
        self.assertEqual(op.state, OperationState.RUNNING)

    def test_cancel(self):
        src = os.path.join(self.tmpdir, "cancel.txt")
        with open(src, "w") as f:
            f.write("data")

        op = self.fo.copy([src], os.path.join(self.tmpdir, "out"))
        self.assertTrue(self.fo.cancel(op.id))
        self.assertEqual(op.state, OperationState.CANCELLED)

    def test_retry(self):
        src = os.path.join(self.tmpdir, "retry.txt")
        with open(src, "w") as f:
            f.write("data")

        op = self.fo.copy([src], os.path.join(self.tmpdir, "out"))
        op.state = OperationState.FAILED
        self.assertTrue(self.fo.retry(op.id))
        self.assertEqual(op.state, OperationState.RUNNING)

    def test_detect_conflict(self):
        src = os.path.join(self.tmpdir, "conflict.txt")
        dst_dir = self.tmpdir
        with open(src, "w") as f:
            f.write("source")
        with open(os.path.join(dst_dir, "conflict.txt"), "w") as f:
            f.write("dest")

        conflict = self.fo.detect_conflict(src, dst_dir)
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict.source, src)

    def test_no_conflict(self):
        src = os.path.join(self.tmpdir, "src", "noconflict.txt")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        with open(src, "w") as f:
            f.write("data")
        dst = os.path.join(self.tmpdir, "dst")
        os.makedirs(dst, exist_ok=True)

        conflict = self.fo.detect_conflict(src, dst)
        self.assertIsNone(conflict)

    def test_callbacks(self):
        events = []
        self.fo.on("completed", lambda op: events.append("done"))

        src = os.path.join(self.tmpdir, "cb.txt")
        with open(src, "w") as f:
            f.write("x")

        op = self.fo.copy([src], os.path.join(self.tmpdir, "out"))
        for _ in range(100):
            self.fo.tick(1.0)
        self.assertIn("done", events)

    def test_stats(self):
        stats = self.fo.get_stats()
        self.assertIn("active", stats)
        self.assertIn("history", stats)
        self.assertIn("total_copied", stats)

    def test_operation_speed(self):
        src = os.path.join(self.tmpdir, "speed.txt")
        with open(src, "w") as f:
            f.write("x" * 1024 * 1024)

        op = self.fo.copy([src], os.path.join(self.tmpdir, "out"))
        # Manually set started_at to the past so speed calculation works
        op.started_at = time.time() - 1.0
        op.state = OperationState.RUNNING
        op.processed_bytes = 512 * 1024  # 512KB processed
        speed = op.speed_bytes_per_sec
        self.assertGreater(speed, 0)

    def test_operation_label(self):
        self.assertEqual(
            FileOperation(id="x", op_type=OperationType.COPY, sources=[]).label,
            "Copying")
        self.assertEqual(
            FileOperation(id="x", op_type=OperationType.DELETE, sources=[]).label,
            "Deleting")

    def test_render(self):
        rgb, w, h = self.fo.render()
        self.assertEqual(w, 400)
        self.assertEqual(h, 300)
        self.assertEqual(len(rgb), w * h * 3)


# ---------------------------------------------------------------------------
# DateTimeSettings tests
# ---------------------------------------------------------------------------

class TestDateTimeSettings(unittest.TestCase):
    """Tests for DateTimeSettings."""

    def setUp(self):
        self.dts = DateTimeSettings()

    def test_initialization(self):
        self.assertEqual(self.dts.config.timezone, "UTC")
        self.assertEqual(self.dts.config.date_format, DateFormat.ISO)

    def test_timezones(self):
        self.assertTrue(len(self.dts.timezones) > 10)
        ny = self.dts.get_timezone("America/New_York")
        self.assertIsNotNone(ny)
        self.assertEqual(ny.offset_hours, -5.0)

    def test_set_timezone(self):
        result = self.dts.set_timezone("Asia/Tokyo")
        self.assertTrue(result)
        self.assertEqual(self.dts.config.timezone, "Asia/Tokyo")

    def test_set_timezone_invalid(self):
        result = self.dts.set_timezone("Invalid/Zone")
        self.assertFalse(result)

    def test_set_date_format(self):
        self.dts.set_date_format(DateFormat.US)
        self.assertEqual(self.dts.config.date_format, DateFormat.US)

    def test_format_date_us(self):
        self.dts.set_date_format(DateFormat.US)
        result = self.dts.format_date(0.0)
        self.assertIn("/", result)

    def test_format_date_iso(self):
        self.dts.set_date_format(DateFormat.ISO)
        result = self.dts.format_date(0.0)
        self.assertIn("-", result)

    def test_format_date_european(self):
        self.dts.set_date_format(DateFormat.EUROPEAN)
        result = self.dts.format_date(0.0)
        self.assertIn(".", result)

    def test_set_time_format(self):
        self.dts.set_time_format(TimeFormat.H12)
        self.assertEqual(self.dts.config.time_format, TimeFormat.H12)

    def test_format_time_24h(self):
        self.dts.set_time_format(TimeFormat.H24)
        result = self.dts.format_time(0.0)
        # Should not contain AM/PM
        self.assertNotIn("AM", result)
        self.assertNotIn("PM", result)

    def test_toggle_ntp(self):
        self.assertTrue(self.dts.config.ntp_enabled)
        self.dts.toggle_ntp()
        self.assertFalse(self.dts.config.ntp_enabled)

    def test_toggle_seconds(self):
        self.assertFalse(self.dts.config.show_seconds)
        self.dts.toggle_seconds()
        self.assertTrue(self.dts.config.show_seconds)

    def test_add_world_clock(self):
        result = self.dts.add_world_clock("America/Chicago")
        self.assertTrue(result)
        self.assertEqual(len(self.dts.world_clocks), 4)

    def test_add_world_clock_duplicate(self):
        result = self.dts.add_world_clock("America/New_York")
        self.assertFalse(result)

    def test_remove_world_clock(self):
        result = self.dts.remove_world_clock("America/New_York")
        self.assertTrue(result)
        self.assertEqual(len(self.dts.world_clocks), 2)

    def test_world_clock_timezone(self):
        wc = WorldClock("Asia/Tokyo", "Tokyo")
        self.assertEqual(wc.timezone_id, "Asia/Tokyo")

    def test_timezone_offset_str(self):
        tz = Timezone("test", "Test", 5.5)
        self.assertEqual(tz.offset_str, "UTC+05:30")

    def test_set_week_start(self):
        self.dts.set_week_start(WeekStart.SUNDAY)
        self.assertEqual(self.dts.config.week_start, WeekStart.SUNDAY)

    def test_toggle_week_number(self):
        result = self.dts.toggle_week_number()
        self.assertTrue(result)

    def test_render(self):
        rgb, w, h = self.dts.render()
        self.assertEqual(len(rgb), w * h * 3)

    def test_to_dict(self):
        d = self.dts.to_dict()
        self.assertIn("timezone", d)
        self.assertIn("ntp_enabled", d)

    def test_set_ntp_server(self):
        self.dts.set_ntp_server("time.google.com")
        self.assertEqual(self.dts.config.ntp_server, "time.google.com")


# ---------------------------------------------------------------------------
# UserAccounts tests
# ---------------------------------------------------------------------------

class TestUserAccounts(unittest.TestCase):
    """Tests for UserAccounts."""

    def setUp(self):
        self.ua = UserAccounts()

    def test_initialization(self):
        self.assertEqual(len(self.ua.users), 1)
        self.assertIsNotNone(self.ua.current_user)

    def test_default_user(self):
        user = self.ua.current_user
        self.assertEqual(user.username, "nyrqis")
        self.assertEqual(user.user_type, UserType.ADMIN)

    def test_add_user(self):
        user = self.ua.add_user("alice", "Alice Smith", password="pass123")
        self.assertEqual(user.username, "alice")
        self.assertEqual(user.display_name, "Alice Smith")
        self.assertEqual(len(self.ua.users), 2)

    def test_add_user_default_type(self):
        user = self.ua.add_user("bob")
        self.assertEqual(user.user_type, UserType.STANDARD)

    def test_remove_user(self):
        user = self.ua.add_user("charlie")
        self.assertTrue(self.ua.remove_user(user.id))
        self.assertEqual(len(self.ua.users), 1)

    def test_cannot_remove_current_user(self):
        current = self.ua.current_user
        self.assertFalse(self.ua.remove_user(current.id))

    def test_get_user(self):
        user = self.ua.add_user("dave")
        found = self.ua.get_user(user.id)
        self.assertIsNotNone(found)
        self.assertEqual(found.username, "dave")

    def test_get_user_by_name(self):
        found = self.ua.get_user_by_name("nyrqis")
        self.assertIsNotNone(found)

    def test_update_user(self):
        user = self.ua.add_user("eve")
        result = self.ua.update_user(user.id, display_name="Eve Wilson")
        self.assertTrue(result)
        self.assertEqual(user.display_name, "Eve Wilson")

    def test_set_user_type(self):
        user = self.ua.add_user("frank")
        self.assertTrue(self.ua.set_user_type(user.id, UserType.GUEST))
        self.assertEqual(user.user_type, UserType.GUEST)

    def test_verify_password(self):
        user = self.ua.add_user("grace", password="secret")
        self.assertTrue(self.ua.verify_password(user.id, "secret"))
        self.assertFalse(self.ua.verify_password(user.id, "wrong"))

    def test_change_password(self):
        user = self.ua.add_user("helen", password="old")
        result = self.ua.change_password(user.id, "old", "new")
        self.assertTrue(result)
        self.assertTrue(self.ua.verify_password(user.id, "new"))
        self.assertFalse(self.ua.verify_password(user.id, "old"))

    def test_change_password_wrong_old(self):
        user = self.ua.add_user("ivan", password="correct")
        result = self.ua.change_password(user.id, "wrong", "new")
        self.assertFalse(result)

    def test_reset_password(self):
        user = self.ua.add_user("judy", password="old")
        result = self.ua.reset_password(user.id, "reset")
        self.assertTrue(result)
        self.assertTrue(self.ua.verify_password(user.id, "reset"))

    def test_password_strength(self):
        self.assertIn(
            UserAccounts.check_password_strength("a"),
            (PasswordStrength.VERY_WEAK, PasswordStrength.WEAK))
        # 'password' has lowercase (1) + length>=8 (1) = score 2
        self.assertIn(
            UserAccounts.check_password_strength("password"),
            (PasswordStrength.WEAK, PasswordStrength.FAIR))
        self.assertIn(
            UserAccounts.check_password_strength("Password1"),
            (PasswordStrength.FAIR, PasswordStrength.STRONG))
        self.assertIn(
            UserAccounts.check_password_strength("P@ssw0rd123"),
            (PasswordStrength.STRONG, PasswordStrength.VERY_STRONG))
        self.assertIn(
            UserAccounts.check_password_strength("C0mpl3x!P@ss#2024"),
            (PasswordStrength.STRONG, PasswordStrength.VERY_STRONG))

    def test_set_avatar(self):
        user = self.ua.add_user("kate")
        avatar = UserAvatar(style=AvatarStyle.INITIALS, initials="K")
        result = self.ua.set_avatar(user.id, avatar)
        self.assertTrue(result)
        self.assertEqual(user.avatar.initials, "K")

    def test_set_avatar_color(self):
        user = self.ua.add_user("leo")
        result = self.ua.set_avatar_color(user.id, (255, 0, 0))
        self.assertTrue(result)
        self.assertEqual(user.avatar.color, (255, 0, 0))

    def test_auto_login(self):
        self.ua.set_auto_login(True, "nyrqis")
        self.assertTrue(self.ua._auto_login_enabled)
        self.assertEqual(self.ua._auto_login_user, "nyrqis")

    def test_lock_timeout(self):
        self.ua.set_lock_timeout(600)
        self.assertEqual(self.ua._lock_screen_timeout, 600)

    def test_lock_unlock_user(self):
        user = self.ua.add_user("mia", password="pass")
        self.assertTrue(self.ua.lock_user(user.id))
        self.assertTrue(user.locked)
        self.assertTrue(self.ua.unlock_user(user.id, "pass"))
        self.assertFalse(user.locked)

    def test_unlock_wrong_password(self):
        user = self.ua.add_user("nick", password="pass")
        self.ua.lock_user(user.id)
        self.assertFalse(self.ua.unlock_user(user.id, "wrong"))
        self.assertTrue(user.locked)

    def test_session(self):
        user = self.ua.add_user("olivia")
        session = self.ua.start_session(user.id)
        self.assertEqual(session.user_id, user.id)
        self.assertEqual(len(self.ua.active_sessions), 1)

    def test_end_session(self):
        user = self.ua.add_user("paul")
        self.ua.start_session(user.id)
        result = self.ua.end_session(user.id)
        self.assertTrue(result)
        self.assertEqual(len(self.ua.active_sessions), 0)

    def test_stats(self):
        stats = self.ua.get_stats()
        self.assertEqual(stats["total_users"], 1)
        self.assertIn("admins", stats)
        self.assertIn("active_sessions", stats)

    def test_render(self):
        rgb, w, h = self.ua.render()
        self.assertEqual(len(rgb), w * h * 3)

    def test_to_dict(self):
        d = self.ua.to_dict()
        self.assertIn("users", d)
        self.assertIn("current_user", d)

    def test_avatar_colors(self):
        self.assertTrue(len(AVATAR_COLORS) >= 4)

    def test_user_home_dir(self):
        user = self.ua.add_user("quinn")
        self.assertEqual(user.home_dir, "/home/quinn")

    def test_user_display_type(self):
        user = self.ua.current_user
        self.assertEqual(user.display_type, "Admin")

    def test_user_initial(self):
        user = self.ua.current_user
        self.assertEqual(user.initial, "N")


if __name__ == "__main__":
    unittest.main()
