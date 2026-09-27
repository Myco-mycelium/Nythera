"""crash_spool — local-only crash-report spool (CRY-001 Option A).

Spools crash reports to a local directory beside the §4.5 daemon state
file. **The module imports no HTTP client anywhere** — "local-only" is
a tested property (the contract suite asserts it), not a prose claim.

Design (CRY-001 v0.2.0 §7, accepted as owner direction 2026-09-27):

- **Redaction-default-on at WRITE time** — the DBG-001 ``_redact_vault``
  discipline applied to the §4.5 recovery record: vault aggregate
  figures never reach the spool unless the operator explicitly opts
  out at read time with ``--no-redact`` (and even then only the READ
  view is unredacted — the spooled bytes never carry them).
- **Audit-chained generation** — every accepted spool event is appended
  to an audit chain (the ``create_audit_chain``/``append_audit_entry``
  machinery) with the report id in the entry result, when a chain
  manager is supplied. Spooling works WITHOUT one (standalone/test
  use); the daemon path always supplies one.
- **Fail-closed in the recovery path** — a spool failure NEVER breaks
  §4.5 recovery: ``spool_report`` returns the report id or ``None``
  and logs; it raises nothing.
- **Retention** — ``max_reports`` (default 20) and ``max_bytes``
  (default 32 MiB); enforced at write time, oldest reports evicted
  first. ``purge`` removes a report (or all) and records the removal.

Report content (the CRY-001 §6.3 schema floor): the §4.5 recovery
record (previous daemon's pid/version/socket/last-known manifest), a
fault-handler trace when one is supplied, and a redacted container
manifest snapshot. The report id is ``crash-<utcstamp>-<rand4>``.

References:
    - CRY-001 v0.2.0 §7 (the pre-staged Option A plan; accepted as
      owner direction 2026-09-27)
    - Plan §4.5 crash-recovery reporting (the state file)
    - DBG-001 Phase A (the redaction-default-on discipline)
    - ADR-0018 (the audit chain)
    - NPS-029 (user data separation — redaction inherits it)
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SPOOL_SCHEMA_VERSION = 1
DEFAULT_MAX_REPORTS = 20
DEFAULT_MAX_BYTES = 32 * 1024 * 1024


class CrashSpoolError(Exception):
    """Raised by explicit management operations (purge/list contract
    violations). ``spool_report`` itself never raises it — the recovery
    path is fail-closed."""


def _utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def redact_recovery_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """The DBG-001 redaction discipline applied to a §4.5 recovery
    record: vault aggregates replaced with a redaction marker; the
    counts that identify capacity removed. The manifest's per-container
    entries keep id/state/command (operationally needed) but drop any
    resource byte figures."""
    out = json.loads(json.dumps(record))  # deep copy, plain types
    vault = out.get("vault")
    if isinstance(vault, dict):
        out["vault"] = {
            "redacted": True,
            "volumes": vault.get("volumes"),
            "fields_removed": [
                "logical_bytes", "physical_bytes", "warned_containers",
            ],
        }
    manifest = out.get("last_known_manifest") or out.get("manifest")
    if isinstance(manifest, list):
        cleaned = []
        for entry in manifest:
            if not isinstance(entry, dict):
                continue
            cleaned.append({
                k: v for k, v in entry.items()
                if not k.endswith("_bytes")
            })
        if "last_known_manifest" in out:
            out["last_known_manifest"] = cleaned
        else:
            out["manifest"] = cleaned
    return out


class CrashSpool:
    """The local crash-report spool directory and its management."""

    def __init__(
        self,
        spool_dir: str,
        max_reports: int = DEFAULT_MAX_REPORTS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        audit_manager: Optional[Any] = None,
        audit_container_id: Optional[str] = None,
    ) -> None:
        if not spool_dir:
            raise ValueError("spool_dir is required")
        self.dir = Path(spool_dir)
        self.max_reports = max(1, int(max_reports))
        self.max_bytes = max(1, int(max_bytes))
        # Optional audit hook: an object exposing create_audit_chain /
        # append_audit_entry (the ContainerManager shape). Absent in
        # standalone/test use — generation still works, unchained.
        self._audit_manager = audit_manager
        self._audit_container_id = audit_container_id
        self._chain_id: Optional[str] = None

    # -- chain handling -------------------------------------------------

    def _audit(self, op: str, report_id: str) -> None:
        """Best-effort audit chaining; a missing manager or a chain
        error never fails the spool operation itself."""
        mgr = self._audit_manager
        if mgr is None:
            return
        try:
            if self._chain_id is None:
                res = mgr.create_audit_chain(
                    self._audit_container_id or "crash-spool")
                cid = res.get("chain_id") if isinstance(res, dict) else None
                if not cid:
                    return
                self._chain_id = cid
            mgr.append_audit_entry(self._chain_id, op, {
                "report_id": report_id,
                "spool": str(self.dir),
            })
        except Exception as exc:  # noqa: BLE001 — audit is best-effort
            logger.warning("crash_spool: audit chaining failed (%s)", exc)

    # -- write path ------------------------------------------------------

    def spool_report(
        self,
        recovery_record: Optional[Dict[str, Any]] = None,
        fault_trace: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Compose, redact, and write one crash report. Returns the
        report id, or ``None`` when the spool could not be written —
        this function NEVER raises (the §4.5 recovery path calls it;
        recovery must survive a broken spool)."""
        try:
            report_id = f"crash-{_utc_stamp()}-{secrets.token_hex(2)}"
            record = recovery_record or {}
            report = {
                "schema": SPOOL_SCHEMA_VERSION,
                "report_id": report_id,
                "spooled_at": time.time(),
                "recovery_record": redact_recovery_record(record),
                "fault_trace": fault_trace,
                "extra": extra or {},
                "redacted": True,
            }
            self.dir.mkdir(parents=True, exist_ok=True)
            target = self.dir / f"{report_id}.json"
            payload = json.dumps(report, indent=2, sort_keys=True)
            # Write-then-rename keeps a partial report from being
            # listed as complete if we die mid-write.
            tmp = self.dir / f".{report_id}.tmp"
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, target)
            self._audit("crash_spool_report", report_id)
            self._enforce_retention()
            return report_id
        except OSError as exc:
            logger.warning("crash_spool: spool write failed (%s)", exc)
            return None
        except Exception as exc:  # noqa: BLE001 — fail-closed contract
            logger.warning("crash_spool: unexpected spool failure (%s)", exc)
            return None

    # -- retention --------------------------------------------------------

    def _report_files(self) -> List[Path]:
        """Report files, OLDEST FIRST by mtime (creation order — within
        one timestamp second the random id suffix would make filename
        order lie about age)."""
        if not self.dir.is_dir():
            return []
        files = [p for p in self.dir.glob("crash-*.json") if p.is_file()]
        try:
            return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))
        except OSError:
            return sorted(files)

    def _enforce_retention(self) -> None:
        """Evict oldest-first past max_reports / max_bytes. Best-effort:
        removal failures are logged, not raised (the write already
        succeeded)."""
        files = self._report_files()
        sizes = {p: p.stat().st_size for p in files}
        total = sum(sizes.values())

        def evict() -> None:
            nonlocal total
            for p in files:
                if len(files) <= self.max_reports and total <= self.max_bytes:
                    return
                try:
                    total -= sizes.get(p, 0)
                    p.unlink()
                    files.remove(p)
                    self._audit("crash_spool_evict", p.stem)
                except OSError as exc:
                    logger.warning("crash_spool: cannot evict %s (%s)",
                                   p, exc)
                    return

        evict()

    # -- read / manage ------------------------------------------------------

    def list_reports(self) -> List[Dict[str, Any]]:
        """Redaction-safe listing: ids, timestamps, sizes. The listing
        never opens report content."""
        out = []
        for p in self._report_files():
            try:
                out.append({
                    "report_id": p.stem,
                    "size_bytes": p.stat().st_size,
                    "modified": p.stat().st_mtime,
                })
            except OSError:
                continue
        return out

    def read_report(self, report_id: str) -> Dict[str, Any]:
        """Load one report. Ids are validated — no traversal."""
        if not report_id.replace("-", "").isalnum() or "/" in report_id \
                or ".." in report_id or report_id.startswith("."):
            raise CrashSpoolError(f"invalid report id: {report_id!r}")
        target = self.dir / f"{report_id}.json"
        if not target.is_file():
            raise CrashSpoolError(f"report not found: {report_id}")
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise CrashSpoolError(
                f"report {report_id} unreadable: {exc}") from exc
        if not isinstance(data, dict) or \
                data.get("schema") != SPOOL_SCHEMA_VERSION:
            raise CrashSpoolError(
                f"report {report_id} has an unsupported schema")
        return data

    def purge(self, report_id: Optional[str] = None) -> int:
        """Remove one report (by id) or all. Returns the count removed."""
        if report_id is not None:
            targets = [self.dir / f"{self._validate_id(report_id)}.json"]
        else:
            targets = self._report_files()
        removed = 0
        for p in targets:
            if p.is_file():
                p.unlink()
                removed += 1
                self._audit("crash_spool_purge", p.stem)
        return removed

    @staticmethod
    def _validate_id(report_id: str) -> str:
        if not report_id.replace("-", "").isalnum() \
                or "/" in report_id or ".." in report_id \
                or report_id.startswith("."):
            raise CrashSpoolError(f"invalid report id: {report_id!r}")
        return report_id


def spool_from_state_file(
    spool_dir: str,
    state_file: Optional[str],
    fault_trace: Optional[str] = None,
    audit_manager: Optional[Any] = None,
) -> Optional[str]:
    """The §4.5 integration point: spool a report from the daemon state
    file's CURRENT content (the pre-recovery record). Fail-closed: a
    missing/unreadable state file still spools a minimal report —
    the fact that the daemon died is itself the report."""
    record: Dict[str, Any] = {}
    if state_file and os.path.exists(state_file):
        try:
            from .daemon_state import DaemonStateFile
            loaded = DaemonStateFile(state_file).load()
            if isinstance(loaded, dict):
                record = loaded
        except Exception as exc:  # noqa: BLE001 — fail-closed
            logger.warning("crash_spool: state file unreadable (%s)", exc)
            record = {"state_file_error": str(exc)}
    return CrashSpool(
        spool_dir, audit_manager=audit_manager
    ).spool_report(
        recovery_record=record, fault_trace=fault_trace,
    )


__all__ = [
    "CrashSpool",
    "CrashSpoolError",
    "redact_recovery_record",
    "spool_from_state_file",
    "SPOOL_SCHEMA_VERSION",
    "DEFAULT_MAX_REPORTS",
    "DEFAULT_MAX_BYTES",
]
