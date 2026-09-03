"""CI/CD Pipeline Visualizer — stage graphs, log streaming, deployment status for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import random


class PipelineStatus(Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCESS = "Success"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    PAUSED = "Paused"
    SKIPPED = "Skipped"


class StageStatus(Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCESS = "Success"
    FAILED = "Failed"
    SKIPPED = "Skipped"


class DeployTarget(Enum):
    STAGING = "Staging"
    PRODUCTION = "Production"
    CANARY = "Canary"
    BLUE_GREEN = "Blue-Green"
    ROLLING = "Rolling"
    FEATURE_FLAG = "Feature Flag"


class TriggerType(Enum):
    PUSH = "Push"
    PR = "Pull Request"
    TAG = "Tag"
    SCHEDULE = "Schedule"
    MANUAL = "Manual"
    WEBHOOK = "Webhook"
    RELEASE = "Release"


@dataclass
class StageStep:
    name: str
    status: StageStatus = StageStatus.PENDING
    duration_s: float = 0.0
    command: str = ""
    log_output: str = ""

    @property
    def status_icon(self) -> str:
        icons = {
            StageStatus.PENDING: "⏳",
            StageStatus.RUNNING: "🔄",
            StageStatus.SUCCESS: "✅",
            StageStatus.FAILED: "❌",
            StageStatus.SKIPPED: "⏭",
        }
        return icons.get(self.status, "?")

    @property
    def duration_str(self) -> str:
        d = self.duration_s
        if d < 60:
            return f"{d:.0f}s"
        return f"{d / 60:.1f}m"


@dataclass
class Stage:
    name: str
    status: StageStatus = StageStatus.PENDING
    steps: List[StageStep] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    runner: str = "ubuntu-latest"
    parallel: bool = False
    needs: List[str] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        icons = {
            StageStatus.PENDING: "⏳",
            StageStatus.RUNNING: "🔄",
            StageStatus.SUCCESS: "✅",
            StageStatus.FAILED: "❌",
            StageStatus.SKIPPED: "⏭",
        }
        return icons.get(self.status, "?")

    @property
    def duration_s(self) -> float:
        if self.started_at > 0 and self.finished_at and self.finished_at > 0:
            return self.finished_at - self.started_at
        if self.started_at > 0:
            return time.time() - self.started_at
        return 0

    @property
    def duration_str(self) -> str:
        d = self.duration_s
        if d < 60:
            return f"{d:.0f}s"
        return f"{d / 60:.1f}m"

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        done = sum(1 for s in self.steps if s.status in (StageStatus.SUCCESS, StageStatus.FAILED, StageStatus.SKIPPED))
        return done / len(self.steps)

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class Deployment:
    id: str
    environment: DeployTarget
    version: str = ""
    status: PipelineStatus = PipelineStatus.PENDING
    started_at: float = 0.0
    completed_at: float = 0.0
    triggered_by: str = ""
    commit_sha: str = ""
    rollback_available: bool = False
    health_check: bool = False
    traffic_percent: float = 100.0

    @property
    def status_icon(self) -> str:
        icons = {
            PipelineStatus.PENDING: "⏳",
            PipelineStatus.RUNNING: "🔄",
            PipelineStatus.SUCCESS: "✅",
            PipelineStatus.FAILED: "❌",
            PipelineStatus.CANCELLED: "🚫",
            PipelineStatus.PAUSED: "⏸",
        }
        return icons.get(self.status, "?")

    @property
    def env_icon(self) -> str:
        icons = {
            DeployTarget.STAGING: "🟡",
            DeployTarget.PRODUCTION: "🔴",
            DeployTarget.CANARY: "🐦",
            DeployTarget.BLUE_GREEN: "🔵🟢",
            DeployTarget.ROLLING: "🔄",
            DeployTarget.FEATURE_FLAG: "🏴",
        }
        return icons.get(self.environment, "?")


@dataclass
class PipelineRun:
    id: int
    pipeline_name: str
    status: PipelineStatus = PipelineStatus.PENDING
    trigger: TriggerType = TriggerType.PUSH
    branch: str = "main"
    commit_sha: str = ""
    commit_msg: str = ""
    author: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    stages: List[Stage] = field(default_factory=list)
    deployment: Optional[Deployment] = None

    @property
    def status_icon(self) -> str:
        icons = {
            PipelineStatus.PENDING: "⏳",
            PipelineStatus.RUNNING: "🔄",
            PipelineStatus.SUCCESS: "✅",
            PipelineStatus.FAILED: "❌",
            PipelineStatus.CANCELLED: "🚫",
            PipelineStatus.PAUSED: "⏸",
        }
        return icons.get(self.status, "?")

    @property
    def trigger_icon(self) -> str:
        icons = {
            TriggerType.PUSH: "⬆",
            TriggerType.PR: "🔀",
            TriggerType.TAG: "🏷",
            TriggerType.SCHEDULE: "⏰",
            TriggerType.MANUAL: "👆",
            TriggerType.WEBHOOK: "🪝",
            TriggerType.RELEASE: "📦",
        }
        return icons.get(self.trigger, "?")

    @property
    def duration_s(self) -> float:
        if self.started_at > 0 and self.finished_at and self.finished_at > 0:
            return self.finished_at - self.started_at
        if self.started_at > 0:
            return time.time() - self.started_at
        return 0

    @property
    def duration_str(self) -> str:
        d = self.duration_s
        if d < 60:
            return f"{d:.0f}s"
        elif d < 3600:
            return f"{d / 60:.1f}m"
        return f"{d / 3600:.1f}h"

    @property
    def stage_flow(self) -> str:
        return " → ".join(f"{s.status_icon}{s.name}" for s in self.stages)


class CICDPipeline:
    def __init__(self):
        self._runs: List[PipelineRun] = []
        self._selected_run: int = 0
        self._selected_stage: int = 0
        self._view_mode: str = "runs"
        self._auto_refresh: bool = True
        self._show_logs: bool = False
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Run 1: Current running
        run1 = PipelineRun(1, "Nyrqis OS Build", PipelineStatus.RUNNING, TriggerType.PUSH,
                           "main", "a1b2c3d", "Fix compositor memory leak",
                           "developer", now - 180,
                           stages=[
                               Stage("Install", StageStatus.SUCCESS, [
                                   StageStep("Install dependencies", StageStatus.SUCCESS, 45, "npm ci"),
                                   StageStep("Install Rust toolchain", StageStatus.SUCCESS, 30, "rustup show"),
                               ], now - 180, now - 105, runner="ubuntu-latest"),
                               Stage("Lint", StageStatus.SUCCESS, [
                                   StageStep("Python linting", StageStatus.SUCCESS, 12, "ruff check ."),
                                   StageStep("Rust clippy", StageStatus.SUCCESS, 25, "cargo clippy -- -D warnings"),
                                   StageStep("Type check", StageStatus.SUCCESS, 8, "mypy ."),
                               ], now - 105, now - 60, runner="ubuntu-latest"),
                               Stage("Test", StageStatus.RUNNING, [
                                   StageStep("Unit tests", StageStatus.SUCCESS, 40, "pytest tests/ -v"),
                                   StageStep("Integration tests", StageStatus.RUNNING, 0, "pytest tests/integration/"),
                                   StageStep("GPU tests", StageStatus.PENDING, 0, "python test_gpu.py"),
                               ], now - 60, None, runner="ubuntu-latest"),
                               Stage("Build", StageStatus.PENDING, [
                                   StageStep("Build Python wheel", StageStatus.PENDING, 0, "python -m build"),
                                   StageStep("Build Rust crates", StageStatus.PENDING, 0, "cargo build --release"),
                                   StageStep("Package DEB", StageStatus.PENDING, 0, "dpkg-deb -b ."),
                               ], runner="ubuntu-latest"),
                               Stage("Deploy", StageStatus.PENDING, [
                                   StageStep("Push to staging", StageStatus.PENDING, 0, "docker push staging"),
                                   StageStep("Health check", StageStatus.PENDING, 0, "curl -f localhost:8080/health"),
                               ], runner="ubuntu-latest"),
                           ],
                           deployment=Deployment("dep-001", DeployTarget.STAGING, "v1.4.0-rc1",
                                                 PipelineStatus.PENDING, triggered_by="developer",
                                                 commit_sha="a1b2c3d"))
        self._runs.append(run1)

        # Run 2: Completed success
        run2 = PipelineRun(2, "Nyrqis OS Build", PipelineStatus.SUCCESS, TriggerType.PR,
                           "feature/wayland-bridge", "e4f5g6h", "Add Wayland bridge protocol",
                           "contributor", now - 7200, now - 6840,
                           stages=[
                               Stage("Install", StageStatus.SUCCESS, [
                                   StageStep("Install dependencies", StageStatus.SUCCESS, 38),
                                   StageStep("Install Rust toolchain", StageStatus.SUCCESS, 28),
                               ], now - 7200, now - 7134),
                               Stage("Lint", StageStatus.SUCCESS, [
                                   StageStep("Python linting", StageStatus.SUCCESS, 10),
                                   StageStep("Rust clippy", StageStatus.SUCCESS, 22),
                                   StageStep("Type check", StageStatus.SUCCESS, 7),
                               ], now - 7134, now - 7095),
                               Stage("Test", StageStatus.SUCCESS, [
                                   StageStep("Unit tests", StageStatus.SUCCESS, 35),
                                   StageStep("Integration tests", StageStatus.SUCCESS, 45),
                               ], now - 7095, now - 7015),
                               Stage("Build", StageStatus.SUCCESS, [
                                   StageStep("Build Python wheel", StageStatus.SUCCESS, 20),
                                   StageStep("Build Rust crates", StageStatus.SUCCESS, 60),
                               ], now - 7015, now - 6935),
                           ],
                           deployment=Deployment("dep-002", DeployTarget.STAGING, "v1.4.0-alpha.3",
                                                 PipelineStatus.SUCCESS, now - 6935, now - 6840,
                                                 "contributor", "e4f5g6h", health_check=True))
        self._runs.append(run2)

        # Run 3: Failed
        run3 = PipelineRun(3, "Security Scan", PipelineStatus.FAILED, TriggerType.SCHEDULE,
                           "main", "i7j8k9l", "Scheduled security audit",
                           "ci-bot", now - 86400, now - 86340,
                           stages=[
                               Stage("SAST", StageStatus.SUCCESS, [
                                   StageStep("Semgrep scan", StageStatus.SUCCESS, 120),
                                   StageStep("Bandit scan", StageStatus.SUCCESS, 45),
                               ], now - 86400, now - 85835),
                               Stage("DAST", StageStatus.SUCCESS, [
                                   StageStep("OWASP ZAP", StageStatus.SUCCESS, 300),
                               ], now - 85835, now - 85535),
                               Stage("Dependency Scan", StageStatus.FAILED, [
                                   StageStep("Trivy scan", StageStatus.SUCCESS, 60),
                                   StageStep("Cargo audit", StageStatus.FAILED, 15,
                                             log_output="error: vulnerable crate `openssl-sys` 0.9.80"),
                               ], now - 85535, now - 85460),
                           ])
        self._runs.append(run3)

        # Run 4: Production deploy
        run4 = PipelineRun(4, "Production Deploy", PipelineStatus.SUCCESS, TriggerType.RELEASE,
                           "v1.3.0", "m0n1o2p", "Release v1.3.0",
                           "maintainer", now - 172800, now - 172620,
                           stages=[
                               Stage("Validate", StageStatus.SUCCESS, [
                                   StageStep("Version check", StageStatus.SUCCESS, 5),
                                   StageStep("Changelog verify", StageStatus.SUCCESS, 3),
                               ], now - 172800, now - 172792),
                               Stage("Deploy Canary", StageStatus.SUCCESS, [
                                   StageStep("Push canary", StageStatus.SUCCESS, 30),
                                   StageStep("Smoke tests", StageStatus.SUCCESS, 120),
                               ], now - 172792, now - 172642),
                               Stage("Promote", StageStatus.SUCCESS, [
                                   StageStep("Shift traffic 25%", StageStatus.SUCCESS, 10),
                                   StageStep("Shift traffic 50%", StageStatus.SUCCESS, 10),
                                   StageStep("Shift traffic 100%", StageStatus.SUCCESS, 10),
                                   StageStep("Health check", StageStatus.SUCCESS, 30),
                               ], now - 172642, now - 172582),
                           ],
                           deployment=Deployment("dep-003", DeployTarget.PRODUCTION, "v1.3.0",
                                                 PipelineStatus.SUCCESS, now - 172800, now - 172620,
                                                 "maintainer", "m0n1o2p",
                                                 rollback_available=True, health_check=True))
        self._runs.append(run4)

        # Run 5: Manual
        run5 = PipelineRun(5, "Nightly Build", PipelineStatus.PENDING, TriggerType.SCHEDULE,
                           "main", "", "Nightly integration",
                           "cron", now, stages=[
                               Stage("Build", StageStatus.PENDING, []),
                               Stage("Test", StageStatus.PENDING, []),
                           ])
        self._runs.append(run5)

    @property
    def selected_run(self) -> Optional[PipelineRun]:
        if 0 <= self._selected_run < len(self._runs):
            return self._runs[self._selected_run]
        return None

    @property
    def total_runs(self) -> int:
        return len(self._runs)

    @property
    def running_pipelines(self) -> int:
        return sum(1 for r in self._runs if r.status == PipelineStatus.RUNNING)

    @property
    def success_rate(self) -> str:
        completed = [r for r in self._runs if r.status in (PipelineStatus.SUCCESS, PipelineStatus.FAILED)]
        if not completed:
            return "N/A"
        ok = sum(1 for r in completed if r.status == PipelineStatus.SUCCESS)
        return f"{ok / len(completed) * 100:.0f}%"

    def select_run(self, idx: int):
        if 0 <= idx < len(self._runs):
            self._selected_run = idx

    def handle_input(self, key: str):
        key = key.lower()
        if key == "l":
            self._show_logs = not self._show_logs
        elif key == "r":
            self._auto_refresh = not self._auto_refresh

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS CI/CD PIPELINE                                    ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Status
        lines.append(f"  Pipelines: {self.total_runs}  Running: {self.running_pipelines}  Success Rate: {self.success_rate}  Auto-Refresh: {'ON' if self._auto_refresh else 'OFF'}")
        lines.append("")

        # Runs list
        lines.append("  ── Pipeline Runs ──")
        for i, run in enumerate(self._runs):
            sel = "▶" if i == self._selected_run else " "
            dep = ""
            if run.deployment:
                dep = f" → {run.deployment.env_icon} {run.deployment.environment.value}"
            lines.append(f"  {sel} {run.status_icon} #{run.id}  {run.pipeline_name}  {run.trigger_icon} {run.branch}  {run.commit_sha[:7]}  {run.duration_str}{dep}")
            lines.append(f"      {run.stage_flow}")
        lines.append("")

        # Selected run detail
        run = self.selected_run
        if run:
            lines.append(f"  ── Run #{run.id}: {run.pipeline_name} ──")
            lines.append(f"  Status: {run.status.value} {run.status_icon}  Trigger: {run.trigger.value}  Branch: {run.branch}")
            lines.append(f"  Commit: {run.commit_sha[:7]}  Author: {run.author}  Duration: {run.duration_str}")
            lines.append(f"  Message: {run.commit_msg}")
            lines.append("")

            # Stages
            lines.append("  ── Stages ──")
            for stage in run.stages:
                lines.append(f"  {stage.status_icon} {stage.name}  [{stage.progress_bar}] {stage.duration_str}  Runner: {stage.runner}")
                for step in stage.steps:
                    log_preview = ""
                    if step.log_output:
                        log_preview = f"  📋 {step.log_output[:50]}"
                    lines.append(f"      {step.status_icon} {step.name}  {step.duration_str}{log_preview}")
            lines.append("")

            # Deployment
            if run.deployment:
                dep = run.deployment
                lines.append(f"  ── Deployment ──")
                lines.append(f"  {dep.status_icon} {dep.environment.value} {dep.version}  Health: {'✅' if dep.health_check else '❓'}  Rollback: {'Available' if dep.rollback_available else 'None'}")
                lines.append("")

        lines.append("  [↑↓]Select [L]Logs [R]Refresh [C]Cancel [R]Retry")
        return lines
