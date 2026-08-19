# Nyrqis v1.0.0: The Complete OS Design Platform

**August 18, 2026**

We're excited to announce **Nyrqis v1.0.0** — the first production release of the Nyrqis operating system platform. This release marks the completion of the full design-to-runtime pipeline, from visual UI design to native code generation.

## What is Nyrqis?

Nyrqis is a complete operating system platform that bridges the gap between visual design tools and running software. It consists of two tightly integrated components:

- **Nyforge** — a visual editor for designing NUI (Nyrqis UI) interfaces
- **Nyrqis** — the runtime backend that validates, loads, executes, and renders those designs

Together, they form a pipeline that has never existed before in the OS space:

```
Design (Nyforge) → Save (.nstudio) → Validate → Load → Run → Render → Export (Rust/C++/Python)
```

## The Numbers

- **1069 tests** — all passing on CI
- **32 editor features** — every roadmap item implemented
- **2 compositor backends** — PIL (14ms) and SDL2 (GPU-accelerated)
- **3 code generators** — Rust, C++, and Python
- **6 IPC operations** — validate, load, current, run, render, display
- **NUI schema 1.0.0** — production-ready
- **Docker support** — Dockerfile + docker-compose.yml

## What Makes This Different

### Design-to-Native Pipeline

Most operating systems have a design tool and a runtime, but they're separate. Nyrqis connects them:

1. **Design** in Nyforge — drag components, set properties, write behaviors
2. **Save** as `.nstudio` — a JSON document that's the single source of truth
3. **Validate** — the import gate checks the design against the NUI contract
4. **Load** — the runtime reads the document and builds the component tree
5. **Execute** — behaviors fire, bindings sync, state updates flow
6. **Render** — the compositor draws pixels (PIL or SDL2)
7. **Export** — generate native code for Rust, C++, or Python

### Triple-Target Code Generation

Nyrqis can generate native code for three targets, matching the platform's language matrix:

| Target | Use Case | Generator |
|--------|----------|-----------|
| Rust | NyCore, NyRuntime | `generate_rust.py` |
| C++ | NyHAL, NyShell | `generate_cpp.py` |
| Python | Tooling, testing, build | `generate_python.py` |

Each generator produces valid, importable code. The Python output can be loaded and executed immediately:

```python
import sys
sys.path.insert(0, '/tmp')
import desktop

doc = desktop.load()
print(f'{doc.name} v{doc.version}')
# Nyrqis Desktop Shell v1.0.0
```

### The 290-Component Desktop Shell

The flagship design is a complete desktop shell with 290 components across 10 screens:

- **Desktop** — icons, dock, context menus
- **Taskbar** — start button, search, system tray, clock
- **Start Menu** — app launcher, pinned apps, power options
- **Window Manager** — title bars, controls, stacking
- **Notifications** — notification center, quick settings
- **Lock Screen** — clock, unlock, power menu
- **And more** — workspace switcher, command palette, settings

All of this renders in 14ms with the PIL compositor.

## Developer Experience

### Quick Start

```bash
# Install dependencies
pip install Pillow pysdl2 pysdl2-dll pynacl numpy watchdog pyyaml

# Start the preview server
python3 tools/preview_server.py

# Open http://localhost:8080
```

### CLI Tools

Every aspect of the pipeline has a CLI tool:

```bash
# Render designs to PNG
python3 tools/render.py desktop.nstudio

# Generate native code
python3 tools/generate_cpp.py desktop.nstudio /tmp/desktop.hpp

# Validate all generators
python3 tools/validate_generators.py --verbose

# Run benchmarks
python3 tests/benchmarks_all.py
```

### Docker

```bash
docker compose up
# Preview server at http://localhost:8080
```

## Performance

| Operation | Time |
|-----------|------|
| PIL render (desktop shell) | 14ms |
| SDL2 render (desktop shell) | 26ms |
| Rust code generation | 0.6ms |
| C++ code generation | 0.6ms |
| Python code generation | 0.6ms |
| NUI validation | ~1ms |

## What's Next

With v1.0.0 complete, the foundation is solid. Future work includes:

- **Real-time compositor** — optimize SDL2 for production use
- **Additional targets** — more code generators as needed
- **Expanded documentation** — tutorials, how-to guides, API docs
- **Performance optimization** — profiling and optimization of the runtime

## Try It

The full platform is open source:

- **Nyrqis**: [github.com/Myco-mycelium/Nythera](https://github.com/Myco-mycelium/Nythera)
- **Nyforge**: [github.com/Myco-mycelium/Nyforge](https://github.com/Myco-mycelium/Nyforge)

Start with the [getting started guide](../getting-started.md) or dive into the [API documentation](../api/CLI.md).

---

*Built with the Nyrqis platform team and AI assistance from Codebuff.*
