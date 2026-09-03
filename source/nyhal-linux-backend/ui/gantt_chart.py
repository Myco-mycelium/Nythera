"""Gantt Chart Project Manager — dependencies, milestones, resource allocation for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
from datetime import datetime, timedelta


class TaskStatus(Enum):
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    BLOCKED = "Blocked"
    ON_HOLD = "On Hold"
    CANCELLED = "Cancelled"


class TaskPriority(Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class MilestoneType(Enum):
    START = "Start"
    END = "End"
    CHECKPOINT = "Checkpoint"
    DEADLINE = "Deadline"
    REVIEW = "Review"


class DependencyType(Enum):
    FINISH_START = "FS"  # Finish-to-Start
    START_START = "SS"  # Start-to-Start
    FINISH_FINISH = "FF"  # Finish-to-Finish
    START_FINISH = "SF"  # Start-to-Finish


class ResourceRole(Enum):
    DEVELOPER = "Developer"
    DESIGNER = "Designer"
    MANAGER = "Manager"
    QA = "QA"
    DEVOPS = "DevOps"
    ADMIN = "Admin"


@dataclass
class Resource:
    name: str
    role: ResourceRole
    allocation: float = 1.0  # 0.0 to 1.0 (1.0 = fully allocated)
    hourly_rate: float = 75.0
    skills: List[str] = field(default_factory=list)
    available: bool = True
    max_hours_per_week: float = 40.0
    booked_hours: float = 0.0

    @property
    def allocation_bar(self) -> str:
        filled = int(self.allocation * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def available_hours(self) -> float:
        return max(0, self.max_hours_per_week - self.booked_hours)

    @property
    def status_icon(self) -> str:
        if not self.available:
            return "🔴"
        if self.allocation >= 1.0:
            return "🟡"
        return "🟢"


@dataclass
class TaskDependency:
    predecessor_id: int
    successor_id: int
    dep_type: DependencyType = DependencyType.FINISH_START
    lag_days: float = 0.0


@dataclass
class Milestone:
    name: str
    milestone_type: MilestoneType
    date: float  # timestamp
    task_id: int = -1
    completed: bool = False
    notes: str = ""

    @property
    def icon(self) -> str:
        icons = {
            MilestoneType.START: "🟢",
            MilestoneType.END: "🏁",
            MilestoneType.CHECKPOINT: "📍",
            MilestoneType.DEADLINE: "⏰",
            MilestoneType.REVIEW: "👁",
        }
        return icons.get(self.milestone_type, "•")


@dataclass
class Task:
    id: int
    name: str
    start_day: int = 0  # day offset from project start
    duration_days: int = 1
    progress: float = 0.0  # 0.0 to 1.0
    status: TaskStatus = TaskStatus.NOT_STARTED
    priority: TaskPriority = TaskPriority.MEDIUM
    assigned_resources: List[str] = field(default_factory=list)
    dependencies: List[int] = field(default_factory=list)  # task IDs
    category: str = ""
    notes: str = ""
    effort_hours: float = 0.0
    actual_hours: float = 0.0
    color: str = "#4a9eff"

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def end_day(self) -> int:
        return self.start_day + self.duration_days

    @property
    def status_icon(self) -> str:
        icons = {
            TaskStatus.NOT_STARTED: "⬜",
            TaskStatus.IN_PROGRESS: "🔄",
            TaskStatus.COMPLETED: "✅",
            TaskStatus.BLOCKED: "🚫",
            TaskStatus.ON_HOLD: "⏸",
            TaskStatus.CANCELLED: "❌",
        }
        return icons.get(self.status, "?")

    @property
    def priority_icon(self) -> str:
        icons = {
            TaskPriority.CRITICAL: "🔴",
            TaskPriority.HIGH: "🟠",
            TaskPriority.MEDIUM: "🟡",
            TaskPriority.LOW: "🟢",
        }
        return icons.get(self.priority, "?")

    @property
    def gantt_bar(self) -> str:
        filled = int(self.progress * 20)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class Project:
    name: str
    start_date: float = 0.0  # timestamp
    tasks: List[Task] = field(default_factory=list)
    milestones: List[Milestone] = field(default_factory=list)
    resources: List[Resource] = field(default_factory=list)
    dependencies: List[TaskDependency] = field(default_factory=list)
    total_budget: float = 50000.0
    spent_budget: float = 0.0
    description: str = ""

    @property
    def total_tasks(self) -> int:
        return len(self.tasks)

    @property
    def completed_tasks(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)

    @property
    def in_progress_tasks(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.IN_PROGRESS)

    @property
    def blocked_tasks(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.BLOCKED)

    @property
    def overall_progress(self) -> float:
        if not self.tasks:
            return 0.0
        return sum(t.progress for t in self.tasks) / len(self.tasks)

    @property
    def progress_bar(self) -> str:
        filled = int(self.overall_progress * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def total_duration(self) -> int:
        if not self.tasks:
            return 0
        return max(t.end_day for t in self.tasks)

    @property
    def budget_bar(self) -> str:
        ratio = min(self.spent_budget / self.total_budget, 1.0) if self.total_budget > 0 else 0
        filled = int(ratio * 10)
        return "█" * filled + "░" * (10 - filled)

    @property
    def budget_str(self) -> str:
        return f"${self.spent_budget:,.0f} / ${self.total_budget:,.0f}"

    @property
    def total_effort(self) -> float:
        return sum(t.effort_hours for t in self.tasks)

    @property
    def actual_effort(self) -> float:
        return sum(t.actual_hours for t in self.tasks)


class GanttChart:
    def __init__(self):
        self._project: Optional[Project] = None
        self._selected_task: int = 0
        self._selected_milestone: int = -1
        self._view_mode: str = "gantt"
        self._zoom_level: float = 1.0
        self._show_dependencies: bool = True
        self._show_milestones: bool = True
        self._show_resources: bool = True
        self._show_critical_path: bool = True
        self._today_offset: int = 5
        self._sort_by: str = "start"  # start, name, progress, priority
        self._filter_status: Optional[TaskStatus] = None
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()
        tasks = [
            Task(0, "Project Planning", 0, 5, 1.0, TaskStatus.COMPLETED, TaskPriority.HIGH,
                 ["Alice"], [], "Planning", "Requirements gathering", 40, 38),
            Task(1, "UI/UX Design", 3, 10, 0.8, TaskStatus.IN_PROGRESS, TaskPriority.HIGH,
                 ["Bob"], [0], "Design", "Wireframes and mockups", 80, 64),
            Task(2, "Backend API Design", 3, 8, 0.9, TaskStatus.IN_PROGRESS, TaskPriority.CRITICAL,
                 ["Carol"], [0], "Development", "REST API specification", 64, 58),
            Task(3, "Database Schema", 5, 5, 1.0, TaskStatus.COMPLETED, TaskPriority.HIGH,
                 ["Carol"], [0], "Development", "PostgreSQL schema design", 40, 36),
            Task(4, "Frontend Development", 10, 15, 0.3, TaskStatus.IN_PROGRESS, TaskPriority.HIGH,
                 ["Bob", "Dave"], [1, 3], "Development", "React components", 120, 36),
            Task(5, "Backend Implementation", 10, 20, 0.2, TaskStatus.IN_PROGRESS, TaskPriority.CRITICAL,
                 ["Carol", "Eve"], [2, 3], "Development", "API endpoints", 160, 32),
            Task(6, "Authentication System", 12, 8, 0.0, TaskStatus.NOT_STARTED, TaskPriority.CRITICAL,
                 ["Eve"], [3], "Development", "OAuth2 + JWT", 64, 0),
            Task(7, "Unit Testing", 15, 10, 0.0, TaskStatus.NOT_STARTED, TaskPriority.MEDIUM,
                 ["Frank"], [4, 5], "QA", "Test coverage 80%", 80, 0),
            Task(8, "Integration Testing", 22, 8, 0.0, TaskStatus.NOT_STARTED, TaskPriority.HIGH,
                 ["Frank"], [7], "QA", "End-to-end tests", 64, 0),
            Task(9, "Performance Optimization", 25, 5, 0.0, TaskStatus.NOT_STARTED, TaskPriority.MEDIUM,
                 ["Dave"], [5], "Development", "Load testing & optimization", 40, 0),
            Task(10, "Security Audit", 28, 5, 0.0, TaskStatus.NOT_STARTED, TaskPriority.CRITICAL,
                 ["Eve"], [6, 5], "Security", "Penetration testing", 40, 0),
            Task(11, "Documentation", 20, 10, 0.0, TaskStatus.NOT_STARTED, TaskPriority.LOW,
                 ["Alice"], [4, 5], "Documentation", "API docs & user guide", 80, 0),
            Task(12, "Deployment Setup", 25, 5, 0.0, TaskStatus.NOT_STARTED, TaskPriority.HIGH,
                 ["Dave"], [5], "DevOps", "CI/CD pipeline", 40, 0),
            Task(13, "User Acceptance Testing", 30, 5, 0.0, TaskStatus.NOT_STARTED, TaskPriority.HIGH,
                 ["Alice", "Frank"], [8, 10, 12], "QA", "Stakeholder review", 40, 0),
            Task(14, "Production Launch", 35, 2, 0.0, TaskStatus.NOT_STARTED, TaskPriority.CRITICAL,
                 ["Dave"], [13], "DevOps", "Go-live deployment", 16, 0),
        ]

        milestones = [
            Milestone("Project Kickoff", MilestoneType.START, now, 0),
            Milestone("Design Complete", MilestoneType.CHECKPOINT, now + 86400 * 13, 1,
                      notes="Design review with stakeholders"),
            Milestone("MVP Ready", MilestoneType.CHECKPOINT, now + 86400 * 25, 5,
                      notes="Core features implemented"),
            Milestone("Code Freeze", MilestoneType.DEADLINE, now + 86400 * 30, 9,
                      notes="No new features after this"),
            Milestone("Launch Day", MilestoneType.END, now + 86400 * 37, 14),
        ]

        resources = [
            Resource("Alice", ResourceRole.MANAGER, 0.8, 100, ["planning", "docs"]),
            Resource("Bob", ResourceRole.DESIGNER, 0.9, 85, ["UI", "UX", "Figma"]),
            Resource("Carol", ResourceRole.DEVELOPER, 1.0, 75, ["Python", "PostgreSQL", "API"]),
            Resource("Dave", ResourceRole.DEVELOPER, 0.7, 75, ["React", "TypeScript", "DevOps"]),
            Resource("Eve", ResourceRole.DEVELOPER, 0.6, 80, ["Security", "Python", "Auth"]),
            Resource("Frank", ResourceRole.QA, 1.0, 65, ["testing", "automation"]),
        ]

        dependencies = [
            TaskDependency(0, 1, DependencyType.FINISH_START),
            TaskDependency(0, 2, DependencyType.FINISH_START),
            TaskDependency(1, 4, DependencyType.FINISH_START),
            TaskDependency(2, 5, DependencyType.FINISH_START),
            TaskDependency(3, 4, DependencyType.FINISH_START),
            TaskDependency(3, 5, DependencyType.FINISH_START),
            TaskDependency(4, 7, DependencyType.FINISH_START),
            TaskDependency(5, 7, DependencyType.FINISH_START),
            TaskDependency(7, 8, DependencyType.FINISH_START),
            TaskDependency(13, 14, DependencyType.FINISH_START),
        ]

        self._project = Project(
            "Nyrqis OS v1.0",
            now,
            tasks, milestones, resources, dependencies,
            total_budget=120000,
            spent_budget=45000,
            description="Nyrqis Linux-based operating system with Wayland compositor"
        )

    @property
    def project(self) -> Optional[Project]:
        return self._project

    @property
    def selected_task(self) -> Optional[Task]:
        if self._project and 0 <= self._selected_task < len(self._project.tasks):
            return self._project.tasks[self._selected_task]
        return None

    @property
    def critical_path(self) -> List[int]:
        """Return task IDs on critical path (tasks with CRITICAL priority)."""
        if not self._project:
            return []
        return [t.id for t in self._project.tasks if t.priority == TaskPriority.CRITICAL]

    def select_task(self, idx: int):
        if self._project and 0 <= idx < len(self._project.tasks):
            self._selected_task = idx

    def update_progress(self, task_id: int, progress: float):
        if self._project:
            for t in self._project.tasks:
                if t.id == task_id:
                    t.progress = max(0, min(1, progress))
                    if t.progress >= 1.0:
                        t.status = TaskStatus.COMPLETED
                    elif t.progress > 0:
                        t.status = TaskStatus.IN_PROGRESS
                    self._history.append(f"Updated {t.name}: {t.progress:.0%}")
                    break

    def add_task(self, name: str, start_day: int, duration: int, priority: TaskPriority = TaskPriority.MEDIUM):
        if self._project:
            task_id = len(self._project.tasks)
            task = Task(task_id, name, start_day, duration, 0, TaskStatus.NOT_STARTED, priority)
            self._project.tasks.append(task)
            self._history.append(f"Added task: {name}")

    def delete_task(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_task
        if self._project and 0 <= i < len(self._project.tasks):
            name = self._project.tasks[i].name
            self._project.tasks.pop(i)
            self._selected_task = min(self._selected_task, len(self._project.tasks) - 1)
            self._history.append(f"Deleted task: {name}")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "d":
            self._show_dependencies = not self._show_dependencies
        elif key == "m":
            self._show_milestones = not self._show_milestones
        elif key == "r":
            self._show_resources = not self._show_resources
        elif key == "c":
            self._show_critical_path = not self._show_critical_path
        elif key == "x":
            self.delete_task()
        elif key == "+":
            self.add_task("New Task", 0, 5)

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        p = self._project
        if not p:
            return ["  No project loaded"]

        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS GANTT CHART                                       ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Project info
        lines.append(f"  📋 {p.name}  {p.description}")
        lines.append(f"  Progress: [{p.progress_bar}] {p.overall_progress:.0%}  Tasks: {p.completed_tasks}/{p.total_tasks} complete  In Progress: {p.in_progress_tasks}  Blocked: {p.blocked_tasks}")
        lines.append(f"  Duration: {p.total_duration} days  Effort: {p.actual_effort:.0f}/{p.total_effort:.0f}h  Budget: [{p.budget_bar}] {p.budget_str}")
        lines.append("")

        # Gantt chart
        lines.append("  ── Timeline ──")
        max_day = p.total_duration
        timeline_width = min(30, max_day + 1)
        day_labels = ""
        for d in range(timeline_width):
            if d % 5 == 0:
                day_labels += f"{d:>3}"
            else:
                day_labels += "  "
        lines.append(f"  {'Task':<25s} |{day_labels}")
        lines.append(f"  {'─' * 25}─┼{'─' * (timeline_width * 3 - 2)}")

        for task in p.tasks:
            bar_start = task.start_day
            bar_end = task.end_day
            bar = ""
            for d in range(timeline_width):
                if d == self._today_offset:
                    bar += "│"
                elif bar_start <= d < bar_end:
                    filled = int((d - bar_start) / max(1, task.duration_days) * 8)
                    if filled < task.progress * 8:
                        bar += "█"
                    else:
                        bar += "▓"
                else:
                    bar += " "
            name = task.name[:24]
            lines.append(f"  {task.status_icon}{name:<24s}|{bar}")
        lines.append("")

        # Milestones
        if self._show_milestones and p.milestones:
            lines.append("  ── Milestones ──")
            for ms in p.milestones:
                check = "✅" if ms.completed else "⬜"
                lines.append(f"  {ms.icon} {check} {ms.name}  {ms.notes}")
            lines.append("")

        # Resources
        if self._show_resources and p.resources:
            lines.append("  ── Resources ──")
            for r in p.resources:
                lines.append(f"  {r.status_icon} {r.name:<10s} {r.role.value:<12s} [{r.allocation_bar}] {r.allocation:.0%}  ${r.hourly_rate:.0f}/hr  Avail: {r.available_hours:.0f}h")
            lines.append("")

        # Task detail
        task = self.selected_task
        if task:
            lines.append(f"  ── Task: {task.name} ──")
            lines.append(f"  Status: {task.status.value} {task.status_icon}  Priority: {task.priority.value} {task.priority_icon}")
            lines.append(f"  Day {task.start_day}→{task.end_day} ({task.duration_days}d)  Progress: [{task.progress_bar}] {task.progress:.0%}")
            lines.append(f"  Effort: {task.actual_hours:.0f}/{task.effort_hours:.0f}h  Resources: {', '.join(task.assigned_resources) or 'None'}")
            if task.dependencies:
                dep_names = []
                for dep_id in task.dependencies:
                    for t in p.tasks:
                        if t.id == dep_id:
                            dep_names.append(t.name)
                            break
                lines.append(f"  Dependencies: {', '.join(dep_names)}")
            if task.notes:
                lines.append(f"  Notes: {task.notes}")
            lines.append("")

        # Critical path
        if self._show_critical_path:
            cp = self.critical_path
            cp_names = [t.name for t in p.tasks if t.id in cp]
            lines.append(f"  ── Critical Path: {' → '.join(cp_names[:4])} ──")
            lines.append("")

        lines.append("  [D]ependencies [M]ilestones [R]esources [C]ritical Path")
        lines.append("  [+]Add Task [X]Delete [↑↓]Select")
        return lines
