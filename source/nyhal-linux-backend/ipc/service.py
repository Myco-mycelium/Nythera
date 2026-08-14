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

Capability enforcement fails closed: ``status`` is denied when no
``CapabilityManager`` is attached (the service cannot verify the grant
it needs) or when the caller lacks ``CAP_SYSTEM_INFO``; ``ping`` needs
nothing beyond what the server already enforced.

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
    ) -> None:
        self.capability_manager = capability_manager
        if backend_version is None:
            try:
                from backend import __version__ as _bv
                backend_version = _bv
            except ImportError:
                backend_version = "unknown"
        self.backend_version = backend_version
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

    def _status(self, server, sender_path: str, call_id: str,
                sender: str) -> None:
        if self.capability_manager is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: no capability manager attached",
            })
            return
        # Lazy import: ipc must not depend on backend eagerly (the
        # established pattern in ipc/core.py).
        from backend.capability import Capability
        if not self.capability_manager.validate_operation(
            sender, Capability.CAP_SYSTEM_INFO
        ):
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
        })

    @staticmethod
    def _reply(server, sender_path: str, call_id: str,
               body: Dict[str, Any]) -> None:
        server.reply(
            sender_path,
            call_id,
            json.dumps(body, sort_keys=True).encode("utf-8"),
        )


__all__ = ["BackendStatusService"]
