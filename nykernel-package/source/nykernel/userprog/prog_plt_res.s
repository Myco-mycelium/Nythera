/*
 * prog_plt_res.s -- the OWN-.plt GNU-protocol dependent for
 * experimental::elf_dyn_plt_demo: a real `ld -shared` object LINKED
 * against the versioned provider (so the linker records the DT_NEEDED
 * crosslib.so.1 edge, the VERS_1.0 requirement, and a REAL
 * linker-emitted .plt with its R_X86_64_JUMP_SLOT for cross_fn --
 * exactly the machinery the GNU lazy-binding protocol lives in).
 *
 * What makes this artifact distinct from every other dependent in
 * the tree: nothing, and that is the point. The FILE is an ordinary
 * `ld -shared` product; the increment is that the KERNEL arms the
 * object's OWN .plt/.got.plt (GOT[1] link-map cookie, GOT[2]
 * resolver thunk, JUMP_SLOT slots left at the linker's initial
 * plt+6 values) instead of emitting synthetic `int 0x88` trap
 * stubs, and the first `call cross_fn` flow here is:
 *
 *   call cross_fn@plt          (pushes this caller's return address)
 *     jmp *GOT[3]              (unbound: the linker's initial plt+6)
 *       push $idx              (PLT1's relocation index)
 *       jmp PLT0               (PLT0 = `push GOT[1]; jmp *GOT[2]`)
 *         push GOT[1]          (the link-map cookie -- the spill)
 *         jmp *GOT[2]          (the kernel-mapped resolver thunk)
 *           int 0x8B           (the PLT-resolve gate)
 *
 * ...and the kernel resolves, patches the REAL GOT slot, and guides
 * the return DIRECTLY into cross_fn (GNU _dl_runtime_resolve's
 * shape), whose own `ret` then returns to the caller below. Calls
 * two and three take the direct path through the now-patched slot.
 *
 * On its private data page it records cross_value (the versioned
 * GLOB_DAT regression), the three cross_fn results, and traps the
 * demo's report vector 0x8C.
 */

.section .text
.align 16
.globl plt_payload
plt_payload:
    movabs $0x6D000720000, %rdi            /* PRIVATE_ADDR (matches elf_dyn_plt_demo.rs) */
    mov   cross_value@GOTPCREL(%rip), %rax /* versioned GLOB_DAT: provider's cross_value */
    mov   (%rax), %rbx
    mov   %rbx, (%rdi)
    call  cross_fn                          /* PLT: GNU lazy protocol; first call traps 0x8B */
    mov   %eax, 8(%rdi)
    call  cross_fn                          /* direct path from here on */
    mov   %eax, 16(%rdi)
    call  cross_fn
    mov   %eax, 24(%rdi)
    int   $0x8C                            /* elf_dyn_plt_demo::TRAP_VECTOR */
.hang:
    pause
    jmp .hang
