//! Minimal DEX (Dalvik Executable) parser -- the Android compatibility
//! track's loader mechanics, same category of progress as `experimental::pe`
//! was for Windows.
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
//! writing a JVM, and completely out of scope here.
//!
//! What it does do, and why that is a real step rather than a count:
//! `parse` validates the fixed header and the `map_list` (the
//! self-describing table of contents every valid DEX must carry);
//! `verify` checks the file's own Adler-32 checksum and SHA-1
//! signature; and `string` / `type_descriptor` / `class_def` /
//! `class_descriptor` resolve the three tables an app's identity
//! lives in -- the MUTF-8 strings, the type descriptors that name
//! them, and the class definitions that say which descriptor each
//! class is and what it extends. A caller can therefore read the
//! class names out of a real file (the boot proof does), which is the
//! first point at which the parser answers something about the app
//! instead of about the container.
//!
//! On top of that, `proto_id` / `method_id` / `type_list` / `class_data`
//! answer a class's METHODS and their signatures: a method's name, its
//! declaring class, its return type and its parameter types, plus the
//! direct/virtual grouping and access flags the class_data encodes. The
//! shorty string is checked against the parameter list, so a proto whose
//! string and types disagree is refused rather than reported.
//!
//! Still absent, and named rather than implied: field_ids (class_data
//! carries field indices and refuses an out-of-range one, but does not
//! resolve field names), `code_item` and therefore every byte of bytecode,
//! annotations, encoded arrays, static values, and the string *pool*
//! de-duplication rules. Those are the next tables, not a hidden part of
//! these ones.
//!
//! Like every other `experimental` parser, stays paranoid about
//! untrusted input: every offset and size is bounds-checked against
//! the actual byte slice before being dereferenced, every count is
//! checked against its section, and every decoded string is validated
//! as MUTF-8 with its declared UTF-16 length matched against the
//! decoded units.

const DEX_MAGIC_PREFIX: [u8; 4] = [b'd', b'e', b'x', b'\n'];
const HEADER_SIZE: u32 = 0x70; // 112 -- fixed for every DEX version
const ENDIAN_TAG_LE: u32 = 0x1234_5678; // this parser only supports little-endian DEX
const TYPE_MAP_LIST: u16 = 0x1000;
const MAP_ITEM_SIZE: usize = 12; // ushort type, ushort unused, uint size, uint offset

/// The `class_def_item.superclass_idx`/`source_file_idx` sentinel for "this
/// class has none": DEX's `NO_INDEX`, not a zero (zero is a real index).
const NO_INDEX: u32 = 0xffff_ffff;

/// The fixed record sizes of the tables this module resolves. They are part of
/// the format, not of a particular file, so they live here rather than being
/// re-derived at each read.
const STRING_ID_SIZE: u32 = 4;
const TYPE_ID_SIZE: u32 = 4;
const PROTO_ID_SIZE: u32 = 12;
const METHOD_ID_SIZE: u32 = 8;
const FIELD_ID_SIZE: u32 = 8;
const CLASS_DEF_SIZE: u32 = 32;
const TYPE_LIST_HEADER: u32 = 4;
const TYPE_LIST_ENTRY: u32 = 2;

use alloc::vec::Vec;

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
    /// A `string_ids` index past `string_ids_size`. The table is small and the
    /// index is a claim; a value past the end is refused rather than wrapped.
    StringIndexOutOfBounds,
    /// A `type_ids` index past `type_ids_size`.
    TypeIndexOutOfBounds,
    /// A `class_defs` index past `class_defs_size`.
    ClassIndexOutOfBounds,
    /// A `string_ids` entry whose `string_data_off` does not land inside the
    /// file. The reader below dereferences this offset, so it is checked first.
    StringDataOutOfBounds,
    /// A ULEB128 that does not terminate inside five bytes, or whose value
    /// does not fit a `u32` as the format requires. Also the error for a
    /// truncated one (the next byte is past EOF).
    BadUleb128,
    /// A `string_data_item` whose MUTF-8 bytes never reach a NUL inside the
    /// file. Real DEX string data is NUL-terminated; a missing terminator is a
    /// malformed file, not a string that happens to end at EOF.
    StringNotTerminated,
    /// Bytes between the ULEB128 length and the terminator that are not a valid
    /// MUTF-8 encoding (a stray continuation byte, a truncated sequence, a
    /// 0xC1 lead, a bare NUL).
    BadMutf8,
    /// The declared `utf16_size` does not equal the number of UTF-16 code
    /// units the MUTF-8 bytes decode to. This is the check that makes the
    /// decoded string's length a fact the file has to agree with, rather than
    /// a number this parser copies into a struct.
    StringLengthMismatch,
    /// A class's `class_idx` resolved to a string that is not a class
    /// descriptor (DEX class descriptors start with `L` and end with `;`).
    BadTypeDescriptor,
    /// A `proto_ids` index past `proto_ids_size`.
    ProtoIndexOutOfBounds,
    /// A `method_ids` index past `method_ids_size`. Raised both by a direct
    /// [`method_id`] call and by a `class_data` member that names a method.
    MethodIndexOutOfBounds,
    /// A `field_ids` index past `field_ids_size`. `class_data` carries field
    /// indices; this module does not resolve field NAMES (field_ids), but it
    /// does refuse an index the table could not answer.
    FieldIndexOutOfBounds,
    /// A `parameters_off` (or other `type_list`) whose size and entries do not
    /// fit inside the file.
    TypeListOutOfBounds,
    /// A `class_data_off` outside the file.
    ClassDataOutOfBounds,
    /// A `class_data` whose four member counts claim more entries than the
    /// bytes remaining could hold. Each entry needs at least two bytes (index
    /// and flags), so a count past that bound is a truncated file, not a long
    /// class.
    ClassDataTruncated,
    /// A proto's shorty string does not have one character per return value
    /// plus one per parameter -- the format's own definition of a shorty, and
    /// the check that ties a proto's string to its type list.
    ShortyLengthMismatch,
    /// A shorty string containing a byte that is not a shorty descriptor
    /// (`V Z B S C I J F D L`, where arrays also shorten to `L`).
    BadShortyDescriptor,
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

/// One `class_def_item`, resolved enough to name the class: the descriptor is
/// read separately by [`class_descriptor`], because a `ClassDef` that carried a
/// slice would tie the struct to the file's lifetime for no benefit.
#[derive(Debug, PartialEq, Eq)]
pub struct ClassDef {
    pub class_idx: u32,
    pub access_flags: u32,
    /// The superclass's TYPE index, or `None` for `NO_INDEX` (`java.lang.Object`
    /// has no superclass in DEX terms).
    pub superclass_idx: Option<u32>,
    /// The source file's STRING index, or `None` when the file does not name
    /// one. Resolved by the caller through [`string`].
    pub source_file_idx: Option<u32>,
    /// The class's `class_data_off`: 0 when the class declares no fields or
    /// methods (a legal, empty class), otherwise the offset [`class_data`]
    /// parses.
    pub class_data_off: u32,
}

fn read_u16(data: &[u8], offset: usize) -> Option<u16> {
    data.get(offset..offset + 2).map(|b| u16::from_le_bytes([b[0], b[1]]))
}
fn read_u32(data: &[u8], offset: usize) -> Option<u32> {
    data.get(offset..offset + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}

/// A header field at a fixed offset, or `TooShort`. Used by the resolvers below
/// so each one reads only the fields it needs and still refuses a truncated
/// file before computing an offset from it.
fn header_u32(data: &[u8], offset: usize) -> Result<u32, DexError> {
    read_u32(data, offset).ok_or(DexError::TooShort)
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

/// Reads one ULEB128 from `data` at `*offset`, advancing the offset past it.
///
/// # Why the bound is five bytes and a `u64` accumulator
///
/// DEX's indices and lengths are `u32`, so a legal ULEB128 is at most five
/// bytes (5 x 7 = 35 bits) and at most `u32::MAX` in value. A six-byte
/// encoding, a continuation bit set on the fifth byte, or a value with bits
/// above 32 set is a malformed file, not a big number -- and a parser that
/// wrapped would turn a malformed file into a plausible index. The accumulator
/// is `u64` so the over-range case is DETECTED rather than silently truncated.
fn read_uleb128(data: &[u8], offset: &mut usize) -> Result<u32, DexError> {
    let mut value = 0u64;
    let mut shift = 0u32;
    for _ in 0..5 {
        let byte = *data.get(*offset).ok_or(DexError::BadUleb128)?;
        *offset += 1;
        value |= ((byte & 0x7f) as u64) << shift;
        if byte & 0x80 == 0 {
            if value > u32::MAX as u64 {
                return Err(DexError::BadUleb128);
            }
            return Ok(value as u32);
        }
        shift += 7;
    }
    Err(DexError::BadUleb128)
}

/// Counts the UTF-16 code units in a DEX MUTF-8 byte string, validating the
/// encoding.
///
/// # What DEX's "MUTF-8" is, and what this accepts
///
/// It is not UTF-8: a NUL is encoded as two bytes (`0xC0 0x80`) so that the
/// byte `0x00` can terminate the string, and a supplementary character is
/// encoded as two three-byte surrogates. This decoder accepts exactly the
/// byte shapes the format permits -- one-byte `0x01..0x7f`, the two-byte NUL,
/// two-byte `0xC2..0xDF`, and three-byte `0xE0..0xEF` sequences with
/// continuation bytes -- and refuses everything else, including the bare NUL
/// that would end the string, `0xC0`/`0xC1` leads (except the NUL pair), and
/// truncated sequences. Each accepted sequence is one UTF-16 code unit, which
/// is what makes the caller's declared `utf16_size` check meaningful.
fn mutf8_utf16_units(bytes: &[u8]) -> Result<u32, DexError> {
    let mut units = 0u32;
    let mut i = 0;
    while i < bytes.len() {
        let lead = bytes[i];
        if lead == 0 || lead < 0x80 {
            if lead == 0 {
                return Err(DexError::BadMutf8);
            }
            i += 1;
        } else if lead == 0xc0 {
            if bytes.get(i + 1) != Some(&0x80) {
                return Err(DexError::BadMutf8);
            }
            i += 2;
        } else if (0xc2..=0xdf).contains(&lead) {
            let second = *bytes.get(i + 1).ok_or(DexError::BadMutf8)?;
            if second & 0xc0 != 0x80 {
                return Err(DexError::BadMutf8);
            }
            i += 2;
        } else if (0xe0..=0xef).contains(&lead) {
            let second = *bytes.get(i + 1).ok_or(DexError::BadMutf8)?;
            let third = *bytes.get(i + 2).ok_or(DexError::BadMutf8)?;
            if second & 0xc0 != 0x80 || third & 0xc0 != 0x80 {
                return Err(DexError::BadMutf8);
            }
            i += 3;
        } else {
            return Err(DexError::BadMutf8);
        }
        units += 1;
    }
    Ok(units)
}

/// Resolves one `string_ids` entry to its bytes, WITHOUT the terminating NUL.
///
/// The read is the format's own: the entry names a `string_data_item`, whose
/// first field is a ULEB128 UTF-16 length, then the MUTF-8 bytes, then a NUL.
/// Both the declared length and the bytes are checked -- the length against
/// the decoded code units, and the bytes against MUTF-8 -- so a string this
/// function answers with is one the file itself has vouched for, not one that
/// merely terminated before EOF.
pub fn string<'a>(data: &'a [u8], index: u32) -> Result<&'a [u8], DexError> {
    if data.len() < HEADER_SIZE as usize {
        return Err(DexError::TooShort);
    }
    let count = header_u32(data, 56)?;
    let ids_off = header_u32(data, 60)?;
    if index >= count {
        return Err(DexError::StringIndexOutOfBounds);
    }
    let id_offset = (ids_off as usize)
        .checked_add(index as usize * STRING_ID_SIZE as usize)
        .ok_or(DexError::StringIndexOutOfBounds)?;
    let data_off = read_u32(data, id_offset).ok_or(DexError::StringDataOutOfBounds)? as usize;
    if data_off >= data.len() {
        return Err(DexError::StringDataOutOfBounds);
    }
    let mut cursor = data_off;
    let declared_utf16 = read_uleb128(data, &mut cursor)?;
    let start = cursor;
    while cursor < data.len() && data[cursor] != 0 {
        cursor += 1;
    }
    if cursor >= data.len() {
        return Err(DexError::StringNotTerminated);
    }
    let bytes = &data[start..cursor];
    let units = mutf8_utf16_units(bytes)?;
    if units != declared_utf16 {
        return Err(DexError::StringLengthMismatch);
    }
    Ok(bytes)
}

/// Resolves one `type_ids` entry to its descriptor string.
///
/// A `type_id_item` is a `string_ids` index -- one hop, not a copy of the
/// string -- so this is where the two tables are tied together, and the
/// index is checked against the string table before it is used.
pub fn type_descriptor<'a>(data: &'a [u8], index: u32) -> Result<&'a [u8], DexError> {
    if data.len() < HEADER_SIZE as usize {
        return Err(DexError::TooShort);
    }
    let count = header_u32(data, 64)?;
    let table_off = header_u32(data, 68)?;
    if index >= count {
        return Err(DexError::TypeIndexOutOfBounds);
    }
    let item_off = (table_off as usize)
        .checked_add(index as usize * TYPE_ID_SIZE as usize)
        .ok_or(DexError::TypeIndexOutOfBounds)?;
    let string_index = read_u32(data, item_off).ok_or(DexError::TypeIndexOutOfBounds)?;
    string(data, string_index)
}

/// Reads one `class_def_item`.
///
/// The class and superclass indices are resolved through [`type_descriptor`]
/// before the answer is returned, so a `ClassDef` this function hands back
/// names types that exist -- an index that points past the type table is
/// refused here rather than surviving as a number the caller might index with
/// later. `NO_INDEX` is the format's "none" sentinel and is the only value
/// that becomes `None`.
pub fn class_def(data: &[u8], index: u32) -> Result<ClassDef, DexError> {
    if data.len() < HEADER_SIZE as usize {
        return Err(DexError::TooShort);
    }
    let count = header_u32(data, 96)?;
    let table_off = header_u32(data, 100)?;
    if index >= count {
        return Err(DexError::ClassIndexOutOfBounds);
    }
    let item_off = (table_off as usize)
        .checked_add(index as usize * CLASS_DEF_SIZE as usize)
        .ok_or(DexError::ClassIndexOutOfBounds)?;
    let class_idx = read_u32(data, item_off).ok_or(DexError::ClassIndexOutOfBounds)?;
    let access_flags = read_u32(data, item_off + 4).ok_or(DexError::ClassIndexOutOfBounds)?;
    let superclass = read_u32(data, item_off + 8).ok_or(DexError::ClassIndexOutOfBounds)?;
    let source_file = read_u32(data, item_off + 16).ok_or(DexError::ClassIndexOutOfBounds)?;
    let class_data_off = read_u32(data, item_off + 24).ok_or(DexError::ClassIndexOutOfBounds)?;

    // Resolve both type indices now: the struct then only ever carries indices
    // that the file's own type table answered for.
    type_descriptor(data, class_idx)?;
    let superclass_idx = if superclass == NO_INDEX {
        None
    } else {
        type_descriptor(data, superclass)?;
        Some(superclass)
    };
    // A source-file string is a STRING index (not a type index), and the
    // sentinel is the same. Resolving it here means `string()` is the only
    // place the MUTF-8 validation can be skipped by accident.
    let source_file_idx = if source_file == NO_INDEX {
        None
    } else {
        string(data, source_file)?;
        Some(source_file)
    };
    // A non-zero class_data_off is dereferenced by `class_data`, so it is
    // checked here, where the class itself is already being validated.
    if class_data_off != 0 && class_data_off as usize >= data.len() {
        return Err(DexError::ClassDataOutOfBounds);
    }
    Ok(ClassDef {
        class_idx,
        access_flags,
        superclass_idx,
        source_file_idx,
        class_data_off,
    })
}

/// The descriptor of the class a `class_defs` entry defines, e.g.
/// `Lnyrqis/hello/Hello;`.
///
/// # Why the descriptor's shape is checked here
///
/// A DEX class descriptor is not any string: it starts with `L` and ends with
/// `;` (an array type would start with `[`, and a primitive descriptor is a
/// single letter -- neither can be a class_def's `class_idx`). A class table
/// that names a string of another shape is malformed, and the caller of this
/// function is about to treat the answer as a class NAME, so the shape check
/// belongs at the point the answer is produced.
pub fn class_descriptor<'a>(data: &'a [u8], index: u32) -> Result<&'a [u8], DexError> {
    let def = class_def(data, index)?;
    let descriptor = type_descriptor(data, def.class_idx)?;
    let shaped = descriptor.first() == Some(&b'L') && descriptor.last() == Some(&b';');
    if !shaped {
        return Err(DexError::BadTypeDescriptor);
    }
    Ok(descriptor)
}

/// One resolved `proto_id_item`: the shape of a method's signature.
///
/// The indices are already checked when this is returned -- the shorty through
/// [`string`], the return type through [`type_descriptor`], and every parameter
/// through the `type_list` [`type_list`] parses -- so a `ProtoId` never names a
/// type the file's own table did not answer for.
#[derive(Debug, PartialEq, Eq)]
pub struct ProtoId {
    pub shorty_idx: u32,
    pub return_type_idx: u32,
    pub parameters_off: u32,
}

/// One resolved `method_id_item`: which class declares a method, its signature,
/// and its name.
#[derive(Debug, PartialEq, Eq)]
pub struct MethodId {
    pub class_idx: u32,
    pub proto_idx: u32,
    pub name_idx: u32,
}

/// One encoded field or method entry inside a `class_data_item`: the table
/// index and the access flags. Field indices are not resolved to names (that is
/// `field_ids`, still absent); method indices are validated as methods.
#[derive(Debug, PartialEq, Eq)]
pub struct ClassMember {
    pub index: u32,
    pub access_flags: u32,
}

/// A class's parsed `class_data_item`: its fields and methods, in the format's
/// four groups. Empty groups are empty vectors, not `None` -- a class with no
/// fields is a fact, and [`class_data`] returns `None` only for a class that has
/// no `class_data_item` at all (`class_data_off == 0`).
#[derive(Debug, PartialEq, Eq)]
pub struct ClassData {
    pub static_fields: Vec<ClassMember>,
    pub instance_fields: Vec<ClassMember>,
    pub direct_methods: Vec<ClassMember>,
    pub virtual_methods: Vec<ClassMember>,
}

/// Whether a byte is one of the DEX shorty descriptors (`L` also stands for
/// arrays, whose real descriptor starts with `[`).
fn is_shorty_descriptor(byte: u8) -> bool {
    matches!(
        byte,
        b'V' | b'Z' | b'B' | b'S' | b'C' | b'I' | b'J' | b'F' | b'D' | b'L'
    )
}

/// Reads a `type_list` at `off` into the type indices it names, resolving each
/// through [`type_descriptor`].
///
/// `off == 0` is the format's "no list" and answers an empty vector; any other
/// offset must land in the file together with its declared size and entries.
/// The size is bounded against the file BEFORE the vector is allocated, so a
/// malformed count cannot make the parser reserve memory it cannot fill.
pub fn type_list(data: &[u8], off: u32) -> Result<Vec<u32>, DexError> {
    if off == 0 {
        return Ok(Vec::new());
    }
    let off = off as usize;
    let size = read_u32(data, off).ok_or(DexError::TypeListOutOfBounds)?;
    let entries_end = (off as u64)
        .checked_add(TYPE_LIST_HEADER as u64)
        .and_then(|v| v.checked_add(size as u64 * TYPE_LIST_ENTRY as u64))
        .ok_or(DexError::TypeListOutOfBounds)?;
    if entries_end > data.len() as u64 {
        return Err(DexError::TypeListOutOfBounds);
    }
    let mut types = Vec::with_capacity(size as usize);
    for i in 0..size {
        let entry = off + TYPE_LIST_HEADER as usize + i as usize * TYPE_LIST_ENTRY as usize;
        let type_idx = read_u16(data, entry).ok_or(DexError::TypeListOutOfBounds)? as u32;
        type_descriptor(data, type_idx)?;
        types.push(type_idx);
    }
    Ok(types)
}

/// Reads one `proto_id_item` and validates all three of its claims.
///
/// Besides the table index, this checks the two things a caller would otherwise
/// have to remember: the shorty string resolves AND has exactly one character
/// per return value plus one per parameter (so the string and the type list
/// agree), and every parameter type exists. That makes "a method's signature"
/// an answer the file has been made to vouch for, not a tuple of numbers.
pub fn proto_id(data: &[u8], index: u32) -> Result<ProtoId, DexError> {
    if data.len() < HEADER_SIZE as usize {
        return Err(DexError::TooShort);
    }
    let count = header_u32(data, 72)?;
    let table_off = header_u32(data, 76)?;
    if index >= count {
        return Err(DexError::ProtoIndexOutOfBounds);
    }
    let item_off = (table_off as usize)
        .checked_add(index as usize * PROTO_ID_SIZE as usize)
        .ok_or(DexError::ProtoIndexOutOfBounds)?;
    let shorty_idx = read_u32(data, item_off).ok_or(DexError::ProtoIndexOutOfBounds)?;
    let return_type_idx = read_u32(data, item_off + 4).ok_or(DexError::ProtoIndexOutOfBounds)?;
    let parameters_off = read_u32(data, item_off + 8).ok_or(DexError::ProtoIndexOutOfBounds)?;

    let shorty = string(data, shorty_idx)?;
    type_descriptor(data, return_type_idx)?;
    let parameters = type_list(data, parameters_off)?;

    if shorty.len() != parameters.len() + 1 {
        return Err(DexError::ShortyLengthMismatch);
    }
    for &byte in shorty {
        if !is_shorty_descriptor(byte) {
            return Err(DexError::BadShortyDescriptor);
        }
    }
    Ok(ProtoId {
        shorty_idx,
        return_type_idx,
        parameters_off,
    })
}

/// Reads one `method_id_item`, resolving its class, signature and name.
///
/// The record is `u2 class_idx, u2 proto_idx, u4 name_idx` -- 8 bytes, and the
/// mixed widths are the part a hand-written reader gets wrong first, so each
/// field is read at its own width rather than as three words.
pub fn method_id(data: &[u8], index: u32) -> Result<MethodId, DexError> {
    if data.len() < HEADER_SIZE as usize {
        return Err(DexError::TooShort);
    }
    let count = header_u32(data, 88)?;
    let table_off = header_u32(data, 92)?;
    if index >= count {
        return Err(DexError::MethodIndexOutOfBounds);
    }
    let item_off = (table_off as usize)
        .checked_add(index as usize * METHOD_ID_SIZE as usize)
        .ok_or(DexError::MethodIndexOutOfBounds)?;
    let class_idx = read_u16(data, item_off).ok_or(DexError::MethodIndexOutOfBounds)? as u32;
    let proto_idx = read_u16(data, item_off + 2).ok_or(DexError::MethodIndexOutOfBounds)? as u32;
    let name_idx = read_u32(data, item_off + 4).ok_or(DexError::MethodIndexOutOfBounds)?;

    type_descriptor(data, class_idx)?;
    proto_id(data, proto_idx)?;
    string(data, name_idx)?;
    Ok(MethodId {
        class_idx,
        proto_idx,
        name_idx,
    })
}

/// Parses one `class_data_item`, in the format's order: static fields, instance
/// fields, direct methods, virtual methods.
///
/// Returns `None` when the class has no `class_data_item` (`class_data_off` is
/// 0) and `Some` with empty vectors when it has one that declares nothing. Each
/// method index is resolved through [`method_id`]; each field index is checked
/// against `field_ids_size` (field names are not resolved yet).
pub fn class_data(data: &[u8], class_index: u32) -> Result<Option<ClassData>, DexError> {
    let def = class_def(data, class_index)?;
    if def.class_data_off == 0 {
        return Ok(None);
    }
    let mut cursor = def.class_data_off as usize;
    if cursor >= data.len() {
        return Err(DexError::ClassDataOutOfBounds);
    }
    let static_count = read_uleb128(data, &mut cursor)?;
    let instance_count = read_uleb128(data, &mut cursor)?;
    let direct_count = read_uleb128(data, &mut cursor)?;
    let virtual_count = read_uleb128(data, &mut cursor)?;
    Ok(Some(ClassData {
        static_fields: class_members(data, &mut cursor, static_count, false)?,
        instance_fields: class_members(data, &mut cursor, instance_count, false)?,
        direct_methods: class_members(data, &mut cursor, direct_count, true)?,
        virtual_methods: class_members(data, &mut cursor, virtual_count, true)?,
    }))
}

/// Reads `count` encoded members from `*cursor`, validating each index.
///
/// The count is checked against the bytes actually left (two per member) BEFORE
/// the vector is allocated: a `class_data` whose counts claim more entries than
/// the file holds is refused as truncated rather than trusted and then run out
/// of bytes.
fn class_members(
    data: &[u8],
    cursor: &mut usize,
    count: u32,
    methods: bool,
) -> Result<Vec<ClassMember>, DexError> {
    let remaining = data.len().saturating_sub(*cursor);
    if count as u64 * 2 > remaining as u64 {
        return Err(DexError::ClassDataTruncated);
    }
    let field_count = header_u32(data, 80)?;
    let mut members = Vec::with_capacity(count as usize);
    for _ in 0..count {
        let index = read_uleb128(data, cursor)?;
        let access_flags = read_uleb128(data, cursor)?;
        if methods {
            method_id(data, index)?;
        } else if index >= field_count {
            return Err(DexError::FieldIndexOutOfBounds);
        }
        members.push(ClassMember {
            index,
            access_flags,
        });
    }
    Ok(members)
}
