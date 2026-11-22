/*
 * prog_win32_app.s -- the CLIENT half of the Win32 API surface proof: a
 * genuine Windows-format PE32+ console executable
 * (`ld -m i386pep --subsystem console`) linked DIRECTLY against
 * `kernel32.dll` above, so ld synthesizes its .idata the ordinary way:
 * GetStdHandle, WriteFile, GetTickCount and ExitProcess each get an IAT
 * slot that the loader fills from kernel32.dll's real export directory.
 *
 * The program is the shape of a minimal console app, in Microsoft's own
 * x64 calling convention (arguments in RCX/RDX/R8/R9, return in RAX):
 *
 *   h = GetStdHandle(STD_OUTPUT_HANDLE)      // (DWORD)-11
 *   WriteFile(h, msg, len, &written, 0)
 *   GetTickCount(); GetTickCount()           // monotonic, must not go back
 *   ExitProcess(7)
 *   int $0x91                                // this chain link's completion
 *
 * Results land in the private slot page the kernel maps for this demo
 * (0x4d0000000000), read back after the completion trap: the WriteFile
 * return value, the two tick readings, the bytes-written out-parameter,
 * and the exit code the kernel recorded when ExitProcess was called.
 */
    .text
    .globl _start
_start:
    /* GetStdHandle(STD_OUTPUT_HANDLE) */
    mov $-11, %ecx
    call *__imp_GetStdHandle(%rip)
    mov %rax, %rbx

    /* WriteFile(h, msg, len, &written, NULL) */
    mov %rbx, %rcx
    lea winmsg(%rip), %rdx
    mov $winmsg_len, %r8d
    movabs $SLOT_WRITTEN, %r9
    call *__imp_WriteFile(%rip)
    movabs $SLOT_WRITE_RET, %rdi
    mov %rax, (%rdi)

    /* GetTickCount() twice: the second reading must never be lower */
    call *__imp_GetTickCount(%rip)
    movabs $SLOT_TICK1, %rdi
    mov %rax, (%rdi)
    call *__imp_GetTickCount(%rip)
    movabs $SLOT_TICK2, %rdi
    mov %rax, (%rdi)

    /* ExitProcess(7) */
    mov $7, %ecx
    call *__imp_ExitProcess(%rip)

    /* v0: ExitProcess returns (see the DLL's note); this is the link's
       completion trap, whose handler asserts the recorded exit code. */
    int $0x91
.hang:
    pause
    jmp .hang

    .data
winmsg:
    .asciz "nyrqis: hello from a Win32 console program\r\n"
    .set winmsg_len, . - winmsg - 1

/* The private report page, mapped USER_ACCESSIBLE|WRITABLE by the demo. */
    .set SLOT_BASE,    0x4d0000000000
    .set SLOT_WRITE_RET, SLOT_BASE + 0
    .set SLOT_WRITTEN,   SLOT_BASE + 8
    .set SLOT_TICK1,     SLOT_BASE + 16
    .set SLOT_TICK2,     SLOT_BASE + 24
