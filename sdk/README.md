# Nyrqis SDK

Developer tools for building Nyrqis applications.

## Overview

The Nyrqis SDK provides everything you need to develop, test, and deploy
applications for the Nyrqis operating system.

## Quick Start

```bash
# Create a new application
nyq new my-app

# Create a shell design
nyq new my-shell -t shell

# Create a Rust library
nyq new my-lib -t rust

# Build and test
nyq build
nyq test

# Preview shell designs
nyq preview
```

## Modules

| Module | Purpose |
|--------|---------|
| `scaffold` | Project template generator |
| `cli` | Command-line interface (nyq command) |
| `hotreload` | File watchers for shell designs |
| `telemetry` | Opt-in crash reporting and metrics |
| `performance` | Timer, MemoryTracker, FrameRateMonitor |
| `restore` | System restore points |

## Package Manager

```bash
# List packages
nyq pkg list
nyq pkg list --installed
nyq pkg list --updatable

# Search packages
nyq pkg search firefox

# Install/remove/update
nyq pkg install gimp
nyq pkg remove gimp
nyq pkg update          # Update all
nyq pkg update rust     # Update specific

# Package info and stats
nyq pkg info rust
nyq pkg stats
```

## Hot Reload

```python
from nyrqis_sdk.hotreload import HotReloader, DirectoryReloader

# Watch a single file
reloader = HotReloader("design.nstudio")
reloader.on_change(lambda path: print(f"Changed: {path}"))
reloader.start()

# Watch a directory
watcher = DirectoryReloader("./designs")
watcher.on_change(lambda path: reload_design(path))
watcher.start()
```

## Telemetry

```python
from nyrqis_sdk.telemetry import Telemetry

telemetry = Telemetry()
telemetry.enable()  # Opt-in required

# Record metrics
telemetry.metric("startup_time_ms", 1234)
telemetry.metric("memory_mb", 512, tags={"component": "shell"})

# Report errors (no PII)
telemetry.error("render_failure", context={"screen": "desktop"})
```

## Performance Monitoring

```python
from nyrqis_sdk.performance import Timer, MemoryTracker, FrameRateMonitor

# Time operations
with Timer("my_operation"):
    do_something()

# Track memory
tracker = MemoryTracker()
tracker.snapshot("before")
allocate_memory()
tracker.snapshot("after")
print(f"Delta: {tracker.delta('before', 'after') / 1024 / 1024:.1f} MB")

# Monitor frame rate
monitor = FrameRateMonitor()
while running:
    render_frame()
    monitor.tick()
print(f"FPS: {monitor.fps}")
```

## System Restore Points

```python
from nyrqis_sdk.restore import RestoreManager

manager = RestoreManager()

# Create restore point
point = manager.create("Before major change")

# List restore points
for p in manager.list_points():
    print(f"{p.id}: {p.description} ({p.age_hours:.1f}h old)")

# Restore if needed
manager.restore(point.id)
```

## Language Strategy

Per ADR-0020, the SDK uses:

- **Python**: Primary developer SDK (this package)
- **Rust**: Core implementation (behind FFI boundary)
- **C++**: Platform services
- **C#**: Secondary bindings (future)

## Testing

```bash
# Run SDK tests
python3 -m unittest discover -s tests

# Run all backend tests
python3 -B test_backend.py
```

## References

- [ADR-0020](../docs/reference/adr/ADR-0020-implementation-languages.md): Implementation languages
- [BUILD-001](../docs/reference/build/BUILD_ARCHITECTURE.md): Build architecture
- [TUT-003](../docs/tutorials/developer-onboarding.md): Developer onboarding
- [NPC-010](../docs/00-platform/008-GOVERNANCE_EXPANSION.md): Governance expansion
