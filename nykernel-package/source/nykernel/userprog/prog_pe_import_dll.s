/*
 * prog_pe_import_dll.s -- the provider half of the PE32+ import
 * resolution proof: a genuine Windows-format DLL (PE32+, `ld -m i386pep
 * --shared --enable-auto-import`) whose OWN export table
 * (IMAGE_DIRECTORY_ENTRY_EXPORT) is the real thing: ld synthesizes the
 * .edata from the two global symbols below. The kernel resolves the
 * client's imports through this directory at boot (GetProcAddress
 * semantics); no synthetic registry values.
 *
 * crossfn: returns the fnmagic immediate in eax.
 * crossmark: a data export whose .quad is the crossball immediate.
 */
    .text
    .global crossfn
crossfn:
    mov fnmagic(%rip), %eax
    ret

    .data
    .global crossmark
crossmark:
    .quad 0xace5f00dd1ce5eed
fnmagic:
    .long 0xfa571ae5
