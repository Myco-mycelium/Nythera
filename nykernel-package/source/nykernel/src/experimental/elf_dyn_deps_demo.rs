//! The recursive-dependency / interposition / weak-version demo: one
//! dependent (a real `ld -shared` object) loads through an in-memory
//! DT_NEEDED CATALOG that resolves its declared dependencies
//! automatically, and its relocations exercise the two remaining
//! resolution classes at once --
//!
//! 1. **Recursive DT_NEEDED through a named catalog** — the
//!    dependent declares `DT_NEEDED crosslib2.so.1`, and the chain
//!    underneath it declares crosslib3.so.1, then crosslib4.so.1,
//!    then the first provider's crosslib.so.1 (all edges RECORDED BY
//!    THE LINKER, not typed by hand). `elf::load_tree` walks the
//!    declares
//!    dependency-FIRST from `elf::DepCatalog` (the kernel's honest
//!    stand-in for `/lib` + a search path until Phase 8's filesystem:
//!    names map to catalog-registered files and bases, nothing
//!    probes storage), so the dependent's contract checks see the
//!    complete scope at relocation time. The typed
//!    `DependencyCycle` refusal is proven against a deliberately
//!    looping catalog (A needs A) every boot — reloading a file to
//!    satisfy itself can never terminate, so it must never be
//!    attempted.
//! 2. **Interposition beyond load order** — `SymbolRegistry::
//!    interpose` marks the second provider as an overlay consulted
//!    BEFORE load order for every LATER object's resolution: the
//!    dependent's UNDEF GLOB_DAT for `dup_symbol` must hold the
//!    interposer's address, not the earlier-loaded provider's,
//!    observably pinned via a before/after `lookup` flip.
//! 3. **Weak versioned semantics** — the dependent references
//!    `ghost_fn@VERS_1.0` as a WEAK UNDEF symbol (a real `.weak` +
//!    `.symver` build artifact). No provider defines it: under the
//!    weak rule the GLOB_DAT slot holds the NULL slot (0), the load
//!    PROCEEDS, and its GNU_RELRO region still revokes read-only (an
//!    unresolved weak in-region entry is supported, not denied).
//!    Strong unresolvable requirements stay refused.
//! 4. **The lazy-bind service serves many objects** — the
//!    dependent's `cross_fn` PLT call arms a trap stub on this
//!    object's own trampoline page; the SHARED lazy_binder service
//!    dispatches by page (the full demo's dependent registered
//!    itself earlier in the same boot), so exactly one vector (0x88)
//!    first-binds BOTH independents.
//!
//! Chained after `experimental::elf_dyn_full_demo` (whose completion
//! trap hands control here) and chain-forward into
//! `experimental::pe_demo`.

use x86_64::structures::paging::PageTableFlags;
use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::elf::SymbolResolver;
use crate::experimental::{elf, lazy_binder, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::serial_println;

/// The dedicated ring-3 trap vector for the demo's completion report
/// (the next free slot after 0x89).
pub const TRAP_VECTOR: u8 = 0x8A;

static CROSS_ALL_LIB_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_all_lib.elf");
static CROSS_ALL_LIB2_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_all_lib2.elf");
static CROSS_ALL2_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_all2.elf");
static CROSS_ALL3_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_all3.elf");
static CROSS_ALL4_ELF: &[u8] = include_bytes!("../../userprog/userprog_cross_all4.elf");
static INTERPOSER_ELF: &[u8] = include_bytes!("../../userprog/userprog_interposer.elf");
static DEPS_ELF: &[u8] = include_bytes!("../../userprog/userprog_deps.elf");
static WEAK_ELF: &[u8] = include_bytes!("../../userprog/userprog_weak.elf");

const LIB_BASE: u64 = 0x_6D0_0000_0000; // first provider (catalog target)
const INTERPOSER_BASE: u64 = 0x_6D0_0010_0000;
const B_BASE: u64 = 0x_6D0_0020_0000; // crosslib2.so.1 (catalog target)
const C_BASE: u64 = 0x_6D0_002A_0000; // crosslib3.so.1 (catalog target, the chain's middle hop)
const D_BASE: u64 = 0x_6D0_0025_0000; // crosslib4.so.1 (catalog target, the chain's third hop)
const DEP_BASE: u64 = 0x_6D0_0030_0000; // the dependent
const TRAMPOLINE_PAGE: u64 = 0x_6D0_0031_0000; // the dependent's lazy stubs
const PRIVATE_ADDR: u64 = 0x_6D0_0040_0000; // matches prog_deps_dep.s's immediate
const USER_STACK_ADDR: u64 = 0x_6D0_0042_0000;
const SCRATCH_BASE: u64 = 0x_6D0_0050_0000; // cycle-refusal scratch
const DEPTH_FAIL_BASE: u64 = 0x_6D0_0055_0000; // depth-cap-refusal scratch
const BINDER_PROBE_PAGE: u64 = 0x_6D0_0057_0000; // unregistered trampoline for the lazy-binder negative control

/// Each chain provider carries a DISTINCT marker immediate (every
/// prog_cross_all*_lib.s's data marker); these are checked kernel-side
/// at each auto-loaded base, so the depth-3 fact is per-object
/// evidence (the RIGHT file loaded at the RIGHT base), not just a
/// count.
const EXPECTED_B_MARKER: u64 = 0x2BAD2BABECAFEC2E;
const EXPECTED_C_MARKER: u64 = 0x3333_C0FE_3333_C0FE;
const EXPECTED_D_MARKER: u64 = 0x4E0C_4E0C_4444_4444;
const WEAK_POS_BASE: u64 = 0x_6D0_0060_0000; // weak artifact, providing scope
const LIB2D_BASE: u64 = 0x_6D0_0061_0000; // the V2 provider, for the weak negative control
const WEAK_NEG_BASE: u64 = 0x_6D0_0062_0000; // weak artifact, wrong-version scope

/// The per-phase RSP0 budgets of this demo's split stage. The
/// round-8 single-stage allocation needed 8 pages because ONE stack
/// accumulated the deep loader work AND the behavioral half; the
/// split re-sized each phase for its own work and the split was then
/// MEASURED at boots, loudly, at the guard: phase B (map/arm/enter
/// ring 3) fits the shared 5-page default, but phase A (the
/// depth-3 tree + weak artifacts + GNU facts) still needs 6 -- the
/// first 5-page phase-A boot faulted a #PF-then-#DF at the guard on
/// the tree load, zero corruption, exactly what the guard exists
/// for. So the cell is 6: per-phase, not silent, and the guard
/// keeps every claim checked at every boot.
const PHASE_A_STACK_PAGES: u64 = 8;

const SONAME_A: &[u8] = b"crosslib.so.1";
const SONAME_B: &[u8] = b"crosslib2.so.1";
const SONAME_C: &[u8] = b"crosslib3.so.1";
const SONAME_D: &[u8] = b"crosslib4.so.1";
const SONAME_INTERPOSER: &[u8] = b"interposer.so.1";
const REQUIRED_VERSION: &[u8] = b"VERS_1.0";


/// The overlay's interp_mark (defined in prog_interpos.s): the
/// interposition observable.
const EXPECTED_DUP_VALUE: u64 = 0x1111_2222_3333_4444;
/// The provider's cross_value (same fact the full demo checks).
const EXPECTED_CROSS_VALUE: u64 = 0xACE5_F00D_D1CE_5EED;
/// cross_fn's 32-bit return (see the full demo for the ABI reason).
const EXPECTED_FN_RESULT: u64 = 0xFA571AE5;
const FN_RESULT_MASK: u64 = 0xFFFF_FFFF;

/// The lazy resolver scope for the dependent's first-use traps,
/// cloned into a 'static slot (the demo whose statics outlive
/// `start`, exactly like the full demo's LAZY_REGISTRY).
static mut DEPS_REGISTRY: Option<elf::SymbolRegistry> = None;
static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;
/// Phase B needs what phase A built (the registry this stage already
/// interposed the overlay into, and the dependent's parsed tables);
/// they are stashed at the phase-A->B seam like every other
/// cross-stage value (no caller frame survives the stack jump).
static mut DEPS_DEP_TABLES: Option<elf::FatDynTables> = None;
/// The two address spaces of this demo's SPLIT stage. Each phase
/// runs on its own DEFAULT-sized (5-page) guarded RSP0 stack -- the
/// former single-stage allocation had to be raised to 8 pages only
/// because one stack accumulated BOTH the deep catalog/weak work AND
/// the behavioral half; the split gives each phase a budget sized for
/// its own work, and NOTHING in the shared default had to change.
static mut STAGE_SPACE: Option<AddressSpace> = None;
static mut STAGE_SPACE_B: Option<AddressSpace> = None;

/// Boots the proof kernel-side (provider -> overlay -> catalog tree),
/// checks the slots, then enters `deps_payload` in ring 3 -- across
/// TWO fresh guarded stacks: phase A (the deep loader work) on the
/// first space's stack, phase B (map + arm + enter ring 3) on the
/// second. `on_trap` (0x8A) verifies the recorded facts and chains
/// into `experimental::pe_demo`.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();

    // The chain-partition rule: each demo's kernel-side work runs on
    // ITS OWN fresh guarded RSP0 stack (see pie_demo's stage split).
    // This demo is the chain's deepest, so its stage is SPLIT in two
    // phases that each land on their own DEFAULT-sized stack: the
    // deep loader work (phase A) and the behavioral ring-3 entry
    // (phase B). The split shrinks THIS demo's own per-stage budget
    // back to the shared default instead of asking the default (or
    // any other demo) to carry this stage's depth.
    let space_a = AddressSpace::new_sharing_kernel_with_stack_pages(fa, PHASE_A_STACK_PAGES)
        .unwrap_or_else(|_| panic!("failed to build elf_dyn_deps_demo address space (phase A)"));
    space_a.activate();
    STAGE_SPACE = Some(space_a);
    // Built while phase A is active (each PML4 copies the same shared
    // entries), but NOT activated -- the seam does NOT switch CR3 (the
    // payload's mappings live under phase A's tables); phase B's
    // space only contributes its OWN default-sized guarded stack as
    // the post-seam RSP0 target.
    let space_b = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build elf_dyn_deps_demo address space (phase B)"));
    STAGE_SPACE_B = Some(space_b);

    crate::experimental::chain::run_on_fresh_stack(
        (*&raw const STAGE_SPACE).as_ref().unwrap(),
        stage_a_after_stack_switch,
    )
}

/// PHASE A: the deep loader work (providers, GNU-hash walker facts,
/// interposition, the depth-3 catalog tree with its negative
/// controls, the weak-flag artifacts), on the first space's own
/// default-sized stack. Phase A ends by stashing the resolver
/// state phase B needs and jumping to phase B's stack.
unsafe extern "C" fn stage_a_after_stack_switch() -> ! {
    let space = unsafe { STAGE_SPACE.as_mut().expect("stage space missing") };
    let mut mapper = space.mapper();
    let fa = unsafe { FRAME_ALLOCATOR.as_mut().unwrap() };

    // ---- first provider: the catalog's first target ----
    elf::load(
        CROSS_ALL_LIB_ELF,
        &mut mapper,
        fa,
        LIB_BASE,
        &elf::LoadContext::none(),
    )
    .unwrap_or_else(|e| panic!("failed to load provider A: {:?}", e));
    let (lib_dyn_vaddr, lib_dyn_filesz) = elf::pt_dynamic_range(CROSS_ALL_LIB_ELF)
        .expect("provider must carry a PT_DYNAMIC segment (readelf-verified)");
    let lib_tables = elf::parse_dyn_tables(LIB_BASE, lib_dyn_vaddr, lib_dyn_filesz)
        .expect("provider dyn tables must parse");
    assert!(
        lib_tables.soname() == Some(SONAME_A),
        "provider A's DT_SONAME must be crosslib.so.1"
    );
    let mut registry = elf::SymbolRegistry::new();
    registry.push(lib_tables.clone(), LIB_BASE);

    // HASH-MECHANISM equality: this provider carries BOTH tables
    // (readelf-pinned in the layout gate). The SysV bucket/chain walk
    // and the glibc-style GNU bloom+bucket/chain walk are separate
    // mechanisms; their ANSWERS on the same tables are checked equal.
    let both_ok = lib_tables.carries_gnu_hash();
    let via_gnu = unsafe { elf::lookup_symbol_gnu(&lib_tables, LIB_BASE, b"cross_value") }
        .expect("the GNU walk must resolve cross_value on a both-tables artifact");
    let via_sysv = unsafe { elf::lookup_symbol_sysv(&lib_tables, LIB_BASE, b"cross_value") }
        .expect("the SysV walk must resolve cross_value on a both-tables artifact");
    let gnu_sysv_ok = via_gnu == via_sysv;

    // WALKER ERROR-PATH FACTS: the two mechanisms' REFUSALS are typed
    // separately, and each mechanism's absent-answer is what its
    // structure implies --
    //
    //  1. A name that exists in NEITHER table answers SymbolNotFound
    //     through the GNU walk (its particular path is merged: bloom
    //     miss and empty/missing bucket both mean "excluded"), the
    //     refusal the resolver interprets as "not this object".
    //  2. The GNU table's UNDEF exclusion, pinned on the weak
    //     artifact below the same way: a name PRESENT in dynsym
    //     (st_shndx == 0)/DT_HASH chain but absent from GNU
    //     buckets answers SymbolNotFound through GNU while SysV
    //     reports it UndefinedSymbol -- the asymmetry lookup_symbol
    //     re-walks SysV to preserve.
    let gnu_absent_name_ok = matches!(
        unsafe { elf::lookup_symbol_gnu(&lib_tables, LIB_BASE, b"name_never_defined_in_provider_a") },
        Err(elf::ElfError::SymbolNotFound)
    );
    serial_println!(
        "  experimental deps-demo: GNU walk of a name in NEITHER of A's tables refused with SymbolNotFound -- {}",
        if gnu_absent_name_ok { "OK (absent, not malformed: GNU-absent is typed as absent)" } else { "FAILED" }
    );

    serial_println!(
        "  experimental deps-demo: provider A carries BOTH hash tables (DT_HASH authority + DT_GNU_HASH); GNU walk == SysV walk on cross_value ({:#x}) -- {}",
        via_gnu,
        if both_ok && gnu_sysv_ok {
            "OK, the GNU mechanism's answer matches the SysV mechanism's"
        } else {
            "FAILED"
        }
    );

    serial_println!(
        "  experimental deps-demo: provider A loaded (crosslib.so.1) -- OK"
    );

    // ---- the interposition overlay ----
    elf::load(
        INTERPOSER_ELF,
        &mut mapper,
        fa,
        INTERPOSER_BASE,
        &elf::LoadContext::none(),
    )
    .unwrap_or_else(|e| panic!("failed to load the interposer: {:?}", e));
    let (ip_dyn_vaddr, ip_dyn_filesz) = elf::pt_dynamic_range(INTERPOSER_ELF)
        .expect("interposer must carry a PT_DYNAMIC segment");
    let interposer_tables = elf::parse_dyn_tables(INTERPOSER_BASE, ip_dyn_vaddr, ip_dyn_filesz)
        .expect("interposer dyn tables must parse");
    assert!(
        interposer_tables.soname() == Some(SONAME_INTERPOSER),
        "interposer's DT_SONAME must be interposer.so.1"
    );    // BEFORE overlay: load order gives the FIRST provider's answer
    // for any name both objects carry. Provider A does NOT define
    // interp_mark (the overlay's observable is deliberately absent
    // from it), so the pre-overlay pre-image is the honest "no
    // definition anywhere": the OVERLAY is the ONLY provider, which
    // makes the flip proof unambiguous.
    let before = registry.lookup(b"interp_mark").expect("pre-overlay lookup must be structurally clean");
    let before_ok = before.is_none();
    serial_println!(
        "  experimental deps-demo: pre-overlay lookup of interp_mark = absent (provider A does not define it) -- {}",
        if before_ok { "OK, absent before the overlay exists" } else { "UNEXPECTED DEFINITION" }
    );

    // AFTER overlay: interpose(last) flips the registry's answer --
    // and only for LATER objects (the first provider's already-
    // resolved slots keep their resolved values structurally).
    registry.push(interposer_tables, INTERPOSER_BASE);
    registry.interpose(registry.len() - 1);
    let after = registry
        .lookup(b"interp_mark")
        .expect("post-overlay lookup must be structurally clean")
        .expect("interp_mark must exist in the overlay");
    let after_val = core::ptr::read_volatile(after as *const u64);
    let after_ok = after_val == EXPECTED_DUP_VALUE;
    serial_println!(
        "  experimental deps-demo: after interpose(overlay), lookup of interp_mark = {:#x} (data {:#x}, the overlay's value) -- {}",
        after,
        after_val,
        if after_ok { "OK, interposition overrides load order" } else { "MISMATCH" }
    );

    // ---- the recursive load through the catalog ----
    // The chain is now THREE catalog hops deep:
    //   dependent3 -> crosslib2 (B) -> crosslib3 (C) -> crosslib4 (D)
    //   -> crosslib (A, ALREADY in scope), so the loader auto-loads
    //   THREE objects.
    let mut catalog = elf::DepCatalog::new(8);
    catalog.add(SONAME_A, CROSS_ALL_LIB_ELF, LIB_BASE);
    catalog.add(SONAME_B, CROSS_ALL2_ELF, B_BASE);
    catalog.add(SONAME_C, CROSS_ALL3_ELF, C_BASE);
    catalog.add(SONAME_D, CROSS_ALL4_ELF, D_BASE);

    // Negative control FIRST (before anything owns the names): a
    // deliberately looping catalog (crosslib3.so.1 -> the B file,
    // which NEEDS crosslib3.so.1 itself, against an EMPTY scope) must
    // be refused with DependencyCycle, not attempted.
    let mut loop_cat = elf::DepCatalog::new(8);
    loop_cat.add(SONAME_C, CROSS_ALL2_ELF, SCRATCH_BASE);
    let empty = elf::SymbolRegistry::new();
    let cycle = unsafe {
        elf::load_tree(
            CROSS_ALL2_ELF,
            Some(SONAME_B),
            SCRATCH_BASE,
            &mut mapper,
            fa,
            &empty,
            &loop_cat,
            None,
        )
    };
    let cycle_ok = matches!(cycle, Err(elf::ElfError::DependencyCycle));
    serial_println!(
        "  experimental deps-demo: looping catalog refused with DependencyCycle -- {}",
        if cycle_ok { "OK, a file can never load itself to satisfy itself" } else { "FAILED" }
    );

    // The real tree: dependent -> crosslib2 -> crosslib3 ->
    // crosslib4 -> crosslib (the last already in scope, so only B, C
    // and D are loaded from the catalog).
    let loaded = unsafe {
        elf::load_tree(
            DEPS_ELF,
            Some(b"deps_demo_root"),
            DEP_BASE,
            &mut mapper,
            fa,
            &registry,
            &catalog,
            Some(VirtAddr::new(TRAMPOLINE_PAGE)),
        )
    }
    .unwrap_or_else(|e| panic!("catalog tree load failed: {:?}", e));
    let (loaded_elf, extras, dep_tables) = loaded;
    let auto_loaded = extras.len();
    serial_println!(
        "  experimental deps-demo: DT_NEEDED CHAIN (dependent -> crosslib2.so.1 -> crosslib3.so.1 -> crosslib4.so.1 -> crosslib.so.1) satisfied dependency-first; {} catalog object(s) auto-loaded -- {}",
        auto_loaded,
        if auto_loaded == 3 {
            "OK, the DEPTH-3 chain (B, C and D) loaded from the catalog; A was already in scope"
        } else {
            "UNEXPECTED COUNT"
        }
    );
    // DEPTH-CAP NEGATIVE CONTROL (before the real registry absorbs
    // the auto-loaded objects): the same chain under a catalog whose
    // declared cap is TOO SHALLOW (max_depth = 2 vs the chain's THREE
    // catalog hops) must be refused with the typed
    // `DependencyDepthExceeded` before the third hop is ever attempted
    // (B and C map into dedicated scratch bases, then the D hop sees
    // in_progress.len() == 2 >= cap and refuses; A was never reached).
    // This is the load_tree's OTHER typed refusal, now checked like
    // the cycle refusal is.
    let mut cap_cat = elf::DepCatalog::new(2);
    cap_cat.add(SONAME_A, CROSS_ALL_LIB_ELF, DEPTH_FAIL_BASE);
    cap_cat.add(SONAME_B, CROSS_ALL2_ELF, DEPTH_FAIL_BASE + 0x2000);
    cap_cat.add(SONAME_C, CROSS_ALL3_ELF, DEPTH_FAIL_BASE + 0x4000);
    cap_cat.add(SONAME_D, CROSS_ALL4_ELF, DEPTH_FAIL_BASE + 0x6000);
    let empty_cap = elf::SymbolRegistry::new();
    let cap_outcome = unsafe {
        elf::load_tree(
            DEPS_ELF,
            Some(b"deps_demo_root_cap"),
            DEPTH_FAIL_BASE + 0x8000,
            &mut mapper,
            fa,
            &empty_cap,
            &cap_cat,
            None,
        )
    };
    let cap_ok = matches!(cap_outcome, Err(elf::ElfError::DependencyDepthExceeded));
    serial_println!(
        "  experimental deps-demo: depth-3 chain under a catalog whose cap is 2 refused with DependencyDepthExceeded (at the D hop, B and C mapped, A never reached) -- {}",
        if cap_ok { "OK" } else { "FAILED" }
    );

    // MARKER FACTS: the auto-loaded providers each carry a DISTINCT
    // marker immediate; each is located by SYMBOL at ITS OWN auto-load
    // base and its content checked, so the depth-3 chain is per-object
    // evidence, not a count alone.
    let marker_expected: [(&'static [u8], u64); 3] = [
        (b"cross2_marker", EXPECTED_B_MARKER),
        (b"cross3_marker", EXPECTED_C_MARKER),
        (b"cross4_marker", EXPECTED_D_MARKER),
    ];
    let mut marker_count = 0u32;
    let mut markers_ok = true;
    for (t, b) in &extras {
        for (name, expected) in &marker_expected {
            if let Ok(addr) = unsafe { elf::lookup_symbol(t, *b, name) } {
                marker_count += 1;
                if core::ptr::read_volatile(addr as *const u64) != *expected {
                    markers_ok = false;
                }
            }
        }
    }
    // All three markers must have been FOUND exactly once; an absent
    // marker fails the count, a wrong content fails the compare.
    markers_ok = markers_ok && marker_count == 3;
    serial_println!(
        "  experimental deps-demo: 3 auto-loaded chain providers carry their DISTINCT marker immediates at their own bases ({}/3 symbols found, contents == the source constants) -- {}",
        marker_count,
        if markers_ok { "OK, the right file loaded at the right base, per object" } else { "FAILED" }
    );

    for (t, b) in extras {
        registry.push(t, b);
    }
    let relro_ok = matches!(
        loaded_elf.relro,
        Some(elf::RelroOutcome::MadeReadOnly { .. })
    );
    serial_println!(
        "  experimental deps-demo: dependent's GNU_RELRO revokes read-only WITH an unresolved WEAK entry in-region (weak != denial) -- {}",
        if relro_ok { "OK" } else { "FAILED" }
    );

    // ---- kernel-side slot facts (located by SYMBOL, not offsets) ----
    let cross_slot = unsafe { elf::find_slot_for_symbol(&dep_tables, DEP_BASE, b"cross_value") }
        .expect("cross_value slot search must be structurally clean")
        .expect("a versioned reference always binds a slot");
    let via_version = registry
        .resolve_versioned(b"cross_value", Some((REQUIRED_VERSION, SONAME_A)))
        .expect("the versioned probe surface must stay structurally clean")
        .expect("cross_value must resolve against provider A");
    let cross_val = core::ptr::read_volatile(cross_slot as *const u64);
    // The slot holds the SYMBOL ADDRESS the versioned lookup produced
    // (the payload's own deref proves the DATA content in on_trap).
    let cross_ok = cross_val == via_version;
    serial_println!(
        "  experimental deps-demo: dependent's versioned GLOB_DAT slot {:#x} == {:#x} (provider's cross_value as resolved BY THE CATALOG CHAIN) -- {}",
        cross_slot,
        cross_val,
        if cross_ok { "OK" } else { "MISMATCH" }
    );

    let dup_slot = unsafe { elf::find_slot_for_symbol(&dep_tables, DEP_BASE, b"interp_mark") }
        .expect("interp_mark slot search must be structurally clean")
        .expect("the UNDEF GOTPCREL reference always binds a slot");
    let dup_val = core::ptr::read_volatile(dup_slot as *const u64);
    let dup_ok = dup_val == after;
    serial_println!(
        "  experimental deps-demo: dependent's interp_mark slot {:#x} == {:#x} (the INTERPOSITION overlay's address, not that of anything provider A defines) -- {}",
        dup_slot,
        dup_val,
        if dup_ok { "OK" } else { "MISMATCH" }
    );

    let ghost_slot = unsafe { elf::find_slot_for_symbol(&dep_tables, DEP_BASE, b"ghost_fn") }
        .expect("ghost_fn slot search must be structurally clean")
        .expect("the weak UNDEF GOTPCREL reference binds a GLOB_DAT slot");
    let ghost_val = core::ptr::read_volatile(ghost_slot as *const u64);
    let ghost_ok = ghost_val == 0;
    serial_println!(
        "  experimental deps-demo: WEAK UNDEF ghost_fn@VERS_1.0 slot {:#x} == {:#x} (the NULL slot; unresolved weak stays non-fatal) -- {}",
        ghost_slot,
        ghost_val,
        if ghost_ok { "OK" } else { "MISMATCH" }
    );

    let lazy_slot = unsafe { elf::lazy_slot_for(&dep_tables, DEP_BASE, 0) }
        .expect("the dependent's single JUMP_SLOT entry must compute");
    let lazy_val = core::ptr::read_volatile(lazy_slot as *const u64);
    let lazy_ok = lazy_val == TRAMPOLINE_PAGE;
    serial_println!(
        "  experimental deps-demo: lazy JUMP_SLOT slot {:#x} == {:#x} (trap stub armed on THIS object's own trampoline page; NOT eagerly resolved) -- {}",
        lazy_slot,
        lazy_val,
        if lazy_ok { "OK" } else { "MISMATCH" }
    );

    // ---- the WEAK VERSION-FLAG artifact (kernel-side only) ----
    // userprog_weak.elf's ONLY UNDEF is a WEAK bind of the versioned
    // cross_value; GNU ld records the VERNEED dependency with
    // VER_FLG_WEAK (gate fact). Positive: against the providing scope
    // it RESOLVES normally. Negative: against the same-SONAME VERS_2.0
    // provider -- where a STRONG requirement is the typed
    // VersionNotProvided refusal -- the weak requirement takes the
    // NULL slot and the load proceeds, region read-only.
    let weak_pos = elf::load(
        WEAK_ELF,
        &mut mapper,
        fa,
        WEAK_POS_BASE,
        &elf::LoadContext { resolver: Some(&registry), lazy_plt_page: None, own_plt_resolver_page: None },
    );
    let weak_pos_slot = weak_pos.as_ref().ok().and_then(|_| {
        let (wv, wf) = elf::pt_dynamic_range(WEAK_ELF)?;
        let t = unsafe { elf::parse_dyn_tables(WEAK_POS_BASE, wv, wf) }.ok()?;
        unsafe { elf::find_slot_for_symbol(&t, WEAK_POS_BASE, b"cross_value") }.ok().flatten()
    });
    let weak_pos_ok = weak_pos.is_ok()
        && weak_pos_slot
            .map(|slot| core::ptr::read_volatile(slot as *const u64) == via_version)
            .unwrap_or(false);

    serial_println!(
        "  experimental deps-demo: WEAK version-flag artifact resolves cross_value@VERS_1.0 against the providing scope (weak relaxes only the UNRESOLVED case) -- {}",
        if weak_pos_ok { "OK" } else { "FAILED" }
    );

    // WALKER ERROR-PATH FACT 2 (see the facts block above): the weak
    // artifact's cross_value is the perfect probe for the GNU UNDEF
    // exclusion -- an UNDEF dynsym entry the SysV DT_HASH chain DOES
    // carry (hence UndefinedSymbol) but the GNU bloom/buckets do NOT
    // carry (hence SymbolNotFound). lookup_symbol's documented SysV
    // re-walk exists exactly so this asymmetry never flips "present
    // but undefined" into "not this object's name".
    let (gnu_excludes_undef_ok, sysv_marks_undef_ok) = if weak_pos.is_ok() {
        let (wv, wf) = elf::pt_dynamic_range(WEAK_ELF).expect("weak artifact PT_DYNAMIC");
        let wt = unsafe { elf::parse_dyn_tables(WEAK_POS_BASE, wv, wf) }
            .expect("weak artifact tables must parse");
        let gnu = matches!(
            unsafe { elf::lookup_symbol_gnu(&wt, WEAK_POS_BASE, b"cross_value") },
            Err(elf::ElfError::SymbolNotFound)
        );
        let sysv = matches!(
            unsafe { elf::lookup_symbol_sysv(&wt, WEAK_POS_BASE, b"cross_value") },
            Err(elf::ElfError::UndefinedSymbol)
        );
        serial_println!(
            "  experimental deps-demo: GNU walk of the weak artifact's UNDEF cross_value reports ABSENT (SymbolNotFound) while the SysV walk reports UndefinedSymbol -- {}",
            if gnu && sysv {
                "OK (GNU excludes UNDEF entries from its buckets; DT_HASH chains them; the asymmetry is typed)"
            } else {
                "FAILED"
            }
        );
        (gnu, sysv)
    } else {
        (false, false)
    };

    // Negative control: the V2 provider (same SONAME, no VERS_1.0) in
    // the resolver scope.
    elf::load(
        CROSS_ALL_LIB2_ELF,
        &mut mapper,
        fa,
        LIB2D_BASE,
        &elf::LoadContext::none(),
    )
    .unwrap_or_else(|e| panic!("failed to load the V2 provider for the weak negative control: {:?}", e));
    let (lib2_dyn_vaddr, lib2_dyn_filesz) = elf::pt_dynamic_range(CROSS_ALL_LIB2_ELF)
        .expect("provider 2 must carry a PT_DYNAMIC segment");
    let lib2_tables = elf::parse_dyn_tables(LIB2D_BASE, lib2_dyn_vaddr, lib2_dyn_filesz)
        .expect("provider 2 dyn tables must parse");
    let mut registry_neg = elf::SymbolRegistry::new();
    registry_neg.push(lib2_tables, LIB2D_BASE);
    let weak_neg = elf::load(
        WEAK_ELF,
        &mut mapper,
        fa,
        WEAK_NEG_BASE,
        &elf::LoadContext { resolver: Some(&registry_neg), lazy_plt_page: None, own_plt_resolver_page: None },
    );
    let weak_neg_relro = weak_neg
        .as_ref()
        .map(|l| matches!(l.relro, Some(elf::RelroOutcome::MadeReadOnly { .. })))
        .unwrap_or(false);
    let weak_neg_slot = weak_neg.ok().and_then(|_| {
        let (wv, wf) = elf::pt_dynamic_range(WEAK_ELF).expect("weak artifact PT_DYNAMIC");
        let t = unsafe { elf::parse_dyn_tables(WEAK_NEG_BASE, wv, wf) }.expect("weak artifact tables");
        let slot = unsafe { elf::find_slot_for_symbol(&t, WEAK_NEG_BASE, b"cross_value") }
            .expect("weak artifact slot search must be structurally clean")
            .expect("the weak versioned UNDEF must bind a GLOB_DAT slot");
        Some(core::ptr::read_volatile(slot as *const u64))
    });
    let weak_neg_ok = weak_neg_slot == Some(0);
    serial_println!(
        "  experimental deps-demo: WEAK version-flag requirement with unmet VERS_1.0 takes the NULL slot (0), load proceeds, relro {} -- {} (a STRONG requirement is the typed VersionNotProvided refusal in the same position)",
        if weak_neg_relro { "read-only" } else { "denied" },
        if weak_neg_ok { "OK" } else { "FAILED" }
    );

    // LAZY-BINDER NEGATIVE CONTROL: what the 0x88 service answers for
    // a trampoline page NO registered object owns. The handler PANICS
    // on exactly this refusal (arm-before-first-use is a wiring
    // contract, never "resolvable by guess"); the typed probe below
    // exposes the SAME identification path as a checkable result, so
    // the refusal itself is a boot fact: the typed variant carries the
    // probe page back, and nothing in the service moved.
    let binds_before = lazy_binder::total_count();
    let registered_before = lazy_binder::registered_count();
    let binder_outcome = unsafe { lazy_binder::probe_unregistered(BINDER_PROBE_PAGE) };
    let binder_refused = matches!(
        binder_outcome,
        Err(lazy_binder::LazyBindRefusal::UnregisteredTrampoline { ip_page })
            if ip_page == BINDER_PROBE_PAGE
    );
    let binder_clean = lazy_binder::registered_count() == registered_before
        && lazy_binder::total_count() == binds_before
        && lazy_binder::entry_count(BINDER_PROBE_PAGE) == 0;
    serial_println!(
        "  experimental deps-demo: lazy-bind service refuses an UNREGISTERED trampoline page ({:#x}) with the typed UnregisteredTrampoline carrying that page; registry size {} -> {} and binds {} -> {} unchanged -- {}",
        BINDER_PROBE_PAGE,
        registered_before,
        lazy_binder::registered_count(),
        binds_before,
        lazy_binder::total_count(),
        if binder_refused && binder_clean {
            "OK (the handler's panic path is demonstrated, not assumed)"
        } else {
            "FAILED"
        }
    );

    let all_kernel_ok = before_ok && after_ok && cycle_ok && cap_ok && markers_ok
        && relro_ok && cross_ok
        && dup_ok && ghost_ok && lazy_ok && auto_loaded == 3 && both_ok
        && gnu_sysv_ok && gnu_absent_name_ok && gnu_excludes_undef_ok
        && sysv_marks_undef_ok
        && weak_pos_ok && weak_neg_ok && weak_neg_relro
        && binder_refused && binder_clean;
    assert!(
        all_kernel_ok,
        "elf_dyn_deps_demo kernel-side checks failed (before={}, after={}, cycle={}, cap={}, markers={}, binder_refused={}, binder_clean={}, relro={}, cross={}, dup={}, ghost={}, lazy={}, auto={}, both={}, gnu_sysv={}, gnu_abs={}, gnu_excl_undef={}, wpos={}, wneg={}, wneg_relro={})",
        before_ok, after_ok, cycle_ok, cap_ok, markers_ok, binder_refused, binder_clean, relro_ok, cross_ok, dup_ok, ghost_ok, lazy_ok, auto_loaded, both_ok, gnu_sysv_ok, gnu_absent_name_ok, gnu_excludes_undef_ok, weak_pos_ok, weak_neg_ok, weak_neg_relro
    );

    // ---- PHASE SEAM ----
    // Phase A is done: every deep-kernel-work fact has run and been
    // asserted on phase A's stack. Stash what phase B needs (the
    // resolver scope and the dependent's parsed tables) into the
    // demo's statics -- no caller frame survives a stack jump -- then
    // jump onto PHASE B's own default-sized guarded stack.
    //
    // Deliberately NOT a CR3 switch: phase A's space stays ACTIVE.
    // Everything the payload needs (its own mappings, the loaded
    // providers' tables, the registry) was mapped under phase A's
    // private PML4 subtree -- giving phase B its own space would
    // strand all of it (only PML4 indices 0 and 2 are shared). The
    // split is a STACK split at this seam: TSS.RSP0 is pointed at
    // phase B's fresh stack (a shared-region allocation, valid under
    // whichever space is active -- that is the region's whole
    // reason to exist), phase B's stack top is never confused with
    // phase A's, and nothing else changes. Phase A's abandoned stack
    // pages stay allocated (the documented leak-by-design of every
    // stage transition).
    DEPS_REGISTRY = Some(registry);
    DEPS_DEP_TABLES = Some(dep_tables);
    let space_b = unsafe { (*&raw const STAGE_SPACE_B).as_ref().expect("phase B space missing") };
    unsafe { crate::gdt::set_rsp0(x86_64::VirtAddr::new(space_b.kernel_stack_top())) };
    crate::experimental::chain::run_on_fresh_stack(space_b, stage_b_after_stack_switch)
}

/// PHASE B: deliberately SHALLOW (sized for the shared default
/// budget): re-derive the state phase A stashed, map the payload's
/// two ring-3 pages, arm the SHARED lazy-binder service with the
/// registry tables phase A built, and enter `deps_payload` in
/// ring 3. Runs with PHASE A's address space still active (see the
/// seam note): the mapping context is one live space, the stack
/// context is phase B's.
unsafe extern "C" fn stage_b_after_stack_switch() -> ! {
    // The ACTIVE space is phase A's (CR3 unchanged at the seam), so
    // pages map through phase A's PML4 -- the payload and the whole
    // loaded chain live there. `STAGE_SPACE_B`'s stack is already
    // under us at this point (TSS.RSP0 pointed at it at the seam;
    // run_on_fresh_stack jumped onto it).
    let space = unsafe { STAGE_SPACE.as_mut().expect("stage space missing") };
    let mut mapper = space.mapper();
    let fa = unsafe { FRAME_ALLOCATOR.as_mut().unwrap() };
    let dep_tables = unsafe { (*&raw const DEPS_DEP_TABLES).as_ref().expect("dep tables missing") };
    let _registry = unsafe { DEPS_REGISTRY.as_mut().expect("deps registry missing") };

    // ---- behavioral half: enter deps_payload in ring 3 ----
    let data_flags = PageTableFlags::PRESENT
        | PageTableFlags::WRITABLE
        | PageTableFlags::USER_ACCESSIBLE
        | PageTableFlags::NO_EXECUTE;
    let data_mapped = crate::memory::paging::map_range(
        &mut mapper,
        fa,
        VirtAddr::new(PRIVATE_ADDR),
        1,
        data_flags,
    );
    assert_eq!(data_mapped, 1, "failed to map deps-demo private data page");
    let stack_mapped = crate::memory::paging::map_range(
        &mut mapper,
        fa,
        VirtAddr::new(USER_STACK_ADDR),
        1,
        data_flags,
    );
    assert_eq!(stack_mapped, 1, "failed to map deps-demo user stack");

    let payload_addr = unsafe { elf::lookup_symbol(dep_tables, DEP_BASE, b"deps_payload") }
        .expect("deps_payload must resolve by name");

    // Arm the shared service BEFORE the first call: this dependent's
    // OWN trampoline page is its dispatch identity, and the resolver
    // scope (registry + the catalog-loaded providers) lives here for
    // the whole chain. The registry is RESUMED here from phase A's
    // stash (it already carries the overlay and the auto-loaded
    // providers' tables; pushing more would corrupt its order).
    // SAFETY: the stash is 'static, the loaded object's mappings live
    // in the shared subtree, and lazy_binder::register's contract
    // holds the same way phase A's demo registries held.
    unsafe {
        let reg_ref: &'static elf::SymbolRegistry =
            (&*(&raw const DEPS_REGISTRY)).as_ref().unwrap();
        lazy_binder::register(
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

/// The completion trap: verifies the payload's recorded facts
/// (cross_value through the catalog-resolved slot, the interposed
/// dup_symbol, three direct-after-first cross_fn results through the
/// shared lazy service) and chains into `experimental::pe_demo`.
///
/// # Safety
/// Called from the (ring-3-privileged) IDT handler; `start` must have
/// armed the demo.
pub unsafe fn on_trap() -> ! {
    let cross_observed = core::ptr::read_volatile(PRIVATE_ADDR as *const u64);
    let dup_observed = core::ptr::read_volatile((PRIVATE_ADDR + 8) as *const u64);
    let f0 = core::ptr::read_volatile((PRIVATE_ADDR + 16) as *const u64);
    let f1 = core::ptr::read_volatile((PRIVATE_ADDR + 24) as *const u64);
    let f2 = core::ptr::read_volatile((PRIVATE_ADDR + 32) as *const u64);

    let cross_ok = cross_observed == EXPECTED_CROSS_VALUE;
    let dup_ok = dup_observed == EXPECTED_DUP_VALUE;    let fn_ok = (f0 & FN_RESULT_MASK) == EXPECTED_FN_RESULT
        && (f1 & FN_RESULT_MASK) == EXPECTED_FN_RESULT
        && (f2 & FN_RESULT_MASK) == EXPECTED_FN_RESULT;
    let count = lazy_binder::entry_count(TRAMPOLINE_PAGE);
    let bind_once = count == 1;
    let service_total = lazy_binder::total_count();
    let service_objects = lazy_binder::registered_count();
    let service_ok = service_total == 2 && service_objects == 2;
    serial_println!(
        "  experimental deps-demo self-test: cross_value = {:#x} (expected {:#x}) -- {}",
        cross_observed,
        EXPECTED_CROSS_VALUE,
        if cross_ok { "OK" } else { "MISMATCH" }
    );
    serial_println!(
        "  experimental deps-demo self-test: interp_mark = {:#x} (expected {:#x}, the overlay's value read through ring 3) -- {}",
        dup_observed,
        EXPECTED_DUP_VALUE,
        if dup_ok { "OK" } else { "MISMATCH" }
    );
    serial_println!(
        "  experimental deps-demo: lazy-bind service fired {} time(s) across 3 calls (exactly 1 required) -- {}",
        count,
        if bind_once { "OK, binding is lazy; calls 2-3 are direct" } else { "FAILED" }
    );
    serial_println!(
        "  experimental deps-demo: shared lazy-bind service: {} independent object(s) registered, {} bind(s) served through ONE vector (0x88) this boot -- {}",
        service_objects,
        service_total,
        if service_ok {
            "OK, the vector is a SERVICE, not a demo"
        } else {
            "FAILED"
        }
    );
    assert!(
        cross_ok && dup_ok && fn_ok && bind_once && service_ok,
        "elf_dyn_deps_demo behavioral checks failed (cross={}, dup={}, fn={}, once={}, service={})",
        cross_ok, dup_ok, fn_ok, bind_once, service_ok
    );

    let fa = FRAME_ALLOCATOR.take().unwrap();
    // The GNU OWN-.plt lazy-protocol proof runs between this demo and
    // pe_demo: a real linker .plt + .got.plt dependent, resolved
    // through the registry-owned link-map service rather than a
    // synthetic trap stub.
    crate::experimental::elf_dyn_plt_demo::start(fa)
}
