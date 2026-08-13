# Rust NyFS codec module — FFI boundary contract and conformance status

**Status: IMPLEMENTED 2026-08-13.** The Rust block codec (`src/lib.rs`)
implements the two hot-path primitives of NyFS storage (NPS-004 §4,
ADR-0007): the per-block **SHA-256 checksum** (of the uncompressed
data) and **Zstandard compression** at the block level, with the read
path verifying the checksum before the payload is returned. CI
(`.github/workflows/ci.yml`) builds and unit-tests the crate on every
push; the dev host still has no Rust toolchain, so **CI is the
compiler and the test runner for this crate**. The pure-Python
implementation (`fuse/nyfs.py`'s `NyFSBlock`, backed by `hashlib` +
the `zstandard` module) remains the shipped correctness floor that the
tests run against on hosts without the crate.

## Why these primitives first

ADR-0020 priority #3 (storage = Rust) is evidence-gated by the
benchmark data in `tests/BENCHMARK_RESULTS.md`:

- **Read path (§5):** per-read SHA-256 verification dominates — the
  same read loop runs ~557 MB/s without the verify step vs ~15.5 MB/s
  with it (35×), so verification moves into the memory-safe module
  where it runs in one FFI call per block.
- **Write path (§3/§6):** per-block compress + checksum bounds the
  per-write cost; the codec makes that bound a single Rust call.

The extraction boundary is the **block codec**, not the whole
filesystem: `fuse/nyfs_codec.py` (the loader) is wired into
`NyFSBlock.compute_checksum`/`compress`/`decompress` — and therefore
into `_make_block`/`_decompress_verified`, the exact hot loops the
benchmarks measure. The FUSE operation handlers themselves stay Python
in the reference backend (ADR-0020: above the platform boundary).

## FFI surface (the ABI rule of ADR-0020 / ABI-001)

Versioned, plain-data entry points, no shared mutable state, no
pointers into Python objects. Output buffers are `libc::malloc`'d by
the crate and freed by the caller through `nyrqis_nyfs_free` (the same
ownership contract as `nyrqis_seccomp`'s program buffers), so no size
metadata crosses the boundary.

| Entry point | Args | Returns |
|-------------|------|---------|
| `nyrqis_nyfs_version` | — | u32 ABI version (0x0001_0000) |
| `nyrqis_nyfs_sha256` | `data, len, digest_out[32]` | 0 or -errno |
| `nyrqis_nyfs_zstd_compress` | `data, len, level, out**, out_len*` | 0, -errno, or internal |
| `nyrqis_nyfs_zstd_decompress_verify` | `compressed, clen, digest[32], out**, out_len*` | 0, -errno, `ERR_CHECKSUM`, or internal |
| `nyrqis_nyfs_free` | `ptr` | — |

**Error contract** (`NyrqisErr` codes, negative i32):

- `-errno` (1..=4095) — real failures (`EINVAL` for null args, `EFBIG`
  for output beyond the 64 MiB per-block sanity bound).
- `ERR_INTERNAL` (`-4096`) — module failure (e.g. a corrupt frame the
  decoder rejects); outside the errno range by design, maps to
  `RuntimeError` on the Python side.
- `ERR_CHECKSUM` (`-4097`) — data-integrity failure: the decompressed
  payload's SHA-256 does not match the expected digest. Also outside
  the errno range so it can never be misreported as a kernel error;
  the loader maps it to the exact `ValueError("Block checksum
  verification failed")` the pure-Python floor raises.

The loader (`fuse/nyfs_codec.py`) falls back to `hashlib` +
`zstandard` on any load or routing failure — the Python path is the
correctness floor — and `NYRQIS_RUST_FORCE=1` turns routing failures
into errors (the conformance gate's guarantee that every call drives
this module).

## Conformance evidence

1. **Golden vectors.** The crate's unit tests pin the canonical SHA-256
   of the empty string, null-arg → `EINVAL`, roundtrip preservation for
   empty/tiny/block-sized/zero-filled corpora, wrong-checksum →
   `ERR_CHECKSUM`, corrupt-frame → negative code, and `free(NULL)` is
   safe.
2. **Seeded differential test** (`TestNyFSCodecConformance` in
   `test_backend.py`): across the corpus set — empty, tiny,
   compressible, zeroes, incompressible random, mixed — the Rust module
   and the pure-Python floor must agree byte-for-byte on checksums and
   roundtrips, and must surface integrity failures as the same
   `ValueError`. Runs wherever the crate is built (the CI conformance
   job); skipped on hosts without it.
3. **Forced-mode conformance gate.** The CI `rust-nyfs-conformance` job
   builds the crate and runs the codec test classes with
   `NYRQIS_RUST_FORCE=1` and `NYRQIS_RUST_LIB` set to the built cdylib.
   The job is **required and blocking** — a semantic regression in the
   Rust codec fails the build. (It runs only the codec classes, not the
   full suite: forcing the NyFS lib would make the *separate* seccomp
   and syscalls loaders fail their own force checks.)
4. **The whole suite stays green both ways.** 167/167 locally (156 run,
   11 skipped without the crates) and under the exact CI no-fusepy
   condition; the storage-guarantee, persistence, snapshot, and
   operations tests all exercise the codec through `NyFSBlock` and pass
   unchanged on the fallback path.
