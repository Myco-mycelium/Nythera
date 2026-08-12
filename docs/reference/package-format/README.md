# Package Format

The installable unit format is defined in
[`NPS-026-package-format.md`](NPS-026-package-format.md) — the `.nypkg`
container: manifest, digital signatures, integrity tree, compression,
delta updates, streaming install, rollback, and dependency resolution.

- **Status:** Draft. The document is the "package-format NPS" deferred by
  NPS-006 §2 and §9 (install-time production of `.nygi` files), and the
  direct response to threat-model finding `FIND-PACKAGE-001` (NPS-020
  §8): `.nygi` integrity relies on checksums alone, which don't establish
  publisher authenticity.
- **Elevated priority:** this gap category was moved above its original
  Milestone 11 list position because of that finding — see
  `007-PROJECT_ROADMAP.md` Milestone 11, gap 7.
