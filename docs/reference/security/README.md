# Threat Model

The Nyrqis threat model is built in phases, tracked in
[`007-PROJECT_ROADMAP.md`](../../00-platform/007-PROJECT_ROADMAP.md)
Milestone 12. Each phase produces one or more documents; later phases
depend on earlier ones and are not started out of order.

| Phase | Status | Documents |
|-------|--------|-----------|
| 1 — Methodology & Trust Boundaries | **Done** | [`NPS-018`](NPS-018-threat-model-methodology.md) |
| 1 — Attack Surface Enumeration | **Done** | [`NPS-019`](NPS-019-attack-surface-enumeration.md) |
| 2 — STRIDE Analysis per Trust Boundary | **Done** | [`NPS-020`](NPS-020-stride-analysis.md) — 3 findings, 2 closed by direct amendment to NPS-001/NPS-003, 1 elevating the package-format gap category's priority |
| 3 — Privilege Boundaries & Capability Escalation Analysis | **Done** | [`NPS-021`](NPS-021-privilege-and-escalation-analysis.md) — 5 findings (2 carried from Phase 2, 3 new); 4 resolved by direct amendment or new artifact, 1 (governance-level) recorded but explicitly not a technical fix |
| 4 — Container Escape Analysis & Runtime Isolation | **Done** | [`NPS-022`](NPS-022-container-escape-analysis.md) — grounded in the real Linux Backend implementation (not a hypothetical); 4 findings, most severe to date (`FIND-BACKEND-002`: capability enforcement covers only IPC, not direct syscalls); 3 resolved by amending `NPS-017` directly, 1 confirmed-safe |
| 5 — Secure Boot Threat Model | **Done** | [`NPS-023`](NPS-023-secure-boot-threat-model.md) — first full pass on TB-BOOT (Phase 2 deferred without analyzing); found the Linux Backend has zero Secure Boot status visibility, and boot-phase transitions aren't order-validated at the API level. Both closed by amending `NPS-017`/`NPS-001`. A measured-boot/TPM attestation gap logged, not fixable by amendment. |
| 6 — AI Threat Model | **Done** | [`NPS-024`](NPS-024-ai-threat-model.md) — first full pass on TB-AI (no implementation exists yet, analyzed against the NPS-015 spec directly); 5 findings, 4 resolved by amending NPS-015 (unspoofable confirmation UI, suggestion audit log, corrected file-search capability, persistence-mechanism exclusion), 1 confirmed bounded by existing controls |
| 7 — Package Trust Model | **Done** | [`NPS-027`](NPS-027-package-trust-model.md) — deepens `TB-PACKAGE`: disposition of Phase 2's `FIND-PACKAGE-001` (responded by NPS-026 §6) plus 4 new findings (NPS-006 still checksum-only, publisher key enrollment/revocation, overlay/base provenance, package-event audit); NPS-006 §6 amended (v1.1.0), REQ-SEC-0003..0006. **Accepted 2026-09-21** (AG decision log D2) — the planned phase list is formally closed |

Phase order follows dependency, not the original review's listed order:
methodology and the surface catalog have to exist before anything can be
analyzed against them, and the four deep-dive phases (3–7) each expand on
specific trust boundaries that Phase 2's first pass will have already
touched at a survey level.

Post-phase follow-on: the implementation surface for the accepted
package trust model (Phase 7 + NPS-026 §6.3) is drafted as
[`NPS-028`](NPS-028-package-pki-implementation-surface.md) — key store,
verification pipeline, revocation channel, enrollment flow, audit
trail, and the `SURFACE-PKI-0001..0004` entries the next threat-model
pass will analyze. It is not a phase document; it is the build
checklist the phases left as their residual. The concrete crypto
scheme that implementation will use was decided 2026-09-22 — the
NPC-002 §6.2-reserved dedicated human review concluded (AG decision
log D4) and landed as NPS-026 v1.3.0 §6.7 (Ed25519 + SHA-256; the
key fingerprint is SHA-256(public key), displayed in full 64 lowercase
hex). **Implementation is landed through the production process model
(v0.9.0)**: key store, pipeline, revocation list + transport,
enrollment/rotation, custody, daemon authority + IPC, audit chain,
and the `PkiDaemonRunner`/`pki serve`/`nyrqis-pki.service` deployment
unit — only the by-design §5.3 stale-list deferral remains.
