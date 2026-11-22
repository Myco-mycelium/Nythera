//! The Win32 API surface proof: a genuine Windows-format PE32+ console
//! program, linked against a genuine Windows-format PE32+ `kernel32.dll`,
//! whose twelve exports -- `GetStdHandle`, `WriteFile`, `GetTickCount`,
//! `ExitProcess`, `CreateFileA`, `ReadFile`, `CloseHandle`, `GetLastError`,
//! `SetLastError`, `GetCommandLineA`, `GetModuleHandleA`, `GetProcAddress` --
//! are performed by this kernel.
//!
//! What is proven, end to end, every boot:
//!
//! 1. `userprog/kernel32.dll` loads at its own `ImageBase`, and its REAL
//!    export directory (`GetProcAddress` semantics) names exactly the twelve
//!    functions; every export address is inside its own `.text` window, and
//!    the call block the stubs use is where the kernel pins it (magic
//!    checked) -- the layout-drift rule.
//! 2. `userprog/win32_console.exe` is loaded, and its REAL
//!    `IMAGE_DIRECTORY_ENTRY_IMPORT` -- synthesized by GNU ld itself from the
//!    DLL -- resolves to those twelve export addresses. Every IAT slot is read
//!    back against the export table's own answer, per symbol.
//! 3. The client's own code runs in ring 3 and uses the imports the ordinary
//!    Windows way. Startup, in the order a CRT makes them: `GetCommandLineA`
//!    answers a stable pointer to the string the loader placed, which the
//!    client compares byte-for-byte (NUL included) against its own copy; and
//!    `GetModuleHandleA(NULL)` answers the base of the image the client is
//!    actually running from, which the client proves by checking that its own
//!    `_start` lies inside `[hmod, hmod + SizeOfImage)`. Runtime binding: the
//!    client asks `GetModuleHandleA("kernel32.dll")` -- the NAMED form the
//!    previous tranche could only refuse -- gets a handle, asks it
//!    `GetProcAddress(.., "GetTickCount")`, and requires the answer to be the
//!    very address the loader wrote into its IAT; then it CALLS through that
//!    pointer, so the runtime-bound address is proved callable and not merely
//!    equal. It also asks for a module that is not loaded and for an export
//!    that module does not have, and reads back `ERROR_MOD_NOT_FOUND` /
//!    `ERROR_PROC_NOT_FOUND` respectively. Console:
//!    `GetStdHandle` then `WriteFile`. Files: it opens
//!    `C:\nyrqis\hello.txt` with `CreateFileA`, reads it in two pieces (which
//!    proves the handle's cursor advances), reads past the end (TRUE, zero
//!    bytes), writes the bytes it read to the console, and closes the handle;
//!    then it opens a file that is deliberately NOT text (`0x00`, `0x80`,
//!    `0xfe`), a zero-length file, and a file it then uses after closing it
//!    (which must fail); and it asks for a file that does not exist (which
//!    must return `INVALID_HANDLE_VALUE`, not an error). Errors: it then asks
//!    `GetLastError` WHY each of those failed -- the miss (`ERROR_FILE_NOT_FOUND`),
//!    the closed handle used twice (`ERROR_INVALID_HANDLE`), and a refused
//!    `GENERIC_WRITE` open (`ERROR_ACCESS_DENIED`) -- proves `SetLastError`
//!    writes an arbitrary code, and proves a successful call leaves the code
//!    alone. That refused open also returns `INVALID_HANDLE_VALUE`, which the
//!    client itself tests the Windows way (`== INVALID_HANDLE_VALUE`, not
//!    zero) and reports: a refusal carries the failure value of the export's
//!    own prototype, so a client's handle test reads it as Windows does.
//! 4. The surface REFUSES what ring 3 cannot justify -- an unknown function
//!    index, a trap from outside kernel32's `.text`, a handle never issued, a
//!    buffer or out-parameter outside the client's mapped regions, a length
//!    past the console's cap, a relative path, an unmounted drive, a
//!    non-ASCII path, a path with no terminator inside the client's region, a
//!    write access request, a creation disposition other than OPEN_EXISTING,
//!    a non-NULL overlapped pointer, using the console handle as a file, and
//!    exhausting the volume's handle table, asking for a command line before
//!    any loader placed one, naming a module before any was registered, asking
//!    an unregistered base for an export, an export a module does not have,
//!    an export name in the wrong case, an ordinal-form request, and
//!    registering a module row that is unusable (empty name, non-text name,
//!    zero base, zero image, duplicate name, an export outside the image) or a
//!    third row -- each typed, each
//!    exercised kernel-side before ring 3 runs (negative controls). Each control also
//!    asserts the Win32 code that refusal reports, so the `GetLastError` table
//!    is proved for every variant and not only for the three the client hits;
//!    the client's own run must raise exactly the refusals it asks for.
//!
//! Deliberate scope, stated: twelve functions, no NT system-call layer beneath
//! them, no user32/gdi32, no Microsoft code, and `ExitProcess` records its code
//! and returns (v0 has no task teardown to switch to) -- the client's next
//! instruction is this link's completion trap, whose handler asserts the
//! recorded code. The volume is three read-only files in the kernel image, not
//! a file system: no directories, no writes, no current directory, no dates.
//! The last error is one cell for the whole kernel, because v0 has one ring-3
//! client; Windows keeps it per thread in a TEB this kernel does not have.
//! That is the difference between a real Win32 API surface and Windows
//! compatibility, and this file only claims the former.
//!
//! The chain: `pe_resource_demo` chains here instead of straight into
//! `isolation_demo`, and this link chains into `isolation_demo` in turn.

use crate::serial_println;
use x86_64::structures::paging::PageTableFlags;
use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{isolation_demo, pe, usermode, win32, win32_fs};
use crate::memory::frame_allocator::BumpFrameAllocator;
use crate::memory::paging;

/// `int $0x91`: this link's completion trap (0x90 is the kernel32 service
/// gate itself).
pub const TRAP_VECTOR: u8 = 0x91;

const KERNEL32_DLL: &[u8] = include_bytes!("../../userprog/kernel32.dll");
const APP_EXE: &[u8] = include_bytes!("../../userprog/win32_console.exe");

/// The client's own load address and `SizeOfImage` (`objdump -p` reports
/// `ImageBase 0x1a0000000000`, `SizeOfImage 0x4000`). The size is the region
/// the surface will accept argument pointers in.
const APP_IMAGE_BASE: u64 = 0x1A00_0000_0000;
const APP_IMAGE_SIZE: u64 = 0x4000;

/// The private report page the client writes its results to, and its stack.
const SLOT_ADDR: u64 = 0x4D00_0000_0000;
const USER_STACK_ADDR: u64 = 0x4D10_0000_0000;

/// Exactly what the client's console output must be: the bytes of
/// `C:\nyrqis\hello.txt`, which the client wrote with `WriteFile` after
/// reading them with `ReadFile`. The boot also asserts this equals the
/// volume's own copy of that file, so the fixture and the expectation cannot
/// drift apart silently.
const EXPECTED_STDOUT: &[u8] = b"hello from the read-only volume\r\n";
/// The code the client passes to `ExitProcess`.
const EXPECTED_EXIT_CODE: u64 = 7;

/// The command line this kernel's loader places for the client: the image path
/// and no arguments, because nothing passes any. A loader would have put this
/// string in the process's environment block long before the first
/// instruction; here it goes into the client's own report page (below), and
/// `GetCommandLineA` answers with that address.
const CMDLINE: &[u8] = b"C:\\nyrqis\\win32_console.exe";
/// Where [`CMDLINE`] lives in the report page: above the report fields and
/// their string area, and BELOW 0x300, where the kernel-side controls write the
/// paths they hand the surface. Pinned by the assertion under the slot
/// constants so a new field cannot land on it.
const CMDLINE_OFFSET: u64 = 0x2A0;

/// How many refusals the surface must have counted during the client's run.
///
/// Not zero any more, and deliberately so: a real program that uses a handle it
/// has already closed is making a mistake, and this surface answers it with a
/// typed refusal instead of a silent zero. The client does exactly that twice
/// on purpose -- `ReadFile` and a second `CloseHandle` on the closed handle --
/// and it also asks for `GENERIC_WRITE` on a read-only volume, which is a
/// refusal too. This tranche adds two more, both of them questions a client is
/// entitled to ask and entitled to a Windows answer for: `GetModuleHandleA` for
/// a module that is not loaded, and `GetProcAddress` for an export that module
/// does not have. So the surface must have refused exactly those five. A
/// different number means either an extra refusal (a bug in the surface) or a
/// missing one (the client never reached the probe), and the per-field
/// assertions below pin which.
const EXPECTED_REFUSALS: u64 = 5;

/// The twelve exports the client imports, and the RVAs they must resolve to
/// (the DLL's own export table is the authority; these are the independent
/// answer the boot checks it against, per symbol). Sorted by name, which is the
/// order GNU ld writes its name table in -- so the stubs appended last in the
/// source for a stable `.text` layout sort into the middle.
const EXPECTED_EXPORTS: [(&[u8], u64); 12] = [
    (b"CloseHandle", 0x10F1),
    (b"CreateFileA", 0x1077),
    (b"ExitProcess", 0x1062),
    (b"GetCommandLineA", 0x1137),
    (b"GetLastError", 0x110D),
    (b"GetModuleHandleA", 0x114C),
    (b"GetProcAddress", 0x1168),
    (b"GetStdHandle", 0x1000),
    (b"GetTickCount", 0x104D),
    (b"ReadFile", 0x10B4),
    (b"SetLastError", 0x1122),
    (b"WriteFile", 0x101C),
];

/// The RVA the pinned table above gives `name`, for a check that has to name one
/// export rather than loop over all of them (the runtime binding binds
/// `GetTickCount`, and the expectation must come from the same pin the IAT check
/// used -- two answers written twice would be two things to keep in step).
fn expected_rva(name: &[u8]) -> u64 {
    EXPECTED_EXPORTS
        .iter()
        .find(|(pinned, _)| *pinned == name)
        .map(|(_, rva)| *rva)
        .unwrap_or_else(|| panic!("{:?} is not one of the pinned exports", NameOf(name)))
}

// The client's report fields, mirroring `userprog/prog_win32_app.s`.
const SLOT_WRITE_RET: u64 = 0;
const SLOT_WRITTEN: u64 = 8;
const SLOT_TICK1: u64 = 16;
const SLOT_TICK2: u64 = 24;
const SLOT_FILE1: u64 = 32;
const SLOT_R1: u64 = 40;
const SLOT_N1: u64 = 48;
const SLOT_R2: u64 = 56;
const SLOT_N2: u64 = 64;
const SLOT_R3: u64 = 72;
const SLOT_N3: u64 = 80;
const SLOT_CLOSE1: u64 = 88;
const SLOT_FILE2: u64 = 96;
const SLOT_R4: u64 = 104;
const SLOT_N4: u64 = 112;
const SLOT_CLOSE2: u64 = 120;
const SLOT_FILE3: u64 = 128;
const SLOT_R5: u64 = 136;
const SLOT_N5: u64 = 144;
const SLOT_CLOSE3: u64 = 152;
const SLOT_FILE4: u64 = 160;
const SLOT_CLOSE4: u64 = 168;
const SLOT_R6: u64 = 176;
const SLOT_N6: u64 = 184;
const SLOT_CLOSE4B: u64 = 192;
const SLOT_FILE5: u64 = 200;
// The error-code fields sit ABOVE the four read buffers (0x100, 0x140, 0x180,
// 0x200) and BELOW the 0x300 area the kernel-side controls write paths into.
// They were first numbered from +208, and the last of them landed on 0x100 --
// on top of the buffer holding the file bytes the client writes to the console,
// which produced a transcript with a correct console message and a false
// read-back check. Pinned here rather than only in the fixture so that collision
// cannot come back silently.
const SLOT_ERR_CLOSED_READ: u64 = 0x210;
const SLOT_ERR_CLOSED_CLOSE: u64 = 0x218;
const SLOT_ERR_MISSING: u64 = 0x220;
const SLOT_ERR_SET: u64 = 0x228;
const SLOT_FILE6: u64 = 0x230;
const SLOT_ERR_ACCESS: u64 = 0x238;
const SLOT_ERR_KEPT: u64 = 0x240;
/// The client's own handle test on the REFUSED `CreateFileA`: 1 when the
/// refusal's result compared equal to `INVALID_HANDLE_VALUE`. A field of its
/// own rather than a derived expectation, because the point is exactly that
/// the client -- not the kernel -- performs Windows' test on the value it got.
const SLOT_HANDLE_TEST: u64 = 0x248;
// The startup pair's answers, above the error-code fields and below
// CMDLINE_OFFSET.
/// The pointer the client's first `GetCommandLineA` returned.
const SLOT_CMDLINE: u64 = 0x250;
/// The pointer its second call returned: Windows answers the same one for the
/// life of the process, so the two fields are compared, not just the bytes.
const SLOT_CMDLINE2: u64 = 0x258;
/// 1 when the client compared the bytes at that pointer with its own copy and
/// they matched, NUL included.
const SLOT_CMDLINE_OK: u64 = 0x260;
/// The `HMODULE` the client's `GetModuleHandleA(NULL)` returned.
const SLOT_MODULE: u64 = 0x268;
/// 1 when the client's own `_start` lay inside `[hmod, hmod + SizeOfImage)`.
const SLOT_MODULE_OK: u64 = 0x270;
// The runtime binding's answers. The first five fill the gap between the startup
// fields and the installed command line at CMDLINE_OFFSET; the four negative
// probes sit above that string and below the 0x300 region.
/// The base `GetModuleHandleA("kernel32.dll")` returned.
const SLOT_NAMED_MODULE: u64 = 0x278;
/// The same ask, spelled `"KERNEL32.DLL"`: module names are case-blind.
const SLOT_NAMED_MODULE2: u64 = 0x280;
/// The address `GetProcAddress(hmod32, "GetTickCount")` returned.
const SLOT_PROC_TICK: u64 = 0x288;
/// 1 when that address equaled the IAT slot the loader filled.
const SLOT_PROC_TICK_SAME: u64 = 0x290;
/// The value of the client calling THROUGH that pointer.
const SLOT_PROC_TICK_VALUE: u64 = 0x298;
/// `GetModuleHandleA("nope.dll")` -> 0, and the code it left behind.
const SLOT_NAMED_NONE: u64 = 0x2C0;
const SLOT_NAMED_NONE_ERR: u64 = 0x2C8;
/// `GetProcAddress(hmod32, "NoSuchExport")` -> 0, and its code.
const SLOT_PROC_NONE: u64 = 0x2D0;
const SLOT_PROC_NONE_ERR: u64 = 0x2D8;
// A compile-time check that the report fields do not overlap the client's read
// buffers (which run up to SLOT_BIN + its eight bytes), the command-line string
// the loader installs, the 0x300 region the kernel-side controls write their
// test paths into, or EACH OTHER. The collision this prevents was real: it
// produced a green-looking console line and a failed read-back in the same boot.
const _: () = assert!(
    SLOT_ERR_CLOSED_READ >= SLOT_BIN + 8
        && SLOT_MODULE_OK + 8 <= SLOT_NAMED_MODULE
        && SLOT_PROC_TICK_VALUE + 8 <= CMDLINE_OFFSET
        && CMDLINE_OFFSET + CMDLINE.len() as u64 + 1 <= SLOT_NAMED_NONE
        && SLOT_PROC_NONE_ERR + 8 <= 0x300,
    "win32-demo report fields must sit between the read buffers and 0x300, must not overlap each other, and must not overlap the installed command line"
);

const SLOT_BUF: u64 = 0x100;
const SLOT_BIN: u64 = 0x200;

/// The pages this stage's kernel-side work is given, deliberately rather than
/// by default.
///
/// # Why an explicit budget, and why this stage needed one
///
/// This demo did NOT run on its own stack before the error-code tranche: both
/// `start` and the `on_trap` continuation ran as ordinary calls on the stack its
/// predecessor (`pe_resource_demo`) happened to be using, and that stack was
/// already carrying the frames of every demo before it. The third tranche's few
/// hundred bytes of locals and control code were enough to cross the edge, and
/// the guard page below the stack caught it exactly as designed -- a #DF in
/// `kernel_stack`'s region, zero corruption, no panic message to guess at.
///
/// So this stage now follows the chain-partition rule every sibling follows
/// (`chain::run_on_fresh_stack`, see `pe_reloc_demo`'s note): its kernel-side
/// work runs on ITS OWN guarded RSP0 stack, and the budget is stated here. Eight
/// pages is the same figure the accumulated-chain era needed and the figure
/// `pe_reloc_demo` still states, because the failure shape is the same -- this
/// stage plus the `on_trap` continuation that chains into `isolation_demo` with
/// an ordinary call, not one stage in isolation. Borrowing a predecessor's
/// stack is what made the overflow depend on unrelated demos' frame sizes.
const WIN32_STACK_PAGES: u64 = 8;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;
/// This demo's address space, stashed for the stage-two continuation (the
/// chain-partition rule: no caller frame survives the stack switch).
static mut STAGE_SPACE: Option<AddressSpace> = None;

/// Reads one qword of the client's report page.
///
/// # Safety
/// The page is mapped user-accessible and readable in this demo's space.
unsafe fn slot(offset: u64) -> u64 {
    core::ptr::read_volatile((SLOT_ADDR + offset) as *const u64)
}

/// Views `len` bytes of the client's report page as a slice (the file bytes it
/// read back turn up here, next to the counts it got with them).
///
/// # Safety
/// Same contract as [`slot`]; the callers pass offsets inside the page.
unsafe fn slot_bytes(offset: u64, len: usize) -> &'static [u8] {
    core::slice::from_raw_parts((SLOT_ADDR + offset) as *const u8, len)
}

/// Builds this stage's address space (with [`WIN32_STACK_PAGES`], see that
/// constant's note) and jumps onto its own kernel stack, where the proof runs.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep already initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    let space = AddressSpace::new_sharing_kernel_with_stack_pages(fa, WIN32_STACK_PAGES)
        .unwrap_or_else(|_| panic!("failed to build win32-demo address space"));
    space.activate();
    STAGE_SPACE = Some(space);
    // The chain-partition rule: everything below runs on this space's own
    // guarded RSP0 stack, so this stage's budget does not depend on how deep
    // the demos before it left the shared one.
    crate::experimental::chain::run_on_fresh_stack(
        (*&raw const STAGE_SPACE).as_ref().unwrap(),
        stage_two_after_stack_switch,
    )
}

/// The whole proof, running on THIS space's own kernel stack: it loads
/// `kernel32.dll` and the console client, runs the import and surface negative
/// controls, resolves the client's imports for real, and enters ring 3 at the
/// client's entry point. [`on_trap`] (vector 0x91) reports the results and
/// chains into `experimental::isolation_demo`.
///
/// # Safety
/// Reached only through [`start`]'s trampoline; the statics it reads are set
/// there, and no caller frame survives the switch.
unsafe extern "C" fn stage_two_after_stack_switch() -> ! {
    let space = (*&raw mut STAGE_SPACE)
        .as_mut()
        .expect("win32-demo stage space missing");
    let mut mapper = space.mapper();
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    // --- the provider: a real DLL, its real export directory ---
    let dll = pe::load(KERNEL32_DLL, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load kernel32.dll: {:?}", e));
    let export = pe::DllExportTable::parse(win32::IMAGE_BASE, dll.image_max_rva)
        .unwrap_or_else(|e| panic!("kernel32 export table failed to parse: {:?}", e));
    let declared = export
        .declared_name()
        .unwrap_or_else(|e| panic!("kernel32 export table name failed: {:?}", e))
        .unwrap_or_else(|| panic!("kernel32 export table declares no DLL name"));
    assert!(
        declared.as_slice() == b"kernel32.dll",
        "the provider must declare the name the client's import descriptor uses"
    );
    assert_eq!(
        export.name_count(),
        EXPECTED_EXPORTS.len() as u32,
        "kernel32.dll must export exactly the functions this surface implements"
    );

    // The layout pins: every export inside `.text`, and the call block's magic
    // where `win32::BLOCK_RVA` says it is. A fixture whose layout moved must
    // fail HERE, not hand the kernel a stale address.
    for (name, rva) in EXPECTED_EXPORTS {
        let addr = export
            .lookup(name)
            .unwrap_or_else(|| panic!("kernel32.dll does not export {:?}", NameOf(name)));
        let offset = addr - win32::IMAGE_BASE;
        assert!(
            (win32::TEXT_RVA..win32::TEXT_RVA + win32::TEXT_SIZE).contains(&offset),
            "export {:?} at +{:#x} is outside kernel32's .text window (+{:#x}..+{:#x})",
            NameOf(name),
            offset,
            win32::TEXT_RVA,
            win32::TEXT_RVA + win32::TEXT_SIZE
        );
        assert_eq!(
            offset, rva,
            "export {:?} RVA drifted from the pinned toolchain answer",
            NameOf(name)
        );
    }
    let block_magic =
        core::ptr::read_volatile((win32::IMAGE_BASE + win32::BLOCK_RVA) as *const u64);
    assert_eq!(
        block_magic,
        win32::BLOCK_MAGIC,
        "the call block is not where the kernel pins it -- the fixture's .data moved"
    );
    serial_println!(
        "  experimental win32-demo: kernel32.dll loaded at its own ImageBase {:#x}; export table declares {:?} with {} name(s), all inside .text (+{:#x}..+{:#x}); call block magic {:#x} at +{:#x} -- OK",
        win32::IMAGE_BASE,
        declared,
        export.name_count(),
        win32::TEXT_RVA,
        win32::TEXT_RVA + win32::TEXT_SIZE,
        block_magic,
        win32::BLOCK_RVA
    );

    // --- the client: real import directory, not yet resolved ---
    let app = pe::load(APP_EXE, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load the Win32 console client: {:?}", e));
    serial_println!(
        "  experimental win32-demo: console client loaded at {:#x} ({} bytes of image, entry {:#x}) -- its imports are still unbound",
        APP_IMAGE_BASE,
        app.image_max_rva,
        app.entry_point.as_u64()
    );

    // Negative control 1: no registry at all -- the walk is refused by name.
    match pe::resolve_imports(APP_IMAGE_BASE, &mut mapper, app.image_max_rva, &pe::DllRegistry::new())
    {
        Err(pe::PeError::MissingDllImport) => serial_println!(
            "  experimental win32-demo: negative control 1 -- an empty registry refused the client's import walk with the typed MissingDllImport -- OK"
        ),
        other => panic!(
            "negative control 1 failed: expected MissingDllImport, got {:?}",
            other.err()
        ),
    }

    // Negative control 2: the right NAME bound to the wrong FILE (the client's
    // own empty export table) -- per-symbol refusal, before any patch.
    let client_exports = pe::DllExportTable::parse(APP_IMAGE_BASE, app.image_max_rva)
        .unwrap_or_else(|e| panic!("client export probe failed: {:?}", e));
    assert!(
        client_exports.is_empty(),
        "the client executable must have no export directory of its own"
    );
    let mut wrong_reg = pe::DllRegistry::new();
    wrong_reg.add(declared.as_slice(), Some(client_exports));
    match pe::resolve_imports(APP_IMAGE_BASE, &mut mapper, app.image_max_rva, &wrong_reg) {
        Err(pe::PeError::MissingImport) => serial_println!(
            "  experimental win32-demo: negative control 2 -- kernel32.dll's name bound to the CLIENT's own empty export table refused every symbol with the typed MissingImport -- OK, the registry identity is the file"
        ),
        other => panic!(
            "negative control 2 failed: expected MissingImport, got {:?}",
            other.err()
        ),
    }

    // --- the real resolution: the client's IAT against kernel32's EAT ---
    let mut registry = pe::DllRegistry::new();
    registry.add(declared.as_slice(), Some(export));
    let resolved = pe::resolve_imports(APP_IMAGE_BASE, &mut mapper, app.image_max_rva, &registry)
        .unwrap_or_else(|e| panic!("real import resolution failed: {:?}", e));
    assert_eq!(
        resolved.facts.len(),
        EXPECTED_EXPORTS.len(),
        "exactly the twelve kernel32 imports must resolve"
    );
    for (name, rva) in EXPECTED_EXPORTS {
        let fact = resolved
            .facts
            .iter()
            .find(|f| f.symbol.as_slice() == name)
            .unwrap_or_else(|| panic!("the {:?} import did not resolve", NameOf(name)));
        assert_eq!(
            fact.value,
            win32::IMAGE_BASE + rva,
            "the {:?} IAT slot must hold kernel32's own export address",
            NameOf(name)
        );
    }
    serial_println!(
        "  experimental win32-demo: resolved {} import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (GetStdHandle, WriteFile, GetTickCount, ExitProcess, CreateFileA, ReadFile, CloseHandle, GetLastError, SetLastError, GetCommandLineA, GetModuleHandleA, GetProcAddress), every slot reading back its export address -- OK",
        resolved.facts.len()
    );

    // --- the surface's own negative controls, kernel-side, before ring 3 ---
    win32::install(APP_IMAGE_BASE, APP_IMAGE_SIZE, SLOT_ADDR, 4096);

    // Private report page + the client's stack. Mapped BEFORE any negative
    // control runs, because the volume controls below hand the surface paths
    // that live in this page -- the surface must see text a real client could
    // have produced, from a region it would actually accept.
    let flags = PageTableFlags::PRESENT
        | PageTableFlags::WRITABLE
        | PageTableFlags::USER_ACCESSIBLE
        | PageTableFlags::NO_EXECUTE;
    for base in [SLOT_ADDR, USER_STACK_ADDR] {
        let mapped = paging::map_range(&mut mapper, fa, VirtAddr::new(base), 1, flags);
        assert_eq!(mapped, 1, "failed to map a win32-demo page at {:#x}", base);
    }

    // A frame allocator hands back whatever it holds, so the report page is
    // zeroed explicitly (both the report fields and the buffers the client
    // reads into): a field the client never reaches reads as 0, never as the
    // previous demo's bytes, and a partially-written out-parameter cannot be
    // mistaken for a full one.
    for i in 0..(4096 / 8) {
        core::ptr::write_volatile((SLOT_ADDR + i * 8) as *mut u64, 0);
    }
    let from_text = win32::IMAGE_BASE + win32::TEXT_RVA + 4;

    // Negative control 3 -- and the start of the last-error contract: before
    // any call has failed, the code is ERROR_SUCCESS, exactly as a Windows
    // thread's TEB starts. A surface that started at random would make a
    // client's first GetLastError() a lie about a call it never made.
    assert_eq!(
        win32::last_error(),
        0,
        "the last error must start at ERROR_SUCCESS before any call"
    );
    expect_refusal(12, [0; 5], from_text, win32::Win32Error::UnknownFunction);
    serial_println!(
        "  experimental win32-demo: negative control 3 -- index 12 refused with the typed UnknownFunction -- OK"
    );
    expect_refusal(
        0,
        [(-11i64) as u64, 0, 0, 0, 0],
        win32::IMAGE_BASE + 8,
        win32::Win32Error::CallerOutsideKernel32,
    );
    serial_println!(
        "  experimental win32-demo: negative control 4 -- a trap at kernel32+0x8 (outside .text) refused with the typed CallerOutsideKernel32 -- OK, the index is a claim and the trapping RIP is the evidence"
    );
    expect_refusal(
        1,
        [0x99, APP_IMAGE_BASE, 8, 0, 0],
        from_text,
        win32::Win32Error::InvalidHandle,
    );
    expect_refusal(
        1,
        [win32::STDOUT_HANDLE, 0x10, 8, 0, 0],
        from_text,
        win32::Win32Error::BufferOutsideClient,
    );
    expect_refusal(
        1,
        [
            win32::STDOUT_HANDLE,
            APP_IMAGE_BASE,
            win32::MAX_WRITE as u64 + 1,
            0,
            0,
        ],
        from_text,
        win32::Win32Error::LengthTooLarge,
    );
    expect_refusal(
        1,
        [win32::STDOUT_HANDLE, APP_IMAGE_BASE, 8, 0x10, 0],
        from_text,
        win32::Win32Error::OutPointerOutsideClient,
    );
    serial_println!(
        "  experimental win32-demo: negative control 5 -- WriteFile refused an unissued handle (InvalidHandle), an out-of-image buffer (BufferOutsideClient), a {} byte length (LengthTooLarge) and an out-of-image out-parameter (OutPointerOutsideClient) -- OK, every argument is checked before the kernel touches it",
        win32::MAX_WRITE + 1
    );

    // Negative control 6: the volume's own rules. The paths live in the
    // client's report page, so the surface sees pointers a real client could
    // have produced -- not kernel addresses that skip the region check.
    let hello = write_slot_string(0x300, b"C:\\nyrqis\\hello.txt");
    let relative = write_slot_string(0x340, b"hello.txt");
    let other_drive = write_slot_string(0x360, b"D:\\nyrqis\\hello.txt");
    let control_byte = write_slot_string(0x390, b"C:\\nyrqis\\x\x01");
    // A path whose LAST mapped byte is not a NUL: the surface must stop at the
    // region's edge with a typed refusal, not read the page beyond it.
    core::ptr::write_volatile((SLOT_ADDR + 0xFFF) as *mut u8, b'A');
    let unterminated = SLOT_ADDR + 0xFFF;
    let missing = write_slot_string(0x3C0, b"C:\\nyrqis\\missing.txt");
    let read = win32::GENERIC_READ as u64;
    let open_existing = 3u64;
    expect_refusal(
        4,
        [relative, read, 1, 0, open_existing],
        from_text,
        win32::Win32Error::File(win32_fs::FsError::PathNotAbsolute),
    );
    expect_refusal(
        4,
        [other_drive, read, 1, 0, open_existing],
        from_text,
        win32::Win32Error::File(win32_fs::FsError::UnknownVolume),
    );
    expect_refusal(
        4,
        [control_byte, read, 1, 0, open_existing],
        from_text,
        win32::Win32Error::PathNotText,
    );
    expect_refusal(
        4,
        [unterminated, read, 1, 0, open_existing],
        from_text,
        win32::Win32Error::PathTooLong,
    );
    expect_refusal(
        4,
        [0x10, read, 1, 0, open_existing],
        from_text,
        win32::Win32Error::PathOutsideClient,
    );
    expect_refusal(
        4,
        [hello, 0x4000_0000, 1, 0, open_existing],
        from_text,
        win32::Win32Error::UnsupportedAccess,
    );
    expect_refusal(
        4,
        [hello, read, 1, 0, 2],
        from_text,
        win32::Win32Error::UnsupportedDisposition,
    );
    expect_refusal(
        5,
        [win32::STDOUT_HANDLE, SLOT_ADDR + 0x400, 8, SLOT_ADDR + 0x408, 1],
        from_text,
        win32::Win32Error::OverlappedNotSupported,
    );
    expect_refusal(
        5,
        [win32::STDOUT_HANDLE, SLOT_ADDR + 0x400, 8, SLOT_ADDR + 0x408, 0],
        from_text,
        win32::Win32Error::InvalidHandle,
    );
    expect_refusal(
        5,
        [0x500, SLOT_ADDR + 0x400, 8, SLOT_ADDR + 0x408, 0],
        from_text,
        win32::Win32Error::File(win32_fs::FsError::NotAFileHandle),
    );
    expect_refusal(
        6,
        [win32::STDOUT_HANDLE, 0, 0, 0, 0],
        from_text,
        win32::Win32Error::File(win32_fs::FsError::NotAFileHandle),
    );
    // A miss is a RETURN VALUE on Windows, not an error: INVALID_HANDLE_VALUE.
    assert_eq!(
        win32::dispatch(4, [missing, read, 1, 0, open_existing], from_text),
        Ok(win32::INVALID_HANDLE_VALUE),
        "CreateFileA must report a missing file as INVALID_HANDLE_VALUE, not as a refusal"
    );
    // The handle table has a real bound, and it is a refusal when reached.
    let mut opened = [0u64; win32_fs::MAX_OPEN];
    for (slot, handle) in opened.iter_mut().enumerate() {
        *handle = win32::dispatch(4, [hello, read, 1, 0, open_existing], from_text)
            .unwrap_or_else(|e| panic!("opening the volume file failed: {:?}", e));
        assert_eq!(*handle, win32_fs::handle_at(slot), "handles are handed out in slot order");
    }
    expect_refusal(
        4,
        [hello, read, 1, 0, open_existing],
        from_text,
        win32::Win32Error::File(win32_fs::FsError::OutOfHandles),
    );
    for handle in opened {
        assert_eq!(
            win32::dispatch(6, [handle, 0, 0, 0, 0], from_text),
            Ok(1),
            "CloseHandle must release a handle the volume issued"
        );
    }
    assert_eq!(
        win32_fs::open_count(),
        0,
        "the negative controls must leave no file open for the client's run"
    );
    serial_println!(
        "  experimental win32-demo: negative control 6 -- the read-only volume refused a relative path (PathNotAbsolute), drive D: (UnknownVolume), a control byte in a path (PathNotText), a path with no NUL inside the client's page (PathTooLong), a pointer outside the client (PathOutsideClient), GENERIC_WRITE (UnsupportedAccess), disposition 2 (UnsupportedDisposition), a non-NULL lpOverlapped (OverlappedNotSupported), the console handle as a file (InvalidHandle / NotAFileHandle) and a {}th open (OutOfHandles); a missing file returned INVALID_HANDLE_VALUE as Windows does, and every handle was closed again -- OK",
        win32_fs::MAX_OPEN + 1
    );

    // Negative control 7: the error-code TABLE, pinned. The controls above
    // prove the codes of the refusals they fire; this proves the rest of the
    // table, so a variant that no control reaches (a non-text console write, a
    // relative path's ERROR_PATH_NOT_FOUND, ...) cannot silently report a
    // made-up number. Pinned as literals rather than through `.code()` itself,
    // which would only prove the function is deterministic.
    // Twenty-eight rows: twenty-two leaf variants plus the six volume rules.
    // Adding a variant without its Windows code must therefore fail HERE, at
    // compile time, rather than at a call site the boot never reaches.
    let codes: [(win32::Win32Error, u32); 28] = [
        (win32::Win32Error::UnknownFunction, 1),
        (win32::Win32Error::CallerOutsideKernel32, 487),
        (win32::Win32Error::InvalidHandle, 6),
        (win32::Win32Error::BufferOutsideClient, 998),
        (win32::Win32Error::LengthTooLarge, 87),
        (win32::Win32Error::OutPointerOutsideClient, 998),
        (win32::Win32Error::NotUtf8, 87),
        (win32::Win32Error::PathOutsideClient, 998),
        (win32::Win32Error::PathTooLong, 206),
        (win32::Win32Error::PathNotText, 123),
        (win32::Win32Error::UnsupportedAccess, 5),
        (win32::Win32Error::UnsupportedDisposition, 87),
        (win32::Win32Error::OverlappedNotSupported, 50),
        (win32::Win32Error::LoaderStateMissing, 21),
        (win32::Win32Error::ModuleNotFound, 126),
        (win32::Win32Error::ModuleNotLoaded, 6),
        (win32::Win32Error::ModuleTableFull, 14),
        (win32::Win32Error::ModuleInstallInvalid, 87),
        (win32::Win32Error::ProcNotFound, 127),
        (win32::Win32Error::OrdinalNotSupported, 50),
        (win32::Win32Error::NameInvalid, 87),
        (win32::Win32Error::CommandLineInvalid, 87),
        (win32::Win32Error::File(win32_fs::FsError::PathNotAbsolute), 3),
        (win32::Win32Error::File(win32_fs::FsError::UnknownVolume), 15),
        (win32::Win32Error::File(win32_fs::FsError::FileNotFound), 2),
        (win32::Win32Error::File(win32_fs::FsError::OutOfHandles), 4),
        (win32::Win32Error::File(win32_fs::FsError::NotAFileHandle), 6),
        (win32::Win32Error::File(win32_fs::FsError::ClosedHandle), 6),
    ];
    for (error, code) in codes {
        assert_eq!(
            error.code(),
            code,
            "{:?} must report the Windows code {}",
            error,
            code
        );
    }

    // A SUCCESSFUL call must not clear the code: after the certifications
    // above the cell holds 2 (the miss), and a successful open must leave it
    // there -- otherwise a client that checks GetLastError after a call that
    // worked would read success as a failure. (The client proves the same from
    // ring 3 with a successful GetTickCount.)
    win32::set_last_error(win32::ERROR_FILE_NOT_FOUND);
    let rb = win32::dispatch(4, [hello, read, 1, 0, open_existing], from_text)
        .unwrap_or_else(|e| panic!("reopening a volume file failed: {:?}", e));
    assert_eq!(rb, win32_fs::handle_at(0), "a successful open returns slot 0's handle");
    assert_eq!(
        win32::last_error(),
        win32::ERROR_FILE_NOT_FOUND,
        "a successful call must leave the last error alone"
    );
    assert_eq!(win32::dispatch(6, [rb, 0, 0, 0, 0], from_text), Ok(1));
    // SetLastError is the writer half of the pair, so the kernel-side proof
    // exercises it directly too, including that a DWORD-sized argument is read
    // as its low 32 bits (0x1234 must not come back as a wider value).
    win32::set_last_error(0xDEAD_BEEF);
    assert_eq!(win32::last_error(), 0xDEAD_BEEF);
    assert_eq!(
        win32::dispatch(8, [0x1234, 0, 0, 0, 0], from_text),
        Ok(0),
        "SetLastError returns void"
    );
    assert_eq!(win32::dispatch(7, [0; 5], from_text), Ok(0x1234));
    assert_eq!(
        win32_fs::open_count(),
        0,
        "the error-code controls must leave no file open either"
    );
    serial_println!(
        "  experimental win32-demo: negative control 7 -- the last-error cell started at ERROR_SUCCESS (0), every one of the {} typed refusals answered its pinned Windows code (unknown function 1, trap outside .text 487, invalid handle 6, out-of-region buffer/out-parameter/path 998, oversized length 87, unterminated path 206, non-text path 123, write access 5, bad disposition 87, overlapped I/O 50, loader state missing 21, unloaded module 126, unregistered module handle 6, full module table 14, bad module row 87, absent export 127, ordinal form 50, unreadable name 87, bad loader install 87, relative path 3, unknown drive 15, missing file 2, exhausted table 4), a successful open left the code alone, and SetLastError(0x1234) read back through GetLastError -- OK",
        codes.len()
    );

    // Negative control 8: the failure VALUE of a refused call, per export.
    //
    // The client's first question is what the call RETURNED, and only its
    // second is why, so a refusal that answered 0 for everything would make a
    // refused `CreateFileA` look like a handle to any client that tests
    // `h == INVALID_HANDLE_VALUE` -- the test Windows' prototype tells it to
    // write. This pins the value for EVERY export, not just the two the client
    // refuses: an export added with the wrong row must fail here. Pinned as
    // literals rather than through `failure_value` itself, which would only
    // prove the function is deterministic.
    let failure_values: [(u64, u64); 12] = [
        (0, win32::INVALID_HANDLE_VALUE), // GetStdHandle      -> HANDLE
        (1, 0),                           // WriteFile         -> BOOL
        (2, 0),                           // GetTickCount      -> DWORD
        (3, 0),                           // ExitProcess       -> void
        (4, win32::INVALID_HANDLE_VALUE), // CreateFileA       -> HANDLE
        (5, 0),                           // ReadFile          -> BOOL
        (6, 0),                           // CloseHandle       -> BOOL
        (7, 0),                           // GetLastError      -> DWORD
        (8, 0),                           // SetLastError      -> void
        (9, 0),                           // GetCommandLineA   -> LPSTR (NULL)
        (10, 0),                          // GetModuleHandleA  -> HMODULE, but
                                          //   its documented failure is NULL
        (11, 0),                          // GetProcAddress    -> FARPROC, whose
                                          //   documented failure is also NULL
    ];
    for (index, value) in failure_values {
        assert_eq!(
            win32::failure_value(index),
            value,
            "export {} must answer its Windows failure value {:#x} when refused",
            index,
            value
        );
    }
    assert_eq!(
        win32::failure_value(12),
        0,
        "an index that names no export has no prototype to borrow a value from"
    );
    serial_println!(
        "  experimental win32-demo: negative control 8 -- a refused call answers with its export's own Windows failure value: INVALID_HANDLE_VALUE from GetStdHandle and CreateFileA (HANDLE), 0 from WriteFile, ReadFile and CloseHandle (BOOL), 0 from GetTickCount and GetLastError (DWORD), 0 from ExitProcess and SetLastError (void), 0 from GetCommandLineA, GetModuleHandleA and GetProcAddress (all three document NULL, even though HMODULE is a handle type and FARPROC is a code pointer), and 0 from an index that names no export -- OK, so the client's handle test reads a refusal exactly as Windows writes it"
    );

    // Negative control 9: the loader state the startup pair describes.
    //
    // # Why this is refused BEFORE it is installed
    //
    // GetCommandLineA is the FIRST call a CRT makes, so the interesting
    // failure is the one Windows never has: a surface asked for a command line
    // that its loader never placed. Ordered first here, while the cell is still
    // empty, so the refusal is a fact about this boot rather than a claim about
    // a code path. A `Ok(0)` here would hand the client a NULL it would then
    // dereference.
    assert_eq!(
        win32::command_line(),
        None,
        "the command line must not exist before the loader installs one"
    );
    expect_refusal(
        9,
        [0; 5],
        from_text,
        win32::Win32Error::LoaderStateMissing,
    );
    // A bad install is refused by name and leaves the state alone: not text,
    // and a range that runs off the end of the client's page. The second one
    // matters most -- a pointer the client cannot read is the only failure mode
    // of this API that ring 3 could not diagnose for itself.
    let cmdline_at = write_slot_string(CMDLINE_OFFSET, CMDLINE);
    assert_eq!(
        win32::install_cmdline(cmdline_at, b"C:\\nyrqis\\x\x01"),
        Err(win32::Win32Error::CommandLineInvalid),
        "non-text command line must be refused at install time"
    );
    assert_eq!(
        win32::install_cmdline(SLOT_ADDR + 0x1000 - 6, b"C:\\nyrqis"),
        Err(win32::Win32Error::CommandLineInvalid),
        "a command line that runs past the client's page must be refused"
    );
    assert_eq!(
        win32::command_line(),
        None,
        "a refused install must not publish a pointer"
    );
    // The real install: the loader's own act, after which the call answers.
    win32::install_cmdline(cmdline_at, CMDLINE)
        .unwrap_or_else(|e| panic!("installing the command line failed: {:?}", e));
    assert_eq!(win32::command_line(), Some(cmdline_at));
    // Twice, and both times the same address -- a client may cache the pointer,
    // and nothing here recomputes it.
    assert_eq!(win32::dispatch(9, [0; 5], from_text), Ok(cmdline_at));
    assert_eq!(win32::dispatch(9, [0; 5], from_text), Ok(cmdline_at));
    // The bytes really are there, NUL-terminated: a pointer is only an answer
    // if the client can read the string it names.
    let installed = core::slice::from_raw_parts(cmdline_at as *const u8, CMDLINE.len() + 1);
    let mut copied = true;
    for (i, want) in CMDLINE.iter().enumerate() {
        copied &= installed.get(i) == Some(want);
    }
    // Windows' command line is a NUL-terminated ANSI string, so the byte after
    // the last character is part of the contract too.
    copied &= installed.get(CMDLINE.len()) == Some(&0u8);
    assert!(
        copied,
        "the installed command line must be readable at the answered pointer and NUL-terminated"
    );
    // GetModuleHandleA(NULL) answers the base the client was mapped at. A
    // NAMED module goes to the module table, which is still empty here -- so
    // the named form is "that module is not loaded", which is exactly what
    // ERROR_MOD_NOT_FOUND means, and not the refusal a missing feature got
    // before this tranche.
    assert_eq!(win32::dispatch(10, [0; 5], from_text), Ok(APP_IMAGE_BASE));
    expect_refusal(
        10,
        [cmdline_at, 0, 0, 0, 0],
        from_text,
        win32::Win32Error::ModuleNotFound,
    );
    // Both cell reads answer for the CLIENT after this, so the transcript's
    // values come from the surface's own state and not from these controls.
    serial_println!(
        "  experimental win32-demo: negative control 9 -- GetCommandLineA was refused with the typed LoaderStateMissing before any loader placed a string; a non-text install and one running past the client's page were refused as CommandLineInvalid and published nothing; the real install then answered the SAME pointer twice ({:#x}) with {:?} plus its NUL readable there; and GetModuleHandleA(NULL) answered the client's own image base {:#x} while a named module no row answers to was refused as ModuleNotFound -- OK, the startup pair reads loader state rather than inventing it",
        cmdline_at,
        NameOf(CMDLINE),
        APP_IMAGE_BASE
    );

    // Negative control 10: the module table `GetProcAddress` answers from.
    //
    // # Why the table is walked before ring 3 is allowed to ask
    //
    // `GetProcAddress` is only as good as the rows it looks in, and every row is
    // the LOADER's claim about what it mapped. So the whole surface of the table
    // is exercised kernel-side first: an empty table ("nothing is loaded" and
    // "no such module" are different answers and must not share a code), each
    // way a registration can be unusable, a duplicate name, the cap, every one of
    // the provider's exports BY NAME, an export the module does not have, the
    // same name in another case, the ordinal form, an unregistered base, and a
    // NULL name. Only then does the client ask, so whatever it gets back is an
    // answer the kernel has already checked from the other side.
    let dll_name = write_slot_string(0x3E0, b"kernel32.dll");
    let dll_name_upper = write_slot_string(0x3F0, b"KERNEL32.DLL");
    // An export name in the wrong case: the module name above is case-blind, an
    // export name is not, and that asymmetry is Windows'.
    let lower_export = write_slot_string(0x440, b"gettickcount");

    assert_eq!(
        win32::module_count(),
        0,
        "no module may be registered before the loader registers one"
    );
    // The two distinct "no" answers, before any row exists.
    expect_refusal(
        11,
        [win32::IMAGE_BASE, dll_name, 0, 0, 0],
        from_text,
        win32::Win32Error::LoaderStateMissing,
    );
    expect_refusal(
        10,
        [dll_name, 0, 0, 0, 0],
        from_text,
        win32::Win32Error::ModuleNotFound,
    );
    // Every unusable registration, refused by name and publishing nothing.
    //
    // The truncated-image one is the row that matters: a module whose recorded
    // image is smaller than the addresses its export table names would let
    // `GetProcAddress` hand ring 3 a pointer outside the image it claimed to
    // describe -- so the check is at install time, where the row can still be
    // refused whole.
    let bad_rows: [(&[u8], u64, u64); 5] = [
        (b"", win32::IMAGE_BASE, dll.image_max_rva),
        (b"bad\x01name", win32::IMAGE_BASE, dll.image_max_rva),
        (b"zero.dll", 0, dll.image_max_rva),
        (b"zeroimage.dll", win32::IMAGE_BASE, 0),
        (b"trunc.dll", win32::IMAGE_BASE, 0x1000),
    ];
    for (name, base, image_size) in bad_rows {
        assert_eq!(
            win32::install_module(name, base, image_size, export),
            Err(win32::Win32Error::ModuleInstallInvalid),
            "an unusable module row ({:?}, base {:#x}, image {:#x}) must be refused",
            NameOf(name),
            base,
            image_size
        );
    }
    assert_eq!(
        win32::module_count(),
        0,
        "a refused registration must not publish a row"
    );
    // The loader's real act: the provider, with its own export directory.
    win32::install_module(b"kernel32.dll", win32::IMAGE_BASE, dll.image_max_rva, export)
        .unwrap_or_else(|e| panic!("registering kernel32.dll as a module failed: {:?}", e));
    assert_eq!(win32::module_count(), 1);
    // ... and this process's own image, whose (legal, empty) export table is
    // what makes `GetProcAddress` against IT an `ERROR_PROC_NOT_FOUND` instead
    // of "unknown module": the table keys on the BASE, as Windows does, not on
    // a list of names that exist to be resolved.
    win32::install_module(
        b"win32_console.exe",
        APP_IMAGE_BASE,
        app.image_max_rva,
        client_exports,
    )
    .unwrap_or_else(|e| panic!("registering the client image as a module failed: {:?}", e));
    assert_eq!(win32::module_count(), 2);
    // Both spellings are one module; a duplicate row and a third row are not.
    assert_eq!(
        win32::dispatch(10, [dll_name, 0, 0, 0, 0], from_text),
        Ok(win32::IMAGE_BASE)
    );
    assert_eq!(
        win32::dispatch(10, [dll_name_upper, 0, 0, 0, 0], from_text),
        Ok(win32::IMAGE_BASE),
        "a module name is compared case-insensitively, as Windows compares it"
    );
    assert_eq!(
        win32::install_module(b"KERNEL32.DLL", win32::IMAGE_BASE, dll.image_max_rva, export),
        Err(win32::Win32Error::ModuleInstallInvalid),
        "a second row for a module already registered must be refused"
    );
    assert_eq!(
        win32::install_module(b"third.dll", win32::IMAGE_BASE, dll.image_max_rva, export),
        Err(win32::Win32Error::ModuleTableFull),
        "the table's cap must be a typed refusal, not an overflow"
    );
    assert_eq!(
        win32::module_count(),
        2,
        "neither a duplicate nor a full table may change the live rows"
    );
    // Every export of the provider, BY NAME, answered from that provider's own
    // export directory -- the same address the import walk bound into the IAT.
    // Names are copied into the client's page first so the surface reads exactly
    // what ring 3 would hand it, through its own checked reader.
    let mut named_resolved = 0;
    for (i, (name, rva)) in EXPECTED_EXPORTS.iter().enumerate() {
        let at = write_slot_string(0x500 + (i as u64) * 0x20, name);
        assert_eq!(
            win32::dispatch(11, [win32::IMAGE_BASE, at, 0, 0, 0], from_text),
            Ok(win32::IMAGE_BASE + rva),
            "GetProcAddress({:?}) must answer the provider's own export address",
            NameOf(name)
        );
        named_resolved += 1;
    }
    // The five refusals, each for its own reason and each with its own code.
    expect_refusal(
        11,
        [win32::IMAGE_BASE, lower_export, 0, 0, 0],
        from_text,
        win32::Win32Error::ProcNotFound,
    );
    expect_refusal(
        11,
        [APP_IMAGE_BASE, dll_name, 0, 0, 0],
        from_text,
        win32::Win32Error::ProcNotFound,
    );
    expect_refusal(
        11,
        [0x1234, dll_name, 0, 0, 0],
        from_text,
        win32::Win32Error::ModuleNotLoaded,
    );
    expect_refusal(
        11,
        [win32::IMAGE_BASE, 3, 0, 0, 0],
        from_text,
        win32::Win32Error::OrdinalNotSupported,
    );
    expect_refusal(
        11,
        [win32::IMAGE_BASE, 0, 0, 0, 0],
        from_text,
        win32::Win32Error::NameInvalid,
    );
    serial_println!(
        "  experimental win32-demo: negative control 10 -- with no module registered, GetProcAddress was refused as LoaderStateMissing and a named GetModuleHandleA as ModuleNotFound; five unusable rows (empty name, non-text name, zero base, zero image, an image too small for the exports it names) were refused as ModuleInstallInvalid and published nothing; kernel32.dll and win32_console.exe then registered, both spellings of the provider's name answered {:#x}, a duplicate and a third row were refused (ModuleInstallInvalid, ModuleTableFull at {} rows), all {} exports resolved BY NAME from the provider's own export directory, a lowercase export name and the client's exportless image answered ProcNotFound, an unregistered base ModuleNotLoaded, an ordinal request OrdinalNotSupported and a NULL name NameInvalid -- OK, GetProcAddress answers from a module table the loader built and then certified",
        win32::IMAGE_BASE,
        win32::MAX_MODULES,
        named_resolved
    );

    // No teardown for this demo (see teardown_demo): the space stays owned by
    // `STAGE_SPACE` for the rest of the boot, and this stack is the one the
    // ring-3 entry below returns to.

    serial_println!(
        "  experimental win32-demo: kernel-side proof complete; entering the console client (ring 3), which starts the way a CRT does -- GetCommandLineA and GetModuleHandleA(NULL) -- then resolves kernel32.dll BY NAME and binds GetTickCount through GetProcAddress, and calls kernel32.dll's own stubs for GetStdHandle, CreateFileA/ReadFile/CloseHandle on the read-only C: volume, WriteFile, GetTickCount twice, then ExitProcess({})",
        EXPECTED_EXIT_CODE
    );
    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(app.entry_point, user_rsp)
}

/// Called from `idt::init`'s installed handler on the client's completion trap
/// (`int $0x91`). Reports the Win32 results the client produced, then chains
/// into `experimental::isolation_demo`, keeping the single demo chain linear.
pub unsafe fn on_trap() -> ! {
    let write_ret = slot(SLOT_WRITE_RET);
    let written = slot(SLOT_WRITTEN);
    let tick1 = slot(SLOT_TICK1);
    let tick2 = slot(SLOT_TICK2);
    let file1 = slot(SLOT_FILE1);
    let (r1, n1) = (slot(SLOT_R1), slot(SLOT_N1));
    let (r2, n2) = (slot(SLOT_R2), slot(SLOT_N2));
    let (r3, n3) = (slot(SLOT_R3), slot(SLOT_N3));
    let close1 = slot(SLOT_CLOSE1);
    let file2 = slot(SLOT_FILE2);
    let (r4, n4) = (slot(SLOT_R4), slot(SLOT_N4));
    let close2 = slot(SLOT_CLOSE2);
    let file3 = slot(SLOT_FILE3);
    let (r5, n5) = (slot(SLOT_R5), slot(SLOT_N5));
    let close3 = slot(SLOT_CLOSE3);
    let (close4, close4b) = (slot(SLOT_CLOSE4), slot(SLOT_CLOSE4B));
    let (r6, n6) = (slot(SLOT_R6), slot(SLOT_N6));
    let (file4, file5) = (slot(SLOT_FILE4), slot(SLOT_FILE5));
    let (err_closed_read, err_closed_close) = (slot(SLOT_ERR_CLOSED_READ), slot(SLOT_ERR_CLOSED_CLOSE));
    let (err_missing, err_set) = (slot(SLOT_ERR_MISSING), slot(SLOT_ERR_SET));
    let (file6, err_access, err_kept) =
        (slot(SLOT_FILE6), slot(SLOT_ERR_ACCESS), slot(SLOT_ERR_KEPT));
    let handle_test = slot(SLOT_HANDLE_TEST);
    let (cmdline, cmdline2) = (slot(SLOT_CMDLINE), slot(SLOT_CMDLINE2));
    let (cmdline_ok, module, module_ok) = (
        slot(SLOT_CMDLINE_OK),
        slot(SLOT_MODULE),
        slot(SLOT_MODULE_OK),
    );
    let (named_module, named_module2) = (slot(SLOT_NAMED_MODULE), slot(SLOT_NAMED_MODULE2));
    let (proc_tick, proc_tick_value) = (slot(SLOT_PROC_TICK), slot(SLOT_PROC_TICK_VALUE));
    let proc_tick_same = slot(SLOT_PROC_TICK_SAME);
    let (named_none, named_none_err) = (slot(SLOT_NAMED_NONE), slot(SLOT_NAMED_NONE_ERR));
    let (proc_none, proc_none_err) = (slot(SLOT_PROC_NONE), slot(SLOT_PROC_NONE_ERR));
    let stdout = win32::received();
    let exit = win32::exit_code();
    let readback = slot_bytes(SLOT_BUF, EXPECTED_STDOUT.len());
    let bin = slot_bytes(SLOT_BIN, win32_fs::FILES[1].data.len());

    // The file handles are checked as VALUES, because the volume's slot reuse
    // is part of its contract: the first free slot is handed out, so every
    // successful open in this program must return slot 0's handle.
    let h0 = win32_fs::handle_at(0);
    let console_ok = write_ret == 1
        && written == EXPECTED_STDOUT.len() as u64
        && stdout == EXPECTED_STDOUT
        && readback == EXPECTED_STDOUT;
    let hello_ok = file1 == h0
        && r1 == 1
        && n1 == 5
        && r2 == 1
        && n2 == EXPECTED_STDOUT.len() as u64 - 5
        && r3 == 1
        && n3 == 0
        && close1 == 1;
    let bytes_ok = file2 == h0 && r4 == 1 && n4 == bin.len() as u64 && bin == win32_fs::FILES[1].data;
    let empty_ok = file3 == h0 && r5 == 1 && n5 == 0 && close3 == 1;
    let closed_ok = close4 == 1 && r6 == 0 && n6 == 0 && close4b == 0;
    let miss_ok = file5 == win32::INVALID_HANDLE_VALUE;
    // The error codes the client asked about, from the same typed refusals the
    // kernel raised: the miss (a failure VALUE), the closed handle used twice
    // (real refusals), the refused GENERIC_WRITE open (a real refusal), the
    // arbitrary code it set itself, and the code a SUCCESSFUL call must not
    // disturb. `file6 == INVALID_HANDLE_VALUE` is the refused open's Windows
    // failure VALUE -- the value CreateFileA's own prototype returns -- and
    // `handle_test == 1` is the client's own `== INVALID_HANDLE_VALUE` test
    // passing on it, rather than the client having been handed a 0 it would
    // have read as success.
    let errors_ok = err_missing == win32::ERROR_FILE_NOT_FOUND as u64
        && err_closed_read == win32::ERROR_INVALID_HANDLE as u64
        && err_closed_close == win32::ERROR_INVALID_HANDLE as u64
        && err_set == 0x1234
        && file6 == win32::INVALID_HANDLE_VALUE
        && handle_test == 1
        && err_access == win32::ERROR_ACCESS_DENIED as u64
        && err_kept == err_access;
    // The startup pair, from ring 3: the pointer is the one the loader
    // installed (so both calls agree), the bytes there are the client's own
    // string, and the module handle names the image the client is running
    // FROM -- the last one is why the client compares `_start`, not a constant.
    let startup_ok = cmdline == SLOT_ADDR + CMDLINE_OFFSET
        && cmdline2 == cmdline
        && cmdline_ok == 1
        && module == APP_IMAGE_BASE
        && module_ok == 1;
    // The runtime binding, from ring 3: the named handle is the provider's base
    // in either case, `GetProcAddress` answered the address the loader also
    // bound into the IAT (so two independent paths agree), that address was
    // CALLABLE, and the two "no" answers carried the two distinct codes a
    // client must be able to tell apart. `proc_tick_value >= tick1` is the
    // condition rather than equality because the tick count may advance between
    // the two calls -- what must hold is that the runtime-bound pointer returned
    // a live tick, not that time stood still.
    let bindings_ok = named_module == win32::IMAGE_BASE
        && named_module2 == named_module
        && proc_tick == win32::IMAGE_BASE + expected_rva(b"GetTickCount")
        && proc_tick_same == 1
        && proc_tick_value >= tick1
        && named_none == 0
        && named_none_err == win32::ERROR_MOD_NOT_FOUND as u64
        && proc_none == 0
        && proc_none_err == win32::ERROR_PROC_NOT_FOUND as u64;
    let ok = startup_ok
        && bindings_ok
        && console_ok
        && hello_ok
        && bytes_ok
        && empty_ok
        && closed_ok
        && miss_ok
        && errors_ok
        && tick2 >= tick1
        && exit == Some(EXPECTED_EXIT_CODE)
        && win32_fs::open_count() == 0
        && win32::refusals() == EXPECTED_REFUSALS;
    // State every check and the raw values behind the ones that are not part of
    // the sentence below (each handle, each close result). A MISMATCH verdict
    // then names its own cause in the transcript instead of asking the next
    // reader to re-derive it from a passing-looking line.
    serial_println!(
        "  experimental win32-demo self-test checks: console={} hello={} bytes={} empty={} closed={} miss={} errors={} monotonic={} exit={} handles={} refusals={} handle_test={} startup={} bindings={}; handles f1={:#x} f2={:#x} f3={:#x} f4={:#x} f5={:#x} f6={:#x}; closes {} {} {} {}; reads {}/{}/{}/{}/{}/{}",
        console_ok,
        hello_ok,
        bytes_ok,
        empty_ok,
        closed_ok,
        miss_ok,
        errors_ok,
        tick2 >= tick1,
        exit == Some(EXPECTED_EXIT_CODE),
        win32_fs::open_count() == 0,
        win32::refusals() == EXPECTED_REFUSALS,
        handle_test,
        startup_ok,
        bindings_ok,
        file1,
        file2,
        file3,
        file4,
        file5,
        file6,
        close1,
        close2,
        close3,
        close4,
        r1,
        r2,
        r3,
        r4,
        r5,
        r6
    );
    serial_println!(
        "  experimental win32-demo self-test: GetCommandLineA answered {:#x} on both calls with the client's own byte-and-NUL compare {} it, and GetModuleHandleA(NULL) answered the client's image base {:#x} with the client's _start-inside-image test {} it; GetModuleHandleA(\"kernel32.dll\") answered the provider's own base {:#x} in either case, and GetProcAddress named GetTickCount at {:#x} -- the very address the loader bound into the IAT ({}) -- which the client then CALLED through, reading {} (>= {}); a module that is not loaded answered {:#x} with ERROR_MOD_NOT_FOUND ({}) and a name that module does not export answered {:#x} with ERROR_PROC_NOT_FOUND ({}); WriteFile returned {} and wrote {} byte(s); the console received {:?}; hello.txt opened as handle {:#x}, read {}+{} byte(s) then {} more at EOF, closed -> {} and the client's own copy of the bytes matches; bytes.bin read back byte-identically (not text: 0x00/0x80/0xfe), empty.txt read TRUE with 0 byte(s); a closed handle was refused by ReadFile ({}) and by a second CloseHandle ({}); a missing file returned {:#x} and GetLastError answered ERROR_FILE_NOT_FOUND ({}), the closed handle answered ERROR_INVALID_HANDLE ({} then {}),         GENERIC_WRITE was refused with ERROR_ACCESS_DENIED ({}), returned {:#x} rather than 0 and passed the client's own INVALID_HANDLE_VALUE handle test ({}), SetLastError(0x1234) read back as {:#x}, and a successful GetTickCount left the code at {}; GetTickCount read {} then {} (monotonic); ExitProcess recorded {:?}; {} file handle(s) still open; refusals {} (the program's closed-handle pair, its refused write-access open, its absent-module and absent-export probes) -- {}",
        cmdline,
        cmdline_ok,
        module,
        module_ok,
        named_module,
        proc_tick,
        proc_tick_same,
        proc_tick_value,
        tick1,
        named_none,
        named_none_err,
        proc_none,
        proc_none_err,
        write_ret,
        written,
        NameOf(stdout),
        file1,
        n1,
        n2,
        n3,
        close1,
        r6,
        close4b,
        file5,
        err_missing,
        err_closed_read,
        err_closed_close,
        err_access,
        file6,
        handle_test,
        err_set,
        err_kept,
        tick1,
        tick2,
        exit,
        win32_fs::open_count(),
        win32::refusals(),
        if ok {
            "OK, a real Windows-format console program started the way a CRT does -- its command line and its own image base, both read from loader state -- then resolved kernel32.dll by name, bound GetTickCount through GetProcAddress out of that module's own export directory and called twelve kernel32 exports through a real PE32+ DLL's stubs, which the kernel performed in ring 0 against a read-only in-memory volume, explaining every failure through GetLastError -- a real Win32 API surface, not Windows compatibility (no NT syscall layer, no directories or writes, ExitProcess records and returns)"
        } else {
            "MISMATCH"
        }
    );

    let fa = (*&raw mut FRAME_ALLOCATOR).take().unwrap();
    isolation_demo::start(fa)
}

/// Writes `bytes` (plus a NUL) at `offset` in the client's report page and
/// returns its address, so a negative control can hand the surface a path a
/// real client could have produced.
///
/// # Safety
/// The page is mapped and the offsets are inside it.
unsafe fn write_slot_string(offset: u64, bytes: &[u8]) -> u64 {
    let dst = (SLOT_ADDR + offset) as *mut u8;
    core::ptr::copy_nonoverlapping(bytes.as_ptr(), dst, bytes.len());
    core::ptr::write_volatile(dst.add(bytes.len()), 0);
    SLOT_ADDR + offset
}

/// Asserts one kernel32 request is refused with exactly `want`, so a refusal
/// that fires for the WRONG reason fails the boot too -- and asserts that the
/// refusal recorded the Win32 code `want` maps to, so `GetLastError` cannot
/// answer with a number that does not belong to the condition.
///
/// # Safety
/// Same contract as [`win32::dispatch`].
unsafe fn expect_refusal(index: u64, args: [u64; 5], rip: u64, want: win32::Win32Error) {
    assert_eq!(
        win32::dispatch(index, args, rip),
        Err(want),
        "expected a {:?} refusal",
        want
    );
    assert_eq!(
        win32::last_error(),
        want.code(),
        "a {:?} refusal must leave GetLastError answering {}, not {}",
        want,
        want.code(),
        win32::last_error()
    );
}

/// A newtype so a byte slice prints as a quoted Windows-text payload without
/// allocating (this kernel has no `alloc`).
struct NameOf<'a>(&'a [u8]);

impl core::fmt::Debug for NameOf<'_> {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        use core::fmt::Write as _;
        f.write_char('"')?;
        for &b in self.0 {
            // Printable ASCII is shown as itself; anything else is escaped, so
            // a transcript never renders a payload it does not have.
            if b.is_ascii_graphic() || b == b' ' {
                f.write_char(char::from(b))?;
            } else {
                write!(f, "\\x{:02x}", b)?;
            }
        }
        f.write_char('"')
    }
}
