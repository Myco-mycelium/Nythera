/*
 * linker_yield.ld -- links prog_yield.s at a load address distinct
 * from every other demo's (0x2... is isolation_demo's, 0x3... is
 * isolated_tasks', 0x1... is the kernel heap) purely so a stray
 * mistake mapping the wrong region would be visibly wrong (a mapping
 * failure or a fault at an unexpected address) rather than silently
 * landing inside another demo's region and maybe still "working" by
 * accident.
 */

ENTRY(_start)

SECTIONS
{
    . = 0x50000000000;

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
