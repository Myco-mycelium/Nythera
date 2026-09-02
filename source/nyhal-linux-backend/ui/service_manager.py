#!/usr/bin/env python3
"""service_manager — Nyrqis service/daemon manager UI.

A systemd-style service management interface:

- List all services with status (running/stopped/failed)
- Start, stop, restart, enable, disable services
- Service detail view with logs, dependencies, resource usage
- Service status dashboard with counts
- Search and filter services
- Service types: service, timer, socket, mount
- Auto-refresh for status updates
- Service dependency tree
- Boot order visualization
- Resource usage (CPU, memory) for running services
- Journal log tail per service
- Keyboard navigation
- PIL rendering

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class ServiceState(Enum):
    """Service states."""
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    STARTING = "starting"
    STOPPING = "stopping"
    RELOADING = "reloading"
    UNKNOWN = "unknown"

    @property
    def color(self) -> Tuple[int, int, int]:
        return {
            ServiceState.RUNNING: (80, 200, 120),
            ServiceState.STOPPED: (160, 160, 160),
            ServiceState.FAILED: (220, 80, 80),
            ServiceState.STARTING: (220, 180, 60),
            ServiceState.STOPPING: (220, 140, 60),
            ServiceState.RELOADING: (80, 140, 220),
            ServiceState.UNKNOWN: (120, 120, 120),
        }[self]


class ServiceType(Enum):
    """Service unit types."""
    SERVICE = "service"
    TIMER = "timer"
    SOCKET = "socket"
    MOUNT = "mount"
    TARGET = "target"
    PATH = "path"


@dataclass
class ServiceInfo:
    """Information about a service/daemon."""
    id: str
    name: str
    display_name: str = ""
    description: str = ""
    service_type: ServiceType = ServiceType.SERVICE
    state: ServiceState = ServiceState.STOPPED
    enabled: bool = False         # starts at boot
    active: bool = False          # currently running
    pid: int = 0
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    uptime_seconds: float = 0.0
    restart_count: int = 0
    dependencies: List[str] = field(default_factory=list)  # service IDs
    required_by: List[str] = field(default_factory=list)
    config_path: str = ""
    log_lines: List[str] = field(default_factory=list)
    last_exit_code: int = 0
    auto_restart: bool = True

    @property
    def display_state(self) -> str:
        return self.state.value.upper()

    @property
    def status_dot(self) -> str:
        return "●" if self.active else "○"

    @property
    def uptime(self) -> str:
        if self.uptime_seconds <= 0:
            return "-"
        secs = self.uptime_seconds
        days = int(secs // 86400)
        hours = int((secs % 86400) // 3600)
        mins = int((secs % 3600) // 60)
        if days > 0:
            return f"{days}d {hours}h"
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"


@dataclass
class ServiceFilter:
    """Filter criteria for service list."""
    search: str = ""
    state: Optional[ServiceState] = None
    service_type: Optional[ServiceType] = None
    enabled_only: bool = False
    running_only: bool = False


# ---------------------------------------------------------------------------
# Service manager
# ---------------------------------------------------------------------------

class ServiceManager:
    """Service management UI.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session.
    """

    def __init__(self, session=None) -> None:
        self._session = session
        self._services: Dict[str, ServiceInfo] = {}
        self._filter = ServiceFilter()
        self._visible = False
        self._selected_index: int = 0
        self._selected_service: Optional[str] = None
        self._view: str = "list"  # list, detail, logs
        self._scroll_offset: int = 0
        self._sort_by: str = "name"
        self._auto_refresh: bool = True
        self._last_refresh: float = 0.0
        self._callbacks: List[Callable] = []

        # Seed with sample services
        self._seed_services()

    def _seed_services(self) -> None:
        """Seed with sample systemd-style services."""
        services = [
            ("nyrqis-daemon", "nyrqis-daemon", "Nyrqis Display Daemon",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 1234, 0.5, 12.4, 86400, 0),
            ("nyrqis-shell", "nyrqis-shell", "Nyrqis Desktop Shell",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 1235, 1.2, 45.8, 86400, 0),
            ("nyrqis-compositor", "nyrqis-compositor", "Nyrqis Wayland Compositor",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 1236, 3.5, 89.2, 86400, 0),
            ("nyrqis-notifications", "nyrqis-notifications", "Notification Service",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 1237, 0.1, 8.3, 86400, 0),
            ("nyrqis-network", "nyrqis-network", "Network Manager",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 456, 0.2, 15.6, 172800, 0),
            ("nyrqis-bluetooth", "nyrqis-bluetooth", "Bluetooth Service",
             ServiceType.SERVICE, ServiceState.STOPPED, True, False, 0, 0, 0, 0, 0),
            ("nyrqis-upower", "nyrqis-upower", "Power Management",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 460, 0.05, 5.2, 172800, 0),
            ("nyrqis-logind", "nyrqis-logind", "Login Manager",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 300, 0.1, 10.5, 172800, 0),
            ("nyrqis-timer-daily", "nyrqis-timer-daily", "Daily Maintenance Timer",
             ServiceType.TIMER, ServiceState.RUNNING, True, True, 0, 0, 0, 86400, 0),
            ("nyrqis-socket-ipc", "nyrqis-socket-ipc", "IPC Socket",
             ServiceType.SOCKET, ServiceState.RUNNING, True, True, 0, 0, 0.1, 86400, 0),
            ("nyrqis-mount-tmp", "nyrqis-mount-tmp", "Tmp Mount",
             ServiceType.MOUNT, ServiceState.RUNNING, True, True, 0, 0, 0, 172800, 0),
            ("nyrqis-failed-worker", "nyrqis-failed-worker", "Background Worker",
             ServiceType.SERVICE, ServiceState.FAILED, False, False, 0, 0, 0, 0, 3),
            ("systemd-journald", "systemd-journald", "Journal Service",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 200, 0.3, 25.0, 172800, 0),
            ("systemd-udevd", "systemd-udevd", "Device Manager",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 201, 0.1, 8.0, 172800, 0),
            ("dbus", "dbus", "D-Bus Message Bus",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 250, 0.2, 12.0, 172800, 0),
            ("sshd", "sshd", "SSH Server",
             ServiceType.SERVICE, ServiceState.STOPPED, False, False, 0, 0, 0, 0, 0),
            ("cron", "cron", "Job Scheduler",
             ServiceType.SERVICE, ServiceState.RUNNING, True, True, 500, 0.01, 3.0, 172800, 0),
        ]

        for sid, name, desc, stype, state, enabled, active, pid, cpu, mem, uptime, restarts in services:
            deps = []
            if name == "nyrqis-shell":
                deps = ["nyrqis-daemon", "nyrqis-compositor"]
            elif name == "nyrqis-notifications":
                deps = ["nyrqis-daemon"]
            elif name == "nyrqis-compositor":
                deps = ["nyrqis-daemon"]

            self._services[sid] = ServiceInfo(
                id=sid, name=name, display_name=desc,
                service_type=stype, state=state,
                enabled=enabled, active=active,
                pid=pid, cpu_percent=cpu, memory_mb=mem,
                uptime_seconds=uptime, restart_count=restarts,
                dependencies=deps,
                config_path=f"/etc/nyrqis/{name}.conf" if stype == ServiceType.SERVICE else "",
            )

    # -- View management -----------------------------------------------

    def show(self) -> None:
        self._visible = True
        self._view = "list"
        self._selected_index = 0
        self._dispatch("shown")

    def hide(self) -> None:
        self._visible = False
        self._dispatch("hidden")

    def toggle(self) -> bool:
        if self._visible:
            self.hide()
        else:
            self.show()
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    def set_view(self, view: str) -> None:
        self._view = view

    @property
    def current_view(self) -> str:
        return self._view

    # -- Filter/sort ---------------------------------------------------

    def search(self, query: str) -> None:
        self._filter.search = query
        self._selected_index = 0

    def filter_state(self, state: Optional[ServiceState]) -> None:
        self._filter.state = state
        self._selected_index = 0

    def filter_type(self, stype: Optional[ServiceType]) -> None:
        self._filter.service_type = stype
        self._selected_index = 0

    def toggle_running_only(self) -> None:
        self._filter.running_only = not self._filter.running_only
        self._selected_index = 0

    def set_sort(self, key: str) -> None:
        self._sort_by = key

    # -- Service list --------------------------------------------------

    def get_services(self) -> List[ServiceInfo]:
        """Get filtered and sorted service list."""
        services = list(self._services.values())

        if self._filter.search:
            q = self._filter.search.lower()
            services = [s for s in services
                        if q in s.name.lower() or q in s.display_name.lower()]

        if self._filter.state:
            services = [s for s in services if s.state == self._filter.state]

        if self._filter.service_type:
            services = [s for s in services if s.service_type == self._filter.service_type]

        if self._filter.running_only:
            services = [s for s in services if s.active]

        # Sort
        if self._sort_by == "name":
            services.sort(key=lambda s: s.name.lower())
        elif self._sort_by == "state":
            services.sort(key=lambda s: s.state.value)
        elif self._sort_by == "cpu":
            services.sort(key=lambda s: s.cpu_percent, reverse=True)
        elif self._sort_by == "memory":
            services.sort(key=lambda s: s.memory_mb, reverse=True)

        return services

    @property
    def services(self) -> List[ServiceInfo]:
        return self.get_services()

    @property
    def service_count(self) -> int:
        return len(self.get_services())

    @property
    def running_count(self) -> int:
        return sum(1 for s in self._services.values() if s.state == ServiceState.RUNNING)

    @property
    def failed_count(self) -> int:
        return sum(1 for s in self._services.values() if s.state == ServiceState.FAILED)

    @property
    def enabled_count(self) -> int:
        return sum(1 for s in self._services.values() if s.enabled)

    # -- Service actions -----------------------------------------------

    def select_service(self, service_id: str) -> bool:
        if service_id in self._services:
            self._selected_service = service_id
            self._view = "detail"
            return True
        return False

    def start_service(self, service_id: str) -> bool:
        svc = self._services.get(service_id)
        if svc is None or svc.state == ServiceState.RUNNING:
            return False
        svc.state = ServiceState.RUNNING
        svc.active = True
        svc.pid = 10000 + hash(service_id) % 50000
        svc.uptime_seconds = 0
        self._dispatch("service_started", service_id)
        return True

    def stop_service(self, service_id: str) -> bool:
        svc = self._services.get(service_id)
        if svc is None or svc.state != ServiceState.RUNNING:
            return False
        svc.state = ServiceState.STOPPED
        svc.active = False
        svc.pid = 0
        svc.uptime_seconds = 0
        self._dispatch("service_stopped", service_id)
        return True

    def restart_service(self, service_id: str) -> bool:
        svc = self._services.get(service_id)
        if svc is None:
            return False
        svc.restart_count += 1
        svc.state = ServiceState.RUNNING
        svc.active = True
        svc.pid = 10000 + hash(service_id) % 50000
        svc.uptime_seconds = 0
        self._dispatch("service_restarted", service_id)
        return True

    def enable_service(self, service_id: str) -> bool:
        svc = self._services.get(service_id)
        if svc:
            svc.enabled = True
            self._dispatch("service_enabled", service_id)
            return True
        return False

    def disable_service(self, service_id: str) -> bool:
        svc = self._services.get(service_id)
        if svc:
            svc.enabled = False
            self._dispatch("service_disabled", service_id)
            return True
        return False

    def get_service(self, service_id: str) -> Optional[ServiceInfo]:
        return self._services.get(service_id)

    def get_dependencies(self, service_id: str) -> List[ServiceInfo]:
        """Get services this service depends on."""
        svc = self._services.get(service_id)
        if svc is None:
            return []
        return [self._services[d] for d in svc.dependencies
                if d in self._services]

    def get_dependents(self, service_id: str) -> List[ServiceInfo]:
        """Get services that depend on this service."""
        svc = self._services.get(service_id)
        if svc is None:
            return []
        result = []
        for s in self._services.values():
            if service_id in s.dependencies:
                result.append(s)
        return result

    # -- Navigation ----------------------------------------------------

    def navigate_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def navigate_down(self) -> None:
        max_idx = len(self.get_services()) - 1
        self._selected_index = min(max_idx, self._selected_index + 1)

    def activate_selected(self) -> Optional[ServiceInfo]:
        services = self.get_services()
        if 0 <= self._selected_index < len(services):
            svc = services[self._selected_index]
            self.select_service(svc.id)
            return svc
        return None

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def selected_service(self) -> Optional[ServiceInfo]:
        if self._selected_service:
            return self._services.get(self._selected_service)
        return None

    # -- Dashboard -----------------------------------------------------

    def dashboard(self) -> Dict[str, Any]:
        """Get dashboard summary data."""
        total = len(self._services)
        running = self.running_count
        failed = self.failed_count
        stopped = sum(1 for s in self._services.values()
                      if s.state == ServiceState.STOPPED)
        enabled = self.enabled_count
        total_cpu = sum(s.cpu_percent for s in self._services.values())
        total_mem = sum(s.memory_mb for s in self._services.values())

        return {
            "total": total,
            "running": running,
            "stopped": stopped,
            "failed": failed,
            "enabled": enabled,
            "total_cpu_percent": round(total_cpu, 1),
            "total_memory_mb": round(total_mem, 1),
            "health": "healthy" if failed == 0 else f"{failed} failed",
        }

    # -- Rendering -----------------------------------------------------

    def render(self, width: int = 1920, height: int = 1080) -> Any:
        """Render the service manager."""
        if not self._visible:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
            font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15)
            font_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
            font_mono = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
        except (OSError, IOError):
            font = font_mono = font_bold = font_title = font_small = ImageFont.load_default()

        # Panel
        px, py = 80, 40
        pw, ph = width - 160, height - 80
        draw.rounded_rectangle(
            [px, py, px + pw, py + ph],
            radius=16, fill=(22, 22, 28, 240), outline=(60, 60, 70))

        # Title
        draw.text((px + 20, py + 16), "Service Manager",
                  fill=(220, 220, 220), font=font_title)

        # Dashboard cards
        dash = self.dashboard()
        cards = [
            ("Running", str(dash["running"]), (80, 200, 120)),
            ("Stopped", str(dash["stopped"]), (160, 160, 160)),
            ("Failed", str(dash["failed"]), (220, 80, 80)),
            ("Total", str(dash["total"]), (140, 140, 140)),
        ]
        card_x = px + pw - 400
        for label, value, color in cards:
            draw.rounded_rectangle(
                [card_x, py + 10, card_x + 88, py + 40],
                radius=6, fill=(35, 35, 45))
            draw.text((card_x + 10, py + 12), value,
                      fill=color, font=font_bold)
            draw.text((card_x + 10, py + 28), label,
                      fill=(120, 120, 120), font=font_small)
            card_x += 96

        # Resource usage
        draw.text((px + 20, py + 50), f"CPU: {dash['total_cpu_percent']}%",
                  fill=(100, 140, 180), font=font_small)
        draw.text((px + 120, py + 50), f"Memory: {dash['total_memory_mb']:.0f} MB",
                  fill=(100, 140, 180), font=font_small)
        health_color = (80, 200, 120) if dash["failed"] == 0 else (220, 80, 80)
        draw.text((px + 280, py + 50), f"Health: {dash['health']}",
                  fill=health_color, font=font_small)

        # Service list
        sy = py + 72
        services = self.get_services()
        visible_count = min(len(services), (ph - 100) // 28)

        # Header
        draw.text((px + 20, sy), "Name", fill=(100, 100, 120), font=font_small)
        draw.text((px + 300, sy), "State", fill=(100, 100, 120), font=font_small)
        draw.text((px + 400, sy), "Type", fill=(100, 100, 120), font=font_small)
        draw.text((px + 480, sy), "Boot", fill=(100, 100, 120), font=font_small)
        draw.text((px + 540, sy), "CPU%", fill=(100, 100, 120), font=font_small)
        draw.text((px + 610, sy), "Memory", fill=(100, 100, 120), font=font_small)
        draw.text((px + 700, sy), "Uptime", fill=(100, 100, 120), font=font_small)
        sy += 18

        for i, svc in enumerate(services[self._scroll_offset:self._scroll_offset + visible_count]):
            ry = sy + i * 28
            is_selected = (i + self._scroll_offset == self._selected_index)

            if is_selected:
                draw.rounded_rectangle(
                    [px + 16, ry - 2, px + pw - 16, ry + 22],
                    radius=4, fill=(40, 40, 55))

            # Status dot
            draw.ellipse([px + 20, ry + 4, px + 30, ry + 14],
                         fill=svc.state.color)

            # Name
            draw.text((px + 36, ry), svc.display_name[:30],
                      fill=(220, 220, 220), font=font)

            # State
            draw.text((px + 300, ry), svc.display_state,
                      fill=svc.state.color, font=font)

            # Type
            draw.text((px + 400, ry), svc.service_type.value,
                      fill=(140, 140, 140), font=font_small)

            # Enabled
            boot = "✓" if svc.enabled else "✗"
            draw.text((px + 480, ry), boot,
                      fill=(80, 200, 120) if svc.enabled else (220, 80, 80),
                      font=font)

            # CPU
            if svc.cpu_percent > 0:
                draw.text((px + 540, ry), f"{svc.cpu_percent:.1f}",
                          fill=(200, 200, 200), font=font_mono)

            # Memory
            if svc.memory_mb > 0:
                draw.text((px + 610, ry), f"{svc.memory_mb:.1f}M",
                          fill=(200, 200, 200), font=font_mono)

            # Uptime
            draw.text((px + 700, ry), svc.uptime,
                      fill=(140, 140, 140), font=font_small)

        # Scrollbar
        if len(services) > visible_count:
            sb_x = px + pw - 16
            sb_h = ph - 100
            sb_thumb_h = max(20, int(sb_h * visible_count / len(services)))
            sb_thumb_y = sy + int(sb_h * self._scroll_offset / len(services))
            draw.rounded_rectangle(
                [sb_x, sy, sb_x + 8, sy + sb_h],
                radius=4, fill=(40, 40, 50))
            draw.rounded_rectangle(
                [sb_x, sb_thumb_y, sb_x + 8, sb_thumb_y + sb_thumb_h],
                radius=4, fill=(80, 80, 100))

        # Status bar
        draw.rectangle([px, py + ph - 22, px + pw, py + ph],
                       fill=(28, 28, 36))
        draw.text((px + 12, py + ph - 18),
                  f"{len(services)} services  |  "
                  f"{self.running_count} running  |  "
                  f"{self.failed_count} failed",
                  fill=(120, 120, 120), font=font_small)

        return img

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, data: Any = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (
            f"ServiceManager(services={len(self._services)}, "
            f"running={self.running_count}, "
            f"failed={self.failed_count})"
        )


__all__ = [
    "ServiceManager", "ServiceInfo", "ServiceState", "ServiceType",
    "ServiceFilter",
]
