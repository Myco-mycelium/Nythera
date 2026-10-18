//! The demo-chain STACK PARTITION: every chained demo's `on_trap`
//! continuation never returns, so without any intervention each
//! demo's kernel-side frames would ACCUMULATE on top of the previous
//! demos' frames on one kernel stack across the entire boot -- the
//! reason `memory::kernel_stack`'s default had to grow from 5 to 8
//! pages when the seventh demo joined the chain. This module fixes
//! the CAUSE instead of the budget: [`run_on_fresh_stack`] JUMPS the
//! current execution onto the ACTIVE `AddressSpace`'s own fresh
//! guarded RSP0 stack (the pages were mapped into the shared subtree
//! by `new_sharing_kernel`, so they are valid under the CR3 the demo
//! just activated), and the rest of that demo's kernel-side work --
//! including `load_tree` recursion depth, `Vec` churn, and the
//! `usermode` entry -- runs on a stack whose budget was sized for ONE
//! stage, not the accumulated chain.
//!
//! Uses the crate's established alignment conventions: `top` is
//! page-aligned, `call` pushes the return address so the callee sees
//! `rsp % 16 == 8`, exactly System V 'C' ABI entry state. The
//! abandoned caller stack is never touched again (its frames leak by
//! design, like every demo `mem::forget`) -- its page budget stays
//! allocated; it is not RE-ENTERED, so nothing else needs to be sent
//! to restore it.
//!
//! # Safety
//! The trampoline runs AFTER `space.activate()` (CR3 + TSS.RSP0 must
//! already point at this space and this stack) and `f` must fetch its
//! own working state through 'static slots (demo statics), since no
//! caller frame survives the switch.

/// Jumps to `f` on `space`'s own guarded kernel stack.
pub unsafe fn run_on_fresh_stack(
    space: &crate::experimental::address_space::AddressSpace,
    f: unsafe extern "C" fn() -> !,
) -> ! {
    let top = space.kernel_stack_top();
    core::arch::asm!(
        "mov rsp, {top}",
        "call {f}",
        top = in(reg) top,
        f = in(reg) (f as *const () as u64),
        options(noreturn)
    );
}
