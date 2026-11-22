/*
 * prog_win32_kernel32.s -- the PROVIDER half of the Win32 API surface
 * proof: a genuine Windows-format PE32+ DLL (`ld -m i386pep --shared
 * --enable-auto-import --image-base=0x190000000000`) named
 * `kernel32.dll`, exporting seven ordinary Win32 names:
 *
 *   tranche 1 (console):  GetStdHandle, WriteFile, GetTickCount, ExitProcess
 *   tranche 2 (files):    CreateFileA, ReadFile, CloseHandle
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
    movq $6, nyr_call_block+48(%rip)
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
