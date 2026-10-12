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

use x86_64::structures::paging::{Mapper, OffsetPageTable, Page, PageTableFlags, Size4KiB};
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

/// GNU ld's own program-header type marking the region GNU_RELRO
/// (`PT_GNU_RELRO`, 0x6474e552). See `LoadedRelro` for how this loader
/// honors it.
const PT_GNU_RELRO: u32 = 0x6474_e552;

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
    /// A RELRO page's per-page permission update (the temporary
    /// write-grant, or the revoke-to-read-only) could not be applied
    /// through the page tables. Never silent: leaving a RELRO page
    /// writable would silently discard the whole property the region
    /// exists for, so a failed update is a refused load, not a
    /// shrug.
    RelroPermissionUpdateFailed,
}

/// What the loader did with a `PT_GNU_RELRO` segment. Returned so a
/// caller (and the boot transcript) can see the region's final
/// protection state as a fact, not an assumption -- and so a caller
/// can structurally re-verify it against the live page tables, which
/// `experimental::pie_demo` does every boot.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RelroOutcome {
    /// The RELRO region existed, every relocation affecting it was a
    /// `R_X86_64_RELATIVE` (the only kind this loader can satisfy),
    /// all were applied, and the region's pages were then revoked to
    /// read-only. `start`/`end` are runtime (bias-adjusted) byte
    /// addresses -- `end` exclusive.
    MadeReadOnly { start: u64, end: u64 },
    /// The RELRO region contained at least one relocation this loader
    /// cannot satisfy (anything symbol-resolved; real dynamic linking
    /// is out of scope), so the region was NOT revoked -- leaving it
    /// writable is the honest state here and is *reported*, not
    /// hidden: a caller can refuse the binary outright or proceed
    /// knowingly. `start`/`end` as above.
    Denied { start: u64, end: u64 },
}

pub struct LoadedElf {
    pub entry_point: VirtAddr,
    /// `Some` iff the file carried a `PT_GNU_RELRO` segment; see
    /// [`RelroOutcome`] for the two possible states.
    pub relro: Option<RelroOutcome>,
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
    // e_entry is read again below, AFTER relocations; reading it here
    // first validates the header before any mapping work happens.
    read_u64(data, 24).ok_or(ElfError::TooShort)?;
    let e_phoff = read_u64(data, 32).ok_or(ElfError::TooShort)? as usize;
    let e_phentsize = read_u16(data, 54).ok_or(ElfError::TooShort)? as usize;
    let e_phnum = read_u16(data, 56).ok_or(ElfError::TooShort)? as usize;

    if e_phentsize < PHDR_SIZE {
        return Err(ElfError::ProgramHeaderOutOfBounds);
    }

    let mut load_segments_found = 0u32;
    let mut dynamic_segment: Option<(u64, u64)> = None; // (p_vaddr, p_filesz), pre-bias
    let mut relro_segment: Option<(u64, u64)> = None; // (p_vaddr, p_memsz), pre-bias

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
        if ph.p_type == PT_GNU_RELRO {
            relro_segment = Some((ph.p_vaddr, ph.p_memsz));
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
        // GNU_RELRO-style layouts), the RELRO pass below temporarily
        // grants the write itself, relocates, then revokes to
        // read-only; the mapping here stays as the file requested.
        // Similarly, an *executable* GNU_RELRO region (some linkers
        // emit PLT-containing relro as PF_R|PF_X) would otherwise make
        // the revoke pass silently widen permissions -- so the revoke
        // below only ever *removes* writability from flags the
        // segment already carries.

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

    // The load happens in a strict order, and only inside this scope
    // does the RELRO region hold caller-granted write permission:
    //  1. map + populate every PT_LOAD segment (above),
    //  2. grant W on the GNU_RELRO pages *only if* relocations exist
    //     whose targets land in the region,
    //  3. apply the relocations,
    //  4. revoke W, restoring exactly the file-requested flag set the
    //     mapping pass laid down (it never adds W to a read-only
    //     segment).
    // If step 3 fails (an unsupported relocation type / entry size),
    // the region is left writable and `LoadedElf::relro` reports
    // `RelroOutcome::Denied` -- the honest state, surfaced to the
    // caller rather than silently kept or silently unchanged.
    let relro_outcome: Option<RelroOutcome> = match relro_segment {
        Some((relro_vaddr, relro_memsz)) => {
            let relro_start_v = load_bias.wrapping_add(relro_vaddr);
            // GNU ld computes the memory size already page-aligned;
            // be exact for the denial-vs-revocation decision (which
            // relocation targets count as "inside" the region), then
            // extend to whole pages for the actual permission work.
            let relro_end_v = relro_start_v.wrapping_add(relro_memsz);
            let page_start = VirtAddr::new(relro_start_v).align_down(4096u64).as_u64();
            let page_end = VirtAddr::new(relro_end_v).align_up(4096u64).as_u64();
            let relro_pages = (page_end - page_start) / 4096;

            let relocations_planned = relro_outcome_planned(&dynamic_segment, load_bias, relro_start_v, relro_end_v);
            if relocations_planned.some {
                // Grant write for the relocation pass only. This is
                // the loader's own request, deliberately distinct
                // (WRITABLE only, the rest untouched) from the file's.
                if !update_page_flags_range(mapper, page_start, relro_pages, |mut f| {
                    f.insert(PageTableFlags::WRITABLE);
                    f
                }) {
                    return Err(ElfError::RelroPermissionUpdateFailed);
                }
            }

            let apply_result = match dynamic_segment {
                Some((dyn_vaddr, dyn_filesz)) => apply_relocations(load_bias, dyn_vaddr, dyn_filesz),
                None => Ok(()),
            };
            match apply_result {
                Ok(()) => {
                    // Revoke: strip WRITABLE, keep everything else
                    // (including NO_EXECUTE and USER_ACCESSIBLE)
                    // exactly as mapped. This runs UNCONDITIONALLY on
                    // the success path: a GNU_RELRO region often
                    // overlaps a writable PT_LOAD segment (here it sits
                    // inside the RW data segment), so "no in-region
                    // relocations" does NOT imply "already read-only"
                    // -- skipping the revoke for that case would leave
                    // the dynamic segment writable while the loader
                    // still reported it protected (exactly the silent
                    // lie this increment exists to eliminate). A revoke
                    // on an already-read-only page is a harmless
                    // no-op.
                    if !update_page_flags_range(mapper, page_start, relro_pages, |f| {
                        f & !PageTableFlags::WRITABLE
                    }) {
                        return Err(ElfError::RelroPermissionUpdateFailed);
                    }
                    Some(RelroOutcome::MadeReadOnly { start: relro_start_v, end: relro_end_v })
                }
                Err(e) => match e {
                    // The failures the RELRO decision logic can
                    // produce: something in the dynamic segment is
                    // deliberately unrepresentable here. Leave the
                    // region writable and deny (see RelroOutcome).
                    ElfError::UnsupportedRelocationType | ElfError::UnsupportedRelocationEntrySize => {
                        Some(RelroOutcome::Denied { start: relro_start_v, end: relro_end_v })
                    }
                    other => return Err(other),
                },
            }
        }
        None => None,
    };

    let e_entry_final = read_u64(data, 24).ok_or(ElfError::TooShort)?;

    Ok(LoadedElf {
        entry_point: VirtAddr::new(load_bias.wrapping_add(e_entry_final)),
        relro: relro_outcome,
    })
}

/// Cross-checks the relocation table (already mapped, via the
/// PT_LOAD pass) against the RELRO region's runtime address range and
/// classifies the region's supportability WITHOUT applying anything:
/// - entries outside the region don't affect the region's own REMAP
///   decision (the RELRO decision logic below uses this only for
///   whether a grant is *needed*, not whether the region is
///   "covered"; see the grant/revoke flow above),
/// - the first in-region entry of any non-RELATIVE type marks the
///   region unsupportable (`some: false` => denial path).
///
/// Soundness note: this is an inspection pass over data already in
/// memory, re-walking the exact same table `apply_relocations` walks,
/// with the same bounds/threshold arithmetic (see
/// `apply_relocations`' own doc for why each check is safe). It must
/// read the table BEFORE any relocation is applied, since the RELRO
/// grant decision is made from it.
fn relro_outcome_planned(
    dynamic_segment: &Option<(u64, u64)>,
    load_bias: u64,
    relro_start: u64,
    relro_end: u64,
) -> RelroPlanningOutcome {
    let Some(&(dyn_vaddr, dyn_filesz)) = dynamic_segment.as_ref() else {
        // No dynamic segment => no relocation table => nothing ever
        // contests the region's read-only reset.
        return RelroPlanningOutcome { some: false };
    };

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
            _ => {}
        }
    }

    let (Some(rela_addr), Some(rela_size)) = (rela_addr, rela_size) else {
        return RelroPlanningOutcome { some: false };
    };
    if let Some(entsize) = rela_entsize {
        if entsize as usize != RELA_ENTRY_SIZE {
            return RelroPlanningOutcome { some: false }; // apply_relocations will refuse it; region is Denied
        }
    }

    let rela_base = load_bias.wrapping_add(rela_addr);
    let rela_count = (rela_size as usize) / RELA_ENTRY_SIZE;

    // "Some" here means: at least one entry NEEDS the region
    // (writable during relocation and later revoked) OR marks the
    // region unsupportable. The caller doesn't distinguish those
    // cases here; the apply pass does.
    for i in 0..rela_count {
        let entry_addr = rela_base + (i * RELA_ENTRY_SIZE) as u64;
        let (r_offset, r_info, _r_addend) = unsafe {
            let r_offset = core::ptr::read_unaligned(entry_addr as *const u64);
            let r_info = core::ptr::read_unaligned((entry_addr + 8) as *const u64);
            let r_addend = core::ptr::read_unaligned((entry_addr + 16) as *const i64);
            (r_offset, r_info, r_addend)
        };
        let _ = _r_addend;
        let target_addr = load_bias.wrapping_add(r_offset);
        let r_type = (r_info & 0xffff_ffff) as u32;
        let target_in_region = target_addr >= relro_start && target_addr < relro_end;
        if target_in_region && r_type != R_X86_64_RELATIVE {
            // A symbol-resolved relocation aimed into this region:
            // this loader genuinely cannot satisfy it (no real
            // dynamic linking -- see the module doc), so this region
            // cannot be made honestly read-only.
            return RelroPlanningOutcome { some: false };
        }
        if target_in_region && r_type == R_X86_64_RELATIVE {
            return RelroPlanningOutcome { some: true };
        }
    }
    RelroPlanningOutcome { some: false }
}

struct RelroPlanningOutcome {
    some: bool,
}

/// Applies `f page_flag -> new page_flag` to every 4KiB page in
/// `[start, start + pages * 4096)`, by re-walking the page tables via
/// the same `OffsetPageTable` the mapping pass used. Any page missing
/// or unmapped is a hard failure (`false`), since the mapping pass
/// must have created all of them (making this a check that the load
/// itself is intact, not a formality).
fn update_page_flags_range(
    mapper: &mut OffsetPageTable<'static>,
    start: u64,
    pages: u64,
    update: impl Fn(PageTableFlags) -> PageTableFlags,
) -> bool {
    for i in 0..pages {
        let page: Page<Size4KiB> = Page::containing_address(VirtAddr::new(start + i * 4096));
        match unsafe { mapper.update_flags(page, update(PageTableFlags::PRESENT)) } {
            Ok(flush) => {
                flush.flush();
            }
            Err(_) => return false,
        }
    }
    true
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
