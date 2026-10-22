//! Nyrqis Phase 10 UEFI-native boot stub (no_std, no_main, zero deps).
//!
//! Handoff order, each step checking the fact it relies on before
//! moving on:
//!   1. banner over the firmware's own console; SecureBoot variable read
//!   2. watchdog off; page tables + the handoff page allocated and zeroed
//!   3. embedded kernel ELF parsed and loaded to its physical addresses
//!   4. UEFI memory map acquired; Multiboot2 info structure synthesized
//!      into the handoff page (memory map + RSDP copy + vendor tag)
//!   5. ExitBootServices; CR3 switched to the stub's tables; jump to
//!      the kernel's own `uefi_entry` with the Multiboot2 contract
//!      registers set exactly the way GRUB's 32-bit handoff leaves them.
//!
//! The embedded kernel ELF is `include_bytes!` from the kernel build's
//! target/kernel.elf -- the stub is built AFTER the kernel on every
//! build (build.sh enforces the order), so a stale embed cannot sneak
//! through a fresh build; and the boot test itself verifies the
//! kernel's own transcript reaching Ready, so it cannot pass silently
//! at boot either.

#![no_std]
#![no_main]

extern crate alloc;

mod boot;
mod efi;
mod elf;
mod mb2;

use core::arch::asm;
use core::panic::PanicInfo;

use efi::*;

/// 1 GiB: the kernel's Phase-1 identity window. Everything the stub
/// allocates and everything it hands to the kernel must live inside
/// it, since the synthesized mapping covers exactly that range.
pub const IDENTITY_LIMIT: u64 = 0x4000_0000;

/// The kernel image, embedded at build time.
fn kernel_elf_bytes() -> &'static [u8] {
    include_bytes!("../../target/kernel.elf")
}

static mut SYSTEM_TABLE: *const SystemTable = core::ptr::null();
static mut IMAGE_HANDLE: Handle = core::ptr::null_mut();

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    // After ExitBootServices the console is gone; the panic prints what
    // it can and halts either way. The stub's logic contains no
    // fallible-Rust paths (every refusal is a typed die() with its own
    // message), so a reaching panic handler means a code bug.
    boot::conout("\r\nnyrqis ubld: PANIC -- stub code bug; halting\r\n");
    boot::halt()
}

#[unsafe(no_mangle)]
pub extern "efiapi" fn efi_main(image_handle: Handle, system_table: *const SystemTable) -> Status {
    unsafe {
        SYSTEM_TABLE = system_table;
        IMAGE_HANDLE = image_handle;
    }

    // ---- system table sanity: signature, revision, surfaces ----
    {
        let st = unsafe { &*SYSTEM_TABLE };
        boot::check!(
            st.hdr.signature == EFI_SYSTEM_TABLE_SIGNATURE,
            "system-table signature wrong -- this is not an EFI System Table"
        );
        boot::check!(
            st.hdr.revision >= 0x0002_0000,
            "system-table revision below UEFI 2.0"
        );
        boot::check!(
            !st.boot_services.is_null() && !st.runtime_services.is_null(),
            "service tables missing from the system table"
        );
        boot::check!(!st.con_out.is_null(), "no console-output protocol");
    }

    // ---- 1. banner + SecureBoot state (a fact, not a policy: ADR-0014
    //         keeps SB-off boot legal; the record travels in the tag) ----
    boot::conout("\r\nnyrqis ubld: stub alive under UEFI; SecureBoot=");
    let sb = boot::read_secure_boot();
    boot::conout(sb.fact());
    boot::conout("\r\n");

    // ---- 2. watchdog off, allocations ----
    boot::disarm_watchdog();
    let pg_phys = boot::alloc_identity_pages(3, "page tables");
    let hp_phys = boot::alloc_identity_pages(1, "handoff page");
    boot::zero_range(pg_phys, 3 * 4096);
    boot::zero_range(hp_phys, 4096);

    // ---- 3. kernel image ----
    let kernel_bytes = kernel_elf_bytes();
    let ki = match elf::parse(kernel_bytes) {
        Ok(ki) => ki,
        Err(e) => boot::die(elf_error_str(e)),
    };
    for seg in ki.segments.iter().flatten() {
        unsafe {
            let dst = seg.vaddr as *mut u8;
            core::ptr::copy_nonoverlapping(seg.data.as_ptr(), dst, seg.data.len());
            core::ptr::write_bytes(
                dst.add(seg.data.len()),
                0,
                (seg.memsz as usize) - seg.data.len(),
            );
        }
    }
    let entry = match elf::find_symbol(kernel_bytes, "uefi_entry") {
        Ok(a) => a,
        Err(e) => boot::die(elf_error_str(e)),
    };
    boot::conout("nyrqis ubld: kernel image loaded from the stub's own bytes; uefi_entry resolved\r\n");

    // ---- 4. memory map + RSDP + synthesis into the handoff page ----
    let mut map = boot::acquire_memory_map();
    boot::conout("nyrqis ubld: memory map acquired\r\n");
    // The stub's own living structures must never appear as free frames
    // in the synthesized map: the kernel's allocator would reuse them
    // and corrupt the running paging structures. Carve them out before
    // synthesis -- the delta is machine-checkable from the MBI itself.
    {
        let mut reserved: alloc::vec::Vec<(u64, u64)> = alloc::vec::Vec::new();
        reserved.push((pg_phys, pg_phys + 3 * 4096)); // identity PML4/PDP/PD
        reserved.push((hp_phys, hp_phys + 4096)); // handoff page (the MBI)
        reserved.push((map.buf_base, map.buf_base + map.buf_capacity as u64));
        boot::carve_stub_ranges(&mut map, &reserved);
    }
    let rsdp = boot::find_acpi_rsdp();
    match rsdp {
        Some((_, rev)) => {
            boot::conout("nyrqis ubld: rsdp found (acpi config table, rev=");
            boot::conout(if rev >= 2 { "2.0" } else { "1.0" });
            boot::conout(")\r\n");
        }
        None => boot::conout("nyrqis ubld: rsdp ABSENT (no config-table entry)\r\n"),
    }
    let _synth_facts = {
        let page = unsafe { core::slice::from_raw_parts_mut(hp_phys as *mut u8, 4096) };
        match mb2::synthesize(page, &map.descriptors, map.desc_size, rsdp, sb.digit()) {
            Ok(f) => f,
            Err(e) => boot::die(e),
        }
    };
    boot::conout("nyrqis ubld: Multiboot2 info synthesized (map entries + rsdp + vendor tag)\r\n");
    // Dump ACPI-reclaimable regions (EFI type 9) for the transcript: the
    // kernel's reclaim self-check trusts this exact map.
    for d in map.descriptors.iter() {
        if d.r#type == 9 {
            boot::conout("acpi-reclaim region: base=");
            boot::conout_hex(d.physical_start);
            boot::conout(" pages=");
            boot::conout_hex(d.number_of_pages);
            boot::conout("\r\n");
        }
    }
    // One-shot MBI walk for the transcript: tag types in the synthesized
    // structure, so a missing RSDP tag is diagnosable from the log.
    {            unsafe {
            let mut c = hp_phys + 8;
            for _ in 0..8 {
                let typ = core::ptr::read_unaligned(c as *const u32);
                let sz = core::ptr::read_unaligned((c + 4) as *const u32);
                boot::conout_hex(typ as u64);
                boot::conout(":");
                boot::conout_hex(sz as u64);
                boot::conout(" ");
                if typ == 0 {
                    break;
                }
                c = (c + sz as u64 + 7) & !7u64;
            }
        }
        boot::conout("\r\n");
    }

    // ---- 5. identity tables, exit boot services, switch, jump ----
    boot::build_identity_tables(pg_phys);
    boot::exit_boot_services(&mut map);
    unsafe {
        asm!("mov {p}, %cr3", p = in(reg) pg_phys, options(att_syntax, preserves_flags));
    }
    boot::raw_uart_write("[s] cr3 switched; jumping uefi_entry\r\n");
    // No firmware calls from here: the console is gone by design; the
    // kernel's own serial driver takes over diagnostics.
    unsafe {
        asm!(
            "mov {m}, %rdi",
            "mov {b}, %rsi",
            "jmp *{e}",
            m = in(reg) elf::MB2_BOOTLOADER_MAGIC,
            b = in(reg) hp_phys,
            e = in(reg) entry,
            options(att_syntax, noreturn)
        );
    }
}

fn elf_error_str(e: elf::ElfError) -> &'static str {
    match e {
        elf::ElfError::TooSmall => "kernel ELF smaller than an ELF64 header",
        elf::ElfError::BadMagic => "kernel ELF bad magic",
        elf::ElfError::Not64Bit => "kernel ELF is not 64-bit",
        elf::ElfError::NotExecutable(_) => "kernel ELF is not x86-64 ELF v1",
        elf::ElfError::Truncated(_) => "kernel ELF truncated/malformed structure",
        elf::ElfError::NoLoadSegments => "kernel ELF carries no PT_LOAD segment",
        elf::ElfError::SymbolMissing(_) => "kernel ELF misses the uefi_entry symbol",
    }
}
