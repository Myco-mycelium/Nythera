#!/usr/bin/env python3
"""
Storage Service — NyVault's first increment (ADR-0022).

The daemon-hosted storage surface: a container (or the operator) CALLs
``service: "storage"`` on the daemon's main socket and obtains a named,
capability-gated **volume**. Volumes are backed by a real NyFS root
(``fuse.nyfs``) created under the daemon's vault directory — this
increment owns the *access and lifecycle* boundary (handles, ownership,
gates); the in-container byte path (the FUSE passthrough whose read/
write ops are themselves CALLs) is the ADR's next increment, as is the
key-management layer (ADR-0023).

Trust model (ADR-0022):

- Every op requires ``CAP_STORAGE_VOLUME`` (a new NPS-011 capability,
  NOT a default grant — a default container can see volumes only via
  explicit grant), enforced fail-closed through the same
  ``CapabilityManager`` the server uses.
- The operator (``DEFAULT_OPERATOR_ID``) is always authorized, matching
  the status service's carve-out: the transport already authenticated
  it by the kernel-attached uid, and such a process has full control of
  the daemon anyway.
- Volumes are **creator-scoped** for this increment: only the creating
  container (or the operator) may open one. A cross-container grant
  matrix is future work.

Operations (JSON request → JSON reply over CALL/REPLY):

- ``{"op": "volume_create", "name": str}`` — create a NyFS-backed
  volume; returns ``{volume_id, name}``.
- ``{"op": "volume_open", "volume_id": str}`` — bind the volume to the
  caller; returns an opaque ``handle`` (the access token for the
  subsequent ops; a random id, never derivable from the volume id).
- ``{"op": "volume_list"}`` — the volumes the caller may open (its own
  creations).
- ``{"op": "volume_close", "handle": str}`` — release a handle.
- ``{"op": "volume_info", "handle": str}`` — the volume's metadata and
  backing-store state (block size, bytes persisted).

References:
- ADR-0022 (NyVault — storage as a daemon-hosted service)
- NPS-011 (capability registry; CAP_STORAGE_VOLUME)
- NPS-004 (NyFS storage guarantees)
"""

import json
import logging
import os
import uuid
from typing import Any, Dict, Optional

from .transport import DEFAULT_OPERATOR_ID  # the server is the auth boundary

logger = logging.getLogger(__name__)


class StorageService:
    """NyVault's first increment: capability-gated named volumes.

    Attach to the daemon's ``IPCDatagramServer`` (its ``_server`` is
    set by the ``ServiceRouter``) and register on the router under
    ``\"storage\"``. ``vault_dir`` is where volume roots live (the
    daemon owns it; ``None`` disables NyFS backing — the registry then
    tracks volume metadata only, for tests and read-only hosts).
    """

    SERVICE_NAME = "nyrqis.backend.storage"
    SERVICE_VERSION = "1.0"

    def __init__(self,
                 capability_manager: Optional[Any] = None,
                 vault_dir: Optional[str] = None) -> None:
        self.capability_manager = capability_manager
        self.vault_dir = vault_dir
        self._server = None
        # volume_id -> record (metadata + optional NyFS root)
        self._volumes: Dict[str, Dict[str, Any]] = {}
        self._by_name: Dict[str, str] = {}
        # handle -> {"volume_id", "container"}
        self._handles: Dict[str, Dict[str, str]] = {}

    def attach(self, server) -> "StorageService":
        """Give the service the server to reply through (the router
        owns ``on_call``; this records the reply path)."""
        self._server = server
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
            if op == "volume_create":
                self._volume_create(server, sender_path, msg.message_id,
                                    sender, request)
            elif op == "volume_open":
                self._volume_open(server, sender_path, msg.message_id,
                                  sender, request)
            elif op == "volume_list":
                self._volume_list(server, sender_path, msg.message_id,
                                  sender)
            elif op == "volume_close":
                self._volume_close(server, sender_path, msg.message_id,
                                   sender, request)
            elif op == "volume_info":
                self._volume_info(server, sender_path, msg.message_id,
                                  sender, request)
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

    # -- authorization ----------------------------------------------

    def _authorized(self, sender: str, capability) -> bool:
        """True when the caller holds ``capability``. Fails closed: no
        ``CapabilityManager`` attached means no grant can be verified,
        so nothing is authorized. The operator (trusted-uid, full
        control of the daemon anyway) is always authorized — the same
        carve-out the status service uses."""
        if sender == DEFAULT_OPERATOR_ID:
            return True
        if self.capability_manager is None:
            return False
        return self.capability_manager.validate_operation(
            sender, capability)

    @staticmethod
    def _reply(server, sender_path: str, call_id: str,
               body: Dict[str, Any]) -> None:
        server.reply(
            sender_path, call_id,
            json.dumps(body, sort_keys=True).encode("utf-8"),
        )

    # -- operations -------------------------------------------------

    def _volume_create(self, server, sender_path: str, call_id: str,
                       sender: str, request: Dict[str, Any]) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        name = request.get("name")
        if not isinstance(name, str) or not name.strip():
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "name must be a non-empty string",
            })
            return
        name = name.strip()
        if name in self._by_name:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "volume already exists: %r" % (name,),
            })
            return
        volume_id = uuid.uuid4().hex
        record = {"id": volume_id, "name": name, "created_by": sender}
        # Back the volume with a real NyFS root under the daemon's
        # vault directory (lazy import — ipc must not depend on the
        # backend eagerly). The root is created empty; data flows in
        # with the byte-path increment.
        if self.vault_dir:
            try:
                from fuse.nyfs import NyFSFilesystem  # noqa: F401
                os.makedirs(self.vault_dir, exist_ok=True)
                record["nyfs"] = NyFSFilesystem(
                    os.path.join(self.vault_dir, volume_id + ".nyfs"))
            except Exception as e:  # noqa: BLE001 - a vault failure is a create failure
                logger.error("ipc: %s could not back volume: %s",
                             self.SERVICE_NAME, e)
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "volume_create failed: %s" % (e,),
                })
                return
        self._volumes[volume_id] = record
        self._by_name[name] = volume_id
        logger.info("ipc: %s created volume %s (%s) for %s",
                    self.SERVICE_NAME, volume_id, name, sender)
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volume_id": volume_id,
            "name": name,
        })

    def _volume_open(self, server, sender_path: str, call_id: str,
                     sender: str, request: Dict[str, Any]) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        volume_id = request.get("volume_id")
        record = self._volumes.get(volume_id)
        if record is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown volume: %r" % (volume_id,),
            })
            return
        # Creator-scoped for this increment (ADR-0022: the cross-container
        # grant matrix is future work).
        if sender != DEFAULT_OPERATOR_ID and record["created_by"] != sender:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: volume %s is not yours" % (volume_id[:8],),
            })
            return
        handle = uuid.uuid4().hex
        self._handles[handle] = {"volume_id": volume_id,
                                 "container": sender}
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "handle": handle,
            "volume_id": volume_id,
        })

    def _volume_list(self, server, sender_path: str, call_id: str,
                     sender: str) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        volumes = []
        for record in self._volumes.values():
            if sender == DEFAULT_OPERATOR_ID or \
                    record["created_by"] == sender:
                volumes.append({
                    "id": record["id"],
                    "name": record["name"],
                    "created_by": record["created_by"],
                })
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volumes": volumes,
        })

    def _volume_close(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        handle = request.get("handle")
        binding = self._handles.pop(handle, None)
        if binding is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown handle",
            })
            return
        if sender != DEFAULT_OPERATOR_ID and binding["container"] != sender:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: handle is not yours",
            })
            return
        self._reply(server, sender_path, call_id, {"ok": True})

    def _volume_info(self, server, sender_path: str, call_id: str,
                     sender: str, request: Dict[str, Any]) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        handle = request.get("handle")
        binding = self._handles.get(handle)
        if binding is None or (
            sender != DEFAULT_OPERATOR_ID
            and binding["container"] != sender
        ):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: unknown or foreign handle",
            })
            return
        record = self._volumes[binding["volume_id"]]
        nyfs = record.get("nyfs")
        info = {
            "ok": True,
            "volume_id": record["id"],
            "name": record["name"],
            "created_by": record["created_by"],
        }
        if nyfs is not None:
            info["backend"] = "nyfs"
            info["block_size"] = nyfs.block_size
            info["bytes_persisted"] = sum(
                p.stat().st_size
                for p in (nyfs.base_path / "state" / "blocks").glob("*")
            ) if (nyfs.base_path / "state" / "blocks").is_dir() else 0
        else:
            info["backend"] = "registry-only"
        self._reply(server, sender_path, call_id, info)


__all__ = ["StorageService"]
