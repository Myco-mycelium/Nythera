"""
Nyrqis Job Scheduler — cron-like job scheduling application.

Features:
- Create, edit, and delete scheduled jobs
- Cron expression parser and builder
- Run history with success/failure tracking
- Resource limits (CPU, memory, I/O priority)
- Job dependencies and chaining
- Notification on completion/failure
- Job templates (backup, cleanup, sync, report)
- Calendar view of scheduled runs
- Keyboard navigation throughout
"""

import time
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime, timedelta


# ─── Data Classes ────────────────────────────────────────────────────────


class JobStatus(Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    RUNNING = "running"


class NotificationType(Enum):
    NONE = "none"
    ON_SUCCESS = "on_success"
    ON_FAILURE = "on_failure"
    ALWAYS = "always"


JOB_STATUS_ICONS = {
    JobStatus.ACTIVE: "🟢",
    JobStatus.PAUSED: "🟡",
    JobStatus.DISABLED: "⚫",
    JobStatus.RUNNING: "🔄",
    JobStatus.COMPLETED: "✅",
    JobStatus.FAILED: "❌",
}

RUN_STATUS_ICONS = {
    RunStatus.SUCCESS: "✅",
    RunStatus.FAILED: "❌",
    RunStatus.TIMEOUT: "⏱️",
    RunStatus.CANCELLED: "🚫",
    RunStatus.RUNNING: "🔄",
}


@dataclass
class CronExpression:
    """A cron schedule expression."""
    minute: str = "*"
    hour: str = "*"
    day: str = "*"
    month: str = "*"
    weekday: str = "*"

    @property
    def display(self) -> str:
        return f"{self.minute} {self.hour} {self.day} {self.month} {self.weekday}"

    @property
    def human_readable(self) -> str:
        parts = []
        if self.minute != "*" or self.hour != "*":
            parts.append(f"at {self.hour}:{self.minute.zfill(2)}")
        if self.day != "*":
            parts.append(f"on day {self.day}")
        if self.month != "*":
            parts.append(f"in month {self.month}")
        if self.weekday != "*":
            day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
            try:
                parts.append(f"on {day_names[int(self.weekday)]}")
            except (ValueError, IndexError):
                parts.append(f"on weekday {self.weekday}")
        if not parts:
            return "Every minute"
        return " ".join(parts)

    @property
    def frequency(self) -> str:
        if (self.minute == "*" and self.hour == "*" and self.day == "*"
                and self.month == "*" and self.weekday == "*"):
            return "Every minute"
        elif self.day == "*" and self.month == "*" and self.weekday == "*":
            if self.minute == "0":
                return f"Every {self.hour} hour(s)" if self.hour != "*" else "Every hour"
            return f"At {self.hour}:{self.minute.zfill(2)} daily"
        elif self.weekday != "*":
            return f"Weekly (weekday {self.weekday})"
        return self.human_readable


@dataclass
class ResourceLimits:
    """Resource limits for a job."""
    max_cpu_pct: float = 100.0
    max_memory_mb: int = 0  # 0 = unlimited
    io_priority: str = "normal"  # low, normal, high
    timeout_seconds: int = 3600
    nice: int = 10  # -20 to 19

    @property
    def display(self) -> str:
        parts = []
        if self.max_cpu_pct < 100:
            parts.append(f"CPU {self.max_cpu_pct:.0f}%")
        if self.max_memory_mb > 0:
            parts.append(f"RAM {self.max_memory_mb}MB")
        if self.nice != 10:
            parts.append(f"nice={self.nice}")
        parts.append(f"I/O {self.io_priority}")
        return " | ".join(parts)


@dataclass
class JobRun:
    """A single run of a scheduled job."""
    run_id: str
    job_id: str
    job_name: str
    status: RunStatus = RunStatus.RUNNING
    started_at: float = 0.0
    completed_at: float = 0.0
    exit_code: int = 0
    output: str = ""
    error: str = ""
    triggered_by: str = "scheduler"  # scheduler, manual, dependency

    def __post_init__(self):
        if not self.run_id:
            self.run_id = hashlib.md5(f"{time.time()}{self.job_id}".encode()).hexdigest()[:8]

    @property
    def duration_str(self) -> str:
        if self.started_at <= 0:
            return "—"
        end = self.completed_at if self.completed_at > 0 else time.time()
        d = end - self.started_at
        if d < 60:
            return f"{d:.1f}s"
        elif d < 3600:
            return f"{d / 60:.1f}m"
        return f"{d / 3600:.1f}h"

    @property
    def status_icon(self) -> str:
        return RUN_STATUS_ICONS.get(self.status, "❓")

    @property
    def time_str(self) -> str:
        if self.started_at <= 0:
            return "—"
        return datetime.fromtimestamp(self.started_at).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.job_name} — {self.time_str} ({self.duration_str})"


@dataclass
class Job:
    """A scheduled job."""
    name: str
    command: str
    cron: CronExpression = field(default_factory=CronExpression)
    status: JobStatus = JobStatus.ACTIVE
    description: str = ""
    # Resource management
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    # Dependencies
    depends_on: List[str] = field(default_factory=list)  # job names
    # Notification
    notification: NotificationType = NotificationType.ON_FAILURE
    # Run tracking
    last_run: float = 0.0
    next_run: float = 0.0
    run_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    # Metadata
    category: str = ""
    tags: List[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    created_by: str = "user"
    job_id: str = ""

    def __post_init__(self):
        if not self.job_id:
            self.job_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def status_icon(self) -> str:
        return JOB_STATUS_ICONS.get(self.status, "❓")

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.name}"

    @property
    def success_rate(self) -> float:
        if self.run_count == 0:
            return 0.0
        return self.success_count / self.run_count * 100

    @property
    def last_run_str(self) -> str:
        if self.last_run <= 0:
            return "never"
        diff = time.time() - self.last_run
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.last_run).strftime("%b %d")

    @property
    def next_run_str(self) -> str:
        if self.next_run <= 0:
            return "—"
        diff = self.next_run - time.time()
        if diff < 0:
            return "overdue"
        elif diff < 60:
            return "in < 1m"
        elif diff < 3600:
            return f"in {int(diff // 60)}m"
        elif diff < 86400:
            return f"in {int(diff // 3600)}h"
        return datetime.fromtimestamp(self.next_run).strftime("%b %d %H:%M")


# ─── Job Scheduler ───────────────────────────────────────────────────────


class JobScheduler:
    """
    Cron-like job scheduler for Nyrqis OS.
    """

    def __init__(self):
        self._jobs: List[Job] = []
        self._runs: List[JobRun] = []
        self._templates: List[Dict] = []
        self._selected_index: int = 0
        self._view_mode: str = "jobs"  # jobs, runs, calendar, templates

        self._init_templates()
        self._init_sample_data()

    def _init_templates(self) -> None:
        self._templates = [
            {"name": "Full Backup", "command": "nyrqis-backup --full /home",
             "cron": "0 2 * * *", "description": "Daily full backup at 2 AM",
             "category": "backup", "timeout": 7200},
            {"name": "Incremental Backup", "command": "nyrqis-backup --inc /home",
             "cron": "0 */6 * * *", "description": "Incremental backup every 6 hours",
             "category": "backup", "timeout": 3600},
            {"name": "Log Cleanup", "command": "nyrqis-cleanup --logs --older-than 30d",
             "cron": "0 3 * * 0", "description": "Weekly log cleanup on Sunday at 3 AM",
             "category": "cleanup", "timeout": 600},
            {"name": "Temp File Cleanup", "command": "nyrqis-cleanup --tmp --older-than 7d",
             "cron": "0 4 * * *", "description": "Daily temp file cleanup at 4 AM",
             "category": "cleanup", "timeout": 300},
            {"name": "Package Cache Sync", "command": "nyrqis-repo sync",
             "cron": "0 */4 * * *", "description": "Sync package repositories every 4 hours",
             "category": "system", "timeout": 900},
            {"name": "System Report", "command": "nyrqis-report --daily",
             "cron": "0 6 * * *", "description": "Generate daily system report",
             "category": "reporting", "timeout": 300},
            {"name": "Database Backup", "command": "nyrqis-db backup --all",
             "cron": "0 1 * * *", "description": "Daily database backup at 1 AM",
             "category": "backup", "timeout": 1800},
            {"name": "File Sync", "command": "nyrqis-sync /home/Documents remote:backup",
             "cron": "30 */3 * * *", "description": "Sync documents every 3 hours",
             "category": "sync", "timeout": 1200},
        ]

    def _init_sample_data(self) -> None:
        now = time.time()
        self._jobs = [
            Job(
                "System Backup", "nyrqis-backup --full /home",
                CronExpression("0", "2", "*", "*", "*"),
                JobStatus.ACTIVE, "Daily full system backup",
                ResourceLimits(50, 2048, "low", 7200, 15),
                notification=NotificationType.ALWAYS,
                last_run=now - 43200, next_run=now + 43200,
                run_count=30, success_count=29, failure_count=1,
                category="backup", tags=["backup", "daily"],
                created=now - 2592000,
            ),
            Job(
                "Log Rotation", "nyrqis-rotate --logs --max-size 100M",
                CronExpression("0", "0", "*", "*", "*"),
                JobStatus.ACTIVE, "Rotate and compress log files",
                ResourceLimits(25, 512, "normal", 600, 10),
                notification=NotificationType.ON_FAILURE,
                last_run=now - 14400, next_run=now + 72000,
                run_count=120, success_count=120, failure_count=0,
                category="cleanup", tags=["logs", "rotation"],
                created=now - 5184000,
            ),
            Job(
                "Cache Cleanup", "nyrqis-cleanup --cache --older-than 14d",
                CronExpression("0", "3", "*", "0", "*"),
                JobStatus.ACTIVE, "Clean package and system caches",
                ResourceLimits(30, 1024, "normal", 900, 10),
                last_run=now - 259200, next_run=now + 345600,
                run_count=15, success_count=15, failure_count=0,
                category="cleanup", tags=["cache", "weekly"],
                created=now - 1296000,
            ),
            Job(
                "Database Sync", "nyrqis-db sync --replicate",
                CronExpression("*/30", "*", "*", "*", "*"),
                JobStatus.PAUSED, "Replicate databases to standby",
                ResourceLimits(80, 4096, "high", 1200, -5),
                last_run=now - 172800, next_run=0,
                run_count=45, success_count=44, failure_count=1,
                category="database", tags=["db", "replication"],
                created=now - 2592000,
            ),
            Job(
                "Disk Health Check", "nyrqis-health --disks --smart",
                CronExpression("0", "6", "*", "*", "1"),
                JobStatus.ACTIVE, "Weekly S.M.A.R.T. health scan",
                ResourceLimits(20, 256, "low", 1800, 19),
                notification=NotificationType.ALWAYS,
                last_run=now - 432000, next_run=now + 180000,
                run_count=20, success_count=20, failure_count=0,
                category="system", tags=["health", "smart", "weekly"],
                created=now - 1209600,
            ),
            Job(
                "Security Scan", "nyrqis-scan --security --full",
                CronExpression("0", "4", "*", "*", "*"),
                JobStatus.ACTIVE, "Daily security vulnerability scan",
                ResourceLimits(60, 2048, "normal", 3600, 10),
                notification=NotificationType.ON_FAILURE,
                last_run=now - 36000, next_run=now + 50400,
                run_count=45, success_count=44, failure_count=1,
                category="security", tags=["scan", "vulnerability"],
                created=now - 3888000,
            ),
            Job(
                "Report Generator", "nyrqis-report --weekly --email admin",
                CronExpression("0", "8", "*", "*", "5"),
                JobStatus.ACTIVE, "Generate and email weekly reports",
                ResourceLimits(40, 1024, "normal", 600, 10),
                last_run=now - 172800, next_run=now + 432000,
                run_count=12, success_count=12, failure_count=0,
                category="reporting", tags=["report", "email", "weekly"],
                created=now - 864000,
            ),
            Job(
                "Old Backup Cleanup", "nyrqis-backup --prune --older-than 90d",
                CronExpression("0", "5", "1", "*", "*"),
                JobStatus.DISABLED, "Prune backups older than 90 days",
                ResourceLimits(30, 512, "low", 3600, 15),
                run_count=3, success_count=3, failure_count=0,
                category="backup", tags=["cleanup", "monthly"],
                created=now - 2592000,
            ),
        ]

        # Generate sample run history
        for i in range(40):
            age = random.uniform(0, 604800)
            job = random.choice(self._jobs[:6])
            status = random.choices(
                [RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.TIMEOUT],
                weights=[90, 8, 2]
            )[0]
            duration = random.uniform(1.0, 300.0)

            run = JobRun(
                run_id=hashlib.md5(f"run{i}".encode()).hexdigest()[:8],
                job_id=job.job_id,
                job_name=job.name,
                status=status,
                started_at=now - age,
                completed_at=now - age + duration,
                exit_code=0 if status == RunStatus.SUCCESS else 1,
                output=f"Completed {job.name} successfully" if status == RunStatus.SUCCESS else "",
                error="" if status == RunStatus.SUCCESS else f"Error in {job.name}",
            )
            self._runs.append(run)

        self._runs.sort(key=lambda r: r.started_at, reverse=True)

    # ── Job Operations ────────────────────────────────────────────────

    def create_job(self, name: str, command: str, cron: CronExpression,
                   description: str = "", category: str = "") -> Job:
        job = Job(
            name=name, command=command, cron=cron,
            description=description, category=category,
            next_run=time.time() + 60,
        )
        self._jobs.append(job)
        return job

    def delete_job(self, index: int) -> bool:
        if 0 <= index < len(self._jobs):
            self._jobs.pop(index)
            self._selected_index = min(self._selected_index, len(self._jobs) - 1)
            return True
        return False

    def toggle_job(self, index: int) -> JobStatus:
        if 0 <= index < len(self._jobs):
            job = self._jobs[index]
            if job.status == JobStatus.ACTIVE:
                job.status = JobStatus.PAUSED
            elif job.status == JobStatus.PAUSED:
                job.status = JobStatus.ACTIVE
            elif job.status == JobStatus.DISABLED:
                job.status = JobStatus.ACTIVE
            return job.status
        return JobStatus.ACTIVE

    def run_job_now(self, index: int) -> Optional[JobRun]:
        if 0 <= index < len(self._jobs):
            job = self._jobs[index]
            run = JobRun(
                run_id=hashlib.md5(f"manual{time.time()}".encode()).hexdigest()[:8],
                job_id=job.job_id,
                job_name=job.name,
                status=RunStatus.SUCCESS,
                started_at=time.time(),
                completed_at=time.time() + random.uniform(1, 30),
                triggered_by="manual",
                exit_code=0,
                output=f"Manual run of {job.name} completed",
            )
            self._runs.insert(0, run)
            job.last_run = time.time()
            job.run_count += 1
            job.success_count += 1
            return run
        return None

    def create_from_template(self, template_idx: int) -> Optional[Job]:
        if 0 <= template_idx < len(self._templates):
            tmpl = self._templates[template_idx]
            parts = tmpl["cron"].split()
            cron = CronExpression(
                parts[0] if len(parts) > 0 else "*",
                parts[1] if len(parts) > 1 else "*",
                parts[2] if len(parts) > 2 else "*",
                parts[3] if len(parts) > 3 else "*",
                parts[4] if len(parts) > 4 else "*",
            )
            return self.create_job(
                tmpl["name"], tmpl["command"], cron,
                tmpl.get("description", ""), tmpl.get("category", ""),
            )
        return None

    # ── Navigation ────────────────────────────────────────────────────

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        items = self._get_display_list()
        self._selected_index = min(len(items) - 1, self._selected_index + 1)

    def get_selected_item(self):
        items = self._get_display_list()
        if 0 <= self._selected_index < len(items):
            return items[self._selected_index]
        return None

    def _get_display_list(self) -> list:
        if self._view_mode == "runs":
            return self._runs
        elif self._view_mode == "templates":
            return list(range(len(self._templates)))
        return self._jobs

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def jobs(self) -> List[Job]:
        return list(self._jobs)

    @property
    def runs(self) -> List[JobRun]:
        return list(self._runs)

    @property
    def templates(self) -> List[Dict]:
        return list(self._templates)

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def active_count(self) -> int:
        return sum(1 for j in self._jobs if j.status == JobStatus.ACTIVE)

    @property
    def total_runs(self) -> int:
        return len(self._runs)

    @property
    def success_rate(self) -> float:
        if not self._runs:
            return 0.0
        successes = sum(1 for r in self._runs if r.status == RunStatus.SUCCESS)
        return successes / len(self._runs) * 100

    # ── Rendering ─────────────────────────────────────────────────────

    def render_jobs(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" ⏰ Job Scheduler")
        lines.append("─" * width)
        lines.append(f" {self.active_count} active | {len(self._jobs)} total | {self.total_runs} runs | {self.success_rate:.0f}% success")
        lines.append("─" * width)

        if not self._jobs:
            lines.append("  No jobs configured. Press N to create or T for templates.")
        else:
            for i, job in enumerate(self._jobs):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {job.display}")
                lines.append(f"   📋 {job.cron.human_readable}")
                lines.append(f"   🔧 {job.command[:width - 8]}")
                lines.append(f"   📊 Runs: {job.run_count} ({job.success_count} ✅ {job.failure_count} ❌) | Last: {job.last_run_str} | Next: {job.next_run_str}")
                if job.limits.display:
                    lines.append(f"   ⚙️  {job.limits.display}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Run now  Space:Toggle  T:Templates")
        lines.append(" R:Run history  Del:Delete  N:New  Esc:Back")
        return lines

    def render_runs(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 📋 Run History ({len(self._runs)} runs)")
        lines.append("─" * width)

        for i, run in enumerate(self._runs[:20]):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {run.display}")
            lines.append(f"   Exit: {run.exit_code} | Triggered by: {run.triggered_by}")
            if run.output:
                lines.append(f"   Output: {run.output[:width - 12]}")
            if run.error:
                lines.append(f"   Error: {run.error[:width - 12]}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Esc:Back")
        return lines

    def render_templates(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📋 Job Templates")
        lines.append("─" * width)

        for i, tmpl in enumerate(self._templates):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {tmpl['name']}")
            lines.append(f"   📋 Schedule: {tmpl['cron']}")
            lines.append(f"   🔧 {tmpl['command'][:width - 8]}")
            lines.append(f"   📝 {tmpl.get('description', '')}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Create from template  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "runs": self.render_runs,
            "templates": self.render_templates,
        }
        renderer = renderers.get(self._view_mode, self.render_jobs)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "runs":
            return self._handle_runs_key(key)
        elif self._view_mode == "templates":
            return self._handle_templates_key(key)
        return self._handle_jobs_key(key)

    def _handle_jobs_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            return "run_now" if self.run_job_now(self._selected_index) else "run_failed"
        elif key == " ":
            self.toggle_job(self._selected_index)
            return "toggle"
        elif key == "t":
            self.set_view("templates")
            return "templates"
        elif key == "r":
            self.set_view("runs")
            return "runs"
        elif key == "Delete":
            return "delete" if self.delete_job(self._selected_index) else "delete_failed"
        return None

    def _handle_runs_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("jobs")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        return None

    def _handle_templates_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("jobs")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.create_from_template(self._selected_index)
            self.set_view("jobs")
            return "create_from_template"
        return None
