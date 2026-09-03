"""Regex Tester — Pattern matching, replace, and cheatsheet.

Features:
- Real-time regex matching with highlighting
- Match groups and named captures
- Replace with backreferences
- Regex flags (g, i, m, s, u)
- Pattern library with common regexes
- Cheatsheet with all regex tokens
- Test history
- Performance timing
"""

from __future__ import annotations

import re
import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class RegexFlag(Enum):
    GLOBAL = "g"
    IGNORE_CASE = "i"
    MULTILINE = "m"
    DOTALL = "s"
    UNICODE = "u"
    VERBOSE = "x"

    @property
    def label(self) -> str:
        labels = {
            "g": "Global (all matches)", "i": "Case Insensitive",
            "m": "Multiline", "s": "DotAll (dot matches newline)",
            "u": "Unicode", "x": "Verbose (ignore whitespace)",
        }
        return labels.get(self.value, "")


@dataclass
class RegexMatch:
    match_text: str = ""
    start: int = 0
    end: int = 0
    groups: List[str] = field(default_factory=list)
    named_groups: Dict[str, str] = field(default_factory=dict)
    group_count: int = 0

    @property
    def span_str(self) -> str:
        return f"[{self.start}:{self.end}]"

    @property
    def groups_str(self) -> str:
        if not self.groups:
            return ""
        return f" Groups: ({', '.join(self.groups)})"


@dataclass
class RegexTest:
    pattern: str = ""
    flags: List[str] = field(default_factory=list)
    test_string: str = ""
    matches: List[RegexMatch] = field(default_factory=list)
    replace_pattern: str = ""
    replace_result: str = ""
    timestamp: float = 0.0
    execution_time_us: float = 0.0
    valid: bool = True
    error: str = ""
    name: str = ""

    @property
    def match_count(self) -> int:
        return len(self.matches)

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def flags_str(self) -> str:
        return "".join(self.flags) if self.flags else "none"


@dataclass
class RegexPattern:
    name: str = ""
    pattern: str = ""
    description: str = ""
    category: str = ""
    example: str = ""
    flags: List[str] = field(default_factory=list)

    @property
    def preview(self) -> str:
        return f"/{self.pattern[:40]}/{''.join(self.flags)}"


@dataclass
class RegexCheatsheetEntry:
    token: str = ""
    description: str = ""
    example: str = ""
    category: str = ""


class RegexTester:
    def __init__(self):
        self._history: List[RegexTest] = []
        self._patterns: List[RegexPattern] = []
        self._cheatsheet: List[RegexCheatsheetEntry] = []
        self._current_pattern: str = ""
        self._current_flags: List[str] = ["g"]
        self._current_test: str = ""
        self._current_replace: str = ""
        self._selected_history: int = 0
        self._view_mode: str = "test"  # test, history, library, cheatsheet, replace
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # History
        self._history = [
            RegexTest(r"(\w+)@(\w+)\.(\w+)", ["g"], "contact us at buffy@nyrqis.dev or nyx@nyrqis.dev",
                      [RegexMatch("buffy@nyrqis.dev", 13, 28, ["buffy", "nyrqis", "dev"]),
                       RegexMatch("nyx@nyrqis.dev", 33, 48, ["nyx", "nyrqis", "dev"])],
                      name="Email extractor", timestamp=now - 100, execution_time_us=12.5),
            RegexTest(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", ["g"],
                      "Server at 192.168.1.100 and backup 10.0.0.50",
                      [RegexMatch("192.168.1.100", 10, 23),
                       RegexMatch("10.0.0.50", 36, 45)],
                      name="IP address finder", timestamp=now - 200, execution_time_us=8.3),
            RegexTest(r"https?://[\w./\-]+", ["g", "i"],
                      "Visit https://nyrqis.dev or http://github.com/Myco-mycelium",
                      [RegexMatch("https://nyrqis.dev", 6, 24),
                       RegexMatch("http://github.com/Myco-mycelium", 29, 62)],
                      name="URL matcher", timestamp=now - 300, execution_time_us=9.1),
            RegexTest(r"#[0-9a-fA-F]{6}\b", ["g"],
                      "Colors: #FF0000, #00FF00, #0000FF, #333333",
                      [RegexMatch("#FF0000", 9, 16),
                       RegexMatch("#00FF00", 18, 25),
                       RegexMatch("#0000FF", 27, 34),
                       RegexMatch("#333333", 36, 43)],
                      name="Hex color finder", timestamp=now - 400, execution_time_us=7.2),
            RegexTest(r"^\d{4}-\d{2}-\d{2}$", ["m"],
                      "2026-09-03\ninvalid-date\n2026-12-31\nnot-a-date",
                      [RegexMatch("2026-09-03", 0, 10),
                       RegexMatch("2026-12-31", 24, 34)],
                      name="Date validator", timestamp=now - 500, execution_time_us=6.8),
        ]

        # Patterns library
        self._patterns = [
            RegexPattern("Email", r"[\w.-]+@[\w.-]+\.\w+", "Match email addresses", "common", "user@example.com"),
            RegexPattern("URL", r"https?://[\w./\-&?=%]+", "Match HTTP/HTTPS URLs", "common", "https://example.com"),
            RegexPattern("IPv4", r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "Match IPv4 addresses", "network", "192.168.1.1"),
            RegexPattern("IPv6", r"([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}", "Match IPv6 addresses", "network", "2001:db8::1"),
            RegexPattern("Phone (US)", r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", "Match US phone numbers", "common", "(555) 123-4567"),
            RegexPattern("Date (ISO)", r"\d{4}-\d{2}-\d{2}", "Match ISO date format", "common", "2026-09-03"),
            RegexPattern("Time (24h)", r"\d{2}:\d{2}(:\d{2})?", "Match 24-hour time", "common", "14:30:00"),
            RegexPattern("Hex Color", r"#[0-9a-fA-F]{6}\b", "Match hex color codes", "design", "#FF0000"),
            RegexPattern("MAC Address", r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", "Match MAC addresses", "network", "AA:BB:CC:DD:EE:FF"),
            RegexPattern("HTML Tag", r"<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>.*?</\1>", "Match HTML tags", "web", "<div>content</div>"),
            RegexPattern("Username", r"^[a-zA-Z0-9_-]{3,16}$", "Match valid usernames", "validation", "buffy_dev"),
            RegexPattern("Password (strong)", r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$", "Strong password pattern", "security", "MyP@ssw0rd!"),
            RegexPattern("File Path (Unix)", r"/[\w./-]+", "Match Unix file paths", "system", "/home/user/file.txt"),
            RegexPattern("Semantic Version", r"\d+\.\d+\.\d+(-\w+)?", "Match semver format", "development", "v2.1.0-rc1"),
            RegexPattern("JWT", r"eyJ[\w-]+\.eyJ[\w-]+\.[\w-]+", "Match JSON Web Tokens", "security", "eyJhbGc..."),
        ]

        # Cheatsheet
        self._cheatsheet = [
            RegexCheatsheetEntry(".", "Any character", "a.b matches acb", "Character"),
            RegexCheatsheetEntry("\\d", "Digit [0-9]", "\\d+ matches 123", "Character"),
            RegexCheatsheetEntry("\\w", "Word char [a-zA-Z0-9_]", "\\w+ matches hello", "Character"),
            RegexCheatsheetEntry("\\s", "Whitespace", "\\s+ matches spaces", "Character"),
            RegexCheatsheetEntry("\\b", "Word boundary", "\\bcat\\b matches 'cat'", "Anchor"),
            RegexCheatsheetEntry("^", "Start of string", "^Hello matches 'Hello...'", "Anchor"),
            RegexCheatsheetEntry("$", "End of string", "world$ matches '...world'", "Anchor"),
            RegexCheatsheetEntry("*", "0 or more", "ab*c matches ac, abc, abbc", "Quantifier"),
            RegexCheatsheetEntry("+", "1 or more", "ab+c matches abc, abbc", "Quantifier"),
            RegexCheatsheetEntry("?", "0 or 1", "colou?r matches color, colour", "Quantifier"),
            RegexCheatsheetEntry("{n}", "Exactly n", "a{3} matches aaa", "Quantifier"),
            RegexCheatsheetEntry("{n,m}", "n to m", "a{2,4} matches aa, aaa, aaaa", "Quantifier"),
            RegexCheatsheetEntry("[abc]", "Character set", "[aeiou] matches vowels", "Group"),
            RegexCheatsheetEntry("[^abc]", "Negated set", "[^0-9] matches non-digits", "Group"),
            RegexCheatsheetEntry("(abc)", "Capture group", "(\\w+)@\\w+ captures username", "Group"),
            RegexCheatsheetEntry("(?:abc)", "Non-capturing group", "(?:foo|bar) matches foo or bar", "Group"),
            RegexCheatsheetEntry("(?=abc)", "Lookahead", "a(?=b) matches 'a' in 'ab'", "Assertion"),
            RegexCheatsheetEntry("(?!abc)", "Neg. Lookahead", "a(?!b) matches 'a' not before 'b'", "Assertion"),
            RegexCheatsheetEntry("a|b", "Alternation", "cat|dog matches cat or dog", "Group"),
        ]

    @property
    def active_flags(self) -> str:
        return "".join(self._current_flags)

    @property
    def flag_status(self) -> str:
        statuses = []
        for f in RegexFlag:
            active = "✓" if f.value in self._current_flags else "✗"
            statuses.append(f"{f.value}:{active}")
        return "  ".join(statuses)

    def toggle_flag(self, flag: str):
        if flag in self._current_flags:
            self._current_flags.remove(flag)
        else:
            self._current_flags.append(flag)

    def select_history(self, idx: int):
        if 0 <= idx < len(self._history):
            self._selected_history = idx

    def set_view(self, mode: str):
        if mode in ("test", "history", "library", "cheatsheet", "replace"):
            self._view_mode = mode

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS REGEX TESTER                                     ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  🔤 Pattern: /{self._current_pattern or '...'}/{self.active_flags}  📜 {len(self._history)} tests  📚 {len(self._patterns)} patterns  📖 {len(self._cheatsheet)} cheats")
        lines.append(f"  Flags: {self.flag_status}")
        lines.append("")

        if self._view_mode == "test":
            lines.append("  ── Pattern ──")
            lines.append(f"  /{self._current_pattern or '...'}/{self.active_flags}")
            lines.append("")
            lines.append("  ── Test String ──")
            lines.append(f"  {self._current_test or 'Enter test string...'}")
            lines.append("")
            lines.append("  ── Replace Pattern ──")
            lines.append(f"  {self._current_replace or 'Enter replacement...'}")

        elif self._view_mode == "history":
            lines.append("  ── Test History ──")
            for i, test in enumerate(self._history):
                sel = "▶" if i == self._selected_history else " "
                valid = "✅" if test.valid else "❌"
                lines.append(f"  {sel}{valid} /{test.pattern[:30]}/{test.flags_str}  {test.match_count} matches  {test.execution_time_us:.1f}μs")
                lines.append(f"      {test.name}")

        elif self._view_mode == "library":
            lines.append("  ── Pattern Library ──")
            for p in self._patterns:
                lines.append(f"  📚 {p.name:<18s} [{p.category}]")
                lines.append(f"      {p.preview}")
                lines.append(f"      {p.description}  e.g. {p.example}")

        elif self._view_mode == "cheatsheet":
            lines.append("  ── Regex Cheatsheet ──")
            current_cat = ""
            for entry in self._cheatsheet:
                if entry.category != current_cat:
                    current_cat = entry.category
                    lines.append(f"  ── {current_cat} ──")
                lines.append(f"  {entry.token:<16s} {entry.description:<35s} {entry.example}")

        elif self._view_mode == "replace":
            lines.append("  ── Find & Replace ──")
            lines.append(f"  Find:    /{self._current_pattern}/{self.active_flags}")
            lines.append(f"  Replace: {self._current_replace}")
            lines.append(f"  Input:   {self._current_test}")
            lines.append("")

        lines.append("")
        lines.append("  [T]est [H]istory [L]ibrary [C]heatsheet [R]eplace [F]lags [↑↓]Nav")
        return lines
