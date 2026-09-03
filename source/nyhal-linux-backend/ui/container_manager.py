"""Container Manager — Docker/Podman integration, resource monitoring for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time


class ContainerRuntime(Enum):
    DOCKER = "Docker"
    PODMAN = "Podman"
    LXC = "LXC"
    CONTAINERD = "containerd"


class ContainerState(Enum):
    RUNNING = "Running"
    STOPPED = "Stopped"
    PAUSED = "Paused"
    RESTARTING = "Restarting"
    CREATED = "Created"
    DEAD = "Dead"
    EXITED = "Exited"


class ImageType(Enum):
    DOCKERFILE = "Dockerfile"
    OCI = "OCI"
    WAVELENGTH = "Wavelength"
    BUILD = "BuildKit"


@dataclass
class Port:
    host_port: int = 0
    container_port: int = 0
    protocol: str = "tcp"
    host_ip: str = "0.0.0.0"

    @property
    def mapping(self) -> str:
        return f"{self.host_ip}:{self.host_port}→{self.container_port}/{self.protocol}"


@dataclass
class Volume:
    host_path: str = ""
    container_path: str = ""
    mode: str = "rw"  # rw, ro

    @property
    def mapping(self) -> str:
        return f"{self.host_path}:{self.container_path}:{self.mode}"


@dataclass
class ResourceUsage:
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_limit_mb: float = 0.0
    memory_percent: float = 0.0
    network_rx_bytes: int = 0
    network_tx_bytes: int = 0
    disk_read_bytes: int = 0
    disk_write_bytes: int = 0
    pids: int = 0
    uptime_s: float = 0.0

    @property
    def cpu_bar(self) -> str:
        filled = int(self.cpu_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def memory_bar(self) -> str:
        filled = int(self.memory_percent / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def memory_str(self) -> str:
        if self.memory_mb < 1024:
            return f"{self.memory_mb:.0f} MB"
        return f"{self.memory_mb / 1024:.1f} GB"

    @property
    def network_str(self) -> str:
        rx = self._fmt_bytes(self.network_rx_bytes)
        tx = self._fmt_bytes(self.network_tx_bytes)
        return f"↓{rx} ↑{tx}"

    @property
    def uptime_str(self) -> str:
        h = int(self.uptime_s // 3600)
        m = int((self.uptime_s % 3600) // 60)
        return f"{h}h {m}m"

    @staticmethod
    def _fmt_bytes(b: int) -> str:
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        return f"{b / (1024 * 1024 * 1024):.1f} GB"


@dataclass
class Container:
    id: str
    name: str
    image: str = ""
    state: ContainerState = ContainerState.RUNNING
    runtime: ContainerRuntime = ContainerRuntime.DOCKER
    created: float = 0.0
    started: float = 0.0
    ports: List[Port] = field(default_factory=list)
    volumes: List[Volume] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)
    resources: ResourceUsage = field(default_factory=ResourceUsage)
    restart_policy: str = "unless-stopped"
    network_mode: str = "bridge"
    privileged: bool = False
    auto_remove: bool = False

    @property
    def state_icon(self) -> str:
        icons = {
            ContainerState.RUNNING: "🟢",
            ContainerState.STOPPED: "🔴",
            ContainerState.PAUSED: "⏸",
            ContainerState.RESTARTING: "🔄",
            ContainerState.CREATED: "⬜",
            ContainerState.DEAD: "💀",
            ContainerState.EXITED: "⏹",
        }
        return icons.get(self.state, "?")

    @property
    def short_id(self) -> str:
        return self.id[:12]

    @property
    def ports_str(self) -> str:
        if not self.ports:
            return ""
        return ", ".join(p.mapping for p in self.ports[:3])

    @property
    def uptime_s(self) -> float:
        if self.started > 0:
            return time.time() - self.started
        return 0

    @property
    def age_str(self) -> str:
        if self.created <= 0:
            return "N/A"
        age = time.time() - self.created
        if age < 3600:
            return f"{age / 60:.0f}m ago"
        elif age < 86400:
            return f"{age / 3600:.0f}h ago"
        return f"{age / 86400:.0f}d ago"


@dataclass
class Image:
    id: str
    name: str
    tag: str = "latest"
    size_mb: float = 0.0
    created: float = 0.0
    image_type: ImageType = ImageType.DOCKERFILE
    layers: int = 0
    used_by: int = 0

    @property
    def full_name(self) -> str:
        return f"{self.name}:{self.tag}"

    @property
    def size_str(self) -> str:
        if self.size_mb < 1024:
            return f"{self.size_mb:.0f} MB"
        return f"{self.size_mb / 1024:.1f} GB"

    @property
    def type_icon(self) -> str:
        icons = {
            ImageType.DOCKERFILE: "🐳",
            ImageType.OCI: "📦",
            ImageType.WAVELENGTH: "🌊",
            ImageType.BUILD: "🔨",
        }
        return icons.get(self.image_type, "?")


@dataclass
class Volume2:
    name: str
    driver: str = "local"
    mountpoint: str = ""
    size_mb: float = 0.0
    containers: List[str] = field(default_factory=list)
    created: float = 0.0

    @property
    def size_str(self) -> str:
        if self.size_mb < 1024:
            return f"{self.size_mb:.0f} MB"
        return f"{self.size_mb / 1024:.1f} GB"


@dataclass
class Network:
    name: str
    driver: str = "bridge"
    subnet: str = "172.17.0.0/16"
    gateway: str = "172.17.0.1"
    containers: List[str] = field(default_factory=list)
    ipam_driver: str = "default"

    @property
    def container_count(self) -> int:
        return len(self.containers)


@dataclass
class BuildLog:
    timestamp: float = 0.0
    step: int = 0
    message: str = ""
    status: str = ""  # building, success, error

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))


class ContainerManager:
    def __init__(self):
        self._containers: List[Container] = []
        self._images: List[Image] = []
        self._volumes: List[Volume2] = []
        self._networks: List[Network] = []
        self._selected_container: int = 0
        self._selected_image: int = 0
        self._view_mode: str = "containers"
        self._runtime: ContainerRuntime = ContainerRuntime.DOCKER
        self._docker_socket: str = "/var/run/docker.sock"
        self._build_logs: List[BuildLog] = []
        self._history: List[str] = []
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        self._containers = [
            Container("a1b2c3d4e5f6", "nyrqis-compositor", "nyrqis/compositor:latest",
                      ContainerState.RUNNING, ContainerRuntime.DOCKER,
                      now - 86400 * 5, now - 3600,
                      [Port(8080, 8080), Port(8443, 8443)],
                      [Volume("/dev/dri", "/dev/dri", "rw")],
                      {"WAYLAND_DISPLAY": "wayland-0", "GPU_DRIVER": "nvidia"},
                      resources=ResourceUsage(12.5, 256, 2048, 12.5, 1024 * 1024 * 50, 1024 * 1024 * 120, 1024 * 1024 * 500, 1024 * 1024 * 100, 24, 86400),
                      privileged=True),
            Container("b2c3d4e5f6a7", "nyrkis-shell", "nyrqis/shell:latest",
                      ContainerState.RUNNING, ContainerRuntime.DOCKER,
                      now - 86400 * 3, now - 1800,
                      [Port(3000, 3000)],
                      [],
                      {"DISPLAY": ":0"},
                      resources=ResourceUsage(3.2, 128, 1024, 12.5, 1024 * 200, 1024 * 80, 1024 * 100, 1024 * 50, 8, 5400)),
            Container("c3d4e5f6a7b8", "postgres-db", "postgres:16-alpine",
                      ContainerState.RUNNING, ContainerRuntime.DOCKER,
                      now - 86400 * 10, now - 86400 * 2,
                      [Port(5432, 5432)],
                      [Volume("pgdata", "/var/lib/postgresql/data", "rw")],
                      {"POSTGRES_PASSWORD": "***", "POSTGRES_DB": "nyrqis"},
                      resources=ResourceUsage(8.4, 512, 4096, 12.5, 1024 * 500, 1024 * 300, 1024 * 1024 * 2, 1024 * 500, 12, 172800)),
            Container("d4e5f6a7b8c9", "redis-cache", "redis:7-alpine",
                      ContainerState.RUNNING, ContainerRuntime.DOCKER,
                      now - 86400 * 10, now - 86400 * 2,
                      [Port(6379, 6379)],
                      [],
                      resources=ResourceUsage(1.1, 32, 512, 6.25, 1024 * 100, 1024 * 50, 1024 * 20, 1024 * 10, 4, 172800)),
            Container("e5f6a7b8c9d0", "nginx-proxy", "nginx:alpine",
                      ContainerState.RUNNING, ContainerRuntime.DOCKER,
                      now - 86400 * 7, now - 86400 * 1,
                      [Port(80, 80), Port(443, 443)],
                      [Volume("/etc/nginx/conf.d", "/etc/nginx/conf.d", "ro")],
                      resources=ResourceUsage(0.5, 24, 256, 9.375, 1024 * 300, 1024 * 200, 1024 * 10, 1024 * 5, 2, 86400)),
            Container("f6a7b8c9d0e1", "grafana-monitor", "grafana/grafana:latest",
                      ContainerState.PAUSED, ContainerRuntime.DOCKER,
                      now - 86400 * 14, now - 86400 * 3,
                      [Port(3001, 3000)],
                      [Volume("grafana-data", "/var/lib/grafana", "rw")],
                      resources=ResourceUsage(2.8, 180, 1024, 17.578125, 1024 * 80, 1024 * 40, 1024 * 50, 1024 * 30, 10, 259200)),
            Container("a7b8c9d0e1f2", "old-worker", "python:3.11-slim",
                      ContainerState.EXITED, ContainerRuntime.DOCKER,
                      now - 86400 * 30, now - 86400 * 5,
                      [], [],
                      restart_policy="no"),
        ]

        self._images = [
            Image("sha256:abc123", "nyrqis/compositor", "latest", 850, now - 86400 * 5, ImageType.DOCKERFILE, 12, 1),
            Image("sha256:def456", "nyrqis/shell", "latest", 420, now - 86400 * 3, ImageType.DOCKERFILE, 8, 1),
            Image("sha256:ghi789", "postgres", "16-alpine", 245, now - 86400 * 10, ImageType.OCI, 6, 1),
            Image("sha256:jkl012", "redis", "7-alpine", 38, now - 86400 * 10, ImageType.OCI, 4, 1),
            Image("sha256:mno345", "nginx", "alpine", 42, now - 86400 * 7, ImageType.OCI, 5, 1),
            Image("sha256:pqr678", "grafana/grafana", "latest", 380, now - 86400 * 14, ImageType.OCI, 10, 1),
            Image("sha256:stu901", "python", "3.11-slim", 156, now - 86400 * 30, ImageType.OCI, 7, 0),
            Image("sha256:vwx234", "golang", "1.22-alpine", 320, now - 86400 * 20, ImageType.OCI, 9, 0),
        ]

        self._volumes = [
            Volume2("pgdata", "local", "/var/lib/docker/volumes/pgdata", 2400, ["postgres-db"]),
            Volume2("grafana-data", "local", "/var/lib/docker/volumes/grafana-data", 180, ["grafana-monitor"]),
        ]

        self._networks = [
            Network("bridge", "bridge", "172.17.0.0/16", "172.17.0.1",
                    ["nyrqis-compositor", "nyrkis-shell", "postgres-db", "redis-cache", "nginx-proxy"]),
            Network("nyrqis-net", "bridge", "10.0.1.0/24", "10.0.1.1",
                    ["nyrqis-compositor", "nyrkis-shell"]),
            Network("host", "host"),
            Network("none", "none"),
        ]

    @property
    def selected_container(self) -> Optional[Container]:
        if 0 <= self._selected_container < len(self._containers):
            return self._containers[self._selected_container]
        return None

    @property
    def total_containers(self) -> int:
        return len(self._containers)

    @property
    def running_containers(self) -> int:
        return sum(1 for c in self._containers if c.state == ContainerState.RUNNING)

    @property
    def total_images(self) -> int:
        return len(self._images)

    @property
    def total_disk_usage(self) -> str:
        total_mb = sum(img.size_mb for img in self._images)
        if total_mb < 1024:
            return f"{total_mb:.0f} MB"
        return f"{total_mb / 1024:.1f} GB"

    def select_container(self, idx: int):
        if 0 <= idx < len(self._containers):
            self._selected_container = idx

    def start_container(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_container
        if 0 <= i < len(self._containers):
            self._containers[i].state = ContainerState.RUNNING
            self._containers[i].started = time.time()
            self._history.append(f"Started {self._containers[i].name}")

    def stop_container(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_container
        if 0 <= i < len(self._containers):
            self._containers[i].state = ContainerState.STOPPED
            self._history.append(f"Stopped {self._containers[i].name}")

    def restart_container(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_container
        if 0 <= i < len(self._containers):
            self._containers[i].state = ContainerState.RESTARTING
            self._history.append(f"Restarting {self._containers[i].name}")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "s":
            self.start_container()
        elif key == "x":
            self.stop_container()
        elif key == "r":
            self.restart_container()
        elif key == "i":
            self._view_mode = "images"
        elif key == "v":
            self._view_mode = "volumes"
        elif key == "n":
            self._view_mode = "networks"

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS CONTAINER MANAGER                                 ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Status
        lines.append(f"  Runtime: {self._runtime.value}  Containers: {self.running_containers}/{self.total_containers} running  Images: {self.total_images} ({self.total_disk_usage})  Volumes: {len(self._volumes)}  Networks: {len(self._networks)}")
        lines.append("")

        # Containers
        lines.append("  ── Containers ──")
        for i, c in enumerate(self._containers):
            sel = "▶" if i == self._selected_container else " "
            priv = "🔓" if c.privileged else "  "
            res = c.resources
            lines.append(f"  {sel} {c.state_icon} {priv} {c.name:<20s} {c.image:<28s} {c.state.value:<10s} CPU: [{res.cpu_bar}] {res.cpu_percent:.1f}%")
            lines.append(f"      Mem: {res.memory_str} [{res.memory_bar}] {res.memory_percent:.1f}%  Net: {res.network_str}  PIDs: {res.pids}")
        lines.append("")

        # Selected container detail
        c = self.selected_container
        if c:
            lines.append(f"  ── {c.name} ({c.short_id}) ──")
            lines.append(f"  Image: {c.image}  Runtime: {c.runtime.value}  Created: {c.age_str}  Uptime: {c.resources.uptime_str}")
            if c.ports:
                lines.append(f"  Ports: {c.ports_str}")
            if c.volumes:
                lines.append(f"  Volumes: {', '.join(v.mapping for v in c.volumes)}")
            if c.env:
                env_str = "  ".join(f"{k}={v[:20]}" for k, v in list(c.env.items())[:4])
                lines.append(f"  Env: {env_str}")
            lines.append(f"  Network: {c.network_mode}  Restart: {c.restart_policy}  Privileged: {c.privileged}")
            lines.append(f"  Resources: CPU {c.resources.cpu_percent:.1f}%  RAM {c.resources.memory_str}  Disk R:{c.resources._fmt_bytes(c.resources.disk_read_bytes)} W:{c.resources._fmt_bytes(c.resources.disk_write_bytes)}")
            lines.append("")

        # Images
        lines.append("  ── Images ──")
        for img in self._images[:6]:
            lines.append(f"  {img.type_icon} {img.full_name:<35s} {img.size_str:<10s} Layers: {img.layers}  Used by: {img.used_by}")
        lines.append("")

        lines.append("  [S]tart [X]Stop [R]estart [I]Images [V]Volumes [N]etworks")
        lines.append("  [↑↓]Select [D]Remove [L]Logs")
        return lines
