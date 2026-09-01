---
title: Contributor Setup Guide
document_id: HOWTO-CONTRIB
version: 1.0.0
status: Accepted
owners: [Nyrqis Engineering]
created: 2026-09-01
updated: 2026-09-01
depends_on: [NPS-017, ADR-0020]
---

# Contributor Setup Guide

This guide walks you through setting up a development environment for
the Nyrqis Linux Backend and Nyforge.

## Prerequisites

### Required

| Tool | Version | Notes |
|------|---------|-------|
| **Python** | 3.10+ | The reference floor and tooling language |
| **Rust** | 1.75+ | For the compiled crates below the platform boundary |
| **Git** | 2.30+ | Repository management |

### Optional (for full functionality)

| Tool | Purpose |
|------|---------|
| **.NET 8 SDK** | Building Nyforge (C#/Avalonia) |
| **QEMU user-mode** | arm64 cross-architecture testing |
| **Docker** | Container runtime tests (some tests skip without it) |

## Repository Layout

```
Nyrqis/
├── source/
│   ├── nyhal-linux-backend/    # The Linux backend (Python + Rust)
│   │   ├── backend/             # Core: container, seccomp, keys, storage
│   │   ├── ipc/                 # IPC service, codec, transport
│   │   ├── ui/                  # NUI runtime, compositor, desktop session
│   │   ├── rust/                # Rust crates (ADR-0020 migrations)
│   │   │   ├── seccomp/         # BPF policy compiler
│   │   │   ├── syscalls/        # Direct syscall wrappers
│   │   │   ├── nyfs/            # Checksum/compression hot path
│   │   │   ├── ipc/             # Wire codec
│   │   │   ├── container/       # Launch-plan primitives
│   │   │   ├── transport/       # Unix-domain datagram transport
│   │   │   ├── ipcd/            # IPC serving loop (ADR-0021)
│   │   │   ├── keys/            # Key management (PyNaCl floor + Rust)
│   │   │   ├── launcher/        # Container launcher binary
│   │   │   ├── nyui/            # NUI import gate (ADR-0025)
│   │   │   ├── nyruntime/       # Runtime core
│   │   │   ├── nycore/          # Core utilities
│   │   │   └── wayland/         # Display server client (ADR-0026)
│   │   ├── test_backend.py      # Main test suite (2,500+ tests)
│   │   └── IMPLEMENTATION_STATUS.md
│   └── ...
├── Nyforge/                      # C#/Avalonia visual editor
├── tests/
│   └── BENCHMARK_RESULTS.md      # Performance gate results
└── docs/
    ├── reference/                # NPS, ADRs, ABI specs
    ├── tutorials/                # Getting-started guides
    └── how-to/                   # Task-oriented guides
```

## Quick Start

### 1. Clone and enter the repo

```bash
git clone <repo-url> Nyrqis
cd Nyrqis
```

### 2. Set up Python

```bash
# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# No external dependencies needed — the backend is stdlib-only
# (except PyNaCl for key management, which is optional)
```

### 3. Build the Rust crates

```bash
cd source/nyhal-linux-backend/rust

# Build all crates (release mode for conformance testing)
for crate in seccomp syscalls nyfs ipc container transport ipcd keys launcher nyui nyruntime nycore wayland; do
    echo "Building $crate..."
    (cd "$crate" && cargo build --release 2>&1 | tail -1)
done

cd ../..
```

### 4. Run the test suite

```bash
cd source/nyhal-linux-backend

# Run all tests (expect ~2,500 passing)
python3 -B -m unittest test_backend -v 2>&1 | tail -5

# Run a specific test class
python3 -B -m unittest test_backend.TestDefaultDeny -v

# Run with Rust conformance gates enabled
NYRQIS_RUST_FORCE=1 python3 -B -m unittest test_backend -v 2>&1 | tail -5
```

### 5. Start the daemon (optional)

```bash
cd source/nyhal-linux-backend
python3 -c "
from backend.container import ContainerManager
from ipc.service import ServiceManager
mgr = ContainerManager()
svc = ServiceManager(mgr)
svc.start()
"
```

## Rust Crate Conventions

Each Rust crate follows the same migration pattern (ADR-0020):

1. **`cdylib` + `rlib`** — the cdylib is the FFI artifact; rlib is for
   `cargo test`.
2. **Minimal dependencies** — most crates depend only on `libc`.  The
   `nyui` crate adds `serde_json` for JSON parsing.
3. **Versioned FFI** — each crate exports a `*_version() -> u32` function
   returning the ABI version.
4. **Error reporting** — each crate has a `*_last_error(buf, cap) -> i32`
   function for diagnostics.
5. **Conformance gate** — each crate has a required CI job that forces the
   Python floor's tests through the FFI:
   ```bash
   NYRQIS_RUST_LIB=/path/to/libfoo.so NYRQIS_RUST_FORCE=1 python3 -m unittest test_backend.TestFooConformance
   ```

### Adding a new crate

1. Create `rust/<name>/Cargo.toml` and `rust/<name>/src/lib.rs`
2. Follow the FFI surface convention (version + last_error + operations)
3. Create `ui/<name>_codec.py` (the Python loader)
4. Add conformance tests to `test_backend.py`
5. Add a CI job to `.github/workflows/ci.yml`
6. Update `rust/README.md` with the new crate's status

## Testing

### Test categories

| Category | What it tests | Count |
|----------|---------------|-------|
| Container primitives | Namespaces, cgroups, PID-1, network, freezer | ~400 |
| Seccomp/LSM | Default-deny, BPF policy, AppArmor/SELinux | ~200 |
| IPC | Codec, transport, serving loop, SCM_CREDENTIALS | ~300 |
| Storage (NyFS) | Block I/O, CoW, snapshots, compression, durability | ~400 |
| Keys/Vault | KEK, DEK, at-rest encryption, rekey, volume lifecycle | ~100 |
| Boot/lifecycle | 4-phase boot, transition validation, Secure Boot | ~150 |
| NUI | Expression language, document model, import gate, compositor | ~650 |
| Cross-architecture | aarch64 syscall table validation | 7 |
| Conformance | Rust vs Python floor differential testing | ~300 |

### Running specific test groups

```bash
# Container tests only
python3 -B -m unittest test_backend.TestContainer -v

# Encryption tests only
python3 -B -m unittest test_backend.TestKeysFloor -v

# NUI tests only (if separate file exists)
python3 -B -m unittest test_backend.TestNuiExpression -v

# Cross-architecture conformance
python3 -B -m unittest test_backend.TestCrossArchitectureConformance -v
```

### Rust unit tests

```bash
cd rust/<name>
cargo test
```

## CI

The CI pipeline runs on every push:

1. **Python tests** — `python3 -m unittest test_backend` (all 2,500+)
2. **Rust builds** — each crate builds and passes `cargo test`
3. **Conformance gates** — each crate's Python floor is forced through the FFI
4. **arm64 conformance** — cross-compiles Rust crates for aarch64 and runs
   cross-architecture tests via QEMU (`.github/workflows/arm64-conformance.yml`)

## Debugging Tips

### Rust crate not loading

```bash
# Check if the cdylib exists
ls -la rust/<name>/target/release/lib<name>.so

# Force the path
NYRQIS_RUST_LIB=/absolute/path/to/libfoo.so python3 -m unittest test_backend.TestFoo

# Debug with RUST_LOG
RUST_LOG=debug NYRQIS_RUST_LIB=... python3 -m unittest test_backend.TestFoo
```

### Test isolation

```bash
# Run with fresh state (no cached daemon)
rm -rf /tmp/nyrqis-test-*
python3 -B -m unittest test_backend.TestStorageService -v
```

### Checking ABI compatibility

```bash
# Verify the ABI version matches
python3 -c "from ui.wayland_codec import NYRQIS_WAYLAND_ABI; print(f'ABI: 0x{NYRQIS_WAYLAND_ABI:08X}')"
python3 -c "import ctypes; lib=ctypes.CDLL('rust/wayland/target/release/libnyrqis_wayland.so'); lib.nyrqis_wayland_version.restype=ctypes.c_uint32; print(f'Crate: 0x{lib.nyrqis_wayland_version():08X}')"
```

## Further Reading

- [Backend Quickstart Tutorial](linux-backend-quickstart.md) — first steps with the daemon
- [Vault Operations Guide](../how-to/vault-operations.md) — key management and at-rest encryption
- [Project Status Summary](../00-platform/PROJECT_STATUS_SUMMARY.md) — stakeholder overview
- [ADR-0020](../reference/adr/ADR-0020-implementation-languages.md) — language matrix and migration rules
- [ADR-0026](../reference/adr/ADR-0026-wayland-display-server-integration.md) — Wayland integration plan
