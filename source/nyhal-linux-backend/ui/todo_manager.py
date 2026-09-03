"""
Nyrqis Tasks — task manager with projects, priorities, and deadlines.

Features:
- Projects with color coding and descriptions
- Tasks with priority (P1-P4), due dates, and tags
- Recurring tasks (daily, weekly, monthly)
- Subtasks with completion tracking
- Kanban-style views (Todo, In Progress, Done)
- Overdue task highlighting
- Task search and filter by project/priority/tag
- Statistics dashboard
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Set
from datetime import datetime, timedelta


# ─── Data Classes ────────────────────────────────────────────────────────


class Priority(Enum):
    P1 = "P1 — Urgent"
    P2 = "P2 — High"
    P3 = "P3 — Medium"
    P4 = "P4 — Low"


PRIORITY_COLORS = {
    Priority.P1: "#E74C3C",
    Priority.P2: "#F39C12",
    Priority.P3: "#3498DB",
    Priority.P4: "#95A5A6",
}

PRIORITY_ICONS = {
    Priority.P1: "🔴",
    Priority.P2: "🟠",
    Priority.P3: "🔵",
    Priority.P4: "⚪",
}


class TaskStatus(Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class Recurrence(Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


@dataclass
class SubTask:
    """A subtask within a task."""
    title: str
    completed: bool = False
    subtask_id: str = ""

    def __post_init__(self):
        if not self.subtask_id:
            self.subtask_id = hashlib.md5(f"{self.title}{time.time()}".encode()).hexdigest()[:6]


@dataclass
class Task:
    """A single task."""
    title: str
    description: str = ""
    project_id: str = ""
    priority: Priority = Priority.P3
    status: TaskStatus = TaskStatus.TODO
    due_date: float = 0.0  # Unix timestamp, 0 = no due date
    created: float = field(default_factory=time.time)
    completed_at: float = 0.0
    tags: List[str] = field(default_factory=list)
    subtasks: List[SubTask] = field(default_factory=list)
    recurrence: Recurrence = Recurrence.NONE
    task_id: str = ""

    def __post_init__(self):
        if not self.task_id:
            self.task_id = hashlib.md5(f"{self.title}{self.created}".encode()).hexdigest()[:8]

    @property
    def is_overdue(self) -> bool:
        if self.due_date <= 0 or self.status == TaskStatus.DONE:
            return False
        return time.time() > self.due_date

    @property
    def days_until_due(self) -> int:
        if self.due_date <= 0:
            return 999
        diff = self.due_date - time.time()
        return int(diff / 86400)

    @property
    def due_str(self) -> str:
        if self.due_date <= 0:
            return ""
        days = self.days_until_due
        if days < 0:
            return f"overdue {-days}d"
        elif days == 0:
            return "due today"
        elif days == 1:
            return "due tomorrow"
        elif days <= 7:
            return f"due in {days}d"
        return datetime.fromtimestamp(self.due_date).strftime("%b %d")

    @property
    def due_date_str(self) -> str:
        if self.due_date <= 0:
            return "No due date"
        return datetime.fromtimestamp(self.due_date).strftime("%Y-%m-%d")

    @property
    def priority_icon(self) -> str:
        return PRIORITY_ICONS.get(self.priority, "⚪")

    @property
    def status_icon(self) -> str:
        icons = {
            TaskStatus.TODO: "☐",
            TaskStatus.IN_PROGRESS: "🔄",
            TaskStatus.DONE: "☑",
            TaskStatus.CANCELLED: "✖",
        }
        return icons.get(self.status, "☐")

    @property
    def completion_pct(self) -> float:
        if not self.subtasks:
            return 100.0 if self.status == TaskStatus.DONE else 0.0
        done = sum(1 for s in self.subtasks if s.completed)
        return (done / len(self.subtasks)) * 100

    @property
    def subtask_summary(self) -> str:
        if not self.subtasks:
            return ""
        done = sum(1 for s in self.subtasks if s.completed)
        return f"{done}/{len(self.subtasks)}"

    @property
    def created_str(self) -> str:
        return datetime.fromtimestamp(self.created).strftime("%Y-%m-%d")


@dataclass
class Project:
    """A task project."""
    name: str
    description: str = ""
    color: str = "#4A90D9"
    project_id: str = ""
    created: float = field(default_factory=time.time)
    archived: bool = False

    def __post_init__(self):
        if not self.project_id:
            self.project_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def display(self) -> str:
        return self.name


# ─── Todo Manager ────────────────────────────────────────────────────────


class TodoManager:
    """
    Task management application for Nyrqis OS.

    Manages tasks with projects, priorities, and deadlines.
    """

    def __init__(self):
        self._tasks: List[Task] = []
        self._projects: List[Project] = [
            Project("Nyrqis OS", "Main OS development", "#4A90D9"),
            Project("Documentation", "Docs and guides", "#2ECC71"),
            Project("Personal", "Personal tasks", "#9B59B6"),
            Project("Work", "Work-related", "#E74C3C"),
        ]

        # View state
        self._view_mode: str = "list"  # list, board, detail, stats
        self._selected_index: int = 0
        self._current_project: Optional[Project] = None
        self._filter_priority: Optional[Priority] = None
        self._filter_status: Optional[TaskStatus] = None
        self._filter_tag: str = ""
        self._search_query: str = ""
        self._board_column: int = 0  # 0=todo, 1=in_progress, 2=done

        # Edit state
        self._editing_task: Optional[Task] = None
        self._edit_field: str = "title"

        # Callbacks
        self._on_change: List[Callable] = []

        # Init sample data
        self._init_sample_tasks()

    def _init_sample_tasks(self) -> None:
        now = time.time()
        p = self._projects[0].project_id  # Nyrqis OS

        samples = [
            ("Implement Wayland protocol handler", "Handle xdg_wm_base and wl_output protocols", p,
             Priority.P1, TaskStatus.DONE, now - 86400 * 3, ["compositor", "wayland"]),
            ("Add Vulkan rendering backend", "Hardware-accelerated rendering via Vulkan", p,
             Priority.P1, TaskStatus.IN_PROGRESS, now + 86400 * 2, ["rendering", "gpu"]),
            ("Write API documentation", "Document all public interfaces", self._projects[1].project_id,
             Priority.P2, TaskStatus.IN_PROGRESS, now + 86400 * 5, ["docs"]),
            ("Fix memory leak in compositor", "Found during stress testing", p,
             Priority.P1, TaskStatus.TODO, now + 86400, ["bug", "compositor"]),
            ("Add multi-monitor profiles", "Save/load monitor configurations", p,
             Priority.P2, TaskStatus.TODO, now + 86400 * 7, ["multi-monitor"]),
            ("Review plugin system PR", "Community contribution review", p,
             Priority.P2, TaskStatus.TODO, now + 86400 * 3, ["plugins"]),
            ("Update package manager UI", "Improve search and filtering", p,
             Priority.P3, TaskStatus.TODO, now + 86400 * 14, ["ui"]),
            ("Grocery shopping", "Weekly grocery run", self._projects[2].project_id,
             Priority.P4, TaskStatus.TODO, now + 86400, ["errands"]),
            ("Prepare sprint demo", "Demo new features for stakeholders", self._projects[3].project_id,
             Priority.P2, TaskStatus.TODO, now + 86400 * 2, ["meeting"]),
            ("Write test suite for email client", "Coverage should reach 90%", self._projects[1].project_id,
             Priority.P3, TaskStatus.TODO, now + 86400 * 10, ["testing"]),
            ("Optimize terminal rendering", "Reduce CPU usage in scrollback", p,
             Priority.P3, TaskStatus.TODO, now + 86400 * 21, ["performance"]),
            ("Update AUR package", "Push v1.0 release to AUR", p,
             Priority.P2, TaskStatus.TODO, now + 86400, ["release"]),
            ("Fix login screen theme", "Theme not applied after update", p,
             Priority.P1, TaskStatus.TODO, now, ["bug", "ui"]),
            ("Learn Rust async/await", "Complete Rust Book chapter 17", self._projects[2].project_id,
             Priority.P4, TaskStatus.TODO, now + 86400 * 30, ["learning"]),
            ("Deploy staging environment", "Set up CI/CD for staging branch", self._projects[3].project_id,
             Priority.P2, TaskStatus.IN_PROGRESS, now + 86400 * 3, ["devops"]),
        ]

        for title, desc, proj, pri, status, due, tags in samples:
            subtasks = []
            if title == "Add Vulkan rendering backend":
                subtasks = [
                    SubTask("Create VulkanInstance wrapper", True),
                    SubTask("Implement swapchain management", True),
                    SubTask("Build render pipeline", False),
                    SubTask("Add shader compilation", False),
                ]
            elif title == "Write API documentation":
                subtasks = [
                    SubTask("Write compositor API docs", True),
                    SubTask("Write plugin API docs", False),
                    SubTask("Write theme API docs", False),
                ]

            self._tasks.append(Task(
                title=title, description=desc, project_id=proj,
                priority=pri, status=status, due_date=due,
                tags=tags, subtasks=subtasks,
                created=now - 86400 * 30,
            ))

    # ── Task CRUD ─────────────────────────────────────────────────────

    def create_task(self, title: str, project_id: str = "", priority: Priority = Priority.P3,
                    due_date: float = 0, tags: List[str] = None) -> Task:
        task = Task(
            title=title,
            project_id=project_id or (self._current_project.project_id if self._current_project else ""),
            priority=priority,
            due_date=due_date,
            tags=tags or [],
        )
        self._tasks.append(task)
        self._notify("change")
        return task

    def update_task(self, task_id: str, **kwargs) -> bool:
        task = self.get_task(task_id)
        if not task:
            return False
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        self._notify("change")
        return True

    def delete_task(self, task_id: str) -> bool:
        for i, t in enumerate(self._tasks):
            if t.task_id == task_id:
                self._tasks.pop(i)
                self._notify("change")
                return True
        return False

    def get_task(self, task_id: str) -> Optional[Task]:
        for t in self._tasks:
            if t.task_id == task_id:
                return t
        return None

    def complete_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task:
            task.status = TaskStatus.DONE
            task.completed_at = time.time()
            # Mark all subtasks complete
            for s in task.subtasks:
                s.completed = True
            self._notify("change")
            return True
        return False

    def toggle_subtask(self, task_id: str, subtask_id: str) -> bool:
        task = self.get_task(task_id)
        if task:
            for s in task.subtasks:
                if s.subtask_id == subtask_id:
                    s.completed = not s.completed
                    self._notify("change")
                    return True
        return False

    def add_subtask(self, task_id: str, title: str) -> Optional[SubTask]:
        task = self.get_task(task_id)
        if task:
            st = SubTask(title=title)
            task.subtasks.append(st)
            self._notify("change")
            return st
        return None

    # ── Queries ───────────────────────────────────────────────────────

    def get_tasks(self) -> List[Task]:
        tasks = list(self._tasks)

        if self._current_project:
            tasks = [t for t in tasks if t.project_id == self._current_project.project_id]
        if self._filter_priority:
            tasks = [t for t in tasks if t.priority == self._filter_priority]
        if self._filter_status:
            tasks = [t for t in tasks if t.status == self._filter_status]
        if self._filter_tag:
            tasks = [t for t in tasks if self._filter_tag in t.tags]
        if self._search_query:
            q = self._search_query.lower()
            tasks = [t for t in tasks if q in t.title.lower() or q in t.description.lower()]

        # Sort: overdue first, then by priority, then by due date
        priority_order = {Priority.P1: 0, Priority.P2: 1, Priority.P3: 2, Priority.P4: 3}
        tasks.sort(key=lambda t: (
            0 if t.is_overdue else 1,
            priority_order.get(t.priority, 4),
            t.due_date if t.due_date > 0 else 9999999999,
        ))

        return tasks

    def get_tasks_by_status(self, status: TaskStatus) -> List[Task]:
        tasks = [t for t in self._tasks if t.status == status]
        if self._current_project:
            tasks = [t for t in tasks if t.project_id == self._current_project.project_id]
        return tasks

    def search_tasks(self, query: str) -> List[Task]:
        self._search_query = query
        return self.get_tasks()

    # ── Projects ──────────────────────────────────────────────────────

    @property
    def projects(self) -> List[Project]:
        return list(self._projects)

    def create_project(self, name: str, description: str = "", color: str = "#4A90D9") -> Project:
        proj = Project(name=name, description=description, color=color)
        self._projects.append(proj)
        return proj

    def select_project(self, project_id: str) -> None:
        self._current_project = self.get_project(project_id)
        self._selected_index = 0

    def select_all_projects(self) -> None:
        self._current_project = None
        self._selected_index = 0

    def get_project(self, project_id: str) -> Optional[Project]:
        for p in self._projects:
            if p.project_id == project_id:
                return p
        return None

    @property
    def current_project(self) -> Optional[Project]:
        return self._current_project

    def project_task_count(self, project_id: str) -> int:
        return len([t for t in self._tasks if t.project_id == project_id and t.status != TaskStatus.DONE])

    # ── Statistics ────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        total = len(self._tasks)
        done = len([t for t in self._tasks if t.status == TaskStatus.DONE])
        in_progress = len([t for t in self._tasks if t.status == TaskStatus.IN_PROGRESS])
        overdue = len([t for t in self._tasks if t.is_overdue])

        by_priority = {}
        for p in Priority:
            by_priority[p.value] = len([t for t in self._tasks if t.priority == p and t.status != TaskStatus.DONE])

        return {
            "total": total,
            "completed": done,
            "in_progress": in_progress,
            "todo": total - done - in_progress,
            "overdue": overdue,
            "completion_rate": (done / total * 100) if total > 0 else 0,
            "by_priority": by_priority,
        }

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        tasks = self.get_tasks()
        self._selected_index = min(len(tasks) - 1, self._selected_index + 1)

    def open_selected(self) -> Optional[Task]:
        tasks = self.get_tasks()
        if 0 <= self._selected_index < len(tasks):
            task = tasks[self._selected_index]
            self._editing_task = task
            self._view_mode = "detail"
            return task
        return None

    # ── View ──────────────────────────────────────────────────────────

    def set_view(self, mode: str) -> None:
        self._view_mode = mode

    def cycle_view(self) -> str:
        views = ["list", "board", "stats"]
        idx = views.index(self._view_mode) if self._view_mode in views else 0
        self._view_mode = views[(idx + 1) % len(views)]
        return self._view_mode

    @property
    def view_mode(self) -> str:
        return self._view_mode

    # ── Rendering ─────────────────────────────────────────────────────

    def render_list(self, width: int = 60) -> List[str]:
        lines = []
        proj_name = self._current_project.name if self._current_project else "All Projects"
        lines.append(f" ✅ Tasks — {proj_name}")
        lines.append("─" * width)

        stats = self.get_stats()
        lines.append(f" 📋 {stats['total']} total · 🔄 {stats['in_progress']} active · ✅ {stats['completed']} done · 🔴 {stats['overdue']} overdue")
        lines.append("─" * width)

        tasks = self.get_tasks()
        if not tasks:
            lines.append("  No tasks. Press N to create one.")
        else:
            for i, task in enumerate(tasks):
                marker = "▸" if i == self._selected_index else " "
                proj = ""
                for p in self._projects:
                    if p.project_id == task.project_id:
                        proj = f" [{p.name}]"
                        break

                # Main line
                due = f" {task.due_str}" if task.due_str else ""
                overdue = " ⚠️" if task.is_overdue else ""
                line = f"{marker} {task.priority_icon} {task.status_icon} {task.title[:width - 20]}"
                lines.append(line[:width])

                # Meta line
                meta = f"   {proj}{due}{overdue}"
                if task.subtask_summary:
                    meta += f" 📎 {task.subtask_summary}"
                if task.tags:
                    meta += f" 🏷️ {', '.join(task.tags[:3])}"
                lines.append(meta[:width])
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Detail  N:New  S:Status  D:Delete")
        return lines

    def render_board(self, width: int = 72) -> List[str]:
        """Render kanban board view."""
        lines = []
        lines.append(" ✅ Task Board")
        lines.append("─" * width)

        col_width = width // 3
        todo = self.get_tasks_by_status(TaskStatus.TODO)
        in_prog = self.get_tasks_by_status(TaskStatus.IN_PROGRESS)
        done = self.get_tasks_by_status(TaskStatus.DONE)

        # Headers
        lines.append(f" {'TODO':<{col_width}} {'IN PROGRESS':<{col_width}} {'DONE':<{col_width}}")
        lines.append("─" * width)

        # Tasks in columns
        max_rows = max(len(todo), len(in_prog), len(done), 1)
        for row in range(min(max_rows, 8)):
            cols = []
            for task_list in [todo, in_prog, done]:
                if row < len(task_list):
                    t = task_list[row]
                    cell = f"{t.priority_icon} {t.title[:col_width - 4]}"
                else:
                    cell = ""
                cols.append(cell[:col_width])
            lines.append(f" {cols[0]} {cols[1]} {cols[2]}")

        lines.append("─" * width)
        lines.append(" L:List  ←→:Column  N:New  Enter:Detail")
        return lines

    def render_detail(self, width: int = 60) -> List[str]:
        lines = []
        task = self._editing_task
        if not task:
            return ["No task selected"]

        lines.append(f" {task.priority_icon} {task.title}")
        lines.append("─" * width)
        lines.append(f"  Status:    {task.status.value.replace('_', ' ').title()}")
        lines.append(f"  Priority:  {task.priority.value}")
        lines.append(f"  Due:       {task.due_date_str}")
        if task.is_overdue:
            lines.append(f"  ⚠️  OVERDUE by {-task.days_until_due} days")
        lines.append(f"  Project:   {self._get_project_name(task.project_id)}")
        lines.append(f"  Tags:      {', '.join(task.tags) if task.tags else 'none'}")
        lines.append(f"  Created:   {task.created_str}")
        if task.completed_at:
            lines.append(f"  Completed: {datetime.fromtimestamp(task.completed_at).strftime('%Y-%m-%d')}")
        lines.append(f"  Recur:     {task.recurrence.value}")
        lines.append("─" * width)

        if task.description:
            lines.append(f"  {task.description}")
            lines.append("")

        # Subtasks
        if task.subtasks:
            lines.append("  Subtasks:")
            for st in task.subtasks:
                check = "☑" if st.completed else "☐"
                lines.append(f"    {check} {st.title}")
            lines.append(f"  Progress: {task.completion_pct:.0f}%")

        lines.append("─" * width)
        lines.append(" Esc:Back  S:Status  P:Priority  T:Tag  +:Subtask")
        return lines

    def render_stats(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 📊 Task Statistics")
        lines.append("─" * width)

        stats = self.get_stats()

        # Overview
        lines.append(f"  Total tasks:     {stats['total']}")
        lines.append(f"  Completed:       {stats['completed']}")
        lines.append(f"  In progress:     {stats['in_progress']}")
        lines.append(f"  To do:           {stats['todo']}")
        lines.append(f"  Overdue:         {stats['overdue']}")
        lines.append(f"  Completion rate: {stats['completion_rate']:.0f}%")
        lines.append("")

        # Progress bar
        bar_width = 40
        filled = int(stats['completion_rate'] / 100 * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        lines.append(f"  [{bar}] {stats['completion_rate']:.0f}%")
        lines.append("")

        # By priority
        lines.append("  ── By Priority ──")
        for pri_name, count in stats["by_priority"].items():
            icon = "🔴" if "Urgent" in pri_name else "🟠" if "High" in pri_name else "🔵" if "Medium" in pri_name else "⚪"
            lines.append(f"  {icon} {pri_name}: {count}")

        lines.append("─" * width)
        lines.append(" L:List  B:Board  ←→:Navigate")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        if self._view_mode == "detail":
            return self.render_detail(width)
        elif self._view_mode == "board":
            return self.render_board(width)
        elif self._view_mode == "stats":
            return self.render_stats(width)
        return self.render_list(width)

    def _get_project_name(self, project_id: str) -> str:
        for p in self._projects:
            if p.project_id == project_id:
                return p.name
        return "None"

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "detail":
            return self._handle_detail_key(key)
        return self._handle_list_key(key)

    def _handle_list_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self.open_selected()
            return "detail"
        elif key == "n":
            task = self.create_task("New Task")
            self._editing_task = task
            self._view_mode = "detail"
            return "new"
        elif key == "s":
            tasks = self.get_tasks()
            if 0 <= self._selected_index < len(tasks):
                t = tasks[self._selected_index]
                statuses = [TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.DONE]
                idx = statuses.index(t.status) if t.status in statuses else 0
                t.status = statuses[(idx + 1) % len(statuses)]
                if t.status == TaskStatus.DONE:
                    t.completed_at = time.time()
            return "cycle_status"
        elif key == "d":
            tasks = self.get_tasks()
            if 0 <= self._selected_index < len(tasks):
                self.delete_task(tasks[self._selected_index].task_id)
            return "delete"
        elif key == "/":
            return "search"
        elif key == "v":
            self.cycle_view()
            return "cycle_view"
        return None

    def _handle_detail_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._editing_task = None
            self._view_mode = "list"
            return "back"
        elif key == "s":
            if self._editing_task:
                statuses = [TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.DONE, TaskStatus.CANCELLED]
                idx = statuses.index(self._editing_task.status) if self._editing_task.status in statuses else 0
                self._editing_task.status = statuses[(idx + 1) % len(statuses)]
                if self._editing_task.status == TaskStatus.DONE:
                    self._editing_task.completed_at = time.time()
            return "cycle_status"
        elif key == "p":
            if self._editing_task:
                pris = list(Priority)
                idx = pris.index(self._editing_task.priority)
                self._editing_task.priority = pris[(idx + 1) % len(pris)]
            return "cycle_priority"
        elif key == "c":
            if self._editing_task and self._editing_task.status != TaskStatus.DONE:
                self.complete_task(self._editing_task.task_id)
                self._editing_task = None
                self._view_mode = "list"
            return "complete"
        elif key == "+":
            if self._editing_task:
                self.add_subtask(self._editing_task.task_id, "New subtask")
            return "add_subtask"
        return None

    # ── Callbacks ─────────────────────────────────────────────────────

    def on_change(self, cb: Callable) -> None:
        self._on_change.append(cb)

    def _notify(self, event: str) -> None:
        for cb in self._on_change:
            try:
                cb()
            except Exception:
                pass
