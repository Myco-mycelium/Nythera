"""
Nyrqis OS - Task Scheduler
Cron-like syntax, recurring jobs, and execution history.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class ScheduleType(Enum):
    ONCE = "once"
    MINUTELY = "minutely"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CRON = "cron"
    INTERVAL = "interval"


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class CronExpression:
    minute: str = "*"
    hour: str = "*"
    day_of_month: str = "*"
    month: str = "*"
    day_of_week: str = "*"

    @property
    def display(self) -> str:
        return f"{self.minute} {self.hour} {self.day_of_month} {self.month} {self.day_of_week}"

    @property
    def human_readable(self) -> str:
        if self.minute == "*" and self.hour == "*":
            return "Every minute"
        if self.minute == "0" and self.hour == "*":
            return "Every hour"
        if self.minute == "0" and self.hour == "0" and self.day_of_month == "*":
            return "Every day at midnight"
        if self.minute == "0" and self.hour == "9" and self.day_of_week == "1-5":
            return "Weekdays at 9:00"
        return self.display


@dataclass
class ScheduledTask:
    name: str
    command: str = ""
    schedule_type: ScheduleType = ScheduleType.ONCE
    cron: Optional[CronExpression] = None
    interval_seconds: int = 0
    scheduled_time: float = 0.0
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    enabled: bool = True
    retries: int = 0
    max_retries: int = 3
    timeout_s: int = 300
    last_run: float = 0.0
    next_run: float = 0.0
    run_count: int = 0
    fail_count: int = 0
    description: str = ""
    tags: List[str] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    working_directory: str = "/"

    @property
    def status_icon(self) -> str:
        icons = {
            TaskStatus.PENDING: "⏳", TaskStatus.RUNNING: "🔄",
            TaskStatus.COMPLETED: "✅", TaskStatus.FAILED: "❌",
            TaskStatus.PAUSED: "⏸", TaskStatus.CANCELLED: "⬜",
        }
        return icons.get(self.status, "?")

    @property
    def priority_icon(self) -> str:
        icons = {
            TaskPriority.CRITICAL: "🔴", TaskPriority.HIGH: "🟠",
            TaskPriority.NORMAL: "🟢", TaskPriority.LOW: "⚪",
        }
        return icons.get(self.priority, "?")

    @property
    def schedule_display(self) -> str:
        if self.schedule_type == ScheduleType.CRON and self.cron:
            return self.cron.human_readable
        if self.schedule_type == ScheduleType.INTERVAL:
            return f"Every {self.interval_seconds}s"
        if self.schedule_type == ScheduleType.ONCE:
            return "One-time"
        return self.schedule_type.value

    @property
    def time_until_run(self) -> str:
        if self.next_run == 0:
            return "N/A"
        delta = self.next_run - time.time()
        if delta < 0:
            return "Overdue"
        if delta < 60:
            return f"{delta:.0f}s"
        elif delta < 3600:
            return f"{delta / 60:.0f}m"
        return f"{delta / 3600:.1f}h"


@dataclass
class TaskExecution:
    task_name: str
    start_time: float = 0.0
    end_time: float = 0.0
    status: TaskStatus = TaskStatus.COMPLETED
    exit_code: int = 0
    output: str = ""
    error: str = ""
    duration_s: float = 0.0
    trigger: str = "scheduled"

    @property
    def status_icon(self) -> str:
        icons = {
            TaskStatus.COMPLETED: "✅", TaskStatus.FAILED: "❌",
            TaskStatus.RUNNING: "🔄",
        }
        return icons.get(self.status, "?")


class TaskScheduler:
    def __init__(self):
        self.tasks: List[ScheduledTask] = []
        self.executions: List[TaskExecution] = []
        self.running_count: int = 0
        self.max_concurrent: int = 5
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.tasks = [
            ScheduledTask(name="System Backup", command="/usr/local/bin/backup.sh",
                          schedule_type=ScheduleType.DAILY,
                          cron=CronExpression(hour="2", minute="0"),
                          priority=TaskPriority.HIGH, status=TaskStatus.COMPLETED,
                          last_run=now - 72000, next_run=now + 14400,
                          run_count=45, fail_count=2,
                          description="Full system backup to external drive",
                          tags=["backup", "system"]),
            ScheduledTask(name="Log Rotation", command="/usr/sbin/logrotate /etc/logrotate.conf",
                          schedule_type=ScheduleType.DAILY,
                          cron=CronExpression(hour="0", minute="0"),
                          priority=TaskPriority.NORMAL, status=TaskStatus.COMPLETED,
                          last_run=now - 86400, next_run=now,
                          run_count=365, fail_count=0,
                          description="Rotate and compress system logs",
                          tags=["maintenance", "logs"]),
            ScheduledTask(name="Memory Cleanup", command="/usr/local/bin/memcleanup.sh",
                          schedule_type=ScheduleType.HOURLY,
                          cron=CronExpression(minute="30"),
                          priority=TaskPriority.LOW, status=TaskStatus.COMPLETED,
                          last_run=now - 1800, next_run=now + 1800,
                          run_count=2400, fail_count=5,
                          description="Clear caches and free memory",
                          tags=["maintenance", "memory"]),
            ScheduledTask(name="Health Check", command="/usr/local/bin/healthcheck.sh",
                          schedule_type=ScheduleType.INTERVAL,
                          interval_seconds=300,
                          priority=TaskPriority.HIGH, status=TaskStatus.COMPLETED,
                          last_run=now - 120, next_run=now + 180,
                          run_count=8640, fail_count=12,
                          description="Check system health and services",
                          tags=["monitoring", "health"]),
            ScheduledTask(name="Database Backup", command="/usr/local/bin/dbbackup.sh",
                          schedule_type=ScheduleType.CRON,
                          cron=CronExpression(minute="0", hour="3", day_of_week="0"),
                          priority=TaskPriority.CRITICAL, status=TaskStatus.COMPLETED,
                          last_run=now - 604800, next_run=now + 604800,
                          run_count=52, fail_count=1,
                          description="Weekly PostgreSQL backup",
                          tags=["backup", "database"]),
            ScheduledTask(name="Security Scan", command="/usr/local/bin/securityscan.sh",
                          schedule_type=ScheduleType.DAILY,
                          cron=CronExpression(hour="4", minute="0"),
                          priority=TaskPriority.HIGH, status=TaskStatus.PAUSED,
                          last_run=now - 86400, next_run=now + 86400,
                          run_count=180, fail_count=3,
                          description="Run security vulnerability scan",
                          tags=["security", "scanning"]),
            ScheduledTask(name="Weather Update", command="python3 /opt/nyrqis/weather.py",
                          schedule_type=ScheduleType.HOURLY,
                          cron=CronExpression(minute="0"),
                          priority=TaskPriority.LOW, status=TaskStatus.COMPLETED,
                          last_run=now - 600, next_run=now + 3000,
                          run_count=4320, fail_count=45,
                          description="Fetch weather data for widget",
                          tags=["weather", "widget"]),
            ScheduledTask(name="GPU Telemetry", command="/opt/nyrqis/gpu-telemetry",
                          schedule_type=ScheduleType.MINUTELY,
                          cron=CronExpression(minute="*/5"),
                          priority=TaskPriority.NORMAL, status=TaskStatus.COMPLETED,
                          last_run=now - 180, next_run=now + 120,
                          run_count=103680, fail_count=120,
                          description="Collect GPU temperature and usage",
                          tags=["monitoring", "gpu"]),
            ScheduledTask(name="Temp File Cleanup", command="find /tmp -mtime +7 -delete",
                          schedule_type=ScheduleType.WEEKLY,
                          cron=CronExpression(minute="0", hour="1", day_of_week="6"),
                          priority=TaskPriority.LOW, status=TaskStatus.COMPLETED,
                          last_run=now - 518400, next_run=now + 172800,
                          run_count=52, fail_count=0,
                          description="Clean up files older than 7 days in /tmp",
                          tags=["cleanup", "maintenance"]),
            ScheduledTask(name="DNS Cache Flush", command="systemd-resolve --flush-caches",
                          schedule_type=ScheduleType.ONCE,
                          scheduled_time=now + 3600,
                          priority=TaskPriority.NORMAL, status=TaskStatus.PENDING,
                          next_run=now + 3600, run_count=0,
                          description="Flush DNS cache (one-time)",
                          tags=["network", "dns"]),
        ]

        for task in self.tasks:
            if task.last_run > 0:
                self.executions.append(TaskExecution(
                    task_name=task.name,
                    start_time=task.last_run,
                    end_time=task.last_run + random.uniform(1, 30),
                    status=TaskStatus.COMPLETED,
                    exit_code=0,
                    output=f"Completed successfully",
                    duration_s=random.uniform(1, 30),
                    trigger="scheduled"))

    def add_task(self, task: ScheduledTask) -> None:
        self.tasks.append(task)

    def remove_task(self, name: str) -> bool:
        for i, t in enumerate(self.tasks):
            if t.name == name:
                del self.tasks[i]
                return True
        return False

    def toggle_task(self, name: str) -> bool:
        task = next((t for t in self.tasks if t.name == name), None)
        if task:
            task.enabled = not task.enabled
            task.status = TaskStatus.PAUSED if not task.enabled else TaskStatus.PENDING
            return True
        return False

    def run_task(self, name: str) -> Optional[TaskExecution]:
        task = next((t for t in self.tasks if t.name == name), None)
        if not task:
            return None
        start = time.time()
        task.status = TaskStatus.RUNNING
        exec_entry = TaskExecution(task_name=name, start_time=start,
                                    status=TaskStatus.RUNNING, trigger="manual")
        self.executions.append(exec_entry)
        task.status = TaskStatus.COMPLETED
        task.last_run = time.time()
        task.run_count += 1
        exec_entry.end_time = time.time()
        exec_entry.duration_s = exec_entry.end_time - exec_entry.start_time
        exec_entry.status = TaskStatus.COMPLETED
        return exec_entry

    def get_tasks_by_status(self, status: TaskStatus) -> List[ScheduledTask]:
        return [t for t in self.tasks if t.status == status]

    def get_tasks_by_priority(self, priority: TaskPriority) -> List[ScheduledTask]:
        return [t for t in self.tasks if t.priority == priority]

    def search_tasks(self, query: str) -> List[ScheduledTask]:
        q = query.lower()
        return [t for t in self.tasks if q in t.name.lower() or q in t.description.lower()]

    def get_executions(self, task_name: str = "", limit: int = 20) -> List[TaskExecution]:
        execs = self.executions
        if task_name:
            execs = [e for e in execs if e.task_name == task_name]
        return sorted(execs, key=lambda e: e.start_time, reverse=True)[:limit]

    def get_stats(self) -> Dict:
        total_runs = sum(t.run_count for t in self.tasks)
        total_fails = sum(t.fail_count for t in self.tasks)
        return {
            "total_tasks": len(self.tasks),
            "enabled": sum(1 for t in self.tasks if t.enabled),
            "total_runs": total_runs,
            "total_failures": total_fails,
            "executions": len(self.executions),
            "success_rate": round((1 - total_fails / max(total_runs, 1)) * 100, 1),
        }
