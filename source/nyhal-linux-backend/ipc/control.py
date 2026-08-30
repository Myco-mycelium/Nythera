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
            elif op == "image_create_layer":
                self._image_create_layer(server, sender_path,
                                         msg.message_id, request)
            elif op == "image_list_layers":
                self._image_list_layers(server, sender_path,
                                         msg.message_id, request)
            elif op == "image_remove_layer":
                self._image_remove_layer(server, sender_path,
                                          msg.message_id, request)
            elif op == "image_diff":
                self._image_diff(server, sender_path,
                                 msg.message_id, request)
            elif op == "registry_pull":
                self._registry_pull(server, sender_path,
                                    msg.message_id, request)
            elif op == "registry_push":
                self._registry_push(server, sender_path,
                                    msg.message_id, request)
            elif op == "registry_catalog":
                self._registry_catalog(server, sender_path,
                                       msg.message_id, request)
            elif op == "cluster_register_node":
                self._cluster_register_node(server, sender_path,
                                             msg.message_id, request)
            elif op == "cluster_unregister_node":
                self._cluster_unregister_node(server, sender_path,
                                              msg.message_id, request)
            elif op == "cluster_heartbeat":
                self._cluster_heartbeat(server, sender_path,
                                        msg.message_id, request)
            elif op == "cluster_nodes":
                self._cluster_nodes(server, sender_path,
                                    msg.message_id, request)
            elif op == "cluster_status":
                self._cluster_status(server, sender_path,
                                     msg.message_id, request)
            elif op == "cluster_schedule":
                self._cluster_schedule(server, sender_path,
                                       msg.message_id, request)
            elif op == "cluster_containers":
                self._cluster_containers(server, sender_path,
                                         msg.message_id, request)
            elif op == "cluster_drain_node":
                self._cluster_drain_node(server, sender_path,
                                         msg.message_id, request)
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
            elif op == "event_correlate":
                self._event_correlate(
                    server, sender_path, msg.message_id, request)
            elif op == "event_timeline":
                self._event_timeline(
                    server, sender_path, msg.message_id, request)
            elif op == "network_rule_add":
                self._network_rule_add(
                    server, sender_path, msg.message_id, request)
            elif op == "network_rule_remove":
                self._network_rule_remove(
                    server, sender_path, msg.message_id, request)
            elif op == "network_rules_list":
                self._network_rules_list(
                    server, sender_path, msg.message_id, request)
            elif op == "network_rules_clear":
                self._network_rules_clear(
                    server, sender_path, msg.message_id, request)
            elif op == "compare_containers":
                self._compare_containers(
                    server, sender_path, msg.message_id, request)
            elif op == "check_thresholds":
                self._check_thresholds(
                    server, sender_path, msg.message_id, request)
            elif op == "threshold_status":
                self._threshold_status(
                    server, sender_path, msg.message_id, request)
            elif op == "set_scheduling_priority":
                self._set_scheduling_priority(
                    server, sender_path, msg.message_id, request)
            elif op == "scheduling_queue":
                self._scheduling_queue(
                    server, sender_path, msg.message_id, request)
            elif op == "ready_containers":
                self._ready_containers(
                    server, sender_path, msg.message_id, request)
            elif op == "audit_record":
                self._audit_record(
                    server, sender_path, msg.message_id, request)
            elif op == "audit_log":
                self._audit_log(
                    server, sender_path, msg.message_id, request)
            elif op == "audit_summary":
                self._audit_summary(
                    server, sender_path, msg.message_id, request)
            elif op == "cost_allocate":
                self._cost_allocate(
                    server, sender_path, msg.message_id, request)
            elif op == "cost_allocate_all":
                self._cost_allocate_all(
                    server, sender_path, msg.message_id, request)
            elif op == "budget_set":
                self._budget_set(
                    server, sender_path, msg.message_id, request)
            elif op == "budget_get":
                self._budget_get(
                    server, sender_path, msg.message_id, request)
            elif op == "budget_check":
                self._budget_check(
                    server, sender_path, msg.message_id, request)
            elif op == "budget_check_all":
                self._budget_check_all(
                    server, sender_path, msg.message_id, request)
            elif op == "budget_clear":
                self._budget_clear(
                    server, sender_path, msg.message_id, request)
            elif op == "remediation_configure":
                self._remediation_configure(
                    server, sender_path, msg.message_id, request)
            elif op == "remediation_execute":
                self._remediation_execute(
                    server, sender_path, msg.message_id, request)
            elif op == "remediation_status":
                self._remediation_status(
                    server, sender_path, msg.message_id, request)
            elif op == "remediation_history":
                self._remediation_history(
                    server, sender_path, msg.message_id, request)
            elif op == "tenant_config_set":
                self._tenant_config_set(
                    server, sender_path, msg.message_id, request)
            elif op == "tenant_config_get":
                self._tenant_config_get(
                    server, sender_path, msg.message_id, request)
            elif op == "tenant_config_list":
                self._tenant_config_list(
                    server, sender_path, msg.message_id, request)
            elif op == "fair_share":
                self._fair_share(
                    server, sender_path, msg.message_id, request)
            elif op == "tenant_enforce":
                self._tenant_enforce(
                    server, sender_path, msg.message_id, request)
            elif op == "tenant_usage_summary":
                self._tenant_usage_summary(
                    server, sender_path, msg.message_id, request)
            elif op == "event_log_export":
                self._event_log_export(
                    server, sender_path, msg.message_id, request)
            elif op == "event_log_import":
                self._event_log_import(
                    server, sender_path, msg.message_id, request)
            elif op == "health_score":
                self._health_score(
                    server, sender_path, msg.message_id, request)
            elif op == "health_score_all":
                self._health_score_all(
                    server, sender_path, msg.message_id, request)
            elif op == "event_log_compress":
                self._event_log_compress(
                    server, sender_path, msg.message_id, request)
            elif op == "archive_schedule_set":
                self._archive_schedule_set(
                    server, sender_path, msg.message_id, request)
            elif op == "archive_schedule_get":
                self._archive_schedule_get(
                    server, sender_path, msg.message_id, request)
            elif op == "archive_schedule_disable":
                self._archive_schedule_disable(
                    server, sender_path, msg.message_id, request)
            elif op == "archive_run_now":
                self._archive_run_now(
                    server, sender_path, msg.message_id, request)
            elif op == "archive_list":
                self._archive_list(
                    server, sender_path, msg.message_id, request)
            elif op == "archive_get":
                self._archive_get(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_breach_process":
                self._sla_breach_process(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_breach_process_all":
                self._sla_breach_process_all(
                    server, sender_path, msg.message_id, request)
            elif op == "smart_remediate":
                self._smart_remediate(
                    server, sender_path, msg.message_id, request)
            elif op == "smart_remediate_all":
                self._smart_remediate_all(
                    server, sender_path, msg.message_id, request)
            elif op == "usage_patterns":
                self._usage_patterns(
                    server, sender_path, msg.message_id, request)
            elif op == "optimization_actions":
                self._optimization_actions(
                    server, sender_path, msg.message_id, request)
            elif op == "rightsize":
                self._rightsize(
                    server, sender_path, msg.message_id, request)
            elif op == "rightsize_all":
                self._rightsize_all(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_compliance_set":
                self._sla_compliance_set(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_compliance_get":
                self._sla_compliance_get(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_compliance_check":
                self._sla_compliance_check(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_compliance_check_all":
                self._sla_compliance_check_all(
                    server, sender_path, msg.message_id, request)
            elif op == "visualization_data":
                self._visualization_data(
                    server, sender_path, msg.message_id, request)
            elif op == "fleet_visualization":
                self._fleet_visualization(
                    server, sender_path, msg.message_id, request)
            elif op == "anomaly_remediate":
                self._anomaly_remediate(
                    server, sender_path, msg.message_id, request)
            elif op == "anomaly_remediate_all":
                self._anomaly_remediate_all(
                    server, sender_path, msg.message_id, request)
            elif op == "monitoring_configure":
                self._monitoring_configure(
                    server, sender_path, msg.message_id, request)
            elif op == "monitoring_get":
                self._monitoring_get(
                    server, sender_path, msg.message_id, request)
            elif op == "monitoring_check":
                self._monitoring_check(
                    server, sender_path, msg.message_id, request)
            elif op == "monitoring_check_all":
                self._monitoring_check_all(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_auto_escalation_configure":
                self._sla_auto_escalation_configure(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_breach_record":
                self._sla_breach_record(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_auto_escalation_status":
                self._sla_auto_escalation_status(
                    server, sender_path, msg.message_id, request)
            elif op == "sla_auto_escalation_reset":
                self._sla_auto_escalation_reset(
                    server, sender_path, msg.message_id, request)
            elif op == "cost_optimize":
                self._cost_optimize(
                    server, sender_path, msg.message_id, request)
            elif op == "cost_optimize_all":
                self._cost_optimize_all(
                    server, sender_path, msg.message_id, request)
            elif op == "anomaly_predict":
                self._anomaly_predict(
                    server, sender_path, msg.message_id, request)
            elif op == "anomaly_predict_all":
                self._anomaly_predict_all(
                    server, sender_path, msg.message_id, request)
            elif op == "predictive_scaling_configure":
                self._predictive_scaling_configure(
                    server, sender_path, msg.message_id, request)
            elif op == "predictive_scaling_evaluate":
                self._predictive_scaling_evaluate(
                    server, sender_path, msg.message_id, request)
            elif op == "predictive_scaling_evaluate_all":
                self._predictive_scaling_evaluate_all(
                    server, sender_path, msg.message_id, request)
            elif op == "predictive_scaling_status":
                self._predictive_scaling_status(
                    server, sender_path, msg.message_id, request)
            elif op == "anomaly_correlate":
                self._anomaly_correlate(
                    server, sender_path, msg.message_id, request)
            elif op == "anomaly_correlation_report":
                self._anomaly_correlation_report(
                    server, sender_path, msg.message_id, request)
            elif op == "resource_heatmap":
                self._resource_heatmap(
                    server, sender_path, msg.message_id, request)
            elif op == "container_pressure_detail":
                self._container_pressure_detail(
                    server, sender_path, msg.message_id, request)
            elif op == "record_pressure_snapshot":
                self._record_pressure_snapshot(
                    server, sender_path, msg.message_id, request)
            elif op == "classify_tier":
                self._classify_tier(
                    server, sender_path, msg.message_id, request)
            elif op == "fleet_tier_summary":
                self._fleet_tier_summary(
                    server, sender_path, msg.message_id, request)
            elif op == "suggest_tier_upgrade":
                self._suggest_tier_upgrade(
                    server, sender_path, msg.message_id, request)
            elif op == "log_stream":
                self._log_stream(server, sender_path,
                                 msg.message_id, request)
            elif op == "log_filter":
                self._log_filter(server, sender_path,
                                 msg.message_id, request)
            elif op == "log_export":
                self._log_export(server, sender_path,
                                 msg.message_id, request)
            elif op == "image_dedup":
                self._image_dedup(server, sender_path,
                                  msg.message_id, request)
            elif op == "image_gc":
                self._image_gc(server, sender_path,
                               msg.message_id, request)
            elif op == "image_layer_stats":
                self._image_layer_stats(server, sender_path,
                                        msg.message_id, request)
            elif op == "dns_generate":
                self._dns_generate(server, sender_path,
                                   msg.message_id, request)
            elif op == "dns_resolve":
                self._dns_resolve(server, sender_path,
                                  msg.message_id, request)
            elif op == "dns_get_config":
                self._dns_get_config(server, sender_path,
                                     msg.message_id, request)
            elif op == "dns_update":
                self._dns_update(server, sender_path,
                                 msg.message_id, request)
            elif op == "create_network":
                self._create_network(server, sender_path,
                                     msg.message_id, request)
            elif op == "remove_network":
                self._remove_network(server, sender_path,
                                     msg.message_id, request)
            elif op == "list_networks":
                self._list_networks(server, sender_path,
                                    msg.message_id, request)
            elif op == "connect_network":
                self._connect_network(server, sender_path,
                                      msg.message_id, request)
            elif op == "disconnect_network":
                self._disconnect_network(server, sender_path,
                                         msg.message_id, request)
            elif op == "network_topology":
                self._network_topology(server, sender_path,
                                       msg.message_id, request)
            elif op == "network_dns_resolve":
                self._network_dns_resolve(server, sender_path,
                                          msg.message_id, request)
            elif op == "test_connectivity":
                self._test_connectivity(server, sender_path,
                                        msg.message_id, request)
            elif op == "plan_migration":
                self._plan_migration(server, sender_path,
                                     msg.message_id, request)
            elif op == "execute_migration":
                self._execute_migration(server, sender_path,
                                        msg.message_id, request)
            elif op == "migration_history":
                self._migration_history(server, sender_path,
                                        msg.message_id, request)
            elif op == "migration_cost":
                self._migration_cost(server, sender_path,
                                     msg.message_id, request)
            elif op == "configure_alert_channel":
                self._configure_alert_channel(server, sender_path,
                                              msg.message_id, request)
            elif op == "remove_alert_channel":
                self._remove_alert_channel(server, sender_path,
                                           msg.message_id, request)
            elif op == "list_alert_channels":
                self._list_alert_channels(server, sender_path,
                                          msg.message_id, request)
            elif op == "enable_alert_channel":
                self._enable_alert_channel(server, sender_path,
                                           msg.message_id, request)
            elif op == "disable_alert_channel":
                self._disable_alert_channel(server, sender_path,
                                            msg.message_id, request)
            elif op == "configure_alert_rules":
                self._configure_alert_rules(server, sender_path,
                                            msg.message_id, request)
            elif op == "get_alert_rules":
                self._get_alert_rules(server, sender_path,
                                      msg.message_id, request)
            elif op == "evaluate_alerts":
                self._evaluate_alerts(server, sender_path,
                                      msg.message_id, request)
            elif op == "alert_history":
                self._alert_history(server, sender_path,
                                    msg.message_id, request)
            elif op == "detect_anomalies":
                self._detect_anomalies(server, sender_path,
                                       msg.message_id, request)
            elif op == "detect_fleet_anomalies":
                self._detect_fleet_anomalies(server, sender_path,
                                            msg.message_id, request)
            elif op == "diff_snapshots":
                self._diff_snapshots(server, sender_path,
                                     msg.message_id, request)
            elif op == "rollback_snapshot":
                self._rollback_snapshot(server, sender_path,
                                       msg.message_id, request)
            elif op == "optimize_placement":
                self._optimize_placement(server, sender_path,
                                        msg.message_id, request)
            elif op == "placement_score":
                self._placement_score(server, sender_path,
                                     msg.message_id, request)
            elif op == "configure_auto_scaling":
                self._configure_auto_scaling(server, sender_path,
                                            msg.message_id, request)
            elif op == "evaluate_and_adjust":
                self._evaluate_and_adjust(server, sender_path,
                                         msg.message_id, request)
            elif op == "auto_scaling_status":
                self._auto_scaling_status(server, sender_path,
                                         msg.message_id, request)
            elif op == "batch_evaluate_scaling":
                self._batch_evaluate_scaling(server, sender_path,
                                            msg.message_id, request)
            elif op == "generate_dependency_graph":
                self._generate_dependency_graph(server, sender_path,
                                               msg.message_id, request)
            elif op == "get_critical_path":
                self._get_critical_path(server, sender_path,
                                       msg.message_id, request)
            elif op == "register_federation_peer":
                self._register_federation_peer(server, sender_path,
                                              msg.message_id, request)
            elif op == "unregister_federation_peer":
                self._unregister_federation_peer(server, sender_path,
                                                msg.message_id, request)
            elif op == "list_federation_peers":
                self._list_federation_peers(server, sender_path,
                                           msg.message_id, request)
            elif op == "share_container_with_peer":
                self._share_container_with_peer(server, sender_path,
                                              msg.message_id, request)
            elif op == "unshare_container_from_peer":
                self._unshare_container_from_peer(server, sender_path,
                                                msg.message_id, request)
            elif op == "share_resources_with_peer":
                self._share_resources_with_peer(server, sender_path,
                                              msg.message_id, request)
            elif op == "get_federation_status":
                self._get_federation_status(server, sender_path,
                                           msg.message_id, request)
            elif op == "plan_cross_cluster_migration":
                self._plan_cross_cluster_migration(server, sender_path,
                                                 msg.message_id, request)
            elif op == "configure_event_trigger":
                self._configure_event_trigger(server, sender_path,
                                             msg.message_id, request)
            elif op == "remove_event_trigger":
                self._remove_event_trigger(server, sender_path,
                                         msg.message_id, request)
            elif op == "list_event_triggers":
                self._list_event_triggers(server, sender_path,
                                        msg.message_id, request)
            elif op == "enable_event_trigger":
                self._enable_event_trigger(server, sender_path,
                                         msg.message_id, request)
            elif op == "disable_event_trigger":
                self._disable_event_trigger(server, sender_path,
                                          msg.message_id, request)
            elif op == "fire_event":
                self._fire_event(server, sender_path,
                               msg.message_id, request)
            elif op == "get_event_log":
                self._get_event_log(server, sender_path,
                                  msg.message_id, request)
            elif op == "get_trigger_stats":
                self._get_trigger_stats(server, sender_path,
                                      msg.message_id, request)
            elif op == "generate_cluster_dashboard":
                self._generate_cluster_dashboard(server, sender_path,
                                              msg.message_id, request)
            elif op == "configure_network_rule":
                self._configure_network_rule(server, sender_path,
                                          msg.message_id, request)
            elif op == "remove_network_rule":
                self._remove_network_rule(server, sender_path,
                                        msg.message_id, request)
            elif op == "list_network_rules":
                self._list_network_rules(server, sender_path,
                                      msg.message_id, request)
            elif op == "enable_network_rule":
                self._enable_network_rule(server, sender_path,
                                       msg.message_id, request)
            elif op == "disable_network_rule":
                self._disable_network_rule(server, sender_path,
                                        msg.message_id, request)
            elif op == "evaluate_network_access":
                self._evaluate_network_access(server, sender_path,
                                            msg.message_id, request)
            elif op == "get_network_rule_stats":
                self._get_network_rule_stats(server, sender_path,
                                          msg.message_id, request)
            elif op == "create_backup":
                self._create_backup(server, sender_path,
                                   msg.message_id, request)
            elif op == "list_backups":
                self._list_backups(server, sender_path,
                                  msg.message_id, request)
            elif op == "get_backup":
                self._get_backup(server, sender_path,
                               msg.message_id, request)
            elif op == "delete_backup":
                self._delete_backup(server, sender_path,
                                  msg.message_id, request)
            elif op == "restore_from_backup":
                self._restore_from_backup(server, sender_path,
                                        msg.message_id, request)
            elif op == "configure_backup_policy":
                self._configure_backup_policy(server, sender_path,
                                           msg.message_id, request)
            elif op == "get_backup_policy":
                self._get_backup_policy(server, sender_path,
                                      msg.message_id, request)
            elif op == "get_dr_status":
                self._get_dr_status(server, sender_path,
                                  msg.message_id, request)
            elif op == "aggregate_cluster_logs":
                self._aggregate_cluster_logs(server, sender_path,
                                          msg.message_id, request)
            elif op == "search_cluster_logs":
                self._search_cluster_logs(server, sender_path,
                                       msg.message_id, request)
            elif op == "get_log_stats":
                self._get_log_stats(server, sender_path,
                                  msg.message_id, request)
            elif op == "scan_container_security":
                self._scan_container_security(server, sender_path,
                                           msg.message_id, request)
            elif op == "scan_fleet_security":
                self._scan_fleet_security(server, sender_path,
                                       msg.message_id, request)
            elif op == "get_security_summary":
                self._get_security_summary(server, sender_path,
                                        msg.message_id, request)
            elif op == "scan_image_vulnerabilities":
                self._scan_image_vulnerabilities(server, sender_path,
                                              msg.message_id, request)
            elif op == "scan_container_vulnerabilities":
                self._scan_container_vulnerabilities(server, sender_path,
                                                 msg.message_id, request)
            elif op == "scan_fleet_vulnerabilities":
                self._scan_fleet_vulnerabilities(server, sender_path,
                                             msg.message_id, request)
            elif op == "get_vulnerability_summary":
                self._get_vulnerability_summary(server, sender_path,
                                            msg.message_id, request)
            elif op == "profile_container_performance":
                self._profile_container_performance(server, sender_path,
                                                msg.message_id, request)
            elif op == "profile_fleet_performance":
                self._profile_fleet_performance(server, sender_path,
                                            msg.message_id, request)
            elif op == "get_performance_recommendations":
                self._get_performance_recommendations(server, sender_path,
                                                  msg.message_id, request)
            elif op == "forecast_resource_needs":
                self._forecast_resource_needs(server, sender_path,
                                          msg.message_id, request)
            elif op == "forecast_fleet_capacity":
                self._forecast_fleet_capacity(server, sender_path,
                                          msg.message_id, request)
            elif op == "get_capacity_recommendations":
                self._get_capacity_recommendations(server, sender_path,
                                               msg.message_id, request)
            elif op == "read_container_file":
                self._read_container_file(server, sender_path,
                                       msg.message_id, request)
            elif op == "write_container_file":
                self._write_container_file(server, sender_path,
                                        msg.message_id, request)
            elif op == "list_container_files":
                self._list_container_files(server, sender_path,
                                       msg.message_id, request)
            elif op == "delete_container_file":
                self._delete_container_file(server, sender_path,
                                         msg.message_id, request)
            elif op == "get_file_info":
                self._get_file_info(server, sender_path,
                                 msg.message_id, request)
            elif op == "get_process_tree":
                self._get_process_tree(server, sender_path,
                                    msg.message_id, request)
            elif op == "get_process_stats":
                self._get_process_stats(server, sender_path,
                                     msg.message_id, request)
            elif op == "generate_comparison_report":
                self._generate_comparison_report(server, sender_path,
                                             msg.message_id, request)
            elif op == "generate_cost_report":
                self._generate_cost_report(server, sender_path,
                                       msg.message_id, request)
            elif op == "configure_health_check":
                self._configure_health_check(server, sender_path,
                                        msg.message_id, request)
            elif op == "get_health_check":
                self._get_health_check(server, sender_path,
                                  msg.message_id, request)
            elif op == "evaluate_health_check":
                self._evaluate_health_check(server, sender_path,
                                       msg.message_id, request)
            elif op == "get_readiness_status":
                self._get_readiness_status(server, sender_path,
                                      msg.message_id, request)
            elif op == "get_liveness_status":
                self._get_liveness_status(server, sender_path,
                                     msg.message_id, request)
            elif op == "fleet_health_overview":
                self._fleet_health_overview(server, sender_path,
                                       msg.message_id)
            elif op == "configure_escalation_chain":
                self._configure_escalation_chain(server, sender_path,
                                           msg.message_id, request)
            elif op == "evaluate_escalation":
                self._evaluate_escalation(server, sender_path,
                                    msg.message_id, request)
            elif op == "get_escalation_status":
                self._get_escalation_status(server, sender_path,
                                       msg.message_id, request)
            elif op == "reset_escalation_state":
                self._reset_escalation_state(server, sender_path,
                                        msg.message_id, request)
            elif op == "disable_escalation_chain":
                self._disable_escalation_chain(server, sender_path,
                                         msg.message_id, request)
            elif op == "generate_compliance_report":
                self._generate_compliance_report(server, sender_path,
                                           msg.message_id, request)
            elif op == "export_audit_logs":
                self._export_audit_logs(server, sender_path,
                                  msg.message_id, request)
            elif op == "get_compliance_summary":
                self._get_compliance_summary(server, sender_path,
                                        msg.message_id, request)
            elif op == "create_secret":
                self._create_secret(server, sender_path,
                               msg.message_id, request)
            elif op == "get_secret":
                self._get_secret(server, sender_path,
                            msg.message_id, request)
            elif op == "delete_secret":
                self._delete_secret(server, sender_path,
                               msg.message_id, request)
            elif op == "rotate_secret":
                self._rotate_secret(server, sender_path,
                               msg.message_id, request)
            elif op == "list_secrets":
                self._list_secrets(server, sender_path,
                              msg.message_id, request)
            elif op == "get_secret_usage":
                self._get_secret_usage(server, sender_path,
                                  msg.message_id)
            elif op == "create_namespace":
                self._create_namespace(server, sender_path,
                                  msg.message_id, request)
            elif op == "set_resource_quota":
                self._set_resource_quota(server, sender_path,
                                    msg.message_id, request)
            elif op == "get_resource_quota":
                self._get_resource_quota(server, sender_path,
                                    msg.message_id, request)
            elif op == "check_quota_compliance":
                self._check_quota_compliance(server, sender_path,
                                        msg.message_id, request)
            elif op == "list_namespaces":
                self._list_namespaces(server, sender_path,
                                 msg.message_id)
            elif op == "delete_namespace":
                self._delete_namespace(server, sender_path,
                                  msg.message_id, request)
            elif op == "get_namespace_summary":
                self._get_namespace_summary(server, sender_path,
                                       msg.message_id)
            elif op == "record_deployment":
                self._record_deployment(server, sender_path,
                                   msg.message_id, request)
            elif op == "get_deployment_history":
                self._get_deployment_history(server, sender_path,
                                       msg.message_id, request)
            elif op == "rollback_deployment":
                self._rollback_deployment(server, sender_path,
                                     msg.message_id, request)
            elif op == "get_deployment_diff":
                self._get_deployment_diff(server, sender_path,
                                     msg.message_id, request)
            elif op == "get_rollback_candidates":
                self._get_rollback_candidates(server, sender_path,
                                         msg.message_id, request)
            elif op == "get_deployment_status":
                self._get_deployment_status(server, sender_path,
                                       msg.message_id, request)
            elif op == "configure_graceful_shutdown":
                self._configure_graceful_shutdown(server, sender_path,
                                            msg.message_id, request)
            elif op == "initiate_graceful_shutdown":
                self._initiate_graceful_shutdown(server, sender_path,
                                           msg.message_id, request)
            elif op == "get_shutdown_status":
                self._get_shutdown_status(server, sender_path,
                                     msg.message_id, request)
            elif op == "force_shutdown":
                self._force_shutdown(server, sender_path,
                               msg.message_id, request)
            elif op == "batch_graceful_shutdown":
                self._batch_graceful_shutdown(server, sender_path,
                                        msg.message_id, request)
            elif op == "get_drain_progress":
                self._get_drain_progress(server, sender_path,
                                    msg.message_id, request)
            elif op == "register_config_watcher":
                self._register_config_watcher(server, sender_path,
                                         msg.message_id, request)
            elif op == "trigger_config_reload":
                self._trigger_config_reload(server, sender_path,
                                      msg.message_id, request)
            elif op == "get_config_watchers":
                self._get_config_watchers(server, sender_path,
                                     msg.message_id, request)
            elif op == "remove_config_watcher":
                self._remove_config_watcher(server, sender_path,
                                       msg.message_id, request)
            elif op == "hot_reload_config":
                self._hot_reload_config(server, sender_path,
                                  msg.message_id, request)
            elif op == "get_reload_history":
                self._get_reload_history(server, sender_path,
                                    msg.message_id, request)
            elif op == "record_event":
                self._record_event(server, sender_path,
                              msg.message_id, request)
            elif op == "correlate_events":
                self._correlate_events(server, sender_path,
                                  msg.message_id, request)
            elif op == "analyze_event_patterns":
                self._analyze_event_patterns(server, sender_path,
                                        msg.message_id, request)
            elif op == "suggest_root_cause":
                self._suggest_root_cause(server, sender_path,
                                    msg.message_id, request)
            elif op == "get_event_timeline":
                self._get_event_timeline(server, sender_path,
                                    msg.message_id, request)
            elif op == "configure_network_monitoring":
                self._configure_network_monitoring(server, sender_path,
                                              msg.message_id, request)
            elif op == "record_network_sample":
                self._record_network_sample(server, sender_path,
                                       msg.message_id, request)
            elif op == "get_network_latency_stats":
                self._get_network_latency_stats(server, sender_path,
                                           msg.message_id, request)
            elif op == "get_bandwidth_stats":
                self._get_bandwidth_stats(server, sender_path,
                                     msg.message_id, request)
            elif op == "get_network_health":
                self._get_network_health(server, sender_path,
                                    msg.message_id, request)
            elif op == "fleet_network_overview":
                self._fleet_network_overview(server, sender_path,
                                        msg.message_id)
            elif op == "configure_storage_profiling":
                self._configure_storage_profiling(server, sender_path,
                                            msg.message_id, request)
            elif op == "record_storage_io":
                self._record_storage_io(server, sender_path,
                                   msg.message_id, request)
            elif op == "get_storage_io_stats":
                self._get_storage_io_stats(server, sender_path,
                                      msg.message_id, request)
            elif op == "get_storage_io_latency":
                self._get_storage_io_latency(server, sender_path,
                                        msg.message_id, request)
            elif op == "clear_storage_cache":
                self._clear_storage_cache(server, sender_path,
                                    msg.message_id, request)
            elif op == "get_storage_hot_paths":
                self._get_storage_hot_paths(server, sender_path,
                                       msg.message_id, request)
            elif op == "initialize_audit_integrity":
                self._initialize_audit_integrity(server, sender_path,
                                            msg.message_id, request)
            elif op == "append_audit_event":
                self._append_audit_event(server, sender_path,
                                    msg.message_id, request)
            elif op == "verify_audit_integrity":
                self._verify_audit_integrity(server, sender_path,
                                        msg.message_id, request)
            elif op == "get_audit_integrity_report":
                self._get_audit_integrity_report(server, sender_path,
                                            msg.message_id, request)
            elif op == "get_tamper_summary":
                self._get_tamper_summary(server, sender_path,
                                    msg.message_id)
            elif op == "configure_rate_limit":
                self._configure_rate_limit(server, sender_path,
                                     msg.message_id, request)
            elif op == "check_rate_limit":
                self._check_rate_limit(server, sender_path,
                                  msg.message_id, request)
            elif op == "get_rate_limit_stats":
                self._get_rate_limit_stats(server, sender_path,
                                      msg.message_id, request)
            elif op == "get_all_rate_limiters":
                self._get_all_rate_limiters(server, sender_path,
                                       msg.message_id, request)
            elif op == "reset_rate_limit":
                self._reset_rate_limit(server, sender_path,
                                  msg.message_id, request)
            elif op == "delete_rate_limit":
                self._delete_rate_limit(server, sender_path,
                                  msg.message_id, request)
            elif op == "create_feature_flag":
                self._create_feature_flag(server, sender_path,
                                    msg.message_id, request)
            elif op == "evaluate_feature_flag":
                self._evaluate_feature_flag(server, sender_path,
                                       msg.message_id, request)
            elif op == "update_feature_flag":
                self._update_feature_flag(server, sender_path,
                                     msg.message_id, request)
            elif op == "list_feature_flags":
                self._list_feature_flags(server, sender_path,
                                    msg.message_id)
            elif op == "delete_feature_flag":
                self._delete_feature_flag(server, sender_path,
                                    msg.message_id, request)
            elif op == "get_feature_flag_summary":
                self._get_feature_flag_summary(server, sender_path,
                                          msg.message_id)
            elif op == "create_bluegreen_deployment":
                self._create_bluegreen_deployment(server, sender_path,
                                            msg.message_id, request)
            elif op == "switch_traffic":
                self._switch_traffic(server, sender_path,
                               msg.message_id, request)
            elif op == "get_bluegreen_status":
                self._get_bluegreen_status(server, sender_path,
                                      msg.message_id, request)
            elif op == "rollback_bluegreen":
                self._rollback_bluegreen(server, sender_path,
                                    msg.message_id, request)
            elif op == "get_bluegreen_history":
                self._get_bluegreen_history(server, sender_path,
                                       msg.message_id, request)
            elif op == "list_bluegreen_deployments":
                self._list_bluegreen_deployments(server, sender_path,
                                           msg.message_id)
            elif op == "delete_bluegreen_deployment":
                self._delete_bluegreen_deployment(server, sender_path,
                                            msg.message_id, request)
            elif op == "create_canary_deployment":
                self._create_canary_deployment(server, sender_path,
                                         msg.message_id, request)
            elif op == "record_canary_metric":
                self._record_canary_metric(server, sender_path,
                                     msg.message_id, request)
            elif op == "evaluate_canary_health":
                self._evaluate_canary_health(server, sender_path,
                                       msg.message_id, request)
            elif op == "promote_canary":
                self._promote_canary(server, sender_path,
                               msg.message_id, request)
            elif op == "rollback_canary":
                self._rollback_canary(server, sender_path,
                                msg.message_id, request)
            elif op == "get_canary_status":
                self._get_canary_status(server, sender_path,
                                  msg.message_id, request)
            elif op == "list_canary_deployments":
                self._list_canary_deployments(server, sender_path,
                                         msg.message_id)
            elif op == "delete_canary_deployment":
                self._delete_canary_deployment(server, sender_path,
                                         msg.message_id, request)
            elif op == "register_service":
                self._register_service(server, sender_path,
                                  msg.message_id, request)
            elif op == "deregister_service":
                self._deregister_service(server, sender_path,
                                    msg.message_id, request)
            elif op == "discover_service":
                self._discover_service(server, sender_path,
                                  msg.message_id, request)
            elif op == "service_heartbeat":
                self._service_heartbeat(server, sender_path,
                                  msg.message_id, request)
            elif op == "get_service_instances":
                self._get_service_instances(server, sender_path,
                                       msg.message_id, request)
            elif op == "inject_dependency":
                self._inject_dependency(server, sender_path,
                                  msg.message_id, request)
            elif op == "list_services":
                self._list_services(server, sender_path,
                               msg.message_id)
            elif op == "create_trace":
                self._create_trace(server, sender_path,
                              msg.message_id, request)
            elif op == "start_span":
                self._start_span(server, sender_path,
                           msg.message_id, request)
            elif op == "finish_span":
                self._finish_span(server, sender_path,
                            msg.message_id, request)
            elif op == "add_span_attribute":
                self._add_span_attribute(server, sender_path,
                                    msg.message_id, request)
            elif op == "finish_trace":
                self._finish_trace(server, sender_path,
                             msg.message_id, request)
            elif op == "get_trace":
                self._get_trace(server, sender_path,
                          msg.message_id, request)
            elif op == "get_trace_summary":
                self._get_trace_summary(server, sender_path,
                                  msg.message_id, request)
            elif op == "list_traces":
                self._list_traces(server, sender_path,
                             msg.message_id, request)
            elif op == "create_chaos_experiment":
                self._create_chaos_experiment(server, sender_path,
                                        msg.message_id, request)
            elif op == "start_chaos_experiment":
                self._start_chaos_experiment(server, sender_path,
                                        msg.message_id, request)
            elif op == "record_chaos_observation":
                self._record_chaos_observation(server, sender_path,
                                         msg.message_id, request)
            elif op == "stop_chaos_experiment":
                self._stop_chaos_experiment(server, sender_path,
                                       msg.message_id, request)
            elif op == "get_chaos_results":
                self._get_chaos_results(server, sender_path,
                                  msg.message_id, request)
            elif op == "list_chaos_experiments":
                self._list_chaos_experiments(server, sender_path,
                                        msg.message_id)
            elif op == "create_game_day":
                self._create_game_day(server, sender_path,
                                 msg.message_id, request)
            elif op == "get_game_day":
                self._get_game_day(server, sender_path,
                             msg.message_id, request)
            elif op == "list_game_days":
                self._list_game_days(server, sender_path,
                                msg.message_id)
            elif op == "configure_cost_allocation":
                self._configure_cost_allocation(server, sender_path,
                                         msg.message_id, request)
            elif op == "get_container_cost":
                self._get_container_cost(server, sender_path,
                                   msg.message_id, request)
            elif op == "get_team_cost_summary":
                self._get_team_cost_summary(server, sender_path,
                                       msg.message_id, request)
            elif op == "get_fleet_cost_summary":
                self._get_fleet_cost_summary(server, sender_path,
                                        msg.message_id, request)
            elif op == "get_billing_breakdown":
                self._get_billing_breakdown(server, sender_path,
                                       msg.message_id, request)
            elif op == "create_slo":
                self._create_slo(server, sender_path,
                            msg.message_id, request)
            elif op == "record_sli_event":
                self._record_sli_event(server, sender_path,
                                  msg.message_id, request)
            elif op == "get_slo_status":
                self._get_slo_status(server, sender_path,
                                msg.message_id, request)
            elif op == "list_slos":
                self._list_slos(server, sender_path,
                           msg.message_id)
            elif op == "get_error_budget_report":
                self._get_error_budget_report(server, sender_path,
                                         msg.message_id)
            elif op == "delete_slo":
                self._delete_slo(server, sender_path,
                            msg.message_id, request)
            elif op == "analyze_resource_usage":
                self._analyze_resource_usage(server, sender_path,
                                        msg.message_id, request)
            elif op == "recommend_right_sizing":
                self._recommend_right_sizing(server, sender_path,
                                       msg.message_id, request)
            elif op == "apply_right_sizing":
                self._apply_right_sizing(server, sender_path,
                                    msg.message_id, request)
            elif op == "fleet_right_sizing_report":
                self._fleet_right_sizing_report(server, sender_path,
                                           msg.message_id)
            elif op == "create_mesh_cluster":
                self._create_mesh_cluster(server, sender_path,
                                    msg.message_id, request)
            elif op == "add_mesh_service":
                self._add_mesh_service(server, sender_path,
                                  msg.message_id, request)
            elif op == "create_traffic_policy":
                self._create_traffic_policy(server, sender_path,
                                       msg.message_id, request)
            elif op == "evaluate_traffic_policy":
                self._evaluate_traffic_policy(server, sender_path,
                                         msg.message_id, request)
            elif op == "get_mesh_topology":
                self._get_mesh_topology(server, sender_path,
                                   msg.message_id)
            elif op == "list_mesh_policies":
                self._list_mesh_policies(server, sender_path,
                                    msg.message_id)
            elif op == "delete_mesh_policy":
                self._delete_mesh_policy(server, sender_path,
                                    msg.message_id, request)
            elif op == "configure_auto_rollback":
                self._configure_auto_rollback(server, sender_path,
                                        msg.message_id, request)
            elif op == "check_and_auto_rollback":
                self._check_and_auto_rollback(server, sender_path,
                                         msg.message_id)
            elif op == "get_auto_rollback_status":
                self._get_auto_rollback_status(server, sender_path,
                                          msg.message_id)
            elif op == "get_auto_rollback_history":
                self._get_auto_rollback_history(server, sender_path,
                                          msg.message_id, request)
            elif op == "disable_auto_rollback":
                self._disable_auto_rollback(server, sender_path,
                                       msg.message_id, request)
            elif op == "delete_auto_rollback":
                self._delete_auto_rollback(server, sender_path,
                                      msg.message_id, request)
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

    def _image_create_layer(self, server, sender_path: str, call_id: str,
                            request: Dict[str, Any]) -> None:
        try:
            result = self.container_manager.create_image_layer(
                base_path=request["base_path"],
                layer_name=request["layer_name"],
                changes=request.get("changes"),
            )
        except (ValueError, KeyError) as e:
            self._reply(server, sender_path, call_id,
                        {"ok": False, "error": str(e)})
            return
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _image_list_layers(self, server, sender_path: str, call_id: str,
                           request: Dict[str, Any]) -> None:
        layers = self.container_manager.list_image_layers(
            request["image_path"])
        self._reply(server, sender_path, call_id,
                    {"ok": True, "layers": layers})

    def _image_remove_layer(self, server, sender_path: str, call_id: str,
                            request: Dict[str, Any]) -> None:
        try:
            self.container_manager.remove_image_layer(
                request["image_path"], request["layer_name"])
        except (ValueError, KeyError) as e:
            self._reply(server, sender_path, call_id,
                        {"ok": False, "error": str(e)})
            return
        self._reply(server, sender_path, call_id, {"ok": True})

    def _image_diff(self, server, sender_path: str, call_id: str,
                    request: Dict[str, Any]) -> None:
        try:
            result = self.container_manager.diff_images(
                request["image_a_path"], request["image_b_path"])
        except (ValueError, KeyError) as e:
            self._reply(server, sender_path, call_id,
                        {"ok": False, "error": str(e)})
            return
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _registry_pull(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        try:
            result = self.container_manager.registry_pull(
                registry_url=request["registry_url"],
                image_name=request["image_name"],
                tag=request.get("tag", "latest"),
                dest_dir=request.get("dest_dir"),
            )
        except (ValueError, KeyError) as e:
            self._reply(server, sender_path, call_id,
                        {"ok": False, "error": str(e)})
            return
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _registry_push(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        try:
            result = self.container_manager.registry_push(
                image_path=request["image_path"],
                registry_url=request["registry_url"],
                image_name=request["image_name"],
                tag=request.get("tag", "latest"),
            )
        except (ValueError, KeyError) as e:
            self._reply(server, sender_path, call_id,
                        {"ok": False, "error": str(e)})
            return
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _registry_catalog(self, server, sender_path: str, call_id: str,
                          request: Dict[str, Any]) -> None:
        result = self.container_manager.registry_catalog(
            registry_url=request["registry_url"])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _cluster_register_node(self, server, sender_path: str, call_id: str,
                               request: Dict[str, Any]) -> None:
        try:
            result = self.container_manager.register_node(
                node_id=request["node_id"],
                node_url=request["node_url"],
                labels=request.get("labels"),
                capacity=request.get("capacity"),
            )
        except (ValueError, KeyError) as e:
            self._reply(server, sender_path, call_id,
                        {"ok": False, "error": str(e)})
            return
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _cluster_unregister_node(self, server, sender_path: str,
                                 call_id: str,
                                 request: Dict[str, Any]) -> None:
        ok = self.container_manager.unregister_node(request["node_id"])
        self._reply(server, sender_path, call_id, {"ok": ok})

    def _cluster_heartbeat(self, server, sender_path: str, call_id: str,
                           request: Dict[str, Any]) -> None:
        try:
            result = self.container_manager.node_heartbeat(
                node_id=request["node_id"],
                status=request.get("status", "active"),
                resource_usage=request.get("resource_usage"),
            )
        except ValueError as e:
            self._reply(server, sender_path, call_id,
                        {"ok": False, "error": str(e)})
            return
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _cluster_nodes(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        nodes = self.container_manager.get_cluster_nodes()
        self._reply(server, sender_path, call_id,
                    {"ok": True, "nodes": nodes})

    def _cluster_status(self, server, sender_path: str, call_id: str,
                        request: Dict[str, Any]) -> None:
        result = self.container_manager.get_cluster_status()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _cluster_schedule(self, server, sender_path: str, call_id: str,
                          request: Dict[str, Any]) -> None:
        result = self.container_manager.schedule_container(
            container_config=request.get("container_config", {}),
            strategy=request.get("strategy", "least_loaded"),
            label_selector=request.get("label_selector"),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _cluster_containers(self, server, sender_path: str, call_id: str,
                            request: Dict[str, Any]) -> None:
        containers = self.container_manager.get_cluster_containers()
        self._reply(server, sender_path, call_id,
                    {"ok": True, "containers": containers})

    def _cluster_drain_node(self, server, sender_path: str, call_id: str,
                            request: Dict[str, Any]) -> None:
        try:
            result = self.container_manager.drain_node(
                node_id=request["node_id"],
                timeout_s=float(request.get("timeout_s", 30.0)),
            )
        except ValueError as e:
            self._reply(server, sender_path, call_id,
                        {"ok": False, "error": str(e)})
            return
        self._reply(server, sender_path, call_id, {"ok": True, **result})

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

    # Event correlation

    def _event_correlate(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        result = self.container_manager.correlate_events(
            time_window_s=request.get("time_window_s", 60.0),
            kinds=request.get("kinds"),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _event_timeline(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        result = self.container_manager.get_event_timeline(
            container_ids=request.get("container_ids"),
            time_window_s=request.get("time_window_s", 300.0),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Network policy rules

    def _network_rule_add(self, server, sender_path: str,
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
        result = self.container_manager.add_network_rule(
            c,
            direction=request.get("direction", "ingress"),
            protocol=request.get("protocol", "tcp"),
            port=request.get("port"),
            source=request.get("source"),
            action=request.get("action", "allow"),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _network_rule_remove(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        rule_index = request.get("rule_index", 0)
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
        result = self.container_manager.remove_network_rule(
            c, rule_index)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _network_rules_list(self, server, sender_path: str,
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
        result = self.container_manager.list_network_rules(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _network_rules_clear(self, server, sender_path: str,
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
        result = self.container_manager.clear_network_rules(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Container comparison

    def _compare_containers(self, server, sender_path: str,
                            call_id: str,
                            request: Dict[str, Any]) -> None:
        container_ids = request.get("container_ids", [])
        if len(container_ids) < 2:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "need at least 2 container_ids to compare",
            })
            return
        result = self.container_manager.compare_containers_detailed(
            container_ids, metrics=request.get("metrics"))
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # Threshold monitoring

    def _check_thresholds(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        result = self.container_manager.check_all_thresholds()
        self._reply(server, sender_path, call_id, {
            "ok": True, "fired": result, "count": len(result),
        })

    def _threshold_status(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        result = self.container_manager.get_threshold_status()
        self._reply(server, sender_path, call_id, {
            "ok": True, "containers": result,
        })

    # Workload scheduling

    def _set_scheduling_priority(self, server, sender_path: str,
                                 call_id: str,
                                 request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        priority = request.get("priority", 50)
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
        result = self.container_manager.set_scheduling_priority(
            c, priority)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _scheduling_queue(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        result = self.container_manager.get_scheduling_queue()
        self._reply(server, sender_path, call_id, {
            "ok": True, "queue": result,
        })

    def _ready_containers(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        result = self.container_manager.get_ready_containers()
        self._reply(server, sender_path, call_id, {
            "ok": True, "ready": result,
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

    def _audit_record(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        action = request.get("action")
        if not container_id or not action:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and action are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        entry = self.container_manager.record_audit_entry(
            c, action=action,
            actor=request.get("actor", "operator"),
            resource=request.get("resource"),
            old_value=request.get("old_value"),
            new_value=request.get("new_value"),
            detail=request.get("detail", ""),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **entry,
        })

    def _audit_log(self, server, sender_path: str, call_id: str,
                    request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        log = self.container_manager.get_audit_log(
            c, tail=request.get("tail"),
            action=request.get("action"),
            actor=request.get("actor"),
            resource=request.get("resource"),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "container_id": container_id,
            "entries": log,
            "count": len(log),
        })

    def _audit_summary(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        summary = self.container_manager.get_audit_summary(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **summary,
        })

    def _cost_allocate(self, server, sender_path: str, call_id: str,
                       request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        rates = request.get("rates")
        allocation = self.container_manager.calculate_cost_allocation(
            c, rates=rates,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **allocation,
        })

    def _cost_allocate_all(self, server, sender_path: str, call_id: str,
                           request: Dict[str, Any]) -> None:
        rates = request.get("rates")
        result = self.container_manager.calculate_cost_allocation_all(
            rates=rates,
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _budget_set(self, server, sender_path: str, call_id: str,
                     request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.set_resource_budget(
            c,
            memory_mb=request.get("memory_mb"),
            cpu_pct=request.get("cpu_pct"),
            pids=request.get("pids"),
            daily_cost_limit=request.get("daily_cost_limit"),
            monthly_cost_limit=request.get("monthly_cost_limit"),
            alert_at_pct=request.get("alert_at_pct", 80.0),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _budget_get(self, server, sender_path: str, call_id: str,
                    request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_resource_budget(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _budget_check(self, server, sender_path: str, call_id: str,
                      request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        budget = getattr(c, '_resource_budget', None)
        if not budget:
            self._reply(server, sender_path, call_id, {
                "ok": True,
                "container_id": container_id,
                "status": "no_budget",
            })
            return
        status = self.container_manager._check_single_budget(c, budget)
        self._reply(server, sender_path, call_id, {
            "ok": True, **status,
        })

    def _budget_check_all(self, server, sender_path: str, call_id: str,
                          request: Dict[str, Any]) -> None:
        results = self.container_manager.check_resource_budgets()
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "results": results,
            "count": len(results),
        })

    def _budget_clear(self, server, sender_path: str, call_id: str,
                      request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.clear_resource_budget(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _remediation_configure(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        try:
            result = self.container_manager.configure_remediation(
                c,
                on_budget_exceeded=request.get(
                    'on_budget_exceeded', 'alert'),
                on_threshold_exceeded=request.get(
                    'on_threshold_exceeded', 'alert'),
                on_oom_risk=request.get(
                    'on_oom_risk', 'alert'),
                max_restarts=request.get('max_restarts', 3),
                cooldown_seconds=request.get('cooldown_seconds', 300.0),
                enabled=request.get('enabled', True),
            )
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _remediation_execute(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        trigger = request.get("trigger")
        if not container_id or not trigger:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_id and trigger are required",
            })
            return
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.execute_remediation(
            c, trigger=trigger,
            reason=request.get('reason', ''),
        )
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _remediation_status(self, server, sender_path: str,
                            call_id: str,
                            request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_remediation_status(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _remediation_history(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        history = self.container_manager.get_remediation_history(
            c,
            tail=request.get('tail'),
            trigger=request.get('trigger'),
            action=request.get('action'),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "container_id": container_id,
            "entries": history,
            "count": len(history),
        })

    def _tenant_config_set(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        owner = request.get("owner")
        if not owner:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "owner is required",
            })
            return
        try:
            result = self.container_manager.set_tenant_config(
                owner,
                priority=request.get('priority', 0),
                weight=request.get('weight', 1.0),
                burstable_pct=request.get('burstable_pct', 20.0),
                enforce=request.get('enforce', True),
                eviction_policy=request.get('eviction_policy', 'alert'),
            )
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _tenant_config_get(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        owner = request.get("owner")
        if not owner:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "owner is required",
            })
            return
        result = self.container_manager.get_tenant_config(owner)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _tenant_config_list(self, server, sender_path: str,
                            call_id: str,
                            request: Dict[str, Any]) -> None:
        result = self.container_manager.list_tenant_configs()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _fair_share(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        resource = request.get('resource', 'memory_mb')
        result = self.container_manager.calculate_fair_share(
            resource=resource)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _tenant_enforce(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        actions = self.container_manager.enforce_tenant_quotas()
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "actions": actions,
            "count": len(actions),
        })

    def _tenant_usage_summary(self, server, sender_path: str,
                              call_id: str,
                              request: Dict[str, Any]) -> None:
        result = self.container_manager.get_tenant_usage_summary()
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "tenants": result,
            "count": len(result),
        })

    def _event_log_export(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        container = None
        if container_id:
            container = self.container_manager.containers.get(container_id)
            if container is None:
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": f"container {container_id!r} not found",
                })
                return
        result = self.container_manager.export_event_log(
            container=container,
            include_audit=request.get('include_audit', True),
            include_oom=request.get('include_oom', True),
            include_sla=request.get('include_sla', True),
            since=request.get('since'),
            until=request.get('until'),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _event_log_import(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        data = request.get('data')
        if not data or not isinstance(data, dict):
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "data dict is required",
            })
            return
        result = self.container_manager.import_event_log(
            data=data,
            container_id=request.get('container_id'),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _health_score(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        container_id = request.get("container_id")
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.calculate_health_score(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _health_score_all(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        result = self.container_manager.calculate_health_scores_all()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _event_log_compress(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        data = request.get('data')
        if not data or not isinstance(data, dict):
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "data dict is required",
            })
            return
        result = self.container_manager.compress_event_log(
            data=data,
            keep_recent=request.get('keep_recent', 100),
            summarize_older=request.get('summarize_older', True),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _archive_schedule_set(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        result = self.container_manager.configure_archive_schedule(
            enabled=request.get('enabled', True),
            interval_s=request.get('interval_s', 86400.0),
            keep_recent=request.get('keep_recent', 500),
            auto_compress=request.get('auto_compress', True),
            max_archives=request.get('max_archives', 30),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _archive_schedule_get(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        result = self.container_manager.get_archive_schedule()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _archive_schedule_disable(self, server, sender_path: str,
                                   call_id: str,
                                   request: Dict[str, Any]) -> None:
        result = self.container_manager.disable_archive_schedule()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _archive_run_now(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        result = self.container_manager.run_archive_now()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _archive_list(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        archives = self.container_manager.list_archives(
            tail=request.get('tail'))
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "archives": archives,
            "count": len(archives),
        })

    def _archive_get(self, server, sender_path: str,
                     call_id: str,
                     request: Dict[str, Any]) -> None:
        index = request.get('index', 0)
        archive = self.container_manager.get_archive(index)
        if archive is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"archive index {index} not found",
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True, **archive,
        })

    def _sla_breach_process(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.process_sla_breach(
            c,
            breach_type=request.get('breach_type', 'downtime'),
            detail=request.get('detail', ''),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_breach_process_all(self, server, sender_path: str,
                                 call_id: str,
                                 request: Dict[str, Any]) -> None:
        result = self.container_manager.process_sla_breach_all(
            breach_type=request.get('breach_type', 'downtime'),
            detail=request.get('detail', ''),
            container_ids=request.get('container_ids'),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _smart_remediate(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.evaluate_and_remediate(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _smart_remediate_all(self, server, sender_path: str,
                              call_id: str,
                              request: Dict[str, Any]) -> None:
        result = self.container_manager.evaluate_and_remediate_all()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _usage_patterns(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.detect_usage_patterns(
            c, window_size=request.get('window_size', 30))
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _optimization_actions(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_usage_optimization_actions(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _rightsize(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.rightsize_container(
            c,
            safety_margin_pct=request.get('safety_margin_pct', 20.0),
            dry_run=request.get('dry_run', False),
        )
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _rightsize_all(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        result = self.container_manager.rightsize_all_containers(
            safety_margin_pct=request.get('safety_margin_pct', 20.0),
            dry_run=request.get('dry_run', False),
        )
        self._save_state()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_compliance_set(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.set_sla_compliance_rules(
            c,
            max_memory_pct=request.get('max_memory_pct', 90.0),
            max_pid_pct=request.get('max_pid_pct', 80.0),
            max_daily_cost=request.get('max_daily_cost'),
            max_consecutive_anomalies=request.get('max_consecutive_anomalies', 5),
            auto_action=request.get('auto_action', 'alert'),
            enabled=request.get('enabled', True),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_compliance_get(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_sla_compliance_rules(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_compliance_check(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.check_sla_compliance(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_compliance_check_all(self, server, sender_path: str,
                                   call_id: str,
                                   request: Dict[str, Any]) -> None:
        result = self.container_manager.check_sla_compliance_all()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _visualization_data(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_visualization_data(
            c,
            time_range_s=request.get('time_range_s', 3600.0),
            resolution=request.get('resolution', 60),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _fleet_visualization(self, server, sender_path: str,
                              call_id: str,
                              request: Dict[str, Any]) -> None:
        result = self.container_manager.get_fleet_visualization(
            time_range_s=request.get('time_range_s', 3600.0))
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _anomaly_remediate(self, server, sender_path: str,
                            call_id: str,
                            request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.remediate_anomaly(
            c,
            resource=request.get('resource', 'memory'),
            sensitivity=request.get('sensitivity', 2.0),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _anomaly_remediate_all(self, server, sender_path: str,
                                call_id: str,
                                request: Dict[str, Any]) -> None:
        result = self.container_manager.remediate_anomaly_all(
            resource=request.get('resource', 'memory'),
            sensitivity=request.get('sensitivity', 2.0),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _monitoring_configure(self, server, sender_path: str,
                               call_id: str,
                               request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.configure_monitoring(
            c,
            memory_high_pct=request.get('memory_high_pct', 90.0),
            memory_low_pct=request.get('memory_low_pct', 10.0),
            cpu_high_pct=request.get('cpu_high_pct', 90.0),
            pid_high_pct=request.get('pid_high_pct', 80.0),
            cost_high_daily=request.get('cost_high_daily'),
            trend_window=request.get('trend_window', 10),
            trend_threshold=request.get('trend_threshold', 0.1),
            enabled=request.get('enabled', True),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _monitoring_get(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_monitoring_config(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _monitoring_check(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.check_monitoring(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _monitoring_check_all(self, server, sender_path: str,
                              call_id: str,
                              request: Dict[str, Any]) -> None:
        result = self.container_manager.check_monitoring_all()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_auto_escalation_configure(self, server, sender_path: str,
                                        call_id: str,
                                        request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.configure_sla_auto_escalation(
            c,
            enabled=request.get('enabled', True),
            breach_threshold=request.get('breach_threshold', 3),
            escalation_window_s=request.get('escalation_window_s', 3600.0),
            max_level=request.get('max_level', 3),
            actions_per_level=request.get('actions_per_level'),
            cooldown_s=request.get('cooldown_s', 300.0),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_breach_record(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.record_sla_breach(
            c,
            breach_type=request.get('breach_type', 'downtime'),
            detail=request.get('detail', ''),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_auto_escalation_status(self, server, sender_path: str,
                                     call_id: str,
                                     request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_sla_auto_escalation_status(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _sla_auto_escalation_reset(self, server, sender_path: str,
                                    call_id: str,
                                    request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.reset_sla_auto_escalation(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _cost_optimize(self, server, sender_path: str,
                       call_id: str,
                       request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_cost_optimization_report(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _cost_optimize_all(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        result = self.container_manager.get_fleet_cost_optimization()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _anomaly_predict(self, server, sender_path: str,
                         call_id: str,
                         request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.predict_anomalies(
            c,
            horizon_s=float(request.get('horizon_s', 3600.0)),
            confidence_threshold=float(
                request.get('confidence_threshold', 0.5)),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _anomaly_predict_all(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        result = self.container_manager.predict_fleet_anomalies(
            horizon_s=float(request.get('horizon_s', 3600.0)),
            confidence_threshold=float(
                request.get('confidence_threshold', 0.5)),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _predictive_scaling_configure(self, server, sender_path: str,
                                     call_id: str,
                                     request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.configure_predictive_scaling(
            c,
            enabled=bool(request.get('enabled', True)),
            lead_time_s=float(request.get('lead_time_s', 300.0)),
            memory_buffer_pct=float(
                request.get('memory_buffer_pct', 20.0)),
            cpu_buffer_pct=float(
                request.get('cpu_buffer_pct', 15.0)),
            scale_up_threshold=float(
                request.get('scale_up_threshold', 0.75)),
            scale_down_threshold=float(
                request.get('scale_down_threshold', 0.30)),
            min_memory_mb=request.get('min_memory_mb'),
            max_memory_mb=request.get('max_memory_mb'),
            dry_run=bool(request.get('dry_run', False)),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _predictive_scaling_evaluate(self, server, sender_path: str,
                                    call_id: str,
                                    request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.evaluate_predictive_scaling(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _predictive_scaling_evaluate_all(self, server, sender_path: str,
                                        call_id: str,
                                        request: Dict[str, Any]) -> None:
        result = (
            self.container_manager.evaluate_predictive_scaling_all())
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _predictive_scaling_status(self, server, sender_path: str,
                                  call_id: str,
                                  request: Dict[str, Any]) -> None:
        container_id = request.get('container_id')
        c = self.container_manager.containers.get(container_id)
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"container {container_id!r} not found",
            })
            return
        result = self.container_manager.get_predictive_scaling_status(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _anomaly_correlate(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        result = self.container_manager.correlate_anomalies(
            time_window_s=float(
                request.get('time_window_s', 300.0)),
            min_containers=int(
                request.get('min_containers', 2)),
            resource_filter=request.get('resource_filter'),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _anomaly_correlation_report(self, server, sender_path: str,
                                   call_id: str,
                                   request: Dict[str, Any]) -> None:
        result = self.container_manager.get_correlation_report(
            time_window_s=float(
                request.get('time_window_s', 300.0)),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _resource_heatmap(self, server, sender_path: str,
                          call_id: str,
                          request: Dict[str, Any]) -> None:
        result = self.container_manager.generate_resource_heatmap(
            window_s=float(request.get('window_s', 300.0)),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _container_pressure_detail(self, server, sender_path: str,
                                  call_id: str,
                                  request: Dict[str, Any]) -> None:
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container not found",
            })
            return
        result = self.container_manager.get_container_pressure_detail(
            c, window_s=float(request.get('window_s', 300.0)),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _record_pressure_snapshot(self, server, sender_path: str,
                                 call_id: str,
                                 request: Dict[str, Any]) -> None:
        result = self.container_manager.record_pressure_snapshot()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _classify_tier(self, server, sender_path: str,
                       call_id: str,
                       request: Dict[str, Any]) -> None:
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container not found",
            })
            return
        result = self.container_manager.classify_container_tier(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _fleet_tier_summary(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        result = self.container_manager.get_fleet_tier_summary()
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _suggest_tier_upgrade(self, server, sender_path: str,
                             call_id: str,
                             request: Dict[str, Any]) -> None:
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container not found",
            })
            return
        result = self.container_manager.suggest_tier_upgrade(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # -- log streaming handlers --

    def _log_stream(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container not found",
            })
            return
        result = self.container_manager.stream_container_logs(
            c,
            follow=request.get('follow', False),
            interval_s=request.get('interval_s', 0.5),
            max_lines=request.get('max_lines', 1000),
            timeout_s=request.get('timeout_s', 5.0),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _log_filter(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container not found",
            })
            return
        result = self.container_manager.filter_container_logs(
            c,
            pattern=request.get('pattern', ''),
            stream=request.get('stream', 'both'),
            tail=request.get('tail'),
            case_insensitive=request.get('case_insensitive', False),
            max_matches=request.get('max_matches', 500),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _log_export(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container not found",
            })
            return
        result = self.container_manager.export_container_logs(
            c,
            dest_path=request['dest_path'],
            format=request.get('format', 'text'),
            stream=request.get('stream', 'both'),
            tail=request.get('tail'),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # -- image dedup/GC handlers --

    def _image_dedup(self, server, sender_path: str,
                     call_id: str,
                     request: Dict[str, Any]) -> None:
        result = self.container_manager.deduplicate_images(
            request.get('images_dir', self.container_manager.base_dir),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _image_gc(self, server, sender_path: str,
                  call_id: str,
                  request: Dict[str, Any]) -> None:
        result = self.container_manager.garbage_collect_images(
            request.get('images_dir', self.container_manager.base_dir),
            dry_run=request.get('dry_run', True),
            max_age_days=request.get('max_age_days'),
            unused_only=request.get('unused_only', False),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _image_layer_stats(self, server, sender_path: str,
                           call_id: str,
                           request: Dict[str, Any]) -> None:
        result = self.container_manager.image_layer_stats(
            request.get('images_dir', self.container_manager.base_dir),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # -- DNS handlers --

    def _dns_generate(self, server, sender_path: str,
                      call_id: str,
                      request: Dict[str, Any]) -> None:
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container not found",
            })
            return
        result = self.container_manager.generate_resolv_conf(
            c,
            nameservers=request.get('nameservers'),
            search_domains=request.get('search_domains'),
            options=request.get('options'),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _dns_resolve(self, server, sender_path: str,
                     call_id: str,
                     request: Dict[str, Any]) -> None:
        result = self.container_manager.resolve_dns(
            request['hostname'],
            nameservers=request.get('nameservers'),
            timeout_s=request.get('timeout_s', 5.0),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _dns_get_config(self, server, sender_path: str,
                        call_id: str,
                        request: Dict[str, Any]) -> None:
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container not found",
            })
            return
        result = self.container_manager.get_dns_config(c)
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    def _dns_update(self, server, sender_path: str,
                    call_id: str,
                    request: Dict[str, Any]) -> None:
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": "container not found",
            })
            return
        result = self.container_manager.update_dns(
            c,
            add_nameservers=request.get('add_nameservers'),
            remove_nameservers=request.get('remove_nameservers'),
            add_search_domains=request.get('add_search_domains'),
            remove_search_domains=request.get('remove_search_domains'),
        )
        self._reply(server, sender_path, call_id, {
            "ok": True, **result,
        })

    # -- container-to-container networking handlers --

    def _create_network(self, server, sender_path, call_id, request):
        result = self.container_manager.create_container_network(
            request['name'],
            subnet=request.get('subnet', '172.18.0.0/16'),
            gateway=request.get('gateway', '172.18.0.1'),
            enable_dns=request.get('enable_dns', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _remove_network(self, server, sender_path, call_id, request):
        result = self.container_manager.remove_container_network(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_networks(self, server, sender_path, call_id, request):
        result = self.container_manager.list_container_networks()
        self._reply(server, sender_path, call_id, {"ok": True, "networks": result})

    def _connect_network(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.connect_to_network(
            request['network_name'], c,
            aliases=request.get('aliases'),
            ip_address=request.get('ip_address'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _disconnect_network(self, server, sender_path, call_id, request):
        result = self.container_manager.disconnect_from_network(
            request['network_name'], request['container_id'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _network_topology(self, server, sender_path, call_id, request):
        result = self.container_manager.get_network_topology(request['network_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _network_dns_resolve(self, server, sender_path, call_id, request):
        result = self.container_manager.resolve_network_dns(
            request['network_name'], request['name'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _test_connectivity(self, server, sender_path, call_id, request):
        result = self.container_manager.test_network_connectivity(
            request['network_name'], request['src_container_id'], request['dst_ip'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- migration handlers --

    def _plan_migration(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.plan_migration(
            c, request['target_node'],
            strategy=request.get('strategy', 'live'),
            max_downtime_ms=request.get('max_downtime_ms', 1000),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _execute_migration(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.execute_migration(
            c, request['target_node'],
            strategy=request.get('strategy', 'live'),
            dry_run=request.get('dry_run', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _migration_history(self, server, sender_path, call_id, request):
        result = self.container_manager.get_migration_history(
            container_id=request.get('container_id'),
            tail=request.get('tail', 20),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _migration_cost(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.estimate_migration_cost(
            c, request['target_node'],
            strategy=request.get('strategy', 'live'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- alerting handlers --

    def _configure_alert_channel(self, server, sender_path, call_id, request):
        result = self.container_manager.configure_alert_channel(
            request['channel_id'], request['channel_type'],
            config=request.get('config'),
            enabled=request.get('enabled', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _remove_alert_channel(self, server, sender_path, call_id, request):
        result = self.container_manager.remove_alert_channel(request['channel_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_alert_channels(self, server, sender_path, call_id, request):
        result = self.container_manager.list_alert_channels()
        self._reply(server, sender_path, call_id, {"ok": True, "channels": result})

    def _enable_alert_channel(self, server, sender_path, call_id, request):
        result = self.container_manager.enable_alert_channel(request['channel_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _disable_alert_channel(self, server, sender_path, call_id, request):
        result = self.container_manager.disable_alert_channel(request['channel_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _configure_alert_rules(self, server, sender_path, call_id, request):
        c = None
        if request.get('container_id'):
            c = self.container_manager.get_container(request['container_id'])
            if c is None:
                self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
                return
        result = self.container_manager.configure_alert_rules(
            container=c,
            rules=request.get('rules'),
            fleet_wide=request.get('fleet_wide', False),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_alert_rules(self, server, sender_path, call_id, request):
        result = self.container_manager.get_alert_rules(
            container_id=request.get('container_id'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _evaluate_alerts(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.evaluate_alerts(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _alert_history(self, server, sender_path, call_id, request):
        result = self.container_manager.get_alert_history(
            container_id=request.get('container_id'),
            alert_type=request.get('alert_type'),
            tail=request.get('tail', 50),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- anomaly detection handlers --

    def _detect_anomalies(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.detect_anomalies(
            c,
            window_size=request.get('window_size', 30),
            z_threshold=request.get('z_threshold', 2.5),
            iqr_multiplier=request.get('iqr_multiplier', 1.5),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _detect_fleet_anomalies(self, server, sender_path, call_id, request):
        result = self.container_manager.detect_fleet_anomalies(
            window_size=request.get('window_size', 30),
            z_threshold=request.get('z_threshold', 2.5),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- snapshot diff/rollback handlers --

    def _diff_snapshots(self, server, sender_path, call_id, request):
        result = self.container_manager.diff_snapshots(
            request.get('snapshot_a', {}),
            request.get('snapshot_b', {}),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _rollback_snapshot(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.rollback_to_snapshot(
            c,
            request.get('snapshot', {}),
            dry_run=request.get('dry_run', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- placement optimization handlers --

    def _optimize_placement(self, server, sender_path, call_id, request):
        result = self.container_manager.optimize_placement(
            containers=request.get('containers'),
            strategy=request.get('strategy', 'balanced'),
            respect_affinity=request.get('respect_affinity', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _placement_score(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.placement_score(
            request['node_id'], c,
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- dynamic resource limit handlers --

    def _configure_auto_scaling(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.configure_auto_scaling(
            c,
            enabled=request.get('enabled', True),
            min_memory_mb=request.get('min_memory_mb'),
            max_memory_mb=request.get('max_memory_mb'),
            target_memory_pct=request.get('target_memory_pct', 70.0),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _evaluate_and_adjust(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.evaluate_auto_scaling(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _auto_scaling_status(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_auto_scaling_status(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _batch_evaluate_scaling(self, server, sender_path, call_id, request):
        # Evaluate all containers with auto-scaling enabled
        results = []
        for cid, c in self.container_manager.containers.items():
            if c.state.value == 'running' and hasattr(c, '_autoscale') and c._autoscale.get('enabled'):
                result = self.container_manager.evaluate_auto_scaling(c)
                results.append(result)
        self._reply(server, sender_path, call_id, {
            "ok": True, "containers_evaluated": len(results), "results": results})

    # -- dependency graph handlers --

    def _generate_dependency_graph(self, server, sender_path, call_id, request):
        result = self.container_manager.generate_dependency_graph(
            container_ids=request.get('container_ids'),
            format=request.get('format', 'ascii'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_critical_path(self, server, sender_path, call_id, request):
        result = self.container_manager.get_critical_path(
            container_ids=request.get('container_ids'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- federation handlers --

    def _register_federation_peer(self, server, sender_path, call_id, request):
        result = self.container_manager.register_federation_peer(
            request['peer_id'], request['peer_url'],
            request['cluster_name'],
            capabilities=request.get('capabilities'),
            trust_level=request.get('trust_level', 'full'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _unregister_federation_peer(self, server, sender_path, call_id, request):
        result = self.container_manager.unregister_federation_peer(request['peer_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_federation_peers(self, server, sender_path, call_id, request):
        result = self.container_manager.list_federation_peers()
        self._reply(server, sender_path, call_id, {"ok": True, "peers": result})

    def _share_container_with_peer(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.share_container_with_peer(
            c, request['peer_id'],
            permissions=request.get('permissions'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _unshare_container_from_peer(self, server, sender_path, call_id, request):
        result = self.container_manager.unshare_container_from_peer(
            request['container_id'], request['peer_id'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _share_resources_with_peer(self, server, sender_path, call_id, request):
        result = self.container_manager.share_resources_with_peer(
            request['peer_id'], request['resource_type'], request['amount'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_federation_status(self, server, sender_path, call_id, request):
        result = self.container_manager.get_federation_status()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _plan_cross_cluster_migration(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.plan_cross_cluster_migration(
            c, request['target_peer_id'],
            strategy=request.get('strategy', 'snapshot'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- event-driven scaling handlers --

    def _configure_event_trigger(self, server, sender_path, call_id, request):
        result = self.container_manager.configure_event_trigger(
            request['trigger_id'], request['event_type'], request['action'],
            conditions=request.get('conditions'),
            enabled=request.get('enabled', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _remove_event_trigger(self, server, sender_path, call_id, request):
        result = self.container_manager.remove_event_trigger(request['trigger_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_event_triggers(self, server, sender_path, call_id, request):
        result = self.container_manager.list_event_triggers()
        self._reply(server, sender_path, call_id, {"ok": True, "triggers": result})

    def _enable_event_trigger(self, server, sender_path, call_id, request):
        result = self.container_manager.enable_event_trigger(request['trigger_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _disable_event_trigger(self, server, sender_path, call_id, request):
        result = self.container_manager.disable_event_trigger(request['trigger_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _fire_event(self, server, sender_path, call_id, request):
        result = self.container_manager.fire_event(
            request['event_type'],
            container_id=request.get('container_id'),
            data=request.get('data'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_event_log(self, server, sender_path, call_id, request):
        result = self.container_manager.get_event_log(
            event_type=request.get('event_type'),
            container_id=request.get('container_id'),
            tail=request.get('tail', 50),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_trigger_stats(self, server, sender_path, call_id, request):
        result = self.container_manager.get_trigger_stats()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- cluster dashboard handler --

    def _generate_cluster_dashboard(self, server, sender_path, call_id, request):
        result = self.container_manager.generate_cluster_dashboard()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- network rule handlers --

    def _configure_network_rule(self, server, sender_path, call_id, request):
        result = self.container_manager.configure_network_rule(
            request['rule_id'], request['direction'], request['action'],
            protocol=request.get('protocol', 'tcp'),
            port=request.get('port'),
            source=request.get('source'),
            destination=request.get('destination'),
            container_filter=request.get('container_filter'),
            priority=request.get('priority', 100),
            enabled=request.get('enabled', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _remove_network_rule(self, server, sender_path, call_id, request):
        result = self.container_manager.remove_network_rule(request['rule_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_network_rules(self, server, sender_path, call_id, request):
        result = self.container_manager.list_network_rules(
            direction=request.get('direction'),
            container_id=request.get('container_id'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, "rules": result})

    def _enable_network_rule(self, server, sender_path, call_id, request):
        result = self.container_manager.enable_network_rule(request['rule_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _disable_network_rule(self, server, sender_path, call_id, request):
        result = self.container_manager.disable_network_rule(request['rule_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _evaluate_network_access(self, server, sender_path, call_id, request):
        result = self.container_manager.evaluate_network_access(
            request['container_id'], request['direction'],
            protocol=request.get('protocol', 'tcp'),
            port=request.get('port'),
            remote_ip=request.get('remote_ip'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_network_rule_stats(self, server, sender_path, call_id, request):
        result = self.container_manager.get_network_rule_stats()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- backup/DR handlers --

    def _create_backup(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.create_backup(
            c,
            backup_id=request.get('backup_id'),
            backup_type=request.get('backup_type', 'full'),
            destination=request.get('destination', '/tmp/nyrqis-backups'),
            include_logs=request.get('include_logs', True),
            include_state=request.get('include_state', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_backups(self, server, sender_path, call_id, request):
        result = self.container_manager.list_backups(
            container_id=request.get('container_id'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, "backups": result})

    def _get_backup(self, server, sender_path, call_id, request):
        result = self.container_manager.get_backup(request['backup_id'])
        if result is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "backup not found"})
        else:
            self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_backup(self, server, sender_path, call_id, request):
        result = self.container_manager.delete_backup(request['backup_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _restore_from_backup(self, server, sender_path, call_id, request):
        result = self.container_manager.restore_from_backup(
            request['backup_id'],
            container_id=request.get('container_id'),
            dry_run=request.get('dry_run', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _configure_backup_policy(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.configure_backup_policy(
            c,
            enabled=request.get('enabled', True),
            interval_hours=request.get('interval_hours', 24),
            retention_count=request.get('retention_count', 7),
            backup_type=request.get('backup_type', 'full'),
            include_logs=request.get('include_logs', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_backup_policy(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_backup_policy(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_dr_status(self, server, sender_path, call_id, request):
        result = self.container_manager.get_disaster_recovery_status()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- log aggregation handlers --

    def _aggregate_cluster_logs(self, server, sender_path, call_id, request):
        result = self.container_manager.aggregate_cluster_logs(
            pattern=request.get('pattern', ''),
            stream=request.get('stream', 'both'),
            tail=request.get('tail', 100),
            container_ids=request.get('container_ids'),
            sort_by=request.get('sort_by', 'timestamp'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _search_cluster_logs(self, server, sender_path, call_id, request):
        result = self.container_manager.search_cluster_logs(
            request['pattern'],
            stream=request.get('stream', 'both'),
            max_matches=request.get('max_matches', 500),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_log_stats(self, server, sender_path, call_id, request):
        result = self.container_manager.get_log_stats()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- security scan handlers --

    def _scan_container_security(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.scan_container_security(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _scan_fleet_security(self, server, sender_path, call_id, request):
        result = self.container_manager.scan_fleet_security()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_security_summary(self, server, sender_path, call_id, request):
        result = self.container_manager.get_security_summary()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- vulnerability scanning handlers --

    def _scan_image_vulnerabilities(self, server, sender_path, call_id, request):
        result = self.container_manager.scan_image_vulnerabilities(
            request['image_path'],
            severity_filter=request.get('severity_filter'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _scan_container_vulnerabilities(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.scan_container_vulnerabilities(
            c, severity_filter=request.get('severity_filter'))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _scan_fleet_vulnerabilities(self, server, sender_path, call_id, request):
        result = self.container_manager.scan_fleet_vulnerabilities(
            severity_filter=request.get('severity_filter'))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_vulnerability_summary(self, server, sender_path, call_id, request):
        result = self.container_manager.get_vulnerability_summary()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- performance profiling handlers --

    def _profile_container_performance(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.profile_container_performance(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _profile_fleet_performance(self, server, sender_path, call_id, request):
        result = self.container_manager.profile_fleet_performance()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_performance_recommendations(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_performance_recommendations(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- capacity forecasting handlers --

    def _forecast_resource_needs(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.forecast_resource_needs(
            c, horizon_hours=request.get('horizon_hours', 24))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _forecast_fleet_capacity(self, server, sender_path, call_id, request):
        result = self.container_manager.forecast_fleet_capacity()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_capacity_recommendations(self, server, sender_path, call_id, request):
        result = self.container_manager.get_capacity_recommendations()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- filesystem handlers --

    def _read_container_file(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.read_container_file(
            c, request['path'], max_size=request.get('max_size', 1048576))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _write_container_file(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.write_container_file(
            c, request['path'], request['content'],
            create_dirs=request.get('create_dirs', True))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_container_files(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.list_container_files(
            c, request.get('path', '/'),
            recursive=request.get('recursive', False),
            max_entries=request.get('max_entries', 500))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_container_file(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.delete_container_file(c, request['path'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_file_info(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_file_info(c, request['path'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- process tree handlers --

    def _get_process_tree(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_process_tree(
            c, root_pid=request.get('root_pid'),
            max_depth=request.get('max_depth', 10))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_process_stats(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if c is None:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_process_stats(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # -- comparison report handlers --

    def _generate_comparison_report(self, server, sender_path, call_id, request):
        result = self.container_manager.generate_comparison_report(
            container_ids=request.get('container_ids'),
            include_recommendations=request.get('include_recommendations', True))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _generate_cost_report(self, server, sender_path, call_id, request):
        result = self.container_manager.generate_cost_report(
            container_ids=request.get('container_ids'))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Health check handlers
    # ------------------------------------------------------------------

    def _configure_health_check(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.configure_health_check(
            c, check_type=request.get('check_type', 'http'),
            endpoint=request.get('endpoint', '/'),
            port=request.get('port', 80),
            interval_seconds=request.get('interval_seconds', 30),
            timeout_seconds=request.get('timeout_seconds', 5),
            failure_threshold=request.get('failure_threshold', 3),
            success_threshold=request.get('success_threshold', 1),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_health_check(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_health_check(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _evaluate_health_check(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.evaluate_health_check(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_readiness_status(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_readiness_status(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_liveness_status(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_liveness_status(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _fleet_health_overview(self, server, sender_path, call_id):
        result = self.container_manager.fleet_health_overview()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Escalation chain handlers
    # ------------------------------------------------------------------

    def _configure_escalation_chain(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.configure_escalation_chain(
            c, name=request.get('name', 'default'),
            steps=request.get('steps'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _evaluate_escalation(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.evaluate_escalation(
            c, severity=request.get('severity', 0),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_escalation_status(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_escalation_status(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _reset_escalation_state(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.reset_escalation_state(
            c, chain_name=request.get('chain_name'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _disable_escalation_chain(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.disable_escalation_chain(
            c, chain_name=request['chain_name'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Compliance handlers
    # ------------------------------------------------------------------

    def _generate_compliance_report(self, server, sender_path, call_id, request):
        result = self.container_manager.generate_compliance_report(
            container_ids=request.get('container_ids'),
            policy=request.get('policy', 'basic'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _export_audit_logs(self, server, sender_path, call_id, request):
        result = self.container_manager.export_audit_logs(
            container_ids=request.get('container_ids'),
            format=request.get('format', 'json'),
            start_time=request.get('start_time'),
            end_time=request.get('end_time'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_compliance_summary(self, server, sender_path, call_id, request):
        result = self.container_manager.get_compliance_summary(
            policy=request.get('policy', 'basic'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Secret management handlers
    # ------------------------------------------------------------------

    def _create_secret(self, server, sender_path, call_id, request):
        result = self.container_manager.create_secret(
            name=request['name'],
            data=request.get('data', {}),
            namespace=request.get('namespace', 'default'),
            secret_type=request.get('secret_type', 'opaque'),
            labels=request.get('labels'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_secret(self, server, sender_path, call_id, request):
        result = self.container_manager.get_secret(
            secret_id=request['secret_id'],
            decrypt=request.get('decrypt', False),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_secret(self, server, sender_path, call_id, request):
        result = self.container_manager.delete_secret(request['secret_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _rotate_secret(self, server, sender_path, call_id, request):
        result = self.container_manager.rotate_secret(
            secret_id=request['secret_id'],
            new_data=request.get('new_data', {}),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_secrets(self, server, sender_path, call_id, request):
        result = self.container_manager.list_secrets(
            namespace=request.get('namespace'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_secret_usage(self, server, sender_path, call_id):
        result = self.container_manager.get_secret_usage()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Namespace / resource quota handlers
    # ------------------------------------------------------------------

    def _create_namespace(self, server, sender_path, call_id, request):
        result = self.container_manager.create_namespace(
            name=request['name'],
            labels=request.get('labels'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _set_resource_quota(self, server, sender_path, call_id, request):
        result = self.container_manager.set_resource_quota(
            namespace=request['namespace'],
            resource_type=request['resource_type'],
            hard_limit=request['hard_limit'],
            soft_limit=request.get('soft_limit'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_resource_quota(self, server, sender_path, call_id, request):
        result = self.container_manager.get_resource_quota(request['namespace'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _check_quota_compliance(self, server, sender_path, call_id, request):
        result = self.container_manager.check_quota_compliance(request['namespace'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_namespaces(self, server, sender_path, call_id):
        result = self.container_manager.list_namespaces()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_namespace(self, server, sender_path, call_id, request):
        result = self.container_manager.delete_namespace(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_namespace_summary(self, server, sender_path, call_id):
        result = self.container_manager.get_namespace_summary()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Deployment rollback handlers
    # ------------------------------------------------------------------

    def _record_deployment(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.record_deployment(
            c,
            config_snapshot=request.get('config_snapshot'),
            notes=request.get('notes', ''),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_deployment_history(self, server, sender_path, call_id, request):
        result = self.container_manager.get_deployment_history(
            container_id=request['container_id'],
            limit=request.get('limit', 10),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _rollback_deployment(self, server, sender_path, call_id, request):
        result = self.container_manager.rollback_deployment(
            container_id=request['container_id'],
            version=request['version'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_deployment_diff(self, server, sender_path, call_id, request):
        result = self.container_manager.get_deployment_diff(
            container_id=request['container_id'],
            version_a=request['version_a'],
            version_b=request['version_b'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_rollback_candidates(self, server, sender_path, call_id, request):
        result = self.container_manager.get_rollback_candidates(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_deployment_status(self, server, sender_path, call_id, request):
        result = self.container_manager.get_deployment_status(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Graceful shutdown handlers
    # ------------------------------------------------------------------

    def _configure_graceful_shutdown(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.configure_graceful_shutdown(
            c, drain_timeout=request.get('drain_timeout', 30),
            signal=request.get('signal', 'SIGTERM'),
            pre_stop_hook=request.get('pre_stop_hook'),
            stop_grace_period=request.get('stop_grace_period', 10),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _initiate_graceful_shutdown(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.initiate_graceful_shutdown(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_shutdown_status(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_shutdown_status(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _force_shutdown(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.force_shutdown(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _batch_graceful_shutdown(self, server, sender_path, call_id, request):
        result = self.container_manager.batch_graceful_shutdown(
            container_ids=request.get('container_ids', []),
            drain_timeout=request.get('drain_timeout', 30),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_drain_progress(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_drain_progress(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Config hot-reload handlers
    # ------------------------------------------------------------------

    def _register_config_watcher(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.register_config_watcher(
            c, config_path=request['config_path'],
            reload_action=request.get('reload_action', 'restart'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _trigger_config_reload(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.trigger_config_reload(c, request['watcher_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_config_watchers(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_config_watchers(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _remove_config_watcher(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.remove_config_watcher(c, request['watcher_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _hot_reload_config(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.hot_reload_config(
            c, new_config=request.get('config', {}),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_reload_history(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_reload_history(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Event correlation handlers
    # ------------------------------------------------------------------

    def _record_event(self, server, sender_path, call_id, request):
        result = self.container_manager.record_event(
            container_id=request['container_id'],
            event_type=request['event_type'],
            message=request.get('message', ''),
            severity=request.get('severity', 'info'),
            metadata=request.get('metadata'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _correlate_events(self, server, sender_path, call_id, request):
        result = self.container_manager.correlate_events(
            time_window=request.get('time_window', 300.0),
            min_containers=request.get('min_containers', 2),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _analyze_event_patterns(self, server, sender_path, call_id, request):
        result = self.container_manager.analyze_event_patterns(
            time_window=request.get('time_window', 3600.0),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _suggest_root_cause(self, server, sender_path, call_id, request):
        result = self.container_manager.suggest_root_cause(
            time_window=request.get('time_window', 300.0),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_event_timeline(self, server, sender_path, call_id, request):
        result = self.container_manager.get_event_timeline(
            container_ids=request.get('container_ids'),
            time_window=request.get('time_window', 3600.0),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Network monitoring handlers
    # ------------------------------------------------------------------

    def _configure_network_monitoring(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.configure_network_monitoring(
            c, interfaces=request.get('interfaces'),
            sample_interval=request.get('sample_interval', 1.0),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _record_network_sample(self, server, sender_path, call_id, request):
        result = self.container_manager.record_network_sample(
            container_id=request['container_id'],
            interface=request.get('interface', 'eth0'),
            latency_ms=request.get('latency_ms', 0),
            rx_bytes=request.get('rx_bytes', 0),
            tx_bytes=request.get('tx_bytes', 0),
            rx_packets=request.get('rx_packets', 0),
            tx_packets=request.get('tx_packets', 0),
            errors=request.get('errors', 0),
            dropped=request.get('dropped', 0),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_network_latency_stats(self, server, sender_path, call_id, request):
        result = self.container_manager.get_network_latency_stats(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_bandwidth_stats(self, server, sender_path, call_id, request):
        result = self.container_manager.get_bandwidth_stats(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_network_health(self, server, sender_path, call_id, request):
        result = self.container_manager.get_network_health(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _fleet_network_overview(self, server, sender_path, call_id):
        result = self.container_manager.fleet_network_overview()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Storage profiling handlers
    # ------------------------------------------------------------------

    def _configure_storage_profiling(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.configure_storage_profiling(
            c, paths=request.get('paths'),
            cache_size_mb=request.get('cache_size_mb', 64),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _record_storage_io(self, server, sender_path, call_id, request):
        result = self.container_manager.record_storage_io(
            container_id=request['container_id'],
            op_type=request.get('op_type', 'read'),
            path=request.get('path', '/'),
            bytes_count=request.get('bytes_count', 0),
            duration_ms=request.get('duration_ms', 0),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_storage_io_stats(self, server, sender_path, call_id, request):
        result = self.container_manager.get_storage_io_stats(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_storage_io_latency(self, server, sender_path, call_id, request):
        result = self.container_manager.get_storage_io_latency(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _clear_storage_cache(self, server, sender_path, call_id, request):
        result = self.container_manager.clear_storage_cache(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_storage_hot_paths(self, server, sender_path, call_id, request):
        result = self.container_manager.get_storage_hot_paths(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Audit integrity handlers
    # ------------------------------------------------------------------

    def _initialize_audit_integrity(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.initialize_audit_integrity(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _append_audit_event(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.append_audit_event(
            c, op=request['op'], details=request.get('details'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _verify_audit_integrity(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.verify_audit_integrity(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_audit_integrity_report(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_audit_integrity_report(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_tamper_summary(self, server, sender_path, call_id):
        result = self.container_manager.get_tamper_summary()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Rate limiting handlers
    # ------------------------------------------------------------------

    def _configure_rate_limit(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.configure_rate_limit(
            c, name=request.get('name', 'default'),
            rate=float(request.get('rate', 100)),
            burst=int(request.get('burst', 200)),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _check_rate_limit(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.check_rate_limit(
            c, name=request.get('name', 'default'),
            tokens=int(request.get('tokens', 1)),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_rate_limit_stats(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_rate_limit_stats(c, request.get('name', 'default'))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_all_rate_limiters(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.get_all_rate_limiters(c)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _reset_rate_limit(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.reset_rate_limit(c, request.get('name', 'default'))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_rate_limit(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.delete_rate_limit(c, request.get('name', 'default'))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Feature flag handlers
    # ------------------------------------------------------------------

    def _create_feature_flag(self, server, sender_path, call_id, request):
        result = self.container_manager.create_feature_flag(
            name=request['name'],
            enabled=request.get('enabled', False),
            rollout_percentage=float(request.get('rollout_percentage', 0)),
            target_containers=request.get('target_containers'),
            description=request.get('description', ''),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _evaluate_feature_flag(self, server, sender_path, call_id, request):
        result = self.container_manager.evaluate_feature_flag(
            container_id=request['container_id'],
            flag_name=request['flag_name'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _update_feature_flag(self, server, sender_path, call_id, request):
        kwargs = {}
        for key in ('enabled', 'rollout_percentage', 'target_containers', 'description'):
            if key in request:
                kwargs[key] = request[key]
        result = self.container_manager.update_feature_flag(request['name'], **kwargs)
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_feature_flags(self, server, sender_path, call_id):
        result = self.container_manager.list_feature_flags()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_feature_flag(self, server, sender_path, call_id, request):
        result = self.container_manager.delete_feature_flag(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_feature_flag_summary(self, server, sender_path, call_id):
        result = self.container_manager.get_feature_flag_summary()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Blue-green deployment handlers
    # ------------------------------------------------------------------

    def _create_bluegreen_deployment(self, server, sender_path, call_id, request):
        result = self.container_manager.create_bluegreen_deployment(
            name=request['name'],
            blue_container_id=request['blue_container_id'],
            green_container_id=request.get('green_container_id'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _switch_traffic(self, server, sender_path, call_id, request):
        result = self.container_manager.switch_traffic(
            deployment_name=request['deployment_name'],
            target_slot=request['target_slot'],
            percentage=request.get('percentage'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_bluegreen_status(self, server, sender_path, call_id, request):
        result = self.container_manager.get_bluegreen_status(request['deployment_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _rollback_bluegreen(self, server, sender_path, call_id, request):
        result = self.container_manager.rollback_bluegreen(request['deployment_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_bluegreen_history(self, server, sender_path, call_id, request):
        result = self.container_manager.get_bluegreen_history(request['deployment_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_bluegreen_deployments(self, server, sender_path, call_id):
        result = self.container_manager.list_bluegreen_deployments()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_bluegreen_deployment(self, server, sender_path, call_id, request):
        result = self.container_manager.delete_bluegreen_deployment(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Canary deployment handlers
    # ------------------------------------------------------------------

    def _create_canary_deployment(self, server, sender_path, call_id, request):
        result = self.container_manager.create_canary_deployment(
            name=request['name'],
            stable_container_id=request['stable_container_id'],
            canary_container_id=request['canary_container_id'],
            traffic_percentage=float(request.get('traffic_percentage', 10)),
            error_threshold=float(request.get('error_threshold', 5)),
            latency_threshold_ms=float(request.get('latency_threshold_ms', 200)),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _record_canary_metric(self, server, sender_path, call_id, request):
        result = self.container_manager.record_canary_metric(
            deployment_name=request['deployment_name'],
            slot=request['slot'],
            latency_ms=float(request.get('latency_ms', 0)),
            is_error=request.get('is_error', False),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _evaluate_canary_health(self, server, sender_path, call_id, request):
        result = self.container_manager.evaluate_canary_health(request['deployment_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _promote_canary(self, server, sender_path, call_id, request):
        result = self.container_manager.promote_canary(request['deployment_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _rollback_canary(self, server, sender_path, call_id, request):
        result = self.container_manager.rollback_canary(request['deployment_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_canary_status(self, server, sender_path, call_id, request):
        result = self.container_manager.get_canary_status(request['deployment_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_canary_deployments(self, server, sender_path, call_id):
        result = self.container_manager.list_canary_deployments()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_canary_deployment(self, server, sender_path, call_id, request):
        result = self.container_manager.delete_canary_deployment(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Service discovery handlers
    # ------------------------------------------------------------------

    def _register_service(self, server, sender_path, call_id, request):
        result = self.container_manager.register_service(
            name=request['name'],
            container_id=request['container_id'],
            port=int(request.get('port', 80)),
            health_endpoint=request.get('health_endpoint', '/health'),
            tags=request.get('tags'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _deregister_service(self, server, sender_path, call_id, request):
        result = self.container_manager.deregister_service(
            name=request['name'],
            container_id=request['container_id'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _discover_service(self, server, sender_path, call_id, request):
        result = self.container_manager.discover_service(
            name=request['name'],
            tag=request.get('tag'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _service_heartbeat(self, server, sender_path, call_id, request):
        result = self.container_manager.service_heartbeat(
            name=request['name'],
            container_id=request['container_id'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_service_instances(self, server, sender_path, call_id, request):
        result = self.container_manager.get_service_instances(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _inject_dependency(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.inject_dependency(
            c, service_name=request['service_name'],
            env_var=request['env_var'],
            required=request.get('required', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_services(self, server, sender_path, call_id):
        result = self.container_manager.list_services()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Distributed tracing handlers
    # ------------------------------------------------------------------

    def _create_trace(self, server, sender_path, call_id, request):
        result = self.container_manager.create_trace(
            name=request['name'],
            container_id=request.get('container_id'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _start_span(self, server, sender_path, call_id, request):
        result = self.container_manager.start_span(
            trace_id=request['trace_id'],
            operation=request['operation'],
            container_id=request.get('container_id'),
            parent_span_id=request.get('parent_span_id'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _finish_span(self, server, sender_path, call_id, request):
        result = self.container_manager.finish_span(
            trace_id=request['trace_id'],
            span_id=request['span_id'],
            status=request.get('status', 'ok'),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _add_span_attribute(self, server, sender_path, call_id, request):
        result = self.container_manager.add_span_attribute(
            trace_id=request['trace_id'],
            span_id=request['span_id'],
            key=request['key'],
            value=request['value'],
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _finish_trace(self, server, sender_path, call_id, request):
        result = self.container_manager.finish_trace(request['trace_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_trace(self, server, sender_path, call_id, request):
        result = self.container_manager.get_trace(request['trace_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_trace_summary(self, server, sender_path, call_id, request):
        result = self.container_manager.get_trace_summary(request['trace_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_traces(self, server, sender_path, call_id, request):
        result = self.container_manager.list_traces(limit=request.get('limit', 20))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Chaos engineering handlers
    # ------------------------------------------------------------------

    def _create_chaos_experiment(self, server, sender_path, call_id, request):
        result = self.container_manager.create_chaos_experiment(
            name=request['name'],
            target_containers=request.get('target_containers', []),
            fault_type=request.get('fault_type', 'latency'),
            fault_config=request.get('fault_config'),
            duration_seconds=int(request.get('duration_seconds', 60)),
            blast_radius=float(request.get('blast_radius', 50)),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _start_chaos_experiment(self, server, sender_path, call_id, request):
        result = self.container_manager.start_chaos_experiment(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _record_chaos_observation(self, server, sender_path, call_id, request):
        result = self.container_manager.record_chaos_observation(
            experiment_name=request['experiment_name'],
            container_id=request['container_id'],
            metric=request['metric'],
            value=float(request.get('value', 0)),
            notes=request.get('notes', ''),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _stop_chaos_experiment(self, server, sender_path, call_id, request):
        result = self.container_manager.stop_chaos_experiment(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_chaos_results(self, server, sender_path, call_id, request):
        result = self.container_manager.get_chaos_results(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_chaos_experiments(self, server, sender_path, call_id):
        result = self.container_manager.list_chaos_experiments()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _create_game_day(self, server, sender_path, call_id, request):
        result = self.container_manager.create_game_day(
            name=request['name'],
            scenario=request['scenario'],
            participants=request.get('participants', []),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_game_day(self, server, sender_path, call_id, request):
        result = self.container_manager.get_game_day(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_game_days(self, server, sender_path, call_id):
        result = self.container_manager.list_game_days()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Cost allocation handlers
    # ------------------------------------------------------------------

    def _configure_cost_allocation(self, server, sender_path, call_id, request):
        c = self.container_manager.get_container(request['container_id'])
        if not c:
            self._reply(server, sender_path, call_id, {"ok": False, "error": "container not found"})
            return
        result = self.container_manager.configure_cost_allocation(
            c, cost_per_hour=float(request.get('cost_per_hour', 0)),
            billing_tag=request.get('billing_tag', ''),
            team=request.get('team', ''),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_container_cost(self, server, sender_path, call_id, request):
        result = self.container_manager.get_container_cost(
            container_id=request['container_id'],
            hours=float(request.get('hours', 24)),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_team_cost_summary(self, server, sender_path, call_id, request):
        result = self.container_manager.get_team_cost_summary(
            team=request['team'],
            hours=float(request.get('hours', 24)),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_fleet_cost_summary(self, server, sender_path, call_id, request):
        result = self.container_manager.get_fleet_cost_summary(
            hours=float(request.get('hours', 24)),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_billing_breakdown(self, server, sender_path, call_id, request):
        result = self.container_manager.get_billing_breakdown(
            hours=float(request.get('hours', 24)),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # SLO/SLI tracking handlers
    # ------------------------------------------------------------------

    def _create_slo(self, server, sender_path, call_id, request):
        result = self.container_manager.create_slo(
            name=request['name'],
            target_percentage=float(request.get('target_percentage', 99.9)),
            window_days=int(request.get('window_days', 30)),
            sli_type=request.get('sli_type', 'availability'),
            description=request.get('description', ''),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _record_sli_event(self, server, sender_path, call_id, request):
        result = self.container_manager.record_sli_event(
            slo_name=request['slo_name'],
            is_good=request.get('is_good', True),
        )
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_slo_status(self, server, sender_path, call_id, request):
        result = self.container_manager.get_slo_status(request['slo_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_slos(self, server, sender_path, call_id):
        result = self.container_manager.list_slos()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_error_budget_report(self, server, sender_path, call_id):
        result = self.container_manager.get_error_budget_report()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_slo(self, server, sender_path, call_id, request):
        result = self.container_manager.delete_slo(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Resource right-sizing handlers
    # ------------------------------------------------------------------

    def _analyze_resource_usage(self, server, sender_path, call_id, request):
        result = self.container_manager.analyze_resource_usage(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _recommend_right_sizing(self, server, sender_path, call_id, request):
        result = self.container_manager.recommend_right_sizing(request['container_id'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _apply_right_sizing(self, server, sender_path, call_id, request):
        result = self.container_manager.apply_right_sizing(
            request['container_id'], dry_run=request.get('dry_run', True))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _fleet_right_sizing_report(self, server, sender_path, call_id):
        result = self.container_manager.fleet_right_sizing_report()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Service mesh handlers
    # ------------------------------------------------------------------

    def _create_mesh_cluster(self, server, sender_path, call_id, request):
        result = self.container_manager.create_mesh_cluster(
            name=request['name'],
            endpoint=request['endpoint'],
            region=request.get('region', 'default'))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _add_mesh_service(self, server, sender_path, call_id, request):
        result = self.container_manager.add_mesh_service(
            cluster_name=request['cluster_name'],
            service_name=request['service_name'],
            port=int(request.get('port', 80)))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _create_traffic_policy(self, server, sender_path, call_id, request):
        result = self.container_manager.create_traffic_policy(
            name=request['name'],
            source_service=request['source_service'],
            destination_service=request['destination_service'],
            rules=request.get('rules'))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _evaluate_traffic_policy(self, server, sender_path, call_id, request):
        result = self.container_manager.evaluate_traffic_policy(
            source=request['source'],
            destination=request['destination'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_mesh_topology(self, server, sender_path, call_id):
        result = self.container_manager.get_mesh_topology()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _list_mesh_policies(self, server, sender_path, call_id):
        result = self.container_manager.list_mesh_policies()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_mesh_policy(self, server, sender_path, call_id, request):
        result = self.container_manager.delete_mesh_policy(request['name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    # ------------------------------------------------------------------
    # Auto-rollback handlers
    # ------------------------------------------------------------------

    def _configure_auto_rollback(self, server, sender_path, call_id, request):
        result = self.container_manager.configure_auto_rollback(
            slo_name=request['slo_name'],
            deployment_name=request.get('deployment_name'),
            canary_name=request.get('canary_name'),
            breach_threshold=float(request.get('breach_threshold', 1.0)),
            cooldown_seconds=int(request.get('cooldown_seconds', 300)))
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _check_and_auto_rollback(self, server, sender_path, call_id):
        result = self.container_manager.check_and_auto_rollback()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_auto_rollback_status(self, server, sender_path, call_id):
        result = self.container_manager.get_auto_rollback_status()
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _get_auto_rollback_history(self, server, sender_path, call_id, request):
        result = self.container_manager.get_auto_rollback_history(request['slo_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _disable_auto_rollback(self, server, sender_path, call_id, request):
        result = self.container_manager.disable_auto_rollback(request['slo_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

    def _delete_auto_rollback(self, server, sender_path, call_id, request):
        result = self.container_manager.delete_auto_rollback(request['slo_name'])
        self._reply(server, sender_path, call_id, {"ok": True, **result})

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
