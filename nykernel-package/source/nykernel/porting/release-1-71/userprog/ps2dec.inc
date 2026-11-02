/* ps2dec.inc -- the PS/2 DECODERS, in USER SPACE (increment 1-50; until 1-49 they were kernel code in experimental/input.rs -- RC-17).
 * The kernel's INPUT_POLL now returns one RAW byte per call in the 16-byte record at 0x300(%rbx): byte at +0, device at +3 (0 = keyboard, 1 = mouse), ticks at +8.
 * ps2_decode turns it back into the record the programs always worked with and returns eax = 1 when that completes an event, 0 when the byte only advanced the decoder:
 *   keyboard (scancode set 1: bit 7 = release, 0xE0 = extended prefix -> code 0xE000 | base, 0xE1 = unsupported sequence, ignored): code u16 @+0, pressed u8 @+2, kind 0 @+3
 *   mouse (3-byte packets; byte 0 has bit 3 always set and is used to resynchronise; overflow packets are dropped, never clamped): buttons @+2, kind 1 @+3, dx i16 @+4, dy i16 @+6 (y UP)
 * Needs from the including program: %rbx = DATA; 0x78 raw-byte counter, 0x80 extended-prefix flag, 0x88 mouse byte count, 0x90..0x92 mouse bytes, 0xa0 tick of the previous mouse byte. Clobbers rax, rcx, rdx only. */
ps2_decode:
    incq 0x78(%rbx)
    movzbl 0x300(%rbx), %eax
    cmpb $1, 0x303(%rbx); je 6f
    cmp $0xe0, %eax; jne 1f
    movq $1, 0x80(%rbx); xor %eax, %eax; ret                               /* extended prefix */
1:  cmp $0xe1, %eax; jne 2f
    movq $0, 0x80(%rbx); xor %eax, %eax; ret                               /* Pause/Break: unsupported, ignored */
2:  mov %eax, %ecx; shr $7, %ecx; xor $1, %ecx                             /* pressed = !(byte & 0x80) */
    and $0x7f, %eax
    cmpq $0, 0x80(%rbx); je 3f; or $0xe000, %eax
3:  movq $0, 0x80(%rbx)
    mov %ax, 0x300(%rbx); mov %cl, 0x302(%rbx); movb $0, 0x303(%rbx); mov $1, %eax; ret
6:  mov 0x88(%rbx), %rcx                                                    /* mouse */
    mov 0x308(%rbx), %rdx; mov 0xa0(%rbx), %rax; mov %rdx, 0xa0(%rbx)        /* 1-64: a pause of more than 2 timer ticks (~110 ms) inside a packet means a byte was lost: restart at the next status byte (bytes of one packet arrive within microseconds) */
    sub %rax, %rdx; cmp $2, %rdx; jbe 10f; xor %ecx, %ecx; movq $0, 0x88(%rbx)
10: movzbl 0x300(%rbx), %eax
    test %rcx, %rcx; jnz 7f; test $8, %al; jnz 7f
    xor %eax, %eax; ret                                                    /* out of sync: wait for a plausible status byte */
7:  mov %al, 0x90(%rbx,%rcx); inc %rcx; cmp $3, %rcx; jae 8f
    mov %rcx, 0x88(%rbx); xor %eax, %eax; ret
8:  movq $0, 0x88(%rbx)
    movzbl 0x90(%rbx), %edx
    test $0xc0, %dl; jz 9f
    xor %eax, %eax; ret                                                    /* overflow: unusable */
9:  movzbl 0x91(%rbx), %eax; test $0x10, %dl; jz 4f; sub $256, %eax
4:  mov %ax, 0x304(%rbx)
    movzbl 0x92(%rbx), %eax; test $0x20, %dl; jz 5f; sub $256, %eax
5:  mov %ax, 0x306(%rbx)
    and $7, %edx; mov %dl, 0x302(%rbx); movb $1, 0x303(%rbx); movw $0, 0x300(%rbx); mov $1, %eax; ret
