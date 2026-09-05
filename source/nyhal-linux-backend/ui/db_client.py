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
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLITE = "sqlite"
    MONGODB = "mongodb"
    REDIS = "redis"


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


@dataclass
class Column:
    name: str
    data_type: str
    nullable: bool = True
    primary_key: bool = False
    default_value: str = ""
    is_foreign_key: bool = False
    references_table: str = ""
    references_column: str = ""


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

    @property
    def size_display(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MB"

    @property
    def column_count(self) -> int:
        return len(self.columns)


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
            Table(name="users", columns=[
                Column("id", "bigint", False, True),
                Column("username", "varchar(64)", False),
                Column("email", "varchar(255)", False),
                Column("password_hash", "text", False),
                Column("created_at", "timestamp", False),
                Column("last_login", "timestamp", True),
                Column("is_active", "boolean", False, False, "true"),
            ], row_count=125000, size_bytes=15360000, engine="InnoDB",
                indexes=["idx_users_email", "idx_users_username"]),
            Table(name="sessions", columns=[
                Column("id", "uuid", False, True),
                Column("user_id", "bigint", False, False, "", True, "users", "id"),
                Column("token", "varchar(128)", False),
                Column("ip_address", "inet", True),
                Column("user_agent", "text", True),
                Column("created_at", "timestamp", False),
                Column("expires_at", "timestamp", False),
            ], row_count=89000, size_bytes=8192000, engine="InnoDB",
                indexes=["idx_sessions_token", "idx_sessions_user"]),
            Table(name="compositor_state", columns=[
                Column("id", "serial", False, True),
                Column("session_id", "uuid", False, False, "", True, "sessions", "id"),
                Column("window_config", "jsonb", True),
                Column("theme", "varchar(32)", False, False, "'dark'"),
                Column("layout", "varchar(16)", False, False, "'tiling'"),
                Column("updated_at", "timestamp", False),
            ], row_count=45000, size_bytes=2048000, engine="InnoDB",
                indexes=["idx_comp_session"]),
            Table(name="gpu_buffers", columns=[
                Column("id", "serial", False, True),
                Column("device_id", "integer", False),
                Column("buffer_size", "bigint", False),
                Column("format", "varchar(16)", False),
                Column("usage_count", "integer", False, False, "0"),
                Column("allocated_at", "timestamp", False),
                Column("freed_at", "timestamp", True),
            ], row_count=25000, size_bytes=4096000, engine="InnoDB",
                indexes=["idx_gpu_device"]),
            Table(name="audit_log", columns=[
                Column("id", "bigserial", False, True),
                Column("user_id", "bigint", True, False, "", True, "users", "id"),
                Column("action", "varchar(64)", False),
                Column("resource", "varchar(255)", False),
                Column("details", "jsonb", True),
                Column("ip_address", "inet", True),
                Column("created_at", "timestamp", False),
            ], row_count=500000, size_bytes=102400000, engine="InnoDB",
                indexes=["idx_audit_user", "idx_audit_action", "idx_audit_time"]),
        ]

        self.saved_queries = [
            SavedQuery(name="Active Users", query="SELECT id, username, email FROM users WHERE is_active = true ORDER BY last_login DESC LIMIT 100;",
                       database="nyrqis_prod", description="Get all active users", tags=["users", "report"]),
            SavedQuery(name="Session Stats", query="SELECT date(created_at), COUNT(*) FROM sessions GROUP BY date(created_at) ORDER BY 1 DESC;",
                       database="nyrqis_prod", description="Daily session counts", tags=["stats"]),
            SavedQuery(name="GPU Buffer Usage", query="SELECT device_id, SUM(buffer_size) as total, COUNT(*) FROM gpu_buffers WHERE freed_at IS NULL GROUP BY device_id;",
                       database="nyrqis_prod", description="Current GPU buffer allocation", tags=["gpu", "monitoring"]),
            SavedQuery(name="Audit Trail", query="SELECT al.*, u.username FROM audit_log al LEFT JOIN users u ON al.user_id = u.id ORDER BY al.created_at DESC LIMIT 50;",
                       database="nyrqis_prod", description="Recent audit entries with usernames", tags=["audit"]),
            SavedQuery(name="Large Tables", query="SELECT tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) FROM pg_stat_user_tables ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;",
                       database="nyrqis_prod", description="Table sizes", tags=["maintenance"]),
        ]

    def execute_query(self, query: str) -> QueryResult:
        self.current_query = query
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
            elif upper.startswith(("INSERT", "UPDATE", "DELETE")):
                result.affected_rows = random.randint(1, 100)
                result.status = QueryStatus.SUCCESS
            elif upper.startswith("CREATE"):
                result.status = QueryStatus.SUCCESS
            elif upper.startswith("DROP"):
                result.status = QueryStatus.SUCCESS
            else:
                result.status = QueryStatus.SUCCESS
        except Exception as e:
            result.status = QueryStatus.ERROR
            result.error_message = str(e)

        result.execution_time_ms = (time.time() - start) * 1000
        self.query_result = result
        self.query_history.append(result)
        return result

    def get_schema(self, table_name: str = "") -> List[Table]:
        if table_name:
            return [t for t in self.tables if t.name == table_name]
        return self.tables

    def get_foreign_keys(self, table_name: str) -> List[Tuple[str, str, str]]:
        table = next((t for t in self.tables if t.name == table_name), None)
        if not table:
            return []
        return [(c.name, c.references_table, c.references_column)
                for c in table.columns if c.is_foreign_key]

    def export_data(self, format_type: ExportFormat, data: List[Dict], filename: str = "") -> str:
        if format_type == ExportFormat.JSON:
            return json.dumps(data[:10], indent=2, default=str)
        elif format_type == ExportFormat.CSV:
            if not data:
                return ""
            headers = list(data[0].keys())
            lines = [",".join(headers)]
            for row in data[:10]:
                lines.append(",".join(str(row.get(h, "")) for h in headers))
            return "\n".join(lines)
        elif format_type == ExportFormat.MARKDOWN:
            if not data:
                return ""
            headers = list(data[0].keys())
            lines = ["| " + " | ".join(headers) + " |"]
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for row in data[:10]:
                lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
            return "\n".join(lines)
        return f"[Export as {format_type.value}]"

    def save_query(self, name: str, query: str, **kwargs) -> SavedQuery:
        sq = SavedQuery(name=name, query=query, **kwargs)
        self.saved_queries.append(sq)
        return sq

    def get_table_stats(self) -> Dict:
        total_tables = len(self.tables)
        total_rows = sum(t.row_count for t in self.tables)
        total_size = sum(t.size_bytes for t in self.tables)
        return {
            "tables": total_tables,
            "total_rows": total_rows,
            "total_size": f"{total_size / (1024 * 1024):.1f} MB",
            "queries_run": len(self.query_history),
            "saved_queries": len(self.saved_queries),
        }


@dataclass
class DBClient:
    name: str = ""
    host: str = ""
    port: int = 5432
    database: str = ""
    connected: bool = False


class Connection:
    pass  # backward compat stub

Index = Table

# ─── Backward-compat exports ────────────────────────────────────────────
from enum import Enum as _Enum

class ColumnType(_Enum):
    SERIAL = "SERIAL"
    VARCHAR = "VARCHAR"
    INTEGER = "INTEGER"
    BIGINT = "BIGINT"
    TEXT = "TEXT"
    BOOLEAN = "BOOLEAN"
    TIMESTAMP = "TIMESTAMP"
    FLOAT = "FLOAT"
    DOUBLE = "DOUBLE"
    DECIMAL = "DECIMAL"
    BLOB = "BLOB"
    JSON = "JSON"
    UUID = "UUID"
    DATE = "DATE"
    TIME = "TIME"
    BINARY = "BINARY"
    SMALLINT = "SMALLINT"
    MEDIUMINT = "MEDIUMINT"
    ENUM = "ENUM"
    SET = "SET"
    CHAR = "CHAR"
    VARBINARY = "VARBINARY"

class ConstraintType:
    PRIMARY_KEY = "PRIMARY KEY"
    FOREIGN_KEY = "FOREIGN KEY"
    UNIQUE = "UNIQUE"
    NOT_NULL = "NOT NULL"
    CHECK = "CHECK"
    DEFAULT = "DEFAULT"
    INDEX = "INDEX"
    EXCLUDE = "EXCLUDE"
