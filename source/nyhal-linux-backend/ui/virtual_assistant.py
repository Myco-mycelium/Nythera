"""
Nyrqis OS - Virtual Assistant
Command history, reminders, and quick actions.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class AssistantMode(Enum):
    CHAT = "chat"
    COMMAND = "command"
    SEARCH = "search"
    REMINDER = "reminder"


class ReminderRepeat(Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass
class ChatMessage:
    role: str = "user"  # user or assistant
    content: str = ""
    timestamp: float = 0.0
    is_command: bool = False

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class Reminder:
    id: int = 0
    title: str = ""
    message: str = ""
    due_time: float = 0.0
    repeat: ReminderRepeat = ReminderRepeat.NONE
    completed: bool = False
    priority: str = "normal"
    tags: List[str] = field(default_factory=list)
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()

    @property
    def time_until(self) -> str:
        if self.due_time == 0:
            return "N/A"
        delta = self.due_time - time.time()
        if delta < 0:
            return "Overdue"
        if delta < 60:
            return f"{delta:.0f}s"
        elif delta < 3600:
            return f"{delta / 60:.0f}m"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h"
        return f"{delta / 86400:.1f}d"

    @property
    def priority_icon(self) -> str:
        icons = {"low": "🔵", "normal": "🟢", "high": "🟠", "urgent": "🔴"}
        return icons.get(self.priority, "?")

    @property
    def status_icon(self) -> str:
        return "✅" if self.completed else "⏳"


@dataclass
class QuickAction:
    name: str = ""
    description: str = ""
    command: str = ""
    category: str = "General"
    icon: str = ""
    shortcut: str = ""
    use_count: int = 0

    @property
    def shortcut_display(self) -> str:
        return f"⚡ {self.shortcut}" if self.shortcut else ""


@dataclass
class CommandHistory:
    command: str = ""
    result: str = ""
    timestamp: float = 0.0
    success: bool = True

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def status_icon(self) -> str:
        return "✅" if self.success else "❌"

    @property
    def time_ago(self) -> str:
        delta = time.time() - self.timestamp
        if delta < 60:
            return "just now"
        elif delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.0f}h ago"
        return f"{delta / 86400:.0f}d ago"


class VirtualAssistant:
    def __init__(self):
        self.conversation: List[ChatMessage] = []
        self.reminders: List[Reminder] = []
        self.quick_actions: List[QuickAction] = []
        self.command_history: List[CommandHistory] = []
        self.mode: AssistantMode = AssistantMode.CHAT
        self.assistant_name: str = "Nyrqis Assistant"
        self.voice_enabled: bool = False
        self.auto_suggest: bool = True
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.conversation = [
            ChatMessage(role="assistant", content="Hello! I'm the Nyrqis Assistant. How can I help you today?",
                         timestamp=now - 7200),
            ChatMessage(role="user", content="What's the system status?",
                         timestamp=now - 7100, is_command=True),
            ChatMessage(role="assistant", content="System status: CPU 34.5%, RAM 28.3/64 GB, Disk 98% healthy, Network 12.5 Mbps ↓ / 2.5 Mbps ↑",
                         timestamp=now - 7090),
            ChatMessage(role="user", content="Show me running processes",
                         timestamp=now - 3600, is_command=True),
            ChatMessage(role="assistant", content="Top processes: nyrqis-compositor (35.2% CPU), firefox (18.5%), code-server (8.0%)",
                         timestamp=now - 3590),
            ChatMessage(role="user", content="Remind me to commit at 5pm",
                         timestamp=now - 1800),
            ChatMessage(role="assistant", content="Done! I've set a reminder for 5:00 PM: 'Commit changes'.",
                         timestamp=now - 1790),
        ]

        self.reminders = [
            Reminder(id=1, title="Commit changes", message="Commit and push Nyrqis changes",
                      due_time=now + 7200, priority="high", tags=["git"]),
            Reminder(id=2, title="Team standup", message="Daily standup meeting",
                      due_time=now + 86400, repeat=ReminderRepeat.DAILY,
                      priority="normal", tags=["meeting"]),
            Reminder(id=3, title="Review PR #42", message="Review and approve Nyrqis PR",
                      due_time=now + 14400, priority="normal", tags=["git", "review"]),
            Reminder(id=4, title="Update system", message="Run system update check",
                      due_time=now + 86400 * 3, repeat=ReminderRepeat.WEEKLY,
                      priority="low", tags=["system"]),
        ]

        self.quick_actions = [
            QuickAction(name="System Status", description="Show CPU, RAM, disk, network",
                        command="/status", category="System", icon="📊", shortcut="Ctrl+1"),
            QuickAction(name="Open Terminal", description="Launch terminal emulator",
                        command="/terminal", category="Apps", icon="⬛", shortcut="Ctrl+T"),
            QuickAction(name="Take Screenshot", description="Capture full screen",
                        command="/screenshot", category="Tools", icon="📸", shortcut="Print"),
            QuickAction(name="Toggle Dark Mode", description="Switch theme variant",
                        command="/theme toggle", category="Settings", icon="🌙", shortcut="Ctrl+D"),
            QuickAction(name="Lock Screen", description="Lock the workstation",
                        command="/lock", category="Security", icon="🔒", shortcut="Super+L"),
            QuickAction(name="Show Notifications", description="Open notification center",
                        command="/notifications", category="System", icon="🔔", shortcut="Super+N"),
            QuickAction(name="Volume Up", description="Increase master volume by 10%",
                        command="/volume up", category="Audio", icon="🔊", shortcut="Ctrl+Up"),
            QuickAction(name="Volume Down", description="Decrease master volume by 10%",
                        command="/volume down", category="Audio", icon="🔇", shortcut="Ctrl+Down"),
        ]

        self.command_history = [
            CommandHistory(command="/status", result="CPU 34.5%, RAM 28.3/64 GB",
                            success=True),
            CommandHistory(command="/terminal", result="Terminal opened",
                            success=True),
            CommandHistory(command="/theme toggle", result="Switched to light mode",
                            success=True),
        ]

    def send_message(self, content: str) -> str:
        self.conversation.append(ChatMessage(role="user", content=content))
        response = self._generate_response(content)
        self.conversation.append(ChatMessage(role="assistant", content=response))
        return response

    def _generate_response(self, message: str) -> str:
        msg = message.lower()
        if "status" in msg or "system" in msg:
            return "System status: CPU 34.5%, RAM 28.3/64 GB, Disk 98% healthy, Network 12.5 Mbps ↓"
        if "help" in msg:
            return "I can help with: system status, reminders, quick actions, and general questions."
        if "hello" in msg or "hi" in msg:
            return "Hello! How can I help you?"
        if "reminder" in msg:
            return "Sure! What would you like to be reminded about?"
        if "time" in msg:
            return f"The current time is {time.strftime('%H:%M:%S')}."
        return f"I understand you're asking about: {message}. Let me help with that."

    def add_reminder(self, title: str, due_time: float, **kwargs) -> Reminder:
        self.reminders.append(Reminder(title=title, due_time=due_time, **kwargs))
        return self.reminders[-1]

    def complete_reminder(self, reminder_id: int) -> bool:
        reminder = next((r for r in self.reminders if r.id == reminder_id), None)
        if reminder:
            reminder.completed = True
            return True
        return False

    def delete_reminder(self, reminder_id: int) -> bool:
        for i, r in enumerate(self.reminders):
            if r.id == reminder_id:
                del self.reminders[i]
                return True
        return False

    def get_pending_reminders(self) -> List[Reminder]:
        return [r for r in self.reminders if not r.completed]

    def get_overdue_reminders(self) -> List[Reminder]:
        return [r for r in self.reminders
                if not r.completed and r.due_time < time.time()]

    def execute_quick_action(self, name: str) -> Optional[str]:
        action = next((a for a in self.quick_actions if a.name == name), None)
        if action:
            action.use_count += 1
            self.command_history.append(CommandHistory(command=action.command, result="Executed"))
            return action.command
        return None

    def search_actions(self, query: str) -> List[QuickAction]:
        q = query.lower()
        return [a for a in self.quick_actions if q in a.name.lower() or q in a.description.lower()]

    def get_conversation_history(self, limit: int = 20) -> List[ChatMessage]:
        return self.conversation[-limit:]

    def get_stats(self) -> Dict:
        return {
            "messages": len(self.conversation),
            "reminders": len(self.reminders),
            "pending_reminders": len(self.get_pending_reminders()),
            "quick_actions": len(self.quick_actions),
            "commands_run": len(self.command_history),
        }


@dataclass
class Message:
    id: int = 0
    role: str = "user"
    content: str = ""
    timestamp: float = 0.0
