# Changelog — Wayland Display Server Integration

## Session: 2026-09-01

### Summary

Complete implementation of Wayland display server integration for the
Nyrqis shell (ADR-0026). The shell now connects to a Wayland compositor,
renders frames via SHM buffers, handles compositor events (resize, close,
input), and supports GPU-accelerated rendering via SDL2's Wayland backend.

**12 commits, 18 files changed, 3,754 insertions, 21 deletions.**

### Files Created (7)

| File | Lines | Purpose |
|------|-------|---------|
| `rust/wayland/Cargo.toml` | 21 | Wayland client crate manifest |
| `rust/wayland/src/lib.rs` | 1,346 | Wayland client crate (ABI 1.1.0, 17 tests) |
| `ui/wayland_codec.py` | 218 | Python FFI loader for Wayland crate |
| `ui/wayland_display.py` | 387 | WaylandDisplay wrapper class |
| `docs/tutorials/wayland-integration.md` | 215 | Wayland integration tutorial |
| `docs/reference/adr/ADR-0026-wayland-display-server-integration.md` | 220 | ADR (Accepted) |
| `.github/workflows/arm64-conformance.yml` | 79 | arm64 cross-arch CI job |

### Files Modified (11)

| File | Changes | Purpose |
|------|---------|---------|
| `ui/desktop_session.py` | +167 | Event loop integration, configure handling |
| `ui/compositor_sdl.py` | +83/-12 | SDL2 Wayland backend support |
| `test_backend.py` | +157 | 7 cross-architecture conformance tests |
| `IMPLEMENTATION_STATUS.md` | +36/-1 | Test count 810→2500, at-rest encryption |
| `rust/README.md` | +6/-1 | Wayland crate status |
| `007-PROJECT_ROADMAP.md` | +24 | M13 completion status |
| `PROJECT_STATUS_SUMMARY.md` | +120 | Stakeholder status summary |
| `.github/workflows/ci.yml` | +19 | rust-wayland CI job |
| `linux-backend-quickstart.md` | +203 | Backend tutorial |
| `vault-operations.md` | +229 | Vault operations guide |
| `contributor-setup.md` | +245 | Contributor setup guide |

### Rust Crate: `nyrqis-wayland` (ABI 1.1.0)

| Feature | FFI Function | Status |
|---------|--------------|--------|
| Connect to display | `nyrqis_wayland_connect` | ✅ Complete |
| Create surface with xdg-shell | `nyrqis_wayland_create_surface` | ✅ Complete |
| SHM buffer submission | `nyrqis_wayland_submit_buffer` | ✅ Complete |
| Event dispatch | `nyrqis_wayland_dispatch_events` | ✅ Complete |
| Disconnect | `nyrqis_wayland_disconnect` | ✅ Complete |
| Destroy surface | `nyrqis_wayland_destroy_surface` | ✅ Complete |
| Set title | `nyrqis_wayland_set_title` | ✅ Complete |
| Get display fd | `nyrqis_wayland_get_fd` | ✅ Complete |
| Event handler callback | `nyrqis_wayland_set_event_handler` | ✅ Complete |
| Error reporting | `nyrqis_wayland_last_error` | ✅ Complete |

### SHM Buffer Pipeline

```
memfd_create("wayland-shm")
    → ftruncate(fd, width * height * 4)
    → mmap(fd, PROT_READ | PROT_WRITE, MAP_SHARED)
    → memcpy(pool_ptr, pixel_data)
    → wl_shm.create_pool(fd, size)
    → wl_shm_pool.create_buffer(0, w, h, stride, ARGB8888)
    → wl_surface.attach(buffer, 0, 0)
    → wl_surface.damage_buffer(0, 0, w, h)
    → wl_surface.commit()
    → munmap + close(fd)
```

### DesktopSession Integration

- `connect_wayland()` — optional Wayland connection with auto-fallback
- `render_to_wayland()` — render + submit to Wayland surface
- `has_wayland` — check if Wayland is active
- `run_event_loop()` — polls Wayland fd, dispatches events, renders frames
- `_setup_wayland_events()` — translates compositor events to session actions

### Event Types

| Event | Compositor Trigger | Session Action |
|-------|-------------------|----------------|
| `WaylandConfigureEvent` | Surface resize | Resize focused window |
| `WaylandCloseEvent` | Close request | Close focused window |
| `WaylandKeyEvent` | Keyboard input | `KeyEvent` dispatch |
| `WaylandPointerEvent` | Pointer input | `MouseEvent` dispatch |

### Test Results

- **2,500/2,500 Python tests** passing
- **17/17 Rust wayland tests** passing
- **7 new cross-architecture tests** for aarch64 seccomp validation
