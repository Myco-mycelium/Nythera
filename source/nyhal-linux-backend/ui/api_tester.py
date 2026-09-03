"""REST API Tester — request builder, response viewer, collection management for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time


class HttpMethod(Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class AuthType(Enum):
    NONE = "None"
    BEARER = "Bearer Token"
    BASIC = "Basic Auth"
    API_KEY = "API Key"
    OAUTH2 = "OAuth 2.0"
    DIGEST = "Digest Auth"


class BodyType(Enum):
    NONE = "None"
    JSON = "JSON"
    FORM_DATA = "Form Data"
    URL_ENCODED = "URL Encoded"
    RAW = "Raw"
    XML = "XML"
    BINARY = "Binary"


class ResponseStatus(Enum):
    SUCCESS = "Success"
    REDIRECT = "Redirect"
    CLIENT_ERROR = "Client Error"
    SERVER_ERROR = "Server Error"
    TIMEOUT = "Timeout"
    NETWORK_ERROR = "Network Error"


@dataclass
class KeyValuePair:
    key: str = ""
    value: str = ""
    enabled: bool = True
    description: str = ""


@dataclass
class AuthConfig:
    auth_type: AuthType = AuthType.NONE
    token: str = ""
    username: str = ""
    password: str = ""
    api_key: str = ""
    api_key_header: str = "X-API-Key"

    @property
    def masked_token(self) -> str:
        if len(self.token) > 8:
            return self.token[:4] + "****" + self.token[-4:]
        return "****"


@dataclass
class RequestBody:
    body_type: BodyType = BodyType.NONE
    content: str = ""
    form_data: List[KeyValuePair] = field(default_factory=list)

    @property
    def content_preview(self) -> str:
        if not self.content:
            return "(empty)"
        return self.content[:100]


@dataclass
class HttpResponse:
    status_code: int = 0
    status_text: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    body: str = ""
    time_ms: float = 0.0
    size_bytes: int = 0
    history: List[int] = field(default_factory=list)

    @property
    def status_category(self) -> ResponseStatus:
        if 200 <= self.status_code < 300:
            return ResponseStatus.SUCCESS
        elif 300 <= self.status_code < 400:
            return ResponseStatus.REDIRECT
        elif 400 <= self.status_code < 500:
            return ResponseStatus.CLIENT_ERROR
        elif 500 <= self.status_code:
            return ResponseStatus.SERVER_ERROR
        return ResponseStatus.SUCCESS

    @property
    def status_icon(self) -> str:
        icons = {
            ResponseStatus.SUCCESS: "✅", ResponseStatus.REDIRECT: "↪",
            ResponseStatus.CLIENT_ERROR: "❌", ResponseStatus.SERVER_ERROR: "💥",
            ResponseStatus.TIMEOUT: "⏱", ResponseStatus.NETWORK_ERROR: "📴",
        }
        return icons.get(self.status_category, "?")

    @property
    def size_str(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024**2:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes / 1024**2:.1f} MB"

    @property
    def time_str(self) -> str:
        if self.time_ms < 1:
            return f"{self.time_ms * 1000:.0f}µs"
        elif self.time_ms < 1000:
            return f"{self.time_ms:.1f}ms"
        return f"{self.time_ms / 1000:.2f}s"


@dataclass
class ApiRequest:
    id: int
    name: str = "New Request"
    method: HttpMethod = HttpMethod.GET
    url: str = ""
    headers: List[KeyValuePair] = field(default_factory=list)
    query_params: List[KeyValuePair] = field(default_factory=list)
    body: RequestBody = field(default_factory=RequestBody)
    auth: AuthConfig = field(default_factory=AuthConfig)
    pre_script: str = ""
    post_script: str = ""
    response: Optional[HttpResponse] = None
    created: float = 0.0
    last_run: float = 0.0

    @property
    def method_icon(self) -> str:
        icons = {
            HttpMethod.GET: "🟢", HttpMethod.POST: "🔵", HttpMethod.PUT: "🟡",
            HttpMethod.PATCH: "🟠", HttpMethod.DELETE: "🔴", HttpMethod.HEAD: "⚪",
            HttpMethod.OPTIONS: "⚪",
        }
        return icons.get(self.method, "?")


@dataclass
class RequestCollection:
    id: int
    name: str = ""
    requests: List[ApiRequest] = field(default_factory=list)
    base_url: str = ""
    auth: AuthConfig = field(default_factory=AuthConfig)
    headers: List[KeyValuePair] = field(default_factory=list)
    variables: Dict[str, str] = field(default_factory=dict)
    created: float = 0.0

    @property
    def request_count(self) -> int:
        return len(self.requests)


@dataclass
class Environment:
    name: str = ""
    variables: Dict[str, str] = field(default_factory=dict)
    base_url: str = ""
    active: bool = False

    @property
    def var_count(self) -> int:
        return len(self.variables)


class ApiTester:
    def __init__(self):
        self._collections: List[RequestCollection] = []
        self._environments: List[Environment] = []
        self._selected_collection: int = 0
        self._selected_request: int = 0
        self._history: List[ApiRequest] = []
        self._view_mode: str = "request"
        self._show_headers: bool = True
        self._auto_format: bool = True
        self._history_limit: int = 100
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        self._environments = [
            Environment("Production", {"base_url": "https://api.nyrqis.io", "api_key": "prod-key-****"},
                        "https://api.nyrqis.io", True),
            Environment("Staging", {"base_url": "https://staging-api.nyrqis.io", "api_key": "stg-key-****"},
                        "https://staging-api.nyrqis.io"),
            Environment("Local", {"base_url": "http://localhost:8000", "api_key": "dev-key-****"},
                        "http://localhost:8000"),
        ]

        # Collection 1: Nyrqis API
        col1 = RequestCollection(1, "Nyrqis API", base_url="https://api.nyrqis.io",
                                 auth=AuthConfig(AuthType.BEARER, "eyJhbGciOiJIUzI1NiJ9.****"),
                                 created=now - 86400 * 30)
        col1.requests = [
            ApiRequest(1, "List Users", HttpMethod.GET, "/api/v1/users",
                       headers=[KeyValuePair("Accept", "application/json")],
                       query_params=[KeyValuePair("page", "1"), KeyValuePair("limit", "20")],
                       response=HttpResponse(200, "OK", {"Content-Type": "application/json"},
                                             '{"users": [...], "total": 150, "page": 1}', 45.2, 2400)),
            ApiRequest(2, "Create User", HttpMethod.POST, "/api/v1/users",
                       headers=[KeyValuePair("Content-Type", "application/json")],
                       body=RequestBody(BodyType.JSON, '{"name": "Alice", "email": "alice@nyrqis.io", "role": "admin"}'),
                       response=HttpResponse(201, "Created", {}, '{"id": 151, "name": "Alice"}', 89.3, 156)),
            ApiRequest(3, "Update User", HttpMethod.PATCH, "/api/v1/users/151",
                       body=RequestBody(BodyType.JSON, '{"role": "superadmin"}'),
                       response=HttpResponse(200, "OK", {}, '{"id": 151, "role": "superadmin"}', 32.1, 128)),
            ApiRequest(4, "Delete User", HttpMethod.DELETE, "/api/v1/users/151",
                       response=HttpResponse(204, "No Content", {}, "", 28.5, 0)),
            ApiRequest(5, "Get User Profile", HttpMethod.GET, "/api/v1/users/151/profile",
                       response=HttpResponse(200, "OK", {}, '{"avatar": "...", "bio": "..."}', 55.0, 1800)),
        ]
        self._collections.append(col1)

        # Collection 2: Auth
        col2 = RequestCollection(2, "Authentication", base_url="https://api.nyrqis.io", created=now - 86400 * 20)
        col2.requests = [
            ApiRequest(6, "Login", HttpMethod.POST, "/auth/login",
                       body=RequestBody(BodyType.JSON, '{"email": "admin@nyrqis.io", "password": "***"}'),
                       response=HttpResponse(200, "OK", {}, '{"token": "eyJhbGci...", "expires_in": 3600}', 120.5, 256)),
            ApiRequest(7, "Refresh Token", HttpMethod.POST, "/auth/refresh",
                       body=RequestBody(BodyType.JSON, '{"refresh_token": "rt_****"}'),
                       response=HttpResponse(200, "OK", {}, '{"token": "eyJhbGci..."}', 65.3, 200)),
            ApiRequest(8, "Logout", HttpMethod.POST, "/auth/logout",
                       response=HttpResponse(204, "No Content", {}, "", 35.2, 0)),
        ]
        self._collections.append(col2)

        # Collection 3: System
        col3 = RequestCollection(3, "System Endpoints", base_url="https://api.nyrqis.io", created=now - 86400 * 10)
        col3.requests = [
            ApiRequest(9, "Health Check", HttpMethod.GET, "/health",
                       response=HttpResponse(200, "OK", {}, '{"status": "healthy", "uptime": "45d 12h"}', 8.2, 64)),
            ApiRequest(10, "System Info", HttpMethod.GET, "/system/info",
                       response=HttpResponse(200, "OK", {}, '{"version": "1.4.0", "kernel": "6.12"}', 22.1, 512)),
            ApiRequest(11, "Metrics", HttpMethod.GET, "/metrics",
                       response=HttpResponse(200, "OK", {"Content-Type": "text/plain"}, "# HELP http_requests_total\nhttp_requests_total 12345", 150.0, 8200)),
        ]
        self._collections.append(col3)

        self._history = [col1.requests[0], col2.requests[0], col3.requests[0]]

    @property
    def selected_collection(self) -> Optional[RequestCollection]:
        if 0 <= self._selected_collection < len(self._collections):
            return self._collections[self._selected_collection]
        return None

    @property
    def selected_request(self) -> Optional[ApiRequest]:
        col = self.selected_collection
        if col and 0 <= self._selected_request < len(col.requests):
            return col.requests[self._selected_request]
        return None

    @property
    def total_requests(self) -> int:
        return sum(c.request_count for c in self._collections)

    def select_collection(self, idx: int):
        if 0 <= idx < len(self._collections):
            self._selected_collection = idx
            self._selected_request = 0

    def select_request(self, idx: int):
        self._selected_request = idx

    def send_request(self):
        req = self.selected_request
        if req:
            # Simulate response
            import random
            req.response = HttpResponse(
                random.choice([200, 200, 200, 201, 400, 404, 500]),
                "OK", {"Content-Type": "application/json"},
                '{"status": "ok"}', random.uniform(10, 200), random.randint(50, 5000))
            req.last_run = time.time()
            self._history.append(req)

    def handle_input(self, key: str):
        key = key.lower()
        if key == "s":
            self.send_request()
        elif key == "h":
            self._show_headers = not self._show_headers

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS REST API TESTER                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        env = next((e for e in self._environments if e.active), None)
        lines.append(f"  Collections: {len(self._collections)}  Requests: {self.total_requests}  History: {len(self._history)}  Env: {env.name if env else 'None'}")
        lines.append("")

        # Collections
        lines.append("  ── Collections ──")
        for i, col in enumerate(self._collections):
            sel = "▶" if i == self._selected_collection else " "
            lines.append(f"  {sel} 📁 {col.name}  {col.request_count} requests  {col.base_url}")
        lines.append("")

        # Requests in collection
        col = self.selected_collection
        if col:
            lines.append(f"  ── {col.name} ──")
            for i, req in enumerate(col.requests):
                sel = "▶" if i == self._selected_request else " "
                resp = ""
                if req.response:
                    resp = f"  {req.response.status_icon} {req.response.status_code} {req.response.time_str}"
                lines.append(f"  {sel} {req.method_icon} {req.method.value:<7s} {req.name:<25s} {req.url}{resp}")
            lines.append("")

        # Selected request detail
        req = self.selected_request
        if req:
            lines.append(f"  ── {req.method.value} {req.url} ──")
            lines.append(f"  Auth: {req.auth.auth_type.value}")
            if req.headers:
                lines.append(f"  Headers: {len(req.headers)}")
            if req.body.content:
                lines.append(f"  Body ({req.body.body_type.value}): {req.body.content_preview}")
            lines.append("")

            # Response
            if req.response:
                resp = req.response
                lines.append(f"  ── Response ──")
                lines.append(f"  {resp.status_icon} {resp.status_code} {resp.status_text}  {resp.time_str}  {resp.size_str}")
                if resp.body:
                    lines.append(f"  Body: {resp.body[:70]}")
                if resp.headers:
                    lines.append(f"  Headers: {', '.join(list(resp.headers.keys())[:5])}")
                lines.append("")

        # Environments
        lines.append("  ── Environments ──")
        for env in self._environments:
            active = "🟢" if env.active else "  "
            lines.append(f"  {active} {env.name}  {env.var_count} vars  {env.base_url}")
        lines.append("")

        lines.append("  [S]Send Request [H]Headers [E]Environments [↑↓]Select [C]Collections")
        return lines
