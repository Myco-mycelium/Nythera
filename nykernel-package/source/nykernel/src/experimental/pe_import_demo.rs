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
//! The delay-load directory has its own three families (controls 9-18,
//! each in a non-inlined frame): the EAGER resolution ruling with its
//! two typed refusals, the structural refusals (unknown `grAttrs`,
//! broken `INT == IAT`, pre-VC7 VA form below the base, and a genuine
//! VA-form descriptor in a relocated image), and the two EXCLUSIVE
//! caps -- a 17-descriptor table and a 65-entry thunk array refused
//! with their exact payloads while every entry within the cap is
//! already resolved, plus the legal maxima at once (16 descriptors,
//! 64 thunks in the first) ACCEPTED and resolved completely. The
//! carriers are composed by `tools/patch_pe_delay.py` and re-parsed
//! before the build is allowed to continue.
//!
//! Deliberate scope (same honest posture as `pe.rs`'s doc): no Win32
//! API surface. Forwarded exports, ordinal imports, DELAY-LOAD
//! IMPORTS and BOUND IMPORTS were all in this list and are closed
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
/// The DELAY-LOAD carriers (patch_pe_delay.py): ordinary toolchain-raw
/// no-import PE32+ clients whose composed `IMAGE_DELAYLOAD_DESCRIPTOR`
/// surfaces are probed by `pe::resolve_delay_imports` at every boot:
/// the GOOD carrier eagerly resolves both its delay imports (named
/// crossfn, ordinal crossmark) through the same registry the normal
/// walk uses; the BAD carrier declares `nowhere.dll` (typed
/// MissingDelayDllImport refusal); the MUT carrier declares the
/// provider with dwTimeStamp = provider stamp + 1 (the old-BIND
/// marker -> typed MalformedDelayImport refusal). See
/// `userprog/prog_pe_delay.s`.
static DELAY_GOOD_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_delay_good.exe");
static DELAY_BAD_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_delay_bad.exe");
static DELAY_MUT_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_delay_mut.exe");
/// The three STRUCTURAL-refusal carriers, each the canonical composition
/// with exactly ONE property broken: ATTRS carries `grAttrs = 0x3` (an
/// undefined bit next to dlattrRva), MIS carries an IAT[0] that
/// contradicts its INT[0] (broken unbound pre-state), VA carries
/// `grAttrs = 0` (pre-VC7 VA form) with the fields left as RVAs, which
/// are far below the image's own preferred base. Each drives one
/// `MalformedDelayImport` reason the kernel reports -- code-verified
/// reasons are claims until a composed file makes the boot refuse them.
static DELAY_ATTRS_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_delay_attrs.exe");
static DELAY_MIS_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_delay_mis.exe");
static DELAY_VA_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_delay_va.exe");
/// The GENUINELY VA-form carrier: fields composed as absolute VAs of its
/// own (LOW) preferred ImageBase -- a descriptor field is 32 bits, so
/// only an image below 4 GiB can carry the pre-VC7 form at all. It is
/// loaded at a BIAS, where every baked VA is stale by construction: the
/// one case the descriptor-level relocated-image ruling exists for.
static DELAY_VABIAS_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_delay_vabias.exe");
/// The CAP-BOUNDARY carriers: the walk's descriptor-array (16) and
/// thunk-array (64) caps are EXCLUSIVE -- the zero terminator is read
/// BEFORE the cap is consulted, so the legal maxima must be ACCEPTED
/// and only a real 17th descriptor / 65th thunk entry refused. Both
/// sides get a file, because a refusal alone cannot be distinguished
/// from a cap checked before the terminator read (which would refuse
/// the 17th identically while wrongly refusing a legal 16). `MANY`
/// carries 17 descriptors, `THUNKS` one descriptor with 65 entries,
/// `FULL` the two legal maxima at once (16 descriptors, the first with
/// exactly 64 thunks). Their compositions need more room than
/// `.idata`'s FileAlignment-bounded padding, so
/// `tools/patch_pe_delay.py` extends the section's raw span under
/// checks it re-parses.
static DELAY_MANY_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_delay_many.exe");
static DELAY_THUNKS_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_delay_thunks.exe");
static DELAY_FULL_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_delay_full.exe");

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
/// The DELAY-LOAD carriers' own fixed ImageBases (--image-base on
/// each link; private fresh subtrees, same rule as every other PE
/// base). BAD and MUT are pinned BELOW bit 47 deliberately: the first
/// failed control-10 boot proved a load image at 0x8000_0000_0000 is
/// NON-CANONICAL in 4-level x86-64 (bit 47 set, bits 63:48 clear) --
/// every byte read of it #GP(0)s, and QEMU's CR2 (0xffff800000000000)
/// named the tentatively-canonical pointer the #GP's UB side effect
/// canonicalizes to. A load base in the canonical hole is not a
/// corner case to tolerate; no image may ever be linked there.
const DELAYGOOD_IMAGE_BASE: u64 = 0x7000_0000_0000;
const DELAYBAD_IMAGE_BASE: u64 = 0x4100_0000_0000;
const DELAYMUT_IMAGE_BASE: u64 = 0x4200_0000_0000;
/// The structural-refusal carriers' bases: private PML4 indexes 0x86,
/// 0x88, 0x8A -- canonical (below 2^47) like every load image the gate
/// admits.
const DELAYATTRS_IMAGE_BASE: u64 = 0x4300_0000_0000;
const DELAYMIS_IMAGE_BASE: u64 = 0x4400_0000_0000;
const DELAYVA_IMAGE_BASE: u64 = 0x4500_0000_0000;
/// The VA-form carrier's LOAD base (not its preferred base -- see
/// `DELAYVABIAS_FILE_BASE`): private PML4 index 0x8C, canonical.
const DELAYVABIAS_IMAGE_BASE: u64 = 0x4600_0000_0000;
/// Its FILE's own ImageBase, pinned by the gate too: the classic PE base
/// 0x40_0000. It is never loaded there (PML4 index 0 is the shared
/// identity subtree); it exists so the descriptor's VA fields fit.
const DELAYVABIAS_FILE_BASE: u64 = 0x0040_0000;
/// The cap-boundary carriers' own fixed ImageBases (--image-base on
/// each link; private PML4 indexes 0x8E, 0x90, 0x92 -- canonical, same
/// rule as every other PE base).
const DELAYMANY_IMAGE_BASE: u64 = 0x4700_0000_0000;
const DELAYTHUNKS_IMAGE_BASE: u64 = 0x4800_0000_0000;
const DELAYFULL_IMAGE_BASE: u64 = 0x4900_0000_0000;
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

/// The DELAY-LOAD control family, in its OWN `#[inline(never)]` frame:
/// the boot function's kernel-stack budget was sized by five rounds of
/// in-body facts, and the delay section's locals pushed the peak past
/// the guard page -- the first boots double-faulted several controls
/// EARLIER than the delay code runs, with the SAME stack pointer every
/// time. Non-overlapping locals get their own frame: the cause-shaped
/// fix, not a larger stack.
///
/// Control 9 is the eager-resolution ruling's positive half -- both
/// delay imports resolve through the same registry the normal walk
/// uses, the slots are WRITTEN (an eager resolve is a fresh
/// resolution, not a bound honor), and the provider's mapped base is
/// written as the module handle where the descriptor pointed;
/// controls 10 and 11 are the refusal half.
#[inline(never)]
unsafe fn run_delay_controls(
    mapper: &mut x86_64::structures::paging::OffsetPageTable<'static>,
    fa: &mut crate::memory::frame_allocator::BumpFrameAllocator,
    reg: &pe::DllRegistry,
) {
    let dgood = pe::load(DELAY_GOOD_CLIENT, mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the delay-load resolution PE: {:?}", e));
    let dbad = pe::load(DELAY_BAD_CLIENT, mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the delay-load missing PE: {:?}", e));
    let dmut = pe::load(DELAY_MUT_CLIENT, mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the delay-load bound PE: {:?}", e));
    let dres = pe::resolve_delay_imports(
        DELAYGOOD_IMAGE_BASE,
        mapper,
        dgood.image_max_rva,
        reg,
    )
    .unwrap_or_else(|e| panic!("delay-load resolution failed: {:?}", e));
    assert_eq!(dres.facts.len(), 2, "the delaygood carrier declares exactly two delay imports");
    let d_fn = dres
        .facts
        .iter()
        .find(|f| f.symbol.as_slice() == b"crossfn")
        .expect("the named crossfn delay fact must be present");
    let d_ord = dres
        .facts
        .iter()
        .find(|f| f.symbol.as_slice() == b"ord2")
        .expect("the ordinal-form crossmark delay fact (ord2) must be present");
    assert_eq!(d_fn.dll.as_slice(), b"userprog_pe_import_dll.dll", "the delay facts must name the provider");
    assert_eq!(d_ord.dll.as_slice(), b"userprog_pe_import_dll.dll");
    assert!(
        matches!(d_fn.origin, pe::ImportOrigin::DelayResolved)
            && matches!(d_ord.origin, pe::ImportOrigin::DelayResolved),
        "every delay fact must carry the DelayResolved origin"
    );
    assert_eq!(
        d_fn.value,
        PROVIDER_IMAGE_BASE + FN_RVA,
        "the named delay import must resolve to the provider's crossfn (same registry path as the normal walk)"
    );
    assert_eq!(
        d_ord.value,
        PROVIDER_IMAGE_BASE + MARK_RVA,
        "the ordinal delay import must resolve to the provider's crossmark"
    );
    assert_eq!(
        d_ord.prior,
        0x8000_0000_0000_0002,
        "the ordinal slot's prior is the composed UNBOUND thunk (ORDINAL_FLAG64 | the provider's own EAT-derived ordinal)"
    );
    assert!(
        d_fn.prior != 0 && d_fn.prior < 0x1_0000 && d_fn.prior != d_fn.value,
        "the named slot's prior is the composed unbound hint/name RVA (never an absolute pointer)"
    );
    for f in dres.facts.iter() {
        // UNALIGNED read, exactly the resolve walk's own access
        // discipline: the patcher composes the delay blobs in section
        // padding with no 8-byte-alignment guarantee (the handle slot
        // sits at name_foff + len(dll_name), whichever byte that
        // lands on); a read_volatile here is a false UB alarm, not a
        // real invariant.
        let readback = core::ptr::read_unaligned(f.slot as *const u64);
        assert_eq!(readback, f.value, "delay IAT slot readback mismatch");
    }
    assert_eq!(dres.modules.len(), 1, "exactly one delay descriptor parses");
    assert_eq!(
        dres.modules[0].0.as_slice(),
        b"userprog_pe_import_dll.dll",
        "the delay descriptor must name the provider"
    );
    assert_eq!(dres.modules[0].1, 0, "the delaygood descriptor's dwTimeStamp is the composed 0 (not bound)");
    assert!(dres.modules[0].2 != 0, "the composed rvaHmod names a handle slot");
    // Same unaligned discipline as the slot readbacks above: the
    // composed hmod slot's address is load_base + whatever RVA the
    // patcher's blob arithmetic produced.
    let handle = core::ptr::read_unaligned(dres.modules[0].2 as *const u64);
    assert_eq!(
        handle,
        PROVIDER_IMAGE_BASE,
        "the module handle the ruling wrote where the descriptor pointed is the provider's MAPPED base"
    );
    serial_println!(
        "  experimental pe-import-demo: negative control 9 (positive half) -- the DELAYGOOD carrier's two delay imports eagerly resolved through the same registry (named crossfn -> {:#x}, ordinal crossmark -> {:#x}; prior contents were the composed unbound thunks, both slots WRITTEN and read back) and the module handle {:#x} was written where the descriptor pointed -- OK, the eager delay ruling matches the normal walk's answers",
        d_fn.value, d_ord.value, handle
    );
    match pe::resolve_delay_imports(DELAYBAD_IMAGE_BASE, mapper, dbad.image_max_rva, reg) {
        Err(pe::PeError::MissingDelayDllImport) => serial_println!(
            "  experimental pe-import-demo: negative control 10 -- the DELAYBAD carrier's delay descriptor naming 'nowhere.dll' refused with the typed MissingDelayDllImport -- OK, the name-vs-file ruling applies to delay descriptors with their OWN flavor"
        ),
        other => panic!(
            "negative control 10 failed: expected MissingDelayDllImport, got {:?}",
            other.err()
        ),
    }
    match pe::resolve_delay_imports(DELAYMUT_IMAGE_BASE, mapper, dmut.image_max_rva, reg) {
        Err(pe::PeError::MalformedDelayImport(
            "bound delay-load descriptors (dwTimeStamp != 0) are out of scope -- refused, not ignored",
        )) => serial_println!(
            "  experimental pe-import-demo: negative control 11 -- the DELAYMUT carrier (dwTimeStamp = provider stamp + 1, the OLD-BIND marker) refused with the typed MalformedDelayImport -- OK, a binding guarantee the kernel cannot verify is NEVER silently honored and never silently ignored"
        ),
        other => panic!(
            "negative control 11 failed: expected the typed dwTimeStamp refusal, got {:?}",
            other.err()
        ),
    }
}

/// The delay directory's STRUCTURAL refusals, each on its own composed
/// carrier (controls 12-14). Separate `#[inline(never)]` frame for the
/// same reason `run_delay_controls` has one: three more mapped images'
/// worth of locals must not touch the boot frame's detected peak.
///
/// What is being proven is not "a panic happened": each arm matches the
/// EXACT `MalformedDelayImport` payload the walk reports, so a carrier
/// that broke for a different reason (an earlier bounds check, a
/// missing DLL) fails the boot instead of passing as the intended
/// refusal.
#[inline(never)]
unsafe fn run_delay_structural_controls(
    mapper: &mut x86_64::structures::paging::OffsetPageTable<'static>,
    fa: &mut crate::memory::frame_allocator::BumpFrameAllocator,
    reg: &pe::DllRegistry,
) {
    let dattrs = pe::load(DELAY_ATTRS_CLIENT, mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the delay grAttrs carrier: {:?}", e));
    let dmis = pe::load(DELAY_MIS_CLIENT, mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the delay INT!=IAT carrier: {:?}", e));
    let dva = pe::load(DELAY_VA_CLIENT, mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the delay VA-form carrier: {:?}", e));

    match pe::resolve_delay_imports(DELAYATTRS_IMAGE_BASE, mapper, dattrs.image_max_rva, reg) {
        Err(pe::PeError::MalformedDelayImport(
            "unknown delay descriptor grAttrs bits",
        )) => serial_println!(
            "  experimental pe-import-demo: negative control 12 -- the DELAYATTRS carrier (grAttrs = 0x3, dlattrRva plus an UNDEFINED bit) refused with the typed MalformedDelayImport -- OK, an attribute set the Windows helper itself cannot interpret is refused before any table is trusted"
        ),
        other => panic!(
            "negative control 12 failed: expected the typed grAttrs refusal, got {:?}",
            other.err()
        ),
    }
    match pe::resolve_delay_imports(DELAYMIS_IMAGE_BASE, mapper, dmis.image_max_rva, reg) {
        Err(pe::PeError::MalformedDelayImport(
            "delay INT entry does not match its IAT entry",
        )) => serial_println!(
            "  experimental pe-import-demo: negative control 13 -- the DELAYMIS carrier (INT[0] names crossfn while IAT[0] carries an ordinal thunk) refused with the typed MalformedDelayImport -- OK, the unbound pre-state the walk resolves is checked entry-for-entry, never assumed"
        ),
        other => panic!(
            "negative control 13 failed: expected the typed INT!=IAT refusal, got {:?}",
            other.err()
        ),
    }
    match pe::resolve_delay_imports(DELAYVA_IMAGE_BASE, mapper, dva.image_max_rva, reg) {
        Err(pe::PeError::MalformedDelayImport(
            "delay VA-form descriptor field below the preferred image base",
        )) => serial_println!(
            "  experimental pe-import-demo: negative control 14 -- the DELAYVA carrier (grAttrs = 0, the pre-VC7 VA form, with RVA-sized fields) refused with the typed MalformedDelayImport -- OK, a VA-form field that cannot be a VA of this image is refused, not reinterpreted"
        ),
        other => panic!(
            "negative control 14 failed: expected the typed VA-form refusal, got {:?}",
            other.err()
        ),
    }
    // Control 15's preconditions are FACTS read from the carrier file, not
    // assumptions about how build.sh happened to link it: the file's own
    // ImageBase must be the low one (that is what makes its VA fields
    // expressible), and the base this control loads it at must differ
    // from it (that is what makes the descriptor stale).
    let e_lfanew = u32::from_le_bytes(
        DELAY_VABIAS_CLIENT[0x3c..0x40].try_into().unwrap(),
    ) as usize;
    let file_base_at = e_lfanew + 4 + 20 + 24;
    let file_base = u64::from_le_bytes(
        DELAY_VABIAS_CLIENT[file_base_at..file_base_at + 8].try_into().unwrap(),
    );
    assert_eq!(
        file_base, DELAYVABIAS_FILE_BASE,
        "the VA-form carrier must be linked at its low preferred base"
    );
    assert_ne!(
        file_base, DELAYVABIAS_IMAGE_BASE,
        "the VA-form carrier must be LOADED away from its preferred base for this control"
    );
    let dvabias = pe::load(DELAY_VABIAS_CLIENT, mapper, fa, DELAYVABIAS_IMAGE_BASE)
        .unwrap_or_else(|e| panic!("failed to load the VA-form carrier at a bias: {:?}", e));
    match pe::resolve_delay_imports(DELAYVABIAS_IMAGE_BASE, mapper, dvabias.image_max_rva, reg) {
        Err(pe::PeError::MalformedDelayImport(
            "delay VA-form descriptor in a relocated image",
        )) => serial_println!(
            "  experimental pe-import-demo: negative control 15 -- the DELAYVABIAS carrier (real VA-form descriptor of ImageBase {:#x}, loaded at {:#x}) refused with the typed MalformedDelayImport -- OK, a pre-VC7 descriptor's baked VAs are refused in a relocated image instead of being reinterpreted against the wrong base",
            file_base, DELAYVABIAS_IMAGE_BASE
        ),
        other => panic!(
            "negative control 15 failed: expected the typed relocated VA-form refusal, got {:?}",
            other.err()
        ),
    }
}

/// Reads a u32 from a carrier's MAPPED image (headers included). Used
/// only by the cap-boundary controls, whose slot-state proof is derived
/// from the image's OWN descriptor table -- the same bytes the walk
/// read -- rather than from a composed-layout constant the patcher
/// could drift away from.
#[inline(always)]
unsafe fn image_u32(load_base: u64, rva: u64) -> u32 {
    core::ptr::read_unaligned((load_base + rva) as *const u32)
}

/// The image's `DataDirectory[13]` (delay-load) RVA, from the mapped
/// header page.
#[inline(always)]
unsafe fn image_delay_dir_rva(load_base: u64) -> u64 {
    let e_lfanew = image_u32(load_base, 0x3c) as u64;
    image_u32(load_base, e_lfanew + 4 + 20 + 112 + 13 * 8) as u64
}

/// One DWORD field (`field` 0..8) of descriptor `i` in the mapped delay
/// directory: 1 = rvaDLLName, 2 = rvaHmod, 3 = rvaIAT, 4 = rvaINT.
#[inline(always)]
unsafe fn image_delay_field(load_base: u64, dir_rva: u64, i: u64, field: u64) -> u64 {
    image_u32(load_base, dir_rva + i * 32 + field * 4) as u64
}

/// An IAT (`which` = 3) or INT (`which` = 4) entry `j` of descriptor
/// `i`, read from the mapped image.
#[inline(always)]
unsafe fn image_thunk(load_base: u64, dir_rva: u64, i: u64, which: u64, j: u64) -> u64 {
    let arr = image_delay_field(load_base, dir_rva, i, which);
    core::ptr::read_unaligned((load_base + arr + j * 8) as *const u64)
}

/// The delay directory's CAP-BOUNDARY controls (16-18), in their own
/// non-inlined frame for the same kernel-stack reason as the other
/// delay families: three more mapped images' locals must not touch the
/// boot frame's detected peak.
///
/// Controls 16 and 17 match the exact over-cap refusal AND prove how far
/// the walk got before refusing: every descriptor / thunk entry within
/// the cap has been resolved (its IAT slot holds the provider's mapped
/// crossfn, its handle slot the provider's base), while the entry past
/// the cap still holds its composed unbound thunk and an untouched
/// handle slot. That is what makes the cap EXCLUSIVE as a boot fact
/// rather than an assertion about the code: the legal maximum is
/// resolved IN FULL and only the entry beyond it is refused.
///
/// Control 18 is the other side of the same claim -- exactly 16
/// descriptors, the first carrying exactly 64 thunk entries -- which
/// the walk must ACCEPT and resolve completely (79 imports).
#[inline(never)]
unsafe fn run_delay_cap_controls(
    mapper: &mut x86_64::structures::paging::OffsetPageTable<'static>,
    fa: &mut crate::memory::frame_allocator::BumpFrameAllocator,
    reg: &pe::DllRegistry,
) {
    let dmany = pe::load(DELAY_MANY_CLIENT, mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the 17-descriptor delay carrier: {:?}", e));
    match pe::resolve_delay_imports(DELAYMANY_IMAGE_BASE, mapper, dmany.image_max_rva, reg) {
        Err(pe::PeError::MalformedDelayImport(
            "delay descriptor array unbounded",
        )) => {}
        other => panic!(
            "negative control 16 failed: expected the typed 17th-descriptor refusal, got {:?}",
            other.err()
        ),
    }
    let drva = image_delay_dir_rva(DELAYMANY_IMAGE_BASE);
    let mut i = 0u64;
    while i < 16 {
        let slot = core::ptr::read_unaligned(
            (DELAYMANY_IMAGE_BASE + image_delay_field(DELAYMANY_IMAGE_BASE, drva, i, 3)) as *const u64,
        );
        assert_eq!(
            slot,
            PROVIDER_IMAGE_BASE + FN_RVA,
            "descriptor {} is within the cap, so its IAT slot must already be resolved",
            i
        );
        let handle = core::ptr::read_unaligned(
            (DELAYMANY_IMAGE_BASE + image_delay_field(DELAYMANY_IMAGE_BASE, drva, i, 2)) as *const u64,
        );
        assert_eq!(
            handle,
            PROVIDER_IMAGE_BASE,
            "descriptor {} is within the cap, so its handle slot must already carry the provider's base",
            i
        );
        i += 1;
    }
    assert_eq!(
        image_delay_field(DELAYMANY_IMAGE_BASE, drva, 16, 0),
        1,
        "the 17th descriptor must EXIST and be RVA-form -- that is what the cap refused"
    );
    let prior = core::ptr::read_unaligned(
        (DELAYMANY_IMAGE_BASE + image_delay_field(DELAYMANY_IMAGE_BASE, drva, 16, 3)) as *const u64,
    );
    assert_eq!(
        prior,
        image_thunk(DELAYMANY_IMAGE_BASE, drva, 16, 4, 0),
        "the 17th descriptor's IAT slot must still equal its composed INT entry: the walk never wrote it"
    );
    assert_ne!(prior, PROVIDER_IMAGE_BASE + FN_RVA, "the 17th slot must be unwritten");
    assert!(
        prior < dmany.image_max_rva,
        "the unwritten slot still holds a hint/name RVA, not an absolute pointer"
    );
    let past_handle = core::ptr::read_unaligned(
        (DELAYMANY_IMAGE_BASE + image_delay_field(DELAYMANY_IMAGE_BASE, drva, 16, 2)) as *const u64,
    );
    assert_eq!(past_handle, 0, "the 17th descriptor's handle slot must be untouched");
    serial_println!(
        "  experimental pe-import-demo: negative control 16 -- the DELAYMANY carrier's 17th descriptor refused with the typed MalformedDelayImport; descriptors 0-15 are FULLY resolved (their IAT slots hold the provider's crossfn {:#x}, their handle slots the provider's mapped base), while the 17th's slot still holds its composed unbound thunk {:#x} and its handle slot is untouched -- OK, the 16-descriptor cap is EXCLUSIVE: the legal maximum is resolved in full and only the entry past it is refused",
        PROVIDER_IMAGE_BASE + FN_RVA, prior
    );

    let dthunks = pe::load(DELAY_THUNKS_CLIENT, mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the 65-thunk delay carrier: {:?}", e));
    match pe::resolve_delay_imports(DELAYTHUNKS_IMAGE_BASE, mapper, dthunks.image_max_rva, reg) {
        Err(pe::PeError::MalformedDelayImport(
            "delay thunk array unbounded",
        )) => {}
        other => panic!(
            "negative control 17 failed: expected the typed 65th-thunk refusal, got {:?}",
            other.err()
        ),
    }
    let tdrva = image_delay_dir_rva(DELAYTHUNKS_IMAGE_BASE);
    let tiat = image_delay_field(DELAYTHUNKS_IMAGE_BASE, tdrva, 0, 3);
    let mut j = 0u64;
    while j < 64 {
        let slot = core::ptr::read_unaligned(
            (DELAYTHUNKS_IMAGE_BASE + tiat + j * 8) as *const u64,
        );
        assert_eq!(
            slot,
            PROVIDER_IMAGE_BASE + FN_RVA,
            "thunk entry {} is within the cap, so its IAT slot must already be resolved",
            j
        );
        j += 1;
    }
    let tprior = core::ptr::read_unaligned(
        (DELAYTHUNKS_IMAGE_BASE + tiat + 64 * 8) as *const u64,
    );
    assert_eq!(
        tprior,
        image_thunk(DELAYTHUNKS_IMAGE_BASE, tdrva, 0, 4, 64),
        "the 65th entry's IAT slot must still equal its composed INT entry: the walk never wrote it"
    );
    assert_ne!(tprior, PROVIDER_IMAGE_BASE + FN_RVA, "the 65th slot must be unwritten");
    serial_println!(
        "  experimental pe-import-demo: negative control 17 -- the DELAYTHUNKS carrier's 65th thunk entry refused with the typed MalformedDelayImport; entries 0-63 are FULLY resolved (all 64 IAT slots hold the provider's crossfn {:#x}) while entry 64 still holds its composed unbound thunk {:#x} -- OK, the 64-entry thunk cap is EXCLUSIVE for the same reason, and one descriptor's INT/IAT pair was walked end to end before the refusal",
        PROVIDER_IMAGE_BASE + FN_RVA, tprior
    );

    let dfull = pe::load(DELAY_FULL_CLIENT, mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the legal-maxima delay carrier: {:?}", e));
    let fres = pe::resolve_delay_imports(DELAYFULL_IMAGE_BASE, mapper, dfull.image_max_rva, reg)
        .unwrap_or_else(|e| panic!(
            "negative control 18 failed: the legal 16-descriptor / 64-thunk maximum must be ACCEPTED, got {:?}",
            e
        ));
    assert_eq!(
        fres.modules.len(),
        16,
        "the legal-maxima carrier declares exactly 16 descriptors (the cap's legal maximum)"
    );
    assert_eq!(
        fres.facts.len(),
        79,
        "64 thunk entries in the first descriptor + 15 in the rest = 79 resolved imports"
    );
    for f in fres.facts.iter() {
        assert_eq!(f.value, PROVIDER_IMAGE_BASE + FN_RVA, "every legal-maxima import resolves to the provider's crossfn");
        assert!(
            matches!(f.origin, pe::ImportOrigin::DelayResolved),
            "every legal-maxima fact must carry the DelayResolved origin"
        );
        assert_eq!(
            core::ptr::read_unaligned(f.slot as *const u64),
            f.value,
            "legal-maxima slot readback mismatch"
        );
    }
    for m in fres.modules.iter() {
        assert_eq!(m.0.as_slice(), b"userprog_pe_import_dll.dll");
        assert_eq!(m.1, 0, "every legal-maxima descriptor is unbound (dwTimeStamp 0)");
    }
    let fdrva = image_delay_dir_rva(DELAYFULL_IMAGE_BASE);
    let fiat = image_delay_field(DELAYFULL_IMAGE_BASE, fdrva, 0, 3);
    assert_eq!(
        core::ptr::read_unaligned((DELAYFULL_IMAGE_BASE + fiat + 63 * 8) as *const u64),
        PROVIDER_IMAGE_BASE + FN_RVA,
        "the 64th thunk entry (index 63) is inside the cap and must be resolved"
    );
    assert_eq!(
        core::ptr::read_unaligned((DELAYFULL_IMAGE_BASE + fiat + 64 * 8) as *const u64),
        0,
        "the composed IAT terminator right AFTER the 64th entry is what makes 64 legal -- it must be the zero the walk stopped at"
    );
    assert_eq!(
        core::ptr::read_unaligned(
            (DELAYFULL_IMAGE_BASE + image_delay_field(DELAYFULL_IMAGE_BASE, fdrva, 15, 2)) as *const u64
        ),
        PROVIDER_IMAGE_BASE,
        "the 16th descriptor (index 15) is inside the cap and its handle slot must be written"
    );
    serial_println!(
        "  experimental pe-import-demo: negative control 18 -- the DELAYFULL carrier (EXACTLY 16 descriptors, the FIRST carrying EXACTLY 64 thunk entries, 79 imports) ACCEPTED and resolved completely: every slot holds the provider's crossfn {:#x}, all 16 handle slots the provider's base, and the IAT terminator right after the 64th entry stopped the walk cleanly -- OK, both caps are exclusive bounds: the legal maxima are accepted, one past them is refused",
        PROVIDER_IMAGE_BASE + FN_RVA
    );
}

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
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    // This space's guarded stack keeps the one-size default: the demo
    // BODY does not run on it (the chain enters this stage through
    // pe_reloc_demo::on_trap with an ordinary call, so the body lives
    // on THAT stage's deliberately-sized stack); this space's stack
    // serves this space's own machine state, like every demo space.
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

    // The DELAY-LOAD control family lives in its own non-inlined
    // frame (see run_delay_controls): the boot function's kernel-stack
    // budget was sized by five rounds of in-body facts, and the delay
    // locals pushed the peak past the guard page -- the first boots
    // double-faulted many controls EARLIER than the delay code runs,
    // with the SAME stack pointer every time.
    run_delay_controls(&mut mapper, fa, &reg);
    run_delay_structural_controls(&mut mapper, fa, &reg);
    run_delay_cap_controls(&mut mapper, fa, &reg);

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

    let fa = (*&raw mut FRAME_ALLOCATOR).take().unwrap();
    isolation_demo::start(fa)
}
