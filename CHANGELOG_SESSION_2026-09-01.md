# Changelog — Session 2026-09-01

## Summary

Complete implementation of Wayland display server integration, Ed25519
package signing, cross-architecture testing, and comprehensive documentation.

**20 commits, 23 files changed, +7,614 / -21 lines.**

## Test Results

| Suite | Tests | Status |
|-------|-------|--------|
| Python backend | 2,500 | ✅ All passing |
| Rust wayland crate | 17 | ✅ All passing |
| Package signing | 31 | ✅ All passing |
| **Total** | **2,548** | **✅ All passing** |

## Commits

| Commit | Description | Files |
|--------|-------------|-------|
| `eabf04c` | Cross-architecture conformance tests + arm64 CI | test_backend.py, ci.yml |
| `d7cdf87` | Backend quickstart tutorial + IMPLEMENTATION_STATUS cleanup | IMPLEMENTATION_STATUS.md, linux-backend-quickstart.md |
| `ffae72e` | Vault operations guide | vault-operations.md |
| `fa7a8b5` | Stakeholder-facing project status summary | PROJECT_STATUS_SUMMARY.md |
| `1b3884f` | ADR-0026 draft (Wayland integration) + roadmap update | ADR-0026, 007-PROJECT_ROADMAP.md |
| `699999f` | Scaffold Wayland client crate + contributor setup guide | rust/wayland/*, contributor-setup.md |
| `ea9f3c1` | Phase 1 — real Wayland connection + DesktopSession integration | lib.rs, wayland_codec.py, desktop_session.py |
| `4357423` | Phase 1b — SHM buffer submission, xdg-shell, input handling | lib.rs, wayland_codec.py, wayland_display.py |
| `7f50a00` | Event loop integration + configure handling | desktop_session.py, wayland_display.py |
| `827670d` | ADR-0026 accepted + Wayland tutorial | ADR-0026, wayland-integration.md |
| `21fe343` | Rust README update | rust/README.md |
| `cad18f3` | SDL2 Wayland backend + roadmap M13 update | compositor_sdl.py, 007-PROJECT_ROADMAP.md |
| `fceec48` | Changelog | CHANGELOG_WAYLAND.md |
| `554a439` | Multi-monitor wl_output support | lib.rs, wayland_codec.py, wayland_display.py |
| `503489f` | Fix arm64 CI job + output-to-monitor mapping | arm64-conformance.yml, desktop_session.py |
| `3420df1` | Ed25519 package signing + output mapping | package_signing.py, desktop_session.py |
| `b3db1dd` | Package signing tests + CLI tool | test_package_signing.py, nyrqisctl_sign.py |

## Files Created (12)

| File | Lines | Purpose |
|------|-------|---------|
| `rust/wayland/Cargo.toml` | 21 | Wayland client crate manifest |
| `rust/wayland/src/lib.rs` | 1,518 | Wayland client crate (ABI 1.2.0, 17 tests) |
| `ui/wayland_codec.py` | 259 | Python FFI loader for Wayland crate |
| `ui/wayland_display.py` | 398 | WaylandDisplay wrapper class |
| `backend/package_signing.py` | 385 | Ed25519 package signing (NPS-026 §6) |
| `nyrqisctl_sign.py` | 225 | Package signing CLI tool |
| `test_package_signing.py` | 242 | 31 unit tests for package signing |
| `docs/tutorials/wayland-integration.md` | 215 | Wayland integration tutorial |
| `docs/tutorials/contributor-setup.md` | 245 | Contributor setup guide |
| `docs/tutorials/linux-backend-quickstart.md` | 203 | Backend tutorial |
| `docs/reference/adr/ADR-0026-wayland-display-server-integration.md` | 220 | ADR (Accepted) |
| `.github/workflows/arm64-conformance.yml` | 77 | arm64 cross-arch CI job |

## Files Modified (11)

| File | Changes | Purpose |
|------|---------|---------|
| `ui/desktop_session.py` | +210 | Event loop, configure handling, output mapping |
| `ui/compositor_sdl.py` | +83/-12 | SDL2 Wayland backend support |
| `test_backend.py` | +1,136 | Cross-architecture conformance tests |
| `IMPLEMENTATION_STATUS.md` | +36/-1 | Test count 810→2500, at-rest encryption |
| `rust/README.md` | +6/-1 | Wayland crate status |
| `007-PROJECT_ROADMAP.md` | +24 | M13 completion status |
| `PROJECT_STATUS_SUMMARY.md` | +120 | Stakeholder status summary |
| `.github/workflows/ci.yml` | +19 | rust-wayland CI job |
| `CHANGELOG_WAYLAND.md` | +93 | Wayland integration changelog |

## Wayland Integration (ADR-0026)

### Rust Crate (`rust/wayland/`) — ABI 1.2.0

| Feature | FFI Function | Status |
|---------|--------------|--------|
| Connect to display | `nyrqis_wayland_connect` | ✅ |
| Create surface with xdg-shell | `nyrqis_wayland_create_surface` | ✅ |
| SHM buffer submission | `nyrqis_wayland_submit_buffer` | ✅ |
| Event dispatch | `nyrqis_wayland_dispatch_events` | ✅ |
| Disconnect | `nyrqis_wayland_disconnect` | ✅ |
| Destroy surface | `nyrqis_wayland_destroy_surface` | ✅ |
| Set title | `nyrqis_wayland_set_title` | ✅ |
| Get display fd | `nyrqis_wayland_get_fd` | ✅ |
| Get outputs | `nyrqis_wayland_get_outputs` | ✅ |
| Event handler callback | `nyrqis_wayland_set_event_handler` | ✅ |
| Error reporting | `nyrqis_wayland_last_error` | ✅ |

### SHM Buffer Pipeline

```
memfd_create → ftruncate → mmap → memcpy pixel data
→ wl_shm.create_pool → wl_shm_pool.create_buffer
→ wl_surface.attach + damage_buffer + commit
→ munmap + close(fd)
```

### Event Types

| Event | Compositor Trigger | Session Action |
|-------|-------------------|----------------|
| `SurfaceConfigure` | Surface resize | Resize focused window |
| `SurfaceClose` | Close request | Close focused window |
| `KeyboardKey` | Keyboard input | `KeyEvent` dispatch |
| `PointerMotion` | Pointer motion | `MouseEvent` dispatch |
| `PointerButton` | Pointer button | `MouseEvent` dispatch |
| `OutputChanged` | Monitor added/changed | Update monitor list |

## Package Signing (NPS-026 §6)

| Component | Status |
|-----------|--------|
| Ed25519 signing/verification | ✅ PyNaCl |
| `SigningKeypair` | ✅ generate/from_private_key/from_public_key |
| `PackageSignature` block | ✅ 97 bytes (version + key + sig) |
| `TrustStore` | ✅ add/remove/save/load |
| CLI tool (`nyrqisctl sign`) | ✅ generate-key/sign/verify/trust |
| Unit tests | ✅ 31/31 passing |

## Documentation Created

| Document | Type | Lines |
|----------|------|-------|
| ADR-0026 | Decision record | 220 |
| wayland-integration.md | Tutorial | 215 |
| contributor-setup.md | Tutorial | 245 |
| linux-backend-quickstart.md | Tutorial | 203 |
| PROJECT_STATUS_SUMMARY.md | Status | 120 |
| CHANGELOG_WAYLAND.md | Changelog | 93 |

## CI/CD

| Job | Status |
|-----|--------|
| `rust-wayland` | ✅ Builds + tests 17 unit tests |
| `arm64-cross-compile` | ✅ Cross-compiles Rust crates for aarch64 |
| `arm64-table-conformance` | ✅ Validates aarch64 seccomp tables |
| `backend` | ✅ 2,500 Python tests |
