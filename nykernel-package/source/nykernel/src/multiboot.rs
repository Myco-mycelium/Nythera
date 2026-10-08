//! Multiboot2 info-structure parsing.
//!
//! GRUB (or any Multiboot2-compliant loader) passes a pointer to this
//! structure in `%rsi` on entry to `kernel_main` (boot.s forwards it
//! from the register the spec puts it in at 32-bit handoff). It's a
//! `total_size`-prefixed, 8-byte-aligned sequence of tags, terminated
//! by a type-0 tag. This module only implements the one tag Phase 2
//! actually needs -- the memory map (type 6) -- rather than a general
//! Multiboot2 tag library; other tags (module lists, framebuffer info,
//! ELF section headers, ...) are documented as unparsed, not silently
//! ignored.
//!
//! Unlike everything since `long_mode_start`, this module reads raw
//! physical memory through pointers the loader gave us. It stays
//! deliberately paranoid about the untrusted `total_size` and per-tag
//! `size` fields -- a malformed or adversarial Multiboot2 structure
//! must not walk this kernel off the end of mapped memory.

use core::mem;

const TAG_TYPE_END: u32 = 0;
const TAG_TYPE_MEMORY_MAP: u32 = 6;

/// Multiboot2 memory region types (spec §3.6.8). Only `Available` is
/// usable by the frame allocator; everything else (ACPI
/// reclaimable/NVS, defective RAM, reserved) is left alone -- some of
/// those categories *could* be reclaimed later (ACPI tables after
/// they're parsed) but Phase 2 doesn't parse ACPI yet, so they stay
/// off-limits for now rather than being incorrectly guessed at.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MemoryRegionType {
    Available,
    Reserved,
    AcpiReclaimable,
    AcpiNvs,
    BadRam,
    Unknown(u32),
}

impl From<u32> for MemoryRegionType {
    fn from(v: u32) -> Self {
        match v {
            1 => MemoryRegionType::Available,
            2 => MemoryRegionType::Reserved,
            3 => MemoryRegionType::AcpiReclaimable,
            4 => MemoryRegionType::AcpiNvs,
            5 => MemoryRegionType::BadRam,
            other => MemoryRegionType::Unknown(other),
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct MemoryRegion {
    pub base_addr: u64,
    pub length: u64,
    pub region_type: MemoryRegionType,
}

#[repr(C)]
struct TagHeader {
    typ: u32,
    size: u32,
}

#[repr(C)]
struct MemoryMapTag {
    header: TagHeader,
    entry_size: u32,
    entry_version: u32,
    // followed by `(header.size - 16) / entry_size` entries
}

#[repr(C)]
struct MemoryMapEntry {
    base_addr: u64,
    length: u64,
    typ: u32,
    reserved: u32,
}

/// A conservative upper bound on total_size we're willing to trust
/// without corroborating evidence -- guards against a wildly bogus
/// value causing us to scan gigabytes of unrelated memory looking for
/// tags that were never there. 16MiB is generous for a Multiboot2 info
/// structure (real ones are typically a few KiB).
const MAX_TRUSTED_TOTAL_SIZE: u32 = 16 * 1024 * 1024;

#[derive(Clone, Copy)]
pub struct MultibootInfo {
    addr: usize,
    total_size: u32,
}

impl MultibootInfo {
    /// # Safety
    /// `addr` must be the physical (== virtual, under Phase 1/2's
    /// identity mapping) address of a real Multiboot2 info structure,
    /// as passed by the loader. Called exactly once, from
    /// `kernel_main`, with the value boot.s forwarded from the loader.
    pub unsafe fn load(addr: u64) -> Option<Self> {
        if addr == 0 {
            return None;
        }
        let total_size = *(addr as *const u32);
        if total_size < 8 || total_size > MAX_TRUSTED_TOTAL_SIZE {
            return None;
        }
        Some(MultibootInfo {
            addr: addr as usize,
            total_size,
        })
    }

    /// Walks the tag list looking for the memory map tag, calling
    /// `f` once per entry. Bounds every read against `total_size`
    /// before dereferencing.
    pub fn for_each_memory_region<F: FnMut(MemoryRegion)>(&self, mut f: F) {
        let end = self.addr + self.total_size as usize;
        // tags start after the 8-byte (total_size, reserved) header
        let mut cursor = self.addr + 8;

        while cursor + mem::size_of::<TagHeader>() <= end {
            let header = unsafe { &*(cursor as *const TagHeader) };
            if header.typ == TAG_TYPE_END {
                break;
            }
            if header.size < mem::size_of::<TagHeader>() as u32 {
                break; // malformed: a tag can't be smaller than its own header
            }
            let tag_end = cursor + header.size as usize;
            if tag_end > end {
                break; // malformed: tag claims to extend past total_size
            }

            if header.typ == TAG_TYPE_MEMORY_MAP {
                self.emit_memory_map_entries(cursor, tag_end, &mut f);
            }

            // tags are 8-byte aligned; advance and round up
            cursor = (tag_end + 7) & !7;
        }
    }

    fn emit_memory_map_entries<F: FnMut(MemoryRegion)>(&self, tag_start: usize, tag_end: usize, f: &mut F) {
        if tag_end - tag_start < mem::size_of::<MemoryMapTag>() {
            return;
        }
        let map_tag = unsafe { &*(tag_start as *const MemoryMapTag) };
        let entry_size = map_tag.entry_size as usize;
        if entry_size < mem::size_of::<MemoryMapEntry>() {
            return; // malformed: entries smaller than the struct we'd read
        }

        let mut entry_addr = tag_start + mem::size_of::<MemoryMapTag>();
        while entry_addr + mem::size_of::<MemoryMapEntry>() <= tag_end {
            let entry = unsafe { &*(entry_addr as *const MemoryMapEntry) };
            f(MemoryRegion {
                base_addr: entry.base_addr,
                length: entry.length,
                region_type: MemoryRegionType::from(entry.typ),
            });
            entry_addr += entry_size;
        }
    }
}
