---
title: Wayland Display Server Integration for the Nyrqis Shell
document_id: ADR-0026
version: 1.0.0
status: Accepted
owners: [Nyrqis Architecture]
created: 2026-09-01
updated: 2026-09-01
ai_assisted: true
depends_on: [ADR-0016, ADR-0020, ADR-0021, ADR-0025, NPS-017]
---

# ADR-0026 — Wayland Display Server Integration for the Nyrqis Shell

## Context

The Nyrqis Linux Backend is substantially complete: all five NPS-017 §4
requirements are implemented, 2,500 tests pass, and the NUI runtime can
import, validate, and render `.nstudio` documents. The `DesktopSession`
(`ui/desktop_session.py`) manages windows, input, and rendering in
software, but it is not connected to a real display server.

For the Nyrqis shell to render on actual hardware, it needs to connect
to a Wayland compositor. This ADR specifies the integration strategy.

## Decision

Implement Wayland display server integration as a **Rust crate**
(`rust/wayland/`) that provides:

1. **Wayland client protocol bindings** — raw FFI wrappers around
   `libwayland-client` via `wayland-sys` (0.31) for `wl_display`,
   `wl_surface`, `wl_seat`, `wl_shm` (shared-memory buffers), and
   `xdg_surface`/`xdg_toplevel` (window management).

2. **Surface management** — the `WaylandDisplay` Python class
   (`ui/wayland_display.py`) owns surfaces and manages their lifecycle
   (create, attach buffer, commit, destroy). The existing PIL
   compositor produces pixel buffers; the WaylandDisplay maps them
   onto surfaces via `wl_shm`.

3. **Input handling** — `wl_seat` binding with `get_keyboard`/
   `get_pointer`; event handler callback registration via
   `nyrqis_wayland_set_event_handler()`. The DesktopSession translates
   Wayland events into its existing `MouseEvent`/`KeyEvent` types.

4. **Buffer allocation** — `wl_shm` for software rendering (Phase 1b):
   `memfd_create` → `mmap` → `wl_shm_pool` → `wl_buffer` →
   `wl_surface.attach` + `damage_buffer` + `commit`.

5. **Event loop integration** — `DesktopSession.run_event_loop()` polls
   the Wayland display fd via `select()`, dispatches compositor events
   (configure, close, keyboard, pointer), and renders frames each tick.

### Architecture

```
  .nstudio document
        │
        ▼
  NyrqisRuntime (state, events, bindings)
        │
        ▼
  DesktopSession (window stack, input routing, event loop)
        │
        ├── live_render() → PIL Image
        │         │
        │         ▼
        │   WaylandDisplay.render_frame()
        │     ├── convert to ARGB8888
        │     ├── submit_buffer() → memfd + wl_shm_pool + wl_buffer
        │     ├── wl_surface.attach + damage_buffer + commit
        │     └── poll_and_dispatch() → select() on display fd
        │
        ├── _setup_wayland_events()
        │     ├── configure → resize focused window
        │     ├── close → close focused window
        │     ├── pointer → MouseEvent
        │     └── key → KeyEvent
        │
        └── Fallback: PIL Compositor (software rendering)
```

### Why Wayland (not X11)

- Wayland is the modern display protocol for Linux; X11 is legacy.
- Wayland's security model (each client sees only its own surfaces)
  aligns with Nyrqis's capability-based isolation.
- XWayland provides backward compatibility for X11 apps when needed.
- The Nyrqis shell is a compositor itself (it manages windows), so
  it would either run as a Wayland compositor or connect to one. This
  ADR proposes connecting to an existing compositor (weston, sway)
  for the initial implementation, with a custom compositor as a
  follow-on.

### Why Rust (not Python)

- The Wayland event loop is latency-sensitive (input delivery,
  frame callbacks).
- The Rust crate stays below the platform boundary per ADR-0020.
- The Python `DesktopSession` remains the reference implementation;
  the Rust crate is the shipped form.

## Alternatives Considered

- **X11 directly** — rejected as the primary path; X11 is legacy and
  its security model doesn't align with capability-based isolation.
  XWayland provides backward compatibility when needed.

- **Custom Wayland compositor** — deferred to a follow-on ADR. Running
  as a compositor gives full control over the display pipeline but
  requires implementing the full Wayland protocol (shell surfaces,
  output management, layer-shell, etc.). The initial implementation
  connects to an existing compositor.

- **SDL2 for display** — the existing `SDLCompositor` renders to SDL2
  windows, which work on both X11 and Wayland via SDL2's backend
  abstraction. However, SDL2 doesn't give the fine-grained control
  needed for a desktop shell (window decoration, input routing,
  multi-monitor). The Wayland crate is the long-term path; SDL2
  remains the quick-preview path.

- **Framebuffer (/dev/fb0)** — too limited for a modern desktop shell;
  no multi-monitor, no hardware acceleration, no window compositing.

## Consequences

### Positive
- The Nyrqis shell renders on real hardware via Wayland.
- Input events flow through the same pipeline as the software session.
- The Rust crate stays below the platform boundary (ADR-0020).
- Multi-monitor support comes naturally from Wayland's output protocol.
- The event loop integration means the session auto-renders at the
  target FPS without manual frame submission.

### Negative
- Adds a dependency on `libwayland-client` (the crate wraps it via
  FFI, similar to the existing seccomp/transport crates).
- The initial implementation is limited to software rendering
  (`wl_shm`); GPU acceleration is a follow-on.
- Running as a Wayland client (not compositor) means the shell is
  subject to the host compositor's window management policy.

### Risks
- Wayland protocol versioning: the crate must handle protocol
  extensions gracefully (older compositors may not support all
  extensions). The initial implementation uses only core Wayland +
  xdg-shell, which are universally available.
- Input handling complexity: keyboard layout management, pointer
  acceleration, and multi-seat support are non-trivial. The initial
  implementation handles basic keyboard and pointer events; advanced
  input is a follow-on.

## Implementation History

### Phase 1: Core Wayland client (2026-09-01)
**Status: COMPLETE** ✅

- `rust/wayland/` — core protocol bindings (wl_display, wl_surface,
  wl_shm, wl_seat, xdg_surface, xdg_toplevel)
- `ui/wayland_codec.py` — FFI loader (same pattern as other crates)
- Real `wl_display_connect` via `wayland-sys` raw FFI
- `wl_compositor` binding via `wl_registry` global enumeration
- `wl_surface` creation via `wl_compositor.create_surface`
- `poll()` + `wl_display_dispatch` for event loop
- 12 unit tests

### Phase 1b: SHM buffer submission + xdg-shell + input (2026-09-01)
**Status: COMPLETE** ✅

- SHM buffer submission: `memfd_create` → `mmap` → `wl_shm_pool` →
  `wl_buffer` → `wl_surface.attach` + `damage_buffer` + `commit`
- xdg-shell: bind `xdg_wm_base`, create `xdg_surface` + `xdg_toplevel`,
  `set_title`/`set_app_id`, surface commit for mapping
- Input: `wl_seat` binding with `get_keyboard`/`get_pointer`, event
  handler callback registration
- New FFI functions: `set_title`, `get_fd`, `set_event_handler`
- ABI bumped to 1.1.0 (0x0001_0100)
- 17 unit tests

### Phase 2: DesktopSession integration (2026-09-01)
**Status: COMPLETE** ✅

- `WaylandDisplay` wrapper class (`ui/wayland_display.py`)
- Event callback registration: `on_configure`, `on_close`, `on_key`,
  `on_pointer`
- `poll_and_dispatch()` for select()-based fd polling
- `render_frame()` with PIL Image → ARGB8888 conversion
- `DesktopSession.connect_wayland()` / `render_to_wayland()` /
  `has_wayland`
- `run_event_loop()` polls Wayland fd, dispatches events, renders frames
- `_setup_wayland_events()` translates compositor events to session
  actions (resize, close, input)
- 2,500/2,500 tests pass

### Phase 3: GPU acceleration (follow-on)
- GBM buffer allocation for hardware-accelerated rendering
- DRM atomic modesetting for direct scanout
- EGL integration for OpenGL rendering

### Phase 4: Custom compositor (follow-on)
- Run as a Wayland compositor instead of a client
- Full control over window management, input, and display pipeline
- Layer-shell protocol for shell surfaces (taskbar, desktop, etc.)

## FFI Surface (ABI 1.1.0)

| Function | Description |
|----------|-------------|
| `nyrqis_wayland_version() -> u32` | ABI version (0x0001_0100) |
| `nyrqis_wayland_connect(name, len) -> i32` | Connect to display |
| `nyrqis_wayland_create_surface(conn, xdg, title, len) -> i32` | Create surface with optional xdg-shell |
| `nyrqis_wayland_submit_buffer(surf, pixels, len, w, h, stride) -> i32` | Submit SHM buffer |
| `nyrqis_wayland_dispatch_events(conn, timeout) -> i32` | Poll + dispatch events |
| `nyrqis_wayland_disconnect(conn) -> i32` | Disconnect |
| `nyrqis_wayland_destroy_surface(surf) -> i32` | Destroy surface |
| `nyrqis_wayland_set_title(surf, title, len) -> i32` | Set xdg_toplevel title |
| `nyrqis_wayland_get_fd(conn) -> i32` | Get display fd for external polling |
| `nyrqis_wayland_set_event_handler(fn) -> ()` | Register event callback |
| `nyrqis_wayland_last_error(buf, cap) -> i32` | Last error message |
