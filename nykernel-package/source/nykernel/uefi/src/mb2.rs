//! Multiboot2 info-structure synthesis from the UEFI memory map (Phase 10).
//!
//! The kernel's entire boot contract is "RDI = Multiboot2 boot-loader
//! magic, RSI = a real Multiboot2 info structure" -- the same
//! structure shape GRUB delivers on the legacy path. The adopted
//! design keeps that contract untouched: this module SYNTHESIZES the
//! structure the contract names, from real boot-services bytes.
//!
//! Contents, and why each tag exists:
//! - a type-6 memory-map tag, re-encoded 1:1 from the UEFI memory
//!   map GetMemoryMap returned (each 40-byte EFI descriptor becomes a
//!   24-byte MB2 entry; the type translation is documented inline);
//! - a type-15 ACPI RSDP tag carrying a byte-exact copy of the ACPI
//!   2.0 RSDP read from the EFI configuration table (the kernel's
//!   `acpi::init` checksum-validates this copy -- the graphic chain
//!   already runs on it, so it must be a faithful copy, not a summary);
//! - a type-0x7d1 vendor tag carrying a short ASCII fact string, e.g.
//!   "UEFI SB=1", which the kernel parses explicitly and logs, so the
//!   boot transcript states WHICH native path produced this MBI.
//! - the terminating type-0 tag.
//!
//! Written into a page the stub owns (allocated before Probe and
//! marked in-use by the synthesized map itself), at a physical address
//! inside the first 1 GiB, because the kernel's Phase-1 identity map
//! covers exactly that window.

use crate::efi::MemoryDescriptor;
use alloc::vec::Vec;

// MB2 tag types (the subset written here).
const TAG_TYPE_END: u32 = 0;
const TAG_TYPE_MEMORY_MAP: u32 = 6;
const TAG_TYPE_ACPI_NEW_RSDP: u32 = 15;
/// Multiboot2 reserves 0x7d1..=0x9ff for custom vendor tags; the type
/// used is recorded alongside the format in multiboot.rs on the
/// kernel's side. One stub, one vendor tag, one kernel parser: the two
/// copies must stay in agreement, and the gate checks both.
const TAG_TYPE_NYRQIS_BOOT_SOURCE: u32 = 0x7d1;

/// MB2 memory types (the values `MultibootInfo::for_each_memory_region`
/// maps: 1 Available, 2 Reserved, 3 AcpiReclaimable, 4 AcpiNvs, 5 BadRam).
fn mb2_type(efi_type: u32) -> u32 {
    match efi_type {
        7 => 1,          // EfiConventionalMemory -> Available
        3 | 4 => 1,      // Efi{BootServicesCode,Data} -> Available (free post-EBS)
        9 => 3,          // EfiACPIReclaimable
        10 => 4,         // EfiACPIMemoryNVS
        8 => 5,          // EfiUnusableMemory -> BadRam
        // Everything else (reserved, loader/rt services, MMIO, ...) is
        // NOT usable by the kernel: reserved, never guessed-free.
        _ => 2,
    }
}

/// The boot-source vendor tag's fixed shape so the kernel-side parser
/// and this writer cannot drift: bytes 0..= ("UEFI SB=") + 1 ASCII
/// digit, NUL-terminated.
pub fn source_string(secure_boot: u8) -> [u8; 10] {
    let mut out = *b"UEFI SB= \0";
    out[8] = b'0' + secure_boot;
    out
}

pub const SOURCE_STRING_LEN: usize = 10;

// Straight-line tag writers; `w` is the synthetic layout cursor.
struct Writer<'a> {
    buf: &'a mut [u8],
    pos: usize,
}

const fn align8(v: usize) -> usize {
    (v + 7) & !7
}

impl<'a> Writer<'a> {
    fn push(&mut self, bytes: &[u8]) -> Result<(), &'static str> {
        if self.pos + bytes.len() > self.buf.len() {
            return Err("synthesized MBI overflowed its page -- enlarge the writer");
        }
        self.buf[self.pos..self.pos + bytes.len()].copy_from_slice(bytes);
        self.pos += bytes.len();
        Ok(())
    }
    fn tag(&mut self, typ: u32, payload: &[u8]) -> Result<(), &'static str> {
        let size = (8 + payload.len()) as u32;
        let hdr = tag_header_bytes(typ, size);
        self.push(&hdr)?;
        self.push(payload)?;
        self.pos = align8(self.pos);
        Ok(())
    }
}

fn tag_header_bytes(typ: u32, size: u32) -> [u8; 8] {
    let mut out = [0u8; 8];
    out[0..4].copy_from_slice(&typ.to_le_bytes());
    out[4..8].copy_from_slice(&size.to_le_bytes());
    out
}

/// Builds the full MBI. Returns (bytes_written, secure_boot_digit_word)
/// -- the second value is for the stub's own transcript line, nothing
/// in the structure is left implicit.
pub struct SynthFacts {
    pub total_size: u32,
    pub map_entry_count: usize,
    pub rsdp_revision: u8,
}

pub fn synthesize(
    buf: &mut [u8],
    map: &[MemoryDescriptor],
    _desc_size: usize,
    rsdp: Option<(&[u8], u8)>, // (rsdp bytes, revision)
    secure_boot: u8,
) -> Result<SynthFacts, &'static str> {
    let mut w = Writer { buf, pos: 0 };

    // MBI header: total_size, reserved. (A placeholder, patched.)
    w.push(&[0u8; 8])?;

    // ---- type-6 memory map ----
    let entries: Vec<[u8; 24]> = {
        let mut out = Vec::with_capacity(map.len());
        for d in map {
            // Zero-length entries are legal firmware padding; skip them.
            if d.number_of_pages == 0 {
                continue;
            }
            let typ = mb2_type(d.r#type);
            let mut e = [0u8; 24];
            e[0..8].copy_from_slice(&d.physical_start.to_le_bytes());
            e[8..16].copy_from_slice(&(d.number_of_pages * 4096).to_le_bytes());
            e[16..20].copy_from_slice(&typ.to_le_bytes());
            out.push(e);
        }
        out
    };
    let map_tag_size = 16 + entries.len() * 24;
    {
        let mut hdr = [0u8; 16];
        hdr[0..4].copy_from_slice(&TAG_TYPE_MEMORY_MAP.to_le_bytes());
        hdr[4..8].copy_from_slice(&(map_tag_size as u32).to_le_bytes());
        hdr[8..12].copy_from_slice(&24u32.to_le_bytes()); // entry_size
        hdr[12..16].copy_from_slice(&0u32.to_le_bytes()); // entry_version
        w.push(&hdr)?;
        for e in &entries {
            w.push(e)?;
        }
        w.pos = align8(w.pos);
    }

    // ---- the vendor tag, tiny and early ----
    let src = source_string(secure_boot);
    w.tag(TAG_TYPE_NYRQIS_BOOT_SOURCE, &src)?;

    // ---- ACPI RSDP (type 15) ----
    let mut rsdp_revision = 0u8;
    if let Some((bytes, revision)) = rsdp {
        rsdp_revision = revision;
        w.tag(TAG_TYPE_ACPI_NEW_RSDP, bytes)?;
    }

    // ---- terminating tag ----
    {
        let mut hdr = [0u8; 8];
        hdr[0..4].copy_from_slice(&TAG_TYPE_END.to_le_bytes());
        hdr[4..8].copy_from_slice(&8u32.to_le_bytes());
        w.push(&hdr)?;
    }

    let total = w.pos as u32;
    // Patch the header's own total_size over the placeholder.
    buf[0..4].copy_from_slice(&total.to_le_bytes());

    Ok(SynthFacts {
        total_size: total,
        map_entry_count: entries.len(),
        rsdp_revision,
    })
}
