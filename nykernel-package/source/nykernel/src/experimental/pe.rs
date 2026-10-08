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
//! `Characteristics` flags). No imports (no `IMPORT_DIRECTORY`
//! resolution -- a binary calling into `kernel32.dll` or any other
//! DLL is out of scope, since that needs a real Win32 API
//! implementation, not just a loader), no resources, no exception
//! tables. Base relocations (`.reloc`) ARE supported: loading at a
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
}

pub struct LoadedPe {
    pub entry_point: VirtAddr,
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

    for i in 0..number_of_sections {
        let off = section_table_off + i * SECTION_HEADER_SIZE;
        let sh = parse_section_header(data, off).ok_or(PeError::SectionHeaderOutOfBounds)?;

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

    Ok(LoadedPe {
        entry_point: VirtAddr::new(load_base.wrapping_add(address_of_entry_point as u64)),
    })
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
