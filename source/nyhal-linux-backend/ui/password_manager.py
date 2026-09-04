"""Password Manager — Vault encryption, generator, and auto-fill simulation.

Features:
- Vault entries with username, password, URL, notes
- Password generator with configurable rules
- Category grouping (login, credit card, secure note, identity, crypto wallet)
- Strength meter for passwords
- TOTP support display
- Auto-fill simulation
- Export/import support
- Audit and breach checking
"""

from __future__ import annotations

import time
import random
import string
import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class VaultCategory(Enum):
    LOGIN = "login"
    CREDIT_CARD = "credit_card"
    SECURE_NOTE = "secure_note"
    IDENTITY = "identity"
    CRYPTO_WALLET = "crypto_wallet"
    API_KEY = "api_key"

    @property
    def icon(self) -> str:
        icons = {
            VaultCategory.LOGIN: "🔑", VaultCategory.CREDIT_CARD: "💳",
            VaultCategory.SECURE_NOTE: "📝", VaultCategory.IDENTITY: "🪪",
            VaultCategory.CRYPTO_WALLET: "🪙", VaultCategory.API_KEY: "🗝",
        }
        return icons.get(self, "?")


@dataclass
class VaultEntry:
    id: int = 0
    name: str = ""
    category: VaultCategory = VaultCategory.LOGIN
    username: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""
    totp_secret: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: float = 0.0
    modified_at: float = 0.0
    last_used: float = 0.0
    favorite: bool = False
    breach_checked: bool = False
    breach_status: str = "safe"  # safe, breached, unknown

    @property
    def strength(self) -> int:
        """Password strength 0-100."""
        score = 0
        p = self.password
        if len(p) >= 8:
            score += 20
        if len(p) >= 12:
            score += 10
        if len(p) >= 16:
            score += 10
        if any(c.isupper() for c in p):
            score += 15
        if any(c.islower() for c in p):
            score += 15
        if any(c.isdigit() for c in p):
            score += 15
        if any(c in string.punctuation for c in p):
            score += 15
        return min(100, score)

    @property
    def strength_bar(self) -> str:
        filled = self.strength // 5
        return "█" * filled + "░" * (20 - filled)

    @property
    def strength_label(self) -> str:
        s = self.strength
        if s >= 80:
            return "Strong 🟢"
        if s >= 60:
            return "Good 🟡"
        if s >= 40:
            return "Fair 🟠"
        if s >= 20:
            return "Weak 🔴"
        return "Very Weak 🚨"

    @property
    def strength_color(self) -> str:
        s = self.strength
        if s >= 80:
            return "green"
        if s >= 60:
            return "yellow"
        if s >= 40:
            return "orange"
        return "red"

    @property
    def breach_icon(self) -> str:
        icons = {"safe": "✅", "breached": "🚨", "unknown": "❓"}
        return icons.get(self.breach_status, "❓")

    @property
    def age_str(self) -> str:
        age = time.time() - self.modified_at
        if age < 86400:
            return "today"
        if age < 86400 * 30:
            return f"{age / 86400:.0f}d ago"
        return f"{age / (86400 * 30):.0f}mo ago"

    @property
    def password_masked(self) -> str:
        return "•" * min(16, len(self.password))

    @property
    def has_totp(self) -> bool:
        return bool(self.totp_secret)

    @property
    def domain(self) -> str:
        if not self.url:
            return ""
        return self.url.split("//")[-1].split("/")[0]


@dataclass
class CreditCard:
    number: str = ""
    expiry: str = ""
    cvv: str = ""
    cardholder: str = ""
    bank: str = ""
    network: str = "Visa"

    @property
    def masked_number(self) -> str:
        return f"****-****-****-{self.number[-4:]}" if len(self.number) >= 4 else "****"

    @property
    def network_icon(self) -> str:
        icons = {"Visa": "💳", "Mastercard": "💳", "Amex": "💳", "Discover": "💳"}
        return icons.get(self.network, "💳")


@dataclass
class PasswordGenerator:
    length: int = 20
    uppercase: bool = True
    lowercase: bool = True
    digits: bool = True
    symbols: bool = True
    exclude_ambiguous: bool = False
    custom_symbols: str = ""

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
            chars += self.custom_symbols if self.custom_symbols else string.punctuation
        if self.exclude_ambiguous:
            chars = chars.replace("l", "").replace("1", "").replace("0", "").replace("O", "")
        return chars or string.ascii_letters + string.digits

    def generate(self) -> str:
        charset = self.charset
        if not charset:
            return ""
        return "".join(random.choice(charset) for _ in range(self.length))

    @property
    def strength_pct(self) -> int:
        score = 0
        if self.length >= 12:
            score += 25
        if self.length >= 16:
            score += 15
        if self.length >= 20:
            score += 10
        count = sum([self.uppercase, self.lowercase, self.digits, self.symbols])
        score += count * 12
        return min(100, score)


@dataclass
class AutoFillEntry:
    domain: str = ""
    username: str = ""
    password: str = ""
    entry_id: int = 0
    last_used: float = 0.0

    @property
    def time_str(self) -> str:
        ago = time.time() - self.last_used
        if ago < 3600:
            return f"{ago / 60:.0f}m ago"
        if ago < 86400:
            return f"{ago / 3600:.0f}h ago"
        return f"{ago / 86400:.0f}d ago"


class PasswordManager:
    def __init__(self):
        self._entries: List[VaultEntry] = []
        self._generator = PasswordGenerator()
        self._autofill: List[AutoFillEntry] = []
        self._selected_entry: int = 0
        self._selected_category: Optional[VaultCategory] = None
        self._view_mode: str = "vault"  # vault, generator, audit, autofill, settings
        self._search_text: str = ""
        self._show_passwords: bool = False
        self._vault_unlocked: bool = True
        self._last_generated: str = ""
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        entries_data = [
            (1, "GitHub", VaultCategory.LOGIN, "buffy@nyrqis.dev", "Gh$tr0ng!P@ss2024", "https://github.com", "Main dev account", "", ["dev", "important"], True),
            (2, "AWS Console", VaultCategory.LOGIN, "admin@nyrqis.dev", "Aws#C0ns0le!Sec9", "https://console.aws.amazon.com", "Production AWS", "JBSWY3DPEHPK3PXP", ["cloud", "critical"], False),
            (3, "Gmail", VaultCategory.LOGIN, "buffy@nyrqis.dev", "Gm@il!Str0ng#Pass", "https://mail.google.com", "Primary email", "", ["email"], True),
            (4, "Visa Platinum", VaultCategory.CREDIT_CARD, "", "", "", "Credit card ending 4242", "", ["finance"], False),
            (5, "API Key - OpenAI", VaultCategory.API_KEY, "sk-nyrqis-xxxxx", "sk-proj-abc123def456ghi789jkl012mno345pqr678stu901", "https://platform.openai.com", "GPT-4 API access", "", ["ai", "api"], False),
            (6, "Seed Phrase - Ledger", VaultCategory.CRYPTO_WALLET, "Ledger Nano X", "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about", "", "BTC/ETH wallet seed", "", ["crypto", "critical"], False),
            (7, "Server SSH Key", VaultCategory.LOGIN, "root", "Pr1v@t3K3y!Nyrqis2024", "ssh://192.168.1.100", "Production server", "", ["server", "ssh"], False),
            (8, "AWS Access Key", VaultCategory.API_KEY, "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "", "IAM user access key", "", ["cloud", "aws"], False),
            (9, "Secure Note - Recovery", VaultCategory.SECURE_NOTE, "", "", "", "Master recovery codes: 1234-5678-9012-3456", "", ["recovery"], False),
            (10, "Bitwarden", VaultCategory.LOGIN, "buffy@nyrqis.dev", "B1tw@rd3n!M@ster#2024", "https://vault.bitwarden.com", "Password manager login", "", ["security"], False),
            (11, "Netflix", VaultCategory.LOGIN, "buffy@email.com", "N3tfl1x!Str3@m2024", "https://netflix.com", "Family plan", "", ["entertainment"], False),
            (12, "Coinbase", VaultCategory.CRYPTO_WALLET, "buffy@nyrqis.dev", "C01nb@se!W@llet#99", "https://coinbase.com", "Crypto exchange", "", ["crypto"], False),
            (13, "Amex Gold", VaultCategory.CREDIT_CARD, "", "", "", "Amex ending 1001", "", ["finance"], False),
            (14, "SSH - Dev Server", VaultCategory.LOGIN, "nyx", "D3vS3rv3r!Nyrqis#2024", "ssh://10.0.0.50", "Development server", "", ["dev", "server"], False),
            (15, "Personal API Key", VaultCategory.API_KEY, "nyrqis-personal", "nyq_pk_abc123def456ghi789jkl012", "https://api.nyrqis.dev", "Personal API access", "", ["api"], False),
        ]

        for (id_, name, cat, user, pwd, url, notes, totp, tags, fav) in entries_data:
            self._entries.append(VaultEntry(
                id=id_, name=name, category=cat, username=user, password=pwd,
                url=url, notes=notes, totp_secret=totp, tags=tags,
                created_at=now - random.uniform(86400 * 30, 86400 * 365),
                modified_at=now - random.uniform(0, 86400 * 90),
                last_used=now - random.uniform(0, 86400 * 30),
                favorite=fav,
                breach_checked=random.random() > 0.3,
                breach_status=random.choice(["safe", "safe", "safe", "unknown"]),
            ))

        # Auto-fill history
        self._autofill = [
            AutoFillEntry("github.com", "buffy@nyrqis.dev", "", 1, now - 3600),
            AutoFillEntry("console.aws.amazon.com", "admin@nyrqis.dev", "", 2, now - 86400),
            AutoFillEntry("mail.google.com", "buffy@nyrqis.dev", "", 3, now - 7200),
            AutoFillEntry("vault.bitwarden.com", "buffy@nyrqis.dev", "", 10, now - 14400),
            AutoFillEntry("netflix.com", "buffy@email.com", "", 11, now - 86400 * 2),
        ]

    @property
    def total_entries(self) -> int:
        return len(self._entries)

    @property
    def breached_count(self) -> int:
        return sum(1 for e in self._entries if e.breach_status == "breached")

    @property
    def weak_count(self) -> int:
        return sum(1 for e in self._entries if e.strength < 40)

    @property
    def filtered_entries(self) -> List[VaultEntry]:
        result = self._entries
        if self._selected_category:
            result = [e for e in result if e.category == self._selected_category]
        if self._search_text:
            q = self._search_text.lower()
            result = [e for e in result if q in e.name.lower() or q in e.username.lower() or q in e.url.lower()]
        return result

    def select_entry(self, idx: int):
        if 0 <= idx < len(self.filtered_entries):
            self._selected_entry = idx

    def set_view(self, mode: str):
        if mode in ("vault", "generator", "audit", "autofill", "settings"):
            self._view_mode = mode

    def toggle_show_passwords(self):
        self._show_passwords = not self._show_passwords

    def generate_password(self) -> str:
        self._last_generated = self._generator.generate()
        return self._last_generated

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS PASSWORD MANAGER                                 ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lock = "🔓" if self._vault_unlocked else "🔒"
        lines.append(f"  {lock} Vault  📦 {self.total_entries} entries  🚨 {self.breached_count} breached  🔴 {self.weak_count} weak  🔑 Auto-fill: {len(self._autofill)} sites")
        lines.append("")

        if self._view_mode == "vault":
            lines.append("  ── Vault ──")
            for i, entry in enumerate(self.filtered_entries[:15]):
                sel = "▶" if i == self._selected_entry else " "
                fav = "⭐" if entry.favorite else "  "
                cat_icon = entry.category.icon
                pwd = entry.password_masked if not self._show_passwords else entry.password[:16]
                totp = "🔐" if entry.has_totp else "  "
                lines.append(f"  {sel}{fav}{cat_icon} {entry.name:<22s} {entry.username:<24s} {totp}")
                lines.append(f"      [{entry.strength_bar}] {entry.strength_label}  {entry.breach_icon} Last: {entry.age_str}")

        elif self._view_mode == "generator":
            lines.append("  ── Password Generator ──")
            lines.append(f"  Length: {self._generator.length}  Upper: {'✓' if self._generator.uppercase else '✗'}  Lower: {'✓' if self._generator.lowercase else '✗'}  Digits: {'✓' if self._generator.digits else '✗'}  Symbols: {'✓' if self._generator.symbols else '✗'}")
            lines.append(f"  Exclude ambiguous: {'✓' if self._generator.exclude_ambiguous else '✗'}")
            lines.append("")
            if self._last_generated:
                lines.append(f"  Generated: {self._last_generated}")
                # Build strength
                s = PasswordGenerator(length=len(self._last_generated))
                lines.append(f"  Strength: [{s.strength_bar}] {s.strength_pct}%")
            lines.append("")
            # Quick generate 3 passwords
            for _ in range(3):
                pwd = self.generate_password()
                lines.append(f"  🎲 {pwd}")

        elif self._view_mode == "audit":
            lines.append("  ── Security Audit ──")
            # Strength distribution
            strengths = {"Strong": 0, "Good": 0, "Fair": 0, "Weak": 0, "Very Weak": 0}
            for e in self._entries:
                s = e.strength
                if s >= 80:
                    strengths["Strong"] += 1
                elif s >= 60:
                    strengths["Good"] += 1
                elif s >= 40:
                    strengths["Fair"] += 1
                elif s >= 20:
                    strengths["Weak"] += 1
                else:
                    strengths["Very Weak"] += 1
            for label, count in strengths.items():
                bar = "█" * (count * 2) + "░" * (20 - count * 2)
                lines.append(f"  {label:<12s} [{bar}] {count}")

            lines.append("")
            lines.append("  ── Weak Passwords ──")
            weak = [e for e in self._entries if e.strength < 40]
            for e in weak[:5]:
                lines.append(f"  🚨 {e.name:<22s} [{e.strength_bar}] {e.strength_label}")

        elif self._view_mode == "autofill":
            lines.append("  ── Auto-fill History ──")
            for af in self._autofill:
                lines.append(f"  🌐 {af.domain:<30s} {af.username:<24s} {af.time_str}")

        lines.append("")
        lines.append("  [V]ault [G]enerate [A]udit [F]ill [S]earch [↑↓]Nav [P]w toggle [N]ew")
        return lines


@dataclass
class PasswordEntry:
    id: int = 0
    name: str = ""
    username: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""
    category: str = ""
