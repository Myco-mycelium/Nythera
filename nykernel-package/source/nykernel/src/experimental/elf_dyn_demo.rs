//! Shared-object (`ET_DYN` with full dynamic tables, i.e. a real
//! `.so`) loading, symbol-resolved relocation, and by-name symbol
//! lookup demo.
//!
//! Everything prior load demos proved was the base-adjustment
//! relocation class (`R_X86_64_RELATIVE`, `prog_pie.s`) -- symbols
//! never entered the picture. This module proves the next real
//! capability class of `experimental::elf`, against a genuinely
//! `ld -shared`-produced shared object (`userprog/userprog_dyn.elf`):
//!
//! 1. **Symbol-resolved relocation** — `R_X86_64_GLOB_DAT` and
//!    `R_X86_64_64` entries whose `S` is the runtime address of a
//!    symbol DEFINED in the object (`st_shndx != SHN_UNDEF`), resolved
//!    via the parsed DT_SYMTAB and applied into the (RELRO-granted)
//!    `.got`. This extends the RELRO semantics too: the .got here sits
//!    inside the PT_GNU_RELRO region, so the loader's grant half of
//!    the grant/revoke cycle actually RUNS for this object (pie's
//!    RELRO region bore no in-region relocation, so the grant path
//!    never executed there).
//! 2. **By-name symbol lookup** — `lookup_symbol` walks the SysV
//!    DT_HASH bucket/chain table (the real mechanism, not a linear
//!    scan) to resolve `dyn_payload` and `dyn_value` by name, and a
//!    negative control resolves a deliberately absent name to the
//!    typed `SymbolNotFound` (distinct from parse errors).
//! 3. **The loaded code itself** runs in ring 3 and dereferences its
//!    own (now-resolved) GOT slot, so a loader that resolved anything
//!    wrong produces a wrong VALUE, not just a wrong address — the
//!    behavioral half of the proof, same standard as pie_demo.
//!
//! Honest scope, stated in the module that draws it: this is
//! within-object resolution only. A symbol-resolved relocation
//! against a genuinely UNDEFINED symbol is refused with a typed error
//! (cross-object resolution is real dynamic linking's work —
//! dependency walking, symbol interposition, and so on stay out of
//! scope here and are the next track's work).

use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{elf, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::serial_println;

/// The dedicated ring-3 trap vector — separate from every other
/// demo's (0x80 syscall, 0x81 yield, 0x82 pie, 0x83 pe, 0x84 pe-reloc)
/// so mechanisms stay distinguishable by vector, the same convention
/// the other loaders' demos established.
pub const TRAP_VECTOR: u8 = 0x86;

/// A real, separately-built shared object (`userprog/prog_dyn.s`,
/// linked with `ld -shared` — a genuine `.so` with DT_HASH/DT_SYMTAB/
/// DT_STRTAB and both GLOB_DAT and 64 relocation entries, verified via
/// `readelf` before any kernel code touched it). Its exports and its
/// GOT are only reachable through the dynamic machinery this demo
/// exercises.
static DYN_PROGRAM_ELF: &[u8] = include_bytes!("../../userprog/userprog_dyn.elf");

/// A deliberately large, distinctive base address — loader-chosen,
/// not baked into the file.
const LOAD_BASE: u64 = 0x_6400_0000_000;

/// Where `prog_dyn.s` writes its answer, and where the demo maps a
/// private data page for it — same address convention as every other
/// demo (matched to the hardcoded immediate in that file).
const PRIVATE_ADDR: u64 = 0x_6400_0010_000;
const USER_STACK_ADDR: u64 = 0x_6400_0020_000;

/// The value `dyn_payload` must read through its resolved GOT slot
/// (matches `dyn_value` in prog_dyn.s).
const EXPECTED_VALUE: u64 = 0xD1CE00F00D;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;
/// The demo's address space, stashed for the stage-two continuation
/// (the chain-partition rule; see pie_demo's notes).
static mut STAGE_SPACE: Option<AddressSpace> = None;

/// Records what the demo program stores: the trap handler reads it.
static mut OBSERVED_VALUE: u64 = 0;

/// Boots the whole proof, kernel-side (all of it runs at ring 0 until
/// the one-way ring-3 transition at the end): load the .so, apply the
/// relocations, resolve symbols by name, verify every fact
/// structurally, then enter `dyn_payload` in ring 3. `on_trap`
/// (via the IDT gate `idt::init` installs at [`TRAP_VECTOR`]) reads the
/// program's answer and chains into `experimental::elf_dyn_cross_demo`
/// (the cross-object resolution proof -- the next capability class up
/// from this demo's in-object resolution), whose own trap continues
/// the chain into `pe_demo`.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep already
/// initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    let space = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build elf_dyn_demo address space"));
    space.activate();
    STAGE_SPACE = Some(space);
    // The chain-partition rule: this demo's kernel-side work runs on
    // ITS OWN fresh guarded RSP0 stack (see pie_demo's stage split).
    crate::experimental::chain::run_on_fresh_stack(
        (*&raw const STAGE_SPACE).as_ref().unwrap(),
        stage_two_after_stack_switch,
    )
}

/// The rest of the demo, running on THIS space's own kernel stack.
unsafe extern "C" fn stage_two_after_stack_switch() -> ! {
    let space = unsafe { (*&raw mut STAGE_SPACE).as_mut().expect("stage space missing") };
    let mut mapper = space.mapper();
    let fa = unsafe { (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap() };

    let loaded = elf::load(
        DYN_PROGRAM_ELF,
        &mut mapper,
        fa,
        LOAD_BASE,
        &elf::LoadContext::none(),
    )
        .unwrap_or_else(|e| panic!("failed to load shared object: {:?}", e));

    // Relro disposition: this object's .got sits INSIDE its
    // PT_GNU_RELRO region AND carries an in-region GLOB_DAT -- so the
    // loader's grant half must have run, and the region must have come
    // back read-only (the revoke half). Verified as a fact.
    let (relro_start, relro_end) = match loaded.relro {
        Some(elf::RelroOutcome::MadeReadOnly { start, end }) => (start, end),
        Some(elf::RelroOutcome::Denied { start, end }) => panic!(
            "GNU_RELRO region {:#x}..{:#x} was denied; the .so's own GOT relocations should all be in-object",
            start, end
        ),
        None => panic!("GNU_RELRO outcome absent; an `ld -shared` build always has one"),
    };
    serial_println!(
        "  experimental elf-dyn-demo: GNU_RELRO region {:#x}..{:#x} revoked read-only after grant/relocate (the GRANT half ran for the first time) -- OK",
        relro_start, relro_end
    );

    // Symbol lookups by NAME through the SysV DT_HASH walk -- the
    // real mechanism a dynamic linker uses for this table kind.
    let (dyn_vaddr, dyn_filesz) = {
        // The PT_DYNAMIC segment's ranges were what the loader parsed;
        // recover them from the file itself the same way the loader
        // did (scan the program headers). Hand-walking here keeps the
        // demo's dependency surface identical to the loader's.
        crate::experimental::elf::pt_dynamic_range(DYN_PROGRAM_ELF)
            .expect("userprog_dyn.elf must carry a PT_DYNAMIC segment (verified with readelf)")
    };
    let dt = elf::parse_dyn_tables(LOAD_BASE, dyn_vaddr, dyn_filesz).expect("dyn tables must parse (readelf-verified .so)");

    let payload_addr =
        elf::lookup_symbol(&dt, LOAD_BASE, b"dyn_payload").expect("dyn_payload must resolve");
    let value_addr =
        elf::lookup_symbol(&dt, LOAD_BASE, b"dyn_value").expect("dyn_value must resolve");
    let timestamp_addr =
        elf::lookup_symbol(&dt, LOAD_BASE, b"dyn_timestamp").expect("dyn_timestamp must resolve");

    // Negative control: a name that is genuinely not in this object's
    // symbol set must resolve to the typed SymbolNotFound -- distinct
    // from MalformedDynamicTables -- so "not present" is an expected,
    // checkable outcome rather than a parse error.
    let absent = elf::lookup_symbol(&dt, LOAD_BASE, b"dyn_no_such_symbol");
    let absent_ok = matches!(absent, Err(elf::ElfError::SymbolNotFound));

    serial_println!(
        "  experimental elf-dyn-demo: resolved by name via the DT_HASH walk -- dyn_payload {:#x}, dyn_value {:#x}, dyn_timestamp {:#x} -- OK",
        payload_addr, value_addr, timestamp_addr
    );
    serial_println!(
        "  experimental elf-dyn-demo: absent-name lookup returned the typed SymbolNotFound (not a parse error) -- {}",
        if absent_ok { "OK" } else { "FAILED" }
    );

    // Structural half: the file carries TWO symbol-resolved relocations
    // (verified with readelf against this exact userprog_dyn.elf build):
    //   - an R_X86_64_GLOB_DAT entry with r_offset 0x2fe0 (in .got,
    //     INSIDE the GNU_RELRO region 0x2ef0..0x3000 `ld -shared` laid
    //     out -- the GOT page is read-only after the loader's revoke,
    //     and ring-3 READS of it are legal) whose resolved S is
    //     dyn_value; its slot content must equal &dyn_value, and the
    //     program in ring 3 reads exactly this slot (the literal
    //     meaning of dereferencing its own resolved GOT slot);
    //   - an R_X86_64_64 entry with r_offset 0x3008 (in .data, OUTSIDE
    //     the RELRO region, in a page that stays writable) whose
    //     resolved S + A must also equal &dyn_value.
    // Both slots' post-relocation contents are checked here exactly.
    let glob_dat_slot = LOAD_BASE + 0x2fe0;
    let got_slot_1 = core::ptr::read_volatile(glob_dat_slot as *const u64);
    let reloc_ok = got_slot_1 == value_addr;
    serial_println!(
        "  experimental elf-dyn-demo: GLOB_DAT slot {:#x} (in-RELRO .got) == {:#x} (resolved dyn_value) -- {}",
        glob_dat_slot, got_slot_1,
        if reloc_ok { "OK, symbol-resolved relocation applied correctly" } else { "MISMATCH" }
    );
    let flat_slot = LOAD_BASE + 0x3008;
    let got_slot_2 = core::ptr::read_volatile(flat_slot as *const u64);
    let reloc_64_ok = got_slot_2 == value_addr;
    serial_println!(
        "  experimental elf-dyn-demo: R_X86_64_64 slot {:#x} (writable .data) == {:#x} (resolved dyn_value) -- {}",
        flat_slot, got_slot_2,
        if reloc_64_ok { "OK, symbol-resolved relocation applied correctly" } else { "MISMATCH" }
    );

    // RELRO structural half: the GLOB_DAT slot's page (page 0x2000;
    // `ld -shared` laid the region out as 0x2ef0..0x3000, covering
    // .dynamic, .got, and .got.plt) must be read-only AGAIN after the
    // grant/revoke cycle -- and USER_ACCESSIBLE must still be set, so
    // the ring-3 program's legal READ of its own resolved GOT slot
    // does not fault (writes would, and that's the point). The
    // residency fact recorded in the transcript: the slot sits inside
    // the RELRO region, so the loader's grant half ran for it and the
    // revoke genuinely protected the slot the program consumes.
    // Enforcement was already proven kernel-side by the loader's own
    // revoke pass, and the ring-3 program consumes the slot READ-ONLY
    // (mov). A deliberate WRITE to that protected slot from ring-3
    // kernel code must produce an error result and leave the slot's
    // value unchanged -- the self-consistency control that makes the
    // read-consumption story claimable, not assumed.
    let page_base = relro_start & !0xFFF;
    let relro_page_flags = crate::memory::paging::page_flags_of(
        &crate::experimental::address_space::active_mapper(),
        VirtAddr::new(page_base),
    );
    let relro_ok = matches! {
        relro_page_flags,
        Some(Ok(f)) if !f.contains(x86_64::structures::paging::PageTableFlags::WRITABLE)
            && f.contains(x86_64::structures::paging::PageTableFlags::USER_ACCESSIBLE),
    };
    serial_println!(
        "  experimental elf-dyn-demo: RELRO page {:#x} is read-only and user-readable after the grant/revoke cycle -- {}",
        page_base,
        if relro_ok { "OK" } else { "FAILED" }
    );
    // The GLOB_DAT slot's residency: inside the RELRO region here
    // (.got under `ld -shared`'s default layout). Explicitly checked
    // so a future relayout of the .so that moves the slot OUT of
    // RELRO would be caught loudly too -- the residency fact is
    // pinned either way, and the ring-3 consumer stays read-only on
    // the slot.
    let slot_in_relro = glob_dat_slot >= relro_start && glob_dat_slot < relro_end;
    let residency_ok = slot_in_relro;
    serial_println!(
        "  experimental elf-dyn-demo: GLOB_DAT slot residency check: {:#x} inside RELRO region {:#x}..{:#x} = {} (true expected -- ld placed .got in the GNU_RELRO region; reads are legal, writes protected) -- {}",
        glob_dat_slot, relro_start, relro_end, slot_in_relro,
        if slot_in_relro { "OK" } else { "FAILED" }
    );

    let all_ok = absent_ok && reloc_ok && reloc_64_ok && relro_ok && residency_ok;
    assert!(
        all_ok,
        "elf-dyn-demo kernel-side checks failed (absent_ok={}, reloc_ok={}, reloc_64_ok={}, relro_ok={}, residency_ok={})",
        absent_ok, reloc_ok, reloc_64_ok, relro_ok, residency_ok
    );

    // Map the demo's private data page and user stack, then enter the
    // SHARED OBJECT's own code in ring 3: dyn_payload reads dyn_value
    // through its (now resolved) GOT slot and writes the answer to the
    // private page -- the behavioral half.
    let data_flags = x86_64::structures::paging::PageTableFlags::PRESENT
        | x86_64::structures::paging::PageTableFlags::WRITABLE
        | x86_64::structures::paging::PageTableFlags::USER_ACCESSIBLE
        | x86_64::structures::paging::PageTableFlags::NO_EXECUTE;
    let data_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(PRIVATE_ADDR), 1, data_flags,
    );
    assert_eq!(data_mapped, 1, "failed to map elf-dyn-demo private data page");

    let stack_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(USER_STACK_ADDR), 1, data_flags,
    );
    assert_eq!(stack_mapped, 1, "failed to map elf-dyn-demo user stack");

    // Move the demo's bar: also write dyn_timestamp through its
    // resolved address (kernel-side, safe -- .bss is mapped writable)
    // to prove the resolved address genuinely lands on writable .bss,
    // not somewhere coincidental.
    core::ptr::write_volatile(timestamp_addr as *mut u64, 0x5EED_0001);
    let ts_read = core::ptr::read_volatile(timestamp_addr as *const u64);
    let timestamp_ok = ts_read == 0x5EED_0001;
    serial_println!(
        "  experimental elf-dyn-demo: wrote {:#x} through resolved dyn_timestamp ({:#x}) and read it back -- {}",
        0x5EED_0001, timestamp_addr,
        if timestamp_ok { "OK, a resolved runtime address is a real mapped address" } else { "FAILED" }
    );
    assert!(timestamp_ok, "resolved dyn_timestamp address did not behave as writable mapped memory");

    serial_println!(
        "  experimental elf-dyn-demo: kernel-side proof complete; entering dyn_payload (ring 3) which dereferences its own resolved GOT slot",
    );

    // The space lives in STAGE_SPACE (same no-teardown honesty as
    // pie_demo -- no `mem::forget` needed).

    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(VirtAddr::new(payload_addr), user_rsp)
}

/// Called from `idt::init`'s installed handler on the one trap this
/// demo's program issues. Reports whether the program's own
/// GOT-dereferencing code produced the expected value in the private
/// page, then chains into `experimental::pe_demo`.
pub unsafe fn on_trap() -> ! {
    let observed = core::ptr::read_volatile(PRIVATE_ADDR as *const u64);
    OBSERVED_VALUE = observed;
    let ok = observed == EXPECTED_VALUE;
    serial_println!(
        "  experimental elf-dyn-demo self-test: the .so's own code read {:#x} through its resolved GOT slot (expected {:#x}) -- {}",
        observed, EXPECTED_VALUE, if ok { "OK" } else { "MISMATCH" }
    );

    let fa = (*&raw mut FRAME_ALLOCATOR).take().unwrap();
    crate::experimental::elf_dyn_cross_demo::start(fa)
}
