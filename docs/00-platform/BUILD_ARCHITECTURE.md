---
title: Build Architecture Specification
document_id: BUILD-ARCH
version: 1.0.0
status: Accepted
owners: [Nyrqis Engineering]
created: 2026-09-01
updated: 2026-09-01
depends_on: [ADR-0012, ADR-0020, NPS-017]
satisfies: [NPC-007 gap 9]
---

# Build Architecture Specification

## Overview

This document specifies the build architecture for the Nyrqis Linux Backend.
It covers toolchain requirements, crate dependency graph, cross-compilation
targets, CI/CD pipeline, and reproducible build guidelines.

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
| python3 | ≥ 3.10.0 | Main backend language |
| pip | ≥ 22.0 | Package management |
| venv | built-in | Virtual environment support |

### System Libraries

| Library | Package (Ubuntu) | Purpose |
|---------|------------------|---------|
| libgbm | libgbm-dev | GBM buffer allocation (Phase 3) |
| libwayland | libwayland-dev | Wayland client libraries |
| libseccomp | libseccomp-dev | seccomp-BPF filtering |
| libfuse | libfuse-dev | FUSE filesystem support |

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
│  ┌──────────┐                                               │
│  │ gbm      │                                               │
│  │ (GPU)    │                                               │
│  └──────────┘                                               │
└─────────────────────────────────────────────────────────────┘
```

### Crate Versions

| Crate | ABI Version | Output | Tests |
|-------|-------------|--------|-------|
| nycore | 1.0.0 | cdylib | 0 (library) |
| seccomp | 1.2.0 | cdylib | 5 |
| syscalls | 1.2.0 | cdylib | 8 |
| keys | 1.0.0 | cdylib | 4 |
| container | 1.0.0 | cdylib | 10 |
| launcher | 1.0.0 | binary | 6 |
| ipc | 1.0.0 | cdylib | 3 |
| ipcd | 1.0.0 | binary | 5 |
| transport | 1.0.0 | cdylib | 3 |
| nyfs | 1.0.0 | cdylib | 4 |
| nyruntime | 1.0.0 | cdylib | 2 |
| nyui | 1.0.0 | cdylib | 3 |
| wayland | 1.2.0 | cdylib | 19 |
| gbm | 1.0.0 | cdylib | 14 |
| **Total** | | | **86** |

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
for crate in seccomp syscalls keys container ipc transport nyfs nyruntime nyui wayland gbm; do
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

## CI/CD Pipeline

### Workflow Jobs

| Job | Trigger | What it does |
|-----|---------|--------------|
| ci | push to main | Build + test all Rust crates + Python tests |
| arm64-conformance | push to main | Cross-compile for aarch64 + run conformance tests |
| docs | push to main (docs/) | Build MkDocs site + deploy to GitHub Pages |

### CI Job Details

#### `ci` (main pipeline)

1. **Rust crate builds** — 14 crates built with `cargo build --release`
2. **Rust crate tests** — 14 crate test suites (86 tests total)
3. **FFI conformance gates** — Python tests run with Rust crate loaded
4. **Python backend tests** — 2,500+ unit tests
5. **Performance benchmarks** — Latency and throughput measurements
6. **Code generator validation** — Verify generated code matches spec

#### `arm64-conformance`

1. **Cross-compile** — Build all crates for aarch64
2. **Conformance tests** — Run aarch64 seccomp syscall table validation on x86_64

#### `docs`

1. **Build site** — `mkdocs build --strict`
2. **Deploy** — Upload to GitHub Pages

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

## Reproducible Builds

### Deterministic Output

All Rust crates are built with:
- `--release` profile (optimization level 3)
- `--locked` flag (uses Cargo.lock exactly)
- `CARGO_INCREMENTAL=0` (no incremental compilation)

### Cargo.lock

The `Cargo.lock` file is committed to the repository to ensure
deterministic dependency resolution.

### Feature Flags

No feature flags are used by default. All crates build with their
default feature set.

## Testing Strategy

### Python Tests

| Category | Count | Description |
|----------|-------|-------------|
| Backend primitives | 2,500 | Container, IPC, storage, lifecycle |
| Package signing | 31 | Ed25519 signing/verification |
| Package installer | 17 | Install/uninstall/verify |
| Package integration | 11 | End-to-end signing + install |
| SDL2 Wayland | 21 | Compositor integration |
| **Total** | **2,580** | |

### Rust Tests

| Crate | Tests | Description |
|-------|-------|-------------|
| wayland | 19 | Wayland protocol, FFI, output detection |
| gbm | 14 | GBM buffer allocation |
| container | 10 | Container lifecycle |
| syscalls | 8 | Clone/launch |
| launcher | 6 | Process management |
| seccomp | 5 | seccomp-BPF |
| ipcd | 5 | IPC serving loop |
| keys | 4 | Key management |
| nyfs | 4 | FUSE operations |
| ipc | 3 | IPC codec |
| transport | 3 | Network transport |
| nyui | 3 | NUI document parsing |
| nyruntime | 2 | Runtime loop |
| **Total** | **86** | |

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
├── nyrqis_launcher          (binary)
└── nyrqis_ipcd              (binary)
```

### Python Packages

```
source/nyhal-linux-backend/
├── backend/
│   ├── container.py
│   ├── installer.py
│   ├── keys.py
│   ├── package_signing.py
│   └── ...
├── ui/
│   ├── compositor_sdl.py
│   ├── desktop_session.py
│   ├── wayland_codec.py
│   ├── wayland_display.py
│   └── ...
└── test_*.py
```

## References

- ADR-0012: NyHAL pluggable kernel backend
- ADR-0020: Implementation languages and the platform boundary
- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- NPC-007: Build architecture specification (gap 9)
