# Threat Model

The Nythera threat model is built in phases, tracked in
[`007-PROJECT_ROADMAP.md`](../../00-platform/007-PROJECT_ROADMAP.md)
Milestone 12. Each phase produces one or more documents; later phases
depend on earlier ones and are not started out of order.

| Phase | Status | Documents |
|-------|--------|-----------|
| 1 — Methodology & Trust Boundaries | **Done** | [`NPS-018`](NPS-018-threat-model-methodology.md) |
| 1 — Attack Surface Enumeration | **Done** | [`NPS-019`](NPS-019-attack-surface-enumeration.md) |
| 2 — STRIDE Analysis per Trust Boundary | **Done** | [`NPS-020`](NPS-020-stride-analysis.md) — 3 findings, 2 closed by direct amendment to NPS-001/NPS-003, 1 elevating the package-format gap category's priority |
| 3 — Privilege Boundaries & Capability Escalation Analysis | Not started | deepens `TB-CAPABILITY` findings from Phase 2 (`FIND-CAPABILITY-001`, `FIND-CAPABILITY-002`) |
| 4 — Container Escape Analysis & Runtime Isolation | Not started | deepens `TB-CONTAINER` / `TB-BACKEND` findings from Phase 2 |
| 5 — Secure Boot Threat Model | Not started | deepens `TB-BOOT` findings from Phase 2, extends ADR-0014 |
| 6 — AI Threat Model | Not started | deepens `TB-AI` findings from Phase 2, extends NPS-015 |
| 7 — Package Trust Model | Not started | deepens `TB-PACKAGE` findings from Phase 2 (`FIND-PACKAGE-001`), extends NPS-006 |

Phase order follows dependency, not the original review's listed order:
methodology and the surface catalog have to exist before anything can be
analyzed against them, and the four deep-dive phases (3–7) each expand on
specific trust boundaries that Phase 2's first pass will have already
touched at a survey level.
