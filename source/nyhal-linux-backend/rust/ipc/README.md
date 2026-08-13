# Rust IPC wire codec — FFI boundary contract and conformance status

**Status: IMPLEMENTED 2026-08-13.** The Rust wire codec (`src/lib.rs`)
implements the binary framing for `IPCMessage` (NPS-003 §3, NPS-017
§4.3): the message serialization a real cross-process transport will
sit on. CI (`.github/workflows/ci.yml`) builds and unit-tests the crate
on every push; the dev host still has no Rust toolchain, so **CI is the
compiler and the test runner for this crate**. The pure-Python
implementation (`ipc/ipc_codec.py`, a `struct`-based floor) remains the
shipped correctness floor that the tests run against on hosts without
the crate.

## Why this module now

ADR-0020 priority #4 (networking = Rust) is gated on the Python IPC
semantics being stable and benchmarked — they are (`tests/
BENCHMARK_RESULTS.md` §1: in-process round-trip 88/124/215 µs
p50/p95/p99, token-bucket measured separately in §3). The extraction
boundary is the **wire codec**: the message serialization/parsing that
future cross-container transport sits on, and the parsing trust
boundary of that transport — a memory-safe parser eliminates an entire
class of parsing bugs before any socket/shared-memory path exists. The
reference transport today (`IPCManager`, in-process queues) is
unchanged; the codec is wired in as `IPCMessage.to_wire()/from_wire()`
so the transport boundary is real and exercised.

## Wire format (canonical — the differential gate verifies byte-parity)

```text
0   4  magic "NYRQ"
4   1  wire version (1)
5   1  message_type (0 send, 1 receive, 2 call, 3 reply, 4 notify)
6   8  timestamp (f64, little-endian)
14  4  message_id_len (u32 LE) + bytes
    4  sender_id_len + bytes
    4  receiver_id_len + bytes
    4  reply_to_len + bytes       (0 = absent)
    4  payload_len + bytes
    4  caps_flat_len + bytes      ([u32 cap_len + cap bytes]*)
    4  metadata_len + bytes       (opaque — the caller's JSON blob)
```

`metadata` is opaque on the wire: the Python side serializes it with
`json.dumps(sort_keys=True)`, so no dict-ordering contract crosses the
boundary. Lengths are bounded (16 MiB total message, 1 MiB per field,
4096 capability entries).

## FFI surface (the ABI rule of ADR-0020 / ABI-001)

Versioned, plain-data entry points, no shared mutable state, no
pointers into Python objects. The encoder's output buffer and the
decoder's `IpcMessageView` are `libc::malloc`'d by the crate and freed
by the caller through `nyrqis_ipc_free` (the seccomp/nyfs ownership
contract).

| Entry point | Args | Returns |
|-------------|------|---------|
| `nyrqis_ipc_version` | — | u32 ABI version (0x0001_0000) |
| `nyrqis_ipc_encode` | type, timestamp, id, sender, receiver, reply_to, payload, caps_flat, metadata, out**, out_len* | 0, -errno, or internal |
| `nyrqis_ipc_decode` | buf, buf_len, view** | 0, -errno, or `ERR_INVALID_WIRE` |
| `nyrqis_ipc_free` | ptr | — |

**Error contract** (`NyrqisErr` codes, negative i32):

- `-errno` (1..=4095) — real failures (`EINVAL` for null args, `EFBIG`
  beyond the 16 MiB bound).
- `ERR_INTERNAL` (`-4096`) — module failure; outside the errno range by
  design, maps to `RuntimeError` on the Python side.
- `ERR_INVALID_WIRE` (`-4097`) — malformed/oversized message: bad magic
  or wire version, unknown message type, truncated field, trailing
  bytes, or an invalid capability flat buffer. Also outside the errno
  range; the loader maps it to the exact `ValueError("invalid IPC
  message wire format")` the pure-Python parser raises.

The loader (`ipc/ipc_codec.py`) falls back to the `struct` floor on any
load or routing failure — the Python path is the correctness floor —
and `NYRQIS_RUST_FORCE=1` turns routing failures into errors (the
conformance gate's guarantee that every call drives this module).

## Conformance evidence

1. **Canonical layout pinned.** The crate's unit tests assert the wire
   bytes of a known message field-by-field (magic, version, type,
   timestamp, every u32 length prefix, every payload), plus the
   roundtrip, malformed-wire rejection (bad magic/version/type,
   truncated, trailing), bad-arg `EINVAL`, bad caps flat → `ERR_INVALID_WIRE`,
   and `free(NULL)` is safe.
2. **Seeded differential test** (`TestIPCCodecConformance` in
   `test_backend.py`): across the message corpus — all five types,
   absent/present `reply_to`, empty and 64 KiB payloads, empty and
   multi-capability transfers, plain and nested metadata — the Rust
   module and the `struct` floor must produce **byte-identical wire
   bytes**, decode field-for-field identically, reject the same
   malformed inputs, and roundtrip through `IPCMessage.to_wire()/
   from_wire()`. Runs wherever the crate is built (the CI conformance
   job); skipped on hosts without it.
3. **Forced-mode conformance gate.** The CI `rust-ipc-conformance` job
   builds the crate and runs the IPC codec test classes with
   `NYRQIS_RUST_FORCE=1` and `NYRQIS_RUST_LIB` set to the built cdylib.
   The job is **required and blocking** — a semantic regression in the
   Rust codec fails the build. (It runs only the IPC classes, not the
   full suite: forcing the IPC lib would make the *separate* seccomp,
   syscalls, and NyFS loaders fail their own force checks.)
4. **The whole suite stays green both ways.** 185/185 locally (169 run,
   16 skipped without the crates) and under the exact CI no-fusepy
   condition; the IPC semantics tests (`TestIPCSemantics`) and the
   wired `to_wire`/`from_wire` roundtrip pass unchanged on the floor
   path.
