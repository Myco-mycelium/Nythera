---
title: NUI (.nstudio) Runtime Consumption — the UI Import Gate
document_id: ADR-0025
version: 0.1.0
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-08-16
updated: 2026-08-16
ai_assisted: true
depends_on: [NFS-001, NPS-009, ADR-0020]
---

# ADR-0025 — NUI (.nstudio) Runtime Consumption

## Status

**Proposed** — drafted for Architecture Group review. The first increment
is **implemented and gated the same day (2026-08-16)** as the reference
floor + Rust crate + conformance gate, mirroring the ADR-0020 migration
pattern. Follow-on increments landed the same day: the import gate is
exposed to operators over the IPC control plane (`nui_validate` /
`nui_load` + `nyrqisctl nui`), a second NyForge-authored screen (the
Security Center) joins the fixtures, and §30 of the benchmark suite
measures the floor-vs-crate A/B.

## Context

NyForge (the visual designer, its own repo `Myco-mycelium/Nyforge`) edits
`.nstudio` documents — the NUI intermediate representation defined by
NFS-001 — and the Nyrqis UI Runtime is the eventual consumer: the shell
UI NyForge produces must actually run on Nyrqis. NFS-001 §1 states the
intent ("eventually, the real Nyrqis UI Runtime") and NPS-009 defines the
adaptive shell's *modes* but not how the runtime *imports* a design.

The runtime needs an **import gate**: before the shell trusts a `.nstudio`
file (authored by a designer, exchanged across machines), the document
must be validated against the NUI contract tables — component vocabulary
(NFS-001 §4), per-type property/event contracts and system actions (§5),
behavior/binding references (§7–§8), and the schema-version gate (§9) —
with failures raised loudly instead of silently misinterpreting the file.

Language posture is fixed by ADR-0020: the UI/Shell layers are platform
layers; platform-critical execution paths must not depend on the Python
interpreter. Python's role for NyUI is "High — UI tooling" (above the
boundary).

## Decision

1. **The `.nstudio` import gate is a parse/validate hot path** — a
   platform-critical execution path per ADR-0020 — and is therefore
   implemented in Rust (`source/nyhal-linux-backend/rust/nyui/`) behind a
   versioned FFI (ABI 1.0.0), following the migration pattern established
   by the seccomp/transport/ipcd crates: caller-supplied input, no
   allocation on the Rust side, status-code returns.

2. **The pure-Python module `ui/nstudio.py` is the reference floor** —
   full parse, version gate, contract validation, `$state:` resolution,
   layout render, and text preview. It remains the reference behavior and
   the basis of the test suite; its suite is forced through the FFI by the
   conformance gate (`TestNstudioCodecConformance`), which is the crate's
   definition of shipped (the ADR-0020 migration contract: the migrated
   component's existing test suite must pass through the FFI).

3. **The contract tables are mirrored from NyForge's single source of
   truth** (`ComponentContracts.cs`, `NuiSystemActions.cs`) per NFS-001 §5
   and NFC-001 §4.3's anti-drift rule. NyForge is the authoritative
   authoring side; the runtime validates against the same vocabulary the
   palette offers.

4. **The schema-version gate is strict** (NFS-001 §9): a document whose
   `version` is not supported raises `NstudioVersionError`; the runtime
   never guesses.

5. **The NyForge example designs are test fixtures** in this repo
   (`source/nyhal-linux-backend/tests/fixtures/nstudio/` — forge-home,
   settings-app, vault-dashboard, nyrqis-shell, security-center), so the
   runtime is self-contained and CI-verifiable without depending on the
   NyForge checkout.

## Consequences

- The Nyrqis side of the NyForge↔runtime pipeline now exists and is
  compiler-verified in CI (`rust-nyui` builds/tests the crate;
  `rust-nyui-conformance` forces the floor's suite through the FFI).
- The crate is the first NyRuntime-shaped artifact for the UI layer (the
  serving loop `rust/ipcd` is the first NyRuntime-shaped artifact
  overall).
- The import gate is operator-drivable end to end: `NuiService`
  (`ui/service.py`) exposes `nui_validate` (gate only) and `nui_load`
  (gate + persist as the daemon's shell UI) over the datagram control
  plane, operator-only (a registered container is refused), with a
  per-call document budget; `nyrqisctl nui validate|load` wraps it. The
  CLI e2e drives the real daemon with the Rust crate as the engine.
- Rendering is deliberately bounded: the floor renders absolute layout
  entries and a text preview. A graphical shell renderer is a separate
  follow-on (C++/declarative UI per the matrix) — this ADR covers the
  import gate, not the compositor.
- Drift risk is acknowledged and bounded: table changes in NyForge must be
  reflected here; the differential test (`test_error_messages_match_floor`)
  pins the two sides to identical validation semantics.
- Benchmark evidence (§30) shows the crate's gate is ~2.1× faster than
  the floor at the median (242 µs vs 502 µs p50 on the security-center
  fixture) — the ADR-0020 migration's performance claim, measured.

## Alternatives Considered

- **Python-only import gate** — rejected: the gate is a platform-critical
  execution path (every shell startup runs it); per ADR-0020 it must not
  depend on the interpreter. The Python module remains as the reference
  floor and tooling.
- **C++ first** — deferred: the matrix lists C++ as NyUI primary, but the
  backend's established, gated migration path is Python-floor → Rust-FFI,
  and the crate must interoperate with the existing Python test harness
  via ctypes. Rust gives the same memory-safety posture with the
  established tooling.
- **Validating only in NyForge, trusting the file** — rejected: the
  runtime must not trust authoring-side validation alone (files are
  exchanged and hand-edited); NFS-001's versioning exists precisely so the
  consumer fails loudly.

---

**End of Document**
