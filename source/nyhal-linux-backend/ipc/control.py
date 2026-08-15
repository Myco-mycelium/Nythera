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
