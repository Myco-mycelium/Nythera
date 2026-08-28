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
            elif op == "container_checkpoint":
                self._container_checkpoint(server, sender_path,
                                           msg.message_id, request)
            elif op == "container_restore":
                self._container_restore(server, sender_path,
                                        msg.message_id, request)
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
        container = self.container_manager.containers.get(container_id)
        if container is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown container: %r" % (container_id,),
            })
            return
        try:
            procs = self.container_manager.container_top(container)
        except Exception as e:  # noqa: BLE001
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container_top failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "container_id": container.id,
            "processes": procs,
            "count": len(procs),
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
