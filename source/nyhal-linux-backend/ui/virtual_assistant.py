"""
Nyrqis OS - Virtual Assistant
Command history, reminders, quick actions, timers, and chat.
"""

import time
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class AssistantIntent(Enum):
    CHAT = "chat"
    REMINDER = "reminder"
    TIMER = "timer"
    SEARCH = "search"
    SYSTEM = "system"
    HELP = "help"
    TIME = "time"
    WEATHER = "weather"
    CALCULATE = "calculate"
    UNKNOWN = "unknown"


class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


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


# ─── Data classes ────────────────────────────────────────────────────────

@dataclass
class Message:
    """A chat message with role, content, and intent."""
    role: MessageRole = MessageRole.USER
    content: str = ""
    timestamp: float = 0.0
    intent: AssistantIntent = AssistantIntent.UNKNOWN

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def display(self) -> str:
        icons = {
            MessageRole.USER: "👤",
            MessageRole.ASSISTANT: "🤖",
            MessageRole.SYSTEM: "⚙️",
        }
        icon = icons.get(self.role, "❓")
        return f"{icon} {self.content}"


@dataclass
class Reminder:
    """A reminder item."""
    title: str = ""
    id: int = 0
    message: str = ""
    remind_at: float = 0.0
    due_time: float = 0.0
    repeat: ReminderRepeat = ReminderRepeat.NONE
    completed: bool = False
    priority: str = "normal"
    tags: List[str] = field(default_factory=list)
    created_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()
        # Support both remind_at and due_time
        if self.due_time == 0.0 and self.remind_at > 0.0:
            self.due_time = self.remind_at
        if self.remind_at == 0.0 and self.due_time > 0.0:
            self.remind_at = self.due_time

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
    def display(self) -> str:
        icons = {"low": "🔵", "normal": "🟢", "high": "🟠", "urgent": "🔴"}
        icon = icons.get(self.priority, "❓")
        status = "✅" if self.completed else "⏳"
        return f"{status} {icon} {self.title}"

    @property
    def priority_icon(self) -> str:
        icons = {"low": "🔵", "normal": "🟢", "high": "🟠", "urgent": "🔴"}
        return icons.get(self.priority, "❓")

    @property
    def status_icon(self) -> str:
        return "✅" if self.completed else "⏳"


@dataclass
class TimerItem:
    """Timer/countdown item for the virtual assistant."""
    name: str = ""
    total_seconds: float = 0.0
    remaining_seconds: float = 0.0
    duration_s: float = 0.0
    remaining_s: float = 0.0
    running: bool = False

    def __post_init__(self):
        # Support both naming conventions
        if self.total_seconds == 0.0 and self.duration_s > 0.0:
            self.total_seconds = self.duration_s
        if self.remaining_seconds == 0.0 and self.remaining_s > 0.0:
            self.remaining_seconds = self.remaining_s
        if self.duration_s == 0.0 and self.total_seconds > 0.0:
            self.duration_s = self.total_seconds
        if self.remaining_s == 0.0 and self.remaining_seconds > 0.0:
            self.remaining_s = self.remaining_seconds

    @property
    def display_time(self) -> str:
        total = int(self.total_seconds if self.total_seconds > 0 else self.duration_s)
        mins = total // 60
        secs = total % 60
        return f"{mins:02d}:{secs:02d}"

    @property
    def progress_bar(self) -> str:
        total = self.total_seconds if self.total_seconds > 0 else self.duration_s
        remaining = self.remaining_seconds if self.remaining_seconds > 0 else self.remaining_s
        if total <= 0:
            return "░" * 20
        pct = max(0.0, min(1.0, 1.0 - remaining / total))
        filled = int(pct * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def progress(self) -> float:
        total = self.total_seconds if self.total_seconds > 0 else self.duration_s
        remaining = self.remaining_seconds if self.remaining_seconds > 0 else self.remaining_s
        if total <= 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - remaining / total))

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


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


# ─── Virtual Assistant ───────────────────────────────────────────────────

class VirtualAssistant:
    """Main virtual assistant with chat, reminders, timers, and quick actions."""

    def __init__(self):
        self.view_mode: str = "chat"
        self.selected_index: int = 0
        self.messages: List[Message] = []
        self.reminders: List[Reminder] = []
        self.timers: List[TimerItem] = []
        self.quick_actions: List[QuickAction] = []
        self.command_history: List[CommandHistory] = []
        self.mode: AssistantMode = AssistantMode.CHAT
        self.assistant_name: str = "Nyrqis Assistant"
        self.voice_enabled: bool = False
        self.auto_suggest: bool = True
        self._reminder_counter: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.messages = [
            Message(MessageRole.ASSISTANT, "Hello! I'm the Nyrqis Assistant. How can I help you today?",
                    timestamp=now - 7200),
            Message(MessageRole.USER, "What's the system status?",
                    timestamp=now - 7100),
            Message(MessageRole.ASSISTANT, "System status: CPU 34.5%, RAM 28.3/64 GB, Disk 98% healthy, Network 12.5 Mbps ↓ / 2.5 Mbps ↑",
                    timestamp=now - 7090),
            Message(MessageRole.USER, "Show me running processes",
                    timestamp=now - 3600),
            Message(MessageRole.ASSISTANT, "Top processes: nyrqis-compositor (35.2% CPU), firefox (18.5%), code-server (8.0%)",
                    timestamp=now - 3590),
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
            CommandHistory(command="/status", result="CPU 34.5%, RAM 28.3/64 GB", success=True),
            CommandHistory(command="/terminal", result="Terminal opened", success=True),
            CommandHistory(command="/theme toggle", result="Switched to light mode", success=True),
        ]

    # ─── Intent classification ───────────────────────────────────────

    def _classify_intent(self, text: str) -> AssistantIntent:
        t = text.lower()
        if re.search(r'\b(what time|current time|time is it)\b', t):
            return AssistantIntent.TIME
        if re.search(r'\b(weather|forecast|temperature)\b', t):
            return AssistantIntent.WEATHER
        if re.search(r'\b(calculate|compute|math)\b', t):
            return AssistantIntent.CALCULATE
        if re.search(r'\b(remind|reminder|remind me)\b', t):
            return AssistantIntent.REMINDER
        if re.search(r'\b(timer|set timer|countdown)\b', t):
            return AssistantIntent.TIMER
        if re.search(r'\b(shutdown|reboot|restart|power off)\b', t):
            return AssistantIntent.SYSTEM
        if re.search(r'\b(help|commands|what can you)\b', t):
            return AssistantIntent.HELP
        if re.search(r'\b(status|system status)\b', t):
            return AssistantIntent.SYSTEM
        return AssistantIntent.CHAT

    def _generate_response(self, message: str, intent: AssistantIntent) -> str:
        if intent == AssistantIntent.TIME:
            return f"The current time is {time.strftime('%H:%M:%S')}."
        if intent == AssistantIntent.WEATHER:
            return "Current weather: 22°C, partly cloudy. High 26°C, Low 18°C."
        if intent == AssistantIntent.CALCULATE:
            nums = re.findall(r'[\d]+', message)
            if len(nums) >= 2:
                result = int(nums[0]) * int(nums[1])
                return f"The result is {result}."
            return "Please provide a calculation like 'calculate 42 * 7'."
        if intent == AssistantIntent.SYSTEM:
            return "System status: CPU 34.5%, RAM 28.3/64 GB, Disk 98% healthy."
        if intent == AssistantIntent.HELP:
            return "I can help with: time, weather, calculator, reminders, timers, and system status."
        if intent == AssistantIntent.REMINDER:
            return "Sure! I've set that reminder for you."
        if intent == AssistantIntent.TIMER:
            return "Timer set! I'll notify you when it's done."
        return f"I understand you're asking about: {message}. Let me help with that."

    def _parse_timer_seconds(self, text: str) -> int:
        m = re.search(r'(\d+)\s*(second|minute|hour|min|sec|hr)s?', text.lower())
        if m:
            val = int(m.group(1))
            unit = m.group(2)
            if unit.startswith('hour') or unit == 'hr':
                return val * 3600
            if unit.startswith('minute') or unit == 'min':
                return val * 60
            return val
        return 300  # default 5 minutes

    def _parse_reminder_minutes(self, text: str) -> int:
        m = re.search(r'(\d+)\s*(minute|hour|min|hr)s?', text.lower())
        if m:
            val = int(m.group(1))
            unit = m.group(2)
            if unit.startswith('hour') or unit == 'hr':
                return val * 60
            return val
        return 30  # default 30 minutes

    # ─── Public API ──────────────────────────────────────────────────

    def process_input(self, text: str) -> Message:
        """Process user input and return a response message."""
        intent = self._classify_intent(text)

        # Add user message
        user_msg = Message(MessageRole.USER, text, intent=intent)
        self.messages.append(user_msg)

        # Handle special intents
        if intent == AssistantIntent.REMINDER:
            minutes = self._parse_reminder_minutes(text)
            self._reminder_counter += 1
            r = Reminder(
                title=f"Reminder {self._reminder_counter}",
                message=text,
                due_time=time.time() + minutes * 60,
                priority="normal",
            )
            self.reminders.append(r)
        elif intent == AssistantIntent.TIMER:
            seconds = self._parse_timer_seconds(text)
            t = TimerItem(name=f"Timer {len(self.timers) + 1}",
                          total_seconds=seconds, remaining_seconds=seconds)
            t.start()
            self.timers.append(t)

        response = self._generate_response(text, intent)
        assistant_msg = Message(MessageRole.ASSISTANT, response, intent=intent)
        self.messages.append(assistant_msg)
        return assistant_msg

    def tick_timers(self) -> List[TimerItem]:
        """Tick all running timers and return expired ones."""
        expired = []
        for t in self.timers:
            if t.running and t.remaining_seconds > 0:
                t.remaining_seconds -= 1
                if t.remaining_seconds <= 0:
                    t.remaining_seconds = 0
                    t.running = False
                    expired.append(t)
        return expired

    def delete_reminder(self, index: int) -> bool:
        """Delete a reminder by index."""
        if 0 <= index < len(self.reminders):
            del self.reminders[index]
            return True
        return False

    def set_view(self, view: str):
        """Switch view mode."""
        self.view_mode = view
        self.selected_index = 0

    def select_down(self):
        """Move selection down."""
        self.selected_index += 1

    def select_up(self):
        """Move selection up."""
        if self.selected_index > 0:
            self.selected_index -= 1

    def handle_key(self, key: str) -> str:
        """Handle keyboard shortcut and return view mode."""
        key_map = {
            "a": "actions",
            "c": "chat",
            "r": "reminders",
            "t": "timers",
            "s": "search",
        }
        if key in key_map:
            self.set_view(key_map[key])
            return key_map[key]
        return self.view_mode

    # ─── Rendering ───────────────────────────────────────────────────

    def render_chat(self) -> List[str]:
        """Render chat view as list of lines."""
        lines = [f"── Chat ({self.assistant_name}) ──"]
        for msg in self.messages[-20:]:
            lines.append(msg.display)
        return lines

    def render_reminders(self) -> List[str]:
        """Render reminders view as list of lines."""
        lines = ["── Reminders ──"]
        for i, r in enumerate(self.reminders):
            marker = "▸ " if i == self.selected_index else "  "
            lines.append(f"{marker}{r.display} ({r.time_until})")
        if not self.reminders:
            lines.append("  No reminders.")
        return lines

    def render_actions(self) -> List[str]:
        """Render quick actions view as list of lines."""
        lines = ["── Quick Actions ──"]
        for i, a in enumerate(self.quick_actions):
            marker = "▸ " if i == self.selected_index else "  "
            lines.append(f"{marker}{a.icon} {a.name} [{a.shortcut}]")
        return lines

    # ─── Legacy API (backward compat) ────────────────────────────────

    @property
    def conversation(self):
        return self.messages

    def send_message(self, content: str) -> str:
        msg = self.process_input(content)
        return msg.content

    def add_reminder(self, title: str, due_time: float, **kwargs) -> Reminder:
        r = Reminder(title=title, due_time=due_time, **kwargs)
        self.reminders.append(r)
        return r

    def complete_reminder(self, reminder_id: int) -> bool:
        # Match by 1-based ID (id=1 → reminders[0])
        for i, r in enumerate(self.reminders):
            if r.id == reminder_id or i + 1 == reminder_id:
                r.completed = True
                return True
        return False

    def get_pending_reminders(self) -> List[Reminder]:
        return [r for r in self.reminders if not r.completed]

    def get_overdue_reminders(self) -> List[Reminder]:
        return [r for r in self.reminders if not r.completed and r.due_time < time.time()]

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

    def get_conversation_history(self, limit: int = 20) -> List[Message]:
        return self.messages[-limit:]

    def get_stats(self) -> Dict:
        return {
            "messages": len(self.messages),
            "reminders": len(self.reminders),
            "pending_reminders": len(self.get_pending_reminders()),
            "quick_actions": len(self.quick_actions),
            "commands_run": len(self.command_history),
        }
