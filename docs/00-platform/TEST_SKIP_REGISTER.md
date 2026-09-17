---
title: Test Skip Register
version: 1.3.0
date: 2026-09-17
---

# Test Skip Register

Every deliberate skip in the backend suite, with its reason and its
owner. The rule (set in 0.29.21 after the `system_monitor` dead-skip
purge, extended in 0.29.22): **a skip is an answer, not an excuse** —
each entry here is a claim that the test cannot run in this
environment, never that the code under test is unfinished. An
import-failure skip that masks a missing API is a bug: the
implementation must meet its spec suite (the 0.28.0/0.29.21
convention), and the skip must be deleted.

Status after 0.29.23: **3 skips** in a suite of 8,992 passing tests
(<0.1%). Building the wayland cdylib locally un-skipped the six
wayland-crate entries (they remain documented below for hosts without
the artifact). Every remaining skip is environmental; keep this
register honest when anything changes — the contract test
`TestSkipRegister` re-reads it, re-scans the suite for dead
import-failure skips, and fails on drift.

## The register (current — 3 entries; 9 when the wayland cdylib is absent; 8 on a bare CI runner, where additionally Pillow is missing)

| # | Test | Category | Reason | Owner |
|---|---|---|---|---|
| 1 | `tests/test_rust_ffi.py:190` | env-hardware | Host has no Vulkan loader/ICD; the crate path is exercised on GPU-equipped CI | graphics team |
| 2 | `tests/test_rust_ffi.py:197` | env-hardware | Same as above | graphics team |
| 3 | `tests/test_wayland_multimonitor.py:24` | env-crate | Wayland client crate artifact not built on this host; CI's `wayland-real-clients` job builds and runs it | compositor team |
| 4 | `tests/test_wayland_multimonitor.py:34` | env-crate | Same as above | compositor team |
| 5 | `tests/test_wayland_multimonitor.py:44` | env-crate | Same as above | compositor team |
| 6 | `tests/test_wayland_multimonitor.py:53` | env-crate | Same as above | compositor team |
| 7 | `tests/test_wayland_multimonitor.py:63` | env-crate | Same as above | compositor team |
| 8 | `tests/test_wayland_multimonitor.py:71` | env-crate | Same as above | compositor team |
| 9 | `tests/test_compositor_presentation.py:334` | env-sandbox | This dev container's seccomp profile drops SCM_RIGHTS ancillary data, so fd-passing over a real Unix socket cannot be observed; CI (no such profile) runs it for real | compositor team |
| 10 | `tests/test_wayland_multimonitor.py:187` | env-renderer | Pillow (the WaylandSession software renderer) absent — render_frame() honestly returns None; CI's wayland-conformance gate installs pillow and runs it for real | compositor team |

## Purged in 0.29.21–0.29.22 (the cautionary tales)

| What was skipped | Why it was wrong | What happened |
|---|---|---|
| `TestSystemMonitor` (16 tests) | `SystemSnapshot` never existed — the suite skipped the very proof that the API was missing | Implementation grew the spec API; all 16 pass |
| `test_boot_to_desktop` compositor/shell tests (3) | Spec-era constructors (`NyrqisCompositor()`, no-arg `NyrqisShell()`) that no code ever provided | Rewritten against the real architecture (`Compositor.render_screen`, `NyrqisShell(doc).run()`); all pass |
| `test_drm.py` device-ops (6) | Conditional inversions: skipped whenever the crate WAS present, so the crate path was never asserted | Both-mode assertions — which immediately exposed a **real bug**: `get_connector_info`'s `ctypes.byref(struct.field)` TypeError, latent forever because stub mode returned early |
| `test_compositor_host.py` stub-engine (1) | Skipped on crate hosts, so the fail-closed stub contract went untested there | Patched-codec assertion; runs in both modes |
| Dead `except ImportError → skip` branches (5) | First-party imports (`DesktopBackend`, `LiveSession`, `nstudio_codec`) guarded by skips that were dead code today but would silently skip on a future breakage | Branches removed — a first-party import failure now FAILS the test; `TestSkipRegister` scans the suite for the pattern |
| `test_desktop_session.py` monitor skips (16→0, incl. timeout variant) | Same `SystemSnapshot` root cause as above | Pass since 0.29.21 |

## Rules for adding a new skip

1. It must appear in this file with a category, reason, and owner.
2. `TestSkipRegister` must be updated in the same commit — the
   register and the suite may not drift.
3. An `ImportError` skip on a **first-party** module is never
   acceptable: fix the import or implement the API.
4. Conditional crate-vs-stub inversions must be rewritten as
   both-mode assertions (see `test_drm.py`) instead of skipping one
   side.
5. Skip reasons should name the environmental fact, not the missing
   work ("host has no Vulkan ICD", never "not implemented yet").
