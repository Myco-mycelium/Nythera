#!/usr/bin/env python3
"""
Persistent daemon state for the Nyrqis Linux Backend (plan §4.5).

The daemon is stateless across restarts by design — containers cannot
be re-adopted after a crash (NPS-010 §4 has no "resume from pid"
transition, and a host pid is not a safe handle to reattach to). What
persistent state ADDS is a crash-recovery *record*: the last-known
daemon identity (pid, version, socket) and a last-known container
manifest, written atomically, so the next start can REPORT what the
previous daemon left behind and operators can review orphaned
processes.

Guarantees:

- **Atomic** — state is written to a temp file in the same directory
  and ``os.replace``d into place, so a crash mid-write can never leave
  a partially-written state file (the same discipline NyFS uses for
  its image/journal, ADR-0019).
- **Versioned** — an unsupported ``schema`` is ignored (never crashed
  on), mirroring the NyFS codec's forward-compatibility rule.
- **Best effort** — a missing or unwritable state directory (plain CLI
  without the systemd ``RuntimeDirectory``) degrades to "persistence
  disabled" with a warning; it never breaks the daemon.
- **Recovery is reporting, not resumption** — ``is_stale`` detects a
  previous daemon that is no longer running; the daemon logs the
  orphaned manifest and exposes it through the health service. It
  NEVER auto-kills or re-spawns.

References:
- implementation_plan.md §4.5: Persistent state management
- NPS-010 §4: Container state machine
- ADR-0019: Journal-commit durability (atomic replace discipline)
"""

import json
import logging
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = 1


class DaemonStateFile:
    """A versioned, atomically-written JSON state file for the daemon.

    ``save()`` returns False (and logs) when the directory is not
    writable; ``load()`` returns None for a missing, corrupt, or
    unsupported-schema file — callers treat None as "no prior state".
    """

    def __init__(self, path: str) -> None:
        if not path:
            raise ValueError("path is required")
        self.path = str(path)

    # -- persistence ------------------------------------------------

    def save(self, state: Dict[str, Any]) -> bool:
        """Write ``state`` atomically (tmp + ``os.replace``). False
        when the directory is not writable; the daemon continues
        without persistence in that case."""
        state = dict(state)
        state["schema"] = STATE_SCHEMA_VERSION
        state["saved_at"] = time.time()
        directory = os.path.dirname(self.path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=".daemon-state-", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(state, fh, sort_keys=True, indent=2)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.warning("daemon-state: cannot persist %s (%s)",
                           self.path, exc)
            return False
        return True

    def load(self) -> Optional[Dict[str, Any]]:
        """The saved state, or None (missing / corrupt / unsupported
        schema — never raises)."""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as exc:
            logger.warning("daemon-state: %s unreadable (%s); ignoring",
                           self.path, exc)
            return None
        if not isinstance(data, dict):
            logger.warning("daemon-state: %s is not an object; ignoring",
                           self.path)
            return None
        if data.get("schema") != STATE_SCHEMA_VERSION:
            logger.warning(
                "daemon-state: %s has unsupported schema %r; ignoring",
                self.path, data.get("schema"))
            return None
        return data

    # -- recovery helpers ------------------------------------------

    @staticmethod
    def is_stale(state: Optional[Dict[str, Any]],
                 current_pid: Optional[int] = None) -> bool:
        """True when ``state`` belongs to a *previous* daemon: a
        different pid that is no longer running. A live pid (or a pid
        we cannot probe) is conservatively treated as not stale."""
        if not isinstance(state, dict):
            return False
        pid = state.get("daemon_pid")
        if not isinstance(pid, int):
            return False
        if current_pid is not None and pid == current_pid:
            return False
        try:
            os.kill(pid, 0)
            return False  # still in the process table (or not probeable)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False

    @staticmethod
    def manifest(containers) -> List[Dict[str, Any]]:
        """Best-effort last-known manifest from a container collection
        (duck-typed: ``id``, ``config.command``, ``state.value``,
        ``pid``, ``created_at``)."""
        out: List[Dict[str, Any]] = []
        for c in containers or []:
            out.append({
                "id": getattr(c, "id", None),
                "command": list(
                    getattr(getattr(c, "config", None), "command", [])
                    or []),
                "state": getattr(getattr(c, "state", None), "value", None),
                "pid": getattr(c, "pid", None),
                "created_at": getattr(c, "created_at", None),
            })
        return out


__all__ = ["DaemonStateFile", "STATE_SCHEMA_VERSION"]
