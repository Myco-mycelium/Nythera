//! Minimal DEX (Dalvik Executable) header parser -- the first real
//! step into Phase 6 (Android compatibility), same category of
//! progress as `experimental::pe` was for Phase 5.
//!
//! **Read this before assuming more than it says.** Android's
//! *native* code (anything compiled from C/C++/Rust via the NDK)
//! already uses the ELF format `experimental::elf` handles -- there is
//! no separate "Android native loader" needed for that. What's
//! actually Android-specific is DEX: the bytecode container format
//! for an app's Java/Kotlin code, which the Android RunTime (ART)
//! compiles and executes. **This module parses the DEX *container
//! format* -- it does not execute any bytecode.** Executing DEX
//! bytecode needs a real interpreter (or an AOT compiler, which is
//! what ART actually does) -- an undertaking comparable in scope to
//! writing a JVM, and completely out of scope here. This is the
//! loader-mechanics equivalent of `experimental::pe`'s first
//! increment: prove the *format* can be parsed correctly, before any
//! claim about running what's inside it.
//!
//! Parses the fixed 112-byte DEX header and the `map_list` structure
//! every valid DEX file is required to have (a self-describing table
//! of contents -- even an empty DEX with no classes still needs a
//! `map_list` with exactly one entry describing itself). Extracts
//! section counts (strings, types, methods, classes, ...) and
//! validates internal consistency (`header_size`/`endian_tag` fixed
//! values, every declared offset within the file). [`verify`] now ALSO
//! checks the `checksum` (Adler-32) and `signature` (SHA-1) fields,
//! using the primitives in `experimental::crypto` -- closing what was
//! previously a named gap ("parsed but not verified -- no
//! Adler-32/SHA-1 implementation in this kernel yet").
//!
//! Like every other `experimental` parser, stays paranoid about
//! untrusted input: every offset and size is bounds-checked against
//! the actual byte slice before being dereferenced.

const DEX_MAGIC_PREFIX: [u8; 4] = [b'd', b'e', b'x', b'\n'];
const HEADER_SIZE: u32 = 0x70; // 112 -- fixed for every DEX version
const ENDIAN_TAG_LE: u32 = 0x1234_5678; // this parser only supports little-endian DEX
const TYPE_MAP_LIST: u16 = 0x1000;
const MAP_ITEM_SIZE: usize = 12; // ushort type, ushort unused, uint size, uint offset

#[derive(Debug)]
pub enum DexError {
    TooShort,
    BadMagic,
    UnsupportedVersion,
    BadHeaderSize,
    BadEndianTag, // includes big-endian DEX, which this parser doesn't support
    FileSizeMismatch,
    MapOffOutOfBounds,
    MapListOutOfBounds,
    NoMapListEntry, // every valid DEX must describe itself in its own map
    SectionOutOfBounds,
    BadChecksum,  // Adler-32 over bytes[12..] doesn't match the header field
    BadSignature, // SHA-1 over bytes[32..] doesn't match the header field
}

#[derive(Debug)]
pub struct DexInfo {
    pub file_size: u32,
    pub string_ids_count: u32,
    pub type_ids_count: u32,
    pub proto_ids_count: u32,
    pub field_ids_count: u32,
    pub method_ids_count: u32,
    pub class_defs_count: u32,
    pub map_entry_count: u32,
}

fn read_u16(data: &[u8], offset: usize) -> Option<u16> {
    data.get(offset..offset + 2).map(|b| u16::from_le_bytes([b[0], b[1]]))
}
fn read_u32(data: &[u8], offset: usize) -> Option<u32> {
    data.get(offset..offset + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}

/// Checks a `(size, off)` pair against the file: if `size > 0`, the
/// range `[off, off + size*entry_size)` must fit within `data`. A
/// zero-sized section's offset is not checked (per the DEX spec, an
/// empty section's offset is meaningless and real DEX files vary in
/// what they put there).
fn check_section_bounds(data_len: usize, count: u32, off: u32, entry_size: u32) -> Result<(), DexError> {
    if count == 0 {
        return Ok(());
    }
    let end = (off as u64)
        .checked_add(count as u64 * entry_size as u64)
        .ok_or(DexError::SectionOutOfBounds)?;
    if end > data_len as u64 {
        return Err(DexError::SectionOutOfBounds);
    }
    Ok(())
}

/// Parses `data` as a DEX file. Returns section counts and the number
/// of entries in the file's own `map_list` (a basic structural-
/// integrity signal: a well-formed DEX always has at least one map
/// entry, describing the map itself).
pub fn parse(data: &[u8]) -> Result<DexInfo, DexError> {
    if data.len() < HEADER_SIZE as usize {
        return Err(DexError::TooShort);
    }
    if data[0..4] != DEX_MAGIC_PREFIX {
        return Err(DexError::BadMagic);
    }
    // Bytes 4..7 are a 3-digit ASCII version ("035", "037", ...) then
    // a NUL. This parser doesn't need to distinguish versions for
    // header-level parsing (the fixed header layout used here has
    // been stable across all released DEX versions), but it does
    // require the NUL terminator to be present as a basic sanity check
    // that this really is a DEX magic, not four coincidental bytes.
    if data[7] != 0 {
        return Err(DexError::UnsupportedVersion);
    }

    let checksum_and_sig_end = 32; // offset where file_size begins
    let file_size = read_u32(data, checksum_and_sig_end).ok_or(DexError::TooShort)?;
    let header_size = read_u32(data, 36).ok_or(DexError::TooShort)?;
    let endian_tag = read_u32(data, 40).ok_or(DexError::TooShort)?;

    if header_size != HEADER_SIZE {
        return Err(DexError::BadHeaderSize);
    }
    if endian_tag != ENDIAN_TAG_LE {
        return Err(DexError::BadEndianTag);
    }
    if file_size as usize != data.len() {
        // A real Android loader treats a DEX embedded inside a larger
        // container (e.g. inside an APK's ZIP central directory
        // framing) differently -- this parser only handles a
        // standalone .dex file, where file_size must match exactly.
        return Err(DexError::FileSizeMismatch);
    }

    let map_off = read_u32(data, 52).ok_or(DexError::TooShort)?;
    let string_ids_size = read_u32(data, 56).ok_or(DexError::TooShort)?;
    let string_ids_off = read_u32(data, 60).ok_or(DexError::TooShort)?;
    let type_ids_size = read_u32(data, 64).ok_or(DexError::TooShort)?;
    let type_ids_off = read_u32(data, 68).ok_or(DexError::TooShort)?;
    let proto_ids_size = read_u32(data, 72).ok_or(DexError::TooShort)?;
    let proto_ids_off = read_u32(data, 76).ok_or(DexError::TooShort)?;
    let field_ids_size = read_u32(data, 80).ok_or(DexError::TooShort)?;
    let field_ids_off = read_u32(data, 84).ok_or(DexError::TooShort)?;
    let method_ids_size = read_u32(data, 88).ok_or(DexError::TooShort)?;
    let method_ids_off = read_u32(data, 92).ok_or(DexError::TooShort)?;
    let class_defs_size = read_u32(data, 96).ok_or(DexError::TooShort)?;
    let class_defs_off = read_u32(data, 100).ok_or(DexError::TooShort)?;

    // string_id_item = 4 bytes; type_id_item = 4 bytes; proto_id_item =
    // 12 bytes; field_id_item = 8 bytes; method_id_item = 8 bytes;
    // class_def_item = 32 bytes -- fixed DEX record sizes.
    check_section_bounds(data.len(), string_ids_size, string_ids_off, 4)?;
    check_section_bounds(data.len(), type_ids_size, type_ids_off, 4)?;
    check_section_bounds(data.len(), proto_ids_size, proto_ids_off, 12)?;
    check_section_bounds(data.len(), field_ids_size, field_ids_off, 8)?;
    check_section_bounds(data.len(), method_ids_size, method_ids_off, 8)?;
    check_section_bounds(data.len(), class_defs_size, class_defs_off, 32)?;

    if (map_off as usize) + 4 > data.len() {
        return Err(DexError::MapOffOutOfBounds);
    }
    let map_entry_count = read_u32(data, map_off as usize).ok_or(DexError::MapListOutOfBounds)?;
    let map_list_end = (map_off as u64)
        .checked_add(4)
        .and_then(|v| v.checked_add(map_entry_count as u64 * MAP_ITEM_SIZE as u64))
        .ok_or(DexError::MapListOutOfBounds)?;
    if map_list_end > data.len() as u64 {
        return Err(DexError::MapListOutOfBounds);
    }

    // Confirm the map genuinely describes itself -- a real structural
    // check, not just "the count field is nonzero": walk every map
    // entry looking for a TYPE_MAP_LIST entry whose own offset matches
    // map_off. A DEX file that claims entries but never actually
    // describes its own map table is malformed, not just unusual.
    let mut found_self_entry = false;
    for i in 0..map_entry_count {
        let item_off = map_off as usize + 4 + (i as usize) * MAP_ITEM_SIZE;
        let item_type = read_u16(data, item_off).ok_or(DexError::MapListOutOfBounds)?;
        let item_file_off = read_u32(data, item_off + 8).ok_or(DexError::MapListOutOfBounds)?;
        if item_type == TYPE_MAP_LIST && item_file_off == map_off {
            found_self_entry = true;
        }
    }
    if !found_self_entry {
        return Err(DexError::NoMapListEntry);
    }

    Ok(DexInfo {
        file_size,
        string_ids_count: string_ids_size,
        type_ids_count: type_ids_size,
        proto_ids_count: proto_ids_size,
        field_ids_count: field_ids_size,
        method_ids_count: method_ids_size,
        class_defs_count: class_defs_size,
        map_entry_count,
    })
}

/// Verifies the DEX file's integrity fields -- the Adler-32 `checksum`
/// (header offset 8, computed over bytes `[12..]`) and the SHA-1
/// `signature` (header offset 12, computed over bytes `[32..]`).
///
/// Kept separate from [`parse`] on purpose: `parse` is pure structural
/// validation (and its negative tests corrupt the magic/map, which
/// would otherwise be masked by a checksum failure raised first). A
/// caller that wants "this is a genuine, unmodified DEX file" calls
/// both -- `parse` then `verify` -- exactly as `dex_demo` does.
///
/// This closes the "checksum/signature parsed but not verified -- no
/// Adler-32/SHA-1 implementation in this kernel yet" gap the module
/// doc names; the algorithms and their known-answer tests live in
/// `experimental::crypto`.
pub fn verify(data: &[u8]) -> Result<(), DexError> {
    if data.len() < HEADER_SIZE as usize {
        return Err(DexError::TooShort);
    }
    let stored_checksum = read_u32(data, 8).ok_or(DexError::TooShort)?;
    let computed_checksum = crate::experimental::crypto::adler32(&data[12..]);
    if computed_checksum != stored_checksum {
        return Err(DexError::BadChecksum);
    }
    let stored_signature = &data[12..32];
    let computed_signature = crate::experimental::crypto::sha1(&data[32..]);
    if computed_signature != stored_signature {
        return Err(DexError::BadSignature);
    }
    Ok(())
}
