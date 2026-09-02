# Getting Started with Nyrqis

Welcome to Nyrqis — a from-scratch operating system targeting native performance across desktops, laptops, tablets, phones, handhelds, and consoles.

## Quick Start (5 minutes)

### 1. Clone and install

```bash
git clone https://github.com/Myco-mycelium/Nythera.git
cd Nythera/Nyrqis/source/nyhal-linux-backend

# Install in development mode
python3 -m pip install --editable ".[dev,crypto,ui]"
```

### 2. Run the boot test

```bash
# Boot the system headless (no display needed)
python3 nyrqis_init.py --headless
```

This will:
- Start the backend daemon
- Load the default shell design
- Render a 1920x1080 frame
- Save it to `/tmp/nyrqis_session.png`

### 3. Check the output

```bash
# View the rendered desktop
xdg-open /tmp/nyrqis_session.png
```

## Development Setup

### Prerequisites

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Python | 3.10 | 3.12+ |
| Rust | 1.75 | stable |
| Linux kernel | 5.10 | 6.1+ |

### System dependencies

```bash
# Ubuntu/Debian
sudo apt install -y python3-dev python3-pip python3-venv \
    fuse3 libfuse-dev libseccomp-dev

# For GPU support (optional)
sudo apt install -y libgbm-dev libegl1-mesa-dev libvulkan-dev libdrm-dev
```

### Build Rust crates

```bash
cd rust
for crate in */; do
    cd "$crate" && cargo build --release && cd ..
done
```

### Run the test suite

```bash
# Run all tests
python3 -m unittest discover -s . -p "test_*.py"

# Run specific test files
python3 -m unittest tests.test_boot_init -v
python3 -m unittest tests.test_gpu_pipeline -v

# Run GPU benchmarks
python3 tests/benchmarks_gpu.py -n 100
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Nyrqis Linux Backend                  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ nycore   │  │ seccomp  │  │ syscalls │  │ keys   │ │
│  │ (core)   │  │ (BPF)    │  │ (clone)  │  │(crypto)│ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───┬────┘ │
│       │              │              │            │      │
│  ┌────┴──────────────┴──────────────┴────────────┴───┐  │
│  │                    container                       │  │
│  │              (container lifecycle)                 │  │
│  └────┬──────────────┬──────────────┬────────────┬───┘  │
│       │              │              │            │      │
│  ┌────┴─────┐  ┌─────┴────┐  ┌─────┴────┐  ┌───┴────┐ │
│  │ launcher │  │ ipc      │  │ ipcd     │  │transport│ │
│  │ (init)   │  │ (codec)  │  │ (serving)│  │ (net)  │ │
│  └──────────┘  └──────────┘  └──────────┘  └────────┘ │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ nyfs     │  │ nyruntime│  │ nyui     │  │ wayland│ │
│  │ (FUSE)   │  │ (loop)   │  │ (NUI)    │  │(display)│ │
│  └──────────┘  └──────────┘  └──────────┘  └────────┘ │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ gbm      │  │ drm      │  │ egl      │  │vulkan  │ │
│  │ (GPU)    │  │ (display)│  │ (OpenGL) │  │(native)│ │
│  └──────────┘  └──────────┘  └──────────┘  └────────┘ │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │              compositor (Wayland)                 │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 18 Rust crates

| Crate | Purpose |
|-------|---------|
| `nycore` | Core types and utilities |
| `seccomp` | BPF policy compiler |
| `syscalls` | Direct syscall wrappers |
| `keys` | Envelope encryption |
| `container` | Container lifecycle |
| `launcher` | PID-1 init binary |
| `ipc` | Wire codec |
| `ipcd` | IPC serving loop |
| `transport` | Unix-domain datagrams |
| `nyfs` | SHA-256 + Zstandard |
| `nyruntime` | Runtime loop |
| `nyui` | NUI document parser |
| `wayland` | Wayland client FFI |
| `gbm` | GPU buffer allocation |
| `drm` | Display modesetting |
| `egl` | OpenGL ES rendering |
| `compositor` | Wayland compositor |
| `vulkan` | Native graphics API |

## Running Commands

### CLI tools

```bash
# Boot the system
python3 nyrqis_backend.py boot

# Start the daemon
python3 nyrqis_backend.py service serve --socket /tmp/nyrqis-status.sock

# Check status
python3 nyrqisctl.py --socket /tmp/nyrqis-status.sock status

# Run a container
python3 nyrqisctl.py --socket /tmp/nyrqis-status.sock containers run /bin/sh

# Load a shell design
python3 nyrqisctl.py --socket /tmp/nyrqis-status.sock nui load shell.nstudio

# Start the desktop session
python3 nyrqis_session.py shell.nstudio
```

### Using the wrapper

```bash
# The nyrqis-ctl wrapper simplifies common operations
./nyrqis-ctl status
./nyrqis-ctl containers list
./nyrqis-ctl app list
./nyrqis-ctl vault list
./nyrqis-ctl init --headless
```

## GPU Support

### Check GPU availability

```bash
# Check for DRM devices
ls /dev/dri/

# Check GPU hardware
lspci | grep -i vga

# Run GPU pipeline tests
python3 -m unittest tests.test_gpu_pipeline -v

# Run GPU benchmarks
python3 tests/benchmarks_gpu.py -n 100
```

### Setup DRM access (without root)

```bash
# Check current status
./packaging/setup-drm.sh --check

# Install udev rules (requires sudo)
./packaging/setup-drm.sh --install

# Log out and back in for group changes
```

## Creating Shell Designs

Shell designs use the NUI `.nstudio` format. See `shell/defaults/README.md` for details.

```bash
# Validate a design
python3 nyrqis_run.py your-shell.nstudio --validate-only

# Render a preview
python3 nyrqis_run.py your-shell.nstudio -o preview.png

# Start an interactive session
python3 nyrqis_session.py your-shell.nstudio
```

## Testing

### Run all tests

```bash
python3 -m unittest discover -s . -p "test_*.py"
```

### Run specific test suites

```bash
# Boot integration tests
python3 -m unittest tests.test_boot_init -v

# GPU pipeline tests
python3 -m unittest tests.test_gpu_pipeline -v

# Desktop session tests
python3 -m unittest tests.test_desktop_session -v
```

### Run benchmarks

```bash
# GPU benchmarks
python3 tests/benchmarks_gpu.py -n 100

# General benchmarks
python3 tests/benchmarks.py
```

## Next Steps

1. Read the [Nyrqis Manifest](../../docs/00-platform/000-THE_NYRQIS_MANIFEST.md) — why this project exists
2. Review the [Project Constitution](../../docs/00-platform/001-PROJECT_CONSTITUTION.md) — enforceable rules
3. Check [REPOSITORY_STATE.md](../../docs/00-platform/REPOSITORY_STATE.md) — what currently exists
4. See [CONTRIBUTING.md](../CONTRIBUTING.md) — how to contribute

## References

- [NPS-017](../../docs/reference/nps/NPS-017-nyhal-kernel-abstraction.md) — NyHAL Backend Contract
- [ADR-0012](../../docs/reference/adr/ADR-0012-nyhal-pluggable-kernel-backend.md) — Pluggable kernel backend
- [ADR-0020](../../docs/reference/adr/ADR-0020-implementation-languages.md) — Implementation languages
- [ADR-0026](../../docs/reference/adr/ADR-0026-wayland-display-server.md) — Wayland integration
- [NEXT_SESSION_PLAN](../00-platform/NEXT_SESSION_PLAN.md) — Current priorities
