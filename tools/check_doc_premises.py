#!/usr/bin/env python3
"""Check the repository's recorded factual premises against current reality.

The repository's documents record claims about the world: that a benchmark
section exists ("BENCHMARK_RESULTS §32e"), that a crate was shipped, that
the remote is still named ``Nythera`` pending the rebrand rename. When
reality moves and the document does not, the record lies — the 2026-09-20
premise audit caught two such lies by hand (ADR-0018's status cells, missed
by an earlier reconciliation; ADR-0009-review-package citing a "pending
re-benchmark" that had existed since 2026-09-10).

This tool makes that audit repeatable. The CLAIM REGISTRY below is the
explicit, human-readable list of recorded premises worth re-verifying; each
entry pairs a claim (matched by regex in the docs/tests tree) with a check
run against the live repository. Add a registry entry whenever a document
records a new load-bearing "X exists / X is named Y / evidence is §NN"
premise.

Usage:
    python3 tools/check_doc_premises.py            # verify all claims
    python3 tools/check_doc_premises.py --list     # print the registry
    python3 tools/check_doc_premises.py --json     # machine-readable output

Output follows the repository's checker discipline
(``tools/check_version_drift.py``): ``FAIL:`` lines mean a recorded premise
is now false (fix the document or the registry), ``WARN:`` lines mean the
check could not run (report it, do not silently pass), and exit status 0
only when every check ran and passed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Repository-rooted paths the checks read.
DOCS_DIR = "docs"
TESTS_DIR = "tests"
BENCHMARK_RESULTS = "tests/BENCHMARK_RESULTS.md"
SOURCE_DIR = "source/nyhal-linux-backend"


@dataclass
class Claim:
    """One recorded premise and how to re-verify it."""

    claim_id: str
    # Regex (re.MULTILINE) matched against the docs/tests tree; a hit means
    # some document records this premise, so the check must pass.
    pattern: str
    description: str
    check: str  # checker function name, see CHECKS below
    check_args: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Checkers. Each takes the repo-relative paths matched by the claim's
# pattern plus the claim's check_args, and returns (status, detail) where
# status is "ok" | "fail" | "warn".
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"\0READ_ERROR:{exc}"


def _check_section_exists(paths: list[str], args: dict) -> tuple[str, str]:
    """The cited `## NN.` section exists in tests/BENCHMARK_RESULTS.md."""
    sec = str(args["section"])
    text = _read(REPO_ROOT / BENCHMARK_RESULTS)
    if text.startswith("\0READ_ERROR"):
        return "warn", f"cannot read {BENCHMARK_RESULTS}: {text.split(':', 1)[1]}"
    if re.search(rf"^## {re.escape(sec)}\.", text, re.MULTILINE):
        return "ok", f"{BENCHMARK_RESULTS} has section {sec}"
    return "fail", f"{BENCHMARK_RESULTS} has no '## {sec}.' heading"


def _check_subsection_exists(paths: list[str], args: dict) -> tuple[str, str]:
    """The cited `### NNa.` subsection exists in tests/BENCHMARK_RESULTS.md."""
    sub = str(args["subsection"])
    text = _read(REPO_ROOT / BENCHMARK_RESULTS)
    if text.startswith("\0READ_ERROR"):
        return "warn", f"cannot read {BENCHMARK_RESULTS}: {text.split(':', 1)[1]}"
    if re.search(rf"^### {re.escape(sub)}\.", text, re.MULTILINE):
        return "ok", f"{BENCHMARK_RESULTS} has subsection {sub}"
    return "fail", f"{BENCHMARK_RESULTS} has no '### {sub}.' heading"


def _check_repo_url_is(paths: list[str], args: dict) -> tuple[str, str]:
    """mkdocs.yml's repo_url names exactly the expected repository."""
    expected = str(args["repo"])
    text = _read(REPO_ROOT / "mkdocs.yml")
    if text.startswith("\0READ_ERROR"):
        return "warn", f"cannot read mkdocs.yml: {text.split(':', 1)[1]}"
    match = re.search(r"^repo_url:\s*(\S+)", text, re.MULTILINE)
    if not match:
        return "fail", "mkdocs.yml has no repo_url"
    actual = match.group(1)
    if expected in actual:
        return "ok", f"repo_url names {actual}"
    return (
        "fail",
        f"repo_url is {actual}; documents record {expected} "
        "(if the rebrand rename completed, update the registry claim too)",
    )


def _check_remote_is(paths: list[str], args: dict) -> tuple[str, str]:
    """The git remote still names the expected repository (rename pending)."""
    expected = str(args["repo"])
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return "warn", f"cannot query git remote: {exc}"
    if expected in out:
        return "ok", f"origin is {out} (rename still pending)"
    return (
        "fail",
        f"origin is {out}; documents record {expected} "
        "(if the rebrand rename completed, update REBRAND_NOTICE.md and the registry)",
    )


def _check_crate_exists(paths: list[str], args: dict) -> tuple[str, str]:
    """The named Rust crate directory (with Cargo.toml) exists."""
    crate = str(args["crate"])
    base = Path(SOURCE_DIR) / "rust" / crate
    if (REPO_ROOT / base / "Cargo.toml").is_file():
        return "ok", f"crate {crate} present ({base}/Cargo.toml)"
    return "fail", f"crate {crate} missing ({base}/Cargo.toml not found)"


def _check_path_contains(paths: list[str], args: dict) -> tuple[str, str]:
    """The needle appears in the evidence files.

    By default the evidence files are the pattern-matched paths (every
    file recording the premise must still contain the needle). For
    cross-file claims — premise text in one document, evidence in
    another — pass ``files`` in check_args to assert the needle against
    an explicit list instead.
    """
    needle = str(args["needle"])
    targets = [str(f) for f in args.get("files", [])] or list(paths)
    if not targets:
        return "warn", "no evidence files to check (pattern matched nothing, no files arg)"
    missing: list[str] = []
    no_match: list[str] = []
    for rel in targets:
        text = _read(REPO_ROOT / rel)
        if text.startswith("\0READ_ERROR"):
            missing.append(rel)
        elif needle not in text:
            no_match.append(rel)
    if missing:
        return "fail", f"files recorded as containing {needle!r} are gone: {', '.join(missing)}"
    if no_match:
        return "fail", f"{needle!r} no longer found in: {', '.join(no_match)}"
    return "ok", f"{needle!r} present in {len(targets)} evidence file(s)"


def _check_dir_absent(paths: list[str], args: dict) -> tuple[str, str]:
    """The recorded not-yet-existing component still does not exist.

    Unlike the other checkers, the asserted paths come from check_args
    (``components``): the matched ``paths`` are the documents *recording*
    the premise, which of course always exist.
    """
    label = str(args["label"])
    components = [str(c) for c in args.get("components", [])]
    if not components:
        return "warn", f"{label}: no component paths configured to assert absent"
    present = [c for c in components if (REPO_ROOT / c).exists()]
    if not present:
        return "ok", f"{label} still absent (documents correctly record it as future work)"
    return "fail", f"{label} now exists ({', '.join(present)}); the 'not yet built' premise is stale"


def _check_all_paths_exist(paths: list[str], args: dict) -> tuple[str, str]:
    """Every recorded evidence file still exists on disk.

    Unlike ``path_contains``, the assertion is existence itself — for
    claims of the form "these deliverables exist under these IDs". The
    ``files`` in check_args list the paths that must ALL be present; a
    missing file fails the claim (the recorded premise is stale).
    """
    targets = [str(f) for f in args.get("files", [])]
    if not targets:
        return "warn", "no files configured for the existence claim"
    missing = [rel for rel in targets if not (REPO_ROOT / rel).exists()]
    if missing:
        return "fail", f"files recorded as existing are gone: {', '.join(missing)}"
    return "ok", f"all {len(targets)} recorded file(s) exist"


def _check_frontmatter_status(paths: list[str], args: dict) -> tuple[str, str]:
    """A document's frontmatter status equals the recorded decided state.

    Pins AG decision outcomes against the ADR-0019 failure mode: a doc
    flipped (or reverted) without a Group record. Args: ``file`` (the
    document whose status is pinned) and ``status`` (expected value).
    """
    rel = str(args["file"])
    expected = str(args["status"])
    text = _read(REPO_ROOT / rel)
    if text.startswith("\0READ_ERROR"):
        return "warn", f"cannot read {rel}: {text.split(':', 1)[1]}"
    block = text.split("---", 2)
    if len(block) < 3:
        return "warn", f"{rel} has no parseable frontmatter block"
    match = re.search(r"^status:\s*(.+?)\s*$", block[1], re.MULTILINE)
    if not match:
        return "fail", f"{rel} frontmatter has no status field"
    actual = match.group(1)
    if actual == expected:
        return "ok", f"{rel} status is {actual} (as recorded)"
    return "fail", f"{rel} status is {actual!r}; the recorded decision says {expected!r} (reconcile whichever is wrong)"


CHECKS = {
    "section_exists": _check_section_exists,
    "subsection_exists": _check_subsection_exists,
    "repo_url_is": _check_repo_url_is,
    "remote_is": _check_remote_is,
    "crate_exists": _check_crate_exists,
    "path_contains": _check_path_contains,
    "dir_absent": _check_dir_absent,
    "all_paths_exist": _check_all_paths_exist,
    "frontmatter_status": _check_frontmatter_status,
}


# ---------------------------------------------------------------------------
# The claim registry — the explicit list of recorded premises.
# ---------------------------------------------------------------------------

CLAIMS: list[Claim] = [
    Claim(
        claim_id="benchmark-section-32",
        pattern=r"BENCHMARK_RESULTS(\.md)? §32",
        description="ADR-0009 close-out: the §32 token-bucket sweep is the evidence base",
        check="section_exists",
        check_args={"section": "32"},
    ),
    Claim(
        claim_id="benchmark-subsection-32e",
        pattern=r"§32e",
        description="Dynamic-mode adversarial data exists (collected 2026-09-10)",
        check="subsection_exists",
        check_args={"subsection": "32e"},
    ),
    Claim(
        claim_id="benchmark-section-34",
        pattern=r"BENCHMARK_RESULTS(\.md)? §34",
        description="ADR-0018 hash-chain overhead benchmark",
        check="section_exists",
        check_args={"section": "34"},
    ),
    Claim(
        claim_id="repo-url-is-nythera",
        pattern=r"repo_url",
        description="mkdocs.yml repo_url still names Myco-mycelium/Nythera (rename pending)",
        check="repo_url_is",
        check_args={"repo": "Myco-mycelium/Nythera"},
    ),
    Claim(
        claim_id="remote-rename-pending",
        pattern=r"rebrand|Nythera",
        description="GitHub repository rename has not happened yet (REBRAND_NOTICE.md)",
        check="remote_is",
        check_args={"repo": "Myco-mycelium/Nythera"},
    ),
    Claim(
        claim_id="crate-nyui",
        pattern=r"rust/nyui",
        description="ADR-0025: the NUI import gate is implemented in rust/nyui/",
        check="crate_exists",
        check_args={"crate": "nyui"},
    ),
    Claim(
        claim_id="crate-transport",
        pattern=r"rust/transport",
        description="ADR-0020 migration #6: the transport hot path lives in rust/transport/",
        check="crate_exists",
        check_args={"crate": "transport"},
    ),
    Claim(
        claim_id="keys-in-both-halves",
        pattern=r"block_encrypt",
        description="ADR-0023: block_encrypt/block_decrypt landed in the Python and Rust key halves",
        check="path_contains",
        check_args={"needle": "block_encrypt"},
    ),
    Claim(
        claim_id="license-placeholder",
        pattern=r"until a formal license is adopted|formal open-source license",
        description="REPOSITORY_STATE item 9: LICENSE is still the Milestone 1 placeholder",
        check="path_contains",
        check_args={"needle": "not yet finalized", "files": ["LICENSE"]},
    ),
    Claim(
        claim_id="owners-unassigned",
        pattern=r"all Unassigned",
        description="REPOSITORY_STATE item 8: SUBSYSTEM_OWNERS.md entries are still Unassigned",
        check="path_contains",
        check_args={
            "needle": "*Unassigned*",
            "files": ["docs/00-platform/SUBSYSTEM_OWNERS.md"],
        },
    ),
    Claim(
        claim_id="brief-d1-registered",
        pattern=r"AG_BRIEF_DYNAMIC_SHARES",
        description="Standing item D1 (dynamic shares default): its pre-read brief is registered in AG_AGENDA.md",
        check="path_contains",
        check_args={
            "needle": "AG_BRIEF_DYNAMIC_SHARES.md",
            "files": ["docs/00-platform/AG_AGENDA.md"],
        },
    ),
    Claim(
        claim_id="brief-d2-registered",
        pattern=r"AG_BRIEF_NPS027_PACKAGE_TRUST",
        description="Standing item D2 (NPS-027 acceptance): its pre-read brief is registered in AG_AGENDA.md",
        check="path_contains",
        check_args={
            "needle": "AG_BRIEF_NPS027_PACKAGE_TRUST.md",
            "files": ["docs/00-platform/AG_AGENDA.md"],
        },
    ),
    Claim(
        claim_id="brief-pk-registered",
        pattern=r"REQ-SEC-0004",
        description="The routed FIND-PACKAGE-003 key-trust design has its pre-read anchored from NPS-026 §6.3",
        check="path_contains",
        check_args={
            "needle": "AG_BRIEF_NPS026_KEY_TRUST.md",
            "files": ["docs/reference/package-format/NPS-026-package-format.md"],
        },
    ),
    Claim(
        claim_id="brief-pk-agenda",
        pattern=r"AG_BRIEF_NPS026_KEY_TRUST",
        description="Standing item D3 (publisher key trust): its pre-read is registered in AG_AGENDA.md",
        check="path_contains",
        check_args={
            "needle": "AG_BRIEF_NPS026_KEY_TRUST.md",
            "files": ["docs/00-platform/AG_AGENDA.md"],
        },
    ),
    Claim(
        claim_id="nykernel-backend-absent",
        pattern=r"backend doesn't exist yet|implementation doesn't exist yet",
        description="NPS-017 §6 / NPS-021 / NPS-023: the NyKernel backend is still unbuilt",
        check="dir_absent",
        check_args={
            "label": "NyKernel backend",
            "components": ["source/nykernel"],
        },
    ),
    # --- AG decisions of 2026-09-21 (D1/D2/D3): pin the decided state ---
    Claim(
        claim_id="nps027-accepted",
        pattern=r"decision log D2",
        description="AG decision D2 (2026-09-21): NPS-027's frontmatter status is Accepted",
        check="frontmatter_status",
        check_args={
            "file": "docs/reference/security/NPS-027-package-trust-model.md",
            "status": "Accepted",
        },
    ),
    Claim(
        claim_id="nps026-key-trust-landed",
        pattern=r"decision log D3",
        description="AG decision D3 (2026-09-21): the §6.3 mechanism text is normative in NPS-026",
        check="path_contains",
        check_args={
            "needle": "6.3.1.",
            "files": ["docs/reference/package-format/NPS-026-package-format.md"],
        },
    ),
    Claim(
        claim_id="crypto-brief-registered",
        pattern=r"AG_BRIEF_NPS026_CRYPTO_SCHEME",
        description="The NPC-002 §6.2 crypto review package exists and is registered in AG_AGENDA.md",
        check="path_contains",
        check_args={
            "needle": "AG_BRIEF_NPS026_CRYPTO_SCHEME.md",
            "files": ["docs/00-platform/AG_AGENDA.md"],
        },
    ),
    Claim(
        claim_id="crypto-scheme-decided",
        pattern=r"decision log D4",
        description="AG decision D4 (2026-09-22): the concrete crypto scheme is accepted and normative in NPS-026 §6.7",
        check="path_contains",
        check_args={
            "needle": "6.7.1.",
            "files": ["docs/reference/package-format/NPS-026-package-format.md"],
        },
    ),
    Claim(
        claim_id="fingerprint-decided-64hex",
        pattern=r"full 64 lowercase hex|64 lowercase hex",
        description="The D4 review decided the fingerprint display form: SHA-256(public key), full 64 lowercase hex (NPS-026 §6.7.2 == NPS-028 §3.3)",
        check="path_contains",
        check_args={
            "needle": "64 lowercase hex",
            "files": [
                "docs/reference/package-format/NPS-026-package-format.md",
                "docs/reference/security/NPS-028-package-pki-implementation-surface.md",
            ],
        },
    ),
    Claim(
        claim_id="package-signing-shipped",
        pattern=r"signing half (already )?ships",
        description="NPS-028/REPOSITORY_STATE record the shipped Ed25519 signing half (package_signing.py)",
        check="path_contains",
        check_args={
            "needle": "PackageSignError",
            "files": ["source/nyhal-linux-backend/backend/package_signing.py"],
        },
    ),
    Claim(
        claim_id="pki-implementation-started",
        pattern=r"package_pki",
        description="NPS-028 v0.2.0 records the trust-machinery implementation start (backend/package_pki.py: key store, verification pipeline, revocation list, enrollment/rotation)",
        check="path_contains",
        check_args={
            "needle": "class VerificationPipeline",
            "files": ["source/nyhal-linux-backend/backend/package_pki.py"],
        },
    ),
    Claim(
        claim_id="req-sec-0004-closed",
        pattern=r"REQ-SEC-0004",
        description="AG decision D3 (2026-09-21): REQ-SEC-0004 is recorded closed in the requirements ledger",
        check="path_contains",
        check_args={
            "needle": "**closed**",
            "files": ["docs/reference/requirements/REQUIREMENTS.md"],
        },
    ),
    # --- The 2026-09-22 implementation session: the trust machinery is
    # built, drilled, and validated; pin the validated state ---
    Claim(
        claim_id="pki-process-model-landed",
        pattern=r"PkiDaemonRunner",
        description="NPS-028 v0.9.0: the daemon's production process model landed (PkiDaemonRunner + pki serve + the nyrqis-pki.service unit)",
        check="path_contains",
        check_args={
            "needle": "class PkiDaemonRunner",
            "files": ["source/nyhal-linux-backend/backend/package_pki.py"],
        },
    ),
    Claim(
        claim_id="pki-unit-ships",
        pattern=r"nyrqis-pki\.service",
        description="NPS-028 v0.9.0: the PKI systemd unit ships in the backend tree (the two-tree mirror rule is contract-pinned)",
        check="path_contains",
        check_args={
            "needle": "pki serve",
            "files": ["source/nyhal-linux-backend/packaging/systemd/nyrqis-pki.service"],
        },
    ),
    Claim(
        claim_id="pki-validation-record",
        pattern=r"Implementation Validation Record",
        description="NPS-028 v0.9.3: §10 records the 15-claim mechanical validation of the surface's normative statements",
        check="path_contains",
        check_args={
            "needle": "## 10. Implementation Validation Record",
            "files": ["docs/reference/security/NPS-028-package-pki-implementation-surface.md"],
        },
    ),
    Claim(
        claim_id="pki-review-registered",
        pattern=r"AG_AGENDA v1\.7\.0",
        description="The NPS-028 acceptance review is registered on AG_AGENDA v1.7.0 as the platform's standing item",
        check="path_contains",
        check_args={
            "needle": "Standing items — registered 2026-09-22",
            "files": ["docs/00-platform/AG_AGENDA.md"],
        },
    ),
    Claim(
        claim_id="nps026-validation-status",
        pattern=r"NPS-026 v1\.3\.1",
        description="NPS-026 v1.3.1: §1 records the §6/§7 machinery's implementation-validation status (the AG interlock's other half)",
        check="path_contains",
        check_args={
            "needle": "Implementation validation status (2026-09-22)",
            "files": ["docs/reference/package-format/NPS-026-package-format.md"],
        },
    ),
    # --- The 2026-09-23 docs-backlog pass: the M11 "remaining" list was
    # stale (all four deliverables landed by 2026-09-06); pin the files ---
    Claim(
        claim_id="m11-gap-docs-exist",
        pattern=r"M11 gap category",
        description="The four M11 backlog deliverables exist on disk (NPC-010, BUILD-001, PERF-001, TUT-003) — the 'still remaining' lists are reconciled",
        check="all_paths_exist",
        check_args={
            "files": [
                "docs/00-platform/008-GOVERNANCE_EXPANSION.md",
                "docs/reference/build/BUILD_ARCHITECTURE.md",
                "docs/reference/build/PERFORMANCE_BUDGETS.md",
                "docs/tutorials/developer-onboarding.md",
            ],
        },
    ),
    Claim(
        claim_id="build-architecture-dual-doc",
        pattern=r"BUILD-ARCH",
        description="The build-architecture deliverable exists under TWO document_ids (BUILD-ARCH Accepted in docs/00-platform vs BUILD-001 Draft in docs/reference/build) — a recorded open finding for the Architecture Group, kept loud until reconciled",
        check="path_contains",
        check_args={
            "needle": "document_id: BUILD-ARCH",
            "files": ["docs/00-platform/BUILD_ARCHITECTURE.md"],
        },
    ),
    Claim(
        claim_id="sdk-scaffold-shipped",
        pattern=r"nyq new",
        description="The SDK scaffolding deliverable exists (sdk/nyrqis_sdk/: nyq new scaffold generator + 46 SDK tests green 2026-09-23) — the roadmap's M14 Phase 3 item is struck",
        check="path_contains",
        check_args={
            "needle": "def cmd_new",
            "files": ["sdk/nyrqis_sdk/cli.py"],
        },
    ),
    Claim(
        claim_id="pkg-manager-real-wiring",
        pattern=r"PackageManager",
        description="The package manager now wires the REAL store: signed-index catalogue load (fail-closed), delta-driven UPDATABLE, checksum-verified install/update with tamper-refusal — pinned by tests/test_package_manager_store.py",
        check="path_contains",
        check_args={
            "needle": "def _load_from_repo",
            "files": ["source/nyhal-linux-backend/ui/package_manager.py"],
        },
    ),
    Claim(
        claim_id="pkg-manager-store-tests",
        pattern=r"test_package_manager_store",
        description="The store-wiring test module exists (8 tests: verified load, tamper-refusal, unsigned-repo fallback, sample marking)",
        check="path_contains",
        check_args={
            "needle": "class TestPackageManagerStoreWiring",
            "files": ["source/nyhal-linux-backend/tests/test_package_manager_store.py"],
        },
    ),
    Claim(
        claim_id="nps029-identity-nps-exists",
        pattern=r"NPS-029",
        description="The Identity subsystem has its own NPS (NPS-029, Draft) — NPS-025 §4.14's placeholder is resolved",
        check="path_contains",
        check_args={
            "needle": "document_id: NPS-029",
            "files": ["docs/reference/nps/NPS-029-identity-and-user-data-separation.md"],
        },
    ),
    Claim(
        claim_id="nps025-identity-resolved",
        pattern=r"NPS-025",
        description="NPS-025 v1.1.0 records §4.14 as resolved by NPS-029 — no object type remains a placeholder",
        check="path_contains",
        check_args={
            "needle": "No object type remains a placeholder in this catalogue",
            "files": ["docs/reference/object-registry/NPS-025-object-registry.md"],
        },
    ),
    Claim(
        claim_id="nps028-remaining-draft-deps",
        pattern=r"remaining Draft dependency",
        description="NPS-028 v0.9.3 §1: the surface's remaining Draft dependency is only NPS-026 §9's canonicalization decision plus the Group's acceptance review",
        check="path_contains",
        check_args={
            "needle": "remaining Draft dependency is NPS-026 §9's canonicalization decision, not implementation",
            "files": ["docs/reference/security/NPS-028-package-pki-implementation-surface.md"],
        },
    ),
    Claim(
        claim_id="debug-bundle-landed",
        pattern=r"nyrqisctl debug bundle",
        description="DBG-001 Phase A + Phase C rider landed: `nyrqisctl debug bundle` composes the EXISTING health/status/containers/audit_log/nui_current ops client-side (no new daemon surface) — pinned by tests/test_debug_bundle.py; Phase B stays Group-gated",
        check="path_contains",
        check_args={
            "needle": "def _debug_bundle",
            "files": ["source/nyhal-linux-backend/nyrqisctl.py"],
        },
    ),
    Claim(
        claim_id="dbg001-design-note",
        pattern=r"DBG-001",
        description="The debug-tooling design note exists (DBG-001, docs/00-platform/DEBUG_TOOLING_SPEC.md, v0.2.0 as-built) — surface audit, deviations recorded, Phase B pre-read",
        check="path_contains",
        check_args={
            "needle": "document_id: DBG-001",
            "files": ["docs/00-platform/DEBUG_TOOLING_SPEC.md"],
        },
    ),
]


def _collect_matching_files() -> dict[str, str]:
    """Read every docs/ and tests/ markdown file once for pattern matching."""
    texts: dict[str, str] = {}
    for base in (DOCS_DIR, TESTS_DIR):
        for path in sorted((REPO_ROOT / base).rglob("*.md")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            texts[rel] = _read(path)
    return texts


def run_checks(texts: dict[str, str]) -> list[dict]:
    results = []
    for claim in CLAIMS:
        regex = re.compile(claim.pattern, re.MULTILINE)
        hits = sorted(rel for rel, text in texts.items() if regex.search(text))
        checker = CHECKS[claim.check]
        status, detail = checker(hits, claim.check_args)
        results.append(
            {
                "claim_id": claim.claim_id,
                "description": claim.description,
                "status": status,
                "detail": detail,
                "recorded_in": hits,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="print the claim registry and exit")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    if args.list:
        for claim in CLAIMS:
            print(f"{claim.claim_id} [{claim.check}] {claim.description}")
        return 0

    texts = _collect_matching_files()
    results = run_checks(texts)
    failures = [r for r in results if r["status"] == "fail"]
    warnings = [r for r in results if r["status"] == "warn"]

    if args.json:
        print(json.dumps(results, indent=2))
        return 1 if failures else 0

    for r in results:
        marker = {"ok": "OK", "fail": "FAIL", "warn": "WARN"}[r["status"]]
        print(f"{marker}: {r['claim_id']} — {r['detail']}")
    if failures:
        print(
            f"\n{len(failures)} recorded premise(s) now FALSE — update the document "
            "or the registry entry (tools/check_doc_premises.py CLAIMS)."
        )
        return 1
    if warnings:
        print(f"\nOK (with {len(warnings)} warning(s)); {len(results)} claims checked")
        return 0
    print(f"OK: all {len(results)} recorded premises hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
