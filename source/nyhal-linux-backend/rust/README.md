# Rust Migration Workspace (ADR-0020)

First ADR-0020 migration landing pad for the Linux backend. Per
**ADR-0020** (Python and Rust as the Nyrqis implementation languages),
kernel-adjacent, hot-path, and security-critical components migrate to
Rust behind a versioned FFI boundary; user-space orchestration stays
Python. See `docs/reference/adr/ADR-0020-implementation-languages.md`
for the full strategy and the ABI rule.

## Status

| Component | Status | Evidence gate (Migration rule) |
|-----------|--------|--------------------------------|
| `seccomp/` policy compiler (BPF generate/validate/simulate) | **Scaffold; Python-side conformance groundwork + FFI loader DONE, Rust implementation blocked on a toolchain** (rustup download does not complete on this host — 2026-08-12). CI (`.github/workflows/ci.yml`) builds the crate on every push; a non-blocking conformance job forces the full Python suite through the FFI and turns green when the port lands | Security posture (memory-safe policy compiler, ADR-0020 priority #1) |
| syscall wrappers (`clone`/`unshare`, …) | Not started | ADR-0020 priority #2 (implementation_plan.md) |
| NyFS checksum/compression hot path | Not started | ADR-0020 priority #3 — gated on §4/§5 measured bottleneck |**Honesty note:** nothing in this directory compiles locally yet — there
is no Rust toolchain on the current dev host (rustup's toolchain download
has not completed in several attempts). The crate is a scaffold
(spec + FFI contract + conformance plan) and **MUST NOT** be treated as
a built or tested component; `.github/workflows/ci.yml` is the first
place it compiles. The Python-side conformance groundwork is done and
verified (serializer + round-trip + syscall-table fixes in
`backend/seccomp.py`) and the **FFI loader is implemented and tested**
(`TestRustFfILoader` pins the wire format and the fallback/force
contract). The Rust implementation itself remains to be written and
built once a toolchain exists. The pure-Python implementation remains
the only shipped implementation until the first Rust module passes its
conformance suite through the FFI.
