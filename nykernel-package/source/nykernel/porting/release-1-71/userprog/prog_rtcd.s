/* prog_rtcd.s -- `rtcd`: the real-time-clock service of the live session (increment 1-52), an UNPRIVILEGED USER-SPACE DRIVER (ADR-0006).
 * Its only hardware authority is an I/O-port capability narrowed to [0x70, 0x72) -- the CMOS index / data ports -- which it must IO_ENABLE (syscall 12) before ring 3 may touch them.
 * Start-up from `livesh`: msg1 = that narrowed capability; msg2 = a SEND capability to the shell.
 * It polls the CMOS clock (status A bit 7 = update in progress: skip; status B bit 2 = binary, else BCD; bit 1 = 24 h, else 12 h with the PM flag in bit 7 of the hour) every 256 yields and, when the
 * HOUR:MINUTE changed, CALLs the shell with the message {14 TIME, a = hour << 8 | minute, 0}; the reply is ignored. DATA: 0x60 IO_ENABLE rc, 0x68 messages sent, 0x70 last key sent, 0x78 CALL failures,
 * 0x80 clock reads skipped (update in progress). A ring-3 #GP (a port outside the window) would arrive as an upcall; there is none to fix up, so the task would end (the window is fixed in the code). */
.set DATA, 0x40000000000
.section .text
.global _start
_start:
    movabs $DATA, %rbx
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov %rdx, %r14                                        /* the narrowed I/O-port capability */
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov %rdx, %r13                                        /* SEND capability to the shell */
    mov $12, %rax; mov %r14, %rdi; int $0x84; mov %rax, 0x60(%rbx)
    movq $-1, 0x70(%rbx)
.again:
    mov $256, %r12d
1:  mov $0, %rax; int $0x84; dec %r12d; jnz 1b           /* yield 256 times between clock reads */
    mov $0x0a, %al; out %al, $0x70; in $0x71, %al
    test $0x80, %al; jz 2f; incq 0x80(%rbx); jmp .again   /* an update is in progress: the registers may be inconsistent */
2:  mov $0x0b, %al; out %al, $0x70; in $0x71, %al; mov %al, %r15b           /* status B */
    mov $0x04, %al; out %al, $0x70; in $0x71, %al; movzbl %al, %ecx        /* hour */
    mov $0x02, %al; out %al, $0x70; in $0x71, %al; movzbl %al, %edx        /* minute */
    test $4, %r15b; jnz 3f                                /* BCD -> binary */
    mov %ecx, %eax; and $0x80, %eax; mov %eax, %esi; and $0x7f, %ecx    /* keep the PM flag of 12-hour mode out of the conversion */
    mov %ecx, %eax; shr $4, %eax; imul $10, %eax; and $15, %ecx; add %eax, %ecx
    mov %edx, %eax; shr $4, %eax; imul $10, %eax; and $15, %edx; add %eax, %edx
    jmp 4f
3:  xor %esi, %esi
    test $2, %r15b; jnz 4f; mov %ecx, %esi; and $0x80, %esi; and $0x7f, %ecx          /* binary, 12-hour: the PM flag */
4:  test $2, %r15b; jnz 5f                                /* 12-hour clock: 12 AM = 0, 12 PM = 12, 1..11 PM = 13..23 */
    cmp $12, %ecx; jb 6f; xor %ecx, %ecx                  /* 12 -> 0 */
6:  test %esi, %esi; jz 5f; add $12, %ecx
5:  cmp $23, %ecx; ja .again; cmp $59, %edx; ja .again   /* garbage: never forwarded */
    mov %ecx, %eax; shl $8, %eax; or %edx, %eax
    cmp 0x70(%rbx), %rax; je .again
    mov %rax, 0x70(%rbx)
    movq $14, 0x100(%rbx); mov %rax, 0x108(%rbx); movq $0, 0x110(%rbx)
    mov $9, %rax; mov %r13, %rdi; lea 0x100(%rbx), %rsi; mov $24, %rdx; lea 0x118(%rbx), %r10; mov $8, %r8; int $0x84
    test %rax, %rax; jns 7f; incq 0x78(%rbx); movq $-1, 0x70(%rbx)      /* 1-62: a failed CALL (desk not receiving yet) must be retried at the next read, not lost until the minute changes */
7:  incq 0x68(%rbx); jmp .again
