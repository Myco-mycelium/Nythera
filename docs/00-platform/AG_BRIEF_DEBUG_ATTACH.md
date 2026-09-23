---
title: AG Brief — Container Debug Attach (DBG-001 Phase B, CAP-DEBUG-ATTACH)
document_id: AG-BRIEF-DBG-PHASEB
version: 1.0.0
status: Proposed
classification: Internal
owners:
  - Nyrqis Engineering
created: 2026-09-23
updated: 2026-09-23
ai_assisted: true
review_cycle: One sitting
depends_on: [DBG-001, NPS-010, NPS-011, NPS-017, ADR-0018, NPS-021]
---

# AG Brief — Container Debug Attach (DBG-001 Phase B)

## 1. Status of This Document

This is the decision package for DBG-001 §4 — the container-side debug
attach channel, the last ungated piece of the M14 Phase 3 debug-tooling
item. It follows the D3/D4/duality brief format: verified evidence,
options with consequences ledgers, one recommendation, overridable.
The brief proposes; the Group decides. Nothing in Phase B has been
implemented — that is the point of this sitting.

## 2. The Verified Evidence

**What exists (audited 2026-09-23):**

- The container is a namespace-isolated process tree (NPS-017 §4.1/
  §4.2): PID, mount, UTS, IPC namespaces per container, network when
  configured; seccomp-BPF installed in-container (FIND-BACKEND-002
  hardening); shell-free launcher (FIND-BACKEND-004).
- The ONLY way in today is `container_exec` (backend/container.py
  §7656): `nsenter(1)` joining the container's namespaces from the
  host side, run by the manager. The container gains nothing — the
  mediation is entirely host-side. Every exec is capability-checked
  and audit-logged.
- The operator surface (`nyrqisctl containers exec/checkpoint/kill`)
  rides the control plane with per-call authorization (ADR-0021's
  dedicated health socket carries probes only).
- The audit machinery is ADR-0018's hash chain (measured: ~6.4 µs
  append, §34), byte-identical to the PKI chain reuse (NPS-028 §7).
- The threat model has already judged adjacent surface: NPS-021
  (privilege/escalation) tightened NPS-017 §4.1/§4.2, and
  FIND-CAPABILITY-004 forced capability splitting. Isolation is the
  product (DBG-001 §4's own words).

**What does not exist:**

- No attach channel of any kind: no debugger entry path, no
  `CAP-DEBUG-ATTACH` in NPS-011's registry (§3 verified — the
  registry has no debug class), no PTY multiplexing, no
  debugpy/gdb/lldb staging.
- No "step-through, breakpoints" anywhere — the roadmap item's
  original wording. Phase A (the incident bundle) and the Phase C
  rider landed 2026-09-23; Phase B is the remaining scope.

**The actual gap:** debugging a misbehaving container today means
`containers exec python3 -m pdb ...`-style one-shot commands — no
interactive session, no live process inspection, no breakpoint
semantics. For the Rust cdylibs there is no in-container attach at
all (the host-side debugger would need the container's mount
namespace *plus* breakpoint privileges the seccomp profile denies).

## 3. The Regulatory Frame (what any option must satisfy)

Per NPS-011 §4.3, a capability whose risk is platform-level MUST NOT
be grantable through the standard prompt flow — it requires an
explicit administrative/developer-mode action. Per NPS-011 §5, adding
the capability requires an entry in the registry (ID, description,
risk tier, default grant) reviewed by the `security` subsystem owner.
Per the platform's own precedent (NPS-021), any new entry path into
the container must be judged against privilege-escalation findings,
and per ADR-0018 every use must be audit-chained. A launcher-mediated
design (the launcher joins the namespaces itself; the debuggee
container gains nothing) is the pattern `container_exec` already
establishes.

## 4. Options and Consequences

### Option A — Launcher-mediated attach, operator-only capability

Add `CAP-DEBUG-ATTACH` to NPS-011 (High risk, **denied by default**
per §4.3 — administrative/developer-mode grant only, never grantable
to application containers via manifest or prompt). The attach channel
is a new launcher-mediated op: the manager (host side) joins the
container's namespaces via the existing nsenter pattern and stages
debugpy (Python) or gdb/lldb (Rust) **from the host**, exposes a
debug-socket through the operator control plane only, and chains
every attach/detach in the ADR-0018 log. The container's seccomp
profile is never relaxed; the debugger runs outside the isolation
boundary, looking in.

Consequences:

- Isolation holds: the debuggee's in-container attack surface does
  not change (no new binary inside, no profile relaxation); the
  trust boundary moves host-ward, where the operator already sits.
- Step-through/breakpoints work for both Python services and Rust
  crates (symbols present in debug builds — verified in the surface
  audit).
- Cost: a real new control-plane op family (attach/detach/status),
  the debugger-staging machinery (host tools must match container
  Pythons — a version-skew tax), and an operator-education burden.
- Risk accepted: an operator-capability compromise equals full
  container compromise — but that is already true of `containers
  exec`, so the delta is small and honest.

### Option B — Developer-mode sidecar channel

Instead of one attach op, ship a developer-mode container manifest
flag (`debug: true`) that makes the launcher mount a host directory
into the container and start a debugpy/gdbserver inside at boot.

Consequences:

- Simplest mental model for developers; works with existing tools.
- BUT it puts debugger binaries inside the isolation boundary,
  requires relaxing the seccomp profile (ptrace is exactly what
  FIND-BACKEND-002's hardening denies), and creates a class of
  containers whose production image differs from the debugged one —
  the "works in debug, breaks in prod" inversion, with the debug
  container being the *more* privileged one.
- It also multiplies the threat-model surface: every debugged
  container is now a ptrace-capable process tree. NPS-021 would need
  a new pass.

### Option C — No attach; strengthen the observational surface

Do not build Phase B. Extend Phase A instead (more ops composed into
the bundle, a `watch` mode, core-dump capture on crash via the
existing recovery manifest).

Consequences:

- Zero new trust surface; the roadmap item closes as "observational
  only, by decision" — an honest record, like the M11
  performance-budget item.
- The platform keeps paying the debugging tax: every hard bug costs a
  one-shot exec round-trip instead of an interactive session, and
  crash forensics stay post-mortem only.

## 5. Recommendation

**Option A**, clearly overridable. It is the only option that adds the
debugging capability without weakening any isolation property the
threat model has already paid for, it reuses the launcher-mediated
pattern the codebase already trusts, and its cost is machinery — not
a new class of risk. Option B's convenience is real but buys it by
moving the debugger inside the boundary, which is the exact trade the
threat model exists to scrutinize. If the Group's judgment is that
interactive debugging is not yet worth any new surface, Option C is
an honest close, not a failure.

Downstream if accepted: NPS-011 v1.4.0 (the new entry, §5 process),
DBG-001 §4 moves from proposed to normative, a new control-plane op
family with per-call authorization (implementation follows the
NPS-028 §3.2 authority pattern), NPS-021 addendum (one escalation
pass over the attach path), and the roadmap item finally strikes as
done. If rejected: Option C's wording lands instead and the item
closes with the decision recorded.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 1.0.0   | 2026-09-23 | Initial brief — surface audit, regulatory frame, three options with ledgers, recommends Option A (launcher-mediated, operator-only, audit-chained) |
