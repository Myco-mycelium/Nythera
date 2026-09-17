---
title: Wire-Verified Protocol Constants and Fail-Closed Acceptance Gates
document_id: ADR-0027
version: 1.0.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-09-16
updated: 2026-09-16
ai_assisted: true
depends_on: [ADR-0020, ADR-0026, NPS-017]
---

# ADR-0027 — Wire-Verified Protocol Constants and Fail-Closed Acceptance Gates

## Context

Two defect classes found and fixed during the live-ISO bring-up
(2026-09-14 → 16) generalize beyond their incidents. Both shipped
green through unit-test suites and would have reached users.

**1. Memory-recalled protocol constants.** The Wayland wire event loop
(ADR-0026) was written against opcode tables recalled from memory. A
real client run (upstream `weston-simple-shm`, then `weston-terminal`)
exposed **five wrong tables** that hand-built fixtures never hit —
because the fixtures encoded the same wrong constants:

- `xdg_wm_base.get_xdg_surface` is **2** (we had 1; pong is 3, not 2 —
  an earlier "fix" moved the constants onto the wrong rung *twice*).
- `wl_shm_pool` is **create_buffer=0, destroy=1, resize=2** (we had
  destroy=2, resize=3 — and then inverted it *again* from memory
  before the canonical header settled it).
- `wl_surface` is **set_opaque_region=4, set_input_region=5** (we had
  5/4), **set_buffer_transform=7, set_buffer_scale=8**.
- `wl_output` events are **done=2, scale=3** (scale precedes done).
- `wl_display.error` carries the **`ous`** signature (object, code,
  message) — omitting the code shifts libwayland's strict parse.

The same real-client run caught a silent kernel-adjacent bug no
protocol audit could see: Python's `recvmsg(bufsize)` defaults
`ancbufsize` to **0**, silently discarding every SCM_RIGHTS fd — a
client's SHM pixel memory vanished in transit. And `wl_buffer.release`
was destroying buffers (release ≠ destroy), killing double-buffered
clients on their third frame.

**2. Components missing at boot.** A user's real boot printed
`✗ MISSING: python3 (backend cannot run)`. Root causes stacked: the
builder's debootstrap include list never carried `python3` (only CI's
separate chroot step papered over it, so any other rootfs path shipped
dead); debootstrap's resolver cannot map **virtual** dependencies
(`python3-zstandard` needs the real `python3-cffi` named;
`python3-pycparser` needs `python3-ply`); and the reused-rootfs path
was non-idempotent (`useradd` aborted on an existing user; `cp -a src
dst` nested a duplicate `/opt` tree — 354 MB → 815 MB in one rebuild).

The common thread: **every failure was preventable by evidence the
machine could have gathered itself, at a checkpoint before shipping.**

## Decision

Two binding engineering rules for Nyrqis protocol and packaging work:

### Rule 1 — Protocol constants are wire-verified or header-verified, never memory-verified

Any constant that crosses a wire boundary (Wayland opcode, event
signature, struct layout, ioctl number, FFI ABI value) must be derived
from one of:

1. **The canonical machine-readable source** — the protocol XML or the
   generated header (`wayland-client-protocol.h` is installed with
   libwayland and carries every opcode enum). This is the primary
   source and settles disputes.
2. **A trace of a real client** — `WAYLAND_DEBUG=1` on an upstream
   binary is ground truth for what a peer actually sends.

Hand-built fixtures that encode the same constants as the
implementation prove nothing about the protocol. The client-compat
suite therefore runs **real upstream clients** (`weston-simple-shm`,
`weston-terminal`) against the compositor in CI (`wayland-real-clients`
job), and the rule is recorded in the compositor's test comments.

Conversely: when a trace contradicts a constant written from memory,
**the trace wins** even if the constant "looks right" — the
`get_xdg_surface` fix was reverted to a wrong value once because
memory outranked evidence.

### Rule 2 — Acceptance is enforced by fail-closed gates at the last checkpoint where evidence exists

An image (or artifact, or release) must be refused by the builder —
not reviewed, not documented, *refused* — when a machine-checkable
acceptance condition fails. Each gate runs at the last checkpoint
where its evidence is still cheap:

| Gate | Checkpoint | Catches |
|------|-----------|---------|
| `dpkg --audit` empty | after rootfs assembly, every acquisition path | unconfigured packages (the virtual-dep failure class) |
| Probe parity | pre-squash | an image whose own boot probe would print MISSING |
| Byte-compile (image's interpreter) | pre-squash | Python-version syntax drift |
| ISO size ≤ 500 MB | post-assemble | silent tree duplication, stray artifacts |
| Boot smoke markers (`READY`/`PONG`/`PKGS=ok`) | boot, in CI and locally | anything that makes the boot incomplete |

Supporting rules, learned the same way:

- **Builder idempotency:** every rootfs-mutating step must tolerate a
  reused rootfs (the CI cache path). Fresh-build-only correctness is
  not correctness.
- **Both smoke paths, both architectures:** the direct-kernel smoke
  isolates live-boot/daemon health; the menu-path smoke proves the
  human path. They are separate CI jobs — a failure of one is
  diagnosable from the job list and cannot mask the other. The serial
  console is arch-specific (`ttyS0`/`ttyAMA0`); every layer that
  dispatches on it must handle both (the arm64 smoke once hung forever
  on a `ttyS0`-only match).
- **Boot-time parity evidence:** the image reports its own package
  parity on the serial log (`NYRQIS_BOOT_SMOKE_PKGS=ok|missing:…`) and
  the smoke drivers gate on it — the user's original failure now fails
  CI at smoke time.
- **Explicit job budgets:** every CI job carries `timeout-minutes`; a
  hang fails its job instead of burning the runner default.

## Alternatives Considered

- **Generate opcodes from the XML at build time** (wayland-scanner
  style). Strongest long-term option, but a larger change to the Rust
  wire loop; deferred. Until then, Rule 1's header-verification with a
  pinned contract test (`test_wayland_client_compat.py` asserts exact
  event bytes) gives the same guarantee for the implemented surface.
- **Trust unit fixtures with hand-encoded constants** — the status quo
  that shipped both defect classes; rejected by the incident record.
- **Warn-only gates** (log a warning, continue). Rejected: a warning
  nobody reads is how `✗ MISSING: python3` reached a user's screen.
  Gates must refuse.
- **Post-ship verification only** (probe on the user's machine, honest
  MISSING output). The probe stays — it is the honest runtime surface
  — but it is the *diagnosis* layer, not the *prevention* layer.
  Prevention belongs upstream of the user.

## Consequences

### Positive

- The five opcode tables, the `recvmsg` fd bug, and the release≠destroy
  bug are all pinned by byte-exact contract tests plus real-client CI;
  a regression fails in minutes.
- An incomplete image cannot ship from any rootfs path: the gate chain
  has refused builds (verified during development, e.g. the
  virtual-dep failure and the size-gate envelope tests).
- The 815 MB nested-tree rebuild and the arm64 `ttyS0`-only handshake
  hang are pinned by contract tests as invariants (idempotency,
  dual-serial match, size envelope).
- CI verdicts are job-legible: one job per arch per boot path, all
  budgeted, serial logs uploaded on failure.

### Negative

- Real-client CI needs weston installed on runners (a small, stable
  apt dependency) and TCG-slowed boot budgets (15–30 min per smoke).
- The gate chain makes deliberate experiments harder: shipping a
  knowingly-incomplete image for a demo requires bypassing gates
  explicitly — which is the point, but costs a flag and an argument
  each time it is legitimately needed.
- Opcode generation from XML remains undone; until then the contract
  tests must be updated when the protocol surface grows.

## References

- Incident record and fix history: `source/nyhal-linux-backend/CHANGELOG.md`
  (0.29.13–0.29.19), `docs/00-platform/NEXT_SESSION_PLAN.md` (Sessions 11–11g).
- Canonical constant source: `wayland-client-protocol.h` (installed
  with libwayland-dev).
- Gate implementations: `packaging/live/build-live-iso.sh`;
  smoke drivers: `tests/boot_smoke.py`, `tests/boot_smoke_menu.py`;
  contract pins: `source/nyhal-linux-backend/tests/test_live_boot_contract.py`,
  `source/nyhal-linux-backend/tests/test_wayland_client_compat.py`.
