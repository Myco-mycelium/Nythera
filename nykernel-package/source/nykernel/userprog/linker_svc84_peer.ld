/*
 * linker_svc84_peer.ld -- links prog_svc84_peer.s at 0x6_0000_00000.
 *
 * The peer is the SECOND ring-3 process of the 0x84 proof and has its OWN
 * address space (its task's CR3 is a PML4 of its own), so it is linked at
 * the SAME anchor as prog_svc84.s on purpose: identical virtual addresses
 * that resolve through different page tables to different frames. That is
 * the fact this slice exists to prove -- a page range per process is what
 * the two programs no longer need, because they cannot see each other's
 * mappings at all. (Until 2026-11-08 they shared one space and the peer
 * lived at 0x7..., which is what an anchor buys when there is only one
 * table.)
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
