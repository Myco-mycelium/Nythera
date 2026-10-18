/*
 * prog_pltprov.s -- a SECOND sonamed provider for
 * experimental::elf_dyn_plt_demo, exporting TWO distinguishable
 * functions (fn_a / fn_b) so the demo can prove the GNU own-.plt
 * protocol handles a MULTI-ENTRY PLT table: two JUMP_SLOT entries in
 * one object, each bound lazily on ITS OWN first call, each slot
 * patched independently. Deliberately NOT version-scripted
 * (unversioned UNDEF dependencies resolve through the same registry
 * rules) and built `ld -shared -soname pltprov.so.1` (see build.sh).
 *
 *   export: fn_a -- function, returns 0xA11CE0DE in %eax.
 *   export: fn_b -- function, returns 0xBB0B5E11 in %eax.
 *   export: prov_marker -- data, a distinct immediate so the demo
 *     can pin the RIGHT FILE at the RIGHT base after load.
 */

.section .data
.align 8
.globl prov_marker
prov_marker:
    .quad 0x0E11CEDE1E1E1E1E

.section .text
.align 16
.globl fn_a
fn_a:
    mov  $0xA11CE0DE, %eax
    ret

.align 16
.globl fn_b
fn_b:
    mov  $0xBB0B5E11, %eax
    ret
