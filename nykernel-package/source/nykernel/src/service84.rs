//! The `0x84` service surface: the gfx and input calls the release's shell
//! stack uses, as much of them as this kernel can honestly serve today.
//!
//! What this is: a ring-3-callable gate (`int $0x84`, DPL 3, call number in
//! `rax`, result in `rax`) that serves two calls:
//!
//! | nr | call | returns |
//! |---|---|---|
//! | 0 | `INPUT_STATE` | `(x & 0xffff) | (y & 0xffff) << 16 | buttons << 32` from the PS/2 pointer |
//! | 1 | `GFX_MODE` | `(width << 32) | height` of the bound framebuffer |
//!
//! and answers `-ENOSYS` for anything else, including the release's IPC,
//! capability, process and shared-memory calls. A call that is not served
//! must say so: a shell that silently receives zeroes from an unported
//! service has no way to tell "nothing happened" from "this kernel cannot
//! do that".
//!
//! What this is NOT, stated as plainly as the manifest does: this is not
//! the release's `0x84` surface (118 KiB of capability/IPC/process/gfx/
//! input), and it does not make the shell a session. There is no
//! user-space process owning the display, no input delivery to a
//! foreground container, no shared framebuffer window and no IPC. The two
//! calls here are the ones whose answers exist in this kernel: the pointer
//! is where the PS/2 driver says it is, and the display is the mode the
//! framebuffer driver bound.
//!
//! The ABI is deliberately register-only. Serving a pointer argument means
//! validating a user range before writing to it -- this kernel has no
//! `user_range_ok` yet (the release does), so a "give me the framebuffer"
//! call here would be a kernel write to an unvalidated user address. The
//! pointer-taking calls arrive with that validator, not before it.

use core::arch::naked_asm;
use x86_64::structures::idt::InterruptDescriptorTable;
use x86_64::{PrivilegeLevel, VirtAddr};

/// The vector the release's shell stack calls.
pub const VECTOR: u8 = 0x84;

pub const CALL_INPUT_STATE: u64 = 0;
pub const CALL_GFX_MODE: u64 = 1;

/// Linux's `ENOSYS`, and the release's convention: negative errno in `rax`.
pub const ENOSYS: i64 = -38;

/// Packs the pointer state into the return register. Clamped rather than
/// wrapped: a coordinate past 65535 is a driver bug, and silently
/// truncating it would put the pointer somewhere it never was.
fn pack_pointer() -> i64 {
    let f = crate::ps2_aux::facts();
    let x = f.last.dx.max(i32::MIN).min(i32::MAX);
    let _ = (x, f.last.dy); // deltas are reported below, not absolute
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

/// The dispatcher, callable directly (and called directly by the boot
/// self-test, which is why the ABI logic is testable without a ring-3
/// program).
pub fn dispatch(nr: u64) -> i64 {
    match nr {
        CALL_INPUT_STATE => pack_pointer(),
        CALL_GFX_MODE => pack_gfx(),
        _ => ENOSYS,
    }
}

/// Installs the gate with `DPL=Ring3` (a ring-3 `int` to a DPL-0 gate would
/// fault on the gate's privilege, not on the handler).
///
/// The entry stub saves exactly the registers it may clobber, passes the
/// call number in `rdi` per the SysV ABI, and returns the result in the
/// caller's `rax`. It does NOT switch stacks: the CPU has already switched
/// to the TSS's ring-0 stack, which is where the stub runs.
pub fn install(idt: &mut InterruptDescriptorTable) {
    unsafe {
        idt[VECTOR as u8]
            .set_handler_addr(VirtAddr::new(service_entry as *const () as u64))
            .set_privilege_level(PrivilegeLevel::Ring3);
    }
}

/// The trap entry. `rax` carries the call number in and the result out; the
/// stub preserves every other register the dispatch could clobber, so the
/// interrupted program sees only its own `rax` change.
///
/// Alignment is handled by saving RSP in `r11` (already saved on the stack)
/// and rounding it down for the `call`, rather than by counting pushes: the
/// frame the CPU pushes differs between a ring-3 and a ring-0 caller, and a
/// pad that is right for one is wrong for the other. `and rsp, -16` is
/// correct for both, and `mov rsp, r11` restores the exact stack the pops
/// expect.
#[unsafe(naked)]
pub unsafe extern "C" fn service_entry() {
    naked_asm!(
        "push rcx",
        "push rdx",
        "push rsi",
        "push rdi",
        "push r8",
        "push r9",
        "push r10",
        "push r11",
        "mov r11, rsp",
        "and rsp, -16",
        "mov rdi, rax", // SysV first argument = the call number
        "call {dispatch}",
        "mov rsp, r11",
        "pop r11",
        "pop r10",
        "pop r9",
        "pop r8",
        "pop rdi",
        "pop rsi",
        "pop rdx",
        "pop rcx",
        "iretq",
        dispatch = sym service_dispatch_c,
    );
}

/// C half of the entry stub: `rax` was moved to `rdi` before the call.
extern "C" fn service_dispatch_c(nr: u64) -> i64 {
    dispatch(nr)
}

/// The boot proof for this surface. It checks the ABI's arithmetic and its
/// refusal, not the privilege transition: `dispatch` is called directly
/// here, because nothing in this kernel owns a user-space shell process to
/// call it through the gate yet. The gate's DPL and its `iretq` path are
/// the same construction as the kernel's proven `0x80`/`0x81` gates; what
/// remains unproven is a real ring-3 caller, and that is stated in the
/// transcript rather than implied.
pub fn self_test() -> bool {
    let gfx = dispatch(CALL_GFX_MODE);
    let gfx_ok = if let Some((w, h)) = crate::devices::framebuffer::bound_mode() {
        gfx == (((w as i64) << 32) | h as i64)
    } else {
        gfx == ENOSYS
    };

    let input = dispatch(CALL_INPUT_STATE);
    let (px, py) = crate::cursor::last_position();
    let input_ok = input == ((px.clamp(0, 0xffff) as i64)
        | (py.clamp(0, 0xffff) as i64) << 16
        | ((crate::ps2_aux::facts().last.buttons as i64 & 0xff) << 32));

    // Every call this kernel does not serve must be refused with the
    // named error, not answered with zeroes.
    let refused = dispatch(2) == ENOSYS
        && dispatch(10) == ENOSYS
        && dispatch(20) == ENOSYS
        && dispatch(u64::MAX) == ENOSYS;

    let ok = gfx_ok && input_ok && refused;
    crate::serial_println!(
        "  service-0x84: gfx/input calls -- GFX_MODE {}, INPUT_STATE {}, unported calls refused with -ENOSYS {} ; gate installed at DPL3, no ring-3 caller yet (no user-space shell owner) -- {}",
        if gfx_ok { "OK" } else { "FAILED" },
        if input_ok { "OK" } else { "FAILED" },
        if refused { "OK" } else { "FAILED" },
        if ok { "OK" } else { "FAILED" }
    );
    ok
}
