# How to Choose an Implementation Language

*Applies to: anyone writing or proposing new Nyrqis components.*

Nyrqis has two primary implementation languages, chosen by component
class. The full rationale, alternatives, and the two normative rules
(ABI rule, Migration rule) live in
[ADR-0020](../reference/adr/ADR-0020-implementation-languages.md) —
read it before you start; this page is the quick map.

## The two-language rule (ADR-0020)

| Component class | Language | Examples |
|-----------------|----------|----------|
| User-space service / rapid-iteration layer | **Python** | Linux-backend stack (containers, capability enforcement, seccomp policy compilation, IPC orchestration, NyFS FUSE operations), build/test tooling, CI, SDK bindings |
| Kernel-adjacent, hot-path, security-critical | **Rust** | NyKernel, NyHAL kernel backend, direct syscall wrappers, seccomp BPF generation/install, NyFS block-store hot path (if measured), boot/secure-boot chain |

## How to decide for a NEW component

1. **Where does it run?** In user space alongside existing backend
   services → Python, unless it is security-critical or hot-path.
   Inside or next to the kernel, in the boot chain, or in a measured
   hot path → Rust.
2. **Is it a hot path?** "Hot path" is a *measured* claim, not a guess
   (NPC-002 §5.2 — no fabricated numbers). If you believe Python is too
   slow, benchmark it first and record the numbers before proposing
   Rust.
3. **Is it security-critical?** Policy compilers, parsers of untrusted
   input, kernel-adjacent code, and the boot chain get Rust for memory
   safety — this is the security posture, not a performance claim.

## The two rules that always apply

- **ABI rule:** Python ↔ Rust boundaries **MUST** be a versioned FFI
  surface governed by ABI-001 (Python `ctypes`/`cffi` ↔ Rust
  `cdylib`). No shared mutable state across the boundary; only data
  crossing stable, versioned entry points.
- **Migration rule:** do not rewrite a working Python component in Rust
  "for style." A rewrite **MUST** be justified by measured performance
  data or a security finding, and **MUST** go through the same
  conformance bar as the component it replaces (the existing test
  suite, forced through the FFI).

## First migration (in progress)

The seccomp policy compiler is the first Python → Rust migration
(ADR-0020 priority #1; scaffold and FFI contract in
`source/nyhal-linux-backend/rust/seccomp/`). Until a Rust toolchain
exists on the dev host and the conformance plan there passes, the
pure-Python implementation remains the only shipped one.

## Got a component that doesn't fit?

If a component genuinely straddles both classes, keep it Python and
extract the bounded hot/security-critical piece into a Rust module
behind the FFI (the seccomp pattern), rather than splitting the whole
component across languages. Propose anything that challenges this map
as a change to ADR-0020, not a local exception.
