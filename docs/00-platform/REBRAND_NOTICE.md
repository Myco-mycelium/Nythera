# Rebrand Notice: Nythera → Nyrqis

| Field | Value |
|-------|-------|
| Status | Accepted |
| Date | 2026-08-12 |
| Linked change request | CR-0035 |
| Reason | Name collision — "Nythera" already exists elsewhere; renamed to avoid legal/IP conflict |
| Applies to | Everything in the repository except the explicit exclusions below |

## Summary

The project name **Nythera** is renamed to **Nyrqis** everywhere in the
repository: documentation, code identifiers, environment variables,
temporary-file prefixes, file names, and the license placeholder.

The rename is **literal and mechanical**: every occurrence of the name is
replaced case-for-case (`Nythera` → `Nyrqis`, `nythera` → `nyrqis`,
`NYTHERA` → `NYRQIS`). No other words are affected.

## What changed

- All documentation: `docs/`, `README.md`, `CONTRIBUTING.md`, and the
  MkDocs site name (`mkdocs.yml` `site_name`).
- File names, with all internal references updated:
  - `source/nyhal-linux-backend/nythera_backend.py` →
    `source/nyhal-linux-backend/nyrqis_backend.py`
  - `docs/00-platform/000-THE_NYTHERA_MANIFEST.md` →
    `docs/00-platform/000-THE_NYRQIS_MANIFEST.md` (NTM-000 keeps its ID)
- Code identifiers:
  - Environment variable `NYTHERA_LOG_LEVEL` → `NYRQIS_LOG_LEVEL`
    (`backend/launcher.py`)
  - Seccomp policy temporary-file prefix `nythera-policy-` →
    `nyrqis-policy-` (`backend/container.py`)
- `LICENSE` placeholder holder: "Nythera Project License / Copyright (c)
  2026 Nythera Contributors" → "Nyrqis Project License / Copyright (c)
  2026 Nyrqis Contributors".
- Git commit identity for new commits: `Nyrqis Bootstrap
  <bootstrap@nyrqis.local>` (previously `Nythera Bootstrap
  <bootstrap@nythera.local>`).

## What deliberately did NOT change

- **The `Ny` prefix.** The prefix now means *Nyrqis* instead of *Nythera*,
  but every existing `Ny`-prefixed term stays exactly as-is: `nyhal`
  (Nyrqis HAL), `nyctr`, `nyfs`, `nygi`, `nypkg`, `NyKernel`, and the
  `source/nyhal-linux-backend/` directory. This was a conscious decision:
  the `Ny` prefix is a brand fragment, not the conflicting name itself.
- **The repository name and GitHub URL.** The local directory (`Nythera/`)
  and the GitHub repository (`Myco-mycelium/Nythera`) keep the old name
  for now. The only remaining occurrences of the old name in the tree
  (outside the rebrand records themselves — this notice and the CR-0035 /
  Repository State entries) are exactly these URL references:
  - `mkdocs.yml` — `repo_url`
  - `docs/implementation_plan.md` — footnotes 1–5
  - `docs/tutorials/first-repository-tour.md` — the `git clone` URL
- **Git history.** Commit messages and authors before this date are
  immutable and retain the old name.
- **Generated artifacts.** The built documentation site (`site/`) and
  Python bytecode caches (`__pycache__/`) are git-ignored and are
  regenerated with the new name on the next build.

## How to complete the rename later

When the repository itself is renamed:

1. Rename the GitHub repository `Myco-mycelium/Nythera` →
   `Myco-mycelium/Nyrqis`.
2. Update the three URL references listed above.
3. Rename the local checkout directory and update any scripts that
   hard-code its path.
4. Delete `site/` and rebuild the documentation.
5. Add a follow-up entry to this notice recording the completion.

## Verification

- `grep -ri nythera` over git-tracked files returns only the three
  intentional GitHub URL references above, plus the rebrand records
  themselves (this notice and the CR-0035 / Repository State entries).
- Backend tests: `python3 test_backend.py` — all pass.
- `mkdocs build --strict` — zero warnings.
