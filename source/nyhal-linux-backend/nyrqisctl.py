#!/usr/bin/env python3
"""
nyrqisctl — the operator CLI for a running Nyrqis backend daemon.

Drives the daemon's main service socket (``--socket``, the same socket
``nyrqis_backend.py service serve`` binds) over the IPC transport: the
status service (``ping`` / ``status`` / ``health``) and the control
plane (``containers list|run|kill``). The caller claims the operator
identity (``DEFAULT_OPERATOR_ID``); the daemon authenticates it by the
kernel-attached uid (the daemon's own user) — an unforgeable check, and
the only identity the control service accepts.

Usage::

    nyrqisctl --socket /run/nyrqis/status.sock status
    nyrqisctl containers list
    nyrqisctl containers run --network --memory 512 /bin/sleep 30
    nyrqisctl --health-socket /run/nyrqis/health.sock health
    nyrqisctl --json health

The daemon's default socket is ``/tmp/nyrqis-status.sock`` (the systemd
unit serves ``/run/nyrqis/status.sock`` — pass ``--socket`` there). A
dedicated health-probe socket (``--health-socket``, ADR-0021) serves
``ping``/``status``/``health`` without contending with container traffic
on the main socket; control commands always use the main ``--socket``
(the health socket never serves them).

Exit status: 0 on success, 1 when the daemon did not reply or the op
failed (``ok: false``), 2 on usage errors.
"""

import argparse
import base64
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

from ipc.transport import (
    DEFAULT_OPERATOR_ID, IPCClient, IPCTransportError,
)

logger = logging.getLogger("nyrqisctl")

DEFAULT_SOCKET = "/tmp/nyrqis-status.sock"
DEFAULT_TIMEOUT_S = 30.0


# The status-service commands the daemon serves on BOTH the main and
# (when configured) the dedicated health socket (ADR-0021).
STATUS_COMMANDS = ("ping", "status", "health")
CONTROL_COMMANDS = ("containers-list", "containers-run", "containers-kill")
NUI_COMMANDS = ("nui-validate", "nui-load", "nui-current")
VAULT_COMMANDS = (
    "vault-volume-create", "vault-volume-open", "vault-volume-list",
    "vault-volume-grant", "vault-volume-revoke", "vault-volume-grants",
    "vault-volume-close", "vault-volume-write", "vault-volume-read",
    "vault-volume-snapshot", "vault-volume-snapshots",
    "vault-volume-restore", "vault-volume-snapshot-delete",
    "vault-volume-delete", "vault-volume-mount", "vault-volume-rekey",
    "vault-volume-quota-set", "vault-volume-quota-get",
    "vault-volume-usage", "vault-volume-summary",
    "vault-volume-events",
)
APP_COMMANDS = ("app-install", "app-list", "app-launch", "app-terminate")
# Vault ops ride the same 64 KiB datagram the transport serves, so a
# single write/read is capped (the service enforces the same limit).
MAX_IO_BYTES = 32 * 1024


# -- payload construction (pure, unit-testable) ------------------------

def _volume_ref(payload: Dict[str, Any], args) -> Dict[str, Any]:
    """Attach the volume id-or-name to a payload (shared by the
    grant/revoke/grants ops, which address the volume by id or
    --name)."""
    if args.name:
        payload["name"] = args.name
    else:
        payload["volume_id"] = args.volume_id
    return payload


def build_payload(command: str, args: argparse.Namespace) -> Dict[str, Any]:
    """The JSON request for ``command`` (the ``args`` come from the
    command's own subparser). Mirror of the daemon's service ops."""
    if command in STATUS_COMMANDS:
        return {"op": command}
    if command == "containers-list":
        return {"service": "control", "op": "container_list"}
    if command == "containers-run":
        return {
            "service": "control",
            "op": "container_run",
            "command": args.run_command,
            "capabilities": [
                c.strip() for c in (args.capabilities or "").split(",")
                if c.strip()
            ],
            "network": bool(args.network),
            "memory_mb": int(args.memory),
            "pids": int(args.pids),
            "name": args.name or None,
            "restart_policy": args.restart_policy,
            "restart_max_retries": args.restart_max_retries,
            "restart_delay": args.restart_delay,
        }
    if command == "containers-kill":
        return {
            "service": "control",
            "op": "container_kill",
            "container_id": args.container_id,
        }
    if command == "containers-stats":
        return {
            "service": "control",
            "op": "container_stats",
            "container_id": args.container_id,
        }
    if command == "containers-logs":
        payload = {
            "service": "control",
            "op": "container_logs",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            payload["tail"] = args.tail
        if args.stream != "both":
            payload["stream"] = args.stream
        return payload
    if command == "containers-exec":
        return {
            "service": "control",
            "op": "container_exec",
            "container_id": args.container_id,
            "command": args.exec_command,
            "timeout": args.timeout,
        }
    if command == "containers-top":
        p: Dict[str, Any] = {
            "service": "control",
            "op": "container_top",
            "container_id": args.container_id,
            "sort_by": args.sort_by,
            "descending": args.descending,
            "max_depth": args.max_depth,
            "summary_only": args.summary_only,
        }
        return p
    if command == "containers-net":
        return {
            "service": "control",
            "op": "container_network_stats",
            "container_id": args.container_id,
        }
    if command == "quotas-set":
        return {
            "service": "control",
            "op": "quota_set",
            "owner": args.owner,
            "memory_mb": args.memory,
            "pid_limit": args.pids,
            "max_containers": args.containers,
        }
    if command == "quotas-get":
        return {
            "service": "control",
            "op": "quota_get",
            "owner": args.owner,
        }
    if command == "quotas-list":
        return {"service": "control", "op": "quota_list"}
    if command == "quotas-delete":
        return {
            "service": "control",
            "op": "quota_delete",
            "owner": args.owner,
        }
    if command == "quotas-usage":
        return {
            "service": "control",
            "op": "quota_usage",
            "owner": args.owner,
        }
    if command == "images-list":
        payload = {
            "service": "control",
            "op": "image_list",
        }
        if getattr(args, "base_dir", None):
            payload["base_dir"] = args.base_dir
        return payload
    if command == "images-remove":
        return {
            "service": "control",
            "op": "image_remove",
            "path": args.path,
        }
    if command == "images-export":
        return {
            "service": "control",
            "op": "image_export",
            "image_path": args.image_path,
            "tar_path": args.tar_path,
        }
    if command == "images-import":
        return {
            "service": "control",
            "op": "image_import",
            "tar_path": args.tar_path,
            "dest_dir": args.dest_dir,
            "name": args.name,
        }
    if command == "containers-checkpoint":
        payload: Dict[str, Any] = {
            "service": "control",
            "op": "container_checkpoint",
            "container_id": args.container_id,
        }
        if args.path:
            payload["path"] = args.path
        return payload
    if command == "containers-restore":
        import json as _json_restore
        with open(args.checkpoint_file) as f:
            cp = _json_restore.load(f)
        return {
            "service": "control",
            "op": "container_restore",
            "checkpoint": cp,
        }
    if command == "containers-diff":
        import json as _json_diff
        with open(args.checkpoint_a) as f:
            cp_a = _json_diff.load(f)
        with open(args.checkpoint_b) as f:
            cp_b = _json_diff.load(f)
        return {
            "service": "control",
            "op": "container_diff",
            "checkpoint_a": cp_a,
            "checkpoint_b": cp_b,
        }
    if command == "containers-events":
        payload = {
            "service": "control",
            "op": "container_events",
        }
        if args.tail is not None:
            payload["tail"] = args.tail
        if args.container:
            payload["container_id"] = args.container
        if args.kind:
            payload["kind"] = args.kind
        return payload
    if command == "containers-health":
        return {
            "service": "control",
            "op": "container_health",
            "container_id": args.container_id,
        }
    if command == "containers-limits":
        return {
            "service": "control",
            "op": "container_resource_limits",
            "container_id": args.container_id,
        }
    if command == "containers-sched":
        if args.nice is not None:
            return {
                "service": "control",
                "op": "container_set_nice",
                "container_id": args.container_id,
                "nice": args.nice,
            }
        if args.affinity is not None:
            return {
                "service": "control",
                "op": "container_set_affinity",
                "container_id": args.container_id,
                "cores": args.affinity,
            }
        # Default: query
        return {
            "service": "control",
            "op": "container_scheduling",
            "container_id": args.container_id,
        }
    if command == "containers-netpolicy":
        return {
            "service": "control",
            "op": "container_network_policy",
            "container_id": args.container_id,
        }
    if command == "containers-start-ordered":
        return {
            "service": "control",
            "op": "container_start_ordered",
            "container_ids": args.container_ids,
        }
    if command == "containers-stop-ordered":
        return {
            "service": "control",
            "op": "container_stop_ordered",
            "container_ids": args.container_ids,
        }
    if command == "containers-dep-graph":
        payload: Dict[str, Any] = {
            "service": "control",
            "op": "container_dependency_graph",
        }
        if args.container_ids:
            payload["container_ids"] = args.container_ids
        return payload
    if command == "containers-restart-info":
        return {
            "service": "control",
            "op": "container_restart_info",
            "container_id": args.container_id,
        }
    if command == "containers-restart-set":
        p: Dict[str, Any] = {
            "service": "control",
            "op": "container_set_restart",
            "container_id": args.container_id,
            "policy": args.policy,
        }
        if args.max_retries is not None:
            p["max_retries"] = args.max_retries
        if args.delay is not None:
            p["delay"] = args.delay
        return p
    if command == "containers-env-set":
        return {
            "service": "control",
            "op": "container_env_set",
            "container_id": args.container_id,
            "key": args.key,
            "value": args.value,
        }
    if command == "containers-env-unset":
        return {
            "service": "control",
            "op": "container_env_unset",
            "container_id": args.container_id,
            "key": args.key,
        }
    if command == "containers-env-list":
        return {
            "service": "control",
            "op": "container_env_list",
            "container_id": args.container_id,
        }
    if command == "containers-snapshot-export":
        payload: Dict[str, Any] = {
            "service": "control",
            "op": "snapshot_export",
            "container_id": args.container_id,
        }
        if args.export_path:
            payload["export_path"] = args.export_path
        return payload
    if command == "containers-snapshot-import":
        return {
            "service": "control",
            "op": "snapshot_import",
            "archive_path": args.archive_path,
        }
    if command == "containers-resource-history":
        p: Dict[str, Any] = {
            "service": "control",
            "op": "resource_history",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            p["tail"] = args.tail
        return p
    if command == "containers-resource-record":
        return {
            "service": "control",
            "op": "resource_record",
            "container_id": args.container_id,
        }
    if command == "containers-resource-record-start":
        return {
            "service": "control",
            "op": "resource_record_start",
            "container_id": args.container_id,
            "interval": args.interval,
        }
    if command == "containers-resource-record-stop":
        return {
            "service": "control",
            "op": "resource_record_stop",
            "container_id": args.container_id,
        }
    if command == "containers-update-limits":
        p: Dict[str, Any] = {
            "service": "control",
            "op": "container_update_limits",
            "container_id": args.container_id,
        }
        if args.memory is not None:
            p["memory_mb"] = args.memory
        if args.pids is not None:
            p["pid_limit"] = args.pids
        if args.cpu_quota is not None:
            p["cpu_quota_us"] = args.cpu_quota
        return p
    if command == "containers-label-set":
        return {
            "service": "control",
            "op": "label_set",
            "container_id": args.container_id,
            "key": args.key,
            "value": args.value,
        }
    if command == "containers-label-unset":
        return {
            "service": "control",
            "op": "label_unset",
            "container_id": args.container_id,
            "key": args.key,
        }
    if command == "containers-label-list":
        return {
            "service": "control",
            "op": "label_list",
            "container_id": args.container_id,
        }
    if command == "containers-label-filter":
        labels = {}
        for pair in args.labels:
            if "=" in pair:
                k, v = pair.split("=", 1)
                labels[k] = v
        return {
            "service": "control",
            "op": "label_filter",
            "labels": labels,
        }
    if command == "containers-cgroup2-status":
        return {
            "service": "control",
            "op": "cgroup2_status",
            "container_id": args.container_id,
        }
    if command == "containers-verify-enforcement":
        return {
            "service": "control",
            "op": "verify_enforcement",
            "container_id": args.container_id,
        }
    if command == "containers-lock":
        return {
            "service": "control",
            "op": "lock_acquire",
            "container_id": args.container_id,
            "non_blocking": args.non_blocking,
        }
    if command == "containers-unlock":
        return {
            "service": "control",
            "op": "lock_release",
            "container_id": args.container_id,
        }
    if command == "containers-locks":
        return {
            "service": "control",
            "op": "lock_list",
        }
    if command == "containers-alert-history":
        p: Dict[str, Any] = {
            "service": "control",
            "op": "alert_history",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            p["tail"] = args.tail
        if args.resource:
            p["resource"] = args.resource
        return p
    if command == "containers-alert-clear":
        return {
            "service": "control",
            "op": "alert_clear",
            "container_id": args.container_id,
        }
    if command == "containers-alert-thresholds":
        p2: Dict[str, Any] = {
            "service": "control",
            "op": "alert_thresholds",
            "container_id": args.container_id,
        }
        if args.memory_warning is not None:
            p2["memory_warning"] = args.memory_warning
        if args.memory_critical is not None:
            p2["memory_critical"] = args.memory_critical
        if args.pid_warning is not None:
            p2["pid_warning"] = args.pid_warning
        if args.pid_critical is not None:
            p2["pid_critical"] = args.pid_critical
        if args.cpu_throttle is not None:
            p2["cpu_throttle"] = args.cpu_throttle
        return p2
    if command == "containers-alert-acknowledge":
        return {
            "service": "control",
            "op": "alert_acknowledge",
            "container_id": args.container_id,
            "alert_index": args.alert_index,
            "acknowledged_by": args.by,
        }
    if command == "containers-alert-suppress":
        return {
            "service": "control",
            "op": "alert_suppress",
            "container_id": args.container_id,
            "resource": args.resource,
            "level": args.level,
            "duration_s": args.duration,
        }
    if command == "containers-alert-unsuppress":
        return {
            "service": "control",
            "op": "alert_unsuppress",
            "container_id": args.container_id,
            "resource": args.resource,
            "level": args.level,
        }
    if command == "containers-alert-statistics":
        return {
            "service": "control",
            "op": "alert_statistics",
            "container_id": args.container_id,
        }
    if command == "containers-alert-suppressions":
        return {
            "service": "control",
            "op": "alert_suppressions_list",
            "container_id": args.container_id,
        }
    if command == "containers-oom-status":
        return {
            "service": "control",
            "op": "oom_status",
            "container_id": args.container_id,
        }
    if command == "containers-oom-set":
        p3: Dict[str, Any] = {
            "service": "control",
            "op": "oom_set",
            "container_id": args.container_id,
        }
        if args.oom_score_adj is not None:
            p3["oom_score_adj"] = args.oom_score_adj
        if args.oom_kill_disable is not None:
            p3["oom_kill_disable"] = args.oom_kill_disable
        if args.memory_swap_max is not None:
            p3["memory_swap_max"] = args.memory_swap_max
        return p3
    if command == "containers-oom-events":
        p4: Dict[str, Any] = {
            "service": "control",
            "op": "oom_events",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            p4["tail"] = args.tail
        return p4
    if command == "containers-dashboard":
        p5: Dict[str, Any] = {
            "service": "control",
            "op": "dashboard",
        }
        if args.container_id:
            p5["container_id"] = args.container_id
        return p5
    if command == "containers-export-history":
        p6: Dict[str, Any] = {
            "service": "control",
            "op": "export_history",
            "container_id": args.container_id,
            "output_path": args.output_path,
            "format": args.format,
        }
        if args.tail is not None:
            p6["tail"] = args.tail
        return p6
    if command == "containers-export-snapshot":
        return {
            "service": "control",
            "op": "export_snapshot",
            "container_id": args.container_id,
            "output_path": args.output_path,
        }
    if command == "webhook-register":
        p: Dict[str, Any] = {
            "service": "control",
            "op": "webhook_register",
            "url": args.url,
        }
        if args.events:
            p["events"] = args.events
        if args.secret:
            p["secret"] = args.secret
        if args.container_filter:
            p["container_filter"] = args.container_filter
        return p
    if command == "webhook-unregister":
        return {
            "service": "control",
            "op": "webhook_unregister",
            "webhook_id": args.webhook_id,
        }
    if command == "webhook-list":
        return {
            "service": "control",
            "op": "webhook_list",
        }
    if command == "webhook-enable":
        return {
            "service": "control",
            "op": "webhook_enable",
            "webhook_id": args.webhook_id,
        }
    if command == "webhook-disable":
        return {
            "service": "control",
            "op": "webhook_disable",
            "webhook_id": args.webhook_id,
        }
    if command == "sla-check":
        return {
            "service": "control",
            "op": "sla_check",
            "container_id": args.container_id,
        }
    if command == "sla-violations":
        p: Dict[str, Any] = {
            "service": "control",
            "op": "sla_violations",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            p["tail"] = args.tail
        return p
    if command == "sla-set":
        p2: Dict[str, Any] = {
            "service": "control",
            "op": "sla_set",
            "container_id": args.container_id,
        }
        if args.uptime_target is not None:
            p2["uptime_target"] = args.uptime_target
        if args.max_restart_count is not None:
            p2["max_restart_count"] = args.max_restart_count
        if args.alert_on_breach is not None:
            p2["alert_on_breach"] = args.alert_on_breach
        return p2
    if command == "sla-escalation-policy":
        payload: Dict[str, Any] = {
            "service": "control",
            "op": "sla_escalation_policy",
            "container_id": args.container_id,
        }
        # Parse policy string if provided
        if args.policy:
            levels = []
            for part in args.policy.split(","):
                part = part.strip()
                if ":" in part:
                    threshold_str, actions_str = part.split(":", 1)
                    levels.append({
                        "threshold": int(threshold_str),
                        "actions": [a.strip() for a in actions_str.split("+")],
                        "cooldown_s": 300,
                    })
            payload["levels"] = levels
        return payload
    if command == "sla-escalation-status":
        return {
            "service": "control",
            "op": "sla_escalation_status",
            "container_id": args.container_id,
        }
    if command == "sla-escalation-reset":
        return {
            "service": "control",
            "op": "sla_escalation_reset",
            "container_id": args.container_id,
        }
    if command == "sla-escalation-history":
        p3: Dict[str, Any] = {
            "service": "control",
            "op": "sla_escalation_history",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            p3["tail"] = args.tail
        return p3
    if command == "billing-rates-set":
        p3: Dict[str, Any] = {
            "service": "control",
            "op": "billing_rates_set",
        }
        if args.memory_mb_per_hour is not None:
            p3["memory_mb_per_hour"] = args.memory_mb_per_hour
        if args.cpu_per_hour is not None:
            p3["cpu_per_hour"] = args.cpu_per_hour
        if args.pid_per_hour is not None:
            p3["pid_per_hour"] = args.pid_per_hour
        if args.storage_mb_per_hour is not None:
            p3["storage_mb_per_hour"] = args.storage_mb_per_hour
        return p3
    if command == "billing-rates-get":
        return {
            "service": "control",
            "op": "billing_rates_get",
        }
    if command == "billing-record":
        return {
            "service": "control",
            "op": "billing_record",
            "container_id": args.container_id,
        }
    if command == "billing-records":
        p4: Dict[str, Any] = {
            "service": "control",
            "op": "billing_records",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            p4["tail"] = args.tail
        return p4
    if command == "billing-summary":
        p5: Dict[str, Any] = {
            "service": "control",
            "op": "billing_summary",
        }
        if args.container_id:
            p5["container_id"] = args.container_id
        return p5
    if command == "cost-budget-configure":
        payload: Dict[str, Any] = {
            "service": "control",
            "op": "cost_budget_configure",
            "container_id": args.container_id,
            "alert_threshold_pct": args.alert_threshold_pct,
        }
        if args.daily_limit is not None:
            payload["daily_limit"] = args.daily_limit
        if args.weekly_limit is not None:
            payload["weekly_limit"] = args.weekly_limit
        if args.monthly_limit is not None:
            payload["monthly_limit"] = args.monthly_limit
        if args.hard_limit is not None:
            payload["hard_limit"] = args.hard_limit
        return payload
    if command == "cost-budget-check":
        return {
            "service": "control",
            "op": "cost_budget_check",
            "container_id": args.container_id,
        }
    if command == "cost-alerts":
        p6: Dict[str, Any] = {
            "service": "control",
            "op": "cost_alerts",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            p6["tail"] = args.tail
        return p6
    if command == "cost-budget-config":
        return {
            "service": "control",
            "op": "cost_budget_config",
            "container_id": args.container_id,
        }
    if command == "autoscale-configure":
        payload: Dict[str, Any] = {
            "service": "control",
            "op": "autoscale_configure",
            "container_id": args.container_id,
            "enabled": args.enabled,
        }
        if args.min_memory_mb is not None:
            payload["min_memory_mb"] = args.min_memory_mb
        if args.max_memory_mb is not None:
            payload["max_memory_mb"] = args.max_memory_mb
        if args.target_memory_pct != 70.0:
            payload["target_memory_pct"] = args.target_memory_pct
        if args.min_cpu_quota is not None:
            payload["min_cpu_quota"] = args.min_cpu_quota
        if args.max_cpu_quota is not None:
            payload["max_cpu_quota"] = args.max_cpu_quota
        if args.target_cpu_pct != 70.0:
            payload["target_cpu_pct"] = args.target_cpu_pct
        if args.scale_up_cooldown_s != 300.0:
            payload["scale_up_cooldown_s"] = args.scale_up_cooldown_s
        if args.scale_down_cooldown_s != 600.0:
            payload["scale_down_cooldown_s"] = args.scale_down_cooldown_s
        return payload
    if command == "autoscale-status":
        return {
            "service": "control",
            "op": "autoscale_status",
            "container_id": args.container_id,
        }
    if command == "autoscale-apply":
        return {
            "service": "control",
            "op": "autoscale_apply",
            "container_id": args.container_id,
        }
    if command == "autoscale-disable":
        return {
            "service": "control",
            "op": "autoscale_disable",
            "container_id": args.container_id,
        }
    if command == "autoscale-events":
        p6: Dict[str, Any] = {
            "service": "control",
            "op": "autoscale_events",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            p6["tail"] = args.tail
        return p6
    if command == "health-configure":
        payload: Dict[str, Any] = {
            "service": "control",
            "op": "health_configure",
            "container_id": args.container_id,
            "auto_restart": args.auto_restart,
            "max_auto_restarts": args.max_auto_restarts,
        }
        if args.cmd is not None:
            payload["cmd"] = args.cmd
        if args.interval is not None:
            payload["interval"] = args.interval
        if args.timeout is not None:
            payload["timeout"] = args.timeout
        if args.retries is not None:
            payload["retries"] = args.retries
        return payload
    if command == "health-trigger":
        return {
            "service": "control",
            "op": "health_trigger",
            "container_id": args.container_id,
        }
    if command == "health-config":
        return {
            "service": "control",
            "op": "health_config",
            "container_id": args.container_id,
        }
    if command == "health-restart-reset":
        return {
            "service": "control",
            "op": "health_restart_reset",
            "container_id": args.container_id,
        }
    if command == "health-restart-history":
        p7: Dict[str, Any] = {
            "service": "control",
            "op": "health_restart_history",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            p7["tail"] = args.tail
        return p7
    if command == "forecast-resource":
        return {
            "service": "control",
            "op": "forecast",
            "container_id": args.container_id,
            "resource": args.resource,
            "horizon_s": args.horizon_s,
        }
    if command == "forecast-all":
        return {
            "service": "control",
            "op": "forecast_all",
            "container_id": args.container_id,
        }
    if command == "forecast-exhaustion":
        return {
            "service": "control",
            "op": "time_to_exhaustion",
            "container_id": args.container_id,
            "resource": args.resource,
        }
    if command == "capacity-plan":
        return {
            "service": "control",
            "op": "capacity_plan",
            "container_id": args.container_id,
            "horizon_days": args.horizon_days,
        }
    if command == "capacity-plan-all":
        return {
            "service": "control",
            "op": "capacity_plan_all",
            "horizon_days": args.horizon_days,
        }
    if command == "network-traffic":
        return {
            "service": "control",
            "op": "network_traffic",
            "container_id": args.container_id,
            "window_s": args.window_s,
        }
    if command == "network-connections":
        return {
            "service": "control",
            "op": "network_connections",
            "container_id": args.container_id,
        }
    if command == "network-bandwidth-history":
        p8: Dict[str, Any] = {
            "service": "control",
            "op": "network_bandwidth_history",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            p8["tail"] = args.tail
        return p8
    if command == "anomaly-detect":
        return {
            "service": "control",
            "op": "anomaly_detect",
            "container_id": args.container_id,
            "resource": args.resource,
            "window_size": args.window,
            "sensitivity": args.sensitivity,
        }
    if command == "anomaly-detect-all":
        return {
            "service": "control",
            "op": "anomaly_detect_all",
            "container_id": args.container_id,
            "window_size": args.window,
            "sensitivity": args.sensitivity,
        }
    if command == "anomaly-spike":
        return {
            "service": "control",
            "op": "anomaly_spike",
            "container_id": args.container_id,
            "resource": args.resource,
            "threshold_pct": args.threshold,
        }
    if command == "anomaly-trend":
        return {
            "service": "control",
            "op": "anomaly_trend",
            "container_id": args.container_id,
            "resource": args.resource,
            "window_size": args.window,
        }
    if command == "compare-resources":
        return {
            "service": "control",
            "op": "compare",
            "container_ids": args.container_ids,
            "resource": args.resource,
        }
    if command == "compare-all":
        return {
            "service": "control",
            "op": "compare_all",
            "container_ids": args.container_ids,
        }
    if command == "compare-relative":
        return {
            "service": "control",
            "op": "relative_usage",
            "container_id": args.container_id,
            "resource": args.resource,
        }
    if command == "compare-top":
        return {
            "service": "control",
            "op": "top_consumers",
            "resource": args.resource,
            "top_n": args.top,
        }
    if command == "recommend-get":
        return {
            "service": "control",
            "op": "recommendations",
            "container_id": args.container_id,
        }
    if command == "recommend-all":
        return {
            "service": "control",
            "op": "recommendations_all",
        }
    if command == "recommend-category":
        return {
            "service": "control",
            "op": "recommendations_category",
            "container_id": args.container_id,
            "category": args.category,
        }
    if command in NUI_COMMANDS:
        payload = {
            "service": "nui",
            "op": command.replace("-", "_"),
        }
        if command != "nui-current":
            # validate/load carry the document; current is a query.
            payload["document"] = getattr(args, "document", "")
        return payload
    if command == "vault-volume-create":
        return {"service": "storage", "op": "volume_create",
                "name": args.name}
    if command == "vault-volume-open":
        if args.name:
            return {"service": "storage", "op": "volume_open",
                    "name": args.name}
        return {"service": "storage", "op": "volume_open",
                "volume_id": args.volume_id}
    if command == "vault-volume-list":
        return {"service": "storage", "op": "volume_list"}
    if command == "vault-volume-grant":
        payload = _volume_ref(
            {"service": "storage", "op": "volume_grant",
             "container": args.container}, args)
        if args.path:
            payload["path"] = args.path
        return payload
    if command == "vault-volume-revoke":
        return _volume_ref({"service": "storage", "op": "volume_revoke",
                            "container": args.container}, args)
    if command == "vault-volume-grants":
        return _volume_ref({"service": "storage", "op": "volume_grants"},
                           args)
    if command == "vault-volume-close":
        return {"service": "storage", "op": "volume_close",
                "handle": args.handle}
    if command == "vault-volume-write":
        return {
            "service": "storage", "op": "volume_write",
            "handle": args.handle, "path": args.path,
            "offset": args.offset,
            "data_b64": base64.b64encode(args.data).decode("ascii"),
        }
    if command == "vault-volume-read":
        return {
            "service": "storage", "op": "volume_read",
            "handle": args.handle, "path": args.path,
            "offset": args.offset, "size": args.size,
        }
    if command == "vault-volume-snapshot":
        return {"service": "storage", "op": "volume_snapshot",
                "handle": args.handle, "name": args.name}
    if command == "vault-volume-snapshots":
        return {"service": "storage", "op": "volume_snapshots",
                "handle": args.handle}
    if command == "vault-volume-restore":
        return {"service": "storage", "op": "volume_restore",
                "handle": args.handle, "name": args.name}
    if command == "vault-volume-snapshot-delete":
        return {"service": "storage", "op": "volume_snapshot_delete",
                "handle": args.handle, "name": args.name}
    if command == "vault-volume-delete":
        if args.name:
            return {"service": "storage", "op": "volume_delete",
                    "name": args.name}
        return {"service": "storage", "op": "volume_delete",
                "volume_id": args.volume_id}
    if command == "vault-volume-rekey":
        return {"service": "storage", "op": "volume_rekey",
                "new_passphrase": args.new_passphrase}
    if command == "vault-volume-quota-set":
        payload = _volume_ref({
            "service": "storage", "op": "volume_quota_set",
            "container": args.container,
            "bytes": None if args.unlimited else args.bytes,
        }, args)
        if args.path:
            payload["path"] = args.path
        return payload
    if command == "vault-volume-quota-get":
        return _volume_ref({"service": "storage", "op": "volume_quota_get"},
                           args)
    if command == "vault-volume-usage":
        return _volume_ref({"service": "storage", "op": "volume_usage"},
                           args)
    if command == "vault-volume-summary":
        return {"service": "storage", "op": "volume_summary"}
    if command == "vault-volume-events":
        return {"service": "storage", "op": "volume_events"}
    if command == "app-install":
        return {
            "service": "control",
            "op": "app_install",
            "app_path": args.app_path,
            "name": args.name or None,
            "sandbox": bool(args.sandbox),
        }
    if command == "app-list":
        return {"service": "control", "op": "app_list"}
    if command == "app-launch":
        return {
            "service": "control",
            "op": "app_launch",
            "app_id": args.app_id,
        }
    if command == "app-terminate":
        return {
            "service": "control",
            "op": "app_terminate",
            "app_id": args.app_id,
        }
    if command == "resource-profile":
        return {
            "service": "control",
            "op": "resource_profile",
            "container_id": args.container_id,
        }
    if command == "resource-profile-history":
        payload = {
            "service": "control",
            "op": "resource_profile_history",
            "container_id": args.container_id,
        }
        if args.tail is not None:
            payload["tail"] = args.tail
        return payload
    if command == "resource-profile-top":
        return {
            "service": "control",
            "op": "resource_profile_top",
            "container_id": args.container_id,
            "resource": args.resource,
            "top_n": args.top_n,
        }
    if command == "batch-start":
        payload: Dict[str, Any] = {
            "service": "control",
            "op": "batch_start",
        }
        if args.labels:
            payload["labels"] = dict(
                item.split("=", 1)
                for item in args.labels.split(",")
                if "=" in item
            )
        if args.name_pattern:
            payload["name_pattern"] = args.name_pattern
        if args.container_ids:
            payload["container_ids"] = args.container_ids.split(",")
        return payload
    if command == "batch-stop":
        payload = {
            "service": "control",
            "op": "batch_stop",
            "timeout_s": args.timeout,
        }
        if args.labels:
            payload["labels"] = dict(
                item.split("=", 1)
                for item in args.labels.split(",")
                if "=" in item
            )
        if args.name_pattern:
            payload["name_pattern"] = args.name_pattern
        if args.container_ids:
            payload["container_ids"] = args.container_ids.split(",")
        return payload
    if command == "batch-kill":
        payload = {
            "service": "control",
            "op": "batch_kill",
        }
        if args.labels:
            payload["labels"] = dict(
                item.split("=", 1)
                for item in args.labels.split(",")
                if "=" in item
            )
        if args.name_pattern:
            payload["name_pattern"] = args.name_pattern
        if args.container_ids:
            payload["container_ids"] = args.container_ids.split(",")
        return payload
    raise ValueError(f"unknown command: {command!r}")


# -- human formatting (pure, unit-testable) ----------------------------


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count (B, KiB, MiB, GiB)."""
    if n < 1024:
        return f"{n} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        n /= 1024
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PiB"


def format_human(command: str, resp: Dict[str, Any]) -> str:
    """Render a successful reply (``ok: true``) for the operator."""
    if command in NUI_COMMANDS:
        if command == "nui-current" and not resp.get("loaded"):
            return "no shell design loaded (run `nyrqisctl nui load`)"
        summary = resp.get("summary") or {}
        lines = [
            f"engine:      {summary.get('engine')}",
            f"schema:      {summary.get('version')}",
            f"screens:     {', '.join(summary.get('screens') or [])}",
            f"components:  {summary.get('components')}",
            f"behaviors:   {summary.get('behaviors')}",
            f"bindings:    {summary.get('bindings')}",
        ]
        if resp.get("path"):
            lines.append(f"stored:      {resp['path']}")
        if resp.get("loaded") is True and resp.get("valid") is False:
            lines.append(f"stale:       {resp.get('error')}")
        return "\n".join(lines)
    if command == "ping":
        return (
            f"pong (caller={resp.get('container')}, "
            f"service={resp.get('service')} v{resp.get('service_version')})"
        )
    if command == "status":
        caps = resp.get("capabilities") or []
        lines = [
            f"backend:      {resp.get('backend_version')}",
            f"service:      {resp.get('service')} v{resp.get('service_version')}",
            f"uptime:       {resp.get('uptime_s')}s",
            f"caller:       {resp.get('container')}",
            f"capabilities: {', '.join(caps) if caps else '(none)'}",
        ]
        vault = resp.get("vault")
        if isinstance(vault, dict):
            lines.append(
                f"vault:        {vault.get('volumes')} volume(s), "
                f"{vault.get('logical_bytes')} logical / "
                f"{vault.get('physical_bytes')} physical bytes, "
                f"{vault.get('warned_containers')} warned")
        return "\n".join(lines)
    if command == "health":
        lines = [
            f"backend:        {resp.get('backend_version')}",
            f"service:        {resp.get('service')} v{resp.get('service_version')}",
            f"uptime:         {resp.get('uptime_s')}s",
            f"serve loop:     "
            f"{'alive' if resp.get('serve_loop_alive') else 'DEAD'}",
        ]
        containers = resp.get("containers")
        if isinstance(containers, dict):
            lines.append(
                f"containers:     {containers.get('known', 0)} known, "
                f"{containers.get('running', 0)} running"
            )
        registry = resp.get("ipc_registry_entries")
        if registry is not None:
            lines.append(f"ipc registry:   {registry} entries")
        if resp.get("state_persisted") is not None:
            lines.append(
                f"state:          "
                f"{'persisted' if resp.get('state_persisted') else 'not persisted'}"
            )
        recovery = resp.get("recovery")
        if recovery:
            lines.append(
                f"recovery:       previous pid {recovery.get('previous_pid')} "
                f"with {recovery.get('containers_left')} containers left"
            )
        else:
            lines.append("recovery:       none")
        vault = resp.get("vault")
        if isinstance(vault, dict):
            lines.append(
                f"vault:          {vault.get('volumes')} volume(s), "
                f"{vault.get('logical_bytes')} logical / "
                f"{vault.get('physical_bytes')} physical bytes, "
                f"{vault.get('warned_containers')} warned")
        return "\n".join(lines)
    if command == "containers-list":
        containers: List[Dict[str, Any]] = resp.get("containers") or []
        if not containers:
            return "no containers"
        rows = [f"{c.get('id')}\t{c.get('state')}\t{c.get('pid')}"
                for c in containers]
        return "\n".join(["id\tstate\tpid"] + rows)
    if command == "containers-run":
        return (
            f"container {resp.get('container_id')} started "
            f"(pid {resp.get('pid')})"
        )
    if command == "containers-kill":
        return f"container {resp.get('container_id')} terminated"
    if command == "containers-logs":
        if not resp.get("available"):
            return f"container {resp.get('container_id')}: log capture not active (set log_capture=True at creation)"
        parts = []
        for stream_name in ("stdout", "stderr"):
            lines = resp.get(stream_name) or []
            if lines:
                parts.append(f"--- {stream_name} ---")
                parts.extend(lines)
        if not parts:
            return f"container {resp.get('container_id')}: no log output yet"
        return "\n".join(parts)
    if command == "containers-exec":
        exit_code = resp.get("exit_code", -1)
        stdout = resp.get("stdout", "")
        stderr = resp.get("stderr", "")
        parts = []
        if stdout:
            parts.append(stdout.rstrip())
        if stderr:
            parts.append(f"[stderr] {stderr.rstrip()}")
        parts.append(f"exit code: {exit_code}")
        return "\n".join(parts)
    if command == "containers-top":
        # Check if this is a summary response
        if "total_processes" in resp:
            return (
                f"container: {resp.get('container_id')}\n"
                f"processes: {resp.get('total_processes', 0)}\n"
                f"threads:   {resp.get('total_threads', 0)}\n"
                f"rss:       {resp.get('total_rss_kb', 0):,} KB\n"
                f"vsize:     {resp.get('total_vsize_kb', 0):,} KB\n"
                f"cpu:       {resp.get('total_cpu_s', 0):.3f}s\n"
                f"states:    {resp.get('states', {})}"
            )
        procs = resp.get("processes") or []
        if not procs:
            return f"container {resp.get('container_id')}: no processes found"
        rows = []
        for p in procs:
            depth = p.get("depth", 0)
            indent = "  " * depth
            rows.append(
                f"{p.get('pid'):>8} {p.get('ppid'):>8} {p.get('state'):>1} "
                f"{p.get('threads'):>3} {p.get('nice'):>4} "
                f"{p.get('user_time_s', 0):>8.3f}s "
                f"{p.get('system_time_s', 0):>8.3f}s "
                f"{p.get('rss_kb', 0):>8} KB "
                f"{p.get('fd_count', 0):>4}fd "
                f"{indent}{p.get('name', '')}"
            )
        header = (
            f"{'PID':>8} {'PPID':>8} {'S':>1} {'THR':>3} {'NI':>4} "
            f"{'USER':>8} {'SYS':>8} {'RSS':>8} {'FD':>4}  NAME"
        )
        return header + "\n" + "\n".join(rows)
    if command == "containers-net":
        stats = resp.get("stats")
        if not stats:
            return f"container {resp.get('container_id')}: no network interface found"
        lines = [
            f"interface:  {stats.get('interface')}",
            f"state:      {stats.get('operstate', '?')}",
            f"mtu:        {stats.get('mtu', '?')}",
            f"RX bytes:   {stats.get('rx_bytes', 0):,}",
            f"RX packets: {stats.get('rx_packets', 0):,}",
            f"RX errors:  {stats.get('rx_errors', 0)}",
            f"RX dropped: {stats.get('rx_dropped', 0)}",
            f"TX bytes:   {stats.get('tx_bytes', 0):,}",
            f"TX packets: {stats.get('tx_packets', 0):,}",
            f"TX errors:  {stats.get('tx_errors', 0)}",
            f"TX dropped: {stats.get('tx_dropped', 0)}",
        ]
        return "\n".join(lines)
    if command == "quotas-set":
        q = resp.get("quota") or {}
        return (f"quota set for {resp.get('owner')}: "
                + ", ".join(f"{k}={v}" for k, v in q.items()))
    if command == "quotas-get":
        q = resp.get("quota")
        if q is None:
            return f"no quota for {resp.get('owner')}"
        return (f"quota for {resp.get('owner')}: "
                + ", ".join(f"{k}={v}" for k, v in q.items()))
    if command == "quotas-list":
        quotas = resp.get("quotas") or {}
        if not quotas:
            return "no quotas set"
        rows = []
        for owner, q in quotas.items():
            parts = [f"{k}={v}" for k, v in q.items()]
            rows.append(f"{owner:<20} {' '.join(parts)}")
        header = f"{'OWNER':<20} LIMITS"
        return header + "\n" + "\n".join(rows)
    if command == "quotas-delete":
        ok = resp.get("ok")
        if ok:
            return f"quota deleted for {resp.get('owner')}"
        return f"no quota found for {resp.get('owner')}"
    if command == "quotas-usage":
        lines = [
            f"owner:       {resp.get('owner')}",
            f"containers:  {resp.get('containers', 0)}"
            + (f"/{resp['max_containers']}" if resp.get('max_containers') else ""),
            f"memory:      {resp.get('memory_used_mb', 0)} MiB"
            + (f"/{resp['memory_limit_mb']} MiB" if resp.get('memory_limit_mb') else ""),
            f"pids:        {resp.get('pid_used', 0)}"
            + (f"/{resp['pid_limit']}" if resp.get('pid_limit') else ""),
        ]
        return "\n".join(lines)
    if command == "images-list":
        images = resp.get("images") or []
        if not images:
            return "no images found"
        rows = []
        for img in images:
            rows.append(
                f"{img.get('name', ''):<20} "
                f"{img.get('inode_count', 0):>6} inodes  "
                f"{img.get('block_count', 0):>6} blocks  "
                f"{img.get('size_bytes', 0):>12,} bytes"
            )
        header = f"{'NAME':<20} {'INODES':>6} {'BLOCKS':>6} {'SIZE':>12}"
        return header + "\n" + "\n".join(rows)
    if command == "images-remove":
        return f"image removed: {resp.get('path')}"
    if command == "images-export":
        size = resp.get("size_bytes", 0)
        return (f"image exported: {resp.get('tar_path')} "
                f"({size:,} bytes)")
    if command == "images-import":
        return f"image imported: {resp.get('image_path')}"
    if command == "containers-checkpoint":
        return (
            f"checkpoint saved: {resp.get('checkpoint_path')} "
            f"({resp.get('overlay_entries', 0)} overlay entries)"
        )
    if command == "containers-restore":
        return (
            f"container {resp.get('container_id')} restored "
            f"(state={resp.get('state')})"
        )
    if command == "containers-diff":
        parts = []
        added = resp.get("added") or []
        removed = resp.get("removed") or []
        modified = resp.get("modified") or []
        cfg = resp.get("config_changes") or {}
        if added:
            parts.append(f"Added ({len(added)}):")
            for p in added[:10]:
                parts.append(f"  + {p}")
            if len(added) > 10:
                parts.append(f"  ... and {len(added) - 10} more")
        if removed:
            parts.append(f"Removed ({len(removed)}):")
            for p in removed[:10]:
                parts.append(f"  - {p}")
            if len(removed) > 10:
                parts.append(f"  ... and {len(removed) - 10} more")
        if modified:
            parts.append(f"Modified ({len(modified)}):")
            for m in modified[:10]:
                parts.append(
                    f"  ~ {m['path']} ({', '.join(m['changes'])})"
                )
            if len(modified) > 10:
                parts.append(f"  ... and {len(modified) - 10} more")
        if cfg:
            parts.append("Config changes:")
            for key, change in cfg.items():
                parts.append(
                    f"  {key}: {change.get('from')} → {change.get('to')}"
                )
        if not parts:
            return resp.get("summary", "no differences")
        return "\n".join(parts)
    if command == "containers-events":
        events = resp.get("events") or []
        if not events:
            return "no events"
        rows = []
        for ev in events:
            import datetime as _dt
            ts = ev.get("time", 0)
            tstr = _dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            rows.append(
                f"{tstr}  {ev.get('kind', ''):<12} "
                f"{ev.get('container_id', ''):<24} "
                f"{ev.get('detail', '')}"
            )
        header = f"{'TIME':>8}  {'KIND':<12} {'CONTAINER':<24} DETAIL"
        return header + "\n" + "\n".join(rows)
    if command == "containers-health":
        status = resp.get("status", "unknown")
        lines = [
            f"container:  {resp.get('container_id')}",
            f"status:     {status}",
            f"failures:   {resp.get('failures', 0)}",
            f"check_cmd:  {' '.join(resp.get('check_cmd') or []) or '(none)'}",
        ]
        last = resp.get("last_check")
        if last:
            import datetime as _dt
            tstr = _dt.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"last_check: {tstr}")
        output = resp.get("last_output", "")
        if output:
            lines.append(f"last_output: {output[:200]}")
        return "\n".join(lines)
    if command == "containers-limits":
        if not resp.get("available"):
            return f"container {resp.get('container_id')}: stats not available"
        mem_pct = resp.get("memory_pct")
        pid_pct = resp.get("pid_pct")
        mem_alert = resp.get("memory_alert", "ok")
        pid_alert = resp.get("pid_alert", "ok")
        alert_icons = {
            "ok": "✓", "warning": "⚠", "critical": "🔴",
            "at_limit": "⛔",
        }
        lines = [
            f"container:  {resp.get('container_id')}",
            f"memory:    {mem_pct}% {alert_icons.get(mem_alert, '?')} ({mem_alert})" if mem_pct is not None else "memory:    unlimited",
            f"pids:      {pid_pct}% {alert_icons.get(pid_alert, '?')} ({pid_alert})" if pid_pct is not None else "pids:      unlimited",
        ]
        return "\n".join(lines)
    if command == "containers-sched":
        nice = resp.get("nice_value_current", resp.get("nice_value", 0))
        affinity = resp.get("cpu_affinity_current")
        config_aff = resp.get("cpu_affinity_config")
        cpu_count = resp.get("cpu_count", "?")
        lines = [
            f"container:      {resp.get('container_id')}",
            f"nice:          {nice}",
            f"cpu_affinity:  {affinity if affinity is not None else f'all ({cpu_count} cores)'}",
        ]
        if config_aff:
            lines.append(f"affinity_conf: {config_aff}")
        return "\n".join(lines)
    if command == "containers-netpolicy":
        policy = resp.get("policy")
        if not policy:
            return f"container {resp.get('container_id')}: no network policy (no veth interface)"
        lines = [
            f"interface:  {policy.get('interface')}",
        ]
        ingress = policy.get("ingress_rules") or []
        egress = policy.get("egress_rules") or []
        if ingress:
            lines.append("ingress:")
            for r in ingress:
                lines.append(f"  {r}")
        else:
            lines.append("ingress: (none)")
        if egress:
            lines.append("egress:")
            for r in egress:
                lines.append(f"  {r}")
        else:
            lines.append("egress: (none)")
        return "\n".join(lines)
    if command in ("containers-start-ordered", "containers-stop-ordered"):
        results = resp.get("results", [])
        if not results:
            return "no results"
        lines = []
        for r in results:
            err = r.get("error")
            if err:
                lines.append(f"{r['id']}: ERROR {err}")
            else:
                lines.append(f"{r['id']}: exit_code={r.get('exit_code')}")
        return "\n".join(lines)
    if command == "containers-dep-graph":
        graph = resp.get("graph", {})
        if not graph:
            return "(empty)"
        lines = []
        for cid, info in sorted(graph.items()):
            deps = info.get("depends_on", [])
            dependents = info.get("dependents", [])
            state = info.get("state", "?")
            lines.append(f"{cid} [{state}]")
            if deps:
                lines.append(f"  depends_on: {', '.join(deps)}")
            if dependents:
                lines.append(f"  dependents: {', '.join(dependents)}")
        return "\n".join(lines)
    if command in ("containers-restart-info", "containers-restart-set"):
        policy = resp.get("restart_policy", "?")
        count = resp.get("restart_count", 0)
        max_r = resp.get("restart_max_retries", 0)
        delay = resp.get("restart_delay", 1.0)
        return (
            f"policy:        {policy}\n"
            f"restart_count: {count}\n"
            f"max_retries:   {max_r} (0=unlimited)\n"
            f"delay:         {delay}s"
        )
    if command == "containers-env-list":
        env = resp.get("environment", {})
        if not env:
            return f"container {resp.get('container_id')}: (no environment variables)"
        lines = [f"container: {resp.get('container_id')}"]
        for k, v in sorted(env.items()):
            lines.append(f"  {k}={v}")
        return "\n".join(lines)
    if command in ("containers-env-set", "containers-env-unset"):
        if command == "containers-env-unset" and not resp.get("existed", True):
            return f"variable {resp.get('key')!r} was not set"
        return f"{resp.get('key')} set on container {resp.get('container_id')}"
    if command == "containers-snapshot-export":
        return (
            f"container: {resp.get('container_id')}\n"
            f"archive:  {resp.get('export_path')}\n"
            f"size:     {resp.get('archive_size', 0):,} bytes\n"
            f"entries:  {resp.get('overlay_entries', 0)}\n"
            f"blobs:    {resp.get('blob_count', 0)}"
        )
    if command == "containers-snapshot-import":
        return (
            f"imported container {resp.get('container_id')}\n"
            f"state: {resp.get('state')}"
        )
    if command == "containers-resource-history":
        history = resp.get("history", [])
        count = resp.get("count", 0)
        lines = [f"container: {resp.get('container_id')} ({count} samples)"]
        for s in history:
            ts = s.get("timestamp", 0)
            mem = s.get("memory_bytes", 0)
            cpu = s.get("cpu_usage_usec", 0)
            pids = s.get("pids_current", 0)
            lines.append(
                f"  {ts:.1f}  mem={mem:,}B  cpu={cpu:,}us  pids={pids}"
            )
        return "\n".join(lines)
    if command == "containers-resource-record":
        sample = resp.get("sample")
        if sample is None:
            return f"container {resp.get('container_id')}: no sample (not running?)"
        return (
            f"container: {resp.get('container_id')}\n"
            f"memory:  {sample.get('memory_bytes', 0):,} bytes\n"
            f"cpu:     {sample.get('cpu_usage_usec', 0):,} us\n"
            f"pids:    {sample.get('pids_current', 0)}"
        )
    if command == "containers-resource-record-start":
        return (
            f"started recording for {resp.get('container_id')} "
            f"(interval={resp.get('interval', 5.0)}s)"
        )
    if command == "containers-resource-record-stop":
        return f"stopped recording for {resp.get('container_id')}"
    if command == "containers-update-limits":
        updated = resp.get("updated", [])
        prev = resp.get("previous", {})
        lines = [f"container: {resp.get('container_id')}"]
        if not updated:
            lines.append("no limits changed")
        else:
            for key in updated:
                old = prev.get(key)
                lines.append(f"  {key}: {old} → (updated)")
        return "\n".join(lines)
    if command == "containers-label-list":
        labels = resp.get("labels", {})
        if not labels:
            return f"container {resp.get('container_id')}: (no labels)"
        lines = [f"container: {resp.get('container_id')}"]
        for k, v in sorted(labels.items()):
            lines.append(f"  {k}={v}")
        return "\n".join(lines)
    if command in ("containers-label-set", "containers-label-unset"):
        if command == "containers-label-unset" and not resp.get("existed", True):
            return f"label {resp.get('key')!r} was not set"
        return f"{resp.get('key')}={resp.get('value', '')} on container {resp.get('container_id')}"
    if command == "containers-label-filter":
        containers = resp.get("containers", [])
        count = resp.get("count", 0)
        lines = [f"{count} container(s) matched:"]
        for c in containers:
            lines.append(f"  {c['id']} [{c['state']}]")
        return "\n".join(lines)
    if command == "containers-cgroup2-status":
        if not resp.get("available"):
            return f"container {resp.get('container_id')}: no cgroup status"
        lines = [f"container: {resp.get('container_id')}"]
        for key in sorted(resp.keys()):
            if key in ("ok", "container_id", "available"):
                continue
            val = resp[key]
            if val is not None:
                lines.append(f"  {key}: {val}")
        return "\n".join(lines)
    if command == "containers-verify-enforcement":
        enforced = resp.get("enforced", False)
        violations = resp.get("violations", [])
        warnings = resp.get("warnings", [])
        lines = [
            f"container: {resp.get('container_id')}",
            f"enforced: {'yes' if enforced else 'NO'}",
        ]
        if violations:
            lines.append("violations:")
            for v in violations:
                lines.append(f"  ❌ {v}")
        if warnings:
            lines.append("warnings:")
            for w in warnings:
                lines.append(f"  ⚠ {w}")
        if not violations and not warnings:
            lines.append("  all limits OK")
        return "\n".join(lines)
    if command == "containers-lock":
        acquired = resp.get("acquired", False)
        if acquired:
            return f"lock acquired for {resp.get('container_id')}"
        return f"lock NOT acquired for {resp.get('container_id')}"
    if command == "containers-unlock":
        return f"lock released for {resp.get('container_id')}"
    if command == "containers-locks":
        locks = resp.get("locks", [])
        count = resp.get("count", 0)
        if not locks:
            return "no locks held"
        lines = [f"{count} lock(s) held:"]
        for lk in locks:
            lines.append(
                f"  {lk['container_id']} (fd={lk['fd']}, "
                f"file={lk['lock_file']})"
            )
        return "\n".join(lines)
    if command == "containers-alert-history":
        alerts = resp.get("alerts", [])
        count = resp.get("count", 0)
        lines = [f"container: {resp.get('container_id')} ({count} alerts)"]
        for a in alerts:
            ts = a.get("timestamp", 0)
            lines.append(
                f"  {ts:.1f}  {a.get('resource')}="
                f"{a.get('level')}  {a.get('detail', '')}"
            )
        return "\n".join(lines)
    if command == "containers-alert-clear":
        return (
            f"cleared {resp.get('cleared', 0)} alerts "
            f"for {resp.get('container_id')}"
        )
    if command == "containers-alert-thresholds":
        lines = [f"container: {resp.get('container_id')}"]
        for key in sorted(resp.keys()):
            if key.startswith("alert_"):
                lines.append(f"  {key}: {resp[key]}%")
        return "\n".join(lines)
    if command == "containers-oom-status":
        lines = [
            f"container:       {resp.get('container_id')}",
            f"score_adj:       {resp.get('oom_score_adj', 0)}",
            f"kill_disable:    {resp.get('oom_kill_disable', False)}",
            f"swap_max:        {resp.get('memory_swap_max')}",
            f"oom_group:       {resp.get('oom_group')}",
            f"cgroup_swap_max: {resp.get('cgroup_swap_max')}",
            f"events:          {resp.get('oom_event_count', 0)}",
        ]
        return "\n".join(lines)
    if command == "containers-oom-set":
        lines = [f"container: {resp.get('container_id')}"]
        lines.append(f"  score_adj:    {resp.get('oom_score_adj', 0)}")
        lines.append(f"  kill_disable: {resp.get('oom_kill_disable', False)}")
        lines.append(f"  swap_max:     {resp.get('memory_swap_max')}")
        return "\n".join(lines)
    if command == "containers-oom-events":
        events = resp.get("events", [])
        count = resp.get("count", 0)
        lines = [f"container: {resp.get('container_id')} ({count} events)"]
        for e in events:
            ts = e.get("timestamp", 0)
            lines.append(f"  {ts:.1f}  {e.get('detail', '')}")
        return "\n".join(lines)
    if command == "containers-alert-acknowledge":
        alert = resp.get("alert", {})
        if not alert:
            return f"error: {resp.get('error', 'unknown')}"
        return (
            f"container:    {alert.get('container_id')}\n"
            f"acknowledged: {alert.get('acknowledged')}\n"
            f"by:           {alert.get('acknowledged_by')}\n"
            f"resource:     {alert.get('resource')}\n"
            f"level:        {alert.get('level')}\n"
            f"detail:       {alert.get('detail')}"
        )
    if command == "containers-alert-suppress":
        return (
            f"container: {resp.get('container_id')}\n"
            f"resource: {resp.get('resource')}\n"
            f"level:    {resp.get('level', 'all')}\n"
            f"duration: {resp.get('duration_s', 0):.0f}s\n"
            f"expires:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(resp.get('expires_at', 0)))}"
        )
    if command == "containers-alert-unsuppress":
        removed = resp.get("removed", False)
        return f"removed: {'yes' if removed else 'no'}"
    if command == "containers-alert-statistics":
        lines = [
            f"container:     {resp.get('container_id')}",
            f"total alerts: {resp.get('total_alerts', 0)}",
            f"acknowledged: {resp.get('acknowledged_count', 0)}",
            f"unack'd:      {resp.get('unacknowledged_count', 0)}",
            "by level:"
        ]
        for level, count in resp.get("by_level", {}).items():
            lines.append(f"  {level}: {count}")
        lines.append("by resource:")
        for res, count in resp.get("by_resource", {}).items():
            lines.append(f"  {res}: {count}")
        dist = resp.get("time_distribution", {})
        lines.append("time distribution:")
        lines.append(f"  last 1h:  {dist.get('last_1h', 0)}")
        lines.append(f"  last 6h:  {dist.get('last_6h', 0)}")
        lines.append(f"  last 24h: {dist.get('last_24h', 0)}")
        return "\n".join(lines)
    if command == "containers-alert-suppressions":
        suppressions = resp.get("suppressions", [])
        if not suppressions:
            return "No active suppressions."
        lines = [f"Active suppressions ({len(suppressions)}):"]
        for s in suppressions:
            expires = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.get("expires_at", 0)))
            lines.append(
                f"  {s.get('resource')} "
                f"level={s.get('level', 'all')} "
                f"expires={expires}"
            )
        return "\n".join(lines)
    if command == "containers-dashboard":
        # Check if this is an all-containers dashboard
        if "total_containers" in resp:
            lines = [
                f"total containers: {resp.get('total_containers', 0)}",
                f"by state: {resp.get('by_state', {})}",
                f"total memory: {resp.get('total_memory_bytes', 0):,} bytes",
                f"total pids: {resp.get('total_pids', 0)}",
                "",
            ]
            for c in resp.get("containers", []):
                mem = c.get("memory_bytes")
                mem_str = f"{mem:,}B" if mem else "?"
                lines.append(
                    f"  {c['id']} [{c['state']}] "
                    f"mem={mem_str} pids={c.get('pids_current', '?')} "
                    f"labels={c.get('labels', {})}"
                )
            return "\n".join(lines)
        # Single container dashboard
        lines = [
            f"=== {resp.get('container_id')} [{resp.get('state')}] ===",
            f"pid: {resp.get('pid')}",
            f"uptime: {resp.get('uptime_s')}s",
            "",
            "--- Resources ---",
            f"memory: {resp.get('memory_pct', '?')}% ({resp.get('memory_alert', 'ok')})",
            f"pids: {resp.get('pid_pct', '?')}% ({resp.get('pid_alert', 'ok')})",
            f"cpu throttle: {resp.get('cpu_throttle_pct', '?')}% ({resp.get('cpu_throttle_alert', 'ok')})",
            "",
            "--- Processes ---",
        ]
        procs = resp.get("processes", {})
        lines.append(f"  total: {procs.get('total_processes', 0)} processes, {procs.get('total_threads', 0)} threads")
        lines.append(f"  rss: {procs.get('total_rss_kb', 0):,} KB")
        lines.append(f"  cpu: {procs.get('total_cpu_s', 0):.3f}s")
        lines.append("")
        lines.append("--- OOM ---")
        oom = resp.get("oom", {})
        lines.append(f"  score_adj: {oom.get('score_adj', 0)}, kill_disable: {oom.get('kill_disable', False)}")
        lines.append(f"  events: {oom.get('event_count', 0)}")
        lines.append("")
        labels = resp.get("labels", {})
        if labels:
            lines.append(f"--- Labels: {labels} ---")
        return "\n".join(lines)
    if command in ("containers-export-history", "containers-export-snapshot"):
        return (
            f"container: {resp.get('container_id')}\n"
            f"path:     {resp.get('path')}\n"
            f"bytes:    {resp.get('bytes_written', 0):,}\n"
            f"samples:  {resp.get('samples', 'N/A')}"
        )
    if command == "webhook-register":
        return (
            f"webhook registered: {resp.get('id')}\n"
            f"url: {resp.get('url')}\n"
            f"events: {resp.get('events') or 'all'}"
        )
    if command == "webhook-list":
        webhooks = resp.get("webhooks", [])
        count = resp.get("count", 0)
        lines = [f"{count} webhook(s):"]
        for wh in webhooks:
            status = "enabled" if wh.get("enabled") else "disabled"
            lines.append(
                f"  {wh['id']} [{status}] → {wh['url']} "
                f"(fired {wh.get('fire_count', 0)} times)"
            )
        return "\n".join(lines)
    if command in ("webhook-enable", "webhook-disable"):
        return f"webhook {resp.get('webhook_id')} {'enabled' if resp.get('ok') else 'not found'}"
    if command == "webhook-unregister":
        if not resp.get("existed", True):
            return f"webhook {resp.get('webhook_id')} not found"
        return f"webhook {resp.get('webhook_id')} unregistered"
    if command == "sla-check":
        tracked = resp.get("tracked", False)
        if not tracked:
            return f"container {resp.get('container_id')}: SLA not tracked"
        breached = resp.get("breached", False)
        lines = [
            f"container: {resp.get('container_id')}",
            f"uptime:   {resp.get('uptime_pct', 0):.4f}%",
            f"target:   {resp.get('target', 0)}%",
            f"status:   {'BREACHED' if breached else 'OK'}",
            f"downtime: {resp.get('downtime_s', 0):.1f}s",
            f"total:    {resp.get('total_time_s', 0):.1f}s",
            f"restarts: {resp.get('restart_count', 0)}/{resp.get('max_restarts', 0)}",
        ]
        violations = resp.get("violations", [])
        if violations:
            lines.append(f"violations: {len(violations)}")
        return "\n".join(lines)
    if command == "sla-violations":
        violations = resp.get("violations", [])
        count = resp.get("count", 0)
        lines = [f"container: {resp.get('container_id')} ({count} violations)"]
        for v in violations:
            ts = v.get("timestamp", 0)
            lines.append(
                f"  {ts:.1f}  {v.get('type', '?')}: {v.get('detail', '')}"
            )
        return "\n".join(lines)
    if command == "sla-set":
        lines = [f"container: {resp.get('container_id')}"]
        lines.append(f"  uptime_target:     {resp.get('sla_uptime_target', 99.9)}%")
        lines.append(f"  max_restarts:      {resp.get('sla_max_restart_count', 3)}")
        lines.append(f"  alert_on_breach:   {resp.get('sla_alert_on_breach', True)}")
        return "\n".join(lines)
    if command == "sla-escalation-policy":
        lines = [f"container: {resp.get('container_id')}"]
        lines.append("policy:")
        for level in resp.get("levels", []):
            actions = "+".join(level.get("actions", []))
            lines.append(
                f"  {level.get('threshold', '?')}: {actions}"
            )
        return "\n".join(lines)
    if command == "sla-escalation-status":
        lines = [
            f"container:       {resp.get('container_id')}",
            f"configured:      {resp.get('configured', False)}",
            f"current level:   {resp.get('current_level', 0)}",
            f"consecutive:     {resp.get('consecutive_breaches', 0)}",
        ]
        last_esc = resp.get("last_escalation_time", 0)
        if last_esc > 0:
            lines.append(
                f"last escalation: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_esc))}"
            )
        levels = resp.get("levels", [])
        if levels:
            lines.append("levels:")
            for level in levels:
                actions = "+".join(level.get("actions", []))
                lines.append(
                    f"  {level.get('threshold', '?')}: {actions}"
                )
        return "\n".join(lines)
    if command == "sla-escalation-reset":
        prev = resp.get("previous_breaches", 0)
        return (
            f"container: {resp.get('container_id')}\n"
            f"reset:     {resp.get('reset', False)}\n"
            f"was:       {prev} consecutive breaches"
        )
    if command == "sla-escalation-history":
        history = resp.get("history", [])
        if not history:
            return "No escalation history."
        lines = [f"escalation history ({len(history)} entries):"]
        for e in history:
            ts = e.get("timestamp", 0)
            actions = "+".join(e.get("actions", []))
            lines.append(
                f"  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))} "
                f"level={e.get('level', 0)} breaches={e.get('consecutive_breaches', 0)} "
                f"actions={actions}"
            )
        return "\n".join(lines)
    if command == "billing-rates-set" or command == "billing-rates-get":
        rates = resp.get("rates", {})
        lines = ["billing rates:"]
        for k, v in sorted(rates.items()):
            lines.append(f"  {k}: ${v}/hour")
        return "\n".join(lines)
    if command == "billing-record":
        if not resp.get("recorded", True):
            return f"container {resp.get('container_id')}: not running"
        return (
            f"container: {resp.get('container_id')}\n"
            f"memory:  ${resp.get('memory_cost', 0):.6f}\n"
            f"cpu:     ${resp.get('cpu_cost', 0):.6f}\n"
            f"pid:     ${resp.get('pid_cost', 0):.6f}\n"
            f"total:   ${resp.get('total_cost', 0):.6f}"
        )
    if command == "billing-records":
        records = resp.get("records", [])
        count = resp.get("count", 0)
        lines = [f"container: {resp.get('container_id')} ({count} records)"]
        for r in records[-5:]:  # Show last 5
            ts = r.get("timestamp", 0)
            lines.append(
                f"  {ts:.1f}  ${r.get('total_cost', 0):.6f}"
            )
        if count > 5:
            lines.append(f"  ... and {count - 5} more")
        return "\n".join(lines)
    if command == "billing-summary":
        if "grand_total_cost" in resp:
            # All-containers summary
            lines = [
                f"grand total: ${resp.get('grand_total_cost', 0):.6f}",
                f"containers:  {resp.get('container_count', 0)}",
            ]
            for c in resp.get("containers", []):
                lines.append(
                    f"  {c['container_id']}: ${c['total_cost']:.6f} "
                    f"({c['record_count']} records)"
                )
            return "\n".join(lines)
        return (
            f"container: {resp.get('container_id')}\n"
            f"total:     ${resp.get('total_cost', 0):.6f}\n"
            f"records:   {resp.get('record_count', 0)}\n"
            f"avg mem:   ${resp.get('avg_memory_cost', 0):.6f}\n"
            f"avg cpu:   ${resp.get('avg_cpu_cost', 0):.6f}\n"
            f"avg pid:   ${resp.get('avg_pid_cost', 0):.6f}"
        )
    if command == "cost-budget-configure":
        budget = resp.get("budget", {})
        lines = [
            f"container: {resp.get('container_id')}",
            f"daily:    ${budget.get('daily_limit', 0):.2f}",
            f"weekly:   ${budget.get('weekly_limit', 0):.2f}",
            f"monthly:  ${budget.get('monthly_limit', 0):.2f}",
            f"hard:     ${budget.get('hard_limit', 0):.2f}",
            f"threshold: {budget.get('alert_threshold_pct', 80)}%",
        ]
        return "\n".join(lines)
    if command == "cost-budget-check":
        lines = [
            f"container: {resp.get('container_id')}",
            f"within_budget: {resp.get('within_budget', True)}",
            "usage:"
        ]
        usage = resp.get("usage", {})
        lines.append(f"  daily:   ${usage.get('daily_cost', 0):.6f}")
        lines.append(f"  weekly:  ${usage.get('weekly_cost', 0):.6f}")
        lines.append(f"  monthly: ${usage.get('monthly_cost', 0):.6f}")
        alerts = resp.get("alerts", [])
        if alerts:
            lines.append(f"alerts ({len(alerts)}):")
            for a in alerts:
                lines.append(f"  [{a.get('severity', '?')}] {a.get('message', '')}")
        return "\n".join(lines)
    if command == "cost-alerts":
        alerts = resp.get("alerts", [])
        if not alerts:
            return "No cost alerts."
        lines = [f"cost alerts ({len(alerts)}):"]
        for a in alerts:
            lines.append(
                f"  [{a.get('severity', '?')}] {a.get('period', '?')}: "
                f"{a.get('message', '')}"
            )
        return "\n".join(lines)
    if command == "cost-budget-config":
        if not resp.get("configured", False):
            return f"container {resp.get('container_id')}: no budget configured"
        lines = [
            f"container:  {resp.get('container_id')}",
            f"daily:     ${resp.get('daily_limit', 0):.2f}",
            f"weekly:    ${resp.get('weekly_limit', 0):.2f}",
            f"monthly:   ${resp.get('monthly_limit', 0):.2f}",
            f"hard:      ${resp.get('hard_limit', 0):.2f}",
            f"threshold: {resp.get('alert_threshold_pct', 80)}%",
        ]
        return "\n".join(lines)
    if command == "autoscale-configure":
        as_cfg = resp.get("autoscale", {})
        lines = [
            f"container: {resp.get('container_id')}",
            f"enabled:  {as_cfg.get('enabled', False)}",
            f"memory:   {as_cfg.get('min_memory_mb', '?')}-{as_cfg.get('max_memory_mb', '?')} MB (target: {as_cfg.get('target_memory_pct', '?')}%)",
            f"cpu:      {as_cfg.get('min_cpu_quota', '?')}-{as_cfg.get('max_cpu_quota', '?')} (target: {as_cfg.get('target_cpu_pct', '?')}%)",
            f"cooldown: up={as_cfg.get('scale_up_cooldown_s', '?')}s down={as_cfg.get('scale_down_cooldown_s', '?')}s",
        ]
        return "\n".join(lines)
    if command == "autoscale-status":
        lines = [
            f"container: {resp.get('container_id')}",
            f"enabled:  {resp.get('enabled', False)}",
        ]
        current = resp.get("current", {})
        if current:
            lines.append(
                f"current:  mem={current.get('memory_mb', '?')} MB "
                f"cpu={current.get('cpu_quota', '?')}"
            )
            last_dir = current.get("last_scale_direction")
            if last_dir:
                lines.append(f"last:     {last_dir} at {current.get('last_scale_time', 0):.0f}")
        events = resp.get("events", [])
        if events:
            lines.append(f"events:   {len(events)} recent")
            for e in events[-3:]:
                lines.append(
                    f"  {e.get('direction', '?')} {e.get('scale_type', '?')}: "
                    f"{e.get('reason', '')}"
                )
        return "\n".join(lines)
    if command == "autoscale-apply":
        if not resp.get("scaled"):
            return f"container {resp.get('container_id')}: no scaling needed - {resp.get('reason', '')}"
        return (
            f"container: {resp.get('container_id')}\n"
            f"scaled:    yes\n"
            f"direction: {resp.get('direction', '?')}\n"
            f"type:      {resp.get('scale_type', '?')}"
        )
    if command == "autoscale-disable":
        return f"container {resp.get('container_id')}: auto-scaling disabled"
    if command == "autoscale-events":
        events = resp.get("events", [])
        if not events:
            return "No scaling events."
        lines = [f"scaling events ({len(events)}):"]
        for e in events:
            ts = e.get("timestamp", 0)
            lines.append(
                f"  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))} "
                f"{e.get('direction', '?')} {e.get('scale_type', '?')}: "
                f"{e.get('reason', '')}"
            )
        return "\n".join(lines)
    if command == "health-configure":
        lines = [
            f"container: {resp.get('container_id')}",
            f"cmd:      {resp.get('health_check_cmd')}",
            f"interval: {resp.get('interval')}s",
            f"timeout:  {resp.get('timeout')}s",
            f"retries:  {resp.get('retries')}",
            f"auto_restart: {resp.get('auto_restart', False)}",
            f"max_restarts: {resp.get('max_auto_restarts', 0)}",
        ]
        return "\n".join(lines)
    if command == "health-trigger":
        lines = [
            f"container: {resp.get('container_id')}",
            f"healthy:   {resp.get('healthy', False)}",
            f"status:    {resp.get('status', '?')}",
            f"exit_code: {resp.get('exit_code', -1)}",
            f"duration:  {resp.get('duration_s', 0)}s",
            f"failures:  {resp.get('failures', 0)}",
        ]
        output = resp.get("output", "")
        if output:
            lines.append(f"output:    {output[:200]}")
        return "\n".join(lines)
    if command == "health-config":
        lines = [
            f"container:      {resp.get('container_id')}",
            f"cmd:           {resp.get('health_check_cmd')}",
            f"interval:      {resp.get('interval')}s",
            f"timeout:       {resp.get('timeout')}s",
            f"retries:       {resp.get('retries')}",
            f"auto_restart:  {resp.get('auto_restart', False)}",
            f"max_restarts:  {resp.get('max_auto_restarts', 0)}",
            f"cooldown:      {resp.get('restart_cooldown_s', 60)}s",
            f"restart_count: {resp.get('restart_count', 0)}",
        ]
        return "\n".join(lines)
    if command == "health-restart-reset":
        prev = resp.get("previous_count", 0)
        return (
            f"container: {resp.get('container_id')}\n"
            f"reset:     {resp.get('reset', False)}\n"
            f"was:       {prev} restarts"
        )
    if command == "health-restart-history":
        history = resp.get("history", [])
        if not history:
            return "No restart history."
        lines = [f"restart history ({len(history)}):"]
        for e in history:
            ts = e.get("timestamp", 0)
            lines.append(
                f"  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))} "
                f"attempt {e.get('attempt', '?')}/{e.get('max', '?')} "
                f"reason: {e.get('reason', '?')}"
            )
        return "\n".join(lines)
    if command == "forecast-resource":
        if not resp.get("sufficient_data"):
            return f"{resp.get('resource')}: insufficient data for forecast"
        lines = [
            f"resource: {resp.get('resource')}",
            f"current:  {resp.get('current_value', 0):,.0f}",
            f"predicted: {resp.get('predicted_value', 0):,.0f} (in {resp.get('sample_count', 0)} samples)",
            f"trend:    {resp.get('trend_per_hour', 0):+,.0f}/hour",
            f"confidence: {resp.get('confidence', 0):.1%}",
        ]
        ttl = resp.get("time_to_limit_s")
        if ttl is not None:
            hours = int(ttl // 3600)
            mins = int((ttl % 3600) // 60)
            lines.append(f"time_to_limit: {hours}h {mins}m")
        return "\n".join(lines)
    if command == "forecast-all":
        lines = [f"container: {resp.get('container_id')}"]
        for res in ("memory", "cpu", "pids"):
            fc = resp.get(res, {})
            if not fc.get("sufficient_data"):
                lines.append(f"  {res}: (insufficient data)")
            else:
                lines.append(
                    f"  {res}: current={fc.get('current_value', 0):,.0f} "
                    f"predicted={fc.get('predicted_value', 0):,.0f} "
                    f"trend={fc.get('trend_per_hour', 0):+,.0f}/h"
                )
        return "\n".join(lines)
    if command == "forecast-exhaustion":
        result = resp.get("result")
        if result is None:
            return f"{resp.get('resource', '?')}: no exhaustion forecast available"
        return (
            f"resource: {result.get('resource')}\n"
            f"time:     {result.get('hours', 0)}h {result.get('minutes', 0)}m {result.get('seconds', 0)}s\n"
            f"total:    {result.get('total_seconds', 0):.1f}s"
        )
    if command == "capacity-plan":
        lines = [
            f"container: {resp.get('container_id')}",
            f"horizon:  {resp.get('horizon_days', 30)} days",
            resp.get("summary", ""),
            "resources:"
        ]
        for res, plan in resp.get("resources", {}).items():
            if not plan.get("sufficient_data"):
                lines.append(f"  {res}: (insufficient data)")
                continue
            risk = plan.get("risk_level", "unknown")
            icon = "!" if risk == "high" else ("~" if risk == "medium" else "ok")
            lines.append(
                f"  {res}: [{icon}] risk={risk} "
                f"util={plan.get('current_utilization_pct', 0)}% "
                f"predicted={plan.get('predicted_utilization_pct', 0)}% "
                f"growth={plan.get('growth_rate_per_day', 0):+.1f}/day"
            )
            if plan.get("time_to_capacity"):
                tc = plan["time_to_capacity"]
                lines.append(
                    f"    time_to_capacity: {tc.get('days', '?')} days"
                )
        rec = resp.get("recommended_limits", {})
        if rec:
            lines.append("recommended:")
            for k, v in rec.items():
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)
    if command == "capacity-plan-all":
        lines = [
            f"horizon:  {resp.get('horizon_days', 30)} days",
            f"containers: {resp.get('container_count', 0)}",
            f"high_risk: {resp.get('high_risk_count', 0)}",
            ""
        ]
        for c in resp.get("containers", []):
            issues = []
            for res, plan in c.get("resources", {}).items():
                if plan.get("risk_level") in ("high", "medium"):
                    issues.append(f"{res}:{plan.get('risk_level')}")
            issue_str = ",".join(issues) if issues else "ok"
            lines.append(
                f"  {c.get('container_id', '?')}: [{issue_str}]"
            )
        return "\n".join(lines)
    if command == "network-traffic":
        if resp.get("insufficient_data"):
            return f"container {resp.get('container_id')}: insufficient data"
        lines = [
            f"container: {resp.get('container_id')}",
            f"window:   {resp.get('window_s', 0)}s",
            f"samples:  {resp.get('sample_count', 0)}",
            f"rx total: {resp.get('rx_bytes_total', 0):,} bytes",
            f"tx total: {resp.get('tx_bytes_total', 0):,} bytes",
            f"rx rate:  {resp.get('rx_bytes_per_sec', 0):,.0f} B/s",
            f"tx rate:  {resp.get('tx_bytes_per_sec', 0):,.0f} B/s",
            f"rx pkts:  {resp.get('rx_packets_delta', 0):,}",
            f"tx pkts:  {resp.get('tx_packets_delta', 0):,}",
        ]
        patterns = resp.get("patterns", {})
        lines.append(f"direction: {patterns.get('dominant_direction', '?')}")
        lines.append(f"burstiness: {patterns.get('burstiness', 0):.3f}")
        lines.append(f"error rate: {patterns.get('error_rate_pct', 0):.2f}%")
        lines.append(f"drop rate:  {patterns.get('drop_rate_pct', 0):.2f}%")
        return "\n".join(lines)
    if command == "network-connections":
        conns = resp.get("connections", [])
        summary = resp.get("summary", {})
        lines = [
            f"container: {resp.get('container_id')}",
            f"total:     {summary.get('total', 0)}",
        ]
        by_proto = summary.get("by_protocol", {})
        if by_proto:
            lines.append("by protocol:")
            for proto, count in by_proto.items():
                lines.append(f"  {proto}: {count}")
        by_state = summary.get("by_state", {})
        if by_state:
            lines.append("by state:")
            for state, count in by_state.items():
                lines.append(f"  {state}: {count}")
        # Show first few connections
        for conn in conns[:5]:
            lines.append(
                f"  {conn.get('proto', '?')} {conn.get('state', '?')} "
                f"{conn.get('local', '?')} -> {conn.get('peer', '?')}"
            )
        if len(conns) > 5:
            lines.append(f"  ... and {len(conns) - 5} more")
        return "\n".join(lines)
    if command == "network-bandwidth-history":
        history = resp.get("history", [])
        if not history:
            return "No bandwidth history."
        lines = [f"bandwidth history ({len(history)} samples):"]
        for h in history[-10:]:
            ts = h.get("timestamp", 0)
            lines.append(
                f"  {time.strftime('%H:%M:%S', time.localtime(ts))} "
                f"rx={h.get('rx_bytes', 0):,} tx={h.get('tx_bytes', 0):,}"
            )
        return "\n".join(lines)
    if command == "anomaly-detect":
        if resp.get("insufficient_data"):
            return f"{resp.get('resource')}: insufficient data ({resp.get('sample_count', 0)} samples)"
        anomalies = resp.get("anomalies", [])
        lines = [
            f"resource: {resp.get('resource')}",
            f"mean:     {resp.get('mean')}",
            f"stddev:   {resp.get('stddev')}",
            f"samples:  {resp.get('sample_count')}",
            f"anomalies: {len(anomalies)}",
        ]
        for a in anomalies:
            lines.append(
                f"  [{a.get('type')}] value={a.get('value')} "
                f"z={a.get('z_score')} dev={a.get('deviation_pct', 0):+.1f}%"
            )
        return "\n".join(lines)
    if command == "anomaly-detect-all":
        lines = [
            f"container: {resp.get('container_id')}",
            f"total anomalies: {resp.get('total_anomalies', 0)}",
        ]
        for res in ("memory", "cpu", "pids"):
            det = resp.get("resources", {}).get(res, {})
            count = len(det.get("anomalies", []))
            if det.get("insufficient_data"):
                lines.append(f"  {res}: (insufficient data)")
            else:
                lines.append(
                    f"  {res}: {count} anomalies "
                    f"(mean={det.get('mean')}, std={det.get('stddev')})"
                )
        return "\n".join(lines)
    if command == "anomaly-spike":
        if resp.get("insufficient_data"):
            return f"{resp.get('resource')}: insufficient data"
        is_spike = resp.get("is_spike", False)
        return (
            f"resource:    {resp.get('resource')}\n"
            f"is_spike:    {'YES' if is_spike else 'no'}\n"
            f"current:     {resp.get('current')}\n"
            f"prev_avg:    {resp.get('previous_avg')}\n"
            f"change:      {resp.get('change_pct', 0):.1f}% {resp.get('direction')}\n"
            f"threshold:   {resp.get('threshold_pct', 0):.1f}%"
        )
    if command == "anomaly-trend":
        if resp.get("trend") == "insufficient_data":
            return f"{resp.get('resource')}: insufficient data"
        return (
            f"resource:     {resp.get('resource')}\n"
            f"trend:        {resp.get('trend')}\n"
            f"recent_rate:  {resp.get('recent_rate', 0):.1%}\n"
            f"overall_rate: {resp.get('overall_rate', 0):.1%}\n"
            f"recent_count: {resp.get('recent_anomaly_count', 0)}"
        )
    if command == "compare-resources":
        lines = [f"resource: {resp.get('resource')}"]
        stats = resp.get("statistics", {})
        lines.append(f"  total: {stats.get('total_current', 0):,}")
        lines.append(f"  mean:  {stats.get('mean_current', 0):,}")
        for e in resp.get("rankings", []):
            lines.append(
                f"  #{e.get('rank')} {e.get('container_id')}: "
                f"current={e.get('current', 0):,} avg={e.get('average', 0):,}"
            )
        return "\n".join(lines)
    if command == "compare-all":
        lines = [f"container count: {resp.get('container_count', 0)}"]
        for res in ("memory", "cpu", "pids"):
            comp = resp.get("comparisons", {}).get(res, {})
            stats = comp.get("statistics", {})
            lines.append(
                f"\n{res}: total={stats.get('total_current', 0):,} "
                f"mean={stats.get('mean_current', 0):,}"
            )
            for e in comp.get("rankings", [])[:3]:
                lines.append(
                    f"  #{e.get('rank')} {e.get('container_id')}: "
                    f"current={e.get('current', 0):,}"
                )
        return "\n".join(lines)
    if command == "compare-relative":
        return (
            f"container:  {resp.get('container_id')}\n"
            f"resource:  {resp.get('resource')}\n"
            f"current:   {resp.get('current', 0):,}\n"
            f"average:   {resp.get('average', 0):,}\n"
            f"rank:      {resp.get('rank', 0)}/{resp.get('total', 0)}\n"
            f"percentile: {resp.get('percentile', 0)}%\n"
            f"vs avg:    {resp.get('vs_average_pct', 0):+.1f}%"
        )
    if command == "compare-top":
        lines = [f"resource: {resp.get('resource')}", "rankings:"]
        for e in resp.get("rankings", []):
            lines.append(
                f"  #{e.get('rank')} {e.get('container_id')}: "
                f"{e.get('value', 0):,}"
            )
        return "\n".join(lines)
    if command == "recommend-get":
        lines = [
            f"container: {resp.get('container_id')}",
            f"score:    {resp.get('score', 0)}/100",
            resp.get("summary", ""),
            "recommendations:"
        ]
        for r in resp.get("recommendations", []):
            sev = r.get("severity", "info")
            icon = "⚠" if sev == "warning" else "ℹ"
            lines.append(f"  {icon} [{r.get('category')}] {r.get('title')}")
            lines.append(f"    {r.get('detail')}")
            if r.get("savings_estimate"):
                lines.append(f"    Savings: {r['savings_estimate']}")
        return "\n".join(lines)
    if command == "recommend-all":
        lines = [
            f"containers: {resp.get('container_count', 0)}",
            f"avg score: {resp.get('average_score', 0)}/100",
            ""
        ]
        for c in resp.get("containers", []):
            count = len(c.get("recommendations", []))
            lines.append(
                f"  {c.get('container_id')}: "
                f"score={c.get('score', 0)} recs={count}"
            )
        return "\n".join(lines)
    if command == "recommend-category":
        lines = [
            f"container: {resp.get('container_id')}",
            f"category: {resp.get('category')}",
            f"count: {resp.get('count', 0)}",
        ]
        for r in resp.get("recommendations", []):
            sev = r.get("severity", "info")
            icon = "⚠" if sev == "warning" else "ℹ"
            lines.append(f"  {icon} {r.get('title')}")
            lines.append(f"    {r.get('detail')}")
        return "\n".join(lines)

    if command == "containers-stats":
        if not resp.get("available"):
            return f"container {resp.get('container_id')}: stats not available (state={resp.get('state')})"
        lines = [
            f"container:  {resp.get('container_id')}",
            f"state:     {resp.get('state')}",
            f"pid:       {resp.get('pid')}",
            f"uptime:    {resp.get('uptime_s')}s",
        ]
        mem = resp.get("memory_bytes")
        if mem is not None:
            lines.append(f"memory:    {mem:,} bytes")
            limit = resp.get("memory_limit_bytes")
            if limit is not None:
                lines.append(f"mem limit: {limit:,} bytes")
                pct = round(mem / limit * 100, 1) if limit > 0 else 0
                lines.append(f"mem used:  {pct}%")
            else:
                lines.append("mem limit: unlimited")
        cpu = resp.get("cpu_usage_usec")
        if cpu is not None:
            lines.append(f"cpu:       {cpu:,} µs")
        throttle = resp.get("cpu_throttle_pct")
        if throttle is not None:
            lines.append(f"throttle:  {throttle}%")
        pids = resp.get("pids_current")
        if pids is not None:
            lines.append(f"pids:      {pids}")
            plim = resp.get("pids_limit")
            if plim is not None:
                lines.append(f"pid limit: {plim}")
        return "\n".join(lines)
    if command == "vault-volume-create":
        return f"volume {resp.get('volume_id')} created ({resp.get('name')})"
    if command == "vault-volume-open":
        return (f"handle {resp.get('handle')} for volume "
                f"{resp.get('volume_id')}")
    if command == "vault-volume-list":
        volumes: List[Dict[str, Any]] = resp.get("volumes") or []
        if not volumes:
            return "no volumes"
        rows = [f"{v.get('id')}\t{v.get('name')}\t{v.get('created_by')}"
                for v in volumes]
        return "\n".join(["id\tname\tcreated_by"] + rows)
    if command == "vault-volume-grant":
        scope = resp.get("path")
        suffix = (f" (scope: {scope})" if scope else " (whole volume)")
        return (f"volume {resp.get('volume_id')}: container "
                f"{resp.get('container')} granted access{suffix}")
    if command == "vault-volume-revoke":
        return (f"volume {resp.get('volume_id')}: container "
                f"{resp.get('container')} access "
                f"{'revoked' if resp.get('revoked') else 'had no grant'}")
    if command == "vault-volume-grants":
        grants = resp.get("grants") or []
        if not grants:
            return f"volume {resp.get('volume_id')}: no grants"
        parts = []
        for g in grants:
            if isinstance(g, dict):
                scope = g.get("path")
                label = (f"{g.get('container')}@{scope}"
                         if scope and scope != "/"
                         else f"{g.get('container')}")
            else:
                label = str(g)
            parts.append(label)
        return (f"volume {resp.get('volume_id')}: "
                + ", ".join(parts))
    if command == "vault-volume-close":
        # The close reply is ``{"ok": true}`` — no handle echoed; the
        # operator knows which handle they closed.
        return "handle closed"
    if command == "vault-volume-write":
        line = (f"wrote {resp.get('bytes_written')} bytes to "
                f"{resp.get('path')}")
        warning = resp.get("warning")
        if warning is not None:
            line += f" (quota warning: {warning})"
        return line
    if command == "vault-volume-snapshot":
        return f"snapshot {resp.get('snapshot_id')} of volume {resp.get('volume_id')}"
    if command == "vault-volume-snapshots":
        snaps = resp.get("snapshots") or []
        return "\n".join(snaps) if snaps else "no snapshots"
    if command == "vault-volume-restore":
        return (f"volume restored to snapshot {resp.get('restored')} "
                f"({resp.get('volume_id')})")
    if command == "vault-volume-snapshot-delete":
        return (f"snapshot {resp.get('deleted')} of volume "
                f"{resp.get('volume_id')} deleted")
    if command == "vault-volume-delete":
        return f"volume {resp.get('volume_id')} deleted " \
               "(crypto-shredded)"
    if command == "vault-volume-rekey":
        return (f"rekeyed {resp.get('rekeyed')} volume(s) — the new "
                f"KEK envelope was written to {resp.get('key_file')}; "
                f"restart the daemon with that key file + the new "
                "passphrase to serve under the new KEK")
    if command == "vault-volume-quota-set":
        scope = resp.get("path")
        target = f"{resp.get('container')}" + (
            f" under {scope}" if scope else "")
        if resp.get("bytes") is None:
            return (f"volume {resp.get('volume_id')}: container "
                    f"{target} quota cleared (unlimited)")
        return (f"volume {resp.get('volume_id')}: container "
                f"{target} quota set to {resp.get('bytes')} bytes")
    if command == "vault-volume-quota-get":
        rows = resp.get("rows") or []
        if not rows:
            return f"volume {resp.get('volume_id')}: no quotas or usage"
        lines = ["container\tscope\tquota\tusage\twarning"]
        for row in rows:
            quota = (f"{row['quota']}" if row.get("quota") is not None
                     else "unlimited")
            warning = row.get("warning") or "-"
            lines.append(f"{row['container']}\t{row.get('scope') or '/'}\t"
                         f"{quota}\t{row['usage']}\t{warning}")
        return "\n".join(lines)
    if command == "vault-volume-usage":
        usage = resp.get("usage") or {}
        phys = resp.get("physical_bytes")
        head = f"volume {resp.get('volume_id')}: "
        if not usage:
            line = head + "no accounted usage"
        else:
            line = head + "\n" + "\n".join(
                ["container\tusage"] +
                [f"{c}\t{u}" for c, u in sorted(usage.items())])
        if phys is not None:
            line += f"\nphysical (block-store): {phys} bytes"
        # Per-subtree usage (0.14.19).
        scope_usage = resp.get("scope_usage") or {}
        for c, m in sorted(scope_usage.items()):
            for s, u in sorted(m.items()):
                line += f"\nsubtree usage ({c} @ {s}): {u} bytes"
        warnings = resp.get("warnings") or {}
        for c, w in sorted(warnings.items()):
            line += f"\nquota warning ({c}): {w}"
        return line
    if command == "vault-volume-summary":
        volumes = resp.get("volumes") or []
        if not volumes:
            return (f"vault: no volumes (logical 0 B, physical 0 B)")
        rows = [f"{v['name']}\t{v['logical_bytes']}\t"
                f"{v['physical_bytes']}\t{v['consumers']}\t"
                f"{v.get('warning_count', 0)}"
                for v in volumes]
        return "\n".join(
            ["vault summary",
             f"volumes: {resp.get('volume_count')} "
             f"(logical {resp.get('total_logical_bytes')} B, "
             f"physical {resp.get('total_physical_bytes')} B)",
             "name\tlogical\tphysical\tconsumers\twarnings"] + rows)
    if command == "vault-volume-events":
        events = resp.get("events") or []
        if not events:
            return "no events"
        rows = []
        for e in events:
            kind = e.get("kind", "quota")
            if kind in ("grant", "revoke"):
                rows.append(f"{e.get('t')}\t{e.get('volume')}\t"
                            f"{e.get('container')}\t{kind}\t"
                            f"scope={e.get('scope')}")
            else:
                rows.append(f"{e.get('t')}\t{e.get('volume')}\t"
                            f"{e.get('container')}\t{e.get('level')}\t"
                            f"{e.get('usage')}/{e.get('quota')}")
        return "\n".join(
            ["time\tvolume\tcontainer\tkind\tdetail"] + rows)
    if command == "app-install":
        app = resp.get("app") or {}
        compat = app.get("compatibility") or {}
        platform = compat.get("platform", "unknown")
        lines = [
            f"installed: {resp.get('app_id')}",
            f"name:      {app.get('name')}",
            f"version:   {app.get('version')}",
            f"platform:  {platform}",
            f"sandbox:   {'yes' if resp.get('sandbox') else 'no'}",
        ]
        perms = compat.get("permissions") or []
        if perms:
            lines.append(f"perms:     {', '.join(perms[:8])}"
                         + (f" (+{len(perms)-8} more)"
                            if len(perms) > 8 else ""))
        return "\n".join(lines)
    if command == "app-list":
        apps = resp.get("apps") or []
        if not apps:
            return "no installed apps"
        rows = []
        for a in apps:
            compat = a.get("compatibility") or {}
            platform = compat.get("platform", "?")
            status = a.get("status", "installed")
            rows.append(
                f"{a.get('app_id')}\t{a.get('name')}\t"
                f"{platform}\t{status}")
        return "\n".join(["id\tname\tplatform\tstatus"] + rows)
    if command == "app-launch":
        return (
            f"app {resp.get('app_id')} launched "
            f"(container {resp.get('container_id')}, "
            f"pid {resp.get('pid')})")
    if command == "app-terminate":
        return f"app {resp.get('app_id')} terminated"
    if command == "resource-profile":
        procs = resp.get("processes", [])
        if not procs:
            return "no running processes"
        summary = resp.get("summary", {})
        lines = [
            f"Processes: {summary.get('process_count', 0)}"
            f"  Threads: {summary.get('total_threads', 0)}"
            f"  CPU: {summary.get('total_cpu_s', 0):.3f}s"
            f"  RSS: {_fmt_bytes(summary.get('total_rss_bytes', 0))}"
            f"  IO-R: {_fmt_bytes(summary.get('total_io_read_bytes', 0))}"
            f"  IO-W: {_fmt_bytes(summary.get('total_io_write_bytes', 0))}",
            "",
            "PID\tCOMM\tCPU\tRSS\tIO-R\tIO-W\tTHR",
        ]
        for p in procs:
            lines.append(
                f"{p['pid']}\t{p.get('comm', '?')}\t"
                f"{p['cpu_time_s']:.3f}s\t"
                f"{_fmt_bytes(p['rss_bytes'])}\t"
                f"{_fmt_bytes(p['io_read_bytes'])}\t"
                f"{_fmt_bytes(p['io_write_bytes'])}\t"
                f"{p['threads']}")
        return "\n".join(lines)
    if command == "resource-profile-history":
        history = resp.get("history", [])
        if not history:
            return "no profiling history"
        lines = ["timestamp\tcpu_s\trss\tio_read\tio_write\tthreads"]
        for h in history:
            s = h.get("summary", {})
            lines.append(
                f"{h.get('timestamp', 0):.0f}\t"
                f"{s.get('total_cpu_s', 0):.3f}\t"
                f"{_fmt_bytes(s.get('total_rss_bytes', 0))}\t"
                f"{_fmt_bytes(s.get('total_io_read_bytes', 0))}\t"
                f"{_fmt_bytes(s.get('total_io_write_bytes', 0))}\t"
                f"{s.get('total_threads', 0)}")
        return "\n".join(lines)
    if command == "resource-profile-top":
        top = resp.get("top", [])
        if not top:
            return f"no data for {resp.get('resource', '?')}"
        lines = [
            f"Top {resp.get('resource', '?')} consumers"
            f" (total: {_fmt_bytes(resp.get('total', 0))}):",
            "",
            "PID\tCOMM\tVALUE",
        ]
        for p in top:
            val = p.get(resp.get("resource", "rss_bytes"), 0)
            lines.append(
                f"{p['pid']}\t{p.get('comm', '?')}\t{_fmt_bytes(val)}")
        return "\n".join(lines)
    if command in ("batch-start", "batch-stop", "batch-kill"):
        verb = command.split("-")[1]
        matched = resp.get("total_matched", 0)
        acted = resp.get("started") or resp.get("stopped") or resp.get("killed") or []
        skipped = resp.get("skipped", [])
        failed = resp.get("failed", [])
        lines = [f"Matched {matched} container(s)"]
        if acted:
            lines.append(f"  {verb}: {', '.join(acted)}")
        if skipped:
            lines.append(f"  skipped: {', '.join(skipped)}")
        if failed:
            for f in failed:
                lines.append(f"  FAILED {f['id']}: {f['error']}")
        return "\n".join(lines)
    return json.dumps(resp, indent=2, sort_keys=True)


# -- daemon round trip -------------------------------------------------

def call_daemon(
    socket_path: str, payload: Dict[str, Any],
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Optional[Dict[str, Any]]:
    """One operator CALL over the IPC transport; ``None`` when the
    daemon did not reply (not running, or the CALL timed out)."""
    tmp = tempfile.mkdtemp(prefix="nyrqisctl-")
    cli_path = os.path.join(tmp, "ctl.sock")
    client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
    try:
        try:
            reply = client.call(
                socket_path, json.dumps(payload).encode("utf-8"),
                timeout_s=timeout_s,
            )
        except (OSError, IPCTransportError) as e:
            # A missing/closed daemon socket surfaces differently per
            # client half: the Rust client half raises OSError
            # (ENOENT/ECONNREFUSED), the floor wraps send failures in
            # IPCTransportError; the floor returns None on timeout.
            # All mean "no daemon there" — the same outcome.
            logger.debug("nyrqisctl: call to %s failed: %s",
                         socket_path, e)
            return None
        if reply is None:
            return None
        try:
            resp = json.loads(reply.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.error("nyrqisctl: malformed reply payload")
            return None
        return resp if isinstance(resp, dict) else None
    finally:
        client.close()
        shutil.rmtree(tmp, ignore_errors=True)


def run(command: str, args: argparse.Namespace) -> int:
    """Execute ``command`` against the daemon; returns the exit code."""
    if command in STATUS_COMMANDS and args.health_socket:
        # ADR-0021: probe the dedicated health socket (no contention
        # with container traffic on the main service socket).
        target = args.health_socket
    elif (command in CONTROL_COMMANDS
          or command in VAULT_COMMANDS
          or command in NUI_COMMANDS
          or command in APP_COMMANDS) and args.health_socket:
        print(
            "error: the health socket serves status/health only — "
            "control and vault commands use the main --socket",
            file=sys.stderr,
        )
        return 2
    else:
        target = args.socket
    if command == "vault-volume-rekey":
        # OPERATOR-ONLY KEK rotation (ADR-0023): the new passphrase
        # rides to the daemon over the authenticated operator path; the
        # new envelope the daemon derives comes back in the reply.
        args.new_passphrase = args.new_passphrase or os.environ.get(
            "NYRQIS_VAULT_REKEY_PASSPHRASE")
        if not args.new_passphrase:
            print("error: vault rekey needs the new passphrase "
                  "(--new-passphrase or "
                  "NYRQIS_VAULT_REKEY_PASSPHRASE)",
                  file=sys.stderr)
            return 2
        if not args.new_key_file:
            print("error: vault rekey needs --new-key-file "
                  "(where the new KEK envelope is written)",
                  file=sys.stderr)
            return 2
        if os.path.exists(args.new_key_file):
            print(f"error: {args.new_key_file} already exists",
                  file=sys.stderr)
            return 2
    if command == "vault-volume-write":
        # The write payload travels as base64 over the datagram
        # transport, so the CLI enforces the same per-call cap the
        # service enforces (page with --offset for larger blobs).
        if getattr(args, "file", ""):
            with open(args.file, "rb") as f:
                args.data = f.read()
        else:
            args.data = sys.stdin.buffer.read()
        if len(args.data) > MAX_IO_BYTES:
            print(
                f"error: write exceeds the {MAX_IO_BYTES}-byte "
                "per-call limit (page with --offset)",
                file=sys.stderr,
            )
            return 2
    if command in NUI_COMMANDS:
        # The .nstudio document rides ONE CALL/REPLY datagram, so the
        # CLI enforces the service's per-call budget before sending.
        from ui.service import NUI_DOCUMENT_MAX_BYTES
        if command == "nui-current":
            args.document = ""
        if getattr(args, "file", ""):
            with open(args.file, "r", encoding="utf-8") as f:
                args.document = f.read()
        else:
            args.document = sys.stdin.read()
        if len(args.document.encode("utf-8")) > NUI_DOCUMENT_MAX_BYTES:
            print(
                f"error: document exceeds the {NUI_DOCUMENT_MAX_BYTES}-byte "
                "per-call budget (wire streaming is a follow-on)",
                file=sys.stderr,
            )
            return 2
    if command == "vault-vault-init":
        # A LOCAL command: writes the KEK envelope (ADR-0023) — the
        # only thing the daemon needs to unlock the vault at rest.
        from backend import keys
        passphrase = args.passphrase or os.environ.get(
            "NYRQIS_VAULT_PASSPHRASE")
        if not passphrase:
            print("error: vault init needs a passphrase "
                  "(--passphrase or NYRQIS_VAULT_PASSPHRASE)",
                  file=sys.stderr)
            return 2
        if os.path.exists(args.key_file):
            print(f"error: {args.key_file} already exists",
                  file=sys.stderr)
            return 2
        blob = keys.make_blob_any(passphrase.encode("utf-8"))
        with open(args.key_file, "wb") as f:
            f.write(blob)
        print(f"vault KEK envelope written to {args.key_file} "
              f"({len(blob)} bytes)")
        return 0
    if command == "vault-volume-mount":
        # A LOCAL command: open the volume and mount it as a FUSE
        # passthrough whose ops are storage-service CALLs (ADR-0022).
        # Needs fusepy + /dev/fuse; without them the deferral is
        # reported honestly (the volume ops stay usable via the other
        # vault subcommands).
        from fuse.vault_mount import NyVaultMount
        volume = args.volume_id or args.name
        if not volume:
            print("error: vault mount needs a volume id or --name",
                  file=sys.stderr)
            return 2
        tmp = tempfile.mkdtemp(prefix="nyrqisctl-mnt-")
        cli_path = os.path.join(tmp, "ctl.sock")
        client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        mount = None
        try:
            mount = NyVaultMount(client, target, volume, args.mount_point)
            ok = mount.mount(
                foreground=True, blocking=False)
        except Exception as e:  # noqa: BLE001 - surface mount failures clearly
            print(f"error: vault mount failed: {e}", file=sys.stderr)
            return 1
        if not ok:
            print(
                "error: fusepy is not available in this environment — "
                "the NyVault mount cannot be established (use the "
                "other 'vault' subcommands for the byte path)",
                file=sys.stderr,
            )
            client.close()
            return 1
        print(f"mounted volume {volume} at {args.mount_point} "
              "(serving until unmounted)")
        # The FUSE loop runs in a daemon thread; this process must stay
        # alive or the kernel mount dies with it. Block until the mount
        # is unmounted (fusermount -u or Ctrl-C).
        try:
            thread = getattr(mount, "_thread", None)
            if thread is not None:
                thread.join()
            return 0
        finally:
            client.close()
    payload = build_payload(command, args)
    resp = call_daemon(target, payload, timeout_s=args.timeout)
    if resp is None:
        print(
            f"error: no reply from the daemon at {target} "
            "(is it running?)",
            file=sys.stderr,
        )
        return 1
    if not resp.get("ok"):
        print(f"error: {resp.get('error', 'operation failed')}",
              file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(resp, indent=2, sort_keys=True))
    elif command == "containers-kill":
        # The kill reply carries no id (``{"ok": true}``) — the CLI
        # knows which container it asked to terminate.
        print(f"container {args.container_id} terminated")
    elif command == "vault-volume-read":
        # Raw bytes by default (a blob read is bytes, not text);
        # ``--output`` redirects to a file instead of stdout.
        data = base64.b64decode(resp["data_b64"])
        if getattr(args, "output", None):
            with open(args.output, "wb") as f:
                f.write(data)
            print(f"wrote {len(data)} bytes to {args.output}")
        else:
            sys.stdout.buffer.write(data)
    elif command == "vault-volume-rekey":
        # Persist the new KEK envelope the daemon derived (its salt is
        # the one the DEKs were re-wrapped with — a locally-generated
        # envelope would NOT match).
        key_file = args.new_key_file
        with open(key_file, "wb") as f:
            f.write(base64.b64decode(resp["new_envelope_b64"]))
        resp["key_file"] = key_file
        print(format_human(command, resp))
    else:
        print(format_human(command, resp))
    return 0


# -- argument parsing --------------------------------------------------

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--socket", default=DEFAULT_SOCKET,
        help=f"The daemon's main service socket "
             f"(default: {DEFAULT_SOCKET})",
    )
    parser.add_argument(
        "--health-socket", default="",
        help="The daemon's dedicated health-probe socket (ADR-0021) — "
             "ping/status/health are routed there instead of the main "
             "--socket (default: disabled)",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S,
        help=f"CALL timeout in seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print the raw JSON reply instead of human output",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging (debug level)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nyrqisctl",
        description="Drive a running Nyrqis backend daemon's control "
                    "plane (status + control over the IPC transport).",
    )
    _add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ping", help="Ping the daemon (no auth beyond "
                                    "the transport's own checks)")
    p.set_defaults(command="ping")

    p = sub.add_parser("status", help="Daemon status (operator)")
    p.set_defaults(command="status")

    p = sub.add_parser("health", help="Daemon health diagnostics "
                                      "(operator)")
    p.set_defaults(command="health")

    containers = sub.add_parser(
        "containers", help="Manage the daemon's containers")
    csub = containers.add_subparsers(dest="container_cmd", required=True)

    cl = csub.add_parser("list", help="List the daemon's containers")
    cl.set_defaults(command="containers-list")

    cr = csub.add_parser("run", help="Spawn a container on the daemon")
    cr.add_argument("--name", default="", help="Container name")
    cr.add_argument(
        "--capabilities", default="",
        help="Comma-separated data-plane capabilities (seccomp)")
    cr.add_argument("--network", action="store_true",
                    help="Give the container its own network namespace")
    cr.add_argument("--memory", type=int, default=256,
                    help="Memory limit in MiB (default: 256)")
    cr.add_argument("--pids", type=int, default=64,
                    help="PID limit (default: 64)")
    cr.add_argument("--restart-policy", default="no",
                    choices=["no", "always", "on-failure"],
                    help="Auto-restart policy (default: no)")
    cr.add_argument("--restart-max-retries", type=int, default=5,
                    help="Max restart attempts (0=unlimited, default: 5)")
    cr.add_argument("--restart-delay", type=float, default=1.0,
                    help="Seconds between restart attempts (default: 1.0)")
    # Named ``run_command`` (NOT ``command``): a positional sharing the
    # subparsers' ``dest`` would clobber the ``command`` value the
    # subparser's ``set_defaults`` installed, so ``run`` would lose the
    # command it is executing.
    cr.add_argument("run_command", nargs="+", help="Command to run")
    cr.set_defaults(command="containers-run")

    ck = csub.add_parser("kill", help="Terminate a container on the daemon")
    ck.add_argument("container_id")
    ck.set_defaults(command="containers-kill")

    cs = csub.add_parser("stats", help="Show live resource stats for a container")
    cs.add_argument("container_id")
    cs.set_defaults(command="containers-stats")

    clo = csub.add_parser("logs", help="Show captured stdout/stderr for a container")
    clo.add_argument("container_id")
    clo.add_argument("--tail", type=int, default=None,
                     help="Show only the last N lines")
    clo.add_argument("--stream", choices=["stdout", "stderr", "both"],
                     default="both", help="Which stream to show")
    clo.set_defaults(command="containers-logs")

    ce = csub.add_parser("exec", help="Execute a command inside a running container")
    ce.add_argument("container_id")
    ce.add_argument("exec_command", nargs="+", help="Command and arguments to run")
    ce.add_argument("--timeout", type=float, default=10.0,
                    help="Timeout in seconds (default: 10)")
    ce.set_defaults(command="containers-exec")

    cc = csub.add_parser("checkpoint", help="Checkpoint a container's filesystem state")
    cc.add_argument("container_id")
    cc.add_argument("--path", default=None, help="Output file path")
    cc.set_defaults(command="containers-checkpoint")

    ct = csub.add_parser("top", help="List processes inside a container")
    ct.add_argument("container_id")
    ct.add_argument("--sort", dest="sort_by", default=None,
                    choices=["pid", "cpu", "memory", "rss", "fd", "threads"],
                    help="Sort by field")
    ct.add_argument("--asc", dest="descending", action="store_false",
                    default=True, help="Sort ascending")
    ct.add_argument("--max-depth", type=int, default=None,
                    help="Max tree depth to scan")
    ct.add_argument("--summary", dest="summary_only", action="store_true",
                    default=False, help="Show summary only")
    ct.set_defaults(command="containers-top")

    cn = csub.add_parser("net", help="Show network interface stats for a container")
    cn.add_argument("container_id")
    cn.set_defaults(command="containers-net")

    cr = csub.add_parser("restore", help="Restore a container from a checkpoint file")
    cr.add_argument("checkpoint_file", help="Path to the checkpoint JSON file")
    cr.set_defaults(command="containers-restore")

    cd = csub.add_parser("diff", help="Compare two checkpoint files")
    cd.add_argument("checkpoint_a", help="Path to first checkpoint JSON")
    cd.add_argument("checkpoint_b", help="Path to second checkpoint JSON")
    cd.set_defaults(command="containers-diff")

    ce = csub.add_parser("events", help="Show container lifecycle events")
    ce.add_argument("--tail", type=int, default=None,
                    help="Show only the last N events")
    ce.add_argument("--container", default=None,
                    help="Filter by container ID")
    ce.add_argument("--kind", default=None,
                    help="Filter by event kind (created, started, etc.)")
    ce.set_defaults(command="containers-events")

    ch = csub.add_parser("health", help="Show container health status")
    ch.add_argument("container_id")
    ch.set_defaults(command="containers-health")

    crl = csub.add_parser("limits", help="Show resource usage vs limits with alerts")
    crl.add_argument("container_id")
    crl.set_defaults(command="containers-limits")

    csc = csub.add_parser("sched", help="Show/set scheduling parameters (nice, CPU affinity)")
    csc.add_argument("container_id")
    csc.add_argument("--nice", type=int, default=None,
                     help="Set nice value (-20 to 19)")
    csc.add_argument("--affinity", nargs="+", type=int, default=None,
                     help="Set CPU core affinity (e.g. --affinity 0 1)")
    csc.set_defaults(command="containers-sched")

    cnp = csub.add_parser("netpolicy", help="Show network policy rules for a container")
    cnp.add_argument("container_id")
    cnp.set_defaults(command="containers-netpolicy")

    cso = csub.add_parser("start-ordered", help="Start containers in dependency order")
    cso.add_argument("container_ids", nargs="+",
                     help="Container IDs to start (in dependency order)")
    cso.set_defaults(command="containers-start-ordered")

    cst = csub.add_parser("stop-ordered", help="Stop containers in reverse dependency order")
    cst.add_argument("container_ids", nargs="+",
                     help="Container IDs to stop")
    cst.set_defaults(command="containers-stop-ordered")

    cdg = csub.add_parser("dep-graph", help="Show container dependency graph")
    cdg.add_argument("container_ids", nargs="*",
                     help="Container IDs to include (default: all)")
    cdg.set_defaults(command="containers-dep-graph")

    cri = csub.add_parser("restart-info", help="Show restart policy for a container")
    cri.add_argument("container_id")
    cri.set_defaults(command="containers-restart-info")

    csr = csub.add_parser("restart-set", help="Set restart policy for a container")
    csr.add_argument("container_id")
    csr.add_argument("--policy", required=True,
                     choices=["no", "always", "on-failure"],
                     help="Restart policy")
    csr.add_argument("--max-retries", type=int, default=None,
                     help="Max restart attempts (0 = unlimited)")
    csr.add_argument("--delay", type=float, default=None,
                     help="Seconds between restart attempts")
    csr.set_defaults(command="containers-restart-set")

    ces = csub.add_parser("env-set", help="Set an environment variable")
    ces.add_argument("container_id")
    ces.add_argument("key", help="Variable name")
    ces.add_argument("value", help="Variable value")
    ces.set_defaults(command="containers-env-set")

    ceu = csub.add_parser("env-unset", help="Remove an environment variable")
    ceu.add_argument("container_id")
    ceu.add_argument("key", help="Variable name")
    ceu.set_defaults(command="containers-env-unset")

    cel = csub.add_parser("env-list", help="List environment variables")
    cel.add_argument("container_id")
    cel.set_defaults(command="containers-env-list")

    cse = csub.add_parser("snapshot-export", help="Export container checkpoint as tar.gz")
    cse.add_argument("container_id")
    cse.add_argument("--path", dest="export_path", default=None,
                     help="Output archive path (auto-generated if omitted)")
    cse.set_defaults(command="containers-snapshot-export")

    csi = csub.add_parser("snapshot-import", help="Import checkpoint from tar.gz archive")
    csi.add_argument("archive_path", help="Path to the tar.gz archive")
    csi.set_defaults(command="containers-snapshot-import")

    crh = csub.add_parser("resource-history", help="Show resource usage history")
    crh.add_argument("container_id")
    crh.add_argument("--tail", type=int, default=None,
                     help="Show only last N samples")
    crh.set_defaults(command="containers-resource-history")

    crr = csub.add_parser("resource-record", help="Take a single resource sample")
    crr.add_argument("container_id")
    crr.set_defaults(command="containers-resource-record")

    crrs = csub.add_parser("resource-record-start", help="Start periodic resource recording")
    crrs.add_argument("container_id")
    crrs.add_argument("--interval", type=float, default=5.0,
                      help="Seconds between samples (default: 5.0)")
    crrs.set_defaults(command="containers-resource-record-start")

    crrp = csub.add_parser("resource-record-stop", help="Stop periodic resource recording")
    crrp.add_argument("container_id")
    crrp.set_defaults(command="containers-resource-record-stop")

    cul = csub.add_parser("update-limits", help="Update resource limits at runtime")
    cul.add_argument("container_id")
    cul.add_argument("--memory", type=int, default=None,
                     help="New memory limit in MiB")
    cul.add_argument("--pids", type=int, default=None,
                     help="New PID limit")
    cul.add_argument("--cpu-quota", type=int, default=None,
                     help="New CPU quota in us (0=unlimited)")
    cul.set_defaults(command="containers-update-limits")

    cls = csub.add_parser("label-set", help="Set a label on a container")
    cls.add_argument("container_id")
    cls.add_argument("key", help="Label key")
    cls.add_argument("value", help="Label value")
    cls.set_defaults(command="containers-label-set")

    clu = csub.add_parser("label-unset", help="Remove a label from a container")
    clu.add_argument("container_id")
    clu.add_argument("key", help="Label key")
    clu.set_defaults(command="containers-label-unset")

    cll = csub.add_parser("label-list", help="List labels for a container")
    cll.add_argument("container_id")
    cll.set_defaults(command="containers-label-list")

    clf = csub.add_parser("label-filter", help="Find containers by labels")
    clf.add_argument("labels", nargs="+",
                     help="Key=value pairs to filter by")
    clf.set_defaults(command="containers-label-filter")

    ccg = csub.add_parser("cgroup2-status", help="Show cgroup2 status for a container")
    ccg.add_argument("container_id")
    ccg.set_defaults(command="containers-cgroup2-status")

    cve = csub.add_parser("verify-enforcement", help="Verify resource limits are enforced")
    cve.add_argument("container_id")
    cve.set_defaults(command="containers-verify-enforcement")

    clock = csub.add_parser("lock", help="Acquire exclusive lock on a container")
    clock.add_argument("container_id")
    clock.add_argument("--non-blocking", action="store_true",
                       default=False, help="Fail if lock is held")
    clock.set_defaults(command="containers-lock")

    culock = csub.add_parser("unlock", help="Release lock on a container")
    culock.add_argument("container_id")
    culock.set_defaults(command="containers-unlock")

    clocks = csub.add_parser("locks", help="List all held locks")
    clocks.set_defaults(command="containers-locks")

    ca = csub.add_parser("alert-history", help="Show alert history for a container")
    ca.add_argument("container_id")
    ca.add_argument("--tail", type=int, default=None,
                    help="Show only last N alerts")
    ca.add_argument("--resource", default=None,
                    help="Filter by resource (memory, pid, cpu_throttle)")
    ca.set_defaults(command="containers-alert-history")

    cac = csub.add_parser("alert-clear", help="Clear alert history")
    cac.add_argument("container_id")
    cac.set_defaults(command="containers-alert-clear")

    cat = csub.add_parser("alert-thresholds", help="Set alert thresholds")
    cat.add_argument("container_id")
    cat.add_argument("--memory-warning", type=float, default=None)
    cat.add_argument("--memory-critical", type=float, default=None)
    cat.add_argument("--pid-warning", type=float, default=None)
    cat.add_argument("--pid-critical", type=float, default=None)
    cat.add_argument("--cpu-throttle", type=float, default=None)
    cat.set_defaults(command="containers-alert-thresholds")

    # Enhanced alert management commands
    ack = csub.add_parser("alert-acknowledge", help="Acknowledge an alert")
    ack.add_argument("container_id")
    ack.add_argument("alert_index", type=int,
                     help="Index of alert to acknowledge")
    ack.add_argument("--by", default="user",
                     help="Who acknowledged")
    ack.set_defaults(command="containers-alert-acknowledge")

    asp = csub.add_parser("alert-suppress", help="Suppress alerts for a resource")
    asp.add_argument("container_id")
    asp.add_argument("resource", help="Resource to suppress")
    asp.add_argument("--level", default=None,
                     help="Specific level (warning/critical). None = all.")
    asp.add_argument("--duration", type=float, default=3600.0,
                     help="Duration in seconds")
    asp.set_defaults(command="containers-alert-suppress")

    aus = csub.add_parser("alert-unsuppress", help="Remove a suppression rule")
    aus.add_argument("container_id")
    aus.add_argument("resource")
    aus.add_argument("--level", default=None)
    aus.set_defaults(command="containers-alert-unsuppress")

    ast = csub.add_parser("alert-statistics", help="Get alert statistics")
    ast.add_argument("container_id")
    ast.set_defaults(command="containers-alert-statistics")

    asl = csub.add_parser("alert-suppressions", help="List active suppressions")
    asl.add_argument("container_id")
    asl.set_defaults(command="containers-alert-suppressions")

    co = csub.add_parser("oom-status", help="Show OOM protection status")
    co.add_argument("container_id")
    co.set_defaults(command="containers-oom-status")

    cos = csub.add_parser("oom-set", help="Update OOM protection settings")
    cos.add_argument("container_id")
    cos.add_argument("--score-adj", type=int, default=None,
                     dest="oom_score_adj",
                     help="OOM score adjustment (-1000 to 1000)")
    cos.add_argument("--disable-kill", dest="oom_kill_disable",
                     action="store_true", default=None,
                     help="Disable OOM killer")
    cos.add_argument("--swap-max", type=int, default=None,
                     dest="memory_swap_max",
                     help="Max swap in bytes (0=no swap)")
    cos.set_defaults(command="containers-oom-set")

    coe = csub.add_parser("oom-events", help="Show OOM events")
    coe.add_argument("container_id")
    coe.add_argument("--tail", type=int, default=None,
                     help="Show only last N events")
    coe.set_defaults(command="containers-oom-events")

    cd = csub.add_parser("dashboard", help="Show resource dashboard")
    cd.add_argument("container_id", nargs="?", default=None,
                    help="Container ID (omit for all containers)")
    cd.set_defaults(command="containers-dashboard")

    ce = csub.add_parser("export-history", help="Export resource history to file")
    ce.add_argument("container_id")
    ce.add_argument("output_path", help="Output file path")
    ce.add_argument("--format", default="json",
                    choices=["json", "csv"], help="Export format")
    ce.add_argument("--tail", type=int, default=None,
                    help="Export only last N samples")
    ce.set_defaults(command="containers-export-history")

    ces = csub.add_parser("export-snapshot", help="Export full container snapshot")
    ces.add_argument("container_id")
    ces.add_argument("output_path", help="Output file path")
    ces.set_defaults(command="containers-export-snapshot")

    crp = csub.add_parser("resource-profile",
                          help="Per-process resource breakdown")
    crp.add_argument("container_id")
    crp.set_defaults(command="resource-profile")

    crph = csub.add_parser("resource-profile-history",
                           help="Resource profiling history")
    crph.add_argument("container_id")
    crph.add_argument("--tail", type=int, default=None,
                      help="Show only last N samples")
    crph.set_defaults(command="resource-profile-history")

    crpt = csub.add_parser("resource-profile-top",
                           help="Top processes by resource")
    crpt.add_argument("container_id")
    crpt.add_argument("--resource", default="rss_bytes",
                      choices=["rss_bytes", "cpu_time_s",
                               "io_read_bytes", "io_write_bytes"],
                      help="Resource to rank by")
    crpt.add_argument("--top-n", type=int, default=5,
                      help="Number of top consumers")
    crpt.set_defaults(command="resource-profile-top")

    bs = csub.add_parser("batch-start",
                         help="Start multiple containers by filter")
    bs.add_argument("--labels",
                    help="Label filter (key=val,key=val)")
    bs.add_argument("--name-pattern",
                    help="Substring match on container name")
    bs.add_argument("--container-ids",
                    help="Comma-separated container IDs")
    bs.set_defaults(command="batch-start")

    bstop = csub.add_parser("batch-stop",
                            help="Stop multiple containers by filter")
    bstop.add_argument("--labels",
                       help="Label filter (key=val,key=val)")
    bstop.add_argument("--name-pattern",
                       help="Substring match on container name")
    bstop.add_argument("--container-ids",
                       help="Comma-separated container IDs")
    bstop.add_argument("--timeout", type=float, default=10.0,
                       help="Seconds to wait before SIGKILL")
    bstop.set_defaults(command="batch-stop")

    bk = csub.add_parser("batch-kill",
                         help="Force-kill multiple containers by filter")
    bk.add_argument("--labels",
                    help="Label filter (key=val,key=val)")
    bk.add_argument("--name-pattern",
                    help="Substring match on container name")
    bk.add_argument("--container-ids",
                    help="Comma-separated container IDs")
    bk.set_defaults(command="batch-kill")

    wh = sub.add_parser("webhooks", help="Manage webhooks")
    whsub = wh.add_subparsers(dest="webhook_cmd", required=True)

    whr = whsub.add_parser("register", help="Register a webhook")
    whr.add_argument("url", help="Webhook URL")
    whr.add_argument("--events", nargs="+", default=None,
                     help="Event types to subscribe to")
    whr.add_argument("--secret", default=None,
                     help="HMAC secret for payload signing")
    whr.add_argument("--filter", dest="container_filter", default=None,
                     help="Filter by container ID")
    whr.set_defaults(command="webhook-register")

    whu = whsub.add_parser("unregister", help="Unregister a webhook")
    whu.add_argument("webhook_id")
    whu.set_defaults(command="webhook-unregister")

    whl = whsub.add_parser("list", help="List webhooks")
    whl.set_defaults(command="webhook-list")

    whe = whsub.add_parser("enable", help="Enable a webhook")
    whe.add_argument("webhook_id")
    whe.set_defaults(command="webhook-enable")

    whd = whsub.add_parser("disable", help="Disable a webhook")
    whd.add_argument("webhook_id")
    whd.set_defaults(command="webhook-disable")

    sla = sub.add_parser("sla", help="Manage SLA (service level agreements)")
    slasub = sla.add_subparsers(dest="sla_cmd", required=True)

    slac = slasub.add_parser("check", help="Check SLA compliance")
    slac.add_argument("container_id")
    slac.set_defaults(command="sla-check")

    slav = slasub.add_parser("violations", help="Show SLA violations")
    slav.add_argument("container_id")
    slav.add_argument("--tail", type=int, default=None,
                      help="Show only last N violations")
    slav.set_defaults(command="sla-violations")

    slas = slasub.add_parser("set", help="Set SLA configuration")
    slas.add_argument("container_id")
    slas.add_argument("--uptime", type=float, default=None,
                      dest="uptime_target",
                      help="Uptime target percentage")
    slas.add_argument("--max-restarts", type=int, default=None,
                      dest="max_restart_count",
                      help="Max restarts before SLA breach")
    slas.add_argument("--no-alert", dest="alert_on_breach",
                      action="store_false", default=None,
                      help="Disable alerts on SLA breach")
    slas.set_defaults(command="sla-set")

    # SLA escalation commands
    esc = sub.add_parser("sla-escalation", help="Manage SLA breach escalation")
    escsub = esc.add_subparsers(dest="esc_cmd", required=True)

    escp = escsub.add_parser("policy", help="Set escalation policy")
    escp.add_argument("container_id")
    # Simple policy: "1:alert,3:alert+webhook,5:restart,10:page"
    escp.add_argument("--policy", default=None,
                      help="Escalation policy string (e.g., '1:alert,3:webhook,5:restart')")
    escp.set_defaults(command="sla-escalation-policy")

    escs = escsub.add_parser("status", help="Get escalation status")
    escs.add_argument("container_id")
    escs.set_defaults(command="sla-escalation-status")

    escr = escsub.add_parser("reset", help="Reset escalation state")
    escr.add_argument("container_id")
    escr.set_defaults(command="sla-escalation-reset")

    esch = escsub.add_parser("history", help="Get escalation history")
    esch.add_argument("container_id")
    esch.add_argument("--tail", type=int, default=None)
    esch.set_defaults(command="sla-escalation-history")

    bill = sub.add_parser("billing", help="Manage billing (cost tracking)")
    billsub = bill.add_subparsers(dest="billing_cmd", required=True)

    brs = billsub.add_parser("rates-set", help="Set billing rates")
    brs.add_argument("--memory", type=float, default=None,
                     dest="memory_mb_per_hour",
                     help="Cost per GB-hour of memory")
    brs.add_argument("--cpu", type=float, default=None,
                     dest="cpu_per_hour",
                     help="Cost per vCPU-hour")
    brs.add_argument("--pid", type=float, default=None,
                     dest="pid_per_hour",
                     help="Cost per PID-hour")
    brs.add_argument("--storage", type=float, default=None,
                     dest="storage_mb_per_hour",
                     help="Cost per GB-hour of storage")
    brs.set_defaults(command="billing-rates-set")

    brg = billsub.add_parser("rates-get", help="Get billing rates")
    brg.set_defaults(command="billing-rates-get")

    brec = billsub.add_parser("record", help="Record current usage")
    brec.add_argument("container_id")
    brec.set_defaults(command="billing-record")

    brecs = billsub.add_parser("records", help="Get billing records")
    brecs.add_argument("container_id")
    brecs.add_argument("--tail", type=int, default=None,
                       help="Show only last N records")
    brecs.set_defaults(command="billing-records")

    bsum = billsub.add_parser("summary", help="Get billing summary")
    bsum.add_argument("container_id", nargs="?", default=None,
                      help="Container ID (omit for all)")
    bsum.set_defaults(command="billing-summary")

    # Cost budget commands
    cb = sub.add_parser("cost-budget", help="Cost budget limits and alerts")
    cbsub = cb.add_subparsers(dest="budget_cmd", required=True)

    cbc = cbsub.add_parser("configure", help="Configure cost budget limits")
    cbc.add_argument("container_id")
    cbc.add_argument("--daily", type=float, default=None,
                     dest="daily_limit", help="Daily cost limit ($)")
    cbc.add_argument("--weekly", type=float, default=None,
                     dest="weekly_limit", help="Weekly cost limit ($)")
    cbc.add_argument("--monthly", type=float, default=None,
                     dest="monthly_limit", help="Monthly cost limit ($)")
    cbc.add_argument("--hard", type=float, default=None,
                     dest="hard_limit", help="Hard limit ($)")
    cbc.add_argument("--threshold", type=float, default=80.0,
                     dest="alert_threshold_pct", help="Alert threshold %%")
    cbc.set_defaults(command="cost-budget-configure")

    cbk = cbsub.add_parser("check", help="Check budget status")
    cbk.add_argument("container_id")
    cbk.set_defaults(command="cost-budget-check")

    cba = cbsub.add_parser("alerts", help="Get cost alert history")
    cba.add_argument("container_id")
    cba.add_argument("--tail", type=int, default=None)
    cba.set_defaults(command="cost-alerts")

    cbg = cbsub.add_parser("config", help="Get budget configuration")
    cbg.add_argument("container_id")
    cbg.set_defaults(command="cost-budget-config")

    # Auto-scaling commands
    asp = sub.add_parser("autoscale", help="Auto-scaling management")
    asub = asp.add_subparsers(dest="autoscale_cmd", required=True)

    asc = asub.add_parser("configure", help="Configure auto-scaling")
    asc.add_argument("container_id")
    asc.add_argument("--enabled", action="store_true", default=True)
    asc.add_argument("--min-memory", type=int, default=None,
                     dest="min_memory_mb", help="Min memory MB")
    asc.add_argument("--max-memory", type=int, default=None,
                     dest="max_memory_mb", help="Max memory MB")
    asc.add_argument("--target-memory", type=float, default=70.0,
                     dest="target_memory_pct", help="Target memory %%")
    asc.add_argument("--min-cpu", type=int, default=None,
                     dest="min_cpu_quota", help="Min CPU quota")
    asc.add_argument("--max-cpu", type=int, default=None,
                     dest="max_cpu_quota", help="Max CPU quota")
    asc.add_argument("--target-cpu", type=float, default=70.0,
                     dest="target_cpu_pct", help="Target CPU %%")
    asc.add_argument("--scale-up-cooldown", type=float, default=300.0,
                     dest="scale_up_cooldown_s")
    asc.add_argument("--scale-down-cooldown", type=float, default=600.0,
                     dest="scale_down_cooldown_s")
    asc.set_defaults(command="autoscale-configure")

    ass = asub.add_parser("status", help="Get auto-scaling status")
    ass.add_argument("container_id")
    ass.set_defaults(command="autoscale-status")

    asa = asub.add_parser("apply", help="Apply auto-scaling now")
    asa.add_argument("container_id")
    asa.set_defaults(command="autoscale-apply")

    asd = asub.add_parser("disable", help="Disable auto-scaling")
    asd.add_argument("container_id")
    asd.set_defaults(command="autoscale-disable")

    ase = asub.add_parser("events", help="Get scaling events")
    ase.add_argument("container_id")
    ase.add_argument("--tail", type=int, default=None)
    ase.set_defaults(command="autoscale-events")

    # Health check commands
    hc = sub.add_parser("health", help="Health check management")
    hcsub = hc.add_subparsers(dest="health_cmd", required=True)

    hcc = hcsub.add_parser("configure", help="Configure health checks")
    hcc.add_argument("container_id")
    hcc.add_argument("--cmd", nargs="+", default=None,
                     help="Health check command")
    hcc.add_argument("--interval", type=float, default=None,
                     help="Check interval in seconds")
    hcc.add_argument("--timeout", type=float, default=None,
                     help="Check timeout in seconds")
    hcc.add_argument("--retries", type=int, default=None,
                     help="Consecutive failures before unhealthy")
    hcc.add_argument("--auto-restart", action="store_true", default=True,
                     help="Auto-restart on unhealthy")
    hcc.add_argument("--no-auto-restart", dest="auto_restart",
                     action="store_false",
                     help="Disable auto-restart")
    hcc.add_argument("--max-restarts", type=int, default=3,
                     dest="max_auto_restarts",
                     help="Max auto-restart attempts")
    hcc.set_defaults(command="health-configure")

    hct = hcsub.add_parser("trigger", help="Trigger a health check now")
    hct.add_argument("container_id")
    hct.set_defaults(command="health-trigger")

    hcg = hcsub.add_parser("config", help="Get health check config")
    hcg.add_argument("container_id")
    hcg.set_defaults(command="health-config")

    hcr = hcsub.add_parser("restart-reset", help="Reset restart counter")
    hcr.add_argument("container_id")
    hcr.set_defaults(command="health-restart-reset")

    hch = hcsub.add_parser("restart-history", help="Get restart history")
    hch.add_argument("container_id")
    hch.add_argument("--tail", type=int, default=None)
    hch.set_defaults(command="health-restart-history")

    fc = sub.add_parser("forecast", help="Resource usage forecasting")
    fcsub = fc.add_subparsers(dest="forecast_cmd", required=True)

    fcr = fcsub.add_parser("resource", help="Forecast a specific resource")
    fcr.add_argument("container_id")
    fcr.add_argument("--resource", default="memory",
                     choices=["memory", "cpu", "pids"],
                     help="Resource to forecast")
    fcr.add_argument("--horizon", type=float, default=3600.0,
                     dest="horizon_s",
                     help="Forecast horizon in seconds")
    fcr.set_defaults(command="forecast-resource")

    fca = fcsub.add_parser("all", help="Forecast all resources")
    fca.add_argument("container_id")
    fca.set_defaults(command="forecast-all")

    fce = fcsub.add_parser("exhaustion", help="Estimate time to exhaustion")
    fce.add_argument("container_id")
    fce.add_argument("--resource", default="memory",
                     choices=["memory", "cpu", "pids"],
                     help="Resource to check")
    fce.set_defaults(command="forecast-exhaustion")

    # Capacity planning commands
    cap = sub.add_parser("capacity", help="Capacity planning")
    capsub = cap.add_subparsers(dest="capacity_cmd", required=True)

    capc = capsub.add_parser("plan", help="Plan capacity for a container")
    capc.add_argument("container_id")
    capc.add_argument("--horizon", type=int, default=30,
                      dest="horizon_days", help="Planning horizon in days")
    capc.set_defaults(command="capacity-plan")

    capa = capsub.add_parser("plan-all", help="Plan capacity for all containers")
    capa.add_argument("--horizon", type=int, default=30,
                      dest="horizon_days", help="Planning horizon in days")
    capa.set_defaults(command="capacity-plan-all")

    # Network traffic analysis commands
    net = sub.add_parser("network", help="Network traffic analysis")
    netsub = net.add_subparsers(dest="network_cmd", required=True)

    nta = netsub.add_parser("traffic", help="Analyze network traffic")
    nta.add_argument("container_id")
    nta.add_argument("--window", type=float, default=300.0,
                     dest="window_s", help="Analysis window in seconds")
    nta.set_defaults(command="network-traffic")

    nco = netsub.add_parser("connections", help="Get active connections")
    nco.add_argument("container_id")
    nco.set_defaults(command="network-connections")

    nbh = netsub.add_parser("bandwidth-history", help="Get bandwidth history")
    nbh.add_argument("container_id")
    nbh.add_argument("--tail", type=int, default=None)
    nbh.set_defaults(command="network-bandwidth-history")

    # Anomaly detection commands
    anomaly = sub.add_parser("anomaly", help="Detect resource anomalies")
    asub = anomaly.add_subparsers(dest="anomaly_cmd", required=True)

    ad = asub.add_parser("detect", help="Detect anomalies using Z-score")
    ad.add_argument("container_id")
    ad.add_argument("--resource", default="memory",
                    choices=["memory", "cpu", "pids"],
                    help="Resource to analyze")
    ad.add_argument("--window", type=int, default=20,
                    help="Sample window size")
    ad.add_argument("--sensitivity", type=float, default=2.0,
                    help="Std-dev threshold for outlier")
    ad.set_defaults(command="anomaly-detect")

    aa = asub.add_parser("detect-all", help="Detect anomalies across all resources")
    aa.add_argument("container_id")
    aa.add_argument("--window", type=int, default=20)
    aa.add_argument("--sensitivity", type=float, default=2.0)
    aa.set_defaults(command="anomaly-detect-all")

    asp = asub.add_parser("spike", help="Detect sudden spikes")
    asp.add_argument("container_id")
    asp.add_argument("--resource", default="memory",
                     choices=["memory", "cpu", "pids"])
    asp.add_argument("--threshold", type=float, default=50.0,
                     help="Min %% change to flag as spike")
    asp.set_defaults(command="anomaly-spike")

    at = asub.add_parser("trend", help="Analyze anomaly trend")
    at.add_argument("container_id")
    at.add_argument("--resource", default="memory",
                    choices=["memory", "cpu", "pids"])
    at.add_argument("--window", type=int, default=20)
    at.set_defaults(command="anomaly-trend")

    # Resource comparison commands
    compare = sub.add_parser("compare", help="Compare container resources")
    cmpsub = compare.add_subparsers(dest="compare_cmd", required=True)

    cc = cmpsub.add_parser("resources", help="Compare specific resource across containers")
    cc.add_argument("container_ids", nargs="+", help="Container IDs to compare")
    cc.add_argument("--resource", default="memory",
                    choices=["memory", "cpu", "pids"],
                    help="Resource to compare")
    cc.set_defaults(command="compare-resources")

    ca = cmpsub.add_parser("all", help="Compare all resources across containers")
    ca.add_argument("container_ids", nargs="+", help="Container IDs to compare")
    ca.set_defaults(command="compare-all")

    cr = cmpsub.add_parser("relative", help="Show container's relative usage vs peers")
    cr.add_argument("container_id")
    cr.add_argument("--resource", default="memory",
                    choices=["memory", "cpu", "pids"])
    cr.set_defaults(command="compare-relative")

    ct = cmpsub.add_parser("top", help="Find top N resource consumers")
    ct.add_argument("--resource", default="memory",
                    choices=["memory", "cpu", "pids"])
    ct.add_argument("--top", type=int, default=5,
                    help="Number of top consumers")
    ct.set_defaults(command="compare-top")

    # Recommendation commands
    rec = sub.add_parser("recommend", help="Get resource optimization recommendations")
    recsub = rec.add_subparsers(dest="recommend_cmd", required=True)

    rc = recsub.add_parser("get", help="Get recommendations for a container")
    rc.add_argument("container_id")
    rc.set_defaults(command="recommend-get")

    ra = recsub.add_parser("all", help="Get recommendations for all containers")
    ra.set_defaults(command="recommend-all")

    rcat = recsub.add_parser("category", help="Get recommendations by category")
    rcat.add_argument("container_id")
    rcat.add_argument("--category", default="memory",
                      choices=["memory", "cpu", "pids", "general"],
                      help="Category to filter")
    rcat.set_defaults(command="recommend-category")

    quotas = sub.add_parser("quotas", help="Manage resource quotas")
    qsub = quotas.add_subparsers(dest="quota_cmd", required=True)

    qs = qsub.add_parser("set", help="Set a resource quota for an owner")
    qs.add_argument("owner", help="Owner identifier (user or group)")
    qs.add_argument("--memory", type=int, default=None,
                    help="Max total memory in MiB")
    qs.add_argument("--pids", type=int, default=None,
                    help="Max total PIDs")
    qs.add_argument("--containers", type=int, default=None,
                    help="Max concurrent containers")
    qs.set_defaults(command="quotas-set")

    qg = qsub.add_parser("get", help="Get quota for an owner")
    qg.add_argument("owner")
    qg.set_defaults(command="quotas-get")

    ql = qsub.add_parser("list", help="List all quotas")
    ql.set_defaults(command="quotas-list")

    qd = qsub.add_parser("delete", help="Delete a quota")
    qd.add_argument("owner")
    qd.set_defaults(command="quotas-delete")

    qu = qsub.add_parser("usage", help="Show quota usage for an owner")
    qu.add_argument("owner")
    qu.set_defaults(command="quotas-usage")

    images = sub.add_parser("images", help="Manage base images for overlays")
    isub = images.add_subparsers(dest="image_cmd", required=True)

    il = isub.add_parser("list", help="List available base images")
    il.add_argument("--base-dir", default=None,
                    help="Image directory (default: ./images)")
    il.set_defaults(command="images-list")

    ir = isub.add_parser("remove", help="Remove a base image")
    ir.add_argument("path", help="Path to the image directory")
    ir.set_defaults(command="images-remove")

    ie = isub.add_parser("export", help="Export a base image as a tar archive")
    ie.add_argument("image_path", help="Path to the image directory")
    ie.add_argument("--tar-path", default=None,
                    help="Output tar file path (auto-generated if omitted)")
    ie.set_defaults(command="images-export")

    im = isub.add_parser("import", help="Import an image from a tar archive")
    im.add_argument("tar_path", help="Path to the tar archive")
    im.add_argument("--dest-dir", default=None,
                    help="Destination directory (default: ./images)")
    im.add_argument("--name", default=None,
                    help="Override image directory name")
    im.set_defaults(command="images-import")

    vault = sub.add_parser(
        "vault", help="NyVault storage service ops (ADR-0022) — "
                      "capability-gated named volumes")
    vsub = vault.add_subparsers(dest="vault_cmd", required=True)

    vc = vsub.add_parser("create", help="Create a named volume")
    vc.add_argument("name")
    vc.set_defaults(command="vault-volume-create")

    vo = vsub.add_parser(
        "open", help="Open a volume by id (or --name) — returns a handle")
    vo.add_argument("volume_id", nargs="?", default="",
                    help="Volume id (or use --name)")
    vo.add_argument("--name", default="", help="Open by volume name")
    vo.set_defaults(command="vault-volume-open")

    vl = vsub.add_parser("list", help="List the volumes you may open")
    vl.set_defaults(command="vault-volume-list")

    vg = vsub.add_parser(
        "grant", help="CREATOR/OPERATOR-ONLY: let another container "
                       "open the volume (ADR-0022 access matrix)")
    vg.add_argument("volume_id", nargs="?", default="",
                    help="Volume id (or use --name)")
    vg.add_argument("--name", default="", help="Volume name")
    vg.add_argument("container", help="The container id to grant access to")
    vg.add_argument("--path", default="",
                    help="Limit the grant to a subtree, e.g. /assets "
                         "(default: whole volume)")
    vg.set_defaults(command="vault-volume-grant")

    vrk2 = vsub.add_parser(
        "revoke", help="CREATOR/OPERATOR-ONLY: withdraw a container's "
                        "volume grant (live handles stay valid until closed)")
    vrk2.add_argument("volume_id", nargs="?", default="",
                      help="Volume id (or use --name)")
    vrk2.add_argument("--name", default="", help="Volume name")
    vrk2.add_argument("container", help="The container id to revoke")
    vrk2.set_defaults(command="vault-volume-revoke")

    vgs = vsub.add_parser(
        "grants", help="CREATOR/OPERATOR-ONLY: list a volume's grants")
    vgs.add_argument("volume_id", nargs="?", default="",
                     help="Volume id (or use --name)")
    vgs.add_argument("--name", default="", help="Volume name")
    vgs.set_defaults(command="vault-volume-grants")

    vcl = vsub.add_parser("close", help="Release a handle")
    vcl.add_argument("handle")
    vcl.set_defaults(command="vault-volume-close")

    vw = vsub.add_parser(
        "write", help="Write bytes to a volume path (stdin or --file)")
    vw.add_argument("handle")
    vw.add_argument("path")
    vw.add_argument("--offset", type=int, default=0,
                    help="Write offset (default: 0 — overwrite)")
    vw.add_argument("--file", default="",
                    help="Read the bytes from FILE instead of stdin")
    vw.set_defaults(command="vault-volume-write")

    vr = vsub.add_parser(
        "read", help="Read bytes from a volume path to stdout (or --output)")
    vr.add_argument("handle")
    vr.add_argument("path")
    vr.add_argument("--offset", type=int, default=0,
                    help="Read offset (default: 0)")
    vr.add_argument("--size", type=int, default=MAX_IO_BYTES,
                    help=f"Bytes to read (default: {MAX_IO_BYTES}, the "
                         f"per-call limit — page with --offset)")
    vr.add_argument("--output", default="",
                    help="Write the bytes to FILE instead of stdout")
    vr.set_defaults(command="vault-volume-read")

    vs = vsub.add_parser("snapshot", help="Snapshot a volume")
    vs.add_argument("handle")
    vs.add_argument("name", help="Snapshot id (1..64 chars of "
                                  "[A-Za-z0-9._-])")
    vs.set_defaults(command="vault-volume-snapshot")

    vss = vsub.add_parser("snapshots", help="List a volume's snapshots")
    vss.add_argument("handle")
    vss.set_defaults(command="vault-volume-snapshots")

    vr2 = vsub.add_parser(
        "restore", help="Restore a volume to a snapshot (the snapshot "
                        "table itself is unchanged)")
    vr2.add_argument("handle")
    vr2.add_argument("name", help="Snapshot id to restore to")
    vr2.set_defaults(command="vault-volume-restore")

    vsd = vsub.add_parser(
        "snapshot-delete", help="Delete a snapshot from a volume (the "
                                 "point-in-time copy is dropped)")
    vsd.add_argument("handle")
    vsd.add_argument("name", help="Snapshot id to delete")
    vsd.set_defaults(command="vault-volume-snapshot-delete")

    vd = vsub.add_parser(
        "delete", help="Delete a volume by id (or --name) — crypto-shreds "
                       "its wrapped DEK and reclaims the backing image")
    vd.add_argument("volume_id", nargs="?", default="",
                    help="Volume id (or use --name)")
    vd.add_argument("--name", default="", help="Delete by volume name")
    vd.set_defaults(command="vault-volume-delete")

    vrk = vsub.add_parser(
        "rekey", help="OPERATOR-ONLY: rotate the vault KEK (ADR-0023) "
                       "without re-encrypting any block — re-wraps every "
                       "volume's DEK with the new key")
    vrk.add_argument("--new-passphrase", default="",
                     help="The new unlock secret (or set "
                          "NYRQIS_VAULT_REKEY_PASSPHRASE)")
    vrk.add_argument("--new-key-file", default="",
                     help="Where to write the new KEK envelope (the "
                          "daemon's salt is the one that matches — the "
                          "reply carries it)")
    vrk.set_defaults(command="vault-volume-rekey")

    vq = vsub.add_parser(
        "quota-set", help="CREATOR/OPERATOR-ONLY: set (or clear) a "
                           "per-container byte quota on a volume "
                           "(ADR-0022 accounting)")
    vq.add_argument("volume_id", nargs="?", default="",
                    help="Volume id (or use --name)")
    vq.add_argument("--name", default="", help="Volume name")
    vq.add_argument("container", help="The container id to quota")
    vq.add_argument("--bytes", type=int, default=None,
                    help="Byte quota (omit with --unlimited to clear)")
    vq.add_argument("--unlimited", action="store_true",
                    help="Clear the quota (unlimited bytes)")
    vq.add_argument("--path", default="",
                    help="Scope the quota to a subtree, e.g. /assets "
                         "(0.14.19; default: whole volume)")
    vq.set_defaults(command="vault-volume-quota-set")

    vqg = vsub.add_parser(
        "quota-get", help="CREATOR/OPERATOR-ONLY: a volume's per-"
                           "container quotas and accounted usage")
    vqg.add_argument("volume_id", nargs="?", default="",
                     help="Volume id (or use --name)")
    vqg.add_argument("--name", default="", help="Volume name")
    vqg.set_defaults(command="vault-volume-quota-get")

    vu = vsub.add_parser(
        "usage", help="Per-container accounted usage for a volume "
                      "(any opener — logical bytes + the volume-wide "
                      "physical block-store figure)")
    vu.add_argument("volume_id", nargs="?", default="",
                    help="Volume id (or use --name)")
    vu.add_argument("--name", default="", help="Volume name")
    vu.set_defaults(command="vault-volume-usage")

    vsm = vsub.add_parser(
        "summary", help="OPERATOR-ONLY: the whole-vault aggregate — "
                         "per-volume logical + physical bytes and "
                         "consumer counts")
    vsm.set_defaults(command="vault-volume-summary")

    vev = vsub.add_parser(
        "events", help="OPERATOR-ONLY: the event ring — quota warning-"
                        "level transitions, EDQUOT rejections, and "
                        "grant/revoke actions, newest first (bounded "
                        "diagnostics, persisted with the registry)")
    vev.set_defaults(command="vault-volume-events")

    vi = vsub.add_parser(
        "init", help="LOCAL: initialize the vault KEK envelope (ADR-0023) "
                      "for the daemon's --vault-key-file")
    vi.add_argument("key_file", help="Path to write the envelope to")
    vi.add_argument("--passphrase", default="",
                    help="The unlock secret (or set "
                         "NYRQIS_VAULT_PASSPHRASE)")
    vi.set_defaults(command="vault-vault-init")

    vm = vsub.add_parser(
        "mount", help="Mount a volume as a FUSE passthrough (ADR-0022) — "
                       "its ops are storage-service CALLs; requires "
                       "fusepy + /dev/fuse")
    vm.add_argument("volume_id", nargs="?", default="",
                    help="Volume id (or use --name)")
    vm.add_argument("--name", default="", help="Mount by volume name")
    vm.add_argument("mount_point", help="Mount point (created if missing)")
    vm.set_defaults(command="vault-volume-mount")

    app = sub.add_parser(
        "app", help="Install, list, and launch cross-platform apps "
                    "(Android APK / Windows .exe/.msi)")
    asub = app.add_subparsers(dest="app_cmd", required=True)

    ai = asub.add_parser(
        "install", help="Install an app from an APK or EXE/MSI file")
    ai.add_argument("app_path", help="Path to the .apk / .exe / .msi file")
    ai.add_argument("--name", default="",
                    help="Override the app display name")
    ai.add_argument("--sandbox", action="store_true", default=True,
                    help="Run in a sandboxed container (default: true)")
    ai.add_argument("--no-sandbox", dest="sandbox", action="store_false",
                    help="Do not sandbox the app")
    ai.set_defaults(command="app-install")

    al = asub.add_parser("list", help="List installed apps")
    al.set_defaults(command="app-list")

    ar = asub.add_parser(
        "launch", help="Launch an installed app by id")
    ar.add_argument("app_id", help="The installed app id")
    ar.set_defaults(command="app-launch")

    at = asub.add_parser(
        "terminate", help="Terminate a running app")
    at.add_argument("app_id", help="The app id to terminate")
    at.set_defaults(command="app-terminate")

    nui = sub.add_parser(
        "nui", help="NUI (.nstudio) import gate (ADR-0025) — validate or "
                     "load a NyForge design on the daemon (operator)")
    nsub = nui.add_subparsers(dest="nui_cmd", required=True)

    nv = nsub.add_parser(
        "validate", help="Validate a design against the NUI contract "
                         "tables (vocabulary, events, actions, bindings, "
                         "schema version)")
    nv.add_argument("--file", default="",
                    help="Read the .nstudio document from FILE "
                         "(default: stdin)")
    nv.set_defaults(command="nui-validate")

    nl = nsub.add_parser(
        "load", help="Validate AND persist the design as the daemon's "
                      "shell UI (<state-dir>/ui/shell.nstudio); needs a "
                      "daemon --state-file")
    nl.add_argument("--file", default="",
                    help="Read the .nstudio document from FILE "
                         "(default: stdin)")
    nl.set_defaults(command="nui-load")

    nc = nsub.add_parser(
        "current", help="Report the daemon's loaded shell design: "
                        "nothing loaded, or the persisted design's "
                        "summary (re-imported through the gate)")
    nc.set_defaults(command="nui-current")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return run(args.command, args)


if __name__ == "__main__":
    sys.exit(main())
