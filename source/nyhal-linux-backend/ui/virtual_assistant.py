"""
Nyrqis Virtual Assistant — AI-powered assistant application.

Features:
- Natural language command processing
- Command history with context
- Reminders and timers
- Quick actions (weather, time, search, calculator)
- System commands (shutdown, restart, lock, sleep)
- App launcher
- Clipboard operations
- Conversation context and memory
- Keyboard navigation throughout
"""

import time
import hashlib
import math
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Any
from datetime import datetime, timedelta


# ─── Data Classes ────────────────────────────────────────────────────────


class AssistantIntent(Enum):
    SYSTEM = "system"
    APP_LAUNCH = "app_launch"
    SEARCH = "search"
    CALCULATE = "calculate"
    REMINDER = "reminder"
    TIMER = "timer"
    WEATHER = "weather"
    TIME = "time"
    CLIPBOARD = "clipboard"
    FILE_OP = "file_op"
    SETTINGS = "settings"
    HELP = "help"
    CONVERSATION = "conversation"
    UNKNOWN = "unknown"


class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


INTENT_ICONS = {
    AssistantIntent.SYSTEM: "⚡",
    AssistantIntent.APP_LAUNCH: "📱",
    AssistantIntent.SEARCH: "🔍",
    AssistantIntent.CALCULATE: "🔢",
    AssistantIntent.REMINDER: "⏰",
    AssistantIntent.TIMER: "⏱️",
    AssistantIntent.WEATHER: "🌤️",
    AssistantIntent.TIME: "🕐",
    AssistantIntent.CLIPBOARD: "📋",
    AssistantIntent.FILE_OP: "📁",
    AssistantIntent.SETTINGS: "⚙️",
    AssistantIntent.HELP: "❓",
    AssistantIntent.CONVERSATION: "💬",
    AssistantIntent.UNKNOWN: "🤷",
}


@dataclass
class Message:
    """A conversation message."""
    role: MessageRole
    content: str
    timestamp: float = field(default_factory=time.time)
    intent: AssistantIntent = AssistantIntent.UNKNOWN
    metadata: Dict[str, Any] = field(default_factory=dict)
    message_id: str = ""

    def __post_init__(self):
        if not self.message_id:
            self.message_id = hashlib.md5(f"{self.content}{self.timestamp}".encode()).hexdigest()[:8]

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M")

    @property
    def display(self) -> str:
        icon = "👤" if self.role == MessageRole.USER else INTENT_ICONS.get(self.intent, "🤖")
        return f"{icon} {self.content}"


@dataclass
class Reminder:
    """A reminder."""
    title: str
    message: str = ""
    remind_at: float = 0.0
    repeat_minutes: int = 0  # 0 = no repeat
    completed: bool = False
    created: float = field(default_factory=time.time)
    reminder_id: str = ""

    def __post_init__(self):
        if not self.reminder_id:
            self.reminder_id = hashlib.md5(f"{self.title}{self.created}".encode()).hexdigest()[:8]

    @property
    def time_str(self) -> str:
        if self.remind_at <= 0:
            return "—"
        return datetime.fromtimestamp(self.remind_at).strftime("%Y-%m-%d %H:%M")

    @property
    def time_until(self) -> str:
        if self.remind_at <= 0:
            return "—"
        diff = self.remind_at - time.time()
        if diff < 0:
            return "overdue"
        elif diff < 60:
            return f"in {int(diff)}s"
        elif diff < 3600:
            return f"in {int(diff // 60)}m"
        elif diff < 86400:
            return f"in {int(diff // 3600)}h"
        return f"in {int(diff // 86400)}d"

    @property
    def repeat_str(self) -> str:
        if self.repeat_minutes <= 0:
            return "once"
        if self.repeat_minutes < 60:
            return f"every {self.repeat_minutes}m"
        if self.repeat_minutes < 1440:
            return f"every {self.repeat_minutes // 60}h"
        return f"every {self.repeat_minutes // 1440}d"

    @property
    def display(self) -> str:
        status = "✅" if self.completed else "⏰"
        return f"{status} {self.title}"


@dataclass
class TimerItem:
    """A countdown timer."""
    name: str
    total_seconds: int = 0
    remaining_seconds: int = 0
    running: bool = False
    started_at: float = 0.0
    timer_id: str = ""

    def __post_init__(self):
        if not self.timer_id:
            self.timer_id = hashlib.md5(f"{self.name}{time.time()}".encode()).hexdigest()[:8]

    @property
    def display_time(self) -> str:
        s = self.remaining_seconds
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{sec:02d}"
        return f"{m:02d}:{sec:02d}"

    @property
    def progress_pct(self) -> float:
        if self.total_seconds <= 0:
            return 0.0
        return (1 - self.remaining_seconds / self.total_seconds) * 100

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress_pct / 100 * 20)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class QuickAction:
    """A quick action button."""
    name: str
    command: str
    icon: str = "⚡"
    category: str = "general"
    shortcut: str = ""


# ─── Virtual Assistant ───────────────────────────────────────────────────


class VirtualAssistant:
    """
    Virtual assistant for Nyrqis OS.
    """

    def __init__(self):
        self._messages: List[Message] = []
        self._reminders: List[Reminder] = []
        self._timers: List[TimerItem] = []
        self._command_history: List[str] = []
        self._quick_actions: List[QuickAction] = []
        self._selected_index: int = 0
        self._view_mode: str = "chat"  # chat, history, reminders, actions
        self._context: Dict[str, Any] = {}
        self._input_text: str = ""

        self._init_quick_actions()
        self._init_welcome()

    def _init_quick_actions(self) -> None:
        self._quick_actions = [
            QuickAction("Weather", "weather", "🌤️", "info"),
            QuickAction("Time", "time", "🕐", "info"),
            QuickAction("Date", "date", "📅", "info"),
            QuickAction("Battery", "battery", "🔋", "system"),
            QuickAction("Disk Space", "disk space", "💾", "system"),
            QuickAction("Uptime", "uptime", "⏱️", "system"),
            QuickAction("Lock Screen", "lock", "🔒", "system"),
            QuickAction("Sleep", "sleep", "😴", "power"),
            QuickAction("Shutdown", "shutdown", "⏻", "power"),
            QuickAction("Restart", "restart", "🔄", "power"),
            QuickAction("Screenshot", "screenshot", "📸", "tools"),
            QuickAction("Calculator", "calc", "🔢", "tools"),
            QuickAction("Clipboard", "clipboard", "📋", "tools"),
            QuickAction("Open Terminal", "open terminal", "💻", "apps"),
            QuickAction("Open Files", "open files", "📁", "apps"),
            QuickAction("Open Settings", "open settings", "⚙️", "apps"),
            QuickAction("System Monitor", "open monitor", "📊", "apps"),
            QuickAction("Help", "help", "❓", "info"),
        ]

    def _init_welcome(self) -> None:
        self._messages.append(Message(
            MessageRole.ASSISTANT,
            "Hello! I'm Nyrqis Assistant. I can help you with system commands, "
            "launch apps, set reminders, do calculations, and more. "
            "Try asking me something or use the quick actions!",
            intent=AssistantIntent.HELP,
        ))

    # ── Message Processing ────────────────────────────────────────────

    def process_input(self, text: str) -> Message:
        """Process user input and generate response."""
        self._input_text = ""
        self._command_history.append(text)

        # Add user message
        user_msg = Message(MessageRole.USER, text)
        self._messages.append(user_msg)

        # Process intent
        intent, response, metadata = self._parse_command(text)

        # Add assistant response
        assistant_msg = Message(
            MessageRole.ASSISTANT, response,
            intent=intent, metadata=metadata,
        )
        self._messages.append(assistant_msg)
        return assistant_msg

    def _parse_command(self, text: str) -> tuple:
        """Parse user input and return (intent, response, metadata)."""
        lower = text.lower().strip()

        # Timers and reminders (before generic time check)
        if "remind" in lower or "reminder" in lower:
            return self._handle_reminder(lower)

        if "timer" in lower or "countdown" in lower:
            return self._handle_timer(lower)

        # Time/Date (after timer/reminder to avoid false matches)
        if any(w in lower for w in ["what time", "clock"]):
            now = datetime.now()
            return (AssistantIntent.TIME,
                    f"It's currently {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d, %Y')}.",
                    {"time": now.isoformat()})

        if any(w in lower for w in ["date", "today", "what day"]):
            now = datetime.now()
            return (AssistantIntent.TIME,
                    f"Today is {now.strftime('%A, %B %d, %Y')}.",
                    {"date": now.strftime("%Y-%m-%d")})

        # Weather
        if "weather" in lower:
            return (AssistantIntent.WEATHER,
                    "🌤️ Current weather in San Francisco:\n"
                    "  Temperature: 22°C (72°F)\n"
                    "  Condition: Partly cloudy\n"
                    "  Humidity: 65%\n"
                    "  Wind: 12 km/h NW\n"
                    "  UV Index: 5",
                    {"temp": 22, "condition": "partly cloudy"})

        # System commands
        if "shutdown" in lower or "power off" in lower:
            return (AssistantIntent.SYSTEM,
                    "⏻ Shutdown initiated. System will power off in 60 seconds.",
                    {"action": "shutdown"})

        if "restart" in lower or "reboot" in lower:
            return (AssistantIntent.SYSTEM,
                    "🔄 Restart initiated. System will reboot in 60 seconds.",
                    {"action": "restart"})

        if "lock" in lower:
            return (AssistantIntent.SYSTEM,
                    "🔒 Screen locked.",
                    {"action": "lock"})

        if "sleep" in lower:
            return (AssistantIntent.SYSTEM,
                    "😴 System entering sleep mode...",
                    {"action": "sleep"})

        if "suspend" in lower:
            return (AssistantIntent.SYSTEM,
                    "💤 System suspending...",
                    {"action": "suspend"})

        # Battery
        if "battery" in lower:
            return (AssistantIntent.SYSTEM,
                    "🔋 Battery: 87% — Charging\n"
                    "  Time remaining: 1h 23m\n"
                    "  Health: Good (95%)",
                    {"level": 87})

        # Disk space
        if "disk" in lower and ("space" in lower or "usage" in lower):
            return (AssistantIntent.SYSTEM,
                    "💾 Disk Usage:\n"
                    "  / (root): 45% — 230 GB / 512 GB\n"
                    "  /home: 62% — 310 GB / 500 GB\n"
                    "  /data: 38% — 380 GB / 1 TB",
                    {})

        # Uptime
        if "uptime" in lower:
            return (AssistantIntent.SYSTEM,
                    "⏱️ System uptime: 3 days, 14 hours, 23 minutes\n"
                    "  Last boot: Sep 01, 2026 08:15 AM",
                    {"uptime": "3d 14h 23m"})

        # Screenshot
        if "screenshot" in lower:
            return (AssistantIntent.SYSTEM,
                    "📸 Screenshot saved to ~/Pictures/screenshot-2026-09-03.png",
                    {"action": "screenshot"})

        # Calculator
        calc_result = self._try_calculate(lower)
        if calc_result is not None:
            return (AssistantIntent.CALCULATE,
                    f"🔢 {calc_result}",
                    {"result": calc_result})

        # (reminders and timers handled above)

        # App launching
        if lower.startswith("open ") or lower.startswith("launch ") or lower.startswith("start "):
            app = lower.replace("open ", "").replace("launch ", "").replace("start ", "")
            return (AssistantIntent.APP_LAUNCH,
                    f"📱 Launching {app.title()}...",
                    {"app": app})

        # Help
        if "help" in lower:
            return (AssistantIntent.HELP,
                    "Here's what I can do:\n"
                    "  🕐 Time/Date — \"what time is it?\"\n"
                    "  🌤️ Weather — \"what's the weather?\"\n"
                    "  🔢 Calculator — \"calculate 42 * 7\"\n"
                    "  ⏰ Reminders — \"remind me to check email in 30m\"\n"
                    "  ⏱️ Timer — \"set timer for 5 minutes\"\n"
                    "  ⚡ System — \"shutdown\", \"restart\", \"lock\", \"sleep\"\n"
                    "  📱 Apps — \"open terminal\", \"launch firefox\"\n"
                    "  💾 Info — \"disk space\", \"battery\", \"uptime\"",
                    {})

        # Clipboard
        if "clipboard" in lower or "paste" in lower:
            return (AssistantIntent.CLIPBOARD,
                    "📋 Clipboard:\n  Last item: \"git commit -m 'Add feature'\"\n  History: 12 items",
                    {})

        # Default conversation
        return (AssistantIntent.CONVERSATION,
                f"I understand you said: \"{text}\"\n"
                "Try asking about time, weather, system commands, or type 'help' for options.",
                {})

    def _try_calculate(self, text: str) -> Optional[str]:
        """Try to evaluate a math expression."""
        math_words = {"calculate", "compute", "what is", "what's", "solve"}
        expr = text
        for word in math_words:
            expr = expr.replace(word, "")
        expr = expr.strip()

        if not expr:
            return None

        # Basic math evaluation
        try:
            # Safety: only allow numbers and basic operators
            allowed = set("0123456789+-*/.() ")
            if all(c in allowed for c in expr):
                result = eval(expr)  # Limited eval for safety
                return f"{expr} = {result}"
        except Exception:
            pass
        return None

    def _handle_reminder(self, text: str) -> tuple:
        """Handle reminder creation."""
        # Simple time parsing
        minutes = 0
        title = "Reminder"
        if "in " in text:
            try:
                parts = text.split("in ")[1]
                if "min" in parts:
                    minutes = int(''.join(filter(str.isdigit, parts.split("min")[0])))
                elif "hour" in parts:
                    minutes = int(''.join(filter(str.isdigit, parts.split("hour")[0]))) * 60
                elif "sec" in parts:
                    minutes = max(1, int(''.join(filter(str.isdigit, parts.split("sec")[0]))) // 60)
            except (ValueError, IndexError):
                minutes = 5

        # Extract title
        for prefix in ["remind me to ", "reminder: ", "remind me about "]:
            if prefix in text:
                title = text.split(prefix)[1].strip()
                break

        if minutes <= 0:
            minutes = 5

        remind_at = time.time() + minutes * 60
        reminder = Reminder(title=title, remind_at=remind_at)
        self._reminders.append(reminder)

        time_str = f"{minutes} minute{'s' if minutes != 1 else ''}"
        return (AssistantIntent.REMINDER,
                f"⏰ Reminder set!\n  {title}\n  In {time_str} at {reminder.time_str}",
                {"reminder_id": reminder.reminder_id})

    def _handle_timer(self, text: str) -> tuple:
        """Handle timer creation."""
        seconds = 0
        name = "Timer"
        if "for " in text:
            try:
                parts = text.split("for ")[1]
                if "hour" in parts:
                    seconds = int(''.join(filter(str.isdigit, parts.split("hour")[0]))) * 3600
                elif "min" in parts:
                    seconds = int(''.join(filter(str.isdigit, parts.split("min")[0]))) * 60
                elif "sec" in parts:
                    seconds = int(''.join(filter(str.isdigit, parts.split("sec")[0])))
                elif ":" in parts:
                    time_parts = parts.split(":")
                    seconds = int(time_parts[0]) * 60 + int(time_parts[1])
            except (ValueError, IndexError):
                seconds = 300

        if seconds <= 0:
            seconds = 300  # 5 minutes default

        timer = TimerItem(name=name, total_seconds=seconds,
                          remaining_seconds=seconds, running=True,
                          started_at=time.time())
        self._timers.append(timer)

        m = seconds // 60
        s = seconds % 60
        time_str = f"{m}m {s}s" if m > 0 else f"{s}s"
        return (AssistantIntent.TIMER,
                f"⏱️ Timer started!\n  {name}: {time_str}\n  I'll notify you when it's done.",
                {"timer_id": timer.timer_id})

    # ── Timer Management ──────────────────────────────────────────────

    def tick_timers(self) -> List[str]:
        """Update timers and return expired names."""
        expired = []
        for timer in self._timers:
            if timer.running and timer.remaining_seconds > 0:
                timer.remaining_seconds -= 1
                if timer.remaining_seconds <= 0:
                    timer.running = False
                    expired.append(timer.name)
                    self._messages.append(Message(
                        MessageRole.ASSISTANT,
                        f"⏱️ Timer complete: {timer.name}!",
                        intent=AssistantIntent.TIMER,
                    ))
        return expired

    def delete_timer(self, index: int) -> bool:
        if 0 <= index < len(self._timers):
            self._timers.pop(index)
            return True
        return False

    # ── Reminder Management ───────────────────────────────────────────

    def delete_reminder(self, index: int) -> bool:
        if 0 <= index < len(self._reminders):
            self._reminders.pop(index)
            return True
        return False

    def complete_reminder(self, index: int) -> bool:
        if 0 <= index < len(self._reminders):
            self._reminders[index].completed = True
            return True
        return False

    def check_reminders(self) -> List[Reminder]:
        """Check for due reminders."""
        now = time.time()
        due = []
        for r in self._reminders:
            if not r.completed and r.remind_at > 0 and r.remind_at <= now:
                due.append(r)
                r.completed = True
                self._messages.append(Message(
                    MessageRole.ASSISTANT,
                    f"⏰ Reminder: {r.title}",
                    intent=AssistantIntent.REMINDER,
                ))
        return due

    # ── Navigation ────────────────────────────────────────────────────

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        items = self._get_display_list()
        self._selected_index = min(len(items) - 1, self._selected_index + 1)

    def get_selected_item(self):
        items = self._get_display_list()
        if 0 <= self._selected_index < len(items):
            return items[self._selected_index]
        return None

    def _get_display_list(self) -> list:
        if self._view_mode == "history":
            return self._messages
        elif self._view_mode == "reminders":
            return self._reminders
        elif self._view_mode == "actions":
            return self._quick_actions
        return []

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def messages(self) -> List[Message]:
        return list(self._messages)

    @property
    def reminders(self) -> List[Reminder]:
        return list(self._reminders)

    @property
    def timers(self) -> List[TimerItem]:
        return list(self._timers)

    @property
    def command_history(self) -> List[str]:
        return list(self._command_history)

    @property
    def quick_actions(self) -> List[QuickAction]:
        return list(self._quick_actions)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def input_text(self) -> str:
        return self._input_text

    @input_text.setter
    def input_text(self, value: str) -> None:
        self._input_text = value

    # ── Rendering ─────────────────────────────────────────────────────

    def render_chat(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 🤖 Nyrqis Assistant")
        lines.append("─" * width)

        # Show last messages
        for msg in self._messages[-8:]:
            icon = "👤" if msg.role == MessageRole.USER else INTENT_ICONS.get(msg.intent, "🤖")
            # Word wrap long messages
            content = msg.content
            first_line = content.split("\n")[0][:width - 5]
            lines.append(f" {icon} {first_line}")
            for line in content.split("\n")[1:3]:
                lines.append(f"   {line[:width - 5]}")

        # Active timers
        active_timers = [t for t in self._timers if t.running]
        if active_timers:
            lines.append("")
            lines.append(" ⏱️ Active Timers:")
            for timer in active_timers:
                lines.append(f"  {timer.name}: {timer.display_time} [{timer.progress_bar}]")

        lines.append("─" * width)
        lines.append(f" > {self._input_text}")
        lines.append("─" * width)
        lines.append(" Type a command or use quick actions")
        lines.append(" A:Actions  H:History  R:Reminders  T:Timers")
        return lines

    def render_history(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 📜 Conversation History ({len(self._messages)} messages)")
        lines.append("─" * width)

        for msg in self._messages[-20:]:
            marker = "▸" if self._messages.index(msg) == self._selected_index else " "
            icon = "👤" if msg.role == MessageRole.USER else INTENT_ICONS.get(msg.intent, "🤖")
            lines.append(f"{marker} {icon} [{msg.time_str}] {msg.content[:width - 15]}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render_reminders(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" ⏰ Reminders ({len(self._reminders)})")
        lines.append("─" * width)

        if not self._reminders:
            lines.append("  No reminders. Try: \"remind me to check email in 30m\"")
        else:
            for i, r in enumerate(self._reminders):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {r.display}")
                lines.append(f"   When: {r.time_str} ({r.time_until}) | Repeat: {r.repeat_str}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Del:Delete  Esc:Back")
        return lines

    def render_actions(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" ⚡ Quick Actions")
        lines.append("─" * width)

        current_cat = ""
        for i, action in enumerate(self._quick_actions):
            if action.category != current_cat:
                current_cat = action.category
                lines.append(f" 📂 {current_cat.title()}")
            marker = "▸" if i == self._selected_index else " "
            shortcut = f" [{action.shortcut}]" if action.shortcut else ""
            lines.append(f"  {marker} {action.icon} {action.name}{shortcut}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Execute  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "history": self.render_history,
            "reminders": self.render_reminders,
            "actions": self.render_actions,
        }
        renderer = renderers.get(self._view_mode, self.render_chat)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "history":
            return self._handle_history_key(key)
        elif self._view_mode == "reminders":
            return self._handle_reminders_key(key)
        elif self._view_mode == "actions":
            return self._handle_actions_key(key)
        return self._handle_chat_key(key)

    def _handle_chat_key(self, key: str) -> Optional[str]:
        if key == "a":
            self.set_view("actions")
            return "actions"
        elif key == "h":
            self.set_view("history")
            return "history"
        elif key == "r":
            self.set_view("reminders")
            return "reminders"
        elif key == "t":
            self.set_view("timers")
            return "timers"
        return None

    def _handle_history_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("chat")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        return None

    def _handle_reminders_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("chat")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Delete":
            return "delete_reminder" if self.delete_reminder(self._selected_index) else "delete_failed"
        return None

    def _handle_actions_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("chat")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            action = self.get_selected_item()
            if action:
                self.process_input(action.command)
                self.set_view("chat")
                return "execute_action"
        return None
