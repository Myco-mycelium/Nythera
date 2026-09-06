---
title: Governance Expansion
document_id: NPC-010
version: 1.0.0
status: Draft
classification: Informative
owners:
  - Nyrqis Architecture
created: 2026-09-06
updated: 2026-09-06
ai_assisted: true
review_cycle: Quarterly
depends_on: [NPC-001, NPC-003, NPC-007]
---

# NPC-010 — Governance Expansion

This document expands on the governance foundations established in
NPC-001 (Project Constitution) and NPC-003 (Engineering Handbook),
providing detailed processes for RFCs, releases, deprecation,
versioning, branching, commits, and ADR workflow.

## 1. RFC Process

### 1.1 When an RFC is Required

An RFC (Request for Comments) is required for:

- **Normative changes** to any Accepted specification (NPS, ADR, NPC)
- **New subsystems** or major architectural changes
- **Breaking changes** to public APIs or ABIs
- **Policy changes** affecting governance or contribution process

### 1.2 RFC Lifecycle

```
Draft → Review → Accepted/Rejected/Deferred
```

1. **Draft**: Author creates RFC in `engineering/rfcs/` with template
2. **Review**: Minimum 7-day review period;至少 one Architecture Group member must review
3. **Decision**: Accept, Reject, or Defer with recorded rationale
4. **Implementation**: Accepted RFCs are tracked in the project roadmap

### 1.3 RFC Template

```markdown
# RFC-NNNN: Title

## Summary
One-paragraph description.

## Motivation
Why this change is needed.

## Detailed Design
Technical specification.

## Alternatives Considered
Other approaches evaluated.

## Drawbacks
Known limitations or risks.

## Unresolved Questions
Open items requiring further discussion.

## References
Related documents and prior art.
```

## 2. Release Process

### 2.1 Version Numbering

Nyrqis follows Semantic Versioning (SemVer):

- **MAJOR** (X.0.0): Breaking changes to public API/ABI
- **MINOR** (0.X.0): New features, backwards-compatible
- **PATCH** (0.0.X): Bug fixes, backwards-compatible

### 2.2 Release Cadence

- **Stable releases**: Every 3 months (quarterly)
- **Patch releases**: As needed for critical fixes
- **Pre-release**: Alpha/beta releases for major versions

### 2.3 Release Checklist

1. All CI checks passing
2. Documentation updated
3. CHANGELOG.md updated
4. Version bumped in all relevant files
5. Tag created: `v{MAJOR}.{MINOR}.{PATCH}`
6. Release notes published
7. GitHub Release created with binaries (if applicable)

## 3. Deprecation Policy

### 3.1 Deprecation Process

1. **Announce**: Add deprecation notice to documentation
2. **Timeline**: Minimum 2 minor versions before removal
3. **Migration**: Provide migration guide or automated tooling
4. **Remove**: Remove deprecated feature after timeline expires

### 3.2 Deprecation Notice Format

```markdown
> **Deprecated since v{VERSION}**: This feature will be removed in v{NEXT_MAJOR}.
> Use `{ALTERNATIVE}` instead.
```

### 3.3 Exceptions

Emergency deprecations (security vulnerabilities) may bypass the
timeline with Architecture Group approval.

## 4. Branching Strategy

### 4.1 Branch Types

| Branch | Purpose | Lifetime |
|--------|---------|----------|
| `main` | Production-ready code | Permanent |
| `develop` | Integration branch | Permanent |
| `feature/*` | New features | Temporary |
| `release/*` | Release preparation | Temporary |
| `hotfix/*` | Critical fixes | Temporary |

### 4.2 Branch Naming

```
feature/{ticket-id}-{short-description}
release/{version}
hotfix/{ticket-id}-{short-description}
```

### 4.3 Merge Strategy

- **Feature branches**: Squash merge to `develop`
- **Release branches**: Merge to `main` and `develop`
- **Hotfix branches**: Merge to `main` and `develop`

## 5. Commit Conventions

### 5.1 Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

### 5.2 Types

| Type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `style` | Formatting, no code change |
| `refactor` | Code restructuring |
| `perf` | Performance improvement |
| `test` | Adding tests |
| `build` | Build system changes |
| `ci` | CI configuration |
| `chore` | Maintenance tasks |

### 5.3 Examples

```
feat(ipc): add shared-memory zero-copy path

Implements NPS-003 §6.2 shared memory optimization.
Reduces IPC latency by ~40% for large payloads.

Closes #123
```

```
fix(seccomp): reject openat with O_CREAT in read-only containers

The previous implementation only checked capability sets at the
control-plane level, missing the data-plane enforcement gap
identified in FIND-BACKEND-002.
```

## 6. ADR Workflow

### 6.1 When an ADR is Required

- Architectural decisions with long-term impact
- Technology selection (languages, frameworks, libraries)
- Process changes affecting multiple subsystems
- Security-relevant design decisions

### 6.2 ADR Lifecycle

```
Proposed → Accepted/Rejected
         ↓
    Deprecated (if later superseded)
```

### 6.3 ADR Template

```markdown
# ADR-NNNN: Title

## Status
Proposed | Accepted | Deprecated | Superseded by ADR-XXXX

## Context
What is the issue motivating this decision?

## Decision
What is the change being proposed?

## Consequences
What are the trade-offs?

## Alternatives
What other options were considered?
```

### 6.4 ADR Review Requirements

- **Proposed**: Any contributor can propose
- **Accepted**: Requires Architecture Group review and approval
- **Deprecated**: Requires Architecture Group approval with rationale

## 7. Architecture Group

### 7.1 Responsibilities

- Review and accept/reject ADRs
- Review and accept/reject RFCs
- Approve major architectural changes
- Resolve cross-cutting concerns

### 7.2 Decision Making

- **Consensus preferred**: Discussion until agreement
- **Fallback**: Majority vote if consensus cannot be reached
- **Tie-breaking**: Project lead has final say

### 7.3 Meeting Cadence

- **Weekly sync**: 30-minute status update
- **Monthly deep dive**: 2-hour architecture review
- **Ad-hoc**: As needed for urgent decisions

## 8. Documentation Standards

### 8.1 Required Documentation

All normative changes MUST include:

1. **Specification update**: NPS, ADR, or NPC document
2. **Implementation notes**: Code comments, commit message
3. **Test coverage**: Unit tests, integration tests
4. **Migration guide**: If breaking change

### 8.2 Documentation Review

- All documentation changes require at least one review
- Technical accuracy verified by domain expert
- Style and clarity reviewed by documentation maintainer

## 9. Security Process

### 9.1 Vulnerability Reporting

- **Private reporting**: Security issues reported via private channel
- **Triage**: Security team triages within 48 hours
- **Fix timeline**: Critical vulnerabilities fixed within 7 days
- **Disclosure**: Coordinated disclosure after fix is available

### 9.2 Security Review

All changes touching security-relevant code require:

1. Security team review
2. Threat model update (if applicable)
3. Penetration testing (for critical changes)

## 10. Revision History

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-09-06 | Initial governance expansion |
