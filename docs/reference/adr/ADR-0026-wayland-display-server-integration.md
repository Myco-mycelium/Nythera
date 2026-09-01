---
title: Wayland Display Server Integration for the Nyrqis Shell
document_id: ADR-0026
version: 0.1.0
status: Draft
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
to a Wayland compositor. This ADR proposes the integration strategy.

## Decision (Proposed)

Implement Wayland display server integration as a **Rust crate**
(`rust/wayland/`) that provides:

1. **Wayland client protocol bindings** — thin wrappers around
   `libwayland-client` for `wl_display`, `wl_surface`, `wl_seat`,
   `wl_shm` (shared-memory buffers), and `xdg_surface`/`xdg_toplevel`
   (window management).

2. **Surface management** — a `WaylandSurface` type that owns a
   `wl_surface` and manages its lifecycle (create, attach buffer,
   commit, destroy). The existing PIL/SDL2 renderers produce pixel
   buffers; the Wayland crate maps them onto surfaces.

3. **Input handling** — `wl_seat` listener that dispatches
   `wl_keyboard` and `wl_pointer` events to the `DesktopSession`'s
   input router. Key repeat, pointer motion, and button events are
   forwarded through the same event pipeline the software session uses.

4. **Buffer allocation** — `wl_shm` for software rendering (the
   initial path) with a documented follow-on for GBM/DRM atomic
   modesetting when GPU acceleration is needed.

### Architecture

```
  .nstudio document
        │
        ▼
  NyrqisRuntime (state, events, bindings)
        │
        ▼
  DesktopSession (window stack, input routing)
        │
        ├── Compositor (PIL render → pixel buffer)
        │         │
        │         ▼
        │   WaylandSurface (wl_surface + wl_shm buffer)
        │         │
        │         ▼
        │   wl_display.flush()
        │
        └── InputRouter ← wl_seat events
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

## Implementation Plan

### Phase 1: Core Wayland client (crate + Python loader)
- `rust/wayland/` — core protocol bindings (wl_display, wl_surface,
  wl_shm, wl_seat, xdg_surface, xdg_toplevel)
- `ui/wayland_codec.py` — FFI loader (same pattern as other crates)
- Basic surface creation and buffer submission

### Phase 2: DesktopSession integration
- Wire `DesktopSession` to use `WaylandSurface` for rendering
- Forward `wl_seat` events to `InputRouter`
- Multi-monitor support via `wl_output`

### Phase 3: GPU acceleration (follow-on)
- GBM buffer allocation for hardware-accelerated rendering
- DRM atomic modesetting for direct scanout
- EGL integration for OpenGL rendering

### Phase 4: Custom compositor (follow-on)
- Run as a Wayland compositor instead of a client
- Full control over window management, input, and display pipeline
- Layer-shell protocol for shell surfaces (taskbar, desktop, etc.)
