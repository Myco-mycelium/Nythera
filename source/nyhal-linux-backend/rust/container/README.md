# Rust container launch-plan primitives — FFI boundary contract and conformance status

**Status: IMPLEMENTED 2026-08-13.** The Rust crate (`src/lib.rs`) ships
the pure, well-bounded computations the container manager makes when
launching a container (NPS-017 §4.1, NPS-010 §7): the launcher argv,
the cgroup v1/v2 resource plan, the `--map-root-user` uid/gid maps, and
the NPS-010 §4 lifecycle state machine. CI (`.github/workflows/ci.yml`)
builds and unit-tests the crate on every push; the dev host still has
no Rust toolchain, so **CI is the compiler and the test runner for
this crate**. The pure-Python implementation (`backend/container_codec.py`)
remains the shipped correctness floor that the tests run against on
hosts without the crate.

## Why this module now

ADR-0020 priority #5 (NyCore/NyHAL = Rust) is the container-primitives
migration, taken incrementally with the Python reference implementation
kept green throughout. The extraction boundary is the **launch-plan
builder**: the exact argv handed to `os.execv` inside the new
namespaces (FIND-BACKEND-004 — hostname and command are argv entries,
never shell-interpolated), the cgroup settings written into the v1/v2
hierarchies (FIND-BACKEND-003 — the memory cgroup carries
`notify_on_release=0`), the uid/gid maps written to `/proc/self/uid_map`
/`gid_map` after `unshare(CLONE_NEWUSER)` (captured before the unshare —
the classic 65534 map-write failure), and the `CREATED → RUNNING →
SUSPENDED → TERMINATED` transition validity the manager enforces.
These are pure functions of (config + limits + ids + command) → plan,
and they sit on a platform-critical execution path (container launch)
that under the ADR-0020 platform-boundary rule must not depend on the
Python interpreter in its shipped form.

The launcher itself, the syscall dance, and the actual cgroup writes
remain Python in the reference backend (and Rust for the syscalls via
`rust/syscalls/`, migration #2) — this crate is the *planning* layer,
the same class of pure computation as the seccomp policy compiler
(#1) and the IPC wire codec (#4).

## Wire formats (canonical — the differential gate verifies byte-parity)

```text
launcher_argv: "NYRQ" | wire_version(1) | u32 argv_count
               | count × (u32 len + bytes)
cgroup_plan:   "NYRQ" | wire_version(1) | u32 v1_count
               | v1_count × (path, pairs)
                 path  = u32 len + bytes
                 pairs = u32 pair_count + pair_count × (u32 klen + key, u32 vlen + val)
               | u32 v2_count | v2_count × (u32 klen + key, u32 vlen + val)
root_maps:     "NYRQ" | wire_version(1) | 3 × (u32 len + bytes)
               [setgroups, uid_map, gid_map]
```

All lengths are u32 little-endian. The launcher argv is ordered
`python launcher --hostname <hostname> [--policy-file <path>
[--default-deny]] -- <command...>`; the command list itself crosses the
boundary as a length-prefixed flat buffer (`build_command_flat`), so no
argv splicing or quoting ever happens. The cgroup plan carries the v1
hierarchy (`memory` + `pids`, memory with `notify_on_release=0`) and
the v2 unified settings (`memory.max`, `pids.max`, and `cpu.max` only
when a quota is configured — `cpu_quota_us < 0` means no CPU limit).

## FFI surface (the ABI rule of ADR-0020 / ABI-001)

Versioned, plain-data entry points, no shared mutable state, no
pointers into Python objects. The encoders' output buffers are
`libc::malloc`'d by the crate and freed by the caller through
`nyrqis_container_free` (the seccomp/syscalls/nyfs/ipc ownership
contract).

| Entry point | Args | Returns |
|-------------|------|---------|
| `nyrqis_container_version` | — | u32 ABI version (0x0001_0000) |
| `nyrqis_container_launcher_argv` | python, launcher, hostname, policy_path, default_deny, command_flat, out**, out_len* | 0, -errno, or `ERR_INVALID_WIRE` |
| `nyrqis_container_cgroup_plan` | container_id, memory_mb, pid_limit, cpu_quota_us, cpu_period_us, out**, out_len* | 0, -errno, or `ERR_INVALID_WIRE` |
| `nyrqis_container_root_maps` | uid, gid, out**, out_len* | 0 or -errno |
| `nyrqis_container_transition_valid` | from_state (u8), to_state (u8) | 0 (valid), `ERR_INVALID_TRANSITION`, or `ERR_INVALID_ARGS` |
| `nyrqis_container_free` | ptr | — |

**State vocabulary** (the crate's wire vocabulary, mirrored by
`container_codec.STATE_INDEX`): 0 `CREATED`, 1 `RUNNING`,
2 `SUSPENDED`, 3 `TERMINATED`. Legal pairs per NPS-010 §4:
`CREATED→RUNNING`, `RUNNING→SUSPENDED`, `RUNNING→TERMINATED`,
`SUSPENDED→RUNNING`, `SUSPENDED→TERMINATED`.

**Error contract** (`NyrqisErr` codes, negative i32):

- `-errno` (1..=4095) — real failures (`EINVAL` for null pointers or
  out-of-range states, `EFBIG` beyond the sanity bound).
- `ERR_INTERNAL` (`-4096`) — module failure; outside the errno range
  by design, maps to `RuntimeError` on the Python side.
- `ERR_INVALID_WIRE` (`-4097`) — malformed command flat buffer: bad
  magic, bad wire version, truncated or trailing length-prefixed
  fields. The loader maps it to the exact `ValueError` the pure-Python
  parser raises.
- `ERR_INVALID_TRANSITION` (`-4098`) — a disallowed NPS-010 §4 state
  pair (from `transition_valid` only). The loader maps it to `False`:
  an invalid transition is a *result*, not an error.

The loader (`backend/container_codec.py`) falls back to the
pure-Python floor on any load or routing failure — the Python path is
the correctness floor — and `NYRQIS_RUST_FORCE=1` turns routing
failures into errors (the conformance gate's guarantee that every call
drives this module).

## Conformance evidence

1. **Canonical layout pinned.** The crate's unit tests assert the wire
   bytes of known launcher argv / cgroup plan / root maps inputs
   field-by-field (magic, version, every u32 length prefix, every
   field), plus the full transition matrix, out-of-range state
   rejection (`ERR_INVALID_ARGS`), bad-arg `EINVAL`, malformed flat
   rejection (`ERR_INVALID_WIRE`), 2 MiB roundtrips, oversized-input
   `EFBIG`, and `free(NULL)` is safe.
2. **Seeded differential tests** (`TestContainerPrimitivesConformance`
   in `test_backend.py`): across the corpus — with/without policy
   file, default-deny on/off, empty and multi-word commands, plain and
   quota'd cgroup plans, boundary uid/gid — the Rust module and the
   pure-Python floor must produce **byte-identical wire bytes**, decode
   field-for-field identically, and agree on the full state-transition
   matrix. Runs wherever the crate is built (the CI conformance job);
   skipped on hosts without it.
3. **Forced-mode conformance gate.** The CI `rust-container-conformance`
   job builds the crate and runs the container-facing test classes
   (`TestContainerPrimitives`, `TestContainerPrimitivesLoader`,
   `TestContainerPrimitivesConformance`, plus `TestLauncherSecurity`
   and `TestDirectSyscallLaunch`, which route through the codec) with
   `NYRQIS_RUST_FORCE=1` and `NYRQIS_RUST_LIB` set to the built
   cdylib. The job is **required and blocking** — a semantic regression
   in the Rust crate fails the build. (It runs only the container
   classes, not the full suite: forcing the container lib would make
   the *separate* seccomp, syscalls, NyFS, and IPC loaders fail their
   own force checks.)
4. **The whole suite stays green both ways.** 205/205 locally (182 run,
   23 skipped without the crates) and under the exact CI no-fusepy
   condition; the container lifecycle tests (`TestContainerPrimitives`,
   end-to-end launches through both the direct-syscall and legacy
   paths) pass unchanged on the floor path.
