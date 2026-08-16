#!/usr/bin/env python3
"""
NyVault FUSE passthrough (ADR-0022's data-plane mount).

A thin FUSE filesystem whose operation handlers are **storage-service
CALLs**: every ``getattr``/``read``/``write``/... is a JSON CALL to the
daemon's ``storage`` service over the authenticated IPC transport, not
a local filesystem touch. The daemon holds the data plane (and the
DEKs for the encrypted block layer); a mount client holds only an
opaque volume handle.

Ops map 1:1 onto the storage service's generic file surface
(``volume_getattr``/``volume_readdir``/``volume_mkdir``/... and the
byte path ``volume_read``/``volume_write``). The byte path caps each
CALL at 32 KiB (the datagram budget), so ``read``/``write`` page
through the service in ≤32 KiB chunks — the FUSE kernel's 128 KiB
requests are satisfied across multiple CALLs.

Like ``NyFSMount``, the kernel mount itself requires ``fusepy`` +
``/dev/fuse``; without them the operations are still fully usable
directly (and that is exactly how the tests drive them), and
``attach()`` reports the deferral honestly rather than pretending.

Trust model: the transport authenticates the caller by the
kernel-attached pid → container id; the service enforces
``CAP_STORAGE_VOLUME`` + creator scoping fail-closed (ADR-0022). A
mount client is just another container with a granted capability.
"""

import base64
import ctypes
import errno
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Mirror of the service's per-call payload cap (ipc/storage.py): a
# single volume op must stay under the 64 KiB datagram budget.
_MAX_IO_BYTES = 32 * 1024

_STORAGE_SERVICE = "storage"


class VaultMountError(OSError):
    """An operation failed on the daemon. Carries the POSIX errno the
    service reported so callers (and the FUSE adapter) can raise the
    right ``OSError`` subclass (ENOENT → FileNotFoundError, ...)."""


class NyVaultOperations:
    """FUSE operation handlers backed by storage-service CALLs.

    Method signatures follow the FUSE kernel protocol as implemented by
    ``fusepy``. Errors surface as ``VaultMountError`` (an ``OSError``
    with a POSIX errno), which the mount adapter translates to
    ``FuseOSError``. The volume handle is opened in ``__init__`` (one
    ``volume_open`` CALL) and released by ``close()``.
    """

    def __init__(self, client, socket_path: str,
                 volume: str, timeout_s: float = 10.0):
        self.client = client
        self.socket_path = socket_path
        self.timeout_s = timeout_s
        # ``volume`` may be a volume id or a name — the service resolves
        # either (``volume_open`` accepts both).
        self.volume = volume
        self._handle: Optional[str] = None
        # ADR-0024: the service advertises the streaming data plane in
        # the open reply. ``stream_ver`` 2 (0.14.21) is the WIRE-level
        # STREAM_CHUNK framing — the transport chunks the CALL/REPLY
        # itself, so this passthrough sends ONE plain call with the
        # full payload. ``stream`` alone (0.14.20) is the service-level
        # envelope (chunk CALLs with a stream_id envelope). When
        # neither (an older daemon), this passthrough pages in ≤32 KiB
        # CALLs — the mixed-version degradation paths that stay
        # implemented forever.
        self._stream_ok = False
        self._stream_ver = 0
        self._open()

    # -- wire helpers -----------------------------------------------

    def _call(self, op: str, **kw) -> Dict[str, Any]:
        """Issue one storage-service CALL; raise VaultMountError on a
        non-ok reply (with the service's errno when it sent one)."""
        payload = json.dumps({"service": _STORAGE_SERVICE, "op": op,
                              "handle": self._handle, **kw},
                             sort_keys=True).encode("utf-8")
        reply = self.client.call(self.socket_path, payload,
                                 timeout_s=self.timeout_s)
        if reply is None:
            raise VaultMountError(errno.ETIMEDOUT,
                                  "no reply from the storage service")
        try:
            body = json.loads(reply.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise VaultMountError(errno.EPROTO,
                                  "malformed reply from the storage "
                                  "service: %s" % (e,))
        if not body.get("ok"):
            err = body.get("errno")
            if isinstance(err, int) and err > 0:
                raise VaultMountError(err, body.get("error") or "op failed")
            raise VaultMountError(errno.EIO,
                                  body.get("error") or "op failed")
        return body

    def _open(self) -> None:
        """Bind the volume to this caller: one ``volume_open`` CALL,
        the handle is the access token for everything else."""
        payload = json.dumps(
            {"service": _STORAGE_SERVICE, "op": "volume_open",
             "volume": self.volume},  # id-or-name (the service resolves)
            sort_keys=True).encode("utf-8")
        reply = self.client.call(self.socket_path, payload,
                                 timeout_s=self.timeout_s)
        if reply is None:
            raise VaultMountError(errno.ETIMEDOUT,
                                  "volume_open: no reply")
        try:
            body = json.loads(reply.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise VaultMountError(errno.EPROTO,
                                  "volume_open: malformed reply: %s" % (e,))
        if not body.get("ok"):
            raise VaultMountError(
                errno.EACCES, body.get("error") or "volume_open failed")
        handle = body.get("handle")
        if not isinstance(handle, str) or not handle:
            raise VaultMountError(errno.EPROTO,
                                  "volume_open: missing handle")
        self._handle = handle
        self._stream_ok = bool(body.get("stream"))
        ver = body.get("stream_ver")
        self._stream_ver = ver if isinstance(ver, int) else (
            1 if self._stream_ok else 0)

    def close(self) -> None:
        """Release the volume handle (best-effort; the daemon also
        reaps handles on container exit)."""
        if self._handle is None:
            return
        try:
            self._call("volume_close")
        except VaultMountError as e:
            logger.warning("vault passthrough: volume_close failed: %s", e)
        finally:
            self._handle = None

    # -- FUSE operations --------------------------------------------

    def getattr(self, path, fh=None):
        return self._call("volume_getattr", path=path)["stat"]

    def readdir(self, path, fh=None):
        # The service's NyFS readdir already includes ``.`` and ``..``
        # (the same convention NyFSOperations exposes).
        return self._call("volume_readdir", path=path)["names"]

    def open(self, path, flags):
        # Path-based ops; no server-side file handle to manage.
        return 0

    def release(self, path, fh):
        return 0

    def read(self, path, size, offset, fh=None):
        """Read ``size`` bytes at ``offset``.

        Streaming (ADR-0024 first increment, when the service
        advertises it): ONE ``volume_read`` CALL with ``stream=True``
        and the full size — the service pages through NyFS in-process
        and replies with a sequence of correlated ≤32 KiB pieces that
        this client reassembles by index. One round trip for a 128 KiB
        kernel request instead of four.

        Fallback (older peer, or a service error): page through the
        service in ≤32 KiB CALLs until ``size`` bytes (or a short
        read) have been collected — the pre-streaming path, kept
        forever."""
        if self._stream_ver >= 2 and size > _MAX_IO_BYTES:
            try:
                return self._read_wire(path, size, offset)
            except VaultMountError as e:
                if e.errno == errno.ETIMEDOUT:
                    logger.warning(
                        "vault passthrough: wire-streamed read timed "
                        "out, service-level fallback: %s", e)
                    if self._stream_ok:
                        try:
                            return self._read_stream(path, size, offset)
                        except VaultMountError as e2:
                            if e2.errno == errno.ETIMEDOUT:
                                logger.warning(
                                    "vault passthrough: streamed read "
                                    "failed, paging fallback: %s", e2)
                            else:
                                raise
                else:
                    raise
        elif self._stream_ok and size > _MAX_IO_BYTES:
            try:
                return self._read_stream(path, size, offset)
            except VaultMountError as e:
                if e.errno == errno.ETIMEDOUT:
                    # The stream path failed to complete (an older
                    # peer that advertised but dropped, or a partial
                    # stream) — degrade to paging for this read.
                    logger.warning(
                        "vault passthrough: streamed read failed, "
                        "paging fallback: %s", e)
                else:
                    raise
        return self._read_paged(path, size, offset)

    def _read_wire(self, path, size, offset):
        """Wire-level read (ADR-0024 follow-on): ONE ``volume_read``
        CALL with the full size — the transport reassembles the
        STREAM_CHUNK reply sequence, so the service's single full
        reply comes back as one payload. One round trip for a 128 KiB
        kernel request instead of four (or instead of N service-level
        pieces)."""
        payload = json.dumps(
            {"service": _STORAGE_SERVICE, "op": "volume_read",
             "handle": self._handle, "path": path, "offset": offset,
             "size": size, "wire_stream": True},
            sort_keys=True).encode("utf-8")
        reply = self.client.call(self.socket_path, payload,
                                 timeout_s=self.timeout_s,
                                 wire_stream=True)
        if reply is None:
            raise VaultMountError(errno.ETIMEDOUT,
                                  "no reply from the storage service")
        try:
            body = json.loads(reply.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise VaultMountError(errno.EPROTO,
                                  "malformed reply from the storage "
                                  "service: %s" % (e,))
        if not body.get("ok"):
            err = body.get("errno")
            if isinstance(err, int) and err > 0:
                raise VaultMountError(err, body.get("error") or "read failed")
            raise VaultMountError(errno.EIO,
                                  body.get("error") or "read failed")
        return base64.b64decode(body["data_b64"], validate=True)

    def _read_stream(self, path, size, offset):
        """One CALL, N correlated pieces, reassembled by index."""
        payload = json.dumps(
            {"service": _STORAGE_SERVICE, "op": "volume_read",
             "handle": self._handle, "path": path, "offset": offset,
             "size": size, "stream": True},
            sort_keys=True).encode("utf-8")
        pieces = self.client.call_stream_reply(
            self.socket_path, payload, timeout_s=self.timeout_s)
        if pieces is None:
            raise VaultMountError(errno.ETIMEDOUT,
                                  "no reply from the storage service")
        out = bytearray()
        for piece in pieces:
            try:
                body = json.loads(piece.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                raise VaultMountError(errno.EPROTO,
                                      "malformed stream piece: %s" % (e,))
            if not body.get("ok"):
                err = body.get("errno")
                if isinstance(err, int) and err > 0:
                    raise VaultMountError(err, body.get("error") or "read failed")
                raise VaultMountError(errno.EIO,
                                      body.get("error") or "read failed")
            out += base64.b64decode(body["data_b64"], validate=True)
        return bytes(out)

    def _read_paged(self, path, size, offset):
        """Page through the service in ≤32 KiB CALLs until ``size``
        bytes (or a short read) have been collected."""
        chunks = []
        remaining = size if size > 0 else _MAX_IO_BYTES
        pos = offset
        while remaining > 0:
            want = min(remaining, _MAX_IO_BYTES)
            body = self._call("volume_read", path=path, offset=pos,
                              size=want)
            data = base64.b64decode(body["data_b64"], validate=True)
            chunks.append(data)
            pos += len(data)
            if len(data) < want:  # EOF — short read
                break
            remaining -= len(data)
        return b"".join(chunks)

    def write(self, path, data, offset, fh=None):
        """Write ``data`` at ``offset``; return the total bytes written
        (create-on-write lives server-side).

        Streaming (ADR-0024 first increment, when the service
        advertises it): the write rides as ONE pipelined stream of
        ≤32 KiB chunk CALLs (each with a fresh ``stream_id`` envelope
        and per-chunk SHA-256) — the service reassembles and performs
        ONE write, ONE quota check, ONE accounting charge, and ONE
        commit. The client sends every chunk back-to-back and awaits
        the single final reply: a 128 KiB kernel write is one round
        trip instead of four.

        Write-commit batching (§27): the stream is sent with
        ``defer_commit`` — the service commits at fsync/close or the
        commit interval instead of per CALL. POSIX semantics: data is
        visible immediately, durable after ``fsync()`` (close is NOT a
        durability boundary — the interval commit is the safety net)."""
        if self._stream_ver >= 2 and len(data) > _MAX_IO_BYTES:
            try:
                return self._write_wire(path, data, offset)
            except VaultMountError as e:
                if e.errno == errno.ETIMEDOUT:
                    logger.warning(
                        "vault passthrough: wire-streamed write timed "
                        "out, service-level fallback: %s", e)
                    if self._stream_ok:
                        try:
                            return self._write_stream(path, data, offset)
                        except VaultMountError as e2:
                            if e2.errno == errno.ETIMEDOUT:
                                logger.warning(
                                    "vault passthrough: streamed write "
                                    "failed, paging fallback: %s", e2)
                            else:
                                raise
                else:
                    raise
        elif self._stream_ok and len(data) > _MAX_IO_BYTES:
            try:
                return self._write_stream(path, data, offset)
            except VaultMountError as e:
                if e.errno == errno.ETIMEDOUT:
                    logger.warning(
                        "vault passthrough: streamed write failed, "
                        "paging fallback: %s", e)
                else:
                    raise
        return self._write_paged(path, data, offset)

    def _write_wire(self, path, data, offset):
        """Wire-level write (ADR-0024 follow-on): ONE ``volume_write``
        CALL with the full payload — the transport chunks it into
        STREAM_CHUNK messages the service reassembles before dispatch,
        so the service performs ONE write, ONE quota check, ONE
        accounting charge, ONE commit. The 0.14.20 service-level
        envelope (per-chunk JSON) is not needed: the wire layer owns
        the framing now."""
        payload = json.dumps(
            {"service": _STORAGE_SERVICE, "op": "volume_write",
             "handle": self._handle, "path": path, "offset": offset,
             "defer_commit": True,
             "data_b64": base64.b64encode(data).decode("ascii")},
            sort_keys=True).encode("utf-8")
        reply = self.client.call(self.socket_path, payload,
                                 timeout_s=self.timeout_s,
                                 wire_stream=True)
        if reply is None:
            raise VaultMountError(errno.ETIMEDOUT,
                                  "no reply from the storage service")
        try:
            body = json.loads(reply.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise VaultMountError(errno.EPROTO,
                                  "malformed reply from the storage "
                                  "service: %s" % (e,))
        if not body.get("ok"):
            err = body.get("errno")
            if isinstance(err, int) and err > 0:
                raise VaultMountError(err, body.get("error") or "write failed")
            raise VaultMountError(errno.EIO,
                                  body.get("error") or "write failed")
        return int(body.get("bytes_written", len(data)))

    def _write_stream(self, path, data, offset):
        """Pipelined chunk CALLs, one final reply; returns the total
        bytes the service wrote (the full payload — the service writes
        the reassembled stream once)."""
        stream_id = os.urandom(6).hex()
        view = memoryview(data)
        n = (len(data) + _MAX_IO_BYTES - 1) // _MAX_IO_BYTES
        chunk_payloads: List[bytes] = []
        for i in range(n):
            piece = bytes(view[i * _MAX_IO_BYTES:(i + 1) * _MAX_IO_BYTES])
            checksum = hashlib.sha256(piece).hexdigest()
            chunk_payloads.append(json.dumps(
                {"service": _STORAGE_SERVICE, "op": "volume_write",
                 "handle": self._handle, "path": path, "offset": offset,
                 "defer_commit": True,
                 "stream_id": stream_id, "stream_index": i,
                 "stream_count": n, "checksum": checksum,
                 "data_b64": base64.b64encode(piece).decode("ascii")},
                sort_keys=True).encode("utf-8"))
        reply = self.client.call_stream_write(
            self.socket_path, chunk_payloads, timeout_s=self.timeout_s)
        if reply is None:
            raise VaultMountError(errno.ETIMEDOUT,
                                  "no reply from the storage service")
        try:
            body = json.loads(reply.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise VaultMountError(errno.EPROTO,
                                  "malformed reply from the storage "
                                  "service: %s" % (e,))
        if not body.get("ok"):
            err = body.get("errno")
            if isinstance(err, int) and err > 0:
                raise VaultMountError(err, body.get("error") or "op failed")
            raise VaultMountError(errno.EIO,
                                  body.get("error") or "op failed")
        return body["bytes_written"]

    def _write_paged(self, path, data, offset):
        """The pre-streaming path: chunk the FUSE write into ≤32 KiB
        CALLs; return the total bytes written. Kept forever as the
        degradation path (an older peer that never advertises
        streaming, or a partial/failed stream)."""
        written = 0
        pos = offset
        view = memoryview(data)
        while written < len(data):
            piece = bytes(view[written:written + _MAX_IO_BYTES])
            body = self._call("volume_write", path=path, offset=pos,
                              defer_commit=True,
                              data_b64=base64.b64encode(piece).decode("ascii"))
            written += body["bytes_written"]
            pos += body["bytes_written"]
        return written

    def truncate(self, path, length, fh=None):
        self._call("volume_truncate", path=path, length=length)
        return 0

    def mkdir(self, path, mode):
        self._call("volume_mkdir", path=path, mode=mode)
        return 0

    def mknod(self, path, mode, dev):
        self._call("volume_mknod", path=path, mode=mode, dev=dev)
        return 0

    def unlink(self, path):
        self._call("volume_unlink", path=path)
        return 0

    def rmdir(self, path):
        self._call("volume_rmdir", path=path)
        return 0

    def rename(self, old, new):
        self._call("volume_rename", **{"from": old, "to": new})
        return 0

    def statfs(self, path):
        return self._call("volume_statfs")["statfs"]

    def fsync(self, path, datasync, fh=None):
        # FUSE fsync contract: commit at the transaction boundary (the
        # service persists the volume before replying).
        self._call("volume_fsync")
        return 0

    def flush(self, path, fh=None):
        # FUSE flush contract (close of the last fd): NOT a durability
        # boundary (POSIX — close does not promise durability, fsync
        # does). The flush is a group-commit opportunity: the service
        # persists the deferred batch if the commit interval has
        # elapsed, so short-lived-file workloads stop paying one
        # save() per close (§27). Durability comes from fsync(),
        # unmount (volume_close), or the interval tick.
        self._call("volume_flush")
        return 0

    def snapshot(self, name: str) -> str:
        """Create a named CoW snapshot of the whole volume (not a FUSE
        op — exposed for the mount owner)."""
        return self._call("volume_snapshot", name=name)["snapshot_id"]

    def restore(self, name: str) -> None:
        """Restore the volume tree to a snapshot. Path-based lookups
        re-resolve against the restored table, so the mount stays
        usable (kernel attribute caches may briefly hold stale sizes)."""
        self._call("volume_restore", name=name)

    def list_snapshots(self):
        return self._call("volume_snapshots")["snapshots"]


def _import_fusepy():
    """Import the third-party ``fuse`` module (fusepy) by file path —
    same trick as ``fuse.nyfs._import_fusepy``: the local package is
    itself named ``fuse``, so a plain import would resolve to us."""
    from fuse.nyfs import _import_fusepy as _nyfs_fusepy
    return _nyfs_fusepy()


class NyVaultMount:
    """FUSE mount wrapper for a NyVault volume (ADR-0022 passthrough).

    Mounts ``NyVaultOperations`` (storage-service CALLs) against a real
    kernel mount via ``fusepy`` when the package is installed and
    ``/dev/fuse`` is accessible; otherwise reports the deferral
    honestly, exactly like ``NyFSMount``.
    """

    def __init__(self, client, socket_path: str, volume: str,
                 mount_point: str, timeout_s: float = 10.0):
        self.client = client
        self.socket_path = socket_path
        self.volume = volume
        self.timeout_s = timeout_s
        self.mount_point = Path(mount_point)
        self.mount_point.mkdir(parents=True, exist_ok=True)
        self.operations = NyVaultOperations(
            client, socket_path, volume, timeout_s=timeout_s)
        self._fusepy = None
        self._fuse = None
        logger.info("Initialized NyVaultMount for volume %s at %s",
                    volume, self.mount_point)

    def attach(self) -> bool:
        """Attempt to load fusepy. Returns True when a real mount is
        possible."""
        if self._fusepy is not None:
            return True
        self._fusepy = _import_fusepy()
        if self._fusepy is None:
            logger.warning(
                "fusepy not importable — NyVault mount unavailable in "
                "this environment (the volume ops are still fully "
                "usable directly through NyVaultOperations)")
        else:
            logger.info("fusepy loaded; NyVault mount available")
        return self._fusepy is not None

    def _build_fuse(self, foreground: bool = True, writeback_cache: bool = True,
                    **fuse_kwargs):
        """Construct the fusepy FUSE object for this mount (blocks in
        the FUSE event loop, like NyFSMount's — ``mount()`` runs it in
        a thread when ``blocking=False``).

        ``writeback_cache`` negotiates FUSE_CAP_BIG_WRITES +
        FUSE_CAP_WRITEBACK_CACHE + FUSE_CAP_MAX_PAGES in the INIT
        handshake (same as ``NyFSMount``), so the kernel batches writes
        into multi-page requests instead of 4 KiB ones. Without it,
        every 4 KiB FUSE write pays the service's durable per-CALL
        save() — measured at ~0.04 MB/s (BENCHMARK_RESULTS.md §27);
        batched, writes ride the 32 KiB per-call path at the honest
        durability cost."""
        from fuse.nyfs import (
            _FuseConnInfo, _FUSE_CAP_BIG_WRITES,
            _FUSE_CAP_WRITEBACK_CACHE, _FUSE_CAP_MAX_PAGES,
        )
        fuse_mod = self._fusepy or _import_fusepy()
        if fuse_mod is None:
            raise VaultMountError(errno.ENODEV, "fusepy is not available")

        class _Adapter:
            """Callable operations object for fusepy (mirrors the
            NyFSMount adapter): ``__getattr__`` exposes the ops, and
            ``__call__`` routes requests, translating
            ``VaultMountError`` -> ``FuseOSError``."""

            def __init__(self, ops: NyVaultOperations):
                self._ops = ops

            def __getattr__(self, name):
                return getattr(self._ops, name)

            def __call__(self, op, path, *args):
                try:
                    return getattr(self._ops, op)(path, *args)
                except VaultMountError as e:
                    raise fuse_mod.FuseOSError(e.errno)

            def init(self, path):
                # Presence marker so fusepy registers the ``init`` C
                # callback; the actual fuse_conn_info negotiation
                # happens in the FUSE subclass's ``init`` override
                # below (mirrors NyFSMount's adapter).
                return 0

        fuse_cls = getattr(fuse_mod, "FUSE", None) or getattr(
            fuse_mod, "Fuse", None)
        if fuse_cls is None:
            raise VaultMountError(errno.ENODEV, "fusepy has no FUSE class")

        class _VaultFUSE(fuse_cls):
            """FUSE subclass that negotiates write-batching capabilities
            in the INIT handshake (mirrors NyFSMount's ``_NyFUSE``):
            without FUSE_CAP_BIG_WRITES + WRITEBACK_CACHE the kernel
            submits one page per write, and each page rides a CALL that
            pays the durable save (the §27 finding)."""

            def init(self, conn):
                if not writeback_cache:
                    return 0
                try:
                    info = ctypes.cast(
                        conn, ctypes.POINTER(_FuseConnInfo)).contents
                    if info.proto_major != 7:
                        logger.warning(
                            "NyVault FUSE INIT negotiation skipped: "
                            "unexpected proto_major=%s (ctypes layout "
                            "mismatch?)", info.proto_major)
                        return 0
                    desired = (_FUSE_CAP_BIG_WRITES
                               | _FUSE_CAP_WRITEBACK_CACHE
                               | _FUSE_CAP_MAX_PAGES)
                    info.want |= desired & info.capable
                    info.max_pages = 256
                except Exception as e:
                    # Negotiation failure must not prevent mounting.
                    logger.warning(
                        "NyVault FUSE INIT negotiation failed (%s); "
                        "falling back to kernel write defaults", e)
                return 0

        self._fuse = _VaultFUSE(
            _Adapter(self.operations),
            str(self.mount_point),
            foreground=foreground,
            nothreads=False,
            fsname="nyvault",
            **fuse_kwargs,
        )
        return self._fuse

    def mount(self, foreground: bool = True, blocking: bool = True,
              **fuse_kwargs):
        """Mount the volume. Returns False when fusepy is unavailable
        (the ops remain usable directly); raises on a failed mount."""
        if not self.attach():
            return False

        def _run():
            logger.info("Mounting NyVault volume %s at %s (FUSE)",
                        self.volume, self.mount_point)
            self._build_fuse(foreground=foreground, **fuse_kwargs)

        if blocking:
            _run()
        else:
            import threading
            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()
        return True

    def unmount(self) -> None:
        """Release the volume handle and stop a background mount thread
        if one is running."""
        self.operations.close()
        thread = getattr(self, "_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
