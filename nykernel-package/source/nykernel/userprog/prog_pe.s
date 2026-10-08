/*
 * prog_pe.s -- a real, minimal PE32+ (Windows x86_64 native
 * subsystem) executable, built entirely with GNU `as`/`ld`
 * (`ld -m i386pep --subsystem native -e _start`) -- no MSVC, no
 * MinGW runtime, no Windows toolchain of any kind. Confirmed a
 * genuine PE32+ file before any kernel code touched it: `file`
 * reports "PE32+ executable (native) x86-64", `objdump -p` shows a
 * real IMAGE_OPTIONAL_HEADER64 (Magic 0x20b) with ImageBase
 * 0x140000000 and zero base relocations -- meaning it must run
 * correctly at exactly that fixed address, the PE equivalent of an
 * ELF ET_EXEC file, and the right first target for
 * experimental::pe before attempting .reloc-based relocation.
 *
 * What it does: writes a magic value to a fixed private address, then
 * traps via `int 0x83` (a new vector, dedicated to
 * experimental::pe_demo, distinct from every other demo's vector)
 * so the kernel can verify the PE's own code actually executed.
 */

.section .text
.global _start
_start:
    movabs $0xFEEDC0DEFEEDC0DE, %rax
    movabs $0x80000010000, %rbx
    mov %rax, (%rbx)
    int $0x83
.hang:
    pause
    jmp .hang
