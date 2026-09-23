---
title: Build Architecture Specification
document_id: BUILD-ARCH
version: 2.0.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-09-01
updated: 2026-09-23
depends_on: [ADR-0012, ADR-0020, NPS-017, NPC-003]
satisfies: [NPC-007 gap 9]
---

# Build Architecture Specification

> **CANONICAL (AG decision log D6, 2026-09-23).** The
> build-architecture deliverable previously existed twice — this
> document (`BUILD-ARCH`, Accepted) and `BUILD-001` (Draft,
> 2026-09-06, the copy the roadmap/TUT-003/sdk cited). The Group
> decided the duality in favor of this document and ordered the
> other copy's policy content absorbed here and the file removed.
> The `Accepted` marking is **sanctioned retroactively by D6 itself**
> (the ADR-0022/0023 remedy — the marking had no prior Group record).
> The absorbed policy sections are marked with their origin; the
> toolchain rows and crate tables were re-verified against the tree
> at merge time (2026-09-23), which refreshed the 2026-09-01-vintage
> numbers.

## Overview

This document specifies the build architecture for the Nyrqis Linux
Backend. It covers toolchain requirements, build policy
(reproducible builds, artifact signing, build budgets), the crate
dependency graph, cross-compilation targets, the CI/CD pipeline,
and testing strategy. It satisfies NPC-007 gap 9.

## Toolchain Requirements

### Rust

| Component | Version | Notes |
|-----------|---------|-------|
| rustc | ≥ 1.75.0 | Stable channel |
| cargo | ≥ 1.75.0 | Matches rustc |
| rustfmt | stable | Used in CI formatting checks |
| clippy | stable | Used in CI lint checks |

### Python

| Component | Version | Notes |
|-----------|---------|-------|
| python3 | ≥ 3.10.0 | Main backend language (`pyproject.toml` `requires-python = ">=3.10"`; CI exercises 3.11 and 3.12) |
| pip | ≥ 22.0 | Package management |
| venv | built-in | Virtual environment support (development; the production posture below) |

### System Libraries

| Library | Package (Ubuntu) | Purpose |
|---------|------------------|---------|
| libgbm | libgbm-dev | GBM buffer allocation (Phase 3) |
| libwayland | libwayland-dev | Wayland client libraries |
| libseccomp | libseccomp-dev | seccomp-BPF filtering |
| libfuse | libfuse-dev | FUSE filesystem support |

### Platform Languages *(absorbed from BUILD-001 §1.1, corrected)*

Per ADR-0020, the platform uses:

| Language | Role | Toolchain |
|----------|------|-----------|
| **Rust** | Performance-critical paths | `rustc` 1.75+, `cargo` |
| **C++** | Kernel, system services | `g++` 12+ or `clang++` 16+ |
| **C** | Low-level interfaces | `gcc` 12+ or `clang` 16+ |
| **Python** | Tooling, tests, reference floor | `python3 ≥ 3.10` (CI: 3.11, 3.12) — the floor per `pyproject.toml`, not 3.12 |

The production posture on Python remains **system Python, no virtual
environments for production** (policy, absorbed from BUILD-001 §1.2);
venv support is a development convenience.

## Build Policy *(absorbed from BUILD-001 §3–§5)*

### Reproducible Builds

All release builds **MUST** be reproducible:

1. **Deterministic output**: Same input → same output (bit-for-bit)
2. **Hermetic builds**: No network access during build
3. **Pinned dependencies**: All versions locked in lockfiles

Measures (absorbed, cross-checked with this document's own build
flags):

- **Cargo.lock**: Committed to repository; crates build with
  `--locked` and `CARGO_INCREMENTAL=0`
- **requirements.txt**: Pinned Python dependencies
- **Docker images**: Pinned base images with SHA256 digests
- **Build scripts**: No timestamps or random data in output

Verification recipe (build twice, compare hashes):

```bash
cargo build --release
sha256sum target/release/libnyrqis_*.so > build1.sha256
cargo clean
cargo build --release
sha256sum target/release/libnyrqis_*.so > build2.sha256
diff build1.sha256 build2.sha256
```

### Artifact Signing

- **Code signing**: GPG signatures for release tarballs
- **Package signing**: Ed25519 for `.nypkg` packages — normative in
  NPS-026 §6 and implemented (`backend/package_signing.py`,
  `backend/package_pki.py`; the PKI implementation surface NPS-028 is
  Accepted as of D5)
- **Container signing**: Cosign for OCI images (future)

Signing keys live in CI/CD secrets, never in the repository; rotation
is annual with a 30-day overlap; public keys are published in the
repository.

### Build Time Budgets *(absorbed from BUILD-001 §6.1)*

| Component | Target | Current |
|-----------|--------|---------|
| Rust crates (all) | < 5 min | ~3 min |
| Python package | < 2 min | ~1 min |
| Documentation | < 1 min | ~30 sec |
| Full CI pipeline | < 30 min | ~25 min |

## Crate Dependency Graph

```
┌─────────────────────────────────────────────────────────────┐
│                    Nyrqis Linux Backend                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ nycore   │  │ seccomp  │  │ syscalls │  │ keys     │   │
│  │ (core)   │  │ (BPF)    │  │ (clone)  │  │ (crypto) │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │              │              │              │         │
│  ┌────┴──────────────┴──────────────┴──────────────┴────┐   │
│  │                    container                          │   │
│  │              (container lifecycle)                    │   │
│  └────┬──────────────┬──────────────┬──────────────┬────┘   │
│       │              │              │              │         │
│  ┌────┴─────┐  ┌─────┴────┐  ┌─────┴────┐  ┌─────┴────┐   │
│  │ launcher │  │ ipc      │  │ ipcd     │  │ transport│   │
│  │ (init)   │  │ (codec)  │  │ (serving)│  │ (net)    │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ nyfs     │  │ nyruntime│  │ nyui     │  │ wayland  │   │
│  │ (FUSE)   │  │ (loop)   │  │ (NUI)    │  │ (display)│   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                                                             │
│  ┌──────────┐  ┌──────────┐                                │
│  │ gbm      │  │ drm/egl/ │                                │
│  │ (GPU)    │  │ vulkan   │                                │
│  └──────────┘  └──────────┘                                │
└─────────────────────────────────────────────────────────────┘
```

### Crate Versions and Test Counts *(re-verified 2026-09-23)*

Counts are grep-level `#[test]` occurrences — the honest floor; exact
suite counts belong to `cargo test`. 18 code crates plus the
`README.md`; launcher is the sole `binary` crate.

| Crate | ABI Version | Output | Tests |
|-------|-------------|--------|-------|
| compositor | 0.1.0 | cdylib | 67 |
| wayland | 0.1.0 | cdylib | 27 |
| nyui | 0.1.0 | cdylib | 26 |
| ipcd | 1.0.0 | binary | 24 |
| egl | 0.1.0 | cdylib | 16 |
| gbm | 0.1.0 | cdylib | 15 |
| seccomp | 0.1.0 | cdylib | 15 |
| container | 0.1.0 | cdylib | 14 |
| nyruntime | 0.1.0 | cdylib | 14 |
| syscalls | 0.1.0 | cdylib | 14 |
| ipc | 0.1.0 | cdylib | 11 |
| launcher | 1.0.0 | binary | 10 |
| nyfs | 0.1.0 | cdylib | 10 |
| vulkan | 0.1.0 | cdylib | 13 |
| drm | 0.1.0 | cdylib | 8 |
| keys | 0.1.0 | cdylib | 8 |
| nycore | 0.1.0 | cdylib | 8 |
| transport | 0.1.0 | cdylib | 5 |
| **Total** | | | **305** |

(The 2026-09-01 table's 18-crate/121-test figures are superseded —
compositor alone was 8 then, 67 now. The `nycore` ABI version listed
in the old table as 1.0.0 is 0.1.0 in its `Cargo.toml`; launcher/ipcd
carry 1.0.0.)

## Build Commands

### Development Build

```bash
# Build all crates in debug mode
cd source/nyhal-linux-backend/rust
for crate in */; do
  cd "$crate" && cargo build && cd ..
done

# Run all Python tests
cd source/nyhal-linux-backend
python3 -B -m unittest discover -s . -p "test_*.py"
```

### Release Build

```bash
# Build all crates in release mode
cd source/nyhal-linux-backend/rust
for crate in */; do
  cd "$crate" && cargo build --release && cd ..
done

# Verify cdylib artifacts
for crate in seccomp syscalls keys container ipc transport nyfs nyruntime nyui wayland gbm drm; do
  test -s "$crate/target/release/libnyrqis_${crate//-/_}.so" && echo "✓ $crate"
done
```

### Cross-Compilation

```bash
# Install cross-compilation target
rustup target add aarch64-unknown-linux-gnu

# Cross-compile for aarch64
cd source/nyhal-linux-backend/rust
for crate in */; do
  cd "$crate" && cargo build --release --target aarch64-unknown-linux-gnu && cd ..
done
```

### Cross-Compilation Targets *(absorbed from BUILD-001 §1.3)*

| Target | Triple | Notes |
|--------|--------|-------|
| x86_64 Linux | `x86_64-unknown-linux-gnu` | Primary development target |
| ARM64 Linux | `aarch64-unknown-linux-gnu` | Raspberry Pi, phones |
| x86_64 Windows | `x86_64-pc-windows-msvc` | Windows compatibility layer (future, per ADR-0005's translation-layer approach) |
| RISC-V 64 | `riscv64gc-unknown-linux-gnu` | Future hardware |

Cross-compilation uses Docker containers with pre-configured
toolchains. (The Windows target row is restored by the merge — the
2026-09-01 table omitted it.)

## CI/CD Pipeline

### Workflow Jobs

| Job | Trigger | What it does |
|-----|---------|--------------|
| ci | push to main | Build + test all Rust crates + Python tests |
| arm64-conformance | push to main | Cross-compile for aarch64 + run conformance tests |
| docs | push to main (docs/) | Build MkDocs site + run the doc/design gates |

### CI Job Details

#### `ci` (main pipeline)

1. **Rust crate builds** — 18 crates built with `cargo build --release`
2. **Rust crate tests** — per-crate test suites (305 `#[test]` as of
   2026-09-23)
3. **FFI conformance gates** — Python tests run with Rust crate loaded
4. **Python backend tests** — 6,500+ tests
5. **Performance benchmarks** — Latency and throughput measurements
6. **Code generator validation** — Verify generated code matches spec

#### `arm64-conformance`

1. **Cross-compile** — Build all crates for aarch64
2. **Conformance tests** — Run aarch64 seccomp syscall table validation on x86_64

#### `docs`

1. **Check depends_on cycles** + **recorded doc premises**
2. **`.nstudio` design gate** — every shipped shell design validated
   through the real import gate (2026-09-23)
3. **Build site** — `mkdocs build --strict`
4. **Deploy** — Upload to GitHub Pages

### Required Gates

The following jobs are required for merge:

- `Rust IPC codec FFI conformance (required gate)`
- `Rust NyRuntime FFI conformance (required gate)`
- `Rust keys FFI conformance (required gate)`
- `Rust NyFS codec FFI conformance (required gate)`
- `Rust seccomp crate (build + tests)`
- `Rust syscalls FFI conformance (required gate)`
- `Rust FFI conformance (required gate)`
- `Rust container FFI conformance (required gate)`
- `Rust IPC serving loop FFI conformance (required gate)`
- `Rust launcher-init FFI conformance (required gate)`
- `Rust NUI FFI conformance (required gate)`
- `Python backend tests`
- `Performance benchmarks`
- `Code generator validation`

### Stage Timeouts *(absorbed from BUILD-001 §4.2)*

| Stage | Timeout |
|-------|---------|
| Lint | 5 min |
| Build | 15 min |
| Test | 30 min |
| Security | 10 min |
| Release | 20 min |

## Testing Strategy

### Conformance Testing

- **x86_64 baseline** — All seccomp syscalls verified
- **aarch64 baseline** — Conservative subset verified via cross-compilation
- **FFI conformance** — Python tests run with Rust crate loaded (byte-identical output)

## Artifact Layout

### Release Artifacts

```
target/release/
├── libnyrqis_seccomp.so
├── libnyrqis_syscalls.so
├── libnyrqis_keys.so
├── libnyrqis_container.so
├── libnyrqis_ipc.so
├── libnyrqis_transport.so
├── libnyrqis_nyfs.so
├── libnyrqis_nyruntime.so
├── libnyrqis_nyui.so
├── libnyrqis_wayland.so
├── libnyrqis_gbm.so
├── libnyrqis_drm.so
├── nyrqis_launcher          (binary)
└── nyrqis_ipcd              (binary)
```

## Revision History

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-09-01 | Initial build architecture specification (18 crates, 121 tests — the figures of that date) |
| 2.0.0 | 2026-09-23 | **CANONICAL (AG decision log D6)**: BUILD-001's policy content absorbed (platform-language matrix with the corrected 3.10 floor, cross-compilation targets incl. the Windows row, reproducible-build MUSTs + verification recipe, artifact signing, stage timeouts, build budgets); its `Accepted` marking sanctioned retroactively by D6 (no prior record existed — the ADR-0022/0023 remedy); the `docs/reference/build/` copy removed and its citations re-pointed here; crate table refreshed to 2026-09-23 (18 code crates, 305 tests) |

## References

- ADR-0012: NyHAL pluggable kernel backend
- ADR-0020: Implementation languages and the platform boundary
- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- NPC-007: Build architecture specification (gap 9)
- `AG_BRIEF_BUILD_ARCH_DUALITY.md`: the duality finding and options
  ledger that D6 decided

**End of Document**
