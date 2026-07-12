---
title: AI Collaboration Protocol
document_id: NPC-002
version: 1.0.1
status: Accepted
classification: Normative
owners:
  - Nythera Architecture
created: 2026-07-12
updated: 2026-07-12
ai_assisted: true
review_cycle: Annual
depends_on: [NTM-000, NPC-001]
---

# NPC-002 — AI Collaboration Protocol

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It
governs how AI tools (including large language models such as Claude) **MAY**
be used to draft specifications, generate code, or assist engineering work on
the Nythera project itself. It is distinct from NTM-000 §9, which governs
AI *inside the shipped operating system*.

---

## 2. Purpose *(Informative)*

Nythera is expected to be built with significant AI assistance. Without clear
rules, AI-generated content can silently introduce architectural drift,
unverified technical claims, or license contamination. This protocol exists
to make AI collaboration productive and auditable rather than forbidden.

---

## 3. Attribution

3.1. Any document, specification, or source file substantially drafted with
AI assistance **MUST** include an `ai_assisted: true` field in its
front-matter.

3.2. Commit messages for AI-assisted contributions **SHOULD** include a
trailer, e.g. `Assisted-by: Claude`.

3.3. Attribution is a transparency record, not a quality judgment. AI-assisted
documents are held to the same review bar as any other document.

---

## 4. Human Authority

4.1. An AI **MUST NOT** be the sole approver of any `Accepted` document. A
named human owner **MUST** review and accept responsibility for correctness.

4.2. AI **MAY** propose ADRs, NPS drafts, or code, but such proposals enter
the same Draft/Review/Accepted lifecycle as human proposals (NPC-001 §5).

4.3. Architectural decisions that touch security boundaries, data ownership,
or public compatibility guarantees **MUST** receive explicit human sign-off
regardless of how much of the surrounding text was AI-drafted.

---

## 5. Verifiability

5.1. AI-generated technical claims about external systems (e.g. Windows API
behavior, ARM instruction semantics, filesystem internals) **SHOULD** be
independently verified before a document reaches `Review` status, since
models can produce plausible but incorrect technical detail.

5.2. Performance numbers, benchmarks, or compatibility claims **MUST NOT** be
published in `Accepted` documents unless backed by a reproducible test
referenced in `tests/`.

---

## 6. Scope Boundaries

6.1. AI tools **MAY** be used for: drafting documentation, generating
boilerplate code, proposing test cases, summarizing discussions, and
suggesting ADR alternatives.

6.2. AI tools **MUST NOT** be treated as an authoritative source for legal
matters (licensing, trademark, patent) or security-critical cryptographic
implementations without dedicated human expert review.

6.3. Generated code that interacts with untrusted input (parsers, format
loaders, network protocol handlers) **MUST** pass through the normal security
review process defined in a future `SECURITY_REVIEW.md` before merging —
AI assistance does not shorten this path.

---

## 7. Continuity Across Sessions *(Informative)*

Because AI assistants used in this project typically do not retain memory
between sessions, contributors **SHOULD** maintain `REPOSITORY_STATE.md` as
the canonical, human-readable snapshot of project status, and **SHOULD**
provide it (or link it) at the start of a new AI-assisted session so proposed
work stays consistent with what already exists.

---

## 8. Prohibited Uses

The following are prohibited regardless of convenience:

- Using AI output to fabricate benchmark results, citations, or standards
  compliance claims.
- Using AI to generate content that circumvents Section 9 of NPC-001
  (Security Baseline).
- Presenting AI-drafted architectural decisions as already `Accepted` without
  going through Section 6 of NPC-001.

---

## Revision History

| Version | Date       | Change                  |
|---------|------------|--------------------------|
| 1.0.0   | 2026-07-12 | Initial draft for review |
| 1.0.1   | 2026-07-12 | Add `ai_assisted: true` retroactively to all existing normative documents to comply with §3.1. Architecture Group review completed. Status: Draft → Accepted (Milestone 2). |

---
**End of Document**
