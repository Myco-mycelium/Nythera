/* prog_shell.s -- NDL-1 adaptive shell renderer (increment 1-35). A USER-SPACE display client: renders the display list for the window-width class of the
 * window it was given (Android/Material classes: compact < 600 dp, medium 600..839, expanded >= 840) into its FrameBuffer row window.
 * Protocol from init: msg1 = SEND {u64 window width in dp} with a FrameBuffer capability; msg2 = CALL {go} -> reply {status}.
 * Rejects a width < 320 or wider than the framebuffer (status -20) WITHOUT drawing. The kernel zeroes the window at FB_MAP, so pixels right of the window stay 0.
 * DATA: 0x10 width  0x18 handle  0x20 FB_INFO rc  0x28 FB_MAP rc  0x30 pixel(5,0) right after the map  0x38 class (0/1/2, -1 rejected)  0x40 pixel(2,2)
 *   0x48 pixel(W-1,100)  0x50 pixel(W+4,100) or -1 when that is off the row  0x58 records drawn  0x78 reply status  0x80 band hash  0x100 FB_INFO */
.set DATA, 0x40000000000
.set FBWIN, 0x40000200000
.set FBI, 0x100
.section .text
.global _start
_start:
    movabs $DATA, %rbx
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov %rdx, 0x18(%rbx)
    mov 0x200(%rbx), %rax; mov %rax, 0x10(%rbx)
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov %rdx, 0x408(%rbx)                           /* reply capability */
    movq $-1, 0x38(%rbx); movq $-1, 0x50(%rbx)
    mov 0x18(%rbx), %r14
    cmp $-1, %r14; je .nocap
    mov $20, %rax; mov %r14, %rdi; lea 0x100(%rbx), %rsi; int $0x84; mov %rax, 0x20(%rbx)
    test %rax, %rax; jnz .fail
    mov $21, %rax; mov %r14, %rdi; movabs $FBWIN, %rsi; int $0x84; mov %rax, 0x28(%rbx)
    test %rax, %rax; jnz .fail
    mov $5, %edi; xor %esi, %esi; call pixel; mov %rax, 0x30(%rbx)
    cmpq $3, 0x200(%rbx); jne 3f; movq $1, 0xb0(%rbx)                       /* go = 3 (1-51): a FULL-SCREEN window: the tall display lists, the launcher is the first destination; needs 768 rows */
    mov (FBI+0x14)(%rbx), %ecx; sub (FBI+0x10)(%rbx), %ecx; cmp $768, %ecx; jb .reject
3:  mov 0x10(%rbx), %rax                                                    /* width in dp */
    cmp $320, %rax; jb .reject
    mov 0x100(%rbx), %ecx; cmp %rcx, %rax; ja .reject                       /* wider than the framebuffer */
    xor %edx, %edx                                                          /* class: compact */
    cmp $600, %rax; jb 1f; mov $1, %edx
    cmp $840, %rax; jb 1f; mov $2, %edx
1:  mov %rdx, 0x38(%rbx)
    movabs $0x2d2d3a2d2d, %rax; mov %rax, 0xd8(%rbx); movq $-1, 0xe0(%rbx)                       /* the clock reads "--:--" until `rtcd` has told the time (1-52) */
    xor %eax, %eax; call setscale                                           /* font scale 0: selects the list (index 9 * tall + 3 * scale + class) and keeps it in 0x70: the event loop re-renders */
    mov 0x70(%rbx), %r13
    movq $0, 0x60(%rbx); cmpq $0, 0xb0(%rbx); je 2f; movq $3, 0x60(%rbx)    /* active destination 0 (HOME); a full-screen window starts on the LAUNCHER (3) */
2:  movq $-1, 0x68(%rbx)                                                    /* no keyboard focus */
    call render; mov 0x58(%rbx), %rax; mov %rax, 0x88(%rbx)                  /* records drawn by the first render (HOME state) */
    mov $2, %edi; mov $2, %esi; call pixel; mov %rax, 0x40(%rbx)
    mov 0x10(%rbx), %edi; dec %edi; mov $100, %esi; call pixel; mov %rax, 0x48(%rbx)
    mov 0x10(%rbx), %edi; add $4, %edi; cmp 0x100(%rbx), %edi; jae 5f
    mov $100, %esi; call pixel; mov %rax, 0x50(%rbx)
5:  call bandhash; mov %rax, 0x80(%rbx)
    xor %eax, %eax; call reply_now
    cmpq $2, 0x200(%rbx); je .ev; cmpq $3, 0x200(%rbx); jne .exit           /* `go` value 2 (band) or 3 (full screen) = stay and serve events (pointer / key) */
.ev:                                                                        /* event loop: msg = {type, a, b}; type 0 quit, 1 POINT(x=a,y=b), 2 KEY(a: 1 next, 2 prev, 3 activate, 4 clear focus) */
    mov $2, %rax; xor %edi, %edi; lea 0x200(%rbx), %rsi; mov $64, %rdx; mov $1, %r10; int $0x84
    mov %rdx, 0x408(%rbx)
    mov 0x200(%rbx), %rax; test %rax, %rax; jz .evquit
    cmp $1, %rax; jne .nt1
    mov 0x208(%rbx), %rdi; mov 0x210(%rbx), %rsi; call hit_point               /* rax = action id or -1 */
    movq $-1, 0x68(%rbx)                                                    /* touch / pointer mode hides the focus ring */
    mov %rax, %r12
    cmpq $2, 0x60(%rbx); jne 1f; cmp $-1, %r12; jne 1f; mov $0x501, %r12d       /* the shade is open: a tap outside its tiles closes it (reply 0x501) */
1:  cmp $-1, %r12; je .evdone
    cmp $0x500, %r12; jb 2f; mov %r12d, %eax; call do_act; jmp .evdone         /* a shade action */
2:  cmp $0x100, %r12; jb .evdone
    cmp $0x1ff, %r12; ja .evdone
    mov %r12d, %eax; and $0xff, %eax; mov %rax, 0x60(%rbx); jmp .evdone
.nt1:
    cmp $3, %rax; je .back
    cmp $4, %rax; je .theme
    cmp $5, %rax; je .typ
    cmp $6, %rax; je .move
    cmp $7, %rax; je .shade
    cmp $8, %rax; je .font
    cmp $9, %rax; je .post
    cmp $10, %rax; je .win
    cmp $11, %rax; je .nav
    cmp $12, %rax; je .navmode
    cmp $13, %rax; je .navg
    cmp $14, %rax; je .clk
    jmp .key
.font:                                                                      /* FONTSCALE(a: 0 / 1 / 2 = 100% / 130% / 200%, 3 = next): reply 1 when the scale changed, 0 when it already was that, -1 for a bad value. The display list and every text face follow */
    mov 0x208(%rbx), %rax; mov $-1, %r12; cmp $3, %rax; ja .evdone; jne 1f; mov 0x98(%rbx), %rax; inc %rax; cmp $3, %rax; jb 1f; xor %eax, %eax
1:  xor %r12d, %r12d; cmp 0x98(%rbx), %rax; je .evdone
    call setscale; mov $1, %r12d; jmp .evdone
.win:                                                                       /* WINDOW(a: 0 / 1 / 2 = 1024 / 640 / 360 dp wide = expanded / medium / compact, 3 = next): a full-screen window only. Reply 1 = resized, 0 = already that size, -1 = bad value or not a full-screen window (1-51) */
    mov $-1, %r12; cmpq $0, 0xb0(%rbx); je .evdone
    mov 0x208(%rbx), %rax; cmp $3, %rax; ja .evdone
    mov $2, %ecx; sub 0x38(%rbx), %rcx                                      /* current size index = 2 - class */
    cmp $3, %rax; jne 1f; lea 1(%rcx), %rax; cmp $3, %rax; jb 1f; xor %eax, %eax
1:  xor %r12d, %r12d; cmp %rcx, %rax; je .evdone
    mov $2, %ecx; sub %rax, %rcx; mov %rcx, 0x38(%rbx)                      /* new class */
    lea win_w(%rip), %rdx; movzwl (%rdx,%rax,2), %eax; mov %rax, 0x10(%rbx)
    movq $-1, 0x68(%rbx); mov 0x98(%rbx), %rax; call setscale; mov $1, %r12d; jmp .evdone
.nav:                                                                       /* NAV(a: 1 Home, 2 Recents): the keyboard equivalents of the navigation bar buttons. Reply = the action id of that button, -1 when not a full-screen window or a bad value (1-51) */
    mov $-1, %r12; cmpq $0, 0xb0(%rbx); je .evdone
    mov 0x208(%rbx), %rax; cmp $1, %rax; jb .evdone; cmp $2, %rax; ja .evdone
    add $0x505, %eax; mov %eax, %r12d; call do_act; jmp .evdone
.navmode:                                                                  /* NAVMODE(a: 0 gesture, 1 three buttons, 2 toggle): full-screen window only. Reply 1 = changed, 0 = already so, -1 = bad value / band window (1-51) */
    mov $-1, %r12; cmpq $0, 0xb0(%rbx); je .evdone
    mov 0x208(%rbx), %rax; cmp $2, %rax; ja .evdone; jne 1f; mov 0xd0(%rbx), %rax; xor $1, %eax
1:  xor %r12d, %r12d; cmp 0xd0(%rbx), %rax; je .evdone
    mov %rax, 0xd0(%rbx); movq $-1, 0x68(%rbx); mov $1, %r12d; jmp .evdone
.navg:                                                                      /* NAVG(a: 1 swipe up = Home, 2 swipe up and hold = Recents): the system gesture; acted on only in GESTURE mode. Reply = the action id of the matching button, 0 when ignored (3-button mode), -1 = bad value / band window (1-51) */
    mov $-1, %r12; cmpq $0, 0xb0(%rbx); je .evdone
    mov 0x208(%rbx), %rax; cmp $1, %rax; jb .evdone; cmp $2, %rax; ja .evdone
    xor %r12d, %r12d; cmpq $0, 0xd0(%rbx); jne .evdone
    add $0x505, %eax; mov %eax, %r12d; call do_act; jmp .evdone
.clk:                                                                      /* TIME(a = hour << 8 | minute): the real-time clock (1-52), formatted as "HH:MM" into the clock string at 0xd8 that the status bar draws. Reply 1 = the time changed, 0 = the same, -1 = not a valid time */
    mov 0x208(%rbx), %r8; mov $-1, %r12
    mov %r8, %rax; shr $16, %rax; jnz .evdone
    mov %r8d, %eax; movzbl %ah, %ecx; movzbl %al, %edi
    cmp $23, %ecx; ja .evdone; cmp $59, %edi; ja .evdone
    xor %r12d, %r12d; cmp 0xe0(%rbx), %r8; je .evdone
    mov %r8, 0xe0(%rbx)
    mov %ecx, %eax; xor %edx, %edx; mov $10, %esi; div %esi; add $48, %eax; mov %al, 0xd8(%rbx); add $48, %edx; mov %dl, 0xd9(%rbx)
    movb $58, 0xda(%rbx)
    mov %edi, %eax; xor %edx, %edx; div %esi; add $48, %eax; mov %al, 0xdb(%rbx); add $48, %edx; mov %dl, 0xdc(%rbx); movb $0, 0xdd(%rbx)
    mov $1, %r12d; jmp .evdone
.post:                                                                      /* POST(a: 1 post, 0 clear, 2 toggle) the notification (1-48): reply 1 when it changed, 0 when it already was so, -1 for a bad value. The status-bar bell and the shade's card are drawn from 0xa0 */
    mov 0x208(%rbx), %rax; mov $-1, %r12; cmp $2, %rax; ja .evdone; jne 1f; mov 0xa0(%rbx), %rax; xor $1, %eax
1:  xor %r12d, %r12d; cmp 0xa0(%rbx), %rax; je .evdone
    mov %rax, 0xa0(%rbx); movq $-1, 0x68(%rbx); mov $1, %r12d; jmp .evdone
.shade:                                                                     /* SHADE(a: 1 open, 0 close): reply 1 = it changed, 0 = it already was so. The shade covers the active destination, remembered at 0x7d8 */
    mov 0x208(%rbx), %rax; and $1, %eax; xor %r12d, %r12d
    cmpq $2, 0x60(%rbx); je 1f
    test %eax, %eax; jz .evdone
    mov 0x60(%rbx), %rcx; mov %rcx, 0x7d8(%rbx); movq $2, 0x60(%rbx); movq $-1, 0x68(%rbx); mov $1, %r12d; jmp .evdone
1:  test %eax, %eax; jnz .evdone
    mov 0x7d8(%rbx), %rcx; mov %rcx, 0x60(%rbx); movq $-1, 0x68(%rbx); mov $1, %r12d; jmp .evdone
.move:                                                                      /* MOVE(a = x, b = y): the pointer position (window dp); the cursor is drawn from now on. Reply = x | y << 16 */
    mov 0x208(%rbx), %rax; mov %rax, 0x7e0(%rbx); mov 0x210(%rbx), %rcx; mov %rcx, 0x7e8(%rbx); movq $1, 0x7f0(%rbx)
    mov %rcx, %r12; shl $16, %r12; or %rax, %r12; jmp .evdone
.typ:                                                                      /* TEXT(a: 0..25 capital letter, 32..57 lowercase letter, 26 space, 255 backspace): edits the search field while FIND is active. Reply = new length, -1 not in FIND / bad code, -2 field full (16) */
    mov $-1, %r12; cmpq $1, 0x60(%rbx); jne .evdone
    mov 0x208(%rbx), %rax; mov 0x7f8(%rbx), %rcx
    cmp $255, %rax; jne 1f
    test %rcx, %rcx; jz 2f
    dec %rcx; mov %rcx, 0x7f8(%rbx); movb $0, 0x800(%rbx,%rcx)
2:  mov %rcx, %r12; jmp .evdone
1:  cmp $26, %rax; jbe 7f; cmp $32, %rax; jb .evdone; cmp $57, %rax; ja .evdone               /* 32..57 = the lowercase letters (a = 32 + index; the character is 65 + a) */
7:
    cmp $16, %rcx; jb 3f
    mov $-2, %r12; jmp .evdone
3:  cmp $26, %rax; jne 4f; mov $32, %eax; jmp 5f
4:  add $65, %eax
5:  mov %al, 0x800(%rbx,%rcx); movb $0, 0x801(%rbx,%rcx)                    /* the field is also full when the text would be wider than the field (window width - 148 dp, body-large Roboto) */
    push %rcx; call ovl; mov %rax, %r13; mov (%r13), %r9d; lea 0x800(%rbx), %rdx; call textw; mov 0x10(%rbx), %edx; sub 28(%r13), %edx; pop %rcx
    cmp %edx, %eax; jbe 6f
    movb $0, 0x800(%rbx,%rcx); mov $-2, %r12; jmp .evdone
6:  inc %rcx; mov %rcx, 0x7f8(%rbx); mov %rcx, %r12; jmp .evdone
.theme:                                                                     /* THEME(a: 0 light, 1 dark, 2 toggle): reply 1 when the theme changed, 0 when it already was that */
    mov 0x208(%rbx), %rax; cmp $2, %rax; jne 1f; xorq $1, 0x90(%rbx); mov $1, %r12d; jmp .evdone     /* a = 2: toggle (F2) */
1:  and $1, %eax; xor %r12d, %r12d; cmp 0x90(%rbx), %rax; je .evdone
    mov %rax, 0x90(%rbx); mov $1, %r12d; jmp .evdone
.back:                                                                      /* BACK (Android order): 1 = closed the focus ring, 3 = closed the shade, 4 = left RECENTS, 2 = went back to HOME (the app's other destination), 5 = left the app for the launcher (full screen only), 0 = not consumed (the system would leave the app) */
    call backlogic; mov %rax, %r12; jmp .evdone
backlogic:                                          /* the BACK key / the navigation bar's Back button -> eax = code above */
    mov 0x68(%rbx), %rax; test %rax, %rax; js 1f
    movq $-1, 0x68(%rbx); mov $1, %eax; ret
1:  cmpq $2, 0x60(%rbx); jne 2f
    mov 0x7d8(%rbx), %rax; mov %rax, 0x60(%rbx); mov $3, %eax; ret
2:  cmpq $0, 0xb0(%rbx); jne 3f
    xor %eax, %eax; cmpq $0, 0x60(%rbx); je 9f; movq $0, 0x60(%rbx); mov $2, %eax
9:  ret
3:  mov 0x60(%rbx), %rcx                                                    /* full screen */
    cmp $4, %rcx; jne 4f; mov 0xc8(%rbx), %rax; mov %rax, 0x60(%rbx); mov $4, %eax; ret
4:  cmp $3, %rcx; jne 5f; xor %eax, %eax; ret
5:  test %rcx, %rcx; jnz 6f
    movq $0, 0xc0(%rbx); movq $1, 0xb8(%rbx); movq $3, 0x60(%rbx); mov $5, %eax; ret      /* the app stays in RECENTS (its destination is remembered) */
6:  movq $0, 0x60(%rbx); mov $2, %eax; ret
.key:
    mov 0x208(%rbx), %rax; mov 0x68(%rbx), %r12                             /* r12 = current focus ordinal */
    cmp $1, %rax; jne 1f
    call hit_count; test %eax, %eax; jz 9f
    inc %r12; cmp %eax, %r12d; jb 8f; xor %r12d, %r12d; jmp 8f              /* next (wraps; -1 -> 0) */
1:  cmp $2, %rax; jne 2f
    call hit_count; test %eax, %eax; jz 9f
    test %r12, %r12; jle 3f; dec %r12; jmp 8f                               /* prev */
3:  dec %eax; mov %rax, %r12; jmp 8f                                        /* wrap to the last (also from -1) */
2:  cmp $3, %rax; jne 4f
    test %r12, %r12; js 9f                                                  /* activate with no focus: -1, nothing happens */
    mov %r12d, %esi; call hit_nth; test %rax, %rax; jz 9f
    mov 28(%rax), %eax; mov %rax, %rdx
    cmp $0x500, %eax; jb 6f; call do_act; jmp 7f                            /* a shade action: tile / close */
6:  cmp $0x100, %eax; jb 7f; cmp $0x1ff, %eax; ja 7f
    and $0xff, %edx; mov %rdx, 0x60(%rbx)
7:  mov %rax, %rdx; mov 0x68(%rbx), %r12; mov %rdx, %r13; jmp .evrender2
4:  cmp $4, %rax; jne 9f
    movq $-1, 0x68(%rbx); xor %r12d, %r12d; jmp .evdone                    /* clear focus -> reply 0 */
9:  mov $-1, %r12; jmp .evdone
8:  mov %r12, 0x68(%rbx)                                                    /* reply = the new focus ordinal */
    jmp .evdone
.evrender2: mov %r13, %r12                                                  /* activate: reply = the action id of the focused target */
.evdone:
    call render
    call bandhash; mov 0x4f8(%rbx), %rcx; cmp $200, %rcx; jae 1f                 /* the log has room for 200 events (one packed slot each at 0x900: reply code in the high 32 bits, band hash in the low 32; the page ends at 0xfe0); a long live session just stops logging */
    mov %eax, %eax; mov %r12, %rdx; shl $32, %rdx; or %rdx, %rax; mov %rax, 0x900(%rbx,%rcx,8); inc %rcx; mov %rcx, 0x4f8(%rbx)
1:
    mov %r12, %rax; call reply_now; jmp .ev
.evquit:
    xor %eax, %eax; call reply_now
.exit:
    mov $3, %rax; int $0x84
.reject:
    call bandhash; mov %rax, 0x80(%rbx)
    mov $-20, %rax; jmp .reply
.nocap:
    mov $-1, %rax; jmp .reply
.fail:
.reply:
    mov %rax, 0x78(%rbx); call reply_now
    mov $3, %rax; int $0x84
.hang:
    jmp .hang

reply_now:                                          /* rax = value -> REPLY on the saved reply capability */
    mov %rax, 0x400(%rbx); mov $10, %rax; mov 0x408(%rbx), %rdi; lea 0x400(%rbx), %rsi; mov $8, %rdx; int $0x84
    ret

tcolor:                                             /* r8d = light-theme colour -> r8d = the colour for the current theme (0x90 = 1: dark); unknown colours pass through */
    cmpq $0, 0x90(%rbx); je 9f
    push %rax; push %rcx; lea dk_tab(%rip), %rax
1:  mov (%rax), %ecx; test %ecx, %ecx; jz 8f; cmp %ecx, %r8d; je 2f; add $8, %rax; jmp 1b
2:  mov 4(%rax), %r8d
8:  pop %rcx; pop %rax
9:  ret

setscale:                                           /* rax = new font scale 0..2: stores it and re-selects the display list (index = 9 * tall + 3 * scale + width class; kept in 0xa8) */
    mov %rax, 0x98(%rbx); lea (%rax,%rax,2), %rax; add 0x38(%rbx), %rax
    mov 0xb0(%rbx), %rcx; lea (%rcx,%rcx,8), %rcx; add %rcx, %rax; mov %rax, 0xa8(%rbx)
    lea dl_tab(%rip), %rcx; mov (%rcx,%rax,8), %rax; lea dl_c0(%rip), %rcx; add %rcx, %rax; mov %rax, 0x70(%rbx); ret
ovl:                                                /* -> rax = the current list's entry of ovl_tab (64 bytes: face, baseline, caret top, caret height, field x, field y, field width = W - [6], fit limit = W - [7], text offset, cover flag); clobbers rcx */
    mov 0xa8(%rbx), %rax; shl $6, %rax; lea ovl_tab(%rip), %rcx; add %rcx, %rax; ret
do_act:                                             /* eax = action id >= 0x500: the caller keeps rax (it replies with the id) */
    push %rax; push %rcx; push %rdx; call do_act0; pop %rdx; pop %rcx; pop %rax; ret
do_act0:                                            /* 0x500 toggle the dark theme, 0x501 close the shade, 0x502 the Home tile, 0x503 next text size, 0x504 open the notification, 0x505 dismiss it; full screen only: 0x506 HOME, 0x507 RECENTS, 0x508 BACK, 0x510 / 0x511 open Messages / Search, 0x512 Settings (the shade), 0x520 open the recents card, 0x521 Clear all */
    cmp $0x503, %eax; jne 0f; mov 0x98(%rbx), %rax; inc %rax; cmp $3, %rax; jb 4f; xor %eax, %eax
4:  jmp setscale
0:  cmp $0x500, %eax; jne 1f; xorq $1, 0x90(%rbx); ret
1:  cmp $0x501, %eax; jne 2f; mov 0x7d8(%rbx), %rcx; mov %rcx, 0x60(%rbx); movq $-1, 0x68(%rbx); ret
2:  cmp $0x502, %eax; jne 3f; cmpq $0, 0xb0(%rbx); jne .home; movq $0, 0x60(%rbx); movq $-1, 0x68(%rbx); ret
3:  cmp $0x504, %eax; jne 4f; movq $0, 0xa0(%rbx); movq $-1, 0x68(%rbx); cmpq $0, 0xb0(%rbx); jne .openapp0; movq $0, 0x60(%rbx); ret       /* open the notification: dismiss it, close the shade, go HOME (full screen: open Messages) */
4:  cmp $0x505, %eax; jne 5f; movq $0, 0xa0(%rbx); movq $-1, 0x68(%rbx); ret                           /* dismiss it (the shade stays) */
5:  cmp $0x506, %eax; jne 6f
.home: call leaveapp; movq $3, 0x60(%rbx); movq $-1, 0x68(%rbx); ret
6:  cmp $0x507, %eax; jne 7f
    mov 0x60(%rbx), %rcx; cmp $2, %rcx; jne 1f; mov 0x7d8(%rbx), %rcx
1:  cmp $4, %rcx; jne 2f; mov 0xc8(%rbx), %rax; mov %rax, 0x60(%rbx); movq $-1, 0x68(%rbx); ret       /* Recents pressed again: back to where it was opened from */
2:  call leaveapp; mov 0x60(%rbx), %rcx; cmp $2, %rcx; jne 3f; mov 0x7d8(%rbx), %rcx
3:  mov %rcx, 0xc8(%rbx); movq $4, 0x60(%rbx); movq $-1, 0x68(%rbx); ret
7:  cmp $0x508, %eax; jne 8f; jmp backlogic
8:  cmp $0x509, %eax; jne 15f; xorq $1, 0xd0(%rbx); movq $-1, 0x68(%rbx); ret                 /* the shade's Nav tile: gestures <-> 3 buttons */
15: cmp $0x513, %eax; jne 13f; mov $1, %eax; jmp .openapp0                    /* 0x513: the launcher's search pill = open Search (a distinct id: every target of a state has its own) */
13: cmp $0x510, %eax; jb 9f; cmp $0x511, %eax; ja 9f
.openapp0: and $1, %eax                                                     /* 0x510 -> destination 0 (Messages, HOME), 0x511 -> destination 1 (Search); 0x504 arrives with eax = 0x504: -> 0 */
    mov %rax, 0x60(%rbx); movq $1, 0xb8(%rbx); movq $-1, 0x68(%rbx); ret
9:  cmp $0x512, %eax; jne 10f; mov 0x60(%rbx), %rcx; mov %rcx, 0x7d8(%rbx); movq $2, 0x60(%rbx); movq $-1, 0x68(%rbx); ret
10: cmp $0x520, %eax; jne 11f; mov 0xc0(%rbx), %rax; mov %rax, 0x60(%rbx); movq $-1, 0x68(%rbx); ret
11: cmp $0x521, %eax; jne 12f; movq $0, 0xb8(%rbx); movq $3, 0x60(%rbx); movq $-1, 0x68(%rbx)
12: ret
leaveapp:                                           /* the app (destination 0 or 1, also under the shade) is left for the launcher or RECENTS: remember its destination and that its task exists */
    mov 0x60(%rbx), %rcx; cmp $2, %rcx; jne 1f; mov 0x7d8(%rbx), %rcx
1:  cmp $1, %rcx; ja 2f; mov %rcx, 0xc0(%rbx); movq $1, 0xb8(%rbx)
2:  ret

cond_ok:                                            /* edx = cond (16 bits) -> eax = 1 when the record is visible in the current state (clobbers ecx, edx). Low 5 bits n: destination n-1 is active; 0x80 = NOT that; 0x40 = hidden while the shade (destination 2) is open; 0x20 = only while a notification is posted;
                                                       0x100 = only while the Messages task exists (1-51), 0x200 = only while it does not, 0x400 = only while the search field is empty */
    mov $1, %eax; test %edx, %edx; jz 9f
    test $0x100, %edx; jz 1f; cmpq $0, 0xb8(%rbx); jne 1f; xor %eax, %eax; ret
1:  test $0x200, %edx; jz 2f; cmpq $0, 0xb8(%rbx); je 2f; xor %eax, %eax; ret
2:  test $0x400, %edx; jz 21f; cmpq $0, 0x7f8(%rbx); je 21f; xor %eax, %eax; ret
21: test $0x800, %edx; jz 22f; cmpq $0, 0xd0(%rbx); jne 22f; xor %eax, %eax; ret                  /* 0x800: only in 3-button navigation mode (0xd0 = 1) */
22: test $0x1000, %edx; jz 3f; cmpq $0, 0xd0(%rbx); je 3f; xor %eax, %eax; ret                 /* 0x1000: only in gesture navigation mode (0xd0 = 0, the default) */
3:  and $0xff, %edx; jz 9f
    mov 0x60(%rbx), %ecx; inc %ecx
    test $0x20, %edx; jz 5f; cmpq $0, 0xa0(%rbx); jne 4f; xor %eax, %eax; ret                 /* 0x20: only while a notification is posted */
4:  and $0xdf, %edx
5:  test $0x40, %edx; jz 0f; cmp $3, %ecx; jne 0f; xor %eax, %eax; ret
0:  and $0xbf, %edx; jz 9f
    test $0x80, %edx; jnz 1f
    cmp %ecx, %edx; je 9f; xor %eax, %eax; jmp 9f
1:  and $0x7f, %edx; cmp %ecx, %edx; jne 9f; xor %eax, %eax
9:  ret

render:                                             /* draws the current list for the current state; then the focus ring when a target is focused */
    push %r15; push %r14
    mov 0x70(%rbx), %r15; lea shell_base(%rip), %r14; movq $0, 0x58(%rbx)
2:  mov (%r15), %eax; test %eax, %eax; jz .rdone2
    mov %eax, %edx; shr $8, %edx; and $0xffff, %edx; and $0xff, %eax; push %rax; call cond_ok; mov %eax, %ecx; pop %rax
    test %ecx, %ecx; jz 9f
    incq 0x58(%rbx)
    cmp $1, %eax; jne 3f
    mov 4(%r15), %edi; mov 8(%r15), %esi; mov 12(%r15), %edx; mov 16(%r15), %ecx; mov 24(%r15), %r8d; call tcolor; call fillrect; jmp 9f
3:  cmp $2, %eax; jne 4f
    mov 4(%r15), %edi; mov 8(%r15), %esi; mov 12(%r15), %edx; mov 16(%r15), %ecx; mov 24(%r15), %r8d; call tcolor; mov 20(%r15), %r9d; call fillrrect; jmp 9f
4:  cmp $3, %eax; jne 5f
    mov 4(%r15), %edi; mov 8(%r15), %esi; mov 28(%r15), %eax; lea (%r14,%rax), %rdx; cmp $-1, %eax; jne .clkok; lea 0xd8(%rbx), %rdx       /* aux = -1: the CLOCK string (1-52), kept in the data page */
.clkok: mov 24(%r15), %r8d; call tcolor; mov 20(%r15), %r9d; call draw_text2; jmp 9f
5:  cmp $5, %eax; jne 9f                                                     /* an icon: the alpha mask (aux = offset into `icons`), size = w, blended over what is already there */
    mov 4(%r15), %edi; mov 8(%r15), %esi; mov 12(%r15), %edx; mov %edx, %ecx; mov 24(%r15), %r8d; call tcolor; mov 28(%r15), %eax; lea icons(%rip), %r9; add %r9, %rax; call blendmask
9:  add $32, %r15; jmp 2b
.rdone2:
    cmpq $1, 0x60(%rbx); jne 7f                                             /* FIND with text typed: the search field overlay (placeholder hidden) + caret */
    cmpq $0, 0x7f8(%rbx); je 7f
    call ovl; mov %rax, %r15                                                /* this list's entry: body-large face / baseline / caret top / caret height / field geometry */
    cmpl $0, 36(%r15); je .nocover                                          /* the band layouts repaint the field (the placeholder is part of the list); the full-screen layouts hide the placeholder with the EMPTY condition instead */
    mov 16(%r15), %edi; mov 20(%r15), %esi; mov 0x10(%rbx), %edx; sub 24(%r15), %edx; mov $56, %ecx; mov $0xF3EDF7, %r8d; call tcolor; mov $28, %r9d; call fillrrect
.nocover:
    mov 16(%r15), %edi; add 32(%r15), %edi; mov 4(%r15), %esi; lea 0x800(%rbx), %rdx; mov $0x1D1B20, %r8d; call tcolor; mov (%r15), %r9d; call draw_text2
    lea 0x800(%rbx), %rdx; mov (%r15), %r9d; call textw; mov 16(%r15), %edi; add 32(%r15), %edi; add %eax, %edi; mov 8(%r15), %esi; mov $2, %edx; mov 12(%r15), %ecx; mov $0x6750A4, %r8d; call tcolor; call fillrect
7:  mov 0x68(%rbx), %rsi; test %rsi, %rsi; js 9f
    call hit_nth; test %rax, %rax; jz 9f
    mov %rax, %r15                                                          /* focus ring: 2 dp frame in the primary colour just inside the target */
    mov 4(%r15), %edi; mov 8(%r15), %esi; mov 12(%r15), %edx; mov $2, %ecx; mov $0x6750A4, %r8d; call tcolor; call fillrect
    mov 4(%r15), %edi; mov 8(%r15), %esi; add 16(%r15), %esi; sub $2, %esi; mov 12(%r15), %edx; mov $2, %ecx; mov $0x6750A4, %r8d; call tcolor; call fillrect
    mov 4(%r15), %edi; mov 8(%r15), %esi; mov $2, %edx; mov 16(%r15), %ecx; mov $0x6750A4, %r8d; call tcolor; call fillrect
    mov 4(%r15), %edi; add 12(%r15), %edi; sub $2, %edi; mov 8(%r15), %esi; mov $2, %edx; mov 16(%r15), %ecx; mov $0x6750A4, %r8d; call tcolor; call fillrect
9:  cmpq $0, 0x7f0(%rbx); je 8f                                             /* the pointer cursor: a 6x12 arrow (row r is r/2+1 wide) in on-surface, kept inside the window */
    mov 0x7e0(%rbx), %r15; mov 0x10(%rbx), %eax; sub $6, %eax; cmp %rax, %r15; jbe 1f; mov %rax, %r15
1:  mov 0x7e8(%rbx), %r14; mov $180, %eax; cmpq $0, 0xb0(%rbx); je .cl; mov $756, %eax                /* the cursor stays inside the window: 192-row band, or the full 768-row screen */
.cl: cmp %rax, %r14; jbe 2f; mov %eax, %r14d
2:  push %r12; xor %r12d, %r12d
3:  cmp $12, %r12d; jae 4f
    mov %r15d, %edi; lea (%r14,%r12), %esi; mov %r12d, %edx; shr $1, %edx; inc %edx; mov $1, %ecx; mov $0x1D1B20, %r8d; call tcolor; call fillrect
    inc %r12d; jmp 3b
4:  pop %r12
8:  pop %r14; pop %r15; ret

blendmask:                                          /* edi x, esi y (window-relative), edx w, ecx h, r8d colour, rax mask (w*h alpha bytes): out = (fg*a + dst*(255-a) + 127) / 255 per channel */
    push %r12; push %r13; push %r14; push %r15; push %rbp; sub $16, %rsp
    mov %r8d, %r15d; mov %edx, %r14d; mov %rax, %rbp; mov %edi, %r12d; mov %ecx, 8(%rsp)
    movabs $FBWIN, %r11; mov (FBI+0x08)(%rbx), %r9d; mov %r9, (%rsp)
    mov %esi, %eax; imul %r9, %rax; add %rax, %r11; mov %r12d, %eax; lea (%r11,%rax,4), %r11
    xor %r10d, %r10d
.irow: cmp 8(%rsp), %r10d; jae .idone
    xor %esi, %esi
.icol: cmp %r14d, %esi; jae .inext
    movzbl (%rbp), %ecx; inc %rbp; test %ecx, %ecx; jz .iskip
    mov (%r11,%rsi,4), %edi; xor %r8d, %r8d
    .irp sh, 0, 8, 16
    mov %r15d, %eax; shr $\sh, %eax; movzbl %al, %eax; imul %ecx, %eax
    mov %edi, %edx; shr $\sh, %edx; movzbl %dl, %edx; mov $255, %r9d; sub %ecx, %r9d; imul %r9d, %edx
    add %edx, %eax; add $127, %eax; xor %edx, %edx; mov $255, %r9d; div %r9d
    shl $\sh, %eax; or %eax, %r8d
    .endr
    mov %r8d, (%r11,%rsi,4)
.iskip: inc %esi; jmp .icol
.inext: add (%rsp), %r11; inc %r10d; jmp .irow
.idone: add $16, %rsp; pop %rbp; pop %r15; pop %r14; pop %r13; pop %r12; ret

draw_text2:                                         /* edi pen x, esi BASELINE y (window-relative), rdx NUL-terminated ASCII, r8d colour, r9d face (0..3): proportional Roboto from the generated glyph table */
    push %r12; push %r13; push %r14; push %r15; push %rbp; sub $16, %rsp
    mov %edi, %r12d; mov %esi, %r13d; mov %rdx, %r14; mov %r8d, %r15d; mov %r9d, %ebp
.tch: movzbl (%r14), %eax; test %eax, %eax; jz .ttdone
    sub $32, %eax; cmp $95, %eax; jb 1f; xor %eax, %eax
1:  mov %ebp, %ecx; imul $95, %ecx, %ecx; add %eax, %ecx; imul $12, %ecx, %ecx; lea font_gl(%rip), %rdx; add %rcx, %rdx
    mov 8(%rdx), %eax; mov %eax, (%rsp)
    mov 4(%rdx), %ecx; mov %ecx, %edi; sar $16, %edi; movsbl %dil, %edi; add %r12d, %edi; mov %ecx, %esi; sar $24, %esi; add %r13d, %esi
    mov (%rdx), %eax; lea font_masks(%rip), %r9; add %r9, %rax
    movzbl %cl, %edx; movzbl %ch, %ecx; mov %r15d, %r8d; call blendmask
    mov (%rsp), %eax; add %eax, %r12d; inc %r14; jmp .tch
.ttdone: add $16, %rsp; pop %rbp; pop %r15; pop %r14; pop %r13; pop %r12; ret

textw:                                              /* rdx NUL-terminated ASCII, r9d face -> eax = the pen advance of the whole string (clobbers ecx, r10, r11, rdx) */
    xor %eax, %eax
1:  movzbl (%rdx), %ecx; test %ecx, %ecx; jz 9f
    sub $32, %ecx; cmp $95, %ecx; jb 2f; xor %ecx, %ecx
2:  mov %r9d, %r10d; imul $95, %r10d, %r10d; add %ecx, %r10d; imul $12, %r10d, %r10d; lea font_gl(%rip), %r11; add 8(%r11,%r10), %eax
    inc %rdx; jmp 1b
9:  ret

hit_nth:                                            /* esi = ordinal among the VISIBLE hit boxes (list order) -> rax = record pointer or 0 */
    push %r15
    mov 0x70(%rbx), %r15
2:  mov (%r15), %eax; test %eax, %eax; jz 7f
    mov %eax, %edx; shr $8, %edx; and $0xffff, %edx; and $0xff, %eax
    cmp $4, %eax; jne 9f
    push %rsi; call cond_ok; pop %rsi; test %eax, %eax; jz 9f
    test %esi, %esi; jz 8f; dec %esi
9:  add $32, %r15; jmp 2b
7:  xor %r15d, %r15d
8:  mov %r15, %rax; pop %r15; ret

hit_count:                                          /* -> eax = number of visible hit boxes */
    push %r15; push %r12
    mov 0x70(%rbx), %r15; xor %r12d, %r12d
2:  mov (%r15), %eax; test %eax, %eax; jz 7f
    mov %eax, %edx; shr $8, %edx; and $0xffff, %edx; and $0xff, %eax
    cmp $4, %eax; jne 9f
    call cond_ok; add %eax, %r12d
9:  add $32, %r15; jmp 2b
7:  mov %r12d, %eax; pop %r12; pop %r15; ret

hit_point:                                          /* rdi = x, rsi = y -> rax = action id of the TOPMOST visible hit box containing the point, or -1 */
    push %r15; push %r12; push %r13; push %r14
    mov %rdi, %r13; mov %rsi, %r14; mov $-1, %r12
    mov 0x70(%rbx), %r15
2:  mov (%r15), %eax; test %eax, %eax; jz 7f
    mov %eax, %edx; shr $8, %edx; and $0xffff, %edx; and $0xff, %eax
    cmp $4, %eax; jne 9f
    call cond_ok; test %eax, %eax; jz 9f
    mov 4(%r15), %eax; cmp %rax, %r13; jb 9f; add 12(%r15), %eax; cmp %rax, %r13; jae 9f
    mov 8(%r15), %eax; cmp %rax, %r14; jb 9f; add 16(%r15), %eax; cmp %rax, %r14; jae 9f
    mov 28(%r15), %r12d
9:  add $32, %r15; jmp 2b
7:  mov %r12, %rax; pop %r14; pop %r13; pop %r12; pop %r15; ret

fillrrect:                                          /* edi x, esi y, edx w, ecx h, r8d colour, r9d radius (0 = square): rounded rectangle from the generated inset table */
    push %r12; push %r13; push %r14; push %r15; push %rbp; sub $24, %rsp
    mov %edi, %r12d; mov %esi, %r13d; mov %edx, %r14d; mov %ecx, %r15d; mov %r8d, %ebp
    lea rr_off(%rip), %rax; mov (%rax,%r9,4), %eax; lea rr_tab(%rip), %rdx; add %rax, %rdx
    mov %rdx, (%rsp); mov %r9, 8(%rsp); movq $0, 16(%rsp)
.rrow:
    mov 16(%rsp), %eax; cmp %r15d, %eax; jae .rrdone
    xor %ecx, %ecx; mov 8(%rsp), %edx; test %edx, %edx; jz .rr2
    cmp %edx, %eax; jae .rr1
    mov (%rsp), %rsi; movzbl (%rsi,%rax), %ecx; jmp .rr2
.rr1: mov %r15d, %esi; sub %eax, %esi; dec %esi
    cmp %edx, %esi; jae .rr2
    mov (%rsp), %rdi; movzbl (%rdi,%rsi), %ecx
.rr2:
    mov %r12d, %edi; add %ecx, %edi; mov %r14d, %edx; sub %ecx, %edx; sub %ecx, %edx
    mov %r13d, %esi; add 16(%rsp), %esi; mov $1, %ecx; mov %ebp, %r8d; call fillrect
    incq 16(%rsp); jmp .rrow
.rrdone: add $24, %rsp; pop %rbp; pop %r15; pop %r14; pop %r13; pop %r12; ret

.include "userprog/gfxlib.inc"
.include "userprog/shell_layout.inc"
.balign 8
dl_tab: .quad 0, dl_c1 - dl_c0, dl_c2 - dl_c0, dl_c3 - dl_c0, dl_c4 - dl_c0, dl_c5 - dl_c0, dl_c6 - dl_c0, dl_c7 - dl_c0, dl_c8 - dl_c0    /* index = 3 * font scale + width class */
        .quad dl_t0 - dl_c0, dl_t1 - dl_c0, dl_t2 - dl_c0, dl_t3 - dl_c0, dl_t4 - dl_c0, dl_t5 - dl_c0, dl_t6 - dl_c0, dl_t7 - dl_c0, dl_t8 - dl_c0    /* 1-51: + 9 for a full-screen window */
win_w:  .word 1024, 640, 360                         /* WINDOW sizes: expanded, medium, compact */
