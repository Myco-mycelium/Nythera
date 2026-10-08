/*
 * linker.ld -- links prog.s as a flat, non-PIE, statically-addressed
 * ELF64 executable. The load address matches
 * experimental::usermode::USER_CODE_ADDR -- not because the loader
 * requires that (experimental::elf::load reads p_vaddr from the file
 * and maps wherever it says), but because this proof doesn't yet
 * implement position-independent relocation, so the address the file
 * claims and the address it's actually mapped at need to agree. A
 * real loader supporting PIE executables is future work.
 */

ENTRY(_start)

SECTIONS
{
    . = 0x20000000000;

    .text :
    {
        *(.text)
    }

    /DISCARD/ :
    {
        *(.eh_frame)
        *(.comment)
        *(.note.*)
    }
}
