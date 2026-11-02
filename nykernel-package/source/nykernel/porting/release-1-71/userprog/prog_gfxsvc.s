/* prog_gfxsvc.s -- a USER-SPACE display client/service (Phase 7: the display belongs to user space; the kernel only hands out a FrameBuffer capability).
 * Protocol from init: msg1 = SEND {role byte} with (maybe) a FrameBuffer capability; msg2 = CALL {go} -> reply {status}.
 *   role 1 = WRITER: FB_INFO + FB_MAP its row window (the kernel ZEROES it first), then draws a tab-bar/button layout following the Apple HIG rules
 *            used throughout this project: 8-pt grid spacing, 44-pt minimum touch targets, system colours (systemBackground F2F2F7, separator C6C6C8,
 *            systemBlue 007AFF for the selected/primary item, systemGray 8E8E93 for the rest). Text (1-28): an original 5x7 bitmap font (userprog/font5x7.inc, generated from tools/font5x7.txt), drawn at 2x: a large title and the button label, in the HIG `label` colour (black) for contrast.
 *   role 2 = READER: maps the SAME window through a READ-ONLY capability and samples the pixels the writer drew.
 *   role 4 = DIRTY writer: floods the window with 0xA5A5A5 (so a LATER writer of the same rows can prove the kernel zeroed it before handing it over)
 *   role 5 = a second writer on a DIFFERENT band: floods it with 0x112233 (the first band must stay untouched)
 *   role 3 = a process in a container that does NOT admit FrameBuffer capabilities: init's transfer was refused (-21), it gets no capability at all.
 * 0x80 = hash of the WHOLE band (rows x width, h = h*31 + (pixel & 0xFFFFFF), 32-bit) -- computed by the writer AND the reader; an independent host renderer must reproduce it.
 * DATA: 0x08 len msg1  0x10 role  0x18 handle (-1 = none)  0x20 FB_INFO rc  0x28 FB_MAP rc  0x30 pixel (5,top) right after the map (writer: 0 = cleared)
 *   samples (low 24 bits): 0x38 (5,0)=hairline  0x40 (5,10)=background  0x48 (128,30)=gray icon  0x50 (384,30)=blue icon  0x58 (440,100)=button (clear of the label)
 *   0x60 (5,last row)=background  0x68 (800,60)=background | 0x78 reply status | 0x100 FB_INFO struct {width,height,pitch,bpp,lo,hi} (u32 each)
 */
.set DATA, 0x40000000000
.set FBWIN, 0x40000200000
.set FBI, 0x100
.section .text
.global _start
_start:
    movabs $DATA, %rbx
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov %rax, 0x08(%rbx); mov %rdx, 0x18(%rbx)
    movzbl 0x200(%rbx), %eax; mov %rax, 0x10(%rbx)
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov %rdx, %r12                                  /* reply capability */
    mov 0x18(%rbx), %r14
    cmp $-1, %r14; je .nocap
    mov $20, %rax; mov %r14, %rdi; lea 0x100(%rbx), %rsi; int $0x84; mov %rax, 0x20(%rbx)
    test %rax, %rax; jnz .fail
    mov $21, %rax; mov %r14, %rdi; movabs $FBWIN, %rsi; int $0x84; mov %rax, 0x28(%rbx)
    test %rax, %rax; jnz .fail
    mov 0x114(%rbx), %r13d; sub 0x110(%rbx), %r13d                          /* r13 = rows in the window */
    cmpq $4, 0x10(%rbx); jne 1f                                             /* role 4 = DIRTY writer: floods its window with 0xA5A5A5 and leaves it that way */
    mov 0x100(%rbx), %edx; mov %r13, %rcx; xor %edi, %edi; xor %esi, %esi; mov $0xA5A5A5, %r8d; call fillrect
    jmp .sample
1:  cmpq $5, 0x10(%rbx); jne 1f                                             /* role 5 = second writer (another band): floods it with 0x112233 */
    mov 0x100(%rbx), %edx; mov %r13, %rcx; xor %edi, %edi; xor %esi, %esi; mov $0x112233, %r8d; call fillrect
    jmp .sample
1:  cmpq $1, 0x10(%rbx); jne .sample
    mov $5, %edi; xor %esi, %esi; call pixel; mov %rax, 0x30(%rbx)          /* what the previous owner left: the kernel zeroed it */
    mov 0x114(%rbx), %r13d; sub 0x110(%rbx), %r13d                          /* r13 = rows in the window */
    mov 0x100(%rbx), %edx; mov %r13, %rcx; xor %edi, %edi; xor %esi, %esi; mov $0xF2F2F7, %r8d; call fillrect     /* systemBackground */
    mov 0x100(%rbx), %edx; mov $1, %ecx; xor %edi, %edi; xor %esi, %esi; mov $0xC6C6C8, %r8d; call fillrect        /* separator (1 px hairline) */
    xor %r15d, %r15d
.icons:                                             /* four tab items on a 256-px pitch; each icon 28x28 inside its 44x44 target, centred */
    cmp $4, %r15d; jae .button
    mov %r15d, %edi; shl $8, %edi; add $114, %edi
    mov $16, %esi; mov $28, %edx; mov $28, %ecx; mov $0x8E8E93, %r8d
    cmp $1, %r15d; jne 1f
    mov $0x007AFF, %r8d                             /* the selected item: systemBlue */
1:  call fillrect
    inc %r15d; jmp .icons
.button:                                            /* primary button: 176 x 44 (8-pt grid), systemBlue */
    mov $424, %edi; mov $96, %esi; mov $176, %edx; mov $44, %ecx; mov $0x007AFF, %r8d; call fillrect
    mov $16, %edi; mov $72, %esi; lea t_title(%rip), %rdx; xor %r8d, %r8d; call draw_text                          /* large title, label colour, on the 8-pt grid */
    mov $489, %edi; mov $111, %esi; lea t_btn(%rip), %rdx; xor %r8d, %r8d; call draw_text                         /* button label, centred in the 176x44 button */
.sample:
    mov $5, %edi; xor %esi, %esi;   call pixel; mov %rax, 0x38(%rbx)
    mov $5, %edi; mov $10, %esi;    call pixel; mov %rax, 0x40(%rbx)
    mov $128, %edi; mov $30, %esi;  call pixel; mov %rax, 0x48(%rbx)
    mov $384, %edi; mov $30, %esi;  call pixel; mov %rax, 0x50(%rbx)
    mov $440, %edi; mov $100, %esi; call pixel; mov %rax, 0x58(%rbx)
    mov 0x114(%rbx), %esi; sub 0x110(%rbx), %esi; dec %esi; mov $5, %edi; call pixel; mov %rax, 0x60(%rbx)
    mov $800, %edi; mov $60, %esi;  call pixel; mov %rax, 0x68(%rbx)
    call bandhash; mov %rax, 0x80(%rbx)
    xor %eax, %eax; jmp .reply
.nocap:
    mov $-1, %rax; jmp .reply
.fail:
.reply:
    mov %rax, 0x78(%rbx); mov %rax, 0x400(%rbx)
    mov $10, %rax; mov %r12, %rdi; lea 0x400(%rbx), %rsi; mov $8, %rdx; int $0x84
    mov $3, %rax; int $0x84
.hang:
    jmp .hang

t_title: .asciz "NYRQIS SETTINGS"
t_btn:   .asciz "OPEN"
.include "userprog/gfxlib.inc"
