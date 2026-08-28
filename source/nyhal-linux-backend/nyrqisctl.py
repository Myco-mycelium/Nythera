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
    raise ValueError(f"unknown command: {command!r}")


# -- human formatting (pure, unit-testable) ----------------------------

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

    cr = csub.add_parser("restore", help="Restore a container from a checkpoint file")
    cr.add_argument("checkpoint_file", help="Path to the checkpoint JSON file")
    cr.set_defaults(command="containers-restore")

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
