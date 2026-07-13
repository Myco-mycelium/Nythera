---
title: Transparent Compression Policy
document_id: NPS-005
version: 1.0.1
status: Draft
classification: Normative
subsystem: storage
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-13
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, ADR-0007, NPS-004]
---

# NPS-005 — Transparent Compression Policy

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
defines the policy layer that decides *how aggressively* to compress data
under NyFS (NPS-004 §4.5), separate from the codec mechanics themselves.

## 2. Scope

This specification covers compression-level selection by data category and
access pattern, and the recompression behavior for cold data. It does not
define the codec itself (ADR-0007) or the on-disk compression mechanics
(NPS-004 §4.5).

## 3. Data Categories

NyFS-backed data **MUST** be classified into at least the following
categories for compression policy purposes:

| Category | Examples | Default Level |
|----------|----------|----------------|
| Active application/game data | Installed games (via NPS-006 images), running applications | Zstd low (speed-favoring) |
| Documents and user files | User-created documents, project files | Zstd medium |
| Cold / archival data | Infrequently accessed files, old snapshots | Zstd high (ratio-favoring) |
| Incompressible / pre-compressed | Video, already-compressed media, encrypted containers | None (pass-through) |

## 4. Policy Rules

4.1. New writes **MUST** default to the category matching their location
(e.g. writes into a mounted game overlay default to "Active application/game
data") unless the application explicitly marks a write as incompressible
per NPS-004 §4.5.

4.2. NyFS **SHOULD** track access recency per file or extent and **MAY**
automatically migrate a file from "Active" to "Cold" classification after a
configurable period of inactivity, recompressing it at a higher level in
the background.

4.3. Recompression **MUST** run as a background, low-priority operation
that does not compete with foreground I/O for scheduling priority (NPS-002
§6.2), consistent with NTM-000 §4 ("Performance").

4.4. A user **MUST** be able to view and override the compression category
of any file or directory, per NTM-000 §4 ("Transparency") and NPC-001 §10
(User Ownership) — compression policy is a convenience default, not a
hidden system behavior.

## 5. Interaction with Game Images

5.1. The base, read-only layer of a game image (NPS-006) **MUST** use the
"Active application/game data" default level at install time, since it is
read-heavy and load-time-sensitive.

5.2. The writable overlay of a game image (NPS-006) **MUST** use the
"Documents and user files" default level, since it holds saves and
user-generated content read less frequently than the base image.

## 6. Failure and Degradation

6.1. If compression would cause a write to fail (e.g. insufficient CPU
headroom on a constrained device), NyFS **MUST** fall back to storing the
data uncompressed rather than failing the write, and **SHOULD** log the
fallback for later recompression.

6.2. Compression policy **MUST NOT** be able to cause data loss; in any
ambiguity between "compress aggressively" and "complete the write
successfully," completing the write **MUST** take priority.

## 7. Open Questions *(Informative)*

- **Status note (Milestone 9 review):** this document defines default
  compression levels per data category (§3), which are directly tied to
  ADR-0007's still-`Proposed` codec/level benchmarks. It remains `Draft`
  transitively — not for issues in its own normative content, but because
  accepting a policy that names specific default levels ahead of the ADR
  that sets them would be premature. It is expected to move to `Accepted`
  in the same review cycle as ADR-0007.
- The exact inactivity threshold for automatic Active→Cold migration
  (§4.2) requires usage-pattern data and is left unspecified pending
  benchmarking, per NPC-002 §5.2.
- Whether compression category should be exposed per-file in the standard
  file-properties UI or only through an advanced settings view is a UX
  decision deferred to the adaptive UI shell specification (Milestone M5).

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-12 | Initial draft |
| 1.0.1   | 2026-07-13 | Clarify Draft status is a transitive dependency on ADR-0007, not an issue in this document's own content (Milestone 9 review) |

---
**End of Document**
