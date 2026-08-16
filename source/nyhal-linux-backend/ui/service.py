#!/usr/bin/env python3
"""NuiService — the NUI (.nstudio) import gate on the operator control
plane (ADR-0025, implementation_plan.md §4.3/§4.5).

An operator-only service (mirroring ``ipc/control.py``'s gate: the
server authenticates the operator by kernel-attached uid, and this
service additionally refuses any sender that is not the operator
identity). Containers cannot reach it — loading or even validating a
shell design is an operator action, so the container capability model
deliberately does not apply.

Operations (JSON request → JSON reply over CALL/REPLY):

- ``{"service": "nui", "op": "nui_validate", "document": "…"}`` —
  run the import gate on the document: validate against the NUI
  contract tables (NFS-001 §4–§9) and report a summary (version,
  screens, component/behavior/binding counts, engine). Fails closed:
  any ``NstudioError`` becomes an ``ok: false`` reply with the
  validation message.
- ``{"service": "nui", "op": "nui_load", "document": "…"}`` — validate
  AND, on success, persist the document as the daemon's shell design at
  ``<state_dir>/ui/shell.nstudio`` (atomic write). Rejected without a
  configured state directory.
- ``{"service": "nui", "op": "nui_current"}`` — report what the
  daemon has loaded: ``loaded: false`` when no design has been
  persisted yet (honest, not an error), or the persisted design's
  summary (re-imported through the gate on every call) plus its path.
  A persisted design that no longer re-imports cleanly is reported as
  ``loaded: true, valid: false`` with the validation message — the
  operator sees the stale design instead of a silent failure.

The import gate itself routes through the Rust crate
(``ui/nstudio_codec.py``) when it is available and falls back to the
pure-Python floor (``ui/nstudio.py``) otherwise — the standard ADR-0020
routing, with identical exception semantics on both sides.

Transport bound: the document rides one CALL/REPLY datagram, so it is
capped at ``NUI_DOCUMENT_MAX_BYTES`` (48 KiB — under the transport's 60
KiB datagram budget). Larger designs are the ADR-0024 wire-streaming
follow-on, not this increment.

References:
- NFS-001: NUI schema (vocabulary §4, contracts §5, behaviors §7,
  bindings §8, versioning §9)
- ADR-0025: NUI runtime consumption decision
- implementation_plan.md §4.3: IPC; §4.5: service bring-up
"""

import json
import logging
import os
from typing import Any, Dict, Optional

from ipc.transport import DEFAULT_OPERATOR_ID  # operator carve-out (below)
from . import nstudio
from . import nstudio_codec

logger = logging.getLogger(__name__)

# One .nstudio document per CALL: comfortably under the transport's
# 60 KiB datagram budget with room for the JSON envelope (the largest
# shipped design today, the 1440x900 shell, is ~23 KiB).
NUI_DOCUMENT_MAX_BYTES = 48 * 1024


class NuiService:
    """Operator-only NUI import gate on the datagram transport.

    Attach to a bound ``IPCDatagramServer`` with :meth:`attach`; the
    server's router dispatches ``service == "nui"`` here. The handler
    never raises into the serve loop: an unexpected error becomes an
    ``internal error`` reply.
    """

    SERVICE_NAME = "nyrqis.backend.nui"
    SERVICE_VERSION = "1.0"

    def __init__(self, state_dir: Optional[str] = None,
                 operator_id: Optional[str] = None) -> None:
        # Where ``nui_load`` persists the shell design
        # (``<state_dir>/ui/shell.nstudio``); None disables ``nui_load``.
        self.state_dir = state_dir
        # None → synced from the server on attach, so the operator gate
        # always matches the server's auth identity by construction.
        self.operator_id = operator_id
        self._server = None

    def attach(self, server) -> "NuiService":
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
        if sender != self.operator_id:
            self._reply(server, sender_path, msg.message_id, {
                "ok": False,
                "error": "forbidden: the NUI service is operator-only",
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
            if op == "nui_validate":
                self._nui_validate(server, sender_path, msg.message_id,
                                   request)
            elif op == "nui_load":
                self._nui_load(server, sender_path, msg.message_id,
                               request)
            elif op == "nui_current":
                self._nui_current(server, sender_path, msg.message_id,
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

    # -- operations -------------------------------------------------

    def _nui_validate(self, server, sender_path: str, call_id: str,
                      request: Dict[str, Any]) -> None:
        document = request.get("document")
        if not isinstance(document, str):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "document must be a JSON string",
            })
            return
        if len(document.encode("utf-8")) > NUI_DOCUMENT_MAX_BYTES:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"document exceeds the {NUI_DOCUMENT_MAX_BYTES}-byte "
                         "per-call budget (wire streaming is a follow-on)",
            })
            return
        ok, detail = self._validate_document(document)
        if not ok:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"nui_validate failed: {detail}",
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "service": self.SERVICE_NAME,
            "service_version": self.SERVICE_VERSION,
            "summary": detail,
        })

    def _nui_load(self, server, sender_path: str, call_id: str,
                  request: Dict[str, Any]) -> None:
        document = request.get("document")
        if not isinstance(document, str):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "document must be a JSON string",
            })
            return
        if len(document.encode("utf-8")) > NUI_DOCUMENT_MAX_BYTES:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"document exceeds the {NUI_DOCUMENT_MAX_BYTES}-byte "
                         "per-call budget (wire streaming is a follow-on)",
            })
            return
        if not self.state_dir:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "nui_load requires a daemon state directory "
                         "(--state-file)",
            })
            return
        ok, detail = self._validate_document(document)
        if not ok:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": f"nui_load failed: {detail}",
            })
            return
        target = self._persist(document)
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "service": self.SERVICE_NAME,
            "service_version": self.SERVICE_VERSION,
            "summary": detail,
            "path": target,
        })

    # -- internals ----------------------------------------------------

    def _nui_current(self, server, sender_path: str, call_id: str,
                     request: Dict[str, Any]) -> None:
        """Report the daemon's loaded shell design (see module docstring)."""
        if not self.state_dir:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "nui_current requires a daemon state directory "
                         "(--state-file)",
            })
            return
        target = os.path.join(self.state_dir, "ui", "shell.nstudio")
        if not os.path.exists(target):
            self._reply(server, sender_path, call_id, {
                "ok": True,
                "loaded": False,
                "service": self.SERVICE_NAME,
                "service_version": self.SERVICE_VERSION,
            })
            return
        try:
            with open(target, "r", encoding="utf-8") as handle:
                document = handle.read()
            ok, detail = self._validate_document(document)
        except OSError as exc:
            self._reply(server, sender_path, call_id, {
                "ok": True,
                "loaded": True,
                "valid": False,
                "path": target,
                "error": f"cannot read persisted design: {exc}",
            })
            return
        if not ok:
            self._reply(server, sender_path, call_id, {
                "ok": True,
                "loaded": True,
                "valid": False,
                "path": target,
                "error": f"persisted design no longer validates: {detail}",
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "loaded": True,
            "valid": True,
            "path": target,
            "service": self.SERVICE_NAME,
            "service_version": self.SERVICE_VERSION,
            "summary": detail,
        })

    def _validate_document(self, document: str):
        """Run the import gate (crate when available, floor otherwise)
        and return ``(True, summary_dict)`` or ``(False, message)``."""
        engine = "rust"
        try:
            if nstudio_codec.available():
                nstudio_codec.validate(document)
            else:
                engine = "python"
            doc = nstudio.loads(document)
        except nstudio.NstudioError as exc:
            return False, str(exc)
        summary = {
            "version": doc.version,
            "engine": engine,
            "screens": [s.id for s in doc.screens],
            "components": len(doc.component_ids()),
            "behaviors": len(doc.behaviors),
            "bindings": len(doc.bindings),
        }
        return True, summary

    def _persist(self, document: str) -> str:
        """Atomically store the shell design under the state dir."""
        ui_dir = os.path.join(self.state_dir, "ui")
        os.makedirs(ui_dir, exist_ok=True)
        target = os.path.join(ui_dir, "shell.nstudio")
        tmp = os.path.join(
            ui_dir, f".shell.nstudio.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(document)
        os.replace(tmp, target)
        return target

    @staticmethod
    def _reply(server, sender_path: str, call_id: str,
               payload: Dict[str, Any]) -> None:
        server.reply(
            sender_path, call_id,
            json.dumps(payload).encode("utf-8"),
        )


__all__ = ["NuiService", "NUI_DOCUMENT_MAX_BYTES"]
