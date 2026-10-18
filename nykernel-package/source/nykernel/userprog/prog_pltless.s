/*
 * prog_pltless.s -- the PLT-LESS dependent for
 * experimental::elf_dyn_plt_demo's boundary proofs: a real
 * `ld -shared` object linked against the versioned provider that
 * carries a versioned GLOB_DAT (cross_value@GOTPCREL) but makes NO
 * PLT calls -- so its .rela.plt is ABSENT (no DT_JMPREL, no
 * DT_PLTRELSZ, no DT_PLTGOT). Two load-bearing facts rest on its
 * absence:
 *
 *   boundary 1: the GNU own-.plt arming mode REQUESTED on this
 *     artifact is a typed `MalformedDynamicTables` refusal (there is
 *     no .got.plt to arm; the loader refuses before any lazy work)
 *   boundary 2: the SAME artifact loads eagerly in the plain resolver
 *     mode -- GLOB_DAT resolved at load, GNU_RELRO revoked
 *     read-only, NO GNU arming side effects (registry/thunk pages
 *     untouched)
 *
 * ...i.e. the GNU protocol is opt-in per load, refusing cleanly
 * where its machinery does not exist, and the eager path remains the
 * default for objects that never had a PLT.
 */

.section .text
.align 16
.globl pltless_fn
pltless_fn:
    mov   cross_value@GOTPCREL(%rip), %rax /* the ONLY undefined reference:
                                             a versioned GLOB_DAT, resolved
                                             eagerly, never a PLT entry */
    mov   (%rax), %rax
    ret
