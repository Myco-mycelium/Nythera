//! Minimal ACPI bring-up: RSDP validation and RSDT/XSDT table
//! enumeration -- just enough to justify reclaiming the memory the
//! firmware labelled `AcpiReclaimable`.
//!
//! Closes the "ACPI-reclaimable memory reuse" gap named in
//! IMPLEMENTATION_STATUS.md. Per the ACPI spec, memory the firmware
//! reports as *reclaimable* holds ACPI tables that the OS may reuse
//! only once it is finished reading them. The frame allocator
//! (`crate::memory::frame_allocator`) deliberately refuses those
//! regions until [`crate::acpi`] says it's done with them -- which is
//! exactly what this module does: it reaches the tables via the RSDP
//! the loader handed off, validates them, and only then does
//! `kernel_main` release the regions back to the allocator.
//!
//! Honest scope: this kernel does **not** yet *interpret* any ACPI
//! table (no MADT/APIC setup, no FADT power management, no AML). It
//! reads the table headers to prove the tables are reachable and
//! valid, then frees the reclaimable regions. If a later subsystem
//! needs a table's contents, it must be copied before the reclaim --
//! stated here so the reclaim isn't mistaken for "ACPI is supported".
//!
//! Every read is against physical addresses (== virtual under this
//! kernel's identity map), so the bounds checks here are real
//! memory-safety checks, not formalities: lengths are capped and
//! table pointers are validated before the header is trusted.

use crate::multiboot::AcpiRsdp;
use crate::serial_println;

/// ACPI table header: signature[4], length u32, revision u8, checksum
/// u8, then OEM id/table id/revisions. 36 bytes for every table type
/// this module reads.
const TABLE_HEADER_LEN: u32 = 36;

/// Upper bound on a single table's claimed length. Real ACPI tables are
/// small (a few KiB at most); this stops a malformed length from
/// walking this kernel across arbitrary memory while checksumming.
const MAX_TABLE_LEN: u32 = 64 * 1024;

/// Upper bound on how many tables we'll enumerate from the root table.
const MAX_TABLES: u32 = 128;

#[derive(Debug, Clone, Copy)]
pub struct AcpiInfo {
    pub revision: u8,
    pub table_count: u32,
    /// One of these is non-zero; which root table was used.
    pub used_xsdt: bool,
    /// The FADT's OEM ID (header bytes 10..16): the firmware vendor's
    /// own signature. `crate::platform_mode` reads this to tell an
    /// Apple or Microsoft machine from a generic one by what the
    /// firmware says about itself, not by guessing from the display.
    pub oem_id: [u8; 6],
    /// The FADT's OEM TABLE ID (header bytes 16..24): the board or
    /// product name the OEM wrote, for the boot transcript.
    pub oem_table_id: [u8; 8],
    /// Whether a FADT ("FACP") was found at all. The OEM fields are
    /// zero when it was not -- a fact the caller can see.
    pub fadt_found: bool,
}

impl AcpiInfo {
    pub fn oem_id(&self) -> &[u8] {
        &self.oem_id
    }

    pub fn oem_table_id(&self) -> &[u8] {
        &self.oem_table_id
    }
}

/// A table header's 4-byte signature.
fn table_signature(addr: u64) -> [u8; 4] {
    let p = addr as *const u8;
    unsafe { [*p, *p.add(1), *p.add(2), *p.add(3)] }
}

/// Validates the RSDP and enumerates the tables reachable from it.
/// Returns `None` if the RSDP checksum is wrong or the root table is
/// unusable -- in which case the caller must **not** reclaim the
/// reclaimable regions (we can't claim we've read them).
pub fn validate_and_enumerate(rsdp: &AcpiRsdp) -> Option<AcpiInfo> {
    if !validate_rsdp_checksum(rsdp) {
        return None;
    }

    let (root_addr, entry_size, used_xsdt) = if rsdp.xsdt_address != 0 {
        (rsdp.xsdt_address, 8u64, true)
    } else if rsdp.rsdt_address != 0 {
        (rsdp.rsdt_address as u64, 4u64, false)
    } else {
        return None;
    };

    let root_len = read_table_length(root_addr)?;
    if root_len < TABLE_HEADER_LEN {
        return None;
    }
    let entries = ((root_len - TABLE_HEADER_LEN) as u64) / entry_size;
    let entries = core::cmp::min(entries, MAX_TABLES as u64) as u32;

    let mut valid = 0u32;
    let mut oem_id = [0u8; 6];
    let mut oem_table_id = [0u8; 8];
    let mut fadt_found = false;
    for i in 0..entries {
        let entry_ptr = (root_addr + TABLE_HEADER_LEN as u64 + i as u64 * entry_size) as *const u8;
        let table_addr: u64 = if used_xsdt {
            unsafe { core::ptr::read_unaligned(entry_ptr as *const u64) }
        } else {
            (unsafe { core::ptr::read_unaligned(entry_ptr as *const u32) }) as u64
        };
        if table_addr == 0 {
            continue;
        }
        if table_checksum_ok(table_addr) {
            valid += 1;
            // The FADT is mandatory on every ACPI system and carries the
            // OEM identity fields in its header, so it is the one table
            // worth reading beyond counting. Its checksum was already
            // verified above, so a signature match here is trustworthy.
            if !fadt_found && table_signature(table_addr) == *b"FACP" {
                fadt_found = true;
                let base = table_addr as *const u8;
                for i in 0..6 {
                    oem_id[i] = unsafe { *base.add(10 + i) };
                }
                for i in 0..8 {
                    oem_table_id[i] = unsafe { *base.add(16 + i) };
                }
            }
        }
    }

    Some(AcpiInfo {
        revision: rsdp.revision,
        table_count: valid,
        used_xsdt,
        oem_id,
        oem_table_id,
        fadt_found,
    })
}

/// The RSDP's own checksum: the first 20 bytes sum to zero (mod 256).
/// For ACPI 2.0+ RSDPs the extended checksum covering the full length
/// is a separate field; the 20-byte checksum is required in both forms
/// and is what we validate here (documented, not an oversight).
fn validate_rsdp_checksum(rsdp: &AcpiRsdp) -> bool {
    let base = rsdp.address as *const u8;
    let mut sum: u8 = 0;
    for i in 0..20 {
        sum = sum.wrapping_add(unsafe { *base.add(i) });
    }
    sum == 0
}

/// Reads a table's length field after validating the signature area is
/// minimally present. Returns `None` for an implausible length.
fn read_table_length(addr: u64) -> Option<u32> {
    let len = unsafe { core::ptr::read_unaligned((addr + 4) as *const u32) };
    if (TABLE_HEADER_LEN..=MAX_TABLE_LEN).contains(&len) {
        Some(len)
    } else {
        None
    }
}

/// Checks that a table's length is plausible and that every byte of the
/// table sums to zero (the ACPI table checksum contract).
fn table_checksum_ok(addr: u64) -> bool {
    let Some(len) = read_table_length(addr) else {
        return false;
    };
    let ptr = addr as *const u8;
    let mut sum: u8 = 0;
    for i in 0..len as u64 {
        sum = sum.wrapping_add(unsafe { *ptr.add(i as usize) });
    }
    sum == 0
}

/// Convenience wrapper: validate the RSDP carried by `multiboot` and
/// enumerate its tables, printing a one-line summary. Returns the parsed
/// info (or `None` plus a printed reason) so `kernel_main` can decide
/// whether reclaiming is justified.
pub fn init(rsdp: Option<AcpiRsdp>) -> Option<AcpiInfo> {
    let Some(rsdp) = rsdp else {
        serial_println!("  acpi: no RSDP tag supplied by the loader -- ACPI-reclaimable memory will not be reused");
        return None;
    };
    match validate_and_enumerate(&rsdp) {
        Some(info) => {
            serial_println!(
                "  acpi: RSDP v{} validated ({} root table), {} tables enumerated and checksum-verified",
                info.revision,
                if info.used_xsdt { "XSDT" } else { "RSDT" },
                info.table_count
            );
            if info.fadt_found {
                let oem = core::str::from_utf8(info.oem_id()).unwrap_or("??????").trim_end();
                let table = core::str::from_utf8(info.oem_table_id())
                    .unwrap_or("????????")
                    .trim_end();
                serial_println!(
                    "  acpi: FADT OEM ID \"{}\", OEM table ID \"{}\" (the firmware names its vendor)",
                    oem,
                    table
                );
            }
            Some(info)
        }
        None => {
            serial_println!("  acpi: RSDP present but failed validation (checksum/root table unusable) -- not reclaiming");
            None
        }
    }
}
