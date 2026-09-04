"""
Nyrqis OS - Dashboard Builder
Visual dashboard creation with drag-and-drop widgets and data sources.

Features:
- Drag-and-drop widget placement on grid layout
- Widget types (chart, table, stat, gauge, text, image, map, list)
- Data source connectors (API, database, CSV, stream)
- Dashboard templates
- Widget configuration (title, refresh, colors, thresholds)
- Dashboard themes and styling
- Multi-page dashboards
- Auto-refresh intervals
- Dashboard sharing and export
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any, Tuple


class WidgetType(Enum):
    LINE_CHART = "line_chart"
    BAR_CHART = "bar_chart"
    PIE_CHART = "pie_chart"
    AREA_CHART = "area_chart"
    GAUGE = "gauge"
    STAT_CARD = "stat_card"
    TABLE = "table"
    TEXT = "text"
    IMAGE = "image"
    MAP = "map"
    LIST = "list"
    HEATMAP = "heatmap"
    SPARKLINE = "sparkline"
    PROGRESS = "progress"
    COUNTER = "counter"
    LOG_VIEWER = "log_viewer"
    TOGGLE = "toggle"
    BUTTON = "button"


class DataSourceType(Enum):
    STATIC = "static"
    REST_API = "rest_api"
    WEBSOCKET = "websocket"
    DATABASE = "database"
    CSV = "csv"
    PROMETHEUS = "prometheus"
    INFLUXDB = "influxdb"
    ELASTICSEARCH = "elasticsearch"
    MQTT = "mqtt"
    PYTHON = "python_script"


class RefreshInterval(Enum):
    OFF = 0
    FIVE_SEC = 5
    TEN_SEC = 10
    THIRTY_SEC = 30
    ONE_MIN = 60
    FIVE_MIN = 300
    FIFTEEN_MIN = 900
    ONE_HOUR = 3600


class AggType(Enum):
    NONE = "none"
    AVG = "avg"
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    LAST = "last"


class ThresholdType(Enum):
    NONE = "none"
    GT = "greater_than"
    LT = "less_than"
    BETWEEN = "between"
    OUTSIDE = "outside"


WIDGET_ICONS = {
    WidgetType.LINE_CHART: "📈", WidgetType.BAR_CHART: "📊",
    WidgetType.PIE_CHART: "🥧", WidgetType.AREA_CHART: "📉",
    WidgetType.GAUGE: "⏱️", WidgetType.STAT_CARD: "🔢",
    WidgetType.TABLE: "📋", WidgetType.TEXT: "📝",
    WidgetType.IMAGE: "🖼️", WidgetType.MAP: "🗺️",
    WidgetType.LIST: "📑", WidgetType.HEATMAP: "🌡️",
    WidgetType.SPARKLINE: "〰️", WidgetType.PROGRESS: "📶",
    WidgetType.COUNTER: "🔢", WidgetType.LOG_VIEWER: "📜",
    WidgetType.TOGGLE: "🔀", WidgetType.BUTTON: "🔘",
}

AGG_ICONS = {
    AggType.NONE: "—", AggType.AVG: "μ",
    AggType.SUM: "Σ", AggType.MIN: "↓",
    AggType.MAX: "↑", AggType.COUNT: "#",
    AggType.LAST: "→",
}


@dataclass
class WidgetPosition:
    col: int = 0
    row: int = 0
    width: int = 4
    height: int = 3

    @property
    def col_end(self) -> int:
        return self.col + self.width

    @property
    def row_end(self) -> int:
        return self.row + self.height

    @property
    def size_str(self) -> str:
        return f"{self.width}×{self.height}"


@dataclass
class Threshold:
    threshold_type: ThresholdType = ThresholdType.NONE
    value: float = 0.0
    value2: float = 0.0
    color: str = "#ff4444"
    label: str = ""

    @property
    def condition_str(self) -> str:
        if self.threshold_type == ThresholdType.GT:
            return f"> {self.value}"
        elif self.threshold_type == ThresholdType.LT:
            return f"< {self.value}"
        elif self.threshold_type == ThresholdType.BETWEEN:
            return f"{self.value} - {self.value2}"
        return ""


@dataclass
class WidgetConfig:
    widget_type: WidgetType = WidgetType.LINE_CHART
    title: str = ""
    data_source_id: int = 0
    query: str = ""
    x_field: str = ""
    y_field: str = ""
    series_field: str = ""
    aggregation: AggType = AggType.NONE
    refresh_interval: RefreshInterval = RefreshInterval.TEN_SEC
    color: str = "#4fc3f7"
    colors: List[str] = field(default_factory=list)
    thresholds: List[Threshold] = field(default_factory=list)
    show_legend: bool = True
    show_grid: bool = True
    show_labels: bool = True
    decimal_places: int = 1
    unit: str = ""
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    text_content: str = ""
    font_size: int = 14
    alignment: str = "left"  # left, center, right
    bg_color: str = ""
    border_radius: int = 8
    padding: int = 12
    custom: Dict[str, Any] = field(default_factory=dict)

    @property
    def type_icon(self) -> str:
        return WIDGET_ICONS.get(self.widget_type, "❓")

    @property
    def refresh_str(self) -> str:
        if self.refresh_interval.value == 0:
            return "Off"
        v = self.refresh_interval.value
        if v < 60:
            return f"{v}s"
        return f"{v // 60}m"


@dataclass
class DashboardWidget:
    id: int = 0
    config: WidgetConfig = field(default_factory=WidgetConfig)
    position: WidgetPosition = field(default_factory=WidgetPosition)
    visible: bool = True
    locked: bool = False
    last_refresh: float = 0.0
    error: str = ""
    value: Any = None
    cached_data: List[Any] = field(default_factory=list)

    @property
    def display(self) -> str:
        vis = "👁️" if self.visible else "🚫"
        return f"{vis} {self.config.type_icon} {self.config.title}"

    @property
    def value_str(self) -> str:
        if self.value is None:
            return "—"
        if isinstance(self.value, float):
            return f"{self.value:.{self.config.decimal_places}f}{self.config.unit}"
        return str(self.value)

    @property
    def last_refresh_str(self) -> str:
        if self.last_refresh == 0:
            return "Never"
        delta = time.time() - self.last_refresh
        if delta < 60:
            return f"{delta:.0f}s ago"
        return f"{delta / 60:.0f}m ago"


@dataclass
class DataSource:
    id: int = 0
    name: str = ""
    source_type: DataSourceType = DataSourceType.STATIC
    url: str = ""
    database: str = ""
    query: str = ""
    auth_type: str = ""  # none, basic, bearer, api_key
    auth_config: Dict[str, str] = field(default_factory=dict)
    refresh_interval: RefreshInterval = RefreshInterval.TEN_SEC
    enabled: bool = True
    last_fetch: float = 0.0
    fetch_count: int = 0
    error_count: int = 0
    schema: List[Dict[str, str]] = field(default_factory=list)
    sample_data: List[Dict] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        if not self.enabled:
            return "⚫"
        if self.error_count > 0:
            return "🟡"
        return "🟢"

    @property
    def type_display(self) -> str:
        return self.source_type.value

    @property
    def last_fetch_str(self) -> str:
        if self.last_fetch == 0:
            return "Never"
        delta = time.time() - self.last_fetch
        if delta < 60:
            return f"{delta:.0f}s ago"
        return f"{delta / 60:.0f}m ago"


@dataclass
class DashboardPage:
    id: int = 0
    name: str = ""
    icon: str = "📊"
    widgets: List[DashboardWidget] = field(default_factory=list)
    cols: int = 12
    row_height: int = 60
    bg_color: str = ""
    is_home: bool = False

    @property
    def widget_count(self) -> int:
        return len(self.widgets)

    @property
    def display(self) -> str:
        home = " ⭐" if self.is_home else ""
        return f"{self.icon} {self.name} ({self.widget_count} widgets){home}"


@dataclass
class DashboardTheme:
    name: str = ""
    bg_color: str = "#1e1e2e"
    card_bg: str = "#2a2a3e"
    text_color: str = "#cdd6f4"
    accent_color: str = "#89b4fa"
    border_color: str = "#45475a"
    success_color: str = "#a6e3a1"
    warning_color: str = "#f9e2af"
    error_color: str = "#f38ba8"
    font_family: str = "Inter, sans-serif"
    border_radius: int = 8

    @property
    def preview(self) -> str:
        return f"BG:{self.bg_color} | Card:{self.card_bg} | Text:{self.text_color}"


@dataclass
class Dashboard:
    id: int = 0
    name: str = ""
    description: str = ""
    pages: List[DashboardPage] = field(default_factory=list)
    theme: DashboardTheme = field(default_factory=DashboardTheme)
    created: float = 0.0
    modified: float = 0.0
    auto_refresh: RefreshInterval = RefreshInterval.TEN_SEC
    fullscreen: bool = False
    grid_snap: bool = True
    show_grid: bool = True
    share_url: str = ""
    is_public: bool = False
    tags: List[str] = field(default_factory=list)
    view_count: int = 0

    @property
    def total_widgets(self) -> int:
        return sum(p.widget_count for p in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

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
    def display(self) -> str:
        pub = " 🌐" if self.is_public else ""
        return f"{self.name}{pub} ({self.total_widgets} widgets, {self.page_count} pages)"


@dataclass
class DashboardTemplate:
    name: str = ""
    description: str = ""
    category: str = ""
    widget_count: int = 0
    page_count: int = 1
    preview_colors: List[str] = field(default_factory=list)
    use_count: int = 0

    @property
    def icon(self) -> str:
        icons = {"Monitoring": "📊", "DevOps": "🔧", "Business": "📈",
                 "IoT": "🌡️", "Analytics": "📉", "Admin": "⚙️"}
        return icons.get(self.category, "📋")

    @property
    def color_bar(self) -> str:
        return " ".join(self.preview_colors[:5]) if self.preview_colors else ""


class DashboardBuilder:
    def __init__(self):
        self.dashboards: List[Dashboard] = []
        self.data_sources: List[DataSource] = []
        self.templates: List[DashboardTemplate] = []
        self._selected_dashboard: int = 0
        self._selected_page: int = 0
        self._selected_widget: int = 0
        self._view_mode: str = "dashboards"
        self._dashboard_counter: int = 0
        self._widget_counter: int = 0
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.data_sources = [
            DataSource(1, "System Metrics", DataSourceType.PROMETHEUS,
                       "http://localhost:9090", "", "nyrqis_*",
                       enabled=True, last_fetch=now - 5, fetch_count=12500,
                       schema=[{"name": "timestamp", "type": "datetime"},
                               {"name": "metric", "type": "string"},
                               {"name": "value", "type": "float"}]),
            DataSource(2, "Application Logs", DataSourceType.ELASTICSEARCH,
                       "http://localhost:9200", "nyrqis-logs-*",
                       enabled=True, last_fetch=now - 2, fetch_count=45000),
            DataSource(3, "Network Stats", DataSourceType.REST_API,
                       "http://localhost:8080/api/network",
                       enabled=True, last_fetch=now - 10, fetch_count=3600),
            DataSource(4, "Database", DataSourceType.DATABASE,
                       "postgresql://localhost:5432/nyrqis",
                       query="SELECT * FROM metrics ORDER BY time DESC LIMIT 1000",
                       enabled=True, last_fetch=now - 30, fetch_count=1800),
            DataSource(5, "Sensor Data", DataSourceType.MQTT,
                       "mqtt://localhost:1883/sensors/#",
                       enabled=True, last_fetch=now - 1, fetch_count=89000),
            DataSource(6, "Static Config", DataSourceType.STATIC,
                       enabled=True, sample_data=[
                           {"label": "CPU", "value": 42},
                           {"label": "RAM", "value": 68},
                           {"label": "Disk", "value": 55},
                       ]),
        ]

        # Dashboard 1: System Overview
        dash1 = Dashboard(
            id=1, name="System Overview", description="Real-time system monitoring",
            created=now - 86400 * 30, modified=now - 600,
            auto_refresh=RefreshInterval.TEN_SEC,
            tags=["system", "monitoring", "realtime"],
        )
        page1 = DashboardPage(1, "Overview", "📊", is_home=True)
        page1.widgets = [
            DashboardWidget(1, WidgetConfig(WidgetType.STAT_CARD, "CPU Usage", 1,
                                            y_field="cpu_percent", unit="%"),
                            WidgetPosition(0, 0, 3, 2), value=42.5),
            DashboardWidget(2, WidgetConfig(WidgetType.STAT_CARD, "Memory Usage", 1,
                                            y_field="mem_percent", unit="%"),
                            WidgetPosition(3, 0, 3, 2), value=68.2),
            DashboardWidget(3, WidgetConfig(WidgetType.STAT_CARD, "Disk Usage", 1,
                                            y_field="disk_percent", unit="%"),
                            WidgetPosition(6, 0, 3, 2), value=55.1),
            DashboardWidget(4, WidgetConfig(WidgetType.STAT_CARD, "Network", 1,
                                            y_field="net_rx", unit=" MB/s"),
                            WidgetPosition(9, 0, 3, 2), value=12.8),
            DashboardWidget(5, WidgetConfig(WidgetType.LINE_CHART, "CPU History", 1,
                                            x_field="time", y_field="cpu",
                                            colors=["#4fc3f7", "#f48fb1"]),
                            WidgetPosition(0, 2, 6, 4)),
            DashboardWidget(6, WidgetConfig(WidgetType.AREA_CHART, "Memory History", 1,
                                            x_field="time", y_field="mem",
                                            color="#a6e3a1"),
                            WidgetPosition(6, 2, 6, 4)),
            DashboardWidget(7, WidgetConfig(WidgetType.GAUGE, "Load Average", 1,
                                            y_field="load_1m", max_value=16,
                                            thresholds=[Threshold(ThresholdType.GT, 12, 0, "#f38ba8", "High")]),
                            WidgetPosition(0, 6, 4, 3)),
            DashboardWidget(8, WidgetConfig(WidgetType.TABLE, "Top Processes", 1,
                                            x_field="name"),
                            WidgetPosition(4, 6, 8, 3)),
        ]
        dash1.pages.append(page1)

        # Dashboard 2: Network
        page_net = DashboardPage(2, "Network", "🌐")
        page_net.widgets = [
            DashboardWidget(9, WidgetConfig(WidgetType.LINE_CHART, "Bandwidth", 3,
                                            colors=["#4fc3f7", "#f48fb1"]),
                            WidgetPosition(0, 0, 8, 4)),
            DashboardWidget(10, WidgetConfig(WidgetType.PIE_CHART, "Protocol Distribution", 3),
                            WidgetPosition(8, 0, 4, 4)),
            DashboardWidget(11, WidgetConfig(WidgetType.TABLE, "Active Connections", 3),
                            WidgetPosition(0, 4, 12, 3)),
        ]
        dash1.pages.append(page_net)
        self.dashboards.append(dash1)

        # Dashboard 2: Application
        dash2 = Dashboard(
            id=2, name="Application Metrics", description="Nyrqis app monitoring",
            created=now - 86400 * 14, modified=now - 3600,
            tags=["app", "metrics"],
        )
        page_app = DashboardPage(3, "App Overview", "📱", is_home=True)
        page_app.widgets = [
            DashboardWidget(12, WidgetConfig(WidgetType.COUNTER, "Active Users", 2,
                                             unit=" users"),
                            WidgetPosition(0, 0, 4, 2), value=1247),
            DashboardWidget(13, WidgetConfig(WidgetType.SPARKLINE, "Requests/sec", 2),
                            WidgetPosition(4, 0, 4, 2), value=3420),
            DashboardWidget(14, WidgetConfig(WidgetType.STAT_CARD, "Error Rate", 2,
                                             unit="%"),
                            WidgetPosition(8, 0, 4, 2), value=0.12),
            DashboardWidget(15, WidgetConfig(WidgetType.BAR_CHART, "API Latency", 2),
                            WidgetPosition(0, 2, 12, 4)),
            DashboardWidget(16, WidgetConfig(WidgetType.LOG_VIEWER, "Recent Errors", 2),
                            WidgetPosition(0, 6, 12, 3)),
        ]
        dash2.pages.append(page_app)
        self.dashboards.append(dash2)

        # Dashboard 3: IoT
        dash3 = Dashboard(
            id=3, name="Smart Home", description="IoT sensor dashboard",
            created=now - 86400 * 7, modified=now - 1800,
            tags=["iot", "sensors"],
        )
        page_iot = DashboardPage(4, "Sensors", "🌡️", is_home=True)
        page_iot.widgets = [
            DashboardWidget(17, WidgetConfig(WidgetType.GAUGE, "Temperature", 5,
                                             unit="°C", max_value=50,
                                             thresholds=[Threshold(ThresholdType.GT, 35, 0, "#f38ba8", "Hot")]),
                            WidgetPosition(0, 0, 4, 3), value=22.5),
            DashboardWidget(18, WidgetConfig(WidgetType.GAUGE, "Humidity", 5,
                                             unit="%", max_value=100),
                            WidgetPosition(4, 0, 4, 3), value=45.0),
            DashboardWidget(19, WidgetConfig(WidgetType.GAUGE, "CO2", 5,
                                             unit=" ppm", max_value=2000,
                                             thresholds=[Threshold(ThresholdType.GT, 1000, 0, "#f9e2af", "Elevated")]),
                            WidgetPosition(8, 0, 4, 3), value=420),
            DashboardWidget(20, WidgetConfig(WidgetType.AREA_CHART, "Temperature History", 5,
                                             color="#f48fb1"),
                            WidgetPosition(0, 3, 12, 4)),
        ]
        dash3.pages.append(page_iot)
        self.dashboards.append(dash3)

        self.templates = [
            DashboardTemplate("System Monitor", "CPU, RAM, disk, network",
                              "Monitoring", 8, 2, ["#4fc3f7", "#a6e3a1", "#f9e2af"], 89),
            DashboardTemplate("DevOps Dashboard", "CI/CD, deployments, alerts",
                              "DevOps", 10, 2, ["#89b4fa", "#f38ba8", "#a6e3a1"], 45),
            DashboardTemplate("Business KPIs", "Revenue, users, growth",
                              "Business", 6, 1, ["#cba6f7", "#f9e2af", "#a6e3a1"], 67),
            DashboardTemplate("IoT Monitoring", "Sensors, temperature, humidity",
                              "IoT", 8, 2, ["#f48fb1", "#4fc3f7", "#a6e3a1"], 23),
            DashboardTemplate("Network Monitor", "Bandwidth, connections, latency",
                              "Monitoring", 6, 1, ["#4fc3f7", "#cba6f7", "#f9e2af"], 34),
            DashboardTemplate("Application Health", "Uptime, errors, performance",
                              "Monitoring", 8, 2, ["#a6e3a1", "#f38ba8", "#f9e2af"], 56),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_dashboard(self) -> Optional[Dashboard]:
        if 0 <= self._selected_dashboard < len(self.dashboards):
            return self.dashboards[self._selected_dashboard]
        return None

    def select_dashboard(self, idx: int):
        if 0 <= idx < len(self.dashboards):
            self._selected_dashboard = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        self._selected_dashboard = min(self._selected_dashboard + 1, len(self.dashboards) - 1)

    def select_up(self):
        self._selected_dashboard = max(self._selected_dashboard - 1, 0)

    # ─── Dashboard Actions ─────────────────────────────────────────────

    def create_dashboard(self, name: str, description: str = "") -> Dashboard:
        self._dashboard_counter += 1
        d = Dashboard(id=self._dashboard_counter, name=name, description=description,
                      created=time.time(), modified=time.time())
        d.pages.append(DashboardPage(name="Page 1", is_home=True))
        self.dashboards.append(d)
        return d

    def delete_dashboard(self, idx: int) -> bool:
        if 0 <= idx < len(self.dashboards):
            self.dashboards.pop(idx)
            if self._selected_dashboard >= len(self.dashboards):
                self._selected_dashboard = max(0, len(self.dashboards) - 1)
            return True
        return False

    def duplicate_dashboard(self, idx: int) -> Optional[Dashboard]:
        if 0 <= idx < len(self.dashboards):
            orig = self.dashboards[idx]
            self._dashboard_counter += 1
            copy = Dashboard(id=self._dashboard_counter, name=f"{orig.name} (copy)",
                             description=orig.description, pages=list(orig.pages),
                             theme=orig.theme, created=time.time(), modified=time.time())
            self.dashboards.insert(idx + 1, copy)
            return copy
        return None

    def add_page(self, dashboard_idx: int, name: str) -> bool:
        if 0 <= dashboard_idx < len(self.dashboards):
            page = DashboardPage(name=name)
            self.dashboards[dashboard_idx].pages.append(page)
            self.dashboards[dashboard_idx].modified = time.time()
            return True
        return False

    def add_widget(self, dashboard_idx: int, page_idx: int,
                   widget_type: WidgetType, title: str) -> Optional[DashboardWidget]:
        if 0 <= dashboard_idx < len(self.dashboards):
            dash = self.dashboards[dashboard_idx]
            if 0 <= page_idx < len(dash.pages):
                self._widget_counter += 1
                config = WidgetConfig(widget_type=widget_type, title=title)
                pos = WidgetPosition(0, 0, 4, 3)
                widget = DashboardWidget(self._widget_counter, config, pos)
                dash.pages[page_idx].widgets.append(widget)
                dash.modified = time.time()
                return widget
        return None

    def remove_widget(self, dashboard_idx: int, page_idx: int, widget_idx: int) -> bool:
        if 0 <= dashboard_idx < len(self.dashboards):
            dash = self.dashboards[dashboard_idx]
            if 0 <= page_idx < len(dash.pages):
                page = dash.pages[page_idx]
                if 0 <= widget_idx < len(page.widgets):
                    page.widgets.pop(widget_idx)
                    dash.modified = time.time()
                    return True
        return False

    def create_from_template(self, template_idx: int, name: str = "") -> Optional[Dashboard]:
        if 0 <= template_idx < len(self.templates):
            template = self.templates[template_idx]
            template.use_count += 1
            return self.create_dashboard(name or template.name, template.description)
        return None

    # ─── Data Source Actions ───────────────────────────────────────────

    def add_data_source(self, name: str, source_type: DataSourceType,
                        url: str = "") -> DataSource:
        ds_id = len(self.data_sources) + 1
        ds = DataSource(ds_id, name, source_type, url)
        self.data_sources.append(ds)
        return ds

    def toggle_data_source(self, idx: int) -> bool:
        if 0 <= idx < len(self.data_sources):
            self.data_sources[idx].enabled = not self.data_sources[idx].enabled
            return True
        return False

    # ─── Queries ───────────────────────────────────────────────────────

    def get_public_dashboards(self) -> List[Dashboard]:
        return [d for d in self.dashboards if d.is_public]

    def search_dashboards(self, query: str) -> List[Dashboard]:
        q = query.lower()
        return [d for d in self.dashboards if q in d.name.lower() or q in d.description.lower()]

    def search_data_sources(self, query: str) -> List[DataSource]:
        q = query.lower()
        return [ds for ds in self.data_sources if q in ds.name.lower()]

    def get_stats(self) -> Dict:
        return {
            "dashboards": len(self.dashboards),
            "total_widgets": sum(d.total_widgets for d in self.dashboards),
            "total_pages": sum(d.page_count for d in self.dashboards),
            "data_sources": len(self.data_sources),
            "active_sources": sum(1 for ds in self.data_sources if ds.enabled),
            "templates": len(self.templates),
        }
