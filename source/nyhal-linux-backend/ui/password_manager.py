"""
Nyrqis Vault — password manager with secure storage and generation.

Features:
- Password vault with categories (Login, Credit Card, Secure Note, Identity)
- Password generator with customizable length, character sets
- Search across all entries
- Favorite/pin frequently used entries
- Password strength indicator
- Copy-to-clipboard simulation
- Import/export vault
- Auto-lock after timeout
- Tag-based organization
"""

import re
import time
import hashlib
import secrets
import string
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Set
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class EntryType(Enum):
    LOGIN = "Login"
    CREDIT_CARD = "Credit Card"
    SECURE_NOTE = "Secure Note"
    IDENTITY = "Identity"
    API_KEY = "API Key"
    SSH_KEY = "SSH Key"


ENTRY_TYPE_ICONS = {
    EntryType.LOGIN: "🔑",
    EntryType.CREDIT_CARD: "💳",
    EntryType.SECURE_NOTE: "📝",
    EntryType.IDENTITY: "👤",
    EntryType.API_KEY: "🔐",
    EntryType.SSH_KEY: "🗝️",
}


@dataclass
class PasswordEntry:
    """A password vault entry."""
    title: str
    entry_type: EntryType = EntryType.LOGIN
    username: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""
    category: str = "Logins"
    tags: List[str] = field(default_factory=list)
    favorite: bool = False
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)
    last_used: float = 0.0
    entry_id: str = ""
    # Credit card specific
    card_number: str = ""
    card_expiry: str = ""
    card_cvv: str = ""
    card_holder: str = ""

    def __post_init__(self):
        if not self.entry_id:
            self.entry_id = hashlib.md5(f"{self.title}{self.created}".encode()).hexdigest()[:8]

    @property
    def icon(self) -> str:
        return ENTRY_TYPE_ICONS.get(self.entry_type, "🔑")

    @property
    def display_title(self) -> str:
        star = " ⭐" if self.favorite else ""
        return f"{self.icon} {self.title}{star}"

    @property
    def strength(self) -> str:
        """Password strength indicator."""
        if not self.password:
            return "None"
        score = 0
        if len(self.password) >= 8:
            score += 1
        if len(self.password) >= 12:
            score += 1
        if len(self.password) >= 16:
            score += 1
        if re.search(r'[a-z]', self.password):
            score += 1
        if re.search(r'[A-Z]', self.password):
            score += 1
        if re.search(r'\d', self.password):
            score += 1
        if re.search(r'[!@#$%^&*(),.?":{}|<>]', self.password):
            score += 1

        if score <= 2:
            return "Weak 🔴"
        elif score <= 4:
            return "Fair 🟡"
        elif score <= 5:
            return "Good 🟢"
        else:
            return "Strong 💪"

    @property
    def strength_score(self) -> float:
        if not self.password:
            return 0.0
        score = 0
        if len(self.password) >= 8:
            score += 1
        if len(self.password) >= 12:
            score += 1
        if len(self.password) >= 16:
            score += 1
        if re.search(r'[a-z]', self.password):
            score += 1
        if re.search(r'[A-Z]', self.password):
            score += 1
        if re.search(r'\d', self.password):
            score += 1
        if re.search(r'[!@#$%^&*(),.?":{}|<>]', self.password):
            score += 1
        return min(1.0, score / 7)

    @property
    def masked_password(self) -> str:
        return "•" * min(len(self.password), 20) if self.password else ""

    @property
    def card_masked(self) -> str:
        if not self.card_number:
            return ""
        return f"•••• •••• •••• {self.card_number[-4:]}" if len(self.card_number) >= 4 else "••••"

    @property
    def time_ago(self) -> str:
        ts = self.last_used or self.modified
        diff = time.time() - ts
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        elif diff < 604800:
            return f"{int(diff // 86400)}d ago"
        return datetime.fromtimestamp(ts).strftime("%b %d")


# ─── Password Generator ─────────────────────────────────────────────────


class PasswordGenerator:
    """Configurable password generator."""

    def __init__(self):
        self.length: int = 20
        self.uppercase: bool = True
        self.lowercase: bool = True
        self.digits: bool = True
        self.symbols: bool = True
        self.exclude_ambiguous: bool = False
        self.exclude_chars: str = ""

    @property
    def charset(self) -> str:
        chars = ""
        if self.lowercase:
            chars += string.ascii_lowercase
        if self.uppercase:
            chars += string.ascii_uppercase
        if self.digits:
            chars += string.digits
        if self.symbols:
            chars += "!@#$%^&*()-_=+[]{}|;:,.<>?"
        if self.exclude_ambiguous:
            chars = chars.replace("l", "").replace("I", "").replace("1", "").replace("0", "").replace("O", "")
        for ch in self.exclude_chars:
            chars = chars.replace(ch, "")
        return chars

    def generate(self, count: int = 1) -> List[str]:
        """Generate password(s)."""
        charset = self.charset
        if not charset:
            charset = string.ascii_letters + string.digits
        passwords = []
        for _ in range(count):
            pw = ''.join(secrets.choice(charset) for _ in range(self.length))
            passwords.append(pw)
        return passwords

    def generate_passphrase(self, words: int = 4, separator: str = "-") -> str:
        """Generate a memorable passphrase."""
        word_list = [
            "apple", "bridge", "castle", "dragon", "eagle", "forest", "garden",
            "harbor", "island", "jungle", "knight", "lemon", "mountain", "nebula",
            "ocean", "piano", "queen", "river", "sunset", "tiger", "umbrella",
            "valley", "winter", "xenon", "yellow", "zenith", "aurora", "blaze",
            "coral", "dusk", "ember", "frost", "glow", "haze", "ivy", "jade",
            "kite", "lava", "moss", "nova", "opal", "pearl", "quartz", "rain",
            "sage", "thorn", "unity", "vapor", "willow", "zephyr",
        ]
        selected = [secrets.choice(word_list) for _ in range(words)]
        return separator.join(selected)


# ─── Password Manager ────────────────────────────────────────────────────


class PasswordManager:
    """
    Password manager for Nyrqis OS.

    Manages password vault with categories, search, and generation.
    """

    def __init__(self):
        self._entries: List[PasswordEntry] = []
        self._categories: List[str] = [
            "Logins", "Credit Cards", "Secure Notes", "Identities",
            "API Keys", "SSH Keys", "Other",
        ]
        self._selected_index: int = 0
        self._current_category: str = ""
        self._view_mode: str = "list"  # list, detail, generator
        self._search_query: str = ""
        self._filter_type: Optional[EntryType] = None
        self._show_favorites_only: bool = False
        self._show_password: bool = False
        self._generator = PasswordGenerator()
        self._generated_passwords: List[str] = []
        self._copied_entry: Optional[PasswordEntry] = None
        self._locked: bool = False
        self._last_activity: float = time.time()
        self._auto_lock_timeout: int = 300  # 5 minutes

        # Callbacks
        self._on_change: List[Callable] = []

        # Init sample data
        self._init_sample_entries()

    def _init_sample_entries(self) -> None:
        now = time.time()
        entries = [
            PasswordEntry("GitHub", EntryType.LOGIN, "user@nyrqis.os", "gh_s3cure!Pass123",
                          "https://github.com", "Main GitHub account", "Logins",
                          ["dev", "work"], True, now - 86400 * 30),
            PasswordEntry("Gmail", EntryType.LOGIN, "user@gmail.com", "Gm@il_Strong#2026!",
                          "https://mail.google.com", "Personal email", "Logins",
                          ["email", "personal"], False, now - 86400 * 60),
            PasswordEntry("AWS Console", EntryType.LOGIN, "admin@nyrqis.os", "Aws!C0ns0le#Secur3",
                          "https://console.aws.amazon.com", "Production AWS account", "Logins",
                          ["cloud", "work"], True, now - 86400 * 15),
            PasswordEntry("Nyrqis Docker Hub", EntryType.API_KEY, "", "dckr_pat_aBcDeFgHiJkLmNoP",
                          "https://hub.docker.com", "Docker Hub access token", "API Keys",
                          ["docker", "dev"], False, now - 86400 * 10),
            PasswordEntry("Vercel Token", EntryType.API_KEY, "", "vercel_tk_xYz123AbC456DeF",
                          "https://vercel.com", "Deployment token", "API Keys",
                          ["deploy", "dev"], False, now - 86400 * 5),
            PasswordEntry("Chase Visa", EntryType.CREDIT_CARD, "", "",
                          "", "Expires 08/28", "Credit Cards",
                          ["banking"], False, now - 86400 * 90,
                          card_number="4532015112830366", card_expiry="08/28",
                          card_cvv="123", card_holder="USER NYRQIS"),
            PasswordEntry("WireGuard VPN", EntryType.SECURE_NOTE, "", "",
                          "", "Server: vpn.nyrqis.os\nPort: 51820\nKey: abc123...", "Secure Notes",
                          ["vpn", "network"], False, now - 86400 * 20),
            PasswordEntry("SSH Server Key", EntryType.SSH_KEY, "root", "",
                          "", "Ed25519 key for production server", "SSH Keys",
                          ["server", "prod"], False, now - 86400 * 45),
            PasswordEntry("Netflix", EntryType.LOGIN, "user@email.com", "N3tfl!x_Str3am",
                          "https://netflix.com", "Family plan", "Logins",
                          ["entertainment"], False, now - 86400 * 120),
            PasswordEntry("Personal Notes", EntryType.SECURE_NOTE, "", "",
                          "", "Recovery codes:\n- ABCD-EFGH-IJKL\n- MNOP-QRST-UVWX\n- YZ12-3456-7890", "Secure Notes",
                          ["recovery"], False, now - 86400 * 180),
            PasswordEntry("Cloudflare", EntryType.LOGIN, "admin@nyrqis.os", "Cf!Dns#M@nager2026",
                          "https://dash.cloudflare.com", "DNS management", "Logins",
                          ["dns", "work"], False, now - 86400 * 8),
            PasswordEntry("Figma", EntryType.LOGIN, "design@nyrqis.os", "F!gm@_D3sign#Pro",
                          "https://figma.com", "Team account", "Logins",
                          ["design"], False, now - 86400 * 25),
        ]
        self._entries = entries

    # ── CRUD ──────────────────────────────────────────────────────────

    def create_entry(self, title: str, entry_type: EntryType = EntryType.LOGIN,
                     username: str = "", password: str = "", **kwargs) -> PasswordEntry:
        entry = PasswordEntry(
            title=title, entry_type=entry_type,
            username=username, password=password,
            category=kwargs.get("category", "Logins"),
            **{k: v for k, v in kwargs.items() if k != "category"},
        )
        self._entries.append(entry)
        self._notify("change")
        return entry

    def update_entry(self, entry_id: str, **kwargs) -> bool:
        entry = self.get_entry(entry_id)
        if not entry:
            return False
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        entry.modified = time.time()
        self._notify("change")
        return True

    def delete_entry(self, entry_id: str) -> bool:
        for i, e in enumerate(self._entries):
            if e.entry_id == entry_id:
                self._entries.pop(i)
                self._notify("change")
                return True
        return False

    def get_entry(self, entry_id: str) -> Optional[PasswordEntry]:
        for e in self._entries:
            if e.entry_id == entry_id:
                return e
        return None

    def toggle_favorite(self, entry_id: str) -> bool:
        entry = self.get_entry(entry_id)
        if entry:
            entry.favorite = not entry.favorite
            return entry.favorite
        return False

    def copy_password(self, entry_id: str) -> Optional[str]:
        entry = self.get_entry(entry_id)
        if entry:
            self._copied_entry = entry
            entry.last_used = time.time()
            return entry.password
        return None

    def copy_username(self, entry_id: str) -> Optional[str]:
        entry = self.get_entry(entry_id)
        if entry:
            return entry.username
        return None

    # ── Queries ───────────────────────────────────────────────────────

    def get_entries(self) -> List[PasswordEntry]:
        entries = list(self._entries)

        if self._current_category:
            entries = [e for e in entries if e.category == self._current_category]
        if self._filter_type:
            entries = [e for e in entries if e.entry_type == self._filter_type]
        if self._show_favorites_only:
            entries = [e for e in entries if e.favorite]
        if self._search_query:
            q = self._search_query.lower()
            entries = [e for e in entries
                       if q in e.title.lower() or q in e.username.lower() or
                       q in e.url.lower() or q in e.notes.lower() or
                       any(q in tag for tag in e.tags)]

        # Sort: favorites first, then by last used
        entries.sort(key=lambda e: (-e.favorite, -(e.last_used or e.modified)))
        return entries

    def search(self, query: str) -> List[PasswordEntry]:
        self._search_query = query
        return self.get_entries()

    @property
    def categories(self) -> List[str]:
        return list(self._categories)

    def set_category(self, category: str) -> None:
        self._current_category = category
        self._selected_index = 0

    @property
    def current_category(self) -> str:
        return self._current_category

    def category_count(self, category: str = "") -> int:
        target = category or self._current_category
        if target:
            return len([e for e in self._entries if e.category == target])
        return len(self._entries)

    @property
    def total_entries(self) -> int:
        return len(self._entries)

    @property
    def total_logins(self) -> int:
        return len([e for e in self._entries if e.entry_type == EntryType.LOGIN])

    # ── Generator ─────────────────────────────────────────────────────

    @property
    def generator(self) -> PasswordGenerator:
        return self._generator

    def generate_passwords(self, count: int = 5) -> List[str]:
        self._generated_passwords = self._generator.generate(count)
        return self._generated_passwords

    def generate_passphrase(self) -> str:
        return self._generator.generate_passphrase()

    @property
    def generated_passwords(self) -> List[str]:
        return list(self._generated_passwords)

    # ── View State ────────────────────────────────────────────────────

    def open_entry(self, entry_id: str = None) -> Optional[PasswordEntry]:
        if entry_id:
            entry = self.get_entry(entry_id)
            if entry:
                self._copied_entry = entry
                self._view_mode = "detail"
                self._show_password = False
                return entry
        entries = self.get_entries()
        if 0 <= self._selected_index < len(entries):
            self._copied_entry = entries[self._selected_index]
            self._view_mode = "detail"
            self._show_password = False
            return entries[self._selected_index]
        return None

    def close_entry(self) -> None:
        self._copied_entry = None
        self._view_mode = "list"
        self._show_password = False

    def toggle_show_password(self) -> bool:
        self._show_password = not self._show_password
        return self._show_password

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def selected_entry(self) -> Optional[PasswordEntry]:
        return self._copied_entry

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        entries = self.get_entries()
        self._selected_index = min(len(entries) - 1, self._selected_index + 1)

    # ── Lock ──────────────────────────────────────────────────────────

    def lock(self) -> None:
        self._locked = True

    def unlock(self, master_password: str) -> bool:
        # In real implementation, would verify against stored hash
        if master_password:
            self._locked = False
            self._last_activity = time.time()
            return True
        return False

    @property
    def is_locked(self) -> bool:
        return self._locked

    def check_auto_lock(self) -> bool:
        if time.time() - self._last_activity > self._auto_lock_timeout:
            self.lock()
            return True
        return False

    # ── Rendering ─────────────────────────────────────────────────────

    def render_list(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🔐 Nyrqis Vault")
        lines.append("─" * width)

        if self._search_query:
            lines.append(f" 🔍 \"{self._search_query}\"")

        entries = self.get_entries()
        lines.append(f" {len(entries)} entries")
        lines.append("─" * width)

        if not entries:
            lines.append("  No entries found.")
        else:
            for i, entry in enumerate(entries):
                marker = "▸" if i == self._selected_index else " "
                line = f"{marker} {entry.display_title[:width - 8]}"
                lines.append(line[:width])

                # Details
                if entry.username:
                    lines.append(f"   👤 {entry.username[:width - 5]}")
                if entry.url:
                    lines.append(f"   🔗 {entry.url[:width - 5]}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Detail  N:New  G:Generate  S:Search")
        return lines

    def render_detail(self, width: int = 60) -> List[str]:
        entry = self._copied_entry
        if not entry:
            return ["No entry selected"]

        lines = []
        lines.append(f" {entry.icon} {entry.title}")
        lines.append("─" * width)

        if entry.username:
            lines.append(f"  👤 Username:  {entry.username}")
        if entry.password:
            if self._show_password:
                lines.append(f"  🔑 Password:  {entry.password}")
            else:
                lines.append(f"  🔑 Password:  {entry.masked_password}  (press P to show)")
            lines.append(f"  💪 Strength:  {entry.strength}")
        if entry.url:
            lines.append(f"  🔗 URL:       {entry.url}")
        if entry.card_number:
            lines.append(f"  💳 Card:      {entry.card_masked}")
            lines.append(f"  📅 Expires:   {entry.card_expiry}")
            lines.append(f"  👤 Holder:    {entry.card_holder}")
        if entry.notes:
            lines.append("")
            lines.append(f"  📝 Notes:")
            for line in entry.notes.split("\n"):
                lines.append(f"     {line[:width - 6]}")

        lines.append("")
        lines.append(f"  📁 Category:  {entry.category}")
        if entry.tags:
            lines.append(f"  🏷️  Tags:      {', '.join(entry.tags)}")
        lines.append(f"  ⭐ Favorite:  {'Yes' if entry.favorite else 'No'}")
        lines.append(f"  📅 Created:   {datetime.fromtimestamp(entry.created).strftime('%Y-%m-%d')}")
        lines.append(f"  📅 Modified:  {datetime.fromtimestamp(entry.modified).strftime('%Y-%m-%d')}")
        if entry.last_used:
            lines.append(f"  📅 Last used: {entry.time_ago}")

        lines.append("─" * width)
        lines.append(" Esc:Back  P:Show/Hide PW  C:Copy PW  U:Copy User  ⭐:Favorite")
        return lines

    def render_generator(self, width: int = 60) -> List[str]:
        lines = []
        g = self._generator
        lines.append(" 🔐 Password Generator")
        lines.append("─" * width)
        lines.append(f"  Length:       {g.length}")
        lines.append(f"  Uppercase:    {'✅' if g.uppercase else '❌'}")
        lines.append(f"  Lowercase:    {'✅' if g.lowercase else '❌'}")
        lines.append(f"  Digits:       {'✅' if g.digits else '❌'}")
        lines.append(f"  Symbols:      {'✅' if g.symbols else '❌'}")
        lines.append(f"  No ambiguous: {'✅' if g.exclude_ambiguous else '❌'}")
        lines.append("─" * width)

        if self._generated_passwords:
            lines.append("  Generated:")
            for i, pw in enumerate(self._generated_passwords):
                lines.append(f"  {i + 1}. {pw}")

        lines.append("")
        lines.append("─" * width)
        lines.append(" Esc:Back  Space:Generate  +/-:Length  ↑↓:Navigate")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        if self._view_mode == "detail":
            return self.render_detail(width)
        elif self._view_mode == "generator":
            return self.render_generator(width)
        return self.render_list(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "detail":
            return self._handle_detail_key(key)
        elif self._view_mode == "generator":
            return self._handle_generator_key(key)
        return self._handle_list_key(key)

    def _handle_list_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.open_entry()
            return "detail"
        elif key == "g":
            self._view_mode = "generator"
            self.generate_passwords()
            return "generator"
        elif key == "/":
            return "search"
        return None

    def _handle_detail_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.close_entry()
            return "back"
        elif key == "p":
            self.toggle_show_password()
            return "toggle_password"
        elif key == "c":
            if self._copied_entry:
                self.copy_password(self._copied_entry.entry_id)
            return "copy_password"
        elif key == "u":
            if self._copied_entry:
                self.copy_username(self._copied_entry.entry_id)
            return "copy_username"
        elif key == "*":
            if self._copied_entry:
                self.toggle_favorite(self._copied_entry.entry_id)
            return "toggle_favorite"
        return None

    def _handle_generator_key(self, key: str) -> Optional[str]:
        g = self._generator
        if key == "Escape":
            self._view_mode = "list"
            return "back"
        elif key == " ":
            self.generate_passwords()
            return "generate"
        elif key == "+" or key == "=":
            g.length = min(64, g.length + 2)
            return "increase_length"
        elif key == "-":
            g.length = max(4, g.length - 2)
            return "decrease_length"
        elif key == "u":
            g.uppercase = not g.uppercase
            return "toggle_uppercase"
        elif key == "l":
            g.lowercase = not g.lowercase
            return "toggle_lowercase"
        elif key == "d":
            g.digits = not g.digits
            return "toggle_digits"
        elif key == "s":
            g.symbols = not g.symbols
            return "toggle_symbols"
        return None

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_change(self, cb: Callable) -> None:
        self._on_change.append(cb)

    def _notify(self, event: str) -> None:
        for cb in self._on_change:
            try:
                cb()
            except Exception:
                pass
