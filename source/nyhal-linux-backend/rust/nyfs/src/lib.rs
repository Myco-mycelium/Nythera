//! Nyrqis NyFS block codec — ADR-0020 migration priority #3.
//!
//! **Implemented 2026-08-13 (SHA-256 checksum + Zstandard).** Every NyFS
//! block carries a SHA-256 checksum of its *uncompressed* data (NPS-004
//! §4) and is compressed with Zstandard (ADR-0007, default level 3).
//! The benchmark evidence (tests/BENCHMARK_RESULTS.md §5) puts the
//! read-path checksum verification and the per-block
//! compress/decompress at the top of NyFS hot-path cost, so these
//! primitives move into a memory-safe Rust module behind the versioned
//! FFI boundary (ABI-001). The pure-Python implementation
//! (`fuse/nyfs.py`'s `NyFSBlock` + `hashlib`/`zstandard`) remains the
//! shipped correctness floor that the tests run against on hosts
//! without the crate; `fuse/nyfs_codec.py` is the FFI loader that
//! routes calls here when the module is present.
//!
//! FFI surface (the ABI rule of ADR-0020 / ABI-001): versioned,
//! plain-data entry points, no shared mutable state, no pointers into
//! Python objects. Output buffers are `libc::malloc`'d by the crate and
//! freed by the caller through `nyrqis_nyfs_free` (the same ownership
//! contract as `nyrqis_seccomp`'s program buffers), so no size metadata
//! crosses the boundary.
//!
//! Return convention: **0 on success or a negative value** —
//! `-errno` (the Linux syscall convention) for real failures and the
//! module's internal codes for its own conditions. `ERR_CHECKSUM` is
//! deliberately OUTSIDE the errno range (1..=4095) so the loader's
//! `-errno → OSError` mapping can never misreport a data-integrity
//! failure as a kernel error; the loader maps it to the same
//! `ValueError` the pure-Python path raises.

use sha2::{Digest, Sha256};
use std::os::raw::{c_uchar, c_void};

/// Module ABI version (semver-major*10000 + minor*100 + patch).
pub const ABI_VERSION: u32 = 0x0001_0000;

// NyrqisErr codes (negative i32 returns). ERR_INTERNAL and ERR_CHECKSUM
// are OUTSIDE the errno range (1..=4095) by contract: -errno maps to
// OSError on the Python side, so in-range codes would be misreported.
pub const ERR_INVALID_ARGS: i32 = -22; // EINVAL
pub const ERR_TOO_LARGE: i32 = -27; // EFBIG — output beyond the sanity bound
pub const ERR_INTERNAL: i32 = -4096;
pub const ERR_CHECKSUM: i32 = -4097; // data integrity failure (→ ValueError)

/// Hard per-block output bound. Path-API blocks are exactly
/// `block_size` (64 KiB); even legacy `write_block` payloads stay far
/// below this. The bound is a defense-in-depth cap against a corrupt
/// or hostile frame claiming a huge content size — the daemon's storage
/// is local/trusted, but unbounded allocation is never acceptable.
const MAX_BLOCK_BYTES: usize = 64 * 1024 * 1024;

/// Report the module ABI version.
#[no_mangle]
pub extern "C" fn nyrqis_nyfs_version() -> u32 {
    ABI_VERSION
}

/// SHA-256 of `data` (the per-block integrity checksum, NPS-004 §4).
/// `digest_out` must point to a 32-byte caller-owned buffer. Returns 0
/// or negative errno.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyfs_sha256(
    data: *const c_uchar,
    len: usize,
    digest_out: *mut c_uchar,
) -> i32 {
    if data.is_null() || digest_out.is_null() {
        return ERR_INVALID_ARGS;
    }
    let input = std::slice::from_raw_parts(data, len);
    let digest = Sha256::digest(input);
    std::ptr::copy_nonoverlapping(
        digest.as_ptr(),
        digest_out,
        digest.len(),
    );
    0
}

/// Zstandard-compress `data` at `level` (1-22; the loader's default is
/// 3, matching NyFSBlock). The output buffer is `libc::malloc`'d here
/// and freed by the caller via `nyrqis_nyfs_free`. Returns 0 or
/// negative errno.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyfs_zstd_compress(
    data: *const c_uchar,
    len: usize,
    level: i32,
    out_ptr: *mut *mut c_uchar,
    out_len: *mut usize,
) -> i32 {
    if data.is_null() || out_ptr.is_null() || out_len.is_null() {
        return ERR_INVALID_ARGS;
    }
    let input = std::slice::from_raw_parts(data, len);
    match zstd::bulk::compress(input, level) {
        Ok(compressed) => publish(&compressed, out_ptr, out_len),
        // A zstd encode failure is an internal condition (bad input or
        // OOM), not a syscall errno — always the module's internal code.
        Err(_) => ERR_INTERNAL,
    }
}

/// Decompress a Zstandard frame, verify its SHA-256 against
/// `expected_digest` (32 bytes), and hand the verified payload to the
/// caller. The output buffer is `libc::malloc`'d here and freed by the
/// caller via `nyrqis_nyfs_free`. Returns 0, negative errno, or
/// `ERR_CHECKSUM` when the data fails integrity verification — the
/// read-path hot loop (the pure-Python floor raises `ValueError` in
/// that case; the loader maps this code to the same exception).
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyfs_zstd_decompress_verify(
    compressed: *const c_uchar,
    clen: usize,
    expected_digest: *const c_uchar,
    out_ptr: *mut *mut c_uchar,
    out_len: *mut usize,
) -> i32 {
    if compressed.is_null() || expected_digest.is_null()
        || out_ptr.is_null() || out_len.is_null()
    {
        return ERR_INVALID_ARGS;
    }
    let input = std::slice::from_raw_parts(compressed, clen);
    let data = match zstd::stream::decode_all(input) {
        Ok(data) => data,
        // A corrupt frame is a data-integrity condition, not a syscall
        // errno — the module's internal code (never a stale errno).
        Err(_) => return ERR_INTERNAL,
    };
    if data.len() > MAX_BLOCK_BYTES {
        return ERR_TOO_LARGE;
    }
    let digest = Sha256::digest(&data);
    let expected = std::slice::from_raw_parts(expected_digest, digest.len());
    if digest.as_slice() != expected {
        return ERR_CHECKSUM;
    }
    publish(&data, out_ptr, out_len)
}

/// Free an output buffer previously returned by this module (the
/// matching `nyrqis_seccomp_free` contract). No-op on a null pointer.
#[no_mangle]
pub unsafe extern "C" fn nyrqis_nyfs_free(ptr: *mut c_uchar) {
    if !ptr.is_null() {
        unsafe { libc::free(ptr as *mut c_void) };
    }
}

/// Copy `bytes` into a freshly malloc'd buffer, storing the pointer and
/// length in the caller's out-params (the module's ownership contract).
/// A zero-length block is valid (empty file content); malloc(0) may
/// return a non-null pointer, so a 1-byte minimum keeps the contract
/// uniform.
unsafe fn publish(bytes: &[u8], out_ptr: *mut *mut c_uchar, out_len: *mut usize) -> i32 {
    let ptr = unsafe { libc::malloc(bytes.len().max(1)) };
    if ptr.is_null() {
        return ERR_INTERNAL;
    }
    if !bytes.is_empty() {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), ptr as *mut c_uchar, bytes.len());
    }
    *out_ptr = ptr as *mut c_uchar;
    *out_len = bytes.len();
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn digest_of(data: &[u8]) -> Vec<u8> {
        Sha256::digest(data).to_vec()
    }

    #[test]
    fn abi_version_is_current() {
        assert_eq!(ABI_VERSION, 0x0001_0000);
    }

    #[test]
    fn err_codes_are_outside_errno_range() {
        // The contract maps -errno -> OSError; internal codes must never
        // collide with a real errno (1..=4095).
        for code in [ERR_INTERNAL, ERR_CHECKSUM] {
            assert!(!(1..=4095).contains(&(-code)), "code {-code} collides with errno range");
        }
        assert_eq!(ERR_INVALID_ARGS, -libc::EINVAL);
        assert_eq!(ERR_TOO_LARGE, -libc::EFBIG);
    }

    #[test]
    fn ffi_symbols_exist() {
        let _ = nyrqis_nyfs_version;
        let _ = nyrqis_nyfs_sha256;
        let _ = nyrqis_nyfs_zstd_compress;
        let _ = nyrqis_nyfs_zstd_decompress_verify;
        let _ = nyrqis_nyfs_free;
    }

    #[test]
    fn sha256_matches_known_vector() {
        // SHA-256 of the empty string is a fixed, canonical value.
        let mut digest = [0u8; 32];
        let rc = unsafe {
            nyrqis_nyfs_sha256(
                b"".as_ptr(),
                0,
                digest.as_mut_ptr(),
            )
        };
        assert_eq!(rc, 0);
        assert_eq!(
            digest,
            [
                0xe3, 0xb0, 0xc4, 0x42, 0x98, 0xfc, 0x1c, 0x14,
                0x9a, 0xfb, 0xf4, 0xc8, 0x99, 0x6f, 0xb9, 0x24,
                0x27, 0xae, 0x41, 0xe4, 0x64, 0x9b, 0x93, 0x4c,
                0xa4, 0x95, 0x99, 0x1b, 0x78, 0x52, 0xb8, 0x55,
            ]
        );
    }

    #[test]
    fn sha256_null_input_is_einval() {
        assert_eq!(unsafe { nyrqis_nyfs_sha256(std::ptr::null(), 0, std::ptr::null_mut()) }, ERR_INVALID_ARGS);
    }

    #[test]
    fn compress_roundtrip_preserves_bytes() {
        // (data, must_shrink): the text-like and zeroes corpora must
        // actually compress; the tiny/empty ones have no ratio to win.
        let corpus: Vec<(Vec<u8>, bool)> = vec![
            (b"".to_vec(), false),                          // empty
            (b"a".to_vec(), false),                         // tiny
            (b"compressible-data;".repeat(4096), true),     // text-like (64 KiB)
            (vec![0u8; 65536], true),                        // zeroes (very compressible)
        ];
        for (data, must_shrink) in &corpus {
            let mut out_ptr: *mut c_uchar = std::ptr::null_mut();
            let mut out_len: usize = 0;
            let rc = unsafe {
                nyrqis_nyfs_zstd_compress(
                    data.as_ptr(),
                    data.len(),
                    3,
                    &mut out_ptr,
                    &mut out_len,
                )
            };
            assert_eq!(rc, 0, "compress failed for {}-byte input", data.len());
            assert!(!out_ptr.is_null());
            let compressed = unsafe { std::slice::from_raw_parts(out_ptr, out_len) }.to_vec();

            if *must_shrink {
                assert!(
                    out_len < data.len(),
                    "expected compression to shrink a {}-byte corpus, got {out_len}",
                    data.len()
                );
            }

            // Now decompress + verify with the correct digest.
            let digest = digest_of(data);
            let mut dec_ptr: *mut c_uchar = std::ptr::null_mut();
            let mut dec_len: usize = 0;
            let rc = unsafe {
                nyrqis_nyfs_zstd_decompress_verify(
                    compressed.as_ptr(),
                    compressed.len(),
                    digest.as_ptr(),
                    &mut dec_ptr,
                    &mut dec_len,
                )
            };
            assert_eq!(rc, 0);
            assert_eq!(dec_len, data.len());
            let decoded = unsafe { std::slice::from_raw_parts(dec_ptr, dec_len) };
            assert_eq!(decoded, data.as_slice(), "roundtrip mismatch for {}-byte input", data.len());

            unsafe {
                nyrqis_nyfs_free(out_ptr);
                nyrqis_nyfs_free(dec_ptr);
            }
        }
    }

    #[test]
    fn decompress_verify_rejects_wrong_checksum() {
        let data = b"integrity-check-payload;".repeat(64);
        let mut out_ptr: *mut c_uchar = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = unsafe {
            nyrqis_nyfs_zstd_compress(
                data.as_ptr(),
                data.len(),
                3,
                &mut out_ptr,
                &mut out_len,
            )
        };
        assert_eq!(rc, 0);
        let compressed = unsafe { std::slice::from_raw_parts(out_ptr, out_len) }.to_vec();

        // A wrong digest must surface ERR_CHECKSUM, not a crash or 0.
        let wrong = [0xabu8; 32];
        let mut dec_ptr: *mut c_uchar = std::ptr::null_mut();
        let mut dec_len: usize = 0;
        let rc = unsafe {
            nyrqis_nyfs_zstd_decompress_verify(
                compressed.as_ptr(),
                compressed.len(),
                wrong.as_ptr(),
                &mut dec_ptr,
                &mut dec_len,
            )
        };
        assert_eq!(rc, ERR_CHECKSUM);

        unsafe {
            nyrqis_nyfs_free(out_ptr);
        }
    }

    #[test]
    fn decompress_verify_rejects_corrupt_frame() {
        let data = b"corrupt-frame-test;".repeat(16);
        let mut out_ptr: *mut c_uchar = std::ptr::null_mut();
        let mut out_len: usize = 0;
        let rc = unsafe {
            nyrqis_nyfs_zstd_compress(
                data.as_ptr(),
                data.len(),
                3,
                &mut out_ptr,
                &mut out_len,
            )
        };
        assert_eq!(rc, 0);
        let mut compressed = unsafe { std::slice::from_raw_parts(out_ptr, out_len) }.to_vec();

        // Flip bytes in the middle of the frame: decode must fail with a
        // negative code (never a crash, never 0).
        if compressed.len() > 8 {
            compressed[compressed.len() / 2] ^= 0xff;
        }
        let digest = digest_of(&data);
        let mut dec_ptr: *mut c_uchar = std::ptr::null_mut();
        let mut dec_len: usize = 0;
        let rc = unsafe {
            nyrqis_nyfs_zstd_decompress_verify(
                compressed.as_ptr(),
                compressed.len(),
                digest.as_ptr(),
                &mut dec_ptr,
                &mut dec_len,
            )
        };
        assert!(rc < 0, "corrupt frame must fail, got {rc}");

        unsafe {
            nyrqis_nyfs_free(out_ptr);
        }
    }

    #[test]
    fn null_args_are_einval() {
        let mut ptr: *mut c_uchar = std::ptr::null_mut();
        let mut len: usize = 0;
        assert_eq!(
            unsafe { nyrqis_nyfs_zstd_compress(std::ptr::null(), 0, 3, &mut ptr, &mut len) },
            ERR_INVALID_ARGS
        );
        assert_eq!(
            unsafe {
                nyrqis_nyfs_zstd_decompress_verify(
                    std::ptr::null(), 0, std::ptr::null(),
                    &mut ptr, &mut len,
                )
            },
            ERR_INVALID_ARGS
        );
    }

    #[test]
    fn free_null_is_safe() {
        unsafe { nyrqis_nyfs_free(std::ptr::null_mut()) };
    }
}
