/*
 * prog_cross_all3_lib.s -- the SECOND-HOP provider in the depth-2
 * catalog chain of experimental::elf_dyn_deps_demo (a real
 * `ld -shared -soname crosslib3.so.1 ...
 * prog_cross_all3_lib.o userprog_cross_all_lib.elf` link, so this
 * object's DT_NEEDED crosslib.so.1 edge to the FIRST provider is
 * RECORDED BY THE LINKER, not typed by hand).
 *
 * The chain the recursive loader walks is now TWO catalog hops:
 *
 *   dependent3 --DT_NEEDED--> crosslib2.so.1  (provider B)
 *                          --DT_NEEDED--> crosslib3.so.1  (provider C, THIS object)
 *                                     --DT_NEEDED--> crosslib.so.1 (already in scope)
 *
 * ...so the dependent's load auto-loads TWO objects from the catalog
 * (B and C) before relocating -- the depth-2 proof.
 */

.section .data
.align 8
.globl cross3_marker
cross3_marker:
    .quad 0x3333C0FE3333C0FE

.section .text
.align 16
.globl cross3_noop
cross3_noop:
    ret
