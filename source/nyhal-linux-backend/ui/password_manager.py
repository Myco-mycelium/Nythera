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
    def name(self) -> str:
        """Alias for title (spec API)."""
        return self.title

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

    def __init__(self, length: int = 20, uppercase: bool = True,
                 lowercase: bool = True, digits: bool = True,
                 symbols: bool = True, exclude_ambiguous: bool = False):
        self.length: int = length
        self.uppercase: bool = uppercase
        self.lowercase: bool = lowercase
        self.digits: bool = digits
        self.symbols: bool = symbols
        self.exclude_ambiguous: bool = exclude_ambiguous

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

    def generate(self, count=None):
        """Generate password(s).

        generate() → single password string.
        generate(n) → list of n passwords.
        """
        if count is None:
            count = 1
            as_list = False
        else:
            as_list = True
        passwords = []
        for _ in range(count):
            pw = "".join(secrets.choice(self.charset) for _ in range(self.length))
            passwords.append(pw)
        return passwords if as_list else passwords[0]

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
        self._search_text: str = ""
        self._category_filter: str = ""
        self._show_password: bool = False
        self._show_passwords: bool = False
        self._current_entry: Optional[PasswordEntry] = None
        self.generator: PasswordGenerator = PasswordGenerator()
        self._is_locked: bool = False
        self._last_generated: str = ""
        self._create_samples()
        # Sample auto-fill rules (spec API)
        self._autofill: List[Dict] = [
            {"site": "github.com", "entry": self._entries[0], "enabled": True},
            {"site": "mail.google.com", "entry": self._entries[1], "enabled": True},
        ]

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
        result = self.generator.generate(count)
        return result if isinstance(result, list) else [result]

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
        lines = ["PASSWORD MANAGER", "=" * 40, "── Password Vault ──"]
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

    def toggle_show_passwords(self) -> bool:
        self._show_passwords = not self._show_passwords
        self._show_password = self._show_passwords
        return self._show_passwords

    # ─── Spec API (test_email_expense_password) ───────────────────
    @property
    def filtered_entries(self) -> List[PasswordEntry]:
        q = (self._search_text or self._search_query or "").lower()
        if not q:
            return self.get_entries()
        return [e for e in self._entries
                if q in e.title.lower() or q in getattr(e, "username", "").lower()
                or q in getattr(e, "url", "").lower()]

    def generate_password(self) -> str:
        pwd = self.generator.generate()
        self._last_generated = pwd
        return pwd

    def render_audit(self) -> List[str]:
        lines = ["── Security Audit ──"]
        weak = [e for e in self._entries if getattr(e, "strength_label", "") in ("Weak", "Fair")]
        lines.append(f"  Entries: {len(self._entries)}  Weak: {len(weak)}  "
                     f"Breached: {self.breached_count}")
        for e in weak:
            lines.append(f"  ⚠ {e.title}: {getattr(e, 'strength_label', 'weak')}")
        return lines

    def render_autofill(self) -> List[str]:
        lines = ["── Auto-fill Rules ──"]
        for rule in self._autofill:
            mark = "✅" if rule.get("enabled") else "⬜"
            entry = rule.get("entry")
            lines.append(f"  {mark} {rule['site']:<24} → {getattr(entry, 'title', '?')}")
        return lines

    def render(self) -> List[str]:
        if self.view_mode == "detail":
            return self.render_detail()
        if self.view_mode == "generator":
            return self.render_generator()
        if self.view_mode == "audit":
            return self.render_audit()
        if self.view_mode == "autofill":
            return self.render_autofill()
        return self.render_list()


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

# ─── Backward-compat exports ────────────────────────────────────────────
from dataclasses import dataclass as _dataclass, field as _field
from typing import Dict as _Dict, List as _List

@_dataclass
@dataclass
class VaultEntry:
    password: str = ""
    strength: float = 0.0
    entropy: float = 0.0
    has_uppercase: bool = False
    has_lowercase: bool = False
    has_digit: bool = False
    has_special: bool = False
    length: int = 0
    compromised: bool = False
    # Spec-API fields
    url: str = ""
    totp_secret: str = ""
    breach_status: str = "safe"

    def __post_init__(self):
        if self.length == 0:
            self.length = len(self.password)
        if self.strength == 0.0 and self.password:
            score = 0
            if self.has_uppercase or any(c.isupper() for c in self.password): score += 1
            if self.has_lowercase or any(c.islower() for c in self.password): score += 1
            if self.has_digit or any(c.isdigit() for c in self.password): score += 1
            if self.has_special or any(not c.isalnum() for c in self.password): score += 1
            # Variety (0..40) plus length (0..60, full marks at 20 chars)
            length_score = min(len(self.password) / 20.0, 1.0) * 60
            self.strength = round(score / 4.0 * 40 + length_score, 1)

    @property
    def strength_label(self) -> str:
        s = self.strength if self.strength <= 100 else self.strength / 100
        if s < 30: return "Weak"
        if s < 60: return "Fair"
        if s < 80: return "Strong"
        return "Very Strong"

    @property
    def strength_bar(self) -> str:
        filled = int(max(0, min(100, self.strength)) / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def password_masked(self) -> str:
        return "•" * self.length

    @property
    def domain(self) -> str:
        """Hostname part of url (e.g. github.com)."""
        if not self.url:
            return ""
        from urllib.parse import urlparse
        host = urlparse(self.url).netloc or self.url.split("/")[0]
        return host

    @property
    def has_totp(self) -> bool:
        return bool(self.totp_secret)

    @property
    def breach_icon(self) -> str:
        icons = {"safe": "✅", "breached": "🚨", "unknown": "❓"}
        return icons.get(self.breach_status, "❓")
