from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import math
import hashlib
import random
import string


class PasswordType(Enum):
    RANDOM = "random"
    PASSPHRASE = "passphrase"
    PIN = "pin"
    MEMORABLE = "memorable"
    CUSTOM = "custom"


class CharPool(Enum):
    LOWERCASE = "lowercase"
    UPPERCASE = "uppercase"
    DIGITS = "digits"
    SYMBOLS = "symbols"
    ALL = "all"
    AMBIGUOUS = "ambiguous-free"
    HEX = "hex"
    URL_SAFE = "url-safe"


class StrengthLevel(Enum):
    VERY_WEAK = "very-weak"
    WEAK = "weak"
    FAIR = "fair"
    STRONG = "strong"
    VERY_STRONG = "very-strong"
    EXCELLENT = "excellent"


class StorageLocation(Enum):
    LOCAL = "local"
    KEYRING = "keyring"
    ENCRYPTED_FILE = "encrypted-file"


@dataclass
class PasswordEntry:
    name: str
    username: str
    password: str
    url: str = ""
    notes: str = ""
    category: str = "General"
    strength: StrengthLevel = StrengthLevel.FAIR
    entropy: float = 0.0
    created_at: float = 0.0
    last_used: float = 0.0
    last_modified: float = 0.0
    expiry_days: int = 90
    is_favorite: bool = False
    is_expired: bool = False
    storage: StorageLocation = StorageLocation.LOCAL
    tags: list = field(default_factory=list)
    use_count: int = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.last_modified:
            self.last_modified = time.time()

    @property
    def age_days(self) -> int:
        return int((time.time() - self.last_modified) / 86400)

    @property
    def masked_password(self) -> str:
        return "•" * len(self.password)

    @property
    def strength_bar(self) -> str:
        levels = {
            StrengthLevel.VERY_WEAK: "▓░░░░░░░░░",
            StrengthLevel.WEAK: "▓▓▓░░░░░░░",
            StrengthLevel.FAIR: "▓▓▓▓▓░░░░░",
            StrengthLevel.STRONG: "▓▓▓▓▓▓▓░░░",
            StrengthLevel.VERY_STRONG: "▓▓▓▓▓▓▓▓▓░",
            StrengthLevel.EXCELLENT: "▓▓▓▓▓▓▓▓▓▓",
        }
        return levels.get(self.strength, "░░░░░░░░░░")


@dataclass
class PassphraseWord:
    word: str
    index: int = 0


class PasswordGenerator:
    def __init__(self):
        self._entries: list[PasswordEntry] = []
        self._selected: int = 0
        self._password_type: PasswordType = PasswordType.RANDOM
        self._length: int = 20
        self._use_uppercase: bool = True
        self._use_lowercase: bool = True
        self._use_digits: bool = True
        self._use_symbols: bool = True
        self._exclude_ambiguous: bool = False
        self._separator: str = "-"
        self._word_count: int = 4
        self._pin_length: int = 6
        self._generated_password: str = ""
        self._generated_entropy: float = 0
        self._generated_strength: StrengthLevel = StrengthLevel.FAIR
        self._show_password: bool = False
        self._view: str = "vault"
        self._categories: dict[str, int] = {"Login": 0, "Banking": 0, "Social": 0, "Work": 0, "Shopping": 0, "General": 0}
        self._wordlists: list[str] = [
            "algorithm", "bramble", "cascade", "delight", "ember", "falcon", "glacier", "harbor",
            "ignite", "jungle", "kindle", "lantern", "meadow", "nebula", "oracle", "prism",
            "quartz", "ripple", "sapphire", "thunder", "umbrella", "valley", "whisper", "zenith",
            "anchor", "breeze", "crystal", "dolphin", "eclipse", "forest", "garden", "horizon",
            "ivory", "jasper", "kelp", "linden", "maple", "nectar", "opal", "pebble",
        ]
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        samples = [
            PasswordEntry("GitHub", "nyrqis-dev", "gh_abc123def456", "https://github.com", "Main dev account", "Login", StrengthLevel.STRONG, 65.5, now - 86400 * 30, now - 3600, now - 86400 * 30, use_count=45, tags=["dev", "2fa"]),
            PasswordEntry("AWS Console", "admin@nyrqis.dev", "Aws!Pr0d_S3cur3_2026", "https://aws.amazon.com", "Production AWS", "Work", StrengthLevel.EXCELLENT, 85.2, now - 86400 * 60, now - 7200, now - 86400 * 60, use_count=12, tags=["cloud", "prod"]),
            PasswordEntry("Gmail", "user@nyrqis.com", "Gm@il_Str0ng_P@ss!", "https://mail.google.com", "Personal email", "Login", StrengthLevel.VERY_STRONG, 78.3, now - 86400 * 15, now - 1800, now - 86400 * 15, is_favorite=True, use_count=120, tags=["email", "personal"]),
            PasswordEntry("Chase Bank", "123456789", "B@nk!ng_S3cur3_P@ss", "https://chase.com", "Checking account", "Banking", StrengthLevel.STRONG, 72.1, now - 86400 * 90, now - 86400, now - 86400 * 90, expiry_days=60, is_expired=True, use_count=8),
            PasswordEntry("Twitter/X", "@nyrqis_dev", "Tw!tt3r_Fun_2026", "https://x.com", "", "Social", StrengthLevel.FAIR, 48.7, now - 86400 * 10, now - 43200, now - 86400 * 10, use_count=34),
            PasswordEntry("Netflix", "movies@nyrqis.com", "N3tfl1x_Ch1ll_!2026", "https://netflix.com", "Family plan", "General", StrengthLevel.STRONG, 68.9, now - 86400 * 45, now - 86400 * 3, now - 86400 * 45, use_count=200, tags=["entertainment"]),
            PasswordEntry("Nyrqis Jenkins", "ci-bot", "Jnk!ns_C1_R0b0t_2026", "https://ci.nyrqis.dev", "CI/CD bot", "Work", StrengthLevel.EXCELLENT, 92.1, now - 86400 * 120, now - 600, now - 86400 * 120, use_count=500, tags=["ci", "automation"]),
            PasswordEntry("Home WiFi", "admin", "H0me_W1f1_S3cur3!", "192.168.1.1", "Router admin", "General", StrengthLevel.STRONG, 70.5, now - 86400 * 180, now - 86400 * 30, now - 86400 * 180, expiry_days=365, use_count=5, tags=["network"]),
        ]
        self._entries = samples
        for e in samples:
            self._categories[e.category] = self._categories.get(e.category, 0) + 1

    @property
    def selected_entry(self) -> Optional[PasswordEntry]:
        if 0 <= self._selected < len(self._entries):
            return self._entries[self._selected]
        return None

    @property
    def total_entries(self) -> int:
        return len(self._entries)

    @property
    def expired_count(self) -> int:
        return sum(1 for e in self._entries if e.is_expired)

    @property
    def favorite_count(self) -> int:
        return sum(1 for e in self._entries if e.is_favorite)

    @property
    def avg_strength(self) -> float:
        if not self._entries:
            return 0
        return sum(e.entropy for e in self._entries) / len(self._entries)

    @property
    def strength_distribution(self) -> dict:
        counts = {}
        for e in self._entries:
            counts[e.strength.value] = counts.get(e.strength.value, 0) + 1
        return counts

    @staticmethod
    def calculate_entropy(password: str) -> float:
        """Calculate Shannon entropy of a password."""
        if not password:
            return 0
        charset_size = 0
        if any(c in string.ascii_lowercase for c in password):
            charset_size += 26
        if any(c in string.ascii_uppercase for c in password):
            charset_size += 26
        if any(c in string.digits for c in password):
            charset_size += 10
        if any(c in string.punctuation for c in password):
            charset_size += 32
        if charset_size == 0:
            charset_size = 256
        return len(password) * math.log2(charset_size)

    @staticmethod
    def calculate_strength(entropy: float) -> StrengthLevel:
        if entropy < 28:
            return StrengthLevel.VERY_WEAK
        elif entropy < 36:
            return StrengthLevel.WEAK
        elif entropy < 60:
            return StrengthLevel.FAIR
        elif entropy < 80:
            return StrengthLevel.STRONG
        elif entropy < 128:
            return StrengthLevel.VERY_STRONG
        return StrengthLevel.EXCELLENT

    def generate(self) -> str:
        rng = random.Random()
        if self._password_type == PasswordType.RANDOM:
            chars = ""
            if self._use_lowercase:
                chars += string.ascii_lowercase
            if self._use_uppercase:
                chars += string.ascii_uppercase
            if self._use_digits:
                chars += string.digits
            if self._use_symbols:
                chars += "!@#$%^&*()_+-=[]{}|;:,.<>?"
            if self._exclude_ambiguous:
                chars = chars.replace("l", "").replace("1", "").replace("I", "").replace("O", "").replace("0", "")
            if not chars:
                chars = string.ascii_letters + string.digits
            self._generated_password = "".join(rng.choice(chars) for _ in range(self._length))
        elif self._password_type == PasswordType.PASSPHRASE:
            words = rng.sample(self._wordlists, self._word_count)
            self._generated_password = self._separator.join(words)
        elif self._password_type == PasswordType.PIN:
            self._generated_password = "".join(rng.choice(string.digits) for _ in range(self._pin_length))
        elif self._password_type == PasswordType.MEMORABLE:
            consonants = "bcdfghjklmnpqrstvwxyz"
            vowels = "aeiou"
            parts = []
            for _ in range(self._length // 4):
                parts.append(rng.choice(consonants) + rng.choice(vowels) + rng.choice(consonants) + rng.choice(vowels))
            self._generated_password = "".join(parts)[:self._length]
        else:
            self._generated_password = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(self._length))

        self._generated_entropy = self.calculate_entropy(self._generated_password)
        self._generated_strength = self.calculate_strength(self._generated_entropy)
        return self._generated_password

    def select(self, idx: int):
        if 0 <= idx < len(self._entries):
            self._selected = idx

    def add_entry(self, entry: PasswordEntry):
        self._entries.append(entry)
        self._selected = len(self._entries) - 1

    def delete_entry(self, idx: int) -> bool:
        if 0 <= idx < len(self._entries):
            self._entries.pop(idx)
            if self._selected >= len(self._entries):
                self._selected = max(0, len(self._entries) - 1)
            return True
        return False

    def toggle_favorite(self):
        e = self.selected_entry
        if e:
            e.is_favorite = not e.is_favorite

    def search(self, query: str) -> list:
        return [e for e in self._entries if query.lower() in e.name.lower() or query.lower() in e.username.lower() or query.lower() in e.url.lower()]

    def render(self, width: int = 80, height: int = 20) -> list:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                   NYRQIS PASSWORD GENERATOR & VAULT                        ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"  Vault: {self.total_entries} entries  ⭐ {self.favorite_count}  ⏰ {self.expired_count} expired  Avg entropy: {self.avg_strength:.0f} bits")
        lines.append("")
        for i, e in enumerate(self._entries):
            sel = "▶" if i == self._selected else " "
            fav = "⭐" if e.is_favorite else " "
            exp = "⏰" if e.is_expired else " "
            lines.append(f"  {sel}{fav}{exp} {e.name}  [{e.strength_bar}]  {e.entropy:.0f} bits")
            lines.append(f"    {e.masked_password}  👤 {e.username}  🔗 {e.url}")
        lines.append("")
        lines.append("  ── Categories ──")
        for cat, count in self._categories.items():
            if count > 0:
                lines.append(f"  📁 {cat}: {count}")
        lines.append("")
        lines.append("  [G]enerate  [A]dd  [D]elete  [F]avorite  [S]earch  [R]eveal")
        return lines

    def render_generator(self) -> list:
        lines = []
        lines.append("  ── Password Generator ──")
        lines.append(f"  Type: {self._password_type.value}  Length: {self._length}  Words: {self._word_count}")
        lines.append(f"  Upper: {'✓' if self._use_uppercase else '✗'}  Lower: {'✓' if self._use_lowercase else '✗'}  Digits: {'✓' if self._use_digits else '✗'}  Symbols: {'✓' if self._use_symbols else '✗'}")
        lines.append("")
        if self._generated_password:
            display = self._generated_password if self._show_password else self.masked_generated
            lines.append(f"  Generated: {display}")
            lines.append(f"  Entropy: {self._generated_entropy:.1f} bits  Strength: {self._generated_strength.value}")
            lines.append(f"  {self._generated_strength_bar}")
        lines.append("")
        lines.append("  [T]ype  [L]ength  [+/-]adjust  [G]enerate  [C]opy  [S]ave")
        return lines

    @property
    def masked_generated(self) -> str:
        return "•" * len(self._generated_password)

    @property
    def _generated_strength_bar(self) -> str:
        levels = {
            StrengthLevel.VERY_WEAK: "▓░░░░░░░░░ Very Weak",
            StrengthLevel.WEAK: "▓▓▓░░░░░░░ Weak",
            StrengthLevel.FAIR: "▓▓▓▓▓░░░░░ Fair",
            StrengthLevel.STRONG: "▓▓▓▓▓▓▓░░░ Strong",
            StrengthLevel.VERY_STRONG: "▓▓▓▓▓▓▓▓▓░ Very Strong",
            StrengthLevel.EXCELLENT: "▓▓▓▓▓▓▓▓▓▓ Excellent",
        }
        return levels.get(self._generated_strength, "?")

    def render_entry_detail(self) -> list:
        e = self.selected_entry
        if not e:
            return ["  No entry selected"]
        lines = []
        lines.append(f"  ── {e.name} ──")
        lines.append(f"  Username: {e.username}")
        lines.append(f"  Password: {e.masked_password}")
        lines.append(f"  URL: {e.url}")
        lines.append(f"  Notes: {e.notes or 'None'}")
        lines.append(f"  Category: {e.category}")
        lines.append(f"  Strength: {e.strength_bar} {e.strength.value} ({e.entropy:.0f} bits)")
        lines.append(f"  Created: {time.strftime('%Y-%m-%d', time.localtime(e.created_at))}  Age: {e.age_days}d")
        lines.append(f"  Last Used: {time.strftime('%Y-%m-%d %H:%M', time.localtime(e.last_used))}")
        lines.append(f"  Uses: {e.use_count}  Storage: {e.storage.value}")
        if e.tags:
            lines.append(f"  Tags: {', '.join(e.tags)}")
        if e.is_expired:
            lines.append(f"  ⚠️ PASSWORD EXPIRED ({e.age_days}d > {e.expiry_days}d)")
        return lines
