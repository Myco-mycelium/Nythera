/*
 * prog_multi_plt.s -- the MULTI-ENTRY GNU-protocol dependent for
 * experimental::elf_dyn_plt_demo (instance 3): a real `ld -shared`
 * object LINKED against TWO providers (crosslib.so.1 for the
 * regression facts and pltprov.so.1 for the two functions), which
 * makes the linker record a .rela.plt with TWO R_X86_64_JUMP_SLOT
 * entries (fn_a, fn_b) -- the GNU own-.plt resolver's first
 * multi-entry object:
 *
 *   call fn_a  -- first use of JUMP_SLOT A traps 0x8B (cookie, idx A)
 *   call fn_b  -- first use of JUMP_SLOT B traps 0x8B (cookie, idx B)
 *   call fn_b  -- direct path through the already-patched slot
 *
 * So ONE link-map cookie takes TWO independent first-use binds (the
 * resolver dispatches both by the cookie and resolves each by ITS
 OWN index), while every later call is direct. Kernel-side the
 * demo pins: both slots patched to their respective resolved
 * addresses, the cookie's bind count EXACTLY 2 (not 1, not 3), and
 * all three eax results correct.
 *
 * On its private data page it records fn_a and fn_b results, then
 * traps the demo's report vector 0x8C.
 */

.section .text
.align 16
.globl multi_payload
multi_payload:
    movabs $0x6D000760000, %rdi            /* PRIVATE3 (matches elf_dyn_plt_demo.rs) */
    call  fn_a                              /* JUMP_SLOT A: GNU lazy protocol, first use */
    mov   %eax, (%rdi)
    call  fn_b                              /* JUMP_SLOT B: GNU lazy protocol, first use */
    mov   %eax, 8(%rdi)
    call  fn_b                              /* direct path from here on */
    mov   %eax, 16(%rdi)
    int   $0x8C                            /* elf_dyn_plt_demo::TRAP_VECTOR */
.hang:
    pause
    jmp .hang
