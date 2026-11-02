/* prog_surf.s -- a "surface": receives key events that the user-space router chose to send it. First message from init = {expected event count};
 * then it RECVs that many 16-byte events and records them. DATA: 0x08 expected  0x10 received  0x18 last message length
 * events at 0x100 + 16*i: {code (u16 in a u64), pressed}
 */
.set DATA, 0x40000000000
.section .text
.global _start
_start:
    movabs $DATA, %rbx
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov 0x200(%rbx), %r14; mov %r14, 0x08(%rbx)
.next:
    cmp %r14, 0x10(%rbx); jae .end
    mov $2, %rax; xor %edi, %edi; lea 0x300(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov %rax, 0x18(%rbx)
    mov 0x10(%rbx), %rcx; shl $4, %rcx
    movzwl 0x300(%rbx), %eax; mov %rax, 0x100(%rbx,%rcx)
    movzbl 0x302(%rbx), %eax; mov %rax, 0x108(%rbx,%rcx)
    incq 0x10(%rbx); jmp .next
.end:
    mov $3, %rax; int $0x84
.hang:
    jmp .hang
