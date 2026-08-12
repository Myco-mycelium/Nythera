---
title: Python and Rust as the Nyrqis Implementation Languages
document_id: ADR-0020
version: 1.0.0
status: Proposed
owners: [Nyrqis Architecture]
created: 2026-08-12
updated: 2026-08-12
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0006, ADR-0012, ADR-0016, ABI-001]
---

# ADR-0020 — Python and Rust as the Nyrqis Implementation Languages

## Context

Nyrqis spans components with very different requirements: a hybrid
microkernel (ADR-0006), kernel-abstraction backends (ADR-0012), a
user-space NyFS FUSE implementation (ADR-0016), a Windows/Android
compatibility story (ADR-0005, ADR-0008), an ABI contract (ABI-001),
and the tooling around all of it. No document currently records the
implementation-language strategy. The only written signals are
scattered: `docs/implementation_plan.md` notes the Linux-backend PoC is
Python and that production syscall/FUSE hot paths will need
"`ctypes` or a dedicated C/Rust component"; `sdk/README.md` anticipates
language bindings.

This gap has real costs. Without a recorded strategy, component teams
pick languages inconsistently, the FFI surface grows unplanned and
unversioned (fighting ABI-001), and new kernel-adjacent code may be
written in memory-unsafe languages that contradict the safety posture
of ADR-0006's hybrid-microkernel decision. The decision below is a
strategy, not a prohibition: it names two primary languages, assigns
each to a component class, and sets the rules for the boundary between
them.

## Decision (Proposed)

Nyrqis implements its components in **two primary languages**, chosen
by component class:

1. **Python** is the language for the **user-space service and
   rapid-iteration layer**: the NyHAL Linux-backend user-space stack
   (container primitives, capability enforcement, seccomp policy
   *compilation*, IPC orchestration, NyFS FUSE operations), build and
   test tooling, CI, and SDK bindings. This is the current de facto
   standard (the entire `source/nyhal-linux-backend/` tree is Python,
   103/103 tests green) and it is retained deliberately: fast
   iteration, a deep standard library, and a first-pass benchmarking
   workflow that has driven real decisions (BENCHMARK_RESULTS §1–§14).

2. **Rust** is the language for **kernel-adjacent, hot-path, and
   security-critical components**: the NyKernel itself (ADR-0006), the
   NyKernel backend of NyHAL (ADR-0012), direct syscall wrappers
   (`clone()`, `unshare()`, … where the plan's `ctypes` fallback is
   insufficient), the seccomp BPF *generation and installation* path,
   the NyFS block-store/checksum/compression hot path where measured
   performance requires it, and the bootloader/secure-boot chain
   (ADR-0014). Rationale: memory safety without a GC, deterministic
   performance, and alignment with the microkernel's security posture.
   Rust is chosen over the plan's generic "C/Rust" for new code; C
   **MAY** remain only at the FFI edge or where a component already
   exists, and only with explicit review.

Two rules govern the boundary (these are the normative part):

- **ABI rule**: the boundary between Python and Rust **MUST** be a
  versioned FFI/ABI surface (Python `ctypes`/`cffi` ↔ Rust `cdylib`
  entry points) governed by ABI-001. The Python layer **MUST NOT**
  reach into Rust internals, and **MUST NOT** share mutable state
  across the boundary — only data crossing stable, versioned entry
  points.
- **Migration rule**: an existing, working Python component
  **SHOULD NOT** be rewritten in Rust for style. A rewrite **MUST** be
  justified by measured performance data or a security finding
  (NPC-002 §5.2 — no fabricated numbers; a claim without a benchmark
  stays pending data). This keeps the transition evidence-driven and
  prevents churn of a tested, working backend.

## Alternatives Considered

- **All-Rust** — rejected as the immediate strategy: it would discard
  the working, tested Python backend and slow iteration while the
  product needs velocity; it remains available for greenfield
  components where the language class rules above already point to
  Rust anyway.
- **All-Python** — rejected: cannot satisfy the kernel, boot, and
  hot-path requirements, and would conflict with the Manifest's
  Performance principle (NTM-000 §4).
- **C/C++ as the primary systems language** — rejected for new
  kernel-adjacent code: manual memory management contradicts the
  safety posture ADR-0006 established for the microkernel, and the
  plan's own "C/Rust" framing is satisfied by Rust. C remains an
  allowed FFI-edge exception under the ABI rule.
- **Go** — rejected: GC pauses and runtime footprint are unsuitable
  for kernel-adjacent and boot code; it is not needed as a third
  language for tooling Python already covers.
- **Zig** — deferred: interesting for kernel work, but Rust already
  covers the memory-safe-systems niche with a far larger ecosystem,
  and adding a third systems language would split the small
  kernel-team effort.

## Consequences

Positive:
- One documented strategy ends ad-hoc language drift and gives
  reviewers a rule to enforce.
- The tested Python backend is preserved; Rust brings memory safety to
  the security-critical core consistent with ADR-0006.
- The versioned FFI boundary (ABI rule) gives an incremental,
  evidence-driven migration path from Python to Rust instead of a
  rewrite.

Negative:
- A two-language tax: two toolchains, FFI ceremony, and build-system
  integration. This is accepted as the cost of matching the language
  to the component class.
- The FFI boundary is a new trust surface that ABI-001 **MUST**
  cover (data layout, versioning, error marshalling).
- The migration rule bounds, but cannot eliminate, the risk of
  premature rewrites — review gates on evidence.

Affected owners **MUST** be tagged for review: kernel (NyKernel
backend), Linux backend, NyFS storage, and ABI ownership.
Architecture Group acceptance is required before this ADR is binding
(NPC-001 §6.4); until then the current de facto state (Python
everywhere) remains unchanged.

## Manifest Alignment

Advances NTM-000 §4 principles: **Performance** (Rust for hot paths),
**Security** (memory-safe systems code in the kernel/security core),
**Longevity** (a small, documented two-language strategy instead of
unplanned sprawl), **Simplicity** (two languages with a clear boundary
rule, not one language per team). Does not violate NTM-000 §5 ("What
Nyrqis Will Never Become") — nothing in that section concerns
implementation language.

## Transition Priorities (initial)

These are the first candidates for the Python → Rust migration, in
order, each gated on the Migration rule's evidence requirement:

1. Seccomp BPF generation and installation (security-critical; the
   policy compiler is already a well-bounded, pure function — the
   natural first Rust module).
2. Direct syscall wrappers (`clone`, `unshare`, namespace setup) per
   `docs/implementation_plan.md`.
3. NyFS checksum + compression hot path, only if the existing §4/§5
   benchmark data (small-op cost dominated by per-call block
   compression + per-read checksum verification) shows the Python path
   is a measured bottleneck.
4. NyKernel bootstrap and its NyHAL backend (with the NyKernel
   project itself).

## References

- `docs/implementation_plan.md` — existing C/Rust + Python signals.
- ABI-001 — the boundary contract the ABI rule extends.
- ADR-0006 (hybrid microkernel), ADR-0012 (NyHAL), ADR-0016 (NyFS
  Linux backend FUSE) — the decisions whose components this strategy
  assigns languages to.
- `source/nyhal-linux-backend/` — the current Python implementation
  this strategy preserves.
