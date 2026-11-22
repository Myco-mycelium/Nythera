//! The Win32 API surface proof: a genuine Windows-format PE32+ console
//! program, linked against a genuine Windows-format PE32+ `kernel32.dll`,
//! whose seven exports -- `GetStdHandle`, `WriteFile`, `GetTickCount`,
//! `ExitProcess`, `CreateFileA`, `ReadFile`, `CloseHandle` -- are performed by
//! this kernel.
//!
//! What is proven, end to end, every boot:
//!
//! 1. `userprog/kernel32.dll` loads at its own `ImageBase`, and its REAL
//!    export directory (`GetProcAddress` semantics) names exactly the seven
//!    functions; every export address is inside its own `.text` window, and
//!    the call block the stubs use is where the kernel pins it (magic
//!    checked) -- the layout-drift rule.
//! 2. `userprog/win32_console.exe` is loaded, and its REAL
//!    `IMAGE_DIRECTORY_ENTRY_IMPORT` -- synthesized by GNU ld itself from the
//!    DLL -- resolves to those seven export addresses. Every IAT slot is read
//!    back against the export table's own answer, per symbol.
//! 3. The client's own code runs in ring 3 and uses the imports the ordinary
//!    Windows way. Console: `GetStdHandle` then `WriteFile`. Files: it opens
//!    `C:\nyrqis\hello.txt` with `CreateFileA`, reads it in two pieces (which
//!    proves the handle's cursor advances), reads past the end (TRUE, zero
//!    bytes), writes the bytes it read to the console, and closes the handle;
//!    then it opens a file that is deliberately NOT text (`0x00`, `0x80`,
//!    `0xfe`), a zero-length file, and a file it then uses after closing it
//!    (which must fail); and it asks for a file that does not exist (which
//!    must return `INVALID_HANDLE_VALUE`, not an error).
//! 4. The surface REFUSES what ring 3 cannot justify -- an unknown function
//!    index, a trap from outside kernel32's `.text`, a handle never issued, a
//!    buffer or out-parameter outside the client's mapped regions, a length
//!    past the console's cap, a relative path, an unmounted drive, a
//!    non-ASCII path, a path with no terminator inside the client's region, a
//!    write access request, a creation disposition other than OPEN_EXISTING,
//!    a non-NULL overlapped pointer, using the console handle as a file, and
//!    exhausting the volume's handle table -- each typed, each exercised
//!    kernel-side before ring 3 runs (negative controls), and the client's own
//!    run must raise none.
//!
//! Deliberate scope, stated: seven functions, no NT system-call layer beneath
//! them, no user32/gdi32, no Microsoft code, and `ExitProcess` records its code
//! and returns (v0 has no task teardown to switch to) -- the client's next
//! instruction is this link's completion trap, whose handler asserts the
//! recorded code. The volume is three read-only files in the kernel image, not
//! a file system: no directories, no writes, no current directory, no dates.
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

/// How many refusals the surface must have counted during the client's run.
///
/// Not zero any more, and deliberately so: a real program that uses a handle it
/// has already closed is making a mistake, and this surface answers it with a
/// typed refusal instead of a silent zero. The client does exactly that twice
/// on purpose -- `ReadFile` and a second `CloseHandle` on the closed handle --
/// so the surface must have refused exactly those two. A different number means
/// either an extra refusal (a bug in the surface) or a missing one (the client
/// never reached the probe), and the per-field assertions below pin which.
const EXPECTED_REFUSALS: u64 = 2;

/// The seven exports the client imports, and the RVAs they must resolve to (the
/// DLL's own export table is the authority; these are the independent answer
/// the boot checks it against, per symbol). Sorted by name, which is the order
/// GNU ld writes its name table in.
const EXPECTED_EXPORTS: [(&[u8], u64); 7] = [
    (b"CloseHandle", 0x10F1),
    (b"CreateFileA", 0x1077),
    (b"ExitProcess", 0x1062),
    (b"GetStdHandle", 0x1000),
    (b"GetTickCount", 0x104D),
    (b"ReadFile", 0x10B4),
    (b"WriteFile", 0x101C),
];

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
const SLOT_BUF: u64 = 0x100;
const SLOT_BIN: u64 = 0x200;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;

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
        "exactly the seven kernel32 imports must resolve"
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
        "  experimental win32-demo: resolved {} import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (GetStdHandle, WriteFile, GetTickCount, ExitProcess, CreateFileA, ReadFile, CloseHandle), every slot reading back its export address -- OK",
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
    expect_refusal(9, [0; 5], from_text, win32::Win32Error::UnknownFunction);
    serial_println!(
        "  experimental win32-demo: negative control 3 -- index 9 refused with the typed UnknownFunction -- OK"
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

    core::mem::forget(space); // no teardown for this demo -- see teardown_demo

    serial_println!(
        "  experimental win32-demo: kernel-side proof complete; entering the console client (ring 3), which calls kernel32.dll's own stubs for GetStdHandle, CreateFileA/ReadFile/CloseHandle on the read-only C: volume, WriteFile, GetTickCount twice, then ExitProcess({})",
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
    let file5 = slot(SLOT_FILE5);
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
    let ok = console_ok
        && hello_ok
        && bytes_ok
        && empty_ok
        && closed_ok
        && miss_ok
        && tick2 >= tick1
        && exit == Some(EXPECTED_EXIT_CODE)
        && win32_fs::open_count() == 0
        && win32::refusals() == EXPECTED_REFUSALS;
    serial_println!(
        "  experimental win32-demo self-test: WriteFile returned {} and wrote {} byte(s); the console received {:?}; hello.txt opened as handle {:#x}, read {}+{} byte(s) then {} more at EOF, closed -> {} and the client's own copy of the bytes matches; bytes.bin read back byte-identically (not text: 0x00/0x80/0xfe), empty.txt read TRUE with 0 byte(s); a closed handle was refused by ReadFile ({}) and by a second CloseHandle ({}); a missing file returned {:#x}; GetTickCount read {} then {} (monotonic); ExitProcess recorded {:?}; {} file handle(s) still open; refusals {} (the program's two deliberate uses of a closed handle) -- {}",
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
        tick1,
        tick2,
        exit,
        win32_fs::open_count(),
        win32::refusals(),
        if ok {
            "OK, a real Windows-format console program called seven kernel32 exports through a real PE32+ DLL's export table and the kernel performed them in ring 0 against a read-only in-memory volume -- a real Win32 API surface, not Windows compatibility (no NT syscall layer, no directories or writes, ExitProcess records and returns)"
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
/// that fires for the WRONG reason fails the boot too.
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
