/*
 * prog_win32_nyrqis.s -- the SECOND provider of the Win32 API surface
 * proof, linked as a genuine Windows-format PE32+ DLL named
 * `nyrqis.dll` (`ld -m i386pep --shared --enable-auto-import
 * --image-base=0x190100000000`).
 *
 * Why a second provider exists at all: the previous tranche gave the
 * kernel a module table, but every row in it was either this process's
 * own image or `kernel32.dll` -- so a table that answered correctly
 * would look exactly like a single-provider table with a special case
 * for the client. This DLL is the evidence that the table is a TABLE:
 * two providers register as two rows, `GetModuleHandleA` answers a
 * different base for each name, and `GetProcAddress` resolves a name
 * out of the row it was handed and no other. That last property is the
 * one worth building a fixture for, because it is the property a
 * hand-kept name list in the kernel cannot fake: `kernel32.dll` does
 * not export `NyrqisBuild`, and this DLL does not export
 * `GetTickCount`, so each provider asked for the other's name must
 * answer `ERROR_PROC_NOT_FOUND` -- while the same ask against its own
 * row resolves.
 *
 * Why these exports are ORDINARY ring-3 code and not trap stubs: a
 * `kernel32.dll` stub exists to enter this kernel (`int $0x90`), and
 * the kernel's service gate accepts a trap only from `kernel32.dll`'s
 * `.text`. An export served by the loader's table has no such
 * restriction -- it is just code in a mapped image -- so the honest
 * shape for the second provider is a real function body that runs
 * entirely in ring 3 and returns a value. The client reaches it only
 * through the address `GetProcAddress` handed it: this program does not
 * import from `nyrqis.dll` at all, so there is no IAT slot, no link-time
 * address, and no way for the value to arrive except through the module
 * table. (The kernel32 export in the same boot still arrives through
 * the IAT as well, and the client compares the two paths; here there is
 * only the runtime one, which is the point.)
 *
 * Why two exports instead of one: a one-name export directory is not
 * strong evidence the table is being consulted -- a special case for
 * "the one name in the second DLL" would pass. Two names, one of which
 * the client asks for second, make the per-row walk do real work: the
 * name table, the ordinal table and the EAT all have to line up for the
 * second answer to be right.
 *
 * The bodies return DWORDs and follow Microsoft's x64 convention (no
 * arguments, result in EAX), so the values are constants of THIS FILE:
 * `NyrqisBuild` names the surface revision (6) and `NyrqisMode` names
 * the surface's one mode (1, console-only). Nothing here is a
 * Microsoft implementation, and nothing here is dispatched by the
 * kernel.
 *
 * Everything is position-independent (`mov $imm, %eax` / `ret`) so the
 * DLL carries no base relocations and loads at its stated ImageBase --
 * the loader refuses a PE that needs rebasing.
 */

    .text

    .globl NyrqisBuild
NyrqisBuild:
    mov $6, %eax
    ret

    .globl NyrqisMode
NyrqisMode:
    mov $1, %eax
    ret
