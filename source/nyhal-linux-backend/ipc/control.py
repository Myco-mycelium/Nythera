#!/usr/bin/env python3
"""
Control Service — the operator control plane on a running daemon
(implementation_plan.md §4.3, §4.5).

Serves container lifecycle operations to the daemon's OWN user over the
same transport as the container-facing services. The server
(``ipc/transport.py``) authenticates the operator by the
kernel-attached uid (``trusted_uids`` — unforgeable, and a process
running as the daemon's user already has full control of the daemon,
so the container capability model deliberately does not apply to it).
Containers cannot reach this service: a daemon-spawned container's pid
resolves through the container registry first (never the operator
path), and this service additionally refuses any sender that is not
the operator identity. The operator identity is synced from the
server on attach (``operator_id=None`` default), so the gate always
matches the server's auth by construction.

Operations (JSON request → JSON reply over CALL/REPLY):

- ``{"service": "control", "op": "container_run", "command": [...],
   "capabilities": [...], "network": bool, "memory_mb": int,
   "pids": int, "name": str}`` — spawn through the daemon's
  ``ContainerManager`` (auto-registered and auto-granted).
- ``{"service": "control", "op": "container_list"}`` — the daemon's
  containers with their state and pid.
- ``{"service": "control", "op": "container_kill",
   "container_id": str}`` — terminate.

References:
- NPS-010 §5: capability assignment/revocation at container lifecycle
- NPS-017 §4.1: container primitives; §4.3: IPC semantics
- implementation_plan.md §4.5: service bring-up
"""

import json
import logging
from typing import Any, Dict, Optional

from .transport import DEFAULT_OPERATOR_ID  # the server is the auth boundary

logger = logging.getLogger(__name__)


class ControlService:
    """Operator control plane on a running daemon.

    Attach to the daemon's ``IPCDatagramServer`` (its ``_server`` is
    set by the ``ServiceRouter``) and register on the router under
    ``"control"``. Every operation is refused for senders other than
    the operator identity — a container (even with CAP_IPC_SEND) gets
    ``forbidden``.
    """

    SERVICE_NAME = "nyrqis.backend.control"
    SERVICE_VERSION = "1.0"

    def __init__(self, container_manager,
                 capability_manager: Optional[Any] = None,
                 operator_id: Optional[str] = None,
                 state_saver: Optional[Any] = None) -> None:
        self.container_manager = container_manager
        self.capability_manager = capability_manager
        # None → synced from the server on attach, so the operator gate
        # always matches the server's auth identity by construction.
        self.operator_id = operator_id
        # Plan §4.5 persistent state: called (best effort) after each
        # mutating op so the daemon's state file tracks the manifest.
        self.state_saver = state_saver
        self._server = None

    def attach(self, server) -> "ControlService":
        """Give the service the server to reply through (the router
        owns ``on_call``; this records the reply path). When constructed
        with ``operator_id=None``, the identity is synced from the
        server so the gate always matches the server's auth."""
        self._server = server
        if self.operator_id is None:
            self.operator_id = getattr(
                server, "operator_id", DEFAULT_OPERATOR_ID
            )
        return self

    # -- handler ----------------------------------------------------

    def _on_call(self, msg, sender: str, sender_path: str) -> None:
        server = self._server
        if server is None:
            logger.error("ipc: %s has no server to reply through",
                         self.SERVICE_NAME)
            return
        # The operator-only gate: the server resolved ``sender`` as
        # either a container id or the operator identity; only the
        # latter may drive the control plane.
        if sender != self.operator_id:
            self._reply(server, sender_path, msg.message_id, {
                "ok": False,
                "error": "forbidden: the control plane is operator-only",
            })
            return
        try:
            try:
                request = json.loads(msg.payload.decode("utf-8") or "{}")
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
            except (ValueError, UnicodeDecodeError):
                self._reply(server, sender_path, msg.message_id, {
                    "ok": False,
                    "error": "bad request: expected a JSON object",
                })
                return
            op = request.get("op")
            if op == "container_run":
                self._container_run(server, sender_path, msg.message_id,
                                    request)
            elif op == "container_list":
                self._container_list(server, sender_path, msg.message_id)
            elif op == "container_kill":
                self._container_kill(server, sender_path, msg.message_id,
                                     request)
            elif op == "container_stats":
                self._container_stats(server, sender_path, msg.message_id,
                                      request)
            elif op == "container_logs":
                self._container_logs(server, sender_path, msg.message_id,
                                     request)
            elif op == "container_exec":
                self._container_exec(server, sender_path, msg.message_id,
                                     request)
            elif op == "container_top":
                self._container_top(server, sender_path,
                                    msg.message_id, request)
            elif op == "container_network_stats":
                self._container_network_stats(server, sender_path,
                                              msg.message_id, request)
            elif op == "image_list":
                self._image_list(server, sender_path, msg.message_id,
                                 request)
            elif op == "image_remove":
                self._image_remove(server, sender_path, msg.message_id,
                                   request)
            elif op == "image_export":
                self._image_export(server, sender_path, msg.message_id,
                                   request)
            elif op == "image_import":
                self._image_import(server, sender_path, msg.message_id,
                                   request)
            elif op == "container_checkpoint":
                self._container_checkpoint(server, sender_path,
                                           msg.message_id, request)
            elif op == "container_restore":
                self._container_restore(server, sender_path,
                                        msg.message_id, request)
            elif op == "container_diff":
                self._container_diff(server, sender_path,
                                     msg.message_id, request)
            elif op == "container_events":
                self._container_events(server, sender_path,
                                       msg.message_id, request)
            elif op == "container_health":
                self._container_health(server, sender_path,
                                       msg.message_id, request)
            elif op == "container_resource_limits":
                self._container_resource_limits(server, sender_path,
                                                msg.message_id, request)
            elif op == "container_scheduling":
                self._container_scheduling(server, sender_path,
                                           msg.message_id, request)
            elif op == "container_set_nice":
                self._container_set_nice(server, sender_path,
                                         msg.message_id, request)
            elif op == "container_set_affinity":
                self._container_set_affinity(server, sender_path,
                                             msg.message_id, request)
            elif op == "container_network_policy":
                self._container_network_policy(server, sender_path,
                                               msg.message_id, request)
            elif op == "quota_set":
                self._quota_set(server, sender_path, msg.message_id,
                                request)
            elif op == "quota_get":
                self._quota_get(server, sender_path, msg.message_id,
                                request)
            elif op == "quota_list":
                self._quota_list(server, sender_path, msg.message_id)
            elif op == "quota_delete":
                self._quota_delete(server, sender_path, msg.message_id,
                                   request)
            elif op == "quota_usage":
                self._quota_usage(server, sender_path, msg.message_id,
                                  request)
            elif op == "app_install":
                self._app_install(server, sender_path, msg.message_id,
                                  request)
            elif op == "app_list":
                self._app_list(server, sender_path, msg.message_id)
            elif op == "app_launch":
                self._app_launch(server, sender_path, msg.message_id,
                                 request)
            elif op == "app_terminate":
                self._app_terminate(server, sender_path, msg.message_id,
                                    request)
            elif op == "container_start_ordered":
                self._container_start_ordered(
                    server, sender_path, msg.message_id, request)
            elif op == "container_stop_ordered":
                self._container_stop_ordered(
                    server, sender_path, msg.message_id, request)
            elif op == "container_dependency_graph":
                self._container_dependency_graph(
                    server, sender_path, msg.message_id, request)
            elif op == "container_restart_info":
                self._container_restart_info(
                    server, sender_path, msg.message_id, request)
            elif op == "container_set_restart":
                self._container_set_restart(
                    server, sender_path, msg.message_id, request)
            elif op == "container_env_set":
                self._container_env_set(
                    server, sender_path, msg.message_id, request)
            elif op == "container_env_unset":
                self._container_env_unset(
                    server, sender_path, msg.message_id, request)
            elif op == "container_env_list":
                self._container_env_list(
                    server, sender_path, msg.message_id, request)
            elif op == "snapshot_export":
                self._snapshot_export(
                    server, sender_path, msg.message_id, request)
            elif op == "snapshot_import":
                self._snapshot_import(
                    server, sender_path, msg.message_id, request)
            elif op == "resource_history":
                self._resource_history(
                    server, sender_path, msg.message_id, request)
            elif op == "resource_record":
                self._resource_record(
                    server, sender_path, msg.message_id, request)
            elif op == "resource_record_start":
                self._resource_record_start(
                    server, sender_path, msg.message_id, request)
            elif op == "resource_record_stop":
                self._resource_record_stop(
                    server, sender_path, msg.message_id, request)
            elif op == "container_update_limits":
                self._container_update_limits(
                    server, sender_path, msg.message_id, request)
            elif op == "label_set":
                self._label_set(
                    server, sender_path, msg.message_id, request)
            elif op == "label_unset":
                self._label_unset(
                    server, sender_path, msg.message_id, request)
            elif op == "label_list":
                self._label_list(
                    server, sender_path, msg.message_id, request)
            elif op == "label_filter":
                self._label_filter(
                    server, sender_path, msg.message_id, request)
            elif op == "cgroup2_status":
                self._cgroup2_status(
                    server, sender_path, msg.message_id, request)
            elif op == "verify_enforcement":
                self._verify_enforcement(
                    server, sender_path, msg.message_id, request)
            elif op == "lock_acquire":
                self._lock_acquire(
                    server, sender_path, msg.message_id, request)
            elif op == "lock_release":
                self._lock_release(
                    server, sender_path, msg.message_id, request)
            elif op == "lock_list":
                self._lock_list(
                    server, sender_path, msg.message_id,
                    request)
            elif op == "alert_history":
                self._alert_history(
                    server, sender_path, msg.message_id, request)
            elif op == "alert_clear":
                self._alert_clear(
                    server, sender_path, msg.message_id, request)
            elif op == "alert_thresholds":
                self._alert_thresholds(
                    server, sender_path, msg.message_id, request)
            elif op == "alert_acknowledge":
                self._alert_acknowledge(
                    server, sender_path, msg.message_id, request)
            elif op == "alert_suppress":
                self._alert_suppress(
                    server, sender_path, msg.message_id, request)
            elif op == "alert_unsuppress":
                self._alert_unsuppress(
                    server, sender_path, msg.message_id, request)
            elif op == "alert_statistics":
                self._alert_statistics(
                    server, sender_path, msg.message_id, request)
            elif op == "alert_suppressions_list":
                self._alert_suppressions_list(
                    server, sender_path, msg.message_id, request)
            elif op == "oom_status":
                self._oom_status(
                    server, sender_path, msg.message_id, request)
            elif op == "oom_set":
                self._oom_set(
                    server, sender_path, msg.message_id, request)
            elif op == "oom_events":
                self._oom_events(
                    server, sender_path, msg.message_id, request)
            elif op == "dashboard":
                self._dashboard(
                    server, sender_path, msg.message_id, request)
            elif op == "export_history":
                self._export_history(
                    server, sender_path, msg.message_id, request)
            elif op == "export_snapshot":
                self._export_snapshot(
                    server, sender_path, msg.message_id, request)
            elif op == "webhook_register":
                self._webhook_register(
                    server, sender_path, msg.message_id, request)
            elif op == "webhook_unregister":
                self._webhook_unregister(
                    server, sender_path, msg.message_id, request)
            elif op == "webhook_list":
                self._webhook_list(
                    server, sender_path, msg.message_id,
                    request)
            elif op == "webhook_enable":
                self._webhook_enable(
                    server, sender_path, msg.message_id, request)
            elif op == "webhook_disable":
                self._webhook_disable(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_check":
                self._sla_check(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_violations":
                self._sla_violations(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_set":
                self._sla_set(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_escalation_policy":
                self._sla_escalation_policy(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_escalation_status":
                self._sla_escalation_status(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_escalation_reset":
                self._sla_escalation_reset(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_escalation_history":
                self._sla_escalation_history(
                    server, sender_path, msg.message_id, request)
            elif op == "billing_rates_set":
                self._billing_rates_set(
                    server, sender_path, msg.message_id, request)
            elif op == "billing_rates_get":
                self._billing_rates_get(
                    server, sender_path, msg.message_id,
                    request)
            elif op == "billing_record":
                self._billing_record(
                    server, sender_path, msg.message_id, request)
            elif op == "billing_records":
                self._billing_records(
                    server, sender_path, msg.message_id, request)
            elif op == "billing_summary":
                self._billing_summary(
                    server, sender_path, msg.message_id, request)
            elif op == "cost_budget_configure":
                self._cost_budget_configure(
                    server, sender_path, msg.message_id, request)
            elif op == "cost_budget_check":
                self._cost_budget_check(
                    server, sender_path, msg.message_id, request)
            elif op == "cost_alerts":
                self._cost_alerts(
                    server, sender_path, msg.message_id, request)
            elif op == "cost_budget_config":
                self._cost_budget_config(
                    server, sender_path, msg.message_id, request)
            elif op == "autoscale_configure":
                self._autoscale_configure(
                    server, sender_path, msg.message_id, request)
            elif op == "autoscale_status":
                self._autoscale_status(
                    server, sender_path, msg.message_id, request)
            elif op == "autoscale_apply":
                self._autoscale_apply(
                    server, sender_path, msg.message_id, request)
            elif op == "autoscale_disable":
                self._autoscale_disable(
                    server, sender_path, msg.message_id, request)
            elif op == "autoscale_events":
                self._autoscale_events(
                    server, sender_path, msg.message_id, request)
            elif op == "health_configure":
                self._health_configure(
                    server, sender_path, msg.message_id, request)
            elif op == "health_trigger":
                self._health_trigger(
                    server, sender_path, msg.message_id, request)
            elif op == "health_config":
                self._health_config(
                    server, sender_path, msg.message_id, request)
            elif op == "health_restart_reset":
                self._health_restart_reset(
                    server, sender_path, msg.message_id, request)
            elif op == "health_restart_history":
                self._health_restart_history(
                    server, sender_path, msg.message_id, request)
            elif op == "forecast":
                self._forecast(
                    server, sender_path, msg.message_id, request)
            elif op == "forecast_all":
                self._forecast_all(
                    server, sender_path, msg.message_id, request)
            elif op == "time_to_exhaustion":
                self._time_to_exhaustion(
                    server, sender_path, msg.message_id, request)
            elif op == "capacity_plan":
                self._capacity_plan(
                    server, sender_path, msg.message_id, request)
            elif op == "capacity_plan_all":
                self._capacity_plan_all(
                    server, sender_path, msg.message_id, request)
            elif op == "network_traffic":
                self._network_traffic(
                    server, sender_path, msg.message_id, request)
            elif op == "network_connections":
                self._network_connections(
                    server, sender_path, msg.message_id, request)
            elif op == "network_bandwidth_history":
                self._network_bandwidth_history(
                    server, sender_path, msg.message_id, request)
            elif op == "anomaly_detect":
                self._anomaly_detect(
                    server, sender_path, msg.message_id, request)
            elif op == "anomaly_detect_all":
                self._anomaly_detect_all(
                    server, sender_path, msg.message_id, request)
            elif op == "anomaly_spike":
                self._anomaly_spike(
                    server, sender_path, msg.message_id, request)
            elif op == "anomaly_trend":
                self._anomaly_trend(
                    server, sender_path, msg.message_id, request)
            elif op == "compare":
                self._compare(
                    server, sender_path, msg.message_id, request)
            elif op == "compare_all":
                self._compare_all(
                    server, sender_path, msg.message_id, request)
            elif op == "relative_usage":
                self._relative_usage(
                    server, sender_path, msg.message_id, request)
            elif op == "top_consumers":
                self._top_consumers(
                    server, sender_path, msg.message_id, request)
            elif op == "recommendations":
                self._recommendations(
                    server, sender_path, msg.message_id, request)
            elif op == "recommendations_all":
                self._recommendations_all(
                    server, sender_path, msg.message_id,
                    request)
            elif op == "recommendations_category":
                self._recommendations_category(
                    server, sender_path, msg.message_id, request)
            elif op == "resource_profile":
                self._resource_profile(
                    server, sender_path, msg.message_id, request)
            elif op == "resource_profile_history":
                self._resource_profile_history(
                    server, sender_path, msg.message_id, request)
            elif op == "resource_profile_top":
                self._resource_profile_top(
                    server, sender_path, msg.message_id, request)
            elif op == "batch_start":
                self._batch_start(
                    server, sender_path, msg.message_id, request)
            elif op == "batch_stop":
                self._batch_stop(
                    server, sender_path, msg.message_id, request)
            elif op == "batch_kill":
                self._batch_kill(
                    server, sender_path, msg.message_id, request)
            elif op == "baseline_record":
                self._baseline_record(
                    server, sender_path, msg.message_id, request)
            elif op == "baseline_get":
                self._baseline_get(
                    server, sender_path, msg.message_id, request)
            elif op == "baseline_compare":
                self._baseline_compare(
                    server, sender_path, msg.message_id, request)
            elif op == "baseline_clear":
                self._baseline_clear(
                    server, sender_path, msg.message_id, request)
            elif op == "process_kill":
                self._process_kill(
                    server, sender_path, msg.message_id, request)
            elif op == "process_list":
                self._process_list(
                    server, sender_path, msg.message_id, request)
            elif op == "process_signal_all":
                self._process_signal_all(
                    server, sender_path, msg.message_id, request)
            elif op == "snapshot_schedule_set":
                self._snapshot_schedule_set(
                    server, sender_path, msg.message_id, request)
            elif op == "snapshot_schedule_get":
                self._snapshot_schedule_get(
                    server, sender_path, msg.message_id, request)
            elif op == "snapshot_schedule_disable":
                self._snapshot_schedule_disable(
                    server, sender_path, msg.message_id, request)
            elif op == "snapshot_schedule_run":
                self._snapshot_schedule_run(
                    server, sender_path, msg.message_id, request)
            elif op == "snapshot_schedule_list":
                self._snapshot_schedule_list(
                    server, sender_path, msg.message_id, request)
            elif op == "dependency_health":
                self._dependency_health(
                    server, sender_path, msg.message_id, request)
            elif op == "dependency_health_reverse":
                self._dependency_health_reverse(
                    server, sender_path, msg.message_id, request)
            elif op == "usage_report":
                self._usage_report(
                    server, sender_path, msg.message_id, request)
            elif op == "alert_summary":
                self._alert_summary(
                    server, sender_path, msg.message_id, request)
            elif op == "set_cpu_weight":
                self._set_cpu_weight(
                    server, sender_path, msg.message_id, request)
            elif op == "set_io_weight":
                self._set_io_weight(
                    server, sender_path, msg.message_id, request)
            elif op == "get_priority":
                self._get_priority(
                    server, sender_path, msg.message_id, request)
            else:
                self._reply(server, sender_path, msg.message_id, {
                    "ok": False,
                    "error": "unknown operation: %r" % (op,),
                })
        except Exception:  # noqa: BLE001 - a service bug must not kill the serve loop
            logger.exception("ipc: %s internal error", self.SERVICE_NAME)
            try:
                self._reply(server, sender_path, msg.message_id, {
                    "ok": False,
                    "error": "internal error",
                })
            except Exception:  # noqa: BLE001 - even the error reply can fail
                logger.exception("ipc: %s could not send error reply",
                                 self.SERVICE_NAME)

    # -- operations ---------------------------------------------------

    def _container_run(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        command = request.get("command")
        if not isinstance(command, list) or not command or not all(
            isinstance(c, str) for c in command
        ):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "command must be a non-empty list of strings",
            })
            return
        # Lazy import: ipc must not depend on backend eagerly (the
        # established pattern in ipc/core.py).
        from backend.container import ContainerConfig, ResourceLimits
        try:
            config = ContainerConfig(
                name=request.get("name"),
                command=command,
                capabilities=list(request.get("capabilities") or []),
                network=bool(request.get("network", False)),
                limits=ResourceLimits(
                    memory_mb=int(request.get("memory_mb") or 256),
                    pid_limit=int(request.get("pids") or 64),
                ),
                restart_policy=request.get("restart_policy", "no"),
                restart_max_retries=int(request.get("restart_max_retries") or 5),
                restart_delay=float(request.get("restart_delay") or 1.0),
                inherit_host_env=bool(request.get("inherit_host_env", True)),
            )
            container = self.container_manager.create(config)
            self.container_manager.spawn(container)
        except Exception as e:  # noqa: BLE001 - report to the operator
            logger.error("ipc: container_run failed: %s", e)
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_run failed: %s" % (e,),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "container_id": container.id,
            "pid": container.pid,
        })

    def _container_list(self, server, sender_path: str, call_id: str) -> None:
        containers = [
            {"id": c.id, "state": c.state.value, "pid": c.pid}
            for c in self.container_manager.containers.values()
        ]
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "containers": containers,
        })

    def _container_kill(self, server, sender_path: str, call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            self.container_manager.terminate(container)
        except Exception as e:  # noqa: BLE001 - report to the operator
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_kill failed: %s" % (e,),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {"ok": True})

    def _container_stats(self, server, sender_path: str, call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            stats = self.container_manager.container_stats(container)
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_stats failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            **stats,
        })

    def _container_logs(self, server, sender_path: str, call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            logs = self.container_manager.container_logs(
                container,
                tail=request.get("tail"),
                stream=request.get("stream", "both"),
            )
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_logs failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            **logs,
        })

    def _container_exec(self, server, sender_path: str, call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        command = request.get("command")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id is required",
            })
            return
        if not command or not isinstance(command, list):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "command must be a non-empty list",
            })
            return
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            result = self.container_manager.container_exec(
                container, command,
                timeout_s=float(request.get("timeout", 10.0)),
            )
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_exec failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            **result,
        })

    def _container_top(self, server, sender_path: str, call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        sort_by = request.get("sort_by")
        descending = request.get("descending", True)
        max_depth = request.get("max_depth")
        summary_only = request.get("summary_only", False)
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            if summary_only:
                result = self.container_manager.container_top_summary(
                    container,
                )
            else:
                procs = self.container_manager.container_top(
                    container, sort_by=sort_by,
                    descending=descending, max_depth=max_depth,
                )
                result = {
                    "container_id": container.id,
                    "processes": procs,
                    "count": len(procs),
                }
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_top failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _container_network_stats(self, server, sender_path: str,
                                  call_id: str,
                                  request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            stats = self.container_manager.container_network_stats(container)
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_network_stats failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "container_id": container.id,
            "stats": stats,
        })

    def _image_list(self, server, sender_path: str, call_id: str,
                     request: Dict[str, Any]) -> None:
        try:
            images = self.container_manager.list_images(
                base_dir=request.get("base_dir"),
            )
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "image_list failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "images": images,
            "count": len(images),
        })

    def _image_remove(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        path = request.get("path")
        if not path:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "path is required",
            })
            return
        try:
            self.container_manager.remove_image(path)
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": str(e),
            })
            return
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "image_remove failed: %s" % (e,),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "path": path,
        })

    def _image_export(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        image_path = request.get("image_path")
        if not image_path:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "image_path is required",
            })
            return
        try:
            tar = self.container_manager.export_image(
                image_path, tar_path=request.get("tar_path"),
            )
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "image_export failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, "tar_path": tar,
            "size_bytes": os.path.getsize(tar),
        })

    def _image_import(self, server, sender_path: str, call_id: str,
                      request: Dict[str, Any]) -> None:
        tar_path = request.get("tar_path")
        if not tar_path:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "tar_path is required",
            })
            return
        try:
            imported = self.container_manager.import_image(
                tar_path,
                dest_dir=request.get("dest_dir"),
                name=request.get("name"),
            )
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "image_import failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, "image_path": imported,
        })

    def _container_checkpoint(self, server, sender_path: str, call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            cp = self.container_manager.container_checkpoint(
                container, path=request.get("path"),
            )
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_checkpoint failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "checkpoint_path": cp.get("checkpoint_path"),
            "overlay_entries": cp.get("overlay_entries", 0),
        })

    def _container_restore(self, server, sender_path: str, call_id: str,
                            request: Dict[str, Any]) -> None:
        checkpoint = request.get("checkpoint")
        if not checkpoint or not isinstance(checkpoint, dict):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "checkpoint dict is required",
            })
            return
        try:
            container = self.container_manager.container_restore(checkpoint)
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_restore failed: %s" % (e,),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "container_id": container.id,
            "state": container.state.value,
        })

    def _container_diff(self, server, sender_path: str, call_id: str,
                         request: Dict[str, Any]) -> None:
        cp_a = request.get("checkpoint_a")
        cp_b = request.get("checkpoint_b")
        if not cp_a or not cp_b:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "both checkpoint_a and checkpoint_b are required",
            })
            return
        try:
            diff = self.container_manager.snapshot_diff(cp_a, cp_b)
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_diff failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            **diff,
        })

    def _container_events(self, server, sender_path: str, call_id: str,
                           request: Dict[str, Any]) -> None:
        try:
            events = self.container_manager.container_events(
                tail=request.get("tail"),
                container_id=request.get("container_id"),
                kind=request.get("kind"),
            )
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_events failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "events": events,
            "count": len(events),
        })

    def _container_health(self, server, sender_path: str, call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            health = self.container_manager.container_health(container)
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_health failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            **health,
        })

    def _container_resource_limits(self, server, sender_path: str,
                                    call_id: str,
                                    request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            limits = self.container_manager.container_resource_limits(
                container,
            )
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_resource_limits failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            **limits,
        })

    def _container_scheduling(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            sched = self.container_manager.get_scheduling(container)
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_scheduling failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            **sched,
        })

    def _container_set_nice(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        nice = request.get("nice")
        if nice is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "nice value is required",
            })
            return
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            ok = self.container_manager.set_nice(container, int(nice))
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": ok, "nice": nice,
        })

    def _container_set_affinity(self, server, sender_path: str,
                                 call_id: str,
                                 request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        cores = request.get("cores")
        if not cores:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "cores list is required",
            })
            return
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            ok = self.container_manager.set_cpu_affinity(container, cores)
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": ok, "cores": cores,
        })

    def _container_network_policy(self, server, sender_path: str,
                                   call_id: str,
                                   request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            policy = self.container_manager.get_network_policy(container)
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_network_policy failed: %s" % (e,),
            })
            return        self._reply(server, sender_path, call_id, {
            "ok": True,
            "container_id": container.id,
            "policy": policy,
        })

    def _quota_set(self, server, sender_path: str, call_id: str,
                    request: Dict[str, Any]) -> None:
        owner = request.get("owner")
        if not owner:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "owner is required",
            })
            return
        try:
            quota = self.container_manager.set_quota(
                owner,
                memory_mb=request.get("memory_mb"),
                pid_limit=request.get("pid_limit"),
                max_containers=request.get("max_containers"),
            )
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "quota_set failed: %s" % (e,),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True, "quota": quota,
        })

    def _quota_get(self, server, sender_path: str, call_id: str,
                    request: Dict[str, Any]) -> None:
        owner = request.get("owner")
        if not owner:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "owner is required",
            })
            return
        quota = self.container_manager.get_quota(owner)
        self._reply(server, sender_path, call_id, {
            "ok": True, "owner": owner, "quota": quota,
        })

    def _quota_list(self, server, sender_path: str, call_id: str) -> None:
        quotas = self.container_manager.list_quotas()
        self._reply(server, sender_path, call_id, {
            "ok": True, "quotas": quotas, "count": len(quotas),
        })

    def _quota_delete(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        owner = request.get("owner")
        if not owner:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "owner is required",
            })
            return
        ok = self.container_manager.delete_quota(owner)
        if ok:
            self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": ok, "owner": owner,
        })

    def _quota_usage(self, server, sender_path: str, call_id: str,
                      request: Dict[str, Any]) -> None:
        owner = request.get("owner")
        if not owner:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "owner is required",
            })
            return
        usage = self.container_manager.quota_usage(owner)
        self._reply(server, sender_path, call_id, {
            "ok": True, **usage,
        })


    def _app_install(self, server, sender_path: str, call_id: str,
                      request: Dict[str, Any]) -> None:
        app_path = request.get("app_path")
        if not app_path:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "app_path is required",
            })
            return
        try:
            import os as _os
            if not _os.path.isfile(app_path):
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "file not found: %s" % (app_path,),
                })
                return
            from ui.app_compat import get_app_manager
            mgr = get_app_manager()
            app_id = mgr.install(app_path)
            if app_id is None:
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "unsupported app format or install failed",
                })
                return
            info = mgr.get_app(app_id)
            app_dict = {
                "name": info.name if info else app_path,
                "version": info.version if info else "",
                "compatibility": {
                    "platform": info.platform.value if info else "unknown",
                    "permissions": info.capabilities if info else [],
                },
            } if info else {}
            # Also register in the container manager
            if hasattr(self.container_manager, "register_app"):
                self.container_manager.register_app(
                    app_dict, app_path,
                    name=request.get("name"),
                    sandbox=request.get("sandbox", True),
                )
        except Exception as e:  # noqa: BLE001
            logger.error("ipc: app_install failed: %s", e)
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "app_install failed: %s" % (e,),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "app_id": app_id,
            "app": app_dict,
            "sandbox": request.get("sandbox", True),
        })

    def _app_list(self, server, sender_path: str, call_id: str) -> None:
        try:
            from ui.app_compat import get_app_manager
            mgr = get_app_manager()
            info_list = mgr.list_apps()
            apps = [
                {
                    "app_id": a.app_id,
                    "name": a.name,
                    "platform": a.platform.value,
                    "status": a.status.value,
                    "compatibility": {
                        "platform": a.platform.value,
                        "permissions": a.capabilities,
                    },
                }
                for a in info_list
            ]
        except Exception:
            apps = []
        # Merge with container manager's registry
        if hasattr(self.container_manager, "list_apps"):
            for a in self.container_manager.list_apps():
                if not any(x.get("app_id") == a.get("app_id") for x in apps):
                    apps.append(a)
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "apps": apps,
        })

    def _app_launch(self, server, sender_path: str, call_id: str,
                    request: Dict[str, Any]) -> None:
        app_id = request.get("app_id")
        if not app_id:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "app_id is required",
            })
            return
        # Try the container manager first (apps registered via register_app)
        container = self.container_manager.app_launch(app_id)
        if container is None:
            # Fall back to the AppManager (apps installed via mgr.install)
            try:
                from ui.app_compat import get_app_manager
                mgr = get_app_manager()
                launch_info = mgr.launch(app_id)
                if launch_info is not None:
                    from backend.container import ContainerConfig
                    config = launch_info.get("container_config")
                    if config is not None:
                        container = self.container_manager.create(config)
                        self.container_manager.spawn(container)
            except Exception:  # noqa: BLE001
                pass
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown app: %r" % (app_id,),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "app_id": app_id,
            "container_id": container.id,
            "pid": container.pid,
        })

    def _app_terminate(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        app_id = request.get("app_id")
        if not app_id:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "app_id is required",
            })
            return
        ok = self.container_manager.terminate_app(app_id)
        if not ok:
            # Try the AppManager
            try:
                from ui.app_compat import get_app_manager
                mgr = get_app_manager()
                mgr.terminate(app_id)
                ok = True
            except Exception:  # noqa: BLE001
                pass
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": ok,
            "app_id": app_id,
        })

    # ------------------------------------------------------------------
    # Dependency ordering
    # ------------------------------------------------------------------

    def _container_start_ordered(self, server, sender_path: str,
                                 call_id: str,
                                 request: Dict[str, Any]) -> None:
        container_ids = request.get("container_ids", [])
        if not container_ids:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_ids is required",
            })
            return
        try:
            results = self.container_manager.start_ordered(container_ids)
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True, "results": results,
        })

    def _container_stop_ordered(self, server, sender_path: str,
                                call_id: str,
                                request: Dict[str, Any]) -> None:
        container_ids = request.get("container_ids", [])
        if not container_ids:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_ids is required",
            })
            return
        try:
            results = self.container_manager.stop_ordered(container_ids)
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True, "results": results,
        })

    def _container_dependency_graph(self, server, sender_path: str,
                                    call_id: str,
                                    request: Dict[str, Any]) -> None:
        container_ids = request.get("container_ids")  # optional
        try:
            graph = self.container_manager.get_dependency_graph(
                container_ids=container_ids,
            )
        except Exception as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, "graph": graph,
        })

    # ------------------------------------------------------------------
    # Auto-restart policy
    # ------------------------------------------------------------------

    def _container_restart_info(self, server, sender_path: str,
                                call_id: str,
                                request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": f"container {container_id!r} not found",
            })
            return
        info = self.container_manager.get_restart_info(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **info,
        })

    def _container_set_restart(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        policy = request.get("policy")
        if not container_id or not policy:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and policy are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": f"container {container_id!r} not found",
            })
            return
        try:
            info = self.container_manager.set_restart_policy(
                c, policy,
                max_retries=request.get("max_retries"),
                delay=request.get("delay"),
            )
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True, **info,
        })

    # ------------------------------------------------------------------
    # Environment variable management
    # ------------------------------------------------------------------

    def _container_env_set(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        key = request.get("key")
        value = request.get("value", "")
        if not container_id or not key:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and key are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        self.container_manager.set_env(c, key, value)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "key": key,
        })

    def _container_env_unset(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        key = request.get("key")
        if not container_id or not key:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and key are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        existed = self.container_manager.unset_env(c, key)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "key": key, "existed": existed,
        })

    def _container_env_list(self, server, sender_path: str,
                            call_id: str,
                            request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        env = self.container_manager.list_env(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "environment": env,
        })

    # ------------------------------------------------------------------
    # Snapshot export / import
    # ------------------------------------------------------------------

    def _snapshot_export(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        export_path = request.get("export_path")  # optional
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        try:
            result = self.container_manager.snapshot_export(
                c, export_path=export_path,
            )
        except Exception as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _snapshot_import(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        archive_path = request.get("archive_path")
        if not archive_path:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "archive_path is required",
            })
            return
        try:
            checkpoint = self.container_manager.snapshot_import(
                archive_path,
            )
        except (FileNotFoundError, ValueError) as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        except Exception as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": f"import failed: {e}",
            })
            return
        # Restore the container from the imported checkpoint
        try:
            container = self.container_manager.container_restore(
                checkpoint,
            )
        except Exception as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": f"restore failed: {e}",
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "container_id": container.id,
            "state": container.state.value,
        })

    # ------------------------------------------------------------------
    # Resource usage history
    # ------------------------------------------------------------------

    def _resource_history(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")  # optional
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        history = self.container_manager.get_resource_history(
            c, tail=tail,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "history": history,
            "count": len(history),
        })

    def _resource_record(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        sample = self.container_manager.record_resource_sample(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "sample": sample,
        })

    def _resource_record_start(self, server, sender_path: str,
                                call_id: str,
                                request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        interval = float(request.get("interval", 5.0))
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        self.container_manager.start_resource_recording(c, interval=interval)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "interval": interval,
        })

    def _resource_record_stop(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        self.container_manager.stop_resource_recording(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
        })

    # ------------------------------------------------------------------
    # Resource limits hot-update
    # ------------------------------------------------------------------

    def _container_update_limits(self, server, sender_path: str,
                                  call_id: str,
                                  request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        try:
            result = self.container_manager.update_resource_limits(
                c,
                memory_mb=request.get("memory_mb"),
                pid_limit=request.get("pid_limit"),
                cpu_quota_us=request.get("cpu_quota_us"),
            )
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # ------------------------------------------------------------------
    # Labels / metadata
    # ------------------------------------------------------------------

    def _label_set(self, server, sender_path: str,
                   call_id: str,
                   request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        key = request.get("key")
        value = request.get("value", "")
        if not container_id or not key:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and key are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        self.container_manager.set_label(c, key, value)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "key": key, "value": value,
        })

    def _label_unset(self, server, sender_path: str,
                     call_id: str,
                     request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        key = request.get("key")
        if not container_id or not key:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and key are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        existed = self.container_manager.unset_label(c, key)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "key": key, "existed": existed,
        })

    def _label_list(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        labels = self.container_manager.list_labels(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "labels": labels,
        })

    def _label_filter(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        labels = request.get("labels", {})
        if not labels:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "labels dict is required",
            })
            return
        matches = self.container_manager.filter_by_labels(labels)
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "containers": [
                {"id": c.id, "state": c.state.value}
                for c in matches
            ],
            "count": len(matches),
        })

    # ------------------------------------------------------------------
    # Cgroup2 advanced enforcement
    # ------------------------------------------------------------------

    def _cgroup2_status(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        status = self.container_manager.get_cgroup2_status(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **status,
        })

    def _verify_enforcement(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.verify_enforcement(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # ------------------------------------------------------------------
    # Container locks
    # ------------------------------------------------------------------

    def _lock_acquire(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        non_blocking = request.get("non_blocking", False)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        try:
            acquired = self.container_manager.acquire_lock(
                container_id, non_blocking=non_blocking,
            )
        except BlockingIOError:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"lock held by another process: {container_id}",
                "locked": True,
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "acquired": acquired,
        })

    def _lock_release(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        self.container_manager.release_lock(container_id)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
        })

    def _lock_list(self, server, sender_path: str,
                   call_id: str,
                   request: Dict[str, Any]) -> None:
        locks = self.container_manager.list_locks()
        self._reply(server, sender_path, call_id, {
            "ok": True, "locks": locks, "count": len(locks),
        })

    # ------------------------------------------------------------------
    # Resource alerts
    # ------------------------------------------------------------------

    def _alert_history(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")
        resource = request.get("resource")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        history = self.container_manager.get_alert_history(
            c, tail=tail, resource=resource,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "alerts": history, "count": len(history),
        })

    def _alert_clear(self, server, sender_path: str,
                     call_id: str,
                     request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        count = self.container_manager.clear_alert_history(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "cleared": count,
        })

    def _alert_thresholds(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.set_alert_thresholds(
            c,
            memory_warning=request.get("memory_warning"),
            memory_critical=request.get("memory_critical"),
            pid_warning=request.get("pid_warning"),
            pid_critical=request.get("pid_critical"),
            cpu_throttle=request.get("cpu_throttle"),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Alert history management (enhanced)

    def _alert_acknowledge(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        alert_index = request.get("alert_index", 0)
        acknowledged_by = request.get("acknowledged_by", "user")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.acknowledge_alert(
            c, alert_index, acknowledged_by=acknowledged_by,
        )
        if result is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "invalid alert index",
            })
        else:
            self._reply(server, sender_path, call_id, {
                "ok": True, "alert": result,
            })

    def _alert_suppress(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        resource = request.get("resource")
        level = request.get("level")
        duration_s = request.get("duration_s", 3600.0)
        if not container_id or not resource:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and resource are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.suppress_alert(
            c, resource, level=level, duration_s=duration_s,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _alert_unsuppress(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        resource = request.get("resource")
        level = request.get("level")
        if not container_id or not resource:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and resource are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        removed = self.container_manager.unsuppress_alert(
            c, resource, level=level,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "removed": removed,
        })

    def _alert_statistics(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_alert_statistics(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _alert_suppressions_list(self, server, sender_path: str,
                                 call_id: str,
                                 request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_active_suppressions(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, "suppressions": result,
        })

    # ------------------------------------------------------------------
    # OOM killer protection
    # ------------------------------------------------------------------

    def _oom_status(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        status = self.container_manager.get_oom_status(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **status,
        })

    def _oom_set(self, server, sender_path: str,
                 call_id: str,
                 request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.set_oom_protection(
            c,
            oom_score_adj=request.get("oom_score_adj"),
            oom_kill_disable=request.get("oom_kill_disable"),
            memory_swap_max=request.get("memory_swap_max"),
        )
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _oom_events(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        events = self.container_manager.get_oom_events(c, tail=tail)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "events": events, "count": len(events),
        })

    # ------------------------------------------------------------------
    # Resource dashboard
    # ------------------------------------------------------------------

    def _dashboard(self, server, sender_path: str,
                   call_id: str,
                   request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            # Return dashboard for all containers
            result = self.container_manager.dashboard_all()
            self._reply(server, sender_path, call_id, {
                "ok": True, **result,
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.container_dashboard(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # ------------------------------------------------------------------
    # Resource export
    # ------------------------------------------------------------------

    def _export_history(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        output_path = request.get("output_path")
        fmt = request.get("format", "json")
        tail = request.get("tail")
        if not container_id or not output_path:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and output_path are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        try:
            result = self.container_manager.export_resource_history(
                c, output_path, format=fmt, tail=tail,
            )
        except (ValueError, OSError) as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _export_snapshot(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        output_path = request.get("output_path")
        if not container_id or not output_path:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and output_path are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        try:
            result = self.container_manager.export_container_snapshot(
                c, output_path,
            )
        except (ValueError, OSError) as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def _webhook_register(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        url = request.get("url")
        if not url:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "url is required",
            })
            return
        config = self.container_manager.register_webhook(
            url=url,
            events=request.get("events"),
            secret=request.get("secret"),
            container_filter=request.get("container_filter"),
            enabled=request.get("enabled", True),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **config,
        })

    def _webhook_unregister(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        webhook_id = request.get("webhook_id")
        if not webhook_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "webhook_id is required",
            })
            return
        existed = self.container_manager.unregister_webhook(webhook_id)
        self._reply(server, sender_path, call_id, {
            "ok": True, "webhook_id": webhook_id,
            "existed": existed,
        })

    def _webhook_list(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        webhooks = self.container_manager.list_webhooks()
        self._reply(server, sender_path, call_id, {
            "ok": True, "webhooks": webhooks,
            "count": len(webhooks),
        })

    def _webhook_enable(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        webhook_id = request.get("webhook_id")
        if not webhook_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "webhook_id is required",
            })
            return
        ok = self.container_manager.enable_webhook(webhook_id)
        self._reply(server, sender_path, call_id, {
            "ok": ok, "webhook_id": webhook_id,
        })

    def _webhook_disable(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        webhook_id = request.get("webhook_id")
        if not webhook_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "webhook_id is required",
            })
            return
        ok = self.container_manager.disable_webhook(webhook_id)
        self._reply(server, sender_path, call_id, {
            "ok": ok, "webhook_id": webhook_id,
        })

    # ------------------------------------------------------------------
    # SLA (service level agreements)
    # ------------------------------------------------------------------

    def _sla_check(self, server, sender_path: str,
                   call_id: str,
                   request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.check_sla(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_violations(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        violations = self.container_manager.get_sla_violations(c, tail=tail)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "violations": violations, "count": len(violations),
        })

    def _sla_set(self, server, sender_path: str,
                 call_id: str,
                 request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.set_sla_config(
            c,
            uptime_target=request.get("uptime_target"),
            max_restart_count=request.get("max_restart_count"),
            alert_on_breach=request.get("alert_on_breach"),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # SLA breach escalation

    def _sla_escalation_policy(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        levels = request.get("levels")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.set_sla_escalation_policy(
            c, levels=levels,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_escalation_status(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_sla_escalation_status(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_escalation_reset(self, server, sender_path: str,
                              call_id: str,
                              request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.reset_sla_escalation(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_escalation_history(self, server, sender_path: str,
                                call_id: str,
                                request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        history = self.container_manager.get_sla_escalation_history(
            c, tail=tail,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "history": history,
        })

    # ------------------------------------------------------------------
    # Billing (cost tracking)
    # ------------------------------------------------------------------

    def _billing_rates_set(self, server, sender_path: str,
                            call_id: str,
                            request: Dict[str, Any]) -> None:
        result = self.container_manager.set_billing_rates(
            memory_mb_per_hour=request.get("memory_mb_per_hour"),
            cpu_per_hour=request.get("cpu_per_hour"),
            pid_per_hour=request.get("pid_per_hour"),
            storage_mb_per_hour=request.get("storage_mb_per_hour"),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "rates": result,
        })

    def _billing_rates_get(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        rates = self.container_manager.get_billing_rates()
        self._reply(server, sender_path, call_id, {
            "ok": True, "rates": rates,
        })

    def _billing_record(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.record_billing_usage(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _billing_records(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        records = self.container_manager.get_billing_records(c, tail=tail)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "records": records, "count": len(records),
        })

    def _billing_summary(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            # Summary for all containers
            result = self.container_manager.get_billing_summary_all()
            self._reply(server, sender_path, call_id, {
                "ok": True, **result,
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_billing_summary(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Cost alerts and budget limits

    def _cost_budget_configure(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.configure_cost_budget(
            c,
            daily_limit=request.get("daily_limit"),
            weekly_limit=request.get("weekly_limit"),
            monthly_limit=request.get("monthly_limit"),
            alert_threshold_pct=request.get("alert_threshold_pct", 80.0),
            hard_limit=request.get("hard_limit"),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _cost_budget_check(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.check_cost_budget(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _cost_alerts(self, server, sender_path: str,
                     call_id: str,
                     request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        alerts = self.container_manager.get_cost_alerts(c, tail=tail)
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "alerts": alerts,
        })

    def _cost_budget_config(self, server, sender_path: str,
                            call_id: str,
                            request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_cost_budget_config(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Auto-scaling

    def _autoscale_configure(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.configure_auto_scaling(
            c,
            enabled=request.get("enabled", True),
            min_memory_mb=request.get("min_memory_mb"),
            max_memory_mb=request.get("max_memory_mb"),
            target_memory_pct=request.get("target_memory_pct", 70.0),
            min_cpu_quota=request.get("min_cpu_quota"),
            max_cpu_quota=request.get("max_cpu_quota"),
            target_cpu_pct=request.get("target_cpu_pct", 70.0),
            scale_up_cooldown_s=request.get("scale_up_cooldown_s", 300.0),
            scale_down_cooldown_s=request.get("scale_down_cooldown_s", 600.0),
            evaluation_window_s=request.get("evaluation_window_s", 300.0),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _autoscale_status(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_auto_scaling_status(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _autoscale_apply(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.apply_auto_scaling(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _autoscale_disable(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.disable_auto_scaling(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _autoscale_events(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        events = self.container_manager.get_auto_scaling_events(
            c, tail=tail,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "events": events,
        })

    # Enhanced health checks with auto-restart

    def _health_configure(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.configure_health_check(
            c,
            cmd=request.get("cmd"),
            interval=request.get("interval"),
            timeout=request.get("timeout"),
            retries=request.get("retries"),
            auto_restart=request.get("auto_restart", True),
            max_auto_restarts=request.get("max_auto_restarts", 3),
            restart_cooldown_s=request.get("restart_cooldown_s", 60.0),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _health_trigger(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.trigger_health_check(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _health_config(self, server, sender_path: str,
                       call_id: str,
                       request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_health_check_config(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _health_restart_reset(self, server, sender_path: str,
                              call_id: str,
                              request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.reset_health_restart_count(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _health_restart_history(self, server, sender_path: str,
                                call_id: str,
                                request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        history = self.container_manager.get_health_restart_history(
            c, tail=tail,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "history": history,
        })

    # ------------------------------------------------------------------
    # Forecasting
    # ------------------------------------------------------------------

    def _forecast(self, server, sender_path: str,
                  call_id: str,
                  request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        resource = request.get("resource", "memory")
        horizon_s = float(request.get("horizon_s", 3600.0))
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.forecast_resource(
            c, resource=resource, horizon_s=horizon_s,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _forecast_all(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.forecast_all_resources(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _time_to_exhaustion(self, server, sender_path: str,
                            call_id: str,
                            request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        resource = request.get("resource", "memory")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.estimate_time_to_exhaustion(
            c, resource=resource,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "result": result,
        })

    # Capacity planning

    def _capacity_plan(self, server, sender_path: str,
                       call_id: str,
                       request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        horizon_days = request.get("horizon_days", 30)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.plan_capacity(
            c, horizon_days=horizon_days,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _capacity_plan_all(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        horizon_days = request.get("horizon_days", 30)
        result = self.container_manager.get_capacity_summary_all(
            horizon_days=horizon_days,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Network traffic analysis

    def _network_traffic(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        window_s = request.get("window_s", 300.0)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_network_traffic_analysis(
            c, window_s=window_s,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _network_connections(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_network_connections(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _network_bandwidth_history(self, server, sender_path: str,
                                   call_id: str,
                                   request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        history = self.container_manager.get_network_bandwidth_history(
            c, tail=tail,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "history": history,
        })

    # Anomaly detection

    def _anomaly_detect(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        resource = request.get("resource", "memory")
        window_size = request.get("window_size", 20)
        sensitivity = request.get("sensitivity", 2.0)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.detect_anomalies(
            c, resource=resource, window_size=window_size,
            sensitivity=sensitivity,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _anomaly_detect_all(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        window_size = request.get("window_size", 20)
        sensitivity = request.get("sensitivity", 2.0)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.detect_anomalies_all(
            c, window_size=window_size, sensitivity=sensitivity,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _anomaly_spike(self, server, sender_path: str,
                       call_id: str,
                       request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        resource = request.get("resource", "memory")
        threshold_pct = request.get("threshold_pct", 50.0)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.detect_spike(
            c, resource=resource, threshold_pct=threshold_pct,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _anomaly_trend(self, server, sender_path: str,
                       call_id: str,
                       request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        resource = request.get("resource", "memory")
        window_size = request.get("window_size", 20)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.detect_anomaly_trend(
            c, resource=resource, window_size=window_size,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Resource comparison

    def _compare(self, server, sender_path: str,
                 call_id: str,
                 request: Dict[str, Any]) -> None:
        container_ids = request.get("container_ids", [])
        resource = request.get("resource", "memory")
        if len(container_ids) < 2:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_ids must have at least 2 entries",
            })
            return
        result = self.container_manager.compare_containers(
            container_ids, resource=resource,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _compare_all(self, server, sender_path: str,
                     call_id: str,
                     request: Dict[str, Any]) -> None:
        container_ids = request.get("container_ids", [])
        if len(container_ids) < 2:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_ids must have at least 2 entries",
            })
            return
        result = self.container_manager.compare_all_resources(
            container_ids,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _relative_usage(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        resource = request.get("resource", "memory")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        result = self.container_manager.get_relative_usage(
            container_id, resource=resource,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _top_consumers(self, server, sender_path: str,
                       call_id: str,
                       request: Dict[str, Any]) -> None:
        resource = request.get("resource", "memory")
        top_n = request.get("top_n", 5)
        result = self.container_manager.find_top_consumers(
            resource=resource, top_n=top_n,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "resource": resource,
            "rankings": result,
        })

    # Resource usage recommendations

    def _recommendations(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_recommendations(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _recommendations_all(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        result = self.container_manager.get_recommendations_all()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _recommendations_category(self, server, sender_path: str,
                                  call_id: str,
                                  request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        category = request.get("category", "memory")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_recommendations_by_category(
            c, category=category,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Resource usage baselines

    def _baseline_record(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.record_baseline(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _baseline_get(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_baseline(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _baseline_compare(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        threshold = request.get("threshold_sigma", 2.0)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.compare_baseline(
            c, threshold_sigma=threshold)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _baseline_clear(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.clear_baseline(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Resource usage reports

    def _usage_report(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        result = self.container_manager.generate_usage_report(
            container_ids=request.get("container_ids"),
            include_trends=request.get("include_trends", True),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _alert_summary(self, server, sender_path: str,
                       call_id: str,
                       request: Dict[str, Any]) -> None:
        result = self.container_manager.generate_alert_summary()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Priority scheduling

    def _set_cpu_weight(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        weight = request.get("weight", 100)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.set_cpu_weight(c, weight)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _set_io_weight(self, server, sender_path: str,
                       call_id: str,
                       request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        weight = request.get("weight", 100)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.set_io_weight(c, weight)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _get_priority(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_priority(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Dependency health

    def _dependency_health(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_dependency_health(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _dependency_health_reverse(self, server, sender_path: str,
                                   call_id: str,
                                   request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_reverse_dependency_health(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Process management

    def _process_kill(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        pid = request.get("pid")
        sig = request.get("signal", 15)  # SIGTERM
        if not container_id or pid is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id and pid are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.kill_process(
            c, pid=int(pid), signal=int(sig))
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _process_list(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.list_processes(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _process_signal_all(self, server, sender_path: str,
                            call_id: str,
                            request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        sig = request.get("signal", 15)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.signal_all(
            c, signal_num=int(sig))
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Snapshot scheduling

    def _snapshot_schedule_set(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.configure_snapshot_schedule(
            c,
            enabled=request.get("enabled", True),
            interval_s=request.get("interval_s", 3600.0),
            max_snapshots=request.get("max_snapshots", 10),
            label_prefix=request.get("label_prefix", "scheduled"),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _snapshot_schedule_get(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_snapshot_schedule(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _snapshot_schedule_disable(self, server, sender_path: str,
                                   call_id: str,
                                   request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.disable_snapshot_schedule(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _snapshot_schedule_run(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.run_scheduled_snapshot(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _snapshot_schedule_list(self, server, sender_path: str,
                                call_id: str,
                                request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.list_scheduled_snapshots(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Batch operations

    def _batch_start(self, server, sender_path: str,
                     call_id: str,
                     request: Dict[str, Any]) -> None:
        result = self.container_manager.batch_start(
            labels=request.get("labels"),
            name_pattern=request.get("name_pattern"),
            states=request.get("states"),
            container_ids=request.get("container_ids"),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _batch_stop(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        result = self.container_manager.batch_stop(
            labels=request.get("labels"),
            name_pattern=request.get("name_pattern"),
            states=request.get("states"),
            container_ids=request.get("container_ids"),
            timeout_s=request.get("timeout_s", 10.0),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _batch_kill(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        result = self.container_manager.batch_kill(
            labels=request.get("labels"),
            name_pattern=request.get("name_pattern"),
            states=request.get("states"),
            container_ids=request.get("container_ids"),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Resource profiling

    def _resource_profile(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_resource_profile(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _resource_profile_history(self, server, sender_path: str,
                                  call_id: str,
                                  request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        tail = request.get("tail")
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        history = self.container_manager.get_resource_profile_history(
            c, tail=tail,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, "container_id": container_id,
            "history": history,
        })

    def _resource_profile_top(self, server, sender_path: str,
                              call_id: str,
                              request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        resource = request.get("resource", "rss_bytes")
        top_n = request.get("top_n", 5)
        if not container_id:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container_id is required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_resource_profile_top_consumers(
            c, resource=resource, top_n=top_n,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _save_state(self) -> None:
        """Best-effort: tell the daemon to persist the container
        manifest after a mutation (plan §4.5). A state-save failure
        must never break the control reply."""
        if self.state_saver is None:
            return
        try:
            self.state_saver()
        except Exception:  # noqa: BLE001 - persistence is best effort
            logger.exception("ipc: %s could not persist state",
                             self.SERVICE_NAME)

    @staticmethod
    def _reply(server, sender_path: str, call_id: str,
               body: Dict[str, Any]) -> None:
        server.reply(
            sender_path,
            call_id,
            json.dumps(body, sort_keys=True).encode("utf-8"),
        )


__all__ = ["ControlService", "DEFAULT_OPERATOR_ID"]
