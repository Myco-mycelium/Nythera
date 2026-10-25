//! PE32+ resource-directory demo -- the resource tree walk
//! (`IMAGE_DIRECTORY_ENTRY_RESOURCE`, DataDirectory[2]) as a real
//! Phase 5 increment. The resource tree is what Windows'
//! `EnumResource*` surfaces to applications; the loader's job here is
//! to parse and VERIFY the two-level (named-first, ID-second) tree at
//! load time -- the entry-array shape, every name blob, every leaf
//! data entry bounded against the image's own footprint -- and refuse
//! a malformed surface at cause (typed
//! `MalformedResourceDirectory`), never to funnel a wrong tree into a
//! later lookup. Enumeration BY TYPE/LANGUE and resource APIs stay
//! deliberately out of scope -- same honest boundary as every other
//! `experimental` loader increment.

//! See `crate::experimental`'s banner: not NPS-002/NPS-003 conformance
//! (and not NPS-007 conformance -- see
//! `engineering/RFC-0001-nykernel-bringup-plan.md`'s still-open action
//! item). Loader capability proof, same standard as every other
//! `experimental` module.
//!
//! The fixtures are `prog_pe_res.s` built FOUR times at distinct
//! ImageBases, then composed by `tools/patch_pe_res.py` (a real on-disk
//! composition: the two `IMAGE_RESOURCE_DIRECTORY` headers, one named
//! entry, its length-prefixed UTF-16 name blob ("TXT"), one ID entry,
//! and a bounded `IMAGE_RESOURCE_DATA_ENTRY` leaf with a real 8-byte
//! payload, laid into `.idata`'s own padding with dir[2] pointed at
//! the tree -- the checked-patch discipline pdata established). The
//! negative controls are boot-controlled, not just code-verified: the
//! RBAD carrier's leaf genuinely overruns the image, the RNAME
//! carrier's named entry genuinely carries an empty (length-0)
//! string, and the RMANY carrier's L1 claims 17 ID entries (past the
//! walk's 16 cap); all three loads are refused with their exact typed
//! reasons before anything ring-side could run.

use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{pe, usermode, isolation_demo};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::serial_println;

pub const TRAP_VECTOR: u8 = 0x8F;

/// The good carrier: `prog_pe_res.s` linked at ImageBase
/// 0x580000000000, composed into the canonical one-named /
/// one-subdirectory / one-ID / one-leaf tree.
static RES_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_res_good.exe");
/// The RBAD carrier: the same client composed so the leaf's Size runs
/// its bytes past the image's own footprint. Its entry point is never
/// entered.
static BAD_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_res_bad.exe");
/// The RNAME carrier: the same client composed so L1's named entry
/// names an EMPTY (length-0) string. Entry point never entered.
static RNAME_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_res_rname.exe");
/// The RMANY carrier: the same client composed so L1 claims 17 ID
/// entries -- handled by the walk's per-directory entry cap. Entry
/// point never entered.
static RMANY_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_res_rmany.exe");

/// Each carrier's file ImageBase (build.sh's four link lines; the
/// demo loads each AT ITS OWN base -- load_bias 0 -- inside this
/// demo's one address space, so the four never collide).
const GOOD_IMAGE_BASE: u64 = 0x_5800_0000_0000;
const BAD_IMAGE_BASE: u64 = 0x_5900_0000_0000;
const RNAME_IMAGE_BASE: u64 = 0x_5A00_0000_0000;
const RMANY_IMAGE_BASE: u64 = 0x_5B00_0000_0000;

/// The good carrier's private data page + user stack (the demo maps
/// them inside its own address space; the client's slot write and
/// `int $0x8f` are absolute, so any load base works).
const PRIVATE_ADDR: u64 = 0x_5800_0010_0000;
const USER_STACK_ADDR: u64 = 0x_5800_0020_0000;
const EXPECTED_VALUE: u64 = 0xF00D_FACE_F00D_FACE;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

/// Builds an address space, loads the four carriers (the good one at
/// its own ImageBase with a verified resource tree; the refusal ones
/// in private subtrees, refusing them for their exact typed reasons),
/// states the walk's facts, and enters ring 3 at the good client's
/// entry point. `on_trap` (vector 0x8F) reports the ring-3 result and
/// chains into `experimental::isolation_demo`, keeping the single
/// unified demo chain.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep already
/// initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    let mut space = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build pe-resource-demo address space"));
    space.activate();
    let mut mapper = space.mapper();

    // --- the good carrier: load and state the walk's facts ---
    let good = pe::load(RES_CLIENT, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the resource good carrier: {:?}", e));

    let facts = good
        .resource_facts
        .as_ref()
        .expect("the composed good carrier must carry a resource tree");
    assert_eq!(
        facts.type_name.as_slice(),
        b"TXT",
        "the composed tree's walked type name must be the composed \"TXT\""
    );
    assert_eq!(facts.lang_id, 1, "the composed tree's walked ID must be 1");
    assert_eq!(
        facts.data_size, 8,
        "the composed leaf's walked size must be the composed 8"
    );
    assert_eq!(
        facts.entries_walked, 2,
        "the composed tree walks exactly two entries (1 named at L1, 1 ID at L2)"
    );

    serial_println!(
        "  experimental pe-resource-demo: good carrier loaded at its own ImageBase {:#x}; the resource tree verified: named type {:?} -> ID {} -> data entry [{:#x},{:#x}) ({} bytes) -- {} entr(ies) walked -- OK, named-first, ID-second, all leaf bytes bounded",
        GOOD_IMAGE_BASE,
        facts.type_name,

        facts.lang_id,
        facts.data_rva,
        facts.data_rva + facts.data_size,
        facts.data_size,
        facts.entries_walked,
    );

    // --- negative control 1: the RBAD carrier's overrunning leaf ---
    match pe::load(BAD_CLIENT, &mut mapper, fa, BAD_IMAGE_BASE) {
        Err(pe::PeError::MalformedResourceDirectory(
            "resource data beyond the image's mapped footprint",
        )) => {
            serial_println!(
                "  experimental pe-resource-demo: negative control 1 -- the RBAD carrier's leaf Size genuinely running past the image's mapped footprint refused the LOAD with the typed MalformedResourceDirectory(\"resource data beyond the image's mapped footprint\") -- OK"
            );
        }
        other => panic!(
            "negative control 1 failed: expected MalformedResourceDirectory(\"resource data beyond the image's mapped footprint\"), got {:?}",
            other.err()
        ),
    }

    // --- negative control 2: the RNAME carrier's empty name string ---
    match pe::load(RNAME_CLIENT, &mut mapper, fa, RNAME_IMAGE_BASE) {
        Err(pe::PeError::MalformedResourceDirectory("empty resource name")) => {
            serial_println!(
                "  experimental pe-resource-demo: negative control 2 -- the RNAME carrier's named entry genuinely carrying a length-0 (empty) string refused the LOAD with the typed MalformedResourceDirectory(\"empty resource name\") -- OK"
            );
        }
        other => panic!(
            "negative control 2 failed: expected MalformedResourceDirectory(\"empty resource name\"), got {:?}",
            other.err()
        ),
    }

    // --- negative control 3: the RMANY carrier's over-cap array ---
    match pe::load(RMANY_CLIENT, &mut mapper, fa, RMANY_IMAGE_BASE) {
        Err(pe::PeError::MalformedResourceDirectory(
            "resource directory entry array unbounded",
        )) => {
            serial_println!(
                "  experimental pe-resource-demo: negative control 3 -- the RMANY carrier's L1 genuinely claiming 17 ID entries (past the walk's 16-entry per-directory cap) refused the LOAD with the typed MalformedResourceDirectory(\"resource directory entry array unbounded\") -- OK"
            );
        }
        other => panic!(
            "negative control 3 failed: expected MalformedResourceDirectory(\"resource directory entry array unbounded\"), got {:?}",
            other.err()
        ),
    }

    // --- the good client's ring-3 half: its own body, entered the
    // same way every other PE demo's client is entered ---
    let data_flags = x86_64::structures::paging::PageTableFlags::PRESENT
        | x86_64::structures::paging::PageTableFlags::WRITABLE
        | x86_64::structures::paging::PageTableFlags::USER_ACCESSIBLE
        | x86_64::structures::paging::PageTableFlags::NO_EXECUTE;
    let data_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(PRIVATE_ADDR), 1, data_flags,
    );
    assert_eq!(data_mapped, 1, "failed to map pe-resource-demo private data page");
    let stack_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(USER_STACK_ADDR), 1, data_flags,
    );
    assert_eq!(stack_mapped, 1, "failed to map pe-resource-demo user stack");

    serial_println!(
        "  experimental pe-resource-demo: entering the good carrier's own body in ring 3 (entry {:#x})",
        good.entry_point.as_u64()
    );

    core::mem::forget(space); // no teardown here; teardown_demo proves that separately

    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(good.entry_point, user_rsp)
}

/// Called from `idt::init`'s installed handler on the one trap this
/// demo's program issues. Reports whether the good carrier's own code
/// actually ran (a magic value only it could have written), then
/// chains into `experimental::isolation_demo`, continuing this
/// kernel's single unified demo chain.
pub unsafe fn on_trap() -> ! {
    let observed = core::ptr::read_volatile(PRIVATE_ADDR as *const u64);
    let ok = observed == EXPECTED_VALUE;
    serial_println!(
        "  experimental pe-resource-demo self-test: resource good carrier's entry point wrote {:#x} (expected {:#x}) -- {}",
        observed, EXPECTED_VALUE,
        if ok { "OK, the resource-verifying load's own PE32+ code ran in ring 3" } else { "MISMATCH" }
    );

    let fa = (*&raw mut FRAME_ALLOCATOR).take().unwrap();
    isolation_demo::start(fa)
}
