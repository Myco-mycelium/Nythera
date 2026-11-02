/* prog_typist.s -- a text-field SURFACE (Phase 7 + 9): receives key events from the user-space input router and draws the typed letters, in the project's
 * 5x7 font at 2x, into its own FrameBuffer band. Needs both a FrameBuffer capability and an endpoint a router can send to.
 * Protocol: msg1 = SEND {expected event count} carrying a WRITE|READ FrameBuffer capability (any band); then it RECVs that many 16-byte events {code u16, pressed u8, ...}.
 * Layout (HIG): systemBackground F2F2F7 band, a 480x44 text field at (16,16) -- 1-px separator C6C6C8 border, white fill, 8-pt grid -- text in label black,
 * 8 pt inside the field edge (x = 24), vertically centred (y = 31). A pressed LETTER key (PS/2 set-1 make code, `keymap` in font5x7.inc) appends its glyph; releases and non-letters
 * (Right arrow) only get logged.
 * DATA: 0x08 expected  0x10 received  0x18 last message length  0x20 FB_INFO rc  0x28 FB_MAP rc  0x30 FB handle  0x38 cursor x  0x80 hash of the whole band
 *   events at 0x100 + 16*i: {code, pressed}   FB_INFO struct at 0x600
 */
.set DATA, 0x40000000000
.set FBWIN, 0x40000200000
.set FBI, 0x600
.section .text
.global _start
_start:
    movabs $DATA, %rbx
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov 0x200(%rbx), %r14; mov %r14, 0x08(%rbx); mov %rdx, 0x30(%rbx)
    cmp $-1, %rdx; je ty_exit
    mov $20, %rax; mov 0x30(%rbx), %rdi; lea FBI(%rbx), %rsi; int $0x84; mov %rax, 0x20(%rbx)
    mov $21, %rax; mov 0x30(%rbx), %rdi; movabs $FBWIN, %rsi; int $0x84; mov %rax, 0x28(%rbx)
    mov 0x20(%rbx), %rax; or 0x28(%rbx), %rax; jnz ty_exit
    mov (FBI+0x14)(%rbx), %ecx; sub (FBI+0x10)(%rbx), %ecx; mov (FBI)(%rbx), %edx; xor %edi, %edi; xor %esi, %esi; mov $0xF2F2F7, %r8d; call fillrect
    mov $16, %edi; mov $16, %esi; mov $480, %edx; mov $44, %ecx; mov $0xC6C6C8, %r8d; call fillrect
    mov $17, %edi; mov $17, %esi; mov $478, %edx; mov $42, %ecx; mov $0xFFFFFF, %r8d; call fillrect
    movq $24, 0x38(%rbx)
ty_next:
    mov 0x10(%rbx), %rax; cmp 0x08(%rbx), %rax; jae ty_end
    mov $2, %rax; xor %edi, %edi; lea 0x300(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov %rax, 0x18(%rbx)
    mov 0x10(%rbx), %rcx; shl $4, %rcx
    movzwl 0x300(%rbx), %r9d; mov %r9, 0x100(%rbx,%rcx)
    movzbl 0x302(%rbx), %eax; mov %rax, 0x108(%rbx,%rcx)
    incq 0x10(%rbx)
    cmp $1, %eax; jne ty_next                              /* releases are logged only */
    cmp $0x33, %r9d; jae ty_next                           /* extended/unknown codes are not letters */
    lea keymap(%rip), %rax; movzbl (%rax,%r9), %eax
    cmp $0xff, %eax; je ty_next
    add $65, %eax; mov %al, 0x400(%rbx); movb $0, 0x401(%rbx)
    mov 0x38(%rbx), %edi; mov $31, %esi; lea 0x400(%rbx), %rdx; xor %r8d, %r8d; call draw_text
    addq $12, 0x38(%rbx)
    jmp ty_next
ty_end:
    call bandhash; mov %rax, 0x80(%rbx)
ty_exit:
    mov $3, %rax; int $0x84
ty_hang:
    jmp ty_hang
.include "userprog/gfxlib.inc"
