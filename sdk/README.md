# sdk

Scaffolded per `docs/00-platform/003-ENGINEERING_HANDBOOK.md` (NPC-003 §5).
Intended to hold developer SDKs, language bindings, project templates, and
tooling for third-party developers once the platform has a public API to
bind against.

Currently empty. Expected to be populated in step with the
[Public API specification](../docs/reference/api/) (Milestone 11 gap
category) and the corresponding implementation work — see
[`docs/00-platform/007-PROJECT_ROADMAP.md`](../docs/00-platform/007-PROJECT_ROADMAP.md).

## Language strategy

Nyrqis's implementation languages are decided by the canonical
language matrix and the platform-boundary principle, not by team
preference — see
[ADR-0020](../docs/reference/adr/ADR-0020-implementation-languages.md)
and the
[language guide](../docs/how-to/choose-an-implementation-language.md).
The matrix gives **NySDK a Rust + C++ primary** with **C# bindings** as
secondary, and a **High Python role** — the Python SDK is the
above-the-boundary developer surface. For this directory, the plan
follows from that decision:

- **The SDK core is Rust + C++** — the platform's services (storage,
  networking, security, …) are compiled per the matrix, so the SDK's
  primary implementation is Rust + C++, exposed behind the versioned
  FFI boundary (ABI-001).
- **The Python SDK is the first-class above-the-boundary binding** —
  Python is unrestricted for tooling and bindings, so the primary
  *developer* SDK language is Python (ctypes/cffi over the Rust/C++
  core), with **C# bindings** following per the matrix.
- **Rust bindings arrive with the first shipped Rust module** (the
  seccomp policy compiler, `source/nyhal-linux-backend/rust/seccomp/`,
  is the first migration) and expose the same functionality the
  Python SDK does.
- No third language is planned; a component that appears to need one
  is a signal to revisit ADR-0020, not a local exception.
