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
//! `field_id` answers the same question for a class's FIELDS -- declaring
//! class, type and name -- and `code_item` reads the header and the raw 16-bit
//! code units of a method that has code, so a member table can say both what a
//! field IS and whether a method has a body (and how many words it is).
//!
//! `annotations_directory` answers a class's annotations (the directory's class,
//! field, method and parameter rows, each resolved through its
//! `annotation_set_item` to `annotation_item`s), and `encoded_array` /
//! `static_values` read the `encoded_array_item` format: a ULEB128 count plus
//! that many `encoded_value`s, whose one header byte packs the value TYPE in its
//! low five bits and a `width - 1` in its top three. Every value form the
//! format defines is decoded -- the integer and floating widths, string, type,
//! field, method, enum and method-type indices, nested arrays and nested
//! annotations, null and boolean -- and each one's declared width is the check
//! that says how many bytes to read, so an index cannot be assembled from the
//! wrong number of them.
//!
//! The `map_list` is read as the file's table of contents rather than scanned
//! for one entry: [`map_list`] returns typed [`MapEntry`]s and checks that every
//! item type is one the format defines, that the entries' offsets strictly
//! increase, that each fixed-size section fits inside the file, that the
//! header's own counts and offsets agree with the entries describing them, and
//! that the map contains its own entry. That reading is what makes three of the
//! format's sections reachable at all: `method_handles` and `call_site_ids` have
//! no header field (they arrived in version 038), and a parameter annotation's
//! `annotation_set_ref_list` is a section in its own right. [`method_handle`]
//! resolves a handle's target through the table its type implies (fields for the
//! accessors, methods for the invokers); [`call_site`] enforces the
//! three-element `(handle, name, method type)` array the format defines for a
//! call site; [`annotation_set_ref_list`] gives a directory's parameter row the
//! one-set-per-parameter level that the format says it names; and
//! [`debug_info`] runs the `debug_info_item` state machine to
//! `DBG_END_SEQUENCE`, which is the only way to find where that item ends.
//!
//! Two rules of the format are load-bearing here rather than incidental, and
//! both are the kind a hand-written reader gets wrong on a small fixture:
//!
//! - `class_data` member indices are DIFFERENTIALS: each entry's
//!   `field_idx_diff`/`method_idx_diff` is a delta from the previous entry in
//!   the SAME list, and every list restarts from its own first delta. This
//!   parser accumulates them per list ([`class_members`]) and validates the
//!   resulting absolute index, so an index it reports is one the table can
//!   answer rather than an offset a caller has to remember to add.
//! - An `encoded_method` is THREE ULEB128s -- `method_idx_diff`,
//!   `access_flags`, `code_off` -- while an `encoded_field` is two. The third
//!   field is where a method's code lives, and its absence is what
//!   `ACC_NATIVE`/`ACC_ABSTRACT` means; [`class_members`] refuses a method
//!   whose flags and `code_off` disagree, and parses each non-zero `code_off`
//!   through [`code_item`] so a class_data entry cannot carry a body nothing
//!   checked.
//!
//! Still absent, and named rather than implied: the MEANING of the code units
//! `code_item` returns (decoding, verifying and executing Dalvik instructions is
//! an interpreter or an AOT compiler -- a different undertaking, not a table
//! this module forgot), the MEANING of an annotation (a `VALUE_ENUM`'s field is
//! resolved as a field, not proven to be an enum constant; a parameter
//! annotation's set count is not compared to a proto's parameter list), the
//! string *pool* de-duplication rules, and ART's private
//! `hiddenapi_class_data_item`, which the map recognises as a known entry but
//! does not read: its layout is outside the public format document this parser
//! follows, so guessing it would be worse than leaving it named. Those are the
//! next questions, not a hidden part of these tables.
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
/// `map_item.type`, the format's whole table. The first nine are fixed-size id
/// tables whose `size` is a count and whose extent is `size * item size`; the
/// rest are data-area items whose `size` is a count of items and whose bytes are
/// only known by parsing each one.
const TYPE_HEADER_ITEM: u16 = 0x0000;
const TYPE_STRING_ID_ITEM: u16 = 0x0001;
const TYPE_TYPE_ID_ITEM: u16 = 0x0002;
const TYPE_PROTO_ID_ITEM: u16 = 0x0003;
const TYPE_FIELD_ID_ITEM: u16 = 0x0004;
const TYPE_METHOD_ID_ITEM: u16 = 0x0005;
const TYPE_CLASS_DEF_ITEM: u16 = 0x0006;
const TYPE_CALL_SITE_ID_ITEM: u16 = 0x0007;
const TYPE_METHOD_HANDLE_ITEM: u16 = 0x0008;
const TYPE_MAP_LIST: u16 = 0x1000;
const TYPE_TYPE_LIST: u16 = 0x1001;
const TYPE_ANNOTATION_SET_REF_LIST: u16 = 0x1002;
const TYPE_ANNOTATION_SET_ITEM: u16 = 0x1003;
const TYPE_CLASS_DATA_ITEM: u16 = 0x2000;
const TYPE_CODE_ITEM: u16 = 0x2001;
const TYPE_STRING_DATA_ITEM: u16 = 0x2002;
const TYPE_DEBUG_INFO_ITEM: u16 = 0x2003;
const TYPE_ANNOTATION_ITEM: u16 = 0x2004;
const TYPE_ENCODED_ARRAY_ITEM: u16 = 0x2005;
const TYPE_ANNOTATIONS_DIRECTORY_ITEM: u16 = 0x2006;
/// ART's private boot-class-path table: the map names it, but its layout is not
/// part of the public `dex-format` document this parser follows, so the entry is
/// recognised (a boot-class-path file is not refused as malformed) and left
/// unread rather than guessed at.
const TYPE_HIDDENAPI_CLASS_DATA_ITEM: u16 = 0xf000;
const MAP_ITEM_SIZE: usize = 12; // ushort type, ushort unused, uint size, uint offset

/// `call_site_id_item` and `method_handle_item` sizes. Both tables exist only in
/// the `map_list` -- the DEX header has no fields for them (they arrived in
/// version 038) -- which is why reading either one is a question about the map
/// before it is a question about an item.
const CALL_SITE_ID_SIZE: u32 = 4;
const METHOD_HANDLE_ITEM_SIZE: u32 = 8;
/// `method_handle_item.method_handle_type`: four field accessors, then five
/// method invokers. The split is the rule that says which id table the item's
/// `field_or_method_id` indexes.
const HANDLE_TYPE_STATIC_PUT: u16 = 0x00;
const HANDLE_TYPE_INSTANCE_GET: u16 = 0x03;
const HANDLE_TYPE_INVOKE_STATIC: u16 = 0x04;
const HANDLE_TYPE_INVOKE_INTERFACE: u16 = 0x08;

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
/// `code_item`'s fixed header: registers_size, ins_size, outs_size,
/// tries_size, debug_info_off, insns_size -- four `u2` then two `u4`.
const CODE_ITEM_HEADER: u32 = 16;
/// One `try_item` (start_addr, insn_count, handler_off), used only to bound the
/// tries a code_item declares; this module does not decode handlers.
const TRY_ITEM_SIZE: u32 = 8;

/// `annotations_directory_item`'s fixed header: `class_annotations_off` then
/// three counts -- four `u4`s. Each row after it is `member_idx` + `annotations_off`.
const ANNOTATIONS_DIRECTORY_HEADER: u32 = 16;
const ANNOTATION_ROW: u32 = 8;
/// `annotation_set_item`: a `u4` size then that many `u4` item offsets.
const ANNOTATION_SET_HEADER: u32 = 4;
const ANNOTATION_SET_ENTRY: u32 = 4;

/// How deep nested `VALUE_ARRAY`/`VALUE_ANNOTATION` values may go. The format
/// states no limit, so this is this parser's own bound -- the same kind of
/// decision as refusing a ULEB128 longer than five bytes: real files nest a
/// handful of levels, and a bound is what keeps a cyclic-looking file from
/// consuming the stack before any check can run.
pub const MAX_ENCODED_VALUE_DEPTH: u32 = 8;

/// `annotation_item.visibility`: compiled-out, runtime-visible, or visible to
/// the system only. The three values are the whole domain.
pub const VISIBILITY_BUILD: u8 = 0;
pub const VISIBILITY_RUNTIME: u8 = 1;
pub const VISIBILITY_SYSTEM: u8 = 2;

/// `encoded_value` header types (`header & 0x1f`).
const VALUE_BYTE: u8 = 0x00;
const VALUE_SHORT: u8 = 0x02;
const VALUE_CHAR: u8 = 0x03;
const VALUE_INT: u8 = 0x04;
const VALUE_LONG: u8 = 0x06;
const VALUE_FLOAT: u8 = 0x10;
const VALUE_DOUBLE: u8 = 0x11;
const VALUE_METHOD_TYPE: u8 = 0x15;
const VALUE_METHOD_HANDLE: u8 = 0x16;
const VALUE_STRING: u8 = 0x17;
const VALUE_TYPE: u8 = 0x18;
const VALUE_FIELD: u8 = 0x19;
const VALUE_METHOD: u8 = 0x1a;
const VALUE_ENUM: u8 = 0x1b;
const VALUE_ARRAY: u8 = 0x1c;
const VALUE_ANNOTATION: u8 = 0x1d;
const VALUE_NULL: u8 = 0x1e;
const VALUE_BOOLEAN: u8 = 0x1f;

/// `ACC_NATIVE`: a method whose body is outside the DEX file (JNI). It is the
/// format's own reason for a `code_off` of zero.
pub const ACC_NATIVE: u32 = 0x0000_0100;
/// `ACC_ABSTRACT`: a method declared but not implemented, so again no code.
pub const ACC_ABSTRACT: u32 = 0x0000_0400;

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
    /// A `code_off` outside the file (or zero, where a caller asked for a code
    /// item directly -- zero is the format's "no code", not an offset).
    CodeItemOutOfBounds,
    /// A `code_item` whose header, code units or try items claim more bytes than
    /// the file holds. Bounded against the file before any vector is allocated.
    CodeItemTruncated,
    /// An `encoded_method` whose `access_flags` and `code_off` disagree: a
    /// native or abstract method must have no code (`code_off == 0`), and a
    /// method that is neither must have one. The format states both directions,
    /// and a reader that ignored either would report a body that does not exist
    /// or hide one that does.
    MethodCodeMismatch,
    /// A `code_item` whose `ins_size` (the registers holding incoming
    /// arguments) exceeds its `registers_size`. The format requires
    /// `ins_size <= registers_size` because the arguments are the HIGH registers
    /// of the frame, so a file that claims otherwise has no register window the
    /// verifier could accept.
    CodeItemRegisterMismatch,
    /// A `class_def.annotations_off` (or an annotations directory's
    /// `class_annotations_off`) that does not land inside the file. Both are
    /// dereferenced, so both are checked where they are read.
    AnnotationsDirectoryOutOfBounds,
    /// An `annotations_directory_item` whose three counts claim more rows than
    /// the bytes left could hold. Each row is eight bytes, so a count past that
    /// bound is a truncated file, not a large directory.
    AnnotationsDirectoryTruncated,
    /// An `annotation_set_item` offset outside the file. Zero is included: a
    /// row's `annotations_off` is required to name a set, so a zero there is
    /// the format's "nothing" used where nothing is not allowed.
    AnnotationSetOutOfBounds,
    /// An `annotation_set_item` whose size claims more item offsets than the
    /// bytes left could hold.
    AnnotationSetTruncated,
    /// An `annotation_item` offset outside the file (or zero, for the same
    /// reason an `annotation_set_item` of zero is refused).
    AnnotationItemOutOfBounds,
    /// An `annotation_item` that stops before its encoded annotation does -- its
    /// type index, element count, or an element's name index runs past EOF.
    AnnotationItemTruncated,
    /// A visibility byte other than BUILD (0), RUNTIME (1) or SYSTEM (2).
    BadAnnotationVisibility,
    /// An `encoded_value` whose header names a type this format does not define
    /// (0x01, 0x05, 0x07..0x0f, 0x12..0x14 and every value above 0x1f are not
    /// value types).
    BadEncodedValueType,
    /// An `encoded_value` whose header's width field is impossible for its type:
    /// wider than the type's own maximum, or non-zero for a type that has no
    /// width at all (null, array, annotation). The byte that says how many bytes
    /// to read is the whole reason this field exists, so a nonsensical one is
    /// refused rather than clamped.
    BadEncodedValueArg,
    /// An `encoded_array_item` offset outside the file.
    EncodedArrayOutOfBounds,
    /// An `encoded_array_item` whose count claims more values than the bytes
    /// left could hold (every value is at least its one header byte).
    EncodedArrayTruncated,
    /// An `encoded_value` that stops before its own bytes do: the header was
    /// read, and the width it declared runs past EOF.
    EncodedValueTruncated,
    /// Nested arrays or annotations deeper than [`MAX_ENCODED_VALUE_DEPTH`].
    /// Nesting is INLINE in this format (a `VALUE_ARRAY`'s value is an
    /// encoded_array, not an offset), so depth is a property of the bytes and a
    /// reader that recursed without a bound could be driven off its stack.
    EncodedValueTooDeep,
    /// A `map_item.type` outside the format's table: a map that names a section
    /// kind the format does not define is describing something other than a DEX
    /// file's contents.
    UnknownMapItemType,
    /// Two `map_item`s whose offsets do not strictly increase. The format
    /// requires the list in ascending offset order (it is the file's table of
    /// contents, laid out in the order the sections occupy), so a list that
    /// steps backwards is not merely unsorted -- it makes "the section at this
    /// offset" ambiguous.
    MapNotSorted,
    /// A map entry whose section does not fit inside the file: `offset + size *
    /// fixed item size` past EOF for an id table, or the section header itself
    /// past EOF for a variable-length one.
    MapSectionOutOfBounds,
    /// A map entry that contradicts a fact the file states elsewhere: one of the
    /// header-declared tables described at another offset or with another count,
    /// a `header_item` entry other than one item at offset zero, or a
    /// `map_list` entry other than this one. The header and the map are two
    /// descriptions of the same sections, and a file where they disagree has no
    /// single answer to "where does this table start".
    MapSectionMismatch,
    /// A `method_handles` item was asked for in a file whose map names no such
    /// section. Version-038 files may omit it entirely; a caller asking anyway is
    /// asking about a table that does not exist.
    MethodHandleSectionMissing,
    /// A `method_handles` index at or past the section's size.
    MethodHandleIndexOutOfBounds,
    /// A `method_handle_item.method_handle_type` outside 0x00..=0x08.
    BadMethodHandleType,
    /// A `call_site_ids` index at or past the section's size, or a file whose map
    /// names no such section.
    CallSiteSectionMissing,
    /// A `call_site_ids` index at or past the section's size.
    CallSiteIndexOutOfBounds,
    /// A call site's definition is not the three-element array the format says
    /// it is: `(method handle, method name, method type)`. A call site whose
    /// array has another shape names no invocable target, so it is refused rather
    /// than reported as a shorter or longer tuple.
    BadCallSiteShape,
    /// An `annotation_set_ref_list` offset outside the file (or zero, which the
    /// format reserves for "absent" in a place that requires a list).
    AnnotationSetRefListOutOfBounds,
    /// An `annotation_set_ref_list` whose size claims more entries than the bytes
    /// left could hold.
    AnnotationSetRefListTruncated,
    /// A `debug_info_item` offset outside the file (or zero, where a caller asked
    /// for one directly -- zero is the format's "no debug info").
    DebugInfoOutOfBounds,
    /// A `debug_info_item` that stops before its header or its bytecode does.
    DebugInfoTruncated,
    /// A `debug_info_item` opcode in 0x05..=0x08, the range the format leaves
    /// undefined (0x0a..=0xff are the special opcodes).
    BadDebugInfoOpcode,
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
    /// The class's `annotations_off`: 0 when the class has no annotations
    /// directory at all, otherwise the offset [`annotations_directory`] reads.
    pub annotations_off: u32,
    /// The class's `static_values_off`: 0 when the class declares no static
    /// initialisers, otherwise the `encoded_array_item` [`static_values`] reads.
    pub static_values_off: u32,
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

    // The map is the file's own table of contents, and it is validated AS one:
    // every entry's type must be one the format defines, the entries must be in
    // ascending offset order, every fixed-size section must fit inside the file,
    // the header's counts and offsets must agree with the entries describing
    // them, and the list must contain its own entry. That last rule is why this
    // is a real structural check rather than "the count field is nonzero": a DEX
    // that claims entries but never describes its own map is malformed.
    let map_entry_count = map_list(data)?.len() as u32;

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

/// One `map_item`: the kind of item, how many of them, and where they start.
///
/// The `size` field means two different things depending on `item_type`, and
/// that is the format's rule rather than a simplification here: for the nine
/// fixed-size id tables it is a count whose extent is `size * item size`, while
/// for every data-area item it is a count of items whose bytes are only known by
/// parsing each one. [`map_list`] states which reading it applies.
#[derive(Debug, PartialEq, Eq)]
pub struct MapEntry {
    pub item_type: u16,
    pub size: u32,
    pub offset: u32,
}

/// Reads the file's `map_list` as typed entries, validating the list as the
/// file's table of contents.
///
/// # What is checked, and why each check is about the file rather than the list
///
/// - **Known type.** The item type must be one of the twenty the format defines
///   (`UnknownMapItemType`). A map naming anything else is describing a file
///   whose sections this parser cannot even name.
/// - **Ascending offsets.** The format requires the entries in ascending offset
///   order, so a list that steps backwards makes "the section at this offset"
///   ambiguous (`MapNotSorted`).
/// - **Extent inside the file.** For the fixed-size tables this is a real
///   arithmetic check (`offset + size * item size`); for the data-area items it
///   is the section header itself, because their per-item lengths are only
///   discoverable by parsing (`MapSectionOutOfBounds`).
/// - **Agreement with the header.** The header and the map both state where
///   `string_ids`, `type_ids`, `proto_ids`, `field_ids`, `method_ids` and
///   `class_defs` are and how many they hold; a file where the two disagree has
///   no single answer, so it is refused (`MapSectionMismatch`). The same rule
///   pins `header_item` to one item at offset zero -- the format's mandatory
///   first entry -- and `map_list` to this one item.
/// - **Self-description.** A map without a `map_list` entry is refused as
///   `NoMapListEntry`: every valid DEX describes itself.
///
/// Sections are NOT required to be present when their header count is zero, and
/// `hiddenapi_class_data_item` is accepted as a known entry without being read
/// (see the constant's note).
pub fn map_list(data: &[u8]) -> Result<Vec<MapEntry>, DexError> {
    if data.len() < HEADER_SIZE as usize {
        return Err(DexError::TooShort);
    }
    let (map_off, count) = map_list_position(data)?;
    let mut entries = Vec::with_capacity(count as usize);
    let mut previous: Option<u32> = None;
    for i in 0..count {
        let at = map_off as usize + 4 + i as usize * MAP_ITEM_SIZE;
        let item_type = read_u16(data, at).ok_or(DexError::MapListOutOfBounds)?;
        let size = read_u32(data, at + 4).ok_or(DexError::MapListOutOfBounds)?;
        let offset = read_u32(data, at + 8).ok_or(DexError::MapListOutOfBounds)?;
        if !known_map_item_type(item_type) {
            return Err(DexError::UnknownMapItemType);
        }
        if let Some(previous) = previous {
            if offset <= previous {
                return Err(DexError::MapNotSorted);
            }
        }
        previous = Some(offset);
        check_map_extent(data, item_type, size, offset)?;
        entries.push(MapEntry {
            item_type,
            size,
            offset,
        });
    }
    // Self-description comes FIRST, before the entry is held to its exact shape:
    // "there is no map entry at all" and "the map entry names another offset"
    // are different answers, and the format's own rule is the first one.
    if !entries.iter().any(|entry| entry.item_type == TYPE_MAP_LIST) {
        return Err(DexError::NoMapListEntry);
    }
    check_map_against_header(data, &entries)?;
    Ok(entries)
}

/// The header's `map_off` and the entry count it points at, bounded against the
/// file before any entry is read.
fn map_list_position(data: &[u8]) -> Result<(u32, u32), DexError> {
    let map_off = header_u32(data, 52)?;
    let list_start = map_off as usize;
    if list_start
        .checked_add(4)
        .map_or(true, |end| end > data.len())
    {
        return Err(DexError::MapOffOutOfBounds);
    }
    let count = read_u32(data, map_off as usize).ok_or(DexError::MapListOutOfBounds)?;
    let end = (map_off as u64)
        .checked_add(4)
        .and_then(|v| v.checked_add(count as u64 * MAP_ITEM_SIZE as u64))
        .ok_or(DexError::MapListOutOfBounds)?;
    if end > data.len() as u64 {
        return Err(DexError::MapListOutOfBounds);
    }
    Ok((map_off, count))
}

/// Whether `item_type` is one of the twenty types the format defines.
fn known_map_item_type(item_type: u16) -> bool {
    matches!(
        item_type,
        TYPE_HEADER_ITEM
            | TYPE_STRING_ID_ITEM
            | TYPE_TYPE_ID_ITEM
            | TYPE_PROTO_ID_ITEM
            | TYPE_FIELD_ID_ITEM
            | TYPE_METHOD_ID_ITEM
            | TYPE_CLASS_DEF_ITEM
            | TYPE_CALL_SITE_ID_ITEM
            | TYPE_METHOD_HANDLE_ITEM
            | TYPE_MAP_LIST
            | TYPE_TYPE_LIST
            | TYPE_ANNOTATION_SET_REF_LIST
            | TYPE_ANNOTATION_SET_ITEM
            | TYPE_CLASS_DATA_ITEM
            | TYPE_CODE_ITEM
            | TYPE_STRING_DATA_ITEM
            | TYPE_DEBUG_INFO_ITEM
            | TYPE_ANNOTATION_ITEM
            | TYPE_ENCODED_ARRAY_ITEM
            | TYPE_ANNOTATIONS_DIRECTORY_ITEM
            | TYPE_HIDDENAPI_CLASS_DATA_ITEM
    )
}

/// Bounds one map entry's section against the file.
///
/// Only the fixed-size id tables have an extent the map states in bytes. Every
/// data-area item (`class_data`, `code`, `string_data`, `debug_info`, the three
/// annotation forms, `encoded_array`, `annotations_directory`) is variable, so
/// its bytes are found by parsing it -- what the map can be held to is that the
/// section starts inside the file, and that the four-byte size word a list-shaped
/// item begins with is there too. (The format's "Item Size In Bytes" column
/// gives `4 + size * n` for those lists, which is the extent of ONE list of
/// `size` entries; a map entry counts ITEMS, so using that formula for a section
/// of several small lists would refuse a legal file.)
fn check_map_extent(
    data: &[u8],
    item_type: u16,
    size: u32,
    offset: u32,
) -> Result<(), DexError> {
    if size == 0 {
        return if offset as usize <= data.len() {
            Ok(())
        } else {
            Err(DexError::MapSectionOutOfBounds)
        };
    }
    let fixed_item_size = match item_type {
        TYPE_HEADER_ITEM => Some(HEADER_SIZE),
        TYPE_STRING_ID_ITEM => Some(STRING_ID_SIZE),
        TYPE_TYPE_ID_ITEM => Some(TYPE_ID_SIZE),
        TYPE_PROTO_ID_ITEM => Some(PROTO_ID_SIZE),
        TYPE_FIELD_ID_ITEM => Some(FIELD_ID_SIZE),
        TYPE_METHOD_ID_ITEM => Some(METHOD_ID_SIZE),
        TYPE_CLASS_DEF_ITEM => Some(CLASS_DEF_SIZE),
        TYPE_CALL_SITE_ID_ITEM => Some(CALL_SITE_ID_SIZE),
        TYPE_METHOD_HANDLE_ITEM => Some(METHOD_HANDLE_ITEM_SIZE),
        _ => None,
    };
    let needed = match fixed_item_size {
        Some(item_size) => size as u64 * item_size as u64,
        None => {
            let list_shaped = matches!(
                item_type,
                TYPE_MAP_LIST
                    | TYPE_TYPE_LIST
                    | TYPE_ANNOTATION_SET_REF_LIST
                    | TYPE_ANNOTATION_SET_ITEM
            );
            if list_shaped {
                TYPE_LIST_HEADER as u64
            } else {
                1
            }
        }
    };
    let end = (offset as u64)
        .checked_add(needed)
        .ok_or(DexError::MapSectionOutOfBounds)?;
    if end > data.len() as u64 {
        return Err(DexError::MapSectionOutOfBounds);
    }
    Ok(())
}

/// Holds the map to the sections the header also describes, and to the two
/// entries the format fixes in place: `header_item` is one item at offset zero,
/// and the map's own entry is this list.
fn check_map_against_header(data: &[u8], entries: &[MapEntry]) -> Result<(), DexError> {
    // (map type, header size field, header offset field), in header order.
    let declared = [
        (TYPE_STRING_ID_ITEM, 56, 60),
        (TYPE_TYPE_ID_ITEM, 64, 68),
        (TYPE_PROTO_ID_ITEM, 72, 76),
        (TYPE_FIELD_ID_ITEM, 80, 84),
        (TYPE_METHOD_ID_ITEM, 88, 92),
        (TYPE_CLASS_DEF_ITEM, 96, 100),
    ];
    for (item_type, size_field, offset_field) in declared {
        let count = header_u32(data, size_field)?;
        let offset = header_u32(data, offset_field)?;
        let entry = entries.iter().find(|entry| entry.item_type == item_type);
        let agrees = match (count, entry) {
            // A zero-count table may be omitted from the map. Describing a
            // table that holds nothing is a contradiction, not a spare entry.
            (0, None) => true,
            (0, Some(_)) => false,
            (_, Some(entry)) => entry.size == count && entry.offset == offset,
            (_, None) => false,
        };
        if !agrees {
            return Err(DexError::MapSectionMismatch);
        }
    }

    let headers: Vec<&MapEntry> = entries
        .iter()
        .filter(|entry| entry.item_type == TYPE_HEADER_ITEM)
        .collect();
    if headers.len() != 1 || headers[0].offset != 0 || headers[0].size != 1 {
        return Err(DexError::MapSectionMismatch);
    }
    let map_off = header_u32(data, 52)?;
    let maps: Vec<&MapEntry> = entries
        .iter()
        .filter(|entry| entry.item_type == TYPE_MAP_LIST)
        .collect();
    if maps.len() != 1 || maps[0].offset != map_off || maps[0].size != 1 {
        return Err(DexError::MapSectionMismatch);
    }
    Ok(())
}

/// The `(offset, size)` of the map entry naming `item_type`, when the file has
/// one. The header has no fields for `call_site_ids` or `method_handles`, so the
/// map is the only way to find either section.
fn map_section(data: &[u8], item_type: u16) -> Result<Option<(u32, u32)>, DexError> {
    Ok(map_list(data)?
        .into_iter()
        .find(|entry| entry.item_type == item_type)
        .map(|entry| (entry.offset, entry.size)))
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
    let annotations_off = read_u32(data, item_off + 20).ok_or(DexError::ClassIndexOutOfBounds)?;
    let class_data_off = read_u32(data, item_off + 24).ok_or(DexError::ClassIndexOutOfBounds)?;
    let static_values_off = read_u32(data, item_off + 28).ok_or(DexError::ClassIndexOutOfBounds)?;

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
    // checked here, where the class itself is already being validated. The same
    // rule covers the two offsets added with the annotation tables: each one is
    // read by exactly one resolver, and a class that names a place outside the
    // file should be refused as a class, before that resolver allocates
    // anything.
    if class_data_off != 0 && class_data_off as usize >= data.len() {
        return Err(DexError::ClassDataOutOfBounds);
    }
    if annotations_off != 0 && annotations_off as usize >= data.len() {
        return Err(DexError::AnnotationsDirectoryOutOfBounds);
    }
    if static_values_off != 0 && static_values_off as usize >= data.len() {
        return Err(DexError::EncodedArrayOutOfBounds);
    }
    Ok(ClassDef {
        class_idx,
        access_flags,
        superclass_idx,
        source_file_idx,
        class_data_off,
        annotations_off,
        static_values_off,
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

/// One resolved `field_id_item`: which class declares a field, the field's type,
/// and its name. All three are checked (class and type through
/// [`type_descriptor`], name through [`string`]) before this is returned, so a
/// `FieldId` names a field the file's own tables answered for.
#[derive(Debug, PartialEq, Eq)]
pub struct FieldId {
    pub class_idx: u32,
    pub type_idx: u32,
    pub name_idx: u32,
}

/// One encoded field or method entry inside a `class_data_item`: the ABSOLUTE
/// table index (the format's deltas already accumulated by [`class_members`])
/// and the access flags. `code_off` is `Some` only for a method that carries a
/// code_item; it is always `None` for a field, and `None` for a native or
/// abstract method.
#[derive(Debug, PartialEq, Eq)]
pub struct ClassMember {
    pub index: u32,
    pub access_flags: u32,
    pub code_off: Option<u32>,
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

/// Reads one `field_id_item`, resolving its class, type and name.
///
/// The record is `u2 class_idx, u2 type_idx, u4 name_idx` -- 8 bytes, the same
/// mixed widths as `method_id_item`, and read at those widths rather than as
/// two words for exactly that reason. All three indices are resolved here: a
/// field whose class or type is not in the file's own tables (or whose name is
/// not in its string pool) is refused instead of reported.
pub fn field_id(data: &[u8], index: u32) -> Result<FieldId, DexError> {
    if data.len() < HEADER_SIZE as usize {
        return Err(DexError::TooShort);
    }
    let count = header_u32(data, 80)?;
    let table_off = header_u32(data, 84)?;
    if index >= count {
        return Err(DexError::FieldIndexOutOfBounds);
    }
    let item_off = (table_off as usize)
        .checked_add(index as usize * FIELD_ID_SIZE as usize)
        .ok_or(DexError::FieldIndexOutOfBounds)?;
    let class_idx = read_u16(data, item_off).ok_or(DexError::FieldIndexOutOfBounds)? as u32;
    let type_idx = read_u16(data, item_off + 2).ok_or(DexError::FieldIndexOutOfBounds)? as u32;
    let name_idx = read_u32(data, item_off + 4).ok_or(DexError::FieldIndexOutOfBounds)?;

    type_descriptor(data, class_idx)?;
    type_descriptor(data, type_idx)?;
    string(data, name_idx)?;
    Ok(FieldId {
        class_idx,
        type_idx,
        name_idx,
    })
}

/// Parses one `class_data_item`, in the format's order: static fields, instance
/// fields, direct methods, virtual methods.
///
/// Returns `None` when the class has no `class_data_item` (`class_data_off` is
/// 0) and `Some` with empty vectors when it has one that declares nothing. Each
/// member's index is a DELTA in the file and an absolute table index in the
/// returned struct -- [`class_members`] accumulates it per list -- so every
/// method index is resolved through [`method_id`], every field index through
/// [`field_id`], and every non-zero `code_off` through [`code_item`].
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

/// Reads `count` encoded members from `*cursor`, accumulating the format's
/// per-list index deltas and validating each resulting member.
///
/// # Why the minimum-size check comes first
///
/// An `encoded_field` is at least two ULEB128s and an `encoded_method` at least
/// three, so a `class_data` whose counts claim more entries than the remaining
/// bytes could hold is a truncated FILE, not a long class. Checking that before
/// the vector is allocated keeps a malformed count from reserving memory it
/// cannot fill.
///
/// # Why the index is accumulated, and reset per list
///
/// `field_idx_diff`/`method_idx_diff` are deltas from the previous entry in the
/// same list, and each of the four lists starts from its own first delta. A
/// reader that treated the delta as an index is right only while every delta is
/// zero or one and nothing follows; a reader that forgot to reset between lists
/// adds one list's last index to the next list's first delta. Both mistakes
/// produce plausible numbers, which is why the accumulation lives here, once,
/// and the fixture the boot proof uses has three members per group.
///
/// # Why `code_off` is checked against the flags
///
/// A native or abstract method has no body (`code_off == 0`) and a method that
/// is neither must have one. That is two directions of one rule, and both are
/// refused: a body claimed by a native method is a code_item nothing could
/// legitimately have produced, and a missing body on an ordinary method is a
/// method that cannot run. A non-zero `code_off` is then parsed immediately, so
/// a member entry cannot carry an offset that only fails when someone else
/// dereferences it.
fn class_members(
    data: &[u8],
    cursor: &mut usize,
    count: u32,
    methods: bool,
) -> Result<Vec<ClassMember>, DexError> {
    let min_entry = if methods { 3u64 } else { 2u64 };
    let remaining = data.len().saturating_sub(*cursor) as u64;
    if count as u64 * min_entry > remaining {
        return Err(DexError::ClassDataTruncated);
    }
    let mut members = Vec::with_capacity(count as usize);
    let mut index = 0u32;
    for _ in 0..count {
        let delta = member_uleb(data, cursor)?;
        // A sum past `u32::MAX` cannot name a table entry, and saturating makes
        // that a refusal from the table bound below rather than a wrap that
        // could land back inside it.
        index = index.saturating_add(delta);
        let access_flags = member_uleb(data, cursor)?;
        if methods {
            let code_off = member_uleb(data, cursor)?;
            // The INDEX is answered before the flags/code rule, and the order is
            // deliberate: an entry that names no method at all is malformed
            // whatever its flags say, so an index past `method_ids` is reported
            // as such rather than as a code mismatch the flags happened to
            // imply. (The boot's older negative control found this the day the
            // rule arrived: it corrupted the index and the flags together, and
            // the flags check answered first with a different, true-but-less-
            // useful refusal.)
            method_id(data, index)?;
            let declared_no_code = access_flags & (ACC_NATIVE | ACC_ABSTRACT) != 0;
            if declared_no_code != (code_off == 0) {
                return Err(DexError::MethodCodeMismatch);
            }
            if code_off != 0 {
                code_item(data, code_off)?;
            }
            members.push(ClassMember {
                index,
                access_flags,
                code_off: if code_off == 0 { None } else { Some(code_off) },
            });
        } else {
            field_id(data, index)?;
            members.push(ClassMember {
                index,
                access_flags,
                code_off: None,
            });
        }
    }
    Ok(members)
}

/// One ULEB128 inside a `class_data_item`, with a missing byte reported as the
/// truncation it is. `read_uleb128` cannot tell "the file ended here" from "this
/// encoding is too long", and inside class_data those are different answers: a
/// list that stops between entries is `ClassDataTruncated`, while bytes that are
/// present but over-long remain `BadUleb128`.
fn member_uleb(data: &[u8], cursor: &mut usize) -> Result<u32, DexError> {
    if *cursor >= data.len() {
        return Err(DexError::ClassDataTruncated);
    }
    read_uleb128(data, cursor)
}

/// One `code_item` as this module reads it: the register frame the method
/// declares and the raw 16-bit instruction words.
///
/// The words are NOT decoded, and this struct deliberately carries no
/// interpretation of them: instruction formats, verification and execution are
/// an interpreter's job (see the module doc). What the struct makes true is that
/// a method's body is *there*, how big its frame is, and which bytes belong to
/// it -- facts a member table needs before anything can be said about running
/// the code.
#[derive(Debug, PartialEq, Eq)]
pub struct CodeItem {
    pub registers_size: u16,
    /// Registers holding incoming arguments, which the format places at the END
    /// of the frame, so `ins_size <= registers_size`.
    pub ins_size: u16,
    pub outs_size: u16,
    pub tries_size: u16,
    pub debug_info_off: u32,
    pub insns: Vec<u16>,
}

/// Reads the `code_item` at `off`, bounding every array it declares against the
/// file before reading it.
///
/// `off == 0` is refused rather than treated as an empty body: zero is the
/// format's "no code item", and a caller asking for one at zero is asking for
/// something that does not exist. The try-item array is padded to a 4-byte
/// boundary when it follows an odd number of code units, which is part of the
/// layout (and part of the bound) even though this module does not decode
/// handlers.
pub fn code_item(data: &[u8], off: u32) -> Result<CodeItem, DexError> {
    if off == 0 {
        return Err(DexError::CodeItemOutOfBounds);
    }
    let start = off as usize;
    let header = data
        .get(start..start + CODE_ITEM_HEADER as usize)
        .ok_or(DexError::CodeItemOutOfBounds)?;
    let registers_size = u16::from_le_bytes([header[0], header[1]]);
    let ins_size = u16::from_le_bytes([header[2], header[3]]);
    let outs_size = u16::from_le_bytes([header[4], header[5]]);
    let tries_size = u16::from_le_bytes([header[6], header[7]]);
    let debug_info_off = u32::from_le_bytes([header[8], header[9], header[10], header[11]]);
    let insns_size = u32::from_le_bytes([header[12], header[13], header[14], header[15]]);

    let insns_end = (start as u64)
        .checked_add(CODE_ITEM_HEADER as u64)
        .and_then(|v| v.checked_add(insns_size as u64 * 2))
        .ok_or(DexError::CodeItemTruncated)?;
    let mut end = insns_end;
    if tries_size != 0 {
        let padding = if insns_size % 2 == 1 { 2u64 } else { 0 };
        end = end
            .checked_add(padding)
            .and_then(|v| v.checked_add(tries_size as u64 * TRY_ITEM_SIZE as u64))
            .ok_or(DexError::CodeItemTruncated)?;
    }
    if end > data.len() as u64 {
        return Err(DexError::CodeItemTruncated);
    }
    if debug_info_off != 0 && debug_info_off as usize >= data.len() {
        return Err(DexError::CodeItemOutOfBounds);
    }
    // `ins_size` counts the frame's HIGH registers, so it cannot exceed the
    // frame. Checked before the code units are read: it is a claim about the
    // method, and a false one makes every later reading of the words meaningless.
    if ins_size > registers_size {
        return Err(DexError::CodeItemRegisterMismatch);
    }
    let mut insns = Vec::with_capacity(insns_size as usize);
    for i in 0..insns_size {
        let at = start + CODE_ITEM_HEADER as usize + i as usize * 2;
        insns.push(read_u16(data, at).ok_or(DexError::CodeItemTruncated)?);
    }
    Ok(CodeItem {
        registers_size,
        ins_size,
        outs_size,
        tries_size,
        debug_info_off,
        insns,
    })
}

/// One `encoded_value`, with whatever the format says to resolve about it.
///
/// The integer and floating forms carry their value; the index forms carry the
/// table index AFTER it has been resolved (so a `String` variant names a string
/// the file's own pool answered for); the two recursive forms carry their
/// nested values. Two are deliberately raw: `FloatBits`/`DoubleBits` keep the
/// bit pattern rather than an `f32`/`f64` (a parser has no business rounding a
/// value it is only validating, and it keeps `Eq`), and `MethodHandle` keeps the
/// index because this module has no method-handle table to check it against.
#[derive(Debug, PartialEq, Eq)]
pub enum EncodedValue {
    Byte(i8),
    Short(i16),
    Char(u16),
    Int(i32),
    Long(i64),
    FloatBits(u32),
    DoubleBits(u64),
    /// A `proto_ids` index (`VALUE_METHOD_TYPE`).
    MethodType(u32),
    /// A `method_handles` index (`VALUE_METHOD_HANDLE`), resolved through
    /// [`method_handle`] -- so the index names a handle whose own target the
    /// file's field or method table answered for.
    MethodHandle(u32),
    String(u32),
    Type(u32),
    Field(u32),
    Method(u32),
    /// A `field_ids` index declared to be an enum constant. The field is
    /// resolved; that it really is an enum constant is not checked.
    Enum(u32),
    Array(Vec<EncodedValue>),
    Annotation(EncodedAnnotation),
    Null,
    Boolean(bool),
}

/// One `encoded_annotation`: a type index, an element count, then
/// `(name_idx, encoded_value)` pairs.
#[derive(Debug, PartialEq, Eq)]
pub struct EncodedAnnotation {
    pub type_idx: u32,
    pub elements: Vec<AnnotationElement>,
}

/// One `annotation_element`: the element's NAME (a `string_ids` index) and its
/// value. The name is resolved through [`string`] before this is returned.
#[derive(Debug, PartialEq, Eq)]
pub struct AnnotationElement {
    pub name_idx: u32,
    pub value: EncodedValue,
}

/// One `annotation_item`: the visibility byte and the annotation it carries.
#[derive(Debug, PartialEq, Eq)]
pub struct AnnotationItem {
    pub visibility: u8,
    pub annotation: EncodedAnnotation,
}

/// One row of an `annotations_directory_item` with its set already resolved.
/// Fields, methods and parameters share this shape in the format -- each row is
/// `member_idx` + `annotations_off` -- so they share it here too.
#[derive(Debug, PartialEq, Eq)]
pub struct AnnotatedMember {
    /// A `field_ids` index for a field row, a `method_ids` index otherwise.
    pub member_idx: u32,
    pub annotations: Vec<AnnotationItem>,
}

/// One row of an `annotations_directory_item`'s PARAMETER list, resolved.
///
/// A parameter row is not the same shape as a field or method row: its
/// `annotations_off` names an `annotation_set_ref_list`, one annotation SET per
/// parameter, and each of those names an `annotation_set_item`. The extra level
/// is the format's (a method has many parameters, and each may carry its own
/// annotations), so it is a separate type here rather than a `Vec` of items that
/// would silently mean "one set for the whole method".
#[derive(Debug, PartialEq, Eq)]
pub struct AnnotatedParameter {
    pub member_idx: u32,
    /// One entry per parameter, each the annotations on that parameter.
    pub parameter_sets: Vec<Vec<AnnotationItem>>,
}

/// A class's resolved `annotations_directory_item`.
///
/// `class_annotations` is empty when `class_annotations_off` is 0, which the
/// format allows (a class with field/method annotations but none of its own).
/// The member lists are empty vectors, not `None`, for the same reason
/// [`ClassData`] uses them: "this class annotates no fields" is a fact.
#[derive(Debug, PartialEq, Eq)]
pub struct AnnotationsDirectory {
    pub class_annotations: Vec<AnnotationItem>,
    pub field_annotations: Vec<AnnotatedMember>,
    pub method_annotations: Vec<AnnotatedMember>,
    pub parameter_annotations: Vec<AnnotatedParameter>,
}

/// Reads one `encoded_value` from `*cursor`, `depth` arrays/annotations deep.
///
/// # Why the width field is checked before it is used
///
/// A value's header byte is `type | (width - 1) << 5`, and the width is what
/// says how many bytes to read. Every type has a maximum width in the format
/// (`byte` 1, `short`/`char` 2, `int`/`float` 4, `long`/`double` 8, indices 4),
/// and `null`, `array` and `annotation` have no width at all -- their value is
/// not bytes but structure. A header claiming a wider value than the type
/// allows, or any width for one of those three, is refused rather than trusted:
/// the same byte that would otherwise silently decide a read length.
///
/// # The two exceptions to `width - 1`
///
/// `VALUE_BOOLEAN`'s header field IS its byte count (0 = false, 1 = one byte
/// holding true), and `VALUE_BYTE`'s is fixed at one -- both stated in their own
/// arms below.
///
/// # `byte` is the one type whose width is fixed at one
///
/// `VALUE_BYTE` exists so a small signed number costs two bytes total, so its
/// header must say width 1 (arg 0) -- there is no range of widths to choose
/// from. The integer types that share this space (`short`, `int`, `long`) are
/// the ones whose width is the value's own size, and their sign extension is
/// why [`signed_value`] exists.
fn read_encoded_value(
    data: &[u8],
    cursor: &mut usize,
    depth: u32,
) -> Result<EncodedValue, DexError> {
    if depth > MAX_ENCODED_VALUE_DEPTH {
        return Err(DexError::EncodedValueTooDeep);
    }
    let header = *data.get(*cursor).ok_or(DexError::EncodedValueTruncated)?;
    *cursor += 1;
    let value_type = header & 0x1f;
    let arg = header >> 5;
    match value_type {
        VALUE_BYTE => {
            require_arg(arg, 0)?;
            Ok(EncodedValue::Byte(signed_value(value_bytes(data, cursor, 1)?, 1) as i8))
        }
        VALUE_SHORT => {
            require_arg(arg, 1)?;
            Ok(EncodedValue::Short(signed_value(value_bytes(data, cursor, arg as u32 + 1)?, arg as u32 + 1) as i16))
        }
        VALUE_CHAR => {
            require_arg(arg, 1)?;
            Ok(EncodedValue::Char(value_bytes(data, cursor, arg as u32 + 1)? as u16))
        }
        VALUE_INT => {
            require_arg(arg, 3)?;
            Ok(EncodedValue::Int(signed_value(value_bytes(data, cursor, arg as u32 + 1)?, arg as u32 + 1) as i32))
        }
        VALUE_LONG => {
            require_arg(arg, 7)?;
            Ok(EncodedValue::Long(signed_value(value_bytes(data, cursor, arg as u32 + 1)?, arg as u32 + 1)))
        }
        VALUE_FLOAT => {
            require_arg(arg, 3)?;
            Ok(EncodedValue::FloatBits(value_bytes(data, cursor, arg as u32 + 1)? as u32))
        }
        VALUE_DOUBLE => {
            require_arg(arg, 7)?;
            Ok(EncodedValue::DoubleBits(value_bytes(data, cursor, arg as u32 + 1)?))
        }
        VALUE_METHOD_TYPE => {
            require_arg(arg, 3)?;
            let index = value_bytes(data, cursor, arg as u32 + 1)? as u32;
            proto_id(data, index)?;
            Ok(EncodedValue::MethodType(index))
        }
        VALUE_METHOD_HANDLE => {
            require_arg(arg, 3)?;
            let index = value_bytes(data, cursor, arg as u32 + 1)? as u32;
            method_handle(data, index)?;
            Ok(EncodedValue::MethodHandle(index))
        }
        VALUE_STRING => {
            require_arg(arg, 3)?;
            let index = value_bytes(data, cursor, arg as u32 + 1)? as u32;
            string(data, index)?;
            Ok(EncodedValue::String(index))
        }
        VALUE_TYPE => {
            require_arg(arg, 3)?;
            let index = value_bytes(data, cursor, arg as u32 + 1)? as u32;
            type_descriptor(data, index)?;
            Ok(EncodedValue::Type(index))
        }
        VALUE_FIELD => {
            require_arg(arg, 3)?;
            let index = value_bytes(data, cursor, arg as u32 + 1)? as u32;
            field_id(data, index)?;
            Ok(EncodedValue::Field(index))
        }
        VALUE_METHOD => {
            require_arg(arg, 3)?;
            let index = value_bytes(data, cursor, arg as u32 + 1)? as u32;
            method_id(data, index)?;
            Ok(EncodedValue::Method(index))
        }
        VALUE_ENUM => {
            require_arg(arg, 3)?;
            let index = value_bytes(data, cursor, arg as u32 + 1)? as u32;
            field_id(data, index)?;
            Ok(EncodedValue::Enum(index))
        }
        VALUE_ARRAY => {
            require_arg(arg, 0)?;
            Ok(EncodedValue::Array(read_encoded_array(data, cursor, depth + 1)?))
        }
        VALUE_ANNOTATION => {
            require_arg(arg, 0)?;
            Ok(EncodedValue::Annotation(read_encoded_annotation(data, cursor, depth + 1)?))
        }
        VALUE_NULL => {
            require_arg(arg, 0)?;
            Ok(EncodedValue::Null)
        }
        VALUE_BOOLEAN => {
            require_arg(arg, 1)?;
            // The one type where the header's width field is NOT `size - 1`: for
            // a boolean, arg IS the number of value bytes (0 for false, 1 for
            // true), so the byte count is `arg`, not `arg + 1`. Getting this
            // backwards is not a small error -- it reads the NEXT value's header
            // as this value's payload, which turns a well-formed file into one
            // with an undefined value type a few bytes later. The boot gate
            // caught exactly that on this increment's first run.
            let raw = value_bytes(data, cursor, arg as u32)?;
            // arg 0 is false with no value byte; when a byte is present the
            // format writes 0x01, and any non-zero byte reads as true.
            Ok(EncodedValue::Boolean(raw != 0))
        }
        _ => Err(DexError::BadEncodedValueType),
    }
}

/// Refuses a value whose declared width is impossible for its type.
fn require_arg(arg: u8, max: u8) -> Result<(), DexError> {
    if arg > max {
        return Err(DexError::BadEncodedValueArg);
    }
    Ok(())
}

/// Reads `size` little-endian bytes of a value, zero-extended into a `u64`.
/// Bounded one byte at a time so a value that stops early is reported as its
/// own truncation, not as the array's.
fn value_bytes(data: &[u8], cursor: &mut usize, size: u32) -> Result<u64, DexError> {
    let mut raw = 0u64;
    for i in 0..size {
        let byte = *data.get(*cursor).ok_or(DexError::EncodedValueTruncated)?;
        *cursor += 1;
        raw |= (byte as u64) << (8 * i);
    }
    Ok(raw)
}

/// Sign-extends a `size`-byte little-endian value. `size` is 1..=8, so the
/// shift is at most 56.
fn signed_value(raw: u64, size: u32) -> i64 {
    let shift = 64 - 8 * size;
    ((raw << shift) as i64) >> shift
}

/// Reads one `encoded_annotation` from `*cursor`: a type index, an element
/// count, then that many `(name_idx, value)` pairs.
///
/// The count is bounded against the bytes left BEFORE the vector is allocated:
/// every element needs at least two bytes (a one-byte name index and a one-byte
/// value header), so a count past that bound is a truncated file.
fn read_encoded_annotation(
    data: &[u8],
    cursor: &mut usize,
    depth: u32,
) -> Result<EncodedAnnotation, DexError> {
    if depth > MAX_ENCODED_VALUE_DEPTH {
        return Err(DexError::EncodedValueTooDeep);
    }
    let type_idx = annotation_uleb(data, cursor)?;
    let size = annotation_uleb(data, cursor)?;
    let remaining = data.len().saturating_sub(*cursor) as u64;
    if size as u64 * 2 > remaining {
        return Err(DexError::AnnotationItemTruncated);
    }
    type_descriptor(data, type_idx)?;
    let mut elements = Vec::with_capacity(size as usize);
    for _ in 0..size {
        let name_idx = annotation_uleb(data, cursor)?;
        string(data, name_idx)?;
        let value = read_encoded_value(data, cursor, depth)?;
        elements.push(AnnotationElement { name_idx, value });
    }
    Ok(EncodedAnnotation { type_idx, elements })
}

/// One ULEB128 inside an annotation item, with a missing byte reported as the
/// truncation it is (the same distinction [`member_uleb`] draws for class_data).
fn annotation_uleb(data: &[u8], cursor: &mut usize) -> Result<u32, DexError> {
    if *cursor >= data.len() {
        return Err(DexError::AnnotationItemTruncated);
    }
    read_uleb128(data, cursor)
}

/// Reads one `encoded_array_item` at `*cursor`: a ULEB128 count then that many
/// values.
///
/// `off == 0` is the format's "no static values" and answers an empty vector;
/// every other offset must land in the file. The count is bounded against the
/// bytes left before allocating -- a value is at least one header byte -- so a
/// malformed count cannot reserve memory it cannot fill.
fn read_encoded_array(
    data: &[u8],
    cursor: &mut usize,
    depth: u32,
) -> Result<Vec<EncodedValue>, DexError> {
    if depth > MAX_ENCODED_VALUE_DEPTH {
        return Err(DexError::EncodedValueTooDeep);
    }
    if *cursor >= data.len() {
        return Err(DexError::EncodedArrayTruncated);
    }
    let size = read_uleb128(data, cursor)?;
    let remaining = data.len().saturating_sub(*cursor) as u64;
    if size as u64 > remaining {
        return Err(DexError::EncodedArrayTruncated);
    }
    let mut values = Vec::with_capacity(size as usize);
    for _ in 0..size {
        values.push(read_encoded_value(data, cursor, depth)?);
    }
    Ok(values)
}

/// Reads the `encoded_array_item` at `off`.
///
/// Zero is the format's "absent" and answers an empty array, because that is
/// what `class_def.static_values_off` of 0 means for a class with static fields
/// that have no initialisers.
pub fn encoded_array(data: &[u8], off: u32) -> Result<Vec<EncodedValue>, DexError> {
    if off == 0 {
        return Ok(Vec::new());
    }
    let mut cursor = off as usize;
    if cursor >= data.len() {
        return Err(DexError::EncodedArrayOutOfBounds);
    }
    read_encoded_array(data, &mut cursor, 0)
}

/// A class's static field initial values, i.e. the `encoded_array_item` its
/// `static_values_off` names. Empty when the class names none.
pub fn static_values(data: &[u8], class_index: u32) -> Result<Vec<EncodedValue>, DexError> {
    let def = class_def(data, class_index)?;
    encoded_array(data, def.static_values_off)
}

/// Reads one `annotation_item` at `off`: a visibility byte, then an
/// encoded_annotation.
///
/// Zero is refused rather than treated as "no annotation": an
/// `annotation_set_item` entry and every directory row name a real item, so a
/// zero there is a file that says "this offset" and means nothing.
pub fn annotation_item(data: &[u8], off: u32) -> Result<AnnotationItem, DexError> {
    if off == 0 || off as usize >= data.len() {
        return Err(DexError::AnnotationItemOutOfBounds);
    }
    let mut cursor = off as usize;
    let visibility = data[cursor];
    cursor += 1;
    if visibility > VISIBILITY_SYSTEM {
        return Err(DexError::BadAnnotationVisibility);
    }
    let annotation = read_encoded_annotation(data, &mut cursor, 0)?;
    Ok(AnnotationItem {
        visibility,
        annotation,
    })
}

/// Reads the `annotation_set_item` at `off`: a count then that many
/// `annotation_item` offsets, each resolved.
///
/// The count is bounded against the bytes left before allocating, and every
/// entry is parsed rather than merely range-checked: a set this function returns
/// contains annotations whose type, element names and values all resolved.
pub fn annotation_set(data: &[u8], off: u32) -> Result<Vec<AnnotationItem>, DexError> {
    if off == 0 {
        return Err(DexError::AnnotationSetOutOfBounds);
    }
    let start = off as usize;
    let size = read_u32(data, start).ok_or(DexError::AnnotationSetOutOfBounds)?;
    let entries_end = (start as u64)
        .checked_add(ANNOTATION_SET_HEADER as u64)
        .and_then(|v| v.checked_add(size as u64 * ANNOTATION_SET_ENTRY as u64))
        .ok_or(DexError::AnnotationSetTruncated)?;
    if entries_end > data.len() as u64 {
        return Err(DexError::AnnotationSetTruncated);
    }
    let mut items = Vec::with_capacity(size as usize);
    for i in 0..size {
        let entry = start + ANNOTATION_SET_HEADER as usize + i as usize * ANNOTATION_SET_ENTRY as usize;
        let item_off = read_u32(data, entry).ok_or(DexError::AnnotationSetTruncated)?;
        items.push(annotation_item(data, item_off)?);
    }
    Ok(items)
}

/// Reads a directory's three member lists, resolving each row's member and its
/// annotation set.
///
/// A field row's index is resolved through [`field_id`] and a method or
/// parameter row's through [`method_id`], so a directory cannot name a member
/// the file's own tables do not have; each row's set is then read in full.
fn annotation_rows(
    data: &[u8],
    cursor: &mut usize,
    count: u32,
    methods: bool,
) -> Result<Vec<AnnotatedMember>, DexError> {
    let mut rows = Vec::with_capacity(count as usize);
    for _ in 0..count {
        let member_idx = read_u32(data, *cursor).ok_or(DexError::AnnotationsDirectoryTruncated)?;
        let annotations_off =
            read_u32(data, *cursor + 4).ok_or(DexError::AnnotationsDirectoryTruncated)?;
        *cursor += ANNOTATION_ROW as usize;
        if methods {
            method_id(data, member_idx)?;
        } else {
            field_id(data, member_idx)?;
        }
        rows.push(AnnotatedMember {
            member_idx,
            annotations: annotation_set(data, annotations_off)?,
        });
    }
    Ok(rows)
}

/// Reads the `annotations_directory_item` a class's `annotations_off` names, or
/// `None` when the class has no directory at all (`annotations_off == 0`).
///
/// The directory is the one table the format splits across levels: it names a
/// `class_annotations_off` plus three counts, the field and method rows name `annotation_set_item`s,
/// and the parameter rows name `annotation_set_ref_list`s -- one set per
/// parameter, one level deeper. Every level is read here, so the answer is the
/// annotations themselves: a caller never holds an offset it would have to
/// remember to resolve.
pub fn annotations_directory(
    data: &[u8],
    class_index: u32,
) -> Result<Option<AnnotationsDirectory>, DexError> {
    let def = class_def(data, class_index)?;
    if def.annotations_off == 0 {
        return Ok(None);
    }
    let start = def.annotations_off as usize;
    if start >= data.len() {
        return Err(DexError::AnnotationsDirectoryOutOfBounds);
    }
    let class_annotations_off =
        read_u32(data, start).ok_or(DexError::AnnotationsDirectoryOutOfBounds)?;
    let fields_size =
        read_u32(data, start + 4).ok_or(DexError::AnnotationsDirectoryOutOfBounds)? as u64;
    let methods_size =
        read_u32(data, start + 8).ok_or(DexError::AnnotationsDirectoryOutOfBounds)? as u64;
    let parameters_size =
        read_u32(data, start + 12).ok_or(DexError::AnnotationsDirectoryOutOfBounds)? as u64;

    // Every row is eight bytes, so the three counts are bounded against the
    // bytes left BEFORE any vector is allocated: a directory whose counts claim
    // more rows than the file could hold is truncated, not large.
    let rows = fields_size + methods_size + parameters_size;
    let rows_end = (start as u64)
        .checked_add(ANNOTATIONS_DIRECTORY_HEADER as u64)
        .and_then(|v| v.checked_add(rows.checked_mul(ANNOTATION_ROW as u64)?))
        .ok_or(DexError::AnnotationsDirectoryTruncated)?;
    if rows_end > data.len() as u64 {
        return Err(DexError::AnnotationsDirectoryTruncated);
    }

    let class_annotations = if class_annotations_off == 0 {
        Vec::new()
    } else {
        annotation_set(data, class_annotations_off)?
    };
    let mut cursor = start + ANNOTATIONS_DIRECTORY_HEADER as usize;
    let field_annotations = annotation_rows(data, &mut cursor, fields_size as u32, false)?;
    let method_annotations = annotation_rows(data, &mut cursor, methods_size as u32, true)?;
    let parameter_annotations =
        parameter_annotation_rows(data, &mut cursor, parameters_size as u32)?;
    Ok(Some(AnnotationsDirectory {
        class_annotations,
        field_annotations,
        method_annotations,
        parameter_annotations,
    }))
}

/// Reads a directory's parameter rows, resolving each row's member and the
/// `annotation_set_ref_list` its offset names.
///
/// The row's `annotations_off` names a ref list, not a set: the format gives
/// each PARAMETER its own annotation set, so a reader that treated the word as
/// a set would take the list's size for a set size and its entries for item
/// offsets. That was this module's own reading until the ref list arrived; the
/// fixture carries the extra level now, and a parameter row that does not point
/// at a well-formed list is refused by [`annotation_set_ref_list`].
fn parameter_annotation_rows(
    data: &[u8],
    cursor: &mut usize,
    count: u32,
) -> Result<Vec<AnnotatedParameter>, DexError> {
    let mut rows = Vec::with_capacity(count as usize);
    for _ in 0..count {
        let member_idx = read_u32(data, *cursor).ok_or(DexError::AnnotationsDirectoryTruncated)?;
        let annotations_off =
            read_u32(data, *cursor + 4).ok_or(DexError::AnnotationsDirectoryTruncated)?;
        *cursor += ANNOTATION_ROW as usize;
        method_id(data, member_idx)?;
        rows.push(AnnotatedParameter {
            member_idx,
            parameter_sets: annotation_set_ref_list(data, annotations_off)?,
        });
    }
    Ok(rows)
}

/// Reads the `annotation_set_ref_list` at `off`: a count, then that many
/// offsets, each resolved through [`annotation_set`].
///
/// This is the level a parameter row points at -- one annotation set per
/// parameter -- and it is a section in its own right (`TYPE_ANNOTATION_SET_REF_LIST`),
/// so a file that has one must be able to name it in its map. Zero is refused
/// rather than read as an empty list: a row's `annotations_off` is required to
/// name a list, and zero is this format's "nothing", which is not a list.
///
/// The count is bounded against the bytes left before anything is allocated, and
/// every entry must resolve: a list this function returns holds annotation sets
/// whose items, types, element names and values all resolved.
pub fn annotation_set_ref_list(data: &[u8], off: u32) -> Result<Vec<Vec<AnnotationItem>>, DexError> {
    if off == 0 || off as usize >= data.len() {
        return Err(DexError::AnnotationSetRefListOutOfBounds);
    }
    let start = off as usize;
    let size = read_u32(data, start).ok_or(DexError::AnnotationSetRefListOutOfBounds)?;
    let end = (start as u64)
        .checked_add(ANNOTATION_SET_HEADER as u64)
        .and_then(|v| v.checked_add(size as u64 * ANNOTATION_SET_ENTRY as u64))
        .ok_or(DexError::AnnotationSetRefListTruncated)?;
    if end > data.len() as u64 {
        return Err(DexError::AnnotationSetRefListTruncated);
    }
    let mut sets = Vec::with_capacity(size as usize);
    for i in 0..size {
        let entry = start + ANNOTATION_SET_HEADER as usize + i as usize * 4;
        let set_off = read_u32(data, entry).ok_or(DexError::AnnotationSetRefListTruncated)?;
        sets.push(annotation_set(data, set_off)?);
    }
    Ok(sets)
}

/// One `method_handle_item`: which kind of handle it is, and the field or method
/// id it names.
///
/// The four accessor types (0x00..=0x03) name a FIELD and the five invoker types
/// (0x04..=0x08) name a METHOD, so `target` is resolved against the table the
/// handle type implies -- and the handle is refused if that table cannot answer.
#[derive(Debug, PartialEq, Eq)]
pub struct MethodHandle {
    pub handle_type: u16,
    pub target: u32,
}

/// Reads one `method_handles` entry. The section has no header field: it is
/// found through the map, which is the reason this table only became readable
/// once the map itself was read as data.
pub fn method_handle(data: &[u8], index: u32) -> Result<MethodHandle, DexError> {
    let (offset, size) = map_section(data, TYPE_METHOD_HANDLE_ITEM)?
        .ok_or(DexError::MethodHandleSectionMissing)?;
    if index >= size {
        return Err(DexError::MethodHandleIndexOutOfBounds);
    }
    let at = (offset as usize)
        .checked_add(index as usize * METHOD_HANDLE_ITEM_SIZE as usize)
        .ok_or(DexError::MethodHandleIndexOutOfBounds)?;
    let handle_type = read_u16(data, at).ok_or(DexError::MethodHandleIndexOutOfBounds)?;
    let target = read_u16(data, at + 4).ok_or(DexError::MethodHandleIndexOutOfBounds)? as u32;
    match handle_type {
        HANDLE_TYPE_STATIC_PUT..=HANDLE_TYPE_INSTANCE_GET => {
            field_id(data, target)?;
        }
        HANDLE_TYPE_INVOKE_STATIC..=HANDLE_TYPE_INVOKE_INTERFACE => {
            method_id(data, target)?;
        }
        _ => return Err(DexError::BadMethodHandleType),
    }
    Ok(MethodHandle {
        handle_type,
        target,
    })
}

/// One resolved call site: the method handle it invokes, the method name it
/// calls, and the method type (a `proto_ids` index) it calls through.
#[derive(Debug, PartialEq, Eq)]
pub struct CallSite {
    pub method_handle: u32,
    pub name_idx: u32,
    pub proto_idx: u32,
}

/// Reads one `call_site_ids` entry, i.e. the `encoded_array_item` its
/// `call_site_off` names.
///
/// The format defines a call site as a THREE-element array -- a method handle, a
/// method name and a method type -- so a file whose array has another shape is
/// refused (`BadCallSiteShape`) rather than reported as a shorter or longer
/// tuple. Each element is already resolved by [`encoded_array`]: the handle
/// through [`method_handle`], the name through [`string`], the type through
/// [`proto_id`].
pub fn call_site(data: &[u8], index: u32) -> Result<CallSite, DexError> {
    let (offset, size) = map_section(data, TYPE_CALL_SITE_ID_ITEM)?
        .ok_or(DexError::CallSiteSectionMissing)?;
    if index >= size {
        return Err(DexError::CallSiteIndexOutOfBounds);
    }
    let at = (offset as usize)
        .checked_add(index as usize * CALL_SITE_ID_SIZE as usize)
        .ok_or(DexError::CallSiteIndexOutOfBounds)?;
    let array_off = read_u32(data, at).ok_or(DexError::CallSiteIndexOutOfBounds)?;
    let values = encoded_array(data, array_off)?;
    match values.as_slice() {
        [EncodedValue::MethodHandle(handle), EncodedValue::String(name), EncodedValue::MethodType(proto)] => {
            Ok(CallSite {
                method_handle: *handle,
                name_idx: *name,
                proto_idx: *proto,
            })
        }
        _ => Err(DexError::BadCallSiteShape),
    }
}

/// One emitted entry of a `debug_info_item`'s positions table: the address in
/// the method's code units and the source line it maps to.
#[derive(Debug, PartialEq, Eq)]
pub struct DebugPosition {
    pub address: u32,
    pub line: i32,
}

/// A `debug_info_item`, read as the facts it states rather than as a decoded
/// listing: the starting line, the declared parameter names (each `None` when the
/// file writes `NO_INDEX`), the positions the state machine emits, and how many
/// locals it introduces.
///
/// The positions are produced by running the format's own state machine, which
/// is the only way to know where the item ends -- there is no length field. The
/// walk stops at `DBG_END_SEQUENCE`, and a missing one is a truncation, not a
/// short listing.
#[derive(Debug, PartialEq, Eq)]
pub struct DebugInfo {
    pub line_start: u32,
    pub parameter_names: Vec<Option<u32>>,
    pub positions: Vec<DebugPosition>,
    pub locals: u32,
}

/// Reads the `debug_info_item` at `off`.
///
/// # What is checked
///
/// The header's `line_start`, `parameters_size` and every parameter name (a
/// `uleb128p1` string index, with 0 meaning `NO_INDEX`), then the bytecode: every
/// operand is read at its own width (ULEB128, SLEB128 or `uleb128p1`), every
/// string and type index is resolved, the undefined opcode range 0x05..=0x08 is
/// refused, and the sequence must reach `DBG_END_SEQUENCE` inside the file. A
/// file that stops first is `DebugInfoTruncated`; an operand whose bytes are
/// present but encoded over-long is `BadUleb128`, because there the encoding is
/// wrong rather than the item short.
///
/// # What is NOT checked
///
/// The format says there is one parameter name per method parameter; comparing
/// that count to the proto would tie this item to the code_item that names it,
/// and this resolver is asked about the item alone. The positions are returned as
/// stated, not replayed against the code's instruction boundaries -- that is a
/// verifier's question, not a parser's.
pub fn debug_info(data: &[u8], off: u32) -> Result<DebugInfo, DexError> {
    if off == 0 || off as usize >= data.len() {
        return Err(DexError::DebugInfoOutOfBounds);
    }
    let mut cursor = off as usize;
    let line_start = debug_uleb(data, &mut cursor)?;
    let parameters_size = debug_uleb(data, &mut cursor)?;
    let remaining = data.len().saturating_sub(cursor) as u64;
    if parameters_size as u64 > remaining {
        return Err(DexError::DebugInfoTruncated);
    }
    let mut parameter_names = Vec::with_capacity(parameters_size as usize);
    for _ in 0..parameters_size {
        let raw = debug_uleb(data, &mut cursor)?;
        if raw == 0 {
            parameter_names.push(None);
        } else {
            let index = raw - 1;
            string(data, index)?;
            parameter_names.push(Some(index));
        }
    }

    let mut address = 0u32;
    let mut line = line_start as i32;
    let mut locals = 0u32;
    let mut positions = Vec::new();
    loop {
        let opcode = *data.get(cursor).ok_or(DexError::DebugInfoTruncated)?;
        cursor += 1;
        match opcode {
            0x00 => break, // DBG_END_SEQUENCE
            0x01 => {
                // DBG_ADVANCE_PC: one ULEB128 address delta.
                let delta = debug_uleb(data, &mut cursor)?;
                address = address.saturating_add(delta);
            }
            0x02 => {
                // DBG_ADVANCE_LINE: one SLEB128 line delta. The line register
                // is signed; the format's own line_start is unsigned.
                let delta = debug_sleb(data, &mut cursor)?;
                line = line.saturating_add(delta);
            }
            0x03 => {
                // DBG_START_LOCAL: register, name (uleb128p1), type (uleb128p1).
                debug_uleb(data, &mut cursor)?;
                debug_optional_string(data, &mut cursor)?;
                debug_optional_type(data, &mut cursor)?;
                locals += 1;
            }
            0x04 => {
                // DBG_START_LOCAL_EXTENDED: the same plus a signature string.
                debug_uleb(data, &mut cursor)?;
                debug_optional_string(data, &mut cursor)?;
                debug_optional_type(data, &mut cursor)?;
                debug_optional_string(data, &mut cursor)?;
                locals += 1;
            }
            0x05..=0x08 => return Err(DexError::BadDebugInfoOpcode),
            0x09 => {
                // DBG_SET_FILE: a uleb128p1 source-file string index.
                debug_optional_string(data, &mut cursor)?;
            }
            special => {
                // Special opcode: address += (op - 0x0a) / 15, line += -4 +
                // (op - 0x0a) % 15, and one positions entry is emitted.
                let adjusted = (special - 0x0a) as u32;
                address = address.saturating_add(adjusted / 15);
                line = line.saturating_add(-4 + (adjusted % 15) as i32);
                positions.push(DebugPosition { address, line });
            }
        }
    }
    Ok(DebugInfo {
        line_start,
        parameter_names,
        positions,
        locals,
    })
}

/// One ULEB128 inside a `debug_info_item`, with a missing byte reported as the
/// truncation it is and an over-long encoding left as `BadUleb128` -- the same
/// distinction [`member_uleb`] draws for class_data. Collapsing the two would
/// call a five-byte continuation run a short file, which it is not: the bytes
/// are there and the encoding is what is wrong.
fn debug_uleb(data: &[u8], cursor: &mut usize) -> Result<u32, DexError> {
    if *cursor >= data.len() {
        return Err(DexError::DebugInfoTruncated);
    }
    read_uleb128(data, cursor)
}

/// One SLEB128 inside a `debug_info_item` (the `DBG_ADVANCE_LINE` operand),
/// split the same way [`debug_uleb`] splits the unsigned reads: a missing byte
/// is `DebugInfoTruncated`, an over-long encoding stays `BadUleb128`.
fn debug_sleb(data: &[u8], cursor: &mut usize) -> Result<i32, DexError> {
    if *cursor >= data.len() {
        return Err(DexError::DebugInfoTruncated);
    }
    read_sleb128(data, cursor)
}

/// A `uleb128p1` string index: 0 is `NO_INDEX`, anything else is `value - 1` and
/// must resolve in the string pool.
fn debug_optional_string(data: &[u8], cursor: &mut usize) -> Result<Option<u32>, DexError> {
    let raw = debug_uleb(data, cursor)?;
    if raw == 0 {
        return Ok(None);
    }
    let index = raw - 1;
    string(data, index)?;
    Ok(Some(index))
}

/// A `uleb128p1` TYPE index, with the same `NO_INDEX` sentinel and a resolution
/// against the type table.
fn debug_optional_type(data: &[u8], cursor: &mut usize) -> Result<Option<u32>, DexError> {
    let raw = debug_uleb(data, cursor)?;
    if raw == 0 {
        return Ok(None);
    }
    let index = raw - 1;
    type_descriptor(data, index)?;
    Ok(Some(index))
}

/// Reads a signed LEB128 (the form `DBG_ADVANCE_LINE` uses), bounded like the
/// unsigned reader: at most five bytes, and a value that fits an `i32`.
fn read_sleb128(data: &[u8], offset: &mut usize) -> Result<i32, DexError> {
    let mut value = 0i64;
    let mut shift = 0u32;
    for _ in 0..5 {
        let byte = *data.get(*offset).ok_or(DexError::BadUleb128)?;
        *offset += 1;
        value |= ((byte & 0x7f) as i64) << shift;
        shift += 7;
        if byte & 0x80 == 0 {
            if shift < 64 && byte & 0x40 != 0 {
                value |= -1i64 << shift;
            }
            let narrowed = value as i32;
            if i64::from(narrowed) != value {
                return Err(DexError::BadUleb128);
            }
            return Ok(narrowed);
        }
    }
    Err(DexError::BadUleb128)
}
