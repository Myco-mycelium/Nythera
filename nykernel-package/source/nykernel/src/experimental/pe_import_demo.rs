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
//! delay-load, no Win32 API surface. Forwarded exports, ordinal
//! imports, and BOUND IMPORTS were all in this list and are closed
//! now -- the bound halves get their own round: the honor path (a
//! fresh-stamp entry whose pre-filled IAT values are verified against
//! a fresh resolution and then left UN-written), the stale-discard
//! path (a stamp+1 entry whose pre-bound claim is discarded and
//! replaced by the registry's fresh forwarder chase), an inert
//! unregistered entry, and the typed stamp/IAT-contradiction refusal
//! -- the directory composed by the same checked-patch discipline
//! (`tools/patch_pe_bound.py`). The forwarder-host provider's own EAT
//! entries are directory-internal
//! forwarder STRING references (composed deliberately and
//! self-verified by `tools/patch_pe_forwarders.py`, the same
//! checked-patch discipline the weak-VERNEED artifact used), the
//! chase lands the client's third import on the PROVIDER's crossfn
//! address, and crossmark's own thunk is composed ordinal-form
//! (OrdinalBase taken from the provider's real export directory, not
//! assumed) -- both with typed negative controls run every boot. A
//! real Windows-format pair's own import/export machinery, driven by
//! the loader, is what ends this boot.

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
/// The FORWARDER HOST: a second real Windows-format provider whose
/// THREE exports start as ordinary ld-emitted stubs and whose EAT
/// entries the checked build-time patcher composes into 2 forwarded-
/// export STRING references plus one unregistered-DLL string (and one
/// self-cycling one); see `tools/patch_pe_forwarders.py`.
static FWDPROV_DLL: &[u8] = include_bytes!("../../userprog/userprog_pe_import_fwdprov.dll");
static IMPORT_APP: &[u8] = include_bytes!("../../userprog/userprog_pe_import_app.exe");
/// The BOUND-IMPORT negative-control carrier: a real Windows-format
/// executable whose ONLY import is crossfn_fwd THROUGH the forwarder
/// host, and whose composed bound-import directory declares a fresh
/// descriptor stamp (the forwarder HOST's own .edata stamp, read by
/// the checked patcher) plus ONE ALL-FRESH forwarder ref naming the
/// primary provider, while its single IAT slot carries a deliberately
/// WRONG bound value. The honor path is gated ON (descriptor and ref
/// both fresh), so the loader verifies the slot against a fresh
/// resolution -- which is the FORWARDER-CHASE target (the primary
/// provider's crossfn) -- sees the contradiction and must refuse with
/// the typed `BoundValueCorrupt` naming both halves. The file is
/// never executed; only its mapped import/bound surfaces are probed.
/// See `userprog/prog_pe_bound_bad.s` and `tools/patch_pe_bound.py`.
static BOUND_BAD_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_bound_bad.exe");
/// The STALE-REF carrier: byte-for-byte the bad client's composition
/// EXCEPT one composed u32 -- the forwarder ref's stamp is the
/// provider's stamp PLUS ONE (a stamp no registered file carries).
/// The honor gate flips OFF by the ref alone: the SAME wrong slot must
/// NOT honor and NOT refuse -- it is DISCARDED (typed
/// `StaleRefDiscarded`) and resolved fresh through the same
/// forwarder chase, landing on the same chased address the bad
/// client's refusal named. The bad/refstale pair is the ref-aware
/// ruling's refuse-vs-discard demonstration. See
/// `userprog/prog_pe_bound_refstale.s`.
static BOUND_REFSTALE_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_bound_refstale.exe");
/// The TWO-REF refusal carrier: the bad client's composition with the
/// descriptor's ref list grown to two -- the PRIMARY PROVIDER then the
/// FORWARDER HOST itself, both declaring their OWN real stamps. The
/// walk must consume BOTH (the composed
/// `NumberOfModuleForwarderRefs` field, not a one-ref assumption):
/// all-fresh gate ON, so the same wrong slot must be REFUSED with the
/// typed `BoundValueCorrupt` exactly as in the one-ref case. See
/// `userprog/prog_pe_bound_ref2bad.s`.
static BOUND_REF2BAD_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_bound_ref2bad.exe");
/// The TWO-REF stale carrier: byte-for-byte the ref2bad carrier's
/// composition except ONE composed u32 -- the SECOND ref's stamp is
/// the host's stamp PLUS ONE. The gate flips OFF by the second ref
/// alone: the same wrong slot is DISCARDED (typed `StaleRef-
/// Discarded`) and resolved fresh through the same chase. One LATER
/// ref stamp byte decides refuse-vs-discard across a TWO-ref chain.
/// See `userprog/prog_pe_bound_ref2stale.s`.
static BOUND_REF2STALE_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_bound_ref2stale.exe");

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
/// The forwarder host's own private base (PML4 index 0x1c -- outside
/// the shared identity subtree, same rule as the pair's bases).
const FWDPROV_IMAGE_BASE: u64 = 0x1c00_0000_0000;
/// The bad client's own fixed ImageBase (--image-base on its link;
/// PML4 index 0x40, outside the shared identity subtree and outside
/// every other demo's private subtree, same rule as the pair's bases).
const BAD_IMAGE_BASE: u64 = 0x2000_0000_0000;
/// The stale-ref carrier's own fixed ImageBase (--image-base on its
/// link; PML4 index 0x60, a private fresh subtree, same rule as every
/// other PE base).
const REFSTALE_IMAGE_BASE: u64 = 0x3000_0000_0000;
/// The two-ref refusal carrier's own fixed ImageBase (--image-base on
/// its link; PML4 index 0xA0, a private fresh subtree, same rule as
/// every other PE base).
const REF2BAD_IMAGE_BASE: u64 = 0x5000_0000_0000;
/// The two-ref stale carrier's own fixed ImageBase (--image-base on
/// its link; PML4 index 0xC0, a private fresh subtree, same rule as
/// every other PE base).
const REF2STALE_IMAGE_BASE: u64 = 0x6000_0000_0000;
/// The WRONG bound value `tools/patch_pe_bound.py` composes into the
/// bad client's single IAT slot (the patcher's WRONG constant,
/// mirrored here; its own re-parse guards the file side, and the
/// refusal below asserts the exact contradicting pair).
const BOUND_WRONG_VALUE: u64 = 0xDEAD_BEEF_0000_1000;

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
/// The forwarder-resolved import's report slot: lives in SLOT_A's own
/// mapped page (one 4 KiB mapping covers all three), which is exactly
/// what the slot-constant gate pin asserts
/// (`SLOT_C == SLOT_A + 16`).
const SLOT_C: u64 = 0x4000_0000_0010; // the forwarder-resolved crossfn_fwd call's result

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

    // --- load the FORWARDER HOST (a second real provider) and parse
    // its OWN export directory. The checked composition's facts are
    // real toolchain-observable output too: a plain `objdump -p` on
    // the embedded file prints its EAT entries as `Forwarder RVA`
    // lines, so the kernel reading them is reading what the file
    // truly contains. ---
    let fwdprov = pe::load(FWDPROV_DLL, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the forwarder-host DLL: {:?}", e));
    let _ = fwdprov;
    let fwd_export = pe::DllExportTable::parse(FWDPROV_IMAGE_BASE, fwdprov.image_max_rva)
        .unwrap_or_else(|e| panic!("forwarder-host export table failed to parse: {:?}", e));
    let fwd_declared = fwd_export
        .declared_name()
        .unwrap_or_else(|e| panic!("forwarder-host export table name failed: {:?}", e))
        .unwrap_or_else(|| panic!("forwarder-host export table declares no DLL name"));
    assert!(
        fwd_declared.as_slice() == b"userprog_pe_import_fwdprov.dll",
        "forwarder host must declare its own pinned DLL name"
    );
    assert_eq!(
        fwd_export.name_count(),
        3,
        "the forwarder host must carry exactly the three composed exports"
    );
    serial_println!(
        "  experimental pe-import-demo: forwarder host loaded at its own ImageBase {:#x}; its export table declares {:?} with {} name(s), every EAT entry a directory-internal forwarder -- OK",
        FWDPROV_IMAGE_BASE,
        fwd_declared,
        fwd_export.name_count()
    );

    // --- load the client WITHOUT imports, then run BOTH typed negative
    // controls on its real mapped import directory BEFORE real
    // resolution touches an IAT slot ---
    let app = pe::load(IMPORT_APP, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the import-dependent PE: {:?}", e));
    // The bound-refusal carrier loads NEXT TO the good client (its own
    // private PML4 subtree); only its import/bound surfaces are probed,
    // its entry point is never entered.
    let bad = pe::load(BOUND_BAD_CLIENT, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the bound-refusal PE: {:?}", e));
    let refstale = pe::load(BOUND_REFSTALE_CLIENT, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the stale-ref PE: {:?}", e));
    let ref2bad = pe::load(BOUND_REF2BAD_CLIENT, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the two-ref bound-refusal PE: {:?}", e));
    let ref2stale = pe::load(BOUND_REF2STALE_CLIENT, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the two-ref stale PE: {:?}", e));

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

    // --- typed refusals of the NEW halves, against the FULL registry
    // (both provider tables registered, the same registry the real
    // resolution runs on): two forwarder controls and two ordinal
    // checks, every one of them typed, permanent, every boot ---
    let mut reg = pe::DllRegistry::new();
    reg.add(dll_name_bytes, Some(export));
    reg.add(fwd_declared.as_slice(), Some(fwd_export));
    // (a) a forwarder chain that only ever names ITSELF again never
    // resolves -- the depth cap's typed refusal:
    match reg.resolve_in(&fwd_export, Some(b"crossfn_cycle"), None) {
        Err(pe::PeError::ForwarderDepthExceeded) => serial_println!(
            "  experimental pe-import-demo: negative control 3 -- a SELF-CYCLING forwarded export refused at the depth cap with the typed ForwarderDepthExceeded -- OK"
        ),
        other => panic!(
            "negative control 3 failed: expected ForwarderDepthExceeded, got {:?}",
            other.err()
        ),
    }
    // (b) a forwarder string naming a DLL the registry has no entry
    // for: the name-vs-file ruling applies to forwarders exactly as
    // it applies to import descriptors:
    match reg.resolve_in(&fwd_export, Some(b"crossfn_nodll"), None) {
        Err(pe::PeError::MissingDllImport) => serial_println!(
            "  experimental pe-import-demo: negative control 4 -- a forwarded export naming an UNREGISTERED DLL refused with the typed MissingDllImport -- OK"
        ),
        other => panic!(
            "negative control 4 failed: expected MissingDllImport, got {:?}",
            other.err()
        ),
    }
    // (c) an ordinal beyond the provider's EAT answers TYPED-ABSENT
    // (Ok(None)), never a guess:
    let far_out = reg
        .resolve_in(&export, None, Some(99))
        .expect("an out-of-range ordinal must answer, not error");
    assert!(far_out.is_none(), "an out-of-range ordinal must answer None");
    // (d) the same probe against the client's OWN (empty) export
    // table -- every EAT-indexed lookup through a no-directory file is
    // absent too:
    let empty_ord = reg
        .resolve_in(&client_exports, None, Some(1))
        .expect("an empty-table ordinal must answer, not error");
    assert!(empty_ord.is_none(), "an empty-table ordinal must answer None");
    serial_println!(
        "  experimental pe-import-demo: ordinal out-of-range / empty-table probes answer typed-None, never a guess -- OK"
    );

    // Negative control 5: the BAD client's bound IMPORT directory
    // declares a FRESH stamp -- it names the provider and carries the
    // provider's OWN ReadDateStamp -- while its single FirstThunk slot
    // carries the composed contradicting value. The honor path trusts
    // the stamp ONLY until it verifies the slot, then refuses with the
    // typed BoundValueCorrupt, naming BOTH the contradicted bound
    // value and the fresh resolution that contradicts it. The claim
    // this pins: stamps are VERIFIED, never trusted blindly -- a
    // wrong stamped slot can never silently become a wrong call
    // target.
    match pe::resolve_imports(BAD_IMAGE_BASE, &mut mapper, bad.image_max_rva, &reg) {
        Err(pe::PeError::BoundValueCorrupt(wrong, fresh)) => {
            assert_eq!(
                wrong, BOUND_WRONG_VALUE,
                "the refused bound value must be the composed contradiction"
            );
            assert_eq!(
                fresh,
                PROVIDER_IMAGE_BASE + FN_RVA,
                "the contradicting fresh resolution must be the FORWARDER CHASE's answer -- the primary provider's crossfn"
            );
            serial_println!(
                "  experimental pe-import-demo: negative control 5 -- a fresh-stamped descriptor with an ALL-FRESH forwarder ref whose IAT slot contradicts the chased answer refused with the typed BoundValueCorrupt ({:#x} != {:#x}) -- OK, stamps are verified THROUGH the forwarder chains too, never trusted blindly",
                wrong, fresh
            );
        }
        other => panic!(
            "negative control 5 failed: expected BoundValueCorrupt, got {:?}",
            other.err()
        ),
    }

    // Negative control 6 (the ref-aware ruling's gate, byte for
    // byte): the REFSTALE carrier is the bad client's composition
    // EXCEPT ONE ref stamp -- provider's + 1, a stamp no registered
    // file carries. The descriptor's own stamp is still fresh, so the
    // gate flips OFF by the ref alone: the SAME wrong slot must NOT
    // honor and NOT refuse -- it is DISCARDED (typed StaleRef-
    // Discarded, coarse per-descriptor, the Windows re-snap semantic)
    // and resolved fresh through the same forwarder chase, landing on
    // the same chased address the bad client's refusal carried. One
    // composed stamp byte decides refuse-vs-discard.
    match pe::resolve_imports(REFSTALE_IMAGE_BASE, &mut mapper, refstale.image_max_rva, &reg) {
        Ok(res2) => {
            assert_eq!(
                res2.facts.len(),
                1,
                "the stale-ref carrier carries exactly one import"
            );
            let fact_rs = &res2.facts[0];
            assert!(
                matches!(fact_rs.origin, pe::ImportOrigin::StaleRefDiscarded),
                "a fresh descriptor with a STALE forwarder ref must DISCARD through the ref flavor"
            );
            assert_eq!(
                fact_rs.prior, BOUND_WRONG_VALUE,
                "the discarded bound value is the SAME wrong value the bad client refused"
            );
            assert_eq!(
                fact_rs.value,
                PROVIDER_IMAGE_BASE + FN_RVA,
                "the fresh resolution is the same forwarder chase the bad client's refusal named"
            );
            assert_eq!(
                res2.bound_modules.len(),
                1,
                "the stale-ref carrier's bound directory has exactly the one entry"
            );
            let bm = &res2.bound_modules[0];
            assert!(
                bm.name.as_slice() == b"userprog_pe_import_fwdprov.dll",
                "the stale-ref entry must name the forwarder host"
            );
            assert_eq!(
                bm.stamp,
                fwd_export.time_date_stamp(),
                "the stale-ref descriptor's OWN stamp must be the forwarder host's real stamp (fresh) -- the gate flips OFF by the ref alone"
            );
            assert_eq!(
                bm.forwarder_refs.len(),
                1,
                "exactly one composed forwarder ref"
            );
            assert!(
                bm.forwarder_refs[0].0.as_slice() == b"userprog_pe_import_dll.dll",
                "the stale ref must name the primary provider"
            );
            assert_eq!(
                bm.forwarder_refs[0].1,
                export.time_date_stamp().wrapping_add(1),
                "the stale ref's stamp must be the provider's + 1: a stamp NO registered file carries"
            );
            serial_println!(
                "  experimental pe-import-demo: negative control 6 -- the REFSTALE carrier (one ref stamp byte beyond the bad client's) DISCARDED the same wrong slot ({:#x}) and resolved it fresh through the same chase ({:#x}); the descriptor's own stamp stayed fresh ({:#x}) and the ref declared {:#x} -- OK, one ref stamp byte decides refuse-vs-discard",
                fact_rs.prior,
                fact_rs.value,
                bm.stamp,
                bm.forwarder_refs[0].1
            );
        }
        other => panic!(
            "negative control 6 failed: expected the stale-ref binding to be DISCARDED and resolved, got {:?}",
            other.err()
        ),
    }

    // Negative control 7 (the multi-hop count boundary, probed by
    // REFUSAL): the REF2BAD carrier composes the SAME fresh
    // descriptor but with TWO forwarder refs -- the primary provider
    // and then the FORWARDER HOST ITSELF, both declaring their own
    // modules' real stamps. If the walk consumed refs by a one-ref
    // assumption instead of the composed NumberOfModuleForwarder-
    // Refs count, this directory would not parse at the described
    // stamps at all; parsed, the all-fresh surface gates the honor
    // path ON purely by stamps, so the per-slot VALUE verification
    // (which runs the forwarder chase) must refuse with the typed
    // BoundValueCorrupt -- the refusal is SEMANTIC through a two-ref
    // chain, never stamp-blind.
    match pe::resolve_imports(REF2BAD_IMAGE_BASE, &mut mapper, ref2bad.image_max_rva, &reg) {
        Err(pe::PeError::BoundValueCorrupt(wrong2, fresh2)) => {
            assert_eq!(
                wrong2, BOUND_WRONG_VALUE,
                "the two-ref refusal must name the composed wrong bound value"
            );
            assert_eq!(
                fresh2,
                PROVIDER_IMAGE_BASE + FN_RVA,
                "the two-ref refusal's fresh half must be the forwarder chase the two FRESH refs verified"
            );
            // The refusal reaching the slot-verify at all proves the
            // count-driven parse consumed BOTH refs (a one-ref
            // assumption would have misparsed the composed directory
            // before any stamp check). The two-ref PARSE facts
            // (names, order, both stamps) are pinned from real bytes
            // by control 8's Ok path and file-side by the gate.
            serial_println!(
                "  experimental pe-import-demo: negative control 7 -- the REF2BAD carrier (TWO refs, BOTH FRESH) refused through the two-ref chain with the typed BoundValueCorrupt ({:#x} != {:#x}) -- OK, the count made the walk unambiguous and the fresh surface still verified the slot THROUGH both refs",
                wrong2,
                fresh2
            );
        }
        other => panic!(
            "negative control 7 failed: expected BoundValueCorrupt through two FRESH refs, got {:?}",
            other.err()
        ),
    }

    // Negative control 8 (the multi-hop flip, one ref byte again):
    // the REF2STALE carrier is the ref2bad carrier's composition
    // except the SECOND ref's stamp -- the HOST's stamp + 1, a stamp
    // no registered file carries. The FIRST ref is still fresh (the
    // same patch bytes the ref2bad carrier carries), so the gate
    // flipping OFF by a LATER ref proves per-ref iteration: the same
    // wrong slot must NOT honor and NOT refuse -- it is DISCARDED
    // (typed StaleRefDiscarded) and resolved fresh through the same
    // chase, landing on the same chased address control 7's refusal
    // named.
    match pe::resolve_imports(REF2STALE_IMAGE_BASE, &mut mapper, ref2stale.image_max_rva, &reg) {
        Ok(res3) => {
            assert_eq!(
                res3.facts.len(),
                1,
                "the stale second-ref carrier carries exactly one import"
            );
            let fact_rs2 = &res3.facts[0];
            assert!(
                matches!(fact_rs2.origin, pe::ImportOrigin::StaleRefDiscarded),
                "a later STALE forwarder ref must DISCARD through the ref flavor"
            );
            assert_eq!(
                fact_rs2.prior, BOUND_WRONG_VALUE,
                "the discarded bound value is the same wrong value control 7 refused"
            );
            assert_eq!(
                fact_rs2.value,
                PROVIDER_IMAGE_BASE + FN_RVA,
                "the fresh resolution is the same forwarder chase control 7's refusal named"
            );
            let bm3 = &res3.bound_modules[0];
            assert_eq!(
                bm3.stamp,
                fwd_export.time_date_stamp(),
                "the second-ref carrier's descriptor stamp stayed FRESH -- the flip is by the ref alone"
            );
            assert_eq!(
                bm3.forwarder_refs.len(),
                2,
                "exactly the composed TWO forwarder refs must parse even when the ruling discards"
            );
            assert!(
                bm3.name.as_slice() == b"userprog_pe_import_fwdprov.dll",
                "the two-ref entry must name the forwarder host"
            );
            assert!(
                bm3.forwarder_refs[0].0.as_slice() == b"userprog_pe_import_dll.dll"
                    && bm3.forwarder_refs[1].0.as_slice() == b"userprog_pe_import_fwdprov.dll",
                "the two refs must name the provider and then the host, in composed order"
            );
            assert_eq!(
                bm3.forwarder_refs[0].1,
                export.time_date_stamp(),
                "the FIRST ref stays FRESH in the stale second-ref carrier"
            );
            assert_eq!(
                bm3.forwarder_refs[1].1,
                fwd_export.time_date_stamp().wrapping_add(1),
                "the SECOND ref's stamp must be the host's + 1: a stamp NO registered file carries"
            );
            serial_println!(
                "  experimental pe-import-demo: negative control 8 -- the REF2STALE carrier (same two refs, only the SECOND's stamp moved to {:#x}) DISCARDED the same wrong slot ({:#x}) and resolved fresh through the same chase ({:#x}); the first ref stayed fresh ({:#x}) -- OK, a LATER ref stamp byte decides refuse-vs-discard across a TWO-ref chain",
                bm3.forwarder_refs[1].1,
                fact_rs2.prior,
                fact_rs2.value,
                bm3.forwarder_refs[0].1
            );
        }
        other => panic!(
            "negative control 8 failed: expected the stale second-ref binding to be DISCARDED and resolved, got {:?}",
            other.err()
        ),
    }

    // --- the client's ordinals and forwarders come through the walk
    // with the SAME positive facts the named path gets ---
    let resolved = pe::resolve_imports(APP_IMAGE_BASE, &mut mapper, app.image_max_rva, &reg)
        .unwrap_or_else(|e| panic!("real import resolution failed: {:?}", e));

    // Per-symbol facts, located by SYMBOL (the layout-drift rule):
    // crossfn (named form), crossmark (composed ORDINAL form; the
    // label is `ord2` = OrdinalBase 1 + crossmark's EAT index 1), and
    // crossfn_fwd (named through the forwarder host). The loop's
    // readback check runs for every fact.
    assert_eq!(resolved.facts.len(), 3, "exactly the three fixture imports must resolve");
    const CROSSFN: &[u8] = b"crossfn";
    const CROSSMARK_ORDINAL: &[u8] = b"ord2";
    const CROSSFN_FWD: &[u8] = b"crossfn_fwd";
    let fact_fn = resolved
        .facts
        .iter()
        .find(|f| f.symbol.as_slice() == CROSSFN)
        .expect("the named crossfn fact must be present");
    let fact_ord = resolved
        .facts
        .iter()
        .find(|f| f.symbol.as_slice() == CROSSMARK_ORDINAL)
        .expect("the ordinal-form crossmark fact (ord2) must be present");
    let fact_fwd = resolved
        .facts
        .iter()
        .find(|f| f.symbol.as_slice() == CROSSFN_FWD)
        .expect("the forwarder-resolved crossfn_fwd fact must be present");
    // The ORDINAL path must answer what the NAMED path answers (the
    // independent named lookup was computed above, before any patch
    // semantic could have leaked into it):
    assert_eq!(
        fact_ord.value, crossmark_addr,
        "the ordinal-form import must resolve to the same crossmark address the named form computes (OrdinalBase semantics, not an index guess)"
    );
    // The forwarder chase must land on the PROVIDER's own crossfn:
    // the forwarder host's own stub body is NEVER executed by it.
    assert_eq!(
        fact_fwd.value, crossfn_addr,
        "the forwarder-resolved import must carry the PROVIDER's crossfn address, not the forwarder host's stub"
    );

    // -- the BOUND-IMPORT facts, per origin (this round's own halves): --
    // honored slots are LEFT UN-written (the whole point of binding):
    // their prior value must equal the verified resolution; the stale
    // slot's prior is the composed contradiction and its value is the
    // fresh answer that replaced it.
    assert!(
        matches!(fact_fn.origin, pe::ImportOrigin::BoundHonored),
        "the fresh-stamped provider entry must HONOR its pre-filled slots"
    );
    assert!(
        matches!(fact_ord.origin, pe::ImportOrigin::BoundHonored),
        "the ordinal-form crossmark lives in the same honored descriptor"
    );
    assert!(
        matches!(fact_fwd.origin, pe::ImportOrigin::StaleDiscarded),
        "the stamp+1 fwdprov entry must be DISCARDED, never honored"
    );
    assert_eq!(
        fact_fn.prior, fact_fn.value,
        "an honored slot's pre-existing value is verified against fresh resolution, never rewritten"
    );
    assert_eq!(
        fact_ord.prior, fact_ord.value,
        "the honored ordinal-form prefill is likewise verified, not rewritten"
    );
    assert_eq!(
        fact_fwd.prior,
        PROVIDER_IMAGE_BASE + MARK_RVA,
        "the stale entry's discarded bound value must be the composed contradiction (crossmark's address in the crossfn slot)"
    );
    assert_ne!(
        fact_fwd.prior, fact_fwd.value,
        "the stale bound value must have been replaced by the fresh forwarder chase"
    );
    // The bound directory's parsed module surface: exactly the three
    // composed entries; nobody.dll is the inert one (no import
    // descriptor names it); both stamps must be the composed pair
    // (fresh = the provider's own parsed stamp, stale = fresh + 1 --
    // a stamp NO file carries, contradicting the actual mapping).
    assert_eq!(
        resolved.bound_modules.len(),
        3,
        "exactly the three composed bound modules must parse"
    );
    let bound_fresh = resolved
        .bound_modules
        .iter()
        .find(|m| m.name.as_slice() == b"userprog_pe_import_dll.dll")
        .expect("the provider's bound entry must parse");
    let bound_stale = resolved
        .bound_modules
        .iter()
        .find(|m| m.name.as_slice() == b"userprog_pe_import_fwdprov.dll")
        .expect("the fwdprov bound entry must parse");
    assert!(
        resolved
            .bound_modules
            .iter()
            .any(|m| m.name.as_slice() == b"nobody.dll"),
        "the inert unregistered entry must parse"
    );
    assert_eq!(
        bound_fresh.stamp,
        export.time_date_stamp(),
        "the fresh entry's stamp must be the provider's OWN parsed stamp (two parsers, one file)"
    );
    assert_eq!(
        bound_stale.stamp,
        export.time_date_stamp().wrapping_add(1),
        "the stale entry's stamp must be fresh + 1: a stamp NO registered provider carries"
    );
    // Neither mid-app descriptor carries forwarder refs: their ref
    // lists must parse EMPTY (the ref-aware kernel path reached these
    // descriptors too, and found zero refs in each -- composed a
    // different way than absent). The refstale carrier (below) is the
    // one that owns the non-empty ref-list facts.
    assert!(
        bound_fresh.forwarder_refs.is_empty(),
        "the fresh descriptor must parse with zero forwarder refs"
    );
    assert!(
        bound_stale.forwarder_refs.is_empty(),
        "the stale descriptor must parse with zero forwarder refs"
    );
    serial_println!(
        "  experimental pe-import-demo: BOUND-IMPORT facts -- provider's fresh-stamp entry: 2 slot(s) HONORED (prefills verified == fresh resolution, slots left un-written); the stale entry (stamp = provider's + 1, a stamp no file carries): its pre-bound contradiction {:#x} DISCARDED and resolved fresh to {:#x}; nobody.dll's inert entry parsed and ignored -- OK",
        fact_fwd.prior, fact_fwd.value
    );
    for f in resolved.facts.iter() {
        let readback = core::ptr::read_volatile(f.slot as *const u64);
        assert_eq!(readback, f.value, "IAT slot readback mismatch");
    }
    serial_println!(
        "  experimental pe-import-demo: resolved 3 import(s) through the REAL import directories -- OK: named crossfn -> {:#x} and ordinal-form crossmark (`ord2`) -> {:#x} both HONORED by stamp (verified prefills, left un-written); forwarder crossfn_fwd -> {:#x} (the PROVIDER's .text, chased through the forwarder host's export entry after its stale pre-binding was DISCARDED); every slot reads back its (verified or written) value",
        fact_fn.value, fact_ord.value, fact_fwd.value
    );
    serial_println!(
        "  experimental pe-import-demo: IAT slots -- crossfn {:#x}, ordinal crossmark {:#x}, forwarder crossfn_fwd {:#x} (slot {}; slot {}; slot {}) -- OK",
        fact_fn.value, fact_ord.value, fact_fwd.value,
        fact_fn.slot, fact_ord.slot, fact_fwd.slot
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
    // The third slot is the FORWARDER-resolved import's result: the
    // same slots rule as A/B -- one private page, constants pinned by
    // the slot-constant gate pin.
    let via_fwd = core::ptr::read_volatile(SLOT_C as *const u64);
    let ok = via_pointer == CROSSMARK_VALUE && via_call == CROSSFN_VALUE && via_fwd == CROSSFN_VALUE;
    serial_println!(
        "  experimental pe-import-demo self-test: imported data read {:#x} through the resolved IAT slot (expected {:#x}); imported function returned {:#x} (expected {:#x}); forwarder-resolved import returned {:#x} (expected {:#x}, the PROVIDER's own code executed through the forwarder host's export entry) -- {}",
        via_pointer,
        CROSSMARK_VALUE,
        via_call,
        CROSSFN_VALUE,
        via_fwd,
        CROSSFN_VALUE,
        if ok {
            "OK, a real Windows-format executable's own imports -- named, ordinal, and forwarder-chased -- all resolved through real Windows-format export tables (GetProcAddress semantics), and its own code used them in ring 3"
        } else {
            "MISMATCH"
        }
    );

    let fa = FRAME_ALLOCATOR.take().unwrap();
    isolation_demo::start(fa)
}
