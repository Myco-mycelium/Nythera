"""
Nyrqis Notification Center — notification management application.

Features:
- Real-time notification display with priority levels
- Notification history with search and filtering
- Quick actions (reply, dismiss, snooze, pin)
- Per-app notification settings
- Do Not Disturb mode
- Notification grouping by app
- Sound and vibration settings
- Badge counts per application
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class NotificationPriority(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class NotificationStatus(Enum):
    NEW = "new"
    READ = "read"
    DISMISSED = "dismissed"
    PINNED = "pinned"
    SNOOZED = "snoozed"


PRIORITY_ICONS = {
    NotificationPriority.LOW: "💤",
    NotificationPriority.NORMAL: "🔔",
    NotificationPriority.HIGH: "📢",
    NotificationPriority.URGENT: "🚨",
}

STATUS_ICONS = {
    NotificationStatus.NEW: "🔵",
    NotificationStatus.READ: "⚪",
    NotificationStatus.DISMISSED: "✔️",
    NotificationStatus.PINNED: "📌",
    NotificationStatus.SNOOZED: "⏰",
}


@dataclass
class NotificationAction:
    """A quick action for a notification."""
    label: str
    action_id: str
    icon: str = "→"


@dataclass
class Notification:
    """A single notification."""
    title: str
    body: str = ""
    app: str = "System"
    priority: NotificationPriority = NotificationPriority.NORMAL
    status: NotificationStatus = NotificationStatus.NEW
    icon: str = "🔔"
    # Actions
    actions: List[NotificationAction] = field(default_factory=list)
    # Metadata
    timestamp: float = field(default_factory=time.time)
    group_id: str = ""
    reply_to: str = ""
    notif_id: str = ""
    # Snooze
    snoozed_until: float = 0.0
    # Source
    source_url: str = ""
    image_url: str = ""

    def __post_init__(self):
        if not self.notif_id:
            self.notif_id = hashlib.md5(f"{self.title}{self.timestamp}".encode()).hexdigest()[:8]
        if not self.group_id:
            self.group_id = self.app

    @property
    def priority_icon(self) -> str:
        return PRIORITY_ICONS.get(self.priority, "🔔")

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def display(self) -> str:
        return f"{self.priority_icon} {self.title}"

    @property
    def preview(self) -> str:
        body = self.body[:60] + "..." if len(self.body) > 60 else self.body
        return f"{self.app}: {body}"

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.timestamp).strftime("%b %d")

    @property
    def is_snoozed(self) -> bool:
        return self.status == NotificationStatus.SNOOZED and time.time() < self.snoozed_until

    @property
    def is_active(self) -> bool:
        return self.status in (NotificationStatus.NEW, NotificationStatus.PINNED, NotificationStatus.SNOOZED)


@dataclass
class AppNotificationSettings:
    """Per-app notification settings."""
    app_name: str
    enabled: bool = True
    sound: bool = True
    badge: bool = True
    priority_override: Optional[NotificationPriority] = None
    icon: str = "📱"
    count: int = 0  # badge count

    @property
    def display(self) -> str:
        status = "🟢" if self.enabled else "🔴"
        badge = f" [{self.count}]" if self.count > 0 and self.badge else ""
        return f"{status} {self.icon} {self.app_name}{badge}"


@dataclass
class NotificationGroup:
    """A group of notifications from the same app."""
    app: str
    notifications: List[Notification] = field(default_factory=list)
    icon: str = "📱"

    @property
    def count(self) -> int:
        return len(self.notifications)

    @property
    def unread_count(self) -> int:
        return sum(1 for n in self.notifications if n.status == NotificationStatus.NEW)

    @property
    def display(self) -> str:
        return f"{self.icon} {self.app} ({self.count})"


# ─── Notification Center ─────────────────────────────────────────────────


class NotificationCenter:
    """
    Notification management for Nyrqis OS.
    """

    def __init__(self):
        self._notifications: List[Notification] = []
        self._app_settings: Dict[str, AppNotificationSettings] = {}
        self._dnd_enabled: bool = False
        self._dnd_until: float = 0.0
        self._selected_index: int = 0
        self._view_mode: str = "notifications"  # notifications, history, apps, settings
        self._filter_priority: Optional[NotificationPriority] = None
        self._filter_app: Optional[str] = None
        self._show_only_unread: bool = False
        self._search_query: str = ""

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        self._notifications = [
            Notification("New commit pushed", "3fc01b7 Add Maps app, QR tools",
                         "GitHub", NotificationPriority.NORMAL,
                         actions=[NotificationAction("Open", "open", "🔗"),
                                  NotificationAction("Dismiss", "dismiss", "✖️")],
                         timestamp=now - 120),
            Notification("Build completed", "All 2898 tests passed ✅",
                         "Nyrqis CI", NotificationPriority.HIGH,
                         actions=[NotificationAction("View", "view", "👀"),
                                  NotificationAction("Rerun", "rerun", "🔄")],
                         timestamp=now - 300),
            Notification("System update available", "nyrqis-kernel 6.12.0 is ready to install",
                         "System", NotificationPriority.HIGH,
                         actions=[NotificationAction("Install", "install", "⬇️"),
                                  NotificationAction("Later", "later", "⏰")],
                         timestamp=now - 1800),
            Notification("Disk space low", "/home is 87% full (14.2 GB remaining)",
                         "Disk Monitor", NotificationPriority.URGENT,
                         actions=[NotificationAction("Clean", "clean", "🧹"),
                                  NotificationAction("Details", "details", "📊")],
                         timestamp=now - 3600),
            Notification("Meeting in 15 minutes", "Sprint planning — Conference Room B",
                         "Calendar", NotificationPriority.HIGH,
                         actions=[NotificationAction("Join", "join", "📹"),
                                  NotificationAction("Snooze", "snooze", "⏰")],
                         timestamp=now - 600, status=NotificationStatus.NEW),
            Notification("SSH login detected", "User root from 203.0.113.50 at 14:32",
                         "Security", NotificationPriority.URGENT,
                         actions=[NotificationAction("Block", "block", "🚫"),
                                  NotificationAction("Allow", "allow", "✅")],
                         timestamp=now - 7200),
            Notification("Backup completed", "System backup saved to /backup/nyrqis-2024-09-03.tar.gz",
                         "Backup", NotificationPriority.LOW,
                         timestamp=now - 14400),
            Notification("CPU temperature warning", "CPU reached 85°C during compilation",
                         "Hardware Monitor", NotificationPriority.HIGH,
                         actions=[NotificationAction("Details", "details", "📊")],
                         timestamp=now - 28800),
            Notification("Package updated", "firefox updated to 130.0",
                         "Package Manager", NotificationPriority.LOW,
                         timestamp=now - 43200),
            Notification("Git PR merged", "PR #42: Add mind map editor has been merged",
                         "GitHub", NotificationPriority.NORMAL,
                         actions=[NotificationAction("View", "view", "🔗")],
                         timestamp=now - 86400),
        ]
        # Mark some as read
        for n in self._notifications[7:]:
            n.status = NotificationStatus.READ

        # App settings
        self._app_settings = {
            "System": AppNotificationSettings("System", True, True, True, icon="⚙️",
                                               count=2),
            "GitHub": AppNotificationSettings("GitHub", True, True, True, icon="🐙",
                                               count=1),
            "Calendar": AppNotificationSettings("Calendar", True, True, True, icon="📅",
                                                 count=1),
            "Security": AppNotificationSettings("Security", True, True, True, icon="🔒",
                                                 count=1),
            "Package Manager": AppNotificationSettings("Package Manager", True, False, True, icon="📦"),
            "Disk Monitor": AppNotificationSettings("Disk Monitor", True, True, True, icon="💾",
                                                     count=1),
            "Hardware Monitor": AppNotificationSettings("Hardware Monitor", True, True, True, icon="🌡️"),
            "Backup": AppNotificationSettings("Backup", True, False, True, icon="💾"),
            "Nyrqis CI": AppNotificationSettings("Nyrqis CI", True, True, True, icon="🔨"),
        }

    # ── Notification Operations ───────────────────────────────────────

    def add_notification(self, title: str, body: str, app: str = "System",
                         priority: NotificationPriority = NotificationPriority.NORMAL,
                         actions: Optional[List[NotificationAction]] = None) -> Notification:
        settings = self._app_settings.get(app, AppNotificationSettings(app))
        if not settings.enabled:
            return None

        notif = Notification(
            title=title, body=body, app=app,
            priority=priority,
            actions=actions or [],
            icon=settings.icon,
        )
        self._notifications.insert(0, notif)
        settings.count += 1
        return notif

    def dismiss(self, index: int = -1) -> bool:
        idx = index if index >= 0 else self._selected_index
        notifs = self._get_filtered_notifications()
        if 0 <= idx < len(notifs):
            notif = notifs[idx]
            notif.status = NotificationStatus.DISMISSED
            settings = self._app_settings.get(notif.app)
            if settings and settings.count > 0:
                settings.count -= 1
            return True
        return False

    def mark_read(self, index: int = -1) -> bool:
        idx = index if index >= 0 else self._selected_index
        notifs = self._get_filtered_notifications()
        if 0 <= idx < len(notifs):
            notif = notifs[idx]
            if notif.status == NotificationStatus.NEW:
                notif.status = NotificationStatus.READ
                settings = self._app_settings.get(notif.app)
                if settings and settings.count > 0:
                    settings.count -= 1
            return True
        return False

    def pin(self, index: int = -1) -> bool:
        idx = index if index >= 0 else self._selected_index
        notifs = self._get_filtered_notifications()
        if 0 <= idx < len(notifs):
            notif = notifs[idx]
            notif.status = NotificationStatus.PINNED
            return True
        return False

    def snooze(self, index: int = -1, minutes: int = 30) -> bool:
        idx = index if index >= 0 else self._selected_index
        notifs = self._get_filtered_notifications()
        if 0 <= idx < len(notifs):
            notif = notifs[idx]
            notif.status = NotificationStatus.SNOOZED
            notif.snoozed_until = time.time() + minutes * 60
            return True
        return False

    def dismiss_all(self) -> int:
        count = 0
        for notif in self._notifications:
            if notif.status == NotificationStatus.NEW:
                notif.status = NotificationStatus.DISMISSED
                count += 1
        for settings in self._app_settings.values():
            settings.count = 0
        return count

    # ── DND Operations ────────────────────────────────────────────────

    def toggle_dnd(self) -> bool:
        self._dnd_enabled = not self._dnd_enabled
        if self._dnd_enabled:
            self._dnd_until = time.time() + 3600  # 1 hour default
        else:
            self._dnd_until = 0.0
        return self._dnd_enabled

    @property
    def dnd_enabled(self) -> bool:
        if self._dnd_enabled and time.time() >= self._dnd_until:
            self._dnd_enabled = False
        return self._dnd_enabled

    # ── Filtering ─────────────────────────────────────────────────────

    def set_filter_priority(self, priority: Optional[NotificationPriority]) -> None:
        self._filter_priority = priority
        self._selected_index = 0

    def set_filter_app(self, app: Optional[str]) -> None:
        self._filter_app = app
        self._selected_index = 0

    def toggle_unread_only(self) -> bool:
        self._show_only_unread = not self._show_only_unread
        self._selected_index = 0
        return self._show_only_unread

    def set_search(self, query: str) -> None:
        self._search_query = query
        self._selected_index = 0

    def _get_filtered_notifications(self) -> List[Notification]:
        notifs = list(self._notifications)
        if self._filter_priority:
            notifs = [n for n in notifs if n.priority == self._filter_priority]
        if self._filter_app:
            notifs = [n for n in notifs if n.app == self._filter_app]
        if self._show_only_unread:
            notifs = [n for n in notifs if n.status == NotificationStatus.NEW]
        if self._search_query:
            q = self._search_query.lower()
            notifs = [n for n in notifs
                      if q in n.title.lower() or q in n.body.lower() or q in n.app.lower()]
        return notifs

    def get_groups(self) -> List[NotificationGroup]:
        groups: Dict[str, NotificationGroup] = {}
        for notif in self._notifications:
            if notif.app not in groups:
                settings = self._app_settings.get(notif.app, AppNotificationSettings(notif.app))
                groups[notif.app] = NotificationGroup(notif.app, icon=settings.icon)
            groups[notif.app].notifications.append(notif)
        return sorted(groups.values(), key=lambda g: g.count, reverse=True)

    # ── Navigation ────────────────────────────────────────────────────

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        notifs = self._get_display_list()
        self._selected_index = min(len(notifs) - 1, self._selected_index + 1)

    def get_selected_item(self):
        notifs = self._get_display_list()
        if 0 <= self._selected_index < len(notifs):
            return notifs[self._selected_index]
        return None

    def _get_display_list(self) -> list:
        if self._view_mode == "notifications":
            return self._get_filtered_notifications()
        elif self._view_mode == "history":
            return [n for n in self._notifications if n.status != NotificationStatus.DISMISSED]
        elif self._view_mode == "apps":
            return list(self._app_settings.values())
        return []

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def notifications(self) -> List[Notification]:
        return list(self._notifications)

    @property
    def unread_count(self) -> int:
        return sum(1 for n in self._notifications if n.status == NotificationStatus.NEW)

    @property
    def active_count(self) -> int:
        return sum(1 for n in self._notifications if n.is_active)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    # ── Rendering ─────────────────────────────────────────────────────

    def render_notifications(self, width: int = 70) -> List[str]:
        lines = []
        dnd = " 🔕 DND" if self.dnd_enabled else ""
        lines.append(f" 🔔 Notifications ({self.unread_count} unread){dnd}")
        lines.append("─" * width)

        notifs = self._get_filtered_notifications()
        if not notifs:
            lines.append("  No notifications.")
        else:
            for i, notif in enumerate(notifs[:15]):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {notif.status_icon} {notif.display}")
                lines.append(f"   📱 {notif.app} · {notif.time_ago}")
                if notif.body:
                    lines.append(f"   {notif.body[:width - 5]}")
                if notif.actions:
                    action_str = " ".join(f"[{a.icon} {a.label}]" for a in notif.actions)
                    lines.append(f"   {action_str}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Read  D:Dismiss  P:Pin  S:Snooze")
        lines.append(" A:Apps  H:History  F:Filter  X:DND  Shift+D:Dismiss all")
        return lines

    def render_history(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 📜 Notification History ({len(self._notifications)})")
        lines.append("─" * width)

        notifs = self._get_display_list()
        for i, notif in enumerate(notifs[:20]):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {notif.priority_icon} {notif.status_icon} {notif.title}")
            lines.append(f"   {notif.app} · {notif.time_ago}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render_apps(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📱 Application Notifications")
        lines.append("─" * width)

        for i, (name, settings) in enumerate(self._app_settings.items()):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {settings.display}")
            lines.append(f"   Sound: {'✅' if settings.sound else '❌'} | Badge: {'✅' if settings.badge else '❌'}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Toggle  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "history": self.render_history,
            "apps": self.render_apps,
        }
        renderer = renderers.get(self._view_mode, self.render_notifications)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "history":
            return self._handle_history_key(key)
        elif self._view_mode == "apps":
            return self._handle_apps_key(key)
        return self._handle_notifications_key(key)

    def _handle_notifications_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            return "read" if self.mark_read() else "read_failed"
        elif key == "d":
            return "dismiss" if self.dismiss() else "dismiss_failed"
        elif key == "p":
            return "pin" if self.pin() else "pin_failed"
        elif key == "s":
            return "snooze" if self.snooze() else "snooze_failed"
        elif key == "a":
            self.set_view("apps")
            return "apps"
        elif key == "h":
            self.set_view("history")
            return "history"
        elif key == "x":
            return "dnd_on" if self.toggle_dnd() else "dnd_off"
        elif key == "Escape":
            self._filter_priority = None
            self._filter_app = None
            self._show_only_unread = False
            return "clear_filters"
        return None

    def _handle_history_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("notifications")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        return None

    def _handle_apps_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("notifications")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            apps = list(self._app_settings.values())
            if 0 <= self._selected_index < len(apps):
                apps[self._selected_index].enabled = not apps[self._selected_index].enabled
                return "toggle_app"
        return None
