//! The Win32 API surface, v0: fourteen `kernel32.dll` exports performed by this
//! kernel, reachable from ring 3 through a real Windows-format DLL whose
//! bodies are trap stubs (`userprog/prog_win32_kernel32.s`) -- including
//! `LoadLibraryA` and `FreeLibrary`, which let the module table below grow and
//! shrink while a program runs.
//!
//! # What this is, precisely
//!
//! The first actual Win32 API surface in this kernel -- and fourteen functions
//! out of the tens of thousands kernel32 exposes: the four a console program
//! needs (`GetStdHandle`, `WriteFile`, `GetTickCount`, `ExitProcess`), the
//! three that give it files (`CreateFileA`, `ReadFile`, `CloseHandle`) against
//! the read-only volume in [`crate::experimental::win32_fs`], the pair that
//! tells it why a call failed (`GetLastError`, `SetLastError`), the pair a
//! CRT calls before it does anything else (`GetCommandLineA`,
//! `GetModuleHandleA`), which answer with pointers into state the LOADER
//! established rather than with values computed at call time, and
//! `GetProcAddress`, which finds an export in a module the loader registered and
//! hands back its address -- the call that turns a program's imports into a
//! runtime question. It is **not** Windows
//! compatibility: there is no NT system-call layer under it, no object
//! manager, no user32/gdi32, and nothing here runs Microsoft's code. What it
//! does prove is the mechanism every later function needs, end to end: a real
//! Windows-format client's import resolves to a ring-3-callable stub; the call
//! crosses into ring 0 carrying Microsoft's own argument convention; the
//! kernel refuses anything ring 3 asks for that it cannot justify; and the
//! Windows-typed result (a handle, a BOOL, a tick count) goes back to the
//! caller, which never learns it left CPL 3.
//!
//! # Why the arguments travel in memory
//!
//! An `int` gate does not save the general-purpose registers (only RFLAGS, CS,
//! RIP, RSP and SS are pushed on a privilege change), so a stub cannot hand
//! RCX/RDX/R8/R9 to the kernel through the trap frame. Each stub therefore
//! parks its arguments in the call block in its own `.data`, and the kernel
//! names that block by RVA ([`BLOCK_RVA`]), checking the magic word at load
//! time so a fixture whose layout moved is refused rather than silently
//! misread -- the same layout-drift rule the other PE fixtures follow.
//!
//! # What the kernel refuses, and why it matters
//!
//! The block is ring-3-writable, so everything in it is an untrusted claim.
//! [`dispatch`] refuses: an index that is not one of the four exports; a
//! trapping RIP outside kernel32.dll's own `.text` (so a client cannot fake a
//! kernel32 call by trapping directly); a handle [`GetStdHandle`] never issued;
//! a buffer or out-parameter outside the regions this kernel mapped for the
//! client; a length past the surface's cap; and bytes that are not text. Each
//! refusal is typed and counted, so the boot proof can show they fired.
//!
//! # Why the refusals also carry Win32 error codes
//!
//! A typed refusal is this kernel's own vocabulary; a Windows client does not
//! speak it. It calls `GetLastError` and switches on a number, and if the
//! number is invented the pair is decoration. So every [`Win32Error`] maps to
//! the code Windows itself uses for that condition ([`Win32Error::code`]):
//! `ERROR_INVALID_HANDLE` for an unissued or closed handle,
//! `ERROR_ACCESS_DENIED` for `GENERIC_WRITE` on a read-only volume,
//! `ERROR_FILE_NOT_FOUND` for a name the volume does not have, and so on.
//! There is exactly one table, and it is derived from the typed refusal -- the
//! surface never stores a number at a call site, so a refusal that fires for
//! the wrong reason cannot report a plausible-looking code.
//!
//! Two different things set the last error, and the distinction matters to a
//! client: a REFUSAL (the call did not happen, `dispatch` returned `Err`) and a
//! Windows-style FAILURE VALUE (`CreateFileA` on a missing file returns
//! `INVALID_HANDLE_VALUE` and sets `ERROR_FILE_NOT_FOUND`). The second is not a
//! refusal -- Windows reports a miss that way -- but it is still a failure the
//! client must be able to explain, which is the whole reason this tranche
//! exists. A SUCCESSFUL call leaves the cell alone, as Windows does: the code
//! is only meaningful immediately after a call that failed, and a surface that
//! cleared it on success would hide a client's own bug (checking the code after
//! a call that worked).
//!
//! # Why the startup pair has to come from the loader
//!
//! `GetCommandLineA` and `GetModuleHandleA(NULL)` are the first two calls a
//! Windows CRT makes, and both are answers about the PROCESS rather than about
//! the call: the command line is a string the loader placed before the first
//! instruction ran, and the module handle is the base address the loader mapped
//! the executable at (`HMODULE` for the main image IS its base, and it is the
//! value a CRT stores so it can walk its own headers for the entry point).
//!
//! That is why neither is implemented as a constant here. `GetCommandLineA`
//! answers with the address [`install_cmdline`] recorded after writing the
//! string into the client's own mapped page -- the address a real loader would
//! have used, and a stable one, so a second call answers the same pointer as it
//! does on Windows. `GetModuleHandleA(NULL)` answers with the client base
//! [`install`] was given. Both therefore FAIL, with a typed refusal, when the
//! loader state they describe does not exist yet
//! ([`Win32Error::LoaderStateMissing`]): a surface that returned `Ok(0)` or a
//! guess would hand a starting CRT a pointer to nothing, which is the one bug
//! a client cannot diagnose from its own side.
//!
//! A NAMED module is a different question, and it is answered from loader state
//! too -- see the module-table section below.
//!
//! # Why GetProcAddress needs a module table, and what the table holds
//!
//! `GetProcAddress(hModule, name)` is the other half of the startup pair, and it
//! is what makes a Windows program's imports a RUNTIME question: a client takes
//! the `HMODULE` a name resolved to and asks it where its functions live,
//! instead of having every address patched in before its first instruction.
//! That question has an answer only if the loader recorded what it loaded -- the
//! module's base, its name and its export directory -- so this surface keeps
//! [`MAX_MODULES`] rows of the loader's own state, filled by [`install_module`],
//! and both the named `GetModuleHandleA` and `GetProcAddress` answer out of them.
//!
//! Three decisions in that table are worth naming, because each is observable
//! from ring 3:
//!
//! - **The answer is the provider's OWN export directory**, parsed from the
//!   mapped image (the same [`pe::DllExportTable`] the import walk resolves
//!   against), never a second hard-coded name list beside `dispatch`'s index
//!   arms. A table of `(name, address)` literals here would answer correctly
//!   whether or not the file exported anything, and the boot's comparison against
//!   the loader-bound IAT would still pass. Answering from the EAT means a name
//!   resolves exactly when the loaded file says it does.
//! - **Module names match case-insensitively; export names case-sensitively.**
//!   The asymmetry is Windows': `GetModuleHandleA("KERNEL32.DLL")` and
//!   `GetModuleHandleA("kernel32.dll")` name one module, because a module name
//!   is a file name, while `GetProcAddress(h, "gettickcount")` fails, because a
//!   PE export name is a byte string compared exactly. The boot proof asks the
//!   uppercase module form (same base required) and the lowercase export form
//!   (`ERROR_PROC_NOT_FOUND` required), so both rules are pinned rather than
//!   described.
//! - **A row is certified before ring 3 can ask about it.** [`install_module`]
//!   refuses a registration whose export table names any address outside the
//!   module's own image, so `GetProcAddress` may hand out a code pointer without
//!   re-checking it: a malformed provider is a refused LOAD, not a pointer into
//!   whatever happens to be mapped next.
//!
//! # Why the table carries TWO providers, and not just the one it imports from
//!
//! A table with a single provider cannot be distinguished from a special case:
//! asking it for a name answers out of that provider's EAT whether the code
//! walks rows or calls one module directly. The second provider
//! (`userprog/nyrqis.dll`, two ordinary ring-3 functions) makes the walk
//! observable, because a name that exists in one row and not the other must
//! resolve in exactly one of them: the boot asks `kernel32.dll` for
//! `NyrqisBuild` and `nyrqis.dll` for `GetTickCount`, and requires
//! `ERROR_PROC_NOT_FOUND` from BOTH -- from ring 3 as well as from the
//! kernel-side controls. The second provider is also the cleaner proof of what
//! a runtime binding is worth: the client never imports from it, so there is no
//! IAT slot and no link-time address, and the only way its exports can be
//! called is the pointer `GetProcAddress` returned.
//!
//! # Why the ordinal form is answered by the SAME table
//!
//! `GetProcAddress` has a second calling form,
//! `GetProcAddress(h, (LPCSTR)(ULONG_PTR)n)`, where a value whose high word is
//! zero names export ordinal `n` instead of a string. The PE rule behind it is
//! not "`n` is an EAT index": an ordinal is `OrdinalBase + index`, and
//! `IMAGE_EXPORT_DIRECTORY` declares that base itself. So
//! [`pe::DllExportTable::lookup_ordinal`] -- not a second, hand-kept ordinal
//! map beside the module table -- answers this form out of the very EAT the
//! named form walks, subtracting the base the provider declared. The second
//! provider makes that rule observable instead of decorative: `nyrqis.def`
//! gives it `OrdinalBase 10`, so its two functions are ordinals 10 and 11 and
//! its first EAT slot is reached by `10 - 10`, not by ordinal 1 and not by
//! index 10. The boot asks for both ordinals, requires the SAME addresses the
//! names answered, calls through the pointer the ordinal form returned, and
//! requires `ERROR_PROC_NOT_FOUND` below the base and past the last function --
//! the two ends a guessed index would have to get wrong.
//!
//! # Why a forwarded export is chased across the rows
//!
//! A PE export's EAT slot can hold a forwarded-export STRING
//! (`other.dll.other_fn`) instead of an address: Windows' `GetProcAddress` marks
//! that case and answers from the NAMED module's own export directory, not
//! from the forwarder's image and not with a failure. The module table already
//! holds those directories, so the chase needs no second registry: the selected
//! row's table classifies its own slot
//! ([`pe::DllExportTable::resolve_export`]), a `Forward` names another ROW, and
//! that row answers in turn. The module half is matched the way every module
//! name here is (ASCII case-insensitively), and a string with no dot is retried
//! as `<name>.dll`, because forwarder strings conventionally omit the
//! extension. The walk is capped at [`FORWARDER_DEPTH_LIMIT`] hops -- the same
//! cap [`pe::DllRegistry`] applies to an import walk -- and every way it can
//! fail (an absent target module, an absent target export, a malformed string,
//! a chain that only forwards) is Windows' single answer for an export that
//! cannot be produced, `ERROR_PROC_NOT_FOUND`. The rows were certified at
//! install time, so an address the chase lands on is still an address from a
//! certified image.
//!
//! # Why LoadLibraryA is what makes the table a LOADER's table
//!
//! Every row up to here was installed before the client's first instruction:
//! the module table was written by this kernel's own loader code, so a table
//! that could never grow would answer every question identically. `LoadLibraryA`
//! makes growth observable. It takes a path, finds the FILE on the read-only
//! volume ([`win32_fs::file_bytes`]), hands those bytes to the loader
//! ([`install_dynamic_loader`] -- the mapping lives with the address space,
//! which is this surface's caller, exactly where Windows splits kernel32 from
//! the loader), and only then adds a row. `FreeLibrary` is the other half: it
//! drops the row at reference count zero.
//!
//! Three consequences are the point of the tranche, and each is a question a
//! loader-time table cannot be asked:
//!
//! - A FORWARDER into a module that is not loaded must fail, and start
//!   working the moment that module is loaded. `nyrqis.dll` exports
//!   `NyrqisDyn`, a forwarder to `nyrqis_dyn.dll.NyrqisDynValue`; that file is
//!   on the volume and nothing loads it before ring 3 asks. The same
//!   `GetProcAddress` question therefore answers `ERROR_PROC_NOT_FOUND`, then
//!   the target's real address, then `ERROR_PROC_NOT_FOUND` again after
//!   `FreeLibrary` -- from one EAT slot, decided by the set of rows.
//! - Loading the same file twice answers the SAME base and bumps a reference
//!   count, because `HMODULE` identity is Windows' and two rows for one image
//!   would make `FreeLibrary`'s count meaningless. The count is per row, so
//!   one `FreeLibrary` after two loads leaves the module loaded.
//! - A base that is not a live row is `ERROR_INVALID_HANDLE` from
//!   `FreeLibrary`, the same code every other unissued handle answers.
//!
//! What the unload does NOT do is stated rather than implied: this kernel's PE
//! loader has no unmap, so `FreeLibrary` removes the ROW and leaves the image's
//! pages mapped for the life of the process. A later `LoadLibraryA` of the same
//! file therefore re-registers the image at the base it already occupies
//! instead of failing on an already-mapped page -- the row is what comes and
//! goes. That is a stated scope limit of the mapping layer, not a claim that a
//! Windows unload has happened.
//!
//! What the table deliberately does not do: it holds [`MAX_MODULES`] rows --
//! this process's own image, the two link-time providers, and ONE runtime
//! module. A registration with no row left is a typed refusal
//! ([`Win32Error::ModuleTableFull`]), not a silent overflow: a bigger table
//! would be a claim about a loader this kernel does not have yet, and the boot
//! proves the cap is reachable only while the dynamic row is live.
//!
//! # Why a refusal also returns something Windows-shaped
//!
//! A code alone is not enough to make a refusal read like Windows, because the
//! client looks at the RESULT before it looks at the code. So a refused call
//! returns that export's own Windows failure value ([`failure_value`]) rather
//! than a blanket 0: `INVALID_HANDLE_VALUE` from a refused `CreateFileA` or
//! `GetStdHandle`, 0 from the BOOL functions. That is what makes the two halves
//! line up the way they do on Windows -- `h == INVALID_HANDLE_VALUE`, then
//! `GetLastError() == ERROR_ACCESS_DENIED` -- and the boot proof checks it from
//! both sides: a kernel-side pin per export, and the client's own handle test
//! on a `CreateFileA` the surface refused.
//!
//! The cell is one per kernel, not one per thread: v0 has a single ring-3
//! client, and Windows' per-thread last error lives in a TEB this kernel does
//! not have yet. That is a stated scope limit, not a design choice to keep.

use crate::experimental::{pe, win32_fs};
use crate::serial_println;
use core::sync::atomic::{AtomicU64, Ordering};

use x86_64::structures::idt::InterruptStackFrame;

/// `int $0x90`: the service gate every kernel32.dll stub traps through. DPL 3 —
/// see `idt::init`'s registration.
pub const TRAP_VECTOR: u8 = 0x90;

/// Where the fixture DLL loads (its own `ImageBase`; it carries no base
/// relocations, so the loader maps it here or refuses it).
pub const IMAGE_BASE: u64 = 0x1900_0000_0000;

/// Where the SECOND provider DLL (`userprog/nyrqis.dll`) loads. Its own
/// `ImageBase`, one gigabyte past `kernel32.dll`'s so the two images cannot
/// overlap even if either grows; it too carries no base relocations.
///
/// # Why a second provider has an address the surface knows
///
/// The module table keys rows on the base (`GetProcAddress`'s `HMODULE` is a
/// base, and `install_module` refuses a duplicate NAME, not a duplicate base),
/// so the boot's kernel-side controls need a value to ask about that is not
/// `IMAGE_BASE` -- otherwise "the row for the handle" and "the one provider"
/// would be the same address and a per-module mistake would be invisible. This
/// constant is a pin of the fixture's link address, exactly like
/// [`IMAGE_BASE`]: `build.sh` checks the linked DLL's `.text` VMA, and the boot
/// parses the EAT at this base.
pub const SECOND_IMAGE_BASE: u64 = 0x1901_0000_0000;

/// `.text` in `userprog/kernel32.dll` -- `objdump -h` reports VMA
/// `0x190000001000`, size `0x1e8` (fourteen stubs: the twelve up to the
/// startup pair, `GetProcAddress`, and the runtime pair `LoadLibraryA` and
/// `FreeLibrary` appended last, so every earlier RVA stayed put -- the
/// tranche that added the pair re-linked and re-checked that claim against
/// the DLL's own EAT).
/// Pinned, and re-checked against
/// the loaded image's own export RVAs at boot (`win32_demo::start`): every
/// export must fall inside this window, which is what lets [`dispatch`] accept
/// a trap only from this DLL's code.
pub const TEXT_RVA: u64 = 0x1000;
pub const TEXT_SIZE: u64 = 0x1e8;

/// `.data`, holding `nyr_call_block` (VMA `0x190000002000`, size `0x40`).
pub const BLOCK_RVA: u64 = 0x2000;
/// `"NYRW32_1"` little-endian, the block's first word.
pub const BLOCK_MAGIC: u64 = 0x4e59_5257_3332_5f31;

// The block's field offsets, mirroring the fixture's documented layout. `+40`
// is where the stub reads its RAX back, `+48` the stub's function index (a
// claim this module verifies), and `+56` the first STACK argument, which the
// two five-parameter exports (`CreateFileA`, `ReadFile`) park there because
// Microsoft's x64 convention passes it on the stack.
const OFF_ARG0: u64 = 8;
const OFF_ARG1: u64 = 16;
const OFF_ARG2: u64 = 24;
const OFF_ARG3: u64 = 32;
const OFF_RESULT: u64 = 40;
const OFF_INDEX: u64 = 48;
const OFF_ARG4: u64 = 56;

/// The one console handle this surface issues (Windows' own pseudo-handle
/// value for stdout is 0xB), and the only handle `WriteFile` accepts.
pub const STDOUT_HANDLE: u64 = 0x0b;

/// What `CreateFileA` returns when the volume has no such file. Windows
/// reports that case as this sentinel rather than as an error, so the client is
/// expected to test for it -- and this surface does the same, which is why the
/// boot proof checks the sentinel from ring 3 instead of counting a refusal.
/// The sentinel alone does not say WHAT went wrong, though, so this path also
/// records [`ERROR_FILE_NOT_FOUND`]: that is the case `GetLastError` exists
/// for, and the one the client in the boot proof actually asks about.
pub const INVALID_HANDLE_VALUE: u64 = u64::MAX;

/// Where the runtime-loaded module's OWN image goes. Its own `ImageBase`,
/// one gigabyte past the second provider, and pinned by `build.sh` out of the
/// linked file's section table -- the same discipline every other image here
/// follows. The surface does not map it: the loader reports the base it used,
/// and this constant exists so the boot can require that the loader used the
/// address the file asked for rather than one it invented.
pub const DYNAMIC_IMAGE_BASE: u64 = 0x1902_0000_0000;

/// The value a REFUSED call returns for export `index`: the failure value of
/// that export's own Windows return type, not of the condition that refused it.
///
/// # Why the value is keyed on the export, and not on the error
///
/// A Win32 client decides what happened by looking at the RESULT first. It
/// writes `if (h == INVALID_HANDLE_VALUE)`, not `if (!h)`, because a
/// HANDLE-returning function signals failure with all-ones; and it writes
/// `if (!WriteFile(..))`, because a BOOL-returning one signals failure with 0.
/// Those tests were written against Windows' prototypes, long before this
/// kernel existed, so the value a refusal returns has to be the one the
/// prototype names -- otherwise the client's own test reads a refusal as
/// success and only a later `GetLastError` (if it checks at all) says
/// otherwise. A blanket 0 would therefore be wrong for exactly the two exports
/// a client is most likely to test: `CreateFileA` and `GetStdHandle`.
///
/// So the mapping follows what the prototype DOCUMENTS as its failure
/// result, and the boot proof checks it per export rather than only for the
/// case the client happens to hit: `GetStdHandle` and `CreateFileA` are
/// `HANDLE` and answer [`INVALID_HANDLE_VALUE`]; `WriteFile`, `ReadFile` and
/// `CloseHandle` are `BOOL` and answer 0; `GetTickCount` and `GetLastError`
/// are `DWORD` and answer 0; `SetLastError` is `void` and answers 0;
/// `ExitProcess` is in the same row for completeness only -- on Windows it
/// never returns, and here its stub returns after the kernel recorded the
/// code. An index that names no export has no prototype to borrow from, so it
/// answers 0 as well: the refusal is already `UnknownFunction`, and what the
/// client gets is the same nothing a missing import would give it.
///
/// The startup exports are why the row is "documented failure result" and
/// not "return type": `GetCommandLineA` returns `LPSTR` and fails with NULL,
/// and `GetModuleHandleA` returns `HMODULE` -- a handle type -- yet its
/// documented failure is ALSO NULL, not [`INVALID_HANDLE_VALUE`]. A table keyed
/// on "is this a handle" would hand a starting CRT all-ones as a module base
/// and it would walk that address for its headers. So both answer 0, beside
/// the BOOL row. `GetProcAddress` is the third of that shape: `FARPROC` is a
/// code pointer, and Windows documents NULL as the failure every client tests
/// for (`if (!pfn) ...`), so it answers 0 too -- and a client that checked
/// `== INVALID_HANDLE_VALUE` on it would be testing the wrong prototype.
/// `LoadLibraryA` is the fourth: `HMODULE` again, and NULL is again the
/// documented failure (a client writes `if (!hmod)`), which is what makes a
/// missing DLL a value to test rather than a refusal to explain. `FreeLibrary`
/// is the fifth: `BOOL`, answering 0.
///
/// This is a second table beside [`Win32Error::code`], and deliberately a
/// different one: the code answers "why" and depends on the condition, the
/// value answers "what did the call return" and depends on the function. A
/// surface that merged them would have to invent a code-shaped result.
pub const fn failure_value(index: u64) -> u64 {
    match index {
        // HANDLE, documented to fail with the all-ones sentinel: GetStdHandle,
        // CreateFileA.
        0 | 4 => INVALID_HANDLE_VALUE,
        // BOOL, DWORD, void, LPSTR-whose-failure-is-NULL (GetCommandLineA),
        // HMODULE-whose-failure-is-NULL (GetModuleHandleA), FARPROC whose
        // failure is also NULL (GetProcAddress), and "no such export".
        _ => 0,
    }
}

/// `GENERIC_READ`: the only access right this volume can grant, since it has
/// no way to write anything.
pub const GENERIC_READ: u32 = 0x8000_0000;

/// The longest module name the table stores, in bytes. A bare module name is
/// what this v0 can be asked for (no paths, no drive letters), and 63 bytes
/// covers every one of them with room to spare. A longer name is refused at
/// [`install_module`] time, so no truncated name can ever be matched against a
/// question a client asks.
pub const MAX_MODULE_NAME: usize = 63;

/// How many modules the table holds: this process's own image, the TWO
/// providers the loader registers before ring 3, and ONE module a program may
/// load at runtime. Stated as a cap rather than "as many as asked" because a
/// fixed static table has to be sized, and a FULL table is a typed refusal
/// ([`Win32Error::ModuleTableFull`]) rather than an overflow -- growing past
/// this is a claim about a loader (search paths, dependency walking, unloading)
/// that this kernel does not have yet.
///
/// # Why the cap is four
///
/// Three is the smallest table in which "this name is in THAT module and not
/// in this one" is testable (the client image occupies a row of its own so
/// `GetProcAddress` against it is `ERROR_PROC_NOT_FOUND`, not
/// `ERROR_MOD_NOT_FOUND`; the two providers make the rows per-module). The
/// fourth is `LoadLibraryA`'s: without a spare row the surface could only
/// refuse a runtime load, and "the module table grows at runtime" would be a
/// sentence rather than a boot fact. One spare row is enough for that claim
/// and is deliberately not "a few": this table should be exactly as big as
/// the loader's ability to fill it, and the loader can map one image on
/// request.
pub const MAX_MODULES: usize = 4;

/// `GetProcAddress`'s documented ordinal form is `(LPCSTR)(ULONG_PTR)ordinal`,
/// so an argument whose high word is zero names a number rather than a string.
/// Windows draws the line here (`HIWORD(lpProcName) == 0`); below it this
/// surface asks the selected module's own export directory
/// ([`pe::DllExportTable::lookup_ordinal`]), and at or above it the argument is
/// read as a name pointer like any other. The line is a property of the
/// ARGUMENT, not a capability flag: which ordinals exist is the file's answer,
/// and one the file does not declare is `ERROR_PROC_NOT_FOUND`.
const ORDINAL_LIMIT: u64 = 0x1_0000;

/// How many forwarded-export hops `GetProcAddress` follows before it gives up.
/// The same bound [`pe::DllRegistry`] applies to an import walk, stated once
/// here for the module table: a chain that has not landed after eight hops is
/// a provider loop, and Windows' answer for an export that cannot be produced
/// is `ERROR_PROC_NOT_FOUND`, not an unbounded walk over attacker-shaped
/// tables.
const FORWARDER_DEPTH_LIMIT: u8 = 8;

/// `OPEN_EXISTING`: the only creation disposition that means anything on a
/// read-only volume, and therefore the only one this surface accepts.
const OPEN_EXISTING: u64 = 3;

// The Win32 error codes this surface reports through `GetLastError`. Every one
// is the value Windows itself uses for the same condition, so a client's own
// `switch (GetLastError())` keeps its meaning here. They are constants with
// their Windows names rather than bare numbers at the call sites, because the
// mapping is a claim about Windows that should be readable and checkable.
pub const ERROR_INVALID_FUNCTION: u32 = 1;
pub const ERROR_FILE_NOT_FOUND: u32 = 2;
pub const ERROR_PATH_NOT_FOUND: u32 = 3;
pub const ERROR_TOO_MANY_OPEN_FILES: u32 = 4;
pub const ERROR_ACCESS_DENIED: u32 = 5;
pub const ERROR_INVALID_HANDLE: u32 = 6;
/// Windows' code for "the table cannot grow any further". Used for a full
/// module table, which only this kernel's own loader can reach.
pub const ERROR_OUTOFMEMORY: u32 = 14;
pub const ERROR_INVALID_DRIVE: u32 = 15;
pub const ERROR_NOT_READY: u32 = 21;
pub const ERROR_NOT_SUPPORTED: u32 = 50;
pub const ERROR_INVALID_PARAMETER: u32 = 87;
pub const ERROR_MOD_NOT_FOUND: u32 = 126;
/// Windows' code for "the file was found and is not a loadable image". A
/// `LoadLibraryA` that names a present file whose bytes are not a PE the
/// loader can map reports this, which is deliberately not `ERROR_MOD_NOT_FOUND`.
pub const ERROR_BAD_EXE_FORMAT: u32 = 193;
/// Windows' code for "that name is not an export of that module" -- a
/// different question from `ERROR_MOD_NOT_FOUND`, which is about the module.
pub const ERROR_PROC_NOT_FOUND: u32 = 127;
pub const ERROR_INVALID_NAME: u32 = 123;
pub const ERROR_FILENAME_EXCED_RANGE: u32 = 206;
pub const ERROR_INVALID_ADDRESS: u32 = 487;
pub const ERROR_NOACCESS: u32 = 998;

/// The largest WriteFile this surface will perform, in bytes. A cap rather
/// than a length limit from Windows: it keeps the copy in this module's own
/// static buffer and makes "too large" a typed refusal instead of a trap.
pub const MAX_WRITE: usize = 96;

/// Every way ring 3 can ask for something this surface will not do.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Win32Error {
    /// `+48` is not one of the fourteen exports.
    UnknownFunction,
    /// The trapping RIP is not inside kernel32.dll's `.text`.
    CallerOutsideKernel32,
    /// `WriteFile` with a handle `GetStdHandle` did not issue.
    InvalidHandle,
    /// A buffer argument outside the regions mapped for this client.
    BufferOutsideClient,
    /// A length past [`MAX_WRITE`].
    LengthTooLarge,
    /// An out-parameter pointer outside the regions mapped for this client.
    OutPointerOutsideClient,
    /// Bytes that are not valid text (this surface's console is text).
    NotUtf8,
    /// A path pointer outside the regions mapped for this client.
    PathOutsideClient,
    /// A path with no NUL terminator inside the client's region and within
    /// `MAX_PATH` bytes -- refused rather than scanned into the next page.
    PathTooLong,
    /// A path that is not printable ASCII. This kernel's volume is named in
    /// ASCII, so a path with a control byte or a high byte is refused.
    PathNotText,
    /// `CreateFileA`'s `dwDesiredAccess` asked for a right other than
    /// `GENERIC_READ` (this volume cannot write).
    UnsupportedAccess,
    /// `CreateFileA`'s `dwCreationDisposition` is not `OPEN_EXISTING`.
    UnsupportedDisposition,
    /// `ReadFile`'s `lpOverlapped` is not NULL: v0 has no asynchronous I/O.
    OverlappedNotSupported,
    /// A startup call asked for loader state that does not exist: no command
    /// line has been installed, or no client base has been.
    LoaderStateMissing,
    /// A module name no registered row answers to, case-insensitively. This is
    /// exactly Windows' `ERROR_MOD_NOT_FOUND` condition for a name that is not
    /// loaded in this process.
    ModuleNotFound,
    /// `GetProcAddress` was given a base that is not a registered module's. The
    /// handle is a claim, like every other handle, and the table is the
    /// evidence -- Windows reports a handle it cannot name this way.
    ModuleNotLoaded,
    /// [`install_module`] was called with the table already at [`MAX_MODULES`].
    /// A kernel-side (loader) condition: a client cannot grow the table.
    ModuleTableFull,
    /// [`install_module`] got something a table row cannot honestly hold: an
    /// empty, non-ASCII or over-long name, a zero base, a zero image size, a
    /// duplicate name, or an export table naming an address outside the image
    /// that table was parsed from.
    ModuleInstallInvalid,
    /// That module has no export of that name, names no export at that
    /// ordinal, or names a forwarded export whose target cannot be produced
    /// (target module not loaded, target export absent, malformed forwarder
    /// string, or a chain longer than [`FORWARDER_DEPTH_LIMIT`]). Boldly FALSE
    /// would be worse: Windows' `ERROR_PROC_NOT_FOUND` is the one code it
    /// reports for an export that cannot be produced, and the name was
    /// compared exactly (the ordinal was resolved through the module's own
    /// `OrdinalBase`, never treated as a bare index).
    ProcNotFound,
    /// A name argument (module name or export name) that cannot be read as a
    /// bounded, printable, NUL-terminated string inside the client's regions.
    /// One variant rather than three because for a NAME there is only one thing
    /// the client can do about it, and `ERROR_INVALID_PARAMETER` is Windows' own
    /// word for "this argument is not usable".
    NameInvalid,
    /// [`install_cmdline`] was given text that is not printable ASCII, or an
    /// address outside the client's mapped regions, or more bytes than a
    /// page holds. A kernel-side (loader) argument error, never a ring-3 one.
    CommandLineInvalid,
    /// `LoadLibraryA` named a file the volume has, but the loader could not
    /// turn its bytes into a mapped image (not a PE, an image that needs
    /// rebasing, a section outside the file), or no loader hook has been
    /// installed at all. Windows' `ERROR_BAD_EXE_FORMAT`: the name resolved
    /// to a file that is not a loadable image, which is a different answer
    /// from `ERROR_MOD_NOT_FOUND` (no such file).
    ImageLoadFailed,
    /// The volume refused the request; which rule is inside.
    File(win32_fs::FsError),
}

impl Win32Error {
    /// The Win32 error code Windows reports for this condition -- the single
    /// table `GetLastError` answers from.
    ///
    /// # Why every arm maps instead of some calls storing a number
    ///
    /// A code written next to the `return Err(..)` that raised it drifts the
    /// moment the refusal is reused or a new check is inserted, and the drift
    /// is invisible: the client still gets a plausible number. Deriving the
    /// code from the variant means a refusal can only report the code of the
    /// condition it actually detected. The boot proof exercises this in both
    /// directions: kernel-side controls assert each variant's code, and the
    /// client reads the same codes back through `GetLastError` after real
    /// failures of its own.
    ///
    /// Choices worth naming. `UnsupportedAccess` is `ERROR_ACCESS_DENIED`
    /// because asking a read-only volume for `GENERIC_WRITE` is exactly the
    /// "the object exists and you may not do that" case. Both "a handle this
    /// surface never issued" and "a handle already closed" are
    /// `ERROR_INVALID_HANDLE`, as on Windows -- the client cannot tell those
    /// states apart, and neither should the code imply it can. A pointer
    /// outside the client's own regions is `ERROR_NOACCESS` (Windows' code for
    /// a bad buffer) rather than a page fault, and a trap from outside
    /// kernel32's `.text` is `ERROR_INVALID_ADDRESS` -- the trapping RIP is not
    /// a call site at all. `UnknownFunction` is `ERROR_INVALID_FUNCTION`:
    /// nothing in this surface's number space answers that index, which is not
    /// the same claim as a missing export (`ERROR_PROC_NOT_FOUND`), where the
    /// name is known and unimplemented. `LengthTooLarge` and a bad disposition
    /// are `ERROR_INVALID_PARAMETER`, and a non-text path is `ERROR_INVALID_NAME`
    /// (Windows' code for a name it will not parse), while an unterminated path
    /// is `ERROR_FILENAME_EXCED_RANGE` -- the name ran past the bound.
    ///
    /// The startup pair's three rows are the same rule applied to loader
    /// state. `LoaderStateMissing` is `ERROR_NOT_READY`: the thing the call
    /// describes is not available yet, which is precisely true and is not a
    /// claim about the client's arguments. `ModuleNameNotSupported` is
    /// `ERROR_MOD_NOT_FOUND` because a name that this process has no module
    /// for is the condition Windows uses that code for. `CommandLineInvalid`
    /// is `ERROR_INVALID_PARAMETER`: the only caller is [`install_cmdline`],
    /// i.e. this kernel's own loader, and the argument it got was wrong.
    ///
    /// The module table's six rows follow the same discipline, and each one
    /// names the question Windows' code actually answers:
    /// `ModuleNotFound` is `ERROR_MOD_NOT_FOUND` (that module is not loaded in
    /// this process) while `ProcNotFound` is `ERROR_PROC_NOT_FOUND` (that module
    /// has no such export) -- two different questions a client must be able to
    /// tell apart, because one means it named the wrong library and the other
    /// the wrong function. `ModuleNotLoaded` is `ERROR_INVALID_HANDLE`, the same
    /// code every other unissued handle answers, because a base that is not a
    /// row is not a module handle. `ModuleTableFull` is `ERROR_OUTOFMEMORY`: the
    /// table cannot grow, which is a resource condition and not a bad argument.
    /// `ModuleInstallInvalid` and `NameInvalid` are both
    /// `ERROR_INVALID_PARAMETER`, at the two places an unusable argument can
    /// arrive (this kernel's loader, and ring 3's string pointers).
    pub const fn code(self) -> u32 {
        match self {
            Win32Error::UnknownFunction => ERROR_INVALID_FUNCTION,
            Win32Error::CallerOutsideKernel32 => ERROR_INVALID_ADDRESS,
            Win32Error::InvalidHandle => ERROR_INVALID_HANDLE,
            Win32Error::BufferOutsideClient => ERROR_NOACCESS,
            Win32Error::LengthTooLarge => ERROR_INVALID_PARAMETER,
            Win32Error::OutPointerOutsideClient => ERROR_NOACCESS,
            Win32Error::NotUtf8 => ERROR_INVALID_PARAMETER,
            Win32Error::PathOutsideClient => ERROR_NOACCESS,
            Win32Error::PathTooLong => ERROR_FILENAME_EXCED_RANGE,
            Win32Error::PathNotText => ERROR_INVALID_NAME,
            Win32Error::UnsupportedAccess => ERROR_ACCESS_DENIED,
            Win32Error::UnsupportedDisposition => ERROR_INVALID_PARAMETER,
            Win32Error::OverlappedNotSupported => ERROR_NOT_SUPPORTED,
            Win32Error::LoaderStateMissing => ERROR_NOT_READY,
            Win32Error::ModuleNotFound => ERROR_MOD_NOT_FOUND,
            Win32Error::ModuleNotLoaded => ERROR_INVALID_HANDLE,
            Win32Error::ModuleTableFull => ERROR_OUTOFMEMORY,
            Win32Error::ModuleInstallInvalid => ERROR_INVALID_PARAMETER,
            Win32Error::ProcNotFound => ERROR_PROC_NOT_FOUND,
            Win32Error::NameInvalid => ERROR_INVALID_PARAMETER,
            Win32Error::CommandLineInvalid => ERROR_INVALID_PARAMETER,
            Win32Error::ImageLoadFailed => ERROR_BAD_EXE_FORMAT,
            Win32Error::File(e) => match e {
                win32_fs::FsError::PathNotAbsolute => ERROR_PATH_NOT_FOUND,
                win32_fs::FsError::UnknownVolume => ERROR_INVALID_DRIVE,
                win32_fs::FsError::FileNotFound => ERROR_FILE_NOT_FOUND,
                win32_fs::FsError::OutOfHandles => ERROR_TOO_MANY_OPEN_FILES,
                win32_fs::FsError::NotAFileHandle => ERROR_INVALID_HANDLE,
                win32_fs::FsError::ClosedHandle => ERROR_INVALID_HANDLE,
            },
        }
    }
}

/// One row of the module table: what the loader loaded, where, and how to ask it
/// for an export. `exports` is the provider's OWN parsed export directory (see
/// the module-table section of this file's docs), so `GetProcAddress` answers
/// from the file rather than from a list kept here.
#[derive(Clone, Copy)]
struct Module {
    /// The registered name, NOT NUL-terminated: `name_len` is the truth, so a
    /// name shorter than the buffer cannot accidentally compare against its
    /// trailing zeros.
    name: [u8; MAX_MODULE_NAME],
    name_len: u8,
    /// The base the loader mapped the module at -- the value `HMODULE` means.
    base: u64,
    exports: pe::DllExportTable,
    /// How many `LoadLibraryA` calls are holding this module. A row installed
    /// by this kernel's own loader starts at one, because the loader's
    /// installation IS the first reference; a second `LoadLibraryA` of the
    /// same file answers this row's base and increments, and `FreeLibrary`
    /// drops the row only when the count reaches zero. Without the count a
    /// program that loaded a module twice would have it disappear on the
    /// first free, which is not what `FreeLibrary` means on Windows.
    refs: u32,
}

/// The regions this kernel mapped for the client, and therefore the only
/// addresses its arguments may name. Zero-length until [`install`] runs, and a
/// zero-length region accepts no pointer -- so a trap that arrives early is
/// refused rather than trusted.
struct Surface {
    client_base: u64,
    client_size: u64,
    slot_base: u64,
    slot_size: u64,
    /// Where the loader placed the client's command line, or 0 while none has
    /// been installed -- the cell `GetCommandLineA` answers from.
    cmdline: u64,
    /// The loader's module table: rows `0..module_count` are live, the rest are
    /// `None`. Kept in the surface (not in a free-standing static) because it is
    /// per-process loader state, exactly like the regions above it.
    modules: [Option<Module>; MAX_MODULES],
    module_count: usize,
}

/// What a loader hook reports after mapping one image: the base it used, the
/// image's RVA footprint, and the export directory parsed out of the mapped
/// bytes. Deliberately the same three facts [`install_module`] takes, so the
/// surface never has to trust a name the hook chose or a size it guessed.
pub struct LoadedModule {
    pub base: u64,
    pub image_size: u64,
    pub exports: pe::DllExportTable,
}

/// The loader `LoadLibraryA` delegates to. The surface owns the module TABLE
/// (names, bases, reference counts, the export answers) while the CALLER owns
/// the address space and the frame allocator those pages come from -- exactly
/// Windows' split between kernel32!LoadLibraryA and the loader behind it. A
/// function pointer rather than a second global table because the loader state
/// (`stage`'s address space, the boot's allocator) belongs to the caller.
///
/// # Safety
/// The hook must map exactly the bytes it is handed, at the file's own
/// `ImageBase` (this kernel's PE loader refuses to rebase), and report that
/// base. It runs inside the `int $0x90` handler while ring 3 is suspended, so
/// it may not block or take a lock the client's own trap could already hold.
pub type DynamicLoader = unsafe fn(&[u8]) -> Result<LoadedModule, Win32Error>;

/// The installed loader hook, or `None` before [`install_dynamic_loader`]. A
/// `LoadLibraryA` with no hook is a typed refusal
/// ([`Win32Error::ImageLoadFailed`]) rather than a NULL: the surface was asked
/// to load a module and has no way to do it, which is a kernel-side setup bug
/// and not a file that happens to be wrong.
static mut DYNAMIC_LOADER: Option<DynamicLoader> = None;

/// Installs the loader `LoadLibraryA` uses. Called by the boot proof next to
/// [`install`], before any trap can arrive.
///
/// # Safety
/// Single-threaded kernel; the hook lives for the whole process.
pub unsafe fn install_dynamic_loader(loader: DynamicLoader) {
    DYNAMIC_LOADER = Some(loader);
}

static mut SURFACE: Surface = Surface {
    client_base: 0,
    client_size: 0,
    slot_base: 0,
    slot_size: 0,
    cmdline: 0,
    modules: [None; MAX_MODULES],
    module_count: 0,
};
static mut STDOUT: [u8; MAX_WRITE] = [0; MAX_WRITE];
static STDOUT_LEN: AtomicU64 = AtomicU64::new(0);
static REFUSALS: AtomicU64 = AtomicU64::new(0);
/// The `ExitProcess` code, or `u64::MAX` while none has been recorded.
static EXIT_CODE: AtomicU64 = AtomicU64::new(u64::MAX);

/// The process's last error, as `GetLastError` reports it.
///
/// Starts at zero (`ERROR_SUCCESS`), which is what a Windows thread's TEB
/// starts with, so a client that reads the code without a preceding failure
/// sees "no error" rather than an unrelated code from earlier. Plain atomic
/// rather than a lock: a single 32-bit value, written by the trap handler and
/// read by the next trap, and no client can observe a torn one because the
/// only writer is the kernel.
static LAST_ERROR: AtomicU64 = AtomicU64::new(0);

/// The last error a failed call recorded, as a client would read it with
/// `GetLastError`.
pub fn last_error() -> u32 {
    LAST_ERROR.load(Ordering::Relaxed) as u32
}

/// `SetLastError`: makes the process's last error `code`.
///
/// Windows exposes this so a client can clear the code before a call whose
/// failure it cannot distinguish from a stale error, and so a library can
/// report its own condition. This surface honours it for the same reasons --
/// and because a reader that cannot be written is not a last-error value at
/// all, it is a log.
pub fn set_last_error(code: u32) {
    LAST_ERROR.store(code as u64, Ordering::Relaxed);
}

/// Installs the client's two regions. Called by the boot proof before ring 3.
///
/// # Safety
/// Must run before any trap depends on the surface, at most once.
pub unsafe fn install(client_base: u64, client_size: u64, slot_base: u64, slot_size: u64) {
    let surface = &mut *&raw mut SURFACE;
    surface.client_base = client_base;
    surface.client_size = client_size;
    surface.slot_base = slot_base;
    surface.slot_size = slot_size;
}

/// The command line this surface will answer `GetCommandLineA` with.
///
/// # Why the kernel writes the string into the client's own page
///
/// `GetCommandLineA` returns `LPSTR`, and a pointer ring 3 cannot read is not
/// an answer -- the kernel's own statics live in supervisor-only pages, so
/// returning `&CMDLINE` would hand the client an address that faults the
/// moment its CRT touches it. So the loader writes the bytes into a page the
/// client owns (the boot proof passes a fixed offset in its report page) and
/// records that address; the call then answers with it. That is the shape a
/// real loader has too: the command line is process state placed before the
/// first instruction, not something the getter synthesizes.
///
/// # Why the install is checked rather than trusted
///
/// The address and the bytes come from kernel-side code, so a wrong one is a
/// bug HERE, not a hostile claim -- and the worst version of that bug is a
/// pointer outside a mapping the client can read, which ring 3 would only
/// discover as a page fault with no explanation. So both are checked and
/// refused by name ([`Win32Error::CommandLineInvalid`]): printable-ASCII text
/// only (this console and volume are ASCII), a NUL-terminated length that
/// fits what is left of the client's region, and a range that lies wholly
/// inside one of the regions [`install`] registered. A refused install leaves
/// the previous state untouched.
///
/// # Safety
/// Must run after [`install`], at most once. `addr` must name a page the
/// client can read (the region check is what makes the copy below safe).
pub unsafe fn install_cmdline(addr: u64, text: &[u8]) -> Result<(), Win32Error> {
    let surface = &mut *&raw mut SURFACE;
    // Text plus the terminating NUL: Windows' command line is a NUL-terminated
    // ANSI string, so the last byte the client must be able to read is the NUL.
    let total = (text.len() as u64).checked_add(1).ok_or(Win32Error::CommandLineInvalid)?;
    if !text.iter().all(|b| (0x20..0x7f).contains(b)) {
        return Err(Win32Error::CommandLineInvalid);
    }
    // The whole string must lie in ONE region, so a string that straddles two
    // mappings (and therefore a hole) is refused rather than half-copied.
    if !allowed(surface, addr, total) {
        return Err(Win32Error::CommandLineInvalid);
    }
    let dst = addr as *mut u8;
    core::ptr::copy_nonoverlapping(text.as_ptr(), dst, text.len());
    core::ptr::write_volatile(dst.add(text.len()), 0);
    surface.cmdline = addr;
    Ok(())
}

/// The address `GetCommandLineA` answers with, or `None` while no command line
/// has been installed (the boot proof asserts the refusal before it installs).
pub fn command_line() -> Option<u64> {
    let surface = unsafe { &*&raw const SURFACE };
    (surface.cmdline != 0).then_some(surface.cmdline)
}

/// Registers a module the loader mapped, so a named `GetModuleHandleA` can
/// resolve it and `GetProcAddress` can be asked about its exports.
///
/// # Why the registration is checked rather than trusted
///
/// The arguments come from kernel-side loader code, so a wrong one is a bug
/// HERE and not a hostile claim -- but two of the bugs are invisible until ring 3
/// trips over them, and both are refused by name
/// ([`Win32Error::ModuleInstallInvalid`]) instead:
///
/// - A name that is empty, not printable ASCII, too long for [`MAX_MODULE_NAME`]
///   or already registered. A truncated or duplicated row would answer the wrong
///   `HMODULE` to a perfectly ordinary question, and the client has no way to
///   tell.
/// - An export table naming an address outside `[base, base + image_size)`. That
///   is the one bug with teeth: `GetProcAddress` hands the answer to ring 3 as a
///   code pointer, so an uncertified EAT entry could point into kernel text. The
///   check is here, at install time, so no later call has to re-walk the EAT to
///   decide whether its own answer is safe.
///
/// A REFUSED registration changes nothing: the count is not bumped and the row is
/// not written, so the table a client's calls see is always a table of certified
/// rows.
///
/// An EMPTY export table is legal and is registered as such: an executable has no
/// export directory, and `GetProcAddress` against it must answer
/// `ERROR_PROC_NOT_FOUND` (a real answer) rather than the module being unusable.
///
/// # Safety
/// Must run after [`install`] (it reads the same surface). At most [`MAX_MODULES`]
/// successful calls.
pub unsafe fn install_module(
    name: &[u8],
    base: u64,
    image_size: u64,
    exports: pe::DllExportTable,
) -> Result<(), Win32Error> {
    let surface = &mut *&raw mut SURFACE;
    let name_ok = !name.is_empty()
        && name.len() <= MAX_MODULE_NAME
        && name.iter().all(|b| (0x20..0x7f).contains(b));
    if !name_ok || base == 0 || image_size == 0 || !exports.every_export_within(image_size) {
        return Err(Win32Error::ModuleInstallInvalid);
    }
    let live = surface.modules[..surface.module_count]
        .iter()
        .flatten();
    if live.clone().any(|m| module_name_equals(m, name)) {
        return Err(Win32Error::ModuleInstallInvalid);
    }
    if surface.module_count == MAX_MODULES {
        return Err(Win32Error::ModuleTableFull);
    }
    let mut row = Module {
        name: [0; MAX_MODULE_NAME],
        name_len: name.len() as u8,
        base,
        exports,
        // The loader's own installation is the first reference: a module the
        // process started with is loaded once, and a matching `FreeLibrary`
        // must be able to drop it.
        refs: 1,
    };
    row.name[..name.len()].copy_from_slice(name);
    surface.modules[surface.module_count] = Some(row);
    surface.module_count += 1;
    Ok(())
}

/// How many modules the loader has registered. The boot proof pins this, so a
/// registration that silently did nothing (or happened twice) cannot pass.
pub fn module_count() -> usize {
    let surface = unsafe { &*&raw const SURFACE };
    surface.module_count
}

/// `true` when `stored`'s name is `name` compared as a Windows module name
/// compares: ASCII case-insensitively, and only over the stored length (the row's
/// buffer is longer than most names).
fn module_name_equals(stored: &Module, name: &[u8]) -> bool {
    let len = stored.name_len as usize;
    stored.name[..len].eq_ignore_ascii_case(name)
}

/// The base of the registered module that answers to `name`, or `None`.
///
/// `None` means "no such module is loaded", which is a real Windows condition
/// ([`ERROR_MOD_NOT_FOUND`]); the caller distinguishes it from "nothing has been
/// loaded at all" ([`Win32Error::LoaderStateMissing`]), because those are
/// different bugs with different fixes.
fn module_base_by_name(surface: &Surface, name: &[u8]) -> Option<u64> {
    surface.modules[..surface.module_count]
        .iter()
        .flatten()
        .find(|m| module_name_equals(m, name))
        .map(|m| m.base)
}

/// The loaded module a forwarder string names, or `None`. A forwarder is
/// matched by the same case-insensitive module-name rule every other module
/// question uses, and a name with no dot is retried as `<name>.dll` because
/// forwarder strings conventionally omit the extension (`kernel32.GetTickCount`)
/// while a row's registered name is a file name (`kernel32.dll`).
fn forwarded_module_base(surface: &Surface, dll: &[u8]) -> Option<u64> {
    if let Some(base) = module_base_by_name(surface, dll) {
        return Some(base);
    }
    if dll.iter().any(|b| *b == b'.') {
        return None; // already names a file (or something that is not a module)
    }
    let mut with_ext = [0u8; MAX_MODULE_NAME + 4];
    if dll.len() + 4 > with_ext.len() {
        return None;
    }
    with_ext[..dll.len()].copy_from_slice(dll);
    with_ext[dll.len()..dll.len() + 4].copy_from_slice(b".dll");
    module_base_by_name(surface, &with_ext[..dll.len() + 4])
}

/// Which half of `GetProcAddress`'s argument space a question names: the
/// string form, or Windows' ordinal form (a value below [`ORDINAL_LIMIT`], not
/// a pointer).
enum ProcTarget<'a> {
    Name(&'a [u8]),
    Ordinal(u64),
}

/// One `GetProcAddress` question, asked of the module at `base` and followed
/// through the table when the answer is a forwarded export.
///
/// # Why the chase lives here and not in `pe`
///
/// [`pe::DllExportTable`] can say that an EAT slot is a forwarder and split its
/// `other.dll.other_fn` string, but it cannot know which modules are LOADED --
/// that is this surface's `modules` array. So the per-table classification is
/// `pe`'s and the row-to-row walk is the table's, exactly where Windows' loader
/// draws the line. Each hop re-enters the target row's own export directory, so
/// a forwarded name is answered by the FILE that exports it, never by the
/// forwarder's table.
///
/// # Why every failure is `ProcNotFound`
///
/// Windows' `GetProcAddress` has one failure shape: NULL and
/// `ERROR_PROC_NOT_FOUND` for an export that cannot be produced, whether the
/// name is absent, the ordinal names no slot, a forwarder string is malformed,
/// the forwarded-to module is not loaded, or the chain never lands. Those are
/// different causes internally, but a client cannot act on the difference, so
/// none of them may become an address or a different code.
fn resolve_proc(
    surface: &Surface,
    base: u64,
    target: ProcTarget<'_>,
    depth: u8,
) -> Result<u64, Win32Error> {
    let module = module_by_base(surface, base).ok_or(Win32Error::ProcNotFound)?;
    // SAFETY: every row's table was parsed from the mapped image its base
    // names, and `install_module` certified its export addresses; the mapped
    // images live for the whole process.
    let answer = unsafe {
        match target {
            ProcTarget::Name(name) => module.exports.resolve_export(Some(name), None),
            ProcTarget::Ordinal(ordinal) => module.exports.resolve_export(None, Some(ordinal)),
        }
    }
    .map_err(|_| Win32Error::ProcNotFound)?;
    match answer {
        None => Err(Win32Error::ProcNotFound),
        Some(pe::ExportAnswer::Address(addr)) => Ok(addr),
        Some(pe::ExportAnswer::Forward { dll, symbol }) => {
            if depth >= FORWARDER_DEPTH_LIMIT {
                return Err(Win32Error::ProcNotFound);
            }
            let next =
                forwarded_module_base(surface, dll.as_slice()).ok_or(Win32Error::ProcNotFound)?;
            resolve_proc(surface, next, ProcTarget::Name(symbol.as_slice()), depth + 1)
        }
    }
}

/// The base name of a path `LoadLibraryA` was given: the bytes after the last
/// `\` or `/`. Windows registers a module under its FILE name, not under the
/// path it was loaded from, so a later `GetModuleHandleA("nyrqis_dyn.dll")`
/// and a second `LoadLibraryA("C:\\nyrqis\\nyrqis_dyn.dll")` must both name the
/// same row. The result is checked against the table's own limits here, before
/// any page is mapped, so a name a row could not hold is refused as an
/// unusable ARGUMENT rather than after the loader has spent frames on it.
fn module_name_from_path(path: &[u8]) -> Result<&[u8], Win32Error> {
    let start = path
        .iter()
        .rposition(|b| *b == b'\\' || *b == b'/')
        .map_or(0, |i| i + 1);
    let name = path.get(start..).ok_or(Win32Error::NameInvalid)?;
    if name.is_empty()
        || name.len() > MAX_MODULE_NAME
        || !name.iter().all(|b| (0x20..0x7f).contains(b))
    {
        return Err(Win32Error::NameInvalid);
    }
    Ok(name)
}

/// Drops the row at `index`, keeping rows `0..module_count` a prefix with no
/// holes: every lookup walks that prefix, so a hole would either hide a live
/// row or need a second representation of "live". Removal is what
/// `FreeLibrary` does at reference count zero.
fn remove_module_at(surface: &mut Surface, index: usize) {
    let live = surface.module_count;
    if index >= live {
        return;
    }
    for i in index..live.saturating_sub(1) {
        surface.modules[i] = surface.modules[i + 1];
    }
    surface.modules[live - 1] = None;
    surface.module_count = live - 1;
}

/// The registered module whose base is exactly `base`, or `None` -- the lookup
/// `GetProcAddress` does with its `HMODULE`.
fn module_by_base(surface: &Surface, base: u64) -> Option<&Module> {
    surface.modules[..surface.module_count]
        .iter()
        .flatten()
        .find(|m| m.base == base)
}

/// Exactly the bytes the client's `WriteFile` handed the console, CRLF
/// included -- what the demo compares to its own message.
pub fn received() -> &'static [u8] {
    let len = STDOUT_LEN.load(Ordering::Relaxed) as usize;
    // SAFETY: `STDOUT` is a fixed static array and `len` is only ever set to a
    // length that was copied into it under the cap.
    unsafe { core::slice::from_raw_parts(&raw const STDOUT as *const u8, len) }
}

/// How many requests this surface refused. The boot proof asserts zero for the
/// client's own run (and counts its deliberate kernel-side probes separately).
pub fn refusals() -> u64 {
    REFUSALS.load(Ordering::Relaxed)
}

/// The `ExitProcess` code, if the client called it.
pub fn exit_code() -> Option<u64> {
    let code = EXIT_CODE.load(Ordering::Relaxed);
    if code == u64::MAX {
        None
    } else {
        Some(code)
    }
}

/// How many bytes are addressable from `ptr` in the region that contains it,
/// or `None` when `ptr` is outside every region the client owns.
fn region_remaining(surface: &Surface, ptr: u64) -> Option<u64> {
    let regions = [
        (surface.client_base, surface.client_size),
        (surface.slot_base, surface.slot_size),
    ];
    regions.iter().find_map(|&(base, size)| {
        (ptr >= base && ptr < base.checked_add(size)?).then(|| base + size - ptr)
    })
}

/// Reads a bounded NUL-terminated path from `ptr`, which must lie in one of the
/// client's regions.
///
/// The scan stops at the region's end as well as at `MAX_PATH`: a client that
/// hands over a pointer to its last mapped byte with no NUL cannot make this
/// kernel walk off the end of the mapping, it just gets a typed refusal.
///
/// # Safety
/// `ptr` is a ring-3-supplied address; the region check is what makes the
/// reads below safe.
unsafe fn read_path<'a>(surface: &Surface, ptr: u64) -> Result<&'a [u8], Win32Error> {
    let available = region_remaining(surface, ptr).ok_or(Win32Error::PathOutsideClient)?;
    let limit = core::cmp::min(available, win32_fs::MAX_PATH as u64);
    let mut len = 0u64;
    while len < limit && core::ptr::read_volatile((ptr + len) as *const u8) != 0 {
        len += 1;
    }
    if len == limit {
        return Err(Win32Error::PathTooLong);
    }
    let path = core::slice::from_raw_parts(ptr as *const u8, len as usize);
    if !path.iter().all(|b| (0x20..0x7f).contains(b)) {
        return Err(Win32Error::PathNotText);
    }
    Ok(path)
}

/// `true` when `ptr .. ptr + len` lies wholly inside one of the client's
/// mapped regions. Overflow is a refusal, not a wrap.
fn allowed(surface: &Surface, ptr: u64, len: u64) -> bool {
    let Some(end) = ptr.checked_add(len) else {
        return false;
    };
    let in_client = ptr >= surface.client_base && end <= surface.client_base + surface.client_size;
    let in_slot = ptr >= surface.slot_base && end <= surface.slot_base + surface.slot_size;
    in_client || in_slot
}

/// Reads a NAME argument (an `LPCSTR` module name or export name) out of the
/// client's regions.
///
/// # Why one refusal covers three scan failures
///
/// A path and a name are both `LPCSTR` and both go through the same bounded
/// reader, but they are not the same claim. A path's failure modes are
/// distinguishable to the client ("not absolute" is your bug, "no such drive" is
/// the volume's), so [`read_path`] reports them separately. A NAME has one thing
/// wrong with it from the client's side -- this surface cannot read it -- and
/// Windows' word for an unusable argument is `ERROR_INVALID_PARAMETER`, so NULL,
/// outside the client's pages, not text and unterminated all answer
/// [`Win32Error::NameInvalid`]. The bound is the same `MAX_PATH`-sized window, so
/// a name near the end of the client's page is refused rather than scanned into
/// the next mapping.
///
/// # Safety
/// `ptr` is a ring-3-supplied address; the region check inside [`read_path`] is
/// what makes the reads safe.
unsafe fn read_name<'a>(surface: &Surface, ptr: u64) -> Result<&'a [u8], Win32Error> {
    if ptr == 0 {
        return Err(Win32Error::NameInvalid);
    }
    read_path(surface, ptr).map_err(|_| Win32Error::NameInvalid)
}

/// Performs one call from the block at `index`/`args`, trapped from `rip`.
///
/// `args` is the five arguments a call can carry: the four register arguments
/// in order, then the first stack argument (`args[4]`), which only the two
/// five-parameter exports read.
///
/// This is the whole API surface: return values are Windows-typed (a handle or
/// `INVALID_HANDLE_VALUE`, a BOOL as 1/0, a DWORD tick count, `ExitProcess`'s
/// unused 0), and every check is a typed refusal rather than a best-effort.
///
/// Records the Win32 error code of a refused call, so `GetLastError` can
/// answer for it -- then returns the refusal unchanged. Every path into the
/// surface goes through here, including the boot proof's kernel-side controls,
/// which is what lets those controls assert the mapping and not just the
/// refusal.
///
/// # Safety
/// The caller (the trap handler, or the boot proof's negative controls) must
/// have a mapped block/console region; `args` are addresses ring 3 supplied.
pub unsafe fn dispatch(
    index: u64,
    args: [u64; 5],
    rip: u64,
) -> Result<u64, Win32Error> {
    let result = call(index, args, rip);
    if let Err(e) = result {
        LAST_ERROR.store(e.code() as u64, Ordering::Relaxed);
    }
    result
}

/// The dispatch proper, without the last-error bookkeeping: [`dispatch`] is the
/// only public entry, so no call can be refused without its code being
/// recorded.
///
/// # Safety
/// Same contract as [`dispatch`].
unsafe fn call(index: u64, args: [u64; 5], rip: u64) -> Result<u64, Win32Error> {
    // A kernel32 call must have come from kernel32's own code: an index in the
    // block is a claim, the trapping address is evidence.
    if rip < IMAGE_BASE + TEXT_RVA || rip >= IMAGE_BASE + TEXT_RVA + TEXT_SIZE {
        return Err(Win32Error::CallerOutsideKernel32);
    }
    // The table is mutable here because `LoadLibraryA` and `FreeLibrary` change
    // it; every other arm only reads it. Single-threaded, one trap at a time.
    let surface = &mut *&raw mut SURFACE;
    match index {
        // GetStdHandle(nStdHandle): the only std handle this surface owns is
        // stdout, and it answers the same value Windows uses for it.
        0 => {
            // `DWORD nStdHandle`: a 32-bit argument, so the kernel reads the
            // low DWORD and not the whole register. A C caller sets ECX and
            // leaves the upper half of RCX as it was, and the stub parks the
            // full register -- comparing all 64 bits would refuse a perfectly
            // valid `GetStdHandle(STD_OUTPUT_HANDLE)` call for that reason
            // alone (the first boot of this link did exactly that).
            if args[0] as u32 != -11i32 as u32 {
                return Err(Win32Error::InvalidHandle);
            }
            Ok(STDOUT_HANDLE)
        }
        // WriteFile(h, buf, len, &written, NULL): the console write itself.
        1 => {
            if args[0] != STDOUT_HANDLE {
                return Err(Win32Error::InvalidHandle);
            }
            let (buf, len) = (args[1], args[2]);
            if len as usize > MAX_WRITE {
                return Err(Win32Error::LengthTooLarge);
            }
            if !allowed(surface, buf, len) {
                return Err(Win32Error::BufferOutsideClient);
            }
            // The out-parameter is optional on Windows; NULL is legal. Its
            // type is LPDWORD, so exactly four bytes are written -- the first
            // version of this surface wrote eight and would have clobbered the
            // DWORD after a real client's variable.
            if args[3] != 0 && !allowed(surface, args[3], 4) {
                return Err(Win32Error::OutPointerOutsideClient);
            }
            let dst = (&raw mut STDOUT).cast::<u8>();
            core::ptr::copy_nonoverlapping(buf as *const u8, dst, len as usize);
            STDOUT_LEN.store(len, Ordering::Relaxed);
            let text = core::str::from_utf8(core::slice::from_raw_parts(dst, len as usize))
                .map_err(|_| Win32Error::NotUtf8)?;
            // The program's own console output, on this kernel's console.
            serial_println!("{}", text.trim_end());
            if args[3] != 0 {
                core::ptr::write_volatile(args[3] as *mut u32, len as u32);
            }
            Ok(1) // TRUE
        }
        // GetTickCount(): milliseconds since boot. The PIT runs at its default
        // ~18.2 Hz here, so this is ms in 55ms steps -- the same coarse clock
        // Windows 95 exposed, and stated rather than dressed up.
        2 => Ok(crate::idt::timer_ticks().wrapping_mul(55)),
        // ExitProcess(uExitCode): records the code. v0 has no task teardown to
        // switch to -- the client is the boot chain's own ring-3 thread -- so
        // the stub returns and the client's next instruction is this link's
        // completion trap, which asserts the recorded code.
        3 => {
            EXIT_CODE.store(args[0], Ordering::Relaxed);
            Ok(0)
        }
        // CreateFileA(lpFileName, dwDesiredAccess, dwShareMode,
        //            lpSecurityAttributes, dwCreationDisposition,
        //            dwFlagsAndAttributes, hTemplateFile).
        //
        // The three arguments this surface cannot honour are stated rather than
        // ignored: `dwDesiredAccess` must be exactly GENERIC_READ,
        // `dwCreationDisposition` must be OPEN_EXISTING (read from the fifth
        // argument, the one the stub had to fetch off the stack), and the share
        // mode, security attributes, flags and template are dropped -- a
        // read-only volume has nothing for them to mean, and pretending to
        // apply them would be worse than saying so.
        4 => {
            let path = read_path(surface, args[0])?;
            // `DWORD dwDesiredAccess`: the low 32 bits, for the same reason
            // GetStdHandle reads its argument as a DWORD -- a C caller sets
            // only EDX and leaves the rest of RDX as it was.
            if args[1] as u32 != GENERIC_READ {
                return Err(Win32Error::UnsupportedAccess);
            }
            if args[4] != OPEN_EXISTING {
                return Err(Win32Error::UnsupportedDisposition);
            }
            match win32_fs::open(path) {
                Ok(handle) => Ok(handle),
                // A miss is a failure VALUE, not a refusal -- Windows' own shape
                // -- but it is still a failure, so it records its code before
                // returning the sentinel. This is the call the client below
                // asks `GetLastError` about, which is the point of the pair.
                Err(win32_fs::FsError::FileNotFound) => {
                    LAST_ERROR.store(ERROR_FILE_NOT_FOUND as u64, Ordering::Relaxed);
                    Ok(INVALID_HANDLE_VALUE)
                }
                Err(e) => Err(Win32Error::File(e)),
            }
        }
        // ReadFile(hFile, lpBuffer, nNumberOfBytesToRead, lpNumberOfBytesRead,
        //          lpOverlapped).
        5 => {
            if args[4] != 0 {
                return Err(Win32Error::OverlappedNotSupported);
            }
            // The console handle belongs to the console, not to the volume.
            if args[0] == STDOUT_HANDLE {
                return Err(Win32Error::InvalidHandle);
            }
            let (buf, len) = (args[1], args[2]);
            if !allowed(surface, buf, len) {
                return Err(Win32Error::BufferOutsideClient);
            }
            if args[3] != 0 && !allowed(surface, args[3], 4) {
                return Err(Win32Error::OutPointerOutsideClient);
            }
            let read = win32_fs::read(args[0], buf as *mut u8, len).map_err(Win32Error::File)?;
            if args[3] != 0 {
                core::ptr::write_volatile(args[3] as *mut u32, read as u32);
            }
            Ok(1) // TRUE, including a zero-byte read at EOF
        }
        // CloseHandle(hObject): releases a file handle's slot and cursor.
        6 => win32_fs::close(args[0]).map(|()| 1).map_err(Win32Error::File),
        // GetLastError(): the code the last failed call recorded. It reads the
        // cell and writes nothing, so asking is never what changes the answer --
        // a client may ask twice, or ask after a successful call, and get the
        // same code.
        7 => Ok(last_error() as u64),
        // SetLastError(dwErrCode): makes the code exactly this.
        //
        // `DWORD dwErrCode`: the low 32 bits, read for the same reason
        // GetStdHandle and CreateFileA read theirs that way -- a C caller sets
        // only ECX and leaves the upper half of RCX as it was. Taking the full
        // register would store garbage above the DWORD and make GetLastError
        // answer with a code no Windows client could have set.
        8 => {
            set_last_error(args[0] as u32);
            Ok(0) // void; the stub does not read the result back
        }
        // GetCommandLineA(): the pointer the loader installed, or a typed
        // refusal when the loader never placed one. Two calls answer the same
        // address, which is what a CRT relies on when it caches the pointer;
        // nothing here is computed per call, so that is a property of the
        // state and not of this function.
        9 => match surface.cmdline {
            0 => Err(Win32Error::LoaderStateMissing),
            addr => Ok(addr),
        },
        // GetModuleHandleA(lpModuleName): NULL asks for THIS image's base; a
        // name asks the module table.
        //
        // `HMODULE` for the main executable is its load address, and the base
        // the client was mapped at is state this surface already owns -- so
        // the NULL answer is a fact rather than `APP_IMAGE_BASE` written out
        // again at a second site. A NAME is answered from the rows
        // [`install_module`] wrote, compared the way Windows compares a module
        // name: ASCII case-insensitively, because a module name is a file name.
        // A name no row answers to is `ERROR_MOD_NOT_FOUND` -- the condition
        // Windows uses for "that module is not loaded in this process", which is
        // now a statement about a table rather than about a missing feature.
        // Read as a pointer, so the whole 64-bit argument is compared -- a name
        // pointer is an address and has no DWORD-sized form.
        10 => {
            if surface.client_base == 0 {
                return Err(Win32Error::LoaderStateMissing);
            }
            if args[0] == 0 {
                return Ok(surface.client_base);
            }
            let name = read_name(surface, args[0])?;
            module_base_by_name(surface, name).ok_or(Win32Error::ModuleNotFound)
        }
        // GetProcAddress(hModule, lpProcName): the address of one export in one
        // loaded module -- the call that lets a program bind its imports at
        // runtime instead of at load time.
        //
        // The order of the checks is the order the questions have to be
        // answered in: is there a module table at all, is the handle one of its
        // rows, is the name readable, and does THAT MODULE's own export
        // directory have it. The last step is the point of the whole tranche --
        // the answer comes from the mapped file's EAT, so it cannot be right
        // about a module whose table does not export the name, and it cannot
        // name an address the install did not certify as inside the image.
        //
        // An export name is compared CASE-SENSITIVELY (inside the export
        // directory's name walk, byte for byte): that is the PE rule, and the
        // opposite of the module-name rule one arm above. A forwarded export is
        // CHASED through the table instead of refused: the slot names
        // `other.dll.other_fn`, so the answer is the target row's own EAT
        // entry ([`resolve_proc`] states the walk and its hop cap), and every
        // way that walk can fail is Windows' `ERROR_PROC_NOT_FOUND`.
        11 => {
            if surface.module_count == 0 {
                return Err(Win32Error::LoaderStateMissing);
            }
            // The handle is answered before the name is read, so a registered
            // module question keeps its own error (an unregistered base is
            // `ModuleNotLoaded`, a NULL name is `NameInvalid`) rather than
            // collapsing into the per-export answer.
            module_by_base(surface, args[0]).ok_or(Win32Error::ModuleNotLoaded)?;
            // Windows' ordinal form is `(LPCSTR)(ULONG_PTR)n`: a low value is a
            // number, not a pointer. Deciding on the VALUE (not on whether a
            // string happens to be readable at that address) is what keeps the
            // two forms distinguishable -- and both end at this module's own
            // export directory: the ordinal subtracts the base the file
            // declares and reads the EAT slot, exactly where the name walk
            // would have landed for the same export.
            if args[1] != 0 && args[1] < ORDINAL_LIMIT {
                return resolve_proc(surface, args[0], ProcTarget::Ordinal(args[1]), 0);
            }
            let name = read_name(surface, args[1])?;
            resolve_proc(surface, args[0], ProcTarget::Name(name), 0)
        }
        // LoadLibraryA(lpLibFileName): load a module this process did not
        // start with, and answer its HMODULE -- the base the loader mapped.
        //
        // The order is Windows': name the file, find it, load it, register it.
        // A name already in the table answers THAT row's base and increments
        // its reference count, because a second row for one image would make
        // FreeLibrary's count meaningless. Everything that can fail before a
        // page is spent does: the path is read through the same checked reader
        // every other path goes through, the module name is derived and
        // validated against the table's limits, and the cap is checked before
        // the loader is asked for frames. Only then are the volume's bytes
        // handed to the hook and the row installed.
        //
        // A file the volume does not have is Windows' failure VALUE (NULL plus
        // ERROR_MOD_NOT_FOUND), not a refused call: a program that probes for
        // an optional DLL must be able to read the answer without the surface
        // calling it a violation. A file that IS there but cannot be mapped is
        // a refusal (ImageLoadFailed / ERROR_BAD_EXE_FORMAT) -- that one is a
        // property of the image, and the caller needs to be told.
        12 => {
            let path = read_path(surface, args[0])?;
            let name = module_name_from_path(path)?;
            let existing = surface.modules[..surface.module_count]
                .iter()
                .position(|m| m.as_ref().map_or(false, |row| module_name_equals(row, name)));
            if let Some(i) = existing {
                if let Some(row) = surface.modules[i].as_mut() {
                    row.refs = row.refs.saturating_add(1);
                    return Ok(row.base);
                }
            }
            if surface.module_count == MAX_MODULES {
                return Err(Win32Error::ModuleTableFull);
            }
            let bytes = match win32_fs::file_bytes(path) {
                Ok(bytes) => bytes,
                Err(win32_fs::FsError::FileNotFound) => {
                    LAST_ERROR.store(ERROR_MOD_NOT_FOUND as u64, Ordering::Relaxed);
                    return Ok(0);
                }
                Err(e) => return Err(Win32Error::File(e)),
            };
            let loader = match *&raw const DYNAMIC_LOADER {
                Some(loader) => loader,
                None => return Err(Win32Error::ImageLoadFailed),
            };
            // SAFETY: the hook was installed by this kernel's loader with the
            // address space that owns the client, and it reads only the bytes
            // it is handed (a static volume entry, not client memory).
            let loaded = unsafe { loader(bytes) }?;
            // A refused registration here means the loader reported something
            // a row cannot honestly hold; the image stays mapped (there is no
            // unmap to call) but no pointer to it can be obtained, which is
            // the safe half of the failure.
            install_module(name, loaded.base, loaded.image_size, loaded.exports)?;
            Ok(loaded.base)
        }
        // FreeLibrary(hLibModule): release one reference to a loaded module,
        // and drop the row at zero.
        //
        // The base must be a LIVE row: anything else is `ERROR_INVALID_HANDLE`,
        // the code every other unissued handle answers. At zero the row is
        // removed, so `GetModuleHandleA` stops answering the name and every
        // forwarder that named this module goes back to ERROR_PROC_NOT_FOUND --
        // which is the observable half of an unload in a kernel whose loader
        // has no page unmap yet. The image's pages stay mapped; a later
        // LoadLibraryA of the same file re-registers them (the loader hook
        // states that), so the row is what comes and goes.
        13 => {
            let existing = surface.modules[..surface.module_count]
                .iter()
                .position(|m| m.as_ref().map_or(false, |row| row.base == args[0]));
            let row_index = existing.ok_or(Win32Error::ModuleNotLoaded)?;
            let drop_row = match surface.modules[row_index].as_mut() {
                Some(row) => {
                    row.refs = row.refs.saturating_sub(1);
                    row.refs == 0
                }
                None => false,
            };
            if drop_row {
                remove_module_at(surface, row_index);
            }
            Ok(1) // TRUE
        }
        _ => Err(Win32Error::UnknownFunction),
    }
}

/// The `int $0x90` handler: reads the call block, dispatches, writes the result
/// back, and returns to ring 3 at the stub's next instruction. Never chains --
/// a refused call still returns to its caller, with that export's Windows
/// failure value as the result ([`failure_value`]), so a misbehaving client's
/// own `== INVALID_HANDLE_VALUE` / `!BOOL` test sees a Windows-shaped failure
/// and the demo's assertions fail instead of the machine hanging.
///
/// # Safety
/// Called from the installed IDT entry.
pub unsafe fn service(frame: &InterruptStackFrame) {
    let block = IMAGE_BASE + BLOCK_RVA;
    if core::ptr::read_volatile(block as *const u64) != BLOCK_MAGIC {
        REFUSALS.fetch_add(1, Ordering::Relaxed);
        return;
    }
    let index = core::ptr::read_volatile((block + OFF_INDEX) as *const u64);
    let args = [
        core::ptr::read_volatile((block + OFF_ARG0) as *const u64),
        core::ptr::read_volatile((block + OFF_ARG1) as *const u64),
        core::ptr::read_volatile((block + OFF_ARG2) as *const u64),
        core::ptr::read_volatile((block + OFF_ARG3) as *const u64),
        core::ptr::read_volatile((block + OFF_ARG4) as *const u64),
    ];
    let result = match dispatch(index, args, frame.instruction_pointer.as_u64()) {
        Ok(value) => value,
        Err(e) => {
            // Counted, and said out loud: a refusal during the client's own run
            // is a bug in the client or in this surface, never a silent zero.
            REFUSALS.fetch_add(1, Ordering::Relaxed);
            serial_println!("  experimental win32-demo: service refusal {:?}", e);
            // The RESULT is the export's Windows failure value, not 0, so the
            // client's own prototype-shaped test reads the refusal the way it
            // reads a Windows failure before it asks GetLastError.
            failure_value(index)
        }
    };
    core::ptr::write_volatile((block + OFF_RESULT) as *mut u64, result);
}
