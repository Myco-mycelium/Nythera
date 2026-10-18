/*
 * prog_cross_all_lib.s -- the versioned, sonamed PROVIDER for the
 * full ELF-dynamic-features increment. Built with `ld -shared
 * -soname crosslib.so.1 --version-script=...` (see build.sh), so it
 * carries a REAL DT_SONAME and REAL version-definition tables
 * (DT_VERDEF via .gnu.version_d) -- exactly the shape a dependent's
 * DT_VERNEED/DT_VERSYM contract refers to.
 *
 *   export: cross_value -- data, VERSED (V1 build: VERS_1.0).
 *   export: dup_symbol  -- data, VERSED (same node).
 *   export: cross_fn    -- FUNCTION the dependent calls through a
 *     real .plt stub, whose JUMP_SLOT entry binds LAZILY (first-call
 *     trap) rather than eagerly.
 *
 * The V2 build (userprog_cross_all_lib2.elf) is the SAME source with
 * a VERS_2.0 version script: same SONAME, different version nodes --
 * the negative control for version matching.
 */

.section .data
.align 8
.globl cross_value
cross_value:
    .quad 0xACE5F00DD1CE5EED

.align 8
.globl dup_symbol
dup_symbol:
    .quad 0x00000000BAD0B100

.section .text
.align 16
.globl cross_fn
cross_fn:
    mov  $0xFA571AE5, %eax    /* the resolved call's observable result */
    ret
