/*
 * prog_pe_pdata.s -- the PE32+ EXCEPTION-TABLE increment's fixture
 * client: a real, minimal PE32+ (Windows x86_64 native subsystem)
 * executable built entirely with GNU `as`/`ld`
 * (`ld -m i386pep --subsystem native -e _start`), the same shape as
 * prog_pe.s. This host's GNU ld emits no `.pdata`/exception directory
 * for a native-subsystem client (no SEH probing of any kind), so the
 * IMAGE_DIRECTORY_ENTRY_EXCEPTION surface the kernel's
 * `experimental::pe` loader walk proves gets COMPOSED from this
 * client's own section-table facts by tools/patch_pe_pdata.py -- a
 * real, checked on-disk composition, not simulated toolchain output.
 *
 * What the (ONE legal carrier's) body does: writes a magic value to a
 * fixed private address the demo mapped for it, then traps via
 * `int 0x8e` (a new vector, dedicated to
 * experimental::pe_exception_demo). The two negative-control carriers
 * (userprog_pe_pdata_mis.exe / _ver.exe) are loaded by the demo but
 * their entry point is NEVER entered: the odd property each carries
 * lives in the .idata-padding composition (an overlapping second
 * runtime-function entry; an UNWIND_INFO version nibble of 3), and
 * the kernel's load-time walk refuses it before any execution could
 * happen. All three carriers share this one assembly; only the
 * composed bytes differ.
 */

.section .text
.global _start
_start:
    movabs $0xC0DEDA7AC0DEDA7A, %rax
    /* The slot address: 0x5500_0010_0000 (+1 MiB from the private
     * subtree's base) -- ONE value, byte-identical to the demo driver's
     * PRIVATE_ADDR const (the digit-swap class that bit other demo
     * artifacts: '0x550001000000' is +16 MiB, a page nobody maps). */
    movabs $0x550000100000, %rbx
    mov %rax, (%rbx)
    int $0x8e
.hang:
    pause
    jmp .hang
    /* The composition splits .text's virtual size into TWO halves; an
     * even virtual size keeps both halves a whole multiple of 2 and
     * the patcher's sanity checks meaningful. `.balign 8` pads the
     * section to a multiple of 8 bytes. */
    .balign 8
