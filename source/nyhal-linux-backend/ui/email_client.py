"""
Nyrqis Mail — full-featured email client with folders and threading.

Features:
- Inbox, Sent, Drafts, Spam, Trash, Archive folders
- Compose new messages with To, CC, BCC, Subject, Body
- Reply, Reply All, Forward
- Search across all messages
- Star/flag important messages
- Read/unread tracking with counts
- Message threading (conversation view)
- Folder management with move operations
- Keyboard navigation throughout
- Contact auto-complete from sent history
"""

import re
import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Set, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class MailFolder(Enum):
    INBOX = "Inbox"
    SENT = "Sent"
    DRAFTS = "Drafts"
    SPAM = "Spam"
    TRASH = "Trash"
    ARCHIVE = "Archive"
    ALL = "All Mail"


class MessageFlag(Enum):
    STARRED = "starred"
    IMPORTANT = "important"
    READ = "read"
    DRAFT = "draft"


@dataclass
class Contact:
    """An email contact."""
    name: str
    email: str
    last_used: float = field(default_factory=time.time)

    @property
    def display(self) -> str:
        return f"{self.name} <{self.email}>"


@dataclass
class EmailAddress:
    """An email address with optional name."""
    email: str
    name: str = ""

    @property
    def display(self) -> str:
        if self.name:
            return f"{self.name} <{self.email}>"
        return self.email


@dataclass
class EmailMessage:
    """A single email message."""
    from_addr: EmailAddress
    to: List[EmailAddress]
    cc: List[EmailAddress] = field(default_factory=list)
    bcc: List[EmailAddress] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    timestamp: float = field(default_factory=time.time)
    folder: MailFolder = MailFolder.INBOX
    flags: Set[MessageFlag] = field(default_factory=set)
    message_id: str = ""
    reply_to: str = ""
    forwarded_from: str = ""
    thread_id: str = ""
    attachments: List[str] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.message_id:
            self.message_id = hashlib.md5(
                f"{self.from_addr.email}{self.subject}{self.timestamp}".encode()
            ).hexdigest()[:12]
        if not self.thread_id:
            self.thread_id = self.message_id

    @property
    def is_read(self) -> bool:
        return MessageFlag.READ in self.flags

    @property
    def is_starred(self) -> bool:
        return MessageFlag.STARRED in self.flags

    @property
    def is_important(self) -> bool:
        return MessageFlag.IMPORTANT in self.flags

    @property
    def is_draft(self) -> bool:
        return MessageFlag.DRAFT in self.flags

    @property
    def to_display(self) -> str:
        return ", ".join(a.display for a in self.to)

    @property
    def from_display(self) -> str:
        return self.from_addr.display

    @property
    def preview(self) -> str:
        first_line = self.body.split("\n")[0].strip()
        return first_line[:80] if first_line else "(empty)"

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        elif diff < 604800:
            return f"{int(diff // 86400)}d ago"
        else:
            return datetime.fromtimestamp(self.timestamp).strftime("%b %d")

    @property
    def date_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d %H:%M")

    @property
    def size_estimate(self) -> int:
        return len(self.body.encode()) + sum(len(a) for a in self.attachments)


# ─── Compose State ───────────────────────────────────────────────────────


class ComposeMode(Enum):
    NEW = "new"
    REPLY = "reply"
    REPLY_ALL = "reply_all"
    FORWARD = "forward"
    EDIT_DRAFT = "edit_draft"


@dataclass
class ComposeState:
    """State of a message being composed."""
    mode: ComposeMode = ComposeMode.NEW
    to_text: str = ""
    cc_text: str = ""
    subject: str = ""
    body: str = ""
    cursor_pos: int = 0
    draft_id: str = ""
    reply_to_id: str = ""
    is_dirty: bool = False
    active_field: str = "to"  # to, cc, subject, body

    @property
    def to_list(self) -> List[EmailAddress]:
        """Parse To field into addresses."""
        return self._parse_addresses(self.to_text)

    @property
    def cc_list(self) -> List[EmailAddress]:
        return self._parse_addresses(self.cc_text)

    def _parse_addresses(self, text: str) -> List[EmailAddress]:
        """Parse comma-separated addresses."""
        if not text.strip():
            return []
        addrs = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            match = re.match(r'^(.+?)\s*<(.+?)>$', part)
            if match:
                addrs.append(EmailAddress(name=match.group(1).strip(), email=match.group(2).strip()))
            elif "@" in part:
                addrs.append(EmailAddress(email=part))
        return addrs


# ─── Email Client ────────────────────────────────────────────────────────


class EmailClient:
    """
    Email client for Nyrqis OS.

    Manages messages, folders, contacts, and compose state.
    """

    def __init__(self):
        self._messages: List[EmailMessage] = []
        self._contacts: List[Contact] = []
        self._current_folder: MailFolder = MailFolder.INBOX
        self._selected_index: int = 0
        self._view_mode: str = "list"  # list, read, compose

        # Compose
        self._compose: Optional[ComposeState] = None

        # Search
        self._search_query: str = ""
        self._search_results: List[EmailMessage] = []

        # Settings
        self._signature: str = "---\nSent from Nyrqis 🍄"
        self._page_size: int = 50

        # Callbacks
        self._on_send: List[Callable] = []
        self._on_receive: List[Callable] = []

        # Init sample data
        self._init_sample_data()

    def _init_sample_data(self) -> None:
        """Create sample emails and contacts."""
        me = EmailAddress(email="user@nyrqis.os", name="User")

        # Sample contacts
        self._contacts = [
            Contact("Alice Wunderland", "alice@wonderland.io"),
            Contact("Bob Builder", "bob@construct.dev"),
            Contact("Charlie Root", "charlie@system.admin"),
            Contact("Dev Team", "dev@nyrqis.os"),
            Contact("Nyrqis Updates", "updates@nyrqis.os"),
        ]

        now = time.time()

        # Sample inbox
        samples = [
            (self._contacts[0], "Welcome to Nyrqis OS!", "Hi there!\n\nWelcome to Nyrqis, the mycelium-powered operating system.\n\nWe're excited to have you on board. Here are some things you can do:\n\n- Open the terminal with Ctrl+Alt+T\n- Launch apps from the app grid\n- Use Spotlight (Ctrl+Space) to find anything\n- Check out Settings for customization\n\nHappy computing! 🍄\n\n— Alice", now - 7200),
            (self._contacts[4], "Nyrqis v1.0 Released", "We're thrilled to announce Nyrqis v1.0!\n\nNew features:\n• Wayland compositor with hardware acceleration\n• Tabbed file manager\n• Built-in terminal with ANSI support\n• Theme engine with dark/light modes\n• Plugin system for third-party apps\n• Multi-monitor support\n\nUpdate your system to enjoy these features.\n\nBest,\nThe Nyrqis Team", now - 86400),
            (self._contacts[1], "Re: Project Status", "Hey,\n\nThe build system is looking great. All 1975 tests passing!\n\nLet me know if you need help with the Rust bindings.\n\nCheers,\nBob", now - 172800),
            (self._contacts[2], "System Maintenance", "Automated notification:\n\nScheduled maintenance window: Saturday 2AM-4AM UTC\nAffected services: Package repository mirror\n\nNo action required.", now - 259200),
            (self._contacts[3], "Sprint Planning Notes", "Team,\n\nHere are the notes from today's sprint planning:\n\n1. Finish web browser integration\n2. Add email client\n3. Calendar app MVP\n4. Terminal multiplexer\n\nAction items assigned in the tracker.\n\n— Dev Team", now - 345600),
            (self._contacts[0], "Re: Re: Project Status", "Great progress!\n\nI've reviewed the latest PR. The code quality is excellent.\n\nOne minor suggestion: consider adding type hints to the new modules.\n\nAlso, the documentation site looks fantastic.\n\n— Alice", now - 432000),
        ]

        for contact, subject, body, ts in samples:
            msg = EmailMessage(
                from_addr=contact,
                to=[me],
                subject=subject,
                body=body,
                timestamp=ts,
                folder=MailFolder.INBOX,
                flags={MessageFlag.READ} if ts < now - 100000 else set(),
            )
            self._messages.append(msg)

        # Sent items
        sent = [
            (self._contacts[0], "Re: Welcome", "Thanks Alice! Excited to contribute.\n\nI've already started working on the file manager tab implementation.", now - 3500),
            (self._contacts[1], "Question about build", "Hey Bob,\n\nQuick question — should we use Cargo workspaces for the Rust crates?\n\nThanks!", now - 90000),
            (self._contacts[4], "Bug Report: Terminal colors", "Hi,\n\nI noticed some ANSI color codes aren't rendering correctly in the terminal.\n\nSteps to reproduce:\n1. Open terminal\n2. Run: echo -e '\\e[31mRed\\e[0m'\n3. Observe incorrect color\n\nSystem: Nyrqis v1.0, Wayland backend\n\nThanks!", now - 200000),
        ]
        for contact, subject, body, ts in sent:
            msg = EmailMessage(
                from_addr=me,
                to=[contact],
                subject=subject,
                body=body,
                timestamp=ts,
                folder=MailFolder.SENT,
                flags={MessageFlag.READ},
            )
            self._messages.append(msg)

        # One draft
        draft = EmailMessage(
            from_addr=me,
            to=[self._contacts[2]],
            subject="Re: System Maintenance",
            body="Hi Charlie,\n\nThanks for the heads up. I'll make sure to save my work.\n\nOne question: will the compositor service be affected?",
            timestamp=now - 200,
            folder=MailFolder.DRAFTS,
            flags={MessageFlag.DRAFT},
        )
        self._messages.append(draft)

    # ── Message Operations ────────────────────────────────────────────

    def get_messages(self, folder: MailFolder = None, search: str = "") -> List[EmailMessage]:
        """Get messages in a folder, optionally filtered."""
        target = folder or self._current_folder
        if target == MailFolder.ALL:
            msgs = [m for m in self._messages if not m.is_draft or m.folder != MailFolder.DRAFTS]
        else:
            msgs = [m for m in self._messages if m.folder == target]

        if search:
            q = search.lower()
            msgs = [m for m in msgs
                    if q in m.subject.lower() or
                    q in m.body.lower() or
                    q in m.from_addr.email.lower() or
                    q in m.from_addr.name.lower() or
                    q in " ".join(a.email for a in m.to)]

        # Sort by timestamp descending
        msgs.sort(key=lambda m: -m.timestamp)
        return msgs

    def get_message(self, message_id: str) -> Optional[EmailMessage]:
        for msg in self._messages:
            if msg.message_id == message_id:
                return msg
        return None

    def move_message(self, message_id: str, folder: MailFolder) -> bool:
        msg = self.get_message(message_id)
        if msg:
            msg.folder = folder
            return True
        return False

    def delete_message(self, message_id: str) -> bool:
        """Move to trash."""
        return self.move_message(message_id, MailFolder.TRASH)

    def trash_to_inbox(self, message_id: str) -> bool:
        return self.move_message(message_id, MailFolder.INBOX)

    def archive_message(self, message_id: str) -> bool:
        return self.move_message(message_id, MailFolder.ARCHIVE)

    def mark_read(self, message_id: str) -> bool:
        msg = self.get_message(message_id)
        if msg:
            msg.flags.add(MessageFlag.READ)
            return True
        return False

    def mark_unread(self, message_id: str) -> bool:
        msg = self.get_message(message_id)
        if msg:
            msg.flags.discard(MessageFlag.READ)
            return True
        return False

    def toggle_star(self, message_id: str) -> bool:
        msg = self.get_message(message_id)
        if msg:
            if MessageFlag.STARRED in msg.flags:
                msg.flags.discard(MessageFlag.STARRED)
            else:
                msg.flags.add(MessageFlag.STARRED)
            return True
        return False

    def mark_important(self, message_id: str) -> bool:
        msg = self.get_message(message_id)
        if msg:
            msg.flags.add(MessageFlag.IMPORTANT)
            return True
        return False

    # ── Compose ───────────────────────────────────────────────────────

    def compose_new(self, to: str = "", subject: str = "") -> ComposeState:
        """Start composing a new message."""
        self._compose = ComposeState(
            mode=ComposeMode.NEW,
            to_text=to,
            subject=subject,
            active_field="to" if not to else "body",
        )
        self._view_mode = "compose"
        return self._compose

    def reply(self, message_id: str) -> Optional[ComposeState]:
        """Reply to a message."""
        msg = self.get_message(message_id)
        if not msg:
            return None
        self._compose = ComposeState(
            mode=ComposeMode.REPLY,
            to_text=msg.from_addr.email,
            subject=f"Re: {msg.subject}" if not msg.subject.startswith("Re: ") else msg.subject,
            body=f"\n\n--- Original Message ---\n{msg.from_display} wrote on {msg.date_str}:\n\n{msg.body}",
            reply_to_id=message_id,
            active_field="body",
        )
        self._view_mode = "compose"
        return self._compose

    def reply_all(self, message_id: str) -> Optional[ComposeState]:
        """Reply to all recipients."""
        msg = self.get_message(message_id)
        if not msg:
            return None
        # Include all recipients except self
        others = [a.email for a in msg.to if a.email != "user@nyrqis.os"]
        to_text = ", ".join(others) if others else msg.from_addr.email
        self._compose = ComposeState(
            mode=ComposeMode.REPLY_ALL,
            to_text=to_text,
            subject=f"Re: {msg.subject}" if not msg.subject.startswith("Re: ") else msg.subject,
            body=f"\n\n--- Original Message ---\n{msg.from_display} wrote on {msg.date_str}:\n\n{msg.body}",
            reply_to_id=message_id,
            active_field="body",
        )
        self._view_mode = "compose"
        return self._compose

    def forward(self, message_id: str) -> Optional[ComposeState]:
        """Forward a message."""
        msg = self.get_message(message_id)
        if not msg:
            return None
        self._compose = ComposeState(
            mode=ComposeMode.FORWARD,
            subject=f"Fwd: {msg.subject}" if not msg.subject.startswith("Fwd: ") else msg.subject,
            body=f"\n\n--- Forwarded Message ---\nFrom: {msg.from_display}\nDate: {msg.date_str}\nSubject: {msg.subject}\n\n{msg.body}",
            active_field="to",
        )
        self._view_mode = "compose"
        return self._compose

    def send_compose(self) -> Optional[EmailMessage]:
        """Send the current compose."""
        if not self._compose:
            return None

        me = EmailAddress(email="user@nyrqis.os", name="User")
        msg = EmailMessage(
            from_addr=me,
            to=self._compose.to_list,
            cc=self._compose.cc_list,
            subject=self._compose.subject,
            body=self._compose.body + "\n" + self._signature,
            folder=MailFolder.SENT,
            flags={MessageFlag.READ},
        )

        if self._compose.reply_to_id:
            msg.reply_to = self._compose.reply_to_id
            # Copy thread ID
            orig = self.get_message(self._compose.reply_to_id)
            if orig:
                msg.thread_id = orig.thread_id

        self._messages.append(msg)

        # Remove draft if exists
        if self._compose.draft_id:
            self._messages = [m for m in self._messages if m.message_id != self._compose.draft_id]

        # Update contacts
        for addr in msg.to:
            self._update_contact(addr)

        self._notify("send", msg)
        self._compose = None
        self._view_mode = "list"
        return msg

    def save_draft(self) -> Optional[EmailMessage]:
        """Save current compose as draft."""
        if not self._compose:
            return None

        me = EmailAddress(email="user@nyrqis.os", name="User")
        draft = EmailMessage(
            from_addr=me,
            to=self._compose.to_list,
            subject=self._compose.subject,
            body=self._compose.body,
            folder=MailFolder.DRAFTS,
            flags={MessageFlag.DRAFT},
        )

        if self._compose.draft_id:
            draft.message_id = self._compose.draft_id
            # Replace existing draft
            self._messages = [m for m in self._messages if m.message_id != self._compose.draft_id]

        self._messages.append(draft)
        self._compose.draft_id = draft.message_id
        self._compose.is_dirty = False
        return draft

    def discard_compose(self) -> None:
        self._compose = None
        self._view_mode = "list"

    @property
    def compose(self) -> Optional[ComposeState]:
        return self._compose

    def update_compose(self, field: str, value: str) -> None:
        if self._compose:
            if field == "to":
                self._compose.to_text = value
            elif field == "cc":
                self._compose.cc_text = value
            elif field == "subject":
                self._compose.subject = value
            elif field == "body":
                self._compose.body = value
            self._compose.is_dirty = True

    def set_compose_field(self, field: str) -> None:
        if self._compose:
            self._compose.active_field = field

    # ── Search ────────────────────────────────────────────────────────

    def search(self, query: str) -> List[EmailMessage]:
        self._search_query = query
        if not query:
            self._search_results = []
            return []
        self._search_results = self.get_messages(search=query)
        return self._search_results

    @property
    def search_query(self) -> str:
        return self._search_query

    # ── Folders ───────────────────────────────────────────────────────

    @property
    def current_folder(self) -> MailFolder:
        return self._current_folder

    def set_folder(self, folder: MailFolder) -> None:
        self._current_folder = folder
        self._selected_index = 0

    def folder_counts(self) -> Dict[str, int]:
        """Get unread counts per folder."""
        counts = {}
        for folder in MailFolder:
            if folder == MailFolder.ALL:
                continue
            unread = len([m for m in self._messages
                          if m.folder == folder and not m.is_read and not m.is_draft])
            counts[folder.value] = unread
        return counts

    def folder_total(self, folder: MailFolder) -> int:
        return len([m for m in self._messages if m.folder == folder])

    # ── Contacts ──────────────────────────────────────────────────────

    def _update_contact(self, addr: EmailAddress) -> None:
        for c in self._contacts:
            if c.email == addr.email:
                c.last_used = time.time()
                if addr.name and not c.name:
                    c.name = addr.name
                return
        self._contacts.append(Contact(
            name=addr.name or addr.email.split("@")[0],
            email=addr.email,
        ))

    def search_contacts(self, query: str) -> List[Contact]:
        q = query.lower()
        return [c for c in self._contacts if q in c.name.lower() or q in c.email.lower()]

    @property
    def contacts(self) -> List[Contact]:
        return list(self._contacts)

    # ── Threading ─────────────────────────────────────────────────────

    def get_thread(self, message_id: str) -> List[EmailMessage]:
        """Get all messages in a thread."""
        msg = self.get_message(message_id)
        if not msg:
            return []
        thread = [m for m in self._messages if m.thread_id == msg.thread_id]
        thread.sort(key=lambda m: m.timestamp)
        return thread

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select(self, index: int) -> None:
        msgs = self.get_messages()
        self._selected_index = max(0, min(len(msgs) - 1, index))

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        msgs = self.get_messages()
        self._selected_index = min(len(msgs) - 1, self._selected_index + 1)

    def get_selected_message(self) -> Optional[EmailMessage]:
        msgs = self.get_messages()
        if 0 <= self._selected_index < len(msgs):
            return msgs[self._selected_index]
        return None

    def open_selected(self) -> Optional[EmailMessage]:
        msg = self.get_selected_message()
        if msg:
            self.mark_read(msg.message_id)
            self._view_mode = "read"
        return msg

    # ── View Mode ─────────────────────────────────────────────────────

    @property
    def view_mode(self) -> str:
        return self._view_mode

    def back_to_list(self) -> None:
        self._view_mode = "list"

    # ── Rendering ─────────────────────────────────────────────────────

    def render_list(self, width: int = 72) -> List[str]:
        lines = []
        folder_name = self._current_folder.value
        counts = self.folder_counts()
        unread = counts.get(folder_name, 0)

        header = f" 📧 {folder_name}"
        if unread:
            header += f" ({unread} unread)"
        lines.append(header[:width])
        lines.append("─" * width)

        if self._search_query:
            lines.append(f" 🔍 \"{self._search_query}\"")
            lines.append("─" * width)

        msgs = self.get_messages()

        if not msgs:
            lines.append("")
            lines.append("  No messages.")
        else:
            for i, msg in enumerate(msgs):
                marker = "▸" if i == self._selected_index else " "
                star = "⭐" if msg.is_starred else "  "
                unread_dot = "●" if not msg.is_read else " "
                important = "❗" if msg.is_important else " "

                # From line
                sender = msg.from_addr.name or msg.from_addr.email
                if self._current_folder == MailFolder.SENT:
                    sender = msg.to_display if msg.to else "(no recipients)"

                line = f"{marker}{unread_dot}{star}{important} {sender[:25]}"
                time_str = msg.time_ago
                line += f"{' ' * max(1, width - len(line) - len(time_str))}{time_str}"
                lines.append(line[:width])

                # Subject
                subj = f"   {msg.subject[:width - 4]}"
                lines.append(subj[:width])

                # Preview
                preview = f"   {msg.preview[:width - 4]}"
                lines.append(preview[:width])
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Read  C:Compose  ⭐:Star  Del:Trash")
        return lines

    def render_read(self, width: int = 72) -> List[str]:
        msg = self.get_selected_message()
        if not msg:
            return ["No message selected"]

        lines = []
        lines.append(f" 📧 {msg.subject[:width - 4]}")
        lines.append("─" * width)
        lines.append(f" From: {msg.from_display}")
        lines.append(f"   To: {msg.to_display}")
        if msg.cc:
            lines.append(f"   CC: {', '.join(a.display for a in msg.cc)}")
        lines.append(f"  Date: {msg.date_str}")
        lines.append("─" * width)

        # Body
        body_lines = msg.body.split("\n")
        for line in body_lines:
            # Word wrap
            while len(line) > width:
                split = line[:width].rfind(" ")
                if split <= 0:
                    split = width
                lines.append(f" {line[:split]}")
                line = line[split:].lstrip()
            lines.append(f" {line}")

        lines.append("")
        lines.append("─" * width)
        lines.append(" R:Reply  A:Reply All  F:Forward  Del:Trash  Esc:Back")
        return lines

    def render_compose(self, width: int = 72) -> List[str]:
        if not self._compose:
            return ["No message being composed"]

        c = self._compose
        lines = []

        mode_label = {
            ComposeMode.NEW: "New Message",
            ComposeMode.REPLY: "Reply",
            ComposeMode.REPLY_ALL: "Reply All",
            ComposeMode.FORWARD: "Forward",
            ComposeMode.EDIT_DRAFT: "Edit Draft",
        }[c.mode]

        lines.append(f" ✉️  {mode_label}")
        if c.is_dirty:
            lines.append(" (unsaved)")
        lines.append("─" * width)

        to_active = "▸" if c.active_field == "to" else " "
        cc_active = "▸" if c.active_field == "cc" else " "
        subj_active = "▸" if c.active_field == "subject" else " "

        lines.append(f"{to_active} To: {c.to_text[:width - 6]}")
        lines.append(f"{cc_active} Cc: {c.cc_text[:width - 6]}")
        lines.append(f"{subj_active} Subject: {c.subject[:width - 11]}")
        lines.append("─" * width)

        body_active = "▸" if c.active_field == "body" else " "
        lines.append(f"{body_active} Body:")

        body_lines = c.body.split("\n")
        for line in body_lines[-20:]:  # Show last 20 lines
            lines.append(f" {line[:width - 2]}")

        if not body_lines or body_lines[-1]:
            lines.append("")

        lines.append("─" * width)
        lines.append(" Tab:Field  Ctrl+S:Send  Ctrl+D:Draft  Esc:Discard")
        return lines

    def render(self, width: int = 72, height: int = 30) -> List[str]:
        if self._view_mode == "compose":
            return self.render_compose(width)
        elif self._view_mode == "read":
            return self.render_read(width)
        return self.render_list(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "compose":
            return self._handle_compose_key(key)
        elif self._view_mode == "read":
            return self._handle_read_key(key)
        return self._handle_list_key(key)

    def _handle_list_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.open_selected()
            return "open"
        elif key == "c":
            self.compose_new()
            return "compose"
        elif key == "/":
            return "search"
        elif key == "Delete":
            msg = self.get_selected_message()
            if msg:
                self.delete_message(msg.message_id)
            return "delete"
        elif key == "*":
            msg = self.get_selected_message()
            if msg:
                self.toggle_star(msg.message_id)
            return "toggle_star"
        elif key == "1":
            self.set_folder(MailFolder.INBOX)
            return "folder_inbox"
        elif key == "2":
            self.set_folder(MailFolder.SENT)
            return "folder_sent"
        elif key == "3":
            self.set_folder(MailFolder.DRAFTS)
            return "folder_drafts"
        elif key == "4":
            self.set_folder(MailFolder.SPAM)
            return "folder_spam"
        elif key == "5":
            self.set_folder(MailFolder.TRASH)
            return "folder_trash"
        elif key == "6":
            self.set_folder(MailFolder.ARCHIVE)
            return "folder_archive"
        return None

    def _handle_read_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.back_to_list()
            return "back"
        elif key == "r":
            msg = self.get_selected_message()
            if msg:
                self.reply(msg.message_id)
            return "reply"
        elif key == "a":
            msg = self.get_selected_message()
            if msg:
                self.reply_all(msg.message_id)
            return "reply_all"
        elif key == "f":
            msg = self.get_selected_message()
            if msg:
                self.forward(msg.message_id)
            return "forward"
        elif key == "Delete":
            msg = self.get_selected_message()
            if msg:
                self.delete_message(msg.message_id)
                self.back_to_list()
            return "delete"
        elif key == "*":
            msg = self.get_selected_message()
            if msg:
                self.toggle_star(msg.message_id)
            return "toggle_star"
        return None

    def _handle_compose_key(self, key: str) -> Optional[str]:
        c = self._compose
        if not c:
            return None

        if key == "Escape":
            if c.is_dirty:
                self.save_draft()
            self.discard_compose()
            return "discard"
        elif key == "Ctrl+s":
            self.send_compose()
            return "send"
        elif key == "Ctrl+d":
            self.save_draft()
            return "save_draft"
        elif key == "Tab":
            fields = ["to", "cc", "subject", "body"]
            idx = fields.index(c.active_field) if c.active_field in fields else 0
            c.active_field = fields[(idx + 1) % len(fields)]
            return "next_field"
        elif key == "Backspace":
            if c.active_field == "to":
                c.to_text = c.to_text[:-1]
            elif c.active_field == "cc":
                c.cc_text = c.cc_text[:-1]
            elif c.active_field == "subject":
                c.subject = c.subject[:-1]
            elif c.active_field == "body":
                c.body = c.body[:-1]
            c.is_dirty = True
            return "backspace"
        elif len(key) == 1:
            if c.active_field == "to":
                c.to_text += key
            elif c.active_field == "cc":
                c.cc_text += key
            elif c.active_field == "subject":
                c.subject += key
            elif c.active_field == "body":
                c.body += key
            c.is_dirty = True
            return "insert"
        return None

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_send(self, cb: Callable) -> None:
        self._on_send.append(cb)

    def on_receive(self, cb: Callable) -> None:
        self._on_receive.append(cb)

    def _notify(self, event: str, *args) -> None:
        cbs = {"send": self._on_send, "receive": self._on_receive}
        for cb in cbs.get(event, []):
            try:
                cb(*args)
            except Exception:
                pass
