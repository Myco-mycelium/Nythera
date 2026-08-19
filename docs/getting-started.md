# Getting Started with Nyrqis

This tutorial walks through the complete **design-to-runtime pipeline**:
from creating a UI design in Nyforge to running it on the Nyrqis backend.

## Prerequisites

- Python 3.10+
- pip

```bash
# Install dependencies
pip install Pillow pysdl2 pysdl2-dll pynacl numpy watchdog pyyaml
```

Or use Docker:

```bash
docker compose up
```

## Quick Start (5 minutes)

### 1. Validate an existing design

The repo ships with several `.nstudio` design files. Let's validate one:

```bash
# From the Nyrqis repo root
python3 tools/validate_generators.py --verbose
```

This runs all three code generators (Rust, C++, Python) and the PIL compositor
on every `.nstudio` fixture. You should see `36/36 passed`.

### 2. Render a design to PNG

```bash
# Render the Nyrqis Desktop Shell
python3 tools/render.py source/nyhal-linux-backend/tests/fixtures/nstudio/desktop.nstudio

# Output goes to ./render_output/desktop/
ls render_output/desktop/
# desktop.png  lock.png
```

### 3. Compare rendering backends

```bash
python3 tools/render.py source/nyhal-linux-backend/tests/fixtures/nstudio/desktop.nstudio --compare
```

This renders with both PIL and SDL2 backends and shows timing:
```
Backend: PIL
  desktop: 1440x900 (26,037 bytes, 14.2ms)
  lock: 1440x900 (8,174 bytes, 6.8ms)
Backend: SDL
  desktop: 1440x900 (8,987 bytes, 742.0ms)
  lock: 1440x900 (6,332 bytes, 14.3ms)
```

### 4. Start the preview server

```bash
python3 tools/preview_server.py
```

Open http://localhost:8080 in your browser. You'll see the Nyrqis Desktop Shell
rendered as a PNG image with a screen selector and live reload.

### 5. Generate native code

```bash
# Generate C++ header
python3 tools/generate_cpp.py source/nyhal-linux-backend/tests/fixtures/nstudio/desktop.nstudio /tmp/desktop.hpp

# Generate Python module
python3 tools/generate_python.py source/nyhal-linux-backend/tests/fixtures/nstudio/desktop.nstudio /tmp/desktop.py

# Verify the Python module loads
python3 -c "import sys; sys.path.insert(0, '/tmp'); import desktop; doc = desktop.load(); print(f'{doc.name} v{doc.version}: {len(doc.screens)} screens')"
```

## Understanding the Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│  Nyforge (Visual Editor)                                    │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌───────────┐  │
│  │ Canvas  │  │ Palette  │  │ Inspector │  │ Behaviors │  │
│  └────┬────┘  └────┬─────┘  └─────┬─────┘  └─────┬─────┘  │
│       └────────────┼──────────────┼───────────────┘         │
│                    ▼              ▼                          │
│              ┌─────────────────────────┐                    │
│              │    .nstudio (NUI)       │                    │
│              └────────────┬────────────┘                    │
└───────────────────────────┼─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Nyrqis (Runtime)                                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ Validate │  │   Load   │  │   Run    │  │  Render   │  │
│  │ (gate)   │  │ (persist)│  │ (behaviors│  │ (PNG/SDL) │  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────┘  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Code Generators: Rust │ C++ │ Python               │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Key Concepts

### NUI Schema (v1.0.0)

The `.nstudio` format is a JSON document with:
- **Screens**: named viewports with size and root component tree
- **Components**: typed nodes with layout, properties, events, children
- **Behaviors**: WHEN/IF/DO logic graphs with AND/OR conditions
- **Bindings**: state-to-property synchronization
- **State**: global, screen, component, session, and persistent scopes

### Component Types

The NUI schema defines 30+ component types across categories:

| Category | Types |
|----------|-------|
| Basic | Button, Text, Input, Toggle, Checkbox, Slider, Image, Icon |
| Layout | Container, Stack, Grid, Dock, SplitView, ScrollView |
| Shell | Taskbar, StartMenu, SystemTray, LockScreen, WindowFrame |
| System | NotificationCenter, QuickSettings, WorkspaceSwitcher |

### Themes

Two built-in themes:
- **Eclipse** (dark): background `#1e1e1e`, accent `#6495ed`
- **Solar** (light): background `#fdf6e3`, accent `#268bd2`

### Code Generators

Three targets matching the platform's language matrix:

| Generator | Target | Output |
|-----------|--------|--------|
| `generate_rust.py` | NyCore/NyRuntime | `.rs` module |
| `generate_cpp.py` | NyHAL | `.hpp` header |
| `generate_python.py` | Tooling/testing | `.py` module |

## Running Tests

```bash
# Backend tests (692 tests)
cd source/nyhal-linux-backend && python3 -B test_backend.py

# Compositor tests (22 tests)
python3 -m unittest tests.test_compositor

# SDL2 compositor tests (17 tests)
python3 -m unittest tests.test_compositor_sdl

# Generator tests (19 + 19 tests)
python3 -m unittest tests.test_generate_cpp tests.test_generate_python

# Shell tests (19 tests)
python3 -m unittest tests.test_shell

# All tests
cd source/nyhal-linux-backend && python3 -B test_backend.py
```

## Docker Usage

```bash
# Build the image
docker build -t nyrqis .

# Run the preview server
docker run -p 8080:8080 nyrqis

# Run with a custom design
docker run -p 8080:8080 -v ./mydesign.nstudio:/app/mydesign.nstudio \
    nyrqis --file /app/mydesign.nstudio

# Run tests
docker run --rm nyrqis -c "cd /app/source/nyhal-linux-backend && python3 -B test_backend.py"

# Docker Compose
docker compose up              # preview server
docker compose run test        # run tests
docker compose run validate    # validate generators
```

## Next Steps

- **Read the architecture docs**: `docs/00-platform/` directory
- **Explore the shell design**: `source/nyhal-linux-backend/tests/fixtures/nstudio/desktop.nstudio`
- **Check the benchmark results**: `tests/benchmarks_all.py`
- **Review the ROADMAP**: all items are implemented

## Troubleshooting

### PIL not found
```bash
pip install Pillow
```

### SDL2 not found
```bash
pip install pysdl2 pysdl2-dll
```

### Fonts missing (text renders as boxes)
```bash
# Debian/Ubuntu
apt install fonts-dejavu-core

# Or use the bitmap fallback (automatic)
```

### Port 8080 in use
```bash
python3 tools/preview_server.py 9090
```
