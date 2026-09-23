---
title: Debug Tooling — `nyrqisctl debug` (design note for the M14 Phase 3 item)
document_id: DBG-001
version: 0.2.0
status: Draft
classification: Informative
owners:
  - Nyrqis Engineering
created: 2026-09-23
updated: 2026-09-23
ai_assisted: true
review_cycle: As needed
depends_on: [NPC-001, NPS-010, ADR-0018, ADR-0021, NPS-019]
---

# DBG-001 — Debug Tooling: `nyrqisctl debug`

## 1. Status of This Document

This is the design note for the roadmap's M14 Phase 3 "Debug tooling —
`nyrqis debug` with step-through, breakpoints" item, recorded here
because the existing surfaces already answer most of what the item
name implies, and the remaining scope should be sized honestly before
any code lands. This document is **informative** — it proposes, it
does not decide.

**Implementation status (2026-09-23):** Phase A and the Phase C rider
have landed as proposed (§3/§5 as built, deviations recorded in
place). Phase B remains a Group decision — nothing in this document
asks for it to be built yet.

## 2. What already exists (the audit, 2026-09-23)

The phrase "step-through, breakpoints" suggests nothing is there. In
fact the platform already ships a layered diagnostic surface:

| Layer | Surface | Already answers |
|---|---|---|
| Daemon liveness | `nyrqisctl ping` / `status` / `health` (dedicated health socket, ADR-0021) | uptime, serve-loop liveness, container counts, IPC registry size, crash-recovery summary, vault aggregate |
| Container state | `nyrqisctl containers list/health/stats/logs/top/net` | state machine position, live resource stats, captured stdout/stderr, process tree, network stats |
| Container interaction | `nyrqisctl containers exec/checkpoint/restore/kill` | command execution inside a running container, filesystem checkpointing |
| Authority trail | ADR-0018 hash-chained audit log; `nyrqisctl` export | every capability grant/revoke, every control op |
| Crash forensics | `DaemonStateFile` + recovery manifest | previous daemon's pid, orphaned containers left behind |

So the honest gap is **not** "no debugging" — it is that these
surfaces are *observational only*. What does not exist:

1. **No structured bundle**: an operator debugging an incident must
   run five commands and correlate timestamps by hand.
2. **No container-side interactive debugging**: `containers exec`
   runs a command, but there is no attach-a-debugger path (the
   container is a namespace-isolated process tree; a debugger must
   enter via the launcher, which today has no such channel).
3. **No breakpoint semantics for the NUI/shell layer**: the UI
   runtime (ADR-0025) has no introspection hook.

## 3. Phase A as built: the incident bundle (no new trust surface)

`nyrqisctl debug bundle [--out DIR] [--container ID] [--audit-tail N]`
— one command that assembles a timestamped incident bundle (landed
2026-09-23, pinned by `tests/test_debug_bundle.py`):

- `health.json` / `status.json` / `containers.json` — the existing
  ops' full replies
- `per-container.json` — per-container logs (both streams, tail 500),
  stats, process tree (`top`, summary-only), network stats
- `audit-log.json` — the last N control-plane audit records
  (`audit_log` op, default tail 100)
- `nui-current.json` — the Phase C rider (§5)
- `meta.json` — bundle format, generation timestamp, socket,
  containers requested, and the provenance note: composed from
  existing authorized ops, no new daemon surface

**Trust discipline (as proposed):** the bundle composes existing ops
with their existing authorization; `debug bundle` is a *client-side*
convenience loop, not a new daemon op — nothing new to authorize,
nothing new in the threat model except volume (SURFACE-DBG-0001
candidate: bundle exfiltration; mitigation: `--out` is
operator-local).

**Deviations from the 0.1.0 draft (recorded, not hidden):**

1. *One flat bundle directory* instead of a `containers/<id>/` tree —
   per-container detail lives in one `per-container.json` keyed by id.
2. *No redaction pass yet.* The draft's "redaction strips vault
   figures by default" is not implemented; the vault aggregate in
   `status.json` is CACHED figures only (ipc/service.py), but a
   redaction option should land before bundles become a sharing
   workflow. Tracked as a follow-up, not silently dropped.
3. *No chain head hash in the audit tail* — `audit_log` returns the
   bounded trail; the head-hash re-verification path needs the
   chain-id-bearing ops and lands with the Phase B decision package.
4. *No `state.json` summary.* Surfacing the daemon state file's
   summary touches the recovery-manifest disclosure rule; deferred
   until that surface is decided rather than risked in a convenience
   loop.

## 4. Proposed design — Phase B: container-side attach (gated)

An `attach` path — `nyrqisctl containers debug <id>` — that enters the
container's namespace with a debugger. This one **is** a new trust
surface and needs the Group:

- A container's isolation is the product (NPS-010, NPS-017); an
  attach channel is a deliberate isolation hole, even operator-only.
- It must be **capability-gated** (a new `CAP-DEBUG-ATTACH` class in
  NPS-011, denied-by-default per NPS-011 §4.3), **audit-chained**
  (every attach/detach in the ADR-0018 log), and **launcher-mediated**
  (the launcher joins the namespaces itself — the debuggee container
  gains nothing).
- "Breakpoints/step-through" for the Python services is then just
  `debugpy`/`pdb` over the attach channel; for the Rust crates it is
  a `gdb`/`lldb` attach (the crates ship cdylibs — symbols are
  already present in debug builds).

## 5. Phase C as built: NUI introspection rider

Landed with Phase A as proposed: the bundle includes
`nui-current.json` from the existing `nui_current` op (service `nui`),
read-only, supplementary (a failure is recorded in the bundle, not
fatal). The deeper `nyrqisctl debug nui` presentation can build on
this rider when there is demand.

## 6. What this note asks

1. Phase A (the bundle) and the Phase C rider are **landed**
   (2026-09-23) — client-side composition of existing authorized ops,
   6/6 tests green in `tests/test_debug_bundle.py`.
2. Phase B (attach) still needs a Group decision on
   `CAP-DEBUG-ATTACH` and the launcher-mediated attach channel (this
   note is the pre-read). The roadmap item stays OPEN for Phase B —
   striking it as done would be false.
3. Phase A follow-ups before bundles become a sharing workflow: the
   redaction option (deviation 2) and the chain-head re-verification
   path (deviation 3).

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 0.1.0   | 2026-09-23 | Initial draft — surface audit, three-phase split, the trust case for the attach channel |
| 0.2.0   | 2026-09-23 | Phase A + Phase C rider landed; §3/§5 rewritten as built with four recorded deviations; §6 updated — Phase B stays Group-gated, item stays open |
