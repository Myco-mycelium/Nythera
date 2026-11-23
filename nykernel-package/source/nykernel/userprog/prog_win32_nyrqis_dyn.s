/*
 * prog_win32_nyrqis_dyn.s -- the RUNTIME provider of the Win32 API surface
 * proof: a genuine Windows-format PE32+ DLL that this kernel's loader maps
 * for the first time only when the ring-3 client calls LoadLibraryA.
 *
 * Why this DLL is different from `nyrqis.dll`: that provider is embedded in
 * the kernel image and the loader registers it before ring 3 starts, so no
 * part of the module table's growth is observed. This one is a FILE on the
 * read-only volume (`C:\nyrqis\nyrqis_dyn.dll`, the same bytes, carried in
 * `win32_fs`); the surface reads it, hands it to the loader, and registers
 * the row it produces. A module table that only ever held loader-time
 * installations could not answer a question about it at all, because there
 * was no row to answer from.
 *
 * Why `NyrqisDynValue` is ordinary ring-3 code and not a trap stub: exactly
 * the reason `nyrqis.dll`'s exports are (see that file's header). An export
 * of a dynamically loaded module has no service gate behind it -- it is code
 * in a mapped image -- so the honest shape is a real body that runs entirely
 * in ring 3 and returns a value. The client reaches it only through the
 * address `GetProcAddress` answered, and that answer arrives through two
 * hops: `nyrqis.dll`'s forwarded export `NyrqisDyn`, then this module's own
 * row. Nothing the client linked against names it.
 *
 * The value 42 is deliberately not 1, 6 or 7 (the other fixtures' constants),
 * so a transcript line that says the client read 42 is evidence that THIS
 * body ran -- not that an earlier answer was reused.
 *
 * Everything is position-independent (`mov $imm, %eax` / `ret`) so the DLL
 * carries no base relocations and maps at its stated ImageBase; the loader
 * refuses a PE that would have to be rebased, which is also why a second
 * LoadLibraryA of this file cannot move it (see `win32_demo`'s note on
 * re-registration: this loader has no unmap, so the row is what comes and
 * goes, not the pages).
 */

    .text

    .globl NyrqisDynValue
NyrqisDynValue:
    mov $42, %eax
    ret
