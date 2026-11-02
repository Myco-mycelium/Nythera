/* prog_kbdrt.s -- a USER-SPACE keyboard router for the adaptive shell (increment 1-38). Holds the Input capability (kernel hands out raw key events only to its holder), turns
 * key events into the shell's event protocol and CALLs the shell: Tab = next focus (Shift+Tab = previous), Right / Left arrows = next / previous, Enter = activate, Esc = BACK,
 * F2 = toggle the dark theme, F3 = next text size, F4 = notification, F5 = next window size, F6 = Home, F7 = Recents, F8 = navigation mode, letters / Space / Backspace = TEXT (the search field in FIND). Releases and every other key are ignored (counted). Init sends two capabilities: (1) Input (READ), (2) the shell's endpoint (SEND).
 * When the queue is empty it sends the shell a quit event and exits.
 * DATA: 0x08 polled (DECODED events; 1-50: 0x78 = raw bytes polled, decoder state at 0x80..0x92)  0x10 forwarded  0x18 ignored  0x20 shell handle  0x28 input handle  0x30 final shift state  0x38 final dark state  0x40 CALL failures
 *   0x500 + 8*i: reply code of the i-th forwarded event (first 200)
 *   0x48 pointer x  0x50 pointer y  0x58 left button down  0x60 gesture start x  0x68 gesture state (0 none, 1 left edge, 2 right edge, 3 fired, 4 top edge, 5 bottom edge)  0x70 gesture start y  0xa0 (ps2dec) previous mouse byte tick  0xa8 Ctrl held  0xb0 keyboard button down */
.set DATA, 0x40000000000
.section .text
.global _start
_start:
    movabs $DATA, %rbx
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84; mov %rdx, 0x28(%rbx)
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84; mov %rdx, 0x20(%rbx)
    xor %r13d, %r13d                                       /* shift */
    xor %r14d, %r14d                                       /* dark */
    movq $512, 0x48(%rbx); movq $96, 0x50(%rbx)            /* pointer starts in the middle of the window */
.ifdef LIVE
    movq $384, 0x50(%rbx)                                  /* ... of the full-screen window (1-51) */
.endif
.poll:
    xor %r9d, %r9d                                         /* the message's b field (0 for keys) */
    mov $22, %rax; mov 0x28(%rbx), %rdi; lea 0x300(%rbx), %rsi; int $0x84
    cmp $1, %rax; jne .done
    call ps2_decode; test %eax, %eax; jz .poll                 /* 1-50: the kernel hands out RAW bytes; the decoding is done here (ps2dec.inc) */
    incq 0x08(%rbx)
    cmpb $1, 0x303(%rbx); je .ptr                          /* a pointer event (kind byte 1): dx at 0x304, dy at 0x306 (i16), buttons at 0x302 */
    movzwl 0x300(%rbx), %eax; movzbl 0x302(%rbx), %ecx
    cmp $0x2a, %eax; je .shift
    cmp $0x36, %eax; je .shift
.ifdef DESK
    cmp $0x1d, %eax; je .ctrl                              /* 1-64: KEYBOARD POINTER (for machines whose mouse is not PS/2: USB-only, tablet, touchpad-less): hold Ctrl and use the arrow keys to move the pointer 24 px per press, Ctrl+Enter = left button (held until Enter is released, so Ctrl+arrows then drags) */
    cmp $0xe01d, %eax; je .ctrl
    cmp $0x1c, %eax; jne 20f
    test %ecx, %ecx; jnz 21f
    cmpq $0, 0xb0(%rbx); je .ign                           /* Enter released: ends a keyboard button press (if one is down) */
    movq $0, 0xb0(%rbx); movw $0, 0x304(%rbx); movw $0, 0x306(%rbx); movb $0, 0x302(%rbx); jmp .ptr
21: cmpq $0, 0xa8(%rbx); je 20f                            /* Enter without Ctrl is the normal Enter */
    cmpq $0, 0xb0(%rbx); jne .poll                         /* key repeat while held */
    movq $1, 0xb0(%rbx); movw $0, 0x304(%rbx); movw $0, 0x306(%rbx); movb $1, 0x302(%rbx); jmp .ptr
20:
.endif
    test %ecx, %ecx; jz .ign                               /* releases are ignored */
.ifdef DESK
    cmpq $0, 0xa8(%rbx); je 22f
    xor %edx, %edx; xor %ecx, %ecx
    cmp $0xe04b, %eax; jne 23f; mov $-24, %edx; jmp 27f    /* Ctrl+Left */
23: cmp $0xe04d, %eax; jne 24f; mov $24, %edx; jmp 27f     /* Ctrl+Right */
24: cmp $0xe048, %eax; jne 25f; mov $24, %ecx; jmp 27f     /* Ctrl+Up (PS/2 dy is up-positive) */
25: cmp $0xe050, %eax; jne 22f; mov $-24, %ecx             /* Ctrl+Down */
27: mov %dx, 0x304(%rbx); mov %cx, 0x306(%rbx); mov 0x58(%rbx), %rax; mov %al, 0x302(%rbx); jmp .ptr
22:
.endif
.ifdef DESK
    cmp $0xe05b, %eax; jne 0f; mov $16, %esi; mov $1, %edx; jmp .send     /* 1-55: the Super (Windows) key = event 16 */
0:
.endif
    cmp $0x0f, %eax; jne 1f
    mov $1, %edx; test %r13d, %r13d; jz .k2; mov $2, %edx; jmp .k2          /* Tab / Shift+Tab */
1:  cmp $0xe04d, %eax; jne 2f; mov $1, %edx; jmp .k2                       /* Right */
2:  cmp $0xe04b, %eax; jne 3f; mov $2, %edx; jmp .k2                       /* Left */
3:  cmp $0x1c, %eax; jne 4f; mov $3, %edx; jmp .k2                         /* Enter */
4:  cmp $0x01, %eax; jne 5f; mov $3, %esi; xor %edx, %edx; jmp .send       /* Esc = BACK */
5:  cmp $0x3c, %eax; jne 6f
    xor $1, %r14d; mov $4, %esi; mov $2, %edx; jmp .send                    /* F2 = toggle theme (THEME a=2: the shell owns the state, the shade tile can change it too; r14 only counts the parity) */
6:  cmp $0x3d, %eax; jne 66f; mov $8, %esi; mov $3, %edx; jmp .send        /* F3 = next text size (FONTSCALE a=3: the shell owns the scale) */
66: cmp $0x3e, %eax; jne 68f; mov $9, %esi; mov $2, %edx; jmp .send      /* F4 = toggle the notification */
68: cmp $0x3f, %eax; jne 69f; mov $10, %esi; mov $3, %edx; jmp .send     /* F5 = next window size (WINDOW a=3: expanded -> medium -> compact; full-screen shell only) */
69: cmp $0x40, %eax; jne 70f; mov $11, %esi; mov $1, %edx; jmp .send     /* F6 = Home (NAV a=1) */
70: cmp $0x41, %eax; jne 71f; mov $11, %esi; mov $2, %edx; jmp .send     /* F7 = Recents (NAV a=2) */
71: cmp $0x42, %eax; jne 67f; mov $12, %esi; mov $2, %edx; jmp .send     /* F8 = toggle the navigation mode (NAVMODE a=2: gestures <-> 3 buttons) */
67: mov $5, %esi
    cmp $0x0e, %eax; jne 7f; mov $255, %edx; jmp .send                     /* Backspace */
7:  cmp $0x39, %eax; jne 8f; mov $26, %edx; jmp .send                       /* Space */
8:  cmp $0x33, %eax; jae .ign
    lea keymap(%rip), %rdx; movzbl (%rdx,%rax), %edx; cmp $0xff, %edx; je .ign   /* letters: the glyph index of the shared keymap; lowercase (+32) unless Shift is held (r13d), as on an Android keyboard */
    test %r13d, %r13d; jnz .send; add $32, %edx
    jmp .send
.k2: mov $2, %esi; jmp .send
.shift:
    mov %ecx, %r13d; jmp .poll
.ctrl:
    movzbl %cl, %ecx; mov %rcx, 0xa8(%rbx); jmp .poll
.ign:
    incq 0x18(%rbx); jmp .poll
.send:                                                      /* esi = type, edx = a, r9 = b */
    call send_msg
    jmp .poll
.ptr:                                                       /* PS/2 mouse: integrate the movement (y grows downward; PS/2 dy is up-positive), clamp to the 1024x192 window, MOVE when the position changed, POINT on a left-button press */
    movswq 0x304(%rbx), %rax; add 0x48(%rbx), %rax
    test %rax, %rax; jns 1f; xor %eax, %eax
1:  cmp $1023, %rax; jbe 2f; mov $1023, %eax
2:  mov %rax, %r8                                          /* new x */
    movswq 0x306(%rbx), %rdx; mov 0x50(%rbx), %rax; sub %rdx, %rax
    test %rax, %rax; jns 3f; xor %eax, %eax
3:
.ifdef LIVE
    cmp $767, %rax; jbe 4f; mov $767, %eax                  /* the live session's window is the whole 768-row screen (1-51) */
.else
    cmp $191, %rax; jbe 4f; mov $191, %eax
.endif
4:  mov %rax, %r15                                         /* new y */
    cmp 0x48(%rbx), %r8; jne 5f; cmp 0x50(%rbx), %r15; je 6f
5:  mov %r8, 0x48(%rbx); mov %r15, 0x50(%rbx); mov 0x308(%rbx), %rax; mov %rax, 0x98(%rbx)          /* 1-53: the tick of the last MOVEMENT (the pause before a release is measured from it) */
    mov $6, %esi; mov %r8, %rdx; mov %r15, %r9; call send_msg          /* MOVE(x, y) */
6:  movzbl 0x302(%rbx), %eax; and $1, %eax; mov 0x58(%rbx), %rcx; mov %rax, 0x58(%rbx)
    test %eax, %eax; jnz 7f
.ifdef DESK
    test %rcx, %rcx; jz .poll                              /* 1-55 (desktop router): no edge gestures; the button release is an event of its own (drag ends) */
    mov $15, %esi; mov 0x48(%rbx), %rdx; mov 0x50(%rbx), %r9; call send_msg; jmp .poll
.endif
    cmpq $5, 0x68(%rbx); jne 11f                           /* release of a bottom-edge swipe: >= 64 px up = Home, >= 64 px up and held still for >= 14 timer ticks (~0.77 s) = Recents */
    mov 0x70(%rbx), %r8; sub 0x50(%rbx), %r8; movq $0, 0x68(%rbx)
    cmp $64, %r8; jl .poll
    mov $13, %esi; mov $1, %edx; mov 0x308(%rbx), %rax; sub 0x98(%rbx), %rax; cmp $14, %rax; jb 12f; mov $2, %edx          /* 1-53: still for >= 14 ticks (about 0.77 s) before the release = HOLD = Recents, else Home */
12: xor %r9d, %r9d; call send_msg; jmp .poll
11: movq $0, 0x68(%rbx); jmp .poll                         /* release: any edge gesture ends (1-42) */
7:  test %ecx, %ecx; jnz .held
.ifdef DESK
    jmp .click                                             /* press = POINT anywhere, including the screen edges */
.endif
    mov 0x48(%rbx), %rax                                   /* press edge: inside the 16-px edge zone it starts a back gesture and is NOT a click (system gesture area) */
    cmp $15, %rax; ja 8f; movq $1, 0x68(%rbx); mov %rax, 0x60(%rbx); jmp .poll
8:  cmp $1008, %rax; jb 9f; movq $2, 0x68(%rbx); mov %rax, 0x60(%rbx); jmp .poll
9:  cmpq $15, 0x50(%rbx); ja 10f                      /* top edge zone (y <= 15, outside the side zones): a swipe-down gesture opens the quick-settings shade; the press is not a click */
    movq $4, 0x68(%rbx); mov 0x50(%rbx), %rax; mov %rax, 0x70(%rbx); jmp .poll
.ifdef LIVE
10: cmpq $752, 0x50(%rbx); jb .click                        /* bottom edge zone (y >= 752, full-screen live window): the press is ALSO sent as a click (a 3-button bar button lives here) and starts the Home / Recents swipe-up gesture, which the shell ignores in 3-button mode */
    mov $1, %esi; mov 0x48(%rbx), %rdx; mov 0x50(%rbx), %r9; call send_msg
    movq $5, 0x68(%rbx); mov 0x50(%rbx), %rax; mov %rax, 0x70(%rbx); mov 0x308(%rbx), %rax; mov %rax, 0x98(%rbx); jmp .poll
.else
10: jmp .click
.endif
.click:
    movq $0, 0x68(%rbx)
    mov $1, %esi; mov 0x48(%rbx), %rdx; mov 0x50(%rbx), %r9; call send_msg   /* POINT(x, y) */
    jmp .poll
.held:                                                      /* button still down: an edge gesture fires BACK once, after 64 px of inward travel */
.ifdef DESK
    jmp .poll                                              /* dragging is the shell's business (it sees MOVE while the button is down) */
.endif
    mov 0x68(%rbx), %rax
    cmp $4, %rax; jne 2f
    mov 0x50(%rbx), %rdx; sub 0x70(%rbx), %rdx; cmp $64, %rdx; jl .poll    /* top edge: 64 px of downward travel opens the shade, once */
    movq $3, 0x68(%rbx); mov $7, %esi; mov $1, %edx; xor %r9d, %r9d; call send_msg; jmp .poll   /* SHADE(open) */
2:  cmp $1, %rax; jne 1f
    mov 0x48(%rbx), %rdx; sub 0x60(%rbx), %rdx; cmp $64, %rdx; jl .poll; jmp .fire
1:  cmp $2, %rax; jne .poll
    mov 0x60(%rbx), %rdx; sub 0x48(%rbx), %rdx; cmp $64, %rdx; jl .poll
.fire:
    movq $3, 0x68(%rbx); mov $3, %esi; xor %edx, %edx; xor %r9d, %r9d; call send_msg   /* BACK */
    jmp .poll
send_msg:                                                   /* esi = type, rdx = a, r9 = b: CALL the shell and log the reply code (at most 100) */
    mov %rsi, 0x100(%rbx); mov %rdx, 0x108(%rbx); mov %r9, 0x110(%rbx)
    call do_call
    mov 0x10(%rbx), %rcx; cmp $200, %rcx; jae 1f
    mov 0x118(%rbx), %rax; mov %rax, 0x500(%rbx,%rcx,8)
1:  incq 0x10(%rbx); ret
.done:
.ifdef LIVE
    mov $0, %rax; int $0x84; jmp .poll                      /* live session: never quit -- yield and keep reading the real keyboard */
.endif
    mov %r13, 0x30(%rbx); mov %r14, 0x38(%rbx)
    movq $0, 0x100(%rbx); movq $0, 0x108(%rbx); movq $0, 0x110(%rbx); call do_call      /* quit */
    mov $3, %rax; int $0x84
.hang:
    jmp .hang
do_call:                                                    /* CALL the shell with the 24-byte message at 0x100; the reply lands at 0x118 */
    mov $9, %rax; mov 0x20(%rbx), %rdi; lea 0x100(%rbx), %rsi; mov $24, %rdx; lea 0x118(%rbx), %r10; mov $8, %r8; int $0x84
    test %rax, %rax; jns 1f; incq 0x40(%rbx)
1:  ret
.include "userprog/font5x7.inc"
.section .text
.include "userprog/ps2dec.inc"
