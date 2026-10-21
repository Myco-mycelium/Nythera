/*
 * prog_pe_delay.s -- the DELAY-LOAD increment's carrier body: a real,
 * no-import native-subsystem PE32+ executable (GNU ld output only --
 * no prov DLL on its link line at all), whose ENTRY POINT IS NEVER
 * ENTERED. tools/patch_pe_delay.py composes into RACH copy's .idata
 * in-block padding a real, MS-spec delay-load directory
 * ((grAttrs = dlattrRva, ONE descriptor + zero terminator): the GOOD
 * carrier (bound 0) declares the REAL provider with a named crossfn
 * import and an ordinal crossmark import (both hinted-at in its
 * unbound IAT/INT), the BAD carrier declares "nowhere.dll" (typed
 * MissingDelayDllImport refusal), and the MUT carrier declares the
 * provider with the SAME imports -- but its descriptor's
 * dwTimeStamp is provider stamp + 1, the OLD-BIND marker the kernel
 * must REFUSE (MalformedDelayImport), never silently honor and never
 * silently ignore either. All three files are byte-equal in their
 * ordinary toolchain bytes; ONLY the composed delay surface differs.
 * The kernel's resolve_delay_imports probes the mapped surfaces.
 */
    .text
    .global _start
_start:
    int $0x8D
.hang:
    pause
    jmp .hang
