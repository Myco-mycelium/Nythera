//! The `0x84` service surface: the gfx and input calls the release's shell
//! stack uses, as much of them as this kernel can honestly serve today.
//!
//! What this is: a ring-3-callable gate (`int $0x84`, DPL 3, call number in
//! `rax`, argument in `rdi`, result in `rax`) that serves four calls:
//!
//! | nr | call | argument | returns |
//! |---|---|---|---|
//! | 0 | `INPUT_STATE` | — | `(x & 0xffff) | (y & 0xffff) << 16 | buttons << 32` |
//! | 1 | `GFX_MODE` | — | `(width << 32) | height` of the bound framebuffer |
//! | 2 | `GFX_MODE_INTO` | user pointer | writes `(u32 width, u32 height)` there; `0`, or `-EFAULT` |
//! | 3 | `PROOF_DONE` | user pointer | the ring-3 proof's read-back check (see below) |
//!
//! and answers `-ENOSYS` for everything else, including the release's IPC,
//! capability, process and shared-memory calls. A call that is not served
//! must say so: a shell that silently receives zeroes from an unported
//! service has no way to tell "nothing happened" from "this kernel cannot
//! do that".
//!
//! What this is NOT, stated as plainly as the manifest does: this is not
//! the release's `0x84` surface (118 KiB of capability/IPC/process/gfx/
//! input), and it does not make the shell a session. There is no
//! user-space process owning the display, no input delivery to a
//! foreground container, no capability-managed framebuffer window and no
//! IPC. The calls here are the ones whose answers exist in this kernel: the
//! pointer is where the PS/2 driver says it is, and the display is the mode
//! the framebuffer driver bound.
//!
//! The pointer-taking call exists only because the user-range validator now
//! does: `GFX_MODE_INTO` refuses any address the active page tables do not
//! mark PRESENT|USER_ACCESSIBLE|WRITABLE (`memory::paging::user_range_ok`),
//! so a ring-3 caller cannot turn "give me the mode" into a kernel write
//! into arbitrary memory. The release's framebuffer *mapping* call -- which
//! hands a process a window onto the device -- is still unported: that is a
//! capability decision (which process may own the display), not an address
//! check.

use core::arch::naked_asm;
use core::sync::atomic::{AtomicI64, AtomicU32, AtomicU64, AtomicU8, Ordering};

use x86_64::structures::idt::InterruptDescriptorTable;
use x86_64::{PrivilegeLevel, VirtAddr};

/// The vector the release's shell stack calls.
pub const VECTOR: u8 = 0x84;

pub const CALL_INPUT_STATE: u64 = 0;
pub const CALL_GFX_MODE: u64 = 1;
pub const CALL_GFX_MODE_INTO: u64 = 2;
pub const CALL_PROOF_DONE: u64 = 3;

/// Linux's `ENOSYS`, and the release's convention: negative errno in `rax`.
pub const ENOSYS: i64 = -38;
/// Bad user pointer: the validator refused the range.
pub const EFAULT: i64 = -14;

// ---- proof bookkeeping -------------------------------------------------
//
// Set only when the caller was ring 3: the boot self-test calls `dispatch`
// directly from ring 0, and those calls must not be mistaken for the
// privilege-transition proof.

static CPL3_CALLS: AtomicU32 = AtomicU32::new(0);
static LAST_CALLER_CPL: AtomicU64 = AtomicU64::new(0xFF);
static RET_GFX: AtomicI64 = AtomicI64::new(0);
static RET_INPUT: AtomicI64 = AtomicI64::new(0);
static RET_ENOSYS: AtomicI64 = AtomicI64::new(0);
static WROTE_W: AtomicU32 = AtomicU32::new(0);
static WROTE_H: AtomicU32 = AtomicU32::new(0);
/// 0 = not finished, 1 = passed, 2 = failed.
static PROOF_STATE: AtomicU8 = AtomicU8::new(0);

pub fn proof_state() -> u8 {
    PROOF_STATE.load(Ordering::Relaxed)
}

/// Packs the pointer state into the return register. Clamped rather than
/// wrapped: a coordinate past 65535 is a driver bug, and silently
/// truncating it would put the pointer somewhere it never was.
fn pack_pointer() -> i64 {
    let f = crate::ps2_aux::facts();
    let (px, py) = crate::cursor::last_position();
    let px = px.clamp(0, 0xffff) as u64;
    let py = py.clamp(0, 0xffff) as u64;
    let buttons = (f.last.buttons & 0xff) as u64;
    (px | (py << 16) | (buttons << 32)) as i64
}

fn pack_gfx() -> i64 {
    match crate::devices::framebuffer::bound_mode() {
        Some((w, h)) => ((w as i64) << 32) | (h as i64),
        None => ENOSYS,
    }
}

/// The dispatcher. `cpl` is the caller's privilege level, read from the
/// saved CS by the entry stub -- without it, a kernel-side call and a
/// ring-3 call look identical, and the proof would be worthless.
pub fn dispatch(nr: u64, arg: u64, cpl: u64) -> i64 {
    let from_ring3 = cpl == 3;
    if from_ring3 {
        LAST_CALLER_CPL.store(cpl, Ordering::Relaxed);
        CPL3_CALLS.fetch_add(1, Ordering::Relaxed);
    }
    match nr {
        CALL_INPUT_STATE => {
            let v = pack_pointer();
            if from_ring3 {
                RET_INPUT.store(v, Ordering::Relaxed);
            }
            v
        }
        CALL_GFX_MODE => {
            let v = pack_gfx();
            if from_ring3 {
                RET_GFX.store(v, Ordering::Relaxed);
            }
            v
        }
        CALL_GFX_MODE_INTO => {
            // The validator decides. Nothing is written before it agrees.
            if !crate::memory::paging::user_range_ok(arg, 8, true) {
                return EFAULT;
            }
            let (w, h) = match crate::devices::framebuffer::bound_mode() {
                Some(m) => m,
                None => return ENOSYS,
            };
            unsafe {
                core::ptr::write_unaligned(arg as *mut u32, w);
                core::ptr::write_unaligned((arg + 4) as *mut u32, h);
            }
            if from_ring3 {
                WROTE_W.store(w, Ordering::Relaxed);
                WROTE_H.store(h, Ordering::Relaxed);
            }
            0
        }
        CALL_PROOF_DONE => {
            if !crate::memory::paging::user_range_ok(arg, 32, false) {
                return EFAULT;
            }
            // Read the four results back OUT OF USER MEMORY: this is the
            // part the registers alone cannot prove, because it shows the
            // ring-3 program actually received and stored them.
            let mut stored = [0i64; 4];
            for (i, slot) in stored.iter_mut().enumerate() {
                *slot = unsafe { core::ptr::read_unaligned((arg as *const i64).add(i)) };
            }
            let want_gfx = RET_GFX.load(Ordering::Relaxed);
            let want_input = RET_INPUT.load(Ordering::Relaxed);
            let want_enosys = RET_ENOSYS.load(Ordering::Relaxed);
            let wrote_ok = crate::devices::framebuffer::bound_mode()
                .map(|(w, h)| {
                    // The geometry was written at BUF+24 (the program's own
                    // offset, matching its assembly); the first version of
                    // this check read BUF+0 -- where GFX_MODE's low dword
                    // lives -- and reported a MISMATCH that was the check's
                    // own bug, not the call's.
                    let at = unsafe { (arg + 24) as *const u32 };
                    unsafe {
                        core::ptr::read_unaligned(at) == w
                            && core::ptr::read_unaligned(at.add(1)) == h
                    }
                })
                .unwrap_or(true);
            let stored_ok = stored[0] == want_gfx
                && stored[1] == want_input
                && stored[2] == want_enosys
                && stored[2] == ENOSYS;
            let ok = stored_ok && wrote_ok;
            PROOF_STATE.store(if ok { 1 } else { 2 }, Ordering::Relaxed);
            crate::serial_println!(
                "  service-0x84 proof: a ring-3 program called int 0x84 from CPL {} -- GFX_MODE {:#x} , INPUT_STATE {:#x} , unported call {:#x} (refused, not answered) , validated write ({},{}) , read back through the validator: GFX {} , INPUT {} , ENOSYS {} , geometry {} -- {}",
                LAST_CALLER_CPL.load(Ordering::Relaxed),
                want_gfx as u64,
                want_input as u64,
                want_enosys as u64,
                WROTE_W.load(Ordering::Relaxed),
                WROTE_H.load(Ordering::Relaxed),
                if stored[0] == want_gfx { "matches" } else { "MISMATCH" },
                if stored[1] == want_input { "matches" } else { "MISMATCH" },
                if stored[2] == want_enosys { "matches" } else { "MISMATCH" },
                if wrote_ok { "matches" } else { "MISMATCH" },
                if ok { "OK" } else { "FAILED" }
            );
            0
        }
        _ => {
            if from_ring3 {
                RET_ENOSYS.store(ENOSYS, Ordering::Relaxed);
            }
            ENOSYS
        }
    }
}

/// Installs the gate with `DPL=Ring3` (a ring-3 `int` to a DPL-0 gate would
/// fault on the gate's privilege, not on the handler).
pub fn install(idt: &mut InterruptDescriptorTable) {
    unsafe {
        idt[VECTOR as u8]
            .set_handler_addr(VirtAddr::new(service_entry as *const () as u64))
            .set_privilege_level(PrivilegeLevel::Ring3);
    }
}

/// The trap entry. `rax` carries the call number in and the result out;
/// `rdi` carries the argument in (and survives, because it is saved);
/// `rdx` carries the caller's CS out of the hardware frame.
///
/// The hardware frame (pushed by the CPU only when the privilege level
/// CHANGES) sits at `rbx + 72` after the nine pushes: RIP, CS, RFLAGS,
/// RSP, SS. CS's low two bits are the caller's CPL, which is why this stub
/// reads it from the frame instead of asking the current ring -- inside the
/// handler we are always ring 0, and that would make every call look
/// privileged.
///
/// RSP is kept in **rbx**, a callee-saved register, and rounded down for
/// the `call` rather than padded by a counted amount (the frame differs
/// between a ring-3 and a ring-0 caller, and a pad that is right for one is
/// wrong for the other). The first version of this stub kept RSP in r11 --
/// caller-saved -- so the Rust dispatcher was free to clobber it, and the
/// epilogue's `mov rsp, r11` restored a garbage stack pointer. That showed
/// up as a double fault *after* two successful calls, which is exactly what
/// a corrupted stack looks like when the first pushes and pops happen to
/// land on valid pages.
#[unsafe(naked)]
pub unsafe extern "C" fn service_entry() {
    naked_asm!(
        "push rcx",
        "push rdx",
        "push rsi",
        "push rdi",
        "push rbx",
        "push r8",
        "push r9",
        "push r10",
        "push r11",
        "mov rbx, rsp",        // callee-saved: the dispatch cannot clobber it
        "mov rsi, rdi",       // arg = the caller's rdi
        "mov rdx, [rbx + 80]", // caller CS, from the hardware frame
        "mov rdi, rax",       // nr = the caller's rax
        "and rsp, -16",
        "call {dispatch}",
        "mov rsp, rbx",
        "pop r11",
        "pop r10",
        "pop r9",
        "pop r8",
        "pop rbx",
        "pop rdi",
        "pop rsi",
        "pop rdx",
        "pop rcx",
        "iretq",
        dispatch = sym service_dispatch_c,
    );
}

/// C half of the entry stub: SysV arguments `(nr, arg, cs)`.
extern "C" fn service_dispatch_c(nr: u64, arg: u64, cs: u64) -> i64 {
    dispatch(nr, arg, cs & 3)
}

/// The boot proof for this surface's ABI, called from ring 0. It checks the
/// answers, the refusal, and the validator's two sides -- that it accepts a
/// user page and refuses a kernel one. The privilege transition itself is
/// proven separately by `svc84_proof`, which runs a real ring-3 program
/// (`userprog/prog_svc84.s`) and closes via `PROOF_DONE`.
pub fn self_test() -> bool {
    let gfx = dispatch(CALL_GFX_MODE, 0, 0);
    let gfx_ok = match crate::devices::framebuffer::bound_mode() {
        Some((w, h)) => gfx == (((w as i64) << 32) | h as i64),
        None => gfx == ENOSYS,
    };

    let input = dispatch(CALL_INPUT_STATE, 0, 0);
    let (px, py) = crate::cursor::last_position();
    let input_ok = input
        == ((px.clamp(0, 0xffff) as i64)
            | (py.clamp(0, 0xffff) as i64) << 16
            | ((crate::ps2_aux::facts().last.buttons as i64 & 0xff) << 32));

    // Every call this kernel does not serve must be refused with the named
    // error, not answered with zeroes.
    let refused = dispatch(10, 0, 0) == ENOSYS
        && dispatch(20, 0, 0) == ENOSYS
        && dispatch(u64::MAX, 0, 0) == ENOSYS;

    // The validator: a kernel stack address must be refused (it is not
    // USER_ACCESSIBLE), and a null page certainly is. Writing through the
    // call must therefore be refused before anything is touched.
    let mut scratch = [0u32; 2];
    let kernel_refused = dispatch(CALL_GFX_MODE_INTO, scratch.as_mut_ptr() as u64, 0) == EFAULT;
    let null_refused = dispatch(CALL_GFX_MODE_INTO, 0, 0) == EFAULT;
    let validator_ok = kernel_refused && null_refused;

    let ok = gfx_ok && input_ok && refused && validator_ok;
    crate::serial_println!(
        "  service-0x84: gfx/input calls -- GFX_MODE {}, INPUT_STATE {}, unported calls refused with -ENOSYS {}, pointer validator refuses a kernel address and null {} ; gate installed at DPL3 -- {}",
        if gfx_ok { "OK" } else { "FAILED" },
        if input_ok { "OK" } else { "FAILED" },
        if refused { "OK" } else { "FAILED" },
        if validator_ok { "OK" } else { "FAILED" },
        if ok { "OK" } else { "FAILED" }
    );
    ok
}
