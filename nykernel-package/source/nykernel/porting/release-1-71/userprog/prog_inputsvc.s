/* prog_inputsvc.s -- a USER-SPACE input router (Phase 9: the kernel only hands raw key events to the holder of an Input capability; deciding WHO
 * receives them is user-space policy). Init sends three messages, each carrying one capability: (1) the Input capability, (2) the endpoint of surface A,
 * (3) the endpoint of surface B, (4) a second Input capability with NO rights. The router polls INPUT_POLL (syscall 22) until the queue is empty and forwards every event as a 16-byte SEND to the
 * FOCUSED surface (A at start). Tab (key 0x000f): the press toggles focus, press and release are both swallowed.
 * DATA: 0x08 polled (DECODED events; 1-50: 0x78 = raw bytes polled, decoder state at 0x80..0x92)  0x10 sent to A  0x18 sent to B  0x20 swallowed (Tab)  0x28 final focus (0=A 1=B)  0x30 SEND failures other than queue-full
 *   0x38 input handle  0x40 A handle  0x48 B handle  0x50 INPUT_POLL on an ENDPOINT handle (expect -16)  0x58 INPUT_POLL on an empty slot (expect -2)
 */
.set DATA, 0x40000000000
.section .text
.global _start
_start:
    movabs $DATA, %rbx
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84; mov %rdx, 0x38(%rbx)
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84; mov %rdx, 0x40(%rbx)
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84; mov %rdx, 0x48(%rbx)
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84; mov %rdx, 0x60(%rbx)
    mov $22, %rax; mov 0x60(%rbx), %rdi; lea 0x300(%rbx), %rsi; int $0x84; mov %rax, 0x68(%rbx)     /* the rights-0 copy: NoRight (-3), nothing popped */
    mov $22, %rax; mov 0x40(%rbx), %rdi; lea 0x300(%rbx), %rsi; int $0x84; mov %rax, 0x50(%rbx)
    mov $22, %rax; mov $127, %rdi; lea 0x300(%rbx), %rsi; int $0x84; mov %rax, 0x58(%rbx)
    xor %r13d, %r13d                                       /* focus */
.poll:
    mov $22, %rax; mov 0x38(%rbx), %rdi; lea 0x300(%rbx), %rsi; int $0x84
    cmp $1, %rax; jne .done
    call ps2_decode; test %eax, %eax; jz .poll                 /* 1-50: raw bytes from the kernel, decoded here (ps2dec.inc) */
    incq 0x08(%rbx)
    movzwl 0x300(%rbx), %eax
    cmp $0x0f, %eax; jne .route
    incq 0x20(%rbx)
    cmpb $1, 0x302(%rbx); jne .poll
    xor $1, %r13d; jmp .poll
.route:
    mov 0x40(%rbx), %rdi; test %r13d, %r13d; jz 1f
    mov 0x48(%rbx), %rdi
1:  mov $1, %rax; lea 0x300(%rbx), %rsi; mov $16, %rdx; mov $-1, %r10; xor %r8d, %r8d; int $0x84
    cmp $-7, %rax; jne 2f
    mov $0, %rax; int $0x84; jmp .route                    /* queue full: yield and retry */
2:  test %rax, %rax; jz 3f
    incq 0x30(%rbx); jmp .poll
3:  test %r13d, %r13d; jz 4f
    incq 0x18(%rbx); jmp .poll
4:  incq 0x10(%rbx); jmp .poll
.done:
    mov %r13, 0x28(%rbx)
    mov $3, %rax; int $0x84
.hang:
    jmp .hang
.include "userprog/ps2dec.inc"
