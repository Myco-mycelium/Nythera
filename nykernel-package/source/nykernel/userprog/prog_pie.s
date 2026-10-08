/*
 * prog_pie.s -- a real position-independent executable (ET_DYN), not
 * hand-waved: `data_ptr` below is an absolute 8-byte pointer to
 * `target_value`, and since this binary's actual load address isn't
 * known until experimental::elf maps it, GNU ld emits a real
 * R_X86_64_RELATIVE relocation for it (verified with `readelf -r`
 * before writing any Rust to process it -- see PHASES.md). Without
 * that relocation being correctly applied at load time, `data_ptr`
 * would contain the file's as-linked value (based at 0), not the
 * real runtime address -- dereferencing it would read from the wrong
 * place entirely, not just "the wrong value at the right place."
 *
 * `lea data_ptr(%rip), %rax` itself needs no relocation (RIP-relative
 * addressing is inherently position-independent); it's specifically
 * the *absolute* pointer stored in `.data` that exercises the
 * relocation mechanism experimental::elf now implements.
 */

.section .text
.global _start
_start:
    lea data_ptr(%rip), %rax   /* rax = &data_ptr (needs no relocation) */
    mov (%rax), %rax           /* rax = *data_ptr -- only correct if the loader relocated it */
    mov (%rax), %rbx           /* rbx = *target_value */
    movabs $0x60000010000, %rcx
    mov %rbx, (%rcx)
    int $0x82
.hang:
    pause
    jmp .hang

.section .data
.align 8
target_value:
    .quad 0xFEEDFACE12345678
.align 8
data_ptr:
    .quad target_value
