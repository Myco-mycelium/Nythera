//! The full ELF dynamic-features demo: a real DEPENDENT .so LINKED
//! against a versioned, sonamed PROVIDER .so, exercising the whole
//! dependent contract at once --
//!
//! 1. **The DT_NEEDED / DT_SONAME dependency contract** — the
//!    dependent declares `DT_NEEDED crosslib.so.1` (recorded by
//!    `ld -shared dep.o lib.so` from the provider's real DT_SONAME).
//!    A load whose resolver scope cannot satisfy the contract is
//!    REFUSED with the typed `MissingDependency`
//!    (every boot, against an empty registry), while the
//!    correctly-registered load proceeds. Filesystem discovery of a
//!    NEEDED file remains future work (needs a storage surface —
//!    Phase 8 scope); what this closes is the CONTRACT between
//!    already-loaded objects.
//! 2. **Versioned symbol resolution** — `cross_value` is required
//!    by the dependent `\@VERS_1.0` (real DT_VERSYM + DT_VERNEED +
//!    VERNEED-aux machinery, not a convention). Resolution succeeds
//!    only against a provider whose real DT_VERDEF includes the
//!    required version: the typed `VersionNotProvided` refusal is
//!    proven against a same-SONAME provider declaring VERS_2.0 only
//!    (every boot), and the dependent's GOT slot holds the
//!    provider's resolved address.
//! 3. **Lazy PLT binding** — the dependent calls `cross_fn` through
//!    a real .plt stub whose `R_X86_64_JUMP_SLOT` entry the loader
//!    arms with a TRAP-BASED lazy stub (`push idx; int 0x88; add
//!    rsp,8; jmp *(slot)`, emitted into a loader-mapped executable
//!    page): the first call traps, `elf::resolve_lazy_entry`
//!    resolves it through the same registry + version contract at
//!    FIRST USE, the handler patches the slot, and every later call
//!    takes the direct path. Proven by the function's observable
//!    result together with the trap happening exactly ONCE across
//!    three calls. This is kernel-arbitrated lazy binding: the
//!    classic userland PLT0/_dl_runtime_resolve ABI (resolver-entry
//!    GOT slots, link_map cookies, argument-register spills) is
//!    userland dynamic-linker machinery and stays the documented
//!    boundary — the lazy SEMANTICS (bind on first use, not at
//!    load) are what arrive here.


use x86_64::structures::paging::PageTableFlags;
use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::elf::SymbolResolver;
use crate::experimental::{elf, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::serial_println;

/// The dedicated ring-3 trap vector for the demo's completion report
/// -- the next free slot after 0x88 (the lazy-bind service vector,
/// `elf::LAZY_BIND_VECTOR`, a shared service rather than a demo's
/// report line).
pub const TRAP_VECTOR: u8 = 0x89;

static CROSS_ALL_LIB_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_all_lib.elf");
static CROSS_ALL_LIB2_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_all_lib2.elf");
static CROSS_ALL_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_all.elf");

const LIB1_BASE: u64 = 0x_6C0_0000_0000;
const LIB2_BASE: u64 = 0x_6C0_0010_0000;
const DEP_BASE: u64 = 0x_6C0_0020_0000;
const TRAMPOLINE_PAGE: u64 = 0x_6C0_0021_0000;
const PRIVATE_ADDR: u64 = 0x_6C0_0030_0000; // matches prog_cross_all.s's immediate
const USER_STACK_ADDR: u64 = 0x_6C0_0032_0000;
const SCRATCH_BASE_A: u64 = 0x_6C0_0040_0000; // empty-registry refusal carrier
const SCRATCH_BASE_B: u64 = 0x_6C0_0050_0000; // wrong-version refusal carrier

/// The provider's cross_value; the dependent MUST observe it through
/// its version-checked GOT slot.
const EXPECTED_CROSS_VALUE: u64 = 0xACE5_F00D_D1CE_5EED;
/// cross_fn's result, observed on the first (lazy-bound) call and
/// the second (direct) call alike.
/// The observable result of the lazy-bound provider function.
/// `cross_fn` returns its result in `%eax` (its store is `mov
/// $0xFA571AE5, %eax; ret` and the dependent records `mov %eax, ...`),
/// so the check compares the 32-bit return width the ABI actually
/// delivers -- full-rax reads would include caller-leftover upper
/// bits that no callee maintains.
const EXPECTED_FN_RESULT: u64 = 0xFA571AE5;
const FN_RESULT_MASK: u64 = 0xFFFF_FFFF;

/// The dependent's own duplicate (self-priority control): its GOT
/// slot must hold THIS address, not the provider's same-named
/// export (readelf-pinned .dynsym entry 3, st_value 0x3008).
const DEP_DUP_ADDR: u64 = DEP_BASE + 0x3008;

const DEP_CROSS_SLOT: u64 = 0x2fe0; // readelf-pinned GLOB_DAT, in-RELRO
const DEP_DUP_SLOT: u64 = 0x2fd8; // readelf-pinned GLOB_DAT, in-RELRO
const DEP_PLT_SLOT: u64 = 0x3000; // readelf-pinned JUMP_SLOT, outside RELRO

const REQUIRED_VERSION: &[u8] = b"VERS_1.0";
const REQUIRED_FILE: &[u8] = b"crosslib.so.1";

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;
/// The demo's address space, stashed for the stage-two continuation
/// (the chain-partition rule; see pie_demo's notes).
static mut STAGE_SPACE: Option<AddressSpace> = None;
/// The resolver scope the dependent's first-use (lazy) traps resolve
/// against, cloned into a 'static slot because the SHARED
/// lazy_binder registration needs a reference that outlives `start`
/// (a call never returns; the registry's tables point into the
/// address space `start` built and left active for the whole
/// chain).
static mut LAZY_REGISTRY: Option<elf::SymbolRegistry> = None;

/// Boots the whole proof kernel-side, then enters `all_payload` in
/// ring 3. `on_plt_trap` services the lazy-bind trap (vector 0x88,
/// returning -- NOT chaining); `on_trap` (0x89) verifies the
/// program's answer and chains into `experimental::pe_demo`.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    let space = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build elf_dyn_full_demo address space"));
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

    // ---- provider 1: the loadable, versioned, sonamed object ----
    elf::load(
        CROSS_ALL_LIB_ELF,
        &mut mapper,
        fa,
        LIB1_BASE,
        &elf::LoadContext::none(),
    )
    .unwrap_or_else(|e| panic!("failed to load versioned provider: {:?}", e));
    let (lib1_dyn_vaddr, lib1_dyn_filesz) = elf::pt_dynamic_range(CROSS_ALL_LIB_ELF)
        .expect("provider must carry a PT_DYNAMIC segment (readelf-verified)");
    let lib1_tables = elf::parse_dyn_tables(LIB1_BASE, lib1_dyn_vaddr, lib1_dyn_filesz)
        .expect("provider dyn tables must parse (readelf-verified)");
    assert!(
        lib1_tables.soname() == Some(REQUIRED_FILE),
        "provider 1's DT_SONAME must be crosslib.so.1 (readelf-pinned)"
    );
    let mut registry = elf::SymbolRegistry::new();
    registry.push(lib1_tables.clone(), LIB1_BASE);
    serial_println!(
        "  experimental full-demo: provider loaded; DT_SONAME fact (crosslib.so.1) validated -- OK"
    );

    // Version 2 (SAME SONAME, different DT_VERDEF names): the
    // version-matching negative control.
    elf::load(
        CROSS_ALL_LIB2_ELF,
        &mut mapper,
        fa,
        LIB2_BASE,
        &elf::LoadContext::none(),
    )
    .unwrap_or_else(|e| panic!("failed to load the V2 provider: {:?}", e));
    let (lib2_dyn_vaddr, lib2_dyn_filesz) = elf::pt_dynamic_range(CROSS_ALL_LIB2_ELF)
        .expect("provider 2 must carry a PT_DYNAMIC segment (readelf-verified)");
    let lib2_tables = elf::parse_dyn_tables(LIB2_BASE, lib2_dyn_vaddr, lib2_dyn_filesz)
        .expect("provider 2 dyn tables must parse");
    let mut registry2 = elf::SymbolRegistry::new();
    registry2.push(lib2_tables, LIB2_BASE);

    // ---- typed probe: the version contract is ENFORCED ----
    let wrong_version = registry2
        .resolve_versioned(b"cross_value", Some((REQUIRED_VERSION, REQUIRED_FILE)));
    let neg_ok = matches!(
        wrong_version,
        Err(elf::ElfError::VersionNotProvided)
    );
    serial_println!(
        "  experimental full-demo: same-SONAME V2 provider refuses the VERS_1.0 requirement with VersionNotProvided -- {}",
        if neg_ok { "OK, version matching is enforced; the name alone is NOT enough" } else { "FAILED" }
    );

    // ---- load the dependent: registry + lazy-armed trampoline page ----
    let via_version = registry
        .resolve_versioned(b"cross_value", Some((REQUIRED_VERSION, REQUIRED_FILE)))
        .expect("version-contract probe must stay structurally clean")
        .expect("cross_value must resolve WITH the version contract");
    let load_ctx = elf::LoadContext {
        resolver: Some(&registry),
        lazy_plt_page: Some(VirtAddr::new(TRAMPOLINE_PAGE)),
        own_plt_resolver_page: None,
    };
    let loaded_dep = elf::load(CROSS_ALL_ELF, &mut mapper, fa, DEP_BASE, &load_ctx)
        .unwrap_or_else(|e| panic!("failed to load the versioned dependent: {:?}", e));
    match loaded_dep.relro {
        Some(elf::RelroOutcome::MadeReadOnly { start, end }) => {
            serial_println!(
                "  experimental full-demo: dependent's GNU_RELRO region {:#x}..{:#x} revoked read-only after the version-checked grant/relocate -- OK",
                start, end
            );
        }
        Some(elf::RelroOutcome::Denied { .. }) => {
            panic!("the correctly-provisioned dependent must load without a DENIED region")
        }
        None => panic!("a dependent linked against a versioned provider always carries GNU_RELRO"),
    }

    // ---- kernel-side slot facts (all readelf-pinned) ----
    let (dep_dyn_vaddr, dep_dyn_filesz) = elf::pt_dynamic_range(CROSS_ALL_ELF)
        .expect("dependent must carry a PT_DYNAMIC segment (readelf-verified)");
    let dep_tables = elf::parse_dyn_tables(DEP_BASE, dep_dyn_vaddr, dep_dyn_filesz)
        .expect("dependent dyn tables must parse");
    let cross_slot = core::ptr::read_volatile((DEP_BASE + DEP_CROSS_SLOT) as *const u64);
    let cross_ok = cross_slot == via_version;
    serial_println!(
        "  experimental full-demo: dependent's versioned GLOB_DAT slot {:#x} == {:#x} (provider's cross_value, version-checked) -- {}",
        DEP_BASE + DEP_CROSS_SLOT,
        cross_slot,
        if cross_ok { "OK" } else { "MISMATCH" }
    );
    let dup_slot = core::ptr::read_volatile((DEP_BASE + DEP_DUP_SLOT) as *const u64);
    let dup_ok = dup_slot == DEP_DUP_ADDR;
    serial_println!(
        "  experimental full-demo: dependent's duplicate-name slot {:#x} == {:#x} (its OWN dup_symbol) -- {}",
        DEP_BASE + DEP_DUP_SLOT,
        dup_slot,
        if dup_ok { "OK, self-priority held under the versioned provider too" } else { "MISMATCH" }
    );
    let plt_slot = core::ptr::read_volatile((DEP_BASE + DEP_PLT_SLOT) as *const u64);
    let plt_armed_ok = plt_slot == TRAMPOLINE_PAGE;
    serial_println!(
        "  experimental full-demo: lazy JUMP_SLOT slot {:#x} == {:#x} (trap stub armed; NOT eagerly resolved) -- {}",
        DEP_BASE + DEP_PLT_SLOT,
        plt_slot,
        if plt_armed_ok { "OK" } else { "MISMATCH" }
    );

    // ---- typed refusals against scratch loads ----
    // (a) an EMPTY registry: the DT_NEEDED contract cannot be met ->
    // MissingDependency, BEFORE any relocation work.
    let empty = elf::SymbolRegistry::new();
    let refused_ctx = elf::LoadContext {
        resolver: Some(&empty),
        lazy_plt_page: None,
        own_plt_resolver_page: None,
    };
    let refused_a = elf::load(CROSS_ALL_ELF, &mut mapper, fa, SCRATCH_BASE_A, &refused_ctx);
    let refusal_a_ok = matches!(refused_a, Err(elf::ElfError::MissingDependency));
    serial_println!(
        "  experimental full-demo: empty-registry dependent refused with MissingDependency -- {}",
        if refusal_a_ok { "OK, the declared dependency contract is enforced" } else { "FAILED" }
    );

    // (b) the wrong-VERSION registry: relocation's version contract
    // fails -> Either the typed error or the RELRO denial, the
    // honest states for an in-region unsatisfiable entry.
    let refused_ctx_b = elf::LoadContext {
        resolver: Some(&registry2),
        lazy_plt_page: None,
        own_plt_resolver_page: None,
    };
    let refused_b = elf::load(CROSS_ALL_ELF, &mut mapper, fa, SCRATCH_BASE_B, &refused_ctx_b);
    let refusal_b_ok = matches!(
        refused_b,
        Ok(elf::LoadedElf {
            relro: Some(elf::RelroOutcome::Denied { .. }),
            ..
        }) | Err(elf::ElfError::VersionNotProvided)
    );
    serial_println!(
        "  experimental full-demo: V2-registry dependent refused (denied region or typed version error) -- {}",
        if refusal_b_ok { "OK, the version contract refuses end to end" } else { "FAILED" }
    );

    let all_ok = cross_ok
        && dup_ok
        && plt_armed_ok
        && refusal_a_ok
        && refusal_b_ok
        && neg_ok;
    assert!(
        all_ok,
        "elf_dyn_full_demo kernel-side checks failed (cross_ok={}, dup_ok={}, plt_ok={}, refusal_a_ok={}, refusal_b_ok={}, version_neg_ok={})",
        cross_ok, dup_ok, plt_armed_ok, refusal_a_ok, refusal_b_ok, neg_ok
    );

    // ---- behavioral half: enter the dependent's own code in ring 3 ----
    let data_flags = PageTableFlags::PRESENT
        | PageTableFlags::WRITABLE
        | PageTableFlags::USER_ACCESSIBLE
        | PageTableFlags::NO_EXECUTE;
    let data_mapped =
        crate::memory::paging::map_range(&mut mapper, fa, VirtAddr::new(PRIVATE_ADDR), 1, data_flags);
    assert_eq!(data_mapped, 1, "failed to map full-demo private data page");
    let stack_mapped =
        crate::memory::paging::map_range(&mut mapper, fa, VirtAddr::new(USER_STACK_ADDR), 1, data_flags);
    assert_eq!(stack_mapped, 1, "failed to map full-demo user stack");

    let payload_addr =
        elf::lookup_symbol(&dep_tables, DEP_BASE, b"all_payload").expect("all_payload must resolve by name");

    // The service's EMPTY-registry refusal branch, proven at the one
    // point of the boot where the registry is genuinely empty: THIS
    // demo performs the chain's first `lazy_binder::register` call,
    // so immediately before it a probe MUST come back as the typed
    // `ServiceEmpty` -- deliberately NOT the `UnregisteredTrampoline`
    // the same probe serves once entries exist (the deps demo's
    // control, against the SAME page shape, after this registration).
    let service_was_empty = crate::experimental::lazy_binder::registered_count() == 0;
    let empty_outcome = unsafe {
        crate::experimental::lazy_binder::probe_unregistered(TRAMPOLINE_PAGE)
    };
    let empty_refusal_ok = matches!(
        empty_outcome,
        Err(crate::experimental::lazy_binder::LazyBindRefusal::ServiceEmpty)
    );
    assert!(
        service_was_empty && empty_refusal_ok,
        "lazy-bind service must refuse with the typed ServiceEmpty while nothing is registered (count={}, outcome={:?})",
        crate::experimental::lazy_binder::registered_count(),
        empty_outcome
    );
    serial_println!(
        "  experimental full-demo: lazy-bind service probe of {:#x} while the registry was EMPTY refused with the typed ServiceEmpty -- OK, the empty branch proven kernel-side before the chain's first registration",
        TRAMPOLINE_PAGE
    );

    // Register THIS boot lazy-binding service instead of demo-local
    // statics -- before the first call, which can trap within
    // microseconds of entry. The dispatcher identifies this object by
    // its trampoline page, exactly like a second lazy dependent
    // elsewhere in the chain identifies its own.
    let registry_for_service = registry.clone();
    LAZY_REGISTRY = Some(registry_for_service);
    unsafe {
        let reg_ref: &'static elf::SymbolRegistry =
            (&*(&raw const LAZY_REGISTRY)).as_ref().unwrap();
        crate::experimental::lazy_binder::register(
            TRAMPOLINE_PAGE,
            dep_tables.clone(),
            DEP_BASE,
            reg_ref,
        );
    }

    // The space lives in STAGE_SPACE (same no-teardown honesty as
    // pie_demo -- no `mem::forget` needed).
    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(VirtAddr::new(payload_addr), user_rsp)
}

/// The completion trap: verifies the dependent's recorded facts --
/// the cross_value it read through its versioned GOT slot, the
/// cross_fn results (first lazy, later direct), and exactly ONE lazy
/// bind across the three calls -- then chains into
/// `experimental::pe_demo`.
///
/// # Safety
/// Called from the (ring-3-privileged) IDT handler; `start` must
/// have armed the demo.
pub unsafe fn on_trap() -> ! {
    let cross_observed = core::ptr::read_volatile(PRIVATE_ADDR as *const u64);
    let fn_first = core::ptr::read_volatile((PRIVATE_ADDR + 8) as *const u64);
    let fn_second = core::ptr::read_volatile((PRIVATE_ADDR + 16) as *const u64);
    let count = crate::experimental::lazy_binder::entry_count(TRAMPOLINE_PAGE);

    let cross_ok = cross_observed == EXPECTED_CROSS_VALUE;
    let fn_ok = (fn_first & FN_RESULT_MASK) == EXPECTED_FN_RESULT
        && (fn_second & FN_RESULT_MASK) == EXPECTED_FN_RESULT;
    let once = count == 1;
    serial_println!(
        "  experimental full-demo self-test: cross_value via versioned slot = {:#x} (expected {:#x}) -- {}",
        cross_observed,
        EXPECTED_CROSS_VALUE,
        if cross_ok { "OK" } else { "MISMATCH" }
    );
    serial_println!(
        "  experimental full-demo self-test: cross_fn results (eax) {:#x} / {:#x} (expected {:#x} both; first via lazy bind, later direct) -- {}",
        fn_first & FN_RESULT_MASK,
        fn_second & FN_RESULT_MASK,
        EXPECTED_FN_RESULT,
        if fn_ok { "OK" } else { "MISMATCH" }
    );
    serial_println!(
        "  experimental full-demo self-test: lazy-bind trap fired {} time(s) across 3 calls (exactly 1 required) -- {}",
        count,
        if once { "OK, binding is lazy; later calls are direct" } else { "FAILED" }
    );
    serial_println!(
        "  experimental full-demo: shared lazy-bind service: {} object(s) registered so far, {} bind(s) served through ONE vector (0x88) this boot -- {}",
        crate::experimental::lazy_binder::registered_count(),
        crate::experimental::lazy_binder::total_count(),
        if crate::experimental::lazy_binder::registered_count() == 1 {
            "OK, this dependent binds through the service, not a demo static"
        } else {
            "INFO (a later demo in the chain registers its own dependent under the same service)"
        }
    );
    assert!(
        cross_ok && fn_ok && once,
        "elf_dyn_full_demo behavioral checks failed (cross_ok={}, fn_ok={}, once={})",
        cross_ok, fn_ok, once
    );

    let fa = (*&raw mut FRAME_ALLOCATOR).take().unwrap();
    crate::experimental::elf_dyn_deps_demo::start(fa)
}
