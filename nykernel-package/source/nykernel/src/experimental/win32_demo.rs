//! The Win32 API surface proof: a genuine Windows-format PE32+ console
//! program, linked against a genuine Windows-format PE32+ `kernel32.dll`,
//! whose fourteen exports -- `GetStdHandle`, `WriteFile`, `GetTickCount`,
//! `ExitProcess`, `CreateFileA`, `ReadFile`, `CloseHandle`, `GetLastError`,
//! `SetLastError`, `GetCommandLineA`, `GetModuleHandleA`, `GetProcAddress`,
//! `LoadLibraryA` and `FreeLibrary` -- are performed by this kernel.
//!
//! What is proven, end to end, every boot:
//!
//! 1. `userprog/kernel32.dll` loads at its own `ImageBase`, and its REAL
//!    export directory (`GetProcAddress` semantics) names exactly the fourteen
//!    functions; every export address is inside its own `.text` window, and
//!    the call block the stubs use is where the kernel pins it (magic
//!    checked) -- the layout-drift rule.
//! 2. `userprog/win32_console.exe` is loaded, and its REAL
//!    `IMAGE_DIRECTORY_ENTRY_IMPORT` -- synthesized by GNU ld itself from the
//!    DLL -- resolves to those fourteen export addresses. Every IAT slot is read
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
//!    equal. A SECOND provider -- `userprog/nyrqis.dll`, a real PE32+ DLL this
//!    program does not import from at all -- registers as its own row: the
//!    client resolves its name to a different base, binds both of its exports
//!    (`NyrqisBuild`, `NyrqisMode`) by name OUT of that row and CALLS them in
//!    ring 3, and then asks each provider for the other's export name, which
//!    must answer `ERROR_PROC_NOT_FOUND` on both sides -- so the table's rows
//!    are per-module, not one list consulted for any handle. It also asks for a
//!    module that is not loaded and for an export
//!    that module does not have, and reads back `ERROR_MOD_NOT_FOUND` /
//!    `ERROR_PROC_NOT_FOUND` respectively. Runtime loading: the client asks
//!    `nyrqis.dll` for its `NyrqisDyn` forwarder, whose target
//!    (`nyrqis_dyn.dll.NyrqisDynValue`) is a FILE on the volume nothing has
//!    loaded -- so it fails with `ERROR_PROC_NOT_FOUND`; then it calls
//!    `LoadLibraryA("C:\\nyrqis\\nyrqis_dyn.dll")`, the kernel maps that file
//!    and adds a row, the module's own name answers its base, the SAME
//!    forwarder now answers the dynamic image's own export, and the client
//!    CALLS through it (42, from code it never linked against). `FreeLibrary`
//!    drops the row, after which the name is `ERROR_MOD_NOT_FOUND` and the
//!    forwarder is `ERROR_PROC_NOT_FOUND` again, and a DLL the volume does not
//!    have answers NULL with `ERROR_MOD_NOT_FOUND`. So the module table grows
//!    and shrinks WHILE the program runs, and one forwarder has three
//!    different answers according to what is loaded. Console:
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
//!    an export name in the wrong case, an ordinal either below the provider's
//!    declared `OrdinalBase` or past its last EAT slot, a `LoadLibraryA` of a
//!    relative path or of a present file that is not a loadable image,
//!    `FreeLibrary` of a base that is not a live row, and
//!    registering a module row that is unusable (empty name, non-text name,
//!    zero base, zero image, duplicate name, an export outside the image) or a
//!    row with no room -- each typed, each
//!    exercised kernel-side before ring 3 runs (negative controls). Each control also
//!    asserts the Win32 code that refusal reports, so the `GetLastError` table
//!    is proved for every variant and not only for the three the client hits;
//!    the client's own run must raise exactly the refusals it asks for.
//!
//! Deliberate scope, stated: fourteen functions, no NT system-call layer beneath
//! them, no user32/gdi32, no Microsoft code, and `ExitProcess` records its code
//! and returns (v0 has no task teardown to switch to) -- the client's next
//! instruction is this link's completion trap, whose handler asserts the
//! recorded code. `FreeLibrary` drops the module ROW but does not unmap the
//! image, because this kernel's PE loader has no unmap yet; a later
//! `LoadLibraryA` of the same file re-registers the pages it already owns. The
//! volume is four read-only files in the kernel image (the fourth is the
//! runtime DLL itself), not a file system: no directories, no writes, no
//! current directory, no dates.
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
/// The second provider. It is loaded and registered exactly like `kernel32.dll`,
/// but this program has no import descriptor for it -- which is the point: the
/// only route to its exports is `GetProcAddress`.
const NYRQIS_DLL: &[u8] = include_bytes!("../../userprog/nyrqis.dll");
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
/// does not have. This tranche adds two more of the same shape: `kernel32.dll`
/// asked for `NyrqisBuild` (which it does not export) and `nyrqis.dll` asked for
/// `GetTickCount` (which IT does not export), so the per-module claim is made
/// from ring 3 in both directions. This tranche adds two more: the client asks
/// the second provider for ordinal 9 (below its declared `OrdinalBase` of 10)
/// and ordinal 15 (past its five EAT slots, three of them forwarders), and both
/// requests name no export, so both are `ProcNotFound`. This tranche adds one
/// for the forwarded-export proof: `NyrqisGhost` is a real forwarder whose
/// target export `kernel32.dll` does not have, so asking for it must end in
/// `ProcNotFound` rather than an address. This tranche adds two more, and they
/// are the pair that brackets the runtime load: the client asks the SAME
/// forwarder `NyrqisDyn` once before `LoadLibraryA` (no `nyrqis_dyn.dll` row,
/// so `ProcNotFound`) and once after `FreeLibrary` has dropped that row (the
/// same answer for the same reason). The surface must therefore have refused
/// exactly those twelve. A
/// different number means either an extra refusal (a bug in the surface) or a
/// missing one (the client never reached the probe), and the per-field
/// assertions below pin which.
const EXPECTED_REFUSALS: u64 = 12;

/// The fourteen exports the client imports, and the RVAs they must resolve to
/// (the DLL's own export table is the authority; these are the independent
/// answer the boot checks it against, per symbol). Sorted by name, which is the
/// order GNU ld writes its name table in -- so the stubs appended last in the
/// source for a stable `.text` layout sort into the middle.
const EXPECTED_EXPORTS: [(&[u8], u64); 14] = [
    (b"CloseHandle", 0x10F1),
    (b"CreateFileA", 0x1077),
    (b"ExitProcess", 0x1062),
    (b"FreeLibrary", 0x11A7),
    (b"GetCommandLineA", 0x1137),
    (b"GetLastError", 0x110D),
    (b"GetModuleHandleA", 0x114C),
    (b"GetProcAddress", 0x1168),
    (b"GetStdHandle", 0x1000),
    (b"GetTickCount", 0x104D),
    (b"LoadLibraryA", 0x118B),
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

/// The exports the SECOND provider must declare, and the RVAs they must name
/// (measured from `userprog/nyrqis.dll` with objdump; `build.sh` pins its
/// `.text` window `0x1000..0x1030`, and the boot checks the parsed EAT against
/// these -- two independent answers to the same question).
const EXPECTED_NYRQIS_EXPORTS: [(&[u8], u64); 2] =
    [(b"NyrqisBuild", 0x1000), (b"NyrqisMode", 0x1006)];

/// The second provider's two FORWARDED exports: the export name, the module
/// its forwarder string names, and the export it names there. They are not in
/// [`EXPECTED_NYRQIS_EXPORTS`] because that table pins RVAs inside
/// `nyrqis.dll`, while a forwarder's answer is another module's code -- which
/// is exactly the distinction this tranche's proof turns on. `NyrqisTick` must
/// resolve to `kernel32.dll`'s own `GetTickCount`; `NyrqisGhost` names an
/// export that module does not have and must resolve to nothing.
const EXPECTED_NYRQIS_FORWARDERS: [(&[u8], &[u8], &[u8]); 3] = [
    (b"NyrqisTick", b"kernel32.dll", b"GetTickCount"),
    (b"NyrqisGhost", b"kernel32.dll", b"NoSuchExport"),
    (b"NyrqisDyn", b"nyrqis_dyn.dll", b"NyrqisDynValue"),
];

/// The volume path and module name of the RUNTIME provider -- a file the
/// kernel never embeds, so `LoadLibraryA` is the only route to a row for it.
/// The image base is [`win32::DYNAMIC_IMAGE_BASE`]: one constant, read by the
/// surface, by the loader hook and by the assertions below, because two
/// hand-typed copies of a load address is exactly the drift the layout pins
/// exist to prevent.
const DYNAMIC_PATH: &[u8] = b"C:\\nyrqis\\nyrqis_dyn.dll";
const DYNAMIC_NAME: &[u8] = b"nyrqis_dyn.dll";
/// The export name that `nyrqis.dll`'s `NyrqisDyn` forwarder names, and the
/// RVA the runtime image's own export table must give it (`build.sh` pins the
/// same window out of the linked file).
const DYNAMIC_EXPORT: &[u8] = b"NyrqisDynValue";
const DYNAMIC_EXPORT_RVA: u64 = 0x1000;
/// The forwarder's name in `nyrqis.dll`; asked before the load, while loaded
/// and after the free, so one name has three different answers.
const DYNAMIC_FORWARDER: &[u8] = b"NyrqisDyn";
/// What calling the runtime module's own code returns.
const DYNAMIC_VALUE: u64 = 42;

/// The RVA of one of the second provider's exports, from the same pin the
/// kernel-side control used, so the ring-3 expectation is not a second
/// hand-typed address that could drift from the first.
fn expected_nyrqis_rva(name: &[u8]) -> u64 {
    EXPECTED_NYRQIS_EXPORTS
        .iter()
        .find(|(pinned, _)| *pinned == name)
        .map(|(_, rva)| *rva)
        .unwrap_or_else(|| panic!("{:?} is not one of the pinned nyrqis exports", NameOf(name)))
}

/// The `OrdinalBase` the second provider's `userprog/nyrqis.def` declares, and
/// therefore the number its first two exports are ordinals OF. Deliberately not
/// 1: with the linker default, Windows' ordinal form and a bare EAT index would
/// be the same answer for every export, so the ordinal proof could not tell a
/// correct lookup from `entry_raw(ordinal - 1)`. `build.sh` pins the same value
/// out of the linked DLL's own export directory, so a DEF file this ld stopped
/// honoring fails the build rather than quietly making the boot's proof weaker.
const EXPECTED_NYRQIS_ORDINAL_BASE: u32 = 10;

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
// The second provider's answers. These sit ABOVE 0x300 (where the kernel-side
// controls write the paths and names they hand the surface) and above the
// kernel's own export-name strings at 0x500..0x6e0, so a client field cannot
// land on a string a kernel control is still using.
/// The base `GetModuleHandleA("nyrqis.dll")` returned; must differ from
/// kernel32's.
const SLOT_NAMED_SECOND: u64 = 0x700;
/// The addresses `GetProcAddress(hnyr, "NyrqisBuild"/"NyrqisMode")` returned --
/// exports this program has no IAT slot for, so the runtime answer is the only
/// route to them.
const SLOT_SECOND_BUILD: u64 = 0x708;
const SLOT_SECOND_MODE: u64 = 0x710;
/// The values of calling THROUGH those pointers: 6 and 1, from the second
/// provider's own code, executed in ring 3.
const SLOT_SECOND_BUILD_VALUE: u64 = 0x718;
const SLOT_SECOND_MODE_VALUE: u64 = 0x720;
/// `GetProcAddress(hmod32, "NyrqisBuild")` -> 0, `ERROR_PROC_NOT_FOUND`: the
/// name is real, just not in THAT row.
const SLOT_CROSS_K32_NONE: u64 = 0x728;
const SLOT_CROSS_K32_ERR: u64 = 0x730;
/// `GetProcAddress(hnyr, "GetTickCount")` -> 0, the same code: the other
/// direction of the same asymmetry.
const SLOT_CROSS_SEC_NONE: u64 = 0x738;
const SLOT_CROSS_SEC_ERR: u64 = 0x740;
/// The ordinal form's answers. `nyrqis.dll` declares `OrdinalBase 10`, so its
/// two exports are ordinals 10 and 11 -- not 1 and 2, which is what an EAT
/// index would be. The client asks for 11, gets the address the NAME
/// `NyrqisMode` answered, and calls through it.
const SLOT_ORD_MODE: u64 = 0x748;
/// 1 when the ordinal answer equaled the named answer.
const SLOT_ORD_SAME: u64 = 0x750;
/// The value of calling through the ordinal answer: 1.
const SLOT_ORD_MODE_VALUE: u64 = 0x758;
/// Ordinal 9 (below the base) and ordinal 12 (past the last slot): both name
/// nothing, so both answer 0 with `ERROR_PROC_NOT_FOUND`.
const SLOT_ORD_LOW_NONE: u64 = 0x760;
const SLOT_ORD_LOW_ERR: u64 = 0x768;
const SLOT_ORD_HIGH_NONE: u64 = 0x770;
const SLOT_ORD_HIGH_ERR: u64 = 0x778;
/// The forwarded-export answers: the address `GetProcAddress(hnyr,
/// "NyrqisTick")` returned -- a forwarder to `kernel32.dll.GetTickCount`, so
/// the answer must be THAT module's code -- whether it equaled the address the
/// target module's own row answered, the value of CALLING through it, and the
/// two halves of the ghost probe (a forwarder to an export `kernel32.dll` does
/// not have).
const SLOT_FWD_TICK: u64 = 0x780;
const SLOT_FWD_SAME: u64 = 0x788;
const SLOT_FWD_TICK_VALUE: u64 = 0x790;
const SLOT_FWD_GHOST_NONE: u64 = 0x798;
const SLOT_FWD_GHOST_ERR: u64 = 0x7A0;
/// Tranche 9's answers, the runtime-loaded module's whole lifecycle. The
/// forwarder is asked BEFORE the load (no row to land in), the load answers its
/// base, the name resolves, the forwarder lands on the dynamic row's own export
/// and the client CALLS it, `FreeLibrary` drops the row, and the same forwarder
/// fails again. The last pair is `LoadLibraryA` of a file the volume does not
/// have -- Windows' NULL plus `ERROR_MOD_NOT_FOUND`, not a refusal.
const SLOT_DYN_BEFORE_NONE: u64 = 0x7A8;
const SLOT_DYN_BEFORE_ERR: u64 = 0x7B0;
const SLOT_DYN_BASE: u64 = 0x7B8;
const SLOT_DYN_HANDLE: u64 = 0x7C0;
const SLOT_DYN_PROC: u64 = 0x7C8;
const SLOT_DYN_PROC_VALUE: u64 = 0x7D0;
const SLOT_DYN_OWN: u64 = 0x7D8;
const SLOT_DYN_FREED: u64 = 0x7E0;
const SLOT_DYN_AFTER_NONE: u64 = 0x7E8;
const SLOT_DYN_AFTER_ERR: u64 = 0x7F0;
const SLOT_DYN_MISS_NONE: u64 = 0x7F8;
const SLOT_DYN_MISS_ERR: u64 = 0x800;
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
        && SLOT_PROC_NONE_ERR + 8 <= 0x300
        && SLOT_NAMED_SECOND >= 0x700
        && SLOT_CROSS_SEC_ERR + 8 <= SLOT_ORD_MODE
        && SLOT_ORD_HIGH_ERR + 8 <= SLOT_FWD_TICK
        && SLOT_FWD_GHOST_ERR + 8 <= SLOT_DYN_BEFORE_NONE
        && SLOT_DYN_MISS_ERR + 8 <= 0x820,
    "win32-demo report fields must sit between the read buffers and 0x300 (or above 0x700, past the kernel's control strings), must not overlap each other, and must not overlap the installed command line"
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
/// work runs on ITS OWN guarded RSP0 stack, and the budget is stated here.
///
/// # Why the budget is twelve pages and not eight
///
/// Eight pages was what this stage needed through v0.8. The runtime-load
/// tranche made the client's `.idata` two descriptors larger (fourteen imports
/// instead of twelve) and added this stage's own dynamic-load lifecycle block,
/// and the first boot of that link ran the stack out at the REAL IMPORT
/// RESOLUTION -- not in the new code, but one frame later: the guard page below
/// the region took a write, `#PF`, and the fault handler's own frame turned it
/// into the printed `#DF`. That is the guard page doing its job; the evidence
/// is QEMU's exception log, whose first fault is a supervisor WRITE to the page
/// just below the region. Twelve pages is eight plus the four this link
/// measurably crossed, stated rather than grown one page at a time so the next
/// reader knows how much headroom remains. This is a per-stage budget, not a
/// shared one: no other stage's stack changes because of it.
const WIN32_STACK_PAGES: u64 = 12;

static mut FRAME_ALLOCATOR: Option<BumpFrameAllocator> = None;
/// This demo's address space, stashed for the stage-two continuation (the
/// chain-partition rule: no caller frame survives the stack switch).
static mut STAGE_SPACE: Option<AddressSpace> = None;
/// Where the runtime-loaded image was mapped, once: `(base, image_size)`.
///
/// # Why this exists at all, when the loader could just map again
///
/// Because this kernel's PE loader has no unmap. `FreeLibrary` removes the
/// module ROW at reference count zero, but the image's pages stay mapped for
/// the life of the process, so a later `LoadLibraryA` of the same file would
/// fail on an already-present page if it tried to map it again. Recording the
/// mapping means the second load RE-REGISTERS the image at the base it already
/// occupies -- the row is what comes and goes, and `win32`'s docs say so
/// rather than implying a Windows unload happened. `None` means nothing has
/// been mapped yet, which is also how the hook knows the first load is the
/// real one.
static mut DYNAMIC_MAPPED: Option<(u64, u64)> = None;

/// `LoadLibraryA`'s loader half, installed into the surface by [`start`]'s
/// stage. It maps the volume's bytes at the image's own `ImageBase` (the PE
/// loader refuses to rebase), parses the export table out of the MAPPED bytes
/// -- the same discipline every other row here follows -- and reports the base
/// it used. Every failure is [`win32::Win32Error::ImageLoadFailed`]: the
/// surface already knows the file was found, so what is left to say is that its
/// bytes are not an image this loader can map.
///
/// # Safety
/// Runs inside the `int $0x90` handler while ring 3 is suspended: the address
/// space and the frame allocator are this demo's own statics, and the client's
/// space is the active one. `bytes` is a static volume entry, never client
/// memory.
unsafe fn load_dynamic_module(bytes: &[u8]) -> Result<win32::LoadedModule, win32::Win32Error> {
    if let Some((base, size)) = *(&raw const DYNAMIC_MAPPED) {
        let exports = pe::DllExportTable::parse(base, size)
            .map_err(|_| win32::Win32Error::ImageLoadFailed)?;
        return Ok(win32::LoadedModule { base, image_size: size, exports });
    }
    let space = (*&raw mut STAGE_SPACE)
        .as_mut()
        .ok_or(win32::Win32Error::ImageLoadFailed)?;
    let mut mapper = space.mapper();
    let fa = (*&raw mut FRAME_ALLOCATOR)
        .as_mut()
        .ok_or(win32::Win32Error::ImageLoadFailed)?;
    let image = pe::load(bytes, &mut mapper, fa, 0)
        .map_err(|_| win32::Win32Error::ImageLoadFailed)?;
    // The file's own ImageBase is a pinned address (`build.sh` checks the
    // linked .text VMA); an image that landed anywhere else would make every
    // assertion below about a base nobody chose.
    if image.load_base != win32::DYNAMIC_IMAGE_BASE {
        return Err(win32::Win32Error::ImageLoadFailed);
    }
    let exports = pe::DllExportTable::parse(image.load_base, image.image_max_rva)
        .map_err(|_| win32::Win32Error::ImageLoadFailed)?;
    DYNAMIC_MAPPED = Some((image.load_base, image.image_max_rva));
    Ok(win32::LoadedModule {
        base: image.load_base,
        image_size: image.image_max_rva,
        exports,
    })
}

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

    // --- the second provider: a different image, a different export table ---
    //
    // Loaded at its own ImageBase exactly like kernel32.dll, and parsed at that
    // base so the EAT this demo hands `install_module` is the one the loader
    // would see. Its two exports are ordinary ring-3 code: the kernel will hand
    // their addresses out through `GetProcAddress`, and the client will call
    // them without ever trapping, which is why no `dispatch` arm mentions them.
    let nyr = pe::load(NYRQIS_DLL, &mut mapper, fa, 0)
        .unwrap_or_else(|e| panic!("failed to load nyrqis.dll: {:?}", e));
    let nyr_exports = pe::DllExportTable::parse(win32::SECOND_IMAGE_BASE, nyr.image_max_rva)
        .unwrap_or_else(|e| panic!("nyrqis export table failed to parse: {:?}", e));
    let nyr_declared = nyr_exports
        .declared_name()
        .unwrap_or_else(|e| panic!("nyrqis export table name failed: {:?}", e))
        .unwrap_or_else(|| panic!("nyrqis export table declares no DLL name"));
    assert!(
        nyr_declared.as_slice() == b"nyrqis.dll",
        "the second provider must declare the name the loader registers it under"
    );
    assert_eq!(
        nyr_exports.name_count(),
        (EXPECTED_NYRQIS_EXPORTS.len() + EXPECTED_NYRQIS_FORWARDERS.len()) as u32,
        "nyrqis.dll must export exactly the two functions and two forwarders its .def states"
    );
    for (name, rva) in EXPECTED_NYRQIS_EXPORTS {
        let addr = nyr_exports
            .lookup(name)
            .unwrap_or_else(|| panic!("nyrqis.dll does not export {:?}", NameOf(name)));
        assert_eq!(
            addr,
            win32::SECOND_IMAGE_BASE + rva,
            "nyrqis.dll's {:?} RVA drifted from the pinned toolchain answer",
            NameOf(name)
        );
        // Its own .text window, pinned by build.sh; the same discipline as
        // kernel32's, so a code pointer out of this row is known to be code.
        assert!(
            (0x1000..0x1030).contains(&rva),
            "nyrqis export {:?} at +{:#x} is outside the provider's .text window",
            NameOf(name),
            rva
        );
    }
    // The forwarders, from the same parsed table: each must be classified as a
    // `Forward` naming exactly the module and export its .def states, and must
    // NOT answer an address out of its OWN image (`lookup` is the
    // registry-agnostic form -- this is the state the surface used to report
    // as `ERROR_PROC_NOT_FOUND`).
    for (name, fwd_dll, fwd_symbol) in EXPECTED_NYRQIS_FORWARDERS {
        assert!(
            nyr_exports.lookup(name).is_none(),
            "forwarded export {:?} must not answer an address out of its own image",
            NameOf(name)
        );
        match nyr_exports.resolve_export(Some(name), None) {
            Ok(Some(pe::ExportAnswer::Forward { dll: got_dll, symbol: got_symbol })) => {
                assert_eq!(
                    got_dll.as_slice(),
                    fwd_dll,
                    "forwarded export {:?} must name the target module its .def states",
                    NameOf(name)
                );
                assert_eq!(
                    got_symbol.as_slice(),
                    fwd_symbol,
                    "forwarded export {:?} must name the target export its .def states",
                    NameOf(name)
                );
            }
            other => panic!(
                "forwarded export {:?} must be classified as a Forward, got {:?}",
                NameOf(name),
                other
            ),
        }
    }
    serial_println!(
        "  experimental win32-demo: second provider nyrqis.dll loaded at its own ImageBase {:#x}; export table declares {:?} with {} name(s) (two functions, two forwarded exports), the functions inside .text (+0x1000..+0x1030) -- OK, a second image whose EAT the module table serves, forwarders included",
        win32::SECOND_IMAGE_BASE,
        nyr_declared,
        nyr_exports.name_count()
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
        "exactly the fourteen kernel32 imports must resolve"
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
        "  experimental win32-demo: resolved {} import(s) from the client's REAL .idata against kernel32.dll's REAL export directory (GetStdHandle, WriteFile, GetTickCount, ExitProcess, CreateFileA, ReadFile, CloseHandle, GetLastError, SetLastError, GetCommandLineA, GetModuleHandleA, GetProcAddress, LoadLibraryA, FreeLibrary), every slot reading back its export address -- OK",
        resolved.facts.len()
    );

    // --- the surface's own negative controls, kernel-side, before ring 3 ---
    win32::install(APP_IMAGE_BASE, APP_IMAGE_SIZE, SLOT_ADDR, 4096);
    // The loader half `LoadLibraryA` delegates to: the mapping lives with this
    // demo's address space and frame allocator, exactly where Windows keeps it
    // (kernel32!LoadLibraryA calls the loader behind it).
    win32::install_dynamic_loader(load_dynamic_module);

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
    expect_refusal(14, [0; 5], from_text, win32::Win32Error::UnknownFunction);
    serial_println!(
        "  experimental win32-demo: negative control 3 -- index 14 refused with the typed UnknownFunction -- OK"
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
        (win32::Win32Error::NameInvalid, 87),
        (win32::Win32Error::CommandLineInvalid, 87),
        (win32::Win32Error::ImageLoadFailed, 193),
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
        "  experimental win32-demo: negative control 7 -- the last-error cell started at ERROR_SUCCESS (0), every one of the {} typed refusals answered its pinned Windows code (unknown function 1, trap outside .text 487, invalid handle 6, out-of-region buffer/out-parameter/path 998, oversized length 87, unterminated path 206, non-text path 123, write access 5, bad disposition 87, overlapped I/O 50, loader state missing 21, unloaded module 126, unregistered module handle 6, full module table 14, bad module row 87, absent export 127, unreadable name 87, bad loader install 87, relative path 3, unknown drive 15, missing file 2, bad image format 193, exhausted table 4), a successful open left the code alone, and SetLastError(0x1234) read back through GetLastError -- OK",
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
    let failure_values: [(u64, u64); 14] = [
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
        (12, 0),                          // LoadLibraryA      -> HMODULE, NULL
        (13, 0),                          // FreeLibrary       -> BOOL
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
    // same name in another case, both ends of the export-ordinal range, the
    // forwarded exports of both kinds (one that lands in the target module, one
    // whose target export is absent), an unregistered base, and a NULL name.
    // Only then does the client ask, so whatever it gets back is an
    // answer the kernel has already checked from the other side.
    let dll_name = write_slot_string(0x3E0, b"kernel32.dll");
    let dll_name_upper = write_slot_string(0x3F0, b"KERNEL32.DLL");
    // An export name in the wrong case: the module name above is case-blind, an
    // export name is not, and that asymmetry is Windows'.
    let lower_export = write_slot_string(0x440, b"gettickcount");
    // The second provider's name and the two export names the per-module proof
    // asks both providers about. Below 0x500, where the export-name loop writes;
    // above the installed command line.
    let second_name = write_slot_string(0x460, b"nyrqis.dll");
    let tick_name = write_slot_string(0x480, b"GetTickCount");
    let build_name = write_slot_string(0x4A0, b"NyrqisBuild");
    let mode_name = write_slot_string(0x4C0, b"NyrqisMode");
    // The second provider's three FORWARDED export names. They sit ABOVE the
    // kernel32 names the loop below writes at 0x500, 0x520, ... 0x6A0 (FOURTEEN
    // names now, so the last slot is 0x6A0) and BELOW the 0x700 area the
    // client's own fields occupy. This placement is not cosmetic: at both
    // twelve and fourteen names the first draft of this tranche's string
    // offsets landed on a loop slot, so the loop overwrote "NyrqisTick" and the
    // surface correctly refused a name nyrqis.dll does not export -- the same
    // collision the previous tranche hit at 0x660, one slot later.
    let fwd_tick_name = write_slot_string(0x6C0, b"NyrqisTick");
    let fwd_ghost_name = write_slot_string(0x6E0, b"NyrqisGhost");

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
    // ... and then the SECOND provider, a row whose export table answers names
    // the first row does not have. Two rows with two EATs is the smallest table
    // in which "the answer came from THAT module" is falsifiable.
    win32::install_module(
        b"nyrqis.dll",
        win32::SECOND_IMAGE_BASE,
        nyr.image_max_rva,
        nyr_exports,
    )
    .unwrap_or_else(|e| panic!("registering nyrqis.dll as a module failed: {:?}", e));
    assert_eq!(win32::module_count(), 2);
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
    assert_eq!(win32::module_count(), 3);
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
    // The cap is proved where it is REACHABLE: with the runtime module loaded
    // (control 11 below), where a fifth registration has no row left. Here the
    // table has one spare row on purpose -- the runtime slot -- so a fourth
    // registration would be a live row rather than a refusal.
    assert_eq!(
        win32::module_count(),
        3,
        "a duplicate row must not change the live rows"
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
    // The SECOND provider's exports, by name, out of ITS row's export
    // directory: a different EAT, parsed from a different image. The names are
    // the ones already written for the cross probes below, so this walks the
    // exact bytes ring 3 will hand the surface.
    let mut second_resolved = 0;
    for (name, rva) in EXPECTED_NYRQIS_EXPORTS {
        let at = if name == b"NyrqisBuild" {
            build_name
        } else {
            mode_name
        };
        assert_eq!(
            win32::dispatch(11, [win32::SECOND_IMAGE_BASE, at, 0, 0, 0], from_text),
            Ok(win32::SECOND_IMAGE_BASE + rva),
            "GetProcAddress({:?}) must answer the SECOND provider's own export address",
            NameOf(name)
        );
        second_resolved += 1;
    }
    // The per-module claim, in BOTH directions: each provider is asked for a
    // name the OTHER one exports. A surface that kept one name list, or that
    // ignored the handle, would answer one of these with an address.
    expect_refusal(
        11,
        [win32::IMAGE_BASE, build_name, 0, 0, 0],
        from_text,
        win32::Win32Error::ProcNotFound,
    );
    expect_refusal(
        11,
        [win32::SECOND_IMAGE_BASE, tick_name, 0, 0, 0],
        from_text,
        win32::Win32Error::ProcNotFound,
    );
    // ... and a NAME still resolves to the row that owns it, not to the first
    // row that exists.
    assert_eq!(
        win32::dispatch(10, [second_name, 0, 0, 0, 0], from_text),
        Ok(win32::SECOND_IMAGE_BASE),
        "the second provider must answer its own base, not the first's"
    );
    // The four refusals, each for its own reason and each with its own code.
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
    // The ordinal form is served, so kernel32's own slots must answer what the
    // NAMES answer. This is the probe the previous tranche used to prove the
    // form was REFUSED; it now proves the form agrees with the name table on the
    // same provider. GNU ld writes the EAT in name-table order (CloseHandle,
    // CreateFileA, ExitProcess, ...), so slot 0 is CloseHandle and slot 2 is
    // ExitProcess -- not the source order the stubs were written in. That is
    // exactly why both expectations come from the pinned name/RVA table rather
    // than from an assumed `EAT[index] == the Nth export in this file`.
    assert_eq!(
        win32::dispatch(11, [win32::IMAGE_BASE, 3, 0, 0, 0], from_text),
        Ok(win32::IMAGE_BASE + expected_rva(b"ExitProcess")),
        "ordinal 3 is kernel32.dll's third EAT slot: the same address the NAME answered"
    );
    expect_refusal(
        11,
        [win32::IMAGE_BASE, 0, 0, 0, 0],
        from_text,
        win32::Win32Error::NameInvalid,
    );
    // The ORDINAL form, from the SAME table and the SAME directories. The
    // second provider declares `OrdinalBase 10`, so its two EAT slots are
    // ordinals 10 and 11: asking by ordinal must answer exactly what the NAME
    // answered, and asking for 1 or 2 -- the numbers a lookup that forgot the
    // base would use -- must answer nothing. kernel32.dll declares the ordinary
    // base 1, so its FIRST slot is ordinal 1 and must answer that slot's own
    // export (CloseHandle, the first name in ld's table), not GetStdHandle.
    assert_eq!(
        nyr_exports.ordinal_base(),
        EXPECTED_NYRQIS_ORDINAL_BASE,
        "the second provider must declare the ordinal base its own .def states"
    );
    assert_eq!(
        export.ordinal_base(),
        1,
        "kernel32.dll's export directory declares the ordinary ordinal base 1"
    );
    assert_eq!(
        win32::dispatch(
            11,
            [win32::SECOND_IMAGE_BASE, EXPECTED_NYRQIS_ORDINAL_BASE as u64, 0, 0, 0],
            from_text
        ),
        Ok(win32::SECOND_IMAGE_BASE + expected_nyrqis_rva(b"NyrqisBuild")),
        "ordinal 10 must answer the address the NAME NyrqisBuild answered"
    );
    assert_eq!(
        win32::dispatch(
            11,
            [win32::SECOND_IMAGE_BASE, EXPECTED_NYRQIS_ORDINAL_BASE as u64 + 1, 0, 0, 0],
            from_text
        ),
        Ok(win32::SECOND_IMAGE_BASE + expected_nyrqis_rva(b"NyrqisMode")),
        "ordinal 11 must answer the address the NAME NyrqisMode answered"
    );
    assert_eq!(
        win32::dispatch(11, [win32::IMAGE_BASE, 1, 0, 0, 0], from_text),
        Ok(win32::IMAGE_BASE + expected_rva(b"CloseHandle")),
        "kernel32.dll's first EAT slot is ordinal 1 under base 1"
    );
    // Both ends of the declared range, refused as ABSENT rather than guessed:
    // one below the base (the base is not an index) and one past the last
    // function. Each also asserts the code it left in the last-error cell.
    expect_refusal(
        11,
        [win32::SECOND_IMAGE_BASE, EXPECTED_NYRQIS_ORDINAL_BASE as u64 - 1, 0, 0, 0],
        from_text,
        win32::Win32Error::ProcNotFound,
    );
    expect_refusal(
        11,
        [
            win32::SECOND_IMAGE_BASE,
            EXPECTED_NYRQIS_ORDINAL_BASE as u64 + 4,
            0,
            0,
            0,
        ],
        from_text,
        win32::Win32Error::ProcNotFound,
    );
    // The FORWARDED exports. Asked for `NyrqisTick` -- whose EAT slot is the
    // string `kernel32.dll.GetTickCount`, not code -- the surface must answer
    // the TARGET row's real GetTickCount address, the very value kernel32's own
    // row returns, by name and by ordinal 12 (the forwarder's own ordinal).
    // `NyrqisGhost` names an export kernel32 does not have, so the same chase
    // must end in `ProcNotFound` rather than in either image.
    assert_eq!(
        win32::dispatch(11, [win32::SECOND_IMAGE_BASE, fwd_tick_name, 0, 0, 0], from_text),
        Ok(win32::IMAGE_BASE + expected_rva(b"GetTickCount")),
        "a forwarded export must answer the TARGET module's real address"
    );
    assert_eq!(
        win32::dispatch(11, [win32::SECOND_IMAGE_BASE, 12, 0, 0, 0], from_text),
        Ok(win32::IMAGE_BASE + expected_rva(b"GetTickCount")),
        "the ordinal form of a forwarder must chase the same way its name does"
    );
    expect_refusal(
        11,
        [win32::SECOND_IMAGE_BASE, fwd_ghost_name, 0, 0, 0],
        from_text,
        win32::Win32Error::ProcNotFound,
    );
    serial_println!(
        "  experimental win32-demo: forwarded exports -- nyrqis.dll's NyrqisTick (a forwarder to kernel32.dll.GetTickCount) answered {:#x}, the very address kernel32.dll's own row gives GetTickCount, BY NAME and BY ORDINAL 12; NyrqisGhost (a forwarder to kernel32.dll.NoSuchExport) answered ProcNotFound with ERROR_PROC_NOT_FOUND ({}) because the target module has no such export -- OK, a forwarder is chased through the module table into the target row's own export directory, not answered out of the forwarder's image",
        win32::IMAGE_BASE + expected_rva(b"GetTickCount"),
        win32::ERROR_PROC_NOT_FOUND
    );
    serial_println!(
        "  experimental win32-demo: negative control 10 -- with no module registered, GetProcAddress was refused as LoaderStateMissing and a named GetModuleHandleA as ModuleNotFound; five unusable rows (empty name, non-text name, zero base, zero image, an image too small for the exports it names) were refused as ModuleInstallInvalid and published nothing; kernel32.dll, nyrqis.dll and win32_console.exe then registered as three rows, both spellings of kernel32.dll's name answered {:#x} and nyrqis.dll answered its own {:#x}, a duplicate row was refused (ModuleInstallInvalid; the cap is proved in control 11, where the runtime row makes it reachable), all {} kernel32 exports and both nyrqis exports resolved BY NAME from their OWN export directories ({}+{}), a lowercase export name and the client's exportless image answered ProcNotFound, kernel32.dll asked for NyrqisBuild answered ProcNotFound and nyrqis.dll asked for GetTickCount answered ProcNotFound (so the rows are per-module), an unregistered base ModuleNotLoaded, a NULL name NameInvalid, and the ORDINAL form answered from the same directories -- nyrqis.dll's declared OrdinalBase {} made its first two exports ordinals {} and {} (both answered the addresses their NAMES did), kernel32.dll's first slot answered ordinal 1 under the ordinary base 1, and ordinals {} and {} (below that base and past its last slot) answered ProcNotFound rather than an index guess -- OK, GetProcAddress answers PER MODULE from a module table the loader built and then certified",
        win32::IMAGE_BASE,
        win32::SECOND_IMAGE_BASE,
        EXPECTED_EXPORTS.len(),
        named_resolved,
        second_resolved,
        EXPECTED_NYRQIS_ORDINAL_BASE,
        EXPECTED_NYRQIS_ORDINAL_BASE,
        EXPECTED_NYRQIS_ORDINAL_BASE + 1,
        EXPECTED_NYRQIS_ORDINAL_BASE - 1,
        EXPECTED_NYRQIS_ORDINAL_BASE + 5
    );

    // Negative control 11: LoadLibraryA/FreeLibrary, the pair that makes the
    // module table GROW and SHRINK while a process runs.
    //
    // # Why this is the tranche's kernel-side half, and why it is ordered
    //
    // The module table so far was entirely loader-installed, so a table that
    // could never change would answer every question identically. This control
    // asks the same forwarder question three times around a real load and a
    // real free, and pins the lifecycle in between: the file the volume has,
    // the file it does not, the same file loaded twice (one row, two
    // references), the table full while that row lives, one free leaving it
    // loaded, the second free dropping it, and a free of the released base
    // refused as an invalid handle. The client below repeats the load, the
    // forwarder resolution, the call and the free from ring 3 -- so the
    // surface's own controls and the client's run agree about a sequence that
    // did not exist at boot time.
    let dyn_path = write_slot_string(0x900, DYNAMIC_PATH);
    let dyn_name = write_slot_string(0x940, DYNAMIC_NAME);
    let dyn_forwarder = write_slot_string(0x960, DYNAMIC_FORWARDER);
    let dyn_export = write_slot_string(0x980, DYNAMIC_EXPORT);
    let missing_dll = write_slot_string(0x9C0, b"C:\\nyrqis\\missing.dll");
    // Before the load: the forwarder names a module with no row.
    expect_refusal(
        11,
        [win32::SECOND_IMAGE_BASE, dyn_forwarder, 0, 0, 0],
        from_text,
        win32::Win32Error::ProcNotFound,
    );
    // A relative path is the volume's rule, not a missing module; a name the
    // volume does not have is Windows' NULL plus ERROR_MOD_NOT_FOUND, because
    // a program probing for an optional DLL is entitled to a value it can test.
    expect_refusal(
        12,
        [relative, 0, 0, 0, 0],
        from_text,
        win32::Win32Error::File(win32_fs::FsError::PathNotAbsolute),
    );
    assert_eq!(
        win32::dispatch(12, [missing_dll, 0, 0, 0, 0], from_text),
        Ok(0),
        "a DLL the volume does not have must answer NULL, as Windows does"
    );
    assert_eq!(
        win32::last_error(),
        win32::ERROR_MOD_NOT_FOUND,
        "a missing DLL must leave ERROR_MOD_NOT_FOUND behind, not a refused call's code"
    );
    // The load itself: a real PE mapped out of the volume's bytes, registered
    // under the file's base name.
    let dyn_base = win32::dispatch(12, [dyn_path, 0, 0, 0, 0], from_text)
        .unwrap_or_else(|e| panic!("LoadLibraryA of the runtime provider failed: {:?}", e));
    assert_eq!(
        dyn_base,
        win32::DYNAMIC_IMAGE_BASE,
        "the runtime image must map at its own ImageBase"
    );
    assert_eq!(win32::module_count(), 4, "LoadLibraryA must add exactly one row");
    // Loading it again answers the SAME base and adds no row: HMODULE identity
    // is per image, and the reference count is what a second load buys.
    assert_eq!(
        win32::dispatch(12, [dyn_path, 0, 0, 0, 0], from_text),
        Ok(dyn_base),
        "a second LoadLibraryA of one file must answer the same base"
    );
    assert_eq!(
        win32::module_count(),
        4,
        "a second LoadLibraryA of one file must not add a row"
    );
    // The cap is reachable exactly now.
    assert_eq!(
        win32::install_module(b"full.dll", win32::IMAGE_BASE, dll.image_max_rva, export),
        Err(win32::Win32Error::ModuleTableFull),
        "with the runtime row live the table is full, and a fifth row is a typed refusal"
    );
    assert_eq!(win32::module_count(), 4, "a refused registration must not add a row");
    // The row answers by NAME, and the forwarder now lands in it: the export
    // comes from the dynamic image's OWN table, at the RVA its file states.
    assert_eq!(
        win32::dispatch(10, [dyn_name, 0, 0, 0, 0], from_text),
        Ok(dyn_base),
        "the runtime module's file name must resolve to its base"
    );
    assert_eq!(
        win32::dispatch(11, [win32::SECOND_IMAGE_BASE, dyn_forwarder, 0, 0, 0], from_text),
        Ok(dyn_base + DYNAMIC_EXPORT_RVA),
        "a forwarder into the RUNTIME module must answer that module's real export"
    );
    assert_eq!(
        win32::dispatch(11, [dyn_base, dyn_export, 0, 0, 0], from_text),
        Ok(dyn_base + DYNAMIC_EXPORT_RVA),
        "the dynamic row asked directly must answer the address the chase landed on"
    );
    // One FreeLibrary releases ONE reference: two loads were made, so the row
    // stays and the forwarder still resolves.
    assert_eq!(
        win32::dispatch(13, [dyn_base, 0, 0, 0, 0], from_text),
        Ok(1),
        "FreeLibrary must answer TRUE for a live module"
    );
    assert_eq!(
        win32::module_count(),
        4,
        "one FreeLibrary after two loads must leave the module loaded"
    );
    assert_eq!(
        win32::dispatch(11, [win32::SECOND_IMAGE_BASE, dyn_forwarder, 0, 0, 0], from_text),
        Ok(dyn_base + DYNAMIC_EXPORT_RVA),
        "the forwarder must still resolve while the last reference is held"
    );
    // The second free drops the row: the name stops resolving, the forwarder
    // goes back to ProcNotFound, and the released base is no longer a handle.
    assert_eq!(
        win32::dispatch(13, [dyn_base, 0, 0, 0, 0], from_text),
        Ok(1),
        "the last FreeLibrary must drop the row"
    );
    assert_eq!(win32::module_count(), 3, "the dropped row must leave three live rows");
    expect_refusal(
        10,
        [dyn_name, 0, 0, 0, 0],
        from_text,
        win32::Win32Error::ModuleNotFound,
    );
    expect_refusal(
        11,
        [win32::SECOND_IMAGE_BASE, dyn_forwarder, 0, 0, 0],
        from_text,
        win32::Win32Error::ProcNotFound,
    );
    expect_refusal(
        13,
        [dyn_base, 0, 0, 0, 0],
        from_text,
        win32::Win32Error::ModuleNotLoaded,
    );
    serial_println!(
        "  experimental win32-demo: runtime modules -- nyrqis.dll's forwarder NyrqisDyn answered ProcNotFound with no nyrqis_dyn.dll row; LoadLibraryA(\"C:\\nyrqis\\nyrqis_dyn.dll\") answered the image's own base {:#x} and made it a fourth row, a SECOND load of the same file answered the same base with no new row, a fifth registration was refused as ModuleTableFull ({} rows), the module's name and the forwarder both answered {:#x} (its own export, at the RVA its file states, and the client's own row answered the same), one FreeLibrary left it loaded with the forwarder still resolving, the second dropped the row so the name answered ERROR_MOD_NOT_FOUND ({}) and the forwarder ERROR_PROC_NOT_FOUND ({}), and FreeLibrary of the released base was refused as ModuleNotLoaded; a DLL the volume does not have answered NULL with ERROR_MOD_NOT_FOUND ({}) -- OK, the module table grows and shrinks while a program runs",
        dyn_base,
        win32::MAX_MODULES,
        dyn_base + DYNAMIC_EXPORT_RVA,
        win32::ERROR_MOD_NOT_FOUND,
        win32::ERROR_PROC_NOT_FOUND,
        win32::ERROR_MOD_NOT_FOUND
    );

    // No teardown for this demo (see teardown_demo): the space stays owned by
    // `STAGE_SPACE` for the rest of the boot, and this stack is the one the
    // ring-3 entry below returns to.

    serial_println!(
        "  experimental win32-demo: kernel-side proof complete; entering the console client (ring 3), which starts the way a CRT does -- GetCommandLineA and GetModuleHandleA(NULL) -- then resolves TWO providers BY NAME and binds GetTickCount from kernel32.dll and NyrqisBuild/NyrqisMode from nyrqis.dll through GetProcAddress, calls kernel32.dll's own stubs for GetStdHandle, CreateFileA/ReadFile/CloseHandle on the read-only C: volume, WriteFile, GetTickCount twice, then ExitProcess({})",
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
    let named_second = slot(SLOT_NAMED_SECOND);
    let (second_build, second_mode) = (slot(SLOT_SECOND_BUILD), slot(SLOT_SECOND_MODE));
    let (second_build_value, second_mode_value) =
        (slot(SLOT_SECOND_BUILD_VALUE), slot(SLOT_SECOND_MODE_VALUE));
    let (cross_k32_none, cross_k32_err) = (slot(SLOT_CROSS_K32_NONE), slot(SLOT_CROSS_K32_ERR));
    let (cross_sec_none, cross_sec_err) = (slot(SLOT_CROSS_SEC_NONE), slot(SLOT_CROSS_SEC_ERR));
    let (ord_mode, ord_same, ord_mode_value) =
        (slot(SLOT_ORD_MODE), slot(SLOT_ORD_SAME), slot(SLOT_ORD_MODE_VALUE));
    let (ord_low_none, ord_low_err) = (slot(SLOT_ORD_LOW_NONE), slot(SLOT_ORD_LOW_ERR));
    let (ord_high_none, ord_high_err) = (slot(SLOT_ORD_HIGH_NONE), slot(SLOT_ORD_HIGH_ERR));
    let (fwd_tick, fwd_same, fwd_tick_value) = (
        slot(SLOT_FWD_TICK),
        slot(SLOT_FWD_SAME),
        slot(SLOT_FWD_TICK_VALUE),
    );
    let (fwd_ghost_none, fwd_ghost_err) =
        (slot(SLOT_FWD_GHOST_NONE), slot(SLOT_FWD_GHOST_ERR));
    let (dyn_before_none, dyn_before_err) =
        (slot(SLOT_DYN_BEFORE_NONE), slot(SLOT_DYN_BEFORE_ERR));
    let (dyn_base, dyn_handle) = (slot(SLOT_DYN_BASE), slot(SLOT_DYN_HANDLE));
    let (dyn_proc, dyn_proc_value, dyn_own) = (
        slot(SLOT_DYN_PROC),
        slot(SLOT_DYN_PROC_VALUE),
        slot(SLOT_DYN_OWN),
    );
    let dyn_freed = slot(SLOT_DYN_FREED);
    let (dyn_after_none, dyn_after_err) = (slot(SLOT_DYN_AFTER_NONE), slot(SLOT_DYN_AFTER_ERR));
    let (dyn_miss_none, dyn_miss_err) = (slot(SLOT_DYN_MISS_NONE), slot(SLOT_DYN_MISS_ERR));
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
    // The per-module half, from ring 3: the second provider answers its own
    // base (and it is not the first provider's), its two exports resolve to the
    // RVAs its own table pins and were CALLED (6 and 1) with no IAT slot in
    // this program, and each provider asked for the other's export name
    // answered NULL with ERROR_PROC_NOT_FOUND -- the two directions that make
    // "the answer came from THIS row" falsifiable.
    let modules_ok = named_second == win32::SECOND_IMAGE_BASE
        && named_second != named_module
        && second_build == win32::SECOND_IMAGE_BASE + expected_nyrqis_rva(b"NyrqisBuild")
        && second_mode == win32::SECOND_IMAGE_BASE + expected_nyrqis_rva(b"NyrqisMode")
        && second_build_value == 6
        && second_mode_value == 1
        && cross_k32_none == 0
        && cross_k32_err == win32::ERROR_PROC_NOT_FOUND as u64
        && cross_sec_none == 0
        && cross_sec_err == win32::ERROR_PROC_NOT_FOUND as u64;
    // The ORDINAL form, from ring 3: asked for ordinal 11 -- which is
    // NyrqisMode, because the provider declares OrdinalBase 10 -- the surface
    // must answer the very address the NAME answered, and the client must be
    // able to CALL through it. Ordinals 9 and 12 sit below the base and past
    // the last slot, and both must fail the way a name the module does not
    // export fails, with the same code. Those two failures are why
    // EXPECTED_REFUSALS rose from 7 to 9.
    let ordinals_ok = ord_mode == second_mode
        && ord_same == 1
        && ord_mode_value == 1
        && ord_low_none == 0
        && ord_low_err == win32::ERROR_PROC_NOT_FOUND as u64
        && ord_high_none == 0
        && ord_high_err == win32::ERROR_PROC_NOT_FOUND as u64;
    // The FORWARDED export, from ring 3: `NyrqisTick` names
    // kernel32.dll.GetTickCount, so its answer must be the very address
    // kernel32's own row gave GetTickCount (not a value inside nyrqis.dll),
    // and the client must be able to CALL through it -- the address is a
    // kernel32 stub, so the call traps from kernel32's .text. `NyrqisGhost`
    // names an export kernel32 does not have and must fail like any absent
    // export. The ghost probe is why EXPECTED_REFUSALS rose from 9 to 10.
    let forwarders_ok = fwd_tick == win32::IMAGE_BASE + expected_rva(b"GetTickCount")
        && fwd_same == 1
        && fwd_tick_value >= tick1
        && fwd_ghost_none == 0
        && fwd_ghost_err == win32::ERROR_PROC_NOT_FOUND as u64;
    // The runtime row, from ring 3: the SAME forwarder that failed before the
    // load (NULL, ERROR_PROC_NOT_FOUND) answers the dynamic module's own
    // export after it, the module's file name answers its base, the client
    // CALLED the answered pointer (42, from code it never linked against),
    // the dynamic row asked directly answered the very same address, and
    // after the client's own FreeLibrary the name and the forwarder both fail
    // again -- while a DLL the volume does not have is Windows' NULL plus
    // ERROR_MOD_NOT_FOUND. The two forwarder failures before and after the
    // load are why EXPECTED_REFUSALS rose from 10 to 12.
    let dynamic_ok = dyn_before_none == 0
        && dyn_before_err == win32::ERROR_PROC_NOT_FOUND as u64
        && dyn_base == win32::DYNAMIC_IMAGE_BASE
        && dyn_handle == dyn_base
        && dyn_proc == dyn_base + DYNAMIC_EXPORT_RVA
        && dyn_proc_value == DYNAMIC_VALUE
        && dyn_own == dyn_proc
        && dyn_freed == 1
        && dyn_after_none == 0
        && dyn_after_err == win32::ERROR_PROC_NOT_FOUND as u64
        && dyn_miss_none == 0
        && dyn_miss_err == win32::ERROR_MOD_NOT_FOUND as u64;
    let ok = startup_ok
        && dynamic_ok
        && bindings_ok
        && modules_ok
        && ordinals_ok
        && forwarders_ok
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
        && win32::module_count() == 3
        && win32::refusals() == EXPECTED_REFUSALS;
    // State every check and the raw values behind the ones that are not part of
    // the sentence below (each handle, each close result). A MISMATCH verdict
    // then names its own cause in the transcript instead of asking the next
    // reader to re-derive it from a passing-looking line.
    serial_println!(
        "  experimental win32-demo self-test checks: console={} hello={} bytes={} empty={} closed={} miss={} errors={} monotonic={} exit={} handles={} refusals={} handle_test={} startup={} bindings={} modules={} ordinals={} forwarders={} dynamic={}; handles f1={:#x} f2={:#x} f3={:#x} f4={:#x} f5={:#x} f6={:#x}; closes {} {} {} {}; reads {}/{}/{}/{}/{}/{}",
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
        modules_ok,
        ordinals_ok,
        forwarders_ok,
        dynamic_ok,
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
        "  experimental win32-demo self-test: GetCommandLineA answered {:#x} on both calls with the client's own byte-and-NUL compare {} it, and GetModuleHandleA(NULL) answered the client's image base {:#x} with the client's _start-inside-image test {} it; GetModuleHandleA(\"kernel32.dll\") answered the provider's own base {:#x} in either case, and GetProcAddress named GetTickCount at {:#x} -- the very address the loader bound into the IAT ({}) -- which the client then CALLED through, reading {} (>= {}); a second provider, nyrqis.dll, answered its own row's base {:#x} (different from kernel32's), and its NyrqisBuild and NyrqisMode exports resolved there at {:#x} and {:#x}, which the client CALLED through, reading {} and {}; kernel32.dll asked for NyrqisBuild answered {:#x} with ERROR_PROC_NOT_FOUND ({}) and nyrqis.dll asked for GetTickCount answered {:#x} the same ({}); asked in Windows' ordinal form, the second provider's base-{} ordinal {} answered {:#x} -- the same address its NAME did ({}) -- which the client CALLED through, reading {}; ordinals {} and {} (below the base, past the last slot) answered {:#x} and {:#x} with ERROR_PROC_NOT_FOUND ({}) and ({}); its forwarded export NyrqisTick (kernel32.dll.GetTickCount) answered {:#x} BY NAME and BY ORDINAL 12 -- the same address kernel32.dll's own row gave GetTickCount ({}) -- which the client CALLED through, reading {}; a forwarder to an export kernel32.dll does not have answered {:#x} with ERROR_PROC_NOT_FOUND ({}); BEFORE LoadLibraryA, nyrqis.dll's forwarder NyrqisDyn (naming nyrqis_dyn.dll.NyrqisDynValue) answered {:#x} with ERROR_PROC_NOT_FOUND ({}), LoadLibraryA(\"C:\\nyrqis\\nyrqis_dyn.dll\") then answered the runtime image's own base {:#x}, the file name GetModuleHandleA(\"nyrqis_dyn.dll\") answered the same base {:#x} (a row for a module the client never linked against), the SAME forwarder then answered {:#x} -- the dynamic row's own export at +{:#x} -- which the client CALLED through, reading {}, FreeLibrary answered {} and the forwarder went back to {:#x} with ERROR_PROC_NOT_FOUND ({}), and a DLL the volume does not have answered {:#x} with ERROR_MOD_NOT_FOUND ({}); a module that is not loaded answered {:#x} with ERROR_MOD_NOT_FOUND ({}) and a name that module does not export answered {:#x} with ERROR_PROC_NOT_FOUND ({}); WriteFile returned {} and wrote {} byte(s); the console received {:?}; hello.txt opened as handle {:#x}, read {}+{} byte(s) then {} more at EOF, closed -> {} and the client's own copy of the bytes matches; bytes.bin read back byte-identically (not text: 0x00/0x80/0xfe), empty.txt read TRUE with 0 byte(s); a closed handle was refused by ReadFile ({}) and by a second CloseHandle ({}); a missing file returned {:#x} and GetLastError answered ERROR_FILE_NOT_FOUND ({}), the closed handle answered ERROR_INVALID_HANDLE ({} then {}),         GENERIC_WRITE was refused with ERROR_ACCESS_DENIED ({}), returned {:#x} rather than 0 and passed the client's own INVALID_HANDLE_VALUE handle test ({}), SetLastError(0x1234) read back as {:#x}, and a successful GetTickCount left the code at {}; GetTickCount read {} then {} (monotonic); ExitProcess recorded {:?}; {} file handle(s) still open; refusals {} (the program's closed-handle pair, its refused write-access open, its absent-module and absent-export probes, its two cross-module probes, its two out-of-range ordinal probes, its forwarder-to-an-absent-export probe, and its two NyrqisDyn probes -- one before LoadLibraryA and one after FreeLibrary) -- {}",
        cmdline,
        cmdline_ok,
        module,
        module_ok,
        named_module,
        proc_tick,
        proc_tick_same,
        proc_tick_value,
        tick1,
        named_second,
        second_build,
        second_mode,
        second_build_value,
        second_mode_value,
        cross_k32_none,
        cross_k32_err,
        cross_sec_none,
        cross_sec_err,
        EXPECTED_NYRQIS_ORDINAL_BASE + 1,
        EXPECTED_NYRQIS_ORDINAL_BASE + 1,
        ord_mode,
        ord_same,
        ord_mode_value,
        EXPECTED_NYRQIS_ORDINAL_BASE - 1,
        EXPECTED_NYRQIS_ORDINAL_BASE + 5,
        ord_low_none,
        ord_high_none,
        ord_low_err,
        ord_high_err,
        fwd_tick,
        fwd_same,
        fwd_tick_value,
        fwd_ghost_none,
        fwd_ghost_err,
        dyn_before_none,
        dyn_before_err,
        dyn_base,
        dyn_handle,
        dyn_proc,
        DYNAMIC_EXPORT_RVA,
        dyn_proc_value,
        dyn_freed,
        dyn_after_none,
        dyn_after_err,
        dyn_miss_none,
        dyn_miss_err,
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
            "OK, a real Windows-format console program started the way a CRT does -- its command line and its own image base, both read from loader state -- then resolved kernel32.dll and a second provider, nyrqis.dll, by name AND by Windows' ordinal form (the second provider's declared OrdinalBase 10 making its exports ordinals 10 and 11, never bare indices), bound GetTickCount through GetProcAddress out of each module's own export directory (every name resolving only in the row that exports it), chased a forwarded export into the module it names (nyrqis.dll's NyrqisTick answering kernel32.dll's real GetTickCount, by name and by ordinal), then LOADED A MODULE AT RUNTIME -- LoadLibraryA mapping nyrqis_dyn.dll out of the read-only volume into a new row, the same forwarder resolving into it (NyrqisDyn answering the dynamic image's own export, which the client called for 42), and FreeLibrary dropping that row so the name and the forwarder failed again -- and called fourteen kernel32 exports plus two nyrqis exports through real PE32+ DLLs, which the kernel performed in ring 0 for the kernel32 half against a read-only in-memory volume, explaining every failure through GetLastError -- a real Win32 API surface, not Windows compatibility (no NT syscall layer, no directories or writes, ExitProcess records and returns)"
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
