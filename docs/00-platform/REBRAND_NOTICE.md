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
- **The repository name and GitHub URL.** The GitHub repository
  (`Myco-mycelium/Nythera`) keeps the old name for now — renaming it is a
  manual step pending a maintainer (see "How to complete the rename
  later"). The local directory was renamed to `Nyrqis/` on 2026-08-12.
  The only remaining occurrences of the old name in the tree (outside the
  rebrand records themselves — this notice, the CR-0035 / Repository
  State entries, and the changelog naming note) are exactly these URL
  references, deliberately left pointing at the current repository name
  because GitHub redirects renamed repositories automatically:
  - `mkdocs.yml` — `repo_url`
  - `docs/implementation_plan.md` — footnotes 1–5
  - `docs/tutorials/first-repository-tour.md` — the `git clone` URL
- **Git history.** Commit messages and authors before this date are
  immutable and retain the old name.
- **Generated artifacts.** The built documentation site (`site/`) and
  Python bytecode caches (`__pycache__/`) are git-ignored and are
  regenerated with the new name on the next build.

## How to complete the rename later

Status as of 2026-08-12: the code, docs, and local directory are renamed;
**the GitHub repository rename is the one remaining step** and needs a
maintainer with admin access (not possible from this environment — no
`gh` CLI / credentials):

1. **Rename the GitHub repository** (Settings → General → Repository
   name): `Myco-mycelium/Nythera` → `Myco-mycelium/Nyrqis`. GitHub
   redirects the old URLs automatically.
2. **Update the three URL references** listed above to
   `Myco-mycelium/Nyrqis` (links keep working before and after the rename
   via redirect, so this is safe to do at any point).
3. **Rename the local checkout directory** — **done 2026-08-12** (no
   tracked file hard-codes the path; verified by grep).
4. **Rebuild the documentation site** — **done 2026-08-12** (`site/` is
   git-ignored; regenerated with the new branding via `mkdocs build`).
5. **Add a follow-up entry to this notice** recording the completion.

## Name review (2026-08-12)

Desk-check performed before committing to the new name; not legal advice.

- **`Nyrqis` collision check — clear.** No operating system, Linux
  distribution, software project, company, domain, or registered
  trademark was found under the exact name `Nyrqis`. The only trace is a
  minor fictional location ("Nyrqis Sanctuary") inside a hobbyist fantasy
  setting on World Anvil — no practical or legal conflict. The name is
  highly distinctive.
- **What the old name collided with.** `Nythera` is an established
  commercial name in tabletop gaming (the *Nythera* campaign for
  Malifaux / Through the Breach, published by Wyrd Miniatures) and a
  well-known game character (Artix Entertainment), plus assorted
  hobbyist projects — the collision this rebrand avoids.
- **`Ny` prefix risk — low.** Keeping the prefix in internal
  architectural component names (`NyHAL`, `NyFS`, `NyKernel`, `nygi`)
  is standard practice for OS architecture naming (compare `nt`/`sys`/
  `vfs`) and reads as descriptive, not a consumer-facing brand; a user
  encountering `NyFS` will not confuse it with a tabletop RPG supplement
  or game character.

## Verification

- `grep -ri nythera` over git-tracked files returns only the three
  intentional GitHub URL references above, plus the rebrand records
  themselves (this notice, the CR-0035 / Repository State entries, and
  the changelog naming note).
- Backend tests: `python3 test_backend.py` — all pass.
- `mkdocs build --strict` — zero warnings.
