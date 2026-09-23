---
title: Debug Tooling — `nyrqisctl debug` (design note for the M14 Phase 3 item)
document_id: DBG-001
version: 0.6.0
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
   `audit-chains.json`. There is no   chain-LISTING op, so ids are
   operator-supplied — auto-discovery would be new daemon surface and
   stays out of scope. Bonus find of the same class as the
   PackageManager duplicate-method bug, **now repaired (CR-0037):**
   the chain summary had never had its own command — the registration
   block reused alert-summary's parser variable, breaking
   `nyrqisctl alert-summary` outright, and the same commit series had
   left a stray unconditional `raise ValueError` mid-`build_payload`
   that made ~300 registered commands crash with "unknown command".
   The chain summary is now `audit-chain-summary --chain-id`,
   alert-summary is restored, and the whole parse→payload surface is
   pinned by `test_nyrqisctl_payload_surface.py`.
4. *No `state.json` summary.* Surfacing the daemon state file's
   summary touches the recovery-manifest disclosure rule; deferred
   until that surface is decided rather than risked in a convenience
   loop.

## 4. Phase B — container-side attach: DECIDED (D7), not yet implemented

**The Group decided 2026-09-23 (AG decision log D7): Option B — the
developer-mode manifest class — overriding this note's launcher-
mediated recommendation.** What was decided:

- A `debug: true` manifest class plus a `CAP-DEBUG-ATTACH` capability
  (NPS-011 v1.4.0 to add the entry).
- debugpy (Python) / gdbserver (Rust) run **inside** the debugged
  container; the container's seccomp profile's ptrace denial is
  relaxed **for that manifest class only** — a named,
  capability-gated exception to the FIND-BACKEND-002 hardening.
- The isolation widening is accepted with eyes open (the brief's
  ledger): debug and production images diverge, and NPS-021 requires
  an escalation-pass addendum over the new surface **before
  implementation lands**.
- Every attach/detach session remains audit-chained (ADR-0018).

Not yet landed (the D7 implementation work items, in order): ~~the
NPS-021 addendum; NPS-011 v1.4.0's registry entry~~ **done 2026-09-23**;
~~the launcher's manifest-class plumbing~~ **done 2026-09-23, same day
as the §4.1 review (see below)**; debug-image staging; the
`nyrqisctl containers debug` op family. The "step-through, breakpoints"
roadmap wording now has a decided design AND its manifest-class
foundation behind it; the roadmap item stays `[~]` until the attach
channel itself lands.

### 4.1 Design review against NPS-021 §5.5 (2026-09-23)

Before any code lands, each of §5.5's five MUST requirements is mapped
to the concrete enforcement site it will live in (sites verified against
the tree, not assumed):

| # | Requirement (NPS-021 §5.5) | Enforcement site (verified) | Fit |
|---|---|---|---|
| 1 | High / denied-by-default / class-conditional / operator-only | `Capability` enum gains `CAP_DEBUG_ATTACH`; the class condition is enforced in the **grant path** — `CapabilityManager` gains a class-requirements map so the check is centralized and testable, and container creation validates the request against the manifest class | Fits; open implementation choice recorded below |
| 2 | Class visible in every state surface | The container state builders (status/inspect reply, containers list, the debug bundle's `meta.json`) each gain an explicit debug-class field | Fits; a pure addition to existing reply shapes |
| 3 | Relaxation construction-time only | `build_policy`/`build_allowlist_policy` currently take only `capabilities` and apply/subtract the static `_ALWAYS_DENY`; both gain a **keyword-only `debug_class=False`** parameter that, when True, admits the ptrace family at *policy build*. Independent confirmation: seccomp filters are one-shot (the `reload_policy` docstring) — a runtime relaxation is not even mechanically available | Fits; the parameter is the loud, citable gate §4.8 fence 2 demands |
| 4 | Loopback-default debug endpoints | Containers already default to an isolated loopback-only network namespace; loopback binding inside the container is therefore the free default, and any wider binding requires `CAP-NETWORK-BIND` (High, prompt-required) per its own registry row | Fits; no new mechanism — the requirement becomes a default argument + validation in the future attach ops |
| 5 | Manifest class in the audit chain | `CapabilityGrant` (the grant audit trail) gains the container's class; the ADR-0018 chained entries for evaluation/grant/attach events carry the class field | Fits; a field addition at already-audited events |

§4.4's evaluation-time rejection maps to container creation: a requested
`CAP-DEBUG-ATTACH` without `debug: true` is an **invalid manifest**
(rejected at creation), not a capability silently stripped later.

**Recorded implementation choice (open until Phase B-i lands):** where
the class-conditional guard lives — (a) centralized in
`CapabilityManager` (one testable enforcement point; the manager needs
the container's class passed in) or (b) at each container.py grant call
site (no manager API change; the check scatters). The review recommends
(a); the implementation picks one and says so.

**Resolution (2026-09-23, same day): the manifest-class plumbing is
LANDED, and the choice was (a)** — the guard is centralized in
`CapabilityManager` (`_CLASS_CONDITIONAL` map + a `container_classes`
registry the container manager feeds via `declare_container_class`).
As-built, pinned by `tests/test_debug_manifest_class.py` (16 tests):
`ContainerConfig.debug_class`; `create()` rejects `CAP-DEBUG-ATTACH`
requests without the class ("invalid manifest", per §4.4); the class
survives spawn (only `reset_container` clears it, with the grants);
`build_policy`/`build_allowlist_policy` gained keyword-only
`debug_class=False` (construction-time gate, both enforcement modes,
arch-safe via the deny/allow skip of arch-absent names); the class
rides the policy JSON so the in-container launcher rebuilds the SAME
policy (a test proves daemon-side and launcher-side policies agree,
ptrace-family included); the class is visible in the state dict, the
daemon-state manifest, the checkpoint round-trip, and the creation
event (`debug_class=true`); the IPC create handler passes the flag
through. Unknown capability names stay inert (unchanged behavior).
Still open: debug-image staging, the `containers debug` op family —
and the D7-required NPS-021 addendum requirements 1/5 (operator-only
ops posture, class-in-audit-chain field) bind those work items.

**Deliberately out of scope here, per D7's own fence:** launcher
manifest-class plumbing, debug-image staging, and the
`nyrqisctl containers debug` op family are separate work items — this
review decides *where* requirements bind, not *when* the channel ships.

### The original Phase B proposal (retained for the record)

An `attach` path — `nyrqisctl containers debug <id>` — that enters the
container's namespace with a debugger. This one **is** a new trust
surface and needed the Group:

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

The Group chose the in-container variant (D7) over this
launcher-mediated shape — the debuggee container is where the
debugger runs.

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
3. ~~The `build_payload` duplicate `"audit-summary"` mapping~~ —
   **repaired (v0.3.1, CR-0037)**: the chain summary has its own
   `audit-chain-summary` command; the stray mid-function raise that
   killed ~300 further commands is gone; the surface is pinned by a
   sweep test. The bundle still composes the ops' wire shapes
   directly, so it never depended on the fix either way.

## Revision History

| Version | Date       | Change       |
|---------|------------|---------------|
| 0.1.0   | 2026-09-23 | Initial draft — surface audit, three-phase split, the trust case for the attach channel |
| 0.2.0   | 2026-09-23 | Phase A + Phase C rider landed; §3/§5 rewritten as built with four recorded deviations; §6 updated — Phase B stays Group-gated, item stays open |
| 0.3.0   | 2026-09-23 | Deviations 2+3 resolved: redaction default-on (--redact/--no-redact); per-container audit trails (correcting a real wire-contract violation the scripted tests had masked); --chain-id summary+verification capture; the build_payload "audit-summary" shadow recorded for its owner |
| 0.3.1   | 2026-09-23 | The recorded shadow repaired (CR-0037): audit-chain-summary command registered properly, alert-summary un-hijacked, the stray mid-build_payload raise removed (~300 commands were unreachable since the CLI's first commit), parse→payload surface pinned by test_nyrqisctl_payload_surface.py |
| 0.4.0   | 2026-09-23 | **Phase B DECIDED (D7): developer-mode manifests (Option B)** — debug:true class + CAP-DEBUG-ATTACH, in-container debugpy/gdbserver, ptrace relaxation for the manifest class only, NPS-021 addendum before implementation; §4 rewritten as decided with the original proposal retained; implementation work items listed, none landed yet |
