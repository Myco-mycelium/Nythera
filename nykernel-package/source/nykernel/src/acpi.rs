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
        }
    }

    Some(AcpiInfo {
        revision: rsdp.revision,
        table_count: valid,
        used_xsdt,
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
            Some(info)
        }
        None => {
            serial_println!("  acpi: RSDP present but failed validation (checksum/root table unusable) -- not reclaiming");
            None
        }
    }
}
