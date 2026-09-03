"""Database GUI Client — query editor, schema browser, data editor for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple, Any
import time
import json


class DatabaseType(Enum):
    POSTGRESQL = "PostgreSQL"
    MYSQL = "MySQL"
    SQLITE = "SQLite"
    MARIADB = "MariaDB"
    MONGODB = "MongoDB"
    REDIS = "Redis"
    INFLUXDB = "InfluxDB"


class QueryStatus(Enum):
    SUCCESS = "Success"
    ERROR = "Error"
    RUNNING = "Running"
    PENDING = "Pending"
    CANCELLED = "Cancelled"


class ColumnType(Enum):
    INTEGER = "integer"
    BIGINT = "bigint"
    FLOAT = "float"
    DOUBLE = "double"
    VARCHAR = "varchar"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"
    JSON = "json"
    UUID = "uuid"
    BINARY = "binary"
    SERIAL = "serial"


class ConstraintType(Enum):
    PRIMARY_KEY = "PRIMARY KEY"
    FOREIGN_KEY = "FOREIGN KEY"
    UNIQUE = "UNIQUE"
    NOT_NULL = "NOT NULL"
    CHECK = "CHECK"
    DEFAULT = "DEFAULT"


@dataclass
class Column:
    name: str
    col_type: ColumnType = ColumnType.VARCHAR
    nullable: bool = True
    primary_key: bool = False
    foreign_key: str = ""
    default_value: str = ""
    max_length: int = 0
    auto_increment: bool = False
    indexed: bool = False
    comment: str = ""

    @property
    def type_str(self) -> str:
        s = self.col_type.value
        if self.max_length > 0:
            s += f"({self.max_length})"
        return s

    @property
    def icon(self) -> str:
        if self.primary_key:
            return "🔑"
        if self.foreign_key:
            return "🔗"
        if not self.nullable:
            return "mandatory"
        return ""


@dataclass
class Index:
    name: str
    table: str
    columns: List[str] = field(default_factory=list)
    unique: bool = False
    index_type: str = "btree"  # btree, hash, gin, gist

    @property
    def type_str(self) -> str:
        prefix = "UNIQUE " if self.unique else ""
        return f"{prefix}{self.index_type.upper()} ({', '.join(self.columns)})"


@dataclass
class Table:
    name: str
    columns: List[Column] = field(default_factory=list)
    row_count: int = 0
    indexes: List[Index] = field(default_factory=list)
    schema: str = "public"
    engine: str = "InnoDB"
    collation: str = "utf8mb4"
    comment: str = ""
    estimated_size: str = "0 B"
    last_analyzed: float = 0.0

    @property
    def column_count(self) -> int:
        return len(self.columns)

    @property
    def primary_keys(self) -> List[str]:
        return [c.name for c in self.columns if c.primary_key]

    @property
    def foreign_keys(self) -> List[Tuple[str, str]]:
        return [(c.name, c.foreign_key) for c in self.columns if c.foreign_key]

    @property
    def size_str(self) -> str:
        return f"{self.row_count:,} rows  {self.estimated_size}"


@dataclass
class QueryResult:
    query: str
    status: QueryStatus = QueryStatus.PENDING
    columns: List[str] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)
    row_count: int = 0
    affected_rows: int = 0
    execution_time_ms: float = 0.0
    error_message: str = ""
    query_plan: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return len(self.rows) > 0

    @property
    def is_select(self) -> bool:
        return self.query.strip().upper().startswith("SELECT")

    @property
    def duration_str(self) -> str:
        if self.execution_time_ms < 1:
            return f"{self.execution_time_ms * 1000:.0f}µs"
        elif self.execution_time_ms < 1000:
            return f"{self.execution_time_ms:.1f}ms"
        else:
            return f"{self.execution_time_ms / 1000:.2f}s"


@dataclass
class SavedQuery:
    name: str
    query: str
    database: str = ""
    tags: List[str] = field(default_factory=list)
    last_run: float = 0.0
    favorite: bool = False
    notes: str = ""

    @property
    def tag_str(self) -> str:
        return " ".join(f"[{t}]" for t in self.tags) if self.tags else ""


@dataclass
class Connection:
    name: str
    db_type: DatabaseType = DatabaseType.POSTGRESQL
    host: str = "localhost"
    port: int = 5432
    database: str = ""
    username: str = ""
    password: str = ""
    ssl: bool = False
    ssh_tunnel: bool = False
    ssh_host: str = ""
    connected: bool = False
    last_connected: float = 0.0
    latency_ms: float = 0.0

    @property
    def url(self) -> str:
        return f"{self.db_type.value.lower()}://{self.username}@{self.host}:{self.port}/{self.database}"

    @property
    def status_icon(self) -> str:
        return "🟢" if self.connected else "🔴"


class DBClient:
    def __init__(self):
        self._connections: List[Connection] = []
        self._active_connection: int = 0
        self._tables: List[Table] = []
        self._selected_table: int = 0
        self._query_history: List[QueryResult] = []
        self._saved_queries: List[SavedQuery] = []
        self._current_query: str = ""
        self._current_result: Optional[QueryResult] = None
        self._view_mode: str = "schema"
        self._auto_commit: bool = True
        self._limit_rows: int = 1000
        self._show_system_tables: bool = False
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        # Connections
        self._connections = [
            Connection("Local PostgreSQL", DatabaseType.POSTGRESQL, "localhost", 5432,
                       "nyrqis_dev", "nyrqis", connected=True, latency_ms=1.2),
            Connection("Production MySQL", DatabaseType.MYSQL, "db.prod.internal", 3306,
                       "nyrqis_prod", "admin", ssl=True, connected=False),
            Connection("MongoDB Analytics", DatabaseType.MONGODB, "mongo.analytics", 27017,
                       "analytics", "reader", connected=True, latency_ms=5.8),
            Connection("SQLite Local", DatabaseType.SQLITE, "", 0, "/data/local.db", "",
                       connected=True, latency_ms=0.1),
            Connection("Redis Cache", DatabaseType.REDIS, "redis.internal", 6379,
                       "", "", connected=True, latency_ms=0.3),
        ]

        # Tables
        self._tables = [
            Table("users", [
                Column("id", ColumnType.SERIAL, False, True, auto_increment=True, indexed=True),
                Column("email", ColumnType.VARCHAR, False, max_length=255, indexed=True),
                Column("username", ColumnType.VARCHAR, False, max_length=50, indexed=True),
                Column("password_hash", ColumnType.VARCHAR, False, max_length=255),
                Column("display_name", ColumnType.VARCHAR, True, max_length=100),
                Column("avatar_url", ColumnType.TEXT, True),
                Column("is_active", ColumnType.BOOLEAN, False, default_value="true"),
                Column("role", ColumnType.VARCHAR, False, default_value="user"),
                Column("created_at", ColumnType.TIMESTAMP, False),
                Column("updated_at", ColumnType.TIMESTAMP, True),
            ], row_count=15420, estimated_size="2.4 MB",
            indexes=[
                Index("idx_users_email", "users", ["email"], unique=True),
                Index("idx_users_username", "users", ["username"], unique=True),
            ]),
            Table("posts", [
                Column("id", ColumnType.SERIAL, False, True, auto_increment=True),
                Column("author_id", ColumnType.BIGINT, False, foreign_key="users.id"),
                Column("title", ColumnType.VARCHAR, False, max_length=200),
                Column("slug", ColumnType.VARCHAR, False, max_length=200, indexed=True),
                Column("content", ColumnType.TEXT, True),
                Column("status", ColumnType.VARCHAR, False, default_value="draft"),
                Column("published_at", ColumnType.TIMESTAMP, True),
                Column("created_at", ColumnType.TIMESTAMP, False),
            ], row_count=8750, estimated_size="12.8 MB"),
            Table("comments", [
                Column("id", ColumnType.SERIAL, False, True, auto_increment=True),
                Column("post_id", ColumnType.BIGINT, False, foreign_key="posts.id"),
                Column("user_id", ColumnType.BIGINT, False, foreign_key="users.id"),
                Column("body", ColumnType.TEXT, False),
                Column("created_at", ColumnType.TIMESTAMP, False),
            ], row_count=42300, estimated_size="8.1 MB"),
            Table("tags", [
                Column("id", ColumnType.SERIAL, False, True, auto_increment=True),
                Column("name", ColumnType.VARCHAR, False, max_length=50, indexed=True),
                Column("slug", ColumnType.VARCHAR, False, max_length=50),
            ], row_count=256, estimated_size="24 KB"),
            Table("post_tags", [
                Column("post_id", ColumnType.BIGINT, False, foreign_key="posts.id"),
                Column("tag_id", ColumnType.BIGINT, False, foreign_key="tags.id"),
            ], row_count=18400, estimated_size="420 KB"),
            Table("sessions", [
                Column("id", ColumnType.UUID, False, True),
                Column("user_id", ColumnType.BIGINT, False, foreign_key="users.id"),
                Column("token", ColumnType.VARCHAR, False, max_length=255, indexed=True),
                Column("ip_address", ColumnType.VARCHAR, True, max_length=45),
                Column("user_agent", ColumnType.TEXT, True),
                Column("expires_at", ColumnType.TIMESTAMP, False),
            ], row_count=3200, estimated_size="1.2 MB"),
            Table("audit_log", [
                Column("id", ColumnType.BIGINT, False, True, auto_increment=True),
                Column("user_id", ColumnType.BIGINT, True, foreign_key="users.id"),
                Column("action", ColumnType.VARCHAR, False, max_length=100),
                Column("resource_type", ColumnType.VARCHAR, True, max_length=50),
                Column("resource_id", ColumnType.BIGINT, True),
                Column("details", ColumnType.JSON, True),
                Column("created_at", ColumnType.TIMESTAMP, False),
            ], row_count=128900, estimated_size="45.6 MB"),
        ]

        # Saved queries
        self._saved_queries = [
            SavedQuery("Active Users", "SELECT id, email, username FROM users WHERE is_active = true ORDER BY created_at DESC LIMIT 100",
                       tags=["report", "users"], favorite=True),
            SavedQuery("Post Stats", "SELECT p.title, COUNT(c.id) as comments FROM posts p LEFT JOIN comments c ON c.post_id = p.id GROUP BY p.id ORDER BY comments DESC",
                       tags=["analytics"]),
            SavedQuery("Recent Audit", "SELECT * FROM audit_log WHERE created_at > NOW() - INTERVAL '1 day' ORDER BY id DESC LIMIT 50",
                       tags=["security"]),
            SavedQuery("Table Sizes", "SELECT tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size FROM pg_tables ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC",
                       tags=["admin", "maintenance"], favorite=True),
        ]

        # Sample query result
        self._current_result = QueryResult(
            "SELECT id, email, username, role, created_at FROM users WHERE is_active = true ORDER BY created_at DESC LIMIT 5",
            QueryStatus.SUCCESS,
            columns=["id", "email", "username", "role", "created_at"],
            rows=[
                [1, "alice@example.com", "alice", "admin", "2026-01-15 10:30:00"],
                [2, "bob@example.com", "bob", "editor", "2026-02-20 14:22:00"],
                [3, "carol@example.com", "carol", "user", "2026-03-01 09:15:00"],
                [4, "dave@example.com", "dave", "user", "2026-03-15 16:45:00"],
                [5, "eve@example.com", "eve", "moderator", "2026-04-01 11:00:00"],
            ],
            row_count=5,
            execution_time_ms=12.4,
        )
        self._current_query = self._current_result.query

    @property
    def active_connection(self) -> Optional[Connection]:
        if 0 <= self._active_connection < len(self._connections):
            return self._connections[self._active_connection]
        return None

    @property
    def selected_table(self) -> Optional[Table]:
        if 0 <= self._selected_table < len(self._tables):
            return self._tables[self._selected_table]
        return None

    @property
    def total_tables(self) -> int:
        return len(self._tables)

    @property
    def total_rows(self) -> int:
        return sum(t.row_count for t in self._tables)

    def select_connection(self, idx: int):
        if 0 <= idx < len(self._connections):
            self._active_connection = idx

    def select_table(self, idx: int):
        if 0 <= idx < len(self._tables):
            self._selected_table = idx

    def execute_query(self, query: str) -> QueryResult:
        import random
        t0 = time.time()
        # Simulate query execution
        result = QueryResult(query, QueryStatus.SUCCESS)
        result.execution_time_ms = random.uniform(0.5, 50)
        result.row_count = random.randint(1, 100)
        result.columns = ["id", "name", "value"]
        result.rows = [[i, f"row_{i}", random.randint(0, 1000)] for i in range(min(result.row_count, 20))]
        self._current_result = result
        self._query_history.append(result)
        self._history.append(f"Executed query ({result.duration_str})")
        return result

    def save_query(self, name: str, query: str):
        sq = SavedQuery(name, query, tags=["manual"])
        self._saved_queries.append(sq)
        self._history.append(f"Saved query: {name}")

    def get_table_ddl(self, table: Table) -> str:
        lines = [f"CREATE TABLE {table.name} ("]
        col_defs = []
        for col in table.columns:
            parts = [f"  {col.name} {col.type_str}"]
            if col.primary_key:
                parts.append("PRIMARY KEY")
            if col.auto_increment:
                parts.append("AUTO_INCREMENT")
            if not col.nullable:
                parts.append("NOT NULL")
            if col.default_value:
                parts.append(f"DEFAULT {col.default_value}")
            if col.foreign_key:
                parts.append(f"REFERENCES {col.foreign_key}")
            col_defs.append(" ".join(parts))
        lines.append(",\n".join(col_defs))
        lines.append(");")
        return "\n".join(lines)

    def handle_input(self, key: str):
        key = key.lower()
        if key == "e":
            self._view_mode = "editor"
        elif key == "s":
            self._view_mode = "schema"
        elif key == "r":
            self._view_mode = "results"
        elif key == "h":
            self._view_mode = "history"
        elif key == "f":
            self._view_mode = "favorites"
        elif key == "c":
            self._show_system_tables = not self._show_system_tables

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS DATABASE CLIENT                                    ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Connections
        lines.append("  ── Connections ──")
        for i, conn in enumerate(self._connections):
            sel = "▶" if i == self._active_connection else " "
            lat = f"{conn.latency_ms:.1f}ms" if conn.connected else "disconnected"
            lines.append(f"  {sel} {conn.status_icon} {conn.name}  [{conn.db_type.value}]  {lat}")
        lines.append("")

        # Schema browser
        lines.append(f"  ── Schema ({self.total_tables} tables, {self.total_rows:,} total rows) ──")
        for i, table in enumerate(self._tables):
            sel = "▶" if i == self._selected_table else " "
            lines.append(f"  {sel} 📋 {table.name:<15s} {table.size_str}")
        lines.append("")

        # Selected table detail
        table = self.selected_table
        if table:
            lines.append(f"  ── {table.name} Columns ({table.column_count}) ──")
            for col in table.columns:
                pk = "🔑" if col.primary_key else "  "
                fk = "🔗" if col.foreign_key else "  "
                nn = "!" if not col.nullable else " "
                lines.append(f"  {pk}{fk} {col.name:<20s} {col.type_str:<16s} {nn}")
            lines.append("")

        # Query editor
        lines.append("  ── Query Editor ──")
        if self._current_query:
            q = self._current_query[:70]
            lines.append(f"  │ {q}")
        lines.append("")

        # Results
        if self._current_result and self._current_result.has_data:
            r = self._current_result
            status = "✅" if r.status == QueryStatus.SUCCESS else "❌"
            lines.append(f"  ── Results {status} {r.row_count} rows in {r.duration_str} ──")
            # Header
            header = " | ".join(f"{c:<15s}" for c in r.columns[:5])
            lines.append(f"  {header}")
            lines.append(f"  {'─' * len(header)}")
            # Rows
            for row in r.rows[:8]:
                vals = " | ".join(f"{str(v):<15s}" for v in row[:5])
                lines.append(f"  {vals}")
            if r.row_count > 8:
                lines.append(f"  ... ({r.row_count - 8} more rows)")
            lines.append("")

        # Saved queries
        lines.append("  ── Saved Queries ──")
        for sq in self._saved_queries[:5]:
            fav = "⭐" if sq.favorite else "  "
            lines.append(f"  {fav} {sq.name}  {sq.tag_str}")
        lines.append("")

        lines.append("  [E]ditor [S]chema [R]esults [H]istory [F]avorites [C]System Tables")
        lines.append("  [F5]Execute [Ctrl+S]Save [Ctrl+Z]Undo")
        return lines
