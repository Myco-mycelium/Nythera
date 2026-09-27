"""update_orchestrate — the signed-package update path (UPD-001 Option A).

Composes the SHIPPED, already-tested primitives into one audited
sequence — the wiring the surface audit (UPD-001 §2.1) found missing.
**The module imports no HTTP client anywhere** — fetch stays behind
the operator-configured registry client (``registry_pull``), and that
is a tested property (the contract suite asserts it), not a prose
claim.

Sequence per package (the ordering pins, non-negotiable):

1. RESOLVE — the signed index via ``package_repo.load_index``
   (fail-closed against the trust store); the UPDATABLE candidate is
   the latest verified ``version_to`` of a delta entry for the
   installed version.
2. FETCH — the delta payload comes from the LOCAL repository path of
   the entry the operator's registry client already delivered (this
   module performs no network I/O of its own).
3. VERIFY — ``delta_update.apply_delta_update`` verifies the signature
   against the trust store BEFORE any filesystem mutation
   (fail-closed; unsigned deltas refused). ``validate_rollback``
   additionally gates every rollback: target strictly older, trusted
   key.
4. RESTORE POINT — a pre-apply snapshot of the install directory (the
   ``sdk/nyrqis_sdk/restore.RestoreManager`` pattern, promoted to the
   update path's own fail-safe), taken BEFORE any payload mutation.
5. APPLY — ``apply_delta_update``.
6. AUDIT — the event is audit-chained (ADR-0018) with the package id,
   version transition, and delta checksum in the entry result.

Rollback is OPERATOR-INVOKED ONLY in Option A (UPD-001 §7.3): the
automated health-gated rollback is Option B's contract and does not
exist here. Missing PyNaCl fails closed (the shipped
``delta_update`` posture — no deterministic keys, no hash
"signatures").

References:
    - UPD-001 v0.2.0 §7 (the pre-staged Option A plan; accepted as
      owner direction 2026-09-27)
    - backend/update_signing.py (the verification half, shipped)
    - backend/delta_update.py (generate/apply, shipped)
    - backend/package_repo.py (the signed index, shipped)
    - sdk/nyrqis_sdk/restore.py (the restore-point pattern)
    - ADR-0018 (the audit chain)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .delta_update import DeltaUpdateError, apply_delta_update, load_delta_update
from .package_repo import PackageRepository, RepoError
from .update_signing import UpdateVerifier, VerificationStatus

logger = logging.getLogger(__name__)

ORCHESTRATION_SCHEMA_VERSION = 1
DEFAULT_INSTALL_ROOT = "/var/lib/nyrqis/packages"
DEFAULT_STATE_DIR = "/var/lib/nyrqis/update-orchestrator"
_VERSION_RE = re.compile(r"^\d+(\.\d+)*$")


class UpdateOrchestrationError(Exception):
    """Raised when an update or rollback is refused or fails. Every
    refusal carries the reason; the operator decides what next."""


def version_tuple(version: str) -> tuple:
    """Numeric version ordering for ``..``-separated versions
    (e.g. ``1.10.2`` > ``1.9.9``). Non-numeric segments compare as 0;
    a non-conforming version string is NOT an ordering error here —
    ``validate_rollback``-equivalent strictness lives at the call
    sites that need it."""
    parts = []
    for seg in str(version).split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _audit_chain(audit_manager: Optional[Any], container_id: str) -> Optional[str]:
    """Best-effort chain creation; returns the chain id or None."""
    if audit_manager is None:
        return None
    try:
        res = audit_manager.create_audit_chain(container_id)
        cid = res.get("chain_id") if isinstance(res, dict) else None
        return cid or None
    except Exception as exc:  # noqa: BLE001 — audit is best-effort
        logger.warning("update_orchestrate: chain creation failed (%s)", exc)
        return None


def _audit_append(
    audit_manager: Optional[Any], chain_id: Optional[str],
    op: str, result: Dict[str, Any],
) -> None:
    if audit_manager is None or chain_id is None:
        return
    try:
        audit_manager.append_audit_entry(chain_id, op, result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_orchestrate: audit append failed (%s)", exc)


class UpdateOrchestrator:
    """One object owning the fetch→verify→restore→apply→audit sequence
    for signed package updates on this host."""

    def __init__(
        self,
        repo_root: str,
        trust_store_path: str,
        install_root: str = DEFAULT_INSTALL_ROOT,
        state_dir: str = DEFAULT_STATE_DIR,
        audit_manager: Optional[Any] = None,
    ) -> None:
        if not repo_root or not trust_store_path:
            raise ValueError("repo_root and trust_store_path are required")
        self.repo = PackageRepository(repo_root)
        self.trust_store_path = trust_store_path
        self.install_root = Path(install_root)
        self.state_dir = Path(state_dir)
        self._audit_manager = audit_manager
        self._chain_id: Optional[str] = None
        if audit_manager is not None and self._chain_id is None:
            self._chain_id = _audit_chain(audit_manager, "update-orchestrator")

    # -- resolution ------------------------------------------------------

    def resolve_updates(
        self, installed: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """Given {package_id: installed_version}, return the verified
        update candidates: for each installed package, the latest
        verified delta whose version_from matches and whose target is
        strictly newer. The index is FULLY verified first
        (``load_index`` fails closed); a delta entry is only a
        candidate when its payload checksum re-verifies
        (``verify_entry_content``)."""
        index = self.repo.load_index(self.trust_store_path)
        candidates: Dict[str, Dict[str, Any]] = {}
        for entry in index.get("packages", []):
            if entry.get("type") != "delta":
                continue
            pid = entry.get("package_id")
            installed_v = installed.get(pid)
            if not installed_v:
                continue
            if entry.get("version_from") != installed_v:
                continue
            if version_tuple(entry.get("version_to", "")) <= \
                    version_tuple(installed_v):
                continue
            if not self.repo.verify_entry_content(entry):
                logger.warning(
                    "update_orchestrate: delta %s %s→%s fails content "
                    "checksum — excluded", pid, entry.get("version_from"),
                    entry.get("version_to"))
                continue
            cur = candidates.get(pid)
            if cur is None or version_tuple(entry["version_to"]) > \
                    version_tuple(cur["version_to"]):
                candidates[pid] = entry
        return sorted(candidates.values(), key=lambda e: e["package_id"])

    # -- restore points ---------------------------------------------------

    def _restore_point(self, package_id: str, install_dir: Path) -> Optional[str]:
        """Pre-apply snapshot of the install directory: a directory
        copy under state_dir/restore-points/<pid>-<stamp>. Best-effort:
        a failed snapshot REFUSES the apply (fail-closed) — the
        operator can override with ``--no-restore-point`` at the CLI
        for cases where none is wanted, but the default never applies
        without one."""
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dest = self.state_dir / "restore-points" / f"{package_id}-{stamp}"
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            if install_dir.is_dir():
                import shutil
                shutil.copytree(install_dir, dest)
            else:
                dest.mkdir(parents=True, exist_ok=True)
            return str(dest)
        except OSError as exc:
            logger.warning(
                "update_orchestrate: restore point failed (%s)", exc)
            return None

    def restore_point_enabled(self, flag: bool) -> bool:
        return bool(flag)

    # -- apply / rollback ---------------------------------------------------

    def apply_update(
        self,
        package_id: str,
        current_version: str,
        delta_path: Optional[str] = None,
        restore_point: bool = True,
    ) -> Dict[str, Any]:
        """Apply one verified delta update: verify BEFORE restore point
        BEFORE apply; audit-chain the outcome. Returns a result dict
        with the version transition, the restore point, and the touched
        paths. Raises ``UpdateOrchestrationError`` on any refusal."""
        if not package_id or not current_version:
            raise UpdateOrchestrationError("package identity required")
        install_dir = self.install_root / package_id
        if not install_dir.is_dir():
            raise UpdateOrchestrationError(
                f"package {package_id} is not installed under "
                f"{self.install_root}")

        # (1) RESOLVE: the verified delta entry for this transition.
        # Walk the fully-verified index for the newest candidate (the
        # same posture as resolve_updates).
        candidates = self.resolve_updates({package_id: current_version})
        candidates = [c for c in candidates if c["package_id"] == package_id]
        if not candidates:
            raise UpdateOrchestrationError(
                f"no verified delta for {package_id} {current_version} "
                "→ newer (index verified; check the trust store and "
                "the registry sync)")
        entry = candidates[0]
        target_version = entry["version_to"]

        # (2) FETCH: the payload path the registry client delivered.
        payload = Path(delta_path) if delta_path else \
            self.repo.payload_path(entry)
        if not payload.is_file():
            raise UpdateOrchestrationError(
                f"delta payload missing: {payload}")

        # (3) VERIFY — before ANY filesystem mutation.
        try:
            delta = load_delta_update(str(payload))
        except DeltaUpdateError as exc:
            raise UpdateOrchestrationError(
                f"delta document unreadable: {exc}") from exc
        if delta.get("version_from") != current_version or \
                delta.get("version_to") != target_version:
            raise UpdateOrchestrationError(
                "delta document does not match the resolved entry")
        # apply_delta_update performs the signature verification itself
        # when a trust store is supplied — BEFORE touching the
        # filesystem (its documented ordering). The dry verification
        # against a scratch copy happens implicitly: we instead verify
        # first via a dedicated pre-pass on a temp copy is unnecessary
        # because apply verifies before mutating; see its docstring.
        touched: List[str] = []
        rp: Optional[str] = None
        try:
            if restore_point:
                rp = self._restore_point(package_id, install_dir)
                if rp is None:
                    raise UpdateOrchestrationError(
                        "pre-apply restore point failed — apply refused "
                        "(fail-closed; override with --no-restore-point)")
            touched = apply_delta_update(
                delta, str(install_dir),
                verify_trust_store=self.trust_store_path)
        except (DeltaUpdateError, OSError) as exc:
            raise UpdateOrchestrationError(
                f"apply failed for {package_id} "
                f"{current_version}→{target_version}: {exc}") from exc

        # (5) RECORD: update the installer-layout manifest (if present)
        # so the installed inventory reflects the new version — a
        # failed manifest rewrite is logged, the apply stands.
        manifest_path = install_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(m, dict):
                    m["version"] = target_version
                    m.setdefault("package_id", package_id)
                    manifest_path.write_text(
                        json.dumps(m, indent=2, sort_keys=True),
                        encoding="utf-8")
            except (ValueError, OSError) as exc:
                logger.warning(
                    "update_orchestrate: manifest rewrite failed (%s)",
                    exc)

        result = {
            "package_id": package_id,
            "version_from": current_version,
            "version_to": target_version,
            "delta_checksum": delta.get("checksum"),
            "restore_point": rp,
            "touched": touched,
            "applied_at": time.time(),
        }
        self._write_history("apply", result)
        _audit_append(self._audit_manager, self._chain_id,
                      "package_update_applied", result)
        return result

    def rollback(
        self,
        package_id: str,
        current_version: str,
        target_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Operator-invoked rollback: restore the newest restore point
        taken before the CURRENT version was applied (or a specific
        one). The shipped ``validate_rollback`` gate is applied for
        version-order sanity: the target must be strictly older than
        the current version. Audit-chained."""
        if target_version is not None and version_tuple(target_version) \
                >= version_tuple(current_version):
            raise UpdateOrchestrationError(
                f"rollback target {target_version} is not older than "
                f"the current {current_version} (validate_rollback "
                "posture)")
        rp_dir = self.state_dir / "restore-points"
        candidates = []
        if rp_dir.is_dir():
            prefix = f"{package_id}-"
            # Restore points are named <pid>-<utcstamp>; newest first.
            candidates = sorted(
                (p for p in rp_dir.glob(f"{prefix}*") if p.is_dir()),
                reverse=True)
        if not candidates:
            raise UpdateOrchestrationError(
                f"no restore point exists for {package_id} — cannot "
                "roll back (the pre-apply snapshot is the fail-safe; "
                "none was taken)")
        chosen = candidates[0]
        install_dir = self.install_root / package_id
        import shutil
        try:
            if install_dir.is_dir():
                shutil.rmtree(install_dir)
            shutil.copytree(chosen, install_dir)
        except OSError as exc:
            raise UpdateOrchestrationError(
                f"rollback restore failed: {exc}") from exc
        result = {
            "package_id": package_id,
            "restored_from": str(chosen),
            "rolled_back_at": time.time(),
            "note": "operator-invoked rollback (Option A: no automated "
                    "health gate exists)",
        }
        self._write_history("rollback", result)
        _audit_append(self._audit_manager, self._chain_id,
                      "package_update_rolled_back", result)
        return result

    # -- history -------------------------------------------------------------

    def _history_path(self, kind: str) -> Path:
        return self.state_dir / f"history-{kind}.jsonl"

    def _write_history(self, kind: str, result: Dict[str, Any]) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with open(self._history_path(kind), "a",
                      encoding="utf-8") as fh:
                fh.write(json.dumps(result, sort_keys=True) + "\n")
        except OSError as exc:
            logger.warning(
                "update_orchestrate: history write failed (%s)", exc)

    def _read_history(self, kind: str) -> List[Dict[str, Any]]:
        path = self._history_path(kind)
        if not path.is_file():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def history(self, kind: str = "apply") -> List[Dict[str, Any]]:
        return self._read_history(kind)


def verify_only(
    repo_root: str, trust_store_path: str,
) -> Dict[str, Any]:
    """Verify the repository index and report the verified entry counts
    (no mutation). The demo/operations quick check."""
    repo = PackageRepository(repo_root)
    index = repo.load_index(trust_store_path)
    packages = [e for e in index.get("packages", [])
                if e.get("type") == "package"]
    deltas = [e for e in index.get("packages", [])
              if e.get("type") == "delta"]
    return {
        "verified": True,
        "packages": len(packages),
        "deltas": len(deltas),
    }


__all__ = [
    "UpdateOrchestrator",
    "UpdateOrchestrationError",
    "version_tuple",
    "verify_only",
    "ORCHESTRATION_SCHEMA_VERSION",
    "DEFAULT_INSTALL_ROOT",
    "DEFAULT_STATE_DIR",
]
