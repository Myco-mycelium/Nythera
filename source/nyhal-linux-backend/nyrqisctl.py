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
    if command == "images-create-layer":
        return {
            "service": "control",
            "op": "image_create_layer",
            "base_path": args.base_path,
            "layer_name": args.layer_name,
            "changes": json.loads(args.changes) if args.changes else None,
        }
    if command == "images-list-layers":
        return {
            "service": "control",
            "op": "image_list_layers",
            "image_path": args.image_path,
        }
    if command == "images-remove-layer":
        return {
            "service": "control",
            "op": "image_remove_layer",
            "image_path": args.image_path,
            "layer_name": args.layer_name,
        }
    if command == "images-diff":
        return {
            "service": "control",
            "op": "image_diff",
            "image_a_path": args.image_a_path,
            "image_b_path": args.image_b_path,
        }
    if command == "registry-pull":
        return {
            "service": "control",
            "op": "registry_pull",
            "registry_url": args.registry_url,
            "image_name": args.image_name,
            "tag": args.tag,
        }
    if command == "registry-push":
        return {
            "service": "control",
            "op": "registry_push",
            "image_path": args.image_path,
            "registry_url": args.registry_url,
            "image_name": args.image_name,
            "tag": args.tag,
        }
    if command == "registry-catalog":
        return {
            "service": "control",
            "op": "registry_catalog",
            "registry_url": args.registry_url,
        }
    if command == "cluster-register-node":
        return {
            "service": "control",
            "op": "cluster_register_node",
            "node_id": args.node_id,
            "node_url": args.node_url,
            "labels": json.loads(args.labels) if args.labels else None,
            "capacity": json.loads(args.capacity) if args.capacity else None,
        }
    if command == "cluster-unregister-node":
        return {
            "service": "control",
            "op": "cluster_unregister_node",
            "node_id": args.node_id,
        }
    if command == "cluster-heartbeat":
        return {
            "service": "control",
            "op": "cluster_heartbeat",
            "node_id": args.node_id,
            "status": args.status,
        }
    if command == "cluster-nodes":
        return {
            "service": "control",
            "op": "cluster_nodes",
        }
    if command == "cluster-status":
        return {
            "service": "control",
            "op": "cluster_status",
        }
    if command == "cluster-schedule":
        return {
            "service": "control",
            "op": "cluster_schedule",
            "container_config": json.loads(args.container_config) if args.container_config else {},
            "strategy": args.strategy,
        }
    if command == "cluster-containers":
        return {
            "service": "control",
            "op": "cluster_containers",
        }
    if command == "cluster-drain-node":
        return {
            "service": "control",
            "op": "cluster_drain_node",
            "node_id": args.node_id,
            "timeout_s": args.timeout_s,
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
    if command == "baseline-record":
        return {
            "service": "control",
            "op": "baseline_record",
            "container_id": args.container_id,
        }
    if command == "baseline-get":
        return {
            "service": "control",
            "op": "baseline_get",
            "container_id": args.container_id,
        }
    if command == "baseline-compare":
        return {
            "service": "control",
            "op": "baseline_compare",
            "container_id": args.container_id,
            "threshold_sigma": args.threshold,
        }
    if command == "baseline-clear":
        return {
            "service": "control",
            "op": "baseline_clear",
            "container_id": args.container_id,
        }
    if command == "process-kill":
        return {
            "service": "control",
            "op": "process_kill",
            "container_id": args.container_id,
            "pid": args.pid,
            "signal": args.signal,
        }
    if command == "process-list":
        return {
            "service": "control",
            "op": "process_list",
            "container_id": args.container_id,
        }
    if command == "process-signal-all":
        return {
            "service": "control",
            "op": "process_signal_all",
            "container_id": args.container_id,
            "signal": args.signal,
        }
    if command == "snapshot-schedule-set":
        return {
            "service": "control",
            "op": "snapshot_schedule_set",
            "container_id": args.container_id,
            "interval": args.interval,
            "max_snapshots": args.max_snapshots,
        }
    if command == "snapshot-schedule-get":
        return {
            "service": "control",
            "op": "snapshot_schedule_get",
            "container_id": args.container_id,
        }
    if command == "snapshot-schedule-disable":
        return {
            "service": "control",
            "op": "snapshot_schedule_disable",
            "container_id": args.container_id,
        }
    if command == "snapshot-schedule-run":
        return {
            "service": "control",
            "op": "snapshot_schedule_run",
            "container_id": args.container_id,
        }
    if command == "snapshot-schedule-list":
        return {
            "service": "control",
            "op": "snapshot_schedule_list",
            "container_id": args.container_id,
        }
    if command == "dependency-health":
        return {
            "service": "control",
            "op": "dependency_health",
            "container_id": args.container_id,
        }
    if command == "dependency-health-reverse":
        return {
            "service": "control",
            "op": "dependency_health_reverse",
            "container_id": args.container_id,
        }
    if command == "usage-report":
        payload = {
            "service": "control",
            "op": "usage_report",
        }
        if args.container_ids:
            payload["container_ids"] = args.container_ids.split(",")
        return payload
    if command == "alert-summary":
        return {"service": "control", "op": "alert_summary"}
    if command == "set-cpu-weight":
        return {
            "service": "control",
            "op": "set_cpu_weight",
            "container_id": args.container_id,
            "weight": args.weight,
        }
    if command == "set-io-weight":
        return {
            "service": "control",
            "op": "set_io_weight",
            "container_id": args.container_id,
            "weight": args.weight,
        }
    if command == "get-priority":
        return {
            "service": "control",
            "op": "get_priority",
            "container_id": args.container_id,
        }
    if command == "event-correlate":
        payload = {
            "service": "control",
            "op": "event_correlate",
            "time_window_s": args.window,
        }
        if args.kinds:
            payload["kinds"] = args.kinds.split(",")
        return payload
    if command == "event-timeline":
        payload = {
            "service": "control",
            "op": "event_timeline",
            "time_window_s": args.window,
        }
        if args.container_ids:
            payload["container_ids"] = args.container_ids.split(",")
        return payload
    if command == "network-rule-add":
        return {
            "service": "control",
            "op": "network_rule_add",
            "container_id": args.container_id,
            "direction": args.direction,
            "protocol": args.protocol,
            "port": args.port,
            "source": args.source,
            "action": args.action,
        }
    if command == "network-rule-remove":
        return {
            "service": "control",
            "op": "network_rule_remove",
            "container_id": args.container_id,
            "rule_index": args.rule_index,
        }
    if command == "network-rules-list":
        return {
            "service": "control",
            "op": "network_rules_list",
            "container_id": args.container_id,
        }
    if command == "network-rules-clear":
        return {
            "service": "control",
            "op": "network_rules_clear",
            "container_id": args.container_id,
        }
    if command == "compare-containers":
        payload = {
            "service": "control",
            "op": "compare_containers",
            "container_ids": args.container_ids.split(","),
        }
        if args.metrics:
            payload["metrics"] = args.metrics.split(",")
        return payload
    if command == "check-thresholds":
        return {"service": "control", "op": "check_thresholds"}
    if command == "threshold-status":
        return {"service": "control", "op": "threshold_status"}
    if command == "set-scheduling-priority":
        return {
            "service": "control",
            "op": "set_scheduling_priority",
            "container_id": args.container_id,
            "priority": args.priority,
        }
    if command == "scheduling-queue":
        return {"service": "control", "op": "scheduling_queue"}
    if command == "ready-containers":
        return {"service": "control", "op": "ready_containers"}
    if command == "audit-record":
        return {
            "service": "control",
            "op": "audit_record",
            "container_id": args.container_id,
            "action": args.action,
            "actor": getattr(args, "actor", "operator"),
            "resource": getattr(args, "resource", None),
            "old_value": getattr(args, "old_value", None),
            "new_value": getattr(args, "new_value", None),
            "detail": getattr(args, "detail", ""),
        }
    if command == "audit-log":
        return {
            "service": "control",
            "op": "audit_log",
            "container_id": args.container_id,
            "tail": getattr(args, "tail", None),
            "action": getattr(args, "action", None),
            "actor": getattr(args, "actor", None),
            "resource": getattr(args, "resource", None),
        }
    if command == "audit-summary":
        return {
            "service": "control",
            "op": "audit_summary",
            "container_id": args.container_id,
        }
    if command == "cost-allocate":
        return {
            "service": "control",
            "op": "cost_allocate",
            "container_id": args.container_id,
        }
    if command == "cost-allocate-all":
        return {"service": "control", "op": "cost_allocate_all"}
    if command == "budget-set":
        return {
            "service": "control",
            "op": "budget_set",
            "container_id": args.container_id,
            "memory_mb": getattr(args, "memory_mb", None),
            "cpu_pct": getattr(args, "cpu_pct", None),
            "pids": getattr(args, "pids", None),
            "daily_cost_limit": getattr(args, "daily_cost_limit", None),
            "monthly_cost_limit": getattr(args, "monthly_cost_limit", None),
            "alert_at_pct": getattr(args, "alert_at_pct", 80.0),
        }
    if command == "budget-get":
        return {
            "service": "control",
            "op": "budget_get",
            "container_id": args.container_id,
        }
    if command == "budget-check":
        return {
            "service": "control",
            "op": "budget_check",
            "container_id": args.container_id,
        }
    if command == "budget-check-all":
        return {"service": "control", "op": "budget_check_all"}
    if command == "budget-clear":
        return {
            "service": "control",
            "op": "budget_clear",
            "container_id": args.container_id,
        }
    if command == "remediation-configure":
        return {
            "service": "control",
            "op": "remediation_configure",
            "container_id": args.container_id,
            "on_budget_exceeded": getattr(args, 'on_budget_exceeded', 'alert'),
            "on_threshold_exceeded": getattr(args, 'on_threshold_exceeded', 'alert'),
            "on_oom_risk": getattr(args, 'on_oom_risk', 'alert'),
            "max_restarts": getattr(args, 'max_restarts', 3),
            "cooldown_seconds": getattr(args, 'cooldown_seconds', 300.0),
            "enabled": getattr(args, 'enabled', True),
        }
    if command == "remediation-execute":
        return {
            "service": "control",
            "op": "remediation_execute",
            "container_id": args.container_id,
            "trigger": args.trigger,
            "reason": getattr(args, 'reason', ''),
        }
    if command == "remediation-status":
        return {
            "service": "control",
            "op": "remediation_status",
            "container_id": args.container_id,
        }
    if command == "remediation-history":
        return {
            "service": "control",
            "op": "remediation_history",
            "container_id": args.container_id,
            "tail": getattr(args, 'tail', None),
            "trigger": getattr(args, 'trigger', None),
            "action": getattr(args, 'action', None),
        }
    if command == "tenant-config-set":
        return {
            "service": "control",
            "op": "tenant_config_set",
            "owner": args.owner,
            "priority": getattr(args, 'priority', 0),
            "weight": getattr(args, 'weight', 1.0),
            "burstable_pct": getattr(args, 'burstable_pct', 20.0),
            "enforce": getattr(args, 'enforce', True),
            "eviction_policy": getattr(args, 'eviction_policy', 'alert'),
        }
    if command == "tenant-config-get":
        return {
            "service": "control",
            "op": "tenant_config_get",
            "owner": args.owner,
        }
    if command == "tenant-config-list":
        return {"service": "control", "op": "tenant_config_list"}
    if command == "fair-share":
        return {
            "service": "control",
            "op": "fair_share",
            "resource": getattr(args, 'resource', 'memory_mb'),
        }
    if command == "tenant-enforce":
        return {"service": "control", "op": "tenant_enforce"}
    if command == "tenant-usage-summary":
        return {"service": "control", "op": "tenant_usage_summary"}
    if command == "event-log-export":
        return {
            "service": "control",
            "op": "event_log_export",
            "container_id": getattr(args, 'container_id', None),
            "include_audit": getattr(args, 'include_audit', True),
            "include_oom": getattr(args, 'include_oom', True),
            "include_sla": getattr(args, 'include_sla', True),
            "since": getattr(args, 'since', None),
            "until": getattr(args, 'until', None),
        }
    if command == "event-log-import":
        import json as _json
        data_file = getattr(args, 'data_file', None)
        if data_file:
            with open(data_file, 'r') as f:
                data = _json.load(f)
        else:
            data = _json.loads(sys.stdin.read())
        return {
            "service": "control",
            "op": "event_log_import",
            "data": data,
            "container_id": getattr(args, 'container_id', None),
        }
    if command == "health-score":
        return {
            "service": "control",
            "op": "health_score",
            "container_id": args.container_id,
        }
    if command == "health-score-all":
        return {"service": "control", "op": "health_score_all"}
    if command == "event-log-compress":
        import json as _json
        data_file = getattr(args, 'data_file', None)
        if data_file:
            with open(data_file, 'r') as f:
                data = _json.load(f)
        else:
            data = _json.loads(sys.stdin.read())
        return {
            "service": "control",
            "op": "event_log_compress",
            "data": data,
            "keep_recent": getattr(args, 'keep_recent', 100),
            "summarize_older": getattr(args, 'summarize_older', True),
        }
    if command == "archive-schedule-set":
        return {
            "service": "control",
            "op": "archive_schedule_set",
            "enabled": getattr(args, 'enabled', True),
            "interval_s": getattr(args, 'interval_s', 86400.0),
            "keep_recent": getattr(args, 'keep_recent', 500),
            "auto_compress": getattr(args, 'auto_compress', True),
            "max_archives": getattr(args, 'max_archives', 30),
        }
    if command == "archive-schedule-get":
        return {"service": "control", "op": "archive_schedule_get"}
    if command == "archive-schedule-disable":
        return {"service": "control", "op": "archive_schedule_disable"}
    if command == "archive-run-now":
        return {"service": "control", "op": "archive_run_now"}
    if command == "archive-list":
        return {
            "service": "control",
            "op": "archive_list",
            "tail": getattr(args, 'tail', None),
        }
    if command == "archive-get":
        return {
            "service": "control",
            "op": "archive_get",
            "index": getattr(args, 'index', 0),
        }
    if command == "sla-breach-process":
        return {
            "service": "control",
            "op": "sla_breach_process",
            "container_id": args.container_id,
            "breach_type": getattr(args, 'breach_type', 'downtime'),
            "detail": getattr(args, 'detail', ''),
        }
    if command == "sla-breach-process-all":
        return {
            "service": "control",
            "op": "sla_breach_process_all",
            "breach_type": getattr(args, 'breach_type', 'downtime'),
            "detail": getattr(args, 'detail', ''),
            "container_ids": getattr(args, 'container_ids', None),
        }
    if command == "smart-remediate":
        return {
            "service": "control",
            "op": "smart_remediate",
            "container_id": args.container_id,
        }
    if command == "smart-remediate-all":
        return {"service": "control", "op": "smart_remediate_all"}
    if command == "usage-patterns":
        return {
            "service": "control",
            "op": "usage_patterns",
            "container_id": args.container_id,
            "window_size": getattr(args, 'window_size', 30),
        }
    if command == "optimization-actions":
        return {
            "service": "control",
            "op": "optimization_actions",
            "container_id": args.container_id,
        }
    if command == "rightsize":
        return {
            "service": "control",
            "op": "rightsize",
            "container_id": args.container_id,
            "safety_margin_pct": getattr(args, 'safety_margin_pct', 20.0),
            "dry_run": getattr(args, 'dry_run', False),
        }
    if command == "rightsize-all":
        return {
            "service": "control",
            "op": "rightsize_all",
            "safety_margin_pct": getattr(args, 'safety_margin_pct', 20.0),
            "dry_run": getattr(args, 'dry_run', False),
        }
    if command == "sla-compliance-set":
        return {
            "service": "control",
            "op": "sla_compliance_set",
            "container_id": args.container_id,
            "max_memory_pct": getattr(args, 'max_memory_pct', 90.0),
            "max_pid_pct": getattr(args, 'max_pid_pct', 80.0),
            "max_daily_cost": getattr(args, 'max_daily_cost', None),
            "max_consecutive_anomalies": getattr(args, 'max_consecutive_anomalies', 5),
            "auto_action": getattr(args, 'auto_action', 'alert'),
            "enabled": getattr(args, 'enabled', True),
        }
    if command == "sla-compliance-get":
        return {
            "service": "control",
            "op": "sla_compliance_get",
            "container_id": args.container_id,
        }
    if command == "sla-compliance-check":
        return {
            "service": "control",
            "op": "sla_compliance_check",
            "container_id": args.container_id,
        }
    if command == "sla-compliance-check-all":
        return {"service": "control", "op": "sla_compliance_check_all"}
    if command == "viz-data":
        return {
            "service": "control",
            "op": "visualization_data",
            "container_id": args.container_id,
            "time_range_s": getattr(args, 'time_range_s', 3600.0),
            "resolution": getattr(args, 'resolution', 60),
        }
    if command == "viz-fleet":
        return {
            "service": "control",
            "op": "fleet_visualization",
            "time_range_s": getattr(args, 'time_range_s', 3600.0),
        }
    if command == "anomaly-remediate":
        return {
            "service": "control",
            "op": "anomaly_remediate",
            "container_id": args.container_id,
            "resource": getattr(args, 'resource', 'memory'),
            "sensitivity": getattr(args, 'sensitivity', 2.0),
        }
    if command == "anomaly-remediate-all":
        return {
            "service": "control",
            "op": "anomaly_remediate_all",
            "resource": getattr(args, 'resource', 'memory'),
            "sensitivity": getattr(args, 'sensitivity', 2.0),
        }
    if command == "monitor-configure":
        return {
            "service": "control",
            "op": "monitoring_configure",
            "container_id": args.container_id,
            "memory_high_pct": getattr(args, 'memory_high_pct', 90.0),
            "memory_low_pct": getattr(args, 'memory_low_pct', 10.0),
            "cpu_high_pct": getattr(args, 'cpu_high_pct', 90.0),
            "pid_high_pct": getattr(args, 'pid_high_pct', 80.0),
            "cost_high_daily": getattr(args, 'cost_high_daily', None),
            "trend_window": getattr(args, 'trend_window', 10),
            "trend_threshold": getattr(args, 'trend_threshold', 0.1),
            "enabled": getattr(args, 'enabled', True),
        }
    if command == "monitor-get":
        return {
            "service": "control",
            "op": "monitoring_get",
            "container_id": args.container_id,
        }
    if command == "monitor-check":
        return {
            "service": "control",
            "op": "monitoring_check",
            "container_id": args.container_id,
        }
    if command == "monitor-check-all":
        return {"service": "control", "op": "monitoring_check_all"}
    if command == "sla-auto-escalation-configure":
        return {
            "service": "control",
            "op": "sla_auto_escalation_configure",
            "container_id": args.container_id,
            "enabled": getattr(args, 'enabled', True),
            "breach_threshold": getattr(args, 'breach_threshold', 3),
            "escalation_window_s": getattr(args, 'escalation_window_s', 3600.0),
            "max_level": getattr(args, 'max_level', 3),
            "cooldown_s": getattr(args, 'cooldown_s', 300.0),
        }
    if command == "sla-breach-record":
        return {
            "service": "control",
            "op": "sla_breach_record",
            "container_id": args.container_id,
            "breach_type": getattr(args, 'breach_type', 'downtime'),
            "detail": getattr(args, 'detail', ''),
        }
    if command == "sla-auto-escalation-status":
        return {
            "service": "control",
            "op": "sla_auto_escalation_status",
            "container_id": args.container_id,
        }
    if command == "sla-auto-escalation-reset":
        return {
            "service": "control",
            "op": "sla_auto_escalation_reset",
            "container_id": args.container_id,
        }
    if command == "cost-optimize":
        return {
            "service": "control",
            "op": "cost_optimize",
            "container_id": args.container_id,
        }
    if command == "cost-optimize-all":
        return {"service": "control", "op": "cost_optimize_all"}
    if command == "anomaly-predict":
        return {
            "service": "control",
            "op": "anomaly_predict",
            "container_id": args.container_id,
            "horizon_s": getattr(args, 'horizon_s', 3600.0),
            "confidence_threshold": getattr(
                args, 'confidence_threshold', 0.5),
        }
    if command == "anomaly-predict-all":
        return {
            "service": "control",
            "op": "anomaly_predict_all",
            "horizon_s": getattr(args, 'horizon_s', 3600.0),
            "confidence_threshold": getattr(
                args, 'confidence_threshold', 0.5),
        }
    if command == "predictive-scale-configure":
        return {
            "service": "control",
            "op": "predictive_scaling_configure",
            "container_id": args.container_id,
            "enabled": getattr(args, 'enabled', True),
            "lead_time_s": getattr(args, 'lead_time_s', 300.0),
            "memory_buffer_pct": getattr(
                args, 'memory_buffer_pct', 20.0),
            "cpu_buffer_pct": getattr(args, 'cpu_buffer_pct', 15.0),
            "scale_up_threshold": getattr(
                args, 'scale_up_threshold', 0.75),
            "scale_down_threshold": getattr(
                args, 'scale_down_threshold', 0.30),
            "min_memory_mb": getattr(args, 'min_memory_mb', None),
            "max_memory_mb": getattr(args, 'max_memory_mb', None),
            "dry_run": getattr(args, 'dry_run', False),
        }
    if command == "predictive-scale-evaluate":
        return {
            "service": "control",
            "op": "predictive_scaling_evaluate",
            "container_id": args.container_id,
        }
    if command == "predictive-scale-evaluate-all":
        return {"service": "control",
                "op": "predictive_scaling_evaluate_all"}
    if command == "predictive-scale-status":
        return {
            "service": "control",
            "op": "predictive_scaling_status",
            "container_id": args.container_id,
        }
    if command == "anomaly-correlate":
        return {
            "service": "control",
            "op": "anomaly_correlate",
            "time_window_s": getattr(args, 'time_window_s', 300.0),
            "min_containers": getattr(args, 'min_containers', 2),
            "resource_filter": getattr(args, 'resource_filter', None),
        }
    if command == "anomaly-correlation-report":
        return {
            "service": "control",
            "op": "anomaly_correlation_report",
            "time_window_s": getattr(args, 'time_window_s', 300.0),
        }
    if command == "resource-heatmap":
        return {
            "service": "control",
            "op": "resource_heatmap",
            "window_s": getattr(args, 'window_s', 300.0),
        }
    if command == "container-pressure-detail":
        return {
            "service": "control",
            "op": "container_pressure_detail",
            "container_id": args.container_id,
            "window_s": getattr(args, 'window_s', 300.0),
        }
    if command == "record-pressure-snapshot":
        return {
            "service": "control",
            "op": "record_pressure_snapshot",
        }
    if command == "classify-tier":
        return {
            "service": "control",
            "op": "classify_tier",
            "container_id": args.container_id,
        }
    if command == "fleet-tier-summary":
        return {
            "service": "control",
            "op": "fleet_tier_summary",
        }
    if command == "suggest-tier-upgrade":
        return {
            "service": "control",
            "op": "suggest_tier_upgrade",
            "container_id": args.container_id,
        }
    if command == "log-stream":
        return {
            "service": "control",
            "op": "log_stream",
            "container_id": args.container_id,
            "follow": getattr(args, 'follow', False),
            "interval_s": getattr(args, 'interval_s', 0.5),
            "max_lines": getattr(args, 'max_lines', 1000),
            "timeout_s": getattr(args, 'timeout_s', 5.0),
        }
    if command == "log-filter":
        return {
            "service": "control",
            "op": "log_filter",
            "container_id": args.container_id,
            "pattern": getattr(args, 'pattern', ''),
            "stream": getattr(args, 'stream', 'both'),
            "tail": getattr(args, 'tail', None),
            "case_insensitive": getattr(args, 'case_insensitive', False),
            "max_matches": getattr(args, 'max_matches', 500),
        }
    if command == "log-export":
        return {
            "service": "control",
            "op": "log_export",
            "container_id": args.container_id,
            "dest_path": args.dest_path,
            "format": getattr(args, 'format', 'text'),
            "stream": getattr(args, 'stream', 'both'),
            "tail": getattr(args, 'tail', None),
        }
    if command == "image-dedup":
        return {
            "service": "control",
            "op": "image_dedup",
            "images_dir": getattr(args, 'images_dir', None),
        }
    if command == "image-gc":
        return {
            "service": "control",
            "op": "image_gc",
            "images_dir": getattr(args, 'images_dir', None),
            "dry_run": getattr(args, 'dry_run', True),
            "max_age_days": getattr(args, 'max_age_days', None),
            "unused_only": getattr(args, 'unused_only', False),
        }
    if command == "image-layer-stats":
        return {
            "service": "control",
            "op": "image_layer_stats",
            "images_dir": getattr(args, 'images_dir', None),
        }
    if command == "dns-generate":
        return {
            "service": "control",
            "op": "dns_generate",
            "container_id": args.container_id,
            "nameservers": getattr(args, 'nameservers', None),
            "search_domains": getattr(args, 'search_domains', None),
            "options": getattr(args, 'options', None),
        }
    if command == "dns-resolve":
        return {
            "service": "control",
            "op": "dns_resolve",
            "hostname": args.hostname,
            "nameservers": getattr(args, 'nameservers', None),
            "timeout_s": getattr(args, 'timeout_s', 5.0),
        }
    if command == "dns-get-config":
        return {
            "service": "control",
            "op": "dns_get_config",
            "container_id": args.container_id,
        }
    if command == "dns-update":
        return {
            "service": "control",
            "op": "dns_update",
            "container_id": args.container_id,
            "add_nameservers": getattr(args, 'add_nameservers', None),
            "remove_nameservers": getattr(args, 'remove_nameservers', None),
            "add_search_domains": getattr(args, 'add_search_domains', None),
            "remove_search_domains": getattr(args, 'remove_search_domains', None),
        }
    if command == "create-network":
        return {
            "service": "control",
            "op": "create_network",
            "name": args.name,
            "subnet": getattr(args, 'subnet', '172.18.0.0/16'),
            "gateway": getattr(args, 'gateway', '172.18.0.1'),
            "enable_dns": getattr(args, 'enable_dns', True),
        }
    if command == "remove-network":
        return {"service": "control", "op": "remove_network", "name": args.name}
    if command == "list-networks":
        return {"service": "control", "op": "list_networks"}
    if command == "connect-network":
        return {
            "service": "control",
            "op": "connect_network",
            "network_name": args.network_name,
            "container_id": args.container_id,
            "aliases": getattr(args, 'aliases', None),
            "ip_address": getattr(args, 'ip_address', None),
        }
    if command == "disconnect-network":
        return {
            "service": "control",
            "op": "disconnect_network",
            "network_name": args.network_name,
            "container_id": args.container_id,
        }
    if command == "network-topology":
        return {"service": "control", "op": "network_topology", "network_name": args.network_name}
    if command == "network-dns-resolve":
        return {
            "service": "control",
            "op": "network_dns_resolve",
            "network_name": args.network_name,
            "name": args.name,
        }
    if command == "test-connectivity":
        return {
            "service": "control",
            "op": "test_connectivity",
            "network_name": args.network_name,
            "src_container_id": args.src_container_id,
            "dst_ip": args.dst_ip,
        }
    if command == "plan-migration":
        return {
            "service": "control",
            "op": "plan_migration",
            "container_id": args.container_id,
            "target_node": args.target_node,
            "strategy": getattr(args, 'strategy', 'live'),
            "max_downtime_ms": getattr(args, 'max_downtime_ms', 1000),
        }
    if command == "execute-migration":
        return {
            "service": "control",
            "op": "execute_migration",
            "container_id": args.container_id,
            "target_node": args.target_node,
            "strategy": getattr(args, 'strategy', 'live'),
            "dry_run": getattr(args, 'dry_run', True),
        }
    if command == "migration-history":
        return {
            "service": "control",
            "op": "migration_history",
            "container_id": getattr(args, 'container_id', None),
            "tail": getattr(args, 'tail', 20),
        }
    if command == "migration-cost":
        return {
            "service": "control",
            "op": "migration_cost",
            "container_id": args.container_id,
            "target_node": args.target_node,
            "strategy": getattr(args, 'strategy', 'live'),
        }
    if command == "configure-alert-channel":
        return {
            "service": "control",
            "op": "configure_alert_channel",
            "channel_id": args.channel_id,
            "channel_type": args.channel_type,
            "config": getattr(args, 'config', None),
            "enabled": getattr(args, 'enabled', True),
        }
    if command == "remove-alert-channel":
        return {"service": "control", "op": "remove_alert_channel", "channel_id": args.channel_id}
    if command == "list-alert-channels":
        return {"service": "control", "op": "list_alert_channels"}
    if command == "enable-alert-channel":
        return {"service": "control", "op": "enable_alert_channel", "channel_id": args.channel_id}
    if command == "disable-alert-channel":
        return {"service": "control", "op": "disable_alert_channel", "channel_id": args.channel_id}
    if command == "configure-alert-rules":
        return {
            "service": "control",
            "op": "configure_alert_rules",
            "container_id": getattr(args, 'container_id', None),
            "rules": getattr(args, 'rules_json', None),
            "fleet_wide": getattr(args, 'fleet_wide', False),
        }
    if command == "get-alert-rules":
        return {
            "service": "control",
            "op": "get_alert_rules",
            "container_id": getattr(args, 'container_id', None),
        }
    if command == "evaluate-alerts":
        return {"service": "control", "op": "evaluate_alerts", "container_id": args.container_id}
    if command == "alert-history":
        return {
            "service": "control",
            "op": "alert_history",
            "container_id": getattr(args, 'container_id', None),
            "alert_type": getattr(args, 'alert_type', None),
            "tail": getattr(args, 'tail', 50),
        }
    if command == "detect-anomalies":
        return {
            "service": "control",
            "op": "detect_anomalies",
            "container_id": args.container_id,
            "window_size": getattr(args, 'window_size', 30),
            "z_threshold": getattr(args, 'z_threshold', 2.5),
            "iqr_multiplier": getattr(args, 'iqr_multiplier', 1.5),
        }
    if command == "detect-fleet-anomalies":
        return {
            "service": "control",
            "op": "detect_fleet_anomalies",
            "window_size": getattr(args, 'window_size', 30),
            "z_threshold": getattr(args, 'z_threshold', 2.5),
        }
    if command == "diff-snapshots":
        return {
            "service": "control",
            "op": "diff_snapshots",
            "snapshot_a": getattr(args, 'snapshot_a', {}),
            "snapshot_b": getattr(args, 'snapshot_b', {}),
        }
    if command == "rollback-snapshot":
        return {
            "service": "control",
            "op": "rollback_snapshot",
            "container_id": args.container_id,
            "snapshot": getattr(args, 'snapshot', {}),
            "dry_run": getattr(args, 'dry_run', True),
        }
    if command == "optimize-placement":
        return {
            "service": "control",
            "op": "optimize_placement",
            "containers": getattr(args, 'containers', None),
            "strategy": getattr(args, 'strategy', 'balanced'),
            "respect_affinity": getattr(args, 'respect_affinity', True),
        }
    if command == "placement-score":
        return {
            "service": "control",
            "op": "placement_score",
            "container_id": args.container_id,
            "node_id": args.node_id,
        }
    if command == "configure-auto-scaling":
        return {
            "service": "control",
            "op": "configure_auto_scaling",
            "container_id": args.container_id,
            "enabled": getattr(args, 'enabled', True),
            "min_memory_mb": getattr(args, 'min_memory_mb', 64),
            "max_memory_mb": getattr(args, 'max_memory_mb', 4096),
            "target_memory_pct": getattr(args, 'target_memory_pct', 70.0),
            "scale_up_step_mb": getattr(args, 'scale_up_step_mb', 128),
            "scale_down_step_mb": getattr(args, 'scale_down_step_mb', 64),
            "cooldown_seconds": getattr(args, 'cooldown_seconds', 60.0),
        }
    if command == "evaluate-and-adjust":
        return {"service": "control", "op": "evaluate_and_adjust", "container_id": args.container_id}
    if command == "auto-scaling-status":
        return {"service": "control", "op": "auto_scaling_status", "container_id": args.container_id}
    if command == "batch-evaluate-scaling":
        return {"service": "control", "op": "batch_evaluate_scaling"}
    if command == "generate-dependency-graph":
        return {
            "service": "control",
            "op": "generate_dependency_graph",
            "container_ids": getattr(args, 'container_ids', None),
            "format": getattr(args, 'format', 'ascii'),
        }
    if command == "get-critical-path":
        return {
            "service": "control",
            "op": "get_critical_path",
            "container_ids": getattr(args, 'container_ids', None),
        }
    if command == "register-federation-peer":
        return {
            "service": "control",
            "op": "register_federation_peer",
            "peer_id": args.peer_id,
            "peer_url": args.peer_url,
            "cluster_name": args.cluster_name,
            "trust_level": getattr(args, 'trust_level', 'full'),
        }
    if command == "unregister-federation-peer":
        return {"service": "control", "op": "unregister_federation_peer", "peer_id": args.peer_id}
    if command == "list-federation-peers":
        return {"service": "control", "op": "list_federation_peers"}
    if command == "share-container-with-peer":
        return {
            "service": "control",
            "op": "share_container_with_peer",
            "container_id": args.container_id,
            "peer_id": args.peer_id,
            "permissions": getattr(args, 'permissions', None),
        }
    if command == "unshare-container-from-peer":
        return {
            "service": "control",
            "op": "unshare_container_from_peer",
            "container_id": args.container_id,
            "peer_id": args.peer_id,
        }
    if command == "share-resources-with-peer":
        return {
            "service": "control",
            "op": "share_resources_with_peer",
            "peer_id": args.peer_id,
            "resource_type": args.resource_type,
            "amount": args.amount,
        }
    if command == "get-federation-status":
        return {"service": "control", "op": "get_federation_status"}
    if command == "plan-cross-cluster-migration":
        return {
            "service": "control",
            "op": "plan_cross_cluster_migration",
            "container_id": args.container_id,
            "target_peer_id": args.target_peer_id,
            "strategy": getattr(args, 'strategy', 'snapshot'),
        }
    if command == "configure-event-trigger":
        return {
            "service": "control",
            "op": "configure_event_trigger",
            "trigger_id": args.trigger_id,
            "event_type": args.event_type,
            "action": args.action,
            "enabled": getattr(args, 'enabled', True),
        }
    if command == "remove-event-trigger":
        return {"service": "control", "op": "remove_event_trigger", "trigger_id": args.trigger_id}
    if command == "list-event-triggers":
        return {"service": "control", "op": "list_event_triggers"}
    if command == "enable-event-trigger":
        return {"service": "control", "op": "enable_event_trigger", "trigger_id": args.trigger_id}
    if command == "disable-event-trigger":
        return {"service": "control", "op": "disable_event_trigger", "trigger_id": args.trigger_id}
    if command == "fire-event":
        return {
            "service": "control",
            "op": "fire_event",
            "event_type": args.event_type,
            "container_id": getattr(args, 'container_id', None),
        }
    if command == "get-event-log":
        return {
            "service": "control",
            "op": "get_event_log",
            "event_type": getattr(args, 'event_type', None),
            "container_id": getattr(args, 'container_id', None),
            "tail": getattr(args, 'tail', 50),
        }
    if command == "get-trigger-stats":
        return {"service": "control", "op": "get_trigger_stats"}
    if command == "generate-cluster-dashboard":
        return {"service": "control", "op": "generate_cluster_dashboard"}
    if command == "configure-network-rule":
        return {
            "service": "control",
            "op": "configure_network_rule",
            "rule_id": args.rule_id,
            "direction": args.direction,
            "action": args.action,
            "protocol": getattr(args, 'protocol', 'tcp'),
            "port": getattr(args, 'port', None),
            "source": getattr(args, 'source', None),
            "destination": getattr(args, 'destination', None),
            "container_filter": getattr(args, 'container_filter', None),
            "priority": getattr(args, 'priority', 100),
        }
    if command == "remove-network-rule":
        return {"service": "control", "op": "remove_network_rule", "rule_id": args.rule_id}
    if command == "list-network-rules":
        return {
            "service": "control",
            "op": "list_network_rules",
            "direction": getattr(args, 'direction', None),
            "container_id": getattr(args, 'container_id', None),
        }
    if command == "enable-network-rule":
        return {"service": "control", "op": "enable_network_rule", "rule_id": args.rule_id}
    if command == "disable-network-rule":
        return {"service": "control", "op": "disable_network_rule", "rule_id": args.rule_id}
    if command == "evaluate-network-access":
        return {
            "service": "control",
            "op": "evaluate_network_access",
            "container_id": args.container_id,
            "direction": args.direction,
            "protocol": getattr(args, 'protocol', 'tcp'),
            "port": getattr(args, 'port', None),
            "remote_ip": getattr(args, 'remote_ip', None),
        }
    if command == "get-network-rule-stats":
        return {"service": "control", "op": "get_network_rule_stats"}
    if command == "create-backup":
        return {
            "service": "control",
            "op": "create_backup",
            "container_id": args.container_id,
            "backup_id": getattr(args, 'backup_id', None),
            "backup_type": getattr(args, 'backup_type', 'full'),
            "destination": getattr(args, 'destination', '/tmp/nyrqis-backups'),
            "include_logs": getattr(args, 'include_logs', True),
            "include_state": getattr(args, 'include_state', True),
        }
    if command == "list-backups":
        return {"service": "control", "op": "list_backups", "container_id": getattr(args, 'container_id', None)}
    if command == "get-backup":
        return {"service": "control", "op": "get_backup", "backup_id": args.backup_id}
    if command == "delete-backup":
        return {"service": "control", "op": "delete_backup", "backup_id": args.backup_id}
    if command == "restore-from-backup":
        return {
            "service": "control",
            "op": "restore_from_backup",
            "backup_id": args.backup_id,
            "container_id": getattr(args, 'container_id', None),
            "dry_run": getattr(args, 'dry_run', True),
        }
    if command == "configure-backup-policy":
        return {
            "service": "control",
            "op": "configure_backup_policy",
            "container_id": args.container_id,
            "enabled": getattr(args, 'enabled', True),
            "interval_hours": getattr(args, 'interval_hours', 24),
            "retention_count": getattr(args, 'retention_count', 7),
            "backup_type": getattr(args, 'backup_type', 'full'),
            "include_logs": getattr(args, 'include_logs', True),
        }
    if command == "get-backup-policy":
        return {"service": "control", "op": "get_backup_policy", "container_id": args.container_id}
    if command == "get-dr-status":
        return {"service": "control", "op": "get_dr_status"}
    if command == "aggregate-cluster-logs":
        return {
            "service": "control",
            "op": "aggregate_cluster_logs",
            "pattern": getattr(args, 'pattern', ''),
            "stream": getattr(args, 'stream', 'both'),
            "tail": getattr(args, 'tail', 100),
            "container_ids": getattr(args, 'container_ids', None),
            "sort_by": getattr(args, 'sort_by', 'timestamp'),
        }
    if command == "search-cluster-logs":
        return {
            "service": "control",
            "op": "search_cluster_logs",
            "pattern": args.pattern,
            "stream": getattr(args, 'stream', 'both'),
            "max_matches": getattr(args, 'max_matches', 500),
        }
    if command == "get-log-stats":
        return {"service": "control", "op": "get_log_stats"}
    if command == "scan-container-security":
        return {"service": "control", "op": "scan_container_security", "container_id": args.container_id}
    if command == "scan-fleet-security":
        return {"service": "control", "op": "scan_fleet_security"}
    if command == "get-security-summary":
        return {"service": "control", "op": "get_security_summary"}
    if command == "scan-image-vulnerabilities":
        return {
            "service": "control",
            "op": "scan_image_vulnerabilities",
            "image_path": args.image_path,
            "severity_filter": getattr(args, 'severity_filter', None),
        }
    if command == "scan-container-vulnerabilities":
        return {
            "service": "control",
            "op": "scan_container_vulnerabilities",
            "container_id": args.container_id,
            "severity_filter": getattr(args, 'severity_filter', None),
        }
    if command == "scan-fleet-vulnerabilities":
        return {
            "service": "control",
            "op": "scan_fleet_vulnerabilities",
            "severity_filter": getattr(args, 'severity_filter', None),
        }
    if command == "get-vulnerability-summary":
        return {"service": "control", "op": "get_vulnerability_summary"}
    if command == "profile-container-performance":
        return {"service": "control", "op": "profile_container_performance", "container_id": args.container_id}
    if command == "profile-fleet-performance":
        return {"service": "control", "op": "profile_fleet_performance"}
    if command == "get-performance-recommendations":
        return {"service": "control", "op": "get_performance_recommendations", "container_id": args.container_id}
    if command == "forecast-resource-needs":
        return {
            "service": "control",
            "op": "forecast_resource_needs",
            "container_id": args.container_id,
            "horizon_hours": getattr(args, 'horizon_hours', 24),
        }
    if command == "forecast-fleet-capacity":
        return {"service": "control", "op": "forecast_fleet_capacity"}
    if command == "get-capacity-recommendations":
        return {"service": "control", "op": "get_capacity_recommendations"}
    if command == "read-container-file":
        return {
            "service": "control",
            "op": "read_container_file",
            "container_id": args.container_id,
            "path": args.path,
            "max_size": getattr(args, 'max_size', 1048576),
        }
    if command == "write-container-file":
        return {
            "service": "control",
            "op": "write_container_file",
            "container_id": args.container_id,
            "path": args.path,
            "content": args.content,
            "create_dirs": getattr(args, 'create_dirs', True),
        }
    if command == "list-container-files":
        return {
            "service": "control",
            "op": "list_container_files",
            "container_id": args.container_id,
            "path": getattr(args, 'path', '/'),
            "recursive": getattr(args, 'recursive', False),
            "max_entries": getattr(args, 'max_entries', 500),
        }
    if command == "delete-container-file":
        return {
            "service": "control",
            "op": "delete_container_file",
            "container_id": args.container_id,
            "path": args.path,
        }
    if command == "get-file-info":
        return {
            "service": "control",
            "op": "get_file_info",
            "container_id": args.container_id,
            "path": args.path,
        }
    if command == "get-process-tree":
        return {
            "service": "control",
            "op": "get_process_tree",
            "container_id": args.container_id,
            "root_pid": getattr(args, 'root_pid', None),
            "max_depth": getattr(args, 'max_depth', 10),
        }
    if command == "get-process-stats":
        return {"service": "control", "op": "get_process_stats", "container_id": args.container_id}
    if command == "generate-comparison-report":
        return {
            "service": "control",
            "op": "generate_comparison_report",
            "container_ids": getattr(args, 'container_ids', None),
            "include_recommendations": getattr(args, 'include_recommendations', True),
        }
    if command == "generate-cost-report":
        return {
            "service": "control",
            "op": "generate_cost_report",
            "container_ids": getattr(args, 'container_ids', None),
        }
    if command == "configure-health-check":
        return {
            "service": "control",
            "op": "configure_health_check",
            "container_id": args.container_id,
            "check_type": getattr(args, 'check_type', 'http'),
            "endpoint": getattr(args, 'endpoint', '/'),
            "port": getattr(args, 'port', 80),
            "interval_seconds": getattr(args, 'interval_seconds', 30),
            "timeout_seconds": getattr(args, 'timeout_seconds', 5),
            "failure_threshold": getattr(args, 'failure_threshold', 3),
            "success_threshold": getattr(args, 'success_threshold', 1),
        }
    if command == "get-health-check":
        return {
            "service": "control",
            "op": "get_health_check",
            "container_id": args.container_id,
        }
    if command == "evaluate-health-check":
        return {
            "service": "control",
            "op": "evaluate_health_check",
            "container_id": args.container_id,
        }
    if command == "get-readiness-status":
        return {
            "service": "control",
            "op": "get_readiness_status",
            "container_id": args.container_id,
        }
    if command == "get-liveness-status":
        return {
            "service": "control",
            "op": "get_liveness_status",
            "container_id": args.container_id,
        }
    if command == "fleet-health-overview":
        return {
            "service": "control",
            "op": "fleet_health_overview",
        }
    if command == "configure-escalation-chain":
        return {
            "service": "control",
            "op": "configure_escalation_chain",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'default'),
        }
    if command == "evaluate-escalation":
        return {
            "service": "control",
            "op": "evaluate_escalation",
            "container_id": args.container_id,
            "severity": getattr(args, 'severity', 0),
        }
    if command == "get-escalation-status":
        return {
            "service": "control",
            "op": "get_escalation_status",
            "container_id": args.container_id,
        }
    if command == "reset-escalation-state":
        return {
            "service": "control",
            "op": "reset_escalation_state",
            "container_id": args.container_id,
            "chain_name": getattr(args, 'chain_name', None),
        }
    if command == "disable-escalation-chain":
        return {
            "service": "control",
            "op": "disable_escalation_chain",
            "container_id": args.container_id,
            "chain_name": args.chain_name,
        }
    if command == "generate-compliance-report":
        return {
            "service": "control",
            "op": "generate_compliance_report",
            "container_ids": getattr(args, 'container_ids', None),
            "policy": getattr(args, 'policy', 'basic'),
        }
    if command == "export-audit-logs":
        return {
            "service": "control",
            "op": "export_audit_logs",
            "container_ids": getattr(args, 'container_ids', None),
            "format": getattr(args, 'format', 'json'),
        }
    if command == "get-compliance-summary":
        return {
            "service": "control",
            "op": "get_compliance_summary",
            "policy": getattr(args, 'policy', 'basic'),
        }
    if command == "create-secret":
        return {
            "service": "control",
            "op": "create_secret",
            "name": args.name,
            "data": json.loads(args.data) if hasattr(args, 'data') and args.data else {},
            "namespace": getattr(args, 'namespace', 'default'),
            "secret_type": getattr(args, 'secret_type', 'opaque'),
        }
    if command == "get-secret":
        return {
            "service": "control",
            "op": "get_secret",
            "secret_id": args.secret_id,
            "decrypt": getattr(args, 'decrypt', False),
        }
    if command == "delete-secret":
        return {
            "service": "control",
            "op": "delete_secret",
            "secret_id": args.secret_id,
        }
    if command == "rotate-secret":
        return {
            "service": "control",
            "op": "rotate_secret",
            "secret_id": args.secret_id,
            "new_data": json.loads(args.new_data) if hasattr(args, 'new_data') and args.new_data else {},
        }
    if command == "list-secrets":
        return {
            "service": "control",
            "op": "list_secrets",
            "namespace": getattr(args, 'namespace', None),
        }
    if command == "secret-usage":
        return {
            "service": "control",
            "op": "get_secret_usage",
        }
    if command == "create-namespace":
        return {
            "service": "control",
            "op": "create_namespace",
            "name": args.name,
        }
    if command == "set-resource-quota":
        return {
            "service": "control",
            "op": "set_resource_quota",
            "namespace": args.namespace,
            "resource_type": args.resource_type,
            "hard_limit": float(args.hard_limit),
            "soft_limit": float(args.soft_limit) if hasattr(args, 'soft_limit') and args.soft_limit else None,
        }
    if command == "get-resource-quota":
        return {
            "service": "control",
            "op": "get_resource_quota",
            "namespace": args.namespace,
        }
    if command == "check-quota-compliance":
        return {
            "service": "control",
            "op": "check_quota_compliance",
            "namespace": args.namespace,
        }
    if command == "list-namespaces":
        return {
            "service": "control",
            "op": "list_namespaces",
        }
    if command == "delete-namespace":
        return {
            "service": "control",
            "op": "delete_namespace",
            "name": args.name,
        }
    if command == "namespace-summary":
        return {
            "service": "control",
            "op": "get_namespace_summary",
        }
    if command == "record-deployment":
        return {
            "service": "control",
            "op": "record_deployment",
            "container_id": args.container_id,
            "notes": getattr(args, 'notes', ''),
        }
    if command == "deployment-history":
        return {
            "service": "control",
            "op": "get_deployment_history",
            "container_id": args.container_id,
            "limit": getattr(args, 'limit', 10),
        }
    if command == "rollback-deployment":
        return {
            "service": "control",
            "op": "rollback_deployment",
            "container_id": args.container_id,
            "version": int(args.version),
        }
    if command == "deployment-diff":
        return {
            "service": "control",
            "op": "get_deployment_diff",
            "container_id": args.container_id,
            "version_a": int(args.version_a),
            "version_b": int(args.version_b),
        }
    if command == "rollback-candidates":
        return {
            "service": "control",
            "op": "get_rollback_candidates",
            "container_id": args.container_id,
        }
    if command == "deployment-status":
        return {
            "service": "control",
            "op": "get_deployment_status",
            "container_id": args.container_id,
        }
    if command == "configure-graceful-shutdown":
        return {
            "service": "control",
            "op": "configure_graceful_shutdown",
            "container_id": args.container_id,
            "drain_timeout": getattr(args, 'drain_timeout', 30),
            "signal": getattr(args, 'signal', 'SIGTERM'),
            "pre_stop_hook": getattr(args, 'pre_stop_hook', None),
        }
    if command == "initiate-graceful-shutdown":
        return {
            "service": "control",
            "op": "initiate_graceful_shutdown",
            "container_id": args.container_id,
        }
    if command == "get-shutdown-status":
        return {
            "service": "control",
            "op": "get_shutdown_status",
            "container_id": args.container_id,
        }
    if command == "force-shutdown":
        return {
            "service": "control",
            "op": "force_shutdown",
            "container_id": args.container_id,
        }
    if command == "batch-graceful-shutdown":
        return {
            "service": "control",
            "op": "batch_graceful_shutdown",
            "container_ids": args.container_ids,
            "drain_timeout": getattr(args, 'drain_timeout', 30),
        }
    if command == "get-drain-progress":
        return {
            "service": "control",
            "op": "get_drain_progress",
            "container_id": args.container_id,
        }
    if command == "register-config-watcher":
        return {
            "service": "control",
            "op": "register_config_watcher",
            "container_id": args.container_id,
            "config_path": args.config_path,
            "reload_action": getattr(args, 'reload_action', 'restart'),
        }
    if command == "trigger-config-reload":
        return {
            "service": "control",
            "op": "trigger_config_reload",
            "container_id": args.container_id,
            "watcher_id": args.watcher_id,
        }
    if command == "get-config-watchers":
        return {
            "service": "control",
            "op": "get_config_watchers",
            "container_id": args.container_id,
        }
    if command == "remove-config-watcher":
        return {
            "service": "control",
            "op": "remove_config_watcher",
            "container_id": args.container_id,
            "watcher_id": args.watcher_id,
        }
    if command == "hot-reload-config":
        return {
            "service": "control",
            "op": "hot_reload_config",
            "container_id": args.container_id,
            "config": json.loads(args.config) if hasattr(args, 'config') and args.config else {},
        }
    if command == "get-reload-history":
        return {
            "service": "control",
            "op": "get_reload_history",
            "container_id": args.container_id,
        }
    if command == "record-event":
        return {
            "service": "control",
            "op": "record_event",
            "container_id": args.container_id,
            "event_type": args.event_type,
            "message": getattr(args, 'message', ''),
            "severity": getattr(args, 'severity', 'info'),
        }
    if command == "correlate-events":
        return {
            "service": "control",
            "op": "correlate_events",
            "time_window": getattr(args, 'time_window', 300.0),
            "min_containers": getattr(args, 'min_containers', 2),
        }
    if command == "analyze-event-patterns":
        return {
            "service": "control",
            "op": "analyze_event_patterns",
            "time_window": getattr(args, 'time_window', 3600.0),
        }
    if command == "suggest-root-cause":
        return {
            "service": "control",
            "op": "suggest_root_cause",
            "time_window": getattr(args, 'time_window', 300.0),
        }
    if command == "get-event-timeline":
        return {
            "service": "control",
            "op": "get_event_timeline",
            "container_ids": getattr(args, 'container_ids', None),
            "time_window": getattr(args, 'time_window', 3600.0),
        }
    if command == "configure-network-monitoring":
        return {
            "service": "control",
            "op": "configure_network_monitoring",
            "container_id": args.container_id,
            "interfaces": getattr(args, 'interfaces', None),
            "sample_interval": getattr(args, 'sample_interval', 1.0),
        }
    if command == "record-network-sample":
        return {
            "service": "control",
            "op": "record_network_sample",
            "container_id": args.container_id,
            "interface": getattr(args, 'interface', 'eth0'),
            "latency_ms": float(getattr(args, 'latency_ms', 0)),
            "rx_bytes": int(getattr(args, 'rx_bytes', 0)),
            "tx_bytes": int(getattr(args, 'tx_bytes', 0)),
        }
    if command == "get-network-latency-stats":
        return {
            "service": "control",
            "op": "get_network_latency_stats",
            "container_id": args.container_id,
        }
    if command == "get-bandwidth-stats":
        return {
            "service": "control",
            "op": "get_bandwidth_stats",
            "container_id": args.container_id,
        }
    if command == "get-network-health":
        return {
            "service": "control",
            "op": "get_network_health",
            "container_id": args.container_id,
        }
    if command == "fleet-network-overview":
        return {
            "service": "control",
            "op": "fleet_network_overview",
        }
    if command == "configure-storage-profiling":
        return {
            "service": "control",
            "op": "configure_storage_profiling",
            "container_id": args.container_id,
            "cache_size_mb": getattr(args, 'cache_size_mb', 64),
        }
    if command == "record-storage-io":
        return {
            "service": "control",
            "op": "record_storage_io",
            "container_id": args.container_id,
            "op_type": getattr(args, 'op_type', 'read'),
            "path": getattr(args, 'path', '/'),
            "bytes_count": int(getattr(args, 'bytes_count', 0)),
            "duration_ms": float(getattr(args, 'duration_ms', 0)),
        }
    if command == "get-storage-io-stats":
        return {
            "service": "control",
            "op": "get_storage_io_stats",
            "container_id": args.container_id,
        }
    if command == "get-storage-io-latency":
        return {
            "service": "control",
            "op": "get_storage_io_latency",
            "container_id": args.container_id,
        }
    if command == "clear-storage-cache":
        return {
            "service": "control",
            "op": "clear_storage_cache",
            "container_id": args.container_id,
        }
    if command == "get-storage-hot-paths":
        return {
            "service": "control",
            "op": "get_storage_hot_paths",
            "container_id": args.container_id,
        }
    if command == "initialize-audit-integrity":
        return {
            "service": "control",
            "op": "initialize_audit_integrity",
            "container_id": args.container_id,
        }
    if command == "append-audit-event":
        return {
            "service": "control",
            "op": "append_audit_event",
            "container_id": args.container_id,
            "op": args.audit_op,
            "details": getattr(args, 'details', None),
        }
    if command == "verify-audit-integrity":
        return {
            "service": "control",
            "op": "verify_audit_integrity",
            "container_id": args.container_id,
        }
    if command == "audit-integrity-report":
        return {
            "service": "control",
            "op": "get_audit_integrity_report",
            "container_id": args.container_id,
        }
    if command == "tamper-summary":
        return {
            "service": "control",
            "op": "get_tamper_summary",
        }
    if command == "configure-rate-limit":
        return {
            "service": "control",
            "op": "configure_rate_limit",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'default'),
            "rate": float(getattr(args, 'rate', 100)),
            "burst": int(getattr(args, 'burst', 200)),
        }
    if command == "check-rate-limit":
        return {
            "service": "control",
            "op": "check_rate_limit",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'default'),
            "tokens": int(getattr(args, 'tokens', 1)),
        }
    if command == "get-rate-limit-stats":
        return {
            "service": "control",
            "op": "get_rate_limit_stats",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'default'),
        }
    if command == "get-all-rate-limiters":
        return {
            "service": "control",
            "op": "get_all_rate_limiters",
            "container_id": args.container_id,
        }
    if command == "reset-rate-limit":
        return {
            "service": "control",
            "op": "reset_rate_limit",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'default'),
        }
    if command == "delete-rate-limit":
        return {
            "service": "control",
            "op": "delete_rate_limit",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'default'),
        }
    if command == "create-feature-flag":
        return {
            "service": "control",
            "op": "create_feature_flag",
            "name": args.name,
            "enabled": getattr(args, 'enabled', False),
            "rollout_percentage": float(getattr(args, 'rollout_percentage', 0)),
            "description": getattr(args, 'description', ''),
        }
    if command == "evaluate-feature-flag":
        return {
            "service": "control",
            "op": "evaluate_feature_flag",
            "container_id": args.container_id,
            "flag_name": args.flag_name,
        }
    if command == "update-feature-flag":
        return {
            "service": "control",
            "op": "update_feature_flag",
            "name": args.name,
            "enabled": getattr(args, 'enabled', None),
            "rollout_percentage": float(getattr(args, 'rollout_percentage', 0)) if hasattr(args, 'rollout_percentage') and args.rollout_percentage is not None else None,
        }
    if command == "list-feature-flags":
        return {
            "service": "control",
            "op": "list_feature_flags",
        }
    if command == "delete-feature-flag":
        return {
            "service": "control",
            "op": "delete_feature_flag",
            "name": args.name,
        }
    if command == "feature-flag-summary":
        return {
            "service": "control",
            "op": "get_feature_flag_summary",
        }
    if command == "create-bluegreen":
        return {
            "service": "control",
            "op": "create_bluegreen_deployment",
            "name": args.name,
            "blue_container_id": args.blue_container_id,
            "green_container_id": getattr(args, 'green_container_id', None),
        }
    if command == "switch-traffic":
        return {
            "service": "control",
            "op": "switch_traffic",
            "deployment_name": args.deployment_name,
            "target_slot": args.target_slot,
            "percentage": float(args.percentage) if hasattr(args, 'percentage') and args.percentage is not None else None,
        }
    if command == "get-bluegreen-status":
        return {
            "service": "control",
            "op": "get_bluegreen_status",
            "deployment_name": args.deployment_name,
        }
    if command == "rollback-bluegreen":
        return {
            "service": "control",
            "op": "rollback_bluegreen",
            "deployment_name": args.deployment_name,
        }
    if command == "get-bluegreen-history":
        return {
            "service": "control",
            "op": "get_bluegreen_history",
            "deployment_name": args.deployment_name,
        }
    if command == "list-bluegreen":
        return {
            "service": "control",
            "op": "list_bluegreen_deployments",
        }
    if command == "delete-bluegreen":
        return {
            "service": "control",
            "op": "delete_bluegreen_deployment",
            "name": args.name,
        }
    if command == "create-canary":
        return {
            "service": "control",
            "op": "create_canary_deployment",
            "name": args.name,
            "stable_container_id": args.stable_container_id,
            "canary_container_id": args.canary_container_id,
            "traffic_percentage": float(getattr(args, 'traffic_percentage', 10)),
            "error_threshold": float(getattr(args, 'error_threshold', 5)),
            "latency_threshold_ms": float(getattr(args, 'latency_threshold_ms', 200)),
        }
    if command == "record-canary-metric":
        return {
            "service": "control",
            "op": "record_canary_metric",
            "deployment_name": args.deployment_name,
            "slot": args.slot,
            "latency_ms": float(getattr(args, 'latency_ms', 0)),
            "is_error": getattr(args, 'is_error', False),
        }
    if command == "evaluate-canary-health":
        return {
            "service": "control",
            "op": "evaluate_canary_health",
            "deployment_name": args.deployment_name,
        }
    if command == "promote-canary":
        return {
            "service": "control",
            "op": "promote_canary",
            "deployment_name": args.deployment_name,
        }
    if command == "rollback-canary":
        return {
            "service": "control",
            "op": "rollback_canary",
            "deployment_name": args.deployment_name,
        }
    if command == "get-canary-status":
        return {
            "service": "control",
            "op": "get_canary_status",
            "deployment_name": args.deployment_name,
        }
    if command == "list-canary":
        return {
            "service": "control",
            "op": "list_canary_deployments",
        }
    if command == "delete-canary":
        return {
            "service": "control",
            "op": "delete_canary_deployment",
            "name": args.name,
        }
    if command == "register-service":
        return {
            "service": "control",
            "op": "register_service",
            "name": args.name,
            "container_id": args.container_id,
            "port": int(getattr(args, 'port', 80)),
            "tags": getattr(args, 'tags', None),
        }
    if command == "deregister-service":
        return {
            "service": "control",
            "op": "deregister_service",
            "name": args.name,
            "container_id": args.container_id,
        }
    if command == "discover-service":
        return {
            "service": "control",
            "op": "discover_service",
            "name": args.name,
            "tag": getattr(args, 'tag', None),
        }
    if command == "service-heartbeat":
        return {
            "service": "control",
            "op": "service_heartbeat",
            "name": args.name,
            "container_id": args.container_id,
        }
    if command == "get-service-instances":
        return {
            "service": "control",
            "op": "get_service_instances",
            "name": args.name,
        }
    if command == "inject-dependency":
        return {
            "service": "control",
            "op": "inject_dependency",
            "container_id": args.container_id,
            "service_name": args.service_name,
            "env_var": args.env_var,
        }
    if command == "list-services":
        return {
            "service": "control",
            "op": "list_services",
        }
    if command == "create-trace":
        return {
            "service": "control",
            "op": "create_trace",
            "name": args.name,
        }
    if command == "start-span":
        return {
            "service": "control",
            "op": "start_span",
            "trace_id": args.trace_id,
            "operation": args.operation,
            "parent_span_id": getattr(args, 'parent_span_id', None),
        }
    if command == "finish-span":
        return {
            "service": "control",
            "op": "finish_span",
            "trace_id": args.trace_id,
            "span_id": args.span_id,
        }
    if command == "finish-trace":
        return {
            "service": "control",
            "op": "finish_trace",
            "trace_id": args.trace_id,
        }
    if command == "get-trace":
        return {
            "service": "control",
            "op": "get_trace",
            "trace_id": args.trace_id,
        }
    if command == "get-trace-summary":
        return {
            "service": "control",
            "op": "get_trace_summary",
            "trace_id": args.trace_id,
        }
    if command == "list-traces":
        return {
            "service": "control",
            "op": "list_traces",
            "limit": int(getattr(args, 'limit', 20)),
        }
    if command == "create-chaos-experiment":
        return {
            "service": "control",
            "op": "create_chaos_experiment",
            "name": args.name,
            "target_containers": args.target_containers,
            "fault_type": getattr(args, 'fault_type', 'latency'),
            "duration_seconds": int(getattr(args, 'duration_seconds', 60)),
            "blast_radius": float(getattr(args, 'blast_radius', 50)),
        }
    if command == "start-chaos-experiment":
        return {
            "service": "control",
            "op": "start_chaos_experiment",
            "name": args.name,
        }
    if command == "stop-chaos-experiment":
        return {
            "service": "control",
            "op": "stop_chaos_experiment",
            "name": args.name,
        }
    if command == "get-chaos-results":
        return {
            "service": "control",
            "op": "get_chaos_results",
            "name": args.name,
        }
    if command == "list-chaos":
        return {
            "service": "control",
            "op": "list_chaos_experiments",
        }
    if command == "create-game-day":
        return {
            "service": "control",
            "op": "create_game_day",
            "name": args.name,
            "scenario": args.scenario,
            "participants": args.participants,
        }
    if command == "list-game-days":
        return {
            "service": "control",
            "op": "list_game_days",
        }
    if command == "configure-cost-allocation":
        return {
            "service": "control",
            "op": "configure_cost_allocation",
            "container_id": args.container_id,
            "cost_per_hour": float(getattr(args, 'cost_per_hour', 0)),
            "billing_tag": getattr(args, 'billing_tag', ''),
            "team": getattr(args, 'team', ''),
        }
    if command == "get-container-cost":
        return {
            "service": "control",
            "op": "get_container_cost",
            "container_id": args.container_id,
            "hours": float(getattr(args, 'hours', 24)),
        }
    if command == "get-team-cost":
        return {
            "service": "control",
            "op": "get_team_cost_summary",
            "team": args.team,
            "hours": float(getattr(args, 'hours', 24)),
        }
    if command == "get-fleet-cost":
        return {
            "service": "control",
            "op": "get_fleet_cost_summary",
            "hours": float(getattr(args, 'hours', 24)),
        }
    if command == "get-billing-breakdown":
        return {
            "service": "control",
            "op": "get_billing_breakdown",
            "hours": float(getattr(args, 'hours', 24)),
        }
    if command == "create-slo":
        return {
            "service": "control",
            "op": "create_slo",
            "name": args.name,
            "target_percentage": float(getattr(args, 'target_percentage', 99.9)),
            "window_days": int(getattr(args, 'window_days', 30)),
            "sli_type": getattr(args, 'sli_type', 'availability'),
        }
    if command == "record-sli-event":
        return {
            "service": "control",
            "op": "record_sli_event",
            "slo_name": args.slo_name,
            "is_good": getattr(args, 'is_good', True),
        }
    if command == "get-slo-status":
        return {
            "service": "control",
            "op": "get_slo_status",
            "slo_name": args.slo_name,
        }
    if command == "list-slos":
        return {
            "service": "control",
            "op": "list_slos",
        }
    if command == "get-error-budget-report":
        return {
            "service": "control",
            "op": "get_error_budget_report",
        }
    if command == "delete-slo":
        return {
            "service": "control",
            "op": "delete_slo",
            "name": args.name,
        }
    raise ValueError(f"unknown command: {command!r}")

    if command == "analyze-resource-usage":
        return {
            "service": "control",
            "op": "analyze_resource_usage",
            "container_id": args.container_id,
        }
    if command == "recommend-right-sizing":
        return {
            "service": "control",
            "op": "recommend_right_sizing",
            "container_id": args.container_id,
        }
    if command == "apply-right-sizing":
        return {
            "service": "control",
            "op": "apply_right_sizing",
            "container_id": args.container_id,
            "dry_run": getattr(args, 'dry_run', True),
        }
    if command == "fleet-right-sizing-report":
        return {
            "service": "control",
            "op": "fleet_right_sizing_report",
        }
    if command == "create-mesh-cluster":
        return {
            "service": "control",
            "op": "create_mesh_cluster",
            "name": args.name,
            "endpoint": args.endpoint,
            "region": getattr(args, 'region', 'default'),
        }
    if command == "add-mesh-service":
        return {
            "service": "control",
            "op": "add_mesh_service",
            "cluster_name": args.cluster_name,
            "service_name": args.service_name,
            "port": int(getattr(args, 'port', 80)),
        }
    if command == "create-traffic-policy":
        return {
            "service": "control",
            "op": "create_traffic_policy",
            "name": args.name,
            "source_service": args.source_service,
            "destination_service": args.destination_service,
        }
    if command == "evaluate-traffic-policy":
        return {
            "service": "control",
            "op": "evaluate_traffic_policy",
            "source": args.source,
            "destination": args.destination,
        }
    if command == "get-mesh-topology":
        return {
            "service": "control",
            "op": "get_mesh_topology",
        }
    if command == "list-mesh-policies":
        return {
            "service": "control",
            "op": "list_mesh_policies",
        }
    if command == "delete-mesh-policy":
        return {
            "service": "control",
            "op": "delete_mesh_policy",
            "name": args.name,
        }
    if command == "configure-auto-rollback":
        return {
            "service": "control",
            "op": "configure_auto_rollback",
            "slo_name": args.slo_name,
            "deployment_name": getattr(args, 'deployment_name', None),
            "canary_name": getattr(args, 'canary_name', None),
            "breach_threshold": float(getattr(args, 'breach_threshold', 1.0)),
            "cooldown_seconds": int(getattr(args, 'cooldown_seconds', 300)),
        }
    if command == "check-auto-rollback":
        return {
            "service": "control",
            "op": "check_and_auto_rollback",
        }
    if command == "get-auto-rollback-status":
        return {
            "service": "control",
            "op": "get_auto_rollback_status",
        }
    if command == "get-auto-rollback-history":
        return {
            "service": "control",
            "op": "get_auto_rollback_history",
            "slo_name": args.slo_name,
        }
    if command == "disable-auto-rollback":
        return {
            "service": "control",
            "op": "disable_auto_rollback",
            "slo_name": args.slo_name,
        }
    if command == "delete-auto-rollback":
        return {
            "service": "control",
            "op": "delete_auto_rollback",
            "slo_name": args.slo_name,
        }
    if command == "run-benchmark":
        return {
            "service": "control",
            "op": "run_benchmark",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'default'),
            "iterations": int(getattr(args, 'iterations', 100)),
        }
    if command == "detect-regression":
        return {
            "service": "control",
            "op": "detect_regression",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'default'),
            "threshold_pct": float(getattr(args, 'threshold_pct', 20)),
        }
    if command == "get-benchmark-history":
        return {
            "service": "control",
            "op": "get_benchmark_history",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'default'),
        }
    if command == "fleet-benchmark-summary":
        return {
            "service": "control",
            "op": "fleet_benchmark_summary",
        }
    if command == "create-tenant":
        return {
            "service": "control",
            "op": "create_tenant",
            "name": args.name,
            "isolation_level": getattr(args, 'isolation_level', 'strict'),
        }
    if command == "assign-to-tenant":
        return {
            "service": "control",
            "op": "assign_container_to_tenant",
            "tenant_name": args.tenant_name,
            "container_id": args.container_id,
        }
    if command == "remove-from-tenant":
        return {
            "service": "control",
            "op": "remove_container_from_tenant",
            "tenant_name": args.tenant_name,
            "container_id": args.container_id,
        }
    if command == "get-tenant-usage":
        return {
            "service": "control",
            "op": "get_tenant_usage",
            "tenant_name": args.tenant_name,
        }
    if command == "list-tenants":
        return {
            "service": "control",
            "op": "list_tenants",
        }
    if command == "delete-tenant":
        return {
            "service": "control",
            "op": "delete_tenant",
            "name": args.name,
        }
    if command == "check-tenant-isolation":
        return {
            "service": "control",
            "op": "check_tenant_isolation",
            "tenant_a": args.tenant_a,
            "tenant_b": args.tenant_b,
        }
    if command == "start-profile":
        return {
            "service": "control",
            "op": "start_profile",
            "container_id": args.container_id,
            "profile_type": getattr(args, 'profile_type', 'cpu'),
            "duration_seconds": int(getattr(args, 'duration_seconds', 30)),
        }
    if command == "stop-profile":
        return {
            "service": "control",
            "op": "stop_profile",
            "profile_id": args.profile_id,
        }
    if command == "get-flame-graph":
        return {
            "service": "control",
            "op": "get_flame_graph",
            "profile_id": args.profile_id,
        }
    if command == "get-profile-summary":
        return {
            "service": "control",
            "op": "get_profile_summary",
            "profile_id": args.profile_id,
        }
    if command == "list-profiles":
        return {
            "service": "control",
            "op": "list_profiles",
            "container_id": getattr(args, 'container_id', None),
        }
    if command == "delete-profile":
        return {
            "service": "control",
            "op": "delete_profile",
            "profile_id": args.profile_id,
        }
    if command == "register-config-schema":
        return {
            "service": "control",
            "op": "register_config_schema",
            "name": args.name,
            "required_fields": getattr(args, 'required_fields', None),
        }
    if command == "validate-config":
        return {
            "service": "control",
            "op": "validate_config",
            "schema_name": args.schema_name,
            "config": json.loads(args.config) if hasattr(args, 'config') and args.config else {},
        }
    if command == "snapshot-config":
        return {
            "service": "control",
            "op": "snapshot_config",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'baseline'),
        }
    if command == "detect-config-drift":
        return {
            "service": "control",
            "op": "detect_config_drift",
            "container_id": args.container_id,
            "name": getattr(args, 'name', 'baseline'),
        }
    if command == "list-config-snapshots":
        return {
            "service": "control",
            "op": "list_config_snapshots",
            "container_id": args.container_id,
        }
    if command == "enforce-config-policy":
        return {
            "service": "control",
            "op": "enforce_config_policy",
            "container_id": args.container_id,
            "policy": json.loads(args.policy) if hasattr(args, 'policy') and args.policy else {},
        }
    if command == "create-role":
        return {
            "service": "control",
            "op": "create_role",
            "name": args.name,
            "permissions": args.permissions,
        }
    if command == "create-rbac-user":
        return {
            "service": "control",
            "op": "create_rbac_user",
            "name": args.name,
            "roles": getattr(args, 'roles', None),
        }
    if command == "assign-role":
        return {
            "service": "control",
            "op": "assign_role",
            "user_name": args.user_name,
            "role_name": args.role_name,
        }
    if command == "revoke-role":
        return {
            "service": "control",
            "op": "revoke_role",
            "user_name": args.user_name,
            "role_name": args.role_name,
        }
    if command == "check-permission":
        return {
            "service": "control",
            "op": "check_permission",
            "user_name": args.user_name,
            "permission": args.permission,
        }
    if command == "get-user-permissions":
        return {
            "service": "control",
            "op": "get_user_permissions",
            "user_name": args.user_name,
        }
    if command == "list-rbac-users":
        return {
            "service": "control",
            "op": "list_rbac_users",
        }
    if command == "list-rbac-roles":
        return {
            "service": "control",
            "op": "list_rbac_roles",
        }
    if command == "delete-rbac-user":
        return {
            "service": "control",
            "op": "delete_rbac_user",
            "name": args.name,
        }
    if command == "delete-rbac-role":
        return {
            "service": "control",
            "op": "delete_rbac_role",
            "name": args.name,
        }
    if command == "configure-audit-stream":
        return {
            "service": "control",
            "op": "configure_audit_stream",
            "name": args.name,
            "severity_threshold": getattr(args, 'severity_threshold', 'warning'),
            "alert_channels": getattr(args, 'alert_channels', None),
        }
    if command == "emit-audit-event":
        return {
            "service": "control",
            "op": "emit_audit_event",
            "stream_name": args.stream_name,
            "event_type": args.event_type,
            "message": getattr(args, 'message', ''),
            "severity": getattr(args, 'severity', 'info'),
        }
    if command == "get-stream-status":
        return {
            "service": "control",
            "op": "get_stream_status",
            "name": args.name,
        }
    if command == "list-audit-streams":
        return {
            "service": "control",
            "op": "list_audit_streams",
        }
    if command == "disable-audit-stream":
        return {
            "service": "control",
            "op": "disable_audit_stream",
            "name": args.name,
        }
    if command == "enable-audit-stream":
        return {
            "service": "control",
            "op": "enable_audit_stream",
            "name": args.name,
        }
    if command == "delete-audit-stream":
        return {
            "service": "control",
            "op": "delete_audit_stream",
            "name": args.name,
        }
    if command == "audit-stream-summary":
        return {
            "service": "control",
            "op": "get_audit_stream_summary",
        }
    if command == "configure-cost-attribution":
        return {
            "service": "control",
            "op": "configure_cost_attribution",
            "container_id": args.container_id,
            "cost_per_hour": getattr(args, 'cost_per_hour', 0.0),
            "billing_tag": getattr(args, 'billing_tag', 'default'),
            "team": getattr(args, 'team', 'unassigned'),
            "currency": getattr(args, 'currency', 'USD'),
        }
    if command == "get-pod-cost":
        return {
            "service": "control",
            "op": "get_pod_cost",
            "container_id": args.container_id,
        }
    if command == "chargeback-report":
        return {
            "service": "control",
            "op": "chargeback_report",
            "team": getattr(args, 'team', None),
        }
    if command == "fleet-cost-overview":
        return {
            "service": "control",
            "op": "fleet_cost_overview",
        }
    if command == "simulate-network-policy":
        return {
            "service": "control",
            "op": "simulate_network_policy",
            "source_container_id": args.source_container_id,
            "dest_container_id": args.dest_container_id,
            "dest_port": getattr(args, 'dest_port', 80),
            "protocol": getattr(args, 'protocol', 'tcp'),
        }
    if command == "what-if-policy-change":
        return {
            "service": "control",
            "op": "what_if_policy_change",
            "source_container_id": args.source_container_id,
            "dest_container_id": args.dest_container_id,
            "dest_port": getattr(args, 'dest_port', 80),
            "protocol": getattr(args, 'protocol', 'tcp'),
            "new_policy_effect": getattr(args, 'new_policy_effect', 'deny'),
        }
    if command == "register-lifecycle-hook":
        return {
            "service": "control",
            "op": "register_lifecycle_hook",
            "container_id": args.container_id,
            "phase": args.phase,
            "action": args.action,
            "command": getattr(args, 'hook_command', None),
            "timeout_s": getattr(args, 'timeout_s', 30.0),
            "on_failure": getattr(args, 'on_failure', 'rollback'),
        }
    if command == "execute-lifecycle-hooks":
        return {
            "service": "control",
            "op": "execute_lifecycle_hooks",
            "container_id": args.container_id,
            "phase": args.phase,
        }
    if command == "list-lifecycle-hooks":
        return {
            "service": "control",
            "op": "list_lifecycle_hooks",
            "container_id": args.container_id,
        }
    if command == "register-webhook":
        return {
            "service": "control",
            "op": "register_admission_webhook",
            "name": args.name,
            "webhook_type": getattr(args, 'webhook_type', 'validating'),
            "url": getattr(args, 'url', ''),
            "failure_policy": getattr(args, 'failure_policy', 'fail'),
        }
    if command == "evaluate-admission":
        return {
            "service": "control",
            "op": "evaluate_admission",
            "webhook_name": args.webhook_name,
            "container_id": args.container_id,
            "operation": getattr(args, 'operation', 'CREATE'),
        }
    if command == "webhook-status":
        return {
            "service": "control",
            "op": "get_webhook_status",
            "webhook_name": args.webhook_name,
        }
    if command == "list-webhooks":
        return {
            "service": "control",
            "op": "list_admission_webhooks",
        }
    if command == "toggle-webhook":
        return {
            "service": "control",
            "op": "toggle_webhook",
            "webhook_name": args.webhook_name,
            "enabled": getattr(args, 'enabled', True),
        }
    if command == "delete-webhook":
        return {
            "service": "control",
            "op": "delete_webhook",
            "webhook_name": args.webhook_name,
        }
    if command == "register-image-signature":
        return {
            "service": "control",
            "op": "register_image_signature",
            "image_ref": args.image_ref,
            "digest": args.digest,
            "signer": args.signer,
            "signature_type": getattr(args, 'signature_type', 'cosign'),
        }
    if command == "set-build-provenance":
        return {
            "service": "control",
            "op": "set_build_provenance",
            "image_ref": args.image_ref,
            "builder_id": args.builder_id,
            "build_config_uri": getattr(args, 'build_config_uri', ''),
            "source_uri": getattr(args, 'source_uri', ''),
            "source_hash": getattr(args, 'source_hash', ''),
        }
    if command == "verify-image":
        return {
            "service": "control",
            "op": "verify_image",
            "image_ref": args.image_ref,
            "require_signature": getattr(args, 'require_signature', True),
            "require_provenance": getattr(args, 'require_provenance', False),
        }
    if command == "supply-chain-status":
        return {
            "service": "control",
            "op": "get_supply_chain_status",
        }
    if command == "image-provenance":
        return {
            "service": "control",
            "op": "get_image_provenance",
            "image_ref": args.image_ref,
        }
    if command == "create-resource-pool":
        return {
            "service": "control",
            "op": "create_resource_pool",
            "name": args.name,
            "total_memory_mb": getattr(args, 'total_memory_mb', 1024),
            "total_cpu_shares": getattr(args, 'total_cpu_shares', 1024),
            "total_pids": getattr(args, 'total_pids', 256),
        }
    if command == "join-resource-pool":
        return {
            "service": "control",
            "op": "join_resource_pool",
            "pool_name": args.pool_name,
            "container_id": args.container_id,
            "memory_mb": getattr(args, 'memory_mb', 0),
            "cpu_shares": getattr(args, 'cpu_shares', 0),
            "pids": getattr(args, 'pids', 0),
        }
    if command == "leave-resource-pool":
        return {
            "service": "control",
            "op": "leave_resource_pool",
            "pool_name": args.pool_name,
            "container_id": args.container_id,
        }
    if command == "reserve-pool-resources":
        return {
            "service": "control",
            "op": "reserve_pool_resources",
            "pool_name": args.pool_name,
            "reservation_name": args.reservation_name,
            "memory_mb": getattr(args, 'memory_mb', 0),
            "cpu_shares": getattr(args, 'cpu_shares', 0),
            "pids": getattr(args, 'pids', 0),
            "ttl_seconds": getattr(args, 'ttl_seconds', 3600.0),
        }
    if command == "pool-status":
        return {
            "service": "control",
            "op": "get_pool_status",
            "pool_name": args.pool_name,
        }
    if command == "list-resource-pools":
        return {
            "service": "control",
            "op": "list_resource_pools",
        }
    if command == "delete-resource-pool":
        return {
            "service": "control",
            "op": "delete_resource_pool",
            "pool_name": args.pool_name,
        }
    if command == "register-gpu":
        return {
            "service": "control",
            "op": "register_gpu_device",
            "device_id": args.device_id,
            "device_type": getattr(args, 'device_type', 'nvidia'),
            "memory_mb": getattr(args, 'memory_mb', 8192),
            "compute_cap": getattr(args, 'compute_cap', '8.9'),
        }
    if command == "assign-gpu":
        return {
            "service": "control",
            "op": "assign_gpu",
            "device_id": args.device_id,
            "container_id": args.container_id,
            "timeslice_ms": getattr(args, 'timeslice_ms', 0),
            "memory_limit_mb": getattr(args, 'memory_limit_mb', None),
        }
    if command == "release-gpu":
        return {
            "service": "control",
            "op": "release_gpu",
            "device_id": args.device_id,
        }
    if command == "gpu-status":
        return {
            "service": "control",
            "op": "get_gpu_status",
        }
    if command == "create-policy":
        return {
            "service": "control",
            "op": "create_policy",
            "name": args.name,
            "rules": getattr(args, 'rules', []),
            "effect": getattr(args, 'effect', 'deny'),
            "enforcement": getattr(args, 'enforcement', 'strict'),
        }
    if command == "evaluate-policy":
        return {
            "service": "control",
            "op": "evaluate_policy",
            "policy_name": args.policy_name,
            "container_id": args.container_id,
        }
    if command == "list-policies":
        return {
            "service": "control",
            "op": "list_policies",
        }
    if command == "toggle-policy":
        return {
            "service": "control",
            "op": "toggle_policy",
            "policy_name": args.policy_name,
            "enabled": getattr(args, 'enabled', True),
        }
    if command == "policy-violations":
        return {
            "service": "control",
            "op": "get_policy_violations",
            "policy_name": args.policy_name,
        }
    if command == "create-composition":
        return {
            "service": "control",
            "op": "create_composition",
            "name": args.name,
            "container_ids": getattr(args, 'container_ids', []),
        }
    if command == "add-sidecar":
        return {
            "service": "control",
            "op": "add_sidecar",
            "composition_name": args.composition_name,
            "sidecar_container_id": args.sidecar_container_id,
            "mount_paths": getattr(args, 'mount_paths', None),
        }
    if command == "add-init-container":
        return {
            "service": "control",
            "op": "add_init_container",
            "composition_name": args.composition_name,
            "init_container_id": args.init_container_id,
            "phase": getattr(args, 'phase', 'pre-start'),
        }
    if command == "composition-status":
        return {
            "service": "control",
            "op": "get_composition_status",
            "composition_name": args.composition_name,
        }
    if command == "list-compositions":
        return {
            "service": "control",
            "op": "list_compositions",
        }
    if command == "start-composition":
        return {
            "service": "control",
            "op": "start_composition",
            "composition_name": args.composition_name,
        }
    if command == "stop-composition":
        return {
            "service": "control",
            "op": "stop_composition",
            "composition_name": args.composition_name,
        }
    if command == "create-workload-identity":
        return {
            "service": "control",
            "op": "create_workload_identity",
            "name": args.name,
            "spiffe_id": args.spiffe_id,
            "trust_domain": getattr(args, 'trust_domain', 'nyrqis.local'),
            "sans": getattr(args, 'sans', None),
            "ttl_seconds": getattr(args, 'ttl_seconds', 3600.0),
        }
    if command == "validate-workload-identity":
        return {
            "service": "control",
            "op": "validate_workload_identity",
            "name": args.name,
        }
    if command == "rotate-workload-identity":
        return {
            "service": "control",
            "op": "rotate_workload_identity",
            "name": args.name,
            "new_ttl_seconds": getattr(args, 'new_ttl_seconds', None),
        }
    if command == "revoke-workload-identity":
        return {
            "service": "control",
            "op": "revoke_workload_identity",
            "name": args.name,
        }
    if command == "list-workload-identities":
        return {
            "service": "control",
            "op": "list_workload_identities",
        }
    if command == "scan-image":
        return {
            "service": "control",
            "op": "scan_image_vulnerabilities",
            "image_ref": args.image_ref,
            "severity_filter": getattr(args, 'severity_filter', None),
        }
    if command == "set-vuln-policy":
        return {
            "service": "control",
            "op": "set_vuln_scan_policy",
            "name": args.name,
            "block_on_critical": getattr(args, 'block_on_critical', True),
            "block_on_high": getattr(args, 'block_on_high', False),
            "max_vulns": getattr(args, 'max_vulns', 0),
        }
    if command == "evaluate-vuln-policy":
        return {
            "service": "control",
            "op": "evaluate_vuln_policy",
            "policy_name": args.policy_name,
            "image_ref": args.image_ref,
        }
    if command == "scan-history":
        return {
            "service": "control",
            "op": "get_scan_history",
        }
    if command == "register-ebpf-hook":
        return {
            "service": "control",
            "op": "register_ebpf_hook",
            "name": args.name,
            "hook_type": getattr(args, 'hook_type', 'kprobe'),
            "target": getattr(args, 'target', ''),
            "metric_name": getattr(args, 'metric_name', ''),
            "description": getattr(args, 'description', ''),
        }
    if command == "attach-ebpf-hook":
        return {
            "service": "control",
            "op": "attach_ebpf_hook",
            "hook_name": args.hook_name,
            "container_id": getattr(args, 'container_id', None),
        }
    if command == "record-ebpf-event":
        return {
            "service": "control",
            "op": "record_ebpf_event",
            "hook_name": args.hook_name,
            "value": getattr(args, 'value', 1.0),
        }
    if command == "ebpf-metrics":
        return {
            "service": "control",
            "op": "get_ebpf_metrics",
            "metric_name": getattr(args, 'metric_name', None),
        }
    if command == "list-ebpf-hooks":
        return {
            "service": "control",
            "op": "list_ebpf_hooks",
        }
    if command == "register-topology-node":
        return {
            "service": "control",
            "op": "register_topology_node",
            "node_id": args.node_id,
            "region": getattr(args, 'region', 'us-east-1'),
            "zone": getattr(args, 'zone', 'us-east-1a'),
            "labels": getattr(args, 'labels', None),
            "taints": getattr(args, 'taints', None),
            "resources": getattr(args, 'resources', None),
        }
    if command == "set-topology-constraints":
        return {
            "service": "control",
            "op": "set_topology_constraints",
            "container_id": args.container_id,
            "node_selector": getattr(args, 'node_selector', None),
            "preferred_region": getattr(args, 'preferred_region', None),
            "anti_affinity_nodes": getattr(args, 'anti_affinity_nodes', None),
        }
    if command == "find-suitable-nodes":
        return {
            "service": "control",
            "op": "find_suitable_nodes",
            "container_id": args.container_id,
        }
    if command == "schedule-with-topology":
        return {
            "service": "control",
            "op": "schedule_with_topology",
            "container_id": args.container_id,
        }
    if command == "topology-view":
        return {
            "service": "control",
            "op": "get_topology_view",
        }
    if command == "create-chaos-schedule":
        return {
            "service": "control",
            "op": "create_chaos_schedule",
            "name": args.name,
            "fault_types": getattr(args, 'fault_types', ['process_kill']),
            "schedule_cron": getattr(args, 'schedule_cron', '0 2 * * *'),
            "targets": getattr(args, 'targets', None),
            "max_targets": getattr(args, 'max_targets', 1),
            "window_minutes": getattr(args, 'window_minutes', 30),
            "auto_rollback": getattr(args, 'auto_rollback', True),
        }
    if command == "execute-chaos-attack":
        return {
            "service": "control",
            "op": "execute_chaos_attack",
            "schedule_name": args.schedule_name,
            "dry_run": getattr(args, 'dry_run', False),
        }
    if command == "chaos-schedule-status":
        return {
            "service": "control",
            "op": "get_chaos_schedule_status",
            "schedule_name": args.schedule_name,
        }
    if command == "list-chaos-schedules":
        return {
            "service": "control",
            "op": "list_chaos_schedules",
        }
    if command == "chaos-log":
        return {
            "service": "control",
            "op": "get_chaos_log",
            "limit": getattr(args, 'limit', 10),
        }
    if command == "register-multiarch-image":
        return {
            "service": "control",
            "op": "register_multiarch_image",
            "name": args.name,
            "architectures": getattr(args, 'architectures', []),
            "default_arch": getattr(args, 'default_arch', 'linux/amd64'),
        }
    if command == "resolve-image-arch":
        return {
            "service": "control",
            "op": "resolve_image_for_arch",
            "name": args.name,
            "target_arch": getattr(args, 'target_arch', 'linux/amd64'),
        }
    if command == "list-multiarch-images":
        return {
            "service": "control",
            "op": "list_multiarch_images",
        }
    if command == "build-matrix":
        return {
            "service": "control",
            "op": "get_build_matrix",
            "name": args.name,
        }
    if command == "register-runtime-class":
        return {
            "service": "control",
            "op": "register_runtime_class",
            "name": args.name,
            "runtime_handler": getattr(args, 'runtime_handler', 'nyrqis-default'),
            "immutable_rootfs": getattr(args, 'immutable_rootfs', False),
        }
    if command == "assign-runtime-class":
        return {
            "service": "control",
            "op": "assign_runtime_class",
            "container_id": args.container_id,
            "class_name": args.class_name,
        }
    if command == "list-runtime-classes":
        return {
            "service": "control",
            "op": "list_runtime_classes",
        }
    if command == "create-replay-trace":
        return {
            "service": "control",
            "op": "create_replay_trace",
            "container_id": args.container_id,
            "trace_name": args.trace_name,
        }
    if command == "record-replay-event":
        return {
            "service": "control",
            "op": "record_replay_event",
            "trace_name": args.trace_name,
            "event_type": args.event_type,
        }
    if command == "stop-replay-trace":
        return {
            "service": "control",
            "op": "stop_replay_trace",
            "trace_name": args.trace_name,
        }
    if command == "replay-trace":
        return {
            "service": "control",
            "op": "replay_trace",
            "trace_name": args.trace_name,
            "from_snapshot": getattr(args, 'from_snapshot', None),
        }
    if command == "list-replay-traces":
        return {
            "service": "control",
            "op": "list_replay_traces",
        }
    if command == "cost-trends":
        return {
            "service": "control",
            "op": "analyze_cost_trends",
            "container_id": args.container_id,
            "lookback_days": getattr(args, 'lookback_days', 7),
        }
    if command == "cost-recommendations":
        return {
            "service": "control",
            "op": "recommend_cost_optimization",
            "container_id": args.container_id,
        }
    if command == "fleet-cost-report":
        return {
            "service": "control",
            "op": "fleet_cost_optimization_report",
        }
    if command == "forecast-cost":
        return {
            "service": "control",
            "op": "forecast_cost",
            "container_id": args.container_id,
            "days_ahead": getattr(args, 'days_ahead', 30),
        }
    if command == "create-affinity-rule":
        return {
            "service": "control",
            "op": "create_affinity_rule",
            "name": args.name,
            "rule_type": getattr(args, 'rule_type', 'affinity'),
            "required": getattr(args, 'required', False),
            "weight": getattr(args, 'weight', 100),
        }
    if command == "evaluate-affinity":
        return {
            "service": "control",
            "op": "evaluate_affinity",
            "container_id": args.container_id,
            "target_labels": getattr(args, 'target_labels', {}),
        }
    if command == "create-priority-class":
        return {
            "service": "control",
            "op": "create_priority_class",
            "name": args.name,
            "value": getattr(args, 'value', 0),
            "preemption_policy": getattr(args, 'preemption_policy', 'PreemptLowerPriority'),
        }
    if command == "assign-priority-class":
        return {
            "service": "control",
            "op": "assign_priority_class",
            "container_id": args.container_id,
            "class_name": args.class_name,
        }
    if command == "evaluate-preemption":
        return {
            "service": "control",
            "op": "evaluate_preemption",
            "evictor_id": args.evictor_id,
            "victim_id": args.victim_id,
        }
    if command == "list-priority-classes":
        return {
            "service": "control",
            "op": "list_priority_classes",
        }
    if command == "configure-autoscaler":
        return {
            "service": "control",
            "op": "configure_autoscaler",
            "container_id": args.container_id,
            "min_replicas": getattr(args, 'min_replicas', 1),
            "max_replicas": getattr(args, 'max_replicas', 10),
            "target_cpu_pct": getattr(args, 'target_cpu_pct', 70.0),
            "predictive": getattr(args, 'predictive', False),
        }
    if command == "evaluate-autoscaling":
        return {
            "service": "control",
            "op": "evaluate_autoscaling",
            "container_id": args.container_id,
            "current_cpu_pct": getattr(args, 'current_cpu_pct', 0.0),
            "current_memory_pct": getattr(args, 'current_memory_pct', 0.0),
        }
    if command == "autoscaler-status":
        return {
            "service": "control",
            "op": "get_autoscaler_status",
            "container_id": args.container_id,
        }
    if command == "list-autoscalers":
        return {
            "service": "control",
            "op": "list_autoscalers",
        }
    if command == "sign-image":
        return {
            "service": "control",
            "op": "sign_image",
            "image_ref": args.image_ref,
            "key_ref": getattr(args, 'key_ref', 'cosign-key'),
        }
    if command == "verify-image-signature":
        return {
            "service": "control",
            "op": "verify_image_signature",
            "image_ref": args.image_ref,
            "key_ref": getattr(args, 'key_ref', None),
        }
    if command == "list-signed-images":
        return {
            "service": "control",
            "op": "list_signed_images",
        }
    if command == "revoke-image-signature":
        return {
            "service": "control",
            "op": "revoke_image_signature",
            "image_ref": args.image_ref,
        }
    if command == "configure-otel":
        return {
            "service": "control",
            "op": "configure_otel_exporter",
            "endpoint": getattr(args, 'endpoint', 'http://localhost:4317'),
            "protocol": getattr(args, 'protocol', 'grpc'),
            "service_name": getattr(args, 'service_name', 'nyrqis'),
        }
    if command == "record-otel-metric":
        return {
            "service": "control",
            "op": "record_otel_metric",
            "name": args.name,
            "value": args.value,
            "metric_type": getattr(args, 'metric_type', 'gauge'),
        }
    if command == "otel-status":
        return {
            "service": "control",
            "op": "get_otel_status",
        }
    if command == "container-metrics":
        return {
            "service": "control",
            "op": "get_container_metrics",
            "container_id": args.container_id,
        }
    if command == "register-fed-cluster":
        return {
            "service": "control",
            "op": "register_federation_cluster",
            "cluster_id": args.cluster_id,
            "endpoint": getattr(args, 'endpoint', ''),
            "region": getattr(args, 'region', 'us-east-1'),
            "zone": getattr(args, 'zone', 'us-east-1a'),
        }
    if command == "global-schedule":
        return {
            "service": "control",
            "op": "global_schedule",
            "workload_name": args.workload_name,
            "required_cpu": getattr(args, 'required_cpu', 1),
            "required_memory_mb": getattr(args, 'required_memory_mb', 256),
            "preferred_region": getattr(args, 'preferred_region', None),
        }
    if command == "federation-status":
        return {
            "service": "control",
            "op": "get_federation_status",
        }
    if command == "list-fed-clusters":
        return {
            "service": "control",
            "op": "list_federation_clusters",
        }
    if command == "fed-workloads":
        return {
            "service": "control",
            "op": "get_federation_workloads",
        }
    if command == "create-batch-job":
        return {
            "service": "control",
            "op": "create_batch_job",
            "name": args.name,
            "commands": getattr(args, 'commands', []),
            "max_parallel": getattr(args, 'max_parallel', 1),
        }
    if command == "execute-batch-job":
        return {
            "service": "control",
            "op": "execute_batch_job",
            "job_name": args.job_name,
        }
    if command == "batch-job-status":
        return {
            "service": "control",
            "op": "get_batch_job_status",
            "job_name": args.job_name,
        }
    if command == "list-batch-jobs":
        return {
            "service": "control",
            "op": "list_batch_jobs",
        }
    if command == "configure-memory-compaction":
        return {
            "service": "control",
            "op": "configure_memory_compaction",
            "container_id": args.container_id,
            "compaction_threshold_pct": getattr(args, 'threshold', 70.0),
            "auto_compact": getattr(args, 'auto_compact', True),
        }
    if command == "run-memory-compaction":
        return {
            "service": "control",
            "op": "run_memory_compaction",
            "container_id": args.container_id,
        }
    if command == "run-memory-defragmentation":
        return {
            "service": "control",
            "op": "run_memory_defragmentation",
            "container_id": args.container_id,
        }
    if command == "fs-snapshot":
        return {
            "service": "control",
            "op": "create_filesystem_snapshot",
            "container_id": args.container_id,
            "snapshot_name": args.snapshot_name,
        }
    if command == "fs-restore":
        return {
            "service": "control",
            "op": "restore_filesystem_snapshot",
            "snapshot_name": args.snapshot_name,
            "target_container_id": getattr(args, 'target_container_id', None),
        }
    if command == "list-fs-snapshots":
        return {
            "service": "control",
            "op": "list_filesystem_snapshots",
            "container_id": getattr(args, 'container_id', None),
        }
    if command == "compare-fs-snapshots":
        return {
            "service": "control",
            "op": "compare_snapshots",
            "snapshot_a": args.snapshot_a,
            "snapshot_b": args.snapshot_b,
        }

    if command == "create-network-monitor":
        return {
            "service": "control",
            "op": "create_network_monitor",
            "container_id": args.container_id,
            "targets": getattr(args, 'targets', None),
            "interval_s": getattr(args, 'interval_s', 1.0),
        }
    if command == "record-network-latency":
        return {
            "service": "control",
            "op": "record_network_latency",
            "monitor_id": args.monitor_id,
            "target": args.target,
            "latency_ms": args.latency_ms,
        }
    if command == "record-bandwidth":
        return {
            "service": "control",
            "op": "record_bandwidth",
            "monitor_id": args.monitor_id,
            "target": args.target,
            "bytes_sent": args.bytes_sent,
            "bytes_received": args.bytes_received,
        }
    if command == "network-latency-stats":
        return {
            "service": "control",
            "op": "get_network_latency_stats",
            "monitor_id": args.monitor_id,
        }
    if command == "bandwidth-stats":
        return {
            "service": "control",
            "op": "get_bandwidth_stats",
            "monitor_id": args.monitor_id,
        }
    if command == "detect-network-anomalies":
        return {
            "service": "control",
            "op": "detect_network_anomalies",
            "monitor_id": args.monitor_id,
            "z_threshold": getattr(args, 'z_threshold', 3.0),
        }
    if command == "stop-network-monitor":
        return {
            "service": "control",
            "op": "stop_network_monitor",
            "monitor_id": args.monitor_id,
        }
    if command == "create-io-profiler":
        return {
            "service": "control",
            "op": "create_io_profiler",
            "container_id": args.container_id,
            "sample_interval_s": getattr(args, 'sample_interval_s', 0.5),
        }
    if command == "record-io-sample":
        return {
            "service": "control",
            "op": "record_io_sample",
            "profiler_id": args.profiler_id,
            "path": args.path,
            "op": args.io_op,
            "bytes": args.bytes_transferred,
            "latency_ms": args.latency_ms,
        }
    if command == "io-profile":
        return {
            "service": "control",
            "op": "get_io_profile",
            "profiler_id": args.profiler_id,
        }
    if command == "detect-io-hotspots":
        return {
            "service": "control",
            "op": "detect_io_hotspots",
            "profiler_id": args.profiler_id,
            "top_n": getattr(args, 'top_n', 5),
        }
    if command == "create-storage-cache":
        return {
            "service": "control",
            "op": "create_storage_cache",
            "container_id": args.container_id,
            "max_size_mb": getattr(args, 'max_size_mb', 256.0),
            "eviction_policy": getattr(args, 'eviction_policy', 'lru'),
        }
    if command == "cache-put":
        return {
            "service": "control",
            "op": "cache_put",
            "cache_id": args.cache_id,
            "key": args.key,
            "data": args.data.encode() if hasattr(args.data, 'encode') else args.data,
            "ttl_s": getattr(args, 'ttl_s', 300.0),
        }
    if command == "cache-get":
        return {
            "service": "control",
            "op": "cache_get",
            "cache_id": args.cache_id,
            "key": args.key,
        }
    if command == "cache-stats":
        return {
            "service": "control",
            "op": "cache_stats",
            "cache_id": args.cache_id,
        }
    if command == "cache-invalidate":
        return {
            "service": "control",
            "op": "cache_invalidate",
            "cache_id": args.cache_id,
            "key": getattr(args, 'key', None),
        }
    if command == "create-audit-chain":
        return {
            "service": "control",
            "op": "create_audit_chain",
            "container_id": args.container_id,
        }
    if command == "append-audit-entry":
        return {
            "service": "control",
            "op": "append_audit_entry",
            "chain_id": args.chain_id,
            "op": args.audit_op,
            "result": getattr(args, 'result', None),
        }
    if command == "verify-audit-chain":
        return {
            "service": "control",
            "op": "verify_audit_chain",
            "chain_id": args.chain_id,
        }
    if command == "audit-entry":
        return {
            "service": "control",
            "op": "get_audit_entry",
            "chain_id": args.chain_id,
            "index": args.index,
        }
    if command == "audit-summary":
        return {
            "service": "control",
            "op": "get_audit_summary",
            "chain_id": args.chain_id,
        }
    if command == "export-audit-chain":
        return {
            "service": "control",
            "op": "export_audit_chain",
            "chain_id": args.chain_id,
            "format": getattr(args, 'format', 'json'),
        }

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
    if command == "images-create-layer":
        return (
            f"layer created: {resp.get('layer_name')} "
            f"({resp.get('changes_count', 0)} changes, "
            f"{resp.get('size_bytes', 0)} bytes)")
    if command == "images-list-layers":
        layers = resp.get('layers', [])
        if not layers:
            return "no layers found"
        lines = ["Image layers:"]
        for l in layers:
            lines.append(
                f"  {l['name']}: {l['changes_count']} changes")
        return "\n".join(lines)
    if command == "images-remove-layer":
        return "layer removed"
    if command == "images-diff":
        added = len(resp.get('added', []))
        removed = len(resp.get('removed', []))
        modified = len(resp.get('modified', []))
        return (
            f"diff: +{added} added, -{removed} removed, "
            f"~{modified} modified, "
            f"identical={resp.get('identical', False)}")
    if command == "registry-pull":
        return (
            f"pulled {resp.get('name')}:{resp.get('tag')} "
            f"({resp.get('size_bytes', 0):,} bytes)")
    if command == "registry-push":
        return (
            f"pushed {resp.get('name')}:{resp.get('tag')} "
            f"({resp.get('size_bytes', 0):,} bytes)")
    if command == "registry-catalog":
        images = resp.get('images', [])
        if not images:
            return "registry catalog is empty"
        lines = [f"Registry {resp.get('registry_url')}:"]
        for img in images:
            lines.append(f"  {img}")
        return "\n".join(lines)
    if command == "cluster-register-node":
        return f"node registered: {resp.get('node_id')} at {resp.get('node_url')}"
    if command == "cluster-unregister-node":
        return f"node unregistered: {resp.get('ok')}"
    if command == "cluster-heartbeat":
        return f"heartbeat acknowledged for {resp.get('node_id')}"
    if command == "cluster-nodes":
        nodes = resp.get('nodes', [])
        if not nodes:
            return "no nodes registered"
        lines = ["Cluster nodes:"]
        for n in nodes:
            lines.append(
                f"  {n['node_id']}: {n['status']} "
                f"(url={n['node_url']})")
        return "\n".join(lines)
    if command == "cluster-status":
        return (
            f"Cluster: {resp.get('active_nodes', 0)} active, "
            f"{resp.get('stale_nodes', 0)} stale, "
            f"{resp.get('total_containers', 0)} containers")
    if command == "cluster-schedule":
        chosen = resp.get('chosen_node')
        if chosen is None:
            return "no node available for scheduling"
        return (
            f"scheduled to: {chosen} "
            f"(strategy={resp.get('strategy')})")
    if command == "cluster-containers":
        containers = resp.get('containers', [])
        if not containers:
            return "no containers in cluster"
        lines = ["Cluster containers:"]
        for c in containers:
            lines.append(
                f"  {c['container_id'][:12]}: {c['state']} "
                f"(node={c['node']})")
        return "\n".join(lines)
    if command == "cluster-drain-node":
        to_migrate = resp.get('containers_to_migrate', [])
        return (
            f"draining {resp.get('node_id')}: "
            f"{len(to_migrate)} containers to migrate")
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
    if command == "baseline-record":
        count = resp.get("snapshot_count", 0)
        bl = resp.get("baseline", {})
        return (
            f"Recorded baseline snapshot #{count}\n"
            f"  memory: {_fmt_bytes(bl.get('memory_bytes', 0))}\n"
            f"  rss: {_fmt_bytes(bl.get('total_rss_bytes', 0))}\n"
            f"  cpu_s: {bl.get('total_cpu_s', 0):.3f}\n"
            f"  threads: {bl.get('total_threads', 0)}")
    if command == "baseline-get":
        count = resp.get("snapshot_count", 0)
        if count == 0:
            return "no baseline data (record some snapshots first)"
        mean = resp.get("mean", {})
        sd = resp.get("stddev", {})
        lines = [
            f"Baseline ({count} snapshots):",
            "",
            "Metric\tMean\tStdDev",
        ]
        for m in sorted(mean.keys()):
            if "bytes" in m or "rss" in m:
                lines.append(
                    f"{m}\t{_fmt_bytes(int(mean[m]))}\t"
                    f"{_fmt_bytes(int(sd[m]))}")
            else:
                lines.append(
                    f"{m}\t{mean[m]:.3f}\t{sd[m]:.3f}")
        return "\n".join(lines)
    if command == "baseline-compare":
        deviations = resp.get("deviations", [])
        reason = resp.get("reason")
        if reason:
            return reason
        if not deviations:
            return "All metrics within baseline (no deviations)"
        lines = ["Deviations from baseline:", ""]
        for d in deviations:
            lines.append(
                f"  {d['metric']}: {d['current']} "
                f"(baseline {d['baseline_mean']:.1f} ± "
                f"{d['baseline_stddev']:.1f}, "
                f"z={d['z_score']}, {d['direction']})")
        return "\n".join(lines)
    if command == "baseline-clear":
        cleared = resp.get("cleared", 0)
        return f"Cleared {cleared} baseline snapshot(s)"
    if command == "process-kill":
        if resp.get("ok"):
            return f"Sent {resp.get('signal_name', '?')} to PID {resp.get('pid')}"
        return f"Failed: {resp.get('error', '?')}"
    if command == "process-list":
        procs = resp.get("processes", [])
        if not procs:
            return "no running processes"
        lines = ["PID\tSTATE\tCPU\tRSS\tNAME"]
        for p in procs:
            cpu = p.get("user_time_s", 0) + p.get("system_time_s", 0)
            rss = p.get("rss_kb", 0)
            lines.append(
                f"{p.get('pid', '?')}\t{p.get('state', '?')}\t"
                f"{cpu:.3f}s\t{rss} KiB\t{p.get('name', '?')}")
        return "\n".join(lines)
    if command == "process-signal-all":
        signaled = resp.get("signaled", [])
        failed = resp.get("failed", [])
        parts = [f"Signaled {len(signaled)} process(es)"]
        if failed:
            parts.append(f"Failed: {len(failed)}")
        return ", ".join(parts)
    if command == "snapshot-schedule-set":
        sched = resp.get("schedule", {})
        return (
            f"Schedule: enabled={sched.get('enabled', False)}, "
            f"interval={sched.get('interval_s', 0)}s, "
            f"max={sched.get('max_snapshots', 0)}, "
            f"snapshots taken={sched.get('snapshot_count', 0)}")
    if command == "snapshot-schedule-get":
        sched = resp.get("schedule")
        if not sched:
            return "No snapshot schedule configured"
        return (
            f"enabled={sched.get('enabled', False)}, "
            f"interval={sched.get('interval_s', 0)}s, "
            f"max={sched.get('max_snapshots', 0)}, "
            f"snapshots={sched.get('snapshot_count', 0)}")
    if command == "snapshot-schedule-disable":
        return "Snapshot scheduling disabled"
    if command == "snapshot-schedule-run":
        if resp.get("ok"):
            return (
                f"Snapshot {resp.get('snapshot_id', '?')} taken "
                f"(pruned {resp.get('pruned', 0)}, "
                f"total {resp.get('total_snapshots', 0)})")
        return f"Failed: {resp.get('error', '?')}"
    if command == "snapshot-schedule-list":
        snaps = resp.get("snapshots", [])
        if not snaps:
            return "No scheduled snapshots"
        lines = []
        for s in snaps:
            lines.append(
                f"  {s.get('snapshot_id', '?')} "
                f"(label={s.get('label', '?')}, "
                f"ts={s.get('timestamp', 0):.0f})")
        return f"{len(snaps)} scheduled snapshot(s):\n" + "\n".join(lines)
    if command in ("dependency-health", "dependency-health-reverse"):
        deps = resp.get("dependencies") or resp.get("dependents", [])
        all_ok = resp.get("all_healthy", True)
        if not deps:
            return "No dependencies"
        lines = [f"All healthy: {all_ok}", ""]
        for d in deps:
            mark = "OK" if d.get("healthy") else "FAIL"
            lines.append(
                f"  [{mark}] {d.get('id', '?')} "
                f"state={d.get('state', '?')}, "
                f"health={d.get('health', '?')}")
        return "\n".join(lines)
    if command == "usage-report":
        totals = resp.get("totals", {})
        containers = resp.get("containers", [])
        lines = [
            f"Report: {resp.get('container_count', 0)} containers",
            f"  Total memory: {_fmt_bytes(totals.get('memory_bytes', 0))}",
            f"  Total PIDs: {totals.get('pids', 0)}",
            "",
            "Container\tState\tMemory\tPIDs",
        ]
        for c in containers:
            lines.append(
                f"{c.get('name') or c.get('id', '?')}\t"
                f"{c.get('state', '?')}\t"
                f"{_fmt_bytes(c.get('memory_bytes', 0))}\t"
                f"{c.get('pids_current', 0)}")
        # Top consumers
        top = resp.get("top_consumers", {})
        if top.get("by_memory"):
            lines.append("\nTop memory consumers:")
            for t in top["by_memory"]:
                lines.append(
                    f"  {t.get('name') or t.get('id', '?')}: "
                    f"{_fmt_bytes(t.get('memory_bytes', 0))}")
        return "\n".join(lines)
    if command == "alert-summary":
        total = resp.get("total_alerts", 0)
        by_sev = resp.get("by_severity", {})
        lines = [f"Total unacknowledged alerts: {total}"]
        if by_sev:
            lines.append(f"  By severity: {by_sev}")
        alerts = resp.get("alerts", [])
        if alerts:
            lines.append("")
            for a in alerts[:10]:
                lines.append(
                    f"  [{a.get('severity', '?')}] "
                    f"{a.get('container_id', '?')}: "
                    f"{a.get('message', '?')}")
        return "\n".join(lines)
    if command == "set-cpu-weight":
        if resp.get("ok"):
            return f"CPU weight set to {resp.get('weight', '?')}"
        return f"Failed: {resp.get('error', '?')}"
    if command == "set-io-weight":
        if resp.get("ok"):
            return f"I/O weight set to {resp.get('weight', '?')}"
        return f"Failed: {resp.get('error', '?')}"
    if command == "get-priority":
        lines = [
            f"nice: {resp.get('nice_value', '?')}",
            f"cpu_weight: {resp.get('cpu_weight', '?')}",
            f"io_weight: {resp.get('io_weight', '?')}",
            f"cpu_affinity: {resp.get('cpu_affinity', '?')}",
        ]
        return "\n".join(lines)
    if command == "event-correlate":
        clusters = resp.get("clusters", [])
        total = resp.get("total_events", 0)
        if not clusters:
            return f"No correlated event clusters ({total} events total)"
        lines = [f"{len(clusters)} correlated cluster(s) ({total} events total):", ""]
        for i, cl in enumerate(clusters, 1):
            lines.append(
                f"  Cluster {i}: {cl['event_count']} events, "
                f"{len(cl['container_ids'])} containers")
            lines.append(
                f"    containers: {', '.join(cl['container_ids'])}")
            lines.append(
                f"    kinds: {', '.join(cl['kinds'])}")
        return "\n".join(lines)
    if command == "event-timeline":
        events = resp.get("events", [])
        summary = resp.get("summary", {})
        if not events:
            return "No events in time window"
        lines = [
            f"{summary.get('total', 0)} events in window:",
            f"  by kind: {summary.get('by_kind', {})}", "",
            "Time\tContainer\tKind\tDetail",
        ]
        for e in events[:20]:
            lines.append(
                f"{e.get('time', 0):.0f}\t"
                f"{e.get('container_id', '?')[:8]}\t"
                f"{e.get('kind', '?')}\t"
                f"{e.get('detail', '')[:40]}")
        if len(events) > 20:
            lines.append(f"  ... and {len(events) - 20} more")
        return "\n".join(lines)
    if command == "network-rule-add":
        if resp.get("ok"):
            return (
                f"Rule #{resp.get('rule_index')} added "
                f"({resp.get('rules_count', 0)} total rules)")
        return f"Failed: {resp.get('error', '?')}"
    if command == "network-rule-remove":
        if resp.get("ok"):
            removed = resp.get("removed", {})
            return (
                f"Removed: {removed.get('direction', '?')} "
                f"{removed.get('protocol', '?')} port={removed.get('port', '?')}")
        return f"Failed: {resp.get('error', '?')}"
    if command == "network-rules-list":
        rules = resp.get("rules", [])
        if not rules:
            return "No network rules"
        lines = ["IDX\tDIR\tPROTO\tPORT\tSOURCE\tACTION"]
        for i, r in enumerate(rules):
            lines.append(
                f"{i}\t{r.get('direction', '?')}\t"
                f"{r.get('protocol', '?')}\t"
                f"{r.get('port', '*')}\t"
                f"{r.get('source', '*')}\t"
                f"{r.get('action', '?')}")
        return "\n".join(lines)
    if command == "network-rules-clear":
        return f"Cleared {resp.get('cleared', 0)} rule(s)"
    if command == "compare-containers":
        if "error" in resp:
            return resp["error"]
        comparison = resp.get("comparison", [])
        rankings = resp.get("rankings", {})
        lines = [f"Comparing {resp.get('container_count', 0)} containers:", ""]
        for c in comparison:
            lines.append(
                f"  {c.get('name') or c.get('id', '?')} "
                f"(state={c.get('state', '?')}): "
                f"mem={_fmt_bytes(c.get('memory_bytes', 0))}, "
                f"pids={c.get('pids_current', 0)}")
        if rankings.get("memory_bytes"):
            lines.append("\nMemory ranking:")
            for r in rankings["memory_bytes"]:
                lines.append(
                    f"  #{r['rank']} {r.get('name') or r['id']}: "
                    f"{_fmt_bytes(r['value'])} ({r['percentage']}%)")
        return "\n".join(lines)
    if command == "check-thresholds":
        fired = resp.get("fired", [])
        count = resp.get("count", 0)
        if count == 0:
            return "No threshold alerts fired"
        lines = [f"{count} alert(s) fired:", ""]
        for a in fired:
            lines.append(
                f"  [{a.get('level', '?').upper()}] "
                f"{a.get('container_id', '?')[:8]}: "
                f"{a.get('resource', '?')} = {a.get('detail', '')}")
        return "\n".join(lines)
    if command == "threshold-status":
        containers = resp.get("containers", [])
        if not containers:
            return "No containers with thresholds"
        lines = ["Container\tMem%\tPID%\tStatus"]
        for c in containers:
            lines.append(
                f"{c.get('name') or c.get('container_id', '?')[:8]}\t"
                f"{c.get('memory_pct', 0)}%\t"
                f"{c.get('pid_pct', 0)}%\t"
                f"{c.get('status', '?')}")
        return "\n".join(lines)
    if command == "set-scheduling-priority":
        if resp.get("ok"):
            return f"Priority set to {resp.get('priority', '?')}"
        return f"Failed: {resp.get('error', '?')}"
    if command == "scheduling-queue":
        queue = resp.get("queue", [])
        if not queue:
            return "No containers in queue"
        lines = ["Container\tState\tPriority\tMemory\tPIDs"]
        for q in queue:
            lines.append(
                f"{q.get('name') or q.get('id', '?')}\t"
                f"{q.get('state', '?')}\t"
                f"{q.get('priority', '?')}\t"
                f"{_fmt_bytes(q.get('memory_bytes', 0))}\t"
                f"{q.get('pids_current', 0)}")
        return "\n".join(lines)
    if command == "ready-containers":
        ready = resp.get("ready", [])
        if not ready:
            return "No containers ready to start"
        lines = ["Container\tPriority"]
        for r in ready:
            lines.append(
                f"{r.get('name') or r.get('id', '?')}\t"
                f"{r.get('priority', '?')}")
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
    if command == "audit-record":
        if resp.get("ok"):
            return (f"audit entry recorded: {resp.get('action')} "
                    f"by {resp.get('actor')} at "
                    f"{resp.get('timestamp', '?')}")
        return f"Failed: {resp.get('error', '?')}"
    if command == "audit-log":
        entries = resp.get("entries", [])
        if not entries:
            return "No audit entries"
        lines = ["Timestamp\tAction\tActor\tResource\tDetail"]
        for e in entries:
            ts = e.get("timestamp", 0)
            lines.append(
                f"{ts:.0f}\t"
                f"{e.get('action', '?')}\t"
                f"{e.get('actor', '?')}\t"
                f"{e.get('resource', '-')}\t"
                f"{e.get('detail', '')}")
        return "\n".join(lines)
    if command == "audit-summary":
        lines = [
            f"Total entries: {resp.get('total_entries', 0)}",
            f"By action: {resp.get('by_action', {})}",
            f"By actor: {resp.get('by_actor', {})}",
        ]
        recent = resp.get("recent", [])
        if recent:
            lines.append(f"Recent ({len(recent)}):")
            for e in recent[:5]:
                lines.append(f"  {e.get('action')} by {e.get('actor')}")
        return "\n".join(lines)
    if command == "cost-allocate":
        if not resp.get("total_cost") and resp.get("total_cost") != 0:
            return f"Failed: {resp.get('error', '?')}"
        lines = [
            f"Container: {resp.get('container_id', '?')}",
            f"Memory cost: ${resp.get('memory_cost', 0):.6f}",
            f"CPU cost:    ${resp.get('cpu_cost', 0):.6f}",
            f"PID cost:    ${resp.get('pid_cost', 0):.6f}",
            f"Total/hour:  ${resp.get('total_cost', 0):.6f}",
            f"Projected/day:   ${resp.get('projected_daily', 0):.4f}",
            f"Projected/month: ${resp.get('projected_monthly', 0):.4f}",
        ]
        usage = resp.get("usage", {})
        if usage:
            lines.append(
                f"Usage: {usage.get('memory_gb', 0):.4f} GB, "
                f"{usage.get('cpu_hours', 0):.6f} CPU-hours, "
                f"{usage.get('pids', 0)} PIDs")
        return "\n".join(lines)
    if command == "cost-allocate-all":
        total = resp.get("total_cost", 0)
        count = resp.get("container_count", 0)
        lines = [f"Total cost: ${total:.6f}/hour ({count} containers)"]
        by_owner = resp.get("by_owner", {})
        if by_owner:
            lines.append("By owner:")
            for owner, cost in sorted(by_owner.items(),
                                       key=lambda x: -x[1]):
                lines.append(f"  {owner}: ${cost:.6f}")
        return "\n".join(lines)
    if command == "budget-set":
        budget = resp.get("budget", {})
        return (f"Budget set for {resp.get('container_id', '?')}: "
                f"{budget}")
    if command == "budget-get":
        budget = resp.get("budget", {})
        if not budget:
            return f"No budget set for {resp.get('container_id', '?')}"
        lines = [f"Budget for {resp.get('container_id', '?')}:"]
        for k, v in budget.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)
    if command in ("budget-check", "budget-check-all"):
        if 'results' in resp:
            results = resp.get('results', [])
            if not results:
                return "No budgets to check"
            lines = []
            for r in results:
                status = r.get('status', '?')
                name = r.get('name') or r.get('container_id', '?')
                marker = {'ok': '\u2713', 'warning': '\u26a0',
                          'exceeded': '\u2717'}.get(status, '?')
                lines.append(f"{marker} {name}: {status}")
                for v in r.get('violations', []):
                    lines.append(
                        f"  EXCEEDED {v['resource']}: "
                        f"{v['used']}/{v['budget']} {v['unit']} "
                        f"({v['pct']}%)")
                for w in r.get('warnings', []):
                    lines.append(
                        f"  WARNING {w['resource']}: "
                        f"{w['used']}/{w['budget']} {w['unit']} "
                        f"({w['pct']}%)")
            return "\n".join(lines)
        status = resp.get('status', '?')
        if status == 'no_budget':
            return f"No budget set for {resp.get('container_id', '?')}"
        lines = [f"{resp.get('container_id', '?')}: {status}"]
        for v in resp.get('violations', []):
            lines.append(
                f"  EXCEEDED {v['resource']}: "
                f"{v['used']}/{v['budget']} {v['unit']} "
                f"({v['pct']}%)")
        for w in resp.get('warnings', []):
            lines.append(
                f"  WARNING {w['resource']}: "
                f"{w['used']}/{w['budget']} {w['unit']} "
                f"({w['pct']}%)")
        return "\n".join(lines)
    if command == "budget-clear":
        if resp.get('cleared'):
            return f"Budget cleared for {resp.get('container_id', '?')}"
        return f"No budget was set for {resp.get('container_id', '?')}"
    if command == "remediation-configure":
        policy = resp.get('policy', {})
        enabled = 'enabled' if policy.get('enabled') else 'disabled'
        return (
            f"Remediation {enabled} for {resp.get('container_id', '?')}:\n"
            f"  budget_exceeded: {policy.get('on_budget_exceeded', '?')}\n"
            f"  threshold_exceeded: {policy.get('on_threshold_exceeded', '?')}\n"
            f"  oom_risk: {policy.get('on_oom_risk', '?')}\n"
            f"  max_restarts: {policy.get('max_restarts', 3)}\n"
            f"  cooldown: {policy.get('cooldown_seconds', 300)}s")
    if command == "remediation-execute":
        action = resp.get('action_taken', '?')
        result = resp.get('result', '')
        cooldown = resp.get('cooldown_active', False)
        lines = [f"Action: {action}", f"Result: {result}"]
        if cooldown:
            lines.append("Note: cooldown is active")
        history = resp.get('history', [])
        if history:
            lines.append(f"Recent history ({len(history)}):")
            for h in history[-3:]:
                lines.append(
                    f"  {h.get('action', '?')} for "
                    f"{h.get('trigger', '?')}: {h.get('result', '')}")
        return "\n".join(lines)
    if command == "remediation-status":
        enabled = 'enabled' if resp.get('enabled') else 'disabled'
        lines = [
            f"Remediation {enabled} for {resp.get('container_id', '?')}",
            f"  Restarts: {resp.get('restart_count', 0)}",
        ]
        policy = resp.get('policy', {})
        if policy:
            lines.append(f"  Budget: {policy.get('on_budget_exceeded', '?')}")
            lines.append(f"  Threshold: {policy.get('on_threshold_exceeded', '?')}")
            lines.append(f"  OOM: {policy.get('on_oom_risk', '?')}")
        total = resp.get('history_total', 0)
        if total:
            lines.append(f"  History: {total} entries")
        return "\n".join(lines)
    if command == "remediation-history":
        entries = resp.get('entries', [])
        if not entries:
            return "No remediation history"
        lines = [f"{len(entries)} remediation events:"]
        for e in entries:
            lines.append(
                f"  {e.get('action', '?')} | "
                f"trigger: {e.get('trigger', '?')} | "
                f"{e.get('result', '')}")
        return "\n".join(lines)
    if command == "tenant-config-set":
        cfg = resp.get('config', {})
        return (
            f"Tenant {resp.get('owner', '?')} configured:\n"
            f"  priority: {cfg.get('priority', 0)}\n"
            f"  weight: {cfg.get('weight', 1.0)}\n"
            f"  burstable: {cfg.get('burstable_pct', 20)}%\n"
            f"  enforce: {cfg.get('enforce', True)}\n"
            f"  eviction: {cfg.get('eviction_policy', 'alert')}")
    if command == "tenant-config-get":
        cfg = resp.get('config', {})
        if not cfg:
            return f"No config for tenant {resp.get('owner', '?')}"
        return (
            f"Tenant {resp.get('owner', '?')}: "
            f"priority={cfg.get('priority', 0)}, "
            f"weight={cfg.get('weight', 1.0)}, "
            f"enforce={cfg.get('enforce', True)}")
    if command == "tenant-config-list":
        tenants = resp.get('tenants', {})
        if not tenants:
            return "No tenant configs"
        lines = [f"{resp.get('count', 0)} tenants:"]
        for owner, cfg in tenants.items():
            lines.append(
                f"  {owner}: priority={cfg.get('priority', 0)}, "
                f"weight={cfg.get('weight', 1.0)}, "
                f"enforce={cfg.get('enforce', True)}")
        return "\n".join(lines)
    if command == "fair-share":
        tenants = resp.get('tenants', {})
        if not tenants:
            return "No tenants with quotas"
        lines = [
            f"Fair-share ({resp.get('resource', '?')}), "
            f"total: {resp.get('total_quota', 0)}"]
        for owner, info in tenants.items():
            status = info.get('status', '?')
            marker = {'ok': '\u2713', 'over_share': '\u26a0',
                      'over_burst': '\u2717'}.get(status, '?')
            lines.append(
                f"  {marker} {owner}: "
                f"usage={info.get('usage', 0)}, "
                f"share={info.get('fair_share', 0)}, "
                f"{info.get('pct_of_share', 0)}%")
        return "\n".join(lines)
    if command == "tenant-enforce":
        actions = resp.get('actions', [])
        if not actions:
            return "All tenants within quota"
        lines = [f"{len(actions)} enforcement actions needed:"]
        for a in actions:
            lines.append(
                f"  {a.get('owner', '?')}: {a.get('resource', '?')} "
                f"{a.get('usage', 0)}/{a.get('limit', 0)} "
                f"(over by {a.get('overage', 0)}), "
                f"action: {a.get('recommended_action', '?')}")
        return "\n".join(lines)
    if command == "tenant-usage-summary":
        tenants = resp.get('tenants', [])
        if not tenants:
            return "No tenants configured"
        lines = ["Tenant\tPriority\tMem%\tPID%\tContainers\tStatus"]
        for t in tenants:
            lines.append(
                f"{t.get('owner', '?')}\t"
                f"{t.get('priority', 0)}\t"
                f"{t.get('memory_pct', 0)}%\t"
                f"{t.get('pid_pct', 0)}%\t"
                f"{t.get('containers', 0)}\t"
                f"{t.get('status', '?')}")
        return "\n".join(lines)
    if command == "event-log-export":
        lines = [
            f"Export time: {resp.get('export_time', 0):.0f}",
            f"Containers: {resp.get('total_events', 0)} total events "
            f"from {len(resp.get('containers', []))} containers",
        ]
        for c in resp.get('containers', []):
            cid = c.get('container_id', '?')[:8]
            lifecycle = len(c.get('lifecycle_events', []))
            audit = len(c.get('audit_log', []))
            oom = len(c.get('oom_events', []))
            sla = len(c.get('sla_violations', []))
            rem = len(c.get('remediation_history', []))
            lines.append(
                f"  {cid}: lifecycle={lifecycle}, audit={audit}, "
                f"oom={oom}, sla={sla}, remediation={rem}")
        return "\n".join(lines)
    if command == "event-log-import":
        imported = resp.get('imported_containers', 0)
        total = resp.get('total_imported', 0)
        errors = resp.get('errors', [])
        lines = [
            f"Imported {total} events into {imported} containers",
        ]
        if errors:
            lines.append(f"Errors ({len(errors)}):")
            for e in errors[:5]:
                lines.append(f"  {e}")
        return "\n".join(lines)
    if command == "health-score":
        score = resp.get('score', 0)
        grade = resp.get('grade', '?')
        name = resp.get('name') or resp.get('container_id', '?')
        lines = [
            f"{name}: {grade} ({score}/100)",
        ]
        for dim, info in resp.get('breakdown', {}).items():
            lines.append(
                f"  {dim}: {info.get('score', 0)}/{info.get('max', '?')}")
        recs = resp.get('recommendations', [])
        if recs:
            lines.append("Recommendations:")
            for r in recs:
                lines.append(f"  - {r}")
        return "\n".join(lines)
    if command == "health-score-all":
        avg = resp.get('fleet_average', 0)
        count = resp.get('container_count', 0)
        unhealthy = resp.get('unhealthy_count', 0)
        lines = [
            f"Fleet health: {avg}/100 avg, "
            f"{count} containers, {unhealthy} unhealthy",
        ]
        for c in resp.get('containers', []):
            score = c.get('score', 0)
            grade = c.get('grade', '?')
            name = c.get('name') or c.get('container_id', '?')[:8]
            marker = {'A': '\u2713', 'B': '\u2713', 'C': '~',
                      'D': '\u26a0', 'F': '\u2717'}.get(grade, '?')
            lines.append(
                f"  {marker} {name}: {grade} ({score})")
        return "\n".join(lines)
    if command == "event-log-compress":
        orig = resp.get('original_events', 0)
        comp = resp.get('compressed_events', 0)
        ratio = resp.get('compression_ratio', 0)
        return (
            f"Compressed {orig} -> {comp} events "
            f"({ratio:.1%} reduction) across "
            f"{len(resp.get('containers', []))} containers")
    if command == "archive-schedule-set":
        sched = resp.get('schedule', {})
        enabled = 'enabled' if sched.get('enabled') else 'disabled'
        return (
            f"Archive schedule {enabled}:\n"
            f"  interval: {sched.get('interval_s', 0):.0f}s\n"
            f"  keep_recent: {sched.get('keep_recent', 0)}\n"
            f"  auto_compress: {sched.get('auto_compress', True)}\n"
            f"  max_archives: {sched.get('max_archives', 0)}")
    if command == "archive-schedule-get":
        sched = resp.get('schedule', {})
        if not sched:
            return "No archive schedule configured"
        enabled = 'enabled' if sched.get('enabled') else 'disabled'
        last = sched.get('last_archive_time', 0)
        count = sched.get('archive_count', 0)
        return (
            f"Archive schedule {enabled}: "
            f"{count} archives, "
            f"last at {last:.0f}")
    if command == "archive-schedule-disable":
        return "Archive schedule disabled"
    if command == "archive-run-now":
        orig = resp.get('original_events', 0)
        comp = resp.get('compressed_events', 0)
        ratio = resp.get('compression_ratio', 0)
        count = resp.get('archive_count', 0)
        return (
            f"Archive #{count}: {orig} -> {comp} events "
            f"({ratio:.1%} reduction)")
    if command == "archive-list":
        archives = resp.get('archives', [])
        if not archives:
            return "No archives stored"
        lines = [f"{len(archives)} archives:"]
        for i, a in enumerate(archives):
            lines.append(
                f"  #{i}: {a.get('original_events', 0)} -> "
                f"{a.get('compressed_events', 0)} events "
                f"({a.get('compression_ratio', 0):.1%})")
        return "\n".join(lines)
    if command == "archive-get":
        orig = resp.get('original_events', 0)
        comp = resp.get('compressed_events', 0)
        return f"Archive: {orig} -> {comp} events"
    if command == "sla-breach-process":
        lines = [
            f"SLA breach processed for {resp.get('container_id', '?')}:",
            f"  type: {resp.get('breach_type', '?')}",
        ]
        esc = resp.get('escalation', {})
        if esc.get('escalated'):
            lines.append(
                f"  escalated: level {esc.get('level', '?')}, "
                f"actions: {esc.get('actions', [])}")
        rem = resp.get('remediation')
        if rem:
            lines.append(
                f"  remediation: {rem.get('action_taken', '?')} - "
                f"{rem.get('result', '')}")
        return "\n".join(lines)
    if command == "sla-breach-process-all":
        lines = [
            f"SLA breach processed across "
            f"{resp.get('containers_processed', 0)} containers:",
            f"  escalated: {resp.get('escalated_count', 0)}",
            f"  remediated: {resp.get('remediated_count', 0)}",
        ]
        return "\n".join(lines)
    if command == "smart-remediate":
        severity = resp.get('severity_score', 0)
        level = resp.get('severity_level', '?')
        name = resp.get('name') or resp.get('container_id', '?')
        lines = [
            f"{name}: {level} (severity={severity}/100)",
            f"  health: {resp.get('health_score', 0)}/100",
            f"  anomalies: {resp.get('anomaly_count', 0)}"
            f" (penalty: {resp.get('anomaly_penalty', 0)})",
            f"  budget: {resp.get('budget_status', 'none')}"
            f" (penalty: {resp.get('budget_penalty', 0)})",
            f"  OOM penalty: {resp.get('oom_penalty', 0)}",
        ]
        action = resp.get('action_taken', 'none')
        if action != 'none':
            lines.append(f"  action: {action}")
        recs = resp.get('recommendations', [])
        if recs:
            lines.append("  recommendations:")
            for r in recs:
                lines.append(f"    - {r}")
        return "\n".join(lines)
    if command == "smart-remediate-all":
        avg = resp.get('average_severity', 0)
        count = resp.get('container_count', 0)
        critical = resp.get('critical_count', 0)
        high = resp.get('high_count', 0)
        remediated = resp.get('remediated_count', 0)
        lines = [
            f"Fleet severity: {avg}/100 avg, "
            f"{count} containers, "
            f"{critical} critical, {high} high, "
            f"{remediated} auto-remediated",
        ]
        for c in resp.get('containers', []):
            severity = c.get('severity_score', 0)
            level = c.get('severity_level', '?')
            name = c.get('name') or c.get('container_id', '?')[:8]
            marker = {'healthy': '\u2713', 'low': '~', 'moderate': '\u26a0',
                      'high': '\u26a0\u26a0', 'critical': '\u2717'}.get(
                level, '?')
            lines.append(
                f"  {marker} {name}: {level} ({severity})")
        return "\n".join(lines)
    if command == "usage-patterns":
        patterns = resp.get('patterns', {})
        corr = resp.get('memory_cpu_correlation', 0)
        lines = ["Resource usage patterns:"]
        for resource, info in patterns.items():
            pattern = info.get('pattern', '?')
            conf = info.get('confidence', 0)
            lines.append(
                f"  {resource}: {pattern} "
                f"(confidence={conf:.1f}%, "
                f"mean={info.get('mean', 0)}, "
                f"cv={info.get('cv', 0):.3f})")
        lines.append(f"  memory-cpu correlation: {corr:.4f}")
        return "\n".join(lines)
    if command == "optimization-actions":
        actions = resp.get('actions', [])
        if not actions:
            return "No optimization actions recommended"
        lines = [f"{len(actions)} optimization actions:"]
        for a in actions:
            priority = a.get('priority', '?')
            marker = {'high': '\u26a0', 'medium': '~',
                      'low': '\u2713'}.get(priority, '?')
            lines.append(
                f"  {marker} [{priority}] {a.get('action', '?')}: "
                f"{a.get('reason', '')}")
            if a.get('suggested') is not None:
                lines.append(
                    f"    {a.get('current')} -> {a.get('suggested')} "
                    f"({a.get('resource', '')}, "
                    f"savings: {a.get('estimated_savings_pct', 0)}%)")
        return "\n".join(lines)
    if command == "rightsize":
        changes = resp.get('changes', [])
        if not changes:
            reason = resp.get('reason', 'no_changes')
            if reason == 'insufficient_data':
                return "Insufficient data for right-sizing (need 5+ samples)"
            return "No right-sizing changes recommended"
        dry_run = resp.get('dry_run', False)
        lines = ["Right-sizing " + ("(dry run) " if dry_run else "") + "changes:"]
        for c in changes:
            lines.append(
                f"  {c.get('resource', '?')}: "
                f"{c.get('current', '?')} -> {c.get('suggested', '?')} "
                f"(savings: {c.get('savings_pct', 0)}%)")
        return "\n".join(lines)
    if command == "rightsize-all":
        total = resp.get('total_changes', 0)
        applied = resp.get('containers_applied', 0)
        dry_run = resp.get('dry_run', False)
        lines = [
            f"Right-sizing " + ("(dry run) " if dry_run else "") +
            f"across {resp.get('container_count', 0)} containers: "
            f"{total} changes, {applied} applied",
        ]
        for c in resp.get('containers', []):
            changes = c.get('changes', [])
            if changes:
                name = c.get('container_id', '?')[:8]
                lines.append(
                    f"  {name}: {len(changes)} changes")
        return "\n".join(lines)
    if command == "sla-compliance-set":
        rules = resp.get('rules', {})
        enabled = 'enabled' if rules.get('enabled') else 'disabled'
        return (
            f"SLA compliance {enabled} for {resp.get('container_id', '?')}:\n"
            f"  memory: <{rules.get('max_memory_pct', 90)}%\n"
            f"  pids: <{rules.get('max_pid_pct', 80)}%\n"
            f"  action: {rules.get('auto_action', 'alert')}")
    if command == "sla-compliance-get":
        rules = resp.get('rules', {})
        if not rules:
            return f"No SLA compliance rules for {resp.get('container_id', '?')}"
        return (
            f"SLA compliance rules for {resp.get('container_id', '?')}: "
            f"{rules.get('auto_action', 'alert')}, "
            f"{len(rules.get('violations', []))} violations")
    if command == "sla-compliance-check":
        compliant = resp.get('compliant', True)
        count = resp.get('violation_count', 0)
        action = resp.get('action_taken', 'none')
        if compliant:
            return f"{resp.get('container_id', '?')}: compliant"
        lines = [
            f"{resp.get('container_id', '?')}: {count} violations",
        ]
        for v in resp.get('violations', []):
            lines.append(
                f"  {v.get('rule', '?')}: {v.get('current', '?')} "
                f"> {v.get('threshold', '?')} ({v.get('resource', '?')})")
        if action != 'none':
            lines.append(f"  action: {action}")
        return "\n".join(lines)
    if command == "sla-compliance-check-all":
        nc = resp.get('non_compliant_count', 0)
        total = resp.get('total_violations', 0)
        count = resp.get('container_count', 0)
        return (
            f"SLA compliance: {count} containers, "
            f"{nc} non-compliant, {total} violations")
    if command == "viz-data":
        name = resp.get('name') or resp.get('container_id', '?')
        sparklines = resp.get('sparklines', {})
        trends = resp.get('trends', {})
        meta = resp.get('metadata', {})
        lines = [
            f"Visualization for {name}:",
            f"  samples: {meta.get('sample_count', 0)} "
            f"-> {meta.get('output_points', 0)} points",
        ]
        for resource, spark in sparklines.items():
            trend = trends.get(resource, {})
            direction = trend.get('direction', '?')
            avg = trend.get('avg', 0)
            lines.append(
                f"  {resource}: {spark} "
                f"({direction}, avg={avg})")
        return "\n".join(lines)
    if command == "viz-fleet":
        count = resp.get('container_count', 0)
        mem = resp.get('fleet_memory_mb', 0)
        pids = resp.get('fleet_pids', 0)
        lines = [
            f"Fleet visualization ({count} containers):",
            f"  total memory: {_fmt_bytes(int(mem * 1024 * 1024))}",
            f"  total PIDs: {pids}",
        ]
        for c in resp.get('containers', []):
            name = c.get('name') or c.get('container_id', '?')[:8]
            spark = c.get('sparklines', {}).get('memory', '')
            lines.append(f"  {name}: {spark}")
        return "\n".join(lines)
    if command == "anomaly-remediate":
        severity = resp.get('severity_score', 0)
        level = resp.get('severity_level', '?')
        action = resp.get('action_taken', 'none')
        count = resp.get('anomaly_count', 0)
        lines = [
            f"{resp.get('container_id', '?')[:8]}: "
            f"{level} (severity={severity}, anomalies={count})",
        ]
        if action != 'none':
            lines.append(f"  action: {action} - {resp.get('action_detail', '')}")
        return "\n".join(lines)
    if command == "anomaly-remediate-all":
        avg = resp.get('average_severity', 0)
        count = resp.get('container_count', 0)
        critical = resp.get('critical_count', 0)
        high = resp.get('high_count', 0)
        remediated = resp.get('remediated_count', 0)
        lines = [
            f"Anomaly remediation across {count} containers: "
            f"avg severity={avg}, "
            f"{critical} critical, {high} high, "
            f"{remediated} remediated",
        ]
        return "\n".join(lines)
    if command == "monitor-configure":
        cfg = resp.get('config', {})
        return (
            f"Monitoring configured for {resp.get('container_id', '?')}:\n"
            f"  memory: <{cfg.get('memory_high_pct', 90)}% / >{cfg.get('memory_low_pct', 10)}%\n"
            f"  pid: <{cfg.get('pid_high_pct', 80)}%\n"
            f"  trend_window: {cfg.get('trend_window', 10)}\n"
            f"  enabled: {cfg.get('enabled', True)}")
    if command == "monitor-get":
        cfg = resp.get('config', {})
        if not cfg:
            return f"No monitoring config for {resp.get('container_id', '?')}"
        return (
            f"Monitoring for {resp.get('container_id', '?')}: "
            f"{len(cfg.get('alerts', []))} alerts, "
            f"last check: {cfg.get('last_check', 0):.0f}")
    if command == "monitor-check":
        status = resp.get('status', 'ok')
        count = resp.get('alert_count', 0)
        if status == 'disabled':
            return f"{resp.get('container_id', '?')}: monitoring disabled"
        lines = [f"{resp.get('container_id', '?')}: {status} ({count} alerts)"]
        for a in resp.get('alerts', []):
            marker = {'warning': '\u26a0', 'info': 'i'}.get(
                a.get('severity', ''), '?')
            lines.append(
                f"  {marker} {a.get('type', '?')}: "
                f"{a.get('resource', '?')}="
                f"{a.get('current', '?')} (threshold={a.get('threshold', '?')})")
        return "\n".join(lines)
    if command == "monitor-check-all":
        count = resp.get('container_count', 0)
        alerting = resp.get('alerting_count', 0)
        total = resp.get('total_alerts', 0)
        return (
            f"Monitoring: {count} containers, "
            f"{alerting} alerting, {total} total alerts")
    if command == "sla-auto-escalation-configure":
        cfg = resp.get('config', {})
        return (
            f"SLA auto-escalation configured for {resp.get('container_id', '?')}:\n"
            f"  enabled: {cfg.get('enabled')}\n"
            f"  breach threshold: {cfg.get('breach_threshold')}\n"
            f"  escalation window: {cfg.get('escalation_window_s')}s\n"
            f"  max level: {cfg.get('max_level')}\n"
            f"  cooldown: {cfg.get('cooldown_s')}s")
    if command == "sla-breach-record":
        return (
            f"SLA breach recorded for {resp.get('container_id', '?')}:\n"
            f"  breach count: {resp.get('breach_count', 0)}\n"
            f"  current level: {resp.get('current_level', 0)}\n"
            f"  escalation triggered: {resp.get('escalation_triggered', False)}")
    if command == "sla-auto-escalation-status":
        return (
            f"SLA auto-escalation status for {resp.get('container_id', '?')}:\n"
            f"  enabled: {resp.get('enabled')}\n"
            f"  breach count: {resp.get('breach_count', 0)}\n"
            f"  current level: {resp.get('current_level', 0)}\n"
            f"  escalation count: {resp.get('escalation_count', 0)}")
    if command == "sla-auto-escalation-reset":
        return f"SLA auto-escalation reset for {resp.get('container_id', '?')}: done"
    if command == "cost-optimize":
        lines = [
            f"Cost optimization for {resp.get('container_id', '?')}:",
            f"  current hourly cost: ${resp.get('current_cost', 0):.4f}",
            f"  potential hourly savings: ${resp.get('potential_hourly_savings', 0):.4f}",
            f"  potential daily savings: ${resp.get('potential_daily_savings', 0):.4f}",
            f"  optimization score: {resp.get('optimization_score', 0)}/100",
            f"  recommendations: {len(resp.get('recommendations', []))}",
        ]
        for rec in resp.get('recommendations', []):
            lines.append(f"    - [{rec.get('severity', '?')}] {rec.get('type', '?')}")
        return "\n".join(lines)
    if command == "cost-optimize-all":
        lines = [
            f"Fleet cost optimization:",
            f"  containers: {resp.get('container_count', 0)}",
            f"  total hourly cost: ${resp.get('total_hourly_cost', 0):.4f}",
            f"  total hourly savings: ${resp.get('total_hourly_savings', 0):.4f}",
            f"  total daily savings: ${resp.get('total_daily_savings', 0):.4f}",
            f"  fleet optimization score: {resp.get('fleet_optimization_score', 0)}/100",
            f"  total recommendations: {resp.get('total_recommendations', 0)}",
        ]
        return "\n".join(lines)
    if command == "anomaly-predict":
        lines = [
            f"Anomaly prediction for {resp.get('container_id', '?')}:",
            f"  risk score: {resp.get('risk_score', 0)}/100",
            f"  time to next anomaly: {resp.get('time_to_next_anomaly')}",
            f"  predictions: {len(resp.get('predictions', []))}",
        ]
        for p in resp.get('predictions', []):
            lines.append(
                f"    {p['resource']}: {p['current_usage_pct']}% "
                f"→ {p['predicted_usage_pct']}% "
                f"[{p['risk_level']}] (confidence: {p['confidence']})")
        return "\n".join(lines)
    if command == "anomaly-predict-all":
        lines = [
            f"Fleet anomaly prediction:",
            f"  containers: {resp.get('container_count', 0)}",
            f"  high risk: {resp.get('high_risk_count', 0)}",
            f"  fleet risk score: {resp.get('fleet_risk_score', 0)}/100",
        ]
        for c in resp.get('containers', []):
            if c.get('risk_score', 0) > 0:
                lines.append(
                    f"    {c['container_id']}: risk={c['risk_score']}" )
        return "\n".join(lines)
    if command == "predictive-scale-configure":
        cfg = resp.get('config', {})
        return (
            f"Predictive scaling configured for "
            f"{resp.get('container_id', '?')}:\n"
            f"  enabled: {cfg.get('enabled')}\n"
            f"  lead time: {cfg.get('lead_time_s')}s\n"
            f"  memory buffer: {cfg.get('memory_buffer_pct')}%\n"
            f"  dry run: {cfg.get('dry_run')}")
    if command == "predictive-scale-evaluate":
        lines = [
            f"Predictive scaling for "
            f"{resp.get('container_id', '?')}:",
            f"  action: {resp.get('action', 'none')}",
            f"  reason: {resp.get('reason', '')}",
            f"  risk score: {resp.get('risk_score', 0)}",
        ]
        changes = resp.get('applied_changes', {})
        for res, chg in changes.items():
            lines.append(
                f"  {res}: {chg['old']} -> {chg['new']}")
        return "\n".join(lines)
    if command == "predictive-scale-evaluate-all":
        return (
            f"Fleet predictive scaling:\n"
            f"  containers: {resp.get('container_count', 0)}\n"
            f"  actions: {resp.get('actions_taken', 0)}\n"
            f"  scale ups: {resp.get('scale_ups', 0)}\n"
            f"  scale downs: {resp.get('scale_downs', 0)}")
    if command == "predictive-scale-status":
        return (
            f"Predictive scaling status for "
            f"{resp.get('container_id', '?')}:\n"
            f"  enabled: {resp.get('enabled')}\n"
            f"  lead time: {resp.get('lead_time_s')}s\n"
            f"  scaling count: {resp.get('scaling_count', 0)}\n"
            f"  dry run: {resp.get('dry_run')}")
    if command == "anomaly-correlate":
        lines = [
            f"Anomaly correlation:",
            f"  total anomalies: {resp.get('total_anomalies', 0)}",
            f"  correlated containers: {resp.get('correlated_containers', 0)}",
            f"  clusters: {len(resp.get('clusters', []))}",
            f"  systemic risk: {resp.get('systemic_risk', 0)}/100",
        ]
        for cl in resp.get('clusters', []):
            lines.append(
                f"    cluster: {cl.get('anomaly_count', 0)} anomalies "
                f"across {len(cl.get('container_ids', []))} containers")
        return "\n".join(lines)
    if command == "anomaly-correlation-report":
        lines = [
            f"Anomaly correlation report:",
            f"  total anomalies: {resp.get('total_anomalies', 0)}",
            f"  systemic risk: {resp.get('systemic_risk', 0)}/100",
            f"  recommendation: {resp.get('recommendation', 'ok')}",
        ]
        for c in resp.get('most_affected_containers', []):
            lines.append(
                f"    {c['container_id']}: "
                f"{c['cluster_count']} clusters")
        return "\n".join(lines)
    if command == "resource-heatmap":
        lines = [
            f"Resource heat map (fleet pressure: {resp.get('fleet_pressure_score', 0):.2f}):",
            f"  total containers: {resp.get('total_containers', 0)}",
        ]
        zones = resp.get('pressure_zones', {})
        for zone, count in zones.items():
            if count > 0:
                lines.append(f"  {zone}: {count}")
        candidates = resp.get('consolidation_candidates', [])
        if candidates:
            lines.append(f"  consolidation candidates: {len(candidates)}")
            for cand in candidates[:3]:
                names = ', '.join(c['name'] for c in cand['containers'])
                lines.append(f"    - {names}: {cand['reason']}")
        return "\n".join(lines)
    if command == "container-pressure-detail":
        lines = [
            f"Pressure detail for {resp.get('container_name', '?')} ({resp.get('container_id', '?')[:12]}):",
        ]
        ratios = resp.get('ratios', {})
        trends = resp.get('trends', {})
        for resource, ratio in ratios.items():
            trend = trends.get(resource, 'stable')
            lines.append(f"  {resource}: {ratio:.2%} ({trend})")
        warnings = resp.get('warnings', [])
        if warnings:
            lines.append("  warnings:")
            for w in warnings:
                lines.append(f"    - {w}")
        else:
            lines.append("  warnings: none")
        lines.append(f"  history points: {resp.get('history_points', 0)}")
        return "\n".join(lines)
    if command == "record-pressure-snapshot":
        return (
            f"Recorded pressure snapshot for "
            f"{resp.get('recorded', 0)} containers at "
            f"{resp.get('timestamp', 0):.0f}")
    if command == "classify-tier":
        lines = [
            f"Container: {resp.get('container_id', '?')[:12]}",
            f"  tier: {resp.get('tier', '?')}",
        ]
        for r in resp.get('reasons', []):
            lines.append(f"  - {r}")
        return "\n".join(lines)
    if command == "fleet-tier-summary":
        t = resp.get('tiers', {})
        lines = [
            f"Fleet tier summary ({resp.get('total', 0)} containers):",
            f"  guaranteed: {t.get('guaranteed', 0)}",
            f"  burstable: {t.get('burstable', 0)}",
            f"  besteffort: {t.get('besteffort', 0)}",
        ]
        return "\n".join(lines)
    if command == "suggest-tier-upgrade":
        lines = [
            f"Tier upgrade for {resp.get('container_id', '?')[:12]}:",
            f"  current: {resp.get('current_tier', '?')}",
            f"  target: {resp.get('target_tier', '?')}",
        ]
        for s in resp.get('suggestions', []):
            lines.append(f"  - {s}")
        return "\n".join(lines)
    if command == "log-stream":
        lines = []
        for entry in resp.get('lines', []):
            lines.append(f"[{entry.get('stream', '?')}] {entry.get('line', '')}")
        summary = f"\nTotal lines: {resp.get('total_lines', 0)}"
        if resp.get('timed_out'):
            summary += " (timed out)"
        if resp.get('container_stopped'):
            summary += " (container stopped)"
        return "\n".join(lines) + summary
    if command == "log-filter":
        matches = resp.get('matches', [])
        lines = []
        for m in matches:
            lines.append(f"[{m.get('stream', '?')}] {m.get('line', '')}")
        lines.append(f"\n{resp.get('match_count', 0)} matches / {resp.get('total_scanned', 0)} scanned")
        return "\n".join(lines)
    if command == "log-export":
        return f"Exported {resp.get('written', 0)} log entries to {resp.get('path', '?')} ({resp.get('format', '?')})"
    if command == "image-dedup":
        lines = [
            f"Scanned {resp.get('images_scanned', 0)} images",
            f"Duplicates: {resp.get('duplicate_count', 0)}",
            f"Bytes saved: {resp.get('bytes_saved', 0):,}",
        ]
        for d in resp.get('duplicates', []):
            lines.append(f"  hash={d.get('hash', '?')} count={d.get('count', 0)} size={d.get('size_bytes', 0):,}")
        return "\n".join(lines)
    if command == "image-gc":
        mode = "DRY RUN" if resp.get('dry_run') else "EXECUTED"
        lines = [
            f"GC {mode}: {resp.get('deleted', 0)} images, {resp.get('bytes_reclaimed', 0):,} bytes reclaimed",
            f"Skipped: {resp.get('skipped_count', 0)}",
        ]
        return "\n".join(lines)
    if command == "image-layer-stats":
        lines = [
            f"Images: {resp.get('image_count', 0)}",
            f"Total size: {resp.get('total_size_bytes', 0):,} bytes",
        ]
        for img in resp.get('images', []):
            lines.append(f"  {img.get('image_id', '?')}: {img.get('size_bytes', 0):,} bytes, {img.get('file_count', 0)} files")
        return "\n".join(lines)
    if command == "dns-generate":
        lines = [
            f"Generated resolv.conf for {resp.get('container_id', '?')[:12]}:",
            f"  Nameservers: {', '.join(resp.get('nameservers', []))}",
            f"  Search domains: {', '.join(resp.get('search_domains', []))}",
            f"  Written: {resp.get('written', False)}",
        ]
        return "\n".join(lines)
    if command == "dns-resolve":
        host = resp.get('hostname', '?')
        addrs = resp.get('addresses', [])
        status = "resolved" if resp.get('resolved') else f"failed: {resp.get('error', '?')}"
        lines = [f"{host}: {status}"]
        for addr in addrs:
            lines.append(f"  {addr}")
        return "\n".join(lines)
    if command == "dns-get-config":
        lines = [
            f"DNS config for {resp.get('container_id', '?')[:12]} (source: {resp.get('source', '?')}):",
            f"  Nameservers: {', '.join(resp.get('nameservers', []))}",
            f"  Search domains: {', '.join(resp.get('search_domains', []))}",
            f"  Options: {', '.join(resp.get('options', []))}",
        ]
        return "\n".join(lines)
    if command == "dns-update":
        return f"DNS updated for {resp.get('container_id', '?')[:12]}: nameservers={resp.get('nameservers', [])}, search={resp.get('search_domains', [])}"
    if command == "create-network":
        return f"Network '{resp.get('name', '?')}' created: subnet={resp.get('subnet', '?')}, gateway={resp.get('gateway', '?')}"
    if command == "remove-network":
        return f"Network '{resp.get('name', '?')}' removed, {resp.get('disconnected', 0)} containers disconnected"
    if command == "list-networks":
        nets = resp.get('networks', [])
        if not nets:
            return "No networks configured"
        lines = ["Container networks:"]
        for n in nets:
            lines.append(f"  {n.get('name', '?')}: {n.get('subnet', '?')} ({n.get('container_count', 0)} containers)")
        return "\n".join(lines)
    if command == "connect-network":
        return f"Connected {resp.get('container_id', '?')[:12]} to '{resp.get('network', '?')}' with IP {resp.get('ip', '?')}"
    if command == "disconnect-network":
        return f"Disconnected {resp.get('container_id', '?')[:12]} from '{resp.get('network', '?')}' (removed IP {resp.get('removed_ip', '?')})"
    if command == "network-topology":
        nodes = resp.get('nodes', [])
        lines = [
            f"Network '{resp.get('network', '?')}' ({resp.get('subnet', '?')}):",
            f"  Gateway: {resp.get('gateway', '?')}",
            f"  Nodes: {len(nodes)}",
        ]
        for n in nodes:
            aliases = f" ({', '.join(n.get('aliases', []))})" if n.get('aliases') else ""
            lines.append(f"    {n.get('name', '?')}: {n.get('ip', '?')}{aliases} [{n.get('state', '?')}]")
        return "\n".join(lines)
    if command == "network-dns-resolve":
        if resp.get('resolved'):
            return f"{resp.get('name', '?')} -> {resp.get('ip', '?')} (container: {resp.get('container_id', '?')[:12]})"
        return f"{resp.get('name', '?')}: not found"
    if command == "test-connectivity":
        status = "reachable" if resp.get('reachable') else "unreachable"
        rtt = f" (RTT: {resp.get('rtt_ms', 0):.1f}ms)" if resp.get('reachable') else ""
        error = f" - {resp.get('error', '')}" if resp.get('error') else ""
        return f"{resp.get('src', '?')[:12]} -> {resp.get('dst', '?')}: {status}{rtt}{error}"
    if command == "plan-migration":
        lines = [
            f"Migration plan: {resp.get('container_id', '?')[:12]}",
            f"  {resp.get('source_node', '?')} -> {resp.get('target_node', '?')} ({resp.get('strategy', '?')})",
            f"  Estimated downtime: {resp.get('estimated_ms', 0)}ms",
            f"  Downtime OK: {resp.get('downtime_ok', False)}",
        ]
        for s in resp.get('steps', []):
            lines.append(f"  Step {s.get('step', '?')}: {s.get('action', '?')} ({s.get('estimated_ms', 0)}ms)")
        for r in resp.get('risks', []):
            lines.append(f"  ⚠ {r}")
        return "\n".join(lines)
    if command == "execute-migration":
        mode = "DRY RUN" if resp.get('dry_run') else "EXECUTED"
        return f"Migration {mode}: {resp.get('container_id', '?')[:12]} -> {resp.get('target_node', '?')} [{resp.get('status', '?')}] ({resp.get('duration_ms', 0)}ms)"
    if command == "migration-history":
        migrs = resp.get('migrations', [])
        if not migrs:
            return "No migration history"
        lines = ["Migration history:"]
        for m in migrs:
            lines.append(f"  {m.get('container_id', '?')[:12]}: {m.get('source', '?')} -> {m.get('target', '?')} [{m.get('status', '?')}] ({m.get('strategy', '?')})")
        return "\n".join(lines)
    if command == "migration-cost":
        lines = [
            f"Migration cost for {resp.get('container_id', '?')[:12]} ({resp.get('strategy', '?')}):",
            f"  Memory: {resp.get('memory_mb', 0)} MB",
            f"  Transfer: {resp.get('estimated_transfer_bytes', 0):,} bytes ({resp.get('estimated_transfer_seconds', 0)}s)",
            f"  Total: {resp.get('estimated_total_seconds', 0)}s (downtime: {resp.get('downtime_ms', 0)}ms)",
        ]
        for r in resp.get('risks', []):
            lines.append(f"  ⚠ {r}")
        return "\n".join(lines)
    if command == "configure-alert-channel":
        return f"Alert channel '{resp.get('channel_id', '?')}' ({resp.get('type', '?')}) enabled={resp.get('enabled', False)}"
    if command == "remove-alert-channel":
        return f"Alert channel '{resp.get('channel_id', '?')}' removed"
    if command == "list-alert-channels":
        chs = resp.get('channels', [])
        if not chs:
            return "No alert channels configured"
        lines = ["Alert channels:"]
        for ch in chs:
            status = "enabled" if ch.get('enabled') else "disabled"
            lines.append(f"  {ch.get('id', '?')}: {ch.get('type', '?')} [{status}] ({ch.get('alert_count', 0)} alerts)")
        return "\n".join(lines)
    if command == "enable-alert-channel":
        return f"Alert channel '{resp.get('channel_id', '?')}' enabled"
    if command == "disable-alert-channel":
        return f"Alert channel '{resp.get('channel_id', '?')}' disabled"
    if command == "configure-alert-rules":
        rules = resp.get('rules', {})
        lines = [f"Alert rules for {resp.get('target', '?')}:"]
        for k, v in rules.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)
    if command == "get-alert-rules":
        rules = resp.get('rules', resp.get('fleet_rules', {}))
        if not rules:
            return "No alert rules configured"
        lines = ["Alert rules:"]
        for k, v in rules.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)
    if command == "evaluate-alerts":
        alerts = resp.get('alerts', [])
        if not alerts:
            return f"No alerts for {resp.get('container_id', '?')[:12]}"
        lines = [f"Alerts for {resp.get('container_id', '?')[:12]}:"]
        for a in alerts:
            lines.append(f"  [{a.get('severity', '?')}] {a.get('message', '?')}")
        lines.append(f"  Notifications sent: {resp.get('notifications_sent', 0)}")
        return "\n".join(lines)
    if command == "alert-history":
        hist = resp.get('alerts', [])
        if not hist:
            return "No alert history"
        lines = ["Alert history:"]
        for h in hist:
            lines.append(f"  {h.get('alert_type', '?')} [{h.get('severity', '?')}] {h.get('message', '?')[:60]}")
        return "\n".join(lines)
    if command == "detect-anomalies":
        anomalies = resp.get('anomalies', [])
        if resp.get('insufficient_data'):
            return f"Insufficient data ({resp.get('data_points', 0)} points, need {resp.get('window_size', 30)})"
        lines = [
            f"Anomalies for {resp.get('container_id', '?')[:12]}:",
            f"  Data points: {resp.get('data_points', 0)}, Window: {resp.get('window_size', 0)}",
            f"  Anomalies found: {resp.get('anomaly_count', 0)}",
        ]
        for a in anomalies[:10]:
            lines.append(f"  [{a.get('method', '?')}] {a.get('metric', '?')}: {a.get('value', 0):.4f}")
        return "\n".join(lines)
    if command == "detect-fleet-anomalies":
        lines = [
            f"Fleet anomaly detection:",
            f"  Containers with anomalies: {resp.get('containers_with_anomalies', 0)}",
            f"  Total anomalies: {resp.get('total_anomalies', 0)}",
        ]
        return "\n".join(lines)
    if command == "diff-snapshots":
        lines = ["Snapshot diff:"]
        if resp.get('added'): lines.append(f"  Added: {len(resp['added'])} files")
        if resp.get('removed'): lines.append(f"  Removed: {len(resp['removed'])} files")
        if resp.get('modified'): lines.append(f"  Modified: {len(resp['modified'])} files")
        if resp.get('resource_changes'): lines.append(f"  Resource changes: {len(resp['resource_changes'])}")
        if not resp.get('has_changes'): lines.append("  No differences")
        return "\n".join(lines)
    if command == "rollback-snapshot":
        mode = "DRY RUN" if resp.get('dry_run') else "EXECUTED"
        lines = [
            f"Rollback {mode}: {resp.get('container_id', '?')[:12]}",
            f"  Status: {resp.get('status', '?')}",
            f"  Files to create: {len(resp.get('files_to_create', []))}",
            f"  Files to update: {len(resp.get('files_to_update', []))}",
            f"  Files to delete: {len(resp.get('files_to_delete', []))}",
        ]
        return "\n".join(lines)
    if command == "optimize-placement":
        lines = [
            f"Placement optimization ({resp.get('strategy', '?')}):",
            f"  Containers: {resp.get('containers_optimized', 0)}",
            f"  Nodes evaluated: {resp.get('nodes_evaluated', 0)}",
            f"  Average score: {resp.get('average_score', 0):.2f}",
        ]
        for r in resp.get('recommendations', []):
            lines.append(f"  {r.get('container_name', '?')}: {r.get('current_node', '?')} -> {r.get('recommended_node', '?')} (score: {r.get('score', 0):.2f})")
        return "\n".join(lines)
    if command == "placement-score":
        lines = [
            f"Placement score for {resp.get('container_id', '?')[:12]} on {resp.get('node_id', '?')}:",
            f"  Feasible: {resp.get('feasible', False)}",
            f"  Score: {resp.get('score', 0):.2f}",
        ]
        if not resp.get('feasible'):
            lines.append(f"  Reason: {resp.get('reason', '?')}")
        return "\n".join(lines)
    if command == "configure-auto-scaling":
        return f"Auto-scaling {'enabled' if resp.get('enabled') else 'disabled'} for {resp.get('container_id', '?')[:12]}: target={resp.get('target_memory_pct', 0)}%, range={resp.get('min_memory_mb', 0)}-{resp.get('max_memory_mb', 0)}MB"
    if command == "evaluate-and-adjust":
        lines = [
            f"Auto-scale eval for {resp.get('container_id', '?')[:12]}:",
            f"  Action: {resp.get('action', '?')}",
            f"  Usage: {resp.get('current_usage_pct', 0)}% (target: {resp.get('target_pct', 0)}%)",
            f"  Limit: {resp.get('previous_limit_mb', 0)}MB -> {resp.get('new_limit_mb', 0)}MB",
        ]
        return "\n".join(lines)
    if command == "auto-scaling-status":
        if not resp.get('configured'):
            return f"Auto-scaling not configured for {resp.get('container_id', '?')[:12]}"
        lines = [
            f"Auto-scaling for {resp.get('container_id', '?')[:12]}:",
            f"  Enabled: {resp.get('enabled', False)}",
            f"  Target: {resp.get('target_memory_pct', 0)}%",
            f"  Range: {resp.get('min_memory_mb', 0)}-{resp.get('max_memory_mb', 0)}MB",
            f"  Adjustments: {resp.get('adjustments_made', 0)}",
        ]
        return "\n".join(lines)
    if command == "batch-evaluate-scaling":
        lines = [
            f"Batch scaling: {resp.get('containers_evaluated', 0)} evaluated, {resp.get('adjustments_made', 0)} adjusted",
        ]
        return "\n".join(lines)
    if command == "generate-dependency-graph":
        return resp.get('graph', '(empty)')
    if command == "get-critical-path":
        names = resp.get('path_names', [])
        return f"Critical path ({resp.get('length', 0)} containers, ~{resp.get('estimated_seconds', 0)}s): {' -> '.join(names)}"
    if command == "register-federation-peer":
        return f"Federated with '{resp.get('cluster_name', '?')}' (trust: {resp.get('trust_level', '?')})"
    if command == "unregister-federation-peer":
        return f"Removed federation peer '{resp.get('peer_id', '?')}'"
    if command == "list-federation-peers":
        peers = resp.get('peers', [])
        if not peers:
            return "No federation peers"
        lines = ["Federation peers:"]
        for p in peers:
            lines.append(f"  {p.get('cluster_name', '?')}: {p.get('status', '?')} (trust: {p.get('trust_level', '?')}, shared: {p.get('shared_count', 0)})")
        return "\n".join(lines)
    if command == "share-container-with-peer":
        return f"Shared {resp.get('container_id', '?')[:12]} with peer '{resp.get('peer_id', '?')}' [{', '.join(resp.get('permissions', []))}]"
    if command == "unshare-container-from-peer":
        return f"Unshared {resp.get('container_id', '?')[:12]} from peer '{resp.get('peer_id', '?')}'"
    if command == "share-resources-with-peer":
        return f"Shared {resp.get('amount', 0)} {resp.get('resource_type', '?')} with peer '{resp.get('peer_id', '?')}' (total: {resp.get('total_shared', 0)})"
    if command == "get-federation-status":
        lines = [
            f"Federation: {resp.get('peer_count', 0)} peers, {resp.get('total_shared_containers', 0)} shared containers",
        ]
        for rtype, amount in resp.get('total_shared_resources', {}).items():
            lines.append(f"  Shared {rtype}: {amount}")
        return "\n".join(lines)
    if command == "plan-cross-cluster-migration":
        lines = [
            f"Cross-cluster migration:",
            f"  {resp.get('source_cluster', '?')} -> {resp.get('target_cluster', '?')}",
            f"  Strategy: {resp.get('strategy', '?')}, Trust: {resp.get('trust_level', '?')}",
        ]
        for s in resp.get('steps', []):
            lines.append(f"  Step {s.get('step', '?')}: {s.get('action', '?')}")
        return "\n".join(lines)
    if command == "configure-event-trigger":
        return f"Event trigger '{resp.get('trigger_id', '?')}' created: {resp.get('event_type', '?')} -> {resp.get('action', '?')}"
    if command == "remove-event-trigger":
        return f"Event trigger '{resp.get('trigger_id', '?')}' removed"
    if command == "list-event-triggers":
        triggers = resp.get('triggers', [])
        if not triggers:
            return "No event triggers configured"
        lines = ["Event triggers:"]
        for t in triggers:
            status = "enabled" if t.get('enabled') else "disabled"
            lines.append(f"  {t.get('id', '?')}: {t.get('event_type', '?')} -> {t.get('action', '?')} [{status}] ({t.get('fired_count', 0)} fired)")
        return "\n".join(lines)
    if command in ("enable-event-trigger", "disable-event-trigger"):
        return f"Event trigger '{resp.get('trigger_id', '?')}' {'enabled' if resp.get('enabled') else 'disabled'}"
    if command == "fire-event":
        lines = [f"Event '{resp.get('event_type', '?')}' fired:"]
        for f in resp.get('fired', []):
            lines.append(f"  Trigger {f.get('trigger_id', '?')}: {f.get('action', '?')}")
        return "\n".join(lines)
    if command == "get-event-log":
        events = resp.get('events', [])
        if not events:
            return "No events logged"
        lines = ["Event log:"]
        for e in events[:10]:
            lines.append(f"  [{e.get('type', '?')}] {e.get('container_id', 'global')[:12]}")
        return "\n".join(lines)
    if command == "get-trigger-stats":
        return f"Triggers: {resp.get('total_triggers', 0)} total, {resp.get('enabled_triggers', 0)} enabled, {resp.get('total_fired', 0)} fired"
    if command == "generate-cluster-dashboard":
        lines = [
            f"Cluster Health: {resp.get('status', '?').upper()}",
            f"  Containers: {resp.get('containers', {}).get('running', 0)}/{resp.get('containers', {}).get('total', 0)} running",
            f"  Memory: {resp.get('resources', {}).get('used_memory_mb', 0)}MB / {resp.get('resources', {}).get('total_memory_mb', 0)}MB ({resp.get('resources', {}).get('memory_utilization_pct', 0)}%)",
            f"  Health score: {resp.get('health', {}).get('average_score', 0):.0f}/100",
            f"  Alerts (1h): {resp.get('alerts', {}).get('recent_count', 0)}",
            f"  Triggers: {resp.get('triggers', {}).get('total_triggers', 0)} ({resp.get('triggers', {}).get('total_fired', 0)} fired)",
            f"  Cluster: {resp.get('cluster', {}).get('nodes', 0)} nodes, {resp.get('cluster', {}).get('networks', 0)} networks",
        ]
        return "\n".join(lines)
    if command == "configure-network-rule":
        return f"Network rule '{resp.get('rule_id', '?')}' created: {resp.get('direction', '?')} {resp.get('action', '?')} (priority {resp.get('priority', 0)})"
    if command == "remove-network-rule":
        return f"Network rule '{resp.get('rule_id', '?')}' removed"
    if command == "list-network-rules":
        rules = resp.get('rules', [])
        if not rules:
            return "No network rules configured"
        lines = ["Network rules:"]
        for r in rules:
            status = "enabled" if r.get('enabled') else "disabled"
            port_str = f":{r['port']}" if r.get('port') else "*"
            lines.append(f"  {r.get('id', '?')}: {r.get('direction', '?')} {r.get('action', '?')} {r.get('protocol', '?')}{port_str} [{status}] (hits: {r.get('hit_count', 0)})")
        return "\n".join(lines)
    if command in ("enable-network-rule", "disable-network-rule"):
        return f"Network rule '{resp.get('rule_id', '?')}' {'enabled' if resp.get('enabled') else 'disabled'}"
    if command == "evaluate-network-access":
        allowed = "ALLOWED" if resp.get('allowed') else "DENIED"
        return f"{allowed}: {resp.get('reason', '?')}"
    if command == "get-network-rule-stats":
        return f"Rules: {resp.get('total_rules', 0)} total ({resp.get('ingress_rules', 0)} ingress, {resp.get('egress_rules', 0)} egress), {resp.get('total_hits', 0)} hits"
    if command == "create-backup":
        return f"Backup '{resp.get('backup_id', '?')}' created: {resp.get('size_bytes', 0):,} bytes ({resp.get('backup_type', '?')})"
    if command == "list-backups":
        backups = resp.get('backups', [])
        if not backups:
            return "No backups found"
        lines = ["Backups:"]
        for b in backups:
            lines.append(f"  {b.get('backup_id', '?')}: {b.get('container_id', '?')[:12]} ({b.get('backup_type', '?')}, {b.get('size_bytes', 0):,} bytes)")
        return "\n".join(lines)
    if command == "delete-backup":
        return f"Backup '{resp.get('backup_id', '?')}' deleted"
    if command == "restore-from-backup":
        mode = "DRY RUN" if resp.get('dry_run') else "EXECUTED"
        return f"Restore {mode}: {resp.get('status', '?')} from {resp.get('backup_id', '?')}"
    if command == "configure-backup-policy":
        return f"Backup policy for {resp.get('container_id', '?')[:12]}: {'enabled' if resp.get('enabled') else 'disabled'}, interval={resp.get('interval_hours', 0)}h, retention={resp.get('retention_count', 0)}"
    if command == "get-dr-status":
        lines = [
            f"DR Status: {resp.get('total_backups', 0)} backups, {resp.get('total_size_bytes', 0):,} bytes",
            f"  Policies: {resp.get('policies_active', 0)} active, {resp.get('containers_with_policy', 0)} containers",
            f"  Stale (>7d): {resp.get('stale_backups_7d', 0)}, Covered: {resp.get('containers_covered', 0)} containers",
        ]
        return "\n".join(lines)
    if command == "aggregate-cluster-logs":
        lines = [
            f"Cluster logs: {resp.get('total_lines', 0)} entries from {resp.get('containers_scanned', 0)} containers",
        ]
        for e in resp.get('entries', [])[:5]:
            lines.append(f"  [{e.get('container_name', '?')}] {e.get('line', '')[:60]}")
        return "\n".join(lines)
    if command == "search-cluster-logs":
        lines = [f"Search: {resp.get('match_count', 0)} matches in {resp.get('containers_searched', 0)} containers"]
        for m in resp.get('matches', [])[:5]:
            lines.append(f"  [{m.get('container_name', '?')}] {m.get('line', '')[:60]}")
        return "\n".join(lines)
    if command == "get-log-stats":
        return f"Logs: {resp.get('total_lines', 0)} lines across {resp.get('containers_with_logs', 0)}/{resp.get('total_containers', 0)} containers (stdout: {resp.get('total_stdout_lines', 0)}, stderr: {resp.get('total_stderr_lines', 0)})"
    if command == "scan-container-security":
        lines = [
            f"Security scan: {resp.get('container_name', '?')} (risk: {resp.get('risk_level', '?')}, score: {resp.get('risk_score', 0)})",
        ]
        for f in resp.get('findings', []):
            lines.append(f"  [{f.get('severity', '?')}] {f.get('type', '?')}: {f.get('description', '?')}")
        return "\n".join(lines)
    if command == "scan-fleet-security":
        lines = [
            f"Fleet security: {resp.get('containers_scanned', 0)} scanned, {resp.get('total_findings', 0)} findings, {resp.get('critical_containers', 0)} critical",
        ]
        for r in resp.get('results', [])[:5]:
            lines.append(f"  {r.get('container_name', '?')}: {r.get('risk_level', '?')} (score: {r.get('risk_score', 0)}, {r.get('finding_count', 0)} findings)")
        return "\n".join(lines)
    if command == "get-security-summary":
        return f"Security: {resp.get('containers_scanned', 0)} scanned, {resp.get('total_findings', 0)} findings, overall risk: {resp.get('overall_risk', '?')}"
    if command == "scan-image-vulnerabilities":
        lines = [
            f"Vulnerability scan: {resp.get('packages_scanned', 0)} packages, {resp.get('vuln_count', 0)} vulns (risk: {resp.get('risk_level', '?')}, score: {resp.get('risk_score', 0)})",
        ]
        for v in resp.get('vulnerabilities', [])[:5]:
            lines.append(f"  [{v.get('severity', '?')}] {v.get('cve_id', '?')}: {v.get('package', '?')} - {v.get('description', '?')}")
        return "\n".join(lines)
    if command == "scan-container-vulnerabilities":
        lines = [
            f"Container {resp.get('container_id', '?')[:12]}: {resp.get('vuln_count', 0)} vulns (risk: {resp.get('risk_level', '?')})",
        ]
        return "\n".join(lines)
    if command == "scan-fleet-vulnerabilities":
        lines = [
            f"Fleet vulnerabilities: {resp.get('containers_scanned', 0)} scanned, {resp.get('total_vulnerabilities', 0)} total, {resp.get('critical_containers', 0)} critical",
        ]
        for r in resp.get('results', [])[:5]:
            lines.append(f"  {r.get('container_id', '?')[:12]}: {r.get('risk_level', '?')} ({r.get('vuln_count', 0)} vulns)")
        return "\n".join(lines)
    if command == "get-vulnerability-summary":
        return f"Vulnerabilities: {resp.get('containers_scanned', 0)} scanned, {resp.get('total_vulnerabilities', 0)} total, overall risk: {resp.get('overall_risk', '?')}"
    if command == "profile-container-performance":
        lines = [
            f"Performance profile: {resp.get('container_name', '?')} (score: {resp.get('performance_score', 0)}, rating: {resp.get('rating', '?')})",
            f"  Memory: {resp.get('memory', {}).get('ratio', 0)*100:.0f}% (score: {resp.get('memory', {}).get('score', 0)})",
            f"  CPU: {resp.get('cpu', {}).get('percent', 0):.1f}% (score: {resp.get('cpu', {}).get('score', 0)})",
            f"  PIDs: {resp.get('pids', {}).get('ratio', 0)*100:.0f}% (score: {resp.get('pids', {}).get('score', 0)})",
        ]
        if resp.get('bottlenecks'):
            lines.append(f"  Bottlenecks: {', '.join(resp['bottlenecks'])}")
        return "\n".join(lines)
    if command == "profile-fleet-performance":
        lines = [
            f"Fleet performance: {resp.get('containers_profiled', 0)} profiled, avg score: {resp.get('average_score', 0)}, {resp.get('critical_containers', 0)} critical",
        ]
        for r in resp.get('results', [])[:5]:
            lines.append(f"  {r.get('container_name', '?')}: {r.get('rating', '?')} (score: {r.get('performance_score', 0)})")
        return "\n".join(lines)
    if command == "get-performance-recommendations":
        lines = [
            f"Recommendations for {resp.get('container_id', '?')[:12]} (score: {resp.get('performance_score', 0)}, rating: {resp.get('rating', '?')}):",
        ]
        for r in resp.get('recommendations', []):
            lines.append(f"  [{r.get('severity', '?')}] {r.get('message', '?')}")
        if not resp.get('recommendations'):
            lines.append("  No recommendations")
        return "\n".join(lines)
    if command == "forecast-resource-needs":
        if resp.get('insufficient_data'):
            return f"Insufficient data for forecast ({resp.get('data_points', 0)} points)"
        lines = [
            f"Resource forecast ({resp.get('horizon_hours', 0)}h horizon):",
        ]
        for metric, fc in resp.get('forecasts', {}).items():
            lines.append(f"  {metric}: {fc.get('current', 0)*100:.0f}% -> {fc.get('predicted', 0)*100:.0f}% ({fc.get('trend', '?')})")
        if resp.get('risk_metrics'):
            lines.append(f"  Risk: {', '.join(resp['risk_metrics'])}")
        return "\n".join(lines)
    if command == "forecast-fleet-capacity":
        lines = [
            f"Fleet forecast: {resp.get('containers_forecasted', 0)} forecasted, {resp.get('high_risk_containers', 0)} high risk",
        ]
        return "\n".join(lines)
    if command == "get-capacity-recommendations":
        lines = [f"Capacity recommendations: {resp.get('count', 0)} ({resp.get('urgent_count', 0)} urgent)"]
        for r in resp.get('recommendations', [])[:5]:
            lines.append(f"  {r.get('container_id', '?')[:12]}: {r.get('metric', '?')} in {r.get('time_to_threshold_hours', 0):.0f}h ({r.get('action', '?')})")
        return "\n".join(lines)
    if command == "read-container-file":
        if resp.get('error'):
            return f"Error: {resp['error']}"
        lines = [f"{resp.get('path', '?')} ({resp.get('size', 0)} bytes):"]
        content = resp.get('content', '')
        for line in content.splitlines()[:20]:
            lines.append(f"  {line}")
        if resp.get('truncated'):
            lines.append("  ... (truncated)")
        return "\n".join(lines)
    if command == "write-container-file":
        if resp.get('error'):
            return f"Error: {resp['error']}"
        return f"Written {resp.get('bytes_written', 0)} bytes to {resp.get('path', '?')}"
    if command == "list-container-files":
        if resp.get('error'):
            return f"Error: {resp['error']}"
        lines = [f"Files in {resp.get('path', '?')}: {resp.get('entry_count', 0)} entries"]
        for e in resp.get('entries', [])[:20]:
            icon = "📁" if e.get('type') == 'directory' else "📄"
            lines.append(f"  {icon} {e.get('path', '?')} ({e.get('size', 0)} bytes)")
        return "\n".join(lines)
    if command == "delete-container-file":
        if resp.get('error'):
            return f"Error: {resp['error']}"
        return f"Deleted {resp.get('path', '?')}"
    if command == "get-file-info":
        if resp.get('error'):
            return f"Error: {resp['error']}"
        return f"{resp.get('path', '?')}: {resp.get('type', '?')}, {resp.get('size', 0)} bytes, mode={resp.get('mode', '?')}"
    if command == "get-process-tree":
        lines = [f"Process tree: {resp.get('total_processes', 0)} processes"]
        for proc in resp.get('tree', [])[:10]:
            lines.append(f"  🟢 {proc.get('name', '?')} (PID {proc.get('pid', '?')})")
            for child in proc.get('children', [])[:5]:
                lines.append(f"    └── {child.get('name', '?')} (PID {child.get('pid', '?')})")
        return "\n".join(lines)
    if command == "get-process-stats":
        return f"Processes: {resp.get('total_processes', 0)} total (running: {resp.get('running', 0)}, sleeping: {resp.get('sleeping', 0)}, zombie: {resp.get('zombie', 0)})"
    if command == "generate-comparison-report":
        lines = [
            f"Comparison Report ({resp.get('container_count', 0)} containers):",
            f"  Total memory: {resp.get('totals', {}).get('memory_mb', 0)}MB",
            f"  Total cost: ${resp.get('totals', {}).get('cost_per_hour', 0):.4f}/hour",
            f"  Average performance: {resp.get('totals', {}).get('average_performance', 0)}/100",
        ]
        for d in resp.get("containers", []):
            lines.append(f"  {d.get('name', '?')}: {d.get('memory_mb', 0)}MB ({d.get('memory_utilization', 0)}% used), score={d.get('performance_score', 0)}, ${d.get('cost_per_hour', 0):.4f}/h")
        if resp.get('recommendations'):
            lines.append(f"  Recommendations ({resp.get('recommendation_count', 0)}):")
            for r in resp['recommendations'][:3]:
                lines.append(f"    - {r.get('message', '?')}")
        return "\n".join(lines)
    if command == "generate-cost-report":
        lines = [
            f"Cost Report:",
            f"  Per hour: ${resp.get('total_cost_per_hour', 0):.4f}",
            f"  Per day: ${resp.get('total_cost_per_day', 0):.2f}",
            f"  Per month: ${resp.get('total_cost_per_month', 0):.2f}",
        ]
        for c in resp.get("containers", [])[:5]:
            lines.append(f"  {c.get('name', '?')}: ${c.get('cost_per_hour', 0):.4f}/h ({c.get('memory_mb', 0)}MB)")
        return "\n".join(lines)
    if command == "configure-health-check":
        return f"Health check configured for {resp.get('container_name', '?')}: type={resp.get('health_check', {}).get('type', '?')}"
    if command == "get-health-check":
        hc = resp.get('health_check', {})
        state = resp.get('state', {})
        lines = [
            f"Health check for {resp.get('container_name', '?')}:",
            f"  Type: {hc.get('type', 'none') if hc else 'none'}",
            f"  Status: {state.get('status', 'unknown')}",
            f"  Failures: {state.get('consecutive_failures', 0)}, Successes: {state.get('consecutive_successes', 0)}",
        ]
        return "\n".join(lines)
    if command == "evaluate-health-check":
        return f"Health check for {resp.get('container_name', '?')}: {resp.get('status', '?')} - {resp.get('detail', '')}"
    if command == "get-readiness-status":
        return f"Readiness for {resp.get('container_name', '?')}: ready={resp.get('ready', False)}, health={resp.get('health_status', '?')}"
    if command == "get-liveness-status":
        return f"Liveness for {resp.get('container_name', '?')}: alive={resp.get('alive', False)}, health={resp.get('health_status', '?')}"
    if command == "fleet-health-overview":
        s = resp.get('summary', {})
        lines = [
            "Fleet Health Overview:",
            f"  Healthy: {s.get('healthy_count', 0)}",
            f"  Unhealthy: {s.get('unhealthy_count', 0)}",
            f"  Pending: {s.get('pending_count', 0)}",
            f"  No check: {s.get('no_check_count', 0)}",
        ]
        return "\n".join(lines)
    if command == "configure-escalation-chain":
        chain = resp.get('chain', {})
        return f"Escalation chain '{chain.get('name', '?')}' configured for {resp.get('container_name', '?')} with {len(chain.get('steps', []))} steps"
    if command == "evaluate-escalation":
        lines = [f"Escalation for {resp.get('container_name', '?')} (severity={resp.get('severity', 0)}):"]
        for e in resp.get('evaluations', []):
            lines.append(f"  Chain {e.get('chain', '?')}: {e.get('action', 'none') or e.get('message', '')}")
        return "\n".join(lines)
    if command == "get-escalation-status":
        lines = [f"Escalation status for {resp.get('container_name', '?')}:"]
        for name, chain in resp.get('chains', {}).items():
            lines.append(f"  {name}: active={chain.get('active')}, actions={chain.get('total_actions', 0)}")
        return "\n".join(lines)
    if command == "generate-compliance-report":
        lines = [
            f"Compliance Report (policy: {resp.get('policy', '?')}):",
            f"  Status: {resp.get('overall_status', '?').upper()}",
            f"  Score: {resp.get('average_score', 0)}/100",
            f"  Critical: {resp.get('critical_count', 0)}, Warnings: {resp.get('warning_count', 0)}",
        ]
        return "\n".join(lines)
    if command == "export-audit-logs":
        return f"Exported {resp.get('event_count', 0)} audit events ({resp.get('format', '?')} format)"
    if command == "get-compliance-summary":
        lines = [
            f"Compliance Summary (policy: {resp.get('policy', '?')}):",
            f"  Status: {resp.get('overall_status', '?').upper()}",
            f"  Score: {resp.get('average_score', 0)}/100",
        ]
        for r in resp.get('recommendations', []):
            lines.append(f"  - {r}")
        return "\n".join(lines)
    if command == "create-secret":
        return f"Secret '{resp.get('name', '?')}' created (id={resp.get('id', '?')})"
    if command == "get-secret":
        lines = [f"Secret {resp.get('name', '?')} ({resp.get('type', '?')}):", f"  ID: {resp.get('id', '?')}", f"  Namespace: {resp.get('namespace', '?')}"]
        for k in resp.get('keys', []):
            lines.append(f"  Key: {k}")
        return "\n".join(lines)
    if command == "rotate-secret":
        return f"Secret rotated (rotation #{resp.get('rotation_count', 0)})"
    if command == "list-secrets":
        lines = [f"Secrets ({resp.get('count', 0)}):"]
        for s in resp.get('secrets', [])[:10]:
            lines.append(f"  {s['name']}: {s['type']} ({', '.join(s.get('keys', []))})")
        return "\n".join(lines)
    if command == "secret-usage":
        lines = [f"Secret usage ({resp.get('total', 0)} total):"]
        for ns, info in resp.get('namespaces', {}).items():
            lines.append(f"  {ns}: {info['count']} secrets")
        return "\n".join(lines)
    if command == "create-namespace":
        return f"Namespace '{resp.get('name', '?')}' created"
    if command == "set-resource-quota":
        return f"Quota set: {resp.get('resource', '?')} = {resp.get('hard_limit', 0)} (soft={resp.get('soft_limit', 0)})"
    if command == "get-resource-quota":
        lines = [f"Quotas for {resp.get('namespace', '?')}:"]
        for r, q in resp.get('quotas', {}).items():
            lines.append(f"  {r}: hard={q['hard']}, soft={q['soft']}")
        return "\n".join(lines)
    if command == "check-quota-compliance":
        status = "COMPLIANT" if resp.get('compliant') else "VIOLATIONS"
        lines = [f"Quota compliance for {resp.get('namespace', '?')}: {status}"]
        for v in resp.get('violations', []):
            lines.append(f"  {v['resource']}: {v['severity']} ({v['current']}/{v['limit']})")
        return "\n".join(lines)
    if command == "list-namespaces":
        lines = ["Namespaces:"]
        for ns in resp.get('namespaces', []):
            lines.append(f"  {ns['name']}: {ns['quota_count']} quotas")
        return "\n".join(lines)
    if command == "namespace-summary":
        lines = ["Namespace Summary:", f"  Namespaces: {resp.get('namespaces', 0)}", f"  Total quotas: {resp.get('total_quotas', 0)}"]
        return "\n".join(lines)
    if command == "record-deployment":
        return f"Recorded deployment v{resp.get('version', '?')} for {resp.get('container_id', '?')[:12]}"
    if command == "deployment-history":
        lines = ["Deployment History:"]
        for v in resp.get('versions', []):
            tag = " [ROLLBACK]" if v.get('rolled_back') else ""
            lines.append(f"  v{v['version']}{tag}: {v.get('notes', '') or 'no notes'}")
        return "\n".join(lines)
    if command == "rollback-deployment":
        return f"Rolled back to v{resp.get('rolled_back_to', '?')} (new version: v{resp.get('new_version', '?')})"
    if command == "deployment-diff":
        lines = ["Changes:"]
        for c in resp.get('changes', []):
            lines.append(f"  {c['field']}: {c.get('from', '?')} -> {c.get('to', '?')}")
        if not resp.get('changes'):
            lines.append("  (no differences)")
        return "\n".join(lines)
    if command == "rollback-candidates":
        lines = ["Rollback candidates:"]
        for c in resp.get('candidates', []):
            lines.append(f"  v{c['version']}: {c.get('notes', '')}")
        return "\n".join(lines)
    if command == "deployment-status":
        current = resp.get('current')
        if current:
            return f"Current version: v{current['version']} ({current.get('notes', '')})"
        return "No deployments recorded"
    if command == "configure-graceful-shutdown":
        cfg = resp.get('shutdown_config', {})
        return f"Graceful shutdown configured: drain={cfg.get('drain_timeout', 0)}s, signal={cfg.get('signal', '?')}"
    if command == "initiate-graceful-shutdown":
        return f"Graceful shutdown initiated: status={resp.get('status', '?')}, timeout={resp.get('drain_timeout', 0)}s"
    if command == "get-shutdown-status":
        state = resp.get('state', {})
        return f"Shutdown status: {state.get('status', '?')}"
    if command == "force-shutdown":
        return f"Force shutdown: {resp.get('status', '?')}"
    if command == "batch-graceful-shutdown":
        return f"Batch shutdown: {resp.get('container_count', 0)} containers"
    if command == "get-drain-progress":
        return f"Drain progress: {resp.get('progress_pct', 0)}% ({resp.get('elapsed_seconds', 0)}s elapsed)"
    if command == "register-config-watcher":
        return f"Watcher registered: {resp.get('watcher_id', '?')} watching {resp.get('path', '?')}"
    if command == "trigger-config-reload":
        return f"Config reload: {resp.get('status', '?')} - {resp.get('message', '')}"
    if command == "get-config-watchers":
        return f"Config watchers: {resp.get('count', 0)}"
    if command == "hot-reload-config":
        return f"Hot reload: {resp.get('change_count', 0)} changes applied"
    if command == "get-reload-history":
        return f"Reload history: {resp.get('total_reloads', 0)} total reloads"
    if command == "record-event":
        return f"Event recorded (id={resp.get('event_id', '?')})"
    if command == "correlate-events":
        lines = ["Correlated events:"]
        for c in resp.get('clusters', [])[:5]:
            lines.append(f"  {c['event_type']}: {c['container_count']} containers, {c['event_count']} events")
        return "\n".join(lines) if lines[1:] else "No correlations found"
    if command == "analyze-event-patterns":
        lines = [f"Event patterns ({resp.get('total_events', 0)} events):"]
        for p in resp.get('patterns', [])[:5]:
            lines.append(f"  {p['type']}: {p['count']} events")
        return "\n".join(lines)
    if command == "suggest-root-cause":
        lines = ["Root cause suggestions:"]
        for s in resp.get('suggestions', []):
            lines.append(f"  {s['cause']} (confidence={s['confidence']:.0%}): {s['description']}")
        return "\n".join(lines) if lines[1:] else "No root causes identified"
    if command == "get-event-timeline":
        return f"Event timeline: {resp.get('count', 0)} events"
    if command == "configure-network-monitoring":
        cfg = resp.get('config', {})
        return f"Network monitoring configured: interfaces={cfg.get('interfaces', [])}, interval={cfg.get('sample_interval', 0)}s"
    if command == "get-network-latency-stats":
        return f"Latency: avg={resp.get('avg_ms', 0)}ms, p95={resp.get('p95_ms', 0)}ms, p99={resp.get('p99_ms', 0)}ms"
    if command == "get-bandwidth-stats":
        return f"Bandwidth: RX={resp.get('total_rx_mb', 0)}MB, TX={resp.get('total_tx_mb', 0)}MB"
    if command == "get-network-health":
        return f"Network health: {resp.get('status', '?').upper()}, latency={resp.get('latency', 0)}ms, errors={resp.get('errors', 0)}"
    if command == "fleet-network-overview":
        return f"Fleet network: {resp.get('count', 0)} containers monitored"
    if command == "configure-storage-profiling":
        return f"Storage profiling configured: cache={resp.get('config', {}).get('cache_size_mb', 0)}MB"
    if command == "get-storage-io-stats":
        return f"Storage I/O: read={resp.get('read_mb', 0)}MB ({resp.get('read_ops', 0)} ops), write={resp.get('write_mb', 0)}MB ({resp.get('write_ops', 0)} ops), cache hit={resp.get('cache_hit_rate', 0)}%"
    if command == "get-storage-io-latency":
        return f"Storage latency: avg_read={resp.get('avg_read_ms', 0)}ms, avg_write={resp.get('avg_write_ms', 0)}ms"
    if command == "clear-storage-cache":
        return f"Cache cleared: {resp.get('cleared', 0)} entries"
    if command == "get-storage-hot-paths":
        lines = ["Hot paths:"]
        for p in resp.get('hot_paths', [])[:5]:
            lines.append(f"  {p['path']}: {p['count']} ops")
        return "\n".join(lines)
    if command == "verify-audit-integrity":
        status = "VALID" if resp.get('valid') else f"TAMPERED ({resp.get('tampered_count', 0)} events)"
        return f"Audit integrity: {status}, chain={resp.get('chain_length', 0)}"
    if command == "audit-integrity-report":
        status = "VALID" if resp.get('valid') else f"TAMPERED ({resp.get('tampered_count', 0)} events)"
        return f"Audit report: {status}, events={resp.get('total_audit_events', 0)}"
    if command == "tamper-summary":
        status = "ALL VALID" if resp.get('all_valid') else f"{resp.get('total_tampered', 0)} TAMPERED"
        return f"Tamper check: {resp.get('containers_checked', 0)} containers, {status}"
    if command == "configure-rate-limit":
        return f"Rate limit '{resp.get('limiter', '?')}' configured: rate={resp.get('rate', 0)}/s, burst={resp.get('burst', 0)}"
    if command == "check-rate-limit":
        status = "ALLOWED" if resp.get('allowed') else "REJECTED"
        return f"Rate limit: {status}, tokens={resp.get('tokens_remaining', 0)}"
    if command == "get-rate-limit-stats":
        return f"Rate limit '{resp.get('limiter', '?')}': allowed={resp.get('total_allowed', 0)}, rejected={resp.get('total_rejected', 0)}, rejection={resp.get('rejection_rate', 0)}%"
    if command == "get-all-rate-limiters":
        return f"Rate limiters: {resp.get('count', 0)}"
    if command == "create-feature-flag":
        return f"Feature flag '{resp.get('name', '?')}' created: enabled={resp.get('enabled', False)}, rollout={resp.get('rollout_percentage', 0)}%"
    if command == "evaluate-feature-flag":
        status = "ACTIVE" if resp.get('active') else "INACTIVE"
        return f"Flag '{resp.get('flag', '?')}': {status} ({resp.get('reason', '?')})"
    if command == "list-feature-flags":
        lines = ["Feature flags:"]
        for f in resp.get('flags', []):
            status = "ON" if f['enabled'] else "OFF"
            lines.append(f"  {f['name']}: {status} (rollout={f['rollout_percentage']}%, evals={f['evaluation_count']})")
        return "\n".join(lines) if lines[1:] else "No feature flags"
    if command == "feature-flag-summary":
        return f"Feature flags: {resp.get('total', 0)} total, {resp.get('enabled', 0)} enabled"
    if command == "create-bluegreen":
        return f"Blue-green '{resp.get('name', '?')}' created: active={resp.get('active_slot', '?')}"
    if command == "switch-traffic":
        pct = resp.get('traffic_percentage', {})
        return f"Traffic switched: blue={pct.get('blue', 0)}%, green={pct.get('green', 0)}%"
    if command == "get-bluegreen-status":
        return f"Blue-green '{resp.get('name', '?')}': active={resp.get('active_slot', '?')}, switches={resp.get('switch_count', 0)}"
    if command == "get-bluegreen-history":
        return f"Switch history: {resp.get('count', 0)} switches"
    if command == "list-bluegreen":
        lines = ["Blue-green deployments:"]
        for d in resp.get('deployments', []):
            lines.append(f"  {d['name']}: active={d['active_slot']}")
        return "\n".join(lines) if lines[1:] else "No deployments"
    if command == "create-canary":
        return f"Canary '{resp.get('name', '?')}' created: traffic={resp.get('traffic_percentage', 0)}%"
    if command == "evaluate-canary-health":
        action = resp.get('action', '?').upper()
        return f"Canary: {action}, error_rate={resp.get('canary_error_rate', 0)}%, latency={resp.get('canary_avg_latency_ms', 0)}ms"
    if command == "promote-canary":
        return f"Canary promoted to {resp.get('traffic_percentage', 0)}% traffic"
    if command == "rollback-canary":
        return f"Canary rolled back to {resp.get('traffic_percentage', 0)}% traffic"
    if command == "get-canary-status":
        return f"Canary '{resp.get('name', '?')}': phase={resp.get('phase', '?')}, traffic={resp.get('traffic_percentage', 0)}%"
    if command == "list-canary":
        lines = ["Canary deployments:"]
        for d in resp.get('deployments', []):
            lines.append(f"  {d['name']}: {d['phase']}, {d['traffic_percentage']}%")
        return "\n".join(lines) if lines[1:] else "No canary deployments"
    if command == "register-service":
        return f"Service '{resp.get('service', '?')}' registered on port {resp.get('port', 0)}"
    if command == "discover-service":
        return f"Discovered {resp.get('count', 0)} instance(s) of '{resp.get('service', '?')}'"
    if command == "list-services":
        lines = ["Services:"]
        for s in resp.get('services', []):
            lines.append(f"  {s['name']}: {s['healthy_instances']}/{s['total_instances']} healthy")
        return "\n".join(lines) if lines[1:] else "No services"
    if command == "inject-dependency":
        if resp.get('injected'):
            return f"Injected {resp.get('service', '?')} -> {resp.get('env_var', '?')}={resp.get('value', '?')}"
        return f"Dependency '{resp.get('service', '?')}' not available"
    if command == "create-trace":
        return f"Trace created: {resp.get('trace_id', '?')} ({resp.get('name', '?')})"
    if command == "start-span":
        return f"Span started: {resp.get('span_id', '?')}"
    if command == "finish-trace":
        return f"Trace finished: {resp.get('span_count', 0)} spans, {resp.get('duration_ms', 0)}ms"
    if command == "get-trace-summary":
        errors = resp.get('error_spans', 0)
        return f"Trace {resp.get('trace_id', '?')[:12]}: {resp.get('span_count', 0)} spans, {resp.get('duration_ms', 0)}ms, {errors} errors"
    if command == "list-traces":
        return f"Traces: {resp.get('count', 0)}"
    if command == "create-chaos-experiment":
        return f"Chaos experiment '{resp.get('name', '?')}' created: {resp.get('fault_type', '?')}, {resp.get('targets', 0)} targets"
    if command == "get-chaos-results":
        return f"Chaos results: {resp.get('observations', 0)} observations, status={resp.get('status', '?')}"
    if command == "list-chaos":
        lines = ["Chaos experiments:"]
        for e in resp.get('experiments', []):
            lines.append(f"  {e['name']}: {e['fault_type']} ({e['status']})")
        return "\n".join(lines) if lines[1:] else "No experiments"
    if command == "list-game-days":
        return f"Game days: {resp.get('count', 0)}"
    if command == "configure-cost-allocation":
        return f"Cost configured: ${resp.get('cost_per_hour', 0)}/hr, team={resp.get('team', '?')}"
    if command == "get-container-cost":
        return f"Cost: ${resp.get('total_cost', 0):.4f} for {resp.get('hours', 0)}h @ ${resp.get('cost_per_hour', 0)}/hr"
    if command == "get-team-cost":
        return f"Team '{resp.get('team', '?')}': ${resp.get('total_cost', 0):.4f} for {resp.get('container_count', 0)} containers"
    if command == "get-fleet-cost":
        lines = [f"Fleet cost: ${resp.get('total_cost', 0):.4f} for {resp.get('container_count', 0)} containers"]
        for team, info in resp.get('teams', {}).items():
            lines.append(f"  {team}: ${info['cost']:.4f} ({info['count']} containers)")
        return "\n".join(lines)
    if command == "get-billing-breakdown":
        lines = ["Billing breakdown:"]
        for tag, info in resp.get('tags', {}).items():
            lines.append(f"  {tag}: ${info['cost']:.4f} ({info['count']})")
        return "\n".join(lines)
    if command == "create-slo":
        return f"SLO '{resp.get('name', '?')}' created: {resp.get('target_percentage', 0)}% target"
    if command == "get-slo-status":
        status = "MET" if resp.get('met') else "BREACHED"
        return f"SLO '{resp.get('name', '?')}': {status}, SLI={resp.get('current_sli', 0)}%, budget={resp.get('error_budget_remaining', 0)}%"
    if command == "list-slos":
        lines = ["SLOs:"]
        for s in resp.get('slos', []):
            status = "MET" if s['met'] else "BREACHED"
            lines.append(f"  {s['name']}: {status} ({s['current_sli']}/{s['target_percentage']}%)")
        return "\n".join(lines) if lines[1:] else "No SLOs"
    if command == "get-error-budget-report":
        return f"Error budget utilization: {resp.get('total_budget_used_pct', 0)}%"
    if command == "recommend-right-sizing":
        lines = ["Right-sizing recommendations:"]
        for r in resp.get('recommendations', []):
            if 'potential_savings_pct' in r:
                lines.append(f"  {r['resource']}: {r.get('current_mb', r.get('current', 0))} -> {r.get('suggested_mb', r.get('suggested', 0))} (save {r['potential_savings_pct']}%)")
            else:
                lines.append(f"  {r['resource']}: {r.get('current', 0)} -> {r.get('suggested', r.get('suggested_mb', 0))} ({r.get('reason', '')})")
        return "\n".join(lines) if lines[1:] else "No recommendations"
    if command == "fleet-right-sizing-report":
        return f"Right-sizing: {resp.get('containers_with_recommendations', 0)}/{resp.get('total_containers', 0)} containers have recommendations, {resp.get('total_potential_savings_pct', 0)}% potential savings"
    if command == "get-mesh-topology":
        return f"Mesh: {resp.get('clusters', 0)} clusters, {resp.get('services', []) and len(resp['services']) or 0} services, {resp.get('policies', 0)} policies"
    if command == "create-traffic-policy":
        return f"Traffic policy '{resp.get('name', '?')}' created: {resp.get('source', '?')} -> {resp.get('destination', '?')}"
    if command == "list-mesh-policies":
        lines = ["Mesh policies:"]
        for p in resp.get('policies', []):
            lines.append(f"  {p['name']}: {p['source']} -> {p['destination']} ({'enabled' if p['enabled'] else 'disabled'})")
        return "\n".join(lines) if lines[1:] else "No policies"
    if command == "configure-auto-rollback":
        return f"Auto-rollback configured for SLO '{resp.get('slo_name', '?')}' (threshold={resp.get('breach_threshold', 0)}%)"
    if command == "check-auto-rollback":
        return f"Auto-rollback: {resp.get('rollbacks_triggered', 0)} rollbacks triggered"
    if command == "get-auto-rollback-status":
        lines = ["Auto-rollback status:"]
        for c in resp.get('configurations', []):
            status = "enabled" if c['enabled'] else "disabled"
            lines.append(f"  SLO {c['slo_name']}: {status}, triggered={c['trigger_count']}")
        return "\n".join(lines) if lines[1:] else "No auto-rollbacks"
    if command == "run-benchmark":
        return f"Benchmark: avg={resp.get('avg_ms', 0)}ms, p95={resp.get('p95_ms', 0)}ms, ops/s={resp.get('ops_per_second', 0)}"
    if command == "detect-regression":
        status = "REGRESSION DETECTED" if resp.get('has_regression') else "No regression"
        return f"Regression: {status} ({resp.get('regression_pct', 0)}% change)"
    if command == "fleet-benchmark-summary":
        return f"Fleet benchmarks: {resp.get('total_runs', 0)} total runs across {len(resp.get('benchmarks', []))} benchmarks"
    if command == "get-tenant-usage":
        usage = resp.get('usage', {})
        quota = resp.get('quota', {})
        return f"Tenant '{resp.get('tenant', '?')}': {usage.get('containers', 0)} containers, {usage.get('memory_mb', 0)}MB memory"
    if command == "list-tenants":
        lines = ["Tenants:"]
        for t in resp.get('tenants', []):
            lines.append(f"  {t['name']}: {t['isolation_level']}, {t['containers']} containers")
        return "\n".join(lines) if lines[1:] else "No tenants"
    if command == "get-profile-summary":
        lines = [f"Profile {resp.get('profile_id', '?')[:20]} ({resp.get('type', '?')}):"]
        for f in resp.get('hottest_functions', [])[:3]:
            lines.append(f"  {f['function']}: {f['value']}")
        return "\n".join(lines)
    if command == "list-profiles":
        return f"Profiles: {resp.get('count', 0)}"
    if command == "validate-config":
        status = "VALID" if resp.get('valid') else f"{resp.get('error_count', 0)} ERRORS"
        return f"Config validation: {status}"
    if command == "detect-config-drift":
        status = "DRIFT DETECTED" if resp.get('has_drift') else "No drift"
        return f"Config drift: {status} ({resp.get('change_count', 0)} changes)"
    if command == "enforce-config-policy":
        status = "COMPLIANT" if resp.get('compliant') else f"{resp.get('violation_count', 0)} VIOLATIONS"
        return f"Config policy: {status}"
    if command == "check-permission":
        status = "ALLOWED" if resp.get('allowed') else "DENIED"
        return f"Permission: {status} for {resp.get('user', '?')} on {resp.get('permission', '?')}"
    if command == "get-user-permissions":
        return f"User {resp.get('user', '?')}: {resp.get('permission_count', 0)} permissions across {len(resp.get('roles', []))} roles"
    if command == "list-rbac-users":
        return f"Users: {resp.get('count', 0)}"
    if command == "list-rbac-roles":
        return f"Roles: {resp.get('count', 0)}"
    if command == "get-stream-status":
        return f"Stream '{resp.get('name', '?')}': events={resp.get('events_matched', 0)}, alerts={resp.get('alerts_sent', 0)}"
    if command == "list-audit-streams":
        return f"Audit streams: {resp.get('count', 0)}"
    if command == "audit-stream-summary":
        return f"Streams: {resp.get('streams', 0)}, events: {resp.get('total_events', 0)}, alerts: {resp.get('total_alerts', 0)}"
    if command == "configure-cost-attribution":
        return f"Cost attribution configured for container {resp.get('container_id', '?')[:12]}"
    if command == "get-pod-cost":
        lines = [
            f"Container {resp.get('container_name', '?')} ({resp.get('container_id', '?')[:12]}):",
            f"  team: {resp.get('team', '?')}, tag: {resp.get('billing_tag', '?')}",
            f"  rate: {resp.get('currency', 'USD')} {resp.get('cost_per_hour', 0):.4f}/hr",
            f"  uptime: {resp.get('uptime_hours', 0):.2f}h, cost: {resp.get('currency', 'USD')} {resp.get('total_cost', 0):.4f}",
        ]
        return "\n".join(lines)
    if command == "chargeback-report":
        lines = ["Chargeback Report:"]
        for team, cost in resp.get("by_team", {}).items():
            lines.append(f"  {team}: {cost:.4f}")
        lines.append(f"  Total: {resp.get('total_cost', 0):.4f}")
        return "\n".join(lines)
    if command == "fleet-cost-overview":
        lines = ["Fleet Cost Overview:"]
        lines.append(f"  Containers: {resp.get('container_count', 0)}")
        lines.append(f"  Total cost: {resp.get('total_cost', 0):.4f}")
        return "\n".join(lines)
    if command == "simulate-network-policy":
        status = "ALLOWED" if resp.get("allowed") else "DENIED"
        lines = [f"Simulation: {resp.get('source', '?')} -> {resp.get('dest', '?')}:{resp.get('dest_port', '?')} [{resp.get('protocol', '?')}] = {status}"]
        if resp.get("allowed_by"):
            lines.append(f"  Allowed by: {resp['allowed_by']}")
        if resp.get("denied_by"):
            lines.append(f"  Denied by: {resp['denied_by']}")
        return "\n".join(lines)
    if command == "what-if-policy-change":
        lines = ["What-If Analysis:"]
        lines.append(f"  Current: {'ALLOWED' if resp.get('current_allowed') else 'DENIED'}")
        lines.append(f"  After change: {'ALLOWED' if resp.get('after_change_allowed') else 'DENIED'}")
        lines.append(f"  Impact: {resp.get('impact', '?')}")
        return "\n".join(lines)
    if command == "register-lifecycle-hook":
        return f"Hook registered for container {resp.get('container_id', '?')[:12]} phase={resp.get('phase', '?')} (total: {resp.get('hook_count', 0)})"
    if command == "execute-lifecycle-hooks":
        lines = [f"Phase {resp.get('phase', '?')}: executed {resp.get('executed', 0)} hooks"]
        if resp.get("rolled_back"):
            lines.append("  WARNING: Rollback triggered due to failure")
        return "\n".join(lines)
    if command == "list-lifecycle-hooks":
        hooks = resp.get("hooks", {})
        lines = ["Lifecycle Hooks:"]
        for phase, hook_list in hooks.items():
            lines.append(f"  {phase}: {len(hook_list)} hook(s)")
        if not hooks:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "register-webhook":
        return f"Webhook '{resp.get('name', '?')}' registered as {resp.get('type', '?')}"
    if command == "evaluate-admission":
        status = "ALLOWED" if resp.get("allowed") else "DENIED"
        patches = resp.get("patches", [])
        lines = [f"Admission {status} via {resp.get('webhook', '?')} ({resp.get('type', '?')})"]
        if patches:
            lines.append(f"  Mutations: {len(patches)} patch(es)")
        return "\n".join(lines)
    if command == "webhook-status":
        lines = [
            f"Webhook {resp.get('name', '?')} ({resp.get('type', '?')}):",
            f"  enabled: {resp.get('enabled', False)}",
            f"  invocations: {resp.get('invocations', 0)}, admissions: {resp.get('admissions', 0)}, rejections: {resp.get('rejections', 0)}",
        ]
        return "\n".join(lines)
    if command == "list-webhooks":
        whs = resp.get("webhooks", [])
        lines = ["Admission Webhooks:"]
        for wh in whs:
            lines.append(f"  {wh['name']} ({wh['type']}): invocations={wh['invocations']}, admissions={wh['admissions']}")
        if not whs:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "register-image-signature":
        return f"Image signature registered: {resp.get('image_ref', '?')}"
    if command == "set-build-provenance":
        return f"Build provenance set for {resp.get('image_ref', '?')}"
    if command == "verify-image":
        status = "VERIFIED" if resp.get("verified") else "FAILED"
        lines = [f"Image {resp.get('image_ref', '?')}: {status}"]
        if resp.get("digest"):
            lines.append(f"  digest: {resp['digest'][:20]}...")
        if resp.get("signer"):
            lines.append(f"  signer: {resp['signer']}")
        if resp.get("issues"):
            lines.append(f"  issues: {', '.join(resp['issues'])}")
        return "\n".join(lines)
    if command == "supply-chain-status":
        return f"Supply chain: {resp.get('signed', 0)}/{resp.get('total', 0)} signed, {resp.get('with_provenance', 0)} with provenance"
    if command == "create-resource-pool":
        return f"Resource pool '{resp.get('name', '?')}' created"
    if command == "pool-status":
        lines = [
            f"Pool {resp.get('name', '?')}:",
            f"  memory: {resp.get('allocated_memory_mb', 0)}/{resp.get('total_memory_mb', 0)}MB allocated, {resp.get('reserved_memory_mb', 0)}MB reserved",
            f"  cpu: {resp.get('allocated_cpu_shares', 0)}/{resp.get('total_cpu_shares', 0)} allocated, {resp.get('reserved_cpu_shares', 0)} reserved",
            f"  members: {resp.get('member_count', 0)}, reservations: {resp.get('reservation_count', 0)}",
        ]
        return "\n".join(lines)
    if command == "list-resource-pools":
        pools = resp.get("pools", [])
        lines = ["Resource Pools:"]
        for p in pools:
            lines.append(f"  {p['name']}: memory={p['memory']}, cpu={p['cpu']}, members={p['members']}")
        if not pools:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "register-gpu":
        return f"GPU '{resp.get('device_id', '?')}' registered"
    if command == "assign-gpu":
        return f"GPU '{resp.get('device_id', '?')}' assigned to container {resp.get('container_id', '?')[:12]}"
    if command == "release-gpu":
        return f"GPU '{resp.get('device_id', '?')}' released"
    if command == "gpu-status":
        gpus = resp.get("gpus", [])
        lines = ["GPU Devices:"]
        for g in gpus:
            status = "allocated" if g["allocated"] else "free"
            lines.append(f"  {g['device_id']} ({g['type']}): {g['memory_mb']}MB, {status}")
        if not gpus:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "evaluate-policy":
        result = resp.get("result", "?")
        lines = [f"Policy '{resp.get('policy', '?')}': {result.upper()}"]
        for v in resp.get("violations", []):
            lines.append(f"  VIOLATION: {v['field']} {v['operator']} {v['expected']} (actual={v['actual']})")
        return "\n".join(lines)
    if command == "list-policies":
        policies = resp.get("policies", [])
        lines = ["Policies:"]
        for p in policies:
            lines.append(f"  {p['name']} ({p['effect']}): {p['rules']} rules, {p['evaluations']} evals, {p['violations']} violations")
        if not policies:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "policy-violations":
        return f"Policy '{resp.get('name', '?')}': {resp.get('violations', 0)} violations in {resp.get('evaluations', 0)} evaluations (rate: {resp.get('violation_rate', 0):.1%})"
    if command == "composition-status":
        lines = [
            f"Composition '{resp.get('name', '?')}' [{resp.get('status', '?')}]:",
            f"  containers: {resp.get('containers', 0)}, sidecars: {resp.get('sidecars', 0)}, init: {resp.get('init_containers', 0)}",
        ]
        return "\n".join(lines)
    if command == "list-compositions":
        comps = resp.get("compositions", [])
        lines = ["Compositions:"]
        for c in comps:
            lines.append(f"  {c['name']} [{c['status']}]: {c['containers']} containers, {c['sidecars']} sidecars")
        if not comps:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "start-composition":
        return f"Composition '{resp.get('name', '?')}' started ({resp.get('total', 0)} containers)"
    if command == "stop-composition":
        return f"Composition '{resp.get('name', '?')}' stopped"
    if command == "create-workload-identity":
        return f"Identity '{resp.get('name', '?')}' created (SPIFFE: {resp.get('spiffe_id', '?')})"
    if command == "validate-workload-identity":
        valid = resp.get("valid", False)
        status = "VALID" if valid else f"INVALID ({resp.get('error', '?')})"
        lines = [f"Identity '{resp.get('name', '?')}': {status}"]
        if valid:
            lines.append(f"  spiffe_id: {resp.get('spiffe_id', '?')}")
            lines.append(f"  remaining: {resp.get('remaining_seconds', 0):.0f}s")
        return "\n".join(lines)
    if command == "rotate-workload-identity":
        return f"Identity '{resp.get('name', '?')}' rotated (#{resp.get('rotations', 0)})"
    if command == "revoke-workload-identity":
        return f"Identity '{resp.get('name', '?')}' revoked"
    if command == "list-workload-identities":
        ids = resp.get("identities", [])
        lines = ["Workload Identities:"]
        for i in ids:
            status = "revoked" if i["revoked"] else ("expired" if i["expired"] else "active")
            lines.append(f"  {i['name']}: {i['spiffe_id']} [{status}] (rotations: {i['rotations']})")
        if not ids:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "scan-image":
        lines = [f"Scan: {resp.get('image_ref', '?')}",
                 f"  total: {resp.get('total_vulns', 0)}, critical: {resp.get('critical', 0)}, high: {resp.get('high', 0)}"]
        return "\n".join(lines)
    if command == "evaluate-vuln-policy":
        status = "BLOCKED" if resp.get("blocked") else "PASSED"
        lines = [f"Vuln policy '{resp.get('policy', '?')}': {status}"]
        for r in resp.get("reasons", []):
            lines.append(f"  REASON: {r}")
        return "\n".join(lines)
    if command == "scan-history":
        scans = resp.get("scans", [])
        lines = ["Scan History:"]
        for s in scans:
            lines.append(f"  {s['image_ref']}: {s['total_vulns']} vulns ({s['critical']} critical)")
        if not scans:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "register-ebpf-hook":
        return f"eBPF hook '{resp.get('name', '?')}' registered as {resp.get('type', '?')}"
    if command == "attach-ebpf-hook":
        return f"eBPF hook '{resp.get('name', '?')}' attached"
    if command == "ebpf-metrics":
        metrics = resp.get("metrics", {})
        if "metric" in resp:
            return f"Metric '{resp['metric']}': count={resp['count']}, total={resp['total']:.2f}, avg={resp['avg']:.2f}"
        lines = ["eBPF Metrics:"]
        for m, info in metrics.items():
            lines.append(f"  {m}: count={info['count']}, total={info['total']:.2f}")
        if not metrics:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "list-ebpf-hooks":
        hooks = resp.get("hooks", [])
        lines = ["eBPF Hooks:"]
        for h in hooks:
            status = "attached" if h["attached"] else "detached"
            lines.append(f"  {h['name']} ({h['type']}): {status}, {h['events_captured']} events")
        if not hooks:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "register-topology-node":
        return f"Node '{resp.get('node_id', '?')}' registered in {resp.get('region', '?')}/{resp.get('zone', '?')}"
    if command == "schedule-with-topology":
        if resp.get("scheduled"):
            return f"Scheduled on {resp.get('node', '?')} ({resp.get('region', '?')}/{resp.get('zone', '?')})"
        return f"Scheduling failed: {resp.get('error', '?')}"
    if command == "topology-view":
        regions = resp.get("regions", {})
        lines = [f"Topology ({resp.get('total_nodes', 0)} nodes):"]
        for r, zones in regions.items():
            lines.append(f"  {r}:")
            for z, nodes in zones.items():
                lines.append(f"    {z}: {', '.join(nodes)}")
        if not regions:
            lines.append("  (empty)")
        return "\n".join(lines)
    if command == "create-chaos-schedule":
        return f"Chaos schedule '{resp.get('name', '?')}' created with faults: {', '.join(resp.get('fault_types', []))}"
    if command == "execute-chaos-attack":
        if resp.get("dry_run"):
            return f"Dry run: would affect {resp.get('would_affect', [])}"
        return f"Chaos attack: {resp.get('affected', 0)} targets affected"
    if command == "chaos-schedule-status":
        s = resp
        status = "enabled" if s.get('enabled') else "disabled"
        lines = [f"Chaos Schedule '{s.get('name', '?')}' [{status}]:"]
        lines.append(f"  faults: {', '.join(s.get('fault_types', []))}")
        lines.append(f"  cron: {s.get('schedule_cron', '?')}, executions: {s.get('executions', 0)}")
        return "\n".join(lines)
    if command == "list-chaos-schedules":
        schedules = resp.get("schedules", [])
        lines = ["Chaos Schedules:"]
        for s in schedules:
            lines.append(f"  {s['name']}: {', '.join(s['fault_types'])}, {s['executions']} runs")
        if not schedules:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "register-multiarch-image":
        return f"Image '{resp.get('name', '?')}' registered with {resp.get('arch_count', 0)} architectures"
    if command == "resolve-image-arch":
        if "error" in resp:
            return f"Error: {resp['error']}"
        return f"Resolved: {resp.get('image', '?')} -> {resp.get('resolved_arch', '?')} ({resp.get('size_bytes', 0)} bytes)"
    if command == "list-multiarch-images":
        images = resp.get("images", [])
        lines = ["Multi-Arch Images:"]
        for img in images:
            lines.append(f"  {img['name']}: {', '.join(img['architectures'])}")
        if not images:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "build-matrix":
        matrix = resp.get("matrix", [])
        lines = [f"Build Matrix ({resp.get('count', 0)} variants):"]
        for m in matrix:
            lines.append(f"  {m['platform']}: goarch={m.get('goarch', '?')}, size={m.get('size_bytes', 0)}")
        return "\n".join(lines)
    if command == "register-runtime-class":
        return f"Runtime class '{resp.get('name', '?')}' registered (handler: {resp.get('runtime_handler', '?')})"
    if command == "assign-runtime-class":
        return f"Runtime class '{resp.get('runtime_class', '?')}' assigned to {resp.get('container_id', '?')[:12]}"
    if command == "list-runtime-classes":
        classes = resp.get("classes", [])
        lines = ["Runtime Classes:"]
        for rc in classes:
            lines.append(f"  {rc['name']}: handler={rc['handler']}, immutable={rc['immutable_rootfs']}, assigned={rc['assigned']}")
        if not classes:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "create-replay-trace":
        return f"Replay trace '{resp.get('trace_name', '?')}' created for {resp.get('container_id', '?')[:12]}"
    if command == "record-replay-event":
        return f"Recorded event #{resp.get('event_count', 0)} in trace '{resp.get('trace_name', '?')}'"
    if command == "stop-replay-trace":
        return f"Trace '{resp.get('trace_name', '?')}' stopped: {resp.get('events', 0)} events, {resp.get('snapshots', 0)} snapshots"
    if command == "replay-trace":
        return f"Replay plan: {resp.get('replay_events', 0)} of {resp.get('total_events', 0)} events"
    if command == "list-replay-traces":
        traces = resp.get("traces", [])
        lines = ["Replay Traces:"]
        for t in traces:
            lines.append(f"  {t['name']}: {t['events']} events, {t['snapshots']} snapshots [{t['status']}]")
        if not traces:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "cost-trends":
        lines = [f"Cost Trends ({resp.get('lookback_days', 0)} days):", f"  avg daily: ${resp.get('avg_daily_cost', 0):.2f}, trend: {resp.get('trend', '?')}", f"  projected monthly: ${resp.get('projected_monthly', 0):.2f}"]
        return "\n".join(lines)
    if command == "cost-recommendations":
        recs = resp.get("recommendations", [])
        lines = [f"Cost Recommendations ({len(recs)} found, {resp.get('total_potential_savings_pct', 0):.1f}% savings):"]
        for r in recs:
            lines.append(f"  [{r.get('type', '?')}] {r.get('recommended', r.get('suggestion', '?'))}")
        if not recs:
            lines.append("  No recommendations")
        return "\n".join(lines)
    if command == "fleet-cost-report":
        lines = [f"Fleet Cost Report: {resp.get('containers_analyzed', 0)} containers, {resp.get('total_recommendations', 0)} recs, avg {resp.get('avg_savings_pct', 0):.1f}% savings"]
        return "\n".join(lines)
    if command == "forecast-cost":
        lines = ["Cost Forecast:", f"  daily: ${resp.get('daily_cost', 0):.2f}", f"  current monthly: ${resp.get('current_monthly', 0):.2f}", f"  projected monthly: ${resp.get('projected_monthly', 0):.2f} ({resp.get('change_pct', 0):+.1f}%)"]
        return "\n".join(lines)
    if command == "create-affinity-rule":
        return f"Affinity rule '{resp.get('name', '?')}' created ({resp.get('type', '?')})"
    if command == "evaluate-affinity":
        schedulable = "SCHEDULABLE" if resp.get("schedulable") else "NOT SCHEDULABLE"
        lines = [f"Affinity result [{schedulable}] (score={resp.get('score', 0)}):", f"  matched: {resp.get('matched', [])}"]
        for r in resp.get("rejected", []):
            lines.append(f"  REJECTED: {r.get('rule', '?')} - {r.get('reason', '?')}")
        return "\n".join(lines)
    if command == "create-priority-class":
        return f"Priority class '{resp.get('name', '?')}' created (value={resp.get('value', 0)})"
    if command == "evaluate-preemption":
        can = "CAN preempt" if resp.get("preempt") else "CANNOT preempt"
        return f"Preemption: {can} (evictor={resp.get('evictor_priority', 0)}, victim={resp.get('victim_priority', 0)}): {resp.get('reason', '?')}"
    if command == "list-priority-classes":
        classes = resp.get("classes", [])
        lines = ["Priority Classes:"]
        for pc in classes:
            lines.append(f"  {pc['name']}: value={pc['value']}, policy={pc['preemption_policy']}")
        if not classes:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "configure-autoscaler":
        return f"Autoscaler configured for {resp.get('container_id', '?')[:12]} (predictive={resp.get('predictive', False)})"
    if command == "evaluate-autoscaling":
        action = resp.get("action", "none")
        return f"Autoscaling: {action} (current={resp.get('current_replicas', 0)}, desired={resp.get('desired_replicas', 0)}, cpu={resp.get('cpu_pct', 0):.1f}%)"
    if command == "autoscaler-status":
        s = resp
        lines = [f"Autoscaler for {s.get('container_id', '?')[:12]}:", f"  replicas: {s.get('current', 0)} ({s.get('min', 0)}-{s.get('max', 0)})", f"  targets: cpu={s.get('target_cpu', 0)}%, memory={s.get('target_memory', 0)}%", f"  predictive: {s.get('predictive', False)}, events: {s.get('total_scaling_events', 0)}"]
        return "\n".join(lines)
    if command == "list-autoscalers":
        autos = resp.get("autoscalers", [])
        lines = ["Autoscalers:"]
        for a in autos:
            lines.append(f"  {a['container_id'][:12]}: {a['current']} replicas ({a['min']}-{a['max']}), events={a['events']}")
        if not autos:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "sign-image":
        return f"Image '{resp.get('image_ref', '?')}' signed (sig: {resp.get('signature_id', '?')})"
    if command == "verify-image-signature":
        status = "VERIFIED" if resp.get("verified") else "FAILED"
        return f"Image '{resp.get('image_ref', '?')}' signature: {status}"
    if command == "list-signed-images":
        images = resp.get("images", [])
        lines = ["Signed Images:"]
        for img in images:
            lines.append(f"  {img['image_ref']}: sig={img['signature_id']}, key={img['key_ref']}")
        if not images:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "configure-otel":
        return f"OTel exporter configured: {resp.get('endpoint', '?')} ({resp.get('protocol', '?')})"
    if command == "record-otel-metric":
        return f"Recorded {resp.get('metric', '?')} (buffer: {resp.get('buffer_size', 0)})"
    if command == "otel-status":
        lines = ["OpenTelemetry:", f"  enabled: {resp.get('enabled', False)}", f"  endpoint: {resp.get('endpoint', '?')}", f"  exported: {resp.get('metrics_exported', 0)}, buffer: {resp.get('buffer_size', 0)}"]
        return "\n".join(lines)
    if command == "register-fed-cluster":
        return f"Cluster '{resp.get('cluster_id', '?')}' registered in {resp.get('region', '?')}"
    if command == "global-schedule":
        if resp.get("scheduled"):
            return f"Workload '{resp.get('workload', '?')}' scheduled on {resp.get('cluster', '?')} ({resp.get('region', '?')})"
        return f"Scheduling failed: {resp.get('error', '?')}"
    if command == "federation-status":
        return f"Federation: {resp.get('clusters', 0)} clusters ({resp.get('healthy', 0)} healthy) in {len(resp.get('regions', []))} regions"
    if command == "list-fed-clusters":
        clusters = resp.get("clusters", [])
        lines = ["Federation Clusters:"]
        for c in clusters:
            status = "healthy" if c["healthy"] else "unhealthy"
            lines.append(f"  {c['cluster_id']}: {c['region']}/{c['zone']} [{status}] cpu={c['cpu_capacity']} workloads={c['workloads']}")
        if not clusters:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "fed-workloads":
        workloads = resp.get("workloads", [])
        lines = ["Federated Workloads:"]
        for w in workloads:
            lines.append(f"  {w['workload']}: cluster={w['cluster']}, region={w['region']}, cpu={w['cpu']}")
        if not workloads:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "create-batch-job":
        return f"Batch job '{resp.get('name', '?')}' created with {resp.get('task_count', 0)} tasks"
    if command == "execute-batch-job":
        return f"Job '{resp.get('job', '?')}': executed {resp.get('executed', 0)} tasks, status={resp.get('status', '?')}"
    if command == "batch-job-status":
        s = resp
        return f"Job '{s.get('name', '?')}': {s.get('completed', 0)}/{s.get('total', 0)} completed, {s.get('failed', 0)} failed [{s.get('status', '?')}]"
    if command == "list-batch-jobs":
        jobs = resp.get("jobs", [])
        lines = ["Batch Jobs:"]
        for j in jobs:
            lines.append(f"  {j['name']}: {j['tasks']} tasks [{j['status']}]")
        if not jobs:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "run-memory-compaction":
        return f"Compacted {resp.get('compacted_bytes', 0)} bytes, score={resp.get('fragmentation_score', 0):.1f}"
    if command == "run-memory-defragmentation":
        return f"Defragmented {resp.get('defragmented_bytes', 0)} bytes, score={resp.get('fragmentation_score', 0):.1f}"
    if command == "fs-snapshot":
        return f"Snapshot '{resp.get('snapshot_name', '?')}' created for {resp.get('container_id', '?')[:12]}"
    if command == "fs-restore":
        return f"Snapshot '{resp.get('snapshot_name', '?')}' restored to {resp.get('target_container_id', '?')[:12]} (restore #{resp.get('restore_count', 0)})"
    if command == "list-fs-snapshots":
        snaps = resp.get("snapshots", [])
        lines = ["Filesystem Snapshots:"]
        for s in snaps:
            lines.append(f"  {s['name']}: container={s['container_id'][:12]}, status={s['status']}, restores={s['restores']}")
        if not snaps:
            lines.append("  (none)")
        return "\n".join(lines)
    if command == "compare-fs-snapshots":
        return f"Snapshots: {resp.get('snapshot_a', '?')} vs {resp.get('snapshot_b', '?')} (same_container={resp.get('same_container', False)})"
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

    blr = csub.add_parser("baseline-record",
                          help="Record a baseline snapshot")
    blr.add_argument("container_id")
    blr.set_defaults(command="baseline-record")

    blg = csub.add_parser("baseline-get",
                          help="Get aggregated baseline stats")
    blg.add_argument("container_id")
    blg.set_defaults(command="baseline-get")

    blc = csub.add_parser("baseline-compare",
                          help="Compare current usage vs baseline")
    blc.add_argument("container_id")
    blc.add_argument("--threshold", type=float, default=2.0,
                     help="Z-score threshold (default 2.0)")
    blc.set_defaults(command="baseline-compare")

    blx = csub.add_parser("baseline-clear",
                          help="Clear all baseline snapshots")
    blx.add_argument("container_id")
    blx.set_defaults(command="baseline-clear")

    pk = csub.add_parser("process-kill",
                         help="Send signal to a process")
    pk.add_argument("container_id")
    pk.add_argument("pid", type=int, help="PID to signal")
    pk.add_argument("--signal", type=int, default=15,
                    help="Signal number (default SIGTERM=15)")
    pk.set_defaults(command="process-kill")

    pl = csub.add_parser("process-list",
                         help="List processes in a container")
    pl.add_argument("container_id")
    pl.set_defaults(command="process-list")

    ps = csub.add_parser("process-signal-all",
                         help="Signal all processes in a container")
    ps.add_argument("container_id")
    ps.add_argument("--signal", type=int, default=15,
                    help="Signal number (default SIGTERM=15)")
    ps.set_defaults(command="process-signal-all")

    sss = csub.add_parser("snapshot-schedule-set",
                          help="Configure snapshot schedule")
    sss.add_argument("container_id")
    sss.add_argument("--interval", type=float, default=3600.0,
                     help="Seconds between snapshots")
    sss.add_argument("--max-snapshots", type=int, default=10,
                     help="Max snapshots to retain")
    sss.set_defaults(command="snapshot-schedule-set")

    ssg = csub.add_parser("snapshot-schedule-get",
                          help="Get snapshot schedule")
    ssg.add_argument("container_id")
    ssg.set_defaults(command="snapshot-schedule-get")

    ssd = csub.add_parser("snapshot-schedule-disable",
                          help="Disable snapshot schedule")
    ssd.add_argument("container_id")
    ssd.set_defaults(command="snapshot-schedule-disable")

    ssr = csub.add_parser("snapshot-schedule-run",
                          help="Run scheduled snapshot now")
    ssr.add_argument("container_id")
    ssr.set_defaults(command="snapshot-schedule-run")

    ssl = csub.add_parser("snapshot-schedule-list",
                          help="List scheduled snapshots")
    ssl.add_argument("container_id")
    ssl.set_defaults(command="snapshot-schedule-list")

    dh = csub.add_parser("dependency-health",
                         help="Check health of dependencies")
    dh.add_argument("container_id")
    dh.set_defaults(command="dependency-health")

    dhr = csub.add_parser("dependency-health-reverse",
                          help="Check dependents of a container")
    dhr.add_argument("container_id")
    dhr.set_defaults(command="dependency-health-reverse")

    ur = sub.add_parser("usage-report",
                       help="Generate resource usage report")
    ur.add_argument("--container-ids", default=None,
                    help="Comma-separated container IDs (default: all)")
    ur.set_defaults(command="usage-report")

    asum = sub.add_parser("alert-summary",
                         help="Summary of all active alerts")
    asum.set_defaults(command="alert-summary")

    scw = csub.add_parser("set-cpu-weight",
                          help="Set CPU weight (cgroups v2)")
    scw.add_argument("container_id")
    scw.add_argument("weight", type=int,
                     help="CPU weight 1-10000 (default 100)")
    scw.set_defaults(command="set-cpu-weight")

    siw = csub.add_parser("set-io-weight",
                          help="Set I/O weight (cgroups v2)")
    siw.add_argument("container_id")
    siw.add_argument("weight", type=int,
                     help="I/O weight 1-100 (default 100)")
    siw.set_defaults(command="set-io-weight")

    gp = csub.add_parser("get-priority",
                         help="Get all priority parameters")
    gp.add_argument("container_id")
    gp.set_defaults(command="get-priority")

    ec = sub.add_parser("event-correlate",
                       help="Correlate events across containers")
    ec.add_argument("--window", type=float, default=60.0,
                    help="Time window in seconds")
    ec.add_argument("--kinds", default=None,
                    help="Comma-separated event kinds to filter")
    ec.set_defaults(command="event-correlate")

    et = sub.add_parser("event-timeline",
                       help="Merged event timeline across containers")
    et.add_argument("--container-ids", default=None,
                    help="Comma-separated container IDs")
    et.add_argument("--window", type=float, default=300.0,
                    help="Time window in seconds")
    et.set_defaults(command="event-timeline")

    nra = csub.add_parser("network-rule-add",
                         help="Add a network policy rule")
    nra.add_argument("container_id")
    nra.add_argument("direction", choices=["ingress", "egress"])
    nra.add_argument("--protocol", default="tcp",
                     choices=["tcp", "udp", "icmp"])
    nra.add_argument("--port", type=int, default=None)
    nra.add_argument("--source", default=None,
                     help="Source CIDR or IP")
    nra.add_argument("--action", default="allow",
                     choices=["allow", "deny"])
    nra.set_defaults(command="network-rule-add")

    nrr = csub.add_parser("network-rule-remove",
                         help="Remove a network policy rule")
    nrr.add_argument("container_id")
    nrr.add_argument("rule_index", type=int)
    nrr.set_defaults(command="network-rule-remove")

    nrl = csub.add_parser("network-rules-list",
                         help="List network policy rules")
    nrl.add_argument("container_id")
    nrl.set_defaults(command="network-rules-list")

    nrc = csub.add_parser("network-rules-clear",
                         help="Clear all network policy rules")
    nrc.add_argument("container_id")
    nrc.set_defaults(command="network-rules-clear")

    cc = sub.add_parser("compare-containers",
                       help="Compare resource usage across containers")
    cc.add_argument("container_ids",
                    help="Comma-separated container IDs (min 2)")
    cc.add_argument("--metrics", default=None,
                    help="Comma-separated metrics to compare")
    cc.set_defaults(command="compare-containers")

    ct = sub.add_parser("check-thresholds",
                       help="Check resource thresholds and fire alerts")
    ct.set_defaults(command="check-thresholds")

    ts = sub.add_parser("threshold-status",
                       help="Show threshold status for all containers")
    ts.set_defaults(command="threshold-status")

    ssp = csub.add_parser("set-scheduling-priority",
                         help="Set scheduling priority")
    ssp.add_argument("container_id")
    ssp.add_argument("priority", type=int,
                     help="Priority 0-99 (0=highest)")
    ssp.set_defaults(command="set-scheduling-priority")

    sq = sub.add_parser("scheduling-queue",
                       help="Show scheduling queue")
    sq.set_defaults(command="scheduling-queue")

    rc = sub.add_parser("ready-containers",
                       help="Show containers ready to start")
    rc.set_defaults(command="ready-containers")

    # -- audit & cost allocation --
    ar = sub.add_parser("audit-record",
                        help="Record an audit entry")
    ar.add_argument("container_id")
    ar.add_argument("action")
    ar.add_argument("--actor", default="operator")
    ar.add_argument("--resource", default=None)
    ar.add_argument("--old-value", default=None)
    ar.add_argument("--new-value", default=None)
    ar.add_argument("--detail", default="")
    ar.set_defaults(command="audit-record")

    al = sub.add_parser("audit-log",
                        help="Show the audit log for a container")
    al.add_argument("container_id")
    al.add_argument("--tail", type=int, default=None)
    al.add_argument("--action", default=None)
    al.add_argument("--actor", default=None)
    al.add_argument("--resource", default=None)
    al.set_defaults(command="audit-log")

    as_ = sub.add_parser("audit-summary",
                         help="Audit activity summary")
    as_.add_argument("container_id")
    as_.set_defaults(command="audit-summary")

    ca = sub.add_parser("cost-allocate",
                        help="Cost allocation for a container")
    ca.add_argument("container_id")
    ca.set_defaults(command="cost-allocate")

    caa = sub.add_parser("cost-allocate-all",
                         help="Cost allocation for all containers")
    caa.set_defaults(command="cost-allocate-all")

    # -- budget tracking --
    bs = sub.add_parser("budget-set",
                        help="Set a resource budget for a container")
    bs.add_argument("container_id")
    bs.add_argument("--memory-mb", type=int, default=None,
                    help="Memory budget in MB")
    bs.add_argument("--cpu-pct", type=float, default=None,
                    help="CPU percentage budget (0-100)")
    bs.add_argument("--pids", type=int, default=None,
                    help="PID count budget")
    bs.add_argument("--daily-cost-limit", type=float, default=None,
                    help="Daily cost limit in dollars")
    bs.add_argument("--monthly-cost-limit", type=float, default=None,
                    help="Monthly cost limit in dollars")
    bs.add_argument("--alert-at-pct", type=float, default=80.0,
                    help="Warning threshold percentage (default: 80)")
    bs.set_defaults(command="budget-set")

    bg = sub.add_parser("budget-get",
                        help="Show the budget for a container")
    bg.add_argument("container_id")
    bg.set_defaults(command="budget-get")

    bc = sub.add_parser("budget-check",
                        help="Check a container against its budget")
    bc.add_argument("container_id")
    bc.set_defaults(command="budget-check")

    bca = sub.add_parser("budget-check-all",
                         help="Check all budgets")
    bca.set_defaults(command="budget-check-all")

    bcr = sub.add_parser("budget-clear",
                         help="Clear a container's budget")
    bcr.add_argument("container_id")
    bcr.set_defaults(command="budget-clear")

    # -- auto-remediation --
    rc = sub.add_parser("remediation-configure",
                        help="Configure auto-remediation policies")
    rc.add_argument("container_id")
    rc.add_argument("--on-budget-exceeded", default="alert",
                    choices=["alert", "restart", "scale_up",
                             "scale_down", "throttle", "none"],
                    help="Action on budget exceeded (default: alert)")
    rc.add_argument("--on-threshold-exceeded", default="alert",
                    choices=["alert", "restart", "scale_up",
                             "scale_down", "throttle", "none"],
                    help="Action on threshold exceeded")
    rc.add_argument("--on-oom-risk", default="alert",
                    choices=["alert", "restart", "scale_up",
                             "scale_down", "throttle", "none"],
                    help="Action on OOM risk")
    rc.add_argument("--max-restarts", type=int, default=3,
                    help="Max restarts in cooldown window (default: 3)")
    rc.add_argument("--cooldown-seconds", type=float, default=300.0,
                    help="Cooldown between actions in seconds (default: 300)")
    rc.add_argument("--enabled", action="store_true", default=True,
                    help="Enable remediation (default: True)")
    rc.add_argument("--disabled", dest="enabled", action="store_false",
                    help="Disable remediation")
    rc.set_defaults(command="remediation-configure")

    re = sub.add_parser("remediation-execute",
                        help="Execute a remediation action now")
    re.add_argument("container_id")
    re.add_argument("trigger",
                    choices=["budget_exceeded", "threshold_exceeded",
                             "oom_risk"],
                    help="Trigger type")
    re.add_argument("--reason", default="",
                    help="Reason for remediation")
    re.set_defaults(command="remediation-execute")

    rs = sub.add_parser("remediation-status",
                        help="Show remediation status")
    rs.add_argument("container_id")
    rs.set_defaults(command="remediation-status")

    rh = sub.add_parser("remediation-history",
                        help="Show remediation history")
    rh.add_argument("container_id")
    rh.add_argument("--tail", type=int, default=None,
                    help="Show only last N entries")
    rh.add_argument("--trigger", default=None,
                    help="Filter by trigger type")
    rh.add_argument("--action", default=None,
                    help="Filter by action taken")
    rh.set_defaults(command="remediation-history")

    # -- multi-tenant fair-share enforcement --
    tcs = sub.add_parser("tenant-config-set",
                         help="Configure tenant parameters")
    tcs.add_argument("owner")
    tcs.add_argument("--priority", type=int, default=0,
                     help="Tenant priority (higher = more important)")
    tcs.add_argument("--weight", type=float, default=1.0,
                     help="Fair-share weight")
    tcs.add_argument("--burstable-pct", type=float, default=20.0,
                     help="Burst percentage above share (default: 20)")
    tcs.add_argument("--enforce", action="store_true", default=True,
                     help="Enable enforcement (default: True)")
    tcs.add_argument("--no-enforce", dest="enforce",
                     action="store_false",
                     help="Disable enforcement")
    tcs.add_argument("--eviction-policy", default="alert",
                     choices=["lowest_priority", "throttle",
                              "alert", "none"],
                     help="Eviction policy (default: alert)")
    tcs.set_defaults(command="tenant-config-set")

    tcg = sub.add_parser("tenant-config-get",
                         help="Get tenant configuration")
    tcg.add_argument("owner")
    tcg.set_defaults(command="tenant-config-get")

    tcl = sub.add_parser("tenant-config-list",
                         help="List all tenant configs")
    tcl.set_defaults(command="tenant-config-list")

    fs = sub.add_parser("fair-share",
                        help="Calculate fair-share allocation")
    fs.add_argument("--resource", default="memory_mb",
                    choices=["memory_mb", "pid_limit"],
                    help="Resource to calculate (default: memory_mb)")
    fs.set_defaults(command="fair-share")

    te = sub.add_parser("tenant-enforce",
                        help="Check and enforce tenant quotas")
    te.set_defaults(command="tenant-enforce")

    tus = sub.add_parser("tenant-usage-summary",
                         help="Tenant usage summary")
    tus.set_defaults(command="tenant-usage-summary")

    # -- event log export / import --
    elo = sub.add_parser("event-log-export",
                        help="Export event log for disaster recovery")
    elo.add_argument("container_id", nargs='?', default=None,
                     help="Export only this container (default: all)")
    elo.add_argument("--no-audit", dest="include_audit",
                     action="store_false", default=True,
                     help="Exclude audit log entries")
    elo.add_argument("--no-oom", dest="include_oom",
                     action="store_false", default=True,
                     help="Exclude OOM event entries")
    elo.add_argument("--no-sla", dest="include_sla",
                     action="store_false", default=True,
                     help="Exclude SLA violation entries")
    elo.add_argument("--since", type=float, default=None,
                     help="Only events after this Unix timestamp")
    elo.add_argument("--until", type=float, default=None,
                     help="Only events before this Unix timestamp")
    elo.add_argument("--output", default=None,
                     help="Output file (default: stdout)")
    elo.set_defaults(command="event-log-export")

    eli = sub.add_parser("event-log-import",
                        help="Import event log from file or stdin")
    eli.add_argument("container_id", nargs='?', default=None,
                     help="Import only for this container")
    eli.add_argument("data_file", nargs='?', default=None,
                     help="JSON file to import (default: stdin)")
    eli.set_defaults(command="event-log-import")

    # -- health scoring --
    hs = sub.add_parser("health-score",
                        help="Calculate health score for a container")
    hs.add_argument("container_id")
    hs.set_defaults(command="health-score")

    hsa = sub.add_parser("health-score-all",
                         help="Calculate health scores for all containers")
    hsa.set_defaults(command="health-score-all")

    # -- event log compression --
    elc = sub.add_parser("event-log-compress",
                        help="Compress event log for archival")
    elc.add_argument("data_file", nargs='?', default=None,
                     help="JSON file to compress (default: stdin)")
    elc.add_argument("--keep-recent", type=int, default=100,
                     help="Number of recent events to keep (default: 100)")
    elc.add_argument("--no-summarize", dest="summarize_older",
                     action="store_false", default=True,
                     help="Don't summarize older events")
    elc.add_argument("--output", default=None,
                     help="Output file (default: stdout)")
    elc.set_defaults(command="event-log-compress")

    # -- archive scheduling --
    ass = sub.add_parser("archive-schedule-set",
                        help="Configure automatic archival schedule")
    ass.add_argument("--enabled", action="store_true", default=True,
                     help="Enable scheduling (default: True)")
    ass.add_argument("--disabled", dest="enabled",
                     action="store_false",
                     help="Disable scheduling")
    ass.add_argument("--interval-s", type=float, default=86400.0,
                     help="Seconds between archives (default: 86400)")
    ass.add_argument("--keep-recent", type=int, default=500,
                     help="Recent events to keep uncompressed (default: 500)")
    ass.add_argument("--no-auto-compress", dest="auto_compress",
                     action="store_false", default=True,
                     help="Don't auto-compress during archival")
    ass.add_argument("--max-archives", type=int, default=30,
                     help="Maximum archives to retain (default: 30)")
    ass.set_defaults(command="archive-schedule-set")

    asg = sub.add_parser("archive-schedule-get",
                         help="Show archive schedule")
    asg.set_defaults(command="archive-schedule-get")

    asd = sub.add_parser("archive-schedule-disable",
                         help="Disable archive scheduling")
    asd.set_defaults(command="archive-schedule-disable")

    arn = sub.add_parser("archive-run-now",
                         help="Run archive immediately")
    arn.set_defaults(command="archive-run-now")

    al = sub.add_parser("archive-list",
                        help="List stored archives")
    al.add_argument("--tail", type=int, default=None,
                    help="Show only last N archives")
    al.set_defaults(command="archive-list")

    ag = sub.add_parser("archive-get",
                        help="Get a specific archive")
    ag.add_argument("index", type=int, nargs='?', default=0,
                    help="Archive index (0 = most recent)")
    ag.set_defaults(command="archive-get")

    # -- SLA breach auto-remediation --
    sbp = sub.add_parser("sla-breach-process",
                        help="Process an SLA breach with auto-remediation")
    sbp.add_argument("container_id")
    sbp.add_argument("--breach-type", default="downtime",
                     choices=["downtime", "latency", "error_rate",
                              "budget", "oom", "custom"],
                     help="Type of breach (default: downtime)")
    sbp.add_argument("--detail", default="",
                     help="Human-readable detail")
    sbp.set_defaults(command="sla-breach-process")

    sbpa = sub.add_parser("sla-breach-process-all",
                          help="Process SLA breaches across containers")
    sbpa.add_argument("--breach-type", default="downtime",
                      help="Type of breach")
    sbpa.add_argument("--detail", default="",
                      help="Human-readable detail")
    sbpa.add_argument("--container-ids", nargs='+', default=None,
                      help="Specific container IDs (default: all with SLA)")
    sbpa.set_defaults(command="sla-breach-process-all")

    # -- smart remediation --
    sr = sub.add_parser("smart-remediate",
                        help="Evaluate and auto-remediate a container")
    sr.add_argument("container_id")
    sr.set_defaults(command="smart-remediate")

    sra = sub.add_parser("smart-remediate-all",
                         help="Evaluate and remediate all containers")
    sra.set_defaults(command="smart-remediate-all")

    # -- usage pattern recognition --
    up = sub.add_parser("usage-patterns",
                        help="Detect resource usage patterns")
    up.add_argument("container_id")
    up.add_argument("--window-size", type=int, default=30,
                    help="Number of samples to analyze (default: 30)")
    up.set_defaults(command="usage-patterns")

    oa = sub.add_parser("optimization-actions",
                        help="Get optimization recommendations")
    oa.add_argument("container_id")
    oa.set_defaults(command="optimization-actions")

    # -- right-sizing --
    rs = sub.add_parser("rightsize",
                        help="Right-size a container's resource limits")
    rs.add_argument("container_id")
    rs.add_argument("--safety-margin", type=float, default=20.0,
                    dest='safety_margin_pct',
                    help="Safety margin %% (default: 20)")
    rs.add_argument("--dry-run", action="store_true", default=False,
                    help="Report changes without applying")
    rs.set_defaults(command="rightsize")

    rsa = sub.add_parser("rightsize-all",
                         help="Right-size all running containers")
    rsa.add_argument("--safety-margin", type=float, default=20.0,
                     dest='safety_margin_pct',
                     help="Safety margin %% (default: 20)")
    rsa.add_argument("--dry-run", action="store_true", default=False,
                     help="Report changes without applying")
    rsa.set_defaults(command="rightsize-all")

    # -- SLA compliance monitoring --
    scs = sub.add_parser("sla-compliance-set",
                        help="Set SLA compliance rules")
    scs.add_argument("container_id")
    scs.add_argument("--max-memory-pct", type=float, default=90.0,
                     help="Max memory usage %% (default: 90)")
    scs.add_argument("--max-pid-pct", type=float, default=80.0,
                     help="Max PID usage %% (default: 80)")
    scs.add_argument("--max-daily-cost", type=float, default=None,
                     help="Max daily cost in dollars")
    scs.add_argument("--max-anomalies", type=int, default=5,
                     dest='max_consecutive_anomalies',
                     help="Max consecutive anomalies (default: 5)")
    scs.add_argument("--auto-action", default="alert",
                     choices=["alert", "remediate", "escalate"],
                     help="Auto action on violation (default: alert)")
    scs.add_argument("--enabled", action="store_true", default=True,
                     help="Enable monitoring (default: True)")
    scs.add_argument("--disabled", dest="enabled",
                     action="store_false",
                     help="Disable monitoring")
    scs.set_defaults(command="sla-compliance-set")

    scg = sub.add_parser("sla-compliance-get",
                         help="Get SLA compliance rules")
    scg.add_argument("container_id")
    scg.set_defaults(command="sla-compliance-get")

    scc = sub.add_parser("sla-compliance-check",
                         help="Check SLA compliance for a container")
    scc.add_argument("container_id")
    scc.set_defaults(command="sla-compliance-check")

    scca = sub.add_parser("sla-compliance-check-all",
                          help="Check SLA compliance for all containers")
    scca.set_defaults(command="sla-compliance-check-all")

    # -- visualization dashboard --
    vd = sub.add_parser("viz-data",
                        help="Get visualization data for a container")
    vd.add_argument("container_id")
    vd.add_argument("--time-range", type=float, default=3600.0,
                    dest='time_range_s',
                    help="Time range in seconds (default: 3600)")
    vd.add_argument("--resolution", type=int, default=60,
                    help="Number of data points (default: 60)")
    vd.set_defaults(command="viz-data")

    vf = sub.add_parser("viz-fleet",
                        help="Get fleet-wide visualization data")
    vf.add_argument("--time-range", type=float, default=3600.0,
                    dest='time_range_s',
                    help="Time range in seconds (default: 3600)")
    vf.set_defaults(command="viz-fleet")

    # -- anomaly auto-remediation --
    ar = sub.add_parser("anomaly-remediate",
                        help="Detect anomalies and auto-remediate")
    ar.add_argument("container_id")
    ar.add_argument("--resource", default="memory",
                    choices=["memory", "cpu", "pids"],
                    help="Resource to monitor (default: memory)")
    ar.add_argument("--sensitivity", type=float, default=2.0,
                    help="Anomaly sensitivity (default: 2.0)")
    ar.set_defaults(command="anomaly-remediate")

    ara = sub.add_parser("anomaly-remediate-all",
                         help="Remediate anomalies across all containers")
    ara.add_argument("--resource", default="memory",
                     help="Resource to monitor (default: memory)")
    ara.add_argument("--sensitivity", type=float, default=2.0,
                     help="Anomaly sensitivity (default: 2.0)")
    ara.set_defaults(command="anomaly-remediate-all")

    # -- resource usage monitoring --
    mc = sub.add_parser("monitor-configure",
                        help="Configure resource monitoring")
    mc.add_argument("container_id")
    mc.add_argument("--memory-high-pct", type=float, default=90.0,
                    help="Memory high threshold %% (default: 90)")
    mc.add_argument("--memory-low-pct", type=float, default=10.0,
                    help="Memory low threshold %% (default: 10)")
    mc.add_argument("--cpu-high-pct", type=float, default=90.0,
                    help="CPU high threshold %% (default: 90)")
    mc.add_argument("--pid-high-pct", type=float, default=80.0,
                    help="PID high threshold %% (default: 80)")
    mc.add_argument("--cost-high-daily", type=float, default=None,
                    help="Daily cost high threshold ($)")
    mc.add_argument("--trend-window", type=int, default=10,
                    help="Trend detection window (default: 10)")
    mc.add_argument("--trend-threshold", type=float, default=0.1,
                    help="Trend slope threshold (default: 0.1)")
    mc.add_argument("--enabled", action="store_true", default=True,
                    help="Enable monitoring (default: True)")
    mc.add_argument("--disabled", dest="enabled",
                    action="store_false",
                    help="Disable monitoring")
    mc.set_defaults(command="monitor-configure")

    mg = sub.add_parser("monitor-get",
                        help="Get monitoring configuration")
    mg.add_argument("container_id")
    mg.set_defaults(command="monitor-get")

    mx = sub.add_parser("monitor-check",
                        help="Check monitoring for a container")
    mx.add_argument("container_id")
    mx.set_defaults(command="monitor-check")

    mca = sub.add_parser("monitor-check-all",
                         help="Check monitoring for all containers")
    mca.set_defaults(command="monitor-check-all")

    sla_ae = sub.add_parser("sla-auto-escalation-configure",
                            help="Configure SLA auto-escalation")
    sla_ae.add_argument("container_id")
    sla_ae.add_argument("--enabled", action="store_true", default=True,
                        help="Enable auto-escalation (default: True)")
    sla_ae.add_argument("--disabled", dest="enabled",
                        action="store_false",
                        help="Disable auto-escalation")
    sla_ae.add_argument("--breach-threshold", type=int, default=3,
                        help="Breaches to trigger level 1 (default: 3)")
    sla_ae.add_argument("--escalation-window", type=float,
                        default=3600.0,
                        help="Time window for breach counting (s)")
    sla_ae.add_argument("--max-level", type=int, default=3,
                        help="Maximum escalation level (default: 3)")
    sla_ae.add_argument("--cooldown", type=float, default=300.0,
                        help="Cooldown between escalations (s)")
    sla_ae.set_defaults(command="sla-auto-escalation-configure")

    sla_br = sub.add_parser("sla-breach-record",
                            help="Record an SLA breach")
    sla_br.add_argument("container_id")
    sla_br.add_argument("--breach-type", default="downtime",
                        help="Breach type (default: downtime)")
    sla_br.add_argument("--detail", default="",
                        help="Breach detail text")
    sla_br.set_defaults(command="sla-breach-record")

    sla_st = sub.add_parser("sla-auto-escalation-status",
                            help="Get SLA auto-escalation status")
    sla_st.add_argument("container_id")
    sla_st.set_defaults(command="sla-auto-escalation-status")

    sla_rs = sub.add_parser("sla-auto-escalation-reset",
                            help="Reset SLA auto-escalation state")
    sla_rs.add_argument("container_id")
    sla_rs.set_defaults(command="sla-auto-escalation-reset")

    co = sub.add_parser("cost-optimize",
                        help="Get cost optimization report for a container")
    co.add_argument("container_id")
    co.set_defaults(command="cost-optimize")

    coa = sub.add_parser("cost-optimize-all",
                         help="Get fleet-wide cost optimization report")
    coa.set_defaults(command="cost-optimize-all")

    ap = sub.add_parser("anomaly-predict",
                        help="Predict anomalies for a container")
    ap.add_argument("container_id")
    ap.add_argument("--horizon", type=float, default=3600.0,
                    help="Prediction horizon in seconds (default: 3600)")
    ap.add_argument("--confidence", type=float, default=0.5,
                    help="Minimum confidence threshold (default: 0.5)")
    ap.set_defaults(command="anomaly-predict")

    apa = sub.add_parser("anomaly-predict-all",
                         help="Predict anomalies across all containers")
    apa.add_argument("--horizon", type=float, default=3600.0,
                     help="Prediction horizon in seconds (default: 3600)")
    apa.add_argument("--confidence", type=float, default=0.5,
                     help="Minimum confidence threshold (default: 0.5)")
    apa.set_defaults(command="anomaly-predict-all")

    psc = sub.add_parser("predictive-scale-configure",
                         help="Configure predictive scaling")
    psc.add_argument("container_id")
    psc.add_argument("--enabled", action="store_true", default=True,
                     help="Enable predictive scaling (default: True)")
    psc.add_argument("--disabled", dest="enabled",
                     action="store_false",
                     help="Disable predictive scaling")
    psc.add_argument("--lead-time", type=float, default=300.0,
                     help="Lead time in seconds (default: 300)")
    psc.add_argument("--memory-buffer", type=float, default=20.0,
                     help="Memory buffer %% (default: 20)")
    psc.add_argument("--cpu-buffer", type=float, default=15.0,
                     help="CPU buffer %% (default: 15)")
    psc.add_argument("--scale-up-threshold", type=float, default=0.75,
                     help="Scale up threshold (default: 0.75)")
    psc.add_argument("--scale-down-threshold", type=float, default=0.30,
                     help="Scale down threshold (default: 0.30)")
    psc.add_argument("--min-memory", type=int, default=None,
                     help="Minimum memory MB")
    psc.add_argument("--max-memory", type=int, default=None,
                     help="Maximum memory MB")
    psc.add_argument("--dry-run", action="store_true", default=False,
                     help="Dry run mode (don't apply changes)")
    psc.set_defaults(command="predictive-scale-configure")

    pse = sub.add_parser("predictive-scale-evaluate",
                         help="Evaluate predictive scaling")
    pse.add_argument("container_id")
    pse.set_defaults(command="predictive-scale-evaluate")

    psea = sub.add_parser("predictive-scale-evaluate-all",
                          help="Evaluate fleet predictive scaling")
    psea.set_defaults(command="predictive-scale-evaluate-all")

    pss = sub.add_parser("predictive-scale-status",
                         help="Get predictive scaling status")
    pss.add_argument("container_id")
    pss.set_defaults(command="predictive-scale-status")

    ac = sub.add_parser("anomaly-correlate",
                        help="Detect correlated anomalies across containers")
    ac.add_argument("--time-window", type=float, default=300.0,
                    help="Time window in seconds (default: 300)")
    ac.add_argument("--min-containers", type=int, default=2,
                    help="Minimum containers for correlation (default: 2)")
    ac.add_argument("--resources", nargs="+", default=None,
                    help="Filter by resources (e.g., memory cpu)")
    ac.set_defaults(command="anomaly-correlate")

    acr = sub.add_parser("anomaly-correlation-report",
                         help="Get correlation report with recommendations")
    acr.add_argument("--time-window", type=float, default=300.0,
                     help="Time window in seconds (default: 300)")
    acr.set_defaults(command="anomaly-correlation-report")

    rhm = sub.add_parser("resource-heatmap",
                        help="Generate fleet-wide resource heat map")
    rhm.add_argument("--window", type=float, default=300.0,
                     help="Time window in seconds (default: 300)")
    rhm.set_defaults(command="resource-heatmap")

    cpd = sub.add_parser("container-pressure-detail",
                        help="Get detailed pressure analysis for a container")
    cpd.add_argument("container_id", help="Container ID")
    cpd.add_argument("--window", type=float, default=300.0,
                     help="Time window in seconds (default: 300)")
    cpd.set_defaults(command="container-pressure-detail")

    rps = sub.add_parser("record-pressure-snapshot",
                        help="Record pressure snapshot for all containers")
    rps.set_defaults(command="record-pressure-snapshot")

    ct = sub.add_parser("classify-tier",
                        help="Classify container QoS tier")
    ct.add_argument("container_id", help="Container ID")
    ct.set_defaults(command="classify-tier")

    fts = sub.add_parser("fleet-tier-summary",
                         help="Fleet-wide tier distribution")
    fts.set_defaults(command="fleet-tier-summary")

    stu = sub.add_parser("suggest-tier-upgrade",
                         help="Suggest tier upgrade changes")
    stu.add_argument("container_id", help="Container ID")
    stu.set_defaults(command="suggest-tier-upgrade")

    # -- log streaming commands --
    ls = sub.add_parser("log-stream", help="Stream container logs in real-time")
    ls.add_argument("container_id")
    ls.add_argument("--follow", action="store_true", help="Follow log output")
    ls.add_argument("--interval-s", type=float, default=0.5)
    ls.add_argument("--max-lines", type=int, default=1000)
    ls.add_argument("--timeout-s", type=float, default=5.0)
    ls.set_defaults(command="log-stream")

    lf = sub.add_parser("log-filter", help="Filter container logs by regex")
    lf.add_argument("container_id")
    lf.add_argument("pattern", help="Regex pattern")
    lf.add_argument("--stream", choices=["stdout", "stderr", "both"], default="both")
    lf.add_argument("--tail", type=int, default=None)
    lf.add_argument("--case-insensitive", action="store_true")
    lf.add_argument("--max-matches", type=int, default=500)
    lf.set_defaults(command="log-filter")

    le = sub.add_parser("log-export", help="Export container logs to file")
    le.add_argument("container_id")
    le.add_argument("dest_path", help="Output file path")
    le.add_argument("--format", choices=["text", "json"], default="text")
    le.add_argument("--stream", choices=["stdout", "stderr", "both"], default="both")
    le.add_argument("--tail", type=int, default=None)
    le.set_defaults(command="log-export")

    # -- image dedup/GC commands --
    id_ = sub.add_parser("image-dedup", help="Detect duplicate image layers")
    id_.add_argument("--images-dir", default=None)
    id_.set_defaults(command="image-dedup")

    ig = sub.add_parser("image-gc", help="Garbage collect unused images")
    ig.add_argument("--images-dir", default=None)
    ig.add_argument("--dry-run", action="store_true", default=True)
    ig.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    ig.add_argument("--max-age-days", type=int, default=None)
    ig.add_argument("--unused-only", action="store_true")
    ig.set_defaults(command="image-gc")

    il = sub.add_parser("image-layer-stats", help="Show image layer statistics")
    il.add_argument("--images-dir", default=None)
    il.set_defaults(command="image-layer-stats")

    # -- DNS commands --
    dg = sub.add_parser("dns-generate", help="Generate resolv.conf for a container")
    dg.add_argument("container_id")
    dg.add_argument("--nameservers", nargs="+", default=None)
    dg.add_argument("--search-domains", nargs="+", default=None)
    dg.add_argument("--options", nargs="+", default=None)
    dg.set_defaults(command="dns-generate")

    dr = sub.add_parser("dns-resolve", help="Resolve a hostname")
    dr.add_argument("hostname")
    dr.add_argument("--nameservers", nargs="+", default=None)
    dr.add_argument("--timeout-s", type=float, default=5.0)
    dr.set_defaults(command="dns-resolve")

    dc = sub.add_parser("dns-get-config", help="Show container DNS config")
    dc.add_argument("container_id")
    dc.set_defaults(command="dns-get-config")

    du = sub.add_parser("dns-update", help="Update container DNS config")
    du.add_argument("container_id")
    du.add_argument("--add-nameservers", nargs="+", default=None)
    du.add_argument("--remove-nameservers", nargs="+", default=None)
    du.add_argument("--add-search-domains", nargs="+", default=None)
    du.add_argument("--remove-search-domains", nargs="+", default=None)
    du.set_defaults(command="dns-update")

    # -- container networking commands --
    cn = sub.add_parser("create-network", help="Create an isolated container network")
    cn.add_argument("name", help="Network name")
    cn.add_argument("--subnet", default="172.18.0.0/16")
    cn.add_argument("--gateway", default="172.18.0.1")
    cn.add_argument("--enable-dns", action="store_true", default=True)
    cn.set_defaults(command="create-network")

    rn = sub.add_parser("remove-network", help="Remove a container network")
    rn.add_argument("name")
    rn.set_defaults(command="remove-network")

    ln = sub.add_parser("list-networks", help="List container networks")
    ln.set_defaults(command="list-networks")

    con = sub.add_parser("connect-network", help="Connect a container to a network")
    con.add_argument("network_name")
    con.add_argument("container_id")
    con.add_argument("--aliases", nargs="+", default=None)
    con.add_argument("--ip-address", default=None)
    con.set_defaults(command="connect-network")

    dis = sub.add_parser("disconnect-network", help="Disconnect a container from a network")
    dis.add_argument("network_name")
    dis.add_argument("container_id")
    dis.set_defaults(command="disconnect-network")

    topo = sub.add_parser("network-topology", help="Show network topology")
    topo.add_argument("network_name")
    topo.set_defaults(command="network-topology")

    ndns = sub.add_parser("network-dns-resolve", help="Resolve a name on a network")
    ndns.add_argument("network_name")
    ndns.add_argument("name")
    ndns.set_defaults(command="network-dns-resolve")

    tc = sub.add_parser("test-connectivity", help="Test connectivity between containers")
    tc.add_argument("network_name")
    tc.add_argument("src_container_id")
    tc.add_argument("dst_ip")
    tc.set_defaults(command="test-connectivity")

    # -- migration commands --
    pm = sub.add_parser("plan-migration", help="Plan a container migration")
    pm.add_argument("container_id")
    pm.add_argument("target_node")
    pm.add_argument("--strategy", choices=["live", "stop", "snapshot"], default="live")
    pm.add_argument("--max-downtime-ms", type=int, default=1000)
    pm.set_defaults(command="plan-migration")

    em = sub.add_parser("execute-migration", help="Execute a container migration")
    em.add_argument("container_id")
    em.add_argument("target_node")
    em.add_argument("--strategy", choices=["live", "stop", "snapshot"], default="live")
    em.add_argument("--dry-run", action="store_true", default=True)
    em.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    em.set_defaults(command="execute-migration")

    mh = sub.add_parser("migration-history", help="Show migration history")
    mh.add_argument("--container-id", default=None)
    mh.add_argument("--tail", type=int, default=20)
    mh.set_defaults(command="migration-history")

    mc = sub.add_parser("migration-cost", help="Estimate migration cost")
    mc.add_argument("container_id")
    mc.add_argument("target_node")
    mc.add_argument("--strategy", choices=["live", "stop", "snapshot"], default="live")
    mc.set_defaults(command="migration-cost")

    # -- alerting commands --
    cac = sub.add_parser("configure-alert-channel", help="Configure an alert notification channel")
    cac.add_argument("channel_id")
    cac.add_argument("channel_type", choices=["webhook", "email", "log", "callback"])
    cac.add_argument("--enabled", action="store_true", default=True)
    cac.set_defaults(command="configure-alert-channel")

    rac = sub.add_parser("remove-alert-channel", help="Remove an alert channel")
    rac.add_argument("channel_id")
    rac.set_defaults(command="remove-alert-channel")

    lac = sub.add_parser("list-alert-channels", help="List alert channels")
    lac.set_defaults(command="list-alert-channels")

    eac = sub.add_parser("enable-alert-channel", help="Enable an alert channel")
    eac.add_argument("channel_id")
    eac.set_defaults(command="enable-alert-channel")

    dac = sub.add_parser("disable-alert-channel", help="Disable an alert channel")
    dac.add_argument("channel_id")
    dac.set_defaults(command="disable-alert-channel")

    car = sub.add_parser("configure-alert-rules", help="Configure alert rules")
    car.add_argument("--container-id", default=None)
    car.add_argument("--fleet-wide", action="store_true")
    car.set_defaults(command="configure-alert-rules")

    gar = sub.add_parser("get-alert-rules", help="Get alert rules")
    gar.add_argument("--container-id", default=None)
    gar.set_defaults(command="get-alert-rules")

    ea = sub.add_parser("evaluate-alerts", help="Evaluate alert rules for a container")
    ea.add_argument("container_id")
    ea.set_defaults(command="evaluate-alerts")

    ah = sub.add_parser("alert-history", help="Show alert history")
    ah.add_argument("--container-id", default=None)
    ah.add_argument("--alert-type", default=None)
    ah.add_argument("--tail", type=int, default=50)
    ah.set_defaults(command="alert-history")

    # -- anomaly detection commands --
    da = sub.add_parser("detect-anomalies", help="Detect anomalies in container resource usage")
    da.add_argument("container_id")
    da.add_argument("--window-size", type=int, default=30)
    da.add_argument("--z-threshold", type=float, default=2.5)
    da.add_argument("--iqr-multiplier", type=float, default=1.5)
    da.set_defaults(command="detect-anomalies")

    dfa = sub.add_parser("detect-fleet-anomalies", help="Detect anomalies across all containers")
    dfa.add_argument("--window-size", type=int, default=30)
    dfa.add_argument("--z-threshold", type=float, default=2.5)
    dfa.set_defaults(command="detect-fleet-anomalies")

    # -- snapshot diff/rollback commands --
    ds = sub.add_parser("diff-snapshots", help="Compare two snapshots")
    ds.add_argument("--snapshot-a", type=eval, default={})
    ds.add_argument("--snapshot-b", type=eval, default={})
    ds.set_defaults(command="diff-snapshots")

    rs = sub.add_parser("rollback-snapshot", help="Rollback container to a snapshot")
    rs.add_argument("container_id")
    rs.add_argument("--snapshot", type=eval, default={})
    rs.add_argument("--dry-run", action="store_true", default=True)
    rs.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    rs.set_defaults(command="rollback-snapshot")

    # -- placement optimization commands --
    op_ = sub.add_parser("optimize-placement", help="Optimize container placement across nodes")
    op_.add_argument("--containers", nargs="+", default=None)
    op_.add_argument("--strategy", choices=["balanced", "packed", "spread"], default="balanced")
    op_.add_argument("--respect-affinity", action="store_true", default=True)
    op_.set_defaults(command="optimize-placement")

    ps = sub.add_parser("placement-score", help="Score a specific container placement")
    ps.add_argument("container_id")
    ps.add_argument("node_id")
    ps.set_defaults(command="placement-score")

    # -- dynamic resource limit commands --
    cas = sub.add_parser("configure-auto-scaling", help="Configure auto-scaling for a container")
    cas.add_argument("container_id")
    cas.add_argument("--enabled", action="store_true", default=True)
    cas.add_argument("--min-memory-mb", type=int, default=64)
    cas.add_argument("--max-memory-mb", type=int, default=4096)
    cas.add_argument("--target-memory-pct", type=float, default=70.0)
    cas.add_argument("--scale-up-step-mb", type=int, default=128)
    cas.add_argument("--scale-down-step-mb", type=int, default=64)
    cas.add_argument("--cooldown-seconds", type=float, default=60.0)
    cas.set_defaults(command="configure-auto-scaling")

    ea = sub.add_parser("evaluate-and-adjust", help="Evaluate and adjust resource limits")
    ea.add_argument("container_id")
    ea.set_defaults(command="evaluate-and-adjust")

    ass_ = sub.add_parser("auto-scaling-status", help="Show auto-scaling status")
    ass_.add_argument("container_id")
    ass_.set_defaults(command="auto-scaling-status")

    bes = sub.add_parser("batch-evaluate-scaling", help="Batch evaluate auto-scaling")
    bes.set_defaults(command="batch-evaluate-scaling")

    # -- dependency graph commands --
    gdg = sub.add_parser("generate-dependency-graph", help="Generate dependency graph")
    gdg.add_argument("--container-ids", nargs="+", default=None)
    gdg.add_argument("--format", choices=["ascii", "dot", "mermaid"], default="ascii")
    gdg.set_defaults(command="generate-dependency-graph")

    gcp = sub.add_parser("get-critical-path", help="Find critical dependency path")
    gcp.add_argument("--container-ids", nargs="+", default=None)
    gcp.set_defaults(command="get-critical-path")

    # -- federation commands --
    rfp = sub.add_parser("register-federation-peer", help="Register a federation peer cluster")
    rfp.add_argument("peer_id")
    rfp.add_argument("peer_url")
    rfp.add_argument("cluster_name")
    rfp.add_argument("--trust-level", choices=["full", "limited", "none"], default="full")
    rfp.set_defaults(command="register-federation-peer")

    ufp = sub.add_parser("unregister-federation-peer", help="Remove a federation peer")
    ufp.add_argument("peer_id")
    ufp.set_defaults(command="unregister-federation-peer")

    lfp = sub.add_parser("list-federation-peers", help="List federation peers")
    lfp.set_defaults(command="list-federation-peers")

    scwp = sub.add_parser("share-container-with-peer", help="Share a container with a peer")
    scwp.add_argument("container_id")
    scwp.add_argument("peer_id")
    scwp.add_argument("--permissions", nargs="+", default=None)
    scwp.set_defaults(command="share-container-with-peer")

    ucfp = sub.add_parser("unshare-container-from-peer", help="Unshare a container from a peer")
    ucfp.add_argument("container_id")
    ucfp.add_argument("peer_id")
    ucfp.set_defaults(command="unshare-container-from-peer")

    srwp = sub.add_parser("share-resources-with-peer", help="Share resources with a peer")
    srwp.add_argument("peer_id")
    srwp.add_argument("resource_type")
    srwp.add_argument("amount", type=int)
    srwp.set_defaults(command="share-resources-with-peer")

    gfs = sub.add_parser("get-federation-status", help="Get federation overview")
    gfs.set_defaults(command="get-federation-status")

    pcm = sub.add_parser("plan-cross-cluster-migration", help="Plan cross-cluster migration")
    pcm.add_argument("container_id")
    pcm.add_argument("target_peer_id")
    pcm.add_argument("--strategy", choices=["snapshot", "live"], default="snapshot")
    pcm.set_defaults(command="plan-cross-cluster-migration")

    # -- event-driven scaling commands --
    cet = sub.add_parser("configure-event-trigger", help="Configure an event trigger")
    cet.add_argument("trigger_id")
    cet.add_argument("event_type")
    cet.add_argument("action")
    cet.add_argument("--enabled", action="store_true", default=True)
    cet.set_defaults(command="configure-event-trigger")

    ret = sub.add_parser("remove-event-trigger", help="Remove an event trigger")
    ret.add_argument("trigger_id")
    ret.set_defaults(command="remove-event-trigger")

    let = sub.add_parser("list-event-triggers", help="List event triggers")
    let.set_defaults(command="list-event-triggers")

    eet = sub.add_parser("enable-event-trigger", help="Enable an event trigger")
    eet.add_argument("trigger_id")
    eet.set_defaults(command="enable-event-trigger")

    det = sub.add_parser("disable-event-trigger", help="Disable an event trigger")
    det.add_argument("trigger_id")
    det.set_defaults(command="disable-event-trigger")

    fe = sub.add_parser("fire-event", help="Fire an event manually")
    fe.add_argument("event_type")
    fe.add_argument("--container-id", default=None)
    fe.set_defaults(command="fire-event")

    gel = sub.add_parser("get-event-log", help="Get event log")
    gel.add_argument("--event-type", default=None)
    gel.add_argument("--container-id", default=None)
    gel.add_argument("--tail", type=int, default=50)
    gel.set_defaults(command="get-event-log")

    gts = sub.add_parser("get-trigger-stats", help="Get trigger statistics")
    gts.set_defaults(command="get-trigger-stats")

    # -- cluster dashboard commands --
    gcd = sub.add_parser("generate-cluster-dashboard", help="Generate cluster health dashboard")
    gcd.set_defaults(command="generate-cluster-dashboard")

    # -- network rule commands --
    cnr = sub.add_parser("configure-network-rule", help="Configure a network firewall rule")
    cnr.add_argument("rule_id")
    cnr.add_argument("direction", choices=["ingress", "egress"])
    cnr.add_argument("action", choices=["allow", "deny", "log"])
    cnr.add_argument("--protocol", default="tcp")
    cnr.add_argument("--port", type=int, default=None)
    cnr.add_argument("--source", default=None)
    cnr.add_argument("--destination", default=None)
    cnr.add_argument("--container-filter", default=None)
    cnr.add_argument("--priority", type=int, default=100)
    cnr.set_defaults(command="configure-network-rule")

    rnr = sub.add_parser("remove-network-rule", help="Remove a network rule")
    rnr.add_argument("rule_id")
    rnr.set_defaults(command="remove-network-rule")

    lnr = sub.add_parser("list-network-rules", help="List network rules")
    lnr.add_argument("--direction", choices=["ingress", "egress"], default=None)
    lnr.add_argument("--container-id", default=None)
    lnr.set_defaults(command="list-network-rules")

    enr = sub.add_parser("enable-network-rule", help="Enable a network rule")
    enr.add_argument("rule_id")
    enr.set_defaults(command="enable-network-rule")

    dnr = sub.add_parser("disable-network-rule", help="Disable a network rule")
    dnr.add_argument("rule_id")
    dnr.set_defaults(command="disable-network-rule")

    ena = sub.add_parser("evaluate-network-access", help="Evaluate network access for a container")
    ena.add_argument("container_id")
    ena.add_argument("direction", choices=["ingress", "egress"])
    ena.add_argument("--protocol", default="tcp")
    ena.add_argument("--port", type=int, default=None)
    ena.add_argument("--remote-ip", default=None)
    ena.set_defaults(command="evaluate-network-access")

    gnrs = sub.add_parser("get-network-rule-stats", help="Get network rule statistics")
    gnrs.set_defaults(command="get-network-rule-stats")

    # -- backup/DR commands --
    cb = sub.add_parser("create-backup", help="Create a container backup")
    cb.add_argument("container_id")
    cb.add_argument("--backup-id", default=None)
    cb.add_argument("--backup-type", choices=["full", "incremental"], default="full")
    cb.add_argument("--destination", default="/tmp/nyrqis-backups")
    cb.add_argument("--include-logs", action="store_true", default=True)
    cb.add_argument("--no-include-logs", dest="include_logs", action="store_false")
    cb.add_argument("--include-state", action="store_true", default=True)
    cb.add_argument("--no-include-state", dest="include_state", action="store_false")
    cb.set_defaults(command="create-backup")

    lb = sub.add_parser("list-backups", help="List backups")
    lb.add_argument("--container-id", default=None)
    lb.set_defaults(command="list-backups")

    gb = sub.add_parser("get-backup", help="Get backup details")
    gb.add_argument("backup_id")
    gb.set_defaults(command="get-backup")

    db = sub.add_parser("delete-backup", help="Delete a backup")
    db.add_argument("backup_id")
    db.set_defaults(command="delete-backup")

    rfb = sub.add_parser("restore-from-backup", help="Restore from backup")
    rfb.add_argument("backup_id")
    rfb.add_argument("--container-id", default=None)
    rfb.add_argument("--dry-run", action="store_true", default=True)
    rfb.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    rfb.set_defaults(command="restore-from-backup")

    cbp = sub.add_parser("configure-backup-policy", help="Configure backup policy")
    cbp.add_argument("container_id")
    cbp.add_argument("--enabled", action="store_true", default=True)
    cbp.add_argument("--interval-hours", type=int, default=24)
    cbp.add_argument("--retention-count", type=int, default=7)
    cbp.add_argument("--backup-type", choices=["full", "incremental"], default="full")
    cbp.add_argument("--include-logs", action="store_true", default=True)
    cbp.add_argument("--no-include-logs", dest="include_logs", action="store_false")
    cbp.set_defaults(command="configure-backup-policy")

    gbp = sub.add_parser("get-backup-policy", help="Get backup policy")
    gbp.add_argument("container_id")
    gbp.set_defaults(command="get-backup-policy")

    gdrs = sub.add_parser("get-dr-status", help="Get DR status")
    gdrs.set_defaults(command="get-dr-status")

    # -- log aggregation commands --
    acl = sub.add_parser("aggregate-cluster-logs", help="Aggregate logs from all containers")
    acl.add_argument("--pattern", default="")
    acl.add_argument("--stream", choices=["stdout", "stderr", "both"], default="both")
    acl.add_argument("--tail", type=int, default=100)
    acl.add_argument("--container-ids", nargs="+", default=None)
    acl.add_argument("--sort-by", choices=["timestamp", "container"], default="timestamp")
    acl.set_defaults(command="aggregate-cluster-logs")

    scl = sub.add_parser("search-cluster-logs", help="Search across all container logs")
    scl.add_argument("pattern")
    scl.add_argument("--stream", choices=["stdout", "stderr", "both"], default="both")
    scl.add_argument("--max-matches", type=int, default=500)
    scl.set_defaults(command="search-cluster-logs")

    gls = sub.add_parser("get-log-stats", help="Get log statistics")
    gls.set_defaults(command="get-log-stats")

    # -- security scan commands --
    scs = sub.add_parser("scan-container-security", help="Scan container security")
    scs.add_argument("container_id")
    scs.set_defaults(command="scan-container-security")

    sfs = sub.add_parser("scan-fleet-security", help="Scan fleet security")
    sfs.set_defaults(command="scan-fleet-security")

    gss = sub.add_parser("get-security-summary", help="Get security summary")
    gss.set_defaults(command="get-security-summary")

    # -- vulnerability scanning commands --
    siv = sub.add_parser("scan-image-vulnerabilities", help="Scan image for vulnerabilities")
    siv.add_argument("image_path")
    siv.add_argument("--severity-filter", nargs="+", default=None)
    siv.set_defaults(command="scan-image-vulnerabilities")

    scv = sub.add_parser("scan-container-vulnerabilities", help="Scan container for vulnerabilities")
    scv.add_argument("container_id")
    scv.add_argument("--severity-filter", nargs="+", default=None)
    scv.set_defaults(command="scan-container-vulnerabilities")

    sfv = sub.add_parser("scan-fleet-vulnerabilities", help="Scan fleet for vulnerabilities")
    sfv.add_argument("--severity-filter", nargs="+", default=None)
    sfv.set_defaults(command="scan-fleet-vulnerabilities")

    gvs = sub.add_parser("get-vulnerability-summary", help="Get vulnerability summary")
    gvs.set_defaults(command="get-vulnerability-summary")

    # -- performance profiling commands --
    pcp = sub.add_parser("profile-container-performance", help="Profile container performance")
    pcp.add_argument("container_id")
    pcp.set_defaults(command="profile-container-performance")

    pfp = sub.add_parser("profile-fleet-performance", help="Profile fleet performance")
    pfp.set_defaults(command="profile-fleet-performance")

    gpr = sub.add_parser("get-performance-recommendations", help="Get performance recommendations")
    gpr.add_argument("container_id")
    gpr.set_defaults(command="get-performance-recommendations")

    # -- capacity forecasting commands --
    frn = sub.add_parser("forecast-resource-needs", help="Forecast resource needs")
    frn.add_argument("container_id")
    frn.add_argument("--horizon-hours", type=int, default=24)
    frn.set_defaults(command="forecast-resource-needs")

    ffc = sub.add_parser("forecast-fleet-capacity", help="Forecast fleet capacity")
    ffc.set_defaults(command="forecast-fleet-capacity")

    gcr = sub.add_parser("get-capacity-recommendations", help="Get capacity recommendations")
    gcr.set_defaults(command="get-capacity-recommendations")

    # -- filesystem commands --
    rcf = sub.add_parser("read-container-file", help="Read a file from a container")
    rcf.add_argument("container_id")
    rcf.add_argument("path")
    rcf.add_argument("--max-size", type=int, default=1048576)
    rcf.set_defaults(command="read-container-file")

    wcf = sub.add_parser("write-container-file", help="Write a file to a container")
    wcf.add_argument("container_id")
    wcf.add_argument("path")
    wcf.add_argument("content")
    wcf.add_argument("--no-create-dirs", dest="create_dirs", action="store_false")
    wcf.set_defaults(command="write-container-file")

    lcf = sub.add_parser("list-container-files", help="List files in a container")
    lcf.add_argument("container_id")
    lcf.add_argument("--path", default="/")
    lcf.add_argument("--recursive", action="store_true")
    lcf.add_argument("--max-entries", type=int, default=500)
    lcf.set_defaults(command="list-container-files")

    dcf = sub.add_parser("delete-container-file", help="Delete a file from a container")
    dcf.add_argument("container_id")
    dcf.add_argument("path")
    dcf.set_defaults(command="delete-container-file")

    gfi = sub.add_parser("get-file-info", help="Get file metadata from a container")
    gfi.add_argument("container_id")
    gfi.add_argument("path")
    gfi.set_defaults(command="get-file-info")

    # -- process tree commands --
    gpt = sub.add_parser("get-process-tree", help="Get container process tree")
    gpt.add_argument("container_id")
    gpt.add_argument("--root-pid", type=int, default=None)
    gpt.add_argument("--max-depth", type=int, default=10)
    gpt.set_defaults(command="get-process-tree")

    gps = sub.add_parser("get-process-stats", help="Get process statistics")
    gps.add_argument("container_id")
    gps.set_defaults(command="get-process-stats")

    # -- comparison report commands --
    gcr2 = sub.add_parser("generate-comparison-report", help="Generate comparison report")
    gcr2.add_argument("--container-ids", nargs="+", default=None)
    gcr2.add_argument("--no-recommendations", dest="include_recommendations", action="store_false")
    gcr2.set_defaults(command="generate-comparison-report")

    gcrep = sub.add_parser("generate-cost-report", help="Generate cost report")
    gcrep.add_argument("--container-ids", nargs="+", default=None)
    gcrep.set_defaults(command="generate-cost-report")

    # Health check subcommands
    chc = sub.add_parser("configure-health-check", help="Configure health check probe")
    chc.add_argument("container_id")
    chc.add_argument("--check-type", default="http", choices=["http", "tcp", "exec", "process"])
    chc.add_argument("--endpoint", default="/")
    chc.add_argument("--port", type=int, default=80)
    chc.add_argument("--interval-seconds", type=int, default=30)
    chc.add_argument("--timeout-seconds", type=int, default=5)
    chc.add_argument("--failure-threshold", type=int, default=3)
    chc.add_argument("--success-threshold", type=int, default=1)
    chc.set_defaults(command="configure-health-check")

    ghc = sub.add_parser("get-health-check", help="Get health check config")
    ghc.add_argument("container_id")
    ghc.set_defaults(command="get-health-check")

    ehc = sub.add_parser("evaluate-health-check", help="Evaluate health check")
    ehc.add_argument("container_id")
    ehc.set_defaults(command="evaluate-health-check")

    grs = sub.add_parser("get-readiness-status", help="Get readiness status")
    grs.add_argument("container_id")
    grs.set_defaults(command="get-readiness-status")

    gls = sub.add_parser("get-liveness-status", help="Get liveness status")
    gls.add_argument("container_id")
    gls.set_defaults(command="get-liveness-status")

    fho = sub.add_parser("fleet-health-overview", help="Fleet health overview")
    fho.set_defaults(command="fleet-health-overview")

    # Escalation chain subcommands
    cec = sub.add_parser("configure-escalation-chain", help="Configure escalation chain")
    cec.add_argument("container_id")
    cec.add_argument("--name", default="default")
    cec.set_defaults(command="configure-escalation-chain")

    eev = sub.add_parser("evaluate-escalation", help="Evaluate escalation")
    eev.add_argument("container_id")
    eev.add_argument("--severity", type=float, default=0)
    eev.set_defaults(command="evaluate-escalation")

    ges = sub.add_parser("get-escalation-status", help="Get escalation status")
    ges.add_argument("container_id")
    ges.set_defaults(command="get-escalation-status")

    res = sub.add_parser("reset-escalation-state", help="Reset escalation state")
    res.add_argument("container_id")
    res.add_argument("--chain-name", default=None)
    res.set_defaults(command="reset-escalation-state")

    dec = sub.add_parser("disable-escalation-chain", help="Disable escalation chain")
    dec.add_argument("container_id")
    dec.add_argument("chain_name")
    dec.set_defaults(command="disable-escalation-chain")

    # Compliance subcommands
    gcr3 = sub.add_parser("generate-compliance-report", help="Generate compliance report")
    gcr3.add_argument("--container-ids", nargs="+", default=None)
    gcr3.add_argument("--policy", default="basic", choices=["basic", "strict", "pci", "hipaa"])
    gcr3.set_defaults(command="generate-compliance-report")

    eal = sub.add_parser("export-audit-logs", help="Export audit logs")
    eal.add_argument("--container-ids", nargs="+", default=None)
    eal.add_argument("--format", default="json", choices=["json", "csv"])
    eal.set_defaults(command="export-audit-logs")

    gcs = sub.add_parser("get-compliance-summary", help="Get compliance summary")
    gcs.add_argument("--policy", default="basic", choices=["basic", "strict", "pci", "hipaa"])
    gcs.set_defaults(command="get-compliance-summary")

    # Secret management subcommands
    cs = sub.add_parser("create-secret", help="Create encrypted secret")
    cs.add_argument("name")
    cs.add_argument("--data", help='JSON key-value pairs (e.g. \'{"key":"value"}\')')
    cs.add_argument("--namespace", default="default")
    cs.add_argument("--secret-type", default="opaque", choices=["opaque", "tls", "docker-registry", "ssh"])
    cs.set_defaults(command="create-secret")

    gs = sub.add_parser("get-secret", help="Get secret")
    gs.add_argument("secret_id")
    gs.add_argument("--decrypt", action="store_true")
    gs.set_defaults(command="get-secret")

    ds = sub.add_parser("delete-secret", help="Delete secret")
    ds.add_argument("secret_id")
    ds.set_defaults(command="delete-secret")

    rs = sub.add_parser("rotate-secret", help="Rotate secret")
    rs.add_argument("secret_id")
    rs.add_argument("--new-data", help='JSON key-value pairs')
    rs.set_defaults(command="rotate-secret")

    ls = sub.add_parser("list-secrets", help="List secrets")
    ls.add_argument("--namespace", default=None)
    ls.set_defaults(command="list-secrets")

    su = sub.add_parser("secret-usage", help="Secret usage overview")
    su.set_defaults(command="secret-usage")

    # Namespace / resource quota subcommands
    cn = sub.add_parser("create-namespace", help="Create namespace")
    cn.add_argument("name")
    cn.set_defaults(command="create-namespace")

    sq = sub.add_parser("set-resource-quota", help="Set resource quota")
    sq.add_argument("namespace")
    sq.add_argument("resource_type", choices=["memory_mb", "cpu_cores", "pids", "containers", "storage_mb"])
    sq.add_argument("hard_limit", type=float)
    sq.add_argument("--soft-limit", type=float, default=None)
    sq.set_defaults(command="set-resource-quota")

    gq = sub.add_parser("get-resource-quota", help="Get resource quotas")
    gq.add_argument("namespace")
    gq.set_defaults(command="get-resource-quota")

    cq = sub.add_parser("check-quota-compliance", help="Check quota compliance")
    cq.add_argument("namespace")
    cq.set_defaults(command="check-quota-compliance")

    ln2 = sub.add_parser("list-namespaces", help="List namespaces")
    ln2.set_defaults(command="list-namespaces")

    dn = sub.add_parser("delete-namespace", help="Delete namespace")
    dn.add_argument("name")
    dn.set_defaults(command="delete-namespace")

    ns2 = sub.add_parser("namespace-summary", help="Namespace summary")
    ns2.set_defaults(command="namespace-summary")

    # Deployment rollback subcommands
    rd = sub.add_parser("record-deployment", help="Record deployment version")
    rd.add_argument("container_id")
    rd.add_argument("--notes", default="")
    rd.set_defaults(command="record-deployment")

    dh = sub.add_parser("deployment-history", help="Deployment version history")
    dh.add_argument("container_id")
    dh.add_argument("--limit", type=int, default=10)
    dh.set_defaults(command="deployment-history")

    rb = sub.add_parser("rollback-deployment", help="Rollback to a version")
    rb.add_argument("container_id")
    rb.add_argument("version", type=int)
    rb.set_defaults(command="rollback-deployment")

    ddf = sub.add_parser("deployment-diff", help="Diff two versions")
    ddf.add_argument("container_id")
    ddf.add_argument("version_a", type=int)
    ddf.add_argument("version_b", type=int)
    ddf.set_defaults(command="deployment-diff")

    rc = sub.add_parser("rollback-candidates", help="List rollback candidates")
    rc.add_argument("container_id")
    rc.set_defaults(command="rollback-candidates")

    dst = sub.add_parser("deployment-status", help="Current deployment status")
    dst.add_argument("container_id")
    dst.set_defaults(command="deployment-status")

    # Graceful shutdown subcommands
    cgs = sub.add_parser("configure-graceful-shutdown", help="Configure graceful shutdown")
    cgs.add_argument("container_id")
    cgs.add_argument("--drain-timeout", type=int, default=30)
    cgs.add_argument("--signal", default="SIGTERM", choices=["SIGTERM", "SIGINT"])
    cgs.add_argument("--pre-stop-hook", default=None)
    cgs.set_defaults(command="configure-graceful-shutdown")

    igs = sub.add_parser("initiate-graceful-shutdown", help="Initiate graceful shutdown")
    igs.add_argument("container_id")
    igs.set_defaults(command="initiate-graceful-shutdown")

    gss = sub.add_parser("get-shutdown-status", help="Get shutdown status")
    gss.add_argument("container_id")
    gss.set_defaults(command="get-shutdown-status")

    fs = sub.add_parser("force-shutdown", help="Force-kill container")
    fs.add_argument("container_id")
    fs.set_defaults(command="force-shutdown")

    bgs = sub.add_parser("batch-graceful-shutdown", help="Batch graceful shutdown")
    bgs.add_argument("container_ids", nargs="+")
    bgs.add_argument("--drain-timeout", type=int, default=30)
    bgs.set_defaults(command="batch-graceful-shutdown")

    gdp = sub.add_parser("get-drain-progress", help="Get drain progress")
    gdp.add_argument("container_id")
    gdp.set_defaults(command="get-drain-progress")

    # Config hot-reload subcommands
    rcw = sub.add_parser("register-config-watcher", help="Register config watcher")
    rcw.add_argument("container_id")
    rcw.add_argument("config_path")
    rcw.add_argument("--reload-action", default="restart", choices=["restart", "signal", "in-place"])
    rcw.set_defaults(command="register-config-watcher")

    tcr = sub.add_parser("trigger-config-reload", help="Trigger config reload")
    tcr.add_argument("container_id")
    tcr.add_argument("watcher_id")
    tcr.set_defaults(command="trigger-config-reload")

    gcw = sub.add_parser("get-config-watchers", help="Get config watchers")
    gcw.add_argument("container_id")
    gcw.set_defaults(command="get-config-watchers")

    rcw2 = sub.add_parser("remove-config-watcher", help="Remove config watcher")
    rcw2.add_argument("container_id")
    rcw2.add_argument("watcher_id")
    rcw2.set_defaults(command="remove-config-watcher")

    hrc = sub.add_parser("hot-reload-config", help="Hot-reload config")
    hrc.add_argument("container_id")
    hrc.add_argument("--config", help='JSON config changes')
    hrc.set_defaults(command="hot-reload-config")

    grh = sub.add_parser("get-reload-history", help="Get reload history")
    grh.add_argument("container_id")
    grh.set_defaults(command="get-reload-history")

    # Event correlation subcommands
    re2 = sub.add_parser("record-event", help="Record an event")
    re2.add_argument("container_id")
    re2.add_argument("event_type")
    re2.add_argument("--message", default="")
    re2.add_argument("--severity", default="info", choices=["debug", "info", "warning", "error", "critical"])
    re2.set_defaults(command="record-event")

    ce = sub.add_parser("correlate-events", help="Correlate events across containers")
    ce.add_argument("--time-window", type=float, default=300.0)
    ce.add_argument("--min-containers", type=int, default=2)
    ce.set_defaults(command="correlate-events")

    aep = sub.add_parser("analyze-event-patterns", help="Analyze event patterns")
    aep.add_argument("--time-window", type=float, default=3600.0)
    aep.set_defaults(command="analyze-event-patterns")

    src = sub.add_parser("suggest-root-cause", help="Suggest root causes")
    src.add_argument("--time-window", type=float, default=300.0)
    src.set_defaults(command="suggest-root-cause")

    get = sub.add_parser("get-event-timeline", help="Get event timeline")
    get.add_argument("--container-ids", nargs="+", default=None)
    get.add_argument("--time-window", type=float, default=3600.0)
    get.set_defaults(command="get-event-timeline")

    # Network monitoring subcommands
    cnm = sub.add_parser("configure-network-monitoring", help="Configure network monitoring")
    cnm.add_argument("container_id")
    cnm.add_argument("--interfaces", nargs="+")
    cnm.add_argument("--sample-interval", type=float, default=1.0)
    cnm.set_defaults(command="configure-network-monitoring")

    rns = sub.add_parser("record-network-sample", help="Record network sample")
    rns.add_argument("container_id")
    rns.add_argument("--interface", default="eth0")
    rns.add_argument("--latency-ms", type=float, default=0)
    rns.add_argument("--rx-bytes", type=int, default=0)
    rns.add_argument("--tx-bytes", type=int, default=0)
    rns.set_defaults(command="record-network-sample")

    gnls = sub.add_parser("get-network-latency-stats", help="Get latency stats")
    gnls.add_argument("container_id")
    gnls.set_defaults(command="get-network-latency-stats")

    gbs = sub.add_parser("get-bandwidth-stats", help="Get bandwidth stats")
    gbs.add_argument("container_id")
    gbs.set_defaults(command="get-bandwidth-stats")

    gnh = sub.add_parser("get-network-health", help="Get network health")
    gnh.add_argument("container_id")
    gnh.set_defaults(command="get-network-health")

    fno = sub.add_parser("fleet-network-overview", help="Fleet network overview")
    fno.set_defaults(command="fleet-network-overview")

    # Storage profiling subcommands
    csp = sub.add_parser("configure-storage-profiling", help="Configure storage profiling")
    csp.add_argument("container_id")
    csp.add_argument("--cache-size-mb", type=int, default=64)
    csp.set_defaults(command="configure-storage-profiling")

    rsio = sub.add_parser("record-storage-io", help="Record storage I/O")
    rsio.add_argument("container_id")
    rsio.add_argument("--op-type", default="read", choices=["read", "write"])
    rsio.add_argument("--path", default="/")
    rsio.add_argument("--bytes-count", type=int, default=0)
    rsio.add_argument("--duration-ms", type=float, default=0)
    rsio.set_defaults(command="record-storage-io")

    gsio = sub.add_parser("get-storage-io-stats", help="Get storage I/O stats")
    gsio.add_argument("container_id")
    gsio.set_defaults(command="get-storage-io-stats")

    gsil = sub.add_parser("get-storage-io-latency", help="Get storage I/O latency")
    gsil.add_argument("container_id")
    gsil.set_defaults(command="get-storage-io-latency")

    csc = sub.add_parser("clear-storage-cache", help="Clear storage cache")
    csc.add_argument("container_id")
    csc.set_defaults(command="clear-storage-cache")

    gshp = sub.add_parser("get-storage-hot-paths", help="Get storage hot paths")
    gshp.add_argument("container_id")
    gshp.set_defaults(command="get-storage-hot-paths")

    # Audit integrity subcommands
    iai = sub.add_parser("initialize-audit-integrity", help="Initialize audit integrity")
    iai.add_argument("container_id")
    iai.set_defaults(command="initialize-audit-integrity")

    aae = sub.add_parser("append-audit-event", help="Append audit event")
    aae.add_argument("container_id")
    aae.add_argument("audit_op")
    aae.set_defaults(command="append-audit-event")

    vai = sub.add_parser("verify-audit-integrity", help="Verify audit integrity")
    vai.add_argument("container_id")
    vai.set_defaults(command="verify-audit-integrity")

    air = sub.add_parser("audit-integrity-report", help="Audit integrity report")
    air.add_argument("container_id")
    air.set_defaults(command="audit-integrity-report")

    ts = sub.add_parser("tamper-summary", help="Tamper detection summary")
    ts.set_defaults(command="tamper-summary")

    # Rate limiting subcommands
    crl = sub.add_parser("configure-rate-limit", help="Configure rate limiter")
    crl.add_argument("container_id")
    crl.add_argument("--name", default="default")
    crl.add_argument("--rate", type=float, default=100)
    crl.add_argument("--burst", type=int, default=200)
    crl.set_defaults(command="configure-rate-limit")

    chl = sub.add_parser("check-rate-limit", help="Check rate limit")
    chl.add_argument("container_id")
    chl.add_argument("--name", default="default")
    chl.add_argument("--tokens", type=int, default=1)
    chl.set_defaults(command="check-rate-limit")

    grls = sub.add_parser("get-rate-limit-stats", help="Get rate limit stats")
    grls.add_argument("container_id")
    grls.add_argument("--name", default="default")
    grls.set_defaults(command="get-rate-limit-stats")

    garl = sub.add_parser("get-all-rate-limiters", help="Get all rate limiters")
    garl.add_argument("container_id")
    garl.set_defaults(command="get-all-rate-limiters")

    rrl = sub.add_parser("reset-rate-limit", help="Reset rate limiter")
    rrl.add_argument("container_id")
    rrl.add_argument("--name", default="default")
    rrl.set_defaults(command="reset-rate-limit")

    drl = sub.add_parser("delete-rate-limit", help="Delete rate limiter")
    drl.add_argument("container_id")
    drl.add_argument("--name", default="default")
    drl.set_defaults(command="delete-rate-limit")

    # Feature flag subcommands
    cff = sub.add_parser("create-feature-flag", help="Create feature flag")
    cff.add_argument("name")
    cff.add_argument("--enabled", action="store_true")
    cff.add_argument("--rollout-percentage", type=float, default=0)
    cff.add_argument("--description", default="")
    cff.set_defaults(command="create-feature-flag")

    eff = sub.add_parser("evaluate-feature-flag", help="Evaluate feature flag")
    eff.add_argument("container_id")
    eff.add_argument("flag_name")
    eff.set_defaults(command="evaluate-feature-flag")

    uff = sub.add_parser("update-feature-flag", help="Update feature flag")
    uff.add_argument("name")
    uff.add_argument("--enabled", action="store_true", default=None)
    uff.add_argument("--rollout-percentage", type=float, default=None)
    uff.set_defaults(command="update-feature-flag")

    lff = sub.add_parser("list-feature-flags", help="List feature flags")
    lff.set_defaults(command="list-feature-flags")

    dff = sub.add_parser("delete-feature-flag", help="Delete feature flag")
    dff.add_argument("name")
    dff.set_defaults(command="delete-feature-flag")

    ffs = sub.add_parser("feature-flag-summary", help="Feature flag summary")
    ffs.set_defaults(command="feature-flag-summary")

    # Blue-green deployment subcommands
    cbg = sub.add_parser("create-bluegreen", help="Create blue-green deployment")
    cbg.add_argument("name")
    cbg.add_argument("blue_container_id")
    cbg.add_argument("--green-container-id", default=None)
    cbg.set_defaults(command="create-bluegreen")

    st = sub.add_parser("switch-traffic", help="Switch traffic slot")
    st.add_argument("deployment_name")
    st.add_argument("target_slot", choices=["blue", "green"])
    st.add_argument("--percentage", type=float, default=None)
    st.set_defaults(command="switch-traffic")

    gbs = sub.add_parser("get-bluegreen-status", help="Get blue-green status")
    gbs.add_argument("deployment_name")
    gbs.set_defaults(command="get-bluegreen-status")

    rbg = sub.add_parser("rollback-bluegreen", help="Rollback blue-green")
    rbg.add_argument("deployment_name")
    rbg.set_defaults(command="rollback-bluegreen")

    gbh = sub.add_parser("get-bluegreen-history", help="Get blue-green history")
    gbh.add_argument("deployment_name")
    gbh.set_defaults(command="get-bluegreen-history")

    lbg = sub.add_parser("list-bluegreen", help="List blue-green deployments")
    lbg.set_defaults(command="list-bluegreen")

    dbg = sub.add_parser("delete-bluegreen", help="Delete blue-green deployment")
    dbg.add_argument("name")
    dbg.set_defaults(command="delete-bluegreen")

    # Canary deployment subcommands
    cc = sub.add_parser("create-canary", help="Create canary deployment")
    cc.add_argument("name")
    cc.add_argument("stable_container_id")
    cc.add_argument("canary_container_id")
    cc.add_argument("--traffic-percentage", type=float, default=10)
    cc.add_argument("--error-threshold", type=float, default=5)
    cc.add_argument("--latency-threshold-ms", type=float, default=200)
    cc.set_defaults(command="create-canary")

    rcm = sub.add_parser("record-canary-metric", help="Record canary metric")
    rcm.add_argument("deployment_name")
    rcm.add_argument("slot", choices=["canary", "stable"])
    rcm.add_argument("--latency-ms", type=float, default=0)
    rcm.add_argument("--is-error", action="store_true")
    rcm.set_defaults(command="record-canary-metric")

    ech = sub.add_parser("evaluate-canary-health", help="Evaluate canary health")
    ech.add_argument("deployment_name")
    ech.set_defaults(command="evaluate-canary-health")

    pc = sub.add_parser("promote-canary", help="Promote canary")
    pc.add_argument("deployment_name")
    pc.set_defaults(command="promote-canary")

    rc = sub.add_parser("rollback-canary", help="Rollback canary")
    rc.add_argument("deployment_name")
    rc.set_defaults(command="rollback-canary")

    gcs = sub.add_parser("get-canary-status", help="Get canary status")
    gcs.add_argument("deployment_name")
    gcs.set_defaults(command="get-canary-status")

    lcan = sub.add_parser("list-canary", help="List canary deployments")
    lcan.set_defaults(command="list-canary")

    dcan = sub.add_parser("delete-canary", help="Delete canary deployment")
    dcan.add_argument("name")
    dcan.set_defaults(command="delete-canary")

    # Service discovery subcommands
    rs = sub.add_parser("register-service", help="Register service")
    rs.add_argument("name")
    rs.add_argument("container_id")
    rs.add_argument("--port", type=int, default=80)
    rs.add_argument("--tags", nargs="+")
    rs.set_defaults(command="register-service")

    ds = sub.add_parser("deregister-service", help="Deregister service")
    ds.add_argument("name")
    ds.add_argument("container_id")
    ds.set_defaults(command="deregister-service")

    dis = sub.add_parser("discover-service", help="Discover service")
    dis.add_argument("name")
    dis.add_argument("--tag", default=None)
    dis.set_defaults(command="discover-service")

    sh = sub.add_parser("service-heartbeat", help="Service heartbeat")
    sh.add_argument("name")
    sh.add_argument("container_id")
    sh.set_defaults(command="service-heartbeat")

    gsi = sub.add_parser("get-service-instances", help="Get service instances")
    gsi.add_argument("name")
    gsi.set_defaults(command="get-service-instances")

    idep = sub.add_parser("inject-dependency", help="Inject service dependency")
    idep.add_argument("container_id")
    idep.add_argument("service_name")
    idep.add_argument("env_var")
    idep.set_defaults(command="inject-dependency")

    ls = sub.add_parser("list-services", help="List services")
    ls.set_defaults(command="list-services")

    # Distributed tracing subcommands
    ct = sub.add_parser("create-trace", help="Create trace")
    ct.add_argument("name")
    ct.set_defaults(command="create-trace")

    ss = sub.add_parser("start-span", help="Start span")
    ss.add_argument("trace_id")
    ss.add_argument("operation")
    ss.add_argument("--parent-span-id", default=None)
    ss.set_defaults(command="start-span")

    fs = sub.add_parser("finish-span", help="Finish span")
    fs.add_argument("trace_id")
    fs.add_argument("span_id")
    fs.set_defaults(command="finish-span")

    ft = sub.add_parser("finish-trace", help="Finish trace")
    ft.add_argument("trace_id")
    ft.set_defaults(command="finish-trace")

    gt = sub.add_parser("get-trace", help="Get trace")
    gt.add_argument("trace_id")
    gt.set_defaults(command="get-trace")

    gts = sub.add_parser("get-trace-summary", help="Get trace summary")
    gts.add_argument("trace_id")
    gts.set_defaults(command="get-trace-summary")

    lt = sub.add_parser("list-traces", help="List traces")
    lt.add_argument("--limit", type=int, default=20)
    lt.set_defaults(command="list-traces")

    # Chaos engineering subcommands
    cce = sub.add_parser("create-chaos-experiment", help="Create chaos experiment")
    cce.add_argument("name")
    cce.add_argument("target_containers", nargs="+")
    cce.add_argument("--fault-type", default="latency", choices=["latency", "cpu_stress", "memory_stress", "network_loss", "disk_fill", "process_kill"])
    cce.add_argument("--duration-seconds", type=int, default=60)
    cce.add_argument("--blast-radius", type=float, default=50)
    cce.set_defaults(command="create-chaos-experiment")

    sce = sub.add_parser("start-chaos-experiment", help="Start chaos experiment")
    sce.add_argument("name")
    sce.set_defaults(command="start-chaos-experiment")

    xce = sub.add_parser("stop-chaos-experiment", help="Stop chaos experiment")
    xce.add_argument("name")
    xce.set_defaults(command="stop-chaos-experiment")

    gcr = sub.add_parser("get-chaos-results", help="Get chaos results")
    gcr.add_argument("name")
    gcr.set_defaults(command="get-chaos-results")

    lch = sub.add_parser("list-chaos", help="List chaos experiments")
    lch.set_defaults(command="list-chaos")

    cg = sub.add_parser("create-game-day", help="Create game day")
    cg.add_argument("name")
    cg.add_argument("scenario")
    cg.add_argument("participants", nargs="+")
    cg.set_defaults(command="create-game-day")

    lgd = sub.add_parser("list-game-days", help="List game days")
    lgd.set_defaults(command="list-game-days")

    # Cost allocation subcommands
    cca = sub.add_parser("configure-cost-allocation", help="Configure cost allocation")
    cca.add_argument("container_id")
    cca.add_argument("--cost-per-hour", type=float, default=0)
    cca.add_argument("--billing-tag", default="")
    cca.add_argument("--team", default="")
    cca.set_defaults(command="configure-cost-allocation")

    gcc = sub.add_parser("get-container-cost", help="Get container cost")
    gcc.add_argument("container_id")
    gcc.add_argument("--hours", type=float, default=24)
    gcc.set_defaults(command="get-container-cost")

    gtc = sub.add_parser("get-team-cost", help="Get team cost summary")
    gtc.add_argument("team")
    gtc.add_argument("--hours", type=float, default=24)
    gtc.set_defaults(command="get-team-cost")

    gfc = sub.add_parser("get-fleet-cost", help="Get fleet cost summary")
    gfc.add_argument("--hours", type=float, default=24)
    gfc.set_defaults(command="get-fleet-cost")

    gbb = sub.add_parser("get-billing-breakdown", help="Get billing breakdown")
    gbb.add_argument("--hours", type=float, default=24)
    gbb.set_defaults(command="get-billing-breakdown")

    # SLO/SLI tracking subcommands
    cslo = sub.add_parser("create-slo", help="Create SLO")
    cslo.add_argument("name")
    cslo.add_argument("--target-percentage", type=float, default=99.9)
    cslo.add_argument("--window-days", type=int, default=30)
    cslo.add_argument("--sli-type", default="availability", choices=["availability", "latency", "error_rate"])
    cslo.set_defaults(command="create-slo")

    rse = sub.add_parser("record-sli-event", help="Record SLI event")
    rse.add_argument("slo_name")
    rse.add_argument("--is-good", action="store_true", default=True)
    rse.set_defaults(command="record-sli-event")

    gss = sub.add_parser("get-slo-status", help="Get SLO status")
    gss.add_argument("slo_name")
    gss.set_defaults(command="get-slo-status")

    lslo = sub.add_parser("list-slos", help="List SLOs")
    lslo.set_defaults(command="list-slos")

    geb = sub.add_parser("get-error-budget-report", help="Get error budget report")
    geb.set_defaults(command="get-error-budget-report")

    dslo = sub.add_parser("delete-slo", help="Delete SLO")
    dslo.add_argument("name")
    dslo.set_defaults(command="delete-slo")

    # Resource right-sizing subcommands
    aru = sub.add_parser("analyze-resource-usage", help="Analyze resource usage")
    aru.add_argument("container_id")
    aru.set_defaults(command="analyze-resource-usage")

    rrs = sub.add_parser("recommend-right-sizing", help="Get right-sizing recommendations")
    rrs.add_argument("container_id")
    rrs.set_defaults(command="recommend-right-sizing")

    ars = sub.add_parser("apply-right-sizing", help="Apply right-sizing")
    ars.add_argument("container_id")
    ars.add_argument("--dry-run", action="store_true", default=True)
    ars.set_defaults(command="apply-right-sizing")

    frr = sub.add_parser("fleet-right-sizing-report", help="Fleet right-sizing report")
    frr.set_defaults(command="fleet-right-sizing-report")

    # Service mesh subcommands
    cmc = sub.add_parser("create-mesh-cluster", help="Create mesh cluster")
    cmc.add_argument("name")
    cmc.add_argument("endpoint")
    cmc.add_argument("--region", default="default")
    cmc.set_defaults(command="create-mesh-cluster")

    ams = sub.add_parser("add-mesh-service", help="Add service to mesh")
    ams.add_argument("cluster_name")
    ams.add_argument("service_name")
    ams.add_argument("--port", type=int, default=80)
    ams.set_defaults(command="add-mesh-service")

    ctp = sub.add_parser("create-traffic-policy", help="Create traffic policy")
    ctp.add_argument("name")
    ctp.add_argument("source_service")
    ctp.add_argument("destination_service")
    ctp.set_defaults(command="create-traffic-policy")

    etp = sub.add_parser("evaluate-traffic-policy", help="Evaluate traffic policy")
    etp.add_argument("source")
    etp.add_argument("destination")
    etp.set_defaults(command="evaluate-traffic-policy")

    gmt = sub.add_parser("get-mesh-topology", help="Get mesh topology")
    gmt.set_defaults(command="get-mesh-topology")

    lmp = sub.add_parser("list-mesh-policies", help="List mesh policies")
    lmp.set_defaults(command="list-mesh-policies")

    dmp = sub.add_parser("delete-mesh-policy", help="Delete mesh policy")
    dmp.add_argument("name")
    dmp.set_defaults(command="delete-mesh-policy")

    # Auto-rollback subcommands
    car = sub.add_parser("configure-auto-rollback", help="Configure auto-rollback")
    car.add_argument("slo_name")
    car.add_argument("--deployment-name", default=None)
    car.add_argument("--canary-name", default=None)
    car.add_argument("--breach-threshold", type=float, default=1.0)
    car.add_argument("--cooldown-seconds", type=int, default=300)
    car.set_defaults(command="configure-auto-rollback")

    ckar = sub.add_parser("check-auto-rollback", help="Check and trigger auto-rollback")
    ckar.set_defaults(command="check-auto-rollback")

    gars = sub.add_parser("get-auto-rollback-status", help="Get auto-rollback status")
    gars.set_defaults(command="get-auto-rollback-status")

    garh = sub.add_parser("get-auto-rollback-history", help="Get auto-rollback history")
    garh.add_argument("slo_name")
    garh.set_defaults(command="get-auto-rollback-history")

    dar = sub.add_parser("disable-auto-rollback", help="Disable auto-rollback")
    dar.add_argument("slo_name")
    dar.set_defaults(command="disable-auto-rollback")

    delar = sub.add_parser("delete-auto-rollback", help="Delete auto-rollback")
    delar.add_argument("slo_name")
    delar.set_defaults(command="delete-auto-rollback")

    # Benchmarking subcommands
    rb = sub.add_parser("run-benchmark", help="Run benchmark")
    rb.add_argument("container_id")
    rb.add_argument("--name", default="default")
    rb.add_argument("--iterations", type=int, default=100)
    rb.set_defaults(command="run-benchmark")

    dr = sub.add_parser("detect-regression", help="Detect regression")
    dr.add_argument("container_id")
    dr.add_argument("--name", default="default")
    dr.add_argument("--threshold-pct", type=float, default=20)
    dr.set_defaults(command="detect-regression")

    gbh = sub.add_parser("get-benchmark-history", help="Get benchmark history")
    gbh.add_argument("container_id")
    gbh.add_argument("--name", default="default")
    gbh.set_defaults(command="get-benchmark-history")

    fbs = sub.add_parser("fleet-benchmark-summary", help="Fleet benchmark summary")
    fbs.set_defaults(command="fleet-benchmark-summary")

    # Multi-tenancy subcommands
    ct = sub.add_parser("create-tenant", help="Create tenant")
    ct.add_argument("name")
    ct.add_argument("--isolation-level", default="strict", choices=["strict", "shared", "none"])
    ct.set_defaults(command="create-tenant")

    att = sub.add_parser("assign-to-tenant", help="Assign container to tenant")
    att.add_argument("tenant_name")
    att.add_argument("container_id")
    att.set_defaults(command="assign-to-tenant")

    rft = sub.add_parser("remove-from-tenant", help="Remove container from tenant")
    rft.add_argument("tenant_name")
    rft.add_argument("container_id")
    rft.set_defaults(command="remove-from-tenant")

    gtu = sub.add_parser("get-tenant-usage", help="Get tenant usage")
    gtu.add_argument("tenant_name")
    gtu.set_defaults(command="get-tenant-usage")

    lt = sub.add_parser("list-tenants", help="List tenants")
    lt.set_defaults(command="list-tenants")

    dt = sub.add_parser("delete-tenant", help="Delete tenant")
    dt.add_argument("name")
    dt.set_defaults(command="delete-tenant")

    cti = sub.add_parser("check-tenant-isolation", help="Check tenant isolation")
    cti.add_argument("tenant_a")
    cti.add_argument("tenant_b")
    cti.set_defaults(command="check-tenant-isolation")

    # Continuous profiling subcommands
    sp = sub.add_parser("start-profile", help="Start profiling")
    sp.add_argument("container_id")
    sp.add_argument("--profile-type", default="cpu", choices=["cpu", "memory", "io"])
    sp.add_argument("--duration-seconds", type=int, default=30)
    sp.set_defaults(command="start-profile")

    spp = sub.add_parser("stop-profile", help="Stop profiling")
    spp.add_argument("profile_id")
    spp.set_defaults(command="stop-profile")

    gfg = sub.add_parser("get-flame-graph", help="Get flame graph")
    gfg.add_argument("profile_id")
    gfg.set_defaults(command="get-flame-graph")

    gps = sub.add_parser("get-profile-summary", help="Get profile summary")
    gps.add_argument("profile_id")
    gps.set_defaults(command="get-profile-summary")

    lp = sub.add_parser("list-profiles", help="List profiles")
    lp.add_argument("--container-id", default=None)
    lp.set_defaults(command="list-profiles")

    dp = sub.add_parser("delete-profile", help="Delete profile")
    dp.add_argument("profile_id")
    dp.set_defaults(command="delete-profile")

    # Config validation/drift subcommands
    rcs = sub.add_parser("register-config-schema", help="Register config schema")
    rcs.add_argument("name")
    rcs.add_argument("--required-fields", nargs="+")
    rcs.set_defaults(command="register-config-schema")

    vc = sub.add_parser("validate-config", help="Validate config")
    vc.add_argument("schema_name")
    vc.add_argument("--config", help='JSON config')
    vc.set_defaults(command="validate-config")

    sc = sub.add_parser("snapshot-config", help="Snapshot config")
    sc.add_argument("container_id")
    sc.add_argument("--name", default="baseline")
    sc.set_defaults(command="snapshot-config")

    dcd = sub.add_parser("detect-config-drift", help="Detect config drift")
    dcd.add_argument("container_id")
    dcd.add_argument("--name", default="baseline")
    dcd.set_defaults(command="detect-config-drift")

    lcs = sub.add_parser("list-config-snapshots", help="List config snapshots")
    lcs.add_argument("container_id")
    lcs.set_defaults(command="list-config-snapshots")

    ecp = sub.add_parser("enforce-config-policy", help="Enforce config policy")
    ecp.add_argument("container_id")
    ecp.add_argument("--policy", help='JSON policy')
    ecp.set_defaults(command="enforce-config-policy")

    # RBAC subcommands
    cr = sub.add_parser("create-role", help="Create role")
    cr.add_argument("name")
    cr.add_argument("permissions", nargs="+")
    cr.set_defaults(command="create-role")

    cu = sub.add_parser("create-rbac-user", help="Create user")
    cu.add_argument("name")
    cu.add_argument("--roles", nargs="+")
    cu.set_defaults(command="create-rbac-user")

    ar = sub.add_parser("assign-role", help="Assign role")
    ar.add_argument("user_name")
    ar.add_argument("role_name")
    ar.set_defaults(command="assign-role")

    rr = sub.add_parser("revoke-role", help="Revoke role")
    rr.add_argument("user_name")
    rr.add_argument("role_name")
    rr.set_defaults(command="revoke-role")

    cp = sub.add_parser("check-permission", help="Check permission")
    cp.add_argument("user_name")
    cp.add_argument("permission")
    cp.set_defaults(command="check-permission")

    gup = sub.add_parser("get-user-permissions", help="Get user permissions")
    gup.add_argument("user_name")
    gup.set_defaults(command="get-user-permissions")

    lru = sub.add_parser("list-rbac-users", help="List users")
    lru.set_defaults(command="list-rbac-users")

    lrr = sub.add_parser("list-rbac-roles", help="List roles")
    lrr.set_defaults(command="list-rbac-roles")

    dru = sub.add_parser("delete-rbac-user", help="Delete user")
    dru.add_argument("name")
    dru.set_defaults(command="delete-rbac-user")

    drr = sub.add_parser("delete-rbac-role", help="Delete role")
    drr.add_argument("name")
    drr.set_defaults(command="delete-rbac-role")

    # Audit stream subcommands
    cas = sub.add_parser("configure-audit-stream", help="Configure audit stream")
    cas.add_argument("name")
    cas.add_argument("--severity-threshold", default="warning", choices=["debug", "info", "warning", "error", "critical"])
    cas.add_argument("--alert-channels", nargs="+")
    cas.set_defaults(command="configure-audit-stream")

    eae = sub.add_parser("emit-audit-event", help="Emit audit event")
    eae.add_argument("stream_name")
    eae.add_argument("event_type")
    eae.add_argument("--message", default="")
    eae.add_argument("--severity", default="info")
    eae.set_defaults(command="emit-audit-event")

    gss = sub.add_parser("get-stream-status", help="Get stream status")
    gss.add_argument("name")
    gss.set_defaults(command="get-stream-status")

    las = sub.add_parser("list-audit-streams", help="List audit streams")
    las.set_defaults(command="list-audit-streams")

    das = sub.add_parser("disable-audit-stream", help="Disable audit stream")
    das.add_argument("name")
    das.set_defaults(command="disable-audit-stream")

    eas = sub.add_parser("enable-audit-stream", help="Enable audit stream")
    eas.add_argument("name")
    eas.set_defaults(command="enable-audit-stream")

    daas = sub.add_parser("delete-audit-stream", help="Delete audit stream")
    daas.add_argument("name")
    daas.set_defaults(command="delete-audit-stream")

    ass = sub.add_parser("audit-stream-summary", help="Audit stream summary")
    ass.set_defaults(command="audit-stream-summary")

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
    hc = sub.add_parser("health-checks", help="Health check management")
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

    il = sub.add_parser("images-create-layer",
                        help="Create a new image layer")
    il.add_argument("base_path", help="Path to base image")
    il.add_argument("layer_name", help="Layer name")
    il.add_argument("--changes", default=None,
                    help='JSON list of {op, path, content} dicts')
    il.set_defaults(command="images-create-layer")

    ill = sub.add_parser("images-list-layers",
                         help="List layers for an image")
    ill.add_argument("image_path", help="Path to image")
    ill.set_defaults(command="images-list-layers")

    ilr = sub.add_parser("images-remove-layer",
                         help="Remove a layer from an image")
    ilr.add_argument("image_path", help="Path to image")
    ilr.add_argument("layer_name", help="Layer name to remove")
    ilr.set_defaults(command="images-remove-layer")

    idf = sub.add_parser("images-diff",
                         help="Diff two images")
    idf.add_argument("image_a_path", help="Path to first image")
    idf.add_argument("image_b_path", help="Path to second image")
    idf.set_defaults(command="images-diff")

    rp = sub.add_parser("registry-pull",
                        help="Pull image from HTTP registry")
    rp.add_argument("registry_url", help="Registry base URL")
    rp.add_argument("image_name", help="Image name")
    rp.add_argument("--tag", default="latest", help="Image tag")
    rp.set_defaults(command="registry-pull")

    rpu = sub.add_parser("registry-push",
                         help="Push image to HTTP registry")
    rpu.add_argument("image_path", help="Local image path")
    rpu.add_argument("registry_url", help="Registry base URL")
    rpu.add_argument("image_name", help="Image name")
    rpu.add_argument("--tag", default="latest", help="Image tag")
    rpu.set_defaults(command="registry-push")

    rc = sub.add_parser("registry-catalog",
                        help="List images in registry")
    rc.add_argument("registry_url", help="Registry base URL")
    rc.set_defaults(command="registry-catalog")

    crn = sub.add_parser("cluster-register-node",
                         help="Register a node in the cluster")
    crn.add_argument("node_id", help="Node identifier")
    crn.add_argument("node_url", help="Node IPC URL")
    crn.add_argument("--labels", default=None,
                     help='JSON dict of labels')
    crn.add_argument("--capacity", default=None,
                     help='JSON dict of resource capacity')
    crn.set_defaults(command="cluster-register-node")

    cun = sub.add_parser("cluster-unregister-node",
                         help="Remove a node from the cluster")
    cun.add_argument("node_id", help="Node identifier")
    cun.set_defaults(command="cluster-unregister-node")

    chb = sub.add_parser("cluster-heartbeat",
                         help="Send node heartbeat")
    chb.add_argument("node_id", help="Node identifier")
    chb.add_argument("--status", default="active",
                     help="Node status (default: active)")
    chb.set_defaults(command="cluster-heartbeat")

    cn = sub.add_parser("cluster-nodes",
                        help="List cluster nodes")
    cn.set_defaults(command="cluster-nodes")

    cs = sub.add_parser("cluster-status",
                        help="Cluster status summary")
    cs.set_defaults(command="cluster-status")

    csc = sub.add_parser("cluster-schedule",
                         help="Schedule a container to a node")
    csc.add_argument("--config", dest="container_config",
                     default=None,
                     help='JSON container config')
    csc.add_argument("--strategy", default="least_loaded",
                     choices=["least_loaded", "spread", "round_robin"],
                     help="Scheduling strategy")
    csc.set_defaults(command="cluster-schedule")

    ccl = sub.add_parser("cluster-containers",
                         help="List containers across all nodes")
    ccl.set_defaults(command="cluster-containers")

    cdn = sub.add_parser("cluster-drain-node",
                         help="Drain a node (evict containers)")
    cdn.add_argument("node_id", help="Node identifier")
    cdn.add_argument("--timeout", dest="timeout_s", type=float,
                     default=30.0, help="Timeout in seconds")
    cdn.set_defaults(command="cluster-drain-node")

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

    # Cost attribution
    cat = sub.add_parser("configure-cost-attribution", help="Configure cost attribution for a container")
    cat.add_argument("container_id")
    cat.add_argument("--cost-per-hour", type=float, default=0.0)
    cat.add_argument("--billing-tag", default="default")
    cat.add_argument("--team", default="unassigned")
    cat.add_argument("--currency", default="USD")
    cat.set_defaults(command="configure-cost-attribution")

    gpc = sub.add_parser("get-pod-cost", help="Get cost for a container")
    gpc.add_argument("container_id")
    gpc.set_defaults(command="get-pod-cost")

    cbr = sub.add_parser("chargeback-report", help="Generate chargeback report")
    cbr.add_argument("--team", default=None)
    cbr.set_defaults(command="chargeback-report")

    fco = sub.add_parser("fleet-cost-overview", help="Fleet-wide cost overview")
    fco.set_defaults(command="fleet-cost-overview")

    # Network simulation
    snp = sub.add_parser("simulate-network-policy", help="Simulate network policy")
    snp.add_argument("source_container_id")
    snp.add_argument("dest_container_id")
    snp.add_argument("--dest-port", type=int, default=80)
    snp.add_argument("--protocol", default="tcp")
    snp.set_defaults(command="simulate-network-policy")

    wip = sub.add_parser("what-if-policy-change", help="What-if policy analysis")
    wip.add_argument("source_container_id")
    wip.add_argument("dest_container_id")
    wip.add_argument("--dest-port", type=int, default=80)
    wip.add_argument("--protocol", default="tcp")
    wip.add_argument("--new-policy-effect", default="deny")
    wip.set_defaults(command="what-if-policy-change")

    # Lifecycle hooks
    rlh = sub.add_parser("register-lifecycle-hook", help="Register a lifecycle hook")
    rlh.add_argument("container_id")
    rlh.add_argument("phase", choices=["pre-start", "post-start", "pre-stop", "post-stop", "pre-restart", "post-restart"])
    rlh.add_argument("action")
    rlh.add_argument("--hook-command", nargs="+")
    rlh.add_argument("--timeout-s", type=float, default=30.0)
    rlh.add_argument("--on-failure", default="rollback")
    rlh.set_defaults(command="register-lifecycle-hook")

    elh = sub.add_parser("execute-lifecycle-hooks", help="Execute lifecycle hooks")
    elh.add_argument("container_id")
    elh.add_argument("phase")
    elh.set_defaults(command="execute-lifecycle-hooks")

    llh = sub.add_parser("list-lifecycle-hooks", help="List lifecycle hooks")
    llh.add_argument("container_id")
    llh.set_defaults(command="list-lifecycle-hooks")

    # Admission webhooks
    rwh = sub.add_parser("register-webhook", help="Register admission webhook")
    rwh.add_argument("name")
    rwh.add_argument("--type", dest="webhook_type", default="validating", choices=["validating", "mutating"])
    rwh.add_argument("--url", default="")
    rwh.add_argument("--failure-policy", default="fail", choices=["fail", "ignore"])
    rwh.set_defaults(command="register-webhook")

    ead = sub.add_parser("evaluate-admission", help="Evaluate container admission")
    ead.add_argument("webhook_name")
    ead.add_argument("container_id")
    ead.add_argument("--operation", default="CREATE", choices=["CREATE", "UPDATE", "DELETE"])
    ead.set_defaults(command="evaluate-admission")

    wst = sub.add_parser("webhook-status", help="Get webhook status")
    wst.add_argument("webhook_name")
    wst.set_defaults(command="webhook-status")

    lwh = sub.add_parser("list-webhooks", help="List admission webhooks")
    lwh.set_defaults(command="list-webhooks")

    twh = sub.add_parser("toggle-webhook", help="Enable/disable webhook")
    twh.add_argument("webhook_name")
    twh.add_argument("--enabled", type=lambda x: x.lower() == 'true', default=True)
    twh.set_defaults(command="toggle-webhook")

    dwh = sub.add_parser("delete-webhook", help="Delete webhook")
    dwh.add_argument("webhook_name")
    dwh.set_defaults(command="delete-webhook")

    # Supply chain
    ris = sub.add_parser("register-image-signature", help="Register image signature")
    ris.add_argument("image_ref")
    ris.add_argument("digest")
    ris.add_argument("signer")
    ris.add_argument("--type", dest="signature_type", default="cosign")
    ris.set_defaults(command="register-image-signature")

    sbp = sub.add_parser("set-build-provenance", help="Set SLSA build provenance")
    sbp.add_argument("image_ref")
    sbp.add_argument("builder_id")
    sbp.add_argument("--build-config-uri", default="")
    sbp.add_argument("--source-uri", default="")
    sbp.add_argument("--source-hash", default="")
    sbp.set_defaults(command="set-build-provenance")

    ver = sub.add_parser("verify-image", help="Verify image supply chain")
    ver.add_argument("image_ref")
    ver.add_argument("--require-provenance", type=lambda x: x.lower() == 'true', default=False)
    ver.set_defaults(command="verify-image")

    scs = sub.add_parser("supply-chain-status", help="Supply chain verification status")
    scs.set_defaults(command="supply-chain-status")

    gip = sub.add_parser("image-provenance", help="Get image provenance details")
    gip.add_argument("image_ref")
    gip.set_defaults(command="image-provenance")

    # Resource pools
    crp = sub.add_parser("create-resource-pool", help="Create resource pool")
    crp.add_argument("name")
    crp.add_argument("--memory-mb", type=int, dest="total_memory_mb", default=1024)
    crp.add_argument("--cpu-shares", type=int, dest="total_cpu_shares", default=1024)
    crp.add_argument("--pids", type=int, dest="total_pids", default=256)
    crp.set_defaults(command="create-resource-pool")

    jrp = sub.add_parser("join-resource-pool", help="Join resource pool")
    jrp.add_argument("pool_name")
    jrp.add_argument("container_id")
    jrp.add_argument("--memory-mb", type=int, default=0)
    jrp.add_argument("--cpu-shares", type=int, default=0)
    jrp.add_argument("--pids", type=int, default=0)
    jrp.set_defaults(command="join-resource-pool")

    lrp = sub.add_parser("leave-resource-pool", help="Leave resource pool")
    lrp.add_argument("pool_name")
    lrp.add_argument("container_id")
    lrp.set_defaults(command="leave-resource-pool")

    rpr = sub.add_parser("reserve-pool-resources", help="Reserve pool resources")
    rpr.add_argument("pool_name")
    rpr.add_argument("reservation_name")
    rpr.add_argument("--memory-mb", type=int, default=0)
    rpr.add_argument("--cpu-shares", type=int, default=0)
    rpr.add_argument("--pids", type=int, default=0)
    rpr.add_argument("--ttl", type=float, dest="ttl_seconds", default=3600.0)
    rpr.set_defaults(command="reserve-pool-resources")

    pst = sub.add_parser("pool-status", help="Get pool status")
    pst.add_argument("pool_name")
    pst.set_defaults(command="pool-status")

    lrps = sub.add_parser("list-resource-pools", help="List resource pools")
    lrps.set_defaults(command="list-resource-pools")

    drp = sub.add_parser("delete-resource-pool", help="Delete resource pool")
    drp.add_argument("pool_name")
    drp.set_defaults(command="delete-resource-pool")

    # GPU scheduling
    rgpu = sub.add_parser("register-gpu", help="Register GPU device")
    rgpu.add_argument("device_id")
    rgpu.add_argument("--type", dest="device_type", default="nvidia")
    rgpu.add_argument("--memory-mb", type=int, default=8192)
    rgpu.add_argument("--compute-cap", default="8.9")
    rgpu.set_defaults(command="register-gpu")

    agpu = sub.add_parser("assign-gpu", help="Assign GPU to container")
    agpu.add_argument("device_id")
    agpu.add_argument("container_id")
    agpu.add_argument("--timeslice-ms", type=int, default=0)
    agpu.add_argument("--memory-limit-mb", type=int, default=None)
    agpu.set_defaults(command="assign-gpu")

    rgpu2 = sub.add_parser("release-gpu", help="Release GPU")
    rgpu2.add_argument("device_id")
    rgpu2.set_defaults(command="release-gpu")

    gpus = sub.add_parser("gpu-status", help="GPU device status")
    gpus.set_defaults(command="gpu-status")

    # Policy engine
    cpolicy = sub.add_parser("create-policy", help="Create policy")
    cpolicy.add_argument("name")
    cpolicy.add_argument("--effect", default="deny", choices=["deny", "audit"])
    cpolicy.add_argument("--enforcement", default="strict", choices=["strict", "permissive"])
    cpolicy.set_defaults(command="create-policy")

    epolicy = sub.add_parser("evaluate-policy", help="Evaluate policy")
    epolicy.add_argument("policy_name")
    epolicy.add_argument("container_id")
    epolicy.set_defaults(command="evaluate-policy")

    lpolicy = sub.add_parser("list-policies", help="List policies")
    lpolicy.set_defaults(command="list-policies")

    tpolicy = sub.add_parser("toggle-policy", help="Enable/disable policy")
    tpolicy.add_argument("policy_name")
    tpolicy.add_argument("--enabled", type=lambda x: x.lower() == 'true', default=True)
    tpolicy.set_defaults(command="toggle-policy")

    pv = sub.add_parser("policy-violations", help="Policy violation summary")
    pv.add_argument("policy_name")
    pv.set_defaults(command="policy-violations")

    # Container composition
    ccomp = sub.add_parser("create-composition", help="Create composition")
    ccomp.add_argument("name")
    ccomp.add_argument("--container-ids", nargs="+", default=[]) 
    ccomp.set_defaults(command="create-composition")

    asc = sub.add_parser("add-sidecar", help="Add sidecar to composition")
    asc.add_argument("composition_name")
    asc.add_argument("sidecar_container_id")
    asc.add_argument("--mount-paths", nargs="+", default=None)
    asc.set_defaults(command="add-sidecar")

    aic = sub.add_parser("add-init-container", help="Add init container")
    aic.add_argument("composition_name")
    aic.add_argument("init_container_id")
    aic.add_argument("--phase", default="pre-start")
    aic.set_defaults(command="add-init-container")

    cstatus = sub.add_parser("composition-status", help="Composition status")
    cstatus.add_argument("composition_name")
    cstatus.set_defaults(command="composition-status")

    lcomp = sub.add_parser("list-compositions", help="List compositions")
    lcomp.set_defaults(command="list-compositions")

    scomp = sub.add_parser("start-composition", help="Start composition")
    scomp.add_argument("composition_name")
    scomp.set_defaults(command="start-composition")

    stcomp = sub.add_parser("stop-composition", help="Stop composition")
    stcomp.add_argument("composition_name")
    stcomp.set_defaults(command="stop-composition")

    # Workload identity
    cwi = sub.add_parser("create-workload-identity", help="Create workload identity")
    cwi.add_argument("name")
    cwi.add_argument("spiffe_id")
    cwi.add_argument("--trust-domain", default="nyrqis.local")
    cwi.add_argument("--sans", nargs="+")
    cwi.add_argument("--ttl", type=float, dest="ttl_seconds", default=3600.0)
    cwi.set_defaults(command="create-workload-identity")

    vwi = sub.add_parser("validate-workload-identity", help="Validate workload identity")
    vwi.add_argument("name")
    vwi.set_defaults(command="validate-workload-identity")

    rwi = sub.add_parser("rotate-workload-identity", help="Rotate workload identity")
    rwi.add_argument("name")
    rwi.add_argument("--ttl", type=float, dest="new_ttl_seconds", default=None)
    rwi.set_defaults(command="rotate-workload-identity")

    revwi = sub.add_parser("revoke-workload-identity", help="Revoke workload identity")
    revwi.add_argument("name")
    revwi.set_defaults(command="revoke-workload-identity")

    lwi = sub.add_parser("list-workload-identities", help="List workload identities")
    lwi.set_defaults(command="list-workload-identities")

    # Vulnerability scanning
    si = sub.add_parser("scan-image", help="Scan image for vulnerabilities")
    si.add_argument("image_ref")
    si.add_argument("--severity-filter", nargs="+")
    si.set_defaults(command="scan-image")

    svp = sub.add_parser("set-vuln-policy", help="Set vulnerability policy")
    svp.add_argument("name")
    svp.add_argument("--block-on-critical", type=lambda x: x.lower() == 'true', default=True)
    svp.add_argument("--block-on-high", type=lambda x: x.lower() == 'true', default=False)
    svp.add_argument("--max-vulns", type=int, default=0)
    svp.set_defaults(command="set-vuln-policy")

    evp = sub.add_parser("evaluate-vuln-policy", help="Evaluate vulnerability policy")
    evp.add_argument("policy_name")
    evp.add_argument("image_ref")
    evp.set_defaults(command="evaluate-vuln-policy")

    sh = sub.add_parser("scan-history", help="Vulnerability scan history")
    sh.set_defaults(command="scan-history")

    # eBPF observability
    rebpf = sub.add_parser("register-ebpf-hook", help="Register eBPF hook")
    rebpf.add_argument("name")
    rebpf.add_argument("--type", dest="hook_type", default="kprobe", choices=["kprobe", "kretprobe", "tracepoint", "uprobe", "perf_event", "cgroup"])
    rebpf.add_argument("--target", default="")
    rebpf.add_argument("--metric-name", default="")
    rebpf.add_argument("--description", default="")
    rebpf.set_defaults(command="register-ebpf-hook")

    aebpf = sub.add_parser("attach-ebpf-hook", help="Attach eBPF hook")
    aebpf.add_argument("hook_name")
    aebpf.add_argument("--container-id", default=None)
    aebpf.set_defaults(command="attach-ebpf-hook")

    rebpf2 = sub.add_parser("record-ebpf-event", help="Record eBPF event")
    rebpf2.add_argument("hook_name")
    rebpf2.add_argument("--value", type=float, default=1.0)
    rebpf2.set_defaults(command="record-ebpf-event")

    ebpfm = sub.add_parser("ebpf-metrics", help="Get eBPF metrics")
    ebpfm.add_argument("--metric-name", default=None)
    ebpfm.set_defaults(command="ebpf-metrics")

    lebpf = sub.add_parser("list-ebpf-hooks", help="List eBPF hooks")
    lebpf.set_defaults(command="list-ebpf-hooks")

    # Topology scheduling
    rtn = sub.add_parser("register-topology-node", help="Register topology node")
    rtn.add_argument("node_id")
    rtn.add_argument("--region", default="us-east-1")
    rtn.add_argument("--zone", default="us-east-1a")
    rtn.set_defaults(command="register-topology-node")

    stc = sub.add_parser("set-topology-constraints", help="Set topology constraints")
    stc.add_argument("container_id")
    stc.add_argument("--preferred-region", default=None)
    stc.set_defaults(command="set-topology-constraints")

    fsn = sub.add_parser("find-suitable-nodes", help="Find suitable nodes")
    fsn.add_argument("container_id")
    fsn.set_defaults(command="find-suitable-nodes")

    swt = sub.add_parser("schedule-with-topology", help="Schedule with topology")
    swt.add_argument("container_id")
    swt.set_defaults(command="schedule-with-topology")

    tv = sub.add_parser("topology-view", help="Topology view")
    tv.set_defaults(command="topology-view")

    # Chaos monkey
    ccs = sub.add_parser("create-chaos-schedule", help="Create chaos schedule")
    ccs.add_argument("name")
    ccs.add_argument("--fault-types", nargs="+", default=["process_kill"])
    ccs.add_argument("--cron", dest="schedule_cron", default="0 2 * * *")
    ccs.add_argument("--targets", nargs="+")
    ccs.add_argument("--max-targets", type=int, default=1)
    ccs.set_defaults(command="create-chaos-schedule")

    eca = sub.add_parser("execute-chaos-attack", help="Execute chaos attack")
    eca.add_argument("schedule_name")
    eca.add_argument("--dry-run", action="store_true", default=False)
    eca.set_defaults(command="execute-chaos-attack")

    css = sub.add_parser("chaos-schedule-status", help="Chaos schedule status")
    css.add_argument("schedule_name")
    css.set_defaults(command="chaos-schedule-status")

    lcs = sub.add_parser("list-chaos-schedules", help="List chaos schedules")
    lcs.set_defaults(command="list-chaos-schedules")

    cl = sub.add_parser("chaos-log", help="Chaos execution log")
    cl.add_argument("--limit", type=int, default=10)
    cl.set_defaults(command="chaos-log")

    # Multi-arch
    rmi = sub.add_parser("register-multiarch-image", help="Register multi-arch image")
    rmi.add_argument("name")
    rmi.add_argument("--default-arch", default="linux/amd64")
    rmi.set_defaults(command="register-multiarch-image")

    ria = sub.add_parser("resolve-image-arch", help="Resolve image for arch")
    ria.add_argument("name")
    ria.add_argument("--target-arch", default="linux/amd64")
    ria.set_defaults(command="resolve-image-arch")

    lmi = sub.add_parser("list-multiarch-images", help="List multi-arch images")
    lmi.set_defaults(command="list-multiarch-images")

    bm = sub.add_parser("build-matrix", help="Build matrix")
    bm.add_argument("name")
    bm.set_defaults(command="build-matrix")

    # Runtime classes
    rrc = sub.add_parser("register-runtime-class", help="Register runtime class")
    rrc.add_argument("name")
    rrc.add_argument("--handler", dest="runtime_handler", default="nyrqis-default")
    rrc.add_argument("--immutable-rootfs", action="store_true", default=False)
    rrc.set_defaults(command="register-runtime-class")

    arc = sub.add_parser("assign-runtime-class", help="Assign runtime class")
    arc.add_argument("container_id")
    arc.add_argument("class_name")
    arc.set_defaults(command="assign-runtime-class")

    lrc = sub.add_parser("list-runtime-classes", help="List runtime classes")
    lrc.set_defaults(command="list-runtime-classes")

    # Replay debugging
    crt = sub.add_parser("create-replay-trace", help="Create replay trace")
    crt.add_argument("container_id")
    crt.add_argument("trace_name")
    crt.set_defaults(command="create-replay-trace")

    rre = sub.add_parser("record-replay-event", help="Record replay event")
    rre.add_argument("trace_name")
    rre.add_argument("event_type")
    rre.set_defaults(command="record-replay-event")

    srt = sub.add_parser("stop-replay-trace", help="Stop replay trace")
    srt.add_argument("trace_name")
    srt.set_defaults(command="stop-replay-trace")

    rt = sub.add_parser("replay-trace", help="Replay a trace")
    rt.add_argument("trace_name")
    rt.add_argument("--from-snapshot", type=int, default=None)
    rt.set_defaults(command="replay-trace")

    lrt = sub.add_parser("list-replay-traces", help="List replay traces")
    lrt.set_defaults(command="list-replay-traces")

    # Cost optimization
    ct = sub.add_parser("cost-trends", help="Cost trends analysis")
    ct.add_argument("container_id")
    ct.add_argument("--lookback-days", type=int, default=7)
    ct.set_defaults(command="cost-trends")

    cr = sub.add_parser("cost-recommendations", help="Cost optimization recommendations")
    cr.add_argument("container_id")
    cr.set_defaults(command="cost-recommendations")

    fcr = sub.add_parser("fleet-cost-report", help="Fleet cost optimization report")
    fcr.set_defaults(command="fleet-cost-report")

    fc = sub.add_parser("forecast-cost", help="Cost forecasting")
    fc.add_argument("container_id")
    fc.add_argument("--days", type=int, dest="days_ahead", default=30)
    fc.set_defaults(command="forecast-cost")

    # Affinity rules
    car = sub.add_parser("create-affinity-rule", help="Create affinity rule")
    car.add_argument("name")
    car.add_argument("--type", dest="rule_type", default="affinity", choices=["affinity", "anti-affinity"])
    car.add_argument("--required", action="store_true", default=False)
    car.add_argument("--weight", type=int, default=100)
    car.set_defaults(command="create-affinity-rule")

    ea = sub.add_parser("evaluate-affinity", help="Evaluate affinity rules")
    ea.add_argument("container_id")
    ea.set_defaults(command="evaluate-affinity")

    # Priority classes
    cpc = sub.add_parser("create-priority-class", help="Create priority class")
    cpc.add_argument("name")
    cpc.add_argument("--value", type=int, default=0)
    cpc.add_argument("--preemption-policy", default="PreemptLowerPriority", choices=["PreemptLowerPriority", "Never"])
    cpc.set_defaults(command="create-priority-class")

    apc = sub.add_parser("assign-priority-class", help="Assign priority class")
    apc.add_argument("container_id")
    apc.add_argument("class_name")
    apc.set_defaults(command="assign-priority-class")

    ep = sub.add_parser("evaluate-preemption", help="Evaluate preemption")
    ep.add_argument("evictor_id")
    ep.add_argument("victim_id")
    ep.set_defaults(command="evaluate-preemption")

    lpc = sub.add_parser("list-priority-classes", help="List priority classes")
    lpc.set_defaults(command="list-priority-classes")

    # Autoscaling
    cas = sub.add_parser("configure-autoscaler", help="Configure autoscaler")
    cas.add_argument("container_id")
    cas.add_argument("--min", type=int, dest="min_replicas", default=1)
    cas.add_argument("--max", type=int, dest="max_replicas", default=10)
    cas.add_argument("--target-cpu", type=float, dest="target_cpu_pct", default=70.0)
    cas.add_argument("--predictive", action="store_true", default=False)
    cas.set_defaults(command="configure-autoscaler")

    eas = sub.add_parser("evaluate-autoscaling", help="Evaluate autoscaling")
    eas.add_argument("container_id")
    eas.add_argument("--cpu", type=float, dest="current_cpu_pct", default=0.0)
    eas.add_argument("--memory", type=float, dest="current_memory_pct", default=0.0)
    eas.set_defaults(command="evaluate-autoscaling")

    ast = sub.add_parser("autoscaler-status", help="Autoscaler status")
    ast.add_argument("container_id")
    ast.set_defaults(command="autoscaler-status")

    las = sub.add_parser("list-autoscalers", help="List autoscalers")
    las.set_defaults(command="list-autoscalers")

    # Image signing
    si = sub.add_parser("sign-image", help="Sign image with Cosign")
    si.add_argument("image_ref")
    si.add_argument("--key-ref", default="cosign-key")
    si.set_defaults(command="sign-image")

    vis = sub.add_parser("verify-image-signature", help="Verify image signature")
    vis.add_argument("image_ref")
    vis.add_argument("--key-ref", default=None)
    vis.set_defaults(command="verify-image-signature")

    lsi = sub.add_parser("list-signed-images", help="List signed images")
    lsi.set_defaults(command="list-signed-images")

    # OpenTelemetry
    cot = sub.add_parser("configure-otel", help="Configure OpenTelemetry exporter")
    cot.add_argument("--endpoint", default="http://localhost:4317")
    cot.add_argument("--protocol", default="grpc", choices=["grpc", "http"])
    cot.add_argument("--service-name", default="nyrqis")
    cot.set_defaults(command="configure-otel")

    rom = sub.add_parser("record-otel-metric", help="Record OTel metric")
    rom.add_argument("name")
    rom.add_argument("value", type=float)
    rom.add_argument("--type", dest="metric_type", default="gauge", choices=["gauge", "counter", "histogram"])
    rom.set_defaults(command="record-otel-metric")

    ots = sub.add_parser("otel-status", help="OTel exporter status")
    ots.set_defaults(command="otel-status")

    # Federation
    rfc = sub.add_parser("register-fed-cluster", help="Register federation cluster")
    rfc.add_argument("cluster_id")
    rfc.add_argument("--endpoint", default="")
    rfc.add_argument("--region", default="us-east-1")
    rfc.add_argument("--zone", default="us-east-1a")
    rfc.set_defaults(command="register-fed-cluster")

    gs = sub.add_parser("global-schedule", help="Schedule workload globally")
    gs.add_argument("workload_name")
    gs.add_argument("--cpu", type=int, dest="required_cpu", default=1)
    gs.add_argument("--memory", type=int, dest="required_memory_mb", default=256)
    gs.add_argument("--region", dest="preferred_region", default=None)
    gs.set_defaults(command="global-schedule")

    fs = sub.add_parser("federation-status", help="Federation status")
    fs.set_defaults(command="federation-status")

    lfc = sub.add_parser("list-fed-clusters", help="List federation clusters")
    lfc.set_defaults(command="list-fed-clusters")

    fw = sub.add_parser("fed-workloads", help="List federated workloads")
    fw.set_defaults(command="fed-workloads")

    # Batch jobs
    cbj = sub.add_parser("create-batch-job", help="Create batch job with DAG")
    cbj.add_argument("name")
    cbj.add_argument("--max-parallel", type=int, default=1)
    cbj.set_defaults(command="create-batch-job")

    ebj = sub.add_parser("execute-batch-job", help="Execute batch job")
    ebj.add_argument("job_name")
    ebj.set_defaults(command="execute-batch-job")

    bjs = sub.add_parser("batch-job-status", help="Batch job status")
    bjs.add_argument("job_name")
    bjs.set_defaults(command="batch-job-status")

    lbj = sub.add_parser("list-batch-jobs", help="List batch jobs")
    lbj.set_defaults(command="list-batch-jobs")

    # Memory compaction
    cmc = sub.add_parser("configure-memory-compaction", help="Configure memory compaction")
    cmc.add_argument("container_id")
    cmc.add_argument("--threshold", type=float, default=70.0)
    cmc.add_argument("--auto-compact", action="store_true", default=True)
    cmc.set_defaults(command="configure-memory-compaction")

    rmc = sub.add_parser("run-memory-compaction", help="Run memory compaction")
    rmc.add_argument("container_id")
    rmc.set_defaults(command="run-memory-compaction")

    rmd = sub.add_parser("run-memory-defragmentation", help="Run memory defragmentation")
    rmd.add_argument("container_id")
    rmd.set_defaults(command="run-memory-defragmentation")

    # Filesystem snapshots
    fss = sub.add_parser("fs-snapshot", help="Create filesystem snapshot")
    fss.add_argument("container_id")
    fss.add_argument("snapshot_name")
    fss.set_defaults(command="fs-snapshot")

    fsr = sub.add_parser("fs-restore", help="Restore filesystem snapshot")
    fsr.add_argument("snapshot_name")
    fsr.add_argument("--target-container-id", default=None)
    fsr.set_defaults(command="fs-restore")

    lfs = sub.add_parser("list-fs-snapshots", help="List filesystem snapshots")
    lfs.add_argument("--container-id", default=None)
    lfs.set_defaults(command="list-fs-snapshots")

    cfs = sub.add_parser("compare-fs-snapshots", help="Compare filesystem snapshots")
    cfs.add_argument("snapshot_a")
    cfs.add_argument("snapshot_b")
    cfs.set_defaults(command="compare-fs-snapshots")


    # -- network monitoring --
    cnm = sub.add_parser("create-network-monitor", help="Create network monitor")
    cnm.add_argument("--container-id", required=True)
    cnm.add_argument("--targets", nargs="*", help="Target host:port pairs")
    cnm.add_argument("--interval-s", type=float, default=1.0)
    cnm.set_defaults(command="create-network-monitor")

    rnl = sub.add_parser("record-network-latency", help="Record network latency")
    rnl.add_argument("--monitor-id", required=True)
    rnl.add_argument("--target", required=True)
    rnl.add_argument("--latency-ms", type=float, required=True)
    rnl.set_defaults(command="record-network-latency")

    rbw = sub.add_parser("record-bandwidth", help="Record bandwidth")
    rbw.add_argument("--monitor-id", required=True)
    rbw.add_argument("--target", required=True)
    rbw.add_argument("--bytes-sent", type=int, required=True)
    rbw.add_argument("--bytes-received", type=int, required=True)
    rbw.set_defaults(command="record-bandwidth")

    nls = sub.add_parser("network-latency-stats", help="Get latency stats")
    nls.add_argument("--monitor-id", required=True)
    nls.set_defaults(command="network-latency-stats")

    bws = sub.add_parser("bandwidth-stats", help="Get bandwidth stats")
    bws.add_argument("--monitor-id", required=True)
    bws.set_defaults(command="bandwidth-stats")

    dna = sub.add_parser("detect-network-anomalies", help="Detect network anomalies")
    dna.add_argument("--monitor-id", required=True)
    dna.add_argument("--z-threshold", type=float, default=3.0)
    dna.set_defaults(command="detect-network-anomalies")

    snm = sub.add_parser("stop-network-monitor", help="Stop network monitor")
    snm.add_argument("--monitor-id", required=True)
    snm.set_defaults(command="stop-network-monitor")

    # -- IO profiling --
    cip = sub.add_parser("create-io-profiler", help="Create IO profiler")
    cip.add_argument("--container-id", required=True)
    cip.add_argument("--sample-interval-s", type=float, default=0.5)
    cip.set_defaults(command="create-io-profiler")

    ris = sub.add_parser("record-io-sample", help="Record IO sample")
    ris.add_argument("--profiler-id", required=True)
    ris.add_argument("--path", required=True)
    ris.add_argument("--io-op", required=True, choices=["read", "write"])
    ris.add_argument("--bytes-transferred", type=int, required=True)
    ris.add_argument("--latency-ms", type=float, required=True)
    ris.set_defaults(command="record-io-sample")

    iop = sub.add_parser("io-profile", help="Get IO profile")
    iop.add_argument("--profiler-id", required=True)
    iop.set_defaults(command="io-profile")

    dih = sub.add_parser("detect-io-hotspots", help="Detect IO hotspots")
    dih.add_argument("--profiler-id", required=True)
    dih.add_argument("--top-n", type=int, default=5)
    dih.set_defaults(command="detect-io-hotspots")

    # -- storage cache --
    csc = sub.add_parser("create-storage-cache", help="Create storage cache")
    csc.add_argument("--container-id", required=True)
    csc.add_argument("--max-size-mb", type=float, default=256.0)
    csc.add_argument("--eviction-policy", default="lru", choices=["lru", "lfu"])
    csc.set_defaults(command="create-storage-cache")

    cp = sub.add_parser("cache-put", help="Put to cache")
    cp.add_argument("--cache-id", required=True)
    cp.add_argument("--key", required=True)
    cp.add_argument("--data", required=True)
    cp.add_argument("--ttl-s", type=float, default=300.0)
    cp.set_defaults(command="cache-put")

    cg = sub.add_parser("cache-get", help="Get from cache")
    cg.add_argument("--cache-id", required=True)
    cg.add_argument("--key", required=True)
    cg.set_defaults(command="cache-get")

    cs = sub.add_parser("cache-stats", help="Get cache stats")
    cs.add_argument("--cache-id", required=True)
    cs.set_defaults(command="cache-stats")

    ci = sub.add_parser("cache-invalidate", help="Invalidate cache")
    ci.add_argument("--cache-id", required=True)
    ci.add_argument("--key", default=None)
    ci.set_defaults(command="cache-invalidate")

    # -- audit chain --
    cac = sub.add_parser("create-audit-chain", help="Create audit chain")
    cac.add_argument("--container-id", required=True)
    cac.set_defaults(command="create-audit-chain")

    aae = sub.add_parser("append-audit-entry", help="Append audit entry")
    aae.add_argument("--chain-id", required=True)
    aae.add_argument("--audit-op", required=True)
    aae.add_argument("--result", default=None)
    aae.set_defaults(command="append-audit-entry")

    vac = sub.add_parser("verify-audit-chain", help="Verify audit chain")
    vac.add_argument("--chain-id", required=True)
    vac.set_defaults(command="verify-audit-chain")

    ae = sub.add_parser("audit-entry", help="Get audit entry")
    ae.add_argument("--chain-id", required=True)
    ae.add_argument("--index", type=int, required=True)
    ae.set_defaults(command="audit-entry")

    asum = sub.add_parser("audit-summary", help="Get audit summary")
    asum.add_argument("--chain-id", required=True)
    asum.set_defaults(command="audit-summary")

    eac = sub.add_parser("export-audit-chain", help="Export audit chain")
    eac.add_argument("--chain-id", required=True)
    eac.add_argument("--format", default="json", choices=["json", "csv"])
    eac.set_defaults(command="export-audit-chain")

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
