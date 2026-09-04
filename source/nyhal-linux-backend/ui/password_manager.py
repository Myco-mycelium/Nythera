"""
Password Manager — secure vault, generator, and auto-fill for Nyrqis OS.
"""

import random
import string
import hashlib
import time
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


# ─── Enums ───────────────────────────────────────────────────────────────

class EntryType(Enum):
    LOGIN = "login"
    NOTE = "note"
    CREDIT_CARD = "credit_card"
    IDENTITY = "identity"
    CRYPTO = "crypto"
    SECURE_NOTE = "secure_note"


class VaultCategory(Enum):
    LOGINS = "Logins"
    CREDIT_CARDS = "Credit Cards"
    IDENTITIES = "Identities"
    SECURE_NOTES = "Secure Notes"
    ALL = "All"

    @property
    def icon(self) -> str:
        icons = {
            VaultCategory.LOGINS: "🔑",
            VaultCategory.CREDIT_CARDS: "💳",
            VaultCategory.IDENTITIES: "👤",
            VaultCategory.SECURE_NOTES: "📝",
            VaultCategory.ALL: "📁",
        }
        return icons.get(self, "?")


# ─── Password Entry ──────────────────────────────────────────────────────

@dataclass
class PasswordEntry:
    title: str = ""
    username: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""
    category: str = "Logins"
    entry_type: EntryType = EntryType.LOGIN
    favorite: bool = False
    card_number: str = ""
    entry_id: str = ""

    def __post_init__(self):
        if not self.entry_id:
            self.entry_id = secrets.token_hex(4)

    @property
    def masked_password(self) -> str:
        return "•" * len(self.password) if self.password else ""

    @property
    def strength(self) -> str:
        score = self.strength_score
        if score >= 0.8:
            return "Strong"
        elif score >= 0.5:
            return "Medium"
        return "Weak"

    @property
    def strength_score(self) -> float:
        if not self.password:
            return 0.0
        score = 0.0
        length = len(self.password)
        score += min(length / 16, 0.4)
        if any(c.isupper() for c in self.password):
            score += 0.2
        if any(c.islower() for c in self.password):
            score += 0.1
        if any(c.isdigit() for c in self.password):
            score += 0.15
        if any(c in "!@#$%^&*()_+-=[]{}|;:',.<>?/" for c in self.password):
            score += 0.15
        return min(score, 1.0)

    @property
    def icon(self) -> str:
        icons = {
            EntryType.LOGIN: "🔑",
            EntryType.CREDIT_CARD: "💳",
            EntryType.IDENTITY: "👤",
            EntryType.NOTE: "📝",
            EntryType.SECURE_NOTE: "🔒",
            EntryType.CRYPTO: "🪙",
        }
        return icons.get(self.entry_type, "📄")

    @property
    def card_masked(self) -> str:
        if not self.card_number:
            return ""
        return "•" * (len(self.card_number) - 4) + self.card_number[-4:]


# ─── Password Generator ──────────────────────────────────────────────────

class PasswordGenerator:
    """Password and passphrase generator."""

    def __init__(self):
        self.length: int = 20
        self.uppercase: bool = True
        self.lowercase: bool = True
        self.digits: bool = True
        self.symbols: bool = True
        self.exclude_ambiguous: bool = False

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
            chars += "!@#$%^&*()-_=+"
        if self.exclude_ambiguous:
            for ch in 'Il1O0':
                chars = chars.replace(ch, '')
        return chars or string.ascii_letters

    def generate(self, count: int = 1) -> List[str]:
        passwords = []
        for _ in range(count):
            pw = "".join(secrets.choice(self.charset) for _ in range(self.length))
            passwords.append(pw)
        return passwords

    def generate_passphrase(self, word_count: int = 4) -> str:
        words = [
            "correct", "horse", "battery", "staple", "rocket", "galaxy",
            "quantum", "forest", "crystal", "phoenix", "nebula", "harmony",
            "velocity", "thunder", "voltage", "magnetic", "prismatic", "celestial",
            "wavelength", "paradox", "eclipse", "cipher", "spectrum", "nexus",
            "vortex", "cascade", "quantum", "neutron", "photon", "plasma",
        ]
        selected = [secrets.choice(words) for _ in range(word_count)]
        return "-".join(selected)

    @property
    def strength_pct(self) -> int:
        score = 0
        if self.length >= 12:
            score += 25
        elif self.length >= 8:
            score += 15
        if self.uppercase:
            score += 20
        if self.lowercase:
            score += 15
        if self.digits:
            score += 20
        if self.symbols:
            score += 20
        return min(score, 100)


# ─── Password Manager ────────────────────────────────────────────────────

class PasswordManager:
    """Main password manager with vault, search, and rendering."""

    def __init__(self):
        self.view_mode: str = "list"
        self._entries: List[PasswordEntry] = []
        self._selected_index: int = 0
        self._search_query: str = ""
        self._category_filter: str = ""
        self._show_password: bool = False
        self._current_entry: Optional[PasswordEntry] = None
        self.generator: PasswordGenerator = PasswordGenerator()
        self._is_locked: bool = False
        self._create_samples()

    def _create_samples(self):
        self._entries = [
            PasswordEntry(title="GitHub", username="dev@nyrqis.com", password="Gh!tHub2026#xK",
                          url="https://github.com", category="Logins", entry_type=EntryType.LOGIN,
                          favorite=True),
            PasswordEntry(title="Gmail", username="user@gmail.com", password="Gm@il_S3cure!",
                          url="https://mail.google.com", category="Logins", entry_type=EntryType.LOGIN),
            PasswordEntry(title="AWS Console", username="admin@nyrqis.com", password="Aws#C0ns0le!2026",
                          url="https://aws.amazon.com", category="Logins", entry_type=EntryType.LOGIN),
            PasswordEntry(title="Visa ending 0366", card_number="4532015112830366",
                          category="Credit Cards", entry_type=EntryType.CREDIT_CARD),
            PasswordEntry(title="SSH Key Passphrase", password="Ssh!K3y#Nyrqis2026",
                          category="Secure Notes", entry_type=EntryType.SECURE_NOTE),
            PasswordEntry(title="Server Root", username="root", password="R00t#Serv3r!",
                          category="Logins", entry_type=EntryType.LOGIN),
        ]

    @property
    def total_entries(self) -> int:
        return len(self._entries)

    @property
    def is_locked(self) -> bool:
        return self._is_locked

    def get_entries(self) -> List[PasswordEntry]:
        if self._category_filter:
            return [e for e in self._entries if e.category == self._category_filter]
        return self._entries[:]

    def get_entry(self, entry_id: str) -> Optional[PasswordEntry]:
        return next((e for e in self._entries if e.entry_id == entry_id), None)

    def create_entry(self, title: str, entry_type: EntryType = EntryType.LOGIN,
                     username: str = "", password: str = "", **kwargs) -> PasswordEntry:
        entry = PasswordEntry(title=title, entry_type=entry_type, username=username,
                              password=password, **kwargs)
        self._entries.append(entry)
        return entry

    def update_entry(self, entry_id: str, **kwargs) -> bool:
        entry = self.get_entry(entry_id)
        if entry:
            for k, v in kwargs.items():
                if hasattr(entry, k):
                    setattr(entry, k, v)
            return True
        return False

    def delete_entry(self, entry_id: str) -> bool:
        entry = self.get_entry(entry_id)
        if entry:
            self._entries.remove(entry)
            return True
        return False

    def toggle_favorite(self, entry_id: str):
        entry = self.get_entry(entry_id)
        if entry:
            entry.favorite = not entry.favorite

    def copy_password(self, entry_id: str) -> str:
        entry = self.get_entry(entry_id)
        return entry.password if entry else ""

    def copy_username(self, entry_id: str) -> str:
        entry = self.get_entry(entry_id)
        return entry.username if entry else ""

    def search(self, query: str) -> List[PasswordEntry]:
        q = query.lower()
        return [e for e in self._entries
                if q in e.title.lower() or q in e.username.lower() or q in e.url.lower()]

    def set_category(self, category: str):
        self._category_filter = category

    def open_entry(self, entry_id: str = None) -> Optional[PasswordEntry]:
        if entry_id:
            self._current_entry = self.get_entry(entry_id)
        elif self._entries:
            idx = min(self._selected_index, len(self._entries) - 1)
            self._current_entry = self._entries[idx]
        if self._current_entry:
            self.view_mode = "detail"
        return self._current_entry

    def close_entry(self):
        self._current_entry = None
        self.view_mode = "list"

    def toggle_show_password(self) -> bool:
        self._show_password = not self._show_password
        return self._show_password

    def generate_passwords(self, count: int = 5) -> List[str]:
        return self.generator.generate(count)

    def lock(self):
        self._is_locked = True

    def unlock(self, master_password: str = ""):
        self._is_locked = False

    def select_up(self):
        if self._selected_index > 0:
            self._selected_index -= 1

    def select_down(self):
        if self._selected_index < len(self._entries) - 1:
            self._selected_index += 1

    def handle_key(self, key: str) -> str:
        if key == "ArrowDown":
            self.select_down()
            return "navigate"
        if key == "ArrowUp":
            self.select_up()
            return "navigate"
        if key == "Enter":
            self.open_entry()
            return "open"
        if key == "Escape":
            self.close_entry()
            return "close"
        return "unknown"

    def render_list(self) -> List[str]:
        lines = ["── Password Vault ──"]
        for i, e in enumerate(self._entries):
            marker = "▸ " if i == self._selected_index else "  "
            fav = "⭐ " if e.favorite else ""
            lines.append(f"{marker}{fav}{e.icon} {e.title}")
        return lines

    def render_detail(self) -> List[str]:
        if not self._current_entry:
            return ["No entry selected."]
        e = self._current_entry
        lines = [
            f"── {e.title} ──",
            f"Type: {e.entry_type.value}",
            f"Username: {e.username}",
            f"Password: {'•' * 10 if self._show_password else e.masked_password}",
            f"URL: {e.url}",
            f"Category: {e.category}",
        ]
        return lines

    def render_generator(self) -> List[str]:
        passwords = self.generate_passwords(5)
        lines = ["── Password Generator ──"]
        for pw in passwords:
            lines.append(f"  {pw}")
        return lines

    def render(self) -> List[str]:
        if self.view_mode == "detail":
            return self.render_detail()
        if self.view_mode == "generator":
            return self.render_generator()
        return self.render_list()

    # ─── Legacy aliases ──────────────────────────────────────────────

    @property
    def breached_count(self) -> int:
        return 0

    @property
    def weak_count(self) -> int:
        return sum(1 for e in self._entries if e.strength == "Weak")

    def filtered_entries(self) -> List[PasswordEntry]:
        return self.get_entries()

    def select_entry(self, idx: int):
        self._selected_index = idx

    def set_view(self, mode: str):
        self.view_mode = mode

    def toggle_show_passwords(self):
        self._show_password = not self._show_password

    def generate_password(self) -> str:
        return self.generator.generate(1)[0]


# ─── Backward-compat aliases ─────────────────────────────────────────────

@dataclass
class CreditCard:
    number: str = ""
    name: str = ""
    expiry: str = ""

    @property
    def masked_number(self) -> str:
        return "•" * (len(self.number) - 4) + self.number[-4:] if self.number else ""

    @property
    def network_icon(self) -> str:
        if self.number.startswith("4"):
            return "💳 Visa"
        elif self.number.startswith("5"):
            return "💳 Mastercard"
        return "💳"


@dataclass
class AutoFillEntry:
    domain: str = ""
    username: str = ""
    timestamp: float = 0.0

    @property
    def time_str(self) -> str:
        delta = time.time() - self.timestamp
        if delta < 60:
            return "just now"
        elif delta < 3600:
            return f"{delta/60:.0f}m ago"
        return f"{delta/3600:.0f}h ago"
