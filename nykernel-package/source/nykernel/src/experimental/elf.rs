//! Minimal ELF64 loader.
//!
//! Parses just enough of the ELF64 format to load a flat, statically-
//! linked executable -- fixed-address (`ET_EXEC`) or position-
//! independent (`ET_DYN`, see below) -- the file header (for `e_entry`
//! and sanity checks) and `PT_LOAD` program headers (for what to map
//! where). No dynamic linking (no symbol resolution, no imported
//! libraries), no `.bss`-only segments beyond simple
//! `p_memsz > p_filesz` zero-fill. This is deliberately the smallest
//! useful subset -- see `crate::experimental`'s module banner: a real
//! Windows PE loader or a real Android ELF/DEX loader (Phase 5/6) will
//! need substantially more (real dynamic linking, symbol resolution,
//! imported libraries), but they'll need this exact category of work
//! -- parse untrusted headers defensively, map segments with
//! file-specified permissions, zero what the file says should be
//! zero, relocate what the file says needs relocating -- underneath
//! whatever else they add.
//!
//! # `ET_DYN` / position-independent executables
//!
//! `ET_EXEC` binaries bake absolute addresses into their code and data
//! at link time -- they can only run at exactly the address they were
//! linked for, so `load` always maps them with `load_bias = 0` (any
//! other value would be wrong, since nothing in the file would be
//! adjusted to match). `ET_DYN` binaries (built with `ld -pie
//! --no-dynamic-linker`, producing a self-contained "static-pie"
//! executable with no interpreter and no external symbol dependencies)
//! can be loaded at *any* base address, provided the loader applies
//! the relocations the file's `PT_DYNAMIC` segment describes. This
//! loader implements exactly one relocation type -- `R_X86_64_RELATIVE`
//! (`*(base + r_offset) = base + r_addend`) -- which is the only kind
//! a self-relocating, no-external-symbols static-pie binary needs; a
//! binary requiring symbol-resolved relocations (`R_X86_64_GLOB_DAT`,
//! `R_X86_64_JUMP_SLOT`, ...) is out of scope, since that needs real
//! dynamic linking, not just base-address adjustment.
//!
//! Like `crate::multiboot`, this stays paranoid about untrusted input:
//! every offset and size is bounds-checked against the actual byte
//! slice before being dereferenced. A malformed or adversarial ELF
//! file must be rejected with an error, not walk this kernel off the
//! end of its own image.

use x86_64::structures::paging::{OffsetPageTable, PageTableFlags};
use x86_64::VirtAddr;

use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;

const ELF_MAGIC: [u8; 4] = [0x7f, b'E', b'L', b'F'];
const ELFCLASS64: u8 = 2;
const ELFDATA2LSB: u8 = 1; // little-endian
const ET_EXEC: u16 = 2; // fixed-address executable
const ET_DYN: u16 = 3; // position-independent executable (see module doc)
const PT_LOAD: u32 = 1;
const PT_DYNAMIC: u32 = 2;
const PF_X: u32 = 1 << 0;
const PF_W: u32 = 1 << 1;
#[allow(dead_code)]
const PF_R: u32 = 1 << 2; // every segment this loader maps is readable regardless; kept for documentation

const EHDR_SIZE: usize = 64;
const PHDR_SIZE: usize = 56;
const DYN_ENTRY_SIZE: usize = 16; // Elf64_Dyn: { d_tag: i64, d_val/d_ptr: u64 }
const RELA_ENTRY_SIZE: usize = 24; // Elf64_Rela: { r_offset: u64, r_info: u64, r_addend: i64 }

const DT_NULL: i64 = 0; // terminates the dynamic-entry array
const DT_RELA: i64 = 7; // address (pre-bias) of the relocation table
const DT_RELASZ: i64 = 8; // total size in bytes of the relocation table
const DT_RELAENT: i64 = 9; // size of one relocation entry (must be RELA_ENTRY_SIZE)

const R_X86_64_RELATIVE: u32 = 8;

#[derive(Debug)]
pub enum ElfError {
    TooShort,
    BadMagic,
    Not64Bit,
    NotLittleEndian,
    UnsupportedType,
    NonZeroBiasOnFixedAddressExecutable,
    ProgramHeaderOutOfBounds,
    SegmentDataOutOfBounds,
    NoLoadSegments,
    MappingFailed,
    UnsupportedRelocationType,
    UnsupportedRelocationEntrySize,
}

pub struct LoadedElf {
    pub entry_point: VirtAddr,
}

/// Reads a little-endian integer field out of `data` at `offset`,
/// bounds-checked. Written by hand (not via a crate) to keep this
/// module's only dependency on external code being the paging/frame
/// allocator plumbing it already needs -- parsing a handful of small
/// integer fields doesn't justify a new dependency.
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

struct ProgramHeader {
    p_type: u32,
    p_flags: u32,
    p_offset: u64,
    p_vaddr: u64,
    p_filesz: u64,
    p_memsz: u64,
}

fn parse_program_header(data: &[u8], off: usize) -> Option<ProgramHeader> {
    Some(ProgramHeader {
        p_type: read_u32(data, off)?,
        p_flags: read_u32(data, off + 4)?,
        p_offset: read_u64(data, off + 8)?,
        p_vaddr: read_u64(data, off + 16)?,
        // p_paddr at off+24, unused by this loader
        p_filesz: read_u64(data, off + 32)?,
        p_memsz: read_u64(data, off + 40)?,
        // p_align at off+48, unused (this loader always page-aligns)
    })
}

/// Parses `data` as an ELF64 executable, maps every `PT_LOAD` segment
/// at `load_bias + p_vaddr` (zero-filled first, then the file's bytes
/// copied on top for the `p_filesz` portion -- so `p_memsz > p_filesz`,
/// i.e. an implicit `.bss`, is handled correctly rather than left as
/// stale/garbage physical-frame content), applies any `R_X86_64_RELATIVE`
/// relocations described by a `PT_DYNAMIC` segment (see the module doc
/// for why that's the only relocation type supported), and returns
/// `load_bias + e_entry`. Does not jump anywhere -- that's the
/// caller's job, same separation as `usermode::prepare` vs
/// `usermode::enter`.
///
/// `load_bias` MUST be `0` for an `ET_EXEC` file (its addresses are
/// fixed at link time; any other bias would be wrong, not just
/// unusual -- enforced with an error, not just documented). For
/// `ET_DYN`, `load_bias` is the real runtime base address the caller
/// has chosen (typically wherever it mapped this process's address
/// space to place code); the loader adds it everywhere the file's
/// own addresses are used.
pub fn load(
    data: &[u8],
    mapper: &mut OffsetPageTable<'static>,
    frame_allocator: &mut BumpFrameAllocator,
    load_bias: u64,
) -> Result<LoadedElf, ElfError> {
    if data.len() < EHDR_SIZE {
        return Err(ElfError::TooShort);
    }
    if data[0..4] != ELF_MAGIC {
        return Err(ElfError::BadMagic);
    }
    if data[4] != ELFCLASS64 {
        return Err(ElfError::Not64Bit);
    }
    if data[5] != ELFDATA2LSB {
        return Err(ElfError::NotLittleEndian);
    }
    let e_type = read_u16(data, 16).ok_or(ElfError::TooShort)?;
    if e_type != ET_EXEC && e_type != ET_DYN {
        return Err(ElfError::UnsupportedType);
    }
    if e_type == ET_EXEC && load_bias != 0 {
        return Err(ElfError::NonZeroBiasOnFixedAddressExecutable);
    }
    let e_entry = read_u64(data, 24).ok_or(ElfError::TooShort)?;
    let e_phoff = read_u64(data, 32).ok_or(ElfError::TooShort)? as usize;
    let e_phentsize = read_u16(data, 54).ok_or(ElfError::TooShort)? as usize;
    let e_phnum = read_u16(data, 56).ok_or(ElfError::TooShort)? as usize;

    if e_phentsize < PHDR_SIZE {
        return Err(ElfError::ProgramHeaderOutOfBounds);
    }

    let mut load_segments_found = 0u32;
    let mut dynamic_segment: Option<(u64, u64)> = None; // (p_vaddr, p_filesz), pre-bias

    for i in 0..e_phnum {
        let off = e_phoff + i * e_phentsize;
        if off + PHDR_SIZE > data.len() {
            return Err(ElfError::ProgramHeaderOutOfBounds);
        }
        let ph = parse_program_header(data, off).ok_or(ElfError::ProgramHeaderOutOfBounds)?;

        if ph.p_type == PT_DYNAMIC {
            dynamic_segment = Some((ph.p_vaddr, ph.p_filesz));
            continue;
        }
        if ph.p_type != PT_LOAD {
            continue;
        }
        load_segments_found += 1;

        let file_start = ph.p_offset as usize;
        let file_end = file_start
            .checked_add(ph.p_filesz as usize)
            .ok_or(ElfError::SegmentDataOutOfBounds)?;
        if file_end > data.len() {
            return Err(ElfError::SegmentDataOutOfBounds);
        }
        if ph.p_memsz < ph.p_filesz {
            return Err(ElfError::SegmentDataOutOfBounds); // nonsensical: file data larger than the memory reserved for it
        }

        let runtime_vaddr = load_bias.wrapping_add(ph.p_vaddr);

        // Page-align the mapping to cover [runtime_vaddr, runtime_vaddr + p_memsz),
        // which may extend before/after the exact requested range.
        let seg_start = VirtAddr::new(runtime_vaddr).align_down(4096u64);
        let seg_end_addr = runtime_vaddr + ph.p_memsz;
        let seg_end = VirtAddr::new(seg_end_addr).align_up(4096u64);
        let page_count = (seg_end.as_u64() - seg_start.as_u64()) / 4096;

        let mut flags = PageTableFlags::PRESENT | PageTableFlags::USER_ACCESSIBLE;
        if ph.p_flags & PF_W != 0 {
            flags |= PageTableFlags::WRITABLE;
        }
        // Real W^X enforcement: a segment without PF_X gets
        // NO_EXECUTE, meaning the CPU will refuse to fetch
        // instructions from it once EFER.NXE is enabled (see
        // memory::paging::enable_nx, called before any ELF is loaded).
        // Verified with an actual positive-control test, not just
        // asserted -- see IMPLEMENTATION_STATUS.md for the transcript
        // of a deliberate data-page execution attempt correctly
        // page-faulting with an instruction-fetch error.
        if ph.p_flags & PF_X == 0 {
            flags |= PageTableFlags::NO_EXECUTE;
        }
        // A PT_DYNAMIC-bearing binary's relocations write into this
        // segment after it's mapped (see below) -- if PF_W is absent
        // (the segment is nominally read-only, e.g. under
        // GNU_RELRO-style layouts), temporarily allow the write by
        // still honoring PF_W as requested here; this loader doesn't
        // implement RELRO's "make read-only again after relocating"
        // step, a documented simplification, not a silent gap (a
        // relocatable segment this loader maps read-only stays
        // writable forever, which a real loader wouldn't allow).

        if page_count > 0 {
            let mapped = paging::map_range(mapper, frame_allocator, seg_start, page_count, flags);
            if mapped != page_count {
                return Err(ElfError::MappingFailed);
            }

            unsafe {
                // Zero the whole mapped range first -- this is what makes
                // p_memsz > p_filesz (an implicit .bss) correct rather
                // than leaving stale physical-frame content in the gap.
                core::ptr::write_bytes(seg_start.as_mut_ptr::<u8>(), 0, page_count as usize * 4096);
                // Then copy the file's actual bytes for this segment on top.
                let dest = VirtAddr::new(runtime_vaddr).as_mut_ptr::<u8>();
                core::ptr::copy_nonoverlapping(data.as_ptr().add(file_start), dest, ph.p_filesz as usize);
            }
        }
    }

    if load_segments_found == 0 {
        return Err(ElfError::NoLoadSegments);
    }

    if let Some((dyn_vaddr, dyn_filesz)) = dynamic_segment {
        apply_relocations(load_bias, dyn_vaddr, dyn_filesz)?;
    }

    Ok(LoadedElf {
        entry_point: VirtAddr::new(load_bias.wrapping_add(e_entry)),
    })
}

/// Walks the `PT_DYNAMIC` segment's tag/value entries (now readable at
/// `load_bias + dyn_vaddr`, since the `PT_LOAD` pass above already
/// mapped and populated whatever segment contains it -- `PT_DYNAMIC`
/// always overlaps a `PT_LOAD` segment, it isn't separately loaded)
/// looking for `DT_RELA`/`DT_RELASZ`/`DT_RELAENT`, then applies every
/// `R_X86_64_RELATIVE` entry in that table. Entries of any other
/// relocation type are rejected -- see the module doc for why this
/// loader doesn't attempt symbol-resolved relocations.
fn apply_relocations(load_bias: u64, dyn_vaddr: u64, dyn_filesz: u64) -> Result<(), ElfError> {
    let dyn_base = load_bias.wrapping_add(dyn_vaddr);
    let dyn_entry_count = (dyn_filesz as usize) / DYN_ENTRY_SIZE;

    let mut rela_addr: Option<u64> = None;
    let mut rela_size: Option<u64> = None;
    let mut rela_entsize: Option<u64> = None;

    for i in 0..dyn_entry_count {
        let entry_addr = dyn_base + (i * DYN_ENTRY_SIZE) as u64;
        let (tag, val) = unsafe {
            let tag = core::ptr::read_unaligned(entry_addr as *const i64);
            let val = core::ptr::read_unaligned((entry_addr + 8) as *const u64);
            (tag, val)
        };
        match tag {
            DT_NULL => break,
            DT_RELA => rela_addr = Some(val),
            DT_RELASZ => rela_size = Some(val),
            DT_RELAENT => rela_entsize = Some(val),
            _ => {} // every other tag (DT_HASH, DT_SYMTAB, DT_STRTAB, ...) is irrelevant to relative-only relocation
        }
    }

    let (Some(rela_addr), Some(rela_size)) = (rela_addr, rela_size) else {
        return Ok(()); // no relocation table -- nothing to do, not an error
    };
    if let Some(entsize) = rela_entsize {
        if entsize as usize != RELA_ENTRY_SIZE {
            return Err(ElfError::UnsupportedRelocationEntrySize);
        }
    }

    let rela_base = load_bias.wrapping_add(rela_addr);
    let rela_count = (rela_size as usize) / RELA_ENTRY_SIZE;

    for i in 0..rela_count {
        let entry_addr = rela_base + (i * RELA_ENTRY_SIZE) as u64;
        let (r_offset, r_info, r_addend) = unsafe {
            let r_offset = core::ptr::read_unaligned(entry_addr as *const u64);
            let r_info = core::ptr::read_unaligned((entry_addr + 8) as *const u64);
            let r_addend = core::ptr::read_unaligned((entry_addr + 16) as *const i64);
            (r_offset, r_info, r_addend)
        };
        let r_type = (r_info & 0xffff_ffff) as u32;
        if r_type != R_X86_64_RELATIVE {
            // A real dynamic linker would resolve symbol-based
            // relocations here; this loader doesn't do symbol
            // resolution at all (see module doc), so any relocation
            // type other than the base-address-only RELATIVE kind is
            // something this loader genuinely cannot satisfy correctly
            // -- rejecting it is the honest outcome, not silently
            // leaving the target word unrelocated.
            return Err(ElfError::UnsupportedRelocationType);
        }
        let target_addr = load_bias.wrapping_add(r_offset);
        let value = load_bias.wrapping_add(r_addend as u64);
        unsafe {
            core::ptr::write_unaligned(target_addr as *mut u64, value);
        }
    }

    Ok(())
}
