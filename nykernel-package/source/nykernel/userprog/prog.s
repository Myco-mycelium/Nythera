/*
 * prog.s -- a tiny, real, statically-linked user-mode program, built
 * with the same as/ld toolchain as the kernel itself, entirely
 * separate from it. This exists to give experimental::elf a genuine
 * ELF64 file to parse -- not bytes hand-assembled to match whatever
 * the loader happened to expect.
 *
 * What it does: writes a magic value to a fixed, kernel-known address
 * (the top of the already-mapped user stack page -- reusing an
 * existing writable/user-accessible mapping rather than requiring the
 * loader to understand a second PT_LOAD segment, which is future
 * work), then traps into the kernel via `int 0x80`, then parks in a
 * ring-3-legal spin loop. The magic value write is what makes this a
 * genuine correctness check: the kernel verifies it after the trap,
 * so this passes only if the ELF's own entry point actually executed
 * -- not merely that *some* code reached the syscall gate correctly.
 */

.section .text
.global _start
_start:
    movabs $0xC0FFEE, %rax
    movabs $0x20000010000, %rbx   /* matches usermode::USER_STACK_ADDR */
    mov %rax, (%rbx)
    int $0x80
.hang:
    pause
    jmp .hang
