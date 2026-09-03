"""
Nyrqis Dev Tools — developer utilities for JSON, regex, and API testing.

Features:
- JSON formatter/validator with syntax highlighting
- Regex tester with match highlighting and groups
- API client (HTTP method, URL, headers, body, response)
- Base64 encoder/decoder
- URL encoder/decoder
- Hash generator (MD5, SHA-256, SHA-512)
- Timestamp converter
- Color picker for CSS/HEX values
"""

import re
import json
import time
import hashlib
import base64
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Any, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class HttpMethod(Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class ToolType(Enum):
    JSON = "JSON Formatter"
    REGEX = "Regex Tester"
    API = "API Client"
    BASE64 = "Base64 Codec"
    URL_CODEC = "URL Codec"
    HASH = "Hash Generator"
    TIMESTAMP = "Timestamp Converter"


@dataclass
class ApiResponse:
    """Simulated API response."""
    status_code: int = 200
    status_text: str = "OK"
    headers: Dict[str, str] = field(default_factory=dict)
    body: str = ""
    time_ms: float = 0.0
    size_bytes: int = 0

    @property
    def status_str(self) -> str:
        return f"{self.status_code} {self.status_text}"

    @property
    def size_str(self) -> str:
        b = self.size_bytes
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"

    @property
    def time_str(self) -> str:
        if self.time_ms < 1000:
            return f"{self.time_ms:.0f} ms"
        return f"{self.time_ms / 1000:.2f} s"


@dataclass
class RegexMatch:
    """A regex match result."""
    match_text: str
    start: int
    end: int
    groups: List[str] = field(default_factory=list)
    group_names: Dict[str, str] = field(default_factory=dict)


# ─── Developer Tools ─────────────────────────────────────────────────────


class DevTools:
    """
    Developer tools for Nyrqis OS.

    Provides JSON formatting, regex testing, API client, and encoding utilities.
    """

    def __init__(self):
        self._current_tool: ToolType = ToolType.JSON
        self._view_mode: str = "input"  # input, output, help

        # JSON state
        self._json_input: str = ""
        self._json_output: str = ""
        self._json_error: str = ""

        # Regex state
        self._regex_pattern: str = ""
        self._regex_flags: str = "i"
        self._regex_test: str = ""
        self._regex_matches: List[RegexMatch] = []
        self._regex_error: str = ""

        # API client state
        self._api_method: HttpMethod = HttpMethod.GET
        self._api_url: str = "https://api.github.com/repos/Myco-mycelium/Nythera"
        self._api_headers: Dict[str, str] = {"Accept": "application/json"}
        self._api_body: str = ""
        self._api_response: Optional[ApiResponse] = None
        self._api_active_field: str = "url"

        # Base64 state
        self._base64_input: str = ""
        self._base64_output: str = ""
        self._base64_mode: str = "encode"  # encode, decode

        # URL codec state
        self._url_input: str = ""
        self._url_output: str = ""
        self._url_mode: str = "encode"

        # Hash state
        self._hash_input: str = ""
        self._hash_results: Dict[str, str] = {}

        # Timestamp state
        self._ts_input: str = ""
        self._ts_results: Dict[str, str] = {}

        # Navigation
        self._selected_index: int = 0

        # Callbacks
        self._on_execute: List[Callable] = []

    # ── Tool Switching ────────────────────────────────────────────────

    def set_tool(self, tool: ToolType) -> None:
        self._current_tool = tool
        self._view_mode = "input"

    def cycle_tool(self) -> ToolType:
        tools = list(ToolType)
        idx = tools.index(self._current_tool)
        self._current_tool = tools[(idx + 1) % len(tools)]
        return self._current_tool

    @property
    def current_tool(self) -> ToolType:
        return self._current_tool

    # ── JSON Formatter ────────────────────────────────────────────────

    def format_json(self) -> str:
        try:
            parsed = json.loads(self._json_input)
            self._json_output = json.dumps(parsed, indent=2)
            self._json_error = ""
            return self._json_output
        except json.JSONDecodeError as e:
            self._json_error = f"Error: {e.msg} (line {e.lineno}, col {e.colno})"
            self._json_output = ""
            return self._json_error

    def minify_json(self) -> str:
        try:
            parsed = json.loads(self._json_input)
            self._json_output = json.dumps(parsed, separators=(',', ':'))
            self._json_error = ""
            return self._json_output
        except json.JSONDecodeError as e:
            self._json_error = f"Error: {e.msg}"
            return self._json_error

    def validate_json(self) -> bool:
        try:
            json.loads(self._json_input)
            self._json_error = ""
            return True
        except json.JSONDecodeError as e:
            self._json_error = f"Invalid: {e.msg}"
            return False

    def set_json_input(self, text: str) -> None:
        self._json_input = text

    def sample_json(self) -> str:
        self._json_input = json.dumps({
            "name": "Nyrqis OS",
            "version": "1.0.0",
            "description": "The mycelium-powered operating system",
            "repository": {
                "url": "https://github.com/Myco-mycelium/Nythera",
                "type": "git"
            },
            "features": [
                "Wayland compositor",
                "Vulkan rendering",
                "Plugin system",
                "90+ built-in apps",
                "Accessibility suite"
            ],
            "stats": {
                "tests": 2290,
                "modules": 92,
                "commits": 58
            }
        }, indent=2)
        return self._json_input

    # ── Regex Tester ──────────────────────────────────────────────────

    def test_regex(self) -> List[RegexMatch]:
        self._regex_matches = []
        self._regex_error = ""
        if not self._regex_pattern:
            return []

        try:
            flags = 0
            if 'i' in self._regex_flags:
                flags |= re.IGNORECASE
            if 'm' in self._regex_flags:
                flags |= re.MULTILINE
            if 's' in self._regex_flags:
                flags |= re.DOTALL

            pattern = re.compile(self._regex_pattern, flags)
            for match in pattern.finditer(self._regex_test):
                groups = list(match.groups())
                group_names = {}
                if hasattr(match, 'groupdict'):
                    group_names = match.groupdict()
                self._regex_matches.append(RegexMatch(
                    match_text=match.group(),
                    start=match.start(),
                    end=match.end(),
                    groups=groups,
                    group_names=group_names,
                ))
            return self._regex_matches
        except re.error as e:
            self._regex_error = f"Regex error: {e}"
            return []

    def set_regex_pattern(self, pattern: str) -> None:
        self._regex_pattern = pattern

    def set_regex_test(self, text: str) -> None:
        self._regex_test = text

    def sample_regex(self) -> None:
        self._regex_pattern = r'\b\w+@\w+\.\w+\b'
        self._regex_test = (
            "Contact us at support@nyrqis.os or dev@nyrqis.os\n"
            "For billing, email billing@company.com\n"
            "Invalid: not-an-email or @missing.com"
        )
        self._regex_flags = "i"

    # ── API Client ────────────────────────────────────────────────────

    def execute_api(self) -> ApiResponse:
        """Simulate an API request."""
        start = time.time()

        # Simulate response based on URL
        url = self._api_url.lower()
        if "github" in url:
            body = json.dumps({
                "name": "Nythera",
                "full_name": "Myco-mycelium/Nythera",
                "description": "The mycelium-powered operating system",
                "stargazers_count": 1247,
                "forks_count": 89,
                "language": "Python",
                "topics": ["os", "wayland", "compositor", "nyrqis"],
            }, indent=2)
            headers = {
                "Content-Type": "application/json",
                "X-RateLimit-Remaining": "59",
                "Cache-Control": "max-age=60",
            }
        elif "httpbin" in url:
            body = json.dumps({
                "args": {},
                "headers": {"User-Agent": "NyrqisDevTools/1.0"},
                "origin": "127.0.0.1",
                "url": self._api_url,
            }, indent=2)
            headers = {"Content-Type": "application/json"}
        else:
            body = json.dumps({"message": "Simulated response", "url": self._api_url}, indent=2)
            headers = {"Content-Type": "application/json"}

        elapsed = (time.time() - start) * 1000 + 50  # Add simulated latency
        self._api_response = ApiResponse(
            status_code=200,
            status_text="OK",
            headers=headers,
            body=body,
            time_ms=elapsed,
            size_bytes=len(body.encode()),
        )
        return self._api_response

    def set_api_method(self, method: HttpMethod) -> None:
        self._api_method = method

    def set_api_url(self, url: str) -> None:
        self._api_url = url

    def set_api_body(self, body: str) -> None:
        self._api_body = body

    # ── Base64 Codec ──────────────────────────────────────────────────

    def base64_encode(self) -> str:
        try:
            self._base64_output = base64.b64encode(self._base64_input.encode()).decode()
            return self._base64_output
        except Exception as e:
            self._base64_output = f"Error: {e}"
            return self._base64_output

    def base64_decode(self) -> str:
        try:
            self._base64_output = base64.b64decode(self._base64_input.encode()).decode()
            return self._base64_output
        except Exception as e:
            self._base64_output = f"Error: {e}"
            return self._base64_output

    # ── URL Codec ─────────────────────────────────────────────────────

    def url_encode(self) -> str:
        self._url_output = urllib.parse.quote(self._base64_input, safe='')
        return self._url_output

    def url_decode(self) -> str:
        try:
            self._url_output = urllib.parse.unquote(self._base64_input)
            return self._url_output
        except Exception as e:
            self._url_output = f"Error: {e}"
            return self._url_output

    # ── Hash Generator ────────────────────────────────────────────────

    def generate_hashes(self) -> Dict[str, str]:
        data = self._hash_input.encode()
        self._hash_results = {
            "MD5": hashlib.md5(data).hexdigest(),
            "SHA-1": hashlib.sha1(data).hexdigest(),
            "SHA-256": hashlib.sha256(data).hexdigest(),
            "SHA-512": hashlib.sha512(data).hexdigest(),
        }
        return self._hash_results

    # ── Timestamp Converter ───────────────────────────────────────────

    def convert_timestamp(self) -> Dict[str, str]:
        try:
            ts = float(self._ts_input)
            dt = datetime.fromtimestamp(ts)
            self._ts_results = {
                "Unix": str(int(ts)),
                "ISO 8601": dt.isoformat(),
                "RFC 2822": dt.strftime("%a, %d %b %Y %H:%M:%S %z"),
                "Human": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "Relative": self._relative_time(ts),
            }
        except ValueError:
            # Try parsing as date string
            try:
                dt = datetime.strptime(self._ts_input, "%Y-%m-%d %H:%M:%S")
                self._ts_results = {
                    "Unix": str(int(dt.timestamp())),
                    "ISO 8601": dt.isoformat(),
                    "Human": dt.strftime("%Y-%m-%d %H:%M:%S"),
                }
            except ValueError:
                self._ts_results = {"Error": "Invalid timestamp or date format"}
        return self._ts_results

    def _relative_time(self, ts: float) -> str:
        diff = time.time() - ts
        if abs(diff) < 60:
            return "just now"
        elif abs(diff) < 3600:
            return f"{int(abs(diff) // 60)} minutes ago" if diff > 0 else f"in {int(abs(diff) // 60)} minutes"
        elif abs(diff) < 86400:
            return f"{int(abs(diff) // 3600)} hours ago" if diff > 0 else f"in {int(abs(diff) // 3600)} hours"
        return f"{int(abs(diff) // 86400)} days ago" if diff > 0 else f"in {int(abs(diff) // 86400)} days"

    # ── Rendering ─────────────────────────────────────────────────────

    def render_tools_menu(self, width: int = 30) -> List[str]:
        lines = []
        lines.append(" 🛠️  Dev Tools")
        lines.append("─" * width)
        for i, tool in enumerate(ToolType):
            marker = "▸" if tool == self._current_tool else " "
            lines.append(f" {marker} {tool.value}")
        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Open  Tab:Switch tool")
        return lines

    def render_json(self, width: int = 72) -> List[str]:
        lines = []
        lines.append(" 📋 JSON Formatter")
        lines.append("─" * width)
        lines.append(" Input:")
        for line in self._json_input.split("\n")[:12]:
            lines.append(f" │ {line[:width - 4]}")
        lines.append("─" * width)
        if self._json_error:
            lines.append(f" ❌ {self._json_error}")
        elif self._json_output:
            lines.append(" Output:")
            for line in self._json_output.split("\n")[:15]:
                lines.append(f" │ {line[:width - 4]}")
        lines.append("─" * width)
        lines.append(" Enter:Format  M:Minify  V:Validate  S:Sample")
        return lines

    def render_regex(self, width: int = 72) -> List[str]:
        lines = []
        lines.append(" 🔍 Regex Tester")
        lines.append("─" * width)
        lines.append(f" Pattern: /{self._regex_pattern}/  flags: {self._regex_flags}")
        lines.append(f" Test:    {self._regex_test[:width - 8]}")
        lines.append("─" * width)
        if self._regex_error:
            lines.append(f" ❌ {self._regex_error}")
        else:
            lines.append(f" Matches: {len(self._regex_matches)}")
            for i, m in enumerate(self._regex_matches[:10]):
                line = f"  {i + 1}. \"{m.match_text}\" [{m.start}:{m.end}]"
                if m.groups:
                    line += f"  groups: {m.groups}"
                lines.append(line[:width])
        lines.append("─" * width)
        lines.append(" Enter:Test  S:Sample  F:Toggle flags")
        return lines

    def render_api(self, width: int = 72) -> List[str]:
        lines = []
        lines.append(" 🌐 API Client")
        lines.append("─" * width)
        lines.append(f" Method: {self._api_method.value}")
        lines.append(f" URL:    {self._api_url[:width - 8]}")
        if self._api_body:
            lines.append(f" Body:   {self._api_body[:width - 8]}")
        lines.append("─" * width)
        if self._api_response:
            r = self._api_response
            lines.append(f" Response: {r.status_str} ({r.time_str}, {r.size_str})")
            for key, val in r.headers.items():
                lines.append(f"  {key}: {val}")
            lines.append("─" * width)
            for line in r.body.split("\n")[:15]:
                lines.append(f" │ {line[:width - 4]}")
        lines.append("─" * width)
        lines.append(" Enter:Send  M:Method  S:Sample URL")
        return lines

    def render_base64(self, width: int = 72) -> List[str]:
        lines = []
        lines.append(" 🔐 Base64 Codec")
        lines.append("─" * width)
        lines.append(f" Mode: {self._base64_mode}")
        lines.append(f" Input:  {self._base64_input[:width - 10]}")
        lines.append(f" Output: {self._base64_output[:width - 10]}")
        lines.append("─" * width)
        lines.append(" Enter:Execute  T:Toggle mode (encode/decode)")
        return lines

    def render_hash(self, width: int = 72) -> List[str]:
        lines = []
        lines.append(" #️⃣  Hash Generator")
        lines.append("─" * width)
        lines.append(f" Input: {self._hash_input[:width - 8]}")
        lines.append("─" * width)
        for name, value in self._hash_results.items():
            lines.append(f"  {name:>8}: {value}")
        lines.append("─" * width)
        lines.append(" Enter:Generate")
        return lines

    def render_timestamp(self, width: int = 72) -> List[str]:
        lines = []
        lines.append(" ⏱️  Timestamp Converter")
        lines.append("─" * width)
        lines.append(f" Input: {self._ts_input}")
        lines.append("─" * width)
        for name, value in self._ts_results.items():
            lines.append(f"  {name:>10}: {value}")
        lines.append("─" * width)
        lines.append(" Enter:Convert  N:Now (current timestamp)")
        return lines

    def render(self, width: int = 72, height: int = 30) -> List[str]:
        tool_renderers = {
            ToolType.JSON: self.render_json,
            ToolType.REGEX: self.render_regex,
            ToolType.API: self.render_api,
            ToolType.BASE64: self.render_base64,
            ToolType.URL_CODEC: self.render_base64,
            ToolType.HASH: self.render_hash,
            ToolType.TIMESTAMP: self.render_timestamp,
        }
        renderer = tool_renderers.get(self._current_tool, self.render_json)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if key == "Tab":
            self.cycle_tool()
            return "cycle_tool"
        elif key == "Escape":
            return "back"

        if self._current_tool == ToolType.JSON:
            return self._handle_json_key(key)
        elif self._current_tool == ToolType.REGEX:
            return self._handle_regex_key(key)
        elif self._current_tool == ToolType.API:
            return self._handle_api_key(key)
        elif self._current_tool == ToolType.HASH:
            return self._handle_hash_key(key)
        elif self._current_tool == ToolType.TIMESTAMP:
            return self._handle_timestamp_key(key)
        elif self._current_tool in (ToolType.BASE64, ToolType.URL_CODEC):
            return self._handle_codec_key(key)
        return None

    def _handle_json_key(self, key: str) -> Optional[str]:
        if key == "Enter":
            self.format_json()
            return "format"
        elif key == "m":
            self.minify_json()
            return "minify"
        elif key == "v":
            self.validate_json()
            return "validate"
        elif key == "s":
            self.sample_json()
            return "sample"
        return None

    def _handle_regex_key(self, key: str) -> Optional[str]:
        if key == "Enter":
            self.test_regex()
            return "test_regex"
        elif key == "s":
            self.sample_regex()
            return "sample"
        elif key == "f":
            flags = list(self._regex_flags)
            if 'i' in flags:
                flags.remove('i')
            else:
                flags.append('i')
            self._regex_flags = ''.join(flags)
            return "toggle_flag"
        return None

    def _handle_api_key(self, key: str) -> Optional[str]:
        if key == "Enter":
            self.execute_api()
            return "execute"
        elif key == "m":
            methods = list(HttpMethod)
            idx = methods.index(self._api_method)
            self._api_method = methods[(idx + 1) % len(methods)]
            return "cycle_method"
        elif key == "s":
            self._api_url = "https://api.github.com/repos/Myco-mycelium/Nythera"
            return "sample_url"
        return None

    def _handle_hash_key(self, key: str) -> Optional[str]:
        if key == "Enter":
            self.generate_hashes()
            return "generate"
        return None

    def _handle_timestamp_key(self, key: str) -> Optional[str]:
        if key == "Enter":
            self.convert_timestamp()
            return "convert"
        elif key == "n":
            self._ts_input = str(int(time.time()))
            self.convert_timestamp()
            return "now"
        return None

    def _handle_codec_key(self, key: str) -> Optional[str]:
        if key == "Enter":
            if self._current_tool == ToolType.BASE64:
                if self._base64_mode == "encode":
                    self.base64_encode()
                else:
                    self.base64_decode()
            elif self._current_tool == ToolType.URL_CODEC:
                if self._url_mode == "encode":
                    self.url_encode()
                else:
                    self.url_decode()
            return "execute"
        elif key == "t":
            if self._current_tool == ToolType.BASE64:
                self._base64_mode = "decode" if self._base64_mode == "encode" else "encode"
            else:
                self._url_mode = "decode" if self._url_mode == "encode" else "encode"
            return "toggle_mode"
        return None
