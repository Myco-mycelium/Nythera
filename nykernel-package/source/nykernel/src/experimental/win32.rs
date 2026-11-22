//! The Win32 API surface, v0: four `kernel32.dll` exports performed by this
//! kernel, reachable from ring 3 through a real Windows-format DLL whose
//! bodies are trap stubs (`userprog/prog_win32_kernel32.s`).
//!
//! # What this is, precisely
//!
//! The first actual Win32 API surface in this kernel -- and four functions out
//! of the tens of thousands kernel32 exposes. It is **not** Windows
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

use crate::serial_println;
use core::sync::atomic::{AtomicU64, Ordering};

use x86_64::structures::idt::InterruptStackFrame;

/// `int $0x90`: the service gate every kernel32.dll stub traps through. DPL 3 —
/// see `idt::init`'s registration.
pub const TRAP_VECTOR: u8 = 0x90;

/// Where the fixture DLL loads (its own `ImageBase`; it carries no base
/// relocations, so the loader maps it here or refuses it).
pub const IMAGE_BASE: u64 = 0x1900_0000_0000;

/// `.text` in `userprog/kernel32.dll` -- `objdump -h` reports VMA
/// `0x190000001000`, size `0x98`. Pinned, and re-checked against the loaded
/// image's own export RVAs at boot (`win32_demo::start`): every export must
/// fall inside this window, which is what lets [`dispatch`] accept a trap only
/// from this DLL's code.
pub const TEXT_RVA: u64 = 0x1000;
pub const TEXT_SIZE: u64 = 0x98;

/// `.data`, holding `nyr_call_block` (VMA `0x190000002000`, size `0x40`).
pub const BLOCK_RVA: u64 = 0x2000;
/// `"NYRW32_1"` little-endian, the block's first word.
pub const BLOCK_MAGIC: u64 = 0x4e59_5257_3332_5f31;

// The block's field offsets, mirroring the fixture's documented layout. `+40`
// is where the stub reads its RAX back and `+48` the stub's function index (a
// claim this module verifies).
const OFF_ARG0: u64 = 8;
const OFF_ARG1: u64 = 16;
const OFF_ARG2: u64 = 24;
const OFF_ARG3: u64 = 32;
const OFF_RESULT: u64 = 40;
const OFF_INDEX: u64 = 48;

/// The one handle this surface issues (Windows' own pseudo-handle value for
/// stdout is 0xB), and the only handle `WriteFile` accepts.
pub const STDOUT_HANDLE: u64 = 0x0b;

/// The largest WriteFile this surface will perform, in bytes. A cap rather
/// than a length limit from Windows: it keeps the copy in this module's own
/// static buffer and makes "too large" a typed refusal instead of a trap.
pub const MAX_WRITE: usize = 96;

/// Every way ring 3 can ask for something this surface will not do.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Win32Error {
    /// `+48` is not one of the four exports.
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
}

static mut SURFACE: Surface = Surface {
    client_base: 0,
    client_size: 0,
    slot_base: 0,
    slot_size: 0,
};
static mut STDOUT: [u8; MAX_WRITE] = [0; MAX_WRITE];
static STDOUT_LEN: AtomicU64 = AtomicU64::new(0);
static REFUSALS: AtomicU64 = AtomicU64::new(0);
/// The `ExitProcess` code, or `u64::MAX` while none has been recorded.
static EXIT_CODE: AtomicU64 = AtomicU64::new(u64::MAX);

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

/// Performs one call from the block at `index`/`args`, trapped from `rip`.
///
/// This is the whole API surface: return values are Windows-typed (a handle, a
/// BOOL as 1/0, a DWORD tick count, `ExitProcess`'s unused 0), and every check
/// is a typed refusal rather than a best-effort.
///
/// # Safety
/// The caller (the trap handler, or the boot proof's negative controls) must
/// have a mapped block/console region; `args` are addresses ring 3 supplied.
pub unsafe fn dispatch(
    index: u64,
    args: [u64; 4],
    rip: u64,
) -> Result<u64, Win32Error> {
    // A kernel32 call must have come from kernel32's own code: an index in the
    // block is a claim, the trapping address is evidence.
    if rip < IMAGE_BASE + TEXT_RVA || rip >= IMAGE_BASE + TEXT_RVA + TEXT_SIZE {
        return Err(Win32Error::CallerOutsideKernel32);
    }
    let surface = &*&raw const SURFACE;
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
            // The out-parameter is optional on Windows; NULL is legal.
            if args[3] != 0 && !allowed(surface, args[3], 8) {
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
                core::ptr::write_volatile(args[3] as *mut u64, len);
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
        _ => Err(Win32Error::UnknownFunction),
    }
}

/// The `int $0x90` handler: reads the call block, dispatches, writes the result
/// back, and returns to ring 3 at the stub's next instruction. Never chains -- a refused call still returns to its caller,
/// with 0 as the result, so a misbehaving client fails the demo's assertions
/// instead of hanging the machine.
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
    ];
    let result = match dispatch(index, args, frame.instruction_pointer.as_u64()) {
        Ok(value) => value,
        Err(e) => {
            // Counted, and said out loud: a refusal during the client's own run
            // is a bug in the client or in this surface, never a silent zero.
            REFUSALS.fetch_add(1, Ordering::Relaxed);
            serial_println!("  experimental win32-demo: service refusal {:?}", e);
            0
        }
    };
    core::ptr::write_volatile((block + OFF_RESULT) as *mut u64, result);
}
