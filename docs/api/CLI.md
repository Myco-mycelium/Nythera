# Nyrqis CLI Tools

Complete reference for all command-line tools in the Nyrqis platform.

## Code Generators

### generate_rust.py

Generate a Rust module from a `.nstudio` file.

```bash
python3 tools/generate_rust.py <input.nstudio> [output.rs]
```

**Arguments:**
- `input.nstudio` — path to the NUI document (required)
- `output.rs` — output file path (optional, prints to stdout if omitted)

**Output:** Rust module with `Layout`, `Component`, `Screen`, `Document` structs, per-screen factory functions, `load()` constructor, and `state` module.

**Example:**
```bash
python3 tools/generate_rust.py desktop.nstudio /tmp/desktop.rs
# Generated /tmp/desktop.rs (766 lines)
```

### generate_cpp.py

Generate a C++ header from a `.nstudio` file.

```bash
python3 tools/generate_cpp.py <input.nstudio> [output.hpp]
```

**Arguments:**
- `input.nstudio` — path to the NUI document (required)
- `output.hpp` — output file path (optional)

**Output:** C++ header with `Layout`, `Property`, `Component`, `Screen`, `Document` structs in `nyrqis::nui` namespace. Uses C++20 designated initializers.

**Example:**
```bash
python3 tools/generate_cpp.py desktop.nstudio /tmp/desktop.hpp
# Generated /tmp/desktop.hpp (777 lines)
```

### generate_python.py

Generate a Python module from a `.nstudio` file.

```bash
python3 tools/generate_python.py <input.nstudio> [output.py]
```

**Arguments:**
- `input.nstudio` — path to the NUI document (required)
- `output.py` — output file path (optional)

**Output:** Python module with `Layout` (frozen), `Component`, `Screen`, `Document`, `State` dataclasses. Generated module is importable.

**Example:**
```bash
python3 tools/generate_python.py desktop.nstudio /tmp/desktop.py
# Generated /tmp/desktop.py (774 lines)

python3 -c "import sys; sys.path.insert(0, '/tmp'); import desktop; doc = desktop.load(); print(f'{doc.name} v{doc.version}')"
# Nyrqis Desktop Shell v1.0.0
```

## Rendering Tools

### render.py

Render `.nstudio` files to PNG images.

```bash
python3 tools/render.py <file.nstudio> [options]
```

**Options:**
| Flag | Description | Default |
|------|-------------|---------|
| `--backend` | Rendering backend: `pil`, `sdl`, `both` | `pil` |
| `--theme` | Theme: `Eclipse`, `Solar` | `Eclipse` |
| `--scale` | Scale factor (1.0 = native, 2.0 = retina) | `1.0` |
| `--screen` | Render only this screen ID | all |
| `--output`, `-o` | Output directory | `./render_output/` |
| `--compare` | Render with both backends, show timing | off |
| `--list-screens` | List screens without rendering | off |

**Examples:**
```bash
# Render all screens
python3 tools/render.py desktop.nstudio

# Solar theme, 2x scale
python3 tools/render.py desktop.nstudio --theme Solar --scale 2.0

# Compare PIL vs SDL2 performance
python3 tools/render.py desktop.nstudio --compare

# Inspect without rendering
python3 tools/render.py desktop.nstudio --list-screens

# Render all fixtures (batch mode)
python3 tools/render.py
```

**Output:**
```
Backend: PIL
  desktop: 1440x900 (26,037 bytes, 14.2ms)
  lock: 1440x900 (8,174 bytes, 6.8ms)
  Output: ./render_output/desktop/
```

### preview_server.py

HTTP server for live shell design viewing.

```bash
python3 tools/preview_server.py [port] [options]
```

**Options:**
| Flag | Description | Default |
|------|-------------|---------|
| `--file`, `-f` | `.nstudio` file to serve | auto-detect |
| `--theme` | Theme: `Eclipse`, `Solar` | `Eclipse` |
| `--scale` | Scale factor | `1.0` |

**Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | HTML page with screen selector |
| `/render/<screen_id>` | GET | PNG image of the screen |
| `/render` | GET | PNG of the first screen |
| `/api/info` | GET | JSON document metadata |
| `/api/screens` | GET | JSON screen list |
| `/api/state` | GET | JSON runtime state |
| `/api/changes` | GET | Poll for file changes (counter) |

**Features:**
- Auto-reload on file changes (via watchdog)
- Browser polls `/api/changes` every 2 seconds
- Theme toggle in the HTML UI

**Example:**
```bash
python3 tools/preview_server.py 8080 --file desktop.nstudio
# Preview server running at http://localhost:8080
```

## Validation Tools

### validate_generators.py

Validate all code generators and compositor on every `.nstudio` fixture.

```bash
python3 tools/validate_generators.py [--verbose]
```

**Options:**
| Flag | Description |
|------|-------------|
| `--verbose`, `-v` | Show per-fixture results |

**Checks:**
- Rust generator: non-empty output, correct header, `load()` function
- C++ generator: non-empty output, `#pragma once`, namespace, balanced braces
- Python generator: non-empty output, syntax valid, importable, `load()` callable
- PIL compositor: renders all screens without errors

**Output:**
```
Results: 36/36 passed, 0 failed
Fixtures: 9
Generators: 3 + PIL compositor

All generators valid.
```

**Exit codes:**
- `0` — all generators pass
- `1` — one or more generators failed

### benchmarks_all.py

Benchmark all compositor backends and generators.

```bash
python3 tests/benchmarks_all.py [--repeat N]
```

**Options:**
| Flag | Description | Default |
|------|-------------|---------|
| `--repeat` | Repeat count for timing | `3` |

**Output:** Tab-separated results suitable for `BENCHMARK_RESULTS.md`.

```
fixture     screens  components  behaviors  PIL Eclipse 1x  SDL2 headless  Rust gen  C++ gen  Python gen
desktop.nstudio  2     37         11         14.2ms          26.1ms          0.6ms     0.6ms    0.6ms
```

## Testing Tools

### test_backend.py

Run the full backend test suite.

```bash
cd source/nyhal-linux-backend && python3 -B test_backend.py
```

**Output:** `Ran 692 tests in Xs — OK`

### Unit tests

Run specific test modules:

```bash
# Compositor tests
python3 -m unittest tests.test_compositor        # 22 tests
python3 -m unittest tests.test_compositor_sdl    # 17 tests

# Generator tests
python3 -m unittest tests.test_generate_rust     # 16 tests
python3 -m unittest tests.test_generate_cpp      # 19 tests
python3 -m unittest tests.test_generate_python   # 19 tests

# Shell tests
python3 -m unittest tests.test_shell             # 19 tests
```

## Docker Commands

```bash
# Build
docker build -t nyrqis .

# Run preview server
docker run -p 8080:8080 nyrqis

# Run with custom file
docker run -p 8080:8080 -v ./mydesign.nstudio:/app/mydesign.nstudio \
    nyrqis --file /app/mydesign.nstudio

# Run tests
docker run --rm nyrqis -c "cd /app/source/nyhal-linux-backend && python3 -B test_backend.py"

# Docker Compose
docker compose up              # preview server
docker compose run test        # run tests
docker compose run validate    # validate generators
```
