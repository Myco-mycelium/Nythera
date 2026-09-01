---
title: Wayland Display Server Integration
document_id: TUTORIAL-WAYLAND
version: 1.0.0
status: Accepted
owners: [Nyrqis Engineering]
created: 2026-09-01
updated: 2026-09-01
depends_on: [ADR-0026, ADR-0020]
---

# Wayland Display Server Integration

This tutorial walks through connecting the Nyrqis shell to a Wayland
compositor, rendering frames to real hardware, and handling compositor
events (resize, close, input).

## Prerequisites

- A running Wayland compositor (sway, weston, GNOME on Wayland, etc.)
- The Nyrqis Linux backend built (`cargo build --release` in `rust/wayland/`)
- `libwayland-dev` installed (for runtime loading)

## Quick Start

### 1. Connect to the Wayland display

```python
from ui.desktop_session import DesktopSession
from ui.nstudio import load

# Load a .nstudio document
doc = load("path/to/shell.nstudio")

# Create a desktop session
session = DesktopSession(doc)

# Connect to the Wayland display
if session.connect_wayland():
    print("Connected to Wayland!")
else:
    print("No Wayland compositor — using PIL fallback")
```

### 2. Create a surface

```python
# The WaylandDisplay is accessible via the session
display = session.wayland_display

# Create a surface with a window title
surface_id = display.create_surface(title="My Nyrqis Shell")
print(f"Surface created: {surface_id}")
```

### 3. Render frames

```python
# Render the session and submit to Wayland
img = session.live_render()  # PIL Image
display.render_frame(img)    # Submit to Wayland surface
```

### 4. Run the event loop

```python
# The event loop polls Wayland, dispatches events, and renders
session.run_event_loop(duration=10.0, fps=60)
```

## Architecture Overview

```
DesktopSession
    │
    ├── connect_wayland()
    │     └── WaylandDisplay.open()
    │           └── wayland_codec.connect() → wl_display
    │
    ├── run_event_loop(duration, fps)
    │     │
    │     ├── WaylandDisplay.poll_and_dispatch()
    │     │     ├── select(display_fd) → check for events
    │     │     └── wayland_codec.dispatch_events()
    │     │
    │     ├── live_render() → PIL Image
    │     │     └── Compositor.render_screen()
    │     │
    │     └── WaylandDisplay.render_frame(img)
    │           ├── img.tobytes() → ARGB8888 pixels
    │           └── wayland_codec.submit_buffer()
    │                 ├── memfd_create("wayland-shm")
    │                 ├── mmap(fd) → shared memory
    │                 ├── copy pixel data
    │                 ├── wl_shm.create_pool(fd, size)
    │                 ├── wl_shm_pool.create_buffer(offset, w, h, stride, format)
    │                 ├── wl_surface.attach(buffer, 0, 0)
    │                 ├── wl_surface.damage_buffer(0, 0, w, h)
    │                 └── wl_surface.commit()
    │
    └── _setup_wayland_events()
          ├── on_configure → resize window
          ├── on_close → close window
          ├── on_key → KeyEvent
          └── on_pointer → MouseEvent
```

## WaylandDisplay API

The `WaylandDisplay` class wraps the low-level FFI functions:

```python
from ui.wayland_display import WaylandDisplay

display = WaylandDisplay(display_name="wayland-0")

# Connection
display.open()              # Connect to compositor
display.close()             # Disconnect

# Surface management
surf_id = display.create_surface(title="Window")
display.set_title(surf_id, "New Title")
display.destroy_surface(surf_id)

# Rendering
display.render_frame(pil_image)          # Submit a frame
display.render_and_submit(pil_image)     # Submit + dispatch events

# Event polling
display.poll_and_dispatch(timeout_s=0.016)  # Poll for events

# Event callbacks
display.on_configure(lambda e: print(f"Resize: {e.width}x{e.height}"))
display.on_close(lambda e: print("Window closed"))
display.on_key(lambda e: print(f"Key {e.key} state={e.state}"))
display.on_pointer(lambda e: print(f"Pointer at ({e.x}, {e.y})"))

# Diagnostics
display.connected   # bool
display.available   # bool
display.fd          # int (display fd for external polling)
display.summary()   # dict
```

## Event Types

| Event | Trigger | Data |
|-------|---------|------|
| `WaylandConfigureEvent` | Compositor resizes surface | `surface_id`, `width`, `height` |
| `WaylandCloseEvent` | Compositor requests close | `surface_id` |
| `WaylandKeyEvent` | Keyboard input | `key`, `state` (0=pressed, 1=released) |
| `WaylandPointerEvent` | Pointer input | `x`, `y`, `button`, `state` |

## Fallback Behavior

When no Wayland compositor is available (headless CI, SSH, etc.):

1. `session.connect_wayland()` returns `False`
2. `session.has_wayland` is `False`
3. All rendering falls back to PIL Compositor
4. `session.run_event_loop()` ticks without Wayland events
5. `session.render_to_file("output.png")` still works

This means the same code works in both environments without changes.

## The SHM Buffer Pipeline

When `submit_buffer()` is called, the Rust crate:

1. **Creates a memfd** — `memfd_create("wayland-shm", MFD_CLOEXEC)`
2. **Sets the size** — `ftruncate(fd, width * height * 4)`
3. **Maps it** — `mmap(fd, PROT_READ | PROT_WRITE, MAP_SHARED)`
4. **Copies pixels** — `memcpy(pool_ptr, pixel_data, len)`
5. **Creates a pool** — `wl_shm.create_pool(fd, size)`
6. **Creates a buffer** — `wl_shm_pool.create_buffer(0, w, h, stride, ARGB8888)`
7. **Attaches to surface** — `wl_surface.attach(buffer, 0, 0)`
8. **Damages the region** — `wl_surface.damage_buffer(0, 0, w, h)`
9. **Commits** — `wl_surface.commit()`
10. **Cleans up** — `munmap` + `close(fd)` (compositor holds its own fd reference)

## xdg-shell Integration

When `create_surface(use_xdg=True)` is called:

1. Binds `xdg_wm_base` global via `wl_registry`
2. Creates `xdg_surface` via `xdg_wm_base.get_xdg_surface(surface)`
3. Creates `xdg_toplevel` via `xdg_surface.get_toplevel()`
4. Sets title and app_id
5. Commits the surface to map the toplevel

The compositor responds with a `configure` event specifying the
initial size. The DesktopSession handles this via the
`on_configure` callback.

## CI Testing

The `rust-wayland` CI job builds the crate and runs 17 unit tests.
The tests verify:

- ABI version returns correctly
- Error handling for invalid inputs
- `memfd_create` and `mmap` work on Linux
- Connection fails gracefully without a compositor
- Surface/buffer lifecycle management

For full integration testing, a Wayland compositor (e.g. `weston`)
would be needed in the CI environment — this is deferred to Phase 3.

## References

- [ADR-0026](../reference/adr/ADR-0026-wayland-display-server-integration.md) — full decision record
- [ADR-0020](../reference/adr/ADR-0020-implementation-languages.md) — language matrix
- [Contributor Setup](contributor-setup.md) — development environment
- [Backend Quickstart](linux-backend-quickstart.md) — daemon and containers
