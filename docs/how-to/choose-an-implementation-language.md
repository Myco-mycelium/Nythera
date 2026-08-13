# How to Choose an Implementation Language

*Applies to: anyone writing or proposing new Nyrqis components.*

Nyrqis's languages are decided by the **canonical language matrix**
(ADR-0020) and one governing principle: **platform-critical execution
paths must not depend on the Python interpreter.** The full rationale,
alternatives, and the three normative rules (Platform-boundary rule,
ABI rule, Migration rule) live in
[ADR-0020](../reference/adr/ADR-0020-implementation-languages.md) —
read it before you start; this page is the quick map.

## The platform boundary

Draw a line between the **shipped platform** and everything that
*builds, tests, automates, administers, or researches* it:

- **Below the boundary (the platform):** kernel, bootloader, HAL, core,
  runtime, UI, shell, game, AI, package management, storage, networking,
  security services. These are implemented in **Rust, C++, and C** per
  the matrix. Python is not an execution language here.
- **Above the boundary (tooling):** build tools, test harnesses, CI/CD,
  documentation pipelines, SDK bindings, diagnostics, administration,
  research and prototyping. **Python is the default here**, and it is
  unrestricted.

## The canonical matrix (ADR-0020)

| Layer | Primary | Secondary / supporting |
|-------|---------|------------------------|
| NyHAL | **Rust** | C/C++ where hardware integration requires it |
| NyCore | Rust | C++ |
| NyRuntime | Rust | C++ |
| NySDK | Rust + C++ | C# bindings |
| NyUI | C++ + declarative UI | Rust |
| NyShell | C++ | Rust |
| NyGame | C++ + Rust | C |
| NyAI | Rust | Python for tooling/research |
| NyPackage | Rust | — |
| NyVault / storage | Rust | C/C++ where hardware integration requires it |
| Networking | Rust | C |
| Security services | Rust | C |
| Build tools | Rust | Python |
| Testing | Rust | Python |
| Developer tools / automation | Rust | Python |
| Bootloader / lowest-level | Rust / C | Assembly where absolutely necessary |
| Linux kernel | C | — |

Python's role per component (tooling, automation, SDK bindings,
administration, research) is recorded in the ADR's component view — it
is always **above the boundary**, never an execution language for a
platform layer.

## How to decide for a NEW component

1. **Which side of the platform boundary does it run on?** If it ships
   as part of the platform (or runs as part of a platform-critical
   execution path — syscall handling, enforcement, FUSE operations, IPC
   transport, boot), it is implemented in the matrix language for its
   layer. If it builds/tests/administers/researches the platform, Python
   is the default — no further justification needed.
2. **Is it a hot path?** "Hot path" is a *measured* claim, not a guess
   (NPC-002 §5.2 — no fabricated numbers). If you believe the Python
   tooling layer is too slow, benchmark it first and record the numbers
   before moving it down.
3. **Is it security-critical?** Policy compilers, parsers of untrusted
   input, kernel-adjacent code, and the boot chain are below the
   boundary by definition — they get Rust for memory safety. This is the
   security posture, not a performance claim.

## The three rules that always apply

- **Platform-boundary rule:** platform-critical execution paths
  **MUST NOT** depend on the Python interpreter. Python is welcome
  above the boundary; the shipped platform is Rust/C++/C.
- **ABI rule:** boundaries between language runtimes **MUST** be a
  versioned FFI surface governed by ABI-001 (Python `ctypes`/`cffi` ↔
  Rust `cdylib`, C++ ↔ Rust `extern "C"`). No shared mutable state
  across the boundary; only data crossing stable, versioned entry
  points.
- **Migration rule:** do not rewrite a working Python component "for
  style." A rewrite **MUST** be justified by measured performance data,
  a security finding, **or the platform-boundary rule itself** (the
  component is a platform-critical execution path), and **MUST** go
  through the same conformance bar as the component it replaces (the
  existing test suite, forced through the FFI).

## First migration (in progress)

The seccomp policy compiler is the first Python → Rust migration
(ADR-0020 priority #1; scaffold, wire format, FFI loader, and CI
conformance gate in `source/nyhal-linux-backend/rust/seccomp/`). Until a
Rust toolchain exists on the dev host and the conformance plan there
passes, the pure-Python implementation remains the only shipped one.

## Got a component that doesn't fit?

If a component genuinely straddles the boundary, keep the tooling in
Python and extract the bounded platform-critical piece into a Rust/C++
module behind the FFI (the seccomp pattern), rather than splitting the
whole component across languages. Propose anything that challenges the
matrix as a change to ADR-0020, not a local exception.
