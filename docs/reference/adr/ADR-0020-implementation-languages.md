---
title: Implementation Languages and the Platform Boundary
document_id: ADR-0020
version: 2.0.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-08-12
updated: 2026-08-13
ai_assisted: true
depends_on: [NTM-000, NPC-001, ADR-0006, ADR-0012, ADR-0016, ABI-001]
---

# ADR-0020 — Implementation Languages and the Platform Boundary

## Status
Accepted — 2026-08-13, following Architecture Group review (issue #2,
per NPC-001 §6.4). The three open questions were resolved: the
platform-boundary rule is normative (not a guideline); the Python Linux
backend's platform-critical modules carry the migration obligation
(evidence-gated via the Migration rule); Python remains the reference
implementation language during development. The canonical matrix and
the platform-boundary principle are binding.

## Context

Nyrqis spans components with very different requirements: a hybrid
microkernel (ADR-0006), kernel-abstraction backends (ADR-0012), a
user-space NyFS FUSE implementation (ADR-0016), a Windows/Android
compatibility story (ADR-0005, ADR-0008), an ABI contract (ABI-001),
and the tooling around all of it.

Version 1.0.0 of this ADR (2026-08-12) recorded a two-primary-language
strategy: **Python** for the user-space service and rapid-iteration
layer, **Rust** for kernel-adjacent, hot-path, and security-critical
components, with C permitted only at the FFI edge. That decision is
now superseded by the canonical language list (2026-08-13): Nyrqis
speaks **Rust, C++, and C** as its platform languages — C++ is a
first-class primary for NyHAL-adjacent layers (UI, Shell, Game), notan FFI-edge exception — and **Python's role is bounded by the platform
boundary**, not by component class. This revision records the canonical
matrix — with the one reconciliation noted below — and elevates the
boundary rule to a normative engineering principle.

## Decision (Proposed)

Nyrqis implements its components per the canonical language matrix
below. Two views of the same decision are recorded, because the
canonical list itself provides both: the **layer map** (which layer
owns which language) and the **component map** (which additionally
states Python's role per component). Where the two views disagree, the
component map is canonical — specifically **NyHAL is Rust-first**
(resolved 2026-08-13; the layer map's "C++ / C" row for NyHAL is
superseded). The same rule resolves the bootloader's row ordering (the
layer map lists "C / Rust"; the component map, canonical here, lists
"Rust/C" — both are the same language set, ordered by the component
map).

### Canonical language matrix — layer view

| Nyrqis layer                 | Primary language     | Secondary / supporting                       |
| ---------------------------- | -------------------- | -------------------------------------------- |
| **NyHAL**                    | **Rust**             | C/C++ where hardware integration requires it |
| **NyCore**                   | Rust                 | C++                                          |
| **NyRuntime**                | Rust                 | C++                                          |
| **NySDK**                    | Rust + C++           | C# bindings                                  |
| **NyUI**                     | C++ + declarative UI | Rust                                         |
| **NyShell**                  | C++                  | Rust                                         |
| **NyGame**                   | C++ + Rust           | C                                            |
| **NyAI**                     | Rust                 | Python for tooling/research                  |
| **NyPackage**                | Rust                 | —                                            |
| **NyVault / storage**        | Rust                 | C/C++ where hardware integration requires it |
| **Networking**               | Rust                 | C                                            |
| **Security services**        | Rust                 | C                                            |
| **Build tools**              | Rust                 | Python                                       |
| **Testing**                  | Rust                 | Python                                       |
| **Developer tools / automation** | Rust             | Python                                       |
| **Bootloader / lowest-level**| Rust / C             | Assembly where absolutely necessary          |
| **Linux kernel**             | C                    | —                                            |

### Canonical language matrix — component view (Python roles)

| Component            | Primary    | Python role                          |
| -------------------- | ---------- | ------------------------------------ |
| Bootloader           | Rust/C     | None                                 |
| Linux kernel         | C          | None                                 |
| **NyHAL**            | Rust/C/C++ | None                                 |
| **NyCore**           | Rust       | Limited tooling                      |
| **NyRuntime**        | Rust       | Limited tooling                      |
| **NyUI**             | C++/Rust   | **High** — UI tooling                |
| **NyShell**          | C++/Rust   | Medium — automation/extensions       |
| **NyForge**          | Rust/C++   | **Very High**                        |
| **NySDK**            | Rust/C++   | **High** — Python SDK                |
| **NyGame**           | C++/Rust   | **High** — asset/build/modding tools |
| **NyAI**             | Rust       | **Very High**                        |
| **NyPackage**        | Rust       | High — package tooling               |
| **NyVault**          | Rust       | Medium — administration/tools        |
| **Networking**       | Rust/C     | Medium — diagnostics/tools           |
| **Security tooling** | Rust       | **High** — auditing/analysis         |
| **Build system**     | Rust       | **Very High**                        |
| **Testing**          | Rust       | **Very High**                        |
| **Developer tools**  | Rust       | **Very High**                        |
| Documentation        | —          | **Very High**                        |
| CI/CD automation     | —          | **Very High**                        |
| Research/prototyping | —          | **Very High**                        |

### The platform-boundary principle (normative)

The engineering principle this ADR establishes:

> Python may be used extensively **above the platform boundary**, but
> **platform-critical execution paths must not depend on the Python
> interpreter**.

For the purposes of this ADR:

- The **platform boundary** separates the *shipped platform* (kernel,
  bootloader, HAL, core, runtime, UI, shell, game, AI, package
  management, storage, networking, security) from everything that
  *builds, tests, automates, administers, or researches* it. Tools,
  build systems, test harnesses, CI/CD, documentation pipelines,
  SDK bindings, diagnostics, and research/prototyping sit **above** the
  boundary.
- A **platform-critical execution path** is any code the shipped
  platform runs as part of a user-visible or security-relevant
  operation: syscall handling, seccomp enforcement, FUSE operations,
  IPC transport, container launch, boot sequencing — anything an
  application or the security model depends on.
- Python's component-map roles above — tooling, automation, research,
  SDK bindings, administration — are all **above the boundary**.
  **None** of the platform layers (below the boundary) lists Python as
  a primary or secondary execution language.

### Boundary rules (normative)

1. **Platform-boundary rule (new):** platform-critical execution paths
   **MUST NOT** depend on the Python interpreter. The shipped platform
   is implemented in Rust, C++, and C per the matrix. Python **MAY**
   be used freely above the boundary, including for tools that *build,
   test, package, or administer* the platform.
2. **ABI rule (unchanged):** the boundary between language runtimes
   **MUST** be a versioned FFI/ABI surface (Python `ctypes`/`cffi` ↔
   Rust `cdylib`, C++ ↔ Rust `extern "C"`, …) governed by ABI-001. No
   shared mutable state across the boundary — only data crossing
   stable, versioned entry points.
3. **Migration rule (updated):** an existing, working Python component
   **SHOULD NOT** be rewritten for style alone; a rewrite **MUST** be
   justified by measured performance data, a security finding
   (NPC-002 §5.2 — no fabricated numbers), **or by this ADR's
   platform-boundary rule** (the component is a platform-critical
   execution path that must not depend on the interpreter). The
   conformance bar is unchanged: the migrated component's existing test
   suite must pass through the FFI.

## What this means for the current Linux backend

`source/nyhal-linux-backend/` is today an **all-Python** implementation
of NyHAL's Linux contract — and its core components are platform-critical
execution paths: seccomp-BPF enforcement (`backend/seccomp.py`), FUSE
operations (`fuse/nyfs.py`), container launch and namespacing
(`backend/container.py`, `backend/launcher.py`), and the IPC core
(`ipc/core.py`). Under the platform-boundary rule, the **shipped** form
of those paths must not depend on the Python interpreter.

The current implementation is a research and prototype implementation —
legitimate under the matrix (research/prototyping is a very-high Python
role) — but it is **not** the shipped form of the platform. The path to
conformance is the ADR-0020 migration queue below, not a rewrite of
everything at once: the Python implementation remains the reference
behavior, the test suite and benchmarks stay Python (above the
boundary), and each platform-critical module migrates to Rust (or
C/C++) behind ABI-001 with its existing suite forced through the FFI.
The seccomp policy compiler — the first migration — already has its
scaffold, wire format, FFI loader, and conformance gate in place
(`source/nyhal-linux-backend/rust/seccomp/`).

## Alternatives Considered

- **All-Rust** — rejected as the immediate strategy: it would discard
  the working, tested Python backend and slow iteration while the
  product needs velocity; it remains available for greenfield
  components where the matrix already points to Rust anyway.
- **All-Python** — rejected: cannot satisfy the kernel, boot, and
  hot-path requirements, would conflict with the Manifest's Performance
  principle (NTM-000 §4), and would violate the platform-boundary rule
  this ADR establishes.
- **Python as the user-space service language (v1.0.0 of this ADR)** —
  rejected in this revision: the user-space *platform services* (UI,
  Shell, Game, storage, networking, security) are as platform-critical
  as kernel-adjacent code, so they follow the same rule — compiled
  implementation, Python allowed only for their tooling.
- **C++ as the single systems language** — rejected for the
  security-critical core: manual memory management contradicts the
  safety posture ADR-0006 established for the microkernel. C++ is a
  primary *where the matrix says so* (UI, Shell, Game, SDK), not a
  default.
- **Go** — rejected: GC pauses and runtime footprint are unsuitable for
  kernel-adjacent and boot code; it is not needed as a third language
  for tooling Python already covers above the boundary.
- **Zig** — deferred: interesting for kernel work, but Rust already
  covers the memory-safe-systems niche with a far larger ecosystem.

## Consequences

Positive:
- The canonical matrix ends ad-hoc language drift and gives reviewers a
  single table to enforce against.
- The platform-boundary principle makes Python's role explicit and
  stable: above the boundary it is unrestricted and welcome; below it,
  execution paths are compiled. No more per-team interpretation.
- The tested Python backend is preserved as the reference
  implementation; Rust/C++ bring memory safety and performance to the
  security-critical core consistent with ADR-0006.
- The versioned FFI boundary (ABI rule) gives an incremental,
  evidence-driven migration path instead of a rewrite.

Negative:
- A multi-language tax: three platform languages plus Python, FFI
  ceremony, and build-system integration. Accepted as the cost of
  matching the language to the layer.
- The FFI boundary is a new trust surface that ABI-001 **MUST** cover
  (data layout, versioning, error marshalling).
- The current Python backend's platform-critical modules carry a
  **migration obligation** (the boundary rule), which is a real,
  scheduled engineering cost — even where performance data alone would
  not have justified a rewrite.

Affected owners **MUST** be tagged for review: kernel (NyKernel
backend), Linux backend, NyFS storage, and ABI ownership. **Accepted
2026-08-13** (Architecture Group, issue #2) — the matrix and the
platform-boundary rule are binding; the migration queue below is the
agreed plan.

## Manifest Alignment

Advances NTM-000 §4 principles: **Performance** (Rust/C++ for hot
paths), **Security** (memory-safe systems code in the kernel/security
core), **Longevity** (a small, documented matrix instead of unplanned
sprawl), **Simplicity** (a boundary rule that removes interpretation,
not one language per team). Does not violate NTM-000 §5 ("What Nyrqis
Will Never Become") — nothing in that section concerns implementation
language.

## Transition Priorities (initial)

Each item is gated on the Migration rule's evidence requirement and the
platform-boundary rule's obligation. In order:

1. **Seccomp BPF generation and installation** (security services =
   Rust; security-critical; the policy compiler is already a
   well-bounded, pure function — the natural first Rust module;
   scaffold, wire format, FFI loader, and CI conformance gate in
   place).
2. **Direct syscall wrappers** (`clone`, `unshare`, `sethostname`,
   `prctl`, namespace setup) per `docs/implementation_plan.md`
   (NyHAL = Rust-first; scaffold at
   `source/nyhal-linux-backend/rust/syscalls/`, built by CI).
3. **NyFS checksum + compression + FUSE hot paths** (storage = Rust),
   using the existing §4/§5/§6 benchmark data to set the extraction
   boundary. **IMPLEMENTED 2026-08-13** (the block codec — SHA-256 +
   Zstandard — shipped in `rust/nyfs/`; the FUSE operation handlers
   themselves remain Python in the reference backend, wired to the
   codec through `fuse/nyfs_codec.py`).
4. **IPC core transport** (networking = Rust), after the Python IPC
   semantics are stable and benchmarked. **IMPLEMENTED 2026-08-13**
   (the message wire codec — binary framing — shipped in
   `rust/ipc/`, wired as `IPCMessage.to_wire()`/`from_wire()`; the
   in-process `IPCManager` transport semantics are unchanged).
5. **Container primitives and launcher** (NyCore/NyHAL = Rust),
   incrementally, keeping the Python reference implementation green
   throughout.
6. **NyKernel bootstrap and its NyHAL backend** (with the NyKernel
   project itself).

## References

- The canonical language list (layer map + component map), recorded
  2026-08-13 — the authoritative source this revision implements
  verbatim.
- `docs/implementation_plan.md` — existing C/Rust + Python signals.
- ABI-001 — the boundary contract the ABI rule extends.
- ADR-0006 (hybrid microkernel), ADR-0012 (NyHAL), ADR-0016 (NyFS
  Linux backend FUSE) — the decisions whose components this strategy
  assigns languages to.
- `source/nyhal-linux-backend/` — the current Python reference
  implementation.
- `source/nyhal-linux-backend/rust/seccomp/` — migration #1:
  **IMPLEMENTED 2026-08-13** (compiler + golden tests + forced-mode
  conformance gate green and required).
- `source/nyhal-linux-backend/rust/syscalls/` — migration #2:
  **IMPLEMENTED 2026-08-13** (sethostname/prctl/unshare/mount/mount_proc,
  ABI 1.1.0; the direct-syscall launcher in `backend/container.py`;
  conformance gate green and required).
- `source/nyhal-linux-backend/rust/nyfs/` — migration #3:
  **IMPLEMENTED 2026-08-13** (SHA-256 checksum + Zstandard block codec,
  ABI 1.0.0; loader `fuse/nyfs_codec.py`; conformance gate green and
  required).
- `source/nyhal-linux-backend/rust/ipc/` — migration #4:
  **IMPLEMENTED 2026-08-13** (IPC message wire codec, ABI 1.0.0,
  `libc` the only dependency; loader `ipc/ipc_codec.py`; conformance gate green
  and required). The dev host still has no Rust toolchain — CI builds
  and tests every crate on each push.
