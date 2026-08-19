# Contributing to Nyrqis

Thank you for your interest in contributing to Nyrqis! This guide will
help you get started.

## Quick Start

```bash
# Clone the repo
git clone https://github.com/Myco-mycelium/Nythera.git
cd Nythera

# Install dependencies
pip install Pillow pysdl2 pysdl2-dll pynacl numpy watchdog pyyaml

# Run the tests
cd source/nyhal-linux-backend && python3 -B test_backend.py

# Start the preview server
python3 tools/preview_server.py
```

## Project Structure

```
Nyrqis/
├── docs/                          # Documentation
│   ├── 00-platform/               # Architecture and platform docs
│   ├── api/                       # API reference (CLI, IPC)
│   └── getting-started.md         # Tutorial
├── source/nyhal-linux-backend/    # Main backend source
│   ├── backend/                   # Core backend modules
│   ├── ui/                        # UI runtime, compositor, NUI
│   ├── ipc/                       # IPC transport and services
│   ├── rust/                      # Rust crate sources
│   └── tests/                     # Test files and fixtures
├── tools/                         # CLI tools
│   ├── generate_rust.py           # NUI → Rust generator
│   ├── generate_cpp.py            # NUI → C++ generator
│   ├── generate_python.py         # NUI → Python generator
│   ├── render.py                  # .nstudio → PNG renderer
│   ├── preview_server.py          # HTTP preview server
│   └── validate_generators.py     # CI validation
├── tests/                         # Root-level tests
├── Dockerfile                     # Docker build
├── docker-compose.yml             # Docker Compose
└── CHANGELOG_LINUX_BACKEND.md     # Changelog
```

## Development Workflow

### 1. Create a branch

```bash
git checkout -b feature/my-feature
```

### 2. Make your changes

Follow the existing code style:
- Python: PEP 8, type hints, docstrings
- Tests: unittest, descriptive names
- Docs: Markdown, include examples

### 3. Run the tests

```bash
# Full backend tests (692 tests)
cd source/nyhal-linux-backend && python3 -B test_backend.py

# Or run specific test modules
python3 -m unittest tests.test_compositor
python3 -m unittest tests.test_shell
python3 -m unittest tests.test_generate_cpp

# Validate all generators
python3 tools/validate_generators.py --verbose
```

### 4. Update documentation

If you add new features:
- Update `CHANGELOG_LINUX_BACKEND.md`
- Update `docs/00-platform/PROJECT_STATUS.md`
- Add API docs in `docs/api/` if applicable

### 5. Commit and push

```bash
git add -A
git commit -m "feat: description of your change"
git push origin feature/my-feature
```

## Code Style

### Python

- Use type hints for all public functions
- Write docstrings for classes and public methods
- Keep functions under 50 lines when possible
- Use descriptive variable names

```python
def render_screen(
    self,
    document: NstudioDocument,
    screen_id: Optional[str] = None,
) -> Image.Image:
    """Render a screen from a NstudioDocument to a PIL Image.

    Parameters
    ----------
    document : NstudioDocument
        The loaded NUI document.
    screen_id : str, optional
        Render only this screen. If None, renders the first screen.

    Returns
    -------
    PIL.Image.Image
        The rendered screen as an RGB image.
    """
```

### Tests

- Use `unittest.TestCase`
- Descriptive test names: `test_render_button_component`
- One assertion per test when practical
- Use fixtures from `tests/fixtures/nstudio/`

```python
class TestCompositorRender(unittest.TestCase):
    """Compositor renders screens to PIL images."""

    def test_render_button_component(self):
        btn = NstudioComponent(
            id="btn1", type="Button",
            layout={"x": 10, "y": 10, "width": 120, "height": 36},
            properties={"text": "Click Me"},
        )
        screen = _make_screen("s", 400, 300, root_children=[btn])
        doc = _make_doc(screens=[screen])

        comp = Compositor()
        img = comp.render_screen(doc)
        self.assertEqual(img.size, (400, 300))
```

### Documentation

- Use Markdown
- Include code examples
- Keep sections focused
- Link to related docs

## Architecture Principles

### Language Choices (ADR-0020)

| Layer | Primary | Secondary |
|-------|---------|-----------|
| NyHAL | C++ | C |
| NyCore | Rust | C++ |
| NyRuntime | Rust | C++ |
| NyUI | C++ + declarative | Rust |
| NySDK | Rust + C++ | C# bindings |
| Testing | Rust | Python |
| Build tools | Rust | Python |
| Developer tools | Rust | Python |

**Principle:** Platform-critical execution paths must not depend on
the Python interpreter.

### NUI Schema (v1.0.0)

The `.nstudio` format is the shared contract between Nyforge (editor)
and Nyrqis (runtime). Key concepts:
- Screens with component trees
- Behaviors with AND/OR logic graphs
- State scopes (global, screen, component, session, persistent)
- Localization, assets, animations

### IPC Transport

All services communicate over Unix datagrams with kernel-attached
SCM_CREDENTIALS authentication. See `docs/api/IPC.md` for details.

## Testing

### Test Categories

| Category | Count | Location |
|----------|-------|----------|
| Backend floor | 692 | `source/nyhal-linux-backend/test_backend.py` |
| Shell runner | 19 | `tests/test_shell.py` |
| PIL compositor | 22 | `tests/test_compositor.py` |
| SDL2 compositor | 17 | `tests/test_compositor_sdl.py` |
| Rust generator | 16 | `tests/test_generate_rust.py` |
| C++ generator | 19 | `tests/test_generate_cpp.py` |
| Python generator | 19 | `tests/test_generate_python.py` |
| **Total** | **1069** | |

### Running Specific Tests

```bash
# All compositor tests
python3 -m unittest tests.test_compositor tests.test_compositor_sdl

# All generator tests
python3 -m unittest tests.test_generate_rust tests.test_generate_cpp tests.test_generate_python

# Full validation
python3 tools/validate_generators.py --verbose
```

## Common Tasks

### Adding a new component type

1. Add the component to `ui/nstudio.py` (schema)
2. Add rendering in `ui/compositor.py` (PIL) and `ui/compositor_sdl.py` (SDL2)
3. Add to the component registry in Nyforge
4. Add tests for the new component
5. Update documentation

### Adding a new IPC operation

1. Add the operation handler in `ui/service.py`
2. Add to the op dispatch in `_on_call`
3. Add tests in `test_backend.py`
4. Update `docs/api/IPC.md`

### Adding a new code generator

1. Create `tools/generate_<target>.py`
2. Follow the pattern of existing generators
3. Add tests in `tests/test_generate_<target>.py`
4. Add to `tools/validate_generators.py`
5. Update `docs/api/CLI.md`

## Getting Help

- **Architecture docs:** `docs/00-platform/`
- **API reference:** `docs/api/`
- **Getting started:** `docs/getting-started.md`
- **Changelog:** `CHANGELOG_LINUX_BACKEND.md`
- **Issues:** https://github.com/Myco-mycelium/Nythera/issues

## License

By contributing, you agree that your contributions will be licensed
under the same license as the project.
