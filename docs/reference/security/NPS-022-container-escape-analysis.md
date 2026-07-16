---
title: Container Escape Analysis and Runtime Isolation
document_id: NPS-022
version: 1.0.0
status: Draft
classification: Normative
subsystem: security
owners:
  - Nythera Architecture
created: 2026-07-15
updated: 2026-07-15
ai_assisted: true
review_cycle: As needed
depends_on: [NTM-000, NPC-001, NPC-009, NPS-018, NPS-019, NPS-020, NPS-017]
---

# NPS-022 — Container Escape Analysis and Runtime Isolation

## 1. Status of This Document

This document is **normative**. RFC 2119 terms apply as in NPC-001. It is
**Phase 4** of the threat model, deepening `TB-CONTAINER`/`TB-BACKEND`
findings from Phase 2 — specifically `FIND-BACKEND-001`, which NPS-020 §7
deferred here.

Unlike Phases 1–3, this phase has real, running code to analyze
(`source/nyhal-linux-backend/`, merged externally and verified per
`REPOSITORY_STATE.md`'s reconciliation entry), not only specifications.
**This document does not treat that code's current behavior as the
correct target.** Where the code and the requirement diverge, the
requirement wins: findings here are resolved by stating what the backend
**MUST** do, and the code is flagged as non-conformant against that
requirement until it's updated — the same discipline `NPS-017` §5.1
already established for backend conformance generally.

## 2. Scope

This document covers container escape vectors specific to the Linux
Backend implementation as it actually exists, and the runtime isolation
guarantees `TB-CONTAINER`/`TB-BACKEND` are supposed to provide. It
supersedes `FIND-BACKEND-001` (which was written against the much
smaller `nyctr` PoC) with findings grounded in the fuller implementation.

## 3. Method

Findings below were produced by reading the actual implementation, not
inferring from its documentation. Specifically: `grep`-ing every call
site of `CapabilityManager.validate_operation()`, tracing how container
processes are actually launched (`_run_container_process()`), and
checking the cgroup setup path against a known real-world escape
technique (cgroup v1 `release_agent`). Each finding below states exactly
what was checked and what was found, so the finding can be re-verified
against a future version of the code rather than trusted on description
alone.

## 4. Findings

### `FIND-BACKEND-002` — Capability Enforcement Covers One Operation, Not All Operations

**What was checked:** every call site of `validate_operation()` across
the implementation.

**What was found:** exactly two non-test call sites exist, both in
`ipc/core.py`, both gating the IPC `send`/`call` path. `backend/container.py`
(container lifecycle: create, start, suspend, resume, terminate) and
`fuse/nyfs.py` (filesystem operations) contain **zero** references to the
capability model. There is no seccomp, no LSM (AppArmor/SELinux), no
`prctl`, and no syscall filtering anywhere in the codebase — confirmed by
searching for each, not merely absent from a status document's own
admission.

**Why this is more severe than the original `FIND-BACKEND-001`:** the
original finding (against `nyctr`) was "capability enforcement doesn't
exist yet." This finding is sharper: capability enforcement now exists,
but only mediates operations that happen to go *through the Python
orchestrator's own API*. Per NPS-018 §5's attacker profiles, a
compromised or malicious container is assumed capable of running its own
code inside its namespace — and any such code can trivially bypass every
capability check simply by making direct syscalls (open a raw socket,
read a file, `ptrace` a sibling process within the same namespace) rather
than calling into the orchestrator. The capability model, as implemented,
constrains what the *orchestrator* does on a container's behalf; it does
not constrain what the *container's own process* can do once it's
running. These are different things, and NPS-017 §4.2's current wording
("the backend SHALL be the sole arbiter of capability validity") doesn't
make that distinction explicit enough to catch this gap during
implementation review.

**Severity: Critical / High.** This isn't a hypothetical architectural
concern — it's the current, real state of the only implementation that
exists, and it means `ADR-0004`'s foundational containerization
guarantee (every application, capability-scoped) currently has no actual
enforcement against the primary attacker model this whole threat model
is built around.

**Resolution:** NPS-017 §4.2 amended (§5 below) to explicitly distinguish
**control-plane enforcement** (what the orchestrator permits itself to do)
from **data-plane enforcement** (what a running container process can
actually do via direct syscalls), and require conformant backends to
implement the latter — not merely the former. New `REQ-NYHAL-0004`
formalizes this. The current implementation is flagged as non-conformant
against the tightened requirement; per NPC-001 §6 this is now the
Linux Backend's single highest-priority remaining implementation item,
ahead of FUSE integration or performance benchmarking, since it's a
security gap rather than a completeness gap.

### `FIND-BACKEND-003` — cgroup v1 `release_agent` Exposure (Fallback Path Only)

**What was checked:** `_get_cgroup_root()` and the cgroup setup/cleanup
path in `backend/container.py`.

**What was found:** the implementation prefers cgroup v2 but falls back
to cgroup v1 (`/sys/fs/cgroup/memory`) when v2 is unavailable. The code
does not write to `release_agent` or `notify_on_release` itself — this is
not an actively-triggered vulnerability — but it also does not neutralize
or restrict access to those files within the container's namespace. cgroup
v1's `release_agent` mechanism is a well-documented, real container escape
technique (a process with write access to it can achieve host-level code
execution when the cgroup's last process exits), independent of this
project.

**Severity: Medium / Low** (requires the v1 fallback path specifically,
which is secondary to the preferred v2 path) **but easy to close
entirely.**

**Resolution:** `ADR-0016` governs NyFS/storage specifically and isn't the
right place for a container-runtime requirement; amended instead into
`NPS-017` §4.1 (Container Primitives, §5 below): when a backend falls back
to cgroup v1, the container's mount namespace **MUST NOT** expose write
access to `release_agent` or `notify_on_release`, and the backend
**SHOULD** prefer failing container creation over silently falling back
to an unhardened v1 path where v2 is expected to be available.

### `FIND-BACKEND-004` — Unsanitized Shell Interpolation of Container-Controlled Strings

**What was checked:** how `hostname` is passed into the `unshare`
invocation in both `backend/container.py` and the original `nyctr.py` PoC.

**What was found:** both use `f"hostname {hostname} 2>/dev/null; exec \"$@\""`,
directly interpolating the hostname string into a shell command line
without escaping. The blast radius is bounded — this shell string executes
*after* `unshare` has already created the new namespaces, so injected
commands run inside the container's own namespace, which that container's
process already has equivalent access to — but it's still an avoidable
bad pattern, especially if a hostname value were ever sourced from a
field trusted less than the container's own command (e.g., a package
manifest field, per `NPS-006` §3).

**Severity: Low.** Contained blast radius, but zero cost to fix.

**Resolution:** not severe enough to require a new normative MUST at the
NPS-017 contract level (this is an implementation-hygiene concern, not a
missing guarantee), but recorded here so it isn't lost, with a `SHOULD`
recommendation added in §5.

### `FIND-CONTAINER-004` — PID Namespace `/proc` Isolation (Confirmed Correct)

**What was checked:** whether `--mount-proc` is used alongside `--pid`
in the namespace setup, since omitting it is a common way PID namespace
isolation gets silently weakened (a fresh PID namespace without a
matching fresh `/proc` mount can still see host process entries through
the old mount).

**What was found:** both implementations correctly pair `--pid` with
`--mount-proc`. This is a positive control, confirmed by reading the
code — not merely assumed because a "PID namespace" was mentioned.

**Severity: N/A.** No finding requiring action; recorded so future
reviews don't have to re-derive that this was checked.

## 5. Specification Amendments

Applied directly, consistent with the pattern established in Phase 2
(NPS-020 §10):

**NPS-017** (NyHAL Backend Contract) — two amendments in the same
document: §4.2 requires backends to distinguish control-plane from
data-plane enforcement and implement both, closing `FIND-BACKEND-002`;
§4.1 adds a cgroup v1 fallback hardening requirement closing
`FIND-BACKEND-003`, plus a `SHOULD`-level shell-interpolation hygiene
note closing `FIND-BACKEND-004`.

Both amendments are applied to those documents directly (see their own
revision history) rather than only described here, per NPS-018 §8.

## 6. Implementation Status Against This Analysis

The current `source/nyhal-linux-backend/` implementation is **not
conformant** to the amended NPS-017 §4.2. This is stated plainly rather
than softened: the capability *tracking* infrastructure (grant, revoke,
attenuate, audit — all tested, per `REQ-NYHAL-0003`) is real and correct
as far as it goes, but "as far as it goes" currently excludes the
enforcement mechanism that would make it a security boundary rather than
bookkeeping. Per this document's own instruction (§1), the specification
is not being weakened to match this reality — the gap is logged as the
implementation's next required step, not resolved by lowering the bar.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-07-15 | Initial draft — Phase 4 of the threat model (container escape analysis and runtime isolation), grounded in the actual Linux Backend implementation rather than a hypothetical |

---
**End of Document**
