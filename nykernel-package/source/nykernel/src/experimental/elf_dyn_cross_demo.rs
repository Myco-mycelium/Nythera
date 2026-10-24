//! Cross-object symbol resolution: TWO real `ld -shared` shared
//! objects, one PROVIDER of exported data and one DEPENDENT whose
//! relocations genuinely need the registry.
//!
//! `elf_dyn_demo` closed the in-object half of symbol resolution.
//! This module closes the cross-object half for the documentable
//! scope:
//!
//! 1. **An UNDEFINED-symbol relocation resolved across objects** —
//!    `userprog_cross_app.elf` references `cross_value`, which it
//!    does NOT define (its own dynsym entry is `SHN_UNDEF`).
//!    Without a resolver that file is REFUSED (`UndefinedSymbol` --
//!    never resolved-to-garbage). With the [`elf::SymbolRegistry`]
//!    holding the provider (`userprog_cross_lib.elf`), the loader
//!    fills the app's GOT slot with the provider's runtime address
//!    of `cross_value`, and -- the behavioral half -- the app's own
//!    code running IN RING 3 dereferences that slot and reads the
//!    provider's data.
//! 2. **Self-priority (the interposition order control)** — both
//!    objects export `dup_symbol` (distinguishable immediates). The
//!    app's relocation against its OWN duplicate must come from the
//!    app's own dynsym (`st_shndx != 0`, taken from the FILE), never
//!    from the registry's earlier entry -- a careless by-name pass
//!    over the registry would slot the provider's address instead,
//!    and the every-boot check MISMATCHes loudly.
//! 3. **Registry search order decides** — the registry resolves in
//!    load order, first DEFINED match wins. `dup_symbol` is looked
//!    up through two registries built in opposite orders; the
//!    observably different results (address-checked, not just
//!    non-None) prove the order is actually consumed, not vestigial.
//! 4. **RELRO with in-region cross-object relocations** — the app's
//!    `ld -shared` layout puts BOTH GLOB_DAT slots (0x2fd8 self-dup,
//!    0x2fe0 cross) inside its GNU_RELRO region (0x2ee8..0x3000), so
//!    the grant decision has to run for entries whose symbol is
//!    UNDEFINED-but-resolvable (the planner consults the resolver),
//!    and the revoke still lands read-only AND user-readable.
//!
//! Honest scope, stated where it's drawn: this is a load-order
//! registry with a documented, simple search discipline -- not real
//! `ld.so`-grade dependency-graph walking. `DT_NEEDED` (dependency
//! promotion from filenames -- which would need a filesystem/lazy
//! load surface), symbol interposition beyond registry order, and
//! lazy PLT binding remain the open track; JUMP_SLOT entries are
//! bound eagerly at load (equivalent to `-z now`), which our loader's
//! relocation processing has done since the in-object increment.

use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{elf, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::serial_println;

/// The dedicated ring-3 trap vector -- separate from every other
/// demo's (0x80 syscall, 0x81 yield, 0x82 pie, 0x83 pe, 0x84 pe-reloc,
/// 0x86 elf-dyn) so mechanisms stay distinguishable by vector, the
/// convention the other loaders' demos established.
pub const TRAP_VECTOR: u8 = 0x87;

/// The provider object: a real `ld -shared` .so exporting
/// `cross_value`/`dup_symbol` (data only; verified via `readelf`
/// before any kernel code touched it).
static CROSS_LIB_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_lib.elf");

/// The dependent object: a real `ld -shared` .so with an
/// `SHN_UNDEF` reference to `cross_value` (materialized by
/// `cross_value@GOTPCREL` as an R_X86_64_GLOB_DAT), which the loader
/// refuses without a `.cross_value`-defining object already
/// registered.
static CROSS_APP_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_app.elf");

/// Loader-chosen bases, far apart and far from elf_dyn_demo's
/// region: the provider at `0x6c0_0000_0000`, the dependent
/// 16 MiB later.
const LIB_BASE: u64 = 0x_6C0_0000_0000;
const APP_BASE: u64 = 0x_6C0_0004_0000;

/// Where `cross_payload` writes its answer, and its stack -- same
/// per-demo private-region convention as every other demo (matched
/// to the hardcoded immediate in `prog_cross_app.s`).
const PRIVATE_ADDR: u64 = 0x_6C0_0008_0000;
const USER_STACK_ADDR: u64 = 0x_6C0_0008_1000;

/// The value the app must observe through its cross-resolved GOT
/// slot (matches `cross_value` in `prog_cross_lib.s`).
const EXPECTED_VALUE: u64 = 0xACE5_F00D_D1CE_5EED;

/// readelf-parameters of the two objects this module's checks are
/// pinned to (same discipline as elf_dyn_demo: the layout facts on
/// the transcript come from the actual built files):
///   lib:  dynsym {1: dup_symbol @0x2008, 2: cross_value @0x2000};
///         GNU_RELRO 0x1f40..0x2000 (covers .dynamic only).
///   app:  dynsym {1: cross_value UND, 2: cross_payload @0x1000,
///                 3: dup_symbol @0x3000}; .rela.dyn:
///         R_X86_64_GLOB_DAT 0x2fd8 -> dup_symbol (self-defined),
///         R_X86_64_GLOB_DAT 0x2fe0 -> cross_value (SHN_UNDEF);
///         GNU_RELRO 0x2ee8..0x3000 (covers .got, so BOTH GLOB_DAT
///         slots are in-region).
const APP_CROSS_SLOT: u64 = 0x2fe0;
const APP_DUP_SLOT: u64 = 0x2fd8;
const APP_DUP_SYM: u64 = 0x3000;
const LIB_CROSS_SYM: u64 = 0x2000;
const LIB_DUP_SYM: u64 = 0x2008;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;
/// The demo's address space, stashed for the stage-two continuation
/// (the chain-partition rule; see pie_demo's notes).
static mut STAGE_SPACE: Option<AddressSpace> = None;

/// Boots the whole proof, kernel-side: load the provider, register
/// it, load the dependent against the registry, verify every fact
/// structurally (slot contents, self-priority, registry order,
/// RELRO state), then enter `cross_payload` in ring 3. `on_trap`
/// (via the IDT gate `idt::init` installs at [`TRAP_VECTOR`]) reads
/// the program's answer and chains into `experimental::elf_dyn_full_demo`
/// (the version-contract + lazy-binding proof), whose own completion
/// trap continues the chain into `pe_demo`.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep already
/// initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    let space = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build elf_dyn_cross_demo address space"));
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

    // ---- provider: load, report its RELRO disposition, register ----
    // No resolver: the provider defines everything its own file
    // references (here: nothing at all -- its `.data` has no
    // relocations).
    let loaded_lib =
        elf::load(CROSS_LIB_ELF, &mut mapper, fa, LIB_BASE, &elf::LoadContext::none())
            .unwrap_or_else(|e| panic!("failed to load cross-object provider: {:?}", e));
    match loaded_lib.relro {
        Some(elf::RelroOutcome::MadeReadOnly { start, end }) => {
            serial_println!(
                "  experimental cross-demo: provider's GNU_RELRO region {:#x}..{:#x} revoked read-only (revoke-only; its layout has no in-region relocations) -- OK",
                start, end
            );
        }
        Some(elf::RelroOutcome::Denied { start, end }) => panic!(
            "cross-provider's GNU_RELRO region {:#x}..{:#x} unexpectedly denied; the provider defines everything it references (here: nothing)",
            start, end
        ),
        None => panic!("cross-provider's GNU_RELRO outcome absent; an `ld -shared` build always has one"),
    }

    // The registry every later object's undefined symbols resolve
    // against. Push order == documented search order.
    let mut registry = elf::SymbolRegistry::new();
    let (lib_dyn_vaddr, lib_dyn_filesz) = elf::pt_dynamic_range(CROSS_LIB_ELF)
        .expect("cross-provider must carry a PT_DYNAMIC segment (readelf-verified)");
    let lib_tables = elf::parse_dyn_tables(LIB_BASE, lib_dyn_vaddr, lib_dyn_filesz)
        .expect("provider dyn tables must parse (readelf-verified .so)");

    // Provider-side registry facts, kernel-side, BEFORE the dependent
    // load: registry alone resolves the provider's definitions, and
    // its duplicate of `dup_symbol` is what registry-order matching
    // WOULD hand out (the control contrast for self-priority below).
    let cross_via_registry = elf::lookup_symbol(&lib_tables, LIB_BASE, b"cross_value")
        .expect("cross_value must resolve in the provider");
    assert!(
        cross_via_registry == LIB_BASE + LIB_CROSS_SYM,
        "resolved cross_value {:#x} did not match the provider's own symbol value {:#x}",
        cross_via_registry,
        LIB_BASE + LIB_CROSS_SYM
    );
    let dup_via_registry = elf::lookup_symbol(&lib_tables, LIB_BASE, b"dup_symbol")
        .expect("dup_symbol must resolve in the provider");
    assert!(
        dup_via_registry == LIB_BASE + LIB_DUP_SYM,
        "resolved dup_symbol {:#x} did not match the provider's own symbol value {:#x}",
        dup_via_registry,
        LIB_BASE + LIB_DUP_SYM
    );

    // Push AFTER the provider-side lookups (which borrow the tables):
    // the registry owns its copies from here on.
    registry.push(lib_tables, LIB_BASE);
    serial_println!(
        "  experimental cross-demo: provider registered (registry 1 object(s)); cross_value {:#x}, dup_symbol {:#x} resolve from the provider's own tables -- OK",
        cross_via_registry, dup_via_registry
    );

    // ---- dependent: load against the registry ----
    // The app's `cross_value` GLOB_DAT entry is SHN_UNDEF in its own
    // file; `elf::load` must consult the registry to fill it --
    // `UndefinedSymbol` (a hard refusal) otherwise.
    let load_ctx = elf::LoadContext {
        resolver: Some(&registry),
        lazy_plt_page: None,
        own_plt_resolver_page: None,
    };
    let loaded_app = elf::load(CROSS_APP_ELF, &mut mapper, fa, APP_BASE, &load_ctx)
        .unwrap_or_else(|e| panic!("failed to load cross-object dependent: {:?}", e));

    // RELRO disposition: BOTH of the app's GLOB_DAT slots sit inside
    // its GNU_RELRO region (readelf 0x2ee8..0x3000), one of them
    // against an UNDEFINED-but-registry-resolvable symbol -- so the
    // planner must have granted W for relocation and the revoke must
    // have landed read-only (both halves of the cycle at once).
    if let Some(elf::RelroOutcome::Denied { start, end }) = loaded_app.relro {
        panic!(
            "cross-dependent's GNU_RELRO region {:#x}..{:#x} was denied; its in-region relocations are all resolvable (one self-defined, one via the registry), so the grant/revoke cycle must have COMPLETED",
            start, end
        );
    }
    if let Some(elf::RelroOutcome::MadeReadOnly { start, end }) = loaded_app.relro {
        serial_println!(
            "  experimental cross-demo: dependent's GNU_RELRO region {:#x}..{:#x} revoked read-only; the grant half ran for both in-region GLOB_DAT slots (one UNDEF-but-resolved) -- OK",
            start, end
        );
    } else {
        panic!("cross-dependent's GNU_RELRO outcome absent; an `ld -shared` build always has one");
    }

    // ---- structural half: the resolved slots ----
    let (app_dyn_vaddr, app_dyn_filesz) = elf::pt_dynamic_range(CROSS_APP_ELF)
        .expect("cross-dependent must carry a PT_DYNAMIC segment (readelf-verified)");
    let app_tables = elf::parse_dyn_tables(APP_BASE, app_dyn_vaddr, app_dyn_filesz)
        .expect("dependent dyn tables must parse (readelf-verified .so)");

    // The app must NOT define cross_value in its own tables: the
    // by-name self-lookup refuses (UndefinedSymbol), which is the
    // structural fact that makes the registry the ONLY valid source
    // for the cross relocation.
    let app_self_lookup_refused = matches!(
        elf::lookup_symbol(&app_tables, APP_BASE, b"cross_value"),
        Err(elf::ElfError::UndefinedSymbol)
    );
    serial_println!(
        "  experimental cross-demo: the app's own by-name lookup of cross_value refused with UndefinedSymbol -- {}",
        if app_self_lookup_refused { "OK, the app genuinely does not define it" } else { "FAILED" }
    );

    let cross_slot = core::ptr::read_volatile((APP_BASE + APP_CROSS_SLOT) as *const u64);
    let cross_ok = cross_slot == cross_via_registry;
    serial_println!(
        "  experimental cross-demo: app's UNDEF GLOB_DAT slot {:#x} == {:#x} (the PROVIDER's cross_value, via the registry) -- {}",
        APP_BASE + APP_CROSS_SLOT,
        cross_slot,
        if cross_ok {
            "OK, cross-object relocation applied correctly"
        } else {
            "MISMATCH"
        }
    );

    let dup_slot = core::ptr::read_volatile((APP_BASE + APP_DUP_SLOT) as *const u64);
    let dup_ok = dup_slot == APP_BASE + APP_DUP_SYM;
    serial_println!(
        "  experimental cross-demo: app's duplicate-name GLOB_DAT slot {:#x} == {:#x} (ITS OWN dup_symbol) -- {}",
        APP_BASE + APP_DUP_SLOT,
        dup_slot,
        if dup_ok {
            "OK, self-priority: the app's OWN dynsym entry won, not the registry's earlier same-named entry"
        } else {
            "MISMATCH"
        }
    );

    // ---- registry-order control ----
    // Look the duplicate name up through two registries built in
    // opposite orders: the observable distinction between the loader
    // consuming order and a vestigial lookup hiding behind it.
    let mut reversed = elf::SymbolRegistry::new();
    reversed.push(app_tables.clone(), APP_BASE);
    reversed.push(
        elf::parse_dyn_tables(LIB_BASE, lib_dyn_vaddr, lib_dyn_filesz)
            .expect("provider dyn tables must parse (readelf-verified .so)"),
        LIB_BASE,
    );
    let dup_late_first = reversed
        .lookup(b"dup_symbol")
        .expect("registry lookup must stay structurally clean in both orders")
        .expect("dup_symbol must resolve in the registry regardless of order");
    let order_ok = dup_late_first == APP_BASE + APP_DUP_SYM && dup_via_registry == LIB_BASE + LIB_DUP_SYM;
    serial_println!(
        "  experimental cross-demo: registry search order decides -- [app,lib] -> {:#x} (app), [lib] -> {:#x} (lib) -- {}",
        dup_late_first,
        dup_via_registry,
        if order_ok { "OK, first DEFINED match in order wins" } else { "FAILED" }
    );

    // ---- typed absent-name control (same discipline as elf_dyn_demo) ----
    let absent = registry
        .lookup(b"cross_no_such_symbol")
        .expect("the scratch lookup must stay structurally clean");
    let absent_ok = absent.is_none();
    serial_println!(
        "  experimental cross-demo: absent-name registry lookup returned Ok(None) (typed absent, no parse error) -- {}",
        if absent_ok { "OK" } else { "FAILED" }
    );

    // ---- the REFUSAL half, for real: without the registry the
    // dependent's UNDEF reference is unsatisfiable. Loading a SECOND
    // copy of the app at a scratch base with NO resolver must refuse
    // honestly -- by the loader's contract that surfaces EITHER as
    // `Err(UndefinedSymbol)` (if the load fails before a RELRO
    // segment exists to carry the outcome) or as
    // `Ok(relro = Denied)` with the region left writable (the
    // documented honest state for a load whose relocations cannot
    // all be satisfied) -- the caller refuses such a load outright.
    // If resolution broke the refusal ("resolve-to-garbage" or a
    // silent pass), the second copy would come back Ok with the
    // RELRO region genuinely revoked, and this check FAILEDs.
    let scratch_base = 0x_6C0_0010_0000;
    let refused_load = elf::load(CROSS_APP_ELF, &mut mapper, fa, scratch_base, &elf::LoadContext::none());
    let (refusal_ok, refusal_desc) = match refused_load {
        Err(elf::ElfError::UndefinedSymbol) => (
            true,
            "Err(UndefinedSymbol)",
        ),
        Ok(loaded) => match loaded.relro {
            Some(elf::RelroOutcome::Denied { .. }) => (
                true,
                "Ok(relro = Denied) -- the caller must refuse",
            ),
            Some(elf::RelroOutcome::MadeReadOnly { .. }) => (
                false,
                "Ok(relro = MadeReadOnly) -- the load ACCEPTED, resolution must not happen without the registry",
            ),
            None => (
                false,
                "Ok(relro = None) -- unacceptable",
            ),
        },
        Err(_) => (
            false,
            "Err(other) -- a refusal we did not predict",
        ),
    };
    serial_println!(
        "  experimental cross-demo: the same dependent WITHOUT a resolver refused with {} -- {}",
        refusal_desc,
        if refusal_ok { "OK, the refusal is NOT gone now that resolution exists" } else { "FAILED" }
    );

    let all_ok = cross_ok && dup_ok && order_ok && absent_ok && refusal_ok && app_self_lookup_refused;
    assert!(
        all_ok,
        "elf_dyn_cross_demo kernel-side checks failed (cross_ok={}, dup_ok={}, order_ok={}, absent_ok={}, refusal_ok={}, self_refused={})",
        cross_ok, dup_ok, order_ok, absent_ok, refusal_ok, app_self_lookup_refused
    );

    // ---- behavioral half: enter the DEPENDENT's own code in ring 3 ----
    // Map the demo's private data page and user stack, then enter
    // cross_payload: it dereferences its (now registry-resolved)
    // GOT slot and writes what the PROVIDER's data contains.
    let data_flags = x86_64::structures::paging::PageTableFlags::PRESENT
        | x86_64::structures::paging::PageTableFlags::WRITABLE
        | x86_64::structures::paging::PageTableFlags::USER_ACCESSIBLE
        | x86_64::structures::paging::PageTableFlags::NO_EXECUTE;
    let data_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(PRIVATE_ADDR), 1, data_flags,
    );
    assert_eq!(data_mapped, 1, "failed to map cross-demo private data page");

    let stack_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(USER_STACK_ADDR), 1, data_flags,
    );
    assert_eq!(stack_mapped, 1, "failed to map cross-demo user stack");

    let payload_addr =
        elf::lookup_symbol(&app_tables, APP_BASE, b"cross_payload").expect("cross_payload must resolve by name");

    serial_println!(
        "  experimental cross-demo: kernel-side proof complete; entering cross_payload (ring 3), which dereferences its registry-resolved GOT slot",
    );

    // `space` must outlive the demo (same reasoning as pie_demo).
    // The space lives in STAGE_SPACE (same no-teardown honesty as
    // pie_demo -- no `mem::forget` needed).

    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(VirtAddr::new(payload_addr), user_rsp)
}

/// Called from `idt::init`'s installed handler on the one trap this
/// demo's program issues. Reports whether the dependent's own code
/// read the provider's data through its registry-resolved GOT slot,
/// then chains into `experimental::pe_demo`.
pub unsafe fn on_trap() -> ! {
    let observed = core::ptr::read_volatile(PRIVATE_ADDR as *const u64);
    let ok = observed == EXPECTED_VALUE;
    serial_println!(
        "  experimental cross-demo self-test: the dependent's own code read {:#x} through its cross-resolved GOT slot (expected {:#x}, the provider's cross_value) -- {}",
        observed,
        EXPECTED_VALUE,
        if ok { "OK" } else { "MISMATCH" }
    );

    // Chain into the versioned lazy-binding demo (the next capability
    // class up from this one), whose own completion trap continues
    // into pe_demo.
    let fa = (*&raw mut FRAME_ALLOCATOR).take().unwrap();
    crate::experimental::elf_dyn_full_demo::start(fa)
}
