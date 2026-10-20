/*
 * prog_pe_bound_bad.s -- the BOUND-IMPORT increment's negative-control
 * carrier: a real native-subsystem PE32+ executable whose ONLY import
 * is provider A's crossfn (GNU ld synthesizes the .idata from the
 * direct dll link, exactly like the main client). Its entry point is
 * never entered: the whole purpose of this artifact is that
 * tools/patch_pe_bound.py composes into it a bound-import directory
 * whose single entry declares the provider's OWN TimeDateStamp (a
 * fresh binding) while its single IAT slot carries a deliberately
 * WRONG bound value -- and `pe::resolve_imports` must refuse it with
 * the typed `BoundValueCorrupt` rather than trust the stamp. The
 * artifact is `include_bytes!`-embedded and the refusal is checked
 * every boot (the demo's negative-control discipline, unchanged).
 *
 * The trap that looks like the other demos' is decoy-only: this image
 * never executes, and `0x8D` is taken.
 */
    .text
    .global _start
_start:
    mov __imp_crossfn(%rip), %rax
    int $0x8D
.hang:
    pause
    jmp .hang
