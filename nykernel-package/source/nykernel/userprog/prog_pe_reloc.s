/*
 * prog_pe_reloc.s -- a minimal PE32+ (Windows x86_64) executable that
 * deliberately REQUIRES base relocation, built with GNU `as`/`ld`
 * (`ld -m i386pep --subsystem native -e _start`), same toolchain as
 * prog_pe.s.
 *
 * Why it needs relocation: `.data` holds `ptr`, a 64-bit ABSOLUTE
 * pointer initialized to the address of `slot` (also in `.data`). That
 * absolute value is recorded in the `.reloc` section as an
 * IMAGE_REL_BASED_DIR64 entry (confirmed with `objdump -p`, which
 * shows `Base Relocation Directory [.reloc]` and a `DIR64` entry).
 * The code loads `ptr` via RIP-relative addressing, then writes a
 * magic value THROUGH that pointer to `slot`.
 *
 * So: if the loader maps the image at an address other than its own
 * ImageBase (0x140000000) and does NOT apply the base relocation,
 * `ptr` still points at the ImageBase-relative (now unmapped) `slot`
 * and the write faults. If relocation is applied correctly, `ptr`
 * points at the runtime `slot` and the write succeeds -- which is
 * exactly the check experimental::pe_reloc_demo makes.
 */

.section .data
.globl ptr
ptr:
    .quad slot

.section .text
.global _start
_start:
    mov ptr(%rip), %rbx          /* rbx = relocated absolute pointer */
    movabs $0xFEEDFACECAFEBEEF, %rax
    mov %rax, (%rbx)             /* write the magic through it */
    int $0x84                    /* dedicated trap vector -> pe_reloc_demo::on_trap */
.hang:
    pause
    jmp .hang

.section .data
.globl slot
slot:
    .quad 0
