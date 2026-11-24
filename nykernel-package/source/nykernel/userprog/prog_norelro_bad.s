/*
 * prog_norelro_bad.s -- the negative control for the loader's no-RELRO arm.
 *
 * A real ELF64 shared object, linked WITHOUT `-z relro` (so it carries no
 * PT_GNU_RELRO at all), whose only dynamic relocation is an R_X86_64_GLOB_DAT
 * against a symbol the file does NOT define and the load supplies NO resolver
 * for. That entry is unsatisfiable.
 *
 * The loader's no-RELRO path applies relocations because its segments are
 * mapped exactly as declared; the honest outcome for an entry it cannot
 * satisfy is a REFUSED load (typed UndefinedSymbol), not a successful load
 * whose GOT slot silently stays zero. experimental::elf_dyn_demo loads this
 * file with no resolver and asserts the refusal.
 */

.section .text
.align 16
.globl norelro_bad_payload
norelro_bad_payload:
    mov  norelro_missing_symbol@GOTPCREL(%rip), %rax
    ret
