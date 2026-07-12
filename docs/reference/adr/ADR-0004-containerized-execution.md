---
title: Containerized Execution Model for All Application Classes
document_id: ADR-0004
version: 1.0.0
status: Proposed
owners: [Nythera Architecture]
created: 2026-07-12
updated: 2026-07-12
depends_on: [NTM-000, NPC-001]
---

# ADR-0004 — Containerized Execution for All Application Classes

## Context
NTM-000 §4 requires that security be architectural rather than reactive.
Native applications, Windows compatibility processes, and Android runtime
apps all need a consistent security boundary rather than three separate,
inconsistent trust models.

## Decision (Proposed)
Every application — native, Windows-compatibility, or Android-runtime —
executes inside a container with an explicit, user-visible permission set
(camera, microphone, network, documents, USB, Bluetooth), per NPC-001 §9.
No application class receives implicit elevated privileges by virtue of its
runtime.

## Alternatives Considered
- **Sandbox Windows/Android apps only, trust native apps by default** —
  rejected; contradicts "Security is created through architecture," which
  applies uniformly, and would make native apps a preferred attack surface.
- **OS-wide single permission model with no per-app granularity** —
  rejected; violates NTM-000 "Transparency" and "User Ownership."

## Consequences
- Requires a capability registry (`docs/reference/capability-registry/`)
  enumerating all permission classes before any runtime is implemented
  (NPC-001 §9.3).
- Compatibility-layer performance overhead from containerization must be
  measured and documented per NPC-002 §5.2 before claims are published.

## Status
Proposed — pending Milestone M6 security specification work.
