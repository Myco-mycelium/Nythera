/*
 * linker_svc84_peer.ld -- links prog_svc84_peer.s at 0x7_0000_00000.
 *
 * The peer is the SECOND ring-3 process of the 0x84 proof and shares the
 * first program's address space (this tree has no per-process address
 * space yet), so it needs a page range of its own INSIDE that space: 0x6...
 * belongs to prog_svc84.s (code 0x600...0000, results 0x600...10000, stack
 * +0x20000, shared region +0x30000), and 0x7... is the next free 40-bit
 * region. Two programs at one load address would be a proof of one program
 * with a second name.
 */

ENTRY(_start)

SECTIONS
{
    . = 0x70000000000;

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
