"""
Notification Center — manage, group, and display notifications for Nyrqis OS.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


# ─── Enums ───────────────────────────────────────────────────────────────

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
    SOCIAL = "social"
    REMINDER = "reminder"
    UPDATE = "update"
    OTHER = "other"


class NotificationStatus(Enum):
    NEW = "new"
    READ = "read"
    DISMISSED = "dismissed"
    PINNED = "pinned"
    SNOOZED = "snoozed"


# ─── Data classes ────────────────────────────────────────────────────────

@dataclass
class Notification:
    title: str = ""
    body: str = ""
    app: str = ""
    category: NotificationCategory = NotificationCategory.OTHER
    priority: NotificationPriority = NotificationPriority.NORMAL
    status: NotificationStatus = NotificationStatus.NEW
    timestamp: float = 0.0
    notification_id: int = 0

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
        return icons.get(self.priority, "❓")

    @property
    def category_icon(self) -> str:
        icons = {
            NotificationCategory.SYSTEM: "⚙️",
            NotificationCategory.MESSAGE: "💬",
            NotificationCategory.EMAIL: "📧",
            NotificationCategory.CALENDAR: "📅",
            NotificationCategory.SOCIAL: "👥",
            NotificationCategory.REMINDER: "⏰",
            NotificationCategory.UPDATE: "🔄",
            NotificationCategory.OTHER: "📌",
        }
        return icons.get(self.category, "❓")

    @property
    def status_icon(self) -> str:
        icons = {
            NotificationStatus.NEW: "●",
            NotificationStatus.READ: "○",
            NotificationStatus.DISMISSED: "×",
            NotificationStatus.PINNED: "📌",
            NotificationStatus.SNOOZED: "💤",
        }
        return icons.get(self.status, "?")

    @property
    def time_ago(self) -> str:
        delta = time.time() - self.timestamp
        if delta < 60:
            return "just now"
        elif delta < 3600:
            return f"{delta/60:.0f}m ago"
        elif delta < 86400:
            return f"{delta/3600:.0f}h ago"
        return f"{delta/86400:.0f}d ago"

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.priority_icon} {self.title}"

    @property
    def preview(self) -> str:
        return f"{self.app}: {self.body[:50]}"

    @property
    def is_active(self) -> bool:
        return self.status == NotificationStatus.NEW


@dataclass
class NotificationGroup:
    app: str = ""
    count: int = 0
    notifications: List[Notification] = field(default_factory=list)


@dataclass
class NotificationStats:
    total: int = 0
    unread: int = 0
    pinned: int = 0
    dismissed: int = 0


@dataclass
class DNDschedule:
    enabled: bool = False
    start_hour: int = 22
    end_hour: int = 7
    days: List[str] = field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])

    @property
    def time_display(self) -> str:
        return f"{self.start_hour:02d}:00 - {self.end_hour:02d}:00"

    @property
    def days_display(self) -> str:
        return ", ".join(self.days)

    @property
    def status_icon(self) -> str:
        return "🌙" if self.enabled else "☀️"


@dataclass
class AppNotificationSettings:
    app: str = ""
    enabled: bool = True
    count: int = 0

    @property
    def display(self) -> str:
        status = "✅" if self.enabled else "❌"
        return f"{status} {self.app} [{self.count}]"


@dataclass
class NotificationAction:
    label: str = ""
    action: str = ""
    icon: str = ""


# ─── Notification Center ─────────────────────────────────────────────────

class NotificationCenter:
    """Main notification center with views, groups, and DND."""

    def __init__(self):
        self.view_mode: str = "notifications"
        self._selected_index: int = 0
        self.notifications: List[Notification] = []
        self.dnd_enabled: bool = False
        self._dnd_schedule: DNDschedule = DNDschedule()
        self._app_settings: List[AppNotificationSettings] = []
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.notifications = [
            Notification("System Update Available", "Nyrqis 2.1 is ready to install", "System",
                         NotificationCategory.UPDATE, NotificationPriority.HIGH, NotificationStatus.NEW,
                         timestamp=now - 300, notification_id=0),
            Notification("New Message from Alice", "Hey, are you free for a call?", "Messages",
                         NotificationCategory.MESSAGE, NotificationPriority.NORMAL, NotificationStatus.NEW,
                         timestamp=now - 600, notification_id=1),
            Notification("Build Failed", "CI pipeline #2847 failed on main", "GitHub",
                         NotificationCategory.SYSTEM, NotificationPriority.HIGH, NotificationStatus.READ,
                         timestamp=now - 1800, notification_id=2),
            Notification("Calendar Reminder", "Team standup in 15 minutes", "Calendar",
                         NotificationCategory.CALENDAR, NotificationPriority.NORMAL, NotificationStatus.NEW,
                         timestamp=now - 900, notification_id=3),
            Notification("Disk Space Warning", "/home is 85% full", "System",
                         NotificationCategory.SYSTEM, NotificationPriority.URGENT, NotificationStatus.PINNED,
                         timestamp=now - 3600, notification_id=4),
        ]

        self._app_settings = [
            AppNotificationSettings("System", True, 3),
            AppNotificationSettings("Messages", True, 1),
            AppNotificationSettings("GitHub", False, 0),
            AppNotificationSettings("Calendar", True, 1),
        ]

    @property
    def unread_count(self) -> int:
        return sum(1 for n in self.notifications
                   if n.status in (NotificationStatus.NEW,))

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def add_notification(self, title: str, body: str = "", app: str = "") -> Notification:
        n = Notification(title=title, body=body, app=app,
                         notification_id=len(self.notifications))
        self.notifications.insert(0, n)
        return n

    def dismiss(self, index: int) -> bool:
        if 0 <= index < len(self.notifications):
            self.notifications[index].status = NotificationStatus.DISMISSED
            return True
        return False

    def dismiss_all(self) -> int:
        count = 0
        for n in self.notifications:
            if n.status != NotificationStatus.PINNED:
                n.status = NotificationStatus.DISMISSED
                count += 1
        return count

    def mark_read(self, index: int) -> bool:
        if 0 <= index < len(self.notifications):
            self.notifications[index].status = NotificationStatus.READ
            return True
        return False

    def pin(self, index: int) -> bool:
        if 0 <= index < len(self.notifications):
            self.notifications[index].status = NotificationStatus.PINNED
            return True
        return False

    def snooze(self, index: int, minutes: int = 30) -> bool:
        if 0 <= index < len(self.notifications):
            self.notifications[index].status = NotificationStatus.SNOOZED
            return True
        return False

    def toggle_dnd(self) -> bool:
        self.dnd_enabled = not self.dnd_enabled
        self._dnd_schedule.enabled = self.dnd_enabled
        return self.dnd_enabled

    def get_groups(self) -> List[NotificationGroup]:
        groups: Dict[str, NotificationGroup] = {}
        for n in self.notifications:
            if n.app not in groups:
                groups[n.app] = NotificationGroup(app=n.app)
            groups[n.app].count += 1
            groups[n.app].notifications.append(n)
        return list(groups.values())

    def set_view(self, view: str):
        self.view_mode = view
        self._selected_index = 0

    def select_down(self):
        if self._selected_index < len(self.notifications) - 1:
            self._selected_index += 1

    def select_up(self):
        if self._selected_index > 0:
            self._selected_index -= 1

    def handle_key(self, key: str) -> str:
        if key == "d":
            self.dnd_enabled = True
            return "dnd_on"
        if key == "x":
            self.dnd_enabled = True
            return "dnd_on"
        if key == "ArrowDown":
            self.select_down()
            return "navigate"
        if key == "ArrowUp":
            self.select_up()
            return "navigate"
        if key == "Escape":
            self.set_view("notifications")
            return "close"
        return "unknown"

    def render_notifications(self) -> List[str]:
        lines = ["── Notifications ──"]
        for i, n in enumerate(self.notifications):
            marker = "▸ " if i == self._selected_index else "  "
            lines.append(f"{marker}{n.display} ({n.time_ago})")
        if not self.notifications:
            lines.append("  No notifications.")
        return lines

    def render_history(self) -> List[str]:
        lines = ["── History ──"]
        for n in self.notifications:
            if n.status == NotificationStatus.READ:
                lines.append(f"  {n.display} ({n.time_ago})")
        if len(lines) == 1:
            lines.append("  No read notifications.")
        return lines

    def render_apps(self) -> List[str]:
        lines = ["── App Settings ──"]
        for s in self._app_settings:
            lines.append(f"  {s.display}")
        return lines

    # ─── Legacy API ──────────────────────────────────────────────────

    @property
    def stats(self) -> NotificationStats:
        return NotificationStats(
            total=len(self.notifications),
            unread=self.unread_count,
            pinned=sum(1 for n in self.notifications if n.status == NotificationStatus.PINNED),
            dismissed=sum(1 for n in self.notifications if n.status == NotificationStatus.DISMISSED),
        )

    def search(self, query: str) -> List[Notification]:
        q = query.lower()
        return [n for n in self.notifications if q in n.title.lower() or q in n.body.lower()]

    def filter_by_category(self, category: NotificationCategory) -> List[Notification]:
        return [n for n in self.notifications if n.category == category]

    def filter_by_priority(self, priority: NotificationPriority) -> List[Notification]:
        return [n for n in self.notifications if n.priority == priority]

    def get_unread(self) -> List[Notification]:
        return [n for n in self.notifications if n.status == NotificationStatus.NEW]

    def mark_all_read(self) -> int:
        count = 0
        for n in self.notifications:
            if n.status == NotificationStatus.NEW:
                n.status = NotificationStatus.READ
                count += 1
        return count

    def clear_all(self) -> int:
        return self.dismiss_all()

    def get_stats(self) -> NotificationStats:
        return self.stats
