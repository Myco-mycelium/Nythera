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
- Volumes are **creator-scoped by default**: only the creating
  container (or the operator) may open one. The creator (or the
  operator) may **grant** another container explicit access
  (``volume_grant``) and revoke it (``volume_revoke``); grants are
  per-container, persisted with the registry, and never implied by
  capabilities alone.

Operations (JSON request → JSON reply over CALL/REPLY):

- ``{"op": "volume_create", "name": str}`` — create a NyFS-backed
  volume; returns ``{volume_id, name}``.
- ``{"op": "volume_open", "volume_id": str}`` — bind the volume to the
  caller (creator, operator, or a granted container); returns an
  opaque ``handle`` (the access token for the subsequent ops; a random
  id, never derivable from the volume id).
- ``{"op": "volume_list"}`` — the volumes the caller may open (its own
  creations plus the volumes granted to it).
- ``{"op": "volume_grant", "volume_id": str, "container": str}`` —
  CREATOR/OPERATOR-ONLY: let ``container`` open the volume.
- ``{"op": "volume_revoke", "volume_id": str, "container": str}`` —
  CREATOR/OPERATOR-ONLY: withdraw the grant (live handles stay valid
  until closed).
- ``{"op": "volume_grants", "volume_id": str}`` — CREATOR/OPERATOR-ONLY:
  the current grant list.
- ``{"op": "volume_close", "handle": str}`` — release a handle.
- ``{"op": "volume_info", "handle": str}`` — the volume's metadata and
  backing-store state (block size, bytes persisted).
- ``{"op": "volume_rekey", "new_passphrase": str}`` — OPERATOR-ONLY
  KEK rotation (ADR-0023): re-wraps every volume's DEK with the new
  KEK (no block re-encryption) and returns the matching envelope.

Quota & accounting (ADR-0022 follow-on, shipped with 0.14.10):

- ``{"op": "volume_quota_set", "volume_id": str, "container": str,
  "bytes": int|null}`` — CREATOR/OPERATOR-ONLY: set (or clear, with
  ``bytes: null``) a per-container byte quota on the volume. A
  container's writes are billed to it fail-closed at write time
  (``EDQUOT``); quotas are unlimited by default.
- ``{"op": "volume_quota_get", "volume_id": str}`` —
  CREATOR/OPERATOR-ONLY: the per-container quotas and their current
  accounted usage.
- ``{"op": "volume_usage", "volume_id": str}`` — the per-container
  accounted usage (any opener may read it) plus the volume-wide
  PHYSICAL block-store bytes (compressed/CoW-deduped — never billed).
  The ledger is a cache re-derived from the NyFS tree at each commit
  (fsync / interval / close / restore), so deletes, truncates and
  restores re-derive it automatically; the enforcement point reads
  the cache.
- ``{"op": "volume_summary"}`` — OPERATOR-ONLY: the whole-vault
  aggregate — volume count, total logical + physical bytes, and a
  per-volume row (logical, physical, consumer count). Re-derives the
  ledger on demand (one walk per volume — the §28-measured cost), so
  the figures are fresh even for uncommitted deferred writes.
- ``{"op": "volume_events"}`` — OPERATOR-ONLY: the quota-event ring
  (warning-level transitions and EDQUOT rejections, newest first).
  In-memory diagnostics, bounded and never persisted — the ledger is
  the durable source of truth.

Deferred-write ops (the FUSE passthrough's data plane):

- ``{"op": "volume_write", ..., "defer_commit": true}`` — write into
  memory; the commit is deferred to fsync/close/the commit interval.
- ``{"op": "volume_fsync", "handle": str}`` — force the durable
  commit (POSIX fsync contract).
- ``{"op": "volume_flush", "handle": str}`` — the FUSE close-of-last-
  fd hook: a group-commit opportunity (interval check), NOT a per-
  close durable commit.

References:
- ADR-0022 (NyVault — storage as a daemon-hosted service)
- NPS-011 (capability registry; CAP_STORAGE_VOLUME)
- NPS-004 (NyFS storage guarantees)
"""

import base64
import errno
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, Optional

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

# Advisory quota-warning thresholds (ADR-0022 accounting): the write
# path is the hard stop; these are the operational signal levels —
# "near" at >= 80%, "at" at >= 95%, "over" above 100% (reachable via
# a restore or a quota set below existing usage).
_QUOTA_NEAR_RATIO = 0.8
_QUOTA_AT_RATIO = 0.95

# Quota-event ring bound: the in-memory history of warning-level
# transitions and EDQUOT rejections (diagnostics — never persisted;
# the ledger itself is the durable source of truth).
_EVENT_RING_SIZE = 64


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
                 kek: Optional[Any] = None,
                 commit_interval: float = 5.0) -> None:
        self.capability_manager = capability_manager
        self.vault_dir = vault_dir
        # Deferred-write commit interval (seconds): the durability tick
        # for the passthrough's ``defer_commit`` writes. Writes are
        # visible in memory immediately; a commit happens at the next
        # ``volume_fsync`` (POSIX contract), at ``volume_close``
        # (unmount), or at the first operation after the interval
        # elapses (group commit — a burst of short-lived files pays ONE
        # save, BENCHMARK_RESULTS §27). ``0`` disables the interval
        # check (fsync/close are then the only commit points).
        self.commit_interval = commit_interval
        self._last_commit = time.monotonic()
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
        # Quota-event ring (diagnostics): warning-level transitions and
        # EDQUOT rejections, newest last. Bounded; never persisted.
        self._events: Deque[Dict[str, Any]] = deque(maxlen=_EVENT_RING_SIZE)
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
                    # container_id -> True; the creator may grant other
                    # containers explicit open access (cross-container
                    # access matrix, ADR-0022)
                    "grants": dict(rec.get("grants") or {}),
                    # Quota & accounting (ADR-0022): per-container byte
                    # quota (unlimited default), path -> last writer
                    # attribution, and the usage ledger cache.
                    "quota": {k: int(v) for k, v in
                               dict(rec.get("quota") or {}).items()},
                    "owners": dict(rec.get("owners") or {}),
                    "usage": {k: int(v) for k, v in
                              dict(rec.get("usage") or {}).items()},
                    # container -> warning level (None/near/at/over);
                    # advisory, re-computed at each refresh
                    "warnings": dict(rec.get("warnings") or {}),
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
             if r.get("wrapped_dek") else None,
             "grants": dict(r.get("grants") or {}),
             "quota": dict(r.get("quota") or {}),
             "owners": dict(r.get("owners") or {}),
             "usage": dict(r.get("usage") or {}),
             "warnings": dict(r.get("warnings") or {})}
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
        try:
            record["dek"] = keys.unwrap(
                self.kek, record["id"].encode("utf-8"),
                record["wrapped_dek"])
        except keys.KeysError as e:
            # Fail-closed with an honest message: the wrapped DEK cannot
            # be unwrapped with the current KEK — the vault was rekeyed
            # (or the wrong key file is serving it).
            raise StorageLockedError(
                "vault key mismatch: the volume's DEK cannot be "
                "unwrapped with the current KEK (was the vault "
                "rekeyed? serve with the NEW key file) [%s]" % (e,))
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
            elif op == "volume_restore":
                self._volume_restore(server, sender_path, msg.message_id,
                                     sender, request)
            elif op == "volume_snapshot_delete":
                self._volume_snapshot_delete(server, sender_path,
                                             msg.message_id, sender, request)
            elif op == "volume_grant":
                self._volume_grant(server, sender_path, msg.message_id,
                                   sender, request)
            elif op == "volume_revoke":
                self._volume_revoke(server, sender_path, msg.message_id,
                                    sender, request)
            elif op == "volume_grants":
                self._volume_grants(server, sender_path, msg.message_id,
                                    sender, request)
            elif op == "volume_quota_set":
                self._volume_quota_set(server, sender_path,
                                       msg.message_id, sender, request)
            elif op == "volume_quota_get":
                self._volume_quota_get(server, sender_path,
                                       msg.message_id, sender, request)
            elif op == "volume_usage":
                self._volume_usage(server, sender_path,
                                   msg.message_id, sender, request)
            elif op == "volume_summary":
                self._volume_summary(server, sender_path,
                                     msg.message_id, sender)
            elif op == "volume_events":
                self._volume_events(server, sender_path,
                                    msg.message_id, sender)
            elif op == "volume_delete":
                self._volume_delete(server, sender_path, msg.message_id,
                                    sender, request)
            elif op == "volume_rekey":
                self._volume_rekey(server, sender_path, msg.message_id,
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
            elif op == "volume_flush":
                self._volume_flush(server, sender_path, msg.message_id,
                                   sender, request)
            else:
                self._reply(server, sender_path, msg.message_id, {
                    "ok": False,
                    "error": "unknown operation: %r" % (op,),
                })
        except StorageLockedError as e:
            # A caller-facing vault state (locked KEK, or a rekeyed
            # vault served with the old key file) — an honest message,
            # not a generic internal error.
            logger.error("ipc: %s: %s", self.SERVICE_NAME, e)
            try:
                self._reply(server, sender_path, msg.message_id, {
                    "ok": False,
                    "error": str(e),
                })
            except Exception:  # noqa: BLE001 - even the error reply can fail
                logger.exception("ipc: %s could not send error reply",
                                 self.SERVICE_NAME)
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

    def _refresh_usage(self, record: Dict[str, Any]) -> None:
        """Re-derive the per-container usage ledger from the NyFS tree
        (ADR-0022: the tree is the source of truth). Sum of file sizes
        = LOGICAL bytes — the operator contract; block-storage bytes
        (CoW sharing, compression) are a separate physical figure this
        increment deliberately does not bill. Attribution is the
        ``owners`` map (path -> last writer), so a delete, truncate,
        rename or restore re-derives automatically: paths absent from
        the walk contribute nothing, and their attribution entries are
        kept so a restore that brings a path back re-attributes it to
        its last writer (stale entries are inert — they only count
        when the path exists again). Best-effort: a missing root is
        not an error."""
        nyfs = record.get("nyfs")
        if nyfs is None:
            return
        owners = record.setdefault("owners", {})
        usage: Dict[str, int] = {}
        try:
            sizes = {}
            for path, inode in nyfs.walk().items():
                if not inode.is_directory:
                    sizes[path] = inode.size
        except Exception as e:  # noqa: BLE001 - a walk failure is not fatal
            logger.warning("ipc: %s could not refresh usage for %s: %s",
                           self.SERVICE_NAME, record["id"][:8], e)
            return
        for path, owner in list(owners.items()):
            size = sizes.get(path)
            if size is None:
                # The path is absent from the live tree (deleted or
                # restored away) — it contributes nothing NOW, but the
                # attribution entry is kept: a restore that brings the
                # path back re-attributes it to its last writer, which
                # is exactly the re-derive the ADR promises (stale
                # entries are inert — they only count when the path
                # exists again).
                continue
            usage[owner] = usage.get(owner, 0) + size
        record["usage"] = usage
        # PHYSICAL bytes (volume-wide): the on-disk state footprint —
        # journal + metadata + the block store, compressed + CoW-
        # deduped, so it is load-dependent and deliberately NOT billed
        # (ADR-0022: logical bytes are the quota contract; physical is
        # a separate operator figure). Cached here so
        # ``volume_usage``/``volume_summary`` never pay a disk walk on
        # demand; refreshed with the ledger.
        record["physical_bytes"] = self._physical_bytes(nyfs)
        # Quota warning levels (advisory — the write path is the hard
        # stop). Computed at every refresh and logged only on a level
        # TRANSITION, so a volume parked near its quota does not spam.
        warnings = record.setdefault("warnings", {})
        quotas = record.get("quota", {})
        for cid in list(warnings):
            if cid not in quotas:
                warnings.pop(cid, None)  # quota cleared -> signal gone
        for cid, quota in quotas.items():
            level = self._warning_level(usage.get(cid, 0), quota)
            if warnings.get(cid) != level:
                if level is not None:
                    logger.warning(
                        "ipc: %s container %s is %s quota on volume %s "
                        "(%d/%d bytes)", self.SERVICE_NAME, cid, level,
                        record["name"], usage.get(cid, 0), quota)
                    self._record_event(record["name"], cid, level,
                                       usage.get(cid, 0), quota)
                warnings[cid] = level

    def _commit_dirty(self) -> None:
        """Persist every volume with deferred (dirty) state. Best-effort
        and non-fatal: one volume failing to save must not block the
        others, and the op that triggered the commit still succeeds.
        A committed volume's usage ledger is re-derived from the tree
        (ADR-0022: the tree is the ledger) and the registry (quotas +
        attribution) is persisted with the commit so accounting
        survives a daemon restart."""
        saved_any = False
        for record in self._volumes.values():
            nyfs = record.get("nyfs")
            if nyfs is not None and nyfs.dirty:
                try:
                    nyfs.save()
                    self._refresh_usage(record)
                    saved_any = True
                except Exception as e:  # noqa: BLE001 - log and move on
                    logger.error(
                        "ipc: %s interval commit failed for volume %s: %s",
                        self.SERVICE_NAME, record["id"][:8], e)
        if saved_any:
            self._save_state()

    def _maybe_interval_commit(self) -> None:
        """Group-commit trigger: when the commit interval has elapsed
        since the last commit, persist every dirty volume. Called from
        the deferred-write path and ``volume_flush``, so a burst of
        deferred writes within the interval pays ONE save at the first
        operation after the interval (or at fsync/close, which commit
        unconditionally)."""
        if self.commit_interval <= 0:
            return
        if time.monotonic() - self._last_commit < self.commit_interval:
            return
        self._commit_dirty()
        self._last_commit = time.monotonic()

    def _record_event(self, volume: str, container: str, level: str,
                      usage: int, quota: Optional[int]) -> None:
        """Append one quota event to the ring (bounded diagnostics:
        warning-level transitions and EDQUOT rejections). The ledger
        itself — not this ring — is the durable source of truth."""
        self._events.append({
            "t": round(time.time(), 3),
            "volume": volume,
            "container": container,
            "level": level,
            "usage": usage,
            "quota": quota,
        })

    @staticmethod
    def _warning_level(used: int, quota: int) -> Optional[str]:
        """The advisory quota-warning level for ``used`` of ``quota``
        bytes: None (< 80%), 'near' (>= 80%), 'at' (>= 95%), or 'over'
        (> 100% — reachable via a restore or a quota set below
        existing usage, since the write path is the only hard stop)."""
        if not quota or quota <= 0:
            return None
        ratio = used / quota
        if ratio > 1.0:
            return "over"
        if ratio >= _QUOTA_AT_RATIO:
            return "at"
        if ratio >= _QUOTA_NEAR_RATIO:
            return "near"
        return None

    @staticmethod
    def _physical_bytes(nyfs) -> int:
        """The volume's on-disk footprint: every file under the NyFS
        state dir (journal + metadata + the blocks/ store — pre-
        compaction blocks live in the journal). Compressed + CoW-
        deduped PHYSICAL bytes, the un-billed operator figure
        (ADR-0022: logical bytes are the quota contract)."""
        state = os.path.join(str(nyfs.base_path), "state")
        if not os.path.isdir(state):
            return 0
        total = 0
        try:
            for root, _dirs, files in os.walk(state):
                for name in files:
                    try:
                        total += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        continue
        except OSError:
            return 0
        return total

    @staticmethod
    def _can_open(record: Dict[str, Any], sender: str) -> bool:
        """Who may open a volume: the operator (always), the creator,
        or a container holding an explicit grant (ADR-0022's access
        matrix). Grants never imply the storage capability itself —
        the capability gate is separate and checked first."""
        return (sender == DEFAULT_OPERATOR_ID
                or record.get("created_by") == sender
                or bool(record.get("grants", {}).get(sender)))

    @staticmethod
    def _grant_scope(record: Dict[str, Any], sender: str) -> Optional[str]:
        """The path scope of ``sender``'s grant: None when not
        granted, '/' for a whole-volume grant (or the creator/
        operator — they are never path-restricted), or the granted
        subtree path. Persisted shape: ``True`` (whole volume, the
        0.14.8 format) or ``{"path": str}`` (path-scoped)."""
        if sender == DEFAULT_OPERATOR_ID or record.get("created_by") == sender:
            return "/"
        grant = record.get("grants", {}).get(sender)
        if grant is None:
            return None
        if isinstance(grant, dict):
            return grant.get("path") or "/"
        return "/"

    @staticmethod
    def _path_in_scope(path: str, scope: Optional[str]) -> bool:
        """True when ``path`` is inside ``scope`` (None or '/' = the
        whole volume; otherwise the subtree — exact or a descendant)."""
        if scope in (None, "/", ""):
            return True
        return path == scope or path.startswith(scope.rstrip("/") + "/")

    def _check_grant_scope(self, server, sender_path: str, call_id: str,
                           record: Dict[str, Any], sender: str,
                           path: Optional[str]) -> bool:
        """Reject a data-plane op whose path falls outside the caller's
        grant scope (path-scoped grants, 0.14.15). The creator and
        operator are never restricted; a whole-volume grant passes
        everything. Returns True when the op may proceed (the failure
        reply was already sent otherwise)."""
        if path is None:
            return True
        scope = self._grant_scope(record, sender)
        if self._path_in_scope(path, scope):
            return True
        self._reply(server, sender_path, call_id, {
            "ok": False,
            "error": "forbidden: %r is outside your grant scope %r"
                      % (path, scope),
            # The honest errno: a scope violation is a permission
            # denial, so the FUSE passthrough surfaces EACCES to the
            # kernel instead of a generic EIO.
            "errno": errno.EACCES,
        })
        return False

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
                  "encrypted": False, "grants": {},
                  # Quota & accounting (ADR-0022): per-container byte
                  # quota (unlimited by default), the last-writer
                  # attribution map (path -> container), the usage
                  # ledger cache re-derived from the tree at commit,
                  # and the advisory warning levels (near/at/over).
                  "quota": {}, "owners": {}, "usage": {}, "warnings": {}}
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
        # ``volume`` is the FUSE passthrough's id-or-name key (it hands
        # whatever the mount was given — an id or a name string — and
        # cannot tell them apart).
        volume_id = request.get("volume_id") or request.get("volume") or \
            self._by_name.get(request.get("name") or "")
        record = self._volumes.get(volume_id)
        if record is None and volume_id:
            # id-or-name: a NAME handed in as ``volume``/``volume_id``
            # resolves through the name map.
            record = self._volumes.get(self._by_name.get(volume_id))
            if record is not None:
                volume_id = record["id"]  # canonicalize for the handle
        if record is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown volume: %r" % (volume_id,),
            })
            return
        # Creator-scoped by default; a granted container may open too
        # (ADR-0022's access matrix).
        if not self._can_open(record, sender):
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
            if self._can_open(record, sender):
                volumes.append({
                    "id": record["id"],
                    "name": record["name"],
                    "created_by": record["created_by"],
                    "granted": bool(record.get("grants", {}).get(sender)),
                })
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volumes": volumes,
        })

    def _resolve_volume(self, request: Dict[str, Any]):
        """(volume_id, record) for the request's id-or-name key, or
        (None, None) when unresolvable. Shares the open path's
        id-or-name logic (``volume_id``/``volume``/``name``)."""
        volume_id = request.get("volume_id") or request.get("volume") or \
            self._by_name.get(request.get("name") or "")
        record = self._volumes.get(volume_id)
        if record is None and volume_id:
            record = self._volumes.get(self._by_name.get(volume_id))
            if record is not None:
                volume_id = record["id"]
        return (volume_id, record) if record is not None else (None, None)

    def _require_owner(self, server, sender_path: str, call_id: str,
                       sender: str, record: Dict[str, Any],
                       op: str) -> bool:
        """True when ``sender`` may administer the volume (creator or
        operator). Sends the failure reply and returns False otherwise."""
        if sender != DEFAULT_OPERATOR_ID and record["created_by"] != sender:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: %s requires the volume creator "
                          "or the operator" % (op,),
            })
            return False
        return True

    def _volume_grant(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        """Let another container open the volume (ADR-0022's access
        matrix). CREATOR/OPERATOR-ONLY: a granted container administers
        nothing — it can only open what it was given.

        An optional ``path`` scope (``/subtree``) limits the grant to
        that subtree: the grantee may open the volume, but every
        data-plane op on a path outside the scope is rejected. A bare
        grant (no ``path``) stays a whole-volume grant (back-
        compatible; persisted as ``True``)."""
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        volume_id, record = self._resolve_volume(request)
        if record is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown volume: %r" % (volume_id,),
            })
            return
        if not self._require_owner(server, sender_path, call_id, sender,
                                   record, "volume_grant"):
            return
        container = request.get("container")
        if not isinstance(container, str) or not container.strip():
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container must be a non-empty string",
            })
            return
        container = container.strip()
        scope_path = request.get("path")
        if scope_path is not None:
            if not self._check_path(scope_path):
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "path must be an absolute volume path "
                              "without '..' or trailing '/'",
                })
                return
            # A path-scoped grant: the grantee may open the volume but
            # only reach data under this subtree.
            record["grants"][container] = {"path": scope_path}
        else:
            # Whole-volume grant (the 0.14.8 shape, back-compatible).
            record["grants"][container] = True
        self._save_state()
        logger.info("ipc: %s granted %s access to volume %s (%s) "
                    "scope=%s", self.SERVICE_NAME, container,
                    record["name"], volume_id[:8],
                    scope_path or "/")
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volume_id": record["id"],
            "container": container,
            "path": scope_path,   # None = whole-volume grant
            "granted": True,
        })

    def _volume_revoke(self, server, sender_path: str, call_id: str,
                       sender: str, request: Dict[str, Any]) -> None:
        """Withdraw a grant. Live handles stay valid until closed —
        the handle is the open-file token (POSIX open semantics); a
        revoke gates future opens, not in-flight ones."""
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        volume_id, record = self._resolve_volume(request)
        if record is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown volume: %r" % (volume_id,),
            })
            return
        if not self._require_owner(server, sender_path, call_id, sender,
                                   record, "volume_revoke"):
            return
        container = request.get("container")
        if not isinstance(container, str) or not container.strip():
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container must be a non-empty string",
            })
            return
        container = container.strip()
        was_granted = record["grants"].pop(container, None) is not None
        if was_granted:
            self._save_state()
        logger.info("ipc: %s revoked %s's access to volume %s (%s)",
                    self.SERVICE_NAME, container, record["name"],
                    volume_id[:8])
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volume_id": record["id"],
            "container": container,
            "revoked": was_granted,
        })

    def _volume_grants(self, server, sender_path: str, call_id: str,
                       sender: str, request: Dict[str, Any]) -> None:
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        volume_id, record = self._resolve_volume(request)
        if record is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown volume: %r" % (volume_id,),
            })
            return
        if not self._require_owner(server, sender_path, call_id, sender,
                                   record, "volume_grants"):
            return
        grants = sorted(
            {"container": c, "path": self._grant_scope(record, c)}
            for c in record["grants"])
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volume_id": record["id"],
            "grants": grants,
        })

    # -- quota & accounting (ADR-0022 follow-on) --------------------

    def _volume_quota_set(self, server, sender_path: str, call_id: str,
                          sender: str, request: Dict[str, Any]) -> None:
        """Set (or clear, with ``bytes: null``) a per-container byte
        quota on the volume. CREATOR/OPERATOR-ONLY — quota is
        administration, exactly like grants; a granted container
        cannot hand itself quota. Unlimited by default."""
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        volume_id, record = self._resolve_volume(request)
        if record is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown volume: %r" % (volume_id,),
            })
            return
        if not self._require_owner(server, sender_path, call_id, sender,
                                   record, "volume_quota_set"):
            return
        container = request.get("container")
        if not isinstance(container, str) or not container.strip():
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "container must be a non-empty string",
            })
            return
        container = container.strip()
        quota_bytes = request.get("bytes")
        if quota_bytes is not None and (
                not isinstance(quota_bytes, int)
                or isinstance(quota_bytes, bool) or quota_bytes < 0):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "bytes must be a non-negative integer or null "
                          "(null clears the quota)",
            })
            return
        if quota_bytes is None:
            record.setdefault("quota", {}).pop(container, None)
        else:
            record.setdefault("quota", {})[container] = quota_bytes
        self._save_state()
        logger.info("ipc: %s set quota for %s on volume %s (%s) to %s",
                    self.SERVICE_NAME, container, record["name"],
                    record["id"][:8], quota_bytes)
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volume_id": record["id"],
            "container": container,
            "bytes": quota_bytes,
        })

    def _volume_quota_get(self, server, sender_path: str, call_id: str,
                          sender: str, request: Dict[str, Any]) -> None:
        """The volume's per-container quotas AND their accounted usage
        (the ledger cache — as of the last commit, since writes are
        billed incrementally between refreshes). CREATOR/OPERATOR-ONLY."""
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        volume_id, record = self._resolve_volume(request)
        if record is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown volume: %r" % (volume_id,),
            })
            return
        if not self._require_owner(server, sender_path, call_id, sender,
                                   record, "volume_quota_get"):
            return
        quotas = record.get("quota", {})
        usage = record.get("usage", {})
        warnings = record.get("warnings", {})
        containers = sorted(set(quotas) | set(usage))
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volume_id": record["id"],
            "rows": [{
                "container": c,
                "quota": quotas.get(c),   # None = unlimited
                "usage": usage.get(c, 0),
                # Advisory warning level (None/near/at/over) — the
                # write path is the hard stop; this is the signal.
                "warning": warnings.get(c),
            } for c in containers],
        })

    def _volume_usage(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        """Per-container accounted usage for a volume the caller may
        open (creator, operator, or a granted container — the volume's
        consumers are exactly who needs the figure)."""
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        volume_id, record = self._resolve_volume(request)
        if record is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "unknown volume: %r" % (volume_id,),
            })
            return
        if not self._can_open(record, sender):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: volume %s is not yours"
                          % (record["id"][:8],),
            })
            return
        usage = record.get("usage", {})
        warnings = record.get("warnings", {})
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volume_id": record["id"],
            "usage": {c: usage.get(c, 0)
                       for c in sorted(set(usage) | set(record.get("quota", {})))},
            # Physical block-store bytes (volume-wide; compressed +
            # CoW-deduped — the un-billed figure, per ADR-0022).
            "physical_bytes": int(record.get("physical_bytes", 0)),
            # Advisory warning levels for quota-holding containers.
            "warnings": {c: w for c, w in warnings.items() if w is not None},
        })

    def _volume_summary(self, server, sender_path: str, call_id: str,
                        sender: str) -> None:
        """The whole-vault aggregate (OPERATOR-ONLY — it reveals the
        existence and sizes of volumes the caller may not be able to
        open). Per-volume: logical bytes (the billed ledger), PHYSICAL
        block-store bytes, and the number of consuming containers.
        The ledger is re-derived on demand (one walk per volume — the
        §28-measured cost), so deferred writes are included."""
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        if sender != DEFAULT_OPERATOR_ID:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: volume_summary is operator-only "
                          "(it reveals volumes you may not open)",
            })
            return
        volumes = []
        total_logical = 0
        total_physical = 0
        for record in self._volumes.values():
            self._refresh_usage(record)  # fresh figures (walk per volume)
            logical = sum(record.get("usage", {}).values())
            physical = int(record.get("physical_bytes", 0))
            consumers = len(record.get("usage", {}))
            total_logical += logical
            total_physical += physical
            warned = sum(1 for w in record.get("warnings", {}).values()
                         if w is not None)
            volumes.append({
                "id": record["id"],
                "name": record["name"],
                "created_by": record["created_by"],
                "encrypted": bool(record.get("wrapped_dek")),
                "logical_bytes": logical,
                "physical_bytes": physical,
                "consumers": consumers,
                "warning_count": warned,
            })
        volumes.sort(key=lambda v: v["name"])
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "volumes": volumes,
            "volume_count": len(volumes),
            "total_logical_bytes": total_logical,
            "total_physical_bytes": total_physical,
        })

    def _volume_events(self, server, sender_path: str, call_id: str,
                       sender: str) -> None:
        """The quota-event ring: warning-level transitions (near/at/
        over) and EDQUOT rejections, newest first. OPERATOR-ONLY —
        quota events reveal per-container accounting. In-memory
        diagnostics (bounded ring, never persisted; the ledger is the
        durable source of truth)."""
        from backend.capability import Capability
        if not self._authorized(sender, Capability.CAP_STORAGE_VOLUME):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: CAP_STORAGE_VOLUME required",
            })
            return
        if sender != DEFAULT_OPERATOR_ID:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: volume_events is operator-only "
                          "(quota events reveal per-container accounting)",
            })
            return
        events = list(self._events)
        events.reverse()  # newest first
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "events": events,
            "event_count": len(events),
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
        # Write-commit batching (§27): a passthrough mount that wrote
        # with ``defer_commit`` gets its dirty state committed here, so
        # unmount/close is a durability boundary (POSIX close semantics).
        record = self._volumes.get(binding["volume_id"])
        if record is not None:
            nyfs = record.get("nyfs")
            if nyfs is not None and nyfs.dirty:
                try:
                    nyfs.save()
                    # Close is a durability AND ledger-refresh boundary.
                    self._refresh_usage(record)
                    self._save_state()
                except Exception as e:  # noqa: BLE001 - close must still succeed
                    logger.warning(
                        "ipc: %s close could not commit dirty volume %s: %s",
                        self.SERVICE_NAME, binding["volume_id"][:8], e)
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
            info["bytes_persisted"] = self._physical_bytes(nyfs)
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

    def _volume_rekey(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        """Rotate the KEK (ADR-0023) without touching any block: every
        volume's wrapped DEK is unwrapped with the CURRENT KEK and
        re-wrapped with the new one, so the ciphertext is untouched.

        OPERATOR-ONLY: the new passphrase is the vault's master secret
        — a container never holds it, regardless of capabilities (the
        operator is kernel-authenticated by the transport). The new KEK
        is derived daemon-side and held in the key handle table for the
        duration of the rekey, then shredded; the reply carries the
        matching envelope (base64) so ``nyrqisctl vault rekey`` can
        persist it and the operator can restart the daemon under the
        new key file. Until that restart the daemon keeps serving with
        the OLD KEK (the rekey does not interrupt service).
        """
        if sender != DEFAULT_OPERATOR_ID:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "forbidden: volume_rekey is operator-only",
            })
            return
        if self.kek is None:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "vault is not encrypted (serve with "
                          "--vault-key-file to enable rekey)",
            })
            return
        new_passphrase = request.get("new_passphrase")
        if not isinstance(new_passphrase, str) or not new_passphrase.strip():
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "new_passphrase must be a non-empty string",
            })
            return
        try:
            from backend import keys
            # The new KEK, derived daemon-side and held in the handle
            # table (the crate owns the master key during the rekey).
            new_blob = keys.make_blob_any(new_passphrase.encode("utf-8"))
            new_kek = keys.unlock(new_blob, new_passphrase.encode("utf-8"))
        except Exception as e:  # noqa: BLE001 - a key failure is a rekey failure
            logger.error("ipc: %s rekey could not derive the new KEK: %s",
                         self.SERVICE_NAME, e)
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "could not derive the new KEK: %s" % (e,),
            })
            return
        rekeyed = 0
        try:
            for record in self._volumes.values():
                wrapped = record.get("wrapped_dek")
                if wrapped is None:
                    continue  # a plaintext volume predating the KEK
                try:
                    dek = keys.unwrap(
                        self.kek, record["id"].encode("utf-8"), wrapped)
                except Exception as e:  # noqa: BLE001 - one bad volume fails the rekey
                    logger.error(
                        "ipc: %s rekey could not unwrap volume %s: %s",
                        self.SERVICE_NAME, record["id"][:8], e)
                    self._reply(server, sender_path, call_id, {
                        "ok": False,
                        "error": "rekey aborted: volume %s failed to "
                                  "unwrap (%s)" % (record["id"][:8], e),
                    })
                    return
                record["wrapped_dek"] = keys.wrap(
                    new_kek, record["id"].encode("utf-8"), dek)
                rekeyed += 1
            self._save_state()
        finally:
            keys.shred(new_kek)
        logger.info("ipc: %s rekeyed %d volume(s) for the operator",
                    self.SERVICE_NAME, rekeyed)
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "rekeyed": rekeyed,
            "new_envelope_b64": base64.b64encode(new_blob).decode("ascii"),
            "note": "restart the daemon with the new key file + "
                    "passphrase to serve under the new KEK",
        })

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
        if not isinstance(path, str) or not path:
            return False
        if path == "/":
            # The volume ROOT is a valid path (getattr/readdir/statfs
            # all target it) even though ``_PATH_RE`` needs at least
            # one non-slash segment.
            return True
        if not _PATH_RE.match(path):
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
        record, nyfs = resolved
        path = request.get("path")
        if not self._check_path(path):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "path must be an absolute volume path "
                          "without '..' or trailing '/'",
            })
            return
        if not self._check_grant_scope(
                server, sender_path, call_id, record, sender, path):
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
        # Quota enforcement (ADR-0022): fail-closed BEFORE the write
        # touches the tree — ``accounted[container] + write > quota``
        # rejects with EDQUOT. The accounted figure is the ledger cache
        # (re-derived from the tree at commit), billed per write below.
        quota_bytes = record.get("quota", {}).get(sender)
        if quota_bytes is not None:
            used = record.get("usage", {}).get(sender, 0)
            if used + len(data) > quota_bytes:
                # The hard stop is the most actionable event an
                # operator can see — record it in the event ring.
                self._record_event(record["name"], sender, "edquot",
                                   used, quota_bytes)
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "quota exceeded: %d bytes accounted, "
                              "%d-byte quota, %d requested"
                              % (used, quota_bytes, len(data)),
                    "errno": errno.EDQUOT,
                })
                return
        # ``defer_commit`` (the FUSE passthrough's write path): commit
        # at the fsync/close/interval boundary instead of per CALL, so
        # a 128 KiB kernel write (4 chunked CALLs) pays ONE save at
        # fsync and a burst of short-lived files pays ONE save per
        # interval — the §27 finding (write-commit batching + group
        # commit). The CLI byte path omits it and stays durable per
        # write. Deferred data is visible in memory immediately and
        # committed by ``volume_fsync``/``volume_close`` (dirty gate)
        # or the interval check below; a daemon crash before then
        # loses it — exactly POSIX fsync semantics, spelled out in the
        # vault runbook.
        defer_commit = bool(request.get("defer_commit"))
        try:
            from fuse.nyfs import NyFSError
            written = self._nyfs_write(nyfs, path, data, offset)
        except NyFSError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "write failed: %s" % (e,),
            })
            return
        # Accounting (ADR-0022): charge the write to the writer and
        # record last-writer attribution, so a shared volume bills each
        # consumer and the commit-time refresh can re-derive usage from
        # the tree. Reads are free; truncate credits the delta below.
        record.setdefault("owners", {})[path] = sender
        usage = record.setdefault("usage", {})
        usage[sender] = usage.get(sender, 0) + len(data)
        # Advisory warning at the point of action: the writer's level
        # AFTER this write (near/at — never 'over', the write path
        # already rejected that case).
        writer_warning = (self._warning_level(usage[sender], quota_bytes)
                          if quota_bytes is not None else None)
        if defer_commit:
            # Group-commit tick: the first operation after the interval
            # persists the whole batch of deferred writes (plus any
            # other dirty volume) in one save.
            self._maybe_interval_commit()
        else:
            # Durability (ADR-0022: ADR-0019's journal commit is the
            # vault's default): commit the transaction before replying
            # so an ack is a durable write, not a memory promise.
            try:
                nyfs.save()
                self._refresh_usage(record)
                self._save_state()
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
            "warning": writer_warning,
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
        record, nyfs = resolved
        path = request.get("path")
        if not self._check_path(path):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "path must be an absolute volume path "
                          "without '..' or trailing '/'",
            })
            return
        if not self._check_grant_scope(
                server, sender_path, call_id, record, sender, path):
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
        # ADMIN op: a snapshot captures the WHOLE volume tree — a
        # path-scoped (or any) grantee snapshotting would copy data
        # outside its scope. CREATOR/OPERATOR-ONLY (0.14.15 tightening).
        if not self._require_owner(server, sender_path, call_id, sender,
                                   record, "volume_snapshot"):
            return
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

    def _volume_restore(self, server, sender_path: str, call_id: str,
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
        # ADMIN op: a restore rewrites the WHOLE volume tree — a
        # grantee restoring would clobber data outside its scope.
        # CREATOR/OPERATOR-ONLY (0.14.15 tightening).
        if not self._require_owner(server, sender_path, call_id, sender,
                                   record, "volume_restore"):
            return
        name = request.get("name")
        if not isinstance(name, str) or not re.match(
                r"^[A-Za-z0-9._-]{1,64}$", name):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "name must be 1..64 chars of [A-Za-z0-9._-]",
            })
            return
        try:
            nyfs.restore_snapshot(name)
            nyfs.save()  # the restored table becomes the durable state
            # The tree changed under the ledger: re-derive usage so a
            # restore frees/re-accounts exactly what the tree holds
            # (ADR-0022: restores re-derive from the tree).
            self._refresh_usage(record)
            self._save_state()
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "restore failed: %s" % (e,),
            })
            return
        except Exception as e:  # noqa: BLE001 - a restore failure is an op failure
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "restore failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "restored": name,
            "volume_id": record["id"],
        })

    def _volume_snapshot_delete(self, server, sender_path: str, call_id: str,
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
        # ADMIN op: deleting a snapshot affects the WHOLE volume's
        # point-in-time history. CREATOR/OPERATOR-ONLY (0.14.15).
        if not self._require_owner(server, sender_path, call_id, sender,
                                   record, "volume_snapshot_delete"):
            return
        name = request.get("name")
        if not isinstance(name, str) or not re.match(
                r"^[A-Za-z0-9._-]{1,64}$", name):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "name must be 1..64 chars of [A-Za-z0-9._-]",
            })
            return
        try:
            nyfs.delete_snapshot(name)
            nyfs.save()  # persist the deletion with the commit
        except ValueError as e:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "snapshot_delete failed: %s" % (e,),
            })
            return
        except Exception as e:  # noqa: BLE001 - a delete failure is an op failure
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "snapshot_delete failed: %s" % (e,),
            })
            return
        self._reply(server, sender_path, call_id, {
            "ok": True,
            "deleted": name,
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
        record, nyfs = resolved
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
            # Path-scoped grants: the data-plane op must stay inside
            # the grantee's subtree (the creator/operator are never
            # restricted).
            if not self._check_grant_scope(
                    server, sender_path, call_id, record, sender, path):
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
        # (the request's own namespace). Rename also re-keys the quota
        # ledger's last-writer attribution, so the bytes cannot dodge
        # accounting by moving to a fresh path.
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
        src = request.get("from")
        dst = request.get("to")
        if not self._check_path(src) or not self._check_path(dst):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "from/to must be absolute volume paths "
                          "without '..' or trailing '/'",
            })
            return
        # Path-scoped grants: BOTH sides of a rename must stay inside
        # the grantee's subtree — a rename is a move, and neither the
        # source nor the destination may leave the scope.
        if not self._check_grant_scope(
                server, sender_path, call_id, record, sender, src):
            return
        if not self._check_grant_scope(
                server, sender_path, call_id, record, sender, dst):
            return
        try:
            nyfs.rename(src, dst)
        except Exception as e:  # noqa: BLE001 - errno mapping below
            from fuse.nyfs import NyFSError
            if isinstance(e, NyFSError):
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "volume_rename failed: %s" % (e,),
                    "errno": e.errno,
                })
            else:
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "volume_rename failed: %s" % (e,),
                })
            return
        owners = record.setdefault("owners", {})
        owners[dst] = owners.pop(src, None) or owners.get(dst)
        self._reply(server, sender_path, call_id, {"ok": True})

    def _volume_truncate(self, server, sender_path: str, call_id: str,
                         sender: str, request: Dict[str, Any]) -> None:
        # Truncate CREDITS the owner's accounted bytes by the size
        # delta (ADR-0022: truncate credits the delta), keeping the
        # ledger cache fresh between commit refreshes — a container
        # that shrinks its files can write again before the next
        # fsync/interval/close refresh.
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
        path = request.get("path")
        length = request.get("length")
        if not self._check_path(path):
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "path must be an absolute volume path "
                          "without '..' or trailing '/'",
            })
            return
        if not self._check_grant_scope(
                server, sender_path, call_id, record, sender, path):
            return
        if not isinstance(length, int) or length < 0:
            self._reply(server, sender_path, call_id, {
                "ok": False,
                "error": "length must be a non-negative integer",
            })
            return
        try:
            old_size = nyfs.getattr(path)["st_size"]
            nyfs.truncate(path, length)
        except Exception as e:  # noqa: BLE001 - errno mapping below
            from fuse.nyfs import NyFSError
            if isinstance(e, NyFSError):
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "volume_truncate failed: %s" % (e,),
                    "errno": e.errno,
                })
            else:
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "volume_truncate failed: %s" % (e,),
                })
            return
        owner = record.get("owners", {}).get(path)
        if owner is not None and length < old_size:
            usage = record.setdefault("usage", {})
            usage[owner] = max(0, usage.get(owner, 0) - (old_size - length))
        self._reply(server, sender_path, call_id, {"ok": True})

    def _volume_statfs(self, server, sender_path: str, call_id: str,
                       sender: str, request: Dict[str, Any]) -> None:
        self._delegate(server, sender_path, call_id, sender, request,
                       lambda nyfs: {"statfs": nyfs.statfs()})

    def _volume_fsync(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        # The FUSE fsync contract (NPS-004 §7): commit at the transaction
        # boundary — the same durability promise the write path gives.
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
        try:
            nyfs.save()
            # The fsync commit is a ledger-refresh point (ADR-0022).
            self._refresh_usage(record)
            self._save_state()
        except Exception as e:  # noqa: BLE001 - a commit failure is an op failure
            from fuse.nyfs import NyFSError
            if isinstance(e, NyFSError):
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "volume_fsync failed: %s" % (e,),
                    "errno": e.errno,
                })
            else:
                self._reply(server, sender_path, call_id, {
                    "ok": False,
                    "error": "volume_fsync failed: %s" % (e,),
                })
            return
        self._reply(server, sender_path, call_id, {"ok": True})

    def _volume_flush(self, server, sender_path: str, call_id: str,
                      sender: str, request: Dict[str, Any]) -> None:
        # FUSE flush (close of the last fd) is NOT a durability
        # boundary (POSIX: close does not promise durability — fsync
        # does). The flush is a group-commit OPPORTUNITY: deferred
        # writes stay in memory until the interval tick, an explicit
        # fsync, or handle close/unmount — so short-lived-file
        # workloads stop paying one save() per close (§27).
        self._maybe_interval_commit()
        self._reply(server, sender_path, call_id, {"ok": True})


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
