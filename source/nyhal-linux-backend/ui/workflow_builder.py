"""Workflow Automation Builder — drag-and-drop nodes, triggers, actions for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple, Any
import time


class TriggerType(Enum):
    MANUAL = "Manual"
    SCHEDULE = "Schedule"
    FILE_CHANGE = "File Change"
    WEBHOOK = "Webhook"
    KEYBOARD = "Keyboard Shortcut"
    APP_LAUNCH = "App Launch"
    SYSTEM_EVENT = "System Event"
    EMAIL = "Email Received"
    GIT_PUSH = "Git Push"
    SENSOR = "Sensor Value"


class ActionType(Enum):
    SHELL_COMMAND = "Shell Command"
    HTTP_REQUEST = "HTTP Request"
    FILE_OP = "File Operation"
    NOTIFY = "Notification"
    DELAY = "Delay"
    CONDITION = "Condition"
    LOOP = "Loop"
    VARIABLE_SET = "Set Variable"
    VARIABLE_GET = "Get Variable"
    SCRIPT = "Run Script"
    APP_CONTROL = "App Control"
    MEDIA = "Media Control"
    UI_ACTION = "UI Action"
    TRANSFORM = "Transform Data"


class NodeStatus(Enum):
    IDLE = "Idle"
    RUNNING = "Running"
    SUCCESS = "Success"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    WAITING = "Waiting"


class LogLevel(Enum):
    INFO = "Info"
    WARNING = "Warning"
    ERROR = "Error"
    DEBUG = "Debug"


@dataclass
class WorkflowVariable:
    name: str = ""
    value: str = ""
    var_type: str = "string"  # string, number, boolean, array
    secret: bool = False
    description: str = ""

    @property
    def display_value(self) -> str:
        if self.secret:
            return "****"
        return self.value[:50]


@dataclass
class WorkflowNode:
    id: int
    name: str = ""
    node_type: str = "action"  # trigger, action, condition, delay
    action_type: Optional[ActionType] = None
    trigger_type: Optional[TriggerType] = None
    x: float = 0.0
    y: float = 0.0
    config: Dict[str, Any] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.IDLE
    enabled: bool = True
    timeout_s: float = 30.0
    retry_count: int = 0
    last_run: float = 0.0
    last_result: str = ""
    error_msg: str = ""

    @property
    def status_icon(self) -> str:
        icons = {
            NodeStatus.IDLE: "⚪", NodeStatus.RUNNING: "🔄",
            NodeStatus.SUCCESS: "✅", NodeStatus.FAILED: "❌",
            NodeStatus.SKIPPED: "⏭", NodeStatus.WAITING: "⏳",
        }
        return icons.get(self.status, "?")

    @property
    def type_icon(self) -> str:
        if self.node_type == "trigger":
            if self.trigger_type:
                icons = {
                    TriggerType.MANUAL: "👆", TriggerType.SCHEDULE: "⏰",
                    TriggerType.FILE_CHANGE: "📁", TriggerType.WEBHOOK: "🪝",
                    TriggerType.KEYBOARD: "⌨️", TriggerType.APP_LAUNCH: "🚀",
                    TriggerType.SYSTEM_EVENT: "⚡", TriggerType.EMAIL: "📧",
                    TriggerType.GIT_PUSH: "📦", TriggerType.SENSOR: "📡",
                }
                return icons.get(self.trigger_type, "⚡")
            return "⚡"
        elif self.node_type == "condition":
            return "🔀"
        elif self.node_type == "delay":
            return "⏱"
        elif self.action_type:
            icons = {
                ActionType.SHELL_COMMAND: "🖥", ActionType.HTTP_REQUEST: "🌐",
                ActionType.FILE_OP: "📄", ActionType.NOTIFY: "🔔",
                ActionType.DELAY: "⏱", ActionType.CONDITION: "🔀",
                ActionType.LOOP: "🔁", ActionType.VARIABLE_SET: "📝",
                ActionType.SCRIPT: "📜", ActionType.APP_CONTROL: "🎛",
                ActionType.MEDIA: "🎵", ActionType.UI_ACTION: "🖱",
                ActionType.TRANSFORM: "🔧",
            }
            return icons.get(self.action_type, "⚙️")
        return "⚙️"

    @property
    def label(self) -> str:
        if self.node_type == "trigger" and self.trigger_type:
            return self.trigger_type.value
        elif self.action_type:
            return self.action_type.value
        return self.name

    @property
    def config_summary(self) -> str:
        parts = []
        if "command" in self.config:
            parts.append(self.config["command"][:30])
        if "url" in self.config:
            parts.append(self.config["url"][:30])
        if "path" in self.config:
            parts.append(self.config["path"][:30])
        if "message" in self.config:
            parts.append(self.config["message"][:30])
        return " | ".join(parts) if parts else ""


@dataclass
class WorkflowEdge:
    source_id: int
    target_id: int
    label: str = ""
    condition: str = ""
    true_branch: bool = True


@dataclass
class WorkflowRun:
    id: int
    workflow_id: int = 0
    status: NodeStatus = NodeStatus.IDLE
    started_at: float = 0.0
    finished_at: float = 0.0
    triggered_by: str = ""
    nodes_run: int = 0
    nodes_total: int = 0
    errors: int = 0

    @property
    def duration_s(self) -> float:
        if self.started_at > 0 and self.finished_at > 0:
            return self.finished_at - self.started_at
        if self.started_at > 0:
            return time.time() - self.started_at
        return 0

    @property
    def duration_str(self) -> str:
        d = self.duration_s
        if d < 1:
            return f"{d * 1000:.0f}ms"
        elif d < 60:
            return f"{d:.1f}s"
        return f"{d / 60:.1f}m"

    @property
    def progress_bar(self) -> str:
        pct = self.nodes_run / max(1, self.nodes_total)
        filled = int(pct * 20)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class LogEntry:
    timestamp: float = 0.0
    level: LogLevel = LogLevel.INFO
    node_name: str = ""
    message: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def level_icon(self) -> str:
        return {"Info": "ℹ️", "Warning": "⚠️", "Error": "❌", "Debug": "🔍"}.get(self.level.value, "")


@dataclass
class Workflow:
    id: int
    name: str = ""
    description: str = ""
    nodes: List[WorkflowNode] = field(default_factory=list)
    edges: List[WorkflowEdge] = field(default_factory=list)
    variables: List[WorkflowVariable] = field(default_factory=list)
    enabled: bool = True
    created: float = 0.0
    modified: float = 0.0
    runs: List[WorkflowRun] = field(default_factory=list)
    logs: List[LogEntry] = field(default_factory=list)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def trigger_count(self) -> int:
        return sum(1 for n in self.nodes if n.node_type == "trigger")

    @property
    def action_count(self) -> int:
        return sum(1 for n in self.nodes if n.node_type == "action")

    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def last_run_status(self) -> str:
        if not self.runs:
            return "Never"
        return self.runs[-1].status.value


class WorkflowBuilder:
    def __init__(self):
        self._workflows: List[Workflow] = []
        self._selected_workflow: int = 0
        self._selected_node: int = 0
        self._view_mode: str = "editor"
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Workflow 1: Auto Backup
        w1 = Workflow(1, "Auto Backup", "Automated daily backup of critical files",
                      enabled=True, created=now - 86400 * 30, modified=now - 3600)
        w1.nodes = [
            WorkflowNode(0, "Daily Trigger", "trigger", trigger_type=TriggerType.SCHEDULE,
                         config={"cron": "0 2 * * *"}, x=100, y=200),
            WorkflowNode(1, "Check Disk Space", "action", ActionType.SHELL_COMMAND,
                         config={"command": "df -h / | tail -1 | awk '{print $5}'"}, x=300, y=200),
            WorkflowNode(2, "Space OK?", "condition", config={"condition": "usage < 90%"}, x=500, y=200),
            WorkflowNode(3, "Run Backup", "action", ActionType.SHELL_COMMAND,
                         config={"command": "rsync -avz /home/ /backup/daily/"}, x=700, y=150,
                         status=NodeStatus.SUCCESS, last_run=now - 3600, last_result="12.4GB synced"),
            WorkflowNode(4, "Send Notification", "action", ActionType.NOTIFY,
                         config={"message": "Backup completed successfully"}, x=900, y=150),
            WorkflowNode(5, "Disk Warning", "action", ActionType.NOTIFY,
                         config={"message": "⚠️ Disk space low! Backup skipped."}, x=700, y=300,
                         status=NodeStatus.FAILED, error_msg="Disk usage 92%"),
            WorkflowNode(6, "Log Result", "action", ActionType.SCRIPT,
                         config={"script": "log_backup_result.sh"}, x=900, y=250),
        ]
        w1.edges = [
            WorkflowEdge(0, 1), WorkflowEdge(1, 2), WorkflowEdge(2, 3, "true"),
            WorkflowEdge(2, 5, "false"), WorkflowEdge(3, 4), WorkflowEdge(4, 6),
            WorkflowEdge(5, 6),
        ]
        w1.variables = [
            WorkflowVariable("BACKUP_PATH", "/backup/daily", description="Backup destination"),
            WorkflowVariable("SOURCE_PATH", "/home/", description="Source directory"),
            WorkflowVariable("MAX_SIZE_GB", "500", "number", description="Max backup size"),
        ]
        w1.runs = [
            WorkflowRun(1, 1, NodeStatus.SUCCESS, now - 86400, now - 86400 + 342, "schedule", 7, 7),
            WorkflowRun(2, 1, NodeStatus.SUCCESS, now - 172800, now - 172800 + 298, "schedule", 7, 7),
            WorkflowRun(3, 1, NodeStatus.FAILED, now - 259200, now - 259200 + 15, "manual", 3, 7, 1),
        ]
        w1.logs = [
            LogEntry(now - 3600, LogLevel.INFO, "Run Backup", "Starting rsync..."),
            LogEntry(now - 3500, LogLevel.INFO, "Run Backup", "Synced 12.4GB in 342s"),
            LogEntry(now - 3400, LogLevel.INFO, "Send Notification", "Notification sent"),
        ]
        self._workflows.append(w1)

        # Workflow 2: Deploy Pipeline
        w2 = Workflow(2, "Deploy Pipeline", "Auto-deploy on git push to main",
                      enabled=True, created=now - 86400 * 20, modified=now - 7200)
        w2.nodes = [
            WorkflowNode(0, "Git Push", "trigger", trigger_type=TriggerType.GIT_PUSH,
                         config={"branch": "main", "repo": "nyrqis/nyrqis"}, x=100, y=200),
            WorkflowNode(1, "Run Tests", "action", ActionType.SHELL_COMMAND,
                         config={"command": "pytest tests/ -v"}, x=300, y=200, timeout_s=300),
            WorkflowNode(2, "Tests Pass?", "condition", config={"condition": "exit_code == 0"}, x=500, y=200),
            WorkflowNode(3, "Build", "action", ActionType.SHELL_COMMAND,
                         config={"command": "cargo build --release"}, x=700, y=150),
            WorkflowNode(4, "Deploy Staging", "action", ActionType.HTTP_REQUEST,
                         config={"url": "https://deploy.nyrqis.io/staging", "method": "POST"}, x=900, y=150),
            WorkflowNode(5, "Notify Failure", "action", ActionType.NOTIFY,
                         config={"message": "❌ Deploy failed!"}, x=700, y=350),
        ]
        w2.edges = [
            WorkflowEdge(0, 1), WorkflowEdge(1, 2), WorkflowEdge(2, 3, "true"),
            WorkflowEdge(2, 5, "false"), WorkflowEdge(3, 4),
        ]
        self._workflows.append(w2)

        # Workflow 3: Smart Home
        w3 = Workflow(3, "Smart Lights", "Automate lights based on time and motion",
                      enabled=False, created=now - 86400 * 10, modified=now - 86400)
        w3.nodes = [
            WorkflowNode(0, "Sunset", "trigger", trigger_type=TriggerType.SCHEDULE,
                         config={"cron": "0 * * * *"}, x=100, y=200),
            WorkflowNode(1, "Motion Detected", "trigger", trigger_type=TriggerType.SENSOR,
                         config={"sensor": "hallway_motion"}, x=100, y=300),
            WorkflowNode(2, "Turn On Lights", "action", ActionType.APP_CONTROL,
                         config={"device": "living_room_light", "action": "on"}, x=400, y=250),
            WorkflowNode(3, "Set Brightness", "action", ActionType.APP_CONTROL,
                         config={"device": "living_room_light", "brightness": "80%"}, x=600, y=250),
        ]
        w3.edges = [
            WorkflowEdge(0, 2), WorkflowEdge(1, 2), WorkflowEdge(2, 3),
        ]
        self._workflows.append(w3)

    @property
    def selected_workflow(self) -> Optional[Workflow]:
        if 0 <= self._selected_workflow < len(self._workflows):
            return self._workflows[self._selected_workflow]
        return None

    @property
    def selected_node(self) -> Optional[WorkflowNode]:
        wf = self.selected_workflow
        if wf and 0 <= self._selected_node < len(wf.nodes):
            return wf.nodes[self._selected_node]
        return None

    @property
    def total_workflows(self) -> int:
        return len(self._workflows)

    @property
    def total_runs(self) -> int:
        return sum(w.run_count for w in self._workflows)

    def select_workflow(self, idx: int):
        if 0 <= idx < len(self._workflows):
            self._selected_workflow = idx
            self._selected_node = 0

    def select_node(self, idx: int):
        self._selected_node = idx

    def handle_input(self, key: str):
        key = key.lower()
        if key == "e":
            self._view_mode = "editor"
        elif key == "l":
            self._view_mode = "logs"
        elif key == "r":
            self._view_mode = "runs"

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS WORKFLOW AUTOMATION BUILDER                        ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  Workflows: {self.total_workflows}  Total Runs: {self.total_runs}  View: {self._view_mode}")
        lines.append("")

        # Workflows
        lines.append("  ── Workflows ──")
        for i, wf in enumerate(self._workflows):
            sel = "▶" if i == self._selected_workflow else " "
            en = "🟢" if wf.enabled else "⚪"
            lines.append(f"  {sel} {en} {wf.name:<25s} {wf.node_count} nodes  {wf.run_count} runs  Last: {wf.last_run_status}")
        lines.append("")

        # Selected workflow detail
        wf = self.selected_workflow
        if wf:
            lines.append(f"  ── {wf.name} ──")
            lines.append(f"  {wf.description}  Triggers: {wf.trigger_count}  Actions: {wf.action_count}")
            lines.append("")

            # Node graph
            lines.append("  ── Flow ──")
            for i, node in enumerate(wf.nodes):
                sel = "▶" if i == self._selected_node else " "
                en = "🟢" if node.enabled else "⚪"
                cfg = f"  [{node.config_summary}]" if node.config_summary else ""
                lines.append(f"  {sel} {en} {node.status_icon} {node.type_icon} {node.name:<25s} {node.label}{cfg}")
            lines.append("")

            # Node detail
            node = self.selected_node
            if node:
                lines.append(f"  ── Node: {node.name} ──")
                lines.append(f"  Type: {node.node_type}  Status: {node.status.value}  Timeout: {node.timeout_s:.0f}s  Retries: {node.retry_count}")
                if node.config:
                    for k, v in node.config.items():
                        lines.append(f"  {k}: {str(v)[:60]}")
                if node.error_msg:
                    lines.append(f"  ❌ Error: {node.error_msg}")
                if node.last_result:
                    lines.append(f"  Result: {node.last_result}")
                lines.append("")

            # Variables
            if wf.variables:
                lines.append("  ── Variables ──")
                for v in wf.variables:
                    lines.append(f"  📝 {v.name} = {v.display_value}  ({v.var_type}) {v.description}")
                lines.append("")

            # Recent runs
            if wf.runs:
                lines.append("  ── Recent Runs ──")
                for run in wf.runs[:3]:
                    lines.append(f"  {run.status.value}  [{run.progress_bar}] {run.nodes_run}/{run.nodes_total}  {run.duration_str}  by {run.triggered_by}")
                lines.append("")

            # Logs
            if wf.logs:
                lines.append("  ── Logs ──")
                for log in wf.logs[:5]:
                    lines.append(f"  {log.time_str} {log.level_icon} {log.node_name}: {log.message[:55]}")
                lines.append("")

        lines.append("  [E]ditor [L]ogs [R]uns [↑↓]Workflow [←→]Node [Space]Run")
        return lines
