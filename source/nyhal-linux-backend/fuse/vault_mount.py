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
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

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
        """Chunk the FUSE write into ≤32 KiB CALLs; return the total
        bytes written (create-on-write lives server-side)."""
        written = 0
        pos = offset
        view = memoryview(data)
        while written < len(data):
            piece = bytes(view[written:written + _MAX_IO_BYTES])
            body = self._call("volume_write", path=path, offset=pos,
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
