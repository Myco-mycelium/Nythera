---
title: Debug Tooling — `nyrqisctl debug` (design note for the M14 Phase 3 item)
document_id: DBG-001
version: 0.3.0
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
2. ~~No redaction pass yet~~ — **resolved (v0.3.0)**: redaction is
   implemented and default-on (`--redact/--no-redact`): vault
   aggregates (logical/physical bytes, warned-container counts) are
   stripped client-side from the status/health replies, the volume
   count is kept, and the redaction is marked in each reply plus
   `meta.json`. The vault values are cached aggregates only
   (ipc/service.py), so nothing secret was ever at stake — the point
   is that capacity figures do not belong in a sharing artifact by
   default.
3. ~~No chain head hash in the audit tail~~ — **resolved, with a
   wire-contract correction (v0.3.0)**: two findings. First, the
   0.2.0 bundle's global `audit_log` call was **wrong on the wire** —
   the op requires a specific `container_id` (there is NO daemon-wide
   trail); a real daemon refuses it, and only the scripted tests hid
   this. The trail is now captured **per container** (`audit.json`
   inside each container's detail). Second, the chain-head path is
   served by `--chain-id ID`: the existing `get_audit_summary` /
   `verify_audit_chain` ops run per supplied id and land in
   `audit-chains.json`. There is no chain-LISTING op, so ids are
   operator-supplied — auto-discovery would be new daemon surface and
   stays out of scope. Bonus find of the same class as the
   PackageManager duplicate-method bug: `build_payload` maps
   `"audit-summary"` twice (container variant first-wins shadows the
   chain variant), so the bundle composes the chain ops' wire shapes
   directly — the shadow is recorded here, not silently relied on or
   unilaterally fixed (it is outside this document's surface).
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
   redaction default-on, 9/9 tests green in
   `tests/test_debug_bundle.py`.
2. Phase B (attach) still needs a Group decision on
   `CAP-DEBUG-ATTACH` and the launcher-mediated attach channel (this
   note is the pre-read). The roadmap item stays OPEN for Phase B —
   striking it as done would be false.
3. The `build_payload` duplicate `"audit-summary"` mapping (deviation
   3) should be fixed in `nyrqisctl` proper — one rename or explicit
   dispatch — by whoever owns the CLI surface; the bundle does not
   depend on it either way.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 0.1.0   | 2026-09-23 | Initial draft — surface audit, three-phase split, the trust case for the attach channel |
| 0.2.0   | 2026-09-23 | Phase A + Phase C rider landed; §3/§5 rewritten as built with four recorded deviations; §6 updated — Phase B stays Group-gated, item stays open |
| 0.3.0   | 2026-09-23 | Deviations 2+3 resolved: redaction default-on (--redact/--no-redact); per-container audit trails (correcting a real wire-contract violation the scripted tests had masked); --chain-id summary+verification capture; the build_payload "audit-summary" shadow recorded for its owner |
