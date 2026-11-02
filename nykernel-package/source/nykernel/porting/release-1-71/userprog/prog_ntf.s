/* prog_ntf.s -- exercises NOTIFY (23), NOTIFY_WAIT (24) and REVOKE (25) -- NPS-003 §3 `notify`, §4.3 owner revocation. It talks to ITS OWN endpoint (handle 0 = owner cap with SEND)
 * and to init, which holds a SEND cap to it and signals it while it is parked.
 * DATA: 0x08 wait on an empty word (non-blocking; expect -12)  0x10 notify 0x1 rc  0x18 notify 0x4 rc  0x20 wait rc  0x28 wait bits (expect 0x5: OR-ed)  0x30 second wait (expect -12: cleared)
 *   0x38 notify with 0 bits (expect -20)  0x40 how many of 20000 notifications returned 0 (a queue would have filled at 4)  0x48 BLOCKING wait rc  0x50 its bits (init sends 0x80)
 *   0x78 notify through a rights-0 capability init handed over (expect -3)  0x58 REVOKE rc (0)  0x60 notify through the revoked cap (expect -4)  0x68 notify on an empty slot (expect -2)  0x70 NOTIFY_WAIT on a non-owned index (expect -2)
 */
.set DATA, 0x40000000000
.section .text
.global _start
_start:
    movabs $DATA, %rbx
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84; mov %rdx, %r13              /* init's endpoint cap, handed over with NO rights */
    mov $23, %rax; mov %r13, %rdi; mov $1, %rsi; int $0x84; mov %rax, 0x78(%rbx)                                       /* NOTIFY needs SEND: expect -3 */
    mov $24, %rax; xor %edi, %edi; xor %esi, %esi; int $0x84; mov %rax, 0x08(%rbx)
    mov $23, %rax; xor %edi, %edi; mov $1, %rsi; int $0x84; mov %rax, 0x10(%rbx)
    mov $23, %rax; xor %edi, %edi; mov $4, %rsi; int $0x84; mov %rax, 0x18(%rbx)
    mov $24, %rax; xor %edi, %edi; xor %esi, %esi; int $0x84; mov %rax, 0x20(%rbx); mov %rdx, 0x28(%rbx)
    mov $24, %rax; xor %edi, %edi; xor %esi, %esi; int $0x84; mov %rax, 0x30(%rbx)
    mov $23, %rax; xor %edi, %edi; xor %esi, %esi; int $0x84; mov %rax, 0x38(%rbx)
    mov $20000, %r12d
1:  mov $23, %rax; xor %edi, %edi; mov $2, %rsi; int $0x84
    test %rax, %rax; jnz 2f
    incq 0x40(%rbx)
2:  dec %r12d; jnz 1b
    mov $24, %rax; xor %edi, %edi; xor %esi, %esi; int $0x84                      /* drain the 0x2 bit the flood left */
    mov $24, %rax; xor %edi, %edi; mov $1, %rsi; int $0x84; mov %rax, 0x48(%rbx); mov %rdx, 0x50(%rbx)   /* BLOCKING: parks until init signals 0x80 */
    mov $25, %rax; xor %edi, %edi; int $0x84; mov %rax, 0x58(%rbx)
    mov $23, %rax; xor %edi, %edi; mov $1, %rsi; int $0x84; mov %rax, 0x60(%rbx)
    mov $23, %rax; mov $100, %rdi; mov $1, %rsi; int $0x84; mov %rax, 0x68(%rbx)
    mov $24, %rax; mov $9, %rdi; xor %esi, %esi; int $0x84; mov %rax, 0x70(%rbx)
    mov $300, %r12d                                                                  /* stay alive (yielding) so init's stale cap meets a LIVE, revoked endpoint: -4, not -5 */
3:  xor %eax, %eax; int $0x84; dec %r12d; jnz 3b
    mov $3, %rax; int $0x84
.hang:
    jmp .hang
