//! The Win32 API surface proof: a genuine Windows-format PE32+ console
//! program, linked against a genuine Windows-format PE32+ `kernel32.dll`,
//! whose four exports -- `GetStdHandle`, `WriteFile`, `GetTickCount`,
//! `ExitProcess` -- are performed by this kernel.
//!
//! What is proven, end to end, every boot:
//!
//! 1. `userprog/kernel32.dll` loads at its own `ImageBase`, and its REAL
//!    export directory (`GetProcAddress` semantics) names exactly the four
//!    functions; every export address is inside its own `.text` window, and
//!    the call block the stubs use is where the kernel pins it (magic
//!    checked) -- the layout-drift rule.
//! 2. `userprog/win32_console.exe` is loaded, and its REAL
//!    `IMAGE_DIRECTORY_ENTRY_IMPORT` -- synthesized by GNU ld itself from the
//!    DLL -- resolves to those four export addresses. Every IAT slot is read
//!    back against the export table's own answer, per symbol.
//! 3. The client's own code runs in ring 3 and uses the imports the ordinary
//!    Windows way: `call *__imp_GetStdHandle(%rip)`, then
//!    `__imp_WriteFile`, `__imp_GetTickCount` (twice), `__imp_ExitProcess`.
//!    Those calls land in the DLL's stub bodies, which trap `int $0x90`; the
//!    kernel performs each operation and returns a Windows-typed result. The
//!    first two SLOTS prove it: `WriteFile` returned TRUE and wrote the byte
//!    count through its own out-parameter, and both tick readings are
//!    monotonic.
//! 4. The surface REFUSES what ring 3 cannot justify -- an unknown function
//!    index, a trap from outside kernel32's `.text`, a handle GetStdHandle
//!    never issued, a buffer or out-parameter outside the client's mapped
//!    regions, and a length past its cap -- each typed, each exercised
//!    kernel-side before ring 3 runs (negative controls), and the client's
//!    own run must raise none.
//!
//! Deliberate scope, stated: four functions, no NT system-call layer beneath
//! them, no user32/gdi32, no Microsoft code, and `ExitProcess` records its
//! code and returns (v0 has no task teardown to switch to) -- the client's
//! next instruction is this link's completion trap, whose handler asserts the
//! recorded code. That is the difference between a real Win32 API surface and
//! Windows compatibility, and this file only claims the former.
//!
//! The chain: `pe_resource_demo` chains here instead of straight into
//! `isolation_demo`, and this link chains into `isolation_demo` in turn.

use crate::serial_println;
use x86_64::structures::paging::PageTableFlags;
use x86_64::VirtAddr;

use crate::experimental::address_space::AddressSpace;
use crate::experimental::{isolation_demo, pe, usermode, win32};
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

/// Exactly what the client's `WriteFile` hands the console, CRLF included --
/// the byte count `WriteFile` reports is this length.
const EXPECTED_STDOUT: &[u8] = b"nyrqis: hello from a Win32 console program\r\n";
/// The code the client passes to `ExitProcess`.
const EXPECTED_EXIT_CODE: u64 = 7;
/// The four exports the client imports, and the RVAs they must resolve to (the
/// DLL's own export table is the authority; these are the independent answer
/// the boot checks it against, per symbol).
const EXPECTED_EXPORTS: [(&[u8], u64); 4] = [
    (b"GetStdHandle", 0x1000),
    (b"WriteFile", 0x101C),
    (b"GetTickCount", 0x104D),
    (b"ExitProcess", 0x1062),
];

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

/// Builds an address space, loads `kernel32.dll` and the console client, runs
/// the import and surface negative controls, resolves the client's imports for
/// real, and enters ring 3 at the client's entry point. [`on_trap`] (vector
/// 0x91) reports the results and chains into `experimental::isolation_demo`.
///
/// # Safety
/// Must be called at most once, with Phases 1-4-prep already initialized.
pub unsafe fn start(frame_allocator: BumpFrameAllocator) -> ! {
    FRAME_ALLOCATOR = Some(frame_allocator);
    let fa = (*&raw mut FRAME_ALLOCATOR).as_mut().unwrap();

    let mut space = AddressSpace::new_sharing_kernel(fa)
        .unwrap_or_else(|_| panic!("failed to build win32-demo address space"));
    space.activate();
    let mut mapper = space.mapper();

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
        4,
        "kernel32.dll must export exactly the four functions this surface implements"
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
        "exactly the four kernel32 imports must resolve"
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
        "  experimental win32-demo: resolved {} import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (GetStdHandle, WriteFile, GetTickCount, ExitProcess), every slot reading back its export address -- OK",
        resolved.facts.len()
    );

    // --- the surface's own negative controls, kernel-side, before ring 3 ---
    win32::install(APP_IMAGE_BASE, APP_IMAGE_SIZE, SLOT_ADDR, 4096);
    let from_text = win32::IMAGE_BASE + win32::TEXT_RVA + 4;
    expect_refusal(9, [0; 4], from_text, win32::Win32Error::UnknownFunction);
    serial_println!(
        "  experimental win32-demo: negative control 3 -- index 9 refused with the typed UnknownFunction -- OK"
    );
    expect_refusal(
        0,
        [(-11i64) as u64, 0, 0, 0],
        win32::IMAGE_BASE + 8,
        win32::Win32Error::CallerOutsideKernel32,
    );
    serial_println!(
        "  experimental win32-demo: negative control 4 -- a trap at kernel32+0x8 (outside .text) refused with the typed CallerOutsideKernel32 -- OK, the index is a claim and the trapping RIP is the evidence"
    );
    expect_refusal(
        1,
        [0x99, APP_IMAGE_BASE, 8, 0],
        from_text,
        win32::Win32Error::InvalidHandle,
    );
    expect_refusal(
        1,
        [win32::STDOUT_HANDLE, 0x10, 8, 0],
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
        ],
        from_text,
        win32::Win32Error::LengthTooLarge,
    );
    expect_refusal(
        1,
        [win32::STDOUT_HANDLE, APP_IMAGE_BASE, 8, 0x10],
        from_text,
        win32::Win32Error::OutPointerOutsideClient,
    );
    serial_println!(
        "  experimental win32-demo: negative control 5 -- WriteFile refused an unissued handle (InvalidHandle), an out-of-image buffer (BufferOutsideClient), a {} byte length (LengthTooLarge) and an out-of-image out-parameter (OutPointerOutsideClient) -- OK, every argument is checked before the kernel touches it",
        win32::MAX_WRITE + 1
    );

    // Private report page + the client's stack.
    let flags = PageTableFlags::PRESENT
        | PageTableFlags::WRITABLE
        | PageTableFlags::USER_ACCESSIBLE
        | PageTableFlags::NO_EXECUTE;
    for base in [SLOT_ADDR, USER_STACK_ADDR] {
        let mapped = paging::map_range(&mut mapper, fa, VirtAddr::new(base), 1, flags);
        assert_eq!(mapped, 1, "failed to map a win32-demo page at {:#x}", base);
    }

    // A frame allocator hands back whatever it holds, so the report page is
    // zeroed explicitly: if a call is refused, the report reads as 0 instead
    // of as the previous demo's bytes.
    for i in 0..8u64 {
        core::ptr::write_volatile((SLOT_ADDR + i * 8) as *mut u64, 0);
    }

    core::mem::forget(space); // no teardown for this demo -- see teardown_demo

    serial_println!(
        "  experimental win32-demo: kernel-side proof complete; entering the console client (ring 3), which calls kernel32.dll's own stubs for GetStdHandle, WriteFile, GetTickCount twice, then ExitProcess({})",
        EXPECTED_EXIT_CODE
    );
    let user_rsp = VirtAddr::new(USER_STACK_ADDR + 4096 - 16);
    usermode::enter_with_stack(app.entry_point, user_rsp)
}

/// Called from `idt::init`'s installed handler on the client's completion trap
/// (`int $0x91`). Reports the Win32 results the client produced, then chains
/// into `experimental::isolation_demo`, keeping the single demo chain linear.
pub unsafe fn on_trap() -> ! {
    let write_ret = core::ptr::read_volatile(SLOT_ADDR as *const u64);
    let written = core::ptr::read_volatile((SLOT_ADDR + 8) as *const u64);
    let tick1 = core::ptr::read_volatile((SLOT_ADDR + 16) as *const u64);
    let tick2 = core::ptr::read_volatile((SLOT_ADDR + 24) as *const u64);
    let stdout = win32::received();
    let exit = win32::exit_code();
    let ok = write_ret == 1
        && written == EXPECTED_STDOUT.len() as u64
        && tick2 >= tick1
        && stdout == EXPECTED_STDOUT
        && exit == Some(EXPECTED_EXIT_CODE)
        && win32::refusals() == 0;
    serial_println!(
        "  experimental win32-demo self-test: WriteFile returned {} and wrote {} byte(s) through the client's own out-parameter; the console received {:?}; GetTickCount read {} then {} (monotonic); ExitProcess recorded {:?}; refusals {} -- {}",
        write_ret,
        written,
        NameOf(stdout),
        tick1,
        tick2,
        exit,
        win32::refusals(),
        if ok {
            "OK, a real Windows-format console program called four kernel32 exports through a real PE32+ DLL's export table and the kernel performed them in ring 0 -- a real Win32 API surface, not Windows compatibility (no NT syscall layer, ExitProcess records and returns)"
        } else {
            "MISMATCH"
        }
    );

    let fa = (*&raw mut FRAME_ALLOCATOR).take().unwrap();
    isolation_demo::start(fa)
}

/// Asserts one kernel32 request is refused with exactly `want`, so a refusal
/// that fires for the WRONG reason fails the boot too.
///
/// # Safety
/// Same contract as [`win32::dispatch`].
unsafe fn expect_refusal(index: u64, args: [u64; 4], rip: u64, want: win32::Win32Error) {
    assert_eq!(
        win32::dispatch(index, args, rip),
        Err(want),
        "expected a {:?} refusal",
        want
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
