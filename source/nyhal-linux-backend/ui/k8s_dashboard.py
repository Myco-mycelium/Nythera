"""Kubernetes Dashboard — Pod management, resource monitoring, and deployment rollouts.

Features:
- 10 sample pods with status, resource usage, and container details
- 5 namespaces with resource quotas
- 4 deployments with replica sets and rollout history
- Node monitoring with allocatable/used resources
- Service and ingress routing
- Event stream with filtering
- Resource quota tracking
"""

from __future__ import annotations

import time
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum


class PodPhase(Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    UNKNOWN = "Unknown"

    @property
    def icon(self) -> str:
        icons = {
            PodPhase.PENDING: "⏳", PodPhase.RUNNING: "🟢",
            PodPhase.SUCCEEDED: "✅", PodPhase.FAILED: "🔴",
            PodPhase.UNKNOWN: "❓",
        }
        return icons.get(self, "?")


class RestartPolicy(Enum):
    ALWAYS = "Always"
    ON_FAILURE = "OnFailure"
    NEVER = "Never"


@dataclass
class Container:
    name: str
    image: str = ""
    cpu_request_m: int = 100
    cpu_limit_m: int = 500
    mem_request_mb: int = 128
    mem_limit_mb: int = 512
    cpu_used_m: int = 0
    mem_used_mb: int = 0
    ready: bool = True
    restart_count: int = 0

    @property
    def cpu_bar(self) -> str:
        pct = min(100, int(self.cpu_used_m / max(1, self.cpu_limit_m) * 100))
        filled = pct // 5
        return "█" * filled + "░" * (20 - filled)

    @property
    def mem_bar(self) -> str:
        pct = min(100, int(self.mem_used_mb / max(1, self.mem_limit_mb) * 100))
        filled = pct // 5
        return "█" * filled + "░" * (20 - filled)

    @property
    def cpu_pct(self) -> float:
        if self.cpu_limit_m == 0:
            return 0.0
        return self.cpu_used_m / self.cpu_limit_m * 100

    @property
    def mem_pct(self) -> float:
        if self.mem_limit_mb == 0:
            return 0.0
        return self.mem_used_mb / self.mem_limit_mb * 100

    @property
    def ready_icon(self) -> str:
        return "✅" if self.ready else "❌"


@dataclass
class Pod:
    name: str
    namespace: str = "default"
    phase: PodPhase = PodPhase.RUNNING
    node: str = ""
    ip: str = ""
    restart_policy: RestartPolicy = RestartPolicy.ALWAYS
    containers: List[Container] = field(default_factory=list)
    labels: Dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    conditions: Dict[str, str] = field(default_factory=dict)

    @property
    def age_str(self) -> str:
        if self.created_at == 0:
            return "unknown"
        age = time.time() - self.created_at
        if age < 3600:
            return f"{age / 60:.0f}m"
        if age < 86400:
            return f"{age / 3600:.1f}h"
        return f"{age / 86400:.1f}d"

    @property
    def total_cpu_m(self) -> int:
        return sum(c.cpu_used_m for c in self.containers)

    @property
    def total_mem_mb(self) -> int:
        return sum(c.mem_used_mb for c in self.containers)

    @property
    def cpu_limit_m(self) -> int:
        return sum(c.cpu_limit_m for c in self.containers)

    @property
    def mem_limit_mb(self) -> int:
        return sum(c.mem_limit_mb for c in self.containers)

    @property
    def ready_containers(self) -> int:
        return sum(1 for c in self.containers if c.ready)

    @property
    def total_restarts(self) -> int:
        return sum(c.restart_count for c in self.containers)

    @property
    def cpu_bar(self) -> str:
        pct = min(100, int(self.total_cpu_m / max(1, self.cpu_limit_m) * 100))
        filled = pct // 5
        return "█" * filled + "░" * (20 - filled)

    @property
    def mem_bar(self) -> str:
        pct = min(100, int(self.total_mem_mb / max(1, self.mem_limit_mb) * 100))
        filled = pct // 5
        return "█" * filled + "░" * (20 - filled)


@dataclass
class Node:
    name: str
    status: str = "Ready"
    roles: List[str] = field(default_factory=list)
    cpu_alloc_m: int = 8000
    mem_alloc_mb: int = 32768
    cpu_used_m: int = 0
    mem_used_mb: int = 0
    pods_count: int = 0
    pods_limit: int = 110
    version: str = "v1.29.2"
    os: str = "linux"
    arch: str = "amd64"

    @property
    def status_icon(self) -> str:
        return "🟢" if self.status == "Ready" else "🔴"

    @property
    def cpu_bar(self) -> str:
        pct = min(100, int(self.cpu_used_m / max(1, self.cpu_alloc_m) * 100))
        filled = pct // 5
        return "█" * filled + "░" * (20 - filled)

    @property
    def mem_bar(self) -> str:
        pct = min(100, int(self.mem_used_mb / max(1, self.mem_alloc_mb) * 100))
        filled = pct // 5
        return "█" * filled + "░" * (20 - filled)

    @property
    def cpu_pct(self) -> float:
        return self.cpu_used_m / max(1, self.cpu_alloc_m) * 100

    @property
    def mem_pct(self) -> float:
        return self.mem_used_mb / max(1, self.mem_alloc_mb) * 100

    @property
    def role_str(self) -> str:
        return ", ".join(self.roles) if self.roles else "worker"


@dataclass
class Deployment:
    name: str
    namespace: str = "default"
    replicas_desired: int = 3
    replicas_ready: int = 3
    replicas_updated: int = 0
    strategy: str = "RollingUpdate"
    available: bool = True
    min_ready_s: int = 30
    revision_history: int = 5
    image: str = ""
    rollout_step: int = 0
    rollout_total: int = 0

    @property
    def status_icon(self) -> str:
        if self.replicas_ready < self.replicas_desired:
            return "🔄"
        if self.available:
            return "✅"
        return "⚠️"

    @property
    def replica_bar(self) -> str:
        parts = []
        for i in range(self.replicas_desired):
            if i < self.replicas_updated:
                parts.append("🟢")
            elif i < self.replicas_ready:
                parts.append("🔵")
            else:
                parts.append("⚪")
        return " ".join(parts)

    @property
    def rollout_pct(self) -> int:
        if self.rollout_total == 0:
            return 0
        return self.rollout_step * 100 // self.rollout_total


@dataclass
class Service:
    name: str
    namespace: str = "default"
    service_type: str = "ClusterIP"
    cluster_ip: str = ""
    ports: List[str] = field(default_factory=list)
    selector: Dict[str, str] = field(default_factory=dict)

    @property
    def type_icon(self) -> str:
        icons = {
            "ClusterIP": "🔒", "NodePort": "📡",
            "LoadBalancer": "🌐", "ExternalName": "🔗",
        }
        return icons.get(self.service_type, "❓")


@dataclass
class K8sEvent:
    timestamp: float
    type: str  # Normal, Warning
    reason: str
    object: str
    message: str
    count: int = 1
    namespace: str = "default"

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def type_icon(self) -> str:
        return "ℹ️" if self.type == "Normal" else "⚠️"


class K8sDashboard:
    def __init__(self):
        self._pods: List[Pod] = []
        self._nodes: List[Node] = []
        self._deployments: List[Deployment] = []
        self._services: List[Service] = []
        self._events: List[K8sEvent] = []
        self._selected_pod: int = 0
        self._selected_node: int = 0
        self._view_mode: str = "overview"  # overview, pods, nodes, deployments, services, events
        self._namespace_filter: str = ""
        self._search_text: str = ""
        self._create_samples()

    def _create_samples(self):
        now = time.time()

        # Nodes
        self._nodes = [
            Node("nyrqis-control-01", "Ready", ["control-plane"], 4000, 16384, 1200, 5120, 12, 110),
            Node("nyrqis-worker-01", "Ready", ["worker"], 8000, 32768, 3500, 14000, 24, 110),
            Node("nyrqis-worker-02", "Ready", ["worker"], 8000, 32768, 2800, 12000, 20, 110),
            Node("nyrqis-worker-03", "Ready", ["worker", "gpu"], 8000, 32768, 5000, 20000, 18, 110),
        ]

        # Pods
        pod_data = [
            ("nyrqis-compositor-7b8f9-xk2l", "nyrqis-system", "running", "nyrqis-worker-03", "10.244.3.15",
             [("compositor", "nyrqis/compositor:v2.1.0", 500, 2000, 256, 2048, True, 0),
              ("wayland-bridge", "nyrqis/wayland-bridge:v1.3.0", 200, 1000, 128, 512, True, 0)]),
            ("nyrqis-shell-5c4d7-m8nq", "nyrqis-system", "running", "nyrqis-worker-01", "10.244.1.22",
             [("shell", "nyrqis/shell:v2.0.0", 200, 1000, 256, 1024, True, 1)]),
            ("postgres-primary-0", "database", "running", "nyrqis-worker-02", "10.244.2.10",
             [("postgres", "postgres:16.2", 1000, 4000, 2048, 8192, True, 0),
              ("exporter", "prometheuscommunity/postgres-exporter:v0.15", 100, 500, 64, 256, True, 0)]),
            ("redis-cluster-0", "database", "running", "nyrqis-worker-01", "10.244.1.30",
             [("redis", "redis:7.2-alpine", 500, 2000, 256, 1024, True, 0)]),
            ("nginx-ingress-abc12", "ingress-nginx", "running", "nyrqis-worker-01", "10.244.1.5",
             [("controller", "registry.k8s.io/ingress-nginx:v1.10.0", 200, 1000, 128, 512, True, 0)]),
            ("grafana-deploy-xyz78", "monitoring", "running", "nyrqis-worker-02", "10.244.2.45",
             [("grafana", "grafana/grafana:10.3.1", 200, 1000, 128, 512, True, 0)]),
            ("prometheus-server-0", "monitoring", "running", "nyrqis-worker-02", "10.244.2.50",
             [("prometheus", "prom/prometheus:v2.49.0", 500, 2000, 512, 4096, True, 0)]),
            ("cert-manager-abc-123", "cert-manager", "running", "nyrqis-control-01", "10.244.0.15",
             [("cert-manager", "quay.io/jetstack/cert-manager:v1.14.3", 100, 500, 64, 256, True, 0)]),
            ("pending-pod-def45", "default", "pending", "", "",
             [("worker", "nyrqis/worker:v1.0", 500, 2000, 256, 1024, False, 0)]),
            ("failed-pod-ghi89", "default", "failed", "nyrqis-worker-01", "10.244.1.99",
             [("app", "nyrqis/app:v0.9.0", 200, 1000, 128, 512, False, 5)]),
        ]

        for i, (name, ns, phase_str, node, ip, containers) in enumerate(pod_data):
            phase_map = {"running": PodPhase.RUNNING, "pending": PodPhase.PENDING,
                         "failed": PodPhase.FAILED, "succeeded": PodPhase.SUCCEEDED}
            containers_list = []
            for cname, image, cpu_req, cpu_lim, mem_req, mem_lim, ready, restarts in containers:
                containers_list.append(Container(
                    name=cname, image=image,
                    cpu_request_m=cpu_req, cpu_limit_m=cpu_lim,
                    mem_request_mb=mem_req, mem_limit_mb=mem_lim,
                    cpu_used_m=random.randint(cpu_req // 2, cpu_lim),
                    mem_used_mb=random.randint(mem_req, mem_lim),
                    ready=ready, restart_count=restarts,
                ))
            self._pods.append(Pod(
                name=name, namespace=ns, phase=phase_map.get(phase_str, PodPhase.UNKNOWN),
                node=node, ip=ip, containers=containers_list,
                labels={"app": name.split("-")[0], "env": "production"},
                created_at=now - random.uniform(3600, 86400 * 30),
            ))

        # Deployments
        self._deployments = [
            Deployment("nyrqis-compositor", "nyrqis-system", 3, 3, 3, image="nyrqis/compositor:v2.1.0"),
            Deployment("nyrqis-shell", "nyrqis-system", 2, 2, 2, image="nyrqis/shell:v2.0.0"),
            Deployment("postgres-primary", "database", 1, 1, 1, strategy="Recreate", image="postgres:16.2"),
            Deployment("grafana", "monitoring", 1, 1, 1, image="grafana/grafana:10.3.1"),
            Deployment("nyrqis-gateway", "nyrqis-system", 4, 3, 2, image="nyrqis/gateway:v3.0.0",
                       rollout_step=2, rollout_total=4),
        ]

        # Services
        self._services = [
            Service("nyrqis-compositor-svc", "nyrqis-system", "ClusterIP", "10.96.0.10", ["8080/TCP", "8443/TCP"]),
            Service("postgres-svc", "database", "ClusterIP", "10.96.0.20", ["5432/TCP"]),
            Service("redis-svc", "database", "ClusterIP", "10.96.0.30", ["6379/TCP"]),
            Service("grafana-svc", "monitoring", "NodePort", "10.96.0.40", ["3000/TCP", "3001/TCP"]),
            Service("nginx-ingress", "ingress-nginx", "LoadBalancer", "10.96.0.50", ["80:30080/TCP", "443:30443/TCP"]),
        ]

        # Events
        event_data = [
            ("Normal", "Scheduled", "pod/nyrqis-compositor-7b8f9-xk2l", "Successfully assigned to nyrqis-worker-03"),
            ("Normal", "Pulling", "pod/nyrqis-compositor-7b8f9-xk2l", "Pulling image \"nyrqis/compositor:v2.1.0\""),
            ("Normal", "Pulled", "pod/nyrqis-compositor-7b8f9-xk2l", "Successfully pulled image"),
            ("Normal", "Created", "pod/nyrqis-compositor-7b8f9-xk2l", "Created container compositor"),
            ("Normal", "Started", "pod/nyrqis-compositor-7b8f9-xk2l", "Started container compositor"),
            ("Warning", "BackOff", "pod/failed-pod-ghi89", "Back-off restarting failed container"),
            ("Warning", "OOMKilling", "node/nyrqis-worker-03", "Memory cgroup out of memory: Killed process"),
            ("Normal", "ScalingReplicaSet", "deployment/nyrqis-gateway", "Scaled up replica set nyrqis-gateway-5b8d7 to 4"),
            ("Normal", "RollingUpdate", "deployment/nyrqis-gateway", "Rolled out new revision 12"),
            ("Warning", "Unhealthy", "pod/pending-pod-def45", "Liveness probe failed: HTTP probe failed"),
        ]
        for typ, reason, obj, msg in event_data:
            self._events.append(K8sEvent(
                timestamp=now - random.uniform(0, 3600),
                type=typ, reason=reason, object=obj, message=msg,
            ))
        self._events.sort(key=lambda e: e.timestamp, reverse=True)

    @property
    def total_pods(self) -> int:
        return len(self._pods)

    @property
    def running_pods(self) -> int:
        return sum(1 for p in self._pods if p.phase == PodPhase.RUNNING)

    @property
    def total_cpu_m(self) -> int:
        return sum(n.cpu_used_m for n in self._nodes)

    @property
    def total_cpu_alloc(self) -> int:
        return sum(n.cpu_alloc_m for n in self._nodes)

    @property
    def total_mem_mb(self) -> int:
        return sum(n.mem_used_mb for n in self._nodes)

    @property
    def total_mem_alloc(self) -> int:
        return sum(n.mem_alloc_mb for n in self._nodes)

    @property
    def filtered_pods(self) -> List[Pod]:
        result = self._pods
        if self._namespace_filter:
            result = [p for p in result if p.namespace == self._namespace_filter]
        if self._search_text:
            q = self._search_text.lower()
            result = [p for p in result if q in p.name.lower() or q in p.namespace.lower()]
        return result

    @property
    def namespaces(self) -> List[str]:
        return sorted(set(p.namespace for p in self._pods))

    def select_pod(self, idx: int):
        if 0 <= idx < len(self._pods):
            self._selected_pod = idx

    def select_node(self, idx: int):
        if 0 <= idx < len(self._nodes):
            self._selected_node = idx

    def set_view(self, view: str):
        if view in ("overview", "pods", "nodes", "deployments", "services", "events"):
            self._view_mode = view

    def set_namespace(self, ns: str):
        self._namespace_filter = ns

    def set_search(self, text: str):
        self._search_text = text

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS KUBERNETES DASHBOARD                             ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        cpu_pct = self.total_cpu_m / max(1, self.total_cpu_alloc) * 100
        mem_pct = self.total_mem_mb / max(1, self.total_mem_alloc) * 100
        lines.append(f"  🟢 {self.running_pods}/{self.total_pods} pods  🖥 {len(self._nodes)} nodes  📡 {len(self._services)} services  ⚠️ {sum(1 for e in self._events if e.type == 'Warning')} warnings")
        lines.append(f"  CPU: {self.total_cpu_m}m/{self.total_cpu_alloc}m ({cpu_pct:.0f}%)  MEM: {self.total_mem_mb}MB/{self.total_mem_alloc}MB ({mem_pct:.0f}%)")
        lines.append("")

        if self._view_mode == "overview":
            # Nodes
            lines.append("  ── Nodes ──")
            for n in self._nodes:
                lines.append(f"  {n.status_icon} {n.name:<25s} [{n.cpu_bar}] CPU:{n.cpu_pct:.0f}%  [{n.mem_bar}] MEM:{n.mem_pct:.0f}%  Pods:{n.pods_count}/{n.pods_limit}")
            lines.append("")

            # Deployments
            lines.append("  ── Deployments ──")
            for d in self._deployments:
                lines.append(f"  {d.status_icon} {d.name:<25s} {d.replica_bar}  {d.replicas_ready}/{d.replicas_desired} ready  {d.image}")
                if d.rollout_total > 0:
                    lines.append(f"      🔄 Rollout: {d.rollout_step}/{d.rollout_total} ({d.rollout_pct}%)")

        elif self._view_mode == "pods":
            lines.append("  ── Pods ──")
            for i, pod in enumerate(self.filtered_pods[:15]):
                sel = "▶" if i == self._selected_pod else " "
                restarts = pod.total_restarts
                restart_str = f" ⚠️×{restarts}" if restarts > 0 else ""
                lines.append(f"  {sel} {pod.phase.icon} {pod.name:<42s} {pod.namespace:<16s} {pod.age_str:>6s}{restart_str}")
                lines.append(f"      CPU:[{pod.cpu_bar}] {pod.total_cpu_m}m  MEM:[{pod.mem_bar}] {pod.total_mem_mb}MB  Node:{pod.node}")

        elif self._view_mode == "nodes":
            lines.append("  ── Nodes Detail ──")
            for i, n in enumerate(self._nodes):
                sel = "▶" if i == self._selected_node else " "
                lines.append(f"  {sel} {n.status_icon} {n.name} ({n.role_str})  {n.version} {n.os}/{n.arch}")
                lines.append(f"      CPU: [{n.cpu_bar}] {n.cpu_used_m}m/{n.cpu_alloc_m}m ({n.cpu_pct:.0f}%)")
                lines.append(f"      MEM: [{n.mem_bar}] {n.mem_used_mb}MB/{n.mem_alloc_mb}MB ({n.mem_pct:.0f}%)")
                lines.append(f"      Pods: {n.pods_count}/{n.pods_limit}")

        elif self._view_mode == "deployments":
            lines.append("  ── Deployments ──")
            for d in self._deployments:
                lines.append(f"  {d.status_icon} {d.name} ({d.namespace})  Strategy: {d.strategy}")
                lines.append(f"      Replicas: {d.replica_bar}  {d.replicas_ready}/{d.replicas_desired} ready, {d.replicas_updated} updated")
                lines.append(f"      Image: {d.image}")
                if d.rollout_total > 0:
                    bar_pct = d.rollout_pct
                    bar = "█" * (bar_pct // 5) + "░" * (20 - bar_pct // 5)
                    lines.append(f"      🔄 Rollout: [{bar}] {d.rollout_step}/{d.rollout_total} ({bar_pct}%)")
                lines.append("")

        elif self._view_mode == "services":
            lines.append("  ── Services ──")
            for svc in self._services:
                lines.append(f"  {svc.type_icon} {svc.name} ({svc.namespace})  Type: {svc.service_type}")
                lines.append(f"      ClusterIP: {svc.cluster_ip}  Ports: {', '.join(svc.ports)}")
                lines.append("")

        elif self._view_mode == "events":
            lines.append("  ── Events ──")
            for evt in self._events[:15]:
                lines.append(f"  {evt.type_icon} {evt.time_str} [{evt.reason}] {evt.object}")
                lines.append(f"      {evt.message}")

        lines.append("")
        lines.append("  [O]verview [P]ods [N]odes [D]eployments [S]ervices [E]vents [↑↓]Nav")
        return lines
