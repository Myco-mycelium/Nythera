/*
 * prog_b.s -- a second, independent test program, identical in
 * structure to prog.s but with a different magic value (prog.s writes
 * 0xC0FFEE; this writes 0xBBBBBBBB). Exists specifically so the
 * process-isolation proof in experimental::address_space can tell two
 * processes' memory apart: if isolation is genuinely working, process
 * A's frame keeps holding 0xC0FFEE and process B's separate frame
 * holds 0xBBBBBBBB, even though both processes use the identical
 * virtual address for their mapping. If isolation were broken (e.g. a
 * copy-mapping bug that accidentally shared the same physical frame),
 * running B would overwrite what A wrote, and switching back to A's
 * address space would incorrectly read B's value -- which is exactly
 * the failure mode this proof is designed to catch.
 */

.section .text
.global _start
_start:
    movabs $0xBBBBBBBB, %rax
    movabs $0x20000010000, %rbx   /* matches usermode::USER_STACK_ADDR */
    mov %rax, (%rbx)
    int $0x80
.hang:
    pause
    jmp .hang
