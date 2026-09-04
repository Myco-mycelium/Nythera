"""
Nyrqis OS - Notification Center
Message history, priority levels, and do-not-disturb scheduling.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class NotificationPriority(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class NotificationCategory(Enum):
    SYSTEM = "system"
    MESSAGE = "message"
    EMAIL = "email"
    CALENDAR = "calendar"
    WEATHER = "weather"
    MEDIA = "media"
    DOWNLOAD = "download"
    SECURITY = "security"
    UPDATE = "update"
    SOCIAL = "social"


class NotificationStatus(Enum):
    NEW = "new"
    READ = "read"
    DISMISSED = "dismissed"
    PINNED = "pinned"


@dataclass
class Notification:
    id: int = 0
    title: str = ""
    body: str = ""
    app_name: str = ""
    app_icon: str = ""
    category: NotificationCategory = NotificationCategory.SYSTEM
    priority: NotificationPriority = NotificationPriority.NORMAL
    status: NotificationStatus = NotificationStatus.NEW
    timestamp: float = 0.0
    actions: List[str] = field(default_factory=list)
    sound: bool = True
    persistent: bool = False
    dismiss_timeout_s: int = 5

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def priority_icon(self) -> str:
        icons = {
            NotificationPriority.LOW: "🔵",
            NotificationPriority.NORMAL: "🟢",
            NotificationPriority.HIGH: "🟠",
            NotificationPriority.URGENT: "🔴",
        }
        return icons.get(self.priority, "?")

    @property
    def category_icon(self) -> str:
        icons = {
            NotificationCategory.SYSTEM: "⚙️", NotificationCategory.MESSAGE: "💬",
            NotificationCategory.EMAIL: "📧", NotificationCategory.CALENDAR: "📅",
            NotificationCategory.WEATHER: "🌤️", NotificationCategory.MEDIA: "🎵",
            NotificationCategory.DOWNLOAD: "📥", NotificationCategory.SECURITY: "🔐",
            NotificationCategory.UPDATE: "🔄", NotificationCategory.SOCIAL: "👥",
        }
        return icons.get(self.category, "?")

    @property
    def status_icon(self) -> str:
        icons = {
            NotificationStatus.NEW: "🔵", NotificationStatus.READ: "⚪",
            NotificationStatus.DISMISSED: "⬜", NotificationStatus.PINNED: "📌",
        }
        return icons.get(self.status, "?")

    @property
    def time_ago(self) -> str:
        delta = time.time() - self.timestamp
        if delta < 60:
            return f"{delta:.0f}s ago"
        elif delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.0f}h ago"
        return f"{delta / 86400:.0f}d ago"


@dataclass
class DNDschedule:
    name: str
    enabled: bool = True
    start_hour: int = 22
    start_minute: int = 0
    end_hour: int = 7
    end_minute: int = 0
    days: List[str] = field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"])
    allow_urgent: bool = True
    allow_calls: bool = True
    allow_repeated: bool = False
    repeat_threshold: int = 3

    @property
    def time_display(self) -> str:
        return f"{self.start_hour:02d}:{self.start_minute:02d} - {self.end_hour:02d}:{self.end_minute:02d}"

    @property
    def days_display(self) -> str:
        if len(self.days) == 5 and all(d in self.days for d in ["Mon", "Tue", "Wed", "Thu", "Fri"]):
            return "Weekdays"
        if len(self.days) == 7:
            return "Every day"
        return ", ".join(self.days)

    @property
    def status_icon(self) -> str:
        return "🟢" if self.enabled else "⚪"


@dataclass
class NotificationGroup:
    app_name: str
    icon: str = ""
    count: int = 0
    latest: Optional[Notification] = None
    muted: bool = False
    priority_override: Optional[NotificationPriority] = None


@dataclass
class NotificationStats:
    total: int = 0
    unread: int = 0
    today: int = 0
    by_category: Dict[str, int] = field(default_factory=dict)
    by_priority: Dict[str, int] = field(default_factory=dict)


class NotificationCenter:
    def __init__(self):
        self.notifications: List[Notification] = []
        self.dnd_schedules: List[DNDschedule] = []
        self.groups: List[NotificationGroup] = []
        self.dnd_active: bool = False
        self.global_mute: bool = False
        self.show_previews: bool = True
        self.sound_enabled: bool = True
        self.max_notifications: int = 200
        self.auto_dismiss_seconds: int = 5
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        sample_notifications = [
            (1, "System Update Available", "Nyrqis OS 0.2.0 is ready to install", "nyrqis-update",
             "🔄", NotificationCategory.UPDATE, NotificationPriority.HIGH,
             ["Install Now", "Remind Later", "Skip"], now - 300),
            (2, "New Message from Alice", "Hey, did you see the new Nyrqis build?", "chat",
             "💬", NotificationCategory.MESSAGE, NotificationPriority.NORMAL,
             ["Reply", "Mark Read"], now - 600),
            (3, "Security Alert", "New login from 10.0.0.5 at 09:15 AM", "security",
             "🔐", NotificationCategory.SECURITY, NotificationPriority.URGENT,
             ["View Details", "Block IP"], now - 900),
            (4, "Weather Update", "Partly cloudy, 22°C today", "weather",
             "🌤️", NotificationCategory.WEATHER, NotificationPriority.LOW,
             [], now - 1200),
            (5, "Download Complete", "nyrqis-kernel-1.0.0-rc1.tar.gz (45 MB)", "download",
             "📥", NotificationCategory.DOWNLOAD, NotificationPriority.NORMAL,
             ["Open File", "Show in Folder"], now - 1800),
            (6, "Calendar Reminder", "Team standup in 15 minutes", "calendar",
             "📅", NotificationCategory.CALENDAR, NotificationPriority.HIGH,
             ["Dismiss", "Snooze 10m"], now - 2400),
            (7, "Spotify", "Now playing: M83 - Midnight City", "media",
             "🎵", NotificationCategory.MEDIA, NotificationPriority.LOW,
             ["Next", "Pause"], now - 3000),
            (8, "Package Update", "3 packages can be updated", "package",
             "📦", NotificationCategory.SYSTEM, NotificationPriority.NORMAL,
             ["View Updates"], now - 3600),
            (9, "Memory Warning", "Memory usage at 85%. Consider closing unused applications.",
             "system", "⚠️", NotificationCategory.SYSTEM, NotificationPriority.HIGH,
             ["View Processes"], now - 4200),
            (10, "GitHub", "Nyrqis/Nythera: 3 new issues assigned to you", "social",
             "👥", NotificationCategory.SOCIAL, NotificationPriority.NORMAL,
             ["View Issues", "Dismiss"], now - 4800),
            (11, "Email from bob@nyrqis.dev", "Re: Architecture Review - Approved", "email",
             "📧", NotificationCategory.EMAIL, NotificationPriority.NORMAL,
             ["Reply", "Archive"], now - 5400),
            (12, "System", "Backup completed successfully", "system",
             "⚙️", NotificationCategory.SYSTEM, NotificationPriority.LOW,
             [], now - 7200),
            (13, "Battery", "Battery level below 15%. Connecting to power recommended.",
             "system", "🔋", NotificationCategory.SYSTEM, NotificationPriority.HIGH,
             ["Power Settings"], now - 7800),
            (14, "Git Push", "Successfully pushed 5 commits to main", "terminal",
             "📤", NotificationCategory.SYSTEM, NotificationPriority.LOW,
             [], now - 8400),
            (15, "Printer", "Nyrqis Lab Printer is now available", "system",
             "🖨️", NotificationCategory.SYSTEM, NotificationPriority.LOW,
             [], now - 9000),
        ]

        for (nid, title, body, app, icon, cat, prio, actions, ts) in sample_notifications:
            status = random.choice([NotificationStatus.NEW, NotificationStatus.NEW,
                                     NotificationStatus.READ, NotificationStatus.READ,
                                     NotificationStatus.PINNED])
            self.notifications.append(Notification(
                id=nid, title=title, body=body, app_name=app, app_icon=icon,
                category=cat, priority=prio, status=status, timestamp=ts,
                actions=actions))

        self.dnd_schedules = [
            DNDschedule(name="Night Mode", enabled=True,
                         start_hour=22, end_hour=7, days=["Mon", "Tue", "Wed", "Thu", "Fri"],
                         allow_urgent=True, allow_calls=False),
            DNDschedule(name="Meeting Hours", enabled=False,
                         start_hour=14, start_minute=0, end_hour=15, end_minute=0,
                         days=["Mon", "Wed", "Fri"], allow_urgent=True,
                         allow_calls=False, allow_repeated=True, repeat_threshold=2),
            DNDschedule(name="Focus Time", enabled=False,
                         start_hour=9, end_hour=12,
                         days=["Tue", "Thu"], allow_urgent=True),
        ]

        for notif in self.notifications:
            group = next((g for g in self.groups if g.app_name == notif.app_name), None)
            if not group:
                self.groups.append(NotificationGroup(
                    app_name=notif.app_name, icon=notif.app_icon,
                    count=1, latest=notif))
            else:
                group.count += 1

    def add_notification(self, notification: Notification) -> None:
        self.notifications.insert(0, notification)
        if len(self.notifications) > self.max_notifications:
            self.notifications = self.notifications[:self.max_notifications]

    def dismiss(self, notification_id: int) -> bool:
        notif = next((n for n in self.notifications if n.id == notification_id), None)
        if notif:
            notif.status = NotificationStatus.DISMISSED
            return True
        return False

    def mark_read(self, notification_id: int) -> bool:
        notif = next((n for n in self.notifications if n.id == notification_id), None)
        if notif:
            notif.status = NotificationStatus.READ
            return True
        return False

    def mark_all_read(self) -> int:
        count = 0
        for notif in self.notifications:
            if notif.status == NotificationStatus.NEW:
                notif.status = NotificationStatus.READ
                count += 1
        return count

    def pin(self, notification_id: int) -> bool:
        notif = next((n for n in self.notifications if n.id == notification_id), None)
        if notif:
            notif.status = NotificationStatus.PINNED
            return True
        return False

    def clear_all(self) -> int:
        before = len(self.notifications)
        self.notifications = [n for n in self.notifications if n.status == NotificationStatus.PINNED]
        return before - len(self.notifications)

    def search(self, query: str) -> List[Notification]:
        q = query.lower()
        return [n for n in self.notifications if q in n.title.lower() or q in n.body.lower()]

    def filter_by_category(self, category: NotificationCategory) -> List[Notification]:
        return [n for n in self.notifications if n.category == category]

    def filter_by_priority(self, priority: NotificationPriority) -> List[Notification]:
        return [n for n in self.notifications if n.priority == priority]

    def get_unread(self) -> List[Notification]:
        return [n for n in self.notifications if n.status == NotificationStatus.NEW]

    def get_stats(self) -> NotificationStats:
        today_start = time.time() - 86400
        today_notifs = [n for n in self.notifications if n.timestamp >= today_start]
        by_cat = {}
        by_prio = {}
        for n in self.notifications:
            by_cat[n.category.value] = by_cat.get(n.category.value, 0) + 1
            by_prio[n.priority.value] = by_prio.get(n.priority.value, 0) + 1
        return NotificationStats(
            total=len(self.notifications),
            unread=len(self.get_unread()),
            today=len(today_notifs),
            by_category=by_cat, by_priority=by_prio)
