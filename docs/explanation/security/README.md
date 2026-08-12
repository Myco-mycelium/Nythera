# Security — Explanation

Design rationale for the security model. The normative threat model lives
in [`docs/reference/security/`](../../reference/security/README.md)
(Milestone 12, phased); this page collects the reasoning pieces.

| Topic | Document |
|-------|----------|
| Why capabilities are part of the architecture, not a permission layer added later | [Why Capabilities Aren't Bolted On](why-capabilities-not-bolted-on.md) |

## Governing Specifications

- [NPS-010 — Container Runtime](../../reference/nps/NPS-010-container-runtime.md)
- [NPS-011 — Capability Registry](../../reference/capability-registry/NPS-011-capability-registry.md)
- [NPS-018 — NPS-024 (Threat Model)](../../reference/security/README.md)
- [ADR-0004 — Containerized execution](../../reference/adr/ADR-0004-containerized-execution.md), [ADR-0018 — Hash-chained audit log](../../reference/adr/ADR-0018-hash-chained-audit-log.md)
