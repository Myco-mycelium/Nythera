"""
Nyrqis OS - Database GUI Client
Query editor, schema browser, and data export for multiple databases.
"""

import time
import random
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any, Tuple


class DatabaseType(Enum):
    POSTGRESQL = "PostgreSQL"
    MYSQL = "MySQL"
    SQLITE = "SQLite"
    MONGODB = "MongoDB"
    REDIS = "Redis"


class QueryStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"
    RUNNING = "running"
    PENDING = "pending"


class ExportFormat(Enum):
    CSV = "csv"
    JSON = "json"
    SQL = "sql"
    EXCEL = "excel"
    XML = "xml"
    MARKDOWN = "markdown"


# ─── ColumnType (enum with lowercase values for backward compat) ───────
class ColumnType(Enum):
    SERIAL = "serial"
    VARCHAR = "varchar"
    INTEGER = "integer"
    BIGINT = "bigint"
    TEXT = "text"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"
    FLOAT = "float"
    DOUBLE = "double"
    DECIMAL = "decimal"
    BLOB = "blob"
    JSON = "json"
    UUID = "uuid"
    DATE = "date"
    TIME = "time"
    BINARY = "binary"
    SMALLINT = "smallint"
    MEDIUMINT = "mediumint"
    ENUM = "enum"
    SET = "set"
    CHAR = "char"
    VARBINARY = "varbinary"


class ConstraintType:
    PRIMARY_KEY = "PRIMARY KEY"
    FOREIGN_KEY = "FOREIGN KEY"
    UNIQUE = "UNIQUE"
    NOT_NULL = "NOT NULL"
    CHECK = "CHECK"
    DEFAULT = "DEFAULT"
    INDEX = "INDEX"
    EXCLUDE = "EXCLUDE"


@dataclass
class Column:
    name: str
    data_type: str = ""
    nullable: bool = True
    primary_key: bool = False
    default_value: str = ""
    is_foreign_key: bool = False
    references_table: str = ""
    references_column: str = ""
    max_length: Optional[int] = None
    foreign_key: Optional[str] = None

    def __post_init__(self):
        # Handle ColumnType enum
        if isinstance(self.data_type, ColumnType):
            self._column_type = self.data_type
            self.data_type = self.data_type.value
        elif isinstance(self.data_type, str) and self.data_type.lower() in [ct.value for ct in ColumnType]:
            self._column_type = ColumnType(self.data_type.lower())
        else:
            self._column_type = None

        # Handle foreign_key as string "table.column"
        if self.foreign_key and not self.is_foreign_key:
            parts = self.foreign_key.split(".")
            if len(parts) == 2:
                self.references_table = parts[0]
                self.references_column = parts[1]
                self.is_foreign_key = True

    @property
    def type_str(self) -> str:
        base = self.data_type
        if self.max_length:
            return f"{base}({self.max_length})"
        return base

    @property
    def icon(self) -> str:
        if self.primary_key:
            return "\U0001f511"  # 🔑
        if self.is_foreign_key or self.references_table:
            return "\U0001f517"  # 🔗
        return ""


@dataclass
class Table:
    name: str
    schema: str = "public"
    columns: List[Column] = field(default_factory=list)
    row_count: int = 0
    size_bytes: int = 0
    engine: str = ""
    last_analyzed: float = 0.0
    indexes: List[str] = field(default_factory=list)
    estimated_size: str = ""

    def __post_init__(self):
        # Handle Table("users", [Column(...)]) — list ends up in schema field
        if isinstance(self.schema, list):
            self.columns = self.schema
            self.schema = "public"

    @property
    def size_display(self) -> str:
        if self.estimated_size:
            return self.estimated_size
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MB"

    @property
    def column_count(self) -> int:
        return len(self.columns)

    @property
    def primary_keys(self) -> List[str]:
        return [c.name for c in self.columns if c.primary_key]

    @property
    def size_str(self) -> str:
        return f"{self.row_count:,} rows ({self.size_display})"


@dataclass
class QueryResult:
    query: str
    status: QueryStatus = QueryStatus.PENDING
    columns: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    error_message: str = ""
    affected_rows: int = 0

    @property
    def has_data(self) -> bool:
        return self.row_count > 0 and len(self.rows) > 0

    @property
    def is_select(self) -> bool:
        return self.query.strip().upper().startswith("SELECT")

    @property
    def status_icon(self) -> str:
        icons = {
            QueryStatus.SUCCESS: "✅",
            QueryStatus.ERROR: "❌",
            QueryStatus.RUNNING: "🔄",
            QueryStatus.PENDING: "⏳",
        }
        return icons.get(self.status, "?")

    @property
    def duration_str(self) -> str:
        if self.execution_time_ms < 1:
            return f"{self.execution_time_ms * 1000:.0f} \u00b5s"
        return f"{self.execution_time_ms:.1f} ms"


@dataclass
class SavedQuery:
    name: str
    query: str
    database: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: float = 0.0
    last_run: float = 0.0
    run_count: int = 0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()


@dataclass
class Index:
    name: str = ""
    table_name: str = ""
    columns: List[str] = field(default_factory=list)
    unique: bool = False

    @property
    def type_str(self) -> str:
        if self.unique:
            return "UNIQUE INDEX"
        return "INDEX"


@dataclass
class Connection:
    name: str = ""
    db_type: Optional[DatabaseType] = None
    host: str = "localhost"
    port: int = 5432
    database: str = ""
    connected: bool = False

    @property
    def status_icon(self) -> str:
        return "\U0001f7e2" if self.connected else "\U0001f534"  # 🟢 or 🔴


# ─── DBClient (backward-compat wrapper around DatabaseClient) ──────────

@dataclass
class DBClient:
    """Backward-compatible DBClient that wraps the internal DatabaseClient logic."""

    _connections: List[Connection] = field(default_factory=list)
    _tables: List[Table] = field(default_factory=list)
    _saved_queries: List[SavedQuery] = field(default_factory=list)
    _history: List[QueryResult] = field(default_factory=list)
    _query_history: List[QueryResult] = field(default_factory=list)
    _active_connection: int = 0
    _selected_table: int = 0
    _current_query: str = ""
    view_mode: str = "tables"
    selected_index: int = 0

    def __post_init__(self):
        if not self._connections:
            self._create_sample_data()

    def _create_sample_data(self):
        self._connections = [
            Connection("Nyrqis Production", DatabaseType.POSTGRESQL, port=5432,
                       database="nyrqis_prod", connected=True),
            Connection("Analytics DB", DatabaseType.POSTGRESQL, port=5432,
                       database="analytics", connected=False),
            Connection("Local SQLite", DatabaseType.SQLITE, host="", port=0,
                       database="/var/lib/nyrqis/local.db", connected=True),
            Connection("Cache Store", DatabaseType.REDIS, port=6379,
                       database="0", connected=True),
            Connection("User Sessions", DatabaseType.MONGODB, port=27017,
                       database="sessions", connected=False),
        ]

        self._tables = [
            Table("users", columns=[
                Column("id", ColumnType.BIGINT, False, True),
                Column("username", ColumnType.VARCHAR, False, max_length=64),
                Column("email", ColumnType.VARCHAR, False, max_length=255),
                Column("password_hash", ColumnType.TEXT, False),
                Column("created_at", ColumnType.TIMESTAMP, False),
                Column("last_login", ColumnType.TIMESTAMP, True),
                Column("is_active", ColumnType.BOOLEAN, False, default_value="true"),
            ], row_count=125000, size_bytes=15360000, engine="InnoDB",
                indexes=["idx_users_email", "idx_users_username"]),
            Table("sessions", columns=[
                Column("id", ColumnType.UUID, False, True),
                Column("user_id", ColumnType.BIGINT, False, foreign_key="users.id"),
                Column("token", ColumnType.VARCHAR, False, max_length=128),
                Column("ip_address", ColumnType.TEXT, True),
                Column("created_at", ColumnType.TIMESTAMP, False),
                Column("expires_at", ColumnType.TIMESTAMP, False),
            ], row_count=89000, size_bytes=8192000, engine="InnoDB",
                indexes=["idx_sessions_token"]),
            Table("compositor_state", columns=[
                Column("id", ColumnType.SERIAL, False, True),
                Column("session_id", ColumnType.UUID, False, foreign_key="sessions.id"),
                Column("window_config", ColumnType.JSON, True),
                Column("theme", ColumnType.VARCHAR, False, default_value="'dark'"),
                Column("updated_at", ColumnType.TIMESTAMP, False),
            ], row_count=45000, size_bytes=2048000, engine="InnoDB"),
            Table("gpu_buffers", columns=[
                Column("id", ColumnType.SERIAL, False, True),
                Column("device_id", ColumnType.INTEGER, False),
                Column("buffer_size", ColumnType.BIGINT, False),
                Column("format", ColumnType.VARCHAR, False),
                Column("allocated_at", ColumnType.TIMESTAMP, False),
            ], row_count=25000, size_bytes=4096000, engine="InnoDB"),
            Table("audit_log", columns=[
                Column("id", ColumnType.BIGINT, False, True),
                Column("user_id", ColumnType.BIGINT, True, foreign_key="users.id"),
                Column("action", ColumnType.VARCHAR, False, max_length=64),
                Column("details", ColumnType.JSON, True),
                Column("created_at", ColumnType.TIMESTAMP, False),
            ], row_count=500000, size_bytes=102400000, engine="InnoDB"),
        ]

        self._saved_queries = [
            SavedQuery(name="Active Users", query="SELECT id, username, email FROM users WHERE is_active = true ORDER BY last_login DESC LIMIT 100;",
                       database="nyrqis_prod"),
            SavedQuery(name="Session Stats", query="SELECT date(created_at), COUNT(*) FROM sessions GROUP BY date(created_at) ORDER BY 1 DESC;",
                       database="nyrqis_prod"),
            SavedQuery(name="GPU Buffer Usage", query="SELECT device_id, SUM(buffer_size) as total, COUNT(*) FROM gpu_buffers WHERE freed_at IS NULL GROUP BY device_id;",
                       database="nyrqis_prod"),
            SavedQuery(name="Audit Trail", query="SELECT al.*, u.username FROM audit_log al LEFT JOIN users u ON al.user_id = u.id ORDER BY al.created_at DESC LIMIT 50;",
                       database="nyrqis_prod"),
        ]

    @property
    def active_connection(self) -> Optional[Connection]:
        if 0 <= self._active_connection < len(self._connections):
            return self._connections[self._active_connection]
        return self._connections[0] if self._connections else None

    @property
    def selected_table(self) -> Optional[Table]:
        if 0 <= self._selected_table < len(self._tables):
            return self._tables[self._selected_table]
        return self._tables[0] if self._tables else None

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
        self._current_query = query
        result = QueryResult(query=query, status=QueryStatus.RUNNING)
        start = time.time()

        try:
            upper = query.strip().upper()
            if upper.startswith("SELECT"):
                count = random.randint(1, 50)
                result.columns = ["id", "created_at", "status", "value"]
                result.rows = [
                    {"id": i, "created_at": f"2026-09-{random.randint(1,4):02d} {random.randint(0,23):02d}:{random.randint(0,59):02d}",
                     "status": random.choice(["active", "pending", "done"]),
                     "value": round(random.uniform(0, 100), 2)}
                    for i in range(count)
                ]
                result.row_count = count
                result.status = QueryStatus.SUCCESS
            else:
                result.status = QueryStatus.SUCCESS
        except Exception as e:
            result.status = QueryStatus.ERROR
            result.error_message = str(e)

        result.execution_time_ms = (time.time() - start) * 1000
        self._history.append(result)
        self._query_history.append(result)
        return result

    def get_table_ddl(self, table) -> str:
        if isinstance(table, Table):
            name = table.name
        elif isinstance(table, str):
            name = table
        else:
            return ""
        lines = [f"CREATE TABLE {name} ("]
        cols = []
        for t in self._tables:
            if t.name == name:
                for c in t.columns:
                    parts = [f"  {c.name} {c.type_str}"]
                    if not c.nullable:
                        parts.append("NOT NULL")
                    if c.primary_key:
                        parts.append("PRIMARY KEY")
                    if c.default_value:
                        parts.append(f"DEFAULT {c.default_value}")
                    cols.append(" ".join(parts))
                break
        lines.extend(cols)
        lines.append(");")
        return "\n".join(lines)

    def save_query(self, name: str, query: str) -> SavedQuery:
        sq = SavedQuery(name=name, query=query)
        self._saved_queries.append(sq)
        return sq

    def render(self) -> List[str]:
        lines = []
        lines.append("=" * 60)
        lines.append("DATABASE CLIENT")
        lines.append("=" * 60)
        conn = self.active_connection
        if conn:
            lines.append(f"Connected: {conn.name} ({conn.db_type.value if conn.db_type else '?'})")
        lines.append(f"Tables: {self.total_tables} | Rows: {self.total_rows:,}")
        lines.append("-" * 60)
        for t in self._tables:
            lines.append(f"  {t.name}: {t.row_count:,} rows")
        return lines


# ─── Legacy DatabaseClient (kept for backward compat) ──────────────────

class DatabaseClient:
    def __init__(self):
        self.connections: List[Dict] = []
        self.current_connection: Optional[Dict] = None
        self.tables: List[Table] = []
        self.query_history: List[QueryResult] = []
        self.saved_queries: List[SavedQuery] = []
        self.current_query: str = ""
        self.query_result: Optional[QueryResult] = None
        self.auto_commit: bool = True
        self.max_rows: int = 1000
        self._create_sample_data()

    def _create_sample_data(self):
        self.connections = [
            {"name": "Nyrqis Production", "type": DatabaseType.POSTGRESQL,
             "host": "localhost", "port": 5432, "database": "nyrqis_prod",
             "user": "nyrqis", "connected": True},
            {"name": "Analytics DB", "type": DatabaseType.POSTGRESQL,
             "host": "10.0.1.50", "port": 5432, "database": "analytics",
             "user": "analyst", "connected": False},
            {"name": "Local SQLite", "type": DatabaseType.SQLITE,
             "host": "", "port": 0, "database": "/var/lib/nyrqis/local.db",
             "user": "", "connected": True},
            {"name": "Cache Store", "type": DatabaseType.REDIS,
             "host": "localhost", "port": 6379, "database": "0",
             "user": "", "connected": True},
            {"name": "User Sessions", "type": DatabaseType.MONGODB,
             "host": "localhost", "port": 27017, "database": "sessions",
             "user": "admin", "connected": False},
        ]
        self.current_connection = self.connections[0]
        self.tables = [
            Table("users", columns=[
                Column("id", "bigint", False, True),
                Column("username", "varchar(64)", False),
                Column("email", "varchar(255)", False),
            ], row_count=125000, size_bytes=15360000),
        ]
        self.saved_queries = [
            SavedQuery(name="Active Users", query="SELECT id, username FROM users LIMIT 100;"),
        ]

    def execute_query(self, query: str) -> QueryResult:
        result = QueryResult(query=query, status=QueryStatus.SUCCESS, row_count=random.randint(1, 50))
        result.columns = ["id", "value"]
        result.rows = [{"id": i, "value": i * 10} for i in range(result.row_count)]
        self.query_result = result
        self.query_history.append(result)
        return result

    def get_schema(self, table_name: str = "") -> List[Table]:
        if table_name:
            return [t for t in self.tables if t.name == table_name]
        return self.tables

    def export_data(self, format_type: ExportFormat, data: List[Dict], filename: str = "") -> str:
        return f"[Export as {format_type.value}]"

    def save_query(self, name: str, query: str, **kwargs) -> SavedQuery:
        sq = SavedQuery(name=name, query=query, **kwargs)
        self.saved_queries.append(sq)
        return sq
