//! PE32+ exception-table demo -- the `.pdata` runtime-function walk
//! (`IMAGE_DIRECTORY_ENTRY_EXCEPTION`, DataDirectory[3]) as a real
//! Phase 5 increment. The exception table is what a Windows unwinder
//! DISPATCHES on at fault time; the loader's job here is to parse and
//! VERIFY it at load time and refuse a malformed surface at cause
//! (typed `MalformedExceptionTable`), never to ride a wrong table
//! silently into a later dispatch. Unwinding/dispatch itself stays
//! deliberately out of scope -- same honest boundary as every other
//! `experimental` loader increment.
//!
//! See `crate::experimental`'s banner: not NPS-002/NPS-003
//! conformance (and not NPS-007 conformance -- see
//! `engineering/RFC-0001-nykernel-bringup-plan.md`'s still-open action
//! item). Loader capability proof, same standard as every other
//! `experimental` module.
//!
//! The fixtures are `prog_pe_pdata.s` built THREE times at distinct
//! ImageBases, then composed by `tools/patch_pe_pdata.py` (a real
//! on-disk composition: the x64 `.pdata` table and the two
//! `UNWIND_INFO` blocks are real bytes laid into `.idata`'s own
//! in-block alignment padding, with DataDirectory[3] pointed at the
//! entry array -- the checked-patch discipline the delay/bound
//! families established). The negative controls are
//! boot-controlled, not just code-verified: the MIS carrier's second
//! entry genuinely overlaps the first, and the VER carrier's second
//! `UNWIND_INFO` carries a genuinely undefined version nibble (3);
//! both loads are refused with their exact typed reasons before
//! anything ring-side could run.

use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{pe, usermode, pe_resource_demo};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::serial_println;

pub const TRAP_VECTOR: u8 = 0x8E;

/// The good carrier: `prog_pe_pdata.s` linked at ImageBase
/// 0x140000000, composed by `tools/patch_pe_pdata.py` into a sorted,
/// two-entry `.pdata` table + two legal `UNWIND_INFO`s (versions 1
/// and 2 -- both halves of the ABI's legal version range appear).
static DATA_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_pdata_good.exe");
/// The MIS carrier: the same client composed so entry 1 covers only
/// the first QUARTER of `.text` -- begin < entry 0's end, a genuinely
/// overlapping (unsorted) array. Its entry point is never entered.
static MIS_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_pdata_mis.exe");
/// The VER carrier: the same client composed with UNWIND_INFO B's
/// version nibble at 3 -- a version the x64 ABI does not define.
static VER_CLIENT: &[u8] = include_bytes!("../../userprog/userprog_pe_pdata_ver.exe");

/// Each carrier's file ImageBase (build.sh's three link lines; the
/// demo loads each AT ITS OWN base -- load_bias 0 -- inside this
/// demo's one address space, so the three never collide).
const GOOD_IMAGE_BASE: u64 = 0x_5500_0000_0000;
const MIS_IMAGE_BASE: u64 = 0x_5600_0000_0000;
const VER_IMAGE_BASE: u64 = 0x_5700_0000_0000;

/// The good carrier's private data page + user stack (the demo maps
/// them inside its own address space; the client's slot write and
/// `int $0x8e` are absolute, so any load base works).
const PRIVATE_ADDR: u64 = 0x_5500_0010_0000;
const USER_STACK_ADDR: u64 = 0x_5500_0020_0000;
const EXPECTED_VALUE: u64 = 0xC0DE_DA7A_C0DE_DA7A;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

/// Parses the embedded good client's OWN `.text` section header out of
/// the file bytes -- the composition's facts are located by the file's
/// own section table, never a baked constant, so a toolchain layout
/// drift cannot desync the demo's expectations from what the file
/// genuinely contains (the same locate-by-fact rule the ELF demos'
/// slot-by-SYMBOL checks follow).
fn text_bounds_of(file: &[u8]) -> (u64, u64) {
    assert_eq!(&file[0..2], b"MZ", "not a PE file");
    let e_lfanew = u32::from_le_bytes(file[0x3c..0x40].try_into().unwrap()) as usize;
    assert_eq!(&file[e_lfanew..e_lfanew + 4], b"PE\x00\x00", "bad PE signature");
    let coff = e_lfanew + 4;
    let nsec = u16::from_le_bytes(file[coff + 2..coff + 4].try_into().unwrap()) as usize;
    let opt_size = u16::from_le_bytes(file[coff + 16..coff + 18].try_into().unwrap()) as usize;
    let sh_off = coff + 20 + opt_size;
    for i in 0..nsec {
        let b = sh_off + i * 40;
        let name = file[b..b + 8].iter().position(|&c| c == 0)
            .map_or(&file[b..b + 8], |n| &file[b..b + n]);
        if name == b".text" {
            let vsize = u32::from_le_bytes(file[b + 8..b + 12].try_into().unwrap()) as u64;
            let vaddr = u32::from_le_bytes(file[b + 12..b + 16].try_into().unwrap()) as u64;
            assert!(vsize > 0 && vsize % 2 == 0, "the composed carrier's .text must be nonempty and even (patcher precondition)");
            return (vaddr, vaddr + vsize);
        }
    }
    panic!("the carrier's section table has no .text section");
}

/// Builds an address space, loads the three carriers (the good one at
/// its own ImageBase with a verified `.pdata`; the MIS and VER ones in
/// private subtrees, refusing them for their exact typed reasons),
/// states the walk's facts, and enters ring 3 at the good client's
/// entry point. `on_trap` (vector 0x8E) reports the ring-3 result and
/// chains into `experimental::pe_resource_demo`, keeping the single
/// unified demo chain.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep already
/// initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    let mut space = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build pe-exception-demo address space"));
    space.activate();
    let mut mapper = space.mapper();

    // --- the good carrier: load and state the walk's facts ---
    let good = pe::load(DATA_CLIENT, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the pdata good carrier: {:?}", e));
    let (text_lo, text_hi) = text_bounds_of(DATA_CLIENT);
    let half = (text_hi - text_lo) / 2;

    let facts = &good.exception_table;
    assert_eq!(
        facts.len(), 2,
        "the composed good carrier must walk out exactly 2 runtime functions"
    );
    assert_eq!(facts[0].begin_rva, text_lo, "entry 0 must cover .text's start");
    assert_eq!(facts[0].end_rva, text_lo + half, "entry 0 must cover .text's first half");
    assert_eq!(facts[0].unwind_version, 1, "entry 0's UNWIND_INFO version nibble must be 1");
    assert_eq!(
        facts[1].begin_rva, text_lo + half,
        "entry 1 must begin exactly where entry 0 ends (sorted, non-overlapping)"
    );
    assert_eq!(facts[1].end_rva, text_hi, "entry 1 must end exactly at .text's end");
    assert_eq!(facts[1].unwind_version, 2, "entry 1's UNWIND_INFO version nibble must be 2");

    serial_println!(
        "  experimental pe-exception-demo: good carrier loaded at its own ImageBase {:#x}; the .pdata walk verified {} runtime function(s): [{:#x},{:#x}) unwind v{}, [{:#x},{:#x}) unwind v{} -- OK, sorted, non-overlapping, both unwind versions the ABI's legal 1 and 2",
        GOOD_IMAGE_BASE,
        facts.len(),
        facts[0].begin_rva, facts[0].end_rva, facts[0].unwind_version,
        facts[1].begin_rva, facts[1].end_rva, facts[1].unwind_version,
    );

    // --- negative control 1: the MIS carrier's overlapping array ---
    match pe::load(MIS_CLIENT, &mut mapper, fa, MIS_IMAGE_BASE) {
        Err(pe::PeError::MalformedExceptionTable("runtime-function entries overlap")) => {
            serial_println!(
                "  experimental pe-exception-demo: negative control 1 -- the MIS carrier's genuinely overlapping second entry (.text quarter [{:#x},{:#x}) began BELOW entry 0's end {:#x}) refused the LOAD with the typed MalformedExceptionTable(\"runtime-function entries overlap\") -- OK",
                text_lo, text_lo + half / 2, text_lo + half
            );
        }
        other => panic!(
            "negative control 1 failed: expected MalformedExceptionTable(\"runtime-function entries overlap\"), got {:?}",
            other.err()
        ),
    }

    // --- negative control 2: the VER carrier's version-3 UNWIND_INFO ---
    match pe::load(VER_CLIENT, &mut mapper, fa, VER_IMAGE_BASE) {
        Err(pe::PeError::MalformedExceptionTable("unwind info version")) => {
            serial_println!(
                "  experimental pe-exception-demo: negative control 2 -- the VER carrier's genuinely undefined UNWIND_INFO version nibble (3) refused the LOAD with the typed MalformedExceptionTable(\"unwind info version\") -- OK"
            );
        }
        other => panic!(
            "negative control 2 failed: expected MalformedExceptionTable(\"unwind info version\"), got {:?}",
            other.err()
        ),
    }

    // --- the good client's ring-3 half: its own body, entered the same
    // way every other PE demo's client is entered ---
    let data_flags = x86_64::structures::paging::PageTableFlags::PRESENT
        | x86_64::structures::paging::PageTableFlags::WRITABLE
        | x86_64::structures::paging::PageTableFlags::USER_ACCESSIBLE
        | x86_64::structures::paging::PageTableFlags::NO_EXECUTE;
    let data_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(PRIVATE_ADDR), 1, data_flags,
    );
    assert_eq!(data_mapped, 1, "failed to map pe-exception-demo private data page");
    let stack_mapped = crate::memory::paging::map_range(
        &mut mapper, fa, VirtAddr::new(USER_STACK_ADDR), 1, data_flags,
    );
    assert_eq!(stack_mapped, 1, "failed to map pe-exception-demo user stack");

    serial_println!(
        "  experimental pe-exception-demo: entering the good carrier's own body in ring 3 (entry {:#x})",
        good.entry_point.as_u64()
    );

    core::mem::forget(space); // no teardown here; teardown_demo proves that separately

    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(good.entry_point, user_rsp)
}

/// Called from `idt::init`'s installed handler on the one trap this
/// demo's program issues. Reports whether the good carrier's own code
/// actually ran (a magic value only it could have written), then
/// chains into `experimental::pe_resource_demo` (the resource-tree
/// proof) which then reaches `experimental::isolation_demo`.
pub unsafe fn on_trap() -> ! {
    let observed = core::ptr::read_volatile(PRIVATE_ADDR as *const u64);
    let ok = observed == EXPECTED_VALUE;
    serial_println!(
        "  experimental pe-exception-demo self-test: the composed carrier's entry point wrote {:#x} (expected {:#x}) -- {}",
        observed, EXPECTED_VALUE,
        if ok { "OK, a genuine Windows-format (PE32+) executable whose verified exception table was part of its load ran in ring 3" } else { "MISMATCH" }
    );

    let fa = (*&raw mut FRAME_ALLOCATOR).take().unwrap();
    pe_resource_demo::start(fa)
}
