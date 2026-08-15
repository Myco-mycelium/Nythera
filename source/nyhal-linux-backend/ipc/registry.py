#!/usr/bin/env python3
"""
ContainerIpcRegistry — pid → container_id for the transport's sender
authentication, kept in sync with live containers by the backend.

The Unix-domain datagram transport (`ipc/transport.py`) authenticates
senders by the kernel-attached pid (`SO_PASSCRED`/`SCM_CREDENTIALS`) and
asks a ``pid_registry`` (dict or callable) to resolve that pid to a
container. This class is that registry, populated automatically: the
``ContainerManager`` registers each direct-syscall container's pid when
it spawns and unregisters it when the container terminates — so a
service can authenticate container senders without manual bookkeeping.

Exactness contract (why the mapping is sound):

- **Direct-syscall launch path (tracked).** The container's command is
  exec'd AS its PID-1 (the launcher's final act), so its host-visible
  pid IS ``container.pid`` — and the kernel attaches that global pid to
  every datagram the command sends (verified 2026-08-14: a sender in a
  new pid+user namespace presents its host pid, not a namespace-local
  one). Registering ``container.pid`` at spawn is therefore exact, and
  the container→service end-to-end test proves the whole chain with the
  auto-registry.
- **Legacy ``unshare(1)`` path (NOT tracked).** The command runs as a
  grandchild of the ``unshare`` process with a different pid, so
  ``container.pid`` does not equal the sender's pid. Its datagrams fail
  closed (dropped as unknown) unless the service supplies its own
  mapping — the documented behavior, not a hole.
- **Forked children of a container command** have their own pids and
  are likewise not auto-tracked; a service that needs them can extend
  the registry (its ``register``/``unregister`` are public).
- **Reaping keeps the mapping accurate.** The manager unregisters on
  ``wait()``/``terminate()``; a container that exits on its own and is
  never reaped leaves its pid mapped until reuse — the standard
  pid-based-auth caveat (an early datagram from a not-yet-registered
  pid is dropped fail-closed, never misattributed).
"""

import logging
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class ContainerIpcRegistry:
    """pid → container_id for transport sender authentication.

    Callable so it slots directly into ``IPCDatagramServer``'s
    ``pid_registry`` argument (the server accepts a dict or a callable).
    """

    def __init__(
        self,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self._pids: Dict[int, str] = {}
        self._on_change = on_change

    def set_on_change(self, on_change: Optional[Callable[[], None]]) -> None:
        """Attach a callback fired after every mutation (register/
        unregister). The daemon uses it to push a fresh policy snapshot
        to the Rust serving loop (ADR-0021's per-container pid-table
        refresh); a failing callback is logged, never raised — registry
        mutations must never break container lifecycle."""
        self._on_change = on_change

    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:  # noqa: BLE001 - policy refresh is best effort
            logger.warning(
                "ipc registry: on_change callback failed", exc_info=True)

    def register(self, pid: int, container_id: str) -> None:
        """Map a container's host pid to its id (called on spawn)."""
        self._pids[pid] = container_id
        self._notify()

    def unregister(self, pid: Optional[int]) -> None:
        """Drop the mapping (called when the container terminates).
        Idempotent: a pid that was never mapped (double-terminate, or
        the legacy unshare(1) path) is a no-op and does NOT fire the
        change hook — nothing changed."""
        if pid is not None and self._pids.pop(pid, None) is not None:
            self._notify()

    def resolve(self, pid: int) -> Optional[str]:
        """The container id for ``pid``, or None (unknown sender → the
        transport drops the datagram)."""
        return self._pids.get(pid)

    def __call__(self, pid: int) -> Optional[str]:
        return self.resolve(pid)

    def __len__(self) -> int:
        return len(self._pids)

    def __contains__(self, pid: int) -> bool:
        return pid in self._pids

    def snapshot(self) -> Dict[int, str]:
        """A copy of the pid → container map (for policy pushes to the
        Rust serving loop — ADR-0021's policy-data-across-the-boundary
        contract)."""
        return dict(self._pids)


__all__ = ["ContainerIpcRegistry"]
