#!/usr/bin/env python3
"""
Backend Status Service — the first real container-facing service on the
transport (implementation_plan.md §4.3).

A service is a CALL handler attached to an ``IPCDatagramServer``. The
server has already authenticated the sender (kernel ``SCM_CREDENTIALS``
pid → container id, ``ipc/registry.py``) and enforced ``CAP_IPC_SEND``
before the handler runs, so a service sees the *authenticated* sender
and enforces its own per-operation capabilities on top.

Operations (JSON request → JSON reply, both over the CALL/REPLY
primitive):

- ``{"op": "ping"}`` — requires nothing beyond the server's checks.
  Verifies the whole chain (transport + kernel identity + reply path).
- ``{"op": "status"}`` — requires ``CAP_SYSTEM_INFO`` (a default
  capability, NPS-011). Reports the backend version, the service's
  uptime, and the *caller's own* container id and capability set.
- ``{"op": "health"}`` — requires ``CAP_SYSTEM_INFO``. Plan §4.5
  health check: serve-loop liveness, container load, IPC registry
  size, state persistence, and a crash-recovery *summary* (previous
  daemon pid + orphan count — the full manifest stays in the daemon's
  state file for operator review). The diagnostic a systemd health
  probe or an operator reads.

Capability enforcement fails closed: ``status``/``health`` are denied
when no ``CapabilityManager`` is attached (the service cannot verify
the grant it needs) or when the caller lacks ``CAP_SYSTEM_INFO``;
``ping`` needs nothing beyond what the server already enforced.

References:
- NPS-017 §4.3: IPC Semantics (CALL/REPLY)
- NPS-011: Capability Registry (CAP_SYSTEM_INFO)
- NPS-003 §5.4: Capability Validation in IPC
- implementation_plan.md §4.3: IPC Implementation
"""

import json
import logging
import time
from typing import Any, Dict, Optional

from .transport import DEFAULT_OPERATOR_ID  # operator carve-out (below)

logger = logging.getLogger(__name__)


class BackendStatusService:
    """Container-facing status endpoint on the datagram transport.

    Attach to a bound ``IPCDatagramServer`` with :meth:`attach`; the
    server's ``on_call`` handler then serves this service's operations.
    The handler never raises into the serve loop: an unexpected error
    becomes an ``internal error`` reply, so one bad request cannot kill
    the serving thread.

    ``backend_version`` defaults to the ``backend`` package's
    ``__version__``; operators should pass the implementation version
    explicitly when the service reports it.
    """

    SERVICE_NAME = "nyrqis.backend.status"
    SERVICE_VERSION = "1.0"

    def __init__(
        self,
        capability_manager: Optional[Any] = None,
        backend_version: Optional[str] = None,
        daemon: Optional[Any] = None,
    ) -> None:
        self.capability_manager = capability_manager
        if backend_version is None:
            try:
                from backend import __version__ as _bv
                backend_version = _bv
            except ImportError:
                backend_version = "unknown"
        self.backend_version = backend_version
        # The runnable daemon (``nyrqis_backend.StatusServiceHost``) the
        # health op reads shared state from: serve-loop thread,
        # container manager, IPC registry, persistent state. Optional —
        # a bare service (tests, embedded use) reports the fields it
        # can, and the rest stay ``None``.
        self.daemon = daemon
        self._started_at = time.time()
        self._server = None

    def attach(self, server) -> "BackendStatusService":
        """Attach as ``server``'s CALL handler (returns self, so
        ``service.attach(server)`` reads as one step)."""
        self._server = server
        server.on_call = self._on_call
        logger.info("ipc: %s attached to %s", self.SERVICE_NAME,
                    server.endpoint.path)
        return self

    # -- handler ----------------------------------------------------

    def _on_call(self, msg, sender: str, sender_path: str) -> None:
        server = self._server
        if server is None:
            logger.error("ipc: %s has no server to reply through",
                         self.SERVICE_NAME)
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
            if op == "ping":
                self._reply(server, sender_path, msg.message_id, {
                    "ok": True,
                    "service": self.SERVICE_NAME,
                    "service_version": self.SERVICE_VERSION,
                    "echo": "pong",
                    "container": sender,
                })
            elif op == "status":
                self._status(server, sender_path, msg.message_id, sender)
            elif op == "health":
                self._health(server, sender_path, msg.message_id, sender)
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

    def _authorized(self, sender: str, capability) -> bool:
        """True when the caller holds ``capability``. Fails closed: no
        ``CapabilityManager`` attached means no grant can be verified,
        so nothing is authorized.

        The operator (``DEFAULT_OPERATOR_ID`` — the daemon's own user
        on the trusted-uid path) is always authorized: the transport
        already authenticated it by the kernel-attached uid, and such
        a process has full control of the daemon anyway (it could kill
        or restart it), so the container capability model deliberately
        does not apply to it — the same model the control service
        uses (see ``ipc/transport.py`` "Operator identity")."""
        if sender == DEFAULT_OPERATOR_ID:
            return True
        if self.capability_manager is None:
            return False
        # Lazy import: ipc must not depend on backend eagerly (the
        # established pattern in ipc/core.py).
        from backend.capability import Capability  # noqa: F401
        return self.capability_manager.validate_operation(
            sender, capability)

    def _vault_summary(self) -> Optional[Dict[str, Any]]:
        """The vault aggregate for status/health — CACHED figures only
        (the ledger + physical bytes as of the last commit; NO tree
        walk, so status stays O(volumes) instead of paying the §28
        refresh). None when the daemon has no storage service."""
        storage = getattr(getattr(self, "daemon", None), "storage", None)
        if storage is None:
            return None
        volumes = getattr(storage, "_volumes", None) or {}
        total_logical = 0
        total_physical = 0
        warned = 0
        for record in volumes.values():
            total_logical += sum(record.get("usage", {}).values())
            total_physical += int(record.get("physical_bytes", 0))
            warned += sum(1 for w in record.get("warnings", {}).values()
                          if w is not None)
        return {
            "volumes": len(volumes),
            "logical_bytes": total_logical,
            "physical_bytes": total_physical,
            "warned_containers": warned,
        }

    def _status(self, server, sender_path: str, call_id: str,
                sender: str) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_SYSTEM_INFO):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_SYSTEM_INFO required",
            })
            return
        caps = self.capability_manager.get_capabilities(sender)
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "service": self.SERVICE_NAME,
            "service_version": self.SERVICE_VERSION,
            "backend_version": self.backend_version,
            "uptime_s": round(time.time() - self._started_at, 3),
            "container": sender,
            "capabilities": sorted(c.value for c in caps),
            "vault": self._vault_summary(),
        })

    def _health(self, server, sender_path: str, call_id: str,
                sender: str) -> None:
        """Liveness + load diagnostics (plan §4.5). Gated like
        ``status`` (``CAP_SYSTEM_INFO``). Fields the daemon cannot
        provide (no daemon attached) are reported as ``None`` rather
        than guessed."""
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_SYSTEM_INFO):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_SYSTEM_INFO required",
            })
            return
        daemon = self.daemon
        containers = None
        if daemon is not None and getattr(
            daemon, "container_manager", None
        ) is not None:
            known = list(daemon.container_manager.containers.values())
            running = [c for c in known if getattr(
                getattr(c, "state", None), "value", None) == "running"]
            containers = {"known": len(known), "running": len(running)}
        registry_entries = None
        if daemon is not None and getattr(
            daemon, "ipc_registry", None
        ) is not None:
            registry_entries = len(daemon.ipc_registry)
        serve_loop_alive = True
        if daemon is not None:
            thread = getattr(daemon, "_thread", None)
            serve_loop_alive = thread is None or thread.is_alive()
        # Crash-recovery SUMMARY only: the previous daemon's pid and
        # orphan count. The full orphan manifest (ids, commands, pids)
        # is operator material — it stays in the daemon's state file
        # and is never shipped to callers (CAP_SYSTEM_INFO is a default
        # grant, so per-container detail must not ride this op).
        recovery = None
        if daemon is not None and daemon._recovery is not None:
            recovery = {
                "previous_pid": daemon._recovery.get("previous_pid"),
                "containers_left": len(
                    daemon._recovery.get("containers_left", [])),
            }
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "service": self.SERVICE_NAME,
            "service_version": self.SERVICE_VERSION,
            "backend_version": self.backend_version,
            "uptime_s": round(time.time() - self._started_at, 3),
            "serve_loop_alive": serve_loop_alive,
            "containers": containers,
            "ipc_registry_entries": registry_entries,
            "state_persisted": bool(
                getattr(daemon, "state", None)
            ) if daemon is not None else False,
            "recovery": recovery,
            "container": sender,
            "vault": self._vault_summary(),
        })

    @staticmethod
    def _reply(server, sender_path: str, call_id: str,
               body: Dict[str, Any]) -> None:
        server.reply(
            sender_path,
            call_id,
            json.dumps(body, sort_keys=True).encode("utf-8"),
        )


class ServiceRouter:
    """Dispatches the server's CALL handler across registered services.

    A daemon hosts several services on one socket; the router is the
    ``on_call`` handler that picks the service from the request's
    ``"service"`` field. Requests without a ``service`` field default
    to ``"status"`` (back-compatible with the status-only clients).

    Registered services must follow the :class:`BackendStatusService`
    contract: an ``_on_call(msg, sender, sender_path)`` method that
    replies through ``self._server``. Wiring prefers the service's
    ``attach(server)`` method when present (the status and control
    services use it, e.g. to sync the operator identity); the minimal
    fallback sets ``_server`` directly. The router never raises into
    the serve loop: an unknown service gets an error REPLY, and a
    service bug becomes an ``internal error`` REPLY.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, Any] = {}
        self._server = None

    @staticmethod
    def _wire(service, server) -> None:
        attach = getattr(service, "attach", None)
        if callable(attach):
            service.attach(server)
        else:
            service._server = server  # minimal contract: expose _server

    def register(self, name: str, service) -> "ServiceRouter":
        """Route requests with ``service == name`` to ``service``."""
        self._handlers[name] = service
        if self._server is not None:
            self._wire(service, self._server)
        logger.info("ipc: service %r registered on the router", name)
        return self

    def attach(self, server) -> "ServiceRouter":
        """Become the server's CALL handler and give every registered
        service the server to reply through (each service's ``attach``
        is the preferred wiring path)."""
        self._server = server
        for service in self._handlers.values():
            self._wire(service, server)
        server.on_call = self._on_call
        logger.info("ipc: service router attached to %s", server.endpoint.path)
        return self

    def _on_call(self, msg, sender: str, sender_path: str) -> None:
        server = self._server
        if server is None:
            logger.error("ipc: service router has no server to reply through")
            return
        name = None
        try:
            try:
                payload = json.loads(msg.payload.decode("utf-8") or "{}")
                name = payload.get("service") if isinstance(payload, dict) else None
            except (ValueError, UnicodeDecodeError):
                name = None
            service = self._handlers.get(name or "status")
            if service is None:
                server.reply(
                    sender_path, msg.message_id,
                    json.dumps({"ok": False,
                                "error": "unknown service: %r" % (name,)})
                    .encode("utf-8"),
                )
                return
            service._on_call(msg, sender, sender_path)
        except Exception:  # noqa: BLE001 - a service bug must not kill the serve loop
            logger.exception("ipc: service %r handler error", name)
            try:
                server.reply(
                    sender_path, msg.message_id,
                    json.dumps({"ok": False, "error": "internal error"})
                    .encode("utf-8"),
                )
            except Exception:  # noqa: BLE001 - even the error reply can fail
                logger.exception("ipc: could not send error reply")


__all__ = ["BackendStatusService", "ServiceRouter"]
