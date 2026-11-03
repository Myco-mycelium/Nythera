/*
 * linker_svc84.ld -- links prog_svc84.s at its own load address
 * (0x6_0000_00000), distinct from every other demo's region (0x2... is
 * isolation_demo's, 0x3... isolated_tasks', 0x5... ring3_scheduler's,
 * 0x1... the kernel heap). A stray mapping mistake is then a visible fault
 * at an address nothing else uses, rather than a program that happens to
 * keep working inside another demo's pages.
 */

ENTRY(_start)

SECTIONS
{
    . = 0x60000000000;

    .text :
    {
        *(.text)
    }

    /DISCARD/ :
    {
        *(.eh_frame)
        *(.comment)
    }
}
