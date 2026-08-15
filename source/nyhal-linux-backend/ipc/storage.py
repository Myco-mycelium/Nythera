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

import base64
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, Optional

from .transport import DEFAULT_OPERATOR_ID  # the server is the auth boundary

logger = logging.getLogger(__name__)

# Byte-path payload cap: the transport's datagram buffers are 64 KiB
# (CALL and REPLY alike), so a single volume op must stay under that
# once JSON + base64 are accounted for. 32 KiB of data → ~43.7 KiB of
# base64 + ~1 KiB of envelope. Streaming (the FUSE passthrough byte
# path of ADR-0022) is the future path; this increment pages with
# ``offset``/``size``.
_MAX_IO_BYTES = 32 * 1024

# Volume paths are flat-ish blob names under the volume root: absolute,
# no ``..`` segments (the NyFS tree is the volume's own namespace, but a
# path is still a path), no trailing slash.
_PATH_RE = re.compile(r"^/(?:[^/]+/)*[^/]+$")


class StorageLockedError(Exception):
    """A vault op needs the unlocked KEK but the daemon has none
    (serve with --vault-key-file and the passphrase)."""


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
                 vault_dir: Optional[str] = None,
                 kek: Optional[Any] = None) -> None:
        self.capability_manager = capability_manager
        self.vault_dir = vault_dir
        # The daemon-held KEK (a keys.KeyHandle — an opaque handle into
        # the Rust keys crate's table when present, the floor's handle
        # otherwise; ADR-0023). ``None`` runs the vault UNENCRYPTED
        # (volumes get no wrapped DEK — the crate-less/plaintext mode).
        self.kek = kek
        self._server = None
        # volume_id -> record (metadata + optional NyFS root + optional
        # dek/wrapped_dek). The ``dek`` is the in-memory plaintext key
        # for the block layer; ``wrapped_dek`` is the persisted at-rest
        # envelope (crypto-shredded on delete).
        self._volumes: Dict[str, Dict[str, Any]] = {}
        self._by_name: Dict[str, str] = {}
        # handle -> {"volume_id", "container"}
        self._handles: Dict[str, Dict[str, str]] = {}
        self._load_state()

    # -- persistence -------------------------------------------------

    def _state_path(self) -> str:
        return os.path.join(self.vault_dir, "volumes.json")

    def _load_state(self) -> None:
        """Rebuild the volume registry from ``volumes.json`` (volumes
        and their wrapped DEKs survive a daemon restart; handles and
        plaintext DEKs are never persisted — the DEK is re-unwrapped
        from the KEK when a volume is opened)."""
        if not self.vault_dir:
            return
        path = self._state_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (ValueError, OSError) as e:
            logger.error("ipc: %s could not read %s: %s",
                         self.SERVICE_NAME, path, e)
            return
        for rec in state.get("volumes", []):
            try:
                volume_id = rec["id"]
                self._volumes[volume_id] = {
                    "id": volume_id,
                    "name": rec["name"],
                    "created_by": rec.get("created_by", ""),
                    "wrapped_dek": base64.b64decode(rec["wrapped_dek"])
                    if rec.get("wrapped_dek") else None,
                    "encrypted": bool(rec.get("wrapped_dek")),
                    # dek + nyfs are lazily re-derived on open
                    "dek": None,
                    "nyfs": None,
                }
                self._by_name[self._volumes[volume_id]["name"]] = volume_id
            except (KeyError, ValueError, TypeError) as e:
                logger.error("ipc: %s skipped a bad volume record: %s",
                             self.SERVICE_NAME, e)
        if self._volumes:
            logger.info("ipc: %s restored %d volume(s) from %s",
                        self.SERVICE_NAME, len(self._volumes), path)

    def _save_state(self) -> None:
        """Atomically persist the registry (id/name/creator/wrapped
        DEK). Never persists plaintext DEKs or handles."""
        if not self.vault_dir:
            return
        state = {"version": 1, "volumes": [
            {"id": r["id"], "name": r["name"],
             "created_by": r["created_by"],
             "wrapped_dek": base64.b64encode(r["wrapped_dek"]).decode("ascii")
             if r.get("wrapped_dek") else None}
            for r in self._volumes.values()
        ]}
        os.makedirs(self.vault_dir, exist_ok=True)
        path = self._state_path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, sort_keys=True)
            os.replace(tmp, path)
        except OSError as e:
            logger.error("ipc: %s could not persist %s: %s",
                         self.SERVICE_NAME, path, e)

    # -- lazy rehydration -------------------------------------------

    def _ensure_dek(self, record: Dict[str, Any]) -> Optional[bytes]:
        """The volume's plaintext DEK, re-unwrapped from the KEK when
        the record was restored from disk (never persisted plaintext).
        Returns None for a plaintext volume."""
        dek = record.get("dek")
        if dek is not None:
            return dek
        if record.get("wrapped_dek") is None:
            return None
        if self.kek is None:
            raise StorageLockedError(
                "vault locked: the KEK is not unlocked (serve with "
                "--vault-key-file and the passphrase)")
        from backend import keys
        record["dek"] = keys.unwrap(
            self.kek, record["id"].encode("utf-8"), record["wrapped_dek"])
        return record["dek"]

    def _ensure_nyfs(self, record: Dict[str, Any]):
        """The volume's NyFS root — created at volume_create, or
        LOADED from disk when the daemon restarted (the ``.nyfs``
        directory is the durable image; the in-memory inode table is
        rebuilt from it and the DEK re-unwrapped)."""
        if record.get("nyfs") is not None:
            return record["nyfs"]
        if not self.vault_dir:
            return None
        path = os.path.join(self.vault_dir, record["id"] + ".nyfs")
        if not os.path.isdir(path):
            return None
        from fuse.nyfs import NyFSFilesystem
        fs = NyFSFilesystem.load(path)
        dek = self._ensure_dek(record)
        fs.dek = dek
        fs._ad = record["id"].encode("utf-8")
        record["nyfs"] = fs
        return fs

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
            elif op == "volume_write":
                self._volume_write(server, sender_path, msg.message_id,
                                   sender, request)
            elif op == "volume_read":
                self._volume_read(server, sender_path, msg.message_id,
                                  sender, request)
            elif op == "volume_snapshot":
                self._volume_snapshot(server, sender_path, msg.message_id,
                                     sender, request)
            elif op == "volume_snapshots":
                self._volume_snapshots(server, sender_path, msg.message_id,
                                       sender, request)
            elif op == "volume_delete":
                self._volume_delete(server, sender_path, msg.message_id,
                                    sender, request)
            elif op == "volume_getattr":
                self._volume_getattr(server, sender_path, msg.message_id,
                                     sender, request)
            elif op == "volume_readdir":
                self._volume_readdir(server, sender_path, msg.message_id,
                                     sender, request)
            elif op == "volume_mkdir":
                self._volume_mkdir(server, sender_path, msg.message_id,
                                   sender, request)
            elif op == "volume_mknod":
                self._volume_mknod(server, sender_path, msg.message_id,
                                   sender, request)
            elif op == "volume_unlink":
                self._volume_unlink(server, sender_path, msg.message_id,
                                    sender, request)
            elif op == "volume_rmdir":
                self._volume_rmdir(server, sender_path, msg.message_id,
                                   sender, request)
            elif op == "volume_rename":
                self._volume_rename(server, sender_path, msg.message_id,
                                    sender, request)
            elif op == "volume_truncate":
                self._volume_truncate(server, sender_path, msg.message_id,
                                      sender, request)
            elif op == "volume_statfs":
                self._volume_statfs(server, sender_path, msg.message_id,
                                    sender, request)
            elif op == "volume_fsync":
                self._volume_fsync(server, sender_path, msg.message_id,
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
        record = {"id": volume_id, "name": name, "created_by": sender,
                  "encrypted": False}
        # ADR-0023: when the daemon holds an unlocked KEK, the volume
        # gets its own random DEK, wrapped with the KEK (ad = the
        # volume id); the wrapped DEK is what persists — the plaintext
        # DEK is held only in the daemon's memory for the block layer.
        if self.kek is not None:
            try:
                from backend import keys
                dek = keys.new_dek()
                record["dek"] = dek
                record["wrapped_dek"] = keys.wrap(
                    self.kek, volume_id.encode("utf-8"), dek)
                record["encrypted"] = True
            except Exception as e:  # noqa: BLE001 - a key failure is a create failure
                logger.error("ipc: %s could not key volume: %s",
                             self.SERVICE_NAME, e)
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "volume_create failed: %s" % (e,),
                })
                return
        # Back the volume with a real NyFS root under the daemon's
        # vault directory (lazy import — ipc must not depend on the
        # backend eagerly). An encrypted volume's root gets the DEK so
        # every block is AEAD-encrypted at rest (checksum-then-encrypt).
        if self.vault_dir:
            try:
                from fuse.nyfs import NyFSFilesystem  # noqa: F401
                os.makedirs(self.vault_dir, exist_ok=True)
                record["nyfs"] = NyFSFilesystem(
                    os.path.join(self.vault_dir, volume_id + ".nyfs"),
                    dek=record.get("dek"), ad=volume_id)
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
        self._save_state()
        logger.info("ipc: %s created volume %s (%s, encrypted=%s) for %s",
                    self.SERVICE_NAME, volume_id, name,
                    record["encrypted"], sender)
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volume_id": volume_id,
            "name": name,
            "encrypted": record["encrypted"],
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
        # Open by id OR by name (operator UX: ``vault open assets``).
        volume_id = request.get("volume_id") or \
            self._by_name.get(request.get("name") or "")
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
        # A restored (encrypted) volume's DEK is re-unwrapped at open,
        # so a locked vault fails here — before any handle exists.
        try:
            self._ensure_dek(record)
        except StorageLockedError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e)})
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
        nyfs = self._ensure_nyfs(record)
        info = {
            "ok": True,
            "volume_id": record["id"],
            "name": record["name"],
            "created_by": record["created_by"],
            "encrypted": bool(record.get("wrapped_dek")),
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

    def _volume_delete(self, server, sender_path: str, call_id: str,
                       sender: str, request: Dict[str, Any]) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        volume_id = request.get("volume_id") or \
            self._by_name.get(request.get("name") or "")
        record = self._volumes.get(volume_id)
        if record is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown volume: %r" % (volume_id,),
            })
            return
        if sender != DEFAULT_OPERATOR_ID and record["created_by"] != sender:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: volume %s is not yours" % (volume_id[:8],),
            })
            return
        # Crypto-shred (ADR-0023): drop every handle, then drop the
        # wrapped DEK + plaintext DEK from the registry — the ciphertext
        # may remain on disk, but with no key path it is unrecoverable.
        for handle, binding in list(self._handles.items()):
            if binding["volume_id"] == volume_id:
                del self._handles[handle]
        name = record["name"]
        del self._volumes[volume_id]
        self._by_name.pop(name, None)
        if self.vault_dir:
            import shutil
            shutil.rmtree(
                os.path.join(self.vault_dir, volume_id + ".nyfs"),
                ignore_errors=True)
        self._save_state()
        logger.info("ipc: %s deleted volume %s (%s) for %s",
                    self.SERVICE_NAME, volume_id, name, sender)
        self._reply(server, sender_path, call_id, {
            "ok": True, "volume_id": volume_id})

    # -- byte path (ADR-0022: the daemon holds the data plane) ------

    def _resolve_handle(self, server, sender_path: str, call_id: str,
                        sender: str, request: Dict[str, Any]):
        """(record, nyfs) for the caller's handle, or None. Validates
        the handle's existence and ownership (creator-scoped like
        open/info); the failure reply is already sent, ``None`` means
        "abort this op". Callers check the capability first."""
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
            return None
        record = self._volumes[binding["volume_id"]]
        try:
            nyfs = self._ensure_nyfs(record)
        except StorageLockedError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False, "error": str(e)})
            return None
        if nyfs is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "volume has no byte backend (registry-only)",
            })
            return None
        return record, nyfs

    @staticmethod
    def _check_path(path: Any) -> bool:
        if not isinstance(path, str) or not _PATH_RE.match(path):
            return False
        # ``..`` must be rejected explicitly: the regex's ``[^/]+``
        # would otherwise admit it as an ordinary segment.
        return ".." not in path.split("/")

    def _volume_write(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        resolved = self._resolve_handle(
            server, sender_path, call_id, sender, request)
        if resolved is None:
            return
        _, nyfs = resolved
        path = request.get("path")
        if not self._check_path(path):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "path must be an absolute volume path "
                          "without '..' or trailing '/'",
            })
            return
        raw = request.get("data_b64")
        try:
            data = base64.b64decode(raw, validate=True) if raw else b""
        except (TypeError, ValueError):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "data_b64 must be valid base64",
            })
            return
        if len(data) > _MAX_IO_BYTES:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "write exceeds the %d-byte per-call limit "
                          "(page with offset)" % _MAX_IO_BYTES,
            })
            return
        offset = request.get("offset", 0)
        if not isinstance(offset, int) or offset < 0:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "offset must be a non-negative integer",
            })
            return
        try:
            from fuse.nyfs import NyFSError
            written = self._nyfs_write(nyfs, path, data, offset)
        except NyFSError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "write failed: %s" % (e,),
            })
            return
        # Durability (ADR-0022: ADR-0019's journal commit is the vault's
        # default): commit the transaction before replying so an ack is
        # a durable write, not a memory-state promise.
        try:
            nyfs.save()
        except Exception as e:  # noqa: BLE001 - a commit failure is a write failure
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "commit failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "bytes_written": written,
            "path": path,
        })

    @staticmethod
    def _nyfs_write(nyfs, path: str, data: bytes, offset: int) -> int:
        """Create-on-write with mkdir -p semantics: a blob store, so a
        first write to a path creates the file (and any missing parent
        directories). NyFS's own inode tree is the namespace — the host
        filesystem is never touched."""
        import errno as _errno
        from fuse.nyfs import NyFSError
        parent, _sep, name = path.rpartition("/")
        if parent:
            _mkdirs(nyfs, parent)
        try:
            nyfs.write(path, data, offset)
        except NyFSError as e:
            if e.errno == _errno.ENOENT:
                nyfs.create_file(path)
                nyfs.write(path, data, offset)
            else:
                raise
        return len(data)

    def _volume_read(self, server, sender_path: str, call_id: str,
                     sender: str, request: Dict[str, Any]) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        resolved = self._resolve_handle(
            server, sender_path, call_id, sender, request)
        if resolved is None:
            return
        _, nyfs = resolved
        path = request.get("path")
        if not self._check_path(path):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "path must be an absolute volume path "
                          "without '..' or trailing '/'",
            })
            return
        offset = request.get("offset", 0)
        if not isinstance(offset, int) or offset < 0:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "offset must be a non-negative integer",
            })
            return
        size = request.get("size", _MAX_IO_BYTES)
        if not isinstance(size, int) or size <= 0 or size > _MAX_IO_BYTES:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "size must be 1..%d (page with offset)"
                          % _MAX_IO_BYTES,
            })
            return
        try:
            from fuse.nyfs import NyFSError
            data = nyfs.read(path, size=size, offset=offset)
        except NyFSError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "read failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "data_b64": base64.b64encode(data).decode("ascii"),
            "path": path,
            "bytes": len(data),
            "offset": offset,
        })

    def _volume_snapshot(self, server, sender_path: str, call_id: str,
                         sender: str, request: Dict[str, Any]) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        resolved = self._resolve_handle(
            server, sender_path, call_id, sender, request)
        if resolved is None:
            return
        record, nyfs = resolved
        name = request.get("name")
        if not isinstance(name, str) or not re.match(r"^[A-Za-z0-9._-]{1,64}$",
                                                     name):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "name must be 1..64 chars of [A-Za-z0-9._-]",
            })
            return
        try:
            snapshot_id = nyfs.create_snapshot(snapshot_id=name)
            nyfs.save()  # persist the snapshot table with the commit
        except Exception as e:  # noqa: BLE001 - a snapshot failure is an op failure
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "snapshot failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "snapshot_id": snapshot_id,
            "volume_id": record["id"],
        })

    def _volume_snapshots(self, server, sender_path: str, call_id: str,
                          sender: str, request: Dict[str, Any]) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        resolved = self._resolve_handle(
            server, sender_path, call_id, sender, request)
        if resolved is None:
            return
        record, nyfs = resolved
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "snapshots": nyfs.list_snapshots(),
            "volume_id": record["id"],
        })

    # -- generic file surface (the FUSE passthrough's backend) ------
    #
    # The passthrough mount (``fuse.vault_mount``) issues FUSE ops as
    # these CALLs; each one is authorized + path-checked server-side,
    # exactly like the byte path. Every NyFSError carries its POSIX
    # errno back to the caller so the mount can re-raise the right
    # ``OSError`` subclass (ENOENT → FileNotFoundError, etc.).

    def _delegate(self, server, sender_path: str, call_id: str,
                  sender: str, request: Dict[str, Any], fn,
                  path_key: str = "path"):
        """Authorize → resolve → path-check → run ``fn(nyfs, **args)``
        → reply. Returns True when a reply was sent (always).
        """
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        resolved = self._resolve_handle(
            server, sender_path, call_id, sender, request)
        if resolved is None:
            return
        _, nyfs = resolved
        args = dict(request)
        args.pop("op", None)
        args.pop("service", None)
        args.pop("handle", None)
        path = args.get(path_key)
        if path is not None:
            if not self._check_path(path):
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "path must be an absolute volume path "
                              "without '..' or trailing '/'",
                })
                return
        try:
            result = fn(nyfs, **args)
        except Exception as e:  # noqa: BLE001 - errno mapping below
            from fuse.nyfs import NyFSError
            if isinstance(e, NyFSError):
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "%s failed: %s" % (request.get("op"), e),
                    "errno": e.errno,
                })
            else:
                logger.error("ipc: %s %s failed: %s",
                             self.SERVICE_NAME, request.get("op"), e)
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "internal error",
                })
            return
        reply = {"ok": True}
        if isinstance(result, dict):
            reply.update(result)
        self._reply(server, sender_path, call_id, reply)

    def _volume_getattr(self, server, sender_path: str, call_id: str,
                        sender: str, request: Dict[str, Any]) -> None:
        self._delegate(server, sender_path, call_id, sender, request,
                       lambda nyfs, path: {"stat": nyfs.getattr(path)})

    def _volume_readdir(self, server, sender_path: str, call_id: str,
                        sender: str, request: Dict[str, Any]) -> None:
        self._delegate(server, sender_path, call_id, sender, request,
                       lambda nyfs, path: {"names": nyfs.readdir(path)})

    def _volume_mkdir(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        self._delegate(server, sender_path, call_id, sender, request,
                       lambda nyfs, path, mode=0o755: nyfs.mkdir(path, mode))

    def _volume_mknod(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        self._delegate(server, sender_path, call_id, sender, request,
                       lambda nyfs, path, mode=0o644, dev=0:
                       nyfs.mknod(path, mode, dev))

    def _volume_unlink(self, server, sender_path: str, call_id: str,
                       sender: str, request: Dict[str, Any]) -> None:
        self._delegate(server, sender_path, call_id, sender, request,
                       lambda nyfs, path: nyfs.unlink(path))

    def _volume_rmdir(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        self._delegate(server, sender_path, call_id, sender, request,
                       lambda nyfs, path: nyfs.rmdir(path))

    def _volume_rename(self, server, sender_path: str, call_id: str,
                       sender: str, request: Dict[str, Any]) -> None:
        # ``from`` is a Python keyword; the wire keeps it as ``from``
        # (the request's own namespace), so pop it before the delegate.
        def _rename(nyfs, **kw):
            return nyfs.rename(kw["from"], kw["to"])
        self._delegate(server, sender_path, call_id, sender, request,
                       _rename, path_key="from")

    def _volume_truncate(self, server, sender_path: str, call_id: str,
                         sender: str, request: Dict[str, Any]) -> None:
        self._delegate(server, sender_path, call_id, sender, request,
                       lambda nyfs, path, length: nyfs.truncate(path, length))

    def _volume_statfs(self, server, sender_path: str, call_id: str,
                       sender: str, request: Dict[str, Any]) -> None:
        self._delegate(server, sender_path, call_id, sender, request,
                       lambda nyfs: {"statfs": nyfs.statfs()})

    def _volume_fsync(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        # The FUSE fsync contract (NPS-004 §7): commit at the transaction
        # boundary — the same durability promise the write path gives.
        def _save(nyfs):
            nyfs.save()
        self._delegate(server, sender_path, call_id, sender, request, _save)


def _mkdirs(nyfs, path: str) -> None:
    """mkdir -p inside the NyFS tree (idempotent)."""
    import errno as _errno
    from fuse.nyfs import NyFSError
    parts = [p for p in path.split("/") if p]
    current = ""
    for part in parts:
        current += "/" + part
        try:
            nyfs.mkdir(current)
        except NyFSError as e:
            if e.errno == _errno.EEXIST:
                continue
            raise



__all__ = ["StorageService"]
