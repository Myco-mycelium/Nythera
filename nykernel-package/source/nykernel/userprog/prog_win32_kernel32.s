/*
 * prog_win32_kernel32.s -- the PROVIDER half of the Win32 API surface
 * proof: a genuine Windows-format PE32+ DLL (`ld -m i386pep --shared
 * --enable-auto-import --image-base=0x190000000000`) named
 * `kernel32.dll`, exporting seven ordinary Win32 names:
 *
 *   tranche 1 (console):  GetStdHandle, WriteFile, GetTickCount, ExitProcess
 *   tranche 2 (files):    CreateFileA, ReadFile, CloseHandle
 *   tranche 3 (errors):   GetLastError, SetLastError
 *   tranche 4 (startup):  GetCommandLineA, GetModuleHandleA
 *   tranche 5 (binding):  GetProcAddress
 *   tranche 9 (runtime):  LoadLibraryA, FreeLibrary
 *   tranche 10 (provenance): GetModuleFileNameA
 *   tranche 11 (memory):  GetProcessHeap, HeapAlloc, HeapFree
 *
 * Why GetProcAddress is the export that completes the startup pair: a CRT
 * (or any program that loads a library late) takes the HMODULE a name
 * resolved to and asks IT where a function lives. So this stub is the one
 * export whose answer is ABOUT the table it lives in: the kernel walks
 * the KERNEL's module table to the row for that handle, then walks THAT
 * row's real IMAGE_EXPORT_DIRECTORY -- this very DLL's .edata -- for the
 * name. Which means the stub's own body is two registers and a trap like
 * every other, while the answer it returns is a fact parsed out of the
 * file this stub is compiled into. A hand-kept name->address list in the
 * kernel would answer the same for a program that never imported
 * anything, and that is exactly the kind of answer this tranche exists to
 * rule out.
 *
 * Why the startup pair is its own tranche: a CRT does not begin with a
 * console write, it begins by asking the loader where it is and what it
 * was told on the command line. Both answers are POINTERS into state the
 * loader established before the first instruction ran, so neither can be
 * faked at call time -- the kernel has to have placed the string and
 * loaded the image already. They are the first two calls a real Windows
 * program makes, and the cheapest proof that this surface's answers are
 * about THIS process rather than about constants.
 *
 * Why the error pair is a separate tranche and not part of the file one:
 * the whole point of GetLastError is that it is the ONE place every failing
 * call in the process agrees to report itself, so it must be answerable by
 * the surface's own typed refusals -- not by the client inventing an
 * errno-style convention of its own. The two stubs are therefore thin: the
 * kernel owns the code cell and sets it whenever a dispatch is refused (and
 * when a call returns a Windows failure value instead of succeeding), the
 * stub only carries the argument in and the code out.
 *
 * SetLastError's body deliberately does NOT load the result into RAX: the
 * function returns void, so the trap's written-back 0 is not a value a
 * caller may read. GetLastError, which does return a DWORD, loads it.
 *
 * None of these bodies is a Microsoft implementation: each is a ring-3
 * TRAP STUB, which is exactly what a Wine-style shim in this kernel has
 * to be. The stub parks its Microsoft-x64 argument registers (RCX, RDX,
 * R8, R9) in the call block below, writes its own function index, and
 * issues `int $0x90`. The kernel's service handler reads the block,
 * performs the operation with its own authority, writes the result back
 * at +40, and returns to ring 3 at the instruction after the trap; the
 * stub then loads that result into RAX and `ret`s to its caller. The
 * caller never knows it left CPL 3.
 *
 * Why a MEMORY block rather than the trap frame's registers: an `int`
 * gate does not save the general-purpose registers (the CPU pushes only
 * RFLAGS/CS/RIP/RSP/SS on a privilege change), so a stub that wants to
 * pass arguments has to put them somewhere both sides can name. The
 * block's address is a fixed RVA in this image, pinned by the kernel
 * (`win32::BLOCK_RVA`) and checked with the magic below at load time --
 * the layout-drift rule: if the fixture's .data moves, the boot refuses
 * instead of reading whatever now lives at the old address.
 *
 * FIVE arguments, not four: CreateFileA and ReadFile both declare a fifth
 * parameter, and Microsoft's x64 convention passes it on the stack. At the
 * stub's entry `0(%rsp)` is the return address and `0x28(%rsp)` is the
 * first stack argument (after the 32-byte shadow space), so those two
 * stubs park it at +56 and the kernel can see it. The kernel refuses a
 * non-NULL `lpOverlapped` and a `dwCreationDisposition` other than
 * OPEN_EXISTING, which it could not do if the fifth argument were
 * silently dropped -- a shim that ignores an argument is a lie about what
 * the client asked for.
 *
 * Everything is RIP-relative, so this DLL carries no base relocations
 * and loads at its stated ImageBase (the loader refuses a PE that needs
 * rebasing; see `experimental::pe`).
 *
 * Block layout (all little-endian u64):
 *   +0   magic 0x4e59525733325f31 ("NYRW32_1")
 *   +8   arg0 (RCX) -- handle / nStdHandle / uExitCode / lpFileName
 *   +16  arg1 (RDX) -- lpBuffer / dwDesiredAccess
 *   +24  arg2 (R8)  -- nBytes / dwShareMode
 *   +32  arg3 (R9)  -- lpNumberOfBytes... / lpSecurityAttributes
 *   +40  result (written by the kernel, read by the stub into RAX)
 *   +48  function index (written by the stub; the kernel checks it is a
 *        real export AND that the trapping RIP lies inside that export)
 *   +56  arg4 -- the FIRST STACK ARGUMENT: dwCreationDisposition for
 *        CreateFileA, lpOverlapped for ReadFile. Only those two stubs
 *        store it; the others leave whatever was there.
 */
    .text

    .globl GetStdHandle
GetStdHandle:
    mov %rcx, nyr_call_block+8(%rip)
    movq $0, nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl WriteFile
WriteFile:
    mov %rcx, nyr_call_block+8(%rip)
    mov %rdx, nyr_call_block+16(%rip)
    mov %r8,  nyr_call_block+24(%rip)
    mov %r9,  nyr_call_block+32(%rip)
    movq $1,  nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl GetTickCount
GetTickCount:
    movq $2, nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl ExitProcess
ExitProcess:
    mov %rcx, nyr_call_block+8(%rip)
    movq $3, nyr_call_block+48(%rip)
    int $0x90
    /* ExitProcess does not return on Windows. v0 has no task teardown
       to switch to here (the demo's ring-3 program is the boot chain's
       own thread), so the kernel records the exit code and the stub
       returns; the client's next instruction is its completion trap,
       and the kernel asserts the recorded code. Stated as a scope
       limit in the demo's self-test line rather than hidden. */
    ret

    .globl CreateFileA
CreateFileA:
    mov %rcx, nyr_call_block+8(%rip)
    mov %rdx, nyr_call_block+16(%rip)
    mov %r8,  nyr_call_block+24(%rip)
    mov %r9,  nyr_call_block+32(%rip)
    mov 0x28(%rsp), %rax          /* 5th argument: dwCreationDisposition */
    mov %rax, nyr_call_block+56(%rip)
    movq $4,  nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl ReadFile
ReadFile:
    mov %rcx, nyr_call_block+8(%rip)
    mov %rdx, nyr_call_block+16(%rip)
    mov %r8,  nyr_call_block+24(%rip)
    mov %r9,  nyr_call_block+32(%rip)
    mov 0x28(%rsp), %rax          /* 5th argument: lpOverlapped */
    mov %rax, nyr_call_block+56(%rip)
    movq $5,  nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

        .globl CloseHandle
CloseHandle:
    mov %rcx, nyr_call_block+8(%rip)
    movq $6,  nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl GetLastError
GetLastError:
    movq $7,  nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl SetLastError
SetLastError:
    mov %rcx, nyr_call_block+8(%rip)
    movq $8,  nyr_call_block+48(%rip)
    int $0x90
    /* void: no result is read back (see the note above). */
    ret

    .globl GetCommandLineA
GetCommandLineA:
    /* No arguments. The loader placed the string before the process
       started, so this call is a pure read of that address: the kernel
       answers with the pointer it installed, and a surface that has
       none refuses rather than inventing one. */
    movq $9,  nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl GetModuleHandleA
GetModuleHandleA:
    /* LPCSTR lpModuleName in RCX. NULL asks for THIS image's base; a name
       is looked up in the loader's module table, case-insensitively (a
       module name is a file name), and a name no row answers to is
       ERROR_MOD_NOT_FOUND rather than a guess. */
    mov %rcx, nyr_call_block+8(%rip)
    movq $10, nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl GetProcAddress
GetProcAddress:
    /* HMODULE hModule in RCX, LPCSTR lpProcName in RDX. Both are parked:
       the handle selects a module ROW and the second argument selects an
       EXPORT inside it, and the kernel answers from that row's own export
       directory. Both of Windows' forms go through unchanged: a pointer is
       read as a name, and a value whose high word is zero is the ordinal
       form and is resolved through the file's declared OrdinalBase. */
    mov %rcx, nyr_call_block+8(%rip)
    mov %rdx, nyr_call_block+16(%rip)
    movq $11, nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl LoadLibraryA
LoadLibraryA:
    /* LPCSTR lpLibFileName in RCX. Windows answers an HMODULE (the base the
       loader mapped) or NULL. The interesting half is what the kernel does
       between those two: it reads the FILE named here out of the read-only
       volume, asks the loader to map it, and only then adds a module-table
       row -- which is what makes a forwarded export into that module
       resolvable at all. A name the volume does not have is Windows' NULL
       plus ERROR_MOD_NOT_FOUND, a failure value rather than a refused call. */
    mov %rcx, nyr_call_block+8(%rip)
    movq $12, nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl FreeLibrary
FreeLibrary:
    /* HMODULE hLibModule in RCX. Decrements the row's reference count and
       drops the row at zero, so a name that module exported stops resolving
       through any other row's forwarder -- the observable half of an unload
       in a kernel whose loader does not unmap pages yet. FALSE for a base
       that is not a live row. */
    mov %rcx, nyr_call_block+8(%rip)
    movq $13, nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl GetModuleFileNameA
GetModuleFileNameA:
    /* HMODULE hModule in RCX, LPSTR lpFilename in RDX, DWORD nSize in R8.
       All three are parked: the handle selects a ROW, the destination is
       validated as a write before anything touches it, and nSize (which
       counts the terminating NUL) decides whether the answer fits. Windows
       answers the byte count WITHOUT the NUL, or nSize with
       ERROR_INSUFFICIENT_BUFFER when the path had to be truncated -- so a
       caller that got a short buffer can tell, and never mistakes a cut
       path for the whole one. NULL means THIS process's image, which is why
       a program can ask where its own executable lives. */
    mov %rcx, nyr_call_block+8(%rip)
    mov %rdx, nyr_call_block+16(%rip)
    mov %r8,  nyr_call_block+24(%rip)
    movq $14, nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl GetProcessHeap
GetProcessHeap:
    /* No arguments. Answers the handle of this process's heap -- a value a
       CRT caches and passes to every allocation, so it must be stable for
       the life of the process. */
    movq $15, nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl HeapAlloc
HeapAlloc:
    /* HANDLE hHeap in RCX, DWORD dwFlags in RDX, SIZE_T dwBytes in R8. The
       result is a pointer ring 3 WRITES through, so the kernel carves it out
       of a region the client's own address space owns -- never out of the
       kernel heap, which ring 3 could not touch. */
    mov %rcx, nyr_call_block+8(%rip)
    mov %rdx, nyr_call_block+16(%rip)
    mov %r8,  nyr_call_block+24(%rip)
    movq $16, nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret

    .globl HeapFree
HeapFree:
    /* HANDLE hHeap in RCX, DWORD dwFlags in RDX, LPVOID lpMem in R8. FALSE for
       a pointer that is not a live block of this heap, so a double free is a
       failure a client can see rather than a corrupted allocator. */
    mov %rcx, nyr_call_block+8(%rip)
    mov %rdx, nyr_call_block+16(%rip)
    mov %r8,  nyr_call_block+24(%rip)
    movq $17, nyr_call_block+48(%rip)
    int $0x90
    mov nyr_call_block+40(%rip), %rax
    ret


    .data
    .p2align 4
nyr_call_block:
    .quad 0x4e59525733325f31   /* +0  magic "NYRW32_1" */
    .quad 0                    /* +8  arg0 */
    .quad 0                    /* +16 arg1 */
    .quad 0                    /* +24 arg2 */
    .quad 0                    /* +32 arg3 */
    .quad 0                    /* +40 result */
    .quad 0                    /* +48 function index */
    .quad 0                    /* +56 arg4 (first stack argument) */
