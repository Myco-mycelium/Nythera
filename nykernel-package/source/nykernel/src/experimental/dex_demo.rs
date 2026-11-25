//! DEX parsing demo -- the Android compatibility track's capability proof,
//! same category of progress as `experimental::pe` was for Windows. See
//! `experimental::dex`'s module doc for the honest scope: this parses the DEX
//! *container format* and resolves its string/type/class tables, it does not
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

/// A second real DEX file (`userprog/classes_hello.dex`, built by
/// `tools/make_dex_fixture.py`), this one with contents: three strings, two
/// type descriptors, and one class definition. Its header, map list, checksum
/// and signature are the same format facts as the empty file's -- what it adds
/// is the tables the resolvers below read.
static CLASS_DEX: &[u8] = include_bytes!("../../userprog/classes_hello.dex");

/// The fixture's fixed layout, repeated here because the negative controls
/// corrupt specific fields and a control that patched the wrong byte would
/// pass for the wrong reason. `tools/make_dex_fixture.py` asserts every one of
/// these before it writes the file.
const STRING_IDS_OFF: usize = 0x70;
const FIRST_STRING_DATA_OFF: usize = 164;
/// The index of the last string’s terminating NUL -- i.e. where a file
/// that ends before its terminator stops. Used by the truncation control.
const LAST_STRING_TERMINATOR_OFF: usize = 217;
const FIRST_CLASS_DESCRIPTOR_OFF: usize = 165;

const EXPECTED_CLASS: &[u8] = b"Lnyrqis/hello/Hello;";
const EXPECTED_SUPER: &[u8] = b"Ljava/lang/Object;";
const EXPECTED_SOURCE: &[u8] = b"Hello.java";

/// Runs the header proof, the table proof and every permanent negative check.
/// Returns `true` iff every part of the proof held.
pub fn run() -> bool {
    let kats_ok = run_known_answer_tests();
    let positive_ok = run_positive();
    let magic_check_ok = run_negative_bad_magic();
    let map_check_ok = run_negative_map_self_reference();
    let checksum_check_ok = run_negative_bad_checksum();
    let tables_ok = run_class_tables();
    let refusals_ok = run_negative_table_refusals();

    serial_println!(
        "  experimental dex-demo self-test: crypto known-answer {}, valid parse+integrity {}, bad-magic rejection {}, map-self-reference rejection {}, checksum rejection {}, class tables {}, table refusals {} -- {}",
        verdict(kats_ok),
        verdict(positive_ok),
        verdict(magic_check_ok),
        verdict(map_check_ok),
        verdict(checksum_check_ok),
        verdict(tables_ok),
        verdict(refusals_ok),
        verdict(kats_ok && positive_ok && magic_check_ok && map_check_ok && checksum_check_ok && tables_ok && refusals_ok)
    );

    kats_ok
        && positive_ok
        && magic_check_ok
        && map_check_ok
        && checksum_check_ok
        && tables_ok
        && refusals_ok
}

fn verdict(ok: bool) -> &'static str {
    if ok {
        "OK"
    } else {
        "FAILED"
    }
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

/// The tables with contents: the string pool, the type descriptors that index
/// it, and the class definition that names both. Each fact is checked against
/// the fixture the generator wrote, and the class's descriptor is compared BY
/// BYTES -- a parser that resolved "a string" but the wrong one would fail here
/// rather than pass on a non-empty answer.
fn run_class_tables() -> bool {
    let info = match dex::parse(CLASS_DEX) {
        Ok(info) => info,
        Err(e) => {
            serial_println!("  experimental dex-demo: FAILED to parse classes_hello.dex: {:?}", e);
            return false;
        }
    };
    let integrity_ok = dex::verify(CLASS_DEX).is_ok();
    let counts_ok = info.file_size == CLASS_DEX.len() as u32
        && info.string_ids_count == 3
        && info.type_ids_count == 2
        && info.class_defs_count == 1;
    let strings_ok = matches!(dex::string(CLASS_DEX, 0), Ok(s) if s == EXPECTED_CLASS)
        && matches!(dex::string(CLASS_DEX, 1), Ok(s) if s == EXPECTED_SUPER)
        && matches!(dex::string(CLASS_DEX, 2), Ok(s) if s == EXPECTED_SOURCE);
    let types_ok = matches!(dex::type_descriptor(CLASS_DEX, 0), Ok(s) if s == EXPECTED_CLASS)
        && matches!(dex::type_descriptor(CLASS_DEX, 1), Ok(s) if s == EXPECTED_SUPER);
    let def = dex::class_def(CLASS_DEX, 0);
    let class_ok = match def {
        Ok(def) => {
            def.class_idx == 0
                && def.access_flags == 0x1
                && def.superclass_idx == Some(1)
                && def.source_file_idx == Some(2)
        }
        Err(_) => false,
    };
    // The descriptor is the answer a class loader would key on, so it is
    // checked through its own entry point rather than re-derived from the
    // indices above.
    let descriptor_ok = matches!(dex::class_descriptor(CLASS_DEX, 0), Ok(s) if s == EXPECTED_CLASS);

    let ok = integrity_ok && counts_ok && strings_ok && types_ok && class_ok && descriptor_ok;
    serial_println!(
        "  experimental dex-demo: class tables -- a {} byte DEX with {} string(s), {} type(s), {} class(es): string 0/1/2 answer \"{}\"/\"{}\"/\"{}\", type 0/1 resolve them as descriptors, and class 0 is {} (public, flags {:#x}) extending {} with source file {} -- {}",
        info.file_size,
        info.string_ids_count,
        info.type_ids_count,
        info.class_defs_count,
        "Lnyrqis/hello/Hello;",
        "Ljava/lang/Object;",
        "Hello.java",
        "Lnyrqis/hello/Hello;",
        0x1,
        "Ljava/lang/Object;",
        "Hello.java",
        if ok { "OK" } else { "MISMATCH" }
    );
    ok
}

/// Every malformed-table shape the resolvers must refuse, each exercised on a
/// copy of the real fixture with exactly one field broken, so the rejection
/// cannot be explained by any other check.
fn run_negative_table_refusals() -> bool {
    let string_offset = {
        let mut corrupted: Vec<u8> = CLASS_DEX.to_vec();
        corrupted[STRING_IDS_OFF..STRING_IDS_OFF + 4].copy_from_slice(&0xffffu32.to_le_bytes());
        matches!(dex::string(&corrupted, 0), Err(DexError::StringDataOutOfBounds))
    };

    let overlong_uleb = {
        let mut corrupted: Vec<u8> = CLASS_DEX.to_vec();
        // Five continuation bytes: a ULEB128 that never terminates inside the
        // format's five-byte bound.
        for i in 0..5 {
            corrupted[FIRST_STRING_DATA_OFF + i] = 0x80;
        }
        matches!(dex::string(&corrupted, 0), Err(DexError::BadUleb128))
    };

    let length_mismatch = {
        let mut corrupted: Vec<u8> = CLASS_DEX.to_vec();
        // The class descriptor is 20 bytes, i.e. 20 UTF-16 units; declare 19.
        corrupted[FIRST_STRING_DATA_OFF] = 19;
        matches!(dex::string(&corrupted, 0), Err(DexError::StringLengthMismatch))
    };

    let unterminated = {
        // The terminator of the LAST string is the last file byte before the
        // map_list, so overwriting it only makes the scan run into the map --
        // it still finds a NUL and fails on the length instead. The actual
        // "no terminator" case is a file that ENDS before one, so the fixture
        // is truncated at the last string's bytes. (`string` reads only the
        // header and the offsets it needs, so a short slice is exactly the
        // malformed file this check is about.)
        let truncated: Vec<u8> = CLASS_DEX[..LAST_STRING_TERMINATOR_OFF].to_vec();
        matches!(dex::string(&truncated, 2), Err(DexError::StringNotTerminated))
    };

    let type_past_end = matches!(
        dex::type_descriptor(CLASS_DEX, 2),
        Err(DexError::TypeIndexOutOfBounds)
    );
    let class_past_end = matches!(
        dex::class_def(CLASS_DEX, 1),
        Err(DexError::ClassIndexOutOfBounds)
    );
    let bad_descriptor = {
        let mut corrupted: Vec<u8> = CLASS_DEX.to_vec();
        corrupted[FIRST_CLASS_DESCRIPTOR_OFF] = b'X';
        matches!(
            dex::class_descriptor(&corrupted, 0),
            Err(DexError::BadTypeDescriptor)
        )
    };

    let ok = string_offset
        && overlong_uleb
        && length_mismatch
        && unterminated
        && type_past_end
        && class_past_end
        && bad_descriptor;
    serial_println!(
        "  experimental dex-demo: table refusals -- a string_data offset past EOF {}, an over-long ULEB128 {}, a declared UTF-16 length of 19 for 20 units {}, an unterminated string {}, a type index past the table {}, a class index past the table {}, and a class descriptor that does not start with L {} -- {}",
        if string_offset { "refused StringDataOutOfBounds" } else { "ACCEPTED" },
        if overlong_uleb { "refused BadUleb128" } else { "ACCEPTED" },
        if length_mismatch { "refused StringLengthMismatch" } else { "ACCEPTED" },
        if unterminated { "refused StringNotTerminated" } else { "ACCEPTED" },
        if type_past_end { "refused TypeIndexOutOfBounds" } else { "ACCEPTED" },
        if class_past_end { "refused ClassIndexOutOfBounds" } else { "ACCEPTED" },
        if bad_descriptor { "refused BadTypeDescriptor" } else { "ACCEPTED" },
        verdict(ok)
    );
    ok
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
