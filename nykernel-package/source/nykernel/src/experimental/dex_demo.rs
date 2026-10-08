//! DEX parsing demo -- the first real Phase 6 (Android compatibility)
//! capability proof, same category of progress as `experimental::pe`
//! was for Phase 5. See `experimental::dex`'s module doc for the
//! honest scope: this parses the DEX *container format*, it does not
//! execute any bytecode.
//!
//! Unlike every ring-3 demo in this kernel, this one needs no address
//! space, no privilege transition, no trap handler -- DEX parsing is
//! pure data validation, so positive *and* negative testing both run
//! as ordinary function calls in the same boot, returning normally,
//! rather than needing a separate temporary-rebuild negative control
//! the way the ring-3/paging demos do. The negative checks below are
//! permanent, shipped code -- they run on every boot, not just once
//! during development.

use alloc::vec::Vec;

use crate::experimental::dex::{self, DexError};
use crate::serial_println;

/// A real, hand-constructed, checksum-and-signature-correct minimal
/// DEX file (`userprog/classes.dex`) -- zero classes/strings/methods,
/// but a genuine, spec-required `map_list` describing itself, built
/// and independently re-verified with Python's own `struct`/`hashlib`
/// before any Rust touched it (see the construction script referenced
/// in `IMPLEMENTATION_STATUS.md`).
static VALID_DEX: &[u8] = include_bytes!("../../userprog/classes.dex");

/// Runs both the positive parse and two permanent negative checks.
/// Returns `true` iff every part of the proof held.
pub fn run() -> bool {
    let kats_ok = run_known_answer_tests();
    let positive_ok = run_positive();
    let magic_check_ok = run_negative_bad_magic();
    let map_check_ok = run_negative_map_self_reference();
    let checksum_check_ok = run_negative_bad_checksum();

    serial_println!(
        "  experimental dex-demo self-test: crypto known-answer {}, valid parse+integrity {}, bad-magic rejection {}, map-self-reference rejection {}, checksum rejection {} -- {}",
        if kats_ok { "OK" } else { "FAILED" },
        if positive_ok { "OK" } else { "FAILED" },
        if magic_check_ok { "OK" } else { "FAILED" },
        if map_check_ok { "OK" } else { "FAILED" },
        if checksum_check_ok { "OK" } else { "FAILED" },
        if kats_ok && positive_ok && magic_check_ok && map_check_ok && checksum_check_ok { "OK" } else { "FAILED" }
    );

    kats_ok && positive_ok && magic_check_ok && map_check_ok && checksum_check_ok
}

fn run_positive() -> bool {
    let info = match dex::parse(VALID_DEX) {
        Ok(info) => info,
        Err(e) => {
            serial_println!("  experimental dex-demo: FAILED to parse a valid DEX file: {:?}", e);
            return false;
        }
    };

    let ok = info.file_size == VALID_DEX.len() as u32
        && info.string_ids_count == 0
        && info.type_ids_count == 0
        && info.proto_ids_count == 0
        && info.field_ids_count == 0
        && info.method_ids_count == 0
        && info.class_defs_count == 0
        && info.map_entry_count == 1;

    serial_println!(
        "  experimental dex-demo: parsed a real DEX file (file_size={}, map_entry_count={}, all section counts zero as constructed) -- {}",
        info.file_size, info.map_entry_count, if ok { "OK" } else { "MISMATCH" }
    );

    // Integrity: the SAME real file's embedded Adler-32 checksum and
    // SHA-1 signature, recomputed in-kernel by experimental::crypto.
    match dex::verify(VALID_DEX) {
        Ok(()) => {
            serial_println!("  experimental dex-demo: Adler-32 checksum + SHA-1 signature verified in-kernel -- OK");
            ok
        }
        Err(e) => {
            serial_println!("  experimental dex-demo: DEX integrity verify FAILED: {:?}", e);
            false
        }
    }
}

/// Corrupts the magic's first byte in a heap-allocated copy and
/// confirms `dex::parse` rejects it with exactly `BadMagic` -- not
/// some other error, and not silently accepted.
fn run_negative_bad_magic() -> bool {
    let mut corrupted: Vec<u8> = VALID_DEX.to_vec();
    corrupted[0] = 0;
    let result = dex::parse(&corrupted);
    let rejected_correctly = matches!(result, Err(DexError::BadMagic));
    serial_println!(
        "  experimental dex-demo: corrupted magic -- parse result {:?}, correctly rejected: {}",
        result.err(), rejected_correctly
    );
    rejected_correctly
}

/// Corrupts the `map_list`'s single entry so it no longer describes
/// itself (changes its recorded file offset to a bogus value) and
/// confirms `dex::parse` rejects it with exactly `NoMapListEntry` --
/// proving the self-reference check is a real structural validation,
/// not just "is the count field nonzero."
fn run_negative_map_self_reference() -> bool {
    let mut corrupted: Vec<u8> = VALID_DEX.to_vec();
    let map_off = u32::from_le_bytes([corrupted[52], corrupted[53], corrupted[54], corrupted[55]]) as usize;
    let item_offset_field = map_off + 4 + 8; // map_item: type(2) + unused(2) + size(4) + offset(4)
    corrupted[item_offset_field..item_offset_field + 4].copy_from_slice(&999u32.to_le_bytes());
    let result = dex::parse(&corrupted);
    let rejected_correctly = matches!(result, Err(DexError::NoMapListEntry));
    serial_println!(
        "  experimental dex-demo: corrupted map self-reference -- parse result {:?}, correctly rejected: {}",
        result.err(), rejected_correctly
    );
    rejected_correctly
}

/// Known-answer tests for the Adler-32/SHA-1 primitives, independent of
/// the DEX file: a correct implementation must hit these published
/// vectors exactly, so a subtly wrong implementation can't pass just
/// because "the real bundled file happened to validate" (a wrong hash
/// that still matched the file's stored hash would be a coincidence of
/// two bugs cancelling -- these vectors rule that out directly).
fn run_known_answer_tests() -> bool {
    let adler = crate::experimental::crypto::adler32(b"Wikipedia");
    let adler_ok = adler == 0x11E6_0398;

    let digest = crate::experimental::crypto::sha1(b"abc");
    let expected: [u8; 20] = [
        0xa9, 0x99, 0x3e, 0x36, 0x47, 0x06, 0x81, 0x6a, 0xba, 0x3e, 0x25, 0x71, 0x78, 0x50, 0xc2, 0x6c, 0x9c,
        0xd0, 0xd8, 0x9d,
    ];
    let sha1_ok = digest == expected;

    serial_println!(
        "  experimental dex-demo: crypto known-answer -- Adler-32(\"Wikipedia\")={:#010x} (expected 0x11e60398) {}, SHA-1(\"abc\") 20-byte digest {}",
        adler,
        if adler_ok { "OK" } else { "MISMATCH" },
        if sha1_ok { "OK" } else { "MISMATCH" }
    );
    adler_ok && sha1_ok
}

/// Corrupts the header's stored Adler-32 checksum field (bytes 8..12,
/// which the checksum does NOT cover) and confirms `dex::verify`
/// rejects it with exactly `BadChecksum`. This is deliberately the one
/// integrity field that can be broken in isolation: changing it does
/// not alter the bytes the checksum is computed over, nor the SHA-1
/// signature, so nothing but the checksum check itself can be
/// responsible for the rejection.
fn run_negative_bad_checksum() -> bool {
    let mut corrupted: Vec<u8> = VALID_DEX.to_vec();
    corrupted[8] ^= 0xFF;
    let result = dex::verify(&corrupted);
    let rejected_correctly = matches!(result, Err(DexError::BadChecksum));
    let original_still_valid = dex::verify(VALID_DEX).is_ok();
    serial_println!(
        "  experimental dex-demo: corrupted checksum field -- verify result {:?}, correctly rejected: {}",
        result.err(), rejected_correctly
    );
    rejected_correctly && original_still_valid
}
