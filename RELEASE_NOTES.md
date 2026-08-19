# Nyrqis v1.0.0 Release Notes

**Release date:** 2026-08-18
**NUI Schema:** 1.0.0 / Accepted

## What is Nyrqis?

Nyrqis is a complete operating system platform consisting of:
- **Nyrqis** — the Linux backend, runtime, IPC, containers, UI runtime, compositor, and code generators
- **Nyforge** — the visual NUI editor/designer (separate repo)

Together they form a complete **design-to-runtime pipeline**:
```
Nyforge (design) → .nstudio → Nyrqis (validate + load + run + render) → shell
```

## Highlights

### 🎨 Full NUI Editor (32 features)
- Canvas with drag, move, resize, multi-select
- Component palette with 30+ types
- Metadata-driven inspector
- Behavior editor with AND/OR logic graphs and action chains
- Animation timeline with keyframes
- Expression language
- Code mode (Visual/Code toggle)
- Undo/Redo, copy/paste, snap-to-grid, alignment guides
- Responsive breakpoints, state scopes, localization
- Reusable components with variants
- Schema migrations (0.2.0 → 0.3.0 → 0.4.0 → 1.0.0)
- 5 self-hosted chrome pieces (Home, status bar, palette, inspector, layers)

### 🖼️ Compositors
- **PIL Compositor** — fast rendering (14ms for 290-component shell)
- **SDL2 Compositor** — GPU-accelerated, windowed + headless modes
- Eclipse and Solar themes
- 30+ component renderers

### 🔧 Code Generators
- **Rust** → `.rs` module for NyCore/NyRuntime
- **C++** → `.hpp` header for NyHAL
- **Python** → `.py` module for tooling/testing
- All generators produce valid, importable output
- Triple-target story per ADR-0020 language choices

### 🛠️ Developer Tools
- `render.py` — CLI rendering tool (PIL/SDL2, compare mode)
- `preview_server.py` — HTTP server with live reload
- `validate_generators.py` — CI validation gate
- `benchmarks_all.py` — Performance profiling

### 🚀 Runtime
- INuiRuntime interface
- ForgePreviewRuntime (editor)
- NyrqisRuntime (OS)
- NyrqisShell runner
- 6 IPC operations (validate, load, current, run, render, display)

### 📦 Deployment
- Dockerfile + docker-compose.yml
- GitHub Actions CI (generators + backend + Rust crates)
- 1069 tests, all green

## Quick Start

```bash
# Install dependencies
pip install Pillow pysdl2 pysdl2-dll pynacl numpy watchdog pyyaml

# Validate all generators
python3 tools/validate_generators.py --verbose

# Render the desktop shell
python3 tools/render.py source/nyhal-linux-backend/tests/fixtures/nstudio/desktop.nstudio

# Start the preview server
python3 tools/preview_server.py

# Generate native code
python3 tools/generate_rust.py desktop.nstudio /tmp/desktop.rs
python3 tools/generate_cpp.py desktop.nstudio /tmp/desktop.hpp
python3 tools/generate_python.py desktop.nstudio /tmp/desktop.py
```

Or use Docker:
```bash
docker compose up
```

## Test Results

| Category | Tests | Status |
|----------|-------|--------|
| Backend floor | 692 | ✅ |
| Shell runner | 19 | ✅ |
| PIL compositor | 22 | ✅ |
| SDL2 compositor | 17 | ✅ |
| Rust generator | 16 | ✅ |
| C++ generator | 19 | ✅ |
| Python generator | 19 | ✅ |
| **Total** | **1069** | ✅ |

## Performance

| Operation | Time |
|-----------|------|
| PIL render (desktop shell) | 14ms |
| SDL2 render (desktop shell) | 26ms |
| Rust code gen | 0.6ms |
| C++ code gen | 0.6ms |
| Python code gen | 0.6ms |
| NUI validation (floor) | ~1ms |

## Documentation

- [Getting Started](docs/getting-started.md)
- [CLI API Reference](docs/api/CLI.md)
- [IPC API Reference](docs/api/IPC.md)
- [Contributing Guide](CONTRIBUTING.md)
- [Project Status](docs/00-platform/PROJECT_STATUS.md)

## What's Next

- Real-time compositor optimization
- Performance profiling and optimization
- Additional code generators as needed
- Expanded tutorials and how-to guides

## Credits

Built with the Nyrqis platform team and AI assistance from Codebuff.
