//! The OWN-.plt GNU lazy-binding protocol demo: dependent .so's whose
//! first PLT calls flow through the REAL linker machinery -- not the
//! kernel's synthetic trap stubs -- under the GNU dynamic-linker
//! protocol this kernel now arms:
//!
//! 1. **The objects' OWN .plt/.got.plt is the machinery.** `elf::load`
//!    (with `own_plt_resolver_page`) applies the GNU lazy JUMP_SLOT
//!    relocation exactly as a real dynamic linker does: every slot
//!    gets the linker's own baked initial value (`plt_stub+6`) with
//!    the load bias added -- nothing else per entry, no resolution.
//!    The loader arms `.got.plt`[1] (a per-object link-map id -- this
//!    kernel's integer equivalent of the GNU `struct link_map*`
//!    cookie) and `.got.plt`[2] (the user-callable resolver thunk),
//!    inside the RELRO grant window, exactly the order GNU ld uses.
//! 2. **The spill is the real protocol shape.** PLT1's `push $idx`
//!    plus PLT0's `push GOT[1]` put (cookie, index, caller ret) on
//!    the user stack -- the resolver identifies the object by the
//!    COOKIE (the registry-owned link-map id), not by any trampoline
//!    page. The kernel-mapped thunk is deliberately not a
//!    kernel pointer handed through writable user data; it is an
//!    integer the registry owns.
//! 3. **First-use resolution + the guided return.** The first
//!    `call cross_fn` traps 0x8B with that spill; the service
//!    resolves through the same registry + version contract as every
//!    other path, patches the real GOT slot, and then GUIDES the
//!    return the way GNU `_dl_runtime_resolve` ends: it discards the
//!    two callee-side spilled words and continues at the RESOLVED
//!    CALLEE, so the very first call executes the function and its
//!    own `ret` returns to the caller. Calls two and three take the
//!    direct path through the patched slot. Exactly ONE bind across
//!    three calls proves both halves: laziness AND the direct-entry
//!    first call.
//! 4. **A service, not a demo: TWO GNU cookies, ONE gate.** The same
//!    dependent artifact is loaded a SECOND time, at its own base
//!    with its own thunk page, and therefore with its own
//!    monotonically-allocated link-map id. Both instances run all
//!    three `cross_fn` calls in ring 3, each delivering its first-use
//!    bind through vector 0x8B; kernel-side, each cookie's count is
//!    EXACTLY 1 -- two distinct GNU objects, each bound lazily and
//!    exactly once, dispatched by their cookies through the same
//!    resolver the `int 0x88` trap-stub path shares.
//! 5. **Multi-entry: TWO first-use binds under ONE cookie.** The
//!    THIRD instance is a genuinely different artifact
//!    (userprog_multi.elf) linked against BOTH providers, whose
//!    `.rela.plt` carries TWO R_X86_64_JUMP_SLOT entries (fn_a from
//!    pltprov.so.1, fn_b). One cookie, TWO independent first-use
//!    binds: each entry resolves by ITS OWN index, both slots patch
//!    to their respective resolved addresses, and the cookie's bind
//!    count lands EXACTLY 2 -- while the registry + version contract
//!    rules stay byte-for-byte the ones every other path uses.
//!
//! Honest scope, stated at the doc boundary: the resolution itself
//! stays kernel-arbitrated (`resolve_lazy_entry` through the
//! registry) -- a ring-3-executable resolver with its own private
//! hash tables remains the GNU-`ld.so` mechanism this kernel does
//! not model. What arrived here is the GNU WIRE protocol: the
//! object's own linker .plt/.got.plt, the link-map-cookie dispatch,
//! the argument spill, and the direct-into-callee first call.
//!
//! See `crate::experimental`'s banner -- not NPS-002/003 conformance.

use x86_64::structures::paging::OffsetPageTable;
use x86_64::structures::paging::PageTableFlags;
use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::serial_println;
use crate::experimental::{elf, usermode};

/// The ABI-correct completion report vector for this demo: the next
/// free ring-3 gate after 0x8B (the own-.plt resolver gate). The
/// resolver traps never accumulate here -- they guide their own
/// returns; 0x8C is each payload's one report trap (three payloads,
/// three reports).
pub const TRAP_VECTOR: u8 = 0x8C;

static CROSS_ALL_LIB_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_all_lib.elf");
static PLTPROV_ELF: &[u8] = include_bytes!("../../userprog/userprog_pltprov.elf");
static PLT_RES_ELF: &[u8] = include_bytes!("../../userprog/userprog_plt_res.elf");
static PLT_MULTI_ELF: &[u8] = include_bytes!("../../userprog/userprog_multi.elf");
static PLTLESS_ELF: &[u8] = include_bytes!("../../userprog/userprog_pltless.elf");

const LIB_BASE: u64 = 0x_6D0_0066_0000; // provider A (the demo's own instance)
const PROVIDER3_BASE: u64 = 0x_6D0_0077_0000; // provider B: pltprov.so.1 (fn_a / fn_b)
const DEP1_BASE: u64 = 0x_6D0_0070_0000; // GNU-protocol dependent, instance 1
const THUNK1_PAGE: u64 = 0x_6D0_0071_0000; // instance 1's resolver thunk (PT_LOAD-disjoint)
const DEP2_BASE: u64 = 0x_6D0_0074_0000; // GNU-protocol dependent, instance 2
const THUNK2_PAGE: u64 = 0x_6D0_0075_0000; // instance 2's resolver thunk (PT_LOAD-disjoint)
const DEP3_BASE: u64 = 0x_6D0_0078_0000; // MULTI-ENTRY GNU dependent (instance 3)
const THUNK3_PAGE: u64 = 0x_6D0_0079_0000; // instance 3's resolver thunk (PT_LOAD-disjoint)
const PRIVATE_ADDR: u64 = 0x_6D0_0072_0000; // matches prog_plt_res.s's immediate (shared)
const PRIVATE3_ADDR: u64 = 0x_6D0_0076_0000; // matches prog_multi_plt.s's immediate
const USER_STACK_ADDR: u64 = 0x_6D0_0073_0000;
// The PLT-LESS boundary proofs' two carriers (inside the 0x6D0_0070_
// ..0x6D0_0080_0000 gate window): a deliberately-refused armed load,
// and the same file's eager load.
const PLTLESS_REFUSAL_BASE: u64 = 0x_6D0_007A_0000;
const PLTLESS_REFUSAL_THUNK: u64 = 0x_6D0_007C_0000; // refused before any arming
const PLTLESS_EAGER_BASE: u64 = 0x_6D0_007B_0000;
/// readelf-pinned: the artifact's ONE .rela.dyn entry (GLOB_DAT
/// cross_value@VERS_1.0) targets slot 0x2fe0.
const PLTLESS_CROSS_SLOT: u64 = 0x2fe0;

/// A link-map id NO registered object owns: the GNU-path typed
/// refusal control (the twin of the trap-path unregistered-page
/// probe). Real ids hand out from 1; this one is invented.
const UNREGISTERED_LM: u64 = 0xBEEF_C0DE;

/// The private page's layout, written by `plt_payload` (instances 1
/// and 2 overwrite it with identical values, matching
/// prog_plt_res.s); PRIVATE3's layout by `multi_payload`
/// (prog_multi_plt.s): [0] fn_a, [8] fn_b, [16] fn_b (third call).
const EXPECTED_CROSS_VALUE: u64 = 0xACE5_F00D_D1CE_5EED;
const EXPECTED_FN_RESULT: u64 = 0xFA571AE5;
/// `fn_a` / `fn_b`'s results, and the provider marker's data value
/// (the right-file-at-right-base pin for the pltprov load).
const EXPECTED_FN_A: u64 = 0xA11C_E0DE;
const EXPECTED_FN_B: u64 = 0xBB0B_5E11;
const FN_RESULT_MASK: u64 = 0xFFFF_FFFF;
const EXPECTED_PROV_MARKER: u64 = 0x0E11_CEDE_1E1E_1E1E;

/// readelf-pinned facts (checked in the CI layout gate):
///   DT_PLTGOT = 0x2fe8 in BOTH embedded GNU artifacts, the
///   dependent's linker-baked initial JUMP_SLOT values are the
///   matching .plt stub's `push` half (plt_stub+6), and
///   userprog_multi.elf's `.rela.plt` carries exactly TWO entries
///   (48 bytes / RELA = 24 each), index 0 = fn_b, index 1 = fn_a.
const PLTGOT_VADDR: u64 = 0x2fe8;
const GNU_INITIAL_SLOT: u64 = 0x1016;
const GNU_INITIAL_MULTI: [u64; 2] = [0x1016, 0x1026];

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;
/// The demo's address space, stashed for the stage-two continuation
/// (the chain-partition rule; see pie_demo's notes).
static mut STAGE_SPACE: Option<AddressSpace> = None;
/// The resolver scope, cloned into a 'static slot because the SHARED
/// registry service needs a reference that outlives `start` (same
/// pattern as `elf_dyn_full_demo`).
static mut LAZY_REGISTRY: Option<elf::SymbolRegistry> = None;

/// The three GNU instances' facts (filled at arm time, consumed by
/// `on_trap`): per instance its own_plt link-map id, its single-
/// entry runtime JUMP_SLOT slot address (instances 1-2), and --
/// instance 3's TWO per-index slot addresses / expected resolutions.
static mut LM_IDS: [u64; 3] = [0, 0, 0];
static mut SLOT_ADDRS: [u64; 3] = [0, 0, 0];
static mut EXPECTED_RESOLVED: u64 = 0;
static mut MULTI_SLOTS: [u64; 2] = [0, 0];
static mut MULTI_EXPECTED: [u64; 2] = [0, 0];
/// The boundary's eager-resolution expectation: provider A's
/// cross_value address (stashed at stage-two time).
static mut EXPECTED_CROSS_ADDR: u64 = 0;
/// Which payload's report `on_trap` is servicing (1, then 2, then 3).
static mut INSTANCE: u8 = 1;

/// Boots the whole proof kernel-side, then enters `plt_payload` in
/// ring 3. `on_trap` (0x8C) verifies the program's answer, chains
/// through the remaining GNU instances, and finally chains into
/// `experimental::pe_demo`.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    let space = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build elf_dyn_plt_demo address space"));
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

    // ---- provider A: the versioned, sonamed object (own instance) ----
    elf::load(
        CROSS_ALL_LIB_ELF,
        &mut mapper,
        fa,
        LIB_BASE,
        &elf::LoadContext::none(),
    )
    .unwrap_or_else(|e| panic!("failed to load the provider: {:?}", e));
    let (lib_dyn_vaddr, lib_dyn_filesz) = elf::pt_dynamic_range(CROSS_ALL_LIB_ELF)
        .expect("provider must carry a PT_DYNAMIC segment (readelf-verified)");
    let lib_tables = elf::parse_dyn_tables(LIB_BASE, lib_dyn_vaddr, lib_dyn_filesz)
        .expect("provider dyn tables must parse (readelf-verified)");

    // The provider's cross_fn runtime address: what the first-use
    // resolution must produce and what every patched slot must hold.
    let expected_resolved =
        elf::lookup_symbol(&lib_tables, LIB_BASE, b"cross_fn").expect("cross_fn must resolve by name");
    unsafe { EXPECTED_RESOLVED = expected_resolved };
    // Provider A's cross_value ADDRESS: the PLT-less boundary's
    // eager-GLOB_DAT expectation (stashed before the tables move
    // into the registry).
    let cross_addr =
        elf::lookup_symbol(&lib_tables, LIB_BASE, b"cross_value").expect("cross_value must resolve by name");
    unsafe { EXPECTED_CROSS_ADDR = cross_addr };

    // ---- provider B (provider3): the MULTI-ENTRY pair's provider ----
    elf::load(
        PLTPROV_ELF,
        &mut mapper,
        fa,
        PROVIDER3_BASE,
        &elf::LoadContext::none(),
    )
    .unwrap_or_else(|e| panic!("failed to load pltprov provider: {:?}", e));
    let (p3_dyn_vaddr, p3_dyn_filesz) = elf::pt_dynamic_range(PLTPROV_ELF)
        .expect("provider3 must carry a PT_DYNAMIC segment (readelf-verified)");
    let p3_tables = elf::parse_dyn_tables(PROVIDER3_BASE, p3_dyn_vaddr, p3_dyn_filesz)
        .expect("provider3 dyn tables must parse (readelf-verified)");
    assert!(
        p3_tables.soname() == Some(b"pltprov.so.1" as &[u8]),
        "provider3's DT_SONAME must be pltprov.so.1 (readelf-pinned)"
    );
    // Right-file-at-right-base pin: the marker's D A T A read back
    // through its resolved address.
    let marker_addr =
        elf::lookup_symbol(&p3_tables, PROVIDER3_BASE, b"prov_marker").expect("prov_marker must resolve");
    let marker_val = unsafe { core::ptr::read_volatile(marker_addr as *const u64) };
    let pinned = marker_val == EXPECTED_PROV_MARKER;
    serial_println!(
        "  experimental plt-demo: provider3 loaded at {:#x}: DT_SONAME pltprov.so.1 AND its prov_marker data reads back {:#x} -- {}",
        PROVIDER3_BASE,
        marker_val,
        if pinned { "OK, the RIGHT file at the RIGHT base" } else { "FAILED" }
    );
    assert!(pinned, "provider3 marker pin failed");

    // ---- one registry, both providers: the shared scope ----
    let mut registry = elf::SymbolRegistry::new();
    registry.push(lib_tables, LIB_BASE);
    registry.push(p3_tables, PROVIDER3_BASE);

    // ---- the GNU-path typed refusal control, FIRST ----
    let refused = unsafe { crate::experimental::lazy_binder::probe_unregistered_lm(UNREGISTERED_LM) };
    let refusal_ok = matches!(
        refused,
        Err(crate::experimental::lazy_binder::LazyBindRefusal::UnregisteredLinkMap { lm }) if lm == UNREGISTERED_LM
    );
    serial_println!(
        "  experimental plt-demo: registry probe of link-map id {:#x} (no GNU object registered yet) refused with the typed UnregisteredLinkMap carrying THAT id -- {}",
        UNREGISTERED_LM,
        if refusal_ok {
            "OK, an unregistered cookie is refused, never guessed"
        } else {
            "FAILED"
        }
    );
    assert!(refusal_ok, "the GNU-path probe must refuse with the typed UnregisteredLinkMap before any GNU registration");

    // The 'static registry slot the SHARED lazy_binder service needs:
    // cloned into LAZY_REGISTRY exactly once, before ANY GNU payload
    // runs -- all three instances' LazyEntries (and every payload's
    // scope) point at the same freed-hazard-free static thereafter.
    let registry_for_service = registry.clone();
    unsafe { LAZY_REGISTRY = Some(registry_for_service) };
    let reg_static: &'static elf::SymbolRegistry =
        unsafe { (*&raw const LAZY_REGISTRY).as_ref().unwrap() };

    // ---- factor the three instances apart in the demo's own lines ----
    let mut mapper = space.mapper();
    let fa = unsafe { (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap() };
    unsafe { INSTANCE = 1 };
    arm_and_enter(
        &mut mapper,
        fa,
        PLT_RES_ELF,
        DEP1_BASE,
        THUNK1_PAGE,
        reg_static,
        0,
        3,
        &[GNU_INITIAL_SLOT],
    )
}

/// Arms ONE GNU-protocol dependent instance (load + kernel-side GNU
/// facts + shared-service registration for EVERY .rela.plt entry the
/// artifact carries), asserts every fact it can check before any
/// user code runs, then enters the artifact's `plt_payload`/
/// `multi_payload` in ring 3. The `expected_objects` number is the
/// registry size the registration lands INTO (3 after the trap-stub
/// demos' two + our first; 4 after our second; 5 after the
/// multi-entry third).
///
/// # Safety
/// Mapper/fa must be the demo's own active space's; called at most
/// three times per boot (the two GNU instances + the multi-entry
/// artifact), each before its payload.
unsafe fn arm_and_enter(
    mapper: &mut OffsetPageTable<'static>,
    fa: &mut BumpFrameAllocator,
    file: &'static [u8],
    dep_base: u64,
    thunk_page: u64,
    reg_static: &'static elf::SymbolRegistry,
    slot_idx: usize,
    expected_objects: usize,
    gnu_initial: &[u64],
) -> ! {
    let load_ctx = elf::LoadContext {
        resolver: Some(reg_static),
        lazy_plt_page: None,
        own_plt_resolver_page: Some(VirtAddr::new(thunk_page)),
    };
    let loaded = elf::load(file, mapper, fa, dep_base, &load_ctx)
        .unwrap_or_else(|e| panic!("failed to load own-.plt dependent instance {}: {:?}", slot_idx + 1, e));
    let (dep_dyn_vaddr, dep_dyn_filesz) = elf::pt_dynamic_range(file)
        .expect("dependent must carry a PT_DYNAMIC segment (readelf-verified)");
    let dep_tables = elf::parse_dyn_tables(dep_base, dep_dyn_vaddr, dep_dyn_filesz)
        .expect("dependent dyn tables must parse");

    // ---- the arming is the GNU pair, and ONLY it ----
    let Some(own_plt) = loaded.own_plt else {
        panic!("the own-.plt dependent instance {} must report its arming facts", slot_idx + 1);
    };
    let got1 = unsafe { core::ptr::read_volatile((own_plt.pltgot + 8) as *const u64) };
    let got2 = unsafe { core::ptr::read_volatile((own_plt.pltgot + 16) as *const u64) };
    let arming_ok =
        got1 == own_plt.lm_id && got2 == thunk_page && own_plt.pltgot == dep_base + PLTGOT_VADDR;
    serial_println!(
        "  experimental plt-demo: instance {} own-.plt armed on THE OBJECT's own .got.plt ({:#x}): GOT[1] = link-map id {:#x}, GOT[2] = resolver thunk {:#x} ({} .rela.plt entries) -- {}",
        slot_idx + 1,
        own_plt.pltgot,
        got1,
        got2,
        gnu_initial.len(),
        if arming_ok { "OK, the GNU resolver pointers are in place (JUMP_SLOT slots touched only by the GNU lazy bias relocation)" } else { "FAILED" }
    );
    assert!(arming_ok, "instance {} GNU arming facts failed", slot_idx + 1);

    // ---- every .rela.plt entry: the GNU INITIAL state survived, the
    //      per-index resolution expectation computed up front ----
    let mut resolved_at = [0u64; 2];
    let mut slot_at = [0u64; 2];
    for (i, _initial) in gnu_initial.iter().enumerate() {
        let slot_addr = unsafe { elf::lazy_slot_for(&dep_tables, dep_base, i) }
            .unwrap_or_else(|e| panic!("instance {}: lazy slot {} must compute: {:?}", slot_idx + 1, i, e));
        let slot_before = unsafe { core::ptr::read_volatile(slot_addr as *const u64) };
        let initial_ok = slot_before == dep_base + gnu_initial[i] && slot_before != 0;
        let expected = unsafe { elf::resolve_lazy_entry(&dep_tables, dep_base, i, Some(reg_static)) }
            .unwrap_or_else(|e| panic!("instance {}: probe resolution of idx {} must succeed: {:?}", slot_idx + 1, i, e));
        let differs = initial_ok && slot_before != expected;
        serial_println!(
            "  experimental plt-demo: instance {} JUMP_SLOT idx {} slot {:#x} == {:#x} pre-run (the LINKER's own plt+6 initial value with the GNU lazy relocation bias applied; not resolved; expected live value {:#x}) -- {}",
            slot_idx + 1,
            i,
            slot_addr,
            slot_before,
            expected,
            if differs { "OK" } else { "FAILED" }
        );
        assert!(differs, "instance {} GNU initial-state fact failed for idx {}", slot_idx + 1, i);
        resolved_at[i] = expected;
        slot_at[i] = slot_addr;
        if slot_idx == 2 {
            // MULTI-ENTRY instance: store BOTH per-index facts.
            unsafe { MULTI_SLOTS[i] = slot_addr };
            unsafe { MULTI_EXPECTED[i] = expected };
        } else if i == 0 {
            // single-entry instances: plain per-instance store.
            unsafe { SLOT_ADDRS[slot_idx] = slot_addr };
            unsafe { EXPECTED_RESOLVED = expected };
        }
    }

    // ---- register for the GNU-protocol service BEFORE first use ----
    // (the 'static scope reference came in from the caller -- stage
    // two set LAZY_REGISTRY once; see its arm-before-first-use note)
    unsafe {
        crate::experimental::lazy_binder::register_lm(
            own_plt.lm_id,
            dep_tables.clone(),
            dep_base,
            reg_static,
        );
    }
    let objects_now = crate::experimental::lazy_binder::registered_count();
    let registered_ok = objects_now == expected_objects;
    serial_println!(
        "  experimental plt-demo: instance {} registered under link-map id {:#x}; the shared service now holds {} object(s) -- {}",
        slot_idx + 1,
        own_plt.lm_id,
        objects_now,
        if registered_ok {
            "OK, the GNU path lives in the SAME registry the trap-stub path uses"
        } else {
            "FAILED"
        }
    );
    assert!(registered_ok, "instance {} service registration fact failed", slot_idx + 1);

    unsafe { LM_IDS[slot_idx] = own_plt.lm_id };

    // ---- data page(s) and the user stack: map once, reused after ----
    if slot_idx == 0 {
        let data_flags = PageTableFlags::PRESENT
            | PageTableFlags::WRITABLE
            | PageTableFlags::USER_ACCESSIBLE
            | PageTableFlags::NO_EXECUTE;
        let data_mapped =
            crate::memory::paging::map_range(mapper, fa, VirtAddr::new(PRIVATE_ADDR), 1, data_flags);
        assert_eq!(data_mapped, 1, "failed to map plt-demo private data page");
        let stack_mapped =
            crate::memory::paging::map_range(mapper, fa, VirtAddr::new(USER_STACK_ADDR), 1, data_flags);
        assert_eq!(
            stack_mapped, 1,
            "failed to map plt-demo user stack"
        );
    }
    if slot_idx == 2 {
        let data_flags = PageTableFlags::PRESENT
            | PageTableFlags::WRITABLE
            | PageTableFlags::USER_ACCESSIBLE
            | PageTableFlags::NO_EXECUTE;
        let multi_mapped =
            crate::memory::paging::map_range(mapper, fa, VirtAddr::new(PRIVATE3_ADDR), 1, data_flags);
        assert_eq!(multi_mapped, 1, "failed to map the multi-entry dependent's private page");
        unsafe { INSTANCE = 3 };
    }

    let _ = resolved_at;
    let _ = slot_at;
    let payload_addr = elf::lookup_symbol(&dep_tables, dep_base, b"plt_payload")
        .or_else(|_| elf::lookup_symbol(&dep_tables, dep_base, b"multi_payload"))
        .expect("the GNU dep's payload must resolve by name");
    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(VirtAddr::new(payload_addr), user_rsp)
}

/// The completion traps (one per GNU instance): instances 1-2 verify
/// their GNU facts (instance 1 proceeding to instance 2); instance 2
/// proceeds to arm/run the MULTI-ENTRY artifact; instance 3 verifies
/// BOTH its per-index slots' patches and its cookie's count of
/// EXACTLY 2, the registry totals, and chains into
/// `experimental::pe_demo`.
///
/// # Safety
/// Called from the (ring-3-privileged) IDT handler; `start` must have
/// armed the demo.
pub unsafe fn on_trap() -> ! {
    let which = unsafe { INSTANCE };

    if which <= 2 {
        // The behavioral values land on the shared private page (both
        // single-entry instances write identical values, matching
        // prog_plt_res.s).
        let cross_observed = unsafe { core::ptr::read_volatile(PRIVATE_ADDR as *const u64) };
        let fn_first = unsafe { core::ptr::read_volatile((PRIVATE_ADDR + 8) as *const u64) };
        let fn_second = unsafe { core::ptr::read_volatile((PRIVATE_ADDR + 16) as *const u64) };
        let fn_third = unsafe { core::ptr::read_volatile((PRIVATE_ADDR + 24) as *const u64) };
        let cross_ok = cross_observed == EXPECTED_CROSS_VALUE;
        let fn_ok = (fn_first & FN_RESULT_MASK) == EXPECTED_FN_RESULT
            && (fn_second & FN_RESULT_MASK) == EXPECTED_FN_RESULT
            && (fn_third & FN_RESULT_MASK) == EXPECTED_FN_RESULT;
        let slot_addr = unsafe { SLOT_ADDRS[(which - 1) as usize] };
        let slot_after = unsafe { core::ptr::read_volatile(slot_addr as *const u64) };
        let expected = unsafe { EXPECTED_RESOLVED };
        let patched = slot_after == expected;
        let count = crate::experimental::lazy_binder::entry_count_by_lm(unsafe { LM_IDS[(which - 1) as usize] });
        let bind_once = count == 1;

        serial_println!(
            "  experimental plt-demo self-test: instance {} -- cross_value = {:#x} (expected {:#x}) -- {}",
            which,
            cross_observed,
            EXPECTED_CROSS_VALUE,
            if cross_ok { "OK" } else { "MISMATCH" }
        );
        serial_println!(
            "  experimental plt-demo self-test: instance {} -- cross_fn results (eax) {:#x} / {:#x} / {:#x} (expected {:#x} all; call 1 entered the callee DIRECTLY via the resolver's guided return) -- {}",
            which,
            fn_first & FN_RESULT_MASK,
            fn_second & FN_RESULT_MASK,
            fn_third & FN_RESULT_MASK,
            EXPECTED_FN_RESULT,
            if fn_ok { "OK" } else { "MISMATCH" }
        );
        serial_println!(
            "  experimental plt-demo self-test: instance {} -- its REAL .got.plt slot {:#x} patched to the resolved cross_fn ({:#x}) -- {}",
            which,
            slot_addr,
            slot_after,
            if patched { "OK" } else { "FAILED" }
        );
        serial_println!(
            "  experimental plt-demo self-test: instance {} -- first-use binds for ITS OWN cookie {:#x}: {} across 3 calls (exactly 1 required) -- {}",
            which,
            unsafe { LM_IDS[(which - 1) as usize] },
            count,
            if bind_once { "OK, GNU lazy semantics on THIS object's own .plt machinery" } else { "FAILED" }
        );
        assert!(
            cross_ok && fn_ok && patched && bind_once,
            "elf_dyn_plt_demo behavioral checks failed for instance {} (cross={}, fn={}, patched={}, once={})",
            which, cross_ok, fn_ok, patched, bind_once
        );

        // Proceed to the next GNU half: instance 2 first (the
        // TWO-COOKIES-ONE-GATE fact), then the MULTI-ENTRY artifact
        // (kernel-side arming inside this handler is the same
        // established chain pattern the other demos use).
        unsafe { INSTANCE = which + 1 };
        let space = unsafe { (*&raw mut STAGE_SPACE).as_mut().expect("stage space missing") };
        let mut mapper = space.mapper();
        let fa = unsafe { (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap() };
        let reg_static: &'static elf::SymbolRegistry = unsafe {
            (&*(&raw const LAZY_REGISTRY)).as_ref().expect("demo registry missing")
        };
        if which == 1 {
            arm_and_enter(
                &mut mapper,
                fa,
                PLT_RES_ELF,
                DEP2_BASE,
                THUNK2_PAGE,
                reg_static,
                1,
                4,
                &[GNU_INITIAL_SLOT],
            )
        }
        arm_and_enter(
            &mut mapper,
            fa,
            PLT_MULTI_ELF,
            DEP3_BASE,
            THUNK3_PAGE,
            reg_static,
            2,
            5,
            &GNU_INITIAL_MULTI,
        )
    }

    // ---- instance 3: the MULTI-ENTRY GNU fact ----
    let fa_out = unsafe { core::ptr::read_volatile(PRIVATE3_ADDR as *const u64) };
    let fb_out = unsafe { core::ptr::read_volatile((PRIVATE3_ADDR + 8) as *const u64) };
    let fb_out2 = unsafe { core::ptr::read_volatile((PRIVATE3_ADDR + 16) as *const u64) };
    let a_ok = (fa_out & FN_RESULT_MASK) == EXPECTED_FN_A;
    let b_ok = (fb_out & FN_RESULT_MASK) == EXPECTED_FN_B
        && (fb_out2 & FN_RESULT_MASK) == EXPECTED_FN_B;
    serial_println!(
        "  experimental plt-demo self-test: instance 3 (multi-entry) -- fn_a = {:#x} (expected {:#x}), fn_b = {:#x} (expected {:#x}, TWO calls, second direct) -- {}",
        fa_out & FN_RESULT_MASK,
        EXPECTED_FN_A,
        fb_out & FN_RESULT_MASK,
        EXPECTED_FN_B,
        if a_ok && b_ok { "OK" } else { "MISMATCH" }
    );

    // Per-index patches: each slot holds the RESOLVED entry of ITS
    // OWN index (computed at arm time through the registry).
    let mut patched_all = true;
    for i in 0..2 {
        let slot = unsafe { MULTI_SLOTS[i] };
        let expect = unsafe { MULTI_EXPECTED[i] };
        let now = unsafe { core::ptr::read_volatile(slot as *const u64) };
        let ok = now == expect;
        serial_println!(
            "  experimental plt-demo self-test: instance 3 -- .rela.plt idx {} slot {:#x} patched to {:#x} (expected {:#x}, ITS OWN index) -- {}",
            i,
            slot,
            now,
            expect,
            if ok { "OK" } else { "FAILED" }
        );
        patched_all = patched_all && ok;
    }

    let count3 = crate::experimental::lazy_binder::entry_count_by_lm(unsafe { LM_IDS[2] });
    let c1 = crate::experimental::lazy_binder::entry_count_by_lm(unsafe { LM_IDS[0] });
    let c2 = crate::experimental::lazy_binder::entry_count_by_lm(unsafe { LM_IDS[1] });
    let objects = crate::experimental::lazy_binder::registered_count();
    let total = crate::experimental::lazy_binder::total_count();
    // Two trap-stub objects (full + deps demos) and three GNU
    // instances; the multi entry adds TWO binds (one per index), so
    // bind total = 2 (stub path) + 1 + 1 + 2 = 6 across 5 objects.
    let service_ok = objects == 5 && total == 6 && c1 == 1 && c2 == 1 && count3 == 2;
    serial_println!(
        "  experimental plt-demo self-test: MULTI-ENTRY artifact cookie {:#x}: {} first-use binds (EXACTLY 2 -- one per .rela.plt entry, one cookie); shared service now {} object(s) / {} bind(s) across both gates (0x88 trap stubs + 0x8B GNU own-.plt) -- {}",
        unsafe { LM_IDS[2] },
        count3,
        objects,
        total,
        if service_ok {
            "OK, one registry serves a MULTI-ENTRY GNU object AND the single-entry AND trap-stub paths"
        } else {
            "FAILED"
        }
    );
    assert!(
        a_ok && b_ok && patched_all && service_ok,
        "elf_dyn_plt_demo multi-entry checks failed (a={}, b={}, patched_all={}, service={})",
        a_ok, b_ok, patched_all, service_ok
    );

    // ---- the GNU-protocol MODE boundary, against a PLT-LESS artifact ----
    // userprog_pltless.elf carries a versioned GLOB_DAT only: NO
    // .rela.plt / .plt / .got.plt (CI-gate-pinned absence). Fact 1:
    // requesting the OWN-.plt arming mode on it is a TYPED refusal
    // (MalformedDynamicTables), before any lazy work -- the GNU
    // protocol needs its own .got.plt machinery to arm. Fact 2: the
    // SAME artifact loads eagerly in the plain resolver mode -- the
    // GLOB_DAT slot resolves AT LOAD, relro is revoked read-only,
    // and the GNU arming leaves NO side effects (no new registry
    // entry, no thunk page, no slot changes): eager stays the
    // default for objects that never had a PLT.
    let space = unsafe { (*&raw mut STAGE_SPACE).as_mut().expect("stage space missing") };
    let mut mapper = space.mapper();
    let fa = unsafe { (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap() };
    let reg_static: &'static elf::SymbolRegistry = unsafe {
        (&*(&raw const LAZY_REGISTRY)).as_ref().expect("demo registry missing")
    };
    let objects_before = crate::experimental::lazy_binder::registered_count();
    let total_before = crate::experimental::lazy_binder::total_count();

    let armed_ctx = elf::LoadContext {
        resolver: Some(reg_static),
        lazy_plt_page: None,
        own_plt_resolver_page: Some(VirtAddr::new(PLTLESS_REFUSAL_THUNK)),
    };
    let refused = elf::load(PLTLESS_ELF, &mut mapper, fa, PLTLESS_REFUSAL_BASE, &armed_ctx);
    let boundary1_ok = matches!(refused, Err(elf::ElfError::MalformedDynamicTables));
    serial_println!(
        "  experimental plt-demo: GNU OWN-.plt mode requested on the PLT-LESS artifact REFUSED with the typed MalformedDynamicTables (no .got.plt machinery to arm) -- {}",
        if boundary1_ok {
            "OK, the protocol refuses cleanly where its machinery does not exist"
        } else {
            "FAILED"
        }
    );

    let eager_ctx = elf::LoadContext {
        resolver: Some(reg_static),
        lazy_plt_page: None,
        own_plt_resolver_page: None,
    };
    let eager = elf::load(PLTLESS_ELF, &mut mapper, fa, PLTLESS_EAGER_BASE, &eager_ctx)
        .unwrap_or_else(|e| panic!("the PLT-LESS artifact must load eagerly: {:?}", e));
    let relro_ok = matches!(eager.relro, Some(elf::RelroOutcome::MadeReadOnly { .. }));
    let cross_slot =
        unsafe { core::ptr::read_volatile((PLTLESS_EAGER_BASE + PLTLESS_CROSS_SLOT) as *const u64) };
    let cross_addr = unsafe { EXPECTED_CROSS_ADDR };
    let eager_ok = cross_slot == cross_addr;
    let objects_after = crate::experimental::lazy_binder::registered_count();
    let total_after = crate::experimental::lazy_binder::total_count();
    let no_side_effects = objects_after == objects_before && total_after == total_before;
    serial_println!(
        "  experimental plt-demo: the SAME PLT-LESS artifact loads eagerly: its GLOB_DAT slot {:#x} == provider A's cross_value ({:#x}) resolved AT LOAD, relro revoked read-only, NO GNU arming side effects (registry {} -> {}, binds {} -> {}) -- {}",
        PLTLESS_EAGER_BASE + PLTLESS_CROSS_SLOT,
        cross_slot,
        objects_before,
        objects_after,
        total_before,
        total_after,
        if relro_ok && eager_ok && no_side_effects {
            "OK, the GNU protocol is opt-in per load; eager stays the default for PLT-less objects"
        } else {
            "FAILED"
        }
    );
    assert!(
        boundary1_ok && relro_ok && eager_ok && no_side_effects,
        "elf_dyn_plt_demo boundary checks failed (refusal={}, relro={}, eager={}, side_effects={})",
        boundary1_ok, relro_ok, eager_ok, no_side_effects
    );

    let fa = unsafe { (*&raw mut FRAME_ALLOCATOR).take().unwrap() };
    crate::experimental::pe_demo::start(fa)
}
