//! PE32+ IMPORT RESOLUTION demo -- the second real Phase-5 capability
//! increment after `experimental::pe` itself and its relocation support.
//!
//! What is proven, end to end, every boot:
//!
//! 1. A real Windows-format DLL (`userprog_pe_import_dll.dll`, `ld -m
//!    i386pep --shared --enable-auto-import`) is loaded at its own
//!    `ImageBase`, and its own `IMAGE_DIRECTORY_ENTRY_EXPORT` table is
//!    parsed into `pe::DllExportTable` -- the real GetProcAddress
//!    mechanism, not a synthetic name->address map. Its declared DLL
//!    name, its two exported names and both exports' absolute runtime
//!    addresses are stated as facts and checked every boot.
//! 2. A real Windows-format executable (`userprog_pe_import_app.exe`,
//!    `ld -m i386pep --subsystem native`, linked DIRECTLY against that
//!    DLL -- GNU ld synthesizes the client's `.idata`/IAT itself, no
//!    dlltool/import-library step exists or is needed on this host) is
//!    loaded, and `pe::resolve_imports` walks its REAL
//!    `IMAGE_DIRECTORY_ENTRY_IMPORT` descriptors and patches its two
//!    IAT slots with the exports' absolute runtime addresses.
//! 3. The client's own code runs in ring 3 and uses the imports the
//!    faithful Windows way: `mov __imp_crossmark(%rip), %rax` (load the
//!    resolved DATA export address out of its IAT slot), dereference it
//!    (the provider's own data), and `call *__imp_crossfn(%rip)`
//!    (indirect call THROUGH the resolved FUNCTION export's slot). Both
//!    results -- the provider's own data immediate read through the
//!    resolved pointer, and the provider's own code's return value --
//!    are checked after the `int $0x8D` trap.
//!
//! Negative controls (typed refusals, permanent, every boot -- both
//! probes run against the real mapped client image before real
//! resolution touches anything):
//! - a registry with NO entry for the imported DLL name ->
//!   `PeError::MissingDllImport`;
//! - the DLL name bound to the WRONG FILE's export table (the client
//!   exe itself, which has NO export directory -> an empty table) ->
//!   `PeError::MissingImport` per symbol. This is the sharp edge the
//!   name-vs-file distinction exists for: the registry identity is the
//!   FILE, not whatever printed name a session may register under it.
//!   The per-symbol refusal carries the per-file ruling, and real
//!   resolution still runs afterward, clean.
//! Deliberate scope (same honest posture as `pe.rs`'s doc): no
//! forwarding exports, no delay-load, no bound imports, no ordinal-name
//! imports (typed `UnsupportedOrdinalImport` by the loader), no Win32
//! API surface. A real Windows-format pair's own import/export
//! machinery, driven by the loader, is what ends this boot.

use x86_64::structures::paging::PageTableFlags;
use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{isolation_demo, pe, usermode};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;
use crate::serial_println;

pub const TRAP_VECTOR: u8 = 0x8D;

/// The provider DLL and the dependent PE. Both real `ld -m i386pep`
/// products embedded at build time; `tools/verify_prog_layout.sh`
/// fact-checks both (real Export Directory sizes, real Import
/// Directory with the pinned DLL name and hint/name entries) BEFORE
/// the kernel build.
static PROVIDER_DLL: &[u8] = include_bytes!("../../userprog/userprog_pe_import_dll.dll");
static IMPORT_APP: &[u8] = include_bytes!("../../userprog/userprog_pe_import_app.exe");

/// The files' own fixed `ImageBase`s, pinned by the layout gate AND by
/// the build (--image-base on both links). Both bases sit in private
/// PML4 indexes (0x14 = the client, 0x18 = the provider), NOT the
/// shared index-0 identity subtree: the pe-demo's own program was
/// loaded at 0x140000000 under index 0, and index-0 mappings persist
/// across every demo address space that shares it -- the first failed
/// boot proved that with a PageAlreadyMapped on this client's header
/// page. The client loads at its own base (fixed-address, no `.reloc`
/// involved), the provider at its own.
const APP_IMAGE_BASE: u64 = 0x1400_0000_0000;
const PROVIDER_IMAGE_BASE: u64 = 0x1800_0000_0000;

/// The two exports' RVAs inside the provider (pinned by the layout
/// gate's Export Address Table reading); the loader's answers are
/// checked against `PROVIDER_IMAGE_BASE + RVA` computed independently
/// of the table walk.
const FN_RVA: u64 = 0x1000;
const MARK_RVA: u64 = 0x2000;

const CROSSMARK_VALUE: u64 = 0xACE5_F00D_D1CE_5EED;
const CROSSFN_VALUE: u64 = 0xFA57_1AE5;

/// The client program's two report slots (moved by keyboard? no -- the
/// program's own `.set` constants; the demo maps these pages private
/// and reads them back from the trap handler).
const SLOT_A: u64 = 0x4000_0000_0000; // *IAT-resolved crossmark (the data immediate)
const SLOT_B: u64 = 0x4000_0000_0008; // the resolved crossfn call's result

const USER_STACK_ADDR: u64 = 0x4000_0001_0000;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

/// Builds an address space, loads provider + client, drives the typed
/// negative controls, resolves the client's imports for real, states
/// the structural facts, and enters ring 3 at the client's entry
/// point. `on_trap` (vector 0x8C) reports the ring-3 results and
/// chains into `experimental::isolation_demo`, keeping the single
/// unified demo chain.
///
/// The whole increment's headline: a real Windows-format executable's
/// own code, calling a real Windows-format DLL's own exported code and
/// data, THROUGH their real import/export tables, both produced by the
/// ordinary toolchain -- the loader did nothing but what a Windows
/// loader does: map them and fill the IAT.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep already initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = FRAME_ALLOCATOR.as_mut().unwrap();

    let mut space = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build pe-import-demo address space"));
    space.activate();
    let mut mapper = space.mapper();

    // --- load the provider; parse its OWN export directory ---
    let prov = pe::load(PROVIDER_DLL, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the provider DLL: {:?}", e));
    let _ = prov; // entry unused; what matters is the mapped image
    let export = pe::DllExportTable::parse(PROVIDER_IMAGE_BASE, prov.image_max_rva)
        .unwrap_or_else(|e| panic!("provider export table failed to parse: {:?}", e));

    let declared = export
        .declared_name()
        .unwrap_or_else(|e| panic!("provider export table name failed: {:?}", e))
        .unwrap_or_else(|| panic!("provider export table declares no DLL name"));
    serial_println!(
        "  experimental pe-import-demo: provider loaded at its own ImageBase {:#x}; its export table declares {:?}",
        PROVIDER_IMAGE_BASE, declared
    );
    let dll_name_bytes = declared.as_slice();
    let expected_dll_name: &[u8] = b"userprog_pe_import_dll.dll";
    assert!(
        dll_name_bytes == expected_dll_name,
        "provider export table's declared DLL name does not match the layout-gate-pinned name"
    );

    // Fixture facts, stated every boot and checked against the export
    // table's OWN answers:
    serial_println!(
        "  experimental pe-import-demo: export table facts -- {} name(s), lookup(crossfn) = provider+{:#x}, lookup(crossmark) = provider+{:#x}",
        export.name_count(),
        export
            .lookup(b"crossfn")
            .unwrap_or_else(|| panic!("crossfn export lookup returned None"))
            .wrapping_sub(PROVIDER_IMAGE_BASE),
        export
            .lookup(b"crossmark")
            .unwrap_or_else(|| panic!("crossmark export lookup returned None"))
            .wrapping_sub(PROVIDER_IMAGE_BASE),
    );
    assert_eq!(export.name_count(), 2, "provider must export exactly the two fixture symbols");
    let crossfn_addr = export.lookup(b"crossfn").unwrap();
    let crossmark_addr = export.lookup(b"crossmark").unwrap();
    assert_eq!(crossfn_addr, PROVIDER_IMAGE_BASE + FN_RVA, "crossfn export address drift");
    assert_eq!(crossmark_addr, PROVIDER_IMAGE_BASE + MARK_RVA, "crossmark export address drift");
    let absent = export.lookup(b"crossball");
    assert!(absent.is_none(), "an absent symbol must answer None, typed as absent");
    serial_println!(
        "  experimental pe-import-demo: provider export lookup self-test: crossfn {:#x}, crossmark {:#x}, absent symbol None -- OK",
        crossfn_addr, crossmark_addr
    );

    // --- load the client WITHOUT imports, then run BOTH typed negative
    // controls on its real mapped import directory BEFORE real
    // resolution touches an IAT slot ---
    let app = pe::load(IMPORT_APP, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the import-dependent PE: {:?}", e));

    match pe::resolve_imports(APP_IMAGE_BASE, &mut mapper, app.image_max_rva, &pe::DllRegistry::new()) {
        Err(pe::PeError::MissingDllImport) => serial_println!(
            "  experimental pe-import-demo: negative control 1 -- EMPTY registry refused the import walk with the typed MissingDllImport -- OK"
        ),
        other => panic!(
            "negative control 1 failed: expected MissingDllImport, got {:?}",
            other.err()
        ),
    }

    // The wrong-file carrier: bind the INTERNALLY-declared DLL name to
    // the CLIENT's own (empty) export table -- the per-FILE identity
    // obstacle: same name, wrong file, zero exports -> per-symbol
    // refusal, before any patch.
    let client_exports = pe::DllExportTable::parse(APP_IMAGE_BASE, app.image_max_rva)
        .unwrap_or_else(|e| panic!("client export table probe failed: {:?}", e));
    assert!(client_exports.is_empty(), "the client exe must have NO export directory");
    assert_eq!(client_exports.name_count(), 0);
    let mut wrong_reg = pe::DllRegistry::new();
    wrong_reg.add(dll_name_bytes, Some(client_exports));
    match pe::resolve_imports(APP_IMAGE_BASE, &mut mapper, app.image_max_rva, &wrong_reg) {
        Err(pe::PeError::MissingImport) => serial_println!(
            "  experimental pe-import-demo: negative control 2 -- the DLL name bound to the WRONG FILE's empty export table refused with the typed MissingImport -- OK, the registry identity is the file, not the name"
        ),
        other => panic!(
            "negative control 2 failed: expected MissingImport, got {:?}",
            other.err()
        ),
    }

    // --- real resolution ---
    let mut reg = pe::DllRegistry::new();
    reg.add(dll_name_bytes, Some(export));
    let resolved = pe::resolve_imports(APP_IMAGE_BASE, &mut mapper, app.image_max_rva, &reg)
        .unwrap_or_else(|e| panic!("real import resolution failed: {:?}", e));

    // Exact per-symbol facts, address-located by SYMBOL (the
    // layout-drift rule): the first walk fact must be crossfn, the
    // second crossmark -- assert that, THEN check each value both
    // externally (independent export lookup) and internally (slot
    // readback):
    assert_eq!(resolved.facts.len(), 2, "exactly the two fixture imports must resolve");
    const CROSSFN: &[u8] = b"crossfn";
    const CROSSMARK: &[u8] = b"crossmark";
    assert!(resolved.facts[0].symbol.as_slice() == CROSSFN, "first import fact must be crossfn");
    assert!(resolved.facts[1].symbol.as_slice() == CROSSMARK, "second import fact must be crossmark");
    for f in resolved.facts.iter() {
        let expected_addr = export.lookup(f.symbol.as_slice());
        assert!(expected_addr.is_some());
        assert_eq!(
            f.value,
            expected_addr.unwrap(),
            "IAT slot value must equal the independently-computed export address"
        );
        let readback = core::ptr::read_volatile(f.slot as *const u64);
        assert_eq!(readback, f.value, "IAT slot readback mismatch");
    }
    serial_println!(
        "  experimental pe-import-demo: resolved 2 import(s) through the REAL import directory -- OK: each slot's value == the independent export lookup, and reads back unchanged"
    );
    let slot_fn = &resolved.facts[0];
    let slot_mark = &resolved.facts[1];
    serial_println!(
        "  experimental pe-import-demo: IAT slot for {:?} at {:#x} == {:#x} (crossfn, the provider's .text); slot for {:?} at {:#x} == {:#x} (crossmark, the provider's .data) -- OK",
        slot_fn.symbol, slot_fn.slot, slot_fn.value, slot_mark.symbol, slot_mark.slot, slot_mark.value
    );

    // The resolved data export is a REAL mapped address: dereference it
    // in kernel space BEFORE any ring-3 run.
    let marked = core::ptr::read_volatile(crossmark_addr as *const u64);
    assert_eq!(marked, CROSSMARK_VALUE, "provider's crossmark data does not carry its immediate");
    serial_println!(
        "  experimental pe-import-demo: resolved crossmark address dereferences to {:#x} (expected {:#x}, the provider's own .data immediate) -- OK",
        marked, CROSSMARK_VALUE
    );

    // Private report-slot pages + a stack for the client.
    let stack_flags = PageTableFlags::PRESENT
        | PageTableFlags::WRITABLE
        | PageTableFlags::USER_ACCESSIBLE
        | PageTableFlags::NO_EXECUTE;
    for (base, pages) in [(SLOT_A, 1u64), (USER_STACK_ADDR, 1u64)] {
        let mapped = paging::map_range(
            &mut mapper,
            fa,
            VirtAddr::new(base),
            pages,
            stack_flags,
        );
        assert_eq!(mapped, pages, "failed to map pe-import-demo pages at {:#x}", base);
    }

    core::mem::forget(space); // no teardown for this demo -- see teardown_demo

    serial_println!(
        "  experimental pe-import-demo: kernel-side proof complete; entering the imported client (ring 3) which dereferences crossmark through its resolved IAT slot and calls crossfn through its own"
    );
    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(app.entry_point, user_rsp)
}

/// Called from `idt::init`'s installed handler on the one trap the
/// client's code issues (`int $0x8D`; 0x8C is already the plt-demo
/// completion gate -- vector collisions overwrite silently, so the
/// next free slot is the hard rule here). Verifies BOTH results the
/// faithful Windows import pattern produced, then chains into
/// `experimental::isolation_demo`.
pub unsafe fn on_trap() -> ! {
    let via_pointer = core::ptr::read_volatile(SLOT_A as *const u64);
    let via_call = core::ptr::read_volatile(SLOT_B as *const u64);
    let ok = via_pointer == CROSSMARK_VALUE && via_call == CROSSFN_VALUE;
    serial_println!(
        "  experimental pe-import-demo self-test: imported data read {:#x} through the resolved IAT slot (expected {:#x}); imported function returned {:#x} (expected {:#x}) -- {}",
        via_pointer,
        CROSSMARK_VALUE,
        via_call,
        CROSSFN_VALUE,
        if ok {
            "OK, a real Windows-format executable's own imports resolved through a real Windows-format DLL's own export table (GetProcAddress), and its own code used them in ring 3"
        } else {
            "MISMATCH"
        }
    );

    let fa = FRAME_ALLOCATOR.take().unwrap();
    isolation_demo::start(fa)
}
