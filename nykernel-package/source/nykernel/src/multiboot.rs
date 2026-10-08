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
/// Multiboot2's ACPI RSDP tags: type 14 carries the ACPI 1.0 RSDP, type 15
/// the ACPI 2.0+ (extended) one. Either is enough to reach the table set.
const TAG_TYPE_ACPI_OLD_RSDP: u32 = 14;
const TAG_TYPE_ACPI_NEW_RSDP: u32 = 15;

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

/// The ACPI Root System Description Pointer, copied out of whichever
/// Multiboot2 tag carried it. `address` is the RSDP's own location
/// (physical == virtual under this kernel's identity map) so the
/// checksum can be recomputed and the RSDT/XSDT walked by `crate::acpi`.
#[derive(Debug, Clone, Copy)]
pub struct AcpiRsdp {
    pub address: u64,
    pub revision: u8,
    /// ACPI 1.0 table root (32-bit table pointers).
    pub rsdt_address: u32,
    /// ACPI 2.0+ table root (64-bit table pointers); 0 when absent.
    pub xsdt_address: u64,
    pub length: u32,
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

    /// Walks every tag, calling `f(tag_type, tag_start, tag_end)` with
    /// `tag_start` pointing at the tag's header (not its payload -- the
    /// memory-map reader needs the header's own fields). Bounds every
    /// read against `total_size` before dereferencing, exactly as the
    /// single-purpose walk used to.
    fn walk_tags<F: FnMut(u32, usize, usize)>(&self, mut f: F) {
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

            f(header.typ, cursor, tag_end);

            // tags are 8-byte aligned; advance and round up
            cursor = (tag_end + 7) & !7;
        }
    }

    /// Walks the tag list looking for the memory map tag, calling
    /// `f` once per entry.
    pub fn for_each_memory_region<F: FnMut(MemoryRegion)>(&self, mut f: F) {
        self.walk_tags(|typ, start, end| {
            if typ == TAG_TYPE_MEMORY_MAP {
                self.emit_memory_map_entries(start, end, &mut f);
            }
        });
    }

    /// Finds and copies out the ACPI RSDP, if the loader supplied one
    /// (Multiboot2 tag 14 or 15 -- GRUB provides both). Validates only
    /// the 8-byte signature here; full checksum validation and table
    /// walking live in `crate::acpi`.
    pub fn find_acpi_rsdp(&self) -> Option<AcpiRsdp> {
        let mut found: Option<AcpiRsdp> = None;
        self.walk_tags(|typ, start, end| {
            if found.is_some() || (typ != TAG_TYPE_ACPI_OLD_RSDP && typ != TAG_TYPE_ACPI_NEW_RSDP) {
                return;
            }
            let payload = start + mem::size_of::<TagHeader>();
            // An RSDP is at least 20 bytes (ACPI 1.0); the 2.0 form adds 16.
            if end < payload + 20 {
                return;
            }
            let rsdp = payload as *const u8;
            let sig = unsafe { core::slice::from_raw_parts(rsdp, 8) };
            if sig != &b"RSD PTR "[..] {
                return;
            }
            let revision = unsafe { *rsdp.add(15) };
            let rsdt_address = unsafe { core::ptr::read_unaligned(rsdp.add(16) as *const u32) };
            let has_v2 = revision >= 2 && end >= payload + 36;
            let (length, xsdt_address) = if has_v2 {
                let len = unsafe { core::ptr::read_unaligned(rsdp.add(20) as *const u32) };
                let xsdt = unsafe { core::ptr::read_unaligned(rsdp.add(24) as *const u64) };
                (len, xsdt)
            } else {
                (20, 0)
            };
            found = Some(AcpiRsdp {
                address: payload as u64,
                revision,
                rsdt_address,
                xsdt_address,
                length,
            });
        });
        found
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
