/*
 * prog_cross_all2_lib.s -- the SECOND provider in the dependency
 * chain of the recursive-DT_NEEDED proof (experimental::elf_dyn_deps_
 * demo). This object (a real `ld -shared -soname crosslib2.so.1 ...
 *
 * prog_cross_all2_lib.o userprog_cross_all_lib.elf` link, so the
 * DT_NEEDED crosslib.so.1 edge to the FIRST provider is RECORDED BY
 * THE LINKER, not typed by hand) exports nothing the kernel's proofs
 * consume; its role is structural:
 *
 *   dependent3 --DT_NEEDED--> crosslib2.so.1 (THIS object)
 *                     --DT_NEEDED--> crosslib.so.1 (the first
 *                                    provider)
 *
 * ...which is the two-hop chain the recursive loader must satisfy
 * dependency-first from the demo's in-memory catalog before
 * relocating dependent3.
 */

.section .data
.align 8
.globl cross2_marker
cross2_marker:
    .quad 0x2BAD2BABECAFEC2E

.section .text
.align 16
.globl cross2_noop
cross2_noop:
    ret
