---
title: Build Architecture
document_id: BUILD-001
version: 1.0.0
status: Draft
classification: Informative
owners:
  - Nyrqis Architecture
created: 2026-09-06
updated: 2026-09-06
ai_assisted: true
review_cycle: Quarterly
depends_on: [ADR-0020, NPC-003]
---

# BUILD-001 — Build Architecture

This document describes the Nyrqis build system architecture,
including toolchain selection, build graph, cross-compilation,
reproducible builds, CI stages, and artifact signing.

## 1. Toolchain

### 1.1 Platform Languages

Per ADR-0020, the platform uses:

| Language | Role | Toolchain |
|----------|------|-----------|
| **Rust** | Performance-critical paths | `rustc` 1.75+, `cargo` |
| **C++** | Kernel, system services | `g++` 12+ or `clang++` 16+ |
| **C** | Low-level interfaces | `gcc` 12+ or `clang` 16+ |
| **Python** | Tooling, tests, reference floor | `python3.12+` |

### 1.2 Toolchain Selection

- **Rust**: Stable channel, MSRV (Minimum Supported Rust Version) tracked in `rust-toolchain.toml`
- **C/C++**: GCC default, Clang optional for sanitizers
- **Python**: System Python, no virtual environments for production

### 1.3 Cross-Compilation

Target triples supported:

| Target | Triple | Notes |
|--------|--------|-------|
| x86_64 Linux | `x86_64-unknown-linux-gnu` | Primary development target |
| ARM64 Linux | `aarch64-unknown-linux-gnu` | Raspberry Pi, phones |
| x86_64 Windows | `x86_64-pc-windows-msvc` | Windows compatibility layer |
| RISC-V 64 | `riscv64gc-unknown-linux-gnu` | Future hardware |

Cross-compilation uses Docker containers with pre-configured toolchains.

## 2. Build Graph

### 2.1 Dependency Order

```
1. Rust crates (seccomp, syscalls, nyfs, ipc, transport, wayland, gbm, drm, compositor)
2. C/C++ kernel modules (if applicable)
3. Python packages (backend, tools)
4. Documentation (MkDocs)
5. Tests (unit, integration, conformance)
```

### 2.2 Build Commands

```bash
# Full build
cargo build --release          # Rust crates
python3 -m build               # Python package
mkdocs build                   # Documentation

# Quick build (development)
cargo build --debug            # Rust debug build
python3 -m build --wheel       # Python wheel only

# Clean
cargo clean                    # Rust artifacts
rm -rf dist/ build/ *.egg-info # Python artifacts
```

### 2.3 Build Artifacts

| Artifact | Location | Format |
|----------|----------|--------|
| Rust crates | `rust/*/target/release/` | `.so` (cdylib) |
| Python package | `dist/` | `.whl` |
| Documentation | `site/` | HTML |
| Tests | `tests/` | Python, Rust |

## 3. Reproducible Builds

### 3.1 Requirements

All release builds MUST be reproducible:

1. **Deterministic output**: Same input → same output (bit-for-bit)
2. **Hermetic builds**: No network access during build
3. **Pinned dependencies**: All versions locked in lockfiles

### 3.2 Reproducibility Measures

- **Cargo.lock**: Committed to repository
- **requirements.txt**: Pinned Python dependencies
- **Docker images**: Pinned base images with SHA256 digests
- **Build scripts**: No timestamps or random data in output

### 3.3 Verification

```bash
# Build twice, compare hashes
cargo build --release
sha256sum target/release/libnyrqis_*.so > build1.sha256
cargo clean
cargo build --release
sha256sum target/release/libnyrqis_*.so > build2.sha256
diff build1.sha256 build2.sha256
```

## 4. CI Stages

### 4.1 Pipeline Overview

```
Push/PR → Lint → Build → Test → Security → Release
```

### 4.2 Stage Details

| Stage | Timeout | Actions |
|-------|---------|---------|
| **Lint** | 5 min | `clippy`, `rustfmt`, `pylint`, `black` |
| **Build** | 15 min | `cargo build --release`, `python3 -m build` |
| **Test** | 30 min | `cargo test`, `pytest`, conformance tests |
| **Security** | 10 min | `cargo audit`, `pip-audit`, SAST scan |
| **Release** | 20 min | Tag, build artifacts, publish |

### 4.3 CI Configuration

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cargo clippy -- -D warnings
      - run: cargo fmt --check
  
  build:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cargo build --release
  
  test:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cargo test
      - run: pytest
```

## 5. Artifact Signing

### 5.1 Signing Strategy

All release artifacts are signed using:

- **Code signing**: GPG signatures for release tarballs
- **Package signing**: Ed25519 for `.nypkg` packages (per NPS-026)
- **Container signing**: Cosign for OCI images (future)

### 5.2 Key Management

- **Signing keys**: Stored in CI/CD secrets, never in repository
- **Key rotation**: Annually, with 30-day overlap period
- **Key distribution**: Public keys published in repository

### 5.3 Verification

```bash
# Verify release signature
gpg --verify nyrqis-v1.0.0.tar.gz.sig nyrqis-v1.0.0.tar.gz

# Verify package signature
nyrqis-ctl package verify app.nypkg
```

## 6. Performance Budgets

### 6.1 Build Time Budgets

| Component | Target | Current |
|-----------|--------|---------|
| Rust crates (all) | < 5 min | ~3 min |
| Python package | < 2 min | ~1 min |
| Documentation | < 1 min | ~30 sec |
| Full CI pipeline | < 30 min | ~25 min |

### 6.2 Artifact Size Budgets

| Artifact | Target | Current |
|----------|--------|---------|
| Rust `.so` (all) | < 10 MB | ~6 MB |
| Python wheel | < 5 MB | ~3 MB |
| Documentation | < 10 MB | ~5 MB |

## 7. Environment Variables

### 7.1 Build Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `NYRQIS_RUST_LIB` | (auto) | Override Rust library path |
| `NYRQIS_RUST_FORCE` | `0` | Force Rust FFI (fail on error) |
| `NYRQIS_DEBUG` | `0` | Enable debug output |

### 7.2 CI Configuration

| Variable | Description |
|----------|-------------|
| `NYRQIS_CI` | Set to `1` in CI environments |
| `NYRQIS_COVERAGE` | Enable code coverage reporting |

## 8. Revision History

| Version | Date | Change |
|---------|------|--------|
| 1.0.0 | 2026-09-06 | Initial build architecture document |
