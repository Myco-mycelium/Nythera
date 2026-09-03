"""
Nyrqis DB Viewer — database browser with query editor and schema inspection.

Features:
- Connect to SQLite databases (create, open)
- Browse tables with data viewer
- Schema inspection (tables, columns, types, indexes)
- Query editor with syntax highlighting (simulated)
- Query results with sorting and pagination
- Table statistics (row count, size)
- Export query results as CSV
- Query history
- Favorite/bookmarked queries
- Keyboard shortcuts throughout
"""

import time
import hashlib
import csv
import io
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Any, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class ViewMode(Enum):
    BROWSER = "browser"
    QUERY = "query"
    SCHEMA = "schema"
    HISTORY = "history"


@dataclass
class Column:
    """A database column definition."""
    name: str
    col_type: str = "TEXT"
    nullable: bool = True
    primary_key: bool = False
    default_value: Optional[str] = None

    @property
    def definition(self) -> str:
        parts = [self.name, self.col_type]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if not self.nullable:
            parts.append("NOT NULL")
        if self.default_value:
            parts.append(f"DEFAULT {self.default_value}")
        return " ".join(parts)


@dataclass
class Index:
    """A database index."""
    name: str
    table: str
    columns: List[str] = field(default_factory=list)
    unique: bool = False

    @property
    def definition(self) -> str:
        unique = "UNIQUE " if self.unique else ""
        cols = ", ".join(self.columns)
        return f"{unique}INDEX {self.name} ON {self.table} ({cols})"


@dataclass
class Table:
    """A database table."""
    name: str
    columns: List[Column] = field(default_factory=list)
    row_count: int = 0
    indexes: List[Index] = field(default_factory=list)
    estimated_size: int = 0

    @property
    def size_str(self) -> str:
        b = self.estimated_size
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"

    @property
    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]


@dataclass
class QueryResult:
    """Results from a SQL query."""
    columns: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    error: str = ""
    query: str = ""

    @property
    def is_error(self) -> bool:
        return bool(self.error)

    @property
    def time_str(self) -> str:
        if self.execution_time_ms < 1:
            return "<1ms"
        elif self.execution_time_ms < 1000:
            return f"{self.execution_time_ms:.1f}ms"
        return f"{self.execution_time_ms / 1000:.2f}s"

    def to_csv(self) -> str:
        output = io.StringIO()
        if self.columns:
            writer = csv.DictWriter(output, fieldnames=self.columns)
            writer.writeheader()
            writer.writerows(self.rows)
        return output.getvalue()


@dataclass
class QueryHistoryEntry:
    """A saved query history entry."""
    query: str
    timestamp: float = field(default_factory=time.time)
    execution_time_ms: float = 0.0
    row_count: int = 0
    is_bookmarked: bool = False
    name: str = ""

    @property
    def preview(self) -> str:
        first_line = self.query.strip().split("\n")[0]
        return first_line[:60]

    @property
    def time_ago(self) -> str:
        diff = time.time() - self.timestamp
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.timestamp).strftime("%b %d")


# ─── Database Viewer ─────────────────────────────────────────────────────


class DatabaseViewer:
    """
    SQLite database viewer for Nyrqis OS.

    Provides table browsing, query execution, and schema inspection.
    """

    def __init__(self):
        self._tables: List[Table] = []
        self._query_history: List[QueryHistoryEntry] = []
        self._current_table: Optional[Table] = None
        self._view_mode: ViewMode = ViewMode.BROWSER
        self._selected_index: int = 0
        self._query_text: str = ""
        self._last_result: Optional[QueryResult] = None
        self._page: int = 0
        self._page_size: int = 50
        self._sort_column: str = ""
        self._sort_ascending: bool = True
        self._database_name: str = ""

        # Init sample database
        self._init_sample_database()

    def _init_sample_database(self) -> None:
        self._database_name = "nyrqis_os.db"

        # Users table
        users_cols = [
            Column("id", "INTEGER", False, True),
            Column("username", "TEXT", False),
            Column("email", "TEXT", False),
            Column("display_name", "TEXT"),
            Column("role", "TEXT", False, False, "'user'"),
            Column("created_at", "DATETIME", False, False, "CURRENT_TIMESTAMP"),
            Column("last_login", "DATETIME"),
            Column("is_active", "BOOLEAN", False, False, "1"),
        ]
        users = Table("users", users_cols, 1547, estimated_size=65536)
        users.indexes = [
            Index("idx_users_email", "users", ["email"], True),
            Index("idx_users_username", "users", ["username"], True),
        ]
        self._tables.append(users)

        # Posts table
        posts_cols = [
            Column("id", "INTEGER", False, True),
            Column("user_id", "INTEGER", False),
            Column("title", "TEXT", False),
            Column("content", "TEXT"),
            Column("status", "TEXT", False, False, "'draft'"),
            Column("views", "INTEGER", False, False, "0"),
            Column("created_at", "DATETIME", False, False, "CURRENT_TIMESTAMP"),
            Column("updated_at", "DATETIME"),
        ]
        posts = Table("posts", posts_cols, 8234, estimated_size=262144)
        posts.indexes = [
            Index("idx_posts_user_id", "posts", ["user_id"]),
            Index("idx_posts_status", "posts", ["status"]),
        ]
        self._tables.append(posts)

        # Comments table
        comments_cols = [
            Column("id", "INTEGER", False, True),
            Column("post_id", "INTEGER", False),
            Column("user_id", "INTEGER", False),
            Column("body", "TEXT", False),
            Column("created_at", "DATETIME", False, False, "CURRENT_TIMESTAMP"),
        ]
        comments = Table("comments", comments_cols, 42156, estimated_size=524288)
        self._tables.append(comments)

        # Tags table
        tags_cols = [
            Column("id", "INTEGER", False, True),
            Column("name", "TEXT", False),
            Column("slug", "TEXT", False),
            Column("color", "TEXT", False, False, "'#4A90D9'"),
        ]
        tags = Table("tags", tags_cols, 156, estimated_size=8192)
        tags.indexes = [Index("idx_tags_slug", "tags", ["slug"], True)]
        self._tables.append(tags)

        # Post tags (junction)
        post_tags_cols = [
            Column("post_id", "INTEGER", False),
            Column("tag_id", "INTEGER", False),
        ]
        post_tags = Table("post_tags", post_tags_cols, 15623, estimated_size=32768)
        post_tags.indexes = [
            Index("idx_post_tags_composite", "post_tags", ["post_id", "tag_id"], True),
        ]
        self._tables.append(post_tags)

        # Settings table
        settings_cols = [
            Column("key", "TEXT", False, True),
            Column("value", "TEXT"),
            Column("category", "TEXT", False, False, "'general'"),
            Column("updated_at", "DATETIME", False, False, "CURRENT_TIMESTAMP"),
        ]
        settings = Table("settings", settings_cols, 89, estimated_size=4096)
        self._tables.append(settings)

        # Sample query history
        self._query_history = [
            QueryHistoryEntry("SELECT u.username, COUNT(p.id) AS post_count\nFROM users u\nLEFT JOIN posts p ON u.id = p.user_id\nGROUP BY u.id\nORDER BY post_count DESC\nLIMIT 10;", 25.3, 10, True, "Top posters"),
            QueryHistoryEntry("SELECT title, views, created_at\nFROM posts\nWHERE status = 'published'\nORDER BY views DESC\nLIMIT 20;", 12.1, 20),
            QueryHistoryEntry("SELECT COUNT(*) AS total_users,\n  SUM(CASE WHEN is_active THEN 1 ELSE 0 END) AS active\nFROM users;", 3.2, 1),
            QueryHistoryEntry("SELECT t.name, COUNT(pt.post_id) AS usage\nFROM tags t\nJOIN post_tags pt ON t.id = pt.tag_id\nGROUP BY t.id\nORDER BY usage DESC;", 18.7, 156),
            QueryHistoryEntry("SELECT * FROM settings WHERE category = 'theme';", 1.1, 5),
        ]

    # ── Table Operations ──────────────────────────────────────────────

    @property
    def tables(self) -> List[Table]:
        return list(self._tables)

    def select_table(self, index: int) -> bool:
        if 0 <= index < len(self._tables):
            self._current_table = self._tables[index]
            self._selected_index = 0
            return True
        return False

    @property
    def current_table(self) -> Optional[Table]:
        return self._current_table

    def get_table_data(self, table: Table = None) -> List[Dict[str, str]]:
        """Generate simulated table data."""
        target = table or self._current_table
        if not target:
            return []

        rows = []
        if target.name == "users":
            names = ["alice", "bob", "charlie", "diana", "eve", "frank", "grace", "henry"]
            roles = ["admin", "editor", "user", "moderator"]
            for i in range(min(20, target.row_count)):
                name = names[i % len(names)]
                rows.append({
                    "id": str(i + 1),
                    "username": f"{name}{i}" if i > 7 else name,
                    "email": f"{name}@example.com",
                    "display_name": name.title(),
                    "role": roles[i % len(roles)],
                    "created_at": f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}",
                    "last_login": f"2026-09-{1 + i % 3:02d}" if i < 5 else "",
                    "is_active": "1" if i < 6 else "0",
                })
        elif target.name == "posts":
            titles = ["Welcome to Nyrqis", "Getting Started", "Advanced Tips", "Plugin Development",
                       "Theme Customization", "Terminal Guide", "File Manager", "Accessibility"]
            statuses = ["published", "draft", "archived"]
            for i in range(min(20, target.row_count)):
                rows.append({
                    "id": str(i + 1),
                    "user_id": str((i % 5) + 1),
                    "title": titles[i % len(titles)],
                    "content": f"Content for post {i + 1}...",
                    "status": statuses[i % 3],
                    "views": str(1000 - i * 50),
                    "created_at": f"2026-08-{1 + i % 28:02d}",
                    "updated_at": f"2026-09-{1 + i % 3:02d}",
                })
        else:
            # Generic data for other tables
            for i in range(min(10, target.row_count)):
                row = {}
                for col in target.columns:
                    if col.col_type == "INTEGER":
                        row[col.name] = str(i + 1)
                    elif col.col_type == "TEXT":
                        row[col.name] = f"value_{i}"
                    else:
                        row[col.name] = "0"
                rows.append(row)

        return rows

    # ── Query Execution ───────────────────────────────────────────────

    def execute_query(self, query: str) -> QueryResult:
        """Execute a SQL query (simulated)."""
        start = time.time()
        query = query.strip()

        # Simulate execution
        if query.upper().startswith("SELECT"):
            result = self._execute_select(query)
        elif query.upper().startswith("INSERT"):
            result = QueryResult(columns=[], rows=[], row_count=1, query=query)
        elif query.upper().startswith("UPDATE"):
            result = QueryResult(columns=[], rows=[], row_count=3, query=query)
        elif query.upper().startswith("DELETE"):
            result = QueryResult(columns=[], rows=[], row_count=0, query=query)
        elif query.upper().startswith("CREATE"):
            result = QueryResult(columns=[], rows=[], row_count=0, query=query)
        else:
            result = QueryResult(error="Unsupported query type", query=query)

        result.execution_time_ms = (time.time() - start) * 1000 + 5  # Add simulated time

        # Add to history
        self._query_history.insert(0, QueryHistoryEntry(
            query=query,
            execution_time_ms=result.execution_time_ms,
            row_count=result.row_count,
        ))

        self._last_result = result
        return result

    def _execute_select(self, query: str) -> QueryResult:
        """Simulate SELECT query execution."""
        query_upper = query.upper()

        # Detect table name
        table_name = ""
        for table in self._tables:
            if table.name.upper() in query_upper:
                table_name = table.name
                break

        if not table_name:
            return QueryResult(error=f"Table not found in query", query=query)

        # Get table data
        table = next((t for t in self._tables if t.name == table_name), None)
        if not table:
            return QueryResult(error=f"Table '{table_name}' not found", query=query)

        data = self.get_table_data(table)

        # Simple LIMIT handling
        limit = 50
        if "LIMIT" in query_upper:
            try:
                limit = int(query_upper.split("LIMIT")[-1].strip().split()[0])
            except (ValueError, IndexError):
                pass

        # Simple COUNT handling
        if "COUNT(*)" in query.upper():
            return QueryResult(
                columns=["count"],
                rows=[{"count": str(table.row_count)}],
                row_count=1,
                query=query,
            )

        # Return columns and rows
        columns = table.column_names
        rows = data[:limit]

        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            query=query,
        )

    # ── View State ────────────────────────────────────────────────────

    def set_view(self, mode: ViewMode) -> None:
        self._view_mode = mode

    def cycle_view(self) -> ViewMode:
        views = [ViewMode.BROWSER, ViewMode.SCHEMA, ViewMode.QUERY, ViewMode.HISTORY]
        idx = views.index(self._view_mode)
        self._view_mode = views[(idx + 1) % len(views)]
        return self._view_mode

    @property
    def view_mode(self) -> ViewMode:
        return self._view_mode

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        if self._view_mode == ViewMode.BROWSER:
            self._selected_index = min(len(self._tables) - 1, self._selected_index + 1)
        elif self._view_mode == ViewMode.HISTORY:
            self._selected_index = min(len(self._query_history) - 1, self._selected_index + 1)

    @property
    def database_name(self) -> str:
        return self._database_name

    @property
    def query_history(self) -> List[QueryHistoryEntry]:
        return list(self._query_history)

    @property
    def last_result(self) -> Optional[QueryResult]:
        return self._last_result

    @property
    def total_tables(self) -> int:
        return len(self._tables)

    @property
    def total_rows(self) -> int:
        return sum(t.row_count for t in self._tables)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_browser(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(f" 🗄️  {self._database_name} — Tables ({self.total_tables})")
        lines.append(f" Total rows: {self.total_rows:,}")
        lines.append("─" * width)

        # Header
        lines.append(f" {'Table':<20} {'Rows':>10} {'Size':>10} {'Columns':>8}")
        lines.append("─" * width)

        for i, table in enumerate(self._tables):
            marker = "▸" if i == self._selected_index else " "
            line = f"{marker} {table.name:<20} {table.row_count:>10,} {table.size_str:>10} {len(table.columns):>8}"
            lines.append(line[:width])

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Browse  S:Schema  Q:Query  H:History")
        return lines

    def render_schema(self, width: int = 72) -> List[str]:
        lines = []
        lines.append(" 📋 Schema Inspector")
        lines.append("─" * width)

        for table in self._tables:
            lines.append(f" 📦 {table.name} ({table.row_count:,} rows, {table.size_str})")
            lines.append(f"   {'Column':<20} {'Type':<12} {'PK':<4} {'Nullable':<8} {'Default'}")
            lines.append(f"   {'─' * (width - 4)}")

            for col in table.columns:
                pk = "PK" if col.primary_key else ""
                nullable = "YES" if col.nullable else "NO"
                default = col.default_value or ""
                lines.append(f"   {col.name:<20} {col.col_type:<12} {pk:<4} {nullable:<8} {default}")

            if table.indexes:
                lines.append(f"   Indexes:")
                for idx in table.indexes:
                    lines.append(f"     {idx.definition}")

            lines.append("")

        lines.append("─" * width)
        lines.append(" B:Browser  Q:Query  H:History")
        return lines

    def render_query(self, width: int = 72) -> List[str]:
        lines = []
        lines.append(" 💻 Query Editor")
        lines.append("─" * width)

        # Query input
        lines.append(" Query:")
        for line in self._query_text.split("\n")[:5]:
            lines.append(f" │ {line[:width - 4]}")

        lines.append("─" * width)

        # Results
        if self._last_result:
            r = self._last_result
            if r.is_error:
                lines.append(f" ❌ {r.error}")
            else:
                lines.append(f" ✅ {r.row_count} rows ({r.time_str})")

                if r.columns:
                    # Header
                    header = " │ ".join(f"{c[:15]:<15}" for c in r.columns[:5])
                    lines.append(f" │ {header[:width - 4]}")
                    lines.append(f" │ {'─' * (width - 4)}")

                    # Rows
                    for row in r.rows[:15]:
                        vals = " │ ".join(f"{str(row.get(c, ''))[:15]:<15}" for c in r.columns[:5])
                        lines.append(f" │ {vals[:width - 4]}")

                    if r.row_count > 15:
                        lines.append(f"   ... and {r.row_count - 15} more rows")

        lines.append("─" * width)
        lines.append(" Enter:Execute  ↑↓:History  B:Browser  S:Schema")
        return lines

    def render_history(self, width: int = 72) -> List[str]:
        lines = []
        lines.append(" 📜 Query History")
        lines.append(f" {len(self._query_history)} queries")
        lines.append("─" * width)

        for i, entry in enumerate(self._query_history):
            marker = "▸" if i == self._selected_index else " "
            star = " ⭐" if entry.is_bookmarked else ""
            name = f" ({entry.name})" if entry.name else ""
            lines.append(f"{marker} {entry.preview}{name}{star}")
            lines.append(f"   {entry.time_ago} · {entry.execution_time_ms:.1f}ms · {entry.row_count} rows")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Load  B:Bookmark  Del:Delete  Esc:Back")
        return lines

    def render(self, width: int = 72, height: int = 30) -> List[str]:
        if self._view_mode == ViewMode.SCHEMA:
            return self.render_schema(width)
        elif self._view_mode == ViewMode.QUERY:
            return self.render_query(width)
        elif self._view_mode == ViewMode.HISTORY:
            return self.render_history(width)
        return self.render_browser(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == ViewMode.QUERY:
            return self._handle_query_key(key)
        elif self._view_mode == ViewMode.HISTORY:
            return self._handle_history_key(key)
        elif self._view_mode == ViewMode.SCHEMA:
            return self._handle_schema_key(key)
        return self._handle_browser_key(key)

    def _handle_browser_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.select_table(self._selected_index)
            return "select_table"
        elif key == "s":
            self._view_mode = ViewMode.SCHEMA
            return "schema"
        elif key == "q":
            self._view_mode = ViewMode.QUERY
            return "query"
        elif key == "h":
            self._view_mode = ViewMode.HISTORY
            return "history"
        return None

    def _handle_query_key(self, key: str) -> Optional[str]:
        if key == "Enter":
            if self._query_text.strip():
                self.execute_query(self._query_text.strip())
            return "execute"
        elif key == "b":
            self._view_mode = ViewMode.BROWSER
            return "browser"
        elif key == "s":
            self._view_mode = ViewMode.SCHEMA
            return "schema"
        elif key == "Escape":
            self._query_text = ""
            self._view_mode = ViewMode.BROWSER
            return "back"
        return None

    def _handle_schema_key(self, key: str) -> Optional[str]:
        if key == "b":
            self._view_mode = ViewMode.BROWSER
            return "browser"
        elif key == "q":
            self._view_mode = ViewMode.QUERY
            return "query"
        elif key == "h":
            self._view_mode = ViewMode.HISTORY
            return "history"
        return None

    def _handle_history_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = ViewMode.BROWSER
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            if 0 <= self._selected_index < len(self._query_history):
                self._query_text = self._query_history[self._selected_index].query
                self._view_mode = ViewMode.QUERY
            return "load_query"
        elif key == "b":
            if 0 <= self._selected_index < len(self._query_history):
                self._query_history[self._selected_index].is_bookmarked = not self._query_history[self._selected_index].is_bookmarked
            return "toggle_bookmark"
        return None

    # ── Callbacks ─────────────────────────────────────────────────────

    def _notify(self, event: str) -> None:
        pass
