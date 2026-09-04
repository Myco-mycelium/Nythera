"""
Tests for Chat App.
"""
import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.chat_app import (
    ChatApp, User, Message, Channel, Reaction, Attachment,
    Thread, FileShare, UserStatus, ChannelType, MessageType,
    NotificationLevel,
)


class TestUser(unittest.TestCase):
    def test_create(self):
        u = User("admin", "Admin", "👤", UserStatus.ONLINE)
        self.assertEqual(u.username, "admin")

    def test_status_icon(self):
        u = User(status=UserStatus.ONLINE)
        self.assertEqual(u.status_icon, "🟢")

    def test_display(self):
        u = User("admin", "Admin", status=UserStatus.ONLINE)
        d = u.display
        self.assertIn("Admin", d)
        self.assertIn("🟢", d)


class TestMessage(unittest.TestCase):
    def test_create(self):
        m = Message(1, "general", "admin", "Hello!")
        self.assertEqual(m.content, "Hello!")

    def test_time_str(self):
        m = Message(timestamp=time.time())
        t = m.time_str
        self.assertIn(":", t)

    def test_reaction_count(self):
        m = Message(reactions=[Reaction("🎉", ["a", "b"]), Reaction("👍", ["c"])])
        self.assertEqual(m.reaction_count, 3)

    def test_pin_icon(self):
        m = Message(pinned=True)
        self.assertEqual(m.pin_icon, "📌")

    def test_bookmark_icon(self):
        m = Message(bookmarked=True)
        self.assertEqual(m.bookmark_icon, "🔖")


class TestReaction(unittest.TestCase):
    def test_create(self):
        r = Reaction("🎉", ["alice", "bob"])
        self.assertEqual(r.count, 2)

    def test_display(self):
        r = Reaction("👍", ["alice"])
        self.assertEqual(r.display, "👍 1")


class TestAttachment(unittest.TestCase):
    def test_create(self):
        a = Attachment("photo.png", "image", 1024000)
        self.assertEqual(a.name, "photo.png")

    def test_size_str(self):
        a = Attachment(size_bytes=2048000)
        self.assertIn("MB", a.size_str)

    def test_icon(self):
        a = Attachment(file_type="image")
        self.assertEqual(a.icon, "🖼️")


class TestChannel(unittest.TestCase):
    def test_create(self):
        ch = Channel("general", "general", ChannelType.PUBLIC)
        self.assertEqual(ch.name, "general")

    def test_type_icon(self):
        ch = Channel(channel_type=ChannelType.PUBLIC)
        self.assertEqual(ch.type_icon, "#")

    def test_display(self):
        ch = Channel("test", "test", unread_count=5)
        d = ch.display
        self.assertIn("test", d)
        self.assertIn("(5)", d)

    def test_member_count(self):
        ch = Channel(members=["a", "b", "c"])
        self.assertEqual(ch.member_count, 3)


class TestFileShare(unittest.TestCase):
    def test_create(self):
        f = FileShare("test.rs", "myco", time.time(), 5000, "code")
        self.assertEqual(f.name, "test.rs")

    def test_size_str(self):
        f = FileShare(size_bytes=5000000)
        self.assertIn("MB", f.size_str)


class TestThread(unittest.TestCase):
    def test_create(self):
        t = Thread(parent_id=5, replies=[Message(), Message()])
        self.assertEqual(t.reply_count, 2)


class TestChatApp(unittest.TestCase):
    def setUp(self):
        self.app = ChatApp()

    def test_initial_state(self):
        self.assertGreater(len(self.app.users), 0)
        self.assertGreater(len(self.app.channels), 0)
        self.assertGreater(len(self.app.messages), 0)

    def test_selected_channel(self):
        ch = self.app.selected_channel
        self.assertIsNotNone(ch)

    def test_select_channel(self):
        self.app.select_channel(2)
        self.assertEqual(self.app._selected_channel, 2)

    def test_send_message(self):
        ch_id = self.app.channels[0].id
        count = len(self.app.messages[ch_id])
        msg = self.app.send_message(ch_id, "Test message")
        self.assertIsNotNone(msg)
        self.assertEqual(len(self.app.messages[ch_id]), count + 1)

    def test_edit_message(self):
        ch_id = self.app.channels[0].id
        # Find a message by current_user (admin)
        msg = next(m for m in self.app.messages[ch_id] if m.author == self.app.current_user)
        result = self.app.edit_message(ch_id, msg.id, "Edited!")
        self.assertTrue(result)
        self.assertEqual(msg.content, "Edited!")

    def test_delete_message(self):
        ch_id = self.app.channels[0].id
        msg = next(m for m in self.app.messages[ch_id] if m.author == self.app.current_user)
        result = self.app.delete_message(ch_id, msg.id)
        self.assertTrue(result)
        self.assertTrue(msg.is_deleted)

    def test_pin_message(self):
        ch_id = self.app.channels[0].id
        msg_id = self.app.messages[ch_id][0].id
        result = self.app.pin_message(ch_id, msg_id)
        self.assertTrue(result)
        self.assertTrue(self.app.messages[ch_id][0].pinned)

    def test_bookmark_message(self):
        ch_id = self.app.channels[0].id
        msg_id = self.app.messages[ch_id][0].id
        result = self.app.bookmark_message(ch_id, msg_id)
        self.assertTrue(result)
        self.assertTrue(self.app.messages[ch_id][0].bookmarked)

    def test_add_reaction(self):
        ch_id = self.app.channels[0].id
        msg_id = self.app.messages[ch_id][0].id
        result = self.app.add_reaction(ch_id, msg_id, "🔥")
        self.assertTrue(result)

    def test_toggle_reaction(self):
        ch_id = self.app.channels[0].id
        msg_id = self.app.messages[ch_id][0].id
        self.app.add_reaction(ch_id, msg_id, "🔥")
        result = self.app.add_reaction(ch_id, msg_id, "🔥")
        self.assertTrue(result)

    def test_create_channel(self):
        count = len(self.app.channels)
        ch = self.app.create_channel("new-channel", ChannelType.PUBLIC, "Test")
        self.assertEqual(len(self.app.channels), count + 1)

    def test_archive_channel(self):
        result = self.app.archive_channel(2)
        self.assertTrue(result)
        self.assertEqual(self.app.channels[2].channel_type, ChannelType.ARCHIVED)

    def test_toggle_mute(self):
        result = self.app.toggle_mute(0)
        self.assertTrue(result)
        self.assertTrue(self.app.channels[0].is_muted)

    def test_upload_file(self):
        count = len(self.app.files)
        f = self.app.upload_file("test.txt", 1024, "text", "general")
        self.assertEqual(len(self.app.files), count + 1)

    def test_search_messages(self):
        results = self.app.search_messages("compositor")
        self.assertGreater(len(results), 0)

    def test_search_users(self):
        results = self.app.search_users("alice")
        self.assertGreater(len(results), 0)

    def test_search_channels(self):
        results = self.app.search_channels("dev")
        self.assertGreater(len(results), 0)

    def test_get_online_users(self):
        online = self.app.get_online_users()
        self.assertGreater(len(online), 0)

    def test_get_unread_channels(self):
        unread = self.app.get_unread_channels()
        self.assertGreater(len(unread), 0)

    def test_get_total_unread(self):
        total = self.app.get_total_unread()
        self.assertGreater(total, 0)

    def test_navigation(self):
        self.app.select_down()
        self.assertEqual(self.app._selected_channel, 1)
        self.app.select_up()
        self.assertEqual(self.app._selected_channel, 0)

    def test_stats(self):
        stats = self.app.get_stats()
        self.assertIn("users", stats)
        self.assertIn("channels", stats)
        self.assertIn("total_messages", stats)


if __name__ == "__main__":
    unittest.main()
