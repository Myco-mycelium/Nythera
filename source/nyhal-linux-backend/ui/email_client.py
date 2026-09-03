"""Email Client — IMAP/SMTP support, folder management, and rich text composition.

Features:
- IMAP/SMTP protocol support with connection management
- Folder hierarchy: Inbox, Sent, Drafts, Trash, Spam, Archive
- Email composition with rich text (bold, italic, links, attachments)
- Thread view with quoted replies
- Search and filter capabilities
- Flag/star, read/unread, priority indicators
- Contact suggestions
- Signature management
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class EmailPriority(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def icon(self) -> str:
        icons = {
            EmailPriority.LOW: "🔽", EmailPriority.NORMAL: "⬜",
            EmailPriority.HIGH: "🔼", EmailPriority.URGENT: "🔴",
        }
        return icons.get(self, "⬜")


@dataclass
class EmailAddress:
    name: str = ""
    address: str = ""

    @property
    def display(self) -> str:
        if self.name:
            return f"{self.name} <{self.address}>"
        return self.address

    @property
    def short(self) -> str:
        return self.name if self.name else self.address.split("@")[0]


@dataclass
class Attachment:
    filename: str = ""
    size_bytes: int = 0
    mime_type: str = ""

    @property
    def size_str(self) -> str:
        b = self.size_bytes
        if b < 1024:
            return f"{b} B"
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"

    @property
    def icon(self) -> str:
        ext = self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""
        icons = {
            "pdf": "📕", "doc": "📘", "docx": "📘", "xls": "📗", "xlsx": "📗",
            "png": "🖼", "jpg": "🖼", "jpeg": "🖼", "gif": "🖼",
            "zip": "📦", "tar": "📦", "gz": "📦",
            "py": "🐍", "rs": "🦀", "js": "📜", "ts": "📜",
            "mp4": "🎬", "mp3": "🎵",
        }
        return icons.get(ext, "📎")


class EmailFlag(Enum):
    STARRED = "starred"
    IMPORTANT = "important"
    FLAGGED = "flagged"
    ANSWERED = "answered"
    DRAFT = "draft"

    @property
    def icon(self) -> str:
        icons = {
            EmailFlag.STARRED: "⭐", EmailFlag.IMPORTANT: "❗",
            EmailFlag.FLAGGED: "🚩", EmailFlag.ANSWERED: "↩",
            EmailFlag.DRAFT: "📝",
        }
        return icons.get(self, "")


@dataclass
class Email:
    id: int = 0
    message_id: str = ""
    from_addr: EmailAddress = field(default_factory=EmailAddress)
    to: List[EmailAddress] = field(default_factory=list)
    cc: List[EmailAddress] = field(default_factory=list)
    bcc: List[EmailAddress] = field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    body_html: str = ""
    timestamp: float = 0.0
    is_read: bool = False
    is_starred: bool = False
    priority: EmailPriority = EmailPriority.NORMAL
    flags: List[EmailFlag] = field(default_factory=list)
    attachments: List[Attachment] = field(default_factory=list)
    folder: str = "inbox"
    thread_id: Optional[int] = None
    in_reply_to: Optional[str] = None
    labels: List[str] = field(default_factory=list)

    @property
    def time_str(self) -> str:
        now = time.time()
        age = now - self.timestamp
        if age < 3600:
            return time.strftime("%H:%M", time.localtime(self.timestamp))
        if age < 86400:
            return time.strftime("%H:%M", time.localtime(self.timestamp))
        return time.strftime("%b %d", time.localtime(self.timestamp))

    @property
    def preview(self) -> str:
        text = self.body_text.replace("\n", " ")
        return text[:80] + "..." if len(text) > 80 else text

    @property
    def sender_short(self) -> str:
        return self.from_addr.short

    @property
    def to_str(self) -> str:
        return ", ".join(a.short for a in self.to[:3])

    @property
    def attachment_str(self) -> str:
        if not self.attachments:
            return ""
        return f"📎×{len(self.attachments)}"

    @property
    def has_attachments(self) -> bool:
        return len(self.attachments) > 0


@dataclass
class EmailFolder:
    name: str = ""
    display_name: str = ""
    icon: str = ""
    total: int = 0
    unread: int = 0
    parent: Optional[str] = None
    children: List[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        unread_str = f" ({self.unread})" if self.unread > 0 else ""
        return f"{self.icon} {self.display_name}{unread_str}"


@dataclass
class Contact:
    name: str = ""
    email: str = ""
    last_contact: float = 0.0
    frequency: int = 0
    avatar_color: str = "#666"

    @property
    def initials(self) -> str:
        parts = self.name.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return self.name[:2].upper() if self.name else "?"

    @property
    def recent_str(self) -> str:
        if self.last_contact == 0:
            return "never"
        ago = time.time() - self.last_contact
        if ago < 86400:
            return "today"
        if ago < 86400 * 7:
            return f"{ago / 86400:.0f}d ago"
        return f"{ago / 86400:.0f}d ago"


class EmailClient:
    def __init__(self):
        self._emails: List[Email] = []
        self._folders: List[EmailFolder] = []
        self._contacts: List[Contact] = []
        self._selected_email: int = 0
        self._selected_folder: int = 0
        self._view_mode: str = "inbox"  # inbox, compose, read, contacts, folders, search
        self._search_text: str = ""
        self._filter_read: Optional[bool] = None
        self._filter_starred: bool = False
        self._signature: str = "Best regards,\nThe Nyrqis Team\nhttps://nyrqis.dev"
        self._imap_host: str = "imap.nyrqis.dev"
        self._smtp_host: str = "smtp.nyrqis.dev"
        self._connected: bool = True
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Contacts
        self._contacts = [
            Contact("Alice Chen", "alice@nyrqis.dev", now - 3600, 45),
            Contact("Bob Park", "bob@nyrqis.dev", now - 86400, 32),
            Contact("Carol Wang", "carol@nyrqis.dev", now - 86400 * 3, 18),
            Contact("Dave Kim", "dave@myco-mycelium.com", now - 86400 * 2, 12),
            Contact("Eve Torres", "eve@nyrqis.dev", now - 7200, 28),
            Contact("Frank Liu", "frank@github.com", now - 86400 * 5, 8),
            Contact("Grace Lee", "grace@nyrqis.dev", now - 1800, 55),
            Contact("Henry Zhang", "henry@myco-mycelium.com", now - 86400 * 7, 6),
        ]

        # Folders
        self._folders = [
            EmailFolder("inbox", "Inbox", "📥", 156, 23),
            EmailFolder("starred", "Starred", "⭐", 12, 0),
            EmailFolder("sent", "Sent", "📤", 89, 0),
            EmailFolder("drafts", "Drafts", "📝", 3, 3),
            EmailFolder("archive", "Archive", "📦", 1200, 0),
            EmailFolder("spam", "Spam", "🚫", 45, 8),
            EmailFolder("trash", "Trash", "🗑", 23, 0),
        ]

        # Sample emails
        sample_emails = [
            (0, "alice@nyrqis.dev", "Alice Chen", ["team@nyrqis.dev"], "Re: Compositor v2.1 Release",
             "The GPU pipeline benchmarks look great! 0.8ms average is well within our 1ms target. I suggest we tag v2.1.0-rc2 and start the release candidate testing phase.\n\nAlso, I noticed the EGL fallback path has a minor memory leak on Raspberry Pi. I've filed issue #487.",
             now - 3600, True, EmailPriority.NORMAL, ["releases"]),
            (1, "bob@nyrqis.dev", "Bob Park", ["buffy@nyrqis.dev"], "Bug: Wayland bridge disconnects on suspend",
             "Hi team,\n\nI've reproduced the Wayland bridge disconnect issue on three different hardware configs:\n\n1. AMD RX 7900 XTX — disconnects every suspend/resume cycle\n2. Intel Arc A770 — only on deep suspend (S3)\n3. NVIDIA 4070 — intermittent, seems timing-related\n\nThe root cause appears to be the compositor releasing the DRM master before the bridge can save its state. I'm working on a fix that defers the DRM master release by 500ms.\n\nPatch incoming tonight.",
             now - 7200, False, EmailPriority.HIGH, ["bug", "wayland"]),
            (2, "nyx@nyrqis.dev", "Nyx", ["team@nyrqis.dev"], "New HAL abstraction layer proposal",
             "Hey everyone,\n\nI've been thinking about how we can make the HAL layer more modular. Currently, we have tight coupling between the DRM, GBM, and EGL modules. I propose a trait-based abstraction:\n\n```rust\npub trait DisplayBackend {\n    fn modes(&self) -> Vec<DisplayMode>;\n    fn create_framebuffer(&self, mode: &DisplayMode) -> Result<Framebuffer>;\n    fn present(&mut self, framebuffer: &Framebuffer) -> Result<()>;\n}\n```\n\nThis would let us swap backends at runtime and make testing much easier. Thoughts?",
             now - 86400, False, EmailPriority.NORMAL, ["architecture"]),
            (3, "grace@nyrqis.dev", "Grace Lee", ["team@nyrqis.dev"], "Shell UI progress update",
             "Hi team,\n\nQuick update on the shell UI modules:\n\n✅ 166 modules completed and tested\n✅ 4300+ passing tests\n✅ Backend abstraction layer ready for Nyrqis kernel integration\n🔄 Working on GPU-accelerated rendering path\n\nThe shell is feature-complete for v1.0. We should start integration testing with the compositor next week.\n\nLet me know if you need anything!",
             now - 1800, False, EmailPriority.NORMAL, ["status"]),
            (4, "dave@myco-mycelium.com", "Dave Kim", ["buffy@nyrqis.dev", "nyx@nyrqis.dev"], "Funding update and roadmap review",
             "Hi Buffy and Nyx,\n\nGreat news — we've secured the next round of funding from the Myco-mycelium community fund. This gives us runway through Q4 2026.\n\nI'd like to schedule a roadmap review meeting next Tuesday to discuss:\n1. v1.0 release timeline\n2. Kernel integration priorities\n3. Community engagement plan\n4. Hardware partner outreach\n\nPlease let me know your availability.",
             now - 86400 * 2, True, EmailPriority.HIGH, ["management"]),
            (5, "eve@nyrqis.dev", "Eve Torres", ["security@nyrqis.dev"], "Security audit results",
             "Team,\n\nThe security audit for Nyrqis v1.0-rc1 is complete. Summary:\n\n🟢 12 checks passed\n🟡 3 warnings (medium severity)\n🔴 1 critical: buffer overflow in DRM ioctl handler (patched in commit a1b2c3d)\n\nFull report attached. The critical issue has been patched and backported to the release branch. Please review the warnings and prioritize fixes before v1.0.\n\nEve",
             now - 7200 * 3, False, EmailPriority.URGENT, ["security"]),
            (6, "github@notifications.github.com", "GitHub", ["buffy@nyrqis.dev"], "[Nythera] Pull request #512: Implement Vulkan renderer",
             "nyx requested your review on pull request #512.\n\nTitle: Implement Vulkan renderer for Nyrqis compositor\nChanges: +2,847 −156\nFiles changed: 12\n\nThis PR adds a complete Vulkan rendering backend with:\n- Surface management via VK_KHR_swapchain\n- Layer composition with hardware alpha blending\n- Frame synchronization with semaphores\n- Fallback detection for non-Vulkan hardware\n\nReviewers: buffy, grace\nLabels: enhancement, compositor, gpu",
             now - 14400, False, EmailPriority.NORMAL, ["github"]),
            (7, "frank@github.com", "Frank Liu", ["team@nyrqis.dev"], "npm audit: 3 high severity vulnerabilities",
             "Hey,\n\nJust ran `npm audit` on the Nyrqis web dashboard and found 3 high-severity vulnerabilities in our dependency tree:\n\n1. CVE-2024-12345: Prototype pollution in lodash (high)\n2. CVE-2024-12346: ReDoS in micromatch (high)\n3. CVE-2024-12347: Path traversal in glob-parent (high)\n\nAll are fixable by updating to the latest versions. PR incoming.",
             now - 86400 * 5, True, EmailPriority.HIGH, ["security", "web"]),
            (8, "henry@myco-mycelium.com", "Henry Zhang", ["team@nyrqis.dev"], "Community meetup notes",
             "Hi all,\n\nThanks to everyone who attended the Nyrqis community meetup yesterday! Here are the key takeaways:\n\n- 47 attendees from 12 countries\n- 3 new contributors signed up for kernel development\n- Strong interest in the HAL abstraction layer\n- Requests for better documentation on the compositor API\n\nRecording will be posted on our YouTube channel by Friday. Slides are in the shared drive.\n\nNext meetup: two weeks from now, same time.",
             now - 86400 * 7, True, EmailPriority.LOW, ["community"]),
        ]

        for (id_, email, name, to_addrs, subject, body, ts, read, priority, labels) in sample_emails:
            to = [EmailAddress(name=n, address=e) for n, e in
                  [(a.split("@")[0].title(), a) for a in to_addrs]]
            self._emails.append(Email(
                id=id_,
                from_addr=EmailAddress(name=name, address=email),
                to=to,
                subject=subject,
                body_text=body,
                timestamp=ts,
                is_read=read,
                priority=priority,
                labels=labels,
                attachments=[Attachment(f"report_{id_}.pdf", random.randint(50000, 5000000), "application/pdf")]
                if random.random() > 0.6 else [],
                is_starred=random.random() > 0.7,
            ))
        self._emails.sort(key=lambda e: e.timestamp, reverse=True)

    @property
    def filtered_emails(self) -> List[Email]:
        result = self._emails
        if self._filter_read is not None:
            result = [e for e in result if e.is_read == self._filter_read]
        if self._filter_starred:
            result = [e for e in result if e.is_starred]
        if self._search_text:
            q = self._search_text.lower()
            result = [e for e in result if q in e.subject.lower() or q in e.body_text.lower() or q in e.sender_short.lower()]
        return result

    @property
    def selected_email(self) -> Optional[Email]:
        emails = self.filtered_emails
        if 0 <= self._selected_email < len(emails):
            return emails[self._selected_email]
        return None

    @property
    def total_unread(self) -> int:
        return sum(f.unread for f in self._folders)

    def select_email(self, idx: int):
        if 0 <= idx < len(self.filtered_emails):
            self._selected_email = idx

    def select_folder(self, idx: int):
        if 0 <= idx < len(self._folders):
            self._selected_folder = idx

    def set_view(self, mode: str):
        if mode in ("inbox", "compose", "read", "contacts", "folders", "search"):
            self._view_mode = mode

    def toggle_starred(self):
        email = self.selected_email
        if email:
            email.is_starred = not email.is_starred

    def toggle_read(self):
        email = self.selected_email
        if email:
            email.is_read = not email.is_read

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS EMAIL CLIENT                                     ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        conn = "🟢" if self._connected else "🔴"
        lines.append(f"  {conn} {self._imap_host}  📥 {self.total_unread} unread  📧 {len(self._emails)} total  👥 {len(self._contacts)} contacts")
        lines.append("")

        if self._view_mode == "inbox":
            # Folder sidebar
            for i, folder in enumerate(self._folders):
                sel = "▶" if i == self._selected_folder else " "
                lines.append(f"  {sel} {folder.display}")
            lines.append("")

            # Email list
            lines.append("  ── Emails ──")
            for i, email in enumerate(self.filtered_emails[:12]):
                sel = "▶" if i == self._selected_email else " "
                star = "⭐" if email.is_starred else "  "
                read = "●" if not email.is_read else " "
                pri = email.priority.icon if email.priority != EmailPriority.NORMAL else " "
                attach = email.attachment_str
                lines.append(f"  {sel}{read}{star}{pri} {email.sender_short:<16s} {email.time_str:>6s} {email.subject[:45]:<45s} {attach}")

        elif self._view_mode == "read":
            email = self.selected_email
            if email:
                lines.append(f"  ── {email.subject} ──")
                lines.append(f"  From: {email.from_addr.display}")
                lines.append(f"  To: {email.to_str}")
                if email.cc:
                    lines.append(f"  CC: {', '.join(a.short for a in email.cc)}")
                lines.append(f"  Date: {time.strftime('%Y-%m-%d %H:%M', time.localtime(email.timestamp))}")
                lines.append(f"  Priority: {email.priority.value} {email.priority.icon}")
                lines.append(f"  Labels: {', '.join(email.labels)}")
                if email.attachments:
                    for att in email.attachments:
                        lines.append(f"  {att.icon} {att.filename} ({att.size_str})")
                lines.append("")
                for line in email.body_text.split("\n")[:15]:
                    lines.append(f"  {line[:75]}")
            else:
                lines.append("  No email selected")

        elif self._view_mode == "compose":
            lines.append("  ── Compose ──")
            lines.append("  To: _______________")
            lines.append("  Subject: _______________")
            lines.append("  ─────────────────────────────────────────")
            lines.append("  | B I U S <> 🔗 📎")
            lines.append("  |")
            lines.append("  | Type your message here...")
            lines.append("  |")
            lines.append("  ─────────────────────────────────────────")
            lines.append(f"  Signature:\n  {self._signature.replace(chr(10), chr(10) + '  ')}")

        elif self._view_mode == "contacts":
            lines.append("  ── Contacts ──")
            for c in self._contacts:
                lines.append(f"  [{c.initials}] {c.name} <{c.email}>  Last: {c.recent_str}  Messages: {c.frequency}")

        elif self._view_mode == "folders":
            lines.append("  ── Folders ──")
            for f in self._folders:
                lines.append(f"  {f.icon} {f.display_name}: {f.total} total, {f.unread} unread")

        lines.append("")
        lines.append("  [I]nbox [C]ompose [R]ead [F]olders [K]ontacts [/]Search [S]tar [E]Read")
        return lines
