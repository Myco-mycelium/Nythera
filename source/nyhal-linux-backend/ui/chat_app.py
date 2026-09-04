"""
Nyrqis OS - Chat Application
Real-time messaging with channels, DMs, file sharing, and reactions.

Features:
- Channels with topics and member management
- Direct messages (DMs) with user presence
- Message reactions (emoji)
- File/attachment sharing
- Thread replies
- Search messages
- User status (online, away, busy, offline)
- Message pinning and bookmarking
- Typing indicators
- Read receipts
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class UserStatus(Enum):
    ONLINE = "online"
    AWAY = "away"
    BUSY = "busy"
    DO_NOT_DISTURB = "dnd"
    OFFLINE = "offline"
    INVISIBLE = "invisible"


class MessageType(Enum):
    TEXT = "text"
    FILE = "file"
    IMAGE = "image"
    CODE = "code"
    SYSTEM = "system"
    LINK = "link"
    EMOJI = "emoji"
    THREAD = "thread"


class ChannelType(Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    DM = "dm"
    GROUP_DM = "group_dm"
    ARCHIVED = "archived"


class NotificationLevel(Enum):
    ALL = "all"
    MENTIONS = "mentions"
    NONE = "none"


STATUS_ICONS = {
    UserStatus.ONLINE: "🟢", UserStatus.AWAY: "🟡",
    UserStatus.BUSY: "🔴", UserStatus.DO_NOT_DISTURB: "⛔",
    UserStatus.OFFLINE: "⚫", UserStatus.INVISIBLE: "👻",
}

CHANNEL_ICONS = {
    ChannelType.PUBLIC: "#", ChannelType.PRIVATE: "🔒",
    ChannelType.DM: "💬", ChannelType.GROUP_DM: "👥",
    ChannelType.ARCHIVED: "📦",
}


@dataclass
class User:
    username: str = ""
    display_name: str = ""
    avatar: str = ""
    status: UserStatus = UserStatus.ONLINE
    status_text: str = ""
    role: str = "member"  # admin, moderator, member
    last_active: float = 0.0
    email: str = ""
    timezone: str = "UTC"

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.display_name or self.username}"

    @property
    def last_active_str(self) -> str:
        if self.last_active == 0:
            return "Never"
        delta = time.time() - self.last_active
        if delta < 60:
            return "Just now"
        elif delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h ago"
        return f"{delta / 86400:.0f}d ago"


@dataclass
class Reaction:
    emoji: str = ""
    users: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.users)

    @property
    def display(self) -> str:
        return f"{self.emoji} {self.count}"


@dataclass
class Attachment:
    name: str = ""
    file_type: str = ""
    size_bytes: int = 0
    url: str = ""
    thumbnail: str = ""

    @property
    def size_str(self) -> str:
        b = self.size_bytes
        if b < 1024:
            return f"{b} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        return f"{b / 1024 ** 2:.1f} MB"

    @property
    def icon(self) -> str:
        icons = {"image": "🖼️", "video": "🎬", "audio": "🎵",
                 "pdf": "📄", "code": "💻", "archive": "📦", "text": "📝"}
        return icons.get(self.file_type, "📎")

    @property
    def is_image(self) -> bool:
        return self.file_type == "image"


@dataclass
class Message:
    id: int = 0
    channel_id: str = ""
    author: str = ""
    content: str = ""
    message_type: MessageType = MessageType.TEXT
    timestamp: float = 0.0
    edited: float = 0.0
    reactions: List[Reaction] = field(default_factory=list)
    attachments: List[Attachment] = field(default_factory=list)
    thread_id: int = 0
    reply_to: int = 0
    pinned: bool = False
    bookmarked: bool = False
    mentions: List[str] = field(default_factory=list)
    is_system: bool = False
    is_deleted: bool = False

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M", time.localtime(self.timestamp))

    @property
    def full_time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))

    @property
    def date_str(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.timestamp))

    @property
    def edited_str(self) -> str:
        if self.edited == 0:
            return ""
        return " (edited)"

    @property
    def reaction_count(self) -> int:
        return sum(r.count for r in self.reactions)

    @property
    def has_attachments(self) -> bool:
        return len(self.attachments) > 0

    @property
    def pin_icon(self) -> str:
        return "📌" if self.pinned else ""

    @property
    def bookmark_icon(self) -> str:
        return "🔖" if self.bookmarked else ""

    @property
    def type_icon(self) -> str:
        icons = {MessageType.TEXT: "", MessageType.FILE: "📎",
                 MessageType.IMAGE: "🖼️", MessageType.CODE: "```",
                 MessageType.SYSTEM: "⚙️", MessageType.LINK: "🔗",
                 MessageType.EMOJI: "", MessageType.THREAD: "🧵"}
        return icons.get(self.message_type, "")


@dataclass
class Channel:
    id: str = ""
    name: str = ""
    channel_type: ChannelType = ChannelType.PUBLIC
    topic: str = ""
    members: List[str] = field(default_factory=list)
    description: str = ""
    created: float = 0.0
    last_message: float = 0.0
    last_message_author: str = ""
    last_message_preview: str = ""
    unread_count: int = 0
    notification_level: NotificationLevel = NotificationLevel.ALL
    is_muted: bool = False
    pinned_messages: int = 0

    @property
    def type_icon(self) -> str:
        return CHANNEL_ICONS.get(self.channel_type, "❓")

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def last_active_str(self) -> str:
        if self.last_message == 0:
            return "No messages"
        delta = time.time() - self.last_message
        if delta < 60:
            return "Just now"
        elif delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h ago"
        return f"{delta / 86400:.0f}d ago"

    @property
    def display(self) -> str:
        unread = f" ({self.unread_count})" if self.unread_count > 0 else ""
        muted = " 🔇" if self.is_muted else ""
        return f"{self.type_icon} {self.name}{unread}{muted}"

    @property
    def notif_icon(self) -> str:
        icons = {NotificationLevel.ALL: "🔔", NotificationLevel.MENTIONS: "💬",
                 NotificationLevel.NONE: "🔕"}
        return icons.get(self.notification_level, "❓")


@dataclass
class Thread:
    parent_id: int = 0
    channel_id: str = ""
    replies: List[Message] = field(default_factory=list)
    participants: List[str] = field(default_factory=list)
    last_reply: float = 0.0

    @property
    def reply_count(self) -> int:
        return len(self.replies)

    @property
    def last_reply_str(self) -> str:
        if self.last_reply == 0:
            return "No replies"
        delta = time.time() - self.last_reply
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        return f"{delta / 3600:.1f}h ago"


@dataclass
class FileShare:
    name: str = ""
    uploaded_by: str = ""
    uploaded_at: float = 0.0
    size_bytes: int = 0
    file_type: str = ""
    channel: str = ""
    downloads: int = 0

    @property
    def size_str(self) -> str:
        b = self.size_bytes
        if b < 1024:
            return f"{b} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        return f"{b / 1024 ** 2:.1f} MB"

    @property
    def time_str(self) -> str:
        delta = time.time() - self.uploaded_at
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h ago"
        return f"{delta / 86400:.0f}d ago"


class ChatApp:
    def __init__(self):
        self.users: List[User] = []
        self.channels: List[Channel] = []
        self.messages: Dict[str, List[Message]] = {}  # channel_id -> messages
        self.threads: List[Thread] = []
        self.files: List[FileShare] = []
        self.current_user: str = "admin"
        self._selected_channel: int = 0
        self._selected_message: int = 0
        self._view_mode: str = "channels"
        self._typing: Dict[str, float] = {}  # channel_id -> timestamp
        self._message_counter: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.users = [
            User("admin", "Admin", "👤", UserStatus.ONLINE, "Building Nyrqis",
                 "admin", now - 60, "admin@nyrqis.dev", "UTC"),
            User("myco", "Myco", "🍄", UserStatus.ONLINE, "Hacking the planet",
                 "admin", now - 120, "myco@nyrqis.dev", "UTC"),
            User("dev", "Dev Bot", "🤖", UserStatus.ONLINE, "Automated CI/CD",
                 "member", now - 30, "dev@nyrqis.dev", "UTC"),
            User("alice", "Alice", "👩‍💻", UserStatus.AWAY, "In a meeting",
                 "member", now - 3600, "alice@nyrqis.dev", "US/Pacific"),
            User("bob", "Bob", "👨‍🔬", UserStatus.BUSY, "Debugging",
                 "member", now - 1800, "bob@nyrqis.dev", "Europe/Berlin"),
            User("charlie", "Charlie", "🧑‍🎨", UserStatus.OFFLINE, "",
                 "member", now - 86400, "charlie@nyrqis.dev", "Asia/Tokyo"),
            User("diana", "Diana", "👩‍🚀", UserStatus.ONLINE, "Reviewing PRs",
                 "moderator", now - 300, "diana@nyrqis.dev", "US/Eastern"),
            User("eve", "Eve", "🕵️", UserStatus.DO_NOT_DISTURB, "Deep focus",
                 "member", now - 600, "eve@nyrqis.dev", "UTC"),
        ]

        self.channels = [
            Channel("general", "general", ChannelType.PUBLIC,
                    "General discussion for Nyrqis OS",
                    ["admin", "myco", "dev", "alice", "bob", "charlie", "diana", "eve"],
                    created=now - 86400 * 90,
                    last_message=now - 120, last_message_author="diana",
                    last_message_preview="Just reviewed the PR, looks good!",
                    unread_count=3, pinned_messages=2),
            Channel("dev", "development", ChannelType.PUBLIC,
                    "Development discussion and code reviews",
                    ["admin", "myco", "dev", "alice", "bob", "diana"],
                    created=now - 86400 * 60,
                    last_message=now - 300, last_message_author="myco",
                    last_message_preview="Merged the compositor fix!",
                    unread_count=7),
            Channel("design", "design", ChannelType.PUBLIC,
                    "UI/UX design discussion",
                    ["admin", "myco", "alice", "charlie", "diana"],
                    created=now - 86400 * 45,
                    last_message=now - 7200, last_message_author="alice",
                    last_message_preview="New mockups for the settings panel",
                    unread_count=0),
            Channel("random", "random", ChannelType.PUBLIC,
                    "Off-topic and fun stuff",
                    ["admin", "myco", "alice", "bob", "charlie", "diana", "eve"],
                    created=now - 86400 * 90,
                    last_message=now - 3600, last_message_author="bob",
                    last_message_preview="Check out this cool algorithm visualization!",
                    unread_count=1, is_muted=True),
            Channel("ops", "operations", ChannelType.PRIVATE,
                    "Infrastructure and deployment",
                    ["admin", "myco", "dev"],
                    created=now - 86400 * 30,
                    last_message=now - 1800, last_message_author="dev",
                    last_message_preview="Deploy v0.1.0-rc1 to staging",
                    unread_count=0),
            Channel("dm-alice", "Alice", ChannelType.DM,
                    members=["admin", "alice"],
                    created=now - 86400 * 20,
                    last_message=now - 600, last_message_author="alice",
                    last_message_preview="Can you review my PR?",
                    unread_count=2),
            Channel("dm-bob", "Bob", ChannelType.DM,
                    members=["admin", "bob"],
                    created=now - 86400 * 15,
                    last_message=now - 86400, last_message_author="bob",
                    last_message_preview="Thanks for the help!",
                    unread_count=0),
        ]

        self.messages["general"] = [
            Message(1, "general", "myco", "Welcome to the Nyrqis OS project! 🍄",
                    MessageType.TEXT, now - 86400 * 90, reactions=[
                        Reaction("🎉", ["alice", "bob", "charlie"]),
                        Reaction("🍄", ["diana", "eve"]),
                    ]),
            Message(2, "general", "admin", "Hey everyone! Excited to be here.",
                    MessageType.TEXT, now - 86400 * 90 + 60),
            Message(3, "general", "alice", "Just joined! Working on the UI components.",
                    MessageType.TEXT, now - 86400 * 30,
                    reactions=[Reaction("👋", ["myco", "admin"])]),
            Message(4, "general", "bob", "Quick question: what's the target for GPU acceleration?",
                    MessageType.TEXT, now - 86400 * 10),
            Message(5, "general", "myco", "Vulkan first, then fall back to EGL/GL if needed.",
                    MessageType.TEXT, now - 86400 * 10 + 300,
                    reactions=[Reaction("👍", ["bob", "admin"])]),
            Message(6, "general", "diana", "Just reviewed the PR, looks good!",
                    MessageType.TEXT, now - 120,
                    reactions=[Reaction("✅", ["myco"])]),
        ]

        self.messages["dev"] = [
            Message(10, "dev", "myco", "Starting work on the Wayland compositor",
                    MessageType.TEXT, now - 86400 * 7),
            Message(11, "dev", "admin", "Great! I'll handle the DRM/KMS backend",
                    MessageType.TEXT, now - 86400 * 7 + 300),
            Message(12, "dev", "dev", "Build #452 passed ✅\n```\nTests: 4060 passed\nCoverage: 87.3%\n```",
                    MessageType.CODE, now - 86400,
                    reactions=[Reaction("🎉", ["admin", "myco", "alice"])]),
            Message(13, "dev", "alice", "New PR ready for review: https://github.com/nyrqis/pull/123",
                    MessageType.LINK, now - 3600),
            Message(14, "dev", "myco", "Merged the compositor fix!",
                    MessageType.TEXT, now - 300,
                    reactions=[Reaction("🚀", ["admin", "alice", "diana"]),
                               Reaction("✅", ["dev"])]),
        ]

        self.messages["dm-alice"] = [
            Message(20, "dm-alice", "alice", "Hey! Can you review my UI PR?",
                    MessageType.TEXT, now - 3600),
            Message(21, "dm-alice", "admin", "Sure, I'll take a look this afternoon.",
                    MessageType.TEXT, now - 3000),
            Message(22, "dm-alice", "alice", "Thanks! It's the new settings panel.",
                    MessageType.TEXT, now - 600),
            Message(23, "dm-alice", "alice", "Can you review my PR?",
                    MessageType.TEXT, now - 600, reply_to=21),
        ]

        self.threads = [
            Thread(5, "general", [
                Message(100, "general", "bob", "What about NVIDIA specifically?",
                        MessageType.TEXT, now - 86400 * 10 + 600),
                Message(101, "general", "myco", "NVK driver for open-source, proprietary as fallback",
                        MessageType.TEXT, now - 86400 * 10 + 900),
                Message(102, "general", "bob", "Makes sense, thanks!",
                        MessageType.TEXT, now - 86400 * 10 + 1200),
            ], ["myco", "bob"], now - 86400 * 10 + 1200),
        ]

        self.files = [
            FileShare("compositor.rs", "myco", now - 86400 * 3, 45000, "code", "dev", 12),
            FileShare("design-mockup.png", "alice", now - 86400 * 2, 2500000, "image", "design", 8),
            FileShare("benchmark-results.csv", "dev", now - 86400, 50000, "text", "dev", 5),
            FileShare("release-notes.md", "admin", now - 43200, 12000, "text", "general", 15),
            FileShare("screenshot-v2.png", "charlie", now - 86400 * 5, 1800000, "image", "design", 3),
        ]

        self._message_counter = 103

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_channel(self) -> Optional[Channel]:
        if 0 <= self._selected_channel < len(self.channels):
            return self.channels[self._selected_channel]
        return None

    def select_channel(self, idx: int):
        if 0 <= idx < len(self.channels):
            self._selected_channel = idx
            ch = self.channels[idx]
            ch.unread_count = 0

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        self._selected_channel = min(self._selected_channel + 1, len(self.channels) - 1)

    def select_up(self):
        self._selected_channel = max(self._selected_channel - 1, 0)

    # ─── Message Actions ───────────────────────────────────────────────

    def send_message(self, channel_id: str, content: str,
                     msg_type: MessageType = MessageType.TEXT) -> Message:
        self._message_counter += 1
        now = time.time()
        msg = Message(self._message_counter, channel_id, self.current_user,
                      content, msg_type, now)
        if channel_id not in self.messages:
            self.messages[channel_id] = []
        self.messages[channel_id].append(msg)

        # Update channel
        for ch in self.channels:
            if ch.id == channel_id:
                ch.last_message = now
                ch.last_message_author = self.current_user
                ch.last_message_preview = content[:100]
                break
        return msg

    def edit_message(self, channel_id: str, msg_id: int, new_content: str) -> bool:
        if channel_id in self.messages:
            for msg in self.messages[channel_id]:
                if msg.id == msg_id and msg.author == self.current_user:
                    msg.content = new_content
                    msg.edited = time.time()
                    return True
        return False

    def delete_message(self, channel_id: str, msg_id: int) -> bool:
        if channel_id in self.messages:
            for msg in self.messages[channel_id]:
                if msg.id == msg_id and msg.author == self.current_user:
                    msg.is_deleted = True
                    return True
        return False

    def pin_message(self, channel_id: str, msg_id: int) -> bool:
        if channel_id in self.messages:
            for msg in self.messages[channel_id]:
                if msg.id == msg_id:
                    msg.pinned = not msg.pinned
                    return True
        return False

    def bookmark_message(self, channel_id: str, msg_id: int) -> bool:
        if channel_id in self.messages:
            for msg in self.messages[channel_id]:
                if msg.id == msg_id:
                    msg.bookmarked = not msg.bookmarked
                    return True
        return False

    def add_reaction(self, channel_id: str, msg_id: int, emoji: str) -> bool:
        if channel_id in self.messages:
            for msg in self.messages[channel_id]:
                if msg.id == msg_id:
                    for r in msg.reactions:
                        if r.emoji == emoji:
                            if self.current_user in r.users:
                                r.users.remove(self.current_user)
                                if not r.users:
                                    msg.reactions.remove(r)
                            else:
                                r.users.append(self.current_user)
                            return True
                    msg.reactions.append(Reaction(emoji, [self.current_user]))
                    return True
        return False

    # ─── Channel Actions ───────────────────────────────────────────────

    def create_channel(self, name: str, channel_type: ChannelType = ChannelType.PUBLIC,
                       topic: str = "") -> Channel:
        ch = Channel(name, name, channel_type, topic,
                     [self.current_user], created=time.time())
        self.channels.append(ch)
        return ch

    def archive_channel(self, idx: int) -> bool:
        if 0 <= idx < len(self.channels):
            self.channels[idx].channel_type = ChannelType.ARCHIVED
            return True
        return False

    def toggle_mute(self, idx: int) -> bool:
        if 0 <= idx < len(self.channels):
            self.channels[idx].is_muted = not self.channels[idx].is_muted
            return True
        return False

    def set_notification(self, idx: int, level: NotificationLevel) -> bool:
        if 0 <= idx < len(self.channels):
            self.channels[idx].notification_level = level
            return True
        return False

    # ─── File Actions ──────────────────────────────────────────────────

    def upload_file(self, name: str, size: int, file_type: str,
                    channel: str) -> FileShare:
        f = FileShare(name, self.current_user, time.time(), size, file_type, channel)
        self.files.append(f)
        return f

    # ─── Search ────────────────────────────────────────────────────────

    def search_messages(self, query: str) -> List[Message]:
        results = []
        q = query.lower()
        for channel_id, msgs in self.messages.items():
            for msg in msgs:
                if not msg.is_deleted and q in msg.content.lower():
                    results.append(msg)
        return sorted(results, key=lambda m: m.timestamp, reverse=True)[:20]

    def search_users(self, query: str) -> List[User]:
        q = query.lower()
        return [u for u in self.users if q in u.username.lower() or q in u.display_name.lower()]

    def search_channels(self, query: str) -> List[Channel]:
        q = query.lower()
        return [c for c in self.channels if q in c.name.lower() or q in c.topic.lower()]

    # ─── Queries ───────────────────────────────────────────────────────

    def get_channel_messages(self, channel_id: str) -> List[Message]:
        return [m for m in self.messages.get(channel_id, []) if not m.is_deleted]

    def get_pinned_messages(self, channel_id: str) -> List[Message]:
        return [m for m in self.messages.get(channel_id, []) if m.pinned and not m.is_deleted]

    def get_bookmarked_messages(self) -> List[Message]:
        results = []
        for msgs in self.messages.values():
            for m in msgs:
                if m.bookmarked and not m.is_deleted:
                    results.append(m)
        return sorted(results, key=lambda m: m.timestamp, reverse=True)

    def get_online_users(self) -> List[User]:
        return [u for u in self.users if u.status == UserStatus.ONLINE]

    def get_unread_channels(self) -> List[Channel]:
        return [c for c in self.channels if c.unread_count > 0]

    def get_total_unread(self) -> int:
        return sum(c.unread_count for c in self.channels)

    def get_stats(self) -> Dict:
        return {
            "users": len(self.users),
            "online": len(self.get_online_users()),
            "channels": len(self.channels),
            "total_messages": sum(len(msgs) for msgs in self.messages.values()),
            "threads": len(self.threads),
            "files": len(self.files),
            "total_unread": self.get_total_unread(),
        }
