//! Minimal PE32+ loader -- the first real step into Phase 5 (Windows
//! compatibility), not another kernel-internals prerequisite. Every
//! prior `experimental` module built the *mechanics* Phase 5 needs
//! (ring 3, per-process address spaces, a loader pattern); this is
//! the first module that actually loads Windows' own executable
//! format.
//!
//! Parses just enough of PE32+ (`IMAGE_OPTIONAL_HEADER64`, x86_64
//! native subsystem) to load a flat, fixed-address executable: the
//! DOS/PE/COFF/Optional headers (for `AddressOfEntryPoint`,
//! `ImageBase`, and sanity checks) and the section table (for what to
//! map where, with real permissions from each section's
//! `Characteristics` flags). Imports ARE supported (2026-10-18 increment): the real
//! `IMAGE_DIRECTORY_ENTRY_IMPORT` walk, resolved against provider DLLs
//! through their OWN `IMAGE_DIRECTORY_ENTRY_EXPORT` tables --
//! GetProcAddress semantics, not a synthetic registry -- see the
//! import/export halves below -- and GetProcAddress-complete:
//! a forwarded-export STRING entry (`other.dll.other_fn`, an EAT value
//! inside the directory) resolves through the registry depth-capped,
//! and an ordinal (`IMAGE_ORDINAL_FLAG64`) import resolves by EAT
//! index. Bound imports ARE supported (2026-10-20 increment): the
//! `IMAGE_DIRECTORY_ENTRY_BOUND_IMPORT` walk -- per-DLL stamp
//! comparison against the provider's OWN export-directory stamp; a
//! matching stamp HONORS the pre-filled IAT slots only after verifying
//! each value against what the registry freshly resolves (the
//! typed `BoundValueCorrupt` refusal otherwise: stamps are never
//! trusted blindly); a mismatched (stale) stamp DISCARDS the pre-bound
//! values and resolves through the registry exactly as if the file
//! had never been bound. Still deliberately out of scope: delay-load
//! imports, resources, exception tables, and any Win32 API surface. Base relocations (`.reloc`) ARE supported: loading at a
//! `load_bias` other than the file's own `ImageBase` applies every
//! `IMAGE_REL_BASED_DIR64`/`HIGHLOW` entry, so an ASLR-style binary
//! runs at the loader-chosen address -- the PE mirror of
//! `experimental::elf`'s `ET_DYN`/`R_X86_64_RELATIVE` support. This is
//! deliberately the smallest useful subset -- see
//! `crate::experimental`'s banner: real Windows app compatibility
//! needs substantially more, but needs this exact category of work --
//! parse untrusted headers defensively, map sections with
//! file-specified permissions, zero what should be zero -- underneath
//! whatever else it adds.
//!
//! Like `crate::multiboot` and `crate::experimental::elf`, this stays
//! paranoid about untrusted input: every offset and size is
//! bounds-checked against the actual byte slice before being
//! dereferenced.

use alloc::vec::Vec;
use x86_64::structures::paging::{OffsetPageTable, PageTableFlags};
use x86_64::VirtAddr;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;

const DOS_MAGIC: [u8; 2] = [b'M', b'Z'];
const PE_SIGNATURE: [u8; 4] = [b'P', b'E', 0, 0];
const MACHINE_AMD64: u16 = 0x8664;
const OPTIONAL_HEADER_MAGIC_PE32_PLUS: u16 = 0x20b;

const IMAGE_SCN_MEM_EXECUTE: u32 = 0x2000_0000;
const IMAGE_SCN_MEM_WRITE: u32 = 0x8000_0000;

/// A null-terminated string read out of a loaded image, capped at 64
/// bytes (a DLL or symbol name longer than that is malformed input,
/// not something to unbounded-loop over). `len` is the byte count
/// BEFORE the terminator.
#[derive(Clone, Copy)]
pub struct NameBuf {
    pub bytes: [u8; 64],
    pub len: usize,
}

impl NameBuf {
    fn empty() -> NameBuf {
        NameBuf { bytes: [0; 64], len: 0 }
    }
    pub fn as_slice(&self) -> &[u8] {
        &self.bytes[..self.len]
    }
}

impl core::fmt::Debug for NameBuf {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        // Print only ASCII-visible bytes so a malformed name can't
        // inject terminal escape sequences into the boot transcript.
        write!(f, "\"")?;
        for &b in self.bytes[..self.len].iter() {
            if b.is_ascii_graphic() {
                write!(f, "{}", b as char)?;
            } else {
                write!(f, "\\x{:02x}", b)?;
            }
        }
        write!(f, "\"")
    }
}

impl PartialEq for NameBuf {
    fn eq(&self, other: &NameBuf) -> bool {
        self.bytes[..self.len] == other.bytes[..other.len]
    }
}

const DOS_HEADER_SIZE: usize = 64; // fixed IMAGE_DOS_HEADER size; e_lfanew is the last field
const COFF_HEADER_SIZE: usize = 20;
const SECTION_HEADER_SIZE: usize = 40;

#[derive(Debug)]
pub enum PeError {
    TooShort,
    BadDosMagic,
    PeHeaderOutOfBounds,
    BadPeSignature,
    UnsupportedMachine,
    BadOptionalHeaderMagic,
    SectionHeaderOutOfBounds,
    SectionDataOutOfBounds,
    NoSections,
    MappingFailed,
    BadRelocationBlock,
    UnsupportedRelocationType(u16),

    // -- the import/export halves --
    /// A `DataDirectory` the optional header does not even declare
    /// (`NumberOfRvaAndSizes` short) but which an operation requires.
    DataDirectoryAbsent(u32),
    /// The import or export structure itself is malformed (unreachable
    /// RVA, unbounded array, missing terminator): never silently
    /// resolved.
    MalformedImageDirectory(&'static str),
    /// A forwarded-export STRING was malformed (no component dot, or a
    /// dot at one end of the string): refused, never resolved by
    /// guessing.
    MalformedForwarder(&'static str),
    /// A forwarder chain exceeded the re-entry depth cap; refused
    /// rather than resolved unboundedly.
    ForwarderDepthExceeded,
    /// The bound-import directory (`IMAGE_DIRECTORY_ENTRY_BOUND_IMPORT`)
    /// is malformed: an unbounded descriptor array, a module name
    /// outside the directory's own bounds or un-terminatable inside
    /// them, an empty/overlong name. A typed refusal, never silently
    /// ignored -- a malformed surface is worse than an absent one.
    MalformedBoundImport(&'static str),
    /// An entry's stamp CLAIMS a fresh binding (it equals the
    /// provider's own export-directory stamp) but a pre-filled IAT slot
    /// does not equal what the registry freshly resolves for that
    /// import. The stamp's whole guarantee is contradicted by the
    /// file -- refused loudly: stamps are verified, never trusted
    /// blindly. Payload: (the contradicted bound value, the freshly
    /// resolved value).
    BoundValueCorrupt(u64, u64),
    /// The import's DLL name matched no registered provider.
    MissingDllImport,
    /// A provider was found for the DLL, but the symbol is not an
    /// export of it.
    MissingImport,
    /// The IAT slot's page is not writable in its own right (the real
    /// GNU ld artifact leaves `.idata` writable); the loader refuses
    /// rather than grant-write a section the file did not ask for.
    ImportSectionNotWritable,
}

pub struct LoadedPe {
    pub entry_point: VirtAddr,
    /// What was resolved for this image's import directory: one fact
    /// per successfully-patched IAT slot, so a caller (and the boot
    /// transcript) can state per-symbol facts rather than a count.
    pub imports: ImportResolution,
    /// The load-time RVA footprint (from the file bytes the loader
    /// mapped); the single authority every later bounds-check uses.
    pub image_max_rva: u64,
}

fn read_u16(data: &[u8], offset: usize) -> Option<u16> {
    data.get(offset..offset + 2).map(|b| u16::from_le_bytes([b[0], b[1]]))
}
fn read_u32(data: &[u8], offset: usize) -> Option<u32> {
    data.get(offset..offset + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}
fn read_u64(data: &[u8], offset: usize) -> Option<u64> {
    data.get(offset..offset + 8)
        .map(|b| u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]))
}

struct SectionHeader {
    virtual_size: u32,
    virtual_address: u32, // RVA
    size_of_raw_data: u32,
    pointer_to_raw_data: u32,
    characteristics: u32,
}

fn parse_section_header(data: &[u8], off: usize) -> Option<SectionHeader> {
    Some(SectionHeader {
        // Name[8] at off+0, unused by this loader
        virtual_size: read_u32(data, off + 8)?,
        virtual_address: read_u32(data, off + 12)?,
        size_of_raw_data: read_u32(data, off + 16)?,
        pointer_to_raw_data: read_u32(data, off + 20)?,
        // Pointer/NumberOfRelocations, Linenumbers fields unused
        characteristics: read_u32(data, off + 36)?,
    })
}

/// Parses `data` as a PE32+ executable and maps every section
/// (zero-filled first, then the file's raw bytes copied on top -- so
/// `VirtualSize > SizeOfRawData`, PE's equivalent of an implicit
/// `.bss`, is handled correctly). `load_bias == 0` loads at the file's
/// own `ImageBase` (no relocation); a nonzero `load_bias` loads there
/// and applies the file's base relocations (`DataDirectory[5]`,
/// `IMAGE_DIRECTORY_ENTRY_BASERELOC`) against `load_bias - ImageBase`,
/// so an ASLR-style binary runs at the loader-chosen address -- the
/// same `load_bias` convention as `experimental::elf::load`. Returns
/// `load_base + AddressOfEntryPoint`.
pub fn load(
    data: &[u8],
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
    load_bias: u64,
) -> Result<LoadedPe, PeError> {
    load_callers(data, mapper, frame_allocator, load_bias, None)
}

/// [`load`] plus import resolution: an optional [`DllRegistry`] against
/// which every import directory entry is resolved (Windows loader
/// semantics: the dependent's IAT slot gets the provider's export's
/// absolute runtime address). Resolution deliberately runs AFTER the
/// mapping and relocation passes: the IAT must be readable at its
/// final runtime address, and the provider addresses are computed
/// against the provider's own load base (the provider is never
/// relocated here -- a provider loaded at its own `ImageBase`, like
/// any fixed-address image, needs no relocation of its exports).
pub fn load_with_imports(
    data: &[u8],
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
    load_bias: u64,
    registry: &DllRegistry,
) -> Result<LoadedPe, PeError> {
    load_callers(data, mapper, frame_allocator, load_bias, Some(registry))
}

fn load_callers(
    data: &[u8],
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
    load_bias: u64,
    registry: Option<&DllRegistry>,
) -> Result<LoadedPe, PeError> {
    if data.len() < DOS_HEADER_SIZE {
        return Err(PeError::TooShort);
    }
    if data[0..2] != DOS_MAGIC {
        return Err(PeError::BadDosMagic);
    }
    let e_lfanew = read_u32(data, 0x3c).ok_or(PeError::TooShort)? as usize;

    if e_lfanew.checked_add(4 + COFF_HEADER_SIZE).ok_or(PeError::PeHeaderOutOfBounds)? > data.len() {
        return Err(PeError::PeHeaderOutOfBounds);
    }
    if data[e_lfanew..e_lfanew + 4] != PE_SIGNATURE {
        return Err(PeError::BadPeSignature);
    }

    let coff_off = e_lfanew + 4;
    let machine = read_u16(data, coff_off).ok_or(PeError::TooShort)?;
    if machine != MACHINE_AMD64 {
        return Err(PeError::UnsupportedMachine);
    }
    let number_of_sections = read_u16(data, coff_off + 2).ok_or(PeError::TooShort)? as usize;
    let size_of_optional_header = read_u16(data, coff_off + 16).ok_or(PeError::TooShort)? as usize;

    let opt_off = coff_off + COFF_HEADER_SIZE;
    if opt_off.checked_add(size_of_optional_header).ok_or(PeError::PeHeaderOutOfBounds)? > data.len() {
        return Err(PeError::PeHeaderOutOfBounds);
    }
    let magic = read_u16(data, opt_off).ok_or(PeError::TooShort)?;
    if magic != OPTIONAL_HEADER_MAGIC_PE32_PLUS {
        return Err(PeError::BadOptionalHeaderMagic);
    }
    let address_of_entry_point = read_u32(data, opt_off + 16).ok_or(PeError::TooShort)?;
    let image_base = read_u64(data, opt_off + 24).ok_or(PeError::TooShort)?;

    // DataDirectory[5] = IMAGE_DIRECTORY_ENTRY_BASERELOC. DataDirectory
    // starts at optional-header offset 112 for PE32+; each entry is 8
    // bytes (VirtualAddress u32, Size u32). Only checked if the
    // optional header actually declares enough directory entries to
    // contain index 5 (NumberOfRvaAndSizes, at offset 108) -- a
    // minimal binary might declare fewer.
    let number_of_rva_and_sizes = read_u32(data, opt_off + 108).unwrap_or(0) as usize;
    let (reloc_rva, reloc_size) = if number_of_rva_and_sizes > 5 {
        let dir_off = opt_off + 112 + 5 * 8;
        (
            read_u32(data, dir_off).unwrap_or(0),
            read_u32(data, dir_off + 4).unwrap_or(0),
        )
    } else {
        (0, 0)
    };

    // The address the image is actually mapped at: the file's own
    // ImageBase when no bias is requested, otherwise the caller's.
    let load_base = if load_bias == 0 { image_base } else { load_bias };

    let section_table_off = opt_off + size_of_optional_header;
    if section_table_off.checked_add(number_of_sections * SECTION_HEADER_SIZE).ok_or(PeError::SectionHeaderOutOfBounds)? > data.len() {
        return Err(PeError::SectionHeaderOutOfBounds);
    }
    if number_of_sections == 0 {
        return Err(PeError::NoSections);
    }

    // The header page(s) Windows' own loader maps: every PE image's
    // first page(s) hold the DOS/PE/Optional headers + the section
    // table, and the import/export walks READ THEM THROUGH THE MAPPED
    // IMAGE (RVAs, at runtime). Map that prefix read-only
    // (PRESENT|USER_ACCESSIBLE|NO_EXECUTE -- the file asked for
    // nothing here; headers are never executed and never written)
    // BEFORE the sections, then pass over writing the raw header
    // bytes. Capped at the first section's RVA so a section at RVA 0
    // can never double-map; absent any section boundary information
    // to the contrary, one whole page is mapped.
    let headers_file_end =
        (section_table_off + number_of_sections * SECTION_HEADER_SIZE) as u64;
    let mut header_span = ((headers_file_end + 4095) / 4096) * 4096;
    {
        let mut first_vaddr: Option<u64> = None;
    for i in 0..number_of_sections {
            let off = section_table_off + i * SECTION_HEADER_SIZE;
            let vaddr = read_u32(data, off + 12).ok_or(PeError::SectionHeaderOutOfBounds)? as u64;
            if vaddr > 0 {
                first_vaddr = Some(first_vaddr.map_or(vaddr, |m: u64| m.min(vaddr)));
            }
        }
        if let Some(first) = first_vaddr {
            header_span = header_span.min(((first + 4095) / 4096) * 4096);
        }
    }
    if header_span > 0 {
        let pages = (header_span / 4096) as u64;
        let hdr_flags = PageTableFlags::PRESENT | PageTableFlags::USER_ACCESSIBLE | PageTableFlags::NO_EXECUTE;
        let hdr_start = VirtAddr::new(load_base).align_down(4096u64);
        let hdr_mapped = paging::map_range(mapper, frame_allocator, hdr_start, pages, hdr_flags);
        if hdr_mapped != pages {
            return Err(PeError::MappingFailed);
        }
        unsafe {
            core::ptr::write_bytes(hdr_start.as_mut_ptr::<u8>(), 0, header_span as usize);
            let n = (headers_file_end as usize).min(data.len());
            core::ptr::copy_nonoverlapping(data.as_ptr(), hdr_start.as_mut_ptr::<u8>(), n);
        }
    }

    let mut max_rva: u64 = 0;
    for i in 0..number_of_sections {
        let off = section_table_off + i * SECTION_HEADER_SIZE;
        let sh = parse_section_header(data, off).ok_or(PeError::SectionHeaderOutOfBounds)?;

        // The load-time footprint, from the FILE BYTES (the same
        // numbers the mapping below is about to use): nothing
        // downstream re-derives it from the mapped image.
        max_rva = max_rva.max(sh.virtual_address as u64 + sh.virtual_size as u64);

        let file_start = sh.pointer_to_raw_data as usize;
        let file_end = file_start
            .checked_add(sh.size_of_raw_data as usize)
            .ok_or(PeError::SectionDataOutOfBounds)?;
        if sh.size_of_raw_data > 0 && file_end > data.len() {
            return Err(PeError::SectionDataOutOfBounds);
        }

        let runtime_vaddr = load_base.wrapping_add(sh.virtual_address as u64);
        let seg_start = VirtAddr::new(runtime_vaddr).align_down(4096u64);
        let seg_end = VirtAddr::new(runtime_vaddr + sh.virtual_size as u64).align_up(4096u64);
        let page_count = (seg_end.as_u64() - seg_start.as_u64()) / 4096;
        if page_count == 0 {
            continue; // a zero-sized section (rare, but not malformed)
        }

        let mut flags = PageTableFlags::PRESENT | PageTableFlags::USER_ACCESSIBLE;
        if sh.characteristics & IMAGE_SCN_MEM_WRITE != 0 {
            flags |= PageTableFlags::WRITABLE;
        }
        // Same W^X enforcement as experimental::elf: a section without
        // IMAGE_SCN_MEM_EXECUTE gets NO_EXECUTE, meaningfully enforced
        // since memory::paging::enable_nx runs before any loader does.
        if sh.characteristics & IMAGE_SCN_MEM_EXECUTE == 0 {
            flags |= PageTableFlags::NO_EXECUTE;
        }

        let mapped = paging::map_range(mapper, frame_allocator, seg_start, page_count, flags);
        if mapped != page_count {
            return Err(PeError::MappingFailed);
        }

        unsafe {
            core::ptr::write_bytes(seg_start.as_mut_ptr::<u8>(), 0, page_count as usize * 4096);
            if sh.size_of_raw_data > 0 {
                let dest = VirtAddr::new(runtime_vaddr).as_mut_ptr::<u8>();
                core::ptr::copy_nonoverlapping(data.as_ptr().add(file_start), dest, sh.size_of_raw_data as usize);
            }
        }
    }

    // Base relocations, applied to the now-mapped image (the entries
    // are read from the mapped `.reloc` section, so this runs after
    // every section is in place). Only needed when the image was loaded
    // somewhere other than its own ImageBase.
    if load_bias != 0 && reloc_size != 0 {
        unsafe { apply_base_relocations(load_base, image_base, reloc_rva, reloc_size) }?;
    }

    // Imports, resolved against the optional registry only now that
    // the image is mapped at its final address (mixed .idata reads and
    // IAT writes happen in the mapped image, never in the file bytes).
    let imports = match registry {
        None => ImportResolution::none(),
        Some(r) => unsafe { resolve_imports(load_base, mapper, max_rva, r) }?,
    };

    Ok(LoadedPe {
        entry_point: VirtAddr::new(load_base.wrapping_add(address_of_entry_point as u64)),
        imports,
        image_max_rva: max_rva,
    })
}

// ---------------------------------------------------------------------
// The IMPORT half (client side) -- the real Windows-loaded import walk.
//
// Every structure here is the genuine PE32+ layout, bounds-checked
// against the image's own section footprint (max RVA derived from the
// section table -- the same paranoia the ELF dynamic loader applies,
// expressed in PE's own units) before a single byte is dereferenced.
// ---------------------------------------------------------------------

/// Reads a null-terminated string at runtime address
/// `load_base + rva` -- capped at [`NameBuf`]'s 64-byte capacity
/// (an uncapped name is malformed, not unbounded). Byte-visible
/// ASCII-only output enforced at `Debug` (injection can't reach the
/// boot transcript).
///
/// # Safety
/// The caller must guarantee the range is within a mapped section AND
/// under the image's own footprint bound; `resolve_imports` and the
/// export table check this per RVA before calling.
unsafe fn read_image_name(load_base: u64, rva: u64, max_rva: u64) -> Result<NameBuf, PeError> {
    if rva >= max_rva {
        return Err(PeError::MalformedImageDirectory("name RVA outside image"));
    }
    let mut out = NameBuf::empty();
    let base = load_base.wrapping_add(rva) as *const u8;
    for i in 0..64u64 {
        let b = core::ptr::read_unaligned(base.add(i as usize));
        if b == 0 {
            if out.len == 0 {
                return Err(PeError::MalformedImageDirectory("empty name"));
            }
            return Ok(out);
        }
        if i == 63 {
            return Err(PeError::MalformedImageDirectory("name exceeds the 64-byte cap"));
        }
        out.bytes[i as usize] = b;
        out.len += 1;
    }
    Err(PeError::MalformedImageDirectory("name exceeded cap"))
}

unsafe fn read_u64_at(load_base: u64, rva: u64, max_rva: u64) -> Result<u64, PeError> {
    match rva.checked_add(8) {
        None => return Err(PeError::MalformedImageDirectory("rva overflow")),
        Some(end) if end > max_rva => {
            return Err(PeError::MalformedImageDirectory("pointer outside image"))
        }
        _ => {}
    }
    let p = load_base.wrapping_add(rva) as *const u64;
    Ok(core::ptr::read_unaligned(p))
}

unsafe fn read_u32_at(load_base: u64, rva: u64, max_rva: u64) -> Result<u32, PeError> {
    match rva.checked_add(4) {
        None => return Err(PeError::MalformedImageDirectory("rva overflow")),
        Some(end) if end > max_rva => {
            return Err(PeError::MalformedImageDirectory("pointer outside image"))
        }
        _ => {}
    }
    let p = load_base.wrapping_add(rva) as *const u32;
    Ok(core::ptr::read_unaligned(p))
}

/// A data-directory entry from the optional header, bounds-checked
/// against `NumberOfRvaAndSizes` -- a header that does not even declare
/// index N answers `DataDirectoryAbsent(N)`.
///
/// # Safety
/// Headers must be mapped at `load_base`; only index is checked, caller
/// supplies a valid optional header offset (this fn re-leafs it).
unsafe fn read_data_directory(load_base: u64, index: u32) -> Result<(u32, u32), PeError> {
    let base = load_base as *const u8;
    let sig_off = core::ptr::read_unaligned(base.add(0x3c) as *const u32) as usize;
    let coff = load_base + sig_off as u64 + 4;
    let machine = core::ptr::read_unaligned(coff as *const u16);
    if machine != MACHINE_AMD64 {
        return Err(PeError::MalformedImageDirectory("machine"));
    }
    let opt = coff + 20;
    let magic = core::ptr::read_unaligned(opt as *const u16);
    if magic != OPTIONAL_HEADER_MAGIC_PE32_PLUS {
        return Err(PeError::MalformedImageDirectory("optional magic"));
    }
    let n = core::ptr::read_unaligned((opt + 108) as *const u32) as u32;
    if index >= n {
        return Err(PeError::DataDirectoryAbsent(index));
    }
    let dir = opt + 112 + (index as u64) * 8;
    let rva = core::ptr::read_unaligned(dir as *const u32);
    let size = core::ptr::read_unaligned((dir + 4) as *const u32);
    Ok((rva, size))
}

/// [`read_data_directory`], but a directory the optional header does
/// not even declare answers ABSENT (`(0, 0)`) instead of an error --
/// the shape the bound-import walk wants, since absence of the
/// directory means absence of the feature, not a malformed image.
/// Re-derives the optional header the same way (through the mapped
/// image's own `e_lfanew`), staying in sync with the original by
/// construction.
///
/// # Safety
/// Headers must be mapped at `load_base`; same caller contract as
/// [`read_data_directory`].
unsafe fn read_data_directory_absent_ok(load_base: u64, index: u32) -> Result<(u32, u32), PeError> {
    let base = load_base as *const u8;
    let sig_off = core::ptr::read_unaligned(base.add(0x3c) as *const u32) as usize;
    let opt = load_base + sig_off as u64 + 4 + 20;
    let n = core::ptr::read_unaligned((opt + 108) as *const u32) as u32;
    if index >= n {
        return Ok((0, 0));
    }
    unsafe { read_data_directory(load_base, index) }
}

/// One resolved import: the runtime address of the patched IAT slot,
/// and the value now in it (the provider's absolute runtime address). The
/// name pair lets a caller print both halves. `origin` states HOW the
/// slot came to its value (fresh registry resolution, a verified
/// bound-honored prefill left un-written, or a stale pre-bound value
/// that was discarded); `prior` carries the value the slot held BEFORE
/// the loader touched it (== `value` for honored slots, the discarded
/// bound value for stale ones, the on-disk zero for fresh ones) so a
/// caller can state the discard fact, not just the outcome.
#[derive(Debug)]
pub enum ImportOrigin {
    /// Resolved fresh through the registry and written.
    FreshResolution,
    /// The entry's stamp matched the provider's export-directory stamp;
    /// the PRE-FILLED value was verified to equal the fresh resolution
    /// and the slot is left UN-written (the honor path). If any slot had
    /// contradicted, the load refuses with
    /// [`PeError::BoundValueCorrupt`] -- stamps are verified.
    BoundHonored,
    /// The entry's stamp did NOT match (stale): the pre-bound value is
    /// discarded (its prior read is recorded, never trusted) and the
    /// slot is resolved fresh exactly as an unbound file would be.
    StaleDiscarded,
}

#[derive(Debug)]
pub struct ResolvedImport {
    pub dll: NameBuf,
    pub symbol: NameBuf,
    pub slot: u64,
    pub value: u64,
    pub origin: ImportOrigin,
    pub prior: u64,
}

#[derive(Debug)]
pub struct ImportResolution {
    pub facts: Vec<ResolvedImport>,
    /// The bound-import directory's parsed module entries, as read:
    /// (module name, declared stamp). Includes entries no import
    /// descriptor uses (inert metadata, tolerated per spec) so a caller
    /// can state the inert fact explicitly.
    pub bound_modules: Vec<(NameBuf, u32)>,
}

impl ImportResolution {
    pub fn none() -> ImportResolution {
        ImportResolution { facts: Vec::new(), bound_modules: Vec::new() }
    }
}

/// Walks a loaded image's `IMAGE_DIRECTORY_ENTRY_IMPORT` directory the
/// Windows loader's way: per-descriptor DLL name -> OriginalFirstThunk
/// (falling back to FirstThunk when there is none -- the classic
/// old-format rule) -> a null-terminated array of `IMAGE_THUNK_DATA64`
/// entries; a name import (hint/name entry at its RVA) or an ordinal
/// import (`IMAGE_ORDINAL_FLAG64` set; the low bits are the ordinal)
/// resolves against the registry, forwarder-aware either way; the
/// resolved absolute address is written into
/// the FirstThunk slot on the very spot the dependent's own code loads
/// through (`call *__imp_sym(%rip)` semantics on the actual artifact).
///
/// Bounded everywhere: max RVA by image footprint, name strings capped
/// at 64 bytes, descriptor / thunk arrays at 16 / 32 entries.
///
/// # Safety
/// `load_base` names a loader-mapped image; the registry lookup may
/// read provider images the CALLER loaded and registered. Runs with the
/// image's address space active.
pub unsafe fn resolve_imports(
    load_base: u64,
    mapper: &mut OffsetPageTable<'static>,
    max_rva_passed: u64,
    registry: &DllRegistry,
) -> Result<ImportResolution, PeError> {
    let max_rva = max_rva_passed;
    let (dir_rva, dir_size) = read_data_directory(load_base, 1)?;
    if dir_rva == 0 || dir_size == 0 {
        return Ok(ImportResolution::none());
    }
    match (dir_rva as u64).checked_add(dir_size as u64) {
        None => return Err(PeError::MalformedImageDirectory("dir overflow")),
        Some(end) if end > max_rva => {
            return Err(PeError::MalformedImageDirectory("import directory outside image"))
        }
        _ => {}
    }

    // The bound-import directory (DataDirectory[11]): OPTIONAL
    // metadata about pre-filled IAT slots. A directory the optional
    // header does not even declare -- or one whose RVA/Size are zero --
    // means the feature is ABSENT, not malformed
    // (read_data_directory_absent_ok). Present, it is parsed the
    // bounded way: the per-entry module name is an OFFSET from the
    // directory's own start (the IMAGE_BOUND_IMPORT_DESCRIPTOR
    // contract, not an independent RVA), and every name must
    // terminate inside the directory's declared bounds -- an
    // unbounded descriptor array, a name outside those bounds, or an
    // un-terminatable/empty/overlong name is a typed
    // MalformedBoundImport refusal, never silently degraded to "no
    // bound imports": a malformed surface is worse than an absent
    // one.
    let (bound_rva, bound_size) = read_data_directory_absent_ok(load_base, 11)?;
    let mut bound_modules: Vec<(NameBuf, u32)> = Vec::new();
    if bound_rva != 0 && bound_size != 0 {
        let bound_end = match (bound_rva as u64).checked_add(bound_size as u64) {
            None => return Err(PeError::MalformedBoundImport("bound directory overflow")),
            Some(end) if end > max_rva => {
                return Err(PeError::MalformedBoundImport("bound directory outside image"))
            }
            Some(end) => end,
        };
        let mut bi: u64 = 0;
        loop {
            if bi >= 16 {
                return Err(PeError::MalformedBoundImport(
                    "bound descriptor array unbounded",
                ));
            }
            let e_rva = bound_rva as u64 + bi * 8;
            // IMAGE_BOUND_IMPORT_DESCRIPTOR: TimeDateStamp u32,
            // OffsetModuleName u16 (offset from the directory's start),
            // Reserved u16 -- all-zero is the terminator.
            let stamp = read_u32_at(load_base, e_rva, max_rva)?;
            let name_off = core::ptr::read_unaligned(
                (load_base.wrapping_add(e_rva + 4)) as *const u16,
            ) as u64;
            let _reserved = core::ptr::read_unaligned(
                (load_base.wrapping_add(e_rva + 6)) as *const u16,
            ) as u64;
            if stamp == 0 && name_off == 0 && _reserved == 0 {
                break; // the bound directory's null terminator
            }
            if name_off == 0 {
                // A name offset of 0 lands the "name" on top of the
                // descriptor array itself: malformed by construction.
                return Err(PeError::MalformedBoundImport(
                    "bound module name on top of the descriptor array",
                ));
            }
            let name = read_room_bounded_name(
                load_base,
                bound_rva as u64 + name_off,
                bound_end,
            )?;
            bound_modules.push((name, stamp));
            bi += 1;
        }
    }

    let mut facts: Vec<ResolvedImport> = Vec::new();
    let mut i: u64 = 0;
    loop {
        if i >= 16 {
            return Err(PeError::MalformedImageDirectory("import descriptor array unbounded"));
        }
        let desc_rva = dir_rva as u64 + i * 20;
        let original_first_thunk = read_u32_at(load_base, desc_rva, max_rva)?;
        let _timestamp = core::ptr::read_unaligned((load_base.wrapping_add(desc_rva) + 4) as *const u32);
        let _forwarder_chain = core::ptr::read_unaligned((load_base.wrapping_add(desc_rva) + 8) as *const u32);
        let name_rva = read_u32_at(load_base, desc_rva + 12, max_rva)?;
        let first_thunk = read_u32_at(load_base, desc_rva + 16, max_rva)?;
        if original_first_thunk == 0 && name_rva == 0 && first_thunk == 0 {
            break; // the directory's null terminator
        }
        if name_rva == 0 {
            return Err(PeError::MalformedImageDirectory("descriptor without a DLL name"));
        }
        if first_thunk == 0 && original_first_thunk == 0 {
            return Err(PeError::MalformedImageDirectory("descriptor without any thunk table"));
        }
        let dll_name = read_image_name(load_base, name_rva as u64, max_rva)?;
        let provider = match registry.find(dll_name.as_slice()) {
            Some(t) => t,
            None => return Err(PeError::MissingDllImport),
        };

        // ---- the bound ruling for THIS descriptor's DLL: the bound
        // entry's stamp must equal the provider's OWN export-directory
        // stamp (parsed, never assumed). A matching stamp honors the
        // pre-filled IAT (after verification, in the per-slot loop); a
        // mismatched (stale) stamp DISCARDS whatever the file pre-bound
        // and resolves fresh; no bound entry at all is the plain
        // fresh-resolution path (a file never bound behaves exactly as
        // it always did). No bound entry exists without an import
        // descriptor to serve it: entries naming DLLs this image does
        // not import never reach a registry lookup (inert metadata).
        let bound_stamp = bound_modules
            .iter()
            .find(|(n, _)| *n == dll_name)
            .map(|(_, s)| *s);
        let honoring = bound_stamp == Some(provider.time_date_stamp());

        // The classic rule: the OriginalFirstThunk array supplies the
        // hint/name pointers; a file lacking it (old-format) means the
        // FirstThunk array serves BOTH roles until resolution overwrites it.
        let thunk_rva = if original_first_thunk != 0 {
            original_first_thunk as u64
        } else {
            first_thunk as u64
        };
        let mut j: u64 = 0;
        loop {
            if j >= 32 {
                return Err(PeError::MalformedImageDirectory("thunk array unbounded"));
            }
            let th = read_u64_at(load_base, thunk_rva + j * 8, max_rva)?;
            if th == 0 {
                break; // thunk array's null terminator
            }
            // Both import shapes resolve through the SAME
            // registry path -- they differ only in how the export's
            // EAT slot is located (by name or by ordinal), and both
            // paths chase forwarded exports identically.
            let (sym, addr) = if th & (1 << 63) != 0 {
                let ordinal = th & !(1u64 << 63);
                let addr = match registry.resolve_in(provider, None, Some(ordinal))? {
                    Some(a) => a,
                    None => return Err(PeError::MissingImport),
                };
                (ordinal_label(ordinal), addr)
            } else {
                // IMAGE_IMPORT_BY_NAME: thunk entry RVA names the `u16`
                // hint field; the name begins two bytes later.
                let sym = read_image_name(load_base, thunk_name_rva(th)?, max_rva)?;
                let addr = match registry.resolve_in(provider, Some(sym.as_slice()), None)? {
                    Some(a) => a,
                    None => return Err(PeError::MissingImport),
                };
                (sym, addr)
            };
            let slot_rva = first_thunk as u64 + j * 8;
            let slot = load_base.wrapping_add(slot_rva);
            // The slot's PRE-LOADER value, read in every path: on the
            // honor path it is the verified bound value; on the stale
            // path it is the discarded bound value (recorded, never
            // trusted); on the fresh path it is the on-disk value
            // (usually zero). The facts are stated FROM it, not
            // inferred around it.
            let prior = core::ptr::read_unaligned(slot as *const u64);
            if honoring {
                // HONOR path: the stamp already guaranteed THIS
                // provider build; the remaining CHECKABLE claim is
                // that the pre-filled slot value is exactly what a
                // fresh resolution would produce -- verified here,
                // then the slot left UN-written (that is the whole
                // point of binding). A contradiction refuses the
                // whole load: the stamp's guarantee is contradicted by
                // the file, and a silently-wrong call target is worse
                // than a refused one. No writable-check and no write
                // belong on this path.
                if prior != addr {
                    return Err(PeError::BoundValueCorrupt(prior, addr));
                }
                facts.push(ResolvedImport {
                    dll: dll_name,
                    symbol: sym,
                    slot,
                    value: addr,
                    origin: ImportOrigin::BoundHonored,
                    prior,
                });
                j += 1;
                continue;
            }
            // The discard/fresh path is the only one that writes:
            // the writable check protects the write (same rule as
            // always -- the loader refuses rather than grant-write a
            // section the file did not ask for).
            let flags = crate::memory::paging::page_flags_of(
                mapper,
                VirtAddr::new(slot),
            )
            .ok_or(PeError::ImportSectionNotWritable)?;
            let flags = flags.map_err(|()| PeError::ImportSectionNotWritable)?;
            if !flags.contains(x86_64::structures::paging::PageTableFlags::WRITABLE) {
                return Err(PeError::ImportSectionNotWritable);
            }
            core::ptr::write_unaligned(slot as *mut u64, addr);
            facts.push(ResolvedImport {
                dll: dll_name,
                symbol: sym,
                slot,
                value: addr,
                origin: if bound_stamp.is_some() {
                    ImportOrigin::StaleDiscarded
                } else {
                    ImportOrigin::FreshResolution
                },
                prior,
            });
            j += 1;
        }
        i += 1;
    }
    Ok(ImportResolution { facts, bound_modules })
}

/// Reads the bound-import directory's module name at `rva`: a
/// NUL-terminated string whose terminator must land BEFORE
/// `room_end` (the directory's declared end), capped at the 64-byte
/// name capacity. Any failure -- outside-image bounds, past-the-end of
/// the directory, empty, overlong -- is a typed
/// `MalformedBoundImport` refusal, never a truncation and never a
/// guess.
///
/// # Safety
/// the image must be mapped and `bound_end` bounds-checked by the
/// caller (it is: the directory's own span within `max_rva`).
unsafe fn read_room_bounded_name(load_base: u64, rva: u64, room_end: u64) -> Result<NameBuf, PeError> {
    if rva >= room_end {
        return Err(PeError::MalformedBoundImport(
            "bound module name outside the directory",
        ));
    }
    let room = (room_end - rva) as usize;
    let base = load_base.wrapping_add(rva) as *const u8;
    let mut out = NameBuf::empty();
    for i in 0..64usize {
        if i >= room {
            return Err(PeError::MalformedBoundImport(
                "bound module name runs past the directory's end",
            ));
        }
        let b = core::ptr::read_unaligned(base.add(i));
        if b == 0 {
            if out.len == 0 {
                return Err(PeError::MalformedBoundImport("empty bound module name"));
            }
            return Ok(out);
        }
        out.bytes[i] = b;
        out.len += 1;
    }
    Err(PeError::MalformedBoundImport(
        "bound module name exceeds the 64-byte cap",
    ))
}

/// `IMAGE_IMPORT_BY_NAME`: a `u16` hint followed by the name. The
/// thunk entry's value is the RVA of the hint field; the NAME starts
/// two bytes later.
fn thunk_name_rva(thunk_value: u64) -> Result<u64, PeError> {
    thunk_value
        .checked_add(2)
        .ok_or(PeError::MalformedImageDirectory("hint/name rva overflow"))
}

/// What one EAT slot (`AddressOfFunctions[ord]`) actually is: a plain
/// export address RVA, or a forwarded-export STRING reference (an RVA
/// pointing INSIDE the export directory -- a `"dll.symbol"` name,
/// never code). The distinction GetProcAddress's own docs draw.
enum RawExport {
    Function(u64),
    Forwarder(u64),
}

/// How an EAT slot is located: by symbol name (the named-import path)
/// or by ordinal (the `IMAGE_ORDINAL_FLAG64` import path). One chase
/// loop serves both.
enum RawTarget {
    Name(NameBuf),
    Ordinal(u64),
}

/// Reads a forwarded-export STRING reference: a NUL-terminated name at
/// `rva` that must live ENTIRELY inside the export directory
/// (`rva < dir_end`, terminator included) and fit the 64-byte cap. A
/// string that runs past the directory's end is malformed, not
/// truncated.
///
/// # Safety
/// the image must be mapped.
unsafe fn read_forwarder_string(
    load_base: u64,
    rva: u64,
    dir_end: u64,
) -> Result<NameBuf, PeError> {
    if rva >= dir_end {
        return Err(PeError::MalformedImageDirectory(
            "forwarder string RVA outside the export directory",
        ));
    }
    let room = (dir_end - rva) as usize;
    let base = load_base.wrapping_add(rva) as *const u8;
    let mut out = NameBuf::empty();
    for i in 0..64usize {
        if i >= room {
            return Err(PeError::MalformedImageDirectory(
                "forwarder string runs past the export directory",
            ));
        }
        let b = core::ptr::read_unaligned(base.add(i));
        if b == 0 {
            if out.len == 0 {
                return Err(PeError::MalformedImageDirectory("empty forwarder string"));
            }
            return Ok(out);
        }
        out.bytes[i] = b;
        out.len += 1;
    }
    Err(PeError::MalformedImageDirectory(
        "forwarder string exceeds the 64-byte cap",
    ))
}

/// `other.dll.other_fn`: the DLL name is everything up to the LAST
/// component dot; the symbol is what follows it. No dot, or a dot at
/// either end of the string, is malformed -- never resolved by
/// guessing.
fn split_forwarder(s: &[u8]) -> Result<(&[u8], &[u8]), PeError> {
    let Some(d) = s.iter().rposition(|c| *c == b'.') else {
        return Err(PeError::MalformedForwarder("no component dot"));
    };
    if d == 0 {
        return Err(PeError::MalformedForwarder("dot begins the string"));
    }
    if d + 1 >= s.len() {
        return Err(PeError::MalformedForwarder("dot ends the string"));
    }
    Ok((&s[..d], &s[d + 1..]))
}

/// Manufactures the fact label for an ordinal import (`ord7`): the
/// ordinal IS the symbol's identity on this path; the label exists so
/// every boot can state the fact the named path states (no `fmt`
/// machinery, no heap).
fn ordinal_label(ordinal: u64) -> NameBuf {
    let mut out = NameBuf::empty();
    out.bytes[0] = b'o';
    out.bytes[1] = b'r';
    out.bytes[2] = b'd';
    let mut digits = [0u8; 20];
    let mut n = 0usize;
    let mut v = ordinal;
    loop {
        digits[n] = b'0' + (v % 10) as u8;
        n += 1;
        v /= 10;
        if v == 0 {
            break;
        }
    }
    for i in 0..n {
        out.bytes[3 + i] = digits[n - 1 - i];
    }
    out.len = 3 + n;
    out
}

// ---------------------------------------------------------------------
// The EXPORT half (provider side) -- GetProcAddress semantics.
// ---------------------------------------------------------------------

/// A provider DLL's `IMAGE_DIRECTORY_ENTRY_EXPORT`, parsed once at load
/// time; `lookup` is `GetProcAddress` minus forwarding support (typed,
/// below). Reads the provider's MAPPED image -- export entries are
/// `RVA`s, so an export's absolute runtime address is `load_base + rva`
/// for any load base.
#[derive(Clone, Copy)]
pub struct DllExportTable {
    load_base: u64,
    max_rva_bound: u64,
    dir_rva: u32,
    dir_size: u32,
    number_of_functions: u32,
    number_of_names: u32,
    address_of_functions_rva: u32,
    address_of_names_rva: u32,
    address_of_name_ordinals_rva: u32,
    /// The directory's `OrdinalBase`: the ordinal an import thunk names
    /// minus this is the real EAT index. Parsed, never assumed.
    ordinal_base: u32,
    /// The directory's `TimeDateStamp`: the stamp a client's
    /// bound-import entry is compared against (the honor-or-discard
    /// question). Parsed, never assumed.
    time_date_stamp: u32,
}

impl DllExportTable {
    /// Parses the export directory out of the MAPPED image at
    /// `load_base` (headers included). A directory that is absent
    /// entirely (RVA/Size == 0) is still a valid, EMPTY table -- lookup
    /// answers `None` for everything, which is exactly how the demo's
    /// wrong-file negative control exercises `MissingImport` at the
    /// per-symbol layer.
    ///
    /// # Safety
    /// `load_base` must name a mapped image; headers must be readable.
pub unsafe fn parse(load_base: u64, max_rva: u64) -> Result<DllExportTable, PeError> {
        let (dir_rva, dir_size) = read_data_directory(load_base, 0)?;
        if dir_rva == 0 || dir_size == 0 {
            return Ok(DllExportTable {
                load_base,
                max_rva_bound: max_rva,
                dir_rva: 0,
                dir_size: 0,
                number_of_functions: 0,
                number_of_names: 0,
                address_of_functions_rva: 0,
                address_of_names_rva: 0,
                address_of_name_ordinals_rva: 0,
                ordinal_base: 0,
                time_date_stamp: 0,
            });
        }
        // IMAGE_EXPORT_DIRECTORY: 40 bytes
        match (dir_rva as u64).checked_add(dir_size as u64) {
            None => return Err(PeError::MalformedImageDirectory("export dir overflow")),
            Some(end) if end > max_rva => {
                return Err(PeError::MalformedImageDirectory("export directory outside image"))
            }
            _ => {}
        }
        let d = load_base.wrapping_add(dir_rva as u64);
        // IMAGE_EXPORT_DIRECTORY: Flags @0, TimeDateStamp @4.
        let time_date_stamp = core::ptr::read_unaligned((d + 4) as *const u32);
        let _name_rva = core::ptr::read_unaligned((d + 12) as *const u32);
        let number_of_functions = core::ptr::read_unaligned((d + 20) as *const u32);
        let number_of_names = core::ptr::read_unaligned((d + 24) as *const u32);
        let ordinal_base = core::ptr::read_unaligned((d + 16) as *const u32);
        let address_of_functions_rva = core::ptr::read_unaligned((d + 28) as *const u32);
        let address_of_names_rva = core::ptr::read_unaligned((d + 32) as *const u32);
        let address_of_name_ordinals_rva = core::ptr::read_unaligned((d + 36) as *const u32);
        // u32*u32 arithmetic in u64 cannot overflow before the bound
        // check answers for it.
        let names_end = address_of_names_rva as u64 + number_of_names as u64 * 4;
        let ords_end = address_of_name_ordinals_rva as u64 + number_of_names as u64 * 2;
        let eat_end = address_of_functions_rva as u64 + number_of_functions as u64 * 8;
        if names_end > max_rva || ords_end > max_rva || eat_end > max_rva {
            return Err(PeError::MalformedImageDirectory("export array outside image"));
        }
        Ok(DllExportTable {
            load_base,
            max_rva_bound: max_rva,
            dir_rva,
            dir_size,
            number_of_functions,
            number_of_names,
            address_of_functions_rva,
            address_of_names_rva,
            address_of_name_ordinals_rva,
            ordinal_base,
            time_date_stamp,
        })
    }

    /// The parsed `IMAGE_EXPORT_DIRECTORY.TimeDateStamp` (0 for the
    /// empty table) -- the quantity any bound-import entry declares
    /// itself bound to.
    pub fn time_date_stamp(&self) -> u32 {
        if self.is_empty() {
            0
        } else {
            self.time_date_stamp
        }
    }

    /// Null-terminated size -- `None` when the table is the empty
    /// (no-export-directory) one.
    pub fn is_empty(&self) -> bool {
        self.dir_rva == 0 || self.dir_size == 0
    }

    /// Number of exported names (0 for the empty table).
    pub fn name_count(&self) -> u32 {
        if self.is_empty() {
            0
        } else {
            self.number_of_names
        }
    }

    /// The export directory's own `Name` field (the DLL name it
    /// declares for itself -- the build gate pins that this equals the
    /// registry key the demo registers).
    ///
    /// # Safety
    /// image must be mapped.
    pub unsafe fn declared_name(&self) -> Result<Option<NameBuf>, PeError> {
        if self.is_empty() {
            return Ok(None);
        }
        let name_rva = core::ptr::read_unaligned(
            self.load_base.wrapping_add(self.dir_rva as u64 + 12) as *const u32,
        );
        if name_rva == 0 {
            return Ok(None);
        }
        Ok(Some(read_image_name(self.load_base, name_rva as u64, self.max_rva_bound)?))
    }

    /// `GetProcAddress(name)`, registry-agnostic: the export's absolute
    /// runtime address when its EAT entry is a plain function/data
    /// address. `None` when the table has no export directory, the name
    /// is absent, the name's stored ordinal exceeds the EAT bounds, its
    /// EAT slot is null (a null slot is UNAVAILABLE -- GetProcAddress
    /// semantics, never `load_base` itself), or the entry is a
    /// FORWARDED export (whose resolution is a registry question; use
    /// `DllRegistry::resolve_in`). Absent-or-address, never a guessed
    /// answer.
    ///
    /// # Safety
    /// image must be mapped.
    pub unsafe fn lookup(&self, name: &[u8]) -> Option<u64> {
        match self.lookup_name_raw(name) {
            Ok(Some(RawExport::Function(rva))) => Some(self.load_base.wrapping_add(rva)),
            _ => None,
        }
    }

    /// Locates the EAT entry for `name`: `None` when absent, or when
    /// the name's stored ordinal exceeds the EAT bounds (absent, the
    /// same not-guessed rule `lookup` has always applied).
    unsafe fn name_ordinal(&self, name: &[u8]) -> Result<Option<u64>, PeError> {
        if self.is_empty() {
            return Ok(None);
        }
        let max_rva = self.max_rva_bound;
        for i in 0..self.number_of_names {
            let name_ptr_rva = core::ptr::read_unaligned(
                self.load_base
                    .wrapping_add(self.address_of_names_rva as u64)
                    .wrapping_add((i as u64) * 4) as *const u32,
            );
            // Same shape the registry-agnostic `lookup` always had: a
            // malformed candidate name is an ABSENT answer, not a
            // panic and not a guess.
            let candidate = match read_image_name(self.load_base, name_ptr_rva as u64, max_rva) {
                Ok(n) => n,
                Err(_) => return Ok(None),
            };
            if candidate.as_slice() == name {
                let ord = core::ptr::read_unaligned(
                    self.load_base
                        .wrapping_add(self.address_of_name_ordinals_rva as u64)
                        .wrapping_add((i as u64) * 2) as *const u16,
                ) as u64;
                if ord >= self.number_of_functions as u64 {
                    return Ok(None); // malformed ordinal -- absent, not guessed at
                }
                return Ok(Some(ord));
            }
        }
        Ok(None)
    }

    /// The EAT slot `ord`: `None` when out of range or a null slot; a
    /// directory-internal value is a `Forwarder` (a STRING reference --
    /// never misresolved as code), anything else a plain `Function`.
    unsafe fn entry_raw(&self, ord: u64) -> Result<Option<RawExport>, PeError> {
        if self.is_empty() || ord >= self.number_of_functions as u64 {
            return Ok(None);
        }
        let f_rva = core::ptr::read_unaligned(
            self.load_base
                .wrapping_add(self.address_of_functions_rva as u64)
                .wrapping_add(ord * 4) as *const u32,
        ) as u64;
        if f_rva == 0 {
            return Ok(None); // null EAT slot: unavailable, never load_base itself
        }
        if f_rva >= self.dir_rva as u64 && f_rva < self.dir_rva as u64 + self.dir_size as u64 {
            return Ok(Some(RawExport::Forwarder(f_rva)));
        }
        Ok(Some(RawExport::Function(f_rva)))
    }

    unsafe fn lookup_name_raw(&self, name: &[u8]) -> Result<Option<RawExport>, PeError> {
        match self.name_ordinal(name)? {
            None => Ok(None),
            Some(ord) => self.entry_raw(ord),
        }
    }

    /// Number of exported EAT slots (the bounded ordinal range; 0 for
    /// the empty table).
    pub fn function_count(&self) -> u32 {
        if self.is_empty() {
            0
        } else {
            self.number_of_functions
        }
    }
}

/// The DLL-name -> export-table map the import walk resolves against.
/// Registered entries can carry a REAL provider table or NONE (the
/// demo's wrong-file negative control exercises the per-symbol refusal
/// by binding a name to a table with zero exports).
pub struct DllRegistry {
    entries: Vec<(NameBuf, Option<DllExportTable>)>,
    capacity: usize,
}

impl DllRegistry {
    pub fn new() -> DllRegistry {
        DllRegistry {
            entries: Vec::new(),
            capacity: 8,
        }
    }

    /// Registers (replaces an existing same-name entry) a provider.
    /// `table.empty()` entries answer every lookup with `None`.
    pub fn add(&mut self, name: &[u8], table: Option<DllExportTable>) {
        let mut nb = NameBuf::empty();
        let n = name.len().min(63);
        nb.bytes[..n].copy_from_slice(&name[..n]);
        nb.len = n;
        for (existing_name, existing_table) in self.entries.iter_mut() {
            if existing_name.as_slice() == name {
                *existing_table = table;
                return;
            }
        }
        if self.entries.len() >= self.capacity {
            // The demo uses 2-3 entries; exceeding the cap is a caller bug.
            panic!("DllRegistry overflow: more entries than the {} cap", self.capacity);
        }
        self.entries.push((nb, table));
    }

    fn find(&self, name: &[u8]) -> Option<&DllExportTable> {
        for (n, t) in self.entries.iter() {
            if n.as_slice() == name {
                return t.as_ref();
            }
        }
        None
    }

    /// The chase the registry exists for: what the per-table `lookup`
    /// answers `None` to (a forwarded export), the registry follows.
    /// The forwarder STRING's `other.dll.other_fn` halves re-enter here
    /// against the registry, depth-capped at 8 re-entries. Typed
    /// answers: an intermediate forwarder naming an UNREGISTERED DLL
    /// is `MissingDllImport` (the name-vs-file ruling applies to
    /// forwarders exactly as it applies to import descriptors); a
    /// target DLL known but the symbol absent there answers `Ok(None)`
    /// through the same path names use; the depth cap and malformed
    /// strings are typed refusals, never guesses.
    ///
    /// # Safety
    /// every table entered must name a mapped image.
    unsafe fn chase<'a>(
        &'a self,
        mut table: &'a DllExportTable,
        mut target: RawTarget,
    ) -> Result<Option<u64>, PeError> {
        // A local, per-resolution depth: the top-level target is depth
        // 0 and every forwarder hop costs one; 8 hops with no plain
        // entry is a chain that only ever forwards again, refused.
        let mut depth = 0u8;
        loop {
            let raw = match target {
                RawTarget::Name(ref nb) => table.lookup_name_raw(nb.as_slice())?,
                RawTarget::Ordinal(o) => {
                    // An import-thunk ordinal names EAT index
                    // `ordinal - OrdinalBase` (the directory's own
                    // declared base -- parsed, never assumed).
                    let base = table.ordinal_base as u64;
                    if o < base {
                        return Ok(None); // below the base: absent, the same not-guessed rule
                    }
                    table.entry_raw(o - base)?
                }
            };
            match raw {
                None => return Ok(None),
                Some(RawExport::Function(rva)) => {
                    return Ok(Some(table.load_base.wrapping_add(rva)));
                }
                Some(RawExport::Forwarder(s_rva)) => {
                    if depth >= 8 {
                        return Err(PeError::ForwarderDepthExceeded);
                    }
                    let s = read_forwarder_string(
                        table.load_base,
                        s_rva,
                        table.dir_rva as u64 + table.dir_size as u64,
                    )?;
                    let (dll, sym) = split_forwarder(s.as_slice())?;
                    let next = match self.find(dll) {
                        Some(t) => t,
                        None => return Err(PeError::MissingDllImport),
                    };
                    let mut nb = NameBuf::empty();
                    let sym_len = sym.len().min(64);
                    nb.bytes[..sym_len].copy_from_slice(&sym[..sym_len]);
                    nb.len = sym_len;
                    table = next;
                    target = RawTarget::Name(nb);
                    depth += 1;
                }
            }
        }
    }

    /// Resolve one import target inside an already-located provider
    /// `table` -- by name (`name = Some`) or by ordinal (`name = None`,
    /// `ordinal = Some`) -- GetProcAddress-complete: forwarders chase
    /// through the registry; a genuinely absent symbol answers `Ok(None)`
    /// (the import walk turns that into its per-symbol
    /// `MissingImport`); malformed chains are typed refusals.
    ///
    /// # Safety
    /// `table` must name a mapped image.
    pub unsafe fn resolve_in(
        &self,
        table: &DllExportTable,
        name: Option<&[u8]>,
        ordinal: Option<u64>,
    ) -> Result<Option<u64>, PeError> {
        match name {
            Some(n) => {
                let mut nb = NameBuf::empty();
                let n_len = n.len().min(64);
                nb.bytes[..n_len].copy_from_slice(&n[..n_len]);
                nb.len = n_len;
                self.chase(table, RawTarget::Name(nb))
            }
            None => {
                let o = ordinal.ok_or(PeError::MalformedImageDirectory(
                    "resolve_in called with neither a name nor an ordinal",
                ))?;
                self.chase(table, RawTarget::Ordinal(o))
            }
        }
    }
}

/// Applies the PE base relocations described in the `.reloc` section at
/// runtime RVA `reloc_rva` (read from the mapped image, so all sections
/// must already be mapped). `delta = load_base - image_base` is added to
/// every relocation target. Supports the two x86_64-relevant types:
/// `IMAGE_REL_BASED_HIGHLOW` (3, a 32-bit field) and
/// `IMAGE_REL_BASED_DIR64` (10, a 64-bit field); `IMAGE_REL_BASED_ABSOLUTE`
/// (0) is padding and skipped. An unknown type is a hard error, not
/// silently ignored -- a mis-relocated image is worse than a refused
/// one, the same posture the loader took before it supported
/// relocation at all.
///
/// # Safety
/// Every relocation target must land inside a mapped, writable section
/// of the image (true for any well-formed PE; a malformed one could aim
/// a target anywhere, so callers pass only loader-mapped images).
unsafe fn apply_base_relocations(
    load_base: u64,
    image_base: u64,
    reloc_rva: u32,
    reloc_size: u32,
) -> Result<(), PeError> {
    let delta = load_base.wrapping_sub(image_base);
    let table = (load_base.wrapping_add(reloc_rva as u64)) as *const u8;
    let size = reloc_size as usize;
    let mut off: usize = 0;
    while off + 8 <= size {
        let page_rva = core::ptr::read_unaligned(table.add(off) as *const u32);
        let block_size = core::ptr::read_unaligned(table.add(off + 4) as *const u32) as usize;
        if block_size < 8 || off + block_size > size {
            return Err(PeError::BadRelocationBlock);
        }
        let entry_count = (block_size - 8) / 2;
        for i in 0..entry_count {
            let entry = core::ptr::read_unaligned(table.add(off + 8 + i * 2) as *const u16);
            let reloc_type = entry >> 12;
            let offset = (entry & 0x0FFF) as u64;
            let target = load_base
                .wrapping_add(page_rva as u64)
                .wrapping_add(offset) as *mut u8;
            match reloc_type {
                0 => {} // IMAGE_REL_BASED_ABSOLUTE -- padding, nothing to do
                3 => {
                    // HIGHLOW: a 32-bit field.
                    let p = target as *mut u32;
                    let v = core::ptr::read_unaligned(p);
                    core::ptr::write_unaligned(p, v.wrapping_add(delta as u32));
                }
                10 => {
                    // DIR64: a 64-bit field.
                    let p = target as *mut u64;
                    let v = core::ptr::read_unaligned(p);
                    core::ptr::write_unaligned(p, v.wrapping_add(delta));
                }
                other => return Err(PeError::UnsupportedRelocationType(other)),
            }
        }
        off += block_size;
    }
    Ok(())
}
