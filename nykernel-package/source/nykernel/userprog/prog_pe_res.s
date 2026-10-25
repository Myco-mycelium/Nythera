/*
 * prog_pe_res.s -- the PE32+ RESOURCE-DIRECTORY increment's fixture
 * client: a real, minimal PE32+ (Windows x86_64 native subsystem)
 * executable built entirely with GNU `as`/`ld`, the same shape as
 * prog_pe.s / prog_pe_pdata.s. This host's GNU ld emits no resource
 * directory (IMAGE_DIRECTORY_ENTRY_RESOURCE, DataDirectory[2]) for a
 * native-subsystem client, so the resource TREE the kernel's
 * `experimental::pe` loader walk proves gets COMPOSED from nothing but
 * this client's own section-table facts by tools/patch_pe_res.py -- a
 * real, checked on-disk composition, not simulated toolchain output.
 *
 * What the (ONE legal carrier's) body does: writes a magic value to a
 * fixed private address the demo mapped for it, then traps via
 * `int 0x8f` (a new vector, dedicated to
 * experimental::pe_resource_demo; 0x8E is the pdata demo's). The three
 * negative-control carriers (userprog_pe_res_bad.exe / _rname.exe /
 * _rmany.exe) are walked by the demo but their entry point is NEVER
 * entered: the odd property each carries lives in the .idata-padding
 * composition (a data entry whose bytes overrun the image; a named
 * entry with an empty length-prefixed string; an entry array over the
 * walk's cap), and the kernel's load-time walk refuses it before any
 * execution could happen. All four carriers share this one assembly;
 * only the composed bytes differ.
 */

.section .text
.global _start
_start:
    movabs $0xF00DFACEF00DFACE, %rax
    /* The slot address: 0x5800_0010_0000 (+1 MiB from the private
     * subtree's base) -- ONE value, byte-identical to the demo driver's
     * PRIVATE_ADDR const (the digit-swap class that bit other demo
     * artifacts: '0x580100000000' is a different PML4 subtree, not a
     * mapped slot). */
    movabs $0x580000100000, %rbx
    mov %rax, (%rbx)
    int $0x8f
.hang:
    pause
    jmp .hang
    /* The composition lays ~80 bytes of tree into .idata's padding; an
     * 8-byte-aligned section end keeps every composed u32/u16 itself
     * aligned (the walk refuses misaligned data entries). */
    .balign 8
