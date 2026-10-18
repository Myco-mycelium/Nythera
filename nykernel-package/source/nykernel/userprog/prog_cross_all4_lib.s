/*
 * prog_cross_all4_lib.s -- the FOURTH provider in the depth-3
 * catalog chain of experimental::elf_dyn_deps_demo (a real
 * `ld -shared -soname crosslib4.so.1 ...
 * prog_cross_all4_lib.o userprog_cross_all_lib.elf` link, so this
 * object's DT_NEEDED crosslib.so.1 edge to the FIRST provider is
 * RECORDED BY THE LINKER, not typed by hand).
 *
 * After the eighth-round depth-3 rebalance, the chain the recursive
 * loader walks is THREE catalog hops:
 *
 *   dependent3 --DT_NEEDED--> crosslib2.so.1   (provider B)
 *                          --DT_NEEDED--> crosslib3.so.1   (provider C)
 *                                     --DT_NEEDED--> crosslib4.so.1   (provider D, THIS object)
 *                                                --DT_NEEDED--> crosslib.so.1 (already in scope)
 *
 * ...so the dependent's load auto-loads THREE objects from the catalog
 * (B, C, D) before relocating -- the depth-3 proof. Like providers B
 * and C this object exports nothing the kernel's proofs consume; its
 * role is structural.
 */

.section .data
.align 8
.globl cross4_marker
cross4_marker:
    .quad 0x4E0C4E0C44444444

.section .text
.align 16
.globl cross4_noop
cross4_noop:
    ret
