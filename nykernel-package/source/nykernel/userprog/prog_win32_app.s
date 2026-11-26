/*
 * prog_win32_app.s -- the CLIENT half of the Win32 API surface proof: a
 * genuine Windows-format PE32+ console executable
 * (`ld -m i386pep --subsystem console`) linked DIRECTLY against
 * `kernel32.dll` above, so ld synthesizes its .idata the ordinary way:
 * all twelve exports get an IAT slot that the loader fills from
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
 *   tranche 5 -- binding an import AT RUNTIME:
 *     hmod32 = GetModuleHandleA("kernel32.dll")   // a NAMED module now
 *                                                // resolves
 *     GetModuleHandleA("KERNEL32.DLL")           // the same base: module
 *                                                // names are case-blind
 *     pfn = GetProcAddress(hmod32, "GetTickCount")
 *     (pfn == __imp_GetTickCount)                // 1: the runtime answer
 *                                                // is the loader's answer
 *     pfn()                                       // callable through the
 *                                                // pointer it returned
 *     GetModuleHandleA("nope.dll")               // 0, ERROR_MOD_NOT_FOUND
 *     GetProcAddress(hmod32, "NoSuchExport")     // 0, ERROR_PROC_NOT_FOUND
 *
 *   tranche 6 -- a SECOND provider, and rows that are per-module:
 *     hnyr = GetModuleHandleA("nyrqis.dll")       // a base this program never
 *                                                // linked against, and not
 *                                                // kernel32's
 *     pfn = GetProcAddress(hnyr, "NyrqisBuild")   // no IAT slot exists for
 *     pfn()                                       // this name: 6, from code
 *                                                // reached only through the
 *                                                // answer
 *     GetProcAddress(hnyr, "NyrqisMode")();        // 1, the row's 2nd export
 *     GetProcAddress(hmod32, "NyrqisBuild")       // 0, ERROR_PROC_NOT_FOUND:
 *                                                // that name is in the OTHER
 *                                                // row
 *     GetProcAddress(hnyr, "GetTickCount")        // 0, ERROR_PROC_NOT_FOUND:
 *                                                // the other direction
 *
 *   tranche 7 -- Windows' ORDINAL form of GetProcAddress:
 *     GetProcAddress(hnyr, (LPCSTR)11)            // nyrqis.dll declares
 *                                                 // OrdinalBase 10, so its
 *                                                 // second EAT slot is
 *                                                 // ordinal 11, not 2
 *     (that answer == the NAME answer)            // 1: both forms name the
 *                                                 // same export of the file
 *     pfn()                                       // 1, called through it
 *     GetProcAddress(hnyr, (LPCSTR)9)             // 0, below the base:
 *                                                 // ERROR_PROC_NOT_FOUND
 *     GetProcAddress(hnyr, (LPCSTR)15)            // 0, past the last slot
 *                                                 // (five EAT slots now --
 *                                                 // two functions and three
 *                                                 // forwarders): ERROR_PROC_NOT_FOUND
 *
 *   tranche 8 -- a FORWARDED export, chased to its real module:
 *     GetProcAddress(hnyr, "NyrqisTick")          // nyrqis.dll's EAT slot is
 *                                                 // the STRING
 *                                                 // kernel32.dll.GetTickCount,
 *                                                 // not code
 *     (that answer == GetProcAddress(hmod32,
 *                        "GetTickCount"))        // 1: the chase landed in
 *                                                 // the NAMED module, whose
 *                                                 // row answered
 *     pfn()                                       // a live tick: the address
 *                                                 // is kernel32's own stub
 *     GetProcAddress(hnyr, "NyrqisGhost")         // 0, ERROR_PROC_NOT_FOUND:
 *                                                 // a forwarder to an export
 *                                                 // the target lacks
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
 *   tranche 11 -- the process heap, the first memory this program owns:
 *     h = GetProcessHeap()                        // a stable handle
 *     p1 = HeapAlloc(h, 0, 64)                     // 16-aligned, in the
 *                                                  // kernel-registered region
 *     p2 = HeapAlloc(h, HEAP_ZERO_MEMORY, 32)      // distinct, zeroed
 *     p1[0] = 0x41; p1[0] == 0x41                   // 1: writable
 *     HeapFree(h, 0, p1)                            // 1
 *     HeapFree(h, 0, p1)                            // 0, ERROR_INVALID_ADDRESS
 *     HeapAlloc(h, 0, 16) == p1                     // 1: the block was reused
 *     HeapAlloc(0x1234, 0, 16)                      // 0, ERROR_INVALID_HANDLE
 *     HeapAlloc(h, 4, 16)                            // 0, ERROR_INVALID_PARAMETER
 *     HeapAlloc(h, 0, 1<<20)                         // 0, ERROR_NOT_ENOUGH_MEMORY
 *     HeapFree(h, 0, 0x1234)                         // 0, ERROR_INVALID_ADDRESS
 *   tranche 13 -- a file's size, and seeking within it:
 *     f7 = CreateFileA("C:\\nyrqis\\hello.txt", ...)  // a FRESH handle: its
 *                                                      // cursor starts at 0
 *     GetFileSize(f7, &high) == 33                    // from the volume's
 *                                                     // entry; high written 0
 *     SetFilePointer(f7, 3, NULL, FILE_BEGIN) == 3    // absolute
 *     ReadFile(f7, a, 4, &n, NULL)                    // bytes 3..6: "lo f"
 *     SetFilePointer(f7, 0, NULL, FILE_CURRENT) == 7  // the cursor the READ
 *                                                     // advanced: shared state
 *     SetFilePointer(f7, -4, NULL, FILE_END) == 29    // SIGNED, from the end
 *     ReadFile(f7, b, 4, &n, NULL)                    // "me\r\n"
 *     SetFilePointer(f7, -100, NULL, FILE_BEGIN)      // INVALID_SET_FILE_
 *       -> GetLastError()                             // POINTER, then
 *                                                     // ERROR_NEGATIVE_SEEK (131)
 *     SetFilePointer(f7, 0, NULL, FILE_CURRENT) == 33 // the refused seek left
 *                                                     // the cursor alone
 *     SetFilePointer(f7, 0, NULL, 7)                  // refused: a move method
 *       -> GetLastError()                             // that does not exist (87)
 *     GetFileSize(STDOUT_HANDLE, NULL)                // refused: not a file (6)
 *     CloseHandle(f7)
 *
 *   tranche 12 -- the heap's size and reallocation:
 *     HeapSize(h, 0, p2) == 32                       // the block's size
 *     HeapReAlloc(h, 0, p2, 48) -> p4 != p2           // moved, byte kept
 *     HeapSize(h, 0, p4) == 128                      // the reused block's size
 *     HeapReAlloc(h, 0, p4, 16) == p4                 // shrink: same pointer
 *     HeapReAlloc(h, IN_PLACE_ONLY, p4, 4096)         // 0, NOT_ENOUGH_MEMORY
 *     HeapSize(h, 0, p2)                              // 0, INVALID_ADDRESS
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

    /* ---- tranche 5: binding an import at runtime ---- */

    /* hmod32 = GetModuleHandleA("kernel32.dll"): a NAMED module. The
       previous tranche could only refuse this; now the loader's module
       table answers it, and the answer is a handle this program can use. */
    lea name_kernel32(%rip), %rcx
    call *__imp_GetModuleHandleA(%rip)
    mov %rax, %rbx
    movabs $SLOT_NAMED_MODULE, %rdi
    mov %rax, (%rdi)

    /* The SAME module in another case: Windows compares module names
       case-insensitively, because a module name is a file name. */
    lea name_kernel32_upper(%rip), %rcx
    call *__imp_GetModuleHandleA(%rip)
    movabs $SLOT_NAMED_MODULE2, %rdi
    mov %rax, (%rdi)

    /* pfn = GetProcAddress(hmod32, "GetTickCount"): the address the
       provider's OWN export directory names, found at runtime rather than
       patched in by the loader. */
    mov %rbx, %rcx
    lea proc_tick(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_PROC_TICK, %rdi
    mov %rax, (%rdi)

    /* It must be the very address the loader wrote into this program's IAT.
       Two independent paths to one export, and the program compares them:
       a kernel-side name list would have to agree with the EAT to pass. */
    mov __imp_GetTickCount(%rip), %rdx
    xor %ecx, %ecx
    cmp %rdx, %rax
    sete %cl
    movabs $SLOT_PROC_TICK_SAME, %rdi
    mov %rcx, (%rdi)

    /* And it must be CALLABLE: call through the pointer the surface just
       handed over, not through the IAT slot. */
    movabs $SLOT_PROC_TICK, %rdi
    mov (%rdi), %rax
    call *%rax
    movabs $SLOT_PROC_TICK_VALUE, %rdi
    mov %rax, (%rdi)

    /* Negative: a module that is not loaded. NULL, and a code that says
       so -- not a guess at some other image's base. */
    lea name_absent(%rip), %rcx
    call *__imp_GetModuleHandleA(%rip)
    movabs $SLOT_NAMED_NONE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_NAMED_NONE_ERR, %rdi
    mov %rax, (%rdi)

    /* Negative: a name that module does not export. NULL, and
       ERROR_PROC_NOT_FOUND -- a different code from the one above, so a
       client can tell "wrong library" from "wrong function". */
    mov %rbx, %rcx
    lea proc_absent(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_PROC_NONE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_PROC_NONE_ERR, %rdi
    mov %rax, (%rdi)

    /* ---- tranche 6: a SECOND provider, and per-module rows ---- */

    /* hnyr = GetModuleHandleA("nyrqis.dll"). This program has NO import
       descriptor for that DLL, so the base can only come out of the
       loader's module table -- and it must not be kernel32's base. */
    lea name_nyrqis(%rip), %rcx
    call *__imp_GetModuleHandleA(%rip)
    mov %rax, %rbp
    movabs $SLOT_NAMED_SECOND, %rdi
    mov %rax, (%rdi)

    /* pfn = GetProcAddress(hnyr, "NyrqisBuild"), then CALL it. There is no
       IAT slot for this name anywhere in this image, so the pointer the
       surface answers is the ONLY route to the function -- and it runs in
       ring 3, returning 6. (RBX still holds hmod32 throughout: the stubs and
       the second provider's bodies leave it alone.) */
    mov %rbp, %rcx
    lea proc_build(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_SECOND_BUILD, %rdi
    mov %rax, (%rdi)
    mov (%rdi), %rax
    call *%rax
    movabs $SLOT_SECOND_BUILD_VALUE, %rdi
    mov %rax, (%rdi)

    /* The SAME row's second export: another EAT entry, and a second name
       the client has never imported. */
    mov %rbp, %rcx
    lea proc_mode(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_SECOND_MODE, %rdi
    mov %rax, (%rdi)
    mov (%rdi), %rax
    call *%rax
    movabs $SLOT_SECOND_MODE_VALUE, %rdi
    mov %rax, (%rdi)

    /* The rows are per-module, in the direction a single-provider table
       cannot prove: ask kernel32.dll for a name the SECOND provider
       exports. NULL and ERROR_PROC_NOT_FOUND -- not an address borrowed
       from another row. */
    mov %rbx, %rcx
    lea proc_build(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_CROSS_K32_NONE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_CROSS_K32_ERR, %rdi
    mov %rax, (%rdi)

    /* ... and the same question the other way round. */
    mov %rbp, %rcx
    lea proc_tick(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_CROSS_SEC_NONE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_CROSS_SEC_ERR, %rdi
    mov %rax, (%rdi)

    /* ---- tranche 7: Windows' ORDINAL form ---- */

    /* GetProcAddress(hnyr, (LPCSTR)11). nyrqis.dll declares OrdinalBase 10
       (its .def states it), so its SECOND function is ordinal ELEVEN -- not
       2, which is the index a lookup that ignored the base would use, and
       not 11 as an index either. Only the file's own base turns this number
       into the right EAT slot. */
    mov %rbp, %rcx
    mov $11, %edx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_ORD_MODE, %rdi
    mov %rax, (%rdi)

    /* The ordinal form and the name form must answer the SAME address: one
       walks the name table to an ordinal, the other subtracts the base and
       reads the EAT directly, and both must land on NyrqisMode. */
    movabs $SLOT_SECOND_MODE, %rsi
    mov (%rsi), %rdx
    xor %ecx, %ecx
    cmp %rdx, %rax
    sete %cl
    movabs $SLOT_ORD_SAME, %rdi
    mov %rcx, (%rdi)

    /* And the ordinal answer must be CALLABLE, exactly like the named one:
       NyrqisMode returns 1. */
    movabs $SLOT_ORD_MODE, %rdi
    mov (%rdi), %rax
    call *%rax
    movabs $SLOT_ORD_MODE_VALUE, %rdi
    mov %rax, (%rdi)

    /* One below the declared base (9): the base is not an index, so this
       names nothing. NULL and ERROR_PROC_NOT_FOUND -- not a wrapped slot. */
    mov %rbp, %rcx
    mov $9, %edx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_ORD_LOW_NONE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_ORD_LOW_ERR, %rdi
    mov %rax, (%rdi)

    /* ... and one past the last slot (the image exports FIVE slots now --
       two functions and three forwarders -- so 15 is past end): the same
       answer for the same reason. */
    mov %rbp, %rcx
    mov $15, %edx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_ORD_HIGH_NONE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_ORD_HIGH_ERR, %rdi
    mov %rax, (%rdi)

    /* ---- tranche 8: a FORWARDED export ---- */

    /* pfn = GetProcAddress(hnyr, "NyrqisTick"). That name's EAT slot holds
       the STRING "kernel32.dll.GetTickCount", not an address, so the
       surface must chase it into kernel32.dll's own row and answer THAT
       module's stop. */
    mov %rbp, %rcx
    lea proc_fwd_tick(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_FWD_TICK, %rdi
    mov %rax, (%rdi)

    /* It must be exactly the address kernel32.dll's own row gave
       GetTickCount earlier: two rows, one real export, and the answer came
       from the module the forwarder names. */
    movabs $SLOT_PROC_TICK, %rsi
    mov (%rsi), %rdx
    xor %ecx, %ecx
    cmp %rdx, %rax
    sete %cl
    movabs $SLOT_FWD_SAME, %rdi
    mov %rcx, (%rdi)

    /* And CALLABLE: the chased address is kernel32's GetTickCount stub, so
       the call traps from kernel32's .text and returns a live tick count,
       even though the name was looked up in nyrqis.dll. */
    movabs $SLOT_FWD_TICK, %rdi
    mov (%rdi), %rax
    call *%rax
    movabs $SLOT_FWD_TICK_VALUE, %rdi
    mov %rax, (%rdi)

    /* The other forwarder names an export kernel32.dll does not have:
       NULL and ERROR_PROC_NOT_FOUND, never an address from either image. */
    mov %rbp, %rcx
    lea proc_fwd_ghost(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_FWD_GHOST_NONE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_FWD_GHOST_ERR, %rdi
    mov %rax, (%rdi)

    /* ---- tranche 9: LoadLibraryA/FreeLibrary, rows at runtime ---- */

    /* Before the load, this forwarder names a module NO ROW DESCRIBES:
       nyrqis.dll's EAT slot is the string "nyrqis_dyn.dll.NyrqisDynValue",
       and that image is not loaded yet. NULL and ERROR_PROC_NOT_FOUND --
       the same answer a missing module must always give. */
    mov %rbp, %rcx
    lea proc_dyn(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_DYN_BEFORE_NONE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_DYN_BEFORE_ERR, %rdi
    mov %rax, (%rdi)

    /* hdyn = LoadLibraryA("C:\nyrqis\nyrqis_dyn.dll"). The kernel finds the
       file on the read-only volume, maps it, and adds a module row -- the
       answer is that row's base, which is how a program not linked against
       a DLL reaches its code at all. */
    lea path_dyn(%rip), %rcx
    call *__imp_LoadLibraryA(%rip)
    mov %rax, %r13
    movabs $SLOT_DYN_BASE, %rdi
    mov %rax, (%rdi)

    /* The module NAME now answers that base: Windows registers a module
       under its file name, not under the path it was loaded from. */
    lea name_dyn(%rip), %rcx
    call *__imp_GetModuleHandleA(%rip)
    movabs $SLOT_DYN_HANDLE, %rdi
    mov %rax, (%rdi)

    /* The SAME question as the first probe in this tranche, with the row
       that was missing now present: the chase lands in the dynamic module's
       own export directory. Two answers from one EAT slot, decided only by
       what is loaded. */
    mov %rbp, %rcx
    lea proc_dyn(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_DYN_PROC, %rdi
    mov %rax, (%rdi)

    /* And it must be CALLABLE: the forwarder's target is ordinary ring-3
       code in the dynamic image, so the call returns 42 with no trap at
       all -- the module table handed out an address, and the address is a
       real function. */
    mov (%rdi), %rax
    call *%rax
    movabs $SLOT_DYN_PROC_VALUE, %rdi
    mov %rax, (%rdi)

    /* The dynamic row asked for the same export directly must answer the
       very address the chase produced: two routes, one EAT entry. */
    mov %r13, %rcx
    lea proc_dyn_value(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_DYN_OWN, %rdi
    mov %rax, (%rdi)

    /* WHICH FILE that row came from: the PATH this program handed
       LoadLibraryA, not the file name the module is registered under. That
       string exists nowhere in this image -- the client never linked against
       the module -- so an answer that matched proves the row kept the record
       the loader was given. */
    mov %r13, %rcx
    movabs $buf_file, %rdx
    mov $64, %r8d
    call *__imp_GetModuleFileNameA(%rip)
    movabs $SLOT_FILE_DYN_LEN, %rdi
    mov %rax, (%rdi)

    movabs $buf_file, %rsi
    lea path_dyn(%rip), %rdi
    mov $DYNAMIC_PATH_LEN, %ecx
    mov $1, %eax
3:
    movzbq (%rsi), %rdx
    movzbq (%rdi), %r8
    cmp %r8, %rdx
    jne 4f
    inc %rsi
    inc %rdi
    loop 3b
    cmpb $0, (%rsi)
    jne 4f
    jmp 5f
4:
    xor %eax, %eax
5:
    movabs $SLOT_FILE_DYN_OK, %rdi
    mov %rax, (%rdi)

    /* FreeLibrary(hdyn): the row is dropped (this was its only reference),
       so the module is no longer loaded in this process. */
    mov %r13, %rcx
    call *__imp_FreeLibrary(%rip)
    movabs $SLOT_DYN_FREED, %rdi
    mov %rax, (%rdi)

    /* ... and the forwarder goes back to failing, because its target is no
       longer a row. Same name, same module, third answer. */
    mov %rbp, %rcx
    lea proc_dyn(%rip), %rdx
    call *__imp_GetProcAddress(%rip)
    movabs $SLOT_DYN_AFTER_NONE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_DYN_AFTER_ERR, %rdi
    mov %rax, (%rdi)

    /* A DLL the volume does not have: Windows answers NULL and says which
       module was not found. A failure VALUE, not a refused call -- a
       program probing for an optional DLL is expected to test for NULL. */
    lea path_dyn_missing(%rip), %rcx
    call *__imp_LoadLibraryA(%rip)
    movabs $SLOT_DYN_MISS_NONE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_DYN_MISS_ERR, %rdi
    mov %rax, (%rdi)

    /* ---- tranche 10: which FILE a handle came from ---- */

    /* GetModuleFileNameA(NULL, buf, 64): Windows' spelling of "where is the
       executable". The answer is this process's own row's path, compared
       against this program's own copy of it -- a length alone would pass on a
       wrong path of the same size. */
    xor %ecx, %ecx
    movabs $buf_file, %rdx
    mov $64, %r8d
    call *__imp_GetModuleFileNameA(%rip)
    movabs $SLOT_FILE_SELF_LEN, %rdi
    mov %rax, (%rdi)

    movabs $buf_file, %rsi
    lea path_self(%rip), %rdi
    mov $SELF_PATH_LEN, %ecx
    mov $1, %eax
3:
    movzbq (%rsi), %rdx
    movzbq (%rdi), %r8
    cmp %r8, %rdx
    jne 4f
    inc %rsi
    inc %rdi
    loop 3b
    cmpb $0, (%rsi)
    jne 4f
    jmp 5f
4:
    xor %eax, %eax
5:
    movabs $SLOT_FILE_SELF_OK, %rdi
    mov %rax, (%rdi)

    /* ... and kernel32.dll's own handle, which must answer THAT module's path:
       one row answering the previous row's string is exactly the mistake a
       table keyed by anything other than the handle would make. */
    lea name_kernel32(%rip), %rcx
    call *__imp_GetModuleHandleA(%rip)
    mov %rax, %rcx
    movabs $buf_file, %rdx
    mov $64, %r8d
    call *__imp_GetModuleFileNameA(%rip)
    movabs $SLOT_FILE_K32_LEN, %rdi
    mov %rax, (%rdi)

    movabs $buf_file, %rsi
    lea path_kernel32_file(%rip), %rdi
    mov $K32_PATH_LEN, %ecx
    mov $1, %eax
3:
    movzbq (%rsi), %rdx
    movzbq (%rdi), %r8
    cmp %r8, %rdx
    jne 4f
    inc %rsi
    inc %rdi
    loop 3b
    cmpb $0, (%rsi)
    jne 4f
    jmp 5f
4:
    xor %eax, %eax
5:
    movabs $SLOT_FILE_K32_OK, %rdi
    mov %rax, (%rdi)

    /* A buffer too small: Windows returns nSize (not the full length) and
       leaves ERROR_INSUFFICIENT_BUFFER, and what it copied is nSize-1 bytes
       plus a NUL. All three halves are checked, because a truncation that
       dropped the terminator would be a string this program could not use. */
    xor %ecx, %ecx
    movabs $buf_trunc, %rdx
    mov $8, %r8d
    call *__imp_GetModuleFileNameA(%rip)
    movabs $SLOT_FILE_TRUNC_RET, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_FILE_TRUNC_ERR, %rdi
    mov %rax, (%rdi)

    movabs $buf_trunc, %rsi
    lea path_trunc_prefix(%rip), %rdi
    mov $TRUNC_PREFIX_LEN, %ecx
    mov $1, %eax
3:
    movzbq (%rsi), %rdx
    movzbq (%rdi), %r8
    cmp %r8, %rdx
    jne 4f
    inc %rsi
    inc %rdi
    loop 3b
    cmpb $0, (%rsi)
    jne 4f
    jmp 5f
4:
    xor %eax, %eax
5:
    movabs $SLOT_FILE_TRUNC_OK, %rdi
    mov %rax, (%rdi)

    /* nSize 0 writes NOTHING and answers 0 with the same code: the buffer is
       deliberately left holding the truncated bytes above, so a surface that
       wrote through a zero length would corrupt a byte this program can see. */
    xor %ecx, %ecx
    movabs $buf_trunc, %rdx
    xor %r8d, %r8d
    call *__imp_GetModuleFileNameA(%rip)
    movabs $SLOT_FILE_ZERO_RET, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_FILE_ZERO_ERR, %rdi
    mov %rax, (%rdi)

    /* A base that names no row: a FAILED call, 0 plus ERROR_INVALID_HANDLE --
       the same answer every other unissued handle gives, and this program's
       thirteenth deliberate refusal. */
    mov $0x1234, %ecx
    movabs $buf_file, %rdx
    mov $64, %r8d
    call *__imp_GetModuleFileNameA(%rip)
    movabs $SLOT_FILE_BAD_RET, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_FILE_BAD_ERR, %rdi
    mov %rax, (%rdi)

    /* ---- tranche 11: the process heap ---- */

    /* h = GetProcessHeap(): the handle a CRT caches and hands to every
       allocation. */
    call *__imp_GetProcessHeap(%rip)
    mov %rax, %r15
    movabs $SLOT_HEAP_HANDLE, %rdi
    mov %rax, (%rdi)

    /* p1 = HeapAlloc(h, 0, 64). The answer must be a pointer this program
       can WRITE through -- the kernel carves it out of a region this
       process's own address space owns. */
    mov %r15, %rcx
    xor %edx, %edx
    mov $64, %r8d
    call *__imp_HeapAlloc(%rip)
    mov %rax, %r14
    movabs $SLOT_HEAP_P1, %rdi
    mov %rax, (%rdi)

    /* p2 = HeapAlloc(h, HEAP_ZERO_MEMORY, 32): a second, disjoint block,
       zeroed before it is handed over. Both facts are checked, because a
       kernel that returned the same address twice would still satisfy a
       "non-NULL" test. */
    mov %r15, %rcx
    mov $8, %edx
    mov $32, %r8d
    call *__imp_HeapAlloc(%rip)
    movabs $SLOT_HEAP_P2, %rdi
    mov %rax, (%rdi)
    xor %ecx, %ecx
    test %rax, %rax
    jz 9f
    cmpq $0, (%rax)
    jne 9f
    cmpq $0, 8(%rax)
    jne 9f
    mov $1, %ecx
9:
    movabs $SLOT_HEAP_ZERO, %rdi
    mov %rcx, (%rdi)

    /* Disjointness: p1 + 64 <= p2 or p2 + 32 <= p1. Two live allocations
       that overlap would let a write through one corrupt the other. */
    movabs $SLOT_HEAP_P2, %rdi
    mov (%rdi), %rdx
    xor %ecx, %ecx
    test %r14, %r14
    jz 8f
    test %rdx, %rdx
    jz 8f
    lea 64(%r14), %rax
    cmp %rdx, %rax
    jbe 7f
    lea 32(%rdx), %rax
    cmp %r14, %rax
    jbe 7f
    jmp 8f
7:
    mov $1, %ecx
8:
    movabs $SLOT_HEAP_DISTINCT, %rdi
    mov %rcx, (%rdi)

    /* Write through the block, then read it back: a pointer into a page the
       client cannot touch would fault here, which is exactly the bug a
       kernel-heap-backed allocator would have. */
    xor %ecx, %ecx
    test %r14, %r14
    jz 6f
    movb $0x41, (%r14)
    cmpb $0x41, (%r14)
    sete %cl
6:
    movabs $SLOT_HEAP_RW, %rdi
    mov %rcx, (%rdi)

    /* HeapFree(p1), then Free it AGAIN: the first is TRUE, the second is
       FALSE with ERROR_INVALID_ADDRESS -- a double free is a client bug, and
       Windows' own allocator would corrupt its metadata rather than report it. */
    mov %r15, %rcx
    xor %edx, %edx
    mov %r14, %r8
    call *__imp_HeapFree(%rip)
    movabs $SLOT_HEAP_FREE, %rdi
    mov %rax, (%rdi)

    mov %r15, %rcx
    xor %edx, %edx
    mov %r14, %r8
    call *__imp_HeapFree(%rip)
    movabs $SLOT_HEAP_FREE2, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_HEAP_FREE2_ERR, %rdi
    mov %rax, (%rdi)

    /* The NEXT allocation of a fitting size must answer the freed address:
       that is what makes Free a return of memory rather than a flag. */
    mov %r15, %rcx
    xor %edx, %edx
    mov $16, %r8d
    call *__imp_HeapAlloc(%rip)
    xor %ecx, %ecx
    cmp %r14, %rax
    sete %cl
    movabs $SLOT_HEAP_REUSE, %rdi
    mov %rcx, (%rdi)
    movabs $SLOT_HEAP_P3, %rdi
    mov %rax, (%rdi)

    /* A handle that is not this process's heap: NULL, ERROR_INVALID_HANDLE. */
    mov $0x1234, %ecx
    xor %edx, %edx
    mov $16, %r8d
    call *__imp_HeapAlloc(%rip)
    movabs $SLOT_HEAP_BADH, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_HEAP_BADH_ERR, %rdi
    mov %rax, (%rdi)

    /* HEAP_GENERATE_EXCEPTIONS (0x4): a Windows exception this kernel has no
       dispatcher for, so the flag is refused rather than silently dropped. */
    mov %r15, %rcx
    mov $4, %edx
    mov $16, %r8d
    call *__imp_HeapAlloc(%rip)
    movabs $SLOT_HEAP_BADF, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_HEAP_BADF_ERR, %rdi
    mov %rax, (%rdi)

    /* Larger than the whole heap region: NULL, ERROR_NOT_ENOUGH_MEMORY. */
    mov %r15, %rcx
    xor %edx, %edx
    mov $0x100000, %r8d
    call *__imp_HeapAlloc(%rip)
    movabs $SLOT_HEAP_BIG, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_HEAP_BIG_ERR, %rdi
    mov %rax, (%rdi)

    /* Freeing an address this heap never issued: FALSE, ERROR_INVALID_ADDRESS. */
    mov %r15, %rcx
    xor %edx, %edx
    mov $0x1234, %r8
    call *__imp_HeapFree(%rip)
    movabs $SLOT_HEAP_BADFREE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_HEAP_BADFREE_ERR, %rdi
    mov %rax, (%rdi)

    /* ---- tranche 12: the heap's size and reallocation ---- */

    /* HeapSize(h, 0, p2) == 32. p2 is the live 32-byte block, and Windows
       answers the BLOCK's size (which this allocator rounds to 16 bytes). */
    mov %r15, %rcx
    xor %edx, %edx
    movabs $SLOT_HEAP_P2, %rdi
    mov (%rdi), %r8
    call *__imp_HeapSize(%rip)
    xor %ecx, %ecx
    cmp $32, %rax
    sete %cl
    movabs $SLOT_HEAP_SIZE_OK, %rdi
    mov %rcx, (%rdi)

    /* p4 = HeapReAlloc(h, 0, p2, 48): 48 does not fit the 32-byte block, so
       the block must MOVE. The byte written through p2 below must arrive in
       the new block -- that is the copy, not a fresh zeroed allocation. */
    movabs $SLOT_HEAP_P2, %rdi
    mov (%rdi), %rax
    movb $0x5a, (%rax)
    mov %r15, %rcx
    xor %edx, %edx
    mov %rax, %r8
    mov $48, %r9d
    call *__imp_HeapReAlloc(%rip)
    mov %rax, %r12
    movabs $SLOT_HEAP_REALLOC_PTR, %rdi
    mov %rax, (%rdi)
    xor %ecx, %ecx
    test %rax, %rax
    jz 5f
    cmpb $0x5a, (%rax)           /* the old block's byte, in the new block */
    jne 5f
    test $15, %al                /* 16-byte aligned */
    jnz 5f
    mov $1, %ecx
5:
    movabs $SLOT_HEAP_REALLOC_OK, %rdi
    mov %rcx, (%rdi)

    /* HeapSize(h, 0, p4) == 128: the 48-byte request reused a FREED 128-byte
       block, and a reused block keeps its own size (the stated allocator
       limit), so the answer is the block's size rather than the request's. */
    mov %r15, %rcx
    xor %edx, %edx
    mov %r12, %r8
    call *__imp_HeapSize(%rip)
    xor %ecx, %ecx
    cmp $128, %rax
    sete %cl
    movabs $SLOT_HEAP_REALLOC_SIZE, %rdi
    mov %rcx, (%rdi)

    /* A shrink that fits the block answers the SAME pointer: no split is made
       and the block keeps its size. */
    mov %r15, %rcx
    xor %edx, %edx
    mov %r12, %r8
    mov $16, %r9d
    call *__imp_HeapReAlloc(%rip)
    xor %ecx, %ecx
    cmp %r12, %rax
    sete %cl
    movabs $SLOT_HEAP_REALLOC_SAME, %rdi
    mov %rcx, (%rdi)

    /* A grow that cannot happen in place, with HEAP_REALLOC_IN_PLACE_ONLY:
       NULL plus ERROR_NOT_ENOUGH_MEMORY, and the block is unchanged. */
    mov %r15, %rcx
    mov $0x10, %edx
    mov %r12, %r8
    mov $4096, %r9d
    call *__imp_HeapReAlloc(%rip)
    movabs $SLOT_HEAP_INPLACE, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_HEAP_INPLACE_ERR, %rdi
    mov %rax, (%rdi)

    /* HEAP_GENERATE_EXCEPTIONS is not a heap flag this kernel can honour. */
    mov %r15, %rcx
    mov $4, %edx
    mov %r12, %r8
    mov $64, %r9d
    call *__imp_HeapReAlloc(%rip)
    movabs $SLOT_HEAP_REALLOC_BADF, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_HEAP_REALLOC_BADF_ERR, %rdi
    mov %rax, (%rdi)

    /* HeapSize of the moved-from block: its address is no longer a block. */
    mov %r15, %rcx
    xor %edx, %edx
    movabs $SLOT_HEAP_P2, %rdi
    mov (%rdi), %r8
    call *__imp_HeapSize(%rip)
    movabs $SLOT_HEAP_SIZE_BAD, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_HEAP_SIZE_BAD_ERR, %rdi
    mov %rax, (%rdi)

    /* Return the two survivors: the reallocated block and p3. */
    mov %r15, %rcx
    xor %edx, %edx
    mov %r12, %r8
    call *__imp_HeapFree(%rip)
    mov %r15, %rcx
    xor %edx, %edx
    movabs $SLOT_HEAP_P3, %rdi
    mov (%rdi), %r8
    call *__imp_HeapFree(%rip)

    /* ---- tranche 13: the file's size and its cursor ---- */

    /* f7 = CreateFileA(hello.txt) -- a fresh handle, so its cursor is 0 and
       every position below is the kernel's answer rather than a leftover. */
    lea path_hello(%rip), %rcx
    mov $0x80000000, %edx
    mov $1, %r8d
    xor %r9d, %r9d
    movq $3, 0x20(%rsp)
    call *__imp_CreateFileA(%rip)
    mov %rax, %rbx
    movabs $SLOT_FILE7, %rdi
    mov %rax, (%rdi)

    /* GetFileSize(f7, &high): 33 bytes. The high dword is an OUT parameter, so
       the kernel writes it even though it is zero here. */
    mov %rbx, %rcx
    movabs $SLOT_SIZE_HIGH, %rdx
    call *__imp_GetFileSize(%rip)
    movabs $SLOT_FILE_SIZE, %rdi
    mov %rax, (%rdi)

    /* SetFilePointer(f7, 3, NULL, FILE_BEGIN) == 3 */
    mov %rbx, %rcx
    mov $3, %edx
    xor %r8d, %r8d
    xor %r9d, %r9d
    call *__imp_SetFilePointer(%rip)
    movabs $SLOT_SEEK_ABS, %rdi
    mov %rax, (%rdi)

    /* ReadFile(f7, seek_a, 4, &n7, NULL): the bytes AT that position. */
    mov %rbx, %rcx
    movabs $buf_seek_a, %rdx
    mov $4, %r8d
    movabs $SLOT_N7, %r9
    movq $0, 0x20(%rsp)
    call *__imp_ReadFile(%rip)

    /* SetFilePointer(f7, 0, NULL, FILE_CURRENT) == 7: the position the READ
       left the cursor at, which is what makes the two calls share state. */
    mov %rbx, %rcx
    xor %edx, %edx
    xor %r8d, %r8d
    mov $1, %r9d
    call *__imp_SetFilePointer(%rip)
    movabs $SLOT_SEEK_CUR, %rdi
    mov %rax, (%rdi)

    /* SetFilePointer(f7, -4, NULL, FILE_END) == 29: a NEGATIVE 32-bit
       distance counted back from the end. */
    mov %rbx, %rcx
    mov $-4, %edx
    xor %r8d, %r8d
    mov $2, %r9d
    call *__imp_SetFilePointer(%rip)
    movabs $SLOT_SEEK_END, %rdi
    mov %rax, (%rdi)

    /* ReadFile(f7, seek_b, 4, &n8, NULL): the file's LAST four bytes. */
    mov %rbx, %rcx
    movabs $buf_seek_b, %rdx
    mov $4, %r8d
    movabs $SLOT_N8, %r9
    movq $0, 0x20(%rsp)
    call *__imp_ReadFile(%rip)

    /* SetFilePointer(f7, -100, NULL, FILE_BEGIN): before the file's first
       byte. Windows' failure VALUE (not a refusal): INVALID_SET_FILE_POINTER
       with ERROR_NEGATIVE_SEEK. */
    mov %rbx, %rcx
    mov $-100, %edx
    xor %r8d, %r8d
    xor %r9d, %r9d
    call *__imp_SetFilePointer(%rip)
    movabs $SLOT_SEEK_NEG, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_SEEK_NEG_ERR, %rdi
    mov %rax, (%rdi)

    /* ... and the cursor is where the refused seek found it. */
    mov %rbx, %rcx
    xor %edx, %edx
    xor %r8d, %r8d
    mov $1, %r9d
    call *__imp_SetFilePointer(%rip)
    movabs $SLOT_SEEK_CUR_AFTER, %rdi
    mov %rax, (%rdi)

    /* SetFilePointer(f7, 0, NULL, 7): a move method that is not
       FILE_BEGIN/CURRENT/END -- a typed refusal, ERROR_INVALID_PARAMETER. */
    mov %rbx, %rcx
    xor %edx, %edx
    xor %r8d, %r8d
    mov $7, %r9d
    call *__imp_SetFilePointer(%rip)
    movabs $SLOT_SEEK_BAD, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_SEEK_BAD_ERR, %rdi
    mov %rax, (%rdi)

    /* GetFileSize of the CONSOLE handle: it names the console, not a file. */
    mov $0xb, %ecx
    xor %edx, %edx
    call *__imp_GetFileSize(%rip)
    movabs $SLOT_SIZE_BAD, %rdi
    mov %rax, (%rdi)
    call *__imp_GetLastError(%rip)
    movabs $SLOT_SIZE_BAD_ERR, %rdi
    mov %rax, (%rdi)

    mov %rbx, %rcx
    call *__imp_CloseHandle(%rip)
    movabs $SLOT_CLOSE7, %rdi
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
/* tranche 5's module and export names. The module name appears in both cases
   Windows must treat as one module; the export name appears in the exact case
   that resolves and beside a name this DLL does not export. */
name_kernel32:
    .asciz "kernel32.dll"
name_kernel32_upper:
    .asciz "KERNEL32.DLL"
name_absent:
    .asciz "nope.dll"
proc_tick:
    .asciz "GetTickCount"
proc_absent:
    .asciz "NoSuchExport"
/* tranche 6's second provider and the two export names asked of both rows.
   The module is not in this program's import directory at all. */
name_nyrqis:
    .asciz "nyrqis.dll"
proc_build:
    .asciz "NyrqisBuild"
proc_mode:
    .asciz "NyrqisMode"
/* tranche 8's forwarded export and its negative: the first is the EAT string
   "kernel32.dll.GetTickCount", the second names an export that module does
   not have. */
proc_fwd_tick:
    .asciz "NyrqisTick"
proc_fwd_ghost:
    .asciz "NyrqisGhost"
/* tranche 10: this program's own path, the provider's, and the prefix a
   truncated copy of its own path must leave behind. All three are copies the
   CLIENT holds, so the comparison is against something the kernel cannot have
   supplied at call time. */
path_self:
    .asciz "C:\\nyrqis\\win32_console.exe"
    .set SELF_PATH_LEN, . - path_self - 1
path_kernel32_file:
    .asciz "C:\\nyrqis\\kernel32.dll"
    .set K32_PATH_LEN, . - path_kernel32_file - 1
path_trunc_prefix:
    .asciz "C:\\nyrq"
    .set TRUNC_PREFIX_LEN, . - path_trunc_prefix - 1
/* tranche 9's runtime module: the path handed to LoadLibraryA, the file
   name Windows registers it under, the forwarder that names it and the
   export that forwarder resolves to once the row exists. */
path_dyn:
    .asciz "C:\\nyrqis\\nyrqis_dyn.dll"
    .set DYNAMIC_PATH_LEN, . - path_dyn - 1
path_dyn_missing:
    .asciz "C:\\nyrqis\\missing.dll"
name_dyn:
    .asciz "nyrqis_dyn.dll"
proc_dyn:
    .asciz "NyrqisDyn"
proc_dyn_value:
    .asciz "NyrqisDynValue"
/* The SizeOfImage the link gives this executable (build.sh pins the same
   0x4000 from objdump -p), used by the module-handle test above. */
    .set IMAGE_SIZE, 0x6000

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
/* tranche 5: the runtime binding's answers. The first five sit in the gap
   between the startup fields (which end at 0x278) and the command-line string
   at 0x2a0; the four negative probes sit ABOVE the string and below the 0x300
   area the kernel-side controls write their test paths into. */
    .set SLOT_NAMED_MODULE,     SLOT_BASE + 0x278 /* GetModuleHandleA(name) */
    .set SLOT_NAMED_MODULE2,    SLOT_BASE + 0x280 /* ... in upper case */
    .set SLOT_PROC_TICK,        SLOT_BASE + 0x288 /* GetProcAddress answer */
    .set SLOT_PROC_TICK_SAME,   SLOT_BASE + 0x290 /* == the IAT slot */
    .set SLOT_PROC_TICK_VALUE,  SLOT_BASE + 0x298 /* called through it */
    .set SLOT_NAMED_NONE,       SLOT_BASE + 0x2c0 /* "nope.dll" -> NULL */
    .set SLOT_NAMED_NONE_ERR,   SLOT_BASE + 0x2c8 /* ... ERROR_MOD_NOT_FOUND */
    .set SLOT_PROC_NONE,        SLOT_BASE + 0x2d0 /* absent export -> NULL */
    .set SLOT_PROC_NONE_ERR,    SLOT_BASE + 0x2d8 /* ... ERROR_PROC_NOT_FOUND */
/* tranche 6: the second provider's answers, ABOVE the 0x300 area the kernel's
   own controls write their test paths and names into (and above the export
   names they write at 0x500+), so a field here cannot land on a string the
   kernel is still using. */
    .set SLOT_NAMED_SECOND,     SLOT_BASE + 0x700 /* "nyrqis.dll" base */
    .set SLOT_SECOND_BUILD,     SLOT_BASE + 0x708 /* NyrqisBuild address */
    .set SLOT_SECOND_MODE,      SLOT_BASE + 0x710 /* NyrqisMode address */
    .set SLOT_SECOND_BUILD_VALUE, SLOT_BASE + 0x718 /* called through: 6 */
    .set SLOT_SECOND_MODE_VALUE,  SLOT_BASE + 0x720 /* called through: 1 */
    .set SLOT_CROSS_K32_NONE,   SLOT_BASE + 0x728 /* kernel32 asked for it */
    .set SLOT_CROSS_K32_ERR,    SLOT_BASE + 0x730 /* ... ERROR_PROC_NOT_FOUND */
    .set SLOT_CROSS_SEC_NONE,   SLOT_BASE + 0x738 /* nyrqis asked for tick */
    .set SLOT_CROSS_SEC_ERR,    SLOT_BASE + 0x740 /* ... ERROR_PROC_NOT_FOUND */
/* tranche 7: the ordinal form's answers, still below 0x800. `nyrqis.dll`
   declares OrdinalBase 10, so ordinal 11 is its second export: the fields
   below ask BOTH ends of the range and the address the middle resolves to. */
    .set SLOT_ORD_MODE,         SLOT_BASE + 0x748 /* GetProcAddress(hnyr, 11) */
    .set SLOT_ORD_SAME,         SLOT_BASE + 0x750 /* == the NAME answer */
    .set SLOT_ORD_MODE_VALUE,   SLOT_BASE + 0x758 /* called through: 1 */
    .set SLOT_ORD_LOW_NONE,     SLOT_BASE + 0x760 /* ordinal 9 -> 0 */
    .set SLOT_ORD_LOW_ERR,      SLOT_BASE + 0x768 /* ... 127 */
    .set SLOT_ORD_HIGH_NONE,    SLOT_BASE + 0x770 /* ordinal 15 -> 0 */
    .set SLOT_ORD_HIGH_ERR,     SLOT_BASE + 0x778 /* ... 127 */
/* tranche 8: the forwarded export's answers. `NyrqisTick` is a forwarder to
   kernel32.dll.GetTickCount, so its address must equal the one kernel32's own
   row gave and must be callable; `NyrqisGhost` forwards to an export that
   module does not have and must fail with ERROR_PROC_NOT_FOUND. */
    .set SLOT_FWD_TICK,         SLOT_BASE + 0x780 /* NyrqisTick answer */
    .set SLOT_FWD_SAME,         SLOT_BASE + 0x788 /* == kernel32's answer */
    .set SLOT_FWD_TICK_VALUE,   SLOT_BASE + 0x790 /* called through: a tick */
    .set SLOT_FWD_GHOST_NONE,   SLOT_BASE + 0x798 /* NyrqisGhost -> 0 */
    .set SLOT_FWD_GHOST_ERR,    SLOT_BASE + 0x7a0 /* ... 127 */
/* tranche 9: the runtime-loaded module's lifecycle, above the forwarder
   fields and below the 0x900 area the kernel's own controls write into. */
    .set SLOT_DYN_BEFORE_NONE,  SLOT_BASE + 0x7a8 /* "NyrqisDyn" -> 0 */
    .set SLOT_DYN_BEFORE_ERR,   SLOT_BASE + 0x7b0 /* ... 127 */
    .set SLOT_DYN_BASE,         SLOT_BASE + 0x7b8 /* LoadLibraryA answer */
    .set SLOT_DYN_HANDLE,       SLOT_BASE + 0x7c0 /* ... by name too */
    .set SLOT_DYN_PROC,         SLOT_BASE + 0x7c8 /* the chase landed */
    .set SLOT_DYN_PROC_VALUE,   SLOT_BASE + 0x7d0 /* called through: 42 */
    .set SLOT_DYN_OWN,          SLOT_BASE + 0x7d8 /* its own row's answer */
    .set SLOT_DYN_FREED,        SLOT_BASE + 0x7e0 /* FreeLibrary -> 1 */
    .set SLOT_DYN_AFTER_NONE,   SLOT_BASE + 0x7e8 /* ... -> 0 again */
    .set SLOT_DYN_AFTER_ERR,    SLOT_BASE + 0x7f0 /* ... 127 */
    .set SLOT_DYN_MISS_NONE,    SLOT_BASE + 0x7f8 /* missing DLL -> 0 */
    .set SLOT_DYN_MISS_ERR,     SLOT_BASE + 0x800 /* ... 126 */
/* tranche 10: a handle's provenance. The runtime row's answer is taken while
   it is loaded; the startup rows' after that. `TRUNC_OK` is the client's own
   byte-and-NUL compare of what a too-small buffer received, and the zero-length
   probe reuses the SAME buffer so a write through nSize 0 would be visible. */
    .set SLOT_FILE_SELF_LEN,    SLOT_BASE + 0x820 /* GetModuleFileNameA(NULL) */
    .set SLOT_FILE_SELF_OK,     SLOT_BASE + 0x828 /* ... bytes + NUL matched */
    .set SLOT_FILE_K32_LEN,     SLOT_BASE + 0x830 /* kernel32.dll's path */
    .set SLOT_FILE_K32_OK,      SLOT_BASE + 0x838 /* ... bytes + NUL matched */
    .set SLOT_FILE_TRUNC_RET,   SLOT_BASE + 0x840 /* 8, not 27 */
    .set SLOT_FILE_TRUNC_ERR,   SLOT_BASE + 0x848 /* ... 122 */
    .set SLOT_FILE_TRUNC_OK,    SLOT_BASE + 0x850 /* ... 7 bytes + NUL */
    .set SLOT_FILE_DYN_LEN,     SLOT_BASE + 0x858 /* the runtime row's path */
    .set SLOT_FILE_DYN_OK,      SLOT_BASE + 0x860 /* ... bytes + NUL matched */
    .set SLOT_FILE_BAD_RET,     SLOT_BASE + 0x868 /* no such row -> 0 */
    .set SLOT_FILE_BAD_ERR,     SLOT_BASE + 0x870 /* ... 6 */
    .set SLOT_FILE_ZERO_RET,    SLOT_BASE + 0x878 /* nSize 0 -> 0 */
    .set SLOT_FILE_ZERO_ERR,    SLOT_BASE + 0x880 /* ... 122 */
    .set buf_trunc,             SLOT_BASE + 0x890
    .set buf_file,              SLOT_BASE + 0x8a0
/* tranche 11: the process heap's answers, above the file-name buffers and
   below the page end. `P1`/`P2`/`P3` are addresses the kernel chose, so every
   assertion about them is relational (distinct, disjoint, reused) or about the
   bytes written through them -- never a constant this program guessed. */
    .set SLOT_HEAP_HANDLE,      SLOT_BASE + 0x900
    .set SLOT_HEAP_P1,          SLOT_BASE + 0x908
    .set SLOT_HEAP_P2,          SLOT_BASE + 0x910
    .set SLOT_HEAP_ZERO,        SLOT_BASE + 0x918
    .set SLOT_HEAP_DISTINCT,    SLOT_BASE + 0x920
    .set SLOT_HEAP_RW,          SLOT_BASE + 0x928
    .set SLOT_HEAP_FREE,        SLOT_BASE + 0x930
    .set SLOT_HEAP_FREE2,       SLOT_BASE + 0x938
    .set SLOT_HEAP_FREE2_ERR,   SLOT_BASE + 0x940
    .set SLOT_HEAP_REUSE,       SLOT_BASE + 0x948
    .set SLOT_HEAP_P3,          SLOT_BASE + 0x950
    .set SLOT_HEAP_BADH,        SLOT_BASE + 0x958
    .set SLOT_HEAP_BADH_ERR,    SLOT_BASE + 0x960
    .set SLOT_HEAP_BADF,        SLOT_BASE + 0x968
    .set SLOT_HEAP_BADF_ERR,    SLOT_BASE + 0x970
    .set SLOT_HEAP_BIG,         SLOT_BASE + 0x978
    .set SLOT_HEAP_BIG_ERR,     SLOT_BASE + 0x980
    .set SLOT_HEAP_BADFREE,     SLOT_BASE + 0x988
    .set SLOT_HEAP_BADFREE_ERR, SLOT_BASE + 0x990
    .set SLOT_HEAP_SIZE_OK,          SLOT_BASE + 0xA00
    .set SLOT_HEAP_REALLOC_PTR,      SLOT_BASE + 0xA08
    .set SLOT_HEAP_REALLOC_OK,       SLOT_BASE + 0xA10
    .set SLOT_HEAP_REALLOC_SIZE,     SLOT_BASE + 0xA18
    .set SLOT_HEAP_REALLOC_SAME,     SLOT_BASE + 0xA20
    .set SLOT_HEAP_INPLACE,          SLOT_BASE + 0xA28
    .set SLOT_HEAP_INPLACE_ERR,      SLOT_BASE + 0xA30
    .set SLOT_HEAP_REALLOC_BADF,     SLOT_BASE + 0xA38
    .set SLOT_HEAP_REALLOC_BADF_ERR, SLOT_BASE + 0xA40
    .set SLOT_HEAP_SIZE_BAD,         SLOT_BASE + 0xA48
    .set SLOT_HEAP_SIZE_BAD_ERR,     SLOT_BASE + 0xA50
/* tranche 13: the file's size and cursor. `SLOT_SIZE_HIGH` is written by the
   KERNEL through GetFileSize's out-parameter, which is why the client passes
   its address rather than a local. `buf_seek_a`/`buf_seek_b` are the four
   bytes read at two different positions of the same file. */
    .set SLOT_FILE7,            SLOT_BASE + 0xB00
    .set SLOT_FILE_SIZE,        SLOT_BASE + 0xB08
    .set SLOT_SIZE_HIGH,        SLOT_BASE + 0xB10
    .set SLOT_SIZE_BAD,         SLOT_BASE + 0xB18
    .set SLOT_SIZE_BAD_ERR,     SLOT_BASE + 0xB20
    .set SLOT_SEEK_ABS,         SLOT_BASE + 0xB28
    .set SLOT_SEEK_CUR,         SLOT_BASE + 0xB30
    .set SLOT_SEEK_END,         SLOT_BASE + 0xB38
    .set SLOT_SEEK_NEG,         SLOT_BASE + 0xB40
    .set SLOT_SEEK_NEG_ERR,     SLOT_BASE + 0xB48
    .set SLOT_SEEK_CUR_AFTER,   SLOT_BASE + 0xB50
    .set SLOT_SEEK_BAD,         SLOT_BASE + 0xB58
    .set SLOT_SEEK_BAD_ERR,     SLOT_BASE + 0xB60
    .set SLOT_CLOSE7,           SLOT_BASE + 0xB68
    .set SLOT_N7,               SLOT_BASE + 0xB70
    .set SLOT_N8,               SLOT_BASE + 0xB78
    .set buf_seek_a,            SLOT_BASE + 0xB90
    .set buf_seek_b,            SLOT_BASE + 0xB98
    .set SLOT_BUF,       SLOT_BASE + 0x100
    .set SLOT_EOFBUF,    SLOT_BASE + 0x140
    .set SLOT_BUF2,      SLOT_BASE + 0x180
    .set SLOT_BIN,       SLOT_BASE + 0x200
