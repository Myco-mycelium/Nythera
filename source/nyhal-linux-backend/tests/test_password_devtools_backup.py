"""
Tests for Password Manager, Dev Tools, and Backup Utility.
"""

import unittest
import time
import sys
import os
import json
import base64

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.password_manager import (
    PasswordManager, PasswordEntry, PasswordGenerator,
    EntryType
)
from ui.dev_tools import (
    DevTools, ToolType, HttpMethod, ApiResponse, RegexMatch
)
from ui.backup_utility import (
    BackupUtility, BackupProfile, Snapshot,
    BackupMode, BackupStatus, ScheduleFrequency
)


# ─── Password Manager Tests ──────────────────────────────────────────────


class TestPasswordManager(unittest.TestCase):

    def setUp(self):
        self.pm = PasswordManager()

    def test_initial_state(self):
        self.assertGreater(len(self.pm.get_entries()), 0)
        self.assertEqual(self.pm.view_mode, "list")

    def test_create_entry(self):
        entry = self.pm.create_entry("Test", EntryType.LOGIN, "user", "pass123")
        self.assertEqual(entry.title, "Test")

    def test_update_entry(self):
        entry = self.pm.create_entry("Update Me")
        result = self.pm.update_entry(entry.entry_id, title="Updated")
        self.assertTrue(result)
        self.assertEqual(entry.title, "Updated")

    def test_delete_entry(self):
        entry = self.pm.create_entry("Delete Me")
        result = self.pm.delete_entry(entry.entry_id)
        self.assertTrue(result)
        self.assertIsNone(self.pm.get_entry(entry.entry_id))

    def test_toggle_favorite(self):
        entry = self.pm.create_entry("Fav")
        was = entry.favorite
        self.pm.toggle_favorite(entry.entry_id)
        self.assertNotEqual(entry.favorite, was)

    def test_copy_password(self):
        entry = self.pm.create_entry("Copy", password="secret")
        pw = self.pm.copy_password(entry.entry_id)
        self.assertEqual(pw, "secret")

    def test_copy_username(self):
        entry = self.pm.create_entry("Copy", username="user@test")
        user = self.pm.copy_username(entry.entry_id)
        self.assertEqual(user, "user@test")

    def test_search(self):
        results = self.pm.search("GitHub")
        self.assertGreater(len(results), 0)

    def test_set_category(self):
        self.pm.set_category("Logins")
        entries = self.pm.get_entries()
        for e in entries:
            self.assertEqual(e.category, "Logins")

    def test_open_entry(self):
        entries = self.pm.get_entries()
        if entries:
            e = self.pm.open_entry(entries[0].entry_id)
            self.assertIsNotNone(e)
            self.assertEqual(self.pm.view_mode, "detail")

    def test_close_entry(self):
        self.pm.open_entry()
        self.pm.close_entry()
        self.assertEqual(self.pm.view_mode, "list")

    def test_toggle_show_password(self):
        self.pm.open_entry()
        result = self.pm.toggle_show_password()
        self.assertTrue(result)
        self.assertTrue(self.pm._show_password)

    def test_generator(self):
        gen = self.pm.generator
        self.assertIsNotNone(gen)

    def test_generate_passwords(self):
        passwords = self.pm.generate_passwords(3)
        self.assertEqual(len(passwords), 3)
        for pw in passwords:
            self.assertEqual(len(pw), 20)

    def test_generate_passphrase(self):
        phrase = self.pm.generator.generate_passphrase(4)
        self.assertEqual(len(phrase.split("-")), 4)

    def test_total_entries(self):
        self.assertGreater(self.pm.total_entries, 0)

    def test_lock_unlock(self):
        self.pm.lock()
        self.assertTrue(self.pm.is_locked)
        self.pm.unlock("master")
        self.assertFalse(self.pm.is_locked)

    def test_selection(self):
        self.pm.select_up()
        self.pm.select_down()

    def test_render_list(self):
        lines = self.pm.render_list()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_detail(self):
        self.pm.open_entry()
        lines = self.pm.render_detail()
        self.assertIsInstance(lines, list)

    def test_render_generator(self):
        self.pm._view_mode = "generator"
        self.pm.generate_passwords()
        lines = self.pm.render_generator()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.pm.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_list(self):
        self.pm.handle_key("ArrowDown")
        self.pm.handle_key("ArrowUp")
        self.pm.handle_key("Enter")
        self.assertEqual(self.pm.view_mode, "detail")

    def test_handle_key_detail(self):
        self.pm.open_entry()
        self.pm.handle_key("Escape")
        self.assertEqual(self.pm.view_mode, "list")


class TestPasswordEntry(unittest.TestCase):

    def test_strength_weak(self):
        e = PasswordEntry(title="T", password="abc")
        self.assertIn("Weak", e.strength)

    def test_strength_strong(self):
        e = PasswordEntry(title="T", password="MyStr0ng!Pass#2026")
        self.assertIn("Strong", e.strength)

    def test_masked_password(self):
        e = PasswordEntry(title="T", password="secret123")
        self.assertEqual(e.masked_password, "•" * 9)

    def test_card_masked(self):
        e = PasswordEntry(title="T", card_number="4532015112830366")
        self.assertIn("0366", e.card_masked)

    def test_icon(self):
        e = PasswordEntry(title="T", entry_type=EntryType.CREDIT_CARD)
        self.assertEqual(e.icon, "💳")

    def test_strength_score(self):
        e = PasswordEntry(title="T", password="abc")
        self.assertLess(e.strength_score, 1.0)


class TestPasswordGenerator(unittest.TestCase):

    def test_generate(self):
        gen = PasswordGenerator()
        pw = gen.generate(1)
        self.assertEqual(len(pw[0]), 20)

    def test_generate_length(self):
        gen = PasswordGenerator()
        gen.length = 32
        pw = gen.generate(1)
        self.assertEqual(len(pw[0]), 32)

    def test_charset(self):
        gen = PasswordGenerator()
        gen.lowercase = False
        gen.uppercase = False
        gen.digits = True
        gen.symbols = False
        charset = gen.charset
        self.assertEqual(charset, "0123456789")

    def test_exclude_ambiguous(self):
        gen = PasswordGenerator()
        gen.exclude_ambiguous = True
        charset = gen.charset
        self.assertNotIn("l", charset)
        self.assertNotIn("I", charset)


# ─── Dev Tools Tests ─────────────────────────────────────────────────────


class TestDevTools(unittest.TestCase):

    def setUp(self):
        self.dt = DevTools()

    def test_initial_state(self):
        self.assertEqual(self.dt.current_tool, ToolType.JSON)

    def test_cycle_tool(self):
        self.dt.cycle_tool()
        self.assertEqual(self.dt.current_tool, ToolType.REGEX)
        self.dt.cycle_tool()
        self.assertEqual(self.dt.current_tool, ToolType.API)

    def test_format_json(self):
        self.dt.set_json_input('{"key": "value"}')
        result = self.dt.format_json()
        self.assertIn("key", result)

    def test_format_json_error(self):
        self.dt.set_json_input("{invalid json}")
        result = self.dt.format_json()
        self.assertIn("Error", result)

    def test_minify_json(self):
        self.dt.set_json_input('{\n  "key": "value"\n}')
        result = self.dt.minify_json()
        self.assertNotIn("\n", result)

    def test_validate_json(self):
        self.dt.set_json_input('{"valid": true}')
        self.assertTrue(self.dt.validate_json())

    def test_validate_json_invalid(self):
        self.dt.set_json_input("not json")
        self.assertFalse(self.dt.validate_json())

    def test_sample_json(self):
        result = self.dt.sample_json()
        self.assertIn("Nyrqis", result)

    def test_regex(self):
        self.dt.set_regex_pattern(r'\d+')
        self.dt.set_regex_test("abc 123 def 456")
        matches = self.dt.test_regex()
        self.assertEqual(len(matches), 2)

    def test_regex_no_match(self):
        self.dt.set_regex_pattern(r'xyz')
        self.dt.set_regex_test("abc 123")
        matches = self.dt.test_regex()
        self.assertEqual(len(matches), 0)

    def test_regex_error(self):
        self.dt.set_regex_pattern(r'[invalid')
        self.dt.set_regex_test("test")
        self.dt.test_regex()
        self.assertIn("error", self.dt._regex_error.lower())

    def test_sample_regex(self):
        self.dt.sample_regex()
        self.assertIn("@", self.dt._regex_pattern)

    def test_api_execute(self):
        response = self.dt.execute_api()
        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.body)

    def test_api_set_method(self):
        self.dt.set_api_method(HttpMethod.POST)
        self.assertEqual(self.dt._api_method, HttpMethod.POST)

    def test_base64_encode(self):
        self.dt._base64_input = "Hello, World!"
        result = self.dt.base64_encode()
        decoded = base64.b64decode(result).decode()
        self.assertEqual(decoded, "Hello, World!")

    def test_base64_decode(self):
        encoded = base64.b64encode(b"Hello").decode()
        self.dt._base64_input = encoded
        result = self.dt.base64_decode()
        self.assertEqual(result, "Hello")

    def test_url_encode(self):
        self.dt._base64_input = "Hello World!"
        result = self.dt.url_encode()
        self.assertIn("%20", result)

    def test_url_decode(self):
        self.dt._base64_input = "Hello%20World"
        result = self.dt.url_decode()
        self.assertEqual(result, "Hello World")

    def test_hash(self):
        self.dt._hash_input = "test"
        results = self.dt.generate_hashes()
        self.assertIn("MD5", results)
        self.assertIn("SHA-256", results)

    def test_timestamp(self):
        self.dt._ts_input = str(int(time.time()))
        results = self.dt.convert_timestamp()
        self.assertIn("Unix", results)
        self.assertIn("ISO 8601", results)

    def test_render_json(self):
        lines = self.dt.render_json()
        self.assertIsInstance(lines, list)

    def test_render_regex(self):
        lines = self.dt.render_regex()
        self.assertIsInstance(lines, list)

    def test_render_api(self):
        lines = self.dt.render_api()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.dt.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_tab(self):
        self.dt.handle_key("Tab")
        self.assertEqual(self.dt.current_tool, ToolType.REGEX)

    def test_handle_key_json(self):
        self.dt._current_tool = ToolType.JSON
        self.dt.handle_key("s")
        self.assertIn("Nyrqis", self.dt._json_input)

    def test_handle_key_hash(self):
        self.dt._current_tool = ToolType.HASH
        self.dt._hash_input = "test"
        self.dt.handle_key("Enter")
        self.assertIn("MD5", self.dt._hash_results)


class TestApiResponse(unittest.TestCase):

    def test_status_str(self):
        r = ApiResponse(status_code=200, status_text="OK")
        self.assertEqual(r.status_str, "200 OK")

    def test_size_str(self):
        r = ApiResponse(size_bytes=1024)
        self.assertIn("KB", r.size_str)

    def test_time_str(self):
        r = ApiResponse(time_ms=500)
        self.assertIn("ms", r.time_str)


class TestRegexMatch(unittest.TestCase):

    def test_match(self):
        m = RegexMatch(match_text="hello", start=0, end=5, groups=["el"])
        self.assertEqual(m.match_text, "hello")
        self.assertEqual(m.groups, ["el"])


# ─── Backup Utility Tests ────────────────────────────────────────────────


class TestBackupUtility(unittest.TestCase):

    def setUp(self):
        self.bu = BackupUtility()

    def test_initial_state(self):
        self.assertGreater(len(self.bu.profiles), 0)
        self.assertEqual(self.bu.view_mode, "profiles")

    def test_create_profile(self):
        profile = self.bu.create_profile("Test", ["/tmp"], "/backup/test")
        self.assertEqual(profile.name, "Test")

    def test_delete_profile(self):
        profile = self.bu.create_profile("Delete Me")
        result = self.bu.delete_profile(profile.profile_id)
        self.assertTrue(result)

    def test_get_profile(self):
        profile = self.bu.profiles[0]
        found = self.bu.get_profile(profile.profile_id)
        self.assertIsNotNone(found)

    def test_get_snapshots(self):
        snapshots = self.bu.get_snapshots()
        self.assertGreater(len(snapshots), 0)

    def test_get_snapshots_by_profile(self):
        profile = self.bu.profiles[0]
        snapshots = self.bu.get_snapshots(profile.profile_id)
        self.assertIsInstance(snapshots, list)

    def test_delete_snapshot(self):
        snapshots = self.bu.get_snapshots()
        if snapshots:
            result = self.bu.delete_snapshot(snapshots[0].snapshot_id)
            self.assertTrue(result)

    def test_start_backup(self):
        profile = self.bu.profiles[0]
        snapshot = self.bu.start_backup(profile.profile_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.status, BackupStatus.COMPLETED)

    def test_restore_snapshot(self):
        snapshots = self.bu.get_snapshots()
        if snapshots:
            result = self.bu.restore_snapshot(snapshots[0].snapshot_id)
            self.assertTrue(result)

    def test_total_backup_size(self):
        size = self.bu.total_backup_size()
        self.assertGreater(size, 0)

    def test_total_snapshot_count(self):
        count = self.bu.total_snapshot_count()
        self.assertGreater(count, 0)

    def test_oldest_snapshot(self):
        oldest = self.bu.oldest_snapshot()
        self.assertIsNotNone(oldest)

    def test_newest_snapshot(self):
        newest = self.bu.newest_snapshot()
        self.assertIsNotNone(newest)

    def test_open_selected(self):
        self.bu.open_selected()
        self.assertEqual(self.bu.view_mode, "snapshots")

    def test_selection(self):
        self.bu.select_up()
        self.bu.select_down()

    def test_render_profiles(self):
        lines = self.bu.render_profiles()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_snapshots(self):
        self.bu.open_selected()
        lines = self.bu.render_snapshots()
        self.assertIsInstance(lines, list)

    def test_render(self):
        lines = self.bu.render()
        self.assertIsInstance(lines, list)

    def test_handle_key_list(self):
        self.bu.handle_key("ArrowDown")
        self.bu.handle_key("ArrowUp")
        self.bu.handle_key("Enter")
        self.assertEqual(self.bu.view_mode, "snapshots")

    def test_handle_key_escape(self):
        self.bu.handle_key("Enter")  # Go to snapshots
        self.bu.handle_key("Escape")
        self.assertEqual(self.bu.view_mode, "profiles")


class TestSnapshot(unittest.TestCase):

    def test_size_str_bytes(self):
        s = Snapshot(name="t", size_bytes=500)
        self.assertEqual(s.size_str, "500 B")

    def test_size_str_mb(self):
        s = Snapshot(name="t", size_bytes=5 * 1024 * 1024)
        self.assertEqual(s.size_str, "5.0 MB")

    def test_size_str_gb(self):
        s = Snapshot(name="t", size_bytes=2 * 1024 * 1024 * 1024)
        self.assertEqual(s.size_str, "2.00 GB")

    def test_duration_str(self):
        s = Snapshot(name="t", duration_seconds=125)
        self.assertEqual(s.duration_str, "2m 5s")

    def test_status_icon(self):
        s = Snapshot(name="t", status=BackupStatus.COMPLETED)
        self.assertEqual(s.status_icon, "✅")


class TestBackupProfile(unittest.TestCase):

    def test_schedule_str(self):
        p = BackupProfile(name="T", schedule=ScheduleFrequency.DAILY)
        self.assertEqual(p.schedule_str, "Daily")

    def test_last_run_str(self):
        p = BackupProfile(name="T", last_run=0)
        self.assertEqual(p.last_run_str, "Never")

    def test_source_str(self):
        p = BackupProfile(name="T", source_paths=["/a", "/b"])
        self.assertEqual(p.source_str, "/a, /b")


if __name__ == "__main__":
    unittest.main()
