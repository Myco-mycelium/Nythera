"""
Nyrqis OS - Data Pipeline Builder
Visual data pipeline with nodes, transforms, and output configuration.

Features:
- Visual pipeline canvas with node-based workflow
- Data source nodes (CSV, JSON, database, API, stream)
- Transform nodes (filter, map, join, aggregate, sort, deduplicate)
- Output nodes (file, database, API, dashboard, notification)
- Pipeline execution with progress and logs
- Node connection management
- Pipeline templates and presets
- Data preview and validation
- Scheduling (cron-like)
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any


class NodeType(Enum):
    SOURCE = "source"
    TRANSFORM = "transform"
    OUTPUT = "output"
    FILTER = "filter"
    AGGREGATE = "aggregate"
    JOIN = "join"
    SPLIT = "split"
    MERGE = "merge"
    ENRICH = "enrich"
    VALIDATE = "validate"
    SCHEDULE = "schedule"
    CONDITION = "condition"
    LOOP = "loop"
    ERROR_HANDLER = "error_handler"


class DataSourceType(Enum):
    CSV = "CSV File"
    JSON = "JSON File"
    DATABASE = "Database"
    REST_API = "REST API"
    GRAPHQL = "GraphQL"
    STREAM = "Stream"
    MQTT = "MQTT"
    KAFKA = "Kafka"
    WEBHOOK = "Webhook"
    FILE_WATCH = "File Watch"


class TransformType(Enum):
    FILTER = "Filter"
    MAP = "Map/Transform"
    SORT = "Sort"
    DEDUPLICATE = "Deduplicate"
    AGGREGATE = "Aggregate"
    GROUP_BY = "Group By"
    PIVOT = "Pivot"
    UNPIVOT = "Unpivot"
    JOIN = "Join"
    UNION = "Union"
    SPLIT = "Split Columns"
    RENAME = "Rename Columns"
    TYPE_CAST = "Type Cast"
    FILL_NULL = "Fill Nulls"
    WINDOW = "Window Function"
    SAMPLE = "Sample"
    FLATTEN = "Flatten Nested"
    EXTRACT = "Extract Fields"


class OutputType(Enum):
    CSV_FILE = "CSV File"
    JSON_FILE = "JSON File"
    DATABASE = "Database Table"
    REST_ENDPOINT = "REST API"
    WEBHOOK = "Webhook"
    DASHBOARD = "Dashboard Widget"
    EMAIL = "Email"
    NOTIFICATION = "System Notification"
    LOG = "Log File"
    CACHE = "Redis Cache"


class NodeStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"
    CANCELLED = "cancelled"


class PipelineStatus(Enum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    SCHEDULED = "scheduled"


class JoinType(Enum):
    INNER = "INNER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    FULL = "FULL OUTER"
    CROSS = "CROSS"


class AggFunction(Enum):
    COUNT = "COUNT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"
    MEDIAN = "MEDIAN"
    DISTINCT = "DISTINCT"
    FIRST = "FIRST"
    LAST = "LAST"


class ScheduleFreq(Enum):
    ONCE = "once"
    MINUTE = "every_minute"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CRON = "cron"


NODE_TYPE_ICONS = {
    NodeType.SOURCE: "📥", NodeType.TRANSFORM: "🔄",
    NodeType.OUTPUT: "📤", NodeType.FILTER: "🔍",
    NodeType.AGGREGATE: "📊", NodeType.JOIN: "🔗",
    NodeType.SPLIT: "✂️", NodeType.MERGE: "🔀",
    NodeType.ENRICH: "➕", NodeType.VALIDATE: "✅",
    NodeType.SCHEDULE: "⏰", NodeType.CONDITION: "❓",
    NodeType.LOOP: "🔁", NodeType.ERROR_HANDLER: "🛡️",
}

STATUS_ICONS = {
    NodeStatus.IDLE: "⏸", NodeStatus.RUNNING: "🔄",
    NodeStatus.COMPLETED: "✅", NodeStatus.FAILED: "❌",
    NodeStatus.WAITING: "⏳", NodeStatus.CANCELLED: "🚫",
}

PIPELINE_STATUS_ICONS = {
    PipelineStatus.DRAFT: "📝", PipelineStatus.RUNNING: "🔄",
    PipelineStatus.COMPLETED: "✅", PipelineStatus.FAILED: "❌",
    PipelineStatus.PAUSED: "⏸", PipelineStatus.SCHEDULED: "⏰",
}


@dataclass
class NodePort:
    name: str = ""
    data_type: str = "any"
    connected: bool = False
    connected_to: str = ""

    @property
    def display(self) -> str:
        conn = " → " + self.connected_to if self.connected else ""
        return f"{self.name} ({self.data_type}){conn}"


@dataclass
class PipelineNode:
    id: int = 0
    name: str = ""
    node_type: NodeType = NodeType.TRANSFORM
    x: int = 0
    y: int = 0
    status: NodeStatus = NodeStatus.IDLE
    config: Dict[str, Any] = field(default_factory=dict)
    input_ports: List[NodePort] = field(default_factory=list)
    output_ports: List[NodePort] = field(default_factory=list)
    rows_processed: int = 0
    processing_time_ms: int = 0
    error_message: str = ""
    description: str = ""

    @property
    def type_icon(self) -> str:
        return NODE_TYPE_ICONS.get(self.node_type, "❓")

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def display(self) -> str:
        return f"{self.type_icon} {self.name} [{self.status.value}]"

    @property
    def rows_str(self) -> str:
        if self.rows_processed == 0:
            return "—"
        if self.rows_processed >= 1_000_000:
            return f"{self.rows_processed / 1_000_000:.1f}M rows"
        elif self.rows_processed >= 1_000:
            return f"{self.rows_processed / 1_000:.1f}K rows"
        return f"{self.rows_processed} rows"

    @property
    def time_str(self) -> str:
        if self.processing_time_ms == 0:
            return "—"
        if self.processing_time_ms >= 1000:
            return f"{self.processing_time_ms / 1000:.1f}s"
        return f"{self.processing_time_ms}ms"


@dataclass
class NodeConnection:
    from_node: int = 0
    from_port: str = ""
    to_node: int = 0
    to_port: str = ""
    label: str = ""

    @property
    def display(self) -> str:
        return f"Node{self.from_node}:{self.from_port} → Node{self.to_node}:{self.to_port}"


@dataclass
class PipelineLog:
    timestamp: float = 0.0
    node_name: str = ""
    level: str = "info"
    message: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def icon(self) -> str:
        icons = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "success": "✅"}
        return icons.get(self.level, "❓")


@dataclass
class DataPreview:
    columns: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    row_count: int = 0
    schema: List[Dict[str, str]] = field(default_factory=list)

    @property
    def col_count(self) -> int:
        return len(self.columns)

    @property
    def summary(self) -> str:
        return f"{self.row_count} rows × {self.col_count} columns"


@dataclass
class PipelineSchedule:
    frequency: ScheduleFreq = ScheduleFreq.DAILY
    cron_expr: str = ""
    enabled: bool = True
    next_run: float = 0.0
    last_run: float = 0.0
    run_count: int = 0

    @property
    def freq_label(self) -> str:
        labels = {
            ScheduleFreq.ONCE: "Once", ScheduleFreq.MINUTE: "Every Minute",
            ScheduleFreq.HOURLY: "Hourly", ScheduleFreq.DAILY: "Daily",
            ScheduleFreq.WEEKLY: "Weekly", ScheduleFreq.MONTHLY: "Monthly",
            ScheduleFreq.CRON: f"Cron: {self.cron_expr}",
        }
        return labels.get(self.frequency, self.frequency.value)

    @property
    def next_run_str(self) -> str:
        if self.next_run == 0:
            return "Not scheduled"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.next_run))


@dataclass
class PipelineTemplate:
    name: str = ""
    description: str = ""
    category: str = ""
    node_count: int = 0
    use_count: int = 0

    @property
    def icon(self) -> str:
        icons = {"ETL": "🔄", "Analytics": "📊", "Streaming": "📡",
                 "Migration": "🚚", "Testing": "🧪", "Integration": "🔗"}
        return icons.get(self.category, "📦")


@dataclass
class Pipeline:
    id: int = 0
    name: str = ""
    description: str = ""
    status: PipelineStatus = PipelineStatus.DRAFT
    nodes: List[PipelineNode] = field(default_factory=list)
    connections: List[NodeConnection] = field(default_factory=list)
    logs: List[PipelineLog] = field(default_factory=list)
    schedule: Optional[PipelineSchedule] = None
    created: float = 0.0
    modified: float = 0.0
    last_run: float = 0.0
    run_count: int = 0
    total_rows: int = 0
    error_count: int = 0

    @property
    def status_icon(self) -> str:
        return PIPELINE_STATUS_ICONS.get(self.status, "❓")

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def modified_str(self) -> str:
        if self.modified == 0:
            return "N/A"
        delta = time.time() - self.modified
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        elif delta < 86400:
            return f"{delta / 3600:.1f}h ago"
        return f"{delta / 86400:.0f}d ago"

    @property
    def last_run_str(self) -> str:
        if self.last_run == 0:
            return "Never"
        delta = time.time() - self.last_run
        if delta < 3600:
            return f"{delta / 60:.0f}m ago"
        return f"{delta / 3600:.1f}h ago"

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.name} ({self.node_count} nodes)"


class DataPipelineManager:
    def __init__(self):
        self.pipelines: List[Pipeline] = []
        self.templates: List[PipelineTemplate] = []
        self._selected_pipeline: int = 0
        self._selected_node: int = 0
        self._view_mode: str = "pipelines"
        self._pipeline_counter: int = 0
        self._node_counter: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.pipelines = [
            Pipeline(
                id=1, name="Sales ETL Pipeline",
                description="Extract sales data from CSV, transform, load to database",
                status=PipelineStatus.COMPLETED,
                nodes=[
                    PipelineNode(1, "Sales CSV", NodeType.SOURCE, 100, 200,
                                 NodeStatus.COMPLETED, {"path": "/data/sales_2026.csv"},
                                 output_ports=[NodePort("output", "dataframe")],
                                 rows_processed=50000, processing_time_ms=320),
                    PipelineNode(2, "Filter Q4", NodeType.FILTER, 350, 200,
                                 NodeStatus.COMPLETED,
                                 {"column": "quarter", "op": "equals", "value": "Q4"},
                                 input_ports=[NodePort("input", "dataframe")],
                                 output_ports=[NodePort("output", "dataframe")],
                                 rows_processed=12500, processing_time_ms=45),
                    PipelineNode(3, "Aggregate by Region", NodeType.AGGREGATE, 600, 200,
                                 NodeStatus.COMPLETED,
                                 {"group_by": ["region"], "agg": {"revenue": "sum", "orders": "count"}},
                                 rows_processed=8, processing_time_ms=120),
                    PipelineNode(4, "Validate", NodeType.VALIDATE, 850, 200,
                                 NodeStatus.COMPLETED,
                                 {"rules": [{"col": "revenue", "min": 0}]},
                                 rows_processed=8, processing_time_ms=10),
                    PipelineNode(5, "PostgreSQL", NodeType.OUTPUT, 1100, 200,
                                 NodeStatus.COMPLETED,
                                 {"table": "q4_sales_summary", "mode": "replace"},
                                 rows_processed=8, processing_time_ms=250),
                ],
                connections=[
                    NodeConnection(1, "output", 2, "input"),
                    NodeConnection(2, "output", 3, "input"),
                    NodeConnection(3, "output", 4, "input"),
                    NodeConnection(4, "output", 5, "input"),
                ],
                created=now - 86400 * 7, modified=now - 86400,
                last_run=now - 3600, run_count=28, total_rows=50000,
            ),
            Pipeline(
                id=2, name="User Analytics Stream",
                description="Real-time user event processing and analytics",
                status=PipelineStatus.RUNNING,
                nodes=[
                    PipelineNode(6, "Kafka Events", NodeType.SOURCE, 100, 200,
                                 NodeStatus.COMPLETED,
                                 {"topic": "user-events", "group": "analytics"},
                                 output_ports=[NodePort("output", "stream")],
                                 rows_processed=1250000, processing_time_ms=0),
                    PipelineNode(7, "Parse JSON", NodeType.TRANSFORM, 350, 200,
                                 NodeStatus.RUNNING,
                                 {"schema": "event_schema.json"},
                                 rows_processed=1248000, processing_time_ms=0),
                    PipelineNode(8, "Filter Bots", NodeType.FILTER, 600, 200,
                                 NodeStatus.COMPLETED,
                                 {"column": "is_bot", "op": "equals", "value": False},
                                 rows_processed=1180000, processing_time_ms=0),
                    PipelineNode(9, "Session Window", NodeType.AGGREGATE, 850, 200,
                                 NodeStatus.RUNNING,
                                 {"window": "30min", "group_by": "user_id"},
                                 rows_processed=850000, processing_time_ms=0),
                    PipelineNode(10, "ClickHouse", NodeType.OUTPUT, 1100, 200,
                                 NodeStatus.RUNNING,
                                 {"table": "user_sessions", "mode": "append"},
                                 rows_processed=850000, processing_time_ms=0),
                ],
                connections=[
                    NodeConnection(6, "output", 7, "input"),
                    NodeConnection(7, "output", 8, "input"),
                    NodeConnection(8, "output", 9, "input"),
                    NodeConnection(9, "output", 10, "input"),
                ],
                created=now - 86400 * 3, modified=now - 600,
                last_run=now - 600, run_count=0, total_rows=1250000,
            ),
            Pipeline(
                id=3, name="Backup to S3",
                description="Daily database backup with compression and S3 upload",
                status=PipelineStatus.SCHEDULED,
                nodes=[
                    PipelineNode(11, "PostgreSQL Dump", NodeType.SOURCE, 100, 200,
                                 NodeStatus.IDLE,
                                 {"command": "pg_dump", "database": "nyrqis"},
                                 output_ports=[NodePort("output", "file")]),
                    PipelineNode(12, "Compress", NodeType.TRANSFORM, 350, 200,
                                 NodeStatus.IDLE,
                                 {"method": "zstd", "level": 3}),
                    PipelineNode(13, "S3 Upload", NodeType.OUTPUT, 600, 200,
                                 NodeStatus.IDLE,
                                 {"bucket": "nyrqis-backups", "prefix": "daily/"}),
                    PipelineNode(14, "Notify", NodeType.OUTPUT, 850, 200,
                                 NodeStatus.IDLE,
                                 {"channel": "slack", "message": "Backup complete"}),
                ],
                connections=[
                    NodeConnection(11, "output", 12, "input"),
                    NodeConnection(12, "output", 13, "input"),
                    NodeConnection(13, "output", 14, "input"),
                ],
                schedule=PipelineSchedule(ScheduleFreq.DAILY, enabled=True,
                                          next_run=now + 86400),
                created=now - 86400 * 30, modified=now - 86400 * 7,
                last_run=now - 86400, run_count=30, total_rows=0,
            ),
            Pipeline(
                id=4, name="ML Feature Engineering",
                description="Feature extraction and transformation for ML models",
                status=PipelineStatus.DRAFT,
                nodes=[
                    PipelineNode(15, "Training Data", NodeType.SOURCE, 100, 200,
                                 NodeStatus.IDLE,
                                 {"table": "raw_features", "limit": 1000000}),
                    PipelineNode(16, "Feature Transform", NodeType.TRANSFORM, 350, 200,
                                 NodeStatus.IDLE,
                                 {"operations": ["normalize", "encode", "impute"]}),
                    PipelineNode(17, "Feature Store", NodeType.OUTPUT, 600, 200,
                                 NodeStatus.IDLE,
                                 {"table": "ml_features_v3", "mode": "replace"}),
                ],
                connections=[
                    NodeConnection(15, "output", 16, "input"),
                    NodeConnection(16, "output", 17, "input"),
                ],
                created=now - 3600, modified=now - 3600,
            ),
        ]
        self._pipeline_counter = 5

        self.templates = [
            PipelineTemplate("CSV → Transform → Database", "Basic ETL from CSV to DB",
                             "ETL", 4, 156),
            PipelineTemplate("API → Process → Store", "REST API data ingestion",
                             "ETL", 3, 89),
            PipelineTemplate("Stream → Filter → Dashboard", "Real-time streaming analytics",
                             "Streaming", 5, 42),
            PipelineTemplate("Database Migration", "Schema and data migration between databases",
                             "Migration", 6, 23),
            PipelineTemplate("Daily Report Generator", "Automated daily report pipeline",
                             "Analytics", 4, 67),
            PipelineTemplate("Data Quality Checker", "Validate incoming data against rules",
                             "Testing", 3, 34),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_pipeline(self) -> Optional[Pipeline]:
        if 0 <= self._selected_pipeline < len(self.pipelines):
            return self.pipelines[self._selected_pipeline]
        return None

    def select_pipeline(self, idx: int):
        if 0 <= idx < len(self.pipelines):
            self._selected_pipeline = idx

    def select_node(self, idx: int):
        if 0 <= idx < len(self.pipelines[self._selected_pipeline].nodes):
            self._selected_node = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        self._selected_pipeline = min(self._selected_pipeline + 1, len(self.pipelines) - 1)

    def select_up(self):
        self._selected_pipeline = max(self._selected_pipeline - 1, 0)

    # ─── Pipeline Actions ──────────────────────────────────────────────

    def create_pipeline(self, name: str, description: str = "") -> Pipeline:
        self._pipeline_counter += 1
        p = Pipeline(id=self._pipeline_counter, name=name, description=description,
                     created=time.time(), modified=time.time())
        self.pipelines.append(p)
        return p

    def delete_pipeline(self, idx: int) -> bool:
        if 0 <= idx < len(self.pipelines):
            self.pipelines.pop(idx)
            if self._selected_pipeline >= len(self.pipelines):
                self._selected_pipeline = max(0, len(self.pipelines) - 1)
            return True
        return False

    def duplicate_pipeline(self, idx: int) -> Optional[Pipeline]:
        if 0 <= idx < len(self.pipelines):
            orig = self.pipelines[idx]
            self._pipeline_counter += 1
            copy = Pipeline(
                id=self._pipeline_counter, name=f"{orig.name} (copy)",
                description=orig.description, nodes=list(orig.nodes),
                connections=list(orig.connections),
                created=time.time(), modified=time.time(),
            )
            self.pipelines.insert(idx + 1, copy)
            return copy
        return None

    def run_pipeline(self, idx: int) -> bool:
        if 0 <= idx < len(self.pipelines):
            pipeline = self.pipelines[idx]
            pipeline.status = PipelineStatus.RUNNING
            pipeline.last_run = time.time()
            pipeline.run_count += 1
            for node in pipeline.nodes:
                node.status = NodeStatus.RUNNING
            return True
        return False

    def stop_pipeline(self, idx: int) -> bool:
        if 0 <= idx < len(self.pipelines):
            pipeline = self.pipelines[idx]
            pipeline.status = PipelineStatus.FAILED
            for node in pipeline.nodes:
                if node.status == NodeStatus.RUNNING:
                    node.status = NodeStatus.CANCELLED
            return True
        return False

    def pause_pipeline(self, idx: int) -> bool:
        if 0 <= idx < len(self.pipelines):
            self.pipelines[idx].status = PipelineStatus.PAUSED
            return True
        return False

    def add_node(self, pipeline_idx: int, name: str, node_type: NodeType,
                 x: int = 400, y: int = 200) -> Optional[PipelineNode]:
        if 0 <= pipeline_idx < len(self.pipelines):
            self._node_counter += 1
            node = PipelineNode(self._node_counter, name, node_type, x, y)
            self.pipelines[pipeline_idx].nodes.append(node)
            self.pipelines[pipeline_idx].modified = time.time()
            return node
        return None

    def remove_node(self, pipeline_idx: int, node_idx: int) -> bool:
        if 0 <= pipeline_idx < len(self.pipelines):
            pipeline = self.pipelines[pipeline_idx]
            if 0 <= node_idx < len(pipeline.nodes):
                node = pipeline.nodes[node_idx]
                pipeline.nodes.pop(node_idx)
                pipeline.connections = [c for c in pipeline.connections
                                        if c.from_node != node.id and c.to_node != node.id]
                pipeline.modified = time.time()
                return True
        return False

    def connect_nodes(self, pipeline_idx: int, from_id: int, from_port: str,
                      to_id: int, to_port: str) -> bool:
        if 0 <= pipeline_idx < len(self.pipelines):
            conn = NodeConnection(from_id, from_port, to_id, to_port)
            self.pipelines[pipeline_idx].connections.append(conn)
            self.pipelines[pipeline_idx].modified = time.time()
            return True
        return False

    def create_from_template(self, template_idx: int, name: str = "") -> Optional[Pipeline]:
        if 0 <= template_idx < len(self.templates):
            template = self.templates[template_idx]
            template.use_count += 1
            return self.create_pipeline(name or template.name, template.description)
        return None

    # ─── Queries ───────────────────────────────────────────────────────

    def get_running_pipelines(self) -> List[Pipeline]:
        return [p for p in self.pipelines if p.status == PipelineStatus.RUNNING]

    def get_scheduled_pipelines(self) -> List[Pipeline]:
        return [p for p in self.pipelines if p.schedule and p.schedule.enabled]

    def search_pipelines(self, query: str) -> List[Pipeline]:
        q = query.lower()
        return [p for p in self.pipelines if q in p.name.lower() or q in p.description.lower()]

    def get_stats(self) -> Dict:
        return {
            "total_pipelines": len(self.pipelines),
            "running": len(self.get_running_pipelines()),
            "scheduled": len(self.get_scheduled_pipelines()),
            "total_nodes": sum(p.node_count for p in self.pipelines),
            "total_connections": sum(len(p.connections) for p in self.pipelines),
            "templates": len(self.templates),
            "total_runs": sum(p.run_count for p in self.pipelines),
        }
