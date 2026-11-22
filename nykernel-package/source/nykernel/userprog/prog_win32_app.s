/*
 * prog_win32_app.s -- the CLIENT half of the Win32 API surface proof: a
 * genuine Windows-format PE32+ console executable
 * (`ld -m i386pep --subsystem console`) linked DIRECTLY against
 * `kernel32.dll` above, so ld synthesizes its .idata the ordinary way:
 * all eleven exports get an IAT slot that the loader fills from
 * kernel32.dll's real export directory.
 *
 * The program is the shape of a minimal console app, in Microsoft's own
 * x64 calling convention (arguments in RCX/RDX/R8/R9, the fifth on the
 * stack after 32 bytes of shadow space, return in RAX):
 *
 *   tranche 1 -- console:
 *     h = GetStdHandle(STD_OUTPUT_HANDLE)          // (DWORD)-11
 *     WriteFile(h, msg, len, &written, 0)
 *     GetTickCount(); GetTickCount()               // monotonic
 *     ExitProcess(7)
 *
 *   tranche 2 -- files on the read-only C: volume:
 *     f1 = CreateFileA("C:\nyrqis\hello.txt", GENERIC_READ, FILE_SHARE_READ,
 *                      NULL, OPEN_EXISTING, 0, NULL)
 *     ReadFile(f1, buf, 5, &n1, NULL)               // "hello"
 *     ReadFile(f1, buf+5, 59, &n2, NULL)            // the rest, proving the
 *                                                   // cursor advanced
 *     ReadFile(f1, eof, 8, &n3, NULL)               // TRUE, 0 bytes: EOF
 *     WriteFile(h, buf, n1+n2, &written, 0)         // the bytes it read
 *     CloseHandle(f1)
 *     f2 = CreateFileA("C:\nyrqis\bytes.bin", ...)  // NOT text: 0x00..0xfe
 *     ReadFile(f2, bin, 8, &n4, NULL); CloseHandle(f2)
 *     f3 = CreateFileA("C:\nyrqis\empty.txt", ...)  // zero-length file
 *     ReadFile(f3, eof, 8, &n5, NULL)               // TRUE, 0 bytes
 *     CloseHandle(f3)
 *     f4 = CreateFileA("C:\nyrqis\hello.txt", ...)  // handle slot reused
 *     CloseHandle(f4); ReadFile(f4, x, 4, &n6, NULL) // FALSE: closed
 *     CloseHandle(f4)                                // FALSE: closed again
 *     CreateFileA("C:\nyrqis\missing.txt", ...)      // INVALID_HANDLE_VALUE
 *
 *   tranche 4 -- startup, before anything else:
 *     lpCmdLine = GetCommandLineA()              // the loader's string,
 *                                                // stable across calls
 *     (bytes at lpCmdLine == this program's own copy + NUL)  // 1
 *     hmod = GetModuleHandleA(NULL)              // THIS image's base
 *     ((char*)_start - hmod < SizeOfImage)       // 1: the handle names
 *                                                // the running image
 *
 *   tranche 3 -- why it failed:
 *     err = GetLastError()                          // ERROR_FILE_NOT_FOUND (2)
 *                                                   // -- after the miss above
 *     err = GetLastError()                          // after the closed-handle
 *                                                   // ReadFile: 6, and 6 again
 *                                                   // after the second close
 *     SetLastError(0x1234); GetLastError()          // 0x1234: the writer works
 *     CreateFileA(hello, GENERIC_WRITE, ...)        // refused:
 *                                                   // INVALID_HANDLE_VALUE, as
 *                                                   // Windows' prototype says
 *     (result == INVALID_HANDLE_VALUE)              // 1: the client's own
 *                                                   // handle test passes
 *       -> GetLastError()                           // ERROR_ACCESS_DENIED (5)
 *     GetTickCount(); GetLastError()                // 5 still: success does
 *                                                   // NOT clear the code
 *
 * Every result is written to the private slot page the kernel maps for
 * this demo (0x4d0000000000), which is zeroed before ring 3 runs, and the
 * completion trap (`int $0x91`) hands those bytes back to the kernel to
 * assert. A field left at zero therefore means "the program never got
 * there", never "it happened to be zero".
 */
    .text
    .globl _start
_start:
    /* 0x30 = 32 bytes of shadow space + 16 for up to two stack
       arguments, a multiple of 16 so every `call` stays aligned. */
    sub $0x30, %rsp

    /* ---- tranche 4: startup, the way a CRT does ---- */

    /* lpCmdLine = GetCommandLineA(). The loader placed the string before
       the first instruction ran, so this call only answers with its
       address -- the program neither owns nor frees it. */
    call *__imp_GetCommandLineA(%rip)
    mov %rax, %rbx
    movabs $SLOT_CMDLINE, %rdi
    mov %rax, (%rdi)

    /* Ask a second time: Windows answers the SAME pointer for the life of
       the process, which is why a CRT can cache it. */
    call *__imp_GetCommandLineA(%rip)
    movabs $SLOT_CMDLINE2, %rdi
    mov %rax, (%rdi)

    /* Compare the bytes at that pointer with this program's own copy,
       byte by byte, and require the NUL terminator too: a pointer to a
       truncated or unterminated string must not pass. */
    mov %rbx, %rsi
    lea cmdline_expected(%rip), %rdi
    mov $CMDLINE_LEN, %ecx
    xor %eax, %eax
1:
    movzbq (%rsi), %rdx
    movzbq (%rdi), %r8
    cmp %r8, %rdx
    jne 2f
    inc %rsi
    inc %rdi
    loop 1b
    cmpb $0, (%rsi)
    jne 2f
    mov $1, %eax
2:
    movabs $SLOT_CMDLINE_OK, %rdi
    mov %rax, (%rdi)

    /* hmod = GetModuleHandleA(NULL): the base address of THIS program's
       image, which is what a CRT stores before it walks its own headers
       for the entry point. */
    xor %ecx, %ecx
    call *__imp_GetModuleHandleA(%rip)
    movabs $SLOT_MODULE, %rdi
    mov %rax, (%rdi)

    /* The handle must name the image this code is running FROM: _start
       lies inside [hmod, hmod + SizeOfImage). A constant, a kernel
       address, or another module's base all fail that test. */
    lea _start(%rip), %rdx
    sub %rax, %rdx
    xor %ecx, %ecx
    cmp $IMAGE_SIZE, %rdx
    setb %cl
    movabs $SLOT_MODULE_OK, %rdi
    mov %rcx, (%rdi)

    /* ---- tranche 1: the console ---- */

    /* GetStdHandle(STD_OUTPUT_HANDLE) */
    mov $-11, %ecx
    call *__imp_GetStdHandle(%rip)
    mov %rax, %r12

    /* ---- tranche 2: the read-only volume ---- */

    /* f1 = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL,
                       OPEN_EXISTING, 0, NULL). Only EDX is set: the
       kernel must read dwDesiredAccess as the DWORD Windows declares
       it to be, not as the whole stale RDX. */
    lea path_hello(%rip), %rcx
    mov $0x80000000, %edx
    mov $1, %r8d
    xor %r9d, %r9d
    movq $3, 0x20(%rsp)
    call *__imp_CreateFileA(%rip)
    mov %rax, %rbx
    movabs $SLOT_FILE1, %rdi
    mov %rax, (%rdi)

    /* ReadFile(f1, buf, 5, &n1, NULL) -- the first five bytes */
    mov %rbx, %rcx
    movabs $SLOT_BUF, %rdx
    mov $5, %r8d
    movabs $SLOT_N1, %r9
    movq $0, 0x20(%rsp)
    call *__imp_ReadFile(%rip)
    movabs $SLOT_R1, %rdi
    mov %rax, (%rdi)

    /* ReadFile(f1, buf+5, 59, &n2, NULL) -- the rest, same handle */
    mov %rbx, %rcx
    movabs $SLOT_BUF+5, %rdx
    mov $59, %r8d
    movabs $SLOT_N2, %r9
    movq $0, 0x20(%rsp)
    call *__imp_ReadFile(%rip)
    movabs $SLOT_R2, %rdi
    mov %rax, (%rdi)

    /* ReadFile(f1, eof, 8, &n3, NULL) -- past the end: TRUE, 0 bytes */
    mov %rbx, %rcx
    movabs $SLOT_EOFBUF, %rdx
    mov $8, %r8d
    movabs $SLOT_N3, %r9
    movq $0, 0x20(%rsp)
    call *__imp_ReadFile(%rip)
    movabs $SLOT_R3, %rdi
    mov %rax, (%rdi)

    /* WriteFile(h, buf, n1 + n2, &written, NULL): the file's own bytes,
       with the length the two ReadFile calls reported. */
    movabs $SLOT_N1, %rdi
    mov (%rdi), %r8
    movabs $SLOT_N2, %rdi
    add (%rdi), %r8
    mov %r12, %rcx
    movabs $SLOT_BUF, %rdx
    movabs $SLOT_WRITTEN, %r9
    movq $0, 0x20(%rsp)
    call *__imp_WriteFile(%rip)
    movabs $SLOT_WRITE_RET, %rdi
    mov %rax, (%rdi)

    /* CloseHandle(f1) -- releases slot 0 */
    mov %rbx, %rcx
    call *__imp_CloseHandle(%rip)
    movabs $SLOT_CLOSE1, %rdi
    mov %rax, (%rdi)

    /* f2 = CreateFileA(bytes.bin) -- must reuse the freed handle value */
    lea path_bin(%rip), %rcx
    mov $0x80000000, %edx
    mov $1, %r8d
    xor %r9d, %r9d
    movq $3, 0x20(%rsp)
    call *__imp_CreateFileA(%rip)
    mov %rax, %rbx
    movabs $SLOT_FILE2, %rdi
    mov %rax, (%rdi)

    /* ReadFile(f2, bin, 8, &n4, NULL) -- eight raw bytes, not text */
    mov %rbx, %rcx
    movabs $SLOT_BIN, %rdx
    mov $8, %r8d
    movabs $SLOT_N4, %r9
    movq $0, 0x20(%rsp)
    call *__imp_ReadFile(%rip)
    movabs $SLOT_R4, %rdi
    mov %rax, (%rdi)

    mov %rbx, %rcx
    call *__imp_CloseHandle(%rip)
    movabs $SLOT_CLOSE2, %rdi
    mov %rax, (%rdi)

    /* f3 = CreateFileA(empty.txt) -- a zero-length file */
    lea path_empty(%rip), %rcx
    mov $0x80000000, %edx
    mov $1, %r8d
    xor %r9d, %r9d
    movq $3, 0x20(%rsp)
    call *__imp_CreateFileA(%rip)
    mov %rax, %rbx
    movabs $SLOT_FILE3, %rdi
    mov %rax, (%rdi)

    /* ReadFile(f3, eof, 8, &n5, NULL) -- TRUE with zero bytes */
    mov %rbx, %rcx
    movabs $SLOT_EOFBUF, %rdx
    mov $8, %r8d
    movabs $SLOT_N5, %r9
    movq $0, 0x20(%rsp)
    call *__imp_ReadFile(%rip)
    movabs $SLOT_R5, %rdi
    mov %rax, (%rdi)

    mov %rbx, %rcx
    call *__imp_CloseHandle(%rip)
    movabs $SLOT_CLOSE3, %rdi
    mov %rax, (%rdi)

    /* f4 = CreateFileA(hello.txt) again */
    lea path_hello(%rip), %rcx
    mov $0x80000000, %edx
    mov $1, %r8d
    xor %r9d, %r9d
    movq $3, 0x20(%rsp)
    call *__imp_CreateFileA(%rip)
    mov %rax, %rbx
    movabs $SLOT_FILE4, %rdi
    mov %rax, (%rdi)

    /* CloseHandle(f4), then use it: both must FAIL */
    mov %rbx, %rcx
    call *__imp_CloseHandle(%rip)
    movabs $SLOT_CLOSE4, %rdi
    mov %rax, (%rdi)

    mov %rbx, %rcx
    movabs $SLOT_BUF2, %rdx
    mov $4, %r8d
    movabs $SLOT_N6, %r9
    movq $0, 0x20(%rsp)
    call *__imp_ReadFile(%rip)
    movabs $SLOT_R6, %rdi
    mov %rax, (%rdi)

    /* Ask why ReadFile failed on the closed handle, BEFORE the second close
       overwrites it: the code must name the condition the surface refused. */
    call *__imp_GetLastError(%rip)
    movabs $SLOT_ERR_CLOSED_READ, %rdi
    mov %rax, (%rdi)

    mov %rbx, %rcx
    call *__imp_CloseHandle(%rip)
    movabs $SLOT_CLOSE4B, %rdi
    mov %rax, (%rdi)

    call *__imp_GetLastError(%rip)
    movabs $SLOT_ERR_CLOSED_CLOSE, %rdi
    mov %rax, (%rdi)

    /* CreateFileA(missing.txt) -- INVALID_HANDLE_VALUE, not an error */
    lea path_missing(%rip), %rcx
    mov $0x80000000, %edx
    mov $1, %r8d
    xor %r9d, %r9d
    movq $3, 0x20(%rsp)
    call *__imp_CreateFileA(%rip)
    movabs $SLOT_FILE5, %rdi
    mov %rax, (%rdi)

    /* ---- tranche 3: why it failed ---- */

    /* The sentinel alone does not say what went wrong: ask. */
    call *__imp_GetLastError(%rip)
    movabs $SLOT_ERR_MISSING, %rdi
    mov %rax, (%rdi)

    /* SetLastError is the writer half; without it the reader is a log. Only
       ECX is set, like every other DWORD argument here. */
    mov $0x1234, %ecx
    call *__imp_SetLastError(%rip)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_ERR_SET, %rdi
    mov %rax, (%rdi)

    /* A REFUSED call (GENERIC_WRITE against a read-only volume): FALSE, and
       an explainable code. This one is a real refusal, not a failure value. */
    lea path_hello(%rip), %rcx
    mov $0x40000000, %edx
    mov $1, %r8d
    xor %r9d, %r9d
    movq $3, 0x20(%rsp)
    call *__imp_CreateFileA(%rip)
    movabs $SLOT_FILE6, %rdi
    mov %rax, (%rdi)

    /* A HANDLE-returning call fails with INVALID_HANDLE_VALUE (all ones),
       never 0 -- so the test a Windows client actually writes is a compare
       against the sentinel, and this refusal must pass it. The result is
       still live in RAX here. */
    xor %edx, %edx
    cmpq $-1, %rax
    sete %dl
    movabs $SLOT_HANDLE_TEST, %rdi
    mov %rdx, (%rdi)

    call *__imp_GetLastError(%rip)
    movabs $SLOT_ERR_ACCESS, %rdi
    mov %rax, (%rdi)

    /* A successful call must leave the code alone, as Windows does: the code
       is meaningful only right after a failure, and clearing it on success
       would hide a client that checked in the wrong place. */
    call *__imp_GetTickCount(%rip)
    movabs $SLOT_TICK1, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_ERR_KEPT, %rdi
    mov %rax, (%rdi)

    /* ---- tranche 1 again: the clock, then exit ---- */

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
path_hello:
    .asciz "C:\\nyrqis\\hello.txt"
path_bin:
    .asciz "C:\\nyrqis\\bytes.bin"
path_empty:
    .asciz "C:\\nyrqis\\empty.txt"
path_missing:
    .asciz "C:\\nyrqis\\missing.txt"
/* This program's own copy of the command line the kernel's loader places
   for it: the client compares the bytes at GetCommandLineA's pointer
   against these, so the proof is about the STRING, not about a non-NULL
   pointer. */
cmdline_expected:
    .asciz "C:\\nyrqis\\win32_console.exe"
    .set CMDLINE_LEN, . - cmdline_expected - 1
/* The SizeOfImage the link gives this executable (build.sh pins the same
   0x4000 from objdump -p), used by the module-handle test above. */
    .set IMAGE_SIZE, 0x4000

/* The private report page, mapped USER_ACCESSIBLE|WRITABLE by the demo and
   zeroed before ring 3. Every field below is written by this program or by
   the kernel through an out-parameter; a zero means "never reached". */
    .set SLOT_BASE,     0x4d0000000000
    .set SLOT_WRITE_RET, SLOT_BASE + 0
    .set SLOT_WRITTEN,   SLOT_BASE + 8
    .set SLOT_TICK1,     SLOT_BASE + 16
    .set SLOT_TICK2,     SLOT_BASE + 24
    .set SLOT_FILE1,     SLOT_BASE + 32
    .set SLOT_R1,        SLOT_BASE + 40
    .set SLOT_N1,        SLOT_BASE + 48
    .set SLOT_R2,        SLOT_BASE + 56
    .set SLOT_N2,        SLOT_BASE + 64
    .set SLOT_R3,        SLOT_BASE + 72
    .set SLOT_N3,        SLOT_BASE + 80
    .set SLOT_CLOSE1,    SLOT_BASE + 88
    .set SLOT_FILE2,     SLOT_BASE + 96
    .set SLOT_R4,        SLOT_BASE + 104
    .set SLOT_N4,        SLOT_BASE + 112
    .set SLOT_CLOSE2,    SLOT_BASE + 120
    .set SLOT_FILE3,     SLOT_BASE + 128
    .set SLOT_R5,        SLOT_BASE + 136
    .set SLOT_N5,        SLOT_BASE + 144
    .set SLOT_CLOSE3,    SLOT_BASE + 152
    .set SLOT_FILE4,     SLOT_BASE + 160
    .set SLOT_CLOSE4,    SLOT_BASE + 168
    .set SLOT_R6,        SLOT_BASE + 176
    .set SLOT_N6,        SLOT_BASE + 184
    .set SLOT_CLOSE4B,   SLOT_BASE + 192
    .set SLOT_FILE5,     SLOT_BASE + 200
/* The error-code fields live at 0x210.., ABOVE the four read buffers
   (0x100 buf, 0x140 EOF probe, 0x180 closed-handle probe, 0x200 binary
   bytes) and below the 0x300 area the kernel's own controls use. The
   first cut of this tranche numbered them from +208 and the last one
   landed on 0x100, i.e. on top of the buffer holding the file bytes
   this program writes to the console -- the transcript then showed a
   correct console message and a self-test mismatch on the read-back,
   because the page had been overwritten after the write. Layout stated
   here, and checked by the kernel's slot pins. */
    .set SLOT_ERR_CLOSED_READ,  SLOT_BASE + 0x210
    .set SLOT_ERR_CLOSED_CLOSE, SLOT_BASE + 0x218
    .set SLOT_ERR_MISSING,      SLOT_BASE + 0x220
    .set SLOT_ERR_SET,          SLOT_BASE + 0x228
    .set SLOT_FILE6,            SLOT_BASE + 0x230
    .set SLOT_ERR_ACCESS,       SLOT_BASE + 0x238
    .set SLOT_ERR_KEPT,         SLOT_BASE + 0x240
    .set SLOT_HANDLE_TEST,      SLOT_BASE + 0x248 /* HANDLE sentinel compare */
/* tranche 4: the startup pair's answers. The command-line STRING itself
   lives at +0x2a0, placed there by the kernel's loader-side install and
   BELOW the 0x300 area the kernel's own controls write paths into. */
    .set SLOT_CMDLINE,          SLOT_BASE + 0x250 /* first call's pointer */
    .set SLOT_CMDLINE2,         SLOT_BASE + 0x258 /* second call's pointer */
    .set SLOT_CMDLINE_OK,       SLOT_BASE + 0x260 /* bytes + NUL matched */
    .set SLOT_MODULE,           SLOT_BASE + 0x268 /* GetModuleHandleA(NULL) */
    .set SLOT_MODULE_OK,        SLOT_BASE + 0x270 /* handle names this image */
    .set SLOT_CMDLINE_TEXT,     SLOT_BASE + 0x2a0
    .set SLOT_BUF,       SLOT_BASE + 0x100
    .set SLOT_EOFBUF,    SLOT_BASE + 0x140
    .set SLOT_BUF2,      SLOT_BASE + 0x180
    .set SLOT_BIN,       SLOT_BASE + 0x200
